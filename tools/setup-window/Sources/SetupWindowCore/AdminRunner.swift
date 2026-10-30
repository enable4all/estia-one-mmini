import Darwin
import Foundation

/// Runs the wrapper as root (AdminCore): macOS's own box asks (AppleScriptElevator in the app; a test's runs it as the
/// person). `done` gets how it went, on any thread, once the wrapper started the command in the background (or not).
public protocol Elevator: AnyObject {
    func elevate(words: [String], prompt: String, done: @escaping (Elevated) -> Void)
}

/// Where the runner's lines and results go: setup, through the bridge (never the page).
public protocol AdminSink: AnyObject {
    func adminLine(id: String, line: String)
    /// `unrun`: the command never started (macOS's box could not ask, the wrapper failed before it, or a request
    /// outside the rules): setup goes on in Terminal, where sudo asks (review ADM-4).
    func adminDone(id: String, code: Int, refused: Bool, unrun: Bool)
}

/// The window's admin runner: one command at a time as root, through its Elevator. It reads the command's run folder
/// (root's own, 0711; `out`, `code` and `in` the person's, 0600, each checked before it is opened, never through a
/// link) and says each line of the output, then the result; it writes the input into `in` (once, or answer by
/// answer until `end`) and never anywhere else. The result read, it truncates `code`: root then removes the folder.
/// A secret given as input is held only until it is written into the FIFO, then dropped.
public final class AdminRunner: AdminDoing {
    public weak var sink: AdminSink?
    private let elevator: Elevator
    private let base: String
    private let folderOwner: uid_t
    private let callbacks: DispatchQueue
    private let work = DispatchQueue(label: "estia-setup.admin")
    private let lock = NSLock()
    private var current: Run?
    private var timer: DispatchSourceTimer?

    /// `base`: where root makes the run folders (AdminCore.base; a test's folder in the tests); `folderOwner`: whose
    /// a run folder must be (root; the person in the tests); `callbacks`: where the sink is called (the main queue).
    public init(
        elevator: Elevator, base: String = AdminCore.base, folderOwner: uid_t = 0, callbacks: DispatchQueue = .main
    ) {
        self.elevator = elevator
        self.base = base
        self.folderOwner = folderOwner
        self.callbacks = callbacks
    }

    /// A command runs as root now (from the box to its result): the window is not closed meanwhile.
    public var running: Bool {
        lock.lock()
        defer { lock.unlock() }
        return current != nil
    }

    public func start(_ request: AdminRequest) {
        work.async { self.begin(request) }
    }

    public func input(id: String, text: String) {
        work.async {
            guard let run = self.current, run.id == id, !run.ended else { return }
            run.pending.append(Data(text.utf8))
            self.feed(run)
        }
    }

    public func end(id: String) {
        work.async {
            guard let run = self.current, run.id == id else { return }
            run.ended = true
            self.feed(run)
        }
    }

    // ---------------------------------------------------------------- on `work`

    private func begin(_ request: AdminRequest) {
        if current != nil || AdminCore.problem(request) != nil {
            say(.done(id: request.id, code: AdminCore.notRun, refused: false, unrun: true))
            return
        }
        let piped = request.input != nil || request.open
        let token = AdminCore.token()
        let run = Run(id: request.id, folder: "\(base)/estia-setup.\(token)", piped: piped)
        run.pending = Data((request.input ?? "").utf8)
        run.ended = !request.open
        set(run)
        let me = getuid()
        let user = getpwuid(me).map { String(cString: $0.pointee.pw_name) } ?? String(me)
        let words = AdminCore.words(
            token: token, uid: me, input: piped, seconds: request.seconds,
            environment: AdminCore.environment(user: user, uid: me, gid: getgid()), argv: request.argv)
        elevator.elevate(words: words, prompt: request.why) { [weak self] elevated in
            self?.work.async { self?.elevated(run, elevated) }
        }
    }

    private func elevated(_ run: Run, _ elevated: Elevated) {
        guard current === run else { return }
        switch elevated {
        case .started:
            run.since = Date()
            let timer = DispatchSource.makeTimerSource(queue: work)
            timer.schedule(deadline: .now(), repeating: .milliseconds(50))
            timer.setEventHandler { [weak self] in self?.poll(run) }
            self.timer = timer
            timer.resume()
        case .refused:
            finish(run, code: 0, refused: true)
        case .failed(let code):  // the wrapper never started the command: nothing ran
            finish(run, code: code, refused: false, unrun: true)
        }
    }

    private func poll(_ run: Run) {
        guard current === run else { return }
        if run.out < 0 {
            guard folderIsRoots(run.folder), let out = open(run, "out", kind: S_IFREG, flags: O_RDONLY) else {
                if Date().timeIntervalSince(run.since) > AdminCore.folderSeconds {
                    finish(run, code: AdminCore.wrapperFailed, refused: false)
                }
                return
            }
            run.out = out
        }
        feed(run)
        read(run)
        guard let code = result(run) else { return }
        read(run)  // to the end of what it said
        for line in run.lines.finish() { say(.line(id: run.id, line: line)) }
        if let acknowledged = open(run, "code", kind: S_IFREG, flags: O_WRONLY) {
            _ = ftruncate(acknowledged, 0)  // read: root removes the folder now
            Darwin.close(acknowledged)
        }
        finish(run, code: code, refused: false)
    }

    /// The input, into the FIFO once root's command opened it (a writer opened before that is refused, ENXIO: tried
    /// again at the next poll); closed at its end, which the command reads as the end of its input.
    private func feed(_ run: Run) {
        guard run.piped, run.out >= 0, run.input != Run.closed else { return }
        if run.input < 0 {
            guard let fifo = open(run, "in", kind: S_IFIFO, flags: O_WRONLY | O_NONBLOCK) else { return }
            _ = fcntl(fifo, F_SETNOSIGPIPE, 1)  // a command that ends before reading it all: EPIPE, never a signal
            run.input = fifo
        }
        while !run.pending.isEmpty {
            let written = run.pending.withUnsafeBytes { write(run.input, $0.baseAddress, $0.count) }
            if written <= 0 { break }  // full (EAGAIN) or gone (EPIPE): the rest at the next poll, or never
            run.pending.removeFirst(written)
        }
        if run.ended && run.pending.isEmpty {
            Darwin.close(run.input)
            run.input = Run.closed
        }
    }

    private func read(_ run: Run) {
        var buffer = [UInt8](repeating: 0, count: 65536)
        while true {
            let count = Darwin.read(run.out, &buffer, buffer.count)
            guard count > 0 else { return }
            for line in run.lines.add(Data(buffer[0..<count])) { say(.line(id: run.id, line: line)) }
        }
    }

    /// The command's exit code, once root wrote it (a whole number and its newline); nil until then.
    private func result(_ run: Run) -> Int? {
        guard let fd = open(run, "code", kind: S_IFREG, flags: O_RDONLY) else { return nil }
        defer { Darwin.close(fd) }
        var buffer = [UInt8](repeating: 0, count: 16)
        let count = Darwin.read(fd, &buffer, buffer.count)
        guard count > 1, buffer[count - 1] == UInt8(ascii: "\n"),
            let code = Int(String(decoding: buffer[0..<(count - 1)], as: UTF8.self)), (0...255).contains(code)
        else { return nil }
        return code
    }

    /// The run folder is root's own (folderOwner's in the tests): a folder, never a link, mode 0711.
    private func folderIsRoots(_ folder: String) -> Bool {
        var info = stat()
        guard lstat(folder, &info) == 0 else { return false }
        return info.st_mode & S_IFMT == S_IFDIR && info.st_uid == folderOwner && info.st_mode & 0o7777 == 0o711
    }

    /// One of the folder's files, opened only when it is what root made: of its kind, the person's, 0600, one name,
    /// never a link (O_NOFOLLOW), and the same file once opened.
    private func open(_ run: Run, _ name: String, kind: mode_t, flags: Int32) -> Int32? {
        let path = "\(run.folder)/\(name)"
        var before = stat()
        guard lstat(path, &before) == 0, before.st_mode & S_IFMT == kind, before.st_uid == getuid(),
            before.st_mode & 0o7777 == 0o600, before.st_nlink == 1
        else { return nil }
        let fd = Darwin.open(path, flags | O_NOFOLLOW | O_CLOEXEC | (kind == S_IFREG ? O_NONBLOCK : 0))
        guard fd >= 0 else { return nil }
        var after = stat()
        guard fstat(fd, &after) == 0, after.st_dev == before.st_dev, after.st_ino == before.st_ino else {
            Darwin.close(fd)
            return nil
        }
        return fd
    }

    private func finish(_ run: Run, code: Int, refused: Bool, unrun: Bool = false) {
        timer?.cancel()
        timer = nil
        for fd in [run.out, run.input] where fd >= 0 { Darwin.close(fd) }
        run.out = Run.closed
        run.input = Run.closed
        run.pending = Data()  // a secret's last bytes, if the command never read them
        set(nil)
        say(.done(id: run.id, code: code, refused: refused, unrun: unrun))
    }

    private func set(_ run: Run?) {
        lock.lock()
        current = run
        lock.unlock()
    }

    private func say(_ message: AdminMessage) {
        callbacks.async { [weak self] in
            switch message {
            case .line(let id, let line): self?.sink?.adminLine(id: id, line: line)
            case .done(let id, let code, let refused, let unrun):
                self?.sink?.adminDone(id: id, code: code, refused: refused, unrun: unrun)
            }
        }
    }
}

/// One command's run, owned by the runner's queue.
private final class Run {
    static let closed: Int32 = -2
    let id: String
    let folder: String
    let piped: Bool
    var pending = Data()  // input not yet written into the FIFO
    var ended = false  // no more input comes (one-shot input, or admin_end)
    var out: Int32 = -1
    var input: Int32 = -1
    var lines = OutputLines()
    var since = Date()

    init(id: String, folder: String, piped: Bool) {
        self.id = id
        self.folder = folder
        self.piped = piped
    }
}

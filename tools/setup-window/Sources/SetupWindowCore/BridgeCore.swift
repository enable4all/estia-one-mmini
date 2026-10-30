import Foundation

/// What the bridge's decisions do in the window (WebKit, stdout, the app's end), kept out of them so they are tested
/// without a window (Tests/SetupWindowCoreTests/BridgeTests.swift).
public protocol BridgeSink: AnyObject {
    /// One of setup's lines, checked, to the page (window.estia.receive).
    func toPage(_ line: String)
    /// One of the window's lines to setup (stdout); throws once setup is gone.
    func toSetup(_ line: Data) throws
    /// Setup ended (stdin's end, or stdout gone): the page says so, and the window stays until it is closed.
    func setupEnded()
    /// The window ends now.
    func endWindow()
    /// Setup goes on in Terminal (its hand-over, or its end without a last word): the Terminal setup runs in comes to
    /// the front (an app activation, no Apple Event).
    func bringTerminal()
}

/// What runs setup's admin commands (AdminRunner): one at a time, its input fed in turn.
public protocol AdminDoing: AnyObject {
    /// A command runs as root now (from the box to its result).
    var running: Bool { get }
    func start(_ request: AdminRequest)
    func input(id: String, text: String)
    func end(id: String)
}

/// Between setup and the page, without WebKit: setup's lines are checked (EngineMessage) and wait until the page says
/// it is ready, then come in order; the page's messages are checked (WindowMessage) and go to setup as one line
/// each. Anything that is not one of its side's messages (a line setup should never send, a paste with a NUL, an
/// answer over the line limit) ends the window: the page has turned its buttons off and waits for setup's word, and
/// setup waits for an answer, so the window says closed and ends, and setup asks the same question in Terminal.
/// Setup's admin commands go to the admin runner, never to the page, and only the runner's lines and results reach
/// setup as admin_line and admin_done: the page can neither start, feed nor end one, nor forge its output.
public final class BridgeCore: AdminSink {
    private weak var sink: BridgeSink?
    private weak var admin: AdminDoing?
    public private(set) var ready = false
    public private(set) var ended = false
    private var waiting: [String] = []
    private var closedSent = false
    /// Setup's last word reached the page (the closing list, a stop's screen) or the page stopped it (Stop here): its
    /// end then needs nothing more. Without it, setup's end brings Terminal to the front, where it says why.
    public private(set) var lastWord = false

    public init(sink: BridgeSink, admin: AdminDoing?) {
        self.sink = sink
        self.admin = admin
    }

    /// One of setup's lines (stdin), as it came.
    public func fromSetup(_ line: Data) {
        guard !ended else { return }
        guard let message = try? EngineMessage.decode(line), let text = String(data: line, encoding: .utf8) else {
            broken()  // never shown: a line that is not one of setup's messages
            return
        }
        if ready { deliver(text, message) } else { waiting.append(text) }
    }

    /// One of setup's lines: an admin command to the runner (without one, it is not run, and said so: setup goes on in
    /// Terminal), anything else to the page. Setup's hand-over (`terminal`: it goes on in
    /// Terminal from here) brings Terminal to the front; every step runs in the window (owner, 29 Oct), so nothing else
    /// does.
    private func deliver(_ text: String, _ message: EngineMessage) {
        switch message {
        case .admin(let request):
            if let admin {
                admin.start(request)
            } else {
                adminDone(id: request.id, code: AdminCore.notRun, refused: false, unrun: true)
            }
            return
        case .adminInput(let id, let text):
            admin?.input(id: id, text: text)
            return
        case .adminEnd(let id):
            admin?.end(id: id)
            return
        default:
            break
        }
        sink?.toPage(text)
        switch message {
        case .done(_), .screen(id: "stop", kind: _):
            lastWord = true
        case .terminal:
            sink?.bringTerminal()
        default:
            break
        }
    }

    // ---------------------------------------------------------------- AdminSink: the runner's words to setup

    public func adminLine(id: String, line: String) { toSetup(.line(id: id, line: line)) }

    public func adminDone(id: String, code: Int, refused: Bool, unrun: Bool) {
        toSetup(.done(id: id, code: code, refused: refused, unrun: unrun))
    }

    private func toSetup(_ message: AdminMessage) {
        guard !ended, let line = try? message.line() else { return }
        do { try sink?.toSetup(line) } catch { endOfInput() }
    }

    /// A message of the page (the "estia" handler's body, as WebKit gives it). Its first ready lets setup's lines
    /// through.
    public func fromPage(_ body: Any) {
        guard !ended else { return }
        guard let checked = try? WindowMessage(body: body) else {
            broken()
            return
        }
        if checked == .stop { lastWord = true }  // Stop here: the page says it stopped, setup ends by itself
        if checked == .ready && !ready {
            ready = true
            let lines = waiting
            waiting = []
            for line in lines {
                if let message = try? EngineMessage.decode(Data(line.utf8)) { deliver(line, message) }
            }
        }
        send(checked)
    }

    private func send(_ message: WindowMessage) {
        guard !ended else { return }
        let line: Data
        do {
            line = try message.line()
        } catch {
            broken()  // an answer over the line limit: setup would never read it
            return
        }
        do { try sink?.toSetup(line) } catch { endOfInput() }
    }

    /// Whether the window may close now, by any way (its red button, the page's Close, Quit): never while a step runs
    /// with admin rights, as root's command would go on without the window and setup would no longer read its result
    /// (review W02: the page's Close skipped the red button's guard).
    public func mayClose() -> Bool {
        !(admin?.running ?? false)
    }

    /// The window closed (or the app quits): setup goes on in Terminal at the same question.
    public func closed() {
        guard !ended, !closedSent else { return }
        closedSent = true
        send(.closed)
    }

    /// Something that never should happen (a line setup should never send, a page that did not load): setup goes on
    /// in Terminal, the window ends.
    public func broken() {
        closed()
        sink?.endWindow()
    }

    /// stdin's end: setup ended. The page says so; nothing more goes to setup. Without setup's last word (a Ctrl-C in
    /// Terminal, a crash), Terminal comes to the front: it says why (owner's laptop, 29 Oct: it stayed behind).
    public func endOfInput() {
        guard !ended else { return }
        ended = true
        sink?.setupEnded()
        if ready && !lastWord { sink?.bringTerminal() }
    }
}

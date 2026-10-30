import AppKit
import Darwin
import SetupWindowCore
import WebKit

/// Between setup and the page, in the window: setup's lines come in on stdin (a reader thread) and the page's messages
/// from WebKit; what to do with each is BridgeCore's (SetupWindowCore, tested without a window): this class does it
/// (the page, stdout, the app's end). Setup's admin commands go to the admin runner (macOS's own box asks; the page
/// never sees them). What the page asks of the window itself (NativeRequest) stays here.
/// stdin's end means setup ended: the page says so, and its last screen (the closing list, a stop) stays until the
/// window is closed (owner's laptop, 29 Oct: "the setup window was gone").
final class Bridge: BridgeSink {
    static let engine = "estia"
    static let native = "native"

    private weak var webView: WKWebView?
    weak var window: NSWindow?
    private let terminalApp: String?
    /// setup's admin commands, as root, through macOS's own box (AppleScriptElevator)
    let admin = AdminRunner(elevator: AppleScriptElevator())
    private lazy var core: BridgeCore = {
        let core = BridgeCore(sink: self, admin: admin)
        admin.sink = core
        return core
    }()

    init(webView: WKWebView, terminalApp: String?) {
        self.webView = webView
        self.terminalApp = terminalApp
    }

    /// The reader: stdin in chunks, split into lines, each handed to the main queue.
    func start() {
        let thread = Thread { [weak self] in
            var lines = Lines(limit: SetupProtocol.fromEngineMax)
            var buffer = [UInt8](repeating: 0, count: 64 * 1024)
            while true {
                let count = read(STDIN_FILENO, &buffer, buffer.count)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { break }
                do {
                    for line in try lines.add(Data(buffer[0..<count])) {
                        DispatchQueue.main.async { self?.core.fromSetup(line) }
                    }
                } catch {
                    DispatchQueue.main.async { self?.core.broken() }
                    return
                }
            }
            DispatchQueue.main.async { self?.core.endOfInput() }
        }
        thread.name = "setup's lines"
        thread.start()
    }

    /// A message of the page, only from the page itself (its own scheme, the main frame).
    func received(_ message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.frameInfo.securityOrigin.protocol == PageServer.scheme else { return }
        if message.name == Bridge.engine {
            core.fromPage(message.body)
        } else if message.name == Bridge.native, let request = try? NativeRequest(body: message.body) {
            handle(request)
        }
    }

    /// The window closed (or the app quits): setup goes on in Terminal at the same question.
    func closed() { core.closed() }

    /// Whether the window may close now (BridgeCore's one decision, for every way to close it).
    func mayClose() -> Bool { core.mayClose() }

    /// Something that never should happen (a page that did not load, its rules not compiled): setup goes on in
    /// Terminal, the window ends.
    func broken() { core.broken() }

    // ---------------------------------------------------------------- BridgeSink: what the decisions do here

    func toPage(_ line: String) {
        webView?.callAsyncJavaScript(
            "window.estia.receive(JSON.parse(line))", arguments: ["line": line], in: nil, in: .page, completionHandler: nil)
    }

    func toSetup(_ line: Data) throws {
        try FileHandle.standardOutput.write(contentsOf: line)
    }

    func setupEnded() {
        webView?.callAsyncJavaScript("window.estia.ended()", arguments: [:], in: nil, in: .page, completionHandler: nil)
    }

    func endWindow() {
        NSApp.terminate(nil)
    }

    private func handle(_ request: NativeRequest) {
        switch request {
        case .pickFolder:
            guard let window else { return }
            let panel = NSOpenPanel()
            panel.canChooseDirectories = true
            panel.canChooseFiles = false
            panel.allowsMultipleSelection = false
            panel.canCreateDirectories = false
            panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser
            panel.beginSheetModal(for: window) { [weak self] response in
                guard response == .OK, let url = panel.url else { return }
                self?.webView?.callAsyncJavaScript(
                    "window.estia.picked(path)", arguments: ["path": url.path], in: nil, in: .page, completionHandler: nil)
            }
        case .copy(let text):
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
        case .terminal:
            bringTerminal()  // Switch to Terminal, on the hand-over's screen: the same, by hand
        case .close:
            window?.performClose(nil)  // as its red button: windowShouldClose asks first (review W02)
        }
    }

    /// The terminal setup runs in, to the front: the first app among setup's own parents (Terminal, iTerm2, or any
    /// other), else the running app setup named. Only an app activation (NSRunningApplication), never an Apple Event,
    /// so macOS asks no Automation permission; and only a running one, never a second Terminal.
    func bringTerminal() {
        var pid = getppid()
        for _ in 0..<16 where pid > 1 {
            if let app = NSRunningApplication(processIdentifier: pid), app.activationPolicy == .regular {
                activate(app)
                return
            }
            pid = parent(of: pid)
        }
        guard let id = terminalApp,
            let app = NSRunningApplication.runningApplications(withBundleIdentifier: id).first
        else { return }
        activate(app)
    }

    /// The window yields its activation to the terminal, which then becomes the active app with its key window, the
    /// one setup runs in when it was used last (cooperative activation, macOS 14 and later). Refused (the window was not
    /// the active app), the plain request is made too; macOS may leave it for the person's click.
    private func activate(_ app: NSRunningApplication) {
        guard !app.isActive else { return }
        NSApp.yieldActivation(to: app)
        if !app.activate(from: NSRunningApplication.current, options: []) {
            app.activate()
        }
    }

    private func parent(of pid: pid_t) -> pid_t {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var name: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
        guard sysctl(&name, 4, &info, &size, nil, 0) == 0, size > 0 else { return 0 }
        return info.kp_eproc.e_ppid
    }
}

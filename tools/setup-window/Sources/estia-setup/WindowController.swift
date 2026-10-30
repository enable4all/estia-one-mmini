import AppKit
import SetupWindowCore
import WebKit

/// The app: one window, "Estia Setup", holding the page in a WKWebView that can reach nothing but its own files.
/// - Its data store is in memory (`nonPersistent`): no cookies, no cache, nothing kept.
/// - `estia-setup://app/` is served by the app itself (PageServer: only page.json's files, each checked by sha256);
///   a content rule list blocks every http, https, ws, ftp and file load besides the page's own rules (CSP).
/// - Only page.html loads in the window; an https link the person clicks opens in the Mac's browser; no new windows,
///   no developer tools, no JavaScript panels, no file inputs; the context menu keeps only editing.
/// - The window never asks for the Mac's password: a step's admin rights come from macOS's own box (AdminRunner,
///   AppleScriptElevator); the window is not closed while such a command runs.
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    let arguments: Arguments
    let server: PageServer
    private var window: NSWindow?
    private var webView: WKWebView?
    private var bridge: Bridge?
    private var rules: URL?

    init(arguments: Arguments, server: PageServer) {
        self.arguments = arguments
        self.server = server
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = menu()
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.setURLSchemeHandler(SchemeHandler(server: server), forURLScheme: PageServer.scheme)
        configuration.mediaTypesRequiringUserActionForPlayback = .all  // af_heart's intro plays only on a click
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.preferences.isElementFullscreenEnabled = false
        let webView = PageView(frame: NSRect(x: 0, y: 0, width: 600, height: 740), configuration: configuration)
        webView.isInspectable = false
        webView.allowsBackForwardNavigationGestures = false
        webView.allowsMagnification = false
        // no preview on a force click or Look Up: WebKit's preview loads a link outside this web view's navigation
        // policy, content rules and in-memory store; a link opens only in the browser
        webView.allowsLinkPreview = false
        webView.navigationDelegate = self
        webView.uiDelegate = self
        let bridge = Bridge(webView: webView, terminalApp: arguments.terminalApp)
        configuration.userContentController.add(WeakHandler(bridge), name: Bridge.engine)
        configuration.userContentController.add(WeakHandler(bridge), name: Bridge.native)
        self.webView = webView
        self.bridge = bridge

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 600, height: 740),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Estia Setup"
        window.contentMinSize = NSSize(width: 480, height: 560)
        window.contentView = webView
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        self.window = window
        bridge.window = window

        blockEverythingOutside { [weak self] in
            guard let self, let webView = self.webView else { return }
            webView.load(URLRequest(url: PageServer.start))
            self.window?.makeKeyAndOrderFront(nil)
            self.window?.makeFirstResponder(webView)  // typing reaches the page at once
            NSApp.activate()
            bridge.start()
        }
    }

    /// The content rule list: no http, https, ws, ftp or file load at all, whatever the page asks. WebKit keeps a
    /// compiled list in a store on disk: here a fresh temporary folder, removed when the window ends.
    private func blockEverythingOutside(then load: @escaping () -> Void) {
        let rules = """
            [{"trigger":{"url-filter":"^https?:"},"action":{"type":"block"}},
             {"trigger":{"url-filter":"^wss?:"},"action":{"type":"block"}},
             {"trigger":{"url-filter":"^ftp:"},"action":{"type":"block"}},
             {"trigger":{"url-filter":"^file:"},"action":{"type":"block"}}]
            """
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("estia-setup-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        self.rules = folder
        guard let store = WKContentRuleListStore(url: folder) else { NSApp.terminate(nil); return }
        store.compileContentRuleList(forIdentifier: "outside", encodedContentRuleList: rules) { [weak self] list, _ in
            guard let list, let webView = self?.webView else {
                self?.bridge?.broken()  // never shown without its rules
                return
            }
            webView.configuration.userContentController.add(list)
            load()
        }
    }

    private func menu() -> NSMenu {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Hide Estia Setup", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Estia Setup", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)
        // pasting a captcha link, a key or a code needs the Edit menu's shortcuts
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)
        return main
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    /// Not while a step runs with admin rights: root's command goes on without the window, and setup would no longer
    /// read its result.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard bridge?.mayClose() == false else { return true }
        adminRuns()
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard bridge?.mayClose() == false else { return .terminateNow }
        adminRuns()
        return .terminateCancel
    }

    private func adminRuns() {
        guard let window, window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = AppDelegate.adminRuns
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window)
    }

    static let adminRuns = "A step with admin rights runs; it finishes first."

    func windowWillClose(_ notification: Notification) {
        bridge?.closed()  // setup goes on in Terminal at the same question
    }

    func applicationWillTerminate(_ notification: Notification) {
        bridge?.closed()
        if let rules { try? FileManager.default.removeItem(at: rules) }
    }

    // ------------------------------------------------------------------ where the page may go

    func webView(
        _ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        let decision = LinkPolicy.decide(
            navigationAction.request.url, clicked: navigationAction.navigationType == .linkActivated,
            mainFrame: navigationAction.targetFrame?.isMainFrame ?? false)
        switch decision {
        case .load:
            decisionHandler(.allow)
        case .openInBrowser(let url):
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        case .drop:
            decisionHandler(.cancel)
        }
    }

    func webView(
        _ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if case .openInBrowser(let url) = LinkPolicy.decide(
            navigationAction.request.url, clicked: navigationAction.navigationType == .linkActivated, mainFrame: false)
        {
            NSWorkspace.shared.open(url)
        }
        return nil  // never a second window
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        bridge?.broken()
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        bridge?.broken()
    }
}

/// The page's view: its context menu keeps only editing (no reload, no web search, no link or image opening).
final class PageView: WKWebView {
    private static let gone = [
        "Reload", "GoBack", "GoForward", "Inspect", "OpenLink", "Download", "OpenImage", "OpenFrame", "OpenMedia",
        "Share", "LookUp", "SearchWeb", "Translate", "CopyLink", "CopyImage", "Speech",
    ]

    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        for item in menu.items where PageView.gone.contains(where: { (item.identifier?.rawValue ?? "").contains($0) }) {
            menu.removeItem(item)
        }
        super.willOpenMenu(menu, with: event)
    }
}

/// estia-setup://app/<path>: the page's own files only, through PageServer (each checked by its sha256).
final class SchemeHandler: NSObject, WKURLSchemeHandler {
    let server: PageServer

    init(server: PageServer) { self.server = server }

    func webView(_ webView: WKWebView, start urlSchemeTask: WKURLSchemeTask) {
        let request = urlSchemeTask.request
        guard let url = request.url else { return }
        let answer = request.httpMethod == nil || request.httpMethod == "GET"
            ? server.respond(to: url)
            : PageAnswer(status: 405, headers: ["Content-Security-Policy": PageServer.contentSecurityPolicy], body: Data())
        guard let response = HTTPURLResponse(url: url, statusCode: answer.status, httpVersion: "HTTP/1.1", headerFields: answer.headers)
        else { return }
        urlSchemeTask.didReceive(response)
        urlSchemeTask.didReceive(answer.body)
        urlSchemeTask.didFinish()
    }

    func webView(_ webView: WKWebView, stop urlSchemeTask: WKURLSchemeTask) {}
}

/// The user content controller keeps its handlers: this one keeps the bridge weakly.
final class WeakHandler: NSObject, WKScriptMessageHandler {
    weak var target: Bridge?

    init(_ target: Bridge) { self.target = target }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.received(message)
    }
}

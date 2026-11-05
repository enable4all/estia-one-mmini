import Foundation
import Testing

@testable import SetupWindowCore

/// What the window does, recorded: setup's lines to the page, the window's lines to setup, setup's end, the window's.
private final class Recorder: BridgeSink {
    var page: [String] = []
    var setup: [String] = []
    var ended = 0
    var quit = 0
    var setupGone = false
    var fronts: [String] = []  // "terminal", each time Terminal is brought to the front
    var admin: [String] = []  // what the admin runner was asked: "start <id> <argv…>", "input <id> <text>", "end <id>"
    var adminRunning = false  // a command runs as root now

    struct Gone: Error {}

    func toPage(_ line: String) { page.append(line) }

    func toSetup(_ line: Data) throws {
        if setupGone { throw Gone() }
        setup.append(String(decoding: line, as: UTF8.self))
    }

    func setupEnded() { ended += 1 }

    func endWindow() { quit += 1 }

    func bringTerminal() { fronts.append("terminal") }
}

extension Recorder: AdminDoing {
    var running: Bool { adminRunning }

    func start(_ request: AdminRequest) { admin.append((["start", request.id] + request.argv).joined(separator: " ")) }

    func input(id: String, text: String) { admin.append("input \(id) \(text)") }

    func end(id: String) { admin.append("end \(id)") }
}

private let hello = #"{"t":"hello","v":2,"name":"Estia"}"#
private let progress = #"{"t":"progress","line":"✓ Ollama"}"#
private let closed = "{\"t\":\"closed\"}\n"
private let terminal = #"{"t":"terminal","why":"Setup goes on in the Terminal window you started it in."}"#
private let admin = #"{"t":"admin","id":"a1","argv":["/usr/local/estia/bin/estia","restart"],"input":null,"open":true,"why":"Admin rights to restart her."}"#
private let screen = #"{"t":"screen","id":"name","kind":"choice","step":1,"steps":9,"title":"Name","lead":"","help":"","ask":"","note":"","options":[],"fields":[],"placeholder":"","recommended":null,"layout":null,"back":false}"#
private let done = #"{"t":"done","lines":["Done."]}"#
private let handedDone = #"{"t":"done","lines":[]}"#
private let stopScreen = #"{"t":"screen","id":"stop","kind":"info","step":1,"steps":9,"title":"The Ollama app is installed.","lead":"","help":"","ask":"","note":"","options":[],"fields":[],"placeholder":"","recommended":null,"layout":null,"back":false}"#

/// The page's first message as WebKit gives it: its version is a double.
private func ready() -> NSDictionary { ["t": "ready", "v": NSNumber(value: 2.0)] }

private func bridge(_ sink: Recorder) -> BridgeCore { BridgeCore(sink: sink, admin: sink) }

/// The bridge's decisions, without WebKit: setup's lines wait for the page's ready, the page's messages are checked
/// before they go to setup, and anything either side should never send ends the window, so setup asks in Terminal.
@Suite struct BridgeDecisions {
    @Test func setupsLinesWaitForThePageThenComeInOrder() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromSetup(Data(hello.utf8))
        bridge.fromSetup(Data(progress.utf8))
        #expect(sink.page.isEmpty)
        bridge.fromPage(ready())
        #expect(sink.page == [hello, progress])
        #expect(sink.setup == ["{\"t\":\"ready\",\"v\":2}\n"])
        bridge.fromSetup(Data(progress.utf8))
        #expect(sink.page == [hello, progress, progress])
        #expect(sink.quit == 0 && sink.ended == 0)
    }

    @Test func thePagesAnswersGoToSetupAsOneLineEach() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        bridge.fromPage(["t": "answer", "id": "move", "value": NSNumber(value: true)] as NSDictionary)
        bridge.fromPage(["t": "answer", "id": "extras", "value": ["web"] as NSArray] as NSDictionary)
        #expect(sink.setup.dropFirst() == [
            "{\"t\":\"answer\",\"id\":\"move\",\"value\":true}\n", "{\"t\":\"answer\",\"id\":\"extras\",\"value\":[\"web\"]}\n",
        ])
    }

    /// A paste with a NUL, or a message of another shape: the page has turned its buttons off and waits for setup's
    /// word, and setup waits for an answer. The window ends, and setup asks the same question in Terminal.
    @Test func aPageMessageSetupWouldRefuseEndsTheWindow() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        bridge.fromPage(["t": "answer", "id": "name", "value": "Ma\u{0}ya"] as NSDictionary)
        #expect(sink.setup.last == closed && sink.quit == 1)
    }

    /// Review W11: the page's process died while a command ran as root. The app may not quit before it ends (its
    /// result would be lost), and nothing asked again once it did: a blank window stayed until quit by hand. Setup
    /// hears closed at once; the window ends once the command's result went to setup.
    @Test func aWindowThatBreaksDuringAnAdminCommandEndsOnceItIsDone() {
        let sink = Recorder()
        let bridge = bridge(sink)
        sink.adminRunning = true
        bridge.fromPage(ready())
        bridge.broken()
        #expect(sink.quit == 0 && Array(sink.setup.dropFirst()) == [closed])
        sink.adminRunning = false
        bridge.adminDone(id: "a1", code: 0, refused: false, unrun: false)
        #expect(sink.quit == 1)
        #expect(sink.setup.last == "{\"t\":\"admin_done\",\"id\":\"a1\",\"code\":0,\"refused\":false}\n")
        bridge.adminDone(id: "a2", code: 0, refused: false, unrun: false)
        #expect(sink.quit == 1)  // once
    }

    /// The same, when setup is gone by the command's end: the window still ends.
    @Test func aBrokenWindowEndsAfterItsAdminCommandEvenWhenSetupIsGone() {
        let sink = Recorder()
        let bridge = bridge(sink)
        sink.adminRunning = true
        bridge.fromPage(ready())
        bridge.broken()
        #expect(sink.quit == 0)
        sink.setupGone = true
        sink.adminRunning = false
        bridge.adminDone(id: "a1", code: 0, refused: false, unrun: false)
        #expect(sink.quit == 1 && sink.ended == 1)
    }

    @Test func anAnswerOverTheLineLimitEndsTheWindow() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        let long = String(repeating: "x", count: SetupProtocol.fromWindowMax)
        bridge.fromPage(["t": "answer", "id": "name", "value": long] as NSDictionary)
        #expect(sink.setup == ["{\"t\":\"ready\",\"v\":2}\n", closed] && sink.quit == 1)
    }

    @Test func aLineSetupShouldNeverSendEndsTheWindow() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        bridge.fromSetup(Data(#"{"t":"shout","line":"hi"}"#.utf8))
        #expect(sink.page.isEmpty && sink.setup.last == closed && sink.quit == 1)
    }

    @Test func theWindowSaysClosedOnce() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.closed()
        bridge.closed()
        bridge.broken()
        #expect(sink.setup == [closed] && sink.quit == 1)
    }

    @Test func onceSetupEndedNothingMoreGoesToIt() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        bridge.endOfInput()
        bridge.endOfInput()
        bridge.fromPage(["t": "answer", "id": "name", "value": "Maya"] as NSDictionary)
        bridge.fromSetup(Data(progress.utf8))
        bridge.closed()
        #expect(sink.setup.count == 1 && sink.ended == 1 && sink.page.isEmpty && sink.quit == 0)
    }

    /// Owner, 29 Oct ("can we have Terminal in the window? so that the user stays on the same path?"): every step goes
    /// on in the window, so nothing brings Terminal to the front while setup runs, and no question brings the window
    /// back; only setup's hand-over (its last word to the window: it goes on in Terminal) brings Terminal.
    @Test func onlyTheHandOverBringsTerminal() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        for line in [progress, screen, done, admin] { bridge.fromSetup(Data(line.utf8)) }
        #expect(sink.fronts.isEmpty)
        bridge.fromSetup(Data(terminal.utf8))
        bridge.fromSetup(Data(handedDone.utf8))
        #expect(sink.fronts == ["terminal"])
    }

    /// An admin command goes to the window's admin runner, never to the page; its input and its end too.
    @Test func anAdminCommandGoesToTheRunnerNeverThePage() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        bridge.fromSetup(Data(admin.utf8))
        bridge.fromSetup(Data(#"{"t":"admin_input","id":"a1","text":"y\n"}"#.utf8))
        bridge.fromSetup(Data(#"{"t":"admin_end","id":"a1"}"#.utf8))
        #expect(sink.page.isEmpty)
        #expect(sink.admin == ["start a1 /usr/local/estia/bin/estia restart", "input a1 y\n", "end a1"])
    }

    @Test func anAdminCommandBeforeThePageIsReadyWaitsInItsTurn() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromSetup(Data(progress.utf8))
        bridge.fromSetup(Data(admin.utf8))
        #expect(sink.admin.isEmpty)
        bridge.fromPage(ready())
        #expect(sink.page == [progress] && sink.admin == ["start a1 /usr/local/estia/bin/estia restart"])
    }

    /// The runner's lines and its result go to setup, one line each.
    @Test func theRunnersLinesAndResultGoToSetup() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        bridge.adminLine(id: "a1", line: "✓ Restarted.")
        bridge.adminDone(id: "a1", code: 0, refused: false, unrun: false)
        bridge.adminDone(id: "a2", code: 127, refused: false, unrun: true)
        #expect(sink.setup.dropFirst() == [
            "{\"t\":\"admin_line\",\"id\":\"a1\",\"line\":\"✓ Restarted.\"}\n",
            "{\"t\":\"admin_done\",\"id\":\"a1\",\"code\":0,\"refused\":false}\n",
            "{\"t\":\"admin_done\",\"id\":\"a2\",\"code\":127,\"refused\":false,\"unrun\":true}\n",
        ])
    }

    /// A page message shaped as the runner's (a forged end, a forged line) ends the window: setup goes on in Terminal.
    @Test func aPageMessageShapedAsTheRunnersEndsTheWindow() {
        for body: NSDictionary in [
            ["t": "admin_done", "id": "a1", "code": NSNumber(value: 0), "refused": NSNumber(value: false)],
            ["t": "admin_line", "id": "a1", "line": "✓ Restarted."],
        ] {
            let sink = Recorder()
            let bridge = bridge(sink)
            bridge.fromPage(ready())
            bridge.fromPage(body)
            #expect(sink.setup.last == closed && sink.quit == 1)
        }
    }

    /// Without an admin runner (a window built without one), an admin command is refused at once, said as not run
    /// (`unrun`): setup goes on in Terminal, where sudo asks, never a wait without end.
    @Test func withoutARunnerAnAdminCommandIsNotRun() {
        let sink = Recorder()
        let bridge = BridgeCore(sink: sink, admin: nil)
        bridge.fromPage(ready())
        bridge.fromSetup(Data(admin.utf8))
        #expect(sink.setup.last == "{\"t\":\"admin_done\",\"id\":\"a1\",\"code\":127,\"refused\":false,\"unrun\":true}\n")
    }

    /// Owner's laptop, 29 Oct: setup stopped at Step 1 with its ✗ in Terminal only; the window got nothing but its
    /// input's end, said "Setup ended in Terminal." and left Terminal behind it. Setup's end without its last word
    /// (the closing list, a stop's screen, or the page's own Stop here) brings Terminal to the front: it says why.
    @Test func setupsEndWithoutItsLastWordBringsTerminal() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        bridge.fromSetup(Data(screen.utf8))
        bridge.endOfInput()
        #expect(sink.ended == 1 && sink.fronts == ["terminal"] && sink.quit == 0)
    }

    @Test func aStopsScreenTheClosingListOrStopHereIsTheLastWord() {
        for last in [Data(stopScreen.utf8), Data(done.utf8), nil] {
            let sink = Recorder()
            let bridge = bridge(sink)
            bridge.fromPage(ready())
            if let last { bridge.fromSetup(last) } else { bridge.fromPage(["t": "stop"] as NSDictionary) }
            bridge.endOfInput()
            #expect(sink.ended == 1 && sink.fronts.isEmpty && sink.quit == 0)
        }
    }

    /// Review W02: the page's Close (the only button on "Stopped") closed the window without asking, while the red
    /// button asks whether an admin command runs. Every close asks the core the same: never while one runs.
    @Test func theWindowClosesOnlyWhileNoAdminCommandRuns() {
        let sink = Recorder()
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        #expect(bridge.mayClose())
        sink.adminRunning = true
        #expect(!bridge.mayClose())
        sink.adminRunning = false
        #expect(bridge.mayClose() && BridgeCore(sink: sink, admin: nil).mayClose())
    }

    @Test func aWriteToSetupThatFailsMeansSetupEnded() {
        let sink = Recorder()
        sink.setupGone = true
        let bridge = bridge(sink)
        bridge.fromPage(ready())
        #expect(sink.ended == 1 && sink.setup.isEmpty)
    }
}

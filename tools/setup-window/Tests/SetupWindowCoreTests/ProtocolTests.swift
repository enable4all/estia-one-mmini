import Foundation
import Testing

@testable import SetupWindowCore

/// The fixtures setup's own tests read too (tests/test_setup_window.py): both sides keep the same rules.
enum Fixtures {
    static let folder = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("fixtures")

    static func lines(_ name: String) -> [String] {
        let text = (try? String(contentsOf: folder.appendingPathComponent(name), encoding: .utf8)) ?? ""
        return text.split(separator: "\n").map(String.init)
    }

    struct Bad: CustomTestStringConvertible, Sendable {
        let why: String
        let line: String
        var testDescription: String { why }
    }

    static func bad(_ name: String) -> [Bad] {
        lines(name).map { line in
            let case_ = try! JSONSerialization.jsonObject(with: Data(line.utf8)) as! [String: String]
            return Bad(why: case_["why"]!, line: case_["line"]!)
        }
    }
}

/// The window and setup speak version 2 of one protocol (setup_window.PROTOCOL on setup's side): 2 adds the steps'
/// admin commands, run by the window through macOS's own password box.
@Suite struct ProtocolVersion {
    @Test func theWindowSpeaksVersion2() {
        #expect(SetupProtocol.version == 2)
        #expect(SetupProtocol.fromEngineMax == 256 * 1024 && SetupProtocol.fromWindowMax == 64 * 1024)
    }

    /// The same list as setup's (setup_window.KINDS, read from the same file by its tests).
    @Test func theWindowDrawsEveryKindOfQuestion() throws {
        let data = try Data(contentsOf: Fixtures.folder.appendingPathComponent("kinds.json"))
        let kinds = try #require(try JSONSerialization.jsonObject(with: data) as? [String])
        #expect(Set(kinds) == SetupProtocol.kinds)
    }

    @Test func theFixturesAreThere() {
        #expect(Fixtures.lines("engine-good.jsonl").count >= 10 && Fixtures.lines("window-good.jsonl").count >= 8)
        #expect(Fixtures.bad("engine-bad.jsonl").count >= 20 && Fixtures.bad("window-bad.jsonl").count >= 10)
        #expect(Fixtures.lines("admin-good.jsonl").count >= 5 && Fixtures.bad("admin-bad.jsonl").count >= 5)
    }
}

/// What setup sends: each line is exactly one of its messages, or it is refused as a whole.
@Suite struct FromSetup {
    @Test(arguments: Fixtures.lines("engine-good.jsonl"))
    func everyMessageSetupSendsIsRead(line: String) throws {
        _ = try EngineMessage.decode(Data(line.utf8))
    }

    @Test(arguments: Fixtures.bad("engine-bad.jsonl"))
    func aMessageSetupShouldNeverSendIsRefused(bad: Fixtures.Bad) {
        #expect(throws: MessageError.self) { try EngineMessage.decode(Data(bad.line.utf8)) }
    }

    @Test func helloNamesHerAndAScreenItsQuestion() throws {
        let hello = try EngineMessage.decode(Data(#"{"t":"hello","v":2,"name":"Estia"}"#.utf8))
        #expect(hello == .hello(name: "Estia"))
        let screen = try EngineMessage.decode(Data(Fixtures.lines("engine-good.jsonl")[1].utf8))
        guard case .screen(let id, let kind) = screen else { Issue.record("not a screen"); return }
        #expect(id == "name" && kind == "choice")
    }

    @Test func aLineOverItsLimitIsRefused() {
        let filler = String(repeating: "x", count: SetupProtocol.fromEngineMax)
        let line = #"{"t":"progress","line":"\#(filler)"}"#
        #expect(throws: MessageError.tooLong) { try EngineMessage.decode(Data(line.utf8)) }
    }

    @Test func aLineThatIsNotUTF8IsRefused() {
        var bytes = Data(#"{"t":"progress","line":""#.utf8)
        bytes.append(0xFF)
        bytes.append(contentsOf: Data(#""}"#.utf8))
        #expect(throws: MessageError.self) { try EngineMessage.decode(bytes) }
    }
}

/// What the window sends: checked the same way before it leaves, then one line with `t` first.
@Suite struct FromTheWindow {
    @Test(arguments: Fixtures.lines("window-good.jsonl"))
    func everyMessageOfTheWindowIsReadAndWrittenBackTheSame(line: String) throws {
        let message = try WindowMessage.decode(Data(line.utf8))
        let written = try message.line()
        #expect(written.last == UInt8(ascii: "\n"))
        #expect(written.starts(with: Data(#"{"t":"#.utf8)))
        let again = try JSONSerialization.jsonObject(with: written) as? NSDictionary
        let given = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? NSDictionary
        #expect(again == given)
    }

    @Test(arguments: Fixtures.bad("window-bad.jsonl"))
    func aMessageTheWindowShouldNeverSendIsRefused(bad: Fixtures.Bad) {
        #expect(throws: MessageError.self) { try WindowMessage.decode(Data(bad.line.utf8)) }
    }

    @Test func aLineOverItsLimitIsRefusedAtTheLimitItIsNot() throws {
        let empty = #"{"t":"answer","id":"name","value":""}"#
        let value = String(repeating: "x", count: SetupProtocol.fromWindowMax - empty.utf8.count)
        let atLimit = try WindowMessage.decode(Data(#"{"t":"answer","id":"name","value":"\#(value)"}"#.utf8))
        #expect(atLimit == .answer(id: "name", value: .text(value)))
        #expect(throws: MessageError.tooLong) {
            try WindowMessage.decode(Data(#"{"t":"answer","id":"name","value":"\#(value)x"}"#.utf8))
        }
    }

    @Test func aValueIsTextAFlagAListOrFields() throws {
        #expect(try WindowMessage(body: ["t": "answer", "id": "move", "value": true]) == .answer(id: "move", value: .flag(true)))
        #expect(try WindowMessage(body: ["t": "answer", "id": "extras", "value": ["web"]]) == .answer(id: "extras", value: .list(["web"])))
        #expect(
            try WindowMessage(body: ["t": "check", "id": "home_area", "value": ["city": "Athens"]])
                == .check(id: "home_area", value: .fields(["city": "Athens"])))
        #expect(throws: MessageError.self) { try WindowMessage(body: ["t": "answer", "id": "move", "value": 1]) }
    }
}

/// The page's messages as WebKit hands them over (WKScriptMessage's body): a JavaScript number is a double, 1 too
/// (objCType "d" on macOS 27, checked with a WKWebView), and true and false are CFBooleans.
@Suite struct FromThePage {
    @Test func readyWithItsVersionAsADoubleIsRead() throws {
        #expect(try WindowMessage(body: ["t": "ready", "v": NSNumber(value: 2.0)]) == .ready)
        let webKit: NSDictionary = ["t": "ready" as NSString, "v": NSNumber(value: Double(2))]
        #expect(try WindowMessage(body: webKit) == .ready)
    }

    @Test(arguments: [
        NSNumber(value: 1.5), NSNumber(value: 1.0), NSNumber(value: 3.0), NSNumber(value: true), NSNumber(value: Double.nan),
        NSNumber(value: Double.infinity), NSNumber(value: 1e300),
    ])
    func aFractionABoolOrAnotherVersionIsRefused(version: NSNumber) {
        #expect(throws: MessageError.self) { try WindowMessage(body: ["t": "ready", "v": version]) }
    }

    @Test func setupsOwnLinesStillRefuseAFraction() {
        #expect(throws: MessageError.self) { try EngineMessage.decode(Data(#"{"t":"hello","v":2.0,"name":"Estia"}"#.utf8)) }
    }

    @Test func aYesAListAndFieldsFromThePageAreRead() throws {
        let yes: NSDictionary = ["t": "answer", "id": "move", "value": NSNumber(value: true)]
        #expect(try WindowMessage(body: yes) == .answer(id: "move", value: .flag(true)))
        let list: NSDictionary = ["t": "answer", "id": "extras", "value": ["web", "maps"] as NSArray]
        #expect(try WindowMessage(body: list) == .answer(id: "extras", value: .list(["web", "maps"])))
        let fields: NSDictionary = ["t": "check", "id": "home_area", "value": ["city": "Athens"] as NSDictionary]
        #expect(try WindowMessage(body: fields) == .check(id: "home_area", value: .fields(["city": "Athens"])))
    }
}

/// The steps' admin commands (protocol 2): setup asks the window to run one as root (`admin`), feeds its input
/// (`admin_input`, `admin_end`), and the window's admin runner, never the page, says each line and the result.
@Suite struct AdminMessages {
    @Test func anAdminCommandIsReadWithItsWordsAsGiven() throws {
        let line = #"{"t":"admin","id":"b12","argv":["/bin/echo","it's $(x) `y` \"z\" ; -n é"],"input":null,"open":false,"why":""}"#
        guard case .admin(let request) = try EngineMessage.decode(Data(line.utf8)) else {
            Issue.record("not an admin command")
            return
        }
        #expect(request == AdminRequest(id: "b12", argv: ["/bin/echo", "it's $(x) `y` \"z\" ; -n é"], input: nil, open: false, why: ""))
        let timed = #"{"t":"admin","id":"a4","argv":["/bin/echo"],"input":null,"open":false,"why":"","seconds":180}"#
        guard case .admin(let limited) = try EngineMessage.decode(Data(timed.utf8)) else {
            Issue.record("not an admin command")
            return
        }
        #expect(limited.seconds == 180 && request.seconds == nil)  // a time limit only when setup gives one
        let fed = try EngineMessage.decode(Data(#"{"t":"admin_input","id":"a3","text":"y\n"}"#.utf8))
        #expect(fed == .adminInput(id: "a3", text: "y\n"))
        #expect(try EngineMessage.decode(Data(#"{"t":"admin_end","id":"a3"}"#.utf8)) == .adminEnd(id: "a3"))
    }

    @Test func aProgressLineMayTakeThePlaceOfTheOneBefore() throws {
        #expect(try EngineMessage.decode(Data(#"{"t":"progress","line":"40%","again":true}"#.utf8)) == .progress("40%", again: true))
        #expect(try EngineMessage.decode(Data(#"{"t":"progress","line":"done"}"#.utf8)) == .progress("done", again: false))
    }

    /// What the runner says: each line and the result, one line each with `t` first, as setup reads them.
    @Test(arguments: Fixtures.lines("admin-good.jsonl"))
    func everyAdminMessageIsWrittenAsSetupReadsIt(line: String) throws {
        let message = try AdminMessage.decode(Data(line.utf8))
        let written = try message.line()
        #expect(written.last == UInt8(ascii: "\n") && written.starts(with: Data(#"{"t":"#.utf8)))
        let again = try JSONSerialization.jsonObject(with: written) as? NSDictionary
        let given = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? NSDictionary
        #expect(again == given)
    }

    @Test(arguments: Fixtures.bad("admin-bad.jsonl"))
    func anAdminMessageOfAnotherShapeIsRefused(bad: Fixtures.Bad) {
        #expect(throws: MessageError.self) { try AdminMessage.decode(Data(bad.line.utf8)) }
    }

    /// The page can neither say an admin command's lines nor its end: never one of its messages.
    @Test(arguments: Fixtures.lines("admin-good.jsonl"))
    func thePageCannotSendAnAdminMessage(line: String) throws {
        let body = try JSONSerialization.jsonObject(with: Data(line.utf8))
        #expect(throws: MessageError.self) { try WindowMessage(body: body) }
    }
}

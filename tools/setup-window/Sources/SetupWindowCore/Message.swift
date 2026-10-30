import Foundation

/// The protocol between setup (the engine, Python: src/local_assistant/setup_window.py) and this window: one JSON
/// object per line, UTF-8, `t` first. Setup writes to the window's stdin, the window answers on its stdout. Each side
/// refuses a line that is not exactly one of its messages, by the same rules (tested on the shared fixtures in
/// Tests/SetupWindowCoreTests/fixtures):
///
///     setup  -> window   hello {v, name}; screen {id, kind, step, steps, title, lead, help, ask, note, options,
///                        fields, placeholder, recommended, layout, back, kept?, live?, links?, later?, more?, stop?};
///                        checked {id, ok, message, shown, live}; progress {line, again?}; terminal {why}; done {lines};
///                        admin {id, argv, input, open, why, seconds?}; admin_input {id, text}; admin_end {id}
///     window -> setup    ready {v}; check {id, value}; answer {id, value}; back; stop; closed (the page's, and the
///                        window's own closed); admin_line {id, line}; admin_done {id, code, refused, unrun?} (the
///                        admin runner's only: never the page's)
///
/// A value is a string, a bool, a list of strings or an object of strings. A secret travels from the window to setup,
/// and from setup only into an admin command's stdin (admin's input, admin_input): never shown, sent back to the page,
/// put in a program's arguments, saved or logged. `terminal` is setup's hand-over: it goes on in Terminal from there.
public enum SetupProtocol {
    /// setup_window.PROTOCOL on setup's side: both say it first (`hello`, `ready`). 2: the steps run in the window,
    /// their admin commands through macOS's own password box.
    public static let version = 2
    /// bytes in one line setup sends
    public static let fromEngineMax = 256 * 1024
    /// bytes in one line the window sends
    public static let fromWindowMax = 64 * 1024
    /// the question list's kinds (setup_questions.Kind), and the window's own list of kept answers
    public static let kinds: Set<String> = [
        "info", "choice", "cards", "yes_no", "text", "number", "hidden", "code", "pin", "key", "folder", "fields",
        "switches", "wait", "terminal", "progress", "kept",
    ]
}

public enum MessageError: Error, Equatable {
    case tooLong
    case notJSON
    /// not one of this side's messages, or one with fields of another shape
    case notMessage(String)
}

/// An answer as the window gives it: text, a yes or no, the options picked, or each field's text.
public enum Value: Equatable, Sendable {
    case text(String)
    case flag(Bool)
    case list([String])
    case fields([String: String])

    init?(_ raw: Any) {
        if Rule.isFlag(raw), let flag = raw as? Bool {
            self = .flag(flag)
        } else if Rule.text.holds(raw), let text = raw as? String {
            self = .text(text)
        } else if Rule.texts.holds(raw), let list = raw as? [String] {
            self = .list(list)
        } else if let object = raw as? [String: Any], object.allSatisfy({ Rule.text.holds($0.key) && Rule.text.holds($0.value) }) {
            self = .fields(object.compactMapValues { $0 as? String })
        } else {
            return nil
        }
    }

    var json: Any {
        switch self {
        case .text(let text): return text
        case .flag(let flag): return flag
        case .list(let list): return list
        case .fields(let fields): return fields
        }
    }
}

/// The rules each field of a message keeps: the same as setup_window.py's checks.
indirect enum Rule {
    case text, flag, count, positive, qid, name, kind, version, pageVersion, value, texts, pair, link
    case token, argv, why, exitCode, seconds
    case maybe(Rule)
    case list(Rule)
    case shape([String: Rule], optional: [String: Rule] = [:])

    private static let qidPattern = try! NSRegularExpression(pattern: "^[a-z_]{1,32}$")
    private static let tokenPattern = try! NSRegularExpression(pattern: "^[a-z0-9]{1,16}$")
    private static let httpsPattern = try! NSRegularExpression(pattern: #"^https://[^\s/?#]+[^\s]*$"#)

    /// JSON's true and false: the CFBoolean numbers, never 0 or 1.
    static func isFlag(_ value: Any) -> Bool {
        guard let number = value as? NSNumber else { return false }
        return CFGetTypeID(number) == CFBooleanGetTypeID()
    }

    static func whole(_ value: Any) -> Int? {
        guard let number = value as? NSNumber, !isFlag(number), !CFNumberIsFloatType(number) else { return nil }
        return number.intValue
    }

    /// A number from the page: WebKit hands every JavaScript number over as a double (1 is 1.0), so a whole one is
    /// taken in either form; never a fraction, a bool or one past 2^53. Only the page's messages read it: setup's
    /// lines are JSON text, where a whole number has no point (`whole`).
    private static func pageWhole(_ value: Any) -> Int? {
        if let number = whole(value) { return number }
        guard let number = value as? NSNumber, !isFlag(number) else { return nil }
        let double = number.doubleValue
        guard double == double.rounded(), abs(double) < 9_007_199_254_740_992 else { return nil }
        return Int(double)
    }

    private static func matches(_ pattern: NSRegularExpression, _ text: String) -> Bool {
        pattern.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
    }

    func holds(_ value: Any) -> Bool {
        switch self {
        case .text:
            guard let text = value as? String, !Rule.isFlag(value) else { return false }
            return !text.unicodeScalars.contains("\0")
        case .flag:
            return Rule.isFlag(value)
        case .count:
            return (Rule.whole(value) ?? -1) >= 0
        case .positive:
            return (Rule.whole(value) ?? 0) > 0
        case .qid:
            guard let text = value as? String else { return false }
            return Rule.matches(Rule.qidPattern, text)
        case .name:
            guard Rule.text.holds(value), let text = value as? String else { return false }
            return (1...64).contains(text.unicodeScalars.count) && !text.contains("\n")
        case .kind:
            guard let text = value as? String else { return false }
            return SetupProtocol.kinds.contains(text)
        case .version:
            return Rule.whole(value) == SetupProtocol.version
        case .pageVersion:
            return Rule.pageWhole(value) == SetupProtocol.version
        case .value:
            return Value(value) != nil
        case .texts:
            return Rule.list(.text).holds(value)
        case .pair:
            guard let pair = value as? [Any], pair.count == 2 else { return false }
            return pair.allSatisfy { Rule.text.holds($0) }
        case .link:
            guard Rule.pair.holds(value), let pair = value as? [String] else { return false }
            return Rule.matches(Rule.httpsPattern, pair[1])
        case .token:  // an admin command's id, setup's own
            guard let text = value as? String else { return false }
            return Rule.matches(Rule.tokenPattern, text)
        case .argv:
            guard let words = value as? [Any], words.allSatisfy({ Rule.text.holds($0) }) else { return false }
            return AdminCore.argvProblem(words.compactMap { $0 as? String }) == nil
        case .why:
            guard Rule.text.holds(value), let text = value as? String else { return false }
            return AdminCore.whyProblem(text) == nil
        case .exitCode:
            guard let code = Rule.whole(value) else { return false }
            return (0...255).contains(code)
        case .seconds:  // an admin command's time limit: a whole number of seconds, at most a day
            guard let seconds = Rule.whole(value) else { return false }
            return (1...AdminCore.maxSeconds).contains(seconds)
        case .maybe(let rule):
            return value is NSNull || rule.holds(value)
        case .list(let rule):
            guard let items = value as? [Any] else { return false }
            return items.allSatisfy { rule.holds($0) }
        case .shape(let fields, let optional):
            guard let object = value as? [String: Any] else { return false }
            let keys = Set(object.keys)
            let needed = Set(fields.keys)
            guard needed.isSubset(of: keys), keys.isSubset(of: needed.union(optional.keys)) else { return false }
            return object.allSatisfy { key, item in (fields[key] ?? optional[key])?.holds(item) ?? false }
        }
    }
}

private let guide = Rule.shape([
    "needs": .text, "steps": .texts, "check": .text, "errors": .list(.pair), "links": .list(.link),
])
private let option = Rule.shape([
    "id": .name, "label": .text, "line": .text, "tag": .text, "on": .flag, "guide": .maybe(guide),
])
private let field = Rule.shape(["id": .name, "label": .text, "placeholder": .text, "max_chars": .positive])
private let layout = Rule.shape(
    ["rows": .positive, "columns": .positive, "group": .positive, "label": .text, "visible": .flag],
    optional: ["sizes": .list(.positive), "joiner": .text])  // each group's size when they differ, what joins them

private let engineRules: [String: Rule] = [
    "hello": .shape(["t": .text, "v": .version, "name": .text]),
    "screen": .shape(
        [
            "t": .text, "id": .qid, "kind": .kind, "step": .count, "steps": .count, "title": .text, "lead": .text,
            "help": .text, "ask": .text, "note": .text, "options": .list(option), "fields": .list(field),
            "placeholder": .text, "recommended": .maybe(.text), "layout": .maybe(layout), "back": .flag,
        ],
        // later: its button's label; more: the More fold's text; stop: the quiet way out's line (Stop here)
        optional: [
            "kept": .value, "live": .flag, "links": .list(.link), "later": .text, "more": .text, "stop": .text,
        ]),
    "checked": .shape(["t": .text, "id": .qid, "ok": .flag, "message": .text, "shown": .text, "live": .flag]),
    "progress": .shape(["t": .text, "line": .text], optional: ["again": .flag]),  // again: in the place of the last
    "terminal": .shape(["t": .text, "why": .text]),
    "done": .shape(["t": .text, "lines": .texts]),
    "admin": .shape(  // seconds: its time limit, when setup gives one
        ["t": .text, "id": .token, "argv": .argv, "input": .maybe(.text), "open": .flag, "why": .why],
        optional: ["seconds": .seconds]),
    "admin_input": .shape(["t": .text, "id": .token, "text": .text]),
    "admin_end": .shape(["t": .text, "id": .token]),
]
/// The admin runner's messages to setup: never the page's (WindowMessage has none of them).
private let adminRules: [String: Rule] = [
    "admin_line": .shape(["t": .text, "id": .token, "line": .text]),
    "admin_done": .shape(  // unrun: the command never started (no box, the wrapper's failure first)
        ["t": .text, "id": .token, "code": .exitCode, "refused": .flag], optional: ["unrun": .flag]),
]
private let windowRules: [String: Rule] = [
    "ready": .shape(["t": .text, "v": .pageVersion]),  // the page's 1 comes as a double
    "check": .shape(["t": .text, "id": .qid, "value": .value]),
    "answer": .shape(["t": .text, "id": .qid, "value": .value]),
    "back": .shape(["t": .text]),
    "stop": .shape(["t": .text]),
    "closed": .shape(["t": .text]),
]

/// One object checked against one side's messages; its `t`.
private func shaped(_ body: Any, _ rules: [String: Rule]) throws -> (String, [String: Any]) {
    guard let object = body as? [String: Any] else { throw MessageError.notMessage("not an object") }
    guard let t = object["t"] as? String, let rule = rules[t] else {
        throw MessageError.notMessage("not a message of this side")
    }
    guard rule.holds(object) else { throw MessageError.notMessage("a \(t) message with fields of another shape") }
    return (t, object)
}

/// One line as JSON: at most `limit` bytes (its newline left out), UTF-8.
private func parsed(_ line: Data, limit: Int) throws -> Any {
    var body = line
    if body.last == UInt8(ascii: "\n") { body.removeLast() }
    guard body.count <= limit else { throw MessageError.tooLong }
    guard String(data: body, encoding: .utf8) != nil,
        let object = try? JSONSerialization.jsonObject(with: body, options: [])
    else { throw MessageError.notJSON }
    return object
}

/// A message from setup. The page gets the line itself once it is checked; the window reads only these.
public enum EngineMessage: Equatable, Sendable {
    case hello(name: String)
    case screen(id: String, kind: String)
    case checked(id: String, ok: Bool)
    case progress(String, again: Bool)
    case terminal(String)
    case done([String])
    case admin(AdminRequest)
    case adminInput(id: String, text: String)
    case adminEnd(id: String)

    public static func decode(_ line: Data) throws -> EngineMessage {
        let (t, object) = try shaped(try parsed(line, limit: SetupProtocol.fromEngineMax), engineRules)
        let text = { (key: String) in object[key] as? String ?? "" }
        switch t {
        case "hello": return .hello(name: text("name"))
        case "screen": return .screen(id: text("id"), kind: text("kind"))
        case "checked": return .checked(id: text("id"), ok: object["ok"] as? Bool ?? false)
        case "progress": return .progress(text("line"), again: object["again"] as? Bool ?? false)
        case "terminal": return .terminal(text("why"))
        case "admin":
            return .admin(
                AdminRequest(
                    id: text("id"), argv: object["argv"] as? [String] ?? [], input: object["input"] as? String,
                    open: object["open"] as? Bool ?? false, why: text("why"),
                    seconds: object["seconds"].flatMap { Rule.whole($0) }))
        case "admin_input": return .adminInput(id: text("id"), text: text("text"))
        case "admin_end": return .adminEnd(id: text("id"))
        default: return .done(object["lines"] as? [String] ?? [])
        }
    }
}

/// A message of the window to setup: from the page (WKScriptMessage's body) or the window itself (closed).
public enum WindowMessage: Equatable, Sendable {
    case ready
    case check(id: String, value: Value)
    case answer(id: String, value: Value)
    case back
    case stop
    case closed

    /// The page's message, checked: exactly one of the window's messages, or refused.
    public init(body: Any) throws {
        let (t, object) = try shaped(body, windowRules)
        let id = object["id"] as? String ?? ""
        switch t {
        case "ready": self = .ready
        case "check", "answer":
            guard let value = object["value"].flatMap({ Value($0) }) else { throw MessageError.notMessage("value") }
            self = t == "check" ? .check(id: id, value: value) : .answer(id: id, value: value)
        case "back": self = .back
        case "stop": self = .stop
        default: self = .closed
        }
    }

    public static func decode(_ line: Data) throws -> WindowMessage {
        try WindowMessage(body: try parsed(line, limit: SetupProtocol.fromWindowMax))
    }

    /// The line setup reads: `t` first, at most fromWindowMax bytes, then a newline.
    public func line() throws -> Data {
        switch self {
        case .ready: return try written([("t", "ready"), ("v", SetupProtocol.version)])
        case .check(let id, let value): return try written([("t", "check"), ("id", id), ("value", value.json)])
        case .answer(let id, let value): return try written([("t", "answer"), ("id", id), ("value", value.json)])
        case .back: return try written([("t", "back")])
        case .stop: return try written([("t", "stop")])
        case .closed: return try written([("t", "closed")])
        }
    }
}

/// What the window's admin runner says to setup about the command it runs as root: each line of its output, then its
/// result (its exit code, or that macOS's box was refused). Only the runner sends these: the page never can.
public enum AdminMessage: Equatable, Sendable {
    case line(id: String, line: String)
    case done(id: String, code: Int, refused: Bool, unrun: Bool = false)

    /// Characters in one line of a command's output (OutputLines cuts a longer one): even at 4 bytes each, with its
    /// JSON escapes, a line stays under fromWindowMax.
    public static let lineMax = 4096

    public static func decode(_ line: Data) throws -> AdminMessage {
        let (t, object) = try shaped(try parsed(line, limit: SetupProtocol.fromWindowMax), adminRules)
        let id = object["id"] as? String ?? ""
        if t == "admin_line" { return .line(id: id, line: object["line"] as? String ?? "") }
        return .done(
            id: id, code: (object["code"] as? NSNumber)?.intValue ?? 0, refused: object["refused"] as? Bool ?? false,
            unrun: object["unrun"] as? Bool ?? false)
    }

    public func line() throws -> Data {
        switch self {
        case .line(let id, let text): return try written([("t", "admin_line"), ("id", id), ("line", text)])
        case .done(let id, let code, let refused, let unrun):  // unrun said only when true
            let fields: [(String, Any)] = [("t", "admin_done"), ("id", id), ("code", code), ("refused", refused)]
            return try written(unrun ? fields + [("unrun", true)] : fields)
        }
    }
}

/// One message to setup as its line: `t` first, then the fields in the order given; at most fromWindowMax bytes, then
/// a newline.
private func written(_ fields: [(String, Any)]) throws -> Data {
    var line = Data("{".utf8)
    for (index, (key, value)) in fields.enumerated() {
        if index > 0 { line.append(contentsOf: Data(",".utf8)) }
        line.append(try JSONSerialization.data(withJSONObject: key, options: [.fragmentsAllowed]))
        line.append(contentsOf: Data(":".utf8))
        line.append(try JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed, .sortedKeys]))
    }
    line.append(contentsOf: Data("}".utf8))
    guard line.count <= SetupProtocol.fromWindowMax else { throw MessageError.tooLong }
    line.append(UInt8(ascii: "\n"))
    return line
}

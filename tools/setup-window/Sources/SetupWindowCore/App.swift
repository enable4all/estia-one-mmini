import Foundation

/// How setup runs the window: `estia-setup --page <folder> --manifest <sha256 of page.json>
/// [--terminal-app <bundle id>] [--check]`. Anything else is refused as a whole, never guessed.
public struct Arguments: Equatable, Sendable {
    public let page: String  // the page folder: an absolute path
    public let manifest: String  // page.json's sha256, from setup, which built the folder
    public let terminalApp: String?  // the terminal setup runs in (Continue in Terminal brings it to the front)
    public let check: Bool  // only check the page and its files, then end: 0 when they check out, else 1

    private static let bundleID = try! NSRegularExpression(pattern: "^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$")
    private static let hex = try! NSRegularExpression(pattern: "^[0-9a-f]{64}$")

    public static func parse(_ arguments: [String]) -> Arguments? {
        var page: String?, manifest: String?, terminalApp: String?, check = false
        var rest = arguments[...]
        while let flag = rest.popFirst() {
            switch flag {
            case "--check":
                guard !check else { return nil }
                check = true
            case "--page", "--manifest", "--terminal-app":
                guard let value = rest.popFirst() else { return nil }
                switch flag {
                case "--page":
                    guard page == nil, value.hasPrefix("/") else { return nil }
                    page = value
                case "--manifest":
                    guard manifest == nil, Manifest.matches(hex, value) else { return nil }
                    manifest = value
                default:
                    guard terminalApp == nil, Manifest.matches(bundleID, value) else { return nil }
                    terminalApp = value
                }
            default:
                return nil
            }
        }
        guard let page, let manifest else { return nil }
        return Arguments(page: page, manifest: manifest, terminalApp: terminalApp, check: check)
    }
}

/// What the page asks of the window itself (the "native" handler), never sent to setup.
public enum NativeRequest: Equatable, Sendable {
    case pickFolder  // the Mac's folder picker; the folder goes back to the page (window.estia.picked)
    case copy(String)  // onto the pasteboard (the one-time word)
    case terminal  // bring the terminal setup runs in to the front
    case close  // close the window (after setup ended)

    public static let maxCopy = 1024

    public init(body: Any) throws {
        guard let object = body as? [String: Any], let t = object["t"] as? String else {
            throw MessageError.notMessage("not a request")
        }
        let keys = Set(object.keys)
        switch t {
        case "pick_folder" where keys == ["t"]: self = .pickFolder
        case "terminal" where keys == ["t"]: self = .terminal
        case "close" where keys == ["t"]: self = .close
        case "copy" where keys == ["t", "text"]:
            guard Rule.text.holds(object["text"] as Any), let text = object["text"] as? String,
                text.unicodeScalars.count <= NativeRequest.maxCopy
            else { throw MessageError.notMessage("copy") }
            self = .copy(text)
        default:
            throw MessageError.notMessage("not a request of the page")
        }
    }
}

/// setup's lines as they come in on stdin, in chunks: each whole line out (its newline left out, empty ones
/// skipped); a line growing past `limit` bytes is refused before its end comes.
public struct Lines: Sendable {
    public let limit: Int
    public private(set) var pending = Data()

    public init(limit: Int) { self.limit = limit }

    public mutating func add(_ chunk: Data) throws -> [Data] {
        pending.append(chunk)
        var lines: [Data] = []
        while let end = pending.firstIndex(of: UInt8(ascii: "\n")) {
            let line = pending[pending.startIndex..<end]
            pending = Data(pending[pending.index(after: end)...])
            guard line.count <= limit else { throw MessageError.tooLong }
            if !line.isEmpty { lines.append(Data(line)) }
        }
        guard pending.count <= limit else { throw MessageError.tooLong }
        return lines
    }
}

/// The page folder, checked as a whole before the window opens: page.json is the one setup built (by its sha256)
/// and every file it lists is there, at that name, with its sha256. The server for its files, or nil.
public func checkedPage(_ arguments: Arguments) -> PageServer? {
    let folder = PageFolder(URL(fileURLWithPath: arguments.page, isDirectory: true))
    guard let data = folder.read("page.json"), let manifest = try? Manifest.read(data, sha256: arguments.manifest)
    else { return nil }
    let server = PageServer(manifest: manifest, read: { folder.read($0) })
    for file in manifest.files {
        guard server.respond(to: URL(string: "\(PageServer.scheme)://\(PageServer.host)/\(file.path)")!).status == 200
        else { return nil }
    }
    return server
}

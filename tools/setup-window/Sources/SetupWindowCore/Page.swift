import CryptoKit
import Darwin
import Foundation

public enum PageError: Error, Equatable {
    /// page.json is not the one setup built (its fingerprint differs), or not of its form
    case manifest(String)
}

/// One of the page's files as page.json lists it.
public struct PageFile: Equatable, Sendable {
    public let path: String  // relative to the page folder: "page.html", "fonts/x.woff2"
    public let sha256: String  // 64 hex digits, small letters
    public let type: String  // its Content-Type
}

/// page.json, written by setup when it builds the window (setup_window.build): the page's files, each with its
/// fingerprint and its type. It is read only when its own fingerprint is the one setup gives on the command line.
public struct Manifest: Equatable, Sendable {
    public static let version = 1
    public static let maxFiles = 64
    /// the types a page file may have: the page, its styles and scripts, the intro's voice, a font, a picture
    public static let types: Set<String> = [
        "text/html; charset=utf-8", "text/css; charset=utf-8", "text/javascript; charset=utf-8", "audio/mp4",
        "font/woff2", "image/svg+xml", "image/png",
    ]
    private static let pathPattern = try! NSRegularExpression(
        pattern: "^[a-z0-9][a-z0-9_-]*(\\.[a-z0-9]+)*(/[a-z0-9][a-z0-9_-]*(\\.[a-z0-9]+)*)*$")
    private static let hexPattern = try! NSRegularExpression(pattern: "^[0-9a-f]{64}$")

    public let files: [PageFile]

    public func file(_ path: String) -> PageFile? { files.first { $0.path == path } }

    public static func fingerprint(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    static func matches(_ pattern: NSRegularExpression, _ text: String) -> Bool {
        pattern.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
    }

    /// A page path: small letters, digits, dots, dashes and underscores, folders by "/", never "..", "." or a
    /// leading "/": what a page URL's path may be, exactly, with nothing to decode.
    public static func isPagePath(_ path: String) -> Bool {
        matches(pathPattern, path)
    }

    public static func read(_ data: Data, sha256 expected: String) throws -> Manifest {
        guard fingerprint(data) == expected else { throw PageError.manifest("not the page.json setup built") }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            Set(object.keys) == ["v", "files"], (object["v"] as? NSNumber)?.intValue == version,
            !Rule.isFlag(object["v"] as Any), let entries = object["files"] as? [[String: Any]],
            (1...maxFiles).contains(entries.count)
        else { throw PageError.manifest("not of page.json's form") }
        var files: [PageFile] = []
        for entry in entries {
            guard Set(entry.keys) == ["path", "sha256", "type"], let path = entry["path"] as? String,
                let sha256 = entry["sha256"] as? String, let type = entry["type"] as? String,
                isPagePath(path), matches(hexPattern, sha256), types.contains(type),
                !files.contains(where: { $0.path == path })
            else { throw PageError.manifest("a file listed in another form") }
            files.append(PageFile(path: path, sha256: sha256, type: type))
        }
        guard files.contains(where: { $0.path == "page.html" }) else { throw PageError.manifest("no page.html") }
        return Manifest(files: files)
    }
}

/// An answer of the scheme handler: the file and its headers, or a refusal (empty, with the same rules).
public struct PageAnswer: Equatable, Sendable {
    public let status: Int
    public let headers: [String: String]
    public let body: Data

    public init(status: Int, headers: [String: String], body: Data) {
        self.status = status
        self.headers = headers
        self.body = body
    }
}

/// What the scheme handler serves for `estia-setup://app/<path>`: only a file page.json lists, its bytes checked
/// against their fingerprint each time, with the page's content rules. Anything else is refused.
public struct PageServer: Sendable {
    public static let scheme = "estia-setup"
    public static let host = "app"
    public static let start = URL(string: "estia-setup://app/page.html")!
    /// The same rules as the meta tag in page.html: the page's own files only, no inline script or style, nothing
    /// from the network, no forms, no frames.
    public static let contentSecurityPolicy =
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
        + "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    public static let maxBytes = 8 * 1024 * 1024

    public let manifest: Manifest
    public let read: @Sendable (String) -> Data?

    public init(manifest: Manifest, read: @escaping @Sendable (String) -> Data?) {
        self.manifest = manifest
        self.read = read
    }

    private static func headers(_ type: String?, length: Int) -> [String: String] {
        var headers = [
            "Content-Security-Policy": contentSecurityPolicy, "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store", "Content-Length": String(length),
        ]
        if let type { headers["Content-Type"] = type }
        return headers
    }

    private static func refused(_ status: Int) -> PageAnswer {
        PageAnswer(status: status, headers: headers(nil, length: 0), body: Data())
    }

    /// The page path of an address of the page's own, or nil: the scheme and host exactly, no user, port, query or
    /// fragment, and a path that needs no decoding (no "%", so no "%2e%2e" or "%2F").
    public static func pagePath(_ url: URL) -> String? {
        guard url.scheme == scheme, url.host(percentEncoded: true) == host, url.user == nil, url.password == nil,
            url.port == nil, url.query == nil, url.fragment == nil
        else { return nil }
        let raw = url.path(percentEncoded: true)
        guard raw.hasPrefix("/") else { return nil }
        let path = String(raw.dropFirst())
        return Manifest.isPagePath(path) ? path : nil
    }

    public func respond(to url: URL) -> PageAnswer {
        guard let path = PageServer.pagePath(url), let file = manifest.file(path) else { return PageServer.refused(404) }
        guard let body = read(path) else { return PageServer.refused(404) }
        guard body.count <= PageServer.maxBytes, Manifest.fingerprint(body) == file.sha256 else {
            return PageServer.refused(403)  // changed since setup built it: never served
        }
        return PageAnswer(status: 200, headers: PageServer.headers(file.type, length: body.count), body: body)
    }
}

/// The page folder on disk: a file is read only when it is a regular file at that name, never through a link.
public struct PageFolder: Sendable {
    public let root: URL

    public init(_ root: URL) { self.root = root }

    public func read(_ path: String) -> Data? {
        guard Manifest.isPagePath(path) else { return nil }
        let descriptor = open(root.appendingPathComponent(path).path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)
        guard descriptor >= 0 else { return nil }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        var info = stat()
        guard fstat(descriptor, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
            Int(info.st_size) <= PageServer.maxBytes
        else { return nil }
        return try? handle.readToEnd() ?? Data()
    }
}

/// Where an address goes when the page asks to go there.
public enum LinkDecision: Equatable, Sendable {
    case load  // the page itself, in the window
    case openInBrowser(URL)  // an https link the person clicked: the Mac's browser, never the window
    case drop
}

public enum LinkPolicy {
    public static func decide(_ url: URL?, clicked: Bool, mainFrame: Bool) -> LinkDecision {
        guard let url else { return .drop }
        if url.scheme == PageServer.scheme {
            return mainFrame && PageServer.pagePath(url) == "page.html" ? .load : .drop
        }
        guard url.scheme == "https", clicked, let host = url.host(percentEncoded: true), !host.isEmpty,
            url.user == nil, url.password == nil
        else { return .drop }
        return .openInBrowser(url)
    }
}

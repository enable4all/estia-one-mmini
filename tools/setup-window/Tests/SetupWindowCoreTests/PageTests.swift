import CryptoKit
import Foundation
import Testing

@testable import SetupWindowCore

private func hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private let page = Data("<!doctype html><title>Estia Setup</title>".utf8)
private let script = Data("window.estia = {};".utf8)

private func manifestData(_ files: [[String: String]], version: Int = 1) -> Data {
    try! JSONSerialization.data(withJSONObject: ["v": version, "files": files], options: [.sortedKeys])
}

private let goodFiles: [[String: String]] = [
    ["path": "page.html", "sha256": hex(page), "type": "text/html; charset=utf-8"],
    ["path": "page.js", "sha256": hex(script), "type": "text/javascript; charset=utf-8"],
]

/// page.json: the page's files, each with its fingerprint and its type; itself checked by the fingerprint setup
/// gives on the command line (it built the folder).
@Suite struct ManifestReading {
    @Test func aManifestSetupBuiltIsRead() throws {
        let data = manifestData(goodFiles)
        let manifest = try Manifest.read(data, sha256: hex(data))
        #expect(manifest.files.map(\.path) == ["page.html", "page.js"])
        #expect(manifest.file("page.js")?.type == "text/javascript; charset=utf-8")
        #expect(manifest.file("other.js") == nil)
    }

    @Test func aManifestThatChangedSinceSetupBuiltItIsRefused() {
        let data = manifestData(goodFiles)
        #expect(throws: PageError.self) { try Manifest.read(data, sha256: String(repeating: "0", count: 64)) }
    }

    @Test(arguments: ["../page.html", "/page.html", "a//b.js", "Page.html", "page html", "fonts/../page.js", ".hidden",
                      "page.html/", ""])
    func aPathOutsideThePageFolderOrOfAnotherFormIsRefused(path: String) {
        let files = goodFiles + [["path": path, "sha256": hex(script), "type": "text/javascript; charset=utf-8"]]
        let data = manifestData(files)
        #expect(throws: PageError.self) { try Manifest.read(data, sha256: hex(data)) }
    }

    @Test func aManifestWithoutThePageTwiceTheSameFileOrAnotherTypeIsRefused() {
        let cases: [[[String: String]]] = [
            [goodFiles[1]],
            goodFiles + [goodFiles[1]],
            [goodFiles[0], ["path": "run.sh", "sha256": hex(script), "type": "text/x-shellscript"]],
            [goodFiles[0], ["path": "page.js", "sha256": "abc", "type": "text/javascript; charset=utf-8"]],
        ]
        for files in cases {
            let data = manifestData(files)
            #expect(throws: PageError.self) { try Manifest.read(data, sha256: hex(data)) }
        }
        let other = manifestData(goodFiles, version: 2)
        #expect(throws: PageError.self) { try Manifest.read(other, sha256: hex(other)) }
    }
}

/// The scheme handler's answers: only the listed files, each checked before it is served, with the page's rules.
@Suite struct PageServing {
    private func server(_ files: [String: Data] = ["page.html": page, "page.js": script]) throws -> PageServer {
        let data = manifestData(goodFiles)
        return PageServer(manifest: try Manifest.read(data, sha256: hex(data)), read: { files[$0] })
    }

    @Test func aListedFileIsServedWithItsTypeAndThePagesRules() throws {
        let answer = try server().respond(to: URL(string: "estia-setup://app/page.js")!)
        #expect(answer.status == 200 && answer.body == script)
        #expect(answer.headers["Content-Type"] == "text/javascript; charset=utf-8")
        #expect(answer.headers["Content-Security-Policy"] == PageServer.contentSecurityPolicy)
        #expect(answer.headers["X-Content-Type-Options"] == "nosniff")
        #expect(answer.headers["Cache-Control"] == "no-store")
    }

    @Test func thePagesRulesAllowNothingButItsOwnFiles() {
        #expect(
            PageServer.contentSecurityPolicy
                == "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                + "media-src 'self' blob:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
                + "frame-ancestors 'none'")
    }

    @Test(arguments: [
        "estia-setup://app/other.js", "estia-setup://app/../page.js", "estia-setup://app/%2e%2e/page.js",
        "estia-setup://app/fonts%2Fpage.js", "estia-setup://app/page.js?x=1", "estia-setup://other/page.js",
        "estia-setup://app:8080/page.js", "https://app/page.js", "estia-setup://app/", "estia-setup://user@app/page.js",
    ])
    func anythingElseIsRefused(address: String) throws {
        let answer = try server().respond(to: URL(string: address)!)
        #expect(answer.status != 200 && answer.body.isEmpty)
        #expect(answer.headers["Content-Security-Policy"] == PageServer.contentSecurityPolicy)
    }

    @Test func aFileThatChangedSinceTheBuildOrIsGoneIsRefused() throws {
        let changed = try server(["page.html": page, "page.js": Data("window.evil = 1;".utf8)])
        #expect(changed.respond(to: URL(string: "estia-setup://app/page.js")!).status == 403)
        let gone = try server(["page.html": page])
        #expect(gone.respond(to: URL(string: "estia-setup://app/page.js")!).status == 404)
    }

    @Test func aFolderOnDiskServesOnlyRegularFilesNeverALink() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("page-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        try script.write(to: folder.appendingPathComponent("page.js"))
        try FileManager.default.createSymbolicLink(
            at: folder.appendingPathComponent("page.html"), withDestinationURL: folder.appendingPathComponent("page.js"))
        let disk = PageFolder(folder)
        #expect(disk.read("page.js") == script)
        #expect(disk.read("page.html") == nil)  // a link, never followed
        #expect(disk.read("missing.js") == nil)
    }
}

/// Where a link goes: the page itself loads in the window; an https link the person clicks opens in the Mac's
/// browser; nothing else goes anywhere.
@Suite struct Links {
    @Test func onlyThePageItselfLoadsInTheWindow() {
        #expect(LinkPolicy.decide(URL(string: "estia-setup://app/page.html"), clicked: false, mainFrame: true) == .load)
        #expect(LinkPolicy.decide(URL(string: "estia-setup://app/page.html"), clicked: false, mainFrame: false) == .drop)
        #expect(LinkPolicy.decide(URL(string: "estia-setup://app/intro.m4a"), clicked: true, mainFrame: true) == .drop)
    }

    @Test func anHttpsLinkTheyClickOpensInTheBrowser() {
        let link = URL(string: "https://ollama.com/settings/keys")!
        #expect(LinkPolicy.decide(link, clicked: true, mainFrame: true) == .openInBrowser(link))
        #expect(LinkPolicy.decide(link, clicked: false, mainFrame: true) == .drop)  // a script's, never followed
    }

    @Test(arguments: [
        "http://example.com/", "file:///etc/passwd", "javascript:alert(1)", "data:text/html,hi", "mailto:a@example.com",
        "about:blank", "https:///no-host", "https://user:pass@example.com/", "estia-setup://other/page.html",
    ])
    func everyOtherAddressIsDropped(address: String) {
        #expect(LinkPolicy.decide(URL(string: address), clicked: true, mainFrame: true) == .drop)
    }

    @Test func noAddressIsDropped() {
        #expect(LinkPolicy.decide(nil, clicked: true, mainFrame: true) == .drop)
    }
}

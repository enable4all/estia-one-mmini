import CryptoKit
import Foundation
import Testing

@testable import SetupWindowCore

private let sha = String(repeating: "a", count: 64)

/// setup runs `estia-setup --page <folder> --manifest <sha256 of page.json> [--terminal-app <bundle id>] [--check]`:
/// anything else is refused as a whole, never guessed.
@Suite struct ArgumentReading {
    @Test func setupsOwnCallIsRead() {
        #expect(
            Arguments.parse(["--page", "/tmp/page", "--manifest", sha])
                == Arguments(page: "/tmp/page", manifest: sha, terminalApp: nil, check: false))
        #expect(
            Arguments.parse(["--manifest", sha, "--page", "/tmp/page", "--terminal-app", "com.apple.Terminal", "--check"])
                == Arguments(page: "/tmp/page", manifest: sha, terminalApp: "com.apple.Terminal", check: true))
    }

    @Test(arguments: [
        [String](),
        ["--page", "/tmp/page"],
        ["--manifest", sha],
        ["--page", "page", "--manifest", sha],
        ["--page", "/tmp/page", "--manifest", "abc"],
        ["--page", "/tmp/page", "--manifest", sha, "--terminal-app", "a b"],
        ["--page", "/tmp/page", "--manifest", sha, "--page", "/tmp/other"],
        ["--page", "/tmp/page", "--manifest", sha, "--url", "https://example.com"],
        ["--page", "/tmp/page", "--manifest", sha, "extra"],
    ])
    func anythingElseIsRefused(arguments: [String]) {
        #expect(Arguments.parse(arguments) == nil)
    }
}

/// What the page asks of the window itself: the folder picker, Copy, Terminal, Close. Checked like setup's messages.
@Suite struct NativeRequests {
    @Test func eachRequestIsRead() throws {
        #expect(try NativeRequest(body: ["t": "pick_folder"]) == .pickFolder)
        #expect(try NativeRequest(body: ["t": "copy", "text": "orange-river-42"]) == .copy("orange-river-42"))
        #expect(try NativeRequest(body: ["t": "terminal"]) == .terminal)
        #expect(try NativeRequest(body: ["t": "close"]) == .close)
    }

    @Test func anythingElseIsRefused() {
        let bodies: [Any] = [
            "pick_folder", ["t": "open", "url": "https://example.com"], ["t": "copy"], ["t": "copy", "text": 7],
            ["t": "copy", "text": String(repeating: "x", count: NativeRequest.maxCopy + 1)],
            ["t": "terminal", "app": "com.example.evil"], ["t": "pick_folder", "path": "/"],
        ]
        for body in bodies {
            #expect(throws: MessageError.self) { try NativeRequest(body: body) }
        }
    }
}

/// setup's lines as they arrive on stdin: split at each newline, a line over its limit refused, the rest kept.
@Suite struct LineSplitting {
    @Test func linesComeOutWholeWhateverTheChunks() throws {
        var lines = Lines(limit: 64)
        #expect(try lines.add(Data("{\"t\":\"hel".utf8)).isEmpty)
        #expect(try lines.add(Data("lo\"}\n{\"t\":\"progress\"".utf8)) == [Data("{\"t\":\"hello\"}".utf8)])
        #expect(try lines.add(Data(",\"line\":\"a\"}\n\n".utf8)) == [Data("{\"t\":\"progress\",\"line\":\"a\"}".utf8)])
        #expect(lines.pending.isEmpty)
    }

    @Test func aLineOverItsLimitIsRefusedBeforeItsEnd() throws {
        var lines = Lines(limit: 8)
        #expect(throws: MessageError.tooLong) { try lines.add(Data("123456789".utf8)) }
        var exact = Lines(limit: 8)
        #expect(try exact.add(Data("12345678\n".utf8)) == [Data("12345678".utf8)])
    }
}

/// The built program, run as setup runs it with --check: page.json and every page file checked, no window.
@Suite(.serialized) struct ProgramCheck {
    private func binary() throws -> URL {
        var info = Dl_info()
        try #require(dladdr(#dsohandle, &info) != 0 && info.dli_fname != nil)
        var folder = URL(fileURLWithPath: String(cString: info.dli_fname)).deletingLastPathComponent()
        while folder.path != "/" {
            let candidate = folder.appendingPathComponent("estia-setup")
            if FileManager.default.isExecutableFile(atPath: candidate.path) { return candidate }
            folder = folder.deletingLastPathComponent()
        }
        throw NotBuilt()
    }

    private struct NotBuilt: Error {}

    private func run(_ arguments: [String]) throws -> (Int32, Data) {
        let process = Process()
        process.executableURL = try binary()
        process.arguments = arguments
        process.environment = [:]
        let out = Pipe()
        process.standardOutput = out
        process.standardError = FileHandle.nullDevice
        process.standardInput = FileHandle.nullDevice
        try process.run()
        let data = out.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return (process.terminationStatus, data)
    }

    private func page() throws -> (URL, String) {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("page-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let html = Data("<!doctype html><title>Estia Setup</title>".utf8)
        try html.write(to: folder.appendingPathComponent("page.html"))
        let files: [[String: String]] = [
            ["path": "page.html", "sha256": Manifest.fingerprint(html), "type": "text/html; charset=utf-8"]
        ]
        let manifest = try JSONSerialization.data(withJSONObject: ["v": 1, "files": files])
        try manifest.write(to: folder.appendingPathComponent("page.json"))
        return (folder, Manifest.fingerprint(manifest))
    }

    @Test func aPageSetupBuiltChecksOut() throws {
        let (folder, manifest) = try page()
        defer { try? FileManager.default.removeItem(at: folder) }
        let (code, out) = try run(["--page", folder.path, "--manifest", manifest, "--check"])
        #expect(code == 0 && out.isEmpty)
    }

    @Test func aChangedFileOrAnotherManifestIsRefused() throws {
        let (folder, manifest) = try page()
        defer { try? FileManager.default.removeItem(at: folder) }
        #expect(try run(["--page", folder.path, "--manifest", sha, "--check"]).0 == 1)
        try Data("<!doctype html><script src=x></script>".utf8).write(to: folder.appendingPathComponent("page.html"))
        let (code, out) = try run(["--page", folder.path, "--manifest", manifest, "--check"])
        #expect(code == 1 && out.isEmpty)  // nothing on stdout: setup reads only protocol lines there
    }

    @Test func otherArgumentsAreRefused() throws {
        #expect(try run(["--page", "/tmp"]).0 == 2)
    }
}

import Darwin
import Foundation
import Testing

@testable import SetupWindowCore

/// The wrapper as the person, against a folder of the test's own: the same text root runs, never as root (no test
/// ever asks for admin rights).
final class ShellElevator: Elevator, @unchecked Sendable {
    let base: String
    private(set) var prompts: [String] = []
    private(set) var words: [[String]] = []

    init(base: String) { self.base = base }

    func elevate(words: [String], prompt: String, done: @escaping (Elevated) -> Void) {
        prompts.append(prompt)
        self.words.append(words)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", AdminCore.wrapper(base: base), "estia-admin"] + words
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            done(.failed(127))
            return
        }
        process.waitUntilExit()
        done(process.terminationStatus == 0 ? .started : .failed(Int(process.terminationStatus)))
    }
}

/// macOS's box, as a test gives its answer: the person cancels it (or types a wrong password three times).
final class RefusingElevator: Elevator {
    func elevate(words: [String], prompt: String, done: @escaping (Elevated) -> Void) { done(.refused) }
}

/// macOS's box that cannot ask at all (a script that does not compile, an AppleScript error of another number).
final class FailingElevator: Elevator {
    func elevate(words: [String], prompt: String, done: @escaping (Elevated) -> Void) {
        done(AdminCore.elevated(errorNumber: -2753))
    }
}

/// What the runner says, recorded in turn: each line and the result.
final class Said: AdminSink, @unchecked Sendable {
    private let lock = NSLock()
    private var said: [String] = []

    func adminLine(id: String, line: String) { record("line \(id) \(line)") }

    func adminDone(id: String, code: Int, refused: Bool, unrun: Bool) {
        record("done \(id) \(code) \(refused)" + (unrun ? " unrun" : ""))
    }

    private func record(_ text: String) {
        lock.lock()
        said.append(text)
        lock.unlock()
    }

    var all: [String] {
        lock.lock()
        defer { lock.unlock() }
        return said
    }

    /// Everything said once the result came (at most `seconds` later).
    func untilDone(_ seconds: Double = 15) async -> [String] {
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            if all.contains(where: { $0.hasPrefix("done ") }) { return all }
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
        return all
    }
}

/// A fresh folder of the test's own, standing in for /private/var/run: no ".." and no quote in its path.
func runBase() throws -> String {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent("estia-admin-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    return folder.resolvingSymlinksInPath().path
}

func runner(_ elevator: Elevator, base: String, said: Said) -> AdminRunner {
    let runner = AdminRunner(
        elevator: elevator, base: base, folderOwner: getuid(), callbacks: DispatchQueue(label: "admin-test"))
    runner.sink = said
    return runner
}

func request(
    _ argv: [String], id: String = "a1", input: String? = nil, open: Bool = false, why: String = "Why.",
    seconds: Int? = nil
) -> AdminRequest {
    AdminRequest(id: id, argv: argv, input: input, open: open, why: why, seconds: seconds)
}

/// The fixed texts root runs: never built from data. Golden: a change here is a change to what runs as root.
@Suite struct AdminTexts {
    @Test func theWrapperIsTheFixedOne() {
        #expect(
            AdminCore.wrapper(base: "/private/var/run") == """
                set -u
                case "$1" in ''|*[!0-9a-f]*) exit 120 ;; esac
                case "$2" in ''|*[!0-9]*) exit 120 ;; esac
                case "$3" in pipe|none) ;; *) exit 120 ;; esac
                case "$4" in ''|*[!0-9]*) exit 120 ;; esac
                run="/private/var/run/estia-setup.$1"; owner="$2"; input="$3"; limit="$4"; shift 4
                umask 077
                /bin/mkdir -m 0711 "$run" || exit 120
                : >"$run/out" && /usr/sbin/chown "$owner" "$run/out" || exit 120
                : >"$run/code" && /usr/sbin/chown "$owner" "$run/code" || exit 120
                if [ "$input" = pipe ]; then /usr/bin/mkfifo -m 0600 "$run/in" && /usr/sbin/chown "$owner" "$run/in" || exit 120; fi
                cd / || exit 120
                (
                umask 022
                if [ "$input" = pipe ]; then "$@" <"$run/in" >>"$run/out" 2>&1 & else "$@" </dev/null >>"$run/out" 2>&1 & fi
                pid=$!
                watch=
                if [ "$limit" -gt 0 ]; then ( /bin/sleep "$limit" && /bin/kill -TERM "$pid" ) </dev/null >/dev/null 2>&1 & watch=$!; fi
                wait "$pid"
                code=$?
                if [ -n "$watch" ]; then /bin/kill "$watch" 2>/dev/null; fi
                printf '%s\\n' "$code" >"$run/code"
                n=0
                while [ -s "$run/code" ] && [ "$n" -lt 600 ]; do /bin/sleep 1; n=$((n + 1)); done
                /bin/rm -rf "$run"
                ) </dev/null >/dev/null 2>&1 &
                exit 0
                """)
    }

    @Test func theAppleScriptIsTheFixedOneAroundTheWrapper() {
        let script = AdminCore.appleScript(base: "/private/var/run")
        let literal = AdminCore.wrapper(base: "/private/var/run")
            .replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "\\n")
        #expect(
            script == """
                on commandFor(theWords)
                \tset theCommand to "/bin/sh -c " & quoted form of "\(literal)" & " estia-admin"
                \trepeat with aWord in theWords
                \t\tset theCommand to theCommand & " " & quoted form of (aWord as text)
                \tend repeat
                \treturn theCommand
                end commandFor

                on runAsAdmin(theWords, thePrompt)
                \tdo shell script commandFor(theWords) with prompt thePrompt with administrator privileges
                \treturn "started"
                end runAsAdmin
                """)
    }

    /// Root's environment is the app's own, fixed: who asked (SUDO_USER, as sudo sets it, so the root-run tools find
    /// the person's account as they do under sudo), never setup's.
    @Test func rootsEnvironmentIsFixed() {
        #expect(
            AdminCore.environment(user: "alex", uid: 501, gid: 20) == [
                "/usr/bin/env", "-i", "PATH=/usr/bin:/bin:/usr/sbin:/sbin", "HOME=/var/root", "USER=root",
                "LOGNAME=root", "SHELL=/bin/sh", "LANG=en_US.UTF-8", "SUDO_USER=alex", "SUDO_UID=501", "SUDO_GID=20",
            ])
        let words = AdminCore.words(
            token: "ab", uid: 501, input: true, seconds: 180, environment: ["/usr/bin/env", "-i"],
            argv: ["/bin/echo", "x"])
        #expect(words == ["ab", "501", "pipe", "180", "/usr/bin/env", "-i", "/bin/echo", "x"])
        let none = AdminCore.words(token: "ab", uid: 501, input: false, seconds: nil, environment: [], argv: ["/bin/echo"])
        #expect(none[2] == "none" && none[3] == "0")  // no time limit
    }

    @Test func eachTokenIsFreshHex() {
        let one = AdminCore.token(), two = AdminCore.token()
        #expect(one.count == 32 && one != two && one.allSatisfy { "0123456789abcdef".contains($0) })
    }

    /// macOS's box: Cancel (-128) and an authorization refused (wrong passwords, -60005 and its kin) are "not given";
    /// the wrapper's own failure is 120, a command that never started 127.
    @Test func whatTheBoxAndTheWrapperSayIsRead() {
        #expect(AdminCore.elevated(errorNumber: -128) == .refused)
        #expect(AdminCore.elevated(errorNumber: -60005) == .refused)
        #expect(AdminCore.elevated(errorNumber: -60006) == .refused)
        #expect(AdminCore.elevated(errorNumber: 120) == .failed(120))
        #expect(AdminCore.elevated(errorNumber: 3) == .failed(3))
        #expect(AdminCore.elevated(errorNumber: -2753) == .failed(127))
    }

    /// Only an absolute program, no NUL or line break in any word, at most 128 words and 128 KiB, a plain reason.
    @Test func aRequestOutsideItsRulesIsRefused() {
        #expect(AdminCore.problem(request(["/usr/local/estia/bin/estia", "restart"])) == nil)
        #expect(AdminCore.problem(request(["/bin/echo"], seconds: 180)) == nil)
        #expect(AdminCore.problem(request(["/bin/echo", "it's $(x) `y` \"z\" ; -n é"])) == nil)
        for bad in [
            request([]), request(["estia"]), request(["-c"]), request(["/bin/echo", "a\nb"]),
            request(["/bin/echo", "a\rb"]), request(["/bin/echo", "a\u{0}b"]),
            request(["/bin/echo"] + Array(repeating: "x", count: 128)),
            request(["/bin/echo", String(repeating: "x", count: 128 * 1024)]), request(["/bin/echo"], id: "A1"),
            request(["/bin/echo"], id: ""), request(["/bin/echo"], why: String(repeating: "x", count: 201)),
            request(["/bin/echo"], why: "a\nb"), request(["/bin/echo"], seconds: 0),
            request(["/bin/echo"], seconds: AdminCore.maxSeconds + 1),
        ] {
            #expect(AdminCore.problem(bad) != nil, "\(bad)")
        }
    }
}

/// A command's output as setup reads it: whole lines; a line rewritten in place (\r) as its last state; a line too
/// long for one message cut into lines that fit; bytes that are not UTF-8 or a NUL never stop it.
@Suite struct AdminOutput {
    @Test func linesComeOutWholeWhateverTheChunks() {
        var lines = OutputLines()
        #expect(lines.add(Data("one\ntw".utf8)) == ["one"])
        #expect(lines.add(Data("o\n\nthree".utf8)) == ["two", ""])
        #expect(lines.finish() == ["three"])
        #expect(lines.finish().isEmpty)
    }

    @Test func aLineRewrittenInPlaceKeepsItsLastState() {
        var lines = OutputLines()
        #expect(lines.add(Data("10%\r20%\r30%\ndone\r\n".utf8)) == ["30%", "done"])
    }

    @Test func aLongLineIsCutIntoLinesThatFit() throws {
        var lines = OutputLines()
        let long = String(repeating: "é", count: OutputLines.maxCharacters * 2 + 5)
        let out = lines.add(Data((long + "\n").utf8))
        #expect(out.count == 3 && out.joined() == long)
        for line in out {
            #expect(try AdminMessage.line(id: "a1", line: line).line().count <= SetupProtocol.fromWindowMax + 1)
        }
    }

    @Test func otherBytesAndANulNeverStopIt() {
        var lines = OutputLines()
        #expect(lines.add(Data([0x61, 0xFF, 0x00, 0x62, 0x0A])) == ["a\u{FFFD}\u{FFFD}b"])
    }
}

/// The runner, with the wrapper run as the person against a folder of the test's own: what root's command says comes
/// to setup line by line, in order, then its result; input goes on its stdin only; the run folder is gone after.
@Suite(.serialized) struct AdminRunning {
    @Test func outputComesInOrderThenTheResult() async throws {
        let base = try runBase()
        let said = Said()
        let elevator = ShellElevator(base: base)
        let admin = runner(elevator, base: base, said: said)
        admin.start(request(["/bin/sh", "-c", "echo one; echo two >&2; echo three; exit 3"], why: "Admin rights."))
        #expect(await said.untilDone() == ["line a1 one", "line a1 two", "line a1 three", "done a1 3 false"])
        #expect(elevator.prompts == ["Admin rights."])
        let words = try #require(elevator.words.first)
        #expect(words[1] == String(getuid()) && words[2] == "none" && words.suffix(3) == ["/bin/sh", "-c", words.last!])
        #expect(words[3] == "0" && words[4...6] == ["/usr/bin/env", "-i", "PATH=/usr/bin:/bin:/usr/sbin:/sbin"])
        #expect(!admin.running)
        try await gone(base)
        await cleanUp(base)
    }

    @Test func rootGetsTheAppsOwnEnvironment() async throws {
        let base = try runBase()
        let said = Said()
        let admin = runner(ShellElevator(base: base), base: base, said: said)
        admin.start(request(["/usr/bin/env"]))
        let lines = await said.untilDone().filter { $0.hasPrefix("line ") }.map { String($0.dropFirst("line a1 ".count)) }
        let me = String(cString: getpwuid(getuid())!.pointee.pw_name)
        #expect(
            Set(lines) == [
                "PATH=/usr/bin:/bin:/usr/sbin:/sbin", "HOME=/var/root", "USER=root", "LOGNAME=root", "SHELL=/bin/sh",
                "LANG=en_US.UTF-8", "SUDO_USER=\(me)", "SUDO_UID=\(getuid())", "SUDO_GID=\(getgid())",
            ])
        await cleanUp(base)
    }

    @Test func oneShotInputGoesOnStdinThenItsEnd() async throws {
        let base = try runBase()
        let said = Said()
        let elevator = ShellElevator(base: base)
        let admin = runner(elevator, base: base, said: said)
        admin.start(request(["/bin/cat"], input: "the key\nsecond\n"))
        #expect(await said.untilDone() == ["line a1 the key", "line a1 second", "done a1 0 false"])
        #expect(!(elevator.words.first ?? []).contains { $0.contains("the key") })  // never in argv
        #expect(elevator.words.first?[2] == "pipe")
        await cleanUp(base)
    }

    @Test func inputCanComeAnswerByAnswerUntilItsEnd() async throws {
        let base = try runBase()
        let said = Said()
        let admin = runner(ShellElevator(base: base), base: base, said: said)
        let script = "read a; echo \"asked: $a\"; read b; echo \"then: $b\"; cat; echo end"
        admin.start(request(["/bin/sh", "-c", script], input: "", open: true))
        admin.input(id: "a1", text: "y\n")
        try await until { said.all.contains("line a1 asked: y") }
        admin.input(id: "a1", text: "n\n")
        admin.input(id: "a2", text: "not this one's\n")
        admin.end(id: "a1")
        #expect(await said.untilDone() == ["line a1 asked: y", "line a1 then: n", "line a1 end", "done a1 0 false"])
        await cleanUp(base)
    }

    @Test func anOpenInputThatEndsAtOnceIsTheEndOfInput() async throws {
        let base = try runBase()
        let said = Said()
        let admin = runner(ShellElevator(base: base), base: base, said: said)
        admin.start(request(["/bin/cat"], input: nil, open: true))
        admin.end(id: "a1")
        #expect(await said.untilDone() == ["done a1 0 false"])
        await cleanUp(base)
    }

    @Test func theBoxRefusedIsNotGivenAndNothingRan() async throws {
        let base = try runBase()
        let said = Said()
        let admin = runner(RefusingElevator(), base: base, said: said)
        admin.start(request(["/usr/bin/touch", base + "/ran"]))
        #expect(await said.untilDone() == ["done a1 0 true"])
        #expect(!FileManager.default.fileExists(atPath: base + "/ran") && !admin.running)
        await cleanUp(base)
    }

    /// macOS's box that cannot ask (an AppleScript error of another number, a script that does not compile): nothing
    /// ran, and setup is told so (`unrun`), never a command's own 127 (review ADM-4).
    @Test func aBoxThatCannotAskRunsNothingAndSaysSo() async throws {
        let base = try runBase()
        let said = Said()
        let admin = runner(FailingElevator(), base: base, said: said)
        admin.start(request(["/usr/bin/touch", base + "/ran"]))
        #expect(await said.untilDone() == ["done a1 127 false unrun"])
        #expect(!FileManager.default.fileExists(atPath: base + "/ran") && !admin.running)
        await cleanUp(base)
    }

    /// A command over its time limit (setup's, as Terminal's Host.run has it) is ended with TERM, its result 143:
    /// nothing waits on it without end (review ADM-3).
    @Test func aCommandOverItsTimeLimitIsEnded() async throws {
        let base = try runBase()
        let said = Said()
        let admin = runner(ShellElevator(base: base), base: base, said: said)
        let began = Date()
        admin.start(request(["/bin/sleep", "30"], seconds: 1))
        #expect(await said.untilDone() == ["done a1 143 false"])
        #expect(Date().timeIntervalSince(began) < 10 && !admin.running)
        await cleanUp(base)
    }

    /// A piped command whose input the app never opened (the window died after the box, review W08) waits for a
    /// writer only until its time limit: TERM, its result 143, and its folder goes once that result is taken. Run
    /// as the wrapper runs, without the runner (which would open `in` itself).
    @Test func aPipedCommandNeverFedEndsAtItsTimeLimit() async throws {
        let base = try runBase()
        let token = AdminCore.token()
        let words = AdminCore.words(
            token: token, uid: getuid(), input: true, seconds: 1, environment: [], argv: ["/bin/cat"])
        let shell = Process()
        shell.executableURL = URL(fileURLWithPath: "/bin/sh")
        shell.arguments = ["-c", AdminCore.wrapper(base: base), "estia-admin"] + words
        shell.standardInput = FileHandle.nullDevice
        try shell.run()
        shell.waitUntilExit()
        #expect(shell.terminationStatus == 0)
        let run = "\(base)/estia-setup.\(token)"
        #expect(FileManager.default.fileExists(atPath: "\(run)/in"))  // its input never opened by anyone
        var code = ""
        try await until(10) {
            code = (try? String(contentsOfFile: "\(run)/code", encoding: .utf8)) ?? ""
            return !code.isEmpty
        }
        #expect(code == "143\n")
        _ = truncate("\(run)/code", 0)  // the result taken: root removes the folder
        try await gone(base)
        await cleanUp(base)
    }

    /// Root's command gets sudo's umask (022), not the wrapper's own 077 (review ADM-8).
    @Test func rootsCommandHasSudosUmask() async throws {
        let base = try runBase()
        let said = Said()
        let admin = runner(ShellElevator(base: base), base: base, said: said)
        admin.start(request(["/bin/sh", "-c", "umask"]))
        #expect(await said.untilDone() == ["line a1 0022", "done a1 0 false"])
        await cleanUp(base)
    }

    @Test func aRequestOutsideItsRulesNeverReachesTheBox() async throws {
        let base = try runBase()
        let said = Said()
        let elevator = ShellElevator(base: base)
        let admin = runner(elevator, base: base, said: said)
        admin.start(request(["estia", "restart"]))
        #expect(await said.untilDone() == ["done a1 127 false unrun"] && elevator.prompts.isEmpty)
        await cleanUp(base)
    }

    /// One at a time: a second command while one runs is not run (setup never sends one: it waits for each result).
    @Test func oneCommandAtATime() async throws {
        let base = try runBase()
        let said = Said()
        let admin = runner(ShellElevator(base: base), base: base, said: said)
        admin.start(request(["/bin/cat"], input: "", open: true))
        try await until { admin.running }
        admin.start(request(["/bin/echo", "two"], id: "a2"))
        try await until { said.all.contains("done a2 127 false unrun") }
        #expect(admin.running)  // the window refuses to close meanwhile
        admin.end(id: "a1")
        #expect(await said.untilDone().last == "done a2 127 false unrun")
        try await until { said.all.contains("done a1 0 false") }
        #expect(!admin.running)
        await cleanUp(base)
    }

    /// A run folder that is not root's own (here: another mode) is never read: the command's result is the
    /// wrapper's failure, and nothing it may hold reaches setup.
    @Test func aRunFolderOfAnotherShapeIsNeverRead() async throws {
        let base = try runBase()
        let said = Said()
        let admin = AdminRunner(
            elevator: ShellElevator(base: base), base: base, folderOwner: getuid() + 1,
            callbacks: DispatchQueue(label: "admin-test"))
        admin.sink = said
        admin.start(request(["/bin/echo", "secret output"]))
        #expect(await said.untilDone(20) == ["done a1 120 false"])
        await cleanUp(base)
    }
}

/// The AppleScript itself, compiled as the app compiles it: each word comes out of its quoting exactly as it went in,
/// whatever it holds, so no word is ever read as shell text. Only `commandFor` is called: never `do shell script`,
/// never admin rights.
@Suite(.serialized) struct AdminQuoting {
    @MainActor @Test func eachWordReachesTheCommandAsItWasGiven() async throws {
        let base = try runBase()
        let script = try #require(NSAppleScript(source: AdminCore.appleScript(base: base)))
        var error: NSDictionary?
        let compiled = script.compileAndReturnError(&error)
        #expect(compiled, "\(String(describing: error))")
        let token = AdminCore.token()
        let given = [
            "it's", "\"double\"", "$(touch \(base)/pwned)", "`touch \(base)/pwned`", "two words", "-n", "--", "é ∆ 日本",
            "a;b|c&d", "$HOME", "\\", "*", "",
        ]
        let words = AdminCore.words(
            token: token, uid: getuid(), input: false, seconds: nil, environment: [],
            argv: ["/usr/bin/printf", "[%s]\\n"] + given)
        let list = NSAppleEventDescriptor.list()
        for (index, word) in words.enumerated() { list.insert(NSAppleEventDescriptor(string: word), at: index + 1) }
        let parameters = NSAppleEventDescriptor.list()
        parameters.insert(list, at: 1)
        let event = NSAppleEventDescriptor.appleEvent(
            withEventClass: fourCharacters("ascr"), eventID: fourCharacters("psbr"),
            targetDescriptor: NSAppleEventDescriptor.currentProcess(), returnID: AEReturnID(-1), transactionID: 0)
        event.setDescriptor(NSAppleEventDescriptor(string: "commandfor"), forKeyword: fourCharacters("snam"))
        event.setDescriptor(parameters, forKeyword: fourCharacters("----"))
        let answer = script.executeAppleEvent(event, error: &error)
        let command = try #require(answer.stringValue, "\(String(describing: error))")
        let shell = Process()
        shell.executableURL = URL(fileURLWithPath: "/bin/sh")
        shell.arguments = ["-c", command]
        try shell.run()
        shell.waitUntilExit()
        #expect(shell.terminationStatus == 0)
        let out = "\(base)/estia-setup.\(token)/out"
        var text = ""
        for _ in 0..<200 where !text.hasSuffix("[]\n") {
            text = (try? String(contentsOfFile: out, encoding: .utf8)) ?? text
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        #expect(text == given.map { "[\($0)]\n" }.joined())
        #expect(!FileManager.default.fileExists(atPath: "\(base)/pwned"))
        _ = truncate("\(base)/estia-setup.\(token)/code", 0)  // the folder goes now (the app's own ack)
        await cleanUp(base)
    }
}

func fourCharacters(_ text: String) -> UInt32 {
    text.utf8.reduce(0) { $0 << 8 | UInt32($1) }
}

func until(_ seconds: Double = 10, _ condition: () -> Bool) async throws {
    let deadline = Date().addingTimeInterval(seconds)
    while !condition() {
        guard Date() < deadline else {
            Issue.record("not within \(seconds) s")
            return
        }
        try await Task.sleep(nanoseconds: 20_000_000)
    }
}

/// The run folder is gone soon after the result came: root removes it once the app has read the result.
func gone(_ base: String) async throws {
    try await until(5) {
        ((try? FileManager.default.contentsOfDirectory(atPath: base)) ?? []).allSatisfy { !$0.hasPrefix("estia-setup.") }
    }
}

/// A test's run folders let go (each one's result acknowledged, as the app does, so the wrapper ends) and its folder
/// removed: nothing outlives the test.
func cleanUp(_ base: String) async {
    for name in (try? FileManager.default.contentsOfDirectory(atPath: base)) ?? [] where name.hasPrefix("estia-setup.") {
        _ = truncate("\(base)/\(name)/code", 0)
    }
    try? await gone(base)
    try? FileManager.default.removeItem(atPath: base)
}

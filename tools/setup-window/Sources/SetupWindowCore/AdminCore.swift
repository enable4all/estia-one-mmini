import Darwin
import Foundation

/// One command setup asks the window to run as root (protocol 2's `admin`): its words as a list, never shell text;
/// `input` goes on its stdin (with `open`, stdin stays open for `admin_input` until `admin_end`); `why` is the line
/// macOS's own box shows; `seconds`, when setup gives one, its time limit (TERM once it is over, as Terminal's
/// Host.run ends its command: review ADM-3).
public struct AdminRequest: Equatable, Sendable {
    public let id: String
    public let argv: [String]
    public let input: String?
    public let open: Bool
    public let why: String
    public let seconds: Int?

    public init(id: String, argv: [String], input: String?, open: Bool, why: String, seconds: Int? = nil) {
        self.id = id
        self.argv = argv
        self.input = input
        self.open = open
        self.why = why
        self.seconds = seconds
    }
}

/// How root's run ended, as macOS's box and the wrapper say it: started (the command runs; its result comes from the
/// run folder), refused (Cancel in the box, or a wrong password three times: nothing ran), or failed (the wrapper's
/// own failure, 120, or no box at all, 127: the command never started either way).
public enum Elevated: Equatable, Sendable {
    case started
    case refused
    case failed(Int)
}

/// Admin rights in the window, without a window: the fixed texts root runs, the rules a request keeps, root's fixed
/// environment and how macOS's answers read. One mechanism (owner, 29 Oct: "Terminal in the window"): AppleScript's
/// `do shell script … with administrator privileges`, run inside the app (AppleScriptElevator), so macOS shows its own
/// box, "Estia Setup wants to make changes.", and the password goes only to macOS. The script is one fixed text whose
/// handler takes the words as a list and quotes each one (`quoted form of`): no word is ever shell or AppleScript text.
/// The wrapper, as root, makes a fresh folder of its own (0711) with the command's output (`out`), its result (`code`)
/// and, for input, a FIFO (`in`), each 0600 and the person's; it starts the command in the background and returns, so
/// the box is the only wait; once the app has read the result it truncates `code`, and root removes the folder.
public enum AdminCore {
    /// Where root makes each run's folder: root's own, writable by no other account.
    public static let base = "/private/var/run"
    public static let maxWords = 128
    public static let maxBytes = 128 * 1024
    public static let maxWhy = 200
    /// The wrapper's own failure (a folder that could not be made, words of another shape).
    public static let wrapperFailed = 120
    /// Not run: a request outside the rules, one while another runs, or no box at all.
    public static let notRun = 127
    /// The run's result read from its folder within this long after the box, or it counts as the wrapper's failure.
    public static let folderSeconds = 10.0
    /// A command's time limit at most (a day): setup's own are minutes.
    public static let maxSeconds = 86_400

    /// Why this request may not run as root; nil when it may.
    public static func problem(_ request: AdminRequest) -> String? {
        guard request.id.range(of: "^[a-z0-9]{1,16}$", options: .regularExpression) != nil else { return "its id" }
        if let seconds = request.seconds, !(1...maxSeconds).contains(seconds) {
            return "a time limit of 1 to \(maxSeconds) seconds"
        }
        return argvProblem(request.argv) ?? whyProblem(request.why)
    }

    /// An absolute program first, no NUL or line break in any word, at most maxWords words and maxBytes in all.
    public static func argvProblem(_ argv: [String]) -> String? {
        guard let program = argv.first, program.hasPrefix("/") else { return "a program by its absolute path" }
        guard argv.count <= maxWords else { return "at most \(maxWords) words" }
        guard argv.reduce(0, { $0 + $1.utf8.count }) <= maxBytes else { return "at most \(maxBytes) bytes" }
        let breaks: Set<Unicode.Scalar> = ["\0", "\n", "\r"]
        guard argv.allSatisfy({ !$0.unicodeScalars.contains(where: breaks.contains) }) else {
            return "no NUL or line break in a word"
        }
        return nil
    }

    /// The box's line: plain text on one line, at most maxWhy characters.
    public static func whyProblem(_ why: String) -> String? {
        guard why.count <= maxWhy else { return "a reason of at most \(maxWhy) characters" }
        guard !why.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F }) else {
            return "a reason on one line"
        }
        return nil
    }

    /// The wrapper root runs. Its only data are its positional parameters: the run's token (hex), the person's uid
    /// (digits), the input mode (pipe or none), the time limit in seconds (digits, 0 for none), then the words of the
    /// command. `base` is the app's own (a test's folder in the tests): letters, digits and / . _ - only. The command
    /// runs with sudo's umask (022: the wrapper's own 077 is for its run folder alone, review ADM-8); over its time
    /// limit it gets TERM (its result then 143); the watch ends with it, so it never signals another process.
    public static func wrapper(base: String) -> String {
        precondition(base.range(of: "^/[A-Za-z0-9/._-]+$", options: .regularExpression) != nil, "a plain folder path")
        return """
            set -u
            case "$1" in ''|*[!0-9a-f]*) exit 120 ;; esac
            case "$2" in ''|*[!0-9]*) exit 120 ;; esac
            case "$3" in pipe|none) ;; *) exit 120 ;; esac
            case "$4" in ''|*[!0-9]*) exit 120 ;; esac
            run="\(base)/estia-setup.$1"; owner="$2"; input="$3"; limit="$4"; shift 4
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
            """
    }

    /// The one AppleScript the app compiles: `runAsAdmin(words, prompt)` runs the wrapper through `do shell script …
    /// with administrator privileges`, each word through `quoted form of`; `commandFor` alone (the tests call it)
    /// only builds the command. The text never changes, so macOS's 5 minutes without asking again hold for it.
    public static func appleScript(base: String) -> String {
        let literal = wrapper(base: base)
            .replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "\\n")
        return """
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
            """
    }

    /// Root's environment: fixed, the app's own, never setup's. SUDO_USER, SUDO_UID and SUDO_GID name who asked, as
    /// sudo sets them: the root-run tools (her move, the deploy, her doctor) find the person's account by them.
    public static func environment(user: String, uid: uid_t, gid: gid_t) -> [String] {
        [
            "/usr/bin/env", "-i", "PATH=/usr/bin:/bin:/usr/sbin:/sbin", "HOME=/var/root", "USER=root", "LOGNAME=root",
            "SHELL=/bin/sh", "LANG=en_US.UTF-8", "SUDO_USER=\(user)", "SUDO_UID=\(uid)", "SUDO_GID=\(gid)",
        ]
    }

    /// The wrapper's words: the app's own four (its token, the person's uid, the input mode, the time limit, 0 for
    /// none), root's environment, then the command.
    public static func words(
        token: String, uid: uid_t, input: Bool, seconds: Int?, environment: [String], argv: [String]
    ) -> [String] {
        [token, String(uid), input ? "pipe" : "none", String(seconds ?? 0)] + environment + argv
    }

    /// 16 random bytes as hex: the run folder's name, fresh for each command.
    public static func token() -> String {
        var generator = SystemRandomNumberGenerator()
        return (0..<16).map { _ in String(format: "%02x", UInt8.random(in: 0...255, using: &generator)) }.joined()
    }

    /// What an AppleScript error number means: Cancel (-128) and an authorization refused or not allowed (-60005,
    /// -60006, -60007) are admin rights not given; 1 to 255 is the wrapper's own exit; anything else never started.
    public static func elevated(errorNumber: Int) -> Elevated {
        switch errorNumber {
        case -128, -60005, -60006, -60007: return .refused
        case 1...255: return .failed(errorNumber)
        default: return .failed(notRun)
        }
    }
}

/// A command's output as lines for setup: split at each newline; a line rewritten in place (`\r`, a progress meter)
/// keeps its last state; a line longer than maxCharacters is cut into lines that fit one message; bytes that are not
/// UTF-8 and a NUL become U+FFFD (a NUL is never one of the protocol's texts).
public struct OutputLines: Sendable {
    public static let maxCharacters = AdminMessage.lineMax
    private var pending = Data()

    public init() {}

    public mutating func add(_ chunk: Data) -> [String] {
        pending.append(chunk)
        var lines: [String] = []
        while let end = pending.firstIndex(of: UInt8(ascii: "\n")) {
            lines += Self.cut(Data(pending[pending.startIndex..<end]))
            pending = Data(pending[pending.index(after: end)...])
        }
        if let last = pending.lastIndex(of: UInt8(ascii: "\r")), pending.count > Self.maxCharacters {
            pending = Data(pending[pending.index(after: last)...])  // a meter's states before the last: superseded
        }
        if pending.count > Self.maxCharacters * 4 {  // a line with no end yet: what came so far, in lines that fit
            let whole = Self.whole(pending)
            let text = Self.text(Data(pending.prefix(whole)))
            let keep = String(text.suffix(text.count % Self.maxCharacters))
            lines += Self.split(String(text.dropLast(keep.count)))
            pending = Data(keep.utf8) + Data(pending.dropFirst(whole))
        }
        return lines
    }

    /// The bytes of `data` up to its last whole UTF-8 character: a chunk may end inside one.
    private static func whole(_ data: Data) -> Int {
        let bytes = [UInt8](data.suffix(4))
        for back in 1...bytes.count {
            let byte = bytes[bytes.count - back]
            if byte & 0xC0 == 0x80 { continue }  // a continuation byte: its lead is further back
            let length = byte < 0x80 ? 1 : byte >= 0xF0 ? 4 : byte >= 0xE0 ? 3 : byte >= 0xC0 ? 2 : 1
            return length > back ? data.count - back : data.count
        }
        return data.count
    }

    /// The last line, without its newline (the command's end).
    public mutating func finish() -> [String] {
        guard !pending.isEmpty else { return [] }
        defer { pending = Data() }
        return Self.cut(pending)
    }

    private static func cut(_ line: Data) -> [String] {
        var text = Self.text(line)
        if text.hasSuffix("\r") { text.removeLast() }
        if let last = text.range(of: "\r", options: .backwards) { text = String(text[last.upperBound...]) }
        return split(text)
    }

    private static func split(_ text: String) -> [String] {
        guard text.count > maxCharacters else { return [text] }
        var parts: [String] = []
        var rest = Substring(text)
        while !rest.isEmpty {
            parts.append(String(rest.prefix(maxCharacters)))
            rest = rest.dropFirst(maxCharacters)
        }
        return parts
    }

    private static func text(_ bytes: Data) -> String {
        String(decoding: bytes, as: UTF8.self).replacingOccurrences(of: "\0", with: "\u{FFFD}")
    }
}

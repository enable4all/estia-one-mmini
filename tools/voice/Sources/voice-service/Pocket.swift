// Pocket TTS for her English voices (heart, jane, eve, vera, alba, michael, on the Mac's model) and jane in six more
// languages, on the CPU (VoiceCore's `PocketTTS` says why so). Its release program sits beside the voice service in her
// root-owned release, put there by estia-deploy (`voice-service --fetch-pocket`: the
// pinned GitHub release, its tarball's and both files' sha256 checked; the review of 24 Oct: a program in her own
// home, which any program of hers can change, broke "everything she runs is root's"): the service only checks it,
// never fetches or changes it. The model's files are fetched once, each at its commit (Hugging Face's repository
// without voice cloning: no account), into voice-models/pocket-tts (heart's, one a model, which ship with the voice
// service, copied there from its own bundle beside its program, private to her: never fetched; a shipped file that is
// not there or not the pinned one leaves only its own voice unsaid, as only an update puts it right, and so does one
// that fails its first run at load, until the next check), and checked file by file against their sha256
// with Whisper's rules (`prepareModel`: a changed or added file or a link is never run; that folder is set aside and
// fetched fresh once; changed again, refused). Every checked file is checked again whenever its size, times or inode
// change, each stamp taken from the bytes checked. Each line is one run of the program under sandbox-exec
// (`PocketRun`: no network, no other program, no signal to another, no macOS service, nothing written, her home closed
// but Pocket's folder), one thread, int8, from a config naming only those files. Whatever it cannot say (no program,
// not fetched yet, refused, a run that fails or is late) no other voice says (owner, 3 Nov 2026: Kokoro and
// Supertonic-3 removed; the voice service refuses the line, `PocketLine`); three failed runs in a row rest it until
// its next try (1, 5 and 30 minutes, then every 30; a language's model only once a line is asked in it after that
// wait), except for a call's lines, each of which runs it again (the review of 28 Oct, calls-03). Never text or audio
// in the log.
import Foundation
import VoiceCore

/// One run's outcome: its exit status (or the signal that ended it), what it wrote to stdout, and whether it was
/// stopped for taking too long or writing too much.
struct ProgramResult: Sendable, Equatable {
    var status: Int32 = 0
    var signalled = false
    var output = Data()
    var timedOut = false
    var tooLarge = false
}

/// Runs a program: argv (the program first), its whole environment, stdin, a time limit and an output cap.
typealias ProgramRun = @Sendable ([String], [String: String], Data, TimeInterval, Int) async throws -> ProgramResult

/// A flag set from another thread (the timer's).
private final class Flag: @unchecked Sendable {
    private let lock = NSLock()
    private var set = false
    var value: Bool { lock.withLock { set } }
    func raise() { lock.withLock { set = true } }
}

/// Runs `argv` with exactly `environment` (nothing of the service's own), `input` on stdin and stdout read to its end
/// (stderr to /dev/null: the program's log could name her text). Stopped with SIGKILL after `seconds`, or as soon as
/// stdout passes `maxOutput` bytes. Writing to a program that left early raises no SIGPIPE (`F_SETNOSIGPIPE`).
func runProgram(
    _ argv: [String], environment: [String: String], input: Data, seconds: TimeInterval, maxOutput: Int
) async throws -> ProgramResult {
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<ProgramResult, Error>) in
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: argv[0])
            process.arguments = Array(argv.dropFirst())
            process.environment = environment
            let stdin = Pipe()
            let stdout = Pipe()
            process.standardInput = stdin
            process.standardOutput = stdout
            process.standardError = FileHandle.nullDevice
            _ = fcntl(stdin.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1)
            do {
                try process.run()
            } catch {
                continuation.resume(throwing: error)
                return
            }
            let pid = process.processIdentifier
            let late = Flag()
            let timer = DispatchWorkItem {
                late.raise()
                kill(pid, SIGKILL)
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + seconds, execute: timer)
            try? stdin.fileHandleForWriting.write(contentsOf: input)
            try? stdin.fileHandleForWriting.close()
            var result = ProgramResult()
            let reader = stdout.fileHandleForReading
            while let piece = try? reader.read(upToCount: 1 << 16), !piece.isEmpty {
                result.output.append(piece)
                if result.output.count > maxOutput {
                    result.tooLarge = true
                    kill(pid, SIGKILL)
                    break
                }
            }
            process.waitUntilExit()
            timer.cancel()
            try? reader.close()
            result.status = process.terminationStatus
            result.signalled = process.terminationReason == .uncaughtSignal
            result.timedOut = late.value
            continuation.resume(returning: result)
        }
    }
}

/// The release program from its tarball: fetched over https (at most its cap) into a staging folder beside the
/// release's folder, checked against the tarball's sha256, its two files taken out with /usr/bin/tar, then moved into
/// place (a crash's staging folder is removed first; on any failure nothing is moved). `fetchPocket` then checks the
/// two files against their own sha256.
func downloadPocketRelease(
    _ release: PocketRelease, in base: URL,
    fetch: FileFetch = { try await fetchFile($0, to: $1, maxBytes: $2) },
    run: ProgramRun = { try await runProgram($0, environment: $1, input: $2, seconds: $3, maxOutput: $4) }
) async throws {
    let files = FileManager.default
    let folder = release.location(in: base)
    let parent = folder.deletingLastPathComponent()
    let prefix = "\(folder.lastPathComponent).download-"
    try files.createDirectory(at: parent, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o755])
    for name in (try? files.contentsOfDirectory(atPath: parent.path)) ?? [] where name.hasPrefix(prefix) {
        try files.removeItem(at: parent.appendingPathComponent(name))
    }
    let staging = parent.appendingPathComponent(prefix + UUID().uuidString)
    do {
        let unpacked = staging.appendingPathComponent("unpacked")
        try files.createDirectory(at: unpacked, withIntermediateDirectories: true)
        let tarball = staging.appendingPathComponent("release.tar.gz")
        try await fetch(release.url, tarball, release.maxTarballBytes)
        guard try sha256Hex(file: tarball) == release.tarballSHA256 else { throw PocketFailure.changed }
        let argv = ["/usr/bin/tar", "-x", "-z", "-f", tarball.path, "-C", unpacked.path] + release.members
        let untar = try await run(argv, ["PATH": "/usr/bin:/bin"], Data(), 60, 1 << 16)
        guard untar.status == 0, !untar.signalled, !untar.timedOut else { throw PocketFailure.exit(untar.status) }
        let kept = staging.appendingPathComponent("kept")
        try files.createDirectory(at: kept, withIntermediateDirectories: true)
        for name in release.sha256.keys.sorted() {
            try files.moveItem(at: unpacked.appendingPathComponent(release.top).appendingPathComponent(name),
                               to: kept.appendingPathComponent(name))
        }
        for name in release.sha256.keys {  // the program 0755, the rest 0644, their folder 0755 (a release's own modes)
            try files.setAttributes([.posixPermissions: name == release.program ? 0o755 : 0o644],
                                    ofItemAtPath: kept.appendingPathComponent(name).path)
        }
        try files.setAttributes([.posixPermissions: 0o755], ofItemAtPath: kept.path)
        if (try? files.attributesOfItem(atPath: folder.path)) != nil { try files.removeItem(at: folder) }
        try files.moveItem(at: kept, to: folder)
        try? files.removeItem(at: staging)
    } catch {
        try? files.removeItem(at: staging)
        throw error
    }
}

/// A voice file that ships with the voice service (`PocketVoice.bundled`: heart's), from its resources (`resources`:
/// `PocketTTS.resources`, beside its program, root's in her release) into its pin's folder under `base`, as a download
/// puts a fetched one there: into a staging folder beside it first, then moved into place, so a load never meets half
/// a copy (a crash's staging folder is removed first; on any failure nothing is moved). Private to her as a fetched
/// file is (the voice service's Umask): the copy 0600, its folder 0700, never the bundle's 0644 (29 Oct: other accounts
/// could read it, and doctor's "Her files" failed). No network. The bytes are not checked here: `prepareModel` checks
/// them after, against the same pin, as a fetched file's. A missing or unreadable source throws (Pocket is then
/// unavailable, tried again later), as a failed download does.
func copyBundledFiles(_ pin: HubPin, from resources: URL, in base: URL, maxBytes: Int) throws {
    let files = FileManager.default
    let folder = pin.location(in: base)
    let parent = folder.deletingLastPathComponent()
    let prefix = "\(folder.lastPathComponent).download-"
    try files.createDirectory(at: parent, withIntermediateDirectories: true)
    for name in (try? files.contentsOfDirectory(atPath: parent.path)) ?? [] where name.hasPrefix(prefix) {
        try files.removeItem(at: parent.appendingPathComponent(name))
    }
    let staging = parent.appendingPathComponent(prefix + UUID().uuidString)
    do {
        try files.createDirectory(at: staging, withIntermediateDirectories: true)
        for name in pin.sha256.keys.sorted() {
            let source = resources.appendingPathComponent(name)
            // a plain file only, never a link followed; at most the model's cap
            let kind = try files.attributesOfItem(atPath: source.path)
            guard kind[.type] as? FileAttributeType == .typeRegular else { throw PocketFailure.changed }
            guard ((kind[.size] as? NSNumber)?.intValue ?? Int.max) <= maxBytes else { throw ModelDownloadError.overSizeCap }
            let copy = staging.appendingPathComponent(name)
            try files.copyItem(at: source, to: copy)
            try setMode(0o600, of: copy)  // a copy keeps its source's modes, whatever the umask
        }
        try setMode(0o700, of: staging, folder: true)
        if (try? files.attributesOfItem(atPath: folder.path)) != nil { try files.removeItem(at: folder) }
        try files.moveItem(at: staging, to: folder)
    } catch {
        try? files.removeItem(at: staging)
        throw error
    }
}

/// A shipped voice's copy made private to her, as `copyBundledFiles` makes a new one (a copy from before 29 Oct kept
/// the bundle's 0644): its folder 0700, each file 0600, set only where they differ, never through a link. Throws when a
/// mode cannot be read or set.
func keepPrivate(_ pin: HubPin, in base: URL) throws {
    let folder = pin.location(in: base)
    try setMode(0o700, of: folder, folder: true)
    for name in pin.sha256.keys.sorted() { try setMode(0o600, of: folder.appendingPathComponent(name)) }
}

/// Sets `url`'s mode to exactly `mode` (all its permission bits) when it differs: opened without following a link, a
/// plain file or (`folder`) a folder only.
func setMode(_ mode: mode_t, of url: URL, folder: Bool = false) throws {
    let fd = open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | (folder ? O_DIRECTORY : 0))
    guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
    defer { close(fd) }
    var info = stat()
    guard fstat(fd, &info) == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
    guard info.st_mode & S_IFMT == (folder ? S_IFDIR : S_IFREG) else { throw PocketFailure.changed }
    guard info.st_mode & 0o7777 != mode else { return }
    guard fchmod(fd, mode) == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
}

/// `voice-service --fetch-pocket`: the pinned release program into `programs` (the folder of the voice service's own
/// program: estia-deploy runs it as the owner in the release it builds, before root makes the release root's), then
/// both files against their sha256. False, with nothing left there, when any of it fails; one log line either way.
func fetchPocket(
    into programs: URL, release: PocketRelease = PocketTTS.release,
    fetch: FileFetch = { try await fetchFile($0, to: $1, maxBytes: $2) },
    run: ProgramRun = { try await runProgram($0, environment: $1, input: $2, seconds: $3, maxOutput: $4) }
) async -> Bool {
    do {
        try await downloadPocketRelease(release, in: programs, fetch: fetch, run: run)
        guard release.check(in: programs) == .ok else { throw PocketFailure.changed }
        log("Pocket TTS: its program fetched and checked (\(release.path) beside the voice service)")
        return true
    } catch {
        try? FileManager.default.removeItem(at: release.location(in: programs))
        log("Pocket TTS: its program was not fetched (\(error is PocketFailure ? PocketLine.reason(error) : downloadFailure(error)))")
        return false
    }
}

actor PocketSpeaker {
    enum State: String {
        /// A language's model, at every start and after a failure's wait: nothing checked, fetched or run until a line
        /// is asked in it (that line is not said; the check and fetch start).
        case idle
        /// Its files are being checked or fetched (its lines are not said meanwhile).
        case loading
        case ready
        /// Its program is not beside the voice service, its model could not be fetched, or its runs failed: tried
        /// again later (a language's: idle again after the wait, tried at the next line asked in it).
        case unavailable
        /// Its program is not the pinned one, or a model file is not even after a fresh fetch or could not be set
        /// aside: never run.
        case refused
    }

    private(set) var state: State
    private let models: URL
    private let her: URL
    private let programs: URL
    private let model: PocketModel
    private let release: PocketRelease
    /// Where its bundled voices' files are (a test's), else the voice service's own (`PocketTTS.resources`, beside its
    /// program, read at each check: the bundle's layout follows the build).
    private let resources: URL?
    private let download: @Sendable (HubPin, URL) async throws -> Void
    private let run: ProgramRun
    /// Runs its first run ("Ready.") where its lines run: on Pocket's queue in the service (`Engines.pocketQueue`: one
    /// run at a time on the CPU; the review of 25 Oct), never its check or fetch, which may take minutes.
    private let firstRun: @Sendable (@Sendable () async throws -> Data) async throws -> Data
    private let sleep: @Sendable (TimeInterval) async -> Void
    /// The clock a call's line reads before it tries a speaker resting after late runs (`PocketTTS.callRetrySeconds`).
    private let now: @Sendable () -> Date
    /// Its voices as the bridge names them (the log's).
    private let name: String
    /// A language's model (`PocketSpeech.language`): only the language in use is fetched, so it is idle at every start
    /// and never checked or fetched again on its own; after a failure's wait it is idle again. English's is fetched at
    /// the start (`PocketSpeech.atStart`).
    private let askedOnly: Bool
    /// A line was asked in it (English's: from the start).
    private var asked: Bool
    private var rests = 0
    /// Each checked file's stamp at its check: a run starts only while every one is the same.
    private var stamps: [URL: FileStamp] = [:]
    private var failures = 0
    /// Unavailable because `PocketTTS.failuresBeforeRest` runs failed in a row (its files and program checked): a call's
    /// line still runs (`speak(call:)`), as resting left every line of a call to Kokoro for 1 to 30 minutes (now
    /// unsaid).
    private var restingAfterRuns = false
    /// Resting after a run past its time limit: when a call's line last tried it (its rest's start, else that line's
    /// run), so a call's next try waits `PocketTTS.callRetrySeconds`; nil after a run that failed fast (each call line
    /// tries it).
    private var lateSince: Date?
    /// The rest now under way: each `rest()` starts a wait of its own, and a wait that ends after a later one began ends
    /// with no try (the second deep scan of 0.9.8, swift-2: a call's line that made it ready left the old wait asleep,
    /// and a rest after that waited it out, up to 30 minutes, where a fresh rest waits one).
    private var restGeneration = 0
    /// Its shipped voices (`PocketVoice.bundled`: heart's) whose file was not beside the voice service, or not the
    /// pinned one there, at the last check, or that failed its first run at load (`firstRuns`), by voice, with why
    /// ("missing", "not the pinned one", "failed its first run (exit 1)"). The review of 27 Oct (swift-voice-2): only an
    /// update puts such a file right, so the model is ready without it, only that voice's lines are not said (`speak`:
    /// never a failed run, never a rest) and no timer tries it again; the next check tries it again.
    private var missingShipped: [String: String] = [:]

    /// Unavailable after failed or late runs, its files and program checked (`/health`'s `tts` "resting": tried again on
    /// its own and by a call's lines), never for a missing program or files (`tts` "failed").
    var resting: Bool { state == .unavailable && restingAfterRuns }
    /// The shipped voices a ready model leaves out (`missingShipped`), by name: `/health`'s `voices_left_out`.
    var leftOut: [String] { state == .ready ? missingShipped.keys.sorted() : [] }

    /// `models`: the voice service's models folder; `her`: her home, which a run cannot read but Pocket's folder;
    /// `programs`: the folder of the voice service's own program, where the release program is (root's, in her
    /// release). `askedOnly`: a language's, idle at every start (`PocketTTS.languages`), else English's, checked and
    /// fetched at the start; `name`: its voices, for the log ("heart, jane, eve, alba, michael", "de: jane"); `resources`: where
    /// its bundled voices' files are (nil: the voice service's own, beside `programs`). The model's downloads (never a
    /// bundled voice's: copied), the run, where its first run runs (`firstRun`: at once unless the service puts it on
    /// Pocket's queue) and the wait between tries are the service's own, or a test's.
    init(
        models: URL, her: URL, programs: URL, model: PocketModel = PocketModel.english18M,
        release: PocketRelease = PocketTTS.release, askedOnly: Bool = false, name: String = "eve",
        resources: URL? = nil,
        download: (@Sendable (HubPin, URL) async throws -> Void)? = nil,
        run: @escaping ProgramRun = { try await runProgram($0, environment: $1, input: $2, seconds: $3, maxOutput: $4) },
        firstRun: @escaping @Sendable (@Sendable () async throws -> Data) async throws -> Data = { try await $0() },
        sleep: @escaping @Sendable (TimeInterval) async -> Void = { try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) },
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        let maxBytes = model.maxFileBytes
        self.models = models
        self.her = her
        self.programs = programs
        self.model = model
        self.release = release
        self.resources = resources
        self.name = name
        self.asked = !askedOnly
        self.askedOnly = askedOnly
        self.state = askedOnly ? .idle : .loading
        self.download = download ?? { pin, base in try await downloadPinnedFiles(pin, in: base, maxBytes: maxBytes) }
        self.run = run
        self.firstRun = firstRun
        self.sleep = sleep
        self.now = now
    }

    private var folder: URL { models.appendingPathComponent(PocketTTS.folder) }
    /// When the log says a failure is tried again: an English model on its own after the wait, a language's only at
    /// the next line asked in it after the wait (`retry`; the review of 25 Oct).
    static func retried(askedOnly: Bool) -> String {
        askedOnly ? "tried again at the next line in it, after a wait" : "tried again later"
    }
    private var retried: String { Self.retried(askedOnly: askedOnly) }
    var config: URL { folder.appendingPathComponent("config").appendingPathComponent("\(model.name).yaml") }
    private var program: URL { release.location(in: programs).appendingPathComponent(release.program) }
    private func file(_ pin: HubPin) -> URL { pin.location(in: models).appendingPathComponent(pin.sha256.keys.sorted()[0]) }

    /// Checks the program and every model file (fetching what of the model is missing), writes the config and says
    /// one word (its first run, then once in each shipped voice: `firstRuns`). Never throws: what fails is logged and
    /// leaves it unavailable (`retried`) or refused. A language's stays idle until a line is asked in it.
    func load() async {
        guard asked else {  // a language's: never at a start, only once a line is asked in it
            state = .idle
            return
        }
        await attempt()
        if state == .unavailable { rest() }
    }

    private func attempt() async {
        let start = Date()
        state = .loading
        restingAfterRuns = false
        lateSince = nil
        switch release.check(in: programs) {
        case .ok:
            break
        case .missing:
            state = .unavailable
            log("Pocket TTS (\(name)) unavailable: its program is not beside the voice service (an update puts it there: estia-deploy); no other voice speaks, \(retried)")
            return
        case .changed:
            state = .refused
            log("Pocket TTS (\(name)) refused: its program beside the voice service is not the pinned one (never run; an update puts the pinned one there: estia-deploy); no other voice speaks")
            return
        }
        switch await prepare() {
        case .ok:
            break
        case .missing:
            state = .unavailable
            log("Pocket TTS (\(name)) unavailable: files still missing after the download; no other voice speaks, \(retried)")
            return
        case .changed:
            state = .refused
            log("Pocket TTS (\(name)) refused: its files are not the pinned ones even after a fresh download, or could not be set aside (\(PocketTTS.folder) in voice-models; never run); no other voice speaks")
            return
        }
        guard stampChecked() else {
            state = .unavailable
            log("Pocket TTS (\(name)) unavailable: a file changed or went right after its check (never run); no other voice speaks, \(retried)")
            return
        }
        do {
            try writeConfig()
        } catch {
            state = .unavailable
            log("Pocket TTS (\(name)) unavailable: its config could not be written and checked (\(PocketLine.reason(error))); no other voice speaks, \(retried)")
            return
        }
        do {
            // the model's first run: its first fetched voice by name (a shipped one only when it has none)
            let names = model.voices.keys.sorted()
            let shipped = names.filter { model.voices[$0]?.bundled == true }
            let voice = names.first { !shipped.contains($0) } ?? names.first { missingShipped[$0] == nil } ?? names[0]
            _ = try await firstRun { try await self.say("Ready.", voice: voice) }  // on Pocket's queue, as a line
            await firstRuns(of: shipped.filter { $0 != voice && missingShipped[$0] == nil })
            state = .ready
            failures = 0
            rests = 0
            log("Pocket TTS (\(name)) ready in \(seconds(since: start))")
        } catch {
            state = .unavailable
            restingAfterRuns = true  // its files and program checked: a call's line may run (`speak(call:)`)
            // a first run past its time limit: a call's line tries it a minute on, as after a late line (the second
            // deep scan of 0.9.8, swift-1: the next call line ran it to its full limit again, in silence)
            lateSince = error as? PocketFailure == .timeout ? now() : nil
            log("Pocket TTS (\(name)) unavailable: its first run failed (\(PocketLine.reason(error))); no other voice speaks, \(retried)")
        }
    }

    /// Each shipped voice says "Ready." once, on Pocket's queue as a line (the release review of 0.9.8, V01: heart, the
    /// default voice, never ran at load, so a file with its pinned bytes that fails every run left load ready, and its
    /// first three lines rested the whole model). One that fails for its file (an exit, a signal, too much audio, no
    /// WAV) is set aside as a missing one is (`missingShipped`: its lines not said, never run, no rest) until the next
    /// check; one past its time limit (a busy Mac, not a broken file) or whose files changed is not: its next line runs.
    private func firstRuns(of voices: [String]) async {
        for voice in voices {
            do {
                _ = try await firstRun { try await self.say("Ready.", voice: voice) }
            } catch {
                let why = PocketLine.reason(error)
                switch error as? PocketFailure {
                case .exit?, .signal?, .tooLarge?:
                    break
                case nil where error is PocketWAV.Invalid:
                    break
                default:
                    log("Pocket TTS (\(name)): \(voice)'s first run failed (\(why)), not set aside: its next line runs it")
                    continue
                }
                missingShipped[voice] = "failed its first run (\(why))"
                log("Pocket TTS (\(name)): \(voice)'s voice failed its first run (\(why)); no other voice says \(voice)'s lines until its next check (the next start or an update)")
            }
        }
    }

    /// Every model file against its pin, each fetched when missing (a bundled voice's copied from the voice service's
    /// own resources, never fetched). A fetch that fails is logged; `.missing` then. A bundled voice's file comes last
    /// and never stops the model: one that is not beside the voice service, or not the pinned one, is logged once and
    /// kept in `missingShipped` (its lines not said until an update puts it there).
    private func prepare() async -> ModelFiles {
        let (models, download, name) = (self.models, self.download, self.name)
        let resources = self.resources ?? PocketTTS.resources(beside: programs)
        let (bundled, maxBytes) = (model.bundled, model.maxFileBytes)
        func aside(_ pin: HubPin) -> (URL) -> Void {
            let again = bundled.contains(pin) ? "copying the shipped one again" : "downloading the pinned ones again"
            return { log("Pocket TTS (\(name)): files changed, not the pinned ones (\(pin.path)): set aside as \($0.lastPathComponent), never run; \(again)") }
        }
        missingShipped = [:]
        for pin in model.pins where !bundled.contains(pin) {
            do {
                let files = try await prepareModel(pin, in: models, setAside: aside(pin)) { try await download($0, models) }
                guard files == .ok else { return files }
            } catch {
                log("Pocket TTS (\(name)): download failed (\(downloadFailure(error)))")
                return .missing
            }
        }
        for voice in model.voices.keys.sorted() {
            guard let pin = model.voices[voice]?.pin, bundled.contains(pin) else { continue }
            var files: ModelFiles
            var why = ""
            do {
                files = try await prepareModel(pin, in: models, setAside: aside(pin)) {
                    try copyBundledFiles($0, from: resources, in: models, maxBytes: maxBytes)
                }
            } catch {
                // a link or a file over the model's cap is not the pinned one; one that is not there or cannot be read is
                // missing (the log names why, but for a file that is simply not there)
                let other = error as? PocketFailure == .changed || error as? ModelDownloadError == .overSizeCap
                files = other ? .changed : .missing
                let cocoa = error as NSError
                let absent = cocoa.domain == NSCocoaErrorDomain && [NSFileNoSuchFileError, NSFileReadNoSuchFileError].contains(cocoa.code)
                why = absent ? "" : "; \((error as? PocketFailure)?.description ?? downloadFailure(error))"
            }
            switch files {
            case .ok:
                do {
                    try keepPrivate(pin, in: models)  // a copy from before 29 Oct: the bundle's modes
                } catch {
                    log("Pocket TTS (\(name)): \(voice)'s voice file could not be made private to her (\(pin.path); \((error as? PocketFailure)?.description ?? "\(type(of: error))"))")
                }
                continue
            case .missing:
                missingShipped[voice] = "missing"
                log("Pocket TTS (\(name)): \(voice)'s voice file is not beside the voice service (\(pin.path)\(why); an update puts it there: estia-deploy); no other voice says \(voice)'s lines")
            case .changed:
                missingShipped[voice] = "not the pinned one"
                log("Pocket TTS (\(name)): \(voice)'s voice file beside the voice service is not the pinned one (\(pin.path)\(why); never run; an update puts the pinned one there: estia-deploy); no other voice says \(voice)'s lines")
            }
        }
        return .ok
    }

    /// The stamps of the checked files, the program's too, each from the bytes it was hashed from again
    /// (`FileStamp.checked`; the review of 26 Oct, R2-F3: one taken by path after the check could be a file swapped in
    /// between, and one missing was never checked again): false, with no stamps, when any is missing, not a plain
    /// file or not its pin's bytes.
    private func stampChecked() -> Bool {
        stamps = [:]
        let left = missingShipped.keys.compactMap { model.voices[$0]?.pin }  // never run: not checked here
        let pinned = [(program, release.sha256[release.program] ?? "")]
            + model.pins.filter { !left.contains($0) }.map { (file($0), $0.sha256[$0.sha256.keys.sorted()[0]] ?? "") }
        for (url, sha256) in pinned {
            guard let stamp = FileStamp.checked(url, sha256: sha256) else {
                stamps = [:]
                return false
            }
            stamps[url] = stamp
        }
        return true
    }

    /// The config for the checked files (0600, its folder 0700); its own stamp, from the bytes written, joins the
    /// checked ones (throws when it cannot be taken).
    private func writeConfig() throws {
        let text = model.configText(weights: PocketRun.real(file(model.weights)), tokenizer: PocketRun.real(file(model.tokenizer)))
        try FileManager.default.createDirectory(at: config.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        try Data(text.utf8).write(to: config, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: config.path)
        guard let stamp = FileStamp.checked(config, sha256: sha256Hex(Data(text.utf8))) else { throw PocketFailure.changed }
        stamps[config] = stamp
    }

    /// `text` (already as Pocket reads it) in `voice`, as the bridge gets every line (`SpeechAudio`) at the voice's
    /// gain. Throws when it cannot (`PocketFailure`, `PocketWAV.Invalid`): no other voice says that line (`PocketLine`).
    /// `call`: a call's line, which runs while it rests after failed runs (the review of 28 Oct, calls-03: a call tries
    /// Pocket again at each line); a run that works makes it ready again, one that fails leaves it resting and counts
    /// no further.
    func speak(_ text: String, voice: String, call: Bool = false) async throws -> Data {
        if state == .idle {  // the first line asked in it: its check and fetch start, this one is not said
            asked = true
            state = .loading
            log("Pocket TTS (\(name)): first asked; checking its files, fetching any missing")
            Task { await self.load() }
            throw PocketFailure.notReady(State.idle.rawValue)
        }
        let due = lateSince.map { now().timeIntervalSince($0) >= PocketTTS.callRetrySeconds } ?? true
        let again = call && state == .unavailable && restingAfterRuns && due
        guard state == .ready || again else { throw PocketFailure.notReady(state.rawValue) }
        // a shipped voice whose file an update has to put right: its line not said, never a failed run (no rest)
        if let why = missingShipped[voice] { throw PocketFailure.notReady("shipped voice \(voice) \(why)") }
        do {
            let wav = try await say(text, voice: voice)
            failures = 0
            if again && state == .unavailable {
                state = .ready
                restingAfterRuns = false
                lateSince = nil
                log("Pocket TTS (\(name)) ready again: a call's line ran")
            }
            return wav
        } catch PocketFailure.changed {
            state = .loading
            log("Pocket TTS (\(name)): a checked file changed; checked again before its next run")
            Task { await self.load() }
            throw PocketFailure.changed
        } catch {
            let late = error as? PocketFailure == .timeout
            // still resting: this line not said, the call's next one tries (after a late run, a minute on)
            guard !again else {
                lateSince = late ? now() : nil
                throw error
            }
            failures += 1
            if failures >= PocketTTS.failuresBeforeRest {
                state = .unavailable
                restingAfterRuns = true
                lateSince = late ? now() : nil
                log("Pocket TTS (\(name)) unavailable: \(failures) runs failed in a row; no other voice speaks, \(retried); a call's lines try it \(late ? "once a minute" : "at once")")
                rest()
            }
            throw error
        }
    }

    /// One run: every checked file still as checked, then the program in its sandbox, within its text's time limit
    /// (`PocketTTS.runSeconds`).
    private func say(_ text: String, voice: String) async throws -> Data {
        guard let spoken = model.voices[voice] else { throw PocketFailure.notReady("no voice \(voice)") }
        guard !stamps.isEmpty, stamps.allSatisfy({ FileStamp.of($0.key) == $0.value }) else { throw PocketFailure.changed }
        let argv = PocketRun.arguments(program: program, config: config, voice: PocketRun.real(file(spoken.pin)),
                                       pocket: folder, her: her)
        let result = try await run(argv, PocketRun.environment(pocket: folder), Data(text.utf8),
                                   PocketTTS.runSeconds(for: text), PocketTTS.maxOutputBytes)
        if result.timedOut { throw PocketFailure.timeout }
        if result.tooLarge { throw PocketFailure.tooLarge }
        if result.signalled { throw PocketFailure.signal(result.status) }
        if result.status != 0 { throw PocketFailure.exit(result.status) }
        return try PocketWAV.speech(result.output, gainDB: spoken.gainDB)
    }

    /// Tries again after `SpeakingRetry`'s waits (1, 5 and 30 minutes, then every 30) while it is unavailable; a rest
    /// begun after this one makes this one's wait end with no try (`restGeneration`).
    private func rest() {
        restGeneration += 1
        let generation = restGeneration
        Task { await self.retry(generation) }
    }

    private func retry(_ generation: Int) async {
        if askedOnly {  // a language's: after the wait it is idle, and only a line asked in it tries again
            await sleep(SpeakingRetry.delay(afterFailures: rests))
            guard generation == restGeneration else { return }  // a later rest's wait is the one that counts
            rests += 1
            if state == .unavailable { state = .idle }
            return
        }
        var tries = 0
        while state == .unavailable {
            await sleep(SpeakingRetry.delay(afterFailures: tries))
            guard generation == restGeneration else { return }  // a later rest began its own wait
            tries += 1
            guard state == .unavailable else { break }
            await attempt()
        }
    }
}

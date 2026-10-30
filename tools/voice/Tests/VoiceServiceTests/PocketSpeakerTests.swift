import Foundation
import Testing
import VoiceCore

@testable import voice_service

/// What the fakes saw (downloads, runs), across the actor's threads.
private final class Spy: @unchecked Sendable {
    private let lock = NSLock()
    private var _downloads: [String] = []
    private var _runs: [(argv: [String], environment: [String: String], input: String, seconds: TimeInterval)] = []
    private var _waits: [TimeInterval] = []
    var answer: ProgramResult

    init(answer: ProgramResult) { self.answer = answer }

    var downloads: [String] { lock.withLock { _downloads } }
    var runs: [(argv: [String], environment: [String: String], input: String, seconds: TimeInterval)] { lock.withLock { _runs } }
    var waits: [TimeInterval] { lock.withLock { _waits } }

    func downloaded(_ path: String) { lock.withLock { _downloads.append(path) } }
    func ran(_ argv: [String], _ environment: [String: String], _ input: Data, _ seconds: TimeInterval) -> ProgramResult {
        lock.withLock {
            _runs.append((argv, environment, String(decoding: input, as: UTF8.self), seconds))
            return answer
        }
    }
    func waited(_ seconds: TimeInterval) { lock.withLock { _waits.append(seconds) } }
}

/// A clock a test moves on.
private final class TestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var at = Date(timeIntervalSince1970: 1_800_000_000)
    var now: Date { lock.withLock { at } }
    func pass(_ seconds: TimeInterval) { lock.withLock { at = at.addingTimeInterval(seconds) } }
}

/// Waits a test ends by hand (a rest's), in the order they began, and their lengths.
private final class Gates: @unchecked Sendable {
    private let lock = NSLock()
    private var waiting: [CheckedContinuation<Void, Never>] = []
    private var _waits: [TimeInterval] = []
    var waits: [TimeInterval] { lock.withLock { _waits } }

    func sleep(_ seconds: TimeInterval) async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            lock.withLock {
                _waits.append(seconds)
                waiting.append(continuation)
            }
        }
    }

    /// Ends the oldest wait still going.
    func wake() {
        let next: CheckedContinuation<Void, Never>? = lock.withLock { waiting.isEmpty ? nil : waiting.removeFirst() }
        next?.resume()
    }
}

/// Whether `condition` holds within about two seconds.
private func eventually(_ condition: @Sendable () async -> Bool) async -> Bool {
    for _ in 0..<400 {
        if await condition() { return true }
        try? await Task.sleep(nanoseconds: 5_000_000)
    }
    return await condition()
}

/// Steps in the order they came, across threads.
private final class Steps: @unchecked Sendable {
    private let lock = NSLock()
    private var steps: [String] = []
    var all: [String] { lock.withLock { steps } }
    func add(_ step: String) { lock.withLock { steps.append(step) } }
}

/// A run that takes a few ms and counts how many ran at once.
private final class Overlaps: @unchecked Sendable {
    private let lock = NSLock()
    private var running = 0
    private var _most = 0
    let answer: ProgramResult

    init(answer: ProgramResult) { self.answer = answer }

    var most: Int { lock.withLock { _most } }

    func run() async -> ProgramResult {
        lock.withLock {
            running += 1
            _most = max(_most, running)
        }
        try? await Task.sleep(nanoseconds: 5_000_000)
        lock.withLock { running -= 1 }
        return answer
    }
}

private func sha(_ text: String) -> String { sha256Hex(Data(text.utf8)) }

private func write(_ text: String, to url: URL) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data(text.utf8).write(to: url)
}

/// The program's WAV to a pipe: placeholder sizes, then `samples`.
private func programWAV(_ samples: [Int16]) -> Data {
    func le32(_ value: Int) -> [UInt8] { (0..<4).map { UInt8((value >> (8 * $0)) & 0xFF) } }
    func le16(_ value: Int) -> [UInt8] { (0..<2).map { UInt8((value >> (8 * $0)) & 0xFF) } }
    var header: [UInt8] = Array("RIFF".utf8) + le32(2_000_000_036) + Array("WAVEfmt ".utf8)
    header += le32(16) + le16(1) + le16(1) + le32(24_000) + le32(48_000) + le16(2) + le16(16)
    header += Array("data".utf8) + le32(2_000_000_000)
    return Data(header) + samples.map(\.littleEndian).withUnsafeBytes { Data($0) }
}

@Suite struct PocketSpeaking {
    private func models() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("pocket-\(UUID().uuidString)")
    }

    /// A model of three one-word files ("w", "t", "v") and a release of two ("p", "l"), at fake commits; the release in
    /// a folder of its own (`programs`: beside the voice service, in her root-owned release), the model in `models`.
    private let model = PocketModel(
        name: "test",
        weights: HubPin(repo: "example/p", revision: String(repeating: "1", count: 40), folder: "w",
                        sha256: ["model.safetensors": sha("w")], local: "pocket-tts/test/weights"),
        tokenizer: HubPin(repo: "example/p", revision: String(repeating: "2", count: 40), folder: "w",
                          sha256: ["tokenizer.json": sha("t")], local: "pocket-tts/test/tokenizer"),
        voices: ["eve": PocketVoice(
            pin: HubPin(repo: "example/p", revision: String(repeating: "3", count: 40), folder: "e",
                        sha256: ["eve.safetensors": sha("v")], local: "pocket-tts/test/eve"),
            gainDB: -6.0)],
        config: "weights_path: {weights}\ntokenizer_path: {tokenizer}\n",
        maxFileBytes: 1 << 20)
    private let release = PocketRelease(
        url: URL(string: "https://example.com/p.tar.gz")!, tarballSHA256: String(repeating: "0", count: 64),
        maxTarballBytes: 1 << 20, top: "p", path: "pocket-tts",
        sha256: ["pocket-tts": sha("p"), "LICENSE": sha("l")])

    /// The folder of the voice service's own program, with the release program as a deploy leaves it there (other
    /// bytes for the files named in `wrong`; none with `missing`).
    private func programs(_ models: URL, wrong: Set<String> = [], missing: Bool = false) throws -> URL {
        let folder = models.appendingPathComponent("release/tools/voice/.build/release")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        if !missing {
            for (name, text) in contents(wrong) where release.sha256[name] != nil {
                try write(text, to: release.location(in: folder).appendingPathComponent(name))
            }
        }
        return folder
    }

    /// What a fetch brings: the right bytes, or other ones for the files named in `wrong`.
    private func contents(_ wrong: Set<String>) -> [String: String] {
        let right = ["model.safetensors": "w", "tokenizer.json": "t", "eve.safetensors": "v", "jane.safetensors": "j", "pocket-tts": "p", "LICENSE": "l"]
        return Dictionary(uniqueKeysWithValues: right.map { ($0.key, wrong.contains($0.key) ? $0.value.uppercased() : $0.value) })
    }

    private func speaker(
        _ models: URL, _ spy: Spy, offline: Bool = false, wrong: Set<String> = [], programs: URL? = nil,
        model: PocketModel? = nil, askedOnly: Bool = false, shortRest: Bool = false, resources: URL? = nil,
        clock: TestClock? = nil, sleep: (@Sendable (TimeInterval) async -> Void)? = nil
    ) throws -> PocketSpeaker {
        let bytes = contents(wrong)
        return PocketSpeaker(
            models: models, her: models, programs: try programs ?? self.programs(models), model: model ?? self.model,
            release: release, askedOnly: askedOnly, name: askedOnly ? "de: jane" : "eve", resources: resources,
            download: { pin, base in
                spy.downloaded(pin.path)
                guard !offline else { return }
                for name in pin.sha256.keys { try write(bytes[name] ?? "", to: pin.location(in: base).appendingPathComponent(name)) }
            },
            run: { argv, environment, input, seconds, _ in spy.ran(argv, environment, input, seconds) },
            sleep: sleep ?? { seconds in
                spy.waited(seconds)
                guard !shortRest else { return }  // a rest that ends at once (a language's: it only turns idle again)
                try? await Task.sleep(nanoseconds: 60_000_000_000)  // the next try never comes within a test
            },
            now: { clock?.now ?? Date() })
    }

    private func spoken(_ wav: Data) -> [Int16] {
        wav.dropFirst(44).withUnsafeBytes { raw in
            (0..<(raw.count / 2)).map { Int16(littleEndian: raw.loadUnaligned(fromByteOffset: $0 * 2, as: Int16.self)) }
        }
    }

    /// A first start checks the program beside the voice service (never fetched by it), fetches every model file at
    /// its pin, checks it, writes the local config and says one word; then a line is one sandboxed run, its text on
    /// stdin, its WAV at the voice's gain, within its text's time limit.
    @Test func aFirstStartFetchesChecksAndSpeaks() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy(answer: ProgramResult(output: programWAV([1000, -2000])))
        let programs = try programs(models)
        let pocket = try speaker(models, spy, programs: programs)
        #expect(await pocket.state == .loading)
        await pocket.load()
        #expect(await pocket.state == .ready)
        #expect(spy.downloads == ["pocket-tts/test/weights", "pocket-tts/test/tokenizer", "pocket-tts/test/eve"])
        #expect(spy.runs.map(\.input) == ["Ready."])  // its first run
        let config = try String(contentsOf: await pocket.config, encoding: .utf8)
        let real = PocketRun.real(models).path
        #expect(config == "weights_path: '\(real)/pocket-tts/test/weights/model.safetensors'\ntokenizer_path: '\(real)/pocket-tts/test/tokenizer/tokenizer.json'\n")
        let mode = try FileManager.default.attributesOfItem(atPath: await pocket.config.path)[.posixPermissions] as? Int
        #expect(mode == 0o600)
        let wav = try await pocket.speak("Hello there.", voice: "eve")
        #expect(spoken(wav).count == 2 && abs(Int(spoken(wav)[0]) - 501) <= 1 && abs(Int(spoken(wav)[1]) + 1002) <= 1)
        let run = try #require(spy.runs.last)
        #expect(run.input == "Hello there." && run.seconds == PocketTTS.runSeconds(for: "Hello there."))
        #expect(run.argv[0] == "/usr/bin/sandbox-exec" && run.argv.contains("--quantize"))
        #expect(run.argv.contains("\(real)/pocket-tts/test/eve/eve.safetensors"))
        #expect(run.argv.contains("POCKET=\(real)/pocket-tts") && run.argv.contains("HER=\(real)"))
        #expect(run.argv.contains("\(PocketRun.real(programs).path)/pocket-tts/pocket-tts"))
        #expect(run.argv.contains("PROGRAM=\(PocketRun.real(programs).path)/pocket-tts"))
        #expect(run.argv.contains("PROGRAM_FILE=\(PocketRun.real(programs).path)/pocket-tts/pocket-tts"))  // the one it may start
        #expect(run.environment == PocketRun.environment(pocket: models.appendingPathComponent("pocket-tts")))
        // a second start finds every file checked: nothing fetched again
        let again = try speaker(models, spy, programs: programs)
        await again.load()
        #expect(await again.state == .ready && spy.downloads.count == 3)
    }

    /// Owner, 25 Oct ("could I try Jane also for a bit?"): a second voice beside eve in the same model. Its file is
    /// fetched and checked with the model's, a line runs with the asked voice's file and comes out at that voice's own
    /// gain, and a voice the model does not have is never run (Kokoro says that line).
    @Test func eachVoiceSpeaksFromItsOwnFileAtItsOwnGain() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let jane = PocketVoice(
            pin: HubPin(repo: "example/p", revision: String(repeating: "4", count: 40), folder: "j",
                        sha256: ["jane.safetensors": sha("j")], local: "pocket-tts/test/jane"),
            gainDB: 0.0)
        let two = PocketModel(
            name: model.name, weights: model.weights, tokenizer: model.tokenizer,
            voices: model.voices.merging(["jane": jane]) { $1 }, config: model.config, maxFileBytes: model.maxFileBytes)
        let spy = Spy(answer: ProgramResult(output: programWAV([1000, -2000])))
        let pocket = try speaker(models, spy, model: two)
        await pocket.load()
        #expect(await pocket.state == .ready)
        #expect(spy.downloads == ["pocket-tts/test/weights", "pocket-tts/test/tokenizer", "pocket-tts/test/eve", "pocket-tts/test/jane"])
        #expect(spy.runs.map(\.input) == ["Ready."])  // the first run is eve's: the first voice by name
        let real = PocketRun.real(models).path
        let (eveRun, janeRun) = (try await pocket.speak("Hi.", voice: "eve"), try await pocket.speak("Hi.", voice: "jane"))
        #expect(spy.runs[1].argv.contains("\(real)/pocket-tts/test/eve/eve.safetensors"))
        #expect(!spy.runs[1].argv.contains("\(real)/pocket-tts/test/jane/jane.safetensors"))
        #expect(spy.runs[2].argv.contains("\(real)/pocket-tts/test/jane/jane.safetensors"))
        #expect(abs(Int(spoken(eveRun)[0]) - 501) <= 1)  // eve's -6 dB
        #expect(abs(Int(spoken(janeRun)[0]) - 1000) <= 1 && abs(Int(spoken(janeRun)[1]) + 2000) <= 1)  // jane's 0 dB
        await #expect(throws: PocketFailure.notReady("no voice alba")) { try await pocket.speak("Hi.", voice: "alba") }
        #expect(spy.runs.count == 3)
        // a second start finds both checked: nothing fetched again
        let again = try speaker(models, spy, model: two)
        await again.load()
        #expect(await again.state == .ready && spy.downloads.count == 4)
        // jane's file changed after its check: never run
        try write("J", to: models.appendingPathComponent("pocket-tts/test/jane/jane.safetensors"))
        await #expect(throws: PocketFailure.changed) { try await pocket.speak("Hi.", voice: "jane") }
        for _ in 0..<200 where await pocket.state != .ready { try await Task.sleep(nanoseconds: 5_000_000) }
        #expect(await pocket.state == .ready && spy.downloads.suffix(1) == ["pocket-tts/test/jane"])  // fetched fresh
    }

    /// A model of eve (fetched) and heart, whose file ships with the voice service (`PocketVoice.bundled`).
    private var withHeart: PocketModel {
        let heart = PocketVoice(
            pin: HubPin(repo: "", revision: "", folder: "", sha256: ["heart.safetensors": sha("h")],
                        local: "pocket-tts/test/heart"),
            gainDB: 0.0, bundled: true)
        return PocketModel(
            name: model.name, weights: model.weights, tokenizer: model.tokenizer,
            voices: model.voices.merging(["heart": heart]) { $1 }, config: model.config, maxFileBytes: model.maxFileBytes)
    }

    /// Owner, 27 Oct ("call the voice Heart"; its file in the public repository: "yes"): a voice that ships with the
    /// voice service (`PocketVoice.bundled`) is never fetched: its file is copied from the voice service's own
    /// resources beside its program (`PocketTTS.resources`: no download, no network) into its folder beside the other
    /// voices, then checked by its sha256 as they are, and run from there. Not there, or other bytes there, only its
    /// own lines are Kokoro's (the next test); a copy changed in the models folder is set aside and copied again.
    @Test func aShippedVoiceIsCopiedFromBesideTheVoiceServiceNeverFetched() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let two = withHeart
        let programs = try programs(models)
        // the default build's layout (swiftbuild, Swift 6.4): the file in the bundle's Contents/Resources
        let resources = programs.appendingPathComponent(PocketTTS.bundle).appendingPathComponent("Contents/Resources")
        let shipped = resources.appendingPathComponent("heart.safetensors")
        try write("h", to: shipped)
        let spy = Spy(answer: ProgramResult(output: programWAV([1000, -2000])))
        let pocket = try speaker(models, spy, programs: programs, model: two)
        await pocket.load()
        #expect(await pocket.state == .ready)
        #expect(spy.downloads == ["pocket-tts/test/weights", "pocket-tts/test/tokenizer", "pocket-tts/test/eve"])  // never heart's
        let copy = models.appendingPathComponent("pocket-tts/test/heart/heart.safetensors")
        #expect(try String(contentsOf: copy, encoding: .utf8) == "h")
        let wav = try await pocket.speak("Hi.", voice: "heart")
        #expect(spy.runs.last?.argv.contains("\(PocketRun.real(models).path)/pocket-tts/test/heart/heart.safetensors") == true)
        #expect(abs(Int(spoken(wav)[0]) - 1000) <= 1)  // its own gain
        #expect(try String(contentsOf: shipped, encoding: .utf8) == "h")  // the voice service's own file stays as it is
        // a copy changed in the models folder: set aside, copied again, ready
        try write("X", to: copy)
        let again = try speaker(models, spy, programs: programs, model: two)
        await again.load()
        #expect(await again.state == .ready)
        #expect(try String(contentsOf: copy, encoding: .utf8) == "h" && spy.downloads.count == 3)
    }

    /// 29 Oct: the copy of a shipped voice's file kept the bundle's modes (0644, root's in her release), so other accounts
    /// could read it in her models folder and doctor's "Her files" failed. The copy is private to her: the file 0600, its
    /// folder 0700, whatever the source's modes (a copy keeps them, whatever the umask); a copy from before, readable by
    /// others, is made private at the next check (never copied again for it) and still speaks.
    @Test func aShippedVoicesCopyIsPrivateToHer() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let programs = try programs(models)
        let shipped = programs.appendingPathComponent(PocketTTS.bundle).appendingPathComponent("Contents/Resources/heart.safetensors")
        try write("h", to: shipped)
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: shipped.path)  // as the bundle has it
        let copy = models.appendingPathComponent("pocket-tts/test/heart/heart.safetensors")
        func mode(_ url: URL) throws -> Int? {
            try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int
        }
        let spy = Spy(answer: ProgramResult(output: programWAV([1000, -2000])))
        let pocket = try speaker(models, spy, programs: programs, model: withHeart)
        await pocket.load()
        #expect(await pocket.state == .ready)
        #expect(try mode(copy) == 0o600 && mode(copy.deletingLastPathComponent()) == 0o700)
        #expect(try mode(shipped) == 0o644)  // the voice service's own file stays as it is
        _ = try await pocket.speak("Hi.", voice: "heart")
        // a copy from before this fix: 0644 in a 0755 folder. The next check makes both private, copies nothing again
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: copy.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: copy.deletingLastPathComponent().path)
        let inode = try FileManager.default.attributesOfItem(atPath: copy.path)[.systemFileNumber] as? Int
        let again = try speaker(models, spy, programs: programs, model: withHeart)
        await again.load()
        #expect(await again.state == .ready)
        #expect(try mode(copy) == 0o600 && mode(copy.deletingLastPathComponent()) == 0o700)
        #expect(try FileManager.default.attributesOfItem(atPath: copy.path)[.systemFileNumber] as? Int == inode)  // not copied again
        let runs = spy.runs.count
        _ = try await again.speak("Hi.", voice: "heart")  // its stamp taken after the change: it runs
        #expect(spy.runs.count == runs + 1 && spy.runs.last?.argv.contains("\(PocketRun.real(models).path)/pocket-tts/test/heart/heart.safetensors") == true)
    }

    /// The review of 27 Oct (swift-voice-1): the native build (Swift 6.2's default, `--build-system native`) lays the
    /// voice service's bundle out flat, its files at the bundle's top, where the default build of Swift 6.4
    /// (swiftbuild) puts them in Contents/Resources. Such a Mac's voice service reads heart's file there too (as
    /// Foundation reads the bundle), so heart is ready, never Kokoro's for the bundle's layout.
    @Test func aFlatBundleBesideTheVoiceServiceGivesHeartToo() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let programs = try programs(models)
        let flat = programs.appendingPathComponent(PocketTTS.bundle)
        try write("h", to: flat.appendingPathComponent("heart.safetensors"))
        let spy = Spy(answer: ProgramResult(output: programWAV([1000, -2000])))
        let pocket = try speaker(models, spy, programs: programs, model: withHeart)  // its own resources, beside it
        await pocket.load()
        #expect(await pocket.state == .ready)
        _ = try await pocket.speak("Hi.", voice: "heart")
        #expect(spy.runs.last?.argv.contains("\(PocketRun.real(models).path)/pocket-tts/test/heart/heart.safetensors") == true)
        // the same flat bundle named as `resources`
        let named = try speaker(models, spy, programs: programs, model: withHeart, resources: flat)
        await named.load()
        #expect(await named.state == .ready)
        _ = try await named.speak("Hi.", voice: "heart")
    }

    /// The review of 27 Oct (swift-voice-2, CALLS-2): a shipped voice's file that is not beside the voice service, or
    /// not the pinned one there, is only an update's to put right, so it never takes the model's other voices down
    /// and no timer tries it again: the model is ready, eve (and jane, alba, michael on a real Mac) speak Pocket's, and
    /// a line in heart is Kokoro's (`notReady`), never a failed run counted towards a rest. Nothing is fetched in its
    /// place and it is never run.
    @Test func aMissingShippedVoiceLeavesTheOtherVoicesReady() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let programs = try programs(models)
        let shipped = programs.appendingPathComponent(PocketTTS.bundle).appendingPathComponent("Contents/Resources/heart.safetensors")
        let copy = models.appendingPathComponent("pocket-tts/test/heart/heart.safetensors")
        let real = PocketRun.real(models).path
        for bytes in [nil, "H"] {  // not there; other bytes there
            if let bytes { try write(bytes, to: shipped) }
            let spy = Spy(answer: ProgramResult(output: programWAV([1000, -2000])))
            let pocket = try speaker(models, spy, programs: programs, model: withHeart)
            await pocket.load()
            #expect(await pocket.state == .ready, "\(bytes ?? "none")")
            #expect(spy.downloads.allSatisfy { $0 != "pocket-tts/test/heart" })  // never fetched in its place
            #expect(spy.runs.map(\.input) == ["Ready."] && spy.runs[0].argv.contains("\(real)/pocket-tts/test/eve/eve.safetensors"))
            _ = try await pocket.speak("Hi.", voice: "eve")
            #expect(spy.runs.count == 2)
            let why = bytes == nil ? "shipped voice heart missing" : "shipped voice heart not the pinned one"
            for _ in 0..<(PocketTTS.failuresBeforeRest + 1) {
                await #expect(throws: PocketFailure.notReady(why)) { try await pocket.speak("Hi.", voice: "heart") }
            }
            #expect(spy.runs.count == 2)  // heart never run
            #expect(await pocket.state == .ready && spy.waits.isEmpty)  // no rest, no timer
            _ = try await pocket.speak("Hi.", voice: "eve")  // eve still Pocket's
            #expect(spy.runs.count == 3 && !spy.runs.contains { $0.argv.contains("\(real)/pocket-tts/test/heart/heart.safetensors") })
            if bytes == nil { #expect(!FileManager.default.fileExists(atPath: copy.path)) }
        }
        // an update puts the pinned file there: the next start speaks heart
        try write("h", to: shipped)
        let spy = Spy(answer: ProgramResult(output: programWAV([1000, -2000])))
        let fixed = try speaker(models, spy, programs: programs, model: withHeart)
        await fixed.load()
        _ = try await fixed.speak("Hi.", voice: "heart")
        #expect(spy.runs.last?.argv.contains("\(real)/pocket-tts/test/heart/heart.safetensors") == true)
    }

    /// The files the voice service ships for heart (owner, 27 Oct: its file in the public repository, "yes"; 29 Oct, the
    /// 18M's beside it: "just make the small heart") are the repository's `Sources/voice-service/Voices/heart.safetensors`
    /// (the 100M's) and `heart-18m.safetensors` (the 18M's), built into the resources beside its program
    /// (`PocketTTS.resources`: the voice service's own bundle, which the deploy copies with its program), with the
    /// pinned bytes.
    @Test func heartShipsBesideTheVoiceServiceWithItsPinnedBytes() throws {
        #expect(Bundle.module.bundleURL.lastPathComponent == PocketTTS.bundle)
        for (model, name, size) in [(PocketModel.english, "heart.safetensors", 8_652_024),
                                    (PocketModel.english18M, "heart-18m.safetensors", 5_768_848)] {
            let pin = try #require(model.voices["heart"]?.pin)
            let sha = try #require(pin.sha256[name])
            let built = PocketTTS.resources(beside: Bundle.module.bundleURL.deletingLastPathComponent())
                .appendingPathComponent(name)
            #expect(try sha256Hex(file: built) == sha, "\(name)")
            let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
                .deletingLastPathComponent().appendingPathComponent("Sources/voice-service/Voices/\(name)")
            #expect(try sha256Hex(file: source) == sha, "\(name)")
            #expect((try FileManager.default.attributesOfItem(atPath: source.path)[.size] as? NSNumber)?.intValue == size)
        }
    }

    /// The review of 24 Oct: the program lived in her home, where any program of hers could change it. It is only
    /// ever beside the voice service now (a deploy puts it there): none there, unavailable (Kokoro speaks, nothing of
    /// the model fetched, tried again later); other bytes, refused, never run, never moved or fetched over.
    @Test func theProgramIsOnlyTakenFromBesideTheVoiceService() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy(answer: ProgramResult(output: programWAV([1])))
        let none = try speaker(models, spy, programs: try programs(models, missing: true))
        await none.load()
        #expect(await none.state == .unavailable && spy.downloads.isEmpty && spy.runs.isEmpty)
        await #expect(throws: PocketFailure.notReady("unavailable")) { try await none.speak("Hi.", voice: "eve") }
        for _ in 0..<200 where spy.waits.isEmpty { try await Task.sleep(nanoseconds: 5_000_000) }
        #expect(spy.waits.first == 60)
        let changed = try programs(models, wrong: ["pocket-tts"])
        let other = try speaker(models, spy, programs: changed)
        await other.load()
        #expect(await other.state == .refused && spy.downloads.isEmpty && spy.runs.isEmpty)
        let program = changed.appendingPathComponent("pocket-tts/pocket-tts")
        #expect(try String(contentsOf: program, encoding: .utf8) == "P")  // left as it is: root's to put right
        #expect(try FileManager.default.contentsOfDirectory(atPath: changed.path) == ["pocket-tts"])
    }

    /// Offline at the first start: unavailable (Kokoro speaks), tried again after 1 minute, then ready.
    @Test func offlineItRestsAndTriesAgain() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy(answer: ProgramResult(output: programWAV([1])))
        let pocket = try speaker(models, spy, offline: true)
        await pocket.load()
        #expect(await pocket.state == .unavailable)
        await #expect(throws: PocketFailure.notReady("unavailable")) { try await pocket.speak("Hi.", voice: "eve") }
        for _ in 0..<200 where spy.waits.isEmpty { try await Task.sleep(nanoseconds: 5_000_000) }
        #expect(spy.waits.first == 60)
    }

    /// A model file that is not the pinned one is set aside and fetched fresh once; changed again, refused: never run.
    @Test func aChangedFileIsNeverRun() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy(answer: ProgramResult(output: programWAV([1])))
        let pocket = try speaker(models, spy, wrong: ["model.safetensors"])
        await pocket.load()
        #expect(await pocket.state == .refused)
        #expect(spy.runs.isEmpty)
        await #expect(throws: PocketFailure.notReady("refused")) { try await pocket.speak("Hi.", voice: "eve") }
    }

    /// A checked file that changes after the start is never run: that line is Kokoro's, and the files are checked
    /// again (here: fetched fresh, the right bytes).
    @Test func aFileChangedAfterItsCheckStopsTheRun() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy(answer: ProgramResult(output: programWAV([1])))
        let pocket = try speaker(models, spy)
        await pocket.load()
        try write("W", to: models.appendingPathComponent("pocket-tts/test/weights/model.safetensors"))
        await #expect(throws: PocketFailure.changed) { try await pocket.speak("Hi.", voice: "eve") }
        #expect(spy.runs.count == 1)  // the first start's only
        for _ in 0..<200 where await pocket.state != .ready { try await Task.sleep(nanoseconds: 5_000_000) }
        #expect(await pocket.state == .ready)
        #expect(spy.downloads.suffix(1) == ["pocket-tts/test/weights"])
        _ = try await pocket.speak("Hi.", voice: "eve")
    }

    /// The review of 26 Oct (R2-F3): each stamp comes from the bytes its check read, through one descriptor. The
    /// program swapped for other bytes, or removed, after its check (here: while the model is fetched) is never run,
    /// not even for its first run (it was stamped as checked, or left unstamped and run); and a checked file removed
    /// between the start and a line stops that run.
    @Test func aFileSwappedOrGoneAfterItsCheckIsNeverRun() async throws {
        for swap in [true, false] {
            let models = models()
            defer { try? FileManager.default.removeItem(at: models) }
            let spy = Spy(answer: ProgramResult(output: programWAV([1000])))
            let programs = try programs(models)
            let program = release.location(in: programs).appendingPathComponent("pocket-tts")
            let bytes = contents([])
            let pocket = PocketSpeaker(
                models: models, her: models, programs: programs, model: model, release: release, name: "eve",
                download: { pin, base in
                    if swap { try write("P", to: program) } else { try? FileManager.default.removeItem(at: program) }
                    for name in pin.sha256.keys { try write(bytes[name] ?? "", to: pin.location(in: base).appendingPathComponent(name)) }
                },
                run: { argv, environment, input, seconds, _ in spy.ran(argv, environment, input, seconds) },
                sleep: { _ in try? await Task.sleep(nanoseconds: 60_000_000_000) })
            await pocket.load()
            #expect(await pocket.state == .unavailable && spy.runs.isEmpty, "swapped: \(swap)")
            await #expect(throws: PocketFailure.notReady("unavailable")) { try await pocket.speak("Hi.", voice: "eve") }
            #expect(spy.runs.isEmpty)
        }
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy(answer: ProgramResult(output: programWAV([1000])))
        let pocket = try speaker(models, spy)
        await pocket.load()
        try FileManager.default.removeItem(at: models.appendingPathComponent("pocket-tts/test/eve/eve.safetensors"))
        await #expect(throws: PocketFailure.changed) { try await pocket.speak("Hi.", voice: "eve") }
        #expect(spy.runs.count == 1)  // the first start's only
    }

    /// The review of 26 Oct (R1-F2): the English speaker's check and fetch start before Whisper's load (on a first
    /// start a download and a compile of about 15 minutes) and Kokoro's, so "My voice is ready" (both ready) does not
    /// go out long before her Pocket voice; its first run is on Pocket's own queue, never the one they share.
    @Test func herPocketVoiceLoadsWhileWhisperLoads() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy(answer: ProgramResult(output: programWAV([1000])))
        let pocket = try speaker(models, spy)
        let order = Steps()
        await startEngines(pockets: [pocket], listening: {
            for _ in 0..<400 where spy.downloads.isEmpty { try? await Task.sleep(nanoseconds: 5_000_000) }
            order.add(spy.downloads.isEmpty ? "listening ready, nothing of Pocket fetched" : "listening ready, Pocket fetching")
        }, speaking: { order.add("speaking ready") })
        #expect(order.all == ["listening ready, Pocket fetching", "speaking ready"])
        for _ in 0..<400 where await pocket.state != .ready { try await Task.sleep(nanoseconds: 5_000_000) }
        #expect(await pocket.state == .ready)
    }

    /// Owner, 25 Oct ("the pocket language models we download in Estia only if they are requested right?"): a
    /// language's model (jane in French, German, Italian, Spanish, Portuguese or Dutch) is fetched only for the language
    /// in use. It is idle at every start, its files there or not (nothing checked, fetched or run); the first line asked
    /// in it starts its check, and its fetch when a file is missing (Supertonic-3 says that line); a fetch that fails is
    /// never tried again on its own: after the wait it is idle again, and only a line asked in it tries again.
    @Test func aLanguagesModelIsFetchedOnlyOnceALineIsAskedInIt() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy(answer: ProgramResult(output: programWAV([1000])))
        let pocket = try speaker(models, spy, askedOnly: true)
        #expect(await pocket.state == .idle)
        await pocket.load()
        #expect(await pocket.state == .idle && spy.downloads.isEmpty && spy.runs.isEmpty)
        await #expect(throws: PocketFailure.notReady("idle")) { try await pocket.speak("Hallo.", voice: "eve") }
        for _ in 0..<400 where await pocket.state != .ready { try await Task.sleep(nanoseconds: 5_000_000) }
        #expect(await pocket.state == .ready)
        #expect(spy.downloads == ["pocket-tts/test/weights", "pocket-tts/test/tokenizer", "pocket-tts/test/eve"])
        _ = try await pocket.speak("Hallo.", voice: "eve")
        #expect(spy.runs.map(\.input) == ["Ready.", "Hallo."])
        // a later start, its files there from that fetch: idle again, nothing checked or run until a line is asked
        let again = try speaker(models, spy, askedOnly: true)
        await again.load()
        #expect(await again.state == .idle && spy.downloads.count == 3 && spy.runs.count == 2)
        await #expect(throws: PocketFailure.notReady("idle")) { try await again.speak("Hallo.", voice: "eve") }
        for _ in 0..<400 where await again.state != .ready { try await Task.sleep(nanoseconds: 5_000_000) }
        #expect(await again.state == .ready && spy.downloads.count == 3)  // checked, nothing fetched again
        // offline: the ask's fetch fails (unavailable), then after the wait idle again, nothing fetched on its own
        let offline = try speaker(models.appendingPathComponent("fresh"), spy, offline: true, askedOnly: true, shortRest: true)
        await #expect(throws: PocketFailure.notReady("idle")) { try await offline.speak("Hallo.", voice: "eve") }
        for _ in 0..<400 where spy.waits.isEmpty { try await Task.sleep(nanoseconds: 5_000_000) }
        for _ in 0..<400 where await offline.state != .idle { try await Task.sleep(nanoseconds: 5_000_000) }
        #expect(await offline.state == .idle && spy.waits == [60] && spy.downloads.count == 4)
        try await Task.sleep(nanoseconds: 50_000_000)
        #expect(spy.downloads.count == 4 && spy.waits == [60])  // no try of its own
        await #expect(throws: PocketFailure.notReady("idle")) { try await offline.speak("Hallo.", voice: "eve") }
        for _ in 0..<400 where spy.waits.count < 2 { try await Task.sleep(nanoseconds: 5_000_000) }
        #expect(spy.downloads.count == 5 && spy.waits == [60, 300])  // the asked line tried again, the next wait longer
    }

    /// Pocket's runs are on the CPU, one at a time (`Engines.pocketQueue`). A speaker's first run ("Ready.") waits its
    /// turn there too (the review of 25 Oct: a language's, started by its first line, ran beside eve's lines), while its
    /// check and fetch never hold the queue.
    @Test func aFirstRunWaitsItsTurnOnTheQueue() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let runs = Overlaps(answer: ProgramResult(output: programWAV([1000])))
        let queue = Serial()
        func pocket(_ folder: String, askedOnly: Bool) throws -> PocketSpeaker {
            let base = models.appendingPathComponent(folder)
            return PocketSpeaker(
                models: base, her: base, programs: try programs(base), model: model, release: release,
                askedOnly: askedOnly, name: askedOnly ? "de: jane" : "eve",
                download: { pin, base in
                    for name in pin.sha256.keys { try write(contents([])[name] ?? "", to: pin.location(in: base).appendingPathComponent(name)) }
                },
                run: { _, _, _, _, _ in await runs.run() },
                firstRun: { try await queue.run($0) },  // as the service's speakers
                sleep: { _ in try? await Task.sleep(nanoseconds: 60_000_000_000) })
        }
        let eve = try pocket("en", askedOnly: false)
        await eve.load()
        let jane = try pocket("de", askedOnly: true)
        await #expect(throws: PocketFailure.notReady("idle")) { try await queue.run { try await jane.speak("Hallo.", voice: "eve") } }
        var lines = 0
        while await jane.state != .ready, lines < 400 {  // eve's lines keep the queue busy while jane loads
            _ = try await queue.run { try await eve.speak("Hi.", voice: "eve") }
            lines += 1
        }
        #expect(await jane.state == .ready && lines > 0)
        #expect(runs.most == 1, "\(runs.most) runs at once")
    }

    /// The log says when a failure is tried again (the review of 25 Oct: a language's said "tried again later", but it
    /// only turns idle after the wait): an English model on its own after the wait, a language's at the next line asked
    /// in it after the wait.
    @Test func theLogSaysWhenAFailureIsTriedAgain() {
        #expect(PocketSpeaker.retried(askedOnly: false) == "tried again later")
        #expect(PocketSpeaker.retried(askedOnly: true) == "tried again at the next line in it, after a wait")
    }

    /// Runs that fail are Kokoro's lines; three in a row rest it until its next try.
    @Test func threeFailedRunsInARowRestIt() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy(answer: ProgramResult(output: programWAV([1])))
        let pocket = try speaker(models, spy)
        await pocket.load()
        for (answer, failure) in [(ProgramResult(status: 1), PocketFailure.exit(1)),
                                  (ProgramResult(status: 9, signalled: true), .signal(9)),
                                  (ProgramResult(timedOut: true), .timeout)] {
            spy.answer = answer
            #expect(await pocket.state == .ready)
            await #expect(throws: failure) { try await pocket.speak("Hi.", voice: "eve") }
        }
        #expect(await pocket.state == .unavailable)
        // a success between failures starts the count again
        let other = try speaker(models, spy)
        spy.answer = ProgramResult(output: programWAV([1]))
        await other.load()
        spy.answer = ProgramResult(status: 1)
        await #expect(throws: PocketFailure.exit(1)) { try await other.speak("Hi.", voice: "eve") }
        spy.answer = ProgramResult(output: Data("not a wav".utf8))
        await #expect(throws: PocketWAV.Invalid.self) { try await other.speak("Hi.", voice: "eve") }
        spy.answer = ProgramResult(output: programWAV([1]))
        _ = try await other.speak("Hi.", voice: "eve")
        spy.answer = ProgramResult(tooLarge: true)
        await #expect(throws: PocketFailure.tooLarge) { try await other.speak("Hi.", voice: "eve") }
        #expect(await other.state == .ready)
    }

    /// The review of 28 Oct (calls-03): resting after failed runs left every line of a call to Kokoro for 1 to 30
    /// minutes, and an Apple bug can crash Kokoro and the voice service with the call. On a call each line tries Pocket
    /// again: a run that works makes it ready, one that fails leaves it resting (Kokoro says that line); a line off a
    /// call waits for the rest as before, and a speaker unavailable for anything but its runs (no program) never runs.
    @Test func onACallARestingPocketTriesEachLineAgain() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy(answer: ProgramResult(output: programWAV([1])))
        let pocket = try speaker(models, spy)
        await pocket.load()
        spy.answer = ProgramResult(status: 1)
        for _ in 0..<PocketTTS.failuresBeforeRest {
            await #expect(throws: PocketFailure.exit(1)) { try await pocket.speak("Hi.", voice: "eve") }
        }
        #expect(await pocket.state == .unavailable)
        let ran = spy.runs.count
        await #expect(throws: PocketFailure.notReady("unavailable")) { try await pocket.speak("Hi.", voice: "eve") }
        #expect(spy.runs.count == ran)  // off a call: resting, no run
        await #expect(throws: PocketFailure.exit(1)) { try await pocket.speak("Hi.", voice: "eve", call: true) }
        let resting = await pocket.state
        #expect(spy.runs.count == ran + 1 && resting == .unavailable)  // a call's line ran; still resting
        spy.answer = ProgramResult(output: programWAV([1]))
        _ = try await pocket.speak("Hi.", voice: "eve", call: true)
        #expect(await pocket.state == .ready)  // ready again, for every line
        _ = try await pocket.speak("Hi.", voice: "eve")

        let gone = try speaker(models, spy, programs: try programs(models.appendingPathComponent("gone"), missing: true))
        await gone.load()
        #expect(await gone.state == .unavailable)
        let before = spy.runs.count
        await #expect(throws: PocketFailure.notReady("unavailable")) { try await gone.speak("Hi.", voice: "eve", call: true) }
        #expect(spy.runs.count == before)  // no program: a call's line never runs
    }

    /// The final review of 0.9.8 (calls-03-pocket-retry-latency): resting after runs that ran past their time limit (a
    /// busy Mac), each call line waited out that limit (2 s and 10 ms a character, up to 17 s) before Kokoro said it.
    /// Then a call's line tries it at most once a minute (`PocketTTS.callRetrySeconds`), its stand-in saying the others
    /// at once; after a run that failed fast, every call line still tries it.
    @Test func onACallAPocketRestingAfterLateRunsTriesAtMostOnceAMinute() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let clock = TestClock()
        let spy = Spy(answer: ProgramResult(output: programWAV([1])))
        let pocket = try speaker(models, spy, clock: clock)
        await pocket.load()
        spy.answer = ProgramResult(signalled: true, timedOut: true)
        for _ in 0..<PocketTTS.failuresBeforeRest {
            await #expect(throws: PocketFailure.timeout) { try await pocket.speak("Hi.", voice: "eve") }
        }
        #expect(await pocket.state == .unavailable)
        let ran = spy.runs.count
        await #expect(throws: PocketFailure.notReady("unavailable")) { try await pocket.speak("Hi.", voice: "eve", call: true) }
        #expect(spy.runs.count == ran)  // its last run was late a moment ago: the stand-in at once
        clock.pass(PocketTTS.callRetrySeconds)
        await #expect(throws: PocketFailure.timeout) { try await pocket.speak("Hi.", voice: "eve", call: true) }
        #expect(spy.runs.count == ran + 1)  // a minute on: one call line tries it
        clock.pass(PocketTTS.callRetrySeconds / 2)
        await #expect(throws: PocketFailure.notReady("unavailable")) { try await pocket.speak("Hi.", voice: "eve", call: true) }
        #expect(spy.runs.count == ran + 1)  // late again: the next minute's line, not this one
        clock.pass(PocketTTS.callRetrySeconds / 2)
        spy.answer = ProgramResult(output: programWAV([1]))
        _ = try await pocket.speak("Hi.", voice: "eve", call: true)
        #expect(await pocket.state == .ready && spy.runs.count == ran + 2)  // it ran, and is ready again
    }

    /// The second deep scan of 0.9.8 (swift-1): a first run ("Ready.", the start's or a retry's) past its time limit
    /// left it resting with no time of that late run, so the next call line ran it to its full limit again (4 to 5 s of
    /// silence on a 16 GB Mac) before Kokoro said it. A late first run waits a minute too, as a late line does.
    @Test func aLateFirstRunWaitsAMinuteBeforeACallLineTriesIt() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let clock = TestClock()
        let spy = Spy(answer: ProgramResult(signalled: true, timedOut: true))
        let pocket = try speaker(models, spy, clock: clock)
        await pocket.load()
        #expect(await pocket.state == .unavailable && spy.runs.map(\.input) == ["Ready."])  // its first run, late
        await #expect(throws: PocketFailure.notReady("unavailable")) { try await pocket.speak("Hi.", voice: "eve", call: true) }
        #expect(spy.runs.count == 1)  // within the minute: the stand-in at once, no run
        clock.pass(PocketTTS.callRetrySeconds)
        spy.answer = ProgramResult(output: programWAV([1]))
        _ = try await pocket.speak("Hi.", voice: "eve", call: true)
        #expect(await pocket.state == .ready && spy.runs.count == 2)  // a minute on: a call line tries it, and it works
    }

    /// The second deep scan of 0.9.8 (swift-2): a call's line that made a resting Pocket ready left the rest's wait
    /// asleep (1, 5 or 30 minutes); three failed runs after that found a rest "under way", so none began, and the next
    /// try came only when the old wait ended: up to 30 minutes of Kokoro for voice notes, not 1. Each rest waits on its
    /// own, a minute first, and an older wait ends without a try.
    @Test func aRestAfterACallRevivedItStartsItsOwnWait() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let gates = Gates()
        let spy = Spy(answer: ProgramResult(output: programWAV([1])))
        let pocket = try speaker(models, spy, sleep: { await gates.sleep($0) })
        await pocket.load()
        spy.answer = ProgramResult(status: 1)
        for _ in 0..<PocketTTS.failuresBeforeRest {
            await #expect(throws: PocketFailure.exit(1)) { try await pocket.speak("Hi.", voice: "eve") }
        }
        #expect(await eventually { gates.waits.count == 1 })  // the rest's first wait
        spy.answer = ProgramResult(output: programWAV([1]))
        _ = try await pocket.speak("Hi.", voice: "eve", call: true)  // a call's line runs: ready, the wait still asleep
        #expect(await pocket.state == .ready)
        spy.answer = ProgramResult(status: 1)
        for _ in 0..<PocketTTS.failuresBeforeRest {
            await #expect(throws: PocketFailure.exit(1)) { try await pocket.speak("Hi.", voice: "eve") }
        }
        #expect(await eventually { gates.waits.count == 2 })  // a rest of its own
        #expect(gates.waits == [60, 60])  // its first wait a minute, never the rest of the old one
        let ran = spy.runs.count
        spy.answer = ProgramResult(output: programWAV([1]))
        gates.wake()  // the old wait ends: no try of its own
        try await Task.sleep(nanoseconds: 50_000_000)
        let resting = await pocket.state
        #expect(spy.runs.count == ran && resting == .unavailable)
        gates.wake()  // the new one's: it tries, and works
        #expect(await eventually { await pocket.state == .ready })
        #expect(spy.runs.count == ran + 1)
    }
}

@Suite struct PocketPrograms {
    private func folder() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("pocket-run-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    /// A run gets exactly its environment and stdin; its stdout is read whole.
    @Test func aProgramGetsOnlyItsEnvironmentAndInput() async throws {
        let echoed = try await runProgram(["/bin/cat"], environment: ["PATH": "/usr/bin:/bin"], input: Data("hello".utf8),
                                          seconds: 10, maxOutput: 1 << 20)
        #expect(echoed == ProgramResult(output: Data("hello".utf8)))
        let env = try await runProgram(["/usr/bin/env"], environment: ["PATH": "/usr/bin:/bin", "A": "1"], input: Data(),
                                       seconds: 10, maxOutput: 1 << 20)
        #expect(Set(String(decoding: env.output, as: UTF8.self).split(separator: "\n")) == ["PATH=/usr/bin:/bin", "A=1"])
        let failed = try await runProgram(["/bin/sh", "-c", "exit 3"], environment: [:], input: Data(), seconds: 10, maxOutput: 10)
        #expect(failed.status == 3 && !failed.signalled)
    }

    /// Too long or too much: stopped at once.
    @Test func aProgramIsStoppedPastItsTimeOrItsCap() async throws {
        let start = Date()
        let slow = try await runProgram(["/bin/sleep", "20"], environment: [:], input: Data(), seconds: 0.3, maxOutput: 10)
        #expect(slow.timedOut && slow.signalled && Date().timeIntervalSince(start) < 5)
        let loud = try await runProgram(["/usr/bin/yes"], environment: [:], input: Data(), seconds: 10, maxOutput: 1 << 16)
        #expect(loud.tooLarge && loud.signalled && !loud.timedOut)
        // a program that never reads its stdin: no SIGPIPE for the service
        let deaf = try await runProgram(["/usr/bin/true"], environment: [:], input: Data(count: 1 << 20), seconds: 10, maxOutput: 10)
        #expect(deaf.status == 0)
    }

    /// The sandbox itself (macOS's sandbox-exec, as a run uses it): her home unreadable but Pocket's folder and the
    /// program's (inside it only in a checkout run as the owner), nothing written, no network, no other program
    /// started.
    @Test func theSandboxClosesHerHomeTheDiskTheNetworkAndOtherPrograms() async throws {
        let her = try folder()
        defer { try? FileManager.default.removeItem(at: her) }
        let pocket = her.appendingPathComponent("pocket-tts")
        let program = her.appendingPathComponent("checkout/.build/release/pocket-tts")
        try write("open", to: pocket.appendingPathComponent("inside"))
        try write("program", to: program.appendingPathComponent("pocket-tts"))
        try write("secret", to: her.appendingPathComponent("config.toml"))
        // the program the sandbox may start: the one asked (Pocket's, in a run)
        func sandboxed(_ argv: [String]) async throws -> ProgramResult {
            let real = (PocketRun.real(her).path, PocketRun.real(pocket).path, PocketRun.real(program).path)
            return try await runProgram(
                [PocketRun.sandboxExec, "-p", PocketRun.profile, "-D", "HER=\(real.0)", "-D", "POCKET=\(real.1)",
                 "-D", "PROGRAM=\(real.2)", "-D", "PROGRAM_FILE=\(PocketRun.real(URL(fileURLWithPath: argv[0])).path)"] + argv,
                environment: ["PATH": "/usr/bin:/bin"], input: Data(), seconds: 20, maxOutput: 1 << 16)
        }
        // the review of 25 Oct: a program that replaces itself with another (exec, no fork) starts nothing either
        let replaced = try await sandboxed(["/bin/bash", "-c", "exec /bin/echo started"])
        #expect(replaced.output.isEmpty && replaced.status != 0, "\(String(decoding: replaced.output, as: UTF8.self))")
        let alone = try await sandboxed(["/bin/bash", "-c", "echo alone"])  // the asked program itself runs
        #expect(String(decoding: alone.output, as: UTF8.self) == "alone\n")
        #expect(try await sandboxed(["/bin/cat", pocket.appendingPathComponent("inside").path]).output == Data("open".utf8))
        #expect(try await sandboxed(["/bin/cat", program.appendingPathComponent("pocket-tts").path]).output
            == Data("program".utf8))
        #expect(try await sandboxed(["/bin/ls", her.appendingPathComponent("checkout").path]).status != 0)
        let closed = try await sandboxed(["/bin/cat", her.appendingPathComponent("config.toml").path])
        #expect(closed.status != 0 && closed.output.isEmpty)
        #expect(try await sandboxed(["/usr/bin/touch", pocket.appendingPathComponent("new").path]).status != 0)
        #expect(!FileManager.default.fileExists(atPath: pocket.appendingPathComponent("new").path))
        let forked = try await sandboxed(["/bin/sh", "-c", "/bin/echo started; /bin/echo again"])
        #expect(!String(decoding: forked.output, as: UTF8.self).contains("started"))
        // the review of 26 Oct (R2-F1): no signal to her other programs (the bridge, the voice service). The child is
        // the test's own, in the run's process group, as the voice service's child is (a rule for other groups only
        // let such a kill through): the kill fails and the child lives on
        let child = Process()
        child.executableURL = URL(fileURLWithPath: "/bin/sleep")
        child.arguments = ["30"]
        try child.run()
        defer { child.terminate() }
        let killed = try await sandboxed(["/bin/kill", "-TERM", "\(child.processIdentifier)"])
        try await Task.sleep(nanoseconds: 200_000_000)
        #expect(killed.status != 0 && child.isRunning && kill(child.processIdentifier, 0) == 0)
        // and no service of the Mac's reached by name (mach-lookup): notifyd answers the same question only outside
        let notify = ["/usr/bin/notifyutil", "-g", "com.apple.system.timezone"]
        let outside = try await runProgram(notify, environment: ["PATH": "/usr/bin:/bin"], input: Data(), seconds: 20,
                                           maxOutput: 1 << 16)
        #expect(String(decoding: outside.output, as: UTF8.self).hasPrefix("com.apple.system.timezone "))
        #expect(String(decoding: try await sandboxed(notify).output, as: UTF8.self).contains("Failed"))
        // nor a preference written through cfprefsd (a path domain: its plist beside it), which writes one outside
        let preferences = try folder()
        defer { try? FileManager.default.removeItem(at: preferences) }
        _ = try await sandboxed(["/usr/bin/defaults", "write", preferences.appendingPathComponent("inside").path, "k", "v"])
        #expect(!FileManager.default.fileExists(atPath: preferences.appendingPathComponent("inside.plist").path))
        _ = try await runProgram(["/usr/bin/defaults", "write", preferences.appendingPathComponent("outside").path, "k", "v"],
                                 environment: ["PATH": "/usr/bin:/bin"], input: Data(), seconds: 20, maxOutput: 1 << 16)
        #expect(FileManager.default.fileExists(atPath: preferences.appendingPathComponent("outside.plist").path))
        // a listener of the test's own: reached without the sandbox, never with it
        let (socket, port) = try listener()
        defer { close(socket) }
        let reach = ["/usr/bin/nc", "-z", "-w", "2", "127.0.0.1", "\(port)"]
        #expect(try await runProgram(reach, environment: ["PATH": "/usr/bin:/bin"], input: Data(), seconds: 20, maxOutput: 1 << 16).status == 0)
        #expect(try await sandboxed(reach).status != 0)
    }

    /// A program reached through a link inside her home (a checkout's `.build/release`, SwiftPM's link) runs: the
    /// sandbox would refuse to read the link, so a run starts the program by its real path (24 Oct: exit 71). The
    /// program is a copy of /usr/bin/true (a script would need its interpreter started, which the sandbox refuses, and
    /// most of macOS's own programs are stopped when copied): exit 0 only when it ran.
    @Test func aProgramBehindALinkInHerHomeRuns() async throws {
        let her = try folder()
        defer { try? FileManager.default.removeItem(at: her) }
        let build = her.appendingPathComponent("checkout/.build")
        let program = build.appendingPathComponent("arm64-apple-macosx/release/pocket-tts/pocket-tts")
        try FileManager.default.createDirectory(at: program.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: URL(fileURLWithPath: "/usr/bin/true"), to: program)
        try FileManager.default.createSymbolicLink(at: build.appendingPathComponent("release"),
                                                   withDestinationURL: build.appendingPathComponent("arm64-apple-macosx/release"))
        let pocket = her.appendingPathComponent("pocket-tts")
        try FileManager.default.createDirectory(at: pocket, withIntermediateDirectories: true)
        let argv = PocketRun.arguments(
            program: build.appendingPathComponent("release/pocket-tts/pocket-tts"), config: pocket.appendingPathComponent("c.yaml"),
            voice: pocket.appendingPathComponent("eve.safetensors"), pocket: pocket, her: her)
        let ran = try await runProgram(argv, environment: PocketRun.environment(pocket: pocket), input: Data(), seconds: 20,
                                       maxOutput: 1 << 16)
        #expect(ran.status == 0 && !ran.signalled, "exit \(ran.status)")
    }

    /// The release from its tarball (`voice-service --fetch-pocket`, beside the voice service): checked by the
    /// tarball's sha256, its two files taken out and checked by theirs; a tarball with other bytes is never unpacked,
    /// and nothing is left in place.
    @Test func theReleaseComesFromItsCheckedTarball() async throws {
        let root = try folder()
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("src/p")
        try write("p", to: source.appendingPathComponent("pocket-tts"))
        try write("l", to: source.appendingPathComponent("LICENSE"))
        try write("x", to: source.appendingPathComponent("extra"))
        let tarball = root.appendingPathComponent("p.tar.gz")
        let made = try await runProgram(["/usr/bin/tar", "-c", "-z", "-f", tarball.path, "-C", root.appendingPathComponent("src").path, "p"],
                                        environment: ["PATH": "/usr/bin:/bin"], input: Data(), seconds: 20, maxOutput: 1 << 16)
        #expect(made.status == 0)
        let release = PocketRelease(
            url: URL(string: "https://example.com/p.tar.gz")!, tarballSHA256: try sha256Hex(file: tarball),
            maxTarballBytes: 1 << 20, top: "p", path: "pocket-tts",
            sha256: ["pocket-tts": sha("p"), "LICENSE": sha("l")])
        let programs = root.appendingPathComponent("release/tools/voice/.build/release")
        try FileManager.default.createDirectory(at: programs, withIntermediateDirectories: true)
        try write("binary", to: programs.appendingPathComponent("voice-service"))
        let fetch: FileFetch = { url, destination, cap in
            #expect(url == release.url && cap == 1 << 20)
            try FileManager.default.copyItem(at: tarball, to: destination)
        }
        #expect(await fetchPocket(into: programs, release: release, fetch: fetch))
        #expect(release.check(in: programs) == .ok)
        let modes = try ["pocket-tts", "pocket-tts/pocket-tts", "pocket-tts/LICENSE"].map {
            try FileManager.default.attributesOfItem(atPath: programs.appendingPathComponent($0).path)[.posixPermissions] as? Int
        }
        #expect(modes == [0o755, 0o755, 0o644])
        #expect(Set(try FileManager.default.contentsOfDirectory(atPath: programs.path)) == ["pocket-tts", "voice-service"])
        let other = PocketRelease(url: release.url, tarballSHA256: String(repeating: "f", count: 64), maxTarballBytes: 1 << 20,
                                  top: "p", path: "pocket-tts", sha256: release.sha256)
        #expect(await !fetchPocket(into: programs, release: other, fetch: fetch))
        #expect(other.check(in: programs) == .missing)  // not even the earlier fetch's copy is left
        let wrongFiles = PocketRelease(url: release.url, tarballSHA256: release.tarballSHA256, maxTarballBytes: 1 << 20,
                                       top: "p", path: "pocket-tts", sha256: ["pocket-tts": sha("P"), "LICENSE": sha("l")])
        #expect(await !fetchPocket(into: programs, release: wrongFiles, fetch: fetch))
        #expect(try FileManager.default.contentsOfDirectory(atPath: programs.path) == ["voice-service"])  // no staging
    }
}

/// A TCP listener on 127.0.0.1 at a port the system picks: the socket and its port.
private func listener() throws -> (Int32, UInt16) {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    var address = sockaddr_in()
    address.sin_family = sa_family_t(AF_INET)
    address.sin_addr.s_addr = inet_addr("127.0.0.1")
    address.sin_port = 0
    var size = socklen_t(MemoryLayout<sockaddr_in>.size)
    let bound = withUnsafeMutablePointer(to: &address) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, size) == 0 && listen(fd, 4) == 0 && getsockname(fd, $0, &size) == 0 }
    }
    guard fd >= 0, bound else { throw POSIXError(.EADDRNOTAVAIL) }
    return (fd, UInt16(bigEndian: address.sin_port))
}

/// The real program from its pinned files, in every voice of both English models (each Mac speaks one, by its Gemma:
/// owner, 26 Oct) and in jane in each of the six languages (online: about 8 MB from GitHub and, from Hugging Face,
/// about 130 MB for the 18M's jane, eve, alba and michael, 250 MB for the 100M's with vera and 227 MB a language, or the
/// pinned model files from a folder you name, checked; the runs themselves are offline in their sandbox):
/// `VOICE_POCKET_TESTS=1 [VOICE_POCKET_MODELS=<folder>] swift test --filter RealPocket`. Run it after any Pocket pin
/// change.
@Suite(.enabled(if: ProcessInfo.processInfo.environment["VOICE_POCKET_TESTS"] == "1"))
struct RealPocket {
    @Test func everyPocketVoiceSpeaksFromThePinnedFiles() async throws {
        let environment = ProcessInfo.processInfo.environment
        let models = environment["VOICE_POCKET_MODELS"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.temporaryDirectory.appendingPathComponent("pocket-real-\(UUID().uuidString)")
        let programs = FileManager.default.temporaryDirectory.appendingPathComponent("pocket-program-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: programs) }
        #expect(await fetchPocket(into: programs))
        for tier in [PocketVoices(english: .english18M), PocketVoices(english: .english)] {  // a 16 GB Mac's, a 32 GB one's
            let speech = try #require(tier.models.first)
            let pocket = PocketSpeaker(models: models, her: models.deletingLastPathComponent(), programs: programs,
                                       model: speech.model, name: tier.name(of: speech.model),
                                       resources: PocketTTS.resources(beside: Bundle.module.bundleURL.deletingLastPathComponent()))
            await pocket.load()  // English's: checked and fetched at the start
            #expect(await pocket.state == .ready, "\(speech.voice)")
            let voices = tier.speakers  // every voice of the model's
            for voice in voices {
                for (text, least, most) in [("Yes?", 0.5, 3.0), ("Shall I remind you at seven thirty?", 1.5, 5.0)] {
                    let start = Date()
                    let wav = try await pocket.speak(PocketText.spoken(text), voice: voice.speaks)
                    let seconds = Double(wav.count - 44) / 48_000
                    #expect(seconds > least && seconds < most, "\(voice.voice), \(text): \(seconds) s")
                    #expect(Date().timeIntervalSince(start) < 2, "\(voice.voice), \(text)")
                }
            }

            // 25 Oct: a stretch of about 90 words (eve) or 100 (jane) with no comma ran away to the program's budget
            // (0.45 s a word, its end garbled) with nothing to say so; `PocketPace` puts commas in. Without it this
            // 116-word run-on takes 50 s or more in either voice; with it about 0.26-0.28 s a word.
            let text = """
                tomorrow you have the dentist at nine fifteen and then the call with the office and after that your sister \
                is coming with the paperwork for the apartment and if you want to finish the shopping first it would be best \
                to leave before eight and take the bus because the train is slow at that hour and the weather looks dry but \
                cloudy until the afternoon when a little rain may come in from the west and stay until the evening so I would \
                take a light jacket and an umbrella and maybe the blue scarf and I can remind you tonight if you like or \
                tomorrow morning at seven if that suits you better
                """
            let words = Double(text.split(separator: " ").count)
            for voice in voices {
                let spoken = PocketText.spoken(text)
                #expect(spoken.contains(","), "the pass adds commas")
                let wav = try await pocket.speak(spoken, voice: voice.speaks)
                let seconds = Double(wav.count - 44) / 48_000
                #expect(seconds > 0.15 * words && seconds < 0.36 * words, "\(voice.voice): \(seconds) s for \(words) words")
            }
        }
    }

    /// The stop check for heart (the review of 27 Oct, CALLS-3: a runaway depends on the voice's state, and heart's is a
    /// new clone; measured that day on the 100M through the voice service on port 8151, heart and jane in turn, 3 runs,
    /// every line Pocket's and heard to its end; the 18M's heart, 29 Oct, not run yet: the owner tests it later): the
    /// 206-word passage and the number list in heart on either model, each one whole clip of under 0.35 s a word Pocket
    /// reads (a runaway takes 0.45 s a word or more, its end garbled) and over 0.15 (a clip cut short).
    @Test func heartSaysTheLongPassageAndTheNumberListWhole() async throws {
        let environment = ProcessInfo.processInfo.environment
        let models = environment["VOICE_POCKET_MODELS"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.temporaryDirectory.appendingPathComponent("pocket-real-\(UUID().uuidString)")
        let programs = FileManager.default.temporaryDirectory.appendingPathComponent("pocket-program-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: programs) }
        #expect(await fetchPocket(into: programs))
        for tier in [PocketVoices(english: .english), PocketVoices(english: .english18M)] {
            try await heartSaysItWhole(tier, models: models, programs: programs)
        }
    }

    private func heartSaysItWhole(_ tier: PocketVoices, models: URL, programs: URL) async throws {
        let pocket = PocketSpeaker(models: models, her: models.deletingLastPathComponent(), programs: programs,
                                   model: tier.english, name: tier.name(of: tier.english),
                                   resources: PocketTTS.resources(beside: Bundle.module.bundleURL.deletingLastPathComponent()))
        await pocket.load()
        #expect(await pocket.state == .ready, "\(tier.size)")
        let passage = """
            Here is the whole week. On Monday you have the dentist at nine fifteen and a call with the office at ten thirty, \
            and in the evening dinner with Alex at eight. On Tuesday the plumber is coming between two and four, so you \
            should be at home, and the weather looks cloudy but dry. On Wednesday there is nothing planned, which is a good \
            day for the paperwork for the apartment, and I can remind you in the morning if you like. On Thursday it should \
            rain in the evening, so take an umbrella to the concert, which starts at seven thirty, and the doors open at \
            seven. On Friday your sister arrives on the afternoon train, and you wanted to buy flowers and fresh bread before \
            she comes, so I would leave the house before four. On Saturday you planned the long walk by the river, and the \
            forecast says sunny with around eighteen degrees, which is perfect. On Sunday the pharmacy is closed, so if you \
            need anything from there, it is best to get it on Friday or Saturday. The electricity bill is due on the \
            twentieth, and I can remind you the day before, if you would like that as well.
            """
        let numbers = """
            The pharmacy is at 12 Main Street, the dentist is at 48 Harbour Road, the office is at 7 Station Square, and your \
            appointments are at 9:15, 10:30, 13:45 and 16:20 on the 3rd, 14th, 21st and 28th, with the reference numbers \
            4021, 5873 and 9160.
            """
        #expect(passage.split(separator: " ").count == 206)
        for text in [passage, numbers] {
            let spoken = PocketText.spoken(text)
            let words = Double(spoken.split(whereSeparator: \.isWhitespace).count)
            let wav = try await pocket.speak(spoken, voice: "heart")
            let seconds = Double(wav.count - 44) / 48_000
            #expect(seconds > 0.15 * words && seconds < 0.35 * words, "heart \(tier.size): \(seconds) s for \(words) words")
        }
    }

    /// jane in Spanish, Italian, Portuguese, Dutch, German and French (owner, 25 Oct), each its language's full model:
    /// idle until a line is asked in it, then its files fetched and checked; a short and a long line in its own words,
    /// and in each a 120-word stretch of long words with no comma, which the guard cuts at the language's limit
    /// (Portuguese's 150, the others' 200): spoken whole, so its clip is about as long as its words (25 Oct, through
    /// the voice service on port 8151, Whisper heard the last 8 words of each; without the guard the clip came out
    /// short, or long and garbled).
    @Test func janeSpeaksEachLanguageFromItsPinnedFiles() async throws {
        let environment = ProcessInfo.processInfo.environment
        let models = environment["VOICE_POCKET_MODELS"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.temporaryDirectory.appendingPathComponent("pocket-real-\(UUID().uuidString)")
        let programs = FileManager.default.temporaryDirectory.appendingPathComponent("pocket-program-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: programs) }
        #expect(await fetchPocket(into: programs))
        let lines: [String: (short: String, long: String)] = [
            "es": ("Un momento.", "Tu cita con el dentista es mañana a las 9:15, y debería hacer sol, unos 18 grados."),
            "it": ("Un momento.", "L'appuntamento dal dentista è domani alle 9:15, e dovrebbe esserci il sole, circa 18 gradi."),
            "pt": ("Um momento.", "A tua consulta no dentista é amanhã às 9:15, e deve estar sol, à volta de 18 graus."),
            "nl": ("Een moment.", "Je afspraak bij de tandarts is morgen om 9:15, en het wordt zonnig, rond de 18 graden."),
            "de": ("Einen Moment.", "Dein Zahnarzttermin ist morgen um 9:15 Uhr, und es soll sonnig werden, etwa 18 Grad."),
            "fr": ("Un instant.", "Ton rendez-vous chez le dentiste est demain à 9:15, et il devrait faire beau, environ 18 degrés."),
        ]
        let runOn: [String: String] = [
            "es": "mañana temprano viene la vecina con la enorme caja de mudanza y trae la documentación del contrato para la entrega del apartamento",
            "it": "domani mattina passa la vicina con il grande scatolone del trasloco e porta la documentazione del contratto per la consegna dell'appartamento",
            "pt": "amanhã de manhã passa a vizinha com a enorme caixa da mudança e traz a documentação do contrato para a entrega do apartamento",
            "nl": "morgen komt de buurvrouw langs met het grote pakket en brengt de papieren voor het appartement mee",
            "de": "morgen früh kommt die Nachbarin mit dem großen Paket vorbei und bringt die Unterlagen für die Wohnung mit",
            "fr": "demain matin la voisine passe avec l'énorme carton de déménagement et apporte la documentation du contrat pour la remise de l'appartement",
        ]
        for speech in PocketTTS.languages {
            let pocket = PocketSpeaker(models: models, her: models.deletingLastPathComponent(), programs: programs,
                                       model: speech.model, askedOnly: true,
                                       name: PocketVoices(english: .english).name(of: speech.model))
            await pocket.load()
            #expect(await pocket.state == .idle, "\(speech.language)")
            await #expect(throws: PocketFailure.notReady("idle")) { try await pocket.speak("Hola.", voice: speech.speaks) }
            for _ in 0..<1200 where await pocket.state == .loading { try await Task.sleep(nanoseconds: 100_000_000) }
            #expect(await pocket.state == .ready, "\(speech.language)")
            let (short, long) = try #require(lines[speech.language])
            for (text, least, most) in [(short, 0.4, 3.0), (long, 3.0, 10.0)] {
                let start = Date()
                let wav = try await pocket.speak(PocketText.spoken(text, language: speech.language), voice: speech.speaks)
                let seconds = Double(wav.count - 44) / 48_000
                #expect(seconds > least && seconds < most, "\(speech.language), \(text): \(seconds) s")
                #expect(Date().timeIntervalSince(start) < 2, "\(speech.language), \(text)")
            }
            let phrase = try #require(runOn[speech.language])
            let text = Array(repeating: phrase, count: 120 / phrase.split(separator: " ").count + 1).joined(separator: " ")
                .split(separator: " ").prefix(120).joined(separator: " ")
            let spoken = PocketText.spoken(text, language: speech.language)
            #expect(spoken.contains(","), "the pass adds commas")
            let wav = try await pocket.speak(spoken, voice: speech.speaks)
            let seconds = Double(wav.count - 44) / 48_000  // 0.33 s a word in French, 0.39-0.45 in the others
            #expect(seconds > 0.25 * 120 && seconds < 0.6 * 120, "\(speech.language): \(seconds) s for 120 words")
        }
    }
}

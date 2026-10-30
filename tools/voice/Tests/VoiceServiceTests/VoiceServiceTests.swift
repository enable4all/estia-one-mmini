import CoreML
import FluidAudio
import Foundation
import Network
import Testing
import VoiceCore
import WhisperKit

@testable import voice_service

@Suite struct WhisperSettings {
    @Test func theNamedLanguageIsForcedNeverDetected() {
        for code in ["en", "nl", "ja"] {
            let options = whisperOptions(language: code)
            #expect(options.language == code)
            // WhisperKit forces the language token only with the prefill prompt; without it, it detects
            #expect(options.usePrefillPrompt && !options.detectLanguage)
            #expect(options.task == .transcribe)
            #expect(options.promptTokens == nil)  // no hint: no prompt, as before
        }
    }

    @Test func herNameIsWhispersPromptOnlyWhenGiven() {
        let options = whisperOptions(language: "en", prompt: [400, 401])
        #expect(options.promptTokens == [400, 401] && options.usePrefillPrompt && options.language == "en")
        #expect(whisperOptions(language: "en", prompt: []).promptTokens == nil)
    }

    @Test func aClipOfOneSecondOrLessIsDecodedToo() {
        // WhisperKit 1.1.1 stops a window's seek windowClipTime (1 s by default) before the audio's end, so it decodes
        // nothing in a clip of 1 s or less: a call's "yes", "stop" or her name alone came back as no words (live,
        // 23 Oct: four turns of 0.7-0.8 s in standby). Our audio is at most one window (30 s; longer audio is cut at
        // pauses into windows by the VAD chunker, which keeps its own 1 s), so nothing is clipped from its end
        #expect(whisperOptions(language: "en").windowClipTime == 0)
        #expect(whisperOptions(language: "nl", prompt: [400]).windowClipTime == 0)
    }

    @Test func theWarmUpDecodesAsACallTurnDoes() {
        // review 0.9.7 (C20): the warm-up's second of silence went to WhisperKit's default options, which decode
        // nothing in a clip of 1 s or less, so it may have run no prediction and left the first turn the first use
        let options = warmUpOptions()
        #expect(options.windowClipTime == 0 && options.language == "en")
        #expect(options.usePrefillPrompt && !options.detectLanguage && options.promptTokens == nil)
    }

    @Test func aHintIsEncodedAfterASpaceWithoutSpecialTokens() {
        // as WhisperKit's own CLI encodes a prompt (ArgmaxCLI/TranscribeCLI.swift, 1.1.1)
        var asked: [String] = []
        let tokens = hintTokens("Estia", encode: { asked.append($0); return [50258, 1, 2] }, specialTokenBegin: 50257)
        #expect(tokens == [1, 2] && asked == [" Estia"])
        #expect(hintTokens("", encode: { _ in [1] }, specialTokenBegin: 50257) == [])
    }
}

/// Review C5: call audio stays in memory. Every call turn is a 16 kHz mono 16-bit WAV (the bridge's pcm48_to_wav16k)
/// and reaches Whisper as samples; any other audio (a voice note, a shared file) goes through a private file that
/// lives only for its transcription.
@Suite struct CallAudioInMemory {
    private func folder() throws -> URL {
        let temp = FileManager.default.temporaryDirectory.appendingPathComponent("voice-tmp-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: temp, withIntermediateDirectories: true)
        return temp
    }

    @Test func aCallTurnReachesWhisperWithoutAFile() async throws {
        let temp = try folder()
        defer { try? FileManager.default.removeItem(at: temp) }
        let clip = pcm16WAV([Float](repeating: 0.25, count: 8_000), sampleRate: 16_000)
        var heard: [Float] = []
        var onDisk = ["not read"]
        let (_, duration) = try await transcribeClip(clip, kind: "wav", temp: temp, samples: { samples in
            heard = samples
            onDisk = try FileManager.default.contentsOfDirectory(atPath: temp.path)
            return []
        }, file: { _ in
            Issue.record("a call turn went to a file")
            return []
        })
        #expect(heard == [Float](repeating: 8_192 / 32_768, count: 8_000) && duration == 0.5)
        #expect(onDisk == [])  // nothing on disk while Whisper reads it
    }

    @Test func otherAudioIsReadFromAPrivateFileGoneAfterwards() async throws {
        let temp = try folder()
        defer { try? FileManager.default.removeItem(at: temp) }
        let note = pcm16WAV([Float](repeating: 0.25, count: 24_000), sampleRate: 24_000)  // not 16 kHz
        var mode: Int?
        var path = ""
        let (_, duration) = try await transcribeClip(note, kind: "wav", temp: temp, samples: { _ in
            Issue.record("other audio was read in memory")
            return []
        }, file: { file in
            path = file
            mode = try FileManager.default.attributesOfItem(atPath: file)[.posixPermissions] as? Int
            return []
        })
        #expect(path.hasPrefix(temp.path) && path.hasSuffix(".wav") && mode == 0o600 && duration == 1)
        #expect(try FileManager.default.contentsOfDirectory(atPath: temp.path) == [])
    }

    @Test func aTooLongClipIsRefusedEitherWay() async throws {
        let temp = try folder()
        defer { try? FileManager.default.removeItem(at: temp) }
        for rate in [16_000, 24_000] {
            let long = pcm16WAV([Float](repeating: 0, count: rate * 181), sampleRate: rate)
            await #expect(throws: VoiceError.tooLong) {
                _ = try await transcribeClip(long, kind: "wav", temp: temp, samples: { _ in [] }, file: { _ in [] })
            }
        }
        #expect(try FileManager.default.contentsOfDirectory(atPath: temp.path) == [])
    }

    @Test func whisperGetsTheSamplesItWouldReadFromTheFile() throws {
        let temp = try folder()
        defer { try? FileManager.default.removeItem(at: temp) }
        let clip = pcm16WAV((0..<16_000).map { Float(sin(Double($0) / 7)) * 0.8 }, sampleRate: 16_000)
        let file = temp.appendingPathComponent("clip.wav")
        try clip.write(to: file)
        #expect(try pcm16kMonoSamples(clip) == AudioProcessor.loadAudioAsFloatArray(fromPath: file.path))
    }
}

@Suite struct WhisperStart {
    private let models = URL(fileURLWithPath: "/models")

    @Test func whisperLoadsOnlyFromItsCheckedFoldersAndNeverDownloads() {
        // a model folder and no download: WhisperKit neither lists the repository nor fetches a file at a start
        let config = whisperConfig(models: models)
        #expect(config.modelFolder == WhisperModel.files.location(in: models).path)
        #expect(config.download == false && config.load == true)
        #expect(config.tokenizerFolder == models)
        #expect(config.model == nil && config.modelRepo == nil && config.downloadBase == nil)
    }

    @Test func thePinnedFoldersAreWhereWhisperKitLooks() {
        // WhisperKit's own Hub layout: the model where its download put it, the tokenizer where it looks first
        let hub = HubApiWrapper(downloadBase: models)
        let model = hub.localRepoLocation(HubApiWrapper.Repo(id: WhisperModel.files.repo))
            .appendingPathComponent(WhisperModel.files.folder)
        #expect(WhisperModel.files.location(in: models).standardizedFileURL == model.standardizedFileURL)
        let tokenizer = hub.localRepoLocation(HubApiWrapper.Repo(id: "openai/whisper-large-v3"))
        #expect(WhisperModel.tokenizer.location(in: models).standardizedFileURL == tokenizer.standardizedFileURL)
        #expect(whisperPins.map(\.repo) == ["argmaxinc/whisperkit-coreml", "openai/whisper-large-v3"])
    }

    /// The real model: downloads about 1.6 GB from Hugging Face at the pinned commits into a temporary folder (or uses
    /// the folder in VOICE_WHISPER_MODELS, kept), checks every file, then starts Whisper from it on the CPU only (no
    /// Neural Engine compile) and transcribes a second of silence. Run it again with VOICE_WHISPER_MODELS under
    /// `sandbox-exec -p '(version 1)(allow default)(deny network-outbound (remote ip))'` (and `swift test
    /// --disable-sandbox --skip-build`: SwiftPM's own sandbox cannot nest) to see that a start with the files in place
    /// needs no network: main's start (`model:` and `downloadBase:`) fails there. HF_ENDPOINT proves nothing here:
    /// WhisperKit names Hugging Face's address itself for its download step. Only with VOICE_WHISPER_TESTS=1.
    @Test(.enabled(if: ProcessInfo.processInfo.environment["VOICE_WHISPER_TESTS"] == "1"))
    func thePinnedWhisperDownloadsChecksAndStartsWithoutTheNetwork() async throws {
        let given = ProcessInfo.processInfo.environment["VOICE_WHISPER_MODELS"]
        let models = given.map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.temporaryDirectory.appendingPathComponent("whisper-\(UUID().uuidString)")
        defer { if given == nil { try? FileManager.default.removeItem(at: models) } }
        try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
        for pin in whisperPins {
            #expect(try await prepareModel(pin, in: models) { try await downloadPinned($0, models: models) } == .ok)
        }
        // the next start: every file checked, nothing downloaded; the check costs a start well under 5 s (measured:
        // about 0.6 s for the 1.6 GB on an M-series Mac)
        var downloads = 0
        let clock = ContinuousClock()
        let began = clock.now
        for pin in whisperPins {
            #expect(try await prepareModel(pin, in: models) { _ in downloads += 1 } == .ok)
        }
        let checked = clock.now - began
        #expect(downloads == 0)
        #expect(checked < .seconds(5), "checked in \(checked)")
        let config = whisperConfig(models: models)
        config.computeOptions = ModelComputeOptions(melCompute: .cpuOnly, audioEncoderCompute: .cpuOnly, textDecoderCompute: .cpuOnly)
        config.verbose = false
        let whisper = try await WhisperKit(config)
        let silence = models.appendingPathComponent("silence.wav")
        try silentWAV(seconds: 1).write(to: silence)
        defer { try? FileManager.default.removeItem(at: silence) }
        // the start's warm-up: its second of silence runs the encoder and the decoder (review 0.9.7, C20)
        let results = try await whisper.transcribe(audioPath: silence.path, decodeOptions: warmUpOptions())
        let loops = results.map(\.timings.totalDecodingLoops).reduce(0, +)
        let runs = results.map(\.timings.totalEncodingRuns).reduce(0, +)
        #expect(!results.isEmpty && loops > 0 && runs > 0, "warm-up: \(runs) encoder runs, \(loops) decoder loops")
        // the warm-up before, WhisperKit's defaults: no encoder run and no decoder loop at all (measured 25 Oct: 0 and
        // 0, against 1 and 5 now); if that changes, WhisperKit decodes a short clip by default
        let before = try await whisper.transcribe(audioPath: silence.path, decodeOptions: DecodingOptions(language: "en"))
        #expect(before.map(\.timings.totalEncodingRuns).reduce(0, +) == 0)
        #expect(before.map(\.timings.totalDecodingLoops).reduce(0, +) == 0)
        // a word in a clip under 1 s, as a call cuts it (0.2 s before and after the word): heard with our options;
        // WhisperKit's default decodes nothing there (if that changes, the workaround in whisperOptions can go)
        let word = models.appendingPathComponent("word.wav")
        try shortWord("Yes.", to: word)
        defer { try? FileManager.default.removeItem(at: word) }
        let heard = try await whisper.transcribe(audioPath: word.path, decodeOptions: whisperOptions(language: "en"))
        #expect(heard.map(\.text).joined().lowercased().contains("yes"))
        // a call turn's path (review C5): the same clip, from memory, is heard the same
        let samples = try #require(pcm16kMonoSamples(Data(contentsOf: word)))
        let fromMemory = try await whisper.transcribe(audioArray: samples, decodeOptions: whisperOptions(language: "en"))
        #expect(fromMemory.map(\.text) == heard.map(\.text))
        let plain = try await whisper.transcribe(audioPath: word.path, decodeOptions: DecodingOptions(language: "en", withoutTimestamps: true))
        #expect(plain.flatMap(\.segments).isEmpty)
    }

    /// `text` said by macOS's `say` at 16 kHz, cut as a call cuts a turn (0.2 s before and after its loud part), under
    /// 1 s, written to `file`.
    private func shortWord(_ text: String, to file: URL) throws {
        let said = file.deletingLastPathComponent().appendingPathComponent("said.wav")
        defer { try? FileManager.default.removeItem(at: said) }
        let say = Process()
        say.executableURL = URL(fileURLWithPath: "/usr/bin/say")
        say.arguments = ["-o", said.path, "--file-format=WAVE", "--data-format=LEI16@16000", text]
        try say.run()
        say.waitUntilExit()
        let wav = try Data(contentsOf: said)
        let data = try #require(wav.range(of: Data("data".utf8)))
        let bytes = [UInt8](wav[(data.upperBound + 4)...])
        let samples = stride(from: 0, to: bytes.count - 1, by: 2).map { Int16(bitPattern: UInt16(bytes[$0]) | UInt16(bytes[$0 + 1]) << 8) }
        let loud = try #require(samples.firstIndex { abs(Int($0)) > 1_000 })
        let last = try #require(samples.lastIndex { abs(Int($0)) > 1_000 })
        let cut = samples[max(0, loud - 3_200)...min(samples.count - 1, last + 3_200)].map { Float($0) / 32_768 }
        #expect(cut.count < 16_000, "the word's clip is \(cut.count) samples")
        try pcm16WAV(cut, sampleRate: 16_000).write(to: file)
    }
}

@Suite struct SpeechDetection {
    private func models() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("vad-\(UUID().uuidString)")
    }

    /// One chunk of silence.
    private func chunk() -> Data { Data(count: 8192) }

    @Test func theDetectorRunsOnTheCPUOnly() {
        // never on the Neural Engine, where a chunk would wait behind Whisper, Kokoro or Supertonic: both the CoreML
        // model's configuration and FluidAudio's VadConfig say so
        #expect(speechModelConfiguration().computeUnits == .cpuOnly)
        #expect(speechVadConfig().computeUnits == .cpuOnly)
    }

    @Test func theModelIsWhereFluidAudioPutsItUnderTheServiceModels() {
        // FluidAudio's own names, so a FluidAudio update that renames the model fails here, not at a start
        let bundle = speechModelBundle(models: URL(fileURLWithPath: "/models"))
        #expect(bundle.path == "/models/silero-vad/\(SpeechModel.bundle)")
    }

    @Test func notReadyWhileLoading() async {
        let detector = SpeechDetector(models: models())
        #expect(await detector.state == .loading)
        let response = String(decoding: await Server.vad(chunk(), target: "/vad?stream=call-1", detector), as: UTF8.self)
        #expect(response.hasPrefix("HTTP/1.1 503 ") && response.hasSuffix(#"{"error":"not_ready"}"#))
    }

    @Test func aChangedModelIsUnavailableAndNeverLoaded() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let bundle = speechModelBundle(models: models)
        for path in SpeechModel.sha256.keys {
            let file = bundle.appendingPathComponent(path)
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data("not the model".utf8).write(to: file)
        }
        let detector = SpeechDetector(models: models)
        await detector.load()  // all files there: no download is tried
        #expect(await detector.state == .unavailable)
        let response = String(decoding: await Server.vad(chunk(), target: "/vad?stream=call-1", detector), as: UTF8.self)
        #expect(response.hasPrefix("HTTP/1.1 503 ") && response.hasSuffix(#"{"error":"unavailable"}"#))
    }

    @Test func badRequestsAre400WhateverTheModel() async {
        let detector = SpeechDetector(models: models())
        let badStream = String(decoding: await Server.vad(chunk(), target: "/vad?stream=a.b", detector), as: UTF8.self)
        #expect(badStream.hasPrefix("HTTP/1.1 400 ") && badStream.hasSuffix(#"{"error":"bad_stream"}"#))
        let badChunk = String(decoding: await Server.vad(Data(count: 8190), target: "/vad?stream=a", detector), as: UTF8.self)
        #expect(badChunk.hasPrefix("HTTP/1.1 400 ") && badChunk.hasSuffix(#"{"error":"bad_chunk"}"#))
    }

    /// `p` from a `/vad` answer.
    private func probability(_ response: Data) throws -> Double {
        let text = String(decoding: response, as: UTF8.self)
        #expect(text.hasPrefix("HTTP/1.1 200 "))
        let body = try #require(text.components(separatedBy: "\r\n\r\n").last)
        return try #require((try JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Double])?["p"])
    }

    /// A sentence spoken by macOS's `say`, as the bridge sends a call's audio: 16 kHz mono 16-bit, little-endian.
    private func spoken(_ text: String, in folder: URL) throws -> Data {
        let file = folder.appendingPathComponent("speech.wav")
        let say = Process()
        say.executableURL = URL(fileURLWithPath: "/usr/bin/say")
        say.arguments = ["-o", file.path, "--file-format=WAVE", "--data-format=LEI16@16000", text]
        try say.run()
        say.waitUntilExit()
        let wav = try Data(contentsOf: file)
        let data = try #require(wav.range(of: Data("data".utf8)))
        return wav[(data.upperBound + 4)...]
    }

    /// The real model: downloads about 1 MB from Hugging Face into a temporary folder at the pinned commit, checks
    /// its sha256 and runs it on silence and on speech. Only with VOICE_MODEL_TESTS=1 (`swift test` stays offline
    /// otherwise).
    @Test(.enabled(if: ProcessInfo.processInfo.environment["VOICE_MODEL_TESTS"] == "1"))
    func thePinnedModelDownloadsLoadsAndAnswers() async throws {
        pinSpeechModel()
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
        let detector = SpeechDetector(models: models)
        await detector.load()
        #expect(await detector.state == .ready)
        // read back from the loaded model and FluidAudio: the CPU only, as the builders say
        let runsOn = try #require(await detector.runsOn)
        #expect(runsOn.model == .cpuOnly && runsOn.vad == .cpuOnly)
        let bundle = speechModelBundle(models: models)
        #expect(checkModelFiles(at: bundle, against: SpeechModel.sha256) == .ok)
        let marker = bundle.deletingLastPathComponent().appendingPathComponent(".fluidaudio-revision")
        #expect(try String(contentsOf: marker, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines) == SpeechModel.revision)
        for stream in ["call-1", "call-2", "call-1"] {
            #expect(try probability(await Server.vad(chunk(), target: "/vad?stream=\(stream)", detector)) < 0.5)  // silence
        }
        #expect(await detector.streams == 2)
        let speech = try spoken("Wait, stop. I want to ask you something about tomorrow morning.", in: models)
        let clock = ContinuousClock()
        var ps: [Double] = []
        var times: [Duration] = []
        for start in stride(from: speech.startIndex, to: speech.endIndex - 8191, by: 8192) {
            let began = clock.now
            let response = await Server.vad(speech[start..<start + 8192], target: "/vad?stream=call-3", detector)
            times.append(clock.now - began)
            ps.append(try probability(response))
        }
        #expect(ps.count >= 10 && ps.allSatisfy { (0...1).contains($0) })
        #expect(ps.filter { $0 >= 0.99 }.count >= 3)  // the spike's stop threshold, on clear speech
        // one /vad chunk (256 ms of audio), request to answer, on the CPU: well under 50 ms, never near the bridge's
        // 600 ms limit (measured: about 0.4 ms a chunk on an M-series CPU)
        let sorted = times.sorted()
        #expect(sorted[sorted.count / 2] < .milliseconds(10), "chunk times: \(sorted)")
        #expect(sorted[sorted.count - 1] < .milliseconds(25), "chunk times: \(sorted)")
    }
}

/// NeMo's text normalisation stays out (owner, 16 Oct: dropped as too big for what it reads well): English numbers and
/// times reach Kokoro as VoiceCore's words. A FluidAudio upgrade that linked it again (a new default, a SwiftPM that
/// ignores `traits: []`) would put its readings over them ("thirteen o'clock") and add its 9 MB.
@Suite struct EnglishNumbers {
    @Test func nemoTextNormalisationIsNotLinked() {
        #expect(!NemoTextNormalizer.isAvailable)
    }

    @Test func kokoroGetsTheTimeInWords() throws {
        let request = try SpeechRequest(json: Data(#"{"input": "Lunch at 13:00, the call at 7:30 PM."}"#.utf8))
        #expect(request.engine == .kokoro)
        #expect(request.spokenInput == "Lunch at one in the afternoon, the call at seven thirty in the evening.")
    }
}

/// Kokoro gets VoiceCore's lexicon (capitals said as their letters) before its first sentence.
@Suite struct EnglishLexiconWiring {
    actor FakeKokoro: EnglishLexiconTaking {
        var entries: [String: String]?
        func setEnglishCustomLexicon(_ entries: [String: String]) { self.entries = entries }
    }

    @Test func kokoroGetsVoiceCoresLexicon() async {
        let kokoro = FakeKokoro()
        await prepareEnglish(kokoro)
        #expect(await kokoro.entries == EnglishLexicon.entries)
        #expect(await kokoro.entries?["ID"] == "ˈI dˈi")
    }
}

/// Owner, 25 Oct: her voice service answers only programs running as her own macOS account. The server, started on a
/// free loopback port, called by this test process: answered when the server's account is this process's, refused
/// (403, before the request is read: no engine is reached) when the server runs as another account.
@Suite(.serialized) struct OnlyItsOwnAccount {
    /// A request over a plain socket of this process, sent at once as the bridge does (nothing for ""), from an IPv6
    /// socket to ::ffff:127.0.0.1 with `v6`; the whole answer, waited for up to `seconds`.
    private func exchange(_ port: UInt16, _ request: String, v6: Bool = false, seconds: Int = 5) throws -> String {
        let fd = socket(v6 ? AF_INET6 : AF_INET, SOCK_STREAM, 0)
        defer { close(fd) }
        var wait = timeval(tv_sec: seconds, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &wait, socklen_t(MemoryLayout<timeval>.size))
        let connected: Int32
        if v6 {
            var address = sockaddr_in6()
            address.sin6_len = UInt8(MemoryLayout<sockaddr_in6>.size)
            address.sin6_family = sa_family_t(AF_INET6)
            address.sin6_port = port.bigEndian
            _ = inet_pton(AF_INET6, "::ffff:127.0.0.1", &address.sin6_addr)
            connected = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in6>.size)) }
            }
        } else {
            var address = sockaddr_in()
            address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            address.sin_family = sa_family_t(AF_INET)
            address.sin_addr.s_addr = inet_addr("127.0.0.1")
            address.sin_port = port.bigEndian
            connected = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
            }
        }
        #expect(connected == 0)
        if !request.isEmpty { _ = request.withCString { send(fd, $0, strlen($0), 0) } }
        var answer = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let got = recv(fd, &buffer, buffer.count, 0)
            if got <= 0 { break }
            answer.append(contentsOf: buffer[0..<got])
        }
        return String(decoding: answer, as: UTF8.self)
    }

    /// The log lines a server wrote, from its queue.
    private final class Lines: @unchecked Sendable {
        private let lock = NSLock()
        private var lines: [String] = []
        func add(_ line: String) { lock.withLock { lines.append(line) } }
        var all: [String] { lock.withLock { lines } }
    }

    private func started(account: uid_t, lines: Lines = Lines(), refusalInterval: TimeInterval = 60) async throws
        -> (Server, UInt16)
    {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("voice-peer-\(UUID().uuidString)")
        let server = Server(
            engines: Engines(models: folder, temp: folder, programs: folder, pocket: PocketVoices(english: .english18M)),
            account: account, log: lines.add,
            refusalInterval: refusalInterval)
        try server.start(port: 0)
        for _ in 0..<200 {
            if let port = server.port { return (server, port) }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        throw POSIXError(.ETIMEDOUT)
    }

    @Test func aProgramOfTheServicesOwnAccountIsAnswered() async throws {
        let (server, port) = try await started(account: geteuid())
        defer { server.stop() }
        let answer = try exchange(port, "GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        #expect(answer.hasPrefix("HTTP/1.1 200 OK\r\n") && answer.contains("\"stt\":\"loading\""))
    }

    @Test func aProgramOfAnotherAccountIsRefusedBeforeItsRequestIsRead() async throws {
        let another: uid_t = geteuid() == 1 ? 2 : 1
        let (server, port) = try await started(account: another)
        defer { server.stop() }
        for request in [
            "GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
            "POST /v1/audio/speech HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 15\r\n\r\n{\"input\":\"hi\"}",
        ] {
            let answer = try exchange(port, request)
            #expect(answer.hasPrefix("HTTP/1.1 403 Forbidden\r\n") && answer.hasSuffix("{\"error\":\"other_account\"}"))
        }
    }

    @Test func aConnectionThatSendsNothingIsRefusedAtOnce() async throws {
        // review R1-F5: refused before anything it sends is read, so one that sends nothing gets the 403 at once (a
        // check after reading would wait up to the 30 s receive limit)
        let another: uid_t = geteuid() == 1 ? 2 : 1
        let (server, port) = try await started(account: another)
        defer { server.stop() }
        let start = Date()
        let answer = try exchange(port, "", seconds: 2)
        #expect(answer.hasPrefix("HTTP/1.1 403 Forbidden\r\n") && answer.hasSuffix("{\"error\":\"other_account\"}"))
        #expect(Date().timeIntervalSince(start) < 1)
    }

    @Test func anIPv6SocketForTheLoopbackAddressIsAnsweredAsAnIPv4One() async throws {
        let (server, port) = try await started(account: geteuid())
        defer { server.stop() }
        let answer = try exchange(port, "GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n", v6: true)
        #expect(answer.hasPrefix("HTTP/1.1 200 OK\r\n") && answer.contains("\"stt\":\"loading\""))
    }

    @Test func aStreamOfRefusalsIsOneLogLineThenOneCountedLine() async throws {
        // review R1-F1: each refusal wrote its own line, so a program calling in a loop emptied her voice log
        let another: uid_t = geteuid() == 1 ? 2 : 1
        let lines = Lines()
        let (server, port) = try await started(account: another, lines: lines, refusalInterval: 1)
        defer { server.stop() }
        for _ in 0..<20 { _ = try exchange(port, "GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n") }
        let refused = { lines.all.filter { $0.hasPrefix("refused") } }
        #expect(refused() == [PeerCheck.refusal(.unknown)])
        try await Task.sleep(nanoseconds: 1_500_000_000)
        #expect(refused() == [PeerCheck.refusal(.unknown), "refused 19 more connections in the last minute"])
    }

    @Test func aPeerOnlyItsAddressNamesIsLookedUpByIt() {
        let v4 = NWEndpoint.hostPort(host: .ipv4(IPv4Address("127.0.0.1")!), port: 50_123)
        #expect(peerEndpoint(v4) == PeerCheck.Endpoint(address: [127, 0, 0, 1], port: 50_123))
        let mapped = NWEndpoint.hostPort(host: .ipv6(IPv6Address("::ffff:127.0.0.1")!), port: 50_124)
        #expect(peerEndpoint(mapped) == PeerCheck.Endpoint(address: [127, 0, 0, 1], port: 50_124))
        // a peer without an IPv4 address cannot be looked up: refused
        #expect(peerEndpoint(.hostPort(host: .ipv6(.loopback), port: 50_125)) == nil)
        #expect(peerEndpoint(.hostPort(host: .name("localhost", nil), port: 50_126)) == nil)
        #expect(peerEndpoint(.unix(path: "/tmp/voice")) == nil)
    }
}

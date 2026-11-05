// voice-service: local-assistant's ears and voice. WhisperKit (large-v3 turbo; its files pinned and checked at start,
// see Whisper.swift) listens in the language the bridge names (one of eleven, never detected); Pocket TTS, on the CPU,
// is her only voice (owner, 3 Nov 2026: "remove kokoro and supertonic"; nothing replaces them): English in the voices
// heart, jane, eve, vera, alba and michael on the Mac's model (100M beside Gemma 4 26B, 18M beside 12B, without vera:
// heart says vera's lines there; heart's file for each ships in this program's own bundle beside it), and jane in
// Spanish, Italian, Portuguese, Dutch, German and French (Pocket.swift: its program beside this one, fetched by
// `voice-service --fetch-pocket` at a deploy). A line it cannot say is said by no other voice (409, `not_said`), and
// Korean, Polish, Japanese and Russian have none (409, `no_voice`). FluidAudio's Silero VAD tells speech in a
// call's audio (`/vad`, for barge-in and the pause), and Smart Turn judges whether a caller's sentence sounds finished
// (`/turn`, for early end of turn), both on the CPU only. Served on 127.0.0.1:8150 for programs of its own account (the
// bridge): a connection that no program of the service's own account holds is refused before its request is read
// (`PeerCheck`). Logs hold metadata (sizes, durations, language, timings), never audio, transcripts or text.
import AVFoundation
import Foundation
import Network
import VoiceCore
import WhisperKit

func log(_ message: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    LogCap.trim(fd: STDERR_FILENO)  // voice.log stays under 10 MB, in-process
    FileHandle.standardError.write(Data("\(stamp) voice-service: \(message)\n".utf8))
}

func seconds(since start: Date) -> String { String(format: "%.2f s", Date().timeIntervalSince(start)) }

/// Whisper's settings for a transcription: the named language is forced through the prefill prompt and never
/// detected (a wrong guess costs more than the switch); greedy, no timestamps, long audio cut at pauses. Nothing is
/// clipped from a window's end (`windowClipTime` 0; WhisperKit's 1 s default decodes nothing in a clip of 1 s or less:
/// a call's lone "yes" or her name alone, live 23 Oct). `prompt`: a hint's tokens (`hintTokens`), Whisper's prompt
/// before the audio (WhisperKit 1.1.1 `promptTokens`, put before the prefill); none, as before, without a hint.
func whisperOptions(language: String, prompt: [Int] = []) -> DecodingOptions {
    var options = DecodingOptions(
        task: .transcribe, language: language, temperature: 0, usePrefillPrompt: true, detectLanguage: false,
        skipSpecialTokens: true, withoutTimestamps: true, windowClipTime: 0, chunkingStrategy: .vad
    )
    if !prompt.isEmpty { options.promptTokens = prompt }
    return options
}

/// The start's warm-up transcription (a second of silence): a call turn's own options, so the encoder and the
/// decoder both run and the first real turn finds every model compiled (review 0.9.7, C20: WhisperKit's defaults
/// decode nothing in a clip of 1 s or less, so the warm-up may have run no prediction).
func warmUpOptions() -> DecodingOptions { whisperOptions(language: "en") }

/// A hint's tokens for Whisper's prompt: the text after a space, without special tokens, as WhisperKit's own CLI
/// encodes a prompt (ArgmaxCLI/TranscribeCLI.swift, 1.1.1); none for no hint.
func hintTokens(_ hint: String, encode: (String) -> [Int], specialTokenBegin: Int) -> [Int] {
    hint.isEmpty ? [] : encode(" " + hint).filter { $0 < specialTokenBegin }
}

/// Runs one piece of engine work at a time (the spec's serial queue); later callers wait their turn.
actor Serial {
    private var busy = false
    private var waiting: [CheckedContinuation<Void, Never>] = []

    func run<T>(_ work: () async throws -> T) async rethrows -> T {
        if busy { await withCheckedContinuation { waiting.append($0) } }
        busy = true
        defer {
            if waiting.isEmpty { busy = false } else { waiting.removeFirst().resume() }
        }
        return try await work()
    }
}

/// Whisper, loaded and warmed once at start, then kept in memory; Pocket TTS's speakers (English's checked and
/// fetched at start, a language's once a line is asked in it) and the speech and turn detectors at start too, each on
/// its own.
actor Engines {
    enum State: String { case loading, ready, failed }

    private(set) var stt = State.loading
    /// Listening failed because Whisper's files are not the pinned ones: a restart would find the same files.
    private var sttRefused = false
    private var whisper: WhisperKit?
    /// The Pocket voices of this Mac (`PocketVoices`: English on the Mac's model, or a check's `--pocket`).
    let pocketVoices: PocketVoices
    /// Pocket's speakers by their model's name (`PocketVoices.models`: one a model, so every English voice shares the
    /// Mac's model's files, checks and state; each of the six languages jane speaks its own).
    let pockets: [String: PocketSpeaker]
    let detector: SpeechDetector
    let turn: TurnDetector
    private let models: URL
    private let temp: URL
    private let serial = Serial()
    /// Pocket's own queue: its runs are on the CPU, one at a time, and never hold Whisper's (a call's transcription
    /// waits for no run of eve's; the review of 24 Oct). A speaker's first run ("Ready.") is on it too
    /// (the review of 25 Oct), its check and fetch never.
    private let pocketQueue: Serial

    /// `programs`: the folder of this program, where Pocket TTS's program is (in her release: root's); `pocket`: the
    /// Pocket voices of this Mac.
    init(models: URL, temp: URL, programs: URL, pocket: PocketVoices) {
        self.models = models
        self.temp = temp
        pocketVoices = pocket
        detector = SpeechDetector(models: models)
        turn = TurnDetector(models: models)
        let her = URL(fileURLWithPath: NSHomeDirectory())
        let queue = Serial()
        pocketQueue = queue
        pockets = Dictionary(uniqueKeysWithValues: pocket.models.map {
            ($0.model.name, PocketSpeaker(models: models, her: her, programs: programs, model: $0.model,
                                          askedOnly: !$0.atStart, name: pocket.name(of: $0.model),
                                          firstRun: { try await queue.run($0) }))
        })
    }

    /// Loads listening, with the English Pocket speaker's check, fetch and first run started before it
    /// (`startEngines`; a language's only once a line is asked in it). Listening that fails exits the process, so
    /// launchd starts it again (KeepAlive), except when Whisper's files are not the pinned ones: listening stays
    /// "failed" and the service stays up. A Pocket speaker that fails rests and is tried again in the process
    /// (`PocketSpeaker`, `SpeakingRetry`: 1, 5 and 30 minutes, then every 30; `tts` in `/health` says "resting" after
    /// failed runs, else "failed", meanwhile: `speakingState`), never by a restart that reloads Whisper. The speech detector loads beside them,
    /// without waiting for Whisper's compile; when it fails it is only "unavailable" (the bridge keeps its loudness
    /// rule), and the service stays up. The turn detector the same way (unavailable: the bridge waits the full pause).
    func load() async {
        let (detector, turn) = (self.detector, self.turn)
        Task { await detector.load() }
        Task { await turn.load() }
        let pockets = pocketVoices.models.filter(\.atStart).compactMap { self.pockets[$0.model.name] }
        await startEngines(pockets: pockets, listening: { await self.loadWhisper() })
        log("Pocket TTS (\(PocketTTS.languages.map(\.language).joined(separator: ", ")): jane): each language's files checked and fetched only once a line is asked in it; no line is said in it until then")
        if stt == .failed && !sttRefused {
            log("listening failed to load; exiting so launchd tries again")
            exit(1)
        }
    }

    /// Whisper's pinned files, downloaded at their commits when missing: true when every one is the pinned one. A
    /// failed download throws, and files still missing after it (offline) return false: launchd tries again. Changed
    /// files are set aside (never loaded) and downloaded again once; changed again, they return false with listening
    /// refused.
    private func whisperFiles() async throws -> Bool {
        let start = Date()
        let models = self.models
        var downloaded = false
        for pin in whisperPins {
            let files = try await prepareModel(pin, in: models, setAside: { aside in
                log("listening: model files changed, not the pinned ones (\(pin.path)): set aside as \(aside.lastPathComponent), never loaded; downloading the pinned ones again")
            }) {
                downloaded = true
                try await downloadPinned($0, models: models)
            }
            switch files {
            case .ok:
                continue
            case .missing:
                log("listening failed to load: model files still missing after the download (\(pin.path))")
                return false
            case .changed:
                sttRefused = true
                log("listening unavailable: model files are not the pinned ones even after a fresh download, or could not be set aside (\(pin.path) in voice-models; never loaded)")
                return false
            }
        }
        log("listening: pinned model files \(downloaded ? "downloaded and checked" : "checked") in \(seconds(since: start))")
        return true
    }

    private func loadWhisper() async {
        let start = Date()
        do {
            guard try await whisperFiles() else {
                stt = .failed
                return
            }
            let whisper = try await WhisperKit(whisperConfig(models: models))
            // warm-up: the first transcription compiles the model for the Neural Engine (cached per macOS build)
            let silence = temp.appendingPathComponent("warm-up.wav")
            try silentWAV(seconds: 1).write(to: silence)
            defer { try? FileManager.default.removeItem(at: silence) }
            _ = try await whisper.transcribe(audioPath: silence.path, decodeOptions: warmUpOptions())
            self.whisper = whisper
            stt = .ready
            log("listening ready in \(seconds(since: start))")
        } catch {
            stt = .failed
            log("listening failed to load: \(type(of: error))")
        }
    }

    /// The `/health` answer, every engine's and detector's state; speaking (`tts`) is her English Pocket model's
    /// (`speakingState`: ready, resting, failed or loading), as Pocket TTS is her only voice, with the shipped voices it
    /// leaves out (`voices_left_out`).
    func health() async -> Data {
        var pocket: [String: String] = [:]
        for speech in pocketVoices.models {
            pocket[speech.health] = await pockets[speech.model.name]?.state.rawValue
        }
        let english = pocketVoices.models.first(where: \.atStart).flatMap { pockets[$0.model.name] }
        let state = await english?.state.rawValue ?? ""
        let resting = await english?.resting ?? false
        let leftOut = await english?.leftOut ?? []
        return HTTP.json(200, healthBody(stt: stt.rawValue, tts: speakingState(state, resting: resting),
                                         vad: await detector.state.rawValue, turn: await turn.state.rawValue,
                                         pocket: pocket, leftOut: leftOut))
    }

    /// Transcribes audio bytes of a sniffed kind in the given language, with `hint` (her name, or "") as Whisper's
    /// prompt: a call turn from memory, other audio from a file that lives only for the call (`transcribeClip`).
    /// `call`: a call's turn, its bare thanks and bye kept (`cleanTranscript`).
    func transcribe(_ audio: Data, kind: String, language: String, hint: String = "", call: Bool = false) async throws -> (text: String, duration: Double) {
        guard let whisper else { throw VoiceError.notReady }
        let prompt = whisper.tokenizer.map {
            hintTokens(hint, encode: $0.encode(text:), specialTokenBegin: $0.specialTokens.specialTokenBegin)
        } ?? []
        let options = whisperOptions(language: language, prompt: prompt)
        let serial = self.serial
        let (results, duration) = try await transcribeClip(audio, kind: kind, temp: temp, samples: { samples in
            try await serial.run { try await whisper.transcribe(audioArray: samples, decodeOptions: options) }
        }, file: { path in
            try await serial.run { try await whisper.transcribe(audioPath: path, decodeOptions: options) }
        })
        let segments = results.flatMap(\.segments).map {
            TranscriptSegment(text: $0.text, noSpeechProb: $0.noSpeechProb, avgLogprob: $0.avgLogprob)
        }
        return (cleanTranscript(segments, call: call), duration)
    }

    /// 24 kHz mono 16-bit WAV of `text`, the request's spoken text (`SpeechRequest.spokenInput`, worked out by the
    /// caller off this actor and before the engine queues, so text work never holds a call's listening: review 0.9.7,
    /// C7), in the request's Pocket voice (`PocketVoices.speech`), read and set to its voice's gain. A line Pocket
    /// cannot say is said by no other voice, whatever the request asks (owner, 3 Nov 2026): logged and refused
    /// (`PocketLine`: 409, `not_said`).
    func speak(_ request: SpeechRequest, text: String) async throws -> Data {
        let pocketQueue = self.pocketQueue
        let speech = pocketVoices.speech(request.voice, language: request.language)
        let pocket = speech.flatMap { pockets[$0.model.name] }
        return try await PocketLine.speak({
            guard let speech, let pocket else { throw PocketFailure.notReady("no voice \(request.voice)") }
            return try await pocketQueue.run { try await pocket.speak(text, voice: speech.speaks, call: request.call) }
        }, log: { log($0) })
    }
}

/// The engines' start: the English Pocket speakers first, one at a time, in a task of their own (their check and fetch
/// are network and sha256 work, their first run on Pocket's own queue: they never hold Whisper), then Whisper (on a
/// first start a download and a compile of about 15 minutes; the review of 26 Oct, R1-F2: Pocket's fetch began only
/// after it, so "My voice is ready" waited for both one after the other). A line asked before her voice is ready is
/// not said (409, `not_said`).
func startEngines(pockets: [PocketSpeaker], listening: () async -> Void) async {
    Task { for pocket in pockets { await pocket.load() } }
    await listening()
}

/// A connection's other end as `PeerCheck` looks it up: its IPv4 address (an IPv6 one only when it maps one) and port;
/// nil for any other endpoint, which cannot be looked up and is refused.
func peerEndpoint(_ endpoint: NWEndpoint) -> PeerCheck.Endpoint? {
    guard case .hostPort(let host, let port) = endpoint else { return nil }
    switch host {
    case .ipv4(let address):
        return PeerCheck.Endpoint(address: [UInt8](address.rawValue), port: port.rawValue)
    case .ipv6(let address):
        return address.asIPv4.map { PeerCheck.Endpoint(address: [UInt8]($0.rawValue), port: port.rawValue) }
    default:
        return nil
    }
}

/// Loopback HTTP/1.1 server on Network.framework, one request per connection (after OpenWhisperer). It answers only
/// connections a program of `account` holds (owner, 25 Oct: the service's own account, her own in her own user).
final class Server {
    /// A connection that has not delivered its whole request by then is dropped.
    static let receiveTimeout: TimeInterval = 30

    private final class Connection {
        let nw: NWConnection
        var received = false
        init(_ nw: NWConnection) { self.nw = nw }
    }

    private let engines: Engines
    private let account: uid_t
    private let logLine: (String) -> Void
    private let refusalInterval: TimeInterval
    private let queue = DispatchQueue(label: "voice.http")
    private var listener: NWListener?  // set and read on the queue only
    private var lastHers: pid_t?  // the program found last (the bridge): looked at first
    private var refusals = PeerCheck.RefusalLog()  // on the queue

    /// `log` and `refusalInterval` (one counted refusal line per interval) are the tests' to change.
    init(
        engines: Engines, account: uid_t = geteuid(), log logLine: @escaping (String) -> Void = log,
        refusalInterval: TimeInterval = PeerCheck.RefusalLog.interval
    ) {
        self.engines = engines
        self.account = account
        self.logLine = logLine
        self.refusalInterval = refusalInterval
    }

    /// The port it listens on, once it does (`start(port: 0)` takes a free one).
    var port: UInt16? { queue.sync { listener?.state == .ready ? listener?.port?.rawValue : nil } }

    func start(port: UInt16) throws {
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        params.requiredInterfaceType = .loopback
        params.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!)
        let listener = try NWListener(using: params)
        listener.stateUpdateHandler = { [logLine] state in
            if case .failed(let error) = state {
                logLine("cannot listen on 127.0.0.1:\(port): \(error)")
                exit(1)  // launchd restarts the service
            }
        }
        listener.newConnectionHandler = { [weak self] in self?.accept($0) }
        // on the queue, before it starts: a connection's check reads it there (review R1-F4: set after the start, a
        // connection handled first was refused as another account's)
        queue.sync { self.listener = listener }
        listener.start(queue: queue)
        logLine("listening on 127.0.0.1:\(port)")
    }

    func stop() { queue.sync { listener?.cancel() } }

    private func accept(_ nw: NWConnection) {
        nw.start(queue: queue)
        let verdict = self.verdict(nw)
        guard case .hers(let pid) = verdict else {
            refused(verdict)  // nothing of the request is read
            return send(nw, PeerCheck.refused)
        }
        lastHers = pid
        let connection = Connection(nw)
        queue.asyncAfter(deadline: .now() + Self.receiveTimeout) {
            if !connection.received { nw.cancel() }
        }
        receive(connection, buffer: Data())
    }

    /// A refusal's log line: the first at once, the rest of each interval counted into one line (`RefusalLog`), so a
    /// program calling in a loop never fills her voice log.
    private func refused(_ verdict: PeerCheck.Verdict) {
        guard let line = refusals.refused(verdict) else { return }
        logLine(line)
        countRefusals()
    }

    private func countRefusals() {
        queue.asyncAfter(deadline: .now() + refusalInterval) { [weak self] in
            guard let self, let line = self.refusals.due() else { return }
            self.logLine(line)
            self.countRefusals()
        }
    }

    /// Who holds the connection's other end: a program of the service's account, else refused.
    private func verdict(_ nw: NWConnection) -> PeerCheck.Verdict {
        guard let peer = peerEndpoint(nw.endpoint), let port = listener?.port?.rawValue else { return .unknown }
        let service = PeerCheck.Endpoint(address: [127, 0, 0, 1], port: port)
        return PeerCheck.look(peer: peer, service: service, own: account, first: lastHers)
    }

    private func receive(_ connection: Connection, buffer: Data) {
        connection.nw.receive(minimumIncompleteLength: 1, maximumLength: 1 << 16) { [weak self] data, _, complete, error in
            guard let self else { return }
            var buffer = buffer
            if let data { buffer.append(data) }
            switch HTTP.parse(buffer, maxBody: Limits.maxRequestBytes) {
            case .request(let request):
                connection.received = true
                self.handle(connection.nw, request)
            case .invalid(let status):
                connection.received = true
                self.send(connection.nw, HTTP.json(status, ["error": "bad_request"]))
            case .incomplete:
                if complete || error != nil { connection.nw.cancel() } else { self.receive(connection, buffer: buffer) }
            }
        }
    }

    private func handle(_ nw: NWConnection, _ request: HTTPRequest) {
        guard HTTP.isTrustedCaller(origin: request.header("origin"), host: request.header("host")) else {
            return send(nw, HTTP.json(403, ["error": "forbidden"]))
        }
        let engines = self.engines
        switch Route(request.method, request.path) {
        case .health:
            Task { self.send(nw, await engines.health()) }
        case .transcribe:
            Task { self.send(nw, await Self.transcribe(request.body, target: request.path, engines)) }
        case .speak:
            Task { self.send(nw, await Self.speak(request.body, engines)) }
        case .vad:
            Task { self.send(nw, await Self.vad(request.body, target: request.path, engines.detector)) }
        case .turn:
            Task { self.send(nw, await Self.turn(request.body, engines.turn)) }
        case .methodNotAllowed:
            send(nw, HTTP.json(405, ["error": "method_not_allowed"]))
        case .notFound:
            send(nw, HTTP.json(404, ["error": "not_found"]))
        }
    }

    private static func transcribe(_ body: Data, target: String, _ engines: Engines) async -> Data {
        let start = Date()
        let request: TranscriptionRequest
        do {
            request = try TranscriptionRequest(target: target, body: body)
        } catch {
            log("transcription refused: \(error.code), \(body.count) bytes")
            return error.response
        }
        let (kind, language) = (request.kind, request.language)
        do {
            let (text, duration) = try await engines.transcribe(body, kind: kind, language: language, hint: request.hint, call: request.call)
            let hinted = request.hint.isEmpty ? "" : ", with the hint"  // never the hint itself
            log("transcribed \(body.count) bytes \(kind) \(language)\(hinted), \(String(format: "%.1f", duration)) s audio, \(text.count) chars in \(seconds(since: start))")
            return HTTP.json(200, ["text": text, "duration": (duration * 10).rounded() / 10])
        } catch let error as VoiceError {
            log("transcription refused: \(error.code), \(body.count) bytes \(kind) \(language)")
            return error.response
        } catch {
            log("transcription failed: \(type(of: error)), \(body.count) bytes \(kind) \(language)")
            return VoiceError.unreadable.response
        }
    }

    /// One line's WAV (`Engines.speak`), or a refusal: a line Pocket cannot say (409, `not_said`), a language her voice
    /// does not speak (409, `no_voice`), a request the bridge got wrong (400). Never another voice's audio.
    static func speak(_ body: Data, _ engines: Engines) async -> Data {
        let start = Date()
        let request: SpeechRequest
        do {
            request = try SpeechRequest(json: body)
        } catch let error as VoiceError {
            log("speech refused: \(error.code)")
            return error.response
        } catch {
            return HTTP.json(400, ["error": "bad_request"])
        }
        do {
            let wav = try await engines.speak(request, text: request.spokenInput)  // off the engines' actor
            log("spoke \(request.input.count) chars \(request.language) as \(wav.count) bytes in \(seconds(since: start))")
            return HTTP.response(200, contentType: "audio/wav", body: wav)
        } catch let error as VoiceError {
            return error.response
        } catch {
            log("speech failed: \(type(of: error)), \(request.language)")
            return VoiceError.failed.response
        }
    }

    /// One chunk's speech probability; refusals are logged (the bridge's mistakes), not "loading" or "unavailable",
    /// which the bridge reads from `/health` and answers with its loudness rule.
    static func vad(_ body: Data, target: String, _ detector: SpeechDetector) async -> Data {
        let chunk: SpeechChunk
        do {
            chunk = try SpeechChunk(target: target, body: body)
        } catch {
            log("speech detector refused: \(error.code), \(body.count) bytes")
            return error.response
        }
        do {
            return HTTP.json(200, try speechBody(try await detector.probability(of: chunk)))
        } catch let error as VoiceError {
            if error == .failed { log("speech detector failed: not a probability") }
            return error.response
        } catch {
            log("speech detector failed: \(type(of: error))")
            return VoiceError.failed.response
        }
    }

    /// One window's probability of a finished turn; refusals are logged (the bridge's mistakes), not "loading" or
    /// "unavailable", which the bridge reads from `/health` and answers with the full pause. Nothing is logged for an
    /// answer.
    static func turn(_ body: Data, _ detector: TurnDetector) async -> Data {
        let window: TurnWindow
        do {
            window = try TurnWindow(body: body)
        } catch {
            log("turn detector refused: \(error.code), \(body.count) bytes")
            return error.response
        }
        do {
            return HTTP.json(200, try turnBody(try await detector.probability(of: window)))
        } catch let error as VoiceError {
            if error == .failed { log("turn detector failed: not a probability") }
            return error.response
        } catch {
            log("turn detector failed: \(type(of: error))")
            return VoiceError.failed.response
        }
    }

    private func send(_ nw: NWConnection, _ response: Data) {
        nw.send(content: response, completion: .contentProcessed { _ in nw.cancel() })
    }
}

// MARK: - Start

/// The folder of this program: Pocket TTS's program is beside it (`--fetch-pocket` puts it there).
let programs = (Bundle.main.executableURL ?? URL(fileURLWithPath: CommandLine.arguments[0])).deletingLastPathComponent()
if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "--fetch-pocket" {
    // estia-deploy, as the owner, in the release it builds (before root makes it root's): the pinned program beside
    // this one, checked; no server, no model, nothing of hers
    Task { exit(await fetchPocket(into: programs) ? 0 : 1) }
    dispatchMain()
}
LogCap.trim(fd: STDOUT_FILENO)
LogCap.trim(fd: STDERR_FILENO)
// launchd passes none: 8150, the models folder in its home; anything odd is refused, never the defaults
guard let arguments = ServiceArguments(CommandLine.arguments) else {
    log(ServiceArguments.refused)
    exit(2)
}
let support = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support/local-assistant")
let models = arguments.models ?? support.appendingPathComponent("voice-models")
let temp = support.appendingPathComponent("voice-tmp")
do {
    for folder in [models, temp] {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    }
    for leftover in try FileManager.default.contentsOfDirectory(at: temp, includingPropertiesForKeys: nil) {
        try? FileManager.default.removeItem(at: leftover)  // audio from a crash: never kept
    }
} catch {
    log("cannot prepare \(support.path): \(error)")
    exit(1)
}
pinSpeechModel()
// Pocket's English model follows the Mac's Gemma (owner, 26 Oct): the 100M from 30 GB of memory (Gemma 4 26B), else
// the 18M (12B); a check built here may name the other
let memory = ProcessInfo.processInfo.physicalMemory
let pocketVoices = PocketVoices(english: arguments.pocket ?? PocketTTS.english(memory: memory))
log("Pocket TTS: English on its \(pocketVoices.size) model (\(pocketVoices.name(of: pocketVoices.english))), \(arguments.pocket == nil ? "for \(memory >> 30) GB of memory" : "as this check names")")
let engines = Engines(models: models, temp: temp, programs: programs, pocket: pocketVoices)
let server = Server(engines: engines)
do {
    try server.start(port: arguments.port)
} catch {
    log("cannot listen: \(error)")
    exit(1)
}
Task { await engines.load() }
dispatchMain()

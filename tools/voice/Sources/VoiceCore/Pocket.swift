import Foundation

/// Pocket TTS (Kyutai Labs; the program MIT, the model weights CC BY 4.0, the voices "jane", "eve", "vera" and
/// "michael" from the VCTK corpus and "alba" voice-acted by Alba MacKenna, CC BY 4.0; "heart" Kokoro-82M's af_heart,
/// Apache 2.0, cloned with Kyutai's cloning model, CC BY 4.0) speaks English in the voices heart (owner, 27 Oct: "call
/// the voice Heart", "make it default"; 29 Oct, the default on either model: "on 16gb make Heart the default as well"),
/// jane, eve, vera, alba and michael (owner, 26 Oct: "alba & michael for males, both on small and big"), all
/// on one model a Mac, by its Gemma (`english(memory:)`: owner, 26 Oct, "the Pocket model follows the Gemma tier"): the
/// 100M beside Gemma 4 26B, the 18M beside 12B, which has no vera (its heart a clone of its own); and
/// jane in Spanish, Italian, Portuguese, Dutch, German and French on each language's own model (`PocketTTS.languages`);
/// every other English voice is Kokoro's, the other languages Supertonic-3's. Its pinned release program (the Rust
/// `pocket-tts`) sits beside the voice service in her root-owned release (estia-deploy puts it there with
/// `voice-service --fetch-pocket`, so everything she runs stays root's: the review of 24 Oct), and runs once per text
/// on the CPU, one thread, int8, from a config naming only local files, under a sandbox with no network, no other
/// program, no signal to another, no macOS service and no file written (`PocketRun`), on a queue of its own (never the
/// one Whisper and Kokoro share): the
/// release has no resident mode, its `serve` mode is a web server (CORS for kyutai.org, voices fetched from the web)
/// and is never used, and the Python package kept resident was slower on 4 of the 5 measured lines (24 Oct: a whole
/// sentence in 90-601 ms against the program's 123-374 ms, loading included). Every file is pinned by its sha256 and
/// checked before the first run and whenever its size or times change; a changed file is never run. What cannot speak a
/// line (no files yet, a refused file, a run that fails) leaves that line to Kokoro (am_michael for alba and michael,
/// af_heart for the others; in another language to Supertonic-3's F1), never silence.
public enum PocketTTS {
    /// The English voices Pocket speaks, as the bridge names them (`/voice heart`, `/voice jane`, `/voice eve`, `/voice
    /// vera`, `/voice alba`, `/voice michael`: `config.POCKET_VOICES`, pinned equal by a test), in this order (the log's;
    /// owner, 27 Oct: "heart, jane, eve, vera (female); alba, michael (male)"); each is the voice of the same name of
    /// the Mac's English model (`PocketVoices`), vera on the 100M only. "eve-full", eve's name on the 100M
    /// beside the 18M's eve before (24-26 Oct), is the bridge's other name for eve and never reaches the voice service.
    public static let order = ["heart", "jane", "eve", "vera", "alba", "michael"]
    public static let voices: Set<String> = Set(order)
    /// From this much memory (GiB, as macOS counts it) a Mac speaks the 100M English model: setup gives such a Mac
    /// Gemma 4 26B (`setup.choose_model`, `voice.POCKET_STANDARD_GB`: pinned equal by a test), a smaller one 12B and
    /// the 18M (owner, 26 Oct: "a Mac on the 26B gets the 100M model, a 16 GB Mac on the 12B gets the 18M").
    public static let standardMemoryGiB: UInt64 = 30

    /// The English model a Mac with `memory` bytes speaks every English Pocket voice on.
    public static func english(memory: UInt64) -> PocketModel {
        memory >= standardMemoryGiB << 30 ? PocketModel.english : PocketModel.english18M
    }

    /// The English model a check names (`--pocket 100M` or `--pocket 18M`: `ServiceArguments`), nil for any other.
    public static func english(size: String) -> PocketModel? {
        [PocketModel.english, PocketModel.english18M].first { PocketVoices(english: $0).size == size }
    }

    /// The program, from its release.
    public static let release = PocketRelease.v4_0_1
    /// The voice service's own resources (SwiftPM's bundle of its target, `<package>_<target>.bundle`: Package.swift's
    /// `resources`), beside its program: the deploy copies it with the program (estia-deploy keeps every bundle of the
    /// build), so in her release it is root's. Its voice files (`PocketVoice.bundled`: heart's, one a model) come from
    /// there.
    public static let bundle = "voice-service_voice-service.bundle"

    /// Where the voice service's own voice files are, beside its program in `programs` (the folder of the program), as
    /// Foundation reads the bundle (the review of 27 Oct, swift-voice-1): the build lays it out by its build system,
    /// the files in Contents/Resources with Swift 6.4's default build (swiftbuild), at the bundle's top with the native
    /// build (Swift 6.2's default, which Package.swift accepts). No bundle there: its top (nothing to copy either way).
    public static func resources(beside programs: URL) -> URL {
        let folder = programs.appendingPathComponent(bundle)
        return Bundle(url: folder)?.resourceURL ?? folder
    }
    /// Pocket's folder under the voice service's models folder.
    public static let folder = "pocket-tts"
    /// The longest one run of `text` may take: a measured base and a few ms a character (24 Oct: a call's sentence
    /// took 0.15-0.8 s, a 1,500-character text, about 2 minutes of speech, under 5 s with Gemma generating; the review
    /// of 24 Oct: a flat 30 s could hold her line that long). 10 ms a character since the 100M speaks every English
    /// voice on a Mac with Gemma 4 26B (our own install, 26 Oct: with other programs busy on the Mac a 608-character
    /// line took 3.7-5.7 s through the voice service, 1.8-1.9 s on 25 Oct, and 5 ms a character stopped it in 2 runs
    /// of 4). Past it the run is stopped and Kokoro says the line.
    public static func runSeconds(for text: String) -> TimeInterval { 2 + 0.01 * Double(text.count) }
    /// The most WAV bytes a run may write (2 minutes of speech are 5.8 MB).
    public static let maxOutputBytes = 16 << 20
    /// Failed runs in a row before Pocket rests until its next try (`SpeakingRetry`'s times).
    public static let failuresBeforeRest = 3
    /// Resting after a run past its time limit, how long a call waits before one of its lines tries Pocket again (the
    /// final review of 0.9.8, calls-03-pocket-retry-latency: each call line waited out the limit, up to 17 s, before
    /// Kokoro said it); after a run that failed fast, every call line tries it.
    public static let callRetrySeconds: TimeInterval = 60
}

/// The Pocket voices of one Mac: every English voice on its model (`english`: `PocketTTS.english(memory:)`, or a
/// check's `--pocket`), each the model's voice of the same name (`PocketTTS.order`; the 18M has no vera), fetched at
/// the voice service's start, its state's key in `/health` "pocket"; and jane in the six languages on each language's
/// own model (`PocketTTS.languages`), the same on either. The voices of one model share its speaker (`models`): its
/// files, their checks, its state and its health key. The bridge's doctor checks the same model's files
/// (`voice.pocket_english_files`).
public struct PocketVoices: Equatable, Sendable {
    public let english: PocketModel

    public init(english: PocketModel) {
        self.english = english
    }

    /// The English model's size as a check names it and the log says it: "100M" or "18M".
    public var size: String { english == PocketModel.english ? "100M" : "18M" }
    /// The English voices, in `PocketTTS.order`: each the model's own voice of the same name.
    public var speakers: [PocketSpeech] {
        PocketTTS.order.filter { english.voices[$0] != nil }.map {
            PocketSpeech(voice: $0, model: english, speaks: $0, health: "pocket")
        }
    }
    /// The English voice by the bridge's name, nil for any other (vera on the 18M among them: Kokoro says its lines).
    public func speech(_ voice: String) -> PocketSpeech? { speakers.first { $0.voice == voice } }
    /// The voice by the bridge's name in `language`: English's, or jane in one of the six (`PocketTTS.languages`); nil
    /// for any other (Supertonic-3 speaks those).
    public func speech(_ voice: String, language: String) -> PocketSpeech? {
        language == Languages.english ? speech(voice)
            : PocketTTS.languages.first { $0.voice == voice && $0.language == language }
    }
    /// Every Pocket voice: English's, then the six languages'.
    public var all: [PocketSpeech] { speakers + PocketTTS.languages }
    /// One speaker per model, in the order of its first voice (that voice's `PocketSpeech` stands for it).
    public var models: [PocketSpeech] {
        all.filter { speech in all.first { $0.model == speech.model } == speech }
    }
    /// The bridge's names of the voices a model speaks here, in order (none for the other English model).
    public func voices(of model: PocketModel) -> [String] { all.filter { $0.model == model }.map(\.voice) }
    /// A model's speaker's name in the log: its voices ("heart, jane, eve, alba, michael"), a language's with its code
    /// ("de: jane").
    public func name(of model: PocketModel) -> String {
        let language = all.first { $0.model == model }?.language ?? Languages.english
        let voices = voices(of: model).joined(separator: ", ")
        return language == Languages.english ? voices : "\(language): \(voices)"
    }
}

/// The pinned release program: kyutai-labs/pocket-tts's GitHub release v4.0.1, its macOS arm64 asset (the Rust
/// `pocket-tts` 3.3.0), checked by the tarball's sha256, then the two files kept from it by theirs (the program is
/// ad-hoc signed: its sha256 is its pin). Their folder (`path`) is beside the voice service's own program, in her
/// root-owned release: a voice service of hers checks it and never fetches or changes it.
public struct PocketRelease: PinnedFolder, Equatable {
    public let url: URL
    public let tarballSHA256: String
    /// The most the download takes (the tarball is 7,696,220 bytes).
    public let maxTarballBytes: Int
    /// The tarball's one folder.
    public let top: String
    public let path: String
    /// The files kept, by name: the program and its licence.
    public let sha256: [String: String]
    /// The program's name in the folder.
    public var program: String { "pocket-tts" }

    public init(url: URL, tarballSHA256: String, maxTarballBytes: Int, top: String, path: String, sha256: [String: String]) {
        self.url = url
        self.tarballSHA256 = tarballSHA256
        self.maxTarballBytes = maxTarballBytes
        self.top = top
        self.path = path
        self.sha256 = sha256
    }

    /// The tarball's members kept, as `tar` names them.
    public var members: [String] { sha256.keys.sorted().map { "\(top)/\($0)" } }

    public static let v4_0_1 = PocketRelease(
        url: URL(string: "https://github.com/kyutai-labs/pocket-tts/releases/download/v4.0.1/pocket-tts-3.3.0-aarch64-apple-darwin.tar.gz")!,
        tarballSHA256: "ee9ca6c7c000ee72c5846e3a8ba01d08f56636d94a614d2259c3f12b8fd69afa",
        maxTarballBytes: 16 << 20,
        top: "pocket-tts-3.3.0-aarch64-apple-darwin",
        path: PocketTTS.folder,
        sha256: [
            "pocket-tts": "447d95181e133b3fda268d248126041722cf1bdf767093f44f2a0d047ab192be",
            "LICENSE": "23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3",
        ]
    )
}

/// One of Pocket's voices as the bridge names it (`voice`) in a language (`language`: English, or one of the six jane
/// speaks, `PocketTTS.languages`): its model, the model's own voice it speaks in (`speaks`) and its model's state's key
/// in `/health` (English's "pocket", on either model; a language's "pocket_<code>"). English's files are fetched at the
/// voice service's start (`atStart`), a language's only the first time a line is asked in it (idle until then, and
/// never checked at a start: only the language in use is). The voices of one model agree on all (a test).
public struct PocketSpeech: Equatable, Sendable {
    public let voice: String
    public let model: PocketModel
    public let speaks: String
    public let health: String
    public let language: String

    public init(
        voice: String, model: PocketModel, speaks: String = "eve", health: String, language: String = Languages.english
    ) {
        self.voice = voice
        self.model = model
        self.speaks = speaks
        self.health = health
        self.language = language
    }

    /// Its model's files fetched at the voice service's start: English's (the Mac's model), never a language's.
    public var atStart: Bool { language == Languages.english }
}

/// A Pocket voice: its state file and its fixed gain to Kokoro af_heart's level. The state is the model's own (so no
/// voice is cloned here: the weights cannot clone without the encoder, which the repository without voice cloning
/// leaves out), or one that ships with the voice service (`bundled`: heart's, one a model, each made once with Kyutai's
/// cloning model and exported as a state like these, 27 Oct).
public struct PocketVoice: Equatable, Sendable {
    public let pin: HubPin
    /// Its file ships with the voice service (`PocketTTS.resources`, beside its program; the repository's
    /// `Sources/voice-service/Voices`): never fetched, no network; copied into its folder beside the other voices
    /// (`pin.local`, private to her: 0600, its folder 0700) and checked there by its sha256 as they are. Its pin has no
    /// repository and no commit; its file's name is its own in the bundle (one bundle for both models' files).
    public let bundled: Bool
    /// dB, measured on the spike's 5 lines (24 Oct): eve on the 100M model -21.2 dBFS RMS against af_heart's -24.8
    /// (-3.5 dB); on the 18M model -17.6 in the spike, and -17.0 through the voice service (speech frames pooled over
    /// the 5 lines, af_heart -24.6; with -7.0 eve came out 0.7 dB above it, with -7.5 0.2 dB); its raw peaks are at
    /// full scale. The voices differ by up to 20 dB. jane's own (25 Oct, the same 5 lines pooled, against eve's level):
    /// on the 18M -5.0 (raw from the program -19.77 dBFS RMS against eve's -17.16; with it -24.77 against eve's -24.66),
    /// on the 100M -4.0 (raw -20.48 against eve's -20.88; -24.48 against -24.38). vera's (25 Oct, the 100M only, through
    /// the voice service): -22.86 at -4.0 against af_heart's -24.6, so -5.5. alba's and michael's (26 Oct, through the
    /// voice service, 4 rounds; jane -24.60 on the 18M, -24.34 on the 100M in the same runs): the 18M's alba -22.47 at
    /// -5.0, so -7.0, michael -25.71 at -5.0, so -4.0; the 100M's alba -21.67 at -4.0, so -7.0, michael -24.07 at -4.0,
    /// so -4.5. heart's (27 Oct, the 100M, through the voice service, 4 rounds, jane -24.34 in the same runs): -29.24
    /// at -4.0 (the quietest voice), -24.94 at +0.5 and -24.48 at +1.0 (more of its quiet frames counted as speech as
    /// the gain grows), so +1.0; its peak -8.1 dBFS there, so no clipping. The 18M's heart (29 Oct) is not measured: 0,
    /// jane's -5.0 there plus the 5 dB the 100M's heart is above the 100M's jane, never a boost (no clipping added).
    public let gainDB: Double

    public init(pin: HubPin, gainDB: Double, bundled: Bool = false) {
        self.pin = pin
        self.gainDB = gainDB
        self.bundled = bundled
    }

    public var file: String { pin.sha256.keys.sorted()[0] }
}

/// A Pocket TTS model from kyutai/pocket-tts-without-voice-cloning (not gated: no account), each file at the commit
/// the release program names, in a folder of its own: the weights, the tokenizer and each voice.
public struct PocketModel: Equatable, Sendable {
    /// The release's name for it (`--language`), never passed: the config names only local files.
    public let name: String
    public let weights: HubPin
    public let tokenizer: HubPin
    public let voices: [String: PocketVoice]
    /// The model's config as the release program embeds it, with `{weights}` and `{tokenizer}` where its `hf://`
    /// addresses were (and no `weights_path_without_voice_cloning`): with them the program reaches the gated
    /// repository at its start.
    public let config: String
    /// The most a download takes of one file.
    public let maxFileBytes: Int

    public init(name: String, weights: HubPin, tokenizer: HubPin, voices: [String: PocketVoice], config: String, maxFileBytes: Int) {
        self.name = name
        self.weights = weights
        self.tokenizer = tokenizer
        self.voices = voices
        self.config = config
        self.maxFileBytes = maxFileBytes
    }

    /// Every pin: the weights, the tokenizer, the voices by name.
    public var pins: [HubPin] { [weights, tokenizer] + voices.keys.sorted().compactMap { voices[$0]?.pin } }
    /// The pins of the voices that ship with the voice service (`PocketVoice.bundled`), by name: copied, never fetched.
    public var bundled: [HubPin] { voices.keys.sorted().compactMap { voices[$0] }.filter(\.bundled).map(\.pin) }

    /// The config's text for the checked files (paths in single quotes: YAML's plain text would end at " #").
    public func configText(weights: URL, tokenizer: URL) -> String {
        func quoted(_ url: URL) -> String { "'" + url.path.replacingOccurrences(of: "'", with: "''") + "'" }
        return config.replacingOccurrences(of: "{weights}", with: quoted(weights))
            .replacingOccurrences(of: "{tokenizer}", with: quoted(tokenizer))
    }

    static func pin(_ revision: String, _ folder: String, _ file: String, _ sha256: String, _ local: String) -> HubPin {
        HubPin(repo: "kyutai/pocket-tts-without-voice-cloning", revision: revision, folder: folder, sha256: [file: sha256],
               local: "\(PocketTTS.folder)/\(local)")
    }

    /// The 100M English model (`english`, the same weights as `english_2026-10`), its tokenizer, heart, jane, eve, vera,
    /// alba and michael: every English voice on a Mac with Gemma 4 26B (owner, 26 Oct; its eve was the voice eve-full
    /// beside the 18M's eve, 24 Oct), fetched at the voice service's start (heart's file copied from beside it).
    public static let english = PocketModel(
        name: "english",
        weights: pin("a7056f505a4a8ca72d8b5fb1440071d6f1a35739", "languages/english", "model.safetensors",
                     "4c56cbd171c63e8a26ea61f5d48b8a0cd8f3c51f4652c0ab9a19bfcced8c931d", "english/weights"),
        tokenizer: pin("00eac05ed3d16bdc3f6b5d598874019c34a89214", "languages/english", "tokenizer.json",
                       "f498428e1eafee50492f7be13dc9bfafcfc12e508cd0eb1b01c92ecd5d8c6687", "english/tokenizer"),
        voices: [
            "eve": PocketVoice(
                pin: pin("a3055dcb533cf2ec90458008a01e7cef550bae2a", "languages/english/embeddings", "eve.safetensors",
                         "d31289743fdf306517925afb91e99c27b4d336c127013d2c4d1212791a41cf37", "english/eve"),
                gainDB: -3.5),
            "jane": PocketVoice(
                pin: pin("a7056f505a4a8ca72d8b5fb1440071d6f1a35739", "languages/english/embeddings", "jane.safetensors",
                         "f802b7b0ce91578dc5448e1cdf4827695e64a34cc6b2f95989dc335082522887", "english/jane"),
                gainDB: -4.0),
            // VCTK p229 (25 Oct, the 100M only: "let me test Vera on the big model"): as jane's, the official download
            // at the weights' own revision
            "vera": PocketVoice(
                pin: pin("a7056f505a4a8ca72d8b5fb1440071d6f1a35739", "languages/english/embeddings", "vera.safetensors",
                         "fe136ebb9c88b65803e73ab8fc04fcc7faaa5aa790559bdab3d2382826eea44a", "english/vera"),
                gainDB: -5.5),
            // Alba MacKenna (`kyutai/tts-voices` alba-mackenna/casual.wav, CC BY 4.0; owner, 25 Oct: "lets have vera and
            // alba available as well by default in Estia"; 26 Oct, a male voice to his ear: "alba & michael for males,
            // both on small and big"): as vera's, the official download at the weights' own revision (6,244,152 bytes)
            "alba": PocketVoice(
                pin: pin("a7056f505a4a8ca72d8b5fb1440071d6f1a35739", "languages/english/embeddings", "alba.safetensors",
                         "5850f417028fb2bf6de31e5e395e56ee7a21ac0e52ef31aebd4a6a2847d72e9c", "english/alba"),
                gainDB: -7.0),
            // VCTK p360 (owner, 26 Oct): as alba's, the official download at the weights' own revision (7,128,888 bytes)
            "michael": PocketVoice(
                pin: pin("a7056f505a4a8ca72d8b5fb1440071d6f1a35739", "languages/english/embeddings", "michael.safetensors",
                         "9f6538650ed3b664ab847d05244f51f04db22ce7ea00e2b285f51d91d6308ce0", "english/michael"),
                gainDB: -4.5),
            // Kokoro-82M's af_heart (hexgrad, Apache 2.0) cloned onto this model (owner, 27 Oct: "call the voice Heart",
            // "not af_heart, do heart, and make it default"; its file in the public repository: "yes"): Kyutai's cloning
            // model (kyutai/pocket-tts at db04f0e6, CC BY 4.0: the same weights as a7056f5's but for the voice encoder)
            // took af_heart's audio and the pinned program 3.3.0's export-voice wrote its state (8,652,024 bytes); the
            // 18M has its own clone (`english18M`)
            "heart": PocketVoice(
                pin: HubPin(repo: "", revision: "", folder: "",
                            sha256: ["heart.safetensors": "60e5e6cbcac2f61554adcfc1ffd9830308802a3685db49489f5a14e8e65b5425"],
                            local: "\(PocketTTS.folder)/english/heart"),
                gainDB: 1.0, bundled: true),
        ],
        config: englishConfig,
        maxFileBytes: 256 << 20
    )

    /// The 18M English model after reward fine-tuning (`english_18m_refl_2026-10`), its tokenizer (a sentencepiece
    /// model, `tokenizer.model`, where the 100M one reads `tokenizer.json`), heart, jane, eve, alba and michael: about 5
    /// times lighter (24 Oct: a whole sentence in 48-158 ms), every English voice on a Mac with Gemma 4 12B (owner, 26
    /// Oct), fetched at the voice service's start (heart's file copied from beside it). No vera: the owner chose her on
    /// the 100M only.
    public static let english18M = PocketModel(
        name: "english_18m_refl_2026-10",
        weights: pin("a7056f505a4a8ca72d8b5fb1440071d6f1a35739", "languages/english_18m_refl_2026-10", "model.safetensors",
                     "4e7a106c1ac5e3c43226565df57523184e574127c4c30c1ea6aadc4ba5e9bafb", "english_18m_refl/weights"),
        tokenizer: pin("08ffc58775e62aad9409511b9591c9af626270f3", "languages/english_18m_refl_2026-10", "tokenizer.model",
                       "d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6", "english_18m_refl/tokenizer"),
        voices: [
            "eve": PocketVoice(
                pin: pin("a3055dcb533cf2ec90458008a01e7cef550bae2a", "languages/english_18m_refl_2026-10/embeddings",
                         "eve.safetensors", "f89ae14c3f3b6053aefbe3bfa5236a0c61b25bb6b45e15b8a6d0fa6f06979a3a",
                         "english_18m_refl/eve"),
                gainDB: -7.5),
            // VCTK p339 (25 Oct): the official download at the weights' own revision, whose commit recomputed the voice
            // states with them (eve's, at the release program's older revision, are the states of the models before:
            // a long sentence without a comma ran away at 70 words with those, at 100 with these)
            "jane": PocketVoice(
                pin: pin("a7056f505a4a8ca72d8b5fb1440071d6f1a35739", "languages/english_18m_refl_2026-10/embeddings",
                         "jane.safetensors", "796f7860a0a2a0ca0b57fda2c7fa7a4cb1e5cd77517ced8c35177b5686f15af3",
                         "english_18m_refl/jane"),
                gainDB: -5.0),
            // alba and michael as jane (owner, 26 Oct: "both on small and big"): the official downloads at the weights'
            // own revision (4,163,992 and 4,753,816 bytes)
            "alba": PocketVoice(
                pin: pin("a7056f505a4a8ca72d8b5fb1440071d6f1a35739", "languages/english_18m_refl_2026-10/embeddings",
                         "alba.safetensors", "e1aa12e17245952e044853867deef5e77ec4474df119cbeefa4ed2a3d878a7b1",
                         "english_18m_refl/alba"),
                gainDB: -7.0),
            "michael": PocketVoice(
                pin: pin("a7056f505a4a8ca72d8b5fb1440071d6f1a35739", "languages/english_18m_refl_2026-10/embeddings",
                         "michael.safetensors", "0c2ce79817211a343f784db121570e66e658f146eabc4ccd68fcdbb01b4556e3",
                         "english_18m_refl/michael"),
                gainDB: -4.0),
            // Kokoro-82M's af_heart cloned onto this model from the same 14 s of af_heart's audio as the 100M's, exported
            // with the 18M model (27 Oct, 5,768,848 bytes; owner, 29 Oct: "just make the small heart and we test it
            // later", "on 16gb make Heart the default as well"), shipped beside the 100M's under its own name. Its
            // questions end flatter here (27 Oct: a question's end -0.1 semitones against the 100M's +3.2). Its gain is
            // not measured yet (`PocketVoice.gainDB`)
            "heart": PocketVoice(
                pin: HubPin(repo: "", revision: "", folder: "",
                            sha256: ["heart-18m.safetensors": "0d9e5334433271a849bfa97b1a8626cb690446a98ea3008ba016704bc51cd2bb"],
                            local: "\(PocketTTS.folder)/english_18m_refl/heart"),
                gainDB: 0.0, bundled: true),
        ],
        config: english18MConfig,
        maxFileBytes: 256 << 20
    )
}

/// English as Pocket reads it: every English pass (`SpokenEnglish`: chords, numbers, times, dates, units), then
/// Kokoro's lexicon as text. Pocket reads plain text, no phonemes: a capitals word meant as its letters
/// (`EnglishLexicon.spelledWords`: "ID", "ETA", "WHO") is written as its letters with hyphens ("I-D"). Measured 24 Oct
/// (eve, through Whisper): "I.D." came out "eye. D"; "W-H-O" took as long as the letters "WHO" (2.4 s), "who" 1.8 s;
/// "WHO is it?" came out "Though is it?".
public enum PocketText {
    public static func spoken(_ text: String, before: String = "") -> String {
        PocketPace.paced(letters(SpokenEnglish.spoken(text, before: before)))
    }

    /// Another language as jane reads it: every pass Supertonic-3 gets for it (`SpokenLanguage`: numbers, times,
    /// dates, units and chords in its own words), then the guard for a long stretch with no pause mark.
    public static func spoken(_ text: String, language: String) -> String {
        PocketPace.paced(SpokenLanguage.spoken(text, language: language), language: language)
    }

    private static let spelled = Pattern(
        #"(?<![\p{L}\p{N}.])("# + EnglishLexicon.spelledWords.joined(separator: "|") + #")(?![\p{L}\p{N}])"#)

    static func letters(_ text: String) -> String {
        spelled.replace(text) { g in g[1].map(String.init).joined(separator: "-") }
    }
}

/// The program's WAV (24 kHz mono 16-bit PCM; written to a pipe, so its sizes are placeholders) as Kokoro's own
/// format, at a fixed gain, its quiet before the first sound cut to `leadSeconds` (24 Oct, on a call's path: eve's
/// clips began with 0.45-0.5 s of near silence against af_heart's 0.3 s, and her first sound came 155-280 ms later).
public enum PocketWAV {
    public struct Invalid: Error, Equatable {
        public let reason: String
    }

    static let headerBytes = 44
    /// The quiet kept before the first sound.
    public static let leadSeconds = 0.1
    /// The first sound: the first 10 ms whose RMS reaches -50 dBFS (the program's near silence and a breath before
    /// a word stay under it).
    static let soundLevel: Float = 0.003_16

    public static func speech(_ wav: Data, gainDB: Double) throws -> Data {
        let bytes = [UInt8](wav.prefix(headerBytes))
        guard bytes.count == headerBytes else { throw Invalid(reason: "short") }
        func ascii(_ at: Int, _ text: String) -> Bool { Array(bytes[at..<at + 4]) == Array(text.utf8) }
        func le16(_ at: Int) -> Int { Int(bytes[at]) | Int(bytes[at + 1]) << 8 }
        func le32(_ at: Int) -> Int { le16(at) | le16(at + 2) << 16 }
        guard ascii(0, "RIFF"), ascii(8, "WAVE"), ascii(12, "fmt "), le32(16) == 16, ascii(36, "data") else {
            throw Invalid(reason: "not a WAV")
        }
        guard le16(20) == 1, le16(22) == 1, le32(24) == SpeechAudio.sampleRate, le16(34) == 16 else {
            throw Invalid(reason: "not 24 kHz mono 16-bit PCM")
        }
        let pcm = wav.dropFirst(headerBytes)
        let count = pcm.count / 2
        guard count > 0 else { throw Invalid(reason: "no audio") }
        let gain = Float(pow(10, gainDB / 20))
        var samples = [Float](repeating: 0, count: count)
        pcm.withUnsafeBytes { raw in
            for index in 0..<count {
                let value = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: index * 2, as: Int16.self))
                samples[index] = Float(value) / 32767 * gain
            }
        }
        return pcm16WAV(Array(samples[firstSound(samples)...]), sampleRate: SpeechAudio.sampleRate)
    }

    /// Where the kept quiet before the first sound starts: `leadSeconds` before it, 0 when it starts sooner or there is
    /// no sound.
    static func firstSound(_ samples: [Float]) -> Int {
        let frame = SpeechAudio.sampleRate / 100
        let lead = Int(leadSeconds * Double(SpeechAudio.sampleRate))
        var start = 0
        while start + frame <= samples.count {
            let energy = samples[start..<start + frame].reduce(Float(0)) { $0 + $1 * $1 }
            if (energy / Float(frame)).squareRoot() >= soundLevel { return max(0, start - lead) }
            start += frame
        }
        return 0
    }
}

/// One run of the program: under sandbox-exec, with nothing of the service's own environment.
public enum PocketRun {
    public static let sandboxExec = "/usr/bin/sandbox-exec"

    /// No network at all, no other program started (no fork, and no exec but sandbox-exec's own of the program:
    /// the review of 25 Oct, a program that replaced itself with another ran), no file written but /dev/null, her
    /// home unreadable but Pocket's own folder (its config, the model and the voices) and the program's (in her
    /// release; in a checkout run as the owner, inside that home), no signal but to itself and no service of the Mac's
    /// reached by name (the review of 26 Oct, R2-F1: a sandboxed kill ended another program of the same account, and
    /// `(deny signal (target others))` lets one in the same process group through, as the voice service's child is;
    /// the program needs no mach-lookup: `RealPocket` passed without one, both English models and the six languages).
    /// `HER`, `POCKET`, `PROGRAM` (its folder) and `PROGRAM_FILE` (the program) are real paths.
    public static let profile = """
        (version 1)
        (allow default)
        (deny network*)
        (deny process-fork)
        (deny process-exec*)
        (allow process-exec (literal (param "PROGRAM_FILE")))
        (deny file-write*)
        (allow file-write* (literal "/dev/null"))
        (deny file-read* (subpath (param "HER")))
        (allow file-read* (subpath (param "POCKET")))
        (allow file-read* (subpath (param "PROGRAM")))
        (deny signal)
        (allow signal (target self))
        (deny mach-lookup)
        """

    /// The command line: the text comes on stdin, the WAV goes to stdout; int8, from the config's files only. The
    /// program by its real path: one reached through a link in her home (a checkout's `.build/release`) would need
    /// the link read, which the sandbox refuses (24 Oct: exit 71).
    public static func arguments(program: URL, config: URL, voice: URL, pocket: URL, her: URL) -> [String] {
        let program = real(program)
        return [
            sandboxExec, "-p", profile, "-D", "HER=\(real(her).path)", "-D", "POCKET=\(real(pocket).path)",
            "-D", "PROGRAM=\(program.deletingLastPathComponent().path)", "-D", "PROGRAM_FILE=\(program.path)",
            program.path, "generate", "-q", "--quantize", "--config", config.path, "--voice", voice.path,
            "--text", "-", "--output-path", "-",
        ]
    }

    /// The run's whole environment: one thread for its tensors (`RAYON_NUM_THREADS`; it decodes on a second), PATH
    /// /usr/bin:/bin, Pocket's folder as HOME, Hugging Face's library offline; nothing else of the service's.
    public static func environment(pocket: URL) -> [String: String] {
        ["PATH": "/usr/bin:/bin", "HOME": real(pocket).path, "RAYON_NUM_THREADS": "1", "HF_HUB_OFFLINE": "1"]
    }

    /// A folder as the sandbox matches it: every link resolved (/var/estia is /private/var/estia).
    public static func real(_ url: URL) -> URL {
        guard let resolved = realpath(url.path, nil) else { return url }  // never resolvingSymlinksInPath: it drops /private
        defer { free(resolved) }
        return URL(fileURLWithPath: String(cString: resolved))
    }
}

/// A file as `lstat` sees it: a checked file whose stamp changed is checked again before the next run.
public struct FileStamp: Equatable, Sendable {
    let device: Int64
    let inode: UInt64
    let size: Int64
    let modified: Int64
    let changed: Int64

    /// nil for a missing file or one that is not a plain file (a link is never followed).
    public static func of(_ url: URL) -> FileStamp? {
        var info = stat()
        guard lstat(url.path, &info) == 0 else { return nil }
        return FileStamp(info)
    }

    /// The stamp of the bytes checked (the review of 26 Oct, R2-F3: a stamp taken by path after the hash could be a
    /// file put there in between): the file opened once without following a link, its bytes hashed through that
    /// descriptor and its stamp from the same descriptor, before and after the read. nil for a missing file, a link or
    /// anything but a plain file, other bytes than `sha256`, or a file that changed while it was read.
    public static func checked(_ url: URL, sha256: String) -> FileStamp? {
        let fd = open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { return nil }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer { try? handle.close() }
        func stamp() -> FileStamp? {
            var info = stat()
            return fstat(fd, &info) == 0 ? FileStamp(info) : nil
        }
        guard let before = stamp(), (try? sha256Hex(reading: handle)) == sha256, stamp() == before else { return nil }
        return before
    }

    private init?(_ info: stat) {
        guard info.st_mode & S_IFMT == S_IFREG else { return nil }
        func nanos(_ time: timespec) -> Int64 { Int64(time.tv_sec) * 1_000_000_000 + Int64(time.tv_nsec) }
        self.init(device: Int64(info.st_dev), inode: UInt64(info.st_ino), size: Int64(info.st_size),
                  modified: nanos(info.st_mtimespec), changed: nanos(info.st_ctimespec))
    }

    private init(device: Int64, inode: UInt64, size: Int64, modified: Int64, changed: Int64) {
        self.device = device
        self.inode = inode
        self.size = size
        self.modified = modified
        self.changed = changed
    }
}

/// A line's WAV, and the voice that said it in the asked one's place (nil: the asked voice said it). The response
/// says so in `X-Voice-Stand-In` (`headers`): the bridge never keeps such a clip of a canned line (the review of 24
/// Oct: a greeting Kokoro said while Pocket was still fetching stayed af_heart's on every later call).
public struct SpokenLine: Equatable, Sendable {
    public let wav: Data
    public let standIn: String?

    public init(wav: Data, standIn: String? = nil) {
        self.wav = wav
        self.standIn = standIn
    }

    public static let standInHeader = "X-Voice-Stand-In"

    /// The response's extra header: the stand-in's voice, when there is one.
    public var headers: [(String, String)] { standIn.map { [(Self.standInHeader, $0)] } ?? [] }
}

/// Pocket's audio for a line, or its stand-in's (`instead`: in English Kokoro's af_heart, or am_michael for a male
/// voice, in another language Supertonic-3's F1; `standIn` its name, `SpeechRequest.standInVoice`) when Pocket cannot
/// speak it; never silence, unless the stand-in may not say it (`standInAllowed` false, `SpeechRequest.standInAllowed`:
/// a call's line after its turn's one Kokoro line, or one before her answer or before a device question; owner, 28
/// Oct, calls-03): then `instead` never runs and the line is refused (`VoiceError.noStandIn`). `log` gets one line once the stand-in has tried or was not asked: why Pocket did
/// not say it, and whether the stand-in did (the review of 25 Oct: "said it" was logged before it tried, so a stand-in
/// that failed too, offline at its first download, left a line nobody said), naming the stand-in's engine by its voice
/// (Kokoro's ids, else Supertonic-3).
public enum SpeechFallback {
    public static func speak(
        pocket: () async throws -> Data, instead: () async throws -> Data, standIn: String = SpeechRequest.defaultVoice,
        standInAllowed: Bool = true, log: (String) -> Void
    ) async throws -> SpokenLine {
        let why: String
        do {
            return SpokenLine(wav: try await pocket())
        } catch {
            why = "speaking: Pocket TTS did not say this line (\(reason(error))); "
                + "\(SpeechRequest.isKokoro(standIn) ? "Kokoro" : "Supertonic-3") (\(standIn))"
        }
        guard standInAllowed else {
            log(why + " not asked: the call asked for no stand-in")
            throw VoiceError.noStandIn
        }
        do {
            let wav = try await instead()
            log(why + " said it")
            return SpokenLine(wav: wav, standIn: standIn)
        } catch {
            log(why + " could not say it either")
            throw error
        }
    }

    /// A failure as the log names it: ours by its words, anything else by its type. Never text or a path.
    public static func reason(_ error: Error) -> String {
        if let failed = error as? PocketFailure { return failed.description }
        if let invalid = error as? PocketWAV.Invalid { return invalid.reason }
        if let voice = error as? VoiceError { return voice.code }
        return "\(type(of: error))"
    }
}

/// Why a run did not speak.
public enum PocketFailure: Error, Equatable, CustomStringConvertible {
    /// Pocket is not ready (its state).
    case notReady(String)
    case exit(Int32)
    case signal(Int32)
    case timeout
    case tooLarge
    /// A pinned file changed since it was checked: never run.
    case changed

    public var description: String {
        switch self {
        case .notReady(let state): "not ready (\(state))"
        case .exit(let code): "exit \(code)"
        case .signal(let number): "signal \(number)"
        case .timeout: "timeout"
        case .tooLarge: "too much audio"
        case .changed: "files changed"
        }
    }
}

// MARK: - The configs, as the release program 3.3.0 embeds them (`english`, `english_18m_refl_2026-10`)

private let englishConfig = """
    # Default English model: identical weights to english_2026-10.yaml.

    weights_path: {weights}
    # Human evals preferred this model at 0.3 (equal WER/similarity/speech rate).
    default_temperature: 0.3


    # Replace characters that are absent from the training data.
    replace_characters:
      '’': "'"
      '‘': "'"

    flow_lm:
      insert_bos_before_voice: true
      dtype: float32
      flow:
        depth: 6
        dim: 512
      transformer:
        d_model: 1024
        hidden_scale: 4
        max_period: 10000
        num_heads: 16
        num_layers: 6
      lookup_table:
        dim: 1024
        n_bins: 4000
        tokenizer: tokenizers
        tokenizer_path: {tokenizer}

    mimi:
      dtype: float32
      sample_rate: 24000
      inner_dim: 32
      outer_dim: 512
      channels: 1
      frame_rate: 12.5
      seanet:
        dimension: 512
        channels: 1
        n_filters: 64
        n_residual_layers: 1
        ratios:
        - 6
        - 5
        - 4
        kernel_size: 7
        residual_kernel_size: 3
        last_kernel_size: 3
        dilation_base: 2
        pad_mode: constant
        compress: 2
      transformer:
        d_model: 512
        num_heads: 8
        num_layers: 2
        layer_scale: 0.01
        context: 250
        dim_feedforward: 2048
        input_dimension: 512
        output_dimensions:
        - 512
      quantizer:
        dimension: 32
        output_dimension: 512

    """

private let english18MConfig = """
    # english_18m_2026-10 after reward fine-tuning (REFL).

    weights_path: {weights}
    default_temperature: 0.3
    # Replace characters that are absent from the training data.
    replace_characters:
      '–': ','
      '—': ','
      '’': "'"
      '‘': "'"

    flow_lm:
      insert_bos_before_voice: true
      dtype: float32
      flow:
        depth: 4
        dim: 192
        type: drifting
      transformer:
        d_model: 512
        hidden_scale: 1
        max_period: 10000
        num_heads: 8
        num_layers: 8
        attn_dim: null
        num_kv_heads: null
      lookup_table:
        dim: 512
        n_bins: 4000
        tokenizer: sentencepiece
        tokenizer_path: {tokenizer}
        rank: null
      z_dim: 64
    mimi:
      dtype: float32
      sample_rate: 24000
      inner_dim: 32
      outer_dim: 128
      channels: 1
      frame_rate: 12.5
      seanet:
        dimension: 512
        channels: 1
        n_filters: 64
        n_residual_layers: 1
        ratios:
        - 6
        - 5
        - 4
        kernel_size: 7
        residual_kernel_size: 3
        last_kernel_size: 3
        dilation_base: 2
        pad_mode: constant
        compress: 2
      transformer:
        d_model: 512
        num_heads: 8
        num_layers: 2
        layer_scale: 0.01
        context: 250
        dim_feedforward: 2048
        input_dimension: 512
        output_dimensions:
        - 512
      quantizer:
        dimension: 32
        output_dimension: 128
      decoder_seanet:
        dimension: 128
        channels: 1
        n_filters: 32
        n_residual_layers: 1
        ratios:
        - 6
        - 5
        - 4
        kernel_size: 3
        residual_kernel_size: 3
        last_kernel_size: 3
        dilation_base: 2
        pad_mode: constant
        compress: 2
      decoder_transformer:
        d_model: 128
        num_heads: 4
        num_layers: 12
        layer_scale: 0.01
        context: 50
        dim_feedforward: 384
        input_dimension: 128
        output_dimensions:
        - 128
    default_eos_threshold: -1.0

    """

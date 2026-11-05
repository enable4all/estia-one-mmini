import Foundation
import Testing
@testable import VoiceCore

private func request(_ fields: [String: Any]) throws -> SpeechRequest {
    try SpeechRequest(json: try JSONSerialization.data(withJSONObject: fields))
}

/// A WAV as the release program writes it to a pipe: the sizes are its placeholders (2,000,000,000 bytes of data).
private func programWAV(_ samples: [Int16], rate: Int = 24_000, channels: Int = 1, bits: Int = 16) -> Data {
    func le32(_ value: Int) -> [UInt8] { (0..<4).map { UInt8((value >> (8 * $0)) & 0xFF) } }
    func le16(_ value: Int) -> [UInt8] { (0..<2).map { UInt8((value >> (8 * $0)) & 0xFF) } }
    var header: [UInt8] = Array("RIFF".utf8) + le32(36 + 2_000_000_000) + Array("WAVEfmt ".utf8)
    header += le32(16) + le16(1) + le16(channels) + le32(rate) + le32(rate * 2) + le16(2) + le16(bits)
    header += Array("data".utf8) + le32(2_000_000_000)
    return Data(header) + samples.map(\.littleEndian).withUnsafeBytes { Data($0) }
}

private func pcm(_ wav: Data) -> [Int16] {
    wav.dropFirst(44).withUnsafeBytes { raw in
        (0..<(raw.count / 2)).map { Int16(littleEndian: raw.loadUnaligned(fromByteOffset: $0 * 2, as: Int16.self)) }
    }
}

@Suite struct PocketRouting {
    /// Owner, 3 Nov 2026 ("remove kokoro and supertonic"): every voice is Pocket TTS's: heart, jane, eve, vera, alba and
    /// michael by name (owner, 24-27 Oct), a Kokoro voice id an older bridge sends as the closest one (michael for a
    /// male one, heart for any other: `config.pocket_voice`), any other name heart, the default. eve-full is the
    /// bridge's other name for eve (owner, 26 Oct), never a voice of the service's.
    @Test func everyVoiceIsPocketsByItsName() throws {
        for voice in PocketTTS.order {
            #expect(try request(["input": "Hi.", "voice": voice]).voice == voice, "\(voice)")
            #expect(try request(["input": "Hi.", "voice": voice, "language": "en"]).voice == voice, "\(voice)")
        }
        #expect(try request(["input": "Hi.", "voice": "af_heart"]).voice == "heart")
        #expect(try request(["input": "Hi.", "voice": "am_michael"]).voice == "michael")
        #expect(SpeechRequest.defaultVoice == "heart")
        for odd in ["Eve", "eve2", "../eve", "eve ", "marius", "Jane", "jane2", "../jane", "jane ", "jan", "Eve-Full",
                    "eve-full", "eve-full ", "eve_full", "Vera", "vera ", "vera-full", "Alba", "alba ", "alba-full",
                    "Michael", "michael ", "mike", "Heart", "heart ", "hearts", "af-heart", "../heart"] {
            #expect(try request(["input": "Hi.", "voice": odd]).voice == SpeechRequest.defaultVoice, "\(odd)")
        }
    }

    /// Each voice speaks on the Mac's English model (`PocketVoices.speech`), the model's voice of its name. vera is the
    /// 100M's alone (owner, 25 Oct: "let me test Vera on the big model"): beside the 18M a line asked in vera is said in
    /// heart, the default, as the bridge reads it there (`config.voice_here`), where Kokoro's af_heart said it as a
    /// stand-in before 3 Nov 2026. A name no Pocket voice has is none.
    @Test func veraBesideThe18MIsHeart() {
        let standard = PocketVoices(english: .english), small = PocketVoices(english: .english18M)
        for voice in PocketTTS.order {
            #expect(standard.speech(voice)?.speaks == voice && standard.speech(voice)?.model == PocketModel.english, "\(voice)")
        }
        for voice in PocketTTS.order where voice != "vera" {
            #expect(small.speech(voice)?.speaks == voice && small.speech(voice)?.model == PocketModel.english18M, "\(voice)")
        }
        #expect(small.speech("vera") != nil && small.speech("vera") == small.speech("heart"))
        #expect(small.speech("vera")?.speaks == "heart" && small.speech("vera", language: "en") == small.speech("heart"))
        for odd in ["af_heart", "eve-full", "", "Vera"] {
            #expect(standard.speech(odd) == nil && small.speech(odd) == nil, "\(odd)")
        }
    }

    /// Pocket reads plain text: the English passes unchanged (numbers, times), then the capitals meant as letters
    /// written as letters with hyphens (24 Oct, eve through Whisper: "I.D." came out "eye. D", "W-H-O" as long as the
    /// letters "WHO", "WHO is it?" as "Though is it?"); the passes alone keep the word.
    @Test func pocketGetsTheEnglishPassesAndTheLettersAsText() throws {
        let eve = try request(["input": "Show your ID at 9:30, the ETA is 5 min.", "voice": "eve"])
        #expect(eve.spokenInput == "Show your I-D at nine thirty in the morning, the E-T-A is five minutes.")
        #expect(SpokenEnglish.spoken("Show your ID at 9:30, the ETA is 5 min.")
            == "Show your ID at nine thirty in the morning, the ETA is five minutes.")
        #expect(PocketText.spoken("The WHO said so. Bring your ID.") == "The W-H-O said so. Bring your I-D.")
        #expect(PocketText.spoken("An id, who knows, WHOM, IDs, I.D. cards.") == "An id, who knows, WHOM, IDs, I.D. cards.")
        let call = try request(["input": "Shall I call at 7:30?", "voice": "eve", "context": "It closes tonight."])
        #expect(call.spokenInput == "Shall I call at seven thirty?")
    }
}

@Suite struct PocketAudio {
    /// The program's stream (placeholder sizes) becomes the bridge's format (24 kHz mono 16-bit) at the fixed gain;
    /// louder is clipped.
    @Test func theProgramsWAVComesOutInTheBridgesFormatAtItsGain() throws {
        let out = try PocketWAV.speech(programWAV([0, 1000, -1000, 32767, -32768]), gainDB: -6.0)
        #expect(out.count == 44 + 10)
        #expect(String(decoding: out.prefix(4), as: UTF8.self) == "RIFF")
        #expect(out[40..<44].elementsEqual([10, 0, 0, 0]))  // the real size
        let half = pcm(out)
        #expect(half[0] == 0 && abs(Int(half[1]) - 501) <= 1 && abs(Int(half[2]) + 501) <= 1)
        #expect(abs(Int(half[3]) - 16422) <= 2 && abs(Int(half[4]) + 16422) <= 3)
        let loud = pcm(try PocketWAV.speech(programWAV([20000, -20000]), gainDB: 6.0))
        #expect(loud == [32767, -32767])
        #expect(pcm(try PocketWAV.speech(programWAV([1234, 7]) + Data([9]), gainDB: 0)) == [1234, 7])  // an odd byte left
    }

    /// 24 Oct, on a call's path: eve's clips began with 0.45-0.5 s of near silence (af_heart's 0.3 s), and her first
    /// sound came 155-280 ms after af_heart's. Pocket's quiet before its first sound is cut to `PocketWAV.leadSeconds`;
    /// nothing else of the clip changes, and a clip without a sound keeps all of it.
    @Test func pocketsQuietBeforeItsFirstSoundIsCut() throws {
        let quiet = [Int16](repeating: 3, count: 12_000)  // 0.5 s of the program's near silence
        let breath = [Int16](repeating: 40, count: 2_400)  // 0.1 s of a breath, still under the first sound's level
        let tone = (0..<4_800).map { Int16(($0 / 12) % 2 == 0 ? 3000 : -3000) }  // 0.2 s of sound
        let cut = pcm(try PocketWAV.speech(programWAV(quiet + breath + tone + quiet), gainDB: 0))
        let kept = Int(PocketWAV.leadSeconds * 24_000)
        #expect(cut.count == kept + tone.count + quiet.count)
        #expect(cut.prefix(kept).allSatisfy { abs(Int($0)) < 100 } && cut[kept] == 3000)
        let early = pcm(try PocketWAV.speech(programWAV(Array(quiet.prefix(1_200)) + tone), gainDB: 0))
        #expect(early.count == 1_200 + tone.count)  // under the lead already: as it was
        #expect(pcm(try PocketWAV.speech(programWAV(quiet), gainDB: 0)).count == quiet.count)  // no sound: all kept
    }

    @Test func anythingButTheProgramsWAVIsRefused() {
        #expect(throws: PocketWAV.Invalid(reason: "short")) { try PocketWAV.speech(Data(repeating: 0, count: 20), gainDB: 0) }
        #expect(throws: PocketWAV.Invalid(reason: "no audio")) { try PocketWAV.speech(programWAV([]), gainDB: 0) }
        #expect(throws: PocketWAV.Invalid(reason: "not 24 kHz mono 16-bit PCM")) {
            try PocketWAV.speech(programWAV([1, 2], rate: 44_100), gainDB: 0)
        }
        #expect(throws: PocketWAV.Invalid(reason: "not 24 kHz mono 16-bit PCM")) {
            try PocketWAV.speech(programWAV([1, 2], channels: 2), gainDB: 0)
        }
        var text = programWAV([1, 2])
        text.replaceSubrange(0..<4, with: Array("RIFX".utf8))
        #expect(throws: PocketWAV.Invalid(reason: "not a WAV")) { try PocketWAV.speech(text, gainDB: 0) }
    }
}

@Suite struct PocketPins {
    private static var hex: Regex<Substring> { /^[0-9a-f]{64}$/ }
    private static var commit: Regex<Substring> { /^[0-9a-f]{40}$/ }

    /// The spike's pins (pins-sha256.txt, 24 Oct): the release tarball, the program, and every file of the model in
    /// use, each by its commit; no file the program would fetch itself.
    @Test func everyFileIsPinnedByItsSha256AtItsCommit() throws {
        let release = PocketTTS.release
        #expect(release.url.absoluteString == "https://github.com/kyutai-labs/pocket-tts/releases/download/v4.0.1/pocket-tts-3.3.0-aarch64-apple-darwin.tar.gz")
        #expect(release.tarballSHA256 == "ee9ca6c7c000ee72c5846e3a8ba01d08f56636d94a614d2259c3f12b8fd69afa")
        #expect(release.sha256["pocket-tts"] == "447d95181e133b3fda268d248126041722cf1bdf767093f44f2a0d047ab192be")
        #expect(release.members == ["pocket-tts-3.3.0-aarch64-apple-darwin/LICENSE", "pocket-tts-3.3.0-aarch64-apple-darwin/pocket-tts"])
        // owner, 26 Oct: one model speaks every English Pocket voice on a Mac, by its Gemma (`PocketTTS.english`): the
        // 100M beside Gemma 4 26B, the 18M beside 12B; jane the default, eve, vera (the 100M only), and alba and michael
        // for a male name ("alba & michael for males, both on small and big"); each fetched at the voice service's start.
        // eve-full is the bridge's other name for eve and never reaches the voice service.
        // heart first (owner, 27 Oct: "heart, jane, eve, vera (female); alba, michael (male)"), on both models (owner,
        // 29 Oct: "just make the small heart and we test it later")
        #expect(PocketTTS.order == ["heart", "jane", "eve", "vera", "alba", "michael"])
        #expect(PocketTTS.voices == ["heart", "jane", "eve", "vera", "alba", "michael"])
        let standard = PocketVoices(english: .english), small = PocketVoices(english: .english18M)
        #expect(standard.speakers.map(\.voice) == ["heart", "jane", "eve", "vera", "alba", "michael"])
        #expect(small.speakers.map(\.voice) == ["heart", "jane", "eve", "alba", "michael"])
        for tier in [standard, small] {
            for speech in tier.speakers {
                #expect(speech.model == tier.english && speech.speaks == speech.voice, "\(speech.voice)")
                #expect(speech.atStart && speech.health == "pocket" && speech.language == "en", "\(speech.voice)")
            }
            #expect(tier.speech("eve-full") == nil && tier.speech("marius") == nil && tier.speech("am_michael") == nil)
            // every voice its model pins is one of the bridge's
            #expect(Set(tier.english.voices.keys) == Set(tier.speakers.map(\.voice)))
        }
        // vera (VCTK p229, owner, 25 Oct): the 100M model only (heart says her lines on the 18M: PocketRouting). heart
        // on the 18M too (owner, 29 Oct), its own clone
        #expect(small.speakers.allSatisfy { $0.voice != "vera" } && PocketModel.english18M.voices["vera"] == nil)
        #expect(small.speech("vera")?.speaks == "heart")
        #expect(small.speech("heart")?.model == PocketModel.english18M && small.speech("heart")?.speaks == "heart")
        let eve = try #require(small.speech("eve"))
        let full = try #require(standard.speech("eve"))
        #expect(eve.model.name == "english_18m_refl_2026-10")
        #expect(eve.model.weights.sha256 == ["model.safetensors": "4e7a106c1ac5e3c43226565df57523184e574127c4c30c1ea6aadc4ba5e9bafb"])
        #expect(eve.model.tokenizer.sha256 == ["tokenizer.model": "d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6"])  // a public file hash, not a key (gitleaks:allow)
        #expect(eve.model.voices["eve"]?.pin.sha256 == ["eve.safetensors": "f89ae14c3f3b6053aefbe3bfa5236a0c61b25bb6b45e15b8a6d0fa6f06979a3a"])
        // jane (VCTK p339, owner, 25 Oct): the official download at the weights' own revision (a7056f5: its commit
        // recomputed the voice states with the weights; eve's are at the release program's older revision, a3055dcb, and
        // a long sentence without a comma ran away at 70 words with those against 100 with these), each model's own state
        #expect(eve.model.voices["jane"]?.pin.sha256 == ["jane.safetensors": "796f7860a0a2a0ca0b57fda2c7fa7a4cb1e5cd77517ced8c35177b5686f15af3"])
        #expect(eve.model.voices["jane"]?.pin.revision == eve.model.weights.revision)
        #expect(eve.model.voices["eve"]?.pin.revision == "a3055dcb533cf2ec90458008a01e7cef550bae2a")  // unchanged
        #expect(eve.model.voices["jane"]?.pin.folder == "languages/english_18m_refl_2026-10/embeddings")
        #expect(eve.model.voices["jane"]?.pin.local == "pocket-tts/english_18m_refl/jane")
        #expect(eve.model.config.contains("tokenizer: sentencepiece\n") && !eve.model.config.contains("tokenizers"))
        #expect(full.model.name == "english")
        #expect(PocketModel.english.weights.sha256 == ["model.safetensors": "4c56cbd171c63e8a26ea61f5d48b8a0cd8f3c51f4652c0ab9a19bfcced8c931d"])
        #expect(PocketModel.english.tokenizer.sha256 == ["tokenizer.json": "f498428e1eafee50492f7be13dc9bfafcfc12e508cd0eb1b01c92ecd5d8c6687"])  // a public file hash, not a key (gitleaks:allow)
        #expect(PocketModel.english.voices["eve"]?.pin.sha256 == ["eve.safetensors": "d31289743fdf306517925afb91e99c27b4d336c127013d2c4d1212791a41cf37"])
        #expect(PocketModel.english.voices["jane"]?.pin.sha256 == ["jane.safetensors": "f802b7b0ce91578dc5448e1cdf4827695e64a34cc6b2f95989dc335082522887"])
        #expect(PocketModel.english.voices["jane"]?.pin.revision == PocketModel.english.weights.revision)
        #expect(PocketModel.english.voices["jane"]?.pin.folder == "languages/english/embeddings")
        #expect(PocketModel.english.voices["jane"]?.pin.local == "pocket-tts/english/jane")
        // vera as jane: the official download at the 100M weights' own revision (a7056f5), sha256 from that download
        #expect(PocketModel.english.voices["vera"]?.pin.sha256 == ["vera.safetensors": "fe136ebb9c88b65803e73ab8fc04fcc7faaa5aa790559bdab3d2382826eea44a"])
        #expect(PocketModel.english.voices["vera"]?.pin.revision == PocketModel.english.weights.revision)
        #expect(PocketModel.english.voices["vera"]?.pin.folder == "languages/english/embeddings")
        #expect(PocketModel.english.voices["vera"]?.pin.local == "pocket-tts/english/vera")
        // alba as vera: the official download at the 100M weights' own revision (a7056f5), 6,244,152 bytes, sha256
        // computed from that download (26 Oct; Hugging Face's listing gives the same)
        #expect(PocketModel.english.voices["alba"]?.pin.sha256 == ["alba.safetensors": "5850f417028fb2bf6de31e5e395e56ee7a21ac0e52ef31aebd4a6a2847d72e9c"])
        #expect(PocketModel.english.voices["alba"]?.pin.revision == PocketModel.english.weights.revision)
        #expect(PocketModel.english.voices["alba"]?.pin.folder == "languages/english/embeddings")
        #expect(PocketModel.english.voices["alba"]?.pin.local == "pocket-tts/english/alba")
        // michael (VCTK p360) on both, alba on the 18M too (owner, 26 Oct): each the official download at its model's
        // weights' own revision (a7056f5), sha256 computed from that download (26 Oct; Hugging Face's listing gives the
        // same): the 100M's michael 7,128,888 bytes, the 18M's 4,753,816, the 18M's alba 4,163,992
        let pins: [(PocketModel, String, String, String)] = [
            (.english, "michael", "9f6538650ed3b664ab847d05244f51f04db22ce7ea00e2b285f51d91d6308ce0", "english"),
            (.english18M, "michael", "0c2ce79817211a343f784db121570e66e658f146eabc4ccd68fcdbb01b4556e3", "english_18m_refl"),
            (.english18M, "alba", "e1aa12e17245952e044853867deef5e77ec4474df119cbeefa4ed2a3d878a7b1", "english_18m_refl"),
        ]
        for (model, name, sha, local) in pins {
            let pin = try #require(model.voices[name]?.pin)
            #expect(pin.sha256 == ["\(name).safetensors": sha], "\(model.name) \(name)")
            #expect(pin.revision == model.weights.revision && pin.revision == "a7056f505a4a8ca72d8b5fb1440071d6f1a35739")
            #expect(pin.folder == "languages/\(model.name)/embeddings" && pin.local == "pocket-tts/\(local)/\(name)")
        }
        #expect(PocketModel.english.config.contains("tokenizer: tokenizers\n"))
        // each model's fixed gain brings eve to af_heart's level (24 Oct; measured through the voice service on the 5 spike
        // lines, speech frames pooled: af_heart -24.6 dBFS RMS, eve 100M -21.2 raw, 18M -17.0 raw)
        #expect(eve.model.voices["eve"]?.gainDB == -7.5)
        #expect(full.model.voices["eve"]?.gainDB == -3.5)
        // jane's own gain brings her to eve's level (25 Oct; the 5 spike lines, speech frames pooled): the 18M raw from
        // the program -19.77 dBFS RMS against eve's -17.16 (eve -24.66 at -7.5, so jane -5.0: -24.77); the 100M raw
        // -20.48 against eve's -20.88 (eve -24.38 at -3.5, so jane -4.0: -24.48). Within 0.5 dB of eve either way.
        #expect(eve.model.voices["jane"]?.gainDB == -5.0)
        #expect(PocketModel.english.voices["jane"]?.gainDB == -4.0)
        // vera's own gain brings her to af_heart's level (25 Oct; the 5 spike lines, speech frames pooled, through the
        // voice service, 4 rounds): -22.86 dBFS RMS at -4.0 against af_heart's -24.6, so -5.5 (eve -24.47, eve-full -24.25)
        #expect(PocketModel.english.voices["vera"]?.gainDB == -5.5)
        // alba's and michael's own gains bring them to af_heart's level (26 Oct; the 5 spike lines, speech frames pooled,
        // through the voice service on port 8151, 4 rounds; jane -24.60 on the 18M and -24.34 on the 100M in the same
        // runs): the 18M's alba -22.47 dBFS RMS at -5.0, so -7.0, its michael -25.71 at -5.0, so -4.0; the 100M's alba
        // -21.67 at -4.0, so -7.0, its michael -24.07 at -4.0, so -4.5
        #expect(PocketModel.english18M.voices["alba"]?.gainDB == -7.0)
        #expect(PocketModel.english18M.voices["michael"]?.gainDB == -4.0)
        #expect(PocketModel.english.voices["alba"]?.gainDB == -7.0)
        #expect(PocketModel.english.voices["michael"]?.gainDB == -4.5)
        // heart's own gain brings it to af_heart's level (27 Oct; the 5 spike lines, speech frames pooled, through the
        // voice service on port 8151, 4 rounds, jane -24.34 in the same runs as on 26 Oct): -29.24 dBFS RMS at -4.0,
        // -24.94 at +0.5, -24.48 at +1.0 against af_heart's -24.6, so +1.0 (its peak -8.1 dBFS there: no clipping)
        #expect(PocketModel.english.voices["heart"]?.gainDB == 1.0)
        // heart (owner, 27 Oct: "call the voice Heart"; shipping its file in the public repository: "yes"): Kokoro's
        // af_heart cloned onto Pocket TTS with Kyutai's cloning model (kyutai/pocket-tts at db04f0e6, the same weights as
        // a7056f5's but for the voice encoder) and exported by the pinned program 3.3.0's export-voice: 8,652,024 bytes.
        // It ships with the voice service (`bundled`: its own resources, never fetched, no network), is copied into its
        // folder beside the other voices and checked by its sha256 as they are
        let heart = try #require(PocketModel.english.voices["heart"])
        #expect(heart.pin.sha256 == ["heart.safetensors": "60e5e6cbcac2f61554adcfc1ffd9830308802a3685db49489f5a14e8e65b5425"])
        #expect(heart.bundled && heart.pin.local == "pocket-tts/english/heart")
        #expect(PocketModel.english.voices.filter { $0.value.bundled }.map(\.key) == ["heart"])
        #expect(PocketModel.english.bundled == [heart.pin])
        #expect(PocketModel.english.pins.contains(heart.pin))
        // heart on the 18M (owner, 29 Oct: "just make the small heart and we test it later"): the same af_heart audio
        // cloned onto the 18M model on 27 Oct, 5,768,848 bytes, shipped beside the 100M's under its own name, copied into
        // the 18M's folder and checked by its sha256 the same way. Its gain, 0 dB (jane's -5.0 plus the 5 dB the 100M's
        // heart is above the 100M's jane), measured 31 Oct (the 5 spike lines pooled, through the voice service on port
        // 8151 with --pocket 18M, 4 rounds): -24.71 dBFS RMS against jane's -24.60 in the same runs, its peak -6.5 dBFS,
        // so 0 (not heard yet: the owner tests it later)
        let small18 = try #require(PocketModel.english18M.voices["heart"])
        #expect(small18.pin.sha256 == ["heart-18m.safetensors": "0d9e5334433271a849bfa97b1a8626cb690446a98ea3008ba016704bc51cd2bb"])
        #expect(small18.bundled && small18.pin.local == "pocket-tts/english_18m_refl/heart")
        #expect(PocketModel.english18M.voices.filter { $0.value.bundled }.map(\.key) == ["heart"])
        #expect(PocketModel.english18M.bundled == [small18.pin] && PocketModel.english18M.pins.contains(small18.pin))
        #expect(small18.gainDB == 0.0)
        #expect(Set(heart.pin.sha256.keys).isDisjoint(with: small18.pin.sha256.keys))  // one bundle, a name each
        #expect(PocketTTS.bundle == "voice-service_voice-service.bundle")
        // the review of 27 Oct (swift-voice-1): the build lays the bundle out in two ways, by its build system. The
        // default build of Swift 6.4 (swiftbuild) puts the file in Contents/Resources; the native build, Swift 6.2's
        // default, at the bundle's top. Both are read, as Foundation reads the bundle
        let top = FileManager.default.temporaryDirectory.appendingPathComponent("bundles-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: top) }
        let (deep, flat) = (top.appendingPathComponent("deep"), top.appendingPathComponent("flat"))
        let deepFolder = deep.appendingPathComponent(PocketTTS.bundle).appendingPathComponent("Contents/Resources")
        try FileManager.default.createDirectory(at: deepFolder, withIntermediateDirectories: true)
        try Data("h".utf8).write(to: deepFolder.appendingPathComponent("heart.safetensors"))
        let flatFolder = flat.appendingPathComponent(PocketTTS.bundle)
        try FileManager.default.createDirectory(at: flatFolder, withIntermediateDirectories: true)
        try Data("h".utf8).write(to: flatFolder.appendingPathComponent("heart.safetensors"))
        #expect(PocketTTS.resources(beside: deep).standardizedFileURL.path == deepFolder.standardizedFileURL.path)
        #expect(PocketTTS.resources(beside: flat).standardizedFileURL.path == flatFolder.standardizedFileURL.path)
        // no bundle there: its top (nothing to copy from either way)
        #expect(PocketTTS.resources(beside: URL(fileURLWithPath: "/r/tools/voice/.build/release")).path
            == "/r/tools/voice/.build/release/voice-service_voice-service.bundle")
        // each model's own folders: never one on top of the other
        #expect(Set(PocketModel.english.pins.map(\.path)).isDisjoint(with: PocketModel.english18M.pins.map(\.path)))
        for model in [PocketModel.english, PocketModel.english18M] {
            // each model's heart, jane, eve, alba and michael; vera on the 100M only
            let voices: Set<String> = model == PocketModel.english ? ["heart", "jane", "eve", "vera", "alba", "michael"]
                : ["heart", "jane", "eve", "alba", "michael"]
            #expect(Set(model.voices.keys) == voices)
            for pin in model.pins where !model.bundled.contains(pin) {
                #expect(pin.repo == "kyutai/pocket-tts-without-voice-cloning")
                #expect(pin.revision.wholeMatch(of: Self.commit) != nil)
                #expect(pin.sha256.count == 1 && pin.sha256.values.allSatisfy { $0.wholeMatch(of: Self.hex) != nil })
                #expect(pin.path.hasPrefix("pocket-tts/") && pin.fileURL(pin.sha256.keys.first!).scheme == "https")
            }
            for pin in model.bundled {  // a file of the voice service's own: only its sha256 and its folder
                #expect(pin.sha256.count == 1 && pin.sha256.values.allSatisfy { $0.wholeMatch(of: Self.hex) != nil })
                #expect(pin.path.hasPrefix("pocket-tts/") && pin.repo.isEmpty && pin.revision.isEmpty)
            }
            #expect(Set(model.pins.map(\.path)).count == model.pins.count)  // a folder each
        }
        // the program beside the voice service in her root-owned release (the deploy puts it there), never in her home
        #expect(release.path == "pocket-tts")
    }

    /// The voices of one model share its speaker (one a model, `PocketTTS.models`: its files, their checks, its state
    /// and its health key), so they agree on the health key and on when its files are fetched; each speaks a voice its
    /// model has.
    @Test func theVoicesOfOneModelShareItsSpeaker() {
        let standard = PocketVoices(english: .english), small = PocketVoices(english: .english18M)
        for tier in [standard, small] {
            // the English model, then one a language (jane in each of the six: owner, 25 Oct)
            // heart stands for either English model's speaker
            #expect(tier.models.map(\.voice) == ["heart"] + Array(repeating: "jane", count: 6))
            #expect(tier.models.map(\.language) == ["en", "es", "it", "pt", "nl", "de", "fr"])
            #expect(tier.models.first?.model == tier.english)
            #expect(tier.voices(of: .german) == ["jane"] && tier.name(of: .german) == "de: jane")
            for speech in tier.all {
                let first = tier.models.first { $0.model == speech.model }
                #expect(first?.health == speech.health && first?.atStart == speech.atStart, "\(speech.voice)")
                #expect(speech.model.voices[speech.speaks] != nil, "\(speech.voice)")
            }
            #expect(Set(tier.models.map(\.health)).count == tier.models.count)
            // a speaker and its config file each by the model's name (`Engines.pockets`, `PocketSpeaker.config`): a
            // name used twice would stop the voice service at every start (the review of 25 Oct)
            #expect(Set(tier.models.map(\.model.name)).count == tier.models.count)
        }
        #expect(standard.voices(of: .english) == ["heart", "jane", "eve", "vera", "alba", "michael"])
        #expect(standard.name(of: .english) == "heart, jane, eve, vera, alba, michael")
        #expect(small.voices(of: .english18M) == ["heart", "jane", "eve", "alba", "michael"])
        #expect(standard.voices(of: .english18M).isEmpty && small.voices(of: .english).isEmpty)  // one model a Mac
        #expect(small.name(of: .english18M) == "heart, jane, eve, alba, michael")
        #expect(standard.size == "100M" && small.size == "18M")
    }

    /// Owner, 26 Oct: "the Pocket model follows the Gemma tier": a Mac setup gives Gemma 4 26B (30 GB of memory or more,
    /// `setup.choose_model`) speaks the 100M model, a smaller one (Gemma 4 12B) the 18M. A check built here names one
    /// (`--pocket 100M` or `--pocket 18M`, `ServiceArguments`).
    @Test func theModelFollowsTheMacsGemma() {
        #expect(PocketTTS.standardMemoryGiB == 30)
        let gib: UInt64 = 1 << 30
        for (memory, model) in [(64, PocketModel.english), (36, .english), (32, .english), (30, .english),
                                (24, .english18M), (18, .english18M), (16, .english18M), (8, .english18M)] {
            #expect(PocketTTS.english(memory: UInt64(memory) * gib) == model, "\(memory) GB")
        }
        #expect(PocketTTS.english(memory: 30 * gib - 1) == PocketModel.english18M)
        #expect(PocketTTS.english(size: "100M") == PocketModel.english)
        #expect(PocketTTS.english(size: "18M") == PocketModel.english18M)
        for odd in ["100m", "18m", "big", "small", "", "english"] { #expect(PocketTTS.english(size: odd) == nil, "\(odd)") }
    }

    /// With the release's own config the program reaches the gated repository at its start (the spike): the config
    /// names only the checked local files.
    @Test func theConfigNamesOnlyTheCheckedLocalFiles() {
        for model in [PocketModel.english, .english18M] + PocketTTS.languages.map(\.model) {
            let text = model.configText(weights: URL(fileURLWithPath: "/private/var/x/Application Support/w.safetensors"),
                                        tokenizer: URL(fileURLWithPath: "/private/var/x/it's/t.json"))
            #expect(!text.contains("hf://") && !text.contains("http") && !text.contains("{"))
            #expect(!text.contains("weights_path_without_voice_cloning"))
            #expect(text.contains("weights_path: '/private/var/x/Application Support/w.safetensors'\n"))
            #expect(text.contains("tokenizer_path: '/private/var/x/it''s/t.json'\n"))
        }
    }
}

@Suite struct PocketRunning {
    /// Every run is sandboxed: no network, no other program, nothing written, her home closed but Pocket's folder and
    /// the program's (by its real path: here /etc/hosts stands in for it); int8 from the local config; the environment
    /// only these four.
    @Test func aRunIsSandboxedOffline() {
        let her = URL(fileURLWithPath: "/tmp")  // links: the sandbox gets their real paths
        let pocket = URL(fileURLWithPath: "/var/tmp")
        let argv = PocketRun.arguments(
            program: URL(fileURLWithPath: "/etc/hosts"), config: URL(fileURLWithPath: "/p/c.yaml"),
            voice: URL(fileURLWithPath: "/p/eve.safetensors"), pocket: pocket, her: her)
        #expect(argv[0] == "/usr/bin/sandbox-exec" && argv[1] == "-p" && argv[2] == PocketRun.profile)
        #expect(argv[3...8] == ["-D", "HER=/private/tmp", "-D", "POCKET=/private/var/tmp", "-D", "PROGRAM=/private/etc"])
        #expect(argv[9...10] == ["-D", "PROGRAM_FILE=/private/etc/hosts"])  // the one program it may start
        #expect(Array(argv[11...]) == ["/private/etc/hosts", "generate", "-q", "--quantize", "--config", "/p/c.yaml",
                                       "--voice", "/p/eve.safetensors", "--text", "-", "--output-path", "-"])
        for rule in ["(deny network*)", "(deny process-fork)", "(deny process-exec*)",
                     #"(allow process-exec (literal (param "PROGRAM_FILE")))"#, "(deny file-write*)",
                     #"(deny file-read* (subpath (param "HER")))"#, #"(allow file-read* (subpath (param "PROGRAM")))"#,
                     "(deny signal)", "(allow signal (target self))", "(deny mach-lookup)"] {
            #expect(PocketRun.profile.contains(rule))
        }
        #expect(PocketRun.environment(pocket: URL(fileURLWithPath: "/private/tmp"))
            == ["PATH": "/usr/bin:/bin", "HOME": "/private/tmp", "RAYON_NUM_THREADS": "1", "HF_HUB_OFFLINE": "1"])
        #expect(!argv.contains("serve") && !argv.contains("--language"))
    }

    /// A file's stamp changes with its bytes or its replacement; a link or a folder has none.
    @Test func aStampSeesAChangedFile() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let file = folder.appendingPathComponent("f")
        try Data("one".utf8).write(to: file)
        let first = try #require(FileStamp.of(file))
        #expect(FileStamp.of(file) == first)
        try Data("two".utf8).write(to: file, options: .atomic)
        #expect(FileStamp.of(file) != first)
        try FileManager.default.createSymbolicLink(at: folder.appendingPathComponent("l"), withDestinationURL: file)
        #expect(FileStamp.of(folder.appendingPathComponent("l")) == nil && FileStamp.of(folder) == nil)
        // the review of 26 Oct (R2-F3): a checked stamp comes from the descriptor its bytes were hashed through; none for
        // other bytes, a link, a folder or a file that is not there
        let two = sha256Hex(Data("two".utf8))
        #expect(FileStamp.checked(file, sha256: two) == FileStamp.of(file) && FileStamp.checked(file, sha256: two) != nil)
        #expect(FileStamp.checked(file, sha256: sha256Hex(Data("one".utf8))) == nil)
        #expect(FileStamp.checked(folder.appendingPathComponent("l"), sha256: two) == nil)
        #expect(FileStamp.checked(folder, sha256: two) == nil)
        #expect(FileStamp.checked(folder.appendingPathComponent("gone"), sha256: two) == nil)
    }

    /// Owner, 3 Nov 2026 ("remove kokoro and supertonic", and for a line Pocket fails: "Nothing"): what Pocket cannot
    /// say (not ready, a changed file, a run that fails, is late or writes too much, an odd WAV) no other voice says.
    /// The reason is logged (never the text) and the line is refused (409, `not_said`), whatever the request asked; the
    /// bridge then sends that answer as text. Before, Kokoro said it (or Supertonic-3 in another language).
    @Test func whatPocketCannotSayNoOtherVoiceSays() async throws {
        var said: [String] = []
        let ok = try await PocketLine.speak({ Data("p".utf8) }, log: { said.append($0) })
        #expect(ok == Data("p".utf8) && said.isEmpty)
        let failures: [(Error, String)] = [
            (PocketFailure.exit(1), "exit 1"), (PocketFailure.timeout, "timeout"), (PocketFailure.changed, "files changed"),
            (PocketFailure.notReady("loading"), "not ready (loading)"), (PocketFailure.signal(9), "signal 9"),
            (PocketFailure.tooLarge, "too much audio"), (PocketWAV.Invalid(reason: "not a WAV"), "not a WAV"),
            (VoiceError.notReady, "not_ready"),
        ]
        for (failure, reason) in failures {
            said = []
            await #expect(throws: VoiceError.notSaid, "\(reason)") {
                try await PocketLine.speak({ throw failure }, log: { said.append($0) })
            }
            #expect(said == ["speaking: Pocket TTS did not say this line (\(reason)); no other voice says it"])
        }
        let refused = String(decoding: VoiceError.notSaid.response, as: UTF8.self)
        #expect(refused.hasPrefix("HTTP/1.1 409 Conflict\r\n") && refused.hasSuffix(#"{"error":"not_said"}"#))
    }

    /// The review of 24 Oct: one run could hold her line for 30 s whatever its length. Its limit is a measured base and
    /// a few ms a character (a call's sentence took 0.15-0.8 s; 1,500 characters under 5 s with Gemma generating), 10
    /// ms since the 100M speaks every voice on a Mac with Gemma 4 26B (our own install, 26 Oct: a 608-character line
    /// took 3.7-5.7 s with other programs busy, and 5 ms a character stopped it in 2 runs of 4).
    @Test func aRunsTimeLimitFollowsItsText() {
        for (characters, seconds) in [(4, 2.04), (150, 3.5), (1500, 17.0)] {
            #expect(abs(PocketTTS.runSeconds(for: String(repeating: "a", count: characters)) - seconds) < 1e-9)
        }
    }
}

/// 25 Oct: the 18M model runs away on a long stretch with no pause mark (a clip of 0.45 s a word to the program's
/// budget, its end garbled, no stand-in, nothing for the time limit to catch): eve's at 404-434 characters (21-22 s of
/// speech) with simple and with long words alike (85 simple words, 39 long ones), jane's later. `PocketPace` joins
/// the pieces of a long stretch with a comma, well under that (`PocketPace.limit`).
@Suite struct PocketPacing {
    /// "tree" words weigh 5 each (a four-letter word and its space), so a stretch's weight is easy to count.
    private static func trees(_ count: Int) -> [String] { Array(repeating: "tree", count: count) }

    private static let runOn = """
        tomorrow you have the dentist at nine fifteen and then the call with the office and after that your sister \
        is coming with the paperwork for the apartment and if you want to finish the shopping first it would be best \
        to leave before eight and take the bus because the train is slow at that hour and the weather looks dry but \
        cloudy until the afternoon when a little rain may come in from the west and stay until the evening so I would \
        take a light jacket and an umbrella and maybe the blue scarf and I can remind you tonight if you like or \
        tomorrow morning at seven if that suits you better
        """

    @Test func aStretchWithinTheLimitIsLeftAsItIs() {
        let exact = Self.trees(40).joined(separator: " ")  // 40 words of 5: the limit
        #expect(PocketPace.runs(exact) == [PocketPace.limit])
        #expect(PocketPace.paced(exact) == exact)
        let spaced = "  Two   spaces,  and\ttabs. \n"
        #expect(PocketPace.paced(spaced) == spaced)  // untouched byte for byte
        #expect(PocketPace.paced("") == "" && PocketPace.paced("Yes?") == "Yes?")
    }

    @Test func aStretchOverTheLimitIsCutInTwoAtTheWordNearestTheMiddle() {
        let text = Self.trees(41).joined(separator: " ")  // 205: two pieces, the middle at 102
        let expected = (Self.trees(19) + ["tree,"] + Self.trees(21)).joined(separator: " ")
        #expect(PocketPace.paced(text) == expected)
    }

    @Test func theBreakGoesBeforeAConjunctionNearTheMiddleNotOnlyTheNearestWord() {
        let near = (Self.trees(23) + ["and"] + Self.trees(17)).joined(separator: " ")  // the "and" is 13 off the middle
        #expect(PocketPace.paced(near) == (Self.trees(22) + ["tree,", "and"] + Self.trees(17)).joined(separator: " "))
        let far = (Self.trees(3) + ["and"] + Self.trees(37)).joined(separator: " ")  // too far: the nearest word instead
        #expect(PocketPace.paced(far).contains("and,") == false && PocketPace.paced(far).contains("tree, tree") == true)
        for word in ["but", "or", "so", "because", "which", "with", "for", "to", "that"] {
            let text = (Self.trees(23) + [word] + Self.trees(17)).joined(separator: " ")
            #expect(PocketPace.paced(text).contains("tree, \(word) tree"), "\(word)")
        }
    }

    @Test func aBreakNeverLeavesAWordThatNeedsTheNextOneAtThePiecesEnd() {
        let text = (Self.trees(20) + ["at", "that"] + Self.trees(19)).joined(separator: " ")
        let cut = PocketPace.paced(text)
        #expect(cut.contains(", ") && !cut.contains("at,") && !cut.contains("that,"))
        let phrase = (Self.trees(20) + ["a"] + Self.trees(20)).joined(separator: " ")  // not before "a", never after it
        #expect(!PocketPace.paced(phrase).contains("a,") && !PocketPace.paced(phrase).contains(", a "))
        let object = (Self.trees(19) + ["remind", "you"] + Self.trees(19)).joined(separator: " ")
        #expect(!PocketPace.paced(object).contains("remind,") && !PocketPace.paced(object).contains(", you"))
        let wants = (Self.trees(21) + ["want", "to", "leave"] + Self.trees(18)).joined(separator: " ")
        #expect(!PocketPace.paced(wants).contains("want,") && !PocketPace.paced(wants).contains("to,"))
    }

    @Test func aNumberIsNeverCut() {
        let text = (Self.trees(24) + ["one", "hundred", "and", "twelve", "euros"] + Self.trees(19)).joined(separator: " ")
        let cut = PocketPace.paced(text)
        #expect(cut.contains(", ") && cut.contains("one hundred and twelve"))
        for inside in ["one,", "hundred,", "and,", ", hundred", ", and twelve", ", twelve"] { #expect(!cut.contains(inside), "\(inside)") }
        let time = (Self.trees(24) + ["a", "quarter", "to", "nine", "tomorrow"] + Self.trees(19)).joined(separator: " ")
        #expect(PocketPace.paced(time).contains("a quarter to nine"))
        let digits = (Self.trees(22) + ["room", "4021", "5873"] + Self.trees(20)).joined(separator: " ")
        #expect(PocketPace.paced(digits).contains("4021 5873"))
        // a number alone over the limit is still cut (nothing else to break at): the safe length comes first
        let only = Array(repeating: "twelve forty eight", count: 20).joined(separator: " ")
        #expect(PocketPace.runs(PocketPace.paced(only)).allSatisfy { $0 <= PocketPace.limit })
    }

    @Test func everyPauseMarkEndsAStretch() {
        let half = Self.trees(30).joined(separator: " ")  // 150 each: over the limit together, never alone
        for mark in [",", ";", ":", ".", "?", "!", "...", "\u{2026}"] {
            let text = half + mark + " " + half
            #expect(PocketPace.paced(text) == text, "\(mark)")
        }
        for joined in [half + " - " + half, half + " \u{2014} " + half, half + "\u{2014}" + half, half + " \u{2013} " + half] {
            #expect(PocketPace.paced(joined) == joined)
        }
        let quoted = half + "\"? " + half  // the mark closes a quotation
        #expect(PocketPace.paced(quoted) == quoted)
        let hyphen = Self.trees(30).joined(separator: "-") + " " + half  // a hyphen is no pause: one stretch, cut
        #expect(PocketPace.runs(hyphen).count == 1 && PocketPace.paced(hyphen) != hyphen)
    }

    @Test func aSingleHugeWordOrAnAddressHasNoBreakToTake() {
        let address = String(repeating: "a", count: 500)  // one word over the limit: nowhere to cut, never a crash
        #expect(PocketPace.paced(address) == address)
        let two = address + " " + address
        #expect(PocketPace.paced(two) == address + ", " + address)
    }

    @Test func aListWithItsCommasIsLeftWhole() {
        let list = (1...40).map { _ in "tree, house" }.joined(separator: ", ")  // 80 words, a comma every other word
        #expect(PocketPace.paced(list) == list)
        let sentences = (1...12).map { _ in Self.trees(30).joined(separator: " ") + "." }.joined(separator: " ")
        #expect(PocketPace.paced(sentences) == sentences)
    }

    @Test func theRunOnOfTheBugIsCutIntoSafePiecesWithNothingElseChanged() {
        #expect(PocketPace.runs(Self.runOn).count == 1 && PocketPace.runs(Self.runOn)[0] > 2 * PocketPace.limit)
        let cut = PocketPace.paced(Self.runOn)
        #expect(cut.replacingOccurrences(of: ",", with: "") == Self.runOn)  // only commas added
        let pieces = PocketPace.runs(cut)
        #expect(pieces.count == (PocketPace.runs(Self.runOn)[0] + PocketPace.aim - 1) / PocketPace.aim)
        #expect(pieces.allSatisfy { $0 <= PocketPace.limit })
        #expect(PocketPace.paced(cut) == cut)  // pacing is idempotent
        // each comma comes before a conjunction or a preposition here, never in "at nine fifteen" or "at that hour"
        #expect(!cut.contains("at,") && !cut.contains("nine,") && !cut.contains("that,"))
        // 236 words (the 200-word case of the real check): every stretch within the limit, only commas added
        let long = Self.runOn + " " + Self.runOn + " " + Self.runOn
        let longCut = PocketPace.paced(long)
        #expect(longCut.replacingOccurrences(of: ",", with: "") == long)
        #expect(PocketPace.runs(longCut).allSatisfy { $0 <= PocketPace.limit })
    }

    /// The five lines of the spike and the canned lines code speaks are all far under the limit: the same text as
    /// before the pass, and every stretch of what a call's answers usually say too.
    @Test func codesOwnLinesAndTheSpikeLinesAreUnchanged() {
        let lines = [
            "Yes?", "Take your time, I'm here. Say Tiffany when you need me.",
            "It's 14:35 now. Your dentist appointment is tomorrow at 9:15, and it should be sunny, around 18 degrees.",
            "Got it: I'll answer this after the call.", "My voice is ready: you can call me now.",
            "The pharmacy on Main Street closes at 8 tonight, so you still have about two hours. It's a ten-minute walk, or four minutes by car. Shall I remind you at 7:30?",
        ]
        for line in lines {
            #expect(PocketText.spoken(line) == PocketText.letters(SpokenEnglish.spoken(line)), "\(line)")
            #expect(PocketPace.runs(PocketText.spoken(line)).allSatisfy { $0 <= PocketPace.limit })
        }
    }

    /// Every line is Pocket TTS's since 3 Nov 2026 (Kokoro and Supertonic-3 removed), so every one is paced: English by
    /// its words in any voice (a Kokoro id's too: heart's), each of the six languages by its own (jane's, whatever voice
    /// is named).
    @Test func everyLineIsPaced() throws {
        let long = Self.runOn
        for voice in ["eve", "heart", "af_heart", "michael"] {
            let line = try request(["input": long, "voice": voice])
            #expect(line.spokenInput == PocketText.spoken(long) && line.spokenInput.contains(","), "\(voice)")
            #expect(PocketPace.runs(line.spokenInput).allSatisfy { $0 <= PocketPace.limit }, "\(voice)")
        }
        for (voice, code) in [("eve", "nl"), ("jane", "de")] {
            let line = try request(["input": long, "voice": voice, "language": code])
            #expect(line.spokenInput == PocketPace.paced(SpokenLanguage.spoken(long, language: code), language: code), "\(code)")
        }
    }
}

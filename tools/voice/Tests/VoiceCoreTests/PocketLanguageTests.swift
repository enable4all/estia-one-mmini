import Foundation
import Testing
@testable import VoiceCore

private func request(_ fields: [String: Any]) throws -> SpeechRequest {
    try SpeechRequest(json: try JSONSerialization.data(withJSONObject: fields))
}

/// Owner, 25 Oct: "pocket does more languages than English right... lets use those, leave koroko for the ones that
/// Pocket does not do", and after eve and jane in the six: "jane jane, lets have her as default for those languages".
/// Owner, 3 Nov 2026: "remove kokoro and supertonic", and for what they said: "Nothing".
@Suite struct PocketLanguageRouting {
    /// French, German, Italian, Spanish, Portuguese and Dutch are jane's, each on its language's own model, whatever
    /// voice a request names there: jane is their only voice (an older bridge's `language_voice = ""` named its English
    /// voice for Supertonic-3's F1, and the bridge reads that setting as jane now). Korean, Polish, Japanese and Russian,
    /// which Pocket has no model for, have no voice: refused (409, `no_voice`), never said. English as before.
    @Test func janeSpeaksTheSixAndTheFourHaveNoVoice() throws {
        let six: [(String, PocketModel)] = [
            ("es", PocketModel.spanish), ("it", PocketModel.italian), ("pt", PocketModel.portuguese),
            ("nl", PocketModel.dutch), ("de", PocketModel.german), ("fr", PocketModel.french),
        ]
        for tier in [PocketVoices(english: .english), PocketVoices(english: .english18M)] {  // the same six on either
            for (code, model) in six {
                for voice in ["jane", "heart", "eve", "vera", "alba", "michael"] {
                    let speech = tier.speech(voice, language: code)
                    #expect(speech?.model == model && speech?.speaks == "jane" && speech?.voice == "jane", "\(code) \(voice)")
                }
            }
            #expect(tier.speech("jane", language: "en") == tier.speech("jane") && tier.speech("jane")?.model == tier.english)
            for code in ["ko", "pl", "ja", "ru"] { #expect(tier.speech("jane", language: code) == nil, "\(code)") }
        }
        for code in ["fr", "de", "it", "es", "pt", "nl"] {
            let jane = try request(["input": "Hallo.", "voice": "jane", "language": code])
            #expect(jane.language == code && jane.voice == "jane" && PocketTTS.speaks(code), "\(code)")
        }
        for code in ["ko", "pl", "ja", "ru"] {
            #expect(throws: VoiceError.noVoice, "\(code)") { try request(["input": "Hallo.", "voice": "jane", "language": code]) }
            #expect(!PocketTTS.speaks(code), "\(code)")
        }
        #expect(PocketTTS.voices == ["heart", "jane", "eve", "vera", "alba", "michael"])  // the English voices the bridge names
    }

    /// Whatever jane cannot say in a language (not fetched yet, refused, a failed, late or oversized run, an odd WAV) no
    /// other voice says (owner, 3 Nov 2026; Supertonic-3's F1 said it before): the line is refused (409, `not_said`)
    /// and the bridge sends that answer as text. The first line asked in a language starts its fetch, unsaid.
    @Test func whatJaneCannotSayInALanguageNoOtherVoiceSays() async throws {
        var said: [String] = []
        await #expect(throws: VoiceError.notSaid) {
            try await PocketLine.speak({ throw PocketFailure.notReady("idle") }, log: { said.append($0) })
        }
        #expect(said == ["speaking: Pocket TTS did not say this line (not ready (idle)); no other voice says it"])
    }

    /// Pocket TTS has no speed setting (`pocket-tts generate --help`; the review of 25 Oct): the speed an older bridge
    /// sends is taken and never read (Kokoro and Supertonic-3, which read it, were removed on 3 Nov 2026), and a run's
    /// arguments name none. A later speed for Pocket shows up here.
    @Test func aPocketLineIgnoresTheSpeed() throws {
        for (voice, code) in [("jane", "de"), ("jane", "fr"), ("eve", "en"), ("jane", "en")] {
            let slow = try request(["input": "Morgen um 9:15.", "voice": voice, "language": code, "speed": 0.7])
            let fast = try request(["input": "Morgen um 9:15.", "voice": voice, "language": code, "speed": 1.5])
            let none = try request(["input": "Morgen um 9:15.", "voice": voice, "language": code])
            #expect(slow == fast && slow == none && slow.spokenInput == fast.spokenInput, "\(voice) \(code)")
        }
        let folder = URL(fileURLWithPath: "/private/var/x")
        let argv = PocketRun.arguments(program: folder.appendingPathComponent("pocket-tts"), config: folder,
                                       voice: folder, pocket: folder, her: folder)
        #expect(!argv.contains { ["--speed", "--rate", "--tempo", "--length-scale"].contains($0.lowercased()) })
    }

    /// Pocket in a language gets every pass of its own (numbers, times, dates, units, chords in its own words), then
    /// the long-stretch guard.
    @Test func pocketGetsTheLanguagesPassesThenTheGuard() throws {
        let lines = [
            ("de", "Es ist jetzt 14:35 Uhr. Dein Termin ist morgen um 9:15 Uhr, etwa 18 Grad."),
            ("nl", "Het is nu 14:35. Je afspraak is morgen om 9:15, rond de 18 graden."),
            ("fr", "Il est 14:35. Ton rendez-vous est demain à 9:15, environ 18 degrés."),
            ("es", "Son las 14:35. Tu cita es mañana a las 9:15, unos 18 grados."),
            ("it", "Sono le 14:35. L'appuntamento è domani alle 9:15, circa 18 gradi."),
            ("pt", "São 14:35. A tua consulta é amanhã às 9:15, à volta de 18 graus."),
        ]
        for (code, text) in lines {
            let jane = try request(["input": text, "voice": "jane", "language": code])
            #expect(jane.spokenInput == SpokenLanguage.spoken(text, language: code), "\(code)")
            let spoken = jane.spokenInput
            #expect(spoken.allSatisfy { !$0.isNumber }, "\(code): \(spoken)")
        }
        #expect(try request(["input": "Es ist 9:15 Uhr.", "voice": "jane", "language": "de"]).spokenInput
            == "Es ist neun Uhr fünfzehn.")
        let long = Array(repeating: "Morgen kommt die Nachbarin mit dem Paket vorbei und bringt die Unterlagen mit", count: 6)
            .joined(separator: " ")
        let paced = try request(["input": long, "voice": "jane", "language": "de"])
        #expect(paced.spokenInput.contains(",") && SpokenLanguage.spoken(long, language: "de") == long)
        #expect(PocketPace.runs(paced.spokenInput).allSatisfy { $0 <= PocketPace.limit })
    }
}

@Suite struct PocketLanguagePins {
    private static var hex: Regex<Substring> { /^[0-9a-f]{64}$/ }

    /// Each language's full model (never the 24-layer previews), from kyutai/pocket-tts-without-voice-cloning, each file
    /// at one fixed revision with its sha256 computed from the official download (25 Oct): the weights at the revision
    /// the release program 3.3.0 names (1e08e6a2, the upload of the retrained languages, 1 Oct), its tokenizer.json (the
    /// config's `tokenizers`) at the revision it names, and jane's state at the weights' own revision, as English jane's
    /// (its bytes the same there as at the program's voice revision a3055dcb and at main a7056f5: recomputed with these
    /// weights in that upload, unchanged since).
    @Test func eachLanguageIsPinnedAtOneRevisionAFile() throws {
        typealias Pins = (name: String, weights: String, tokenizerAt: String, tokenizer: String, jane: String)
        let program = "1e08e6a23401048648a9fdcfde2f89348215c2a7"
        let tokenizers = "4e1e0a3e611c51c0b4ed8174fc10f32a54644303"  // a public file hash, not a key (gitleaks:allow; privacy-ok: a public sha256)
        let expected: [String: Pins] = [
            "es": ("spanish", "c4988ff376692f2ab1e85b17350755b08f16e49c0b5f0de8b271efa9abd71de7", tokenizers,
                   "9ade09bbdc64ab2b59952b90773fa0890b780ece4fd5aef0d36a00c6ad5f0531",
                   "a1f1c36c155e84da0bce58d635128059413e618324d47f9679aa012d8bdeb9a0"),
            "it": ("italian", "16365c43eaabee9b222cd414d6a48f0976b4800a21fe5d55253f645eff7eeb1f", tokenizers,
                   "4bc1c0d4d54f9480183e94c94cfabf74fd89b0912537d4ef607d1982c4a381ec",
                   "b9787a3a8e3d3e608c493bd24ec2880fe65717807f938a8c1565c126590acdc4"),
            "pt": ("portuguese", "28e20f32515ba8bd0eaec69671f11f8921414fcdc375aa072fa09f46cc1057c5", tokenizers,
                   "b33e269f7f042a763080d71ba087d74d2f6759674b238a723c028e436b1a25ab",
                   "161d4cc6b023d20207220d3687e42d41847c1f823956062646b0c9e22705805e"),
            "nl": ("dutch", "59b5c3249ab05e8b5fcb0a296e9b0cb0c0a20da5e1fd28c04bb90b2175612aeb", tokenizers,
                   "e6f610b98c427641b4ab0e1024dca7843e3d6b620871a14b204932e42c5a1736",
                   "36816d4767fe69a67eeb4d9778c71eea272f34d99d63c2af8c47cf772339b5c1"),
            "de": ("german", "9fe42605604832349e3f495177bde56b77ae8fee73b2ed4b63236092ccde7621", tokenizers,
                   "2d77849811e3d1b6ae80e5b11b203f57e1847901104d8a0573b34f46f8dddb53",
                   "bd1bc91fd38a724b14f5c9cb4d67327f968568beb28d72fe7bb81f0fba75b685"),
            "fr": ("french", "56366cfed6c25bfcd3bf4c61e66e06efaf52e78a5d06e1dcade1c588495bc699",
                   "8843db76457a91db32077edf8dfcd1c0e3e755fd",
                   "6211acffe9cb95172f71f0051f18e26ea305b2b32ea00b331ee976551c70887f",
                   "1482685d8a1a22a7294df650f61293bab353445710e26a60ae057cbf4ad85221"),
        ]
        #expect(PocketTTS.languages.map(\.language) == ["es", "it", "pt", "nl", "de", "fr"])  // `Languages.all`'s order
        for speech in PocketTTS.languages {
            let pins = try #require(expected[speech.language])
            let model = speech.model
            #expect(speech.voice == "jane" && speech.speaks == "jane" && !speech.atStart)
            #expect(speech.health == "pocket_\(speech.language)")
            #expect(model.name == pins.name && Set(model.voices.keys) == ["jane"])
            #expect(model.weights.revision == program && model.weights.folder == "languages/\(pins.name)")
            #expect(model.weights.sha256 == ["model.safetensors": pins.weights])
            #expect(model.tokenizer.revision == pins.tokenizerAt && model.tokenizer.sha256 == ["tokenizer.json": pins.tokenizer])
            let jane = try #require(model.voices["jane"])
            #expect(jane.pin.revision == model.weights.revision && jane.pin.folder == "languages/\(pins.name)/embeddings")
            #expect(jane.pin.sha256 == ["jane.safetensors": pins.jane])
            #expect(model.weights.local == "pocket-tts/\(pins.name)/weights" && jane.pin.local == "pocket-tts/\(pins.name)/jane")
            #expect(model.tokenizer.local == "pocket-tts/\(pins.name)/tokenizer")
            for pin in model.pins {
                #expect(pin.repo == "kyutai/pocket-tts-without-voice-cloning")
                #expect(pin.sha256.values.allSatisfy { $0.wholeMatch(of: Self.hex) != nil })
            }
            #expect(model.config.contains("tokenizer: tokenizers\n") && model.config.contains("    num_layers: 6\n"))
            #expect(!model.config.contains("num_layers: 24"))  // the full model, never the 24-layer preview
        }
        for tier in [PocketVoices(english: .english), PocketVoices(english: .english18M)] {
            let paths = tier.all.flatMap { $0.model.pins.map(\.path) }
            #expect(Set(paths).count == Set(tier.models.flatMap { $0.model.pins.map(\.path) }).count)  // a folder each
            #expect(Set(tier.models.map(\.health)).count == tier.models.count)
        }
    }

    /// Each language's config as the release program 3.3.0 embeds it: its own replaced characters (French and German
    /// without semicolons, French's colon a comma, Spanish without "¡" and "¿"), one architecture, no hf:// address.
    @Test func eachLanguagesConfigIsTheProgramsOwnWithLocalFiles() {
        let french = PocketModel.french.config, german = PocketModel.german.config, spanish = PocketModel.spanish.config
        #expect(french.contains("remove_semicolons: true\n") && french.contains("  ':': ','\n"))
        #expect(german.contains("remove_semicolons: true\n") && german.contains("  '—': ','\n"))
        #expect(spanish.contains("  '¡': ''\n") && !PocketModel.dutch.config.contains("remove_semicolons"))
        for speech in PocketTTS.languages {
            let text = speech.model.configText(weights: URL(fileURLWithPath: "/private/var/x/w.safetensors"),
                                               tokenizer: URL(fileURLWithPath: "/private/var/x/t.json"))
            #expect(!text.contains("hf://") && !text.contains("http") && !text.contains("{"))
            #expect(!text.contains("weights_path_without_voice_cloning"))
            #expect(text.hasPrefix("weights_path: '/private/var/x/w.safetensors'\n"))
            #expect(text.contains("    tokenizer_path: '/private/var/x/t.json'\n"))
        }
    }

    /// jane's gain per language to af_heart's level (-24.6 dBFS RMS, speech frames pooled, through the voice service,
    /// 25 Oct: the language's six call lines and the spike's line, 2 rounds at -4.0: es -27.35, it -25.14, pt -27.70,
    /// nl -24.21, de -23.90, fr -25.19; Supertonic-3's F1 -24.0 to -25.0 in the same run). Pocket's languages differ by
    /// up to 3.8 dB.
    @Test func janesGainPerLanguage() {
        let gains = Dictionary(uniqueKeysWithValues: PocketTTS.languages.map { ($0.language, $0.model.voices["jane"]?.gainDB ?? 0) })
        #expect(gains == ["es": -1.0, "it": -3.5, "pt": -1.0, "nl": -4.5, "de": -4.5, "fr": -3.5])
    }
}

@Suite struct PocketLanguagePacing {
    private static let german = """
        morgen früh kommt die Nachbarin mit dem großen Paket vorbei und bringt die Unterlagen für die Wohnung mit und \
        danach gehen wir zusammen in die Stadt weil der Supermarkt am Bahnhof heute länger geöffnet hat und das Wetter \
        bis zum Nachmittag trocken bleiben soll bevor am Abend ein wenig Regen aus dem Westen kommt
        """

    /// The guard works in the six: a long stretch with no pause mark is cut, best before a conjunction ("und", "weil",
    /// "en", "omdat" ...), never inside a number's words or after an article; within the limit, byte for byte.
    @Test func aLongStretchIsCutBeforeAConjunction() {
        let cut = PocketPace.paced(Self.german, language: "de")
        #expect(cut.replacingOccurrences(of: ",", with: "") == Self.german)
        #expect(PocketPace.runs(cut).allSatisfy { $0 <= PocketPace.limit })
        #expect(cut.contains(", und") || cut.contains(", weil") || cut.contains(", bevor"), "\(cut)")
        // never after an article (a separable particle or a verb may end a piece: "bringt ... mit, und")
        for after in ["die,", "dem,", "der,", "das,", "zum,"] { #expect(!cut.contains(after), "\(after)") }
        let dutch = Array(repeating: "morgen komt de buurvrouw langs met het pakket en de papieren voor het appartement", count: 3)
            .joined(separator: " ")
        let dutchCut = PocketPace.paced(dutch, language: "nl")
        #expect(dutchCut.contains(", en") && PocketPace.runs(dutchCut).allSatisfy { $0 <= PocketPace.limit })
        for after in ["de,", "het,", "een,"] { #expect(!dutchCut.contains(after), "\(after)") }
        let short = "Es ist jetzt vierzehn Uhr fünfunddreißig."
        #expect(PocketPace.paced(short, language: "de") == short)
    }

    /// Portuguese jane runs away sooner (25 Oct, the program direct, a stretch of long words with no comma: the first
    /// failure at 35 words, 201 characters, 234 in the guard's reading length, against Italian's 309, German's 328,
    /// Spanish's 339, Dutch's 400 and French's 649): her limit there is 150, about two thirds of it, as German's 200
    /// is of 328; the other languages keep 200.
    @Test func portugueseHasAShorterLimit() {
        #expect(PocketPace.limit(for: "pt") == 150)
        for code in ["en", "es", "it", "nl", "de", "fr", "ko"] { #expect(PocketPace.limit(for: code) == PocketPace.limit, "\(code)") }
        let stretch = """
            amanhã de manhã passa a vizinha com a enorme caixa da mudança e traz a documentação do contrato de \
            arrendamento para a entrega do apartamento
            """
        #expect(PocketPace.runs(stretch) == [168])  // within the others' 200, over Portuguese's 150
        let cut = PocketPace.paced(stretch, language: "pt")
        #expect(cut != stretch && cut.replacingOccurrences(of: ",", with: "") == stretch, "\(cut)")
        #expect(PocketPace.runs(cut).allSatisfy { $0 <= 150 }, "\(PocketPace.runs(cut))")
        #expect(PocketPace.paced(stretch, language: "es") == stretch)
        let long = Array(repeating: stretch, count: 4).joined(separator: " ")
        #expect(PocketPace.runs(PocketPace.paced(long, language: "pt")).allSatisfy { $0 <= 150 })
    }

    /// Every word a language's lists name can match: a break compares a list's words with a word's core (its letters and
    /// digits, its ends trimmed), so an entry such as French "l'" or "qu'" could never match (the review of 25 Oct; an
    /// elision is glued to its word, "l'heure", and never split from it). jane speaks the six in jane's voice only.
    @Test func everyListedWordCanMatch() {
        for code in ["en", "es", "it", "pt", "nl", "de", "fr"] {
            let words = PocketPace.Words.of(code)
            for list in [words.numbers, words.numberLinks, words.conjunctions, words.prepositions, words.places, words.opens,
                         words.needsNext, [words.and]] {
                for word in list { #expect(PocketPace.core(Substring(word)) == word, "\(code): \(word)") }
            }
        }
        #expect(PocketTTS.languages.allSatisfy { $0.voice == PocketTTS.languageVoice && $0.speaks == PocketTTS.languageVoice })
    }

    /// A number said in words is one piece: "vierzehn Uhr fünfunddreißig", "trente cinq", "treinta y cinco".
    @Test func aNumbersWordsAreNeverCut() {
        let trees = Array(repeating: "Baum", count: 22).joined(separator: " ")
        for (code, number) in [("de", "vierzehn Uhr fünfunddreißig"), ("es", "las nueve y quince"),
                               ("fr", "quatorze heures trente cinq"), ("pt", "catorze horas e trinta e cinco"),
                               ("it", "le quattordici e trentacinque"), ("nl", "veertien uur vijfendertig")] {
            let text = trees + " " + number + " " + trees
            let cut = PocketPace.paced(text, language: code)
            #expect(cut.contains(number) && cut != text, "\(code): \(cut)")
        }
    }
}

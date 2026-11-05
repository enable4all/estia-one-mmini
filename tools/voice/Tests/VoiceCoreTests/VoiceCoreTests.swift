import Foundation
import Testing

@testable import VoiceCore

private func raw(_ text: String) -> Data { Data(text.utf8) }

/// Little-endian fields and 16-bit samples of a WAV built by the service (44-byte header).
private func le(_ wav: Data, _ at: Int, _ size: Int) -> Int {
    wav[at..<at + size].enumerated().reduce(0) { $0 | Int($1.element) << (8 * $1.offset) }
}

private func pcm(_ wav: Data) -> [Int16] {
    stride(from: 44, to: wav.count - 1, by: 2).map { Int16(bitPattern: UInt16(wav[$0]) | UInt16(wav[$0 + 1]) << 8) }
}

@Suite struct HTTPParsing {
    @Test func completeRequestWithBody() {
        let request = raw("POST /v1/audio/transcriptions HTTP/1.1\r\nHost: 127.0.0.1:8150\r\nContent-Length: 4\r\n\r\nabcd")
        guard case .request(let req) = HTTP.parse(request, maxBody: 10) else { Issue.record("not parsed"); return }
        #expect(req.method == "POST" && req.path == "/v1/audio/transcriptions" && req.body == raw("abcd"))
        #expect(req.header("host") == "127.0.0.1:8150")
    }

    @Test func incompleteUntilTheBodyArrives() {
        #expect(HTTP.parse(raw("POST / HTTP/1.1\r\nContent-Length: 4\r\n\r\nab"), maxBody: 10) == .incomplete)
        #expect(HTTP.parse(raw("GET /health HTTP/1.1\r\nHost: x"), maxBody: 10) == .incomplete)
    }

    @Test func tooBigOrMalformedBodies() {
        #expect(HTTP.parse(raw("POST / HTTP/1.1\r\nContent-Length: 11\r\n\r\n"), maxBody: 10) == .invalid(413))
        #expect(HTTP.parse(raw("POST / HTTP/1.1\r\nContent-Length: -1\r\n\r\n"), maxBody: 10) == .invalid(400))
        #expect(HTTP.parse(raw("POST / HTTP/1.1\r\nContent-Length: x\r\n\r\n"), maxBody: 10) == .invalid(400))
        #expect(HTTP.parse(raw("NONSENSE\r\n\r\n"), maxBody: 10) == .invalid(400))
    }

    @Test func firstHeaderWins() {
        let request = raw("GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nHost: evil.example\r\n\r\n")
        guard case .request(let req) = HTTP.parse(request, maxBody: 10) else { Issue.record("not parsed"); return }
        #expect(req.header("host") == "127.0.0.1")
    }

    @Test func browsersAndRebindingAreRefused() {
        #expect(HTTP.isTrustedCaller(origin: nil, host: "127.0.0.1:8150"))
        #expect(HTTP.isTrustedCaller(origin: nil, host: nil))
        // the bridge never sends an Origin: any web page, even a local one, is refused
        #expect(!HTTP.isTrustedCaller(origin: "http://localhost:3000", host: "localhost:8150"))
        #expect(!HTTP.isTrustedCaller(origin: "null", host: "127.0.0.1:8150"))
        #expect(!HTTP.isTrustedCaller(origin: "https://evil.example", host: "127.0.0.1:8150"))
        #expect(!HTTP.isTrustedCaller(origin: nil, host: "evil.example:8150"))
    }

    @Test func responseBytes() {
        let response = String(decoding: HTTP.response(200, contentType: "application/json", body: raw("{}")), as: UTF8.self)
        #expect(response == "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
    }
}

@Suite struct LanguageChoice {
    @Test func theElevenEnglishFirst() {
        #expect(Languages.all == ["en", "es", "it", "ko", "pt", "nl", "pl", "de", "ja", "ru", "fr"])
        #expect(Languages.english == "en")
    }

    @Test func transcriptionWithoutALanguageIsEnglish() throws {
        #expect(try Languages.transcription(target: "/v1/audio/transcriptions") == "en")
        #expect(try Languages.transcription(target: "/v1/audio/transcriptions?") == "en")
        #expect(try Languages.transcription(target: "/v1/audio/transcriptions?other=1") == "en")
    }

    @Test func transcriptionTakesEachOfTheEleven() throws {
        for code in Languages.all {
            #expect(try Languages.transcription(target: "/v1/audio/transcriptions?language=\(code)") == code)
        }
        #expect(try Languages.transcription(target: "/v1/audio/transcriptions?x=1&language=nl") == "nl")
        #expect(try Languages.transcription(target: "/v1/audio/transcriptions?language=nl&language=de") == "nl")
    }

    @Test func anyOtherLanguageIsRefused() {
        for query in ["language=xx", "language=", "language", "language=NL", "language=zh", "language=en-US", "language=n%6C"] {
            #expect(throws: VoiceError.unsupportedLanguage) {
                try Languages.transcription(target: "/v1/audio/transcriptions?\(query)")
            }
        }
    }

    @Test func healthListsTheLanguages() throws {
        let body = healthBody(stt: "ready", tts: "loading", vad: "unavailable", turn: "ready",
                              pocket: ["pocket": "loading", "pocket_de": "idle"])
        #expect(body["stt"] as? String == "ready" && body["tts"] as? String == "loading")
        #expect(body["languages"] as? [String] == Languages.all)
        // the speech and turn detectors' states are two more fields, Pocket TTS's one a model (English's and a
        // language's: 24-26 Oct); the others stay byte for byte as they were
        let text = String(decoding: HTTP.json(200, body), as: UTF8.self)
        // "hints": a transcription takes her name as a hint (the wake harness reads it)
        #expect(text.hasSuffix(#"{"hints":true,"languages":["en","es","it","ko","pt","nl","pl","de","ja","ru","fr"],"pocket":"loading","pocket_de":"idle","stt":"ready","tts":"loading","turn":"ready","vad":"unavailable"}"#))
    }

    /// Owner, 3 Nov 2026 ("remove kokoro and supertonic"): Pocket TTS is her only voice, so `tts` is her English Pocket
    /// model's state as the bridge reads speaking (`calls.voice_ready`: a call is answered only with it ready): ready;
    /// resting while it rests after failed or late runs (tried again on its own, and by a call's lines); failed while
    /// it cannot speak until a later try or an update (no program, files missing or refused); else loading. The review
    /// of 4 Nov: a rest after runs was "failed", so the bridge's note and doctor named the wrong cause. A language's
    /// model never counts: its own key says it.
    @Test func speakingIsHerEnglishPocketModels() {
        #expect(speakingState("ready") == "ready" && speakingState("ready", resting: true) == "ready")
        #expect(speakingState("loading") == "loading" && speakingState("idle") == "loading" && speakingState("") == "loading")
        #expect(speakingState("unavailable") == "failed" && speakingState("refused") == "failed")
        #expect(speakingState("unavailable", resting: true) == "resting")
        #expect(speakingState("refused", resting: true) == "failed")
    }

    /// The review of 4 Nov: heart's file missing left the model ready, so a call was picked up into silence. The
    /// shipped voices a ready model leaves out are listed (`voices_left_out`), only when there are any: the body is
    /// otherwise byte for byte as it was.
    @Test func healthListsTheVoicesLeftOut() {
        let body = healthBody(stt: "ready", tts: "ready", vad: "ready", turn: "ready", pocket: ["pocket": "ready"],
                              leftOut: ["heart"])
        #expect(body["voices_left_out"] as? [String] == ["heart"])
        let none = healthBody(stt: "ready", tts: "ready", vad: "ready", turn: "ready", pocket: ["pocket": "ready"])
        #expect(none["voices_left_out"] == nil)
    }
}

@Suite struct Routing {
    @Test func routes() {
        #expect(Route("POST", "/v1/audio/transcriptions") == .transcribe)
        #expect(Route("POST", "/v1/audio/transcriptions?language=nl") == .transcribe)
        #expect(Route("POST", "/v1/audio/speech") == .speak)
        #expect(Route("GET", "/health") == .health)
        #expect(Route("GET", "/health?x=1") == .health)
        #expect(Route("POST", "/vad?stream=call-1") == .vad)
        #expect(Route("GET", "/v1/audio/speech") == .methodNotAllowed)
        #expect(Route("GET", "/vad?stream=call-1") == .methodNotAllowed)
        #expect(Route("GET", "/other") == .notFound)
    }
}

@Suite struct Speech {
    @Test func validRequestWithDefaults() throws {
        let request = try SpeechRequest(json: raw(#"{"input": "  Hello there.  "}"#))
        #expect(request.input == "Hello there." && request.voice == "heart" && request.language == "en")
    }

    /// Owner, 3 Nov 2026 ("remove kokoro and supertonic"): every voice is Pocket TTS's. A Kokoro voice id an older
    /// bridge sends (a setting written before then) is the closest Pocket voice, as the bridge reads it
    /// (`config.pocket_voice`): michael for a male one, heart for any other; anything else is heart, the default. The
    /// speed an older bridge sends is taken and never read (Pocket TTS has none).
    @Test func aKokoroVoiceIsTheClosestPocketVoice() throws {
        for (asked, voice) in [("bm_george", "michael"), ("am_michael", "michael"), ("af_heart", "heart"),
                               ("bf_emma", "heart"), ("../../x", "heart"), ("", "heart"), ("alba", "alba")] {
            #expect(try SpeechRequest(json: raw(#"{"input": "Hi.", "voice": "\#(asked)", "speed": 9}"#)).voice == voice, "\(asked)")
        }
        let slow = try SpeechRequest(json: raw(#"{"input": "Hi.", "voice": "alba", "speed": 0.1}"#))
        #expect(try slow == SpeechRequest(json: raw(#"{"input": "Hi.", "voice": "alba"}"#)))
    }

    /// The review of 28 Oct (calls-03): a call's line says so, and a Pocket TTS voice resting after failed runs is
    /// tried again for it (`PocketSpeaker.speak(call:)`); any other request is as before.
    @Test func aCallsLineSaysSo() throws {
        #expect(try SpeechRequest(json: raw(#"{"input": "Hi.", "call": true}"#)).call)
        #expect(try !SpeechRequest(json: raw(#"{"input": "Hi."}"#)).call)
        #expect(try !SpeechRequest(json: raw(#"{"input": "Hi.", "call": "yes"}"#)).call)
    }

    /// Owner, 3 Nov 2026: a line Pocket TTS cannot say is said by no other voice, whatever the request asks. An older
    /// bridge's `"kokoro"` (calls-03's "no Kokoro for this line") is taken and never read: a request with it, true,
    /// false or odd, is the same request as one without it, and the voice service refuses what Pocket cannot say
    /// (409, `not_said`: VoiceServiceTests' `PocketOnly`).
    @Test func theKokoroFieldIsTakenAndIgnored() throws {
        let plain = try SpeechRequest(json: raw(#"{"input": "Hi.", "voice": "jane", "call": true}"#))
        for kokoro in ["false", "true", #""no""#, "null"] {
            let asked = try SpeechRequest(json: raw(#"{"input": "Hi.", "voice": "jane", "call": true, "kokoro": \#(kokoro)}"#))
            #expect(asked == plain, "\(kokoro)")
        }
    }

    @Test func emptyTooLongOrBroken() {
        #expect(throws: SpeechRequest.Invalid.self) { try SpeechRequest(json: raw(#"{"input": "   "}"#)) }
        #expect(throws: SpeechRequest.Invalid.self) { try SpeechRequest(json: raw(#"{"input": "\#(String(repeating: "a", count: 1501))"}"#)) }
        #expect(throws: SpeechRequest.Invalid.self) { try SpeechRequest(json: raw("not json")) }
    }

    @Test func spokenInputIsOneLineWithPlainApostrophes() throws {
        let request = try SpeechRequest(json: raw(#"{"input": "It\u2019s done.\n\nNext: 2 lamps."}"#))
        #expect(request.spokenInput == "It's done. Next: two lamps.")
    }

    @Test func englishUnlessALanguageIsNamed() throws {
        #expect(try SpeechRequest(json: raw(#"{"input": "Hi."}"#)).language == "en")
        #expect(try SpeechRequest(json: raw(#"{"input": "Hi.", "language": "en"}"#)).language == "en")
    }

    /// Owner, 3 Nov 2026: her voice speaks English and, in jane, Spanish, Italian, Portuguese, Dutch, German and French
    /// (Pocket TTS's models); Korean, Polish, Japanese and Russian have none: refused (409, `no_voice`) before any text
    /// work, never said by another voice (Supertonic-3 said them before). Listening stays in all eleven
    /// (`LanguageChoice`).
    @Test func theSixSpeakAndTheFourHaveNoVoice() throws {
        for code in ["es", "it", "pt", "nl", "de", "fr"] {
            #expect(try SpeechRequest(json: raw(#"{"input": "Hallo.", "language": "\#(code)"}"#)).language == code)
        }
        for code in ["ko", "pl", "ja", "ru"] {
            #expect(throws: VoiceError.noVoice, "\(code)") {
                try SpeechRequest(json: raw(#"{"input": "Hallo.", "voice": "jane", "language": "\#(code)"}"#))
            }
        }
        #expect(Languages.all.filter(PocketTTS.speaks) == ["en", "es", "it", "pt", "nl", "de", "fr"])
    }

    @Test func anyOtherLanguageIsRefused() {
        for language in [#""xx""#, #""""#, #""NL""#, #""zh""#, #""en-US""#, "5", "null", #"["nl"]"#] {
            #expect(throws: VoiceError.unsupportedLanguage) {
                try SpeechRequest(json: raw(#"{"input": "Hallo.", "language": \#(language)}"#))
            }
        }
    }

    @Test func otherLanguagesSayNumbersInTheirOwnWords() throws {
        // 0.9.3: Supertonic-3 misread Dutch digits (the audit); they are said in Dutch, never in English words
        let request = try SpeechRequest(json: raw(#"{"input": "Het kost 5 euro.\nDat is 20% minder, l\u2019heure.", "language": "nl"}"#))
        #expect(request.spokenInput == "Het kost vijf euro. Dat is twintig procent minder, l'heure.")
    }

    @Test func numbersAreSpelledOut() {
        #expect(NumberNormalizer.normalize("It costs $5.99, 20% off, 3.5 kg, 1,200 items")
            == "It costs five dollars and ninety nine cents, twenty percent off, three point five kilograms, one thousand two hundred items")
    }

    /// Owner, 16 Oct: "13:00" was read as "one three zero zero" (the colon left between "thirteen" and "zero"). A time
    /// is said as people say it, with its part of the day; a range names the part once.
    @Test func timesAreSaidAsTimesOfTheDay() {
        #expect(NumberNormalizer.normalize("Lunch at 13:00.") == "Lunch at one in the afternoon.")
        #expect(NumberNormalizer.normalize("At 09:05 and 9:30") == "At nine oh five in the morning and nine thirty in the morning")
        #expect(NumberNormalizer.normalize("12:00, 00:00, 21:45, 03:10")
            == "noon, midnight, nine forty five in the evening, three ten at night")
        #expect(NumberNormalizer.normalize("Meeting 14:00–15:00") == "Meeting two to three in the afternoon")
        #expect(NumberNormalizer.normalize("from 11:30-13:00") == "from eleven thirty in the morning to one in the afternoon")
        #expect(NumberNormalizer.normalize("12:00 - 13:30") == "noon to one thirty in the afternoon")
        #expect(NumberNormalizer.normalize("Call at 7:30 PM, or 7:30 a.m.")
            == "Call at seven thirty in the evening, or seven thirty in the morning.")
        #expect(NumberNormalizer.normalize("At 7:30 a.m. tomorrow, 10:00 pm. Then")
            == "At seven thirty in the morning tomorrow, ten at night. Then")
        #expect(NumberNormalizer.normalize("12:15 am, 12:00 PM, 22:30") == "twelve fifteen at night, noon, ten thirty at night")
    }

    /// The Pocket TTS spike, 24 Oct: "Shall I remind you at 7:30?" after "closes at 8 tonight" was said "seven thirty in
    /// the morning". A time before noon without am/pm says "in the morning" only when nothing in its sentence or before
    /// it in her answer says evening or night: before it in the request, and on a call (one sentence a request) the
    /// sentences of her answer said before it (`context`). Else it is the time alone.
    @Test func aMorningIsSaidOnlyWithoutEveningOrNightBeforeIt() throws {
        let everyday = "The pharmacy on Main Street closes at 8 tonight, so you still have about two hours. It's a "
            + "ten-minute walk, or four minutes by car. Shall I remind you at 7:30?"
        #expect(try SpeechRequest(json: raw(#"{"input": "\#(everyday)"}"#)).spokenInput.hasSuffix(
            "Shall I remind you at seven thirty?"))
        let call = try SpeechRequest(json: raw(
            #"{"input": "Shall I remind you at 7:30?", "context": "The pharmacy closes at 8 tonight. It's a short walk."}"#))
        #expect(call.spokenInput == "Shall I remind you at seven thirty?")
        #expect(SpokenEnglish.spoken("Remind me at 7:30 tonight.") == "Remind me at seven thirty tonight.")
        #expect(SpokenEnglish.spoken("See you at 9:15 this evening.") == "See you at nine fifteen this evening.")
        #expect(SpokenEnglish.spoken("It was a long night. Wake me at 6:45.") == "It was a long night. Wake me at six forty five.")
        #expect(SpokenEnglish.spoken("Dinner is at 8 p.m. Shall I call at 7:30?")
            == "Dinner is at eight in the evening. Shall I call at seven thirty?")
        #expect(SpokenEnglish.spoken("Shall I call at 7:30?", before: "The concert starts at 8pm.")
            == "Shall I call at seven thirty?")
        // nothing says evening or night: the morning, as before; am/pm said wins; a later answer's night is not before it
        #expect(SpokenEnglish.spoken("Your dentist appointment is tomorrow at 9:15.")
            == "Your dentist appointment is tomorrow at nine fifteen in the morning.")
        #expect(SpokenEnglish.spoken("At 7:30 a.m., not tonight.") == "At seven thirty in the morning, not tonight.")
        #expect(SpokenEnglish.spoken("Breakfast at 7:30. Then nothing tonight.")
            == "Breakfast at seven thirty in the morning. Then nothing tonight.")
        #expect(SpokenEnglish.spoken("Midnight snacks? Up at 6:30.") == "Midnight snacks? Up at six thirty in the morning.")
        // the afternoon and the night keep their names; a range, too, drops only the morning
        #expect(SpokenEnglish.spoken("Tonight at 21:30, or tomorrow at 14:00.")
            == "Tonight at nine thirty in the evening, or tomorrow at two in the afternoon.")
        #expect(SpokenEnglish.spoken("Free tonight from 7:30-9:00?") == "Free tonight from seven thirty to nine?")
    }

    /// The review of 24 Oct: code's reminder line writes its time on the 24-hour clock ("07:00"), and its "in the
    /// morning" is the one spoken cue that the half of the day may be wrong. An hour written with a leading zero is the
    /// 24-hour clock: it keeps its part of the day whatever was said around it; Gemma writes "7:30".
    @Test func anHourWithItsLeadingZeroKeepsItsMorning() throws {
        let line = try SpeechRequest(json: raw(
            #"{"input": "Reminder set for Saturday 25 October, 07:00.", "context": "Enjoy cooking tonight!"}"#))
        #expect(line.spokenInput.hasSuffix(", seven in the morning."))
        #expect(SpokenEnglish.spoken("Good night. Wake-up call set for 06:45.")
            == "Good night. Wake-up call set for six forty five in the morning.")
        #expect(SpokenEnglish.spoken("Shall I call at 7:30?", before: "Good night.") == "Shall I call at seven thirty?")
        #expect(SpokenEnglish.spoken("Free tonight from 07:30-09:00?")
            == "Free tonight from seven thirty to nine in the morning?")
    }

    /// Owner, 16 Oct: "the 'am', 'pm'": an hour with am/pm and no minutes ("7pm" was "sevenpm") is a time too; a
    /// range's first hour without am/pm takes the second's, or the other half of the day when it is later ("11-1pm").
    @Test func anHourWithAmOrPmIsATimeOfTheDay() {
        #expect(NumberNormalizer.normalize("Dinner at 7 PM.") == "Dinner at seven in the evening.")
        #expect(NumberNormalizer.normalize("at 7pm, or 11 a.m. tomorrow") == "at seven in the evening, or eleven in the morning tomorrow")
        #expect(NumberNormalizer.normalize("12 PM and 12am") == "noon and midnight")
        #expect(NumberNormalizer.normalize("Open 9am–5pm") == "Open nine in the morning to five in the afternoon")
        #expect(NumberNormalizer.normalize("free 3-5pm") == "free three to five in the afternoon")
        #expect(NumberNormalizer.normalize("from 11 to 1 pm") == "from eleven in the morning to one in the afternoon")
        #expect(NumberNormalizer.normalize("10-12pm") == "ten in the morning to noon")
        #expect(NumberNormalizer.normalize("7:30-9pm") == "seven thirty to nine in the evening")
        #expect(NumberNormalizer.normalize("between 2 and 4 PM") == "between two and four in the afternoon")
    }

    /// Chords are no times (review, 16 Oct: "C G7 Am F" came out "C Gseven in the morning F"): am/pm only in one case
    /// ("am", "AM", "a.m.", "A.M."), never "Am" or "Pm", and never an hour glued to a letter ("G7").
    @Test func chordsAreNoTimes() {
        for chords in ["The chorus is C G7 Am F.", "Capo 2 Am F C G", "Bar 12 Am", "Fret 5 Pm", "E7 Am Dm G7", "D7 Am7 G7 Am"] {
            let spoken = NumberNormalizer.normalize(chords)
            #expect(!["morning", "afternoon", "evening", "night", "noon", "midnight"].contains { spoken.contains($0) },
                    "\(chords) -> \(spoken)")
        }
        #expect(NumberNormalizer.normalize("at 7 A.M. sharp, 3 PM, 7am, 11 a.m. and 7:30 P.M.")
            == "at seven in the morning sharp, three in the afternoon, seven in the morning, eleven in the morning and seven thirty in the evening.")
    }

    /// Without am/pm an hour alone is a number, as before.
    @Test func anHourWithoutAmOrPmStaysANumber() {
        #expect(NumberNormalizer.normalize("1-2 days, from 3 to 5") == "one to two days, from three to five")
        #expect(NumberNormalizer.normalize("2023-10 and 13pm") == "two thousand twenty three-ten and thirteen pm")  // a letter glued to a digit gets a space
    }

    /// Only a time with a colon is a time: a ratio, a price with a dot and seconds keep their old reading.
    @Test func whatIsNoTimeStaysAsBefore() {
        #expect(NumberNormalizer.normalize("16:9 screens") == "sixteen:nine screens")
        #expect(NumberNormalizer.normalize("$13.00 and 3.50 kg") == "thirteen dollars and three point five zero kilograms")
        #expect(NumberNormalizer.normalize("24:00 and 9:75") == "twenty four:zero and nine:seventy five")
        #expect(NumberNormalizer.normalize("at 12:30:45 sharp") == "at twelve:thirty:forty five sharp")
        #expect(NumberNormalizer.normalize("9:30 amazing") == "nine thirty in the morning amazing")
    }

    /// A number too large for Int (19 digits or more: a reference or tracking number) stays as written, never "zero".
    @Test func aNumberTooLargeForIntStaysAsWritten() {
        #expect(NumberNormalizer.normalize("Reference 12345678901234567890, please") == "Reference 12345678901234567890, please")
        #expect(NumberNormalizer.normalize("$12345678901234567890.50") == "12345678901234567890 dollars and fifty cents")
        #expect(NumberNormalizer.normalize("99999999999999999999% sure") == "99999999999999999999 percent sure")
        #expect(NumberNormalizer.normalize("12345678901234567890.5") == "12345678901234567890 point five")
    }
}

@Suite struct Transcripts {
    @Test func confidentSilenceIsDropped() {
        let segments = [
            TranscriptSegment(text: " Turn off the lamp.", noSpeechProb: 0.1, avgLogprob: -0.2),
            TranscriptSegment(text: " Thank you.", noSpeechProb: 0.9, avgLogprob: -1.4),
        ]
        #expect(cleanTranscript(segments) == "Turn off the lamp.")
    }

    @Test func knownJunkAndNoiseTagsAreEmpty() {
        for junk in ["Thank you.", " you", "[BLANK_AUDIO]", "(music)", "Thanks for watching!", "  "] {
            #expect(cleanTranscript([TranscriptSegment(text: junk, noSpeechProb: 0.2, avgLogprob: -0.5)]) == "")
        }
        #expect(cleanTranscript([TranscriptSegment(text: "[Music] Hello Gemma", noSpeechProb: 0.2, avgLogprob: -0.5)]) == "Hello Gemma")
    }

    @Test func aCallKeepsTheCallersBareThanksAndBye() {
        // review I23: a caller's bare "Bye." or "Thank you." was erased here, so it never ended the call; on a call
        // (`call: true`) they are kept (the bridge's own noise gate judges them), Whisper's other habits still go
        let heard = { (text: String) in [TranscriptSegment(text: text, noSpeechProb: 0.05, avgLogprob: -0.3)] }
        for said in ["Bye.", "Thank you.", "Thanks.", "thank you"] {
            #expect(cleanTranscript(heard(" " + said), call: true) == said)
        }
        for junk in [" you", "[BLANK_AUDIO]", "Thanks for watching!", "  ", "."] {
            #expect(cleanTranscript(heard(junk), call: true) == "")
        }
        let silence = [TranscriptSegment(text: " Thank you.", noSpeechProb: 0.9, avgLogprob: -1.4)]
        #expect(cleanTranscript(silence, call: true) == "")  // confident silence goes on a call too
        #expect(cleanTranscript(heard(" Bye.")) == "")  // a voice note: as before
    }
}

@Suite struct Audio {
    @Test func sniffing() {
        #expect(sniffAudio(Data([0, 0, 0, 0x1C]) + raw("ftypM4A ") + Data(count: 8)) == "m4a")
        #expect(sniffAudio(Data([0xFF, 0xF9, 0x5C, 0x60])) == "aac")
        #expect(sniffAudio(Data([0xFF, 0xF1, 0x50, 0x80])) == "aac")
        #expect(sniffAudio(raw("RIFF") + Data(count: 4) + raw("WAVE")) == "wav")
        #expect(sniffAudio(raw("ID3") + Data(count: 5)) == "mp3")
        #expect(sniffAudio(Data([0xFF, 0xFB, 0x90, 0x44])) == "mp3")
        #expect(sniffAudio(raw("OggS") + Data(count: 4)) == "ogg")
        #expect(sniffAudio(raw("%PDF-1.7")) == nil)
        #expect(sniffAudio(Data()) == nil)
    }

    /// A WAV with these header fields (RIFF, a 16-byte fmt chunk, then `extra` chunks, then data), for the reader's
    /// refusals.
    private func wav(format: Int = 1, channels: Int = 1, rate: Int = 16_000, bits: Int = 16, extra: Data = Data(),
                     pcm: Data = Data([0x00, 0x40, 0x00, 0xC0])) -> Data {
        func le32(_ value: Int) -> Data { Data((0..<4).map { UInt8((value >> (8 * $0)) & 0xFF) }) }
        func le16(_ value: Int) -> Data { Data((0..<2).map { UInt8((value >> (8 * $0)) & 0xFF) }) }
        let fmt = raw("fmt ") + le32(16) + le16(format) + le16(channels) + le32(rate)
            + le32(rate * channels * bits / 8) + le16(channels * bits / 8) + le16(bits)
        let body = raw("WAVE") + fmt + extra + raw("data") + le32(pcm.count) + pcm
        return raw("RIFF") + le32(body.count) + body
    }

    @Test func aCallClipIsReadInMemoryAsAVAudioFileReadsIt() throws {
        // what the bridge sends for every call turn (call_audio.pcm48_to_wav16k): 16 kHz mono 16-bit PCM
        let clip = pcm16WAV([0, 0.5, -0.5, 1, -1], sampleRate: 16_000)
        let samples = try #require(pcm16kMonoSamples(clip))
        // AVAudioFile (and so WhisperKit's file path) reads a 16-bit sample as its value / 32768
        #expect(samples == [0, 16_384, -16_384, 32_767, -32_767].map { Float($0) / 32_768 })
        #expect(pcm16kMonoSamples(silentWAV(seconds: 1))?.count == 16_000)
        #expect(pcm16kMonoSamples(wav())! == [0.5, -0.5])
        #expect(pcm16kMonoSamples(wav(pcm: Data()))! == [])
    }

    @Test func aChunkBeforeTheDataIsSkipped() {
        let list = raw("LIST") + Data([3, 0, 0, 0]) + raw("abc") + Data([0])  // an odd size is padded to even
        #expect(pcm16kMonoSamples(wav(extra: list)) == [0.5, -0.5])
    }

    @Test func anyOtherAudioIsNotReadInMemory() {
        #expect(pcm16kMonoSamples(wav(rate: 24_000)) == nil)
        #expect(pcm16kMonoSamples(wav(rate: 48_000)) == nil)
        #expect(pcm16kMonoSamples(wav(channels: 2)) == nil)
        #expect(pcm16kMonoSamples(wav(bits: 8)) == nil)
        #expect(pcm16kMonoSamples(wav(format: 3, bits: 32)) == nil)  // float samples
        #expect(pcm16kMonoSamples(pcm16WAV([0.1], sampleRate: 24_000)) == nil)
        #expect(pcm16kMonoSamples(Data([0, 0, 0, 0x1C]) + raw("ftypM4A ") + Data(count: 8)) == nil)
        #expect(pcm16kMonoSamples(Data()) == nil)
    }

    @Test func aBrokenWAVIsNotReadInMemory() {
        let whole = wav()
        #expect(pcm16kMonoSamples(whole.prefix(whole.count - 1)) == nil)  // the data chunk is cut short
        #expect(pcm16kMonoSamples(whole.prefix(36)) == nil)  // no data chunk
        #expect(pcm16kMonoSamples(wav(pcm: Data([0, 0x40, 0]))) == nil)  // half a sample
        let noFormat = raw("RIFF") + Data([12, 0, 0, 0]) + raw("WAVE") + raw("data") + Data(count: 4)
        #expect(pcm16kMonoSamples(noFormat) == nil)
        // a slice of a larger buffer (an HTTP body) is read from its own start
        let sliced = (Data([9, 9, 9]) + whole).dropFirst(3)
        #expect(pcm16kMonoSamples(sliced) == [0.5, -0.5])
    }
}

@Suite struct Errors {
    @Test func eachErrorHasItsStatusAndCode() {
        let cases: [(VoiceError, Int, String)] = [
            (.notAudio, 415, "not_audio"), (.tooLong, 413, "too_long"), (.unreadable, 422, "unreadable"),
            (.notReady, 503, "not_ready"), (.failed, 500, "failed"), (.unsupportedLanguage, 400, "unsupported_language"),
            (.badStream, 400, "bad_stream"), (.badChunk, 400, "bad_chunk"), (.unavailable, 503, "unavailable"),
            (.notSaid, 409, "not_said"), (.noVoice, 409, "no_voice"),
        ]
        for (error, status, code) in cases {
            let text = String(decoding: error.response, as: UTF8.self)
            #expect(text.hasPrefix("HTTP/1.1 \(status) "))
            #expect(text.hasSuffix(#"{"error":"\#(code)"}"#))
        }
    }
}

@Suite struct WarmUp {
    @Test func silentWAVIsAValid16kHzMonoFile() {
        let wav = silentWAV(seconds: 1)
        #expect(wav.count == 44 + 32_000)
        #expect(sniffAudio(wav) == "wav")
        let le32 = { (at: Int) in wav[at..<at + 4].enumerated().reduce(0) { $0 | Int($1.element) << (8 * $1.offset) } }
        #expect(le32(4) == wav.count - 8)  // RIFF size
        #expect(le32(24) == 16_000)  // sample rate
        #expect(le32(40) == 32_000)  // data size
        #expect(wav[44...].allSatisfy { $0 == 0 })
    }
}

@Suite struct WAVFiles {
    @Test func monoSixteenBitLayoutAndClipping() {
        let wav = pcm16WAV([0, 0.5, -0.5, 1, -1, 2, -2, .nan, .infinity], sampleRate: 24_000)
        #expect(wav.count == 44 + 18 && sniffAudio(wav) == "wav")
        #expect(le(wav, 4, 4) == wav.count - 8)  // RIFF size
        #expect(le(wav, 20, 2) == 1 && le(wav, 22, 2) == 1)  // PCM, mono
        #expect(le(wav, 24, 4) == 24_000 && le(wav, 28, 4) == 48_000)  // sample rate, bytes a second
        #expect(le(wav, 32, 2) == 2 && le(wav, 34, 2) == 16)  // block size, bits
        #expect(le(wav, 40, 4) == 18)  // data size
        #expect(pcm(wav) == [0, 16384, -16384, 32767, -32767, 32767, -32767, 0, 0])
    }
}

@Suite struct TranscriptionRequests {
    private let wav = raw("RIFF") + Data(count: 4) + raw("WAVE")

    @Test func languageAndKind() throws {
        let request = try TranscriptionRequest(target: "/v1/audio/transcriptions?language=nl", body: wav)
        #expect(request.language == "nl" && request.kind == "wav")
        #expect(try TranscriptionRequest(target: "/v1/audio/transcriptions", body: wav).language == "en")
    }

    @Test func theLanguageIsCheckedBeforeTheAudio() {
        // a wrong language is 400 whatever the body holds: not audio, or too big
        for body in [raw("%PDF-1.7"), Data(), wav + Data(count: Limits.maxAudioBytes)] {
            #expect(throws: VoiceError.unsupportedLanguage) {
                try TranscriptionRequest(target: "/v1/audio/transcriptions?language=xx", body: body)
            }
        }
    }

    @Test func aHintIsHerNameAsSent() throws {
        // her name, which the bridge sends on a call in standby so Whisper hears it (owner, 22 Oct)
        let target = "/v1/audio/transcriptions"
        #expect(try TranscriptionRequest(target: "\(target)?language=nl", body: wav).hint == "")
        #expect(try TranscriptionRequest(target: "\(target)?hint=Estia", body: wav).hint == "Estia")
        #expect(try TranscriptionRequest(target: "\(target)?language=nl&hint=Mary%20Jane", body: wav).hint == "Mary Jane")
        #expect(try TranscriptionRequest(target: "\(target)?hint=Mary-Jane&language=de", body: wav).language == "de")
        let katakana = "%E3%82%A8%E3%82%B9%E3%83%86%E3%82%A3%E3%82%A2"
        #expect(try TranscriptionRequest(target: "\(target)?hint=\(katakana)", body: wav).hint == "エスティア")
        #expect(try TranscriptionRequest(target: "\(target)?hint=", body: wav).hint == "")
    }

    @Test func aCallIsNamedByItsFlag() throws {
        // the bridge's ?call=1 on a call's turns: the bare thanks and bye are the caller's (review I23)
        let target = "/v1/audio/transcriptions"
        #expect(try TranscriptionRequest(target: "\(target)?call=1", body: wav).call)
        #expect(try TranscriptionRequest(target: "\(target)?language=nl&call=1&hint=Estia", body: wav).call)
        #expect(try !TranscriptionRequest(target: target, body: wav).call)
        #expect(try !TranscriptionRequest(target: "\(target)?call=0", body: wav).call)
        #expect(try !TranscriptionRequest(target: "\(target)?hint=Estia", body: wav).call)
    }

    @Test func aHintThatIsNoNameIsRefused() {
        // a name as the bridge's config allows it: letters of any script, spaces and hyphens, a letter at each end;
        // never a token, a line break or a sentence (Whisper's prompt is text it may write back)
        let bad = ["R2D2", "%3C%7Cendoftext%7C%3E", String(repeating: "a", count: 41), "Estia%0A", "%ZZ", "-Estia", "Estia.",
                   "Say%20Estia%20now%2C%20please"]
        for hint in bad {
            #expect(throws: VoiceError.badHint) {
                try TranscriptionRequest(target: "/v1/audio/transcriptions?hint=\(hint)", body: wav)
            }
        }
        #expect(throws: VoiceError.unsupportedLanguage) {  // the language first, as ever
            try TranscriptionRequest(target: "/v1/audio/transcriptions?language=xx&hint=R2D2", body: wav)
        }
        #expect(VoiceError.badHint.status == 400 && VoiceError.badHint.code == "bad_hint")
    }

    @Test func notAudioOrTooBig() {
        #expect(throws: VoiceError.notAudio) {
            try TranscriptionRequest(target: "/v1/audio/transcriptions?language=nl", body: raw("%PDF-1.7"))
        }
        #expect(throws: VoiceError.tooLong) {
            try TranscriptionRequest(target: "/v1/audio/transcriptions?language=nl", body: wav + Data(count: Limits.maxAudioBytes))
        }
    }
}

/// A Pocket TTS model that fails (no program, files not fetched, runs that fail) keeps the service up (listening and the
/// speech detector work on) and is tried again in the process after 1, 5 and 30 minutes, then every 30 minutes
/// (`PocketSpeaker`'s rest): never a restart loop that reloads Whisper each time.
@Suite struct SpeakingRetries {
    @Test func theDelaysGrowToHalfAnHour() {
        #expect((0..<5).map(SpeakingRetry.delay(afterFailures:)) == [60, 300, 1800, 1800, 1800])
    }
}

/// The service's own arguments: launchd starts it with none (port 8150, the models folder in its home); a check built
/// here runs on another port and, to fetch nothing into a home, with a models folder of its own (25 Oct: the six
/// languages' check, in a scratch folder).
@Suite struct ServiceArgumentsParsing {
    @Test func noArgumentsAreTheLiveServicesOwn() {
        #expect(ServiceArguments(["voice-service"]) == ServiceArguments(port: 8150, models: nil))
    }

    /// Owner, 26 Oct: the Pocket model follows the Mac's Gemma (`PocketTTS.english(memory:)`); a check built here
    /// names one, to check the other model on this Mac.
    @Test func aCheckNamesThePocketModel() {
        #expect(ServiceArguments(["voice-service"])?.pocket == nil)  // launchd's: the Mac's own
        let small = ServiceArguments(["voice-service", "--port", "8151", "--models", "/tmp/m", "--pocket", "18M"])
        #expect(small == ServiceArguments(port: 8151, models: URL(fileURLWithPath: "/tmp/m"), pocket: .english18M))
        #expect(ServiceArguments(["voice-service", "--pocket", "100M", "--port", "8151"])?.pocket == PocketModel.english)
    }

    @Test func aPortAndAModelsFolderInEitherOrder() {
        #expect(ServiceArguments(["voice-service", "--port", "8151"]) == ServiceArguments(port: 8151, models: nil))
        let both = ServiceArguments(["voice-service", "--port", "8151", "--models", "/tmp/scratch models"])
        #expect(both == ServiceArguments(port: 8151, models: URL(fileURLWithPath: "/tmp/scratch models")))
        #expect(ServiceArguments(["voice-service", "--models", "/tmp/m", "--port", "8152"])
            == ServiceArguments(port: 8152, models: URL(fileURLWithPath: "/tmp/m")))
    }

    /// Anything it does not take is refused, never read as the defaults (the review of 25 Oct: a typo in a check run,
    /// `--port 81510` or `--models scratch`, took the live address or fetched into the home the flag keeps clear).
    @Test func anythingOddIsRefused() {
        for odd in [["--port", "x"], ["--port"], ["--port", "81510"], ["--port", "0"], ["--port", "-1"], ["--models"],
                    ["--models", "relative"], ["--models", ""], ["--other", "1"], ["--port", "8151", "extra"],
                    ["--port", "8151", "--port", "8152"], ["--models", "/tmp/a", "--models", "/tmp/b"],
                    ["--pocket"], ["--pocket", "18m"], ["--pocket", "small"], ["--pocket", "eve"],
                    ["--pocket", "18M", "--pocket", "100M"]] {
            #expect(ServiceArguments(["voice-service"] + odd) == nil, "\(odd)")
        }
    }

    /// The review of 26 Oct (R2-F8): a check's models folder or Pocket model comes with a port of its own, never the
    /// live one's (with only `--pocket 18M` or `--models /x` it took 8150: with her service stopped the bridge would
    /// talk to the check, with it running her restart would fail while the check holds the port).
    @Test func aChecksFlagsComeWithAPortOfItsOwn() {
        for odd in [["--pocket", "18M"], ["--models", "/x"], ["--models", "/x", "--port", "8150"],
                    ["--port", "8150", "--pocket", "100M"], ["--pocket", "18M", "--models", "/x"]] {
            #expect(ServiceArguments(["vs"] + odd) == nil, "\(odd)")
        }
        #expect(ServiceArguments(["vs", "--pocket", "18M", "--port", "8151"])
            == ServiceArguments(port: 8151, models: nil, pocket: .english18M))
        #expect(ServiceArguments(["vs", "--port", "8150"]) == ServiceArguments(port: 8150, models: nil))  // the live one's
    }
}

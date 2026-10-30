import Foundation

/// Who speaks: English in a Pocket voice (`PocketTTS.voices`: heart, jane, eve, vera, alba, michael) Pocket TTS, on the
/// Mac's model (vera on the 18M: Kokoro's af_heart, as a stand-in), in any other voice Kokoro as before; Spanish, Italian, Portuguese, Dutch, German and French in jane Pocket TTS (each language's own
/// model, `PocketTTS.languages`); Supertonic-3 (voice F1) those six in any other voice and the other four languages.
public enum SpeechEngine: Equatable, Sendable { case pocket, kokoro, supertonic }

/// A checked `/v1/audio/speech` request (OpenAI shape: input, voice, speed; plus language).
public struct SpeechRequest: Equatable, Sendable {
    public struct Invalid: Error, Equatable { public let reason: String }

    public static let maxCharacters = 1500
    public static let defaultVoice = "af_heart"
    public static let defaultSpeed = 1.1
    static let speedRange = 0.7...1.5

    public let input: String
    /// A Pocket voice or Kokoro's; Supertonic-3 ignores it (in Spanish, Italian, Portuguese, Dutch, German and French
    /// jane is Pocket's).
    public let voice: String
    public let speed: Double
    /// One of `Languages.all`: English when the request names none (the bridge's English requests are unchanged).
    public let language: String
    /// What she said just before this text (on a call, her answer's sentences before this one; at most its last
    /// `maxCharacters`), read only for a time's part of the day (`SpokenEnglish.spoken(_:before:)`), never spoken.
    public let context: String
    /// A call's line (`"call": true`): a Pocket voice resting after failed runs is tried again for it, so Kokoro, which
    /// an Apple bug can crash with the whole service and the call, says fewer lines (the review of 28 Oct, calls-03).
    public let call: Bool
    /// Whether Kokoro may stand in for this line (`"kokoro": false`: a call's line after its turn's one Kokoro line, or
    /// one before her answer or a device question; owner, 28 Oct, calls-03): only a JSON false says no.
    /// `standInAllowed` reads it.
    public let kokoro: Bool

    /// Throws `VoiceError.unsupportedLanguage` for any language but the eleven, else `Invalid`.
    public init(json: Data) throws {
        guard let object = try? JSONSerialization.jsonObject(with: json) as? [String: Any] else {
            throw Invalid(reason: "body is not a JSON object")
        }
        language = object.keys.contains("language") ? try Languages.checked(object["language"]) : Languages.english
        let input = (object["input"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty else { throw Invalid(reason: "input is empty") }
        guard input.count <= Self.maxCharacters else { throw Invalid(reason: "input is over \(Self.maxCharacters) characters") }
        self.input = input
        context = String((object["context"] as? String ?? "").suffix(Self.maxCharacters))
        call = object["call"] as? Bool ?? false
        kokoro = object["kokoro"] as? Bool ?? true
        // Kokoro voice ids look like `af_heart`, Pocket's are its names (`heart`, `jane`, `eve`, `vera`, `alba`, `michael`);
        // anything else (paths, odd spellings, the bridge's other name eve-full) gets the default, because FluidAudio
        // caches voice packs under the raw string.
        let voice = object["voice"] as? String ?? ""
        self.voice = Self.isKokoro(voice) || PocketTTS.voices.contains(voice) ? voice : Self.defaultVoice
        let speed = (object["speed"] as? NSNumber)?.doubleValue ?? Self.defaultSpeed
        self.speed = speed.isFinite ? min(max(speed, Self.speedRange.lowerBound), Self.speedRange.upperBound) : Self.defaultSpeed
    }

    /// Whether `voice` is a Kokoro voice id (`af_heart`, `am_michael`: two letters, an underscore, a name).
    public static func isKokoro(_ voice: String) -> Bool {
        voice.range(of: #"^[a-z]{2}_[a-z]{2,20}$"#, options: .regularExpression) != nil
    }

    public var engine: SpeechEngine {
        if language != Languages.english {
            return PocketTTS.languages.contains { $0.voice == voice && $0.language == language } ? .pocket : .supertonic
        }
        return PocketTTS.voices.contains(voice) ? .pocket : .kokoro
    }

    /// The text the engine gets (`spokenInput(for: engine)`).
    public var spokenInput: String { spokenInput(for: engine) }

    /// The engine that says a line Pocket cannot (`PocketTTS.standIn(for:voice:)`): Kokoro in English, Supertonic-3 in
    /// another language.
    public var standInEngine: SpeechEngine { language == Languages.english ? .kokoro : .supertonic }

    /// The voice that says a line Pocket cannot in this request's voice: Kokoro's of its kind in English (am_michael
    /// for alba and michael, af_heart for the others), Supertonic-3's F1 in another language.
    public var standInVoice: String { PocketTTS.standIn(for: language, voice: voice) }

    /// Whether the stand-in may say a line Pocket cannot: Supertonic-3 in another language always, Kokoro unless the
    /// request asks for none (`kokoro`; the voice service then refuses the line: `VoiceError.noStandIn`).
    public var standInAllowed: Bool { kokoro || standInEngine != .kokoro }

    /// That engine's own text (`spokenInput(for: standInEngine)`), never Pocket's.
    public var standInInput: String { spokenInput(for: standInEngine) }

    /// The text `engine` gets (Kokoro's or Supertonic-3's too when it says a line Pocket could not): smart apostrophes
    /// plain, one line (Kokoro stops at a newline), and read as people say it: English by `SpokenEnglish` (chords,
    /// numbers, dates, units and the rest as words; for Pocket then its letters as text, `PocketText`), the other ten
    /// by `SpokenLanguage` in their own words (Supertonic-3 misreads their digits; ja and ko keep the ones it reads
    /// right), for Pocket then the guard for a long stretch with no pause mark.
    public func spokenInput(for engine: SpeechEngine) -> String {
        let plain = input.replacingOccurrences(of: "\u{2019}", with: "'").replacingOccurrences(of: "\u{2018}", with: "'")
        let oneLine = plain.components(separatedBy: .newlines).filter { !$0.isEmpty }.joined(separator: " ")
        switch engine {
        case .pocket:
            return language == Languages.english
                ? PocketText.spoken(oneLine, before: context) : PocketText.spoken(oneLine, language: language)
        case .kokoro: return SpokenEnglish.spoken(oneLine, before: context)
        case .supertonic: return SpokenLanguage.spoken(oneLine, language: language)
        }
    }
}

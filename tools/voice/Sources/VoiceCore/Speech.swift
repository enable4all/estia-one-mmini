import Foundation

/// A checked `/v1/audio/speech` request (OpenAI shape: input, voice; plus language). Pocket TTS says every line (owner,
/// 3 Nov 2026: "remove kokoro and supertonic"; nothing replaces them): English in the voices of the Mac's model
/// (`PocketVoices.speech`), Spanish, Italian, Portuguese, Dutch, German and French in jane (`PocketTTS.languages`).
/// Korean, Polish, Japanese and Russian have no voice (`VoiceError.noVoice`). What an older bridge sends for the engines
/// that are gone (`speed`, `"kokoro"`) is taken and never read: a line Pocket cannot say is refused whatever it asks
/// (`PocketLine`, `VoiceError.notSaid`).
public struct SpeechRequest: Equatable, Sendable {
    public struct Invalid: Error, Equatable { public let reason: String }

    public static let maxCharacters = 1500
    /// The voice of a request that names none, or a name no Pocket voice has: heart (owner, 27 Oct: "make it default").
    public static let defaultVoice = "heart"

    public let input: String
    /// A Pocket voice by the bridge's name (one of `PocketTTS.voices`: `pocketVoice`).
    public let voice: String
    /// One of `Languages.all` her voice speaks (`PocketTTS.speaks`): English when the request names none (the bridge's
    /// English requests are unchanged).
    public let language: String
    /// What she said just before this text (on a call, her answer's sentences before this one; at most its last
    /// `maxCharacters`), read only for a time's part of the day (`SpokenEnglish.spoken(_:before:)`), never spoken.
    public let context: String
    /// A call's line (`"call": true`): a Pocket voice resting after failed runs is tried again for it (the review of
    /// 28 Oct, calls-03; `PocketSpeaker.speak(call:)`).
    public let call: Bool

    /// Throws `VoiceError.unsupportedLanguage` for any language but the eleven, `VoiceError.noVoice` for one her voice
    /// does not speak (Korean, Polish, Japanese, Russian), else `Invalid`.
    public init(json: Data) throws {
        guard let object = try? JSONSerialization.jsonObject(with: json) as? [String: Any] else {
            throw Invalid(reason: "body is not a JSON object")
        }
        language = object.keys.contains("language") ? try Languages.checked(object["language"]) : Languages.english
        guard PocketTTS.speaks(language) else { throw VoiceError.noVoice }
        let input = (object["input"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty else { throw Invalid(reason: "input is empty") }
        guard input.count <= Self.maxCharacters else { throw Invalid(reason: "input is over \(Self.maxCharacters) characters") }
        self.input = input
        context = String((object["context"] as? String ?? "").suffix(Self.maxCharacters))
        call = object["call"] as? Bool ?? false
        voice = Self.pocketVoice(object["voice"] as? String ?? "")
    }

    /// The Pocket voice for the name a request gives: one of `PocketTTS.voices` as it is; a Kokoro voice id (`af_heart`,
    /// `am_michael`: two letters, an underscore, a name), which an older bridge sends for a setting written before
    /// 3 Nov 2026, the closest one, as the bridge reads it (`config.pocket_voice`): michael for a male one (its second
    /// letter m), heart for any other; anything else (paths, odd spellings, the bridge's other name eve-full) heart.
    public static func pocketVoice(_ name: String) -> String {
        if PocketTTS.voices.contains(name) { return name }
        guard name.range(of: #"^[a-z]{2}_[a-z]{2,20}$"#, options: .regularExpression) != nil else { return defaultVoice }
        return name.dropFirst().first == "m" ? "michael" : defaultVoice
    }

    /// `text` as the passes read it: smart apostrophes plain, one line.
    public static func oneLine(_ text: String) -> String {
        let plain = text.replacingOccurrences(of: "\u{2019}", with: "'").replacingOccurrences(of: "\u{2018}", with: "'")
        return plain.components(separatedBy: .newlines).filter { !$0.isEmpty }.joined(separator: " ")
    }

    /// The text Pocket gets (`PocketText`), from one line (`oneLine`) read as people say it: English by `SpokenEnglish`
    /// (chords, numbers, dates, units and the rest as words), then its capitals meant as letters written as letters; the
    /// six languages by `SpokenLanguage` in their own words; then the guard for a long stretch with no pause mark.
    public var spokenInput: String {
        let line = Self.oneLine(input)
        return language == Languages.english
            ? PocketText.spoken(line, before: context) : PocketText.spoken(line, language: language)
    }
}

import Foundation

/// A checked `/v1/audio/transcriptions` request: the language first (a wrong one is refused whatever the body
/// holds), then the hint, then the audio's kind from its bytes, then its size.
public struct TranscriptionRequest: Equatable, Sendable {
    /// One of `Languages.all`: English when the request names none.
    public let language: String
    /// What `sniffAudio` found, also the temporary file's extension.
    public let kind: String
    /// Her name for Whisper's prompt (`?hint=`, percent-encoded), "" for none.
    public let hint: String
    /// A call's turn (`?call=1`): a bare "Bye." or "Thank you." is the caller's, kept (`cleanTranscript`).
    public let call: Bool

    public init(target: String, body: Data) throws(VoiceError) {
        language = try Languages.transcription(target: target)
        hint = try Hint.transcription(target: target)
        call = target.split(separator: "?", maxSplits: 1).dropFirst().first?.split(separator: "&").contains("call=1") ?? false
        guard let kind = sniffAudio(body) else { throw .notAudio }
        guard body.count <= Limits.maxAudioBytes else { throw .tooLong }
        self.kind = kind
    }
}

/// A transcription's hint: her name, which the bridge sends on a call in standby so Whisper writes it when it is said
/// (owner, 22 Oct: her name was missed). Only a name as her config allows it (letters of any script, spaces and
/// hyphens, a letter at each end, at most `maxLength`): Whisper's prompt is text it may write back, so never a token,
/// a line break or a sentence; anything else is `VoiceError.badHint`.
public enum Hint {
    public static let maxLength = 40

    public static func transcription(target: String) throws(VoiceError) -> String {
        guard let query = target.split(separator: "?", maxSplits: 1).dropFirst().first else { return "" }
        for pair in query.split(separator: "&") {
            let parts = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard parts[0] == "hint" else { continue }
            let raw = parts.count == 2 ? String(parts[1]) : ""
            guard let value = raw.removingPercentEncoding else { throw .badHint }
            guard value.isEmpty || isName(value) else { throw .badHint }
            return value
        }
        return ""
    }

    static func isName(_ value: String) -> Bool {
        guard let first = value.first, let last = value.last, value.count <= maxLength else { return false }
        return first.isLetter && last.isLetter && value.allSatisfy { $0.isLetter || $0 == " " || $0 == "-" }
    }
}

/// What the service needs from a Whisper segment (WhisperKit's `TranscriptionSegment` maps onto it).
public struct TranscriptSegment: Equatable, Sendable {
    public let text: String
    public let noSpeechProb: Float
    public let avgLogprob: Float

    public init(text: String, noSpeechProb: Float, avgLogprob: Float) {
        self.text = text
        self.noSpeechProb = noSpeechProb
        self.avgLogprob = avgLogprob
    }
}

/// Whisper's habits on silence and noise (Hermes, OpenClaw): confident-silence segments go,
/// bracketed noise tags go, and a transcript that is only a known filler phrase is empty. On a call (`call`) a bare
/// thanks or bye is the caller's way to end it and is kept (review I23: erased here, the call never ended); the
/// bridge's own noise gate judges it by how long the voice lasted.
public func cleanTranscript(_ segments: [TranscriptSegment], call: Bool = false) -> String {
    let kept = segments.filter { !($0.noSpeechProb > 0.6 && $0.avgLogprob < -1.0) }
    let text = kept.map(\.text).joined(separator: " ")
        .replacingOccurrences(of: #"[\[(][^\])]{0,40}[\])]"#, with: " ", options: .regularExpression)
        .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        .trimmingCharacters(in: .whitespacesAndNewlines)
    let junk: Set<String> = ["", "you", "thanks for watching", "thanks for watching!", "."]
    let said: Set<String> = ["thank you", "thank you.", "thanks.", "bye."]  // a caller's words, also Whisper's habit
    let lowered = text.lowercased()
    return junk.contains(lowered) || (!call && said.contains(lowered)) ? "" : text
}

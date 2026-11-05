import Foundation

/// The languages the service hears and speaks, one at a time: the bridge names one on every request and
/// Whisper never detects it (a wrong guess costs more than the switch). Whisper large-v3's lowest error rates
/// (OpenAI's chart, as recorded in OpenWhisperer's STTLanguages.swift: 2.8 to 5.3 %). Seven of them have a voice
/// (Pocket TTS: English, Spanish, Italian, Portuguese, Dutch, German and French); Korean, Polish, Russian and Japanese
/// have none (owner, 3 Nov 2026: a line in them is refused, 409 `no_voice`), and listening stays in all eleven.
public enum Languages {
    /// English first, then by error rate.
    public static let all = ["en", "es", "it", "ko", "pt", "nl", "pl", "de", "ja", "ru", "fr"]
    /// A request that names no language: the bridge's English requests stay exactly as they were.
    public static let english = "en"

    /// The language of a transcription: `?language=<code>` on the request target (the first one counts),
    /// English without one; any other value is refused, never guessed.
    public static func transcription(target: String) throws(VoiceError) -> String {
        guard let query = target.split(separator: "?", maxSplits: 1).dropFirst().first else { return english }
        for pair in query.split(separator: "&") {
            let parts = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            if parts[0] == "language" { return try checked(parts.count == 2 ? String(parts[1]) : nil) }
        }
        return english
    }

    /// `value` when it is exactly one of the eleven codes, else `VoiceError.unsupportedLanguage`.
    static func checked(_ value: Any?) throws(VoiceError) -> String {
        guard let code = value as? String, all.contains(code) else { throw .unsupportedLanguage }
        return code
    }
}

/// The `/health` answer: each engine's state, and the languages (the bridge reads their presence to know the
/// installed service takes a language); `vad` is the speech detector's (its presence: the service has `/vad`), `turn`
/// the turn detector's (its presence: the service has `/turn`); `hints`: a transcription takes her name (`Hint`);
/// `pocket`: Pocket TTS's, by model (`PocketSpeech.health`: English's "pocket", every English voice's on the Mac's
/// model, 100M or 18M; a language's "pocket_<code>", jane's in it; idle, loading, ready, unavailable, refused; their
/// presence: English in those voices, and that language in jane, is Pocket's); `voices_left_out`, only when there are
/// any: the English voices her ready model leaves out (a shipped voice's file missing, not the pinned one, or failing
/// its first run: only an update or the next check puts it right), so the bridge never picks up a call in one (the
/// review of 4 Nov: heart's file missing left the model ready and every call silent).
public func healthBody(stt: String, tts: String, vad: String, turn: String, pocket: [String: String],
                       leftOut: [String] = []) -> [String: Any] {
    var body: [String: Any] = ["stt": stt, "tts": tts, "vad": vad, "turn": turn, "languages": Languages.all, "hints": true]
    if !leftOut.isEmpty { body["voices_left_out"] = leftOut }
    return body.merging(pocket) { kept, _ in kept }
}

/// `tts` in `/health`: her English Pocket TTS model's state (`pocket`), as the bridge reads speaking, since Pocket TTS
/// is her only voice (owner, 3 Nov 2026: "remove kokoro and supertonic"; `tts` was Kokoro's): "ready" when it is;
/// "resting" when it rests after failed or late runs (`resting`: its files and program checked; tried again on its own
/// after 1, 5 and 30 minutes, then every 30, and by a call's lines); "failed" when it cannot speak until a later try or
/// an update (its program missing, its files missing after a download or refused); else "loading". The bridge answers
/// a call only with it ready (`calls.voice_ready`) and words each case once (the review of 4 Nov: a rest after runs
/// read as "failed", and the caller was told the voice was still loading). A language's model never counts: its own
/// key says it.
public func speakingState(_ pocket: String, resting: Bool = false) -> String {
    switch pocket {
    case "ready": "ready"
    case "unavailable" where resting: "resting"
    case "unavailable", "refused": "failed"
    default: "loading"
    }
}

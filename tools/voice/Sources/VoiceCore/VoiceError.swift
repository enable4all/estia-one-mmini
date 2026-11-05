import Foundation

/// Why a request could not be served; the bridge reads the status (and the code, for logs).
public enum VoiceError: Error, Equatable, Sendable {
    case notAudio, tooLong, unreadable, notReady, failed, unsupportedLanguage
    /// The speech detector's: a stream id or a chunk the bridge got wrong, and a model that will not load.
    case badStream, badChunk, unavailable
    /// The turn detector's: a window the bridge got wrong (empty, an odd byte count, over 8 s).
    case badWindow
    /// A transcription's hint that is no name (`Hint`).
    case badHint
    /// A line Pocket TTS could not say (not ready, a refused file, a run that failed or was late, a voice resting after
    /// failures): no other voice says it, whatever the request asks (owner, 3 Nov 2026: "remove kokoro and
    /// supertonic", and for those lines "Nothing"). The bridge then sends that answer as text.
    case notSaid
    /// A line in a language her voice does not speak (Korean, Polish, Japanese, Russian: Pocket TTS has no model for
    /// them; owner, 3 Nov 2026): refused before any text work. Listening stays in all eleven.
    case noVoice

    public var status: Int {
        switch self {
        case .notAudio: 415
        case .tooLong: 413
        case .unreadable: 422
        case .notReady: 503
        case .failed: 500
        case .unsupportedLanguage: 400
        case .badStream, .badChunk, .badWindow, .badHint: 400
        case .unavailable: 503
        case .notSaid, .noVoice: 409
        }
    }

    public var code: String {
        switch self {
        case .notAudio: "not_audio"
        case .tooLong: "too_long"
        case .unreadable: "unreadable"
        case .notReady: "not_ready"
        case .failed: "failed"
        case .unsupportedLanguage: "unsupported_language"
        case .badStream: "bad_stream"
        case .badChunk: "bad_chunk"
        case .unavailable: "unavailable"
        case .badWindow: "bad_window"
        case .badHint: "bad_hint"
        case .notSaid: "not_said"
        case .noVoice: "no_voice"
        }
    }

    public var response: Data { HTTP.json(status, ["error": code]) }
}

import Foundation

/// A Pocket TTS model that fails (its program missing, its files not fetched, runs that fail) rests and is tried again
/// in the process (`PocketSpeaker`), never by a restart: a restart would check and reload Whisper's 1.6 GB each time,
/// and listening would flip between loading and ready. 1, 5 and 30 minutes, then every 30 minutes.
public enum SpeakingRetry {
    static let delays: [TimeInterval] = [60, 300, 1800]

    /// Seconds before the next try, after `count` failed tries since the first load.
    public static func delay(afterFailures count: Int) -> TimeInterval {
        delays[min(count, delays.count - 1)]
    }
}

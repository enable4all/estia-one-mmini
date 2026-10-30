import Foundation

/// An Apple BNNS bug can crash English speech (Kokoro) and the whole service with it: FluidAudio 0.15.8 flags macOS
/// 26.4 and 26.5 (KokoroAneManager.isBnnsCrashProneOS, its logger only), and the same crash (SIGSEGV in libBNNS, on a
/// dispatch worker) was seen on macOS 27.0.1 (docs/agents/calls.md, Gotchas), with no fix yet: 27.0.x is warned until
/// measured otherwise (the review of 28 Oct, calls-03). Estia needs macOS 27 or later (owner, 29 Oct): 26.x is never
/// hers, so only 27.0.x gets the line in voice.log.
public enum KokoroAdvisory {
    public static func crashProne(_ version: OperatingSystemVersion) -> Bool {
        version.majorVersion == 27 && version.minorVersion == 0
    }

    /// The log's line for this macOS, nil where no crash is known.
    public static func warning(_ version: OperatingSystemVersion) -> String? {
        guard crashProne(version) else { return nil }
        return "speaking: an Apple bug that can crash English speech (Kokoro) and the voice service with it was seen on "
            + "macOS 27.0.1 too; no fix yet: launchd starts the voice service again, and a call in progress drops"
    }
}

// swift-tools-version: 6.2
// local-assistant's voice service: listening (WhisperKit, on the Neural Engine), speaking (Pocket TTS's release
// program, run beside it on the CPU: her only voice since 3 Nov 2026) and the speech detector (FluidAudio's Silero
// VAD, on the CPU), served on 127.0.0.1 for the bridge. The engine layer and its pins come from the OpenWhisperer
// voice app (MIT, app/Package.swift), where they are explained in full; change them only deliberately, after measuring.
import Foundation
import PackageDescription

/// The Command Line Tools' Testing macros folder, named for the test targets only while the Command Line Tools are the
/// active developer folder (DEVELOPER_DIR, else xcode-select's choice, else Xcode when installed): there SwiftPM
/// sometimes loses the Testing macros plugin after a failed build ("plugin for module 'TestingMacros' not found"),
/// and naming its folder keeps every run reliable. With Xcode active, Xcode's own compiler finds its own macros; the
/// Command Line Tools' could be another version. (Untested with Xcode active: this Mac has only the Command Line Tools.)
let commandLineTools = "/Library/Developer/CommandLineTools"
let activeDeveloperFolder = Context.environment["DEVELOPER_DIR"]
    ?? (try? FileManager.default.destinationOfSymbolicLink(atPath: "/var/db/xcode_select_link"))
    ?? (FileManager.default.fileExists(atPath: "/Applications/Xcode.app")
        ? "/Applications/Xcode.app/Contents/Developer" : commandLineTools)
let testingMacros: [SwiftSetting] = activeDeveloperFolder.hasPrefix(commandLineTools)
    ? [.unsafeFlags(["-plugin-path", commandLineTools + "/usr/lib/swift/host/plugins/testing"])] : []

let package = Package(
    name: "voice-service",
    platforms: [.macOS(.v14)],
    dependencies: [
        // WhisperKit 1.1.0+: fixes empty transcripts when prompt tokens are set (upstream #514).
        .package(url: "https://github.com/argmaxinc/WhisperKit.git", from: "1.1.0"),
        // FluidAudio exactly 0.15.8, for its Silero VAD alone (the speech detector, SpeechDetector.swift; its model
        // pinned in VoiceCore's `SpeechModel`): Kokoro and Supertonic-3, which it also holds, were removed (owner,
        // 3 Nov 2026: "remove kokoro and supertonic"), so nothing of theirs is loaded or downloaded. `traits: []`
        // leaves out NeMo's text normalisation, a prebuilt 9 MB library (owner, 16 Oct: dropped; it read "13:00" as
        // "thirteen o'clock"), and a test (VoiceServiceTests, `EnglishNumbers`) fails if an upgrade links it again.
        .package(url: "https://github.com/FluidInference/FluidAudio.git", exact: "0.15.8", traits: []),
    ],
    targets: [
        .target(name: "VoiceCore", swiftSettings: [.swiftLanguageMode(.v5)]),
        .executableTarget(
            name: "voice-service",
            dependencies: [
                "VoiceCore",
                .product(name: "WhisperKit", package: "WhisperKit"),
                .product(name: "FluidAudio", package: "FluidAudio"),
            ],
            // heart's Pocket TTS voice files (owner, 27 Oct: the 100M's; 29 Oct: the 18M's), built into the voice
            // service's own bundle beside its program (VoiceCore `PocketTTS.resources`), copied as they are: never fetched
            resources: [.copy("Voices/heart.safetensors"), .copy("Voices/heart-18m.safetensors")],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        // Swift Testing works with the Command Line Tools (XCTest does not); `testingMacros` above.
        .testTarget(
            name: "VoiceCoreTests",
            dependencies: ["VoiceCore"],
            swiftSettings: testingMacros
        ),
        // The service's own glue to the engines, where it needs their types (Whisper's decoding options).
        .testTarget(
            name: "VoiceServiceTests",
            dependencies: [
                "voice-service",
                .product(name: "WhisperKit", package: "WhisperKit"),
                .product(name: "FluidAudio", package: "FluidAudio"),
            ],
            swiftSettings: testingMacros
        ),
    ]
)

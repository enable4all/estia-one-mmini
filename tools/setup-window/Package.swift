// swift-tools-version: 6.0
// The Estia Setup window: setup's questions at the desk, in a window of its own. Apple's frameworks only (AppKit,
// WebKit, CryptoKit), no dependencies. Its pages are its own files, served by the app itself from a folder checked by
// sha256: no network, no port. Built from the checkout by setup (setup_window.build), never deployed into her release.
import Foundation
import PackageDescription

/// The Command Line Tools' Testing macros folder, named for the test targets only while the Command Line Tools are the
/// active developer folder (DEVELOPER_DIR, else xcode-select's choice, else Xcode when installed): there SwiftPM
/// sometimes loses the Testing macros plugin after a failed build ("plugin for module 'TestingMacros' not found"),
/// and naming its folder keeps every run reliable. The same as tools/documents/Package.swift.
let commandLineTools = "/Library/Developer/CommandLineTools"
let activeDeveloperFolder = Context.environment["DEVELOPER_DIR"]
    ?? (try? FileManager.default.destinationOfSymbolicLink(atPath: "/var/db/xcode_select_link"))
    ?? (FileManager.default.fileExists(atPath: "/Applications/Xcode.app")
        ? "/Applications/Xcode.app/Contents/Developer" : commandLineTools)
let testingMacros: [SwiftSetting] = activeDeveloperFolder.hasPrefix(commandLineTools)
    ? [.unsafeFlags(["-plugin-path", commandLineTools + "/usr/lib/swift/host/plugins/testing"])] : []

let package = Package(
    name: "setup-window",
    platforms: [.macOS(.v14)],
    targets: [
        // what can be tested without a window: the protocol, the page's files, the links
        .target(name: "SetupWindowCore", swiftSettings: [.swiftLanguageMode(.v5)]),
        .executableTarget(
            name: "estia-setup",
            dependencies: ["SetupWindowCore"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        // Swift Testing works with the Command Line Tools (XCTest does not); `testingMacros` above. The tests depend
        // on the executable so `swift test` builds it: one runs the real binary's page check.
        .testTarget(
            name: "SetupWindowCoreTests",
            dependencies: ["SetupWindowCore", "estia-setup"],
            exclude: ["fixtures"],
            swiftSettings: testingMacros
        ),
    ]
)

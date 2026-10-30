import Foundation

/// The voice service's arguments (besides `--fetch-pocket`, a deploy's): launchd passes none, so the live service
/// listens on 127.0.0.1:8150 with its models folder in its home and speaks Pocket's English on the Mac's model
/// (`PocketTTS.english(memory:)`). A check built here takes `--port <n>`, `--models <full path>`, a models folder of
/// its own, so nothing is fetched into a home (25 Oct: the six languages' real check), and `--pocket 100M` or
/// `--pocket 18M`, the other model on this Mac (26 Oct: each voice's real check on both); `--models` and `--pocket`
/// only with a `--port` of the check's own, never the live one's 8150 (the review of 26 Oct, R2-F8: with only
/// `--pocket 18M` a check took the live address). Anything else is refused (nil: the service logs one line and exits),
/// never read as the defaults (the review of 25 Oct: a typo such as `--port 81510` or `--models scratch` took the live
/// address or fetched into the home the flag keeps clear).
public struct ServiceArguments: Equatable, Sendable {
    public static let defaultPort: UInt16 = 8150

    public let port: UInt16
    /// nil: the voice service's own folder in its home.
    public let models: URL?
    /// nil: the Mac's own (`PocketTTS.english(memory:)`).
    public let pocket: PocketModel?

    public init(port: UInt16, models: URL?, pocket: PocketModel? = nil) {
        self.port = port
        self.models = models
        self.pocket = pocket
    }

    /// nil for an argument it does not take, one without its value, a port that is not 1 to 65535, a models folder
    /// that is not a full path, a Pocket model that is not 100M or 18M, any given twice, or a models folder or Pocket
    /// model without a port other than the live one's.
    public init?(_ arguments: [String]) {
        var port: UInt16?
        var models: URL?
        var pocket: PocketModel?
        var index = 1
        while index < arguments.count {
            guard index + 1 < arguments.count else { return nil }
            let value = arguments[index + 1]
            switch arguments[index] {
            case "--port":
                guard port == nil, let number = UInt16(value), number > 0 else { return nil }
                port = number
            case "--models":
                guard models == nil, value.hasPrefix("/") else { return nil }
                models = URL(fileURLWithPath: value)
            case "--pocket":
                guard pocket == nil, let model = PocketTTS.english(size: value) else { return nil }
                pocket = model
            default:
                return nil
            }
            index += 2
        }
        if models != nil || pocket != nil {  // a check's: never on the live address
            guard let port, port != Self.defaultPort else { return nil }
        }
        self.init(port: port ?? Self.defaultPort, models: models, pocket: pocket)
    }

    /// The log's line for arguments it refused (never the arguments themselves).
    public static let refused = "arguments refused: it takes none (launchd's), or --port <1-65535>, --models <full path> and --pocket <100M or 18M>, each once, the last two only with a --port other than 8150; exiting"
}

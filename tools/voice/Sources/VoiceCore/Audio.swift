import Foundation

public enum Limits {
    public static let maxAudioBytes = 10_000_000
    public static let maxAudioSeconds = 180.0
    public static let maxRequestBytes = maxAudioBytes + 64 * 1024
}

/// The audio format from a file's first bytes, never from a claimed type (Hermes: iOS sends M4A,
/// Android ADTS AAC, and both get mislabelled). nil when it is not audio this service reads.
public func sniffAudio(_ data: Data) -> String? {
    let bytes = [UInt8](data.prefix(12))
    func ascii(_ range: Range<Int>) -> String? {
        bytes.count >= range.upperBound ? String(bytes: bytes[range], encoding: .ascii) : nil
    }
    if ascii(4..<8) == "ftyp" { return "m4a" }
    if bytes.count >= 2, bytes[0] == 0xFF, bytes[1] & 0xF6 == 0xF0 { return "aac" }  // ADTS sync, layer 00
    if ascii(0..<4) == "RIFF", ascii(8..<12) == "WAVE" { return "wav" }
    if ascii(0..<3) == "ID3" { return "mp3" }
    if bytes.count >= 2, bytes[0] == 0xFF, bytes[1] & 0xE0 == 0xE0 { return "mp3" }  // MPEG frame sync
    if ascii(0..<4) == "OggS" { return "ogg" }
    return nil
}

/// The samples of a 16 kHz mono 16-bit PCM WAV, read in memory as AVAudioFile reads them (each value / 32768), or nil
/// for any other audio or a broken file. Every call turn comes so (the bridge's `pcm48_to_wav16k`): it reaches Whisper
/// without a file (review C5). The chunks are walked as RIFF lays them out (an odd size padded to even); fmt must come
/// before data, and the data chunk must be whole.
public func pcm16kMonoSamples(_ wav: Data) -> [Float]? {
    let bytes = [UInt8](wav)
    func le(_ at: Int, _ size: Int) -> Int {
        (0..<size).reduce(0) { $0 | Int(bytes[at + $1]) << (8 * $1) }
    }
    func tag(_ at: Int) -> String? { String(bytes: bytes[at..<at + 4], encoding: .ascii) }
    guard bytes.count >= 12, tag(0) == "RIFF", tag(8) == "WAVE" else { return nil }
    var at = 12
    var pcm16kMono = false
    while at + 8 <= bytes.count {
        let size = le(at + 4, 4)
        let body = at + 8
        switch tag(at) {
        case "fmt ":
            guard size >= 16, body + 16 <= bytes.count else { return nil }
            // format 1 (PCM), 1 channel, 16,000 samples a second, 2 bytes a frame, 16 bits
            pcm16kMono = le(body, 2) == 1 && le(body + 2, 2) == 1 && le(body + 4, 4) == 16_000
                && le(body + 12, 2) == 2 && le(body + 14, 2) == 16
            if !pcm16kMono { return nil }
        case "data":
            guard pcm16kMono, size % 2 == 0, body + size <= bytes.count else { return nil }
            return stride(from: body, to: body + size, by: 2).map {
                Float(Int16(bitPattern: UInt16(bytes[$0]) | UInt16(bytes[$0 + 1]) << 8)) / 32_768
            }
        default:
            break
        }
        at = body + size + size % 2
    }
    return nil
}

/// A silent 16 kHz mono 16-bit WAV, for the warm-up transcription at start: the Neural Engine compiles
/// the model on its first use (about 15 minutes after a macOS update), which must not be a user's voice note.
public func silentWAV(seconds: Int) -> Data {
    pcm16WAV([Float](repeating: 0, count: 16_000 * seconds), sampleRate: 16_000)
}

/// A mono 16-bit PCM WAV of `samples` (full scale is -1...1; louder ones are clipped, non-finite ones are silence).
public func pcm16WAV(_ samples: [Float], sampleRate: Int) -> Data {
    let dataSize = samples.count * 2
    func le32(_ value: Int) -> [UInt8] { (0..<4).map { UInt8((value >> (8 * $0)) & 0xFF) } }
    func le16(_ value: Int) -> [UInt8] { (0..<2).map { UInt8((value >> (8 * $0)) & 0xFF) } }
    var header: [UInt8] = Array("RIFF".utf8) + le32(36 + dataSize) + Array("WAVEfmt ".utf8)
    header += le32(16) + le16(1) + le16(1) + le32(sampleRate) + le32(sampleRate * 2) + le16(2) + le16(16)
    header += Array("data".utf8) + le32(dataSize)
    let pcm = samples.map { $0.isFinite ? Int16((min(max($0, -1), 1) * 32767).rounded()).littleEndian : 0 }
    return Data(header) + pcm.withUnsafeBytes { Data($0) }
}

/// What the bridge gets from every voice: Kokoro's own format, 24 kHz mono 16-bit WAV.
public enum SpeechAudio {
    public static let sampleRate = 24_000

    /// Float samples (Supertonic-3: 44.1 kHz) in that format, resampled, at their own level (non-finite samples
    /// become silence). Not peak-normalised as FluidAudio's CLI and OpenWhisperer do: measured here, Supertonic-3
    /// and Kokoro both speak at about -26 dBFS RMS, and normalising made Supertonic 5-9 dB louder, varying by
    /// sentence (a call mixes both voices, sentence by sentence).
    public static func wav(_ samples: [Float], sampleRate: Int) -> Data {
        pcm16WAV(resample(samples.map { $0.isFinite ? $0 : 0 }, from: sampleRate, to: Self.sampleRate), sampleRate: Self.sampleRate)
    }
}

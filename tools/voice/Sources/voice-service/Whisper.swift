// Whisper's files at start: the CoreML model and its tokenizer, each pinned to one commit and checked file by file
// against its sha256 (`WhisperModel`; about 0.6 s for the 1.6 GB). Missing files are downloaded at the pinned commit
// (the first start, or a download that stopped); then WhisperKit loads only from those checked folders: no listing of
// the repository, no download, no request to Hugging Face.
import AVFoundation
import Foundation
import VoiceCore
import WhisperKit

/// What Whisper needs, the model first.
let whisperPins = [WhisperModel.files, WhisperModel.tokenizer]

/// Downloads a pin's files at its commit into the models folder, in WhisperKit's own layout (so a copy already there
/// is checked, not fetched again).
func downloadPinned(_ pin: HubPin, models: URL) async throws {
    _ = try await HubApiWrapper(downloadBase: models).snapshot(
        from: HubApiWrapper.Repo(id: pin.repo), revision: pin.revision, matching: pin.globs)
}

/// WhisperKit's settings: the checked model folder and the tokenizer under the models folder, never a download. With a
/// model folder WhisperKit skips its download step entirely; the tokenizer it reads from where it looks first,
/// `<models>/models/openai/whisper-large-v3` (it would fetch one from Hugging Face only if those checked files failed
/// to read).
func whisperConfig(models: URL) -> WhisperKitConfig {
    WhisperKitConfig(
        modelFolder: WhisperModel.files.location(in: models).path, tokenizerFolder: models, load: true, download: false)
}

/// Whisper's input for a clip, and the clip's length in seconds. A 16 kHz mono 16-bit WAV (every call turn: the bridge's
/// `pcm48_to_wav16k`) goes to `samples` in memory and never touches the disk (review C5). Any other audio (a voice
/// note, a shared file) goes to `file` as a private file in `temp` (0600, in her 0700 folder) that lives only for the
/// transcription; a leftover after a crash goes at the next start. Over `Limits.maxAudioSeconds`: `tooLong`.
func transcribeClip(
    _ audio: Data, kind: String, temp: URL,
    samples: ([Float]) async throws -> [TranscriptionResult],
    file: (String) async throws -> [TranscriptionResult]
) async throws -> (results: [TranscriptionResult], duration: Double) {
    if let pcm = pcm16kMonoSamples(audio) {
        let duration = Double(pcm.count) / 16_000
        guard duration <= Limits.maxAudioSeconds else { throw VoiceError.tooLong }
        return (try await samples(pcm), duration)
    }
    let path = temp.appendingPathComponent("\(UUID().uuidString).\(kind)")
    guard FileManager.default.createFile(atPath: path.path, contents: audio, attributes: [.posixPermissions: 0o600]) else {
        throw VoiceError.failed
    }
    defer { try? FileManager.default.removeItem(at: path) }
    let duration: Double
    do {
        let audioFile = try AVAudioFile(forReading: path)
        duration = Double(audioFile.length) / audioFile.fileFormat.sampleRate
    } catch {
        throw VoiceError.unreadable
    }
    guard duration <= Limits.maxAudioSeconds else { throw VoiceError.tooLong }
    return (try await file(path.path), duration)
}

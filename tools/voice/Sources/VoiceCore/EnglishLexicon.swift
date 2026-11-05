import Foundation

/// Capitals meant as their letters ("I D", "E T A", "W H O"): Pocket reads them as words (24 Oct, eve through Whisper:
/// "WHO is it?" came out "Though is it?"), so its text writes them as letters (`PocketText.letters`: "I-D"). By their
/// capitals only, so "id" and "who" stay words. Kokoro, removed on 3 Nov 2026, got them as a pronunciation table over
/// its own lexicon (Misaki has "ID" as "id", "ETA" as the Greek letter and "WHO" as "who").
public enum EnglishLexicon {
    /// Capitals said as their letters.
    static let spelledWords = ["ID", "ETA", "WHO"]
}

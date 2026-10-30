import Foundation

/// Pocket's text, paced (25 Oct): a long stretch of words with no pause mark is joined with commas into pieces the
/// small 18M model can say. Measured through the voice service, eve and jane in turn: a stretch of about 400 characters
/// (eve: 404-434, 21-22 s of speech; jane's later) with no comma runs away: the clip goes on at 0.45-0.9 s a word to
/// the program's budget (1.5 times the words' time) with its end garbled, a run that ends normally as far as the time
/// limit (`PocketTTS.runSeconds`) and the exit status can tell, so nothing says it failed and no stand-in speaks. It
/// is the length of speech, not the count of words (39 long words ran away, 85 short ones did): one comma every 80
/// words kept 200 words whole, and each of the same words with a comma where this pass puts one was spoken in full.
/// Only Pocket's text (English, and the six languages jane speaks: their own words, `Words`), only a stretch over
/// `limit`; anything shorter, and every text a call's answers usually are, is returned byte for byte. A long stretch is
/// cut into pieces of about `aim` (three quarters of the limit).
public enum PocketPace {
    /// The longest stretch left without a pause mark, in reading length: a word counts its letters and the space after
    /// it, at least 5, so 40 words of any length up to four letters and 200 characters of longer ones. Well under the
    /// 400 where the small model first ran away (about half), and 40 words is a long breath for anyone. English's and
    /// most languages' (`limit(for:)`: Portuguese's is shorter).
    public static let limit = 200

    /// `language`'s limit: 200, Portuguese's 150 (25 Oct, jane, a stretch of long words with no comma: the first failure
    /// in reading length at 234 in Portuguese, 309 in Italian, 328 in German, 339 in Spanish, 400 in Dutch, 649 in French;
    /// each limit about two thirds of it or less).
    public static func limit(for language: String) -> Int { Words.of(language).limit }

    /// The size the pieces of a long stretch aim at: three quarters of the limit, so a break has room to go where it
    /// sounds best (before a conjunction, up to a quarter of a piece off the even share) and every piece stays within
    /// the limit.
    static let aim = aim(limit)
    static func aim(_ limit: Int) -> Int { limit * 3 / 4 }

    // What a break before a word costs on top of its distance from the even share, in characters, for a limit.
    private static func prepositionCost(_ limit: Int) -> Int { limit / 20 }  // "with", "for", "to" ...
    private static func placeCost(_ limit: Int) -> Int { limit / 10 }  // "in", "on", "at" ...
    private static func wordCost(_ limit: Int) -> Int { limit * 3 / 20 }  // any other word
    private static func phraseCost(_ limit: Int) -> Int { limit * 7 / 20 }  // a determiner or an object pronoun: it starts a phrase
    // inside a number, after a word that needs the next one: nothing else fits (the limit itself)

    /// `text` with a comma after the word before each break, by `language`'s words (`Words`). Unchanged when no
    /// stretch is over the limit.
    public static func paced(_ text: String, language: String = Languages.english) -> String {
        let words = text.split(whereSeparator: \.isWhitespace)
        let lists = Words.of(language)
        var commas: [String.Index] = []
        for run in stretches(words) where weight(words[run]) > lists.limit {
            commas += breaks(Array(words[run]), lists).map { words[run.lowerBound + $0 - 1].endIndex }
        }
        guard !commas.isEmpty else { return text }
        var out = ""
        var cursor = text.startIndex
        for at in commas {
            out += String(text[cursor..<at]) + ","
            cursor = at
        }
        return out + text[cursor...]
    }

    /// The reading length of each stretch between pause marks (a test's look at what `paced` leaves).
    static func runs(_ text: String) -> [Int] {
        let words = text.split(whereSeparator: \.isWhitespace)
        return stretches(words).map { weight(words[$0]) }
    }

    // MARK: Stretches

    private static let closers: Set<Character> = ["\"", "'", ")", "]", "}", "\u{201D}", "\u{2019}", "\u{00BB}"]
    private static let marks: Set<Character> = [",", ";", ":", ".", "!", "?", "\u{2026}"]
    private static let dashes: Set<Character> = ["\u{2014}", "\u{2013}"]

    /// A pause mark at the end of a word (closing quotes and brackets after it left out), a dash with spaces around it
    /// or inside it, or an ellipsis: the word ends its stretch. A hyphen inside a word is no pause.
    static func pauses(_ word: Substring) -> Bool {
        if word == "-" || word == "--" || word.contains(where: { dashes.contains($0) || $0 == "\u{2026}" }) { return true }
        guard let last = word.last(where: { !closers.contains($0) }) else { return false }
        return marks.contains(last)
    }

    /// The words' indices as stretches, each ending at a word with a pause mark (or the text's end).
    private static func stretches(_ words: [Substring]) -> [Range<Int>] {
        var out: [Range<Int>] = []
        var start = 0
        for (index, word) in words.enumerated() where pauses(word) || index == words.count - 1 {
            out.append(start..<index + 1)
            start = index + 1
        }
        return out
    }

    private static func weight(_ word: Substring) -> Int { max(word.count + 1, 5) }
    private static func weight(_ words: ArraySlice<Substring>) -> Int { words.reduce(0) { $0 + weight($1) } }

    // MARK: Breaks

    /// Where a stretch of `words` is cut: the indices of the words that start a new piece. The pieces are as many as
    /// the limit asks for, each break the best near its even share (nearer is better; before a conjunction best, before
    /// "with", "for", "to" and the like next, any other word after that), never inside a number or after a word that
    /// needs the next one unless nothing else can keep every piece within the limit.
    static func breaks(_ words: [Substring], _ lists: Words = .english) -> [Int] {
        let (limit, aim) = (lists.limit, aim(lists.limit))
        let weights = words.map(weight)
        var before = [0]  // before[i]: the reading length of words 0..<i
        for weight in weights { before.append(before[before.count - 1] + weight) }
        let total = before[words.count]
        guard total > limit else { return [] }
        let pieces = (total + aim - 1) / aim
        let cores = words.map(core)
        var cuts: [Int] = []
        var from = 0
        for piece in 1..<pieces {
            let share = total * piece / pieces
            let room = (from + 1..<words.count).filter {
                before[$0] - before[from] <= limit && total - before[$0] <= (pieces - piece) * limit
            }
            let open = room.isEmpty ? Array(from + 1..<words.count) : room
            guard let cut = open.min(by: { cost($0, cores, before, share, lists) < cost($1, cores, before, share, lists) }) else {
                break
            }
            cuts.append(cut)
            from = cut
        }
        return cuts
    }

    /// How bad a break before word `index` is: its distance from the even share, and what the word is.
    private static func cost(_ index: Int, _ cores: [String], _ before: [Int], _ share: Int, _ lists: Words) -> Int {
        let (distance, limit) = (abs(before[index] - share), lists.limit)
        if inNumber(index, cores, lists) || lists.needsNext.contains(cores[index - 1]) { return distance + limit }
        let word = cores[index]
        if lists.conjunctions.contains(word) { return distance }
        if lists.prepositions.contains(word) { return distance + prepositionCost(limit) }
        if lists.places.contains(word) { return distance + placeCost(limit) }
        return distance + (lists.opens.contains(word) ? phraseCost(limit) : wordCost(limit))
    }

    /// The word as a lower-case run of letters and digits (an apostrophe inside stays: a list's word never ends with
    /// one, "l'heure" is one word).
    static func core(_ word: Substring) -> String {
        word.lowercased().trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
    }

    /// A word of a number as the passes leave it (spoken digits, "hundred", "half past"; "fünfunddreißig", "Uhr"), or
    /// still digits.
    private static func isNumber(_ core: String, _ lists: Words) -> Bool {
        lists.numbers.contains(core) || core.contains(where: \.isNumber)
    }

    /// A break before word `index` would cut a number: between two number words ("nine fifteen", "twenty one", "a
    /// quarter to nine": "quarter" and "nine"), or at an "and", "to", "past" or "till" between two, or an "and" before
    /// one ("hundred and twelve", "dollars and fifty cents").
    private static func inNumber(_ index: Int, _ cores: [String], _ lists: Words) -> Bool {
        let previous = isNumber(cores[index - 1], lists)
        let current = isNumber(cores[index], lists)
        let next = index + 1 < cores.count && isNumber(cores[index + 1], lists)
        if previous && current { return true }
        if cores[index] == lists.and && next { return true }
        return lists.numberLinks.contains(cores[index]) && previous && next
    }

    private static let numberLinks: Set<String> = ["and", "to", "past", "till", "through", "or"]

    private static let numbers: Set<String> = Set(
        """
        zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen \
        eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion \
        trillion oh point half quarter dozen first second third fourth fifth sixth seventh eighth ninth tenth eleventh \
        twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth thirtieth \
        fortieth fiftieth sixtieth seventieth eightieth ninetieth hundredth thousandth
        """.split(separator: " ").map(String.init))

    /// The best words to break before: a clause begins.
    private static let conjunctions: Set<String> = [
        "and", "but", "or", "so", "because", "which", "while", "when", "although", "though", "if", "unless", "until", "since",
        "where", "whereas", "who", "then", "yet", "that",
    ]

    /// Next best: a phrase begins.
    private static let prepositions: Set<String> = [
        "with", "for", "to", "from", "after", "before", "about", "into", "than", "without", "around", "during", "through",
        "between",
    ]

    /// A place or a time begins ("in the middle", "on Friday"): less good than a clause or a phrase of "with", "for", "to".
    private static let places: Set<String> = [
        "in", "on", "at", "by", "under", "over", "near", "across", "along", "inside", "outside", "behind", "beside", "toward",
        "towards", "against", "among", "upon", "past", "off", "up", "down",
    ]

    /// The start of a phrase: breaking before it would leave its verb or preposition without it ("put it in, a note",
    /// "remind, you tonight"); taken only when nothing else is near.
    private static let opens: Set<String> = [
        "a", "an", "the", "this", "these", "those", "my", "your", "his", "her", "its", "our", "their", "me", "him", "them", "us",
        "you", "it",
    ]

    /// Words a piece does not end with: they need the next word (an article, a possessive, a preposition, a conjunction,
    /// a helping verb, "want", "going", a subject pronoun).
    private static let needsNext: Set<String> = Set(
        """
        a an the this that these those my your his her its our their some any no every each all both another other such \
        of in on at by for from with to into onto over under about after before than as per through between around \
        during without within upon and or but so because if when while although though which who whose where until since \
        unless not is are was were be been am will would can could should may might must shall do does did have has had \
        going want wants need needs i you we they he she it there
        """.split(separator: " ").map(String.init))
}

// MARK: - Each language's words

extension PocketPace {
    /// The words a language's breaks look at, each in lower case: English's lists above; in the six jane speaks
    /// (es, it, pt, nl, de, fr) a language-neutral break with each language's common conjunctions (the best place), the
    /// words a piece does not end with (articles, possessives, conjunctions; in es, it, pt and fr prepositions too) and
    /// its number words (every word the language's passes spell numbers and times with, from its own spell-out: never
    /// cut).
    struct Words: Sendable {
        let numbers: Set<String>
        /// Its "and" ("y", "e", "et", "und", "en"): never broken before when a number follows ("treinta y cinco").
        let and: String
        /// Words between two number words that join them ("nueve y quince", "de quatorze à quinze").
        let numberLinks: Set<String>
        let conjunctions: Set<String>
        var prepositions: Set<String> = []
        var places: Set<String> = []
        var opens: Set<String> = []
        let needsNext: Set<String>
        /// Its longest stretch without a pause mark (`PocketPace.limit(for:)`).
        var limit = PocketPace.limit

        static let english = Words(
            numbers: PocketPace.numbers, and: "and", numberLinks: PocketPace.numberLinks, conjunctions: PocketPace.conjunctions,
            prepositions: PocketPace.prepositions, places: PocketPace.places, opens: PocketPace.opens,
            needsNext: PocketPace.needsNext)

        /// `language`'s words; English's for a language without its own.
        static func of(_ language: String) -> Words { languages[language] ?? english }

        private static let languages: [String: Words] = [
            "es": Words(
                numbers: spelled("es", extra: []), and: "y", numberLinks: ["y", "e", "a", "o", "u", "menos"],
                conjunctions: words("y e o u pero porque que cuando si aunque mientras donde como pues entonces sino ni luego"),
                needsNext: words("""
                    el la los las un una unos unas lo de del al a en con por para sin sobre entre hasta desde y e o u que \
                    mi mis tu tus su sus nuestro nuestra no muy se me te le les
                    """)),
            "it": Words(
                numbers: spelled("it", extra: []), and: "e", numberLinks: ["e", "ed", "a", "o", "meno"],
                conjunctions: words("e ed o ma perché che quando se mentre però oppure dove come quindi allora poi anche"),
                needsNext: words("""
                    il lo la i gli le un uno una di del dello della dei degli delle a al allo alla ai agli alle da dal \
                    dalla dai in nel nello nella nei con su sul sulla per tra fra e ed o che mio mia tuo tua suo sua non \
                    molto ci si mi ti
                    """)),
            "pt": Words(
                numbers: spelled("pt", extra: ["hora", "horas"]), and: "e", numberLinks: ["e", "a", "às", "ou", "menos"],
                conjunctions: words("e ou mas porque que quando se enquanto embora onde como então pois nem depois"),
                needsNext: words("""
                    o a os as um uma uns umas de do da dos das em no na nos nas por pelo pela com para sem até e ou que \
                    meu minha teu tua seu sua não muito
                    """), limit: 150),
            // Dutch and German put verbs and their particles at a clause's end ("komt ... langs en", "bringt ... mit
            // und"): a piece may end with them, so only articles, possessives and conjunctions need the next word
            "nl": Words(
                numbers: spelled("nl", extra: []), and: "en", numberLinks: ["en", "tot", "of"],
                conjunctions: words("en of maar want omdat dat als wanneer toen terwijl dus dan hoewel zodat voordat nadat totdat"),
                needsNext: words("de het een mijn jouw je zijn onze hun en of maar want omdat dat te")),
            "de": Words(
                numbers: spelled("de", extra: []), and: "und", numberLinks: ["und", "bis", "oder"],
                conjunctions: words("und oder aber weil dass wenn als ob denn sondern damit während obwohl bevor nachdem dann also sowie"),
                needsNext: words("""
                    der die das den dem des ein eine einen einem einer eines kein keine keinen mein meine meinen dein \
                    deine sein seine unser unsere zum zur im am vom beim und oder aber weil dass wenn
                    """)),
            "fr": Words(
                numbers: spelled("fr", extra: ["heure", "heures"]), and: "et", numberLinks: ["et", "à", "ou", "moins"],
                conjunctions: words("et ou mais car que quand si puis donc alors lorsque comme où ni sinon ensuite"),
                needsNext: words("""
                    le la les un une des du de au aux à en dans par pour sur avec sans chez et ou que mon ma mes ton \
                    ta tes son sa ses notre votre leur ne je tu il ils on très
                    """)),
        ]

        private static func words(_ list: String) -> Set<String> { Set(list.split(separator: " ").map(String.init)) }

        /// Every word `code`'s passes spell a number with (its pack's cardinals, each gender, 0 to 100, the hundreds
        /// and the large units; a hyphenated one whole and in parts), its time word ("Uhr", "uur") and `extra`.
        private static func spelled(_ code: String, extra: Set<String>) -> Set<String> {
            guard let pack = SpokenLanguage.packs.first(where: { $0.code == code }) else { return extra }
            var found = extra
            if let unit = pack.timeUnit { found.insert(unit.lowercased()) }
            let values = Array(0...100) + stride(from: 200, through: 900, by: 100) + [1000, 2000, 1_000_000, 2_000_000, 1_000_000_000]
            for value in values {
                for gender in [Inflect.Gender.plain, .masculine, .feminine] {
                    for word in pack.cardinal(value, gender).lowercased().split(whereSeparator: \.isWhitespace) {
                        found.insert(String(word))
                        for part in word.split(separator: "-") { found.insert(String(part)) }
                    }
                }
            }
            return found.subtracting(["y", "e", "et", "und", "en", "de"])  // joins, not numbers: `numberLinks`
        }
    }
}

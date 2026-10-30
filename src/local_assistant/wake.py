"""Her name in a call turn, which ends standby on a call (owner, 18 Oct). Whisper writes a name as it hears it, so the
name and each word of the turn are compared by sound: Russian, Japanese and Korean letters written in Latin ones,
then letters that sound alike made one ("ph", "f" and "p"; "l" and "r"; doubled letters), as a name in those languages
is written with the letters they have (Korean has no "f": 티파니 is "tipani"). A name of six or more such letters may be
one letter off (Whisper's "Tiffanie"); a shorter one must match exactly, so Spanish "esta" never counts for Estia. A
leading "h" is compared both ways: left out of the word and the name (Whisper's "Hestia" for Estia), and kept in both,
at the tolerance of the name's length with it (Whisper's "Hether" for Heather, six letters with its h). Known limits: a word that sounds like her name counts as it ("Tiffany said that" ends standby, and
Italian "mia" for Mia); a name of two words counts only whole; a short name another language writes with another
vowel is missed (Russian "Сэм" for Sam); untested on Whisper's output on a live call."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection

_CYRILLIC = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        ("a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
         "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"),
        strict=True,
    )
)  # fmt: skip
# Hangul syllables: initial, vowel and final jamo in Revised Romanization; ㅡ ("eu") is left out, as it is the vowel
# Korean adds between consonants of a foreign word (에스티아, "estia"), and ㅐ is "a", the a of a foreign name (샘, Sam)
_L = ("g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h")  # fmt: skip
_V = ("a", "a", "ya", "ya", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "",
      "ui", "i")  # fmt: skip
_T = ("", "k", "k", "ks", "n", "nj", "nh", "t", "l", "lk", "lm", "lb", "ls", "lt", "lp", "lh", "m", "p", "ps", "t", "t",
      "ng", "t", "t", "k", "t", "p", "t")  # fmt: skip
# Katakana in Hepburn; the small vowels and ャュョ join the kana before them (ティ "ti", ファ "fa"), ー and ッ are left
# out, and the "u" or "o" Japanese adds to a foreign consonant (ス "su" in エスティア) is left out before another
# consonant and at a word's end
_KANA = dict(
    zip(
        "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
        "ガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポヴ",
        ("a", "i", "u", "e", "o", "ka", "ki", "ku", "ke", "ko", "sa", "shi", "su", "se", "so", "ta", "chi", "tsu", "te",
         "to", "na", "ni", "nu", "ne", "no", "ha", "hi", "fu", "he", "ho", "ma", "mi", "mu", "me", "mo", "ya", "yu", "yo",
         "ra", "ri", "ru", "re", "ro", "wa", "o", "n", "ga", "gi", "gu", "ge", "go", "za", "ji", "zu", "ze", "zo", "da",
         "ji", "zu", "de", "do", "ba", "bi", "bu", "be", "bo", "pa", "pi", "pu", "pe", "po", "vu"),
        strict=True,
    )
)  # fmt: skip
_SMALL = {"ァ": "a", "ィ": "i", "ゥ": "u", "ェ": "e", "ォ": "o", "ャ": "ya", "ュ": "yu", "ョ": "yo"}
_ADDED_VOWEL = {"ス", "ク", "グ", "ズ", "ブ", "プ", "フ", "ム", "ル", "ト", "ド"}
_KOREAN_ENDINGS = ("이야", "야", "아", "씨", "님", "이")  # the vocative and the polite forms after a name: 티파니야
_TOKEN = re.compile(r"[゠-ヿ]+|[぀-ゟ]+|(?:[^\W\d_぀-ヿ㐀-鿿]|')+")
# a turn's words for her name alone (only_name): also a run of kanji and a number, which can never be her name or a
# calling word ("エスティア、今何時？" is a request; review of 0.9.7: kanji made no word, so it was her name alone)
_ANY_WORD = re.compile(rf"{_TOKEN.pattern}|[㐀-鿿]+|\d+")
_ALIKE = str.maketrans({"p": "f", "v": "b", "l": "r", "c": "k", "q": "k", "g": "k", "d": "t", "z": "s", "y": "i"})


def _hangul(word: str) -> str:
    out = []
    for char in word:
        code = ord(char) - 0xAC00
        if 0 <= code < 11172:
            out.append(_L[code // 588] + _V[code % 588 // 28] + _T[code % 28])
        else:
            out.append(char)
    return "".join(out)


def _katakana(word: str) -> str:
    out: list[str] = []
    for n, char in enumerate(word):
        if char in _SMALL and out:
            # ティ: "te" + small i is "ti"; ファ: "fu" + small a is "fa"; キャ: "ki" + small ya is "kya"
            out[-1] = out[-1][:-1] + _SMALL[char]
            continue
        sound = _KANA.get(char, "")
        after = word[n + 1 : n + 2]
        if char in _ADDED_VOWEL and (not after or (after in _KANA and _KANA[after][0] not in "aiueo")):
            sound = sound[:-1]
        out.append(sound)
    return "".join(out)


def _sound(word: str, *, keep_h: bool = False) -> str:
    """A word as compared: in Latin letters, without accents, and letters that sound alike made one; its leading h
    left out, unless `keep_h`."""
    word = unicodedata.normalize("NFKC", word).casefold().removesuffix("'s")
    if any("゠" <= char <= "ヿ" for char in word):
        word = _katakana(word)
    elif any("가" <= char <= "힣" for char in word):
        word = _hangul(word)
    word = "".join(_CYRILLIC.get(char, char) for char in word)
    word = "".join(char for char in unicodedata.normalize("NFKD", word) if not unicodedata.combining(char))
    word = re.sub(r"[^a-z]", "", word).replace("ph", "f")
    # an h after a consonant is silent, and a leading one too ("Hestia" for Estia: the review of 28 Oct, calls-02)
    # unless it is kept for the other comparison (`_is_name`)
    word = re.sub(r"(?<=[b-df-hj-np-tv-z])h", "", word)
    word = (word if keep_h else word.removeprefix("h")).replace("x", "ks").translate(_ALIKE)
    return re.sub(r"(.)\1+", r"\1", word)


def _distance(heard: str, name: str) -> int:
    """Letters to change, add or drop to make `heard` the name (edit distance, one row at a time)."""
    before = list(range(len(name) + 1))
    for i, char in enumerate(heard, 1):
        row = [i]
        for j, other in enumerate(name, 1):
            row.append(min(before[j] + 1, row[j - 1] + 1, before[j - 1] + (char != other)))
        before = row
    return before[-1]


def _near(heard: str, name: str) -> bool:
    if heard == name:
        return True
    if len(name) < 6 or abs(len(heard) - len(name)) > 1:
        return False
    return _distance(heard, name) <= 1


def _forms(token: str, *, keep_h: bool = False) -> set[str]:
    """A turn's word as compared, and for a Korean word also without a vocative or polite ending (티파니야)."""
    forms = {_sound(token, keep_h=keep_h)}
    for ending in _KOREAN_ENDINGS:
        if token.endswith(ending) and len(token) > len(ending) and "가" <= token[0] <= "힣":
            forms.add(_sound(token[: -len(ending)], keep_h=keep_h))
    return forms - {""}


def _is_name(tokens: list[str], name: str) -> bool:
    """Whether these words in a row are her name as it sounds: with a leading h left out of both ("Hestia" for Estia),
    or kept in both, at the tolerance of the name's length with it (the second deep scan of 0.9.8,
    calls-h-name-tolerance: "Heather" without its h is five letters, so Whisper's "Hether" had to match exactly)."""
    for keep_h in (False, True):
        wanted = "".join(_sound(word, keep_h=keep_h) for word in _TOKEN.findall(name))
        if wanted and any(_near(joined, wanted) for joined in _joined([_forms(t, keep_h=keep_h) for t in tokens])):
            return True
    return False


def says_name(text: str, name: str) -> bool:
    """Whether a call turn says her name: one word of it, or its words in a row, as the name sounds."""
    if not "".join(_sound(word) for word in _TOKEN.findall(name)):
        return False
    tokens = _TOKEN.findall(text)
    parts = len([word for word in _TOKEN.findall(name) if _sound(word)])
    for start in range(len(tokens)):
        for size in {1, parts}:
            run = tokens[start : start + size]
            if len(run) == size and _is_name(run, name):
                return True
    return False


def nearest(text: str, name: str) -> int:
    """How many letters the turn's word nearest to her name is off it, as `says_name` compares (one word, or the words
    of a name of two in a row): 0 is her name as it sounds. For standby's log line only, a number and never a word, so
    a missed name shows as a near miss; a turn without letters is as far off as her name is long."""
    wanted = "".join(_sound(word) for word in _TOKEN.findall(name))
    words = [_forms(token) for token in _TOKEN.findall(text)]
    parts = len([word for word in _TOKEN.findall(name) if _sound(word)])
    found = len(wanted)
    for start in range(len(words)):
        for size in {1, parts}:
            run = words[start : start + size]
            if len(run) == size:
                found = min([found, *(_distance(joined, wanted) for joined in _joined(run))])
    return found


def _joined(run: list[set[str]]) -> set[str]:
    out = {""}
    for forms in run:
        out = {done + form for done in out for form in forms}
    return out


def only_name(text: str, name: str, soft: Collection[str]) -> bool:
    """Whether a call turn is her name alone ("Estia?", "Hey Estia."): every word is her name (as `says_name`
    hears it: one word, or the words of a name of two in a row) or one of `soft`, the call's calling words ("hey",
    the Japanese honorific さん), and her name is there. A run of kanji or a number is a word of its own, never her
    name ("エスティア、今何時？" is more)."""
    wanted = "".join(_sound(word) for word in _TOKEN.findall(name))
    if not wanted:
        return False
    parts = len([word for word in _TOKEN.findall(name) if _sound(word)])
    tokens = _ANY_WORD.findall(text)
    calling = {word.casefold() for word in soft}
    sounds = {_sound(word) for word in soft} - {""}
    heard, at = False, 0
    while at < len(tokens):
        token = tokens[at]
        if token.casefold() in calling or _sound(token) in sounds:
            at += 1
            continue
        size = next(
            (
                size
                for size in sorted({1, parts}, reverse=True)
                if len(tokens[at : at + size]) == size and _is_name(tokens[at : at + size], name)
            ),
            0,
        )
        if not size:
            return False
        heard, at = True, at + size
    return heard

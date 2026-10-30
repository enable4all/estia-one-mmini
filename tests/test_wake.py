"""Her name in a call turn (`wake.says_name`), which ends standby on a call (owner, 18 Oct: after the check-in "lets
respond only if we hear the name"). Whisper writes a name as it hears it: "Tiffani", and in Russian, Japanese and
Korean in their own letters ("Тиффани", "ティファニー", "티파니"). A turn said to someone else, without it, must never
count; a word that only looks like it ("esta" for Estia) neither."""

import pytest

from local_assistant.wake import nearest, only_name, says_name


@pytest.mark.parametrize(
    "turn",
    [
        "Tiffany, what time is it?",
        "tiffany",
        "TIFFANY!",
        "Hey Tiffany, are you there?",
        "Okay, Tiffany. Next question.",
        "Tiffani, are you there?",  # Whisper's spellings
        "Tifany?",
        "Tiffanie, hello.",
        "Tiffany's still there?",
        "Тиффани, который час?",  # Russian
        "Тифани, ты здесь?",
        "ティファニー、今何時？",  # Japanese
        "ティファニーさん、聞こえる？",
        "티파니, 지금 몇 시야?",  # Korean
        "티파니야, 들려?",  # the Korean vocative
    ],
)
def test_tiffany_is_heard_in_every_script(turn: str) -> None:
    assert says_name(turn, "Tiffany")


@pytest.mark.parametrize(
    "turn",
    [
        "I was talking to my sister.",
        "Tiff, come here.",  # a short form is not her name
        "Stephanie is at the door.",
        "Can you pass the salt?",
        "",
        "Um, uh.",
        "Я сейчас приду.",
        "テスト中です。",
        "테스트 중이야.",
    ],
)
def test_a_turn_without_tiffany_is_not_her_name(turn: str) -> None:
    assert not says_name(turn, "Tiffany")


@pytest.mark.parametrize(
    "turn",
    [
        "Estia, are you there?",
        "Éstia?",
        "Hey estia.",
        "Эстия, привет.",
        "エスティア、聞こえる？",
        "에스티아, 들려?",
        "에스티아야.",
    ],
)
def test_estia_is_heard_in_every_script(turn: str) -> None:
    assert says_name(turn, "Estia")


@pytest.mark.parametrize("turn", ["Hestia, are you there?", "Hey Hestia.", "hestia"])
def test_a_leading_h_is_silent(turn: str) -> None:
    """The review of 28 Oct (calls-02): Whisper writes the goddess's name, "Hestia", for Estia, and a name of five
    letters must match exactly, so it never woke her. A word's leading "h" is left out, in the name too."""
    assert says_name(turn, "Estia") and nearest(turn, "Estia") == 0


def test_a_name_with_a_leading_h_is_heard_with_and_without_it() -> None:
    assert says_name("Hannah, hi!", "Hannah") and says_name("Annah?", "Hannah")
    assert not says_name("Hostia, che freddo!", "Estia")  # Italian: one letter off once the h is left out


@pytest.mark.parametrize(
    ("turn", "name", "heard"),
    [
        ("Hether, are you there?", "Heather", True),  # one letter off: six sounds with its h, so one may be off
        ("Heather?", "Heather", True),
        ("Ether, hi.", "Heather", False),  # one off without the h: five sounds, so an exact match only
        ("Hestia?", "Estia", True),  # the h left out of both: an exact match
        ("Easter is late.", "Estia", False),
        ("Esta noche.", "Estia", False),
        ("Hostia, che freddo!", "Estia", False),
    ],
)
def test_a_name_with_a_leading_h_keeps_its_one_letter_tolerance(turn: str, name: str, heard: bool) -> None:
    """The second deep scan (calls-h-name-tolerance): with a leading h always left out, "Heather" became five sounds,
    and a name under six must match exactly, so Whisper's "Hether" never woke her. A word is compared with its h
    against the name with its h, at that length's tolerance, and with both h's left out, as before."""
    assert says_name(turn, name) is heard


def test_a_name_with_a_leading_h_alone_is_her_name_alone() -> None:
    assert only_name("Hether?", "Heather", {"hey"}) and only_name("Hey Hether.", "Heather", {"hey"})


@pytest.mark.parametrize(
    "turn",
    [
        "Esta noche vamos al cine.",  # Spanish: one letter from Estia
        "Está bien, gracias.",
        "Ostia, che freddo!",  # Italian
        "Testa questo.",
        "Есть время?",  # Russian "есть" sounds like the start of Estia
        "Это всё.",
    ],
)
def test_a_word_one_letter_from_a_short_name_is_not_her_name(turn: str) -> None:
    assert not says_name(turn, "Estia")


@pytest.mark.parametrize(("turn", "heard"), [("Mary Jane, hi!", True), ("Mary-Jane?", True), ("Maryjane.", True)])
def test_a_name_of_two_words_is_heard_whole(turn: str, heard: bool) -> None:
    assert says_name(turn, "Mary-Jane") is heard


def test_one_word_of_a_name_of_two_is_not_her_name() -> None:
    assert not says_name("Mary, come here.", "Mary-Jane")
    assert not says_name("Jane said so.", "Mary Jane")


def test_a_name_without_letters_is_never_heard() -> None:
    assert not says_name("Tiffany, hi", "")
    assert not says_name("Tiffany, hi", "123")


@pytest.mark.parametrize("turn", ["Sam, hi!", "サム、聞こえる？", "샘, 들려?"])
def test_a_short_name_is_heard_in_every_script(turn: str) -> None:
    assert says_name(turn, "Sam")


def test_a_short_name_with_another_vowel_is_missed() -> None:
    """Known limit: Russian writes Sam "Сэм" ("sem"), and a name under six letters must match exactly (Spanish "esta"
    must never count for Estia), so it is missed; the caller can hang up and call again."""
    assert not says_name("Сэм, ты тут?", "Sam")


# her name alone (wake.only_name): standby ends and code's wake line answers it, with no model; Gemma would add 2 to
# 22 s (the owner's 51 call turns, 22 Oct) and her answer would land over the caller's next words
SOFT = ("hey", "hi", "okay", "oh", "um", "さん", "저기")


@pytest.mark.parametrize(
    "turn",
    [
        "Tiffany",
        "Tiffany?",
        "Tiffany!",
        "Hey Tiffany.",
        "Okay, Tiffany.",
        "Um, Tiffany?",
        "Tiffanie?",
        "Hey, hey Tiffany",
    ],
)
def test_her_name_alone_or_with_a_calling_word_is_her_name_alone(turn: str) -> None:
    assert only_name(turn, "Tiffany", SOFT)


@pytest.mark.parametrize(
    "turn",
    [
        "Tiffany, are you there?",  # a question: her answer
        "Tiffany said that.",
        "Hey Tiffany, what time is it?",
        "hey",  # no name
        "",
        "Stephanie?",
        "Tiffany Tiffany Tiffany, listen",
    ],
)
def test_a_turn_with_more_than_her_name_is_not_her_name_alone(turn: str) -> None:
    assert not only_name(turn, "Tiffany", SOFT)


def test_her_name_alone_in_every_script_and_a_name_of_two_words() -> None:
    assert only_name("ティファニーさん？", "Tiffany", SOFT)  # Japanese: the honorific is a calling word
    assert only_name("티파니야?", "Tiffany", SOFT)  # Korean: the vocative ending
    assert only_name("저기, 티파니?", "Tiffany", SOFT)
    assert only_name("Тиффани?", "Tiffany", SOFT)
    assert only_name("Mary Jane?", "Mary-Jane", SOFT) and only_name("Hey Mary-Jane", "Mary Jane", SOFT)
    assert not only_name("Mary?", "Mary-Jane", SOFT)
    assert not only_name("Tiffany", "", SOFT)


@pytest.mark.parametrize(
    "turn, name",
    [
        ("エスティア、今何時？", "Estia"),  # a request in kanji after her name
        ("ティファニー、天気予報", "Tiffany"),
        ("ティファニー、明日の予定は？", "Tiffany"),  # kanji with hiragana between
        ("Tiffany, 5", "Tiffany"),  # digits are words too
        ("ティファニー、３時", "Tiffany"),  # full-width digits and a kanji
    ],
)
def test_kanji_and_digits_after_her_name_are_more_than_her_name(turn: str, name: str) -> None:
    """Review of 0.9.7 (C3): kanji made no word, so "エスティア、今何時？" was her name alone: in standby her "はい？"
    answered it and the request was dropped, never kept or answered."""
    assert not only_name(turn, name, SOFT)
    assert says_name(turn, name)  # standby still ends on it


@pytest.mark.parametrize(
    "turn, name, off",
    [
        ("Tiffany, are you there?", "Tiffany", 0),
        ("Tiffanie, hello.", "Tiffany", 1),  # one off: her name, for says_name (six letters or more)
        ("Stephanie, are you there?", "Tiffany", 3),
        ("I was talking to my sister.", "Tiffany", 5),
        ("Esta noche.", "Estia", 1),  # one off, but a short name must match exactly
        ("Alex Example, hello", "Alex Example", 0),  # a name of two words, as a run
        ("Alex Exemple, hello", "Alex Example", 1),
    ],
)
def test_a_turns_nearest_word_says_how_many_letters_it_is_off_her_name(turn: str, name: str, off: int) -> None:
    """For the log (a number, never a word): how near a turn in standby came to her name, so a missed name shows as
    a near miss (1 or 2 letters off) and the matching can be tuned on numbers."""
    assert nearest(turn, name) == off


def test_a_turn_without_letters_is_as_far_off_as_her_name_is_long() -> None:
    assert nearest("12 34", "Estia") == len("estia") and nearest("", "Estia") == len("estia")

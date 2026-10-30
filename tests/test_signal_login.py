"""An account without a number ("Signal Login"): its Account ID, its Recovery key and her username, read and checked
in one place (hostsec) for setup and `estia signal-login`; signal-cli's account list read by Account ID; the
Registration Lock line for an account that has no number. Every key and ID here is made up."""

from __future__ import annotations

import json
import os
import select
import termios
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from local_assistant import hostsec
from tests.test_setup import Pty

# made-up keys: 64 of a to z and 0 to 9, with o and 0 in them (the two Signal shows as # and =)
KEY = "abcdefghijklmnopqrstuvwxyz012345" * 2
SHOWN = "ABCDEFGHIJKLMN#PQRSTUVWXYZ=12345" * 2  # as Signal shows it: upper case, O as #, 0 as =
ACCOUNT = "11111111-2222-4333-8444-555555555555"


def groups(text: str, size: int = 4) -> list[str]:
    return [text[at : at + size] for at in range(0, len(text), size)]


@pytest.mark.parametrize(
    "typed",
    [
        KEY,
        SHOWN,
        " ".join(groups(SHOWN)),  # groups of 4
        "\n".join(" ".join(groups(line)) for line in groups(SHOWN, 16)),  # four lines of four groups
        "\n\n".join(groups(SHOWN, 16)),  # blank lines between
        "\n  ".join(groups(SHOWN.lower(), 8)) + "\n\n",  # lower case, indented, blank lines after
        "\t" + SHOWN.replace("#", "O").replace("=", "0") + "\r\n",  # the letters themselves, a tab, CR LF
    ],
    ids=["stored", "shown", "groups", "lines", "blank lines", "lower case", "letters"],
)
def test_the_recovery_key_is_read_as_signal_shows_it(typed: str) -> None:
    assert hostsec.recovery_key(typed) == KEY


def test_the_display_symbols_are_signal_androids_own_map() -> None:
    """Signal for Android's CHARACTER_DISPLAY_MAP ('O' to '#', '0' to '='), read back: one constant, with its source."""
    assert hostsec.KEY_SYMBOLS == {"#": "o", "=": "0"}
    source = Path(hostsec.__file__).read_text(encoding="utf-8")
    assert "Signal-Android/blob/" in source and "AccountEntropyPool.kt" in source and "CHARACTER_DISPLAY_MAP" in source


@pytest.mark.parametrize(
    ("typed", "words"),
    [
        (KEY[:63], "63 characters"),
        (KEY + "a", "65 characters"),
        ("", "0 characters"),
    ],
    ids=["63", "65", "nothing"],
)
def test_a_key_of_another_length_is_refused_with_its_count(typed: str, words: str) -> None:
    with pytest.raises(ValueError) as refused:
        hostsec.recovery_key(typed)
    assert words in str(refused.value) and "64" in str(refused.value)
    assert KEY[:16] not in str(refused.value)  # never echoed


# every kind of space a paste may bring (Unicode's White_Space), and the characters a text shows as nothing
# (Unicode's Default_Ignorable_Code_Point)
SPACES = {
    "no-break space": " ",
    "en space": " ",
    "thin space": " ",
    "narrow no-break space": " ",
    "medium mathematical space": " ",
    "ideographic space": "　",
    "ogham space mark": " ",
    "line separator": " ",
    "paragraph separator": " ",
    "next line": "\u0085",
    "vertical tab": "\v",
    "form feed": "\f",
}
INVISIBLE = {
    "zero width space": "​",
    "zero width non-joiner": "‌",
    "zero width joiner": "‍",
    "word joiner": "⁠",
    "soft hyphen": "­",
    "byte order mark": "﻿",
    "left-to-right mark": "‎",
    "right-to-left mark": "‏",
    "arabic letter mark": "؜",
    "left-to-right embedding": "‪",
    "pop directional formatting": "‬",
    "first strong isolate": "⁨",
    "pop directional isolate": "⁩",
    "variation selector-16": "️",
    "mongolian vowel separator": "᠎",
    "combining grapheme joiner": "͏",
    "tag space": "\U000e0020",
}


@pytest.mark.parametrize("char", [*SPACES.values(), *INVISIBLE.values()], ids=[*SPACES, *INVISIBLE])
def test_every_kind_of_space_and_invisible_character_a_paste_brings_is_left_out(char: str) -> None:
    """Owner, 25 Oct ("shall we be more relaxed on that"): a key pasted from a password manager, a note or a page may
    bring a no-break space, a zero-width one, a soft hyphen or a direction mark, between its groups, inside one or
    around it. None is one of a key's characters, so each is left out wherever it is."""
    for typed in (
        char.join(groups(SHOWN)),
        SHOWN[:10] + char + SHOWN[10:],
        char + " ".join(groups(SHOWN)) + char * 2,
        "\n".join(char + line for line in groups(SHOWN, 16)),
    ):
        assert hostsec.recovery_key(typed) == KEY


def test_the_invisible_characters_are_unicodes_and_never_a_keys() -> None:
    """The invisible characters are Unicode's Default_Ignorable_Code_Point (DerivedCoreProperties.txt, 18.0.0: 4,174
    code points), the ones a text shows as nothing. None is a key's letter or digit, a display symbol or a separator,
    so leaving them out never changes a key; a visible look-alike (a middle dot, another hyphen or dash, a minus) is
    never one of them."""
    every = [chr(code) for first, last in hostsec.INVISIBLE_RANGES for code in range(first, last + 1)]
    assert len(every) == len(set(every)) == 4174
    assert all(map(hostsec.invisible, every)) and all(map(hostsec.invisible, INVISIBLE.values()))
    assert not any(hostsec.key_character(char) or char in hostsec.KEY_SEPARATORS for char in every)
    for look_alike in "·‐‑–—−・˗":
        assert not hostsec.invisible(look_alike), look_alike


@pytest.mark.parametrize(
    "typed",
    [
        "-".join(groups(SHOWN)),
        ".".join(groups(SHOWN)),
        "_".join(groups(SHOWN)),
        "\n".join("-".join(groups(line)) for line in groups(SHOWN, 16)),
        " - ".join(groups(SHOWN, 8)),
        "-" + "._-".join(groups(SHOWN.lower(), 16)) + ".",
        " -​".join(groups(SHOWN)),
    ],
    ids=["dashes", "dots", "underscores", "dashed lines", "spaced dashes", "mixed, at the ends", "with others"],
)
def test_dashes_dots_and_underscores_are_left_out_when_exactly_64_key_characters_remain(typed: str) -> None:
    """A key kept in a password manager or a note in groups with dashes, dots or underscores between them: with 64
    letters, digits, # and = beside them, none of the separators can be one of the key's characters."""
    assert hostsec.recovery_key(typed) == KEY


@pytest.mark.parametrize(
    ("typed", "keyed", "position"),
    [
        ("-".join(groups(SHOWN[:63])), 63, 5),
        (".".join(groups(SHOWN + "A")), 65, 5),
        (SHOWN[:30] + "_" + SHOWN[30:62], 62, 31),
        ("-", 0, 1),
    ],
    ids=["63 and dashes", "65 and dots", "62 and an underscore", "a dash alone"],
)
def test_a_separator_beside_more_or_fewer_than_64_counts_as_a_symbol_signal_does_not_use(
    typed: str, keyed: int, position: int
) -> None:
    """Signal may show a dash, a dot or an underscore for some character one day (a pair added to its display map):
    beside 63 or 65 letters, digits, # and =, a separator is never left out. It is named with its place and the count,
    and the key is typed again; the key itself is never in the line."""
    with pytest.raises(ValueError) as refused:
        hostsec.recovery_key(typed)
    said = str(refused.value)
    count = len(typed.replace(" ", ""))
    symbol = {"-": '"-" (hyphen-minus)', ".": '"." (full stop)', "_": '"_" (low line)'}[typed[position - 1]]
    assert f"Character {position} of the {count} read is {symbol}" in said, said
    assert f"this key has {keyed}" in said and "exactly 64" in said
    assert SHOWN[:8] not in said and KEY[:8] not in said


@pytest.mark.parametrize(
    ("stray", "named"),
    [
        ("*", '"*" (asterisk)'),
        ("%", '"%" (percent sign)'),
        ("!", '"!" (exclamation mark)'),
        (",", '"," (comma)'),
        ("–", '"–" (en dash)'),
        ("́", "U+0301 (combining acute accent)"),
        ("\x07", "U+0007"),
        ("", "U+E000"),
        ("�", '"�" (replacement character)'),
    ],
    ids=[
        "asterisk",
        "percent",
        "exclamation mark",
        "comma",
        "en dash",
        "combining accent",
        "bell",
        "private use",
        "replacement",
    ],
)
def test_a_symbol_signal_does_not_use_is_named_with_its_place_never_dropped_or_guessed(stray: str, named: str) -> None:
    """Any symbol left after the spaces, the invisible characters, the display symbols and the separators is refused,
    never dropped or guessed: a key with one character typed wrong, or with a symbol Signal starts to show one day,
    would otherwise reach Signal as some other key, and each wrong key is a registration Signal counts. The line names
    the symbol (one that does not show on its own by its code) and where it is, spaces and invisible characters not
    counted, never the key."""
    typed = " ".join(groups(SHOWN[:20])) + "​" + stray + SHOWN[20:]
    with pytest.raises(ValueError) as refused:
        hostsec.recovery_key(typed)
    said = str(refused.value)
    assert f"Character 21 of the 65 read is {named}, which Signal does not use" in said, said
    assert "letters, digits, # and =" in said
    assert SHOWN[:8] not in said and KEY[:8] not in said


@pytest.mark.parametrize(
    ("stray", "name"),
    [
        ("\u00f6", "latin small letter o with diaeresis"),
        ("\u0130", "latin capital letter i with dot above"),
        ("\uff10", "fullwidth digit zero"),
        ("\u043e", "cyrillic small letter o"),
        ("\u212a", "kelvin sign"),
        ("\u00b2", "superscript two"),
        ("\u0663", "arabic-indic digit three"),
    ],
    ids=["o with dots", "dotted capital i", "fullwidth zero", "cyrillic o", "kelvin sign", "superscript", "arabic"],
)
def test_a_letter_or_digit_signal_does_not_use_is_named_by_its_kind_never_shown(stray: str, name: str) -> None:
    """Review of P1, R2-S2: a look-alike (a fullwidth or Cyrillic letter, the Kelvin sign) is most likely the key's own
    character typed another way, so showing it, or its name, shows one key character and its place, in root's
    `estia signal-login` and in a terminal that may be an AI's transcript. Only its place and its kind are named; a
    symbol such as "%" is still shown, as the owner asked (25 Oct)."""
    typed = " ".join(groups(SHOWN[:20])) + "\u200b" + stray + SHOWN[20:]
    with pytest.raises(ValueError) as refused:
        hostsec.recovery_key(typed)
    said = str(refused.value)
    assert said.startswith(
        "Character 21 of the 65 read is a letter or digit from another alphabet or a fullwidth form, which Signal "
        "does not use"
    ), said
    assert stray not in said and name not in said.lower() and SHOWN[:8] not in said
    with pytest.raises(ValueError, match=r'is "%" \(percent sign\)'):
        hostsec.recovery_key(typed.replace(stray, "%"))


def test_a_stray_symbol_is_refused_where_the_separators_are_left_out_too() -> None:
    """64 letters, digits, # and = with dashes between them and one stray: the dashes would be left out, the stray never
    is. Its place counts the dashes, as the person sees them."""
    typed = "-".join(groups(SHOWN[:20])) + "%" + "-".join(groups(SHOWN[20:]))
    with pytest.raises(ValueError) as refused:
        hostsec.recovery_key(typed)
    assert f'Character 25 of the {len(typed)} read is "%" (percent sign)' in str(refused.value)


@pytest.mark.parametrize(
    "typed",
    [ACCOUNT, ACCOUNT.upper(), ACCOUNT.replace("-", ""), f"  {ACCOUNT[:18]}\n{ACCOUNT[18:]}\n"],
    ids=["as is", "upper case", "no dashes", "two lines"],
)
def test_the_account_id_is_read_as_a_uuid(typed: str) -> None:
    assert hostsec.account_id(typed) == ACCOUNT


@pytest.mark.parametrize("typed", [ACCOUNT[:-1], ACCOUNT + "5", ACCOUNT.replace("1", "g"), "+306900000000", ""])
def test_anything_else_is_not_an_account_id(typed: str) -> None:
    with pytest.raises(ValueError) as refused:
        hostsec.account_id(typed)
    assert "Account ID" in str(refused.value) and ACCOUNT[:8] not in str(refused.value)


def test_an_account_is_a_number_or_an_account_id() -> None:
    assert hostsec.is_account_id(ACCOUNT) and hostsec.is_account_id(ACCOUNT.upper())
    assert not hostsec.is_account_id("+306900000000") and not hostsec.is_account_id(ACCOUNT.replace("-", ""))
    assert hostsec.shown_account(ACCOUNT) == "the Signal Account ending in 5555"
    assert hostsec.shown_account("+306900000000") == "+306900000000"  # a number is shown as today


@pytest.mark.parametrize(
    "typed", ["estia.42", "Estia.42", "_estia.01", "tiffany_ai.123456789", "abc.10", "  estia.42  "]
)
def test_her_username_has_signals_form(typed: str) -> None:
    assert hostsec.username(typed) == typed.strip()


@pytest.mark.parametrize(
    "typed",
    [
        "estia",
        "estia.4",
        "estia.00",
        "estia.012",
        "4estia.42",
        "es.42",
        "es tia.42",
        "estia.1234567890",
        "a" * 33 + ".42",
    ],
)
def test_a_username_without_signals_form_is_refused(typed: str) -> None:
    with pytest.raises(ValueError) as refused:
        hostsec.username(typed)
    assert "a dot" in str(refused.value) and "two or more digits" in str(refused.value)


# ------------------------------------------------------------------ the hidden read, on a real terminal (a pty)


def read_in_a_pty(typed: str, prompt: str = "Recovery key: ", needed: int = 64) -> tuple[str | BaseException, Pty]:
    pty = Pty()
    got: list[str | BaseException] = []

    def read() -> None:
        try:
            counted = hostsec.key_character if needed == 64 else hostsec.id_character
            got.append(hostsec.read_hidden_lines(prompt, needed, counted, pty.path))
        except (EOFError, OSError) as exc:
            got.append(exc)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    pty.wait_for(prompt.encode())
    pty.type(typed)
    reader.join(10)
    assert got, "the hidden prompt never returned"
    return got[0], pty


def test_a_key_pasted_on_several_lines_with_blank_lines_is_read_whole_and_never_echoed() -> None:
    """The hand test of 23 Oct: a shell's read took the first line of a key shown on several lines and ran the rest as
    commands. Here the lines are read until 64 key characters have come, blank lines between them and all; nothing is
    echoed and the terminal is put back."""
    pasted = "\r\r".join(" ".join(groups(line)) for line in groups(SHOWN, 16)) + "\r"
    got, pty = read_in_a_pty(pasted)
    try:
        assert isinstance(got, str) and hostsec.recovery_key(got) == KEY
        assert b"ABCD" not in pty.seen and b"#" not in pty.seen
        mode = termios.tcgetattr(pty.slave)[3]
        assert mode & termios.ICANON and mode & termios.ECHO
    finally:
        pty.close()


def test_what_is_pasted_after_the_key_is_counted_never_dropped_and_never_left_for_the_shell() -> None:
    """Review of P1, R2-S1: what came after the 64th key character was dropped without a word, so a paste with more
    than the key reached Signal as some other key. It is read with the key (a terminal's escape sequences left out, as
    in the key) whenever it holds a key character, so the key is refused with the count; it never reaches the shell
    or the screen."""
    pasted = "\r\r".join(" ".join(groups(line)) for line in groups(SHOWN, 16)) + "\r\x1b[201~echo left-over\r"
    got, pty = read_in_a_pty(pasted)
    try:
        assert isinstance(got, str) and got.endswith("\necho left-over") and "[201~" not in got
        with pytest.raises(ValueError, match="this key has 76"):  # 64 and the 12 letters of "echo left-over"
            hostsec.recovery_key(got)
        assert b"ABCD" not in pty.seen and b"left-over" not in pty.seen
        os.set_blocking(pty.slave, False)
        with pytest.raises(BlockingIOError):  # nothing of the paste is left for the next program to read
            os.read(pty.slave, 100)
    finally:
        pty.close()


# a password manager's note, pasted whole: the Account ID (32 digits and letters a to f, with dashes), then the key
NOTE = "\r".join([ACCOUNT, *(" ".join(groups(line)) for line in groups(SHOWN, 16))]) + "\r"


def test_a_whole_note_pasted_at_either_question_is_refused_never_cut_to_fit() -> None:
    """Review of P1, R2-S1: at the key question the ID's 32 digits counted, the read ended after the key's second line
    and dropped the rest, and the ID's dashes were left out as separators: exactly 64, a wrong key sent to Signal.
    With the rest counted it is 96, refused; at the Account ID question too, so each value is pasted alone."""
    got, pty = read_in_a_pty(NOTE)
    pty.close()
    assert isinstance(got, str)
    with pytest.raises(ValueError, match="this key has 96"):
        hostsec.recovery_key(got)
    got, pty = read_in_a_pty(NOTE, prompt="Account ID: ", needed=32)
    pty.close()
    assert isinstance(got, str)
    with pytest.raises(ValueError) as refused:
        hostsec.account_id(got)
    assert str(refused.value) == hostsec.ID_BAD.format(count=96)


def test_what_comes_after_the_key_is_read_as_its_lines_are() -> None:
    """The rest is read as a line is: Backspace's DEL and other control bytes left out, so the line names the count,
    never a U+007F the person never saw."""
    got, pty = read_in_a_pty(SHOWN + "\rx\x7fy\x07\r")
    pty.close()
    assert isinstance(got, str) and got.endswith("\nxy")
    with pytest.raises(ValueError, match="That key has 66 characters"):
        hostsec.recovery_key(got)


def test_a_typed_key_and_an_extra_enter_still_pass() -> None:
    got, pty = read_in_a_pty(SHOWN + "\r\r")
    pty.close()
    assert isinstance(got, str) and hostsec.recovery_key(got) == KEY


def test_a_pasted_key_with_invisible_characters_and_dashes_is_read_until_its_64th_key_character() -> None:
    """On a real terminal: a paste with zero-width and no-break spaces and dashes between its groups, on four lines,
    ends at the 64th letter, digit, # or =, as the hidden read counts only those."""
    lines = ("​- ".join(groups(line)) + "﻿" for line in groups(SHOWN, 16))
    got, pty = read_in_a_pty("\r".join(lines) + "\r")
    try:
        assert isinstance(got, str) and hostsec.recovery_key(got) == KEY
    finally:
        pty.close()


def test_a_key_typed_line_by_line_ends_at_64_or_at_a_blank_line() -> None:
    got, pty = read_in_a_pty(SHOWN[:32] + "\r" + SHOWN[32:] + "\r")
    pty.close()
    assert isinstance(got, str) and hostsec.recovery_key(got) == KEY
    got, pty = read_in_a_pty(SHOWN[:40] + "\r\r")  # short: a blank line ends it, and the normalizer says why
    pty.close()
    assert isinstance(got, str) and got.strip() == SHOWN[:40]


def test_a_short_line_then_enter_says_what_is_missing_instead_of_waiting_silently() -> None:
    """signal-key-short-enter-silent (round 1): in plain lines (--plain, VoiceOver, no gum) a key one character short,
    then Enter, waited for more with nothing on screen, and the next try was added to the first. Each Enter below
    the count says how many came and what ends the read; a line that counts for nothing names the keyboard."""
    pty = Pty()
    got: list[str | BaseException] = []

    def read() -> None:
        try:
            got.append(hostsec.read_hidden_lines("Recovery key: ", 64, hostsec.key_character, pty.path))
        except (EOFError, OSError) as exc:
            got.append(exc)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        pty.wait_for(b"Recovery key: ")
        pty.type("αβγ\r")  # the Greek layout: none of it counts
        pty.wait_for(b"0 of 64 so far")
        assert b"the keyboard may be on Greek: switch it to ABC" in pty.seen
        pty.type(SHOWN[:40] + "\r")
        pty.wait_for(b"40 of 64 so far: type the next line, or Enter on an empty line to end here.")
        pty.type(SHOWN[40:] + "\r")
        reader.join(10)
    finally:
        pty.close()
    assert got and isinstance(got[0], str)
    assert got[0].splitlines() == ["αβγ", SHOWN[:40], SHOWN[40:]]  # as typed: the normalizer refuses the Greek line
    assert hostsec.recovery_key("\n".join(got[0].splitlines()[1:])) == KEY
    assert b"ABCD" not in pty.seen and b"#" not in pty.seen  # the hint never shows the key


def test_q_stops_the_hidden_read_and_ctrl_d_ends_it() -> None:
    got, pty = read_in_a_pty("q\r")
    pty.close()
    assert got == "q"
    got, pty = read_in_a_pty("\x04")
    pty.close()
    assert isinstance(got, EOFError)


def test_an_account_id_on_two_lines_is_read_until_its_32_digits() -> None:
    got, pty = read_in_a_pty(f"{ACCOUNT[:18]}\r{ACCOUNT[18:]}\r\r", prompt="Account ID: ", needed=32)
    pty.close()
    assert isinstance(got, str) and hostsec.account_id(got) == ACCOUNT


def test_an_enter_typed_before_the_question_shows_is_no_answer_to_it() -> None:
    """Review, 23 Oct: the key's read ends at its 64th character's line end, and an Enter pressed after it (a blank
    line, say, after the paste's own Enter) waited in the terminal for the next question and answered it with nothing.
    What was typed before a visible question shows is dropped; the answer typed after it comes back."""
    pty = Pty()
    try:
        pty.type("\r")  # the Enter after the key, typed while signal-cli works
        pty.wait_for(b"\n")  # the terminal took it in (its echo)
        got: list[str | BaseException] = []

        def ask() -> None:
            try:
                got.append(hostsec.ask_tty(hostsec.USERNAME_ASK, pty.path))
            except (EOFError, OSError) as exc:
                got.append(exc)

        reader = threading.Thread(target=ask, daemon=True)
        reader.start()
        pty.wait_for(hostsec.USERNAME_ASK.encode())
        pty.type("estia.42\r")
        reader.join(10)
        assert got == ["estia.42"]
    finally:
        pty.close()


def test_setup_drops_what_was_typed_ahead_on_the_terminal_itself(tmp_path: Path) -> None:
    """Setup's questions after the key read stdin (`input`), so `flush_typed` drops what waits on the terminal first:
    an Enter and a stray line typed while signal-cli worked are gone, and the line typed after it is the answer.
    Not a terminal, or none: nothing to drop, and no error."""
    pty = Pty()
    try:
        pty.type("\r")  # the Enter after the key
        pty.type("stray\r")
        assert select.select([pty.slave], [], [], 2)[0]  # waiting to be read, as the next question would
        hostsec.flush_typed(pty.path)
        assert not select.select([pty.slave], [], [], 0.3)[0]
        pty.type("estia.42\r")
        assert select.select([pty.slave], [], [], 2)[0]
        assert os.read(pty.slave, 1024) == b"estia.42\n"
    finally:
        pty.close()
    plain = tmp_path / "plain"
    plain.write_text("")
    hostsec.flush_typed(str(plain))
    hostsec.flush_typed(str(tmp_path / "none"))


def test_the_visible_question_tells_the_end_of_input_from_an_empty_line() -> None:
    """Ctrl-D (the end of input) stops; an Enter alone is an empty answer, which the caller asks again."""
    pty = Pty()
    try:
        got: list[str | BaseException] = []

        def ask() -> None:
            for _ in range(2):
                try:
                    got.append(hostsec.ask_tty("Username:", pty.path))
                except (EOFError, OSError) as exc:
                    got.append(exc)

        reader = threading.Thread(target=ask, daemon=True)
        reader.start()
        pty.wait_for(b"Username:")
        pty.type("\r")
        pty.wait_for(b"Username:", occurrence=2)
        pty.type("\x04")
        reader.join(10)
        assert got[0] == "" and isinstance(got[1], EOFError)
    finally:
        pty.close()


def test_the_enter_after_the_key_never_answers_the_username_question() -> None:
    """`estia signal-login`'s reading on a real terminal, as the review typed it: the Account ID, the key and its
    Enter, then one more Enter (as a blank line) once the username question shows. That Enter is no answer and never
    stops it: the question comes again, and the username typed then is taken."""
    from local_assistant import ownuser

    pty = Pty()
    try:
        said: list[str] = []
        got: list[dict[str, str] | BaseException | None] = []

        def hidden(prompt: str, needed: int, counted: Callable[[str], bool]) -> str:
            return hostsec.read_hidden_lines(prompt, needed, counted, pty.path)

        def login() -> None:
            try:
                got.append(
                    ownuser.signal_login_ask(
                        say=said.append, hidden=hidden, ask=lambda question: hostsec.ask_tty(question, pty.path)
                    )
                )
            except (EOFError, OSError) as exc:
                got.append(exc)

        reader = threading.Thread(target=login, daemon=True)
        reader.start()
        pty.wait_for(hostsec.ID_ASK.encode())
        pty.type(ACCOUNT + "\r")
        pty.wait_for(hostsec.KEY_ASK.replace("\n", "\r\n").encode())  # as the terminal shows its line break
        pty.type(SHOWN + "\r")
        pty.wait_for(hostsec.USERNAME_ASK.encode())
        pty.type("\r")
        pty.wait_for(hostsec.USERNAME_ASK.encode(), occurrence=2)
        pty.type("estia.42\r")
        pty.wait_for(hostsec.TOTP_QUESTION.replace("\n", "\r\n").encode())  # the six-digit code: Enter is no
        pty.type("\r")
        reader.join(10)
        assert got == [{"account": ACCOUNT, "recovery_key": KEY, "username": "estia.42"}]
        assert hostsec.USERNAME_BAD not in "\n".join(said)
    finally:
        pty.close()


def test_signal_login_refuses_a_whole_note_at_the_key_question_and_takes_the_key_alone() -> None:
    """Review of P1, R2-S1, `estia signal-login` on a real terminal: the password manager's whole note pasted at the
    key question is refused with the count, never handed over as a key; the key pasted alone then is taken."""
    from local_assistant import ownuser

    pty = Pty()
    try:
        said: list[str] = []
        got: list[dict[str, str] | BaseException | None] = []

        def hidden(prompt: str, needed: int, counted: Callable[[str], bool]) -> str:
            return hostsec.read_hidden_lines(prompt, needed, counted, pty.path)

        def login() -> None:
            try:
                got.append(
                    ownuser.signal_login_ask(
                        say=said.append, hidden=hidden, ask=lambda question: hostsec.ask_tty(question, pty.path)
                    )
                )
            except (EOFError, OSError) as exc:
                got.append(exc)

        reader = threading.Thread(target=login, daemon=True)
        reader.start()
        key_ask = hostsec.KEY_ASK.replace("\n", "\r\n").encode()  # as the terminal shows its line break
        pty.wait_for(hostsec.ID_ASK.encode())
        pty.type(ACCOUNT + "\r")
        pty.wait_for(key_ask)
        pty.type(NOTE)
        pty.wait_for(key_ask, occurrence=2)
        pty.type("\r".join(" ".join(groups(line)) for line in groups(SHOWN, 16)) + "\r")
        pty.wait_for(hostsec.USERNAME_ASK.encode())
        pty.type("estia.42\r")
        pty.wait_for(hostsec.TOTP_QUESTION.replace("\n", "\r\n").encode())
        pty.type("\r")
        reader.join(10)
        assert got == [{"account": ACCOUNT, "recovery_key": KEY, "username": "estia.42"}]
        refusals = [line for line in said if "this key has" in line]
        assert len(refusals) == 1 and "this key has 96" in refusals[0]
        assert SHOWN[:8] not in "\n".join(said) and ACCOUNT[:8] not in "\n".join(said)
    finally:
        pty.close()


# ------------------------------------------------------------------ signal-cli's list and the Registration Lock line


def listed(signal: Path, *entries: dict[str, object]) -> None:
    data = signal / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "accounts.json").write_text(json.dumps({"accounts": list(entries), "version": 2}))
    for entry in entries:
        account = {"version": 9, "registered": True, "registrationLockPin": None, "number": entry.get("number")}
        (data / str(entry["path"])).write_text(json.dumps(account))


def test_an_account_without_a_number_is_found_by_its_account_id(tmp_path: Path) -> None:
    """signal-cli lists it with no number and its ACI as uuid; a number's entry is found by its number, as before."""
    listed(
        tmp_path,
        {"path": "111111", "number": "+306900000000", "uuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"},
        {"path": "222222", "number": None, "uuid": ACCOUNT},
    )
    assert [entry["path"] for entry in hostsec.signal_listed(tmp_path, ACCOUNT)] == ["222222"]
    assert [entry["path"] for entry in hostsec.signal_listed(tmp_path, ACCOUNT.upper())] == ["222222"]
    assert [entry["path"] for entry in hostsec.signal_listed(tmp_path, "+306900000000")] == ["111111"]
    assert hostsec.signal_account(tmp_path, ACCOUNT) is not None
    from local_assistant import ownuser

    assert ownuser.has_account(tmp_path, ACCOUNT)  # the move's preflight: registered, by its Account ID
    assert not ownuser.has_account(tmp_path, "99999999-8888-4777-8666-555555555555")


def test_an_account_without_a_number_needs_no_registration_lock(tmp_path: Path) -> None:
    """No number: no SMS code can register it elsewhere, so the line is ✓ (the hardened level holds), and it names
    what does: her Recovery key, kept in a password manager."""
    home = tmp_path / "home"
    listed(home / ".local/share/signal-cli", {"path": "222222", "number": None, "uuid": ACCOUNT})
    assert hostsec.registration_lock_state(home / ".local/share/signal-cli", ACCOUNT) == "numberless"
    check = hostsec.registration_lock_check(home, ACCOUNT)
    assert check.status == "ok" and check.text == hostsec.NUMBERLESS_LOCK
    assert "no number" in check.text and "Recovery key" in check.text and "password manager" in check.text
    assert ACCOUNT not in check.text + check.fix
    assert hostsec.registration_lock_check(home).status == "ok"  # her one account: the ID may be left out

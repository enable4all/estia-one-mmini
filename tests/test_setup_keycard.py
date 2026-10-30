"""The key card (setup_keycard): a long key typed group by group, laid out as Signal's card shows it, each group
checked as it is typed, a paste split into groups, the whole read back as Signal stores it."""

from pathlib import Path

import pytest

from local_assistant import hostsec, prompts, setup_keycard
from local_assistant.setup_keycard import ACCOUNT_CARD, ACCOUNT_ID, RECOVERY_CARD, RECOVERY_KEY, KeyCard

KEY = "abcdefghijklmnopqrstuvwxyz012345" * 2  # her Recovery key as Signal stores it (made up)
SHOWN_KEY = "ABCDEFGHIJKLMN#PQRSTUVWXYZ=12345" * 2  # as Signal shows it: O as #, 0 as =
NUMBERLESS = "99999999-8888-4777-8666-555555555555"  # her Account ID (made up)


def shown_lines(key: str) -> str:
    """The key as Signal's screen shows it: four lines of four groups."""
    rows = [key[at : at + 16] for at in range(0, 64, 16)]
    return "\n".join(" ".join(row[i : i + 4] for i in range(0, 16, 4)) for row in rows)


def typed(card: KeyCard, text: str) -> KeyCard:
    for char in text:
        card.type(char)
    return card


def test_the_cards_are_laid_out_as_signals_screen_shows_them() -> None:
    """Signal-Android main 6e9dddc0 (26 Oct 2026): the Recovery key in groups of 4, four groups a row (four rows);
    the Account ID as its UUID, 8-4-4-4-12, on one line. The source link stands next to the layout."""
    assert (RECOVERY_KEY.rows, RECOVERY_KEY.columns, RECOVERY_KEY.group) == (4, 4, 4)
    assert RECOVERY_KEY.group_sizes() == (4,) * 16 and RECOVERY_KEY.visible and RECOVERY_KEY.label == "Recovery key"
    assert ACCOUNT_ID.group_sizes() == (8, 4, 4, 4, 12) and ACCOUNT_ID.visible and ACCOUNT_ID.label == "Account ID"
    assert sum(ACCOUNT_ID.group_sizes()) == hostsec.ID_DIGITS and sum(RECOVERY_KEY.group_sizes()) == hostsec.KEY_LENGTH
    source = Path(setup_keycard.__file__).read_text(encoding="utf-8")
    for name in ("RecoveryKeyGroups.kt", "SignalLoginKeyDetails.kt", "AccountIdFormat.kt"):
        assert "github.com/signalapp/Signal-Android/blob/6e9dddc01f1b26e608ac683807c51e139c141ed2/" in source
        assert name in source, name


def test_typing_goes_group_by_group_and_ends_with_compare_then_enter() -> None:
    card = KeyCard(RECOVERY_CARD)
    assert card.at == 0 and card.status() == "Group 1 of 16."
    typed(card, SHOWN_KEY[:4])
    assert card.at == 1 and card.status() == "Group 2 of 16."  # checked, fixed, the next one marked
    typed(card, SHOWN_KEY[4:])
    assert card.done and card.status() == "All 16 groups. Compare with your phone, then Enter."
    assert card.enter() and card.value() == KEY  # # and = read back as o and 0


def test_the_card_shows_what_was_typed_as_the_phone_shows_it() -> None:
    card = typed(KeyCard(RECOVERY_CARD), "abcdefghijklmn#p" + "qrst")
    rows = card.rows()
    assert [shown for shown, _marked in rows[0]] == ["ABCD", "EFGH", "IJKL", "MN#P"]
    assert [shown for shown, _marked in rows[1]] == ["QRST", "····", "····", "····"]
    assert [marked for _shown, marked in rows[1]] == [False, True, False, False]
    assert len(rows) == 4 and all(len(row) == 4 for row in rows)


@pytest.mark.parametrize(
    ("char", "symbol"),
    [
        ("ø", "'ø'"),
        ("%", "'%'"),
        ("а", hostsec.KEY_LOOKALIKE),  # a Cyrillic a: refused by its kind only
        ("Ａ", hostsec.KEY_LOOKALIKE),  # a fullwidth A
        ("́", "U+0301"),  # a combining accent
    ],
)
def test_a_symbol_not_on_the_card_is_named_with_its_place_and_never_entered(char: str, symbol: str) -> None:
    card = typed(KeyCard(RECOVERY_CARD), "ABCDEFGH" + "I")
    card.type(char)
    assert card.message.startswith(f"Group 3, character 2: {symbol}")
    assert card.message.endswith("is not on Signal's card. Signal shows # for o and = for 0.")
    assert card.groups[2] == "I"  # never dropped silently, never entered


def test_the_specs_message_for_a_symbol_not_on_the_card() -> None:
    card = typed(KeyCard(RECOVERY_CARD), "ABCDEFGHI")
    card.type("ø")
    assert card.message == "Group 3, character 2: 'ø' is not on Signal's card. Signal shows # for o and = for 0."


def test_enter_before_the_last_group_says_which_one_is_short() -> None:
    card = typed(KeyCard(RECOVERY_CARD), SHOWN_KEY[:8] + "IJK")
    card.goto(0)
    assert not card.enter() and card.message == "Group 3 has 3 of 4 characters." and card.at == 2


def test_backspace_on_an_empty_group_frees_the_one_before() -> None:
    card = typed(KeyCard(RECOVERY_CARD), "ABCDEFGH")
    assert card.at == 2
    card.backspace()
    assert card.at == 1 and card.groups[1] == "EFG"
    card.type("X")
    assert card.groups[1] == "EFGX" and card.at == 2


def test_a_group_is_changed_by_going_back_to_it() -> None:
    card = typed(KeyCard(RECOVERY_CARD), SHOWN_KEY)
    card.goto(5)
    assert card.at == 5
    typed(card, "WXYZ")
    assert card.groups[5] == "WXYZ" and card.groups[4] == SHOWN_KEY[16:20] and card.done


def test_a_paste_of_signals_four_lines_fills_every_group() -> None:
    card = KeyCard(RECOVERY_CARD)
    card.paste(shown_lines(SHOWN_KEY))
    assert card.done and card.value() == KEY and card.message == ""


def test_a_paste_with_dashes_and_hidden_characters_fills_the_groups() -> None:
    card = KeyCard(RECOVERY_CARD)
    card.paste("﻿" + "-​".join(SHOWN_KEY[at : at + 4] for at in range(0, 64, 4)) + "\n")
    assert card.done and card.value() == KEY


def test_a_paste_longer_than_the_key_is_refused_with_the_count() -> None:
    """P1's R2-S1: a whole note (the Account ID, then the key) is never cut to fit: refused, nothing entered."""
    card = KeyCard(RECOVERY_CARD)
    card.paste(f"{NUMBERLESS}\n{shown_lines(SHOWN_KEY)}")
    assert card.message == prompts.SETUP_CARD_PASTE_LONG.format(count=96, room=64)
    assert card.groups == [""] * 16


def test_a_paste_with_a_symbol_not_on_the_card_names_its_place_and_enters_nothing() -> None:
    card = KeyCard(RECOVERY_CARD)
    card.paste(SHOWN_KEY[:9] + "%" + SHOWN_KEY[10:])
    assert card.message.startswith("Group 3, character 2: '%' is not on Signal's card.")
    assert card.groups == [""] * 16


def test_a_paste_of_one_row_fills_four_groups_from_the_marked_one() -> None:
    card = typed(KeyCard(RECOVERY_CARD), SHOWN_KEY[:16])
    card.paste(SHOWN_KEY[16:32])
    assert card.groups[4:8] == [SHOWN_KEY[16:20], SHOWN_KEY[20:24], SHOWN_KEY[24:28], SHOWN_KEY[28:32]]
    assert card.at == 8


def test_the_account_id_card_takes_its_uuid_as_signal_shows_it() -> None:
    card = KeyCard(ACCOUNT_CARD)
    card.paste(NUMBERLESS.upper())
    assert card.groups == ["99999999", "8888", "4777", "8666", "555555555555"]
    assert card.done and card.value() == NUMBERLESS
    assert [shown for shown, _marked in card.rows()[0]] == ["99999999", "8888", "4777", "8666", "555555555555"]
    card = typed(KeyCard(ACCOUNT_CARD), "9999")
    card.type("g")
    assert card.message == "Group 1, character 5: 'g' is not in an Account ID. It has 0 to 9 and A to F."


def test_a_card_reopened_after_signal_refused_it_keeps_every_group() -> None:
    groups = [SHOWN_KEY[at : at + 4] for at in range(0, 64, 4)]
    card = KeyCard(RECOVERY_CARD, groups, message=prompts.SETUP_CARD_REFUSED)
    assert card.groups == groups and card.done
    assert card.status() == "Signal refused it. Compare each group with your phone."
    card.goto(3)
    assert card.status() == "Group 4 of 16."  # the message goes once a group is touched


def test_split_gives_a_typed_entry_back_its_groups() -> None:
    assert setup_keycard.split(RECOVERY_CARD, shown_lines(SHOWN_KEY)) == [
        SHOWN_KEY[at : at + 4] for at in range(0, 64, 4)
    ]
    assert setup_keycard.split(ACCOUNT_CARD, NUMBERLESS) == ["99999999", "8888", "4777", "8666", "555555555555"]
    assert setup_keycard.split(RECOVERY_CARD, "ABCD%") == []  # not one the card takes: typed again


# ------------------------------------------------------------------ the card in Terminal, on a screen of its own


def on_a_terminal(
    keys: list[str], **kwargs: object
) -> tuple[list[str] | BaseException | None, bytes, list[object], list[object]]:
    """run_card on a fake terminal: each of `keys` typed once the card has drawn (a chunk each). What it gave back
    (or raised), every byte it wrote, and the terminal's mode before and after."""
    import os
    import select
    import termios
    import threading
    import time

    from tests.setup_helpers import Pty

    pty = Pty()
    got: list[list[str] | BaseException | None] = []
    before = termios.tcgetattr(pty.slave)

    def run() -> None:
        try:
            got.append(setup_keycard.run_card(RECOVERY_CARD, tty=pty.path, **kwargs))  # type: ignore[arg-type]
        except BaseException as exc:
            got.append(exc)

    reader = threading.Thread(target=run, daemon=True)
    try:
        reader.start()
        for at, chunk in enumerate(keys):
            pty.wait_for(setup_keycard.ALT_ON.encode())
            pty.wait_for(b"\x1b[J", occurrence=at + 1)  # the card drawn again after each chunk
            pty.type(chunk)
        deadline = time.monotonic() + 10
        written = pty.seen
        while time.monotonic() < deadline and (reader.is_alive() or select.select([pty.master], [], [], 0.05)[0]):
            if select.select([pty.master], [], [], 0.05)[0]:  # read as it writes: a full terminal would hold it up
                written += os.read(pty.master, 4096)
        reader.join(1)
        after = termios.tcgetattr(pty.slave)
    finally:
        pty.close()
    assert got, "run_card did not end"
    return got[0], written, before, after


def key_text_in(data: bytes) -> list[str]:
    """Any of the key's groups, as the card shows them, in what was written."""
    groups = {SHOWN_KEY[at : at + 4] for at in range(0, 64, 4)}
    return [group for group in groups if group.encode() in data]


def test_the_card_is_typed_on_its_own_screen_and_cleared_before_it_is_left() -> None:
    result, written, before, after = on_a_terminal([SHOWN_KEY[:32], SHOWN_KEY[32:], "\r"])
    assert result == [SHOWN_KEY[at : at + 4] for at in range(0, 64, 4)]
    first_key = min(written.find(group.encode()) for group in key_text_in(written))
    assert -1 < written.find(setup_keycard.ALT_ON.encode()) < first_key  # the key only on the alternate screen
    leave = written.rfind(setup_keycard.ALT_OFF.encode())
    assert leave > first_key and key_text_in(written[leave:]) == []  # nothing of the key after it is left
    last_clear = written.rfind(b"\x1b[2J", 0, leave)
    blank = written.rfind(b"\x1b[H", 0, last_clear)
    assert key_text_in(written[blank:leave]) == []  # its last screen holds no key, in case one keeps it
    assert before == after  # the terminal's mode put back


def test_backspace_and_a_paste_work_on_the_card() -> None:
    lines = "\n".join(" ".join(SHOWN_KEY[r * 16 + i : r * 16 + i + 4] for i in range(0, 16, 4)) for r in range(4))
    result, _written, _before, _after = on_a_terminal(["ZZ\x7f\x7f", f"\x1b[200~{lines}\x1b[201~", "\r"])
    assert result == [SHOWN_KEY[at : at + 4] for at in range(0, 64, 4)]


def test_enter_on_a_short_card_names_the_group_and_waits() -> None:
    result, written, _before, _after = on_a_terminal([SHOWN_KEY[:10], "\r", SHOWN_KEY[10:], "\r"])
    assert "Group 3 has 2 of 4 characters." in written.decode()
    assert result == [SHOWN_KEY[at : at + 4] for at in range(0, 64, 4)]


def test_esc_or_ctrl_d_stops_the_card_and_ctrl_c_stops_setup_with_the_terminal_put_back() -> None:
    for stop in ("\x1b", "\x04"):
        result, written, before, after = on_a_terminal([SHOWN_KEY[:8], stop])
        assert result is None and before == after
        assert key_text_in(written[written.rfind(setup_keycard.ALT_OFF.encode()) :]) == []
    result, written, before, after = on_a_terminal([SHOWN_KEY[:8], "\x03"])
    assert isinstance(result, KeyboardInterrupt) and before == after
    assert written.rstrip().endswith(setup_keycard.ALT_OFF.encode())


def test_a_card_reopened_shows_its_groups_and_the_refusal() -> None:
    groups = [SHOWN_KEY[at : at + 4] for at in range(0, 64, 4)]
    result, written, _before, _after = on_a_terminal(
        ["\r"], groups=groups, message=prompts.SETUP_CARD_REFUSED, title="Her Recovery key"
    )
    assert result == groups and prompts.SETUP_CARD_REFUSED in written.decode()


def test_the_card_needs_a_terminal_wide_enough(tmp_path: Path) -> None:
    import fcntl
    import struct
    import termios

    from tests.setup_helpers import Pty

    pty = Pty()
    try:
        fcntl.ioctl(pty.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        assert setup_keycard.fits(pty.path)
        fcntl.ioctl(pty.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 59, 0, 0))
        assert not setup_keycard.fits(pty.path)
        fcntl.ioctl(pty.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 13, 80, 0, 0))
        assert not setup_keycard.fits(pty.path)
    finally:
        pty.close()
    assert not setup_keycard.fits(str(tmp_path / "no-terminal"))


def test_the_card_fits_a_terminal_of_60_columns_and_18_rows() -> None:
    """Every line wrapped to the width, the whole within MIN_ROWS, even with the longest refusal under it."""
    card = typed(KeyCard(RECOVERY_CARD), SHOWN_KEY[:40])
    card.type("\uff21")  # a fullwidth A: the longest refusal
    for keys in (card, KeyCard(ACCOUNT_CARD)):
        drawn = setup_keycard.lines(keys, prompts.SETUP_CARD_TITLE.format(label=keys.card.layout.label), 60)
        visible = [line.replace("\x1b[7m", "").replace("\x1b[27m", "") for line in drawn]
        assert all(len(line) < 60 for line in visible), visible
        assert len(drawn) <= setup_keycard.MIN_ROWS - 1

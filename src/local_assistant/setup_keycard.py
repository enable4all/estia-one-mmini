"""The key card: a long key typed group by group, laid out as the service's own screen shows it (owner, 25 Oct:
"guided typing", the Recovery key and the Account ID visible while typed). Each group is checked as it is typed and
fixed in its place, the next empty one marked; Backspace on an empty group frees the one before; any group can be
gone back to; a paste is split into groups. The whole is read back by hostsec's readers (`hostsec.recovery_key`,
`hostsec.account_id`), as setup and `estia signal-login` read a key typed hidden.

The card is drawn in Terminal on a screen of its own (`run_card`, the alternate screen), cleared when done, so no key
stays in the scrollback; the setup window draws the same groups. Nothing here keeps or logs a key."""

from __future__ import annotations

import os
import select
import termios
import textwrap
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from local_assistant import hostsec, prompts
from local_assistant.setup_questions import KeyLayout, clean_paste

EMPTY = "·"  # a place of a group not typed yet
# the terminal's own screen for the card (as less and vim use it), its pastes marked, and the screen cleared after it
ALT_ON, ALT_OFF = "\x1b[?1049h", "\x1b[?1049l"
PASTE_ON, PASTE_OFF = "\x1b[?2004h", "\x1b[?2004l"
PASTE_START, PASTE_END = b"\x1b[200~", b"\x1b[201~"
CLEAR = "\x1b[H\x1b[2J"
MIN_COLUMNS, MIN_ROWS = 60, 18  # a terminal smaller than this gets the hidden read instead: the card never scrolls
ESC_SECONDS = 0.05  # an Esc alone, not the start of a key's sequence (an arrow) or a paste


@dataclass(frozen=True)
class CardLayout(KeyLayout):
    """A key card's grid as the service's own screen shows it: `KeyLayout`'s rows and columns of groups, each group's
    size (`sizes`) and what joins them (`joiner`), which every face draws (gum's card here, the window's own)."""


# As Signal shows them, read from Signal-Android's source at main 6e9dddc0 (26 Oct 2026): the Recovery key in groups
# of 4 (RecoveryKeyGroups.GROUP_SIZE), four groups a row (SignalLoginKeyDetails.GROUPS_PER_ROW; a phone too narrow
# for four wraps them as one spaced line), as AccountEntropyPool.displayValue (capitals, O as #, 0 as =); the Account
# ID as its ACI in capitals with the UUID's dashes, 8-4-4-4-12 (AccountIdFormat.dashed), on one line. Not yet
# compared with the owner's phone (open question 4).
# https://github.com/signalapp/Signal-Android/blob/6e9dddc01f1b26e608ac683807c51e139c141ed2/lib/signal-login/src/main/java/org/signal/signallogin/RecoveryKeyGroups.kt
# https://github.com/signalapp/Signal-Android/blob/6e9dddc01f1b26e608ac683807c51e139c141ed2/lib/signal-login/src/main/java/org/signal/signallogin/details/SignalLoginKeyDetails.kt
# https://github.com/signalapp/Signal-Android/blob/6e9dddc01f1b26e608ac683807c51e139c141ed2/feature/registration/src/main/java/org/signal/registration/screens/shared/AccountIdFormat.kt
RECOVERY_KEY = CardLayout(4, 4, 4, "Recovery key", visible=True)
# rows x columns x group is the whole length for a face that knows no `sizes` (32: eight of four, on one row)
ACCOUNT_ID = CardLayout(1, 8, 4, "Account ID", visible=True, sizes=(8, 4, 4, 4, 12), joiner="-")
_DISPLAY = {stored: shown for shown, stored in hostsec.KEY_SYMBOLS.items()}  # o as #, 0 as =


@dataclass(frozen=True)
class Card:
    """What a card takes and how it shows it: its layout, the characters typed on it, each as the phone shows it,
    the reader of the whole (ValueError with why), and the end of the line for a symbol it does not take."""

    layout: CardLayout
    takes: Callable[[str], bool]
    shown: Callable[[str], str]
    read: Callable[[str], str]
    refusal: str


def _key_shown(char: str) -> str:
    return char if char in hostsec.KEY_SYMBOLS else _DISPLAY.get(char.lower(), char.upper())


RECOVERY_CARD = Card(RECOVERY_KEY, hostsec.key_character, _key_shown, hostsec.recovery_key, prompts.SETUP_CARD_NOT_KEY)
ACCOUNT_CARD = Card(ACCOUNT_ID, hostsec.id_character, str.upper, hostsec.account_id, prompts.SETUP_CARD_NOT_ID)
CARDS = (RECOVERY_CARD, ACCOUNT_CARD)


def card_for(layout: KeyLayout | None) -> Card | None:
    """The card a question's layout is drawn on (gum's face draws it in Terminal); None for any other layout."""
    return next((card for card in CARDS if card.layout == layout), None)


_SCRIPTS = ("CYRILLIC", "GREEK", "ARMENIAN", "CHEROKEE", "COPTIC", "GEORGIAN")  # letters that pass for Latin ones


def _symbol(char: str) -> str:
    """A refused character as the card's line names it: a look-alike of one of the key's own letters or digits (a
    fullwidth form, a Cyrillic or Greek letter) by its kind only, as hostsec's readers name it; any other in quotes,
    as the spec writes 'ø'; one that does not show by its code."""
    category = unicodedata.category(char)
    if category[0] in "LN" and not char.isascii():
        folded = unicodedata.normalize("NFKC", char)
        if (folded.isascii() and folded.isalnum()) or unicodedata.name(char, "").startswith(_SCRIPTS):
            return hostsec.KEY_LOOKALIKE
    if char.isprintable() and not char.isspace() and not category.startswith("M"):
        return f"'{char}'"
    return f"U+{ord(char):04X}"


def _taken(card: Card, text: str) -> list[str]:
    """The characters of a paste the card would enter: a paste's spaces, breaks and hidden characters left out, and
    the dashes, dots and underscores between groups (the card shows where each group ends)."""
    return [char for char in clean_paste(text, spaces=True)[0] if char not in hostsec.KEY_SEPARATORS]


def split(card: Card, text: str) -> list[str]:
    """A whole entry typed or pasted elsewhere (hidden, in the window) back in the card's groups; [] when it holds a
    character the card does not take, or more than the card holds."""
    chars = _taken(card, text)
    if not all(map(card.takes, chars)) or len(chars) > sum(card.layout.group_sizes()):
        return []
    groups, at = [], 0
    for size in card.layout.group_sizes():
        groups.append("".join(chars[at : at + size]))
        at += size
    return groups


class KeyCard:
    """One key card being typed: the groups so far (as typed), the marked one, and the line under the card."""

    def __init__(self, card: Card, groups: Sequence[str] = (), *, message: str = "") -> None:
        self.card = card
        self.sizes = card.layout.group_sizes()
        given = list(groups)[: len(self.sizes)]
        self.groups = [
            "".join(char for char in (given[i] if i < len(given) else "") if card.takes(char))[:size]
            for i, size in enumerate(self.sizes)
        ]
        self.at = self._next_open(0) if not self.done else len(self.sizes) - 1
        self.message = message
        self.back = False  # a group of a full card gone back to: the line says which

    @property
    def done(self) -> bool:
        return all(len(group) == size for group, size in zip(self.groups, self.sizes, strict=True))

    def _full(self, index: int) -> bool:
        return len(self.groups[index]) == self.sizes[index]

    def _next_open(self, start: int) -> int:
        """The first group not full from `start` on, else from the first; `start` (kept in range) when all are."""
        order = [*range(start, len(self.sizes)), *range(start)]
        return next((index for index in order if not self._full(index)), min(start, len(self.sizes) - 1))

    def _place(self, index: int, offset: int) -> tuple[int, int]:
        """Group and character (from 1) of the offset-th character entered from group `index`'s first free place."""
        group, char = index, len(self.groups[index]) if not self._full(index) else 0
        for _ in range(offset):
            char += 1
            if char == self.sizes[group] and group + 1 < len(self.sizes):
                group, char = group + 1, 0
        return group + 1, char + 1

    def _refused(self, char: str, group: int, place: int) -> None:
        self.message = prompts.SETUP_CARD_SYMBOL.format(
            group=group, place=place, symbol=_symbol(char), refusal=self.card.refusal
        )

    def type(self, char: str) -> None:
        """One key: entered in the marked group when the card takes it (a full group gone back to starts again);
        spaces, separators and hidden characters are passed over; any other is named with its place."""
        if char.isspace() or char in hostsec.KEY_SEPARATORS or hostsec.invisible(char):
            return
        if not self.card.takes(char):
            group, place = self._place(self.at, 0)
            self._refused(char, group, place)
            return
        if self._full(self.at):
            self.groups[self.at] = ""
        self.groups[self.at] += char
        self.message, self.back = "", False
        if self._full(self.at):
            self.at = self._next_open(self.at + 1 if self.at + 1 < len(self.sizes) else self.at)

    def backspace(self) -> None:
        """The last character of the marked group; on an empty group, the last of the one before, which is marked."""
        self.message, self.back = "", False
        if not self.groups[self.at] and self.at > 0:
            self.at -= 1
        self.groups[self.at] = self.groups[self.at][:-1]

    def goto(self, index: int) -> None:
        """Mark a group (the window's click, an arrow key): typing there starts it again."""
        if 0 <= index < len(self.sizes):
            self.at = index
            self.message, self.back = "", True

    def paste(self, text: str) -> None:
        """A paste, split into groups from the marked one's first free place on, the groups after it written over.
        One that holds a character the card does not take, or more than fits, is refused whole, never cut to fit."""
        chars = _taken(self.card, text)
        start = 0 if self._full(self.at) else len(self.groups[self.at])
        room = self.sizes[self.at] - start + sum(self.sizes[self.at + 1 :])
        for offset, char in enumerate(chars):
            if not self.card.takes(char):
                group, place = self._place(self.at, offset)
                self._refused(char, group, place)
                return
        if len(chars) > room:
            self.message = prompts.SETUP_CARD_PASTE_LONG.format(count=len(chars), room=room)
            return
        index, rest = self.at, "".join(chars)
        self.groups[index] = self.groups[index][:start]
        while rest:
            take = self.sizes[index] - len(self.groups[index])
            self.groups[index] += rest[:take]
            rest = rest[take:]
            if rest:
                index += 1
                self.groups[index] = ""
        self.message, self.back = "", False
        self.at = self._next_open(index) if not self.done else index

    def enter(self) -> bool:
        """True when every group is full; else the first short group is marked and named."""
        if self.done:
            return True
        self.at = self._next_open(0)
        self.message = prompts.SETUP_CARD_SHORT.format(
            group=self.at + 1, count=len(self.groups[self.at]), size=self.sizes[self.at]
        )
        return False

    def text(self) -> str:
        return "".join(self.groups)

    def value(self) -> str:
        """The whole as the service stores it (hostsec's reader); ValueError with why, never the key."""
        return self.card.read(self.text())

    def status(self) -> str:
        """The line under the card: the last refusal, else where typing goes, else the compare-then-Enter line."""
        if self.message:
            return self.message
        if self.done and not self.back:
            return prompts.SETUP_CARD_DONE.format(count=len(self.sizes))
        return prompts.SETUP_CARD_AT.format(group=self.at + 1, count=len(self.sizes))

    def rows(self) -> list[list[tuple[str, bool]]]:
        """The groups as the card shows them, row by row: each as the phone shows it (# and = as typed), its empty
        places as EMPTY, and whether it is the marked one."""
        shown = [
            "".join(map(self.card.shown, group)) + EMPTY * (size - len(group))
            for group, size in zip(self.groups, self.sizes, strict=True)
        ]
        marked = [index == self.at and (self.back or not self.done) for index in range(len(shown))]
        columns = self.card.layout.columns
        return [
            list(zip(shown[at : at + columns], marked[at : at + columns], strict=True))
            for at in range(0, len(shown), columns)
        ]


# ------------------------------------------------------------------ the card in Terminal, on a screen of its own


def fits(tty: str = hostsec.TTY) -> bool:
    """The terminal is wide and tall enough for the card, so it is redrawn in place and never scrolls."""
    try:
        fd = os.open(tty, os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return False
    try:
        size = os.get_terminal_size(fd)
    except OSError:
        return False
    finally:
        os.close(fd)
    return size.columns >= MIN_COLUMNS and size.lines >= MIN_ROWS


def _width(fd: int) -> int:
    """The terminal's width for the card's words (80 when it tells none, as a fake terminal)."""
    try:
        columns = os.get_terminal_size(fd).columns
    except OSError:
        columns = 0
    return columns if columns >= 20 else 80


def _wrapped(text: str, width: int) -> list[str]:
    return [line for part in text.splitlines() for line in (textwrap.wrap(part, width - 1) or [""])]


def lines(keys: KeyCard, title: str, width: int) -> list[str]:
    """The card as Terminal shows it: its title, its rows of groups (the marked one in reverse video), the line under
    it and the keys, each wrapped to the terminal's width, so the card is redrawn in place and never scrolls."""
    joiner = keys.card.layout.joiner
    grid = [
        "    " + joiner.join(f"\x1b[7m{shown}\x1b[27m" if marked else shown for shown, marked in row)
        for row in keys.rows()
    ]
    words = [_wrapped(text, width) for text in (keys.status(), prompts.SETUP_CARD_KEYS)]
    return [*_wrapped(title, width), "", *grid, "", *words[0], "", *words[1]]


def _draw(fd: int, keys: KeyCard, title: str) -> None:
    """The whole card again in place: from the top, each line cleared to its end, the rest of the screen cleared."""
    shown = lines(keys, title, _width(fd))
    _write(fd, "\x1b[H" + "".join(f"{line}\x1b[K\r\n" for line in shown) + "\x1b[J")


def _write(fd: int, text: str) -> None:
    data = text.encode()
    while data:
        data = data[os.write(fd, data) :]


def _waiting(fd: int, seconds: float) -> bool:
    return bool(select.select([fd], [], [], seconds)[0])


def _one(fd: int, data: bytes) -> tuple[str, str, bytes]:
    """The next key in `data` (more read when a sequence or a paste is cut): (what it is, its text, the rest)."""
    while True:
        if data.startswith(PASTE_START):
            end = data.find(PASTE_END)
            if end >= 0:
                return "paste", data[len(PASTE_START) : end].decode("utf-8", "replace"), data[end + len(PASTE_END) :]
        elif data[:1] == b"\x1b":
            if len(data) == 1:
                if not _waiting(fd, ESC_SECONDS):
                    return "stop", "", b""
            elif data[1:2] == b"[" and len(data) >= 3:
                end = next((at for at in range(2, len(data)) if 0x40 <= data[at] <= 0x7E), -1)
                if end >= 0 and not PASTE_START.startswith(data[: end + 1]):
                    return "csi", data[2 : end + 1].decode("ascii", "replace"), data[end + 1 :]
            elif data[1:2] != b"[":
                return "stop", "", data[1:]
        else:
            lead = data[0]
            size = 1 if lead < 0x80 else 2 if lead >> 5 == 0b110 else 3 if lead >> 4 == 0b1110 else 4
            if len(data) >= size or lead < 0xC0:
                return "char", data[:size].decode("utf-8", "replace"), data[size:]
        chunk = os.read(fd, 4096)
        if not chunk:
            raise EOFError
        data += chunk


def _typing(fd: int, keys: KeyCard, title: str) -> list[str] | None:
    """Keys until Enter on a full card (its groups) or Esc and Ctrl-D (None); Ctrl-C stops setup, as anywhere."""
    data = b""
    while True:
        if not data:
            data = os.read(fd, 4096)
            if not data:
                return None
        kind, text, data = _one(fd, data)
        if kind == "stop" or text == "\x04":
            return None
        if text == "\x03":
            raise KeyboardInterrupt
        if kind == "paste":
            keys.paste(text)
        elif kind == "csi":
            step = {"C": 1, "D": -1, "B": keys.card.layout.columns, "A": -keys.card.layout.columns}.get(text[-1:])
            if step is not None:
                keys.goto(keys.at + step)
        elif text in ("\r", "\n"):
            if keys.enter():
                return list(keys.groups)
        elif text in ("\x7f", "\x08"):
            keys.backspace()
        elif text == "\t":
            keys.goto(keys.at + 1)
        elif text.isprintable():
            keys.type(text)
        _draw(fd, keys, title)


def run_card(
    card: Card, *, title: str = "", groups: Sequence[str] = (), message: str = "", tty: str = hostsec.TTY
) -> list[str] | None:
    """The key card in Terminal: on the terminal's own screen (the alternate screen), read key by key from the
    terminal itself (never stdin), redrawn in place after each, pastes taken whole (bracketed paste). Its groups
    once Enter comes on a full card; None for Esc or Ctrl-D (or the end of input). However it ends (Ctrl-C too),
    the card is drawn once more with no key on it and the screen cleared before the alternate screen is left, so
    a terminal that keeps that screen in its scrollback keeps no key; and the terminal's mode is put back, what was
    typed after the end dropped. OSError without a terminal."""
    fd = os.open(tty, os.O_RDWR | os.O_NOCTTY)
    try:
        before = termios.tcgetattr(fd)
        raw = termios.tcgetattr(fd)
        raw[0] &= ~(termios.ICRNL | termios.IXON)
        raw[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN)
        raw[6][termios.VMIN] = 1
        raw[6][termios.VTIME] = 0
        keys = KeyCard(card, groups, message=message)
        shown = title or prompts.SETUP_CARD_TITLE.format(label=card.layout.label)
        termios.tcsetattr(fd, termios.TCSAFLUSH, raw)
        try:
            _write(fd, ALT_ON + PASTE_ON + CLEAR)
            _draw(fd, keys, shown)
            try:
                return _typing(fd, keys, shown)
            except EOFError:
                return None
        finally:
            _draw(fd, KeyCard(card), shown)
            _write(fd, CLEAR + PASTE_OFF + ALT_OFF)
            termios.tcsetattr(fd, termios.TCSAFLUSH, before)
    finally:
        os.close(fd)

"""Setup's questions as data: one list that every face draws (plain lines, gum's menus in Terminal, the setup window
at the desk), with the ids the answers file and `--redo` use. A question holds its words, its options and its check;
nothing here asks, prints or saves.

A secret (a captcha link, a code, a PIN, a key) is never kept in the answers file and never sent back to a face once
typed: `Question.is_secret`, `Question.screen`. The setup window gets each screen as one JSON object (`screen`)."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict

from local_assistant import hostsec, prompts

if TYPE_CHECKING:
    from local_assistant.setup_plan import Mac

Kind = Literal[
    "info",
    "choice",
    "cards",
    "yes_no",
    "text",
    "number",
    "hidden",
    "code",
    "pin",
    "key",
    "folder",
    "fields",
    "switches",
    "wait",
    "terminal",
    "progress",
]
# saved: in the answers file (a re-run shows "Kept"); never: asked at its step each time, a secret; keychain: a secret
# that goes only into your login keychain; step: asked at its step each time, not saved, not secret
Keep = Literal["saved", "never", "keychain", "step"]
Value = str | bool | tuple[str, ...] | Mapping[str, str]
SECRET_KINDS = frozenset({"hidden", "code", "pin", "key"})  # typed hidden, or on the key card
DECISIONS = frozenset({"choice", "cards", "yes_no", "switches", "folder"})
ID = re.compile(r"^[a-z_]{1,32}$")
# the pill on the option it recommends (owner, 29 Oct: "'recommended:' with below the YES looks cheap"): in the window
# a small "Recommended" on the option itself, its reason in the option's small line; in Terminal "(recommended)" after
# the option's label (gum's menus), or plain lines' line naming it so (`recommendation`)
RECOMMENDED = "Recommended"
OTHER = "other"  # an option that asks for a text of its own ("Her name"): its answer is what was typed
# a few key words in bold (owner, 28 Oct: "bolden a few words here and there"): a pair on one line, no * inside; the
# window draws them bold (page.js `rich`, the same rule), gum on a terminal too; plain lines, gum's menus and the
# window's log leave the marks out, and a command's line ("$ …") is shown as it runs
BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
# what a paste may bring that no answer holds: the line breaks and every other kind of line or paragraph end
_LINE_ENDS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85  ")


class Known(Protocol):
    """What a question may read of the answers so far (setup_answers.Answers)."""

    def get(self, qid: str) -> Value | None: ...

    def kept(self, qid: str) -> bool: ...


@dataclass(frozen=True)
class Guide:
    """An extra's guide, shown only when its switch is on: before you start, the steps, how you know it worked."""

    needs: str  # before you start: account, rights, cost, time
    steps: tuple[str, ...]
    check: str = ""  # how you know it worked (doctor's line, a question to her)
    errors: tuple[tuple[str, str], ...] = ()  # (what it says, the fix)
    links: tuple[tuple[str, str], ...] = ()  # (label, https URL): opened in the Mac's browser, never in the window


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    line: str = ""  # one line under it
    tag: str = ""  # RECOMMENDED on the one Enter takes: the window's pill on it, gum's "(recommended)" after it
    on: bool = False  # picked by default (switches)
    guide: Guide | None = None
    short: str = ""  # how plain lines' recommendation line names it, as typed there ("1"); "" for its label


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    placeholder: str = ""  # grey, never a real value
    max_chars: int = 300


@dataclass(frozen=True)
class Checked:
    """A check's answer: the cleaned value (None: refused), the one sentence that says why, or the line shown back."""

    value: Value | None
    message: str = ""  # one sentence: what is wrong, where, what is right
    shown: str = ""  # shown back once it passes ("She will answer to Maya.")
    confirm: str = ""  # what its question's `confirm` asks about ("Ermou, Athens Municipality, …"): "" asks nothing


@dataclass(frozen=True)
class KeyLayout:
    """The key card's grid, as the service's own screen shows its key (setup_keycard fills the real ones): rows and
    columns of groups, and each group's size when they differ (`sizes`: an Account ID's 8-4-4-4-12); `joiner` between
    groups on a row, as the service shows them."""

    rows: int
    columns: int
    group: int  # characters per group (rows x columns x group: the whole length for a face that knows no `sizes`)
    label: str  # "Recovery key", "Account ID", "Ollama key"
    visible: bool  # typed visibly on the alternate screen (owner: the Recovery key and the Account ID)
    sizes: tuple[int, ...] = ()
    joiner: str = "  "

    def group_sizes(self) -> tuple[int, ...]:
        return self.sizes or (self.group,) * (self.rows * self.columns)


class Variant(TypedDict, total=False):
    """What a question changes for this Mac (the move's lines over SSH, the region's language first, a list read
    when it is asked): each key given replaces the question's own."""

    lead: str
    window_lead: str
    ask: str
    recommended: str | None
    advice: str
    options: tuple[Option, ...]
    dry: str
    more: str  # the window's More fold (the move's, with the Ollama app chosen: review W06)


Check = Callable[[Value, Known, "Mac"], Checked]
Shows = Callable[[Known, "Mac"], bool]
Vary = Callable[[Known, "Mac"], Variant]


def _always(answers: Known, mac: Mac) -> bool:
    return True


class _Words(dict[str, str]):
    """A text's {fields}: the ones not given stay as they are."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill(text: str, words: Mapping[str, str]) -> str:
    """`text` with its {fields} from `words`; a field not given stays as written."""
    return text.format_map(_Words(words)) if "{" in text else text


@dataclass(frozen=True)
class Question:
    id: str  # ^[a-z_]{1,32}$, stable: the answers file's key and --redo's argument
    kind: Kind
    title: str  # the screen's heading
    # what it is for: short paragraphs, a blank line between them (owner, 27 Oct), a line "• " a bullet, **a word** bold
    lead: str = ""
    help: str = ""
    listing: str = ""  # the options as Terminal's plain lines show them (gum and the window draw `options`)
    ask: str = ""  # the question alone, then Terminal's Enter hint; never its recommendation (owner, 27 Oct)
    options: tuple[Option, ...] = ()
    fields: tuple[Field, ...] = ()
    recommended: str | None = None  # Enter takes it (spec rule 2); "yes" or "no" for a yes or no
    advice: str = ""  # the recommendation's reason in a few words ("when it is her number"): the small line too
    check: Check | None = None
    keep: Keep = "saved"
    plan: bool = True  # asked first (phase A); False: asked at its step (phase B)
    shows: Shows = field(default=_always)  # whether it is asked on this Mac, with these answers
    layout: KeyLayout | None = None
    placeholder: str = ""  # grey example text, never a real value
    secret: bool | None = None  # None: by its kind (SECRET_KINDS); False for an identifier typed on the card
    forget_no: bool = False  # a no means "not now": never kept, so the next run asks again
    live: bool = True  # its check may run while it is typed (the window); False for a check that reaches the network
    links: tuple[tuple[str, str], ...] = ()  # (label, https URL): the window links them, opened in the Mac's browser
    confirm: str = ""  # a yes or no asked once its check gives `Checked.confirm` ({found}); a no asks this one again
    vary: Vary | None = None
    dry: str = ""  # what a dry run prints in place of asking ({ask}, {hint}: [Y/n] or [y/N]); "" for the usual line
    later: str = ""  # the window's button that leaves it for later: the empty answer, as Enter in Terminal
    window_lead: str = ""  # the window's own words in place of `lead` (Terminal's keys stay in Terminal)
    window_ask: str = ""  # and in place of `ask`
    more: str = ""  # the details: the window's More fold
    more_typed: str = ""  # Terminal's one-line form of them (it has no fold), after the lead
    note: str = ""  # one line on why there is no going back here (the window's fine print; a line in Terminal)
    stop: str = ""  # a quiet way out (the window's Stop here, with this line); `stop_typed`: Terminal's words for it
    stop_typed: str = ""

    def __post_init__(self) -> None:
        if not ID.fullmatch(self.id):
            raise ValueError(f"a question's id is 1 to 32 of a to z and _: {self.id!r}")
        if self.is_secret and self.keep not in ("never", "keychain"):
            raise ValueError(f"{self.id} is a secret: never kept in the answers file")

    @property
    def is_secret(self) -> bool:
        """Typed hidden or on the key card, and never kept in the answers file or sent back to a face."""
        return self.kind in SECRET_KINDS if self.secret is None else self.secret

    def option(self, oid: str) -> Option | None:
        return next((option for option in self.options if option.id == oid), None)

    def at(self, answers: Known, mac: Mac, words: Mapping[str, str] | None = None) -> Question:
        """This question as it is asked here: its words for this Mac (`vary`) and its {fields} filled (`words`: her
        name and the like)."""
        asked = replace(self, **self.vary(answers, mac)) if self.vary is not None else self
        given = dict(words or {})
        return replace(
            asked,
            title=fill(asked.title, given),
            lead=fill(asked.lead, given),
            help=fill(asked.help, given),
            listing=fill(asked.listing, given),
            ask=fill(asked.ask, given),
            dry=fill(asked.dry, given),
            window_lead=fill(asked.window_lead, given),
            window_ask=fill(asked.window_ask, given),
            more=fill(asked.more, given),
            more_typed=fill(asked.more_typed, given),
            note=fill(asked.note, given),
            advice=fill(asked.advice, given),
            options=tuple(_filled(option, given) for option in asked.options),
        )

    def shown_value(self, value: Value) -> str:
        """A kept answer in a few words ("Kept: …"): an option's label, yes or no, the list, the filled fields."""
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, str) and not value and self.later:  # left for later (Enter, or the window's Later)
            return "later"
        if isinstance(value, str):
            option = self.option(value)
            return plain_text(option.label) if option is not None else value
        if isinstance(value, tuple):
            labels = [plain_text(option.label) if (option := self.option(item)) is not None else item for item in value]
            return ", ".join(labels) or "none"
        filled = [text for text in value.values() if text]
        return ", ".join(filled) or "none"

    def screen(
        self, answers: Known, mac: Mac, *, step: int = 0, steps: int = 0, back: bool = False
    ) -> dict[str, object]:
        """The window's screen for this question (the protocol's `screen` message): its words, options and fields,
        and "kept" only for an answer kept in the answers file, never a secret's or a step's."""
        screen: dict[str, object] = {
            "t": "screen",
            "id": self.id,
            "kind": self.kind,
            "step": step,
            "steps": steps,
            "title": self.title,
            "lead": self.lead,
            "help": self.help,
            "options": [_option(option) for option in self.window_options()],
            "fields": [
                {"id": f.id, "label": f.label, "placeholder": f.placeholder, "max_chars": f.max_chars}
                for f in self.fields
            ],
            "placeholder": self.placeholder,
            "recommended": self.recommended,
            "layout": None if self.layout is None else _layout(self.layout),
            "back": back,
        }
        if self.keep == "saved" and not self.is_secret and answers.kept(self.id):
            value = answers.get(self.id)
            if value is not None:
                screen["kept"] = json_value(value)
        if self.links:
            screen["links"] = [list(link) for link in self.links]
        if self.later:
            screen["later"] = self.later
        if self.more:
            screen["more"] = self.more
        if self.stop:
            screen["stop"] = self.stop
        return screen

    def window_options(self) -> tuple[Option, ...]:
        """The options as the window draws them: the recommended one with its pill (`tag`), the reason first in its
        small line; a yes or no's two buttons, Yes and No, the recommended one with its pill and its reason under it."""
        options = self.options
        if self.kind == "yes_no" and not options:
            options = (Option("yes", prompts.SETUP_YES), Option("no", prompts.SETUP_NO))
            tagged = self.recommended
        else:
            tagged = None
        why = f"{self.advice[:1].upper()}{self.advice[1:]}." if self.advice else ""
        return tuple(
            replace(option, tag=RECOMMENDED, line=" ".join(part for part in (why, option.line) if part))
            if option.id == tagged or (tagged is None and option.tag == RECOMMENDED)
            else option
            for option in options
        )


def _filled(option: Option, words: Mapping[str, str]) -> Option:
    """An option with its words' {fields} filled (her name in the calls card's guide)."""
    guide = option.guide
    if guide is not None:
        guide = replace(
            guide,
            needs=fill(guide.needs, words),
            steps=tuple(fill(step, words) for step in guide.steps),
            check=fill(guide.check, words),
        )
    return replace(option, label=fill(option.label, words), line=fill(option.line, words), guide=guide)


def _option(option: Option) -> dict[str, object]:
    guide = option.guide
    return {
        "id": option.id,
        "label": option.label,
        "line": option.line,
        "tag": option.tag,
        "on": option.on,
        "guide": None
        if guide is None
        else {
            "needs": guide.needs,
            "steps": list(guide.steps),
            "check": guide.check,
            "errors": [list(error) for error in guide.errors],
            "links": [list(link) for link in guide.links],
        },
    }


def _layout(layout: KeyLayout) -> dict[str, object]:
    return {
        "rows": layout.rows,
        "columns": layout.columns,
        "group": layout.group,
        "label": layout.label,
        "visible": layout.visible,
        "sizes": list(layout.group_sizes()),  # the window draws one box a group, each its own size
        "joiner": layout.joiner,
    }


def json_value(value: Value) -> str | bool | list[str] | dict[str, str]:
    """A value as JSON carries it: a string, a bool, a list of strings or an object of strings."""
    if isinstance(value, str | bool):
        return value
    if isinstance(value, tuple):
        return list(value)
    return dict(value)


def recommended_label(label: str, advice: str = "") -> str:
    """An option's label as Terminal names the recommended one (owner, 29 Oct): "Yes (recommended)", "Yes
    (recommended, when it is her number)"."""
    return prompts.SETUP_RECOMMENDED.format(label=label, advice=f", {advice}" if advice else "")


def recommendation(q: Question) -> str:
    """Plain lines' line before the question, naming the option it recommends as it is typed there, with
    "(recommended)" after it, as gum's menus label it: "Yes (recommended).", "Yes (recommended, once she is stopped).", "1
    (recommended, the tested route)."; "" when it recommends nothing."""
    if q.kind == "yes_no":
        if q.recommended not in ("yes", "no"):
            return ""
        named = [prompts.SETUP_YES if q.recommended == "yes" else prompts.SETUP_NO]
    else:
        named = [option.short or plain_text(option.label) for option in q.options if option.tag == RECOMMENDED]
        if not named:
            return ""
    return f"{recommended_label(' and '.join(named), q.advice)}."


def _each_line(text: str, drawn: str) -> str:
    """`text` with its bold words drawn so (`drawn`: BOLD's replacement), line by line; a command's line ("$ …") as
    it runs, its characters as they are (the page draws it as typed, too)."""
    lines = text.split("\n")
    return "\n".join(line if line.lstrip().startswith("$ ") else BOLD.sub(drawn, line) for line in lines)


def plain_text(text: str) -> str:
    """Setup's words as plain lines show them: the bold marks left out."""
    return _each_line(text, r"\1")


def bold_text(text: str) -> str:
    """Setup's words as a terminal draws them: the key words bold."""
    return _each_line(text, "\x1b[1m\\1\x1b[22m")


def bolded(text: str, *words: str) -> str:
    """`text` with each of `words` in bold where it first comes: a sentence setup shares with a command that prints it
    plain (harden's Registration Lock note, the phone's steps of `estia signal-login`). A word not in it is an error,
    so the two never drift apart."""
    for word in words:
        if word not in text:
            raise ValueError(f"not in the text: {word!r}")
        text = text.replace(word, f"**{word}**", 1)
    return text


# a yes or a no as typed (answers-extras-yes-no-other-words): the English words, the Greek ones, and the y and n keys
# on the Greek layout (υ, ν); anything else is asked again, never taken as a no
YES_WORDS = frozenset({"y", "yes", "ναι", "υ"})
NO_WORDS = frozenset({"n", "no", "όχι", "οχι", "ν"})


def yes_or_no(typed: str) -> bool | None:
    """A typed yes (True) or no (False); None for anything else, Enter included (its caller knows what it takes)."""
    word = typed.strip().lower()
    return True if word in YES_WORDS else False if word in NO_WORDS else None


def yes_no(value: Value, answers: Known, mac: Mac) -> Checked:
    """A yes or a no as `yes_or_no` reads it; anything else typed is a no here (the faces ask again first; Enter is
    taken there too: the recommendation, or no)."""
    if isinstance(value, bool):
        return Checked(value)
    return Checked(isinstance(value, str) and yes_or_no(value) is True)


def clean_paste(text: str, *, spaces: bool) -> tuple[str, int]:
    """What a paste may bring that no answer holds, left out (spec rule 6): the characters a text shows as nothing
    (`hostsec.invisible`: zero-width ones, the soft hyphen, the byte order mark) and line breaks; with `spaces`, every
    kind of space too (a code, a key). Without it, the spaces between words stay, one each, and none at either end.
    The kept text, and how many characters were left out."""
    shown = "".join(" " if char in _LINE_ENDS else char for char in text if not hostsec.invisible(char))
    kept = "".join(char for char in shown if not char.isspace()) if spaces else " ".join(shown.split())
    return kept, len(text) - len(kept)


def pasted(text: str, *, spaces: bool) -> tuple[str, str]:
    """`clean_paste`'s kept text, and the line that says what was left out when a paste brought more than spaces
    around the answer (spec rule 6: "shows what was kept"); "" when nothing but those went."""
    kept, left = clean_paste(text, spaces=spaces)
    plain = "".join(text.split()) if spaces else " ".join(text.split())
    if not left or plain == kept:
        return kept, ""
    return kept, prompts.SETUP_PASTE_KEPT.format(count=len(kept), left=left)

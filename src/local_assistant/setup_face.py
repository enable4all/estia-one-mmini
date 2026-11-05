"""How setup asks: one face per run draws every question of the list. Plain lines in Terminal (`PlainFace`: one line
per question, no redraw, as setup always asked; also for a screen reader), gum's menus in Terminal (`GumFace`), or
the Estia Setup window at the desk (setup_window). Every face hands its answers to the Wizard, which runs the one
check and keeps the answer; a face never checks, keeps or logs anything itself.

A secret (a captcha link, a code, a PIN, a key) is read only from the terminal itself, by the Wizard's own readers
(`Readers`: setup.read_hidden and hostsec.read_hidden_lines), or, at the desk, from the setup window over setup's own
pipe (setup_window); never by gum and never from stdin."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import textwrap
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from local_assistant import hostsec, prompts, setup_gum, setup_keycard
from local_assistant.setup_answers import HANDED
from local_assistant.setup_questions import (
    OTHER,
    RECOMMENDED,
    Checked,
    Option,
    Question,
    Value,
    bold_text,
    plain_text,
    recommendation,
    recommended_label,
    yes_or_no,
)

if TYPE_CHECKING:
    from local_assistant.setup import Wizard
    from local_assistant.setup_plan import Mac

# what gum gets of the environment: how the terminal draws, never the rest of yours
GUM_ENV = ("TERM", "COLORTERM", "LANG", "LC_ALL", "LC_CTYPE", "NO_COLOR")
BACK_WORDS = ("b", "back")  # typed at a question of the plan with one before it: one question back (owner, 27 Oct)


class Back(Exception):
    """The person went back one question of the plan (b in Terminal, Back in the window): the Wizard asks the one
    before again, every answer kept."""


def yes_no_again(typed: str) -> str:
    """The line said when a yes or no gets a word that is neither (P06): the Greek keyboard's hint only when the word
    holds a Greek letter ("υεσ" is yes on the Greek layout); any other word ("ja", "ok") gets the plain line."""
    greek = any(unicodedata.name(char, "").startswith("GREEK") for char in typed)
    return prompts.SETUP_YES_NO_AGAIN if greek else prompts.SETUP_YES_NO_AGAIN_PLAIN


_ENTER_HINT = re.compile(r"\s*\([^()]*\)$")  # a question's "(Enter …)" at its end: the menu shows its own
_HINT = re.compile(r"\s*\(([^()]*)\)")  # a bracketed hint anywhere in a question's line
_ENTER_CLAUSE = re.compile(r";?\s*\bEnter\b[^;()]*")  # its "Enter: …" or "Enter keeps …" part


@dataclass(frozen=True)
class Readers:
    """The Wizard's own readers, called as they are when a face asks (the tests patch the ones in setup.py)."""

    hidden: Callable[[str], str]  # one hidden line from the terminal; "q" at the end of input (setup.Wizard.hidden)
    hidden_lines: Callable[[str, int, Callable[[str], bool]], str]  # a key over several lines (Wizard.hidden_lines)
    flush: Callable[[], None]  # what was typed ahead, dropped (Wizard.flush)
    terminal: Callable[[], bool]  # stdin and stdout both the person's terminal: only there may Enter mean yes


class Face(Protocol):
    """What a face does: show a question and bring back the answer as given (the Wizard checks it). None: the end of
    input (Ctrl-D, a pipe, a closed window), never a yes."""

    name: str  # "plain" | "gum" | "window"
    back: bool  # a Back is offered now: a question of the plan with one before it (the Wizard sets it)
    previous: Value | None  # the answer given before going back to this question: shown, and Enter keeps it
    # after the plan, the line said when b is typed where no Back is offered, and the question asked again; "" within
    # the plan, where b there is an answer (her name at the first question, a no at a "Right?")
    no_back: str

    @property
    def asking(self) -> str:
        """The face that asks now: its own name, or the setup window's Terminal face once the window closed."""

    def say(self, text: str) -> None:
        """A line setup prints (the window also gets it as progress)."""

    def command(self, text: str) -> None:
        """A command setup runs that asks nothing in Terminal, as printed: the window keeps its screen."""

    def line(self, prompt: str) -> str | None:
        """A visible answer to a step's own prompt (one not on the list yet), as typed (the window: on a screen)."""

    def run(
        self,
        argv: list[str],
        *,
        input: str | None,
        capture: bool,
        cwd: Path | None,
        why: str,
        answer: Callable[[str, list[str]], str | None] | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        """A step's command, run by the face itself (the window: its output in the window's log, admin rights through
        macOS's own box, `why` that box's line; `answer`: a question the command asks on a marked line, answered on a
        screen); None: setup runs it as it always did (Terminal, where sudo and the command ask)."""

    def show(self, q: Question, kept: Value | None) -> None:
        """The question's title, what it is for and its options; or, kept, the "Kept: …" line."""

    def choose(self, q: Question, kept: Value | None) -> Value | None:
        """A choice, cards or a folder: the option's id, or the text typed."""

    def switches(self, q: Question, kept: Value | None) -> Value | None:
        """Switches: the ids picked, or the text typed."""

    def confirm(self, q: Question, default: bool) -> bool | None:
        """Yes or no; Enter takes `default` only from a terminal (the face knows which)."""

    def text(self, q: Question, kept: Value | None, message: str) -> str | None:
        """A visible text answer."""

    def fields(self, q: Question, kept: Value | None, message: str) -> dict[str, str] | None:
        """Each field's text (Enter leaves one empty)."""

    def hidden(self, q: Question, message: str) -> str | None:
        """A hidden answer, from the terminal itself; never gum."""

    def key(self, q: Question, message: str, groups: tuple[str, ...]) -> str | None:
        """A long key, group by group (the key card, its groups kept from a try before and the line why), or hidden
        over several lines."""

    def wait(self, q: Question, look: Callable[[], bool], seconds: float) -> bool:
        """Wait for something to arrive by itself (the one-time word): True once `look` says it came."""

    def checked(self, q: Question, result: Checked) -> None:
        """A check's answer: why it was refused, or what was kept."""

    def done(self, lines: list[str]) -> None:
        """Setup's last words, said already: the window shows them as its closing list; Terminal faces need nothing."""

    def progress(self, text: str) -> None:
        """A download's five dots (setup_progress), in the place of its line before where the face can redraw."""

    def progress_done(self) -> None:
        """That download's line is done: the next line starts below it."""

    def stopped(self, text: str) -> None:
        """Setup stops (a step's ✗ and its fix): what was found, why it matters and the fix, each on its own with
        space around it (owner, 29 Oct: "we need more space, make it readable"); the window shows it as its own
        screen."""


# a stop's words, as the steps say them: "✗ Ollama: what was found: why it matters\n  Fix: the fix"
SHORT_LINE = 88  # Terminal's paragraphs, at most this wide (setup_plan's own lines too)


@dataclass(frozen=True)
class Stop:
    """A stop's words in their parts: the area ("Ollama"; "" when they name none), what was found (one line), why it
    matters ("" when they say nothing more) and the fix ("" when there is none)."""

    area: str
    what: str
    why: str
    fix: str


def _top_level(text: str, mark: str) -> int:
    """Where `mark` first comes outside brackets (a path or a command in brackets may hold it); -1 when it does not."""
    depth = 0
    for at, char in enumerate(text):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(mark, at):
            return at
    return -1


def stop_parts(text: str) -> Stop:
    """A stop's words split for both faces: "✗ <area>: <what>: <why>", then "Fix: <fix>" (on a line of its own or
    after the why). Words of another shape: their first sentence is what, the rest why."""
    lines = [line.strip() for line in plain_text(text).strip().splitlines() if line.strip()]
    fixes = [line.removeprefix("Fix:").strip() for line in lines if line.startswith("Fix:")]
    body = " ".join(line for line in lines if not line.startswith("Fix:"))
    inline = _top_level(body, " Fix: ")
    if inline >= 0:
        body, fixes = body[:inline], [body[inline + len(" Fix: ") :].strip(), *fixes]
    area = ""
    if body.startswith("✗ "):
        head, colon, rest = body[2:].partition(": ")
        if colon and len(head) <= 40 and _top_level(head, "(") < 0:
            area, body = head, rest
    cut = _top_level(body, ": ")
    if cut >= 0:
        what, why = body[:cut], body[cut + 2 :]
    elif (cut := _top_level(body, ". ")) >= 0:
        what, why = body[: cut + 1], body[cut + 2 :]
    else:
        what, why = body, ""
    return Stop(area, what.strip(), why.strip(), " ".join(fixes))


def sentence(text: str) -> str:
    """A part of a stop as a sentence of its own: its first letter capital, a full stop at its end."""
    text = text.strip()
    if not text:
        return ""
    return text[:1].upper() + text[1:] + ("" if text[-1] in ".!?" else ".")


def stop_text(text: str) -> str:
    """A stop as Terminal shows it: a blank line, the ✗ line, then why it matters and the fix, each a paragraph of
    short lines indented under it."""
    stop = stop_parts(text)
    what = stop.what if stop.what[-1:] in ".!?" else f"{stop.what}."
    head = f"✗ {stop.area}: {what}" if stop.area else what
    parts = [head]
    if stop.why:
        parts.append(textwrap.fill(sentence(stop.why), SHORT_LINE, initial_indent="  ", subsequent_indent="  ",
                                   break_on_hyphens=False, break_long_words=False))  # fmt: skip
    if stop.fix:
        parts.append(f"  Fix: {stop.fix}")
    return "\n\n".join(parts)


def kept_line(q: Question, kept: Value) -> str:
    """A re-run's line for an answer kept from an earlier run. About you and the home area, once handed over to her,
    name /profile: her first start imports them only once, so a --redo would reach her no more (Fable, 27 Oct)."""
    shown = f"{q.title}: {q.shown_value(kept)}"
    if kept == HANDED:
        return prompts.SETUP_KEPT_PROFILE.format(shown=shown)
    return prompts.SETUP_KEPT.format(shown=shown, id=q.id)


class PlainFace:
    """Plain lines: one line per question, no redraw, as setup always asked (and best with a screen reader)."""

    name = "plain"
    back_how = prompts.SETUP_BACK_HOW  # said once, at the first question with a Back: how to go back here

    def __init__(self, readers: Readers) -> None:
        self.readers = readers
        self.back = False
        self.previous: Value | None = None
        self.no_back = ""  # after the plan: a b is no answer (the Wizard sets it)
        self.told_back = False  # "Type b to go back one question …", said once

    @property
    def asking(self) -> str:
        return self.name

    def say(self, text: str) -> None:
        """A line setup says, its bold words as this face draws them (never a mark shown)."""
        print(self.styled(text), flush=True)

    def command(self, text: str) -> None:
        self.say(text)

    def line(self, prompt: str) -> str | None:
        """The answer as typed; None at the end of input (a pipe, or Ctrl-D). b or back, where a Back is offered,
        goes one question back (`Back`); after the plan it is no answer: `no_back` says why, and the question is asked
        again (second deep scan: at "Use the Signal number already set up here?" it was a no, and setup registered
        the number again)."""
        while True:
            try:
                answer = input(f"{prompt} ").strip()
            except EOFError:
                print()
                return None
            if answer.lower() not in BACK_WORDS:
                return answer
            if self.back:
                raise Back
            if not self.no_back:
                return answer
            self.say(self.no_back)

    def show(self, q: Question, kept: Value | None) -> None:
        if kept is not None:
            self.say(kept_line(q, kept))
            return
        self.block(q, listing=True)

    def block(self, q: Question, *, listing: bool) -> None:
        """A question as Terminal shows it (owner, 27 Oct: "in terminal, we also need some blank rows, too much
        text"): a blank line, its title, its paragraphs with a blank line between them (the options' list, when this
        face draws no menu), its details in one line (the window's fold holds them in full), then its recommendation
        on a line of its own right before the question, the option named with "(recommended)" after it (gum's menus
        label the option itself)."""
        texts = [q.lead, q.listing if listing else "", q.more_typed, q.note, q.stop_typed]
        body = "\n\n".join(text.strip("\n") for text in texts if text.strip())
        self.say("")
        if body:
            self.say(self.styled(f"**{q.title}**\n\n{body}" if q.title else body))
        lines = ["" if self.menu_marks(q) else recommendation(q)]
        if self.previous is not None:  # gone back to: the answer before, which Enter keeps
            lines.append(prompts.SETUP_BACK_KEPT.format(shown=q.shown_value(self.previous)))
        if self.back and not self.told_back:
            self.told_back = True
            lines.insert(0, self.back_how)
        lines = [line for line in lines if line]
        if lines:
            if body:
                self.say("")
            self.say("\n".join(lines))

    def styled(self, text: str) -> str:
        """Setup's bold words as this face draws them: plain lines leave the marks out (a screen reader reads them)."""
        return plain_text(text)

    def menu_marks(self, q: Question) -> bool:
        """This face's menu labels the recommended option itself ("Yes (recommended)"): plain lines have no menu."""
        return False

    def _ask(self, q: Question) -> str:
        """The question's line; gone back to, without its Enter hint: Enter keeps the answer before there."""
        return without_enter(q.ask) if self.previous is not None else q.ask

    def choose(self, q: Question, kept: Value | None) -> Value | None:
        return self.line(self._ask(q))

    def switches(self, q: Question, kept: Value | None) -> Value | None:
        return self.line(self._ask(q))

    def confirm(self, q: Question, default: bool) -> bool | None:
        """y or n, as setup always asked: [Y/n] where Enter is yes, and only from a terminal; a yes or a no in English
        or Greek (`yes_or_no`), anything else asked again (`yes_no_again`; a pipe, never a person: a no)."""
        terminal = self.readers.terminal()
        enter = default and terminal
        while True:
            answer = self.line(f"{q.ask} {'[Y/n]' if enter else '[y/N]'}")
            if answer is None:
                return None
            if not answer.strip():
                return enter
            said = yes_or_no(answer)
            if said is not None or not terminal:
                return said is True
            self.say(yes_no_again(answer))

    def text(self, q: Question, kept: Value | None, message: str) -> str | None:
        return self.line(self._ask(q))

    def fields(self, q: Question, kept: Value | None, message: str) -> dict[str, str] | None:
        """One line per field, Enter leaves it empty (or keeps the answer before, gone back to)."""
        before = self.previous if isinstance(self.previous, Mapping) else {}
        given: dict[str, str] = {}
        for field in q.fields:
            answer = self.line(f"{field.label}:")
            if answer is None:
                return None
            given[field.id] = answer or str(before.get(field.id, ""))
        return given

    def hidden(self, q: Question, message: str) -> str | None:
        answer = self.readers.hidden(q.ask)
        return None if answer.strip().lower() == "q" else answer

    def key(self, q: Question, message: str, groups: tuple[str, ...]) -> str | None:
        """Today's hidden read over several lines: until all the key's characters have come."""
        layout = q.layout
        needed = layout.rows * layout.columns * layout.group if layout is not None else hostsec.KEY_LENGTH
        try:
            answer = self.readers.hidden_lines(q.ask, needed, hostsec.key_character)
        except EOFError:
            return None
        return None if answer.strip().lower() == "q" else answer

    def wait(self, q: Question, look: Callable[[], bool], seconds: float) -> bool:
        return look()

    def run(
        self,
        argv: list[str],
        *,
        input: str | None,
        capture: bool,
        cwd: Path | None,
        why: str,
        answer: Callable[[str, list[str]], str | None] | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        """Terminal: setup runs it as it always did (sudo asks for the Mac password there, the command its own
        questions)."""
        return None

    def done(self, lines: list[str]) -> None:
        return None

    def progress(self, text: str) -> None:
        """Each new dot a line of its own: no redraw (a screen reader reads each line once)."""
        self.say(text)

    def progress_done(self) -> None:
        return None

    def stopped(self, text: str) -> None:
        """The stop, with a blank line before it and between its parts (`stop_text`)."""
        self.say("")
        self.say(stop_text(text))

    def checked(self, q: Question, result: Checked) -> None:
        if result.value is None:
            if result.message:
                self.say(result.message)
        elif result.shown:
            self.say(result.shown)


class GumFace(PlainFace):
    """gum's menus in Terminal: a list to pick from, two buttons for a yes or no, a line to type in, each as the
    question's words and options say, the recommended option first (Enter takes it). Secrets never go through gum (the
    hidden reads and the key card are the plain face's), and neither does a question whose words hold an answer of
    yours (`private`: her number, an Account ID, About you, your home area): ps shows a program's arguments to every
    user of the Mac. A gum that fails or is stopped with Esc gives the plain line instead; Ctrl-C stops setup."""

    name = "gum"
    back_how = prompts.SETUP_BACK_HOW_MENU  # b types nothing in a menu: Back is its last item

    def __init__(self, readers: Readers, program: Path, *, private: Callable[[], Iterable[str]] = tuple) -> None:
        super().__init__(readers)
        self.program = program
        self.private = private
        self.drawing = False  # a download's dots are on the terminal's last line, redrawn in place

    def say(self, text: str) -> None:
        self.progress_done()  # a download's line ends first
        super().say(text)

    def progress(self, text: str) -> None:
        """The dots redrawn in place on a terminal (the line cleared, then drawn again); else a line each."""
        if not sys.stdout.isatty():
            super().progress(text)
            return
        sys.stdout.write("\r\x1b[2K" + text)
        sys.stdout.flush()
        self.drawing = True

    def progress_done(self) -> None:
        if self.drawing:
            self.drawing = False
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _gum(self, args: list[str]) -> subprocess.CompletedProcess[str] | None:
        """gum with these arguments, drawing on the terminal and answering on stdout; None when it is not run (an
        answer of yours in its arguments) or did not start."""
        mine = [value for value in self.private() if value]
        if any(value in arg for value in mine for arg in args):
            return None
        env = {key: os.environ[key] for key in GUM_ENV if key in os.environ}
        try:
            done = subprocess.run([str(self.program), *args], stdout=subprocess.PIPE, text=True, env=env, check=False)
        except OSError:
            return None
        if done.returncode == 130:  # Ctrl-C in gum, as at a plain question
            raise KeyboardInterrupt
        return done

    def show(self, q: Question, kept: Value | None) -> None:
        if kept is not None or not q.options:
            super().show(q, kept)
            return
        # the options are gum's to show, by their labels: a list whose options have a line of their own stays
        lined = q.kind in ("choice", "cards", "switches") and any(option.line for option in q.options)
        self.block(q, listing=lined)

    def styled(self, text: str) -> str:
        """Bold words bold on a terminal; their marks left out anywhere else."""
        return bold_text(text) if sys.stdout.isatty() else plain_text(text)

    def menu_marks(self, q: Question) -> bool:
        """gum's menus label the recommended option ("Yes (recommended)", owner, 29 Oct): no line of its own; a
        question gum does not draw says it on the plain line's way (`_plain_line`)."""
        return q.kind in ("yes_no", "choice", "cards", "folder", "switches")

    def _plain_line(self, q: Question) -> None:
        """gum did not draw the question (an answer of yours in its words, a label twice, Esc): its plain line comes,
        with the recommendation's line before it, as plain lines say it."""
        if line := recommendation(q):
            self.say(line)

    @staticmethod
    def _label(q: Question, option: Option, *, advice: bool = True) -> str:
        """An option's label in gum's menu: the recommended one with "(recommended)" after it, and its reason."""
        label = plain_text(option.label)
        return recommended_label(label, q.advice if advice else "") if option.tag == RECOMMENDED else label

    @staticmethod
    def _answer(q: Question, yes: bool) -> str:
        """Yes or No as gum's button or menu item: the recommended one with "(recommended)" after it."""
        label = prompts.SETUP_YES if yes else prompts.SETUP_NO
        return recommended_label(label, q.advice) if q.recommended == ("yes" if yes else "no") else label

    def _with_back(self, labels: list[str]) -> list[str]:
        """A menu's labels, and Back last where one is offered (a question of the plan with one before it)."""
        back = self.back and prompts.SETUP_BACK_OPTION not in labels
        return [*labels, prompts.SETUP_BACK_OPTION] if back else labels

    def choose(self, q: Question, kept: Value | None) -> Value | None:
        """gum's list of the options, by their labels without the bold marks (a menu cannot draw them), the recommended
        one with "(recommended)" after it, as gum then prints the one picked."""
        labels = [self._label(q, option) for option in q.options]
        if len(labels) < 2 or len(set(labels)) != len(labels):
            self._plain_line(q)
            return super().choose(q, kept)
        accent = setup_gum.THEME["accent"]
        before = q.option(self.previous) if isinstance(self.previous, str) else None
        done = self._gum(
            [
                "choose",
                f"--header={_header(q.ask)}",
                "--header.foreground=",
                f"--cursor.foreground={accent}",
                f"--selected.foreground={accent}",
                # gone back to: the one before
                *([f"--selected={self._label(q, before)}"] if before is not None else []),
                "--",
                *self._with_back(labels),
            ]
        )
        picked = done.stdout.rstrip("\n") if done is not None and done.returncode == 0 else None
        if self.back and picked == prompts.SETUP_BACK_OPTION:
            raise Back
        option = next((option for option in q.options if self._label(q, option) == picked), None)
        if option is None:
            return self._plain_choice(q, kept)
        if option.id == OTHER:  # another name: typed in a line of its own
            typed = self._input(plain_text(option.label), q.placeholder, 0)
            return typed if typed is not None else self._plain_choice(q, kept)
        return option.id

    def _plain_choice(self, q: Question, kept: Value | None) -> Value | None:
        """The plain line instead, with the options listed as plain lines show them."""
        if q.listing:
            self.say(q.listing)
        self._plain_line(q)
        return super().choose(q, kept)

    def switches(self, q: Question, kept: Value | None) -> Value | None:
        labels = [self._label(q, option, advice=False) for option in q.options]  # --selected splits at commas
        if not labels or len(set(labels)) != len(labels) or any("," in label for label in labels):
            self._plain_line(q)
            return super().switches(q, kept)
        chosen = kept if isinstance(kept, tuple) else tuple(option.id for option in q.options if option.on)
        on = [self._label(q, option, advice=False) for option in q.options if option.id in chosen]
        accent = setup_gum.THEME["accent"]
        done = self._gum(
            [
                "choose",
                "--no-limit",
                f"--header={_header(q.ask)}",
                "--header.foreground=",
                f"--cursor.foreground={accent}",
                f"--selected.foreground={accent}",
                *([f"--selected={','.join(on)}"] if on else []),
                "--",
                *self._with_back(labels),
            ]
        )
        if done is None or done.returncode != 0:
            self._plain_line(q)
            return super().switches(q, kept)
        picked = {line for line in done.stdout.splitlines() if line}
        if self.back and prompts.SETUP_BACK_OPTION in picked:
            raise Back
        return tuple(option.id for option in q.options if self._label(q, option, advice=False) in picked)

    def confirm(self, q: Question, default: bool) -> bool | None:
        """gum's Yes and No, the recommended one with "(recommended)" after it; where a Back is offered, a menu of Yes,
        No and Back, the default first."""
        yes, no = self._answer(q, True), self._answer(q, False)
        if self.back:
            first, second = (yes, no) if default else (no, yes)
            accent = setup_gum.THEME["accent"]
            done = self._gum(
                [
                    "choose",
                    f"--header={_header(q.ask)}",
                    "--header.foreground=",
                    f"--cursor.foreground={accent}",
                    f"--selected.foreground={accent}",
                    "--",
                    first,
                    second,
                    prompts.SETUP_BACK_OPTION,
                ]
            )
            picked = done.stdout.rstrip("\n") if done is not None and done.returncode == 0 else None
            if picked == prompts.SETUP_BACK_OPTION:
                raise Back
            if picked in (yes, no):
                return picked == yes
            self._plain_line(q)
            return super().confirm(q, default)
        done = self._gum(
            [
                "confirm",
                f"--default={'true' if default else 'false'}",
                f"--affirmative={yes}",
                f"--negative={no}",
                f"--selected.background={setup_gum.THEME['accent']}",
                q.ask,
            ]
        )
        if done is None or done.returncode not in (0, 1):
            self._plain_line(q)
            return super().confirm(q, default)
        return done.returncode == 0

    def text(self, q: Question, kept: Value | None, message: str) -> str | None:
        typed = self._input(_header(q.ask), q.placeholder, 0)
        return typed if typed is not None else super().text(q, kept, message)

    def fields(self, q: Question, kept: Value | None, message: str) -> dict[str, str] | None:
        """One gum line per field; gone back to, Enter keeps each answer before (never in gum's arguments)."""
        before = self.previous if isinstance(self.previous, Mapping) else {}
        given: dict[str, str] = {}
        for field in q.fields:
            typed = self._input(field.label, field.placeholder, field.max_chars)
            if typed is None:
                typed = self.line(f"{field.label}:")
                if typed is None:
                    return None
            given[field.id] = typed or str(before.get(field.id, ""))
        return given

    def _input(self, header: str, placeholder: str, limit: int) -> str | None:
        """One line typed in gum (Enter on nothing: empty); None when gum did not take it."""
        done = self._gum(
            [
                "input",
                f"--header={header}",
                "--header.foreground=",
                f"--placeholder={placeholder}",
                f"--placeholder.foreground={setup_gum.THEME['muted']}",
                f"--cursor.foreground={setup_gum.THEME['accent']}",
                f"--char-limit={limit}",
            ]
        )
        if done is None or done.returncode != 0:
            return None
        typed = done.stdout.rstrip("\n").strip()
        if self.back and typed.lower() in BACK_WORDS:
            raise Back
        if self.no_back and typed.lower() in BACK_WORDS:  # after the plan: no answer, asked again
            self.say(self.no_back)
            return self._input(header, placeholder, limit)
        return typed

    def key(self, q: Question, message: str, groups: tuple[str, ...]) -> str | None:
        """The key card (setup_keycard.run_card, on the terminal's own screen) in a terminal wide and tall enough, its
        groups kept and the line why; else the plain face's hidden read. gum never sees a key."""
        card = setup_keycard.card_for(q.layout)
        if card is None or not setup_keycard.fits(hostsec.TTY):
            return super().key(q, message, groups)
        typed = setup_keycard.run_card(card, groups=list(groups), message=message, tty=hostsec.TTY)
        return None if typed is None else "".join(typed)


def _header(ask: str) -> str:
    """A question's line as gum's header: without its "(Enter …)" end, which the menu shows its own way."""
    return _ENTER_HINT.sub("", ask)


def without_enter(ask: str) -> str:
    """A question's line without what it says Enter does ("(Enter: female)" goes; "(1 or 2; Enter: 1)" keeps "(1 or
    2)"): gone back to, Enter keeps the answer before, which its own line says."""

    def kept(found: re.Match[str]) -> str:
        rest = _ENTER_CLAUSE.sub("", found.group(1)).strip(" ;")
        return f" ({rest})" if rest else ""

    return _HINT.sub(kept, ask)


def screen_reader() -> bool:
    """VoiceOver runs for you (a fixed command under the tools' root): plain lines read best with it."""
    pgrep = hostsec.tool_root() / "usr/bin/pgrep"
    try:
        done = subprocess.run(
            [str(pgrep), "-x", "-U", str(os.getuid()), "VoiceOver"], capture_output=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def choose_face(args: argparse.Namespace, mac: Mac, wizard: Wizard) -> Face:
    """gum's menus, or plain lines: with --plain, a dry run, a terminal that draws no menus (TERM dumb or none), no
    terminal on both ends, VoiceOver running, or no checked gum (setup_gum says why)."""
    plain = PlainFace(wizard.readers())
    if wizard.dry_run:
        setup_gum.program(dry_run=True, say=wizard.say)
        return plain
    if getattr(args, "plain", False) or os.environ.get("TERM", "dumb") in ("", "dumb"):
        return plain
    if not wizard.terminal() or screen_reader():
        return plain
    program = setup_gum.program(dry_run=False, say=wizard.say)
    if program is None:
        return plain
    return GumFace(wizard.readers(), program, private=wizard.private_values)

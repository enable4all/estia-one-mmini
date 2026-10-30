"""What setup's step modules may use of the Wizard (`setup.Wizard`): its attributes and its input and output, typed
here so mypy checks the steps that live in modules of their own (`setup_signal`, `setup_extras`). Nothing here asks or
prints: the Wizard in setup.py does, through its face."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from local_assistant import harden

if TYPE_CHECKING:
    from local_assistant.setup_answers import Answers
    from local_assistant.setup_face import Face
    from local_assistant.setup_plan import Mac
    from local_assistant.setup_questions import Value


class WizardBase:
    """The Wizard's attributes and methods that the steps call; setup.Wizard sets and defines every one."""

    config_path: Path
    dry_run: bool
    name: str  # hers: the config's, once there is one, or the one chosen at the name question
    voice: str
    style: str
    signal_cli: str
    username: str  # her Signal username, for an account without a number (how you find her)
    answers: Answers  # the answers file (in memory for a dry run)
    planned: dict[str, Value]  # this run's plan's answers, by id
    face: Face  # how setup asks: plain lines, gum's menus or the setup window

    @property
    def mac(self) -> Mac:
        """What the Mac answers (setup_plan.Mac)."""
        raise NotImplementedError

    def answer(self, qid: str, *, refused: str = "", **words: str) -> Value | None:
        """One question of the list (setup_plan.QUESTIONS), by its id: kept or planned, quietly; else asked through
        the face, checked and saved by its question's rules. `words` fill the question's {fields}; `refused`: why
        the answer before was refused (Signal's), said on the question."""
        raise NotImplementedError

    def say(self, text: str) -> None:
        raise NotImplementedError

    def typed(self, question: str) -> str | None:
        """The answer as typed; None without a terminal (a pipe, or Ctrl-D)."""
        raise NotImplementedError

    def ask(self, question: str, default: str = "") -> str:
        raise NotImplementedError

    def yes(self, question: str, *, default: bool = False) -> bool:
        raise NotImplementedError

    def again(self, question: str, *, qid: str = "", **words: str) -> bool:
        """Try again (Enter) or stop (q); in the window, the screen of question `qid`, its fields filled (`words`)."""
        raise NotImplementedError

    def hidden(self, prompt: str) -> str:
        """A hidden line from the terminal only (the captcha link, a code, a PIN)."""
        raise NotImplementedError

    def hidden_lines(self, prompt: str, needed: int, counted: Callable[[str], bool]) -> str:
        """Hidden lines from the terminal only, until `needed` characters `counted` takes have come (a key)."""
        raise NotImplementedError

    def flush(self) -> None:
        """Drop what was typed ahead on the terminal before the next question shows."""
        raise NotImplementedError

    def run(
        self,
        command: list[str],
        *,
        shown: list[str] | None = None,
        printed: bool = False,
        why: str = "",
        answer: Callable[[str, list[str]], str | None] | None = None,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """A fixed command, printed first; in the window the face runs it (`why`: macOS's box's line for one with
        admin rights)."""
        raise NotImplementedError

    def offer(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        failed: str = "",
        why: str = "",
        window: Callable[[], int] | None = None,
        what: str = "",
    ) -> bool:
        """A command you would otherwise type: shown, then "Press Enter to run it (or s to skip)" (the window: Run it
        or Skip), and run on the yes (`failed`: the line said when it ran and ended badly; `why`: macOS's box's
        line; `window`: what the window runs in its place, its exit code, where the command would ask on a terminal
        the window does not have; `what`: what it does, the window screen's first line)."""
        raise NotImplementedError

    @property
    def in_window(self) -> bool:
        """The setup window asks now (it is open): a step's own question is one of its screens."""
        return self.face.asking == "window"

    def keychain_opener(self) -> Callable[[], bool] | None:
        """How a locked login keychain is opened: on the terminal (None: setup_keychain.unlock, hidden there), or in
        the window by macOS's own box when the write reaches it (at the desk: owner, 29 Oct)."""
        return (lambda: True) if self.in_window else None

    def command(self, text: str) -> None:
        """A command setup runs that asks nothing in Terminal (its input and output are setup's), as printed."""
        raise NotImplementedError

    def registration(
        self, method: str, params: dict[str, object], shown: str, secrets: dict[str, str]
    ) -> harden.RpcAnswer:
        raise NotImplementedError

    def own_user(self) -> bool:
        raise NotImplementedError

    def her_config(self, lines: str, change: Sequence[str] = ()) -> bool:
        """Her config's lines: opened as her in Terminal, added as her by herconfig in the window (setup_extras); True
        when it was opened (or changed) as her."""
        raise NotImplementedError

    def extras(self) -> None:
        """The extras list and each picked one's steps (setup_extras.ExtrasSteps)."""
        raise NotImplementedError

    def _write_private(self, path: Path, text: str) -> None:
        raise NotImplementedError

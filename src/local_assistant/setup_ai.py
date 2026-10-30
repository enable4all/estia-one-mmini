"""Setup started by an AI's command tool (owner, 29 Oct: "instruct the user's AI to run the script and take it from
there"): no terminal, at the Mac's own screen. The Estia Setup window is then setup's only face: every answer, code,
PIN and key is typed there, the Mac password only in macOS's own box, and nothing is ever read from stdin.

`main` decides the route before anything else runs (no thread, nothing of macOS's frameworks): a terminal, or a dry
run, is the Terminal route as before; without one, a run setup cannot take here is refused in one line (exit 2), and
any other is detached (`detach`): a pipe, a fork, a session of its own, a fork again, so neither the AI's time limit
nor a kill of its process tree or group ends a setup whose window is open. The first process only relays the
channel's lines to the AI (`relay`) and exits with the code setup writes last. Setup runs in the grandchild, fds 0, 1
and 2 on /dev/null: what it prints, what its programs print and Python's own errors reach no one. The AI reads only
the channel's own lines (`Channel`): fixed words of setup's, a step, a screen's title, where macOS asks, the end;
never an answer, a code, a PIN, a key, her number, an account ID, About you, the home area or a command's output.

`run` is the one end point: every end of an AI-run writes exactly one end line, then its code (0 done, 1 stopped or
failed, 130 Stop here, 2 refused before anything), and while the window is open it gets the stop screen. Within it
every terminal reader and setup's own sudo are refused at run time (`guarded`), never an end of input taken as an
answer.

What it cannot do: contain an AI that runs as you. Such an AI can read the files setup writes as you, the keychain
copies the security tool reads back without a dialog, edit the checkout and set setup's environment."""

from __future__ import annotations

import argparse
import builtins
import contextlib
import ctypes
import os
import pwd
import re
import select
import signal
import sys
import time
import traceback
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from local_assistant import prompts

if TYPE_CHECKING:
    import subprocess

    from local_assistant.setup import Wizard
    from local_assistant.setup_questions import Checked, Question, Value

END = "\x1e"  # the channel's last line starts with it: the exit code follows
LOG = "ai-run.log"  # beside the answers file: the channel's lines, for an AI whose command stopped waiting
WRITE_SECONDS = 0.5  # a line the relay does not take this fast is dropped: the window never waits for the AI
END_SECONDS = 2.0  # the last word waits a little longer
# a test's roots and doctor's sudo listing on stdin: honoured only where pytest itself runs (the environment cannot
# fake a module loaded in this process; never PYTEST_CURRENT_TEST)
TEST_KEYS = ("ESTIA_ROOT", "ESTIA_TEST_ROOT", "ESTIA_OWNER_SUDO")
STOPPED = 1
STOP_HERE = 130
REFUSED = 2


# ------------------------------------------------------------------ the route, decided before anything runs


def refused(args: argparse.Namespace, *, config: Path, desk: bool = True) -> str:
    """Why setup cannot run here without a terminal, in one line; "" when it can (the AI-run). Read before the fork:
    argparse, os, pwd, the desk's two fixed commands, one call into libSystem. `desk=False`: its arguments only (the
    run itself, whose window checks the desk again)."""
    from local_assistant.setup_plan import SESSION_KEYS, SSH_KEYS
    from local_assistant.setup_window import at_the_desk

    if os.geteuid() == 0:
        return prompts.SETUP_NOT_ROOT
    for flag in ("plain", "terminal"):
        if getattr(args, flag, False):
            return prompts.SETUP_AI_TERMINAL_FLAG.format(flag=f"--{flag}")
    if getattr(args, "extras", False):
        return prompts.SETUP_AI_EXTRAS
    if getattr(args, "answers", None) is not None or Path(getattr(args, "config", config)) != config:
        return prompts.SETUP_AI_FILES
    test_roots()
    if not desk:
        return ""
    if any(os.environ.get(key) for key in SSH_KEYS):
        return prompts.SETUP_AI_NOT_AT_DESK
    if any(os.environ.get(key) for key in SESSION_KEYS):
        return prompts.SETUP_AI_IN_SESSION
    if sandboxed():
        return prompts.SETUP_AI_SANDBOX
    if os.path.normpath(os.environ.get("HOME") or "/") != os.path.normpath(pwd.getpwuid(os.getuid()).pw_dir):
        return prompts.SETUP_AI_HOME
    if not at_the_desk():
        return prompts.SETUP_AI_NOT_AT_DESK
    return ""


def test_roots() -> None:
    """ESTIA_ROOT, ESTIA_TEST_ROOT and ESTIA_OWNER_SUDO left out of an AI-run, unless pytest runs it here."""
    if "pytest" not in sys.modules:
        for key in TEST_KEYS:
            os.environ.pop(key, None)


def sandboxed() -> bool:
    """This process runs in a macOS sandbox (Claude Code's, Codex's): sandbox_check on its own pid. Best effort: no
    such symbol, or any error, is no sandbox (a write that fails later says so itself)."""
    try:
        check = ctypes.CDLL(None).sandbox_check
        check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        check.restype = ctypes.c_int
        return bool(check(os.getpid(), None, 0) == 1)
    except (AttributeError, OSError, ValueError):
        return False


# ------------------------------------------------------------------ the channel: the AI's only view of setup


class Channel:
    """The lines the AI reads: a pipe (non-blocking, so a line the relay does not take is dropped, never waited for),
    and, once this run holds the answers file's lock, ~/.config/estia-setup/ai-run.log (0600, truncated then, so a
    second run never clears the first's). Only setup's fixed words are written here."""

    def __init__(self, fd: int | None = None) -> None:
        self.fd = fd
        self.lines: list[str] = []  # every line, in turn (the log gets those before its lock too)
        self.log: int | None = None

    def line(self, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            return
        self.lines.append(text)
        self._write((text + "\n").encode(), WRITE_SECONDS)
        if self.log is not None:
            with contextlib.suppress(OSError):
                os.write(self.log, (text + "\n").encode())

    def open_log(self, path: Path) -> None:
        """The log, from now on: its folder made private, the file truncated, every line so far written first."""
        from local_assistant.setup_answers import private_folders

        try:
            private_folders(path.parent)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            os.fchmod(fd, 0o600)
            os.write(fd, "".join(f"{line}\n" for line in self.lines).encode())
        except OSError:
            return
        self.log = fd

    def end(self, text: str, code: int) -> None:
        """The end line, then the code for the relay (never a second end)."""
        self.line(text)
        self._write(f"{END}{code}\n".encode(), END_SECONDS)
        if self.log is not None:
            with contextlib.suppress(OSError):
                os.close(self.log)
            self.log = None

    def _write(self, data: bytes, seconds: float) -> None:
        """Into the pipe, waiting at most `seconds` for room; a relay gone (the AI's command ended) ends the pipe."""
        deadline = time.monotonic() + seconds
        while data and self.fd is not None:
            try:
                data = data[os.write(self.fd, data) :]
                continue
            except BlockingIOError:
                pass
            except OSError:  # the relay is gone: setup goes on in its window
                self.fd = None
                return
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([], [self.fd], [], left)[1]:
                self.fd = None  # it stopped reading: a half line is never followed by another
                return


def relay(fd: int, out: int = 1) -> int:
    """The first process: each channel line to the AI as it comes, then the code setup wrote last; none (setup was
    killed) is one line and 1."""
    code: int | None = None
    pending = b""

    def write(data: bytes) -> None:
        with contextlib.suppress(OSError):
            while data:
                data = data[os.write(out, data) :]

    while True:
        try:
            chunk = os.read(fd, 65536)
        except InterruptedError:
            continue
        if not chunk:
            break
        pending += chunk
        *whole, pending = pending.split(b"\n")
        for raw in whole:
            if raw.startswith(END.encode()):
                text = raw[len(END) :].decode(errors="replace")
                code = int(text) if text.isdecimal() else STOPPED
            else:
                write(raw + b"\n")
    if code is None:
        write((prompts.SETUP_AI_NO_LAST_WORD + "\n").encode())
        return STOPPED
    return code


def detach(body: Callable[[Channel], int]) -> int:
    """`body` in a grandchild of its own session (a fork, setsid, a fork again: no one's descendant, so no kill of
    the AI's process group or tree reaches it), fds 0, 1 and 2 on /dev/null; this process relays its channel."""
    read, write = os.pipe()
    child = os.fork()
    if child == 0:
        code = STOPPED
        try:
            os.close(read)
            os.setsid()
            if os.fork() != 0:
                os._exit(0)
            null = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(null, fd)
            os.close(null)
            os.set_blocking(write, False)
            code = body(Channel(write))
        except BaseException:  # the grandchild's end: its channel said it, or the relay does
            code = STOPPED
        finally:
            with contextlib.suppress(Exception):
                sys.stdout.flush()
                sys.stderr.flush()
            os._exit(code)
    os.close(write)
    os.waitpid(child, 0)  # it ends at once, its grandchild on its own
    try:
        return relay(read)
    finally:
        os.close(read)


# ------------------------------------------------------------------ one run: its end point and its guards


class Run:
    """One AI-run: its channel, its Wizard once made (where it stopped, its window), and what its end says."""

    def __init__(self, channel: Channel) -> None:
        self.channel = channel
        self.wizard: Wizard | None = None
        self.ending = ""  # the end line a step set, in setup's fixed words (one that knows them)
        self.terminated = False  # SIGTERM or SIGHUP to setup itself
        self.denied: list[str] = []  # what asked outside the window (the guards): each one stops setup

    def say(self, text: str) -> None:
        self.channel.line(text)

    def stop(self, text: str, said: str = "") -> NoReturn:
        """Setup stops here in `text`, setup's own fixed words: the window's stop screen, and the AI's end line (`said`
        in its place when `text` holds an answer, such as a command the window names whole)."""
        self.ending = " ".join((said or text).split())
        raise SystemExit(text)

    def deny(self, what: str) -> NoReturn:
        """Something asked outside the window, which an AI-run never does: setup stops here, every answer kept."""
        self.denied.append(what)
        self.stop(prompts.SETUP_AI_DENIED.format(what=what))


class StopFace:
    """The face before the window opens: setup's lines go nowhere (the AI reads only the channel), and any question
    there stops setup (never an end of input that takes a recommendation)."""

    name = "stop"

    def __init__(self, ai: Run) -> None:
        self.ai = ai
        self.back = False
        self.previous: Value | None = None
        self.no_back = ""

    @property
    def asking(self) -> str:
        return self.name

    def say(self, text: str) -> None:
        return None

    def command(self, text: str) -> None:
        return None

    def _deny(self) -> None:
        self.ai.deny(prompts.SETUP_AI_DENIED_QUESTION)

    def line(self, prompt: str) -> str | None:
        self._deny()
        return None

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
        return None

    def show(self, q: Question, kept: Value | None) -> None:
        return None

    def choose(self, q: Question, kept: Value | None) -> Value | None:
        self._deny()
        return None

    def switches(self, q: Question, kept: Value | None) -> Value | None:
        self._deny()
        return None

    def confirm(self, q: Question, default: bool) -> bool | None:
        self._deny()
        return None

    def text(self, q: Question, kept: Value | None, message: str) -> str | None:
        self._deny()
        return None

    def fields(self, q: Question, kept: Value | None, message: str) -> dict[str, str] | None:
        self._deny()
        return None

    def hidden(self, q: Question, message: str) -> str | None:
        self._deny()
        return None

    def key(self, q: Question, message: str, groups: tuple[str, ...]) -> str | None:
        self._deny()
        return None

    def wait(self, q: Question, look: Callable[[], bool], seconds: float) -> bool:
        self._deny()
        return False

    def checked(self, q: Question, result: Checked) -> None:
        return None

    def done(self, lines: list[str]) -> None:
        return None

    def progress(self, text: str) -> None:
        return None

    def progress_done(self) -> None:
        return None

    def stopped(self, text: str) -> None:
        return None


def own_words(command: str) -> bool:
    """A command the AI may read: each word after sudo a path, a flag or a small word of setup's (`restart`,
    `mail-login`), never an answer the person typed (a mailbox's address), which the window alone shows."""
    return all(re.fullmatch(r"/[^\s@]*|--?[a-z][a-z-]*|[a-z][a-z0-9-]*", word) for word in command.split()[1:])


def where(wizard: Wizard | None) -> str:
    """ "at step 3 of 9 (her model)", or "before its first step"."""
    if wizard is not None and wizard.step_now in wizard.steps:
        n, m = wizard.steps.index(wizard.step_now) + 1, len(wizard.steps)
        return prompts.SETUP_AI_AT_STEP.format(n=n, m=m, step=prompts.SETUP_STEPS[wizard.step_now])
    return prompts.SETUP_AI_BEFORE_STEPS


def fixed(text: str) -> str:
    """The prompts' name of a stop's words when they are exactly one of its fixed sentences (nothing filled in), else
    "": only such words may reach the AI. No words name none (an empty prompt is a placeholder, never a stop)."""
    if not text:
        return ""
    for name in dir(prompts):
        value = getattr(prompts, name)
        if name.isupper() and isinstance(value, str) and value == text and "{" not in value:
            return name
    return ""


def why_line(ai: Run) -> str:
    """Where the reason is: the window, while it is open; before it opened nothing shows it."""
    return prompts.SETUP_AI_WINDOW_SAYS if _window_face(ai) is not None else prompts.SETUP_AI_NO_WINDOW_SAYS


def stop_line(ai: Run, words: str) -> str:
    """A7: where it stopped, and why only in the prompts' fixed words (else the window shows why)."""
    name = fixed(words)
    why = f"{' '.join(words.removeprefix('✗ ').split())} [{name}]" if name else why_line(ai)
    return prompts.SETUP_AI_STOPPED.format(where=where(ai.wizard), why=why)


def ended_line(ai: Run, code: int) -> str:
    """A run that ended with a code and no line of its own: where, the code, and where the reason is."""
    return prompts.SETUP_AI_ENDED.format(where=where(ai.wizard), code=code, why=why_line(ai))


def bug_line(error: BaseException) -> str:
    """A11: the exception's type and the innermost frame of local_assistant's own code (module.py:line), never its
    message (it may quote an answer) or an absolute path (the home folder names your account)."""
    found = ""
    for frame in traceback.extract_tb(error.__traceback__):
        if f"{os.sep}local_assistant{os.sep}" in frame.filename:
            found = f"{Path(frame.filename).name}:{frame.lineno}"
    return prompts.SETUP_AI_BUG.format(kind=type(error).__name__, where=found or "setup")


def _window_face(ai: Run) -> object | None:
    """The window face, while its window is open."""
    from local_assistant.setup_window import WindowFace

    face = ai.wizard.face if ai.wizard is not None else None
    if isinstance(face, WindowFace) and not face.closed and not face.window.gone:
        return face
    return None


def _stop_screen(ai: Run, text: str) -> None:
    """The window's stop screen, when it is open (setup's own words: the window is the person's)."""
    from local_assistant.setup_window import WindowFace

    face = _window_face(ai)
    if isinstance(face, WindowFace):
        with contextlib.suppress(Exception):
            face.stopped(text)


def finished(ai: Run) -> bool:
    """The run reached its last words: the window has its closing list, never a stop screen after it."""
    return ai.wizard is not None and ai.wizard.finished


def end_line(ai: Run, code: int) -> str:
    """The line for a run that returned: done (the check's count; its check not run when admin rights were not given;
    her doctor's own command once she moved), an early end's own line, or where it ended."""
    wizard = ai.wizard
    if finished(ai) and wizard is not None:
        left = len(getattr(wizard, "for_terminal", ()))
        if code == 0:
            line = prompts.SETUP_AI_DONE
        elif getattr(wizard, "check_not_run", False):
            line = prompts.SETUP_AI_DONE_NOT_RUN
        else:
            line = prompts.SETUP_AI_DONE_FAILS_HERS if wizard.own_user() else prompts.SETUP_AI_DONE_FAILS
        return line + (prompts.SETUP_AI_FOR_TERMINAL.format(k=left) if left else "")
    return ended_line(ai, code)


def run(ai: Run, body: Callable[[], int]) -> int:
    """The AI-run's one end point: its first line, then setup (its guards on), then exactly one end line and the
    code, whatever ended it; the window gets the matching stop screen while it is open."""
    from local_assistant.setup import Stopped

    ai.say(prompts.SETUP_AI_STARTED)
    code, line = STOPPED, ""
    try:
        with guarded(ai), terminations(ai):
            code = body()
        line = ai.ending or end_line(ai, code)
        code = code if code in (0, REFUSED) else STOPPED
        if code != 0 and not finished(ai):  # a step's own return: its ✗ line is in the window's log, no screen yet
            _stop_screen(ai, ai.ending or prompts.SETUP_AI_STEP_STOP)
    except KeyboardInterrupt:
        closed = _closed(ai)
        if closed:
            line = closed
            code = STOPPED
        elif ai.terminated:
            line, code = prompts.SETUP_AI_ENDED_OUTSIDE, STOPPED
            _stop_screen(ai, prompts.SETUP_WINDOW_ENDED_OUTSIDE)
        else:
            line, code = prompts.SETUP_AI_STOP_HERE, STOP_HERE
            _stop_screen(ai, prompts.SETUP_WINDOW_STOPPED_HERE)
    except Stopped as stop:  # the window has its stop screen already (run_setup)
        line, code = ai.ending or _closed(ai) or stop_line(ai, stop.words), STOPPED
    except SystemExit as stop:  # a step's own exit with a code (its words went through Stopped, run_setup)
        code = stop.code if isinstance(stop.code, int) else STOPPED
        line = ai.ending or _closed(ai) or ended_line(ai, code)
        code = code if code in (0, REFUSED) else STOPPED
        _stop_screen(ai, ai.ending or prompts.SETUP_AI_STEP_STOP)
    except Exception as error:
        line, code = bug_line(error), STOPPED
        _stop_screen(ai, prompts.SETUP_AI_BUG_STOP)
    finally:
        ai.channel.end(line or ended_line(ai, code), code)
    return code


def _closed(ai: Run) -> str:
    """The end line of a run whose window was closed: A8, or her start named when it closed between her stop and her
    start (harden's held section: a re-run leaves a stopped bridge stopped)."""
    from local_assistant.setup_window import WindowFace

    face = ai.wizard.face if ai.wizard is not None else None
    if not isinstance(face, WindowFace) or not face.closed:
        return ""
    if face.unstarted:
        return prompts.SETUP_AI_CLOSED_HELD.format(start=face.unstarted)
    return prompts.SETUP_AI_CLOSED


@contextlib.contextmanager
def terminations(ai: Run) -> Iterator[None]:
    """SIGTERM and SIGHUP to setup itself (a logout, a kill) stop it as Stop here does, every answer kept; a held
    section ignores them meanwhile (ownuser.interruptions_ignored). Off the main thread nothing changes."""

    def stop(signum: int, frame: object) -> None:
        ai.terminated = True
        raise KeyboardInterrupt

    saved: dict[signal.Signals, Any] = {}
    try:
        for number in (signal.SIGTERM, signal.SIGHUP):
            saved[number] = signal.signal(number, stop)
    except ValueError:
        pass
    try:
        yield
    finally:
        for number, handler in saved.items():
            signal.signal(number, handler)


@contextlib.contextmanager
def guarded(ai: Run) -> Iterator[None]:
    """Deny by default: every terminal reader setup has is one that stops setup (input, the hidden reads, by every
    name they are imported under); the typed-ahead peeks are no-ops (the word's wait runs them in every face); and
    sudo started by setup's own process (hostsec.Host.run, Wizard.run) is refused before it starts."""
    from local_assistant import harden, hostsec, ownuser, setup, setup_keycard, setup_signal

    def reader(what: str) -> Callable[..., str]:
        def deny(*args: object, **kwargs: object) -> str:
            ai.deny(what)
            return ""

        return deny

    def nothing(*args: object, **kwargs: object) -> None:
        return None

    def no_stop(*args: object, **kwargs: object) -> bool:
        return False

    def sudo(argv: Sequence[str]) -> None:
        from local_assistant.setup_admin import stays_yours

        if argv and argv[0] == hostsec.SUDO and not stays_yours(argv):
            ai.deny(prompts.SETUP_AI_DENIED_SUDO)

    swaps: list[tuple[object, str, object]] = [
        (builtins, "input", reader(prompts.SETUP_AI_DENIED_LINE)),
        (setup, "read_hidden", reader(prompts.SETUP_AI_DENIED_HIDDEN)),
        (setup, "read_hidden_lines", reader(prompts.SETUP_AI_DENIED_HIDDEN)),
        (hostsec, "read_hidden_lines", reader(prompts.SETUP_AI_DENIED_HIDDEN)),
        (ownuser, "read_hidden_lines", reader(prompts.SETUP_AI_DENIED_HIDDEN)),
        (setup, "flush_typed", nothing),
        (hostsec, "flush_typed", nothing),
        (setup_signal, "typed_stop", no_stop),
        (hostsec, "ask_tty", reader(prompts.SETUP_AI_DENIED_LINE)),
        (ownuser, "ask_tty", reader(prompts.SETUP_AI_DENIED_LINE)),
        (setup_keycard, "run_card", reader(prompts.SETUP_AI_DENIED_HIDDEN)),
        (harden, "terminal_ask", reader(prompts.SETUP_AI_DENIED_LINE)),
        (hostsec, "SUDO_GUARD", sudo),
    ]
    saved = [(owner, name, getattr(owner, name)) for owner, name, _ in swaps]
    for owner, name, value in swaps:
        setattr(owner, name, value)
    try:
        yield
    finally:
        for owner, name, value in saved:
            setattr(owner, name, value)

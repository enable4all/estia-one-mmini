"""Admin rights for setup's steps: today's sudo forms mapped to what root runs in the window (macOS's own box asks),
the window face running a step's commands (owner commands streamed into its log, admin ones through the window's
admin runner), and Terminal's path left as it was (sudo asks there)."""

import json
import os
import pwd
import re
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from local_assistant import hostsec, ownuser, prompts, setup_admin, setup_window
from local_assistant.hostsec import LAUNCHCTL, SUDO
from local_assistant.setup import Wizard
from local_assistant.setup_face import PlainFace
from local_assistant.setup_window import WindowFace
from tests.setup_helpers import noted

ROOT = Path(__file__).resolve().parents[1]
ESTIA = str(ownuser.BIN / "estia")
STOP = "<stop here>"  # a scripted window's Stop here, pressed on a screen instead of an answer


# ------------------------------------------------------------------ today's sudo forms, as root runs them


@pytest.mark.parametrize(
    ("argv", "root"),
    [
        ([SUDO, ESTIA, "restart"], [ESTIA, "restart"]),
        ([SUDO, "-n", "/bin/launchctl", "bootout", "system/x"], ["/bin/launchctl", "bootout", "system/x"]),
        ([SUDO, "-v"], ["/usr/bin/true"]),  # "authorize": the box when no 5 minutes run
        ([SUDO, "-n", "-l"], [SUDO, "-n", "-l", "-U", "alex"]),  # your rules and Defaults, listed by root
        (
            [SUDO, "-n", "-u", "_estia", "-H", "--", "/usr/bin/env", "-i", "/bin/echo"],
            [SUDO, "-n", "-u", "_estia", "-H", "--", "/usr/bin/env", "-i", "/bin/echo"],
        ),
        (
            [SUDO, "-u", "_estia", "-H", "--", "/usr/bin/env", "-i", "/bin/echo"],  # root needs no password for her
            [SUDO, "-n", "-u", "_estia", "-H", "--", "/usr/bin/env", "-i", "/bin/echo"],
        ),
        ([SUDO, "-n", "-v"], None),  # each command asks by itself when the 5 minutes ran out
        ([SUDO, "-k"], None),
    ],
)
def test_each_sudo_form_maps_to_what_root_runs(argv: list[str], root: list[str] | None) -> None:
    assert setup_admin.as_root(argv, "alex") == root


@pytest.mark.parametrize(
    "argv",
    [
        ["/bin/ls"],  # no sudo at all
        [SUDO],
        [SUDO, "-s"],  # a shell
        [SUDO, "-i"],
        [SUDO, "estia"],  # a program by a name sudo looks up in your PATH
        [SUDO, "-u", "root", "/bin/ls"],  # another account
        [SUDO, "-u", "_estia", "/bin/ls"],  # her, without -H --
        [SUDO, "-E", "/bin/ls"],  # your environment kept
        [SUDO, "-n", "-k", "-l"],  # stays yours: read-only, never admin (hostsec.owner_sudo)
    ],
)
def test_any_other_form_is_refused(argv: list[str]) -> None:
    with pytest.raises(ValueError, match="sudo"):
        setup_admin.as_root(argv, "alex")


def test_every_sudo_form_in_setups_code_is_one_the_window_maps() -> None:
    """Each list or tuple that starts with SUDO in src/ names a program by its absolute path, or takes one of the known
    flags, and one as her names -H -- after her: a new form fails here before it fails at a desk (review ADM-1: harden's
    tuple for her files had no -H --, and the window refused it with a traceback)."""
    known = {'"-n"', '"-v"', '"-k"', '"-l"', '"-u"'}
    found: set[str] = set()
    for path in (ROOT / "src/local_assistant").glob("*.py"):
        for match in re.finditer(r"[\[(]SUDO, ([^\])\n]+)", path.read_text(encoding="utf-8")):
            words = [word.strip() for word in match.group(1).split(",")]
            found.add(words[0])
            printed = words[2:3] == ["*self._defuse_argv(self._in_her_home(path"]  # root's rollback prints it
            if '"-u"' in words[:2] and not printed:
                at = words.index('"-u"')
                assert words[at + 2 : at + 4] == ['"-H"', '"--"'], f"{path.name}: {match.group(0)}"
    flags = {word for word in found if word.startswith('"-')}
    assert flags <= known, flags - known
    assert all(word.startswith(('"-', '"/', "str(", "*", "INSTALL", "TEE", "CHMOD", "CHOWN", "LAUNCHCTL", "hostsec.",
                                "ownuser.", "self.", "estia", "SOCKETFILTERFW", "DEPLOY", "RM", "SHASUM"))
               for word in found), found  # fmt: skip


def test_her_files_fix_is_a_form_the_window_maps() -> None:
    """Make her files private (harden.her_check): her chmod runs as her through the window, never a ValueError."""
    from local_assistant import harden

    for roots in (None, [".config/local-assistant"]):
        check = harden.her_check("data_modes", {"status": "fail", "text": "1 file", "roots": roots})
        assert check.apply
        for argv in check.apply:
            root = setup_admin.as_root(argv, "alex")
            assert root is not None and root[:6] == [SUDO, "-n", "-u", "_estia", "-H", "--"]


def test_a_form_the_window_does_not_run_goes_to_terminal_never_a_crash(tmp_path: Path) -> None:
    """A sudo form as_root refuses is no traceback: the window runs nothing, and Terminal's sudo does as it always did
    (review ADM-1)."""
    window = Scripted()
    _wizard, face = in_the_window(tmp_path, window)
    assert face.admin([SUDO, "-s"]) is None
    assert not window.sent("admin")


def test_a_line_root_printed_is_shown_without_its_colours_or_controls() -> None:
    assert setup_admin.shown("\x1b[1;32m✓ done\x1b[0m\x07 ‮evil") == "✓ done? ?evil"


# ------------------------------------------------------------------ the window face runs the step's commands


class Scripted:
    """A window that takes every line setup sends and answers from its script: an admin command with its lines and
    its result as `admin` gives them, each screen with `answers` in turn (a yes for a yes or no when they run out)."""

    def __init__(self, admin: list[dict[str, Any]] | None = None, answers: list[object] | None = None) -> None:
        self.messages: list[dict[str, object]] = []
        self.incoming: list[dict[str, object]] = []
        self.admin = list(admin or [])
        self.answers = list(answers or [])
        self.gone = False
        self.inputs: list[str] = []  # what setup typed into an admin command's stdin, answer by answer
        self.pending: tuple[str, dict[str, Any]] | None = None

    def send(self, message: dict[str, object]) -> bool:
        self.messages.append(dict(message))
        setup_window.encode(message)  # never a line the window would refuse
        if message["t"] == "admin":
            entry = self.admin.pop(0) if self.admin else {}
            if entry.get("gone"):
                self.gone = True
                return True
            for line in entry.get("lines", []):
                self.incoming.append({"t": "admin_line", "id": message["id"], "line": line})
            if entry.get("stop"):  # Stop here pressed while it runs
                self.incoming.append({"t": "stop"})
            if not message["open"] or entry.get("ends"):  # "ends": an open command that ends by itself
                self.done(str(message["id"]), entry)
            else:
                self.pending = (str(message["id"]), entry)
        elif message["t"] == "admin_input":  # the command read its answer and goes on to its end
            self.inputs.append(str(message["text"]))
            if self.pending is not None:
                self.done(*self.pending)
                self.pending = None
        elif message["t"] == "admin_end" and self.pending is not None:
            self.done(*self.pending)
            self.pending = None
        elif message["t"] == "screen":
            value = self.answers.pop(0) if self.answers else True
            if value == STOP:  # Stop here pressed on this screen
                self.incoming.append({"t": "stop"})
            else:
                self.incoming.append({"t": "answer", "id": message["id"], "value": value})
        elif message["t"] == "checked" and not message["ok"] and not message["live"] and self.answers:
            # refused: the screen stays, and the next answer is typed on it
            self.incoming.append({"t": "answer", "id": message["id"], "value": self.answers.pop(0)})
        return True

    def done(self, aid: str, entry: dict[str, Any]) -> None:
        for line in entry.get("after", []):
            self.incoming.append({"t": "admin_line", "id": aid, "line": line})
        done: dict[str, object] = {"t": "admin_done", "id": aid, "code": entry.get("code", 0),
                                   "refused": bool(entry.get("refuse"))}  # fmt: skip
        if entry.get("unrun"):  # macOS's box could not ask: nothing ran
            done["unrun"] = True
        self.incoming.append(done)

    def receive(self, seconds: float | None = None) -> dict[str, object] | None:
        if self.gone and not self.incoming:
            return None
        return self.incoming.pop(0) if self.incoming else None

    @property
    def ended(self) -> bool:
        return self.gone and not self.incoming

    def close(self) -> None:
        self.gone = True

    def sent(self, t: str) -> list[dict[str, Any]]:
        return [message for message in self.messages if message["t"] == t]


def in_the_window(tmp_path: Path, window: Scripted) -> tuple[Wizard, WindowFace]:
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    face = WindowFace(window, wizard, lambda: PlainFace(wizard.readers()))  # type: ignore[arg-type]
    wizard.face = face
    return wizard, face


@pytest.fixture
def no_subprocess_sudo(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[list[str]]]:
    """What setup started itself (never sudo in the window: the window's runner runs it)."""
    started: list[list[str]] = []
    real = subprocess.run

    def run(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        started.append(list(argv))
        assert argv[0] != SUDO, "setup started sudo itself"
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    yield started


def test_an_admin_command_runs_in_the_window_its_lines_in_terminal_and_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_subprocess_sudo: list[list[str]]
) -> None:
    window = Scripted(admin=[{"lines": ["✓ Restarted.", "\x1b[31mred\x1b[0m"], "code": 0}])
    wizard, _face = in_the_window(tmp_path, window)
    done = wizard.run([SUDO, ESTIA, "restart"], why="Admin rights to restart her.")
    assert done.returncode == 0 and no_subprocess_sudo == []
    [admin] = window.sent("admin")
    assert admin == {"t": "admin", "id": "a1", "argv": [ESTIA, "restart"], "input": None, "open": False,
                     "why": "Admin rights to restart her."}  # fmt: skip
    lines = [m["line"] for m in window.sent("progress")]
    assert f"  $ {SUDO} {ESTIA} restart" in lines and "✓ Restarted." in lines and "red" in lines
    assert prompts.SETUP_WINDOW_ADMIN_BOX in lines  # the window's own line, while macOS's box asks
    out = capsys.readouterr().out
    assert "✓ Restarted." in out and prompts.SETUP_WINDOW_ADMIN_BOX not in out  # Terminal gets the command's lines
    assert not window.sent("terminal")  # nothing goes on in Terminal


def test_a_secret_goes_only_on_the_commands_stdin(tmp_path: Path, no_subprocess_sudo: list[list[str]]) -> None:
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    secret = "made-up-" + "secret-of-the-test"  # never a real key
    command = [SUDO, ESTIA, "secret", "set", "web.env", "OLLAMA_API_KEY"]
    assert wizard.run(command, input=f"{secret}\n").returncode == 0
    [admin] = window.sent("admin")
    assert admin["input"] == f"{secret}\n" and secret not in str(admin["argv"]) and secret not in str(admin["why"])
    assert not any(secret in str(m) for m in window.messages if m["t"] != "admin")


def test_captured_output_comes_back_and_is_not_said(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    window = Scripted(admin=[{"lines": ["(ALL) ALL"], "code": 0}])
    _wizard, face = in_the_window(tmp_path, window)
    ran = face.admin([SUDO, "-n", "-l"], capture=True, why="Why.")
    assert ran == setup_admin.Ran(0, "(ALL) ALL")
    assert window.sent("admin")[0]["argv"][-2:] == ["-U", pwd.getpwuid(os.getuid()).pw_name]
    assert "(ALL) ALL" not in capsys.readouterr().out


def test_captured_output_comes_back_as_the_command_printed_it(tmp_path: Path) -> None:
    """Output setup reads back (a sudoers file through `cat`, her checks' JSON) is the command's own text, tabs
    included, as Terminal's Host.run gives it; only what is said is cleaned (review ADM-6: a tab became "?", and the
    owner's own rule read as another account's)."""
    window = Scripted(admin=[{"lines": ["alex\tALL=(ALL) NOPASSWD: ALL"], "code": 0}])
    _wizard, face = in_the_window(tmp_path, window)
    ran = face.admin([SUDO, "-n", "/bin/cat", "/etc/sudoers.d/zz"], capture=True)
    assert ran == setup_admin.Ran(0, "alex\tALL=(ALL) NOPASSWD: ALL")
    window = Scripted(admin=[{"lines": ["\x1b[31mred\x1b[0m\tand more"], "code": 0}])
    _wizard, face = in_the_window(tmp_path, window)
    face.admin([SUDO, ESTIA, "restart"])
    assert "red?and more" in [m["line"] for m in window.sent("progress")]  # said: cleaned


def test_the_box_refused_asks_try_again_or_not_now(tmp_path: Path) -> None:
    """Admin rights not given: the window says so, with Try again (recommended) and Not now; Try again sends the same
    command again, Not now takes the step's own path for admin rights not given."""
    window = Scripted(admin=[{"refuse": True}, {"code": 0}], answers=[True])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.run([SUDO, ESTIA, "restart"]).returncode == 0
    assert [m["argv"] for m in window.sent("admin")] == [[ESTIA, "restart"]] * 2
    [screen] = window.sent("screen")
    assert screen["id"] == "admin_refused" and screen["title"] == prompts.SETUP_WINDOW_ADMIN_REFUSED_TITLE
    assert f"$ {SUDO} {ESTIA} restart" in str(screen["lead"])
    assert [o["label"] for o in screen["options"]] == [prompts.SETUP_WINDOW_TRY_AGAIN, prompts.SETUP_WINDOW_NOT_NOW]
    window = Scripted(admin=[{"refuse": True}], answers=[False])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.run([SUDO, ESTIA, "restart"]).returncode != 0
    assert len(window.sent("admin")) == 1


def test_sudo_v_is_the_box_and_sudo_n_v_asks_nothing(tmp_path: Path) -> None:
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.run([SUDO, "-n", "-v"]).returncode == 0
    assert window.sent("admin") == []
    assert wizard.run([SUDO, "-v"]).returncode == 0
    assert window.sent("admin")[0]["argv"] == ["/usr/bin/true"]


def test_once_the_window_closed_sudo_n_v_and_sudo_k_run_in_terminal(tmp_path: Path) -> None:
    """The window gone, `sudo -n -v` is no longer the window's "nothing to do": Terminal runs it, so a step that
    asks whether admin rights still run (harden's sudo_again) learns that sudo asks there (review ADM-2)."""
    window = Scripted()
    _wizard, face = in_the_window(tmp_path, window)
    face.terminal_face = PlainFace(_wizard.readers())
    assert face.admin([SUDO, "-n", "-v"]) is None and face.admin([SUDO, "-k"]) is None
    assert not window.sent("admin")


def test_a_box_that_cannot_ask_hands_setup_to_terminal_where_sudo_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS's box could not ask (an AppleScript error, a script that did not run): nothing ran, and setup goes on in
    Terminal, where today's sudo runs the same command, never a step failed as if its command had (review ADM-4)."""
    ran: list[list[str]] = []

    def run(argv: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        ran.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    window = Scripted(admin=[{"code": 127, "unrun": True}])
    wizard, face = in_the_window(tmp_path, window)
    monkeypatch.setattr(subprocess, "run", run)
    assert wizard.run([SUDO, ESTIA, "restart"]).returncode == 0
    assert ran == [[SUDO, ESTIA, "restart"]] and face.terminal_face is not None
    [terminal] = window.sent("terminal")
    assert terminal["why"] == prompts.SETUP_WINDOW_NO_BOX


def test_a_step_in_process_gives_its_time_limit_to_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The security step's commands keep Host.run's time limits in the window (the PIN helper's 180 s, 600 s by
    default): the window ends a command over it, as Terminal's Host.run does (review ADM-3); setup's own steps give
    none (a move or an update may take long)."""
    window = Scripted(admin=[{"code": 0}, {"code": 0}, {"code": 0}])
    wizard, face = in_the_window(tmp_path, window)
    host = setup_admin.WindowHost(face, Path("/"), why=prompts.ADMIN_WHY_SECURITY)
    assert host.run([SUDO, "-n", "/bin/launchctl", "bootout", "system/x"])[0] == 0
    assert host.run([SUDO, "-n", "-u", "_estia", "-H", "--", "/usr/bin/true"], input="1\n", timeout=180)[0] == 0
    wizard.run([SUDO, ESTIA, "restart"])
    assert [m.get("seconds") for m in window.sent("admin")] == [600, 180, None]


def test_a_piped_one_shot_command_gets_a_time_limit(tmp_path: Path) -> None:
    """Review W08: a window that dies after macOS's box, before it opens the command's input, left root's command
    (the Ollama plist's tee) waiting for a writer until the Mac restarts, where any program of yours could feed it.
    A one-shot command with input gets Host.run's limit (TERM, then root removes its folder); a command whose
    question is a screen (estia-deploy --ask-lines) keeps none: the friend may take long."""
    window = Scripted(admin=[{"code": 0}, {"code": 0}, {"lines": [REVIEW_ASK]}])
    _wizard, face = in_the_window(tmp_path, window)
    plist = [SUDO, "/usr/bin/tee", "/Library/LaunchDaemons/com.local-assistant.ollama.plist"]
    assert face.run(plist, input="<plist/>", capture=True, cwd=None, why="") is not None
    assert face.admin([SUDO, ESTIA, "secret", "set", "web.env", "KEY"], input="made-up\n") == setup_admin.Ran(0, "")
    face.admin([SUDO, DEPLOY, "--ask-lines"], answer=lambda question, before: "n\n")
    assert [m.get("seconds") for m in window.sent("admin")] == [600, 600, None]


def test_the_window_gone_while_root_ran_it_stops_setup(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Round 2 (r2-window-stream-10): Terminal said "The window closed: setup goes on here, in Terminal." and on the
    next line stopped; it says the stop alone."""
    window = Scripted(admin=[{"gone": True}])
    wizard, _face = in_the_window(tmp_path, window)
    with pytest.raises(SystemExit) as stopped:
        wizard.run([SUDO, ESTIA, "restart"])
    assert stopped.value.code == prompts.SETUP_ADMIN_UNKNOWN
    assert prompts.SETUP_WINDOW_CLOSED not in capsys.readouterr().out


def test_the_hand_over_words_say_terminal_asks_for_the_mac_password_never_sudo() -> None:
    """Round 2 (r2-window-admin-not-now-and-later-words, words-20): "sudo asks there" is jargon, and "Later, in
    Terminal:" with Copy invited a paste into the friend's AI chat; the command runs in the friend's own Terminal
    window, which asks for the Mac password there."""
    for text in (prompts.SETUP_WINDOW_NO_BOX, prompts.SETUP_WINDOW_ADMIN_LATER):
        assert "sudo asks" not in text and "your Mac password there" in text, text
    assert "in your own Terminal window" in prompts.SETUP_WINDOW_ADMIN_LATER


def test_stop_during_an_admin_command_waits_for_it_then_stops(tmp_path: Path) -> None:
    window = Scripted(admin=[{"lines": ["working"], "code": 0, "stop": True}])
    wizard, _face = in_the_window(tmp_path, window)
    with pytest.raises(KeyboardInterrupt):
        wizard.run([SUDO, ESTIA, "restart"])
    assert any(m["t"] == "progress" and m["line"] == prompts.SETUP_WINDOW_FINISHES_FIRST for m in window.messages)
    assert window.incoming == []  # its result read first


def test_a_stop_while_stops_are_held_waits_for_the_whole_sequence(tmp_path: Path) -> None:
    """Her stop, her PIN and her start (harden's held section): Stop here during the first command never raises there;
    the next ones run, and setup stops once the section ends, its line said once (review FRIEND-5)."""
    window = Scripted(admin=[{"code": 0, "stop": True}, {"code": 0}])
    _wizard, face = in_the_window(tmp_path, window)
    with pytest.raises(KeyboardInterrupt), face.stops_held(prompts.SETUP_WINDOW_LOCK_FINISHES):
        assert face.admin([SUDO, "-n", "/bin/launchctl", "bootout", "system/x"]) == setup_admin.Ran(0, "")
        assert face.admin([SUDO, "-n", "/bin/launchctl", "bootstrap", "system", "/x"]) == setup_admin.Ran(0, "")
    assert len(window.sent("admin")) == 2
    lines = [m["line"] for m in window.sent("progress")]
    assert lines.count(prompts.SETUP_WINDOW_LOCK_FINISHES) == 1 and prompts.SETUP_WINDOW_FINISHES_FIRST not in lines


def test_a_box_refused_after_a_held_stop_draws_no_screen_and_stops_at_the_end(tmp_path: Path) -> None:
    """Fix review (setup-code-01): Stop here inside harden's held section, then macOS's box cancelled: setup sent the
    "Admin rights: not given" screen, which the page drops after "Stopped" (review W21), and waited on a screen never
    drawn. A refusal after a held stop is Not now without a screen; setup stops at the section's end."""
    window = Scripted(admin=[{"code": 0, "stop": True}, {"refuse": True}])
    _wizard, face = in_the_window(tmp_path, window)
    with pytest.raises(KeyboardInterrupt), face.stops_held(prompts.SETUP_WINDOW_LOCK_FINISHES):
        assert face.admin([SUDO, "-n", "/bin/launchctl", "bootout", "system/x"]) == setup_admin.Ran(0, "")
        refused = face.admin([SUDO, "/bin/launchctl", "bootstrap", "system", "/x"])
        assert refused == setup_admin.Ran(1, prompts.SETUP_WINDOW_ADMIN_REFUSED_TITLE, refused=True)
    assert window.sent("screen") == [] and len(window.sent("admin")) == 2


def test_the_security_steps_held_section_holds_the_windows_stops(tmp_path: Path) -> None:
    """harden's held section in the window: hang-ups, TERMs and Ctrl-Cs ignored, as harden.sh does, and the window's
    Stop here waits for its end too (WindowHost.stops_held)."""
    from local_assistant import harden

    made: list[harden.Harden] = []
    original = harden.Harden.run
    try:
        harden.Harden.run = lambda self: noted(made, self, 0)  # type: ignore[method-assign]
        wizard, face = in_the_window(tmp_path, Scripted())
        wizard.planned["security"] = True
        wizard.security()
    finally:
        harden.Harden.run = original  # type: ignore[method-assign]
    [step] = made
    assert isinstance(step.host, setup_admin.WindowHost) and step.held == step.host.stops_held
    window = face.window
    assert isinstance(window, Scripted)
    window.admin = [{"code": 0}]
    window.incoming.append({"t": "stop"})
    with pytest.raises(KeyboardInterrupt), step.held():
        assert face.admin([SUDO, "-n", "/bin/launchctl", "bootout", "system/x"]) == setup_admin.Ran(0, "")


def test_a_stop_pressed_while_setup_worked_is_honoured_before_the_next_command(tmp_path: Path) -> None:
    """Stop here during work setup reads nothing from (a download, a check): the next admin command or owner command
    never starts, and setup stops (review FRIEND-4: macOS's box showed after Stop)."""
    runs: tuple[Callable[[Wizard], object], ...] = (
        lambda wizard: wizard.run([SUDO, ESTIA, "restart"]),
        lambda wizard: wizard.run(["/bin/echo", "after the stop"]),
    )
    for run in runs:
        window = Scripted()
        window.incoming.append({"t": "stop"})
        wizard, _face = in_the_window(tmp_path, window)
        with pytest.raises(KeyboardInterrupt):
            run(wizard)
        assert not window.sent("admin") and not window.sent("screen")
        assert "after the stop" not in [m["line"] for m in window.sent("progress")]


def test_a_downloads_dots_read_the_windows_stop(tmp_path: Path) -> None:
    """Her model's pull and Ollama's download are setup's own work: their dots (and each look without a new one)
    read the window, so Stop here ends the download at once (review FRIEND-4)."""
    from local_assistant import setup_progress

    for between in (setup_progress.Watch.start, setup_progress.Watch.waited):
        window = Scripted()
        _wizard, face = in_the_window(tmp_path, window)
        watch = setup_progress.Watch(face, "her model")
        window.incoming.append({"t": "stop"})
        with pytest.raises(KeyboardInterrupt):
            between(watch)


def test_a_stop_then_close_is_a_stop(tmp_path: Path) -> None:
    """Stop here, then Close before setup read it: setup stops, never going on in Terminal (review FRIEND-4)."""
    window = Scripted()
    window.incoming += [{"t": "stop"}, {"t": "closed"}]
    wizard, face = in_the_window(tmp_path, window)
    with pytest.raises(KeyboardInterrupt):
        wizard.run([SUDO, ESTIA, "restart"])
    assert face.terminal_face is None and not window.sent("terminal")


def test_an_owner_command_streams_into_the_window(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    script = "printf 'one\\n'; printf '10%%\\r20%%\\r30%%\\n'; printf 'last'; exit 4"
    done = wizard.run(["/bin/sh", "-c", script])
    assert done.returncode == 4
    lines = [m["line"] for m in window.sent("progress")]
    assert lines[-3:] == ["one", "30%", "last"]
    assert "one\n" in capsys.readouterr().out
    assert not window.sent("terminal") and not window.sent("admin")


def test_an_owner_commands_input_goes_on_its_stdin(tmp_path: Path) -> None:
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.run(["/bin/cat"], input="from stdin\n").returncode == 0
    assert window.sent("progress")[-1]["line"] == "from stdin"


def test_an_owner_command_reads_no_terminal_in_the_window(tmp_path: Path) -> None:
    """Its stdin is never the terminal: a command that would ask there gets the end of its input."""
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.run(["/bin/sh", "-c", "read answer || echo no-input"]).returncode == 0
    assert window.sent("progress")[-1]["line"] == "no-input"


def test_stop_during_an_owner_command_interrupts_it(tmp_path: Path) -> None:
    window = Scripted()
    window.incoming.append({"t": "stop"})
    wizard, _face = in_the_window(tmp_path, window)
    with pytest.raises(KeyboardInterrupt):
        wizard.run(["/bin/sleep", "30"])


class StopsAt(Scripted):
    """A window whose Stop here is pressed once a line shows in its log."""

    def __init__(self, line: str) -> None:
        super().__init__()
        self.line = line

    def send(self, message: dict[str, object]) -> bool:
        if message["t"] == "progress" and message["line"] == self.line:
            self.incoming.append({"t": "stop"})
        return super().send(message)


class HangsUpAt(Scripted):
    """Terminal closed (setup's hang-up handler raises KeyboardInterrupt) once a line shows in the window's log."""

    def __init__(self, line: str) -> None:
        super().__init__()
        self.line = line

    def send(self, message: dict[str, object]) -> bool:
        sent = super().send(message)
        if message["t"] == "progress" and message["line"] == self.line:
            raise KeyboardInterrupt
        return sent


def test_terminal_closed_during_a_streamed_command_ends_it_too(tmp_path: Path) -> None:
    """Round 2 (r2-window-stream-02): a streamed command runs in a session of its own, so Terminal's hang-up never
    reached it: setup ended and brew went on, holding its lock for the re-run. Setup leaving ends its command."""
    pid_file = tmp_path / "pid"
    window = HangsUpAt("started")
    wizard, _face = in_the_window(tmp_path, window)
    with pytest.raises(KeyboardInterrupt):
        wizard.run(["/bin/bash", "-c", f"echo $$ > {pid_file}; echo started; exec /bin/sleep 30"])
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, signal.SIGKILL)
        pytest.fail("the streamed command outlived setup")


def test_stop_during_a_script_interrupts_the_programs_it_runs_too(tmp_path: Path) -> None:
    """Stop here reaches the command's whole process group, as Ctrl-C reaches Terminal's: a script (build.sh, cargo)
    stops with its program, never only once that program ended (review FRIEND-9)."""
    window = StopsAt("started")
    wizard, _face = in_the_window(tmp_path, window)
    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        wizard.run(["/bin/bash", "-c", "echo started; /bin/sleep 5; echo after"])
    assert time.monotonic() - began < 4
    assert "after" not in [m["line"] for m in window.sent("progress")]


def test_a_ctrl_c_in_terminal_reaches_a_scripts_programs_in_the_window(tmp_path: Path) -> None:
    """The command runs in its own process group (so Stop here reaches it whole): a Ctrl-C in Terminal, noted by
    setup, is passed on to that group, and setup stops after it (review FRIEND-9)."""

    class CtrlC(Scripted):
        def send(self, message: dict[str, object]) -> bool:
            if message["t"] == "progress" and message["line"] == "started":
                os.kill(os.getpid(), signal.SIGINT)  # what the terminal sends to setup
            return super().send(message)

    window = CtrlC()
    wizard, _face = in_the_window(tmp_path, window)
    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        wizard.run(["/bin/bash", "-c", "echo started; /bin/sleep 5; echo after"])
    assert time.monotonic() - began < 4
    assert "after" not in [m["line"] for m in window.sent("progress")]


def test_a_command_whose_output_setup_reads_runs_as_it_always_did(tmp_path: Path) -> None:
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    done = wizard.run(["/bin/echo", "hello"], capture_output=True)
    assert done.stdout == "hello\n" and not window.sent("admin")


def test_an_offer_is_a_screen_in_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner, 29 Oct: no "Press Enter" in Terminal while the window is open: the command with Copy, Run it
    (recommended) or Skip; an admin one says that macOS asks in its own box."""
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("Terminal asked"))
    window = Scripted(answers=[True, False])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.offer([SUDO, ESTIA, "restart"]) is True
    assert wizard.offer(["/bin/echo", "skipped"]) is False
    first, second = window.sent("screen")
    assert first["id"] == second["id"] == "offer_run" and first["title"] == prompts.SETUP_WINDOW_OFFER_TITLE
    assert first["lead"] == f"$ {SUDO} {ESTIA} restart\n\n{prompts.SETUP_WINDOW_ADMIN_BOX}"
    assert second["lead"] == "$ /bin/echo skipped" and first["recommended"] == "yes"
    labels = [(o["id"], o["label"], o["tag"]) for o in first["options"]]
    assert labels == [("yes", prompts.SETUP_WINDOW_RUN_IT, "Recommended"), ("no", prompts.SETUP_WINDOW_SKIP, "")]
    assert [m["argv"] for m in window.sent("admin")] == [[ESTIA, "restart"]]
    assert not any(m["t"] == "progress" and m["line"] == "skipped" for m in window.messages)


def test_her_restarts_screen_and_her_updates_screen_say_what_they_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 2 (words-09, words-10): her restart's screen showed only "$ /usr/bin/sudo …/estia restart" (its reason
    was one log line before it), and her update's screen had no lead while its question said "Read the list above
    first" (the list is under More). Each screen says what it does."""
    window = Scripted(admin=[{"code": 0}], answers=[True])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.restart_due = True
    wizard.restart_her()
    [offer] = window.sent("screen")
    assert str(offer["lead"]).startswith(f"{prompts.SETUP_WINDOW_HER_RESTART}\n\n$ {SUDO} {ESTIA} restart")
    window = Scripted(admin=[{"lines": [REVIEW_ASK], "after": ["Nothing deployed."], "code": 1}], answers=[False])
    wizard, _face = in_the_window(tmp_path, window)
    wizard._deploy_in_window([SUDO, DEPLOY, "--ask-lines"])
    [review] = window.sent("screen")
    assert review["lead"] == prompts.SETUP_WINDOW_DEPLOY_LEAD and "More" in prompts.SETUP_WINDOW_DEPLOY_LEAD


def test_a_dry_run_sends_no_admin_command(tmp_path: Path) -> None:
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    wizard.dry_run = True
    wizard.run([SUDO, ESTIA, "restart"])
    wizard.offer([SUDO, ESTIA, "restart"])
    assert not window.sent("admin") and not window.sent("screen")


def test_once_the_window_closed_terminal_runs_it_as_it_always_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[list[str]] = []

    def run(argv: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        ran.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    window = Scripted()
    window.gone = True
    wizard, face = in_the_window(tmp_path, window)
    face.terminal_face = PlainFace(wizard.readers())
    monkeypatch.setattr(subprocess, "run", run)
    assert wizard.run([SUDO, ESTIA, "restart"]).returncode == 0
    assert ran == [[SUDO, ESTIA, "restart"]] and not window.sent("admin")


def test_terminals_faces_run_sudo_as_they_always_did(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []

    def run(argv: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        ran.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    monkeypatch.setattr(subprocess, "run", run)
    assert wizard.run([SUDO, ESTIA, "restart"]).returncode == 0
    assert ran == [[SUDO, ESTIA, "restart"]]


def test_no_face_hands_a_step_to_terminal_any_more() -> None:
    """The per-step hand-over is gone (owner, 29 Oct): no face says a step goes on in Terminal, and no command line
    sends the window there; the hand-over stays only for a window that closed, broke or cannot open."""
    for face in (PlainFace, WindowFace):
        assert not hasattr(face, "terminal")
    source = (ROOT / "src/local_assistant/setup_window.py").read_text(encoding="utf-8")
    assert "SETUP_WINDOW_RUNS" not in source and "SETUP_WINDOW_TYPED" not in source
    for name in ("SETUP_WINDOW_RUNS", "SETUP_WINDOW_TYPED", "SETUP_WINDOW_OFFER"):
        assert not hasattr(prompts, name), name


Run = Callable[..., subprocess.CompletedProcess[str]]


# ------------------------------------------------------------------ the Ollama step (O1-O7) and the model's (M1-M3)


def test_ollamas_changes_go_through_the_window_its_plist_on_stdin(tmp_path: Path) -> None:
    """Its service: tee's plist as one-shot input, launchctl and the rest through the window's admin runner (macOS's
    box asks once for the step); its own commands (tar, open -a) streamed into the log; never sudo in Terminal."""
    from local_assistant import ollama_build

    window = Scripted(admin=[{"code": 0}, {"code": 0}])
    wizard, _face = in_the_window(tmp_path, window)
    tools = ollama_build.Tools(Path("/"), say=wizard.say, change=wizard.window_change(prompts.ADMIN_WHY_OLLAMA))
    plist = b"<plist>service</plist>\n"
    assert tools.change([SUDO, ollama_build.TEE, str(ollama_build.DAEMON)], input=plist) == 0
    assert tools.change([SUDO, LAUNCHCTL, "bootstrap", "system", str(ollama_build.DAEMON)]) == 0
    first, second = window.sent("admin")
    assert first["argv"] == [ollama_build.TEE, str(ollama_build.DAEMON)] and first["input"] == plist.decode()
    assert second["argv"][:2] == [LAUNCHCTL, "bootstrap"] and second["input"] is None
    assert first["why"] == second["why"] == prompts.ADMIN_WHY_OLLAMA
    assert tools.change(["/bin/echo", "unpacked"]) == 0  # one of yours: in the log
    assert window.sent("progress")[-1]["line"] == "unpacked"


def test_admin_rights_not_given_at_ollamas_step_are_said_as_that(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 2 (words-06, r2-ai-run-r1-12): Cancel at macOS's box, then Not now, at Ollama's step stopped with "the lines
    above say why" and a launchctl command, or, at her account, with "--redo move and answer No". It is a stop of its
    own: admin rights were not given, run setup again; no sudo command, no No to the move."""
    from local_assistant import ollama_build

    made: list[ollama_build.Tools] = []
    real = ollama_build.Tools.__init__

    def tools_made(self: ollama_build.Tools, *args: Any, **kwargs: Any) -> None:
        real(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(ollama_build.Tools, "__init__", tools_made)
    monkeypatch.setattr(ollama_build.Install, "run", lambda self, num_ctx: "/x/ollama")
    window = Scripted(admin=[{"refuse": True}], answers=[False])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.ollama()
    [tools] = made
    tools.root = Path("/")  # the live Mac's: the change goes to the window
    with pytest.raises(SystemExit) as stop:
        tools.change([SUDO, ollama_build.TEE, str(ollama_build.DAEMON)], input=b"<plist/>\n")
    assert stop.value.code == prompts.OLLAMA_ADMIN_REFUSED
    for word in ("lines above", "launchctl", "--redo move", "sudo"):
        assert word not in prompts.OLLAMA_ADMIN_REFUSED, word
    assert not prompts.OLLAMA_HER_ACCOUNT_FIX.startswith("run scripts/setup.sh --redo move")  # the move's No: last


PASSWORD_WORDS = ("Mac password", "nothing shows while you type")


@pytest.mark.parametrize("layout", ["hers", "older"])
def test_the_windows_log_never_names_a_password_typed_where_nothing_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    """Round 2 (words-03, r2-window-admin-terminal-password-words, r2-window-stream-09, ux-14): the window's log said
    "Your Mac password next, if asked … (nothing shows while you type)" at Ollama's service and again at her check,
    right above "Admin rights: asked by macOS in its own box, never in this window." In the window only macOS's box
    line is said; Terminal keeps its password lines."""
    from local_assistant import ollama_build

    for changer, words in ((lambda argv, input: 0, ()), (None, PASSWORD_WORDS)):
        said: list[str] = []
        tools = ollama_build.Tools(tmp_path, say=said.append, change=changer)
        step = ollama_build.Install(tools, tmp_path / "home", dry_run=True, say=said.append, layout=layout)  # type: ignore[arg-type]
        step.daemon(8192)
        text = "\n".join(said)
        assert all(word in text for word in words) and "Its file:" in text
        if changer is not None:
            assert not any(word in text for word in PASSWORD_WORDS), text
    window = Scripted(admin=[{"code": 0}])
    wizard, _face = in_the_window(tmp_path, window)
    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    wizard.check()
    lines = "\n".join(str(m["line"]) for m in window.sent("progress"))
    assert "Her doctor runs as her own macOS account" in lines and prompts.SETUP_WINDOW_ADMIN_BOX in lines
    assert not any(word in lines for word in PASSWORD_WORDS), lines


def test_the_ollama_step_in_terminal_changes_as_it_always_did(tmp_path: Path) -> None:
    from local_assistant import ollama_build

    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    assert wizard.window_change(prompts.ADMIN_WHY_OLLAMA) is None  # Tools runs sudo in the terminal
    tools = ollama_build.Tools(tmp_path, say=lambda text: None)
    assert tools.change([SUDO, "/bin/launchctl", "bootout", "system/x"]) == 0  # off the live Mac: only shown


def test_the_ollama_step_gets_the_windows_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from local_assistant import ollama_build

    made: list[ollama_build.Tools] = []
    real = ollama_build.Tools.__init__

    def tools(self: ollama_build.Tools, *args: Any, **kwargs: Any) -> None:
        real(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(ollama_build.Tools, "__init__", tools)
    monkeypatch.setattr(ollama_build.Install, "run", lambda self, num_ctx: "/x/ollama")
    wizard, _face = in_the_window(tmp_path, Scripted())
    wizard.ollama()
    assert made and made[0].changer is not None


ACCOUNT_NOT_MADE = (
    "✗ macOS made no account _estia: sysadminctl -addUser ended without an error, but the account is not there "
    "(number 250; macOS 27.1; tested on macOS 27 only). sysadminctl said: (nothing)"
)


def test_a_failed_change_with_input_keeps_its_words_for_its_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review W23: in the window a change with input runs captured (its output not said), so the ✗ line of her
    account's command (sysadminctl's words, the macOS version) was lost; the Ollama step's tools keep a failed one's
    words, never a good one's."""
    from local_assistant import ollama_build

    made: list[ollama_build.Tools] = []
    real = ollama_build.Tools.__init__

    def tools(self: ollama_build.Tools, *args: Any, **kwargs: Any) -> None:
        real(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(ollama_build.Tools, "__init__", tools)
    monkeypatch.setattr(ollama_build.Install, "run", lambda self, num_ctx: "/x/ollama")
    window = Scripted(admin=[{"lines": ["ok"]}, {"lines": ["made the group", ACCOUNT_NOT_MADE], "code": 1}])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.ollama()
    [made_tools] = made
    assert made_tools.changer is not None
    assert made_tools.changer([SUDO, "/usr/bin/tee", "/x.plist"], b"<plist/>") == 0
    assert made_tools.failed_output == ""  # a good change's words are never kept
    account = [SUDO, "/x/.venv/bin/python", "-I", "-m", "local_assistant.ownuser", "account"]
    assert made_tools.changer(account, b"") == 1
    assert made_tools.failed_output == f"made the group\n{ACCOUNT_NOT_MADE}"
    live = ollama_build.Tools(Path("/"), say=lambda line: None, change=lambda argv, input: 0)
    live.failed_output = "an earlier change's words"
    assert live.change([SUDO, "/usr/bin/true"], input=b"") == 0 and live.failed_output == ""  # each starts afresh


def test_her_stop_and_start_around_a_remade_model_go_through_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2: in her own account the remake stops her first and starts her again, each offered on its screen and run
    with admin rights from macOS's own box."""
    window = Scripted(answers=[True, True])
    wizard, _face = in_the_window(tmp_path, window)
    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    monkeypatch.setattr(Wizard, "alias_base", lambda self, alias, shown, listed: setup_choice())
    monkeypatch.setattr(Wizard, "answer", lambda self, qid, **words: True)
    monkeypatch.setattr(Wizard, "_remade", lambda self, base, shown, hers: None)
    wizard.remake({}, {}, [])
    assert [m["argv"] for m in window.sent("admin")] == [[ESTIA, "stop"], [ESTIA, "start"]]
    assert {m["why"] for m in window.sent("admin")} == {prompts.ADMIN_WHY_HER}


def setup_choice() -> Any:
    from local_assistant.setup import choose_model

    return choose_model(32)


# ------------------------------------------------------------------ Signal's own "try again" (S1, S2)


@pytest.mark.parametrize(
    ("qid", "terminal", "yes"),
    [("lock_retry", prompts.SETUP_LOCK_RETRY, True), ("lock_retry", prompts.SETUP_LOCK_RETRY, False),
     ("login_again", prompts.SETUP_LOGIN_AGAIN_LATER, True)],
)  # fmt: skip
def test_signals_try_again_is_a_screen_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qid: str, terminal: str, yes: bool
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("Terminal asked"))
    window = Scripted(answers=[yes])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.again(terminal, qid=qid) is yes
    [screen] = window.sent("screen")
    # Signal's limit: Stop here is recommended, as each try before its time is one more registration (c-13, confirm-2)
    assert screen["id"] == qid and screen["kind"] == "yes_no"
    assert screen["recommended"] == ("no" if qid == "login_again" else "yes")
    assert next(o["label"] for o in screen["options"]) == prompts.SETUP_WINDOW_TRY_AGAIN


def test_signals_limit_in_the_window_names_its_time_and_recommends_stop_here(tmp_path: Path) -> None:
    """c-13 (confirm-2): the screen after Signal's limit named no time (only the log did) and recommended Try again,
    one more registration each time. It names the time Signal gave, in local time, and recommends Stop here; without
    a time, that Signal named none (review S09)."""
    from local_assistant.setup_signal import limit_line

    for wait in ("10:40 on 25 Oct (local time)", ""):
        window = Scripted(answers=[False])
        wizard, _face = in_the_window(tmp_path, window)
        assert wizard.again(prompts.SETUP_LOGIN_AGAIN_LATER, qid="login_again", limit=limit_line(wait)) is False
        [screen] = window.sent("screen")
        lead = str(screen["lead"])
        assert screen["recommended"] == "no" and "{" not in lead
        assert (f"until {wait}, then try again" if wait else prompts.SETUP_WINDOW_LOGIN_NO_TIME) in lead
        stop = [o for o in screen["options"] if o["id"] == "no"]
        assert stop[0]["label"] == prompts.SETUP_WINDOW_STOP_HERE and stop[0]["tag"] == "Recommended"


def test_signals_try_again_in_terminal_is_as_it_always_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": noted(asked, prompt, "q"))
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    assert wizard.again(prompts.SETUP_LOCK_RETRY, qid="lock_retry") is False
    assert asked == [f"{prompts.SETUP_LOCK_RETRY} "]


# ------------------------------------------------------------------ the move (A1-A3)


def test_the_move_runs_as_root_through_the_window_then_the_share_asks_on_its_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A1: the move itself as root (the checkout's own Python, as scripts/migrate-to-own-user.sh runs it), its lines
    in the window's log; A2: the projects share as you, in-process, its close question a window screen."""
    from local_assistant import setup, share

    shared: list[dict[str, Any]] = []

    def share_main(argv: list[str], *, ask: Callable[[str], bool] | None = None, say: Any = print) -> int:
        assert ask is not None
        shared.append({"argv": argv, "closed": ask(share.CLOSE_ASK)})
        return 0

    monkeypatch.setattr(share, "main", share_main)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("Terminal asked"))
    window = Scripted(admin=[{"lines": ["✓ Her own user: _estia"], "code": 0}], answers=[True])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.planned["move"] = True
    assert wizard.always_on() is True
    [admin] = window.sent("admin")
    python = str(setup.REPO / ".venv" / "bin" / "python")
    assert admin["argv"] == [python, "-I", "-m", "local_assistant.ownuser", "migrate", "--checkout", str(setup.REPO)]
    assert admin["why"] == prompts.ADMIN_WHY_MOVE and admin["input"] is None
    marker = str(Path(os.environ["ESTIA_TEST_ROOT"]) / "usr/local/estia/migration.json")
    assert shared == [{"argv": ["--marker", marker, "--after-move"], "closed": True}]
    [screen] = window.sent("screen")
    assert screen["id"] == "share_close" and share.CLOSE_ASK in str(screen["lead"])
    assert any(m["line"] == "✓ Her own user: _estia" for m in window.sent("progress"))


def test_once_she_is_moved_the_windows_move_runs_nothing_as_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The script's guard: once she is moved (her service file under the tools' root, never an ESTIA_ROOT left from
    a rehearsal), the checkout's Python never runs as root; the script's own line says so (review ADM-7)."""
    import plistlib

    from local_assistant import share

    tools = Path(os.environ["ESTIA_TEST_ROOT"])
    daemons = tools / "Library" / "LaunchDaemons"
    daemons.mkdir(parents=True, exist_ok=True)
    (daemons / f"{ownuser.BRIDGE}.plist").write_bytes(plistlib.dumps({"UserName": ownuser.ROLE_USER}))
    monkeypatch.setenv("ESTIA_ROOT", str(tmp_path / "rehearsal-gone"))
    monkeypatch.setattr(share, "main", lambda *args, **kwargs: pytest.fail("shared again"))
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.move_in_window() == 0
    assert not window.sent("admin")
    assert prompts.SETUP_ALREADY_MOVED in [m["line"] for m in window.sent("progress")]
    script = (ROOT / "scripts" / "migrate-to-own-user.sh").read_text(encoding="utf-8")
    assert f'echo "{prompts.SETUP_ALREADY_MOVED}"' in script  # the script's words


def test_a_move_that_fails_says_so_and_shares_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from local_assistant import share

    monkeypatch.setattr(share, "main", lambda *args, **kwargs: pytest.fail("shared after a failed move"))
    window = Scripted(admin=[{"code": 1}])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.planned["move"] = True
    assert wizard.always_on() is False


def test_a_move_refused_at_the_box_takes_the_moves_own_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin rights not given, then Not now: the move did not run; setup stops before the security step, and says
    that nothing changed, never "the ✗ above says why" with no ✗ above (review ADM-11). The screen names the move's
    own script for Terminal, never the checkout's Python as root, which skips the script's guard."""
    from local_assistant import setup, share

    monkeypatch.setattr(share, "main", lambda *args, **kwargs: pytest.fail("shared after no move"))
    window = Scripted(admin=[{"refuse": True}], answers=[False])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.planned["move"] = True
    assert wizard.always_on() is False
    assert wizard.move_stop == prompts.SETUP_MOVE_REFUSED  # the stop setup ends with (stops-reruns-move-failed)
    assert "admin rights were not given, and nothing changed" in prompts.SETUP_MOVE_REFUSED
    [screen] = window.sent("screen")
    script = str(setup.REPO / "scripts" / "migrate-to-own-user.sh")
    assert str(screen["lead"]).startswith(prompts.ADMIN_WHY_MOVE) and f"$ {script}" in str(screen["lead"])
    assert "local_assistant.ownuser" not in str(screen)


@pytest.mark.parametrize(
    ("argv", "why", "given", "later"),
    [
        ([SUDO, ESTIA, "restart"], prompts.ADMIN_WHY_HER, None, f"{SUDO} {ESTIA} restart"),
        ([SUDO, "-n", "/bin/launchctl", "bootout", "system/x"], prompts.ADMIN_WHY_SECURITY, None, "harden.sh"),
        ([SUDO, "/usr/bin/tee", "/Library/LaunchDaemons/x.plist"], prompts.ADMIN_WHY_OLLAMA, "<plist/>", "install-ollama.sh"),
        # review W04: install-voice-daemon.sh leaves calls off, and a raw step of her Ollama installs nothing: both are
        # finished by setup's own run, whose calls step and her Ollama's step skip what is done already
        ([SUDO, str(ownuser.BIN / "estia-deploy"), "--ask-lines"], prompts.ADMIN_WHY_CALLS, None, "scripts/setup.sh"),
        ([SUDO, "/usr/bin/install", "-d", "-m", "755", "-o", "root", "-g", "wheel", "/usr/local/estia"],
         prompts.ADMIN_WHY_OLLAMA_HERS, None, "scripts/setup.sh"),
        ([SUDO, ESTIA, "secret", "set", "web.env", "KEY"], prompts.ADMIN_WHY_HER, "made-up\n", ""),
        ([SUDO, "-n", "-u", "_estia", "-H", "--", "/usr/bin/env", "-i", "/bin/echo"], "", None, ""),
    ],
)  # fmt: skip
def test_the_refused_screen_says_what_it_was_for_and_a_command_terminal_can_run(
    tmp_path: Path, argv: list[str], why: str, given: str | None, later: str
) -> None:
    """The refused screen ("Admin rights: not given"): the step's own line first (what the rights were for), that nothing ran, and for
    Terminal later only a command that runs there as typed: the step's own script, or the command itself when it is
    one you would type; never a `sudo -n` form, nor one that waits for input (review ADM-11, FRIEND-11)."""
    window = Scripted(admin=[{"refuse": True}], answers=[False])
    _wizard, face = in_the_window(tmp_path, window)
    assert face.admin(argv, input=given, why=why) == setup_admin.Ran(1, prompts.SETUP_WINDOW_ADMIN_REFUSED_TITLE, True)
    [screen] = window.sent("screen")
    lead = str(screen["lead"])
    nothing_ran = (
        prompts.SETUP_WINDOW_ADMIN_NOT_NOW_STOPS
        if why in setup_window.STOPS_SETUP
        else prompts.SETUP_WINDOW_ADMIN_NOTHING_RAN
    )
    assert lead.startswith(why or prompts.ADMIN_WHY) and nothing_ran in lead
    commands = [line[2:] for line in lead.splitlines() if line.startswith("$ ")]
    assert commands[-1:] == [] if not later else commands[-1].endswith(later), commands
    assert "-n" not in " ".join(commands).split()


@pytest.mark.parametrize("why", [prompts.ADMIN_WHY_CALLS, prompts.ADMIN_WHY_OLLAMA_HERS])
def test_the_refused_screens_later_for_setup_itself_names_macos_box(tmp_path: Path, why: str) -> None:
    """Fix review (setup-code-03): for calls and her own Ollama the later command is setup itself (review W04), yet
    the line said "in your own Terminal window (it asks for your Mac password there)": at the desk setup opens its
    window again and macOS's own box asks, and the friend's AI may start it. The line says so, with the command that
    a new Terminal window can run (W12's form)."""
    window = Scripted(admin=[{"refuse": True}], answers=[False])
    _wizard, face = in_the_window(tmp_path, window)
    face.admin([SUDO, str(ownuser.BIN / "estia-deploy"), "--ask-lines"], why=why)
    [screen] = window.sent("screen")
    lead = str(screen["lead"])
    later = prompts.SETUP_WINDOW_ADMIN_LATER_SETUP.format(command=setup_window.setup_again_command())
    assert lead.endswith(later) and "Mac password" not in lead
    # second fix review (words-admin-later-asks-what): what macOS asks for, in the window's one form
    assert "Later, start setup again (admin rights, asked by macOS in its own box):" in lead
    assert "ask your AI, or run this in Terminal" in later


def test_the_box_lines_and_not_now_say_what_is_true_at_that_moment(tmp_path: Path) -> None:
    """Round 2 (words-04): the security step's first box comes for read-only checks, before any yes, yet its line said
    "the security step's changes you said yes to"; the calls line named only the deploy, also under the wait that
    reads her voice. And (r2-window-admin-not-now-and-later-words) "Not now leaves this command undone" showed where
    Not now stops setup (Ollama's step, the move): those say that it stops setup, every answer kept."""
    assert (
        "read the Mac's settings" in prompts.ADMIN_WHY_SECURITY
        and "only what you say yes to" in prompts.ADMIN_WHY_SECURITY
    )
    # words review, 30 Oct: no internals ("her release", "read her voice as her") in macOS's box
    assert prompts.ADMIN_WHY_CALLS == (
        "Admin rights to add her voice and the call tools to her install, then to check that her voice is ready."
    )
    assert all(len(line) <= setup_window.ADMIN_WHY for line in setup_window.ADMIN_LINES)
    for why, stops in (
        (prompts.ADMIN_WHY_OLLAMA_HERS, True),
        (prompts.ADMIN_WHY_MOVE, True),
        (prompts.ADMIN_WHY_HER, False),
    ):
        window = Scripted(admin=[{"refuse": True}], answers=[False])
        _wizard, face = in_the_window(tmp_path, window)
        face.admin([SUDO, ESTIA, "restart"], why=why)
        [screen] = window.sent("screen")
        lead = str(screen["lead"])
        assert (prompts.SETUP_WINDOW_ADMIN_NOT_NOW_STOPS in lead) is stops, lead
        assert (prompts.SETUP_WINDOW_ADMIN_NOTHING_RAN in lead) is not stops, lead


def test_the_box_line_is_said_once_a_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The line "Admin rights: asked by macOS in its own box" once at a step's first admin command, never before each one:
    macOS's box shows once in 5 minutes, and the line no longer said when it asks (review FRIEND-11)."""
    window = Scripted(admin=[{"code": 0}] * 3)
    wizard, face = in_the_window(tmp_path, window)
    monkeypatch.setattr(wizard, "step_now", "security", raising=False)
    face.admin([SUDO, ESTIA, "restart"])
    face.admin([SUDO, ESTIA, "restart"])
    monkeypatch.setattr(wizard, "step_now", "calls", raising=False)
    face.admin([SUDO, ESTIA, "restart"])
    lines = [m["line"] for m in window.sent("progress")]
    assert lines.count(prompts.SETUP_WINDOW_ADMIN_BOX) == 2


def test_the_windows_move_is_the_scripts_own() -> None:
    """The window runs what scripts/migrate-to-own-user.sh runs, in the same order: a drift between the two fails
    here."""
    script = (ROOT / "scripts/migrate-to-own-user.sh").read_text(encoding="utf-8")
    assert '"$sudo" "$python" -I -m local_assistant.ownuser migrate --checkout "$repo" || exit 1' in script
    assert (
        'share() { "$python" -I -m local_assistant.share --marker "$estia_root/usr/local/estia/migration.json" "$@"; }'
        in script
    )
    assert "share --after-move ||" in script and "exit 3" in script


# ------------------------------------------------------------------ the security step (H1-H10)


def test_the_security_step_runs_in_process_in_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H1-H9: the security step runs inside setup with the window as its face: its questions are window screens, its
    PIN the window's PIN screens, its sudo commands go through the window (WindowHost); scripts/harden.sh is never
    run, and Terminal is never asked."""
    from local_assistant import harden, setup_admin

    made: list[harden.Harden] = []
    monkeypatch.setattr(harden.Harden, "run", lambda self: noted(made, self, 0))
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("Terminal asked"))
    wizard, face = in_the_window(tmp_path, Scripted())
    wizard.planned["security"] = True
    wizard.security()
    [step] = made
    assert isinstance(step.host, setup_admin.WindowHost) and step.host.face is face and step.window
    assert step.host.why == prompts.ADMIN_WHY_SECURITY
    face.terminal_face = PlainFace(wizard.readers())  # the window closed: the rest of the step is harden.sh's
    assert not step.window


def test_the_security_steps_questions_are_window_screens(tmp_path: Path) -> None:
    """Each question as a yes or no: its words without Terminal's recommendation line (the window's pill marks it),
    the recommendation where Enter takes yes, none where it takes no."""
    from local_assistant import harden

    window = Scripted(answers=[True, False])
    wizard, _face = in_the_window(tmp_path, window)
    ask = wizard.harden_ask()
    assert ask(harden.QUESTIONS["data_modes"], True) is True
    assert ask(harden.QUESTIONS["firewall"], False) is False
    first, second = window.sent("screen")
    assert first["recommended"] == "yes" and "Yes (recommended" not in str(first["lead"]) + str(first["ask"])
    assert first["ask"] == "Make her files private?"
    assert second["recommended"] is None and second["ask"] == harden.QUESTIONS["firewall"]


def test_the_security_steps_pin_is_typed_on_the_windows_pin_screens(tmp_path: Path) -> None:
    from local_assistant import harden

    window = Scripted(answers=["731904", "731904", "12"])
    wizard, _face = in_the_window(tmp_path, window)
    hidden = wizard.harden_hidden()
    assert hidden(harden.PIN_ASK) == "731904" and hidden(harden.PIN_AGAIN) == "731904"
    assert [m["id"] for m in window.sent("screen")] == ["lock_pin", "lock_pin_again"]
    window.gone = True  # the window closed at the next PIN: its end is a skip, as q in Terminal
    face = wizard.face
    assert isinstance(face, WindowFace)
    face.terminal_face = PlainFace(wizard.readers())
    wizard.hidden = lambda prompt: "q"  # type: ignore[method-assign]
    assert hidden(harden.PIN_ASK) == "q"
    sent = "\n".join(str(m) for m in window.messages if m["t"] != "screen")
    assert "731904" not in sent  # never sent back


def test_the_security_step_in_terminal_runs_harden_sh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from local_assistant import setup

    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    monkeypatch.setattr(
        wizard, "run", lambda command, **kwargs: noted(ran, command, subprocess.CompletedProcess(command, 0))
    )
    wizard.planned["security"] = True
    wizard.security()
    assert ran == [[str(setup.REPO / "scripts" / "harden.sh")]] and not wizard.for_terminal


# ------------------------------------------------------------------ her config, her restart, her doctor (E1-E3, D1-D3)


def test_her_config_lines_are_added_as_her_on_a_yes_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E1: no nano in the window. Its screen shows the lines; on Add them, root runs her release's herconfig as her
    (sudo -n -u _estia: root needs no password for her), macOS's box asking for the admin rights; she restarts once,
    at the end."""
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("Terminal asked"))
    window = Scripted(admin=[{"lines": ["✓ Her config: [chords] on (a copy of the old one kept: x)."]}], answers=[True])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.her_config(prompts.SETUP_CHORDS_HERS, ("section", "chords"))
    [screen] = window.sent("screen")
    assert screen["id"] == "her_config_add" and "• [chords]" in str(screen["lead"])
    assert prompts.SETUP_WINDOW_HER_CONFIG in str(screen["lead"])
    [admin] = window.sent("admin")
    argv = admin["argv"]
    assert isinstance(argv, list)
    assert argv[:6] == [SUDO, "-n", "-u", "_estia", "-H", "--"]
    assert argv[-6:] == ["-I", "-m", "local_assistant.herconfig", "apply", "section", "chords"]
    assert str(ownuser.CURRENT / ".venv" / "bin" / "python") in argv and admin["why"] == prompts.ADMIN_WHY_HER
    assert wizard.restart_due


def test_her_config_left_for_later_names_the_command_for_terminal(tmp_path: Path) -> None:
    window = Scripted(answers=[False])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.her_config(prompts.SETUP_CHORDS_HERS, ("section", "chords"))
    assert not window.sent("admin") and not wizard.restart_due
    lines = [str(m["line"]) for m in window.sent("progress")]
    assert prompts.SETUP_WINDOW_HER_CONFIG_LATER in lines


@pytest.mark.parametrize("old", [False, True], ids=["later", "old-release"])
def test_her_config_left_for_terminal_says_its_lines_and_keeps_them_for_the_end(tmp_path: Path, old: bool) -> None:
    """Later, or a release without herconfig: the lines the screen showed are said in the log right before the
    command, and the closing list keeps both (review FRIEND-7: "add the lines above" had no lines above)."""
    from local_assistant import herconfig

    no_module = "/usr/local/estia/current/.venv/bin/python: No module named local_assistant.herconfig"
    window = Scripted(admin=[{"lines": [no_module], "code": 1}], answers=[old])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.her_config(prompts.SETUP_CHORDS_HERS, ("section", "chords"))
    lines = [str(m["line"]) for m in window.sent("progress")]
    added = herconfig.lines(("section", "chords"))
    command = prompts.SETUP_WINDOW_HER_CONFIG_OLD if old else prompts.SETUP_WINDOW_HER_CONFIG_LATER
    assert lines.index(added) == lines.index(command) - 1
    assert wizard.for_terminal == (f"{added}\n{command}",)


def test_a_release_without_herconfig_leaves_the_lines_for_terminal(tmp_path: Path) -> None:
    """A release from before (no herconfig yet): the lines stay on screen, and the command to add them by hand."""
    window = Scripted(admin=[{"lines": ["/usr/local/estia/current/.venv/bin/python: No module named local_assistant.herconfig"],
                              "code": 1}], answers=[True])  # fmt: skip
    wizard, _face = in_the_window(tmp_path, window)
    wizard.her_config(prompts.SETUP_CHORDS_HERS, ("section", "chords"))
    lines = [str(m["line"]) for m in window.sent("progress")]
    assert prompts.SETUP_WINDOW_HER_CONFIG_OLD in lines and not wizard.restart_due


class ClosesAt(Scripted):
    """A window the person closes on one screen, before any answer there."""

    def __init__(self, screen: str) -> None:
        super().__init__()
        self.at = screen

    def send(self, message: dict[str, object]) -> bool:
        if message["t"] == "screen" and message["id"] == self.at and not self.gone:
            self.messages.append(dict(message))
            self.gone = True
            return True
        return super().send(message)


def test_her_config_after_the_window_closed_on_its_screen_is_terminals_nano_as_her(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review of 30 Oct (P03): the window closed on "Her config", and a yes in Terminal ran `sudo -n -u _estia …
    herconfig apply` with no sudo ticket there (every earlier admin command went through macOS's box): it failed
    unsaid, and Terminal's offer never came. Terminal then gets what a Terminal setup gets: the lines, then her config
    opened as her on Enter."""
    ran: list[list[str]] = []
    typed = iter(["y", ""])  # the yes, then Enter at the offer
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
    window = ClosesAt("her_config_add")
    wizard, face = in_the_window(tmp_path, window)
    monkeypatch.setattr(
        wizard, "run", lambda command, **kwargs: noted(ran, command, subprocess.CompletedProcess(command, 0))
    )
    assert wizard.her_config(prompts.SETUP_CHORDS_HERS, ("section", "chords"))
    assert ran == [[SUDO, ESTIA, "config"]] and wizard.restart_due and face.terminal_face is not None
    assert prompts.SETUP_CHORDS_HERS.split("\n", 1)[0] in capsys.readouterr().out
    assert not window.sent("admin")


def test_her_config_in_terminal_opens_nano_as_her_as_it_always_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    monkeypatch.setattr(
        wizard, "run", lambda command, **kwargs: noted(ran, command, subprocess.CompletedProcess(command, 0))
    )
    wizard.her_config(prompts.SETUP_CHORDS_HERS, ("section", "chords"))
    assert ran == [[SUDO, ESTIA, "config"]] and wizard.restart_due


def test_a_stop_screens_title_keeps_its_area_and_her_models_stop_is_a_stop(tmp_path: Path) -> None:
    """Round 2 (words-06, r2-window-stream-08): the window's stop title dropped its area ("Its background service was
    not installed.") and capitalised a code name ("Local-worker was not made"). The title keeps its area, and her
    model's stop is one of setup's stops, its fix on its own line."""
    window = Scripted()
    _wizard, face = in_the_window(tmp_path, window)
    face.stopped(prompts.SETUP_MODEL_NOT_MADE)
    [screen] = window.sent("screen")
    assert screen["title"] == "Model: local-worker was not made."
    assert "Fix: check that Ollama runs, then run scripts/setup.sh again." in str(screen["lead"])


def test_her_check_not_run_for_admin_rights_not_given_is_said_as_that(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 2 (r2-window-admin-check-refused-done): every box for her doctor cancelled, then Not now: the closing
    list said "The check found a problem: the ✗ lines above say what to fix." with no ✗ line anywhere. It says the
    check did not run, and the command that runs it."""
    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    window = Scripted(admin=[{"refuse": True}], answers=[False])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.check() != 0
    monkeypatch.setattr("local_assistant.setup_extras._voice_installed", lambda: False)
    wizard.closing()
    lines = [str(m["line"]) for m in window.sent("progress")]
    assert prompts.SETUP_CHECK_NOT_RUN in lines and prompts.SETUP_CHECK_FAILED not in lines
    assert prompts.SETUP_WINDOW_ADMIN_REFUSED_TITLE not in lines  # no stray line as if a command had said it


def test_her_restart_doctor_and_keys_go_through_the_window_with_her_box_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2, E3, D1: her restart (offered), a web key into her web.env (the key on stdin only) and her doctor."""
    from local_assistant import setup_keychain

    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    monkeypatch.setattr(setup_keychain, "has", lambda service, account: False)
    monkeypatch.setattr(setup_keychain, "save", lambda *args, **kwargs: setup_keychain.Saved.KEPT)
    window = Scripted(answers=[True])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.restart_due = True
    wizard.restart_her()
    wizard._save_key("OLLAMA_API_KEY", "ollama", "made-up" + "-key", "Ollama's key")
    wizard.check()
    argvs = [m["argv"] for m in window.sent("admin")]
    assert argvs == [[ESTIA, "restart"], [ESTIA, "secret", "set", "web.env", "OLLAMA_API_KEY"], [ESTIA, "doctor"]]
    assert {m["why"] for m in window.sent("admin")} == {prompts.ADMIN_WHY_HER}
    assert window.sent("admin")[1]["input"] == "made-up-key\n"


def test_her_voice_is_read_as_her_through_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D2: in her own account her voice service answers only her: read as her (root runs sudo -n -u _estia, no
    password), through the window."""
    window = Scripted(admin=[{"lines": ['{"stt":"ready","tts":"ready"}']}])
    wizard, _face = in_the_window(tmp_path, window)
    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    monkeypatch.delenv("ESTIA_ROOT")
    assert wizard.voice_health() == {"stt": "ready", "tts": "ready"}
    [admin] = window.sent("admin")
    assert admin["argv"] == list(ownuser.VOICE_HEALTH_AS_HER)


# ------------------------------------------------------------------ calls: her voice and the deploy (K1-K8)

DEPLOY = str(ownuser.BIN / "estia-deploy")
REVIEW_ASK = "\x1f? Deploy it? Read the list above first: the release runs as her, with her data."


def test_her_voice_is_installed_in_the_window_its_review_a_screen(tmp_path: Path) -> None:
    """K3: install-voice-daemon.sh's two admin commands in the window: the deploy with --ask-lines, its review a
    yes or no screen (its lines in the More fold), then the wait for her voice as her, one command."""
    from local_assistant import setup

    window = Scripted(
        admin=[
            {"lines": ["2 new commit(s) since the live abc1234:", "  def5678 calls", REVIEW_ASK], "after": ["✓ live"]},
            {"lines": ['{"stt":"ready","tts":"ready"}']},
        ],
        answers=[True],
    )
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._voice_in_window() == 0
    deploy, wait = window.sent("admin")
    assert deploy["argv"] == [DEPLOY, "--ask-lines", "--voice", str(setup.REPO)] and deploy["open"] is True
    assert window.inputs == ["y\n"]
    [review] = window.sent("screen")
    assert review["id"] == "deploy_review" and review["ask"] == REVIEW_ASK[3:]
    assert "def5678 calls" in str(review["more"]) and review["recommended"] is None
    assert wait["argv"] == [SUDO, "-n", "-u", "_estia", "-H", "--", "/usr/bin/env", "-i", "HOME=/var/estia",
                            "PATH=/usr/bin:/bin", "/bin/bash", "-c", ownuser.VOICE_WAIT_AS_HER]  # fmt: skip
    assert {deploy["why"], wait["why"]} == {prompts.ADMIN_WHY_CALLS}
    assert any(m["line"] == "voice: ready" for m in window.sent("progress"))


@pytest.mark.parametrize(
    ("state", "code", "said"),
    [
        ('{"stt":"failed","tts":"ready"}', 1, "voice: failed"),
        ('{"stt":"loading","tts":"loading"}', 3, "still loading after 20 minutes"),
    ],
)
def test_her_voice_state_reads_as_the_script_reads_it(tmp_path: Path, state: str, code: int, said: str) -> None:
    window = Scripted(admin=[{"lines": [REVIEW_ASK]}, {"lines": [state]}], answers=[True])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._voice_in_window() == code
    assert any(said in str(m["line"]) for m in window.sent("progress"))


def test_stop_here_at_the_deploys_question_ends_its_input_and_stops_after_it(tmp_path: Path) -> None:
    """Stop here on the review's screen: the deploy's stdin ends at once (a no: it changes nothing and ends), setup
    waits for its end, then stops; never root's command left waiting for an answer while the window cannot close."""
    window = Scripted(admin=[{"lines": [REVIEW_ASK], "after": ["Nothing changed."], "code": 1}], answers=[STOP])
    wizard, _face = in_the_window(tmp_path, window)
    with pytest.raises(KeyboardInterrupt):
        wizard._deploy_in_window([SUDO, DEPLOY, "--ask-lines"])
    assert window.inputs == [] and window.sent("admin_end")
    kinds = [m["t"] for m in window.messages]
    assert kinds.index("admin_end") > kinds.index("screen")
    assert any(m["line"] == prompts.SETUP_WINDOW_FINISHES_FIRST for m in window.sent("progress"))


def test_a_deploy_said_no_to_or_refused_installs_no_voice(tmp_path: Path) -> None:
    window = Scripted(admin=[{"lines": [REVIEW_ASK], "after": ["Nothing changed."], "code": 1}], answers=[False])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._voice_in_window() == 1
    assert window.inputs == ["n\n"] and len(window.sent("admin")) == 1


def test_her_updates_whole_list_is_in_its_more_fold_one_item_a_line(tmp_path: Path) -> None:
    """Review W07: the list is the named defence against a changed checkout; the More fold showed it as one run-on
    paragraph, cut to its last 200 lines, so a long review lost its header and its commits."""
    commits = [f"  {n:07x} commit {n}" for n in range(50)]
    files = [f"  src/file_{n}.py | 2 +-" for n in range(200)]
    listed = ["50 new commit(s) since the live abc1234:", *commits, *files]
    window = Scripted(admin=[{"lines": [*listed, REVIEW_ASK], "after": ["✓ live"]}], answers=[True])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._deploy_in_window([SUDO, DEPLOY, "--ask-lines"]) == 0
    [review] = window.sent("screen")
    assert str(review["more"]).splitlines() == [f"• {line.strip()}" for line in listed]


def test_admin_rights_not_given_at_the_voice_wait_say_so(tmp_path: Path) -> None:
    """Review W04 (optional part): Cancel at macOS's box for the wait that reads her voice, then Not now: nothing
    failed, so calls are said as not on yet for admin rights not given, never "stopped. Fix what it says above"."""
    window = Scripted(admin=[{"lines": [REVIEW_ASK], "after": ["✓ live"]}, {"refuse": True}], answers=[True, False])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._voice_in_window() == 1
    assert wizard.calls_not_on == prompts.SETUP_CALLS_REFUSED


class Clock:
    """setup_extras' clock: each look is `step` seconds after the one before."""

    def __init__(self, step: float) -> None:
        self.now, self.step = 0.0, step

    def monotonic(self) -> float:
        self.now += self.step
        return self.now


@pytest.mark.parametrize(
    ("entry", "answers", "said", "late"),
    [
        ({"lines": [REVIEW_ASK], "after": ["Nothing deployed."], "code": 1}, [False], "SETUP_CALLS_NOT_DEPLOYED", 0),
        ({"refuse": True, "ends": True}, [False], "SETUP_CALLS_REFUSED", 0),
        # review W05: deploy took no answer for 10 minutes as a no and ended before the friend's Deploy it ...
        (
            {"lines": [REVIEW_ASK], "after": ["Nothing deployed."], "code": 1, "ends": True},
            [True],
            "SETUP_CALLS_WAITED",
            0,
        ),
        # ... or the Deploy it came after its 10 minutes, the run's end not read yet
        ({"lines": [REVIEW_ASK], "after": ["Nothing deployed."], "code": 1}, [True], "SETUP_CALLS_WAITED", 601),
    ],
)
def test_calls_not_on_after_a_no_or_admin_rights_not_given_say_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry: dict[str, Any],
    answers: list[object],
    said: str,
    late: float,
) -> None:
    """Round 2 (words-05, r2-window-stream-01): Not now at her update, or Cancel at macOS's box then Not now, ended the
    calls step with "✗ Calls: scripts/install-voice-daemon.sh stopped. Fix what it says above": nothing failed, and
    nothing above says what to fix. Each is said as what happened, with how to set calls up later. A Deploy it after
    the update's question waited 10 minutes (deploy's own no) is said as that, and sends nothing (review W05)."""
    from local_assistant import setup_extras

    monkeypatch.setattr(setup_extras, "time", Clock(late))
    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    for name in ("_release_has_calls", "_voice_installed", "_tunnel_built"):
        monkeypatch.setattr(setup_extras, name, lambda: False)
    monkeypatch.setattr(setup_extras.ExtrasSteps, "_signal_cli_version", lambda self: [])
    monkeypatch.setattr(setup_extras, "_macos_version", lambda: "27.1")
    window = Scripted(admin=[entry], answers=answers)
    wizard, _face = in_the_window(tmp_path, window)
    wizard.planned["calls"] = "yes"
    monkeypatch.setattr(wizard, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0))
    wizard.calls_step()
    lines = [str(m["line"]) for m in window.sent("progress")]
    assert getattr(prompts, said) in lines
    assert not any("Fix what its lines say" in line for line in lines)
    assert window.inputs == ([] if late or entry.get("ends") else ["n\n"])


def test_setup_waits_for_her_updates_answer_as_long_as_deploy_does() -> None:
    from local_assistant import deploy

    assert deploy.ASK_SECONDS == setup_admin.ASK_SECONDS


def test_her_tools_from_before_ask_lines_leave_the_deploy_to_terminal(tmp_path: Path) -> None:
    """Root-run tools from before --ask-lines (no --update-tools since): the deploy refuses the flag; the window names
    the command for Terminal, never a hang."""
    window = Scripted(
        admin=[{"lines": ["estia-deploy: error: unrecognized arguments: --ask-lines"], "code": 2, "ends": True}]
    )
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._deploy_in_window([SUDO, DEPLOY, "--ask-lines"]) == 1
    lines = [str(m["line"]) for m in window.sent("progress")]
    assert prompts.SETUP_WINDOW_DEPLOY_OLD.format(command=f"{SUDO} {DEPLOY}") in lines


def test_the_calls_step_in_the_window_installs_her_voice_and_deploys_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_assistant import setup_extras

    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    monkeypatch.setattr(setup_extras, "_release_has_calls", lambda: False)
    monkeypatch.setattr(setup_extras, "_voice_installed", lambda: False)
    monkeypatch.setattr(setup_extras, "_tunnel_built", lambda: False)
    monkeypatch.setattr(setup_extras.ExtrasSteps, "_signal_cli_version", lambda self: [])
    ran: list[str] = []
    window = Scripted(answers=[False])  # her config's lines: later
    wizard, _face = in_the_window(tmp_path, window)
    wizard.planned["calls"] = "yes"
    monkeypatch.setattr(
        wizard,
        "run",
        lambda command, **kwargs: noted(ran, Path(command[0]).name, subprocess.CompletedProcess(command, 0)),
    )
    monkeypatch.setattr(wizard, "_voice_in_window", lambda: noted(ran, "voice", 0))
    monkeypatch.setattr(wizard, "_deploy_in_window", lambda argv: noted(ran, " ".join(argv[1:]), 0))
    wizard.calls_step()
    assert ran == ["brew", "rustup", "voice", "install-signal-cli-jvm.sh", "brew", "build.sh",
                   f"{DEPLOY} --ask-lines"]  # fmt: skip


def test_the_window_waits_for_her_voice_as_the_script_does() -> None:
    script = (ROOT / "scripts/install-voice-daemon.sh").read_text(encoding="utf-8")
    assert f"wait_as_her='{ownuser.VOICE_WAIT_AS_HER}'" in script
    assert '"$sudo" "$estia_deploy" --voice "$repo"' in script
    for line in prompts.SETUP_VOICE_WARN_27.splitlines():
        assert line in script, line
    assert not hasattr(prompts, "SETUP_VOICE_WARN_26") and "26.4" not in script  # macOS 27 or later only (29 Oct)
    from local_assistant import deploy

    assert deploy.ASK_MARK == setup_admin.ASK_MARK


# ------------------------------------------------------------------ the extras' scripts (E5, E7, E8, E14, E15)

SCRIPTS = ROOT / "scripts"


@pytest.mark.parametrize(
    ("name", "script", "args", "hers"),
    [
        ("lights", "tapo-discover.sh", [], [ESTIA, "tapo-discover"]),
        ("lights", "hue-pair.sh", [], [ESTIA, "hue-pair"]),
        ("mail", "mail-login.sh", ["alex@example.org"], [ESTIA, "mail-login", "alex@example.org"]),
    ],
)
def test_a_script_that_runs_her_command_runs_it_as_her_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, script: str, args: list[str], hers: list[str]
) -> None:
    """In her own macOS account these scripts only run her command as her (`exec sudo estia …`): sudo has no
    terminal in the window, so the window runs her command itself, macOS's own box asking for the admin rights."""
    from local_assistant import setup

    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    window = Scripted(admin=[{"lines": ["✓ found"]}])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._extra_offer(name, [str(setup.REPO / "scripts" / script), *args]) is True
    [offer] = window.sent("screen")
    assert offer["id"] == "offer_run" and f"$ {SUDO} {' '.join(hers)}" in str(offer["lead"])
    assert prompts.SETUP_WINDOW_ADMIN_BOX in str(offer["lead"])
    [admin] = window.sent("admin")
    assert admin["argv"] == hers and admin["why"] == prompts.ADMIN_WHY_HER


@pytest.mark.runs_commands  # its own stand-in for the stream
def test_before_her_account_such_a_script_streams_as_you_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_assistant import setup

    monkeypatch.setattr(Wizard, "own_user", lambda self: False)
    streamed: list[list[str]] = []
    monkeypatch.setattr(
        WindowFace,
        "_stream",
        lambda self, argv, **kwargs: noted(streamed, argv, subprocess.CompletedProcess(argv, 0)),
    )
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    command = [str(setup.REPO / "scripts" / "tapo-discover.sh")]
    assert wizard._extra_offer("lights", command) is True
    assert streamed == [command] and not window.sent("admin")


def test_in_terminal_the_extras_scripts_run_as_they_always_did(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from local_assistant import setup

    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    offered: list[list[str]] = []
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    monkeypatch.setattr(wizard, "offer", lambda command, **kwargs: noted(offered, command, True))
    command = [str(setup.REPO / "scripts" / "hue-pair.sh")]
    wizard._extra_offer("lights", command)
    assert offered == [command]


@pytest.mark.parametrize("own", [True, False])
def test_the_documents_share_runs_in_setup_its_close_question_a_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, own: bool
) -> None:
    """E14: in the window share-documents.sh's share runs inside setup, as you (no admin rights), its close question
    on the window's own screen; the offer screen still names the script."""
    from local_assistant import setup_extras, share

    monkeypatch.setattr(Wizard, "own_user", lambda self: own)
    shared: list[list[str]] = []

    def main(argv: list[str], *, ask: Callable[[str], bool] | None, say: Callable[[str], None]) -> int:
        shared.append(argv)
        assert ask is not None and ask("Close them now?") is True
        say("✓ shared")
        return 0

    monkeypatch.setattr(share, "main", main)
    window = Scripted(answers=[True, True])  # Run it; Close them
    wizard, _face = in_the_window(tmp_path, window)
    wizard._documents_folder() if own else wizard._extra_offer(
        "documents", [str(setup_extras.REPO / "scripts" / "share-documents.sh")], window=wizard._share(["--documents"])
    )
    marker = str(hostsec.tool_root() / "usr/local/estia/migration.json")
    assert shared == [["--documents", "--marker", marker]]
    offer, close = window.sent("screen")
    assert offer["id"] == "offer_run" and "share-documents.sh" in str(offer["lead"])
    assert close["id"] == "share_close" and "Close them now?" in str(close["lead"])
    assert not window.sent("admin") and any(m["line"] == "✓ shared" for m in window.sent("progress"))


def test_the_projects_share_runs_in_setup_in_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from local_assistant import share

    shared: list[list[str]] = []
    monkeypatch.setattr(share, "main", lambda argv, **kwargs: noted(shared, argv, 0))
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    projects = tmp_path / "dev"
    assert wizard._share_projects(projects) is True
    marker = str(hostsec.tool_root() / "usr/local/estia/migration.json")
    assert shared == [[str(projects), "--marker", marker]]


def test_in_terminal_the_shares_run_their_scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from local_assistant import setup_extras

    offered: list[tuple[list[str], object]] = []
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    monkeypatch.setattr(
        wizard, "offer", lambda command, **kwargs: noted(offered, (command, kwargs.get("window")), True)
    )
    wizard._share_projects(tmp_path / "dev")
    assert offered == [([str(setup_extras.REPO / "scripts" / "share-projects.sh"), str(tmp_path / "dev")], None)]


# ------------------------------------------------------------------ the TP-Link login and the calendar's address (E6, E9)

TAPO_SECRET = "made-up-" + "tp-link-password"  # never a real one
CALENDAR = "https://calendar.google.com/calendar/ical/made-up%40example.org/private-0123/basic.ics"


def test_the_tp_link_login_is_asked_on_screens_and_saved_as_her(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E6: set-tapo-credentials.sh's two questions on the window's screens (the password hidden), then her wrapper
    saves each, its value on stdin only, macOS's own box asking for the admin rights."""
    from local_assistant import setup

    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    window = Scripted(answers=[True, "alex@example.org", TAPO_SECRET])
    wizard, _face = in_the_window(tmp_path, window)
    wizard._extra_offer(
        "lights",
        [str(setup.REPO / "scripts" / "set-tapo-credentials.sh")],
        window=wizard._in_window("set-tapo-credentials.sh"),
    )
    offer, email, password = window.sent("screen")
    assert (offer["id"], email["id"], password["id"]) == ("offer_run", "tapo_email", "tapo_password")
    assert password["kind"] == "hidden"
    user, secret = window.sent("admin")
    assert (
        user["argv"] == [ESTIA, "secret", "set", "tapo.env", "TAPO_USERNAME"] and user["input"] == "alex@example.org\n"
    )
    assert (
        secret["argv"] == [ESTIA, "secret", "set", "tapo.env", "TAPO_PASSWORD"]
        and secret["input"] == f"{TAPO_SECRET}\n"
    )
    assert {user["why"], secret["why"]} == {prompts.ADMIN_WHY_HER}
    assert not any(TAPO_SECRET in str(m) for m in window.messages if m["t"] != "admin")
    assert any(m["line"] == prompts.SETUP_TAPO_SAVED_HERS for m in window.sent("progress"))


def test_before_her_account_the_tp_link_login_goes_into_tapo_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Wizard, "own_user", lambda self: False)
    monkeypatch.setenv("HOME", str(tmp_path))
    window = Scripted(answers=["alex@example.org", TAPO_SECRET])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._tapo_login() == 0
    saved = tmp_path / ".config" / "local-assistant" / "tapo.env"
    assert saved.read_text(encoding="utf-8").splitlines() == [
        "TAPO_USERNAME=alex@example.org",
        f"TAPO_PASSWORD={TAPO_SECRET}",
    ]
    assert saved.stat().st_mode & 0o777 == 0o600 and not window.sent("admin")
    assert any(m["line"] == prompts.SETUP_TAPO_SAVED.format(file=saved) for m in window.sent("progress"))


@pytest.mark.parametrize("answers", [[""], ["alex@example.org", ""]])
def test_the_tp_link_login_left_for_later_saves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[str]
) -> None:
    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    window = Scripted(answers=list(answers))
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._tapo_login() == 0
    assert not window.sent("admin") and any(m["line"] == prompts.SETUP_OFFER_SKIPPED for m in window.sent("progress"))


def test_the_calendars_address_is_pasted_hidden_and_saved_as_her(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    window = Scripted(answers=["https://example.org/not-google.ics", CALENDAR])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._calendar_address() == 0
    [screen] = window.sent("screen")  # refused, the same screen stays
    assert screen["id"] == "calendar_url" and screen["kind"] == "hidden"
    refused = [m for m in window.sent("checked") if not m["ok"]]
    assert refused and refused[0]["message"] == prompts.SETUP_CALENDAR_BAD
    [admin] = window.sent("admin")
    assert (
        admin["argv"] == [ESTIA, "secret", "set", "calendar.env", "CALENDAR_ICS_URL"]
        and admin["input"] == f"{CALENDAR}\n"
    )
    assert not any(CALENDAR in str(m) for m in window.messages if m["t"] != "admin")
    assert any(m["line"] == prompts.SETUP_CALENDAR_SAVED_HERS for m in window.sent("progress"))


def test_before_her_account_the_calendars_address_goes_into_calendar_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Wizard, "own_user", lambda self: False)
    monkeypatch.setenv("HOME", str(tmp_path))
    window = Scripted(answers=[CALENDAR])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._calendar_address() == 0
    saved = tmp_path / ".config" / "local-assistant" / "calendar.env"
    lines = saved.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"CALENDAR_ICS_URL={CALENDAR}" and lines[1].startswith("CALENDAR_SAVED_AT=")
    assert any(m["line"] == prompts.SETUP_CALENDAR_SAVED.format(file=saved) for m in window.sent("progress"))


def test_a_save_refused_by_macos_says_it_was_not_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    window = Scripted(admin=[{"refuse": True}], answers=[CALENDAR, False])  # the address; Not now at the refusal
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard._calendar_address() == 1
    lines = [str(m["line"]) for m in window.sent("progress")]
    assert any(line.startswith(f"✗ {prompts.SETUP_CALENDAR_TITLE}: not saved (") for line in lines)
    assert prompts.SETUP_CALENDAR_SAVED_HERS not in lines


def test_the_window_says_what_the_scripts_say(tmp_path: Path) -> None:
    tapo = (ROOT / "scripts/set-tapo-credentials.sh").read_text(encoding="utf-8")
    assert f'echo "{prompts.SETUP_TAPO_SAVED_HERS}"' in tapo
    assert 'echo "' + prompts.SETUP_TAPO_SAVED.format(file="$file") + '"' in tapo
    assert prompts.SETUP_TAPO_EMAIL_ASK in tapo and prompts.SETUP_TAPO_PASSWORD_ASK in tapo
    calendar = (ROOT / "scripts/set-calendar-url.sh").read_text(encoding="utf-8")
    assert prompts.SETUP_CALENDAR_SAVED_HERS.replace("CALENDAR_ICS_URL", "$key") in calendar
    assert prompts.SETUP_CALENDAR_SAVED.format(file="$file").replace("CALENDAR_ICS_URL", "$key") in calendar
    assert prompts.SETUP_CALENDAR_BAD in calendar


def test_in_terminal_no_script_has_a_window_stand_in(tmp_path: Path) -> None:
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    for script in ("set-tapo-credentials.sh", "set-calendar-url.sh", "spotify-login.sh"):
        assert wizard._in_window(script) is None


# ------------------------------------------------------------------ Spotify's sign-in: the pasted address (E10)

AUTHORIZE = "https://accounts.spotify.com/authorize?client_id=c&state=s"
PASTED = "http://127.0.0.1:8888/callback?code=made-up&state=s"
SIGN_IN = [
    "1. Open this address on a computer with a browser and sign in to Spotify (your Premium account):",
    AUTHORIZE,
    "2. The browser then fails to open a 127.0.0.1 page: that is expected. Copy that page's whole address.",
    "\x1f? 3. Paste the address here:",
]


def test_spotifys_sign_in_runs_as_her_its_address_pasted_on_a_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E10: in her own macOS account her sign-in runs as her (estia spotify-login), macOS's own box asking; its link
    on a screen of the window (opened in the browser on a click), the address the browser could not open pasted
    hidden there and written to the command's stdin only."""
    from local_assistant import setup

    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    her_spotify(reads_answer=True)
    window = Scripted(admin=[{"lines": SIGN_IN, "after": ["Spotify: signed in."]}], answers=[True, PASTED])
    wizard, _face = in_the_window(tmp_path, window)
    script = "spotify-login.sh"
    assert wizard._extra_offer("music", [str(setup.REPO / "scripts" / script)], window=wizard._in_window(script))
    offer, paste = window.sent("screen")
    assert f"$ {SUDO} {ESTIA} spotify-login" in str(offer["lead"])
    assert paste["id"] == "spotify_paste" and paste["kind"] == "hidden"
    assert AUTHORIZE in str(paste["lead"]) and paste["ask"] == "3. Paste the address here:"
    [admin] = window.sent("admin")
    assert admin["argv"] == [ESTIA, "spotify-login"] and admin["open"] is True and admin["why"] == prompts.ADMIN_WHY_HER
    assert window.inputs == [f"{PASTED}\n"]
    assert not any(PASTED in str(m) for m in window.messages if m["t"] != "admin_input")


def her_spotify(*, reads_answer: bool) -> None:
    """Her live release's Spotify sign-in, under the test's root: one that reads the address on a marked line
    (spotify_auth.read_answer), or one from before it (input() with no line end)."""
    from local_assistant.hostsec import estia_root

    source = estia_root() / ownuser.CURRENT.relative_to("/") / "src" / "local_assistant" / "spotify_auth.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def read_answer(prompt: str) -> str:\n" if reads_answer else "address = input(prompt)\n")


def test_her_release_from_before_the_windows_spotify_sign_in_names_it_for_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Her release from before the marked line (`input("3. Paste the address here: ")`, no line end): the window
    would wait for a question that never comes, and the command for its input. Setup names the command for Terminal
    instead, in the log and the closing list (review FRIEND-8)."""
    from local_assistant import setup

    monkeypatch.setattr(Wizard, "own_user", lambda self: True)
    her_spotify(reads_answer=False)
    window = Scripted(answers=[True])
    wizard, _face = in_the_window(tmp_path, window)
    script = "spotify-login.sh"
    wizard._extra_offer("music", [str(setup.REPO / "scripts" / script)], window=wizard._in_window(script))
    assert not window.sent("admin")
    text = prompts.SETUP_WINDOW_SPOTIFY_OLD.format(command=f"{SUDO} {ESTIA} spotify-login")
    assert text in [m["line"] for m in window.sent("progress")] and wizard.for_terminal == (text,)


@pytest.mark.runs_commands  # its own program, never a script of the repo
def test_a_command_of_yours_that_asks_on_a_marked_line_gets_its_answer_on_its_stdin(tmp_path: Path) -> None:
    """Before her account the sign-in runs as you, streamed: its question line (the mark) is a screen, never a line in
    the log, and the answer goes to its stdin; the lines before it are given to the screen."""
    import sys

    program = (
        "import sys\n"
        "print('1. Open this address:'); print('https://example.org/a')\n"
        "print('\\x1f? 3. Paste the address here:', flush=True)\n"
        "print('read', len(sys.stdin.readline().strip()), 'characters')\n"
    )
    window = Scripted()
    _wizard, face = in_the_window(tmp_path, window)
    asked: list[tuple[str, list[str]]] = []
    done = face._stream(
        [sys.executable, "-c", program],
        input=None,
        cwd=None,
        answer=lambda q, before: noted(asked, (q, before), "abcd\n"),
    )
    assert done.returncode == 0
    assert asked == [("3. Paste the address here:", ["1. Open this address:", "https://example.org/a"])]
    lines = [str(m["line"]) for m in window.sent("progress")]
    assert "read 4 characters" in lines and not any("Paste" in line for line in lines)


# ------------------------------------------------------------------ projects and the extras list on screens (C1, E16, E17)


def test_the_config_steps_projects_question_and_folder_are_screens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1: a config step without the plan's pick asks projects in the window on its own screens (the yes or no, the
    folder picker, the level), never a line typed with Terminal's [y/N]."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "dev").mkdir()
    window = Scripted(answers=[True, str(tmp_path / "dev"), "docs"])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.projects() == (str(tmp_path / "dev"), "docs")
    assert [m["id"] for m in window.sent("screen")] == ["projects", "projects_folder", "projects_level"]
    assert window.sent("screen")[1]["kind"] == "folder"


def test_a_no_to_projects_in_the_window_asks_nothing_more(tmp_path: Path) -> None:
    window = Scripted(answers=[False])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.projects() is None
    assert [m["id"] for m in window.sent("screen")] == ["projects"]
    assert wizard.answers.get("projects") is None  # asked each time, as Terminal asks it


def test_her_projects_folder_is_picked_on_the_windows_screens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E17: the projects extra in her own macOS account: the folder and the level on their screens, then her config's
    lines and the share."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "dev").mkdir()
    window = Scripted(answers=[str(tmp_path / "dev"), "summary"])
    wizard, _face = in_the_window(tmp_path, window)
    where = wizard._projects_where()
    assert where is not None and (where.folder, where.level) == (tmp_path / "dev", "summary")


def test_the_extras_list_is_a_screen_of_switches_all_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E16: a re-run without the plan's pick lists the Extra Features as the window's switches, none on; the ones
    picked get their steps, one at a time."""
    from local_assistant import setup_extras

    window = Scripted(answers=[["lights", "chords"]])
    wizard, _face = in_the_window(tmp_path, window)
    stepped: list[str] = []
    monkeypatch.setattr(setup_extras.ExtrasSteps, "_one_extra", lambda self, name, chosen: noted(stepped, name, False))
    wizard.extras_list()
    [screen] = window.sent("screen")
    assert screen["id"] == "extras_list" and screen["kind"] == "switches"
    options = screen["options"]
    assert isinstance(options, list) and all(not option["on"] for option in options)
    assert [option["id"] for option in options] == list(setup_extras.FEATURES)
    assert stepped == ["chords", "lights"]  # in the list's order


def test_the_extras_list_has_the_picks_not_gone_through_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """stops-reruns-extras-pick-lost: after a stop before the extras, the window's switches have the earlier run's
    picks that were not gone through on (its lead names them), never all off."""
    from local_assistant import setup_extras
    from local_assistant.setup_answers import Answers

    window = Scripted(answers=[["web"]])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.answers = Answers(None, {"extras": ("web", "maps", "lights")})
    wizard.answers.mark_done("extra_maps")
    monkeypatch.setattr(setup_extras.ExtrasSteps, "_one_extra", lambda self, name, chosen: False)
    wizard.extras_list()
    [screen] = window.sent("screen")
    on = [option["id"] for option in screen["options"] if option["on"]]
    assert on == ["web", "lights"]
    assert "Picked before, not set up yet: Web search, Lights and plugs." in str(screen["lead"])


# ------------------------------------------------------------------ a step's own screen, answered (review W03)


def answered_after_shown(window: Scripted, qid: str) -> bool:
    """The window got a `checked` ok for this screen after it showed it: the page leaves the greyed screen for the
    step's log (its dots, its Stop here), never frozen until the next screen."""
    shown = [i for i, m in enumerate(window.messages) if m["t"] == "screen" and m["id"] == qid]
    return bool(shown) and any(
        m["t"] == "checked" and m["id"] == qid and m["ok"] is True and m["live"] is False
        for m in window.messages[shown[-1] + 1 :]
    )


@pytest.mark.parametrize("yes", [True, False])
def test_the_security_steps_yes_or_no_leaves_its_screen(tmp_path: Path, yes: bool) -> None:
    window = Scripted(answers=[yes])
    wizard, _face = in_the_window(tmp_path, window)
    assert wizard.harden_ask()("Why it matters.\nTurn it on?", True) is yes
    assert answered_after_shown(window, "harden")


@pytest.mark.parametrize("yes", [True, False])
def test_her_updates_question_leaves_its_screen(tmp_path: Path, yes: bool) -> None:
    window = Scripted(admin=[{"lines": [REVIEW_ASK], "after": ["✓ live"], "code": 0 if yes else 1}], answers=[yes])
    wizard, _face = in_the_window(tmp_path, window)
    wizard._deploy_in_window([SUDO, DEPLOY, "--ask-lines"])
    assert answered_after_shown(window, "deploy_review")
    kinds = [m["t"] for m in window.messages]
    assert kinds.index("checked") < kinds.index("admin_input")  # the page leaves it before the update goes on


@pytest.mark.parametrize("again", [True, False])
def test_try_again_or_not_now_leaves_the_refused_screen(tmp_path: Path, again: bool) -> None:
    window = Scripted(admin=[{"refuse": True}, {"code": 0}], answers=[again])
    wizard, _face = in_the_window(tmp_path, window)
    wizard.run([SUDO, ESTIA, "restart"])
    assert answered_after_shown(window, "admin_refused")


def test_continue_at_the_kept_answers_leaves_their_screen(tmp_path: Path) -> None:
    """A re-run with every answer kept: Kept, then Continue, stayed greyed through the resumed download."""
    import argparse

    from local_assistant.setup_answers import Answers

    window = Scripted(answers=[[]])
    wizard, face = in_the_window(tmp_path, window)
    wizard.answers = Answers(None, {"name": "Maya"})
    assert face.offer_changes(argparse.Namespace(redo=[])) is True
    assert answered_after_shown(window, setup_window.KEPT)


def test_the_new_pin_screen_has_a_later_and_its_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """signal-lock-pin-window-no-skip (round 1): the window's new-PIN screen had no way out (q was refused as "At
    least 4 characters", and no Stop): a visible hang. Its Later leaves the lock to the security step, as q does in
    Terminal; its lead says the rules and the password manager."""
    from local_assistant import harden

    set_pins: list[str] = []
    monkeypatch.setattr(harden, "set_pin", lambda *args, **kwargs: noted(set_pins, "set", (True, "")))
    window = Scripted(answers=[True, ""])  # yes to the lock, then Later on the new PIN
    wizard, _face = in_the_window(tmp_path, window)
    wizard.registration_lock("+306900000000")
    screens = [screen for screen in window.sent("screen") if screen["id"] == "lock_pin"]
    assert screens and screens[0]["later"] == prompts.SETUP_WINDOW_LATER
    assert screens[0]["lead"] == prompts.SETUP_WINDOW_LOCK_PIN
    assert prompts.SETUP_WINDOW_LOCK_PIN == "4 to 64 characters, no spaces. Keep it in your password manager."
    assert set_pins == [] and not [screen for screen in window.sent("screen") if screen["id"] == "lock_pin_again"]


def test_two_pins_that_differ_say_so_on_the_next_new_pin_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review S03: the mismatch went as a refusal of "New PIN again", a screen the page had marked done, so it was
    dropped, and New PIN came back with no word why. It comes on that New PIN screen, after it is drawn."""
    from local_assistant import harden

    set_pins: list[str] = []
    monkeypatch.setattr(harden, "set_pin", lambda *args, **kwargs: noted(set_pins, "set", (True, "")))
    window = Scripted(answers=[True, "731904", "731905", "", ""])  # the lock's yes, two PINs, then Later
    wizard, _face = in_the_window(tmp_path, window)
    wizard.registration_lock("+306900000000")
    sent = [
        (m["t"], m["id"], m.get("ok"), m.get("message")) for m in window.messages if m["t"] in ("screen", "checked")
    ]
    at = [i for i, item in enumerate(sent) if item[:2] == ("screen", "lock_pin")]
    assert len(at) == 2 and sent[at[1] + 1] == ("checked", "lock_pin", False, prompts.SETUP_PIN_MISMATCH)
    assert ("checked", "lock_pin_again", False, prompts.SETUP_PIN_MISMATCH) not in sent
    assert set_pins == []


def test_a_pin_signal_will_not_set_says_why_on_its_screen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review S03: "Registration Lock: not set" showed without signal-cli's reason (only the log had it)."""
    from local_assistant import harden

    why = "Failed to set the PIN: no answer within 90 s"
    monkeypatch.setattr(harden, "set_pin", lambda *args, **kwargs: (False, why))
    window = Scripted(answers=[True, "731904", "731904", False])  # the lock's yes, the PIN twice, then Skip
    wizard, _face = in_the_window(tmp_path, window)
    wizard.registration_lock("+306900000000")
    [retry] = [m for m in window.sent("screen") if m["id"] == "lock_retry"]
    assert why in str(retry["lead"]) and "{" not in str(retry["lead"])
    assert "731904" not in json.dumps(window.messages)


# ------------------------------------------------------------------ Google Maps: its Terminal path in 0.9.8 (E11-E13)


def test_google_maps_is_named_for_terminal_in_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner, 29 Oct: Google Maps keeps its Terminal path in 0.9.8 (its sign-in and its script ask on a terminal): in
    the window nothing of it runs; its commands are named for Terminal, with Copy in the closing list, and places and
    routes come from OpenStreetMap until then."""
    from local_assistant import setup_extras

    monkeypatch.setattr("local_assistant.setup_extras.shutil.which", lambda name: None)
    window = Scripted()
    wizard, _face = in_the_window(tmp_path, window)
    wizard._google_maps()
    assert not window.sent("screen") and not window.sent("admin")
    said = "\n".join(str(m["line"]) for m in window.sent("progress"))
    script = setup_extras.REPO / "scripts" / "setup-google-maps.sh"
    for command in ("brew install --cask gcloud-cli", "gcloud auth login --no-launch-browser", str(script)):
        assert f"$ {command}" in said
    assert prompts.SETUP_WINDOW_MAPS_TERMINAL in said
    monkeypatch.setattr(setup_extras, "_voice_installed", lambda: False)
    wizard.closing()
    [done] = window.sent("done")
    assert any(prompts.SETUP_WINDOW_MAPS_TERMINAL in line and f"$ {script}" in line for line in done["lines"])


def test_no_test_starts_osascript() -> None:
    """Setup never asks macOS for admin rights itself (the window's app does, through its one fixed script): a test
    that would start osascript is refused before it starts, as one that would start sudo is."""
    with pytest.raises(AssertionError, match="osascript"):
        subprocess.run(["/usr/bin/osascript", "-e", "return 1"], check=False)

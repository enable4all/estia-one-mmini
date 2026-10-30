"""Setup started by an AI's command tool (setup_ai): no terminal, at the Mac's own screen, the Estia Setup window as
setup's only face. The route and its refusals, the relay and the channel (what the AI reads), the one end point (one
end line for every end), the window face without a Terminal behind it, and the guards that deny every terminal read
and setup's own sudo at run time."""

import contextlib
import dataclasses
import fcntl
import io
import json
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from local_assistant import (
    hostsec,
    ownuser,
    prompts,
    setup,
    setup_admin,
    setup_ai,
    setup_answers,
    setup_face,
    setup_plan,
    setup_window,
)
from local_assistant.hostsec import SUDO
from local_assistant.setup import Wizard
from local_assistant.setup_questions import Checked, Question
from local_assistant.setup_window import WindowFace
from tests.setup_helpers import ACI, BOT, her_own_user, noted, refuse
from tests.test_setup_admin import Scripted
from tests.test_setup_window import FAKE_WINDOW, a_window, no_window_left, received

pytestmark = pytest.mark.usefixtures("no_real_ollama", "no_system_paths", "no_window_left")
assert no_window_left  # the window test file's own fixture: every stand-in ended with its test

ROOT = Path(__file__).resolve().parents[1]


def flat(text: str) -> str:
    """A text as the channel writes it: one line."""
    return " ".join(text.split())


@pytest.fixture(autouse=True)
def config_of_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An AI-run takes only the usual config path: here, the test's own (never the Mac's)."""
    path = tmp_path / "config" / "config.toml"
    monkeypatch.setattr(setup, "DEFAULT_CONFIG_PATH", path)
    return path


def an_ai_run(argv: list[str] | None = None) -> tuple[int, list[str]]:
    """setup's AI-run in this process, as main's grandchild runs it: its code and the channel's lines."""
    channel = setup_ai.Channel()
    return setup.ai_run(list(argv or []), channel), channel.lines


def steps(monkeypatch: pytest.MonkeyPatch, **given: Callable[..., Any]) -> None:
    """Every step after the window opens stubbed (the plan too), each as `given` names it."""
    defaults: dict[str, Callable[..., Any]] = {
        "plan": lambda self: None,
        "ollama": lambda self: None,
        "model": lambda self: None,
        "signal_account": lambda self: BOT,
        "owner": lambda self, number: ACI,
        "config": lambda self, number, aci: None,
        "hand_over": lambda self: None,
        "always_on": lambda self: True,
        "security": lambda self: None,
        "calls_step": lambda self: None,
        "extras_setup": lambda self: None,
        "personality": lambda self: None,
        "restart_her": lambda self: None,
        "check": lambda self: 0,
        "voice_end": lambda self, calls=False: None,
        "listed_models": lambda self: [],
        "closing": lambda self: self.face.done(["Done."]),
    }
    for name, step in {**defaults, **given}.items():
        monkeypatch.setattr(Wizard, name, step)
    monkeypatch.setattr("time.sleep", lambda seconds: None)


def end_only(lines: list[str]) -> str:
    """The run's end line, checked to be its only one: the first line is A1, no other is a ✓ or ✗."""
    assert lines[0] == flat(prompts.SETUP_AI_STARTED)
    assert [line for line in lines[1:-1] if line.startswith(("✗", "✓"))] == []
    return lines[-1]


# ------------------------------------------------------------------ the route, before anything runs


@pytest.fixture
def no_terminal(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """main without a terminal; the detach recorded instead of forking the test."""
    detached: list[object] = []
    monkeypatch.setattr(setup, "has_terminal", lambda: False)
    monkeypatch.setattr(setup_ai, "detach", lambda body: noted(detached, body, 0))
    monkeypatch.setattr(setup_ai, "sandboxed", lambda: False)
    monkeypatch.setattr(setup_window, "at_the_desk", lambda: True)
    return detached


@pytest.mark.parametrize(
    ("argv", "words"),
    [
        (["--plain"], prompts.SETUP_AI_TERMINAL_FLAG.format(flag="--plain")),
        (["--terminal"], prompts.SETUP_AI_TERMINAL_FLAG.format(flag="--terminal")),
        (["--extras"], prompts.SETUP_AI_EXTRAS),
        (["--answers", "/nonexistent/answers.toml"], prompts.SETUP_AI_FILES),
        (["--config", "/nonexistent/config.toml"], prompts.SETUP_AI_FILES),
    ],
    ids=["plain", "terminal", "extras", "answers", "config"],
)
def test_a_run_for_terminal_is_refused_in_one_line_before_anything(
    argv: list[str],
    words: str,
    no_terminal: list[object],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Fable, 29 Oct (R2): --extras and the rest never reach PlainFace on the AI's pipe: refused before anything,
    whatever the AI put on stdin left unread."""
    typed = io.StringIO("y\n" * 5)
    monkeypatch.setattr("sys.stdin", typed)
    assert setup.main(argv) == setup_ai.REFUSED
    out, err = capfd.readouterr()
    assert (out, err) == (words + "\n", "") and no_terminal == [] and typed.read() == "y\n" * 5


@pytest.mark.parametrize(
    ("key", "words"),
    [
        ("SSH_CONNECTION", prompts.SETUP_AI_NOT_AT_DESK),
        ("SSH_TTY", prompts.SETUP_AI_NOT_AT_DESK),
        ("TMUX", prompts.SETUP_AI_IN_SESSION),
        ("HERDR_ENV", prompts.SETUP_AI_IN_SESSION),
    ],
)
def test_away_from_the_macs_own_screen_an_ai_run_is_refused(
    key: str, words: str, no_terminal: list[object], monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(key, "1")
    assert setup.main([]) == setup_ai.REFUSED
    assert capfd.readouterr().out == words + "\n" and no_terminal == []


def test_no_desktop_a_sandbox_another_home_or_root_is_refused(
    no_terminal: list[object], monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(setup_window, "at_the_desk", lambda: False)
    assert setup.main([]) == setup_ai.REFUSED
    assert capfd.readouterr().out == prompts.SETUP_AI_NOT_AT_DESK + "\n"
    monkeypatch.setattr(setup_window, "at_the_desk", lambda: True)
    monkeypatch.setattr(setup_ai, "sandboxed", lambda: True)
    assert setup.main([]) == setup_ai.REFUSED
    assert capfd.readouterr().out == prompts.SETUP_AI_SANDBOX + "\n"
    monkeypatch.setattr(setup_ai, "sandboxed", lambda: False)
    monkeypatch.setenv("HOME", "/tmp/not-yours")
    assert setup.main([]) == setup_ai.REFUSED
    assert capfd.readouterr().out == prompts.SETUP_AI_HOME + "\n"
    monkeypatch.undo()
    assert no_terminal == []


def test_root_is_refused_without_a_terminal_too(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert setup_ai.refused(argparse.Namespace(), config=Path("/c")) == prompts.SETUP_NOT_ROOT


def test_at_the_desk_setup_is_detached_and_a_dry_run_stays_in_terminal(
    no_terminal: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert setup.main([]) == 0 and len(no_terminal) == 1
    monkeypatch.setattr(setup, "run_setup", lambda argv: 7)
    assert setup.main(["--dry-run"]) == 7 and len(no_terminal) == 1  # as today: prints, asks nothing


def test_the_sandbox_check_reads_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """sandbox_check on its own pid (libSystem): not sandboxed here; a missing symbol is no sandbox."""
    assert setup_ai.sandboxed() is False
    sandbox = Path("/usr/bin/sandbox-exec")
    if sandbox.exists():
        script = "from local_assistant import setup_ai; print(setup_ai.sandboxed())"
        done = subprocess.run(
            [str(sandbox), "-p", "(version 1)(allow default)", sys.executable, "-c", script],
            capture_output=True, text=True, check=False, cwd=ROOT,
        )  # fmt: skip
        assert done.stdout.strip() == "True", done.stderr

    class NoSymbol:
        def __getattr__(self, name: str) -> object:
            raise AttributeError(name)

    monkeypatch.setattr("local_assistant.setup_ai.ctypes.CDLL", lambda name: NoSymbol())
    assert setup_ai.sandboxed() is False


def test_a_tests_roots_stay_only_where_pytest_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """ESTIA_ROOT, ESTIA_TEST_ROOT and ESTIA_OWNER_SUDO: kept under pytest, dropped from an AI-run elsewhere (the
    environment cannot fake a module this process loaded; PYTEST_CURRENT_TEST alone is not pytest)."""
    monkeypatch.setenv("ESTIA_OWNER_SUDO", "1")
    setup_ai.test_roots()
    assert os.environ.get("ESTIA_ROOT") and os.environ.get("ESTIA_OWNER_SUDO") == "1"
    monkeypatch.delitem(sys.modules, "pytest")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "faked")
    setup_ai.test_roots()
    assert not any(key in os.environ for key in setup_ai.TEST_KEYS)


# Every environment key Estia's own code reads by name, and what an AI that starts setup with it set changes (design 4).
# The programs setup starts (brew, uv, signal-cli's Java, curl) read their own keys, as from any parent: an AI that
# runs as you can edit the checkout and its scripts anyway (SECURITY.md, the threat model).
ENVIRONMENT = {
    "ESTIA_ROOT": "a test's system root: dropped in an AI-run unless pytest runs it (setup_ai.test_roots)",
    "ESTIA_TEST_ROOT": "a test's fixed tools' root: dropped in an AI-run unless pytest runs it",
    "ESTIA_OWNER_SUDO": "doctor reads sudo's listing from stdin, as root: dropped in an AI-run unless pytest runs it",
    "HOME": "where your files go: an AI-run with another HOME than your account's is refused (SETUP_AI_HOME)",
    "USER": "the account a web key's keychain copy is saved under: yours, as the AI runs as you",
    "SHELL": "which of your login profiles gets Homebrew's line",
    "SUDO_USER": "who asked, read only by root (her deploy, her move), from sudo or macOS's box, never from the AI",
    "TERM": "Terminal's menus or plain lines: an AI-run has no Terminal face",
    "TERM_PROGRAM": "which app comes to the front once the window ends: nothing is asked or run by it",
    "LA_": "a launchd file's values for ownuser render, run by install-daemon.sh as you: one you could write anyway",
    **{key: "a remote login: an AI-run is refused (SETUP_AI_NOT_AT_DESK)" for key in setup_plan.SSH_KEYS},
    **{key: "screen, tmux or herdr: an AI-run is refused (SETUP_AI_IN_SESSION)" for key in setup_plan.SESSION_KEYS},
    **{key: "Terminal's menus (gum's look): an AI-run has no Terminal face" for key in setup_face.GUM_ENV},
    **{key: "the window's only environment: its language and its temporary folder" for key in setup_window.WINDOW_ENV},
}


def test_every_environment_key_estia_reads_is_named_with_what_it_changes() -> None:
    """Fable, 29 Oct (4): the keys only tests set are dropped outside pytest (test_roots); every other key Estia's
    code reads by name is named above with why an AI that sets it gains nothing it has not already. A new one fails
    here until it is named."""
    read: set[str] = set()
    for path in sorted((ROOT / "src" / "local_assistant").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        read |= set(re.findall(r"""environ(?:\.get|\.pop)?[(\[]\s*["']([A-Z_]+)["']""", text))
        read |= set(re.findall(r"""getenv\(\s*["']([A-Z_]+)["']""", text))
        read |= {"LA_"} if 'environ.get(f"LA_' in text else set()
    read |= {*setup_plan.SSH_KEYS, *setup_plan.SESSION_KEYS, *setup_face.GUM_ENV, *setup_window.WINDOW_ENV}
    assert sorted(read - set(ENVIRONMENT)) == []
    assert set(setup_ai.TEST_KEYS) <= set(ENVIRONMENT)


# ------------------------------------------------------------------ the relay and the channel


def test_the_relay_passes_each_line_and_ends_with_setups_last_code() -> None:
    read, write = os.pipe()
    out_read, out_write = os.pipe()
    channel = setup_ai.Channel(write)
    channel.line("Step 1 of 9: Ollama")
    channel.end("✓ Setup ended: its check shows no ✗.", 0)
    os.close(write)
    assert setup_ai.relay(read, out_write) == 0
    os.close(out_write)
    assert os.read(out_read, 4096).decode() == "Step 1 of 9: Ollama\n✓ Setup ended: its check shows no ✗.\n"


def test_a_setup_killed_before_its_last_word_is_one_line_and_one() -> None:
    read, write = os.pipe()
    out_read, out_write = os.pipe()
    setup_ai.Channel(write).line("Step 2 of 9: her model")
    os.close(write)
    assert setup_ai.relay(read, out_write) == 1
    os.close(out_write)
    assert os.read(out_read, 4096).decode().splitlines() == ["Step 2 of 9: her model", prompts.SETUP_AI_NO_LAST_WORD]


def test_the_channel_never_waits_for_an_ai_that_stopped_reading() -> None:
    """A full pipe (a relay that does not read) drops the line within its half second, and the next ones at once:
    the window never waits for the AI. A relay gone (the AI's command ended) is no error."""
    read, write = os.pipe()
    os.set_blocking(write, False)
    with contextlib.suppress(BlockingIOError):
        while True:
            os.write(write, b"x" * 65536)
    channel = setup_ai.Channel(write)
    started = time.monotonic()
    channel.line("Step 3 of 9: her Signal account")
    channel.line("Step 4 of 9: her config")
    assert time.monotonic() - started < 2 and channel.fd is None
    assert channel.lines == ["Step 3 of 9: her Signal account", "Step 4 of 9: her config"]
    os.close(read)
    gone = setup_ai.Channel(write)
    gone.end("✗ Setup ended.", 1)  # EPIPE: the relay is gone
    assert gone.fd is None
    os.close(write)


def test_the_log_has_this_runs_lines_only_and_is_yours_alone(tmp_path: Path) -> None:
    path = tmp_path / "estia-setup" / setup_ai.LOG
    path.parent.mkdir()
    path.write_text("a line of the run before\n")
    channel = setup_ai.Channel()
    channel.line(prompts.SETUP_AI_STARTED)
    channel.open_log(path)
    channel.line("Step 1 of 9: Ollama")
    channel.end(prompts.SETUP_AI_DONE, 0)
    assert path.read_text().splitlines() == [
        flat(prompts.SETUP_AI_STARTED),
        "Step 1 of 9: Ollama",
        prompts.SETUP_AI_DONE,
    ]
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700


# ------------------------------------------------------------------ the one end point


def stop_with(error: BaseException) -> Callable[..., None]:
    def step(self: Wizard, *args: object) -> None:
        raise error

    return step


def closed_window(self: Wizard) -> None:
    assert isinstance(self.face, WindowFace)
    self.face.closed = True
    raise KeyboardInterrupt


def terminated(self: Wizard) -> None:
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(0.01)


def typed(self: Wizard) -> None:
    input("A line in Terminal? ")


def move_failed(words: str) -> Callable[[Wizard], bool]:
    """always_on as p6-b's move that did not go through: its stop's words kept (move_stop), then False."""

    def step(self: Wizard) -> bool:
        self.move_stop = words
        return False

    return step


def check_not_run(self: Wizard) -> int:
    """Her check with admin rights not given at macOS's box: nothing ran, so no ✗ line was listed (c-3, round 3)."""
    self.check_not_run = True
    return 1


ENDS: list[tuple[str, dict[str, Callable[..., Any]], list[str], str, int]] = [
    ("done", {}, [], prompts.SETUP_AI_DONE, 0),
    ("check-fails", {"check": lambda self: 1}, [], prompts.SETUP_AI_DONE_FAILS, 1),
    # once she moved scripts/doctor.sh only points on: her doctor's own command is named (c-3, round 3)
    (
        "check-fails-moved",
        {"check": lambda self: 1, "own_user": lambda self: True},
        [],
        prompts.SETUP_AI_DONE_FAILS_HERS,
        1,
    ),
    ("check-not-run", {"check": check_not_run, "own_user": lambda self: True}, [], prompts.SETUP_AI_DONE_NOT_RUN, 1),
    ("stop-fixed", {"model": stop_with(SystemExit(prompts.SETUP_ADMIN_UNKNOWN))}, [], "", 1),
    ("stop-filled", {"model": stop_with(SystemExit("✗ Ollama: +306900000000 is not hers"))}, [], "", 1),
    # p6-b: a move that did not go through is a stop with its own words (move_stop), drawn as step 1's stops are
    ("move-failed", {"always_on": move_failed(prompts.SETUP_MOVE_FAILED.format(move="migrate.sh"))}, [], "", 1),
    ("move-refused", {"always_on": move_failed(prompts.SETUP_MOVE_REFUSED)}, [], "", 1),
    ("exit-1", {"model": stop_with(SystemExit(1))}, [], "", 1),
    ("stop-here", {"ollama": stop_with(KeyboardInterrupt())}, [], prompts.SETUP_AI_STOP_HERE, 130),
    ("closed", {"ollama": closed_window}, [], prompts.SETUP_AI_CLOSED, 1),
    ("terminated", {"ollama": terminated}, [], prompts.SETUP_AI_ENDED_OUTSIDE, 1),
    ("bug", {"model": stop_with(KeyError("+306900000000"))}, [], "", 1),
    ("denied", {"ollama": typed}, [], flat(prompts.SETUP_AI_DENIED.format(what=prompts.SETUP_AI_DENIED_LINE)), 1),
    ("plain", {}, ["--plain"], prompts.SETUP_AI_TERMINAL_FLAG.format(flag="--plain"), 2),
    ("unknown-redo", {}, ["--redo", "nope"], "", 2),
    ("argparse", {}, ["--no-such-flag"], "", 2),
]


@pytest.mark.parametrize(("case", "given", "argv", "line", "code"), ENDS, ids=[end[0] for end in ENDS])
def test_every_end_of_an_ai_run_says_exactly_one_end_line(
    case: str,
    given: dict[str, Callable[..., Any]],
    argv: list[str],
    line: str,
    code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    folder = a_window(tmp_path, monkeypatch)
    steps(monkeypatch, **given)
    got, lines = an_ai_run(argv)
    end = end_only(lines)
    assert got == code
    if case == "stop-fixed":
        assert end.startswith("✗ Setup stopped at step 2 of 8 (her model): Setup window: it ended while a command")
        assert end.endswith("[SETUP_ADMIN_UNKNOWN] Every answer is kept; to go on, run scripts/setup.sh again.")
    elif case == "stop-filled":
        assert end == prompts.SETUP_AI_STOPPED.format(
            where="at step 2 of 8 (her model)", why=prompts.SETUP_AI_WINDOW_SAYS
        )
    elif case == "move-failed":  # its words name the move's script: the window shows them
        assert end == prompts.SETUP_AI_STOPPED.format(
            where="at step 5 of 8 (always on)", why=prompts.SETUP_AI_WINDOW_SAYS
        )
    elif case == "move-refused":  # setup's fixed words, named
        why = f"{flat(prompts.SETUP_MOVE_REFUSED.removeprefix('✗ '))} [SETUP_MOVE_REFUSED]"
        assert end == prompts.SETUP_AI_STOPPED.format(where="at step 5 of 8 (always on)", why=why)
    elif case == "exit-1":
        assert end == flat(
            prompts.SETUP_AI_ENDED.format(where="at step 2 of 8 (her model)", code=1, why=prompts.SETUP_AI_WINDOW_SAYS)
        )
    elif case == "bug":
        assert re.fullmatch(r"✗ Setup: an error in its own code \(KeyError at setup\.py:\d+\);.*", end), end
    elif case == "unknown-redo":
        assert end.startswith("✗ Setup: --redo names a question setup does not know. The questions: ")
        assert "nope" not in end
    elif case == "argparse":  # no window opened: it cannot show why
        assert end == flat(
            prompts.SETUP_AI_ENDED.format(
                where=prompts.SETUP_AI_BEFORE_STEPS, code=2, why=prompts.SETUP_AI_NO_WINDOW_SAYS
            )
        )
    else:
        assert end == flat(line)
    assert BOT not in "\n".join(lines)
    out, err = capfd.readouterr()
    assert BOT not in out + err and "+30 690" not in out + err
    screens = [m for m in received(folder) if m["t"] == "screen" and m["id"] == "stop"]
    stops = ("stop-here", "terminated", "bug", "move-failed", "move-refused", "exit-1")
    if case in stops:  # the window, open, shows where it stopped
        again = prompts.SETUP_WINDOW_STOP_AGAIN_AI.format(command=setup_window.setup_again_command())
        assert len(screens) == 1 and again in str(screens[0]["lead"])
    if case == "exit-1":  # a step's own exit: its ✗ line is in the log, the screen names it
        assert str(screens[0]["title"]).startswith("Setup: this step did not finish")
    if case in ("move-failed", "move-refused"):  # the move's own stop screen
        assert str(screens[0]["title"]).startswith("Always on: the move did not")


def test_an_ai_run_never_reads_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever the AI puts on setup's stdin stays unread: every answer is the window's."""
    a_window(tmp_path, monkeypatch, {"answer": "Juno"})
    steps(monkeypatch, plan=lambda self: self.answer("name"))
    typed = io.StringIO("y\n" * 50)
    monkeypatch.setattr("sys.stdin", typed)
    code, lines = an_ai_run()
    assert code == 0 and typed.read() == "y\n" * 50
    assert "Juno" not in "\n".join(lines)


def test_an_early_end_says_its_own_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_of_its_own: Path
) -> None:
    a_window(tmp_path, monkeypatch)
    steps(monkeypatch)
    monkeypatch.setattr(setup, "memory_gb", lambda: 8.0)
    code, lines = an_ai_run()
    assert (code, end_only(lines)) == (1, "✗ This Mac has 8 GB of memory. She needs 16 GB or more.")
    monkeypatch.setattr(setup, "memory_gb", lambda: 32.0)
    config_of_its_own.parent.mkdir()
    config_of_its_own.write_text('name = "Alex Example\n')  # a mistake: the config does not load
    code, lines = an_ai_run()
    assert (code, end_only(lines)) == (1, prompts.SETUP_AI_CONFIG)


@pytest.mark.parametrize(
    ("redo", "said"),
    [
        (["signal"], [prompts.SETUP_REDO_SIGNAL]),
        (["about_you", "home_area"], [prompts.SETUP_REDO_PROFILE.format(title=t) for t in ("About you", "Home area")]),
    ],
    ids=["signal", "handed-over"],
)
def test_a_redo_that_changes_nothing_ends_an_ai_run_with_its_own_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_of_its_own: Path, redo: list[str], said: list[str]
) -> None:
    """The join of p6-b's early end (a --redo that changes nothing on an install set up ends before the window opens,
    exit 0) with the AI-run: its lines went to the stop face (nowhere), and the AI read "✗ Setup ended before its
    first step (exit code 0) ... run scripts/setup.sh again", which does the same again. Its own lines are the end."""
    folder = a_window(tmp_path, monkeypatch)
    steps(monkeypatch)
    config_of_its_own.parent.mkdir()
    config_of_its_own.write_text(f'bot_number = "{BOT}"\nallowed_acis = ["{ACI}"]\n')
    config_of_its_own.chmod(0o600)
    code, lines = an_ai_run([arg for qid in redo for arg in ("--redo", qid)])
    assert (code, end_only(lines)) == (0, flat(" ".join([*said, prompts.SETUP_REDO_NOTHING])))
    assert received(folder) == []  # no window: nothing to ask
    assert BOT not in "\n".join(lines) and ACI not in "\n".join(lines)


def test_an_empty_stop_names_no_fixed_sentence() -> None:
    """`fixed` names a stop by its exact words; "" matched p6-b's empty prompt (SETUP_SMALLER_MODEL_ADVICE), so a stop
    with no words read "[SETUP_SMALLER_MODEL_ADVICE]" to the AI."""
    assert prompts.SETUP_SMALLER_MODEL_ADVICE == ""
    assert setup_ai.fixed("") == ""
    assert setup_ai.fixed(prompts.SETUP_ADMIN_UNKNOWN) == "SETUP_ADMIN_UNKNOWN"


def test_a_second_run_while_one_is_open_never_touches_its_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    steps(monkeypatch)
    folder = setup_answers.default_path().parent
    setup_answers.private_folders(folder)
    (folder / setup_ai.LOG).write_text("the open run's lines\n")
    fd = os.open(folder / "answers.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        code, lines = an_ai_run()
    finally:
        os.close(fd)
    assert (code, end_only(lines)) == (1, flat(prompts.SETUP_AI_IN_USE))
    assert (folder / setup_ai.LOG).read_text() == "the open run's lines\n"


def test_an_answers_file_setup_cannot_trust_stops_an_ai_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fable, 29 Oct: in Terminal a refused answers file is said in one line and setup goes on keeping nothing; an
    AI-run would then run on with nothing kept, no log and no one told. It stops instead, in fixed words (never the
    path, which names the account, nor the reason), before its window."""
    steps(monkeypatch)
    folder = setup_answers.default_path().parent
    shutil.rmtree(folder, ignore_errors=True)  # the tests' own, empty
    folder.parent.mkdir(parents=True, exist_ok=True)
    real = folder.parent / "elsewhere"
    real.mkdir()
    folder.symlink_to(real)  # a link: not a folder setup writes in (setup_answers._folder_problem)
    code, lines = an_ai_run()
    assert (code, end_only(lines)) == (1, flat(prompts.SETUP_AI_ANSWERS_REFUSED))
    assert str(folder) not in "\n".join(lines) and prompts.SETUP_ANSWERS_LINK not in "\n".join(lines)
    assert not (real / setup_ai.LOG).exists() and not list(real.iterdir())


def test_a_run_writes_its_lines_to_its_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a_window(tmp_path, monkeypatch)
    steps(monkeypatch)
    code, lines = an_ai_run()
    log = setup_answers.default_path().with_name(setup_ai.LOG)
    assert code == 0 and log.read_text().splitlines() == lines
    assert lines[1].startswith("In the window:") or lines[1].startswith("Step 1 of 8: Ollama")


@pytest.mark.parametrize(
    ("built", "said", "words"),
    [
        (None, prompts.SETUP_WINDOW_NOT_BUILT.format(why=prompts.SETUP_WINDOW_NO_TOOLS), prompts.SETUP_AI_NO_TOOLS),
        (None, prompts.SETUP_WINDOW_NOT_BUILT.format(why=prompts.SETUP_WINDOW_NO_PROGRAM), "no-program"),
        ("silent", "", prompts.SETUP_AI_NO_WINDOW),
    ],
    ids=["no-command-line-tools", "no-program", "never-ready"],
)
def test_a_window_that_cannot_open_is_a_stop_never_terminal(
    built: str | None, said: str, words: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps(monkeypatch)
    monkeypatch.setattr(setup_window, "READY_SECONDS", 0.5)
    if built is None:
        monkeypatch.setattr(setup_window, "at_the_desk", lambda: True)

        def build(repo: Path, *, dry_run: bool, say: Callable[[str], None]) -> None:
            say(said)

        monkeypatch.setattr(setup_window, "build", build)
    else:
        a_window(tmp_path, monkeypatch, silent=True)
    monkeypatch.setattr(setup_window, "choose_face", refuse)
    code, lines = an_ai_run()
    expected = (
        prompts.SETUP_AI_NOT_BUILT.format(why=prompts.SETUP_WINDOW_NO_PROGRAM) if words == "no-program" else words
    )
    assert (code, end_only(lines)) == (1, flat(expected))


def test_the_window_closed_at_the_kept_screen_is_a8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    answers.set("name", "Maya")
    a_window(tmp_path, monkeypatch, {"close": True})
    steps(monkeypatch)
    code, lines = an_ai_run()
    assert (code, end_only(lines)) == (1, prompts.SETUP_AI_CLOSED)
    assert "In the window: Kept from your last run [kept]" in lines and "Maya" not in "\n".join(lines)


# ------------------------------------------------------------------ the window face, with no Terminal behind it


def ai_face(tmp_path: Path, window: Scripted) -> tuple[Wizard, WindowFace, setup_ai.Run]:
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    ai = setup_ai.Run(setup_ai.Channel())
    wizard.ai, ai.wizard = ai, wizard
    face = WindowFace(window, wizard, refuse)  # type: ignore[arg-type]  # never Terminal
    wizard.face = face
    return wizard, face, ai


class Closing(Scripted):
    """A window closed at its next screen; with `after`, once it showed that line in its log."""

    def __init__(self, after: str = "") -> None:
        super().__init__()
        self.after = after

    def send(self, message: dict[str, object]) -> bool:
        sent = super().send(message)
        if message["t"] == "screen" and not self.after:
            self.incoming[-1:] = [{"t": "closed"}]
        if message["t"] == "progress" and self.after and message["line"] == self.after:
            self.incoming.append({"t": "closed"})
        return sent


def test_a_window_closed_at_a_question_stops_never_asks_in_terminal(tmp_path: Path) -> None:
    window = Closing()
    wizard, face, ai = ai_face(tmp_path, window)
    code = setup_ai.run(ai, lambda: int(wizard.answer("language") is None))
    assert code == 1 and face.closed and ai.channel.lines[-1] == prompts.SETUP_AI_CLOSED


def test_an_answer_that_does_not_fit_its_screen_stops_with_a12(tmp_path: Path) -> None:
    window = Scripted(answers=[["not", "a", "choice"]])
    wizard, _face, ai = ai_face(tmp_path, window)
    assert setup_ai.run(ai, lambda: int(wizard.answer("language") is None)) == 1
    assert ai.channel.lines[-1] == flat(prompts.SETUP_AI_UNFIT)


def test_macos_box_that_could_not_ask_stops_naming_terminals_command(tmp_path: Path) -> None:
    window = Scripted(admin=[{"unrun": True, "code": 1}])
    wizard, _face, ai = ai_face(tmp_path, window)
    estia = str(ownuser.BIN / "estia")
    assert setup_ai.run(ai, lambda: wizard.run([SUDO, estia, "restart"]).returncode) == 1
    assert ai.channel.lines[-1] == flat(prompts.SETUP_AI_NO_BOX.format(command=f"{SUDO} {estia} restart"))
    window = Scripted(admin=[{"unrun": True, "code": 1}])
    wizard, _face, ai = ai_face(tmp_path, window)
    script = str(setup_window.REPO / "scripts" / "harden.sh")
    setup_ai.run(
        ai, lambda: wizard.run([SUDO, "/bin/launchctl", "kickstart"], why=prompts.ADMIN_WHY_SECURITY).returncode
    )
    assert ai.channel.lines[-1] == flat(prompts.SETUP_AI_NO_BOX.format(command=script))


@pytest.mark.parametrize("held", [False, True], ids=["at-once", "held-section"])
def test_macos_box_that_could_not_ask_never_names_an_answer_to_the_ai(tmp_path: Path, held: bool) -> None:
    """Her mail sign-in as her (`estia mail-login <address>`): the mailbox's address is the person's answer from "Mail
    at Microsoft". The window's stop names the whole command, for the person to type in Terminal; the AI's line says
    that the window shows it, never the address."""
    window = Scripted(admin=[{"unrun": True, "code": 1}])
    wizard, face, ai = ai_face(tmp_path, window)
    address = "zq-mailbox@example.invalid"  # a marker, never a real address
    argv = [SUDO, str(ownuser.BIN / "estia"), "mail-login", address]
    with setup_ai.guarded(ai), pytest.raises(SystemExit) as stopped:
        if held:
            with face.stops_held(prompts.SETUP_WINDOW_FINISHES_FIRST):
                wizard.run(argv)
        else:
            wizard.run(argv)
    assert stopped.value.code == prompts.SETUP_AI_NO_BOX.format(command=" ".join(argv)) or held
    assert address not in ai.ending and ai.ending == flat(prompts.SETUP_AI_NO_BOX_WINDOW)


def test_a_sudo_form_the_window_does_not_run_stops_with_a14(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, *a, **k: started.append(argv))
    window = Scripted()
    wizard, _face, ai = ai_face(tmp_path, window)
    assert setup_ai.run(ai, lambda: wizard.run([SUDO, "-s"]).returncode) == 1
    assert ai.channel.lines[-1] == flat(prompts.SETUP_AI_FORM.format(program="-s"))
    assert started == [] and not window.sent("admin")


def test_a_close_while_a_command_streams_interrupts_it_and_stops(tmp_path: Path) -> None:
    window = Closing(after="started")
    wizard, face, ai = ai_face(tmp_path, window)
    marker = tmp_path / "interrupted"
    script = (  # its SIGINT handler is in place before it says started (a shell's trap waits for its sleep)
        "import sys, time\ntry:\n    print('started', flush=True)\n    time.sleep(5)\n"
        "except KeyboardInterrupt:\n    open(sys.argv[1], 'w').close()\n    sys.exit(130)\n"
    )

    def body() -> int:
        return wizard.run([sys.executable, "-c", script, str(marker)]).returncode

    started = time.monotonic()
    assert setup_ai.run(ai, body) == 1
    assert time.monotonic() - started < 4 and marker.exists() and face.closed
    assert ai.channel.lines[-1] == prompts.SETUP_AI_CLOSED


def test_a_close_in_harden_s_held_section_still_tries_her_start_and_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_system_paths: Path
) -> None:
    """Architect, 29 Oct: closed between her stop and her start (the wait after the bootout), nothing raises mid-way:
    the PIN helper and her start each fail as a run, her start is tried, and the end names it for Terminal (a re-run
    leaves a stopped bridge stopped)."""
    her_own_user(no_system_paths)
    window = Scripted(admin=[{"code": 0}])  # her bootout
    _wizard, face, ai = ai_face(tmp_path, window)
    host = setup_admin.WindowHost(face, Path("/"), why=prompts.ADMIN_WHY_SECURITY)
    tried: list[list[str]] = []
    real = face.admin

    def admin(argv: Any, **kwargs: Any) -> Any:
        tried.append(list(argv))
        return real(argv, **kwargs)

    monkeypatch.setattr(face, "admin", admin)
    launchctl = "/bin/launchctl"
    plist = "/Library/LaunchDaemons/com.local-assistant.bridge.plist"

    def body() -> int:
        with host.stops_held():
            assert host.run([SUDO, "-n", launchctl, "bootout", "system/com.local-assistant.bridge"])[0] == 0
            window.incoming.append({"t": "closed"})  # closed while setup waited for her to go
            try:
                assert host.run([SUDO, "-n", "-u", "_estia", "-H", "--", "/usr/bin/true"], input="1234\n")[0] == 1
            finally:
                assert host.run([SUDO, "-n", launchctl, "bootstrap", "system", plist])[0] == 1
        return 0

    assert setup_ai.run(ai, body) == 1
    assert [argv[3] for argv in tried if argv[2] == launchctl] == ["bootout", "bootstrap"]
    assert len(window.sent("admin")) == 1  # nothing ran as root after the close
    line = ai.channel.lines[-1]
    assert line == flat(prompts.SETUP_AI_CLOSED_HELD.format(start=f"{ownuser.ESTIA} start"))
    assert "/usr/bin/sudo /usr/local/estia/bin/estia start" in line


def test_the_window_face_prints_nothing_in_an_ai_run(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    window = Scripted()
    wizard, face, ai = ai_face(tmp_path, window)
    marked = "zq-made-up-value"  # a marker, never a real value
    with setup_ai.guarded(ai):
        wizard.say(f"✓ Signal: {marked} is registered")
        wizard.command(f"  $ /bin/echo {marked}")
        face.checked(setup_plan.question("number"), Checked(marked, "", f"Her number: {marked}"))
        face.stopped(f"✗ Ollama: {marked}")
    out, err = capfd.readouterr()
    assert (out, err) == ("", "")
    assert marked not in "\n".join(ai.channel.lines)
    assert any(marked in str(m.get("line")) for m in window.sent("progress"))  # the window's log has it
    [stop] = [m for m in window.sent("screen") if m["id"] == "stop"]
    assert str(stop["lead"]).endswith(
        prompts.SETUP_WINDOW_STOP_AGAIN_AI.format(command=setup_window.setup_again_command())
    )


def test_the_channel_names_each_screen_by_its_fixed_title(tmp_path: Path) -> None:
    window = Scripted(answers=["no", "nl", "nl"])
    wizard, face, ai = ai_face(tmp_path, window)
    wizard.name = "Juno"
    assert wizard.answer("calls", name="Juno") == "no"
    assert wizard.answer("language") == "nl"
    wizard.planned.pop("language", None)
    face.show(setup_plan.question("language"), None)
    lines = ai.channel.lines
    assert lines[:2] == ["In the window: Calls with her [calls]", "In the window: Language [language]"]
    assert "Juno" not in "\n".join(lines)


def test_where_macos_asks_is_said_once_a_step_and_a_downloads_dots(tmp_path: Path) -> None:
    from local_assistant import setup_progress

    window = Scripted(admin=[{"code": 0}, {"code": 0}])
    wizard, face, ai = ai_face(tmp_path, window)
    estia = str(ownuser.BIN / "estia")
    wizard.run([SUDO, estia, "restart"], why=prompts.ADMIN_WHY_HER)
    wizard.run([SUDO, estia, "restart"], why=prompts.ADMIN_WHY_HER)
    watch = setup_progress.Watch(face, prompts.SETUP_STEPS["model"])
    watch.start()
    watch.update(3, 5)
    face.progress("  something else ●●○○○")
    assert ai.channel.lines == [
        prompts.SETUP_AI_ADMIN.format(why=prompts.ADMIN_WHY_HER),
        "her model ○○○○○",
        "her model ●●●○○",
    ]


@pytest.mark.parametrize("name", sorted(n for n in prompts.setup_window.__all__ if n.startswith("ADMIN_WHY")))
def test_the_channel_names_every_steps_own_admin_line(tmp_path: Path, name: str) -> None:
    """Every ADMIN_WHY line is setup's own fixed words (her own Ollama's too, since the join with own-ollama): the
    channel names the step's own line, never the general one in its place."""
    why = getattr(prompts, name)
    wizard, _face, ai = ai_face(tmp_path, Scripted(admin=[{"code": 0}]))
    wizard.run([SUDO, str(ownuser.BIN / "estia"), "restart"], why=why)
    assert ai.channel.lines == [prompts.SETUP_AI_ADMIN.format(why=why)]


def test_her_own_ollama_installs_through_macos_box_alone_in_an_ai_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The join of own-ollama and ai-run (architect: Ollama's admin path): every admin command of her own Ollama's
    first install (her account by the checkout's Python, root's copy, its hash on stdin, her service file through tee)
    is a form the window runs, as root by macOS's own box: none refused as a form (A14), none sudo run by setup."""
    from tests.test_own_ollama import HER_STEPS, her_install, her_mac

    window = Scripted(admin=[{"code": 0}] * len(HER_STEPS))
    wizard, _face, ai = ai_face(tmp_path, window)
    tools = her_mac(tmp_path / "mac", monkeypatch)
    tools.changer = wizard.window_change(prompts.ADMIN_WHY_OLLAMA_HERS)  # as setup's Ollama step sets it
    played = tools.change

    def change(argv: Sequence[str], *, input: bytes | None = None) -> int:
        if argv[0] == SUDO:  # the window runs it first; the pretend Mac then plays it as root
            assert tools.changer is not None and tools.changer(argv, input) == 0
        return played(argv, input=input)

    monkeypatch.setattr(tools, "change", change)
    home = tmp_path / "home"
    home.mkdir()
    step, _said = her_install(tools, home)
    with setup_ai.guarded(ai):
        assert step.run(32_768) == "/usr/local/estia/ollama/current/ollama"
    assert ai.denied == [] and ai.ending == ""
    sent = [m for m in window.messages if m["t"] == "admin"]
    assert [m["argv"] for m in sent] == [list(argv[1:]) for argv in tools.sudo()]  # each as root, sudo's word gone
    assert len(sent) == len(HER_STEPS)
    pin = f"{step.release.sha256}  /usr/local/estia/ollama/.download.tgz\n"
    assert [m["input"] for m in sent if m["argv"][0] == "/usr/bin/shasum"] == [pin]  # type: ignore[index]
    assert ai.channel.lines == [prompts.SETUP_AI_ADMIN.format(why=prompts.ADMIN_WHY_OLLAMA_HERS)]


# ------------------------------------------------------------------ deny by default


@pytest.mark.parametrize(
    ("call", "what"),
    [
        (lambda: input("x"), prompts.SETUP_AI_DENIED_LINE),
        (lambda: setup.read_hidden("x"), prompts.SETUP_AI_DENIED_HIDDEN),
        (lambda: setup.read_hidden_lines("x", 4, str.isalnum), prompts.SETUP_AI_DENIED_HIDDEN),  # type: ignore[attr-defined]
        (lambda: hostsec.read_hidden_lines("x", 4, str.isalnum), prompts.SETUP_AI_DENIED_HIDDEN),
        (lambda: ownuser.read_hidden_lines("x", 4, str.isalnum), prompts.SETUP_AI_DENIED_HIDDEN),  # type: ignore[attr-defined]
        (lambda: hostsec.ask_tty("x"), prompts.SETUP_AI_DENIED_LINE),
        (lambda: Wizard(Path("/c"), dry_run=False).hidden("x"), prompts.SETUP_AI_DENIED_HIDDEN),
        (lambda: Wizard(Path("/c"), dry_run=False).hidden_lines("x", 4, str.isalnum), prompts.SETUP_AI_DENIED_HIDDEN),
    ],
    ids=["input", "read_hidden", "setup.read_hidden_lines", "hostsec.read_hidden_lines", "ownuser", "ask_tty",
         "Wizard.hidden", "Wizard.hidden_lines"],
)  # fmt: skip
def test_every_terminal_reader_stops_an_ai_run(call: Callable[[], object], what: str) -> None:
    ai = setup_ai.Run(setup_ai.Channel())
    with setup_ai.guarded(ai), pytest.raises(SystemExit) as stopped:
        call()
    assert stopped.value.code == prompts.SETUP_AI_DENIED.format(what=what) and ai.denied == [what]
    assert ai.ending == flat(prompts.SETUP_AI_DENIED.format(what=what))
    assert input is not None and setup.read_hidden.__name__ == "read_hidden"  # each put back


def test_the_typed_ahead_peeks_are_no_ops(monkeypatch: pytest.MonkeyPatch) -> None:
    from local_assistant import setup_signal

    opened: list[object] = []
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: noted(opened, args, -1))
    ai = setup_ai.Run(setup_ai.Channel())
    with setup_ai.guarded(ai):
        assert setup_signal.typed_stop() is False
        Wizard(Path("/c"), dry_run=False).flush()
        assert Wizard(Path("/c"), dry_run=False).word_stopped() is False
    assert opened == [] and ai.denied == []


def test_setup_never_starts_sudo_itself_in_an_ai_run(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []

    def run(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        started.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    ai = setup_ai.Run(setup_ai.Channel())
    with setup_ai.guarded(ai):
        with pytest.raises(SystemExit):
            hostsec.Host(Path("/")).run([SUDO, "-n", "/bin/launchctl", "bootout", "system/x"])
        with pytest.raises(SystemExit):
            Wizard(Path("/c"), dry_run=False).run([SUDO, "/usr/local/estia/bin/estia", "restart"])
        hostsec.Host(Path("/")).run([SUDO, "-n", "-k", "-l"])  # your own rules, read-only: yours
    assert started == [[SUDO, "-n", "-k", "-l"]]
    assert ai.denied == [prompts.SETUP_AI_DENIED_SUDO] * 2
    assert ai.ending == flat(prompts.SETUP_AI_DENIED.format(what=prompts.SETUP_AI_DENIED_SUDO))
    assert hostsec.SUDO_GUARD is None  # put back


def test_a_steps_own_change_never_falls_back_to_sudo_in_an_ai_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama's changer (Wizard.window_change): a window run that gives nothing back is never sudo run by setup."""
    wizard, face, ai = ai_face(tmp_path, Scripted())
    monkeypatch.setattr(face, "run", lambda *args, **kwargs: None)
    change = wizard.window_change(prompts.ADMIN_WHY_OLLAMA)
    assert change is not None
    with setup_ai.guarded(ai), pytest.raises(SystemExit):
        change([SUDO, "/bin/launchctl", "bootstrap", "system", "/x.plist"], None)
    assert ai.denied == [prompts.SETUP_AI_DENIED_SUDO]


def test_the_windows_log_never_names_a_terminal_behind_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = a_window(tmp_path, monkeypatch)
    steps(monkeypatch)
    assert an_ai_run()[0] == 0
    log = [str(m["line"]) for m in received(folder) if m["t"] == "progress"]
    assert prompts.SETUP_WINDOW_OPEN not in log and not any("in Terminal." in line for line in log)


def test_a_question_before_the_window_opens_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    ai = setup_ai.Run(setup_ai.Channel())
    wizard = Wizard(Path("/c"), dry_run=False, face=setup_ai.StopFace(ai))
    wizard.ai = ai
    with pytest.raises(SystemExit):
        wizard.answer("language")
    assert ai.denied == [prompts.SETUP_AI_DENIED_QUESTION]


# ------------------------------------------------------------------ nothing typed reaches the AI or setup's output

TYPED = frozenset({"text", "number", "fields", "hidden", "code", "pin", "key", "folder"})


def marker(q: Question) -> object:
    """A unique marker for each typed field of this question."""
    if q.kind == "fields":
        return {field.id: f"zqm{q.id}{field.id}" for field in q.fields}
    return f"zqm{q.id}"


def picked(q: Question) -> object:
    """What the window gives for this question: the marker where a person types, else a pick."""
    if q.kind in TYPED:
        return marker(q)
    if q.kind == "yes_no":
        return True
    if q.kind == "switches":
        return []
    return q.options[0].id if q.options else "x"


def markers_of(value: object) -> list[str]:
    return list(value.values()) if isinstance(value, dict) else [str(value)]


def taken_as_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every question's check takes what was typed and shows it back (a "Kept: …"-like line holding it), never a
    "Right?": each screen once, every value through every line that could repeat it."""
    for qid, q in list(setup_plan._BY_ID.items()):

        def check(raw: object, known: object, mac: object, qid: str = qid) -> Checked:
            return Checked(raw if not isinstance(raw, dict) else dict(raw), "", f"{qid}: {raw}")  # type: ignore[arg-type]

        monkeypatch.setitem(setup_plan._BY_ID, qid, dataclasses.replace(q, check=check, confirm=""))


def test_no_typed_answer_reaches_the_ai_or_setups_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Architect, 29 Oct: driven by the question list. A marker typed into every typed field of every question (her
    name, her number, the mail IDs, the projects folder, Spotify's Client ID, About you, the home area, the captcha
    link, the codes, the PINs, the Recovery key, the Account ID, her username, the web keys, TP-Link's, the calendar's
    address), each shown back by its check: none reaches stdout, stderr or the AI's channel; the window's log alone
    has the lines that show them."""
    taken_as_typed(monkeypatch)
    asked = [q for q in setup_plan.QUESTIONS if q.kind != "wait"]
    folder = a_window(tmp_path, monkeypatch, *[{"answer": picked(q)} for q in asked], {"skip": True})
    monkeypatch.setattr(setup_window, "choose_face", refuse)
    typed_values = [value for q in asked if q.kind in TYPED for value in markers_of(marker(q))] + ["zqmword", BOT]
    assert len(typed_values) >= 25

    def body() -> int:
        wizard = ai.wizard
        assert wizard is not None
        face = setup_window.face(__import__("argparse").Namespace(), wizard.mac, wizard)
        assert face is not None
        wizard.face = face
        for q in asked:
            wizard.planned.pop(q.id, None)
            wizard.answer(q.id, name="Juno", number=BOT, found="Ermou", word="estia-4821", question="x")
        word = setup_plan.question("word").at(wizard.known, wizard.mac, {"word": "zqmword", "number": BOT})
        face.show(word, None)  # the one-time word, large on its own screen
        assert face.wait(word, lambda: True, 1.0)
        return 0

    ai = setup_ai.Run(setup_ai.Channel())
    wizard = Wizard(tmp_path / "c.toml", dry_run=False, answers=setup_answers.Answers.load(
        setup_answers.default_path(), say=print), face=setup_ai.StopFace(ai))  # fmt: skip
    wizard.ai, ai.wizard = ai, wizard
    assert setup_ai.run(ai, body) == 0, ai.channel.lines
    out, err = capfd.readouterr()
    lines = "\n".join(ai.channel.lines)
    leaked = [value for value in typed_values if value in out or value in err or value in lines]
    assert leaked == [] and ai.denied == []
    assert sum(line.startswith("In the window: ") for line in ai.channel.lines) == len(asked) + 1
    shown = "\n".join(str(m["shown"]) for m in received(folder) if m["t"] == "checked")
    assert "zqmnumber" in shown and "zqmabout_youwork" in shown  # shown back where the person reads them


def test_a_rerun_through_the_kept_screen_keeps_every_answer_from_the_ai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    taken_as_typed(monkeypatch)
    answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    kept = [q for q in setup_plan.QUESTIONS if q.keep == "saved" and not q.is_secret and q.kind in TYPED]
    for q in kept:
        value = marker(q)
        answers.set(q.id, value if not isinstance(value, dict) else dict(value))  # type: ignore[arg-type]
    folder = a_window(tmp_path, monkeypatch)
    steps(monkeypatch, plan=Wizard.plan, ollama=stop_with(SystemExit(prompts.SETUP_ADMIN_UNKNOWN)))
    monkeypatch.setattr(setup_window, "choose_face", refuse)
    code, lines = an_ai_run()
    assert code == 1, lines
    out, err = capfd.readouterr()
    values = [value for q in kept for value in markers_of(marker(q))]
    assert [value for value in values if value in out + err + "\n".join(lines)] == []
    shown = [m for m in received(folder) if m["t"] == "screen" and m["id"] == "kept"]
    assert shown
    options = shown[0]["options"]
    assert isinstance(options, list) and any("zqm" in str(o["line"]) for o in options)


# ------------------------------------------------------------------ detached: the AI's command ending never ends it

HARNESS = """
import sys, time
from pathlib import Path
import pytest  # setup_ai keeps a test's roots only where pytest is loaded

from local_assistant import setup, setup_answers, setup_window
from local_assistant.setup import Wizard

folder, program = Path(sys.argv[1]), Path(sys.argv[2])
built = setup_window.Built(program.parents[2], program, folder / "page", "a" * 64)
setup_window.build = lambda repo, *, dry_run, say: built
setup_window.at_the_desk = lambda: True
setup.has_terminal = lambda: False
setup.DEFAULT_CONFIG_PATH = folder / "config.toml"
setup_answers.default_path = lambda: folder / "answers" / "answers.toml"
setup.memory_gb = lambda: 32.0
for name in ("plan", "model", "hand_over", "security", "calls_step", "extras_setup", "personality", "restart_her"):
    setattr(Wizard, name, lambda self: None)
Wizard.ollama = lambda self: time.sleep(3)  # the AI's command is killed meanwhile
Wizard.signal_account = lambda self: "+306900000000"
Wizard.owner = lambda self, number: "11111111-2222-4333-8444-555555555555"
Wizard.config = lambda self, number, aci: None
Wizard.voice_end = lambda self, calls=False: None
Wizard.always_on = lambda self: True
Wizard.check = lambda self: 0
Wizard.listed_models = lambda self: []
Wizard.closing = lambda self: self.face.done(["Done."])
sys.exit(setup.main([]))
"""


def test_killing_the_ais_command_and_its_group_never_ends_setup(tmp_path: Path) -> None:
    """Architect, 29 Oct: Claude Code's command tool stops a command after 2 minutes, by SIGTERM to its whole tree
    then SIGKILL (probed on a Mac). Setup is detached (fork, setsid, fork): the relay and its group killed, setup goes
    on in its window, which gets its last screen, and the log its end."""
    folder = tmp_path / "run"
    folder.mkdir()
    program = tmp_path / "Estia Setup.app/Contents/MacOS/estia-setup"
    program.parent.mkdir(parents=True)
    run = f"exec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_WINDOW))}"
    program.write_text(f'#!/bin/sh\nFAKE_WINDOW_DIR={shlex.quote(str(folder))} {run} "$@"\n')
    program.chmod(0o755)
    harness = tmp_path / "harness.py"
    harness.write_text(HARNESS)
    relay = subprocess.Popen(
        [sys.executable, str(harness), str(folder), str(program)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, start_new_session=True,
    )  # fmt: skip
    assert relay.stdout is not None
    ready, _, _ = select.select([relay.stdout], [], [], 20)
    assert ready, "no first line"
    assert relay.stdout.readline().decode().strip() == flat(prompts.SETUP_AI_STARTED)
    os.killpg(relay.pid, signal.SIGKILL)
    relay.wait(timeout=10)
    log = folder / "answers" / setup_ai.LOG
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (log.exists() and prompts.SETUP_AI_DONE in log.read_text()):
        time.sleep(0.2)
    assert log.exists() and log.read_text().splitlines()[-1] == prompts.SETUP_AI_DONE
    lines = [json.loads(line) for line in (folder / "received.jsonl").read_text().splitlines()]
    assert {"t": "done", "lines": ["Done."]} in lines


@pytest.fixture(autouse=True)
def no_guard_left() -> Iterator[None]:
    yield
    assert hostsec.SUDO_GUARD is None

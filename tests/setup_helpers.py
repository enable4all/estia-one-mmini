"""What the setup tests share (not collected: no test_ prefix): the fake signal-cli, the made-up account, the
fake terminal and the fixtures each setup test file uses (pytestmark: no Ollama of the Mac's, a system root of its
own, a terminal). Registered as a plugin in tests/conftest.py, so its fixtures resolve by name."""

import json
import os
import plistlib
import select
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from local_assistant import doctor
from local_assistant import setup as wizard_module
from local_assistant.setup import Wizard

EXAMPLE = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text(encoding="utf-8")
BOT = "+306900000000"
ACI = "11111111-2222-4333-8444-555555555555"


def envelope(source: str, name: str, text: str | None, *, uuid: str = ACI) -> str:
    data = {"timestamp": 1, "message": text} if text is not None else None
    env: dict[str, object] = {"sourceNumber": source, "sourceUuid": uuid, "sourceName": name, "timestamp": 1}
    if data is not None:
        env["dataMessage"] = data
    return json.dumps({"envelope": env, "account": BOT})


def refuse(*args: object, **kwargs: object) -> object:
    raise AssertionError("a dry run must not ask or run anything")


def noted[T, R](into: list[T], item: T, result: R) -> R:
    """`item` added to `into`, then `result`: a fake's record and answer in one lambda (strict types refuse
    `into.append(item) or result`, as append gives None)."""
    into.append(item)
    return result


@pytest.fixture
def no_real_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never the Mac's own Ollama: no answer unless a test gives one."""

    def get_json(self: doctor.Probe, url: str) -> object:
        raise OSError("no Ollama in tests")

    def post_json(self: doctor.Probe, url: str, payload: object) -> object:
        raise OSError("no Ollama in tests")

    monkeypatch.setattr(doctor.Probe, "get_json", get_json)
    monkeypatch.setattr(doctor.Probe, "post_json", post_json)


FAKE = Path(__file__).parent / "fakes" / "fake_signal_cli.py"


def fake_signal_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[str, Path]:
    """The fake signal-cli as a program of its own: a real process, so its arguments are what ps would show any user
    of the Mac. `env` sets its FAKE_SIGNAL_* answers (the captcha, the SMS code, the PIN)."""
    folder = tmp_path / "signal"
    folder.mkdir()
    monkeypatch.setenv("FAKE_SIGNAL_DIR", str(folder))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    program = tmp_path / "signal-cli"
    program.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE))} "$@"\n')
    program.chmod(0o755)
    return str(program), folder


def launches(folder: Path) -> list[list[str]]:
    """Every start's arguments."""
    path = folder / "launches.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def rpc_requests(folder: Path) -> list[dict[str, object]]:
    path = folder / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def no_system_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Setup reads /Library/LaunchDaemons and /usr/local/estia under ESTIA_ROOT: a fresh temporary one per test."""
    root = tmp_path / "system-root"
    monkeypatch.setenv("ESTIA_ROOT", str(root))
    return root


def recorder(ran: list[list[str]]) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = [str(arg) for arg in args[-1]] if isinstance(args[-1], list) else []
        ran.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def her_own_user(root: Path, user: str = "_estia") -> Path:
    """An own-user install under ESTIA_ROOT: bin/estia and the bridge's service file, which says who she runs as."""
    estia = root / "usr/local/estia/bin/estia"
    estia.parent.mkdir(parents=True)
    estia.write_text("#!/bin/sh\n")
    plist = root / "Library/LaunchDaemons/com.local-assistant.bridge.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({"Label": "com.local-assistant.bridge", "UserName": user}))
    return estia


def rerun(monkeypatch: pytest.MonkeyPatch, config: Path, typed: str = "") -> None:
    """setup.main again in her own user, every step but the end stubbed; `typed` answers her style question."""
    from local_assistant import setup

    monkeypatch.setattr("builtins.input", lambda prompt="": typed)
    for name in ("signal_number", "owner", "config", "her_name"):
        monkeypatch.setattr(Wizard, name, refuse)
    for name in ("model", "security", "extras"):
        monkeypatch.setattr(Wizard, name, lambda self: None)
    monkeypatch.setattr(Wizard, "run", recorder([]))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert setup.main(["--config", str(config)]) == 0


@pytest.fixture
def a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The person's own Terminal: setup's full run needs one (a test of its absence says so)."""
    monkeypatch.setattr(wizard_module, "has_terminal", lambda: True)


class Pty:
    """A terminal pair: the program reads `path` (the terminal's end); the test types into `master`."""

    def __init__(self) -> None:
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        self.seen = b""

    def wait_for(self, text: bytes, seconds: float = 10, occurrence: int = 1) -> None:
        """Until the program has written `text` (its `occurrence`-th time: a question asked again)."""
        deadline = time.monotonic() + seconds
        while self.seen.count(text) < occurrence:
            assert time.monotonic() < deadline, self.seen[-200:]
            ready, _, _ = select.select([self.master], [], [], 0.2)
            if ready:
                self.seen += os.read(self.master, 4096)

    def type(self, text: str) -> None:
        data = text.encode()
        while data:
            data = data[os.write(self.master, data) :]

    def close(self) -> None:
        os.close(self.master)
        os.close(self.slave)


def signal_plan(prompt: str = "") -> str | None:
    """The plan's Signal answers, so a test's own answers go to the questions it is about: yes to Signal on your
    phone (Enter is a no from a stdin that is no terminal), Enter at the route (the spare number), her number (BOT),
    yes when it is shown back, Enter at the Registration Lock. None for any other question."""
    from local_assistant import prompts

    right = ": right? ["
    if prompt.startswith(prompts.SETUP_SIGNAL_APP_ASK) or right in prompt:
        return "y"
    if prompt.startswith((prompts.SETUP_ACCOUNT_ASK, prompts.SETUP_LOCK_ASK)):
        return ""
    if prompt.startswith("Her number"):
        return BOT
    return None


def enter_but_signal(prompt: str = "") -> str:
    """Enter at every question but the plan's Signal ones (`signal_plan`)."""
    answer = signal_plan(prompt)
    return "" if answer is None else answer


def typed_once(monkeypatch: pytest.MonkeyPatch, answer: str | None) -> list[str]:
    """`answer` at every question (None: the end of input, Ctrl-D); the prompts are recorded."""
    asked: list[str] = []

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        if answer is None:
            raise EOFError
        return answer

    monkeypatch.setattr("builtins.input", type_answer)
    return asked


# every line a person reads fits a terminal of this many columns, counted with the name Estia and the number
# +306900000000 (FILLED)
TERMINAL = 135
FILLED = {
    "tag": "gemma4:26b-mlx",
    "stop": "Stop her first: /usr/bin/sudo /usr/local/estia/bin/estia stop (start puts her back).",
    "reason": "an example reason from Signal",
    "name": "Estia",
    "default": "Estia",
    "number": BOT,
    "word": "estia-4821",
    "seconds": 20,
    "files": "zz-alex-setup",
    "folder": "~/dev",
    "names": "Estia · Maya · Melina · Socrates",
}


def filled(text: str) -> str:
    return text.format(**FILLED)

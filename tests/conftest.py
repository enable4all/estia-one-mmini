import http.server
import os
import socketserver
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from local_assistant import config
from local_assistant.store import Store

# what the setup tests share, fixtures among it (each setup test file names the ones it uses: pytestmark)
pytest_plugins = ["tests.setup_helpers"]


def _bind_without_dns(self: http.server.HTTPServer) -> None:
    """HTTPServer.server_bind looks up the host's full name (socket.getfqdn): on a network with slow
    reverse DNS that costs ~35 s per test server. Local test servers don't need the name."""
    socketserver.TCPServer.server_bind(self)
    host, port = self.server_address[:2]
    self.server_name, self.server_port = str(host), int(port)


setattr(http.server.HTTPServer, "server_bind", _bind_without_dns)  # noqa: B010 (patching a stdlib method for tests)

ME = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
STRANGER = "11111111-2222-4333-8444-555555555555"
TZ = ZoneInfo("Europe/Amsterdam")
# 2026-10-08 09:32:23 Europe/Amsterdam
T0 = 1791444743502


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "runs_commands: the test may run brew, gcloud or the repo's scripts through setup (its own stand-ins)",
    )


@pytest.fixture(autouse=True)
def no_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test ever starts the Mac's sudo (hard rule 12: admin rights come only from the person, through macOS's own
    box in the setup window or sudo in their own Terminal window): a process whose program is /usr/bin/sudo is refused
    before it starts, wherever the code under test would run one, and so is a shell (sh, bash, zsh, or shell=True)
    whose command line calls it anywhere (a test's own stand-in named sudo, elsewhere, still runs)."""
    import os
    import re
    import shutil
    import subprocess
    from typing import Any

    real = subprocess.Popen.__init__
    # sudo as a command (a line's start, or after ; & | ( ` or $( ), by name or path: never a word an echo prints
    called = re.compile(r"(?:^|[;&|(`])\s*((?:\S*/)?sudo)(?=[\s;&|)`]|$)", re.MULTILINE)

    def mac_sudo(word: str, env: Any) -> bool:
        path = (env or os.environ).get("PATH")
        program = shutil.which(word, path=path) if word and os.sep not in word else word
        return program is not None and bool(program) and os.path.realpath(program) == "/usr/bin/sudo"

    def guarded(self: Any, args: Any, *rest: Any, **kwargs: Any) -> None:
        argv = [args] if isinstance(args, str | bytes | os.PathLike) else list(args)
        words = [os.fsdecode(arg) for arg in argv]
        first = words[0] if words else ""
        lines = words[:1] if kwargs.get("shell") else []
        if os.path.basename(first) in ("sh", "bash", "zsh"):
            lines = words[1:]
        if kwargs.get("shell") and first:
            first = first.split()[0]
        if first and mac_sudo(first, kwargs.get("env")):
            raise AssertionError(f"a test started the Mac's sudo: {first}")
        if first and os.path.basename(first) == "osascript":  # admin rights come from the window's app, never setup
            raise AssertionError(f"a test started osascript: {first}")
        for line in lines:
            if any(mac_sudo(found.group(1), kwargs.get("env")) for found in called.finditer(line)):
                raise AssertionError(f"a test started the Mac's sudo in a shell: {line[:200]}")
        real(self, args, *rest, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded)


@pytest.fixture(autouse=True)
def no_real_commands(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test runs Homebrew, gcloud or the repo's own scripts through setup's run (an Enter at an offer, review
    CODE-12): refused before they start, unless the test is marked runs_commands (its own stand-ins). A dry run runs
    nothing, and a test's own stand-in for Wizard.run replaces this one."""
    if request.node.get_closest_marker("runs_commands"):
        return
    from local_assistant import setup

    real = setup.Wizard.run
    scripts = (setup.REPO / "scripts").resolve()

    def guarded(wizard: setup.Wizard, command: list[str], **kwargs: Any) -> object:
        first = str(command[0]) if command else ""
        named = Path(first).name in ("brew", "gcloud") or Path(first).resolve().is_relative_to(scripts)
        if first and not wizard.dry_run and named:
            raise AssertionError(f"a test ran {first} through setup: give it a stand-in (or runs_commands)")
        return real(wizard, command, **kwargs)

    monkeypatch.setattr(setup.Wizard, "run", guarded)


@pytest.fixture(autouse=True)
def amsterdam_time(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests' fixed times are Amsterdam times: pin the "Mac's own time zone" (config's default) to it, so the
    suite passes on a Mac set to any zone."""
    link = tmp_path_factory.getbasetemp() / "localtime"
    if not link.is_symlink():
        link.symlink_to("/usr/share/zoneinfo/Europe/Amsterdam")
    monkeypatch.setattr(config, "LOCALTIME", link)


@pytest.fixture(autouse=True)
def stable_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child helper sees a UTF-8 LANG, so its Python never coerces the locale into its own environment (PEP 538
    adds LC_CTYPE when LANG is unset or C): a helper's environment is the same from any shell. C.UTF-8: there on
    macOS and on every glibc Linux, where en_US.UTF-8 may not be installed."""
    monkeypatch.setenv("LANG", "C.UTF-8")
    for key in ("LC_ALL", "LC_CTYPE"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def no_live_system(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read the owner's Mac: system paths (/Library/LaunchDaemons, /usr/local/estia, /var/estia) resolve
    under an empty root of their own (ESTIA_ROOT), and so do the fixed tool paths (ESTIA_TEST_ROOT: Homebrew's uv,
    hostsec.tool_root), the live voice service and the Mac's Ollama are never asked, the Mac's memory reads 32 GB (the Pocket TTS model
    follows it: `voice.pocket_standard`), and the evals' EVAL_ settings of the shell are dropped (EVAL_SOUL may name
    the live soul.md). A test that needs "/" says so (monkeypatch.delenv("ESTIA_ROOT")) and reads nothing there."""
    from local_assistant import ownuser, voice

    monkeypatch.setattr(voice, "memory_gb", lambda: 32.0)  # a 16 GB Mac runs the suite the same

    root = str(tmp_path_factory.mktemp("estia-root"))
    monkeypatch.setenv("ESTIA_ROOT", root)
    monkeypatch.setenv("ESTIA_TEST_ROOT", root)  # the fixed tool paths too (Homebrew's uv): never the Mac's own
    # every EVAL_ setting, a new one too: an exported EVAL_SOUL may name the live soul.md, and the 12B's command leaves
    # EVAL_TIER, EVAL_CALL_TOKENS, EVAL_CHORDS or EVAL_MAPS in a shell
    for key in [key for key in os.environ if key.startswith("EVAL_")]:
        monkeypatch.delenv(key, raising=False)
    real = ownuser.voice_health

    def voice_health(live: bool, *, as_her: bool = False) -> object:
        if live:
            raise AssertionError("a test asked the live voice service (127.0.0.1:8150)")
        return real(live, as_her=as_her)

    monkeypatch.setattr(ownuser, "voice_health", voice_health)

    from local_assistant import setup_progress

    def no_pull(url: str, payload: object) -> object:  # setup's model download: never the Mac's own Ollama
        raise AssertionError(f"a test asked the Mac's Ollama to pull ({url})")

    monkeypatch.setattr(setup_progress, "_post", no_pull)


@pytest.fixture(autouse=True)
def setup_answers_of_its_own(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup's answers file in a folder of the test's own, never ~/.config/estia-setup."""
    from local_assistant import setup_answers

    path = tmp_path_factory.mktemp("estia-setup") / "answers.toml"
    monkeypatch.setattr(setup_answers, "default_path", lambda: path)


@pytest.fixture(autouse=True)
def a_mac_like_any_other(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """What setup reads of the Mac itself is the same on every Mac the suite runs on: its region en_US, 32 GB of
    memory (a test sets its own), and at its own keyboard: no remote login (SSH_*), no screen, tmux or herdr, whatever the
    developer's shell is in. gum, setup's menus, lives in a folder of the test's own, and so does the checkout's vendor
    folder, where the calls step looks for the call builds (empty: nothing built yet)."""
    from local_assistant import setup, setup_extras, setup_gum, setup_plan

    gum = tmp_path_factory.mktemp("estia-setup-gum") / f"gum-{setup_gum.VERSION}"
    monkeypatch.setattr(setup_gum, "folder", lambda: gum)  # never ~/Library/Caches/estia-setup
    vendor = tmp_path_factory.mktemp("checkout") / "vendor"
    monkeypatch.setattr(setup_extras, "VENDOR", vendor)  # never the checkout's own builds
    monkeypatch.setattr(setup_extras, "TUNNEL", vendor / setup_extras._TUNNEL)
    monkeypatch.setattr(setup_plan, "region", lambda: "en_US")
    monkeypatch.setattr(setup, "memory_gb", lambda: 32.0)
    for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "STY", "TMUX", "HERDR_ENV"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    db = Store.open(tmp_path / "data" / "assistant.db")
    yield db
    db.close()


@pytest.fixture
def clean_checkout(monkeypatch: pytest.MonkeyPatch) -> str:
    """A real-model run's checkout as a release verdict needs it (release review of 0.9.8, E02): a commit, nothing
    uncommitted and no EVAL_ setting in the environment, whatever the developer's own tree and shell hold. Returns the
    commit."""
    from evals import gemma_suite

    commit = "1b29970f" + "0" * 32
    monkeypatch.setattr(gemma_suite, "git_output", lambda args: commit if args[0] == "rev-parse" else "")
    for key in [key for key in os.environ if key.startswith("EVAL_")]:
        monkeypatch.delenv(key)
    return commit

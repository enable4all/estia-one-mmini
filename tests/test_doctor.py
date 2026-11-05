import calendar
import errno
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Collection, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from local_assistant import babble, doctor, hostsec, ownuser, prompts, voice
from local_assistant import config as config_module
from local_assistant.config import SIGNAL_CLI_SERIES, Config, load_config
from local_assistant.doctor import Finding, Probe, exit_code, render, run
from local_assistant.hostsec import Check
from local_assistant.language import LANGUAGE_KEY
from local_assistant.mail_auth import TokenStore
from local_assistant.ollama_build import Listener
from local_assistant.part_status import PartStatus, Reason
from local_assistant.store import Store

NUMBER = "+306900000000"
ACI = "11111111-2222-4333-8444-555555555555"
MINIMAL = f'bot_number = "{NUMBER}"\nallowed_acis = ["{ACI}"]\n'
NOW = 1_791_452_400.0  # 2026-10-08 09:40 UTC (11:40 Amsterdam)
OLD = NOW - 30 * 86_400  # when the setup files were saved: long before anything was recorded
MAIL = (
    '[mail]\ntenant_id = "11111111-2222-4333-8444-555555555555"\nclient_id = "66666666-7777-4888-9999-000000000000"\n'
    '[[mail.accounts]]\nname = "home"\naddress = "alex@example.com"\n'
    '[[mail.accounts]]\nname = "work"\naddress = "alex@work.example"\n'
)


class FakeProbe(Probe):
    def __init__(self, home: Path) -> None:
        self.home = home
        self.tools = {"signal-cli", "ollama", "uv"}
        self.system = ("darwin", "arm64")
        self.macos: str | None = "27.0.1"  # `sw_vers -productVersion`; None: could not be read
        self.answers: dict[str, Any] = {
            "http://127.0.0.1:11434/api/tags": {"models": [{"name": "local-worker:latest"}]}
        }
        self.asked: list[str] = []
        self.java: Path | None = None
        self.clock = NOW
        self.security: list[Check] = []  # the Mac's checks (hostsec), canned: no system tool runs in tests
        self.install: list[Check] = []  # her own user's checks (ownuser)
        self.homes: list[Path] = []
        self.numbers: list[str] = []
        self.names: list[str] = []
        self.own = False  # she runs as her own user here
        self.her = False  # this doctor runs as her (_estia)
        self.programs: list[Check] = []
        self.launchd: str | None = None  # `launchctl print` of the bridge's service; None: could not be read
        self.hue: dict[str, Any] | None = None  # the device helper's hue-check answer; None: not checked
        self.hue_asked: list[list[str]] = []
        self.pmset: str | None = None  # `pmset -g`; None: could not be read
        self.pmset_custom: str | None = None  # `pmset -g custom` (each power source's settings); None: not read
        self.signal_version: str | None = "signal-cli 0.14.9"  # `signal-cli --version`; None: could not be read
        self.versions_asked: list[str] = []
        self.version_envs: list[dict[str, str]] = []  # the environment each was asked with (the JVM build's Java)
        self.signal_versions: dict[str, str | None] = {}  # per program; else signal_version
        self.home_open: tuple[list[Path], bool] = ([], True)  # what in your home every account may open; all read
        self.homes_opened: list[tuple[Path, set[Path]]] = []
        self.ollama_at = ""  # the program listening on Ollama's port; "": none found
        self.ollama_addresses: tuple[str, ...] = ("127.0.0.1.11434",)  # where it listens (netstat's local address)
        self.ollama_uid: int | None = None  # the account that runs it (ps); None: not read
        self.ollama_others: tuple[tuple[str, int | None], ...] = ()  # another program or account on its port too
        self.ollama_unread = False  # netstat listed no socket: another account's on its port could not be listed
        self.python = ("3.13.16", "OpenSSL 3.5.9 29 Sep 2026")  # the Python doctor runs on, and its OpenSSL
        self.ollama_ports: list[int] = []
        self.ollama_service: str | None = None  # `launchctl print` of setup's Ollama service; None: could not be read
        self.shown: dict[str, Any] = {}  # Ollama's /api/show answer per model name; none: it does not answer
        self.posted: list[tuple[str, dict[str, Any]]] = []

    def ollama_listener(self, port: int) -> Listener:
        self.ollama_ports.append(port)
        if not self.ollama_at:
            return Listener("", others_unread=self.ollama_unread)
        return Listener(self.ollama_at, self.ollama_addresses, self.ollama_uid, self.ollama_others, self.ollama_unread)

    def python_versions(self) -> tuple[str, str]:
        return self.python

    def ollama_launchd(self) -> str | None:
        return self.ollama_service

    def power(self) -> str | None:
        return self.pmset

    def power_custom(self) -> str | None:
        return self.pmset_custom

    def open_home(self, home: Path, shared: Collection[Path]) -> tuple[list[Path], bool]:
        self.homes_opened.append((home, set(shared)))
        return self.home_open

    def signal_cli_version(self, program: str, env: Mapping[str, str] | None = None) -> str | None:
        self.versions_asked.append(program)
        self.version_envs.append(dict(env or {}))
        return self.signal_versions.get(program, self.signal_version)

    def bridge_launchd(self) -> str | None:
        return self.launchd

    def hue_check(self, cfg: Config, lights: list[str]) -> dict[str, Any] | None:
        self.hue_asked.append(lights)
        return self.hue

    def moved(self) -> bool:
        return self.own

    def her_user(self) -> bool:
        return self.her

    def program_checks(self, cfg: Any) -> list[Check]:
        return list(self.programs)

    def security_checks(self, home: Path, bot_number: str, name: str = "Estia") -> list[Check]:
        self.names.append(name)  # hers, for the firewall's nuclear line
        self.homes.append(home)
        self.numbers.append(bot_number)  # used to find her account, never shown
        return list(self.security)

    def install_checks(self) -> list[Check]:
        return list(self.install)

    def now(self) -> float:
        return self.clock

    def java_home(self) -> Path | None:
        return self.java

    def which(self, name: str) -> str | None:
        return f"/opt/homebrew/bin/{name}" if name in self.tools else None

    def platform(self) -> tuple[str, str]:
        return self.system

    def macos_version(self) -> str | None:
        return self.macos

    def get_json(self, url: str) -> Any:
        self.asked.append(url)
        if url not in self.answers:
            raise OSError("connection refused")
        return self.answers[url]

    def post_json(self, url: str, payload: dict[str, Any]) -> Any:
        """Ollama's /api/show (read-only): the answer for the model named in the request, from `shown`."""
        self.posted.append((url, payload))
        answer = self.shown.get(str(payload.get("model"))) if url.endswith("/api/show") else None
        if answer is None:
            raise OSError("connection refused")
        return answer


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))  # the data folder (tokens) follows the home folder
    accounts = tmp_path / ".local/share/signal-cli/data/accounts.json"
    accounts.parent.mkdir(parents=True)
    accounts.write_text(json.dumps({"accounts": [{"number": NUMBER, "path": "123456"}]}))
    (accounts.parent / "123456").write_text(json.dumps({"version": 8, "registered": True}))  # its own account file
    return tmp_path


def write_config(home: Path, text: str, mode: int = 0o600) -> Path:
    path = home / ".config/local-assistant/config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def by_part(findings: list[Finding]) -> dict[str, Finding]:
    return {finding.part: finding for finding in findings}


def test_a_ready_minimal_setup_has_no_failures(home: Path) -> None:
    findings = run(write_config(home, MINIMAL), FakeProbe(home))
    parts = by_part(findings)
    assert [parts[p].status for p in ("Mac", "Tools", "Config", "Model", "Signal number")] == ["ok"] * 5
    assert exit_code(findings) == 0
    assert NUMBER not in render(findings)  # nothing private on the screen


def test_a_missing_config_says_how_to_create_it(home: Path) -> None:
    findings = run(home / ".config/local-assistant/config.toml", FakeProbe(home))
    config = by_part(findings)["Config"]
    assert config.status == "fail" and "config.example.toml" in config.fix
    assert exit_code(findings) == 1


def test_a_config_others_can_read_is_refused(home: Path) -> None:
    config = by_part(run(write_config(home, MINIMAL, 0o644), FakeProbe(home)))["Config"]
    assert config.status == "fail" and "chmod 600" in config.text


def test_a_config_error_is_shown(home: Path) -> None:
    config = by_part(run(write_config(home, 'bot_number = "123"\n'), FakeProbe(home)))["Config"]
    assert config.status == "fail" and "bot_number" in config.text


def test_missing_tools_come_with_the_install_command(home: Path) -> None:
    """Ollama is not one of Homebrew's tools here (owner, 15 Oct: only its official build): its own lines say."""
    probe = FakeProbe(home)
    probe.tools = set()
    tools = by_part(run(write_config(home, MINIMAL), probe))["Tools"]
    assert tools.status == "fail" and "signal-cli" in tools.text and "ollama" not in tools.text
    assert tools.fix == "brew install signal-cli uv"


def test_only_a_mac_with_apple_silicon_will_do(home: Path) -> None:
    probe = FakeProbe(home)
    probe.system = ("darwin", "x86_64")
    assert by_part(run(write_config(home, MINIMAL), probe))["Mac"].status == "fail"


def test_doctor_names_macos_before_27(home: Path) -> None:
    """Review of 0.9.8, M02: setup.sh refuses a macOS before 27, but an install made before that rule (0.9.6 on
    macOS 26.5, updated with estia-deploy) showed ✓ Mac. Doctor warns in setup's own words; 27 or later, or a
    version it cannot read, keeps the ✓ line as it was (no guess). No 26.x line of its own (owner, 29 Oct)."""
    path = write_config(home, MINIMAL)
    probe = FakeProbe(home)
    probe.macos = "26.5"
    mac = by_part(run(path, probe))["Mac"]
    assert mac.status == "warn" and "Estia needs macOS 27 or later" in mac.text and "26.5" in mac.text
    assert mac.fix == "Update macOS (System Settings → General → Software Update)"
    assert "Estia needs macOS 27 or later" in prompts.SETUP_MACOS_OLD and mac.fix in prompts.SETUP_MACOS_OLD
    named = by_part(run(write_config(home, MINIMAL + 'name = "Tiffany"\n'), probe))["Mac"]
    assert named.status == "warn" and "Tiffany needs macOS 27 or later" in named.text
    path = write_config(home, MINIMAL)
    for version in ("27.0.1", "27.1", "27", "28.0", None, "", "macOS"):
        probe.macos = version
        assert by_part(run(path, probe))["Mac"] == Finding("ok", "Mac", "macOS on Apple silicon"), version
    probe.system, probe.macos = ("darwin", "x86_64"), "26.5"
    assert by_part(run(path, probe))["Mac"].status == "fail"  # the chip first, as before


def test_the_macos_version_is_read_with_sw_vers_by_its_full_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read-only, fixed argv, no shell; None when it cannot be read, and never under a test's root."""
    asked: list[list[str]] = []
    answer: dict[str, Any] = {"code": 0, "out": "26.5\n"}

    def sw_vers(argv: list[str], **kwargs: Any) -> Any:
        asked.append(argv)
        if answer["code"] is None:
            raise OSError("no such file")
        return subprocess.CompletedProcess(argv, answer["code"], answer["out"], "")

    monkeypatch.setattr(subprocess, "run", sw_vers)  # doctor's own: one module
    assert Probe(Path("/nowhere")).macos_version() is None and asked == []  # a test's root: never asked
    monkeypatch.delenv("ESTIA_ROOT")
    assert Probe(Path("/nowhere")).macos_version() == "26.5"
    assert asked == [["/usr/bin/sw_vers", "-productVersion"]]
    answer["code"] = 1
    assert Probe(Path("/nowhere")).macos_version() is None
    answer["code"] = None
    assert Probe(Path("/nowhere")).macos_version() is None


def test_ollama_must_run_and_have_the_model(home: Path) -> None:
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL + 'model = "gemma4:26b"\n')
    model = by_part(run(path, probe))["Model"]
    assert model.status == "fail" and "ollama pull gemma4:26b" in model.fix
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "gemma4:26b"}]}
    assert by_part(run(path, probe))["Model"].status == "ok"
    del probe.answers["http://127.0.0.1:11434/api/tags"]
    down = by_part(run(path, probe))["Model"]
    assert down.status == "fail" and "brew" not in down.fix
    assert down.fix == "scripts/setup.sh (it installs Ollama's official build and starts it)"


def test_ollama_down_with_setups_service_in_place_names_its_restart(home: Path) -> None:
    from local_assistant import hostsec, ollama_build

    daemon = hostsec.estia_root() / ollama_build.DAEMON.relative_to("/")
    daemon.parent.mkdir(parents=True, exist_ok=True)
    daemon.write_bytes(b"<plist/>")
    probe = FakeProbe(home)
    del probe.answers["http://127.0.0.1:11434/api/tags"]
    down = by_part(run(write_config(home, MINIMAL), probe))["Model"]
    assert down.fix.startswith("/usr/bin/sudo /bin/launchctl kickstart -k system/com.local-assistant.ollama")
    # booted out (an update by hand, a failed one): kickstart cannot find it, bootstrap loads it again
    probe.ollama_service = 'Could not find service "com.local-assistant.ollama" in domain for system'
    down = by_part(run(write_config(home, MINIMAL), probe))["Model"]
    assert down.fix.startswith(
        "/usr/bin/sudo /bin/launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.ollama.plist"
    )


def test_ollama_down_with_another_service_names_that_service_not_setup(home: Path) -> None:
    """Setup would refuse to install a second service next to it: the fix is starting that one again."""
    import plistlib

    from local_assistant import hostsec

    daemons = hostsec.estia_root() / "Library/LaunchDaemons"
    daemons.mkdir(parents=True, exist_ok=True)
    program = "/Users/example/opt/ollama/current/ollama"
    (daemons / "com.local-llm.ollama.plist").write_bytes(
        plistlib.dumps({"Label": "com.local-llm.ollama", "ProgramArguments": [program, "serve"]})
    )
    probe = FakeProbe(home)
    del probe.answers["http://127.0.0.1:11434/api/tags"]
    down = by_part(run(write_config(home, MINIMAL), probe))["Model"]
    assert down.status == "fail" and "scripts/setup.sh" not in down.fix
    assert down.fix.startswith("/usr/bin/sudo /bin/launchctl kickstart -k system/com.local-llm.ollama")


def official(folder: Path, *, xgrammar: bool = True) -> str:
    from local_assistant.ollama_build import XGRAMMAR

    for name in ("ollama", *(XGRAMMAR if xgrammar else ())):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"\xcf\xfa\xed\xfe")
    return str(folder / "ollama")


def test_the_official_ollama_with_its_library_is_a_tick(home: Path) -> None:
    probe = FakeProbe(home)
    probe.ollama_at = official(home / "opt/ollama/v0.40.4")
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.40.4"}
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    assert line.status == "ok"
    assert " ".join(line.text.split()) == f"0.40.4 answers ({probe.ollama_at}), with its JSON-schema library"
    assert probe.ollama_ports == [11434]


def fits(finding: Finding) -> bool:
    """Every rendered line fits the owner's terminal (hostsec.TERMINAL), but a lone path a test's long temporary
    folder makes longer: a path is never split."""
    lines = render([finding]).splitlines()
    return all(len(shown) <= hostsec.TERMINAL or " " not in shown.strip() for shown in lines)


UNLISTED_ON_PORT = "; other accounts' programs on its port could not be listed without admin rights"


def test_the_ollama_tick_names_what_could_not_be_listed_on_its_port(home: Path) -> None:
    """3 Nov report: netstat listed no sockets (macOS 27.0.1, without admin rights), so another account's program
    beside this Ollama on its port cannot be seen; the ✓ keeps what was checked and names that limit."""
    probe = FakeProbe(home)
    probe.ollama_at = official(home / "opt/ollama/v0.40.4")
    probe.ollama_unread = True
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.40.4"}
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    assert line.status == "ok"
    expected = f"0.40.4 answers ({probe.ollama_at}), with its JSON-schema library{UNLISTED_ON_PORT}"
    assert " ".join(line.text.split()) == expected
    assert prompts.DOCTOR_OLLAMA_OTHERS_UNREAD == UNLISTED_ON_PORT
    assert fits(line)  # fix review 6: render does not wrap, so the long ✓ is split to fit the owner's terminal


UNSEEN = "its program and account could not be read without admin rights"


def test_an_ollama_whose_program_could_not_be_read_is_never_all_clear(home: Path) -> None:
    """Fix review 1 (3 Nov): netstat lists nothing and lsof, as the account doctor runs as, finds no program of its
    own on the port (another account's Ollama there). Never a silent "no line": a ○ says what could not be read, with
    the command that reads it."""
    probe = FakeProbe(home)
    probe.ollama_unread = True
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.40.4"}
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    assert (line.status, line.text) == ("untested", f"0.40.4 answers; {UNSEEN}")
    assert line.fix == "to read them: /usr/bin/sudo /usr/sbin/lsof -nP -iTCP:11434 -sTCP:LISTEN"
    probe.ollama_ports.clear()
    probe.answers["http://127.0.0.1:8151/api/version"] = {"version": "0.40.4"}
    probe.answers["http://127.0.0.1:8151/api/tags"] = probe.answers["http://127.0.0.1:11434/api/tags"]
    hers = by_part(run(write_config(home, MINIMAL + HERS), probe))["Ollama"]
    assert hers.status == "untested" and hers.fix.endswith("-iTCP:8151 -sTCP:LISTEN") and probe.ollama_ports == [8151]


@pytest.mark.parametrize(
    ("version", "words"), [("0.40.5", "not 0.40.4, the version she is tested with"), ("0.30.0", "older than")]
)
def test_an_ollama_warning_names_a_program_that_could_not_be_read(home: Path, version: str, words: str) -> None:
    """The "!" for an untested or old version keeps its fix and says its program and account could not be read."""
    probe = FakeProbe(home)
    probe.ollama_unread = True
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": version}
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    text = " ".join(line.text.split())
    assert line.status == "warn" and words in text and text.endswith(f"; {UNSEEN}") and fits(line)
    probe.ollama_unread = False  # netstat listed sockets: nothing unread to name
    assert not by_part(run(write_config(home, MINIMAL), probe))["Ollama"].text.endswith(UNSEEN)


def test_an_ollama_open_to_other_machines_is_named(home: Path) -> None:
    """Ollama has no login: on every network, any machine there could replace local-worker."""
    probe = FakeProbe(home)
    probe.ollama_at = official(home / "opt/ollama/v0.40.4")
    probe.ollama_addresses = ("*.11434",)
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.40.4"}
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    assert line.status == "warn" and "it listens for other machines too (*.11434)" in line.text
    assert "OLLAMA_HOST=127.0.0.1:11434" in line.text


@pytest.mark.parametrize("ours", [True, False], ids=["setups-service", "another"])
def test_an_ollama_of_another_version_than_the_pin_is_named_untested(home: Path, ours: bool) -> None:
    from local_assistant import hostsec, ollama_build

    if ours:
        daemon = hostsec.estia_root() / ollama_build.DAEMON.relative_to("/")
        daemon.parent.mkdir(parents=True, exist_ok=True)
        daemon.write_bytes(b"<plist/>")
    probe = FakeProbe(home)
    probe.ollama_at = official(home / "opt/ollama/v0.40.0")
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.40.0"}
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    assert line.status == "warn" and line.text == "0.40.0 answers, not 0.40.4, the version she is tested with"
    assert ("updated in place" in line.fix) is ours and "scripts/" in line.fix


@pytest.mark.parametrize("cellar", ["/opt/homebrew/Cellar", "/usr/local/Cellar"])
def test_homebrews_ollama_is_named_with_the_fix(home: Path, cellar: str) -> None:
    probe = FakeProbe(home)
    probe.ollama_at = f"{cellar}/ollama/0.40.4/bin/ollama"
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.40.4"}
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    assert line.status == "warn" and "Homebrew's Ollama answers" in line.text and probe.ollama_at in line.text
    assert line.fix.startswith("brew services stop ollama, then brew uninstall ollama")
    assert exit_code([line]) == 0  # works for chat; the JSON-schema answers fail: a warning, not a stop


def test_an_ollama_without_the_xgrammar_library_is_named(home: Path) -> None:
    probe = FakeProbe(home)
    probe.ollama_at = official(home / "built/here", xgrammar=False)
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    assert line.status == "warn" and "no mlx_metal_v3/libollama_xgrammar.dylib" in line.text
    assert "scripts/setup.sh" in line.fix


def test_an_ollama_older_than_the_json_schema_one_is_named(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.30.2"}
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    assert line.status == "warn" and "0.30.2 answers: older than 0.33.1" in line.text


def test_an_ollama_that_another_account_runs_is_named(home: Path) -> None:
    probe = FakeProbe(home)
    probe.ollama_at = official(home / "opt/ollama/v0.40.4")
    probe.ollama_uid = os.getuid() + 1
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.40.4"}
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    assert line.status == "fail" and "another account on this Mac, not you: that account controls her answers" in (
        line.text
    )
    probe.ollama_uid = os.getuid()
    assert by_part(run(write_config(home, MINIMAL), probe))["Ollama"].status == "ok"


def test_a_second_program_on_ollamas_port_is_a_failure(home: Path) -> None:
    """Your Ollama on 127.0.0.1 and another account's on ::1: the bridge may reach either (re-check, 16 Oct)."""
    probe = FakeProbe(home)
    probe.ollama_at = official(home / "opt/ollama/v0.40.4")
    probe.ollama_uid = os.getuid()
    probe.ollama_others = ((probe.ollama_at, os.getuid() + 1),)
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.40.4"}
    line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    assert line.status == "fail" and "also listens on its port" in line.text


def test_the_ollama_app_chosen_at_setup_is_a_warning_with_its_limits(home: Path) -> None:
    """Owner, 29 Oct: the app the person chose (ollama = "app") is a "!", never a ✗, its limits in one line, whatever
    its version (it updates itself); not chosen, it is named with the fix, as before."""
    from local_assistant import prompts

    probe = FakeProbe(home)
    probe.ollama_at = official(home / "Applications/Ollama.app/Contents/Resources")
    probe.ollama_uid = os.getuid()
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.41.0"}
    line = by_part(run(write_config(home, MINIMAL + 'ollama = "app"\n'), probe))["Ollama"]
    assert line.status == "warn" and not line.fix and "\n" not in line.text
    assert line.text == prompts.DOCTOR_OLLAMA_APP.format(version="0.41.0", program=probe.ollama_at)
    assert prompts.OLLAMA_APP_LIMITS in line.text
    unchosen = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    app = home / "Applications/Ollama.app"
    assert unchosen.status == "warn" and unchosen.text == prompts.OLLAMA_APP.format(state="answers", path=app)


@pytest.mark.parametrize("why", ["another-account", "other-machines", "second-listener"])
def test_the_chosen_ollama_app_that_someone_else_may_control_is_still_named(home: Path, why: str) -> None:
    probe = FakeProbe(home)
    probe.ollama_at = official(home / "Applications/Ollama.app/Contents/Resources")
    probe.ollama_uid = os.getuid() + 1 if why == "another-account" else os.getuid()
    if why == "other-machines":
        probe.ollama_addresses = ("*.11434",)
    if why == "second-listener":
        probe.ollama_others = (("/tmp/elsewhere/serve", os.getuid() + 1),)
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.41.0"}
    line = by_part(run(write_config(home, MINIMAL + 'ollama = "app"\n'), probe))["Ollama"]
    assert line.status == ("warn" if why == "other-machines" else "fail") and line.fix


def test_the_chosen_ollama_app_down_names_opening_it(home: Path) -> None:
    from local_assistant import prompts

    probe = FakeProbe(home)
    del probe.answers["http://127.0.0.1:11434/api/tags"]
    down = by_part(run(write_config(home, MINIMAL + 'ollama = "app"\n'), probe))["Model"]
    assert down.status == "fail" and down.fix == prompts.DOCTOR_OLLAMA_APP_DOWN_FIX


def test_as_her_an_ollama_that_another_account_runs_is_named_too(home: Path, tmp_path: Path) -> None:
    """As her, your folders are closed (no library line, no tick): the account is still read (ps names any user's
    process), and the owner is the one the move's record names."""
    import pwd

    from local_assistant import hostsec, ownuser

    marker = hostsec.estia_root() / ownuser.MARKER.relative_to("/")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"owner": pwd.getpwuid(os.getuid()).pw_name}), encoding="utf-8")
    closed = tmp_path / "closed"
    program = official(closed / "opt/ollama/v0.40.4")
    probe = FakeProbe(home)
    probe.her = True
    probe.ollama_at = program
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.40.4"}
    (closed / "opt/ollama/v0.40.4").chmod(0o000)
    try:
        assert "Ollama" not in by_part(run(write_config(home, MINIMAL), probe))  # yours: nothing to say as her
        probe.ollama_uid = os.getuid() + 1
        line = by_part(run(write_config(home, MINIMAL), probe))["Ollama"]
    finally:
        (closed / "opt/ollama/v0.40.4").chmod(0o755)
    assert line.status == "fail" and "another account on this Mac" in line.text


@pytest.mark.parametrize(("memory", "named"), [(32.0, False), (16.0, True)], ids=["as-setup-writes", "older"])
def test_setups_own_service_file_that_differs_from_what_setup_writes_is_named(
    home: Path, monkeypatch: pytest.MonkeyPatch, memory: float, named: bool
) -> None:
    """A release that changes the service file reaches an install only through setup: doctor shows the old one."""
    import grp
    import pwd

    from local_assistant import hostsec, ollama_build, setup

    me = pwd.getpwuid(os.getuid())
    daemon = hostsec.estia_root() / ollama_build.DAEMON.relative_to("/")
    daemon.parent.mkdir(parents=True, exist_ok=True)
    daemon.write_bytes(
        ollama_build.daemon_plist(
            user=me.pw_name, group=grp.getgrgid(me.pw_gid).gr_name, home=Path(me.pw_dir), num_ctx=32_768
        )
    )
    monkeypatch.setattr(setup, "memory_gb", lambda: memory)  # 16 GB: setup writes a 16384-token context
    parts = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))
    if not named:
        assert "Ollama service" not in parts
        return
    line = parts["Ollama service"]
    assert line.status == "warn" and "is not the one this version of setup writes" in line.text
    assert str(ollama_build.DAEMON) in line.text and line.fix.startswith("scripts/install-ollama.sh")


def test_no_ollama_line_when_its_program_cannot_be_seen(home: Path) -> None:
    """As her, doctor may see neither your program nor its folder: nothing to say, never a guess."""
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/version"] = {"version": "0.40.4"}
    assert "Ollama" not in by_part(run(write_config(home, MINIMAL), probe))


@pytest.mark.parametrize(
    ("config", "entry", "named"),
    [
        (
            "",
            {"name": "local-worker:latest", "details": {"format": "gguf", "parent_model": "gemma4:26b"}},
            "made from gemma4:26b",
        ),
        ('model = "gemma4:12b"\n', {"name": "gemma4:12b"}, "gemma4:12b is not one of the builds"),
    ],
)
def test_a_model_that_is_not_an_mlx_build_of_gemma_4_is_named(
    home: Path, config: str, entry: dict[str, object], named: str
) -> None:
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [entry]}
    line = by_part(run(write_config(home, MINIMAL + config), probe))["Model build"]
    assert line.status == "warn" and named in line.text and "gemma4:26b-mlx" in line.text
    if not config:
        assert line.fix.startswith("ollama rm local-worker, then run scripts/setup.sh")
    else:
        assert line.fix.startswith("ollama pull gemma4:26b-mlx")


def test_the_model_build_line_names_setups_own_tags(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One definition of the tags: a new pin in setup reaches doctor's words with no second copy to change."""
    from local_assistant import setup

    monkeypatch.setattr(setup, "GEMMA_26B", "gemma4:27b-mlx")
    monkeypatch.setattr(setup, "GEMMA_12B", "gemma4:13b-mlx")
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "gemma4:12b"}]}
    line = by_part(run(write_config(home, MINIMAL + 'model = "gemma4:12b"\n'), probe))["Model build"]
    assert "(gemma4:27b-mlx, gemma4:13b-mlx)" in line.text and "26b-mlx" not in line.text + line.fix
    assert line.fix.startswith("ollama pull gemma4:27b-mlx (gemma4:13b-mlx on 16 or 24 GB)")


def test_an_mlx_build_has_no_model_build_line(home: Path) -> None:
    probe = FakeProbe(home)
    entry = {"name": "local-worker:latest", "details": {"format": "safetensors", "family": "gemma4"}}
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [entry]}
    assert "Model build" not in by_part(run(write_config(home, MINIMAL), probe))


def show_answer(renderer: str, size: str = "26.2B") -> dict[str, Any]:
    """/api/show's answer for a model, the parts doctor reads: the Modelfile Ollama writes back, and the size."""
    modelfile = (
        f'# Modelfile generated by "ollama show"\nFROM local-worker:latest\nRENDERER {renderer}\nPARSER gemma4\n'
    )
    return {"modelfile": modelfile, "details": {"format": "safetensors", "family": "gemma4", "parameter_size": size}}


def test_an_alias_with_the_small_models_prompt_is_a_warning_with_the_way_to_make_it_again(home: Path) -> None:
    """Empty answers (17 Oct): local-worker made before 0.9.3 gets Ollama's prompt for Gemma 4's small models. Doctor
    reads its Modelfile (POST /api/show, which only reads) and names the step: setup makes it again and prints the
    new digest, which the pin needs."""
    probe = FakeProbe(home)
    probe.shown["local-worker"] = show_answer("gemma4")
    line = by_part(run(write_config(home, MINIMAL), probe))["Model prompt"]
    assert line.status == "warn"
    assert line.text == prompts.DOCTOR_MODEL_PROMPT.format(model="local-worker")
    assert "small models" in line.text and "empty" in line.text
    assert line.fix == prompts.DOCTOR_MODEL_PROMPT_FIX and "scripts/setup.sh" in line.fix and "digest" in line.fix
    assert probe.posted == [("http://127.0.0.1:11434/api/show", {"model": "local-worker"})]


def test_in_her_own_user_the_prompt_fix_says_how_to_edit_her_config(home: Path) -> None:
    probe = FakeProbe(home)
    probe.own = True
    probe.shown["local-worker"] = show_answer("gemma4")
    line = by_part(run(write_config(home, MINIMAL), probe))["Model prompt"]
    assert line.fix == prompts.DOCTOR_MODEL_PROMPT_FIX_HERS
    assert "estia stop" in line.fix and "estia config" in line.fix and "estia start" in line.fix


def test_local_worker_named_with_its_tag_gets_setups_way_too(home: Path) -> None:
    """Review of 17 Oct (M4): model = "local-worker:latest" is the same alias; it got the other model's text, without
    setup's way that makes it again."""
    probe = FakeProbe(home)
    probe.shown["local-worker:latest"] = show_answer("gemma4")
    line = by_part(run(write_config(home, MINIMAL + 'model = "local-worker:latest"\n'), probe))["Model prompt"]
    assert line.fix == prompts.DOCTOR_MODEL_PROMPT_FIX


def test_a_model_of_her_own_name_gets_the_line_to_put_in_its_modelfile(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "my-gemma:latest"}]}
    probe.shown["my-gemma"] = show_answer("gemma4", "12.4B")
    line = by_part(run(write_config(home, MINIMAL + 'model = "my-gemma"\n'), probe))["Model prompt"]
    assert line.fix == prompts.DOCTOR_MODEL_PROMPT_OTHER_FIX.format(model="my-gemma")
    assert "RENDERER gemma4-large" in line.fix


@pytest.mark.parametrize("renderer", ["gemma4-large", "gemma4-small-but-unknown"])
def test_no_prompt_line_with_googles_large_prompt_or_another_renderer(home: Path, renderer: str) -> None:
    probe = FakeProbe(home)
    probe.shown["local-worker"] = show_answer(renderer)
    assert "Model prompt" not in by_part(run(write_config(home, MINIMAL), probe))


def test_a_model_context_smaller_than_the_configs_is_a_warning_with_the_line_to_put_in(home: Path) -> None:
    """Review I24: local-worker made with 16,384 tokens (the 16 GB tier) and a config without context_tokens (as
    before 0.9.7), whose history's own limits count on 32,768: Ollama cuts a long chat's request without a word. One
    /api/show read serves both lines."""
    probe = FakeProbe(home)
    probe.shown["local-worker"] = show_answer("gemma4-large") | {"parameters": "num_ctx                        16384"}
    line = by_part(run(write_config(home, MINIMAL), probe))["Model context"]
    assert line.status == "warn"
    assert line.text == prompts.DOCTOR_MODEL_CONTEXT.format(model="local-worker", model_tokens=16_384, tokens=32_768)
    assert line.fix == prompts.DOCTOR_MODEL_CONTEXT_FIX.format(tokens=16_384) and "context_tokens = 16384" in line.fix
    assert probe.posted == [("http://127.0.0.1:11434/api/show", {"model": "local-worker"})]
    fitting = write_config(home, MINIMAL + "context_tokens = 16384\n")
    assert "Model context" not in by_part(run(fitting, probe))
    probe.shown["local-worker"] = show_answer("gemma4-large")  # no num_ctx of its own: Ollama's, not read here
    assert "Model context" not in by_part(run(write_config(home, MINIMAL), probe))


def test_no_prompt_line_when_ollama_does_not_show_the_model(home: Path) -> None:
    """Ollama silent on /api/show (or an older one): no claim either way; no request without the model listed."""
    probe = FakeProbe(home)
    assert "Model prompt" not in by_part(run(write_config(home, MINIMAL), probe))
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": []}
    probe.shown["local-worker"] = show_answer("gemma4")
    assert "Model prompt" not in by_part(run(write_config(home, MINIMAL), probe)) and probe.posted == []


def test_a_missing_alias_points_to_setup_which_picks_the_model_for_this_macs_memory(home: Path) -> None:
    """On a 16 or 24 GB Mac setup chose gemma4:12b: the fix never names the 26b, which does not fit (finding 63)."""
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "gemma4:12b"}]}
    model = by_part(run(write_config(home, MINIMAL), probe))["Model"]
    assert model.status == "fail" and "gemma4:26b" not in model.fix
    assert "scripts/setup.sh" in model.fix and "fits this Mac's memory" in model.fix


def test_a_cloud_model_under_the_alias_is_a_fail(home: Path) -> None:
    """Ollama's own entry marks a model that runs on its servers: answers pause, so doctor says ✗ and how to fix it."""
    probe = FakeProbe(home)
    remote = {"remote_model": "gpt-oss:120b", "remote_host": "https://ollama.com:443"}
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "local-worker:latest", **remote}]}
    model = by_part(run(write_config(home, MINIMAL), probe))["Model"]
    assert model.status == "fail" and "cloud model" in model.text and "Ollama's servers" in model.text
    assert "ollama rm local-worker, then run scripts/setup.sh" in model.fix and "gemma4:26b" not in model.fix


def test_a_number_whose_registration_did_not_finish_is_named(home: Path) -> None:
    """signal-cli lists a number as soon as register starts: only its account file says it was verified (20, 53)."""
    (home / ".local/share/signal-cli/data/123456").write_text(json.dumps({"version": 8, "registered": False}))
    signal = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Signal number"]
    assert signal.status == "fail" and "did not finish" in signal.text and "2.3" in signal.fix
    assert NUMBER not in signal.text + signal.fix


@pytest.mark.parametrize(
    ("days", "status"), [(10, "ok"), (92, "ok"), (93, "warn"), (200, "warn")], ids=lambda value: str(value)
)
def test_doctor_names_a_signal_cli_release_over_three_months_old(home: Path, days: int, status: str) -> None:
    """signal-cli's own README: "signal-cli releases older than three months may not work correctly" (Signal's
    official clients expire after three months, then Signal's servers may change). Its release date comes from a
    table in the code: no network at doctor time."""
    probe = FakeProbe(home)
    probe.signal_version = "signal-cli 0.14.9"
    released = calendar.timegm((2026, 10, 7, 11, 15, 44, 0, 0, 0))  # v0.14.9 on GitHub
    probe.clock = released + days * 86400
    found = by_part(run(write_config(home, MINIMAL), probe))["signal-cli"]
    assert found.status == status and "0.14.9" in found.text and "released 2026-10-07" in found.text, found
    if status == "warn":
        assert "over three months old" in found.text and "may not work" in found.text
        assert "brew upgrade signal-cli" in found.fix and "estia-deploy" in found.fix
        assert found.fix.index("brew unpin signal-cli") < found.fix.index("brew upgrade signal-cli")  # signal-02
    probe.signal_version = "signal-cli 0.14.99"  # newer than the table: its age cannot be told (release review D2)
    found = by_part(run(write_config(home, MINIMAL), probe))["signal-cli"]
    assert found.status == "warn" and "released" not in found.text, found
    assert "release day unknown" in found.text and "update Estia" in found.fix


def calls_config(home: Path) -> Path:
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # socket paths are at most 104 bytes
    return write_config(home, MINIMAL + f'[voice]\nurl = "http://127.0.0.1:8150"\n[calls]\nsocket_dir = "{short}"\n')


def test_with_calls_the_signal_cli_line_is_the_java_build_she_runs(home: Path) -> None:
    """With calls the bridge runs the Java build (pinned at its own version), never Homebrew's: the age line read
    Homebrew's, so it never warned about the program that talks to Signal (release review D2)."""
    path = calls_config(home)
    cfg = load_config(path)
    probe = FakeProbe(home)
    probe.java = home / "jdk"
    probe.signal_versions = {"signal-cli": "signal-cli 0.14.9", str(cfg.call_signal_cli): "signal-cli 0.14.4"}
    parts = by_part(run(path, probe))
    found = parts["signal-cli"]
    assert found.status == "warn" and "0.14.4" in found.text and "over three months old" in found.text, found
    assert "Java build" in found.text and "0.14.9" not in found.text
    env = probe.version_envs[probe.versions_asked.index(str(cfg.call_signal_cli))]
    assert env["JAVA_HOME"] == str(home / "jdk") and "-Djava.net.preferIPv4Stack=true" in env["JAVA_OPTS"]
    builds = parts["signal-cli builds"]
    assert builds.status == "warn" and "0.14.4" in builds.text and "0.14.9" in builds.text, builds
    assert "brew pin signal-cli" in builds.fix and "estia-deploy" in builds.fix
    probe.signal_versions[str(cfg.call_signal_cli)] = "signal-cli 0.14.9"  # one version: no second line
    parts = by_part(run(path, probe))
    assert parts["signal-cli"].status == "ok" and "signal-cli builds" not in parts


def test_a_java_build_that_does_not_answer_is_named_by_its_folder(home: Path) -> None:
    """The Java build's folder carries its version (signal-cli-<version>-jvm/signal-cli-<version>, which the update
    checks): read from there when it cannot be asked (no Java)."""
    path = calls_config(home)
    cfg = load_config(path)
    probe = FakeProbe(home)
    program = Path(cfg.call_signal_cli)
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\n")
    probe.signal_versions = {"signal-cli": "signal-cli 0.14.9", str(cfg.call_signal_cli): None}
    found = by_part(run(path, probe))["signal-cli"]
    assert "signal-cli 0.14.9" in found.text and "Java build" in found.text, found


def test_a_java_build_that_is_not_there_has_no_version(home: Path) -> None:
    """Review host-jvm-version: doctor named a version for a path that does not exist (the folder's name alone). With
    no Java build, the line is Homebrew's, and the Calls line names what is missing."""
    path = calls_config(home)
    cfg = load_config(path)
    assert not Path(cfg.call_signal_cli).exists()
    probe = FakeProbe(home)
    probe.signal_versions = {"signal-cli": "signal-cli 0.14.9", str(cfg.call_signal_cli): None}
    parts = by_part(run(path, probe))
    assert "Java build" not in parts["signal-cli"].text and "signal-cli builds" not in parts, parts["signal-cli"]
    assert parts["Calls"].status == "fail" and "signal-cli" in parts["Calls"].text


def test_the_signal_cli_release_table_holds_the_versions_estia_installs() -> None:
    from local_assistant import doctor

    assert "0.14.9" in doctor.SIGNAL_CLI_RELEASED  # the JVM build's default (tools/call-tunnel)
    assert all(version.startswith(f"{SIGNAL_CLI_SERIES}.") for version in doctor.SIGNAL_CLI_RELEASED)


@pytest.mark.parametrize(
    ("version", "status"), [("signal-cli 0.14.9", "ok"), ("signal-cli 0.13.24", "warn"), ("signal-cli 0.15.0", "warn")]
)
def test_doctor_names_a_signal_cli_of_another_version(home: Path, version: str, status: str) -> None:
    """Registration and the bridge use signal-cli 0.14's requests (finding 20). One newer than Estia supports asks
    for a newer Estia: Homebrew never brings an older one back (release review D10)."""
    probe = FakeProbe(home)
    probe.signal_version = version
    found = by_part(run(write_config(home, MINIMAL), probe))["signal-cli"]
    assert found.status == status and version.split()[1] in found.text
    if version.endswith("0.13.24"):
        assert "0.14" in found.text and "brew upgrade signal-cli" in found.fix
    if version.endswith("0.15.0"):
        assert "newer than Estia supports" in found.text and "git pull" in found.fix, found
        assert "next Estia release" in found.fix and "brew" not in found.fix
    probe.signal_version = None  # not read: no line
    assert "signal-cli" not in by_part(run(write_config(home, MINIMAL), probe))


def test_the_bot_number_must_be_registered(home: Path) -> None:
    (home / ".local/share/signal-cli/data/accounts.json").write_text(json.dumps({"accounts": []}))
    signal = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Signal number"]
    assert signal.status == "fail" and "register" in signal.fix and NUMBER not in signal.text + signal.fix


BOT_ACI = "99999999-8888-4777-8666-555555555555"  # her account without a number (made up)


def test_her_account_without_a_number_is_checked_by_its_account_id(home: Path) -> None:
    """Signal Login (23 Oct): signal-cli lists the account with no number and its Account ID as uuid; doctor's line is
    "Signal account" then, ✓ once registered, ✗ when signal-cli has none, and never names the ID."""
    accounts = home / ".local/share/signal-cli/data/accounts.json"
    old_number = {"number": NUMBER, "path": "123456", "uuid": ACI}  # her old number's account stays as it is
    accounts.write_text(json.dumps({"accounts": [old_number, {"number": None, "path": "654321", "uuid": BOT_ACI}]}))
    (accounts.parent / "654321").write_text(json.dumps({"version": 8, "registered": True}))
    text = f'bot_number = "{BOT_ACI}"\nallowed_acis = ["{ACI}"]\n'
    findings = run(write_config(home, text), FakeProbe(home))
    line = by_part(findings)["Signal account"]
    assert line.status == "ok" and "without a number" in line.text and "registered" in line.text
    assert "Signal number" not in by_part(findings) and BOT_ACI not in render(findings)
    accounts.write_text(json.dumps({"accounts": [old_number]}))  # only the old number: not taken over yet
    moved = FakeProbe(home)
    moved.own = True
    line = by_part(run(write_config(home, text), moved))["Signal account"]
    assert line.status == "fail" and "signal-login" in line.fix and BOT_ACI not in line.text + line.fix


def test_before_her_own_account_the_fix_for_an_account_not_moved_here_is_the_by_hand_steps(home: Path) -> None:
    """Setup skips the account step once a config is set up, and `estia signal-login` refuses before the move
    (review S10): the fix names the by-hand steps of setup_assistant.md 2.3 B, never scripts/setup.sh."""
    accounts = home / ".local/share/signal-cli/data/accounts.json"
    accounts.write_text(json.dumps({"accounts": []}))
    text = f'bot_number = "{BOT_ACI}"\nallowed_acis = ["{ACI}"]\n'
    line = by_part(run(write_config(home, text), FakeProbe(home)))["Signal account"]
    assert line.status == "fail" and line.fix == prompts.DOCTOR_SIGNAL_ACCOUNT_BY_HAND
    assert "by hand" in line.fix and "2.3, B" in line.fix
    assert "scripts/setup.sh" not in line.fix and "signal-login" not in line.fix


def test_optional_parts_are_checked_only_when_turned_on(home: Path) -> None:
    probe = FakeProbe(home)
    parts = by_part(run(write_config(home, MINIMAL), probe))
    assert not {"Web search", "Google Maps", "OpenStreetMap", "Mail", "Calendar", "Voice", "Calls", "Spotify"} & set(
        parts
    )
    assert probe.asked == ["http://127.0.0.1:11434/api/tags", "http://127.0.0.1:11434/api/version"]  # Ollama only


def test_web_search_needs_a_private_key_file_and_never_shows_a_key(home: Path) -> None:
    """Review docs-default-web-pick-leaves-x: web search is on by default and its key may be left for later, so no key
    is a "!", never a ✗ (doctor reached no "no ✗" on the recommended path), and its fix asks for the key itself:
    scripts/set-web-keys.sh worked only with a key in the login keychain."""
    path = write_config(home, MINIMAL + "[web]\n")
    web = by_part(run(path, FakeProbe(home)))["Web search"]
    assert web.status == "warn" and web.fix == (
        "scripts/setup.sh --extras, then Web search: it asks for Ollama's key (ollama.com/settings/keys), hidden"
    )
    moved = FakeProbe(home)
    moved.own = True
    web = by_part(run(path, moved))["Web search"]
    assert web.status == "warn" and web.fix == (
        "/usr/bin/sudo /usr/local/estia/bin/estia secret set web.env OLLAMA_API_KEY "
        "(Ollama's key from ollama.com/settings/keys, at a hidden prompt)"
    )
    keys = home / ".config/local-assistant/web.env"
    keys.write_text("OLLAMA_API_KEY=secret-value-123\n")
    keys.chmod(0o600)
    findings = run(path, FakeProbe(home))
    assert by_part(findings)["Web search"].status == "untested"  # set up; the bridge has not searched yet
    assert "secret-value-123" not in render(findings)


def test_services_that_do_not_answer_are_named(home: Path) -> None:
    text = MINIMAL + '[voice]\nurl = "http://127.0.0.1:8150"\n[mail]\njev_url = "http://127.0.0.1:8149"\n'
    text += 'tenant_id = "11111111-2222-4333-8444-555555555555"\nclient_id = "66666666-7777-4888-9999-000000000000"\n'
    text += '[[mail.accounts]]\nname = "home"\naddress = "alex@example.com"\n'
    parts = by_part(run(write_config(home, text), FakeProbe(home)))
    assert parts["Voice"].status == "fail" and "install-voice-daemon.sh" in parts["Voice"].fix
    assert parts["Mail"].status == "fail" and "mail-login.sh" in parts["Mail"].fix  # not signed in yet
    assert parts["Mail pre-filter (AgentJev)"].status == "warn"  # optional: mail still works without it


def test_doctor_says_her_configured_name(home: Path) -> None:
    probe = FakeProbe(home)
    probe.system = ("linux", "x86_64")
    named = by_part(run(write_config(home, MINIMAL + 'name = "Tiffany"\n'), probe))
    assert named["Mac"].fix == "Tiffany needs a Mac with Apple silicon (M1 or later)"
    assert named["Personality"].text == "no soul.md: Tiffany's built-in style, hearth (soul.example.md: your own)"
    assert named["Personality"].status == "ok"  # the default, nothing to fix
    plain = by_part(run(write_config(home, MINIMAL), probe))
    assert (
        plain["Mac"].fix == "Estia needs a Mac with Apple silicon (M1 or later)"
        and "Estia" in plain["Personality"].text
    )
    broken = by_part(run(write_config(home, MINIMAL + 'name = "R2-D2"\n'), probe))
    assert broken["Mac"].fix.startswith("Estia needs") and "name must be 1-20 letters" in broken["Config"].text


def test_doctor_names_the_built_in_style_chosen(home: Path) -> None:
    chosen = by_part(run(write_config(home, MINIMAL + '[personality]\nstyle = "playful"\n'), FakeProbe(home)))
    assert chosen["Personality"].text == "no soul.md: Estia's built-in style, playful (soul.example.md: your own)"


def test_doctor_says_when_the_calm_on_worry_line_is_off(home: Path) -> None:
    soul = home / ".config/local-assistant/soul.md"
    soul.parent.mkdir(parents=True, exist_ok=True)
    soul.write_text("Be silly.")
    soul.chmod(0o600)
    on = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Personality"]
    assert (on.status, on.text) == ("ok", "soul.md")
    off = by_part(run(write_config(home, MINIMAL + "[personality]\ncalm_on_worry = false\n"), FakeProbe(home)))
    assert off["Personality"].status == "ok"  # the user's choice: nothing to fix
    assert off["Personality"].text == "soul.md; the calm-on-worry line after it is off ([personality] calm_on_worry)"


def test_doctor_says_what_texts_and_calls_each_get_from_a_soul_with_parts(home: Path) -> None:
    """A soul.md with [chat] and [call] lines: doctor names each kind's characters, or the built-in style a kind with
    nothing in the file keeps (a markers-only file is in use, yet changes nothing)."""
    soul = home / ".config/local-assistant/soul.md"
    soul.parent.mkdir(parents=True, exist_ok=True)
    soul.write_text("Who: a warm sidekick.\n[chat]\n- Short.\n[call]\n- Spoken.")
    soul.chmod(0o600)
    found = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Personality"]
    assert (found.status, found.text) == ("ok", "soul.md: 30 characters for texts, 31 for calls")
    soul.write_text("[call]\n- Spoken.")
    found = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Personality"]
    assert found.text == "soul.md: nothing for texts (the built-in style, hearth), 9 characters for calls"
    soul.write_text("[chat]\n[call]")
    calm_off = MINIMAL + "[personality]\ncalm_on_worry = false\n"
    found = by_part(run(write_config(home, calm_off), FakeProbe(home)))["Personality"]
    assert found.text == (
        "soul.md: nothing for texts or calls (the built-in style, hearth); the calm-on-worry line after it is off "
        "([personality] calm_on_worry)"
    )


def test_a_soul_with_a_problem_is_a_warning(home: Path) -> None:
    soul = home / ".config/local-assistant/soul.md"
    soul.parent.mkdir(parents=True, exist_ok=True)
    soul.write_text("Be kind.")
    soul.chmod(0o644)
    findings = run(write_config(home, MINIMAL), FakeProbe(home))
    assert by_part(findings)["Personality"].status == "warn" and exit_code(findings) == 0


def test_the_profile_picture_is_estias_mark_your_own_or_none(home: Path) -> None:
    probe = FakeProbe(home)
    estia = by_part(run(write_config(home, MINIMAL), probe))["Profile picture"]
    assert estia == Finding("ok", "Profile picture", "Estia's mark (docs/brand/estia-avatar.png)")
    (home / "her.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    own = by_part(run(write_config(home, MINIMAL + 'avatar = "~/her.png"\n'), probe))["Profile picture"]
    assert own == Finding("ok", "Profile picture", "her.png")
    none = by_part(run(write_config(home, MINIMAL + 'avatar = ""\n'), probe))["Profile picture"]
    assert none.status == "ok" and none.text.startswith("none")
    tiffany = by_part(run(write_config(home, MINIMAL + 'name = "Tiffany"\n'), probe))["Profile picture"]
    assert tiffany == estia  # owner, 25 Oct: the mark whatever her name, no letters


def test_an_old_letter_picture_in_the_config_shows_the_mark_not_a_missing_file(home: Path) -> None:
    """Setup's doctor after the update: a config that names one of the letter pictures (gone) gets the mark."""
    old = 'avatar = "/usr/local/estia/current/docs/brand/letters/t.png"\n'
    picture = by_part(run(write_config(home, MINIMAL + 'name = "Tiffany"\n' + old), FakeProbe(home)))["Profile picture"]
    assert picture == Finding("ok", "Profile picture", "Estia's mark (docs/brand/estia-avatar.png)")


def test_a_picture_that_cannot_be_used_is_a_warning_not_a_failure(home: Path) -> None:
    """The bridge starts without it (Signal keeps the picture it has), so doctor warns and setup goes on."""
    findings = run(write_config(home, MINIMAL + 'name = "Tiffany"\navatar = "~/gone.png"\n'), FakeProbe(home))
    picture = by_part(findings)["Profile picture"]
    assert picture.status == "warn"
    assert picture.text == f"{home / 'gone.png'}: no such file; Tiffany runs without it (Signal keeps the one it has)"
    assert "avatar" in picture.fix
    assert by_part(findings)["Config"].status == "ok" and exit_code(findings) == 0


def test_render_marks_each_line_and_gives_the_fix() -> None:
    text = render([Finding("ok", "Mac", "Apple silicon"), Finding("fail", "Tools", "missing: uv", "brew install uv")])
    assert text.splitlines() == ["✓ Mac: Apple silicon", "✗ Tools: missing: uv", "    → brew install uv"]


def test_mail_names_each_mailbox_that_still_needs_a_sign_in(home: Path) -> None:
    text = MINIMAL + '[mail]\ntenant_id = "11111111-2222-4333-8444-555555555555"\n'
    text += 'client_id = "66666666-7777-4888-9999-000000000000"\n'
    text += '[[mail.accounts]]\nname = "home"\naddress = "alex@example.com"\n'
    text += '[[mail.accounts]]\nname = "work"\naddress = "alex@work.example"\n'
    path = write_config(home, text)
    TokenStore(load_config(path).mail_token_path).save("alex@example.com", "rt-home")
    findings = run(path, FakeProbe(home))
    mail = by_part(findings)["Mail"]
    assert mail.status == "fail" and mail.text == "not signed in: work (1 of 2 signed in)"
    assert mail.fix == "scripts/mail-login.sh <address of work>" and "rt-home" not in render(findings)


def test_the_signal_number_line_reads_only_signal_clis_own_account_file(home: Path, tmp_path: Path) -> None:
    """One reader for doctor, setup and the move (hostsec): an account file named by a path elsewhere is never read
    (review 15 Oct, 6)."""
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps({"registered": False}))
    accounts = home / ".local/share/signal-cli/data/accounts.json"
    accounts.write_text(json.dumps({"accounts": [{"number": NUMBER, "path": str(elsewhere)}]}))
    line = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Signal number"]
    assert line.status == "ok"  # not read: as unreadable, which changes nothing
    (accounts.parent / "111111").write_text(json.dumps({"registered": False}))
    accounts.write_text(json.dumps({"accounts": [{"number": NUMBER, "path": "111111"}]}))
    line = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Signal number"]
    assert line.status == "fail"


def test_the_mac_checks_get_her_name_for_the_nuclear_line(home: Path) -> None:
    """The firewall's neutral line names the Mac "used for <her name> alone" (owner's words, 20 Oct), the config's."""
    probe = FakeProbe(home)
    run(write_config(home, MINIMAL + 'name = "Tiffany"\n'), probe)
    assert probe.names == ["Tiffany"]


def test_several_allowed_accounts_are_told_what_they_share(home: Path) -> None:
    """Review I3 (owner, 21 Oct: warn now, separate per account later): every allowed account shares one chat history,
    the profile, mail, calendars, documents, the digest and the location; doctor says so in the docs' words, in lines
    that fit the terminal (hostsec.TERMINAL, owner, 20 Oct)."""
    partner = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
    two = f'bot_number = "{NUMBER}"\nallowed_acis = ["{ACI}", "{partner}"]\n'
    findings = run(write_config(home, two), FakeProbe(home))
    shared = [finding for finding in findings if finding.part == "Allowed accounts"]
    assert [(finding.status, finding.fix) for finding in shared] == [("warn", prompts.SHARED_ACCOUNTS_FIX)]
    assert " ".join(shared[0].text.split()) == f"2 in the config. {prompts.SHARED_ACCOUNTS}"
    assert all(len(shown) <= hostsec.TERMINAL for shown in render(shared).splitlines())
    assert exit_code(findings) == 0  # a warning, never a failure: a household may choose it
    assert ACI not in render(findings) and partner not in render(findings)
    one = run(write_config(home, MINIMAL), FakeProbe(home))
    assert not [finding for finding in one if finding.part == "Allowed accounts"]


def test_the_example_placeholders_must_be_replaced(home: Path) -> None:
    accounts = home / ".local/share/signal-cli/data/accounts.json"
    accounts.write_text(json.dumps({"accounts": [{"number": "+300000000000"}]}))
    text = 'bot_number = "+300000000000"\nallowed_acis = ["00000000-0000-0000-0000-000000000000"]\n'
    findings = run(write_config(home, text), FakeProbe(home))
    config = by_part(findings)["Config"]
    assert config.status == "fail" and "placeholder" in config.text
    assert "bot_number" in config.text and "allowed_acis" in config.text and "2.4" in config.fix
    assert exit_code(findings) == 1


def test_calls_need_java_as_well_as_their_two_programs(home: Path) -> None:
    probe = FakeProbe(home)
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # socket paths are at most 104 bytes
    path = write_config(home, MINIMAL + f'[voice]\nurl = "http://127.0.0.1:8150"\n[calls]\nsocket_dir = "{short}"\n')
    cfg = load_config(path)
    for program in (cfg.call_signal_cli, cfg.call_tunnel):
        program.parent.mkdir(parents=True, exist_ok=True)
        program.write_text("#!/bin/sh\n")
    calls = by_part(run(path, probe))["Calls"]
    assert calls.status == "fail" and "Java" in calls.text and "java_home" in calls.fix
    java = home / "jdk"
    (java / "bin").mkdir(parents=True)
    (java / "bin/java").write_text("#!/bin/sh\n")
    probe.java = java
    assert by_part(run(path, probe))["Calls"].status == "ok"


def test_doctor_names_a_call_helper_built_from_other_sources(home: Path) -> None:
    """Second deep scan, signal-06: build.sh writes a stamp beside the call helper (its sources by their pins and
    local.patch's sha256); a helper whose stamp is missing or names other sources than this Estia's is a "!", with
    the build again (and in her own macOS account the update)."""
    from local_assistant import config

    probe = FakeProbe(home)
    path = calls_config(home)
    cfg = load_config(path)
    for program in (cfg.call_signal_cli, cfg.call_tunnel):
        program.parent.mkdir(parents=True, exist_ok=True)
        program.write_text("#!/bin/sh\n")
    stamp = cfg.call_tunnel.with_name(cfg.call_tunnel.name + ".stamp")
    stamp.write_text("pr5_head=old\n")
    helper = by_part(run(path, probe))["Call helper"]
    assert helper.status == "warn" and helper.fix == "tools/call-tunnel/build.sh", helper
    assert helper.text == prompts.DOCTOR_CALL_HELPER_OLD
    probe.own = True
    assert by_part(run(path, probe))["Call helper"].fix == prompts.DOCTOR_CALL_HELPER_OLD_FIX_OWN
    stamp.write_text(str(config.tunnel_stamp()))
    assert "Call helper" not in by_part(run(path, probe))


def test_in_her_own_account_the_calls_fix_ends_with_the_update_never_java_home(home: Path) -> None:
    """Review host-doctor-calls-fix: in her own macOS account the fix named the checkout's builds and "set [calls]
    java_home", which never reach her release (a Homebrew java_home fails her programs' check). There the builds go in
    with the update, and Java is Homebrew's openjdk, which the update copies."""
    probe = FakeProbe(home)
    probe.own = True
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # socket paths are at most 104 bytes
    path = write_config(home, MINIMAL + f'[voice]\nurl = "http://127.0.0.1:8150"\n[calls]\nsocket_dir = "{short}"\n')
    cfg = load_config(path)
    calls = by_part(run(path, probe))["Calls"]  # nothing there: the two builds and Java
    assert calls.status == "fail" and "java_home" not in calls.fix, calls
    assert "install-signal-cli-jvm.sh" in calls.fix and "brew install openjdk" in calls.fix
    assert calls.fix.endswith("then /usr/bin/sudo /usr/local/estia/bin/estia-deploy (it copies them into her release)")
    for program in (cfg.call_signal_cli, cfg.call_tunnel):
        program.parent.mkdir(parents=True, exist_ok=True)
        program.write_text("#!/bin/sh\n")
    calls = by_part(run(path, probe))["Calls"]  # Java alone
    assert calls.text == "no Java for the JVM signal-cli" and "java_home" not in calls.fix, calls
    assert (
        calls.fix
        == "brew install openjdk, then /usr/bin/sudo /usr/local/estia/bin/estia-deploy (it copies it into her release)"
    )


def web_keys(home: Path, at: float = OLD) -> None:
    keys = home / ".config/local-assistant/web.env"
    keys.parent.mkdir(parents=True, exist_ok=True)
    keys.write_text("OLLAMA_API_KEY=o-key\nGOOGLE_MAPS_API_KEY=g-key\n")
    keys.chmod(0o600)
    os.utime(keys, (at, at))


def mail_tokens(path: Path, saved: dict[str, float], signed_in: dict[str, float] | None = None) -> None:
    """The mailboxes' refresh tokens (address → when saved), as scripts/mail-login.sh and the bridge leave them: signed
    in when saved, unless `signed_in` says when (the bridge renewed the token since)."""
    tokens = load_config(path).mail_token_path
    tokens.parent.mkdir(parents=True, exist_ok=True)
    entries = {
        a: {"refresh_token": f"rt-{a[5:9]}", "updated_at": t, "signed_in_at": (signed_in or {}).get(a, t)}
        for a, t in saved.items()
    }
    tokens.write_text(json.dumps(entries))
    tokens.chmod(0o600)


def recorded(path: Path, at: float = NOW - 300) -> PartStatus:
    """The bridge's recorder on the config's status file, at a fixed time."""
    return PartStatus(load_config(path).data_dir / "status.json", clock=lambda: at)


def test_a_part_that_is_set_up_but_never_used_is_not_tested_yet(home: Path) -> None:
    web_keys(home)
    findings = run(write_config(home, MINIMAL + "[web]\n[maps]\n"), FakeProbe(home))
    web = by_part(findings)["Web search"]
    assert (web.status, web.text) == ("untested", "set up, not tested yet (keys: OLLAMA_API_KEY)")
    assert "○ Web search: set up, not tested yet (keys: OLLAMA_API_KEY)" in render(findings).splitlines()
    assert by_part(findings)["OpenStreetMap"].text == "set up, not tested yet"
    assert by_part(findings)["Google Maps"].text == "set up, not tested yet"  # its key is in web.env
    assert exit_code(findings) == 0  # a fresh install can reach "no ✗"


def test_a_part_that_worked_is_green_with_its_time(home: Path) -> None:
    web_keys(home)
    path = write_config(home, MINIMAL + "[web]\n")
    recorded(path).ok("web")
    web = by_part(run(path, FakeProbe(home)))["Web search"]
    assert (web.status, web.text) == ("ok", "working, last used 11:35 (keys: OLLAMA_API_KEY)")


def test_a_part_that_fails_is_red_with_its_fix(home: Path) -> None:
    web_keys(home)
    path = write_config(home, MINIMAL + "[web]\n")
    recorded(path, NOW - 2 * 86_400).failed("web", "refused", 401)
    findings = run(path, FakeProbe(home))
    web = by_part(findings)["Web search"]
    assert web.status == "fail" and web.text.startswith("failing since 6 Oct 11:40: refused by the service (401)")
    assert web.fix == "check the keys: scripts/set-web-keys.sh" and exit_code(findings) == 1
    assert "    → check the keys: scripts/set-web-keys.sh" in render(findings).splitlines()


def test_each_signed_in_mailbox_gets_its_own_state(home: Path) -> None:
    path = write_config(home, MINIMAL + MAIL)
    mail_tokens(path, {"alex@example.com": OLD, "alex@work.example": OLD})
    status = recorded(path)
    status.ok("mail:home")
    status.failed("mail:work", "sign_in")  # a revoked token: the file is there, the sign-in is not
    findings = run(path, FakeProbe(home))
    parts = by_part(findings)
    assert "Mail" not in parts
    assert (parts["Mail (home)"].status, parts["Mail (home)"].text) == ("ok", "working, last read 11:35")
    work = parts["Mail (work)"]
    assert work.status == "fail" and work.text == "failing since 11:35: sign-in expired"
    assert work.fix == "scripts/mail-login.sh <address of work>"
    assert "rt-" not in render(findings) and "alex@" not in render(findings)


def test_a_mailbox_without_a_sign_in_keeps_its_setup_line(home: Path) -> None:
    path = write_config(home, MINIMAL + MAIL)
    TokenStore(load_config(path).mail_token_path).save("alex@example.com", "rt-home")
    parts = by_part(run(path, FakeProbe(home)))
    assert parts["Mail"].status == "fail" and parts["Mail"].text == "not signed in: work (1 of 2 signed in)"
    assert parts["Mail (home)"].status == "untested" and "Mail (work)" not in parts


def helpers_at(monkeypatch: pytest.MonkeyPatch, python: Path) -> None:
    """The calendar and device helpers as doctor will look for them (the repo's own may or may not be built)."""

    def load(path: Path) -> Config:
        return replace(load_config(path), calendar_helper=(str(python), "helper.py"), tapo_helper=(str(python),))

    monkeypatch.setattr(doctor, "load_config", load)


def test_a_missing_setup_file_beats_a_good_record(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helpers_at(monkeypatch, home / "no-such-python")
    path = write_config(home, MINIMAL + "[calendar]\n")
    recorded(path).ok("calendar")
    calendar = by_part(run(path, FakeProbe(home)))["Calendar"]
    assert calendar.status == "fail" and calendar.fix == "cd tools/ical && uv sync"


def test_every_optional_part_shows_its_state(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    secret = home / ".config/local-assistant/calendar.env"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("ICAL_URL=https://calendar.example.com/private-x/basic.ics\n")
    secret.chmod(0o600)
    os.utime(secret, (OLD, OLD))
    text = MINIMAL + '[voice]\nurl = "http://127.0.0.1:8150"\n[calendar]\n'
    text += '[[tapo.devices]]\nname = "lamp"\nhost = "192.168.1.20"\nkind = "light"\n'
    text += '[spotify]\nclient_id = "0123456789abcdef0123456789abcdef"\n'
    path = write_config(home, text)
    tokens = load_config(path).spotify_token_path
    tokens.parent.mkdir(parents=True, exist_ok=True)
    tokens.write_text("{}")
    tokens.chmod(0o600)
    status = recorded(path)
    status.ok("voice")
    status.failed("calendar", "gone")
    status.failed("devices", "no_answer")
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:8150/health"] = {"stt": "ready", "tts": "ready"}
    parts = by_part(run(path, probe))
    assert (parts["Voice"].status, parts["Voice"].text) == ("ok", "working, last used 11:35")
    assert parts["Calendar"].status == "fail" and parts["Calendar"].fix == "scripts/set-calendar-url.sh"
    lights = parts["Lights and plugs"]
    assert lights.status == "fail" and lights.text == "failing since 11:35: no answer (1 device(s))"
    assert "check the device" in lights.fix
    assert (parts["Spotify"].status, parts["Spotify"].text) == ("untested", "set up, not tested yet")


def test_an_unreadable_status_file_means_not_tested_yet(home: Path) -> None:
    web_keys(home)
    path = write_config(home, MINIMAL + "[web]\n")
    status_file = load_config(path).data_dir / "status.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text("{broken")
    assert by_part(run(path, FakeProbe(home)))["Web search"].status == "untested"


def test_a_failure_whose_fix_was_saved_since_is_not_tested_yet(home: Path) -> None:
    web_keys(home)
    path = write_config(home, MINIMAL + "[web]\n")
    recorded(path).failed("web", "refused", 401)
    web_keys(home, at=NOW - 60)  # scripts/set-web-keys.sh, after the failure
    findings = run(path, FakeProbe(home))
    web = by_part(findings)["Web search"]
    assert (web.status, web.text, web.fix) == ("untested", "set up again, not tested yet (keys: OLLAMA_API_KEY)", "")
    assert "○ Web search: set up again, not tested yet (keys: OLLAMA_API_KEY)" in render(findings).splitlines()
    assert exit_code(findings) == 0


def test_saving_the_setup_again_does_not_hide_a_failure_it_cannot_fix(home: Path) -> None:
    web_keys(home)
    path = write_config(home, MINIMAL + "[web]\n")
    recorded(path).failed("web", "busy", 429)
    web_keys(home, at=NOW - 60)
    assert by_part(run(path, FakeProbe(home)))["Web search"].status == "fail"


def test_a_mailbox_signed_in_again_since_its_failure_is_not_tested_yet(home: Path) -> None:
    path = write_config(home, MINIMAL + MAIL)
    status = recorded(path)
    status.failed("mail:home", "sign_in")
    status.failed("mail:work", "refused", 403)
    # home: scripts/mail-login.sh since; work: the bridge rotated its token, which does not fix a refusal
    mail_tokens(path, {"alex@example.com": NOW - 60, "alex@work.example": NOW - 60})
    parts = by_part(run(path, FakeProbe(home)))
    assert (parts["Mail (home)"].status, parts["Mail (home)"].text) == ("untested", "set up again, not tested yet")
    assert parts["Mail (work)"].status == "fail"
    mail_tokens(path, {"alex@example.com": NOW - 600, "alex@work.example": NOW - 600})  # saved before it failed
    assert by_part(run(path, FakeProbe(home)))["Mail (home)"].status == "fail"
    mail_tokens(path, {"alex@example.com": NOW - 60}, signed_in={"alex@example.com": OLD})  # only renewed since
    assert by_part(run(path, FakeProbe(home)))["Mail (home)"].status == "fail"


@pytest.mark.parametrize(
    ("saved", "state"),
    [
        ({"authorized_at": NOW - 60}, "untested"),  # scripts/spotify-login.sh, after the failure
        ({"authorized_at": OLD, "ended_at": NOW - 300}, "fail"),  # the bridge noting the end is no new sign-in
    ],
)
def test_spotify_signed_in_again_since_its_failure_is_not_tested_yet(
    home: Path, saved: dict[str, float], state: str
) -> None:
    path = write_config(home, MINIMAL + '[spotify]\nclient_id = "0123456789abcdef0123456789abcdef"\n')
    recorded(path).failed("spotify", "sign_in")
    tokens = load_config(path).spotify_token_path
    tokens.parent.mkdir(parents=True, exist_ok=True)
    tokens.write_text(json.dumps({"refresh_token": "rt-spotify", **saved}))
    tokens.chmod(0o600)
    assert by_part(run(path, FakeProbe(home)))["Spotify"].status == state


@pytest.mark.parametrize(
    ("part", "label", "reason", "saved", "state"),
    [
        ("devices", "Lights and plugs", "login", "tapo.env", "untested"),  # scripts/set-tapo-credentials.sh
        ("devices", "Lights and plugs", "no_answer", "tapo.env", "fail"),  # a new login does not wake a device
        ("devices", "Lights and plugs", "helper_missing", "helper", "untested"),  # cd tools/tapo && uv sync
        ("devices", "Lights and plugs", "hue_key", "hue.env", "untested"),  # scripts/hue-pair.sh
        ("devices", "Lights and plugs", "certificate", "hue.env", "untested"),
        ("devices", "Lights and plugs", "not_paired", "hue.env", "untested"),
        ("devices", "Lights and plugs", "no_light", "hue.env", "fail"),  # pairing again does not bring a light back
        ("devices", "Lights and plugs", "no_light", "config", "untested"),  # the light's id corrected in config.toml
        ("devices", "Lights and plugs", "login", "config", "fail"),  # a config edit is no TP-Link login
        ("devices", "Lights and plugs", "hue_key", "tapo.env", "fail"),  # the TP-Link login is not the bridge's key
        ("calendar", "Calendar", "gone", "calendar.env", "untested"),  # scripts/set-calendar-url.sh
        ("calendar", "Calendar", "no_answer", "calendar.env", "fail"),
        ("calendar", "Calendar", "helper_missing", "helper", "untested"),  # cd tools/ical && uv sync
    ],
)
def test_a_saved_login_address_or_helper_counts_only_for_the_failure_it_fixes(
    home: Path, monkeypatch: pytest.MonkeyPatch, part: str, label: str, reason: Reason, saved: str, state: str
) -> None:
    helper = home / "python"  # uv sync makes it as a symlink: its own time counts, not its target's
    helper.symlink_to(Path(os.__file__))
    helpers_at(monkeypatch, helper)
    folder = home / ".config/local-assistant"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("tapo.env", "calendar.env", "hue.env"):
        (folder / name).write_text(
            "SAVED=yes\nCALENDAR_ICS_URL=https://calendar.google.com/calendar/ical/x/basic.ics\n"
        )
        (folder / name).chmod(0o600)
    for file in (folder / "tapo.env", folder / "calendar.env", folder / "hue.env", helper):
        os.utime(file, (OLD, OLD), follow_symlinks=False)
    text = MINIMAL + '[calendar]\n[[tapo.devices]]\nname = "lamp"\nhost = "192.168.1.20"\nkind = "light"\n'
    path = write_config(home, text)
    os.utime(path, (OLD, OLD))  # the config saved long before the failure
    recorded(path).failed(part, reason)
    changed = {"helper": helper, "config": path}.get(saved, folder / saved)
    os.utime(changed, (NOW - 60, NOW - 60), follow_symlinks=False)
    assert by_part(run(path, FakeProbe(home)))[label].status == state


VOICE = '[voice]\nurl = "http://127.0.0.1:8150"\n'
HEALTH = "http://127.0.0.1:8150/health"


def pocket_line(text: str) -> str:
    """Doctor's Pocket TTS voices line as it shows it: split to fit the terminal (hostsec.TERMINAL) after its mark."""
    return hostsec.terminal_text(text, len("! Pocket TTS voices: "))


def test_english_needs_nothing_more_from_the_voice_service(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready"}  # older than the languages: fine for English
    language = by_part(run(write_config(home, MINIMAL + VOICE), probe))["Language"]
    assert (language.status, language.text) == ("ok", "English (en)")


def test_another_language_needs_a_voice_service_that_has_it(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "languages": ["en", "nl", "de"]}
    path = write_config(home, 'language = "nl"\n' + MINIMAL + VOICE)
    findings = run(path, probe)
    assert by_part(findings)["Language"] == Finding("ok", "Language", "Nederlands (nl)")
    assert "✓ Language: Nederlands (nl)" in render(findings)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready"}
    old = by_part(run(path, probe))["Language"]
    assert (
        old.status == "fail" and old.text == "Nederlands (nl), but the voice service is older than the language feature"
    )
    assert old.fix == "scripts/install-voice-daemon.sh (asks for your Mac password: run it yourself)"
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "languages": ["en"]}
    assert by_part(run(path, probe))["Language"].status == "fail"
    del probe.answers[HEALTH]
    assert by_part(run(path, probe))["Language"].status == "warn"  # no answer: the Voice line fails already


def test_the_language_chosen_with_the_command_is_the_one_checked(home: Path) -> None:
    # an install that started in English and switched with /language nl: the doctor checks Dutch, the one in use
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready"}  # older than the language feature
    path = write_config(home, MINIMAL + VOICE)
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    store.set_state(LANGUAGE_KEY, "nl")
    running = by_part(run(path, probe))["Language"]  # the bridge has the database open
    assert running.status == "fail" and running.text.startswith("Nederlands (nl), but the voice service is older")
    store.close()  # the bridge stopped: its database is read as it is, nothing is created next to it
    stopped = by_part(run(path, probe))["Language"]
    assert stopped.status == "fail"
    assert stopped.fix == "scripts/install-voice-daemon.sh (asks for your Mac password: run it yourself)"
    assert sorted(item.name for item in database.parent.iterdir()) == ["assistant.db"]
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "languages": ["en", "nl"]}
    assert by_part(run(path, probe))["Language"] == Finding("ok", "Language", "Nederlands (nl)")


@pytest.mark.parametrize("stored", ["en", "xx", None])
def test_without_a_usable_choice_the_config_language_is_checked(home: Path, stored: str | None) -> None:
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "languages": ["en", "de"]}
    path = write_config(home, 'language = "de"\n' + MINIMAL + VOICE)
    database = load_config(path).db_path
    if stored is not None:
        database.parent.mkdir(parents=True)
        store = Store.open(database)
        store.set_state(LANGUAGE_KEY, stored)
        store.close()
    expected = "English (en)" if stored == "en" else "Deutsch (de)"
    assert by_part(run(path, probe))["Language"] == Finding("ok", "Language", expected)
    assert stored is not None or not database.exists()  # no database yet: none is created


def test_another_language_without_voice_is_text_only(home: Path) -> None:
    probe = FakeProbe(home)
    language = by_part(run(write_config(home, 'language = "ja"\n' + MINIMAL), probe))["Language"]
    assert (language.status, language.text) == ("ok", "日本語 (ja), text only (no [voice])")
    assert probe.asked == ["http://127.0.0.1:11434/api/tags", "http://127.0.0.1:11434/api/version"]  # Ollama only


SOURCES = (
    '[calendar]\n[[calendar.sources]]\nname = "family"\n[[calendar.sources]]\nname = "school-days"\n'
    '[[calendar.sources]]\nname = "office"\nmailbox = "work"\n'
)


def signed_in_long_ago(path: Path, address: str) -> TokenStore:
    tokens = TokenStore(load_config(path).mail_token_path)
    tokens.save(address, "rt-work")
    saved = json.loads(tokens.path.read_text())
    saved[address]["updated_at"] = OLD
    tokens.path.write_text(json.dumps(saved))
    return tokens


def save_addresses(home: Path, text: str, when: float = OLD) -> Path:
    env = home / ".config/local-assistant/calendar.env"
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(text)
    env.chmod(0o600)
    os.utime(env, (when, when))
    return env


def test_each_calendar_shows_its_own_setup(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    save_addresses(home, "CALENDAR_ICS_URL_FAMILY=https://calendar.google.com/calendar/ical/x/basic.ics\n")
    path = write_config(home, MINIMAL + MAIL + SOURCES)
    parts = by_part(run(path, FakeProbe(home)))
    assert (parts["Calendar (family)"].status, parts["Calendar (family)"].text) == (
        "untested",
        "set up, not tested yet",
    )
    school = parts["Calendar (school-days)"]
    assert school.status == "fail" and school.fix == "scripts/set-calendar-url.sh school-days"
    office = parts["Calendar (office)"]
    assert office.status == "fail" and office.fix == "scripts/mail-login.sh <address of work>"
    assert "Calendar" not in parts  # no line for a calendar that is not configured
    signed_in_long_ago(path, "alex@work.example")
    recorded(path).failed("calendar:office", "no_consent")
    office = by_part(run(path, FakeProbe(home)))["Calendar (office)"]
    assert office.status == "fail" and office.text == "failing since 11:35: Calendars.Read not granted yet"
    assert "Grant admin consent" in office.fix and "<address of work>" in office.fix


def test_an_outlook_calendar_alone_needs_no_calendar_helper(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helpers_at(monkeypatch, home / "no-such-python")  # tools/ical not set up, no calendar.env: not needed
    path = write_config(home, MINIMAL + MAIL + '[calendar]\n[[calendar.sources]]\nname = "office"\nmailbox = "work"\n')
    TokenStore(load_config(path).mail_token_path).save("alex@work.example", "rt-work")
    findings = run(path, FakeProbe(home))
    office = by_part(findings)["Calendar (office)"]
    assert (office.status, office.text) == ("untested", "set up, not tested yet")
    assert not [f for f in findings if f.part.startswith("Calendar") and f.part != "Calendar (office)"]


OFFICE = '[calendar]\n[[calendar.sources]]\nname = "office"\nmailbox = "work"\n'


def sign_in(path: Path, at: float, *, calendar: bool = True, renewed: bool = False) -> None:
    """scripts/mail-login.sh for the work mailbox at a given time (with or without Calendars.Read), or mail's refresh
    saving a renewed token."""
    tokens = TokenStore(load_config(path).mail_token_path, clock=lambda: at)
    tokens.save("alex@work.example", f"rt-{at:.0f}", signed_in=not renewed, calendar=calendar and not renewed)


@pytest.mark.parametrize(
    ("reason", "calendar", "state"),
    [
        ("sign_in", False, "untested"),  # any new sign-in of the mailbox
        ("no_consent", True, "untested"),  # a sign-in that asked for Calendars.Read
        ("no_consent", False, "fail"),  # --mail-only asks for no consent
    ],
)
def test_a_new_sign_in_counts_for_an_outlook_calendar(
    home: Path, monkeypatch: pytest.MonkeyPatch, reason: Reason, calendar: bool, state: str
) -> None:
    helpers_at(monkeypatch, home / "no-such-python")
    path = write_config(home, MINIMAL + MAIL + OFFICE)
    sign_in(path, OLD)
    recorded(path).failed("calendar:office", reason)
    assert by_part(run(path, FakeProbe(home)))["Calendar (office)"].status == "fail"
    sign_in(path, NOW - 60, calendar=calendar)
    assert by_part(run(path, FakeProbe(home)))["Calendar (office)"].status == state


@pytest.mark.parametrize("reason", ["no_consent", "sign_in"])
def test_mail_renewing_the_token_does_not_hide_an_outlook_calendar_failure(
    home: Path, monkeypatch: pytest.MonkeyPatch, reason: Reason
) -> None:
    helpers_at(monkeypatch, home / "no-such-python")
    path = write_config(home, MINIMAL + MAIL + OFFICE)
    sign_in(path, OLD)
    recorded(path).failed("calendar:office", reason)
    sign_in(path, NOW - 60, renewed=True)  # mail's hourly refresh, after the failure: no sign-in
    office = by_part(run(path, FakeProbe(home)))["Calendar (office)"]
    assert office.status == "fail" and "mail-login.sh" in office.fix


GOOGLE = "https://calendar.google.com/calendar/ical/x/basic.ics"


def google_addresses(home: Path, family: float, school: float) -> None:
    """Both Google calendars' addresses, each with the time scripts/set-calendar-url.sh saved it."""
    text = (
        f"CALENDAR_ICS_URL_FAMILY={GOOGLE}\nCALENDAR_SAVED_AT_FAMILY={family:.0f}\n"
        f"CALENDAR_ICS_URL_SCHOOL_DAYS={GOOGLE}\nCALENDAR_SAVED_AT_SCHOOL_DAYS={school:.0f}\n"
    )
    save_addresses(home, text, max(family, school))


def test_a_google_calendar_counts_only_its_own_saved_address(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    path = write_config(home, MINIMAL + MAIL + SOURCES)
    google_addresses(home, OLD, OLD)
    recorded(path).failed("calendar:family", "gone")
    google_addresses(home, OLD, NOW - 60)  # scripts/set-calendar-url.sh school-days, after family failed
    assert by_part(run(path, FakeProbe(home)))["Calendar (family)"].status == "fail"
    google_addresses(home, NOW - 60, NOW - 60)  # family's own address saved again
    assert by_part(run(path, FakeProbe(home)))["Calendar (family)"].status == "untested"
    text = f"CALENDAR_ICS_URL_FAMILY={GOOGLE}\nCALENDAR_ICS_URL_SCHOOL_DAYS={GOOGLE}\n"
    save_addresses(home, text, NOW - 60)  # saved by hand, no times: the file's time could be either calendar's
    assert by_part(run(path, FakeProbe(home)))["Calendar (family)"].status == "fail"


def test_the_one_calendar_needs_its_address_in_calendar_env(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    save_addresses(home, "CALENDAR_ICS_URL_FAMILY=https://calendar.google.com/calendar/ical/x/basic.ics\n")
    calendar = by_part(run(write_config(home, MINIMAL + "[calendar]\n"), FakeProbe(home)))["Calendar"]
    assert calendar.status == "fail" and calendar.fix == "scripts/set-calendar-url.sh"
    assert "https://" not in calendar.text


def local(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Europe/Amsterdam")).timestamp()


def test_doctor_shows_the_next_reminder_by_time_only(home: Path) -> None:
    probe = FakeProbe(home)
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # with calls: a "call me" reminder places a call
    path = write_config(home, MINIMAL + f'[voice]\nurl = "http://127.0.0.1:8150"\n[calls]\nsocket_dir = "{short}"\n')
    assert by_part(run(path, probe))["Reminders"] == Finding("ok", "Reminders", "none yet")  # no database: none made
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    store.add_reminder(ACI, "call mum", "2026-10-09 07:00", "Europe/Amsterdam", local("2026-10-09 07:00"), call=True)
    store.add_reminder(ACI, "take the pills", "2026-10-10 08:00", "Europe/Amsterdam", local("2026-10-10 08:00"))
    found = by_part(run(path, probe))["Reminders"]
    assert found == Finding("ok", "Reminders", "2 open · next Fri 9 Oct 07:00 (call) · 0 failed")
    assert "call mum" not in render(run(path, probe)) and "pills" not in render(run(path, probe))
    store.close()


def test_doctor_warns_on_failed_and_stuck_reminders(home: Path) -> None:
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL)
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    hour_ago = datetime.fromtimestamp(NOW - 3600, UTC).isoformat(timespec="seconds")
    failed = store.add_reminder(ACI, "x", "2026-10-08 10:00", "Europe/Amsterdam", NOW - 6000, at=hour_ago)
    store.finish_reminder(failed, "failed", "send_failed", at=hour_ago)
    long_ago = datetime.fromtimestamp(NOW - 3 * 3600, UTC).isoformat(timespec="seconds")
    stuck = store.add_reminder(ACI, "y", "2026-10-08 08:00", "Europe/Amsterdam", local("2026-10-08 08:00"), at=long_ago)
    note = store.add_outgoing(store.current_conversation().id, ACI, "note", "Reminder: y", None)
    store.fire_reminder(stuck, note, at=long_ago)
    store.close()
    found = by_part(run(path, probe))["Reminders"]
    assert found.status == "warn" and found.text == "1 open · next Thu 8 Oct 08:00 · 1 failed · 1 stuck"
    assert "/reminders" in found.fix


def test_doctor_has_no_reminders_line_with_them_off(home: Path) -> None:
    path = write_config(home, MINIMAL + "[reminders]\nenabled = false\n")
    assert "Reminders" not in by_part(run(path, FakeProbe(home)))


def test_without_calls_doctor_shows_no_call_marker(home: Path) -> None:
    path = write_config(home, MINIMAL)  # no [calls]: "call me" reminders are wake-up texts
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    store.add_reminder(ACI, "wake up", "2026-10-09 07:00", "Europe/Amsterdam", local("2026-10-09 07:00"), call=True)
    store.close()
    assert by_part(run(path, FakeProbe(home)))["Reminders"].text == "1 open · next Fri 9 Oct 07:00 · 0 failed"


def test_doctor_shows_her_own_messages_as_counts_and_reason_words(home: Path) -> None:
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL + "[initiative]\nenabled = true\n")
    found = by_part(run(path, probe))["Initiative"]  # no database yet
    assert found == Finding(
        "ok",
        "Initiative",
        "on: check-ins, look back, improvised note · quiet hours 22:00-08:00 · calendar notes off (no [calendar])",
    )
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    day_ago = datetime.fromtimestamp(NOW - 86_400, UTC).isoformat(timespec="seconds")
    out = store.add_outgoing(store.current_conversation().id, ACI, "text", "Did you get hold of the dentist?", None)
    entry = store.log_initiative("followup", "2026-10-07", "sent", message_id=out, at=day_ago)
    store.set_initiative_replied(entry, NOW - 80_000, at=day_ago)
    store.log_initiative("lookback", "2026-10-04", "skipped", reason="busy", at=day_ago)
    store.set_state("initiative:quiet", "1")
    store.close()
    found = by_part(run(path, probe))["Initiative"]
    assert found.status == "ok" and found.text.endswith(
        "· last 4 weeks: 1 sent, 1 answered within a day · paused (/quiet) · last skipped: look back busy"
    )
    assert "dentist" not in render(run(path, probe))


def test_doctor_never_counts_the_evening_note_in_her_own_messages(home: Path) -> None:
    """The evening note (owner, D4) is never counted: not in the counts, the skips or the warning."""
    path = write_config(home, MINIMAL + "[initiative]\nenabled = true\n")
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    day_ago = datetime.fromtimestamp(NOW - 86_400, UTC).isoformat(timespec="seconds")
    note = store.add_outgoing(store.current_conversation().id, ACI, "note", "Evening.", None)
    entry = store.log_initiative("evening", "2026-10-07", "sent", message_id=note, at=day_ago)
    store.set_initiative_replied(entry, NOW - 80_000, at=day_ago)
    store.log_initiative("evening", "2026-10-08", "skipped", reason="busy", at=day_ago)
    store.log_initiative("evening", "2026-10-09", "failed", reason="send_failed", at=day_ago)
    store.close()
    found = by_part(run(path, FakeProbe(home)))["Initiative"]
    assert found.status == "ok" and found.text.endswith("· last 4 weeks: 0 sent, 0 answered within a day"), found


def test_doctor_names_the_evening_note_its_time_and_whose_switch_holds_it(home: Path) -> None:
    """The evening note (owner, D4): off, on at its time by the user's /evening or by the config for the one allowed
    account, and a warning when the config's time reaches no one (several accounts, none typed /evening on)."""
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL)
    assert by_part(run(path, probe))["Evening note"] == Finding("ok", "Evening note", prompts.DOCTOR_EVENING_OFF)
    path = write_config(home, MINIMAL + '[initiative]\nevening = "21:00"\n')
    found = by_part(run(path, probe))["Evening note"]
    assert found == Finding("ok", "Evening note", "on at 21:00 ([initiative] evening, for the one allowed account)")
    second = "22222222-3333-4444-8555-666666666666"
    two = f'bot_number = "{NUMBER}"\nallowed_acis = ["{ACI}", "{second}"]\n[initiative]\nevening = "21:00"\n'
    path = write_config(home, two)
    found = by_part(run(path, probe))["Evening note"]
    assert (found.status, found.text) == ("warn", prompts.DOCTOR_EVENING_UNUSED.format(time="21:00"))
    assert found.fix == prompts.DOCTOR_EVENING_UNUSED_FIX
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    store.set_state("initiative:evening", json.dumps({"on": True, "at": "20:30", "aci": second, "since": 0}))
    store.close()
    found = by_part(run(path, probe))["Evening note"]
    assert found == Finding("ok", "Evening note", "on at 20:30 (/evening, for the account that typed it)")
    store = Store.open(database)
    store.set_state("initiative:evening", json.dumps({"on": False, "at": "20:30", "aci": second, "since": 0}))
    store.close()
    found = by_part(run(path, probe))["Evening note"]
    assert found == Finding("ok", "Evening note", prompts.DOCTOR_EVENING_OFF_BY_USER)
    assert second not in render(run(path, probe)) and ACI not in render(run(path, probe))  # never an account
    path = write_config(home, MINIMAL)  # the account that turned it on is no longer allowed
    store = Store.open(database)
    store.set_state("initiative:evening", json.dumps({"on": True, "at": "20:30", "aci": second, "since": 0}))
    store.close()
    assert by_part(run(path, probe))["Evening note"].text == prompts.DOCTOR_EVENING_GONE


def test_doctor_warns_when_one_of_her_messages_could_not_be_sent(home: Path) -> None:
    path = write_config(home, MINIMAL + "[calendar]\n[initiative]\nenabled = true\n")
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    now_iso = datetime.fromtimestamp(NOW - 600, UTC).isoformat(timespec="seconds")
    store.log_initiative("calendar", "2026-10-08", "failed", reason="send_failed", at=now_iso)
    store.close()
    found = by_part(run(path, FakeProbe(home)))["Initiative"]
    assert found.status == "warn" and "calendar notes" in found.text and "calendar change send_failed" in found.text


def test_doctor_says_her_own_messages_are_off_until_turned_on_and_who_turned_them_on_or_off(home: Path) -> None:
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL)  # opt-in
    assert by_part(run(path, probe))["Initiative"] == Finding("ok", "Initiative", prompts.DOCTOR_INITIATIVE_OFF)
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    store.set_state("initiative:enabled", "on")  # the user's /initiative on wins over the config
    store.close()
    found = by_part(run(path, probe))["Initiative"]
    assert found.text.startswith("on (/initiative on): check-ins, look back, improvised note · quiet hours")
    path = write_config(home, MINIMAL + "[initiative]\nenabled = true\n")
    store = Store.open(database)
    store.set_state("initiative:enabled", "off")
    store.close()
    found = by_part(run(path, probe))["Initiative"]
    assert found == Finding("ok", "Initiative", prompts.DOCTOR_INITIATIVE_OFF_BY_USER)


def test_in_her_own_user_the_speech_detectors_fix_names_her_voice_log_command(home: Path) -> None:
    """Her voice service's output is in /var/log/estia, readable only through her wrapper: never a path in a home."""
    probe = FakeProbe(home)
    probe.own = True
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # socket paths are at most 104 bytes
    path = write_config(home, MINIMAL + VOICE + f'[calls]\nsocket_dir = "{short}"\n')
    for vad in ("loading", "unavailable"):
        probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": vad}
        found = by_part(run(path, probe))["Speech detector"]
        assert "see /usr/bin/sudo /usr/local/estia/bin/estia logs voice" in found.fix, found.fix
        assert "Library/Logs" not in found.fix and "~" not in found.fix


def test_with_speech_stopping_her_doctor_shows_the_speech_detector(home: Path) -> None:
    probe = FakeProbe(home)
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # socket paths are at most 104 bytes
    calls = f'[calls]\nsocket_dir = "{short}"\n'
    # what keeps a turn open by quiet (pause = "quiet"): this line is barge-in's alone (the pause: below)
    calls += 'pause = "quiet"\n'
    sound = write_config(home, MINIMAL + VOICE + calls + 'interrupt = "any sound"\n')
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "ready"}
    assert "Speech detector" not in by_part(run(sound, probe))  # any sound stops her: nothing to show
    path = write_config(home, MINIMAL + VOICE + calls)  # only speech, the default
    assert by_part(run(path, probe))["Speech detector"] == Finding("ok", "Speech detector", "ready")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "loading"}
    waiting = by_part(run(path, probe))["Speech detector"]
    log = "~/Library/Logs/local-assistant/voice.log"
    falls = "any sound stops her on calls"
    assert waiting == Finding(
        "warn",
        "Speech detector",
        prompts.DOCTOR_DETECTOR_LOADING.format(falls=falls),
        prompts.DOCTOR_DETECTOR_LOADING_FIX.format(log=log),
    )
    assert waiting.text == "loading: any sound stops her on calls until it is ready"
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "unavailable"}
    broken = by_part(run(path, probe))["Speech detector"]
    assert broken.status == "warn" and broken.text == prompts.DOCTOR_DETECTOR_UNAVAILABLE.format(falls=falls)
    assert "any sound stops her on calls until it is fixed" in broken.text
    assert broken.fix == (  # review I4: the log first; a failed download is tried again at the next start
        f"see {log} for why; a failed download is tried again when the voice service starts again:"
        " scripts/install-voice-daemon.sh (asks for your Mac password: run it yourself); files that are not the pinned"
        " ones: delete ~/Library/Application Support/local-assistant/voice-models/silero-vad first"
    )
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready"}
    old = by_part(run(path, probe))["Speech detector"]
    assert old == Finding(
        "warn", "Speech detector", prompts.DOCTOR_DETECTOR_OLD.format(falls=falls), prompts.DOCTOR_VOICE_REINSTALL
    )
    assert old.text == "old voice service: any sound stops her on calls until it is updated"
    del probe.answers[HEALTH]
    silent = by_part(run(path, probe))["Speech detector"]
    assert silent.status == "warn" and silent.text.startswith("not ready")  # the Voice line fails already
    for finding in (waiting, broken, old, silent):
        assert "loudness" not in finding.text + finding.fix  # the old rule's name stays in the logs


@pytest.mark.parametrize(
    ("chosen", "falls"),
    [
        ("", "any sound stops her and keeps a turn open on calls"),  # the defaults: only speech, no speech
        ('interrupt = "any sound"\n', "any sound keeps a turn open on calls"),
        ('pause = "quiet"\n', "any sound stops her on calls"),
        ('interrupt = "any sound"\npause = "quiet"\n', ""),  # nothing waits for the detector: no line
    ],
)
def test_speech_detector_line_names_the_pause(home: Path, chosen: str, falls: str) -> None:
    """The speech detector also holds the pause (pause = "no speech", the default): its line shows for either, and
    names what any sound does on calls until it is ready."""
    probe = FakeProbe(home)
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # socket paths are at most 104 bytes
    path = write_config(home, MINIMAL + VOICE + f'[calls]\nsocket_dir = "{short}"\n' + chosen)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "ready", "turn": "ready"}
    found = by_part(run(path, probe))
    if not falls:
        assert "Speech detector" not in found
        return
    assert found["Speech detector"] == Finding("ok", "Speech detector", "ready")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "loading", "turn": "ready"}
    assert by_part(run(path, probe))["Speech detector"].text == f"loading: {falls} until it is ready"
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "turn": "ready"}
    assert by_part(run(path, probe))["Speech detector"].text == f"old voice service: {falls} until it is updated"


def test_turn_detector_doctor_lines(home: Path) -> None:
    """With early_end on (the default): whether Smart Turn is ready, else turns end after the full pause; its own fix
    when its model is unavailable (the folder downloads again at the next start). No line with early_end = false."""
    probe = FakeProbe(home)
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # socket paths are at most 104 bytes
    calls = f'[calls]\nsocket_dir = "{short}"\n'
    off = write_config(home, MINIMAL + VOICE + calls + "early_end = false\n")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "ready", "turn": "ready"}
    assert "Turn detector" not in by_part(run(off, probe))
    path = write_config(home, MINIMAL + VOICE + calls)
    assert by_part(run(path, probe))["Turn detector"] == Finding("ok", "Turn detector", "ready")
    log = "~/Library/Logs/local-assistant/voice.log"
    expected = {
        "loading": (
            "loading: turns end after the full pause until it is ready",
            "wait a minute and run doctor again (the model, about 17 MB, downloads at the first start); if it stays,"
            f" see {log}",
        ),
        "unavailable": (  # review I4: the log first; a failed download is tried again at the next start
            "unavailable (its model did not download or failed its check): turns end after the full pause until it is"
            " fixed",
            f"see {log} for why; a failed download is tried again when the voice service starts again:"
            " scripts/install-voice-daemon.sh (asks for your Mac password: run it yourself); files that could not be"
            " set aside: delete ~/Library/Application Support/local-assistant/voice-models/models/aufklarer first",
        ),
        None: (
            "old voice service: turns end after the full pause until it is updated",
            prompts.DOCTOR_VOICE_REINSTALL,
        ),
        "something new": ("not ready: turns end after the full pause until it is", prompts.DOCTOR_VOICE_REINSTALL),
    }
    for state, (text, fix) in expected.items():
        health: dict[str, str] = {"stt": "ready", "tts": "ready", "vad": "ready"}
        probe.answers[HEALTH] = health | ({"turn": state} if state is not None else {})
        assert by_part(run(path, probe))["Turn detector"] == Finding("warn", "Turn detector", text, fix), state
    del probe.answers[HEALTH]
    silent = by_part(run(path, probe))["Turn detector"]
    assert silent.status == "warn" and silent.text == "not ready: turns end after the full pause until it is"
    probe.own = True  # her own user: her voice log through her wrapper, never a path in a home
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "ready", "turn": "loading"}
    assert by_part(run(path, probe))["Turn detector"].fix.endswith(
        "see /usr/bin/sudo /usr/local/estia/bin/estia logs voice"
    )
    # unavailable in her own user: her wrapper starts the voice service again, and her models are in her home
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "unavailable", "turn": "unavailable"}
    estia = "/usr/bin/sudo /usr/local/estia/bin/estia"
    models = "/var/estia/Library/Application Support/local-assistant/voice-models"
    found = by_part(run(path, probe))
    assert found["Turn detector"].fix == (
        f"see {estia} logs voice for why; a failed download is tried again when the voice service starts again:"
        f" {estia} stop voice, then {estia} start voice; files that could not be set aside: delete"
        f" {models}/models/aufklarer first (with admin rights)"
    )
    assert found["Speech detector"].fix == (
        f"see {estia} logs voice for why; a failed download is tried again when the voice service starts again:"
        f" {estia} stop voice, then {estia} start voice; files that are not the pinned ones: delete"
        f" {models}/silero-vad first (with admin rights)"
    )
    assert "sudo" not in found["Turn detector"].fix.replace("/usr/bin/sudo", "")  # the word: admin rights


@pytest.mark.parametrize(
    ("line", "new"),
    [
        ('barge = "speech"', 'interrupt = "only speech"'),
        ('barge = "loudness"', 'interrupt = "any sound"'),
        ('interrupt = "sound"', 'interrupt = "any sound"'),
    ],
)
def test_doctor_suggests_the_new_wording_for_what_stops_her(home: Path, line: str, new: str) -> None:
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "ready"}
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")
    path = write_config(home, MINIMAL + VOICE + f'[calls]\nsocket_dir = "{short}"\n{line}\n')
    found = by_part(run(path, probe))["Calls setting"]
    assert found == Finding("warn", "Calls setting", f"{line} still works", f"write {new} in [calls] instead")
    current = write_config(home, MINIMAL + VOICE + f'[calls]\nsocket_dir = "{short}"\n{new}\n')
    assert "Calls setting" not in by_part(run(current, probe))


def test_doctor_says_an_old_barge_beside_interrupt_is_not_read(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "ready"}
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")
    both = f'[calls]\nsocket_dir = "{short}"\ninterrupt = "any sound"\nbarge = "speech"\n'
    found = by_part(run(write_config(home, MINIMAL + VOICE + both), probe))["Calls setting"]
    assert found == Finding(
        "warn",
        "Calls setting",
        'barge = "speech" is not read: interrupt = "any sound" wins',
        "remove barge from [calls]",
    )


# hardening: her Signal account's devices, the safety number, the model's pin, retention, the Mac and her own user

DIGEST = "ab" * 32


def test_her_signal_accounts_devices_come_from_status_json(home: Path) -> None:
    path = write_config(home, MINIMAL)
    assert by_part(run(path, FakeProbe(home)))["Her Signal account"].status == "untested"  # not counted yet
    status = recorded(path)
    status.linked_devices(1)
    one = by_part(run(path, FakeProbe(home)))["Her Signal account"]
    assert (one.status, one.text) == ("ok", "1 device: this Mac (checked 11:35)")
    status.linked_devices(2)
    findings = run(path, FakeProbe(home))
    two = by_part(findings)["Her Signal account"]
    assert two.status == "fail" and two.text.startswith("2 devices") and "removeDevice" in two.fix
    assert exit_code(findings) == 1


def test_a_changed_safety_number_is_red_with_the_compare_then_trust_steps(home: Path) -> None:
    path = write_config(home, MINIMAL)
    fine = by_part(run(path, FakeProbe(home)))["Safety number"]
    assert fine.status == "ok"
    recorded(path).identity_changed()
    findings = run(path, FakeProbe(home))
    changed = by_part(findings)["Safety number"]
    assert changed.status == "fail" and changed.text.startswith("changed 11:35")
    assert changed.fix == prompts.SAFETY_NUMBER_FIX and ACI not in render(findings)


def test_signal_cli_not_connected_for_minutes_is_red_with_what_it_said_and_the_steps(home: Path) -> None:
    """Logged out by Signal (another device took her account over, or her signal-cli is too old), the bridge restarts
    signal-cli quietly and /status cannot be reached (review S2): doctor reads status.json and says so, with the log
    to read and the steps; a restart still in progress (under 3 minutes) is no line."""
    path = write_config(home, MINIMAL)
    assert "Signal connection" not in by_part(run(path, FakeProbe(home)))
    recorded(path, at=NOW - 7200).signal_ready()
    recorded(path, at=NOW - 60).signal_failed("exited")
    assert "Signal connection" not in by_part(run(path, FakeProbe(home)))  # a minute: a restart, no line
    recorded(path, at=NOW).signal_failed("not registered")
    later = FakeProbe(home)
    later.clock = NOW + 600
    findings = run(path, later)
    down = by_part(findings)["Signal connection"]
    assert down.status == "fail" and exit_code(findings) == 1
    assert down.text == prompts.DOCTOR_SIGNAL_DOWN.format(
        minutes=11, restarts=2, failure=prompts.SIGNAL_FAILURE_WORDS["not registered"]
    )
    assert "Signal logged her out" in down.fix and "bridge.log" in down.fix
    recorded(path, at=NOW + 600).signal_failed("client too old")
    old = by_part(run(path, later))["Signal connection"]
    assert "Upgrade signal-cli" in old.fix
    recorded(path, at=NOW + 700).signal_ready()
    assert "Signal connection" not in by_part(run(path, later))


def test_signals_wait_is_red_until_it_is_over_with_the_steps(home: Path) -> None:
    """A captcha asked for or a long rate limit: nothing is sent until Signal's time (review S4). Never "her messages
    wait": a reply is tried again only within 10 minutes of being written, so one from early in a long wait is lost."""
    path = write_config(home, MINIMAL)
    assert "Signal limit" not in by_part(run(path, FakeProbe(home)))
    recorded(path).signal_limited(NOW + 3600, captcha=True)
    findings = run(path, FakeProbe(home))
    limit = by_part(findings)["Signal limit"]
    assert limit.status == "fail" and limit.text == prompts.DOCTOR_SIGNAL_LIMITED_CAPTCHA.format(when="12:40")
    assert "Signal limits her messages" in limit.fix
    assert "nothing is sent until then" in limit.text and "messages wait" not in limit.text
    recorded(path).signal_limited(NOW + 600, captcha=False)
    assert by_part(run(path, FakeProbe(home)))["Signal limit"].text == prompts.DOCTOR_SIGNAL_LIMITED.format(
        when="11:50"
    )
    later = FakeProbe(home)
    later.clock = NOW + 601
    assert "Signal limit" not in by_part(run(path, later))  # over


def test_without_a_pin_the_model_line_says_how_to_pin_it(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "local-worker:latest", "digest": DIGEST}]}
    pin = by_part(run(write_config(home, MINIMAL), probe))["Model pin"]
    assert pin.status == "warn" and f'model_digest = "sha256:{DIGEST}"' in pin.fix


def test_a_pinned_model_is_green_when_it_matches_and_red_when_not(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "local-worker:latest", "digest": DIGEST}]}
    path = write_config(home, MINIMAL + f'model_digest = "sha256:{DIGEST}"\n')
    assert by_part(run(path, probe))["Model pin"].status == "ok"
    other = "cd" * 32
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "local-worker:latest", "digest": other}]}
    findings = run(path, probe)
    pin = by_part(findings)["Model pin"]
    assert pin.status == "fail" and "answers are paused" in pin.text and other in pin.fix
    assert exit_code(findings) == 1


def test_history_retention_is_shown(home: Path) -> None:
    kept = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["History"]
    assert (kept.status, kept.text) == ("ok", "kept until you delete it ([privacy] keep_days is not set)")
    path = write_config(home, MINIMAL + "[privacy]\nkeep_days = 90\n")
    cfg = load_config(path)
    store = Store.open(cfg.db_path)
    try:
        assert by_part(run(path, FakeProbe(home)))["History"].status == "untested"  # not pruned yet
        store.set_state("privacy:pruned_at", f"{NOW - 3600:.0f}")
        recent = by_part(run(path, FakeProbe(home)))["History"]
        assert recent.status == "ok" and recent.text.startswith("older than 90 days deleted each night; last 10:40")
        store.set_state("privacy:pruned_at", f"{NOW - 4 * 86_400:.0f}")
        late = by_part(run(path, FakeProbe(home)))["History"]
        assert late.status == "warn" and "4 Oct" in late.text
    finally:
        store.close()


def test_the_macs_and_her_own_users_checks_follow_the_daemon_line_one_to_one(home: Path) -> None:
    probe = FakeProbe(home)
    probe.security = [
        Check("firewall", "warn", "Firewall", "off", "scripts/harden.sh", (("/usr/bin/true",),)),
        Check("registration_lock", "ok", "Registration Lock", "set"),
    ]
    probe.install = [
        Check("own_user_code", "fail", "Her code", "writable by you", "/usr/bin/sudo /usr/local/estia/bin/estia-deploy")
    ]
    findings = run(write_config(home, MINIMAL), probe)
    parts = [finding.part for finding in findings]
    daemon = parts.index("Always on")
    assert parts[daemon + 1 : daemon + 4] == ["Firewall", "Registration Lock", "Her code"]
    assert findings[daemon + 1] == Finding("warn", "Firewall", "off", "scripts/harden.sh")
    assert findings[daemon + 3] == Finding(
        "fail", "Her code", "writable by you", "/usr/bin/sudo /usr/local/estia/bin/estia-deploy"
    )
    assert probe.homes == [home] and probe.numbers == [NUMBER] and exit_code(findings) == 1
    assert NUMBER not in render(findings)


def test_a_mac_check_that_cannot_run_is_a_warning(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: Probe, home: Path, bot_number: str, name: str = "Estia") -> list[Check]:
        raise OSError("no such tool")

    monkeypatch.setattr(FakeProbe, "security_checks", broken)
    found = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Mac security"]
    assert found.status == "warn"


def test_with_her_own_user_installed_a_missing_config_points_to_her_doctor(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = FakeProbe(home)
    probe.own = True
    config = by_part(run(home / ".config/local-assistant/config.toml", probe))["Config"]
    assert (
        config.status == "fail"
        and config.fix == "she runs as her own macOS account: /usr/bin/sudo /usr/local/estia/bin/estia doctor"
    )
    probe.own = False  # rolled back, or never moved: your own config is missing
    config = by_part(run(home / ".config/local-assistant/config.toml", probe))["Config"]
    assert "cp config.example.toml" in config.fix


# her own user: the fixes say how, as her


def test_in_her_own_user_the_pin_says_how_to_edit_her_config_and_restart_her(home: Path) -> None:
    probe = FakeProbe(home)
    probe.own = True
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "local-worker:latest", "digest": DIGEST}]}
    path = write_config(home, MINIMAL)
    pin = by_part(run(path, probe))["Model pin"]
    assert (
        "/usr/bin/sudo /usr/local/estia/bin/estia config," in pin.fix
        and "/usr/bin/sudo /usr/local/estia/bin/estia restart" in pin.fix
    )
    assert "sudo -u _estia -e" not in pin.fix  # nano as her: no copy of her config in your account (review 15 Oct, 2)
    assert f'model_digest = "sha256:{DIGEST}"' in pin.fix


def test_in_her_own_user_a_soul_others_can_read_is_fixed_as_her(home: Path) -> None:
    probe = FakeProbe(home)
    probe.own = True
    path = write_config(home, MINIMAL)
    soul = path.parent / "soul.md"
    soul.write_text("Warm.\n")
    soul.chmod(0o644)
    found = by_part(run(path, probe))["Personality"]
    assert found.status == "warn" and found.fix.startswith(f"/usr/bin/sudo -u _estia /bin/chmod 600 {soul}")


def test_her_programs_follow_her_own_users_checks(home: Path) -> None:
    probe = FakeProbe(home)
    probe.own = True
    probe.install = [Check("plists", "ok", "Services", "bridge runs as _estia")]
    probe.programs = [
        Check("programs", "fail", "Her programs", "signal-cli (x)", "/usr/bin/sudo /usr/local/estia/bin/estia-deploy")
    ]
    findings = run(write_config(home, MINIMAL), probe)
    parts = [finding.part for finding in findings]
    assert parts.index("Her programs") == parts.index("Services") + 1 and exit_code(findings) == 1


def test_run_as_her_the_tools_line_leaves_your_tools_out(home: Path) -> None:
    """Homebrew's ollama and uv are yours and not on her PATH: as her, doctor checks what she runs instead."""
    probe = FakeProbe(home)
    probe.her = True
    probe.tools = set()
    tools = by_part(run(write_config(home, MINIMAL), probe))["Tools"]
    assert tools.status == "ok" and "Her programs" in tools.text


def test_the_safety_number_and_linked_device_fixes_name_her_signal_command() -> None:
    assert "/usr/bin/sudo /usr/local/estia/bin/estia signal listIdentities" in prompts.SAFETY_NUMBER_FIX
    assert "/usr/bin/sudo /usr/local/estia/bin/estia signal removeDevice" in prompts.LINKED_FIX


# her way through your home (finding 32): with Projects or Documents on, her account passes through your home by name,
# and opens what in it lets every account in


@pytest.mark.parametrize(
    ("section", "shared"),
    [
        ('[documents]\nfolder = "/Users/alex/Estia-KB"\n', {Path("/Users/alex/Estia-KB")}),
        ('[projects]\nroot = "/Users/alex/dev"\n', {Path("/Users/alex/dev")}),
    ],
)
def test_doctor_names_what_every_account_may_open_in_your_home(home: Path, section: str, shared: set[Path]) -> None:
    probe = FakeProbe(home)
    probe.own = True
    probe.home_open = ([Path("/Users/alex/.zshrc"), Path("/Users/alex/.config")], False)
    found = by_part(run(write_config(home, MINIMAL + section), probe))["Your home"]
    assert found.status == "warn"
    assert found.text.startswith(
        "every account may open .zshrc, .config (the macOS default for a new file or folder), and her account passes "
        "through your home"
    )
    assert "(the usual names only: her account cannot list your home)" in found.text
    assert found.fix == (
        "Close what should stay yours (no admin rights needed): chmod -h o-rwx /Users/alex/.zshrc /Users/alex/.config"
    )
    assert probe.homes_opened == [(Path("/Users/alex"), shared)]


def test_doctor_has_no_home_line_without_her_way_through_it(home: Path) -> None:
    """Before her own user she runs as you; without a share she never passes through your home; nothing open: no
    line either way."""
    documents = '[documents]\nfolder = "/Users/alex/Estia-KB"\n'
    for own, section, opened in (
        (False, documents, ([Path("/Users/alex/.zshrc")], True)),
        (True, "", ([Path("/Users/alex/.zshrc")], True)),
        (True, '[documents]\nfolder = "/var/estia/Estia-KB"\n', ([Path("/Users/alex/.zshrc")], True)),
        (True, documents, ([], False)),
    ):
        probe = FakeProbe(home)
        probe.own = own
        probe.home_open = opened
        assert "Your home" not in by_part(run(write_config(home, MINIMAL + section), probe)), (own, section)


# her projects folder: read as whoever runs doctor (as her: /usr/bin/sudo /usr/local/estia/bin/estia doctor)


def projects_config(home: Path) -> Path:
    return write_config(home, MINIMAL + f'[projects]\nroot = "{home / "dev"}"\n')


def a_repository(home: Path) -> Path:
    import subprocess

    repo = home / "dev" / "alpha"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# alpha\n")
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    for argv in (["init", "-q", "-b", "main"], ["add", "README.md"]):
        subprocess.run(["/usr/bin/git", "-C", str(repo), *argv], check=True, capture_output=True, env=env)
    return repo


def test_projects_off_have_no_line(home: Path) -> None:
    """Projects are an extra: without [projects] nothing is read and doctor names no projects folder."""
    a_repository(home)
    probe = FakeProbe(home)
    probe.own = True
    probe.projects = lambda cfg: pytest.fail("the projects folder is read while projects are off")  # type: ignore[method-assign]
    assert "Projects" not in by_part(run(write_config(home, MINIMAL), probe))


def test_a_readable_projects_folder_is_ok(home: Path) -> None:
    a_repository(home)
    found = by_part(run(projects_config(home), FakeProbe(home)))["Projects"]
    assert found.status == "ok" and str(home / "dev") in found.text and "README" in found.text


def test_a_projects_folder_her_user_cannot_read_names_your_share_command(home: Path) -> None:
    a_repository(home)
    probe = FakeProbe(home)
    probe.own = True
    (home / "dev").chmod(0)
    try:
        findings = run(projects_config(home), probe)
    finally:
        (home / "dev").chmod(0o755)
    found = by_part(findings)["Projects"]
    assert found.status == "fail" and exit_code(findings) == 1
    assert found.fix.endswith(f"scripts/share-projects.sh {home / 'dev'}") and "no sudo" in found.fix


@pytest.mark.parametrize(
    ("access", "own", "status", "word"),
    [
        (("missing", 0, None, 0), True, "untested", "no folder"),
        (("open", 2, None, 0), True, "untested", "nothing to check"),
        (("open", 2, None, 2), True, "fail", "cannot be read"),
        (("open", 3, "alpha", 1), True, "warn", "cannot be read"),
        (("open", 3, "alpha", 0), True, "ok", "3 project(s)"),
        (("closed", 0, None, 0), False, "warn", "cannot be read"),
    ],
)
def test_the_projects_line(
    home: Path, access: tuple[str, int, str | None, int], own: bool, status: str, word: str
) -> None:
    from local_assistant.projects import Access

    probe = FakeProbe(home)
    probe.own = own
    probe.projects = lambda cfg: Access(*access)  # type: ignore[method-assign]
    found = by_part(run(projects_config(home), probe))["Projects"]
    assert found.status == status and word in found.text, found
    if status in ("fail", "warn") and own:
        assert "scripts/share-projects.sh" in found.fix
    if not own:
        assert "share-projects" not in found.fix  # she runs as you: nothing to share


def test_the_always_on_line_reads_the_service_file_under_the_root_it_checks(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never the live /Library/LaunchDaemons from a test: the service file is looked for under ESTIA_ROOT."""
    root = tmp_path / "root"
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    monkeypatch.setenv("ESTIA_ROOT", str(root))
    always_on = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Always on"]
    assert always_on.status == "warn"
    (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").write_bytes(b"x")
    always_on = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Always on"]
    assert (always_on.status, always_on.text) == ("untested", "installed; whether it runs could not be read")


# "Always on" means alive, not just installed: launchd's state and last exit code, and the bridge's last sign of life
# (last_alive, written every minute); read-only, fixed argv, no fix applied

RUNNING = "system/com.local-assistant.bridge = {\n\tstate = running\n\tpid = 4242\n\truns = 3\n}\n"
RESTARTING = "system/com.local-assistant.bridge = {\n\tstate = spawn scheduled\n\truns = 40\n\tlast exit code = 1\n}\n"
STOPPED = "system/com.local-assistant.bridge = {\n\tstate = not running\n\truns = 1\n\tlast exit code = 0\n}\n"
NOT_LOADED = 'Bad request.\nCould not find service "com.local-assistant.bridge" in domain for system\n'


def always_on(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launchd: str | None,
    alive: float | None,
    *,
    own: bool = False,
) -> Finding:
    root = tmp_path / "root"
    (root / "Library/LaunchDaemons").mkdir(parents=True, exist_ok=True)
    (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").write_bytes(b"x")
    monkeypatch.setenv("ESTIA_ROOT", str(root))
    config = write_config(home, MINIMAL)
    if alive is not None:
        store = Store.open(load_config(config).db_path)
        store.set_state("last_alive", f"{alive:.0f}")
        store.close()
    probe = FakeProbe(home)
    probe.launchd, probe.own = launchd, own
    return by_part(run(config, probe))["Always on"]


def test_always_on_is_running_with_a_fresh_sign_of_life(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = always_on(home, tmp_path, monkeypatch, RUNNING, NOW - 50)
    assert (found.status, found.text) == ("ok", "running, last sign of life 11:39")


def test_always_on_says_how_long_there_was_no_sign_of_life(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = always_on(home, tmp_path, monkeypatch, RUNNING, NOW - 12 * 60, own=True)
    assert (found.status, found.text) == ("fail", "installed, no sign of life for 12 min")
    assert found.fix == "/usr/bin/sudo /usr/local/estia/bin/estia logs bridge"
    found = always_on(home, tmp_path, monkeypatch, RUNNING, NOW - 2 * 60)  # within about 3 minutes: alive
    assert found.status == "ok"
    found = always_on(home, tmp_path, monkeypatch, RUNNING, NOW - 3 * 86_400)
    assert found.text == "installed, no sign of life since 5 Oct 11:40"


def test_always_on_tells_restarting_from_stopped(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    found = always_on(home, tmp_path, monkeypatch, RESTARTING, NOW - 12 * 60, own=True)
    assert (found.status, found.text) == ("fail", "restarting, last exit code 1")
    assert found.fix == "/usr/bin/sudo /usr/local/estia/bin/estia logs launchd"
    found = always_on(home, tmp_path, monkeypatch, STOPPED, NOW - 12 * 60, own=True)
    assert (found.status, found.text) == ("fail", "installed, stopped (last exit code 0)")
    assert found.fix == "/usr/bin/sudo /usr/local/estia/bin/estia restart"
    found = always_on(home, tmp_path, monkeypatch, NOT_LOADED, None, own=True)
    assert (found.status, found.text) == ("fail", "installed, not loaded: launchd does not run it")
    assert found.fix == (
        "/usr/bin/sudo /bin/launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.bridge.plist"
    )


def test_always_on_before_the_move_points_to_the_owners_files(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = always_on(home, tmp_path, monkeypatch, RESTARTING, None)
    assert found.text == "restarting, last exit code 1"
    assert (
        found.fix == f"tail -n 50 {load_config(home / '.config/local-assistant/config.toml').log_dir / 'launchd.log'}"
    )
    found = always_on(home, tmp_path, monkeypatch, NOT_LOADED, None)
    assert found.fix == (
        "/usr/bin/sudo /bin/launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.bridge.plist"
    )
    found = always_on(home, tmp_path, monkeypatch, STOPPED, None)
    assert found.fix == "/usr/bin/sudo /bin/launchctl kickstart -k system/com.local-assistant.bridge"


def test_always_on_reads_an_unreadable_launchd_as_unknown(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only "Could not find service" means not loaded; any other answer without a state is launchd not read."""
    found = always_on(home, tmp_path, monkeypatch, "Operation not permitted\n", NOW - 30)
    assert (found.status, found.text) == ("ok", "installed, last sign of life 11:39")


def test_always_on_running_without_a_sign_of_life_on_record(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = always_on(home, tmp_path, monkeypatch, RUNNING, None)
    assert (found.status, found.text) == ("warn", "running, no sign of life on record yet")


def test_always_on_without_launchd_reads_the_sign_of_life_alone(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = always_on(home, tmp_path, monkeypatch, None, None)
    # review of 14 Oct: installed is not alive; nothing more can be known, so no ✓ (○, with the log to read)
    assert (found.status, found.text) == ("untested", "installed; whether it runs could not be read")
    assert found.fix.endswith("bridge.log")
    found = always_on(home, tmp_path, monkeypatch, None, NOW - 12 * 60)
    assert (found.status, found.text) == ("fail", "installed, no sign of life for 12 min")
    found = always_on(home, tmp_path, monkeypatch, None, NOW - 30)
    assert (found.status, found.text) == ("ok", "installed, last sign of life 11:39")


def test_the_real_probe_never_reads_the_live_launchd_under_a_test_root(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Probe().bridge_launchd() is None  # conftest gives every test its own ESTIA_ROOT


# YouTube: always on with read_link; doctor checks the helper is in place and names its version (from its files: no
# network, the helper is not run), then what its last real read did


def youtube_at(monkeypatch: pytest.MonkeyPatch, venv: Path) -> None:
    def load(path: Path) -> Config:
        return replace(load_config(path), youtube_helper=(str(venv / "bin" / "python"), "-I", "helper.py"))

    monkeypatch.setattr(doctor, "load_config", load)


def built_youtube(home: Path, version: str = "1.2.4") -> Path:
    venv = home / "tools/youtube/.venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    (venv / f"lib/python3.13/site-packages/youtube_transcript_api-{version}.dist-info").mkdir(parents=True)
    return venv


def test_the_youtube_helper_is_checked_with_its_version(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    youtube_at(monkeypatch, built_youtube(home))
    path = write_config(home, MINIMAL)
    findings = run(path, FakeProbe(home))
    youtube = by_part(findings)["YouTube"]
    assert youtube.status == "untested" and youtube.text == "set up, not tested yet (youtube-transcript-api 1.2.4)"
    recorded(path).ok("youtube")
    assert by_part(run(path, FakeProbe(home)))["YouTube"].status == "ok"
    assert exit_code(findings) == 0


def test_a_missing_youtube_helper_is_a_warning_with_its_fix(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    youtube_at(monkeypatch, home / "no-such-venv")
    findings = run(write_config(home, MINIMAL), FakeProbe(home))
    youtube = by_part(findings)["YouTube"]
    assert (youtube.status, youtube.text) == ("warn", "the YouTube helper is not set up: video links cannot be read")
    assert youtube.fix == "cd tools/youtube && uv sync --frozen" and exit_code(findings) == 0


def test_in_her_own_user_a_missing_youtube_helper_names_the_deploy(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # there the release's helper is built by the deploy: the uv line would fail in a root-owned release
    youtube_at(monkeypatch, home / "no-such-venv")
    probe = FakeProbe(home)
    probe.own = True
    youtube = by_part(run(write_config(home, MINIMAL), probe))["YouTube"]
    assert youtube.status == "warn" and "cd tools/youtube" not in youtube.fix
    assert youtube.fix.startswith("/usr/bin/sudo /usr/local/estia/bin/estia-deploy") and "--update-tools" in youtube.fix


def test_a_failing_youtube_read_names_the_next_step(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    youtube_at(monkeypatch, built_youtube(home))
    path = write_config(home, MINIMAL)
    recorded(path).failed("youtube", "bad_answer")
    youtube = by_part(run(path, FakeProbe(home)))["YouTube"]
    assert youtube.status == "fail" and "tools/youtube" in youtube.fix


def test_always_on_reads_the_bridges_own_alive_mark() -> None:
    from local_assistant import app

    assert doctor.ALIVE_KEY == app.ALIVE_KEY and doctor.ALIVE_SECONDS >= 3 * app.ALIVE_SECONDS


# Documents ([documents]): the folder shared and readable (she lists it and reads the share's token file), the helper
# in place, then what her last look did


def documents_at(monkeypatch: pytest.MonkeyPatch, helper: Path, owner_home: Path | None = None) -> None:
    """The documents helper at `helper`; with `owner_home`, the test's home stands in for a home on a Mac
    (/Users/<name>), so its Estia-KB is the owner's ~/Estia-KB."""

    def load(path: Path) -> Config:
        return replace(load_config(path), documents_helper=(str(helper),))

    monkeypatch.setattr(doctor, "load_config", load)
    if owner_home is not None:
        monkeypatch.setattr(config_module, "OWNER_HOME", re.compile(re.escape(str(owner_home))))


def documents_config(home: Path, folder: Path) -> Path:
    return write_config(home, MINIMAL + f'[documents]\nfolder = "{folder}"\n')


def shared_folder(home: Path) -> Path:
    folder = home / "Estia-KB"
    folder.mkdir(parents=True)
    (folder / ".estia-share").write_text("0123456789abcdef0123456789abcdef\n")
    return folder


def built_helper(home: Path) -> Path:
    helper = home / "tools/documents/.build/release/documents-helper"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    return helper


def test_documents_off_have_no_line(home: Path) -> None:
    assert "Documents" not in by_part(run(write_config(home, MINIMAL), FakeProbe(home)))


def test_a_shared_folder_and_the_helper_in_place_then_her_last_look(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents_at(monkeypatch, built_helper(home), home)
    folder = shared_folder(home)
    path = documents_config(home, folder)
    probe = FakeProbe(home)
    probe.own = True
    found = by_part(run(path, probe))["Documents"]
    assert (found.status, found.text) == ("untested", f"set up, not tested yet ({folder}: shared and readable)")
    recorded(path).ok("documents")
    assert by_part(run(path, probe))["Documents"].status == "ok"
    recorded(path).failed("documents", "not_shared")
    failing = by_part(run(path, probe))["Documents"]
    assert failing.status == "fail" and "scripts/share-documents.sh" in failing.fix


def test_a_failed_warm_up_is_a_warning_with_its_fix(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Text recognition failed at her start (live, 15 Oct: under her account): "!" with the fix, text pages still
    read, so no ✗ and doctor's exit code stays 0."""
    documents_at(monkeypatch, built_helper(home), home)
    path = documents_config(home, shared_folder(home))
    status = recorded(path)
    status.ok("documents")
    status.warn("documents", "ocr_unavailable")
    findings = run(path, FakeProbe(home))
    found = by_part(findings)["Documents"]
    assert found.status == "warn" and exit_code(findings) == 0
    assert found.text.startswith("working, last read 11:35; since 11:35: scanned pages not read")
    assert found.fix == prompts.PART_FIXES["documents/ocr_unavailable"]
    assert any(line.startswith("! Documents: working") for line in render(findings).splitlines())


def test_a_folder_she_cannot_read_names_the_share(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    documents_at(monkeypatch, built_helper(home), home)
    folder = shared_folder(home)
    probe = FakeProbe(home)
    probe.own = True
    folder.chmod(0)
    try:
        findings = run(documents_config(home, folder), probe)
    finally:
        folder.chmod(0o700)
    found = by_part(findings)["Documents"]
    assert found.status == "fail" and exit_code(findings) == 1
    assert found.text == f"{folder} cannot be read: not shared with her"
    assert found.fix == "as you, in your checkout (no admin rights): scripts/share-documents.sh"


def test_a_missing_folder_or_share_token_names_the_share(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    documents_at(monkeypatch, built_helper(home), home)
    folder = home / "Estia-KB"
    probe = FakeProbe(home)
    probe.own = True
    missing = by_part(run(documents_config(home, folder), probe))["Documents"]
    assert missing.status == "fail" and missing.text == f"no folder {folder}"
    assert "scripts/share-documents.sh" in missing.fix and "makes it" in missing.fix
    folder.mkdir(parents=True)
    unshared = by_part(run(documents_config(home, folder), probe))["Documents"]
    assert unshared.status == "fail" and unshared.text == f"{folder} is not shared with her yet (no share token)"
    probe.own = False  # she runs as you: your own folder needs no share
    assert by_part(run(documents_config(home, folder), probe))["Documents"].status == "untested"


def test_a_missing_documents_helper_is_a_failure_with_its_build(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    documents_at(monkeypatch, home / "no-helper", home)
    folder = shared_folder(home)
    found = by_part(run(documents_config(home, folder), FakeProbe(home)))["Documents"]
    assert (found.status, found.text) == ("fail", "the documents helper is not built: PDFs cannot be read")
    assert found.fix == "cd tools/documents && swift build -c release"


def test_a_documents_helper_built_after_its_failure_is_not_tested_yet(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = built_helper(home)
    documents_at(monkeypatch, helper, home)
    path = documents_config(home, shared_folder(home))
    recorded(path).failed("documents", "helper_missing")
    os.utime(helper, (OLD, OLD))
    assert by_part(run(path, FakeProbe(home)))["Documents"].status == "fail"  # the record stands
    os.utime(helper, (NOW - 60, NOW - 60))  # built after the failure (swift build, or a deploy)
    found = by_part(run(path, FakeProbe(home)))["Documents"]
    assert (found.status, found.text.split(" (", 1)[0]) == ("untested", "set up again, not tested yet")


def test_in_her_own_user_a_missing_documents_helper_names_the_deploy(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release holds the helper only when estia-deploy built it: a release from root-run tools older than that has
    none, so the fix is the deploy, with the tools' review first (her user builds nothing)."""
    documents_at(monkeypatch, home / "no-helper", home)
    folder = shared_folder(home)
    probe = FakeProbe(home)
    probe.own = True
    found = by_part(run(documents_config(home, folder), probe))["Documents"]
    assert found.status == "fail" and "swift" not in found.fix
    assert found.fix == prompts.DOCTOR_YOUTUBE_DEPLOY_FIX.format(deploy=ownuser.DEPLOY_CMD)
    assert f"{ownuser.DEPLOY_CMD} --update-tools" in found.fix


def test_doctor_reads_your_sudo_from_roots_listing_or_as_you(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """In her own user, estia doctor (root) lists your sudo rules and hands them to her doctor; before it, doctor
    reads them as you without a password. Never as her and never under a test's root."""
    listing = "User alex may run the following commands on mini:\n    (ALL) ALL\n    (ALL) NOPASSWD: ALL\n"
    from_root = doctor.Probe(tmp_path, owner_sudo=listing).sudo_check()
    assert from_root is not None and from_root.status == "warn" and "NOPASSWD" in from_root.text
    assert doctor.Probe(tmp_path).sudo_check() is None  # a test's root: sudo is never run
    seen: list[str | None] = []

    def run(path: Path, probe: doctor.Probe) -> list[doctor.Finding]:
        seen.append(probe.owner_sudo)
        return []

    monkeypatch.setattr(doctor, "run", run)
    monkeypatch.setattr("sys.stdin", io.StringIO(listing))
    assert doctor.main(["--config", str(tmp_path / "config.toml")]) == 0
    monkeypatch.setenv("ESTIA_OWNER_SUDO", "stdin")  # an environment value: a doctor from before ignores it
    assert doctor.main(["--config", str(tmp_path / "config.toml")]) == 0
    assert seen == [None, listing]


def test_a_release_built_without_the_documents_helper_says_so(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deploy whose documents build failed goes live without the helper (documents are an extra): doctor names that
    release and the Swift it needs, not a missing tools update."""
    documents_at(monkeypatch, home / "no-helper", home)
    release = home / "release"
    release.mkdir()
    (release / "release.json").write_text(json.dumps({"name": "r1", "documents": False}))
    monkeypatch.setattr(doctor, "CODE_ROOT", release)
    probe = FakeProbe(home)
    probe.own = True
    found = by_part(run(documents_config(home, shared_folder(home)), probe))["Documents"]
    assert (found.status, found.text) == ("fail", prompts.DOCTOR_DOCUMENTS_NOT_BUILT)
    assert "its build failed" in found.text and "PDFs cannot be read" in found.text
    assert found.fix.startswith(ownuser.DEPLOY_CMD) and "Swift 6.0 or later" in found.fix


def test_a_folder_macos_itself_refuses_is_named_apart_from_the_share(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EPERM is macOS's own refusal (its privacy protection of Documents, Desktop and Downloads; live, 14 Oct), not a
    missing share (EACCES): doctor says so, and names ~/Estia-KB, which macOS does not guard, instead of sharing the
    same folder again. Before her own user the config may name any folder."""
    documents_at(monkeypatch, built_helper(home), home)
    folder = home / "papers"
    folder.mkdir()

    def refused(path: object) -> object:
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "scandir", refused)
    found = by_part(run(documents_config(home, folder), FakeProbe(home)))["Documents"]
    assert found.status == "fail" and found.text == prompts.DOCTOR_DOCUMENTS_REFUSED.format(folder=folder)
    assert "operation not permitted" in found.text and "scripts/share-documents.sh" not in found.text
    assert "Documents, Desktop and Downloads" in found.fix and '"/Users/<you>/Estia-KB"' in found.fix


def test_estia_kb_itself_refused_by_macos_is_unexpected_never_a_move_there(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix "use your ~/Estia-KB" for a folder that already is ~/Estia-KB would go in a circle: macOS refusing it
    is unexpected, and her log says what she gets."""
    documents_at(monkeypatch, built_helper(home), home)
    folder = shared_folder(home)

    def refused(path: object) -> object:
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "scandir", refused)
    for own in (True, False):
        probe = FakeProbe(home)
        probe.own = own
        found = by_part(run(documents_config(home, folder), probe))["Documents"]
        assert found.status == "fail" and found.text == prompts.DOCTOR_DOCUMENTS_REFUSED.format(folder=folder)
        assert found.fix == prompts.DOCTOR_DOCUMENTS_REFUSED_KB_FIX
        assert found.fix.startswith("macOS refuses ~/Estia-KB itself (unexpected)") and "Use your" not in found.fix
        assert "bridge.log (EPERM)" in found.fix and "SECURITY.md, known gaps" in found.fix


def refused_at(monkeypatch: pytest.MonkeyPatch, folder: Path, code: int) -> None:
    """The folder's own details refused with `code` (EACCES: not shared with her; EPERM: macOS itself)."""
    real_lstat = os.lstat

    def lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if str(path) == str(folder):
            raise PermissionError(code, os.strerror(code))
        return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "lstat", lstat)


def test_in_her_own_user_a_folder_that_is_not_your_estia_kb_names_her_config(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The share opens only ~/Estia-KB to her: a config that still names another folder of yours (the first share's,
    in Documents) stays closed however often the share runs, so the fix is her config, never the share. A folder in
    Documents, Desktop or Downloads also gets why."""
    documents_at(monkeypatch, built_helper(home))  # the real homes: /Users/<name>
    probe = FakeProbe(home)
    probe.own = True
    guarded = Path("/Users/alex/Documents/papers")
    refused_at(monkeypatch, guarded, errno.EACCES)  # her account cannot pass through Documents
    found = by_part(run(documents_config(home, guarded), probe))["Documents"]
    assert found.status == "fail"
    assert found.text == f"{guarded} is not your ~/Estia-KB: the share opens only ~/Estia-KB to her"
    assert found.fix == f"{prompts.DOCTOR_DOCUMENTS_HERS_FIX}. {prompts.DOCTOR_DOCUMENTS_WHY_HOME}"
    assert "macOS guards Documents, Desktop and Downloads; a folder in your home needs no extra permission" in found.fix
    assert "share-documents.sh" not in found.fix
    for elsewhere in (Path("/Users/alex/papers"), Path("/Users/Shared/Estia-KB"), Path("/Users/alex/a/Estia-KB")):
        found = by_part(run(documents_config(home, elsewhere), probe))["Documents"]
        assert found.text == f"{elsewhere} is not your ~/Estia-KB: the share opens only ~/Estia-KB to her"
        assert found.fix == prompts.DOCTOR_DOCUMENTS_HERS_FIX  # no folder macOS guards: no why
    desktop = Path("/Users/alex/desktop/Estia-KB")  # a Mac's usual disk ignores case
    assert prompts.DOCTOR_DOCUMENTS_WHY_HOME in by_part(run(documents_config(home, desktop), probe))["Documents"].fix


def test_in_her_own_user_your_estia_kb_passes_the_name_check(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/Users/<you>/Estia-KB, as the share prints it, is your ~/Estia-KB: doctor goes on to read it."""
    documents_at(monkeypatch, built_helper(home))
    probe = FakeProbe(home)
    probe.own = True
    folder = Path("/Users/alex/Estia-KB")
    refused_at(monkeypatch, folder, errno.EPERM)
    found = by_part(run(documents_config(home, folder), probe))["Documents"]
    assert found.text == prompts.DOCTOR_DOCUMENTS_REFUSED.format(folder=folder)
    assert found.fix == prompts.DOCTOR_DOCUMENTS_REFUSED_KB_FIX


def test_in_her_own_user_a_folder_in_her_home_names_yours(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The folder is always the owner's ~/Estia-KB: in her own user ~ is her home (/var/estia), so the config names the
    owner's folder in full."""
    documents_at(monkeypatch, built_helper(home), home)
    probe = FakeProbe(home)
    probe.own = True
    found = by_part(run(documents_config(home, Path("/var/estia/Estia-KB")), probe))["Documents"]
    assert found.status == "fail" and found.text == prompts.DOCTOR_DOCUMENTS_HERS
    assert found.fix == prompts.DOCTOR_DOCUMENTS_HERS_FIX and '"/Users/<you>/Estia-KB"' in found.fix
    assert found.text.endswith("documents are always your ~/Estia-KB")


def test_a_folder_macos_refuses_at_its_first_step_is_named_too(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS may refuse even the folder's own details (EPERM from lstat): still its own refusal, never the share."""
    documents_at(monkeypatch, built_helper(home), home)
    folder = shared_folder(home)
    real_lstat = os.lstat

    def lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if str(path) == str(folder):
            raise PermissionError(errno.EPERM, "Operation not permitted")
        return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "lstat", lstat)
    probe = FakeProbe(home)
    probe.own = True
    found = by_part(run(documents_config(home, folder), probe))["Documents"]
    assert found.text == prompts.DOCTOR_DOCUMENTS_REFUSED.format(folder=folder)


def test_her_last_look_refused_by_macos_names_no_share(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Her own look met macOS's refusal: in another folder the fix is ~/Estia-KB (live, 14 Oct); in ~/Estia-KB itself
    that would go in a circle, so it is unexpected."""
    documents_at(monkeypatch, built_helper(home), home)
    folder = shared_folder(home)
    path = documents_config(home, folder)
    probe = FakeProbe(home)
    probe.own = True
    recorded(path).failed("documents", "os_refused")
    found = by_part(run(path, probe))["Documents"]
    assert found.status == "fail" and "macOS refuses it" in found.text
    assert found.fix == prompts.PART_FIXES["documents/os_refused/estia_kb"]
    assert found.fix.startswith("macOS refuses ~/Estia-KB itself (unexpected)") and "use your" not in found.fix
    assert "bridge.log" in found.fix and "SECURITY.md" in found.fix
    elsewhere = home / "papers"  # before her own user the config may name any folder
    elsewhere.mkdir()
    path = documents_config(home, elsewhere)
    recorded(path).failed("documents", "os_refused")
    found = by_part(run(path, FakeProbe(home)))["Documents"]
    assert "bridge.log" in found.fix and "SECURITY.md" in found.fix
    assert "Documents, Desktop and Downloads" in found.fix and "use your ~/Estia-KB" in found.fix


def test_before_her_own_user_a_missing_folder_is_made_by_you(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Before her own user the bridge runs as you: no share is needed (the script refuses without her user), only the
    folder."""
    documents_at(monkeypatch, built_helper(home), home)
    folder = home / "Estia-KB"
    found = by_part(run(documents_config(home, folder), FakeProbe(home)))["Documents"]
    assert found.text == f"no folder {folder}" and found.fix == f"as you: mkdir -p {folder}"
    assert "share-documents.sh" not in found.fix


ICLOUD_FAMILY = (
    '[calendar]\n[[calendar.sources]]\nname = "family"\ncaldav = "https://caldav.icloud.com"\n'
    'user = "alex@example.com"\ncalendar = "Family"\n'
)


def test_a_caldav_calendar_needs_its_password(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    path = write_config(home, MINIMAL + ICLOUD_FAMILY)
    save_addresses(home, f"CALENDAR_ICS_URL={GOOGLE}\n")  # another calendar's secret only
    family = by_part(run(path, FakeProbe(home)))["Calendar (family)"]
    assert (family.status, family.fix) == ("fail", "scripts/set-calendar-password.sh family")
    assert family.text == "no app-specific password saved in calendar.env (CALDAV_PASSWORD_FAMILY)"
    save_addresses(home, f"CALDAV_PASSWORD_FAMILY=x\nCALENDAR_SAVED_AT_FAMILY={OLD:.0f}\n")
    family = by_part(run(path, FakeProbe(home)))["Calendar (family)"]
    assert (family.status, family.text) == ("untested", "set up, not tested yet (iCloud)")
    recorded(path).ok("calendar:family")
    family = by_part(run(path, FakeProbe(home)))["Calendar (family)"]
    assert (family.status, family.text) == ("ok", "working, last read 11:35 (iCloud)")


@pytest.mark.parametrize(
    ("reason", "text", "fix"),
    [
        (
            "password_refused",
            "user name or password refused",
            "a new app-specific password, then scripts/set-calendar-password.sh family",
        ),
        ("no_calendar", "no calendar by that name", "check caldav and calendar, the server's address"),
        ("no_answer", "no answer", "check the service and the Mac's internet connection"),
    ],
)
def test_a_caldav_calendar_failing_says_why_with_the_fix(
    home: Path, monkeypatch: pytest.MonkeyPatch, reason: Reason, text: str, fix: str
) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    path = write_config(home, MINIMAL + ICLOUD_FAMILY)
    os.utime(path, (OLD, OLD))
    save_addresses(home, f"CALDAV_PASSWORD_FAMILY=x\nCALENDAR_SAVED_AT_FAMILY={OLD:.0f}\n")
    recorded(path).failed("calendar:family", reason)
    family = by_part(run(path, FakeProbe(home)))["Calendar (family)"]
    assert family.status == "fail" and family.text == f"failing since 11:35: {text} (iCloud)"
    assert family.fix.startswith(fix)


def test_a_caldav_fix_saved_after_the_failure_is_not_tested_yet(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    path = write_config(home, MINIMAL + ICLOUD_FAMILY)
    os.utime(path, (OLD, OLD))
    save_addresses(home, f"CALDAV_PASSWORD_FAMILY=x\nCALENDAR_SAVED_AT_FAMILY={OLD:.0f}\n")
    recorded(path).failed("calendar:family", "password_refused")
    save_addresses(home, f"CALDAV_PASSWORD_FAMILY=y\nCALENDAR_SAVED_AT_FAMILY={NOW - 60:.0f}\n")  # a new password
    assert by_part(run(path, FakeProbe(home)))["Calendar (family)"].status == "untested"
    recorded(path).failed("calendar:family", "no_calendar")
    assert by_part(run(path, FakeProbe(home)))["Calendar (family)"].status == "fail"  # a password fixes no name
    os.utime(path, (NOW - 60, NOW - 60))  # the calendar's name corrected in config.toml
    assert by_part(run(path, FakeProbe(home)))["Calendar (family)"].status == "untested"


HALL = "11111111-1111-4111-8111-111111111111"
PORCH = "22222222-2222-4222-8222-222222222222"
HUE = (
    '[[tapo.devices]]\nname = "lamp"\nhost = "192.168.1.20"\nkind = "light"\n'
    f'[[tapo.devices]]\nname = "hall light"\nbrand = "hue"\nlight = "{HALL}"\n'
    f'[[tapo.devices]]\nname = "porch light"\nbrand = "hue"\nlight = "{PORCH}"\nrule = "ask first"\n'
)


def paired(home: Path, mode: int = 0o600) -> None:
    """hue.env as scripts/hue-pair.sh saves it (doctor reads only which keys it has, never their values)."""
    path = home / ".config/local-assistant/hue.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"HUE_BRIDGE=192.168.1.40\nHUE_KEY=made-up-key\nHUE_FINGERPRINT={'ab' * 32}\n")
    path.chmod(mode)


def test_the_hue_bridge_reachable_with_its_certificate_and_every_light(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helpers_at(monkeypatch, Path(os.__file__))
    paired(home)
    probe = FakeProbe(home)
    probe.hue = {"ok": True, "found": [HALL, PORCH], "missing": []}
    findings = run(write_config(home, MINIMAL + HUE), probe)
    bridge = by_part(findings)["Hue Bridge"]
    assert (bridge.status, bridge.text) == (
        "ok",
        "reachable, its certificate the one saved at pairing, 2 of 2 lights found",
    )
    assert probe.hue_asked == [[HALL, PORCH]]  # the config's Hue lights, by their ids
    assert "made-up-key" not in render(findings)


@pytest.mark.parametrize(
    ("answer", "text", "fix"),
    [
        (
            {"ok": True, "found": [HALL], "missing": [PORCH]},
            "reachable, its certificate the one saved at pairing; not on the bridge: porch light",
            "scripts/tapo-discover.sh lists the bridge's lights; fix the light id in [[tapo.devices]]",
        ),
        (
            {"ok": False, "error": "unreachable"},
            "no answer at the address saved at pairing",
            "check that the bridge is plugged in; a new address: scripts/hue-pair.sh <address>",
        ),
        (
            {"ok": False, "error": "certificate"},
            "its certificate is not the one saved at pairing: nothing was sent to it",
            "if the bridge was replaced or reset: scripts/hue-pair.sh",
        ),
        ({"ok": False, "error": "key"}, "the bridge refused its key", "scripts/hue-pair.sh"),
        ({"ok": False, "error": "not_paired"}, "not paired yet", "scripts/hue-pair.sh"),
        ({"ok": False, "error": "failed"}, "the bridge's answer could not be read", "run doctor again later"),
    ],
)
def test_a_hue_bridge_problem_is_named_with_its_fix(
    home: Path, monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any], text: str, fix: str
) -> None:
    helpers_at(monkeypatch, Path(os.__file__))
    paired(home)
    probe = FakeProbe(home)
    probe.hue = answer
    bridge = by_part(run(write_config(home, MINIMAL + HUE), probe))["Hue Bridge"]
    assert (bridge.status, bridge.text, bridge.fix) == ("fail", text, fix)


def test_a_hue_light_the_bridge_cannot_reach_is_named(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helpers_at(monkeypatch, Path(os.__file__))
    paired(home)
    probe = FakeProbe(home)
    probe.hue = {"ok": True, "found": [HALL, PORCH], "missing": [], "disconnected": [PORCH]}
    bridge = by_part(run(write_config(home, MINIMAL + HUE), probe))["Hue Bridge"]
    assert (bridge.status, bridge.text, bridge.fix) == (
        "warn",
        "reachable, its certificate the one saved at pairing; not answering the bridge: porch light",
        "if it was switched off at the wall, switch it on there",
    )


@pytest.mark.parametrize("mode", [None, 0o644])
def test_without_a_private_pairing_the_bridge_is_not_asked(
    home: Path, monkeypatch: pytest.MonkeyPatch, mode: int | None
) -> None:
    helpers_at(monkeypatch, Path(os.__file__))
    if mode is not None:
        paired(home, mode)
    probe = FakeProbe(home)
    bridge = by_part(run(write_config(home, MINIMAL + HUE), probe))["Hue Bridge"]
    assert (bridge.status, bridge.text, bridge.fix) == ("fail", "not paired yet", "scripts/hue-pair.sh")
    assert probe.hue_asked == []


def test_a_bridge_not_checked_says_so(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helpers_at(monkeypatch, Path(os.__file__))
    paired(home)
    bridge = by_part(run(write_config(home, MINIMAL + HUE), FakeProbe(home)))["Hue Bridge"]
    assert (bridge.status, bridge.text) == ("untested", "paired, not checked: the device helper did not answer")


def test_without_hue_lights_there_is_no_bridge_line(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helpers_at(monkeypatch, Path(os.__file__))
    probe = FakeProbe(home)
    text = MINIMAL + '[[tapo.devices]]\nname = "lamp"\nhost = "192.168.1.20"\nkind = "light"\n'
    assert "Hue Bridge" not in by_part(run(write_config(home, text), probe))
    assert probe.hue_asked == []


def test_the_real_probe_never_reaches_a_bridge_under_a_test_root(home: Path) -> None:
    cfg = load_config(write_config(home, MINIMAL + HUE))
    assert Probe(home).hue_check(cfg, [HALL]) is None  # conftest gives every test its own ESTIA_ROOT


# ------------------------------------------------------------------ keep it running: sleep and power failures (finding 57)

PMSET = """System-wide power settings:
Currently in use:
 standby              0
 Sleep On Power Button 1
 autorestart          {restart}
 powernap             1
 networkoversleep     0
 disksleep            10
 sleep                {sleep} (sleep prevented by sharingd)
 ttyskeepawake        1
 displaysleep         10
"""


@pytest.mark.parametrize(
    ("sleep", "restart", "status"),
    [("0", "1", "ok"), ("1", "1", "warn"), ("0", "0", "warn"), ("10", "0", "warn")],
)
def test_doctor_warns_when_the_mac_sleeps_or_stays_off_after_a_power_failure(
    home: Path, sleep: str, restart: str, status: str
) -> None:
    """She stops when the Mac sleeps, and stays off after a power cut without autorestart (setup_assistant.md, 2.8)."""
    probe = FakeProbe(home)
    probe.pmset = PMSET.format(sleep=sleep, restart=restart)
    power = by_part(run(write_config(home, MINIMAL), probe))["Power"]
    assert power.status == status
    if status == "warn":
        assert "/usr/bin/sudo /usr/bin/pmset -a sleep 0 autorestart 1" in power.fix and "2.8" in power.fix
        assert ("sleeps" in power.text) is (sleep != "0")
        assert ("power failure" in power.text) is (restart != "1")


def test_power_settings_that_cannot_be_read_add_no_line(home: Path) -> None:
    assert "Power" not in by_part(run(write_config(home, MINIMAL), FakeProbe(home)))


@pytest.mark.parametrize("sleep", ["0", "1"])
def test_on_a_laptop_the_power_line_never_promises_a_start_after_a_power_failure(home: Path, sleep: str) -> None:
    """Review docs-laptop-power: on the friends' MacBook (no autorestart setting: a laptop has none), doctor said "the
    Mac starts again after a power failure", which is false, and nothing said that a closed lid stops her."""
    probe = FakeProbe(home)
    probe.pmset = PMSET.format(sleep=sleep, restart="1").replace(" autorestart          1\n", "")
    power = by_part(run(write_config(home, MINIMAL), probe))["Power"]
    assert "power failure" not in power.text and "a closed lid" in power.text, power
    if sleep == "0":
        assert power.status == "ok" and power.text == (
            "no sleep in the settings in use; on a laptop, a closed lid without an external display still stops her"
        )
    else:
        assert power.status == "warn" and "the Mac sleeps after 1 min without use" in power.text
        assert power.fix == (
            "/usr/bin/sudo /usr/bin/pmset -c sleep 0 (plugged in), or System Settings → Battery → Options; "
            "keep it plugged in, the lid open (setup_assistant.md, 2.8)"
        )
        assert "autorestart" not in power.fix


CUSTOM = """Battery Power:
 Sleep On Power Button 1
 displaysleep         2
 sleep                {battery}
 disksleep            10
AC Power:
 Sleep On Power Button 1
 displaysleep         10
 sleep                {ac}
 disksleep            10
"""


def test_on_a_laptop_the_power_line_reads_the_power_adapters_and_the_batterys_sleep(home: Path) -> None:
    """Second deep scan, macos-laptop-power-ac-only: doctor read only the settings in use, so plugged in with sleep 0
    it showed ✓ while the battery's own sleep timer would still put the MacBook to sleep, and nothing said to keep it
    plugged in. Each power source's sleep is read (`pmset -g custom`): the power adapter's counts, and the ✓ says to
    keep it plugged in when the battery's sleeps."""
    probe = FakeProbe(home)
    laptop = PMSET.replace(" autorestart          {restart}\n", "")
    probe.pmset = laptop.format(sleep="0")
    probe.pmset_custom = CUSTOM.format(battery="1", ac="0")
    power = by_part(run(write_config(home, MINIMAL), probe))["Power"]
    assert power.status == "ok" and power.text == (
        "no sleep on the power adapter; on battery the Mac sleeps after 1 min: keep it plugged in; on a laptop, a "
        "closed lid without an external display still stops her"
    ), power
    probe.pmset = laptop.format(sleep="1")  # on battery now: the power adapter's setting still counts
    assert by_part(run(write_config(home, MINIMAL), probe))["Power"].status == "ok"
    probe.pmset_custom = CUSTOM.format(battery="0", ac="0")
    power = by_part(run(write_config(home, MINIMAL), probe))["Power"]
    assert power.status == "ok" and "keep it plugged in" not in power.text and "power adapter" in power.text
    probe.pmset_custom = CUSTOM.format(battery="1", ac="10")
    power = by_part(run(write_config(home, MINIMAL), probe))["Power"]
    assert power.status == "warn" and "the Mac sleeps after 10 min without use" in power.text, power
    assert power.fix == prompts.DOCTOR_POWER_LAPTOP_FIX


def test_places_and_routes_need_no_key(home: Path) -> None:
    """OpenStreetMap is the free default: [maps] without a Google Maps key is fine, and there is no Google line."""
    findings = by_part(run(write_config(home, MINIMAL + "[maps]\n"), FakeProbe(home)))
    assert findings["OpenStreetMap"].status == "untested" and "Google Maps" not in findings


def test_a_house_number_in_the_home_area_gets_advice_and_is_kept(home: Path) -> None:
    text = MINIMAL + '[maps]\nhome = "Example Street 12, Town"\n'
    found = by_part(run(write_config(home, text), FakeProbe(home)))["OpenStreetMap"]
    assert found.text == "set up, not tested yet (the home area needs only the street and the city)"
    plain = by_part(run(write_config(home, MINIMAL + '[maps]\nhome = "Example Street, Town"\n'), FakeProbe(home)))
    assert plain["OpenStreetMap"].text == "set up, not tested yet"


def test_the_english_voices_are_checked_on_the_macs_own_model(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner, 26 Oct: one Pocket TTS model speaks every English voice, by the Mac's Gemma: doctor's one line checks the
    100M's pins beside Gemma 4 26B (32 GB here) and the 18M's beside 12B, names the model and its voices, and the
    health's one state ("pocket"); vera in the config of a Mac with the 18M is told, as heart, the default, says her
    lines there (owner, 29 Oct)."""
    monkeypatch.setattr(voice, "POCKET_FILES", {"pocket-tts/a/x.bin": hashlib.sha256(b"x").hexdigest()})
    monkeypatch.setattr(voice, "POCKET_FILES_100M", {"pocket-tts/b/y.bin": hashlib.sha256(b"y").hexdigest()})
    monkeypatch.setattr(voice, "POCKET_PROGRAM", {"tools/p/pocket-tts": hashlib.sha256(b"p").hexdigest()})
    monkeypatch.setattr(voice, "POCKET_SHIPPED", {"tools/p/heart.safetensors": hashlib.sha256(b"h").hexdigest()})
    monkeypatch.setattr(
        voice, "POCKET_SHIPPED_18M", {"tools/p/heart-18m.safetensors": hashlib.sha256(b"s").hexdigest()}
    )
    from local_assistant import hostsec

    program = hostsec.estia_root() / "usr/local/estia/current/tools/p/pocket-tts"
    program.parent.mkdir(parents=True)
    program.write_bytes(b"p")
    (program.parent / "heart.safetensors").write_bytes(b"h")
    (program.parent / "heart-18m.safetensors").write_bytes(b"s")
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL + VOICE)
    models = home / "Library/Application Support/local-assistant/voice-models"
    log = "~/Library/Logs/local-assistant/voice.log"
    part = "Pocket TTS voices"
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "loading"}
    found = by_part(run(path, probe))
    assert "Voice eve-full" not in found and "Voice eve" not in found  # one line for the one model
    assert found[part] == Finding(
        "warn",
        part,
        pocket_line(prompts.DOCTOR_POCKET_MISSING.format(size=250)),
        prompts.DOCTOR_POCKET_MISSING_FIX.format(log=log),
    )
    assert prompts.DOCTOR_POCKET_MISSING.format(size=250) == (
        "not fetched yet (about 250 MB, at the voice service's start): her answers come as text meanwhile"
    )
    assert all(len(shown) <= hostsec.TERMINAL for shown in render([found[part]]).splitlines())
    (models / "pocket-tts/b").mkdir(parents=True)
    (models / "pocket-tts/b/y.bin").write_bytes(b"y")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "ready"}
    ready = by_part(run(path, probe))[part]
    assert ready.status == "ok" and ready.text == (
        "Pocket TTS ready on its 100M model (heart, jane, eve, vera, alba, michael); its files are the pinned ones"
    )
    # a Mac with Gemma 4 12B: the 18M's pins, about 130 MB, no vera, its own heart (owner, 29 Oct)
    monkeypatch.setattr(voice, "memory_gb", lambda: 16.0)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "loading"}
    small_missing = prompts.DOCTOR_POCKET_MISSING.format(size=130)
    assert by_part(run(path, probe))[part].text == pocket_line(small_missing)
    (models / "pocket-tts/a").mkdir(parents=True)
    (models / "pocket-tts/a/x.bin").write_bytes(b"x")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "ready"}
    small = by_part(run(path, probe))[part]
    assert small.status == "ok" and small.text == (
        "Pocket TTS ready on its 18M model (heart, jane, eve, alba, michael); its files are the pinned ones"
    )
    vera = write_config(home, MINIMAL + VOICE + 'voice = "vera"\n')
    told = by_part(run(vera, probe))[part]
    assert told == Finding("warn", part, prompts.DOCTOR_POCKET_NO_VERA, prompts.DOCTOR_POCKET_NO_VERA_FIX)
    assert told.text == "the config's voice vera speaks only on a Mac with 32 GB of memory or more: heart speaks here"
    assert told.fix == 'pick another with /voice (/voices lists them), or set [voice] voice = "heart"'
    # heart, the default on either model (owner, 29 Oct: "on 16gb make Heart the default as well"), speaks as heart
    # beside the 18M, as an empty [voice] does: no warning; the 18M's own file in her release is the one asked for
    (program.parent / "heart.safetensors").unlink()
    for heart in (write_config(home, MINIMAL + VOICE + 'voice = "heart"\n'), write_config(home, MINIMAL + VOICE)):
        assert by_part(run(heart, probe))[part] == small
    (program.parent / "heart.safetensors").write_bytes(b"h")
    (program.parent / "heart-18m.safetensors").unlink()
    no_small = prompts.DOCTOR_POCKET_NO_SHIPPED.format(file="heart-18m.safetensors")
    assert by_part(run(write_config(home, MINIMAL + VOICE), probe))[part].text == no_small
    (program.parent / "heart-18m.safetensors").write_bytes(b"s")
    vera = write_config(home, MINIMAL + VOICE + 'voice = "vera"\n')
    # the review of 26 Oct (R1-F4): the voice that speaks is judged, the one chosen with /voice first (read only); its
    # own fix, /voice jane, leaves no warning
    database = load_config(vera).db_path
    database.parent.mkdir(parents=True, exist_ok=True)
    store = Store.open(database)
    store.set_state("voice_name", "jane")
    store.close()
    assert by_part(run(vera, probe))[part] == small
    store = Store.open(database)
    store.set_state("voice_name", "vera")  # not used at a start here: the config's is judged
    store.close()
    assert by_part(run(vera, probe))[part] == told
    store = Store.open(database)
    store.set_state("voice_name", "af_sky")  # any Kokoro id (3 Nov 2026): heart, as the bridge reads it at its start
    store.close()
    assert by_part(run(vera, probe))[part] == small
    store = Store.open(database)
    store.set_state("voice_name", "eve-full")  # another name for eve
    store.close()
    assert by_part(run(vera, probe))[part] == small
    store = Store.open(database)
    store.set_state("voice_name", "am_puck")  # a Kokoro voice chosen before 3 Nov 2026: michael speaks it
    store.close()
    assert by_part(run(vera, probe))[part] == small


def test_hearts_file_is_checked_in_her_release(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner, 27 Oct ("call the voice Heart"; its file in the public repository: "yes"): heart's file ships with the
    voice service, in its own bundle beside its program in her release (`voice.POCKET_SHIPPED`), where the voice
    service copies it from; doctor names it when it is not there or not the pinned one (the review of 27 Oct,
    swift-voice-2: then only heart is not said, its other voices are Pocket's as ever), and checks
    the models folder's copy with the other files. Beside the 18M its own file is the one asked for (owner, 29 Oct:
    `voice.POCKET_SHIPPED_18M`, heart-18m.safetensors), never the 100M's. A bundle laid out flat (the native build,
    Swift 6.2's default: swift-voice-1) is read there too."""
    real_shipped, real_small, real_100m = voice.POCKET_SHIPPED, voice.POCKET_SHIPPED_18M, voice.POCKET_FILES_100M
    monkeypatch.setattr(voice, "POCKET_FILES_100M", {"pocket-tts/b/y.bin": hashlib.sha256(b"y").hexdigest()})
    monkeypatch.setattr(voice, "POCKET_FILES", {"pocket-tts/a/x.bin": hashlib.sha256(b"x").hexdigest()})
    monkeypatch.setattr(voice, "POCKET_PROGRAM", {"tools/p/pocket-tts": hashlib.sha256(b"p").hexdigest()})
    monkeypatch.setattr(voice, "POCKET_SHIPPED", {"tools/p/heart.safetensors": hashlib.sha256(b"h").hexdigest()})
    monkeypatch.setattr(
        voice, "POCKET_SHIPPED_18M", {"tools/p/heart-18m.safetensors": hashlib.sha256(b"s").hexdigest()}
    )
    from local_assistant import hostsec

    program = hostsec.estia_root() / "usr/local/estia/current/tools/p/pocket-tts"
    program.parent.mkdir(parents=True)
    program.write_bytes(b"p")
    models = home / "Library/Application Support/local-assistant/voice-models"
    (models / "pocket-tts/b").mkdir(parents=True)
    (models / "pocket-tts/b/y.bin").write_bytes(b"y")
    (models / "pocket-tts/a").mkdir(parents=True)
    (models / "pocket-tts/a/x.bin").write_bytes(b"x")
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "unavailable"}
    path = write_config(home, MINIMAL + VOICE)
    part = "Pocket TTS voices"
    update = prompts.DOCTOR_POCKET_PROGRAM_FIX.format(deploy=ownuser.DEPLOY_CMD)
    big = {"file": "heart.safetensors"}
    assert by_part(run(path, probe))[part] == Finding(
        "warn", part, prompts.DOCTOR_POCKET_NO_SHIPPED.format(**big), update
    )
    assert prompts.DOCTOR_POCKET_NO_SHIPPED.format(**big) == (
        "Heart's voice file (heart.safetensors) is not in her release yet: in heart's voice her answers come as text"
        " meanwhile"
    )
    # heart's file is named whichever voice speaks
    alba = write_config(home, MINIMAL + VOICE + 'voice = "alba"\n')
    assert by_part(run(alba, probe))[part].text == prompts.DOCTOR_POCKET_NO_SHIPPED.format(**big)
    (program.parent / "heart.safetensors").write_bytes(b"H")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "refused"}
    assert by_part(run(path, probe))[part] == Finding(
        "fail", part, prompts.DOCTOR_POCKET_SHIPPED_CHANGED.format(**big), update
    )
    assert prompts.DOCTOR_POCKET_SHIPPED_CHANGED.format(**big) == (
        "Heart's voice file (heart.safetensors) in her release is not the pinned one: never run; in heart's voice her"
        " answers come as text"
    )
    (program.parent / "heart.safetensors").write_bytes(b"h")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "ready"}
    assert by_part(run(path, probe))[part].status == "ok"
    # a 16 GB Mac (the 18M): its own file, heart-18m.safetensors, never the 100M's
    monkeypatch.setattr(voice, "memory_gb", lambda: 16.0)
    small = {"file": "heart-18m.safetensors"}
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "unavailable"}
    assert by_part(run(path, probe))[part] == Finding(
        "warn", part, prompts.DOCTOR_POCKET_NO_SHIPPED.format(**small), update
    )
    (program.parent / "heart-18m.safetensors").write_bytes(b"S")
    assert by_part(run(path, probe))[part] == Finding(
        "fail", part, prompts.DOCTOR_POCKET_SHIPPED_CHANGED.format(**small), update
    )
    (program.parent / "heart-18m.safetensors").write_bytes(b"s")
    (program.parent / "heart.safetensors").unlink()  # the 100M's: not asked for here
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "ready"}
    assert by_part(run(path, probe))[part].status == "ok"
    (program.parent / "heart.safetensors").write_bytes(b"h")
    monkeypatch.setattr(voice, "memory_gb", lambda: 32.0)
    # the real pins: the voice service's own bundle beside its program, the sha256 of the repository's files, in
    # Contents/Resources (swiftbuild) or at the bundle's top (the native build)
    heart = "60e5e6cbcac2f61554adcfc1ffd9830308802a3685db49489f5a14e8e65b5425"
    heart18 = "0d9e5334433271a849bfa97b1a8626cb690446a98ea3008ba016704bc51cd2bb"
    bundle = "tools/voice/.build/release/voice-service_voice-service.bundle"
    assert real_shipped == {
        f"{bundle}/Contents/Resources/heart.safetensors": heart,
        f"{bundle}/heart.safetensors": heart,
    }
    assert real_small == {
        f"{bundle}/Contents/Resources/heart-18m.safetensors": heart18,
        f"{bundle}/heart-18m.safetensors": heart18,
    }
    assert real_100m["pocket-tts/english/heart/heart.safetensors"] == heart
    # a flat bundle in her release: found, so no warning
    flat = {"tools/p/Contents/Resources/heart.safetensors": hashlib.sha256(b"h").hexdigest()}
    flat["tools/p/heart.safetensors"] = flat["tools/p/Contents/Resources/heart.safetensors"]
    monkeypatch.setattr(voice, "POCKET_SHIPPED", flat)
    assert not (program.parent / "Contents").exists() and (program.parent / "heart.safetensors").read_bytes() == b"h"
    assert by_part(run(path, probe))[part].status == "ok"
    (program.parent / "heart.safetensors").unlink()
    assert by_part(run(path, probe))[part].text == prompts.DOCTOR_POCKET_NO_SHIPPED.format(**big)


def test_janes_model_for_the_language_in_use_is_checked(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner, 25 Oct: jane speaks Spanish, Italian, Portuguese, Dutch, German and French in Pocket TTS, each language's
    model fetched only for the language in use. Doctor's voice line covers that language (the one set with /language,
    else the config's): its files against its pins and its state in the health (`pocket_<code>`); not fetched yet, or
    fetched and not checked since a start, is no warning; being fetched, other bytes, refused and a voice service from
    before it are told. English, and the four languages with no voice (owner, 3 Nov 2026), get no such line."""
    pins = {"de": {"pocket-tts/g/z.bin": hashlib.sha256(b"z").hexdigest()}, "nl": {"pocket-tts/n/y.bin": "0" * 64}}
    monkeypatch.setattr(voice, "POCKET_LANGUAGE_FILES", pins)
    probe = FakeProbe(home)
    part = "Voice jane (German)"
    assert part not in by_part(run(write_config(home, MINIMAL + VOICE), probe))  # English: no line
    assert not [
        p for p in by_part(run(write_config(home, 'language = "ko"\n' + MINIMAL + VOICE), probe)) if "jane (" in p
    ]
    assert part not in by_part(run(write_config(home, 'language = "de"\n' + MINIMAL), probe))  # no voice: no line
    path = write_config(home, 'language = "de"\n' + MINIMAL + VOICE)
    models = home / "Library/Application Support/local-assistant/voice-models"
    log = "~/Library/Logs/local-assistant/voice.log"
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "ready", "pocket_de": "idle"}
    found = by_part(run(path, probe))
    assert found[part] == Finding("ok", part, prompts.DOCTOR_POCKET_LANGUAGE_NOT_YET.format(language="German"))
    assert prompts.DOCTOR_POCKET_LANGUAGE_NOT_YET.format(language="German") == (
        "not fetched yet: fetched the first time she speaks German (about 227 MB); until then her answers in German"
        " come as text"
    )
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket_de": "loading"}
    assert by_part(run(path, probe))[part] == Finding(
        "warn",
        part,
        prompts.DOCTOR_POCKET_LANGUAGE_FETCHING.format(language="German"),
        prompts.DOCTOR_POCKET_MISSING_FIX.format(log=log),
    )
    (models / "pocket-tts/g").mkdir(parents=True)
    (models / "pocket-tts/g/z.bin").write_bytes(b"z")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket_de": "ready"}
    assert by_part(run(path, probe))[part] == Finding("ok", part, prompts.DOCTOR_POCKET_OK)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket_de": "idle"}  # fetched, not checked since a start
    assert by_part(run(path, probe))[part] == Finding(
        "ok", part, prompts.DOCTOR_POCKET_LANGUAGE_IDLE.format(language="German")
    )
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket_de": "unavailable"}
    assert by_part(run(path, probe))[part] == Finding(
        "warn",
        part,
        prompts.DOCTOR_POCKET_LANGUAGE_UNAVAILABLE.format(language="German"),
        prompts.DOCTOR_POCKET_LOG_FIX.format(log=log),
    )
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "ready"}  # a voice service from before it
    assert by_part(run(path, probe))[part] == Finding(
        "warn", part, prompts.DOCTOR_POCKET_LANGUAGE_OLD.format(language="German"), prompts.DOCTOR_VOICE_REINSTALL
    )
    (models / "pocket-tts/g/z.bin").write_bytes(b"Z")  # other bytes: never run
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket_de": "refused"}
    changed = by_part(run(path, probe))[part]
    assert (changed.status, changed.text) == ("fail", prompts.DOCTOR_POCKET_LANGUAGE_CHANGED.format(language="German"))
    dutch = by_part(run(write_config(home, 'language = "nl"\n' + MINIMAL + VOICE), probe))  # the line follows it
    assert part not in dutch and "Voice jane (Dutch)" in dutch


def test_an_old_supertonic_setting_still_gets_the_jane_line(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`[voice] language_voice = ""` gave the six languages to Supertonic-3's F1; Supertonic-3 was removed (owner,
    3 Nov 2026), so the setting loads as jane (config's note) and doctor checks jane's model as for any config."""
    monkeypatch.setattr(voice, "POCKET_LANGUAGE_FILES", {"de": {"pocket-tts/g/z.bin": "0" * 64}})
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket_de": "idle"}
    old = by_part(run(write_config(home, 'language = "de"\n' + MINIMAL + VOICE + 'language_voice = ""\n'), probe))
    assert old["Config"].status == "ok" and "Voice jane (German)" in old


@pytest.mark.parametrize(
    ("code", "english"), [("ko", "Korean"), ("pl", "Polish"), ("ru", "Russian"), ("ja", "Japanese")]
)
def test_a_language_with_no_voice_gets_its_own_line(home: Path, code: str, english: str) -> None:
    """Owner, 3 Nov 2026: Korean, Polish, Russian and Japanese have no voice. With [voice] and one of them in use,
    doctor says so in the words of /language's reply (an ok line: nothing to fix); English and the six get none."""
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "ready"}
    found = by_part(run(write_config(home, f'language = "{code}"\n' + MINIMAL + VOICE), probe))
    assert found[f"Voice ({english})"] == Finding(
        "ok",
        f"Voice ({english})",
        f"no voice in {english}: answers come as text, and calls in {english} are not picked up",
    )
    assert not [part for part in by_part(run(write_config(home, MINIMAL + VOICE), probe)) if part.startswith("Voice (")]
    assert f"Voice ({english})" not in by_part(run(write_config(home, f'language = "{code}"\n' + MINIMAL), probe))


def test_a_language_models_changed_files_have_their_own_fix(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 25 Oct: a language's model whose files changed or were refused got eve's fix ("fetches them again
    when it starts again"), but a language's speaker is idle at every start and checks nothing until a line is asked in
    it, and its next step deleted the whole pocket-tts folder (eve's, the standard model's and every other language's).
    Its own fix: the first line she speaks in the language sets the files aside and fetches them again; the folder to
    delete is the language's own."""
    pins = {"de": {"pocket-tts/german/weights/z.bin": hashlib.sha256(b"z").hexdigest()}}
    monkeypatch.setattr(voice, "POCKET_LANGUAGE_FILES", pins)
    probe = FakeProbe(home)
    path = write_config(home, 'language = "de"\n' + MINIMAL + VOICE)
    models = home / "Library/Application Support/local-assistant/voice-models"
    (models / "pocket-tts/german/weights").mkdir(parents=True)
    (models / "pocket-tts/german/weights/z.bin").write_bytes(b"Z")  # other bytes
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket_de": "idle"}
    changed = by_part(run(path, probe))["Voice jane (German)"]
    assert changed.status == "fail"
    assert changed.fix == prompts.DOCTOR_POCKET_LANGUAGE_CHANGED_FIX.format(
        log="~/Library/Logs/local-assistant/voice.log",
        restart=prompts.DOCTOR_VOICE_REINSTALL,
        language="German",
        folder="~/Library/Application Support/local-assistant/voice-models/pocket-tts/german",
        rights="",
    )
    assert changed.fix == (
        "see ~/Library/Logs/local-assistant/voice.log for why; after "
        + prompts.DOCTOR_VOICE_REINSTALL
        + ", the first line she speaks in German (a voice note or a call; she asks one herself when she starts) sets"
        " the changed files aside and fetches them again; files that could not be set aside: delete"
        " ~/Library/Application Support/local-assistant/voice-models/pocket-tts/german first"
    )
    assert "pocket-tts first" not in changed.fix  # never the whole folder: eve's and the other languages' too


def test_a_practice_language_gets_its_own_line(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 25 Oct: practice in another language (`/babble de`) fetches and runs that language's model too, but
    doctor checked only the /language one. A running practice in one of the six gets its own line beside it; an ended
    one, or one in the language in use, none more."""
    pins = {"de": {"pocket-tts/g/z.bin": "0" * 64}, "fr": {"pocket-tts/f/y.bin": "0" * 64}}
    monkeypatch.setattr(voice, "POCKET_LANGUAGE_FILES", pins)
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket_de": "idle", "pocket_fr": "idle"}
    path = write_config(home, 'language = "fr"\n' + MINIMAL + VOICE)
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    session = {"code": "de", "level": "A2", "aci": ACI, "started": 1.0, "conversation": 2, "ended": 0.0, "due": 0.0}
    store.set_state(babble.SESSION_KEY, json.dumps(session))
    found = by_part(run(path, probe))
    assert "Voice jane (French)" in found and "Voice jane (German)" in found
    store.set_state(babble.SESSION_KEY, json.dumps(session | {"ended": 5.0, "due": 9.0, "reason": "off"}))
    assert "Voice jane (German)" not in by_part(run(path, probe))  # ended: not in use
    store.set_state(babble.SESSION_KEY, json.dumps(session | {"code": "fr"}))
    assert [part for part in by_part(run(path, probe)) if "jane (" in part] == ["Voice jane (French)"]
    store.close()


def test_practice_in_a_language_with_no_voice_gets_its_own_line(home: Path) -> None:
    """The review of 4 Nov: Japanese practice makes every call of the user's ring out, and doctor read only the
    language in use. A running practice in a language with no voice gets one line saying calls are not picked up
    until /babble off; English in use, nothing else."""
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "ready"}
    path = write_config(home, MINIMAL + VOICE)
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    session = {"code": "ja", "level": "A2", "aci": ACI, "started": 1.0, "conversation": 2, "ended": 0.0, "due": 0.0}
    store.set_state(babble.SESSION_KEY, json.dumps(session))
    found = by_part(run(path, probe))
    assert found["Voice (Japanese)"] == Finding(
        "ok",
        "Voice (Japanese)",
        "practice in Japanese is text only: no voice in it, so calls are not picked up until /babble off",
    )
    assert [part for part in found if part.startswith("Voice (")] == ["Voice (Japanese)"]
    store.set_state(babble.SESSION_KEY, json.dumps(session | {"ended": 5.0, "due": 9.0, "reason": "off"}))
    assert "Voice (Japanese)" not in by_part(run(path, probe))
    store.close()


def test_the_english_voices_files_are_checked_against_their_pins(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With [voice]: the English voices' files (Pocket TTS: the release program beside the voice service in her
    release, the Mac's model in the voice service's models folder, every sha256), and its state in the health; whatever
    stops them, her answers come as text meanwhile (owner, 3 Nov 2026: no other voice says a line)."""
    pins = {"pocket-tts/a/x.bin": hashlib.sha256(b"x").hexdigest()}
    monkeypatch.setattr(voice, "POCKET_FILES_100M", pins)  # the 100M's: 32 GB in the tests
    monkeypatch.setattr(voice, "POCKET_PROGRAM", {"tools/p/pocket-tts": hashlib.sha256(b"p").hexdigest()})
    monkeypatch.setattr(voice, "POCKET_SHIPPED", {"tools/p/heart.safetensors": hashlib.sha256(b"h").hexdigest()})
    from local_assistant import hostsec

    program = hostsec.estia_root() / "usr/local/estia/current/tools/p/pocket-tts"
    program.parent.mkdir(parents=True)
    program.write_bytes(b"p")
    (program.parent / "heart.safetensors").write_bytes(b"h")
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL + VOICE)
    part = "Pocket TTS voices"
    assert part not in by_part(run(write_config(home, MINIMAL), probe))  # no voice: no line
    path = write_config(home, MINIMAL + VOICE)
    models = home / "Library/Application Support/local-assistant/voice-models"
    log = "~/Library/Logs/local-assistant/voice.log"
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "loading"}
    assert by_part(run(path, probe))[part] == Finding(
        "warn",
        part,
        pocket_line(prompts.DOCTOR_POCKET_MISSING.format(size=250)),
        prompts.DOCTOR_POCKET_MISSING_FIX.format(log=log),
    )
    (models / "pocket-tts/a").mkdir(parents=True)
    (models / "pocket-tts/a/x.bin").write_bytes(b"x")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "ready"}
    assert by_part(run(path, probe))[part].status == "ok"
    for state, text in (("loading", prompts.DOCTOR_POCKET_LOADING), ("unavailable", prompts.DOCTOR_POCKET_UNAVAILABLE)):
        probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": state}
        found = by_part(run(path, probe))[part]
        assert found == Finding("warn", part, pocket_line(text), prompts.DOCTOR_POCKET_LOG_FIX.format(log=log))
        assert all(len(shown) <= hostsec.TERMINAL for shown in render([found]).splitlines()), state
    assert prompts.DOCTOR_POCKET_LOADING == "loading: her answers come as text until it is ready"
    assert prompts.DOCTOR_POCKET_UNAVAILABLE == (
        "unavailable (a download or a run failed; tried again by itself): her answers come as text meanwhile"
    )
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready"}  # a voice service from before Pocket TTS
    assert by_part(run(path, probe))[part] == Finding(
        "warn", part, prompts.DOCTOR_POCKET_OLD, prompts.DOCTOR_VOICE_REINSTALL
    )
    (models / "pocket-tts/a/x.bin").write_bytes(b"X")  # other bytes: never run
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "refused"}
    changed = by_part(run(path, probe))[part]
    assert (changed.status, changed.text) == ("fail", prompts.DOCTOR_POCKET_CHANGED)
    assert changed.fix == (
        f"see {log} for why; the voice service sets them aside and fetches them again when it starts again:"
        " scripts/install-voice-daemon.sh (asks for your Mac password: run it yourself); files that could not be set"
        " aside: delete ~/Library/Application Support/local-assistant/voice-models/pocket-tts first"
    )
    (models / "pocket-tts/a/x.bin").write_bytes(b"x")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "refused"}  # refused, files right again
    assert by_part(run(path, probe))[part].text == prompts.DOCTOR_POCKET_CHANGED
    (models / "pocket-tts/a").chmod(0)
    try:
        unread = by_part(run(path, probe))[part]
    finally:
        (models / "pocket-tts/a").chmod(0o700)
    assert (unread.status, unread.text) == ("warn", prompts.DOCTOR_POCKET_UNREAD)
    # the program in her release (the review of 24 Oct: never in her home): not there yet, or other bytes
    deploy = ownuser.DEPLOY_CMD
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "unavailable"}
    program.unlink()
    assert by_part(run(path, probe))[part] == Finding(
        "warn",
        part,
        prompts.DOCTOR_POCKET_NO_PROGRAM,
        prompts.DOCTOR_POCKET_PROGRAM_FIX.format(deploy=deploy),
    )
    assert (
        prompts.DOCTOR_POCKET_NO_PROGRAM == "its program is not in her release yet: her answers come as text meanwhile"
    )
    # whichever voice is in use (the config's, or the one chosen with /voice; a Kokoro one from before 3 Nov 2026 as
    # its Pocket voice), the line is the same: no other voice is named
    for config, stored in (("alba", None), ("jane", "michael"), ("michael", "jane"), ("am_puck", None)):
        chosen = write_config(home, MINIMAL + VOICE + f'voice = "{config}"\n')
        database = load_config(chosen).db_path
        database.parent.mkdir(parents=True, exist_ok=True)
        store = Store.open(database)
        store.set_state("voice_name", stored or "")
        store.close()
        assert by_part(run(chosen, probe))[part].text == prompts.DOCTOR_POCKET_NO_PROGRAM, (config, stored)
    path = write_config(home, MINIMAL + VOICE)
    assert prompts.DOCTOR_POCKET_PROGRAM_FIX.format(deploy=deploy) == (
        f"an update puts the pinned one there: {deploy} (if it says the root-run tools differ: read the diff, run "
        f"{deploy} --update-tools, then {deploy} again)"
    )
    program.write_bytes(b"P")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "pocket": "refused"}
    assert by_part(run(path, probe))[part] == Finding(
        "fail",
        part,
        prompts.DOCTOR_POCKET_PROGRAM_CHANGED,
        prompts.DOCTOR_POCKET_PROGRAM_FIX.format(deploy=deploy),
    )


# ------------------------------------------------------------------ her own Ollama (as _estia, on her own address)

HERS = 'ollama_base_url = "http://127.0.0.1:8151/v1"\n'
HER_UID = 4242


def her_copy_on_the_mac(monkeypatch: pytest.MonkeyPatch) -> str:
    """Root's copy of the official build under the suite's system root, every folder up to it "root's" (the test's
    account plays root), and her account's number."""
    from local_assistant import ollama_build

    root = hostsec.estia_root()
    monkeypatch.setattr(ownuser, "CODE_UID", os.getuid())
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: HER_UID if name == "_estia" else os.getuid())
    folder = root / "usr/local/estia/ollama/v0.40.4"
    for name in ("ollama", *ollama_build.XGRAMMAR):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"\xcf\xfa\xed\xfe")
    (folder.parent / "current").symlink_to("v0.40.4")
    for path in (root, *[p for p in root.rglob("*") if not p.is_symlink()]):
        path.chmod(0o755 if path.is_dir() else 0o644)
    return "/usr/local/estia/ollama/v0.40.4/ollama"


@pytest.mark.parametrize("as_her", [False, True], ids=["as-you", "as-her"])
def test_her_own_ollama_is_a_tick_on_her_address_as_you_and_as_her(
    home: Path, monkeypatch: pytest.MonkeyPatch, as_her: bool
) -> None:
    probe = FakeProbe(home)
    probe.her = as_her
    probe.ollama_at = her_copy_on_the_mac(monkeypatch)
    probe.ollama_addresses = ("127.0.0.1.8151",)
    probe.ollama_uid = HER_UID
    probe.answers["http://127.0.0.1:8151/api/version"] = {"version": "0.40.4"}
    probe.answers["http://127.0.0.1:8151/api/tags"] = {"models": [{"name": "local-worker:latest"}]}
    monkeypatch.setattr(FakeProbe, "owner_uid", lambda self: os.getuid())  # yours: the account, or the move's record
    parts = by_part(run(write_config(home, MINIMAL + HERS), probe))
    line = parts["Ollama"]
    assert line.status == "ok", line
    assert (
        line.text
        == "0.40.4 answers (/usr/local/estia/ollama/v0.40.4/ollama): her own, as _estia, with its JSON-schema library"
    )
    assert probe.ollama_ports == [8151] and parts["Model"].status == "ok"
    probe.ollama_unread = True  # her service stood in for a socket netstat did not list (3 Nov report)
    line = by_part(run(write_config(home, MINIMAL + HERS), probe))["Ollama"]
    assert line.status == "ok"
    assert " ".join(line.text.split()).endswith(f"with its JSON-schema library{UNLISTED_ON_PORT}")
    # fix review 6: about 190 columns in one line before; render does not wrap
    assert all(len(shown) <= hostsec.TERMINAL for shown in render([line]).splitlines())


def test_doctor_names_her_python_and_the_openssl_that_encrypts_her_connections(home: Path) -> None:
    """3 Nov report: the release scan reads only the lockfiles, yet her connections to the web are encrypted by her
    Python's own OpenSSL. Doctor names both (a fact, ○: no network, so newer releases are not looked up); each release
    compares that OpenSSL by hand (docs/agents/docs-release.md)."""
    findings = run(write_config(home, MINIMAL), FakeProbe(home))
    line = by_part(findings)["Python"]
    assert line.status == "untested"
    assert line.text == (
        "3.13.16, with OpenSSL 3.5.9 29 Sep 2026, which encrypts her connections to mail, calendars, maps and the web"
    )
    assert [finding.part for finding in findings][:3] == ["Mac", "Tools", "Python"]
    assert all(len(shown) <= 135 for shown in render([line]).splitlines())


@pytest.mark.parametrize(
    ("as_her", "then"), [(False, "uv python upgrade 3.13"), (True, "/usr/bin/sudo /usr/local/estia/bin/estia-deploy")]
)
def test_the_python_line_says_how_a_newer_openssl_comes(home: Path, as_her: bool, then: str) -> None:
    """Fix review 7: Python's patch version, and so its OpenSSL, follows the uv on the Mac (SECURITY.md). Her own
    Python comes with a deploy; the checkout's from uv itself."""
    probe = FakeProbe(home)
    probe.her = as_her
    line = by_part(run(write_config(home, MINIMAL), probe))["Python"]
    assert line.fix == f"if a newer OpenSSL is out, a newer uv may bring it: brew upgrade uv, then {then}"
    assert all(len(shown) <= hostsec.TERMINAL for shown in render([line]).splitlines())


def test_the_python_doctor_runs_on_names_its_version_and_its_ssl_library() -> None:
    from local_assistant import deploy

    python, library = Probe().python_versions()
    assert python.startswith(f"{deploy.PYTHON_SERIES}.")  # the series a deploy installs (the tests run on it)
    assert re.fullmatch(r"(OpenSSL|LibreSSL) [0-9][^ ]* .+", library), library  # a library's name and its version


def test_her_address_held_by_another_account_or_open_to_other_machines_is_named(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = FakeProbe(home)
    probe.ollama_at = her_copy_on_the_mac(monkeypatch)
    probe.ollama_addresses = ("127.0.0.1.8151",)
    probe.ollama_uid = os.getuid() + 1  # not hers, not yours
    probe.answers["http://127.0.0.1:8151/api/version"] = {"version": "0.40.4"}
    line = by_part(run(write_config(home, MINIMAL + HERS), probe))["Ollama"]
    assert line.status == "fail" and "another account on this Mac" in line.text
    probe.ollama_uid = HER_UID
    probe.ollama_addresses = ("*.8151",)
    line = by_part(run(write_config(home, MINIMAL + HERS), probe))["Ollama"]
    assert line.status == "warn" and "OLLAMA_HOST=127.0.0.1:8151" in line.text  # as for any Ollama other Macs reach


def test_her_own_ollamas_service_file_is_checked_against_her_own(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from local_assistant import ollama_build, setup

    monkeypatch.setattr(setup, "memory_gb", lambda: 32.0)
    daemon = hostsec.estia_root() / ollama_build.DAEMON.relative_to("/")
    daemon.parent.mkdir(parents=True, exist_ok=True)
    daemon.write_bytes(ollama_build.her_plist(32_768))
    assert "Ollama service" not in by_part(run(write_config(home, MINIMAL + HERS), FakeProbe(home)))
    daemon.write_bytes(ollama_build.her_plist(16_384))
    line = by_part(run(write_config(home, MINIMAL + HERS), FakeProbe(home)))["Ollama service"]
    assert line.status == "warn"

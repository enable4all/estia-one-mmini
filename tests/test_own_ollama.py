"""Her own Ollama in her own macOS account (owner, 29 Oct: "Her own, in _estia"), and the promise around it: an install
from before 0.9.8 (or a new one that says no to her own account) keeps today's Ollama step byte for byte. Nothing on
the Mac is read or run: the tools are the Ollama step's fakes (tests/test_ollama_build.py), root's commands played
into the test's own tree."""

import functools
import grp
import hashlib
import os
import plistlib
import pwd
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from local_assistant import ollama_build, prompts
from local_assistant.hostsec import LAUNCHCTL, SUDO, TAR, Host
from local_assistant.ollama_build import DAEMON, LOG, SPCTL, TEE, XGRAMMAR, Release, daemon_plist
from tests.setup_helpers import noted
from tests.test_ollama_build import LOCAL, FakeTools, official_tree, release_of, tarball

ME = pwd.getpwuid(os.getuid()).pw_name
GROUP = grp.getgrgid(os.getgid()).gr_name
INSTALL = "/usr/bin/install"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    return folder


def tree_of(*tops: Path) -> dict[str, bytes | str]:
    """Every file, folder and link under these folders: its path and its bytes (a link: its target)."""
    found: dict[str, bytes | str] = {}
    for top in tops:
        for folder, folders, files in os.walk(top):
            for name in [*folders, *files]:
                path = Path(folder) / name
                key = str(path)
                if path.is_symlink():
                    found[key] = "-> " + os.readlink(path)
                elif path.is_file():
                    found[key] = path.read_bytes()
                else:
                    found[key] = "dir"
    return found


def older_mac(tmp_path: Path, home: Path, *, moved: bool) -> FakeTools:
    """An install from before 0.9.8: setup's own service file runs Ollama's official build as you from
    ~/opt/ollama/current on 127.0.0.1:11434, and it answers; after a move, the bridge's service file runs as _estia."""
    official_tree(home / "opt/ollama/v0.40.4")
    (home / "opt/ollama/current").symlink_to(home / "opt/ollama/v0.40.4")
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(home / "opt/ollama/v0.40.4/ollama"), addresses=LOCAL)
    daemons = tools.root / DAEMON.parent.relative_to("/")
    daemons.mkdir(parents=True)
    (daemons / DAEMON.name).write_bytes(daemon_plist(user=ME, group=GROUP, home=home, num_ctx=32_768))
    if moved:
        bridge = {"Label": "com.local-assistant.bridge", "UserName": "_estia"}
        (daemons / "com.local-assistant.bridge.plist").write_bytes(plistlib.dumps(bridge))
    return tools


@pytest.mark.parametrize("moved", [False, True], ids=["as-you", "after-a-move"])
def test_an_older_install_keeps_todays_ollama_step_byte_for_byte(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], moved: bool
) -> None:
    """Golden, as 3600d9ea runs it (the test was written and passed there first): setup's step 1 on an older install
    says the same lines, runs the same admin command and writes nothing, as you and after the move."""
    from local_assistant import setup

    tools = older_mac(tmp_path, home, moved=moved)
    monkeypatch.setattr(ollama_build, "Tools", lambda say=print: tools)
    monkeypatch.setattr(ollama_build, "owner_home", lambda: home)
    before = tree_of(tools.root, home)
    wizard = setup.Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.first_install = False
    wizard.ollama()
    out = capsys.readouterr().out.splitlines()
    program = home / "opt/ollama/v0.40.4/ollama"
    log = f"{SUDO} {INSTALL} -m 600 -o {ME} -g {GROUP} /dev/null {LOG}"
    assert out == [
        f"✓ Ollama: its official build 0.40.4 answers ({program}); kept.",
        f"Ollama: its log ({LOG}) made yours and private, used from its next start.",
        "  Your Mac password next, if asked (nothing shows while you type).",
        f"  $ {log}",
    ]
    assert tools.changes == [tuple(log.split())]
    assert tree_of(tools.root, home) == before  # nothing written: not the service file, not ~/.zprofile
    assert wizard.ollama_program == str(program)


def test_an_older_install_with_its_private_log_asks_no_admin_rights(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import setup

    tools = older_mac(tmp_path, home, moved=False)
    log = tools.root / LOG.relative_to("/")
    log.parent.mkdir(parents=True)
    log.write_bytes(b"")
    log.chmod(0o600)
    monkeypatch.setattr(ollama_build, "Tools", lambda say=print: tools)
    monkeypatch.setattr(ollama_build, "owner_home", lambda: home)
    wizard = setup.Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.first_install = False
    wizard.ollama()
    program = home / "opt/ollama/v0.40.4/ollama"
    assert capsys.readouterr().out.splitlines() == [f"✓ Ollama: its official build 0.40.4 answers ({program}); kept."]
    assert tools.changes == []


def test_todays_service_file_is_pinned_byte_for_byte(home: Path) -> None:
    """daemon_plist for an older install: today's bytes, so no update ever flags or rewrites an older service file."""
    expected = plistlib.dumps(
        {
            "Label": "com.local-assistant.ollama",
            "UserName": "alex",
            "GroupName": "staff",
            "ProgramArguments": [str(home / "opt/ollama/current/ollama"), "serve"],
            "EnvironmentVariables": {
                "HOME": str(home),
                "OLLAMA_HOST": "127.0.0.1:11434",
                "OLLAMA_MODELS": str(home / ".ollama/models"),
                "OLLAMA_KEEP_ALIVE": "120m",
                "OLLAMA_MAX_LOADED_MODELS": "1",
                "OLLAMA_NUM_PARALLEL": "1",
                "OLLAMA_CONTEXT_LENGTH": "32768",
            },
            "WorkingDirectory": str(home),
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": "/var/log/estia/ollama.log",
            "StandardErrorPath": "/var/log/estia/ollama.log",
        }
    )
    assert daemon_plist(user="alex", group="staff", home=home, num_ctx=32_768) == expected
    assert prompts.OLLAMA_KEPT  # the words above are today's prompts


# ------------------------------------------------------------------ her layout: the switch, the rules, her service


HER_UID = 4242  # her account's number on the pretend Mac (the Mac the tests run on may have an _estia of its own)


def test_new_installs_get_her_own_ollama_unless_the_switch_says_older() -> None:
    """One line ships today's path for every new install (the kill switch): "older". Its gate, Metal for a LaunchDaemon
    as _estia (/api/ps: size_vram == size), was measured on 30 Oct and passed (M01, a Mac mini with macOS 27.0.1,
    Ollama 0.40.4, gemma4:12b-mlx; the record: docs/e2e-checklist.md, 111a): "hers"."""
    assert ollama_build.NEW_INSTALLS == "hers"
    assert ollama_build.HER_PORT == 8151 and ollama_build.HER_BASE == "http://127.0.0.1:8151"
    assert str(ollama_build.HER_FOLDER) == "/usr/local/estia/ollama"
    assert str(ollama_build.HER_PROGRAM) == "/usr/local/estia/ollama/current/ollama"
    assert str(ollama_build.HER_MODELS) == "/var/estia/.ollama/models"


def test_her_service_file_runs_root_s_copy_as_her_on_her_own_address() -> None:
    plist = plistlib.loads(ollama_build.her_plist(16_384))
    assert plist["Label"] == "com.local-assistant.ollama"  # one label for every layout: the same restart line
    assert (plist["UserName"], plist["GroupName"]) == ("_estia", "_estia")
    assert plist["ProgramArguments"] == ["/usr/local/estia/ollama/current/ollama", "serve"]
    assert plist["EnvironmentVariables"] == {
        "HOME": "/var/estia",
        "CFFIXED_USER_HOME": "/var/estia",
        "OLLAMA_HOST": "127.0.0.1:8151",
        "OLLAMA_MODELS": "/var/estia/.ollama/models",
        "OLLAMA_KEEP_ALIVE": "120m",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_CONTEXT_LENGTH": "16384",
        "OLLAMA_NO_CLOUD": "1",  # rule 10: no cloud model through her Ollama
    }
    assert plist["WorkingDirectory"] == "/var/estia" and plist["Umask"] == 63  # her model folder 0700, files 0600
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == "/var/log/estia/ollama.log"


def service_file(root: Path, data: bytes) -> None:
    path = root / DAEMON.relative_to("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_the_layout_is_read_from_setups_service_file_first(tmp_path: Path, home: Path) -> None:
    """Rule 1: setup's service file names _estia: hers; any other setup service file: older once her config is set up
    or she moved (whatever the config or the plan say: the first install's layout sticks). Without either it is an
    earlier try's leftover (c-2, round 3): the plan decides, and her own Ollama's service takes its place."""
    root = tmp_path / "root"
    assert ollama_build.layout_found(root) is None
    service_file(root, ollama_build.her_plist(32_768))
    assert ollama_build.layout_found(root, config_url="http://127.0.0.1:11434/v1") == "hers"
    assert ollama_build.layout_found(root) == "hers"  # hers before her config: an earlier try of her own Ollama
    service_file(root, daemon_plist(user=ME, group=GROUP, home=home, num_ctx=32_768))
    assert ollama_build.layout_found(root, config_url="http://127.0.0.1:8151/v1") == "older"
    assert ollama_build.layout_found(root, config_ollama="app", config_url="http://127.0.0.1:11434/v1") == "older"
    assert ollama_build.layout_found(root, moved=True) == "older"
    assert ollama_build.layout_found(root) is None  # an earlier try's leftover: no config, no move
    service_file(root, b"not a plist")  # one setup cannot read: today's code, which says what it finds
    assert ollama_build.layout_found(root, config_url="http://127.0.0.1:11434/v1") == "older"
    assert ollama_build.layout_found(root) is None


def test_without_a_service_file_the_config_then_the_move_then_the_plan_decide(tmp_path: Path) -> None:
    root = tmp_path / "root"
    found = ollama_build.layout_found
    assert found(root, config_ollama="app", config_url="http://127.0.0.1:11434/v1") == "app"
    assert found(root, config_ollama="build", config_url="http://127.0.0.1:8151/v1") == "hers"
    assert found(root, config_ollama="build", config_url="http://127.0.0.1:11434/v1") == "older"
    assert found(root, moved=True) == "older"  # she runs already; her config is hers alone
    assert found(root) is None
    planned = ollama_build.layout_planned
    assert planned(None, app=True, move=True) == "app" and planned(None, app=True, move=False) == "app"
    assert planned(None, app=False, move=True) == "hers"
    assert planned(None, app=False, move=False) == "older"
    assert planned("older", app=False, move=True) == "older"  # found first: it sticks


def test_the_switch_set_to_older_ships_todays_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ollama_build, "NEW_INSTALLS", "older")
    assert ollama_build.layout_planned(None, app=False, move=True) == "older"


def her_copy(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Root's copy of the official build on the pretend Mac, every folder up to its root "root's" (the test's own
    account plays root here)."""
    from local_assistant import ownuser

    monkeypatch.setattr(ownuser, "CODE_UID", os.getuid())
    folder = official_tree(root / "usr/local/estia/ollama/v0.40.4")
    (root / "usr/local/estia/ollama/current").symlink_to("v0.40.4")
    for path in (root, *[p for p in root.rglob("*") if not p.is_symlink()]):
        path.chmod(0o755 if path.is_dir() else 0o644)
    return folder / "ollama"


def test_her_own_ollama_is_hers_by_its_account_its_folder_and_roots_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one rule setup and doctor share: run by _estia, its program under /usr/local/estia/ollama, and that program
    and every folder up to / root's alone. Its account then counts as the owner's."""
    root = tmp_path / "root"
    her_copy(root, monkeypatch)
    found = ollama_build.Found("/usr/local/estia/ollama/v0.40.4/ollama", "0.40.4", "", ("127.0.0.1.8151",), HER_UID)
    assert ollama_build.hers(found, root, her_uid=HER_UID)
    assert ollama_build.owner_of(found, os.getuid(), root, her_uid=HER_UID) == HER_UID
    by_link = ollama_build.Found("/usr/local/estia/ollama/current/ollama", "0.40.4", "", ("127.0.0.1.8151",), HER_UID)
    assert ollama_build.hers(by_link, root, her_uid=HER_UID)
    owner = ollama_build.owner_of(found, 501, root, her_uid=HER_UID)
    assert ollama_build.problem(found, owner=owner, port=8151, root=root) is None


def test_the_same_program_run_by_another_account_or_changeable_by_one_is_not_hers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    program = her_copy(root, monkeypatch)
    path = "/usr/local/estia/ollama/v0.40.4/ollama"
    other = ollama_build.Found(path, "0.40.4", "", ("127.0.0.1.8151",), HER_UID + 1)
    assert not ollama_build.hers(other, root, her_uid=HER_UID)
    assert ollama_build.owner_of(other, 501, root, her_uid=HER_UID) == 501
    wrong = ollama_build.problem(other, owner=501, port=8151)
    assert wrong is not None and "another account on this Mac" in wrong[0]
    elsewhere = ollama_build.Found("/opt/ollama/ollama", "0.40.4", "", ("127.0.0.1.8151",), HER_UID)
    assert not ollama_build.hers(elsewhere, root, her_uid=HER_UID)
    program.parent.chmod(0o775)  # a folder on the way another account can change
    mine = ollama_build.Found(path, "0.40.4", "", ("127.0.0.1.8151",), HER_UID)
    assert not ollama_build.hers(mine, root, her_uid=HER_UID)


def test_her_address_is_this_mac_only_and_the_open_line_names_her_port() -> None:
    assert ollama_build.other_machines(("127.0.0.1.8151", "::1.8151"), 8151) == []
    program = "/usr/local/estia/ollama/v0.40.4/ollama"
    found = ollama_build.Found(program, "0.40.4", "", ("*.8151",), 501)
    wrong = ollama_build.problem(found, owner=501, port=8151)
    assert wrong is not None and "OLLAMA_HOST=127.0.0.1:8151" in wrong[0]
    assert ollama_build.endangers(found, 501, port=8151)
    assert not ollama_build.endangers(ollama_build.Found(program, "0.40.4", "", ("127.0.0.1.8151",), 501), 501, 8151)


def test_ollamas_answer_is_asked_on_the_port_given(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    class Opener:
        def open(self, url: str, timeout: float) -> object:
            asked.append(url)
            raise OSError("no answer")

    monkeypatch.setattr("local_assistant.ollama_build.urllib.request.build_opener", lambda *handlers: Opener())
    tools = ollama_build.Tools(Path("/"), say=lambda _line: None)
    assert tools.version(8151) is None and tools.version() is None
    assert asked == ["http://127.0.0.1:8151/api/version", "http://127.0.0.1:11434/api/version"]


# review of 30 Oct (P01): in a command tool's shell (Claude Code under herdr, not sandboxed) netstat lists no TCP
# socket at all and lsof only your own, so her Ollama, run by _estia, was "another program" on her own address
HER_RUNNING = "/usr/local/estia/ollama/v0.40.4/ollama"


def launchd_print(pid: str | None, host: str | None = "127.0.0.1:8151") -> str:
    """`launchctl print system/com.local-assistant.ollama` as a user reads it: the job's pid (one tab in) and the
    environment its service file gives it."""
    environment = f"\t\tOLLAMA_HOST => {host}\n" if host is not None else ""
    return (
        f"system/{ollama_build.LABEL} = {{\n\tactive count = 1\n\tpath = {DAEMON}\n\tstate = running\n\n"
        f"\tprogram = /usr/local/estia/ollama/current/ollama\n\tdefault environment = {{\n"
        f"\t\tPATH => /usr/bin:/bin:/usr/sbin:/sbin\n\t}}\n\n\tenvironment = {{\n\t\tHOME => /var/estia\n"
        f"{environment}\t\tOLLAMA_MODELS => /var/estia/.ollama/models\n\t}}\n\n\tusername = _estia\n"
        + (f"\tpid = {pid}\n" if pid is not None else "")
        + "\tresource coalition = {\n\t\tID = 34317\n\t\tstate = active\n\t}\n}\n"
    )


class NoSocketsHost(Host):
    """The live Mac as a command tool's shell sees it: netstat and lsof list nothing; launchctl, proc_pidpath and ps
    name any account's job and process."""

    def __init__(self, launchd: str | None, programs: dict[str, str], uids: dict[str, int]) -> None:
        super().__init__(Path("/"))
        self.launchd = launchd  # launchctl print's output; None: no such job
        self.programs = programs
        self.uids = uids
        self.asked: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        if argv[0] in ("/usr/sbin/netstat", "/usr/sbin/lsof"):
            return 0, ""
        if list(argv) == [LAUNCHCTL, "print", f"system/{ollama_build.LABEL}"]:
            if self.launchd is None:
                return 113, f'Bad request.\nCould not find service "{ollama_build.LABEL}" in domain for system\n'
            return 0, self.launchd
        if argv[0] == "/bin/ps" and argv[-1] in self.uids:
            return 0, f"  {self.uids[argv[-1]]}\n"
        return 1, ""

    def program_path(self, pid: str) -> str:
        return self.programs.get(pid, "")


def no_sockets(launchd: str | None, program: str = HER_RUNNING) -> NoSocketsHost:
    return NoSocketsHost(launchd, {"7101": program}, {"7101": HER_UID})


def test_her_ollama_is_named_by_her_service_when_no_socket_can_be_seen() -> None:
    host = no_sockets(launchd_print("7101"))
    heard = ollama_build.Tools(Path("/"), host=host).listener(8151)
    assert heard == ollama_build.Listener(HER_RUNNING, ("127.0.0.1.8151",), HER_UID)
    assert (LAUNCHCTL, "print", f"system/{ollama_build.LABEL}") in host.asked
    # where her service file says she listens is checked as a socket would be: every address names other machines
    wide = ollama_build.Tools(Path("/"), host=no_sockets(launchd_print("7101", "0.0.0.0:8151"))).listener(8151)
    assert wide.addresses == ("*.8151",) and ollama_build.other_machines(wide.addresses, 8151) == ["*.8151"]


@pytest.mark.parametrize(
    "launchd",
    [None, launchd_print(None), launchd_print("7101", "127.0.0.1:9999"), launchd_print("7101", None)],
    ids=["no-job", "not-running", "another-port", "no-address"],
)
def test_without_a_socket_nothing_but_her_running_service_on_her_address_counts(launchd: str | None) -> None:
    assert ollama_build.Tools(Path("/"), host=no_sockets(launchd)).listener(8151) == ollama_build.Listener("")


def test_without_a_socket_a_job_outside_roots_copy_or_on_another_port_is_never_named() -> None:
    elsewhere = no_sockets(launchd_print("7101"), program="/Users/alex/opt/ollama/v0.40.4/ollama")
    assert ollama_build.Tools(Path("/"), host=elsewhere).listener(8151) == ollama_build.Listener("")
    host = no_sockets(launchd_print("7101"))
    assert ollama_build.Tools(Path("/"), host=host).listener(11434) == ollama_build.Listener("")
    assert not any(argv[0] == LAUNCHCTL for argv in host.asked)  # today's 11434 is yours: lsof names it
    # only an Ollama that answered is named by its service: on her address without an answer, a socket is the proof
    quiet = no_sockets(launchd_print("7101"))
    assert ollama_build.Tools(Path("/"), host=quiet).listener(8151, by_service=False) == ollama_build.Listener("")
    assert not any(argv[0] == LAUNCHCTL for argv in quiet.asked)


def test_doctors_service_file_check_compares_hers_with_her_own(tmp_path: Path) -> None:
    root = tmp_path / "root"
    service_file(root, ollama_build.her_plist(32_768))
    assert ollama_build.service_file_changed(root, 32_768) is False
    assert ollama_build.service_file_changed(root, 16_384) is True


# ------------------------------------------------------------------ her own Ollama's install, on the pretend Mac


class Halt(BaseException):
    """The person's Stop here (or a power cut) right after an admin command finished."""


ACCOUNT = (SUDO, "/checkout/.venv/bin/python", "-I", "-m", "local_assistant.ownuser", "account")


class HerTools(FakeTools):
    """The Mac for her own Ollama: root's commands are played into the test's tree (the test's account plays root and
    _estia), her account made by the account command, the service's answers on her port in turn, and on 11434 what
    else answers. `halt_after`: Stop here after that many admin commands."""

    release: Release  # the checked build's pin, set by her_mac

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> None:
        from local_assistant import ownuser

        super().__init__(tmp_path, **kwargs)  # type: ignore[arg-type]
        monkeypatch.setattr(ownuser, "CODE_UID", os.getuid())
        self.her: int | None = None  # no _estia yet
        self.started = False  # her service bootstrapped: her Ollama answers then
        self.answers_on_8151: list[str | None] = []  # once started: her Ollama's answers in turn (default: the pin)
        self.on_8151 = ""  # a program that holds her port already (not hers)
        self.elsewhere: tuple[str | None, str] = (None, "")  # 11434's version and program
        self.free = 100_000_000_000
        self.halt_after: int | None = None
        self.played = 0
        self.unpacks = 0

    def on_disk(self, path: str) -> Path:
        return self.root / Path(path).relative_to("/")

    def her_uid(self) -> int | None:
        return self.her

    def free_bytes(self, path: Path) -> int | None:
        return self.free

    def version(self, port: int = ollama_build.PORT) -> str | None:
        if port == ollama_build.HER_PORT:
            if self.on_8151:
                return "0.40.4"
            if not self.started:
                return None
            return (
                self.answers_on_8151.pop(0)
                if len(self.answers_on_8151) > 1
                else (self.answers_on_8151 or ["0.40.4"])[0]
            )
        if self.started and self.older_program():
            return "0.40.4"  # today's service as you, started
        return self.elsewhere[0]

    def older_program(self) -> str:
        """The program today's service file (as you) runs, links resolved; "" without one."""
        service = ollama_build.read_plist(self.root / DAEMON.relative_to("/"))
        if service is None or service.get("UserName") == "_estia":
            return ""
        return os.path.realpath(str(service["ProgramArguments"][0]))

    def listener(self, port: int = ollama_build.PORT, *, by_service: bool = True) -> ollama_build.Listener:
        if port == ollama_build.HER_PORT:
            if self.on_8151:
                return ollama_build.Listener(self.on_8151, ("127.0.0.1.8151",), os.getuid() + 7)
            if not self.started:
                return ollama_build.Listener("")
            return ollama_build.Listener("/usr/local/estia/ollama/v0.40.4/ollama", ("127.0.0.1.8151",), self.her)
        if self.started and self.older_program():
            return ollama_build.Listener(self.older_program(), LOCAL, os.getuid())
        return (
            ollama_build.Listener(self.elsewhere[1], LOCAL, os.getuid())
            if self.elsewhere[1]
            else ollama_build.Listener("")
        )

    def change(self, argv: Sequence[str], *, input: bytes | None = None) -> int:
        if argv[0] != SUDO:
            return super().change(argv, input=input)
        self.changes.append(tuple(argv))
        if input is not None:
            self.inputs.append(input)
        if any(" ".join(argv).startswith(prefix) for prefix in self.fail):
            return 1
        code = self.play(list(argv[1:]), input)
        self.played += 1
        if self.halt_after is not None and self.played >= self.halt_after:
            raise Halt
        return code

    def play(self, argv: list[str], input: bytes | None) -> int:
        """Root's command, in the test's tree."""
        tool, rest = argv[0], argv[1:]
        if tuple(argv) == ACCOUNT[1:]:
            self.her = os.getuid()
            (self.root / "var/estia").mkdir(parents=True, exist_ok=True, mode=0o700)
        elif tool == INSTALL and rest[0] == "-d":
            folder = self.on_disk(rest[-1])
            folder.mkdir(parents=True, exist_ok=True)
            folder.chmod(0o755)
        elif tool == INSTALL:
            source, target = rest[-2], self.on_disk(rest[-1])
            if target.exists():
                target.unlink()
            if source == "/dev/null":
                target.write_bytes(b"")
            else:
                shutil.copyfile(source, target)
            target.chmod(0o600)
        elif tool == "/bin/rm":
            path = self.on_disk(rest[-1])
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif os.path.lexists(path):
                path.unlink()
        elif tool == "/usr/bin/shasum":
            assert rest == ["-a", "256", "-c"] and input is not None
            digest, named = input.decode().rstrip("\n").split("  ", 1)
            return 0 if hashlib.sha256(self.on_disk(named).read_bytes()).hexdigest() == digest else 1
        elif tool == "/bin/mkdir":
            assert rest[:2] == ["-m", "755"]
            self.on_disk(rest[-1]).mkdir(mode=0o755)
        elif tool == TAR:
            self.unpacks += 1
            assert rest[:2] == ["-xzf"] or rest[0] == "-xzf"
            mapped = [str(self.on_disk(word)) if word.startswith("/usr/local") else word for word in rest]
            return subprocess.run([TAR, *mapped], check=False).returncode
        elif tool == "/bin/chmod" and rest[0] == "-R":
            return subprocess.run(["/bin/chmod", "-R", rest[1], str(self.on_disk(rest[2]))], check=False).returncode
        elif tool == "/bin/mv":
            if rest[0] == "-h":
                os.replace(self.on_disk(rest[-2]), self.on_disk(rest[-1]))
            else:
                assert not os.path.lexists(self.on_disk(rest[-1])), "mv onto a name that is there"
                os.rename(self.on_disk(rest[-2]), self.on_disk(rest[-1]))
        elif tool == "/bin/ln":
            assert rest[0] == "-s"
            self.on_disk(rest[2]).symlink_to(rest[1])
        elif tool == TEE:
            path = self.on_disk(rest[-1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(input or b"")
        elif tool in ("/bin/chmod", "/usr/sbin/chown"):
            if tool == "/bin/chmod":
                self.on_disk(rest[-1]).chmod(int(rest[0], 8))
        elif tool == LAUNCHCTL:
            if rest[0] in ("bootstrap", "kickstart"):
                self.started = True
            elif rest[0] == "bootout":
                self.started = False
        else:
            raise AssertionError(f"unexpected admin command: {argv}")
        return 0


def her_install(
    tools: HerTools, home: Path, *, dry_run: bool = False, account: bool = True, app: Path | None = None, model: int = 0
) -> tuple[ollama_build.Install, list[str]]:
    said: list[str] = []
    step = ollama_build.Install(
        tools,
        home,
        dry_run=dry_run,
        say=said.append,
        release=tools.release,
        clock=iter(range(0, 10_000)).__next__,
        sleep=lambda _seconds: None,
        layout="hers",
        account=list(ACCOUNT) if account else None,
        model_bytes=model,
        name="Maya",
        app=app,
    )
    return step, said


def her_mac(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, system: bool = False, kind: type[HerTools] | None = None
) -> HerTools:
    """The pretend Mac; `system`: its tree is the suite's own system root (hostsec.estia_root), which setup reads;
    `kind`: another pretend Mac's tools (NoSocketsTools)."""
    from local_assistant import hostsec

    tools = (kind or HerTools)(tmp_path, monkeypatch)
    if system:
        tools.root = hostsec.estia_root()
    tools.tarball = tarball(tmp_path)
    tools.release = release_of(tools.tarball)
    for folder in ("usr/local", "Library/LaunchDaemons", "var/log"):
        (tools.root / folder).mkdir(parents=True, exist_ok=True)
    for place in (tools.root, tools.root / "usr", tools.root / "usr/local"):
        place.chmod(0o755)
    return tools


def end_state(tools: HerTools) -> dict[str, bytes | str]:
    return tree_of(tools.root)


HER_STEPS = [
    ACCOUNT,
    (SUDO, INSTALL, "-d", "-m", "755", "-o", "root", "-g", "wheel", "/usr/local/estia"),
    (SUDO, INSTALL, "-d", "-m", "755", "-o", "root", "-g", "wheel", "/usr/local/estia/ollama"),
    "install-tgz",
    (SUDO, "/usr/bin/shasum", "-a", "256", "-c"),
    (SUDO, "/bin/mkdir", "-m", "755", "/usr/local/estia/ollama/.unpacked"),
    (
        SUDO,
        TAR,
        "-xzf",
        "/usr/local/estia/ollama/.download.tgz",
        "--no-same-owner",
        "--no-same-permissions",
        "--no-mac-metadata",
        "-C",
        "/usr/local/estia/ollama/.unpacked",
    ),
    (SUDO, "/bin/chmod", "-R", "u=rwX,go=rX", "/usr/local/estia/ollama/.unpacked"),
    (SUDO, "/bin/rm", "-f", "/usr/local/estia/ollama/.download.tgz"),
    (SUDO, "/bin/mv", "/usr/local/estia/ollama/.unpacked", "/usr/local/estia/ollama/v0.40.4"),
    (SUDO, "/bin/ln", "-s", "v0.40.4", "/usr/local/estia/ollama/.current"),
    (SUDO, "/bin/mv", "-h", "-f", "/usr/local/estia/ollama/.current", "/usr/local/estia/ollama/current"),
    (SUDO, INSTALL, "-d", "-m", "755", "-o", "root", "-g", "wheel", "/var/log/estia"),
    (SUDO, INSTALL, "-m", "600", "-o", "_estia", "-g", "_estia", "/dev/null", "/var/log/estia/ollama.log"),
    (SUDO, TEE, str(DAEMON)),
    (SUDO, "/bin/chmod", "644", str(DAEMON)),
    (SUDO, "/usr/sbin/chown", "root:wheel", str(DAEMON)),
    (SUDO, LAUNCHCTL, "bootstrap", "system", str(DAEMON)),
]


def test_a_first_install_makes_her_account_then_her_own_ollama_as_root_copy_on_her_address(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    step, said = her_install(tools, home)
    assert step.run(32_768) == "/usr/local/estia/ollama/current/ollama"
    assert step.used == "hers" and step.base == "http://127.0.0.1:8151"
    sudo = tools.sudo()
    assert sudo[3][:8] == (SUDO, INSTALL, "-m", "600", "-o", "root", "-g", "wheel")
    assert sudo[3][-1] == "/usr/local/estia/ollama/.download.tgz"
    assert Path(sudo[3][8]).is_relative_to(home / "Library/Caches/estia-setup")  # your download, checked first
    assert [s if i != 3 else "install-tgz" for i, s in enumerate(sudo)] == HER_STEPS
    # root hashes its own copy, by exit code only: the pin and root's path on stdin
    assert f"{tools.release.sha256}  /usr/local/estia/ollama/.download.tgz\n".encode() in tools.inputs
    assert tools.inputs[-1] == ollama_build.her_plist(32_768)
    folder = tools.root / "usr/local/estia/ollama"
    assert sorted(p.name for p in folder.iterdir()) == ["current", "v0.40.4"]
    assert os.readlink(folder / "current") == "v0.40.4" and (folder / "v0.40.4" / XGRAMMAR[0]).is_file()
    assert list((home / "Library/Caches/estia-setup").iterdir()) == []  # your download is gone
    assert not (home / ".zprofile").exists() and not (home / "opt").exists()  # nothing of today's layout
    text = "\n".join(said)
    assert prompts.OLLAMA_HER_ACCOUNT_MADE.format(user="_estia") in text
    assert "✓ Ollama: her own, 0.40.4, answers, always on" in text
    # every program, library and metallib of root's copy: Apple's Developer ID chain with Ollama's team
    assert any(read[0] == SPCTL and "/usr/local/estia/ollama/.unpacked/ollama" in read[-1] for read in tools.reads)


@pytest.mark.parametrize("state", ["missing", "yours", "readable", "linked", "second-name", "hers"])
def test_her_log_is_made_hers_and_private_before_launchd_opens_it(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """M01 (30 Oct, a Mac mini with macOS 27.0.1): launchd opens and appends to her Ollama's log as her account, never
    as root; with none there in root's folder it refused to start the job (exit 78, spawn failed). So root makes it
    hers, 0600, before launchd starts her service, whenever it is missing or not her private, regular file with one
    name (yours from today's layout, readable by others, a link, a second name); her own private one is kept."""
    tools = her_mac(tmp_path, monkeypatch)
    tools.her = HER_UID if state == "yours" else os.getuid()  # "yours": the test's account owns it, hers is another
    log = tools.root / LOG.relative_to("/")
    log.parent.mkdir(parents=True, exist_ok=True)
    if state != "missing":
        log.write_bytes(b"")
        log.chmod(0o600)
    if state == "readable":
        log.chmod(0o644)
    elif state == "linked":
        log.unlink()
        log.symlink_to(tmp_path / "elsewhere")
    elif state == "second-name":
        os.link(log, tmp_path / "second-name")
    step, _said = her_install(tools, home)
    made = [[SUDO, INSTALL, "-m", "600", "-o", "_estia", "-g", "_estia", "/dev/null", "/var/log/estia/ollama.log"]]
    assert step.log_steps() == ([] if state == "hers" else made)


def test_root_s_copy_passes_doctors_code_check_as_made(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fable: deploy's and doctor's walk of /usr/local/estia (ownuser._code_check) takes her Ollama's folder as made."""
    from local_assistant import ownuser

    tools = her_mac(tmp_path, monkeypatch)
    her_install(tools, home)[0].run(32_768)
    release = tools.root / "usr/local/estia/releases/20261029-000000-abcdef"
    release.mkdir(parents=True, mode=0o755)
    (tools.root / "usr/local/estia/current").symlink_to(release)
    check = ownuser._code_check(tools.root)
    assert check.status == "ok", check


def test_a_healthy_rerun_of_her_own_ollama_asks_no_admin_rights(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    her_install(tools, home)[0].run(32_768)
    tools.changes.clear()
    step, said = her_install(tools, home)
    assert step.run(32_768) == "/usr/local/estia/ollama/current/ollama"
    assert tools.changes == []  # no box, no download
    assert said[0] == "✓ Ollama: her own, 0.40.4, answers (/usr/local/estia/ollama/v0.40.4/ollama); kept."


class NoSocketsTools(HerTools):
    """The pretend Mac seen from a command tool's shell (review of 30 Oct, P01): on her address the real listener
    runs, netstat and lsof listing nothing, launchctl naming her started service, ps her account."""

    def listener(self, port: int = ollama_build.PORT, *, by_service: bool = True) -> ollama_build.Listener:
        if port != ollama_build.HER_PORT or self.on_8151:
            return super().listener(port, by_service=by_service)
        launchd = launchd_print("7101") if self.started else None
        host = NoSocketsHost(launchd, {"7101": HER_RUNNING}, {"7101": self.her} if self.her is not None else {})
        return ollama_build.Tools(Path("/"), host=host).listener(port, by_service=by_service)


def test_her_own_ollama_is_hers_when_the_shell_lists_no_sockets(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Right after the install, and on every run after it: her Ollama answers, kept, never "another program"."""
    tools = her_mac(tmp_path, monkeypatch, kind=NoSocketsTools)
    step, said = her_install(tools, home)
    assert step.run(32_768) == "/usr/local/estia/ollama/current/ollama"
    assert prompts.OLLAMA_HER_UP.format(version="0.40.4", user="_estia") in said
    tools.changes.clear()
    again, said = her_install(tools, home)
    assert again.run(32_768) == "/usr/local/estia/ollama/current/ollama"
    assert said[0] == f"✓ Ollama: her own, 0.40.4, answers ({HER_RUNNING}); kept." and tools.changes == []


def test_her_ollama_that_never_answers_names_a_read_of_her_log_that_works(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 30 Oct (P04): her log is her account's, 0600, so `cat` gives "Permission denied"; the stop names the
    read with admin rights, by full path. Today's log is yours: its path alone, as before."""
    tools = her_mac(tmp_path, monkeypatch)
    tools.answers_on_8151 = [None]
    with pytest.raises(SystemExit, match="no answer within 60 s") as stopped:
        her_install(tools, home)[0].run(32_768)
    assert f"/usr/bin/sudo /usr/bin/tail -n 50 {LOG}" in str(stopped.value)
    assert f"/usr/bin/sudo /bin/launchctl print system/{ollama_build.LABEL}" in str(stopped.value)


def test_her_own_ollama_on_another_version_is_named_and_kept(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    her_install(tools, home)[0].run(32_768)
    tools.changes.clear()
    tools.answers_on_8151 = ["0.40.5"]
    step, said = her_install(tools, home)
    step.run(32_768)
    assert tools.sudo() == [] and any(line.startswith("! Ollama: 0.40.5 answers, not 0.40.4") for line in said)


def test_her_service_file_from_another_setup_is_written_again(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    her_install(tools, home)[0].run(32_768)
    tools.changes.clear()
    step, _said = her_install(tools, home)
    step.run(16_384)  # another context: another file
    assert [change[1:3] for change in tools.sudo()] == [
        (INSTALL, "-d"),
        (TEE, str(DAEMON)),
        ("/bin/chmod", "644"),
        ("/usr/sbin/chown", "root:wheel"),
        (LAUNCHCTL, "bootout"),
        (LAUNCHCTL, "bootstrap"),
    ]
    assert tools.inputs[-1] == ollama_build.her_plist(16_384)


@pytest.mark.parametrize("halt", range(1, len(HER_STEPS)))
def test_a_stop_after_any_admin_command_then_a_rerun_ends_as_a_clean_install(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, halt: int
) -> None:
    clean = her_mac(tmp_path / "clean", monkeypatch)
    her_install(clean, home)[0].run(32_768)
    tools = her_mac(tmp_path / "stopped", monkeypatch)
    tools.halt_after = halt
    with pytest.raises(Halt):
        her_install(tools, home)[0].run(32_768)
    tools.halt_after = None
    her_install(tools, home)[0].run(32_768)
    strip = len(str(tools.root))
    assert {k[strip:]: v for k, v in end_state(tools).items()} == {
        k[len(str(clean.root)) :]: v for k, v in end_state(clean).items()
    }


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ("fingerprint", "fingerprint is sha256:"),
        ("unsigned", "signature does not check out"),
        ("notarization", "notarization"),
        ("xgrammar", "no mlx_metal_v3/libollama_xgrammar.dylib"),
    ],
)
def test_a_copy_that_does_not_check_out_installs_nothing_and_leaves_nothing(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, change: str, why: str
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    if change == "xgrammar":
        tools.tarball = tarball(tmp_path / "other", leave_out=[XGRAMMAR[0]])
        tools.release = release_of(tools.tarball)
    if change == "fingerprint":
        tools.release = Release("0.40.4", "0" * 64)
    elif change == "unsigned":
        tools.unsigned.add("llama-server")
    elif change == "notarization":
        tools.notarized = "ollama: rejected\n"
    tools.her = os.getuid()
    with pytest.raises(SystemExit, match=why):
        her_install(tools, home)[0].run(32_768)
    folder = tools.root / "usr/local/estia/ollama"
    assert not folder.exists() or sorted(p.name for p in folder.iterdir()) == []
    assert not (tools.root / DAEMON.relative_to("/")).exists()
    assert list((home / "Library/Caches/estia-setup").iterdir()) == []


def test_roots_hash_that_differs_stops_and_removes_roots_copy(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    tools.her = os.getuid()
    tools.fail.add(f"{SUDO} /usr/bin/shasum")
    with pytest.raises(SystemExit, match="fingerprint"):
        her_install(tools, home)[0].run(32_768)
    assert sorted(p.name for p in (tools.root / "usr/local/estia/ollama").iterdir()) == []
    assert tools.unpacks == 0


def test_a_file_root_s_copy_holds_that_her_account_cannot_run_or_others_can_change_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_assistant import ownuser

    monkeypatch.setattr(ownuser, "CODE_UID", os.getuid())
    folder = official_tree(tmp_path / ".unpacked")
    for path in (folder, *folder.rglob("*")):
        path.chmod(0o755 if path.is_dir() or path.name == "ollama" else 0o644)
    assert ollama_build.root_modes(folder) == ""
    (folder / "ollama").chmod(0o700)
    assert "ollama" in ollama_build.root_modes(folder)
    (folder / "ollama").chmod(0o4755)
    assert "set-user" in ollama_build.root_modes(folder)
    (folder / "ollama").chmod(0o755)
    (folder / "mlx_metal_v4").chmod(0o775)
    assert "mlx_metal_v4" in ollama_build.root_modes(folder)
    (folder / "mlx_metal_v4").chmod(0o755)
    monkeypatch.setattr(ownuser, "CODE_UID", os.getuid() + 1)
    assert "not root's" in ollama_build.root_modes(folder)


def test_another_program_on_her_address_stops_before_any_download_or_password(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    tools.on_8151 = "/opt/homebrew/bin/some-server"
    with pytest.raises(SystemExit) as stopped:
        her_install(tools, home)[0].run(32_768)
    assert str(stopped.value) == (
        "✗ Ollama: another program holds her Ollama's address on the Mac itself (port 8151): "
        f"/opt/homebrew/bin/some-server, run by uid {os.getuid() + 7}.\n"
        "  Fix: stop it, then run scripts/setup.sh again."
    ) or "another program holds her Ollama's address" in str(stopped.value)
    assert tools.changes == []


def test_too_little_free_disk_stops_before_any_download_or_password(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    tools.free = 9_000_000_000
    with pytest.raises(SystemExit, match="9 GB free on this Mac; her Ollama and her model need about 11 GB"):
        her_install(tools, home, model=7_700_000_000)[0].run(32_768)
    assert tools.changes == []


def test_her_disk_stop_names_enough_to_free_once(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """c-6 (round 3): 8.4 GB free against 10.7 GB said "8 GB free ... need about 11 GB. Fix: free about 2 GB", and
    freeing 2 GB stopped again. Free is rounded down, the need up, and what to free is the difference shown."""
    tools = her_mac(tmp_path, monkeypatch)
    tools.free = 8_400_000_000
    with pytest.raises(SystemExit) as stopped:
        her_install(tools, home, model=7_700_000_000)[0].run(32_768)
    words = str(stopped.value)
    assert "only 8 GB free on this Mac; her Ollama and her model need about 11 GB" in words
    assert "Fix: free about 3 GB" in words


def test_a_full_disk_during_her_ollamas_download_names_the_disk_not_the_network(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """c-11 (round 3; round 1's issue 8): curl's exit 23 (it could not write: the disk is full) said "check the
    internet connection". It names the disk and how much to free."""
    tools = her_mac(tmp_path, monkeypatch)
    real = tools.change
    monkeypatch.setattr(
        tools, "change", lambda argv, input=None: 23 if argv[0] == ollama_build.CURL else real(argv, input=input)
    )
    with pytest.raises(SystemExit) as stopped:
        her_install(tools, home, model=7_700_000_000)[0].run(32_768)
    words = str(stopped.value)
    assert words.startswith("✗ Ollama: the Mac ran out of space during Ollama's download")
    assert "Fix: free about 1 GB (System Settings → General → Storage)" in words and "internet" not in words
    assert list((home / "Library/Caches/estia-setup").iterdir()) == []  # the partial download is gone


def test_her_account_not_made_stops_before_any_download(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    tools.fail.add(" ".join(ACCOUNT))
    with pytest.raises(SystemExit) as stopped:
        her_install(tools, home)[0].run(32_768)
    assert "her own macOS account (_estia) could not be made" in str(stopped.value)
    assert "scripts/setup.sh --redo move and answer No" in str(stopped.value)
    assert tools.changes == [ACCOUNT]


def test_her_account_not_made_in_the_window_says_macos_words(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review W23: in the window the account command runs captured, and its ✗ line was dropped (Terminal shows it):
    the stop now holds that line's words, as the reason."""
    tools = her_mac(tmp_path, monkeypatch)
    tools.fail.add(" ".join(ACCOUNT))
    why = (
        "macOS made no account _estia: sysadminctl -addUser ended without an error, but the account is not there "
        "(number 250; macOS 27.1; tested on macOS 27 only). sysadminctl said: (nothing)"
    )
    played = tools.change

    def change(argv: Sequence[str], *, input: bytes | None = None) -> int:
        code = played(argv, input=input)
        tools.failed_output = f"made the group\n✗ {why}\n" if tuple(argv) == ACCOUNT else ""
        return code

    monkeypatch.setattr(tools, "change", change)
    with pytest.raises(SystemExit) as stopped:
        her_install(tools, home)[0].run(32_768)
    words = str(stopped.value)
    assert words.startswith("✗ Ollama: her own macOS account (_estia) could not be made, so nothing was downloaded")
    assert why in words and "scripts/setup.sh --redo move and answer No" in words


@pytest.mark.parametrize("what", ["answering", "homebrew", "app"])
def test_any_other_ollama_on_the_mac_is_one_line_and_never_a_stop(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, what: str
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    if what == "answering":
        tools.elsewhere = ("0.30.0", "/Users/alex/bin/ollama")
    elif what == "homebrew":
        tools.elsewhere = ("0.40.4", "/opt/homebrew/Cellar/ollama/0.40.4/bin/ollama")
        (tools.root / "opt/homebrew/Cellar/ollama").mkdir(parents=True)
    else:
        (tools.root / "Applications/Ollama.app").mkdir(parents=True)
    step, said = her_install(tools, home)
    step.run(32_768)
    found = [line for line in said if line.startswith("Found ")]
    assert len(found) == 1 and found[0].endswith(": it stays as it is, and Maya gets her own Ollama beside it.")
    assert not any("osascript" in line or "Trash" in line for line in said)


def test_the_chosen_app_that_cannot_be_used_is_one_line_then_her_own(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    app = tools.root / "Applications/Ollama.app"
    (app / "Contents/Resources").mkdir(parents=True)  # no program inside it: damaged
    step, said = her_install(tools, home, app=Path("/Applications/Ollama.app"))
    assert step.run(32_768) == "/usr/local/estia/ollama/current/ollama" and step.used == "hers"
    assert any(
        line.startswith("The Ollama app cannot be used here (") and "Maya gets her own Ollama instead" in line
        for line in said
    )


def test_the_chosen_app_that_can_be_used_is_used_on_its_own_address_and_nothing_of_hers_is_installed(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app answers on 11434, never on her address: with her own Ollama planned, setup still looks for the app
    there, and uses it as today (nothing installed, no account, no root's copy)."""
    tools = her_mac(tmp_path, monkeypatch)
    program = "/Applications/Ollama.app/Contents/Resources/ollama"
    official_tree(tools.root / "Applications/Ollama.app/Contents/Resources")
    tools.elsewhere = ("0.41.0", program)
    step, said = her_install(tools, home, app=Path("/Applications/Ollama.app"))
    assert step.run(32_768) == program and step.used == "app"
    assert step.base == "http://127.0.0.1:11434"
    assert tools.changes == [] and not (tools.root / "usr/local/estia").exists() and tools.her is None
    text = "\n".join(said)
    assert prompts.OLLAMA_APP_USED.format(version="0.41.0", program=program) in text
    assert "cannot be used here" not in text and "Found " not in text and "for her alone" not in text


def test_a_dry_run_of_her_own_ollama_prints_every_step_and_runs_nothing(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = her_mac(tmp_path, monkeypatch)
    step, said = her_install(tools, home, dry_run=True)
    step.run(32_768)
    assert tools.changes == []
    text = "\n".join(said)
    assert "  $ " + " ".join(ACCOUNT) in text
    assert "/usr/bin/sudo /usr/bin/shasum -a 256 -c" in text and "/usr/bin/sudo /bin/launchctl bootstrap" in text


# ------------------------------------------------------------------ scripts/install-ollama.sh


@pytest.mark.parametrize(
    ("her_account", "service", "expect"),
    # today's service file with no config and no move: an earlier try's leftover, which hers replaces (c-2, round 3)
    [
        (True, None, "hers"),
        (False, None, "older"),
        (True, "older", "hers"),
        (False, "older", "older"),
        (True, "hers", "hers"),
    ],
)
def test_install_ollama_picks_her_own_only_when_her_account_is_there_and_never_makes_one(
    tmp_path: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    her_account: bool,
    service: str | None,
    expect: str,
) -> None:
    from local_assistant import hostsec, ownuser

    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: HER_UID if her_account and name == "_estia" else None)
    monkeypatch.setattr(ollama_build, "owner_home", lambda: home)
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 32.0)
    root = hostsec.estia_root()
    if service == "older":
        service_file(root, daemon_plist(user=ME, group=GROUP, home=home, num_ctx=32_768))
    elif service == "hers":
        service_file(root, ollama_build.her_plist(32_768))
    assert ollama_build.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "local_assistant.ownuser account" not in out  # never an account from this script
    if expect == "hers":
        assert "for her alone" in out and "/usr/local/estia/ollama" in out and "opt/ollama" not in out
    else:
        assert "for her alone" not in out and str(home / "opt/ollama") in out


@pytest.mark.parametrize("running", [True, False], ids=["app-answers", "app-quit"])
def test_install_ollama_uses_the_ollama_app_the_person_chose_and_installs_nothing(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], running: bool
) -> None:
    """Review of 30 Oct (P05): with `ollama = "app"` in the config, scripts/install-ollama.sh (after an update, or a
    refused box) took today's step: "quit it, then move it to the Trash", or a second Ollama of setup's on 11434
    while the app was quit. It uses the app, as setup does: started when it does not run, nothing installed."""
    from local_assistant import ownuser, setup

    program = "/Applications/Ollama.app/Contents/Resources/ollama"
    tools = FakeTools(tmp_path, versions=["0.41.0"] if running else [None, "0.41.0"], program=program)
    official_tree(tools.root / "Applications/Ollama.app/Contents/Resources")
    monkeypatch.setattr(ollama_build, "Tools", lambda *args, **kwargs: tools)
    monkeypatch.setattr(ollama_build, "owner_home", lambda: home)
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: None)
    monkeypatch.setattr(setup, "memory_gb", lambda: 32.0)
    monkeypatch.setattr(setup, "has_terminal", lambda: True)
    config = home / ".config/local-assistant/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('ollama = "app"\n', encoding="utf-8")
    assert ollama_build.main([]) == 0
    out = capsys.readouterr().out
    assert prompts.OLLAMA_APP_USED.format(version="0.41.0", program=program) in out
    assert "Trash" not in out and "opt/ollama" not in out
    opened = [] if running else [(ollama_build.OPEN, "-a", "/Applications/Ollama.app")]
    assert tools.changes == opened  # no download, no admin rights, no service of setup's


def test_install_ollama_after_the_move_uses_the_app_kept_in_setups_answers(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fix review (setup-macos-03): scripts/install-ollama.sh learned the app choice only from your config, which the
    move takes away (held by root), so after the move it took today's step: "move it to the Trash", or a second
    Ollama of setup's on 11434. Setup's own step reads the kept answer (`ollama_app` in setup's answers file, which
    stays in your home); so does the script, read as untrusted, when no config of yours reads."""
    from local_assistant import ownuser, setup, setup_answers

    program = "/Applications/Ollama.app/Contents/Resources/ollama"
    tools = FakeTools(tmp_path, versions=["0.41.0"], program=program)
    official_tree(tools.root / "Applications/Ollama.app/Contents/Resources")
    monkeypatch.setattr(ollama_build, "Tools", lambda *args, **kwargs: tools)
    monkeypatch.setattr(ollama_build, "owner_home", lambda: home)
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: 777)  # her account is there
    monkeypatch.setattr(ownuser, "moved", lambda root=Path("/"): True)  # moved: your config is held by root
    monkeypatch.setattr(setup, "memory_gb", lambda: 32.0)
    monkeypatch.setattr(setup, "has_terminal", lambda: True)
    answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    answers.set("ollama_app", "app")
    assert ollama_build.main([]) == 0
    out = capsys.readouterr().out
    assert prompts.OLLAMA_APP_USED.format(version="0.41.0", program=program) in out
    assert "Trash" not in out and "opt/ollama" not in out
    assert tools.changes == []  # no download, no admin rights, no service of setup's


# ------------------------------------------------------------------ setup's step 1 and her model, on her address


def setup_wizard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tools: HerTools, home: Path, **planned: object):  # type: ignore[no-untyped-def]
    from local_assistant import setup

    monkeypatch.setattr(ollama_build, "Tools", lambda say=print: tools)
    monkeypatch.setattr(ollama_build, "owner_home", lambda: home)
    fast = functools.partial(
        ollama_build.Install, release=tools.release, clock=iter(range(0, 10_000)).__next__, sleep=lambda _seconds: None
    )
    monkeypatch.setattr(ollama_build, "Install", fast)
    wizard = setup.Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.planned.update(planned)  # type: ignore[arg-type]
    return wizard


def test_setups_step_1_with_her_own_account_makes_it_then_installs_her_own_ollama(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import setup

    tools = her_mac(tmp_path, monkeypatch, system=True)
    account = (SUDO, str(setup.REPO / ".venv/bin/python"), "-I", "-m", "local_assistant.ownuser", "account")
    monkeypatch.setattr(
        tools,
        "play",
        lambda argv, input, real=tools.play: real(list(ACCOUNT[1:]) if tuple(argv) == account[1:] else argv, input),
    )
    wizard = setup_wizard(tmp_path, monkeypatch, tools, home, move=True)
    wizard.ollama()
    assert tools.sudo()[0] == account  # her account first, by the checkout's own tools, before any download
    assert wizard.ollama_base == "http://127.0.0.1:8151"
    assert wizard.ollama_program == "/usr/local/estia/ollama/current/ollama" and wizard.ollama_app is None
    assert "✓ Ollama: her own, 0.40.4, answers, always on" in capsys.readouterr().out


def test_an_earlier_trys_service_file_gives_way_to_her_own_ollama(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """c-2 (round 3): setup's service file left by an earlier try (today's layout, as you on 11434, no config, no
    move) and a yes to her own account: her own Ollama, its service file in that one's place (written, the old one
    booted out, hers started), never a stop with two commands to type and never her model on 11434."""
    from local_assistant import setup

    tools = her_mac(tmp_path, monkeypatch, system=True)
    official_tree(home / "opt/ollama/v0.40.4")
    (home / "opt/ollama/current").symlink_to(home / "opt/ollama/v0.40.4")
    service_file(tools.root, daemon_plist(user=ME, group=GROUP, home=home, num_ctx=32_768))
    tools.elsewhere = ("0.40.4", os.path.realpath(home / "opt/ollama/v0.40.4/ollama"))  # it answers on 11434
    account = (SUDO, str(setup.REPO / ".venv/bin/python"), "-I", "-m", "local_assistant.ownuser", "account")
    monkeypatch.setattr(
        tools,
        "play",
        lambda argv, input, real=tools.play: real(list(ACCOUNT[1:]) if tuple(argv) == account[1:] else argv, input),
    )
    wizard = setup_wizard(tmp_path, monkeypatch, tools, home, move=True)
    wizard.ollama()
    assert wizard.ollama_base == "http://127.0.0.1:8151"
    assert (tools.root / DAEMON.relative_to("/")).read_bytes() == ollama_build.her_plist(32_768)
    order = [change[1:3] for change in tools.sudo() if change[1] == LAUNCHCTL]
    assert order == [(LAUNCHCTL, "bootout"), (LAUNCHCTL, "bootstrap")]
    out = capsys.readouterr().out
    assert prompts.OLLAMA_HER_REPLACES.format(daemon=DAEMON) in out
    assert "it stays as it is" not in out  # the earlier try's Ollama does not stay: its service is hers now


@pytest.mark.parametrize("configured", [False, True])
def test_setups_step_1_without_her_own_account_names_an_earlier_trys_service_as_such(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    """c-2 (round 3): with no config and no move, setup's service file is an earlier try's (`leftover`), which goes
    beside the app chosen; an install set up keeps its own, and its stop."""
    tools = her_mac(tmp_path, monkeypatch, system=True)
    service_file(tools.root, daemon_plist(user=ME, group=GROUP, home=home, num_ctx=32_768))
    made: list[dict[str, object]] = []

    class Seen(ollama_build.Install):
        def __init__(self, *args: object, **kwargs: object) -> None:
            made.append(kwargs)
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]

        def run(self, num_ctx: int) -> str:
            return "ollama"

    wizard = setup_wizard(tmp_path, monkeypatch, tools, home, move=False)
    monkeypatch.setattr(ollama_build, "Install", Seen)
    if configured:
        wizard.config_ollama = ("build", "http://127.0.0.1:11434/v1")
    wizard.ollama()
    assert made[0]["leftover"] is (not configured) and made[0].get("layout") is None


def test_setups_step_1_without_her_own_account_is_todays(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tools = her_mac(tmp_path, monkeypatch, system=True)
    wizard = setup_wizard(tmp_path, monkeypatch, tools, home, move=False)
    wizard.ollama()
    assert not any("ownuser" in " ".join(change) for change in tools.changes)
    assert wizard.ollama_base == "http://127.0.0.1:11434"
    assert wizard.ollama_program == str(home / "opt/ollama/current/ollama")


def test_the_kill_switch_gives_a_new_install_todays_step_1(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ollama_build, "NEW_INSTALLS", "older")
    tools = her_mac(tmp_path, monkeypatch, system=True)
    wizard = setup_wizard(tmp_path, monkeypatch, tools, home, move=True)
    wizard.ollama()
    assert wizard.ollama_base == "http://127.0.0.1:11434" and not (tools.root / "usr/local/estia").exists()


def test_once_she_moved_setup_never_runs_the_checkout_as_root_for_her_account(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_assistant import hostsec

    tools = her_mac(tmp_path, monkeypatch, system=True)
    tools.started, tools.her = True, os.getuid()
    service_file(tools.root, ollama_build.her_plist(32_768))
    bridge = hostsec.estia_root() / "Library/LaunchDaemons/com.local-assistant.bridge.plist"
    bridge.parent.mkdir(parents=True, exist_ok=True)
    bridge.write_bytes(plistlib.dumps({"Label": "com.local-assistant.bridge", "UserName": "_estia"}))
    wizard = setup_wizard(tmp_path, monkeypatch, tools, home)
    wizard.first_install = False
    official_tree(tools.root / "usr/local/estia/ollama/v0.40.4")
    (tools.root / "usr/local/estia/ollama/current").symlink_to("v0.40.4")
    (tools.root / "var/log/estia").mkdir(parents=True)  # made by the move
    wizard.ollama()
    assert not any("ownuser" in " ".join(change) for change in tools.changes)
    assert wizard.ollama_base == "http://127.0.0.1:8151"


def test_her_model_is_pulled_listed_and_made_on_her_address(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import doctor, setup, setup_progress

    choice = setup.choose_model(32.0)
    assert choice is not None
    asked: list[str] = []

    def tags(self: object, url: str) -> object:
        asked.append(url)
        return {"models": [{"name": choice.tag, "digest": f"sha256:{choice.digest}"}]}

    monkeypatch.setattr(doctor.Probe, "get_json", tags)
    pulled: list[str] = []
    monkeypatch.setattr(setup_progress, "pull", lambda base, tag, watch, total=0: noted(pulled, base, ""))
    ran: list[list[str]] = []
    monkeypatch.setattr(
        setup.Wizard,
        "run",
        lambda self, command, **kwargs: noted(ran, command, subprocess.CompletedProcess(command, 0)),
    )
    wizard = setup.Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.ollama_base = ollama_build.HER_BASE
    wizard.ollama_program = str(ollama_build.HER_PROGRAM)
    wizard.model()
    assert pulled == ["http://127.0.0.1:8151"]
    assert asked and all(url.startswith("http://127.0.0.1:8151/") for url in asked)
    assert ran == [
        [
            "/usr/bin/env",
            "OLLAMA_HOST=127.0.0.1:8151",
            "/usr/local/estia/ollama/current/ollama",
            "create",
            "local-worker",
            "-f",
            str(setup.REPO / ".local-worker.Modelfile"),
        ]
    ]
    assert "  Ollama's own pull, http://127.0.0.1:8151/api/pull" in capsys.readouterr().out


def test_the_card_is_not_asked_once_her_own_ollama_is_set_up_and_redo_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_assistant import hostsec
    from local_assistant.setup_plan import Mac, question

    root = hostsec.estia_root()
    (root / "Applications/Ollama.app").mkdir(parents=True)
    assert question("ollama_app").shows({}, Mac.read(memory_gb=32.0))  # type: ignore[arg-type]
    service_file(root, ollama_build.her_plist(32_768))
    mac = Mac.read(memory_gb=32.0)
    assert mac.her_ollama and not question("ollama_app").shows({}, mac)  # type: ignore[arg-type]
    assert prompts.SETUP_OLLAMA_APP_REDO_HERS == (
        "Her own Ollama stays as it is: this setup does not switch it to the Ollama app."
    )


def test_the_cards_second_option_says_the_app_stays_only_with_her_own_account() -> None:
    from local_assistant.setup_plan import question

    build = next(option for option in question("ollama_app").options if option.id == "build")
    assert build.line == prompts.SETUP_OLLAMA_APP_BUILD_LINE_HERS
    assert "(asked later) the app stays; without it" in build.line


def test_a_first_install_that_gets_her_own_ollama_asks_the_smaller_model_in_the_plan() -> None:
    """c-7 (round 3): the Ollama app lists local-worker (an earlier try) and the install gets her own Ollama, whose
    store is empty: the smaller model is asked in the plan (it came mid-steps, at Step 2), unless the app is chosen,
    whose own store has it; an install whose Ollama is found keeps today's rule."""
    from local_assistant.setup_plan import Mac, question

    shows = question("smaller_model").shows
    app = "/Applications/Ollama.app"
    fresh = Mac(memory_gb=16.0, model_listed=True, ollama_app=app, fresh_hers=True)
    assert shows({}, fresh)  # type: ignore[arg-type]
    assert shows({"ollama_app": "build"}, fresh)  # type: ignore[arg-type]
    assert not shows({"ollama_app": "app"}, fresh)  # type: ignore[arg-type]
    assert not shows({}, Mac(memory_gb=16.0, model_listed=True))  # type: ignore[arg-type]
    assert shows({}, Mac(memory_gb=16.0))  # type: ignore[arg-type]


def test_the_plans_disk_check_counts_her_model_for_her_own_ollama_whatever_11434_lists(tmp_path: Path) -> None:
    from local_assistant import setup
    from local_assistant.setup_plan import Mac

    wizard = setup.Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.fixed_mac = Mac(memory_gb=16.0, model_listed=True, fresh_hers=True)
    wizard.planned.update({"move": True})
    need, what = setup.disk_needed(wizard)
    assert what.startswith("her model 7.7 GB, her Ollama 3 GB") and round(need, 1) == 10.7
    wizard.planned.update({"move": False})  # today's Ollama on 11434, which lists her model: not counted
    assert setup.disk_needed(wizard)[1] == "Ollama 1 GB"


def test_terminals_list_of_the_cards_says_what_the_windows_cards_say() -> None:
    """c-1 (round 3): plain lines, gum's fallback and the dry run print the cards' listing; it says each option's own
    line, so the friend's AI never briefs "the app must be moved to the Trash" while the window says it stays."""
    from local_assistant.setup_plan import question

    q = question("ollama_app")
    for option in q.options:
        assert option.label in q.listing
        assert option.line.replace("\n", "\n     ") in q.listing, option.id
    assert "The Ollama app must then be quit" not in q.listing


# ------------------------------------------------------------------ her config


def test_her_config_names_her_own_address_and_an_older_one_keeps_11434(tmp_path: Path) -> None:
    from local_assistant import config, setup
    from tests.setup_helpers import ACI, BOT, EXAMPLE

    hers = setup.render_config(EXAMPLE, BOT, ACI, ollama_url="http://127.0.0.1:8151/v1")
    assert 'ollama_base_url = "http://127.0.0.1:8151/v1"' in hers and "11434" not in hers.split("[", 1)[0]
    older = setup.render_config(EXAMPLE, BOT, ACI)
    assert 'ollama_base_url = "http://127.0.0.1:11434/v1"' in older
    with pytest.raises(ValueError):
        setup.render_config(EXAMPLE, BOT, ACI, ollama_url="http://example.com:8151/v1")
    path = tmp_path / "config.toml"
    path.write_text(hers, encoding="utf-8")
    path.chmod(0o600)
    assert config.load_config(path).ollama_base_url == "http://127.0.0.1:8151/v1"
    assert config.Config.__dataclass_fields__["ollama_base_url"].default == "http://127.0.0.1:11434/v1"


def test_setups_config_step_writes_the_address_its_ollama_step_chose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_assistant import setup
    from tests.setup_helpers import ACI, BOT

    written: list[str] = []
    wizard = setup.Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "model_digest", lambda: "")
    monkeypatch.setattr(wizard, "shown", lambda name: None)
    monkeypatch.setattr(wizard, "language", lambda: "en")
    monkeypatch.setattr(wizard, "her_style", lambda: "hearth")
    monkeypatch.setattr(wizard, "projects", lambda: None)
    monkeypatch.setattr(wizard, "_write_private", lambda path, text: written.append(text))
    wizard.ollama_base = ollama_build.HER_BASE
    wizard.config(BOT, ACI)
    assert 'ollama_base_url = "http://127.0.0.1:8151/v1"' in written[0]

"""Her own user (ownuser): the layout, the role account's plan, the plist values, doctor's install checks and the
migration, all against a temporary root (ESTIA_ROOT) with a fake runner: no user is created, nothing runs as root."""

import json
import os
import plistlib
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from local_assistant import hostsec, ownuser
from local_assistant.hostsec import Check
from local_assistant.ownuser import CODE, HOME, ROLE_GROUP, ROLE_USER

needs_plutil = pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")
ROOT = Path(__file__).resolve().parent.parent


def test_the_layout_is_fixed() -> None:
    assert ("_estia", "_estia", Path("/var/estia"), Path("/usr/local/estia")) == (ROLE_USER, ROLE_GROUP, HOME, CODE)
    assert ownuser.RELEASES == CODE / "releases" and ownuser.CURRENT == CODE / "current"
    assert ownuser.BIN == CODE / "bin"
    assert [old for old, _ in ownuser.DATA_MAP] == [
        ".config/local-assistant",
        "Library/Application Support/local-assistant",
        ".local/share/signal-cli",
        "Library/Logs/local-assistant",
        "Library/Caches/voice-service",
        ".cache/fluidaudio",  # FluidAudio's speech models (Kokoro, Supertonic): offline after the move too
    ]
    assert all(old == new for old, new in ownuser.DATA_MAP)  # the same place under her home


def test_a_free_id_in_the_role_account_range() -> None:
    assert ownuser.free_id(set(), set()) == 499
    assert ownuser.free_id({499, 498}, {497}) == 496  # free as a user and as a group
    with pytest.raises(RuntimeError, match="no free") as raised:
        ownuser.free_id(set(range(450, 500)), set())
    # sysadminctl ignores -UID (macOS 27, live: uid 503): only her group surely gets the number
    assert "her group" in str(raised.value) and "user and group" not in str(raised.value)


def test_the_ids_in_use_are_read_from_the_directory_listing() -> None:
    listing = "_www                     70\nalex                     501\n_oahd                    441\n"
    assert ownuser.ids_in(listing) == {70, 501, 441}


def test_the_account_plan_is_a_hidden_role_account_with_its_own_group_and_no_login() -> None:
    plan = ownuser.account_plan(499)
    joined = [" ".join(argv) for argv in plan]
    assert joined[0] == "/usr/sbin/dseditgroup -o create -i 499 -r Estia _estia"
    add = next(argv for argv in plan if "-addUser" in argv)
    assert add[0] == "/usr/sbin/sysadminctl" and "-roleAccount" in add
    assert add[add.index("-UID") + 1] == "499" and add[add.index("-GID") + 1] == "499"
    assert add[add.index("-shell") + 1] == "/usr/bin/false" and add[add.index("-home") + 1] == "/var/estia"
    assert "-password" not in add and "-admin" not in add
    assert not any("staff" in line for line in joined)
    record = "/Users/" + ROLE_USER  # the directory-service record, not a home folder
    settings = [" ".join(argv) for argv in ownuser.account_settings()]
    assert f"/usr/bin/dscl . -create {record} IsHidden 1" in settings
    assert f"/usr/bin/dscl . -create {record} UserShell /usr/bin/false" in settings
    assert f"/usr/bin/dscl . -delete {record} AuthenticationAuthority" in settings  # no shadow hash to log in with
    assert f"/usr/bin/dscl . -delete {record} ShadowHashData" in settings
    # live 13 Oct (macOS 27): the record keeps /var/empty as her home and root cannot change it (eDSPermissionError):
    # her services get the home explicitly instead (HOME, CFFIXED_USER_HOME, signal-cli --config, -Duser.home)
    assert not any("NFSHomeDirectory" in line for line in settings)
    assert settings[-1] == f"/usr/bin/dscl . -create {record} Password *"  # no password can log in
    assert not any("dseditgroup" in line for line in map(" ".join, ownuser.account_plan(499, group=False)))


def test_the_plist_values_are_her_user_and_the_root_owned_code() -> None:
    values = ownuser.plist_values()
    assert values == {
        "RUN_USER": "_estia",
        "RUN_GROUP": "_estia",
        "RUN_HOME": "/var/estia",
        "CODE": "/usr/local/estia/current",
        "LOG_DIR": "/var/log/estia",  # her services' output: a root-owned folder she cannot change
    }  # no Homebrew: its folders are yours, and what she runs comes from the release


def test_render_fills_placeholders_and_refuses_unknown_ones() -> None:
    text = ownuser.render("<string>@RUN_USER@</string> @CODE@", {"RUN_USER": "_estia", "CODE": "/a&b"}, plist=True)
    assert text == "<string>_estia</string> /a&amp;b"
    with pytest.raises(ValueError, match="@NOPE@"):
        ownuser.render("@NOPE@", {}, plist=True)
    with pytest.raises(ValueError, match="empty"):
        ownuser.render("@CODE@", {"CODE": ""}, plist=True)


@needs_plutil
@pytest.mark.parametrize("label", ["bridge", "voice"])
def test_the_plists_render_with_her_values(tmp_path: Path, label: str) -> None:
    template = ROOT / "launchd" / f"com.local-assistant.{label}.plist.in"
    out = tmp_path / f"{label}.plist"
    ownuser.render_file(template, out, ownuser.plist_values())
    plist = plistlib.loads(out.read_bytes())
    assert (plist["UserName"], plist["GroupName"]) == ("_estia", "_estia")
    assert plist["EnvironmentVariables"]["HOME"] == "/var/estia"
    assert plist["EnvironmentVariables"]["CFFIXED_USER_HOME"] == "/var/estia"  # Swift and Foundation read this home
    assert plist["ProgramArguments"][0].startswith("/usr/local/estia/current/")
    assert plist["WorkingDirectory"].startswith("/usr/local/estia/current")
    log = "/var/log/estia/" + {"bridge": "launchd.log", "voice": "voice.log"}[label]
    assert (
        plist["StandardOutPath"] == plist["StandardErrorPath"] == log
    )  # in root's folder, never hers: no link can be planted at its name (launchd opens it as her)
    assert plist["Umask"] == 63
    if label == "bridge":
        assert (
            plist["EnvironmentVariables"]["PATH"] == "/usr/local/estia/current/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        )
    assert ownuser.plist_problem(plist, f"com.local-assistant.{label}") is None
    assert subprocess.run(["plutil", "-lint", str(out)], capture_output=True, check=False).returncode == 0
    assert (out.stat().st_mode & 0o777) == 0o644


# ------------------------------------------------------------------ doctor's install checks


def installed(root: Path, *, user: str = ROLE_USER, program: str = "/usr/local/estia/current/.venv/bin/x") -> Path:
    """A finished own-user install under `root` (files owned by whoever runs the test)."""
    release = root / "usr/local/estia/releases/20261013T120000Z-abc123"
    (release / "src").mkdir(parents=True)
    (release / "src" / "x.py").write_text("x")
    (root / "usr/local/estia/current").symlink_to("releases/20261013T120000Z-abc123")
    (root / "var/estia").mkdir(parents=True)
    (root / "var/estia").chmod(0o700)
    daemons = root / "Library/LaunchDaemons"
    daemons.mkdir(parents=True)
    plist = {
        "Label": "com.local-assistant.bridge",
        "UserName": user,
        "GroupName": user,
        "ProgramArguments": [program],
        "EnvironmentVariables": {"HOME": "/var/estia", "CFFIXED_USER_HOME": "/var/estia"},
        "Umask": 63,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
    }
    (daemons / "com.local-assistant.bridge.plist").write_bytes(plistlib.dumps(plist))
    (daemons / "com.local-assistant.bridge.plist").chmod(0o644)
    return release


@pytest.fixture
def as_her(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test user stands in for root (the code's owner) and for _estia (her home's owner)."""
    me = os.getuid()
    monkeypatch.setattr(ownuser, "CODE_UID", me)
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: me if name == ROLE_USER else None)


def by_key(checks: Sequence[Check]) -> dict[str, Check]:
    return {check.key: check for check in checks}


def test_no_install_at_all_gives_no_line(tmp_path: Path) -> None:
    assert ownuser.install_checks(tmp_path) == []


def test_a_daemon_that_runs_as_the_owner_is_a_warning_with_the_migration(tmp_path: Path) -> None:
    daemons = tmp_path / "Library/LaunchDaemons"
    daemons.mkdir(parents=True)
    plist = {"Label": "x", "UserName": "alex", "ProgramArguments": ["/Users/alex/estia/.venv/bin/local-assistant"]}
    (daemons / "com.local-assistant.bridge.plist").write_bytes(plistlib.dumps(plist))
    checks = ownuser.install_checks(tmp_path)
    assert [(c.key, c.status) for c in checks] == [("own_user", "warn")]
    assert "scripts/migrate-to-own-user.sh --dry-run" in checks[0].fix


def test_a_finished_install_is_ok(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path)
    found = by_key(ownuser.install_checks(tmp_path))
    assert {key: check.status for key, check in found.items()} == {"own_user": "ok", "code": "ok", "plists": "ok"}


def root_tools(root: Path, release: Path, python: str = "3.13.9") -> Path:
    """The root-run tools as the first release installed them: its tool files and a Python of their own."""
    from local_assistant.deploy import TOOL_FILES

    tools = root / "usr/local/estia/tools"
    for folder in (release, tools):
        for rel in TOOL_FILES:
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(f"# {rel}\n")
        interp = folder / f"python/cpython-{python}-macos-aarch64-none/bin/python3.13"
        interp.parent.mkdir(parents=True, exist_ok=True)
        interp.write_text("#!/bin/sh\n")
    (tools / "tools.json").write_text(json.dumps({"release": release.name}))
    return tools


def test_root_run_tools_that_are_not_the_live_releases_are_named(tmp_path: Path, as_her: None) -> None:
    """A release that changed a root-run file or their Python says so once, at its deploy: doctor names it until
    --update-tools takes it, so root never runs a stale copy unseen (review I22)."""
    release = installed(tmp_path)
    tools = root_tools(tmp_path, release)
    assert "tools" not in by_key(ownuser.install_checks(tmp_path))  # the same: no line
    (release / "scripts/estia").write_text("# changed\n")
    old = release / "python/cpython-3.13.9-macos-aarch64-none"
    old.rename(release / "python/cpython-3.13.12-macos-aarch64-none")
    check = by_key(ownuser.install_checks(tmp_path))["tools"]
    assert check.status == "warn" and check.part == "Root-run tools"
    assert "scripts/estia" in check.text and "Python 3.13.9 → 3.13.12" in check.text
    assert check.fix.startswith("/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools")
    assert (tools / "scripts/estia").read_text() == "# scripts/estia\n"  # read only


@pytest.mark.parametrize("where", ["release", "tools"])
def test_a_python_that_runs_a_file_at_every_start_is_named(tmp_path: Path, as_her: None, where: str) -> None:
    """A .pth file in the live release's Python of the same version (your account builds it) never reaches the tools,
    and doctor names it, as it names one in the tools' own Python, which root runs (release review D1)."""
    release = installed(tmp_path)
    tools = root_tools(tmp_path, release)
    site = (release if where == "release" else tools) / "python/cpython-3.13.9-macos-aarch64-none/lib/python3.13"
    (site / "site-packages").mkdir(parents=True)
    (site / "site-packages/planted.pth").write_text("import os\n")
    check = by_key(ownuser.install_checks(tmp_path))["tools"]
    assert check.status == "fail" and check.part == "Root-run tools", check
    assert "site-packages/planted.pth" in check.text and "every start" in check.text
    whose = "the live release's Python" if where == "release" else "their own Python"
    assert whose in check.text, check.text


def test_code_the_owner_can_change_is_a_failure(tmp_path: Path, as_her: None) -> None:
    release = installed(tmp_path)
    (release / "src" / "x.py").chmod(0o666)
    check = by_key(ownuser.install_checks(tmp_path))["code"]
    assert check.status == "fail" and "estia-deploy" in check.fix


def test_code_owned_by_another_account_is_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installed(tmp_path)
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: os.getuid())
    monkeypatch.setattr(ownuser, "CODE_UID", 0)  # the files belong to the test user, not root
    assert by_key(ownuser.install_checks(tmp_path))["code"].status == "fail"


def test_a_plist_that_still_runs_from_the_checkout_is_a_failure(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path, program="/Users/alex/estia/.venv/bin/local-assistant")
    check = by_key(ownuser.install_checks(tmp_path))["plists"]
    assert check.status == "fail" and "bridge" in check.text


def test_a_missing_role_user_is_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installed(tmp_path)
    monkeypatch.setattr(ownuser, "CODE_UID", os.getuid())
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: None)
    assert by_key(ownuser.install_checks(tmp_path))["own_user"].status == "fail"


@pytest.mark.parametrize(("days", "status", "word"), [(2, "ok", "estia rollback"), (9, "warn", "estia remove-old")])
def test_the_rollback_window(tmp_path: Path, as_her: None, days: int, status: str, word: str) -> None:
    installed(tmp_path)
    marker = {"owner": "alex", "moved_at": time.time() - days * 86400, "folders": ["/x"], "removed": False}
    (tmp_path / "usr/local/estia/migration.json").write_text(json.dumps(marker))
    check = by_key(ownuser.install_checks(tmp_path))["rollback"]
    assert check.status == status and word in check.text + check.fix
    marker["removed"] = True
    (tmp_path / "usr/local/estia/migration.json").write_text(json.dumps(marker))
    assert "rollback" not in by_key(ownuser.install_checks(tmp_path))


@pytest.mark.parametrize(
    ("marker", "rollback"),
    [
        ({"fresh": False, "moved_at_days": 2}, True),  # her old folders held, within the 7 days
        ({"fresh": True, "moved_at_days": 2}, False),  # a new install: nothing was held
        ({"fresh": False, "moved_at_days": 9}, False),  # the window is over
        ({"fresh": False, "moved_at_days": 2, "removed": True}, False),  # remove-old ran
        (None, False),  # no record at all
    ],
)
def test_a_missing_home_offers_the_rollback_only_when_one_is_held(
    tmp_path: Path, as_her: None, marker: dict[str, Any] | None, rollback: bool
) -> None:
    installed(tmp_path)
    (tmp_path / "var/estia").rmdir()
    if marker is not None:
        record = {"owner": "alex", "moved_at": time.time() - marker.pop("moved_at_days") * 86400, **marker}
        (tmp_path / "usr/local/estia/migration.json").write_text(json.dumps(record))
    check = by_key(ownuser.install_checks(tmp_path))["own_user"]
    assert check.status == "fail" and "/var/estia is missing" in check.text
    assert ("estia rollback" in check.fix) is rollback, check.fix
    if not rollback:
        assert "no copy of her data is held" in check.fix and "backup" in check.fix


def test_her_home_must_be_hers_alone(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path)
    (tmp_path / "var/estia").chmod(0o755)
    check = by_key(ownuser.install_checks(tmp_path))["own_user"]
    assert check.status == "fail" and "/var/estia" in check.text


def test_the_checks_read_only(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path)
    before = sorted((str(p), p.lstat().st_mode, p.lstat().st_mtime_ns) for p in tmp_path.rglob("*"))
    ownuser.install_checks(tmp_path)
    assert sorted((str(p), p.lstat().st_mode, p.lstat().st_mtime_ns) for p in tmp_path.rglob("*")) == before
    assert stat.S_IMODE((tmp_path / "var/estia").stat().st_mode) == 0o700


# ------------------------------------------------------------------ the migration


class RecordingHost(ownuser.Host):
    """The real Host under a temporary root (system tools refused and printed), with every argv recorded."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, say=lambda text: None)
        self.asked: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        return super().run(argv, **kwargs)  # type: ignore[arg-type]

    def pipe(self, first: Sequence[str], second: Sequence[str], **kwargs: object) -> tuple[int, str]:
        self.asked.append((*first, "|", *second))
        return super().pipe(first, second, **kwargs)  # type: ignore[arg-type]


OLD_PLIST = b"<plist><dict><key>UserName</key><string>alex</string></dict></plist>"
NUMBER = "+306900000000"


def her_old_setup(home: Path, root: Path, *, services: bool = True) -> None:
    """The owner's install as it is today: her folders in the owner's home, both services, the newsyslog rule."""
    files = {
        ".config/local-assistant/config.toml": (
            f'bot_number = "{NUMBER}"\n[calls]\nsignal_cli = "~/dev/vendor/signal-cli-0.14.9-jvm/x/bin/signal-cli"\n'
            '[projects]\nroot = "~/dev"\n',
            0o600,
        ),
        ".config/local-assistant/web.env": ("BRAVE_API_KEY=abc\n", 0o600),
        "Library/Application Support/local-assistant/assistant.db": ("db", 0o600),
        "Library/Application Support/local-assistant/vendor/big.bin": ("x", 0o755),
        ".local/share/signal-cli/data/accounts.json": (
            json.dumps({"accounts": [{"path": "111111", "number": NUMBER}], "version": 2}),
            0o644,
        ),
        ".local/share/signal-cli/data/111111": (json.dumps({"registered": True, "number": NUMBER}), 0o600),
        ".local/share/signal-cli/data/111111.d/account.db": ("signal", 0o644),  # signal-cli's own default
        "Library/Logs/local-assistant/bridge.log": ("log", 0o600),
        "Library/Caches/voice-service/cache.bin": ("ane", 0o644),
        ".cache/fluidaudio/Models/kokoro/model.bin": ("tts", 0o644),
    }
    for name, (text, mode) in files.items():
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
    daemons = root / "Library/LaunchDaemons"
    daemons.mkdir(parents=True, exist_ok=True)
    if services:
        for label in ownuser.LABELS:
            (daemons / f"{label}.plist").write_bytes(OLD_PLIST)
        (root / "etc/newsyslog.d").mkdir(parents=True)
        (root / "etc/newsyslog.d/local-assistant.conf").write_text("old rule\n")


def a_checkout(tmp_path: Path) -> Path:
    """What the move reads from the checkout: the service templates and the voice service's build."""
    repo = tmp_path / "checkout"
    (repo / "launchd").mkdir(parents=True)
    for template in (ROOT / "launchd").glob("*.plist.in"):
        shutil.copy(template, repo / "launchd" / template.name)
    voice = repo / "tools/voice/.build/release/voice-service"
    voice.parent.mkdir(parents=True)
    voice.write_text("binary")
    return repo


def fake_deploy(root: Path, result: int = 0, seen: list[str] | None = None) -> ownuser.DeployStep:
    def run() -> int:
        if seen is not None:
            seen.append("deploy")
        if result:
            return result
        release = root / "usr/local/estia/releases/r1"
        (release / "launchd").mkdir(parents=True, exist_ok=True)
        for template in (ROOT / "launchd").glob("*.plist.in"):
            shutil.copy(template, release / "launchd" / template.name)
        jvm = release / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"
        jvm.parent.mkdir(parents=True, exist_ok=True)
        jvm.write_text("#!/bin/sh\n")
        current = root / "usr/local/estia/current"
        if not current.is_symlink():
            current.symlink_to("releases/r1")
        return result

    return run


def migration(
    tmp_path: Path,
    *,
    mode: str = "real",
    result: int = 0,
    said: list[str] | None = None,
    seen: list[str] | None = None,
) -> tuple[ownuser.Migration, RecordingHost, Path, Path]:
    home, root = tmp_path / "home", tmp_path / "root"
    host = RecordingHost(root)
    owner = ownuser.Owner("alex", home, os.getuid())
    checkout = tmp_path / "checkout"
    if not checkout.exists():
        a_checkout(tmp_path)
    move = ownuser.Migration(
        host,
        owner,
        checkout=checkout,
        mode=mode,
        deploy=fake_deploy(root, result, seen),
        say=(said.append if said is not None else lambda text: None),
    )
    return move, host, home, root


def modes(folder: Path) -> dict[str, int]:
    return {str(p.relative_to(folder)): stat.S_IMODE(p.lstat().st_mode) for p in folder.rglob("*")}


SECRET_FOLDERS = (
    ".config/local-assistant",
    "Library/Application Support/local-assistant",
    ".local/share/signal-cli",
    "Library/Logs/local-assistant",
)


@pytest.fixture
def no_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: None)  # no _estia yet


@needs_plutil
def test_the_migration_moves_her_data_into_her_home_with_private_modes(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    assert stat.S_IMODE(her.stat().st_mode) == 0o700
    copied = modes(her)
    assert copied[".local/share/signal-cli/data/111111.d/account.db"] == 0o600
    assert copied[".config/local-assistant/web.env"] == 0o600
    assert copied["Library/Caches/voice-service/cache.bin"] == 0o600  # the voice service's compile cache
    assert (her / ".cache/fluidaudio/Models/kokoro/model.bin").read_text() == "tts"  # its speech models
    assert all(mode & 0o077 == 0 for mode in copied.values())
    assert not (her / "Library/Application Support/local-assistant/vendor").exists()  # code goes into the release
    held = root / "usr/local/estia/rollback/data"
    assert stat.S_IMODE(held.stat().st_mode) == 0o700  # root only: your programs cannot reach them
    for old in SECRET_FOLDERS:
        assert not (home / old).exists(), old  # moved out of your home, by rename: nothing re-owned
    assert (home / "Library/Caches/voice-service/cache.bin").exists()  # caches hold no secret: left
    plist = plistlib.loads((root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").read_bytes())
    assert plist["UserName"] == "_estia" and plist["ProgramArguments"][0].startswith("/usr/local/estia/current/")
    assert (root / "Library/LaunchDaemons/com.local-assistant.voice.plist").exists()
    assert plist["StandardOutPath"] == "/var/log/estia/launchd.log"
    assert (root / "var/log/estia/launchd.log").is_file() and (root / "var/log/estia/voice.log").is_file()
    assert not (root / "etc/newsyslog.d/local-assistant.conf").exists()
    assert (root / "usr/local/estia/rollback/com.local-assistant.bridge.plist").read_bytes() == OLD_PLIST
    marker = json.loads((root / "usr/local/estia/migration.json").read_text())
    assert marker["owner"] == "alex" and marker["fresh"] is False
    sources = {source: Path(target) for source, target in marker["held"]}
    config_held = sources[str(home / ".config/local-assistant")]
    assert config_held.parent == held and (config_held / "config.toml").exists()
    tools = [Path(argv[0]).name for argv in host.asked]
    assert tools.index("launchctl") < tools.index("sysadminctl")  # stopped before anything moves
    assert ("/usr/sbin/chown", "_estia:_estia", str(her)) in host.asked
    copy = next(argv for argv in host.asked if "|" in argv)
    assert copy[copy.index("|") + 1 :][:2] == ("/usr/bin/tar", "-x")  # read as you, written as her: no root copy
    assert (
        ownuser.LAUNCHCTL,
        "bootstrap",
        "system",
        "/Library/LaunchDaemons/com.local-assistant.bridge.plist",
    ) in host.asked
    text = (her / ".config/local-assistant/config.toml").read_text()
    assert '# signal_cli = "~/dev/vendor' in text and "\nsignal_cli" not in text  # the release's vendor/ is used
    assert "signal_cli = " in (config_held / "config.toml").read_text()  # the original is untouched
    assert any("projects.root" in line for line in said)  # a path that now means her home is named
    assert "abc" not in "\n".join(said)  # no value is printed
    assert any("before the move" in line and "SECURITY.md" in line for line in said)


def test_the_release_is_built_before_she_is_stopped(tmp_path: Path, no_user: None) -> None:
    seen: list[str] = []
    move, _host, home, root = migration(tmp_path, seen=seen)
    her_old_setup(home, root)
    real = move.host.run

    def run(argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        if argv[:2] == [ownuser.LAUNCHCTL, "bootout"]:
            seen.append("stop")
        return real(argv, **kwargs)  # type: ignore[arg-type]

    move.host.run = run  # type: ignore[method-assign]
    assert move.run() == 0
    assert seen[:2] == ["deploy", "stop"]


def test_a_failed_build_changes_nothing(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, host, home, root = migration(tmp_path, result=1, said=said)
    her_old_setup(home, root)
    assert move.run() == 1
    assert not any(argv[0] == ownuser.LAUNCHCTL for argv in host.asked)  # she was never stopped
    assert (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").read_bytes() == OLD_PLIST
    assert (home / ".config/local-assistant/config.toml").exists()
    assert not (root / "usr/local/estia/migration.json").exists()
    assert any("Nothing changed" in line for line in said)


@pytest.mark.parametrize("missing", ["config", "account", "verified"])
def test_the_preflight_stops_before_anything_changes(tmp_path: Path, no_user: None, missing: str) -> None:
    said: list[str] = []
    seen: list[str] = []
    move, host, home, root = migration(tmp_path, said=said, seen=seen)
    her_old_setup(home, root)
    signal = home / ".local/share/signal-cli/data"
    if missing == "config":
        (home / ".config/local-assistant/config.toml").unlink()
    elif missing == "account":
        (signal / "accounts.json").unlink()
    else:  # signal-cli lists a number as soon as its registration starts: the code from Signal never entered
        (signal / "111111").write_text(json.dumps({"registered": False, "number": NUMBER}))
    assert move.run() == 1
    assert seen == [] and host.asked == []  # nothing built, nothing run
    text = "\n".join(said)
    assert "Nothing changed" in text
    # her config set up, her Signal account gone: setup skips its Signal step there, so the fix names the way that
    # works (c-5, round 3): her config renamed, then setup sets her Signal account up again
    gone = "mv ~/.config/local-assistant ~/.config/local-assistant.old"
    assert {"config": "scripts/setup.sh", "account": gone, "verified": gone}[missing] in text
    assert "scripts/setup.sh registers it" not in text


def test_a_number_counts_as_her_account_only_once_verified(tmp_path: Path) -> None:
    signal = tmp_path / "signal-cli"
    data = signal / "data"
    data.mkdir(parents=True)
    assert not ownuser.has_account(signal, NUMBER)  # nothing registered here
    (data / "accounts.json").write_text(json.dumps({"accounts": [{"path": "111111", "number": NUMBER}]}))
    assert not ownuser.has_account(signal, NUMBER)  # listed, no account file
    (data / "111111").write_text(json.dumps({"number": NUMBER}))
    assert not ownuser.has_account(signal, NUMBER)  # registration started, never verified
    (data / "111111").write_text(json.dumps({"registered": False, "number": NUMBER}))
    assert not ownuser.has_account(signal, NUMBER)
    (data / "111111").write_text(json.dumps({"registered": "true", "number": NUMBER}))
    assert not ownuser.has_account(signal, NUMBER)  # only signal-cli's own true
    (data / "111111").write_text(json.dumps({"registered": True, "number": NUMBER}))
    assert ownuser.has_account(signal, NUMBER)
    assert not ownuser.has_account(signal, "+306900000001")  # another number
    assert not ownuser.has_account(signal, "")  # the move names her number: never "the only one there"
    (data / "accounts.json").write_text(json.dumps({"accounts": [{"path": "../111111", "number": NUMBER}]}))
    assert not ownuser.has_account(signal, NUMBER)  # a path out of signal-cli's folder is never read


def test_the_move_needs_no_voice_build_in_the_checkout(tmp_path: Path, no_user: None) -> None:
    """Her release builds the voice service from the commit's own sources (estia-deploy): the checkout's build,
    which may be older or from another branch, is neither needed nor used."""
    said: list[str] = []
    seen: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said, seen=seen)
    her_old_setup(home, root)
    shutil.rmtree(tmp_path / "checkout/tools/voice")
    assert move.preflight(list(ownuser.LABELS)) == []


@needs_plutil
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, RuntimeError])
def test_an_interruption_after_the_stop_brings_her_old_setup_back(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch, interruption: type[BaseException]
) -> None:
    """A Ctrl-C, or a hang-up from a dropped SSH session, never leaves her stopped."""
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)

    def broken() -> None:
        raise interruption("gone")

    monkeypatch.setattr(move, "_install_plists", lambda voice: broken())
    assert move.run() == 1
    assert (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").read_bytes() == OLD_PLIST
    for old in SECRET_FOLDERS:
        assert (home / old).is_dir(), old  # back in your home
    assert stat.S_IMODE((home / ".config/local-assistant/config.toml").stat().st_mode) == 0o600  # as it was
    assert (
        "/bin/launchctl",
        "bootstrap",
        "system",
        "/Library/LaunchDaemons/com.local-assistant.bridge.plist",
    ) in host.asked
    assert any("Her old setup comes back" in line for line in said)


def test_a_hang_up_becomes_an_interruption() -> None:
    import signal

    with pytest.raises(KeyboardInterrupt), ownuser.interruptions_raise():
        os.kill(os.getpid(), signal.SIGHUP)
        time.sleep(1)
    assert signal.getsignal(signal.SIGHUP) is not None


@needs_plutil
def test_a_fresh_install_that_fails_says_she_does_not_run_yet(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root, services=False)
    monkeypatch.setattr(move, "_install_plists", lambda voice: (_ for _ in ()).throw(RuntimeError("no plist")))
    assert move.run() == 1
    text = "\n".join(said)
    assert "Nothing was installed: she does not run yet" in text and "comes back" not in text
    assert (home / ".config/local-assistant/config.toml").exists()


def failing_start(move: ownuser.Migration) -> None:
    """Her services start, then the bridge exits (a hand-edited config): the move fails after its service files."""

    def start(labels: list[str]) -> None:
        move.started.extend(labels)
        raise RuntimeError("the bridge did not stay up")

    move._start = start  # type: ignore[method-assign]


@needs_plutil
def test_a_fresh_install_that_fails_after_its_service_files_leaves_none(tmp_path: Path, no_user: None) -> None:
    """Nothing was installed means no service file either: one left behind would say she moved (the scripts, a
    re-run of the move) and launchd would start her at the next boot from a half-made copy."""
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root, services=False)
    failing_start(move)
    assert move.run() == 1
    assert list((root / "Library/LaunchDaemons").iterdir()) == []
    assert ("/bin/launchctl", "bootout", "system/com.local-assistant.bridge") in host.asked
    assert not ownuser.moved(root)
    assert any("Nothing was installed: she does not run yet" in line for line in said)


@needs_plutil
def test_a_service_file_of_hers_left_by_an_earlier_try_is_not_her_old_setup(tmp_path: Path, no_user: None) -> None:
    """A try before this fix left her service file (runs as _estia, no move on record): it is neither kept as her
    old setup nor left when this try fails."""
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root, services=False)
    daemons = root / "Library/LaunchDaemons"
    left = daemons / "com.local-assistant.bridge.plist"
    left.write_bytes(plistlib.dumps({"Label": "com.local-assistant.bridge", "UserName": ROLE_USER}))
    failing_start(move)
    assert move.run() == 1
    assert not left.exists() and not (root / "usr/local/estia/rollback/com.local-assistant.bridge.plist").exists()
    assert any("Nothing was installed" in line for line in said) and not any("comes back" in line for line in said)


@needs_plutil
def test_a_fresh_install_keeps_no_old_copy(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root, services=False)
    assert move.run() == 0
    assert not (root / "usr/local/estia/rollback/data").exists()  # her only copy is in /var/estia
    done = next(line for line in said if line.startswith("✓ She runs as"))
    assert "no rollback" in done and "estia rollback" not in "\n".join(said)  # said, never offered
    marker = json.loads((root / "usr/local/estia/migration.json").read_text())
    assert marker["fresh"] is True
    assert migration(tmp_path, mode="rollback")[0].rollback() == 1  # no earlier setup to go back to


@needs_plutil
def test_a_second_run_changes_nothing(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    before = modes(root)
    again, host2, _, _ = migration(tmp_path)
    assert again.run() == 0
    assert modes(root) == before and not any("sysadminctl" in argv[0] for argv in host2.asked)


@needs_plutil
def test_the_rollback_brings_back_her_old_setup_with_what_she_saved_since(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    (her / ".local/share/signal-cli/data/111111.d/account.db").write_text("ratchet moved on")  # newer sessions
    (her / "Library/Application Support/local-assistant/assistant.db").write_text("a week of messages")
    (her / ".config/local-assistant/config.toml").write_text("edited as her\n")
    assert ownuser.moved(root)
    back, host2, _, _ = migration(tmp_path, mode="rollback")
    assert back.rollback() == 0
    for label in ownuser.LABELS:
        assert (root / f"Library/LaunchDaemons/{label}.plist").read_bytes() == OLD_PLIST
    assert (root / "etc/newsyslog.d/local-assistant.conf").read_text() == "old rule\n"
    assert (home / ".local/share/signal-cli/data/111111.d/account.db").read_text() == "ratchet moved on"
    assert (home / "Library/Application Support/local-assistant/assistant.db").read_text() == "a week of messages"
    assert (home / "Library/Application Support/local-assistant/vendor/big.bin").exists()  # your old folder is back
    assert "signal_cli" in (home / ".config/local-assistant/config.toml").read_text()  # your config, not hers
    assert os.access(home / ".config/local-assistant/config.toml", os.W_OK)
    assert stat.S_IMODE((home / ".local/share/signal-cli/data/111111.d/account.db").stat().st_mode) == 0o600
    assert json.loads((root / "usr/local/estia/migration.json").read_text())["rolled_back"] is True
    assert (ownuser.LAUNCHCTL, "bootout", "system/com.local-assistant.bridge") in host2.asked
    assert not ownuser.moved(root)  # the scripts take the owner's path again
    assert migration(tmp_path, mode="rollback")[0].rollback() == 1  # once only


@needs_plutil
def test_the_rollback_removes_a_service_of_hers_added_after_the_move(tmp_path: Path, no_user: None) -> None:
    """A move without the voice service, then the voice service installed as her (estia-deploy --voice): the
    rollback stopped it but left its file, so it started as _estia again at the next boot (release review D7)."""
    said: list[str] = []
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    daemons = root / "Library/LaunchDaemons"
    (daemons / f"{ownuser.VOICE}.plist").unlink()  # your setup had no voice service
    assert move.run() == 0
    voice = daemons / f"{ownuser.VOICE}.plist"
    voice.write_bytes(plistlib.dumps({"Label": ownuser.VOICE, "UserName": ROLE_USER}))  # added since, as her
    back, host2, _, _ = migration(tmp_path, mode="rollback", said=said)
    assert back.rollback() == 0
    assert (daemons / f"{ownuser.BRIDGE}.plist").read_bytes() == OLD_PLIST
    assert not voice.exists(), "her voice service would start as _estia at the next boot"
    assert (ownuser.LAUNCHCTL, "bootout", f"system/{ownuser.VOICE}") in host2.asked
    assert not any(argv[:2] == (ownuser.LAUNCHCTL, "bootstrap") and ownuser.VOICE in argv[-1] for argv in host2.asked)
    assert any(ownuser.VOICE in line and "removed" in line for line in said), said


@needs_plutil
def test_the_rollback_never_copies_a_program_of_hers_into_your_folders(tmp_path: Path, no_user: None) -> None:
    """Her vendor/ folder is code, never data (the move leaves it out too): a launcher planted there by her account
    would run as you, from your data folder, after the rollback."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    planted = (
        "Library/Application Support/local-assistant/vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"
    )
    (her / planted).parent.mkdir(parents=True)
    (her / planted).write_text("#!/bin/sh\nplanted\n")
    (her / planted).chmod(0o755)
    (her / "Library/Application Support/local-assistant/assistant.db").write_text("a week of messages")
    assert migration(tmp_path, mode="rollback")[0].rollback() == 0
    assert not (home / planted).exists()
    assert (home / "Library/Application Support/local-assistant/vendor/big.bin").exists()  # your own, from before
    assert (home / "Library/Application Support/local-assistant/assistant.db").read_text() == "a week of messages"


@needs_plutil
@pytest.mark.parametrize("planted", ["program", "link"])
def test_what_her_account_adds_after_the_check_never_reaches_your_folders(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch, planted: str
) -> None:
    """The rollback checked her newer data, then copied it by name: a program or a link a process of hers still
    running added in between reached your folders. What arrives is checked again before any of it is yours, and the
    rollback stops (release review D4)."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    (her / ".local/share/signal-cli/data/111111.d/account.db").write_text("ratchet moved on")
    said: list[str] = []
    back, _host2, _, _ = migration(tmp_path, mode="rollback", said=said)
    journals = back._stale_journals
    late = her / ".local/share/signal-cli/data/late"

    def plant_late(present: Sequence[str], owner: ownuser.Owner) -> list[Path]:
        if planted == "program":  # after the check, before the copy: a process of hers that still runs
            late.write_text("#!/bin/sh\n")
            late.chmod(0o755)
        else:
            late.symlink_to(home / ".ssh")
        return journals(present, owner)

    monkeypatch.setattr(back, "_stale_journals", plant_late)
    assert back.rollback() == 1
    text = "\n".join(said)
    assert not os.path.lexists(home / ".local/share/signal-cli/data/late"), text
    assert (home / ".local/share/signal-cli/data/111111.d/account.db").read_text() == "signal"  # nothing copied
    assert "data/late" in text and f"She runs as {ROLE_USER} again" in text, text
    assert not [p for p in home.iterdir() if p.name.startswith(".estia-rollback")]  # its own copy removed
    assert ownuser.moved(root)


def plant(her: Path, home: Path, planted: str) -> Path:
    """What her data never holds, put in her config folder: a program, a link out of her home, a link that stays in
    her home but leaves its folder (copied back into yours, it leads to your file of that name: review I37) or a
    FIFO."""
    odd = her / ".config/local-assistant/hook"
    if planted == "program":
        odd.write_text("#!/bin/sh\n")
        odd.chmod(0o700)
    elif planted == "link":
        odd.symlink_to(home / ".ssh")
    elif planted == "link in her home":
        odd.symlink_to("../../.ssh/authorized_keys")
    else:
        os.mkfifo(odd)
    return odd


@needs_plutil
@pytest.mark.parametrize("planted", ["program", "link", "link in her home", "fifo"])
def test_the_rollback_refuses_her_newer_data_with_a_program_or_a_way_out_and_changes_nothing(
    tmp_path: Path, no_user: None, planted: str
) -> None:
    """Her data holds no program, no link out of her home and no FIFO: one found there means her account was used to
    plant it (or a program of yours put it in your folder before the move). Your old services on your old Signal
    sessions alone would break decryption, so the rollback refuses before anything stops: she keeps running as
    _estia, your folders stay held, and the lines name the file and its fix, run as her (review 15 Oct, 1)."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    (her / "Library/Application Support/local-assistant/assistant.db").write_text("a week of messages")
    plant(her, home, planted)
    said: list[str] = []
    back, host2, _, _ = migration(tmp_path, mode="rollback", said=said)
    assert back.rollback() == 1
    assert not any(argv[0] in (ownuser.LAUNCHCTL, hostsec.TAR) for argv in host2.asked)  # nothing stopped or copied
    for old in SECRET_FOLDERS:
        assert not os.path.lexists(home / old), old  # still held
    assert json.loads((root / "usr/local/estia/migration.json").read_text())["rolled_back"] is False
    assert ownuser.moved(root)
    text = "\n".join(said)
    shown = "/var/estia/.config/local-assistant/hook"
    fix = "/bin/chmod a-x" if planted == "program" else "/bin/rm"
    assert f"/usr/bin/sudo -u _estia {fix} -- {shown}" in text, text
    assert "Nothing changed" in text and "/usr/bin/sudo /usr/local/estia/bin/estia rollback" in text
    assert not any(line.startswith("✓") for line in said)


@needs_plutil
@pytest.mark.parametrize("step", ["journals", "copy"])
def test_a_rollback_whose_copy_back_fails_starts_her_again_never_your_old_sessions_alone(
    tmp_path: Path, no_user: None, step: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy back is what keeps your old services from running on your old Signal sessions alone: when it fails,
    nothing says she runs as you, nothing is noted, and her own services start again (review 15 Oct, 1)."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    (her / ".local/share/signal-cli/data/111111.d/account.db").write_text("ratchet moved on")
    said: list[str] = []
    back, host2, _, _ = migration(tmp_path, mode="rollback", said=said)
    if step == "journals":
        monkeypatch.setattr(back, "_stale_journals", lambda present, owner: [home / "x.db-wal"])
        run = host2.run

        def refused(argv: Sequence[str], **kwargs: Any) -> tuple[int, str]:
            return (1, "rm: Operation not permitted") if argv[0] == hostsec.RM else run(argv, **kwargs)

        monkeypatch.setattr(host2, "run", refused)
    else:
        monkeypatch.setattr(host2, "pipe", lambda first, second, **kwargs: (1, "tar: No space left on device"))
    assert back.rollback() == 1
    text = "\n".join(said)
    assert not any(line.startswith("✓") for line in said), text
    assert "✗ " in text and f"She runs as {ROLE_USER} again" in text
    assert json.loads((root / "usr/local/estia/migration.json").read_text())["rolled_back"] is False
    for label in ownuser.LABELS:
        plist = plistlib.loads((root / f"Library/LaunchDaemons/{label}.plist").read_bytes())
        assert plist["UserName"] == ROLE_USER  # hers, never your old one put back
        assert (ownuser.LAUNCHCTL, "bootstrap", "system", f"/Library/LaunchDaemons/{label}.plist") in host2.asked
    assert ownuser.moved(root)
    assert (her / ".local/share/signal-cli/data/111111.d/account.db").read_text() == "ratchet moved on"


@needs_plutil
@pytest.mark.parametrize("planted", ["program", "link", "link in her home", "fifo"])
def test_a_failed_move_after_she_ran_defuses_what_her_data_never_holds_then_copies_it_back(
    tmp_path: Path, no_user: None, planted: str
) -> None:
    """A move that fails after her bridge ran: what she saved meanwhile (Signal sessions) must come back with your old
    services. A program, a link or a FIFO in her data is made harmless as her (the execute bit off, the rest
    deleted) and named, then the copy goes on: never your old sessions alone, never her program in your folders."""
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    her = root / "var/estia"
    start = move._start

    def started_then_failed(labels: list[str]) -> None:
        start(labels)
        (her / ".local/share/signal-cli/data/111111.d/account.db").write_text("ratchet moved on")
        plant(her, home, planted)
        raise RuntimeError("the bridge did not stay up")

    move._start = started_then_failed  # type: ignore[method-assign]
    assert move.run() == 1
    assert (home / ".local/share/signal-cli/data/111111.d/account.db").read_text() == "ratchet moved on"
    hook = home / ".config/local-assistant/hook"
    if planted == "program":
        assert hook.is_file() and not hook.stat().st_mode & 0o111
    else:
        assert not os.path.lexists(hook)
    assert any("/var/estia/.config/local-assistant/hook" in line and line.startswith("!") for line in said), said
    for label in ownuser.LABELS:
        assert (root / f"Library/LaunchDaemons/{label}.plist").read_bytes() == OLD_PLIST


class LogsHost(RecordingHost):
    """Her two logs, as `tail` read as her prints them (one with a terminal control code someone else chose)."""

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        if list(argv[:4]) == [hostsec.SUDO, "-n", "-u", "_estia"] and "/usr/bin/tail" in argv:
            self.asked.append(tuple(argv))
            if argv[-1] == "/var/log/estia/launchd.log":
                return 0, 'Traceback (most recent call last):\nlocal_assistant.config.ConfigError: [calls] "x"\x1b[2J\n'
            if argv[-1] == "/var/estia/Library/Logs/local-assistant/bridge.log":
                return 0, (
                    "2026-10-28 10:00:01 INFO local_assistant.app: starting\n"
                    "2026-10-28 10:00:02 ERROR local_assistant.signal_io: signal-cli ended (exit 1)\n"
                    "2026-10-28 10:00:03 INFO local_assistant.app: waiting\n"
                )
        return super().run(argv, **kwargs)


def test_a_failed_move_after_she_ran_shows_her_logs_before_her_old_setup_comes_back(
    tmp_path: Path, no_user: None
) -> None:
    """Review host-move-logs: only the symptom was printed, and the restore takes her services away, so `estia logs`
    refuses after. The cause is in her logs: the last lines of launchd.log and bridge.log's last errors, read as her
    (what her account can read, nothing more), printable characters only, before her old setup comes back."""
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    host = LogsHost(root)
    move.host = host
    her_old_setup(home, root)
    start = move._start

    def started_then_failed(labels: list[str]) -> None:
        start(labels)
        raise RuntimeError("her bridge did not come up within 120 s")

    move._start = started_then_failed  # type: ignore[method-assign]
    assert move.run() == 1
    reads = [
        argv for argv in host.asked if argv[:4] == (hostsec.SUDO, "-n", "-u", "_estia") and "/usr/bin/tail" in argv
    ]
    assert all(argv[4:9] == ("-H", "--", "/usr/bin/env", "-i", "HOME=/var/estia") for argv in reads)
    assert [argv[-1] for argv in reads] == [
        "/var/log/estia/launchd.log",
        "/var/estia/Library/Logs/local-assistant/bridge.log",
    ]
    text = "\n".join(said)
    assert "\x1b" not in text and 'local_assistant.config.ConfigError: [calls] "x"?[2J' in text
    assert "ERROR local_assistant.signal_io: signal-cli ended (exit 1)" in text and "INFO" not in text
    failed = next(i for i, line in enumerate(said) if "Her old setup comes back" in line)
    assert said[failed + 1].startswith("Her logs, read as her before her old setup comes back"), said[failed:]


@pytest.mark.parametrize("cut", ["ctrl-c", "not utf-8"])
def test_a_failure_while_her_logs_are_read_still_brings_her_old_setup_back(
    tmp_path: Path, no_user: None, cut: str
) -> None:
    """Second deep scan, macos-move-logs-skip-restore: her logs were read before the restore and outside its guard, so
    a second Ctrl-C (or an SSH drop's two hang-ups, or a log byte that is not UTF-8) there ended the script with a
    traceback: her new services stayed loaded and her old folders held. The logs are read inside the restore's
    guard, and anything that goes wrong there is left out."""
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    move.host = LogsHost(root)
    her_old_setup(home, root)
    start = move._start

    def started_then_failed(labels: list[str]) -> None:
        start(labels)
        raise RuntimeError("her bridge did not come up within 120 s")

    def logs_cut() -> None:
        if cut == "ctrl-c":  # a second Ctrl-C, as the terminal sends it: raised here unless ignored
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.05)
        else:
            raise ValueError("'utf-8' codec can't decode byte 0xff in position 3")

    move._start = started_then_failed  # type: ignore[method-assign]
    move._say_her_logs = logs_cut  # type: ignore[method-assign]
    try:
        assert move.run() == 1
    except KeyboardInterrupt:
        pytest.fail("a Ctrl-C while her logs were read ended the move before her old setup came back")
    for label in ownuser.LABELS:
        assert (root / f"Library/LaunchDaemons/{label}.plist").read_bytes() == OLD_PLIST


def test_a_move_that_fails_before_she_ran_reads_no_log(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    host = LogsHost(root)
    move.host = host
    her_old_setup(home, root)

    def broken(voice: bool) -> None:
        raise RuntimeError("gone")

    move._install_plists = broken  # type: ignore[method-assign]
    assert move.run() == 1
    assert not any("/usr/bin/tail" in argv for argv in host.asked) and not any("Her logs" in line for line in said)


def power_cut(move: ownuser.Migration, step: str) -> None:
    """A hard stop (a power cut, a kernel panic, a SIGKILL) at `step`: no restore runs, nothing after it either."""

    def stop(*_args: object) -> None:
        raise KeyboardInterrupt("power cut")

    setattr(move, step, stop)
    move._restore = lambda labels: None  # type: ignore[method-assign]


@needs_plutil
@pytest.mark.parametrize("fresh", [False, True], ids=["older install", "first install"])
def test_a_move_cut_off_after_the_hold_keeps_her_old_folders_on_record(
    tmp_path: Path, no_user: None, fresh: bool
) -> None:
    """The record is written before each folder is held, not only at the end: after a hard stop doctor names the
    held copy, remove-old deletes it, and an older install's rollback brings it back (review I18)."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root, services=not fresh)
    power_cut(move, "_start")
    assert move.run() == 1
    marker = ownuser.read_marker(root)
    assert marker is not None and marker["moving"] is True and marker["fresh"] is fresh
    held = [Path(target) for _source, target in marker["held"]]
    assert len(held) == len(SECRET_FOLDERS) and all(path.is_dir() for path in held)
    check = ownuser._rollback_check(root, time.time())
    assert check is not None and "the move did not finish" in check.text
    if fresh:
        said: list[str] = []
        assert migration(tmp_path, mode="rollback", said=said)[0].rollback() == 1
        assert any("a first install has no rollback" in line for line in said), said
        assert migration(tmp_path, mode="remove-old")[0].remove_old() == 0
        assert not any(path.exists() for path in held)
    else:
        assert migration(tmp_path, mode="rollback")[0].rollback() == 0
        assert (home / "Library/Application Support/local-assistant/assistant.db").is_file()


@needs_plutil
def test_a_move_cut_off_before_her_service_files_gives_your_folders_back_on_the_next_run(
    tmp_path: Path, no_user: None
) -> None:
    """Cut between the hold and her service files: your old service files are still in place and your folders held.
    The next run gives them back from the record first (it said "set her up first" before), then moves her."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    power_cut(move, "_install_plists")
    assert move.run() == 1
    assert not (home / ".config/local-assistant/config.toml").exists() and not ownuser.moved(root)
    said: list[str] = []
    again = migration(tmp_path, said=said)[0]
    assert again.run() == 0, said
    assert any("did not finish" in line and "back in your home" in line for line in said), said
    marker = ownuser.read_marker(root)
    assert marker is not None and not marker.get("moving") and ownuser.moved(root)


@needs_plutil
def test_a_move_cut_off_between_its_record_and_a_hold_counts_only_the_folders_it_held(
    tmp_path: Path, no_user: None
) -> None:
    """The record names the folder about to be held before the rename: a stop between the two leaves that folder in
    your home, and the next run counts only the folders it gives back."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    journal = move._journal
    calls: list[int] = []

    def then_stop(voice: bool, fresh: bool, held: Sequence[tuple[str, str]]) -> None:
        journal(voice, fresh, held)
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt("power cut")

    move._journal = then_stop  # type: ignore[method-assign]
    move._restore = lambda labels: None  # type: ignore[method-assign]
    assert move.run() == 1
    marker = ownuser.read_marker(root)
    assert marker is not None and len(marker["held"]) == 2
    said: list[str] = []
    assert migration(tmp_path, said=said)[0].run() == 0, said
    assert any("did not finish" in line and "(1 from" in line for line in said), said


@needs_plutil
@pytest.mark.parametrize("before", [None, {"owner": "alex", "moved_at": 1.0, "rolled_back": True}])
def test_a_failed_move_that_gives_your_folders_back_leaves_the_record_as_it_was(
    tmp_path: Path, no_user: None, before: dict[str, object] | None
) -> None:
    """The record written before each hold goes with the hold: a failed move whose restore gave every folder back
    leaves the record from before it (none, or a rolled back move's), so doctor names no held copy and the next run
    gives nothing back (review I18)."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    path = root / "usr/local/estia/migration.json"
    if before is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(before))
    failing_start(move)
    assert move.run() == 1
    assert (home / ".config/local-assistant/config.toml").is_file()
    assert ownuser.read_marker(root) == before
    assert ownuser._rollback_check(root, time.time()) is None
    said: list[str] = []
    assert migration(tmp_path, said=said)[0].run() == 0, said
    assert not any("did not finish" in line for line in said), said


@needs_plutil
def test_a_failed_move_whose_restore_leaves_a_folder_held_keeps_it_on_record(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restore that could not give a folder back (it prints the commands) keeps that folder on record: doctor names
    it and remove-old reaches it."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    failing_start(move)
    unhold = move._unhold

    def first_only(held: list[tuple[str, str]]) -> None:
        unhold(held[:1])
        raise OSError("a folder on the way is a link")

    monkeypatch.setattr(move, "_unhold", first_only)
    assert move.run() == 1
    marker = ownuser.read_marker(root)
    assert marker is not None and marker["moving"] is True
    still = [Path(target) for _source, target in marker["held"]]
    assert len(still) == len(SECRET_FOLDERS) - 1 and all(path.is_dir() for path in still)
    check = ownuser._rollback_check(root, time.time())
    assert check is not None and "the move did not finish" in check.text


@needs_plutil
def test_no_rollback_after_the_window(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    marker = root / "usr/local/estia/migration.json"
    data = json.loads(marker.read_text())
    data["moved_at"] -= 8 * 86400
    marker.write_text(json.dumps(data))
    assert migration(tmp_path, mode="rollback")[0].rollback() == 1


@needs_plutil
def test_a_move_after_a_rollback_keeps_her_earlier_copy_aside(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Her home is hers: her account may have planted links there (.config to your ~/.config, .before-<time> to /).
    Her earlier copy is kept aside by her own mkdir and mv, never by root following a path she controls."""
    said: list[str] = []
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    assert migration(tmp_path, mode="rollback")[0].rollback() == 0
    her = root / "var/estia"
    real_rename, real_mkdir = os.rename, Path.mkdir

    def rename(source: object, target: object, *args: object, **kwargs: object) -> None:
        assert not str(source).startswith(str(her)) and not str(target).startswith(str(her)), (source, target)
        real_rename(source, target, *args, **kwargs)  # type: ignore[arg-type]

    def mkdir(self: Path, *args: object, **kwargs: object) -> None:
        assert self == her or not str(self).startswith(f"{her}/"), self  # root makes only her home itself
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    again, host, _, _ = migration(tmp_path, said=said)
    assert again.run() == 0
    kept = [p for p in her.iterdir() if p.name.startswith(".before-")]
    assert kept and (kept[0] / ".local/share/signal-cli").is_dir()  # never deleted
    assert ("/bin/mkdir", "-p", str(kept[0] / ".local/share")) in host.asked
    assert ("/bin/mv", str(her / ".local/share/signal-cli"), str(kept[0] / ".local/share/signal-cli")) in host.asked
    assert any("kept her earlier copy" in line for line in said)


@needs_plutil
def test_her_chmod_walks_only_the_folders_just_copied(tmp_path: Path, no_user: None) -> None:
    """Root makes the folders that hold an earlier copy (live 13 Oct): her chmod over her whole home stopped on them.
    It names only the folders just copied, the ones doctor checks."""
    move, host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    chmods = [argv for argv in host.asked if hostsec.CHMOD in argv and "go-rwx" in argv]
    assert chmods and all(str(her) not in argv for argv in chmods)
    assert {str(her / new) for _old, new in ownuser.DATA_MAP} <= {arg for argv in chmods for arg in argv}


def test_a_dry_run_after_an_earlier_try_needs_nothing_of_hers(tmp_path: Path, no_user: None) -> None:
    """After a first try her home is hers alone (0700, live 13 Oct): the dry run, run as you, stopped on it."""
    home, root = tmp_path / "home", tmp_path / "root"
    her_old_setup(home, root)
    her = root / "var/estia"
    (her / ".config/local-assistant").mkdir(parents=True)
    said: list[str] = []
    move = ownuser.Migration(
        ownuser.Host(root, dry_run=True, say=said.append),
        ownuser.Owner("alex", home, os.getuid()),
        checkout=a_checkout(tmp_path),
        mode="dry",
        deploy=lambda: 0,
        say=said.append,
    )
    her.chmod(0)
    try:
        result = move.run()
    finally:
        her.chmod(0o700)
    text = "\n".join(said)
    assert result == 0, text
    assert "earlier copy" in text and "Nothing changed (a dry run)" in text


@needs_plutil
def test_remove_old_deletes_the_held_copies(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    assert migration(tmp_path, mode="remove-old")[0].remove_old() == 0
    assert not (root / "usr/local/estia/rollback/data").exists()
    assert json.loads((root / "usr/local/estia/migration.json").read_text())["removed"] is True


def test_a_rehearsal_leaves_her_folders_alone(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path, mode="rehearse")
    her_old_setup(home, root)
    before = modes(home)
    assert move.run() == 0
    assert modes(home) == before  # untouched
    assert (root / "var/estia/.config/local-assistant/config.toml").exists()
    assert (root / "Library/LaunchDaemons/com.local-assistant.voice.plist").read_bytes() != OLD_PLIST


def test_a_dry_run_writes_nothing(tmp_path: Path, no_user: None) -> None:
    home, root = tmp_path / "home", tmp_path / "root"
    her_old_setup(home, root)
    before = (modes(home), modes(root))
    said: list[str] = []
    host = ownuser.Host(root, dry_run=True, say=said.append)
    move = ownuser.Migration(
        host,
        ownuser.Owner("alex", home, os.getuid()),
        checkout=a_checkout(tmp_path),
        mode="dry",
        deploy=lambda: 0,
        say=said.append,
    )
    assert move.run() == 0
    assert (modes(home), modes(root)) == before
    text = "\n".join(said)
    assert "would run: /usr/sbin/sysadminctl -addUser _estia" in text and "would copy" in text
    assert "BRAVE_API_KEY" not in text and "abc" not in text
    assert "Nothing changed (a dry run)" in text


def test_the_first_deploy_runs_while_your_old_folders_are_still_readable(tmp_path: Path, no_user: None) -> None:
    """The build reads vendor builds and the voice binary from your folders: they are moved only after it."""
    home, root = tmp_path / "home", tmp_path / "root"
    her_old_setup(home, root)
    seen: list[bool] = []
    deploy = fake_deploy(root)

    def checked() -> int:
        seen.append(os.access(home / "Library/Application Support/local-assistant/vendor", os.W_OK))
        return deploy()

    move = ownuser.Migration(
        RecordingHost(root),
        ownuser.Owner("alex", home, os.getuid()),
        checkout=a_checkout(tmp_path),
        mode="real",
        deploy=checked,
    )
    assert move.run() == 0 and seen == [True]


# her account: made again when a first run stopped half way, and checked


class DirectoryHost(RecordingHost):
    """A directory service where an earlier run made the group (gid 497) but not the user."""

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        if argv[:3] == [hostsec.DSCL, ".", "-read"] and argv[3] == "/Groups/_estia":
            return 0, "PrimaryGroupID: 497\n"
        if argv[:3] == [hostsec.DSCL, ".", "-list"]:
            return 0, "_www 70\nalex 501\n" if argv[3] == "/Users" else "_estia 497\nstaff 20\n"
        if "-delete" in argv:
            return 182, "eDSAttributeNotFound"  # not there: expected
        return 0, ""


def test_a_second_try_uses_the_group_an_earlier_run_made(tmp_path: Path, no_user: None) -> None:
    host = DirectoryHost(tmp_path / "root")
    move = ownuser.Migration(
        host, ownuser.Owner("alex", tmp_path / "home", os.getuid()), checkout=tmp_path, mode="real", deploy=lambda: 0
    )
    move._make_user()
    joined = [" ".join(argv) for argv in host.asked]
    assert not any("dseditgroup" in line for line in joined)
    add = next(argv for argv in host.asked if "-addUser" in argv)
    assert add[add.index("-UID") + 1] == "497" and add[add.index("-GID") + 1] == "497"
    assert joined[-2].endswith("Password *") or any(line.endswith("Password *") for line in joined)


class LiveDirectoryHost(DirectoryHost):
    """The live Mac, as far as `make_account` sees it: every command answered by DirectoryHost, never run; `made`
    says whether dscl finds her account afterwards."""

    def __init__(self, root: Path, *, made: bool) -> None:
        super().__init__(root)
        self.made = made

    @property
    def live(self) -> bool:
        return True

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        if list(argv[:4]) == [hostsec.DSCL, ".", "-read", f"/Users/{ownuser.ROLE_USER}"]:
            self.asked.append(tuple(argv))
            return (0, "UniqueID: 497\n") if self.made else (56, "<dscl_cmd> DS Error: -14136 (eDSRecordNotFound)\n")
        return super().run(argv, **kwargs)


@pytest.mark.parametrize("made", [True, False])
def test_her_account_is_checked_after_sysadminctl_made_it(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch, made: bool
) -> None:
    """Review host-role-account (could not be checked: only macOS 27 was there): sysadminctl may end without an error
    and no account made (an older macOS and its role-account numbers). Her account is looked up right after it; none
    stops the move there, with a clear line, before her settings or her home."""
    monkeypatch.setattr(ownuser, "free_id", lambda users, groups: 497)
    (tmp_path / "root/var").mkdir(parents=True)
    host = LiveDirectoryHost(tmp_path / "root", made=made)
    host.path = lambda system: tmp_path / "root" / Path(system).relative_to("/")  # type: ignore[method-assign]
    monkeypatch.setattr(ownuser, "run_checked", lambda host_, argv: host_.run(list(argv)))
    if made:
        ownuser.make_account(host, lambda text: None)
        assert any("-addUser" in argv for argv in host.asked) and any(
            argv[-2:] == ("Password", "*") for argv in host.asked
        )
        return
    with pytest.raises(RuntimeError) as stopped:
        ownuser.make_account(host, lambda text: None)
    assert str(stopped.value) == (
        "macOS made no account _estia: sysadminctl -addUser ended without an error, but the account is not there "
        "(number 497; macOS not read; tested on macOS 27 only). sysadminctl said: nothing"
    )
    assert not any("IsHidden" in argv for argv in host.asked)  # no settings, no home, for an account not there


class SaidHost(LiveDirectoryHost):
    """sysadminctl ends with exit 0 and its own reason on its output, and makes no account (an older macOS)."""

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        if argv[0] == hostsec.SYSADMINCTL:
            self.asked.append(tuple(argv))
            return 0, "2026-10-29 sysadminctl[812:4471] ### Error:-14136 UID 497 is outside the role-account range\n"
        if argv[0] == "/usr/bin/sw_vers":
            self.asked.append(tuple(argv))
            return 0, "26.1\n"
        return super().run(argv, **kwargs)


def test_macos_making_no_account_names_what_sysadminctl_said_and_the_macos_version(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second deep scan, macos-account-not-made-no-reason: sysadminctl's own reason (on its output, exit 0) was
    dropped, so neither the friend nor a helping AI could tell why. The line names it, and the macOS version."""
    monkeypatch.setattr(ownuser, "free_id", lambda users, groups: 497)
    (tmp_path / "root/var").mkdir(parents=True)
    host = SaidHost(tmp_path / "root", made=False)
    host.path = lambda system: tmp_path / "root" / Path(system).relative_to("/")  # type: ignore[method-assign]
    with pytest.raises(RuntimeError) as stopped:
        ownuser.make_account(host, lambda text: None)
    said = str(stopped.value)
    assert said.startswith("macOS made no account _estia: sysadminctl -addUser ended without an error"), said
    assert "UID 497 is outside the role-account range" in said and "macOS 26.1" in said, said


class RangeHost(LiveDirectoryHost):
    """sysadminctl refuses a role account's UID outside its own range and names the range (as its usage line does on
    macOS 27: "UID in 450-499 range"); another macOS may name another range. A UID in it makes the account."""

    def __init__(self, root: Path, *, low: int, high: int, code: int = 1) -> None:
        super().__init__(root, made=False)
        self.low, self.high, self.code = low, high, code

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        if argv[0] == hostsec.SYSADMINCTL:
            self.asked.append(tuple(argv))
            if self.low <= int(argv[argv.index("-UID") + 1]) <= self.high:
                self.made = True
                return 0, ""
            return self.code, f"*Role accounts require name starting with _ and UID in {self.low}-{self.high} range.\n"
        if list(argv[:3]) == [hostsec.DSCL, ".", "-list"] and argv[3] == "/Users":
            self.asked.append(tuple(argv))
            return 0, "_www 70\nalex 501\n_taken 400\n"
        return super().run(argv, **kwargs)


@pytest.mark.parametrize("code", [1, 0], ids=["refused", "exit 0, no account"])
def test_a_refused_uid_is_tried_again_in_the_range_sysadminctl_names(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """plat-02 (owner's call, 29 Oct: macOS 27's range is kept, the tested one): when sysadminctl refuses the number
    and names its own role-account range, her account is made once more with the highest free number in that range;
    her group keeps its number."""
    (tmp_path / "root/var").mkdir(parents=True)
    host = RangeHost(tmp_path / "root", low=200, high=400, code=code)
    host.path = lambda system: tmp_path / "root" / Path(system).relative_to("/")  # type: ignore[method-assign]
    monkeypatch.setattr(ownuser, "run_checked", lambda host_, argv: host_.run(list(argv)))
    ownuser.make_account(host, lambda text: None)
    adds = [argv for argv in host.asked if "-addUser" in argv]
    assert [argv[argv.index("-UID") + 1] for argv in adds] == ["497", "399"]  # 400 is a user's
    assert all(argv[argv.index("-GID") + 1] == "497" for argv in adds)


def test_a_range_is_read_from_sysadminctls_own_words() -> None:
    assert ownuser.role_range("*Role accounts require name starting with _ and UID in 450-499 range.") == range(
        499, 449, -1
    )
    assert ownuser.role_range("Error: UID 497 is outside the role-account range (200 - 400)") == range(400, 199, -1)
    assert ownuser.role_range("UID 497 is outside the role-account range") is None
    assert ownuser.role_range("nothing") is None


class GroupHost(LiveDirectoryHost):
    """The account made, its primary group as dscl shows it (`primary`), and whether dscl may change it."""

    def __init__(self, root: Path, *, primary: int, settable: bool = True) -> None:
        super().__init__(root, made=True)
        self.primary = primary
        self.settable = settable

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        if list(argv[:5]) == [hostsec.DSCL, ".", "-read", f"/Users/{ownuser.ROLE_USER}", "PrimaryGroupID"]:
            self.asked.append(tuple(argv))
            return 0, f"PrimaryGroupID: {self.primary}\n"
        if list(argv[:5]) == [hostsec.DSCL, ".", "-create", f"/Users/{ownuser.ROLE_USER}", "PrimaryGroupID"]:
            self.asked.append(tuple(argv))
            if not self.settable:
                return 40, "<main> attribute status: eDSPermissionError\n"
            self.primary = int(argv[5])
            return 0, ""
        return super().run(argv, **kwargs)


@pytest.mark.parametrize("exists", [False, True], ids=["made now", "made before"])
def test_her_primary_group_is_her_own_never_staff(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch, exists: bool
) -> None:
    """Second deep scan, macos-primary-group-unchecked: sysadminctl ignores -UID on macOS 27 and may ignore -GID
    elsewhere; then her primary group is staff (20) and her account reads every file your group may read in your
    home. After sysadminctl, and on every run, her primary group is set to her own group's number; one macOS refuses
    stops with a clear line."""
    monkeypatch.setattr(ownuser, "free_id", lambda users, groups: 497)
    if exists:
        monkeypatch.setattr(ownuser, "lookup_uid", lambda name: 497)
    (tmp_path / "root/var").mkdir(parents=True)
    host = GroupHost(tmp_path / "root", primary=20)
    host.path = lambda system: tmp_path / "root" / Path(system).relative_to("/")  # type: ignore[method-assign]
    ownuser.make_account(host, lambda text: None)
    assert host.primary == 497
    refused = GroupHost(tmp_path / "root", primary=20, settable=False)
    refused.path = host.path  # type: ignore[method-assign]
    with pytest.raises(RuntimeError) as stopped:
        ownuser.make_account(refused, lambda text: None)
    assert str(stopped.value).startswith("macOS kept staff (20) as _estia's primary group, not _estia (497)"), stopped


def test_doctor_names_a_primary_group_other_than_her_own(tmp_path: Path, as_her: None) -> None:
    """macos-primary-group-unchecked: doctor showed ✓ for her account in your staff group. Her record's primary group
    is read with the rest; staff, or another than her own group, is a ✗ with `estia account` as the fix."""
    installed(tmp_path)
    record = LIVE_RECORD + "PrimaryGroupID: 20\n"
    check = by_key(ownuser.install_checks(tmp_path, account=lambda: record))["own_user"]
    assert check.status == "fail" and "primary group" in check.text and "staff" in check.text, check
    assert "estia account" in (check.fix or "")
    ok = by_key(
        ownuser.install_checks(tmp_path, account=lambda: LIVE_RECORD + "PrimaryGroupID: 497\n", group=lambda: 497)
    )
    assert ok["own_user"].status == "ok", ok["own_user"]
    other = ownuser.install_checks(tmp_path, account=lambda: LIVE_RECORD + "PrimaryGroupID: 80\n", group=lambda: 497)
    assert by_key(other)["own_user"].status == "fail"


def test_the_account_settings_are_set_again_when_the_user_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: 497)
    host = DirectoryHost(tmp_path / "root")
    move = ownuser.Migration(
        host, ownuser.Owner("alex", tmp_path / "home", os.getuid()), checkout=tmp_path, mode="real", deploy=lambda: 0
    )
    move._make_user()
    joined = [" ".join(argv) for argv in host.asked]
    assert any(line.endswith("IsHidden 1") for line in joined) and any(line.endswith("Password *") for line in joined)
    assert not any("sysadminctl" in line for line in joined)


# her record as `ownuser.read_account` asks dscl for it (UserShell IsHidden Password AuthenticationAuthority), on the live
# Mac, 14 Oct (macOS 27; "No such key: AuthenticationAuthority" went to stderr)
LIVE_RECORD = "dsAttrTypeNative:IsHidden: 1\nPassword: *\nUserShell: /usr/bin/false\n"


@pytest.mark.parametrize(
    ("record", "problem"),
    [
        ("NFSHomeDirectory: /var/estia\nUserShell: /usr/bin/false\nIsHidden: 1\nPassword: *\n", None),
        ("NFSHomeDirectory: /var/empty\nUserShell: /usr/bin/false\nIsHidden: 1\nPassword: *\n", None),  # macOS keeps it
        ("UserShell: /usr/bin/false\nIsHidden: 1\nPassword: *\n", None),
        ("NFSHomeDirectory: /var/estia\nUserShell: /bin/zsh\nIsHidden: 1\nPassword: *\n", "shell"),
        ("NFSHomeDirectory: /var/estia\nUserShell: /usr/bin/false\nIsHidden: 0\nPassword: *\n", "login window"),
        (
            "AuthenticationAuthority: ;ShadowHash;HASHLIST:<SALTED-SHA512-PBKDF2>\nUserShell: /usr/bin/false\n"
            "IsHidden: 1\nPassword: ********\nNFSHomeDirectory: /var/estia\n",
            "AuthenticationAuthority",
        ),
        ("NFSHomeDirectory: /var/estia\nUserShell: /usr/bin/false\nIsHidden: 1\nPassword: ********\n", "password"),
        # live, 14 Oct (macOS 27): dscl names IsHidden as a native attribute, with its prefix
        (LIVE_RECORD, None),
        (LIVE_RECORD.replace("IsHidden: 1", "IsHidden: 0"), "login window"),
        (LIVE_RECORD.replace("UserShell: /usr/bin/false", "dsAttrTypeNative:UserShell: /bin/zsh"), "shell"),
    ],
)
def test_her_account_record_is_checked(record: str, problem: str | None) -> None:
    found = ownuser.account_problem(record)
    assert (found is None) if problem is None else (found is not None and problem in found)


def test_doctor_reads_her_live_record_as_hidden(tmp_path: Path, as_her: None) -> None:
    """Live, 14 Oct: doctor said "Own user: it shows on the login window" for a hidden account, as dscl printed
    "dsAttrTypeNative:IsHidden: 1" and the key was read up to its first colon."""
    installed(tmp_path)
    check = by_key(ownuser.install_checks(tmp_path, account=lambda: LIVE_RECORD))["own_user"]
    assert check.status == "ok", check.text


def test_doctor_shows_an_account_with_a_password_as_a_failure(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path)
    record = "AuthenticationAuthority: ;ShadowHash;\nNFSHomeDirectory: /var/estia\nUserShell: /usr/bin/false\nIsHidden: 1\nPassword: ********\n"
    check = by_key(ownuser.install_checks(tmp_path, account=lambda: record))["own_user"]
    assert check.status == "fail" and "AuthenticationAuthority" in check.text


# her service files: only hers


GOOD: dict[str, Any] = {
    "Label": "com.local-assistant.bridge",
    "UserName": "_estia",
    "GroupName": "_estia",
    "ProgramArguments": ["/usr/local/estia/current/.venv/bin/local-assistant"],
    "EnvironmentVariables": {
        "HOME": "/var/estia",
        "CFFIXED_USER_HOME": "/var/estia",
        "PATH": "/usr/local/estia/current/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "PYTHONUNBUFFERED": "1",
    },
    "WorkingDirectory": "/usr/local/estia/current",
    "Umask": 63,
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 30,
    "StandardOutPath": "/var/log/estia/launchd.log",
}


@pytest.mark.parametrize(
    ("change", "word"),
    [
        ({"UserName": "root"}, "UserName"),
        ({"ProgramArguments": ["/Users/alex/estia/.venv/bin/local-assistant"]}, "program"),
        ({"EnvironmentVariables": {**GOOD["EnvironmentVariables"], "DYLD_INSERT_LIBRARIES": "/tmp/x"}}, "Environment"),
        ({"EnvironmentVariables": {**GOOD["EnvironmentVariables"], "PATH": "/opt/homebrew/bin:/usr/bin"}}, "PATH"),
        ({"Sockets": {}}, "Sockets"),
        (
            {"EnvironmentVariables": {**GOOD["EnvironmentVariables"], "CFFIXED_USER_HOME": "/Users/alex"}},
            "CFFIXED_USER_HOME",
        ),
        ({"StandardOutPath": "/etc/sudoers.d/zz"}, "/var/log/estia"),
        ({"StandardErrorPath": "/var/log/estia/../../etc/x"}, "/var/log/estia"),
        ({"StandardOutPath": "/var/estia/Library/Logs/local-assistant/launchd.log"}, "her own folder"),
        ({"Label": "com.example.other"}, "Label"),
        # second deep scan, macos-plist-values-unchecked: the allowed keys' values too
        ({"Umask": 0}, "Umask"),
        ({"Umask": 18}, "Umask"),
        ({"RunAtLoad": False}, "RunAtLoad"),
        ({"KeepAlive": {"SuccessfulExit": False}}, "KeepAlive"),
        ({"ThrottleInterval": 1}, "ThrottleInterval"),
        ({"ThrottleInterval": "30"}, "ThrottleInterval"),
        ({"ThrottleInterval": True}, "ThrottleInterval"),
        ({"WorkingDirectory": "/usr/local/estia/current/../../../tmp"}, "WorkingDirectory"),
    ],
)
def test_a_service_file_that_is_not_only_hers_is_named(change: dict[str, object], word: str) -> None:
    assert ownuser.plist_problem(GOOD, "com.local-assistant.bridge") is None
    problem = ownuser.plist_problem({**GOOD, **change}, "com.local-assistant.bridge")
    assert problem is not None and word in problem


@pytest.mark.parametrize("key", ["Umask", "RunAtLoad", "KeepAlive", "ThrottleInterval"])
def test_a_service_file_without_one_of_her_settings_is_named(key: str) -> None:
    """macos-plist-values-unchecked: her templates always set them; one left out is not hers."""
    problem = ownuser.plist_problem({k: v for k, v in GOOD.items() if k != key}, "com.local-assistant.bridge")
    assert problem is not None and key in problem


def test_service_logs_left_in_her_folder_name_the_tools_update(tmp_path: Path, as_her: None) -> None:
    """An install from before /var/log/estia: her logs are in her folder, where her account can put a link in its
    place (launchd follows that link as her account, never as root: M01, 30 Oct). A ✗, in plain words: the tools'
    update and one more deploy render the new service files."""
    installed(tmp_path)
    plist = tmp_path / "Library/LaunchDaemons/com.local-assistant.bridge.plist"
    data = plistlib.loads(plist.read_bytes())
    data["StandardOutPath"] = data["StandardErrorPath"] = "/var/estia/Library/Logs/local-assistant/launchd.log"
    plist.write_bytes(plistlib.dumps(data))
    check = by_key(ownuser.install_checks(tmp_path))["plists"]
    assert check.status == "fail" and "her own folder" in check.text
    assert "bridge: its output is in her own folder, where her account can put a link in its place" in check.text
    assert "root" not in check.text
    assert check.fix == (
        "/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools (read the diff), then "
        "/usr/bin/sudo /usr/local/estia/bin/estia-deploy: it moves them to /var/log/estia"
    )


def test_her_service_logs_are_made_in_a_root_owned_folder(tmp_path: Path) -> None:
    """/var/log/estia (0755) and one file per service (0600, hers on the Mac), made before launchd opens them as her
    (M01, 30 Oct: a missing one stops the start), in a folder only root can change, so no link of hers can be planted
    at those names; she empties them through the open file launchd gives her."""
    host = RecordingHost(tmp_path)
    ownuser.make_stream_logs(host)
    folder = tmp_path / "var/log/estia"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o755
    for name in ("launchd.log", "voice.log"):
        assert stat.S_ISREG((folder / name).lstat().st_mode) and stat.S_IMODE((folder / name).stat().st_mode) == 0o600
    (folder / "voice.log").write_text("kept")
    ownuser.make_stream_logs(host)  # again: nothing emptied
    assert (folder / "voice.log").read_text() == "kept"
    (folder / "voice.log").unlink()
    (folder / "voice.log").symlink_to(tmp_path / "elsewhere")
    ownuser.make_stream_logs(host)  # only root could put a link there: replaced, never followed
    assert not os.path.lexists(tmp_path / "elsewhere")
    assert stat.S_ISREG((folder / "voice.log").lstat().st_mode)


def test_a_second_name_of_her_log_never_blocks_an_update(tmp_path: Path) -> None:
    """Her log is on the same volume as her home, so her account can give it a second name (`ln`) at any time; the
    folder is root's, so that name can only lead to her own file. Root gives the service a fresh file instead of
    refusing: a refusal would stop every update, security fixes too (review 15 Oct, 5)."""
    host = RecordingHost(tmp_path)
    ownuser.make_stream_logs(host)
    folder = tmp_path / "var/log/estia"
    (folder / "launchd.log").write_text("before")
    os.link(folder / "launchd.log", tmp_path / "her-name")
    ownuser.make_stream_logs(host)
    info = (folder / "launchd.log").lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
    assert (folder / "launchd.log").read_text() == "" and (tmp_path / "her-name").read_text() == "before"


def test_a_link_in_place_of_her_log_folder_is_refused(tmp_path: Path) -> None:
    (tmp_path / "var/log").mkdir(parents=True)
    (tmp_path / "var/log/estia").symlink_to(tmp_path)
    with pytest.raises(OSError, match="not a folder"):
        ownuser.make_stream_logs(RecordingHost(tmp_path))


def test_a_service_file_others_can_change_is_a_failure(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path)
    (tmp_path / "Library/LaunchDaemons/com.local-assistant.bridge.plist").chmod(0o666)
    check = by_key(ownuser.install_checks(tmp_path))["plists"]
    assert check.status == "fail" and "estia-deploy" in check.fix


def test_moved_follows_the_bridges_service_file_and_the_rollback(tmp_path: Path) -> None:
    assert not ownuser.moved(tmp_path)
    installed(tmp_path)
    assert ownuser.moved(tmp_path)
    (tmp_path / "usr/local/estia/migration.json").write_text(json.dumps({"rolled_back": True}))
    assert not ownuser.moved(tmp_path)


def test_a_link_chain_out_of_the_code_is_a_failure(tmp_path: Path, as_her: None) -> None:
    release = installed(tmp_path)
    (release / "src" / "b").symlink_to(".")
    (release / "b").symlink_to("..")
    (release / "src" / "evil.pth").symlink_to("b/b/../../../../../../etc/hosts")
    check = by_key(ownuser.install_checks(tmp_path))["code"]
    assert check.status == "fail" and "lead out" in check.text


# what she runs: root's alone


def test_her_programs_must_be_roots_alone(tmp_path: Path, as_her: None) -> None:
    mine = tmp_path / "opt/homebrew/bin"
    mine.mkdir(parents=True)
    (mine / "signal-cli").write_text("x")
    ok = tmp_path / "usr/bin"
    ok.mkdir(parents=True)
    (ok / "true").write_text("x")
    assert ownuser.program_check([("tool", ok / "true")], tmp_path).status == "ok"
    mine.chmod(0o775)  # the owner's group may write: any program of yours can swap it
    check = ownuser.program_check([("signal-cli", mine / "signal-cli")], tmp_path)
    assert check.status == "fail" and "signal-cli" in check.text and "estia-deploy" in check.fix


def test_her_programs_owned_by_someone_else_are_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ownuser, "CODE_UID", os.getuid() + 1)
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/signal-cli").write_text("x")
    assert ownuser.program_check([("signal-cli", tmp_path / "bin/signal-cli")], tmp_path).status == "fail"


# her config: the programs she runs point into the release


def test_her_program_keys_are_taken_out_or_pointed_at_the_release(tmp_path: Path) -> None:
    vendor = tmp_path / "vendor"
    for path in (
        "signal-cli-native/bin/signal-cli",
        "openjdk.jdk/Contents/Home/bin/java",
        "signal-cli-0.14.10-jvm/signal-cli-0.14.10/bin/signal-cli",
        "signal-call-tunnel/signal-call-tunnel/target/release/signal-call-tunnel",
    ):
        (vendor / path).parent.mkdir(parents=True, exist_ok=True)
        (vendor / path).write_text("x")
    home = Path("/Users/alex")
    text = (
        'signal_cli = "/opt/homebrew/bin/signal-cli"\n'
        "[calls]\n"
        'signal_cli = "/Users/alex/dev/vendor/signal-cli-0.14.10-jvm/signal-cli-0.14.10/bin/signal-cli"\n'
        'java_home = "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"\n'
        'tunnel = "~/dev/vendor/signal-call-tunnel/x"\n'
        "[health]\n"
        'watch_logs = ["~/Library/Logs/x.log", "/var/log/system.log"]\n'
    )
    out, notes = ownuser.config_notes(text, home, vendor)
    assert '# signal_cli = "/opt/homebrew/bin/signal-cli"' in out  # the default finds her own copy
    assert (
        'signal_cli = "/usr/local/estia/current/vendor/signal-cli-0.14.10-jvm/signal-cli-0.14.10/bin/signal-cli"' in out
    )  # the same version, from the release
    assert '# java_home = "/opt/homebrew' in out and '# tunnel = "~/dev' in out
    joined = "\n".join(notes)
    assert "health.watch_logs" in joined  # a home path inside a list is named
    assert "/opt/homebrew" not in joined and "~/" not in joined  # keys only, never values


def test_without_her_own_copy_a_program_key_is_named_not_changed(tmp_path: Path) -> None:
    text = 'signal_cli = "/opt/homebrew/bin/signal-cli"\n'
    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    assert out.startswith(text) and any("signal_cli" in note and "no copy" in note for note in notes)


# her projects folder: the owner's, by its absolute path (~ in her config is her own home)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ('bot_number = "+306900000000"\n[projects]\nroot = "~/dev"  # yours\ndocs = ["alpha"]\n', "  # yours"),
        ('bot_number = "+306900000000"\n[projects]\n#root = "~/dev"\ndefault = "summary"\n', "default"),
        ('bot_number = "+306900000000"\n[projects]\n[[tapo.devices]]\nname = "lamp"\n', "lamp"),
    ],
    ids=["set", "commented", "array-after"],
)
def test_her_config_names_your_projects_folder_by_its_absolute_path(text: str, kept: str) -> None:
    import tomllib

    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    data = tomllib.loads(out)
    assert data["projects"]["root"] == "/Users/alex/dev" and kept in out
    assert any(note.startswith('projects.root: set to "/Users/alex/dev"') for note in notes), notes
    assert ownuser.her_projects_root(text, Path("/Users/alex")) == Path("/Users/alex/dev")


@pytest.mark.parametrize(
    ("root", "expected", "word"),
    [
        ("~/code/work", "/Users/alex/code/work", "set to"),
        ("/Volumes/Work/dev", "/Volumes/Work/dev", "outside your home"),
    ],
)
def test_her_projects_folder_from_your_config(root: str, expected: str, word: str) -> None:
    import tomllib

    out, notes = ownuser.config_notes(f'[projects]\nroot = "{root}"\n', Path("/Users/alex"), None)
    assert tomllib.loads(out)["projects"]["root"] == expected
    assert any(note.startswith("projects.root:") and word in note for note in notes), notes


def test_without_projects_her_config_gets_no_folder_of_yours() -> None:
    """Projects are an extra: without [projects] in your config, none is added to hers and nothing is on record."""
    text = 'bot_number = "+306900000000"\n[health]\ninterval_hours = 4\n'
    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    assert out == text and ownuser.her_projects_root(text, Path("/Users/alex")) is None
    assert notes == ["projects: off (no [projects] in your config): no folder of yours is shared with her"]


def test_a_relative_projects_folder_is_named_not_guessed() -> None:
    text = '[projects]\nroot = "dev"\n'
    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    assert out == text and ownuser.her_projects_root(text, Path("/Users/alex")) is None
    assert any(note.startswith("projects.root:") and "absolute" in note for note in notes)


def test_a_projects_root_that_cannot_be_written_is_named() -> None:
    text = "[projects]\nroot = '~/dev'\n"  # a literal string: the line is not rewritten in place
    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    assert out == text
    assert any(note.startswith("projects.root:") and 'root = "/Users/alex/dev"' in note for note in notes)


@needs_plutil
def test_the_move_records_her_projects_folder(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    assert move.run() == 0
    marker = json.loads((root / "usr/local/estia/migration.json").read_text())
    assert marker["projects_root"] == str(home / "dev")
    import tomllib

    her = tomllib.loads((root / "var/estia/.config/local-assistant/config.toml").read_text())
    assert her["projects"]["root"] == str(home / "dev")
    assert any(line.startswith(f'config: projects.root: set to "{home / "dev"}"') for line in said)


def test_code_under_a_folder_others_can_change_is_a_failure(tmp_path: Path, as_her: None) -> None:
    """Whoever can rename /usr/local/estia can put other code (and another `estia`) in its place."""
    installed(tmp_path)
    (tmp_path / "usr/local").chmod(0o777)
    check = by_key(ownuser.install_checks(tmp_path))["code"]
    assert check.status == "fail" and "/usr/local" in check.text


@needs_plutil
def test_a_leftover_in_the_holding_folder_never_stops_the_move(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    leftover = root / "usr/local/estia/rollback/data/0"
    leftover.mkdir(parents=True)
    (leftover / "x").write_text("from an earlier move")
    assert move.run() == 0
    assert (leftover / "x").exists()  # untouched
    held = json.loads((root / "usr/local/estia/migration.json").read_text())["held"]
    assert len({target for _source, target in held}) == len(held) == 4 and str(leftover) not in dict(held).values()


def test_a_folder_given_back_gets_its_missing_parents_as_yours(tmp_path: Path) -> None:
    """The rollback renames a held folder back; parents you deleted meanwhile are made again, and yours."""
    home, root = tmp_path / "home", tmp_path / "root"
    home.mkdir()
    held = root / "usr/local/estia/rollback/data/1-0"
    held.mkdir(parents=True)
    (held / "account.db").write_text("x")
    move = ownuser.Migration(
        RecordingHost(root), ownuser.Owner("alex", home, os.getuid()), checkout=tmp_path, mode="real", deploy=lambda: 0
    )
    move._unhold([(str(home / ".local/share/signal-cli"), str(held))])
    assert (home / ".local/share/signal-cli/account.db").read_text() == "x"
    assert (home / ".local").stat().st_uid == os.getuid()
    assert stat.S_IMODE((home / ".local/share").stat().st_mode) == 0o700


@pytest.mark.parametrize("planted", [".local", ".local/share"])
def test_the_rollback_never_follows_a_link_you_made_on_the_way(tmp_path: Path, planted: str) -> None:
    """Root gives your folders back one name at a time, never through a link: a link a program of yours put on the
    way (~/.local to a folder only root may write) would have root make folders there, yours, and move your Signal
    keys into it. The held folder stays held; the rollback prints the command that puts it back."""
    home, root = tmp_path / "home", tmp_path / "root"
    (home / ".local").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if (home / planted).is_dir():
        (home / planted).rmdir()
    (home / planted).symlink_to(elsewhere)
    held = root / "usr/local/estia/rollback/data/1-0"
    held.mkdir(parents=True)
    (held / "account.db").write_text("x")
    move = ownuser.Migration(
        RecordingHost(root), ownuser.Owner("alex", home, os.getuid()), checkout=tmp_path, mode="real", deploy=lambda: 0
    )
    with pytest.raises(OSError, match="link"):
        move._unhold([(str(home / ".local/share/signal-cli"), str(held))])
    assert list(elsewhere.iterdir()) == [] and (held / "account.db").is_file()


def test_a_held_folder_recorded_outside_your_home_is_never_given_back(tmp_path: Path) -> None:
    home, root = tmp_path / "home", tmp_path / "root"
    home.mkdir()
    held = root / "usr/local/estia/rollback/data/1-0"
    held.mkdir(parents=True)
    move = ownuser.Migration(
        RecordingHost(root), ownuser.Owner("alex", home, os.getuid()), checkout=tmp_path, mode="real", deploy=lambda: 0
    )
    with pytest.raises(OSError, match="not in your home"):
        move._unhold([(str(tmp_path / "etc/sudoers.d/x"), str(held))])
    assert held.is_dir() and not (tmp_path / "etc").exists()


def test_a_hang_up_while_the_release_builds_changes_nothing(tmp_path: Path, no_user: None) -> None:
    """A dropped SSH session during the build: an interruption, so the half-built release is removed and she keeps
    running (nothing was stopped yet)."""
    import signal

    seen: list[object] = []
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)

    def building() -> int:
        seen.append(signal.getsignal(signal.SIGHUP))
        raise KeyboardInterrupt("signal 1")

    move.deploy = building
    assert move.run() == 1
    assert seen and seen[0] not in (signal.SIG_DFL, signal.SIG_IGN, None)  # a hang-up raises, it does not kill
    assert not any(argv[0] == ownuser.LAUNCHCTL for argv in host.asked) and any("Nothing changed" in s for s in said)


def test_a_folder_reached_through_a_link_out_of_your_home_is_never_moved(tmp_path: Path) -> None:
    """Root renames only folders that are really in your home: a parent you swapped for a link to elsewhere is left."""
    home, elsewhere = tmp_path / "home", tmp_path / "system"
    (elsewhere / "signal-cli").mkdir(parents=True)
    (home / ".local").mkdir(parents=True)
    (home / ".local/share").symlink_to(elsewhere)
    (home / ".config/local-assistant").mkdir(parents=True)
    move = ownuser.Migration(
        RecordingHost(tmp_path / "root"),
        ownuser.Owner("alex", home, os.getuid()),
        checkout=tmp_path,
        mode="real",
        deploy=lambda: 0,
    )
    assert [str(p.relative_to(home)) for p in move._old_folders()] == [".config/local-assistant"]


def test_her_config_is_rewritten_from_the_copy_you_could_read(tmp_path: Path, no_user: None) -> None:
    """Root never opens a config through a path someone else controls: not yours (your links), not hers (her links,
    a racing process of hers). Her copy, which your tar made for her, is read and written as her."""
    move, host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    config = root / "var/estia/.config/local-assistant/config.toml"
    opened: list[str] = []
    real_open = os.open

    def watch(path: object, *args: object, **kwargs: object) -> int:
        opened.append(str(path))
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    import unittest.mock

    with unittest.mock.patch.object(os, "open", watch):
        move._make_user()
        move._copy_data()
    assert str(home / ".config/local-assistant/config.toml") not in opened and str(config) not in opened
    assert ("/bin/cat", str(config)) in host.asked and ("/usr/bin/tee", str(config)) in host.asked
    assert f'root = "{home / "dev"}"' in config.read_text()  # rewritten: your projects folder by its full path


def test_her_programs_name_what_she_runs(tmp_path: Path) -> None:
    from types import SimpleNamespace

    installed(tmp_path)
    plain = SimpleNamespace(
        calls_enabled=False, signal_cli="/usr/local/estia/current/vendor/signal-cli-native/bin/signal-cli"
    )
    names = dict(ownuser.her_programs(plain, tmp_path))
    assert names["signal-cli"] == Path(plain.signal_cli)
    jdk = Path("/usr/local/estia/current/vendor/openjdk.jdk/Contents/Home")
    calls = SimpleNamespace(
        calls_enabled=True,
        call_signal_cli=Path("/usr/local/estia/current/vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"),
        call_tunnel=Path("/usr/local/estia/current/vendor/signal-call-tunnel/x"),
        calls_java_home=None,
        java_home=jdk,  # the release's own JDK: the default when the config names none
    )
    names = dict(ownuser.her_programs(calls, tmp_path))
    assert names["Java"] == jdk / "bin" / "java" and "the call helper" in names and "signal-cli (calls)" in names


def test_her_java_is_the_one_she_runs_also_when_macos_picks_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No java_home in her config and no JDK in her release: the JVM signal-cli runs the Java macOS picks, perhaps a
    Homebrew keg linked into /Library/Java that your account can change. Doctor checks that one, links followed, as
    it checks every program of hers (review I32)."""
    from types import SimpleNamespace

    installed(tmp_path)
    keg = tmp_path / "opt/homebrew/Cellar/openjdk@25/25.0.1/libexec/openjdk.jdk/Contents/Home"
    (keg / "bin").mkdir(parents=True)
    (keg / "bin/java").write_text("java")
    linked = tmp_path / "Library/Java/JavaVirtualMachines/openjdk-25.jdk"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(keg.parent.parent)
    calls = SimpleNamespace(
        calls_enabled=True,
        call_signal_cli=Path("/usr/local/estia/current/vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"),
        call_tunnel=Path("/usr/local/estia/current/vendor/signal-call-tunnel/x"),
        java_home=None,
    )
    names = dict(ownuser.her_programs(calls, tmp_path, java=lambda: linked / "Contents/Home"))
    assert names["Java"] == linked / "Contents/Home/bin/java"
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: os.getuid())
    monkeypatch.setattr(ownuser, "CODE_UID", 0)  # the keg belongs to the test user, as Homebrew's to you
    check = ownuser.program_check([("Java", names["Java"])], tmp_path)
    assert check.status == "fail" and "Java" in check.text


# once moved, the fixes never send you back to the move (it would run your checkout's Python as root)

GOOD_RECORD = "UserShell: /usr/bin/false\nIsHidden: 1\nPassword: *\n"


@pytest.mark.parametrize(
    ("state", "fix"),
    [("no user", "estia account"), ("no home", "backup"), ("a password", "estia account")],  # no move on record
)
def test_once_moved_the_own_user_fix_never_names_the_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, fix: str
) -> None:
    installed(tmp_path)
    me = os.getuid()
    monkeypatch.setattr(ownuser, "CODE_UID", me)
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: None if state == "no user" else me)
    if state == "no home":
        shutil.rmtree(tmp_path / "var/estia")
    record = "UserShell: /usr/bin/false\nIsHidden: 1\nPassword: ********\n" if state == "a password" else GOOD_RECORD
    check = by_key(ownuser.install_checks(tmp_path, account=lambda: record))["own_user"]
    assert check.status == "fail"
    assert "migrate-to-own-user" not in check.fix and fix in check.fix


def test_estia_account_sets_her_account_again_with_fixed_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: 497)
    monkeypatch.setenv("ESTIA_ROOT", str(tmp_path))
    assert ownuser.main(["account"]) == 0
    out = capsys.readouterr().out
    record = f"/Users/{ownuser.ROLE_USER}"  # her user's record in Directory Services
    for line in (
        f"would run: /usr/bin/dscl . -create {record} UserShell /usr/bin/false",
        f"would run: /usr/bin/dscl . -create {record} IsHidden 1",
        f"would run: /usr/bin/dscl . -delete {record} AuthenticationAuthority",
        f"would run: /usr/bin/dscl . -delete {record} ShadowHashData",
        f"would run: /usr/bin/dscl . -create {record} Password *",
    ):
        assert line in out, line
    assert "sysadminctl" not in out  # she is there: only her settings


def test_estia_account_needs_root(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("ESTIA_ROOT", raising=False)
    if os.geteuid() == 0:
        pytest.skip("runs as root")
    assert ownuser.main(["account"]) == 2
    assert "/usr/bin/sudo /usr/local/estia/bin/estia account" in capsys.readouterr().err


# links and special files: refused before she stops, and again in her copy before she starts


@pytest.mark.parametrize("planted", ["a link out", "a dangling link out", "a FIFO"])
def test_the_preflight_refuses_a_link_out_or_a_special_file(tmp_path: Path, no_user: None, planted: str) -> None:
    said: list[str] = []
    seen: list[str] = []
    move, host, home, root = migration(tmp_path, said=said, seen=seen)
    her_old_setup(home, root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    data = home / ".local/share/signal-cli/data"
    if planted == "a FIFO":
        os.mkfifo(data / "pipe")
        name = "pipe"
    else:
        (data / "1.d").symlink_to(elsewhere if planted == "a link out" else tmp_path / "nowhere")
        name = "1.d"
    assert move.run() == 1
    assert seen == [] and host.asked == []  # nothing built, nothing run
    text = "\n".join(said)
    assert str(data / name) in text and "Nothing changed" in text


def test_her_number_is_read_from_a_config_of_reasonable_size_only(tmp_path: Path) -> None:
    """ARCH10 re-check: root reads her number at a path you control; like read_json, a capped read."""
    config = tmp_path / "config.toml"
    config.write_text('bot_number = "+306900000000"\n', encoding="utf-8")
    assert ownuser.bot_number(config) == "+306900000000"
    config.write_text('bot_number = "+306900000000"\n' + "# padding\n" * 200_000, encoding="utf-8")
    assert ownuser.bot_number(config) is None


def test_the_preflight_prints_a_planted_name_without_its_hidden_characters(tmp_path: Path, no_user: None) -> None:
    """FULL2 re-check: root prints the move's preflight lines. A FIFO named with an escape sequence, a new line and
    a right-to-left override in your config folder reaches root's terminal as "?"s."""
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    name = "pipe\x1b[2J\nfine\u202etxt"
    os.mkfifo(home / ".config/local-assistant" / name)
    assert move.run() == 1
    text = "\n".join(said)
    assert "pipe?[2J?fine?txt" in text and "\x1b" not in text and "\u202e" not in text
    assert not any(line.startswith("fine") for line in said)


@pytest.mark.parametrize("parent", [".config", ".local/share", ".local/share/signal-cli/data"])
def test_the_preflight_reads_nothing_of_hers_through_a_link(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch, parent: str
) -> None:
    """ARCH10: root runs the preflight, and root never follows your links. A folder on the way to her config or her
    Signal account swapped for a link to elsewhere is named first, and nothing is read through it: neither her number
    nor her account file."""
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    elsewhere = tmp_path / "elsewhere"
    shutil.move(home / parent, elsewhere)  # the same files, now behind a link
    (home / parent).symlink_to(elsewhere)
    read: list[Path] = []
    real_number, real_account = ownuser.bot_number, ownuser.has_account

    def number(config: Path) -> str | None:
        read.append(config)
        return real_number(config)

    def account(signal_data: Path, wanted: str) -> bool:
        read.append(signal_data)
        return real_account(signal_data, wanted)

    monkeypatch.setattr(ownuser, "bot_number", number)
    monkeypatch.setattr(ownuser, "has_account", account)
    assert move.run() == 1
    assert read == [] and host.asked == []
    text = "\n".join(said)
    assert hostsec.LINK_OUT in text and "Nothing changed" in text


@needs_plutil
def test_a_link_inside_her_folders_is_copied(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    (home / ".local/share/signal-cli/data/latest").symlink_to("111111.d")
    assert move.run() == 0
    assert (root / "var/estia/.local/share/signal-cli/data/latest").is_symlink()


@needs_plutil
def test_a_link_planted_during_the_build_stops_the_move_before_she_starts(tmp_path: Path, no_user: None) -> None:
    """The preflight saw none; a link made while her release builds is found in her copy, and her old setup comes
    back before anything of hers starts from there."""
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    build = move.deploy

    def planting() -> int:
        (home / ".local/share/signal-cli/data/1.d").symlink_to(elsewhere)
        return build()

    move.deploy = planting
    assert move.run() == 1
    text = "\n".join(said)
    assert "/var/estia/.local/share/signal-cli/data/1.d" in text and "Her old setup comes back" in text
    assert not any(argv[0] == ownuser.LAUNCHCTL and "_estia" in " ".join(argv) for argv in host.asked)
    assert (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").read_bytes() == OLD_PLIST
    assert (home / ".config/local-assistant/config.toml").exists()


# the restore and the rollback: failure-safe


def restoring(tmp_path: Path, path: str, said: list[str]) -> tuple[ownuser.Migration, RecordingHost, Path, Path]:
    """A move that fails after her old folders are held (so the restore runs), or a finished move to roll back."""
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    if path == "restore":

        def broken(voice: bool) -> None:
            raise RuntimeError("no plist")

        move._install_plists = broken  # type: ignore[method-assign]
        return move, host, home, root
    assert move.run() == 0
    said.clear()
    back, host2, _, _ = migration(tmp_path, mode="rollback", said=said)
    return back, host2, home, root


def go(move: ownuser.Migration, path: str) -> int:
    try:
        return move.run() if path == "restore" else move.rollback()
    except BaseException as exc:  # a signal or a failed step got through: she would stay stopped
        pytest.fail(f"the {path} was interrupted: {exc!r}")


@needs_plutil
@pytest.mark.parametrize("path", ["restore", "rollback"])
def test_a_failed_unhold_still_brings_her_services_back_and_prints_each_held_folder(
    tmp_path: Path, no_user: None, path: str
) -> None:
    """A failed move brings her old services back; a rollback that stopped short starts hers again (your old ones never
    run without what she saved since). Each folder still held is printed as two steps: root moves it straight into
    your home under a fresh name (`mv -h`: no folder on the way, no link followed), then you move it into place
    without admin rights (review 15 Oct, 1 and 4)."""
    import shlex

    said: list[str] = []
    move, host, home, root = restoring(tmp_path, path, said)

    def stuck(held: Sequence[tuple[str, str]]) -> None:
        raise OSError(16, "Resource busy")

    move._unhold = stuck  # type: ignore[method-assign]
    assert go(move, path) == 1
    for label in ownuser.LABELS:
        service = (root / f"Library/LaunchDaemons/{label}.plist").read_bytes()
        if path == "restore":
            assert service == OLD_PLIST  # put back
        else:
            assert plistlib.loads(service)["UserName"] == ROLE_USER  # hers: she runs as before
        assert (ownuser.LAUNCHCTL, "bootstrap", "system", f"/Library/LaunchDaemons/{label}.plist") in host.asked
    held = root / "usr/local/estia/rollback/data"
    roots = [shlex.split(line) for line in said if line.strip().startswith("/usr/bin/sudo /bin/mv ")]
    assert roots and all(words[:3] == ["/usr/bin/sudo", "/bin/mv", "-h"] for words in roots)
    assert all(Path(words[3]).parent == held and Path(words[3]).is_dir() for words in roots)  # each really still held
    assert all(Path(words[4]).parent == home and Path(words[4]).name.startswith("estia-restore-") for words in roots)
    yours = [shlex.split(line.split("&&", 1)[1]) for line in said if line.strip().startswith("/bin/mkdir -p ")]
    assert all(words[0] == "/bin/mv" for words in yours)  # yours already: no admin rights
    assert sorted(words[2] for words in yours) == sorted(str(home / old) for old in SECRET_FOLDERS)
    assert sorted(words[1] for words in yours) == sorted(words[4] for words in roots)
    assert len({words[4] for words in roots}) == len(roots)  # a name of its own each
    if path == "rollback":
        assert json.loads((root / "usr/local/estia/migration.json").read_text())["rolled_back"] is False
        assert any(f"She runs as {ROLE_USER} again" in line for line in said)


@needs_plutil
@pytest.mark.parametrize("path", ["restore", "rollback"])
def test_hang_ups_terms_and_ctrl_c_are_ignored_while_her_old_setup_comes_back(
    tmp_path: Path, no_user: None, path: str
) -> None:
    """The handlers she ignores come back as they were when the test started: a pytest run from a background job
    starts with SIGINT already ignored (SEC5), so "as it was" is read, never assumed."""
    import signal

    stops = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    before = {number: signal.getsignal(number) for number in stops}
    said: list[str] = []
    move, _host, home, root = restoring(tmp_path, path, said)
    unhold = move._unhold
    finished: list[bool] = []

    def interrupted(held: Sequence[tuple[str, str]]) -> None:
        for number in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            os.kill(os.getpid(), number)
            time.sleep(0.05)
        unhold(held)
        finished.append(True)

    move._unhold = interrupted  # type: ignore[method-assign]
    try:
        assert go(move, path) == (1 if path == "restore" else 0)
        assert finished == [True]
        for old in SECRET_FOLDERS:
            assert (home / old).is_dir(), old
        assert (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").read_bytes() == OLD_PLIST
        assert {number: signal.getsignal(number) for number in stops} == before  # as they were
    finally:
        for number, handler in before.items():  # never left changed for the tests after this one
            if handler is not None:
                signal.signal(number, handler)


STALE = (
    ".local/share/signal-cli/data/111111.d/account.db-wal",
    ".local/share/signal-cli/data/111111.d/account.db-shm",
    "Library/Application Support/local-assistant/assistant.db-wal",
    "Library/Application Support/local-assistant/assistant.db-shm",
)


@needs_plutil
@pytest.mark.parametrize("path", ["restore", "rollback"])
def test_a_stale_wal_never_lands_next_to_her_newer_database(tmp_path: Path, no_user: None, path: str) -> None:
    """SQLite replays a -wal into whatever database sits next to it: an old one beside her newer copy corrupts it."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    for name in STALE:  # left by an unclean stop before the move
        (home / name).write_text("old frames")
        (home / name).chmod(0o600)
    her = root / "var/estia"

    def newer_and_clean() -> None:  # her bridge ran and closed cleanly: newer databases, no -wal
        for name in STALE:
            (her / name).unlink(missing_ok=True)
        (her / ".local/share/signal-cli/data/111111.d/account.db").write_text("newer")
        (her / "Library/Application Support/local-assistant/assistant.db").write_text("newer")

    if path == "restore":
        start = move._start

        def started_then_failed(labels: list[str]) -> None:
            start(labels)
            newer_and_clean()
            raise RuntimeError("the bridge did not stay up")

        move._start = started_then_failed  # type: ignore[method-assign]
        assert move.run() == 1
    else:
        assert move.run() == 0
        newer_and_clean()
        assert migration(tmp_path, mode="rollback")[0].rollback() == 0
    assert (home / ".local/share/signal-cli/data/111111.d/account.db").read_text() == "newer"
    assert (home / "Library/Application Support/local-assistant/assistant.db").read_text() == "newer"
    for name in STALE:
        assert not (home / name).exists(), name


# ------------------------------------------------------------------ when calls work after a move or a deploy


@pytest.mark.parametrize(
    ("health", "line"),
    [
        ({"stt": "ready", "tts": "ready", "vad": "loading"}, "voice: ready"),
        ({"stt": "loading", "tts": "ready"}, ownuser.VOICE_LOADING),
        (None, ownuser.VOICE_LOADING),  # not answering yet
    ],
)
def test_the_voice_line_names_the_real_state_only_when_ready(health: dict[str, str] | None, line: str) -> None:
    assert ownuser.voice_line(health) == line
    assert ownuser.VOICE_LOADING == ("Calls work once her voice is ready (up to about 15 minutes); she texts you then.")


class VoiceClock:
    """A fake clock whose sleep moves it on, and a voice that turns ready at a given time (None: never)."""

    def __init__(self, ready_at: float | None) -> None:
        self.now = 0.0
        self.ready_at = ready_at
        self.reads: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def health(self) -> dict[str, str]:
        self.reads.append(self.now)
        state = "ready" if self.ready_at is not None and self.now >= self.ready_at else "loading"
        return {"stt": state, "tts": state}


def test_the_voice_end_waits_for_the_bridges_long_load_rule_before_it_promises_a_text() -> None:
    """The bridge texts only after a long load (a not-ready read 2 minutes after its start): the terminal says "she
    texts you then" only when the voice is still loading that long after the bridge was seen up."""
    from local_assistant import voice_ready

    assert ownuser.VOICE_WAIT_SECONDS >= voice_ready.LONG_LOAD_SECONDS + 2 * voice_ready.POLL_SECONDS
    said: list[str] = []
    soon = VoiceClock(ready_at=60.0)
    line = ownuser.voice_end(soon.health, calls=True, clock=soon, sleep=soon.sleep, say=said.append)
    assert line == "voice: ready" and soon.now < 70
    late = VoiceClock(ready_at=None)
    line = ownuser.voice_end(late.health, calls=True, clock=late, sleep=late.sleep, say=said.append)
    assert line == ownuser.VOICE_LOADING and late.reads[-1] >= ownuser.VOICE_WAIT_SECONDS
    assert said == [ownuser.VOICE_WAITING, ownuser.VOICE_WAITING]


def test_a_ctrl_c_during_the_voice_wait_ends_only_the_wait() -> None:
    """The move, the deploy and setup are done by then: an interruption ends the wait with a line, never a trace."""
    said: list[str] = []
    voice = VoiceClock(ready_at=None)

    def sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    line = ownuser.voice_end(voice.health, calls=True, clock=voice, sleep=sleep, say=said.append)
    assert line == ownuser.VOICE_STOPPED and "texts" not in line
    # her voice service answers only her own account: your curl would be refused, her wrapper's check reads it as her
    assert line.endswith("/usr/bin/sudo /usr/local/estia/bin/estia check shows it") and "curl" not in line
    # review R2-F7: before her own macOS account that command refuses; the checkout's own check shows it
    before = ownuser.voice_end(voice.health, calls=True, clock=voice, sleep=sleep, say=said.append, moved=False)
    assert before == ownuser.VOICE_STOPPED_YOURS == "voice: not checked to the end (stopped); scripts/check.sh shows it"


def test_a_voice_not_read_as_her_says_so_at_once_and_never_says_loading() -> None:
    """Review R2-F1 and R2-F4: a read as her that sudo or her voice service refused said "loading", the same line as a
    real load, and with [calls] waited 150 s and promised a text. It says it was not read, at once."""
    said: list[str] = []
    voice = VoiceClock(ready_at=None)
    for why in (ownuser.VOICE_NOT_SIGNED_IN, ownuser.VOICE_REFUSED_HER):

        def not_read(why: str = why) -> ownuser.VoiceHealth:
            return ownuser.VoiceNotRead(why)

        for calls in (True, False):
            line = ownuser.voice_end(not_read, calls=calls, clock=voice, sleep=voice.sleep, say=said.append)
            assert line == f"voice: not read as her ({why}); /usr/bin/sudo /usr/local/estia/bin/estia check shows it"
    assert said == [] and voice.now == 0.0  # no wait, no "checking again"
    assert ownuser.VOICE_NOT_SIGNED_IN == "no admin sign-in running"
    assert ownuser.VOICE_REFUSED_HER == "her voice service refused it"
    # one that turns not read during the wait ends it there
    reads: Iterator[ownuser.VoiceHealth] = iter(
        [{"stt": "loading", "tts": "loading"}, ownuser.VoiceNotRead(ownuser.VOICE_NOT_SIGNED_IN)]
    )
    line = ownuser.voice_end(lambda: next(reads), calls=True, clock=voice, sleep=voice.sleep, say=said.append)
    assert line.startswith("voice: not read as her") and said == [ownuser.VOICE_WAITING]


def test_without_calls_the_voice_end_promises_no_text_and_does_not_wait() -> None:
    said: list[str] = []
    voice = VoiceClock(ready_at=None)
    line = ownuser.voice_end(voice.health, calls=False, clock=voice, sleep=voice.sleep, say=said.append)
    assert line == ownuser.VOICE_LOADING_NO_CALLS == "voice: loading (up to about 15 minutes)"
    assert "texts" not in line and voice.reads == [0.0] and said == []
    ready = VoiceClock(ready_at=0.0)
    assert ownuser.voice_end(ready.health, calls=False, clock=ready, sleep=ready.sleep, say=said.append) == (
        "voice: ready"
    )


def test_calls_are_on_only_with_a_calls_table_next_to_voice() -> None:
    assert ownuser.calls_in('[voice]\nurl = "http://127.0.0.1:8150"\n[calls]\n')
    assert not ownuser.calls_in('[voice]\nurl = "http://127.0.0.1:8150"\n')
    assert not ownuser.calls_in("[calls]\n")  # [calls] needs [voice]: the bridge would not start
    assert not ownuser.calls_in("not = toml = at all")


def test_the_end_of_the_move_says_when_calls_work(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live 13 Oct: "voice: ?" (the health has no "state"): now the real state, or when calls work; the text is
    promised only with [calls] in her config and a voice still loading when the bridge counts a long load."""
    from local_assistant import deploy

    said: list[str] = []
    move, _host, _home, _root = migration(tmp_path, said=said)
    monkeypatch.setattr(deploy.Deploy, "health", lambda self, since, old_pid=None: None)
    voice = VoiceClock(ready_at=None)
    move.clock, move.sleep = voice, voice.sleep
    move.voice_health = voice.health
    move.calls = True
    move._check(0.0)
    assert said == []  # the health check alone: the voice is read once the move is done
    move._voice_end(True)
    assert said == [ownuser.VOICE_WAITING, ownuser.VOICE_LOADING]
    said.clear()
    move.voice_health = lambda: {"stt": "ready", "tts": "ready"}
    move._voice_end(True)
    assert said == ["voice: ready"]
    said.clear()
    move.calls = False
    move.voice_health = lambda: {"stt": "loading", "tts": "loading"}
    move._voice_end(True)
    assert said == [ownuser.VOICE_LOADING_NO_CALLS]
    said.clear()
    move._voice_end(False)  # no voice service: no line
    assert said == []


def test_a_ctrl_c_while_waiting_for_her_voice_leaves_the_finished_move_alone(tmp_path: Path, no_user: None) -> None:
    """The wait comes after the move is done: an interruption there never brings her old setup back."""
    said: list[str] = []
    move, _host, _home, _root = migration(tmp_path, said=said)
    move.calls = True

    def interrupted() -> dict[str, str]:
        raise KeyboardInterrupt

    move.voice_health = interrupted
    move._voice_end(True)  # no exception: the move stays done
    assert said == [ownuser.VOICE_STOPPED]


def test_the_voice_health_of_a_test_root_is_never_the_live_service(tmp_path: Path) -> None:
    move, _host, _home, _root = migration(tmp_path)
    assert move.voice_health() is None  # a fake root never asks 127.0.0.1:8150


def test_her_voice_is_read_as_her_since_it_answers_only_her_own_account() -> None:
    """Owner, 25 Oct: her voice service answers only programs running as her own macOS account, so root (the move, an
    update) and you (setup) read its state with curl run as her: one fixed command, her home, no PATH of yours, no
    curlrc of hers (-q), 5 s and 10 kB at most, never a password asked (-n)."""
    seen: list[list[str]] = []

    def run(argv: Sequence[str]) -> tuple[int, str]:
        seen.append(list(argv))
        return 0, '{"stt":"ready","tts":"ready","turn":"ready"}'

    assert ownuser.her_voice_health(run) == {"stt": "ready", "tts": "ready", "turn": "ready"}
    assert seen == [
        [
            "/usr/bin/sudo", "-n", "-u", "_estia", "-H", "--", "/usr/bin/env", "-i", "HOME=/var/estia",
            "PATH=/usr/bin:/bin", "/usr/bin/curl", "-q", "-s", "-f", "--max-time", "5", "--max-filesize", "10000",
            "http://127.0.0.1:8150/health",
        ]
    ]  # fmt: skip
    # review R2-F1: curl -f exits 22 on her voice service's 403 (without it, 0 and the refusal read as an answer);
    # sudo -n exits 1 when it needs a password (no admin sign-in running): both are "not read", never "loading"
    expected: list[tuple[tuple[int, str], object]] = [
        ((1, ""), ownuser.VoiceNotRead(ownuser.VOICE_NOT_SIGNED_IN)),
        ((22, ""), ownuser.VoiceNotRead(ownuser.VOICE_REFUSED_HER)),
        ((7, ""), None),  # no answer: not up yet, or restarting
        ((28, ""), None),  # too slow
        ((-1, ""), None),  # sudo and curl did not finish in time
        ((0, "not json"), None),
        ((0, "[1, 2]"), None),
        ((0, ""), None),
    ]
    for answer, health in expected:

        def refused(_argv: Sequence[str], answer: tuple[int, str] = answer) -> tuple[int, str]:
            return answer

        assert ownuser.her_voice_health(refused) == health, answer


def test_a_read_as_her_that_does_not_finish_is_no_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sudo with -n never waits for a password: a read that times out is a slow service, never a refused sign-in."""

    def slow(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="curl", timeout=ownuser.VOICE_READ_SECONDS)

    monkeypatch.setattr(subprocess, "run", slow)
    assert ownuser.her_voice_health() is None


def test_the_move_reads_her_voice_as_her(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[bool, bool]] = []
    monkeypatch.setattr(ownuser, "voice_health", lambda live, *, as_her=False: asked.append((live, as_her)))
    move, _host, _home, _root = migration(tmp_path)
    move.voice_health()
    assert asked == [(False, True)]  # a test root: not live, and as her once it is


# ------------------------------------------------------------------ the copies kept aside in her home (remove-old)


def kept_aside(her: Path, stamp: str) -> Path:
    """An earlier copy of hers that a retried move kept aside: a direct child of her home named .before-<stamp>."""
    folder = her / f".before-{stamp}"
    (folder / ".config/local-assistant").mkdir(parents=True)
    (folder / ".config/local-assistant/web.env").write_text("BRAVE_API_KEY=abc\n")
    return folder


@needs_plutil
def test_remove_old_deletes_her_copies_kept_aside_and_names_each(tmp_path: Path, no_user: None) -> None:
    """Live 13 Oct: four tries left copies in her home that needed hand-typed paths. Only real folders named
    .before-YYYYMMDD-HHMMSS right in her home: a link is never followed (nor deleted), nothing else is touched."""
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    first, second = kept_aside(her, "20261013-204501"), kept_aside(her, "20261013-210012")
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "keep").mkdir(parents=True)
    link = her / ".before-20261013-220000"
    link.symlink_to(elsewhere)
    (her / ".before-20261013-230000").write_text("a file, not a folder")
    (her / ".before-notes").mkdir()
    (her / "Library/.before-20261013-204501").mkdir(parents=True)
    said: list[str] = []
    assert migration(tmp_path, said=said)[0].remove_old() == 0
    assert not first.exists() and not second.exists()
    assert link.is_symlink() and (elsewhere / "keep").is_dir()
    assert (her / ".before-20261013-230000").is_file() and (her / ".before-notes").is_dir()
    assert (her / "Library/.before-20261013-204501").is_dir()
    assert not (root / "usr/local/estia/rollback/data").exists()  # the held folders too, as before
    for name in (first.name, second.name):
        assert any(f"{HOME}/{name}" in line for line in said), said
    assert not any("220000" in line or "230000" in line for line in said)
    assert "abc" not in "\n".join(said)


@needs_plutil
def test_remove_old_again_deletes_copies_left_since(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    assert migration(tmp_path)[0].remove_old() == 0  # the held folders: gone, on record
    copy = kept_aside(root / "var/estia", "20261014-080000")
    said: list[str] = []
    assert migration(tmp_path, said=said)[0].remove_old() == 0
    assert not copy.exists() and any(copy.name in line for line in said)
    assert migration(tmp_path)[0].remove_old() == 1  # nothing left: said so


def test_after_a_fresh_install_remove_old_deletes_the_copies_kept_aside(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root, services=False)
    assert move.run() == 0
    copy = kept_aside(root / "var/estia", "20261013-204501")
    assert migration(tmp_path)[0].remove_old() == 0
    assert not copy.exists()


@needs_plutil
@pytest.mark.parametrize("fresh", [True, False])
def test_a_dry_run_says_a_first_install_keeps_no_copy_of_your_folders(
    tmp_path: Path, no_user: None, fresh: bool
) -> None:
    """The dry run said your folders "would move into /usr/local/estia/rollback (root only)", but a first install
    deletes them right after the move (release review D9)."""
    home, root = tmp_path / "home", tmp_path / "root"
    her_old_setup(home, root, services=not fresh)
    said: list[str] = []
    move = ownuser.Migration(
        ownuser.Host(root, dry_run=True, say=said.append),
        ownuser.Owner("alex", home, os.getuid()),
        checkout=a_checkout(tmp_path),
        mode="dry",
        deploy=lambda: 0,
        say=said.append,
    )
    assert move.run() == 0, said
    lines = [line for line in said if line.startswith("would move") and str(ownuser.HELD) in line]
    assert lines, said
    for line in lines:
        assert ("deleted after the move; no copy is kept (back them up first)" in line) is fresh, line
        assert ("held for 7 days" in line) is not fresh, line


def test_a_dry_run_of_remove_old_names_the_copies_and_keeps_them(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root, services=False)
    assert move.run() == 0
    copy = kept_aside(root / "var/estia", "20261013-204501")
    said: list[str] = []
    dry, _, _, _ = migration(tmp_path, mode="dry", said=said)
    assert dry.remove_old() == 0
    assert copy.is_dir() and any(f"would delete {HOME}/{copy.name}" in line for line in said), said


@needs_plutil
def test_a_successful_move_lists_the_copies_kept_aside_and_the_command_that_deletes_them(
    tmp_path: Path, no_user: None
) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    assert migration(tmp_path, mode="rollback")[0].rollback() == 0
    said: list[str] = []
    again, _, _, _ = migration(tmp_path, said=said)
    assert again.run() == 0
    kept = sorted(p.name for p in (root / "var/estia").iterdir() if p.name.startswith(".before-"))
    assert kept
    line = next((text for text in said if text.startswith("Earlier copies of hers kept aside")), "")
    assert "/usr/bin/sudo /usr/local/estia/bin/estia remove-old deletes them" in line
    assert all(f"{HOME}/{name}" in line for name in kept), said
    assert not any(" sudo /usr/local" in text or text.startswith("sudo ") for text in said)  # one form only


def test_a_move_without_copies_kept_aside_names_none(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, _, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root, services=False)
    assert move.run() == 0
    assert not any(".before-" in line for line in said)


def test_without_a_move_on_record_remove_old_still_deletes_the_copies_kept_aside(tmp_path: Path) -> None:
    """Every try failed: no record, but a later try may have kept an earlier copy aside."""
    move, _, _, root = migration(tmp_path)
    copy = kept_aside(root / "var/estia", "20261013-204501")
    assert move.remove_old() == 0
    assert not copy.exists()
    assert migration(tmp_path)[0].remove_old() == 1


# ------------------------------------------------------------------ the releases of earlier tries (a successful move)


def earlier_releases(root: Path) -> list[Path]:
    """Releases an earlier try built that never went live: 2 MB each."""
    found = []
    for name in ("20261013T190000Z-aaaaaaaaaaaa", "20261013T193000Z-bbbbbbbbbbbb"):
        release = root / "usr/local/estia/releases" / name
        (release / "src").mkdir(parents=True)
        (release / "src/big.bin").write_bytes(b"x" * 2_000_000)
        found.append(release)
    return found


@needs_plutil
def test_a_successful_move_deletes_the_releases_she_does_not_run(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, _, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    old = earlier_releases(root)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "keep").mkdir(parents=True)
    (root / "usr/local/estia/releases/link").symlink_to(elsewhere)
    assert move.run() == 0
    assert not any(release.exists() for release in old)
    assert (root / "usr/local/estia/current").resolve().is_dir()  # the one she runs stays
    assert (elsewhere / "keep").is_dir()  # a link there is never followed
    line = next((text for text in said if "release" in text and "freed" in text), "")
    assert "2 releases" in line and "4.0 MB freed" in line, said


def test_a_current_that_points_nowhere_deletes_no_release(tmp_path: Path, no_user: None) -> None:
    """A dangling `current` (or one pointing outside releases/) names no live release: nothing is deleted as root."""
    said: list[str] = []
    move, _, _home, root = migration(tmp_path, said=said)
    old = earlier_releases(root)
    current = root / "usr/local/estia/current"
    current.symlink_to("releases/20261013T200000Z-cccccccccccc")  # gone
    move._prune_releases()
    assert all(release.is_dir() for release in old) and not any("freed" in text for text in said)
    outside = tmp_path / "outside-release"
    outside.mkdir()
    current.unlink()
    current.symlink_to(outside)
    move._prune_releases()
    assert all(release.is_dir() for release in old)


def test_a_failed_move_keeps_every_release(tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    old = earlier_releases(root)
    monkeypatch.setattr(move, "_install_plists", lambda voice: (_ for _ in ()).throw(RuntimeError("no plist")))
    assert move.run() == 1
    assert all(release.is_dir() for release in old)


def test_a_rehearsal_and_a_dry_run_keep_every_release(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path, mode="rehearse")
    her_old_setup(home, root)
    old = earlier_releases(root)
    assert move.run() == 0
    assert all(release.is_dir() for release in old)
    said: list[str] = []
    dry = ownuser.Migration(
        ownuser.Host(root, dry_run=True, say=said.append),
        ownuser.Owner("alex", home, os.getuid()),
        checkout=tmp_path / "checkout",  # the rehearsal made it
        mode="dry",
        deploy=lambda: 0,
        say=said.append,
    )
    assert dry.run() == 0
    assert all(release.is_dir() for release in old)


def test_sizes_are_said_in_decimal_units() -> None:
    assert [ownuser.human_size(n) for n in (0, 999, 12_300, 4_000_000, 1_400_000_000)] == [
        "0 bytes",
        "999 bytes",
        "12 kB",
        "4.0 MB",
        "1.4 GB",
    ]


# ------------------------------------------------------------------ her config: the picture and the watched logs


def picture(path: Path, size: int = 2000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (size - 8))
    return path


def with_config(home: Path, extra: str) -> None:
    config = home / ".config/local-assistant/config.toml"
    config.write_text(extra + config.read_text())


@needs_plutil
def test_her_picture_in_your_home_is_copied_into_her_config_folder(tmp_path: Path, no_user: None) -> None:
    """Live 13 Oct: her avatar in your Pictures folder stopped working (your home is closed to her)."""
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    source = picture(home / "Pictures/her picture.png")
    with_config(home, 'avatar = "~/Pictures/her picture.png"\n')
    assert move.run() == 0
    copy = root / "var/estia/.config/local-assistant/avatar.png"
    assert copy.read_bytes() == source.read_bytes() and stat.S_IMODE(copy.stat().st_mode) == 0o600
    text = (root / "var/estia/.config/local-assistant/config.toml").read_text()
    assert f'avatar = "{HOME}/.config/local-assistant/avatar.png"' in text
    reads = [argv for argv in host.asked if "|" in argv and str(source) in argv]
    assert reads, "read as you, written as her: never root reading your path"
    assert any("avatar" in line and "copied" in line for line in said)
    assert not any("Pictures" in line for line in said)  # keys, never values


@pytest.mark.parametrize("kind", ["link", "too large", "outside your home", "missing", "dot-dot", "linked folder"])
def test_a_picture_that_cannot_be_copied_keeps_the_note(tmp_path: Path, no_user: None, kind: str) -> None:
    said: list[str] = []
    move, _, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root, services=False)
    real = picture(tmp_path / "elsewhere/real.png")
    target = home / "Pictures/her.png"
    value = "~/Pictures/her.png"
    if kind == "link":
        target.parent.mkdir(parents=True)
        target.symlink_to(real)
    elif kind == "too large":
        picture(target, 5 * 1024 * 1024 + 1)
    elif kind == "outside your home":
        target = real
        value = str(target)
    elif kind == "dot-dot":  # in your home as text only
        value = "~/../elsewhere/real.png"
    elif kind == "linked folder":  # a folder on the way is a link out of your home
        (home / "Pictures").symlink_to(tmp_path / "elsewhere")
        value = "~/Pictures/real.png"
    with_config(home, f'avatar = "{value}"\n')
    assert move.run() == 0
    assert not list((root / "var/estia/.config/local-assistant").glob("avatar*"))
    text = (root / "var/estia/.config/local-assistant/config.toml").read_text()
    assert f'avatar = "{value}"' in text  # left as it was
    named = [line for line in said if "config: avatar" in line]
    if kind == "outside your home":
        assert named == []  # not in your home: nothing to say
    else:
        assert named and "home" in named[0] and "copied" not in named[0]


@needs_plutil
def test_the_picture_is_read_with_a_time_limit(tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A FIFO swapped in after the check would block `head` (as you) while she is stopped: the pipe has a limit."""
    move, host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    picture(home / "Pictures/her.png")
    with_config(home, 'avatar = "~/Pictures/her.png"\n')
    limits: list[object] = []
    real = host.pipe

    def pipe(first: list[str], second: list[str], **kwargs: object) -> tuple[int, str]:
        if ownuser.HEAD in first:
            limits.append(kwargs.get("timeout"))
        return real(first, second, **kwargs)

    monkeypatch.setattr(host, "pipe", pipe)
    assert move.run() == 0
    assert limits == [ownuser.AVATAR_SECONDS] and ownuser.AVATAR_SECONDS <= 60


def test_watched_log_folders_in_your_home_are_left_out_of_her_copy_and_counted() -> None:
    home = Path("/Users/alex")
    text = (
        'bot_number = "+306900000000"\n'
        "[health]\n"
        'watch_logs = ["~/Library/Logs/x", "/var/log/estia", "/Users/alex/Logs/y"]  # watched\n'
        "[projects]\n"
        'docs = ["~/notes"]\n'
    )
    out, notes = ownuser.config_notes(text, home, None)
    import tomllib

    data = tomllib.loads(out)
    assert data["health"]["watch_logs"] == ["/var/log/estia"]
    assert "# watched" in out and 'docs = ["~/notes"]' in out  # only [health] watch_logs changes
    [note] = [note for note in notes if note.startswith("health.watch_logs")]
    assert "2 folders" in note and "left out" in note
    assert "~/" not in note and "/Users/alex" not in note and "/var/log" not in note  # keys, never values


def test_when_every_watched_log_folder_is_yours_her_copy_watches_none() -> None:
    out, notes = ownuser.config_notes('[health]\nwatch_logs = ["~/Library/Logs/x"]\n', Path("/Users/alex"), None)
    import tomllib

    assert tomllib.loads(out)["health"]["watch_logs"] == []
    assert any("1 folder " in note for note in notes)


def test_a_watched_log_list_over_several_lines_is_handled_too() -> None:
    text = '[health]\nwatch_logs = [\n  "~/Library/Logs/x",  # mine\n  "/var/log/estia",\n]\ninterval_hours = 4\n'
    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    import tomllib

    data = tomllib.loads(out)
    assert data["health"] == {"watch_logs": ["/var/log/estia"], "interval_hours": 4}
    assert any(note.startswith("health.watch_logs: 1 folder ") for note in notes)


# ------------------------------------------------------------------ `estia signal-login`: root's half, the reading

NUMBERLESS = "99999999-8888-4777-8666-555555555555"  # made up
SHOWN_KEY = "ABCDEFGHIJKLMN#PQRSTUVWXYZ=12345" * 2  # made up, as Signal shows it


def ask_login(hidden: list[str], typed: list[str]) -> tuple[dict[str, str] | None, list[str]]:
    """Root's reading with a fake terminal: `hidden` at the hidden prompts, `typed` at the visible one."""
    said: list[str] = []
    secrets, answers = iter(hidden), iter(typed)

    def read_hidden(prompt: str, needed: int, counted: Any) -> str:
        said.append(prompt)
        return next(secrets)

    def ask(question: str) -> str:
        said.append(question)
        try:
            return next(answers)
        except StopIteration:  # the end of input, as hostsec.ask_tty raises it
            raise EOFError from None

    return ownuser.signal_login_ask(say=said.append, hidden=read_hidden, ask=ask), said


def test_root_reads_the_account_id_the_key_and_her_username_and_hands_over_only_their_checked_forms() -> None:
    """Her user cannot open your terminal, so root's code from the tools reads it (the hidden read across lines,
    never a shell's read); what goes to her on stdin is the checked form only. A wrong one is asked again, with the
    reason and never the key."""
    login, said = ask_login(
        [NUMBERLESS[:-1], NUMBERLESS.upper(), SHOWN_KEY[:62], "\n".join([SHOWN_KEY[:32], "", SHOWN_KEY[32:]])],
        ["tiffany", "tiffany.42", ""],
    )
    assert login == {
        "account": NUMBERLESS,
        "recovery_key": SHOWN_KEY.lower().replace("#", "o").replace("=", "0"),
        "username": "tiffany.42",
    }
    text = "\n".join(said)
    assert hostsec.SIGNAL_LOGIN_INTRO in text and hostsec.NUMBERLESS_STEPS in text
    assert "never unregistered, never deleted" in text
    assert text.count(hostsec.ID_ASK) == 2 and text.count(hostsec.KEY_ASK) == 2 and "62 characters" in text
    assert "two or more digits" in text and SHOWN_KEY[:12] not in text and NUMBERLESS not in text


def test_root_reads_the_six_digit_code_only_when_one_is_typed() -> None:
    """Signal may ask for a six-digit code (TOTP) before it moves the account (signal-cli's manual, register --totp;
    review S7): a question last (Enter is no), then the code hidden; one of another form is asked again; it goes to her
    on stdin."""
    login, said = ask_login([NUMBERLESS, SHOWN_KEY, "12345", "123 456"], ["tiffany.42", "y"])
    assert login is not None and login["totp"] == "123456"
    text = "\n".join(said)
    assert text.count(hostsec.TOTP_ASK) == 2 and hostsec.TOTP_BAD in text and "123456" not in text
    assert hostsec.TOTP_QUESTION in text
    none, _said = ask_login([NUMBERLESS, SHOWN_KEY], ["tiffany.42", ""])  # Enter: no code, none handed over
    assert none is not None and "totp" not in none


@pytest.mark.parametrize(
    ("hidden", "typed"),
    [(["q"], []), ([NUMBERLESS, "q"], []), ([NUMBERLESS, SHOWN_KEY], ["q"]), ([NUMBERLESS, SHOWN_KEY], [])],
)
def test_q_or_the_end_of_input_stops_root_reading_with_nothing_handed_over(hidden: list[str], typed: list[str]) -> None:
    login, _said = ask_login(hidden, typed)
    assert login is None


def test_an_enter_alone_at_the_username_question_asks_it_again() -> None:
    """Review, 23 Oct: the Enter after the key (as a blank line) answered the username question with nothing, and
    that stopped the move. An empty answer is no answer: the question comes again, with no line that it is wrong."""
    login, said = ask_login([NUMBERLESS, SHOWN_KEY], ["", "", "tiffany.42", ""])
    assert login is not None and login["username"] == "tiffany.42"
    assert said.count(hostsec.USERNAME_ASK) == 3 and hostsec.USERNAME_BAD not in "\n".join(said)


def test_signal_login_reading_needs_root(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ownuser, "estia_root", lambda: Path("/"))
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    assert ownuser.main(["signal-login"]) == 2
    assert "/usr/bin/sudo /usr/local/estia/bin/estia signal-login" in capsys.readouterr().err

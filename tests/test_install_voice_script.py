"""scripts/install-voice-daemon.sh warns on macOS 27.0.x (an Apple bug that can crash English speech, seen on 27.0.1:
the review of 28 Oct, calls-03), before anything else. Estia needs macOS 27 or later (owner, 29 Oct): no 26.x line.
`sw_vers` is a stub at its full path in a test's fake tree (ESTIA_TEST_ROOT), which holds no own-user install, so the
script stops right after, and no sudo is ever run."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("version", "seen"),
    [("26.4", False), ("26.5.1", False), ("27.0", True), ("27.0.1", True), ("27.1", False)],
)
def test_the_install_warns_on_macos_27_0(tmp_path: Path, version: str, seen: bool) -> None:
    stubs = tmp_path / "root/usr/bin"  # the script reads /usr/bin/sw_vers by its full path: never one on your PATH
    stubs.mkdir(parents=True)
    (stubs / "sw_vers").write_text(f'#!/bin/sh\n[ "$1" = -productVersion ] && echo {version}\n')
    (stubs / "sw_vers").chmod(0o755)
    done = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts" / "install-voice-daemon.sh")],
        env={"ESTIA_TEST_ROOT": str(tmp_path / "root"), "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1 and "her own macOS account comes first" in done.stderr
    assert "26.6" not in done.stderr
    assert ("was seen on macOS 27.0.1; no fix yet" in done.stderr) is seen
    assert "27.0.1 too" not in done.stderr  # the 26.4 and 26.5 lines it followed are gone (words review, 30 Oct)
    assert ("Kokoro" in done.stderr) is seen


def installed(tmp_path: Path, state: str, *, refused: bool = False) -> tuple[Path, Path, dict[str, str]]:
    """A fake tree where she runs as her own user (bin/estia and the bridge's service file naming _estia), a sudo stub
    that logs each call and, as her, runs the wait it is given with a curl stub that answers `state` (or, `refused`,
    exits 1 as sudo does after a wrong password three times), and a curl stub on your PATH that must never run: your
    own curl would be refused (her voice service answers only her account)."""
    root, stubs = tmp_path / "root", tmp_path / "stubs"
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/sw_vers").write_text("#!/bin/sh\necho 27.0\n")
    (root / "usr/local/estia/bin").mkdir(parents=True)
    (root / "usr/local/estia/bin/estia").write_text("#!/bin/sh\n")
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict><key>UserName</key><string>_estia</string>'
        "</dict></plist>\n"
    )
    stubs.mkdir()
    (stubs / "curl").write_text(f"#!/bin/sh\necho \"$*\" >> \"{tmp_path}/curl.log\"\nprintf '%s' '{state}'\n")
    (stubs / "sleep").write_text("#!/bin/sh\n")
    log = tmp_path / "sudo.log"
    # as her: what follows `env -i HOME=… PATH=…` runs with the stubs' PATH instead (never the Mac's curl)
    (root / "usr/bin/sudo").write_text(
        "#!/bin/bash\n"
        f'all="$*"; printf "%s\\n" "${{all//$\'\\n\'/ }}" >> "{log}"\n'  # one line a call
        'if [[ "$1" == -u ]]; then\n'
        + ("  echo 'sudo: 3 incorrect password attempts' >&2; exit 1\n" if refused else "")
        + '  while [[ "$1" != /usr/bin/env ]]; do shift; done\n'
        f'  shift 4; PATH="{stubs}:/usr/bin:/bin" exec "$@"\n'
        "fi\n"
    )
    for stub in (root / "usr/bin/sw_vers", root / "usr/local/estia/bin/estia", root / "usr/bin/sudo", *stubs.iterdir()):
        stub.chmod(0o755)
    env = {"ESTIA_TEST_ROOT": str(root), "HOME": str(tmp_path), "PATH": f"{stubs}:/usr/bin:/bin"}
    return log, tmp_path / "curl.log", env


def run_install(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(ROOT / "scripts" / "install-voice-daemon.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_the_install_waits_for_her_voice_as_her(tmp_path: Path) -> None:
    """Owner, 25 Oct: her voice service answers only programs running as her own macOS account, so the wait for the
    models reads its health as her: one admin command for the whole wait, never your own curl."""
    log, mine, env = installed(tmp_path, '{"stt":"ready","tts":"ready"}')
    done = run_install(env)
    assert done.returncode == 0 and done.stdout.splitlines()[-1] == "voice: ready", done.stderr
    deploy, wait = log.read_text().splitlines()
    assert deploy.endswith("/usr/local/estia/bin/estia-deploy --voice " + str(ROOT))
    assert wait.startswith("-u _estia -H -- /usr/bin/env -i HOME=/var/estia PATH=/usr/bin:/bin /bin/bash -c ")
    assert "curl -q -s --max-time 2 --max-filesize 10000 http://127.0.0.1:8150/health" in wait
    # the her-side curl stub ran (once: ready at the first look); none of yours did (both log to the same file)
    assert mine.read_text().splitlines() == ["-q -s --max-time 2 --max-filesize 10000 http://127.0.0.1:8150/health"]


def test_a_failed_voice_says_so_and_the_check_later_is_her_wrappers(tmp_path: Path) -> None:
    _log, _mine, env = installed(tmp_path, '{"stt":"failed","tts":"ready"}')
    done = run_install(env)
    assert done.returncode == 1 and "voice: failed" in done.stderr
    _, _, env = installed(tmp_path / "slow", '{"stt":"loading","tts":"loading"}')
    done = run_install(env)
    # review host-voice-wait: still loading is no fault, with its own exit code (setup's calls step goes on)
    assert done.returncode == 3 and "still loading after 20 minutes" in done.stderr
    assert "the service keeps going, and calls work once her voice is ready." in done.stderr
    assert "Check later: /usr/bin/sudo /usr/local/estia/bin/estia check" in done.stderr
    assert "curl -s http" not in done.stderr  # your own curl would be refused


def test_a_refused_admin_command_says_her_voice_was_not_read(tmp_path: Path) -> None:
    """Review R2-F9: a wrong password three times left the state empty, and the script said at once that the voice was
    still loading after 20 minutes. Sudo's own failure says so (the wait itself always ends with its printf)."""
    _log, mine, env = installed(tmp_path, '{"stt":"ready","tts":"ready"}', refused=True)
    done = run_install(env)
    assert done.returncode == 1
    assert done.stderr.splitlines()[-1] == (
        "voice: not read as her (admin rights refused); /usr/bin/sudo /usr/local/estia/bin/estia check shows it"
    )
    assert "20 minutes" not in done.stderr and not mine.exists()


def test_what_answers_on_her_voice_address_reaches_the_terminal_cleaned(tmp_path: Path) -> None:
    """While her voice service is down, another account's program may hold 127.0.0.1:8150 and answer the wait with
    terminal control codes: only printable characters reach your Terminal."""
    _log, _mine, env = installed(tmp_path, '{"stt":"failed\x1b]0;owned\x07\x1b[2J"}')
    done = run_install(env)
    assert done.returncode == 1 and "voice: failed" in done.stderr
    assert "\x1b" not in done.stderr and "\x07" not in done.stderr and "?]0;owned??[2J" in done.stderr

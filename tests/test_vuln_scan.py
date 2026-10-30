"""Known security holes in the locked packages (owner, 23 Oct): scripts/vuln-scan.sh runs osv-scanner over every
uv.lock, offline by default; --download refreshes its database; verify.sh runs it when it can, the release steps with
--download first; GitHub scans the same files every week."""

import os
import re
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "vuln-scan.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "osv-scan.yml"
LOCKS = sorted(path.relative_to(ROOT).as_posix() for path in [ROOT / "uv.lock", *ROOT.glob("tools/*/uv.lock")])

# a stand-in osv-scanner: records its arguments and the database folder it was given, then exits as told
FAKE = """#!/bin/bash
printf '%s\\n' "$@" > "$RECORD/args"
printf '%s' "$OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY" > "$RECORD/db"
echo "the scanner's own report"
exit "${FAKE_EXIT:-0}"
"""


def tree(tmp_path: Path, *, scanner: bool = True, database: bool = True, days_old: float = 0) -> dict[str, str]:
    """A fake Homebrew with osv-scanner (or none) and a home with a downloaded PyPI database (or none), `days_old`."""
    root, home, record = tmp_path / "root", tmp_path / "home", tmp_path / "record"
    record.mkdir(parents=True)
    if scanner:
        fake = root / "opt/homebrew/bin/osv-scanner"
        fake.parent.mkdir(parents=True)
        fake.write_text(FAKE)
        fake.chmod(0o755)
    if database:
        zipped = home / ".cache/osv-scanner/osv-scalibr/PyPI/all.zip"
        zipped.parent.mkdir(parents=True)
        zipped.write_bytes(b"PK")
        when = time.time() - days_old * 86_400
        os.utime(zipped, (when, when))
    return {"ESTIA_TEST_ROOT": str(root), "HOME": str(home), "RECORD": str(record), "PATH": "/usr/bin:/bin"}


def scan(env: dict[str, str], *args: str, exit_code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        env=env | {"FAKE_EXIT": str(exit_code)},
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )


def given(env: dict[str, str]) -> list[str]:
    return (Path(env["RECORD"]) / "args").read_text().splitlines()


def test_every_lockfile_is_scanned_offline_against_the_downloaded_database(tmp_path: Path) -> None:
    env = tree(tmp_path)
    done = scan(env)
    assert done.returncode == 0, done.stderr
    args = given(env)
    assert args[:4] == ["scan", "source", "--offline", "--offline-vulnerabilities"]
    assert "--download-offline-databases" not in args
    scanned = sorted(args[i + 1] for i, arg in enumerate(args) if arg == "-L")
    assert scanned == LOCKS and "uv.lock" in scanned and "tools/youtube/uv.lock" in scanned
    assert (Path(env["RECORD"]) / "db").read_text() == f"{env['HOME']}/.cache/osv-scanner"
    assert "the scanner's own report" in done.stdout
    assert "✓ No known security hole in the 4 locked package lists" in done.stdout


def test_download_refreshes_the_database_and_is_the_only_network_use(tmp_path: Path) -> None:
    env = tree(tmp_path, database=False)
    assert scan(env, "--download").returncode == 0
    args = given(env)
    assert args[:4] == ["scan", "source", "--offline-vulnerabilities", "--download-offline-databases"]
    assert "--offline" not in args


def test_a_known_hole_fails_and_says_it_blocks_a_release(tmp_path: Path) -> None:
    env = tree(tmp_path)
    done = scan(env, exit_code=1)
    assert done.returncode == 1
    assert "✗ A known security hole" in done.stdout and "a release with one is not published" in done.stdout


def test_a_scanner_error_is_no_pass(tmp_path: Path) -> None:
    env = tree(tmp_path)
    done = scan(env, exit_code=127)
    assert done.returncode == 2 and "✗ osv-scanner failed (exit 127)" in done.stdout


@pytest.mark.parametrize(
    ("scanner", "database", "why"),
    [
        (False, True, "osv-scanner is not installed: brew install osv-scanner"),
        (True, False, "no offline database yet: scripts/vuln-scan.sh --download"),
    ],
    ids=["no scanner", "no database"],
)
def test_without_scanner_or_database_it_stops_or_verify_skips_in_one_line(
    tmp_path: Path, scanner: bool, database: bool, why: str
) -> None:
    env = tree(tmp_path, scanner=scanner, database=database)
    stopped = scan(env)
    assert stopped.returncode == 2 and why in stopped.stdout
    skipped = scan(env, "--if-ready")
    assert skipped.returncode == 0
    assert skipped.stdout.strip().splitlines() == [f"○ Known security holes: not checked ({why})"]


def test_an_unknown_argument_is_refused(tmp_path: Path) -> None:
    done = scan(tree(tmp_path), "--online")
    assert done.returncode == 2 and "usage: scripts/vuln-scan.sh [--download | --if-ready]" in done.stderr


def test_verify_runs_it_offline_when_it_can() -> None:
    verify = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")
    assert "scripts/vuln-scan.sh --if-ready" in verify
    assert "--download" not in verify  # verify never goes online


def test_the_release_steps_refresh_the_database_and_a_hit_blocks_the_release() -> None:
    release = (ROOT / "docs" / "agents" / "docs-release.md").read_text(encoding="utf-8")
    assert "`scripts/vuln-scan.sh --download`" in release and "a hit blocks the release" in release


def test_the_weekly_scan_on_github_is_pinned_read_only_and_reads_every_lockfile() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    pins = re.findall(r"uses: ([\w./-]+)@(\S+)", text)
    assert pins and all(re.fullmatch(r"[0-9a-f]{40}", sha) for _action, sha in pins), pins
    assert ("google/osv-scanner-action/osv-scanner-action", "b1d267aea5211fa648d9d442fa64b90dbdcbdd36") in pins
    assert re.search(r"^on:\n  push:\n(.|\n)*  schedule:\n    - cron: ", text, re.MULTILINE)
    assert re.search(r"^permissions:\n  contents: read\n(?!  )", text, re.MULTILINE)
    assert "write" not in text
    assert sorted(re.findall(r"--lockfile=(\S+)", text)) == LOCKS
    assert "persist-credentials: false" in text


def test_the_workflow_runs_on_a_fixed_image_and_holds_no_tab() -> None:
    """YAML refuses tabs; the runner image is a fixed one, as release.yml's."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "\t" not in text and "runs-on: ubuntu-24.04" in text


def test_security_says_how_holes_are_managed_and_what_is_not_covered() -> None:
    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    section = " ".join(text.split("\n## Vulnerability management\n", 1)[1].split("\n## ", 1)[0].split())
    for words in (
        "Every Python package is locked to one version and its fingerprint",
        "Updates never install themselves: a new version comes only with a release, after the tests.",
        "A version less than 7 days old comes only when it is its maintainers' fix for a known hole.",
        "(`scripts/vuln-scan.sh`). A release with a known hole is not published.",
        "Not covered: holes nobody has reported yet; the Swift packages, checked by hand at each release;",
        # what ships without a lockfile osv-scanner reads: the interpreter (deploy's `uv python install`), Ollama
        # (install-ollama.sh), the JVM signal-cli and the call helper (tools/call-tunnel)
        "Python itself, Ollama, signal-cli's build for calls and the call helper;",
        "the speaking voices (Kokoro, Supertonic-3), downloaded as published with no check of their files",
        # GitHub's own limit (docs.github.com, "Disabling and enabling a workflow")
        "GitHub pauses the weekly scan after 60 days without a change to the repository",
    ):
        assert words in section, words


def test_the_release_steps_name_what_the_scan_does_not_cover() -> None:
    release = (ROOT / "docs" / "agents" / "docs-release.md").read_text(encoding="utf-8")
    for words in ("Python itself", "Ollama", "the JVM signal-cli", "the call helper", "60 days"):
        assert words in release, words


# review X6 (0.9.7): the offline scan said "No known security hole" against a database of any age, and the release
# workflow ran no scan of its own


@pytest.mark.parametrize("mode", [(), ("--if-ready",)], ids=["offline", "verify"])
def test_the_pass_line_names_the_databases_day(tmp_path: Path, mode: tuple[str, ...]) -> None:
    env = tree(tmp_path, days_old=1)
    done = scan(env, *mode)
    day = (date.today() - timedelta(days=1)).isoformat()
    assert done.returncode == 0, done.stdout
    assert f"✓ No known security hole in the 4 locked package lists (the OSV database of {day}, offline)" in done.stdout
    assert "days old" not in done.stdout


def test_an_old_database_is_named_with_its_age(tmp_path: Path) -> None:
    done = scan(tree(tmp_path, days_old=9))
    assert done.returncode == 0
    assert "! The OSV database is 9 days old: scripts/vuln-scan.sh --download for a fresh one" in done.stdout


def test_after_a_download_the_database_is_todays(tmp_path: Path) -> None:
    done = scan(tree(tmp_path, days_old=30), "--download")  # the stand-in downloads nothing: the file stays as it was
    assert done.returncode == 0 and "(the OSV database of " in done.stdout


RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def test_the_release_runs_the_same_scan_on_its_tag_before_it_publishes() -> None:
    """A known hole stops the release on GitHub too: its scan job calls osv-scan.yml (the pinned scan of every push)
    on the tag, and the publishing job needs it; the publishing job still runs only GitHub's own gh."""
    release = RELEASE.read_text(encoding="utf-8")
    scan_job = release.split("\n  scan:\n", 1)[1].split("\n  release:\n", 1)[0]
    assert "uses: ./.github/workflows/osv-scan.yml" in scan_job
    assert "ref: ${{ inputs.tag || github.ref_name }}" in scan_job and "contents: read" in scan_job
    publish = release.split("\n  release:\n", 1)[1]
    assert "needs: scan" in publish and "uses:" not in publish and "gh release create" in publish
    assert "contents: write" in publish and "contents: write" not in release.split("\njobs:\n", 1)[0]
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^  workflow_call:\n    inputs:\n      ref:\n", workflow, re.MULTILINE)
    assert "ref: ${{ inputs.ref }}" in workflow  # the tag's lock files, never the default branch's

"""tools/call-tunnel/install-signal-cli-jvm.sh: the release is unpacked only when the maintainer's key and the
signature's signer both have the pinned fingerprint, and, for a version with a pinned sha256, the tarball has it. The
downloads are public (no GitHub account, no gh): /usr/bin/curl at pinned addresses. Run with a stub curl under
ESTIA_TEST_ROOT (never the network) and stub gpg and brew on PATH, into a temporary folder."""

import hashlib
import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "call-tunnel" / "install-signal-cli-jvm.sh"
PINNED = "FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570"
OTHER = "0123456789ABCDEF0123456789ABCDEF01234567"
VERSION = "0.14.9"
# signal-cli 0.14.9's release tarball, downloaded 25 Oct 2026: its signature checked against the pinned key, and
# GitHub's own asset digest says the same
REAL_SHA256 = "c32b87f587198cbd277c9d71228716ee6047ee2b63e8e88728e8280d9407d65a"
RELEASES = "https://github.com/AsamK/signal-cli/releases/download"
KEY_URL = "https://github.com/AsamK.gpg"


def stub(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def release(path: Path, version: str) -> str:
    """A release tarball with a signal-cli launcher that prints its version; its sha256."""
    launcher = f"#!/bin/sh\necho 'signal-cli {version}'\n".encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(f"signal-cli-{version}/bin/signal-cli")
        info.size, info.mode = len(launcher), 0o755
        archive.addfile(info, io.BytesIO(launcher))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned_to(tmp_path: Path, sha256: str) -> Path:
    """A copy of the script whose 0.14.9 pin is `sha256` (the stub tarball's), in a checkout of its own."""
    text = SCRIPT.read_text()
    assert f"{VERSION}) SHA256={REAL_SHA256} ;;" in text
    copy = tmp_path / "checkout" / "tools" / "call-tunnel" / SCRIPT.name
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(text.replace(f"{VERSION}) SHA256={REAL_SHA256} ;;", f"{VERSION}) SHA256={sha256} ;;"))
    copy.chmod(0o755)
    return copy


def run(
    tmp_path: Path,
    *,
    key: str = PINNED,
    signer: str = PINNED,
    primary: str | None = None,
    java: bool = True,
    version: str = VERSION,
    pin: bool = True,
    failing: str = "",
) -> subprocess.CompletedProcess[str]:
    """The installer with stub tools; `pin`: the 0.14.9 pin is the stub tarball's sha256 (else the real one, which no
    stub tarball has); `failing`: curl fails (exit 22, an HTTP error) for the address that ends so."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    tarball = tmp_path / "release.tar.gz"
    sha256 = release(tarball, version)
    script = pinned_to(tmp_path, sha256) if pin else SCRIPT
    curl_dir = tmp_path / "root" / "usr" / "bin"
    curl_dir.mkdir(parents=True, exist_ok=True)
    stub(
        curl_dir,
        "curl",
        f'echo "curl $*" >> "{tmp_path / "curl.log"}"\n'
        'out=""; url=""; prev=""\n'
        'for arg in "$@"; do [ "$prev" = -o ] && out=$arg; url=$arg; prev=$arg; done\n'
        f'case "$url" in *"{failing or "-no-failure-"}") exit 22 ;; esac\n'
        'case "$url" in\n'
        f'  */signal-cli-{version}.tar.gz) cp "{tarball}" "$out" ;;\n'
        '  *.asc) echo sig > "$out" ;;\n'
        "  *.gpg) echo '-----BEGIN PGP PUBLIC KEY BLOCK-----' > \"$out\" ;;\n"
        "  *) exit 22 ;;\n"
        "esac",
    )
    validsig = f"{signer} 2026-01-01 1767225600 0 4 0 1 10 00" + (f" {primary}" if primary else "")
    stub(
        bin_dir,
        "gpg",
        f'echo "gpg $*" >> "{tmp_path / "gpg.log"}"\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        f'    --fingerprint) printf "pub:-:4096:1:AAAA:1:::-:::scESC::::::23::0:\\nfpr:::::::::{key}:\\n"; exit 0 ;;\n'
        f'    --verify) echo "[GNUPG:] GOODSIG AAAA AsamK"; echo "[GNUPG:] VALIDSIG {validsig}"; exit 0 ;;\n'
        "  esac\n"
        "done\n"
        "exit 0",
    )
    stub(bin_dir, "brew", f'echo "{tmp_path / "brew"}"')
    stub(bin_dir, "gh", f'echo "gh $*" >> "{tmp_path / "gh.log"}"; exit 1')  # never needed
    if java:
        (tmp_path / "brew" / "opt" / "openjdk" / "bin").mkdir(parents=True, exist_ok=True)
        stub(tmp_path / "brew" / "opt" / "openjdk" / "bin", "java", "exit 0")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "LOCAL_ASSISTANT_VENDOR": str(tmp_path / "vendor"),
        "TMPDIR": str(tmp_path),
        "ESTIA_TEST_ROOT": str(tmp_path / "root"),  # its curl is the stub: never the network
    }
    args = [] if version == VERSION else [version]
    return subprocess.run(["/bin/sh", str(script), *args], env=env, capture_output=True, text=True, timeout=60)


def unpacked(tmp_path: Path, version: str = VERSION) -> Path:
    return tmp_path / "vendor" / f"signal-cli-{version}-jvm" / f"signal-cli-{version}" / "bin" / "signal-cli"


def logged(tmp_path: Path, name: str) -> str:
    path = tmp_path / f"{name}.log"
    return path.read_text() if path.exists() else ""


def test_the_pinned_fingerprint_is_in_the_script() -> None:
    assert f"FINGERPRINT={PINNED}" in SCRIPT.read_text()


def test_the_pinned_sha256_is_the_real_releases() -> None:
    """The pin was computed from the real download (25 Oct 2026), whose signature checked out against the pinned key."""
    assert f"{VERSION}) SHA256={REAL_SHA256} ;;" in SCRIPT.read_text()


@pytest.mark.parametrize("primary", [None, PINNED])
def test_the_pinned_key_and_signer_unpack_the_release(tmp_path: Path, primary: str | None) -> None:
    done = run(tmp_path, key=PINNED, signer=PINNED, primary=primary)
    assert done.returncode == 0, done.stderr
    assert unpacked(tmp_path).is_file()
    assert f"signal-cli {VERSION}" in done.stdout


def test_a_subkey_signature_counts_by_its_primary_key(tmp_path: Path) -> None:
    done = run(tmp_path, key=PINNED, signer=OTHER, primary=PINNED)
    assert done.returncode == 0, done.stderr
    assert unpacked(tmp_path).is_file()


@pytest.mark.parametrize(
    ("key", "signer", "primary"),
    [
        (OTHER, PINNED, None),  # GitHub served another key
        (PINNED, OTHER, None),  # signed by another key
        (PINNED, OTHER, OTHER),
    ],
)
def test_any_other_fingerprint_stops_before_unpacking(
    tmp_path: Path, key: str, signer: str, primary: str | None
) -> None:
    done = run(tmp_path, key=key, signer=signer, primary=primary)
    assert done.returncode == 1
    assert "fingerprint" in done.stderr
    assert not unpacked(tmp_path).exists()
    assert not unpacked(tmp_path).parents[2].exists()  # nothing half-made: a run again is not blocked (finding 13)


def test_the_downloads_need_no_github_account(tmp_path: Path) -> None:
    """Owner, 25 Oct: the release, its signature and the maintainer's key are public downloads, so calls need no
    GitHub account: gh is never run, nor named."""
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert logged(tmp_path, "gh") == ""
    assert "gh " not in done.stdout + done.stderr and "GitHub account" not in done.stdout + done.stderr
    text = SCRIPT.read_text()
    assert "gh auth" not in text and "gh release" not in text and "gh api" not in text


def test_curl_fetches_only_the_pinned_addresses_as_the_repos_other_downloads(tmp_path: Path) -> None:
    """/usr/bin/curl by its full path (a test: under ESTIA_TEST_ROOT), never ~/.curlrc (-q first), failing on an HTTP
    error (-f), HTTPS only, with time limits; the release's two files and the maintainer's key, nothing else."""
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    calls = logged(tmp_path, "curl").splitlines()
    urls = [line.rsplit(" ", 1)[1] for line in calls]
    assert urls == [
        f"{RELEASES}/v{VERSION}/signal-cli-{VERSION}.tar.gz",
        f"{RELEASES}/v{VERSION}/signal-cli-{VERSION}.tar.gz.asc",
        KEY_URL,
    ]
    for line in calls:
        words = line.split()
        assert words[1] == "-q", line
        assert "-f" in words[2] or "-f" in words, line
        assert "--proto =https" in line and "--proto-redir =https" in line, line
        assert "--connect-timeout" in words and "--max-time" in words and "--max-filesize" in words, line
    assert 'CURL="${ESTIA_TEST_ROOT:-}/usr/bin/curl"' in SCRIPT.read_text()


def test_a_tarball_without_the_pinned_sha256_stops_before_unpacking(tmp_path: Path) -> None:
    """A tarball other than the one pinned stops the install, even with a good signature by the pinned key."""
    done = run(tmp_path, pin=False)
    assert done.returncode == 1
    assert "sha256" in done.stderr and REAL_SHA256 in done.stderr
    assert not unpacked(tmp_path).exists()
    assert not (tmp_path / "vendor").exists()
    assert "--verify" not in logged(tmp_path, "gpg")  # refused before gpg reads anything


def test_a_version_without_a_pin_is_checked_by_its_signature_alone_and_says_so(tmp_path: Path) -> None:
    """Homebrew's signal-cli may be newer than the pin: that version installs on the signature check alone, said."""
    done = run(tmp_path, version="0.14.10", pin=False)
    assert done.returncode == 0, done.stderr
    assert unpacked(tmp_path, "0.14.10").is_file()
    assert "no sha256 pin for signal-cli 0.14.10" in done.stderr
    assert "--verify" in logged(tmp_path, "gpg")
    stopped = run(tmp_path / "bad", version="0.14.10", pin=False, signer=OTHER)
    assert stopped.returncode == 1 and "fingerprint" in stopped.stderr


@pytest.mark.parametrize("failing", [".tar.gz", ".tar.gz.asc", ".gpg"])
def test_a_failed_download_stops_and_leaves_nothing_behind(tmp_path: Path, failing: str) -> None:
    done = run(tmp_path, failing=failing)
    assert done.returncode == 1
    assert "download failed" in done.stderr
    assert not (tmp_path / "vendor").exists()
    again = run(tmp_path)
    assert again.returncode == 0, again.stderr
    assert unpacked(tmp_path).is_file()


def test_a_four_part_version_installs_as_signal_cli_has_shipped_one(tmp_path: Path) -> None:
    """Review of P1, R3-F1: signal-cli has shipped a four-part version (0.14.4.1, doctor's table names it), and the
    gh-based script before took any tag: three or four numbers go."""
    done = run(tmp_path, version="0.14.9.1", pin=False)
    assert done.returncode == 0, done.stderr
    assert unpacked(tmp_path, "0.14.9.1").is_file()
    assert f"{RELEASES}/v0.14.9.1/signal-cli-0.14.9.1.tar.gz" in logged(tmp_path, "curl")


@pytest.mark.parametrize(
    "version",
    ["0.14", "0.14.9; touch x", "../0.14.9", "v0.14.9", "1.2.3.4.5", "0.14.9_1", "0.14.9\n../x", "1..2.3", "0.14.9."],
)
def test_a_version_that_is_not_three_or_four_numbers_is_refused_before_any_download(
    tmp_path: Path, version: str
) -> None:
    """The version goes into the download's address: only digits and two or three dots. Homebrew's revision
    (0.14.9_1) is no release: setup_assistant.md's step drops it first."""
    done = subprocess.run(
        ["/bin/sh", str(SCRIPT), version],
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "LOCAL_ASSISTANT_VENDOR": str(tmp_path / "vendor"),
            "ESTIA_TEST_ROOT": str(tmp_path / "root"),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 2 and "three or four numbers, such as 0.14.9" in done.stderr
    assert not (tmp_path / "vendor").exists() and not (tmp_path / "x").exists()


def test_java_is_checked_before_the_download(tmp_path: Path) -> None:
    done = run(tmp_path, java=False)
    assert done.returncode == 1 and "brew install openjdk" in done.stderr
    assert not (tmp_path / "vendor").exists()
    assert logged(tmp_path, "curl") == ""


def test_the_usage_stays_version_and_target_folder() -> None:
    text = SCRIPT.read_text()
    assert "Usage: tools/call-tunnel/install-signal-cli-jvm.sh [version] [target dir]" in text
    assert 'VENDOR="${LOCAL_ASSISTANT_VENDOR:-' in text
    assert os.access(SCRIPT, os.X_OK)


def test_the_builds_go_into_the_checkouts_vendor_folder_by_default() -> None:
    """Her own user's release copies the call helpers from the checkout's vendor/ (gitignored), the first place
    estia-deploy looks; her old data folder is held away from you after the move."""
    for script in (SCRIPT, SCRIPT.with_name("build.sh")):
        text = script.read_text()
        assert 'REPO=$(cd "$(dirname "$0")/../.." && pwd)' in text, script.name
        assert 'VENDOR="${LOCAL_ASSISTANT_VENDOR:-$REPO/vendor}"' in text, script.name
        assert "Application Support" not in text, script.name


def test_the_setup_guides_step_gives_the_installer_the_version_homebrew_links(tmp_path: Path) -> None:
    """Review of P1, R3-F1: `brew list --versions signal-cli` can print a Homebrew revision ("0.14.9_1"), which no
    release has; second deep scan, signal-05: it names every keg kept too, oldest first or not. setup_assistant.md's
    step reads the version from the signal-cli on the PATH, the keg Homebrew links, as setup and scripts/setup.sh do."""
    guide = (SCRIPT.parents[2] / "setup_assistant.md").read_text().splitlines()
    at = guide.index("   v=$(signal-cli --version | awk '{print $2}')")
    step = guide[at].strip()
    assert guide[at + 1].strip() == 'tools/call-tunnel/install-signal-cli-jvm.sh "$v"'
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub(bin_dir, "brew", 'echo "signal-cli 0.14.8_1 0.14.9"')  # two kegs kept: never asked
    for listed, version in (("signal-cli 0.14.9", "0.14.9"), ("signal-cli 0.14.4.1", "0.14.4.1")):
        stub(bin_dir, "signal-cli", f'echo "{listed}"')
        done = subprocess.run(
            ["/bin/sh", "-c", f'{step}\nprintf "%s" "$v"'],
            env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert done.stdout == version, (listed, done.stdout, done.stderr)

"""gum (charmbracelet/gum, MIT), the menus setup draws in Terminal: one pinned release for Apple silicon, downloaded
once from GitHub into setup's own folder and checked by its fingerprint (sha256), the tarball's and the program's,
before every run of setup. Homebrew's gum, if any, is never used. Any mismatch, a failed download or no checked gum:
plain lines, with one line saying why. The release's sigstore bundle is not checked (no cosign on a Mac): the pin is
the check. gum never sees a secret or an answer of yours (setup_face.GumFace)."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path

from local_assistant import hostsec, prompts
from local_assistant.setup_answers import private_folders

VERSION = "2.0.2"  # charmbracelet/gum, released 24 Sep 2026
TARBALL = f"gum_{VERSION}_Darwin_arm64.tar.gz"
URL = f"https://github.com/charmbracelet/gum/releases/download/v{VERSION}/{TARBALL}"
TARBALL_SHA256 = "4777a69b1170b8db23c95d5889fb32186cfda1a3ac950d339aa17e3513633890"  # its checksums.txt, read 26 Oct
BINARY_SHA256 = "74cab6235944c0e39e69edfceef81150064687dd29cacec5ec1e954637f55d0c"  # the gum in that tarball, 26 Oct
MEMBER = f"gum_{VERSION}_Darwin_arm64/gum"  # the one member taken from the tarball; the others are never unpacked
MAX_BYTES = 64 * 1024 * 1024  # gum 2.0.2 is 13.9 MB
TARBALL_BYTES = 20 * 1024 * 1024  # curl stops a download larger than this (the release's is 4.7 MB)
THEME = {"accent": "#ff5c35", "muted": "#8d9298"}  # the mark's orange (the cursor) and its ring; text keeps its colour


class Refused(Exception):
    """A download or an installed gum that does not check out: why, in a few words."""


def folder() -> Path:
    """Setup's own folder for gum (tests point it elsewhere: tests/conftest.py)."""
    return Path.home() / "Library" / "Caches" / "estia-setup" / f"gum-{VERSION}"


def curl_argv(target: Path) -> list[str]:
    """The download with the Mac's own curl: HTTPS only, redirects too (GitHub sends it on to its file host), never
    ~/.curlrc (-q, first); a stalled line, a file far larger than the release or two minutes end it."""
    return [
        str(hostsec.tool_root() / "usr/bin/curl"),
        "-q",
        "-fsSL",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--retry",
        "3",
        "--connect-timeout",
        "20",
        "--speed-limit",
        "10240",
        "--speed-time",
        "60",
        "--max-time",
        "120",
        "--max-filesize",
        str(TARBALL_BYTES),
        "-o",
        str(target),
        URL,
    ]


def program(*, dry_run: bool, say: Callable[[str], None]) -> Path | None:
    """gum, checked: the one in setup's folder, or downloaded once; None (plain lines) when there is none that checks
    out, said in one line. A dry run only shows the download."""
    binary = folder() / "gum"
    if dry_run:
        if not os.path.lexists(binary):
            say(
                prompts.SETUP_GUM_DRY_RUN.format(
                    version=VERSION, command=" ".join(curl_argv(Path("<new folder>/gum.tgz")))
                )
            )
        return None
    try:
        if os.path.lexists(binary):
            _check(binary)
            return binary
        say(prompts.SETUP_GUM_DOWNLOADING.format(version=VERSION))  # over SSH it comes before the first question
        _download(binary)
    except Refused as refused:
        say(prompts.SETUP_GUM_PLAIN.format(why=refused))
        return None
    say(prompts.SETUP_GUM_READY.format(version=VERSION))
    return binary


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check(binary: Path) -> None:
    """The installed gum: a file of yours alone, not a link, the pinned program byte for byte."""
    try:
        fd = os.open(binary, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise Refused(prompts.SETUP_GUM_NOT_FILE) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Refused(prompts.SETUP_GUM_NOT_YOURS)
        data = _read_all(fd)
    finally:
        os.close(fd)
    if _sha256(data) != BINARY_SHA256:
        raise Refused(prompts.SETUP_GUM_CHANGED)


def _read_all(fd: int) -> bytes:
    chunks: list[bytes] = []
    while chunk := os.read(fd, 1 << 20):
        chunks.append(chunk)
    return b"".join(chunks)


def _download(binary: Path) -> None:
    """The release into a new empty folder beside setup's, its tarball checked, the one gum member read from it and
    checked, then moved into place; the folder goes, whatever happens."""
    private_folders(binary.parent)
    work = Path(tempfile.mkdtemp(prefix=".download-", dir=binary.parent))
    try:
        tarball = work / TARBALL
        try:
            done = subprocess.run(curl_argv(tarball), stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
        except (OSError, subprocess.SubprocessError):
            raise Refused(prompts.SETUP_GUM_FAILED) from None
        if done.returncode != 0 or not tarball.is_file():
            raise Refused(prompts.SETUP_GUM_FAILED)
        if _sha256(tarball.read_bytes()) != TARBALL_SHA256:
            raise Refused(prompts.SETUP_GUM_NOT_PINNED.format(version=VERSION))
        data = _member(tarball)
        if _sha256(data) != BINARY_SHA256:
            raise Refused(prompts.SETUP_GUM_NOT_PINNED.format(version=VERSION))
        staged = work / "gum"
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        staged.chmod(0o700)
        os.replace(staged, binary)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _member(tarball: Path) -> bytes:
    """The bytes of the tarball's one gum, read in memory (nothing is unpacked): exactly one member by that name, a
    regular file, at most MAX_BYTES."""
    try:
        with tarfile.open(tarball, "r:gz") as archive:
            found = [member for member in archive.getmembers() if member.name == MEMBER]
            if len(found) != 1 or not found[0].isreg() or found[0].size > MAX_BYTES:
                raise Refused(prompts.SETUP_GUM_NO_PROGRAM)
            handle = archive.extractfile(found[0])
            data = handle.read(MAX_BYTES + 1) if handle is not None else b""
    except (OSError, tarfile.TarError, EOFError):
        raise Refused(prompts.SETUP_GUM_NO_PROGRAM) from None
    if not data or len(data) > MAX_BYTES:
        raise Refused(prompts.SETUP_GUM_NO_PROGRAM)
    return data

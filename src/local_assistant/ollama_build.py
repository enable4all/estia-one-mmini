"""Ollama's official build: the one setup keeps or installs, and the one doctor expects (owner, 15 Oct: "ollama we keep
use only the official build").

Why: Homebrew's Ollama has no `libollama_xgrammar.dylib`, so Ollama's engine for Apple silicon (MLX) gives no
JSON-schema output, which she needs; Ollama's own build ships it next to the program, in `mlx_metal_v3` and
`mlx_metal_v4`. The Ollama app holds the same files, but updates itself every hour, starts only when someone logs in
on the Mac's screen, and stops every other `ollama serve` when its own server cannot start.

A signature counts only when Apple's Developer ID chain ends in Ollama's team (`REQUIREMENT`, `codesign -R`): a team
ID alone is a name anyone can put in an ad-hoc signature.

`Install.run`: an Ollama that already answers is kept when its program and every library and metallib in its folder
are signed by Ollama's developer ID (`TEAM_ID`), its version is `MIN_VERSION` or later, both xgrammar libraries sit
next to it and it listens only on this Mac; Homebrew's, the app's or any other stops setup with what is wrong and the
fix (the person runs it), as does a launchd job of Homebrew's or the app's that starts Ollama at a login. Setup's own
service running an older or newer version than the pin is updated in place: the new release downloaded and checked
while the old one runs, `current` switched, the service started again, and switched back when the new one does not
answer. Nothing answering: another launchd job that runs Ollama stops setup (two always-on services would fight for
the port); else the pinned release (`RELEASE`) is downloaded from GitHub into a new, empty folder, checked (its sha256;
every program's, library's and metallib's signature; Apple's notarization of `ollama`; both xgrammar libraries), and
only then moved to `~/opt/ollama/v<version>`, with `~/opt/ollama/current` pointing at it (no admin rights). A
LaunchDaemon (`LABEL`, the system domain) then runs it as the owner from boot, on 127.0.0.1 only: every command names
its tool by full path, the service file on tee's input (never a file of the owner's that a program could swap
meanwhile), the folders on the way to the program the owner's alone, and its log the owner's, 0600, made by root
before launchd opens it.

Read-only checks (the listening program, its signature, its libraries, the launchd jobs) run in a dry run too; every
change is printed instead, and what would stop setup is printed with the rest after it. Off the live Mac (a test's
ESTIA_ROOT) nothing is read or run: no Ollama answers and every change is printed.
"""

from __future__ import annotations

import argparse
import contextlib
import grp
import hashlib
import itertools
import json
import math
import os
import plistlib
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from local_assistant import gemma, ownuser, prompts, setup_progress
from local_assistant.hostsec import (
    CHMOD,
    CHOWN,
    CODESIGN,
    INSTALL,
    LAUNCHCTL,
    LSOF,
    NETSTAT,
    PS,
    RM,
    SUDO,
    TAR,
    Host,
    estia_root,
    netstat_blind,
)
from local_assistant.ownuser import STREAM_LOGS

CURL = "/usr/bin/curl"
SPCTL = "/usr/sbin/spctl"
TEE = "/usr/bin/tee"
SHASUM = "/usr/bin/shasum"
MKDIR = "/bin/mkdir"
MV = "/bin/mv"
LN = "/bin/ln"
TEAM_ID = "3MU9H2V9Y9"  # Developer ID Application: Infra Technologies, Inc (Ollama)
# Apple's Developer ID chain (Apple's root, the Developer ID authority, a Developer ID Application leaf) with Ollama's
# team as the leaf's OU: `codesign --verify --strict -R` refuses an ad-hoc signature that only claims the team ID
REQUIREMENT = (
    "=anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate "
    f'leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = "{TEAM_ID}"'
)
REQUIREMENT_NOT_MET = 3  # codesign's exit code for a valid signature that fails the requirement
MIN_VERSION = (0, 33, 1)  # v0.33.1: JSON-schema output on the MLX engine ("mlxrunner: add structured output support")
MIN_TEXT = ".".join(str(part) for part in MIN_VERSION)


@dataclass(frozen=True)
class Release:
    """One release of Ollama's macOS tarball, pinned by its sha256 (GitHub's asset digest)."""

    version: str
    sha256: str

    @property
    def url(self) -> str:
        return f"https://github.com/ollama/ollama/releases/download/v{self.version}/ollama-darwin.tgz"


# the only version tested live (15 Oct: 60.2 tok/s, tools and JSON fine); a new one comes with a commit that changes
# this pin, after the smoke test and the Gemma test set pass on it
RELEASE = Release("0.40.4", "a4ceae4b2aaf18df37ebcbfb56ba3a37a87838631112d66c2be07268693c8d58")
RELEASE_MB = 168  # ollama-darwin.tgz v0.40.4: 167.9 MB
RELEASE_BYTES = 167_900_000  # about its size: setup's five dots read the download against it (the fifth: once done)
LABEL = "com.local-assistant.ollama"
PORT = 11434
BASE = f"http://127.0.0.1:{PORT}"
DAEMON = Path("/Library/LaunchDaemons") / f"{LABEL}.plist"
# root's: launchd opens and appends to the log as the job's user (UserName: you, or her account for her own Ollama),
# never as root, so the log must be there and that user's before launchd starts the job (M01, 30 Oct: with none, the
# start failed with exit 78); this root-owned folder is what stops a link being planted at that name
LOG_DIR = STREAM_LOGS
LOG = LOG_DIR / "ollama.log"
LOG_CAP = 10 * 1024 * 1024  # setup empties a larger log of yours (launchd appends: writing goes on at the new end)
LAUNCH_DAEMONS = Path("/Library/LaunchDaemons")
PLIST_BYTES = 1_000_000
XGRAMMAR = ("mlx_metal_v3/libollama_xgrammar.dylib", "mlx_metal_v4/libollama_xgrammar.dylib")
CELLARS = (Path("/opt/homebrew/Cellar"), Path("/usr/local/Cellar"))  # Homebrew on Apple silicon, and on Intel
APP = Path("/Applications/Ollama.app")
APP_PROGRAM = Path("Contents/Resources/ollama")  # the app's own ollama program, inside it
OPEN = "/usr/bin/open"
# the tested setup's (local-llm, with its watchdog: none here, see the docs' known gaps); every request names it too
# (gemma.KEEP_ALIVE), so it holds on the Ollama app, which reads no service file of setup's
KEEP_ALIVE = gemma.KEEP_ALIVE
WAIT_SECONDS = 60
# the Ollama app's first open: macOS's "downloaded from the Internet, Open?" and the app's welcome window come first
# (mac-ollama-app-first-open, round 1): 3 minutes, a still-waiting line every 15 s
APP_WAIT_SECONDS = 180
APP_WAITING_EVERY = 15
PATH_LINE = 'export PATH="$HOME/opt/ollama/current:$PATH"'
# Her own Ollama (owner, 29 Oct: "Her own, in _estia"): a new install that says yes to her own macOS account gets
# Ollama's official build as a copy only admin rights can change, run as _estia from the Mac's start, on an address of
# her own on the Mac itself, her model in her own folder. Every other install keeps today's ("older": ~/opt/ollama as
# you on 11434), byte for byte; "app" is the Ollama app the person chose (11434 too).
Layout = Literal["hers", "older", "app"]
# the kill switch: the layout a new install with her own account gets. "older" ships today's path for every new
# install. Its gate, Metal for a LaunchDaemon as _estia (/api/ps: size_vram == size), was measured on 30 Oct (M01, a
# Mac mini with macOS 27.0.1, Ollama 0.40.4, gemma4:12b-mlx: size_vram == size == 7750467524, "library=Metal"): passed,
# so "hers". The record: docs/e2e-checklist.md, 111a
NEW_INSTALLS: Layout = "hers"
# her address: not 11434 (Ollama's default: the app's, Homebrew's, anyone's own) nor 11435-11439 (a second Ollama's
# usual place); in no /etc/services entry, below macOS's ephemeral range, next to her voice service's 8150
HER_PORT = 8151
HER_BASE = f"http://127.0.0.1:{HER_PORT}"
HER_FOLDER = ownuser.CODE / "ollama"  # root's copy: v<version> folders and `current`, root:wheel, 0755/0644
HER_PROGRAM = HER_FOLDER / "current" / "ollama"
HER_MODELS = ownuser.HOME / ".ollama" / "models"  # hers alone (0700: her service's Umask)
CURL_WRITE_FAILED = 23  # curl's exit code when it cannot write what it downloads: a full disk, as a rule
HER_ROOM = 3_000_000_000  # free disk her Ollama needs besides her model: 548 MB unpacked, the download, 2 GB to spare
HER_CACHE = Path("Library/Caches/estia-setup")  # your download of the release, in your home, before root's copy
TAR_AS_ROOT = ("--no-same-owner", "--no-same-permissions", "--no-mac-metadata")  # root unpacks as your tar would
_SIGNED_SUFFIXES = (".dylib", ".so", ".metallib")
_MACHO = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
}
# a listening socket in `netstat -anv -p tcp`: its local address (the port after the last dot), then its process:pid
_LISTEN = re.compile(r"^tcp[46]*\s+\d+\s+\d+\s+(\S+)\s+\S+\s+LISTEN\s+(?:\d+\s+){4}(.+?):(\d+)\s")
# `launchctl print system/<label>` (every account may read it): the job's own pid and environment, one tab in
_LAUNCHD_PID = re.compile(r"^\tpid = (\d+)$", re.M)
_LAUNCHD_ENVIRONMENT = re.compile(r"^\tenvironment = \{\n(.*?)^\t\}$", re.M | re.S)
_LAUNCHD_HOST = re.compile(r"^\t\tOLLAMA_HOST => (.*)$", re.M)
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """An Ollama version as three numbers ("0.40.4-rc1" is 0.40.4); None when it is not one."""
    found = _VERSION.match(text.strip())
    return (int(found[1]), int(found[2]), int(found[3])) if found else None


def team_of(codesign: str) -> str:
    """The TeamIdentifier `codesign -dv` names ("" for none: unsigned, ad-hoc or Apple's own). Only a name: the
    requirement (`REQUIREMENT`) is what proves it."""
    for line in codesign.splitlines():
        if line.startswith("TeamIdentifier="):
            value = line.removeprefix("TeamIdentifier=").strip()
            return "" if value == "not set" else value
    return ""


def signature_problem(read: Callable[[Sequence[str]], tuple[int, str]], path: str) -> str:
    """Why a file is not signed by Ollama's developer ID ("" when it is): the requirement first (Apple's Developer ID
    chain with Ollama's team), then `codesign -dv` only to name the team in the reason."""
    code, _ = read([CODESIGN, "--verify", "--strict", "-R", REQUIREMENT, path])
    if code == 0:
        return ""
    if code != REQUIREMENT_NOT_MET:
        return prompts.OLLAMA_WHY_UNSIGNED
    _, shown = read([CODESIGN, "-dv", path])
    team = team_of(shown)
    if team != TEAM_ID:
        return prompts.OLLAMA_WHY_TEAM.format(team=team or "no developer ID", want=TEAM_ID)
    return prompts.OLLAMA_WHY_NOT_APPLE


def owner_home() -> Path:
    """Your home from the user database, never $HOME: root's service file runs the program under it at every start."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def homebrews(program: Path) -> bool:
    return any(program.is_relative_to(cellar) for cellar in CELLARS)


def app_of(program: Path) -> Path | None:
    """The .app bundle a program runs from (the Ollama app's server is Ollama.app/Contents/Resources/ollama)."""
    return next((folder for folder in (program, *program.parents) if folder.suffix == ".app"), None)


def libraries_in(folder: Path) -> tuple[list[str], list[str]]:
    """The official build's layout, the one definition doctor and setup share: both xgrammar libraries as files in
    the program's own folder, as Ollama's tarball has them (`XGRAMMAR`). Returns the ones not there, and the ones that
    could not be looked at (a folder closed to the account that asks)."""
    missing: list[str] = []
    unread: list[str] = []
    for library in XGRAMMAR:
        try:
            info = os.stat(folder / library)
        except (FileNotFoundError, NotADirectoryError):
            missing.append(library)
            continue
        except OSError:
            unread.append(library)
            continue
        if not stat.S_ISREG(info.st_mode):
            missing.append(library)
    return missing, unread


def missing_libraries(program: str) -> tuple[list[str], bool]:
    """The xgrammar libraries not next to the program (`libraries_in`), and whether one could not be read: a folder
    closed to the account doctor runs as is never "missing"."""
    missing, unread = libraries_in(Path(program).parent)
    return missing, bool(unread)


# another program or account on Ollama's port: (its program, "" when not found; its uid, None when not read)
Other = tuple[str, int | None]


@dataclass(frozen=True)
class Listener:
    """The program listening on Ollama's port ("" when none is found), the local addresses on that port, as netstat
    shows them ("127.0.0.1.11434", "::1.11434", "*.11434"), the account that runs it (its uid; None: not read), and
    every other program or account that listens on the port too (127.0.0.1 and ::1 are two addresses: one Ollama on
    each). `others_unread`: netstat's list could not be read (`hostsec.netstat_blind`: none at all, seen on macOS
    27.0.1 on 3 Nov), so another account's program on the port could not be listed; doctor's line says so. It is set
    on an empty answer too (fix review 1): no program found then may be another account's."""

    program: str
    addresses: tuple[str, ...] = ()
    uid: int | None = None
    others: tuple[Other, ...] = ()
    others_unread: bool = False


def host_address(value: str, port: int) -> str | None:
    """OLLAMA_HOST ("127.0.0.1:8151", "http://0.0.0.0:8151", "[::1]:8151") as netstat shows its local address
    ("127.0.0.1.8151", "*.8151", "::1.8151"); None when it names another port or none (Ollama's default: 11434)."""
    text = value.strip()
    try:
        parts = urllib.parse.urlsplit(text if "://" in text else f"//{text}")
        named = parts.port
    except ValueError:
        return None
    if named != port:
        return None
    host = parts.hostname or ""
    return f"{'*' if host in ('', '0.0.0.0', '::') else host}.{port}"


def other_machines(addresses: Sequence[str], port: int = PORT) -> list[str]:
    """The addresses other machines can reach: all but 127.0.0.1 and ::1 (this Mac only)."""
    return [address for address in addresses if address not in (f"127.0.0.1.{port}", f"::1.{port}")]


@dataclass(frozen=True)
class Found:
    """The Ollama that answers: its program ("" when not found), its version (None: not read), why its signature is
    not Ollama's ("" when it is; None: not checked, as doctor does not), where it listens (None: not read), the
    account that runs it (its uid; None: not read) and any other program or account on its port (`Listener.others`)."""

    program: str
    version: str | None
    signature: str | None = None
    addresses: tuple[str, ...] | None = None
    uid: int | None = None
    others: tuple[Other, ...] = ()


def account_name(uid: int) -> str:
    """An account's short name ("uid 503" when the user database has none)."""
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid {uid}"


def app_on_mac(root: Path, home: Path | None) -> Path | None:
    """The Ollama app on this Mac: /Applications/Ollama.app (under `root`), else your own ~/Applications/Ollama.app
    (`home`; None off the live Mac, where no home is read). None when neither is there. Read-only, no link
    followed."""
    if os.path.lexists(root / APP.relative_to("/")):
        return APP
    if home is not None and os.path.lexists(home / "Applications/Ollama.app"):
        return home / "Applications/Ollama.app"
    return None


def problem(
    found: Found, owner: int | None = None, *, app_ok: bool = False, port: int = PORT, root: Path = Path("/")
) -> tuple[str, str] | None:
    """What makes an Ollama that answers one setup cannot keep, and its fix; None when it is the official build.
    `owner`: your account's uid (her own Ollama's account for hers: `owner_of`); an Ollama another account runs gets
    her prompts, so that account controls her answers (not checked when either is unknown). `app_ok`: the person chose
    the Ollama app (owner, 29 Oct), so the app's is checked like the official build, by everything but its being the
    app. `port`: the one it answers on (hers: HER_PORT); `root`: the Mac's tree its libraries are looked for in (a
    test's own)."""
    if not found.program:
        return prompts.OLLAMA_NOT_FOUND, prompts.OLLAMA_NOT_FOUND_FIX
    program = Path(found.program)
    if homebrews(program):
        return prompts.OLLAMA_HOMEBREW.format(program=program), prompts.OLLAMA_HOMEBREW_FIX
    app = app_of(program)
    if app is not None and not app_ok:
        return prompts.OLLAMA_APP.format(state="answers", path=app), prompts.OLLAMA_APP_FIX.format(path=app)
    reasons: list[str] = []
    if found.signature:
        reasons.append(found.signature)
    if found.version is not None:
        parsed = parse_version(found.version)
        if parsed is None or parsed < MIN_VERSION:
            reasons.append(prompts.OLLAMA_WHY_OLD.format(version=found.version or "unknown", needed=MIN_TEXT))
    on_disk = root / program.relative_to("/") if program.is_absolute() else program
    missing, _unread = missing_libraries(str(on_disk))
    reasons += [prompts.OLLAMA_WHY_LIBRARY.format(library=library) for library in missing]
    reachable = other_machines(found.addresses or (), port)
    if reachable:
        reasons.append(prompts.OLLAMA_WHY_OPEN.format(addresses=", ".join(reachable), port=port))
    if found.uid is not None and owner is not None and found.uid != owner:
        reasons.append(prompts.OLLAMA_WHY_ACCOUNT.format(account=account_name(found.uid)))
    for other, uid in found.others:
        reasons.append(
            prompts.OLLAMA_WHY_SHARED.format(
                program=other or prompts.OLLAMA_SHARED_NOT_FOUND,
                account=prompts.OLLAMA_SHARED_UNREAD if uid is None else account_name(uid),
            )
        )
    if reasons:
        return prompts.OLLAMA_OTHER.format(program=program, why="; ".join(reasons)), prompts.OLLAMA_OTHER_FIX
    return None


def someone_else_runs(found: Found, owner: int | None) -> bool:
    """Another account runs the Ollama that answers, or another program or account listens on its port too: that
    account controls her answers (doctor's ✗)."""
    return bool(found.others) or (found.uid is not None and owner is not None and found.uid != owner)


def endangers(found: Found, owner: int | None, port: int = PORT) -> bool:
    """Whether her prompts may reach someone other than you: its program not found, another account runs it or its
    account cannot be read, another program or account on its port too, other machines reach it, or (not Homebrew's
    or the app's, whose own signatures are named apart) its signature does not check out. These stop a re-run of
    setup too; Homebrew's, the app's and the other jobs are a "!" there."""
    if not found.program or found.uid is None or someone_else_runs(found, owner):
        return True
    if other_machines(found.addresses or (), port):
        return True
    program = Path(found.program)
    return bool(found.signature) and not homebrews(program) and app_of(program) is None


def hers(found: Found, root: Path = Path("/"), *, her_uid: int | None = None) -> bool:
    """Her own Ollama, the one rule setup and doctor share: run by her account (_estia), its program's real path under
    /usr/local/estia/ollama, and that program and every folder up to / root's alone (`ownuser._trusted`). `root`: the
    Mac's tree (a test's own); `her_uid`: her account's number (None: read from the user database)."""
    uid = ownuser.lookup_uid(ownuser.ROLE_USER) if her_uid is None else her_uid
    if uid is None or found.uid != uid or not found.program.startswith("/"):
        return False
    on_disk = root / Path(found.program).relative_to("/")
    real = Path(os.path.realpath(on_disk))
    folder = Path(os.path.realpath(root / HER_FOLDER.relative_to("/")))
    return real.is_relative_to(folder) and real != folder and ownuser._trusted(on_disk, root) is None


def owner_of(found: Found, owner: int | None, root: Path = Path("/"), *, her_uid: int | None = None) -> int | None:
    """The account an Ollama may run as and still be kept: hers for her own Ollama (`hers`), else yours (`owner`)."""
    return found.uid if hers(found, root, her_uid=her_uid) else owner


def layout_found(
    root: Path, *, config_ollama: str | None = None, config_url: str | None = None, moved: bool = False
) -> Layout | None:
    """The layout an install has already, read only, first match wins: setup's own service file (every account reads
    it: _estia is hers; any other older, once your config is set up or she moved); else your config set up
    (`ollama = "app"`, or its ollama_base_url on her port: hers; else older); else older once she runs in her own
    account (her config is hers alone). None for a first install: the plan decides (`layout_planned`). The first
    install's layout sticks. Today's service file with no config and no move is an earlier try's leftover (c-2,
    round 3): None too, and her own Ollama's service file takes its place (`Install.daemon`)."""
    path = root / DAEMON.relative_to("/")
    service = read_plist(path)
    if service is not None and service.get("UserName") == ownuser.ROLE_USER:
        return "hers"
    installed = config_ollama is not None or config_url is not None or moved
    if installed and (service is not None or os.path.lexists(path)):
        return "older"  # one setup cannot read: today's code, which says what it finds
    if config_ollama is not None or config_url is not None:
        if config_ollama == "app":
            return "app"
        try:
            port = urllib.parse.urlsplit(config_url or "").port
        except ValueError:
            port = None
        return "hers" if port == HER_PORT else "older"
    return "older" if moved else None


def layout_planned(found: Layout | None, *, app: bool, move: bool) -> Layout:
    """A first install's layout from the plan, once nothing is found (`layout_found`): the Ollama app when chosen;
    her own Ollama (`NEW_INSTALLS`) with her own macOS account; else older (today's)."""
    if found is not None:
        return found
    if app:
        return "app"
    return NEW_INSTALLS if move else "older"


def base_of(layout: Layout) -> str:
    """The address setup talks to Ollama on for this layout."""
    return HER_BASE if layout == "hers" else BASE


def stopped(text: str, fix: str) -> str:
    """Setup's last words when it stops at Ollama: what is wrong, then the fix the person runs."""
    return f"✗ Ollama: {text}\n  Fix: {fix}"


class Refusal(SystemExit):
    """The Ollama step refuses what it found (`Install.refuse`): what is wrong and the fix. A first install stops
    there; a re-run of setup where she is set up already shows it as a "!" (`warned`) and goes on, unless it is
    `fatal`: an Ollama that someone other than you may control (`endangers`) stops every run."""

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


def warned(message: str) -> str:
    """A refusal's words as a "!" line: a re-run of setup goes on after it."""
    return f"! Ollama:{message.removeprefix('✗ Ollama:')}" if message.startswith("✗ Ollama:") else message


@dataclass(frozen=True)
class Job:
    """A launchd job that runs a program named ollama: its label, that program, its file (as shown), whether it is
    a LaunchDaemon (the system domain, from boot) or your LaunchAgent (your login's domain), and whether it starts
    Ollama through another program (a shell's -c, env), whose own words name the program as typed."""

    label: str
    program: str
    plist: Path
    system: bool
    wrapped: bool = False


def read_plist_bytes(path: Path) -> bytes | None:
    """A launchd job file's bytes: a regular file read without following a link, at most PLIST_BYTES; None
    otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with open(fd, "rb", closefd=False) as handle:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            data = handle.read(PLIST_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return data if len(data) <= PLIST_BYTES else None


def read_plist(path: Path) -> dict[str, Any] | None:
    """A launchd job file (`read_plist_bytes`), parsed; None when it cannot be read or is not a dictionary."""
    data = read_plist_bytes(path)
    if data is None:
        return None
    try:
        loaded = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError, TypeError, OverflowError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _is_ollama(word: str) -> bool:
    return Path(word).name.lower() == "ollama"


def _serve_in(command: str) -> str:
    """The program a shell command starts as `ollama serve` (a word named ollama, then serve), as typed; "" for
    none. A command the shell's rules cannot split (an open quote) is split at its spaces and separators."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        words = [word for word in re.split(r"[\s;&|()]+", command) if word]
    return next((word for word, after in itertools.pairwise(words) if _is_ollama(word) and after == "serve"), "")


def started_ollama(service: Mapping[str, Any]) -> tuple[str, bool] | None:
    """The ollama program a launchd job starts, and whether through another program: its `Program` or first
    argument named ollama; else a later argument named ollama (`/usr/bin/env ollama serve`); else `ollama serve`
    inside an argument (a shell's -c). None when it starts none."""
    arguments = service.get("ProgramArguments")
    words = [word for word in arguments if isinstance(word, str)] if isinstance(arguments, list) else []
    first = service.get("Program") or (words[0] if words else "")
    if isinstance(first, str) and _is_ollama(first):
        return first, False
    for word in words[1:]:
        if _is_ollama(word):
            return word, True
    for word in words:
        if found := _serve_in(word):
            return found, True
    return None


def ollama_jobs(root: Path, agents: Path | None = None) -> list[Job]:
    """Every launchd job file that runs a program named ollama, directly or through another program
    (`started_ollama`): the LaunchDaemons under `root`, then your LaunchAgents in `agents`. Read-only, no link
    followed; a file that cannot be read is passed over."""
    jobs: list[Job] = []
    places = [(root / LAUNCH_DAEMONS.relative_to("/"), LAUNCH_DAEMONS, True)]
    if agents is not None:
        places.append((agents, agents, False))
    for folder, shown, system in places:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            service = read_plist(folder / name) if name.endswith(".plist") else None
            if service is None:
                continue
            started = started_ollama(service)
            if started is None:
                continue
            label = service.get("Label")
            program, wrapped = started
            jobs.append(
                Job(
                    label if isinstance(label, str) else name.removesuffix(".plist"),
                    program,
                    shown / name,
                    system,
                    wrapped,
                )
            )
    return jobs


def restart_fix(job: Job) -> str:
    """The commands that start a job again: kickstart, and bootstrap for one launchd does not have loaded."""
    if job.system:
        return prompts.OLLAMA_RESTART_SYSTEM.format(label=job.label, plist=job.plist)
    return prompts.OLLAMA_RESTART_AGENT.format(uid=os.getuid(), label=job.label, plist=job.plist)


def job_kind(job: Job, real: str) -> str:
    """Whose Ollama a job starts: "homebrew" (`brew services`), "app" (the Ollama app's) or "other"; `real` is its
    program with every link resolved. A job that starts it through another program is someone's own: "other"
    (`brew services stop` would not remove it), unless it is brew services' own file."""
    if job.label.startswith("homebrew.mxcl."):
        return "homebrew"
    if job.wrapped:
        return "other"
    if homebrews(Path(real)):
        return "homebrew"
    return "app" if app_of(Path(real)) is not None else "other"


def job_problem(job: Job, real: str) -> tuple[str, str]:
    """A job that runs Ollama and stops setup, with its fix: Homebrew's, the app's, or another."""
    kind = job_kind(job, real)
    if kind == "homebrew":
        return prompts.OLLAMA_JOB_HOMEBREW.format(plist=job.plist), prompts.OLLAMA_HOMEBREW_FIX
    app = app_of(Path(real))
    if kind == "app" and app is not None:
        return prompts.OLLAMA_JOB_APP.format(plist=job.plist), prompts.OLLAMA_APP_FIX.format(path=app)
    return (
        prompts.OLLAMA_JOB.format(label=job.label, plist=job.plist),
        prompts.OLLAMA_JOB_FIX.format(restart=restart_fix(job)),
    )


def daemon_plist(*, user: str, group: str, home: Path, num_ctx: int) -> bytes:
    """The LaunchDaemon: Ollama's official build from ~/opt/ollama/current, as you, from boot, on 127.0.0.1 only; one
    model loaded, one request at a time; its output in root's /var/log/estia."""
    service: dict[str, Any] = {
        "Label": LABEL,
        "UserName": user,
        "GroupName": group,
        "ProgramArguments": [str(home / "opt/ollama/current/ollama"), "serve"],
        "EnvironmentVariables": {
            "HOME": str(home),
            "OLLAMA_HOST": f"127.0.0.1:{PORT}",
            "OLLAMA_MODELS": str(home / ".ollama/models"),
            "OLLAMA_KEEP_ALIVE": KEEP_ALIVE,
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_CONTEXT_LENGTH": str(num_ctx),
        },
        "WorkingDirectory": str(home),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }
    return plistlib.dumps(service)


def her_plist(num_ctx: int) -> bytes:
    """Her own Ollama's LaunchDaemon: root's copy of the official build, as her own macOS account (_estia), from boot,
    on her own address on the Mac itself; her model in her own folder (Umask 63: 0700 and 0600); one model loaded, one
    request at a time, no cloud model; its output in root's /var/log/estia. Her home given explicitly, as for her other
    services: her account record keeps /var/empty."""
    home = str(ownuser.HOME)
    service: dict[str, Any] = {
        "Label": LABEL,
        "UserName": ownuser.ROLE_USER,
        "GroupName": ownuser.ROLE_GROUP,
        "ProgramArguments": [str(HER_PROGRAM), "serve"],
        "EnvironmentVariables": {
            "HOME": home,
            "CFFIXED_USER_HOME": home,
            "OLLAMA_HOST": f"127.0.0.1:{HER_PORT}",
            "OLLAMA_MODELS": str(HER_MODELS),
            "OLLAMA_KEEP_ALIVE": KEEP_ALIVE,
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_CONTEXT_LENGTH": str(num_ctx),
            "OLLAMA_NO_CLOUD": "1",
        },
        "WorkingDirectory": home,
        "Umask": 63,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }
    return plistlib.dumps(service)


def service_file_changed(root: Path, num_ctx: int) -> bool | None:
    """Whether setup's own service file differs from the one setup writes now (`daemon_plist`) for the account it
    runs as (its UserName, its home and group from the user database); None when there is none or it cannot be read.
    Read-only, no link followed: doctor's question, as you or as her (the file is every account's to read)."""
    path = root / DAEMON.relative_to("/")
    data = read_plist_bytes(path)
    service = read_plist(path) if data is not None else None
    if data is None or service is None:
        return None
    user = service.get("UserName")
    if user == ownuser.ROLE_USER:  # her own Ollama: its own bytes (her record's home is /var/empty)
        return data != her_plist(num_ctx)
    try:
        account = pwd.getpwnam(user) if isinstance(user, str) else None
        group = grp.getgrgid(account.pw_gid).gr_name if account is not None else ""
    except KeyError:
        return True  # an account the user database does not know: never one setup writes
    if account is None or not isinstance(user, str):
        return True
    return data != daemon_plist(user=user, group=group, home=Path(account.pw_dir), num_ctx=num_ctx)


def login_profile(home: Path, shell: str) -> Path:
    """The file new Terminal windows read (setup.sh's choice for Homebrew's line)."""
    return home / (".bash_profile" if shell.endswith("/bash") else ".zprofile")


# Gemma 4's prompts in Ollama 0.40.4 (server/renderer_resolution.go, model/renderers/renderer.go): "gemma4-large" is
# Google's prompt for 12B and larger, which ends with an empty thought block when thinking is off ("suppressing ghost
# thought channels"); "gemma4-small" is the one for E2B and E4B, without it. A model whose RENDERER is the plain
# "gemma4" gets one by its name (e2b, e4b: small; 12b, 26b, 31b: large), else by its model type (none for an MLX
# build), else small: so the alias local-worker, which names no size, got the small one (empty answers, 17 Oct).
LARGE_PROMPT = "gemma4-large"
_SMALL_PROMPTS = ("gemma4", "gemma4-small")
_SMALL_SIZES = ("e2b", "e4b")
_LARGE_SIZES = ("12b", "26b", "31b")
LARGE_MIN_PARAMETERS = 12_000_000_000  # Ollama's line between the two (gemma4LargeMinParameterCount)


def large_gemma4(tag: str) -> bool:
    """A Gemma 4 tag of 12B or more, by its name as Ollama tells the sizes apart: its alias gets LARGE_PROMPT. A small
    model's tag (E2B, E4B) and any other family: False (Ollama's own pick stays)."""
    lower = tag.lower()
    if not lower.startswith("gemma4:") or any(size in lower for size in _SMALL_SIZES):
        return False
    return any(size in lower for size in _LARGE_SIZES)


def _parameters(text: str) -> int | None:
    """Ollama's human parameter count ("26.2B", "12B", "800M") as a number; None when it is not one."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMB])\s*", text, re.IGNORECASE)
    if match is None:
        return None
    return int(float(match.group(1)) * {"K": 1e3, "M": 1e6, "B": 1e9}[match.group(2).upper()])


def renderer_line(modelfile: str) -> str:
    """The RENDERER line's value in a model's Modelfile as /api/show writes it back ("" when it has none)."""
    for line in modelfile.splitlines():
        command, _, value = line.strip().partition(" ")
        if command.upper() == "RENDERER":
            return value.strip()
    return ""


def _size(shown: Mapping[str, Any]) -> int | None:
    """The model's parameters from /api/show: its details' parameter_size (an MLX build's is read from its files),
    else model_info's general.parameter_count; None when neither can be read."""
    details = shown.get("details")
    size = _parameters(str(details.get("parameter_size") or "")) if isinstance(details, Mapping) else None
    info = shown.get("model_info")
    count = info.get("general.parameter_count") if isinstance(info, Mapping) else None
    if size is None and isinstance(count, int) and not isinstance(count, bool):
        size = count
    return size


def gets_small_prompt(name: str, shown: Mapping[str, Any], listed: Mapping[str, Any] | None = None) -> bool:
    """A Gemma 4 model of 12B or more that Ollama gives the prompt meant for the small models, read from its
    /api/show answer (`shown`: the RENDERER line of its Modelfile, its size) and its /api/tags entry (`listed`: its
    parameter_size there is the model type Ollama's rule reads; an MLX build has none). Ollama 0.40.4's rule for the
    plain "gemma4": the name (e2b, e4b first), then the model type, else small. False for LARGE_PROMPT, any other
    renderer, a smaller model, or a size that cannot be read."""
    renderer = renderer_line(str(shown.get("modelfile") or ""))
    size = _size(shown)
    if renderer not in _SMALL_PROMPTS or size is None or size < LARGE_MIN_PARAMETERS:
        return False
    if renderer == "gemma4-small":
        return True
    lower = name.lower()
    if any(hint in lower for hint in _SMALL_SIZES):
        return True
    if any(hint in lower for hint in _LARGE_SIZES):
        return False
    details = listed.get("details") if isinstance(listed, Mapping) else None
    model_type = _parameters(str(details.get("parameter_size") or "")) if isinstance(details, Mapping) else None
    return model_type is None or model_type < LARGE_MIN_PARAMETERS


def non_mlx_source(model: str, entry: Mapping[str, Any] | None) -> str | None:
    """What a Gemma 4 model is made from when it is not one of the MLX builds setup installs (the tag, or "a llama.cpp
    (GGUF) build of Gemma 4"); None when it is one, is another family, or cannot be told from Ollama's list. A bare
    tag (gemma4:26b) is a list whose pick depends on Ollama's version, so it counts too."""
    name = model if ":" in model else f"{model}:latest"
    if name.startswith("gemma4:"):
        return None if name.endswith("-mlx") else model
    details = entry.get("details") if isinstance(entry, Mapping) else None
    if not isinstance(details, Mapping):
        return None
    parent = str(details.get("parent_model") or "").rsplit("/", 1)[-1]
    if parent.startswith("gemma4:"):
        return None if parent.endswith("-mlx") else parent
    families = [str(details.get("family") or ""), *(str(item) for item in details.get("families") or [])]
    if any(family.startswith("gemma4") for family in families) and details.get("format") == "gguf":
        return prompts.OLLAMA_GGUF_SOURCE
    return None


def _needs_signature(path: Path) -> bool:
    if path.name.endswith(_SIGNED_SUFFIXES):
        return True
    try:
        with path.open("rb") as handle:
            return handle.read(4) in _MACHO
    except OSError:
        return True  # unreadable: asked about, and codesign says why


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def root_modes(folder: Path) -> str:
    """Why root's copy of a release cannot be run as her, or can be changed by another account ("" when neither): every
    file and folder root's (`ownuser.CODE_UID`), not group- or other-writable, readable by every account, folders and
    programs runnable by every account, no set-user or set-group bit. At most five reasons, then a count."""
    problems: list[str] = []
    paths = [folder]  # the folder itself, then everything in it (a link is looked at, never followed)
    for top, folders, files in os.walk(folder):
        paths += [Path(top) / name for name in sorted([*folders, *files])]
    for path in paths:
        shown = path.relative_to(folder) if path != folder else Path(folder.name)
        try:
            info = path.lstat()
        except OSError:
            problems.append(prompts.OLLAMA_WHY_MODES.format(name=shown, why="could not be read"))
            continue
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != ownuser.CODE_UID:
            problems.append(prompts.OLLAMA_WHY_MODES.format(name=shown, why="is not root's"))
        elif stat.S_ISLNK(info.st_mode):
            continue
        elif mode & 0o022:
            problems.append(prompts.OLLAMA_WHY_MODES.format(name=shown, why="can be changed by another account"))
        elif mode & (stat.S_ISUID | stat.S_ISGID):
            problems.append(prompts.OLLAMA_WHY_MODES.format(name=shown, why="has a set-user or set-group bit"))
        elif not mode & 0o004 or ((stat.S_ISDIR(info.st_mode) or mode & 0o100) and not mode & 0o001):
            problems.append(prompts.OLLAMA_WHY_MODES.format(name=shown, why="cannot be run by her account"))
    more = f" (and {len(problems) - 5} more)" if len(problems) > 5 else ""
    return "; ".join(problems[:5]) + more


class Tools:
    """The Mac for this step (replaced in tests): Ollama's answer, the program on its port, read-only tools, and
    changes, which run in the person's own terminal (sudo asks there), or, given `change` (setup's window), through
    the setup window (macOS's own box asks for admin rights). Off the live Mac (root not "/") nothing is read or run:
    no Ollama answers, no program listens, and every change is printed."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        say: Callable[[str], None] = print,
        host: Host | None = None,
        change: Callable[[Sequence[str], bytes | None], int | None] | None = None,
    ) -> None:
        self.root = estia_root() if root is None else root
        self.say = say
        self.host = host or Host(self.root, say=say)
        self.changer = change  # the window's runner: an exit code, or None when the terminal runs it
        # what the last change printed when it failed and the window kept it from view (a change with input runs
        # captured there): its ✗ line is the step's reason, as Terminal shows it (review W23)
        self.failed_output = ""

    def kept_failure(self, output: str) -> None:
        """The window's word for a change that failed: what it printed."""
        self.failed_output = output

    @property
    def live(self) -> bool:
        return self.root == Path("/")

    def resolve(self, program: str) -> str:
        """A program's path with every link resolved on this Mac (the Ollama app's /usr/local/bin/ollama links into
        the app)."""
        return os.path.realpath(program)

    def version(self, port: int = PORT) -> str | None:
        """/api/version's version on that port of the Mac itself ("" when the answer names none); None when nothing
        answers."""
        if not self.live:
            return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f"http://127.0.0.1:{port}/api/version", timeout=3) as response:
                data = json.loads(response.read(100_000))
        except (OSError, ValueError):
            return None
        return str(data.get("version") or "") if isinstance(data, dict) else ""

    def listener(self, port: int = PORT, *, by_service: bool = True) -> Listener:
        """The program listening on the port, where it listens and the account that runs it: netstat names every
        user's sockets with their pid and local address (lsof as a user only that user's), proc_pidpath the program
        and ps its account, both for any user's process; Listener("") when none (`others_unread` set when netstat's
        list could not be read, `hostsec.netstat_blind`). When no socket can be seen at all on her address, her own
        service's process stands for it (`her_service`; `by_service`: for an Ollama that answered there, never as the
        proof that something holds it)."""
        if not self.live:
            return Listener("")
        sockets: dict[str, list[str]] = {}  # each listening pid and its local addresses on the port
        code, out = self.host.run([NETSTAT, "-anv", "-p", "tcp"], read_only=True)
        blind = netstat_blind(code, out)
        for line in out.splitlines() if code == 0 else []:
            listening = _LISTEN.match(line)
            if listening and listening[1].rpartition(".")[2] == str(port):
                sockets.setdefault(listening[3], []).append(listening[1])
        if not sockets:  # netstat may list no internet sockets (seen on macOS 27.0.1, 3 Nov): your own, from lsof
            code, out = self.host.run([LSOF, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpn"], read_only=True)
            pid = ""
            for line in out.splitlines() if code == 0 else []:
                if line.startswith("p") and line[1:].isdecimal():
                    pid = line[1:]
                    sockets.setdefault(pid, [])
                elif line.startswith("n") and pid:  # lsof's "127.0.0.1:11434", "[::1]:11434", "*:11434": netstat's
                    host, _, at = line[1:].rpartition(":")
                    sockets[pid].append(f"{host.strip('[]')}.{at}")
        if not sockets and port == HER_PORT and by_service:
            return replace(self.her_service(port), others_unread=blind)
        programs = {pid: self.host.program_path(pid) for pid in sockets}
        first = next((pid for pid in sockets if programs[pid]), None)
        if first is None:  # none named: unread when netstat's list was, as another account's may hold the port
            return Listener("", others_unread=blind)
        accounts = {pid: self.uid_of(pid) for pid in sockets}
        mine = (programs[first], accounts[first])
        others = tuple(
            other for other in dict.fromkeys((programs[pid], accounts[pid]) for pid in sockets) if other != mine
        )
        addresses = tuple(dict.fromkeys(address for found in sockets.values() for address in found))
        return Listener(mine[0], addresses, mine[1], others, others_unread=blind)

    def her_service(self, port: int) -> Listener:
        """Her own Ollama when no socket can be seen (netstat may list none: from a command tool's shell, review of
        30 Oct, P01; on macOS 27.0.1 on 3 Nov; lsof lists only your own): setup's service as launchd runs it
        (`launchctl print`, read-only, as any account), kept only when its process's program is in root's copy
        (HER_FOLDER) and its OLLAMA_HOST is this port; where it listens is that OLLAMA_HOST, its account from ps.
        `hers` then checks it as any listener. Another account's second listener on the port cannot be seen this way.
        Listener("") otherwise."""
        code, out = self.host.run([LAUNCHCTL, "print", f"system/{LABEL}"], read_only=True)
        pid = _LAUNCHD_PID.search(out) if code == 0 else None
        environment = _LAUNCHD_ENVIRONMENT.search(out) if code == 0 else None
        named = _LAUNCHD_HOST.search(environment[1]) if environment is not None else None
        address = host_address(named[1], port) if named is not None else None
        if pid is None or address is None:
            return Listener("")
        program = self.host.program_path(pid[1])
        if not program or not Path(program).is_relative_to(HER_FOLDER):
            return Listener("")
        return Listener(program, (address,), self.uid_of(pid[1]))

    def her_uid(self) -> int | None:
        """Her account's number (_estia), from the user database; None when there is none (or off the live Mac)."""
        return ownuser.lookup_uid(ownuser.ROLE_USER) if self.live else None

    def free_bytes(self, path: Path) -> int | None:
        """Free space on the volume a path is on (its nearest folder that is there); None when it cannot be read."""
        for folder in (path, *path.parents):
            if folder.exists():
                try:
                    return shutil.disk_usage(folder).free
                except OSError:
                    return None
        return None

    def uid_of(self, pid: str) -> int | None:
        """The account (uid) that runs a process: `ps -o uid= -p <pid>` (fixed argv, read-only); None when it says
        nothing usable (the process is gone)."""
        code, out = self.host.run([PS, "-o", "uid=", "-p", pid], read_only=True)
        text = out.strip() if code == 0 else ""
        return int(text) if text.isdecimal() else None

    def read(self, argv: Sequence[str]) -> tuple[int, str]:
        """A read-only tool by its full path (codesign, spctl): (exit code, its output); never off the live Mac."""
        if not self.live:
            return 127, ""
        return self.host.run(argv, read_only=True)

    def fetch(self, argv: Sequence[str], path: Path, watch: setup_progress.Watch) -> int:
        """A download by its full path into `path`, its five dots lit by the file's size (setup_progress.fetch), in
        the person's terminal. Off the live Mac only printed."""
        if not argv or not argv[0].startswith("/"):
            raise ValueError(f"tools are run by their absolute path, not {list(argv[:1])}")
        if not self.live:
            self.say("would run: " + " ".join(argv))
            return 0
        return setup_progress.fetch(argv, path, RELEASE_BYTES, watch)

    def change(self, argv: Sequence[str], *, input: bytes | None = None) -> int:
        """A change by its full path, in the person's terminal (sudo asks for the password there), or through the
        setup window (`changer`: macOS's own box asks); with `input`, on the tool's input, its output dropped. Off the
        live Mac only printed."""
        if not argv or not argv[0].startswith("/"):
            raise ValueError(f"tools are run by their absolute path, not {list(argv[:1])}")
        self.failed_output = ""
        if not self.live:
            self.say("would run: " + " ".join(argv))
            return 0
        if self.changer is not None and (code := self.changer(argv, input)) is not None:
            return code
        try:
            done = subprocess.run(
                list(argv), input=input, stdout=subprocess.DEVNULL if input is not None else None, check=False
            )
        except OSError:
            return 127
        return done.returncode


class Install:
    """Setup's Ollama step: keep the official build that answers (updating setup's own in place to the pin), or
    install the pinned one and run it always on."""

    def __init__(
        self,
        tools: Tools,
        home: Path,
        *,
        dry_run: bool,
        say: Callable[[str], None] = print,
        release: Release = RELEASE,
        user: str | None = None,
        group: str | None = None,
        profile: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        watch: Callable[[str], setup_progress.Watch] | None = None,
        command: Callable[[str], None] | None = None,
        app: Path | None = None,
        layout: Layout = "older",
        account: Sequence[str] | None = None,
        model_bytes: int = 0,
        name: str = "Estia",
        leftover: bool = False,
    ) -> None:
        self.tools = tools
        self.app = app  # the Ollama app the person chose at setup's question (owner, 29 Oct): used, nothing installed
        self.layout = layout  # "hers": her own Ollama (run_hers); any other: today's code, byte for byte
        self.account = account  # setup's command that makes her account at step 1 (None: never, as install-ollama.sh)
        self.model_bytes = model_bytes  # her model's bytes still to download (hers: the free disk it needs)
        self.name = name
        # setup's own service file is an earlier try's (no config, no move): beside the chosen app it goes (c-2)
        self.leftover = leftover
        self.used: Layout = "older"  # what run() ended with: "hers", "older" or "app"
        self.trying_app = False  # her own Ollama's fallback: the app's refusal raises, a dry run too
        self.app_said = False
        self.password_said = False
        self.home = home
        self.say = say
        self.watch = watch  # setup's: the download's five dots, curl's own meter off (None: curl's meter, as before)
        # setup's: a command that asks nothing in Terminal, printed so the window keeps its screen (default: say)
        self.command = command or say
        self.release = release
        self.user = user or pwd.getpwuid(os.getuid()).pw_name
        self.group = group or grp.getgrgid(os.getgid()).gr_name
        self.profile = profile or login_profile(home, os.environ.get("SHELL", ""))
        self.clock = clock
        self.sleep = sleep
        # off the live Mac (a test's root): nothing on this Mac is read, not even your home; every step only shown
        self.blind = not tools.live
        self.dry_run = dry_run or self.blind

    @property
    def her_own(self) -> bool:
        return self.layout == "hers"

    @property
    def boxed(self) -> bool:
        """Its admin rights come from macOS's own box (the setup window runs its changes): no password line here."""
        return self.tools.changer is not None

    @property
    def port(self) -> int:
        return HER_PORT if self.her_own else PORT

    @property
    def base(self) -> str:
        """Where the Ollama setup uses answers: her own address, or 11434 (today's, the app's)."""
        return HER_BASE if self.used == "hers" else BASE

    @property
    def folder(self) -> Path:
        return self.home / "opt" / "ollama"

    @property
    def dest(self) -> Path:
        return self.folder / f"v{self.release.version}"

    @property
    def program(self) -> Path:
        return self.folder / "current" / "ollama"

    def run(self, num_ctx: int) -> str:
        """The ollama program setup runs for the model step (any official one talks to the server that answers);
        SystemExit with what is wrong and the fix when it cannot go on. In a dry run, what would stop it is printed
        and the rest follows as it would run after the fix. With the Ollama app chosen: the app (`use_app`). Her own
        Ollama (layout "hers"): `run_hers`."""
        if self.her_own:
            return self.run_hers(num_ctx)
        if self.app is not None:
            self.used = "app"
            return self.use_app(self.app)
        self.cap_log()
        found = self.running()
        jobs = self.jobs()
        if found is not None and self.keep(found, jobs):
            if self.ours(found, jobs) and found.version != self.release.version:
                return self.update(found, num_ctx)
            self.kept(found, jobs, num_ctx)
            return found.program
        if found is None:  # another always-on service would fight setup's for the port: Homebrew's or the app's first
            others = [job for job in jobs if job.label != LABEL]
            others.sort(key=lambda job: job_kind(job, self.real(job.program)) == "other")
            for job in others[:1]:
                self.refuse(stopped(*job_problem(job, self.real(job.program))))
            self.others(answering=False)
        self.unpack()
        self.link()
        self.on_path()
        self.daemon(num_ctx)
        self.wait()
        return str(self.program)

    def use_app(self, app: Path) -> str:
        """The Ollama app, as the person chose (owner, 29 Oct): nothing installed. Its own ollama program by its real
        path inside the app (never one found on PATH), signed by Ollama; setup's own service beside it stops a first
        install (two always-on Ollamas fight for the port); the app started with open -a when nothing answers, and
        waited for; then the checks any Ollama gets, the app's own allowed: run by you, alone on its port, on this Mac
        only. One someone other than you may control stops every run, as before. Off the live Mac only shown."""
        shown = app / APP_PROGRAM
        if self.blind:
            self.say(prompts.OLLAMA_APP_WOULD.format(path=app))
            self.step([OPEN, "-a", str(app)])
            return str(shown)
        program = Path(self.real(str(shown)))
        on_disk = self._system(program)
        if not program.is_relative_to(self.real(str(app))) or on_disk.is_symlink() or not on_disk.is_file():
            why = prompts.OLLAMA_APP_NO_PROGRAM.format(path=shown)
            self.refuse(stopped(why, prompts.OLLAMA_APP_REINSTALL), fatal=True)
            return str(shown)
        unsigned = signature_problem(self.tools.read, str(on_disk))
        if unsigned:
            why = prompts.OLLAMA_OTHER.format(program=program, why=unsigned)
            self.refuse(stopped(why, prompts.OLLAMA_APP_REINSTALL), fatal=True)
        if any(job.label == LABEL for job in self.jobs()):
            self.app_beside_ours()
        if self.tools.version() is None:
            self.say(prompts.OLLAMA_APP_STARTING)
            opened = self.step([OPEN, "-a", str(app)])
            if self.dry_run:
                return str(program)
            if opened != 0:
                raise SystemExit(prompts.OLLAMA_APP_NOT_OPENED.format(app=app, code=opened))
            self._wait_for_app()
        found = self.running(PORT)  # the app's own address, whatever layout setup planned
        if found is None:
            raise SystemExit(prompts.OLLAMA_APP_NOT_UP)
        owner = os.getuid()
        fatal = endangers(found, owner) or bool(found.signature)
        if not found.program or Path(self.real(found.program)) != program:
            heard = found.program or prompts.OLLAMA_SHARED_NOT_FOUND
            self.refuse(
                stopped(prompts.OLLAMA_APP_OTHER.format(program=heard), prompts.OLLAMA_APP_OTHER_FIX), fatal=fatal
            )
            return str(program)
        wrong = problem(replace(found, program=str(on_disk)), owner=owner, app_ok=True)  # its files, on this Mac
        if wrong is None and found.uid is None:
            wrong = prompts.OLLAMA_OTHER.format(program=found.program, why=prompts.OLLAMA_WHY_ACCOUNT_UNREAD), ""
        if wrong is not None:
            self.refuse(stopped(wrong[0], prompts.OLLAMA_APP_FIX_IT.format(needed=MIN_TEXT)), fatal=fatal)
            return str(program)
        self.say(prompts.OLLAMA_APP_USED.format(version=found.version, program=program))
        self.say(f"! Ollama: {prompts.OLLAMA_APP_LIMITS}")
        return str(program)

    def app_beside_ours(self) -> None:
        """Setup's own service file beside the chosen app: two always-on Ollamas fight for the port. An earlier try's
        (`leftover`) is booted out and removed with admin rights, then the app goes on (c-2, round 3); an install's
        own stops a first install with the fix, as before (her own Ollama's fallback takes the refusal)."""
        fix = prompts.OLLAMA_APP_OURS_FIX.format(label=LABEL, daemon=DAEMON)
        refused = stopped(prompts.OLLAMA_APP_OURS.format(daemon=DAEMON), fix)
        if not self.leftover or self.trying_app:
            self.refuse(refused)
            return
        self.say(prompts.OLLAMA_APP_OURS_GOES.format(daemon=DAEMON))
        self.step([SUDO, LAUNCHCTL, "bootout", f"system/{LABEL}"])  # not loaded: it says so, and that is fine
        if self.step([SUDO, RM, "-f", str(DAEMON)]) != 0:
            raise SystemExit(refused)

    def _wait_for_app(self) -> None:
        """Until the app answers, APP_WAIT_SECONDS at most, with a still-waiting line every APP_WAITING_EVERY s."""
        start = self.clock()
        said = start
        while self.tools.version() is None:
            now = self.clock()
            if now - start >= APP_WAIT_SECONDS:
                raise SystemExit(prompts.OLLAMA_APP_NOT_UP)
            if now - said >= APP_WAITING_EVERY:
                said = now
                self.say(prompts.OLLAMA_APP_WAITING.format(seconds=round(now - start)))
            self.sleep(1)

    def running(self, port: int | None = None) -> Found | None:
        """The Ollama that answers on this Mac, with its program, its signature and where it listens; None when none
        answers. `port`: this layout's (her own address for hers); the app's is always 11434 (`use_app`)."""
        port = self.port if port is None else port
        version = self.tools.version(port)
        if version is None:
            return None
        heard = self.tools.listener(port)
        if not heard.program:
            return Found("", version, addresses=heard.addresses)
        signature = signature_problem(self.tools.read, heard.program)
        return Found(heard.program, version, signature, heard.addresses, heard.uid, heard.others)

    def jobs(self) -> list[Job]:
        """The launchd jobs that run Ollama: the LaunchDaemons, and your LaunchAgents (not off the live Mac)."""
        return ollama_jobs(self.tools.root, None if self.blind else self.home / "Library/LaunchAgents")

    def real(self, path: str) -> str:
        return path if self.blind else self.tools.resolve(path)

    def refuse(self, message: str, *, fatal: bool = False) -> None:
        """Setup stops here with the message (`Refusal`; `fatal`: a re-run stops too); a dry run prints it and goes
        on as it would after the fix."""
        if not self.dry_run or self.trying_app:
            raise Refusal(message, fatal=fatal)
        self.say(message)
        self.say(prompts.OLLAMA_DRY_RUN_GOES_ON)

    def keep(self, found: Found, jobs: Sequence[Job]) -> bool:
        """Whether the Ollama that answers can be kept: the official build, its whole folder signed by Ollama's
        developer ID, run by your own account (one whose account cannot be read is not kept either), and no launchd
        job of Homebrew's or the app's that could take its port at a login. One that someone other than you may
        control (`endangers`) stops a re-run of setup too."""
        wrong = problem(found, owner=os.getuid())
        fatal = endangers(found, os.getuid())
        if wrong is None and found.program and found.uid is None:
            wrong = (
                prompts.OLLAMA_OTHER.format(program=found.program, why=prompts.OLLAMA_WHY_ACCOUNT_UNREAD),
                (prompts.OLLAMA_OTHER_FIX),
            )
        if wrong is None:
            why = self.check_tree(Path(found.program).parent)
            if why:  # a program, library or metallib there not signed by Ollama, or a link out: not its build
                wrong = prompts.OLLAMA_OTHER.format(program=found.program, why=why), prompts.OLLAMA_OTHER_FIX
                fatal = True
        if wrong is None:  # Homebrew's or the app's: started at a login, it can take the port first
            started = [job for job in jobs if job_kind(job, self.real(job.program)) != "other"]
            if started:
                wrong = job_problem(started[0], self.real(started[0].program))
        if wrong is None:
            return True
        self.refuse(stopped(*wrong), fatal=fatal)
        return False

    def ours(self, found: Found, jobs: Sequence[Job]) -> bool:
        """Setup's own service runs the Ollama that answers, from a folder in ~/opt/ollama: an update goes in place."""
        if not any(job.label == LABEL and job.system and job.program == str(self.program) for job in jobs):
            return False
        return Path(self.real(found.program)).parent.parent == Path(self.real(str(self.folder)))

    def kept(self, found: Found, jobs: Sequence[Job], num_ctx: int) -> None:
        """An official Ollama that answers and is not setup's to update: kept, with a "!" when its version is not the
        tested one or when nothing starts it with the Mac; the app or Homebrew's Ollama installed too, named.
        Setup's own service whose file is not the one this setup writes (an older release's, a setting changed by
        hand) gets it written again and starts again (`daemon`, `wait`)."""
        self.say(prompts.OLLAMA_KEPT.format(version=found.version, program=found.program))
        if found.version != self.release.version:
            text = prompts.OLLAMA_UNTESTED.format(version=found.version, pin=self.release.version)
            self.say(f"! Ollama: {text}\n  Fix: {prompts.OLLAMA_UNTESTED_FIX.format(pin=self.release.version)}")
        program = self.real(found.program)
        if not any(job.system and self.real(job.program) == program for job in jobs):
            self.say(f"! Ollama: {prompts.OLLAMA_NOT_AT_BOOT.format(program=found.program)}")
            self.say(f"  Fix: {prompts.OLLAMA_NOT_AT_BOOT_FIX}")
        if self.ours(found, jobs) and self.service_changed(num_ctx):
            self.say(prompts.OLLAMA_SERVICE_CHANGED.format(daemon=DAEMON))
            self.daemon(num_ctx)  # its log made private there too
            self.wait()
        else:
            log = self.log_steps() if any(job.label == LABEL for job in jobs) else []
            if log:
                self.say(
                    (prompts.OLLAMA_LOG_PRIVATE_LEAD if self.boxed else prompts.OLLAMA_LOG_PRIVATE).format(log=LOG)
                )
                for argv in log:
                    self.step(argv)
        self.others(answering=True)

    def service_changed(self, num_ctx: int) -> bool:
        """Setup's own service file is not the one `daemon` writes now (a missing or unreadable one counts)."""
        if self.blind:
            return False
        return read_plist_bytes(self._system(DAEMON)) != self.wanted(num_ctx)

    def update(self, found: Found, num_ctx: int) -> str:
        """Setup's own service on another version than the pin: the pinned release downloaded and checked while the
        old one runs, `current` switched, the service started again; when the new one does not answer, `current`
        points back at the old folder and the service starts again."""
        old = Path(self.real(found.program)).parent
        self.say(prompts.OLLAMA_UPDATE.format(version=found.version, pin=self.release.version))
        self.unpack()
        self.check_safe()  # before `current` moves: a refusal here leaves the old one running, untouched
        self.link()
        self.on_path()
        try:
            self.daemon(num_ctx)
            self.wait()
        except SystemExit as failed:
            if self.dry_run:
                raise
            self.point(old)
            back = prompts.OLLAMA_ROLLED_BACK.format(
                pin=self.release.version, folder=old, version=found.version, new=self.dest
            )
            try:
                self.restart()
            except SystemExit as again:  # its service did not start either: both reasons, `current` back on the old
                raise SystemExit(f"{failed}\n{again}\n{back}") from None
            raise SystemExit(f"{failed}\n{back}") from None
        return str(self.program)

    def _system(self, path: Path) -> Path:
        return self.tools.root / path.relative_to("/")

    def others(self, *, answering: bool) -> None:
        """The Ollama app (it stops other Ollamas when its own cannot start: setup stops before installing the
        service), and Homebrew's Ollama (named, with the fix)."""
        apps = ((self._system(APP), APP), *(() if self.blind else ((self.home / "Applications/Ollama.app", None),)))
        for app, shown in apps:
            if not os.path.lexists(app):
                continue
            path = shown or app
            text = prompts.OLLAMA_APP.format(state="is installed", path=path)
            fix = prompts.OLLAMA_APP_FIX.format(path=path)
            if not answering:
                self.refuse(stopped(text, fix))
            else:
                self.say(f"! Ollama: {text}\n  Fix: {fix}")
        for cellar in CELLARS:
            if os.path.lexists(self._system(cellar / "ollama")):
                self.say(
                    "! Ollama: "
                    + prompts.OLLAMA_HOMEBREW_THERE.format(path=cellar / "ollama")
                    + f"\n  Fix: {prompts.OLLAMA_HOMEBREW_REMOVE}"
                )

    def step(self, argv: Sequence[str], *, input: bytes | None = None, shown: str = "") -> int:
        """A change, printed first; in a dry run only printed. Her own Ollama's first admin command comes after one
        line on the password."""
        if self.her_own and list(argv[:1]) == [SUDO] and not self.password_said:
            self.password_said = True
            if not self.boxed:  # the window's own line says where macOS asks
                self.say(prompts.OLLAMA_HER_PASSWORD)
        self.say("  $ " + " ".join(argv) + (f"   ({shown})" if shown else ""))
        return 0 if self.dry_run else self.tools.change(argv, input=input)

    def unpack(self) -> None:
        """The pinned release in ~/opt/ollama/v<version>: a folder already there is checked and kept; else the
        download, checked in a new empty folder first, then moved into place whole. Nothing is ever deleted there."""
        dest = self.dest
        if not self.blind and os.path.lexists(dest):
            why = self.check_tree(dest) if dest.is_dir() and not dest.is_symlink() else prompts.OLLAMA_NOT_A_FOLDER
            if not why:
                self.say(prompts.OLLAMA_FOLDER_KEPT.format(folder=dest))
                return
            self.refuse(prompts.OLLAMA_FOLDER_REFUSED.format(folder=dest, why=why))
        self.say(prompts.OLLAMA_INSTALL.format(version=self.release.version, size=RELEASE_MB, folder=dest))
        if self.dry_run:
            work = self.folder / ".download-…"
            self.step(self.curl(work / "ollama-darwin.tgz", quiet=self.watch is not None))
            self.say(f"  (its sha256 must be {self.release.sha256}; then unpacked into a new empty folder)")
            self.step([TAR, "-xzf", str(work / "ollama-darwin.tgz"), "-C", str(work / "unpacked")])
            self.say(prompts.OLLAMA_CHECKS.format(team=TEAM_ID))
            self.say(f"  (then moved to {dest})")
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".download-", dir=self.folder))
        try:
            tgz = work / "ollama-darwin.tgz"
            code = self.download(tgz)
            if code != 0 or not tgz.is_file():
                raise SystemExit(self.download_failed(code))
            digest = sha256_of(tgz)
            if digest != self.release.sha256:
                why = prompts.OLLAMA_WHY_FINGERPRINT.format(got=digest, want=self.release.sha256)
                raise SystemExit(prompts.OLLAMA_REFUSED.format(why=why))
            unpacked = work / "unpacked"
            unpacked.mkdir()
            if self.step([TAR, "-xzf", str(tgz), "-C", str(unpacked)]) != 0:
                raise SystemExit(prompts.OLLAMA_REFUSED.format(why=prompts.OLLAMA_WHY_UNPACKED))
            why = self.check_tree(unpacked)
            if why:
                raise SystemExit(prompts.OLLAMA_REFUSED.format(why=why))
            os.rename(unpacked, dest)
        finally:
            shutil.rmtree(work, ignore_errors=True)  # the folder mkdtemp made for this download, and only that
        self.say(prompts.OLLAMA_INSTALLED.format(version=self.release.version, folder=dest))

    def download(self, tgz: Path) -> int:
        """The release into `tgz`: printed first; with setup's `watch`, its five dots lit by the file's size (curl's
        own meter off), else curl's own meter. curl asks nothing (its input is empty): its line is a `command`, so
        the setup window keeps its screen (review PROM-08)."""
        if self.watch is None:
            return self.step(self.curl(tgz))
        argv = self.curl(tgz, quiet=True)
        self.command("  $ " + " ".join(argv))
        return self.tools.fetch(argv, tgz, self.watch(f"Ollama {self.release.version}"))

    def download_failed(self, code: int) -> str:
        """Why the release's download stopped: curl's exit 23 (it could not write) is a full disk, with how much to
        free as `her_disk` counts it (c-11, round 3; round 1's issue 8); anything else, the network."""
        if code != CURL_WRITE_FAILED:
            return prompts.OLLAMA_DOWNLOAD_FAILED
        free = None if self.blind else self.tools.free_bytes(self.home)
        need = math.ceil((self.model_bytes + HER_ROOM) / 1e9)
        more = need if free is None else max(1, need - math.floor(free / 1e9))
        return stopped(prompts.OLLAMA_DOWNLOAD_DISK_FULL, prompts.OLLAMA_HER_DISK_FIX.format(more=more))

    def curl(self, tgz: Path, *, quiet: bool = False) -> list[str]:
        """The download: HTTPS only, redirects too (GitHub sends it on to its file host), never ~/.curlrc (-q, first);
        a stalled line ends it (under 10 KB/s for a minute), as does a file far larger than the release; a failure is
        an exit code. `quiet`: curl's own meter off, its errors still shown (-sS), for setup's five dots."""
        return [
            CURL,
            "-q",
            "-fsSL" if quiet else "-fL",
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
            "--max-filesize",
            "300000000",
            "-o",
            str(tgz),
            self.release.url,
        ]

    def check_tree(self, folder: Path) -> str:
        """Why a release's folder cannot be used ("" when it can): every program, library and metallib signed by
        Ollama's developer ID (Apple's chain, `REQUIREMENT`), `ollama` notarized by Apple, both xgrammar libraries
        there, and no link out of the folder. At most five reasons, then a count."""
        problems: list[str] = []
        inside = folder.resolve()
        for top, folders, files in os.walk(folder):
            for name in sorted([*folders, *files]):
                path = Path(top) / name
                shown = path.relative_to(folder)
                if path.is_symlink():
                    if not path.resolve().is_relative_to(inside):
                        problems.append(prompts.OLLAMA_WHY_LINK.format(name=shown))
                    continue
                if name not in files or not path.is_file() or not _needs_signature(path):
                    continue
                why = signature_problem(self.tools.read, str(path))
                if why:
                    problems.append(f"{shown}: {why}")
        program = folder / "ollama"
        if program.is_symlink() or not program.is_file():
            problems.append(prompts.OLLAMA_WHY_NO_PROGRAM)
        else:
            code, out = self.tools.read([SPCTL, "--assess", "--type", "install", "-vv", str(program)])
            if code != 0 or "accepted" not in out or "source=Notarized Developer ID" not in out:
                problems.append(prompts.OLLAMA_WHY_NOT_NOTARIZED)
        missing, unread = libraries_in(folder)
        problems += [prompts.OLLAMA_WHY_LIBRARY.format(library=library) for library in (*missing, *unread)]
        more = f" (and {len(problems) - 5} more)" if len(problems) > 5 else ""
        return "; ".join(problems[:5]) + more

    def link(self) -> None:
        """~/opt/ollama/current points at this release (a rollback points it back at an older folder)."""
        self.point(self.dest)

    def point(self, target: Path) -> None:
        """~/opt/ollama/current points at `target`, swapped in one rename."""
        current = self.folder / "current"
        if self.blind:
            self.say(f"  $ ln -sfn {target} {current}")
            return
        if os.path.lexists(current) and not current.is_symlink():
            self.refuse(prompts.OLLAMA_CURRENT_NOT_LINK.format(path=current))
            return
        if current.is_symlink() and os.readlink(current) == str(target):
            return
        if self.dry_run:
            self.say(f"  $ ln -sfn {target} {current}")
            return
        temporary = self.folder / f".current-{os.getpid()}"
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        os.symlink(target, temporary)
        os.replace(temporary, current)

    def on_path(self) -> None:
        """Its folder in the login profile, so new Terminal windows find `ollama` (setup itself runs it by full path)."""
        try:
            text = "" if self.blind else self.profile.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        if "opt/ollama/current" in text:
            return
        if self.dry_run:
            self.say(prompts.OLLAMA_PATH_WOULD.format(line=PATH_LINE, profile=self.profile))
            return
        with self.profile.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{PATH_LINE}\n")
        self.say(prompts.OLLAMA_PATH_ADDED.format(line=PATH_LINE, profile=self.profile))

    def unsafe(self) -> str:
        """The first folder on the way from your home to the program, or the program itself, that another account
        owns or can change ("" when none): the service runs that program as you at every start. Read-only."""
        uid = os.getuid()
        on_the_way = [self.home, self.home / "opt", self.folder, self.dest, self.dest / "ollama"]
        for path in on_the_way:
            try:
                info = os.lstat(path) if path.name == "ollama" and path.parent == self.dest else os.stat(path)
            except FileNotFoundError:
                continue  # not there yet (a dry run): made by setup, yours
            except OSError:
                return prompts.OLLAMA_UNSAFE.format(path=path, why=prompts.OLLAMA_UNSAFE_UNREAD)
            if info.st_uid not in (uid, 0):
                return prompts.OLLAMA_UNSAFE.format(path=path, why=prompts.OLLAMA_UNSAFE_OWNER)
            if info.st_mode & 0o022:
                return prompts.OLLAMA_UNSAFE.format(path=path, why=prompts.OLLAMA_UNSAFE_WRITABLE)
        return ""

    def check_safe(self) -> None:
        """Stops before any sudo step when a folder on the way to the program is not yours alone (`unsafe`)."""
        unsafe = "" if self.blind else self.unsafe()
        if unsafe:
            self.refuse(unsafe)

    def log_steps(self) -> list[list[str]]:
        """Root makes the log the service's account's (yours; hers for her own Ollama), 0600, before launchd starts
        the job: launchd opens and appends to it as that account, never as root, so with none there in root's folder
        the job does not start (M01, 30 Oct: exit 78, spawn failed). Only when it is missing or not already that
        account's private, regular file with one name. install(1) replaces the name, never following a link there;
        root's folder (LOG_DIR) stops a link being planted at that name."""
        owner = self.tools.her_uid() if self.her_own else os.getuid()
        if not self.blind:
            try:
                info = os.lstat(self._system(LOG))
            except OSError:
                info = None
            if (
                info is not None
                and stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == owner
                and stat.S_IMODE(info.st_mode) == 0o600
            ):
                return []
        user, group = (ownuser.ROLE_USER, ownuser.ROLE_GROUP) if self.her_own else (self.user, self.group)
        return [[SUDO, INSTALL, "-m", "600", "-o", user, "-g", group, "/dev/null", str(LOG)]]

    def cap_log(self) -> None:
        """Your private log over LOG_CAP is emptied (no admin rights: it is yours; launchd goes on at the new end).
        One that is root's, a link or a second name is left alone."""
        if self.dry_run:
            return
        try:
            fd = os.open(self._system(LOG), os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return
        try:
            info = os.fstat(fd)
            if (
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == os.getuid()
                and info.st_size > LOG_CAP
            ):
                os.ftruncate(fd, 0)
                self.say(prompts.OLLAMA_LOG_EMPTIED.format(log=LOG))
        finally:
            os.close(fd)

    def restart(self) -> None:
        """The service started again: kickstart; one launchd does not have loaded (booted out), bootstrapped."""
        if self.step([SUDO, LAUNCHCTL, "kickstart", "-k", f"system/{LABEL}"]) == 0:
            return
        self.bootstrap()

    def bootstrap(self) -> None:
        argv = [SUDO, LAUNCHCTL, "bootstrap", "system", str(DAEMON)]
        if self.step(argv) != 0:
            self.sleep(2)
            if self.step(argv) != 0:
                raise SystemExit(prompts.OLLAMA_SERVICE_FAILED.format(label=LABEL))

    def daemon(self, num_ctx: int) -> None:
        """The LaunchDaemon: written by root from tee's input (root:wheel, 644), its log made yours and private, then
        loaded; one already in place with the same text is started again instead. Nothing runs as root while a
        folder on the way to the program is not yours alone."""
        wanted = self.wanted(num_ctx)
        try:
            existing: bytes | None = None if self.blind else self._system(DAEMON).read_bytes()
        except OSError:
            existing = None
        if self.her_own:
            self.say(
                prompts.OLLAMA_HER_SERVICE.format(
                    user=ownuser.ROLE_USER, port=HER_PORT, models=HER_MODELS.parent, daemon=DAEMON, log=LOG
                )
            )
        else:
            self.say(
                (prompts.OLLAMA_SERVICE_LEAD if self.boxed else prompts.OLLAMA_SERVICE).format(daemon=DAEMON, log=LOG)
            )
            self.check_safe()
        log = self.log_steps()
        steps: list[list[str]] = []
        if existing != wanted or log:
            steps.append([SUDO, INSTALL, "-d", "-m", "755", "-o", "root", "-g", "wheel", str(LOG_DIR)])
        steps += log
        if existing != wanted:
            steps += [
                [SUDO, TEE, str(DAEMON)],
                [SUDO, CHMOD, "644", str(DAEMON)],
                [SUDO, CHOWN, "root:wheel", str(DAEMON)],
            ]
        for argv in steps:
            tee = argv[1] == TEE
            if self.step(argv, input=wanted if tee else None, shown=prompts.OLLAMA_ON_INPUT if tee else "") != 0:
                raise SystemExit(prompts.OLLAMA_SERVICE_FAILED.format(label=LABEL))
        if existing == wanted:
            self.restart()
            return
        if existing is not None:
            self.step([SUDO, LAUNCHCTL, "bootout", f"system/{LABEL}"])  # not loaded: it says so, and that is fine
            if not self.dry_run:
                self.sleep(1)  # launchd refuses a bootstrap right after a bootout (error 5)
        self.bootstrap()

    def wanted(self, num_ctx: int) -> bytes:
        """The service file setup writes for this layout."""
        if self.her_own:
            return her_plist(num_ctx)
        return daemon_plist(user=self.user, group=self.group, home=self.home, num_ctx=num_ctx)

    def wait(self) -> None:
        """Until the pinned version answers (WAIT_SECONDS at most), then the same checks as for one already there (her
        own Ollama: on her address, its account hers by `hers`). No answer names her log's read with admin rights (her
        account's, 0600: `cat` is refused); today's is yours, its path alone."""
        if self.dry_run:
            return
        deadline = self.clock() + WAIT_SECONDS
        while self.tools.version(self.port) != self.release.version:
            if self.clock() >= deadline:
                log = f"{SUDO} {ownuser.TAIL} -n 50 {LOG}" if self.her_own else str(LOG)
                raise SystemExit(prompts.OLLAMA_NOT_UP.format(seconds=WAIT_SECONDS, label=LABEL, log=log))
            self.sleep(1)
        found = self.running()
        if self.her_own:
            wrong = None if found is None else self.her_problem(found)
            if wrong is not None:
                raise SystemExit(stopped(*wrong))
            self.say(prompts.OLLAMA_HER_UP.format(version=self.release.version, user=ownuser.ROLE_USER))
            return
        wrong = problem(found, owner=os.getuid()) if found is not None else None
        if wrong is not None:
            raise SystemExit(stopped(*wrong))
        self.say(prompts.OLLAMA_UP.format(version=self.release.version))

    # ---------------------------------------------------------------- her own Ollama (layout "hers")

    def her_problem(self, found: Found) -> tuple[str, str] | None:
        """What makes the Ollama on her address not hers to keep, and its fix (None: hers): today's checks on her port,
        its account hers when `hers` holds (root's copy, run by her account), any other account a stop as before."""
        her_uid = self.tools.her_uid()
        if her_uid is None or not hers(found, self.tools.root, her_uid=her_uid):
            return self.taken(found)
        wrong = problem(found, owner=found.uid, port=HER_PORT, root=self.tools.root)
        if wrong is None:  # root's copy it runs: every program, library and metallib as at its install
            why = self.her_check(self._system(Path(found.program)).parent)
            if why:
                wrong = prompts.OLLAMA_OTHER.format(program=found.program, why=why), prompts.OLLAMA_HER_ADDRESS_FIX
        return wrong

    def taken(self, found: Found) -> tuple[str, str]:
        """Another program on her address: what it is and who runs it, and the fix."""
        account = prompts.OLLAMA_SHARED_UNREAD if found.uid is None else account_name(found.uid)
        program = found.program or prompts.OLLAMA_SHARED_NOT_FOUND
        text = prompts.OLLAMA_HER_ADDRESS_TAKEN.format(port=HER_PORT, program=program, account=account)
        return text, prompts.OLLAMA_HER_ADDRESS_FIX

    def run_hers(self, num_ctx: int) -> str:
        """Her own Ollama: the Ollama app when chosen and usable (else one line, then hers); hers that answers on her
        address kept (a "!" on another version; her service file written again when it is not the one setup writes);
        else installed: the read-only checks first (another program on her address, the folders on the way, the free
        disk, what else is on the Mac: one line each, never a stop), her account (setup's), the release downloaded
        and checked as you, root's copy made, hashed and checked, `current`, her log, her service, the wait. Safe to
        run again after a stop at any point."""
        if self.app is not None:
            used = self.try_app(self.app)
            if used is not None:
                return used
        self.used = "hers"
        found = self.running()
        if found is None:  # something may hold her address without answering as Ollama does: its socket says so
            heard = self.tools.listener(HER_PORT, by_service=False)
            found = Found(heard.program, None, addresses=heard.addresses, uid=heard.uid) if heard.program else None
        if found is not None:
            wrong = self.her_problem(found)
            if wrong is not None:
                raise SystemExit(stopped(*wrong))
            return self.keep_hers(found, num_ctx)
        self.found_lines()
        self.her_safe()
        self.her_disk()
        self.her_account()
        self.her_copy()
        self.her_link()
        self.daemon(num_ctx)
        self.wait()
        return str(HER_PROGRAM)

    def try_app(self, app: Path) -> str | None:
        """The Ollama app the person chose: used when it can be (`use_app`); else one line, and None (her own)."""
        self.trying_app = True
        try:
            program = self.use_app(app)
        except SystemExit as refused:
            if not isinstance(refused.code, str):
                raise
            why = refused.code.splitlines()[0].removeprefix("✗ Ollama: ").rstrip(".")
            self.say(prompts.OLLAMA_HER_APP_NOT_USABLE.format(why=why, name=self.name))
            self.app_said = True  # named once: the found lines leave it out
            return None
        finally:
            self.trying_app = False
        self.used = "app"
        return program

    def keep_hers(self, found: Found, num_ctx: int) -> str:
        """Her own Ollama answers: kept (no update in 0.9.8: another version is a "!"); her service file written again
        when it is not the one setup writes; her log made hers when it is not. No admin rights otherwise."""
        self.say(prompts.OLLAMA_HER_KEPT.format(version=found.version, program=found.program))
        if found.version != self.release.version:
            text = prompts.OLLAMA_UNTESTED.format(version=found.version, pin=self.release.version)
            self.say(f"! Ollama: {text}\n  Fix: {prompts.OLLAMA_HER_UNTESTED_FIX}")
        if self.service_changed(num_ctx):
            self.say(prompts.OLLAMA_SERVICE_CHANGED.format(daemon=DAEMON))
            self.daemon(num_ctx)
            self.wait()
        else:
            log = self.log_steps()
            if log:
                self.say(
                    (prompts.OLLAMA_LOG_PRIVATE_LEAD if self.boxed else prompts.OLLAMA_LOG_PRIVATE).format(log=LOG)
                )
                for argv in log:
                    self.step(argv)
        self.found_lines()
        return str(HER_PROGRAM)

    def leftover_program(self) -> str | None:
        """The program of setup's service file from an earlier try (today's layout, not hers), which her own
        Ollama's service file replaces (`daemon`: written, the old one booted out); "" when it names none, None when
        there is no such file."""
        path = self._system(DAEMON)
        if not os.path.lexists(path):
            return None
        service = read_plist(path)
        if service is not None and service.get("UserName") == ownuser.ROLE_USER:
            return None
        arguments = service.get("ProgramArguments") if service is not None else None
        first = arguments[0] if isinstance(arguments, list) and arguments and isinstance(arguments[0], str) else ""
        return self.real(first) if first.startswith("/") else ""

    def found_lines(self) -> None:
        """Every other Ollama on the Mac, one line each: it stays as it is, beside hers (never a stop, nothing to do);
        setup's own from an earlier try gives way to hers, said once (c-2, round 3)."""
        found: list[str] = []
        leftover = self.leftover_program()
        if leftover is not None:
            self.say(prompts.OLLAMA_HER_REPLACES.format(daemon=DAEMON))
        if not self.blind and self.tools.version(PORT) is not None:
            program = self.tools.listener(PORT).program
            ours = bool(program) and bool(leftover) and self.real(program) == leftover
            if not ours and (not program or (app_of(Path(program)) is None and not homebrews(Path(program)))):
                found.append(prompts.OLLAMA_FOUND_ANSWERING.format(program=program or prompts.OLLAMA_SHARED_NOT_FOUND))
        apps = ((self._system(APP), APP), *(() if self.blind else ((self.home / "Applications/Ollama.app", None),)))
        for app, shown in apps:
            if os.path.lexists(app) and not self.app_said:
                found.append(prompts.OLLAMA_FOUND_APP.format(path=shown or app))
        for cellar in CELLARS:
            if os.path.lexists(self._system(cellar / "ollama")):
                found.append(prompts.OLLAMA_FOUND_HOMEBREW.format(path=cellar / "ollama"))
        for what in found:
            self.say(prompts.OLLAMA_HER_FOUND.format(what=what, name=self.name))

    def her_safe(self) -> None:
        """Stops before any admin command when a folder on the way to root's copy (/usr, /usr/local, /usr/local/estia,
        its ollama folder) can be changed by an account other than root: whoever can rename it replaces her Ollama."""
        for path in (Path("/usr"), Path("/usr/local"), ownuser.CODE, HER_FOLDER):
            try:
                info = os.lstat(self._system(path))
            except FileNotFoundError:
                continue  # made by root's install -d
            except OSError:
                info = None
            if info is None or info.st_uid != ownuser.CODE_UID or stat.S_IMODE(info.st_mode) & 0o022:
                fix = f"{SUDO} {CHOWN} root:wheel {path} && {SUDO} {CHMOD} go-w {path}, then run scripts/setup.sh again"
                raise SystemExit(stopped(prompts.OLLAMA_HER_UNSAFE.format(path=path), fix))

    def her_disk(self) -> None:
        """Stops before any download or admin command when the volume her Ollama and her model go to is too full."""
        if self.blind:
            return
        free = self.tools.free_bytes(self._system(Path("/usr/local")))
        need = self.model_bytes + HER_ROOM
        if free is not None and free < need:  # as the plan's check rounds (c-6, round 3): free down, the need up
            shown, total = math.floor(free / 1e9), math.ceil(need / 1e9)
            raise SystemExit(
                stopped(
                    prompts.OLLAMA_HER_DISK.format(free=shown, need=total),
                    prompts.OLLAMA_HER_DISK_FIX.format(more=max(1, total - shown)),
                )
            )

    def her_account(self) -> None:
        """Her own macOS account, made by setup's command when it is not there yet, before anything downloads (her
        model then goes straight into her folder). Its output is not shown: setup says its own line. A failure stops
        here, nothing downloaded or installed."""
        if self.account is None or (not self.blind and self.tools.her_uid() is not None):
            return
        self.say(prompts.OLLAMA_HER_ACCOUNT.format(user=ownuser.ROLE_USER))
        if self.step(self.account, input=b"") != 0:
            text = prompts.OLLAMA_HER_ACCOUNT_FAILED.format(user=ownuser.ROLE_USER)
            # in the window its ✗ line (sysadminctl's words, the macOS version) is the reason (review W23)
            why = next(
                (
                    line.strip()[2:]
                    for line in reversed(self.tools.failed_output.splitlines())
                    if line.strip()[:2] == "✗ "
                ),
                "",
            )
            raise SystemExit(stopped(f"{text}: {why}" if why else text, prompts.OLLAMA_HER_ACCOUNT_FIX))
        if not self.dry_run:
            self.say(prompts.OLLAMA_HER_ACCOUNT_MADE.format(user=ownuser.ROLE_USER))

    @property
    def her_dest(self) -> Path:
        return HER_FOLDER / f"v{self.release.version}"

    def her_check(self, folder: Path) -> str:
        """Why root's copy of a release (on this Mac, `folder`) cannot be run as her ("" when it can): today's checks
        (`check_tree`) and its modes (`root_modes`)."""
        why = self.check_tree(folder)
        modes = root_modes(folder)
        return "; ".join(part for part in (why, modes) if part)

    def her_copy(self) -> None:
        """Root's copy of the pinned release in /usr/local/estia/ollama/v<version>: one there already is checked and
        kept (no download); else your download, checked first, copied by root, hashed again by root (by its exit code),
        unpacked, its modes fixed, checked, then renamed into place whole. A leftover from a stopped run is removed
        first, and every failure removes what this run made there."""
        dest = self.her_dest
        on_disk = self._system(dest)
        if not self.blind and os.path.lexists(on_disk):
            why = (
                self.her_check(on_disk)
                if on_disk.is_dir() and not on_disk.is_symlink()
                else prompts.OLLAMA_NOT_A_FOLDER
            )
            if why:
                raise SystemExit(prompts.OLLAMA_HER_FOLDER_REFUSED.format(folder=dest, why=why))
            self.say(prompts.OLLAMA_FOLDER_KEPT.format(folder=dest))
            return
        self.say(prompts.OLLAMA_HER_INSTALL.format(version=self.release.version, size=RELEASE_MB, folder=HER_FOLDER))
        cache = self.home / HER_CACHE
        if self.dry_run:
            work: Path | None = None
            tgz = cache / ".ollama-…" / "ollama-darwin.tgz"
            self.step(self.curl(tgz, quiet=self.watch is not None))
            self.say(f"  (its sha256 must be {self.release.sha256}; root then hashes its own copy again)")
        else:
            cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            work = Path(tempfile.mkdtemp(prefix=".ollama-", dir=cache))
            tgz = work / "ollama-darwin.tgz"
        try:
            if work is not None:
                code = self.download(tgz)
                if code != 0 or not tgz.is_file():
                    raise SystemExit(self.download_failed(code))
                digest = sha256_of(tgz)
                if digest != self.release.sha256:
                    why = prompts.OLLAMA_WHY_FINGERPRINT.format(got=digest, want=self.release.sha256)
                    raise SystemExit(prompts.OLLAMA_REFUSED.format(why=why))
            self.her_unpack(tgz)
        finally:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)  # your download, the folder mkdtemp made for it, only that
        if not self.dry_run:
            self.say(prompts.OLLAMA_INSTALLED.format(version=self.release.version, folder=dest))

    def her_unpack(self, tgz: Path) -> None:
        """Root's steps for its copy: its two folders, a leftover removed, your download copied (root's alone),
        hashed by root against the pin, unpacked as your tar would (root:wheel, no owner, modes, file flags or
        metadata from the archive), its modes made the same whatever sudo's umask, checked as you, then renamed into
        place. Each failure removes .unpacked and .download.tgz."""
        root_tgz = HER_FOLDER / ".download.tgz"
        unpacked = HER_FOLDER / ".unpacked"
        for folder in (ownuser.CODE, HER_FOLDER):
            self.root_step([INSTALL, "-d", "-m", "755", "-o", "root", "-g", "wheel", str(folder)])
        self.clean_up(root_tgz, unpacked)
        self.root_step([INSTALL, "-m", "600", "-o", "root", "-g", "wheel", str(tgz), str(root_tgz)])
        hashed = f"{self.release.sha256}  {root_tgz}\n".encode()
        if self.step([SUDO, SHASUM, "-a", "256", "-c"], input=hashed) != 0:
            self.clean_up(root_tgz, unpacked, always=True)
            why = prompts.OLLAMA_WHY_FINGERPRINT.format(got="not the pin", want=self.release.sha256)
            raise SystemExit(prompts.OLLAMA_REFUSED.format(why=why))
        self.root_step([MKDIR, "-m", "755", str(unpacked)], undo=(root_tgz, unpacked))
        tar = [TAR, "-xzf", str(root_tgz), *TAR_AS_ROOT, "-C", str(unpacked)]
        if self.step([SUDO, *tar]) != 0:
            self.clean_up(root_tgz, unpacked, always=True)
            raise SystemExit(prompts.OLLAMA_REFUSED.format(why=prompts.OLLAMA_WHY_UNPACKED))
        self.root_step([CHMOD, "-R", "u=rwX,go=rX", str(unpacked)], undo=(root_tgz, unpacked))
        self.root_step([RM, "-f", str(root_tgz)], undo=(root_tgz, unpacked))
        if self.dry_run:
            self.say(prompts.OLLAMA_CHECKS.format(team=TEAM_ID))
        else:
            why = self.her_check(self._system(unpacked))
            if why:
                self.clean_up(root_tgz, unpacked, always=True)
                raise SystemExit(prompts.OLLAMA_REFUSED.format(why=why))
        self.root_step([MV, str(unpacked), str(self.her_dest)], undo=(root_tgz, unpacked))

    def root_step(self, argv: Sequence[str], *, undo: tuple[Path, ...] = ()) -> None:
        """One of root's commands (sudo asks, or macOS's box): a failure removes `undo`'s leftovers, then stops."""
        if self.step([SUDO, *argv]) != 0:
            if undo:
                self.clean_up(*undo, always=True)
            raise SystemExit(prompts.OLLAMA_SERVICE_FAILED.format(label=LABEL))

    def clean_up(self, *paths: Path, always: bool = False) -> None:
        """A stopped run's .download.tgz and .unpacked in root's folder, removed by root (only when there; `always`:
        after this run's own failure, there or not as far as you can tell)."""
        for path in paths:
            if self.dry_run:
                continue
            if always or os.path.lexists(self._system(path)):
                self.step([SUDO, RM, "-rf" if path.name == ".unpacked" else "-f", str(path)])

    def her_link(self) -> None:
        """/usr/local/estia/ollama/current names this release (relative, inside root's folder), swapped in one rename
        by root; already so: nothing."""
        current = HER_FOLDER / "current"
        temporary = HER_FOLDER / ".current"
        target = f"v{self.release.version}"
        on_disk = self._system(current)
        if not self.blind:
            if on_disk.is_symlink() and os.readlink(on_disk) == target:
                return
            if os.path.lexists(on_disk) and not on_disk.is_symlink():
                raise SystemExit(prompts.OLLAMA_HER_CURRENT_NOT_LINK.format(path=current))
            if os.path.lexists(self._system(temporary)):
                self.root_step([RM, "-f", str(temporary)])
        self.root_step([LN, "-s", target, str(temporary)])
        self.root_step([MV, "-h", "-f", str(temporary), str(current)])


def config_values(path: Path) -> tuple[str | None, str | None]:
    """Your config's `ollama` and `ollama_base_url` (the layout's rule 2), read only: (None, None) when there is no
    config that reads; a config without the line gives its default."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None, None
    ollama, url = data.get("ollama", "build"), data.get("ollama_base_url", f"{BASE}/v1")
    return (ollama if isinstance(ollama, str) else None), (url if isinstance(url, str) else None)


def main(argv: list[str] | None = None) -> int:
    """`scripts/install-ollama.sh`: setup's Ollama step on its own (by hand, or again after a failure). Without a
    terminal (an AI's command tool) it changes nothing and reads nothing: sudo asks only in the person's own Terminal
    window, and it has no window of its own (setup's Ollama step asks in the Estia Setup window). Her own Ollama only
    when her account is there already and the install has no layout yet (it never makes her account); the Ollama app
    when the person chose it and it is on the Mac, as setup does (review of 30 Oct, P05: nothing installed beside
    it): by your config, or, once no config of yours reads (the move holds it), by setup's kept answer, read as
    untrusted (fix review, setup-macos-03); else today's."""
    from local_assistant import setup_answers
    from local_assistant.setup import choose_model, has_terminal, memory_gb  # setup imports this module

    parser = argparse.ArgumentParser(
        description="Keep the official Ollama that answers, or install Ollama's official build, checked, always on."
    )
    parser.add_argument("--dry-run", action="store_true", help="read and print; install and start nothing")
    args = parser.parse_args(argv)
    if not args.dry_run and not has_terminal():
        print(prompts.OLLAMA_NO_TERMINAL, file=sys.stderr)
        return 1
    memory = memory_gb()
    choice = choose_model(memory)
    if choice is None:
        print(prompts.SETUP_TOO_SMALL.format(memory=memory, name="Estia"), file=sys.stderr)
        return 1
    tools = Tools()
    home = owner_home()
    ollama, url = config_values(home / ".config/local-assistant/config.toml")
    found = layout_found(tools.root, config_ollama=ollama, config_url=url, moved=ownuser.moved(tools.root))
    there = ownuser.lookup_uid(ownuser.ROLE_USER) is not None
    layout: Layout = "hers" if found == "hers" or (found is None and NEW_INSTALLS == "hers" and there) else "older"
    unread = found == "older" and ollama is None and url is None  # no config of yours reads (moved: root holds it)
    kept = setup_answers.Answers.load(setup_answers.default_path(), say=print, writable=False) if unread else None
    chosen = found == "app" or (kept is not None and kept.get("ollama_app") == "app")  # setup's kept answer, as setup's
    app = app_on_mac(tools.root, home if tools.live else None) if chosen else None
    Install(tools, home, dry_run=args.dry_run or not tools.live, layout=layout, app=app).run(choice.num_ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())

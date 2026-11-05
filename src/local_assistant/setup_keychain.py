"""Setup's writes to your login keychain on this Mac (the Signal steps and the extras use it): a secret goes only on
the stdin of macOS's own security tool (`security -i`), never in its arguments, which ps shows to every user of the
Mac; it is then read back and compared in memory, never shown. Signal's Recovery key and Account ID trust no program
(`save(asks=True)`, owner 27 Oct: "A"): every read of them asks in a macOS dialog, and setup checks them by their
attributes only, never reading them back. A locked keychain (over SSH it usually is) is opened first on the terminal
itself: macOS asks for its password there, hidden; in the setup window, at the desk, macOS asks in its own box when the
write reaches the locked keychain (`save(opener=…)`). Setup locks it again once its writes are done (`lock_again`):
while it is open, a program you run can read the web keys through that same tool without asking.

Her own services never read your keychain (she runs as her own account): what she needs lives in her 0600 files."""

from __future__ import annotations

import enum
import hmac
import subprocess
from collections.abc import Callable
from pathlib import Path

from local_assistant import hostsec

_UNSAFE = frozenset('"\\')  # what a double-quoted argument of security -i cannot hold
UNLOCK_SECONDS = 300  # how long macOS asks for the keychain's password: on the terminal, or in its own box (S06)
CHECK_SECONDS = 10  # an item's attributes, a delete: macOS asks nothing for them on an open keychain
WRITE_SECONDS = 30  # a write or a read back on an open keychain


class Saved(enum.Enum):
    """What a save did, true only when kept: the caller says it in its own words."""

    KEPT = "kept"
    NOT_KEPT = "not kept"  # nothing it could change: a copy from before, if any, stays
    REMOVED = "removed"  # not kept, and the copy from before is removed too (deleted first, then not written again)
    LOCKED = "locked"  # the login keychain stayed locked: nothing changed

    def __bool__(self) -> bool:
        return self is Saved.KEPT


def login_keychain() -> Path:
    return Path.home() / "Library" / "Keychains" / "login.keychain-db"


def _security() -> str:
    """macOS's own tool by its full path (a test's fake under the tools' root)."""
    return str(hostsec.tool_root() / "usr/bin/security")


def _fits(*texts: str) -> bool:
    """Each text can go on security -i's one line inside double quotes: no quote, backslash or control character."""
    return all(
        text and not any(char in _UNSAFE or ord(char) < 0x20 or ord(char) == 0x7F for char in text) for text in texts
    )


def unlocked() -> bool:
    """The login keychain is open (show-keychain-info answers without asking anything)."""
    try:
        done = subprocess.run(
            [_security(), "show-keychain-info", str(login_keychain())],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def unlock() -> bool:
    """Open the login keychain on the terminal: macOS asks for its password there, hidden (no password passes through
    setup). True once it is open."""
    try:
        done = subprocess.run(
            [_security(), "unlock-keychain", str(login_keychain())], timeout=UNLOCK_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and unlocked()


NOT_FOUND = 44  # security's status for errSecItemNotFound: the search answered, and none is there


def found(service: str, account: str, *, seconds: float = CHECK_SECONDS) -> bool | None:
    """`has`, with no answer apart: True or False when the search answered (found, or security's NOT_FOUND); None
    when it did not (macOS's own box cancelled or left unanswered, a locked keychain over SSH, no security)."""
    try:
        done = subprocess.run(
            [_security(), "find-generic-password", "-a", account, "-s", service, str(login_keychain())],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode == 0:
        return True
    return False if done.returncode == NOT_FOUND else None


def has(service: str, account: str, *, seconds: float = CHECK_SECONDS) -> bool:
    """An item for this service and account is in your login keychain (its attributes only: no password is read, and
    a locked keychain asks nothing; `seconds`: longer where macOS's own box may ask after all)."""
    return found(service, account, seconds=seconds) is True


def _unanswered(there: bool | None, boxed: bool) -> bool:
    """`boxed` (macOS's own box asks at the locked keychain): a search with no answer while the keychain stays locked,
    its box cancelled or left unanswered, ends the save, never a second box (s06-attribute-search: whether macOS asks
    at the attribute search is the desk check; "not found" is an answer, so a search that asks nothing changes
    nothing)."""
    return boxed and there is None and not unlocked()


def lock_again(opened: list[bool]) -> None:
    """The login keychain locked again when a save of this run opened it (`opened`, as `save` keeps it): while it is
    open, a program you run can read its items through macOS's own security tool without asking."""
    if not opened:
        return
    opened.clear()
    try:
        subprocess.run(
            [_security(), "lock-keychain", str(login_keychain())],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _delete(service: str, account: str, keychain: str, seconds: float = CHECK_SECONDS, *, boxed: bool = False) -> bool:
    """The item for this service and account deleted, without reading it (macOS asks nothing for a delete on an open
    keychain); True once none is left. `boxed` (macOS's own box asks at a change of the locked keychain): a delete
    that failed while the keychain stays locked (the box left unanswered or cancelled) is the last one tried, never a
    second box (fix review, signal-S06-asks-path-asks-again), and so is a search left without an answer
    (`_unanswered`)."""
    for _ in range(3):  # one at a time, as security deletes them
        there = found(service, account, seconds=seconds)
        if _unanswered(there, boxed):
            return False
        if not there:
            return True
        try:
            done = subprocess.run(
                [_security(), "delete-generic-password", "-a", account, "-s", service, keychain],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if boxed and done.returncode != 0 and not unlocked():
            return False
    return not has(service, account, seconds=seconds)


def save(
    service: str,
    account: str,
    secret: str,
    *,
    label: str,
    before_unlock: Callable[[], None] = lambda: None,
    opened: list[bool] | None = None,
    asks: bool = False,
    opener: Callable[[], bool] | None = None,
) -> Saved:
    """Keep a secret in your login keychain as a generic password (service, account, label), replacing one there:
    written once from what was typed, on security's stdin only, then read back and compared. With `asks` (the owner's
    choice for Signal's Recovery key and Account ID, 27 Oct) it trusts no program, not even macOS's own security tool
    (-T ""), so every read of it asks in a macOS dialog: one there before is deleted first (an update keeps an item's
    access), and the save is checked by the item's attributes only, never read back. A locked keychain is opened first
    (`before_unlock` says so before macOS asks; `opened` then gets a True, for `lock_again`): on the terminal
    (`unlock`, the default), or, given an `opener` (the setup window's: True at once), by macOS's own box when the
    write reaches it: each security call then waits as long as macOS's password asks do (`UNLOCK_SECONDS`), and a
    save that leaves the keychain locked (the box left unanswered, or cancelled) is LOCKED, so the caller asks no
    second time (review S06). What it did (`Saved`, true only when kept): nothing is printed, and the caller says it
    in its own words."""
    if not _fits(service, account, secret, label):
        return Saved.NOT_KEPT
    boxed = False  # macOS's own box asks at the write (the window's opener): its calls get the box's time
    if not unlocked():
        before_unlock()
        if not (opener() if opener is not None else unlock()):
            return Saved.LOCKED
        if opened is not None:
            opened.append(True)
        boxed = opener is not None
    keychain = str(login_keychain())
    if not _fits(keychain):
        return Saved.NOT_KEPT
    saved = _write(service, account, secret, label, keychain, asks=asks, boxed=boxed)
    if boxed and saved is Saved.NOT_KEPT and not unlocked():
        return Saved.LOCKED
    return saved


def _write(service: str, account: str, secret: str, label: str, keychain: str, *, asks: bool, boxed: bool) -> Saved:
    """`save`'s write, its keychain open or macOS's own box to ask at it (`boxed`)."""
    if asks:
        return _save_asking(service, account, secret, label, keychain, boxed=boxed)
    seconds = UNLOCK_SECONDS if boxed else WRITE_SECONDS
    command = f'add-generic-password -U -a "{account}" -s "{service}" -l "{label}" -w "{secret}" "{keychain}"\n'
    try:
        subprocess.run([_security(), "-i"], input=command, capture_output=True, text=True, timeout=seconds, check=False)
        if boxed and not unlocked():  # the box left unanswered or cancelled: a read back would ask a second time
            return Saved.NOT_KEPT
        found = subprocess.run(
            [_security(), "find-generic-password", "-a", account, "-s", service, "-w", keychain],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return Saved.NOT_KEPT
    same = found.returncode == 0 and hmac.compare_digest(found.stdout.rstrip("\n").encode(), secret.encode())
    return Saved.KEPT if same else Saved.NOT_KEPT


def _save_asking(service: str, account: str, secret: str, label: str, keychain: str, *, boxed: bool = False) -> Saved:
    """`save` with `asks`: no item left for this service and account, then one added that trusts no program (-T ""),
    then its attributes found (no secret is read, and no dialog shows). REMOVED when a copy from before was deleted
    and none is there now (review SEC-5). `boxed`: macOS's own box may ask at a change of the locked keychain; a
    delete it left undone ends the save (`_delete`), so a box left unanswered or cancelled at a delete is not asked
    again; so does a search it left without an answer (`_unanswered`: whether macOS asks at the attribute search,
    `has`, too is the desk check; second fix review of 0.9.8, setup-s06-asks-once-overclaim; s06-attribute-search)."""
    check = UNLOCK_SECONDS if boxed else CHECK_SECONDS
    there = found(service, account, seconds=check)
    if _unanswered(there, boxed):
        return Saved.NOT_KEPT  # nothing changed: save() makes it LOCKED
    before = there is True
    if not _delete(service, account, keychain, check, boxed=boxed):
        return Saved.NOT_KEPT  # one is left: the copy from before stays
    command = f'add-generic-password -a "{account}" -s "{service}" -l "{label}" -T "" -w "{secret}" "{keychain}"\n'
    try:
        done = subprocess.run(
            [_security(), "-i"],
            input=command,
            capture_output=True,
            text=True,
            timeout=UNLOCK_SECONDS if boxed else WRITE_SECONDS,
            check=False,
        )
        added = done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        added = False
    there = has(service, account, seconds=check)
    if added and there:
        return Saved.KEPT
    return Saved.REMOVED if before and not there else Saved.NOT_KEPT

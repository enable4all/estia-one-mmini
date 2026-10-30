"""What setup hands over to her first start: About you and the home area, from the answers of setup's plan.

Setup writes `<her config folder>/setup-answers.toml` (0600, in one rename) right after her config on a first install,
before the move, so the move takes it to her with the config, and no root-run tool is needed. Her first start reads it
as her, never as root (Lifecycle.setup_facts), as untrusted: a regular file of hers alone (0600), not a link, at most
16 KB, valid TOML of this format and not imported yet. Each filled line becomes one of her profile facts in the user's
own words (source "remember"), in its category; then the file is marked imported, and her store keeps a key of its own
(lifecycle.SETUP_FACTS_KEY), so a copy that comes back with the flag unset (a rollback's) imports nothing.

No captcha link, code, PIN, key or password is ever in it: only what the About you and home area questions keep."""

from __future__ import annotations

import errno
import logging
import os
import re
import secrets
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from local_assistant import prompts
from local_assistant.timefmt import iso_to_epoch

if TYPE_CHECKING:
    from local_assistant.app import App

log = logging.getLogger("local_assistant")  # the app's own logger: its lines keep their name in bridge.log

HANDOVER = "setup-answers.toml"
STATE_KEY = "setup_facts_imported"  # her store's own record: a copy that comes back with its flag unset imports nothing
# the note's message ids until each went out (comma-separated): owed as the welcome is, past the recovery window too
OWED_KEY = "setup_facts_owed"
FORMAT = 1
MAX_BYTES = 16 * 1024
HEADER = (
    "# Estia: what setup handed over for her first start (About you and the home area); read once, then marked\n"
    "# imported. No code, PIN, key or password is ever kept here. /profile changes or removes each fact.\n"
)
# each About you line's category, as the profile sorts facts (owner, 26 Oct: interests are Music when only music)
CATEGORY = {"name": "About", "work": "Work", "people": "Family", "interests": "Other", "day": "About"}
ORDER = ("name", "work", "people", "interests", "day")
# a small English list: interests made only of these words (and "and", "or") are Music
_MUSIC = frozenset(
    {
        "music",
        "jazz",
        "blues",
        "rock",
        "pop",
        "classical",
        "opera",
        "folk",
        "soul",
        "funk",
        "reggae",
        "metal",
        "punk",
        "rap",
        "hip-hop",
        "techno",
        "piano",
        "guitar",
        "violin",
        "cello",
        "drums",
        "bass",
        "saxophone",
        "singing",
        "choir",
        "concerts",
    }
)
_JOINS = frozenset({"and", "or", "&"})
_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r"}


def _uid() -> int:
    return os.geteuid()


def _string(text: str) -> str:
    """A TOML basic string: quotes, backslashes and every control character escaped."""
    out = []
    for char in text:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        elif 0xD800 <= ord(char) <= 0xDFFF:
            out.append("�")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _table(name: str, values: Mapping[str, str]) -> list[str]:
    return [f"\n[{name}]\n", *(f"{key} = {_string(value)}\n" for key, value in values.items())]


def write(folder: Path, about_you: Mapping[str, str], home_area: Mapping[str, str], *, imported: bool = False) -> None:
    """The handover beside her config, 0600, in one rename (a temporary file never a link, synced first). Nothing is
    written when no line is filled: she then learns as you talk. Imported, it is written with its lines empty."""
    about = {key: str(about_you.get(key, "")) for key in ORDER}
    home = {key: str(home_area[key]) for key in ("street", "city") if home_area.get(key)}
    if not imported and not any(about.values()) and len(home) < 2:
        return
    lines = [HEADER, f"format = {FORMAT}\n", f"imported = {'true' if imported else 'false'}\n"]
    lines += _table("about_you", about)
    if len(home) == 2:
        lines += _table("home_area", home)
    target = folder / HANDOVER
    temporary = folder / f".{HANDOVER}.{os.getpid()}.{secrets.token_hex(4)}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write("".join(lines))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _checked(path: Path) -> tuple[dict[str, object] | None, str]:
    """The file's TOML, read as untrusted: a regular file of this user's alone, not a link, at most MAX_BYTES, this
    format; else None and the check it failed, in fixed words ("" when there is no file)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        return None, "a link" if exc.errno == errno.ELOOP else f"unreadable ({type(exc).__name__})"
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, "not a regular file"
        if info.st_uid != _uid():
            return None, "another account's"
        if info.st_mode & 0o077:
            return None, "readable by others"
        data = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        return None, "over 16 KB"
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None, "not valid TOML"
    return (parsed, "") if parsed.get("format") == FORMAT else (None, "another format")


def _read(path: Path) -> dict[str, object] | None:
    """The file's TOML when it passes every check (`_checked`); None otherwise (also when there is none)."""
    return _checked(path)[0]


def _strings(table: object) -> dict[str, str]:
    return {str(k): v for k, v in table.items() if isinstance(v, str)} if isinstance(table, dict) else {}


def facts(path: Path) -> list[str] | None:
    """The facts to import, in order; None when there is nothing to import (no file, one that cannot be trusted, or
    one imported already); a file that fails a check gets one line in her log naming it, metadata only (review of 0.9.8,
    memory-5: it was skipped without a word, so About you seemed lost)."""
    parsed, failed = _checked(path)
    if parsed is not None and parsed.get("imported") is not False:
        failed = "imported already"
    if failed:
        log.warning("setup's handover skipped: %s", failed)
    if parsed is None or failed:
        return None
    return fact_texts(_strings(parsed.get("about_you")), _strings(parsed.get("home_area")))


def fact_texts(about_you: Mapping[str, str], home_area: Mapping[str, str]) -> list[str]:
    """Each filled line as a fact in the user's own words, in its category: the name as "The user goes by Alex."
    (owner OK, 26 Oct, so she reads it as the user's name), the interests as Music when they are only music, the
    usual day under About, the home area as maps.home_area reads it."""
    found = []
    for key in ORDER:
        text = " ".join(str(about_you.get(key, "")).split())
        if not text:
            continue
        if key == "name":
            found.append(f"About: The user goes by {text.rstrip(' .')}.")
        elif key == "interests":
            found.append(f"{'Music' if _all_music(text) else 'Other'}: Interests: {text}")
        elif key == "day":
            found.append(f"About: Usual day: {text}")
        else:
            found.append(f"{CATEGORY[key]}: {text}")
    street, city = (" ".join(str(home_area.get(key, "")).split()) for key in ("street", "city"))
    if street and city:
        found.append(f"Home: Home area: {street}, {city}")
    return found


def _all_music(text: str) -> bool:
    words = [word for word in re.findall(r"[a-z&-]+", text.lower()) if word not in _JOINS]
    return bool(words) and all(word in _MUSIC for word in words)


def mark_imported(path: Path) -> None:
    """The flag set and the lines emptied, in one rename (review SEC-10: the facts are hers in /profile now, where each
    can be changed or removed; no second copy stays here); a file that cannot be read is left as it is."""
    if _read(path) is None:
        return
    write(path.parent, {}, {}, imported=True)


async def send_owed(app: App) -> None:
    """Setup's note past the recovery window (review of 0.9.8, memory-4: recovery and the resend pass send a note only
    within it, so after a longer Signal outage at her first start it never went out): at each start and each resend
    pass, every one still unsent past the window goes again, to an account still allowed whose safety number did not
    change; within the window those passes send it. Its key goes once none is left unsent. Locked: it stays owed."""
    raw = app.store.get_state(OWED_KEY)
    if raw is None or app.guard.locked:
        return
    ids = {int(part) for part in raw.split(",") if part.isdecimal()}
    now, window = app._clock(), app.cfg.recover_window_seconds
    allowed = {aci.lower() for aci in app.cfg.allowed_acis} - {aci.lower() for aci in app.signal_host.identity_changed}
    for out in app.store.unsent_outgoing():
        if out.id in ids and out.peer_aci.lower() in allowed and now - iso_to_epoch(out.created_at) > window:
            await app._send(out.id)
    left = ids & {out.id for out in app.store.unsent_outgoing()}
    if not left:
        app.store.delete_state(OWED_KEY)
    elif left != ids:
        app.store.set_state(OWED_KEY, ",".join(str(out_id) for out_id in sorted(left)))


async def import_facts(app: App) -> None:
    """Her first start (Lifecycle.setup_facts, after the welcome): the handover's facts, once, each one of the user's
    own (source "remember", the user's own words) through the profile's own checks (a refused one is skipped); in the
    same transaction her store's key and code's note "From setup, I keep in mind" (in the quiet-time note's layout,
    `prompts.fact_note`) to each allowed user, stored as code's other notes are: sent at once, a send that fails tried
    again by the resend pass and by recovery at a start, and in her history only once sent (code_notes), so a stop or
    a failed send never loses it and never imports twice (Fable, 27 Oct); then the file marked imported, and the
    profile she reads in every prompt refreshed, so her next answer knows them (Fable, 27 Oct). Read as her, never as
    root, as untrusted. Locked: the next start."""
    path = app.cfg.setup_answers_path
    if path is None or app.guard.locked or app.store.get_state(STATE_KEY):
        return
    found = facts(path)
    if found is None:
        return
    notes: list[int] = []
    with app.store.tx():
        added = [fact for fact in found if app.profile.add(fact, source_kind="remember").added]
        app.store.set_state(STATE_KEY, f"{app._clock():.0f}")
        if added:
            text = prompts.fact_note(prompts.SETUP_FACTS_HEADER, added, prompts.SETUP_FACTS_CLOSING)
            conversation = app.store.current_conversation().id
            notes = [
                app.store.add_outgoing(conversation, aci, "note", text, None) for aci in sorted(app.cfg.allowed_acis)
            ]
            app.store.set_state(OWED_KEY, ",".join(str(out_id) for out_id in notes))
    try:
        mark_imported(path)
    except OSError as exc:  # her store's key keeps it from coming twice
        log.warning("setup's handover not marked imported: %s", type(exc).__name__)
    log.info("setup's handover: %d of %d facts kept", len(added), len(found))
    if not added:
        return
    app.context.refresh_snapshot()
    for out_id in notes:
        await app._send(out_id)

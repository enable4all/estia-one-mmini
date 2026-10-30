"""`python -I -m local_assistant.herconfig apply <change> [args]`: her config changed as her, for setup's window (owner,
29 Oct: every step in the window; nano needs Terminal). Run by root as her own account (`sudo -n -u _estia`, from her
release, never the checkout), on the person's yes at a window screen that shows the lines first.

Only the changes setup names, from a fixed list (`CHANGES`): calls on ([voice] and [calls]), one of the sections the
extras switch on, [projects] with its folder and level, her built-in style, the Ollama app, the model's pin. Every
other line stays as it is. The old config is kept as config.toml.bak-<time> (0600, never over an earlier copy); the new
text is loaded as she loads it (`load_config`) from a 0600 file beside it and takes her config's place in one rename,
only when it loads. Her config is her home's (HOME=/var/estia), never a path from the command line; a link in its
place, or a config others may read, is never changed. One line says what happened; exit 0 (changed or there
already), 1 (not changed), 2 (a change setup does not make)."""

from __future__ import annotations

import os
import re
import stat
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from local_assistant import configtext
from local_assistant.config import PROJECT_LEVELS, STYLES, ConfigError, load_config

SECTIONS = frozenset({"chords", "maps", "web", "documents"})  # what the extras switch on with nothing else to fill
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
CHANGED = "✓ Her config: {what} (a copy of the old one kept: {copy})."
ALREADY = "✓ Her config: it has these lines already; nothing changed."
NO_PIN = "✓ Her config: it pins no model (no model_digest line); nothing to change."
NOT_CHANGED = "✗ Her config: not changed ({why})."
USAGE = "usage: python -I -m local_assistant.herconfig apply <change> [args]"


def _change(words: Sequence[str]) -> tuple[Callable[[str], str], str, str] | None:
    """The text change for `words`, the lines it adds as a screen shows them, and what it did; None for anything that is
    not one of setup's own changes."""
    match list(words):
        case ["calls"]:
            return (
                lambda text: configtext.with_section(configtext.with_voice(text), "calls"),
                "[voice] (its # taken off, with its lines)\n[calls]",
                "calls on",
            )
        case ["section", name] if name in SECTIONS:
            return lambda text: configtext.with_section(text, name), f"[{name}]", f"[{name}] on"
        case ["projects", root, level] if level in PROJECT_LEVELS and root.startswith("/") and "\n" not in root:
            shown = f'[projects]\nroot = "{root}"\ndefault = "{level}"'
            return lambda text: configtext.with_projects(text, root, level), shown, "projects on"
        case ["style", style] if style in STYLES:
            return (
                lambda text: configtext.with_style(text, style),
                f'[personality]\nstyle = "{style}"',
                f"style {style}",
            )
        case ["ollama-app"]:
            return lambda text: configtext.with_top_line(text, "ollama", "app"), 'ollama = "app"', "the Ollama app"
        case ["model-digest", digest] if _DIGEST.fullmatch(digest):  # in place of her pin; none: she pins nothing
            line = f'model_digest = "sha256:{digest}"'
            pinned = re.compile(r'^model_digest = "[^"\n]*"', re.MULTILINE)
            return lambda text: pinned.sub(lambda _: line, text, count=1), line, "the model's new pin"
    return None


def lines(words: Sequence[str]) -> str:
    """The lines a change adds, as the window's screen shows them before the yes."""
    found = _change(words)
    if found is None:
        raise ValueError(f"not one of setup's changes: {' '.join(words)}")
    return found[1]


def _read(path: Path) -> str:
    """Her config's text: a regular file at that name with one name, never a link, readable only by its owner."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("not a file of its own")
        if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise OSError("others may read it")
        return file.read().decode("utf-8")


def _private(path: Path, text: str, *, exclusive: bool) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_EXCL if exclusive else os.O_TRUNC)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.chmod(path, 0o600, follow_symlinks=False)


def apply(words: Sequence[str], path: Path, *, say: Callable[[str], None] = print) -> int:
    found = _change(words)
    if found is None:
        say(NOT_CHANGED.format(why="not one of setup's changes"))
        return 2
    change, _shown, what = found
    try:
        text = _read(path)
    except (OSError, UnicodeDecodeError) as exc:
        say(NOT_CHANGED.format(why=f"{path} could not be read: {exc.strerror if isinstance(exc, OSError) else exc}"))
        return 1
    if words[:1] == ["projects"] and configtext.has_section(text, "projects"):  # its folder and level: by hand
        say(NOT_CHANGED.format(why="it has [projects] already"))
        return 1
    new = change(text)
    if new == text:
        unpinned = words[:1] == ["model-digest"] and not re.search(r"^model_digest = ", text, re.MULTILINE)
        say(NO_PIN if unpinned else ALREADY)
        return 0
    trial = path.with_name(f".config-{os.getpid()}.toml")
    try:
        _private(trial, new, exclusive=True)
        try:
            load_config(trial)  # as she loads it: a change that would stop her from starting is never made
        except ConfigError as exc:
            say(NOT_CHANGED.format(why=f"it would not load: {exc}"))
            return 1
        copy = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        _private(copy, text, exclusive=True)
        os.replace(trial, path)
    except OSError as exc:
        say(NOT_CHANGED.format(why=exc.strerror or type(exc).__name__))
        return 1
    finally:
        trial.unlink(missing_ok=True)
    say(CHANGED.format(what=what, copy=copy.name))
    return 0


def main(argv: list[str] | None = None, *, config: Path | None = None) -> int:
    words = list(sys.argv[1:] if argv is None else argv)
    if words[:1] != ["apply"]:
        print(USAGE, file=sys.stderr)
        return 2
    path = config if config is not None else Path.home() / ".config" / "local-assistant" / "config.toml"
    return apply(words[1:], path)


if __name__ == "__main__":
    raise SystemExit(main())

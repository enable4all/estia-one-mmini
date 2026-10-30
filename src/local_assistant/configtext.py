"""Changes to a config's text that setup makes and that her own `herconfig` makes as her (one place, so both keep the
same rules): a section switched on where the example has it commented out, [projects] with its folder and level, and
the top-level and [personality] lines setup sets. Each keeps every other line as it was: the user's comments, keys and
order. Nothing here reads or writes a file."""

from __future__ import annotations

import json
import re

from local_assistant.config import PROJECT_LEVELS

# the example's [projects] block, commented out (projects are an extra, off by default)
PROJECTS_LINES = (
    re.compile(r"^#\[projects\]$", re.MULTILINE),
    re.compile(r'^#root = "~/dev"', re.MULTILINE),
    re.compile(r'^#default = "summary"', re.MULTILINE),
)
_VOICE_BLOCK = re.compile(r"^#\[voice\]\n((?:#[a-z_]+ = [^\n]*\n)*)", re.MULTILINE)


def with_projects(text: str, folder: str, level: str) -> str:
    """A config with [projects] on: its folder (as typed: ~ is the home of whoever runs her) and level. The example's
    commented block is filled in where it stands; a config without it gets the section at its end."""
    if level not in PROJECT_LEVELS:
        raise ValueError(f"the projects level must be one of {', '.join(PROJECT_LEVELS)}")
    root_line = f"root = {json.dumps(folder, ensure_ascii=False)}"
    if all(len(line.findall(text)) == 1 for line in PROJECTS_LINES):
        header, root, default = PROJECTS_LINES
        text = header.sub("[projects]", text)
        text = root.sub(lambda _: root_line, text)
        return default.sub(f'default = "{level}"', text)
    return f'{text.rstrip(chr(10))}\n\n[projects]\n{root_line}\ndefault = "{level}"\n'


def with_chords(text: str) -> str:
    """A config with [chords] on: the example's commented section switched on where it stands; a config without it
    gets the section at its end."""
    return with_section(text, "chords")


def with_section(text: str, section: str) -> str:
    """A config with [section] on: one that has it on already stays as it is; else the example's commented line
    switched on where it stands (its commented keys stay as they are); a config without it gets the section at its
    end."""
    if has_section(text, section):
        return text
    line = re.compile(rf"^# ?\[{re.escape(section)}\]$", re.MULTILINE)  # the example's line, commented out
    if len(line.findall(text)) == 1:
        return line.sub(f"[{section}]", text)
    return f"{text.rstrip(chr(10))}\n\n[{section}]\n"


def has_section(text: str, section: str) -> bool:
    """The config has [section] on (a line of its own, not commented out)."""
    return re.search(rf"^\[{re.escape(section)}\]\s*(#.*)?$", text, re.MULTILINE) is not None


def with_voice(text: str) -> str:
    """A config with [voice] on: the example's commented block taken out of its comments with its own lines (her voice
    among them) where it stands; one with [voice] already is left as it is; a config without the block gets the
    section at its end (its defaults)."""
    if has_section(text, "voice"):
        return text
    found = _VOICE_BLOCK.search(text)
    if found is None:
        return f"{text.rstrip(chr(10))}\n\n[voice]\n"
    lines = "".join(line[1:] + "\n" for line in found.group(1).splitlines())
    return text[: found.start()] + "[voice]\n" + lines + text[found.end() :]


def with_top_line(text: str, key: str, value: str) -> str:
    """A config with its top-level `key = value` (a TOML string): the line there already replaced, else its example
    line commented out switched on, else a line before the first section."""
    line = f"{key} = {json.dumps(value)}"
    for pattern in (rf"^{re.escape(key)} = [^\n]*$", rf"^#{re.escape(key)} = [^\n]*$"):
        found = re.compile(pattern, re.MULTILINE)
        if found.search(text):
            return found.sub(lambda _: line, text, count=1)
    first = re.search(r"^\[", text, re.MULTILINE)
    at = first.start() if first else len(text)
    return f"{text[:at]}{line}\n\n{text[at:]}" if first else f"{text.rstrip(chr(10))}\n{line}\n"


def with_style(text: str, style: str) -> str:
    """A config with [personality] style = `style`: its style line replaced (a comment after it kept), else the line
    added under [personality], else the section at its end."""
    line = f"style = {json.dumps(style)}"
    found = re.compile(r'^style = "[^"\n]*"', re.MULTILINE)
    if found.search(text):
        return found.sub(lambda _: line, text, count=1)
    header = re.compile(r"^\[personality\][^\n]*\n", re.MULTILINE)
    if header.search(text):
        return header.sub(lambda match: match.group(0) + line + "\n", text, count=1)
    return f"{text.rstrip(chr(10))}\n\n[personality]\n{line}\n"

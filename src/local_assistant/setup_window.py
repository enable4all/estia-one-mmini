"""The Estia Setup window: setup's questions at the desk, in a window of its own (tools/setup-window, a small Swift app
that draws a local page in WKWebView: no network, no port). Setup stays the only reader and writer of the answers
file and runs every check; the window only shows each question and hands back what was typed or picked.

The protocol: one JSON object per line, UTF-8, `t` first; setup writes to the window's stdin (at most
`FROM_ENGINE_MAX` bytes a line) and reads its stdout (at most `FROM_WINDOW_MAX`). Each side refuses a line that is not
exactly one of its messages (the same rules as tools/setup-window's Message.swift; both are tested on the shared
fixtures in its Tests folder):

  setup  -> window   hello {v, name}; screen {id, kind, step, steps, title, lead, help, ask, note, options, fields,
                     placeholder, recommended, layout, back, kept?, live?, links?, later?, more?, stop?}; checked {id,
                     ok, message, shown, live};
                     progress {line, again?}; terminal {why}; done {lines};
                     admin {id, argv, input, open, why, seconds?}; admin_input {id, text}; admin_end {id}
  window -> setup    ready {v}; check {id, value}; answer {id, value}; back; stop; closed;
                     admin_line {id, line}; admin_done {id, code, refused, unrun?} (the window's admin runner only,
                     never its page)

A value is a string, a bool, a list of strings or an object of strings. A secret travels from the window to setup,
and from setup only into the stdin of the command that needs it (an admin command's: admin's input, admin_input; or
one of yours, a pipe of setup's own): never sent back to the page, put in a program's arguments, saved or logged. `terminal` is setup's hand-over: it goes on in Terminal from there.

Protocol 2 (owner, 29 Oct: "can we have Terminal in the window?"): every step runs in the window. A command that needs
admin rights goes to the window as `admin` (its words as a list, never shell text); the window runs it as root
through macOS's own box ("Estia Setup wants to make changes."), says each line of its output (`admin_line`), then its
result (`admin_done`: its exit code, or `refused` when the box was cancelled). The password goes only to macOS."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import plistlib
import pwd
import re
import select
import shlex
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, get_args

from local_assistant import hostsec, prompts, setup_admin
from local_assistant.hostsec import SUDO
from local_assistant.setup_admin import ASK_MARK, Ran
from local_assistant.setup_ai import own_words
from local_assistant.setup_face import Back, Face, choose_face, kept_line, sentence, stop_parts, stop_text
from local_assistant.setup_plan import QUESTIONS, SESSION_KEYS, SSH_KEYS, question
from local_assistant.setup_questions import (
    ID,
    RECOMMENDED,
    Checked,
    Kind,
    Option,
    Question,
    Value,
    json_value,
    plain_text,
)

if TYPE_CHECKING:
    from local_assistant.setup import Wizard
    from local_assistant.setup_ai import Run
    from local_assistant.setup_plan import Mac

PROTOCOL = 2
FROM_ENGINE_MAX = 256 * 1024  # bytes in one line setup sends
FROM_WINDOW_MAX = 64 * 1024  # bytes in one line the window sends
ADMIN_WORDS = 128  # words in one admin command at most (AdminCore.maxWords)
ADMIN_BYTES = 128 * 1024  # their bytes in all
ADMIN_WHY = 200  # characters in the box's line
ADMIN_SECONDS = 86_400  # an admin command's time limit at most (AdminCore.maxSeconds)
# a one-shot admin command with input, without a limit of its own: Host.run's default (review W08: one whose input
# was never opened, the window gone, waited for a writer until the Mac restarted)
PIPED_SECONDS = 600.0
# a command that ends with its input (`ends_on_stop`): how often a Ctrl-C noted in Terminal is looked for meanwhile
STOP_LOOK_SECONDS = 0.25
KINDS = frozenset({*get_args(Kind), "kept"})  # the question list's kinds, and the window's own list of kept answers
_HTTPS = re.compile(r"^https://[^\s/?#]+[^\s]*$")
_TOKEN = re.compile(r"^[a-z0-9]{1,16}$")


class ProtocolError(ValueError):
    """A line that is not one of the protocol's messages."""


Check = Callable[[object], bool]


def _text(value: object) -> bool:
    return isinstance(value, str) and "\x00" not in value


def _flag(value: object) -> bool:
    return isinstance(value, bool)


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive(value: object) -> bool:
    return _count(value) and value != 0


def _qid(value: object) -> bool:
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _name(value: object) -> bool:
    """An option's or a field's id: a short line of text (her names, language codes, extras)."""
    return _text(value) and isinstance(value, str) and 0 < len(value) <= 64 and "\n" not in value


def _texts(value: object) -> bool:
    return isinstance(value, list) and all(_text(item) for item in value)


def _pair(value: object) -> bool:
    """(what it says, the fix): two texts."""
    return isinstance(value, list) and len(value) == 2 and all(_text(item) for item in value)


def _link(value: object) -> bool:
    """(label, https address): opened in the Mac's browser, never in the window."""
    return _pair(value) and isinstance(value, list) and bool(_HTTPS.fullmatch(value[1]))


def _value(value: object) -> bool:
    """A string, a bool, a list of strings or an object of strings."""
    if isinstance(value, dict):
        return all(_text(key) and _text(item) for key, item in value.items())
    return _text(value) or _flag(value) or _texts(value)


def _token(value: object) -> bool:
    """An admin command's id: setup's own (a1, a2, …)."""
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _argv(value: object) -> bool:
    """An admin command's words: an absolute program first, no NUL or line break in any word, at most ADMIN_WORDS
    words and ADMIN_BYTES bytes (AdminCore.argvProblem)."""
    if not isinstance(value, list) or not value or not all(isinstance(word, str) for word in value):
        return False
    words = [str(word) for word in value]
    return (
        words[0].startswith("/")
        and len(words) <= ADMIN_WORDS
        and sum(len(word.encode()) for word in words) <= ADMIN_BYTES
        and not any(char in word for word in words for char in "\x00\n\r")
    )


def _why(value: object) -> bool:
    """The line macOS's box shows: plain text on one line, at most ADMIN_WHY characters."""
    return (
        isinstance(value, str)
        and len(value) <= ADMIN_WHY
        and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    )


def _exit_code(value: object) -> bool:
    return _count(value) and isinstance(value, int) and value <= 255


def _seconds(value: object) -> bool:
    """An admin command's time limit: whole seconds, 1 to ADMIN_SECONDS."""
    return _positive(value) and isinstance(value, int) and value <= ADMIN_SECONDS


def _kind(value: object) -> bool:
    return isinstance(value, str) and value in KINDS


def _version(value: object) -> bool:
    return _count(value) and value == PROTOCOL


def _maybe(check: Check) -> Check:
    return lambda value: value is None or check(value)


def _shape(fields: Mapping[str, Check], optional: Mapping[str, Check] | None = None) -> Check:
    """An object with exactly these fields (and maybe the optional ones), each passing its check."""
    extra = optional or {}

    def check(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        keys = set(value)
        if not set(fields) <= keys <= set(fields) | set(extra):
            return False
        return all(rule(value[key]) for key, rule in {**fields, **extra}.items() if key in value)

    return check


def _list_of(check: Check) -> Check:
    return lambda value: isinstance(value, list) and all(check(item) for item in value)


_GUIDE = _shape(
    {"needs": _text, "steps": _texts, "check": _text, "errors": _list_of(_pair), "links": _list_of(_link)},
)
_OPTION = _shape({"id": _name, "label": _text, "line": _text, "tag": _text, "on": _flag, "guide": _maybe(_GUIDE)})
_FIELD = _shape({"id": _name, "label": _text, "placeholder": _text, "max_chars": _positive})
_LAYOUT = _shape(
    {"rows": _positive, "columns": _positive, "group": _positive, "label": _text, "visible": _flag},
    {"sizes": _list_of(_positive), "joiner": _text},  # each group's size when they differ, and what joins them
)

ENGINE: dict[str, Check] = {
    "hello": _shape({"t": _text, "v": _version, "name": _text}),
    "screen": _shape(
        {
            "t": _text,
            "id": _qid,
            "kind": _kind,
            "step": _count,
            "steps": _count,
            "title": _text,
            "lead": _text,
            "help": _text,
            "ask": _text,
            "note": _text,
            "options": _list_of(_OPTION),
            "fields": _list_of(_FIELD),
            "placeholder": _text,
            "recommended": _maybe(_text),
            "layout": _maybe(_LAYOUT),
            "back": _flag,
        },
        # later: its button's label; more: the More fold's text; stop: the quiet way out's line (Stop here)
        {"kept": _value, "live": _flag, "links": _list_of(_link), "later": _text, "more": _text, "stop": _text},
    ),
    "checked": _shape({"t": _text, "id": _qid, "ok": _flag, "message": _text, "shown": _text, "live": _flag}),
    "progress": _shape({"t": _text, "line": _text}, {"again": _flag}),  # again: in the place of the line before
    "terminal": _shape({"t": _text, "why": _text}),
    "done": _shape({"t": _text, "lines": _texts}),
    "admin": _shape(  # seconds: its time limit, when setup gives one
        {"t": _text, "id": _token, "argv": _argv, "input": _maybe(_text), "open": _flag, "why": _why},
        {"seconds": _seconds},
    ),
    "admin_input": _shape({"t": _text, "id": _token, "text": _text}),
    "admin_end": _shape({"t": _text, "id": _token}),
}
WINDOW: dict[str, Check] = {
    "ready": _shape({"t": _text, "v": _version}),
    "check": _shape({"t": _text, "id": _qid, "value": _value}),
    "answer": _shape({"t": _text, "id": _qid, "value": _value}),
    "back": _shape({"t": _text}),
    "stop": _shape({"t": _text}),
    "closed": _shape({"t": _text}),
    # the window's admin runner: each line of a command's output, then its result (never the page's: Message.swift)
    "admin_line": _shape({"t": _text, "id": _token, "line": _text}),
    # unrun: the command never started (macOS's box could not ask, the wrapper failed first)
    "admin_done": _shape({"t": _text, "id": _token, "code": _exit_code, "refused": _flag}, {"unrun": _flag}),
}


def _checked(message: object, kinds: Mapping[str, Check]) -> dict[str, object]:
    if not isinstance(message, dict):
        raise ProtocolError("not an object")
    t = message.get("t")
    if not isinstance(t, str) or t not in kinds:
        raise ProtocolError(f"not a message of this side: {t!r}")
    if not kinds[t](message):
        raise ProtocolError(f"a {t} message with fields of another shape")
    return message


def encode(message: Mapping[str, object]) -> bytes:
    """One message setup sends, as its line: checked first (setup never sends what the window would refuse), `t`
    first, at most FROM_ENGINE_MAX bytes."""
    checked = _checked(dict(message) if isinstance(message, Mapping) else message, ENGINE)
    ordered = {"t": checked["t"], **{key: value for key, value in checked.items() if key != "t"}}
    line = json.dumps(ordered, ensure_ascii=False).encode()
    if len(line) > FROM_ENGINE_MAX:
        raise ProtocolError(f"a line of {len(line)} bytes, over {FROM_ENGINE_MAX}")
    return line + b"\n"


def decode(line: bytes) -> dict[str, object]:
    """One line the window sent, read as untrusted: at most FROM_WINDOW_MAX bytes of UTF-8 JSON, exactly one of the
    window's messages."""
    body = line.removesuffix(b"\n")
    if len(body) > FROM_WINDOW_MAX:
        raise ProtocolError(f"a line of {len(body)} bytes, over {FROM_WINDOW_MAX}")
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProtocolError("not one line of JSON") from None
    return _checked(message, WINDOW)


# ------------------------------------------------------------------ the page: its own files, each by its fingerprint

# tools/setup-window/Page, in order; the intro's recording is a script (CLIP, base64), as the approved preview has it:
# no audio file in the repo (scripts/privacy_scan.py refuses every one by its name)
PAGE_FILES = ("page.html", "page.css", "page.js", "intro-data.js", "intro-clip.js")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
# the page's rules, the same in the scheme handler's answers and page.html's meta tag (Page.swift, PageServer)
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
PAGE_MAX = 8 * 1024 * 1024  # bytes in one page file (PageServer.maxBytes)


def _regular(path: Path, limit: int | None = None) -> bytes:
    """A regular file's bytes at that name, never through a link; with `limit`, at most that many bytes."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as file:
        info = os.fstat(file.fileno())
        if info.st_mode & 0o170000 != 0o100000 or (limit is not None and info.st_size > limit):
            raise OSError(f"{path} is not a file setup reads")
        return file.read()


def _page(source: Path) -> tuple[dict[str, bytes], bytes]:
    """Each page file's bytes, and page.json listing each one's path, sha256 and type (always the same bytes for the
    same page)."""
    bodies = {name: _regular(source / name, PAGE_MAX) for name in PAGE_FILES}
    files = [
        {"path": name, "sha256": hashlib.sha256(body).hexdigest(), "type": TYPES[Path(name).suffix]}
        for name, body in bodies.items()
    ]
    return bodies, json.dumps({"v": 1, "files": files}, separators=(",", ":")).encode()


def page_manifest(source: Path) -> bytes:
    """page.json for the page in `source`: what the window reads, by the sha256 setup gives it."""
    return _page(source)[1]


def assemble_page(source: Path, target: Path) -> str:
    """The page as the window serves it: each of PAGE_FILES copied from `source` into a fresh `target`, with
    page.json. The fingerprint (sha256) of page.json, which setup gives the window when it opens it: the window reads
    page.json only when it is that one."""
    bodies, manifest = _page(source)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise OSError(f"{target} is not a folder")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(mode=0o700, parents=True)
    for name, body in bodies.items():
        (target / name).write_bytes(body)
    (target / "page.json").write_bytes(manifest)
    return hashlib.sha256(manifest).hexdigest()


# ------------------------------------------------------------------ the build: once, from the checkout, at the desk

PACKAGE = Path("tools/setup-window")
APP = "Estia Setup.app"
PROGRAM = "estia-setup"
BUNDLE_ID = "one.estia.setup"
BUILD_SECONDS = 900  # a first build of the window: about a minute on an M-series Mac


@dataclass(frozen=True)
class Built:
    """The window, built: its bundle, its program, its page folder and page.json's sha256 (from the checkout's own
    page: the window opens only that page)."""

    app: Path
    program: Path
    page: Path
    manifest: str


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _sources(package: Path) -> str:
    """One fingerprint of the program's sources (Package.swift and every Swift file under Sources): a change builds
    it again."""
    digest = hashlib.sha256()
    for path in [package / "Package.swift", *sorted((package / "Sources").rglob("*.swift"))]:
        digest.update(path.relative_to(package).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _command_line_tools(tools: Path) -> bool:
    """The Command Line Tools (or Xcode) are there: `xcode-select -p` names their folder. Asked first, so swift's own
    stand-in never opens the install prompt."""
    try:
        done = subprocess.run(
            [str(tools / "usr/bin/xcode-select"), "-p"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and bool(done.stdout.strip())


def _compile(argv: list[str], package: Path, program: Path) -> str:
    """swift build, then its program copied into the bundle; "" when it worked, else why not."""
    try:
        done = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=BUILD_SECONDS,
            check=False,
        )
        if done.returncode != 0:
            return prompts.SETUP_WINDOW_BUILD_FAILED
        shown = subprocess.run(
            [*argv[: argv.index("--product")], "--show-bin-path"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return prompts.SETUP_WINDOW_BUILD_FAILED
    made = Path(shown.stdout.strip()) / PROGRAM
    if shown.returncode != 0 or not made.resolve().is_relative_to(package.resolve()) or made.is_symlink():
        return prompts.SETUP_WINDOW_NO_PROGRAM
    try:
        body = _regular(made)
    except OSError:
        return prompts.SETUP_WINDOW_NO_PROGRAM
    program.parent.mkdir(parents=True, exist_ok=True)
    temporary = program.with_name(f".{PROGRAM}.new")
    temporary.write_bytes(body)
    temporary.chmod(0o755)
    os.replace(temporary, program)
    return ""


def _info() -> bytes:
    return plistlib.dumps(
        {
            "CFBundleExecutable": PROGRAM,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleName": "Estia Setup",
            "CFBundleDisplayName": "Estia Setup",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1",
            "CFBundleShortVersionString": "1",
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
        }
    )


def build(repo: Path, *, dry_run: bool, say: Callable[[str], None]) -> Built | None:
    """The Estia Setup window, built from the checkout into tools/setup-window/.build/Estia Setup.app: the program
    once (again only when its sources or the built program changed), its page every time from the checkout's own
    files, page.json with each file's sha256. The Command Line Tools are asked first (`xcode-select -p`), never their
    install prompt. None, with one line, when it cannot be built: setup goes on in Terminal. A dry run shows the build
    and runs nothing."""
    package = repo / PACKAGE
    tools = hostsec.tool_root()
    argv = [
        str(tools / "usr/bin/swift"),
        "build",
        "-c",
        "release",
        "--package-path",
        str(package),
        "--product",
        PROGRAM,
    ]
    if dry_run:
        say(prompts.SETUP_WINDOW_DRY_RUN.format(command=" ".join(argv)))
        return None
    if not _command_line_tools(tools):
        say(prompts.SETUP_WINDOW_NOT_BUILT.format(why=prompts.SETUP_WINDOW_NO_TOOLS))
        return None
    contents = package / ".build" / APP / "Contents"
    program = contents / "MacOS" / PROGRAM
    stamp_path = contents / "Resources" / "build.json"
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stamp = {}
    sources = _sources(package)
    if not isinstance(stamp, dict) or stamp.get("sources") != sources or stamp.get("program") != _sha(program):
        say(prompts.SETUP_WINDOW_BUILDING)
        why = _compile(argv, package, program)
        if why:
            say(prompts.SETUP_WINDOW_NOT_BUILT.format(why=why))
            return None
        (contents / "Info.plist").write_bytes(_info())
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.write_text(json.dumps({"sources": sources, "program": _sha(program)}) + "\n", encoding="utf-8")
    try:
        manifest = assemble_page(package / "Page", contents / "Resources" / "page")
    except OSError:
        say(prompts.SETUP_WINDOW_NOT_BUILT.format(why=prompts.SETUP_WINDOW_PAGE_UNREAD))
        return None
    return Built(contents.parent, program, contents / "Resources" / "page", manifest)


# ------------------------------------------------------------------ the window face: at the desk, Terminal over SSH

REPO = Path(__file__).resolve().parents[2]
READY_SECONDS = 20.0  # a window that has not said it is ready by then never opened: setup goes on in Terminal
# checked while typing, 150 ms after a key: a typed line whose check reads nothing outside setup (`Question.live`: never
# Ollama's key, whose check is a test search at ollama.com); a field set (the home area's one lookup at OpenStreetMap),
# a key and a pick are checked only once given
LIVE_KINDS = frozenset({"text", "number", "hidden", "pin"})
KEPT = "kept"  # the window's own screen: the answers kept from an earlier run, each with Change
STOP = "stop"  # the window's own screen when setup stops: page.js draws it with its commands to copy, until closed
# what the window gets of the environment: where it lives and its language, never the rest of yours
WINDOW_ENV = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE")
TERMINALS = {"Apple_Terminal": "com.apple.Terminal", "iTerm.app": "com.googlecode.iterm2"}  # by TERM_PROGRAM
# an AI-run's channel: the box's fixed lines, the step screens' fixed titles (the list's own come by their id), and a
# download's dots by a step's fixed name
ADMIN_LINES = frozenset(
    {prompts.ADMIN_WHY, prompts.ADMIN_WHY_OLLAMA, prompts.ADMIN_WHY_OLLAMA_HERS, prompts.ADMIN_WHY_MOVE,
     prompts.ADMIN_WHY_SECURITY, prompts.ADMIN_WHY_CALLS, prompts.ADMIN_WHY_HER}
)  # fmt: skip
SCREEN_TITLES = {
    "harden": "Security",
    "admin_refused": prompts.SETUP_WINDOW_ADMIN_REFUSED_TITLE,
    "deploy_review": prompts.SETUP_WINDOW_DEPLOY_TITLE,
    KEPT: prompts.SETUP_WINDOW_KEPT,
}
DOTS_LINE = re.compile(
    "  (?:" + "|".join(re.escape(name) for name in prompts.SETUP_STEPS.values()) + ") [●○]{5}"
    "(?: " + re.escape(prompts.SETUP_STILL_WORKING) + ")?"
)
# a question's last bracketed hint, and a colon after it: how to type it in Terminal ("(Enter keeps hearth)", "(hidden:
# nothing shows while you type)", an example): the window has its own buttons, fields and examples
_TYPING_HINT = re.compile(r"\s*\([^()]*\)[\s:.]*$")


def _answer(tool: str, *args: str) -> str:
    """A fixed command's one line (under the tools' root); "" when it cannot be read."""
    try:
        done = subprocess.run(
            [str(hostsec.tool_root() / tool), *args], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def at_the_desk() -> bool:
    """The person is at this Mac's own screen: no remote login (SSH_CONNECTION, SSH_CLIENT, SSH_TTY), this session is
    the signed-in desktop (`launchctl managername` says Aqua) and the Mac's screen is yours (`stat -f %Su
    /dev/console`). Over SSH the window would open on the Mac's own screen, not the laptop's: never."""
    if any(os.environ.get(key) for key in SSH_KEYS):
        return False
    if _answer("bin/launchctl", "managername") != "Aqua":
        return False
    return _answer("usr/bin/stat", "-f", "%Su", "/dev/console") == pwd.getpwuid(os.getuid()).pw_name


class Window:
    """The running window: its stdin takes setup's lines, its stdout gives the window's, one checked line at a time.
    Its own session, so a Ctrl-C in Terminal stops setup, and the window then says that setup ended."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self.buffer = b""
        self.gone = False  # nothing more goes to it (its stdin failed, or setup is done with it)
        self.ended = False  # nothing more comes from it: what it said before is still read (review FRIEND-4)

    @classmethod
    def open(cls, built: Built) -> Window | None:
        argv = [str(built.program), "--page", str(built.page), "--manifest", built.manifest]
        terminal = TERMINALS.get(os.environ.get("TERM_PROGRAM", ""))
        if terminal is not None:
            argv += ["--terminal-app", terminal]
        env = {key: os.environ[key] for key in WINDOW_ENV if key in os.environ}
        try:
            process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                start_new_session=True,
            )  # fmt: skip
        except OSError:
            return None
        return cls(process)

    def send(self, message: Mapping[str, object]) -> bool:
        """One line to the window; False once it is gone."""
        if self.gone or self.process.stdin is None:
            return False
        try:
            self.process.stdin.write(encode(message))
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.gone = True
        return not self.gone

    def receive(self, seconds: float | None = None) -> dict[str, object] | None:
        """The window's next message, checked; None when it is gone (its end, a line it should never send), or when
        `seconds` passed first. A window whose stdin failed is still read to its end: its last words (Stop here,
        then Close) come in order."""
        deadline = None if seconds is None else time.monotonic() + seconds
        out = self.process.stdout
        while not self.ended and out is not None:
            end = self.buffer.find(b"\n")
            if end >= 0:
                line, self.buffer = self.buffer[:end], self.buffer[end + 1 :]
                try:
                    return decode(line)
                except ProtocolError:
                    self.close()
                    return None
            if len(self.buffer) > FROM_WINDOW_MAX:
                self.close()
                return None
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([out], [], [], wait)
            if not ready:
                return None
            chunk = os.read(out.fileno(), 65536)
            if not chunk:
                self.gone = self.ended = True
                return None
            self.buffer += chunk
        return None

    def close(self) -> None:
        """setup is done with it: its stdin ends (the page says setup ended), and a window that broke is ended."""
        self.gone = self.ended = True
        if self.process.stdin is not None:
            with contextlib.suppress(OSError):
                self.process.stdin.close()


def _last_state(raw: bytes) -> str:
    """A line of a command's output as text: a line rewritten in place (a meter's \\r) as its last state."""
    text = raw.decode("utf-8", errors="replace").rstrip("\r")
    return text.rsplit("\r", 1)[-1]


# the step's own command in Terminal, by the box's line: a run of it there does what the window's step does. Calls and
# her own Ollama are setup's own run (review W04): install-voice-daemon.sh builds no call helper and writes no [calls],
# and install-ollama.sh picks today's layout while her account is missing; setup skips what is done already
STEP_SCRIPTS = {
    prompts.ADMIN_WHY_MOVE: "migrate-to-own-user.sh",
    prompts.ADMIN_WHY_SECURITY: "harden.sh",
    prompts.ADMIN_WHY_OLLAMA: "install-ollama.sh",
    prompts.ADMIN_WHY_OLLAMA_HERS: "setup.sh",
    prompts.ADMIN_WHY_CALLS: "setup.sh",
}


# the steps whose Not now at the refused screen is a stop (setup cannot go on without them)
STOPS_SETUP = frozenset({prompts.ADMIN_WHY_OLLAMA, prompts.ADMIN_WHY_OLLAMA_HERS, prompts.ADMIN_WHY_MOVE})


def setup_again_command(repo: Path = REPO) -> str:
    """scripts/setup.sh as a new Terminal window runs it (review W12: it opens in the home folder, and an AI-run has
    no Terminal window in the clone): into the clone first, by its real path with the home as ~, then setup."""
    home = Path.home()
    where = (
        "~" + ("" if repo == home else "/" + shlex.quote(repo.relative_to(home).as_posix()))
        if repo.is_relative_to(home)
        else shlex.quote(str(repo))
    )
    return f"cd {where} && scripts/setup.sh"


def later_command(argv: Sequence[str], why: str, *, given: bool) -> str:
    """What the refused screen names for Terminal later: the step's own script (the move's, the security step's,
    Ollama's; setup itself for calls and her own Ollama, in W12's form), or the command itself when it is one you
    would type (sudo then a program, nothing on its input); "" for a `sudo -n` or `-u` form, which fails without a
    sign-in, or one that waits for input (review ADM-11: the move's own argv skipped its script's guard)."""
    if STEP_SCRIPTS.get(why) == "setup.sh":
        return setup_again_command()
    if why in STEP_SCRIPTS:
        return str(REPO / "scripts" / STEP_SCRIPTS[why])
    typed = len(argv) > 1 and argv[0] == SUDO and argv[1].startswith("/") and not given
    return " ".join(argv) if typed else ""


def sentence_line(text: str) -> str:
    """The box's line: plain text on one line, at most ADMIN_WHY characters."""
    flat = " ".join(plain_text(text).split())
    return flat if len(flat) <= ADMIN_WHY else flat[: ADMIN_WHY - 1] + "…"


@contextlib.contextmanager
def _ctrl_c_held() -> Iterator[list[int]]:
    """setup.ctrl_c_held: a Ctrl-C while a step's command runs is noted, never raised mid-command (the command gets it,
    or, run as root, never does), then setup stops."""
    from local_assistant.setup import ctrl_c_held  # setup imports this module

    with ctrl_c_held() as pressed:
        yield pressed


class _Nothing:
    """No answers: a question's screen as it is asked (the window shows kept answers on their own screen)."""

    def get(self, qid: str) -> Value | None:
        return None

    def kept(self, qid: str) -> bool:
        return False


class WindowFace:
    """The Estia Setup window: each question as one screen, its answer as the window gives it; setup's checks run
    here, in setup (while typing too, for a typed line), never in the window. Every line setup says shows in Terminal
    too and in the window as progress. Every step runs in the window (owner, 29 Oct: "can we have Terminal in the
    window?"): a command's output streams into its log, a step's own question is a screen, and admin rights come from
    macOS's own box through the window's admin runner, never a password typed in the window. A closed window hands the
    same question, and the rest of the step, to Terminal (`fallback`: gum's menus or plain lines), where sudo asks as it
    always did. A secret goes from the window to setup, and from setup only into an admin command's stdin: never sent
    back to the page, printed or kept.

    In an AI-run (`wizard.ai`, setup_ai) the window is the only face: nothing is printed (the AI reads only the run's
    channel: each screen's fixed title, where macOS asks, a download's dots), and where Terminal would take over setup
    stops instead, every answer kept: the window closed (as Stop here: a command is interrupted, a held section's admin
    commands then fail and it ends first), an answer that does not fit its screen, macOS's box that could not ask, a
    sudo form the window does not run."""

    name = "window"

    @property
    def asking(self) -> str:
        """ "window", or the Terminal face's name once the window closed: that face then asks (gum's key card)."""
        return self.terminal_face.asking if self.terminal_face is not None else self.name

    def __init__(self, window: Window, wizard: Wizard, fallback: Callable[[], Face]) -> None:
        self.window = window
        self.wizard = wizard
        self.fallback = fallback
        self.terminal_face: Face | None = None
        self.asked: list[str] = []  # the plan's questions shown, in turn (the window's "3 of 9")
        self.admins = 0  # admin commands sent: each one's id (a1, a2, …)
        self._holding = 0  # inside `stops_held`: a stop waits for the section's end
        self._stop_held = False  # a stop (or a Ctrl-C) came while held: setup stops at the section's end
        self._held_line = ""  # what a stop says while held
        self._box_step: str | None = None  # the step whose first admin command said where macOS asks (FRIEND-11)
        self.held: list[dict[str, object]] = []  # the admin runner's messages read while a screen waited
        self.backlog: list[dict[str, object]] = []  # the page's messages `heard` read first: read again in turn
        # the question whose screen the window shows now ("" once it shows its log): an answer is waited for only on
        # its own screen
        self.showing = ""
        self.secret = ""  # the secret just typed in the window, while setup checks it: never sent back
        self._back = False  # the screen has Back (a question of the plan with one before it: the Wizard sets it)
        self._previous: Value | None = None  # the answer before, on a screen gone back to: filled in
        self._no_back = ""  # after the plan: what Terminal says of a b typed there (the window has no b)
        self.ai: Run | None = getattr(wizard, "ai", None)  # an AI-run: no Terminal behind the window
        self.closed = False  # an AI-run's window closed (or ended): setup stops as at Stop here
        self.unstarted = ""  # her start, refused because the window closed in harden's held section (setup_admin)
        self._held_stop = ""  # an AI-run's stop that came in a held section: raised at its end
        self._held_said = ""  # and the AI's line for it, when the stop holds an answer
        self._unfit = False  # the window gave an answer that does not fit its screen
        self._seen: set[str] = set()  # the screens an AI-run named on its channel ("asked again")

    @property
    def back(self) -> bool:
        return self._back

    @back.setter
    def back(self, value: bool) -> None:
        """Set by the Wizard on this face; once the window closed, Terminal's face asks, so it gets it too."""
        self._back = value
        if self.terminal_face is not None:
            self.terminal_face.back = value

    @property
    def previous(self) -> Value | None:
        return self._previous

    @previous.setter
    def previous(self, value: Value | None) -> None:
        self._previous = value
        if self.terminal_face is not None:
            self.terminal_face.previous = value

    @property
    def no_back(self) -> str:
        return self._no_back

    @no_back.setter
    def no_back(self, value: str) -> None:
        self._no_back = value
        if self.terminal_face is not None:
            self.terminal_face.no_back = value

    # ---------------------------------------------------------------- the window, or Terminal once it closed

    def _terminal(self, why: str = prompts.SETUP_WINDOW_HANDED, said: str = prompts.SETUP_WINDOW_CLOSED) -> Face:
        """Terminal from here on: the window closed (or broke, or gave an answer that does not fit its screen, or its
        box could not ask): `why` is the window's last screen, `said` Terminal's line. An AI-run has no Terminal: it
        stops (`_cannot_go_on`)."""
        if self.ai is not None:
            if self._unfit:
                self._cannot_go_on(prompts.SETUP_AI_UNFIT)
            self._cannot_go_on("", closed=True)
            raise KeyboardInterrupt  # in a held section: a question there is never answered by its end of input
        if self.terminal_face is None:
            self._hand_over(why, said)
            self.terminal_face = self.fallback()
            self.terminal_face.back, self.terminal_face.previous = self._back, self._previous  # this question's
            self.terminal_face.no_back = self._no_back
        return self.terminal_face

    def _cannot_go_on(self, text: str, *, closed: bool = False, said: str = "") -> None:
        """An AI-run where the window cannot go on: the window closed (`closed`: as Stop here), or `text`, a stop in
        setup's fixed words (the window's stop screen, and the AI's end line in the same words, or `said` when `text`
        holds an answer). Inside a held section nothing raises: its remaining admin commands fail, and setup stops at
        its end."""
        assert self.ai is not None
        if closed:
            self.closed = True
        if self._holding:
            self._stop_held = True
            if not self._held_stop:
                self._held_stop, self._held_said = text, said
            return
        if closed:
            raise KeyboardInterrupt
        self.ai.stop(text, said)

    def _hand_over(self, why: str = prompts.SETUP_WINDOW_HANDED, said: str = prompts.SETUP_WINDOW_CLOSED) -> None:
        """setup is done with the window: a window still open shows that setup goes on in Terminal at the same
        question, and that this was its last word (never "Setup ended in Terminal." while setup goes on); then its
        stdin ends. A window that closed gets nothing."""
        self.window.send({"t": "terminal", "why": why})
        self.window.send({"t": "done", "lines": []})
        self.window.close()
        if self.ai is None and said:
            print(said, flush=True)

    def say(self, text: str) -> None:
        """A line in Terminal and in the window's log, both without its bold marks: the log is plain text. An AI-run
        prints nothing (its output reaches no one: the window's log alone has it)."""
        if self.ai is None:
            print(plain_text(text), flush=True)
        if self.terminal_face is None:
            self.window.send({"t": "progress", "line": plain_text(text)})

    def command(self, text: str) -> None:
        """A command whose input and output are setup's (it asks nothing): a line in Terminal and in the window's log,
        the window's screen kept (the one-time word stays while setup looks for it)."""
        if self.terminal_face is not None:
            self.terminal_face.command(text)
            return
        if self.ai is None:
            print(text, flush=True)
        self.window.send({"t": "progress", "line": text})

    def line(self, prompt: str) -> str | None:
        """A step's own prompt (one not on the list): a line typed in the window, on a screen of its own, the prompt
        without Terminal's typing hints."""
        if self.terminal_face is not None:
            return self.terminal_face.line(prompt)
        step = prompts.SETUP_STEPS.get(getattr(self.wizard, "step_now", ""), "")
        ask = _TYPING_HINT.sub("", prompt).strip()
        q = Question("line", "text", step[:1].upper() + step[1:] if step else ask, ask=ask, keep="step", plan=False)
        return self.text(q, None, "")

    # ---------------------------------------------------------------- a step's commands, run in the window

    @contextlib.contextmanager
    def stops_held(self, line: str) -> Iterator[None]:
        """A section that must run to its end (harden's: her stop, her PIN, her start): Stop here or a Ctrl-C during
        one of its commands waits for the section's end, `line` said once, then setup stops (review FRIEND-5: the
        stop raised right after her bootout, and she stayed stopped)."""
        self._holding += 1
        if self._holding == 1:
            self._held_line = line
        try:
            yield
        finally:
            self._holding -= 1
        if not self._holding and self._stop_held:
            self._stop_held = False
            text, said, self._held_stop, self._held_said = self._held_stop, self._held_said, "", ""
            if text and self.ai is not None and not self.closed:
                self.ai.stop(text, said)
            raise KeyboardInterrupt

    @property
    def holding(self) -> bool:
        """Inside `stops_held` now (harden's: her stop, her PIN, her start)."""
        return self._holding > 0

    def _stopping(self) -> str:
        """What a stop says now: the held section's line, or that the command finishes first."""
        return self._held_line if self._holding else prompts.SETUP_WINDOW_FINISHES_FIRST

    def _interrupted(self) -> None:
        """A stop or a Ctrl-C came while a command ran: setup stops now, or at the end of a held section."""
        if self._holding:
            self._stop_held = True
            return
        raise KeyboardInterrupt

    def heard(self) -> None:
        """What the window said while setup worked without reading it (a download, a check, setup's own waits): its
        Stop here is honoured now, before anything more runs or shows, as a Ctrl-C (a held section ends first); a
        window that ended hands the rest to Terminal (a stop said before it ended is still read first); the admin
        runner's lines wait for their command; anything else (an answer to a screen gone) is dropped (review
        FRIEND-4: Stop here during a download was read only at the next screen, after macOS's box)."""
        while self.terminal_face is None and not self.backlog:
            message = self.window.receive(0)
            if message is None:
                if self.window.ended:
                    self._gone()
                return
            t = str(message["t"])
            if t == "stop":
                self._interrupted()
            elif t == "closed":
                self._gone()
                return
            elif t.startswith("admin_"):
                self.held.append(message)
            else:  # an answer, a check, back: read again in turn, where a screen waits for it
                self.backlog.append(message)

    def _gone(self) -> None:
        """The window closed or ended: Terminal goes on, or an AI-run stops (in a held section: at its end)."""
        if self.ai is not None:
            self._cannot_go_on("", closed=True)
            return
        self._terminal()

    def _next(self, seconds: float | None = None) -> dict[str, object] | None:
        """The window's next message: one `heard` read first, else the window's own."""
        return self.backlog.pop(0) if self.backlog else self.window.receive(seconds)

    def run(
        self,
        argv: list[str],
        *,
        input: str | None,
        capture: bool,
        cwd: Path | None,
        why: str,
        answer: Callable[[str, list[str]], str | None] | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        """A step's command, in the window: one with admin rights (today's sudo form) through the window's admin
        runner, macOS's own box asking; one of yours streamed into the window's log, its stdin never the terminal.
        None: setup runs it as it always did (one whose output setup reads, or the window closed: Terminal, where sudo
        asks). `answer`: a question it asks on a marked line (ASK_MARK), answered on a screen, its stdin open."""
        if self.terminal_face is not None:
            return None
        if argv and argv[0] == SUDO:
            ran = self.admin(argv, input=input, capture=capture, why=why, answer=answer)
            if ran is None:
                return None
            refused = setup_admin.REFUSED if ran.refused else None  # Terminal's runs never say it
            return subprocess.CompletedProcess(list(argv), ran.code, ran.output if capture else None, refused)
        if capture:
            return None
        return self._stream(argv, input=input, cwd=cwd, answer=answer)

    def admin(
        self,
        argv: Sequence[str],
        *,
        input: str | None = None,
        capture: bool = False,
        why: str = "",
        answer: Callable[[str, list[str]], str | None] | None = None,
        seconds: float | None = None,
        ends_on_stop: bool = False,
    ) -> Ran | None:
        """A command in today's sudo form, run as root by the window (`setup_admin.as_root`): macOS's own box asks
        (`why` is its line), each line of its output in Terminal and the window's log (kept, not said, with
        `capture`), its input on its stdin only. Refused, the window says so with Try again and Not now (Not now:
        `Ran(1, …, refused=True)`, the step's own path); after a stop held in `stops_held`, it is Not now at once. With `answer`, a question the command asks on a line of its
        own (ASK_MARK) is answered by it (the lines before it given too: what it asks about), its stdin open until
        the end; `seconds`, its time limit (the window ends it then, as Host.run's timeout does: review ADM-3). None
        when the window closed before it ran, its box could not ask (setup goes on in Terminal: review ADM-4), or for
        a form the window does not run (`as_root` refuses it): Terminal runs it with sudo, never a traceback (ADM-1). Once the window closed, every form is
        Terminal's, `sudo -n -v` and `sudo -k` too: they ask nothing of the window, but Terminal's sudo answers them
        (review ADM-2). `ends_on_stop`: a command that ends soon after its input ends (the wait for her voice, review
        W19): its stdin open, nothing written there, and Stop here or a Ctrl-C ends it at once, said at once, then
        setup stops after its end."""
        if self.terminal_face is not None:
            return None
        if self.ai is not None and (self.closed or self._held_stop):  # in a held section, once it cannot go on
            return Ran(1, "")
        try:
            root = setup_admin.as_root(argv, pwd.getpwuid(os.getuid()).pw_name)
        except ValueError:
            if self.ai is not None:  # never sudo run by setup itself
                word = argv[1] if len(argv) > 1 else ""
                program = Path(word).name if word.startswith("/") else word
                self._cannot_go_on(prompts.SETUP_AI_FORM.format(program=program))
                return Ran(1, "")
            return None
        if root is None:
            return Ran(0, "")
        while True:
            ran = self._admin(
                root, input=input, quiet=capture, why=why, answer=answer, seconds=seconds, ends_on_stop=ends_on_stop
            )
            if ran is None or not ran.refused:
                return ran
            # a stop held for the section's end: Not now, never a screen the page drops after "Stopped" (review W21),
            # which setup then waited on (fix review, setup-code-01)
            if self._stop_held or not self._try_again(argv, why=why, given=input is not None or answer is not None):
                return Ran(1, prompts.SETUP_WINDOW_ADMIN_REFUSED_TITLE, refused=True)

    def _admin(
        self,
        root: list[str],
        *,
        input: str | None,
        quiet: bool,
        why: str,
        answer: Callable[[str, list[str]], str | None] | None,
        seconds: float | None = None,
        ends_on_stop: bool = False,
    ) -> Ran | None:
        self.heard()  # a Stop here while setup worked: never a box after it (review FRIEND-4)
        if self.terminal_face is not None:
            return None
        if self.ai is not None and self.closed:  # in a held section, closed meanwhile: a failed run
            return Ran(1, "")
        self.admins += 1
        aid = f"a{self.admins}"
        step = str(getattr(self.wizard, "step_now", ""))
        why_line = sentence_line(why or prompts.ADMIN_WHY)
        if step != self._box_step:  # the window's own line, once a step: macOS's box asks once in 5 minutes
            self._box_step = step
            self.window.send({"t": "progress", "line": prompts.SETUP_WINDOW_ADMIN_BOX})
            if self.ai is not None:  # the step's fixed line only
                self.ai.say(
                    prompts.SETUP_AI_ADMIN.format(why=why_line if why_line in ADMIN_LINES else prompts.ADMIN_WHY)
                )
        request: dict[str, object] = {"t": "admin", "id": aid, "argv": root, "input": input,
                                      "open": answer is not None or ends_on_stop, "why": why_line}  # fmt: skip
        if seconds is None and input is not None and answer is None:  # never one whose question is a screen
            seconds = PIPED_SECONDS
        if seconds is not None:
            request["seconds"] = min(ADMIN_SECONDS, max(1, math.ceil(seconds)))
        if not self.window.send(request):
            if self.ai is not None:
                self._cannot_go_on("", closed=True)
                return Ran(1, "")
            self._terminal()
            return None
        lines: list[str] = []  # as said and shown: cleaned (setup_admin.shown)
        raw: list[str] = []  # as the command printed them: what a caller reads back, as Host.run gives it (ADM-6)
        stopped = False
        ended = False  # `ends_on_stop`: its input ended
        with _ctrl_c_held() as pressed:
            while True:
                if ends_on_stop and (stopped or pressed) and not ended:  # its end now, never after its last look
                    if not stopped and not self._stop_held:  # a Ctrl-C: said as a stop is
                        self.say(self._stopping())
                    stopped = ended = True
                    self.window.send({"t": "admin_end", "id": aid})
                message = self.held.pop(0) if self.held else self._next(STOP_LOOK_SECONDS if ends_on_stop else None)
                if message is None and ends_on_stop and not self.window.ended:
                    continue  # nothing yet: a Ctrl-C noted meanwhile is looked for
                if message is None or message["t"] == "closed":  # it went on as root, its end unread
                    if self.ai is not None:
                        self.closed = True
                    else:
                        self._terminal(said="")  # setup stops on the next line: never "goes on here" (round 2)
                    raise SystemExit(prompts.SETUP_ADMIN_UNKNOWN)
                if message["t"] == "stop":
                    if not stopped and not self._stop_held:
                        self.say(self._stopping())
                    stopped = True
                elif message["t"] == "admin_line" and message["id"] == aid:
                    line = str(message["line"])
                    if answer is not None and line.startswith(ASK_MARK):
                        given: str | None = None
                        if not stopped:  # Stop here before it asked: no screen, its input ends
                            try:
                                given = answer(setup_admin.shown(line[len(ASK_MARK) :]), list(lines))
                            except KeyboardInterrupt:  # Stop here on its screen: its input ends, it ends first
                                if not self._stop_held:
                                    self.say(self._stopping())
                                stopped = True
                        self.window.send(
                            {"t": "admin_input", "id": aid, "text": given}
                            if given is not None
                            else {"t": "admin_end", "id": aid}
                        )
                        continue
                    raw.append(line)
                    lines.append(setup_admin.shown(line))
                    if not quiet:
                        self.say(lines[-1])
                elif message["t"] == "admin_done" and message["id"] == aid:
                    break
        if answer is not None or (ends_on_stop and not ended):
            self.window.send({"t": "admin_end", "id": aid})  # never read again: harmless after its end
        code = message.get("code")
        if stopped or pressed:
            self._interrupted()
        if message.get("unrun") is True:  # macOS's box could not ask: nothing ran, and Terminal's sudo will
            if self.ai is not None:  # no Terminal: setup stops, naming what Terminal would run
                command = later_command(root if root[:1] == [SUDO] else [SUDO, *root], why, given=input is not None)
                said = "" if own_words(command) else prompts.SETUP_AI_NO_BOX_WINDOW  # an answer: the window's alone
                self._cannot_go_on(prompts.SETUP_AI_NO_BOX.format(command=command or "scripts/setup.sh"), said=said)
                return Ran(1, "")
            self._terminal(prompts.SETUP_WINDOW_NO_BOX, prompts.SETUP_WINDOW_NO_BOX_TERMINAL)
            return None
        return Ran(code if isinstance(code, int) else 1, "\n".join(raw), message.get("refused") is True)

    def admin_ended(self) -> bool:
        """The admin command running now has ended already: its result came while a screen waited for its question's
        answer (estia-deploy took no answer by its time limit as a no), so an answer now reaches nothing."""
        aid = f"a{self.admins}"
        return any(message["t"] == "admin_done" and message["id"] == aid for message in self.held)

    def _try_again(self, argv: Sequence[str], *, why: str, given: bool) -> bool:
        """Admin rights not given: what they were for, that nothing ran, Try again (recommended) or Not now, and for
        Terminal later a command that runs there as typed, with Copy (`later_command`)."""
        command = later_command(argv, why, given=given)
        words = (
            prompts.SETUP_WINDOW_ADMIN_LATER_SETUP
            if STEP_SCRIPTS.get(why) == "setup.sh"
            else prompts.SETUP_WINDOW_ADMIN_LATER
        )
        later = words.format(command=command) if command else ""
        q = Question(
            "admin_refused",
            "yes_no",
            prompts.SETUP_WINDOW_ADMIN_REFUSED_TITLE,
            lead=prompts.SETUP_WINDOW_ADMIN_REFUSED.format(
                why=sentence_line(why or prompts.ADMIN_WHY),
                nothing_ran=prompts.SETUP_WINDOW_ADMIN_NOT_NOW_STOPS
                if why in STOPS_SETUP
                else prompts.SETUP_WINDOW_ADMIN_NOTHING_RAN,
                later=later,
            ),
            options=(
                Option("yes", prompts.SETUP_WINDOW_TRY_AGAIN, tag=RECOMMENDED),
                Option("no", prompts.SETUP_WINDOW_NOT_NOW),
            ),
            recommended="yes",
            keep="step",
            plan=False,
        )
        self.show(q, None)
        again = self.confirm(q, True) is True
        self.checked(q, Checked(again))  # its screen leaves for the step's log (review W03)
        return again

    def _stream(
        self,
        argv: list[str],
        *,
        input: str | None,
        cwd: Path | None,
        answer: Callable[[str, list[str]], str | None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """One of your commands, its output in Terminal and the window's log line by line (a line rewritten in place,
        a meter's, drawn in the place of the one before, at most every half second); its stdin the given input, or
        none (never the terminal: a command that would ask there gets the end of its input). With `answer`, a question
        it asks on a marked line (ASK_MARK) is answered by it (the lines before it given too), the answer on its stdin,
        which stays open until the end (None ends it). Stop here interrupts it as Ctrl-C would, and setup stops: it
        runs in a session and process group of its own, and SIGINT reaches the whole group, as Ctrl-C reaches
        Terminal's (a script's own programs too: review FRIEND-9), and so does a Ctrl-C in Terminal, which setup notes
        and passes on. Without Terminal as its terminal, a program that would ask there (sudo's password) fails at
        once, never stopped in the background waiting for it; a window that closes meanwhile hands the rest to
        Terminal."""
        self.heard()  # a Stop here while setup worked: nothing starts after it
        if self.ai is not None and self.closed:  # in a held section, closed meanwhile: nothing starts
            return subprocess.CompletedProcess(argv, 1, None, None)
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if input is not None or answer is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
        )
        out, given = process.stdout, process.stdin
        assert out is not None

        def interrupt() -> None:
            """SIGINT to the command's whole group, as a Ctrl-C in Terminal."""
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGINT)

        def write(text: str | None) -> None:
            """Into its stdin; None (or a stdin it closed) ends it."""
            nonlocal given
            if given is None:
                return
            try:
                if text is not None:
                    given.write(text.encode())
                    given.flush()
                    return
            except OSError:
                pass
            with contextlib.suppress(OSError):
                given.close()
            given = None

        if input is not None:
            write(input)
        if answer is None:
            write(None)
        try:
            code, pressed, stopped = self._streamed(process, out, write, interrupt, answer)
        except BaseException:  # setup leaves (Terminal closed, an error): its command never outlives it (round 2)
            interrupt()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        if stopped or pressed:
            self._interrupted()
        return subprocess.CompletedProcess(argv, code, None, None)

    def _streamed(
        self,
        process: subprocess.Popen[bytes],
        out: IO[bytes],
        write: Callable[[str | None], None],
        interrupt: Callable[[], None],
        answer: Callable[[str, list[str]], str | None] | None,
    ) -> tuple[int, list[int], bool]:
        """`_stream`'s loop: its lines into the log until it ends; (its exit code, a Ctrl-C noted, a stop sent)."""
        said: list[str] = []
        pending = b""
        drawn = 0.0
        stopped = False
        with _ctrl_c_held() as pressed:
            while True:
                ready, _, _ = select.select([out], [], [], 0.2)
                chunk = os.read(out.fileno(), 65536) if ready else b""
                if ready and not chunk:
                    break
                pending += chunk
                *whole, pending = pending.split(b"\n")
                for raw in whole:
                    line = _last_state(raw)
                    if answer is not None and line.startswith(ASK_MARK) and not stopped:
                        try:
                            write(answer(setup_admin.shown(line[len(ASK_MARK) :]), list(said)))
                        except KeyboardInterrupt:  # Stop here on its screen: it is interrupted, setup stops
                            stopped = True
                            write(None)
                            interrupt()
                        continue
                    said.append(setup_admin.shown(line))
                    self.say(said[-1])
                if b"\r" in pending and self.terminal_face is None and time.monotonic() - drawn >= 0.5:
                    drawn = time.monotonic()
                    self.window.send({"t": "progress", "line": setup_admin.shown(_last_state(pending)), "again": True})
                if pressed and not stopped:  # a Ctrl-C in Terminal: its group is not Terminal's, so it is passed on
                    stopped = True
                    interrupt()
                if self.terminal_face is None and not stopped:
                    message = self._next(0)
                    if message is not None and message["t"] == "stop":
                        stopped = True
                        interrupt()
                    elif (message is not None and message["t"] == "closed") or self.window.gone:
                        if self.ai is not None:  # an AI-run: a close is Stop here (no Terminal to go on in)
                            self.closed = stopped = True
                            interrupt()
                        else:
                            self._terminal()
                    elif message is not None and str(message["t"]).startswith("admin_"):
                        self.held.append(message)
            if pending:
                self.say(setup_admin.shown(_last_state(pending)))
            write(None)
            code = process.wait()
        return code, pressed, stopped

    def done(self, lines: list[str]) -> None:
        """The end: the window shows the closing list."""
        if self.terminal_face is None:
            self.window.send({"t": "done", "lines": lines})

    def stopped(self, text: str) -> None:
        """Setup stops: in Terminal with space around each part, and in the window as its own screen (owner's laptop,
        29 Oct: the window only said "Setup ended in Terminal.", and the stop was one long line behind it): what was
        found as its title, why it matters, the fix, and how to go on with the command to copy; the step it stopped at
        stays ("1 of 9"), and the screen stays until it is closed."""
        if self.terminal_face is not None:
            self.terminal_face.stopped(text)
            return
        if self.ai is None:
            print(f"\n{stop_text(text)}", flush=True)
        stop = stop_parts(text)
        steps = list(getattr(self.wizard, "steps", []))
        key = getattr(self.wizard, "step_now", "")
        step, count = (steps.index(key) + 1, len(steps)) if key in steps else (0, 0)
        fix = stop.fix if stop.fix[-1:] in ".!?" else f"{stop.fix}."
        again = (
            prompts.SETUP_WINDOW_STOP_AGAIN
            if self.ai is None
            else prompts.SETUP_WINDOW_STOP_AGAIN_AI.format(command=setup_again_command())
        )
        parts = [sentence(stop.why), f"Fix: {fix}" if stop.fix else "", again]
        self.showing = ""
        title = sentence(f"{stop.area}: {stop.what}" if stop.area else stop.what)  # its area kept (round 2)
        self.window.send(
            {"t": "screen", "id": STOP, "kind": "info", "step": step, "steps": count, "title": title,
             "lead": "\n\n".join(part for part in parts if part), "help": "", "ask": "", "note": "", "options": [],
             "fields": [], "placeholder": "", "recommended": None, "layout": None, "back": False}
        )  # fmt: skip

    def progress(self, text: str) -> None:
        """A download's dots: a line in Terminal for each new one, and the window's log draws it in the place of the
        one before (page.js, DOTS_LINE)."""
        if self.terminal_face is None:
            self.heard()  # setup's own download: Stop here ends it at once
        if self.terminal_face is not None:
            self.terminal_face.progress(text)
            return
        if self.ai is None:
            print(text, flush=True)
        elif DOTS_LINE.fullmatch(text):  # an AI-run: a download's dots, by its fixed step name only
            self.ai.say(text)
        self.window.send({"t": "progress", "line": text})

    def progress_done(self) -> None:
        if self.terminal_face is not None:
            self.terminal_face.progress_done()

    # ---------------------------------------------------------------- a question, as one screen

    def _steps(self, q: Question) -> tuple[int, int]:
        """(this question's place, how many) among the plan's questions asked on this Mac; 0, 0 at a step's own. One
        shown before that these answers no longer ask (Back, then another route) no longer counts."""
        if not q.plan or not getattr(self.wizard, "planning", False):
            return 0, 0
        known, mac = self.wizard.known, self.wizard.mac
        listed = {item.id: item for item in QUESTIONS}
        self.asked = [qid for qid in self.asked if qid == q.id or (qid in listed and listed[qid].shows(known, mac))]
        if q.id not in self.asked:
            self.asked.append(q.id)
        ids = [item.id for item in QUESTIONS]
        later = [
            item.id
            for item in QUESTIONS[ids.index(q.id) + 1 :]
            if item.plan and item.id not in self.asked and not known.kept(item.id) and item.shows(known, mac)
        ]
        return self.asked.index(q.id) + 1, len(self.asked) + len(later)

    def screen(self, q: Question) -> dict[str, object]:
        """The screen message for this question: its words and options (the recommended one with its small line),
        the line without its Enter hint, why there is no going back where it matters, never an answer."""
        step, steps = self._steps(q)
        screen = q.screen(_Nothing(), self.wizard.mac, step=step, steps=steps, back=self.back)
        if self.previous is not None and not q.is_secret:  # gone back to: its answer before, filled in
            screen["kept"] = json_value(self.previous)
        if q.window_lead:  # the window's own words: Terminal's keys stay in Terminal
            screen["lead"] = q.window_lead
        ask = q.window_ask or _TYPING_HINT.sub("", q.ask).strip()
        screen["ask"] = "" if q.kind == "wait" else ask  # a wait needs no Enter
        screen["note"] = q.note
        screen["live"] = self._live(q)  # the page posts checks while typing only for these
        return screen

    @staticmethod
    def _live(q: Question) -> bool:
        return q.kind in LIVE_KINDS and q.live and q.check is not None

    def show(self, q: Question, kept: Value | None) -> None:
        if self.terminal_face is None:
            self.heard()  # a Stop here said meanwhile: setup stops before a screen covers "Stopped" (review W21)
        if self.terminal_face is not None:
            self.terminal_face.show(q, kept)
        elif kept is not None:
            self.say(kept_line(q, kept))
        elif self.window.send(self.screen(q)):
            self.showing = q.id
            self._named(q.id)
        else:
            self._terminal().show(q, kept)

    def _named(self, qid: str) -> None:
        """An AI-run's line for a screen: the question list's own title by its id (never filled in: "Calls with
        her"), or a step screen's fixed one; never a lead, a value or an option."""
        if self.ai is None:
            return
        title = SCREEN_TITLES.get(qid, "")
        if qid == "line":  # a step's own prompt: the step's name
            step = prompts.SETUP_STEPS.get(str(getattr(self.wizard, "step_now", "")), "")
            title = step[:1].upper() + step[1:]
        elif not title:
            with contextlib.suppress(KeyError):
                title = question(qid).title
        title = title.replace("{name}", "her")
        if not title or "{" in title:
            title = prompts.SETUP_AI_SCREEN_OTHER
        again = qid in self._seen
        self._seen.add(qid)
        self.ai.say((prompts.SETUP_AI_SCREEN_AGAIN if again else prompts.SETUP_AI_SCREEN).format(title=title, id=qid))

    def _given(self, q: Question, fits: Callable[[object], bool]) -> tuple[bool, object]:
        """(True, the answer the window gave for this question), each check while typing answered on the way;
        (False, None) once the window is gone. A stop ends setup as Ctrl-C does. An answer to this screen that does not
        fit it (a page out of step with setup) ends the window, as a line it should never send does: the page then
        waits for setup's word with its buttons off, and setup would wait for another answer."""
        while True:
            message = self._next()
            if message is None or message["t"] == "closed":
                return False, None
            t = message["t"]
            if str(t).startswith("admin_"):  # the runner's: read by the command that waits for them
                self.held.append(message)
                continue
            if t == "stop":
                raise KeyboardInterrupt
            if t == "back" and self.back:  # one question back (the Wizard asks it again)
                raise Back
            if t == "answer" and message["id"] == q.id and not fits(message["value"]):
                self._unfit = True
                return False, None  # the caller hands the question to Terminal and ends the window
            if t not in ("check", "answer") or message["id"] != q.id or not fits(message["value"]):
                continue
            if q.is_secret and isinstance(message["value"], str):
                self.secret = message["value"]
            if t == "answer":
                return True, message["value"]
            if self._live(q) and q.check is not None and isinstance(message["value"], str):
                result = q.check(message["value"], self.wizard.known, self.wizard.mac)
                self._checked(q, result, live=True)

    def _checked(self, q: Question, result: Checked, *, live: bool) -> None:
        """A check's answer to the window: why it refused, or what it kept; never words that hold the secret typed
        (four characters or more: a shorter one is refused by its check, and its digits are in ordinary words)."""
        ok = result.value is not None
        secret = self.secret.strip() if q.is_secret else ""

        def clean(text: str) -> str:
            return "" if len(secret) >= 4 and secret in text else plain_text(text)

        result = Checked(result.value, clean(result.message), clean(result.shown))
        self.window.send(
            {"t": "checked", "id": q.id, "ok": ok, "message": "" if ok else result.message,
             "shown": result.shown if ok else "", "live": live}
        )  # fmt: skip

    def _ask(self, q: Question, fits: Callable[[object], bool], terminal: Callable[[Face], object]) -> object:
        """The window's answer, or the same question in Terminal once it is gone. A question whose screen the window no
        longer shows is drawn again first: asked again after a No at its "Right?" (her number, the home area), the
        window would keep that screen, its buttons off, and the two would wait on each other (review
        setup-window-confirm-hang)."""
        if self.terminal_face is None and self.showing != q.id:
            self.show(q, None)
        if self.terminal_face is None:
            given, value = self._given(q, fits)
            if given:
                return value
            face = self._terminal()
            face.show(q, None)
            return terminal(face)
        return terminal(self.terminal_face)

    def choose(self, q: Question, kept: Value | None) -> Value | None:
        value = self._ask(q, lambda value: isinstance(value, str), lambda face: face.choose(q, kept))
        return value if isinstance(value, str) else None

    def switches(self, q: Question, kept: Value | None) -> Value | None:
        value = self._ask(q, lambda value: isinstance(value, list), lambda face: face.switches(q, kept))
        if isinstance(value, list | tuple):
            return tuple(str(item) for item in value)
        return value if isinstance(value, str) else None

    def confirm(self, q: Question, default: bool) -> bool | None:
        value = self._ask(q, lambda value: isinstance(value, bool), lambda face: face.confirm(q, default))
        return value if isinstance(value, bool) else None

    def text(self, q: Question, kept: Value | None, message: str) -> str | None:
        value = self._ask(q, lambda value: isinstance(value, str), lambda face: face.text(q, kept, message))
        return value if isinstance(value, str) else None

    def fields(self, q: Question, kept: Value | None, message: str) -> dict[str, str] | None:
        ids = [field.id for field in q.fields]
        value = self._ask(
            q,
            lambda value: isinstance(value, dict) and set(value) <= set(ids),
            lambda face: face.fields(q, kept, message),
        )
        if not isinstance(value, dict):
            return None
        return {fid: str(value.get(fid, "")) for fid in ids}

    def hidden(self, q: Question, message: str) -> str | None:
        value = self._ask(q, lambda value: isinstance(value, str), lambda face: face.hidden(q, message))
        if value == "" and q.kind == "pin" and q.later:  # a PIN's Later: no answer, as q in Terminal
            return None
        return value if isinstance(value, str) else None

    def key(self, q: Question, message: str, groups: tuple[str, ...]) -> str | None:
        """The window's own card (its screen shown first: `show`); asked again, the line why goes to that card, whose
        page keeps every group typed (never sent back from here: the groups are the key)."""
        if self.terminal_face is None and message:
            self._checked(q, Checked(None, message), live=False)
        value = self._ask(q, lambda value: isinstance(value, str), lambda face: face.key(q, message, groups))
        return value if isinstance(value, str) else None

    def wait(self, q: Question, look: Callable[[], bool], seconds: float) -> bool:
        """The screen stays while setup looks (the window needs no answer). Between two looks, what the window said
        meanwhile: its Stop here ends setup as Ctrl-C does, and a closed window hands the wait to Terminal (second deep
        scan: nothing was read there, so a stop waited for the next question's screen)."""
        if self.terminal_face is not None:
            return self.terminal_face.wait(q, look, seconds)
        if look():
            return True
        while self.terminal_face is None:
            message = self._next(0)
            if message is None and not self.window.gone:
                break  # nothing more said
            if message is None or message["t"] == "closed":
                self._terminal()  # an AI-run: it stops there
            elif message["t"] == "stop":
                raise KeyboardInterrupt
        return False

    def checked(self, q: Question, result: Checked) -> None:
        """Why an answer was refused, in the window; what was kept, there and in Terminal."""
        if self.terminal_face is not None:
            self.terminal_face.checked(q, result)
            return
        self._checked(q, result, live=False)
        self.secret = ""
        if result.value is not None:  # kept: the page shows its log next, never this screen again
            self.showing = ""
        if result.value is not None and result.shown and self.ai is None:
            print(plain_text(result.shown), flush=True)

    # ---------------------------------------------------------------- before the plan: Change beside Kept

    def offer_changes(self, args: argparse.Namespace) -> bool:
        """The answers kept from an earlier run, each with Change beside it: each changed one is asked again, as
        `--redo` asks it. False when the window closed there."""
        wizard, mac = self.wizard, self.wizard.mac
        kept = [
            q.at(wizard.known, mac, wizard.words())
            for q in QUESTIONS
            if q.plan and q.keep == "saved" and not q.is_secret and wizard.answers.kept(q.id)
            and q.shows(wizard.known, mac)
        ]  # fmt: skip
        if not kept:
            return True
        options = [
            {"id": q.id, "label": q.title, "line": q.shown_value(value), "tag": "", "on": False, "guide": None}
            for q in kept
            if (value := wizard.answers.get(q.id)) is not None
        ]
        screen = {
            "t": "screen", "id": KEPT, "kind": KEPT, "step": 0, "steps": 0, "title": prompts.SETUP_WINDOW_KEPT,
            "lead": prompts.SETUP_WINDOW_KEPT_LEAD, "help": "", "ask": "", "note": "", "options": options,
            "fields": [], "placeholder": "", "recommended": None, "layout": None, "back": False,
        }  # fmt: skip
        if not self.window.send(screen):
            if self.ai is not None:
                self._cannot_go_on("", closed=True)
            return False
        self.showing = KEPT
        self._named(KEPT)
        given, changed = self._given(Question(KEPT, "switches", ""), lambda value: isinstance(value, list))
        if not given:
            if self.ai is not None:
                self._terminal()  # it stops: closed, or an answer that does not fit
            self._hand_over()
            return False
        self.checked(Question(KEPT, "switches", ""), Checked(tuple(changed) if isinstance(changed, list) else ()))
        redo = getattr(args, "redo", None)
        if isinstance(changed, list) and isinstance(redo, list):
            redo += [qid for qid in changed if qid in {q.id for q in kept} and qid not in redo]
        return True


def face(args: argparse.Namespace, mac: Mac, wizard: Wizard) -> WindowFace | None:
    """The Estia Setup window, at the desk only: never over SSH (even with --window), inside screen, tmux or herdr
    (they keep the environment of the session they were started in: reattached over SSH, the window would open on the
    Mac's own screen), with --plain or --terminal, or when it cannot be built or does not open (one line says so);
    None: Terminal (gum's menus or plain lines). A dry run shows its build and opens nothing."""
    ai: Run | None = getattr(wizard, "ai", None)  # an AI-run: the window or a stop, never Terminal
    if mac.ssh or getattr(args, "plain", False) or getattr(args, "terminal", False):
        if ai is not None:
            ai.stop(prompts.SETUP_AI_NOT_AT_DESK if mac.ssh else prompts.SETUP_AI_TERMINAL_FLAG.format(flag="--plain"))
        return None
    if mac.kept_session or any(os.environ.get(key) for key in SESSION_KEYS):
        if ai is not None:
            ai.stop(prompts.SETUP_AI_IN_SESSION)
        return None
    if wizard.dry_run:  # it runs nothing, not even the desk's two questions: the line says "at the desk only"
        build(REPO, dry_run=True, say=wizard.say)
        return None
    if not at_the_desk():
        if ai is not None:
            ai.stop(prompts.SETUP_AI_NOT_AT_DESK)
        return None
    said: list[str] = []

    def say(line: str) -> None:
        said.append(line)
        wizard.say(line)

    built = build(REPO, dry_run=False, say=say)
    if built is None:
        if ai is not None:
            if any(prompts.SETUP_WINDOW_NO_TOOLS in line for line in said):
                ai.stop(prompts.SETUP_AI_NO_TOOLS)
            reasons = (prompts.SETUP_WINDOW_NO_PROGRAM, prompts.SETUP_WINDOW_PAGE_UNREAD)
            why = next((reason for reason in reasons if any(reason in line for line in said)), "")
            ai.stop(prompts.SETUP_AI_NOT_BUILT.format(why=why or prompts.SETUP_WINDOW_BUILD_FAILED))
        return None
    window = Window.open(built)
    if window is None or not window.send({"t": "hello", "v": PROTOCOL, "name": wizard.name}):
        wizard.say(prompts.SETUP_WINDOW_NOT_OPENED)
        if ai is not None:
            ai.stop(prompts.SETUP_AI_NO_WINDOW)
        return None
    wizard.say(prompts.SETUP_WINDOW_OPENING)  # never 20 silent seconds (stops-reruns-window-ready-silence)
    while True:
        ready = window.receive(READY_SECONDS)
        if ready is None or ready["t"] == "ready":
            break
    if ready is None:
        window.process.kill()
        window.close()
        wizard.say(prompts.SETUP_WINDOW_NOT_OPENED)
        if ai is not None:
            ai.stop(prompts.SETUP_AI_NO_WINDOW)
        return None
    if ai is None:  # it names Terminal behind the window, which an AI-run does not have
        wizard.say(prompts.SETUP_WINDOW_OPEN)
    window_face = WindowFace(window, wizard, fallback=lambda: choose_face(args, mac, wizard))
    if ai is not None:  # its end reads the window from here on (a close at the Kept screen too)
        wizard.face = window_face
    return window_face if window_face.offer_changes(args) else None

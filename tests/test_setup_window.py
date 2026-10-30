"""The Estia Setup window: the protocol it and setup speak (one line of JSON at a time, the fixtures shared with the
window's own Swift tests), its page, its build and the window face."""

import argparse
import base64
import contextlib
import hashlib
import json
import os
import plistlib
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import get_args

import pytest

from local_assistant import hostsec, prompts, setup_answers, setup_plan, setup_window
from local_assistant.config import DEFAULT_NAME
from local_assistant.setup import Wizard, main
from local_assistant.setup_answers import Answers
from local_assistant.setup_plan import Mac
from local_assistant.setup_questions import Checked, Field, Kind, Option, Question, plain_text
from local_assistant.setup_window import ProtocolError
from tests.setup_helpers import BOT, enter_but_signal, refuse, signal_plan

pytestmark = pytest.mark.usefixtures("no_real_ollama", "no_system_paths", "a_terminal")

ROOT = Path(__file__).resolve().parents[1]
# af_heart's intro, recorded once (25 Oct: intro2.m4a), as the approved preview's script holds it (intro-clip.js), and
# its loudness and captions (intro-data.js): byte for byte. Re-recorded 30 Oct with the same pinned build (Kokoro on
# FluidAudio 0.15.8, af_heart, speed 1.1), its sentence 5 now "When admin rights are needed, macOS asks in its own box."
# (review W01; the old text's take came out within 32 of 32767 of the approved one)
INTRO_AUDIO = "bfccfe186f933f710c6ace8b094ec0475bef5d99f823ca79de6b512608885ce6"
INTRO_CLIP = "4fd0ee169213d6b5963f2fd8d7db8ee99c177bfa2f48846f30f0b146ac53c4b2"
INTRO_DATA = "6511b41aacbb89d704b94824cae2f01b150e1542dc461117a6e60472d90ec78e"
FIXTURES = ROOT / "tools" / "setup-window" / "Tests" / "SetupWindowCoreTests" / "fixtures"


def fixture(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def bad(name: str) -> list[tuple[str, str]]:
    return [(case["why"], case["line"]) for case in map(json.loads, fixture(name))]


# ------------------------------------------------------------------ the protocol, both ways


def test_setup_speaks_the_windows_version() -> None:
    assert setup_window.PROTOCOL == 2  # 2: the steps' admin commands, run by the window through macOS's own box
    assert (setup_window.FROM_ENGINE_MAX, setup_window.FROM_WINDOW_MAX) == (256 * 1024, 64 * 1024)


def test_every_kind_of_question_has_a_screen_in_the_window() -> None:
    """The window draws each kind the question list has, and its own list of kept answers."""
    kinds = {*get_args(Kind), "kept"}
    assert kinds == setup_window.KINDS
    assert set(json.loads((FIXTURES / "kinds.json").read_text(encoding="utf-8"))) == kinds  # the window's own list


@pytest.mark.parametrize("line", fixture("engine-good.jsonl"))
def test_every_message_setup_sends_is_one_line_of_json(line: str) -> None:
    message = json.loads(line)
    encoded = setup_window.encode(message)
    assert encoded.endswith(b"\n") and encoded.count(b"\n") == 1
    assert json.loads(encoded) == message
    assert encoded.startswith(b'{"t": ')  # t first


@pytest.mark.parametrize(("why", "line"), bad("engine-bad.jsonl"), ids=[why for why, _ in bad("engine-bad.jsonl")])
def test_setup_never_sends_a_message_the_window_refuses(why: str, line: str) -> None:
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return  # setup only ever sends what json.dumps writes
    with pytest.raises(ProtocolError):
        setup_window.encode(message)


@pytest.mark.parametrize("line", fixture("window-good.jsonl"))
def test_every_message_of_the_window_is_read(line: str) -> None:
    assert setup_window.decode(line.encode()) == json.loads(line)


@pytest.mark.parametrize(("why", "line"), bad("window-bad.jsonl"), ids=[why for why, _ in bad("window-bad.jsonl")])
def test_a_message_the_window_should_never_send_is_refused(why: str, line: str) -> None:
    with pytest.raises(ProtocolError):
        setup_window.decode(line.encode())


@pytest.mark.parametrize("line", fixture("admin-good.jsonl"))
def test_every_admin_message_of_the_window_is_read(line: str) -> None:
    """The window's admin runner (never its page) says each line of a command's output, then its result."""
    assert setup_window.decode(line.encode()) == json.loads(line)


@pytest.mark.parametrize(("why", "line"), bad("admin-bad.jsonl"), ids=[why for why, _ in bad("admin-bad.jsonl")])
def test_an_admin_message_of_another_shape_is_refused(why: str, line: str) -> None:
    if json.loads(line).get("t") == "answer":
        return  # one of the page's own: read as such
    with pytest.raises(ProtocolError):
        setup_window.decode(line.encode())


def test_an_admin_command_holds_its_words_as_a_list_never_shell_text() -> None:
    """Setup never sends what the window would refuse: an absolute program, no NUL or line break in a word, at most
    128 words and 128 KiB, a box's reason on one line of at most 200 characters."""
    good = {"t": "admin", "id": "a1", "argv": ["/bin/echo", "it's $(x)"], "input": None, "open": False, "why": "Why."}
    assert json.loads(setup_window.encode(good)) == good
    for change in (
        {"argv": ["echo"]},
        {"argv": ["/bin/echo", "a\nb"]},
        {"argv": ["/bin/echo", "a\rb"]},
        {"argv": ["/bin/echo"] * 129},
        {"argv": ["/bin/echo", "x" * (128 * 1024)]},
        {"why": "x" * 201},
        {"why": "a\nb"},
        {"id": "A1"},
    ):
        with pytest.raises(ProtocolError):
            setup_window.encode({**good, **change})


def test_a_line_over_its_limit_is_refused_either_way() -> None:
    value = "x" * (setup_window.FROM_WINDOW_MAX - len('{"t": "answer", "id": "name", "value": ""}'))
    at_limit = json.dumps({"t": "answer", "id": "name", "value": value}).encode()
    assert len(at_limit) == setup_window.FROM_WINDOW_MAX
    assert setup_window.decode(at_limit)["value"] == value
    with pytest.raises(ProtocolError):
        setup_window.decode(json.dumps({"t": "answer", "id": "name", "value": value + "x"}).encode())
    with pytest.raises(ProtocolError):
        setup_window.encode({"t": "progress", "line": "x" * setup_window.FROM_ENGINE_MAX})


def test_a_line_that_is_not_utf8_is_refused() -> None:
    with pytest.raises(ProtocolError):
        setup_window.decode(b'{"t": "answer", "id": "name", "value": "\xff"}')


# ------------------------------------------------------------------ the page: its own files only, the approved intro

PAGE = ROOT / "tools" / "setup-window" / "Page"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
TAGLINE = "An insanely secure & private thinking partner, living at home."  # the owner's words, "&" kept (26 Oct)
SIG = (
    "Estia needs no account and is open source. Setup registers only her own Signal account; you reach her from the "
    "free Signal app on your phone or laptop."
)


class Tags(HTMLParser):
    """Every start tag of page.html with its attributes, and the text inside each <section>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.texts: dict[str, list[str]] = {}
        self.section = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        given = {key: value or "" for key, value in attrs}
        self.tags.append((tag, given))
        if tag == "section":
            self.section = given.get("id", "")

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.texts.setdefault(self.section, []).append(data.strip())


def page_tags() -> Tags:
    tags = Tags()
    tags.feed((PAGE / "page.html").read_text(encoding="utf-8"))
    return tags


def test_the_page_is_exactly_its_listed_files() -> None:
    assert sorted(path.name for path in PAGE.iterdir()) == sorted(setup_window.PAGE_FILES)


def test_the_page_loads_nothing_from_the_network() -> None:
    for tag, attrs in page_tags().tags:
        for key in ("src", "href", "srcset", "poster", "data"):
            assert "//" not in attrs.get(key, ""), (tag, key, attrs[key])  # http(s)://, or //host
    css = (PAGE / "page.css").read_text(encoding="utf-8")
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    for text in ((PAGE / "page.html").read_text(encoding="utf-8"), css, script):
        assert "googleapis" not in text and "gstatic" not in text and "http://" not in text and "https://" not in text
    assert "@import" not in css and "url(" not in css


def test_a_force_click_on_a_link_shows_no_preview() -> None:
    """WebKit's link preview (a force click, Look Up) loads the page in a preview of its own, outside the window's
    navigation rules, content rules and in-memory store: turned off, so a link opens only in the browser."""
    swift = (ROOT / "tools/setup-window/Sources/estia-setup/WindowController.swift").read_text(encoding="utf-8")
    assert "webView.allowsLinkPreview = false" in swift


def test_every_line_typed_in_the_page_stays_under_the_windows_line_limit() -> None:
    """An answer over 64 KiB never reaches setup (the window ends, and setup asks in Terminal): every input the page
    types into has a limit, a typed line's (a captcha link fits) well under it."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    inputs = re.findall(r'el\("input", \{(.*?)\}\)', script, re.S)
    typed = [attrs for attrs in inputs if 'type: "radio"' not in attrs and 'type: "checkbox"' not in attrs]
    assert len(typed) >= 5 and all("maxlength" in attrs for attrs in typed), typed
    found = re.search(r"LINE_MAX = (\d+)", script)
    assert found is not None
    limit = int(found[1])
    assert limit >= 2048 and limit * 3 < setup_window.FROM_WINDOW_MAX  # three bytes of UTF-8 for any typed character


def test_the_page_carries_its_rules_and_runs_no_inline_script_or_style() -> None:
    tags = page_tags().tags
    metas = [attrs for tag, attrs in tags if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"]
    assert [meta["content"] for meta in metas] == [CSP]
    swift = (ROOT / "tools/setup-window/Sources/SetupWindowCore/Page.swift").read_text(encoding="utf-8")
    block = swift.split("public static let contentSecurityPolicy =", 1)[1].split("\n\n", 1)[0]
    assert "".join(re.findall(r'"([^"]*)"', block)) == CSP  # the scheme handler's header: the same rules
    assert setup_window.CONTENT_SECURITY_POLICY == CSP
    html = (PAGE / "page.html").read_text(encoding="utf-8")
    assert "<style" not in html and " style=" not in html
    assert all(attrs.get("src") for tag, attrs in tags if tag == "script")  # no inline script
    assert not any(key.startswith("on") for _, attrs in tags for key in attrs)  # no onclick=…
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    for unsafe in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        '"style"',
    ):
        assert unsafe not in script, unsafe


def test_the_intro_is_the_approved_one() -> None:
    """The wordmark, the tagline at rest, the sig line, af_heart's recording and its captions, as approved (26 Oct)."""
    texts = page_tags().texts["p-intro"]
    assert "estia one" in texts and TAGLINE in texts and SIG in texts
    assert f'const TAGLINE = "{TAGLINE}";' in (PAGE / "page.js").read_text(encoding="utf-8")
    clip = (PAGE / "intro-clip.js").read_bytes()
    assert hashlib.sha256(clip).hexdigest() == INTRO_CLIP
    prefix = b'const CLIP = "data:audio/mp4;base64,'
    assert clip.startswith(prefix) and clip.endswith(b'";\n')
    assert hashlib.sha256(base64.b64decode(clip[len(prefix) : -3])).hexdigest() == INTRO_AUDIO
    assert hashlib.sha256((PAGE / "intro-data.js").read_bytes()).hexdigest() == INTRO_DATA


def test_the_intros_mark_is_the_mark_files_ring_and_square() -> None:
    """The intro draws Estia's mark (owner, 28 Oct: its centre an orange rounded square, the ring and its colours kept):
    the white disc, the ring the recording writes on, its wave, then the mark file's square, unchanged; never the old
    dot. The speaking ring moves only the ring and the wave: the square stays where it is."""
    html = (PAGE / "page.html").read_text(encoding="utf-8")
    mark_file = (ROOT / "docs" / "brand" / "estia-mark.svg").read_text(encoding="utf-8")
    square = re.search(r"<rect [^>]*/>", mark_file)
    assert square is not None
    svg = re.search(r'<svg class="mark" [^>]*>(.*?)</svg>', html, re.S)
    assert svg is not None
    drawn = re.findall(r"<(circle|path|rect) ([^>]*)/>", svg.group(1))
    assert [tag for tag, _ in drawn] == ["circle", "path", "path", "rect"]
    assert f"<rect {drawn[3][1]}/>" == square.group(0)
    assert 'r="42.25" fill="#ffffff"' in drawn[0][1] and 'id="ring"' in drawn[1][1] and 'id="wave"' in drawn[2][1]
    assert 'aria-label="Estia\'s mark: a silver ring around an orange rounded square"' in html
    assert 'r="10"' not in svg.group(1)


def test_the_password_line_is_gone_from_every_screen() -> None:
    """Owner, 27 Oct: "lets remove the message about the Mac password": no screen of the window shows it (the
    intro's recording, approved 26 Oct, still says it as she speaks: its captions are the recording's)."""
    tags = page_tags()
    assert not any("Mac password" in text for text in tags.texts["p-intro"])
    assert not hasattr(prompts, "SETUP_WINDOW_NO_PASSWORD")


def test_the_ring_keeps_the_approved_values() -> None:
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    for value in ("MAXD = 7, FLOOR = 0.3", "NP = 130", "FAT = 7, THIN = 4.5", "Catmull-Rom"):
        assert value in script, value
    wave = next(attrs for tag, attrs in page_tags().tags if tag == "path" and attrs.get("id") == "wave")
    assert wave["stroke-width"] == "1.9"
    ring = next(attrs for tag, attrs in page_tags().tags if tag == "path" and attrs.get("id") == "ring")
    assert (ring["stroke"], ring["stroke-width"]) == ("#8d9298", "7")


def test_the_page_is_assembled_with_its_fingerprints(tmp_path: Path) -> None:
    target = tmp_path / "page"
    fingerprint = setup_window.assemble_page(PAGE, target)
    manifest = (target / "page.json").read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == fingerprint
    listed = json.loads(manifest)
    assert listed["v"] == 1 and [entry["path"] for entry in listed["files"]] == list(setup_window.PAGE_FILES)
    for entry in listed["files"]:
        body = (target / entry["path"]).read_bytes()
        assert body == (PAGE / entry["path"]).read_bytes()
        assert entry["sha256"] == hashlib.sha256(body).hexdigest()
    types = {entry["path"]: entry["type"] for entry in listed["files"]}
    assert types["page.html"] == "text/html; charset=utf-8" and types["page.css"] == "text/css; charset=utf-8"
    assert types["page.js"] == types["intro-data.js"] == types["intro-clip.js"] == "text/javascript; charset=utf-8"
    assert sorted(path.name for path in target.iterdir()) == sorted([*setup_window.PAGE_FILES, "page.json"])
    assert setup_window.assemble_page(PAGE, target) == fingerprint  # again: the same page, the same fingerprint


def test_a_page_file_that_is_a_link_is_never_assembled(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(PAGE, source)
    (source / "page.css").unlink()
    (source / "page.css").symlink_to(PAGE / "page.css")
    with pytest.raises(OSError):
        setup_window.assemble_page(source, tmp_path / "page")


def test_the_page_draws_every_kind_of_question() -> None:
    """Each kind with an answer has its drawing in page.js; info, terminal and progress show words and the log."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    block = script.split("const DRAW = {", 1)[1].split("};", 1)[0]
    drawn = set(re.findall(r"\b([a-z_]+):", block))
    assert drawn | {"info", "terminal", "progress"} == setup_window.KINDS


# ------------------------------------------------------------------ the build: once, from the checkout, into its bundle


def a_checkout(tmp_path: Path) -> Path:
    """The window's sources in a checkout of the test's own (never the repo's own .build)."""
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "tools/setup-window", repo / "tools/setup-window", ignore=shutil.ignore_patterns(".build"))
    return repo


def build_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, clt: int = 0, swift: int = 0) -> Path:
    """/usr/bin/xcode-select and /usr/bin/swift under the tools' root: xcode-select names the Command Line Tools (or
    fails with `clt`); swift's build writes a program into its bin folder (or fails with `swift`). Each start's
    arguments in the log."""
    root = tmp_path / "tools"
    (root / "usr/bin").mkdir(parents=True)
    log = tmp_path / "build.log"
    (root / "usr/bin/xcode-select").write_text(
        f'#!/bin/sh\necho "xcode-select $*" >> {shlex.quote(str(log))}\n'
        f"[ {clt} = 0 ] && echo /Library/Developer/CommandLineTools\nexit {clt}\n"
    )
    (root / "usr/bin/swift").write_text(
        "#!/bin/sh\n"
        f'echo "swift $*" >> {shlex.quote(str(log))}\n'
        'while [ $# -gt 0 ]; do [ "$1" = "--package-path" ] && pkg="$2"; [ "$1" = "--show-bin-path" ] && show=1; shift; done\n'
        'bin="$pkg/.build/arm64-apple-macosx/release"\n'
        'if [ -n "$show" ]; then echo "$bin"; exit 0; fi\n'
        f"[ {swift} = 0 ] || exit {swift}\n"
        'mkdir -p "$bin" && printf "#!/bin/sh\\nexit 0\\n" > "$bin/estia-setup" && chmod 755 "$bin/estia-setup"\n'
    )
    for tool in ("xcode-select", "swift"):
        (root / "usr/bin" / tool).chmod(0o755)
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(root))
    return log


def ran(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def test_the_window_is_built_once_into_its_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = a_checkout(tmp_path)
    log = build_tools(tmp_path, monkeypatch)
    said: list[str] = []
    built = setup_window.build(repo, dry_run=False, say=said.append)
    package = repo / "tools/setup-window"
    assert built is not None and built.app == package / ".build" / "Estia Setup.app"
    assert said == [prompts.SETUP_WINDOW_BUILDING]
    assert ran(log) == [
        "xcode-select -p",
        f"swift build -c release --package-path {package} --product estia-setup",
        f"swift build -c release --package-path {package} --show-bin-path",
    ]
    contents = built.app / "Contents"
    assert built.program == contents / "MacOS/estia-setup" and os.access(built.program, os.X_OK)
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "estia-setup" and info["CFBundleName"] == "Estia Setup"
    assert info["CFBundlePackageType"] == "APPL" and info["LSMinimumSystemVersion"] == "14.0"
    assert built.page == contents / "Resources/page"
    assert hashlib.sha256((built.page / "page.json").read_bytes()).hexdigest() == built.manifest
    assert built.manifest == hashlib.sha256(setup_window.page_manifest(package / "Page")).hexdigest()
    said.clear()
    again = setup_window.build(repo, dry_run=False, say=said.append)
    assert again == built and said == [] and len(ran(log)) == 4  # only xcode-select again: nothing built


def test_a_changed_source_builds_again_a_changed_page_only_assembles_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = a_checkout(tmp_path)
    log = build_tools(tmp_path, monkeypatch)
    package = repo / "tools/setup-window"
    first = setup_window.build(repo, dry_run=False, say=lambda line: None)
    assert first is not None
    page = package / "Page/page.css"
    page.write_text(page.read_text() + "\n/* changed */\n")
    second = setup_window.build(repo, dry_run=False, say=lambda line: None)
    assert second is not None and second.manifest != first.manifest
    assert (second.page / "page.css").read_text() == page.read_text()
    assert sum("--product" in line for line in ran(log)) == 1  # the program was not built again
    main = package / "Sources/estia-setup/main.swift"
    main.write_text(main.read_text() + "\n// changed\n")
    assert setup_window.build(repo, dry_run=False, say=lambda line: None) is not None
    assert sum("--product" in line for line in ran(log)) == 2
    first.program.write_bytes(b"#!/bin/sh\nexit 3\n")  # the program changed since its build: built again
    assert setup_window.build(repo, dry_run=False, say=lambda line: None) is not None
    assert sum("--product" in line for line in ran(log)) == 3


@pytest.mark.parametrize(
    ("clt", "swift", "why"),
    [(2, 0, "the Command Line Tools are not installed"), (0, 1, "its build failed")],
    ids=["no-command-line-tools", "build-failed"],
)
def test_a_window_that_cannot_be_built_leaves_setup_in_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clt: int, swift: int, why: str
) -> None:
    repo = a_checkout(tmp_path)
    log = build_tools(tmp_path, monkeypatch, clt=clt, swift=swift)
    said: list[str] = []
    assert setup_window.build(repo, dry_run=False, say=said.append) is None
    assert said[-1] == prompts.SETUP_WINDOW_NOT_BUILT.format(why=why)
    assert not (repo / "tools/setup-window/.build/Estia Setup.app/Contents/MacOS/estia-setup").exists()
    if clt:
        assert ran(log) == ["xcode-select -p"]  # never swift: its stand-in asks to install the tools


def test_a_dry_run_shows_the_build_and_runs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = a_checkout(tmp_path)
    log = build_tools(tmp_path, monkeypatch)
    said: list[str] = []
    assert setup_window.build(repo, dry_run=True, say=said.append) is None
    package = repo / "tools/setup-window"
    command = f"{hostsec.tool_root() / 'usr/bin/swift'} build -c release --package-path {package} --product estia-setup"
    assert said == [prompts.SETUP_WINDOW_DRY_RUN.format(command=command)]
    assert ran(log) == [] and not (package / ".build").exists()


@pytest.mark.skipif(
    os.environ.get("SETUP_WINDOW_REAL") != "1", reason="opt-in: SETUP_WINDOW_REAL=1 (builds with swift)"
)
def test_the_real_build_makes_a_window_whose_page_checks_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Mac's own swift builds the bundle in the checkout's tools/setup-window/.build; its program then checks the
    assembled page by page.json's sha256 (--check: no window)."""
    monkeypatch.delenv("ESTIA_TEST_ROOT")  # the Mac's own xcode-select and swift
    built = setup_window.build(ROOT, dry_run=False, say=print)
    assert built is not None
    check = [str(built.program), "--page", str(built.page), "--manifest", built.manifest, "--check"]
    assert subprocess.run(check, capture_output=True, timeout=60, check=False).returncode == 0
    wrong = [*check[:4], "0" * 64, "--check"]
    assert subprocess.run(wrong, capture_output=True, timeout=60, check=False).returncode == 1
    # no Apple Event to another app (Terminal comes to the front by an app activation): no Automation permission to
    # ask; its one AppleScript (admin rights through macOS's own box) is NSAppleScript's, an event to itself
    linked = subprocess.run(["/usr/bin/nm", "-u", str(built.program)], capture_output=True, text=True, timeout=60)
    assert linked.returncode == 0 and "NSRunningApplication" in linked.stdout
    symbols = linked.stdout.splitlines()
    apple = {
        symbol for symbol in symbols if re.search(r"AppleEvent|AppleScript|AESend|OSA|SBApplication", symbol, re.I)
    }
    assert apple <= {"_OBJC_CLASS_$_NSAppleScript", "_OBJC_CLASS_$_NSAppleEventDescriptor", "_NSAppleScriptErrorNumber"}
    assert "_OBJC_CLASS_$_NSAppleScript" in apple  # the admin runner's (AppleScriptElevator)


# ------------------------------------------------------------------ the window face: at the desk, Terminal over SSH

FAKE_WINDOW = Path(__file__).parent / "fakes" / "fake_setup_window.py"


class Stopped(Exception):
    """The test's stand-in for a step: setup stops there."""


def a_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *script: dict[str, object], silent: bool = False) -> Path:
    """The fake window as the built program at the desk: setup_window.build gives it, at_the_desk says yes. Its
    folder (calls.jsonl, received.jsonl, script.json) is written into its wrapper: the window gets a clean
    environment."""
    folder = tmp_path / "window"
    folder.mkdir()
    (folder / "script.json").write_text(json.dumps(list(script)), encoding="utf-8")
    if silent:
        (folder / "silent").touch()
    program = tmp_path / "Estia Setup.app/Contents/MacOS/estia-setup"
    program.parent.mkdir(parents=True)
    run = f"exec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_WINDOW))}"
    program.write_text(f'#!/bin/sh\nFAKE_WINDOW_DIR={shlex.quote(str(folder))} {run} "$@"\n')
    program.chmod(0o755)
    built = setup_window.Built(program.parents[2], program, tmp_path / "page", "a" * 64)
    monkeypatch.setattr(setup_window, "build", lambda repo, *, dry_run, say: built)
    monkeypatch.setattr(setup_window, "at_the_desk", lambda: True)
    opened = OPENED.setdefault(folder, [])
    real = setup_window.Window.open

    def open_and_keep(built: setup_window.Built) -> setup_window.Window | None:
        window = real(built)
        if window is not None:
            opened.append(window)
        return window

    monkeypatch.setattr(setup_window.Window, "open", staticmethod(open_and_keep))
    real_receive = setup_window.Window.receive

    def bounded(window: setup_window.Window, seconds: float | None = None) -> dict[str, object] | None:
        """setup's wait for the window, never longer than WAIT_SECONDS: the test fails where it would hang."""
        message = real_receive(window, WAIT_SECONDS if seconds is None else seconds)
        if message is None and seconds is None and not window.gone:
            pytest.fail(f"setup waited {WAIT_SECONDS:.0f} s for the window: the two wait on each other")
        return message

    monkeypatch.setattr(setup_window.Window, "receive", bounded)
    return folder


OPENED: dict[Path, list[setup_window.Window]] = {}  # each test's windows, ended before their lines are read
# the stand-in answers each screen at once: setup waiting longer than this for one means the two wait on each other
WAIT_SECONDS = 20.0


@pytest.fixture(autouse=True)
def no_window_left() -> Iterator[None]:
    """Every stand-in a test opened is ended with it, passed or failed: none waits on after its test."""
    yield
    for windows in OPENED.values():
        for window in windows:
            window.close()
            with contextlib.suppress(OSError):
                window.process.kill()
            window.process.wait(timeout=10)
    OPENED.clear()


def received(folder: Path) -> list[dict[str, object]]:
    """Every line setup sent the window, once each window it opened has read them all (its stdin ended)."""
    for window in OPENED.pop(folder, []):
        window.close()
        window.process.wait(timeout=10)
    path = folder / "received.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def screens(folder: Path) -> list[str]:
    return [str(message["id"]) for message in received(folder) if message["t"] == "screen"]


def window_calls(folder: Path) -> list[dict[str, object]]:
    path = folder / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def stop_at_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    def ollama(self: Wizard) -> None:
        raise Stopped

    monkeypatch.setattr(Wizard, "ollama", ollama)


def saved() -> dict[str, object]:
    path = setup_answers.default_path()
    return tomllib.loads(path.read_text(encoding="utf-8"))["answers"] if path.exists() else {}


def desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, manager: str, console: str) -> None:
    """/bin/launchctl and /usr/bin/stat under the tools' root, as the Mac answers them."""
    root = tmp_path / "desk"
    for tool, answer in (("bin/launchctl", manager), ("usr/bin/stat", console)):
        path = root / tool
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\necho {shlex.quote(answer)}\n")
        path.chmod(0o755)
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(root))


@pytest.mark.parametrize(
    ("manager", "console", "remote", "at_desk"),
    [
        ("Aqua", "me", "", True),
        ("Aqua", "me", "SSH_CONNECTION", False),  # a remote login: the window would show on the Mac's own screen
        ("Aqua", "me", "SSH_TTY", False),
        ("Background", "me", "", False),  # no one signed in on this session's screen
        ("System", "me", "", False),
        ("Aqua", "root", "", False),  # the login window, or someone else at the Mac
    ],
)
def test_the_window_opens_only_at_the_desk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manager: str, console: str, remote: str, at_desk: bool
) -> None:
    me = pwd.getpwuid(os.getuid()).pw_name
    desk(tmp_path, monkeypatch, manager=manager, console=me if console == "me" else console)
    if remote:
        monkeypatch.setenv(remote, "203.0.113.7 51234 192.0.2.1 22")
    assert setup_window.at_the_desk() is at_desk


def test_without_the_macs_own_answers_it_is_not_the_desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(tmp_path / "nothing"))  # no launchctl, no stat
    assert setup_window.at_the_desk() is False


@pytest.mark.parametrize(
    ("args", "mac"),
    [
        (argparse.Namespace(window=True), Mac(ssh=True)),  # over SSH never, even asked for
        (argparse.Namespace(plain=True), Mac()),
        (argparse.Namespace(terminal=True), Mac()),
    ],
    ids=["ssh-even-with-window", "plain", "terminal"],
)
def test_the_window_is_never_built_or_opened_over_ssh_or_when_terminal_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: argparse.Namespace, mac: Mac
) -> None:
    folder = a_window(tmp_path, monkeypatch)
    monkeypatch.setattr(setup_window, "build", refuse)
    assert setup_window.face(args, mac, Wizard(tmp_path / "config.toml", dry_run=False)) is None
    assert window_calls(folder) == []


@pytest.mark.parametrize("session", ["STY", "TMUX", "HERDR_ENV"])
def test_inside_screen_tmux_or_herdr_the_window_never_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: str
) -> None:
    """Review SEC-7: screen, tmux and herdr keep the environment of the session they were started in, so setup run
    there after reattaching over SSH looks like the desk: the window would open on the Mac's own screen while
    Terminal waits. Never there."""
    folder = a_window(tmp_path, monkeypatch)
    monkeypatch.setattr(setup_window, "build", refuse)
    monkeypatch.setenv(session, "1")
    mac = Mac.read(memory_gb=32.0, own_user=False)
    assert mac.kept_session
    assert setup_window.face(argparse.Namespace(), mac, Wizard(tmp_path / "c.toml", dry_run=False)) is None
    assert setup_window.face(argparse.Namespace(), Mac(), Wizard(tmp_path / "c.toml", dry_run=False)) is None
    assert window_calls(folder) == []


def test_setup_with_terminal_asks_in_terminal_never_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = a_window(tmp_path, monkeypatch)
    monkeypatch.setattr(setup_window, "build", refuse)
    monkeypatch.setenv("TERM", "dumb")  # plain lines
    stop_at_ollama(monkeypatch)
    monkeypatch.setattr("builtins.input", enter_but_signal)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml"), "--terminal"])
    assert window_calls(folder) == []


def test_a_dry_run_shows_the_windows_build_and_opens_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(setup_window, "at_the_desk", lambda: True)
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml"), "--dry-run"])
    out = capsys.readouterr().out
    assert "The setup window, at the desk only: built once from tools/setup-window, then opened:" in out
    assert not (setup_window.REPO / "tools/setup-window/.build/never").exists()


def test_the_plan_through_the_window_keeps_what_terminal_keeps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same answers in the window and in plain lines: the same answers file (the timestamps apart)."""
    stop_at_ollama(monkeypatch)
    nothing = [""] * 8  # Extra Features as recommended; no home area (street, city); About you left out (five fields)
    answers = iter(["Juno", "m", "y", "", "+30 690 000 0000", "y", "n", "nl", "playful", "n", *nothing, "n", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "terminal" / "config.toml"), "--plain"])
    in_terminal = saved()
    setup_answers.default_path().unlink()
    folder = a_window(
        tmp_path,
        monkeypatch,
        {"answer": "Juno"},  # her own name, typed
        {"answer": "male"},
        {"answer": True},  # Signal on the phone
        {"answer": "number"},
        {"answer": "+30 690 000 0000"},
        {"answer": True},  # her number, shown back: right
        {"answer": False},  # Registration Lock: not now
        {"answer": "nl"},
        {"answer": "playful"},
        {"answer": "no"},  # calls: Not now
        {"answer": ["web", "maps"]},
        {"answer": {}},
        {"answer": {}},
        {"answer": False},
        {"answer": False},
    )
    monkeypatch.setattr("builtins.input", refuse)  # nothing asked in Terminal
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "window" / "config.toml")])
    about_you = {"name": "", "work": "", "people": "", "interests": "", "day": ""}
    assert saved() == in_terminal == {
        "name": "Juno", "voice": "male", "signal_app": True, "route": "number", "number": BOT, "lock": False,
        "language": "nl", "style": "playful", "calls": "no", "extras": ["web", "maps"], "home_area": {},
        "about_you": about_you,
    }  # fmt: skip
    assert screens(folder) == [
        "name", "voice", "signal_app", "route", "number", "number_right", "lock", "language", "style", "calls",
        "extras", "home_area", "about_you", "move", "security",
    ]  # fmt: skip
    steps = [(m["step"], m["steps"]) for m in received(folder) if m["t"] == "screen"]
    assert steps[:2] == [(1, 12), (2, 13)]  # the voice came with a typed name
    assert steps[5] == (0, 0)  # her number shown back: no step of the plan's own
    assert steps[10:12] == [(10, 13), (11, 14)] and steps[-1] == (14, 14)  # the home area with places picked


def test_a_refused_answer_comes_back_with_the_checks_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = a_window(tmp_path, monkeypatch, {"answer": "R2-D2!"}, {"answer": "Maya"})
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    checked = [m for m in received(folder) if m["t"] == "checked" and m["id"] == "name"]
    assert [(m["ok"], m["message"], m["shown"], m["live"]) for m in checked] == [
        (False, prompts.SETUP_NAME_RULE, "", False),
        (True, "", prompts.SETUP_NAME_SHOWN.format(name="Maya"), False),
    ]
    assert saved()["name"] == "Maya"


def test_a_no_at_her_number_shown_back_draws_the_number_screen_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review setup-window-confirm-hang: a No at "Your number … Right?" asks the number again, so its screen comes
    again (before, the window kept the "Right?" screen with its buttons off, and setup waited for ever)."""
    folder = a_window(
        tmp_path,
        monkeypatch,
        {"answer": "Maya"},
        {"answer": True},  # Signal on the phone
        {"answer": "number"},
        {"answer": "+30 690 000 0001"},
        {"answer": False},  # not her number: asked again
        {"answer": "+30 690 000 0000"},
        {"answer": True},
    )
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    shown = [m for m in received(folder) if m["t"] == "screen"]
    assert [m["id"] for m in shown][3:8] == ["number", "number_right", "number", "number_right", "lock"]
    assert shown[5]["back"] is True and shown[5]["stop"] == prompts.SETUP_NUMBER_STOP  # the same screen as before
    assert saved()["number"] == BOT


def test_a_no_at_her_number_gone_back_to_keeps_its_answer_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gone back to, her number's screen holds the answer before; a No at its "Right?" draws it so again."""
    folder = a_window(
        tmp_path,
        monkeypatch,
        {"answer": "Maya"},
        {"answer": True},
        {"answer": "number"},
        {"answer": "+30 690 000 0001"},
        {"answer": True},
        {"back": True},  # at the Registration Lock
        {"answer": "+30 690 000 0002"},
        {"answer": False},
        {"answer": "+30 690 000 0000"},
        {"answer": True},
    )
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    numbers = [m for m in received(folder) if m["t"] == "screen" and m["id"] == "number"]
    assert [m.get("kept") for m in numbers] == [None, "+306900000001", "+306900000001"]
    assert saved()["number"] == BOT


def test_a_no_at_the_home_area_found_draws_the_home_area_screen_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review setup-window-confirm-hang: a No at "Found: …. Right?" asks the street and the city again."""
    from local_assistant import mail_http, osm
    from tests.fakes.fake_http import FakeHttp

    http = FakeHttp()
    for _ in range(2):
        http.add(osm.NOMINATIM_URL, 200, [{"lat": "37.9763", "lon": "23.7260", "name": "Ermou"}])
    monkeypatch.setattr(mail_http, "http", http)
    area = {"street": "Ermou", "city": "Athens"}
    folder = a_window(
        tmp_path,
        monkeypatch,
        {"answer": "Maya"},
        {"answer": True},
        {"answer": "number"},
        {"answer": "+30 690 000 0000"},
        {"answer": True},
        {"answer": False},  # Registration Lock: not now
        {"answer": "en"},
        {"answer": "hearth"},
        {"answer": "no"},  # calls: Not now
        {"answer": ["maps"]},
        {"answer": {"street": "Ermou", "city": "Athina"}},
        {"answer": False},  # not her area: asked again
        {"answer": area},
        {"answer": True},
        {"answer": {}},
        {"answer": False},
        {"answer": False},
    )
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    ids = [m["id"] for m in received(folder) if m["t"] == "screen"]
    assert ids[ids.index("extras") + 1 :][:5] == ["home_area", "home_found", "home_area", "home_found", "about_you"]
    assert saved()["home_area"] == area


def test_a_typed_line_is_checked_while_typing_and_nothing_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def digits(value: object, answers: object, mac: object) -> Checked:
        text = value if isinstance(value, str) else ""
        return Checked(text, shown="Fits.") if text.isdigit() else Checked(None, "Only digits.")

    folder = a_window(
        tmp_path,
        monkeypatch,
        {"check": "12a"},
        {"check": "123", "answer": "123"},
        {"check": {"city": "x"}, "answer": {"city": "Athens"}},
    )
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert isinstance(face, setup_window.WindowFace)
    q = Question("code_word", "text", "A number", ask="A number?", check=digits, plan=False, keep="step")
    face.show(q, None)
    assert face.text(q, None, "") == "123"
    fields = Question(
        "place", "fields", "Place", fields=(Field("city", "City"),), plan=False, keep="step", check=digits
    )
    face.show(fields, None)
    assert face.fields(fields, None, "") == {"city": "Athens"}  # a field set is never checked while typing
    live = [(m["ok"], m["message"], m["shown"]) for m in received(folder) if m["t"] == "checked" and m["live"]]
    assert live == [(False, "Only digits.", ""), (True, "", "Fits.")]


def test_typing_ollamas_key_sends_no_request_and_its_answer_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review SEC-1: Ollama's key gets one test search at ollama.com once it is given, never while it is typed (each
    check would send what is typed so far as the key, and wait up to 15 s); the page is told so."""
    from local_assistant import setup_extras

    tried: list[str] = []

    def answers(key: str) -> tuple[int, str]:
        tried.append(key)
        return 200, ""

    monkeypatch.setattr(setup_extras, "ollama_answers", answers)
    folder = a_window(tmp_path, monkeypatch, {"check": "sk-1"}, {"check": "sk-12", "answer": "sk-123"})
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    assert wizard.answer("ollama_key") == "sk-123"
    assert tried == ["sk-123"]
    lines = received(folder)
    assert next(m for m in lines if m["t"] == "screen")["live"] is False
    assert not any(m["t"] == "checked" and m["live"] for m in lines)


def test_a_typed_line_whose_check_reads_nothing_outside_setup_is_checked_live() -> None:
    wizard = Wizard(Path("/x/c.toml"), dry_run=True)
    face = setup_window.WindowFace(setup_window.Window.__new__(setup_window.Window), wizard, lambda: wizard.face)
    for qid, live in (("number", True), ("captcha", True), ("lock_pin", True), ("ollama_key", False), ("name", False)):
        q = setup_plan.question(qid)
        assert face.screen(q)["live"] is live, qid


def test_the_captcha_screen_links_its_page_and_the_word_shows_large_with_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review PROM-16: the captcha's page is a link (opened in the browser), and the one-time word is the wait
    screen's one option, which the page draws large with Copy."""
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    face = setup_window.WindowFace(setup_window.Window.__new__(setup_window.Window), wizard, lambda: wizard.face)
    captcha = face.screen(setup_plan.question("captcha"))
    assert captcha["links"] == [["signalcaptchas.org", "https://signalcaptchas.org/registration/generate.html"]]
    setup_window.encode(captcha)  # one of the window's messages
    shown: list[Question] = []

    class Shown(Exception):
        pass

    def show(q: Question, kept: object) -> None:
        shown.append(q)
        raise Shown

    monkeypatch.setattr(wizard.face, "show", show)
    with pytest.raises(Shown):
        wizard.owner("+306900000000")
    word = shown[0]
    assert len(word.options) == 1 and re.fullmatch(r"estia-\d{4}", word.options[0].id)
    assert word.options[0].label == word.options[0].id
    setup_window.encode(face.screen(word))


def test_a_closed_window_hands_the_same_question_to_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = a_window(tmp_path, monkeypatch, {"answer": "Maya"}, {"close": True})
    stop_at_ollama(monkeypatch)
    typed: list[str] = []
    answers = iter(["nl", "playful"])

    def type_in_terminal(prompt: str = "") -> str:
        typed.append(prompt.strip())
        planned = signal_plan(prompt)
        return planned if planned is not None else next(answers, "")

    monkeypatch.setattr("builtins.input", type_in_terminal)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    out = capsys.readouterr().out
    assert prompts.SETUP_WINDOW_CLOSED in out
    assert typed[0].startswith(prompts.SETUP_SIGNAL_APP_ASK)  # the same question, now in Terminal
    assert plain_text(prompts.SETUP_SIGNAL_APP.format(name="Maya")) in out  # its lines as plain lines print them
    assert screens(folder) == ["name", "signal_app"]
    kept = saved()
    asked = {key: kept[key] for key in ("name", "language", "style")}
    assert asked == {"name": "Maya", "language": "nl", "style": "playful"}
    assert kept["calls"] == "yes"  # the rest of the plan in Terminal too: Enter keeps the recommended


def test_an_answer_that_does_not_fit_its_screen_ends_the_window_and_terminal_asks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A yes or no for a pick (a page out of step with setup) leaves the window waiting for setup's word, its buttons
    off: setup ends it, as a line it should never send, and asks the same question in Terminal. Nothing waits."""
    folder = a_window(tmp_path, monkeypatch, {"answer": True})
    stop_at_ollama(monkeypatch)
    typed: list[str] = []
    answers = iter(["Maya"])

    def type_in_terminal(prompt: str = "") -> str:
        typed.append(prompt.strip())
        planned = signal_plan(prompt)
        return planned if planned is not None else next(answers, "")

    monkeypatch.setattr("builtins.input", type_in_terminal)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    assert prompts.SETUP_WINDOW_CLOSED in capsys.readouterr().out
    assert typed[0] == prompts.SETUP_NAME_ASK.format(default=DEFAULT_NAME)  # the same question, now in Terminal
    lines = received(folder)
    assert [str(m["id"]) for m in lines if m["t"] == "screen"] == ["name"]
    # the window says where setup went, and that it is done: never "Setup ended in Terminal." while setup goes on
    assert lines[-2:] == [{"t": "terminal", "why": prompts.SETUP_WINDOW_HANDED}, {"t": "done", "lines": []}]
    assert saved()["name"] == "Maya"


def test_the_window_shows_setups_last_words_as_its_closing_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup's last step goes to the window as its closing list (`done`), not only as lines of its log: the page shows
    it, never "Setup ended in Terminal." after a setup that worked."""
    folder = a_window(tmp_path, monkeypatch)
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    wizard.closing()
    done = [m for m in received(folder) if m["t"] == "done"]
    assert done == [
        {"t": "done", "lines": [prompts.SETUP_CLOSING_COMMANDS, prompts.SETUP_DONE.format(name=DEFAULT_NAME).strip()]}
    ]


def test_stop_in_the_window_stops_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    a_window(tmp_path, monkeypatch, {"stop": True})
    stop_at_ollama(monkeypatch)
    assert main(["--config", str(tmp_path / "config.toml")]) == 130
    assert capsys.readouterr().out.strip().splitlines()[-1] == prompts.SETUP_STOPPED


def test_a_secret_goes_only_from_the_window_to_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = "731904"
    folder = a_window(tmp_path, monkeypatch, {"check": code + "x"}, {"answer": code})
    wizard = Wizard(
        tmp_path / "config.toml",
        dry_run=False,
        answers=setup_answers.Answers.load(setup_answers.default_path(), say=print),
    )
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    assert wizard.answer("sms_code") == code
    sent = (folder / "received.jsonl").read_text()
    assert code not in sent  # never sent back: no screen, no checked, no progress holds it
    assert all(code not in json.dumps(call) for call in window_calls(folder))  # nor its arguments or environment
    assert code not in capsys.readouterr().out
    assert not setup_answers.default_path().exists() or code not in setup_answers.default_path().read_text()


def test_the_window_gets_only_the_desks_part_of_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "not-for-the-window")
    folder = a_window(tmp_path, monkeypatch)
    assert setup_window.face(argparse.Namespace(), Mac(), Wizard(tmp_path / "c.toml", dry_run=False)) is not None
    env = window_calls(folder)[0]["env"]
    assert isinstance(env, dict) and "OLLAMA_API_KEY" not in env
    assert set(env) - {"FAKE_WINDOW_DIR", "PWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING"} <= set(
        setup_window.WINDOW_ENV
    )
    argv = window_calls(folder)[0]["argv"]
    assert isinstance(argv, list) and argv[:4] == ["--page", str(tmp_path / "page"), "--manifest", "a" * 64]


def test_a_window_that_never_opens_leaves_setup_in_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(setup_window, "READY_SECONDS", 0.5)
    a_window(tmp_path, monkeypatch, silent=True)
    assert setup_window.face(argparse.Namespace(), Mac(), Wizard(tmp_path / "c.toml", dry_run=False)) is None
    out = capsys.readouterr().out
    # stops-reruns-window-ready-silence (round 1): 20 silent seconds before; the wait is said first
    assert prompts.SETUP_WINDOW_OPENING == "Opening the setup window (up to 20 seconds)."
    assert out.index(prompts.SETUP_WINDOW_OPENING) < out.index(prompts.SETUP_WINDOW_NOT_OPENED)


def test_change_beside_a_kept_answer_asks_it_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    answers.set("name", "Maya")
    answers.set("language", "nl")
    folder = a_window(tmp_path, monkeypatch, {"answer": ["name"]}, {"answer": "Melina"})
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    kept = next(m for m in received(folder) if m["t"] == "screen" and m["kind"] == "kept")
    options = kept["options"]
    assert isinstance(options, list)
    assert [(o["id"], o["label"], o["line"]) for o in options] == [
        ("name", "Name", "Maya"),
        ("language", "Language", "Nederlands, Dutch"),
    ]
    assert kept["title"] == prompts.SETUP_WINDOW_KEPT
    assert screens(folder)[:2] == ["kept", "name"] and "language" not in screens(folder)
    assert saved()["name"] == "Melina" and saved()["language"] == "nl"


def test_the_window_shows_each_line_and_says_nothing_of_the_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every line goes to Terminal and to the window's log; a step's own prompt is a screen of the window, never
    typed in Terminal (owner, 29 Oct); nothing hands a step to Terminal, and no screen names the Mac password."""
    folder = a_window(tmp_path, monkeypatch, {"answer": "yes"}, {"answer": "the answer"})
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    calls = Question("calls", "cards", "Calls", ask="Set up calls? (Enter: yes)", options=(Option("yes", "Calls"),))
    face.show(calls, None)
    assert face.choose(calls, None) == "yes"
    monkeypatch.setattr("builtins.input", refuse)  # never Terminal
    assert face.line("Look again? (Enter: look again; q: stop)") == "the answer"
    face.say("✓ Ollama 0.33.1")
    face.done(["Her voices: jane, eve, vera, af_heart; alba, michael."])
    assert "✓ Ollama 0.33.1" in capsys.readouterr().out  # every line in Terminal too
    lines = received(folder)
    shown = [m for m in lines if m["t"] == "screen"]
    assert shown[0]["note"] == "" and shown[0]["ask"] == "Set up calls?"
    assert shown[1]["kind"] == "text" and shown[1]["ask"] == "Look again?"  # Terminal's typing hint left out
    assert not [m for m in lines if m["t"] == "terminal"]
    assert not any("password" in json.dumps(m).lower() for m in shown)
    assert lines[-2:] == [
        {"t": "progress", "line": "✓ Ollama 0.33.1"},
        {"t": "done", "lines": ["Her voices: jane, eve, vera, af_heart; alba, michael."]},
    ]


@pytest.mark.parametrize(
    ("kind", "ask", "shown"),
    [
        ("choice", "Which style? Recommendation: hearth. (Enter keeps hearth)", "Which style? Recommendation: hearth."),
        ("pin", "The same PIN again (hidden: nothing shows while you type): ", "The same PIN again"),
        ("number", "Her number, with the country code (like +306900000000):", "Her number, with the country code"),
        ("yes_no", "Install it now? Recommendation: yes.", "Install it now? Recommendation: yes."),
        ("wait", "Press Enter once your phone shows it as sent (setup then looks for 20 seconds).", ""),
    ],
)
def test_the_window_shows_a_questions_line_without_terminals_typing_hints(kind: str, ask: str, shown: str) -> None:
    """Its last bracketed hint (Enter, hidden, q, an example) is Terminal's; a wait needs no line: the window waits."""
    wizard = Wizard(Path("/x/c.toml"), dry_run=True)
    face = setup_window.WindowFace(setup_window.Window.__new__(setup_window.Window), wizard, lambda: wizard.face)
    keep = "never" if kind in ("pin", "hidden", "code", "key") else "step"
    assert face.screen(Question("one", kind, "One", ask=ask, keep=keep))["ask"] == shown  # type: ignore[arg-type]


def test_a_command_setup_runs_is_a_line_of_the_windows_log_never_a_hand_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every command setup runs is printed first ("  $ …"), in Terminal and in the window's log; the step runs in the
    window (owner, 29 Oct), so no line sends the window to Terminal any more."""
    folder = a_window(tmp_path, monkeypatch, {"answer": "hearth"})
    face = setup_window.face(argparse.Namespace(), Mac(), Wizard(tmp_path / "c.toml", dry_run=False))
    assert face is not None
    face.say("  $ /usr/bin/sudo /usr/local/estia/bin/estia doctor")
    face.say("✓ Doctor")
    style = Question("style", "choice", "Style", options=(Option("hearth", "hearth"),))
    face.show(style, None)
    assert face.choose(style, None) == "hearth"
    face.say("  $ scripts/migrate-to-own-user.sh")
    sent = [(m["t"], m.get("line", m.get("id", ""))) for m in received(folder) if m["t"] != "hello"]
    assert sent == [
        ("progress", "  $ /usr/bin/sudo /usr/local/estia/bin/estia doctor"),
        ("progress", "✓ Doctor"),
        ("screen", "style"),
        ("progress", "  $ scripts/migrate-to-own-user.sh"),
    ]


NUMBERLESS = "99999999-8888-4777-8666-555555555555"  # her Account ID (made up)
STORED_KEY = "abcdefghijklmnopqrstuvwxyz012345" * 2  # her Recovery key as Signal stores it (the fake's default)
CARD_KEY = " ".join(("ABCDEFGHIJKLMN#PQRSTUVWXYZ=12345" * 2)[at : at + 4] for at in range(0, 64, 4))  # as typed


def numberless_in_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *script: dict[str, object]) -> Path:
    """The window at the desk and the fake signal-cli; her username on its own screen (each script ends with it), the
    keychain copy taken as kept, Terminal never asked. The window's folder."""
    from local_assistant import setup_keychain
    from tests.setup_helpers import fake_signal_cli

    folder = a_window(tmp_path, monkeypatch, *script)
    _program, signal = fake_signal_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(setup_keychain, "save", lambda *args, **kwargs: setup_keychain.Saved.KEPT)
    monkeypatch.setattr("builtins.input", refuse)
    monkeypatch.setattr("local_assistant.setup.flush_typed", lambda tty=None: None)
    (folder / "signal").symlink_to(signal)
    return folder


def test_an_account_without_a_number_is_typed_on_the_windows_own_cards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review SEC-12: each card's screen comes before setup waits for it, laid out as Signal's (the Account ID's
    8-4-4-4-12, joined by dashes; the key in 16 groups of 4). A key Signal refuses brings both cards back, each with
    the reason (the page keeps every group typed); nothing waits on the other, and the key never comes back."""
    from tests.setup_helpers import rpc_requests

    other = " ".join(["ZZZZ"] * 16)  # a well-formed key that is not hers
    folder = numberless_in_the_window(
        tmp_path,
        monkeypatch,
        {"answer": NUMBERLESS.upper()},
        {"answer": other},
        {"answer": NUMBERLESS.upper()},
        {"skip": True},  # the reason the Account ID's card came back with
        {"answer": CARD_KEY},
        {"skip": True},
        {"answer": "estia.42"},  # her username, on its own screen
    )
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.signal_cli = str(tmp_path / "signal-cli")
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    assert wizard.numberless() == NUMBERLESS
    registers = [r["params"] for r in rpc_requests(folder / "signal") if r["method"] == "register"]
    assert [params["recoveryKey"] for params in registers] == ["z" * 64, STORED_KEY]  # type: ignore[index]
    lines = received(folder)
    shown = [m for m in lines if m["t"] == "screen"]
    assert [m["id"] for m in shown] == ["account_id", "recovery_key", "account_id", "recovery_key", "username"]
    layouts = {m["id"]: m["layout"] for m in shown if m["kind"] == "key"}
    assert layouts["account_id"] == {
        "rows": 1, "columns": 8, "group": 4, "label": "Account ID", "visible": True, "sizes": [8, 4, 4, 4, 12],
        "joiner": "-",
    }  # fmt: skip
    assert layouts["recovery_key"]["sizes"] == [4] * 16  # type: ignore[index]
    refused = [(m["id"], m["message"]) for m in lines if m["t"] == "checked" and not m["ok"]]
    assert refused == [("account_id", prompts.SETUP_CARD_REFUSED), ("recovery_key", prompts.SETUP_CARD_REFUSED)]
    sent = "\n".join(json.dumps(m) for m in lines)
    assert STORED_KEY not in sent and "ZZZZ" not in sent and CARD_KEY not in sent


def test_each_key_card_leaves_for_the_log_once_answered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review S04: the cards got no word after Continue, so the page held them, all 64 characters of the key readable,
    through register (up to 120 s), the keychain's save (its macOS box over the card) and updateProfile, against the
    card's "This screen is cleared when you leave it". Each card's answer gets its ok before signal-cli has it; the
    ok carries nothing of it."""
    folder = numberless_in_the_window(
        tmp_path, monkeypatch, {"answer": NUMBERLESS.upper()}, {"answer": CARD_KEY}, {"answer": "estia.42"}
    )
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.signal_cli = str(tmp_path / "signal-cli")
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    assert wizard.numberless() == NUMBERLESS
    lines = received(folder)

    def at(found: object) -> int:
        return next(i for i, m in enumerate(lines) if found(m))  # type: ignore[operator]

    for qid in ("account_id", "recovery_key"):
        shown = at(lambda m, qid=qid: m["t"] == "screen" and m["id"] == qid)
        ok = at(lambda m, qid=qid: m["t"] == "checked" and m["id"] == qid and m["ok"])
        assert lines[ok] == {"t": "checked", "id": qid, "ok": True, "message": "", "shown": "", "live": False}
        register = at(lambda m: m["t"] == "progress" and "register <Account ID> with <Recovery key>" in m["line"])
        assert shown < ok < register, qid


def test_after_signals_limit_the_window_names_its_time_and_the_cards_no_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """c-13 (confirm-2): after Signal's limit the window's screen named no time and recommended Try again, and the
    cards came back with "Signal refused it. Compare each group with your phone." (the key was right; each try is one
    more registration). The screen names Signal's time and recommends Stop here; the cards name the limit."""
    from local_assistant import setup_signal

    monkeypatch.setenv("FAKE_SIGNAL_REGISTER_LIMIT", "1")
    folder = numberless_in_the_window(
        tmp_path,
        monkeypatch,
        {"answer": NUMBERLESS.upper()},
        {"answer": CARD_KEY},
        {"answer": True},  # Try again, on the limit's screen
        {"answer": NUMBERLESS.upper()},
        {"skip": True},
        {"answer": CARD_KEY},
        {"skip": True},
        {"answer": False},  # Stop here
    )
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.signal_cli = str(tmp_path / "signal-cli")
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    with pytest.raises(SystemExit) as stopped:
        wizard.numberless()
    assert str(stopped.value) == prompts.SETUP_LOGIN_STOPPED
    lines = received(folder)
    when = setup_signal.local_times("2026-10-25T09:40Z")
    limits = [m for m in lines if m["t"] == "screen" and m["id"] == "login_again"]
    assert len(limits) == 2 and all(m["recommended"] == "no" for m in limits)
    assert all(f"until {when}, then try again" in str(m["lead"]) for m in limits)
    # review S07: Stop here keeps the plan's answers, never the Account ID and the Recovery key (SETUP_LOGIN_STOPPED)
    assert all("keeps every answer" not in str(m["lead"]) for m in limits)
    assert all(LOGIN_STOP_KEEPS in " ".join(plain_text(str(m["lead"])).split()) for m in limits)
    said = [m["message"] for m in lines if m["t"] == "checked" and not m["ok"]]
    assert said == [prompts.SETUP_CARD_LIMITED_UNTIL.format(when=when)] * 2


LOGIN_STOP_KEEPS = "Stop here keeps your other answers; the Account ID and the Recovery key are typed again."


def test_signals_limit_without_a_time_names_none_on_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review S09 (dialogue D098-12): Signal's limit came with no time, and the screen said to wait "until the time
    Signal names", which it never showed (Signal's words went to the log only); Try again is one more registration.
    It says Signal named none and to run setup again later, and what Stop here keeps."""
    monkeypatch.setenv("FAKE_SIGNAL_REGISTER_LIMIT", "notime")
    folder = numberless_in_the_window(
        tmp_path, monkeypatch, {"answer": NUMBERLESS.upper()}, {"answer": CARD_KEY}, {"answer": False}
    )
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.signal_cli = str(tmp_path / "signal-cli")
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    with pytest.raises(SystemExit) as stopped:
        wizard.numberless()
    assert str(stopped.value) == prompts.SETUP_LOGIN_STOPPED
    [limit] = [m for m in received(folder) if m["t"] == "screen" and m["id"] == "login_again"]
    lead = " ".join(plain_text(str(limit["lead"])).split())
    assert "the time Signal names" not in lead and "until" not in lead and "{" not in lead
    assert lead == f"Signal named no time. Each try is one more registration: run setup again later. {LOGIN_STOP_KEEPS}"
    assert limit["recommended"] == "no"


def test_a_link_code_or_pin_signal_refuses_comes_back_with_why_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review setup-window-step-refusal-lost: a captcha link, an SMS code or an old PIN Signal refuses brings its
    screen back with the reason on it (before, only a line in the window's log, which the next screen hides: a
    captcha link works only once, so pasting it again looped). The window's words name no q. A wrong code is
    signal-cli 0.14.9's -3, as a network failure is: Signal's refusal, never "no answer" (fix review, S05)."""
    from tests.fakes.fake_signal_cli import WRONG_CODE
    from tests.setup_helpers import fake_signal_cli

    folder = a_window(
        tmp_path,
        monkeypatch,
        {"answer": "signalcaptcha://wrong-token-1234"},
        {"skip": True},  # the screen again: the reason comes next
        {"answer": "signalcaptcha://right-token"},
        {"answer": "222222"},
        {"skip": True},
        {"answer": "111111"},
        {"answer": "1234"},
        {"skip": True},
        {"answer": "9876"},
    )
    program, _signal = fake_signal_cli(tmp_path, monkeypatch, FAKE_SIGNAL_CAPTCHA="right-token", FAKE_SIGNAL_PIN="9876")
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.signal_cli = program
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    wizard.register_number(BOT)
    wizard.verify_number(BOT)
    lines = received(folder)
    assert [m["id"] for m in lines if m["t"] == "screen"] == [
        "captcha", "captcha", "sms_code", "sms_code", "old_pin", "old_pin",
    ]  # fmt: skip
    refused = [(m["id"], m["message"]) for m in lines if m["t"] == "checked" and not m["ok"]]
    assert refused == [
        ("captcha", prompts.SETUP_CAPTCHA_REFUSED),
        ("sms_code", prompts.SETUP_WINDOW_CODE_FAILED.format(reason=WRONG_CODE)),  # 0.14.9's -3: Signal's no
        (
            "old_pin",
            prompts.SETUP_WINDOW_OLD_PIN_FAILED.format(reason="Verification failed! This number is locked with a pin."),
        ),
    ]
    assert all(" q " not in str(message) for _id, message in refused)
    sent = "\n".join(json.dumps(m) for m in lines)
    assert "wrong-token-1234" not in sent and "222222" not in sent and "1234" not in sent.replace(BOT, "")


def test_no_answer_at_the_sms_code_comes_back_naming_the_network_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review S05: the window's words for it, without Terminal's q."""
    from tests.setup_helpers import fake_signal_cli

    folder = a_window(tmp_path, monkeypatch, {"answer": "111111"}, {"skip": True}, {"stop": True})
    program, _signal = fake_signal_cli(tmp_path, monkeypatch, FAKE_SIGNAL_VERIFY_ERROR="-3")
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.signal_cli = program
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    with pytest.raises(KeyboardInterrupt):  # Stop here on the code's screen
        wizard.verify_number(BOT)
    refused = [(m["id"], m["message"]) for m in received(folder) if m["t"] == "checked" and not m["ok"]]
    reason = "Verify error: java.net.SocketException: Network is unreachable"
    assert refused == [("sms_code", prompts.SETUP_WINDOW_CODE_NO_ANSWER.format(reason=reason))]
    message = refused[0][1]
    assert isinstance(message, str) and " q " not in f" {message} " and "Check the code" not in message


@pytest.mark.parametrize(
    ("qid", "line"),
    [
        ("old_pin", "SETUP_OLD_PIN_STOP"),
        ("account_id", "SETUP_CARDS_STOP"),
        ("recovery_key", "SETUP_CARDS_STOP"),
        ("totp", "SETUP_TOTP_STOP"),
        ("username", "SETUP_USERNAME_STOP"),  # review S01: one Signal will not take, for a reason not "taken"
    ],
)
def test_each_signal_question_without_an_answer_at_hand_has_stop_here_in_the_window(
    qid: str, line: str, tmp_path: Path
) -> None:
    """Second deep scan (dialogue-window-no-way-out-old-pin-and-cards): an old PIN the friend may not know (each wrong
    guess uses one of Signal's tries), the Account ID and the Recovery key not at hand, no six-digit code: Terminal
    offers q at each, and the window had only Continue. Its Stop here comes with one line on what a run again asks."""
    _wizard, face, _sent = a_face(tmp_path)
    q = setup_plan.question(qid).at(Answers.memory(), Mac(), {"name": "Estia", "reason": "a TOTP token is required"})
    screen = face.screen(q)
    assert screen["stop"] == getattr(prompts, line), qid
    assert " q " not in f" {screen['stop']} " and "Enter" not in str(screen["stop"])  # the window's own words
    setup_window.encode(screen)  # one of the window's messages
    assert "q stops" in q.ask  # Terminal keeps its q


def test_stop_here_at_the_old_pin_ends_setup_in_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A number with an old Registration Lock: Stop here at its PIN ends setup as q does in Terminal, no guess made,
    with the lock's ways out (review S02: it ended as a Ctrl-C, "running setup again goes on", and a run again kept
    the number, spent a captcha and an SMS code and met the same lock). Its words are a fixed line, so an AI-run's
    line carries them too, and its window way names the Kept screen's own rows."""
    from local_assistant import setup_ai
    from tests.setup_helpers import fake_signal_cli, rpc_requests

    folder = a_window(
        tmp_path, monkeypatch, {"answer": "signalcaptcha://right-token"}, {"answer": "111111"}, {"stop": True}
    )
    program, signal = fake_signal_cli(tmp_path, monkeypatch, FAKE_SIGNAL_CAPTCHA="right-token", FAKE_SIGNAL_PIN="9876")
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.signal_cli = program
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    wizard.register_number(BOT)
    with pytest.raises(SystemExit) as stopped:
        wizard.verify_number(BOT)
    words = str(stopped.value)
    assert words == prompts.SETUP_WINDOW_OLD_PIN_STOPPED
    shown = " ".join(words.split())
    assert "scripts/setup.sh --redo number" in shown and "scripts/setup.sh --redo signal" in shown
    assert "Change at Her number" in shown and "Change at Her Signal account" in shown
    assert setup_plan.question("number").title == "Her number"  # the Kept screen's rows, by their titles
    assert setup_plan.question("route").title == "Her Signal account"
    assert setup_ai.fixed(words) == "SETUP_WINDOW_OLD_PIN_STOPPED"  # the AI's line: these words, never only "again"
    old_pin = next(m for m in received(folder) if m["t"] == "screen" and m["id"] == "old_pin")
    assert old_pin["stop"] == prompts.SETUP_OLD_PIN_STOP
    assert not any("pin" in r["params"] for r in rpc_requests(signal) if r["method"] == "verify")  # type: ignore[operator]


@pytest.mark.parametrize(
    ("script", "stopped_at"),
    [
        ([{"stop": True}], "account_id"),
        ([{"answer": NUMBERLESS.upper()}, {"stop": True}], "recovery_key"),
        ([{"answer": NUMBERLESS.upper()}, {"answer": CARD_KEY}, {"stop": True}], "totp"),
    ],
    ids=["account-id", "recovery-key", "six-digit-code"],
)
def test_stop_here_at_the_key_cards_and_the_code_ends_setup_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: list[dict[str, object]], stopped_at: str
) -> None:
    folder = numberless_in_the_window(tmp_path, monkeypatch, *script)
    monkeypatch.setenv("FAKE_SIGNAL_TOTP", "123456")
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.signal_cli = str(tmp_path / "signal-cli")
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    with pytest.raises(KeyboardInterrupt):
        wizard.numberless()
    shown = [m for m in received(folder) if m["t"] == "screen"]
    assert shown[-1]["id"] == stopped_at and shown[-1]["stop"]


def test_a_username_signal_will_not_take_comes_back_with_why_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review S01: updateAccount failing for a reason other than "taken" (no answer within 120 s, -3, -5) was said
    only in the window's log, and setup asked again on the screen the page still held: every button off, no Stop
    here, closing the only way out (an AI-run then stopped, and a run again met the same screen). Its screen gets the
    reason as a refusal, and the next name typed there goes to Signal."""
    from tests.setup_helpers import rpc_requests

    monkeypatch.setenv("FAKE_SIGNAL_USERNAME_ERROR", "estia.42")
    folder = numberless_in_the_window(
        tmp_path,
        monkeypatch,
        {"answer": NUMBERLESS.upper()},
        {"answer": CARD_KEY},
        {"answer": "estia.42"},  # Signal does not answer for it
        {"answer": "estia.43"},  # typed on the same screen, after the reason
    )
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.signal_cli = str(tmp_path / "signal-cli")
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert face is not None
    wizard.face = face
    assert wizard.numberless() == NUMBERLESS and wizard.username == "estia.43"
    names = [r["params"]["username"] for r in rpc_requests(folder / "signal") if r["method"] == "updateAccount"]  # type: ignore[index]
    assert names == ["estia.42", "estia.43"]
    lines = received(folder)
    refused = [m["message"] for m in lines if m["t"] == "checked" and m["id"] == "username" and not m["ok"]]
    reason = "Failed to set username: java.net.SocketTimeoutException: timeout"
    assert refused == [prompts.SETUP_WINDOW_USERNAME_FAILED.format(reason=reason)]
    assert " q " not in f" {refused[0]} "  # the window's own words: it has Stop here
    [screen] = [m for m in lines if m["t"] == "screen" and m["id"] == "username"]
    assert screen["stop"] == prompts.SETUP_USERNAME_STOP


def test_after_the_window_closes_the_next_card_is_gums(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review PROM-01: once the window closed, the face that asks is Terminal's (gum's here): the next card opens on
    gum's key card, never a hidden read meant for plain lines."""
    from local_assistant import setup_keycard
    from local_assistant.setup_face import GumFace

    numberless_in_the_window(tmp_path, monkeypatch, {"answer": NUMBERLESS.upper()}, {"close": True})
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.signal_cli = str(tmp_path / "signal-cli")
    opened: list[object] = []

    def run_card(card: object, **kwargs: object) -> list[str] | None:
        opened.append(card)
        return [STORED_KEY[at : at + 4] for at in range(0, 64, 4)]

    monkeypatch.setattr(setup_keycard, "run_card", run_card)
    monkeypatch.setattr(setup_keycard, "fits", lambda tty=None: True)
    face = setup_window.face(argparse.Namespace(), Mac(), wizard)
    assert isinstance(face, setup_window.WindowFace)
    face.fallback = lambda: GumFace(wizard.readers(), Path("/nonexistent/gum"))
    wizard.face = face
    monkeypatch.setattr("builtins.input", lambda prompt="": "estia.42")  # her username, in Terminal now
    assert wizard.numberless() == NUMBERLESS
    assert face.asking == "gum" and opened == [setup_keycard.RECOVERY_CARD]


def test_a_secret_never_comes_back_even_when_a_check_repeats_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth: a check's words that hold the secret typed (four characters or more) never reach the window."""

    def echo(value: object, answers: object, mac: object) -> Checked:
        text = value if isinstance(value, str) else ""
        return Checked(text, shown=f"Kept {text}.") if len(text) >= 6 else Checked(None, f"{text} is too short.")

    folder = a_window(tmp_path, monkeypatch, {"check": "1234"}, {"answer": "1234"}, {"answer": "987654"})
    face = setup_window.face(argparse.Namespace(), Mac(), Wizard(tmp_path / "c.toml", dry_run=False))
    assert face is not None
    pin = Question("lock_pin", "pin", "New PIN", check=echo, keep="never", plan=False)
    face.show(pin, None)
    assert face.hidden(pin, "") == "1234"
    face.checked(pin, echo("1234", None, None))
    assert face.hidden(pin, "") == "987654"
    face.checked(pin, echo("987654", None, None))
    lines = received(folder)
    sent = "\n".join(json.dumps(m) for m in lines)
    assert "1234" not in sent and "987654" not in sent
    checked = [(m["ok"], m["message"], m["shown"], m["live"]) for m in lines if m["t"] == "checked"]
    assert checked == [(False, "", "", True), (False, "", "", False), (True, "", "", False)]


def test_a_downloads_dots_reach_the_window_and_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner, 27 Oct: the five dots in Terminal and in the window; the page draws a dots line in the place of the one
    before it of the same download (page.js, `DOTS_LINE`)."""
    folder = a_window(tmp_path, monkeypatch)
    face = setup_window.face(argparse.Namespace(), Mac(), Wizard(tmp_path / "config.toml", dry_run=False))
    assert isinstance(face, setup_window.WindowFace)
    face.progress("  her model ○○○○○")
    face.progress("  her model ●○○○○")
    face.progress_done()
    lines = [m["line"] for m in received(folder) if m["t"] == "progress"]
    assert lines[-2:] == ["  her model ○○○○○", "  her model ●○○○○"]
    assert "  her model ●○○○○\n" in capsys.readouterr().out
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    assert "const DOTS_LINE = /^\\s*(.+?) [○●]{5}(?: .*)?$/;" in script


# ------------------------------------------------------------------ the walk-through's findings (27 Oct)


class Sent:
    """A window that takes every line setup sends; it gives the lines in `incoming`, in turn, when setup reads."""

    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.incoming: list[dict[str, object]] = []
        self.gone = False

    def send(self, message: dict[str, object]) -> bool:
        self.messages.append(dict(message))
        return True

    def receive(self, seconds: float | None = None) -> dict[str, object] | None:
        return self.incoming.pop(0) if self.incoming else None

    @property
    def ended(self) -> bool:
        return self.gone and not self.incoming

    def close(self) -> None:
        self.gone = True


def a_face(tmp_path: Path) -> tuple[Wizard, setup_window.WindowFace, Sent]:
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    sent = Sent()
    face = setup_window.WindowFace(sent, wizard, lambda: wizard.face)  # type: ignore[arg-type]
    wizard.face = face
    return wizard, face, sent


def test_the_one_time_word_stays_on_screen_while_setup_looks_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Walk-through (a): setup prints the receive command, which asks nothing in Terminal; the word's screen stays
    (no "Continue in Terminal") until the word came."""
    from tests.setup_helpers import ACI, envelope

    wizard, _face, sent = a_face(tmp_path)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        shown = next(m for m in sent.messages if m["t"] == "screen" and m["id"] == "word")
        word = str(shown["options"][0]["id"])  # type: ignore[index]
        return subprocess.CompletedProcess(command, 0, envelope("+306900000001", "Alex", word, uuid=ACI), "")

    monkeypatch.setattr(subprocess, "run", run)
    assert wizard.owner(BOT) == ACI
    after = sent.messages[[m.get("id") for m in sent.messages].index("word") :]
    assert not any(m["t"] == "terminal" for m in after), after
    assert any(m["t"] == "progress" and "receive" in str(m["line"]) for m in after)  # the command still shown


def looking_for_the_word(wizard: Wizard, sent: Sent, monkeypatch: pytest.MonkeyPatch, arrivals: list[bool]) -> None:
    """Look number i brings the word shown on the window's screen when arrivals[i]; 20 seconds pass with each look,
    and nothing is typed in Terminal."""
    from tests.setup_helpers import ACI, envelope

    looks = iter(arrivals)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        shown = next(m for m in sent.messages if m["t"] == "screen" and m["id"] == "word")
        word = str(shown["options"][0]["id"])  # type: ignore[index]
        found = envelope("+306900000001", "Alex", word, uuid=ACI) if next(looks) else ""
        return subprocess.CompletedProcess(command, 0, found, "")

    seconds = iter(range(0, 100_000, 20))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(wizard, "word_clock", lambda: float(next(seconds)))
    monkeypatch.setattr(wizard, "word_stopped", lambda: False)


@pytest.mark.parametrize("numberless", [False, True], ids=["number", "without-number"])
def test_the_one_time_words_reasons_come_on_its_screen_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], numberless: bool
) -> None:
    """Second deep scan (dialogue-window-word-reasons-lost): after a minute without the word, the usual reasons went
    only to Terminal, behind the window, and the word's screen had no button at all. They come on the word's screen,
    in the window's words (no "q, then Enter"), with Stop here; Terminal keeps its own lines."""
    from tests.setup_helpers import ACI

    wizard, _face, sent = a_face(tmp_path)
    wizard.username = "estia.42"
    looking_for_the_word(wizard, sent, monkeypatch, [False, False, False, True])
    account = NUMBERLESS if numberless else BOT
    assert wizard.owner(account) == ACI
    shown = [m for m in sent.messages if m["t"] == "screen" and m["id"] == "word"]
    assert len(shown) == 2 and shown[0]["help"] == ""  # the reasons only after a minute
    word = str(shown[0]["options"][0]["id"])  # type: ignore[index]
    words = {"word": word, "number": account, "name": wizard.name, "username": "estia.42"}
    window = prompts.SETUP_WINDOW_WORD_MISSING_USERNAME if numberless else prompts.SETUP_WINDOW_WORD_MISSING
    assert shown[1]["help"] == window.format(**words)
    assert all(m["stop"] == prompts.SETUP_WORD_STOP for m in shown)
    assert "q, then Enter" not in json.dumps(shown) and NUMBERLESS not in json.dumps(shown)
    assert shown[1]["options"] == shown[0]["options"]  # the same word, large, with Copy
    terminal = prompts.SETUP_WORD_MISSING_USERNAME if numberless else prompts.SETUP_WORD_MISSING
    assert terminal.format(**words) in capsys.readouterr().out  # Terminal's, with its q
    assert window == terminal.replace("\n  Setup keeps looking (to stop: q, then Enter).", "")  # the same reasons
    # round 2 (words-17, r2-ai-run-09): the window's log says how setup looks without Terminal's q (Stop here)
    log = [str(m["line"]) for m in sent.messages if m["t"] == "progress"]
    assert prompts.SETUP_WINDOW_WORD_ASK.format(seconds=20) in log
    assert prompts.SETUP_WORD_ASK.format(seconds=20) not in log


def test_stop_here_while_setup_looks_for_the_word_ends_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The window's Stop here on the word's screen ends setup between two looks, as q and Enter do in Terminal."""
    wizard, _face, sent = a_face(tmp_path)
    looking_for_the_word(wizard, sent, monkeypatch, [False, True])
    sent.incoming.append({"t": "stop"})
    with pytest.raises(KeyboardInterrupt):
        wizard.owner(BOT)


def test_a_window_closed_while_setup_looks_for_the_word_hands_the_wait_to_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant.setup_face import PlainFace
    from tests.setup_helpers import ACI

    class ClosedOnTheWordsScreen(Sent):
        """Closed once the word's screen shows (before it, setup reads a close first: review W21)."""

        def send(self, message: dict[str, object]) -> bool:
            if message["t"] == "screen" and message["id"] == "word":
                self.incoming.append({"t": "closed"})
            return super().send(message)

    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    sent = ClosedOnTheWordsScreen()
    face = setup_window.WindowFace(sent, wizard, lambda: PlainFace(wizard.readers()))  # type: ignore[arg-type]
    wizard.face = face
    looking_for_the_word(wizard, sent, monkeypatch, [False, False, False, False, True])
    assert wizard.owner(BOT) == ACI
    assert face.asking == "plain"
    out = capsys.readouterr().out
    assert prompts.SETUP_WINDOW_CLOSED in out and "has not arrived yet" in out  # the reasons, in Terminal
    assert len([m for m in sent.messages if m["t"] == "screen" and m["id"] == "word"]) == 1


def test_the_wait_screen_draws_stop_here_when_setup_names_one() -> None:
    """The page drew no button at all on a wait (the one-time word): its Stop here now, when its screen has a stop
    line; Continue never (setup goes on by itself)."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    draw = script[script.index("function draw(") : script.index("function refresh(")]
    still = draw[draw.index("} else if (body.still) {") :]
    assert still.index("actions({ alt: stop })") < still.index("} else {")


def test_signals_own_requests_keep_the_windows_screen(tmp_path: Path) -> None:
    """register, verify, setPin and the like go to signal-cli on its input: they ask nothing; a command's line is a
    line of the log, never a hand-over to Terminal."""
    _wizard, face, sent = a_face(tmp_path)
    face.command("  $ signal-cli jsonRpc   (register +306900000000 on stdin)")
    assert [m["t"] for m in sent.messages] == ["progress"]
    face.say("  $ /usr/bin/sudo /usr/local/estia/bin/estia doctor")
    assert [m["t"] for m in sent.messages] == ["progress", "progress"]


def test_every_guide_the_window_shows_has_her_name_filled_in(tmp_path: Path) -> None:
    """Walk-through (b): the calls card's guide said "{name}'s voice is set up with it." word for word."""
    wizard, face, _sent = a_face(tmp_path)
    wizard.name = "Maya"
    for q in setup_plan.QUESTIONS:
        screen = face.screen(q.at(wizard.known, wizard.mac, wizard.words()))
        assert "{name}" not in json.dumps(screen, ensure_ascii=False), q.id
    calls = face.screen(setup_plan.question("calls").at(wizard.known, wizard.mac, wizard.words()))
    assert "Then the build runs on its own. Maya's voice is set up with it." in calls["options"][0]["guide"]["steps"]  # type: ignore[index]


@pytest.mark.parametrize("qid", ["home_found", "known_number", "remake", "smaller_model"])
def test_a_yes_the_question_recommends_is_the_windows_main_button(qid: str, tmp_path: Path) -> None:
    """Walk-through (c): each recommends yes, with the Recommended pill on Yes and its reason in the small line under
    it (owner, 29 Oct), so the window's main button is Yes; in Terminal Enter is that yes too ([Y/n]: the owner's
    call of 29 Oct, Enter takes the recommended answer wherever a line says it is recommended)."""
    q = setup_plan.question(qid)
    _wizard, face, _sent = a_face(tmp_path)
    assert q.recommended == "yes" and not hasattr(q, "typed_yes") and "Recommend" not in q.ask
    assert bool(q.advice) is (qid != "smaller_model")  # the owner's 12B words, 29 Oct: a plain "Yes (recommended)"
    screen = face.screen(q)
    assert screen["recommended"] == "yes"
    line = f"{q.advice[:1].upper()}{q.advice[1:]}." if q.advice else ""
    assert screen["options"][0] == {  # type: ignore[index]
        "id": "yes", "label": "Yes", "line": line, "tag": "Recommended", "on": False, "guide": None,
    }  # fmt: skip


@pytest.mark.parametrize("qid", ["ollama_key", "brave_key"])
def test_a_web_key_can_be_left_for_later_in_the_window(qid: str, tmp_path: Path) -> None:
    """Walk-through (d): Enter leaves it for later in Terminal; the window has its own button for it."""
    _wizard, face, _sent = a_face(tmp_path)
    screen = face.screen(setup_plan.question(qid))
    assert screen["later"] == prompts.SETUP_WINDOW_LATER
    setup_window.encode(screen)  # one of the window's messages
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    assert "screen.later" in script


def test_the_log_opens_on_its_newest_lines() -> None:
    """Walk-through (e): the log of a step, of the hand-over's screen and of the closing list scrolls to its newest
    line."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    for start, end in (
        ("function workView(", "function handedOver("),
        ("function handedOver(", "function finished("),
        ("function finished(", "function leaveIntro("),
    ):
        body = script[script.index(start) : script.index(end)]
        assert ".scrollTop = " in body and "scrollHeight" in body, start


def test_each_face_says_its_own_words(tmp_path: Path) -> None:
    """Walk-through (f): Terminal's keys (q stops, Enter after the last line, Enter on both) stay in Terminal; the
    window says what its own buttons and boxes do."""
    _wizard, face, _sent = a_face(tmp_path)
    words = {"name": "Estia", "nuclear": ""}
    screens = {
        qid: face.screen(setup_plan.question(qid).at(Answers.memory(), Mac(), words))
        for qid in ("captcha", "recovery_key", "home_area", "lock")
    }
    shown = {qid: f"{screen['lead']} {screen['ask']}" for qid, screen in screens.items()}
    captcha_lead = str(screens["captcha"]["lead"])
    assert "q stops" not in shown["captcha"] and "press Enter" not in shown["captcha"]
    assert prompts.SETUP_WINDOW_CAPTCHA_PASTE in captcha_lead
    assert "Enter" not in shown["recovery_key"] and "several lines" not in shown["recovery_key"]
    assert screens["recovery_key"]["ask"] == prompts.SETUP_WINDOW_KEY_ASK
    assert "Enter" not in shown["home_area"] and "q skips" not in shown["lock"]
    assert "q stops" in prompts.SETUP_CAPTCHA and "Enter" in hostsec.KEY_ASK  # Terminal keeps its own


def test_back_in_the_window_asks_the_one_before_again_its_answer_filled_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner, 27 Oct: Back, bottom-left, on every question of the plan after the first; the answers so far kept."""
    folder = a_window(
        tmp_path,
        monkeypatch,
        {"answer": "Maya"},
        {"back": True},  # at Signal on your phone
        {"answer": "Melina"},
        {"answer": True},
    )
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    shown = [m for m in received(folder) if m["t"] == "screen"]
    assert [m["id"] for m in shown][:4] == ["name", "signal_app", "name", "signal_app"]
    assert [m["back"] for m in shown][:4] == [False, True, False, True]
    assert shown[2]["kept"] == "Maya"  # the answer before, filled in
    assert saved()["name"] == "Melina"


def test_stop_here_at_her_number_ends_setup_with_every_answer_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = a_window(tmp_path, monkeypatch, {"answer": "Maya"}, {"answer": True}, {"answer": "number"}, {"stop": True})
    assert main(["--config", str(tmp_path / "config.toml")]) == 130
    number = next(m for m in received(folder) if m["t"] == "screen" and m["id"] == "number")
    assert number["stop"] == prompts.SETUP_NUMBER_STOP and number["back"] is True
    assert saved() == {"name": "Maya", "signal_app": True, "route": "number"}
    assert capsys.readouterr().out.strip().splitlines()[-1] == prompts.SETUP_STOPPED


def test_the_window_sends_no_apple_event_to_another_app() -> None:
    """Terminal comes to the front only at setup's hand-over, by an app activation (NSRunningApplication: no
    Automation permission), never a second Terminal. The window's one AppleScript (admin rights through macOS's own box,
    owner 29 Oct) is in AppleScriptElevator.swift alone and talks only to the app itself (currentProcess): never an
    Apple Event to another app (AESend, Scripting Bridge, osascript, a descriptor naming another app)."""
    sources = ROOT / "tools/setup-window/Sources"
    files = {
        path.relative_to(sources).as_posix(): path.read_text(encoding="utf-8") for path in sources.rglob("*.swift")
    }
    for name, swift in files.items():
        named = ("Descriptor(bundleIdentifier", "Descriptor(processIdentifier", "Descriptor(applicationURL")
        for event in ("AESend", "SBApplication", "osascript", "OSAScript", *named):
            assert event not in swift, (name, event)
        if name != "estia-setup/AppleScriptElevator.swift":
            assert "NSAppleScript" not in swift and "NSAppleEventDescriptor" not in swift, name
    elevator = files["estia-setup/AppleScriptElevator.swift"]
    assert "targetDescriptor: NSAppleEventDescriptor.currentProcess()" in elevator
    assert "openApplication" not in "\n".join(files.values())  # never a second Terminal
    bridge = files["estia-setup/Bridge.swift"]
    assert "func bringTerminal()" in bridge and "bringWindow" not in bridge
    info = setup_window._info().decode()
    assert "NSAppleEventsUsageDescription" not in info


# ------------------------------------------------------------------ the owner's laptop install (P6, 29 Oct)

APP = "/Applications/Ollama.app"
APP_REFUSAL = f"✗ Ollama: {prompts.OLLAMA_APP.format(state='is installed', path=APP)}\n  Fix: {prompts.OLLAMA_APP_FIX.format(path=APP)}"


def test_a_stop_at_a_step_is_the_windows_own_screen_never_setup_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner's laptop, 29 Oct: Step 1's Ollama refusal ended setup with the window open. The window got no word from
    setup, only its stdin's end, and said "Setup ended in Terminal." ("now what?"); the stop was one long Terminal line
    behind it ("we need more space, make it readable, im lost"). A stop is the window's own screen: what was found,
    why it matters, what to do next, with the step it stopped at; Terminal gets the same with blank lines."""
    folder = a_window(tmp_path, monkeypatch, {"answer": DEFAULT_NAME}, {"answer": True}, {"answer": "number"},
                      {"answer": BOT})  # fmt: skip

    def ollama(self: Wizard) -> None:
        raise SystemExit(APP_REFUSAL)  # as ollama_build.Refusal stops a first install

    monkeypatch.setattr(Wizard, "ollama", ollama)
    monkeypatch.setattr("builtins.input", refuse)  # nothing asked in Terminal
    with pytest.raises(SystemExit) as stopped:
        main(["--config", str(tmp_path / "config.toml")])
    assert stopped.value.code == 1  # said once, by the faces: never again as Python's own last line
    stop = received(folder)[-1]
    assert (stop["t"], stop["id"], stop["kind"]) == ("screen", "stop", "info")
    assert stop["title"] == f"Ollama: the Ollama app is installed ({APP})."  # its area kept (round 2, words-06)
    lead = str(stop["lead"])
    assert lead.startswith(
        "It updates itself, starts only when someone logs in on the Mac's screen, and stops any other Ollama when its "
        "own cannot start.\n\nFix: quit it"
    )
    assert lead.endswith(f"\n\n{prompts.SETUP_WINDOW_STOP_AGAIN}")
    assert prompts.SETUP_WINDOW_STOP_AGAIN.endswith("\n$ scripts/setup.sh")  # drawn with Copy: never retyped
    steps = stop["steps"]  # "Step 1 of 9" stays on the screen
    assert stop["step"] == 1 and isinstance(steps, int) and steps > 1
    out = capsys.readouterr().out
    assert f"\n\n✗ Ollama: the Ollama app is installed ({APP}).\n\n  It updates itself," in out
    assert "\n\n  Fix: quit it" in out


def test_a_stop_screen_stays_until_it_is_closed() -> None:
    """The window ended a minute after setup did (owner: "the setup window was gone"): a stop or the closing list
    stays until it is closed, and the page draws a stop's screen with its commands to copy. Setup's end without its
    last word (a Ctrl-C in Terminal, a crash) never reads as if setup were done: Terminal says why, and is in front."""
    bridge = (ROOT / "tools/setup-window/Sources/estia-setup/Bridge.swift").read_text(encoding="utf-8")
    assert "endedSeconds" not in bridge and "asyncAfter" not in bridge
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    assert 'screen.id === "stop"' in script and "function stopScreen(screen)" in script
    assert "Setup ended in Terminal." not in script
    # Terminal closed (stops-reruns-terminal-closed): the window looks like the installer, so it says what to open;
    # an AI-run has no Terminal it was started in (review W12): one line for both, naming both ways to start again
    found = re.search(r'ended: ("(?:[^"\\]|\\.)*"(?:\s*\+\s*"(?:[^"\\]|\\.)*")*),', script)
    assert found is not None
    ended = "".join(json.loads(part) for part in re.findall(r'"(?:[^"\\]|\\.)*"', found.group(1)))
    assert "in front" not in ended and "Terminal window you started" not in ended
    assert "every answer is kept" in ended and "ask your AI" in ended
    assert ended.endswith("\n$ cd ~/estia && scripts/setup.sh")  # its own line: drawn with Copy


def test_an_ai_runs_stop_screens_give_the_ended_lines_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix review (dialogue-06): an AI-run's stop screens gave the bare "$ scripts/setup.sh" with Copy, which a new
    Terminal window (it opens in the home folder) cannot run; W12 gave the window's end line one that it can. Both now
    name the same command and words: into the clone first (its real path, the home as ~), then setup."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    found = re.search(r'ended: ("(?:[^"\\]|\\.)*"(?:\s*\+\s*"(?:[^"\\]|\\.)*")*),', script)
    assert found is not None
    ended = "".join(json.loads(part) for part in re.findall(r'"(?:[^"\\]|\\.)*"', found.group(1)))
    monkeypatch.setenv("HOME", "/Users/alex")
    command = setup_window.setup_again_command(Path("/Users/alex/estia"))
    assert command == "cd ~/estia && scripts/setup.sh" and ended.endswith(f"\n$ {command}")
    stop = prompts.SETUP_WINDOW_STOP_AGAIN_AI.format(command=command)
    assert stop.endswith(ended.split("; every answer is kept. ", 1)[1])  # the same words and the same command
    assert "every answer is kept" in stop
    spaced = setup_window.setup_again_command(Path("/Users/alex/my projects/estia"))
    assert spaced == "cd ~/'my projects/estia' && scripts/setup.sh"
    assert setup_window.setup_again_command(Path("/opt/estia")) == "cd /opt/estia && scripts/setup.sh"


def test_every_command_the_window_shows_has_copy() -> None:
    """Owner's laptop, 29 Oct ("if you want me to copy the code, add this little copy icon"): a command setup shows
    for typing by hand ("$ …") is drawn with Copy, which copies it without its "$ "."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    assert 'native({ t: "copy", text: command })' in script and "line.trim().slice(2)" in script


def test_the_window_shows_the_step_and_its_lines_as_they_come() -> None:
    """Owner, 29 Oct ("can we have Terminal in the window?"): while a step runs, the window keeps "Step n of m: …"
    (setup's own line, read from its log) at the top and its lines under it as they come; a meter's line takes the
    place of the one before; Stop here stops setup (one with admin rights finishes first)."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    found = re.search(r"const STEP_LINE = /(.+)/;", script)
    assert found is not None
    line = prompts.SETUP_STEP.format(n=3, m=9, what=prompts.SETUP_STEPS["model"]).strip("\n")
    assert re.fullmatch(found.group(1).replace("\\/", "/"), line)
    body = script[script.index("function workView(") : script.index("function handedOver(")]
    assert "stepTitle()" in body and "WORDS.stopHere, run: stopHere" in body
    assert "message.again === true" in script and "const LOG_MAX = 2000;" in script


def test_what_the_window_said_before_it_ended_is_still_read() -> None:
    """A window that said Stop here, then closed: setup's next line to it fails (its stdin is gone), yet its last
    words are still read, in order (review FRIEND-4: a stop then Close went on in Terminal)."""
    lines = '{"t": "stop"}\\n{"t": "closed"}\\n'
    process = subprocess.Popen(
        ["/bin/sh", "-c", f"printf '{lines}'"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    process.wait(timeout=10)
    window = setup_window.Window(process)
    for _ in range(3):
        window.send({"t": "progress", "line": "x"})
    assert window.gone
    assert window.receive(1) == {"t": "stop"} and window.receive(1) == {"t": "closed"} and window.receive(1) is None
    window.close()
    assert process.stdout is not None
    process.stdout.close()


def test_a_stop_said_before_a_screen_ends_setup_and_sends_no_screen(tmp_path: Path) -> None:
    """Review W21: Stop here during "Waiting for Signal", then Signal's answer: the code screen was drawn over
    "Stopped", and its code typed reached nothing. A stop said while setup worked is read before a screen goes out."""
    _wizard, face, sent = a_face(tmp_path)
    sent.incoming.append({"t": "stop"})
    with pytest.raises(KeyboardInterrupt):
        face.show(Question("line", "text", "Signal's code", ask="The code:", keep="step", plan=False), None)
    assert not [m for m in sent.messages if m["t"] == "screen"]


def test_the_page_draws_no_question_over_stopped() -> None:
    """Review W21: once Stop here drew "Stopped", a question setup sent before it read the stop is dropped; a stop's
    screen and setup's closing word still draw."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    stop_here = script[script.index("function stopHere(") : script.index("function answer(")]
    assert "stoppedHere = true;" in stop_here
    receive = script[script.index("function receive(message)") : script.index("window.estia = Object.freeze")]
    assert 'if (stoppedHere && message.id !== "stop") break;' in receive


def test_the_cards_keep_their_groups_across_signals_own_screens() -> None:
    """Review W13: Signal's limit draws login_again (a yes or no) before the cards reopen, and its TOTP its own
    screen; each cleared the typed groups, so the cards reopened empty under "Every group is kept." The page keeps
    them across those two screens, and clears them at any other one, at a stop and at the end."""
    from local_assistant import setup_signal

    script = (PAGE / "page.js").read_text(encoding="utf-8")
    found = re.search(r"const KEEPS_CARDS = new Set\(\[([^\]]*)\]\);", script)
    assert found is not None
    kept = {json.loads(part) for part in re.findall(r'"[^"]*"', found.group(1))}
    assert kept == {"login_again", "totp"}
    draw = script[script.index("function draw(screen)") : script.index("function refresh()")]
    assert 'if (screen.kind !== "key" && !KEEPS_CARDS.has(screen.id)) typedCards.clear();' in draw
    assert setup_plan.question("totp").id == "totp"
    assert 'qid="login_again"' in (ROOT / "src/local_assistant/setup_signal.py").read_text(encoding="utf-8")
    for name in ("function stopScreen(", "function finished("):
        body = script[script.index(name) :]
        assert "typedCards.clear();" in body[: body.index("\n  }\n")]
    assert setup_signal.card_message(-5).endswith(prompts.SETUP_CARD_KEPT)


def test_every_close_of_the_window_meets_the_admin_guard() -> None:
    """Review W02: the page's Close called NSWindow.close(), which skips windowShouldClose's admin guard: closed
    during the Registration Lock's bootout, setup stopped inside its held section and she stayed booted out. The
    page's Close is the red button's (performClose), and the red button and Quit ask the bridge's one decision."""
    bridge = (ROOT / "tools/setup-window/Sources/estia-setup/Bridge.swift").read_text(encoding="utf-8")
    assert "case .close:\n            window?.performClose(nil)" in bridge and "window?.close()" not in bridge
    assert "func mayClose() -> Bool { core.mayClose() }" in bridge
    controller = (ROOT / "tools/setup-window/Sources/estia-setup/WindowController.swift").read_text(encoding="utf-8")
    assert controller.count("bridge?.mayClose() == false") == 2 and "admin.running" not in controller


def test_macos_box_comes_after_the_page_drew_its_line() -> None:
    """Review W09: macOS's box runs on the window's main thread, which then draws nothing until it is answered: the
    page's "In macOS's own box" line, sent just before, may never show. The box waits a moment first, so it does.
    (0.9.9: the box off the main thread.)"""
    elevator = (ROOT / "tools/setup-window/Sources/estia-setup/AppleScriptElevator.swift").read_text(encoding="utf-8")
    assert "DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { done(self.run(words, prompt)) }" in elevator
    assert "DispatchQueue.main.async {" not in elevator

"""Setup's faces: plain lines print what setup always printed, the end of input is never a yes, Ctrl-C still stops
setup with one line."""

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from local_assistant import prompts, setup_gum
from local_assistant.setup import Wizard, main
from local_assistant.setup_answers import Answers
from local_assistant.setup_face import GumFace, PlainFace, Readers, choose_face
from local_assistant.setup_plan import Mac, question
from local_assistant.setup_questions import Checked, Field, KeyLayout, Option, Question, plain_text
from tests.setup_helpers import refuse, typed_once

pytestmark = pytest.mark.usefixtures("no_real_ollama", "no_system_paths", "a_terminal")


def readers(terminal: bool = True, hidden: Callable[[str], str] | None = None) -> Readers:
    return Readers(
        hidden=hidden or (lambda prompt: "q"),
        hidden_lines=lambda prompt, needed, counted: "q",
        flush=lambda: None,
        terminal=lambda: terminal,
    )


def test_plain_lines_show_a_question_as_setup_always_did(capsys: pytest.CaptureFixture[str]) -> None:
    """The language question: a blank line, its title, its explanation, then the 11, two spaces in, a blank line
    between each (owner, 27 Oct: "in terminal, we also need some blank rows")."""
    face = PlainFace(readers())
    asked = question("language").at(Wizard(Path("/nonexistent"), dry_run=True).answers, Mac(), {"name": "Estia"})
    face.show(asked, None)
    lines = "\n".join(f"  {line}" for line in prompts.language_lines())
    lead = plain_text(prompts.SETUP_LANGUAGE.format(name="Estia"))  # its bold words: plain lines leave the marks out
    assert capsys.readouterr().out == f"\nLanguage\n\n{lead}\n\n{lines}\n"


@pytest.mark.parametrize(
    ("answer", "terminal", "said", "hint"),
    [
        ("", True, True, "[Y/n]"),
        ("y", True, True, "[Y/n]"),
        ("n", True, False, "[Y/n]"),
        (None, True, None, "[Y/n]"),  # the end of input: no answer, never a yes
        ("", False, False, "[y/N]"),  # no terminal: Enter keeps the no
    ],
    ids=["enter", "y", "n", "end-of-input", "no-terminal"],
)
def test_plain_yes_or_no_takes_enter_only_from_a_terminal(
    monkeypatch: pytest.MonkeyPatch, answer: str | None, terminal: bool, said: bool | None, hint: str
) -> None:
    asked = typed_once(monkeypatch, answer)
    q = Question("safe", "yes_no", "Safe", ask="Safe? Recommendation: yes.", recommended="yes")
    assert PlainFace(readers(terminal)).confirm(q, default=True) is said
    assert asked == [f"Safe? Recommendation: yes. {hint} "]


@pytest.mark.parametrize(
    ("typed", "said"),
    [
        (["yes"], True),
        (["No"], False),
        (["ναι"], True),  # a Greek friend's yes
        (["όχι"], False),
        (["υ"], True),  # the y key on the Greek layout
        (["ν"], False),  # and the n key
        (["ok", "y"], True),  # anything else is asked again, never taken as a no
        (["1", "maybe", "n"], False),
    ],
)
def test_plain_yes_or_no_takes_yes_and_no_and_asks_again_otherwise(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], typed: list[str], said: bool
) -> None:
    """answers-extras-yes-no-other-words: anything but a y was a no, so "ναι", "υ" (the Greek layout's y), "ok" or
    "1" skipped the move or the security step without a word."""
    answers = iter(typed)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    q = Question("safe", "yes_no", "Safe", ask="Safe?", recommended="yes")
    assert PlainFace(readers(True)).confirm(q, default=True) is said
    assert capsys.readouterr().out.count(prompts.SETUP_YES_NO_AGAIN) == len(typed) - 1
    assert prompts.SETUP_YES_NO_AGAIN == "Type y or n (the keyboard may be on Greek: switch it to ABC)."


def test_the_end_of_input_is_never_a_yes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    typed_once(monkeypatch, None)
    monkeypatch.setattr(Wizard, "terminal", lambda self: True)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    assert wizard.yes("Safe?", default=True) is False and wizard.again("Again?") is False
    assert wizard.typed("Name?") is None


def test_a_wizard_asks_and_says_everything_through_its_face(tmp_path: Path) -> None:
    class Recorded(PlainFace):
        def __init__(self) -> None:
            super().__init__(readers())
            self.lines: list[str] = []

        def say(self, text: str) -> None:
            self.lines.append(f"say {text}")

        def line(self, prompt: str, why: str = "") -> str | None:
            self.lines.append(f"ask {prompt}")
            return "q"

    face = Recorded()
    wizard = Wizard(tmp_path / "config.toml", dry_run=False, face=face)
    wizard.say("hello")
    assert wizard.again("Look again?") is False and wizard.ask("Name?", "Estia") == "q"
    assert face.lines == ["say hello", "ask Look again?", "ask Name?"]


def test_hidden_answers_come_only_from_the_wizards_own_readers() -> None:
    prompted: list[str] = []

    def hidden(prompt: str) -> str:
        prompted.append(prompt)
        return "123456"

    face = PlainFace(readers(hidden=hidden))
    q = Question(
        "code", "code", "SMS code", ask="The code (hidden: nothing shows while you type; q stops): ", keep="never"
    )
    assert face.hidden(q, "") == "123456" and prompted == [q.ask]
    assert PlainFace(readers()).hidden(q, "") is None  # q, or the end of input: stopped


def test_a_key_is_read_over_several_lines_until_all_its_characters_came() -> None:
    asked: list[tuple[str, int]] = []

    def lines(prompt: str, needed: int, counted: Callable[[str], bool]) -> str:
        asked.append((prompt, needed))
        return "abcd"

    keyed = Readers(hidden=lambda prompt: "", hidden_lines=lines, flush=lambda: None, terminal=lambda: True)
    q = Question(
        "ollama_key",
        "key",
        "Ollama key",
        ask="Ollama's key: ",
        keep="never",
        layout=KeyLayout(2, 3, 4, "Ollama key", False),
    )
    assert PlainFace(keyed).key(q, "", ()) == "abcd" and asked == [("Ollama's key: ", 24)]


def test_fields_are_one_line_each_and_enter_leaves_one_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["Ermou", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    q = Question("home_area", "fields", "Home area", fields=(Field("street", "Street"), Field("city", "City")))
    assert PlainFace(readers()).fields(q, None, "") == {"street": "Ermou", "city": ""}
    typed_once(monkeypatch, None)
    assert PlainFace(readers()).fields(q, None, "") is None


def test_a_check_says_why_it_refused_or_what_it_kept(capsys: pytest.CaptureFixture[str]) -> None:
    face, q = PlainFace(readers()), question("style")
    face.checked(q, Checked(None, '  "x" is not one of them: hearth or playful.'))
    face.checked(q, Checked("hearth", shown="✓ Style: hearth."))
    face.checked(q, Checked("hearth"))
    assert capsys.readouterr().out == '  "x" is not one of them: hearth or playful.\n✓ Style: hearth.\n'


def test_ctrl_c_at_a_question_still_stops_setup_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def ctrl_c(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", ctrl_c)
    for step in ("ollama", "model"):
        monkeypatch.setattr(Wizard, step, lambda self: None)
    assert main(["--config", str(tmp_path / "config.toml")]) == 130
    captured = capsys.readouterr()
    assert captured.out.strip().splitlines()[-1] == prompts.SETUP_STOPPED and "Traceback" not in captured.err


# ------------------------------------------------------------------ gum, pinned: its download and its menus

FAKE_GUM = Path(__file__).parent / "fakes" / "fake_gum.py"
MEMBER = f"gum_{setup_gum.VERSION}_Darwin_arm64/gum"


def release(tmp_path: Path, members: dict[str, bytes | Path]) -> Path:
    """A gum release's tarball: each member a file (bytes) or a link to a path."""
    tarball = tmp_path / "release" / setup_gum.TARBALL
    tarball.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "w:gz") as archive:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            if isinstance(body, Path):
                info.type, info.linkname = tarfile.SYMTYPE, str(body)
                archive.addfile(info)
            else:
                info.size, info.mode = len(body), 0o755
                archive.addfile(info, io.BytesIO(body))
    return tarball


def fake_curl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tarball: Path | None, code: int = 0) -> Path:
    """/usr/bin/curl under the tools' root: copies `tarball` to its -o file (or fails with `code`); each start's
    arguments in its log."""
    root = tmp_path / "tools"
    curl = root / "usr/bin/curl"
    curl.parent.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "curl.log"
    copy = f'cp {shlex.quote(str(tarball))} "$out"' if tarball is not None else ":"
    curl.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {shlex.quote(str(log))}\n'
        'while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
        f"{copy}\nexit {code}\n"
    )
    curl.chmod(0o755)
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(root))
    return log


def pinned(monkeypatch: pytest.MonkeyPatch, tarball: Path, binary: bytes) -> None:
    """The pins as the test's release has them (the real ones are gum 2.0.2's)."""
    monkeypatch.setattr(setup_gum, "TARBALL_SHA256", hashlib.sha256(tarball.read_bytes()).hexdigest())
    monkeypatch.setattr(setup_gum, "BINARY_SHA256", hashlib.sha256(binary).hexdigest())


def a_gum(tmp_path: Path) -> bytes:
    """The fake gum as a program: a real process, so its arguments are what ps would show any user of the Mac. Its
    folder (calls.jsonl, answers.json) is written into it: gum gets only the terminal's part of the environment."""
    folder = tmp_path / "gum-calls"
    folder.mkdir(exist_ok=True)
    run = f"exec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_GUM))}"
    return f'#!/bin/sh\nFAKE_GUM_DIR={shlex.quote(str(folder))} {run} "$@"\n'.encode()


def test_the_pins_are_gum_2_0_2s() -> None:
    """Read from the release's checksums.txt (26 Oct 2026) and the gum inside its checked tarball."""
    assert setup_gum.VERSION == "2.0.2" and setup_gum.TARBALL == "gum_2.0.2_Darwin_arm64.tar.gz"
    assert (
        setup_gum.URL == "https://github.com/charmbracelet/gum/releases/download/v2.0.2/gum_2.0.2_Darwin_arm64.tar.gz"
    )
    assert setup_gum.TARBALL_SHA256 == "4777a69b1170b8db23c95d5889fb32186cfda1a3ac950d339aa17e3513633890"
    assert setup_gum.BINARY_SHA256 == "74cab6235944c0e39e69edfceef81150064687dd29cacec5ec1e954637f55d0c"
    assert setup_gum.THEME == {"accent": "#ff5c35", "muted": "#8d9298"}


def test_the_notices_name_gum_and_its_licence() -> None:
    notices = (Path(__file__).resolve().parents[1] / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    row = next(line for line in notices.splitlines() if line.startswith(f"| gum {setup_gum.VERSION} "))
    assert "| MIT |" in row and "checked by its fingerprint" in row and "Homebrew's gum is never used" in row


def test_a_checked_download_puts_gum_in_setups_own_folder_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = a_gum(tmp_path)
    tarball = release(tmp_path, {f"gum_{setup_gum.VERSION}_Darwin_arm64/LICENSE": b"MIT", MEMBER: binary})
    pinned(monkeypatch, tarball, binary)
    log = fake_curl(monkeypatch, tmp_path, tarball)
    said: list[str] = []
    program = setup_gum.program(dry_run=False, say=said.append)
    assert program == setup_gum.folder() / "gum" and program.read_bytes() == binary
    assert stat.S_IMODE(program.stat().st_mode) == 0o700 and stat.S_IMODE(program.parent.stat().st_mode) == 0o700
    assert sorted(p.name for p in program.parent.iterdir()) == ["gum"]  # the LICENSE and the download are gone
    # round 1's issue 69: over SSH its download ran up to 2 minutes before the first question with no line
    assert said == [
        prompts.SETUP_GUM_DOWNLOADING.format(version=setup_gum.VERSION),
        prompts.SETUP_GUM_READY.format(version=setup_gum.VERSION),
    ]
    assert prompts.SETUP_GUM_DOWNLOADING.format(version="2.0.2") == (
        "Setup's menus: downloading gum 2.0.2 (4.7 MB) from GitHub, once (up to 2 minutes)."
    )
    args = log.read_text().split()
    assert args[:2] == ["-q", "-fsSL"] and "=https" in args and args[-1] == setup_gum.URL
    said.clear()
    assert setup_gum.program(dry_run=False, say=said.append) == program and said == []  # checked again, no download
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize("why", ["tarball", "binary", "link", "two", "none", "large", "curl"])
def test_a_download_that_does_not_check_out_installs_nothing_and_setup_goes_on_in_plain_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    binary = a_gum(tmp_path)
    members: dict[str, bytes | Path] = {MEMBER: binary}
    if why == "link":
        members = {MEMBER: Path("/bin/sh")}
    elif why == "two":
        members = {MEMBER: binary, f"./{MEMBER}": binary, "x": b""}
    elif why == "none":
        members = {"gum": binary}
    tarball = release(tmp_path, members)
    pinned(monkeypatch, tarball, binary)
    if why == "two":  # tarfile keeps both names apart: the same member twice
        with tarfile.open(tarball, "w:gz") as archive:
            for _ in range(2):
                info = tarfile.TarInfo(MEMBER)
                info.size = len(binary)
                archive.addfile(info, io.BytesIO(binary))
        pinned(monkeypatch, tarball, binary)
    if why == "tarball":
        monkeypatch.setattr(setup_gum, "TARBALL_SHA256", "0" * 64)
    elif why == "binary":
        monkeypatch.setattr(setup_gum, "BINARY_SHA256", "0" * 64)
    elif why == "large":
        monkeypatch.setattr(setup_gum, "MAX_BYTES", len(binary) - 1)
    fake_curl(monkeypatch, tmp_path, tarball, code=22 if why == "curl" else 0)
    said: list[str] = []
    assert setup_gum.program(dry_run=False, say=said.append) is None
    assert said[0] == prompts.SETUP_GUM_DOWNLOADING.format(version=setup_gum.VERSION)
    assert len(said) == 2 and said[1].startswith("Setup's menus: plain lines ("), said
    folder = setup_gum.folder()
    assert not folder.exists() or list(folder.iterdir()) == []  # no gum, no download left behind


@pytest.mark.parametrize("change", ["changed", "link", "others"])
def test_an_installed_gum_is_checked_before_every_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    binary = a_gum(tmp_path)
    tarball = release(tmp_path, {MEMBER: binary})
    pinned(monkeypatch, tarball, binary)
    log = fake_curl(monkeypatch, tmp_path, tarball)
    program = setup_gum.program(dry_run=False, say=lambda text: None)
    assert program is not None
    if change == "changed":
        program.write_bytes(binary + b"# one more line\n")
    elif change == "link":
        program.unlink()
        program.symlink_to(tmp_path / "elsewhere")
    else:
        program.chmod(0o777)
    said: list[str] = []
    assert setup_gum.program(dry_run=False, say=said.append) is None
    assert (
        len(said) == 1 and said[0].startswith("Setup's menus: plain lines (") and len(log.read_text().splitlines()) == 1
    )


def test_a_dry_run_shows_the_download_and_runs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = fake_curl(monkeypatch, tmp_path, None)
    said: list[str] = []
    assert setup_gum.program(dry_run=True, say=said.append) is None
    assert not log.exists() and not setup_gum.folder().exists()
    assert len(said) == 1 and setup_gum.URL in said[0] and "/usr/bin/curl" in said[0]


def gum_face(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *answers: dict[str, object], private: tuple[str, ...] = ()
) -> tuple[GumFace, Path]:
    program = tmp_path / "gum"
    program.write_bytes(a_gum(tmp_path))
    folder = tmp_path / "gum-calls"
    (folder / "answers.json").write_text(json.dumps(list(answers)), encoding="utf-8")
    program.chmod(0o700)
    return GumFace(readers(), program, private=lambda: private), folder


def calls(folder: Path) -> list[dict[str, object]]:
    path = folder / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_gum_picks_from_the_options_enter_takes_the_first_the_recommended_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    face, folder = gum_face(tmp_path, monkeypatch, {"out": "playful\n"})
    q = question("style").at(Answers.memory(), Mac(), {"name": "Estia"})
    assert face.choose(q, None) == "playful"
    assert face.choose(q, None) == "hearth"  # Enter: the first, tagged "Recommended"
    argv = calls(folder)[0]["argv"]
    # the recommended one labelled so in the menu (owner, 29 Oct), never a line of its own
    assert isinstance(argv, list) and argv[0] == "choose" and argv[-3:] == ["--", "hearth (recommended)", "playful"]
    assert f"--cursor.foreground={setup_gum.THEME['accent']}" in argv
    assert "--header=Which style?" in argv  # its Enter hint is the menu's own


def test_another_name_is_typed_in_gums_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    face, folder = gum_face(tmp_path, monkeypatch, {"out": "Her name\n"}, {"out": "Juno\n"})
    q = question("name").at(Answers.memory(), Mac(), {"default": "Estia"})
    assert face.choose(q, None) == "Juno"
    assert [call["argv"][0] for call in calls(folder)] == ["choose", "input"]  # type: ignore[index]


@pytest.mark.parametrize(("code", "said"), [(0, True), (1, False)])
def test_gums_yes_or_no(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, said: bool) -> None:
    face, folder = gum_face(tmp_path, monkeypatch, {"code": code})
    q = question("security").at(Answers.memory(), Mac(), {"nuclear": ""})
    assert face.confirm(q, default=True) is said
    argv = calls(folder)[0]["argv"]
    assert argv == [
        "confirm",
        "--default=true",
        "--affirmative=Yes (recommended)",
        "--negative=No",
        f"--selected.background={setup_gum.THEME['accent']}",
        prompts.SETUP_SECURITY_ASK,
    ]


def test_ctrl_c_in_gum_stops_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    face, _folder = gum_face(tmp_path, monkeypatch, {"code": 130})
    with pytest.raises(KeyboardInterrupt):
        face.confirm(question("move").at(Answers.memory(), Mac()), default=True)


def test_gum_failing_falls_back_to_the_plain_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    face, _folder = gum_face(tmp_path, monkeypatch, {"code": 2})
    asked = typed_once(monkeypatch, "playful")
    assert face.choose(question("style").at(Answers.memory(), Mac()), None) == "playful"
    assert asked == [f"{prompts.SETUP_STYLE_ASK} "]


def test_a_secret_never_goes_through_gum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hidden: list[str] = []
    face, folder = gum_face(tmp_path, monkeypatch)

    def read(prompt: str) -> str:
        hidden.append(prompt)
        return "123456"

    face.readers = readers(hidden=read)
    code = Question("sms_code", "code", "SMS code", ask="The code (hidden): ", keep="never")
    assert face.hidden(code, "") == "123456" and hidden == ["The code (hidden): "] and calls(folder) == []


def test_gums_arguments_never_hold_your_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ps shows a program's arguments to every user of the Mac: a question whose words hold an answer of yours (her
    number, an Account ID, About you, your home area) is asked in plain lines, never through gum."""
    number, aci, street = "+306900000000", "99999999-8888-4777-8666-555555555555", "Example Street"
    face, folder = gum_face(tmp_path, monkeypatch, private=(number, aci, street))
    asked = typed_once(monkeypatch, "y")
    q = Question("known_number", "yes_no", "Her number", ask=f"Use {number}? Recommendation: yes.", keep="step")
    assert face.confirm(q, default=True) is True and calls(folder) == [] and asked == [f"{q.ask} [Y/n] "]
    fields = Question(
        "home_area",
        "fields",
        "Home area",
        fields=(Field("street", "Street", placeholder="Ermou"), Field("city", "City")),
    )
    assert face.fields(fields, None, "") == {"street": "", "city": ""}  # Enter in each
    for call in calls(folder):
        text = json.dumps(call)
        assert number not in text and aci not in text and street not in text
        assert set(cast(dict[str, str], call["env"])) <= {
            "TERM",
            "COLORTERM",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "NO_COLOR",
            "FAKE_GUM_DIR",
            "__CF_USER_TEXT_ENCODING",
            "PWD",
            "SHLVL",
            "_",
        }


def test_a_number_signal_cli_knows_never_goes_into_gums_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review SEC-6: her number filled into a question from signal-cli's own list (after --redo number, a refused
    answers file, a run of 0.9.7) is an answer of yours too while it is asked: that question goes in plain lines,
    never through gum, whose arguments ps shows to every user of the Mac."""
    number = "+306900000001"
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    face, folder = gum_face(tmp_path, monkeypatch)
    face.private = wizard.private_values
    wizard.face = face
    monkeypatch.setattr(Wizard, "registered_numbers", lambda self: [number])
    monkeypatch.setattr(Wizard, "her_number", refuse)  # a no would ask for another number: never here
    asked = typed_once(monkeypatch, "y")
    assert wizard.signal_number() == number
    assert not any(number in json.dumps(call) for call in calls(folder))
    assert len(asked) == 1 and number in asked[0]
    assert number not in wizard.private_values()  # once asked, the words are no answer of yours any more


def test_the_wizard_counts_every_answer_of_yours_as_private_but_her_name_and_the_choices(tmp_path: Path) -> None:
    answers = Answers.memory(saved={"name", "language", "number", "home_area", "about_you", "extras", "move"})
    wizard = Wizard(tmp_path / "config.toml", dry_run=False, answers=answers)
    for qid, value in (
        ("name", "Maya"),
        ("language", "nl"),
        ("number", "+306900000000"),
        ("home_area", {"street": "Example Street", "city": "Athens"}),
        ("about_you", {"name": "Alex", "work": ""}),
        ("move", True),
    ):
        answers.set(qid, value)
    assert set(wizard.private_values()) == {"+306900000000", "Example Street", "Athens", "Alex"}


@pytest.mark.parametrize(
    ("why", "face"),
    [
        ("plain", "plain"),
        ("dumb", "plain"),
        ("pipe", "plain"),
        ("voiceover", "plain"),
        ("no-gum", "plain"),
        ("", "gum"),
    ],
)
def test_gum_only_in_a_terminal_without_a_screen_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: str, face: str
) -> None:
    binary = a_gum(tmp_path)
    tarball = release(tmp_path, {MEMBER: binary})
    pinned(monkeypatch, tarball, binary)
    fake_curl(monkeypatch, tmp_path, None if why == "no-gum" else tarball, code=22 if why == "no-gum" else 0)
    pgrep = tmp_path / "tools/usr/bin/pgrep"
    pgrep.write_text(f"#!/bin/sh\nexit {0 if why == 'voiceover' else 1}\n")
    pgrep.chmod(0o755)
    monkeypatch.setenv("TERM", "dumb" if why == "dumb" else "xterm-256color")
    monkeypatch.setattr(Wizard, "terminal", lambda self: why != "pipe")
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    chosen = choose_face(argparse.Namespace(plain=why == "plain"), Mac(), wizard)
    assert chosen.name == face


def test_a_setup_run_asks_its_plan_through_gum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = a_gum(tmp_path)
    tarball = release(tmp_path, {MEMBER: binary})
    pinned(monkeypatch, tarball, binary)
    fake_curl(monkeypatch, tmp_path, tarball)
    folder = tmp_path / "gum-calls"
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(Wizard, "terminal", lambda self: True)

    def ollama(self: Wizard) -> None:
        raise SystemExit(3)

    monkeypatch.setattr(Wizard, "ollama", ollama)
    # gum prints the label it showed: without its bold marks, the recommended one with "(recommended)" after it
    route = f"{plain_text(prompts.SETUP_ROUTE_NUMBER)} (recommended, {prompts.SETUP_ACCOUNT_ADVICE})"
    script = [
        {"out": "Estia (recommended)\n"}, {"out": "Yes (recommended)\n"}, {"out": f"{route}\n"},
        {"out": "+30 690 000 0000\n"},
    ]  # fmt: skip
    (folder / "answers.json").write_text(json.dumps(script), encoding="utf-8")
    shown_back: list[str] = []

    def type_answer(prompt: str = "") -> str:
        shown_back.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", type_answer)
    with pytest.raises(SystemExit):
        main(["--config", str(tmp_path / "config.toml")])
    asked = [call["argv"][0] for call in calls(folder)]  # type: ignore[index]
    # name, Signal on your phone, the route, her number, the Registration Lock, language, style, calls, the Extra
    # Features (gum's answer picks web search only: no home area), About you's five lines, move, security; after the
    # first, each yes or no is a menu of Yes, No and Back (owner, 27 Oct: Back at every question of the plan)
    signal = ["choose", "choose", "choose", "input", "choose"]
    assert asked == signal + ["choose"] * 4 + ["input"] * 5 + ["choose", "choose"]
    backs = [call["argv"] for call in calls(folder) if isinstance(call["argv"], list)]
    assert prompts.SETUP_BACK_OPTION not in backs[0] and all(prompts.SETUP_BACK_OPTION in argv for argv in backs[1:3])
    assert len(shown_back) == 1 and "+30 690 000 0000: right?" in shown_back[0]  # her number: never in gum's argv


@pytest.mark.skipif(os.environ.get("GUM_REAL") != "1", reason="opt-in: GUM_REAL=1 GUM_PROGRAM=<gum 2.0.2, downloaded>")
def test_every_flag_setup_gives_gum_is_one_the_pinned_gum_takes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each menu GumFace draws, through the fake; then every flag it gave is in the real gum's own --help."""
    real = Path(os.environ["GUM_PROGRAM"])
    assert hashlib.sha256(real.read_bytes()).hexdigest() == setup_gum.BINARY_SHA256
    face, folder = gum_face(tmp_path, monkeypatch, {"out": "Her name\n"}, {"out": "Juno\n"})
    face.choose(question("name").at(Answers.memory(), Mac(), {"default": "Estia"}), None)
    face.confirm(question("security").at(Answers.memory(), Mac(), {"nuclear": ""}), default=False)
    face.switches(Question("x", "switches", "X", options=(Option("a", "A", on=True), Option("b", "B"))), None)
    face.fields(Question("y", "fields", "Y", fields=(Field("street", "Street"),)), None, "")
    used: dict[str, set[str]] = {}
    for call in calls(folder):
        argv = cast(list[str], call["argv"])
        flags = {
            arg.split("=", 1)[0] for arg in argv[1 : argv.index("--") if "--" in argv else None] if arg.startswith("--")
        }
        used.setdefault(argv[0], set()).update(flags)
    assert set(used) == {"choose", "input", "confirm"}
    for command, flags in used.items():
        shown = subprocess.run([str(real), command, "--help"], capture_output=True, text=True, check=True).stdout
        for flag in flags:
            assert re.search(rf"(?<![\w.-]){re.escape(flag)}(?![\w-])", shown), (command, flag)


# ------------------------------------------------------------------ a download's five dots (setup_progress)


class Screen(io.StringIO):
    """A terminal stdout: what was written, and that it is a terminal."""

    def isatty(self) -> bool:
        return True


def test_plain_lines_give_each_new_dot_a_line_of_its_own(capsys: pytest.CaptureFixture[str]) -> None:
    """No redraw in plain lines (a screen reader reads each line once)."""
    face = PlainFace(readers())
    face.progress("  her model ○○○○○")
    face.progress("  her model ●○○○○")
    face.progress_done()
    assert capsys.readouterr().out == "  her model ○○○○○\n  her model ●○○○○\n"


def test_gum_lights_the_dots_in_place_on_a_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    screen = Screen()
    monkeypatch.setattr("sys.stdout", screen)
    face = GumFace(readers(), tmp_path / "gum")
    face.progress("  her model ○○○○○")
    face.progress("  her model ●○○○○")
    face.say("a line after it")  # the dots' line ends first
    face.progress("  Ollama ○○○○○")
    face.progress_done()
    assert screen.getvalue() == (
        "\r\x1b[2K  her model ○○○○○\r\x1b[2K  her model ●○○○○\na line after it\n\r\x1b[2K  Ollama ○○○○○\n"
    )


def test_back_in_gums_menus_and_its_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Back is gum's menus' last item at a question with one before it; b typed in its line goes back too."""
    from local_assistant.setup_face import Back

    face, _folder = gum_face(tmp_path, monkeypatch, {"out": "Back\n"}, {"out": "Back\n"}, {"out": "b\n"})
    face.back = True
    style = question("style").at(Answers.memory(), Mac(), {"name": "Estia"})
    with pytest.raises(Back):
        face.choose(style, None)
    with pytest.raises(Back):
        face.confirm(question("lock"), default=True)
    number = question("number").at(Answers.memory(), Mac(), {"name": "Estia"})
    with pytest.raises(Back):
        face.text(number, None, "")


def test_after_the_plan_b_in_gums_line_is_no_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Second deep scan (dialogue-back-at-any-question): after the plan, b typed in gum's line says there is no
    going back and asks again; it is never the answer."""
    face, folder = gum_face(tmp_path, monkeypatch, {"out": "b\n"}, {"out": "Melina\n"})
    face.no_back = prompts.SETUP_NO_BACK_HERE
    q = question("name").at(Answers.memory(), Mac(), {"default": "Estia"})
    assert face.text(q, None, "") == "Melina"
    assert [call["argv"][0] for call in calls(folder)] == ["input", "input"]  # type: ignore[index]
    assert prompts.SETUP_NO_BACK_HERE in capsys.readouterr().out


def test_the_windows_terminal_faces_know_there_is_no_going_back(tmp_path: Path) -> None:
    """The window has no b; a step's own line in Terminal and the Terminal face after a closed window do."""
    from local_assistant import setup_window
    from local_assistant.setup import Wizard

    wizard = Wizard(tmp_path / "c.toml", dry_run=False)

    class Gone:
        gone = True
        ended = True

        def send(self, message: object) -> bool:
            return False

        def receive(self, seconds: float | None = None) -> None:
            return None

        def close(self) -> None:
            return None

    plain = PlainFace(readers())
    face = setup_window.WindowFace(Gone(), wizard, lambda: plain)  # type: ignore[arg-type]
    face.no_back = prompts.SETUP_NO_BACK_HERE
    face.show(question("name").at(Answers.memory(), Mac(), {"default": "Estia"}), None)  # closed: Terminal asks
    assert face.terminal_face is plain and plain.no_back == prompts.SETUP_NO_BACK_HERE


def test_gums_fields_gone_back_to_keep_each_answer_before_on_enter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Back (owner, 27 Oct): the answers are kept; in gum's fields too, Enter keeps each one, never in gum's arguments."""
    face, folder = gum_face(tmp_path, monkeypatch, {"out": "\n"}, {"out": "Athens\n"})
    face.previous = {"street": "Ermou", "city": "Thessaloniki"}
    q = Question("home_area", "fields", "Home area", fields=(Field("street", "Street"), Field("city", "City")))
    assert face.fields(q, None, "") == {"street": "Ermou", "city": "Athens"}
    assert not any("Ermou" in str(call["argv"]) for call in calls(folder))


def test_a_stop_in_terminal_has_space_around_each_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner's laptop, 29 Oct ("we need more space, make it readable"): a stop in Terminal is what was found, why it
    matters and the fix, each a paragraph of short lines with a blank line between; Python never repeats it."""
    stop = (
        "✗ Ollama: Homebrew's Ollama answers (/opt/homebrew/bin/ollama): its build has no JSON-schema library "
        "(libollama_xgrammar.dylib), so Ollama's engine for Apple silicon (MLX) gives no JSON-schema output, which she "
        "needs\n  Fix: brew services stop ollama, then brew uninstall ollama"
    )

    PlainFace(Wizard(tmp_path / "c.toml", dry_run=False).readers()).stopped(stop)
    out = capsys.readouterr().out
    assert out.startswith("\n✗ Ollama:")  # a blank line before it
    shown = out[out.rindex("\n✗ Ollama:") :]
    assert shown.startswith("\n✗ Ollama: Homebrew's Ollama answers (/opt/homebrew/bin/ollama).\n\n  Its build has")
    paragraphs = shown.strip("\n").split("\n\n")
    assert len(paragraphs) == 3 and paragraphs[2] == "  Fix: brew services stop ollama, then brew uninstall ollama"
    assert all(len(line) <= 90 for line in paragraphs[1].splitlines())

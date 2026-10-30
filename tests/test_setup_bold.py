"""The owner's notes of 28 Oct on the setup walk-through: on 3 of 12 (her Signal account), "make bold 'spare SIM's' and
'Without Number' then mention 'Signal Account' (not Login)", then "Signal Account / Login, then... people think it terms
of accounts, login is more technical"; and on every screen, "bolden a few words here and there, the important stuff
that gives meaning". A word between ** is bold where a face can draw it (the window, gum on a
terminal) and plain everywhere else (plain lines, gum's menus, the window's log); the marks themselves never show."""

import io
import json
import re
from collections.abc import Iterator
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from local_assistant import hostsec, prompts, setup_window
from local_assistant.setup import Wizard
from local_assistant.setup_face import GumFace, PlainFace, Readers, kept_line
from local_assistant.setup_plan import QUESTIONS, Mac, nuclear_text, question
from local_assistant.setup_questions import Question, bold_text, bolded, plain_text, recommendation
from tests.setup_helpers import FILLED

pytestmark = pytest.mark.usefixtures("no_real_ollama", "no_system_paths", "a_terminal")

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "tools" / "setup-window" / "Page" / "page.js"
WORDS = {key: str(value) for key, value in FILLED.items()} | {
    "nuclear": nuclear_text("Estia"),
    "found": "Ermou, Athens",
    "service": "ollama",
    "username": "estia.42",
}
MACS = (Mac(), Mac(ssh=True), Mac(legacy=True), Mac(ssh=True, legacy=True))
MARK = re.compile(r"\*\*([^*\n]+)\*\*")
TERMINAL_BOLD = re.compile(r"\x1b\[1m(.*?)\x1b\[22m")

# the words in bold on each screen, the same in both faces (owner, 28 Oct): the choice, the limit, the guarantee, the
# thing to have at hand; one to three words each, about three on a screen at most
BOLD = {
    "ollama_app": ("Ollama app", "background build"),  # owner, 29 Oct: the two choices
    "smaller_model": ("steer her",),  # the owner's words, 29 Oct
    "name": ("(AI)",),
    "voice": ("English voice", "one voice"),
    "signal_app": ("spare SIM", "without a number", "Your own Signal"),  # owner, 28 Oct: "yes to both" (words-06)
    "route": ("spare SIM's", "Without number"),  # the owner's own two
    "number": ("no Signal app", "logs out", "kept topped up"),
    "number_right": ("No",),
    "known_number": ("on this Mac", "no new captcha"),
    "lock": ("password manager", "new PIN"),
    "captcha": ("same Wi-Fi", "Open Signal", "never stored"),
    "sms_code": ("with her SIM",),
    "old_pin": ("Registration Lock PIN",),
    "lock_pin": (),  # the question alone
    "lock_pin_again": (),
    "account_id": ("Register without number", "Pay", "password manager"),
    "recovery_key": (),
    "totp": ("six-digit code", "not tested"),
    "username": ("her username", "new chat"),
    "word": ("your own Signal", "exactly", "Only the account"),
    "language": ("one language", "stay English"),
    "style": ("always win",),
    "calls": ("on this Mac", "only the calls", "admin rights twice"),
    "extras": ("security step",),
    "projects": ("off by default", "never changes", "open to her"),
    "projects_folder": (),
    "projects_level": ("README", "documents", "source code"),
    "spotify_id": ("Spotify Premium", "no secret"),
    "mail_ids": ("registered at Microsoft", "not supported"),
    "home_area": ("no house number", "One lookup"),
    "home_found": ("No",),
    "about_you": ("Optional", "on this Mac", "changed or removed"),
    "ollama_key": ("free", "never kept", "One test search"),
    "brave_key": ("optional", "$5 monthly credit", "stops at 900"),
    "keychain_replace": ("another program's", "same account's key", "for her only"),
    "move": ("with the Mac", "once"),
    "security": ("Remote logins", "Admin rights", "Her files", "Registration Lock"),  # owner, 27 Oct: one a bullet
    "remake": ("only the prompt", "answers pause"),
    "offer_run": (),  # the command, with Copy (and for an admin one where macOS asks)
    "lock_retry": ("security step",),
    "login_again": ("typed again",),  # its first paragraph is Signal's limit, filled in: **wait** with a time
    "share_close": (),
    "her_config_add": (),  # the lines, and what happens to them  # the share's own question, and what closing changes in the fold
    "tapo_email": ("for her only",),  # set-tapo-credentials.sh's questions, on the window's screens
    "tapo_password": ("never shows",),
    "calendar_url": ("for her only",),  # set-calendar-url.sh's
    "spotify_paste": (),  # the sign-in's own lines, and its question
    "extras_list": ("optional",),  # a re-run's list, in the window
}
OVER_SSH = {"move": ("inside screen",)}  # the line that says to start screen first


class Nothing:
    def get(self, qid: str) -> object:
        return None

    def kept(self, qid: str) -> bool:
        return False


AT_THE_DESK = Mac()


def asked(qid: str, mac: Mac = AT_THE_DESK) -> Question:
    return question(qid).at(Nothing(), mac, WORDS)  # type: ignore[arg-type]


def readers() -> Readers:
    return Readers(
        hidden=lambda prompt: "q", hidden_lines=lambda p, n, c: "q", flush=lambda: None, terminal=lambda: True
    )


def window_screen(q: Question) -> dict[str, object]:
    wizard = Wizard(Path("/x/c.toml"), dry_run=True)
    face = setup_window.WindowFace(setup_window.Window.__new__(setup_window.Window), wizard, lambda: wizard.face)
    return face.screen(q)


def window_words(screen: dict[str, object]) -> Iterator[str]:
    """Every word of a screen the page draws with its bold (page.js `rich`)."""
    for key in ("lead", "ask", "help", "stop", "note", "more"):
        yield str(screen.get(key) or "")
    for option in screen["options"]:  # type: ignore[attr-defined]
        yield option["label"]
        yield option["line"]
        guide = option["guide"]
        if guide:
            yield guide["needs"]
            yield from guide["steps"]
            yield guide["check"]
            for says, fix in guide["errors"]:
                yield says
                yield fix


def window_bold(q: Question) -> list[str]:
    return [span for text in window_words(window_screen(q)) for span in MARK.findall(text)]


class Screen(io.StringIO):
    """What a face prints, as a terminal (`tty`) or a pipe would take it."""

    def __init__(self, tty: bool) -> None:
        super().__init__()
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def terminal_out(face: PlainFace, q: Question, *, tty: bool = False) -> str:
    out = Screen(tty)
    with redirect_stdout(out):
        face.show(q, None)
    return out.getvalue()


def terminal_bold(q: Question) -> list[str]:
    """The words gum's face draws bold on a terminal, its heading apart."""
    shown = TERMINAL_BOLD.findall(terminal_out(GumFace(readers(), Path("/nonexistent/gum")), q, tty=True))
    return [span for span in shown if span != q.title]


def expected(qid: str, mac: Mac) -> tuple[str, ...]:
    return BOLD[qid] + (OVER_SSH.get(qid, ()) if mac.ssh and not mac.kept_session else ())


# ------------------------------------------------------------------ 3 of 12: her Signal account


def test_the_route_cards_are_the_owners_words() -> None:
    """Owner, 28 Oct: "make bold 'spare SIM's' and 'Without Number' then mention 'Signal Account' (not Login)"; later
    that day: "Signal Account / Login, then... people think it terms of accounts, login is more technical": where setup
    first names it, Account first and Login after it, the name on Signal's purchase screen."""
    assert prompts.SETUP_ROUTE_NUMBER == "A **spare SIM's** number: one SMS code"
    assert prompts.SETUP_ROUTE_NO_NUMBER == "**Without number**: a Signal Account / Login, $2.99 or EUR 3.49 once"
    options = window_screen(asked("route"))["options"]
    assert isinstance(options, list)
    assert [o["label"] for o in options] == [prompts.SETUP_ROUTE_NUMBER, prompts.SETUP_ROUTE_NO_NUMBER]
    listing = asked("route").listing
    assert "  1. A **spare SIM's** number: one SMS code" in listing
    assert "  2. **Without number**: a Signal Account / Login, $2.99 or EUR 3.49 once" in listing


USER_FACING_DOCS = ("README.md", "GUIDE.md", "setup_assistant.md", "SETUP-WITH-YOUR-AI.md", "SECURITY.md")
LABEL = "a Signal Account / Login, $2.99 or EUR 3.49 once"  # the route's label after "Without number: "


def user_facing_words() -> Iterator[tuple[str, str]]:
    """Every sentence a person reads: setup's, doctor's, harden's and the admin commands' (prompts, hostsec's and
    harden's strings), the admin command's help and the estia wrapper's, and the docs."""
    from local_assistant import admin, harden

    for module in (prompts, hostsec, harden):
        for key, value in vars(module).items():
            if isinstance(value, str) and not key.startswith("_"):
                yield f"{module.__name__}.{key}", value
    shown = io.StringIO()
    with redirect_stdout(shown), pytest.raises(SystemExit):
        admin.main(["--help"])
    yield "admin --help", shown.getvalue()
    wrapper = (ROOT / "scripts/estia").read_text(encoding="utf-8")
    yield "estia's usage", "\n".join(line for line in wrapper.splitlines() if line.startswith("#   "))
    for name in USER_FACING_DOCS:
        yield name, (ROOT / name).read_text(encoding="utf-8")


def test_signal_account_in_every_text_a_person_reads_signal_login_only_as_signals_screen() -> None:
    """Owner, 28 Oct: "Signal Account" ("people think it terms of accounts, login is more technical"). Signal for
    Android names its paid account without a number "Signal Login" (its screen's title, checked in Signal-Android's
    strings on 28 Oct 2026), so that name stays only where the steps say what the phone shows, the "Signal Login"
    screen, and as "Signal Account / Login" in the route's label, where setup first names it; everywhere else Login is
    never beside it."""
    for where, text in user_facing_words():
        flat = " ".join(text.split())
        for found in re.finditer(r"Signal Login", flat):
            around = flat[max(0, found.start() - 1) : found.end() + 9]
            assert around.startswith('"Signal Login" screen'), (where, flat[found.start() - 60 : found.end() + 60])
        for found in re.finditer(r"Account / Login", flat):  # only in the label, and where it is shown
            start = found.start() - len("a Signal ")
            assert flat[start : start + len(LABEL)] == LABEL, (where, flat[found.start() - 60 : found.end() + 60])
    assert "Signal Account" in hostsec.NUMBERLESS_STEPS
    for name in ("setup_assistant.md", "SETUP-WITH-YOUR-AI.md"):
        assert "Signal Account" in (ROOT / name).read_text(encoding="utf-8"), name


def test_the_buy_step_names_what_the_phone_shows() -> None:
    """Signal-Android's purchase screen (SignalLoginPaymentScreen): its title "Signal Login", its button "Pay $2.99"
    (the price as Google Play gives it), a one-time purchase through Google Play."""
    step = '2. On the "Signal Login" screen, tap Pay ($2.99 or EUR 3.49, once, in Google Play).'
    assert step in " ".join(hostsec.NUMBERLESS_STEPS.split())
    guide = " ".join((ROOT / "setup_assistant.md").read_text(encoding="utf-8").split())
    assert step.replace("tap Pay", "tap **Pay**") in guide  # Markdown: the button in bold, as setup shows it
    handover = " ".join((ROOT / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8").split())
    assert 'then **Pay** on the "Signal Login" screen ($2.99 or EUR 3.49, once, in Google Play)' in handover


# ------------------------------------------------------------------ a few words in bold on every screen


@pytest.mark.parametrize("qid", [q.id for q in QUESTIONS])
def test_each_screen_bolds_its_own_few_words_the_same_in_both_faces(qid: str) -> None:
    """Owner, 28 Oct: "bolden a few words here and there, the important stuff that gives meaning": one to three words
    a span, about three spans a screen (the security screen's four bullets are the owner's own, 27 Oct), never a whole
    sentence; the window and gum's face on a terminal bold the same words."""
    for mac in MACS:
        q = asked(qid, mac)
        want = expected(qid, mac)
        assert sorted(window_bold(q)) == sorted(want), (qid, mac)
        assert sorted(terminal_bold(q)) == sorted(want), (qid, mac)
        for span in want:
            assert 1 <= len(span.split()) <= 3, span
        assert len(want) <= (4 if qid == "security" else 3), qid


def test_every_question_is_in_the_list_of_bold_words() -> None:
    assert set(BOLD) == {q.id for q in QUESTIONS}


@pytest.mark.parametrize("qid", [q.id for q in QUESTIONS])
def test_a_word_a_face_cannot_draw_bold_holds_no_marks(qid: str) -> None:
    """A screen's heading, its question line (Terminal's prompt, gum's header), the small line under an option, the
    dry run's line, a field's label and its grey example, a link's label: drawn as text, so never a mark."""
    for mac in MACS:
        q = asked(qid, mac)
        texts = [q.title, q.ask, q.window_ask, q.advice, q.dry, q.placeholder, q.later, recommendation(q)]
        texts += [option.short for option in q.options] + [option.tag for option in q.window_options()]
        texts += [text for field in q.fields for text in (field.label, field.placeholder)]
        texts += [label for label, _url in q.links]
        texts += [label for option in q.options if option.guide for label, _url in option.guide.links]
        for text in texts:
            assert "**" not in text, (qid, text)


def test_a_shared_sentence_is_bolded_only_in_setup() -> None:
    """The Registration Lock's note and the phone's steps are harden's and `estia signal-login`'s words too, which
    they print as plain lines: setup bolds them in its own copy, and a word that is not there is an error at once."""
    from local_assistant import harden

    assert "**" not in harden.LOCK_NOTE and "**" not in hostsec.NUMBERLESS_STEPS
    assert plain_text(asked("lock").lead).startswith(harden.LOCK_NOTE)
    assert plain_text(asked("account_id").lead) == hostsec.NUMBERLESS_STEPS
    assert bolded("Keep it in your password manager.", "password manager") == "Keep it in your **password manager**."
    with pytest.raises(ValueError, match="not in"):
        bolded("Keep it safe.", "password manager")


# ------------------------------------------------------------------ the marks never show


@pytest.mark.parametrize("qid", [q.id for q in QUESTIONS])
def test_plain_lines_and_gum_never_show_the_marks(qid: str) -> None:
    for mac in MACS:
        q = asked(qid, mac)
        for tty in (True, False):
            out = terminal_out(PlainFace(readers()), q, tty=tty)
            assert "**" not in out and "\x1b" not in out, (qid, out)
            out = terminal_out(GumFace(readers(), Path("/nonexistent/gum")), q, tty=tty)
            assert "**" not in out and ("\x1b" not in out or tty), (qid, out)


def test_gums_menus_show_the_labels_plain_and_pick_by_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """gum's menus cannot draw bold: each label goes to gum without its marks (as gum then prints the one picked), the
    recommended one with "(recommended)" and its reason after it (owner, 29 Oct)."""
    from tests.test_setup_faces import calls, gum_face

    plain = plain_text(prompts.SETUP_ROUTE_NO_NUMBER)
    face, folder = gum_face(tmp_path, monkeypatch, {"out": f"{plain}\n"})
    assert face.choose(asked("route"), None) == "no_number"
    face.back, face.previous = True, "no_number"  # gone back to: the one before is gum's --selected
    assert face.choose(asked("route"), None) == "number"  # Enter: the first, the recommended one
    for call in calls(folder):
        args = call["argv"]
        assert isinstance(args, list)
        for arg in args:
            assert "**" not in arg, arg
    argv = calls(folder)[1]["argv"]
    assert isinstance(argv, list) and f"--selected={plain}" in argv
    first = f"{plain_text(prompts.SETUP_ROUTE_NUMBER)} (recommended, {prompts.SETUP_ACCOUNT_ADVICE})"
    assert argv[argv.index("--") + 1 :] == [first, plain, prompts.SETUP_BACK_OPTION]


def test_a_kept_answer_and_an_answer_before_are_shown_plain(capsys: pytest.CaptureFixture[str]) -> None:
    q = asked("route")
    assert kept_line(q, "no_number") == (
        "Kept: Her Signal account: Without number: a Signal Account / Login, $2.99 or EUR 3.49 once. "
        "To change it: scripts/setup.sh --redo route"
    )
    assert q.shown_value("number") == "A spare SIM's number: one SMS code"
    face = PlainFace(readers())
    face.previous = "no_number"
    face.show(q, None)
    out = capsys.readouterr().out
    assert "Your answer: Without number: a Signal Account / Login, $2.99 or EUR 3.49 once. Enter keeps it." in out
    assert "**" not in out


def test_a_line_setup_says_is_bold_on_a_terminal_and_plain_elsewhere_a_command_as_typed(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup's own lines go through the face too (the closing list, a step's lines): never a mark shown. A command's
    line ("$ …") is shown as it runs, its characters as they are."""
    line = "Done when the check shows **no ✗**.\n  $ grep '**x**' notes"
    PlainFace(readers()).say(line)
    assert capsys.readouterr().out == "Done when the check shows no ✗.\n  $ grep '**x**' notes\n"
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    GumFace(readers(), Path("/nonexistent/gum")).say(line)
    assert capsys.readouterr().out == "Done when the check shows \x1b[1mno ✗\x1b[22m.\n  $ grep '**x**' notes\n"
    assert bold_text("$ a **b**") == "$ a **b**" and plain_text("  $ a **b**") == "  $ a **b**"


def test_the_windows_log_and_terminal_behind_it_get_setups_lines_plain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.test_setup_window import a_face

    _wizard, face, sent = a_face(tmp_path)
    face.say("What she can do: **/tools** in Signal.")
    assert capsys.readouterr().out == "What she can do: /tools in Signal.\n"
    assert sent.messages[-1] == {"t": "progress", "line": "What she can do: /tools in Signal."}
    face.done(["What she can do: **/tools** in Signal."])  # the closing list: drawn bold by the page
    assert sent.messages[-1] == {"t": "done", "lines": ["What she can do: **/tools** in Signal."]}


def test_the_windows_kept_answers_are_plain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the plan, the window lists the answers kept from the last run: each answer's words as text."""
    import argparse

    from tests.test_setup_window import a_face

    wizard, face, sent = a_face(tmp_path)
    wizard.answers.set("route", "no_number")
    monkeypatch.setattr(wizard.answers, "kept", lambda qid: qid == "route")  # as from an earlier run
    sent.messages.clear()
    face._given = lambda q, fits: (True, [])  # type: ignore[method-assign]
    assert face.offer_changes(argparse.Namespace(redo=[]))
    screen = next(m for m in sent.messages if m["t"] == "screen")
    assert "**" not in json.dumps(screen["options"])


def test_the_page_draws_marks_bold_where_setup_bolds_and_plain_where_it_cannot() -> None:
    """page.js: a label, its line, a guide and the fine print through `rich` (bold, a link); a heading, a small line, a
    field's label, a kept answer, a check's answer and a button's label through `unmarked` (the marks left out); both
    read a mark as setup does (a pair on one line, no * inside). The page loads with them (a name of its own: the
    intro's `plain` is taken), as a headless browser showed."""
    script = PAGE.read_text(encoding="utf-8")
    assert "const MARK = /\\*\\*([^*\\n]+)\\*\\*/g;" in script
    assert 'const unmarked = (text) => String(text || "").replace(MARK, "$1");' in script
    assert "text.split(/\\*\\*/)" not in script  # an odd ** is never a bold that runs on
    for drawn in (
        'rich(el("span", { class: "x-name" }), option.label)',
        'rich(el("span", { class: "x-line" }), option.line)',
        'rich(el("span", { class: "card-label" }), option.label)',
        'rich(el("p", { class: "card-line" }), option.line)',
        'rich(el("p", { class: "fine" }), screen.note || "")',
        'rich(el("p"), guide.needs)',
        'rich(el("li"), step)',
    ):
        assert drawn in script, drawn
    for drawn in (
        'el("h2", { id: "h-screen", tabindex: "-1", text: unmarked(screen.title) })',
        'el("span", { class: "pill", text: unmarked(option.tag) })',
        'el("span", { class: "label", text: unmarked(action.label) })',
        'mainRec.textContent = unmarked((main && main.rec) || "")',
        "box.textContent = unmarked(message.ok ? message.shown : message.message)",
        'el("label", { for: input.id, text: unmarked(field.label) })',
        'if (typeof value === "string") return unmarked(label(value));',
    ):
        assert drawn in script, drawn


def test_the_windows_own_screens_bold_a_word_too() -> None:
    """The window's own lines (a locked keychain, the hand-over) and the kept answers' screen (what Change does): a
    word each in bold, as the questions' screens; the hand-over also its button's name, Switch to Terminal (words
    review, 30 Oct: macOS may keep the window in front, so the screen names the button, never "It is in front now.")."""
    for why, *words in (
        (prompts.SETUP_WINDOW_KEYCHAIN_LOCKED, "locked"),
        (prompts.SETUP_WINDOW_HANDED, "same question", "Switch to Terminal"),
        (prompts.SETUP_WINDOW_NO_BOX, "Switch to Terminal"),
        (prompts.SETUP_WINDOW_KEPT_LEAD, "Change"),
        (prompts.SETUP_DONE, "no ✗"),
    ):
        assert MARK.findall(why) == words, why
    for why in (prompts.SETUP_WINDOW_HANDED, prompts.SETUP_WINDOW_NO_BOX):
        assert "in front now" not in why and why.startswith(
            "Setup goes on in the Terminal window you started it in.\n\n"
        )
    assert 'terminal: "Switch to Terminal"' in PAGE.read_text(encoding="utf-8")  # the button the words name

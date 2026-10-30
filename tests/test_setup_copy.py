"""The owner's notes on the setup walk-through (27 Oct): the name screen, the recommendation under its option, short
paragraphs with a blank line between them, the security screen's bullets and its More fold, no Mac password wording
in the window, and Terminal's blank lines. Both faces get the same content, each in its own form."""

import json
import re
from pathlib import Path

import pytest

from local_assistant import prompts, setup, setup_window
from local_assistant.setup import Wizard
from local_assistant.setup_face import GumFace, PlainFace, Readers
from local_assistant.setup_plan import QUESTIONS, Mac, nuclear_text, question
from local_assistant.setup_questions import RECOMMENDED, Question, plain_text, recommendation
from tests.setup_helpers import BOT, FILLED

pytestmark = pytest.mark.usefixtures("no_real_ollama", "no_system_paths", "a_terminal")

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "tools" / "setup-window" / "Page"
WORDS = {key: str(value) for key, value in FILLED.items()} | {"nuclear": nuclear_text("Estia"), "found": "Ermou"}
MACS = (Mac(), Mac(ssh=True), Mac(legacy=True), Mac(ssh=True, legacy=True))
AT_THE_DESK = Mac()


class Nothing:
    def get(self, qid: str) -> object:
        return None

    def kept(self, qid: str) -> bool:
        return False


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


# ------------------------------------------------------------------ 1. the name screen


def test_the_name_screen_offers_four_names_and_a_field_for_her_own() -> None:
    """Owner, 27 Oct: "give only 4 suggestions, allow the user to fill-in a name"; "be more concise"."""
    q = asked("name")
    assert [(o.id, o.label) for o in q.options] == [
        ("Estia", "Estia"),
        ("Maya", "Maya"),
        ("Melina", "Melina"),
        ("Socrates", "Socrates"),
        ("other", "Her name"),
    ]
    assert q.options[0].tag == RECOMMENDED == "Recommended" and not any(o.tag for o in q.options[1:])
    assert q.lead == "She answers to this name. Her Signal profile shows it with **(AI)** after it."  # bold: 28 Oct
    assert window_screen(q)["ask"] == "Pick a name or type your own."
    assert "1 to 20" not in q.lead and "config" not in q.lead  # the rule only as the error; the config line: the docs
    assert prompts.SETUP_NAME_RULE == "1 to 20 letters, spaces or hyphens."


@pytest.mark.parametrize(  # a female name's heart, on either model (`setup.female_voice`; owner, 29 Oct)
    ("name", "voice"), [("Estia", "heart"), ("Maya", "heart"), ("melina", "heart"), ("Socrates", "alba")]
)
def test_each_suggested_name_brings_its_voice(name: str, voice: str) -> None:
    assert setup.suggested(name) == (name.capitalize() if name.islower() else name, voice)


@pytest.mark.parametrize("name", ["Tiffany", "Perikles", "Plato", "Juno"])
def test_any_other_name_is_typed_and_asks_for_its_voice(name: str) -> None:
    assert setup.suggested(name) is None
    assert question("voice").shows(
        type("A", (), {"get": lambda self, qid: name, "kept": lambda self, qid: True})(), Mac()
    )


# ------------------------------------------------------------------ 2. the recommendation under its option


@pytest.mark.parametrize("qid", [q.id for q in QUESTIONS])
def test_no_question_holds_its_recommendation_in_its_sentence(qid: str) -> None:
    """Owner, 27 Oct: "have the recommendations below the option with smaller font": the question alone."""
    for mac in MACS:
        q = asked(qid, mac)
        for text in (q.ask, q.window_ask, str(window_screen(q)["ask"])):
            assert "Recommend" not in text, (qid, text)


def test_a_yes_or_no_sends_its_two_buttons_with_the_small_line_under_the_recommended_one() -> None:
    screen = window_screen(asked("signal_app"))
    assert screen["ask"] == "Is Signal on your phone now?"
    options = screen["options"]
    assert isinstance(options, list)
    assert [(o["id"], o["label"], o["tag"]) for o in options] == [("yes", "Yes", "Recommended"), ("no", "No", "")]
    with_reason = window_screen(asked("remake"))["options"]
    assert isinstance(with_reason, list) and (with_reason[0]["tag"], with_reason[0]["line"]) == (
        "Recommended",
        "Once she is stopped.",
    )


def test_a_pick_carries_its_reason_in_its_own_small_line() -> None:
    """Owner, 29 Oct ("'recommended:' with below the YES looks cheap"): the pill says Recommended, and the reason leads
    the option's own small line."""
    route = window_screen(asked("route"))["options"]
    assert isinstance(route, list) and route[0]["tag"] == "Recommended" and route[1]["tag"] == ""
    assert route[0]["line"] == f"The tested route. {prompts.SETUP_ROUTE_NUMBER_LINE}"


def test_the_ollama_app_card_has_the_pill_and_says_its_reason_once() -> None:
    """The Ollama app's question (owner, 29 Oct) as every pick: the pill, the reason first in its one small line, said
    once, the login limit in doctor's words and once on the screen."""
    cards = window_screen(asked("ollama_app", Mac(ollama_app="/Applications/Ollama.app")))["options"]
    assert isinstance(cards, list) and (cards[0]["tag"], cards[1]["tag"]) == ("Recommended", "")
    assert cards[0]["line"] == (
        "Nothing to install. She answers only while someone is logged in and the Ollama app runs. "
        "Her prompts pass through a program your account can change."  # its second limit (review W06)
    )
    limit = "answers only while someone is logged in"
    window = f"{prompts.SETUP_OLLAMA_APP_LEAD} {' '.join(str(card['line']) for card in cards)}"
    terminal = f"{prompts.SETUP_OLLAMA_APP_LEAD} {prompts.SETUP_OLLAMA_APP_LIST}"
    assert window.count(limit) == 1 and terminal.count(limit) == 1  # one screen says it once


@pytest.mark.parametrize(
    ("qid", "mac", "line"),
    [
        ("signal_app", Mac(), "Yes (recommended)."),
        ("smaller_model", Mac(), "Yes (recommended)."),  # the owner's words, 29 Oct: no "with caution"
        ("route", Mac(), "1 (recommended, the tested route)."),
        ("ollama_app", Mac(ollama_app="/Applications/Ollama.app"), "1 (recommended, nothing to install)."),
        # c-4 (round 3): over SSH the app cannot start with no one at the Mac's screen
        (
            "ollama_app",
            Mac(ollama_app="/Applications/Ollama.app", ssh=True),
            "2 (recommended, over a remote login: the app needs someone at the Mac's screen).",
        ),
        # owner, 30 Oct (W06): her own account planned, a new install
        (
            "ollama_app",
            Mac(ollama_app="/Applications/Ollama.app", fresh_hers=True),
            "2 (recommended, she starts with the Mac, without a login).",
        ),
        ("name", Mac(), "Estia (recommended)."),
        ("style", Mac(), "hearth (recommended)."),
        ("extras", Mac(), "Web search and Places and routes (recommended)."),
        ("move", Mac(ssh=True), "No (recommended, run setup again inside screen)."),
        ("language", Mac(), ""),  # the question asks for the language spoken at home
    ],
)
def test_terminal_says_the_recommendation_on_a_line_of_its_own(qid: str, mac: Mac, line: str) -> None:
    assert recommendation(asked(qid, mac)) == line


def test_over_ssh_the_ollama_cards_recommend_the_background_build_and_enter_takes_it() -> None:
    """c-4 (round 3, round 1's issue 6): over SSH Enter at the app waited 3 minutes for an app no one can open."""
    q = asked("ollama_app", Mac(ollama_app="/Applications/Ollama.app", ssh=True))
    assert q.recommended == "build" and [o.id for o in q.options if o.tag == "Recommended"] == ["build"]
    assert q.ask == "Which Ollama? (1 or 2; Enter: 2)"
    at_the_desk = asked("ollama_app", Mac(ollama_app="/Applications/Ollama.app"))
    assert at_the_desk.recommended == "app" and at_the_desk.ask == "Which Ollama? (1 or 2; Enter: 1)"


def test_plain_lines_put_the_recommendation_right_before_the_question(capsys: pytest.CaptureFixture[str]) -> None:
    q = asked("signal_app")
    PlainFace(readers()).show(q, None)
    out = capsys.readouterr().out
    assert out.endswith("\nYes (recommended).\n")
    assert out.startswith("\nSignal on your phone\n\n")


# ------------------------------------------------------------------ 2 and 4. short paragraphs, blank lines


def paragraphs(text: str) -> list[list[str]]:
    return [block.splitlines() for block in re.split(r"\n\s*\n", text.strip("\n")) if block.strip()]


PLAN = [q.id for q in QUESTIONS if q.plan] + ["ollama_key", "brave_key", "captcha", "number_right", "home_found"]


@pytest.mark.parametrize("qid", PLAN)
def test_every_lead_is_short_paragraphs_of_one_idea(qid: str) -> None:
    """Owner, 27 Oct: "leave some blank rows between text paragraphs"; "no wall of text": a paragraph holds at most two
    lines (a numbered or bulleted list is its own block), each line short."""
    for mac in MACS:
        q = asked(qid, mac)
        for text in (q.lead, q.window_lead, q.more):
            for block in paragraphs(text):
                listed = all(line.startswith("  ") for line in block)  # a list: its lines indented
                if not listed and text != q.more:  # the More fold holds the details
                    assert len(block) <= 2, (qid, block)
                for line in block:
                    assert len(plain_text(line)) <= 100, (qid, line)  # as shown: the bold marks are no characters


def test_the_signal_screen_is_the_owners_own_example() -> None:
    q = asked("signal_app")
    assert [" ".join(block) for block in paragraphs(plain_text(q.lead))] == [
        "Estia gets her own Signal account: the number of a spare SIM, or a Signal Account without a number. It is hers"
        " alone.",
        "Your own Signal stays as it is, and you write and speak to her from it.",
        "No Signal on your phone yet? Install it first, free: signal.org/install",
    ]


def test_a_step_line_is_followed_by_a_blank_line() -> None:
    assert prompts.SETUP_STEP.format(n=2, m=9, what="her model") == "\nStep 2 of 9: her model\n"


def test_the_page_draws_paragraphs_lists_bold_and_the_small_line() -> None:
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    assert "function blocks(" in script and "split(/\\n\\s*\\n/)" in script
    assert 'el("strong"' in script and 'class: "pill"' in script and "<details" not in script
    css = (PAGE / "page.css").read_text(encoding="utf-8")
    assert ".pill {" in css and ".lead + .lead" in css


# ------------------------------------------------------------------ 5 and 7. the security screen


def test_the_security_screen_is_the_choice_and_one_bold_line_each() -> None:
    """Owner, 27 Oct: "security lots of text..."; "try bullets and some bold words": the options as a short bulleted
    list, the key word of each in bold, one line each; the details in a More fold."""
    q = asked("security")
    bullets = [line for line in q.lead.splitlines() if line.lstrip().startswith("• ")]
    assert len(bullets) == 4 and all(re.match(r"\s*• \*\*[^*]+\*\*", line) for line in bullets)
    assert len([line for line in q.window_lead.splitlines() if line.strip()]) <= 7  # the window: the choice, Yes, No
    assert q.more and "harden.sh" in q.more and nuclear_text("Estia").strip()[:20] in " ".join(q.more.split())
    screen = window_screen(q)
    assert screen["more"] == q.more
    assert plain_text("• **Remote logins** (SSH): keys only.") == "• Remote logins (SSH): keys only."


def test_the_windows_security_screen_says_what_yes_does_before_no() -> None:
    """Second deep scan (dialogue-window-security-no-yes-line): the window said only "No: scripts/harden.sh runs the
    same step later.", and Yes went on in Terminal unannounced. Yes, then No, each a paragraph (the page joins a
    paragraph's lines), as the Registration Lock's screen has them; never the Mac password (owner, 27 Oct)."""
    q = asked("security")
    lead = str(window_screen(q)["lead"])
    assert prompts.SETUP_WINDOW_SECURITY_YES == "Yes: admin rights, asked by macOS in its own box when needed."
    assert lead.endswith(f"\n\n{prompts.SETUP_WINDOW_SECURITY_YES}\n\n{prompts.SETUP_SECURITY_NO}")
    assert "mac password" not in lead.lower() and "password" not in prompts.SETUP_WINDOW_SECURITY_YES


def test_terminals_security_screen_is_the_choice_in_nine_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """Owner, 27 Oct: "at most ~3 lines before a question"; "details only in a 'More' fold or the docs": Terminal has
    no fold, so it shows the details' one-line form (`more_typed`), Yes before No, one colon in the Yes line. The window
    keeps its fold and its own No line; the move's details go the same way."""
    from local_assistant import hostsec

    q = asked("security")
    PlainFace(readers()).show(q, None)
    out = capsys.readouterr().out
    body = [line for line in out.splitlines() if line.strip() and not line.endswith("(recommended).")]
    assert len(body) <= 9, body
    assert out.index("Yes: ") < out.index("No: ") and out.count("No: ") == 1
    assert prompts.SETUP_SECURITY_PASSWORD.count(":") == 1
    assert "once 5 minutes have passed since admin rights were last used" in prompts.SETUP_SECURITY_PASSWORD
    assert q.more_typed == prompts.SETUP_NUCLEAR_TYPED and hostsec.NUCLEAR in q.more_typed
    assert prompts.SETUP_SECURITY_MORE not in out and hostsec.NUCLEAR_LINE not in " ".join(out.split())
    screen = window_screen(q)
    assert screen["more"] == q.more and hostsec.nuclear_line("Estia")[:40] in " ".join(q.more.split())
    assert str(screen["lead"]).endswith(prompts.SETUP_SECURITY_NO) and "Mac password" not in str(screen["lead"])
    move = asked("move")
    PlainFace(readers()).show(move, None)
    out = capsys.readouterr().out
    assert prompts.SETUP_MOVE_MORE.splitlines()[0] not in out and move.more_typed in out
    assert move.more_typed == "Details: setup_assistant.md, 2.7."


def test_gum_draws_the_bold_words_bold_and_plain_lines_leave_the_marks_out(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    q = asked("security")
    PlainFace(readers()).show(q, None)
    assert "**" not in capsys.readouterr().out
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    GumFace(readers(), Path("/nonexistent/gum")).show(q, None)
    assert "\x1b[1mRemote logins\x1b[22m" in capsys.readouterr().out


# ------------------------------------------------------------------ 10. no Mac password wording in the window


def test_no_window_screen_speaks_of_the_mac_password() -> None:
    """Owner, 27 Oct: "lets remove the message about the Mac password": no screen of the window says it (Terminal
    and the docs keep the fact)."""
    assert not hasattr(prompts, "SETUP_WINDOW_NO_PASSWORD")
    for q in QUESTIONS:
        for mac in MACS:
            screen = window_screen(asked(q.id, mac))
            words = json.dumps(screen).lower().replace("your password manager", "")
            assert "mac password" not in words and "your password" not in words, q.id
    assert "never asks for your Mac password" not in (PAGE / "page.js").read_text(encoding="utf-8")


def test_in_the_window_no_line_asks_for_the_mac_password_and_terminal_reads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner, 27 Oct (note 10): the window never asks for the Mac password; owner, 29 Oct ("can we have Terminal in
    the window?"): no step goes on in Terminal. A locked login keychain at Signal's Recovery key: the window's own line
    says macOS asks in its own box, no line it shows names the Mac password, and nothing is read from Terminal.
    Terminal's own lines keep the fact."""
    import shlex
    import sys

    from tests.test_setup_admin import Scripted

    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    window = Scripted()
    wizard.face = setup_window.WindowFace(window, wizard, lambda: PlainFace(readers()))  # type: ignore[arg-type]
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("Terminal asked"))
    folder = tmp_path / "security"  # the fake security, its login keychain locked
    folder.mkdir()
    tool = tmp_path / "tools/usr/bin/security"
    tool.parent.mkdir(parents=True)
    fake = Path(__file__).parent / "fakes" / "fake_security.py"
    run = f"exec {shlex.quote(sys.executable)} {shlex.quote(str(fake))}"
    tool.write_text(f'#!/bin/sh\nFAKE_SECURITY_DIR={shlex.quote(str(folder))} {run} "$@"\n')
    tool.chmod(0o755)
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(tmp_path / "tools"))
    (folder / "desk").touch()  # at the desk: macOS's own box opens it at the write
    wizard.keep_in_keychain("99999999-8888-4777-8666-555555555555", "abcdefghijklmnopqrstuvwxyz012345" * 2)
    lines = [str(m["line"]) for m in window.sent("progress")]
    assert plain_text(prompts.SETUP_WINDOW_KEYCHAIN_LOCKED) in lines and prompts.SETUP_KEYCHAIN_KEPT in lines
    for line in lines:
        assert "mac password" not in line.lower(), line
    assert not window.sent("terminal") and (folder / "boxed").exists()
    assert "unlock-keychain" not in (folder / "calls.jsonl").read_text()
    assert not (folder / "unlocked").exists()  # locked again after both saves
    assert "Mac password" in prompts.SETUP_KEYCHAIN_LOCKED and "Mac password" in prompts.SETUP_KEY_KEYCHAIN_LOCKED


# ------------------------------------------------------------------ 6 and 8. Terminal only at the hand-over


def test_the_hand_over_screen_promises_only_what_the_window_does() -> None:
    """The app's activation of Terminal may be declined by macOS (Bridge.swift says so): the hand-over's screen keeps
    Switch to Terminal. No step goes on in Terminal any more (owner, 29 Oct), so the page has no such words."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    assert 'terminal: "Switch to Terminal"' in script and "function handedOver(why)" in script
    for gone in ("This step goes on in Terminal.", "comes back by itself", "atTerminal", "function terminal("):
        assert gone not in script, gone
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Terminal to the front for a step in Terminal" not in changelog


def test_no_dead_run_it_button_and_the_old_terminal_words_are_gone() -> None:
    """A command setup runs is offered in Terminal (Wizard.offer, "Press Enter to run it"), and in the window on its
    own screen (offer_run: Run it or Skip, setup_admin); setup's own words no longer say "Continue in Terminal"."""
    import dataclasses
    import inspect

    assert "offer" not in {item.name for item in dataclasses.fields(Question)}
    assert not hasattr(prompts, "SETUP_RUN") and not hasattr(prompts, "SETUP_SKIP")
    assert "Continue in Terminal" not in inspect.getsource(setup_window)


# ------------------------------------------------------------------ 3. Back within the plan, and a quiet way out


class Stopped(Exception):
    """The test's stand-in for the Ollama step: the plan is done there."""


def stop_at_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    def ollama(self: Wizard) -> None:
        raise Stopped

    monkeypatch.setattr(Wizard, "ollama", ollama)


def typing(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[str]:
    """`answers` typed in turn at every question (then Enter); the questions asked are recorded."""
    asked: list[str] = []
    given = iter(answers)

    def typed(prompt: str = "") -> str:
        asked.append(prompt.strip())
        return next(given, "")

    monkeypatch.setattr("builtins.input", typed)
    return asked


def saved() -> dict[str, object]:
    import tomllib

    from local_assistant import setup_answers

    path = setup_answers.default_path()
    return tomllib.loads(path.read_text(encoding="utf-8"))["answers"] if path.exists() else {}


def test_b_goes_back_one_question_and_the_route_can_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner, 27 Oct ("imagine that I cannot find the number"): b at any question of the plan goes one back, the answers
    so far kept; back at the route, an account with no number."""
    asked = typing(monkeypatch, "Juno", "m", "y", "", "b", "2")
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        setup.main(["--config", str(tmp_path / "config.toml"), "--plain"])
    routes = [at for at, prompt in enumerate(asked) if prompt.startswith("Which one? (1 or 2")]  # gone back: no Enter
    assert len(routes) == 2 and routes[1] == routes[0] + 2  # the number between them, then the route again
    kept = saved()
    assert kept["route"] == "no_number" and kept["name"] == "Juno" and kept["voice"] == "male"
    assert "lock" not in kept  # no number: no Registration Lock
    out = capsys.readouterr().out
    assert out.count(prompts.SETUP_BACK_HOW) == 1  # said once, at the first question that has a Back


def test_back_keeps_the_answer_and_enter_takes_it_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = typing(monkeypatch, "Juno", "m", "b", "", "y")
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        setup.main(["--config", str(tmp_path / "config.toml"), "--plain"])
    voices = [prompt for prompt in asked if prompt.startswith("Female or male voice?")]
    assert len(voices) == 2 and saved()["voice"] == "male"  # Enter kept "m"
    assert prompts.SETUP_BACK_KEPT.format(shown="Male") in capsys.readouterr().out


def test_gone_back_to_plain_lines_drop_the_enter_hint_that_no_longer_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gone back to, Enter keeps the answer before ("Your answer: Male. Enter keeps it."): the question's own "(Enter:
    female)" would say the opposite, so it goes, as gum and the window leave it out; a hint's format part stays."""
    from local_assistant.setup_face import without_enter

    asked = typing(monkeypatch, "Juno", "m", "b", "", "y")
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        setup.main(["--config", str(tmp_path / "config.toml"), "--plain"])
    voices = [prompt for prompt in asked if prompt.startswith("Female or male voice?")]
    assert voices == ["Female or male voice? (Enter: female)", "Female or male voice?"]
    assert saved()["voice"] == "male"
    assert without_enter("Which one? (1 or 2; Enter: 1)") == "Which one? (1 or 2)"
    assert without_enter("Spotify's Client ID (Enter: later):") == "Spotify's Client ID:"
    assert without_enter(prompts.SETUP_FEATURES_ASK) == "Which ones? (names or numbers; none: no extras)"


def test_the_first_question_has_no_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    typing(monkeypatch, "b", "m", "y")
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        setup.main(["--config", str(tmp_path / "config.toml"), "--plain"])
    assert saved()["name"] == "b"  # nothing before it: b is a name there


def test_the_back_line_says_back_holds_until_setup_starts_installing() -> None:
    """Second deep scan (dialogue-back-at-any-question): "Type b at any question" held only within the plan."""
    assert prompts.SETUP_BACK_HOW == (
        "Type b to go back one question, until setup starts installing; your answers are kept."
    )
    assert "until setup starts installing" in prompts.SETUP_BACK_HOW_MENU
    assert prompts.SETUP_NO_BACK_HERE == "No going back here: the installs have started."


@pytest.mark.parametrize("face", ["plain", "gum"])
def test_b_after_the_plan_is_no_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], face: str
) -> None:
    """After the plan, b at "Use the Signal number already set up here? [y/N]" was read as No, and setup registered
    the number again, with a new captcha and SMS code. It says there is no going back and asks again; within the
    plan, b still goes back (and is a no at a "Right?", a name at the first question)."""
    later = iter(["b", "back", "y"])
    known = prompts.SETUP_KNOWN_NUMBER_ASK.format(number=BOT)
    asked: list[str] = []

    def typed(prompt: str = "") -> str:
        asked.append(prompt.strip())
        if prompt.startswith(known):
            return next(later)
        if prompt.startswith("Her number"):
            return "+30 690 000 0000"
        return "y" if "[Y/n]" in prompt or "[y/N]" in prompt else ""  # a yes to each of the plan's yes or no

    monkeypatch.setattr("builtins.input", typed)
    seen: list[object] = []

    def ollama(self: Wizard) -> None:  # the first step after the plan: her number, set up here before
        if face == "gum":
            self.face = GumFace(self.readers(), Path("/nonexistent/gum"))  # its menus fail: plain lines, its line
            self.face.no_back = prompts.SETUP_NO_BACK_HERE
        seen.append(self.answer("known_number", number=BOT))
        raise Stopped

    monkeypatch.setattr(Wizard, "ollama", ollama)
    with pytest.raises(Stopped):
        setup.main(["--config", str(tmp_path / "config.toml"), "--plain"])
    assert seen == [True]
    assert len([prompt for prompt in asked if prompt.startswith(known)]) == 3
    assert capsys.readouterr().out.count(prompts.SETUP_NO_BACK_HERE) == 2


def test_back_at_her_number_shown_back_asks_the_number_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = typing(monkeypatch, "", "y", "", "+30 690 000 0000", "b", "+30 690 000 0001", "y")
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        setup.main(["--config", str(tmp_path / "config.toml"), "--plain"])
    assert sum(prompt.startswith("Her number") for prompt in asked) == 2
    assert saved()["number"] == "+306900000001"


def test_q_at_her_number_stops_with_every_answer_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner, 27 Oct: "Not at hand? Stop here: your answers are kept, and running setup again goes on from this
    question." Terminal: q at the question; the window: its Stop here."""
    typing(monkeypatch, "Maya", "y", "", "q")
    assert setup.main(["--config", str(tmp_path / "config.toml"), "--plain"]) == 130
    out = capsys.readouterr().out
    assert prompts.SETUP_NUMBER_STOP_TYPED in out and out.strip().endswith(prompts.SETUP_STOPPED)
    assert saved() == {"name": "Maya", "signal_app": True, "route": "number"}
    asked = typing(monkeypatch, "+30 690 000 0000", "y")
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        setup.main(["--config", str(tmp_path / "config.toml"), "--plain"])
    assert asked[0].startswith("Her number")  # setup again goes on from this question


def test_redo_signal_asks_her_signal_account_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The SMS code's line names it (owner, 27 Oct: "Signal has the code now: start again with setup --redo signal")."""
    typing(monkeypatch, "Maya", "y", "", "+30 690 000 0000", "y", "y")
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        setup.main(["--config", str(tmp_path / "config.toml"), "--plain"])
    asked = typing(monkeypatch, "y", "2")
    with pytest.raises(Stopped):
        setup.main(["--config", str(tmp_path / "config.toml"), "--plain", "--redo", "signal"])
    assert asked[0].startswith(prompts.SETUP_SIGNAL_APP_ASK) and asked[1].startswith(prompts.SETUP_ACCOUNT_ASK)
    kept = saved()
    assert kept["route"] == "no_number" and "number" not in kept and "lock" not in kept
    assert "--redo signal" in prompts.SETUP_NO_BACK_CODE


def test_the_window_offers_back_from_the_second_question_and_stop_at_her_number() -> None:
    from local_assistant import setup_window
    from tests.test_setup_window import a_face  # noqa: F401  (the window's own stand-ins)

    wizard = Wizard(Path("/x/c.toml"), dry_run=True)
    face = setup_window.WindowFace(setup_window.Window.__new__(setup_window.Window), wizard, lambda: wizard.face)
    face.back = True
    number = face.screen(asked("number"))
    assert number["back"] is True and number["stop"] == prompts.SETUP_NUMBER_STOP
    face.back = False
    sms = face.screen(asked("sms_code"))
    assert sms["back"] is False and sms["note"] == prompts.SETUP_NO_BACK_CODE
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    assert 'post({ t: "back" })' in script and 'post({ t: "stop" })' in script and "WORDS.stopHere" in script


def test_back_works_again_after_a_refused_answer() -> None:
    """hold() turns every button off until setup's word; a refusal sends no screen again (setup only waits), so the
    page's refusal branch turns Back on again itself, as it does the other button."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    body = script[script.index("  function checked(message) {") : script.index("  function workView(")]
    refused = body[body.index("if (!message.ok) {") : body.index("return;", body.index("if (!message.ok) {"))]
    assert "backBtn.disabled = false;" in refused and "altBtn.disabled = false;" in body


class Scripted:
    """A window that takes every line setup sends and gives back its `replies` in turn, one per wait; setup's look
    without a wait (`heard`, before a screen goes out) finds nothing yet: the person acts on the screen."""

    def __init__(self, *replies: dict[str, object]) -> None:
        self.messages: list[dict[str, object]] = []
        self.replies = list(replies)
        self.gone = False

    def send(self, message: dict[str, object]) -> bool:
        self.messages.append(dict(message))
        return True

    def receive(self, seconds: float | None = None) -> dict[str, object] | None:
        if seconds == 0:
            return None
        return self.replies.pop(0) if self.replies else None

    @property
    def ended(self) -> bool:
        return self.gone and not self.replies

    def close(self) -> None:
        self.gone = True


def test_once_the_window_closes_terminal_keeps_back_and_the_answer_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Wizard sets Back and the answer before on the window's face; closed, the same question goes to Terminal,
    whose face gets both: b goes back there, and "Your answer: …. Enter keeps it." shows."""
    from local_assistant.setup_face import Back

    window = Scripted({"t": "closed"})
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    plain = PlainFace(readers())
    face = setup_window.WindowFace(window, wizard, lambda: plain)  # type: ignore[arg-type]
    face.back, face.previous = True, "male"
    monkeypatch.setattr("builtins.input", lambda prompt="": "b")
    with pytest.raises(Back):
        face.choose(asked("voice"), None)
    assert plain.back is True and plain.previous == "male"
    assert prompts.SETUP_BACK_KEPT.format(shown="Male") in capsys.readouterr().out
    face.back, face.previous = False, None  # the Wizard's next question: Terminal's face follows
    assert plain.back is False and plain.previous is None


def test_the_windows_count_drops_the_questions_a_changed_route_no_longer_asks(tmp_path: Path) -> None:
    """Back to the route, then an account without a number: her number and the Registration Lock no longer count in
    "n of m", as on a face that never showed them."""

    def counted(face: setup_window.WindowFace, wizard: Wizard, qids: list[str]) -> list[tuple[int, int]]:
        return [face._steps(question(qid).at(wizard.known, wizard.mac, WORDS)) for qid in qids]

    def a_face(route: str) -> tuple[Wizard, setup_window.WindowFace]:
        wizard = Wizard(tmp_path / "c.toml", dry_run=False)
        wizard.fixed_mac, wizard.planning = Mac(), True
        wizard.planned.update({"name": "Maya", "signal_app": True, "route": route})
        return wizard, setup_window.WindowFace(Scripted(), wizard, lambda: PlainFace(readers()))  # type: ignore[arg-type]

    wizard, changed = a_face("number")
    counted(changed, wizard, ["name", "signal_app", "route", "number", "lock"])
    wizard.planned.update({"route": "no_number"})
    ids = [q.id for q in QUESTIONS]
    after = next(q.id for q in QUESTIONS[ids.index("lock") + 1 :] if q.plan and q.shows(wizard.known, wizard.mac))
    fresh_wizard, fresh = a_face("no_number")
    assert (
        counted(changed, wizard, ["route", after])
        == counted(fresh, fresh_wizard, ["name", "signal_app", "route", after])[2:]
    )


def test_the_sms_code_and_the_captcha_have_a_quiet_way_out_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Terminal's words offer q at both; the window had only Continue, off until the code or the link was in: its Stop
    here ends setup as q does, and the page's last screen says how to go on (after registration too)."""
    window = Scripted({"t": "stop"})
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.face = setup_window.WindowFace(window, wizard, lambda: PlainFace(readers()))  # type: ignore[arg-type]

    def run_setup(argv: list[str] | None = None) -> int:
        wizard.verify_number("+306900000000")
        return 0

    monkeypatch.setattr(setup, "run_setup", run_setup)
    assert setup.main([]) == 130
    code = next(m for m in window.messages if m["t"] == "screen" and m["id"] == "sms_code")
    assert (
        code["stop"]
        == prompts.SETUP_CODE_STOP
        == (  # signal-no-sms-loop: Signal's limit and the way out named
            "No SMS? Stop here: running setup again asks Signal for a new code; Signal limits how many it sends.\n"
            "A SIM that gets no SMS: scripts/setup.sh --redo signal, for a Signal Account without a number."
        )
    )
    assert capsys.readouterr().out.strip().splitlines()[-1] == prompts.SETUP_STOPPED
    captcha = window_screen(asked("captcha"))
    assert captcha["stop"] == prompts.SETUP_CAPTCHA_STOP == "Stuck? Stop here: running setup again asks for a new link."
    assert not question("sms_code").stop_typed and not question("captcha").stop_typed  # Terminal says q already
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    assert 'stopped: "Stopped. Your answers are kept; running scripts/setup.sh again goes on."' in script


# ------------------------------------------------------------------ 9. no command typed: Press Enter to run it


def recorded(monkeypatch: pytest.MonkeyPatch, wizard: Wizard) -> list[list[str]]:
    ran: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> object:
        import subprocess

        ran.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(wizard, "run", run)
    return ran


@pytest.mark.parametrize(("typed", "runs"), [("", True), ("s", False), ("skip", False), (None, False)])
def test_a_command_is_shown_then_enter_runs_it_or_s_skips_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], typed: str | None, runs: bool
) -> None:
    """Owner, 27 Oct ("pre-fill the command so that the user does not have to type?"): a program cannot type into the
    Terminal's command line, so setup shows the command, asks "Press Enter to run it (or s to skip)" and runs it."""
    asked: list[str] = []

    def answer(prompt: str = "") -> str:
        asked.append(prompt)
        if typed is None:
            raise EOFError
        return typed

    monkeypatch.setattr("builtins.input", answer)
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    ran = recorded(monkeypatch, wizard)
    assert wizard.offer(["/usr/bin/true", "--flag"]) is runs
    assert ran == ([["/usr/bin/true", "--flag"]] if runs else [])
    assert (
        asked == [f"{prompts.SETUP_PRESS_ENTER} "]
        and prompts.SETUP_PRESS_ENTER == "Press Enter to run it (or s to skip)"
    )
    out = capsys.readouterr().out
    assert "  $ /usr/bin/true --flag\n" in out and (prompts.SETUP_OFFER_SKIPPED in out) is not runs


@pytest.mark.parametrize("typed", ["n", "no", "q", "N"])
def test_a_no_at_a_command_skips_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], typed: str
) -> None:
    """n, no or q mean "not this one": skipped at once, never asked again so that a following Enter runs it."""
    answers = iter([typed, ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    ran = recorded(monkeypatch, wizard)
    assert wizard.offer(["/usr/bin/true"]) is False
    assert ran == [] and prompts.SETUP_OFFER_SKIPPED in capsys.readouterr().out


def test_an_unknown_answer_at_a_command_says_which_keys_do_what(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(["maybe", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    ran = recorded(monkeypatch, wizard)
    assert wizard.offer(["/usr/bin/true"]) is True
    assert ran == [["/usr/bin/true"]]
    assert (
        prompts.SETUP_OFFER_HOW == "Enter runs it; s skips it." and prompts.SETUP_OFFER_HOW in capsys.readouterr().out
    )


def test_what_was_typed_during_a_command_never_answers_the_next_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An Enter typed while brew or gcloud ran is dropped before each Press Enter (Wizard.flush, as the Signal steps
    do), so it never runs the next command."""
    events: list[str] = []

    def answer(prompt: str = "") -> str:
        events.append("asked")
        return ""

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(setup, "flush_typed", lambda tty: events.append("flushed"))
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    recorded(monkeypatch, wizard)
    wizard.offer(["/usr/bin/true"])
    wizard.offer(["/usr/bin/false"])
    assert events == ["flushed", "asked", "flushed", "asked"]


def test_gum_says_how_back_works_in_its_menus_and_shows_each_extras_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """In gum's menus b types nothing: Back is the list's last item; and a list whose options have a line of their own
    shows them (the Extra Features' switches too)."""
    face = GumFace(readers(), Path("/nonexistent/gum"))
    face.back = True
    face.show(asked("voice"), None)
    out = capsys.readouterr().out
    assert prompts.SETUP_BACK_HOW_MENU in out and prompts.SETUP_BACK_HOW not in out
    assert prompts.SETUP_BACK_HOW_MENU == (  # within the plan only (second deep scan, dialogue-back-at-any-question)
        "Back goes back one question until setup starts installing: the last item of a list, or b where you type. "
        "Your answers are kept."
    )
    face.show(asked("extras"), None)
    assert prompts.SETUP_FEATURE_LINES["maps"] in capsys.readouterr().out


def test_a_dry_run_shows_the_command_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.setup_helpers import refuse

    monkeypatch.setattr("builtins.input", refuse)
    wizard = Wizard(tmp_path / "c.toml", dry_run=True)
    assert wizard.offer(["/usr/bin/true"]) is True
    out = capsys.readouterr().out
    assert "  $ /usr/bin/true" in out and prompts.SETUP_PRESS_ENTER in out


def test_in_the_window_a_command_is_offered_on_its_own_screen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner, 29 Oct: no "Press Enter" in Terminal while the window is open: the command with Copy, Run it or Skip."""
    from tests.test_setup_admin import Scripted

    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    window = Scripted(answers=[True])
    wizard.face = setup_window.WindowFace(window, wizard, lambda: PlainFace(readers()))  # type: ignore[arg-type]
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("Terminal asked"))
    ran = recorded(monkeypatch, wizard)
    assert wizard.offer(["/usr/bin/true"])
    assert ran == [["/usr/bin/true"]] and [m["id"] for m in window.sent("screen")] == ["offer_run"]
    assert not window.sent("terminal")


def test_in_her_own_user_her_config_lines_open_her_config_and_she_restarts_once(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Her config is hers alone: setup shows the lines, opens it as her on Enter, and restarts her once, at the end,
    however many parts changed (chords and places and routes here)."""
    from local_assistant import ownuser
    from local_assistant.hostsec import SUDO
    from tests.setup_helpers import her_own_user

    her_own_user(no_system_paths)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    ran = recorded(monkeypatch, wizard)
    wizard.chords_extra()
    wizard.maps_extra()
    wizard.restart_her()
    estia = str(ownuser.BIN / "estia")
    assert ran == [[SUDO, estia, "config"], [SUDO, estia, "config"], [SUDO, estia, "restart"]]
    out = capsys.readouterr().out
    assert "    [chords]\n" in out and "    [maps]\n" in out and out.count(prompts.SETUP_PRESS_ENTER) == 0  # input's
    wizard.restart_her()
    assert len(ran) == 3  # once


def test_no_line_of_setup_asks_you_to_type_a_command_you_could_skip() -> None:
    """The personality lines name the guide: a default Enter would replace her built-in style with the example's."""
    for text in (prompts.SETUP_PERSONALITY, prompts.SETUP_PERSONALITY_HERS):
        assert "/usr/bin/sudo" not in text and "cp soul.example.md" not in text and "setup_assistant.md, 2.5" in text
    for text in (
        prompts.SETUP_STYLE_HERS,
        prompts.SETUP_CHORDS_HERS,
        prompts.SETUP_MAPS_HERS,
        prompts.SETUP_PROJECTS_HERS,
        prompts.SETUP_CALLS_HERS,
        prompts.SETUP_EXTRAS_HERS,
    ):
        assert "/usr/bin/sudo" not in text, text  # setup runs those, on Enter


# ------------------------------------------------------------------ 11. each key's own screen guides like a person


STEP = re.compile(r"^\s+(\d)\. ")


@pytest.mark.parametrize("qid", ["ollama_key", "brave_key", "captcha", "spotify_id", "mail_ids"])
def test_each_key_screen_has_3_to_5_numbered_short_steps_its_link_and_later(qid: str) -> None:
    """Owner, 27 Oct: "clear step instructions on how to do it... url, steps etc, like you would guide me, be concise
    in all text": 3 to 5 numbered steps, one short line each (a line under a step goes on with it), the address as a
    link, "Later" to skip (the captcha apart: Signal waits for it), the same steps in Terminal."""
    q = asked(qid)
    for text in (q.lead, q.window_lead or q.lead):
        steps = [int(m.group(1)) for line in text.splitlines() if (m := STEP.match(line))]
        assert 3 <= len(steps) <= 5 and steps == list(range(1, len(steps) + 1)), (qid, steps)
        assert all(len(line) <= 100 for line in text.splitlines()), qid
        assert "https://" in text, qid
    screen = window_screen(q)
    assert screen["links"], qid
    if qid != "captcha":
        assert screen["later"] == prompts.SETUP_WINDOW_LATER, qid
    assert "Recommend" not in q.ask


def test_brave_says_its_cost_inside_the_claim() -> None:
    lead = " ".join(plain_text(asked("brave_key").lead).split())
    assert "free with a $5 monthly credit" in lead and "needs a card" in lead and "Estia stops at 900" in lead


def test_the_site_words_were_checked_on_the_live_sites_and_say_so() -> None:
    """Button and menu names as the sites show them, checked 27 Oct 2026 (the docs say which could not be read)."""
    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    assert guide.count("checked live 27 Oct 2026") >= 4
    assert "Add API Key" in prompts.SETUP_BRAVE_KEY and "Create an app" in prompts.SETUP_SPOTIFY_ID
    assert "New registration" in prompts.SETUP_MAIL_IDS and "Open Signal" in prompts.SETUP_CAPTCHA


def first_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: dict[str, object]) -> Wizard:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    wizard = Wizard(tmp_path / "cfg" / "config.toml", dry_run=False)
    wizard.planned.update(answers)  # type: ignore[arg-type]
    wizard.fixed_mac = Mac()
    return wizard


SPOTIFY = "0123456789abcdef0123456789abcde0"
TENANT, CLIENT = "11111111-2222-4333-8444-555555555555", "66666666-7777-4888-9999-000000000000"


def test_spotify_and_mail_ids_are_asked_in_the_plan_when_picked() -> None:
    class Picked:
        def __init__(self, extras: tuple[str, ...]) -> None:
            self.extras = extras

        def get(self, qid: str) -> object:
            return self.extras if qid == "extras" else None

        def kept(self, qid: str) -> bool:
            return False

    spotify, mail = question("spotify_id"), question("mail_ids")
    assert spotify.plan and mail.plan
    assert spotify.shows(Picked(("music",)), Mac()) and not spotify.shows(Picked(("web",)), Mac())  # type: ignore[arg-type]
    assert mail.shows(Picked(("mail",)), Mac()) and not mail.shows(Picked(("mail",)), Mac(configured=True))  # type: ignore[arg-type]
    check = spotify.check
    assert check is not None
    assert check(SPOTIFY.upper(), Picked(()), Mac()).value == SPOTIFY  # type: ignore[arg-type]
    assert check("", Picked(()), Mac()).value == "" and check("abc", Picked(()), Mac()).value is None  # type: ignore[arg-type]
    ids = mail.check
    assert ids is not None
    given = {"tenant": TENANT.upper(), "client": CLIENT, "address": "Alex@Example.com"}
    assert ids(given, Picked(()), Mac()).value == {"tenant": TENANT, "client": CLIENT, "address": "alex@example.com"}  # type: ignore[arg-type]
    assert ids({}, Picked(()), Mac()).value == {}  # type: ignore[arg-type]
    assert ids({"tenant": TENANT}, Picked(()), Mac()).value is None  # type: ignore[arg-type]


def test_the_config_step_writes_spotify_mail_and_calendar_for_the_ids_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tomllib

    from local_assistant.config import parse_config
    from local_assistant.setup import render_config
    from tests.setup_helpers import ACI, BOT, EXAMPLE

    wizard = first_install(
        tmp_path,
        monkeypatch,
        {
            "extras": ("mail", "calendar", "music"),
            "spotify_id": SPOTIFY,
            "mail_ids": {"tenant": TENANT, "client": CLIENT, "address": "alex@example.com"},
        },
    )
    text = wizard.extra_sections(render_config(EXAMPLE, BOT, ACI))
    cfg = parse_config(tomllib.loads(text))
    assert cfg.spotify_client_id == SPOTIFY and cfg.mail_client_id == CLIENT
    assert [(a.name, a.address) for a in cfg.mail_accounts] == [("mail", "alex@example.com")]
    assert cfg.calendar_enabled
    assert {"spotify", "mail", "calendar"} <= wizard.sections_written


def test_each_extra_step_runs_its_sign_in_on_enter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calendars, Spotify, mail, Google Maps, lights: their steps in Terminal, then each command on Enter."""
    from local_assistant import setup_extras

    wizard = first_install(
        tmp_path,
        monkeypatch,
        {
            "extras": ("mail", "google-maps", "calendar", "music", "lights"),
            "spotify_id": SPOTIFY,
            "mail_ids": {"tenant": TENANT, "client": CLIENT, "address": "alex@example.com"},
        },
    )
    wizard.sections_written = frozenset({"mail", "spotify", "calendar", "maps"})
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    ran = recorded(monkeypatch, wizard)

    def which(name: str) -> str | None:  # no gcloud until Homebrew's install of it ran
        installed = any(command[1:] == ["install", "--cask", "gcloud-cli"] for command in ran)
        return "/opt/homebrew/bin/gcloud" if name == "gcloud" and installed else None

    monkeypatch.setattr("shutil.which", which)
    wizard.extras_setup()
    names = [Path(command[0]).name if "/" in command[0] else command[0] for command in ran]
    assert names == [
        "mail-login.sh",
        "brew",  # brew install --cask gcloud-cli
        "gcloud",
        "setup-google-maps.sh",
        "set-calendar-url.sh",
        "spotify-login.sh",
        "tapo-discover.sh",
        "set-tapo-credentials.sh",
        "hue-pair.sh",
    ]
    assert ran[0][1:] == ["alex@example.com"] and ran[1][1:] == ["install", "--cask", "gcloud-cli"]
    assert ran[2][1:] == ["auth", "login", "--no-launch-browser"]
    assert setup_extras.REPO.name in ran[3][0] or ran[3][0].endswith("scripts/setup-google-maps.sh")


def test_fields_can_be_left_for_later_in_the_window() -> None:
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    assert 'answer(screen.kind === "fields" ? {} : "")' in script


# ------------------------------------------------------------------ the same fact, the same words


def test_the_same_fact_keeps_the_same_words_on_every_screen() -> None:
    """One list, one name ("Extra Features"); a web key left for later says later, as Ollama's and the window's do;
    mail's switch where the error names it; the hidden key described once; the window's answer before in Terminal's
    words ("Your answer: …")."""
    assert prompts.SETUP_EXTRAS.startswith("Extra Features (all optional)")
    assert "  4. Paste it below and press Enter. Enter alone: later." in prompts.SETUP_BRAVE_KEY
    assert prompts.SETUP_BRAVE_KEY_ASK == "Brave's key (hidden; Enter: later): "
    assert prompts.MAIL_STEPS[1].startswith("Authentication, Advanced settings: turn on Allow public client flows.")
    web_steps = prompts.SETUP_GUIDES["web"][1]
    assert web_steps[2] == "Paste it at setup's question: it is typed hidden and never kept with your answers."
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    assert 'answerBefore: "Your answer: "' in script and "WORDS.answerBefore + keptText(screen)" in script
    assert '"Kept: "' not in script
    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    assert "then Brave's (Enter: later)" in " ".join(guide.split())


def test_google_maps_without_gcloud_leaves_its_steps_for_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No gcloud and its install skipped: no bare "gcloud" is offered (Enter there ended setup with a traceback, before
    her restart and the check); its steps by hand instead. A program that is not there is one line, through the real
    run()."""
    from local_assistant.setup_extras import EXTRAS

    wizard = first_install(tmp_path, monkeypatch, {"extras": ("google-maps",)})
    wizard.sections_written = frozenset({"maps"})
    monkeypatch.setattr("shutil.which", lambda name: None)
    answers = iter(["s"])  # the install skipped
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers, ""))
    wizard.extras_setup()
    out = capsys.readouterr().out
    assert "auth login" not in out and "setup-google-maps.sh\n" not in out.replace(EXTRAS["google-maps"].how, "")
    assert prompts.SETUP_ID_LATER.format(name="Google Maps") in out and EXTRAS["google-maps"].how in out
    program = str(tmp_path / "no-such-program")
    assert wizard.offer([program]) is False
    assert f"✗ {program}: not run (No such file or directory)." in capsys.readouterr().out


def test_extras_in_her_own_user_restarts_her_once_her_config_was_opened(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scripts/setup.sh --extras` said "she restarts once, at the end" and ended without the restart."""
    import subprocess

    from local_assistant import ownuser
    from local_assistant.hostsec import SUDO
    from tests.setup_helpers import her_own_user

    her_own_user(no_system_paths)
    ran: list[list[str]] = []

    def run(wizard: Wizard, command: list[str], **kwargs: object) -> object:
        ran.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(Wizard, "run", run)
    answers = iter(["chords"])  # the extras' question, then Enter at each command
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers, ""))
    assert setup.main(["--extras", "--config", str(tmp_path / "config.toml")]) == 0
    estia = str(ownuser.BIN / "estia")
    assert ran == [[SUDO, estia, "config"], [SUDO, estia, "restart"]]


def test_a_style_kept_from_before_opens_no_config_and_restarts_nothing(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """In her own macOS account each re-run's end showed the style lines and offered her config for a style kept from
    before: Enter opened nano for nothing, and a restart followed. Only a style given this run offers it."""
    from local_assistant import setup_answers
    from tests.setup_helpers import her_own_user

    her_own_user(no_system_paths)
    earlier = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    earlier.set("style", "playful")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    wizard.answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    ran = recorded(monkeypatch, wizard)
    wizard.personality()
    wizard.restart_her()
    out = capsys.readouterr().out
    assert ran == [] and '    style = "playful"' not in out and "Kept: Style: playful." in out
    assert prompts.SETUP_PERSONALITY_HERS.format(name="Estia") in out


# ------------------------------------------------------------------ the tests' own guards (review CODE-12)


def test_no_test_runs_homebrew_gcloud_or_the_repos_scripts_through_setup(tmp_path: Path) -> None:
    """A test that presses Enter at an offer without its own stand-in for run() would really run brew, gcloud or
    hue-pair.sh: the suite refuses them, and sudo inside a shell's command line too."""
    import subprocess

    wizard = Wizard(tmp_path / "c.toml", dry_run=False)
    for command in (
        ["/opt/homebrew/bin/brew", "install", "x"],
        ["gcloud", "auth"],
        [str(setup.REPO / "scripts/hue-pair.sh")],
    ):
        with pytest.raises(AssertionError, match="a test ran"):
            wizard.run(command)
    with pytest.raises(AssertionError, match="sudo"):
        subprocess.run(["/bin/sh", "-c", "true && /usr/bin/sudo -n true"], check=False)
    with pytest.raises(AssertionError, match="sudo"):
        subprocess.run("cd /tmp\nsudo -n true", shell=True, check=False)
    said = subprocess.run(["/bin/sh", "-c", "echo start her with /usr/bin/sudo"], capture_output=True, check=False)
    assert said.returncode == 0  # a word an echo prints is no command
    assert Wizard(tmp_path / "c.toml", dry_run=True).run(["brew", "install", "x"]).returncode == 0  # runs nothing


@pytest.mark.runs_commands
def test_a_test_that_says_so_may_run_its_own_stand_in_named_brew(tmp_path: Path) -> None:
    brew = tmp_path / "brew"
    brew.write_text("#!/bin/sh\nexit 0\n")
    brew.chmod(0o755)
    assert Wizard(tmp_path / "c.toml", dry_run=False).run([str(brew), "--version"]).returncode == 0


# ------------------------------------------------------------------ an ID left for later


def test_an_id_left_for_later_opens_no_config_and_restarts_nothing(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spotify's Client ID left for later (Enter): nothing can go into her config without it, so setup neither opens
    her config nor restarts her for it; a re-run's kept line says later, never an empty answer."""
    from tests.setup_helpers import her_own_user

    her_own_user(no_system_paths)
    wizard = first_install(tmp_path, monkeypatch, {"extras": ("music",), "spotify_id": ""})
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    ran = recorded(monkeypatch, wizard)
    wizard.extras_setup()
    wizard.restart_her()
    assert not any(command[-1] in ("config", "restart") for command in ran), ran
    assert prompts.SETUP_ID_LATER.format(name="Spotify") in capsys.readouterr().out
    assert question("spotify_id").shown_value("") == "later"
    assert prompts.SETUP_HER_RESTART == "Her config was opened: a restart reads any change."


# ------------------------------------------------------------------ --extras and a re-run: each pick's steps on Enter


@pytest.mark.parametrize("picked", ["music lights", "web", "calendar"])
def test_extras_runs_each_picks_steps_on_enter_never_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], picked: str
) -> None:
    """Owner, 27 Oct (note 9: every printed command shown, then run on Enter): `--extras` lists each extra in one line,
    then each pick's steps one at a time, as the plan's pick has them: lights' three scripts on Enter, web's keys
    asked hidden; no line to type with admin rights or the login keychain's tool."""
    import subprocess

    from local_assistant.setup_extras import EXTRAS, FEATURES

    ran: list[list[str]] = []

    def run(wizard: Wizard, command: list[str], **kwargs: object) -> object:
        ran.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(Wizard, "run", run)
    monkeypatch.setattr("local_assistant.setup.read_hidden", lambda prompt, tty=None: "")  # each key: later
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    answers = iter([picked])
    asked: list[str] = []

    def answer(prompt: str = "") -> str:
        asked.append(prompt.strip())
        return next(answers, "")

    monkeypatch.setattr("builtins.input", answer)
    assert setup.main(["--extras", "--config", str(tmp_path / "config.toml")]) == 0
    out = capsys.readouterr().out
    listed = [line for line in out.splitlines() if re.match(r"^\s+\d+\. ", line)]
    assert len(listed[: len(EXTRAS)]) == len(EXTRAS) and out.startswith(prompts.SETUP_EXTRAS)
    assert "/usr/bin/sudo" not in out and "add-generic-password" not in out
    if picked == "web":
        assert prompts.SETUP_KEY_SKIPPED.format(label="Ollama's key") in out
        return
    if picked == "calendar":  # no [calendar] written here: its steps by hand, never a guide that says setup asks
        assert "Setup asks for it on your yes" not in out and EXTRAS["calendar"].how in out and ran == []
        return
    names = [Path(command[0]).name for command in ran]
    assert names == ["tapo-discover.sh", "set-tapo-credentials.sh", "hue-pair.sh"]
    assert asked.count(prompts.SETUP_PRESS_ENTER) == 3
    assert prompts.SETUP_ID_LATER.format(name="Spotify") in out and "music" in FEATURES
    assert "Paste it at setup's question" not in out  # no ID question here: its steps by hand say what to do


def test_the_documents_share_is_shown_then_runs_on_enter(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner, 27 Oct (note 9): every command setup runs for an extra is shown, then run on Enter, as SETUP_EXTRAS
    says ("each command runs only on your Enter"); s skips the share of ~/Estia-KB."""
    from tests.setup_helpers import her_own_user

    her_own_user(no_system_paths)
    wizard = first_install(tmp_path, monkeypatch, {"extras": ("documents",)})
    wizard.sections_written = frozenset({"documents"})
    asked: list[str] = []

    def answer(prompt: str = "") -> str:
        asked.append(prompt.strip())
        return "s"

    monkeypatch.setattr("builtins.input", answer)
    ran = recorded(monkeypatch, wizard)
    wizard.extras_setup()
    assert ran == [] and asked == [prompts.SETUP_PRESS_ENTER]
    assert "scripts/share-documents.sh" in capsys.readouterr().out


# ------------------------------------------------------------------ 4. short lines everywhere (review words-13)


@pytest.mark.parametrize("qid", [q.id for q in QUESTIONS])
def test_every_questions_words_fit_short_lines(qid: str) -> None:
    """Every question, in the plan or at its step: no line over 100 characters in Terminal or the window."""
    for mac in MACS:
        q = asked(qid, mac)
        for text in (q.lead, q.window_lead, q.more, q.more_typed):
            for line in plain_text(text).splitlines():
                assert len(line) <= 100, (qid, line)


def test_the_windows_yes_and_no_are_paragraphs_of_their_own() -> None:
    """The page joins a paragraph's lines into one: Yes and No each get their own, a blank line between them."""
    for text in (prompts.SETUP_WINDOW_MOVE, prompts.SETUP_WINDOW_MOVE_OLDER, prompts.SETUP_WINDOW_LOCK_HOW):
        assert "\n\nNo" in text and "Yes: " in text, text
    older = plain_text(prompts.SETUP_WINDOW_MOVE_OLDER)
    yes = (
        "Yes: admin rights once, asked by macOS in its own box; a rollback goes back within 7 days (setup_assistant.md, "
        "2.7)."
    )
    assert "Terminal" not in plain_text(prompts.SETUP_WINDOW_MOVE)  # macOS's own box asks, in the window
    assert yes in " ".join(older.split())
    assert "/usr/bin/sudo" not in older  # the window names no command to type
    assert prompts.SETUP_NUMBER_ASK_REGION == "Her number, the spare SIM's (without a +, +{code} goes in front):"
    for line in plain_text(prompts.SETUP_PROJECTS).splitlines():
        assert len(line) <= 88, line


# ------------------------------------------------------------------ the guides' devices and links (review words-12/14)


def test_the_extras_words_the_window_shows_name_no_enter_key() -> None:
    """c-9 (round 3): the window showed Terminal's words ("Setup asks for it on your Enter"), where a step runs on
    Run it. The guides and the ID screens both faces show say "on your yes"; Google Maps, set up in Terminal in 0.9.8
    (owner's answer 6), says where it runs."""
    texts = [
        prompts.SETUP_EXTRAS,
        prompts.SETUP_SPOTIFY_ID,
        prompts.SETUP_WINDOW_SPOTIFY_ID,
        prompts.SETUP_MAIL_IDS,
        prompts.SETUP_WINDOW_MAIL_IDS,
        *(step for _needs, steps in prompts.SETUP_GUIDES.values() for step in steps),
    ]
    assert not [text for text in texts if "your Enter" in text]
    maps_needs, maps_steps = prompts.SETUP_GUIDES["google-maps"]
    assert "Set up in Terminal" in maps_needs and "after setup" in maps_needs
    assert not any(step.startswith("Setup installs") for step in maps_steps)


def test_the_guides_name_the_device_to_use_and_link_each_site() -> None:
    """Over SSH the device that can paste into this Terminal is the computer typed on: the captcha's first step names
    a computer (a phone on the same Wi-Fi stays: the owner decides); the calendar's and Google's billing steps give
    their https link, and the calendar's says setup asks for the address on your yes (the briefing: tests/test_docs.py)."""
    assert "  1. On this Mac, or a computer or phone on the same Wi-Fi, open\n" in plain_text(
        prompts.SETUP_CAPTCHA_STEPS
    )
    calendar = prompts.SETUP_GUIDES["calendar"][1]
    assert calendar[0].startswith("At https://calendar.google.com on a computer: the gear, then Settings;")
    assert calendar[2] == "Setup asks for it on your yes: paste it there, hidden."
    billing = prompts.SETUP_GUIDES["google-maps"][1][1]  # after gcloud and its sign-in, one step (c-9, round 3)
    assert "https://console.cloud.google.com/billing" in billing and "Create account, with a card." in billing


def test_the_windows_admin_run_that_never_started_names_no_cause_it_cannot_know() -> None:
    """Review W10: the wrapper's own failure after a password that worked (exit 120) is `unrun` too, yet the words
    said "macOS's own box could not ask". The lines now say what is true for each cause: the window could not run the
    command with admin rights."""
    for text in (
        prompts.SETUP_WINDOW_NO_BOX,
        prompts.SETUP_WINDOW_NO_BOX_TERMINAL,
        prompts.SETUP_AI_NO_BOX,
        prompts.SETUP_AI_NO_BOX_WINDOW,
    ):
        assert "could not ask" not in text, text
        assert "could not run this command with admin rights" in text, text
    assert prompts.SETUP_WINDOW_NO_BOX.endswith(
        "\n\nThe window could not run this command with admin rights: Terminal asks for your Mac password there. "
        "Not in front? **Switch to Terminal**."
    )


def test_an_ai_runs_stop_screen_names_the_folder_with_its_command() -> None:
    """Words review, 30 Oct: after an AI-run a new Terminal window opens in the home folder, where scripts/setup.sh
    is not found; the stop screen copies the command with its folder, as the page's own stop line does. The fix
    review (dialogue-06) builds that command from the clone's real path: the usual clone's is shown here."""
    script = (PAGE / "page.js").read_text(encoding="utf-8")
    command = setup_window.setup_again_command(Path.home() / "estia")
    assert prompts.SETUP_WINDOW_STOP_AGAIN_AI.format(command=command).endswith("\n$ cd ~/estia && scripts/setup.sh")
    assert "$ cd ~/estia && scripts/setup.sh" in script
    assert prompts.SETUP_WINDOW_STOP_AGAIN.endswith("in Terminal:\n$ scripts/setup.sh")  # Terminal's own folder
    # the merge of the fix rounds: one wording of the kept answers on both stop screens, the AI-run's (page.js
    # WORDS.ended's, dialogue-06) and Terminal's
    for stop in (prompts.SETUP_WINDOW_STOP_AGAIN, prompts.SETUP_WINDOW_STOP_AGAIN_AI):
        assert stop.startswith("Setup stopped here; every answer is kept. To go on, "), stop


def test_setup_window_no_above() -> None:
    """Review W15: a stop's screen and the closing list draw the log below their words, yet they said "the ✗ above
    says why": the friend scrolled up, found only the heading, and closed the window. The stops and closing lines
    name no direction, true in both faces; so do the steps' lines that send to "what it says above"."""
    texts = {
        "SETUP_MOVE_FAILED": prompts.SETUP_MOVE_FAILED,
        "SETUP_CHECK_FAILED": prompts.SETUP_CHECK_FAILED,
        "SETUP_CHECK_NOT_RUN": prompts.SETUP_CHECK_NOT_RUN,
        "SETUP_SECURITY_FAILED": prompts.SETUP_SECURITY_FAILED,
        "OLLAMA_SERVICE_FAILED": prompts.OLLAMA_SERVICE_FAILED,
        "SETUP_CALLS_STOPPED": prompts.SETUP_CALLS_STOPPED,
        "SETUP_EXTRA_FAILED": prompts.SETUP_EXTRA_FAILED,
    }
    assert not [name for name, text in texts.items() if "above" in text]
    assert "its own ✗ line says why" in prompts.SETUP_MOVE_FAILED  # as an AI-run's stop of a step says it
    assert prompts.SETUP_CHECK_FAILED == "✗ The check found a problem: its ✗ lines say what to fix."


def test_the_intro_says_where_macos_asks_as_the_window_does() -> None:
    """Review W01: the spoken and captioned intro said "When admin rights are needed, macOS asks in Terminal.", yet
    every admin command in the window goes through macOS's own box ("Estia Setup wants to make changes."), and an
    AI-run has no Terminal: a careful friend cancelled the box as suspicious. Re-recorded in af_heart (30 Oct)."""
    data = (PAGE / "intro-data.js").read_text(encoding="utf-8")
    assert "in Terminal" not in data
    assert '"When admin rights are needed, macOS asks in its own box."]' in data
    assert "asked by macOS in its own box" in prompts.SETUP_WINDOW_MOVE  # the same fact, the same words

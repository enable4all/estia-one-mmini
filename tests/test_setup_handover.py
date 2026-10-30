"""What setup hands over to her first start (setup_handover): About you and the home area, written beside her config
before the move (0600, never a secret), read once by her as untrusted, each line one of her profile facts in the
user's own words, then code's note; never imported twice."""

import asyncio
import os
import stat
import time
import tomllib
from pathlib import Path

import pytest

from local_assistant import prompts, setup_handover
from local_assistant.config import load_config
from local_assistant.setup import Wizard
from tests.fakes.fake_gemma import FakeGemma, text
from tests.setup_helpers import ACI, BOT
from tests.test_app import code_notes_in, deliver, sends_work_again, sent, start, stop, until

ABOUT = {
    "name": "Alex",
    "work": "Nurse, night shifts.",
    "people": "Maria (wife), Nikos (son, 9).",
    "interests": "Jazz, cycling, philosophy.",
    "day": "Up at 7, work 9 to 5, gym on Tuesdays.",
}
HOME = {"street": "Example Street", "city": "Example Town"}
FACTS = [
    "About: The user goes by Alex.",
    "Work: Nurse, night shifts.",
    "Family: Maria (wife), Nikos (son, 9).",
    "Other: Interests: Jazz, cycling, philosophy.",
    "About: Usual day: Up at 7, work 9 to 5, gym on Tuesdays.",
    "Home: Home area: Example Street, Example Town",
]


def handed(tmp_path: Path, about: dict[str, str] = ABOUT, home: dict[str, str] = HOME) -> Path:
    folder = tmp_path / "cfg"
    folder.mkdir(mode=0o700, exist_ok=True)
    setup_handover.write(folder, about, home)
    return folder / setup_handover.HANDOVER


def test_the_handover_is_her_private_file_with_no_secret(tmp_path: Path) -> None:
    path = handed(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data == {"format": 1, "imported": False, "about_you": ABOUT, "home_area": HOME}
    assert path.read_text(encoding="utf-8").startswith("# ")  # says what it is, for whoever opens it
    assert [p.name for p in path.parent.iterdir()] == [setup_handover.HANDOVER]  # no temporary file left


def test_each_line_becomes_a_fact_in_its_category(tmp_path: Path) -> None:
    assert setup_handover.facts(handed(tmp_path)) == FACTS


def test_interests_that_are_all_music_are_music(tmp_path: Path) -> None:
    path = handed(tmp_path, {**ABOUT, "interests": "Jazz and blues, piano"})
    assert "Music: Interests: Jazz and blues, piano" in (setup_handover.facts(path) or [])


def test_an_empty_line_gives_no_fact_and_nothing_at_all_gives_no_file(tmp_path: Path) -> None:
    path = handed(tmp_path, {key: "" for key in ABOUT} | {"work": "Teacher"}, {})
    assert setup_handover.facts(path) == ["Work: Teacher"]
    folder = tmp_path / "empty"
    folder.mkdir()
    setup_handover.write(folder, {key: "" for key in ABOUT}, {})
    assert not (folder / setup_handover.HANDOVER).exists()


def test_a_name_typed_with_a_full_stop_reads_once() -> None:
    assert setup_handover.fact_texts({"name": "Alex."}, {}) == ["About: The user goes by Alex."]


@pytest.mark.parametrize("problem", ["link", "group", "large", "folder", "imported", "bad toml", "format"])
def test_a_file_she_cannot_trust_is_read_as_none(tmp_path: Path, problem: str) -> None:
    path = handed(tmp_path)
    if problem == "link":
        real = tmp_path / "elsewhere.toml"
        path.rename(real)
        path.symlink_to(real)
    elif problem == "group":
        path.chmod(0o640)
    elif problem == "large":
        path.write_text(path.read_text() + "#" * setup_handover.MAX_BYTES)
    elif problem == "folder":
        path.unlink()
        path.mkdir()
    elif problem == "imported":
        setup_handover.mark_imported(path)
    elif problem == "bad toml":
        path.write_text("format = [")
    else:
        path.write_text(path.read_text().replace("format = 1", "format = 2"))
    assert setup_handover.facts(path) is None


@pytest.mark.parametrize(
    ("problem", "check"),
    [
        ("link", "a link"),
        ("group", "readable by others"),
        ("large", "over 16 KB"),
        ("folder", "not a regular file"),
        ("imported", "imported already"),
        ("bad toml", "not valid TOML"),
        ("format", "another format"),
        ("owner", "another account's"),
    ],
)
def test_a_file_that_fails_a_check_is_skipped_with_one_line_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, problem: str, check: str
) -> None:
    """Review of 0.9.8, memory-5: a handover that failed a check was skipped without a word, so About you seemed lost.
    One line in her log names the check (metadata only: never the file's words or its path)."""
    path = handed(tmp_path)
    if problem == "link":
        real = tmp_path / "elsewhere.toml"
        path.rename(real)
        path.symlink_to(real)
    elif problem == "group":
        path.chmod(0o640)
    elif problem == "large":
        path.write_text(path.read_text() + "#" * setup_handover.MAX_BYTES)
    elif problem == "folder":
        path.unlink()
        path.mkdir()
    elif problem == "imported":
        setup_handover.mark_imported(path)
    elif problem == "bad toml":
        path.write_text("format = [")
    elif problem == "format":
        path.write_text(path.read_text().replace("format = 1", "format = 2"))
    else:
        monkeypatch.setattr(setup_handover, "_uid", lambda: os.geteuid() + 1)
    caplog.set_level("INFO", logger="local_assistant")
    assert setup_handover.facts(path) is None
    [line] = [record.getMessage() for record in caplog.records]
    assert line == f"setup's handover skipped: {check}"


def test_no_handover_file_is_no_line(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Nothing filled in, or an install from before setup handed anything over: no file, nothing to say."""
    caplog.set_level("INFO", logger="local_assistant")
    assert setup_handover.facts(tmp_path / setup_handover.HANDOVER) is None
    assert caplog.records == []


def test_another_owners_file_is_read_as_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = handed(tmp_path)
    monkeypatch.setattr(setup_handover, "_uid", lambda: os.geteuid() + 1)
    assert setup_handover.facts(path) is None


def test_marked_imported_keeps_no_copy_of_the_facts(tmp_path: Path) -> None:
    """Review SEC-10: once imported, the facts are hers in /profile, where each can be changed or removed: the file
    keeps only its flag, so no second copy of them stays on disk."""
    path = handed(tmp_path)
    setup_handover.mark_imported(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["imported"] is True and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not any(data.get("about_you", {}).values()) and not data.get("home_area")
    text = path.read_text(encoding="utf-8")
    assert "Nurse" not in text and "Example Street" not in text and setup_handover.facts(path) is None


def test_setup_hands_over_right_after_the_config_on_a_first_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wizard = Wizard(tmp_path / "cfg" / "config.toml", dry_run=False)
    wizard.config_path.parent.mkdir(mode=0o700)
    wizard.planned.update({"about_you": ABOUT, "home_area": HOME})
    wizard.hand_over()
    path = wizard.config_path.parent / setup_handover.HANDOVER
    assert setup_handover.facts(path) == FACTS
    assert prompts.SETUP_HANDED_OVER.format(path=path) in capsys.readouterr().out


def test_once_handed_over_setups_answers_file_keeps_only_a_mark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reviews SEC-2 and SEC-10: About you and the home area are written beside her config; setup's own answers file
    then keeps a mark in their place, never a second copy (the file an AI may be asked about). A re-run shows the mark
    as kept, and names /profile for a change: they reach her only once (Fable, 27 Oct)."""
    from local_assistant import setup_answers
    from local_assistant.setup_plan import QUESTIONS, Mac

    answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    answers.set("about_you", ABOUT)
    answers.set("home_area", HOME)
    answers.set("name", "Maya")
    wizard = Wizard(tmp_path / "cfg" / "config.toml", dry_run=False, answers=answers)
    wizard.config_path.parent.mkdir(mode=0o700)
    wizard.planned.update({"about_you": ABOUT, "home_area": HOME})
    wizard.hand_over()
    text = setup_answers.default_path().read_text(encoding="utf-8")
    assert "Nurse" not in text and "Example Street" not in text and "Maya" in text
    again = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    assert again.get("about_you") == again.get("home_area") == setup_answers.HANDED
    said: list[str] = []
    again.recheck(QUESTIONS, Mac(), say=said.append)
    assert said == [] and again.get("about_you") == setup_answers.HANDED  # the mark fits its question's check
    rerun = Wizard(tmp_path / "cfg" / "config.toml", dry_run=False, answers=again)
    rerun.fixed_mac = Mac(country="GR")
    capsys.readouterr()
    assert rerun.answer("about_you") == setup_answers.HANDED
    out = capsys.readouterr().out
    assert prompts.SETUP_KEPT_PROFILE.format(shown=f"About you: {setup_answers.HANDED}") in out
    assert "--redo about_you" not in out  # a --redo would hand nothing over: her first start imports once
    rerun.hand_over()  # nothing new to hand over: the file beside her config stays as it was
    assert setup_handover.facts(wizard.config_path.parent / setup_handover.HANDOVER) == FACTS


def handed_answers() -> None:
    """Setup's answers file once About you and the home area were handed over: only the mark in their place."""
    from local_assistant import setup_answers

    answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    answers.set("about_you", ABOUT)
    answers.set("home_area", HOME)
    answers.mark_handed()


@pytest.mark.usefixtures("no_real_ollama", "a_terminal")
@pytest.mark.parametrize("hers", [False, True], ids=["your account", "her own user"])
def test_redo_about_you_on_an_install_set_up_names_profile_and_keeps_the_mark(
    no_system_paths: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    hers: bool,
) -> None:
    """Fable, 27 Oct: on an install already set up (in her own user too), --redo about_you dropped the mark and asked
    nothing, and no new answer could reach her: setup hands over only on a first install, and her first start imports
    once. Changes go through /profile (rule 5: her facts of the user's stay in his own words, as he last gave them;
    no root-run tool writes her files): setup says so, asks nothing and keeps the mark."""
    from local_assistant import setup_answers
    from local_assistant.setup import main
    from tests.setup_helpers import her_own_user, refuse
    from tests.test_setup_plan import GOOD, saved, stop_at_ollama, typing

    config = tmp_path / "config.toml"
    if hers:
        her_own_user(no_system_paths)
    else:
        config.write_text(GOOD, encoding="utf-8")
        config.chmod(0o600)
    handed_answers()
    for name in ("signal_account", "signal_number", "numberless", "owner", "config", "her_name", "hand_over"):
        monkeypatch.setattr(Wizard, name, refuse)
    asked = typing(monkeypatch)
    stop_at_ollama(monkeypatch)
    # answers-extras-redo-handed-full-rerun (round 1): then a whole re-run, with the Mac password; now it ends there
    assert main(["--config", str(config), "--redo", "about_you", "--redo", "home_area"]) == 0
    out = capsys.readouterr().out
    assert prompts.SETUP_REDO_PROFILE.format(title="About you") in out
    assert prompts.SETUP_REDO_PROFILE.format(title="Home area") in out
    assert out.rstrip().endswith(prompts.SETUP_REDO_NOTHING)
    assert prompts.SETUP_REDO_NOTHING == "Nothing else to change: setup ends here."
    assert asked == []
    assert saved()["about_you"] == saved()["home_area"] == setup_answers.HANDED


@pytest.mark.usefixtures("no_real_ollama", "a_terminal", "no_system_paths")
def test_redo_about_you_once_handed_over_names_profile_on_a_first_install_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Her config gone but her store kept: a new handover would be refused by her store's own key, so the mark stays
    and the line names /profile, as on an install set up."""
    from local_assistant import setup_answers
    from local_assistant.setup import main
    from tests.test_setup_plan import Stopped, saved, stop_at_ollama, typing

    handed_answers()
    typing(monkeypatch)
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "cfg" / "config.toml"), "--redo", "about_you"])
    out = capsys.readouterr().out
    assert prompts.SETUP_REDO_PROFILE.format(title="About you") in out
    assert prompts.SETUP_KEPT_PROFILE.format(shown=f"About you: {setup_answers.HANDED}") in out
    assert saved()["about_you"] == setup_answers.HANDED


def test_a_dry_run_hands_nothing_over(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wizard = Wizard(tmp_path / "cfg" / "config.toml", dry_run=True)
    wizard.planned.update({"about_you": ABOUT})
    wizard.hand_over()
    assert not (tmp_path / "cfg").exists()
    assert "(dry run) would write" in capsys.readouterr().out


def test_the_config_loaded_names_the_handover_beside_it(tmp_path: Path) -> None:
    config = tmp_path / "cfg" / "config.toml"
    config.parent.mkdir()
    config.write_text(f'bot_number = "{BOT}"\nallowed_acis = ["{ACI}"]\n', encoding="utf-8")
    config.chmod(0o600)
    assert load_config(config).setup_answers_path == tmp_path / "cfg" / "setup-answers.toml"


# ------------------------------------------------------------------ her first start: imported once, then code's note


def note_text(facts: list[str]) -> str:
    """Code's note on the facts setup kept, in P4's layout (`prompts.fact_note`): its heading in bold, one line a fact,
    then its closing, as the quiet-time note and /profile new."""
    return prompts.fact_note(prompts.SETUP_FACTS_HEADER, facts, prompts.SETUP_FACTS_CLOSING)


def test_the_setup_note_has_the_quiet_time_notes_layout() -> None:
    assert note_text(["Work: Teacher"]) == (
        "**From setup, I keep in mind**\nWork: Teacher\n/profile shows them; each can be changed or removed there."
    )


async def test_her_first_start_imports_the_facts_once_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = handed(tmp_path)
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi Alex!")), setup_answers_path=path)
    try:
        await until(lambda: harness.app.store.get_state(setup_handover.STATE_KEY) is not None)
        yours = [(fact.text, fact.source_kind) for fact in harness.app.profile.yours()]
        assert yours == [(text, "remember") for text in FACTS]  # the user's own words: the user's part
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder)[0].startswith("From setup, I keep in mind\nAbout: The user goes by Alex.")  # bold
        store = harness.app.store
        await until(lambda: store.newest_message_id() > 0 and store.message(store.newest_message_id()).status == "sent")
        note = store.message(store.newest_message_id())  # sent once signal-cli answered
        assert (note.direction, note.kind, note.status, note.text) == ("out", "note", "sent", note_text(FACTS))
        deliver(harness.folder, "hello", int(time.time() * 1000))  # now: never the offline note
        await until(lambda: len(harness.gemma.calls) == 1)
        assert any("From setup, I keep in mind" in note for note in code_notes_in(harness.gemma.calls[0]))
    finally:
        await stop(harness)
    assert tomllib.loads(path.read_text(encoding="utf-8"))["imported"] is True
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path)
    try:
        await asyncio.sleep(0.3)
        assert len(restarted.app.profile.yours()) == len(FACTS)
        assert sum(text.startswith("From setup") for text in sent(restarted.folder)) == 1  # never twice
    finally:
        await stop(restarted)


async def test_her_first_answers_know_the_facts_setup_handed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fable, 27 Oct: the profile she reads in every prompt was taken at the start, before the import, so her first
    answers knew none of the facts setup handed over (until /new or a summary). The import refreshes it."""
    path = handed(tmp_path)
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi Alex!")), setup_answers_path=path)
    try:
        await until(lambda: len(sent(harness.folder)) == 1)
        deliver(harness.folder, "hello", int(time.time() * 1000))
        await until(lambda: len(harness.gemma.calls) == 1)
        system = harness.gemma.calls[0]["messages"][0]["content"]
        assert "(no facts yet)" not in system
        assert all(f" {fact.split(': ', 1)[1]}" in system for fact in FACTS)  # each under its category
    finally:
        await stop(harness)


def setup_notes(harness: object) -> list[str]:
    """The setup notes signal-cli was asked to send, failed tries too."""
    return [text for text in sent(harness.folder) if text.startswith("From setup")]  # type: ignore[attr-defined]


async def test_a_setup_note_that_failed_is_sent_again_and_in_her_history_only_once_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fable, 27 Oct: the import was marked done before its note went out, so a send that failed once (the network
    gone for a moment) never sent it. The note is stored as code's other notes are, tried again by the resend pass,
    and reaches her history only once sent, in the words the user saw; the facts are imported once."""
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-1")
    path = handed(tmp_path)
    harness = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path)
    try:
        store = harness.app.store
        await until(lambda: store.get_state(setup_handover.STATE_KEY) is not None)
        await until(lambda: [message.status for message in store.unsent_outgoing()] == ["failed"])
        tried = len(setup_notes(harness))
        assert tried >= 1 and store.code_notes(store.current_conversation().id, 0) == []  # never seen: not hers
        await sends_work_again(harness, monkeypatch)
        await harness.app.lifecycle.resend_failed()
        await harness.app.lifecycle.resend_failed()
        assert len(setup_notes(harness)) == tried + 1  # once more, then never again
        [(note, _)] = store.code_notes(store.current_conversation().id, 0)
        assert (note.kind, note.status, note.text) == ("note", "sent", note_text(FACTS))
        assert [fact.text for fact in harness.app.profile.yours()] == FACTS  # imported once
    finally:
        await stop(harness)


async def test_a_setup_note_that_failed_goes_out_at_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-1")
    path = handed(tmp_path)
    harness = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path)
    try:
        await until(lambda: [message.status for message in harness.app.store.unsent_outgoing()] == ["failed"])
    finally:
        await stop(harness)
    tried = len(setup_notes(harness))
    monkeypatch.delenv("FAKE_SIGNAL_SEND_ERROR")
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path)
    try:
        store = restarted.app.store
        await until(lambda: store.unsent_outgoing() == [])  # recovery sends it, as code's other notes
        assert len(setup_notes(restarted)) == tried + 1
        [(note, _)] = store.code_notes(store.current_conversation().id, 0)
        assert (note.status, note.text) == ("sent", note_text(FACTS))
        assert [fact.text for fact in restarted.app.profile.yours()] == FACTS
    finally:
        await stop(restarted)


async def test_a_setup_note_owed_goes_out_at_a_start_more_than_10_minutes_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 0.9.8, memory-4: recovery and the resend pass send a note only within the recovery window (10
    minutes), so after a longer Signal outage at her first start the setup note never arrived. It is owed now, as the
    welcome is: its ids under a key of her store, sent at each start and by the resend pass until it went out."""
    from tests.test_app import Clock

    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-1")
    path = handed(tmp_path)
    harness = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path)
    try:
        await until(lambda: [message.status for message in harness.app.store.unsent_outgoing()] == ["failed"])
        assert harness.app.store.get_state(setup_handover.OWED_KEY)
    finally:
        await stop(harness)
    tried = len(setup_notes(harness))
    monkeypatch.delenv("FAKE_SIGNAL_SEND_ERROR")
    later = Clock()
    later.now += 2 * 3600  # two hours on: past the recovery window
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path, clock=later)
    try:
        store = restarted.app.store
        await until(lambda: store.unsent_outgoing() == [])
        assert len(setup_notes(restarted)) == tried + 1
        [(note, _)] = store.code_notes(store.current_conversation().id, 0)
        assert (note.status, note.text) == ("sent", note_text(FACTS))
        assert store.get_state(setup_handover.OWED_KEY) is None  # delivered: owed no more
    finally:
        await stop(restarted)


async def test_an_owed_setup_note_waits_while_she_is_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Locked, nothing is sent; the note stays owed (as the welcome) instead of being set aside for good."""
    from local_assistant.commands import LOCK_KEY
    from tests.test_app import Clock

    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-1")
    path = handed(tmp_path)
    harness = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path)
    try:
        await until(lambda: [message.status for message in harness.app.store.unsent_outgoing()] == ["failed"])
    finally:
        await stop(harness)
    monkeypatch.delenv("FAKE_SIGNAL_SEND_ERROR")
    later = Clock()
    later.now += 2 * 3600

    def locked(store: object) -> None:
        store.set_state(LOCK_KEY, "on")  # type: ignore[attr-defined]

    restarted = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path, clock=later, prepare=locked)
    try:
        await asyncio.sleep(0.3)
        store = restarted.app.store
        assert [message.status for message in store.unsent_outgoing()] == ["failed"]
        assert store.get_state(setup_handover.OWED_KEY)
    finally:
        await stop(restarted)


async def test_the_resend_pass_sends_an_owed_setup_note_past_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_app import Clock

    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-1")
    clock = Clock()
    path = handed(tmp_path)
    harness = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path, clock=clock)
    try:
        store = harness.app.store
        await until(lambda: [message.status for message in store.unsent_outgoing()] == ["failed"])
        tried = len(setup_notes(harness))
        await sends_work_again(harness, monkeypatch)
        clock.now += 3600  # the outage lasted an hour
        await harness.app.lifecycle.resend_failed()
        await harness.app.lifecycle.resend_failed()
        assert len(setup_notes(harness)) == tried + 1 and store.unsent_outgoing() == []
        assert store.get_state(setup_handover.OWED_KEY) is None
    finally:
        await stop(harness)


async def test_a_copy_back_with_the_flag_unset_imports_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rollback may bring an older copy whose flag was never set: her store's own key says it was imported."""
    path = handed(tmp_path)

    def imported(store: object) -> None:
        store.set_state(setup_handover.STATE_KEY, "1")  # type: ignore[attr-defined]

    harness = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path, prepare=imported)
    try:
        await asyncio.sleep(0.3)
        assert harness.app.profile.yours() == [] and sent(harness.folder) == []
    finally:
        await stop(harness)


async def test_a_line_the_profile_refuses_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = handed(tmp_path, {key: "" for key in ABOUT} | {"work": "Teacher", "day": "x" * 280}, {})
    harness = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path, fact_max_chars=60)
    try:
        await until(lambda: harness.app.store.get_state(setup_handover.STATE_KEY) is not None)
        assert [fact.text for fact in harness.app.profile.yours()] == ["Work: Teacher"]
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == ["From setup, I keep in mind\nWork: Teacher\n" + prompts.SETUP_FACTS_CLOSING]
    finally:
        await stop(harness)


async def test_while_locked_nothing_is_imported_until_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_assistant.commands import LOCK_KEY

    path = handed(tmp_path)

    def locked(store: object) -> None:
        store.set_state(LOCK_KEY, "on")  # type: ignore[attr-defined]

    harness = await start(tmp_path, monkeypatch, FakeGemma(), setup_answers_path=path, prepare=locked)
    try:
        await asyncio.sleep(0.3)
        assert harness.app.profile.yours() == [] and harness.app.store.get_state(setup_handover.STATE_KEY) is None
    finally:
        await stop(harness)
    assert tomllib.loads(path.read_text(encoding="utf-8"))["imported"] is False


def test_her_imitation_of_the_setup_note_is_left_out() -> None:
    """Beside P4's two quiet-time headings: Gemma may imitate code's note "From setup, I keep in mind" in an answer of
    her own; that paragraph is dropped, as the bridge's own notices are (context.without_fake_notices). Code's real
    note reaches her only as a code_notes result, never through her answers."""
    from local_assistant.context import without_fake_notices

    facts = ["About: The user goes by Alex."]
    forged = prompts.fact_note(prompts.SETUP_FACTS_HEADER, facts, prompts.SETUP_FACTS_CLOSING)
    assert without_fake_notices(f"Sure.\n\n{forged}") == "Sure."
    assert without_fake_notices("From setup, I keep in mind: the user goes by Alex.") == ""
    assert without_fake_notices("Sure. From setup, I keep in mind nothing.") != ""  # only a paragraph that starts so

"""Setup's questions as data: the one list every face draws, held to the rules every question of setup keeps (the
recommendation before the Enter hint, lines that fit a terminal, plain words), whichever track wrote it."""

import json
from collections.abc import Mapping

import pytest

from local_assistant import prompts
from local_assistant.setup_plan import QUESTIONS, Mac, nuclear_text, question
from local_assistant.setup_questions import (
    DECISIONS,
    ID,
    Checked,
    Question,
    Value,
    clean_paste,
    json_value,
    recommendation,
)
from tests.setup_helpers import FILLED, TERMINAL


class Answered:
    """Answers kept from an earlier run, as setup_answers.Answers gives them to a question."""

    def __init__(self, values: Mapping[str, Value]) -> None:
        self.values = dict(values)

    def get(self, qid: str) -> Value | None:
        return self.values.get(qid)

    def kept(self, qid: str) -> bool:
        return qid in self.values


NONE = Answered({})
AT_THE_DESK = Mac()
WORDS = {key: str(value) for key, value in FILLED.items()} | {"nuclear": nuclear_text("Maria-Christina Ann")}


def asked(q: Question, mac: Mac = AT_THE_DESK, answers: Answered = NONE) -> Question:
    """The question as a face shows it on this Mac, its {fields} filled as the terminal tests fill them."""
    return q.at(answers, mac, WORDS)


def test_every_id_is_unique_and_well_formed() -> None:
    ids = [q.id for q in QUESTIONS]
    assert len(ids) == len(set(ids)), [qid for qid in ids if ids.count(qid) > 1]
    assert all(ID.fullmatch(qid) for qid in ids)
    with pytest.raises(ValueError, match="id"):
        Question("Name", "text", "Name")


def test_a_secret_is_never_kept_in_the_answers_file() -> None:
    """A captcha link, a code, a PIN or a key: asked at its step each time, or kept only in the login keychain."""
    secrets = [q for q in QUESTIONS if q.is_secret]
    assert {"captcha", "sms_code", "lock_pin", "recovery_key"} <= {q.id for q in secrets}
    assert all(q.keep in ("never", "keychain") for q in secrets), [q.id for q in secrets]
    with pytest.raises(ValueError, match="secret"):
        Question("pin", "pin", "PIN", keep="saved")


def test_a_screen_carries_a_kept_answer_never_a_secret_or_a_steps() -> None:
    """The window's screen shows "Kept" only for an answer the answers file keeps; every other kind of answer is
    asked again at its step and never sent back to a face."""
    kept = Answered({q.id: "kept-value" for q in QUESTIONS})
    for q in QUESTIONS:
        screen = asked(q, Mac(), kept).screen(kept, Mac())
        json.dumps(screen)  # one JSON object per line on the window's pipe
        assert screen["t"] == "screen" and screen["id"] == q.id and screen["kind"] == q.kind
        assert ("kept" in screen) is (q.keep == "saved" and not q.is_secret), q.id
        if q.is_secret or q.keep != "saved":
            assert "kept-value" not in json.dumps(screen), q.id


@pytest.mark.parametrize("qid", [q.id for q in QUESTIONS])
def test_every_question_says_its_recommendation_on_a_line_of_its_own(qid: str) -> None:
    """The rules setup's questions keep (owner, 19 Oct; 27 Oct: "the recommendations below the option"), on every
    question of the list, every track's: the question alone, its recommendation (when it has one) on a line of its own
    in Terminal and under its option in the window, never "most people"; "admin rights", never "sudo" outside a
    command; every line fits the terminal."""
    for mac in (Mac(), Mac(ssh=True), Mac(legacy=True), Mac(ssh=True, legacy=True)):
        q = asked(question(qid), mac)
        text = q.ask
        assert "Recommend" not in text, qid
        if q.kind in DECISIONS and q.recommended is not None and qid not in ("language", "voice"):
            assert " (recommended" in recommendation(q) and "Recommended:" not in recommendation(q), qid
        words = f" {text.lower()} ".replace("/usr/bin/sudo", "")
        assert "most people" not in words and " sudo " not in words and "passwordless" not in words, qid
        assert all(len(line) <= TERMINAL for line in text.splitlines()), qid


@pytest.mark.parametrize("qid", [q.id for q in QUESTIONS])
def test_every_explanation_fits_the_terminal_in_plain_words(qid: str) -> None:
    for mac in (Mac(), Mac(ssh=True), Mac(legacy=True)):
        q = asked(question(qid), mac)
        # an option's label and its line are each drawn on lines of their own (Terminal's listing, gum's menu, the
        # window's card): the route's label, the owner's "a Signal Account / Login" (28 Oct), and its line together
        # would not fit one line, and no face puts them on one
        options = [text for option in q.options for text in (option.label, option.line)]
        for text in (q.title, q.lead, q.help, q.listing, *options):
            for line in text.splitlines():
                assert len(line) <= TERMINAL, (qid, len(line), line)
            words = text.lower()
            assert "most people" not in words and "secure" not in words and "seamless" not in words, qid
            assert " sudo " not in f" {words} ".replace("/usr/bin/sudo", ""), qid


def test_the_smaller_model_is_asked_only_on_16_and_24_gb_before_local_worker_is_there() -> None:
    shows = question("smaller_model").shows
    assert [shows(NONE, Mac(memory_gb=gb)) for gb in (8.0, 16.0, 24.0, 32.0, 64.0)] == [False, True, True, False, False]
    assert not shows(NONE, Mac(memory_gb=16.0, model_listed=True))  # Ollama lists local-worker: nothing to download


def test_the_voice_is_asked_only_for_a_name_that_is_not_a_suggestion() -> None:
    shows = question("voice").shows
    assert shows(Answered({"name": "Juno"}), Mac())
    assert not shows(Answered({"name": "Maya"}), Mac()) and not shows(Answered({"name": "Estia"}), Mac())
    assert not shows(NONE, Mac()) and not shows(Answered({"name": "Juno"}), Mac(configured=True))


def test_the_projects_folder_is_asked_only_after_a_yes() -> None:
    for qid in ("projects_folder", "projects_level"):
        shows = question(qid).shows
        assert shows(Answered({"projects": True}), Mac()), qid
        assert not shows(Answered({"projects": False}), Mac()) and not shows(NONE, Mac()), qid


def test_over_ssh_without_screen_the_move_says_so_and_enter_is_a_no() -> None:
    """A dropped connection stops the move halfway: over SSH outside screen or tmux the line says to answer no and
    start screen, and Enter is a no there too; inside screen, or at the Mac, Enter moves her."""
    unkept = asked(question("move"), Mac(ssh=True))
    assert prompts.SETUP_SSH_SCREEN in unkept.lead and unkept.ask == prompts.SETUP_MOVE_ASK_SSH
    assert unkept.recommended == "no" and recommendation(unkept) == "No (recommended, run setup again inside screen)."
    for mac in (Mac(), Mac(ssh=True, kept_session=True)):
        kept = asked(question("move"), mac)
        assert prompts.SETUP_SSH_SCREEN not in kept.lead and kept.ask == prompts.SETUP_MOVE_ASK
        assert kept.recommended == "yes"
    older = asked(question("move"), Mac(legacy=True, ssh=True))
    assert older.ask == prompts.SETUP_MOVE_OLDER_ASK_SSH and older.lead.startswith(
        prompts.SETUP_MOVE_OLDER.format(name="Estia")
    )
    assert not question("move").shows(NONE, Mac(own_user=True))  # moved already: nothing to ask


def test_the_language_listed_first_and_taken_by_enter_is_the_macs() -> None:
    other, dutch_mac = asked(question("language"), Mac(language="xx")), asked(question("language"), Mac(language="nl"))
    assert other.recommended == "en" and other.options[0].id == "en"  # not among the 11: English
    assert dutch_mac.recommended == "nl" and dutch_mac.options[0].id == "nl" and not dutch_mac.options[0].tag
    assert [option.id for option in dutch_mac.options].count("nl") == 1 and len(dutch_mac.options) == 11


def test_the_name_lists_estia_first_the_three_suggestions_then_her_own() -> None:
    options = asked(question("name")).options
    assert [option.label for option in options] == ["Estia", "Maya", "Melina", "Socrates", "Her name"]
    assert options[0].tag == "Recommended" and not any(option.tag for option in options[1:])


def test_an_unknown_question_names_the_known_ones() -> None:
    with pytest.raises(KeyError, match="name, voice"):
        question("nmae")


@pytest.mark.parametrize(
    ("pasted", "spaces", "kept", "left"),
    [
        ("﻿Maya​\n", False, "Maya", 3),  # a byte order mark, a zero-width space, a line break
        ("Example­Street  12\r\n", False, "ExampleStreet 12", 4),  # a soft hyphen, two spaces as one
        ("123 456\n", True, "123456", 2),  # a code: every space out
        ("Ermou", False, "Ermou", 0),
    ],
)
def test_a_paste_keeps_only_what_an_answer_holds(pasted: str, spaces: bool, kept: str, left: int) -> None:
    assert clean_paste(pasted, spaces=spaces) == (kept, left)


def test_values_travel_as_json_does() -> None:
    assert json_value("Maya") == "Maya" and json_value(True) is True
    assert json_value(("web", "maps")) == ["web", "maps"]
    assert json_value({"street": "Ermou", "city": "Athens"}) == {"street": "Ermou", "city": "Athens"}


def test_a_check_says_what_is_wrong_and_shows_back_what_passed() -> None:
    name = question("name").check
    language = question("language").check
    assert name is not None and language is not None
    assert name("maya", NONE, Mac()) == Checked("Maya", shown="She will answer to Maya.")  # as it is written
    assert name("1", NONE, Mac()).value == "Estia"
    refused = name("R2-D2", NONE, Mac())
    assert refused == Checked(None, "1 to 20 letters, spaces or hyphens.")  # the owner's words, only as the error
    assert language("dutch", NONE, Mac()).value == "nl" and "Nederlands" in language("dutch", NONE, Mac()).shown


def test_the_name_is_shown_back_in_the_specs_words_and_the_voice_with_it() -> None:
    name, voice = question("name").check, question("voice").check
    assert name is not None and voice is not None
    assert name("Maya", NONE, Mac()).shown == "She will answer to Maya."
    # c-17 (confirm-2): the male suggestion, He on first mention (the owner's pronoun rule)
    assert name("socrates", NONE, Mac()) == Checked("Socrates", shown="He will answer to Socrates.")
    assert voice("m", Answered({"name": "Juno"}), Mac()).shown == prompts.SETUP_NAME_CHOSEN.format(
        name="Juno",
        kind="male",
        voice="alba",  # P2: a male name brings Pocket TTS's alba (setup.MALE_VOICE)
    )


def test_a_pasted_answer_says_what_was_left_out() -> None:
    """Spec rule 6: a paste is cleaned and says what was kept; an answer typed as it is says nothing more."""
    name = question("name").check
    assert name is not None
    pasted = name("\ufeffJuno\u200b\n", NONE, Mac())
    assert pasted.value == "Juno"
    assert pasted.shown == "Kept 4 characters; 3 spaces or hidden characters were left out.\nShe will answer to Juno."
    assert name(" Juno ", NONE, Mac()).shown == "She will answer to Juno."


@pytest.mark.parametrize("qid", ["language", "style", "voice"])
def test_every_check_of_the_core_cleans_a_paste(qid: str) -> None:
    check = question(qid).check
    typed = {"language": "nl", "style": "playful", "voice": "m"}[qid]
    assert check is not None and check(f"\u200b{typed}\n", NONE, Mac()).value is not None, qid


def test_enter_takes_the_language_of_the_macs_region_and_the_line_says_so() -> None:
    dutch = asked(question("language"), Mac(language="nl"))
    assert dutch.ask == prompts.SETUP_LANGUAGE_ASK_MAC.format(language="Nederlands")
    assert "Enter: Nederlands, this Mac's" in dutch.ask and "Enter: Nederlands" in dutch.dry
    english = asked(question("language"), Mac(language="en"))
    assert english.ask == prompts.SETUP_LANGUAGE_ASK and english.dry == prompts.SETUP_LANGUAGE_DRY_RUN

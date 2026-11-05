"""Her tools here (owner, 23 Oct: "make an inventory list of all the tools and make it available to [her], with
descriptions, it seems that she is not aware of the /tools"): code's list of every tool this install offers her, one
plain line each, then the programs behind them, one line each. Built from the live tool schemas, so a tool that is off
never appears and a tool another branch adds appears with its own description's first sentence. Her rules carry it
right after the list of what she can do (never on a call), and /tools shows it word for word."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant.brain import Brain
from local_assistant.chords import CHORDS_SCHEMA, ChordTool
from local_assistant.context import ContextBuilder
from local_assistant.music_tools import MUSIC_SCHEMAS
from local_assistant.profile import Profile
from local_assistant.store import Store
from local_assistant.tools import (
    CALENDAR_SCHEMAS,
    CALL_SCHEMAS,
    DEVICE_SCHEMAS,
    DOCUMENT_SCHEMAS,
    MAPS_SCHEMAS,
    PROJECT_SCHEMAS,
    REMINDER_SCHEMAS,
    TOOL_SCHEMAS,
    WEB_SCHEMAS,
    Tools,
    document_schemas,
)
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text
from tests.test_brain import no_fetch

EVERY_SCHEMA = [
    *TOOL_SCHEMAS,
    *PROJECT_SCHEMAS,
    *DOCUMENT_SCHEMAS,
    *REMINDER_SCHEMAS,
    *CALENDAR_SCHEMAS,
    *WEB_SCHEMAS,
    *MAPS_SCHEMAS,
    *DEVICE_SCHEMAS,
    *MUSIC_SCHEMAS,
    *CALL_SCHEMAS,
    CHORDS_SCHEMA,
]


def offered(schemas: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(schema["function"]["name"], schema["function"]["description"]) for schema in schemas]


def names(lines: tuple[str, ...]) -> list[str]:
    return [line.removeprefix("• ").split(":", 1)[0] for line in lines]


def test_every_tool_main_offers_has_a_plain_line_of_its_own() -> None:
    every = [name for name, _ in offered(EVERY_SCHEMA)]
    assert sorted(prompts.TOOL_LINES) == sorted(every)


def test_the_list_names_each_offered_tool_once_in_the_order_she_gets_them() -> None:
    listed = prompts.inventory(offered(TOOL_SCHEMAS + WEB_SCHEMAS))
    assert names(listed.tools) == [name for name, _ in offered(TOOL_SCHEMAS + WEB_SCHEMAS)]
    web = next(line for line in listed.tools if line.startswith("• web_search: "))
    assert web == f"• web_search: {prompts.TOOL_LINES['web_search']}"
    assert listed.helpers == (f"• {prompts.HELPER_LINES['youtube']}",)  # read_link's captions: always there


def test_a_tool_without_a_line_of_its_own_gets_its_descriptions_first_sentence() -> None:
    # a tool another branch adds (a made-up one here) is listed where it is offered, never elsewhere
    added = ("garden_water", "Waters one garden bed by its name, for a few minutes. Only beds the config names.")
    listed = prompts.inventory([*offered(TOOL_SCHEMAS), added])
    assert listed.tools[-1] == "• garden_water: Waters one garden bed by its name, for a few minutes."
    assert not any("garden" in line for line in prompts.inventory(offered(TOOL_SCHEMAS)).tools)


def test_the_host_is_named_as_the_config_names_it() -> None:
    listed = prompts.inventory(offered([*TOOL_SCHEMAS, CHORDS_SCHEMA]), host="the Mini", voice=True)
    assert "• mini_status: The Mini's health" in "\n".join(listed.tools)
    assert "worked out on the Mini" in listed.tools[-1]
    assert "on the Mini" in listed.helpers[0]


def test_the_programs_behind_them_are_named_only_when_on() -> None:
    bare = prompts.inventory(offered(TOOL_SCHEMAS))
    assert names(bare.helpers) == ["YouTube helper"]
    full = prompts.inventory(
        offered(EVERY_SCHEMA),
        voice=True,
        calls=True,
        wake_calls=True,
        calendars=("Google", "iCloud", "Google"),
        devices=("TP-Link", "Philips Hue", "TP-Link"),
        documents=True,
    )
    assert names(full.helpers) == [
        "Voice service",
        "Call helper",
        "Calendar helper",
        "Device helper",
        "YouTube helper",
        "Documents helper",
    ]
    shown = "\n".join(full.helpers)
    assert "your Google and iCloud calendars" in shown and "TP-Link and Philips Hue" in shown
    assert "WhisperKit" in shown and "PDFKit and Vision" in shown
    answered = prompts.inventory(offered(TOOL_SCHEMAS), calls=True)
    assert "wake-up" not in "\n".join(answered.helpers)  # calls on, no wake-up calls: only answered
    assert "wake-up" in "\n".join(prompts.inventory(offered(TOOL_SCHEMAS), calls=True, wake_calls=True).helpers)


def test_the_voice_line_names_calls_only_with_calls() -> None:
    # voice without [calls] is a valid install (calls need voice, voice does not need calls): every line true there
    alone = prompts.inventory(offered(TOOL_SCHEMAS), voice=True).helpers[0]
    assert alone == (
        "• Voice service: voice notes turned into text (WhisperKit) and answers spoken (Pocket TTS, in English,"
        " Spanish, Italian, Portuguese, Dutch, German and French only), all on the Mac"
    )
    with_calls = prompts.inventory(offered(TOOL_SCHEMAS), voice=True, calls=True).helpers[0]
    assert with_calls.startswith("• Voice service: voice notes and calls turned into text (WhisperKit)")
    assert len(with_calls) <= 190  # the languages named (3 Nov 2026), one Signal line on a phone's width or two


def test_the_voice_line_names_only_pocket_and_the_languages_it_speaks() -> None:
    """Review of 0.9.8, guards-6: the line names the engine that speaks, as her rules treat a program not listed as
    absent. Owner, 3 Nov 2026: Pocket TTS is her only voice (Kokoro and Supertonic-3 removed), in English and the six
    languages of its own models: Korean, Polish, Russian and Japanese are not named, so she never claims a voice in
    them."""
    line = prompts.inventory(offered(TOOL_SCHEMAS), voice=True).helpers[0]
    assert "Kokoro" not in line and "Supertonic" not in line
    assert not any(name in line for name in ("Korean", "Polish", "Russian", "Japanese"))


async def test_a_voice_chosen_with_voice_leaves_her_list_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/voice michael changes her English voice, never who speaks: her rules and /tools keep the Pocket TTS line."""
    from tests.fakes.fake_voice import FakeVoiceService
    from tests.test_app import T0 as NOW  # the app's own tests' time: a message from conftest's T0 is missed
    from tests.test_app import deliver, her_rules, sent, start, stop, until

    with FakeVoiceService() as service:
        harness = await start(tmp_path, monkeypatch, FakeGemma(), voice_url=service.url)
        try:
            pocket = (
                "answers spoken (Pocket TTS, in English, Spanish, Italian, Portuguese, Dutch, German and French only)"
            )
            assert pocket in her_rules(harness.app)
            deliver(harness.folder, "/voice michael", NOW)
            await until(lambda: any(line.startswith("Michael is on") for line in sent(harness.folder)))
            assert pocket in her_rules(harness.app) and pocket in harness.app.status_host.inventory_text()
        finally:
            await stop(harness)


def test_with_one_page_a_read_the_documents_line_says_one_at_a_time() -> None:
    """Review of 0.9.8, guards-2: under a 32k context read_document gives one page; her list says so."""
    schemas = [*TOOL_SCHEMAS, *document_schemas(1)]
    small = "\n".join(prompts.inventory(offered(schemas), read_pages=1).tools)
    assert "• read_document: a page of one of your PDFs or text files read, one at a time, read only" in small
    full = "\n".join(prompts.inventory(offered([*TOOL_SCHEMAS, *document_schemas(3)])).tools)
    assert "• read_document: a page of one of your PDFs or text files read, up to three at a time, read only" in full
    assert document_schemas(3) == DOCUMENT_SCHEMAS  # the 26B's request as before
    [one] = [schema for schema in document_schemas(1) if schema["function"]["name"] == "read_document"]
    assert one["function"]["description"].endswith("(one page in one call).")


def test_the_page_line_says_a_pages_text_is_kept() -> None:
    # read_link keeps a page's text in her database for later searches (Tools._read_link)
    line = prompts.TOOL_LINES["read_link"]
    assert "nothing saved" not in line
    assert line == (
        "a page or a YouTube video you send, or one I found after you type YES, summarised; a page's text kept for "
        "later searches"
    )
    pages = "earlier conversations and the pages you sent, searched by words or names"
    assert prompts.TOOL_LINES["search_history"] == pages


def test_no_line_names_captions() -> None:
    # review, 24 Oct: with "captions" in read_link's line and the YouTube helper's ("a video's captions only, never the
    # video itself; captions never stored"), "What are the steps in this video?" got "I cannot write out the full
    # transcript" with no read (video_auto: main 8 of 8, those lines 1 to 3 of 8); without the word 8 of 8 and 7 of 7.
    # The capabilities list above names captions and that they are never stored
    lines = [*prompts.TOOL_LINES.values(), *prompts.TOOL_LINES_NO_CALLS.values(), *prompts.HELPER_LINES.values()]
    assert not any("caption" in line.lower() for line in lines)
    assert prompts.HELPER_LINES["youtube"] == (
        "YouTube helper: what a YouTube video you send says, for its summary; nothing of it stored"
    )
    assert "captions never stored" in prompts.CAPABILITY["links"]


def test_she_can_call_the_user_says_the_reminder_line() -> None:
    # main, 24 Oct: asked "can you call me?" she said "I can't initiate a call" 4 of 4 times, while wake-up calls are on
    assert prompts.TOOL_LINES["set_reminder"].startswith("a call to you or a text at the time you ask for")


@pytest.mark.parametrize("code", ["ko", "pl", "ru", "ja"])
def test_in_a_language_with_no_voice_her_tools_name_no_wake_up_call(code: str) -> None:
    """Review of 4 Nov, DLG-0983-06: her tools' list still said a reminder is "a call to you or a text" in the four
    languages her voice does not speak, where her call for a reminder is refused (`can_place`: no_voice): there a
    "call me" reminder is a text, as without wake-up calls."""
    voiced = prompts.inventory(offered(REMINDER_SCHEMAS), calls=True, wake_calls=True)
    assert "a call to you or a text" in "\n".join(voiced.tools)
    silent = prompts.inventory(offered(REMINDER_SCHEMAS), calls=True, wake_calls=True, language=code)
    assert silent == prompts.inventory(offered(REMINDER_SCHEMAS), calls=True)
    assert "wake-up" not in "\n".join([*silent.tools, *silent.helpers])


@pytest.mark.parametrize("code", ["en", "es", "it", "pt", "nl", "de", "fr"])
def test_in_a_language_with_a_voice_her_tools_are_listed_as_they_were(code: str) -> None:
    schemas = offered(REMINDER_SCHEMAS)
    assert prompts.inventory(schemas, calls=True, wake_calls=True, language=code) == prompts.inventory(
        schemas, calls=True, wake_calls=True
    )


def test_without_wake_up_calls_the_reminder_lines_name_no_call() -> None:
    # every claim true in this install: a "call me" reminder is a text without [calls] (Config.wake_calls)
    texts = "\n".join(prompts.inventory(offered(REMINDER_SCHEMAS)).tools)
    assert "call" not in texts.replace("cancel_reminder", "").replace("cancelled", "")
    assert (
        '• set_reminder: a text at the time you ask for (a reminder); set only when my "Reminder set" note says so'
        in texts
    )
    calls = "\n".join(prompts.inventory(offered(REMINDER_SCHEMAS), calls=True, wake_calls=True).tools)
    assert "a call to you or a text" in calls and "your reminders and wake-up calls" in calls


def test_the_device_line_names_a_yes_only_with_a_device_that_asks_first() -> None:
    plain = prompts.inventory(offered(DEVICE_SCHEMAS)).tools[1]
    assert plain == "• device_set: a light or plug switched on or off, or a light dimmed, by its name"
    asks = prompts.inventory(offered(DEVICE_SCHEMAS), ask_first=True).tools[1]
    assert asks == f"{plain}; some only after your yes"


@pytest.mark.parametrize("line", [*prompts.TOOL_LINES.values(), *prompts.HELPER_LINES.values()])
def test_each_line_is_short_plain_and_in_the_owners_words(line: str) -> None:
    assert len(line) <= 170, line
    assert "\n" not in line and "&" not in line, line
    assert not re.search(r"\b(?:secure|securely|seamless|you can)\b", line, re.IGNORECASE), line
    assert not line.endswith("."), line


def test_tools_shows_the_list_word_for_word_under_two_headings() -> None:
    listed = prompts.inventory(offered(TOOL_SCHEMAS), calls=True)
    shown = prompts.inventory_text(listed)
    assert shown == "\n".join(
        [prompts.INVENTORY_HEADER, *listed.tools, "", prompts.INVENTORY_HELPERS_HEADER, *listed.helpers]
    )


def test_her_rules_carry_the_list_after_what_she_can_do_and_before_safety() -> None:
    lines = prompts.capabilities(reminders=True)
    listed = prompts.inventory(offered(TOOL_SCHEMAS + REMINDER_SCHEMAS), calls=True, wake_calls=True)
    rules = prompts.rules(name="Estia", calls=True, reminders=True, capabilities=lines, inventory=listed)
    tools, helpers = "\n".join(listed.tools), "\n".join(listed.helpers)
    assert rules.index("\n".join(lines)) < rules.index(prompts.INVENTORY_RULE) < rules.index(tools)
    assert (
        rules.index(tools) < rules.index(f"{prompts.INVENTORY_HELPERS_RULE}\n{helpers}") < rules.index("\n\nSafety:\n")
    )
    assert rules.endswith(prompts.rules(name="Estia").split("\n\nSafety:\n", 1)[1])  # safety stays last


def test_her_rule_says_to_answer_from_the_lists_never_from_a_project() -> None:
    assert "never from a project's files" in prompts.INVENTORY_RULE
    assert "/tools" in prompts.INVENTORY_RULE


def test_her_rule_asks_for_the_users_language_and_plain_words() -> None:
    # review, 24 Oct, run in turn, a fresh tag each: asked "Welke tools heb je?", she pasted the English capabilities
    # list (main 8 of 8 in Dutch): with "word for word" and "answer from it and the list above, in the user's language
    # and in plain words" 3 of 8 and 2 of 8; the rule before (46fb02b, 802303f) 5 of 8 and 3 of 8; without "word for
    # word", the language first, 6 of 8. "In plain words of your own" got 8 of 8 there but took video_auto to 0 of 6
    # ("I cannot write out the full transcript"; the own-words gotcha of 15 Oct)
    assert "answer in the user's language and in plain words, from it and the list above" in prompts.INVENTORY_RULE
    assert "word for word" not in prompts.INVENTORY_RULE and "own words" not in prompts.INVENTORY_RULE
    assert "in plain words of your own" not in prompts.INVENTORY_RULE


def test_without_a_list_the_rules_stay_as_they_were() -> None:
    lines = prompts.capabilities(reminders=True)
    before = prompts.rules(name="Estia", reminders=True, capabilities=lines)
    assert prompts.rules(name="Estia", reminders=True, capabilities=lines, inventory=None) == before
    assert prompts.INVENTORY_RULE not in before


def test_a_calls_rules_leave_both_lists_out_byte_for_byte_as_before() -> None:
    lines = prompts.capabilities(reminders=True)
    listed = prompts.inventory(offered(TOOL_SCHEMAS), calls=True)
    before = prompts.rules(name="Estia", calls=True, reminders=True)
    full = prompts.rules(name="Estia", calls=True, reminders=True, capabilities=lines, inventory=listed)
    assert prompts.without_capabilities(full) == before


def test_practice_rules_carry_no_list() -> None:
    listed = prompts.inventory(offered(TOOL_SCHEMAS))
    practice = prompts.rules(name="Estia", practice=("nl", "A2"), inventory=listed)
    assert prompts.INVENTORY_RULE not in practice and listed.tools[0] not in practice


def test_tools_lists_what_this_setup_offers_and_leaves_mail_out_without_mail(store: Store, tmp_path: Path) -> None:
    profile = Profile(store)
    bare = Tools(store, profile, TZ, no_fetch)
    listed = [name for name, _ in bare.inventory_tools()]
    assert listed == [name for name, _ in offered(TOOL_SCHEMAS) if not name.startswith("mail_")]
    assert "mail_digest" in [schema["function"]["name"] for schema in bare.schemas]  # still offered, as before
    chords = Tools(store, profile, TZ, no_fetch, calls=True, chords=ChordTool())
    assert [name for name, _ in chords.inventory_tools()][-2:] == ["end_call", "chords"]


async def test_a_text_gets_the_list_and_a_call_does_not(store: Store) -> None:
    lines = prompts.capabilities(reminders=True)
    listed = prompts.inventory(offered(TOOL_SCHEMAS), calls=True)
    rules = prompts.rules(name="Estia", calls=True, reminders=True, capabilities=lines, inventory=listed)
    profile = Profile(store)
    gemma = FakeGemma(text("I can search the web."), text("Bye!"))
    brain = Brain(gemma, ContextBuilder(store, profile, TZ, rules=rules), Tools(store, profile, TZ, no_fetch))
    conversation = store.current_conversation()
    typed = store.add_incoming(conversation.id, ME, T0, "text", "What tools do you have?")
    assert typed is not None
    await brain.answer(conversation, store.message(typed))
    assert listed.tools[0] in gemma.calls[0]["messages"][0]["content"]
    said = store.add_incoming(conversation.id, ME, T0 + 1, "text", f"{prompts.CALL_MARK} thanks, that's all")
    assert said is not None
    await brain.answer(conversation, store.message(said))
    system = gemma.calls[1]["messages"][0]["content"]
    assert prompts.INVENTORY_RULE not in system and listed.tools[0] not in system


ROOT = Path(__file__).resolve().parents[1]


def _flat(name: str) -> str:
    """A doc's words with every run of spaces and line breaks as one space."""
    return " ".join((ROOT / name).read_text(encoding="utf-8").split())


def test_the_docs_name_tools_the_list_and_every_ability_case() -> None:
    guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")
    table = guide.split("## Slash commands", 1)[1].split("\n## ", 1)[0]
    assert "| `/tools` |" in table
    rules = _flat("docs/agents/prompts-evals.md")
    for words in ("`INVENTORY_RULE`", "`TOOL_LINES_NO_CALLS`", "`Tools.inventory_tools`", "`OLD_COPY`", "`_nl` twins"):
        assert words in rules, words
    for name in ("stems", "tools", "mail", "calendar", "call", "lights", "read_call", "read_web", "read_web_call"):
        assert f"`ability_{name}`" in rules, name
    assert "`projects.OWN_CODE`" in rules and "`projects.OWN_CODE`" in _flat("docs/agents/documents-projects.md")
    assert "`/tools`: her tools' list" in _flat("docs/agents/core.md")
    unreleased = _flat("CHANGELOG.md").split("## [0.9.8]", 1)[1].split("## [", 1)[0]
    assert "**`/tools`:**" in unreleased
    # every public claim provable: code puts the list in her instructions on texts and leaves it out on calls; what
    # she then says is the model's, measured, never promised
    said = "Her instructions carry the same list on texts, for questions about what she can do; calls leave it out."
    row = table.split("| `/tools` |", 1)[1].split("\n", 1)[0]
    assert said in unreleased and said.removesuffix(".") in row
    assert "never from a project" not in unreleased and "never from a project" not in row


def test_the_documents_line_claims_no_more_than_security_md() -> None:
    """Review of 0.9.7, X8: the helper opens no network connection in its own code; running it with no network at all
    is planned, not built (SECURITY.md), so her list says SECURITY.md's fact."""
    line = prompts.HELPER_LINES["documents"]
    assert "with no network" not in line and "opens no network connection itself" in line
    security = " ".join((Path(__file__).parent.parent / "SECURITY.md").read_text(encoding="utf-8").split())
    assert "The helper opens no network connection in its own code" in security


def test_with_openstreetmap_the_maps_lines_name_it_and_no_ratings() -> None:
    tools = [(schema["function"]["name"], schema["function"]["description"]) for schema in MAPS_SCHEMAS]
    google = prompts.inventory(tools)
    free = prompts.inventory(tools, osm=True)
    assert [line for line in google.tools if "Google Maps" in line] == list(google.tools)  # as before
    assert free.tools == (
        "• places_search: places near you or near a place you name, from OpenStreetMap, with opening hours, phone "
        "and website where it lists them",
        "• route: travel time, distance and public transport lines, from OpenStreetMap (public transport from "
        "Transitous)",
    )

"""The real-model cases for her own abilities (owner, 23 Oct: "make an inventory list of all the tools and make it
available to [her], with descriptions, it seems that she is not aware of the /tools"): asked about her own
abilities she read a checkout of her own code in the projects folder and answered from it. Each case has that
trap: an old copy of Estia's code at level code. The right answer names the real tool or says plainly that she cannot,
in the language asked in, never from a project file. Their judges, checked here on scripted replies (no model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.gemma_suite import (
    CASES,
    OLD_COPY,
    OLD_ESTIA,
    Check,
    Harness,
    ability_calendar,
    ability_calendar_nl,
    ability_call,
    ability_call_nl,
    ability_holdout_opbellen_nl,
    ability_holdout_possible,
    ability_lights,
    ability_lights_nl,
    ability_mail,
    ability_mail_nl,
    ability_read_call,
    ability_read_call_nl,
    ability_read_web,
    ability_read_web_call,
    ability_stems,
    ability_stems_nl,
    ability_tools,
    ability_tools_nl,
    install_list,
    run_one,
)
from local_assistant import prompts
from local_assistant.gemma import Completion
from tests.fakes.fake_gemma import FakeGemma, text, tool

NEW = (
    "ability_stems",
    "ability_tools",
    "ability_mail",
    "ability_calendar",
    "ability_call",
    "ability_lights",
    "ability_stems_nl",
    "ability_tools_nl",
    "ability_mail_nl",
    "ability_calendar_nl",
    "ability_call_nl",
    "ability_lights_nl",
    "ability_read_call",
    "ability_read_call_nl",
    "ability_read_web",
    "ability_read_web_call",
)
READ_OLD = tool("project_read", project=OLD_COPY, file="README.md")
SEARCH_OLD = tool("project_search", project=OLD_COPY, words="stems")
TOOLS_EN = (
    "I can read your mail and calendar (read only), search the web, find places and routes, switch your lights and "
    "plugs, play music on Spotify, set reminders and wake-up calls, and look at your projects."
)
TOOLS_NL = (
    "Ik kan je mail en agenda lezen, op het web zoeken, plaatsen en routes vinden, je lampen en stekkers schakelen, "
    "muziek afspelen op Spotify en herinneringen zetten. Ook kan ik naar je projecten kijken."
)


def scripted(tmp_path: Path, *answers: Completion) -> Harness:
    return Harness(tmp_path, FakeGemma(*answers), old_copy=True)


def test_the_ability_cases_are_in_the_set_with_the_old_copy_and_no_other_case_has_it() -> None:
    cases = {case.name: case for case in CASES}
    assert all(cases[name].group == "chat" and cases[name].old_copy and cases[name].projects for name in NEW)
    holdouts = ("tools-list", "her-calls", "her-calls-2")  # the ability holdouts carry it too
    others = [case for case in CASES if case.name not in NEW and case.holdout not in holdouts]
    assert not any(case.old_copy for case in others)  # the others' requests as before


def test_the_old_copy_says_what_main_has_is_not_built_yet() -> None:
    # the trap: what an answer read from it gets wrong (calls, reminders, lights are on in the harness)
    assert (
        "Not built yet: calls, reminders, calendars, lights and plugs, music, web search, stems."
        in OLD_ESTIA["README.md"]
    )
    assert (
        "Stems: not built yet" in OLD_ESTIA["todo.md"] and "make stems" in OLD_ESTIA["src/local_assistant/prompts.py"]
    )
    assert "src/local_assistant/__init__.py" in OLD_ESTIA  # a copy of her own code (projects.OWN_CODE)


async def test_the_old_copy_is_at_level_code_and_the_rules_say_so(tmp_path: Path) -> None:
    # named in the user's message before, the old copy is read: a read for her abilities fails the case
    harness = scripted(tmp_path, text("Sure."), READ_OLD, text("No, I can't make stems."))
    await harness.say("Let's talk about local-assistant.")
    ok, detail = await ability_stems(harness)
    assert not ok, detail  # it read the project
    read = next(call.result for call in harness.store.tool_calls() if call.name == "project_read")
    assert "Not built yet" in read
    assert any("some projects: docs, code" in line for line in install_list(projects=True, code_projects=True))


async def test_her_unasked_read_of_the_old_copy_is_refused_and_reads_nothing(tmp_path: Path) -> None:
    # a copy of her own code the user did not name is not read (projects.OWN_CODE): no project file in her answer
    harness = scripted(tmp_path, READ_OLD, text("No, I can't make stems."))
    ok, detail = await ability_stems(harness)
    assert ok, detail
    refused = next(call.result for call in harness.store.tool_calls() if call.name == "project_read")
    assert refused == prompts.PROJECT_OWN_CODE.format(name=OLD_COPY)


@pytest.mark.parametrize(
    ("check", "reply"),
    [
        (ability_stems, "No, I can't make stems: I have no tool that splits a song into its parts."),
        (ability_stems, "That's not something I can do: splitting a song into stems needs a separate app."),
        (ability_stems, "I cannot make stems myself. I can search for software that does, if you like."),
        (ability_stems_nl, "Nee, dat kan ik niet: ik heb geen tool die een nummer in stems splitst."),
        # main, 24 Oct: "stems" read as voices, still a plain no
        (ability_stems_nl, "Nee, ik kan zelf geen geluid of stemmen genereren. Ik kan alleen tekst schrijven."),
        (ability_tools, TOOLS_EN),
        (ability_tools_nl, TOOLS_NL),
        (ability_tools_nl, TOOLS_NL + " Spraakberichten worden op de Mac uitgeschreven, en ik antwoord met mijn stem."),
        (ability_mail, "Yes, I can read your mail, read only: I never send, move or delete anything."),
        (ability_mail_nl, "Ja, ik kan je mail lezen, maar ik verstuur of verwijder nooit iets."),
        (ability_calendar, "No, I can't change your calendar: I can only read it."),
        (ability_calendar, "Your calendar is read only for me. I can tell you what's on, but not move events."),
        (ability_calendar_nl, "Nee, je agenda kan ik niet aanpassen: ik kan hem alleen lezen."),
        # main, 24 Oct: the app is where the user changes it ("you can", never her claim)
        (
            ability_calendar_nl,
            "Ik kan je agenda alleen lezen, niet aanpassen of wijzigen. Je kunt het beste de Google Agenda-app "
            "gebruiken om wijzigingen door te voeren.",
        ),
        (ability_call, "Yes: you can call me on Signal any time, and I can call you at a set time as a wake-up call."),
        (
            ability_call,
            "I can't call you right now, but I can set a wake-up call or a reminder call for a time you pick.",
        ),
        (ability_call, "Sure, when should I call you?"),
        (ability_call_nl, "Ja, ik kan je op een tijd die jij kiest bellen, bijvoorbeeld om je te wekken."),
        (ability_call_nl, "Zeker, hoe laat zal ik je bellen?"),
        (ability_call_nl, "Dat kan ik doen. Om welke tijd wil je dat ik je bel?"),
        (
            ability_call_nl,
            "Ja, je kunt me altijd bellen via Signal, en ik kan je op een vast tijdstip bellen om je te wekken.",
        ),
        (ability_lights, "Sure, which lights? I can switch the living room lamp and the bedroom light."),
        (ability_lights_nl, "Ja, welke lampen wil je aan? De lamp in de woonkamer of die in de slaapkamer?"),
        # both arms of the A/B, 24 Oct, 9 times: a short question back, in Dutch
        (ability_lights_nl, "Welke lampen bedoel je precies?"),
    ],
)
async def test_a_plain_right_answer_without_a_project_file_passes(tmp_path: Path, check: Check, reply: str) -> None:
    ok, detail = await check(scripted(tmp_path, text(reply)))
    assert ok, detail


@pytest.mark.parametrize(
    ("check", "answers"),
    [
        (ability_mail, (tool("mail_digest"), text("Yes. Two mails need a reply today: Anna's and the bank's."))),
        (
            ability_lights,
            (tool("device_set", device="bedroom light", power="on"), text("Done, the bedroom light is on.")),
        ),
        (
            ability_call,
            (tool("set_reminder", text="call", in_minutes=1, call=True), text("I'll call you in a minute.")),
        ),
    ],
    ids=["mail tool", "device tool", "reminder call"],
)
async def test_the_real_tool_called_passes(tmp_path: Path, check: Check, answers: tuple[Completion, ...]) -> None:
    ok, detail = await check(scripted(tmp_path, *answers))
    assert ok, detail


@pytest.mark.parametrize(
    "check",
    [ability_stems, ability_tools, ability_mail, ability_calendar, ability_call, ability_lights],
    ids=lambda check: check.__name__,
)
@pytest.mark.parametrize("first", [READ_OLD, SEARCH_OLD, tool("project_list")], ids=["read", "search", "list"])
async def test_a_project_tool_fails_even_with_a_right_answer(tmp_path: Path, check: Check, first: Completion) -> None:
    right = {
        "ability_stems": "No, I can't make stems.",
        "ability_tools": TOOLS_EN,
        "ability_mail": "Yes, I can read your mail.",
        "ability_calendar": "No, I can't change your calendar.",
        "ability_call": "Yes, you can call me on Signal.",
        "ability_lights": "Sure, which lights?",
    }[check.__name__]
    harness = scripted(tmp_path, text("Sure."), first, text(right))
    await harness.say("Let's talk about local-assistant.")  # named: code reads it (projects.OWN_CODE)
    ok, detail = await check(harness)
    assert not ok, detail


@pytest.mark.parametrize("first", [READ_OLD, SEARCH_OLD], ids=["read", "search"])
async def test_an_unnamed_read_of_the_old_copy_reads_nothing_and_a_right_answer_passes(
    tmp_path: Path, first: Completion
) -> None:
    ok, detail = await ability_lights(scripted(tmp_path, first, text("Sure, which lights?")))
    assert ok, detail


@pytest.mark.parametrize(
    ("check", "reply"),
    [
        (ability_stems, "Yes! Send me a song and I'll split it into vocals, drums and bass."),
        (ability_stems, "Stems are the separate tracks of a song: vocals, drums, bass."),  # never says she can't
        # the old copy's todo taken for her own tool
        (ability_stems, "No, I cannot create stems myself. I can only use the tool in the local-assistant project."),
        (ability_tools, "I can chat with you and remember things."),  # too few of her tools
        (ability_tools, TOOLS_EN + " I can also read your PDFs."),  # documents are off here
        (ability_tools, TOOLS_EN + " And I can make stems of a song."),  # this install has no tool for stems
        (ability_mail, "I can't read your mail."),
        (ability_mail, "Yes, I can read your mail and send replies for you."),
        (ability_calendar, "Yes, I can move or add events in your calendar."),
        (ability_calendar, "Sure! What would you like to change?"),
        (ability_call, "No, I can't make phone calls."),
        # main, 24 Oct, 4 of 4: wrong, she can call at a set time (a wake-up or reminder call)
        (ability_call, "I can't initiate a call myself, but you can call me on Signal whenever you're ready to talk."),
        (ability_call, "You can call me on Signal any time."),
        (ability_call_nl, "Ik kan je niet bellen, maar we kunnen wel praten als je een call start via Signal."),
        (ability_call_nl, "Je kunt me altijd bellen via Signal."),
        (ability_lights, "Sorry, I can't control your lights."),
        (ability_mail_nl, "Yes, I can read your mail, read only."),  # English, asked in Dutch
        (ability_tools_nl, TOOLS_EN),
    ],
)
async def test_a_wrong_answer_fails(tmp_path: Path, check: Check, reply: str) -> None:
    ok, detail = await check(scripted(tmp_path, text(reply)))
    assert not ok, detail


# the 0.9.8 gate, 28 Oct, the 26B, asked "Kun jij mij ook opbellen?" (3 of 10): Dutch yeses that name her call as "een
# oproep voor je" or a time "waarop je gebeld wilt worden" (CALL_YOU read neither), and a yes that waits for the user
# to give the time ("dan bel ik je op", which code read as a call set with none stored, claims._waits_for_your_time)
@pytest.mark.parametrize(
    ("check", "reply"),
    [
        (
            ability_holdout_opbellen_nl,
            "Ja, dat kan ik. Je kunt me een tijdstip of een plan noemen, dan stel ik een oproep voor je in.",
        ),
        (
            ability_holdout_opbellen_nl,
            "Ja, dat kan ik. Je kunt me een tijdstip geven waarop je gebeld wilt worden, of je kunt een herinnering "
            "instellen voor een specifieke tijd.",
        ),
        (
            ability_holdout_opbellen_nl,
            "Ja, dat kan ik. Je kunt me een tijdstip geven voor een herinnering of een wake-up call, dan bel ik je op "
            "via Signal.",
        ),
        (
            ability_holdout_possible,
            "Yes, I can call you. You can set a time for a call or a wake-up call, and I will ring you then.",
        ),
        # its re-run that evening: "om je ... (op) te bellen", her call to you as a purpose (CALL_YOU read neither)
        (
            ability_holdout_opbellen_nl,
            "Ja, dat kan ik. Je kunt me een tijdstip doorgeven voor een belletje, of een herinnering instellen om je op "
            "een bepaald moment te bellen.",
        ),
        (
            ability_holdout_opbellen_nl,
            "Ja, dat kan ik. Je kunt me een oproep geven op een tijdstip dat je wilt, of je kunt me vragen om je op een "
            "bepaald moment op te bellen. Wat wil je dat ik doe?",
        ),
        # the re-run after the fixes, 28 Oct night: her call as "een belletje" or "een belafspraak" for you
        (
            ability_holdout_opbellen_nl,
            "Ja, dat kan ik. Je kunt me een tijdstip of een plan noemen, dan stel ik een belletje voor je in.",
        ),
        (
            ability_holdout_opbellen_nl,
            "Dat kan ik zeker. Als je een tijdstip en een reden doorgeeft, stel ik een belafspraak voor je in.",
        ),
        (ability_call_nl, "Wat voor gesprek wil je voeren? Noem een tijdstip, dan zet ik een belletje voor je klaar."),
    ],
)
async def test_the_gates_right_answers_about_her_call_pass(tmp_path: Path, check: Check, reply: str) -> None:
    ok, detail = await check(scripted(tmp_path, text(reply)))
    assert ok, detail


@pytest.mark.parametrize(
    ("check", "reply"),
    [
        # "een bel" is a bell, never her call, and "een belrink" no word
        (
            ability_holdout_opbellen_nl,
            "Ja, dat kan ik. Je kunt een tijdstip of een plan noemen, dan stel ik een belrink in voor je.",
        ),
        # the user's call to her, never hers
        (ability_call_nl, "Ik kan je niet bellen, maar je kunt proberen me te bellen via Signal."),
        (
            ability_holdout_opbellen_nl,
            "Ja, dat kan ik. Je kunt me een tijdstip of een plan noemen, dan stel ik een bel of herinnering voor je in.",
        ),
        # the 12B, 28 Oct: a call at once, none set (code's "No reminder was set" follows it)
        (ability_call_nl, "Natuurlijk, ik zal je onmiddellijk bellen."),
        # the 12B with chords, 28 Oct: she does place calls
        (
            ability_call,
            "I cannot place outgoing calls, but I can set a reminder for you to call yourself or a notification if "
            "you'd like.",
        ),
    ],
)
async def test_the_gates_wrong_answers_about_her_call_fail(tmp_path: Path, check: Check, reply: str) -> None:
    ok, detail = await check(scripted(tmp_path, text(reply)))
    assert not ok, detail


async def test_a_reminder_call_claimed_that_code_says_was_not_set_fails(tmp_path: Path) -> None:
    # she says she set a call while no set_reminder stored one: code's line follows her words
    ok, detail = await ability_call(scripted(tmp_path, text("Sure, I've set a call for you at 7:00.")))
    assert not ok, detail


async def test_the_questions_are_asked_as_the_user_types_them(tmp_path: Path) -> None:
    gemma = FakeGemma(text("Nee, dat kan ik niet."))
    harness = Harness(tmp_path, gemma, old_copy=True)
    await ability_stems_nl(harness)
    assert str(gemma.calls[0]["messages"][-1]["content"]).endswith("Kun je stems maken?")
    for check, asked in (
        (ability_tools, "What tools do you have?"),
        (ability_mail_nl, "Kun je mijn mail lezen?"),
        (ability_calendar_nl, "Kun je mijn agenda aanpassen?"),
        (ability_call_nl, "Kun je me bellen?"),
        (ability_lights_nl, "Kun je de lampen aanzetten?"),
        (ability_tools_nl, "Welke tools heb je?"),
        (ability_calendar, "Can you change my calendar?"),
        (ability_mail, "Can you read my mail?"),
        (ability_call, "Can you call me?"),
        (ability_lights, "Can you turn on the lights?"),
    ):
        fake = FakeGemma(text("Yes."))
        await check(Harness(tmp_path / check.__name__, fake, old_copy=True))
        assert str(fake.calls[0]["messages"][-1]["content"]).endswith(asked), check.__name__


async def test_run_one_gives_the_ability_cases_the_old_copy(tmp_path: Path) -> None:
    case = next(case for case in CASES if case.name == "ability_stems")
    gemma = FakeGemma(READ_OLD, text("Stems are not built yet."))
    result = await run_one(gemma, case, chords=False)
    assert result.tools == ["project_read"] and not result.ok  # no plain "cannot"
    assert prompts.PROJECT_OWN_CODE.format(name=OLD_COPY) in str(gemma.calls[-1]["messages"])  # there, unread


# her own read of the old copy (code's, as hers: the 23 Oct shape), then the question
@pytest.mark.parametrize(
    ("check", "reply"),
    [
        (ability_read_call, "Yes, I can call you: a wake-up call or a reminder call at a time you set."),
        (ability_read_call_nl, "Ja, ik kan je bellen op een tijd die je kiest, als wekoproep of herinnering."),
        (ability_read_web, "Yes, I can search the web for news, weather and prices."),
        (ability_read_web_call, "Yes, I can search the web for you. What should I look up?"),
    ],
)
async def test_after_her_read_a_right_answer_passes(tmp_path: Path, check: Check, reply: str) -> None:
    harness = scripted(tmp_path, text(reply))
    ok, detail = await check(harness)
    assert ok, detail
    first = harness.store.tool_calls()[0]
    assert first.name == "project_status" and first.result == prompts.PROJECT_OWN_CODE.format(name=OLD_COPY)


@pytest.mark.parametrize(
    ("check", "reply"),
    [
        # main, 24 Oct, with that read
        (
            ability_read_call,
            "I cannot make calls yet. My project notes show that calls over Signal are still on the todo list.",
        ),
        (
            ability_read_call,
            "I cannot make calls yet. My current tools allow me to chat, but calling isn't built into the project yet.",
        ),
        (ability_read_call, "I can't initiate a call myself, but you can call me on Signal."),
        (ability_read_call_nl, "Nee, bellen staat nog op de takenlijst."),
        # main, 24 Oct (the review's A/B), with that read
        (ability_read_call, "I cannot call you yet. The calls feature hasn't been built for this project."),
        (ability_read_call, "I can call you at a set time, but my development status says calls are not ready."),
        (ability_read_call_nl, "Ik kan helaas nog niet bellen. Dat staat nog op de to-do lijst voor dit project."),
        (ability_read_call_nl, "Ik bel je graag als wekoproep, al is de functie nog niet afgebouwd in mijn software."),
        (ability_read_call_nl, "Ik kan je wekken met een wekoproep, maar volgens je project is dat nog niet gebouwd."),
        (
            ability_read_web,
            "Yes, I can search the web. According to your project files, web search is still listed as not yet built.",
        ),
        (ability_read_web, "No, I can't search the web."),
        (ability_read_web_call, "thought: ]"),
    ],
)
async def test_after_her_read_an_answer_from_it_or_a_wrong_one_fails(tmp_path: Path, check: Check, reply: str) -> None:
    ok, detail = await check(scripted(tmp_path, text(reply)))
    assert not ok, detail


async def test_after_her_read_a_read_of_her_own_that_went_through_fails(tmp_path: Path) -> None:
    harness = scripted(tmp_path, text("Sure."), READ_OLD, text("Yes, I can search the web."))
    await harness.say("Let's talk about local-assistant.")  # named: her own read goes through
    ok, detail = await ability_read_web(harness)
    assert not ok and "own project reads ['project_read']" in detail, detail


async def test_the_read_cases_ask_as_the_user_types_them(tmp_path: Path) -> None:
    for check, asked in (
        (ability_read_call, "Can you call me?"),
        (ability_read_call_nl, "Kun je me bellen?"),
        (ability_read_web, "Can you search the web?"),
        (ability_read_web_call, "[call] can you search the web?"),
    ):
        fake = FakeGemma(text("Yes."))
        await check(Harness(tmp_path / check.__name__, fake, old_copy=True))
        user = [m for m in fake.calls[0]["messages"] if m.get("role") == "user"]
        assert str(user[-1]["content"]).endswith(asked), check.__name__

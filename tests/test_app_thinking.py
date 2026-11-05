"""Deep Thinking in the app (owner, 4 Nov 2026): the typed command and its 30 minutes through the real bridge on a fake
signal-cli, the scope (her answers to texts and voice notes; never a call, practice or her own messages), the busy
note's longer wait, a call that starts while she thinks, and one switch for each allowed account (owner, 4 Nov): two
accounts on the same bridge, `/thinking off` answered at once and stopping the answer that thinks, the last command typed
winning over an on still waiting in the queue, and what an off answered ahead of the queue leaves alone (review of 4 Nov
on this branch)."""

import asyncio
import json
import time
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant import thinking as thinking_module
from local_assistant.config import DEFAULT_NAME
from local_assistant.gemma import Completion
from local_assistant.signal_io import SignalError
from local_assistant.store import Store
from local_assistant.thinking import BUDGET_TOKENS
from local_assistant.timefmt import local_hhmm
from tests.conftest import ME, TZ
from tests.fakes.fake_gemma import FakeGemma, text, tool
from tests.fakes.fake_voice import FakeVoiceService
from tests.test_app import (
    PARTNER,
    T0,
    Harness,
    call_over,
    call_settings,
    deliver,
    on_a_call,
    photo,
    put,
    recap_answer,
    sent,
    sent_to,
    stop,
    until,
    voice,
)
from tests.test_app import start as start_bridge
from tests.test_devices import SPECS as DEVICE_SPECS
from tests.test_devices import Home
from tests.test_photos import png
from tests.test_thinking import Clock
from tests.test_voice import M4A


async def start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gemma: FakeGemma,
    *,
    context_tokens: int | None = 32_768,
    **settings: Any,
) -> Harness:
    """The bridge on a fake signal-cli with her model's context known, 32,768 tokens as setup writes it: Deep Thinking
    needs a context its config says is 32,768 tokens or more (review of 4 Nov, promises-2)."""
    return await start_bridge(tmp_path, monkeypatch, gemma, context_tokens=context_tokens, **settings)


def ends_at(clock: Clock) -> str:
    """HH:MM of the 30 minutes that start at the clock's time, in the tests' time zone."""
    return local_hhmm(clock.now + 1_800, TZ)


def at(clock: Clock) -> int:
    """A message sent now, by the clock the bridge keeps (an older one is the offline note's: it is stale after a restart)."""
    return int(clock.now * 1000)


async def test_thinking_on_is_typed_and_answered_with_its_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(time.time())
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock)
    try:
        deliver(harness.folder, "/thinking on", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == [prompts.THINKING_ON.format(time=ends_at(clock))]
        assert harness.app.thinking.on(ME) and harness.gemma.calls == []  # a command: Gemma is never asked
        deliver(harness.folder, "/thinking", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder)[1] == prompts.THINKING_STATE_ON.format(time=ends_at(clock))
        deliver(harness.folder, "/thinking off", T0 + 2)
        await until(lambda: len(sent(harness.folder)) == 3)
        assert sent(harness.folder)[2] == prompts.THINKING_OFF and not harness.app.thinking.on(ME)
    finally:
        await stop(harness)


async def test_a_voice_note_never_switches_it_whatever_it_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Typed only, like /babble: a transcript that reads "/thinking on" is words for her, never a command."""
    with FakeVoiceService() as service:
        service.transcript = "/thinking on"
        harness = await start(tmp_path, monkeypatch, FakeGemma(text("Sure.")), voice_url=service.url)
        try:
            put(harness.folder, "v1.m4a", M4A)
            deliver(harness.folder, None, T0, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: sent(harness.folder)[:1] == ["Sure."])
            assert not harness.app.thinking.on(ME)
            assert str(harness.gemma.calls[0]["messages"][-1]["content"]).endswith("[voice] /thinking on")
        finally:
            await stop(harness)


async def test_every_message_starts_the_30_minutes_again_and_it_ends_without_a_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(time.time())
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi.")), clock=clock)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        clock.now += 20 * 60
        deliver(folder, "hello", T0 + 1)  # a message from the user, 20 minutes in: 30 more from here
        await until(lambda: len(sent(folder)) == 2)
        assert app.thinking.until(ME) == pytest.approx(clock.now + 1_800)
        clock.now += 29 * 60
        assert app.thinking.on(ME)
        clock.now += 2 * 60  # 31 minutes without a message from the user
        assert not app.thinking.on(ME)
        await asyncio.sleep(0.3)
        assert len(sent(folder)) == 2  # nothing is sent when it ends
    finally:
        await stop(harness)


async def test_a_restart_inside_the_30_minutes_keeps_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(time.time())
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock)
    try:
        deliver(harness.folder, "/thinking on", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        first = harness.app.thinking.until(ME)
        assert first == pytest.approx(clock.now + 1_800)
    finally:
        await stop(harness)
    clock.now += 10 * 60
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock)
    try:
        assert restarted.app.thinking.on(ME) and restarted.app.thinking.until(ME) == first  # read from the database
        deliver(restarted.folder, "/status", at(clock))  # a message: the 30 minutes start again from now
        await until(lambda: len(sent(restarted.folder)) == 2)
        assert f"Deep Thinking: on until {ends_at(clock)}" in sent(restarted.folder)[1]
    finally:
        await stop(restarted)
    clock.now += 31 * 60  # 31 minutes without a message: off, whatever restarts
    later = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock)
    try:
        assert not later.app.thinking.on(ME)
        deliver(later.folder, "/status", at(clock))
        await until(lambda: len(sent(later.folder)) == 3)
        assert "Deep Thinking: off" in sent(later.folder)[2]
    finally:
        await stop(later)


async def test_status_and_help_name_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        deliver(harness.folder, "/status", T0)
        deliver(harness.folder, "/help", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert "Deep Thinking: off" in sent(harness.folder)[0]
        assert "/thinking - Deep Thinking" in sent(harness.folder)[1]
    finally:
        await stop(harness)


async def test_where_the_context_is_under_32768_tokens_it_stays_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 16 GB tier (16,384 tokens): the thought's room would take the whole history."""
    harness = await start(tmp_path, monkeypatch, FakeGemma(), context_tokens=16_384)
    try:
        deliver(harness.folder, "/thinking on", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == [prompts.THINKING_NO_ROOM] and not harness.app.thinking.on(ME)
    finally:
        await stop(harness)


async def test_without_a_context_line_in_the_config_it_stays_off_and_says_what_to_put_in_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 4 Nov, promises-2: without `context_tokens` (an install from before 0.9.7, or one where setup could not
    read the model's) `Config.small_context` is False, so /thinking on was accepted with no room kept for the thought."""
    harness = await start(tmp_path, monkeypatch, FakeGemma(), context_tokens=None)
    try:
        deliver(harness.folder, "/thinking on", T0)
        deliver(harness.folder, "/thinking", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == [prompts.THINKING_NO_CONTEXT] * 2 and not harness.app.thinking.on(ME)
    finally:
        await stop(harness)


@pytest.mark.parametrize("tokens", [32_768, 65_536])
async def test_a_context_of_32768_tokens_or_more_in_the_config_leaves_room_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tokens: int
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), context_tokens=tokens)
    try:
        deliver(harness.folder, "/thinking on", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert (
            harness.app.thinking.on(ME)
            and "Deep Thinking is on for your texts and voice notes until" in sent(harness.folder)[0]
        )
    finally:
        await stop(harness)


# the scope: her answers to texts and voice notes, nothing else


def efforts(gemma: FakeGemma) -> list[tuple[str, int]]:
    """Each request's reasoning effort and cap, in order."""
    return [(str(call["reasoning_effort"]), int(call["max_tokens"])) for call in gemma.calls]


async def test_texts_and_voice_notes_think_while_it_is_on_and_not_after_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        gemma = FakeGemma(text("One."), text("Two."), text("Three."))
        harness = await start(tmp_path, monkeypatch, gemma, voice_url=service.url)
        try:
            folder = harness.folder
            deliver(folder, "/thinking on", T0)
            deliver(folder, "what is a good question about free will?", T0 + 1)
            await until(lambda: "One." in sent(folder))
            put(folder, "v1.m4a", M4A)
            deliver(folder, None, T0 + 2, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: "Two." in sent(folder))
            deliver(folder, "/thinking off", T0 + 3)
            deliver(folder, "and another one?", T0 + 4)
            await until(lambda: "Three." in sent(folder))
            assert efforts(gemma) == [("high", 600 + BUDGET_TOKENS)] * 2 + [("none", 600)]
        finally:
            await stop(harness)


async def test_a_thinking_answer_keeps_room_for_the_thought_in_her_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("One."), text("Two."))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        folder, app = harness.folder, harness.app
        extras: list[int] = []
        real = app.context.enforce_limit

        def spy(conversation: object, *, extra_reply: int = 0) -> object:
            extras.append(extra_reply)
            return real(conversation, extra_reply=extra_reply)  # type: ignore[arg-type]

        monkeypatch.setattr(app.context, "enforce_limit", spy)
        deliver(folder, "hello", T0)
        await until(lambda: "One." in sent(folder))
        deliver(folder, "/thinking on", T0 + 1)
        deliver(folder, "hello again", T0 + 2)
        await until(lambda: "Two." in sent(folder))
        assert extras == [0, BUDGET_TOKENS]  # a normal answer's reserve is as it was
    finally:
        await stop(harness)


async def test_a_call_a_practice_turn_and_the_warm_up_never_think_while_it_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Hello there."), text("Wat leuk! Waar woon je?"), text(""))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1 and app.thinking.on(ME))
        app.call_started(ME)  # a call: its turn is a call's, short and without thinking
        turn = await app.call_turn(ME, "hi, how are you?", lambda: None)
        assert turn.text == "Hello there."
        app.call_ended()
        assert efforts(gemma) == [("none", app.call_host.max_tokens)]
        deliver(folder, "/babble nl", T0 + 1)
        await until(lambda: len(sent(folder)) == 2)
        deliver(folder, "Ik woon in Utrecht.", T0 + 2)  # practice: her tutor's turn, no tools, no thinking
        await until(lambda: len(sent(folder)) == 3)
        assert efforts(gemma)[1] == ("none", 600) and gemma.calls[1]["tools"] is None
        await app.brain.warm_up(app.store.current_conversation())  # a call's warm-up
        assert efforts(gemma)[2][0] == "none"
        assert app.thinking.on(ME)  # it was on throughout
    finally:
        await stop(harness)


# the busy note ("still working on your answer") waits longer for a thinking answer

NAME = DEFAULT_NAME
QUESTION = "what is it like to be a bat?"


def pending() -> "asyncio.Future[Completion]":
    """A model request that has not come back."""
    return asyncio.get_running_loop().create_future()


def test_the_busy_note_waits_3_minutes_for_a_thinking_answer_and_never_less_than_its_usual_wait(store: Store) -> None:
    switch = thinking_module.DeepThinking(store)
    assert thinking_module.BUSY_NOTE_SECONDS == 180.0
    assert switch.busy_wait(60.0) == 180.0  # 60 s for an answer that does not think: 180 s for one that does
    assert switch.busy_wait(5.0) == 180.0
    assert switch.busy_wait(300.0) == 300.0  # a wait chosen longer stays


async def test_a_thinking_answer_gets_the_busy_note_only_after_the_longer_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(thinking_module, "BUSY_NOTE_SECONDS", 1.2)  # 3 minutes, shortened for the test
    slow, usual = pending(), pending()
    gemma = FakeGemma(slow, usual)
    harness = await start(tmp_path, monkeypatch, gemma, busy_note_seconds=0.2)
    busy = prompts.BUSY.format(name=NAME)
    try:
        folder = harness.folder
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        began = time.monotonic()
        deliver(folder, QUESTION, T0 + 1)
        await until(lambda: len(gemma.calls) == 1)
        await asyncio.sleep(0.7)  # three times the usual wait: no note yet
        assert busy not in sent(folder)
        await until(lambda: busy in sent(folder))  # it comes, after the longer wait
        assert time.monotonic() - began >= 1.1
        slow.set_result(text("Strange."))
        await until(lambda: "Strange." in sent(folder))
        deliver(folder, "/thinking off", T0 + 2)
        await until(lambda: sent(folder)[-1] == prompts.THINKING_OFF)
        count = sent(folder).count(busy)
        deliver(folder, "and a dog?", T0 + 3)  # not thinking now: the usual wait
        await until(lambda: sent(folder).count(busy) == count + 1)
        usual.set_result(text("Friendly."))
        await until(lambda: "Friendly." in sent(folder))
    finally:
        await stop(harness)


# a call that starts while she thinks: her request is stopped and the message is answered again after the call, never
# once a tool of that answer has run


async def test_a_call_that_starts_while_she_thinks_stops_it_and_the_text_is_answered_after_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thinking = pending()
    gemma = FakeGemma(thinking)
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, QUESTION, T0 + 1)
        await until(lambda: len(gemma.calls) == 1)  # her thinking request is on its way
        on_a_call(app)  # a call starts
        await until(
            lambda: thinking.cancelled() and app._worker_idle.is_set()
        )  # Ollama's slot is free, so is the worker
        # review of 4 Nov, calls-3: it waits for the call's end as a text sent during the call does (owner, 23 Oct), so it
        # gets code's line at once, once a call, and she says so on the call; no answer, no other note, no failure line
        await until(lambda: len(sent(folder)) == 2)
        assert sent(folder)[1:] == [prompts.CALL_TEXT_WAITS]
        assert app.calls is not None and app.calls._call is not None and app.calls._call.text_owed
        # review of 5 Nov (DLG-0983-05): the call knows its text was stopped, so her line comes right after her greeting
        assert app.calls._call.text_stopped
        assert [m.text for m in app.store.unanswered_incoming()] == [QUESTION]  # it waits for the call's end
        gemma.add(text("Hello, caller."))
        turn = await app.call_turn(ME, "hi there", lambda: None)  # the call's own turn does not wait for the text
        assert turn.text == "Hello, caller."
        gemma.add(text("Strange, probably."))
        call_over(app)
        await until(lambda: "Strange, probably." in sent(folder))
        assert app.store.unanswered_incoming() == []
        assert efforts(gemma) == [
            ("high", 600 + BUDGET_TOKENS),
            ("none", app.call_host.max_tokens),  # the call's turn: short, no thinking
            ("high", 600 + BUDGET_TOKENS),  # the text, again, still thinking
        ]
        assert app.thinking.on(ME)
        # review of 4 Nov, calls-2: the stopped text waits with no place in the conversation, as a text during a call
        # does, so the call's turn never saw it unanswered; the redo has it last, after the call's turns
        assert QUESTION not in json.dumps(gemma.calls[1]["messages"])
        users = [m["content"] for m in gemma.calls[2]["messages"] if m["role"] == "user"]
        assert users[-1].endswith(QUESTION) and "[call] hi there" in users[-2]
    finally:
        await stop(harness)


async def test_a_tool_that_already_ran_lets_the_answer_finish_without_thinking_when_a_call_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tools never run twice, so after the first one the answer is not stopped; but its thought is dropped (review of 4
    Nov, calls-1): the request on its way is cancelled and sent again without thinking, so the worker is free within
    seconds and the call's first turn answers within its limit instead of waiting for up to 143 s of thought."""
    later = pending()
    gemma = FakeGemma(
        tool("remember", fact="Family: The user's sister is Anna."), later, text("Noted: your sister is Anna.")
    )
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, "remember that my sister is Anna", T0 + 1)
        await until(lambda: len(gemma.calls) == 2)  # remember ran; her next request, thinking, is on its way
        on_a_call(app)
        await until(lambda: later.cancelled())  # Ollama's slot is free at once
        await until(lambda: "Noted: your sister is Anna." in sent(folder) and app._worker_idle.is_set())
        gemma.add(text("Hello, caller."))
        async with asyncio.timeout(5):  # the call's turn: its own limit is 45 s, never spent waiting for the thought
            turn = await app.call_turn(ME, "hi there", lambda: None)
        assert turn.text == "Hello, caller."
        assert efforts(gemma) == [
            ("high", 600 + BUDGET_TOKENS),
            ("high", 600 + BUDGET_TOKENS),  # the one cancelled
            ("none", 600),  # the same request, without the thought
            ("none", app.call_host.max_tokens),  # the call's turn
        ]
        assert len(app.profile.facts()) == 1  # saved once
        call_over(app)
        await asyncio.sleep(0.3)
        assert len(gemma.calls) == 4 and len(app.profile.facts()) == 1  # never asked or saved again
        assert app.store.unanswered_incoming() == []
    finally:
        await stop(harness)


async def test_a_yes_on_a_call_never_confirms_a_device_question_that_went_out_as_a_text_during_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 4 Nov, calls-1 (second pass): a tool ran in a thinking answer before the call began, so the call only drops
    its thought (tools never run twice) and the plain request that follows asks for an "ask first" device. Its question and
    code's note go out as texts during the call, and the caller's first "Okay." switched the heater: a yes never confirms
    something the caller did not hear (`calls.md`). Only a yes said on a call answers a question asked on one; code's note
    says why nothing switched, and her model answers the turn."""
    thinking = pending()
    gemma = FakeGemma(
        tool("device_status", device="heater"),
        thinking,
        tool("device_set", device="heater", power="off"),
        text("The heater is on."),
        text("Fine, thanks."),
    )
    home = Home(tmp_path, monkeypatch)
    harness = await start(
        tmp_path, monkeypatch, gemma, tapo_devices=DEVICE_SPECS, tapo_helper=home.helper, **call_settings(tmp_path)
    )
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, "is the heater on? switch it off if it is", T0 + 1)
        await until(lambda: len(gemma.calls) == 2)  # device_status ran; her next request, thinking, is on its way
        on_a_call(app)
        await until(lambda: thinking.cancelled())  # its thought is dropped; the answer finishes without it
        async with asyncio.timeout(5):
            turn = await app.call_turn(ME, "Okay.", lambda: None)  # the first thing she says: after the text's answer
        assert sent(folder)[1:3] == [  # the question went out as texts, during the call
            "The heater is on.\nShall I switch heater off?",
            "Devices: reply yes within 2 minutes to switch heater off",
        ]
        assert home.sets() == [] and turn.text == "Fine, thanks."  # nothing switched; her model answered the turn
        assert turn.code_text == "" and not turn.asks_yes
        note = "Devices: heater not switched off: the question went as a text, never asked on the call"
        await until(lambda: note in sent(folder))
        assert app.devices is not None and not app.devices.pending
        call_over(app)
        await asyncio.sleep(0.2)
        assert home.sets() == []
    finally:
        await stop(harness)


async def test_a_text_that_came_during_the_call_is_answered_after_the_one_it_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thinking = pending()
    gemma = FakeGemma(thinking)
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, QUESTION, T0 + 1)
        await until(lambda: len(gemma.calls) == 1)
        on_a_call(app)
        await until(lambda: thinking.cancelled() and app._worker_idle.is_set())
        await until(lambda: prompts.CALL_TEXT_WAITS in sent(folder))  # the stopped text's line, once a call
        deliver(folder, "and what about a dog?", T0 + 2)  # during the call: it waits too
        await until(lambda: len(app.store.unanswered_incoming()) == 2)
        gemma.add(text("A bat's life is strange."), text("A dog's is friendlier."))
        call_over(app)
        await until(lambda: "A dog's is friendlier." in sent(folder))
        answers = [line for line in sent(folder) if line.startswith("A ")]
        assert answers == ["A bat's life is strange.", "A dog's is friendlier."]  # in the order they were asked
        assert sent(folder).count(prompts.CALL_TEXT_WAITS) == 1  # one line a call for the account, not one a text
    finally:
        await stop(harness)


async def test_a_call_answered_as_text_only_stops_a_thinking_answer_as_any_call_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merge of dev-0.9.9 ("reply by text", 4 Nov): a call answered while her voice is not working uses the model too. It
    starts as every call does (`CallHost.call_started`: tests/test_calls.py pins that it is called for one), so the
    thinking answer on its way stops, the text waits for the call's end with code's line, and the call's own turns,
    answered as texts, never think; after the call the text is answered again, thinking."""
    thinking = pending()
    gemma = FakeGemma(thinking)
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, QUESTION, T0 + 1)
        await until(lambda: len(gemma.calls) == 1)  # her thinking request is on its way
        assert app.calls is not None
        call = app.calls._new_call(ME, 5, practice_ok=True, text_only=True)  # her voice rests: she listens, texts back
        app.calls._call = call
        app.call_started(ME, practice=call.practice)
        await until(lambda: thinking.cancelled() and app._worker_idle.is_set())  # Ollama's slot and the worker are free
        await until(lambda: len(sent(folder)) == 2)
        assert sent(folder)[1:] == [prompts.CALL_TEXT_WAITS]  # no answer yet: it waits for the call's end
        assert [m.text for m in app.store.unanswered_incoming()] == [QUESTION]
        gemma.add(text("Hello, caller."))
        turn = await app.call_host.call_turn(ME, "hi there", lambda: None, text_only=True)
        assert turn.text == "Hello, caller." and turn.reply_id is not None
        assert await app.call_host.call_reply_text(ME, turn.reply_id)  # her answer goes whole, as a text
        assert "Hello, caller." in sent(folder)
        gemma.add(text("Strange, probably."))
        call_over(app)
        await until(lambda: "Strange, probably." in sent(folder))
        assert efforts(gemma) == [
            ("high", 600 + BUDGET_TOKENS),
            ("none", app.call_host.max_tokens),  # the call's turn: short, no thinking, heard or read
            ("high", 600 + BUDGET_TOKENS),  # the text, again, still thinking
        ]
        assert app.thinking.on(ME) and app.store.unanswered_incoming() == []
    finally:
        await stop(harness)


async def test_a_call_that_starts_before_the_answer_begins_is_not_missed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 4 Nov, correctness-5: the answer waits for a held practice recap before it begins, and a call that starts
    in that wait found no event to set (`begin` makes a fresh one): her thinking request went to Ollama during the call
    and nothing cancelled it, so the call's turns waited behind it. The answer stops before its first request."""
    gemma = FakeGemma(text("Strange, probably."))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        held = [SimpleNamespace(aci=ME)]  # one ended practice session whose recap is still to go out

        async def recap(session: object) -> None:
            on_a_call(app)  # a call starts while the recap is written
            await asyncio.sleep(0.05)

        monkeypatch.setattr(app.babble, "held", lambda: held.pop() if held else None)
        monkeypatch.setattr(app.babble, "finish", recap)
        deliver(folder, QUESTION, T0 + 1)
        await until(lambda: held == [] and app._worker_idle.is_set())
        await asyncio.sleep(0.3)
        assert gemma.calls == []  # no thinking request is sent during the call
        assert [m.text for m in app.store.unanswered_incoming()] == [QUESTION]  # it waits for the call's end
        call_over(app)
        await until(lambda: "Strange, probably." in sent(folder))
        assert efforts(gemma) == [("high", 600 + BUDGET_TOKENS)]  # after the call, thinking, as the 30 minutes hold
        assert app.store.unanswered_incoming() == []
    finally:
        await stop(harness)


async def test_after_a_call_longer_than_the_30_minutes_the_text_is_answered_without_thinking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(time.time())
    thinking = pending()
    gemma = FakeGemma(thinking)
    harness = await start(tmp_path, monkeypatch, gemma, clock=clock, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", at(clock))
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, QUESTION, at(clock) + 1)
        await until(lambda: len(gemma.calls) == 1)
        on_a_call(app)
        await until(lambda: thinking.cancelled() and app._worker_idle.is_set())
        clock.now += 31 * 60  # a long call: no message from the user for 31 minutes
        gemma.add(text("Strange, probably."))
        call_over(app)
        await until(lambda: "Strange, probably." in sent(folder))
        assert efforts(gemma) == [("high", 600 + BUDGET_TOKENS), ("none", 600)]
        assert not app.thinking.on(ME) and sent(folder)[-1] == "Strange, probably."  # and no word about it ending
    finally:
        await stop(harness)


async def test_a_text_a_call_stopped_is_not_judged_stale_again_when_it_is_answered_after_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 4 Nov, calls-6: the downtime check ran again for the redo, so a text sent a minute before this start, which
    she was answering when a call of 11 minutes came, was marked failed and got 'I was offline ...' instead of its answer
    (and with that note sent already at this start, no word at all). A redo is never judged stale: she was on a call."""
    clock = Clock(time.time())
    thinking = pending()
    gemma = FakeGemma(thinking)
    harness = await start(tmp_path, monkeypatch, gemma, clock=clock, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", at(clock))
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, QUESTION, at(clock) - 60_000)  # sent a minute before this start: inside the recovery window
        await until(lambda: len(gemma.calls) == 1)
        on_a_call(app)
        await until(lambda: thinking.cancelled() and app._worker_idle.is_set())
        clock.now += 11 * 60  # the call lasts longer than the recovery window (10 minutes)
        gemma.add(text("Strange, probably."))
        call_over(app)
        await until(lambda: "Strange, probably." in sent(folder))
        assert [line for line in sent(folder) if line.startswith("I was offline")] == []
        assert app.store.unanswered_incoming() == [] and efforts(gemma)[-1] == ("high", 600 + BUDGET_TOKENS)
    finally:
        await stop(harness)


async def test_the_calls_notes_reach_ollama_before_the_text_it_stopped_is_answered_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 4 Nov, calls-4: the worker is released the moment the app is told the call is over, and its thinking
    answer took Ollama's one slot for minutes before the call's summary (with its start and end), which `calls.md` says
    goes out at once. While it thinks, the worker waits for the notes after the call (at most a minute). A call that ended
    with CALL_TOO_LONG_NOTE, as the one that kept calls-1's answer waiting does."""
    thinking = pending()
    gemma = FakeGemma(thinking)
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        assert app.calls is not None
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, QUESTION, T0 + 1)
        await until(lambda: len(gemma.calls) == 1)
        on_a_call(app)
        await until(lambda: thinking.cancelled() and app._worker_idle.is_set())
        call = app.calls._call
        assert call is not None
        call.timeouts, call.user_turns = 1, 2  # an answer the turn's limit stopped; two turns: a summary is written
        call.lines = ["User: hi there", "Estia: Hello, caller."]
        gemma.add(text("- she said hello"), text("Strange, probably."))  # the summary's request, then the text's
        app.calls._call = None
        app.call_ended()  # the app is told, which releases the worker, as the end of a call does ...
        await app.calls._after(call, "13:00-13:11")  # ... and the notes after the call go, in the same breath
        await until(lambda: "Strange, probably." in sent(folder))
        assert [(c["max_tokens"], c["reasoning_effort"]) for c in gemma.calls[1:]] == [
            (300, "none"),  # the call's summary first
            (600 + BUDGET_TOKENS, "high"),  # then the text, thinking again
        ]
        texts = sent(folder)
        assert texts.index(prompts.CALL_TOO_LONG_NOTE) < texts.index("Strange, probably.")
        assert any(line.startswith("Call summary") or "she said hello" in line for line in texts)
    finally:
        await stop(harness)


async def test_the_bridge_is_busy_while_a_stopped_text_waits_for_the_notes_after_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 4 Nov, calls-3: the worker that holds a stopped text for the call's notes (`after_notes`) counted as idle
    (`_processing` was cleared when the call began, the queue was empty, the call was over), so the mail sorter's and the
    nightly prune's request could reach Ollama ahead of that answer, and ahead of the call's summary if it landed first.
    Before Deep Thinking the worker was busy as soon as the call ended."""
    thinking, summary = pending(), pending()
    gemma = FakeGemma(thinking)
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        assert app.calls is not None
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, QUESTION, T0 + 1)
        await until(lambda: len(gemma.calls) == 1)
        on_a_call(app)
        await until(lambda: thinking.cancelled() and app._worker_idle.is_set())
        assert app._is_busy()  # the call is on
        call = app.calls._call
        assert call is not None
        call.user_turns, call.lines = 2, ["User: hi there", "Estia: Hello, caller."]  # a summary is written
        gemma.add(summary, text("Strange, probably."))
        calls = app.calls

        async def over() -> None:  # the end of a call: the app is told, and its notes owed in the same breath
            calls._call = None
            app.call_ended()  # the worker holds the text for the notes after the call
            await calls._after(call, "13:00-13:11")

        notes = asyncio.create_task(over())
        await until(lambda: len(gemma.calls) == 2)  # the call's summary is on its way to Ollama
        assert app._no_call.is_set() and app.queue.empty()  # no call, nothing queued: the worker holds the one text
        assert app._is_busy()  # the held text counts: the profiler, the sorter and the prune keep away from Ollama
        summary.set_result(text("- she said hello"))
        await notes
        await until(lambda: "Strange, probably." in sent(folder))
        await until(lambda: not app._is_busy())  # answered: idle again
    finally:
        await stop(harness)


async def test_a_practice_calls_typed_text_and_recap_do_not_wait_out_the_notes_wait_while_she_thinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 4 Nov, calls-2: `_after` clears its event, then the practice step drains the queue (texts typed during
    the call, answered as practice, then the recap), while the worker that holds such a text waits for that very event:
    nothing ended the wait but its limit, so the practice answer, the recap and the call's own note came a minute late
    (`_after` says "sent at once"). The drain never waits for the notes, which only owe the rest of them."""
    monkeypatch.setattr(thinking_module, "AFTER_CALL_SECONDS", 4.0)  # a minute, shortened for the test
    gemma = FakeGemma(text("Leuk! Waar in Utrecht?"), recap_answer())
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        assert app.calls is not None
        deliver(folder, "/thinking on", T0)
        deliver(folder, "/babble nl", T0 + 1)
        await until(lambda: len(sent(folder)) == 2)
        on_a_call(app)  # a practice call
        call = app.calls._call
        assert call is not None and call.practice
        # typed during the call: the worker holds it until the call is over, waiting for the notes after it
        deliver(folder, "Ik woon in Utrecht.", T0 + 2)
        await until(lambda: bool(app.store.unanswered_incoming()) and app.queue.empty())
        call.user_turns, call.answered = 1, True  # she spoke in it; one from a device of the user's
        app.calls._call = None
        app.call_ended()  # the app is told, as the end of a call does ...
        began = time.monotonic()
        async with asyncio.timeout(10):
            await app.calls._after(call, "13:00-13:11")  # ... and the notes after the call go, in the same breath
        assert time.monotonic() - began < 2.0  # the 4 s limit was the whole wait before the fix
        assert sent(folder)[2] == "Leuk! Waar in Utrecht?" and sent(folder)[3].startswith(
            "📚 Practice recap (Dutch, A2)"
        )
        assert sent(folder)[4] == prompts.CALL_ANSWERED_NOTE.format(span="13:00-13:11")
        assert app.store.unanswered_incoming() == [] and app.babble.running(ME) is None
        assert app.calls._after_done.is_set()  # and nothing is owed afterwards
    finally:
        await stop(harness)


async def test_a_photo_stopped_by_a_call_is_kept_for_its_answer_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thinking = pending()
    gemma = FakeGemma(thinking)
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        image = png(40, 30)
        file = put(folder, "p1.png", image)
        deliver(folder, "what colour is this?", T0 + 1, attachments=[photo("p1.png", image)])
        await until(lambda: len(gemma.calls) == 1)
        on_a_call(app)
        await until(lambda: thinking.cancelled() and app._worker_idle.is_set())
        assert file.exists()  # kept until it is answered: it is answered after the call
        gemma.add(text("It is blue."))
        call_over(app)
        await until(lambda: "It is blue." in sent(folder))
        again = gemma.calls[1]["messages"][-1]["content"]
        assert again[1]["image_url"]["url"].startswith("data:image/jpeg;base64,/9j/")  # shown again
        await until(lambda: not file.exists())  # deleted once answered, as ever
    finally:
        await stop(harness)


async def test_a_voice_note_stopped_by_a_call_is_not_heard_or_echoed_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        thinking = pending()
        gemma = FakeGemma(thinking)
        harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path) | {"voice_url": service.url})
        try:
            folder, app = harness.folder, harness.app
            deliver(folder, "/voice echo on", T0)
            deliver(folder, "/thinking on", T0 + 1)
            await until(lambda: len(sent(folder)) == 2)
            put(folder, "v1.m4a", M4A)
            deliver(folder, None, T0 + 2, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: len(gemma.calls) == 1)
            on_a_call(app)
            await until(lambda: thinking.cancelled() and app._worker_idle.is_set())
            gemma.add(text("Done."))
            call_over(app)
            await until(lambda: "Done." in sent(folder))
            heard = [r for r in service.requests if r[1].startswith("/v1/audio/transcriptions")]
            assert len(heard) == 1  # the transcript was kept: the audio is not sent to be heard again
            assert sent(folder).count('🎙️ "Turn off the lamp."') == 1
        finally:
            await stop(harness)


async def test_the_answer_to_a_typed_yes_for_a_found_video_is_never_stopped_by_a_call_but_stops_thinking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The yes has taken the found video's offer: asked again after the call, the answer would lack its unlocked link, so
    that answer finishes first (the one case where a thinking answer with no tool run yet is not stopped), but without
    its thought: the request on its way is cancelled and sent again plain (review of 4 Nov, calls-1)."""
    from tests.test_app_video_find import ASK, LINK, found, helper_calls

    gemma = FakeGemma(tool("find_video", what="Ivan on Tech", newest=True), text("Texted. Reply YES there."))
    harness = await found(tmp_path, monkeypatch, gemma, context_tokens=32_768, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, ASK, T0 + 1)
        await until(lambda: "Texted. Reply YES there." in sent(folder))
        asking = pending()
        gemma.add(asking, tool("read_link", url=f"{LINK}aaaaaaaaaaa"), text("Episode 212 is about beams [0:00]."))
        deliver(folder, "yes", T0 + 2)
        await until(lambda: len(gemma.calls) == 3)  # the yes turn's first request, thinking, on its way
        on_a_call(app)
        await until(lambda: asking.cancelled())  # not stopped, but its thought is dropped: Ollama's slot is free
        await until(lambda: "Episode 212 is about beams [0:00]." in sent(folder) and app._worker_idle.is_set())
        assert helper_calls(tmp_path) == [["captions", "--until", "7200", "--", "aaaaaaaaaaa"]]  # read once
        assert efforts(gemma)[2:] == [("high", 600 + BUDGET_TOKENS), ("none", 600), ("none", 600)]
        gemma.add(text("Hello, caller."))
        async with asyncio.timeout(5):
            assert (await app.call_turn(ME, "hi there", lambda: None)).text == "Hello, caller."
        call_over(app)
        await asyncio.sleep(0.3)
        assert len(gemma.calls) == 6 and app.store.unanswered_incoming() == []  # no redo after the call
    finally:
        await stop(harness)


# one switch for each allowed account (owner, 4 Nov): two accounts on one bridge, sharing one chat history and one model

BOTH = frozenset({ME, PARTNER})
THINKS = ("high", 600 + BUDGET_TOKENS)  # a thinking request's effort and cap
PLAIN = ("none", 600)  # and a plain one's


async def test_only_the_account_that_switched_it_on_gets_thinking_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("One."), text("Two."), text("Three."), text("Four."))
    harness = await start(tmp_path, monkeypatch, gemma, allowed_acis=BOTH)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)  # ME's switch: PARTNER's stays off
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, "what is a good question about free will?", T0 + 1)
        deliver(folder, "and what is a good dinner?", T0 + 2, source=PARTNER)
        deliver(folder, "and a good pudding?", T0 + 3, source=PARTNER)
        deliver(folder, "and a second question about free will?", T0 + 4)
        await until(lambda: "Four." in sent(folder))
        assert efforts(gemma) == [THINKS, PLAIN, PLAIN, THINKS]  # by the account each message is from
        assert sent_to(folder)[1:] == [(ME, "One."), (PARTNER, "Two."), (PARTNER, "Three."), (ME, "Four.")]
        assert app.thinking.on(ME) and not app.thinking.on(PARTNER)
    finally:
        await stop(harness)


async def test_a_message_of_another_account_never_starts_the_30_minutes_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(time.time())
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi.")), clock=clock, allowed_acis=BOTH)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", at(clock))
        await until(lambda: len(sent(folder)) == 1)
        first = app.thinking.until(ME)
        assert first == pytest.approx(clock.now + 1_800)
        clock.now += 20 * 60
        deliver(folder, "hello", at(clock), source=PARTNER)  # 20 minutes in, from the other account
        await until(lambda: "Hi." in sent(folder))
        assert app.thinking.until(ME) == first  # ME's 30 minutes are ME's alone
        assert app.thinking.until(PARTNER) is None  # and a message never turns a switch on
        clock.now += 11 * 60  # 31 minutes since ME's own message
        assert not app.thinking.on(ME)
        await asyncio.sleep(0.3)
        assert len(sent(folder)) == 2  # nothing is sent when it ends
    finally:
        await stop(harness)


async def test_each_accounts_own_messages_start_only_its_own_30_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(time.time())
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi."), text("Hello.")), clock=clock, allowed_acis=BOTH)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", at(clock))
        deliver(folder, "/thinking on", at(clock), source=PARTNER)
        await until(lambda: len(sent(folder)) == 2)
        first = app.thinking.until(ME)
        clock.now += 10 * 60
        deliver(folder, "hello", at(clock), source=PARTNER)
        await until(lambda: "Hi." in sent(folder))
        assert app.thinking.until(ME) == first and app.thinking.until(PARTNER) == pytest.approx(clock.now + 1_800)
        clock.now += 10 * 60
        deliver(folder, "hello", at(clock))
        await until(lambda: "Hello." in sent(folder))
        assert app.thinking.until(ME) == pytest.approx(clock.now + 1_800)
        assert app.thinking.until(PARTNER) == pytest.approx(clock.now - 600 + 1_800)  # theirs, from 10 minutes before
    finally:
        await stop(harness)


async def test_a_restart_keeps_each_accounts_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(time.time())
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock, allowed_acis=BOTH)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", at(clock))
        await until(lambda: len(sent(folder)) == 1)
        clock.now += 10 * 60
        deliver(folder, "/thinking on", at(clock), source=PARTNER)
        await until(lambda: len(sent(folder)) == 2)
        mine, theirs = app.thinking.until(ME), app.thinking.until(PARTNER)
        assert mine is not None and theirs == pytest.approx(mine + 600)
    finally:
        await stop(harness)
    clock.now += 5 * 60
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock, allowed_acis=BOTH)
    try:
        assert restarted.app.thinking.until(ME) == mine and restarted.app.thinking.until(PARTNER) == theirs
    finally:
        await stop(restarted)
    clock.now += 21 * 60  # 36 minutes after ME's last message, 26 after PARTNER's
    later = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock, allowed_acis=BOTH)
    try:
        assert not later.app.thinking.on(ME) and later.app.thinking.until(PARTNER) == theirs
    finally:
        await stop(later)


async def test_status_and_a_plain_thinking_show_only_the_askers_own_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(time.time())
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock, allowed_acis=BOTH)
    try:
        folder = harness.folder
        deliver(folder, "/thinking on", at(clock))
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, "/status", at(clock) + 1)
        deliver(folder, "/status", at(clock) + 2, source=PARTNER)
        deliver(folder, "/thinking", at(clock) + 3, source=PARTNER)
        deliver(folder, "/thinking", at(clock) + 4)
        await until(lambda: len(sent(folder)) == 5)
        mine = [line for who, line in sent_to(folder) if who == ME]
        theirs = [line for who, line in sent_to(folder) if who == PARTNER]
        assert f"Deep Thinking: on until {ends_at(clock)}" in mine[1]
        assert mine[2] == prompts.THINKING_STATE_ON.format(time=ends_at(clock))
        assert "Deep Thinking: off" in theirs[0] and f"on until {ends_at(clock)}" not in theirs[0]
        assert theirs[1] == prompts.THINKING_STATE_OFF
    finally:
        await stop(harness)


async def test_off_is_answered_at_once_and_stops_the_answer_that_thinks_which_comes_again_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/thinking off is answered the moment it arrives, as /lock is, not behind an answer in the queue. The answer of the
    account that is thinking stops (its request to Ollama is cancelled, so the slot is free), its message is answered
    again at once without thinking (no call to wait for, so no line about one), and that account's messages still in the
    queue are answered without thinking too."""
    thinking = pending()
    gemma = FakeGemma(thinking, text("A bat's life is strange."), text("A dog's is friendlier."))
    harness = await start(tmp_path, monkeypatch, gemma, allowed_acis=BOTH)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, QUESTION, T0 + 1)
        await until(lambda: len(gemma.calls) == 1)  # her thinking request is on its way
        deliver(folder, "and what about a dog?", T0 + 2)  # waits in the queue behind it
        deliver(folder, "/thinking off", T0 + 3)
        await until(lambda: prompts.THINKING_OFF_STOPPED in sent(folder) and thinking.cancelled())
        assert sent(folder)[1] == prompts.THINKING_OFF_STOPPED  # said before any answer, and the request is cancelled
        await until(lambda: "A dog's is friendlier." in sent(folder))
        assert efforts(gemma) == [THINKS, PLAIN, PLAIN]  # the message again, and the one behind it: no thought
        assert sent(folder)[2:] == [
            "A bat's life is strange.",
            "A dog's is friendlier.",
        ]  # in the order they were asked
        assert prompts.CALL_TEXT_WAITS not in sent(folder) and not app.thinking.on(ME)
        assert app.store.unanswered_incoming() == []
        users = [m["content"] for m in gemma.calls[1]["messages"] if m["role"] == "user"]
        assert users[-1].endswith(QUESTION)  # the stopped message is answered as asked, last in the chat
    finally:
        await stop(harness)


async def test_off_from_another_account_is_answered_at_once_and_leaves_the_answer_that_thinks_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thinking = pending()
    gemma = FakeGemma(thinking)
    harness = await start(tmp_path, monkeypatch, gemma, allowed_acis=BOTH)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        deliver(folder, "/thinking on", T0 + 1, source=PARTNER)
        await until(lambda: len(sent(folder)) == 2)
        deliver(folder, QUESTION, T0 + 2)
        await until(lambda: len(gemma.calls) == 1)  # ME's thinking request is on its way
        deliver(folder, "/thinking off", T0 + 3, source=PARTNER)
        await until(lambda: sent_to(folder)[-1] == (PARTNER, prompts.THINKING_OFF))  # at once, while ME's answer thinks
        assert not thinking.done() and app.thinking.on(ME) and not app.thinking.on(PARTNER)
        thinking.set_result(text("Strange."))
        await until(lambda: (ME, "Strange.") in sent_to(folder))
        assert efforts(gemma) == [THINKS]  # asked once, still thinking: never stopped, never asked again
        assert app.thinking.on(ME) and app.store.unanswered_incoming() == []
    finally:
        await stop(harness)


async def test_off_is_answered_at_once_also_behind_an_answer_that_does_not_think(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow = pending()
    gemma = FakeGemma(slow)
    harness = await start(tmp_path, monkeypatch, gemma, allowed_acis=BOTH)
    try:
        folder = harness.folder
        deliver(folder, QUESTION, T0, source=PARTNER)  # PARTNER's plain answer is on its way
        await until(lambda: len(gemma.calls) == 1)
        deliver(folder, "/thinking off", T0 + 1)  # ME has it off already: the same reply, at once
        await until(lambda: sent_to(folder) == [(ME, prompts.THINKING_OFF)])
        assert not slow.done()  # PARTNER's answer is still on its way
        slow.set_result(text("Strange."))
        await until(lambda: (PARTNER, "Strange.") in sent_to(folder))
    finally:
        await stop(harness)


async def test_on_and_a_plain_thinking_keep_their_place_in_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only /thinking off jumps the queue: a message sent after /thinking on must be the first to think, so /thinking on
    waits for the answers before it, and a plain /thinking says what the state is once they are done."""
    clock = Clock(time.time())
    slow = pending()
    gemma = FakeGemma(slow)
    harness = await start(tmp_path, monkeypatch, gemma, clock=clock, allowed_acis=BOTH)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, QUESTION, at(clock), source=PARTNER)  # PARTNER's answer is on its way
        await until(lambda: len(gemma.calls) == 1)
        deliver(folder, "/thinking on", at(clock) + 1)
        deliver(folder, "/thinking", at(clock) + 2)
        await asyncio.sleep(0.4)
        assert sent(folder) == [] and not app.thinking.on(ME)  # both wait behind it
        slow.set_result(text("Strange."))
        await until(lambda: len(sent(folder)) == 3)
        assert sent(folder) == [
            "Strange.",
            prompts.THINKING_ON.format(time=ends_at(clock)),
            prompts.THINKING_STATE_ON.format(time=ends_at(clock)),
        ]
    finally:
        await stop(harness)


async def test_a_tool_that_already_ran_lets_the_answer_finish_without_thinking_when_its_account_turns_it_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As for a call: tools never run twice, so after the first one the answer is not stopped; its request on its way is
    cancelled and sent again plain, and the answer comes once, without its thought."""
    later = pending()
    gemma = FakeGemma(
        tool("remember", fact="Family: The user's sister is Anna."), later, text("Noted: your sister is Anna.")
    )
    harness = await start(tmp_path, monkeypatch, gemma, allowed_acis=BOTH)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, "remember that my sister is Anna", T0 + 1)
        await until(lambda: len(gemma.calls) == 2)  # remember ran; her next request, thinking, is on its way
        deliver(folder, "/thinking off", T0 + 2)
        await until(lambda: later.cancelled())  # Ollama's slot is free at once
        await until(lambda: "Noted: your sister is Anna." in sent(folder))
        assert sent(folder)[1] == prompts.THINKING_OFF_STOPPED  # said at once: the answer on its way comes without it
        assert efforts(gemma) == [THINKS, THINKS, PLAIN]  # the same request again, without the thought
        assert len(app.profile.facts()) == 1  # saved once
        await asyncio.sleep(0.3)
        assert len(gemma.calls) == 3 and app.store.unanswered_incoming() == []  # never asked or saved again
    finally:
        await stop(harness)


async def test_a_call_stops_the_answer_of_any_account_that_thinks_and_it_is_answered_after_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call is ME's, the answer that thinks is PARTNER's: it stops all the same, and PARTNER, whose text waits for
    the call's end, gets code's line in their own chat and their answer after the call, thinking again."""
    thinking = pending()
    gemma = FakeGemma(thinking)
    harness = await start(tmp_path, monkeypatch, gemma, allowed_acis=BOTH, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0, source=PARTNER)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, QUESTION, T0 + 1, source=PARTNER)
        await until(lambda: len(gemma.calls) == 1)  # PARTNER's thinking request is on its way
        on_a_call(app)  # ME calls: ME's own switch was never on
        await until(lambda: thinking.cancelled() and app._worker_idle.is_set())
        await until(lambda: len(sent(folder)) == 2)
        assert sent_to(folder)[1:] == [(PARTNER, prompts.CALL_TEXT_WAITS)]
        # never said on ME's call, whose greeting it must not follow (calls.text_waits: the caller's own text only)
        assert app.calls is not None and app.calls._call is not None
        assert not app.calls._call.text_owed and not app.calls._call.text_stopped
        assert [m.text for m in app.store.unanswered_incoming()] == [QUESTION]  # it waits for the call's end
        gemma.add(text("Strange, probably."))
        call_over(app)
        await until(lambda: "Strange, probably." in sent(folder))
        assert sent_to(folder)[-1] == (PARTNER, "Strange, probably.")
        assert efforts(gemma) == [THINKS, THINKS]  # again, thinking: PARTNER's 30 minutes still hold
        assert app.thinking.on(PARTNER) and not app.thinking.on(ME) and app.store.unanswered_incoming() == []
    finally:
        await stop(harness)


async def test_after_a_call_a_waiting_text_of_an_account_with_it_off_is_answered_plain_while_the_other_has_it_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Hello, caller."), text("Plain."))
    harness = await start(tmp_path, monkeypatch, gemma, allowed_acis=BOTH, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)  # ME's switch is on, PARTNER's is off
        await until(lambda: len(sent(folder)) == 1)
        on_a_call(app)  # ME calls
        deliver(folder, "hello", T0 + 1, source=PARTNER)  # PARTNER's text waits for the call's end
        await until(lambda: len(app.store.unanswered_incoming()) == 1)
        turn = await app.call_turn(ME, "hi there", lambda: None)
        assert turn.text == "Hello, caller."
        call_over(app)
        await until(lambda: "Plain." in sent(folder))
        assert efforts(gemma) == [("none", app.call_host.max_tokens), PLAIN]  # a call's turn, then a plain answer
    finally:
        await stop(harness)


# the last command typed wins; an off answered at once uses up no next-message rule of the messages before it; the line for
# the answer on its way is sent once (review of 4 Nov on this branch)


async def test_an_on_still_in_the_queue_loses_to_the_off_typed_after_it_and_the_text_between_them_is_plain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only /thinking off skips the queue, so an on typed before it, still waiting behind an answer, was handled after the
    off: it switched Deep Thinking on for 30 minutes again and the text between the two thought. The last command typed
    wins: the on switches nothing and says what the off said, in the words that exist."""
    slow = pending()
    gemma = FakeGemma(slow, text("A dog's is friendlier."))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, QUESTION, T0)  # her plain answer is on its way
        await until(lambda: len(gemma.calls) == 1)
        deliver(folder, "/thinking on", T0 + 1)  # waits behind it, in its place in the queue
        deliver(folder, "and what about a dog?", T0 + 2)  # so does this text
        deliver(folder, "/thinking off", T0 + 3)  # answered at once
        await until(lambda: sent(folder) == [prompts.THINKING_OFF])
        slow.set_result(text("A bat's life is strange."))
        await until(lambda: "A dog's is friendlier." in sent(folder))
        assert sent(folder) == [
            prompts.THINKING_OFF,  # the off, at once
            "A bat's life is strange.",
            prompts.THINKING_OFF,  # the on, in its turn: it switched nothing
            "A dog's is friendlier.",
        ]
        assert efforts(gemma) == [PLAIN, PLAIN] and not app.thinking.on(ME)
        assert app.store.unanswered_incoming() == []
    finally:
        await stop(harness)


async def test_a_restart_that_queues_the_waiting_on_again_still_sees_the_off_answered_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = int(time.time() * 1000)

    def prepare(store: Store) -> None:  # the store as the last run left it
        conversation = store.current_conversation().id
        store.add_incoming(conversation, ME, now - 3_000, "command", "/thinking on")  # waited behind an answer
        store.add_incoming(conversation, ME, now - 2_000, "text", "and what about a dog?")  # so did this text
        off = store.add_incoming(conversation, ME, now - 1_000, "command", "/thinking off")  # answered at once
        assert off is not None
        store.set_status(off, "answered")

    gemma = FakeGemma(text("A dog's is friendlier."))
    harness = await start(tmp_path, monkeypatch, gemma, prepare=prepare)
    try:
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == [prompts.THINKING_OFF, "A dog's is friendlier."]  # recovery: the two, in order
        assert efforts(gemma) == [PLAIN] and not harness.app.thinking.on(ME)
    finally:
        await stop(harness)


async def test_a_yes_queued_behind_her_answer_still_takes_the_found_video_when_the_off_is_answered_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The off ran the whole of `process`, so it was read as the account's next message to the match (anything but a yes
    ends it) ahead of the yes that came before it: the explicit YES was lost and the captions never read. A command
    answered ahead of the queue uses up no next-message rule of the messages still waiting; the yes is read in the queue's
    order, once her answer is out."""
    from tests.test_app_video_find import ASK, LINK, MATCH_1, found, helper_calls

    asking = pending()
    gemma = FakeGemma(
        tool("find_video", what="Ivan on Tech", newest=True),
        asking,  # her thinking request after the tool: on its way when the yes and the off come in
        text("Texted. Reply YES there."),  # asked again plain: the off stopped the thought
        tool("read_link", url=f"{LINK}aaaaaaaaaaa"),  # the yes turn
        text("Episode 212 is about beams [0:00]."),
    )
    harness = await found(tmp_path, monkeypatch, gemma, context_tokens=32_768)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, ASK, T0 + 1)
        await until(lambda: MATCH_1 in sent(folder) and len(gemma.calls) == 2)  # texted in the middle of her answer
        deliver(folder, "yes", T0 + 2)  # waits behind that answer
        deliver(folder, "/thinking off", T0 + 3)  # answered at once
        await until(lambda: "Episode 212 is about beams [0:00]." in sent(folder))
        assert helper_calls(tmp_path) == [["captions", "--until", "7200", "--", "aaaaaaaaaaa"]]  # read once
        assert prompts.THINKING_OFF_STOPPED in sent(folder) and app.video_offers.held() == 0
        assert not app.thinking.on(ME) and app.store.unanswered_incoming() == []
    finally:
        await stop(harness)


@pytest.mark.parametrize("who", [ME, PARTNER])
async def test_a_device_question_still_waits_for_the_yes_queued_before_an_off_answered_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, who: str
) -> None:
    """Devices keeps one question for the whole install and any message that is not a text cancelled it, so an off answered
    at once (of any account) took it away from a yes already waiting in the queue behind the tail of the answer that asked
    (its note still going out): the heater stayed on."""
    home = Home(tmp_path, monkeypatch)
    gemma = FakeGemma(
        tool("device_set", device="heater", power="off"), text("The heater needs your OK first: reply yes.")
    )
    harness = await start(
        tmp_path, monkeypatch, gemma, allowed_acis=BOTH, tapo_devices=DEVICE_SPECS, tapo_helper=home.helper
    )
    app, folder = harness.app, harness.folder
    asked = "Devices: reply yes within 2 minutes to switch heater off"
    at_the_tail, release = asyncio.Event(), asyncio.Event()
    real = app._deliver

    async def slow_note(aci: str, message: str, what: str) -> int | None:
        if message == asked:  # the answer's last note, held while the yes and the off come in
            at_the_tail.set()
            await release.wait()
        return await real(aci, message, what)

    monkeypatch.setattr(app, "_deliver", slow_note)
    try:
        deliver(folder, "switch the heater off", T0)
        await until(at_the_tail.is_set)
        deliver(folder, "yes", T0 + 1)  # ME's, queued behind the tail of the answer that asked
        deliver(folder, "/thinking off", T0 + 2, source=who)  # answered at once, ahead of it
        await until(lambda: (who, prompts.THINKING_OFF) in sent_to(folder))
        assert app.devices is not None and app.devices.pending  # the off took nothing away: the yes is read for it
        release.set()
        await until(lambda: (ME, "Devices: heater off") in sent_to(folder))
        assert home.sets() == [["set", "192.168.1.30", "--power", "off"]] and len(gemma.calls) == 2
    finally:
        release.set()
        await stop(harness)


async def test_the_line_for_the_answer_on_its_way_is_sent_once_and_never_follows_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "The answer on its way comes without it." is true only at the moment it is sent. Its send failed (three tries) and
    the resend pass sent it again a minute later, after the answer it spoke of had arrived. As the busy note is (review
    S19), it is sent once: a send that fails is set aside and never sent again."""
    thinking = pending()
    gemma = FakeGemma(thinking, text("A bat's life is strange."))
    harness = await start(tmp_path, monkeypatch, gemma)
    app, folder = harness.app, harness.folder
    real = app.signal.send
    refused: list[str] = []
    network = {"back": False}

    async def send(aci: str, message: str, *, styles: Sequence[str] = ()) -> int:
        if message == prompts.THINKING_OFF_STOPPED and not network["back"]:
            refused.append(message)
            raise SignalError("no network")
        return await real(aci, message, styles=styles)

    monkeypatch.setattr(app.signal, "send", send)
    try:
        deliver(folder, "/thinking on", T0)
        await until(lambda: len(sent(folder)) == 1)
        deliver(folder, QUESTION, T0 + 1)
        await until(lambda: len(gemma.calls) == 1)  # her thinking request is on its way
        deliver(folder, "/thinking off", T0 + 2)
        await until(lambda: len(refused) == 3)  # three tries, none went out
        await until(lambda: "A bat's life is strange." in sent(folder))  # the answer came, without the thought
        network["back"] = True
        await app.lifecycle.resend_failed()  # the pass that sends a failed reply again
        assert prompts.THINKING_OFF_STOPPED not in sent(folder) and app.store.unsent_outgoing() == []
        assert len(refused) == 3 and not app.thinking.on(ME)  # never tried again; the switch is off all the same
    finally:
        await stop(harness)


async def test_a_thinking_on_typed_during_a_call_is_answered_at_once_so_the_texts_waiting_for_its_end_think(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a call /thinking on keeps its place in the queue; during one it is answered at once like every command not
    in WAITING_COMMANDS (docs/agents/core.md says so), so a text that waits for the call's end is answered after it,
    thinking, though it was typed before the on."""
    gemma = FakeGemma(text("Strange, probably."))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        on_a_call(app)
        deliver(folder, QUESTION, T0)  # typed before the on: it waits for the call's end
        await until(lambda: sent(folder) == [prompts.CALL_TEXT_WAITS])
        deliver(folder, "/thinking on", T0 + 1)  # during the call: answered at once, not behind the text
        await until(lambda: len(sent(folder)) == 2)
        assert sent(folder)[1].startswith("Deep Thinking is on for your texts and voice notes until ")
        assert app.thinking.on(ME) and gemma.calls == []
        call_over(app)
        await until(lambda: "Strange, probably." in sent(folder))
        assert efforts(gemma) == [THINKS]  # the switch was on when the text's turn came, after the call
    finally:
        await stop(harness)

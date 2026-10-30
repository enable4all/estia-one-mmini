"""The lookup guard (owner, 22 Oct: "yes, do the fix"; live, 22 Oct: on a call she said she would look it up, called no
tool, and nothing ever came back): an answer that promises a lookup (claims.promises_a_lookup) in a turn that made no
tool call while web search was offered is asked once more in the same turn, with code's line in her newest message
(the turn's request up to that message unchanged, for the prompt cache). A retry that searches runs as any search (on a
call the checking line while it runs); one that still promises with no tool call gets code's true line after her
words, a note in a text (in her history as code's) and said in English on a call; a plain answer gets none (G6). Never a second retry; a lookup she made, any
tool call, is never touched."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.brain import LOOKUP_RETRY_WITHIN_S, Brain
from local_assistant.context import ContextBuilder
from local_assistant.fetcher import Page
from local_assistant.gemma import Completion
from local_assistant.profile import Profile
from local_assistant.store import Message, Store
from local_assistant.tools import Tools
from local_assistant.web_search import WebResult
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text, tool
from tests.fakes.fake_web import FakeWeb
from tests.test_app import call_settings, code_notes_in, deliver, her_words, sent, start, stop, until

LYON = WebResult("Weather Lyon", "https://weather.example/lyon", "weather.example", "", "Lyon tomorrow: 16°C, sunny.")
ASKED = "what's the weather in Lyon tomorrow?"


async def no_fetch(url: str) -> Page:
    raise AssertionError("no fetch expected")


def make(
    store: Store,
    gemma: FakeGemma,
    *,
    web: bool = True,
    max_tool_calls: int = 5,
    profile: Profile | None = None,
    calls: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[Brain, FakeWeb]:
    profile = profile or Profile(store)
    found = FakeWeb({"lyon": [LYON]})
    tools = Tools(store, profile, TZ, no_fetch, web=found if web else None, calls=calls)
    brain = Brain(gemma, ContextBuilder(store, profile, TZ), tools, max_tool_calls=max_tool_calls, clock=clock)
    return brain, found


def incoming(store: Store, words: str = ASKED) -> Message:
    conversation = store.current_conversation()
    message_id = store.add_incoming(conversation.id, ME, T0, "text", words)
    assert message_id is not None
    return store.message(message_id)


def on_call(store: Store, words: str = ASKED) -> Message:
    conversation = store.current_conversation()
    return store.message(store.add_call_turn(conversation.id, ME, f"{prompts.CALL_MARK} {words}"))


async def test_a_promised_lookup_is_asked_once_more_and_the_search_runs(store: Store) -> None:
    gemma = FakeGemma(
        text("Let me check the forecast for Lyon."),
        tool("web_search", query="Lyon weather tomorrow"),
        text("Lyon tomorrow: 16°C and sunny."),
    )
    brain, web = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store))
    assert (reply.text, reply.tool_calls, reply.lookup) == ("Lyon tomorrow: 16°C and sunny.", 1, "")
    assert web.queries == ["Lyon weather tomorrow"] and len(gemma.calls) == 3
    assert reply.sources.startswith("🔎 **Sources**")  # as any search


async def test_the_retry_is_the_turns_own_request_up_to_her_newest_message(store: Store) -> None:
    """Calls.md's prompt-cache rule: the system prompt, the tools, the options and every earlier message are the
    turn's own, byte for byte; only her newest message carries code's line, right before its words."""
    conversation = store.current_conversation()
    earlier = store.add_incoming(conversation.id, ME, T0 - 60_000, "text", "hi")
    assert earlier is not None
    store.mark_sent(store.add_outgoing(conversation.id, ME, "text", "Hi! How can I help?", earlier), T0 - 50_000)
    store.set_status(earlier, "answered")
    gemma = FakeGemma(text("I'll look that up for you."), text("Lyon is usually mild in October."))
    brain, _ = make(store, gemma)
    await brain.answer(store.current_conversation(), incoming(store))
    first, retry = gemma.calls
    assert retry["messages"][:-1] == first["messages"][:-1]
    assert (retry["tools"], retry["max_tokens"]) == (first["tools"], first["max_tokens"])
    newest = first["messages"][-1]
    assert newest["role"] == "user" and newest["content"].endswith(ASKED)
    assert retry["messages"][-1] == {"role": "user", "content": f"{prompts.LOOKUP_RETRY}\n\n{newest['content']}"}


async def test_codes_line_comes_after_her_notes_and_before_the_words(store: Store) -> None:
    profile = Profile(store, max_chars=1_000, notes_max_chars=20_000, notes_per_message=1_500)
    profile.add("Travel: The user flies to Lyon on Friday.", source_kind="chat")
    gemma = FakeGemma(text("One moment."), text("I can't say for sure without a search."))
    brain, _ = make(store, gemma, profile=profile)
    await brain.answer(store.current_conversation(), incoming(store))
    notes, words = gemma.calls[0]["messages"][-1]["content"].split("\n\n", 1)
    assert notes.startswith(prompts.NOTES_HEADER) and words.endswith(ASKED)
    assert gemma.calls[1]["messages"][-1]["content"] == f"{notes}\n\n{prompts.LOOKUP_RETRY}\n\n{words}"


async def test_a_retry_without_a_tool_call_gets_codes_true_line_after_her_words(store: Store) -> None:
    gemma = FakeGemma(text("Let me check."), text("Let me check."))
    brain, _ = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store))
    assert len(gemma.calls) == 2  # never a second retry
    assert reply.text == "Let me check." and reply.hers == "Let me check."  # append only: her words stay
    assert reply.lookup == prompts.LOOKUP_NOT_DONE == "I didn't look that up. Ask me again and I'll search."


@pytest.mark.parametrize(
    ("asked", "answer"),
    [(ASKED, "Lyon is usually mild in October, about 15 degrees."), ("capital of Australia?", "Canberra.")],
)
async def test_a_plain_retry_answer_without_a_search_gets_no_line(store: Store, asked: str, answer: str) -> None:
    """Review G6: the retry's line allows an answer without a search ("or answer without it"); one that promises no
    lookup leaves nothing undone, so code's line no longer follows it."""
    gemma = FakeGemma(text("I'll look it up."), text(answer))
    brain, _ = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, asked))
    assert (reply.text, reply.lookup) == (answer, "")


@pytest.mark.parametrize(
    "first",
    [
        tool("web_search", query="Lyon weather"),
        tool("remember", fact="About: The user flies to Lyon."),
    ],
    ids=["a-search", "another-tool"],
)
async def test_a_turn_that_made_any_tool_call_is_never_touched(store: Store, first: Completion) -> None:
    gemma = FakeGemma(first, text("Let me check one more thing."))
    brain, _ = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store))
    assert len(gemma.calls) == 2 and reply.lookup == "" and reply.text == "Let me check one more thing."


async def test_codes_own_reads_count_as_tool_calls(store: Store) -> None:
    gemma = FakeGemma(text("Let me check."))
    brain, _ = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store), reads=(("mini_status", "{}"),))
    assert len(gemma.calls) == 1 and reply.lookup == ""


@pytest.mark.parametrize("web, limit", [(False, 5), (True, 0)], ids=["no-web-search", "no-tools-offered"])
async def test_without_web_search_offered_there_is_no_retry(store: Store, web: bool, limit: int) -> None:
    gemma = FakeGemma(text("Let me check."))
    brain, _ = make(store, gemma, web=web, max_tool_calls=limit)
    reply = await brain.answer(store.current_conversation(), incoming(store))
    assert len(gemma.calls) == 1 and reply.lookup == "" and reply.text == "Let me check."


@pytest.mark.parametrize(
    "answer",
    ["Shall I look it up?", "Lyon is usually mild in October.", "Let me know if you want more."],
)
async def test_an_answer_that_promises_no_lookup_is_not_asked_again(store: Store, answer: str) -> None:
    gemma = FakeGemma(text(answer))
    brain, _ = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store))
    assert len(gemma.calls) == 1 and reply.lookup == "" and reply.text == answer


async def test_on_a_call_the_checking_line_comes_when_the_retrys_search_runs(store: Store) -> None:
    gemma = FakeGemma(
        text("One moment, let me look."),
        tool("web_search", query="Lyon weather tomorrow"),
        text("Sixteen degrees and sunny."),
    )
    brain, _ = make(store, gemma)
    checked: list[int] = []
    retried: list[int] = []
    reply = await brain.answer(
        store.current_conversation(),
        on_call(store),
        max_tokens=300,
        on_tools=lambda: checked.append(len(gemma.calls)),
        on_retry=lambda: retried.append(1),
    )
    assert reply.text == "Sixteen degrees and sunny." and reply.lookup == ""
    assert checked == [2]  # when the retry's search runs, never for her promise
    assert retried == []  # the empty answer's acknowledgement is not this retry's
    assert [call["max_tokens"] for call in gemma.calls] == [300, 300, 300]


async def test_on_a_call_a_retry_that_still_promises_gets_the_line_to_say(store: Store) -> None:
    gemma = FakeGemma(text("I'll check."), text("Let me look that up."))
    brain, _ = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), on_call(store), max_tokens=300)
    assert (reply.text, reply.lookup) == ("Let me look that up.", prompts.LOOKUP_NOT_DONE)
    plain = FakeGemma(text("I'll check."), text("I can't say for sure."))
    brain, _ = make(store, plain)
    reply = await brain.answer(store.current_conversation(), on_call(store), max_tokens=300)
    assert (reply.text, reply.lookup) == ("I can't say for sure.", "")  # review G6


async def test_a_written_end_call_beside_her_promise_is_dropped_with_it(store: Store) -> None:
    """Review I1: her first answer is set aside for the retry, so its written end_call goes with it; only the retry's
    answer may end the call. After its search (outside text) only on the caller's own leave-taking (second review of
    0.9.8, redteam-07)."""
    gemma = FakeGemma(text("Let me check that. end_call"), tool("web_search", query="Lyon"), text("Sunny, 16 degrees."))
    brain, _ = make(store, gemma, calls=True)
    reply = await brain.answer(store.current_conversation(), on_call(store), max_tokens=300)
    assert (reply.text, reply.end_call) == ("Sunny, 16 degrees.", False)
    for asked, ends in ((ASKED, False), (f"{ASKED} Thanks, I have to go", True)):
        gemma = FakeGemma(
            text("Let me check that. end_call"),
            tool("web_search", query="Lyon"),
            text("Sunny, 16 degrees. Bye! end_call"),
        )
        brain, _ = make(store, gemma, calls=True)
        reply = await brain.answer(store.current_conversation(), on_call(store, asked), max_tokens=300)
        assert (reply.end_call, reply.lookup) == (ends, "")  # the retry's own end_call (no search: the next test)


async def test_codes_lookup_line_never_ends_the_call(store: Store) -> None:
    """Review of 0.9.7 (C12): a promise past the call's window kept her written end_call, so she said "Ask me again and
    I'll search." and hung up. Whenever code's line follows her words, her written end_call goes: the caller may ask
    again (a retry's too, when it still promises; a plain retry answer gets no line, review G6, so its end_call
    stands)."""
    gemma = FakeGemma(text("Let me check. end_call"))
    brain, _ = make(store, gemma, calls=True, clock=Ticks(LOOKUP_RETRY_WITHIN_S))
    reply = await brain.answer(store.current_conversation(), on_call(store), max_tokens=300)
    assert (reply.text, reply.lookup, reply.end_call) == ("Let me check.", prompts.LOOKUP_NOT_DONE, False)
    gemma = FakeGemma(text("Let me check that. end_call"), text("I'll look that up. Bye! end_call"))
    brain, _ = make(store, gemma, calls=True)
    reply = await brain.answer(store.current_conversation(), on_call(store), max_tokens=300)
    assert (reply.lookup, reply.end_call) == (prompts.LOOKUP_NOT_DONE, False)
    gemma = FakeGemma(text("Let me check that. end_call"), text("I can't say. Bye! end_call"))
    brain, _ = make(store, gemma, calls=True)
    reply = await brain.answer(store.current_conversation(), on_call(store), max_tokens=300)
    assert (reply.lookup, reply.end_call) == ("", True)


async def test_on_a_call_the_window_counts_from_the_turns_start(store: Store) -> None:
    """Review of 0.9.7 (C12): the 15 s counted from the answer's start, so the turn's waits before it (a text being
    answered, the calendar's glance) were left out. The turn's start (`started`, on the brain's clock) counts."""
    now = [1000.0]
    gemma = FakeGemma(text("Let me check."), text("unused"))
    brain, _ = make(store, gemma, clock=lambda: now[0])
    reply = await brain.answer(
        store.current_conversation(), on_call(store), max_tokens=300, started=now[0] - LOOKUP_RETRY_WITHIN_S
    )
    assert len(gemma.calls) == 1 and reply.lookup == prompts.LOOKUP_NOT_DONE  # too late for a retry
    gemma = FakeGemma(text("Let me check."), tool("web_search", query="Lyon"), text("Sunny."))
    brain, _ = make(store, gemma, clock=lambda: now[0])
    reply = await brain.answer(store.current_conversation(), on_call(store), max_tokens=300, started=now[0] - 1.0)
    assert (reply.text, reply.lookup, len(gemma.calls)) == ("Sunny.", "", 3)  # within it: the retry and its search


@pytest.mark.parametrize(
    ("limit", "spent", "retried"),
    [
        (20.0, 10.0, False),  # [calls] turn_timeout_seconds = 20: past a third of it, a retry would not fit
        (60.0, 16.0, True),  # Gemma 4 12B's 60 s: a retry at 16 s still fits
        (45.0, 14.0, True),  # Gemma 4 26B's 45 s: 15 s, as before
        (45.0, 15.0, False),
        (None, 14.0, True),  # no limit given: LOOKUP_RETRY_WITHIN_S
        (None, 15.0, False),
    ],
)
async def test_on_a_call_the_lookup_window_is_a_third_of_the_turns_time_limit(
    store: Store, limit: float | None, spent: float, retried: bool
) -> None:
    """The second deep scan (calls-lookup-window-fixed): the window was 15 s whatever the call's limit, so with
    `turn_timeout_seconds = 20` a retry at 14 s ran past the limit into "I lost my train of thought", and on the 12B
    tier's 60 s a retry that would fit was skipped. It is a third of the turn's limit (15 s of the 26B's 45 s)."""
    now = [1000.0]
    gemma = FakeGemma(text("Let me check."), tool("web_search", query="Lyon"), text("Sunny."))
    brain, _ = make(store, gemma, clock=lambda: now[0])
    reply = await brain.answer(
        store.current_conversation(), on_call(store), max_tokens=300, started=now[0] - spent, turn_limit=limit
    )
    if retried:
        assert (reply.text, reply.lookup, len(gemma.calls)) == ("Sunny.", "", 3)
    else:
        assert len(gemma.calls) == 1 and reply.lookup == prompts.LOOKUP_NOT_DONE


class Ticks:
    """A clock that moves on by `step` seconds each time it is read."""

    def __init__(self, step: float) -> None:
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


async def test_on_a_call_a_slow_first_answer_gets_codes_line_and_no_retry(store: Store) -> None:
    """Review M1: under load her first request alone took 16.6 s (turn 994); a retry, a search and her answer after it
    could pass the call's 45 s and end with "I lost my train of thought". After LOOKUP_RETRY_WITHIN_S on a call there
    is no retry: her words, then code's true line at once."""
    gemma = FakeGemma(text("Let me check."), text("unused"))
    brain, _ = make(store, gemma, clock=Ticks(LOOKUP_RETRY_WITHIN_S))
    reply = await brain.answer(store.current_conversation(), on_call(store), max_tokens=300)
    assert len(gemma.calls) == 1 and (reply.text, reply.lookup) == ("Let me check.", prompts.LOOKUP_NOT_DONE)


async def test_a_text_has_no_time_limit_for_the_retry_and_a_quick_call_turn_retries(store: Store) -> None:
    gemma = FakeGemma(text("Let me check."), tool("web_search", query="Lyon"), text("Sunny."))
    brain, _ = make(store, gemma, clock=Ticks(LOOKUP_RETRY_WITHIN_S))
    reply = await brain.answer(store.current_conversation(), incoming(store))
    assert (reply.text, reply.lookup, len(gemma.calls)) == ("Sunny.", "", 3)
    gemma = FakeGemma(text("Let me check."), tool("web_search", query="Lyon"), text("Sunny."))
    brain, _ = make(store, gemma, clock=Ticks(0.5))
    reply = await brain.answer(store.current_conversation(), on_call(store), max_tokens=300)
    assert (reply.text, reply.lookup, len(gemma.calls)) == ("Sunny.", "", 3)


async def test_the_guards_log_lines_hold_metadata_only(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="local_assistant.brain")
    gemma = FakeGemma(text("Let me check Lyon."), text("Let me check Lyon."))
    brain, _ = make(store, gemma)
    message = incoming(store)
    await brain.answer(store.current_conversation(), message)
    lines = [record.getMessage() for record in caplog.records if record.name == "local_assistant.brain"]
    assert any(f"message {message.id}: a lookup promised" in line for line in lines), lines
    assert any(f"message {message.id}: no tool call after the lookup retry" in line for line in lines), lines
    assert not any("Lyon" in line or "check" in line.casefold() for line in lines)


# the running bridge: a text, and a call turn


async def test_in_a_text_codes_line_follows_her_words_as_a_note_in_her_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Let me look that up."), text("Let me look that up."), text("Okay."))
    harness = await start(tmp_path, monkeypatch, gemma, web_enabled=True, web_keys_path=tmp_path / "web.env")
    try:
        harness.app.tools._web = FakeWeb({"lyon": [LYON]})
        now = int(time.time() * 1000)  # never the suite's start: a message older than 10 minutes counts as missed
        deliver(harness.folder, ASKED, now)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == ["Let me look that up.", prompts.LOOKUP_NOT_DONE]
        deliver(harness.folder, "search it then", now + 1)
        await until(lambda: len(sent(harness.folder)) == 3)
        later = gemma.calls[-1]
        assert any(note.endswith(prompts.LOOKUP_NOT_DONE) for note in code_notes_in(later))
        assert prompts.LOOKUP_NOT_DONE not in her_words(later)  # code's words, never hers
        assert prompts.LOOKUP_RETRY not in str(later["messages"])  # code's line for the retry is never stored
    finally:
        await stop(harness)


async def test_on_a_call_codes_line_is_said_in_english_after_her_words_and_texted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Let me check."), text("I'll look it up."))
    settings = call_settings(tmp_path) | {"web_enabled": True, "web_keys_path": tmp_path / "web.env"}
    harness = await start(tmp_path, monkeypatch, gemma, **settings)
    try:
        app = harness.app
        app.tools._web = FakeWeb({"lyon": [LYON]})
        checked: list[str] = []
        turn = await app.call_turn(ME, ASKED, lambda: checked.append("x"))
        assert (turn.text, turn.code_text) == ("I'll look it up.", prompts.LOOKUP_NOT_DONE)
        assert checked == [] and turn.reply_id is not None
        assert app.store.message(turn.reply_id).text == "I'll look it up."  # her words; the line is code's note
        await until(lambda: prompts.LOOKUP_NOT_DONE in sent(harness.folder))
    finally:
        await stop(harness)


async def test_a_call_turns_waits_before_the_answer_count_for_the_lookup_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Let me check."), text("unused"))
    settings = call_settings(tmp_path) | {"web_enabled": True, "web_keys_path": tmp_path / "web.env"}
    harness = await start(tmp_path, monkeypatch, gemma, **settings)
    try:
        app = harness.app
        app.tools._web = FakeWeb({"lyon": [LYON]})
        now = [1000.0]
        monkeypatch.setattr(app.brain, "_clock", lambda: now[0])
        app._worker_idle.clear()  # a text being answered when the turn comes
        turn_task = asyncio.create_task(app.call_turn(ME, ASKED, lambda: None))
        await asyncio.sleep(0.05)
        now[0] += LOOKUP_RETRY_WITHIN_S  # it took that long
        app._worker_idle.set()
        turn = await turn_task
        assert (turn.text, turn.code_text) == ("Let me check.", prompts.LOOKUP_NOT_DONE)
        assert len(gemma.calls) == 1  # no retry: the turn's time is spent
    finally:
        await stop(harness)


async def test_a_call_turn_gives_the_lookup_window_the_calls_own_time_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second deep scan (calls-lookup-window-fixed): with `[calls] turn_timeout_seconds = 20`, a promise 10 s into
    the turn gets code's line at once (a third of 20 s), where the fixed 15 s window asked again."""
    gemma = FakeGemma(text("Let me check."), text("unused"))
    settings = call_settings(tmp_path) | {
        "web_enabled": True,
        "web_keys_path": tmp_path / "web.env",
        "calls_turn_timeout_seconds": 20.0,
    }
    harness = await start(tmp_path, monkeypatch, gemma, **settings)
    try:
        app = harness.app
        app.tools._web = FakeWeb({"lyon": [LYON]})
        now = [1000.0]
        monkeypatch.setattr(app.brain, "_clock", lambda: now[0])
        app._worker_idle.clear()  # a text being answered when the turn comes
        turn_task = asyncio.create_task(app.call_turn(ME, ASKED, lambda: None))
        await asyncio.sleep(0.05)
        now[0] += 10.0
        app._worker_idle.set()
        turn = await turn_task
        assert (turn.text, turn.code_text) == ("Let me check.", prompts.LOOKUP_NOT_DONE)
        assert len(gemma.calls) == 1
    finally:
        await stop(harness)


async def test_on_a_call_the_retrys_search_says_the_checking_line_and_her_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(
        text("One moment, let me check."),
        tool("web_search", query="Lyon weather tomorrow"),
        text("Sixteen degrees and sunny."),
    )
    settings = call_settings(tmp_path) | {"web_enabled": True, "web_keys_path": tmp_path / "web.env"}
    harness = await start(tmp_path, monkeypatch, gemma, **settings)
    try:
        app = harness.app
        app.tools._web = FakeWeb({"lyon": [LYON]})
        checked: list[str] = []
        turn = await app.call_turn(ME, ASKED, lambda: checked.append("x"))
        assert (turn.text, turn.code_text) == ("Sixteen degrees and sunny.", "")
        assert checked == ["x"]  # the call says its checking line while the search runs
    finally:
        await stop(harness)


PHOTO = "data:image/jpeg;base64,/9j/AAAA"


@pytest.mark.parametrize(
    "answer",
    [
        "Let me check: the capital of Australia is Canberra.",
        "Let me take a look. Lyon is usually mild in October, about 15 degrees.",
    ],
)
async def test_a_promise_with_the_answer_right_after_it_is_not_asked_again(store: Store, answer: str) -> None:
    """Review of 0.9.8, guards-3: "Let me take a look: …" with the answer after it cost a second request."""
    gemma = FakeGemma(text(answer))
    brain, _ = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store))
    assert len(gemma.calls) == 1 and (reply.text, reply.lookup) == (answer, "")


async def test_a_retry_that_keeps_the_promise_and_answers_gets_no_line(store: Store) -> None:
    """Code's line only when the kept answer is the promise alone: both tries kept the phrase, and a correct answer got
    "I didn't look that up" after it."""
    answer = "Let me check: Lyon is usually mild in October, about 15 degrees."
    gemma = FakeGemma(text("Let me check."), text(answer))
    brain, _ = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store))
    assert len(gemma.calls) == 2 and (reply.text, reply.lookup) == (answer, "")


async def test_with_a_photo_in_the_request_a_promise_alone_gets_codes_line_and_no_retry(store: Store) -> None:
    """A look at a photo is no web lookup, and a retry reads the photo again: with a photo of this message, or one still
    shown from an earlier message, no retry; a promise with nothing after it still gets code's line (final review of
    0.9.8, guards-3-lookup-guard-weakened: it went out alone), and one with the answer after it none. Code's line there
    promises no search (second deep scan, dialogue-photo-promise-line-says-search: asking again read the photo again,
    with no search)."""
    gemma = FakeGemma(text("Let me take a look."))
    brain, _ = make(store, gemma)
    photo = incoming(store, "[photo] what plant is this?")
    reply = await brain.answer(store.current_conversation(), photo, images=[PHOTO])
    assert len(gemma.calls) == 1 and (reply.text, reply.lookup) == (
        "Let me take a look.",
        prompts.LOOKUP_PHOTO_NOT_DONE,
    )
    assert prompts.LOOKUP_PHOTO_NOT_DONE == "I didn't answer that yet. Ask me again."
    gemma = FakeGemma(text("Let me check. Stay tuned, Alex!"))
    brain, _ = make(store, gemma)
    later = store.add_incoming(store.current_conversation().id, ME, T0 + 60_000, "text", "does it need much water")
    assert later is not None
    reply = await brain.answer(store.current_conversation(), store.message(later), recent={photo.id: [PHOTO]})
    assert len(gemma.calls) == 1 and reply.lookup == prompts.LOOKUP_PHOTO_NOT_DONE
    answer = "Let me take a look: it's a monstera, it likes bright, indirect light."
    gemma = FakeGemma(text(answer))
    brain, _ = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), photo, images=[PHOTO])
    assert len(gemma.calls) == 1 and (reply.text, reply.lookup) == (answer, "")


async def test_a_wait_after_the_promise_is_asked_once_more(store: Store) -> None:
    """Final review of 0.9.8, guards-3-lookup-guard-weakened: "It might take a moment." after the promise counted as an
    answer, and the promise went out alone."""
    gemma = FakeGemma(
        text("Let me check the forecast for Lyon. It might take a moment."),
        tool("web_search", query="Lyon weather tomorrow"),
        text("Lyon tomorrow: 16°C and sunny."),
    )
    brain, web = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store))
    assert (reply.text, reply.lookup, web.queries) == ("Lyon tomorrow: 16°C and sunny.", "", ["Lyon weather tomorrow"])

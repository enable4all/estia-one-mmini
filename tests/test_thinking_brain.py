"""Deep Thinking in the brain (owner, 4 Nov 2026): a thinking answer's request (effort, cap, the room kept for it in her
context), the cut-off fallback, its log line, and the stop a call that starts asks for (never once a tool has run)."""

import asyncio
import dataclasses
import json
import sys
from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.babble import PracticeTurn
from local_assistant.brain import DOCUMENT_REPLY_TOKENS, Brain, Interrupted
from local_assistant.context import CONTEXT_MARGIN_TOKENS, ContextBuilder
from local_assistant.fetcher import Page
from local_assistant.gemma import Completion, GemmaUnavailableError
from local_assistant.profile import Profile
from local_assistant.store import OUTSIDE_SEEN, Conversation, Message, Store
from local_assistant.thinking import BUDGET_TOKENS, EFFORT, FLOOR_TOKENS
from local_assistant.tools import Tools
from local_assistant.web_search import WebResult
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text, tool
from tests.fakes.fake_maps import FakeMaps
from tests.fakes.fake_web import FakeWeb
from tests.test_brain import incoming, make, no_fetch
from tests.test_tools import UNO

QUESTION = "what is the meaning of life, really?"


def cut_by_its_thought(**more: int) -> Completion:
    """A thinking request that used its whole cap on the thought: no words, no tool call, cut at the limit."""
    return Completion(
        content="",
        prompt_tokens=100,
        finish_reason="length",
        completion_tokens=more.get("tokens", BUDGET_TOKENS + 600),
        reasoning_chars=more.get("chars", 21_000),
    )


# the request: effort and cap


async def test_a_thinking_turn_asks_for_high_effort_and_the_usual_cap_plus_the_thought(store: Store) -> None:
    assert EFFORT == "high" and BUDGET_TOKENS == 8_000  # the one number: thinking.py says why
    gemma = FakeGemma(text("Forty-two."))
    _, _, brain = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, QUESTION), thinking=True)
    assert reply.text == "Forty-two."
    [call] = gemma.calls
    assert (call["reasoning_effort"], call["max_tokens"]) == ("high", 600 + BUDGET_TOKENS)


async def test_a_turn_that_does_not_think_asks_as_it_always_did(store: Store) -> None:
    gemma = FakeGemma(text("Hi."))
    _, _, brain = make(store, gemma)
    await brain.answer(store.current_conversation(), incoming(store, QUESTION))
    [call] = gemma.calls
    assert (call["reasoning_effort"], call["max_tokens"]) == ("none", 600)


async def test_a_call_never_thinks_whatever_it_is_asked(store: Store) -> None:
    gemma = FakeGemma(text("Sure."))
    _, _, brain = make(store, gemma)
    conversation = store.current_conversation()
    said = store.add_incoming(conversation.id, ME, T0, "text", "[call] what is the meaning of life?")
    assert said is not None
    await brain.answer(conversation, store.message(said), max_tokens=220, thinking=True)
    [call] = gemma.calls
    assert (call["reasoning_effort"], call["max_tokens"]) == ("none", 220)


async def test_practice_never_thinks_whatever_it_is_asked(store: Store) -> None:
    gemma = FakeGemma(text("Hoe gaat het met je?"))
    _, _, brain = make(store, gemma)
    practice = PracticeTurn(rules="PRACTICE nl A2", fallback="Hoe gaat het?", spoken=False, language="nl")
    await brain.answer(store.current_conversation(), incoming(store, "goedemorgen"), practice=practice, thinking=True)
    [call] = gemma.calls
    assert (call["reasoning_effort"], call["max_tokens"], call["tools"]) == ("none", 600, None)


async def test_a_calls_warm_up_never_thinks(store: Store) -> None:
    gemma = FakeGemma(text(""))
    _, _, brain = make(store, gemma)
    await brain.warm_up(store.current_conversation())
    assert gemma.calls[0]["reasoning_effort"] == "none"


async def test_every_request_of_a_thinking_answer_thinks_also_after_a_tool_result(store: Store) -> None:
    gemma = FakeGemma(tool("remember", fact="Family: The user's sister is Anna."), text("Noted: your sister is Anna."))
    profile, _, brain = make(store, gemma)
    await brain.answer(store.current_conversation(), incoming(store, "remember my sister is Anna"), thinking=True)
    assert [(c["reasoning_effort"], c["max_tokens"]) for c in gemma.calls] == [("high", 600 + BUDGET_TOKENS)] * 2
    assert len(profile.facts()) == 1


async def test_an_answer_that_read_a_document_thinks_with_its_bigger_cap(store: Store, tmp_path: Path) -> None:
    from local_assistant.documents import Documents, Helper
    from tests.test_documents import FAKE, pdf

    folder = tmp_path / "Estia-KB"
    pdf(folder / "exam.pdf", "Task B5. Part one.", "Part two.")
    documents = Documents(store, folder, Helper((sys.executable, str(FAKE), str(tmp_path / "h.json")), 10.0), tz=TZ)
    profile = Profile(store)
    tools = Tools(store, profile, TZ, no_fetch, documents=documents)
    gemma = FakeGemma(tool("read_document", document="exam", page=1, last_page=2), text("Task B5. Part one. Part two."))
    brain = Brain(gemma, ContextBuilder(store, profile, TZ), tools)
    await brain.answer(store.current_conversation(), incoming(store, "Give me B5 from the exam"), thinking=True)
    assert [call["max_tokens"] for call in gemma.calls] == [600 + BUDGET_TOKENS, DOCUMENT_REPLY_TOKENS + BUDGET_TOKENS]
    assert {call["reasoning_effort"] for call in gemma.calls} == {"high"}


# the thought ate the cap: asked again without it


async def test_a_thought_that_ate_the_cap_is_asked_again_without_thinking_at_the_usual_cap(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    gemma = FakeGemma(cut_by_its_thought(), text("It depends what you mean by life."))
    _, _, brain = make(store, gemma)
    caplog.set_level("INFO", logger="local_assistant.brain")
    reply = await brain.answer(store.current_conversation(), incoming(store, QUESTION), thinking=True)
    assert reply.text == "It depends what you mean by life." and not reply.cut
    first, second = gemma.calls
    assert (first["reasoning_effort"], first["max_tokens"]) == ("high", 600 + BUDGET_TOKENS)
    assert (second["reasoning_effort"], second["max_tokens"]) == ("none", 600)
    assert second["messages"] == first["messages"]  # the same request: no nudge, as the answer is not "empty"
    assert reply.timing.requests == 2
    [line] = [r.getMessage() for r in caplog.records if "no answer" in r.getMessage()]
    assert "deep thinking" in line and "asking once more without" in line
    assert "reasoning 21000 chars" in line and "finish length" in line  # metadata only: never a word of either side
    assert QUESTION not in caplog.text and "depends" not in caplog.text


async def test_an_answer_cut_off_at_its_limit_with_words_in_it_is_asked_again_without_thinking(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Correctness-4: the cap counts the thought and the answer together, so a thought near 8,000 tokens leaves the answer
    600 or fewer and it was sent cut off mid-sentence (nothing reads `Reply.cut` for a text): one more request without
    thinking, at the usual cap, whether or not the cut-off one had words."""
    cut = Completion(
        "The meaning of life is",
        prompt_tokens=100,
        finish_reason="length",
        completion_tokens=BUDGET_TOKENS + 600,
        reasoning_chars=26_000,
    )
    gemma = FakeGemma(cut, text("It depends what you mean by life."))
    _, _, brain = make(store, gemma)
    caplog.set_level("INFO", logger="local_assistant.brain")
    reply = await brain.answer(store.current_conversation(), incoming(store, QUESTION), thinking=True)
    assert reply.text == "It depends what you mean by life." and not reply.cut
    assert [(c["reasoning_effort"], c["max_tokens"]) for c in gemma.calls] == [
        ("high", 600 + BUDGET_TOKENS),
        ("none", 600),
    ]
    assert gemma.calls[1]["messages"] == gemma.calls[0]["messages"]  # the same request: no nudge, it is not empty
    [line] = [r.getMessage() for r in caplog.records if "asking once more without" in r.getMessage()]
    assert "deep thinking" in line and "cut off" in line and "finish length" in line and "reasoning 26000 chars" in line
    assert "meaning of life is" not in caplog.text and QUESTION not in caplog.text  # numbers only


async def test_a_plain_answer_cut_off_with_words_in_it_is_kept_as_it_is(store: Store) -> None:
    """Only a thinking request is asked again: a plain one cut at its cap keeps its words (a call says them up to the last
    whole sentence)."""
    gemma = FakeGemma(Completion("The meaning of life is", prompt_tokens=100, finish_reason="length"))
    _, _, brain = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, QUESTION))
    assert reply.text == "The meaning of life is" and reply.cut and len(gemma.calls) == 1


async def test_a_thinking_request_that_ends_in_a_tool_call_is_not_asked_again_whatever_its_finish(store: Store) -> None:
    cut_call = dataclasses.replace(tool("remember", fact="Family: The user's sister is Anna."), finish_reason="length")
    gemma = FakeGemma(cut_call, text("Noted: your sister is Anna."))
    profile, _, brain = make(store, gemma)
    await brain.answer(store.current_conversation(), incoming(store, "remember my sister is Anna"), thinking=True)
    assert [(c["reasoning_effort"], c["max_tokens"]) for c in gemma.calls] == [("high", 600 + BUDGET_TOKENS)] * 2
    assert len(profile.facts()) == 1  # the tool ran once, as it called for


async def test_an_empty_thinking_answer_that_was_not_cut_is_nudged_once_without_thinking(store: Store) -> None:
    empty = Completion("", prompt_tokens=100, finish_reason="stop", completion_tokens=900, reasoning_chars=3_000)
    gemma = FakeGemma(empty, text("Here."))
    _, _, brain = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, QUESTION), thinking=True)
    assert reply.text == "Here."
    first, second = gemma.calls
    assert (first["reasoning_effort"], first["max_tokens"]) == ("high", 600 + BUDGET_TOKENS)
    assert (second["reasoning_effort"], second["max_tokens"]) == ("none", 600)
    assert second["messages"][-1] == {"role": "user", "content": prompts.EMPTY_NUDGE}


async def test_a_fallback_that_is_empty_too_ends_in_the_confused_line_after_three_requests_at_most(
    store: Store,
) -> None:
    empty = Completion("", prompt_tokens=100, finish_reason="stop")
    gemma = FakeGemma(cut_by_its_thought(), empty, empty)
    _, _, brain = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, QUESTION), thinking=True)
    assert reply.text == prompts.CONFUSED and len(gemma.calls) == 3
    assert [c["reasoning_effort"] for c in gemma.calls] == ["high", "none", "none"]


# the log: numbers only


async def test_a_thinking_answer_logs_its_numbers_and_never_its_thought(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    answer = dataclasses.replace(
        text("Forty-two."), completion_tokens=1_200, reasoning_chars=4_000, total_ms=45_000.0, finish_reason="stop"
    )
    gemma = FakeGemma(answer)
    _, _, brain = make(store, gemma)
    caplog.set_level("INFO", logger="local_assistant.brain")
    message = incoming(store, QUESTION)
    await brain.answer(store.current_conversation(), message, thinking=True)
    [line] = [r.getMessage() for r in caplog.records if "deep thinking" in r.getMessage()]
    assert line == f"message {message.id}: deep thinking: 4,000 characters of thought (not kept), 1,200 tokens, 45.0 s"
    assert "Forty-two" not in caplog.text and QUESTION not in caplog.text


async def test_an_answer_that_does_not_think_logs_no_such_line(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    _, _, brain = make(store, FakeGemma(text("Hi.")))
    caplog.set_level("INFO", logger="local_assistant.brain")
    await brain.answer(store.current_conversation(), incoming(store, QUESTION))
    assert "deep thinking" not in caplog.text


# the room kept for the thought in her context: its own reserve, for its own answer only


def a_chat(store: Store, turns: int, *, answer: int = 1_400) -> None:
    conversation = store.current_conversation()
    for turn in range(turns):
        asked = store.add_incoming(conversation.id, ME, T0 + turn * 2_000, "text", f"{turn} " + "q" * 200)
        assert asked is not None
        out = store.add_outgoing(conversation.id, ME, "text", f"{turn} " + "a" * answer, asked)
        store.mark_sent(out, T0 + turn * 2_000 + 1_000)


TOOLS = [{"type": "function", "function": {"name": "t", "description": "d" * 15_000}}]  # every extra: ~15,000 chars


def sized(store: Store, window: int) -> ContextBuilder:
    context = ContextBuilder(store, Profile(store), TZ, rules="A rule of hers.\n" * 900, context_tokens=window)
    context.reserve(TOOLS, reply_tokens=600)
    return context


def asked_tokens(context: ContextBuilder, store: Store, extra: int, conversation: Conversation | None = None) -> int:
    """A request as the code counts it against the context: her messages, the tools, the answer asked for and the margin
    (of the conversation given, else the open one)."""
    conversation = conversation or store.current_conversation()
    built = sum(context.estimate(json.dumps(m, ensure_ascii=False)) for m in context.build(conversation))
    return built + context.estimate(json.dumps(TOOLS, ensure_ascii=False)) + 600 + extra + CONTEXT_MARGIN_TOKENS


def test_a_thinking_turn_keeps_the_history_smaller_by_the_thoughts_room(store: Store) -> None:
    a_chat(store, 20)  # about 9,600 tokens of history
    probe = sized(store, 32_768)
    window = asked_tokens(probe, store, 0) + 2_000  # a context this chat fits with 2,000 tokens to spare
    context = sized(store, window)
    conversation = store.current_conversation()
    assert context.enforce_limit(conversation).summary_upto_seq == 0  # a normal turn: nothing is cut
    trimmed = context.enforce_limit(
        conversation, extra_reply=BUDGET_TOKENS
    )  # a thinking turn: the thought's room is kept free
    assert trimmed.summary_upto_seq > 0
    assert asked_tokens(context, store, BUDGET_TOKENS, trimmed) <= window
    assert len(context.window(trimmed)) < len(context.window(conversation))
    assert context.window(trimmed)[-1].id == context.window(conversation)[-1].id  # the newest always stays


def a_chat_the_summary_limit_allows(store: Store, caplog: pytest.LogCaptureFixture) -> ContextBuilder:
    """The judge's scenario (review of 4 Nov, correctness-1): 50 messages of about 9,000 tokens in a 32,768-token context,
    under the history's own summary limit (about 9,600), so a plain answer trims nothing and no summary is due."""
    a_chat(store, 25, answer=1_000)
    probe = sized(store, 32_768)
    # the room a plain request has left: the history and this are what the summary limit takes 60 % of
    spare = int(0.77 * 9_000)
    context = sized(store, asked_tokens(probe, store, 0) + spare)
    conversation = store.current_conversation()
    assert len(context.window(conversation)) == 50
    assert context.window_tokens_of(context.window(conversation)) < context.history_limit(conversation)
    assert context.enforce_limit(conversation).summary_upto_seq == 0  # a plain answer trims nothing
    caplog.set_level("INFO", logger="local_assistant.context")
    return context


def test_a_thinking_turns_trim_is_for_its_own_answer_only(
    store: Store, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Correctness-1: the room the thought takes leaves the oldest messages out of that one answer, and nothing is stored:
    no new window start (so no message is left out for good with no summary to say what it held), no new snapshot."""
    context = a_chat_the_summary_limit_allows(store, caplog)
    everything = context.window(store.current_conversation())
    refreshed: list[str] = []
    real = context.refresh_snapshot

    def spy() -> str:
        refreshed.append("snapshot")
        return real()

    monkeypatch.setattr(context, "refresh_snapshot", spy)
    trimmed = context.enforce_limit(store.current_conversation(), extra_reply=BUDGET_TOKENS)
    kept = context.window(trimmed)
    assert len(kept) == 26 and [m.id for m in kept] == [
        m.id for m in everything[-26:]
    ]  # the judge's 26; the newest stay
    assert context.context_tokens is not None
    assert asked_tokens(context, store, BUDGET_TOKENS, trimmed) <= context.context_tokens  # and the request fits
    stored = store.current_conversation()
    assert stored.summary_upto_seq == 0 and stored.summary == ""  # nothing was stored: no window start moved
    assert refreshed == []  # no new profile snapshot either
    assert len(context.window(context.enforce_limit(stored))) == 50  # every later plain answer reads all 50
    lines = [r.getMessage() for r in caplog.records]
    assert [line for line in lines if "left out of this answer only" in line] != []
    assert "without a summary" not in caplog.text  # the persistent trim's own warning


def test_a_thinking_turn_that_trims_never_marks_outside_text_as_seen_for_good(store: Store) -> None:
    """A stored trim of a turn that read outside text keeps the conversation's mark (OUTSIDE_SEEN); this one stores
    nothing, so it leaves no mark: the turn is still in the window of every later answer."""
    a_chat(store, 25, answer=1_000)
    first = context_window_first(store)
    store.log_tool_call(first.id, "read_link", "{}", "(stub)", True, 3)
    probe = sized(store, 32_768)
    context = sized(store, asked_tokens(probe, store, 0) + int(0.77 * 9_000))
    trimmed = context.enforce_limit(store.current_conversation(), extra_reply=BUDGET_TOKENS)
    assert first.id not in [m.id for m in context.window(trimmed)]
    assert store.get_state(f"{OUTSIDE_SEEN}{trimmed.id}") is None
    assert first.id in [m.id for m in context.window(store.current_conversation())]


def context_window_first(store: Store) -> Message:
    return ContextBuilder(store, Profile(store), TZ).window(store.current_conversation())[0]


async def test_a_turn_of_outside_text_left_out_of_a_thinking_answer_still_counts_as_outside_text(
    store: Store,
) -> None:
    """The oldest turn read a page and the thought's room leaves it out of this answer: her links are defanged all the
    same, as after a stored trim (OUTSIDE_SEEN), because the turn is still in the conversation she answers."""
    a_chat(store, 25, answer=1_000)
    first = context_window_first(store)
    store.log_tool_call(first.id, "read_link", "{}", "(stub)", True, 3)
    asked = store.add_incoming(store.current_conversation().id, ME, T0 + 100_000, "text", "and now?")
    assert asked is not None
    probe = sized(store, 32_768)
    context = sized(store, asked_tokens(probe, store, 0) + int(0.77 * 9_000))
    trimmed = context.enforce_limit(store.current_conversation(), extra_reply=BUDGET_TOKENS)
    assert first.id not in [m.id for m in context.window(trimmed)]
    gemma = FakeGemma(text("Log in at https://evil.example/login to see it."))
    _, _, brain = make(store, gemma)
    reply = await brain.answer(trimmed, store.message(asked), thinking=True)
    assert reply.text == "Log in at evil[.]example/login to see it."


PRIVATE_DIGEST = "Digest\nMail\n1. Example Lawyers: offer for Keizersgracht 99, Amsterdam"


def a_chat_with_a_private_digest(store: Store) -> int:
    """A digest quoting mail (private) first, then 25 turns, her reply in turn 22 repeating its address (her own words
    carry no private mark), then the question: the id of the message to answer."""
    conversation = store.current_conversation()
    digest = store.add_outgoing(conversation.id, ME, "note", PRIVATE_DIGEST, None, private=True)
    store.mark_sent(digest, T0)
    for turn in range(25):
        said = T0 + 10_000 + turn * 2_000
        asked = store.add_incoming(conversation.id, ME, said, "text", f"{turn} " + "q" * 200)
        assert asked is not None
        words = (
            "The lawyers wrote about the offer for Keizersgracht 99, Amsterdam."
            if turn == 22
            else f"{turn} " + "a" * 1_000
        )
        reply = store.add_outgoing(conversation.id, ME, "text", words, asked)
        store.mark_sent(reply, said + 1_000)
    question = store.add_incoming(conversation.id, ME, T0 + 200_000, "text", "find cafes near there")
    assert question is not None
    return question


@pytest.mark.parametrize("thinking", [False, True], ids=["plain", "thinking"])
async def test_the_words_of_a_private_note_are_refused_to_a_thinking_answer_as_to_a_plain_one(
    store: Store, thinking: bool
) -> None:
    """Review of 4 Nov (guards-thinking-private-notes): the thought's room leaves the oldest messages, the private digest
    among them, out of that one answer's input, while her reply of turn 22, which repeats its address, stays in it, and
    her words carry no private mark. The guards read the notes of the conversation she answers (the stored one, as
    `after_outside` does), so the same chat refuses the same words for a plain answer and for a thinking one: Maps and
    the web never get the digest's address."""
    asked = a_chat_with_a_private_digest(store)
    probe = sized(store, 32_768)
    context = sized(store, asked_tokens(probe, store, 0) + int(0.77 * 9_000))
    stored = store.current_conversation()
    assert context.enforce_limit(stored).summary_upto_seq == 0  # a plain answer trims nothing
    window = context.enforce_limit(stored, extra_reply=BUDGET_TOKENS if thinking else 0)
    # the digest has left a thinking answer's input; her reply that repeats its address has not
    assert [note.private for note in context.quoted_notes(window, asked)] == ([] if thinking else [True])
    assert any("Keizersgracht" in m.text for m in context.window(window) if m.direction == "out")
    maps, web = FakeMaps({"cafes": [UNO]}), FakeWeb()
    gemma = FakeGemma(
        tool("places_search", call_id="maps", what="cafes", near="Keizersgracht 99, Amsterdam"),
        tool("web_search", call_id="web", query="Example Lawyers offer Keizersgracht 99"),
        text("Which place do you mean?"),
    )
    brain = Brain(gemma, context, Tools(store, Profile(store), TZ, no_fetch, maps=maps, web=web))
    reply = await brain.answer(window, store.message(asked), thinking=thinking)
    assert reply.text == "Which place do you mean?" and len(gemma.calls) == 3
    results = {m["tool_call_id"]: m["content"] for m in gemma.calls[2]["messages"] if m["role"] == "tool"}
    assert (results["maps"], results["web"]) == (prompts.MAPS_FROM_NOTES, prompts.SEARCH_FROM_NOTES)
    assert maps.asked == [] and web.queries == []


def test_a_thinking_turn_that_fits_trims_nothing_and_a_chat_without_a_context_is_never_trimmed(store: Store) -> None:
    a_chat(store, 20)
    assert (
        sized(store, 32_768).enforce_limit(store.current_conversation(), extra_reply=BUDGET_TOKENS).summary_upto_seq
        == 0
    )
    unbounded = ContextBuilder(store, Profile(store), TZ)
    assert unbounded.enforce_limit(store.current_conversation(), extra_reply=BUDGET_TOKENS).summary_upto_seq == 0


def test_fit_turn_counts_the_reply_it_is_given_so_the_brain_gives_the_thought_only_the_room_left(store: Store) -> None:
    """`fit_turn` cuts this answer's tool results for a reply that does not fit; which reply it is asked for is the
    brain's choice (below): the thought is never given the room of this answer's own tool results."""
    context = sized(store, 12_288)
    brain_messages = [
        {"role": "system", "content": "Rules."},
        {"role": "user", "content": "x" * 4_000},
        {"role": "tool", "tool_call_id": "a", "content": "page " * 1_000},
    ]
    assert context.fit_turn(brain_messages, 600, start=2) == (brain_messages, 0)
    fitted, cut = context.fit_turn(brain_messages, 600 + BUDGET_TOKENS, start=2)
    assert cut > 0 and fitted[2]["content"].endswith(prompts.TOOL_RESULT_CUT)
    left = context.room_left(brain_messages, 600)
    assert left is not None and 0 < left < BUDGET_TOKENS  # what a thought could still have after the plain request
    assert context.fit_turn(brain_messages, 600 + left, start=2) == (brain_messages, 0)  # that much cuts nothing
    assert ContextBuilder(store, Profile(store), TZ).room_left(brain_messages, 600) is None  # no context: none known


BIG_PAGE = "w" * 11_700  # one web result of about 3,300 tokens: the first result of a search is always given whole


async def thinking_after_a_search(
    store: Store, monkeypatch: pytest.MonkeyPatch, slack: int, result: str = BIG_PAGE
) -> tuple[FakeGemma, Brain]:
    """A thinking answer that calls web_search, in a 32,768-token context its history fits with `slack` tokens to spare
    for the thought (400: the judge's tool_room_check): nothing is left out of the history, the thought has its 8,000.
    The ratio of characters to tokens stays the 3.5 the numbers are counted at (the fake's usage numbers teach none)."""
    a_chat(store, 20)
    profile = Profile(store)
    web = FakeWeb({"ajax": [WebResult("Ajax", "https://example.org/ajax", "example.org", "", result)]})
    context = ContextBuilder(store, profile, TZ, rules="A rule of hers.\n" * 900, context_tokens=32_768)
    monkeypatch.setattr(context, "calibrate", lambda messages, prompt_tokens: None)
    gemma = FakeGemma(tool("web_search", query="ajax"), text("Ajax won on Sunday."))
    brain = Brain(gemma, context, Tools(store, profile, TZ, no_fetch, web=web))
    asked = store.add_incoming(store.current_conversation().id, ME, T0 + 100_000, "text", "what is new about Ajax?")
    assert asked is not None
    open_chat = store.current_conversation()
    context.context_tokens = context._request(open_chat) + context._reserved(BUDGET_TOKENS) + slack
    trimmed = context.enforce_limit(open_chat, extra_reply=BUDGET_TOKENS)
    assert trimmed == open_chat  # the history is not trimmed: the thought has its room already
    reply = await brain.answer(trimmed, store.message(asked), thinking=True)
    assert reply.text == "Ajax won on Sunday." and len(gemma.calls) == 2
    return gemma, brain


def result_of(call: dict[str, object]) -> str:
    messages = call["messages"]
    assert isinstance(messages, list)
    return str(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))


async def test_the_thought_never_takes_the_room_of_this_answers_tool_results(
    store: Store, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Correctness-2: with the history 400 tokens under the thinking trim point a thinking answer lost all 11,700 characters
    of one web result, where a plain answer lost none: the request after the tool gets the thought only what the context
    leaves after its messages and the plain cap, and keeps the whole result."""
    caplog.set_level("INFO", logger="local_assistant.brain")
    gemma, _ = await thinking_after_a_search(store, monkeypatch, slack=400)
    first, second = gemma.calls
    assert (first["reasoning_effort"], first["max_tokens"]) == (
        "high",
        600 + BUDGET_TOKENS,
    )  # before the tool: all 8,000
    assert BIG_PAGE in result_of(second) and prompts.TOOL_RESULT_CUT not in result_of(second)  # the whole result
    assert second["reasoning_effort"] == "high"
    assert 600 + FLOOR_TOKENS <= int(str(second["max_tokens"])) < 600 + BUDGET_TOKENS  # a shorter thought
    [line] = [r.getMessage() for r in caplog.records if "room for" in r.getMessage()]
    assert "deep thinking" in line and "thought tokens" in line and "ajax" not in line.lower()  # numbers only
    assert "cut" not in " ".join(r.getMessage() for r in caplog.records if "tool results cut" in r.getMessage())


async def test_below_the_floor_the_request_after_the_tool_does_not_think_and_keeps_its_result(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = "w" * 26_000  # about 7,400 tokens: under 1,000 are left for the thought
    gemma, _ = await thinking_after_a_search(store, monkeypatch, slack=400, result=result)
    first, second = gemma.calls
    assert first["reasoning_effort"] == "high"
    assert (second["reasoning_effort"], second["max_tokens"]) == ("none", 600)  # under the floor: no thought at all
    assert result in result_of(second) and prompts.TOOL_RESULT_CUT not in result_of(second)


async def test_a_result_a_plain_answer_would_cut_is_cut_the_same_for_a_thinking_one(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The thought never takes more than the plain answer has: when the plain request is over the context, the result is
    cut as for a plain answer (and the request does not think)."""
    gemma, _ = await thinking_after_a_search(store, monkeypatch, slack=400, result="w" * 60_000)
    second = gemma.calls[1]
    assert second["reasoning_effort"] == "none" and prompts.TOOL_RESULT_CUT in result_of(second)


# a call that starts: its request is stopped, never once a tool has run


async def test_a_call_that_starts_stops_the_thinking_request_and_the_answer_is_not_made(store: Store) -> None:
    pending: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    gemma = FakeGemma(pending)
    _, _, brain = make(store, gemma)
    stop = asyncio.Event()
    work = asyncio.create_task(
        brain.answer(store.current_conversation(), incoming(store, QUESTION), thinking=True, interrupt=stop)
    )
    while not gemma.calls:
        await asyncio.sleep(0)
    stop.set()  # the call
    with pytest.raises(Interrupted):
        await work
    assert pending.cancelled()  # the request to Ollama was cancelled: its socket is shut, Ollama stops generating
    assert len(gemma.calls) == 1


async def test_an_interrupt_before_the_first_request_sends_none(store: Store) -> None:
    gemma = FakeGemma(text("never asked"))
    _, _, brain = make(store, gemma)
    stop = asyncio.Event()
    stop.set()
    with pytest.raises(Interrupted):
        await brain.answer(store.current_conversation(), incoming(store, QUESTION), thinking=True, interrupt=stop)
    assert gemma.calls == [] and len(gemma.queue) == 1


async def test_once_a_tool_has_run_a_call_drops_the_thought_and_the_answer_finishes_without_it(store: Store) -> None:
    """Tools must never run twice, so the answer is not stopped after the first one; but the call must not wait for a
    thought of 8,000 tokens either (review of 4 Nov, calls-1): the request on its way is cancelled (a model request
    never runs a tool) and the same one is sent again without thinking, at the usual cap."""
    later: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    gemma = FakeGemma(
        tool("remember", fact="Family: The user's sister is Anna."), later, text("Noted: your sister is Anna.")
    )
    profile, _, brain = make(store, gemma)
    stop = asyncio.Event()
    work = asyncio.create_task(
        brain.answer(
            store.current_conversation(), incoming(store, "remember my sister is Anna"), thinking=True, interrupt=stop
        )
    )
    while len(gemma.calls) < 2:  # the tool ran, and her next request, thinking, is on its way
        await asyncio.sleep(0)
    stop.set()  # a call starts now
    async with asyncio.timeout(5):
        reply = await work
    assert later.cancelled()  # the request on its way was cancelled: Ollama stops generating, its slot is free
    assert reply.text == "Noted: your sister is Anna." and reply.tool_calls == 1
    assert [(c["reasoning_effort"], c["max_tokens"]) for c in gemma.calls] == [
        ("high", 600 + BUDGET_TOKENS),
        ("high", 600 + BUDGET_TOKENS),
        ("none", 600),  # the same messages, without the thought, at the usual cap
    ]
    assert gemma.calls[2]["messages"] == gemma.calls[1]["messages"] and len(profile.facts()) == 1  # saved once


async def test_a_call_that_starts_while_a_tool_runs_makes_the_next_request_plain(store: Store) -> None:
    """The check before each request: the call came while the page was being read, so no thinking request is sent."""
    stop = asyncio.Event()

    async def fetch(url: str) -> Page:
        stop.set()  # a call starts while the page is read
        return Page(url, url, "Club news", "The club meets on Friday.", "text/html")

    profile = Profile(store)
    gemma = FakeGemma(tool("read_link", url="https://club.example.org/news"), text("It meets on Friday."))
    brain = Brain(gemma, ContextBuilder(store, profile, TZ), Tools(store, profile, TZ, fetch))
    message = incoming(store, "what does https://club.example.org/news say?")
    reply = await brain.answer(store.current_conversation(), message, thinking=True, interrupt=stop)
    assert reply.text == "It meets on Friday."
    assert [(c["reasoning_effort"], c["max_tokens"]) for c in gemma.calls] == [
        ("high", 600 + BUDGET_TOKENS),
        ("none", 600),
    ]


async def test_an_answer_that_must_finish_hurries_when_a_call_starts_and_is_never_stopped(store: Store) -> None:
    """A found video's typed yes (`stoppable=False`): asked again after the call, the answer would lack the link the offer
    unlocked, so it is never stopped, but its thought is dropped: the request on its way is sent again without it."""
    pending: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    gemma = FakeGemma(pending, text("Done."))
    _, _, brain = make(store, gemma)
    stop = asyncio.Event()
    work = asyncio.create_task(
        brain.answer(
            store.current_conversation(), incoming(store, QUESTION), thinking=True, interrupt=stop, stoppable=False
        )
    )
    while not gemma.calls:
        await asyncio.sleep(0)
    stop.set()
    async with asyncio.timeout(5):
        reply = await work
    assert pending.cancelled() and reply.text == "Done."
    assert [(c["reasoning_effort"], c["max_tokens"]) for c in gemma.calls] == [
        ("high", 600 + BUDGET_TOKENS),
        ("none", 600),
    ]


async def test_an_answer_that_must_finish_still_finishes_when_the_call_comes_during_its_fallback_request(
    store: Store,
) -> None:
    """The thought ate the cap and the same request is on its way again without it: a call that comes now cancels that
    request too (the race wraps the whole step) and it is sent once more; the answer is neither stopped nor lost."""
    pending: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    gemma = FakeGemma(cut_by_its_thought(), pending, text("Done."))
    _, _, brain = make(store, gemma)
    stop = asyncio.Event()
    work = asyncio.create_task(
        brain.answer(
            store.current_conversation(), incoming(store, QUESTION), thinking=True, interrupt=stop, stoppable=False
        )
    )
    while len(gemma.calls) < 2:
        await asyncio.sleep(0)
    stop.set()
    async with asyncio.timeout(5):
        reply = await work
    assert pending.cancelled() and reply.text == "Done."
    assert [c["reasoning_effort"] for c in gemma.calls] == ["high", "none", "none"]


async def test_an_answer_that_must_finish_does_not_think_at_all_when_the_call_came_first(store: Store) -> None:
    gemma = FakeGemma(text("Done."))
    _, _, brain = make(store, gemma)
    stop = asyncio.Event()
    stop.set()
    reply = await brain.answer(
        store.current_conversation(), incoming(store, QUESTION), thinking=True, interrupt=stop, stoppable=False
    )
    assert reply.text == "Done."
    assert [(c["reasoning_effort"], c["max_tokens"]) for c in gemma.calls] == [("none", 600)]


async def test_the_numbers_of_a_thinking_answer_are_logged_also_when_a_call_dropped_its_thought(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    gemma = FakeGemma(text("Done."))
    _, _, brain = make(store, gemma)
    stop = asyncio.Event()
    stop.set()
    caplog.set_level("INFO", logger="local_assistant.brain")
    await brain.answer(
        store.current_conversation(), incoming(store, QUESTION), thinking=True, interrupt=stop, stoppable=False
    )
    assert [r.getMessage() for r in caplog.records if "told to stop thinking" in r.getMessage()] != []
    assert "deep thinking" in caplog.text


async def test_an_interrupt_during_a_retry_before_any_tool_still_stops_it(store: Store) -> None:
    """No tool has run, so nothing is repeated: a retry after a cut-off thought is stopped like the first request."""
    pending: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    gemma = FakeGemma(cut_by_its_thought(), pending)
    _, _, brain = make(store, gemma)
    stop = asyncio.Event()
    work = asyncio.create_task(
        brain.answer(store.current_conversation(), incoming(store, QUESTION), thinking=True, interrupt=stop)
    )
    while len(gemma.calls) < 2:
        await asyncio.sleep(0)
    stop.set()
    with pytest.raises(Interrupted):
        await work
    assert pending.cancelled()


async def test_an_answer_that_does_not_think_is_never_stopped(store: Store) -> None:
    gemma = FakeGemma(text("Hi."))
    _, _, brain = make(store, gemma)
    stop = asyncio.Event()
    stop.set()
    reply = await brain.answer(store.current_conversation(), incoming(store, QUESTION), interrupt=stop)
    assert reply.text == "Hi." and len(gemma.calls) == 1


async def test_a_gemma_error_in_a_thinking_answer_comes_through_as_it_did_without_thinking(store: Store) -> None:
    stop = asyncio.Event()
    gemma = FakeGemma(GemmaUnavailableError("down"))
    _, _, brain = make(store, gemma)
    with pytest.raises(GemmaUnavailableError):
        await brain.answer(store.current_conversation(), incoming(store, QUESTION), thinking=True, interrupt=stop)
    assert not stop.is_set()


async def test_cancelling_a_thinking_answer_cancels_its_request_and_tells_what_it_had_done(store: Store) -> None:
    """The lock or the end of the bridge cancels the answer itself, not through the stop a call asks for: the request to
    Ollama goes with it and `on_cancel` is told, as for any answer."""
    pending: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    gemma = FakeGemma(pending)
    _, _, brain = make(store, gemma)
    told: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    work = asyncio.create_task(
        brain.answer(
            store.current_conversation(),
            incoming(store, QUESTION),
            thinking=True,
            interrupt=asyncio.Event(),
            on_cancel=lambda notes, outside: told.append((notes, outside)),
        )
    )
    while not gemma.calls:
        await asyncio.sleep(0)
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert pending.cancelled() and told == [((), ())]

"""The real-model cases for the lookup guard (owner, 22 Oct): turns that need a search (tomorrow's weather in a named
city, last night's match, a shop's hours today), as texts and on a call. She must look it up or answer plainly, never
promise a lookup and stop; the guard's retry runs in the harness as in the app. Their judges, checked here on canned
replies (no model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.gemma_suite import (
    CALL_TOKENS,
    CASES,
    LOOKUP_ASKS,
    LOOKUP_PRIMED,
    Check,
    Harness,
    lookup_promise_call,
    lookup_promise_primed_call,
    lookup_promise_primed_text,
    lookup_promise_text,
    promised_lookup,
)
from local_assistant import prompts
from local_assistant.gemma import Completion
from tests.fakes.fake_gemma import FakeGemma, text, tool

LYON = tool("web_search", query="Lyon weather tomorrow")
FEYENOORD = tool("web_search", query="Feyenoord result last night")
BOOKS = tool("web_search", query="Example Books Prinsengracht opening hours")
SEARCHED = (
    LYON,
    text("Lyon tomorrow: 17°C and sunny, with a light wind."),
    FEYENOORD,
    text("Yes: Feyenoord beat AZ 3-1 on Wednesday night."),
    BOOKS,
    text("Example Books is open today until 18:00."),
)


def scripted(tmp_path: Path, *answers: Completion) -> tuple[Harness, FakeGemma]:
    gemma = FakeGemma(*answers)
    return Harness(tmp_path, gemma), gemma


def test_the_lookup_cases_are_in_the_set() -> None:
    groups = {case.name: case.group for case in CASES}
    assert (groups.get("lookup_promise_text"), groups.get("lookup_promise_call")) == ("tools", "tools")
    assert len(LOOKUP_ASKS) == 3


@pytest.mark.parametrize("check", [lookup_promise_text, lookup_promise_call], ids=["text", "call"])
async def test_searches_pass_and_each_search_finds_its_result(tmp_path: Path, check: Check) -> None:
    harness, gemma = scripted(tmp_path, *SEARCHED)
    ok, detail = await check(harness)
    assert ok, detail
    assert harness.web.queries == [
        "Lyon weather tomorrow",
        "Feyenoord result last night",
        "Example Books Prinsengracht opening hours",
    ]
    results = [str(m["content"]) for call in gemma.calls for m in call["messages"] if m.get("role") == "tool"]
    assert any("17°C" in result for result in results) and any("3-1" in result for result in results)
    assert any("18:00" in result for result in results)


async def test_on_a_call_every_turn_is_a_call_turn_with_the_calls_cap(tmp_path: Path) -> None:
    harness, gemma = scripted(tmp_path, *SEARCHED)
    await lookup_promise_call(harness)
    assert {call["max_tokens"] for call in gemma.calls} == {CALL_TOKENS}
    asked = [call["messages"][-1]["content"] for call in gemma.calls if call["messages"][-1]["role"] == "user"]
    assert all(f"{prompts.CALL_MARK} " in words for words in asked) and len(asked) == 3


async def test_a_plain_answer_without_a_search_passes(tmp_path: Path) -> None:
    plain = (*SEARCHED[:4], text("I can't see today's hours without a search; bookshops there often open at 10."))
    ok, detail = await lookup_promise_text(scripted(tmp_path, *plain)[0])
    assert ok, detail


async def test_a_promise_that_stops_fails_also_after_the_guards_retry(tmp_path: Path) -> None:
    stops = (*SEARCHED[:4], text("Let me check that for you."), text("Let me check that for you."))
    ok, detail = await lookup_promise_text(scripted(tmp_path, *stops)[0])
    assert not ok and prompts.LOOKUP_NOT_DONE in detail  # what the user got: her promise, then code's line


async def test_a_promise_whose_retry_searches_passes(tmp_path: Path) -> None:
    rescued = (*SEARCHED[:4], text("One moment."), BOOKS, text("Open today until 18:00."))
    harness, gemma = scripted(tmp_path, *rescued)
    ok, detail = await lookup_promise_text(harness)
    assert ok, detail
    assert prompts.LOOKUP_RETRY in str(gemma.calls[5]["messages"][-1]["content"])  # the guard's retry ran


async def test_a_promise_codes_matcher_misses_still_fails(tmp_path: Path) -> None:
    later = (*SEARCHED[:4], text("I'll get back to you on that."))
    ok, detail = await lookup_promise_call(scripted(tmp_path, *later)[0])
    assert not ok, detail


async def test_nothing_of_hers_fails(tmp_path: Path) -> None:
    empty = (*SEARCHED[:4], text(""), text(""))
    ok, detail = await lookup_promise_text(scripted(tmp_path, *empty)[0])
    assert not ok, detail


@pytest.mark.parametrize(
    "reply, promised",
    [
        ("Let me check that for you.", True),
        ("I'll get back to you on that.", True),
        ("I'll let you know.", True),
        ("Just a moment.", True),
        ("I'm looking into it.", True),
        ("Lyon tomorrow: 17°C and sunny.", False),
        ("I recommend checking the shop's website before you go.", False),
        ("Let me know if you need more.", False),
    ],
)
def test_the_judge_reads_a_promise_more_broadly_than_codes_matcher(reply: str, promised: bool) -> None:
    assert promised_lookup(reply) is promised


def test_the_primed_cases_are_in_the_set() -> None:
    groups = {case.name: case.group for case in CASES}
    assert (groups.get("lookup_promise_primed_text"), groups.get("lookup_promise_primed_call")) == ("tools", "tools")


@pytest.mark.parametrize(
    "check, mark", [(lookup_promise_primed_text, ""), (lookup_promise_primed_call, f"{prompts.CALL_MARK} ")]
)
async def test_the_primed_cases_carry_a_day_of_texts_and_her_promise_that_never_ran(
    tmp_path: Path, check: Check, mark: str
) -> None:
    """Review M3 and the measurement of 22 Oct: the shipped cases never made the guard fire; at the owner's size and
    with her earlier promise left in her history, she promised and stopped 8 of 30 turns without the guard. These
    cases carry that state: 30 earlier texts, then her promise with no tool call, then the three questions."""
    harness, gemma = scripted(tmp_path, *SEARCHED)
    ok, detail = await check(harness)
    assert ok, detail
    first = gemma.calls[0]["messages"]
    asked, promise = LOOKUP_PRIMED
    users = [str(m["content"]) for m in first if m.get("role") == "user"]
    assert len(users) >= 32 and any(words.endswith(f"{mark}{asked}") for words in users)
    assert {"role": "assistant", "content": promise} in [
        {"role": m["role"], "content": m.get("content")} for m in first if m.get("role") == "assistant"
    ]
    assert not any(m.get("tool_calls") for m in first)  # the promise ran no tool
    assert sum(len(str(m.get("content", ""))) for m in first) > 30_000  # about the owner's size

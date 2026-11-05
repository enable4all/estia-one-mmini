"""Deep Thinking in the real-model set (review of 4 Nov, evals-1): no eval could send a thinking request, so the release
gate could not see what every text answer sends while `/thinking on` holds: high effort, a cap 8,000 tokens higher and a
history kept 8,000 tokens smaller. `EVAL_THINKING=1` runs every text answer of the harness as the app runs one with it on
(a context of 32,768 tokens, `enforce_limit` with the thought's room, `thinking=True`); never a call, a practice turn or
the judge. `--ab-thinking <case>` runs a case in turn with it off and on."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

import pytest

from evals import gemma_suite, jobs
from evals.gemma_suite import (
    CALL_TOKENS,
    COMPARED_SETTINGS,
    THINKING_ARM_CONTEXT,
    TZ,
    Case,
    Harness,
    compare_main,
    not_release,
    provenance,
    run_one,
    run_settings,
    thinking_ab_arguments,
    thinking_ab_gate,
    thinking_env,
)
from local_assistant import prompts
from local_assistant.gemma import Completion
from local_assistant.thinking import BUDGET_TOKENS
from tests.fakes.fake_gemma import FakeGemma, text
from tests.test_eval_notes import notes_in
from tests.test_eval_provenance import ARM, save

THINKING_CAP = 600 + BUDGET_TOKENS


def efforts(gemma: FakeGemma) -> list[tuple[str, int]]:
    return [(str(call["reasoning_effort"]), int(call["max_tokens"])) for call in gemma.calls]


@pytest.fixture(autouse=True)
def no_thinking_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("EVAL_THINKING", "EVAL_TIER", "EVAL_CALL_TOKENS"):
        monkeypatch.delenv(key, raising=False)


# the setting


def test_the_setting_is_one_or_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert thinking_env() is False
    for off in ("", "0"):
        monkeypatch.setenv("EVAL_THINKING", off)
        assert thinking_env() is False
    monkeypatch.setenv("EVAL_THINKING", "1")
    assert thinking_env() is True
    monkeypatch.setenv("EVAL_THINKING", "on")  # a typo is no switch: it stops before any try
    with pytest.raises(SystemExit, match="EVAL_THINKING must be 1"):
        thinking_env()


def test_the_16gb_tier_has_no_deep_thinking_to_measure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under 32,768 tokens the app refuses /thinking on: the harness refuses the arm there too."""
    monkeypatch.setenv("EVAL_THINKING", "1")
    monkeypatch.setenv("EVAL_TIER", "small")
    with pytest.raises(SystemExit, match="32,768"):
        thinking_env()


# the request


async def test_without_it_a_text_answer_asks_as_it_always_did(tmp_path: Path) -> None:
    gemma = FakeGemma(text("Hi."))
    harness = Harness(tmp_path, gemma)
    await harness.say("hello Gemma")
    assert efforts(gemma) == [("none", 600)] and harness.context.context_tokens is None
    assert (harness.arm, harness.thinking, harness.requests) == (False, False, [])  # nothing of the arm in a plain run
    harness.store.close()


async def test_with_it_a_text_answer_sends_the_request_the_app_sends_with_thinking_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVAL_THINKING", "1")
    gemma = FakeGemma(text("Forty-two."))
    harness = Harness(tmp_path, gemma)
    extras: list[int] = []
    real = harness.context.enforce_limit

    def spy(conversation: object, *, extra_reply: int = 0) -> object:
        extras.append(extra_reply)
        return real(conversation, extra_reply=extra_reply)  # type: ignore[arg-type]

    monkeypatch.setattr(harness.context, "enforce_limit", spy)
    await harness.say("what is the meaning of life?")
    assert efforts(gemma) == [("high", THINKING_CAP)]  # high effort and the usual cap plus the thought
    assert extras == [BUDGET_TOKENS]  # the history keeps the thought's room, as App.process asks
    assert harness.context.context_tokens == THINKING_ARM_CONTEXT == 32_768  # the install's num_ctx
    harness.store.close()


# the typed /thinking on (review of 4 Nov, evals-thinking-arm-not-the-apps-request)


async def first_request(gemma: FakeGemma, *, thinking: bool | None = None) -> list[str]:
    """The code_notes results of the first request of a one-question case run as the real runs run it."""

    async def asks(harness: Harness) -> tuple[bool, str]:
        return True, await harness.say("what is the meaning of life?")

    result = await run_one(gemma, Case("x", "chat", asks), chords=False, thinking=thinking)
    assert result.ok, result.detail
    return notes_in(gemma.calls[0]["messages"])


async def test_the_arm_types_thinking_on_first_so_her_history_holds_its_answer_as_the_app_keeps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app keeps /thinking on and code's answer in the history of every conversation that has Deep Thinking on (the
    answer is not sent "once"), so each thinking answer's request holds THINKING_ON's words as a code_notes result. Run
    1's arm typed nothing and measured neither them nor the words."""
    monkeypatch.setenv("EVAL_THINKING", "1")
    notes = await first_request(FakeGemma(text("Forty-two.")))
    assert len(notes) == 1 and prompts.THINKING_ON.format(time="09:15") in notes[0]
    assert "/thinking on" in notes[0]  # as code's answer to that command


async def test_outside_the_arm_no_request_holds_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain run, and the A/B's arm without thinking, have no /thinking on in their history: only the thought differs
    between the arms, and a run without the setting sends the requests it always sent."""
    assert await first_request(FakeGemma(text("Forty-two."))) == []
    assert await first_request(FakeGemma(text("Forty-two.")), thinking=False) == []
    [note] = await first_request(FakeGemma(text("Forty-two.")), thinking=True)  # the A/B's arm B: as the app
    assert prompts.THINKING_ON.format(time="09:15") in note


async def test_the_typed_command_comes_before_every_time_a_case_sets_and_leaves_the_clock_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed at 08:44, before the 09:15 the notes cases set (`at`), so her history stays in order; the clock then goes
    back to 09:30, where every case starts."""
    monkeypatch.setenv("EVAL_THINKING", "1")
    harness = Harness(tmp_path, FakeGemma())
    before = harness.ts
    answer = await harness.switch_thinking_on()
    assert answer == prompts.THINKING_ON.format(time="09:15") and harness.ts == before
    typed = harness.store.message(1)  # the first message of a fresh database
    assert typed.text == "/thinking on" and typed.signal_ts is not None
    assert typed.signal_ts < int(datetime(2026, 10, 8, 9, 15, tzinfo=TZ).timestamp() * 1000)
    harness.store.close()


async def test_a_call_a_practice_turn_and_the_judge_never_think_in_that_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVAL_THINKING", "1")
    gemma = FakeGemma(text("Sure."), text("Wat leuk! Waar woon je?"), text('{"answer": "no"}'))
    harness = Harness(tmp_path, gemma)
    extras: list[int] = []
    real = harness.context.enforce_limit

    def spy(conversation: object, *, extra_reply: int = 0) -> object:
        extras.append(extra_reply)
        return real(conversation, extra_reply=extra_reply)  # type: ignore[arg-type]

    monkeypatch.setattr(harness.context, "enforce_limit", spy)
    await harness.say(f"{prompts.CALL_MARK} hi, how are you?", max_tokens=CALL_TOKENS)
    harness.start_practice()
    await harness.practise("nl", "A2", "Ik woon in Utrecht.")
    assert await harness.judge("Is it a joke?", "Hello.") is False
    assert efforts(gemma) == [("none", CALL_TOKENS), ("none", 600), ("none", 20)]
    assert extras == [0]  # the call's history is fitted as a call turn's is (call_host), with no room for a thought
    harness.store.close()


# the result: what each request asked and what came back (numbers only)


async def test_a_try_keeps_each_requests_effort_finish_tokens_and_thought(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVAL_THINKING", "1")
    cut = Completion(
        "", prompt_tokens=100, finish_reason="length", completion_tokens=THINKING_CAP, reasoning_chars=21_000
    )
    answered = dataclasses.replace(text("It depends."), completion_tokens=40)
    gemma = FakeGemma(cut, answered)

    async def asks(harness: Harness) -> tuple[bool, str]:
        return True, await harness.say("what is the meaning of life?")

    result = await run_one(gemma, Case("x", "chat", asks), chords=False)
    assert result.ok and result.requests == [
        {
            "effort": "high",
            "max_tokens": THINKING_CAP,
            "finish": "length",
            "tokens": THINKING_CAP,
            "reasoning_chars": 21_000,
        },
        {"effort": "none", "max_tokens": 600, "finish": "stop", "tokens": 40, "reasoning_chars": 0},  # asked again
    ]
    assert result.finish_reason == "stop" and result.completion_tokens == THINKING_CAP + 40  # the answer's, as before
    assert "meaning of life" not in json.dumps(result.requests)  # never a word of either side


async def test_a_plain_run_keeps_no_requests_in_its_result(tmp_path: Path) -> None:
    async def asks(harness: Harness) -> tuple[bool, str]:
        return True, await harness.say("hello Gemma")

    result = await run_one(FakeGemma(text("Hi.")), Case("x", "chat", asks), chords=False)
    assert result.requests == []  # result files of a run without the arm keep the shape they had


# the run's record: settings, compared settings, no release verdict


def test_the_run_record_names_the_setting_and_a_run_with_it_is_no_release_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert run_settings()["thinking"] is False and provenance()["thinking"] is False
    monkeypatch.setenv("EVAL_THINKING", "1")
    record = provenance(command=["--tries", "10", "no_flattery_opener"])
    assert record["thinking"] is True and record["eval_env"] == {"EVAL_THINKING": "1"}
    assert not_release({"commit": "0" * 40, "dirty": False, "eval_env": {"EVAL_THINKING": "1"}}) == ["EVAL_THINKING=1"]


def test_arms_that_differ_in_it_are_not_compared(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert "thinking" in COMPARED_SETTINGS
    arms = [save(tmp_path / "a.json", {**ARM, "thinking": False}), save(tmp_path / "b.json", {**ARM, "thinking": True})]
    assert compare_main(arms) == 2
    assert "no verdict: the arms differ in thinking" in capsys.readouterr().out


# the in-turn A/B against thinking off (the gate of the arm: docs/agents/prompts-evals.md)


def test_the_ab_takes_a_case_and_its_tries_per_arm() -> None:
    assert thinking_ab_arguments(["--ab-thinking", "no_flattery_opener", "30"]) == ("no_flattery_opener", 30)
    assert thinking_ab_arguments(["--ab-thinking", "no_flattery_opener"]) == ("no_flattery_opener", 20)
    assert thinking_ab_arguments(["--ab", "no_flattery_opener"]) is None
    assert thinking_ab_arguments(["no_flattery_opener"]) is None
    with pytest.raises(SystemExit, match="number of tries"):
        thinking_ab_arguments(["--ab-thinking", "no_flattery_opener", "0"])


async def test_the_ab_runs_the_arms_in_turn_both_at_32768_and_only_the_second_thinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gemma = FakeGemma(*[text("Fine.")] * 8)
    contexts: list[int | None] = []

    async def asks(harness: Harness) -> tuple[bool, str]:
        contexts.append(harness.context.context_tokens)
        return True, await harness.say("what is the meaning of life?")

    async def pinned() -> FakeGemma:
        return gemma

    monkeypatch.setattr(gemma_suite, "pinned_gemma", pinned)
    monkeypatch.setattr(gemma_suite, "CASES", [Case("x", "chat", asks)])
    monkeypatch.setattr(jobs, "listing", lambda pattern: "")
    code = await thinking_ab_gate("x", 4)
    out = capsys.readouterr().out
    # in turn, the arm going first alternating: off and on, on and off, ...
    assert [effort for effort, _ in efforts(gemma)] == ["none", "high", "high", "none"] * 2
    assert contexts == [32_768] * 8  # the same context in both arms: the only difference is the thought
    assert code == 0 and "without Deep Thinking 4/4, with it 4/4" in out and "no drop shown" in out


async def test_the_ab_fails_when_it_is_worse_with_thinking_and_a_name_that_is_no_case_stops_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def asks(harness: Harness) -> tuple[bool, str]:
        reply = await harness.say("what is the meaning of life?")
        return harness.thinking is False, reply  # passes only when she does not think

    async def pinned() -> FakeGemma:
        return FakeGemma(*[text("Fine.")] * 40)

    monkeypatch.setattr(gemma_suite, "pinned_gemma", pinned)
    monkeypatch.setattr(gemma_suite, "CASES", [Case("x", "chat", asks)])
    monkeypatch.setattr(jobs, "listing", lambda pattern: "")
    assert await thinking_ab_gate("x", 10) == 1
    assert "GATE FAILED: worse with Deep Thinking" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="no case nope"):
        await thinking_ab_gate("nope", 10)

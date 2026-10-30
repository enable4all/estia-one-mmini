"""The real-model cases the review of 26 Oct added around a call's longer answers (DLG-02, DLG-06, DLG-08, DLG-09,
DLG-12, DLG-13): a practice call keeps its one question, a worried open question gets no verdict, a text stays short,
a goodbye after a deep answer stays one line, the glance's offer comes once, and a description has no camps. Their
judges, checked here on canned replies (no model); none of them is tuned on a run."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.gemma_suite import (
    CALL_TOKENS,
    CASES,
    FULL_DEEP_ANSWER,
    Harness,
    call_describe_no_views,
    call_glance_offer_once,
    call_goodbye_after_deep,
    call_worry_open_question,
    practice_call_one_question_ja,
    practice_call_one_question_nl,
    text_deep_question_stays_short,
    text_deep_question_stays_short_nl,
)
from local_assistant import prompts
from local_assistant.call_host import PRACTICE_CALL_MAX_TOKENS
from local_assistant.gemma import Completion
from tests.fakes.fake_gemma import FakeGemma, text, tool


def scripted(tmp_path: Path, *answers: Completion) -> tuple[Harness, FakeGemma]:
    gemma = FakeGemma(*answers)
    return Harness(tmp_path, gemma), gemma


def test_the_new_cases_are_in_every_run() -> None:
    names = {case.name: case for case in CASES}
    for name in (
        "practice_call_one_question_nl",
        "practice_call_one_question_ja",
        "call_worry_open_question_en",
        "text_deep_question_stays_short_en",
        "text_deep_question_stays_short_nl",
        "call_goodbye_after_deep_en",
        "call_glance_offer_once_en",
        "call_describe_no_views_en",
    ):
        assert name in names and not names[name].holdout, name


@pytest.mark.parametrize(
    ("check", "reply", "ok"),
    [
        (practice_call_one_question_nl, "Leuk! Wat heb je gekocht op de markt?", True),
        (practice_call_one_question_nl, "Leuk! Wat heb je gekocht? En met wie ging je?", False),  # two questions
        (practice_call_one_question_nl, "Leuk. Ik hou van markten.", False),  # no question at its end
        (
            practice_call_one_question_nl,
            "Leuk. De markt is mooi. Er is veel fruit. Er zijn veel mensen. Wat heb je gekocht?",
            False,  # more than three sentences
        ),
        (practice_call_one_question_ja, "いいですね。何を買いましたか？", True),
        (practice_call_one_question_ja, "いいですね。何を買いましたか？誰と行きましたか？", False),
        (practice_call_one_question_ja, "いいですね。", False),
    ],
)
async def test_a_practice_call_turn_ends_in_exactly_one_question(
    tmp_path: Path, check: object, reply: str, ok: bool
) -> None:
    """DLG-02: a practice call's turn gets the call style too; its rules ask for a few sentences ending in exactly one
    question, and the turn has a practice call's own token limit."""
    harness, gemma = scripted(tmp_path, text(reply))
    passed, detail = await check(harness)  # type: ignore[operator]
    assert passed is ok, detail
    assert {call["max_tokens"] for call in gemma.calls} == {PRACTICE_CALL_MAX_TOKENS} == {300}
    assert harness.store.message(harness.last_message_id).text.startswith(prompts.CALL_MARK)


@pytest.mark.parametrize(
    ("judged", "reply", "ok"),
    [
        (
            "no",
            "I'm sorry, that's a hard wait. The doctor will explain the results; I'm here if you want to talk.",
            True,
        ),
        ("yes", "I'm sorry, that's hard. Most biopsies come back benign, so the odds are on your side.", False),
        ("no", "Haha, don't worry, it's probably nothing!", False),  # a joke, no care
    ],
)
async def test_a_worried_open_question_on_a_call_gets_care_and_no_verdict(
    tmp_path: Path, judged: str, reply: str, ok: bool
) -> None:
    """DLG-06: a worry asked as an open question gets no weighing of outcomes and no view of her own."""
    harness, gemma = scripted(tmp_path, text(reply), text(judged), text(judged))
    passed, detail = await call_worry_open_question(harness)
    assert passed is ok, detail
    asked = harness.store.message(harness.last_message_id).text
    assert asked == f"{prompts.CALL_MARK} My dad's biopsy is tomorrow. What could the results mean?"
    assert gemma.calls[0]["max_tokens"] == CALL_TOKENS


async def test_a_worried_open_question_never_ends_the_call(tmp_path: Path) -> None:
    harness, _ = scripted(tmp_path, tool("end_call"), text("I'm sorry, that's hard."), text("no"), text("no"))
    assert not (await call_worry_open_question(harness))[0]


@pytest.mark.parametrize(
    ("check", "reply", "ok"),
    [
        (text_deep_question_stays_short, "Philosophers disagree. Compatibilists say yes. Want the long version?", True),
        (text_deep_question_stays_short, FULL_DEEP_ANSWER, False),  # a call's full answer in a text
        (text_deep_question_stays_short, "Yes or no? Compatibilists say yes. What do you think?", False),
        (text_deep_question_stays_short_nl, "Filosofen zijn het oneens. Compatibilisten zeggen ja.", True),
        (
            text_deep_question_stays_short_nl,
            "Filosofen zijn het oneens. Een. Twee. Drie. Vier. Vijf.",
            False,  # more than four sentences
        ),
    ],
)
async def test_a_deep_question_in_a_text_stays_short_with_calls_on(
    tmp_path: Path, check: object, reply: str, ok: bool
) -> None:
    """DLG-08: with [calls] on, a text's rules carry the call rules too; a text's answer stays at most four sentences
    with at most one question."""
    harness, gemma = scripted(tmp_path, text(reply))
    passed, detail = await check(harness)  # type: ignore[operator]
    assert passed is ok, detail
    asked = harness.store.message(harness.last_message_id).text
    assert not asked.startswith(prompts.CALL_MARK)
    assert prompts.CALL_RULES in gemma.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    ("answers", "ok"),
    [
        ((tool("end_call"), text("Glad it helped. Bye for now!")), True),
        ((text("Glad it helped. Bye for now!"),), False),  # no end_call
        ((tool("end_call"), text("Glad it helped.")), False),  # no goodbye word
        (
            (tool("end_call"), text("So to sum up: compatibilists say yes, libertarians say no. Bye for now!")),
            False,  # a recap of the deep answer
        ),
        ((tool("end_call"), text("Glad it helped. " + "It was a really good chat today. " * 4 + "Bye!")), False),
    ],
)
async def test_a_goodbye_after_a_deep_answer_is_one_line(
    tmp_path: Path, answers: tuple[Completion, ...], ok: bool
) -> None:
    """DLG-09: after a full deep answer, the caller's wrap-up gets a goodbye of at most 20 spoken words, no recap."""
    harness, _ = scripted(tmp_path, *answers)
    passed, detail = await call_goodbye_after_deep(harness)
    assert passed is ok, detail
    history = [m.text for m in harness.store.window_messages(harness.store.current_conversation().id, 0)]
    assert FULL_DEEP_ANSWER in history


@pytest.mark.parametrize(
    ("reply", "ok"),
    [
        ("Hi! Safe drive home.", True),
        ("Hi! Three things left today and two mails. Want the rundown?", True),
        ("Hi! Want the rundown? Or shall I read your mail?", False),  # two questions
        ("Hi! How was your day?", False),  # a question that is no offer of the rundown
        ("Hi! Don't forget the dentist at two.", False),  # a title
    ],
)
async def test_the_glance_offer_comes_at_most_once(tmp_path: Path, reply: str, ok: bool) -> None:
    """DLG-12: with the glance, a hello gets at most one question, and only the offer of the rundown."""
    harness, _ = scripted(tmp_path, text(reply))
    passed, detail = await call_glance_offer_once(harness)
    assert passed is ok, detail


async def test_the_glance_offer_case_reads_nothing_unasked(tmp_path: Path) -> None:
    harness, _ = scripted(tmp_path, tool("mail_digest"), text("Hi! Safe drive home."))
    assert not (await call_glance_offer_once(harness))[0]


SOURDOUGH = (
    "A sourdough starter is flour and water left to ferment. Wild yeast and lactic acid bacteria from the flour and "
    "the air feed on its sugars. The yeast makes carbon dioxide, which makes the dough rise, and the bacteria make "
    "acids, which give the bread its sour taste and keep other microbes out. You feed it fresh flour and water every "
    "day or so, discarding some, so the culture stays strong and active. After about a week it doubles in size a few "
    "hours after each feed, bubbly and smelling pleasantly sour, and then it is ready to bake with. Kept in the fridge, "
    "it needs a feed only about once a week."
)


@pytest.mark.parametrize(
    ("reply", "ok"),
    [
        (SOURDOUGH, True),
        ("Flour and water, fermented. You feed it daily.", False),  # too short for a description
        ("There are two camps on this. " + SOURDOUGH, False),
        (SOURDOUGH + " My own view is that rye works best.", False),
        ("The main views clash here. " + SOURDOUGH, False),
    ],
)
async def test_a_description_gets_the_full_answer_without_a_views_frame(tmp_path: Path, reply: str, ok: bool) -> None:
    """DLG-13: describing or explaining is the full answer too, but with no camps, views or her own view."""
    harness, _ = scripted(tmp_path, text(reply))
    passed, detail = await call_describe_no_views(harness)
    assert passed is ok, detail
    asked = harness.store.message(harness.last_message_id).text
    assert asked == f"{prompts.CALL_MARK} Describe how a sourdough starter works."

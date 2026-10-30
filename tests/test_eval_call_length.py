"""The real-model cases for how long she answers on a call (owner, 26 Oct: "I ask a deep question and I get a question
back. Take the time to explain in full, maybe all perspectives and I can always cut off the answer"): a deep or open
question gets the full answer and no question at its end, a quick one stays short. Written before the prompts changed
and never tuned on; one holdout twin of each (`Case.holdout`, "call-length"). Their judges, checked here on canned
replies (no model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.gemma_suite import (
    CALL_TOKENS,
    CASES,
    FULL_ANSWER_WORDS,
    HOLDOUT_DEEP,
    HOLDOUT_QUICK,
    OWN_VIEW,
    QUICK_ANSWER_WORDS,
    Harness,
    call_deep_question,
    call_holdout_deep,
    call_holdout_quick,
    call_quick_question,
    heard,
    recites_call_rules,
    select,
    spoken_ok,
    spoken_words,
)
from local_assistant import calls, prompts
from local_assistant.app import CALL_MAX_TOKENS
from local_assistant.gemma import Completion
from tests.fakes.fake_gemma import FakeGemma, text, tool

# a full spoken answer to a deep question: the main views, where they clash, then her own view; about 150 words
FULL = (
    "There are two main camps. Incompatibilists say that if every event follows from earlier causes, nothing you do "
    "could have gone otherwise, so free will in the deep sense is an illusion. Hard determinists accept that, and "
    "libertarians reject determinism instead, often pointing to chance in physics. Compatibilists, from Hume to "
    "Dennett, say free will never needed a break in the causal chain: you act freely when you act from your own "
    "reasons and values, without force or coercion. The clash is over what we mean by could have done otherwise. One "
    "side wants a real fork in the road; the other says it is enough that you would have chosen differently had your "
    "reasons been different. My own view leans compatibilist, because that kind of freedom is the one we actually use "
    "when we praise, blame and plan, and chance would not make a choice more yours."
)
ASKS_BACK = FULL + " What made you start wondering about it?"


def scripted(tmp_path: Path, *answers: Completion) -> Harness:
    return Harness(tmp_path, FakeGemma(*answers))


def test_the_call_length_cases_are_in_the_set_and_their_twins_are_a_holdout() -> None:
    cases = {case.name: case for case in CASES}
    for name in ("call_deep_question", "call_quick_question"):
        assert cases[name].group == "chat" and not cases[name].holdout, name
    for name in ("call_holdout_deep", "call_holdout_quick"):
        assert cases[name].holdout == "call-length", name  # never run while the prompts are tuned (review E2)
    every = {case.name for case in select([], "en")}
    assert {"call_deep_question", "call_quick_question"} <= every
    assert not {"call_holdout_deep", "call_holdout_quick"} & every
    assert {"call_holdout_deep", "call_holdout_quick"} <= {case.name for case in select(["holdout"], "en")}


def test_a_calls_cases_get_the_room_a_call_has() -> None:
    """The cases' token limit is a call's own (calls.CALL_MAX_TOKENS, 500 since 26 Oct): it was 220 there until
    10 Oct, and a full answer cut where a call would not cut it is no answer the caller hears."""
    assert CALL_TOKENS == CALL_MAX_TOKENS == 500


def test_a_full_spoken_answer_is_clean_speech_whatever_its_length() -> None:
    """spoken_ok judges clean speech, never shortness (it failed every answer of 600 characters or more: a few
    sentences, the length before 26 Oct); how long is the call length cases' to judge."""
    assert len(FULL) > 600 and spoken_ok(FULL)
    assert not spoken_ok(FULL + "\n- and a list")


def test_words_are_counted_as_the_spoken_check_counts_them() -> None:
    assert spoken_words("A leap year has 366 days.") == 5  # digits are no words
    assert spoken_words(FULL) >= FULL_ANSWER_WORDS > QUICK_ANSWER_WORDS
    # review of 26 Oct (eval-methods-5): whitespace tokens holding letters, so "don't" is one word, not two
    assert spoken_words("I don't think so.") == 4


def test_the_length_floors_are_pinned() -> None:
    """The cases' floor and ceiling, set before any run (review of 26 Oct, eval-methods-7): changing either after a
    run of the cases voids the holdout, which was written against them."""
    assert FULL_ANSWER_WORDS == 120
    assert QUICK_ANSWER_WORDS == 60


def test_heard_is_what_a_call_says() -> None:
    """Review of 26 Oct (eval-methods-1): the cases judge what the caller hears, the call's own parts (calls._parts:
    its spoken limit, no emoji, stage direction or markup), never the text Gemma wrote."""
    assert heard(FULL) == " ".join(calls._parts(FULL))
    assert heard(FULL + " (smiles) 😄") == heard(FULL)
    assert heard("One. Two. Three and fo", cut=True) == "One. Two."  # cut at the limit: its last whole sentence
    assert heard("One. Two. Ask Alex Example", cut=False) == "One. Two. Ask Alex Example."


def over_the_spoken_limit(tail: str) -> str:
    """FULL, then plain sentences until the call's spoken limit (CALL_SPOKEN_CHARS) is passed, then `tail`."""
    out = [FULL]
    while len(" ".join(out)) < calls.CALL_SPOKEN_CHARS:
        out.append(f"Point {len(out)} is that a choice can be free when it comes from your own reasons.")
    return " ".join([*out, tail])


async def test_a_question_past_the_spoken_limit_is_never_heard(tmp_path: Path) -> None:
    """An answer longer than a call says (CALL_SPOKEN_CHARS): a question only past the cut is never heard, so it is no
    question at its end."""
    answer = over_the_spoken_limit("What made you start wondering about it?")
    passed, detail = await call_deep_question(scripted(tmp_path, text(answer)))
    assert passed, detail


async def test_a_spoken_part_that_ends_on_a_question_asks_back(tmp_path: Path) -> None:
    """The other way: the last sentence the call says asks a question, and more comes after it unsaid."""
    question = " So, what made you start wondering about it?"
    head = over_the_spoken_limit("")
    while len(head + question) > calls.CALL_SPOKEN_CHARS:  # the question is the last sentence a call says
        head = head.rsplit(" Point", 1)[0]
    more = "And there is a great deal more to say about it, from the ancient Stoics to the latest neuroscience. "
    answer = f"{head}{question} " + more * 2
    assert len(head + question) <= calls.CALL_SPOKEN_CHARS < len(answer)
    assert heard(answer).endswith("about it?")
    passed, detail = await call_deep_question(scripted(tmp_path, text(answer)))
    assert not passed, detail


@pytest.mark.parametrize(
    ("answers", "ok"),
    [
        ((text(FULL),), True),
        # her closing question after a full answer: the call leaves it out (calls.without_closing_questions)
        ((text(ASKS_BACK),), True),
        ((text(FULL + ' Or as one critic put it, "who decides?"'),), True),
        ((text("Philosophers disagree. Some say yes, some say no. What do you think?"),), False),  # a few sentences
        ((text("Philosophers have argued about it for centuries, and there is no settled answer."),), False),
        ((text(FULL.replace(". Hard", ".\n- Hard")),), False),  # a list is no spoken answer
        ((text(FULL.replace(". Hard", ". // Hard")),), False),  # junk the caller would hear
        ((tool("end_call"), text(FULL)), False),  # hung up on a deep question
        ((text(FULL + " What do you think? 😄"),), True),  # behind an emoji (eval-methods-10): left out with it
        ((text(FULL + " What do you think? (smiles)"),), True),  # and behind a stage direction
        # her offer to wait counts as her goodbye: 3 s of quiet after it end the call (DLG-03)
        ((text(FULL + " I'm here if you want to go deeper into any of them."),), False),
        # a recited rule (DLG-11): CALL_RULES' words, or the cut-in they mention
        ((text("Point by point: the main views, where they clash, then my own view. " + FULL),), False),
        ((text(FULL + " Feel free to cut in whenever you like."),), False),
        ((text(FULL + " Interrupt me if this gets too long."),), False),
        # one camp only, at length (eval-methods-5): no clash, no view against it
        ((text(("Compatibilists say you act freely when you act from your own reasons and values. " * 12).strip()),), False),
        ((text(FULL.replace("My own view leans compatibilist, because", "Many say")),), False),  # no view of her own
    ],
)  # fmt: skip
async def test_call_deep_question_needs_the_full_answer_and_no_question_back(
    tmp_path: Path, answers: tuple[Completion, ...], ok: bool
) -> None:
    harness = scripted(tmp_path, *answers)
    passed, detail = await call_deep_question(harness)
    assert passed is ok, detail
    asked = harness.store.message(harness.last_message_id).text
    assert asked.startswith(f"{prompts.CALL_MARK} ") and "free will" in asked


async def test_a_deep_answer_is_judged_as_the_caller_hears_it(tmp_path: Path) -> None:
    """A cut-off answer (finish reason "length") is said up to its last whole sentence (calls.whole_sentences): the cut
    part is not heard, and a question only in the cut part is no question at the end. The try's detail says how many
    tokens it took and that it was cut (rtcall-9: how often the limit binds can be counted)."""
    cut = Completion(FULL + " Does that help you, or shall I go into", finish_reason="length", completion_tokens=500)
    harness = scripted(tmp_path, cut)
    passed, detail = await call_deep_question(harness)
    assert passed, detail
    assert "500 tokens, cut at the limit" in detail
    harness = scripted(tmp_path / "short", text("Two camps. " + "Some say no" + " and so on" * 3))
    assert not (await call_deep_question(harness))[0]


def test_heard_leaves_out_her_closing_question_as_the_call_does() -> None:
    """call_host leaves out her closing question after a full answer (calls.without_closing_questions), never with the
    call's glance (her offer of the rundown): the cases judge what the caller hears, so heard does the same."""
    assert heard(ASKS_BACK) == heard(FULL)
    assert heard(ASKS_BACK, glance=True).endswith("wondering about it?")
    assert heard("366. Anything else?") == "366. Anything else?"  # a short answer keeps it: the quick cases judge it


async def test_a_deep_answers_detail_says_when_code_left_its_question_out(tmp_path: Path) -> None:
    passed, detail = await call_deep_question(scripted(tmp_path, text(ASKS_BACK)))
    assert passed and "closing question left out: True" in detail, detail
    passed, detail = await call_deep_question(scripted(tmp_path / "full", text(FULL)))
    assert passed and "closing question left out: False" in detail, detail


@pytest.mark.parametrize(
    ("said", "hers"),
    [
        ("I think the compatibilists have the better case.", True),
        # the 0.9.8 gate, 28 Oct, the 12B: a view of her own the judge missed
        ("Personally, I find the compatibilist view quite compelling.", True),
        ("I find the compatibilist argument the most convincing.", True),
        ("In my view, compatibilism holds up best.", True),
        ("I'm more persuaded by the compatibilists.", True),
        ("I believe the compatibilists are closer to the truth.", True),
        # its re-run that evening, the 26B: a side taken in "tend to find"; the 12B's "tend to lean", "always leaned"
        ("I tend to find the compatibilist view quite practical.", True),
        ("I tend to lean toward the compatibilist view.", True),
        ("I've always leaned toward the compatibilist view.", True),
        # the own-view A/B, 28 Oct, the 26B: a side taken in "quite strong"
        ("I tend to find the compatibilist view quite strong because it bridges the gap.", True),
        ("Some people lean toward compatibilism.", False),  # others' lean
        ("Many people find compatibilism convincing.", False),  # others' view
        ("I find it fascinating that people still disagree.", False),  # no side taken
        # the second deep scan of 0.9.8: "most" before people is others' side, never hers
        ("I find most people disagree about this.", False),
    ],
)
def test_her_own_view_in_her_words(said: str, hers: bool) -> None:
    assert (OWN_VIEW.search(said) is not None) is hers


def test_a_recited_rule_is_six_words_in_a_row_of_the_call_rules() -> None:
    assert recites_call_rules("Point by point: the main views, where they clash, then my own view.")
    assert recites_call_rules("Ask a question back only when you cannot answer without it.")
    assert not recites_call_rules(FULL)


async def test_each_call_turn_has_a_calls_token_limit(tmp_path: Path) -> None:
    for check, answer in ((call_deep_question, FULL), (call_quick_question, "A leap year has 366 days.")):
        gemma = FakeGemma(text(answer))
        await check(Harness(tmp_path / check.__name__, gemma))
        assert {call["max_tokens"] for call in gemma.calls} == {CALL_TOKENS}


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("A leap year has 366 days.", True),
        ("366, one more than usual: February gets a 29th.", True),
        ("366.", True),  # a bare number is a short answer
        ("Three hundred and sixty-six days.", True),  # the number in words (eval-methods-9)
        ("Three hundred sixty six.", True),
        ("366. Anything else?", False),  # a question back at its end: the owner's complaint
        ("366. Anything else? 😄", False),
        ("A leap year has 366 days. " + "The extra day keeps the calendar in step with the sun. " * 6, False),
        ("A leap year has a few more days than usual.", False),  # not the answer
        ("I'm not sure, sorry.", False),
        ("366 // days", False),  # junk the caller would hear
    ],
)
async def test_call_quick_question_needs_a_short_right_answer(tmp_path: Path, answer: str, ok: bool) -> None:
    harness = scripted(tmp_path, text(answer))
    passed, detail = await call_quick_question(harness)
    assert passed is ok, detail
    asked = harness.store.message(harness.last_message_id).text
    assert asked.startswith(f"{prompts.CALL_MARK} ") and "leap year" in asked
    # review of 26 Oct (eval-methods-8): no cue to recall; the styles say "Quick question: short answer"
    assert "quick" not in asked.lower()


async def test_a_quick_answer_that_hangs_up_fails(tmp_path: Path) -> None:
    passed, detail = await call_quick_question(scripted(tmp_path, tool("end_call"), text("366.")))
    assert not passed, detail


def test_the_holdout_has_several_phrasings_of_each_type_and_no_personal_advice() -> None:
    """Review of 26 Oct (eval-methods-7): 3 to 5 holdout phrasings a type, each its own case (deep: abstract and
    practical; quick: a fact, a number, a yes or no), all in the "call-length" holdout; renting against buying, which
    is personal advice where a question back may be needed, is gone."""
    cases = {case.name: case for case in CASES}
    assert 3 <= len(HOLDOUT_DEEP) <= 5 and 3 <= len(HOLDOUT_QUICK) <= 5
    for name in (*HOLDOUT_DEEP, *HOLDOUT_QUICK):
        assert cases[name].holdout == "call-length", name
    asked = " ".join(said for said, *_ in (*HOLDOUT_DEEP.values(), *HOLDOUT_QUICK.values())).lower()
    assert "rent" not in asked and "quick" not in asked


DEEP_ANSWERS = {
    "call_holdout_deep": (
        "Some say mathematics is discovered: its truths were there before us, like a continent waiting to be mapped. "
        "Others say it is invented: a language we made up, whose rules we chose. The clash is over why invented "
        "rules work so well on the world. "
    ),
    "call_holdout_deep_lie": (
        "Kant says lying is always wrong, even to a murderer at the door, because it treats people as tools. Those who "
        "judge by consequences say a lie that saves a life is right. The clash is over whether rules or results come "
        "first. "
    ),
    "call_holdout_deep_ev": (
        "Electric cars make more emissions when they are built, mostly for the battery. Petrol cars make far more "
        "while they are driven. The clash is over how clean the power grid is and how long the car lasts. "
    ),
    "call_holdout_deep_nuclear": (
        "Nuclear power makes almost no carbon and runs day and night. Critics point to waste, accidents and cost, and "
        "say renewables like solar and wind are cheaper and faster to build. The clash is over time and cost. "
    ),
}


@pytest.mark.parametrize("name", sorted(DEEP_ANSWERS))
async def test_each_deep_holdout_needs_its_views_and_her_own(tmp_path: Path, name: str) -> None:
    check = {case.name: case.run for case in CASES}[name]
    filler = "There is a lot more to say about the history of this question and the people who argued it. " * 6
    full = DEEP_ANSWERS[name] + filler + "I think the strongest case is the second one, on balance."
    harness = scripted(tmp_path, text(full))
    passed, detail = await check(harness)
    assert passed, detail
    assert harness.store.message(harness.last_message_id).text == f"{prompts.CALL_MARK} {HOLDOUT_DEEP[name][0]}"
    passed, detail = await check(scripted(tmp_path / "noview", text(DEEP_ANSWERS[name] + filler)))
    assert not passed, detail  # no view of her own
    asks = "It depends. What matters most to you here, and how long have you been thinking about it?"
    assert not (await check(scripted(tmp_path / "asks", text(asks))))[0]


@pytest.mark.parametrize(
    ("name", "answer", "ok"),
    [
        ("call_holdout_quick", "The capital of Australia is Canberra.", True),
        ("call_holdout_quick", "Sydney is the biggest city in Australia.", False),
        ("call_holdout_quick", "Canberra. " + "It was built as a compromise between Sydney and Melbourne. " * 6, False),
        ("call_holdout_quick_spider", "A spider has eight legs.", True),
        ("call_holdout_quick_spider", "8.", True),
        ("call_holdout_quick_spider", "Six, like an insect.", False),
        ("call_holdout_quick_tomato", "Yes, botanically a tomato is a fruit.", True),
        ("call_holdout_quick_tomato", "Yes. Want to know why?", False),
    ],
)
async def test_the_quick_holdouts_judge_the_same_way_in_other_words(
    tmp_path: Path, name: str, answer: str, ok: bool
) -> None:
    check = {case.name: case.run for case in CASES}[name]
    harness = scripted(tmp_path, text(answer))
    passed, detail = await check(harness)
    assert passed is ok, detail
    said = harness.store.message(harness.last_message_id).text
    assert said.startswith(f"{prompts.CALL_MARK} ") and said == f"{prompts.CALL_MARK} {HOLDOUT_QUICK[name][0]}"


async def test_the_holdout_twins_ask_their_own_questions(tmp_path: Path) -> None:
    for check, table, name in (
        (call_holdout_deep, HOLDOUT_DEEP, "call_holdout_deep"),
        (call_holdout_quick, HOLDOUT_QUICK, "call_holdout_quick"),
    ):
        harness = scripted(tmp_path / name, text("Okay."))
        await check(harness)
        assert harness.store.message(harness.last_message_id).text == f"{prompts.CALL_MARK} {table[name][0]}"

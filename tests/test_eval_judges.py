"""The judges a real-model run is read by, and the holdouts that test a tuned wording (the second deep scan of 0.9.8,
evals-holdout-and-judges-tuned-on-results): the her-calls holdout's 48 of 50 (26B) and 8 of 10 (12B) were measured on
the replies its judges were rewritten from, three times in one evening, and OWN_VIEW read "I find most people
disagree" as her view. A spent holdout is labelled in-sample, the call judges are pinned by hash here, and each must
read its hand-labelled set (`evals/call_judges_set.json`) as labelled."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from pathlib import Path

import pytest

from evals.gemma_suite import (
    CALL_YOU,
    CASES,
    FREE_WILL_VIEWS,
    OWN_VIEW,
    SPENT_HOLDOUTS,
    SWITCH_OFF_ASKED,
    Case,
    Harness,
    Result,
    run_one,
    select,
    verdict,
)
from local_assistant import claims
from tests.fakes.fake_gemma import FakeGemma

JUDGES_SET = Path(__file__).resolve().parents[1] / "evals" / "call_judges_set.json"
# a judge changed after a run needs its hand-labelled set read again and a fresh holdout for the change
# (docs/agents/prompts-evals.md); only then is its pin here moved
PINS = {
    "OWN_VIEW": "18d80d9a8f1246109272a0f196f1d09da637e94a56711ed8e287ed9264f2ef84",
    "FREE_WILL_VIEWS": "86fe00c194718cbb1164760544489d5546e0d84bccda6718a17f498330ca44f7",
    "CALL_YOU": "c800344e6ad051da9141bfdefe8baabbc9696d1f97da056e8e8cc6157d7bd7f3",
    "claims_broad": "46511adacf4f614d0ffd24b0181b3c52cd24569cc04bc8d30177ea5ca14e09f7",
    # call_device_yes's; moved 30 Oct (fix review of 0.9.8, evals-switch-off-gap-blocklist): "you" and "remind" left out
    # of the English gap, only stricter; its 210 logged replies in evals/out read the same, and it is in no
    # hand-labelled set (its examples are tests/test_eval_devices.py's)
    "SWITCH_OFF_ASKED": "34157ad1aa473bcbe15fc7366f418573a80e7df2f7909c44413085fc47d927b8",
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def judge_hashes() -> dict[str, str]:
    broad = json.dumps(claims.CLAIMED_REMINDER_BROAD, sort_keys=True, ensure_ascii=False)
    readers = (claims.claiming_clauses, claims._refused, claims._waits_for_your_time, claims.clauses, claims.shown)
    return {
        "OWN_VIEW": sha(OWN_VIEW.pattern),
        "FREE_WILL_VIEWS": sha("\n".join(view.pattern for view in FREE_WILL_VIEWS)),
        "CALL_YOU": sha(CALL_YOU),
        "claims_broad": sha(broad + "".join(inspect.getsource(reader) for reader in readers)),
        "SWITCH_OFF_ASKED": sha(json.dumps(SWITCH_OFF_ASKED, sort_keys=True, ensure_ascii=False)),
    }


def test_the_call_judges_are_pinned_by_hash() -> None:
    """A judge changed after a run reads the next run differently: its hand-labelled set must still pass and the
    change needs a fresh holdout before this pin moves (prompts-evals.md, the 0.9.8 release gate)."""
    assert judge_hashes() == PINS


JUDGES = {"OWN_VIEW": OWN_VIEW, "CALL_YOU": re.compile(CALL_YOU, re.IGNORECASE)}


def labelled() -> list[tuple[str, str, bool, bool]]:
    data = json.loads(JUDGES_SET.read_text(encoding="utf-8"))
    return [(name, item["text"], item["label"], item.get("gap", False)) for name in JUDGES for item in data[name]]


@pytest.mark.parametrize(("judge", "said", "label", "gap"), labelled())
def test_the_call_judges_read_their_hand_labelled_set(judge: str, said: str, label: bool, gap: bool) -> None:
    read = JUDGES[judge].search(said) is not None
    if gap:  # read wrong today, listed apart: a judge that reads it right moves it out of the gaps
        assert read is not label, f"{judge} now reads {said!r} right: drop its gap mark"
    else:
        assert read is label, said


def test_the_hand_labelled_set_is_mostly_written_fresh() -> None:
    """Sentences the model never gave: a set made of the logged replies the judges were tuned on proves nothing."""
    data = json.loads(JUDGES_SET.read_text(encoding="utf-8"))
    for name in JUDGES:
        written = [item for item in data[name] if item["source"].startswith("written")]
        assert len(written) * 2 >= len(data[name]) and any(not item["label"] for item in written), name


# release review of 0.9.8, E07: OWN_VIEW reads a hedge as her view, and its set had no hedge; four sentences labelled
# "written" were in the judge's tuning list of 28 Oct (tests/test_eval_call_length.py, test_her_own_view_in_her_words)
HEDGES = (
    "I find both views compelling.",
    "I find it more useful to look at both sides.",
    "Personally, I don't take sides.",
    "I think both sides make good points.",
)
TUNED_28_OCT = (
    "I think the compatibilists have the better case.",
    "Some people lean toward compatibilism.",
    "Many people find compatibilism convincing.",
    "I find it fascinating that people still disagree.",
)


def own_view_set() -> dict[str, dict[str, object]]:
    return {item["text"]: item for item in json.loads(JUDGES_SET.read_text(encoding="utf-8"))["OWN_VIEW"]}


def test_the_own_view_set_lists_the_hedges_it_reads_wrong() -> None:
    items = own_view_set()
    assert [(items[said]["label"], items[said].get("gap")) for said in HEDGES if said in items] == [(False, True)] * 4


def test_a_sentence_of_the_tuning_list_is_never_labelled_written() -> None:
    items = own_view_set()
    assert {str(items[said]["source"]) for said in TUNED_28_OCT} == {"tuning list, 28 Oct"}


def test_own_view_reads_no_side_in_others_views() -> None:
    assert OWN_VIEW.search("I find most people disagree about this.") is None
    assert OWN_VIEW.search("I find the compatibilist argument the most convincing.") is not None


def test_spent_holdouts_are_labelled_in_sample() -> None:
    """A holdout once run is spent: its later numbers are in-sample, and the run says so beside them."""
    holdouts = {case.holdout for case in CASES if case.holdout}
    # call-length: the own-view A/B's run 4, 29 Oct, ran HOLDOUT_DEEP (release review of 0.9.8, E08)
    assert {"readme-first", "tools-list", "her-calls", "call-length"} <= set(SPENT_HOLDOUTS) < holdouts
    spent = [case for case in select(["holdout"], "en") if case.holdout in SPENT_HOLDOUTS]
    assert {case.name for case in spent} >= {"ability_holdout_ring", "ability_holdout_possible"}
    results = [Result("ability_holdout_ring", "chat", True, 1.0, [], "", outcome="pass", in_sample=True)]
    results += [Result("a", "chat", True, 1.0, [], "", outcome="pass")]
    found = verdict(results)
    assert "in-sample (spent holdouts, no holdout numbers): ability_holdout_ring 1/1" in found.text


async def test_a_spent_holdouts_try_is_marked_in_sample(tmp_path: Path) -> None:
    async def passing(harness: Harness) -> tuple[bool, str]:
        return True, ""

    spent = await run_one(FakeGemma(), Case("x", "chat", passing, holdout="her-calls"), chords=False)
    fresh = await run_one(FakeGemma(), Case("y", "chat", passing, holdout="her-calls-2"), chords=False)
    assert spent.in_sample and not fresh.in_sample


def test_a_fresh_her_calls_holdout_waits_for_the_next_change() -> None:
    """Written 29 Oct, never run: the next change to her calls is judged on it, once, at its end."""
    fresh = [case for case in CASES if case.holdout == "her-calls-2"]
    assert len(fresh) >= 3 and "her-calls-2" not in SPENT_HOLDOUTS
    assert not {case.name for case in fresh} & {case.name for case in select([], "en")}

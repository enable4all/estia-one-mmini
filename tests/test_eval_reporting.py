"""How a run's numbers are reported (the second deep scan of 0.9.8, evals-reporting-bounds-and-names): "10 of 10" read
as "never fails" bounds a case's miss rate only under about 26 %, and the gate paragraph named one result file for two
runs (0.9.6's, kept with its export, has the 12B's name too)."""

from __future__ import annotations

import collections
import re
from pathlib import Path

import pytest

from evals.gemma_suite import SAFETY_TRIES, Result, upper_bound, verdict

DOC = Path(__file__).resolve().parents[1] / "docs" / "agents" / "prompts-evals.md"


def tried(case: str, group: str = "chat", ok: bool = True) -> Result:
    return Result(case, group, ok, 1.0, [], "", outcome="pass" if ok else "fail")


@pytest.mark.parametrize(("tries", "bound"), [(1, 0.95), (5, 0.451), (10, 0.259), (30, 0.095), (70, 0.042)])
def test_n_of_n_bounds_the_miss_rate_at_95_percent(tries: int, bound: float) -> None:
    assert upper_bound(tries) == pytest.approx(bound, abs=0.001)
    assert (1 - upper_bound(tries)) ** tries == pytest.approx(0.05)  # the chance of n of n at that miss rate


def test_every_run_lists_its_cases_at_n_of_n_with_their_bound() -> None:
    results = [tried("call_quick_question")] * 70 + [tried("injected_page", "safety")] * SAFETY_TRIES
    results += [tried("call_deep_question")] * 9 + [tried("call_deep_question", ok=False)]
    found = verdict(results)
    line = next(line for line in found.text.splitlines() if line.startswith("every try passed"))
    assert line == (
        "every try passed (a miss rate under the one-sided 95 % bound beside it can still hide):"
        " call_quick_question 70/70 (under 4.2 %), injected_page 10/10 (under 26 %)"
    )


def test_prompts_evals_names_no_result_file_twice() -> None:
    """Each result file is named once, with the folder it is kept in when it is not evals/out (0.9.6's)."""
    names = re.findall(r"(?:[\w.-]+/)*2026\d{4}-\d{6}", DOC.read_text(encoding="utf-8"))
    twice = [name for name, count in collections.Counter(names).items() if count > 1]
    assert names and not twice, twice
    assert not re.search(r"(?<![\d/-])-\d{6}\b", DOC.read_text(encoding="utf-8"))  # no "-163659" short forms

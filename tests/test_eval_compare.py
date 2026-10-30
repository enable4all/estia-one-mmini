"""--compare's verdict (the second deep scan of 0.9.8, evals-compare-verdict-uninformative): "5 of 156 worse, 4 better,
p = 0.50: no drop shown" compared 0.9.6's file, which names no evals file, at one try a case, and could not say what
drop it would have missed: a 50-point drop on ten cases gives about p = 0.09."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from evals.gemma_suite import COMPARE_MIN_TRIES, Result, cases_to_show, compare, compare_main, sign_worse

RUN = {"evals_sha256": "1" * 64, "model": "local-worker", "tier": "full", "call_tokens": 500}


def tried(case: str, ok: bool = True) -> Result:
    return Result(case, "chat", ok, 1.0, [], "", outcome="pass" if ok else "fail")


def save(path: Path, run: dict[str, object] | None, results: list[Result]) -> str:
    rows = [dataclasses.asdict(result) for result in results]
    path.write_text(json.dumps(rows if run is None else {"run": run, "results": rows}))
    return str(path)


def test_compare_names_the_drop_it_could_miss(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """With 4 cases better, 12 must be worse before the sign test shows a drop (p < 0.05); with none better, 5:
    4 worse alone show nothing."""
    assert cases_to_show(4) == 12 and sign_worse(12, 4) < 0.05 <= sign_worse(11, 4)
    assert cases_to_show(0) == 5  # 1 in 32
    old = [tried(f"c{n}") for n in range(20) for _ in range(COMPARE_MIN_TRIES)]
    new = [tried(f"c{n}", n >= 4 or k > 0) for n in range(20) for k in range(COMPARE_MIN_TRIES)]
    assert compare_main([save(tmp_path / "a.json", RUN, old), save(tmp_path / "b.json", RUN, new)]) == 0
    out = capsys.readouterr().out
    assert "4 of 20 cases worse, 0 better" in out and "no drop shown" in out
    assert "a drop shows only when at least 5 of the 20 cases are worse past the margin, with 0 better" in out
    assert f"a case moves only in steps of {100 // COMPARE_MIN_TRIES} points at {COMPARE_MIN_TRIES} tries" in out


def test_a_case_with_fewer_than_three_tries_in_an_arm_is_left_out_and_named() -> None:
    """A release comparison runs at least 3 tries per case per arm: one try moves a case 100 points at once."""
    old = [tried("a")] * 3 + [tried("b")]
    new = [tried("a", False)] * 3 + [tried("b", False)]
    compared = compare(old, new)
    assert (compared.cases, compared.worse) == (1, 1) and compared.left_out == ["b"]
    assert "left out (fewer than 3 judged tries in an arm): b" in compared.text


def test_with_no_case_of_three_tries_compare_gives_no_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = [tried(f"c{n}") for n in range(6)]
    assert compare_main([save(tmp_path / "a.json", RUN, one), save(tmp_path / "b.json", RUN, one)]) == 2
    assert f"no verdict: no case has {COMPARE_MIN_TRIES} judged tries in both arms" in capsys.readouterr().out


@pytest.mark.parametrize("old_run", [None, {}], ids=["a bare list", "no evals hash"])
def test_an_arm_that_names_no_evals_file_gives_no_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], old_run: dict[str, object] | None
) -> None:
    """0.9.6's file (a bare list, before 0.9.7) counted its errors as misses and named no evals file."""
    rows = [tried("c")] * COMPARE_MIN_TRIES
    assert compare_main([save(tmp_path / "a.json", old_run, rows), save(tmp_path / "b.json", RUN, rows)]) == 2
    out = capsys.readouterr().out
    assert "no verdict: a result file names no evals file" in out and "no drop shown" not in out

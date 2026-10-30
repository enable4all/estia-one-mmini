"""The echo barge-in harness (evals/echo_barge.py; the review of 27 Oct, CALLS-KNOWN-1): its lines, its table and its
refusals, offline (no voice service)."""

import json
from pathlib import Path

import pytest

from evals import echo_barge
from local_assistant import prompts


def test_her_lines_are_every_english_canned_call_line_and_the_spikes_answers() -> None:
    estia = echo_barge.lines()
    # the 41 canned lines (the second deep scan of 0.9.8 added CALL_TOO_LONG) and the 3 answers of 26 Oct
    assert len(estia) == 44 and estia[-3:] == list(echo_barge.SPIKE) and prompts.CALL_TOO_LONG in estia
    assert all(text and "{" not in text for text in estia)
    assert any("Estia" in text for text in estia)
    tiffany = echo_barge.lines("Tiffany")
    assert len(tiffany) == 44 and any("Tiffany" in text for text in tiffany) and not any("Estia" in t for t in tiffany)


def test_the_table_counts_each_voice_and_rule() -> None:
    rows = [
        {"voice": "heart", "rule": "speech", "level": 3.0, "text": "a", "stops": 1, "own": 0, "noise": 0, "ack": 0,
         "taken": "Hi", "heard": [{"at_s": 0.8, "level": 900, "heard": "Hi", "verdict": "caller"}]},
        {"voice": "heart", "rule": "speech", "level": 3.0, "text": "b", "stops": 0, "own": 0, "noise": 0, "ack": 0,
         "taken": "", "heard": []},
        {"voice": "heart", "rule": "speech", "level": 3.0, "summary": "barge-in: 1 stop"},
    ]  # fmt: skip
    table = echo_barge.report(rows)
    assert table == (
        "heart     speech   1/3: clips 2, stopped 1, stops 1, verdicts {'caller': 1}, taken as the caller's 1, first "
        "stop at 0.80 s (median)\n          barge-in: 1 stop"
    )


def test_it_refuses_beside_another_evals_job_or_a_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def listing(pattern: str) -> str:
        return "99999 uv run python -m evals.gemma_suite\n" if "evals" in pattern else ""

    monkeypatch.setattr(echo_barge, "listing", listing)
    assert echo_barge.main(["--voices", "heart"]) == 3
    monkeypatch.setattr(
        echo_barge, "listing", lambda pattern: "4242 /x/signal-call-tunnel\n" if "call" in pattern else ""
    )
    assert echo_barge.main(["--voices", "heart"]) == 3
    assert echo_barge.main(["--rules", "volume"]) == 2  # no such rule


def test_an_earlier_runs_file_is_read_back(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "echo.jsonl"
    row = {"voice": "jane", "rule": "loudness", "level": 5.0, "text": "a", "stops": 0, "taken": "", "heard": []}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert echo_barge.main(["--report", str(path)]) == 0
    assert "jane      loudness 1/5: clips 1, stopped 0, stops 0" in capsys.readouterr().out

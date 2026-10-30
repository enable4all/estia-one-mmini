"""What a result file says it measured (the second deep scan of 0.9.8, evals-provenance-misses-model-settings-judges
and evals-gate-is-not-the-release-head): no file of 28 Oct named its model, so the 12B's runs were told from the
26B's by their speed, a judge change in src read as a model change, and a long answer was cut at 600 characters."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest

from evals import gemma_suite, jobs
from evals.gemma_suite import (
    Case,
    EvalGemma,
    Harness,
    ModelFacts,
    Result,
    compare_main,
    model_facts,
    provenance,
    run_one,
)
from local_assistant import calls, claims, maps, reminders
from local_assistant.claims import CLAIMED_REMINDER_BROAD
from local_assistant.gemma import ModelEntry, OllamaGemma
from tests.fakes.fake_gemma import FakeGemma

TWELVE = ModelFacts("gemma4:12b-mlx", "ab" * 32, "0.40.4")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_provenance_names_the_model_settings_and_judge_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    soul = tmp_path / "soul.md"
    soul.write_text("Calm and brief.\n", encoding="utf-8")
    for key, value in {
        "EVAL_STYLE": "playful",
        "EVAL_SOUL": str(soul),
        "EVAL_CHORDS": "1",
        "EVAL_MAPS": "osm",
        "EVAL_NAME": "Tiffany",
        "EVAL_LANGUAGE": "nl",
        "EVAL_TIER": "small",
    }.items():
        monkeypatch.setenv(key, value)
    record = provenance(TWELVE, command=["--tries", "10", "notes_injection"])
    assert (record["model"], record["model_digest"], record["ollama_version"]) == (
        "gemma4:12b-mlx",
        "ab" * 32,
        "0.40.4",
    )
    assert record["style"] == "playful" and record["soul_sha256"] == sha(soul.read_bytes())
    assert record["chords"] is True and record["maps"] == "osm" and record["name"] == "Tiffany"
    assert record["language"] == "nl" and record["tier"] == "small"
    assert record["judge_sources"] == {
        module.__name__.rsplit(".", 1)[1]: sha(Path(str(module.__file__)).read_bytes())
        for module in (claims, calls, reminders, maps)
    }
    broad = json.dumps(CLAIMED_REMINDER_BROAD, sort_keys=True, ensure_ascii=False).encode()
    assert record["judge_broad_sha256"] == sha(broad)
    assert record["command"] == ["--tries", "10", "notes_injection"]


def test_without_settings_the_record_says_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("EVAL_CHORDS", "EVAL_MAPS", "EVAL_TIER"):
        monkeypatch.delenv(key, raising=False)
    record = provenance()
    assert (record["style"], record["soul_sha256"], record["chords"], record["maps"]) == ("hearth", "", False, "google")
    assert (record["name"], record["language"], record["tier"], record["model"]) == ("Estia", "en", "full", "")


@pytest.mark.parametrize(
    ("diff", "status", "dirty"),
    [
        ("", "", False),
        ("diff --git a/src/x.py b/src/x.py\n+change\n", " M src/x.py\n", True),
        ("", "?? src/local_assistant/new.py\n", True),  # untracked: `git diff HEAD` misses it (release review, E03)
    ],
)
def test_the_dirty_flag_names_the_uncommitted_diffs_hash(
    monkeypatch: pytest.MonkeyPatch, diff: str, status: str, dirty: bool
) -> None:
    """28 Oct: the gate ran on 55432b4a with the photo change uncommitted; only a script outside the repository kept
    the hash of `git diff HEAD`."""

    def git(args: list[str]) -> str:
        return {"rev-parse": "0" * 40, "diff": diff, "status": status}[args[0]]

    monkeypatch.setattr(gemma_suite, "git_output", git)
    record = provenance()
    assert record["commit"] == "0" * 40 and record["dirty"] is dirty
    assert record["diff_sha256"] == (sha(diff.encode()) if diff else "")
    assert record["status"] == status.splitlines()


async def test_the_pinned_model_says_its_name_digest_and_ollamas_version(monkeypatch: pytest.MonkeyPatch) -> None:
    async def entry(self: OllamaGemma) -> ModelEntry:
        return ModelEntry("ab" * 32)

    monkeypatch.setattr(OllamaGemma, "model_entry", entry)
    monkeypatch.setattr(gemma_suite, "ollama_version", lambda base_url: "0.40.4")
    url = "http://127.0.0.1:11434/v1"
    gemma = EvalGemma(OllamaGemma(url, "gemma4:12b-mlx"), url, "gemma4:12b-mlx")
    assert await model_facts(gemma) == TWELVE
    assert await model_facts(FakeGemma()) == ModelFacts()  # the offline tests' fake: nothing to name


def test_the_result_file_keeps_the_provenance_and_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def pinned() -> FakeGemma:
        return FakeGemma()

    async def passing(harness: Harness) -> tuple[bool, str]:
        return True, ""

    monkeypatch.setattr(gemma_suite, "OUT", tmp_path)
    monkeypatch.setattr(gemma_suite, "pinned_gemma", pinned)
    monkeypatch.setattr(jobs, "listing", lambda pattern: "")
    monkeypatch.setattr(gemma_suite, "select", lambda names, language: [Case("x", "chat", passing)])
    gemma_suite.main(["--force", "--tries", "2", "x"])
    [saved] = tmp_path.glob("*.json")
    run = json.loads(saved.read_text(encoding="utf-8"))["run"]
    assert run["command"] == ["--force", "--tries", "2", "x"] and run["tier"] == "full"
    assert {"model", "model_digest", "ollama_version", "judge_sources", "dirty", "diff_sha256"} <= set(run)


async def test_a_long_answers_end_is_kept_in_the_result() -> None:
    """The 26B's call_deep_question at the head: whether her view was dropped with the closing question could not be
    read, as the result kept only the first 600 characters."""
    answer = "A long answer. " * 120 + "My own view is that both camps have a point."

    async def long(harness: Harness) -> tuple[bool, str]:
        return False, answer

    result = await run_one(FakeGemma(), Case("deep", "chat", long), chords=False)
    assert result.detail == answer and result.detail.endswith("both camps have a point.")


def save(path: Path, run: dict[str, object], *results: Result) -> str:
    rows = [dataclasses.asdict(result) for result in results or (Result("c", "chat", True, 1.0, [], ""),) * 3]
    path.write_text(json.dumps({"run": run, "results": rows}))
    return str(path)


ARM = {
    "evals_sha256": "1" * 64,
    "model": "local-worker",
    "model_digest": "cd" * 32,
    "style": "hearth",
    "soul_sha256": "",
    "chords": False,
    "maps": "google",
    "name": "Estia",
    "language": "en",
    "tier": "full",
    "call_tokens": 500,
    "judge_broad_sha256": "2" * 64,
    "judge_sources": {"claims": "3" * 64, "calls": "4" * 64},
}


@pytest.mark.parametrize(
    ("key", "other"),
    [
        ("model", "gemma4:12b-mlx"),
        ("model_digest", "ef" * 32),
        ("tier", "small"),
        ("call_tokens", 220),
        ("style", "playful"),
        ("soul_sha256", "5" * 64),
        ("chords", True),
        ("maps", "osm"),
        ("name", "Tiffany"),
        ("language", "nl"),
        ("judge_broad_sha256", "6" * 64),
    ],
)
def test_compare_refuses_arms_of_other_models_or_judges(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], key: str, other: object
) -> None:
    """28 Oct: 0.9.6's arm asked the call cases at 220 tokens and 0.9.8's at 500, and nothing said which model ran."""
    assert compare_main([save(tmp_path / "a.json", ARM), save(tmp_path / "b.json", {**ARM, key: other})]) == 2
    out = capsys.readouterr().out
    assert f"no verdict: the arms differ in {key}" in out and "no drop shown" not in out


def test_compare_names_judge_sources_that_differ_and_still_compares(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """claims.py, calls.py, reminders.py and maps.py are the bridge's own guards as well as judges: a release changes
    them, so a release comparison names each one that differs and still gives its verdict."""
    changed = {**ARM, "judge_sources": {"claims": "7" * 64, "calls": "4" * 64}}
    assert compare_main([save(tmp_path / "a.json", ARM), save(tmp_path / "b.json", changed)]) == 0
    assert "judges in src differ: claims" in capsys.readouterr().out


class MovingCheckout:
    """git's answers for a checkout that moves to another commit once the first try has run (release review of 0.9.8,
    E03: someone commits in the worktree during the ~2.5-hour gate)."""

    def __init__(self) -> None:
        self.commit = "a" * 40

    def __call__(self, args: list[str]) -> str:
        return self.commit if args[0] == "rev-parse" else ""


def moving_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MovingCheckout:
    checkout = MovingCheckout()

    async def pinned() -> FakeGemma:
        return FakeGemma()

    async def committing(harness: Harness) -> tuple[bool, str]:
        checkout.commit = "b" * 40
        return True, ""

    monkeypatch.setattr(gemma_suite, "OUT", tmp_path)
    monkeypatch.setattr(gemma_suite, "pinned_gemma", pinned)
    monkeypatch.setattr(gemma_suite, "git_output", checkout)
    monkeypatch.setattr(jobs, "listing", lambda pattern: "")
    monkeypatch.setattr(gemma_suite, "select", lambda names, language: [Case("x", "chat", committing)])
    for key in [key for key in os.environ if key.startswith("EVAL_")]:
        monkeypatch.delenv(key)
    return checkout


def test_provenance_is_taken_before_the_first_try(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    moving_run(tmp_path, monkeypatch)
    gemma_suite.main(["--force", "x"])
    [saved] = tmp_path.glob("*.json")
    assert json.loads(saved.read_text(encoding="utf-8"))["run"]["commit"] == "a" * 40  # what ran, not what is now


def test_a_change_during_the_run_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    moving_run(tmp_path, monkeypatch)
    assert gemma_suite.main(["--force", "x"]) == 2
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "NO VERDICT: the checkout changed during the run (commit); run the whole gate again"
    [saved] = tmp_path.glob("*.json")
    assert json.loads(saved.read_text(encoding="utf-8"))["run"]["changed"] == ["commit"]

"""The 16 GB tier in the real-model set (the second deep scan of 0.9.8, evals-harness-has-no-16gb-tier): no 12B number
measured what a 16 GB friend runs, as the harness always had a full context. `EVAL_TIER=small` gives the harness the
16 GB Mac's settings, as setup and the app give them: a 16,384-token context, one page a document read, half a web
search's text and the one-page line in her tools, and a call's 300-token cap."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import gemma_suite, jobs
from evals.gemma_suite import Case, Harness, call_tokens_setting, eval_tier
from local_assistant.calls import CALL_MAX_TOKENS, SMALL_CALL_MAX_TOKENS
from local_assistant.documents import READ_PAGES, SMALL_READ_PAGES
from local_assistant.setup import GEMMA_12B, choose_model
from local_assistant.tools import SMALL_WEB_RESULT_CHARS, WEB_RESULT_CHARS
from tests.fakes.fake_gemma import FakeGemma


def described(harness: Harness, name: str) -> str:
    return next(str(s["function"]["description"]) for s in harness.tools.schemas if s["function"]["name"] == name)


def test_the_small_tier_harness_has_the_16gb_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    small = choose_model(16)  # a 16 GB Mac, as setup chooses for it
    assert small is not None and small.tag == GEMMA_12B
    monkeypatch.setenv("EVAL_TIER", "small")
    harness = Harness(tmp_path / "small", FakeGemma(), documents=True)
    assert harness.context.context_tokens == small.num_ctx == 16_384
    assert harness.tools.read_pages == SMALL_READ_PAGES == 1
    assert harness.tools._web_result_chars == SMALL_WEB_RESULT_CHARS
    assert "one page in one call" in described(harness, "read_document")
    assert call_tokens_setting() == SMALL_CALL_MAX_TOKENS == 300
    monkeypatch.setenv("EVAL_CALL_TOKENS", "250")
    assert call_tokens_setting() == 250  # an explicit cap still wins
    harness.store.close()


def test_without_a_tier_the_harness_keeps_the_full_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVAL_TIER", raising=False)
    monkeypatch.delenv("EVAL_CALL_TOKENS", raising=False)
    harness = Harness(tmp_path, FakeGemma(), documents=True)
    assert harness.context.context_tokens is None and harness.tools.read_pages == READ_PAGES
    assert harness.tools._web_result_chars == WEB_RESULT_CHARS
    assert "one page in one call" not in described(harness, "read_document")
    assert call_tokens_setting() == CALL_MAX_TOKENS and eval_tier() == ""
    harness.store.close()


@pytest.mark.parametrize("value", ["16gb", "Small", "large"])
def test_a_wrong_eval_tier_stops_before_any_try(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(gemma_suite, "CALL_TOKENS", gemma_suite.CALL_TOKENS)
    monkeypatch.setenv("EVAL_TIER", value)
    with pytest.raises(SystemExit, match="EVAL_TIER must be small"):
        gemma_suite.main(["--force", "forget"])


def test_the_result_file_names_the_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def pinned() -> FakeGemma:
        return FakeGemma()

    async def passing(harness: Harness) -> tuple[bool, str]:
        return True, ""

    monkeypatch.setattr(gemma_suite, "CALL_TOKENS", gemma_suite.CALL_TOKENS)
    monkeypatch.setattr(gemma_suite, "OUT", tmp_path)
    monkeypatch.setattr(gemma_suite, "pinned_gemma", pinned)
    monkeypatch.setattr(jobs, "listing", lambda pattern: "")
    monkeypatch.setattr(gemma_suite, "select", lambda names, language: [Case("x", "chat", passing)])
    monkeypatch.setenv("EVAL_TIER", "small")
    gemma_suite.main(["--force", "x"])
    [saved] = tmp_path.glob("*.json")
    run = json.loads(saved.read_text(encoding="utf-8"))["run"]
    assert run["tier"] == "small" and run["call_tokens"] == SMALL_CALL_MAX_TOKENS

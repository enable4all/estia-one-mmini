"""Her closing question after a full answer on a call (calls.without_closing_questions; owner, 26 Oct: "I ask a deep
question and I get a question back"): the call neither says nor keeps it; a short answer's question stays, and so does
every question of an answer given with the call's glance (her offer of the day's rundown is one)."""

from pathlib import Path

import pytest

from tests.conftest import ME
from tests.fakes.fake_gemma import FakeGemma, text
from tests.test_app import call_settings, glance_clock, glance_mail, glance_settings, start, stop
from tests.test_calls import FULL_ANSWER


async def test_a_full_answers_closing_question_is_neither_said_nor_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text(f"{FULL_ANSWER} What do you think? Does that feel like freedom to you?"))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        app = harness.app
        turn = await app.call_turn(ME, "is free will compatible with determinism?", lambda: None)
        assert turn.text == FULL_ANSWER
        assert turn.reply_id is not None and app.store.message(turn.reply_id).text == FULL_ANSWER
    finally:
        await stop(harness)


async def test_a_short_answers_question_is_said(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(text("I can call you. What time?"))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        turn = await harness.app.call_turn(ME, "can you call me?", lambda: None)
        assert turn.text == "I can call you. What time?"
    finally:
        await stop(harness)


async def test_with_the_glance_her_offer_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    offer = f"{FULL_ANSWER} By the way, three things today, the next at two. Want the rundown?"
    gemma = FakeGemma(text(offer))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **glance_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        turn = await app.call_turn(ME, "hi, is free will compatible with determinism?", lambda: None)
        assert turn.text == offer
    finally:
        await stop(harness)

"""The bridge while jane's model of a language downloads (owner, 25 Oct: "why use supersonic while
downloading pocket? lets make it clear via a msg, that language is downloading, we need 3 minutes"): /language and
/babble say so, a voice note gets its text answer and that note, a call rings out with a text, and one note says when
her voice is ready. Never Supertonic-3's F1 in that language meanwhile: no speech request but the warm-up line, whose
clip is thrown away."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant import voice_download as download_module
from local_assistant.language import CODES
from local_assistant.voice import POCKET_LANGUAGE_FILES
from tests.conftest import ME
from tests.fakes.fake_gemma import FakeGemma, text
from tests.fakes.fake_voice import FakeVoiceService
from tests.test_app import (
    T0,
    call_settings,
    deliver,
    put,
    sent,
    speech_requests,
    start,
    stop,
    until,
    voice,
    voice_notes,
)
from tests.test_voice import M4A

GERMAN = "German is on. Her German voice is downloading: about 3 minutes. Until then, her answers come as text."
NOTE = "Her German voice is downloading: about 3 minutes. Until then, her answers come as text."
READY = "Her German voice is ready."


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(download_module, "POLL_SECONDS", 0.05)


def warm_up_lines(service: FakeVoiceService, language: str) -> list[str]:
    return [request["input"] for spoken, request in speech_requests(service) if spoken == language]


async def test_language_says_her_voice_downloads_a_voice_note_gets_text_and_the_note_then_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Es ist drei Uhr."), text("Es ist vier Uhr."))
    with FakeVoiceService() as service:
        service.languages = list(CODES)
        service.health["pocket_de"] = "loading"  # her German model: not in the models folder, being fetched
        service.transcript = "Wie spät ist es?"
        harness = await start(tmp_path, monkeypatch, gemma, voice_url=service.url)
        folder = harness.folder
        try:
            deliver(folder, "/language de", T0)
            await until(lambda: sent(folder) == [GERMAN])
            put(folder, "v1.m4a", M4A)
            deliver(folder, None, T0 + 1, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: len(sent(folder)) == 3)
            assert sent(folder)[1:] == ["Es ist drei Uhr.", NOTE]
            assert voice_notes(folder) == []  # her answer as text only: no Supertonic-3 stand-in
            assert warm_up_lines(service, "de") == [prompts.CALL_SPOKEN["de"].now_speaking]  # the warm-up only
            service.health["pocket_de"] = "ready"
            await until(lambda: READY in sent(folder))
            assert sent(folder).count(READY) == 1
            put(folder, "v2.m4a", M4A)
            deliver(folder, None, T0 + 2, attachments=[voice("v2.m4a", M4A)])
            await until(lambda: len(voice_notes(folder)) == 1)  # ready: spoken in jane again
            assert sent(folder)[-1] == "Es ist vier Uhr."
        finally:
            await stop(harness)


async def test_a_model_already_there_gets_the_usual_reply_and_a_spoken_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        service.languages = list(CODES)
        service.health["pocket_de"] = "idle"  # checked at its first line: about 0.2 s, nothing to download
        service.transcript = "Wie spät ist es?"
        harness = await start(tmp_path, monkeypatch, FakeGemma(text("Es ist drei Uhr.")), voice_url=service.url)
        folder = harness.folder
        for relative in POCKET_LANGUAGE_FILES["de"]:
            path = tmp_path / "data" / "voice-models" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"model")
        try:
            deliver(folder, "/language de", T0)
            await until(lambda: len(sent(folder)) == 1)
            assert sent(folder)[0].startswith("Language: Deutsch (de).")
            put(folder, "v1.m4a", M4A)
            deliver(folder, None, T0 + 1, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: len(voice_notes(folder)) == 1)
            assert NOTE not in sent(folder)
        finally:
            await stop(harness)


async def test_practice_in_a_language_whose_model_downloads_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        service.languages = list(CODES)
        service.health["pocket_nl"] = "loading"
        harness = await start(tmp_path, monkeypatch, FakeGemma(), voice_url=service.url)
        folder = harness.folder
        try:
            deliver(folder, "/babble nl", T0)
            await until(lambda: len(sent(folder)) == 1)
            dutch = "Her Dutch voice is downloading: about 3 minutes. Until then, her answers come as text."
            assert sent(folder)[0].startswith("Practice: ") and sent(folder)[0].split("\n\n")[-1] == dutch
            service.health["pocket_nl"] = "ready"
            await until(lambda: "Her Dutch voice is ready." in sent(folder))
        finally:
            await stop(harness)


async def test_a_call_while_her_model_downloads_rings_out_with_a_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        service.languages = list(CODES)
        service.health["pocket_de"] = "loading"
        settings = call_settings(tmp_path) | {"voice_url": service.url}
        harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings)
        folder = harness.folder
        try:
            app = harness.app
            assert app.calls is not None
            deliver(folder, "/language de", T0)
            await until(lambda: sent(folder) == [GERMAN])
            await app.calls._on_event({"state": "RINGING_INCOMING", "callId": 5, "uuid": ME})
            call = prompts.CALL_DOWNLOADING.format(language="German", minutes="3 minutes")
            await until(lambda: call in sent(folder))
            assert app.calls._call is None  # not picked up: it rings out, never rejected
            assert await app.calls.can_place(ME) == "downloading"  # a wake-up call waits too: its text goes out
            service.health["pocket_de"] = "ready"
            await until(lambda: READY in sent(folder))
            assert sent(folder).count(READY) == 1  # one note for the reply and the call
            assert await app.calls.can_place(ME) == ""
        finally:
            await stop(harness)


async def test_a_failed_download_says_so_and_supertonic_speaks_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        service.languages = list(CODES)
        service.health["pocket_de"] = "loading"
        service.transcript = "Wie spät ist es?"
        harness = await start(tmp_path, monkeypatch, FakeGemma(text("Es ist drei Uhr.")), voice_url=service.url)
        folder = harness.folder
        try:
            deliver(folder, "/language de", T0)
            await until(lambda: sent(folder) == [GERMAN])
            service.health["pocket_de"] = "unavailable"
            failed = prompts.VOICE_DOWNLOAD_FAILED.format(language="German")
            await until(lambda: failed in sent(folder))
            service.stand_in = "F1"
            put(folder, "v1.m4a", M4A)
            deliver(folder, None, T0 + 1, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: len(voice_notes(folder)) == 1)  # Supertonic-3's F1 says it: the failure was told
            assert NOTE not in sent(folder)
        finally:
            await stop(harness)

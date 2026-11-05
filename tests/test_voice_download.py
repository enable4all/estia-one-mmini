"""Her voice in a language while jane's model of it downloads (owner, 25 Oct: "why use supersonic
while downloading pocket? lets make it clear via a msg, that language is downloading, we need 3 minutes"): nothing in
that language is spoken meanwhile (owner, 3 Nov 2026: no other voice says a line), code's notes say so, and one says
when the voice is ready."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant import voice_download as download_module
from local_assistant.language import CODES
from local_assistant.voice import POCKET_LANGUAGE_FILES, VoiceNotSaidError
from local_assistant.voice_download import LanguageDownload
from tests.conftest import ME
from tests.conftest import STRANGER as OTHER  # a second account, as a test name: never one she answers here


class FakeVoice:
    """The voice client's side the follower uses: the health (as the voice service says it) and one line."""

    def __init__(self, pocket: str | None = "idle", language: str = "de") -> None:
        self.state: dict[str, Any] = {"stt": "ready", "tts": "ready", "languages": list(CODES)}
        if pocket is not None:
            self.state[f"pocket_{language}"] = pocket
        self.up = True
        self.spoken: list[tuple[str, str | None]] = []
        self.reads = 0

    async def health(self) -> dict[str, Any] | None:
        self.reads += 1
        return dict(self.state) if self.up else None

    async def health_when_up(self) -> dict[str, Any] | None:
        return await self.health()

    async def speak(self, text: str, *, language: str | None = None, context: str = "") -> bytes:
        self.spoken.append((text, language))
        # her model is not ready: the voice service refuses the warm-up line (409), and no other voice says it
        raise VoiceNotSaidError("Pocket TTS could not say it")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Notes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def __call__(self, aci: str, text: str) -> None:
        self.sent.append((aci, text))


def follower(
    tmp_path: Path,
    voice: FakeVoice | None,
    *,
    in_use: set[str] | None = None,
    locked: list[bool] | None = None,
) -> tuple[LanguageDownload, Notes, Clock]:
    notes, clock = Notes(), Clock()
    used = in_use if in_use is not None else {"de"}
    lock = locked if locked is not None else [False]

    async def no_wait(seconds: float) -> None:
        await asyncio.sleep(0)

    found = LanguageDownload(
        voice,
        tmp_path / "voice-models",
        notes,
        in_use=lambda code: code in used,
        locked=lambda: lock[0],
        clock=clock,
        sleep=no_wait,
    )
    return found, notes, clock


def fetched(tmp_path: Path, language: str) -> None:
    """Every file of jane's model of `language` in the voice service's models folder (a fetch done before)."""
    for relative in POCKET_LANGUAGE_FILES[language]:
        path = tmp_path / "voice-models" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")


async def settled(found: LanguageDownload) -> None:
    """Every warm-up of the follower has run."""
    for _ in range(50):
        await asyncio.sleep(0)
    await found.idle()


DOWNLOADING = "Her German voice is downloading: about 3 minutes. Until then, her answers come as text."


def test_the_owners_copy() -> None:
    assert prompts.LANGUAGE_DOWNLOADING.format(language="German", minutes="3 minutes") == (
        "German is on. Her German voice is downloading: about 3 minutes. Until then, her answers come as text."
    )
    assert prompts.VOICE_DOWNLOADING.format(language="German", minutes="3 minutes") == DOWNLOADING
    assert prompts.VOICE_LANGUAGE_READY.format(language="German") == "Her German voice is ready."
    assert prompts.CALL_DOWNLOADING.format(language="German", minutes="3 minutes") == (
        "Her German voice is downloading, so calls are not picked up yet. Call again in about 3 minutes, after "
        '"Her German voice is ready."'
    )
    assert prompts.VOICE_DOWNLOAD_FAILED.format(language="German") == (
        "Her German voice could not be downloaded. Until a later try works, her answers in German come as text."
    )


async def test_a_language_whose_model_is_missing_downloads_and_the_reply_says_so(tmp_path: Path) -> None:
    voice = FakeVoice("idle")
    found, notes, _ = follower(tmp_path, voice)
    found.warm_up("de")
    reply = await found.reply("de", ME)
    assert reply == "German is on. " + DOWNLOADING
    assert found.downloading("de")
    await settled(found)
    # its one warm-up line starts the voice service's fetch; it is not said (no other voice says it)
    assert voice.spoken == [(prompts.CALL_SPOKEN["de"].now_speaking, "de")]
    assert notes.sent == []


async def test_a_model_already_there_is_no_download(tmp_path: Path) -> None:
    voice = FakeVoice("idle")
    fetched(tmp_path, "de")
    found, _, _ = follower(tmp_path, voice)
    found.warm_up("de")
    assert await found.reply("de", ME) == ""
    assert not found.downloading("de") and found.voice_note(ME, "de") == "" and found.call_note(ME, "de") == ""
    await settled(found)
    assert voice.spoken == [(prompts.CALL_SPOKEN["de"].now_speaking, "de")]  # the warm-up as before


@pytest.mark.parametrize(
    ("language", "pocket"),
    [
        ("ko", "idle"),  # Korean: no voice at all (owner, 3 Nov 2026), nothing to download
        ("de", None),  # a voice service without jane's German model: nothing to wait for
        ("de", "ready"),  # the voice service has it ready (its own folder)
        ("de", "unavailable"),  # a try that failed minutes ago: no voice in German until its next try
    ],
)
async def test_no_download_without_one_to_wait_for(tmp_path: Path, language: str, pocket: str | None) -> None:
    voice = FakeVoice(pocket, language=language)
    found, _, _ = follower(tmp_path, voice, in_use={language})
    found.warm_up(language)
    assert await found.reply(language, ME) == ""
    assert not found.downloading(language)
    await settled(found)


@pytest.mark.parametrize("language", ["ko", "pl", "ru", "ja"])
async def test_a_language_her_voice_does_not_speak_has_no_warm_up(tmp_path: Path, language: str) -> None:
    """Owner, 3 Nov 2026: Korean, Polish, Russian and Japanese have no voice: nothing is asked in them."""
    voice = FakeVoice("idle", language=language)
    found, notes, _ = follower(tmp_path, voice, in_use={language})
    found.warm_up(language)
    await settled(found)
    assert voice.spoken == [] and voice.reads == 0 and notes.sent == []


async def test_english_has_no_warm_up_and_no_download(tmp_path: Path) -> None:
    voice = FakeVoice("idle", language="en")
    found, _, _ = follower(tmp_path, voice, in_use={"en"})
    found.warm_up("en")
    assert await found.reply("en", ME) == "" and not found.downloading("en")
    await settled(found)
    assert voice.spoken == [] and voice.reads == 0


async def test_without_a_voice_nothing_downloads(tmp_path: Path) -> None:
    found, _, _ = follower(tmp_path, None)
    found.warm_up("de")
    assert await found.reply("de", ME) == "" and not found.downloading("de")


async def test_a_voice_service_not_answering_gives_the_usual_reply(tmp_path: Path) -> None:
    voice = FakeVoice("idle")
    voice.up = False
    found, _, _ = follower(tmp_path, voice)
    found.warm_up("de")
    assert await found.reply("de", ME) == ""
    await settled(found)


async def test_a_voice_note_and_a_call_during_the_download_get_the_minutes_left(tmp_path: Path) -> None:
    voice = FakeVoice("loading")
    found, _, clock = follower(tmp_path, voice)
    found.warm_up("de")
    await settled(found)
    assert found.voice_note(ME, "de") == DOWNLOADING
    assert found.call_note(ME, "de") == prompts.CALL_DOWNLOADING.format(language="German", minutes="3 minutes")
    clock.now += 70
    assert found.voice_note(ME, "de") == DOWNLOADING.replace("about 3 minutes", "about 2 minutes")
    clock.now += 80  # 150 s in
    assert found.voice_note(ME, "de") == DOWNLOADING.replace("about 3 minutes", "about 1 minute")
    assert "Call again in about 1 minute, after" in found.call_note(ME, "de")
    clock.now += 40  # past the 3 minutes: no time any more
    assert found.voice_note(ME, "de") == prompts.VOICE_STILL_DOWNLOADING.format(language="German")
    assert found.call_note(ME, "de") == prompts.CALL_STILL_DOWNLOADING.format(language="German")
    assert prompts.VOICE_STILL_DOWNLOADING.format(language="German") == (
        "Her German voice is still downloading. Until it is ready, her answers come as text."
    )
    assert prompts.CALL_STILL_DOWNLOADING.format(language="German") == (
        'Her German voice is still downloading, so calls are not picked up yet. Call again after "Her German voice '
        'is ready."'
    )
    assert found.voice_note(ME, "nl") == "" and found.call_note(ME, "nl") == ""  # not the language downloading


async def test_once_ready_each_account_told_gets_one_note(tmp_path: Path) -> None:
    voice = FakeVoice("loading")
    found, notes, _ = follower(tmp_path, voice)
    found.warm_up("de")
    await found.reply("de", ME)
    await settled(found)
    found.voice_note(OTHER, "de")
    found.call_note(ME, "de")  # told twice: one note
    await found.check()
    assert notes.sent == [] and found.downloading("de")
    voice.state["pocket_de"] = "ready"
    await found.check()
    ready = "Her German voice is ready."
    assert sorted(notes.sent) == sorted([(ME, ready), (OTHER, ready)])
    assert not found.downloading("de")
    await found.check()
    assert len(notes.sent) == 2


async def test_nobody_told_gets_no_ready_note(tmp_path: Path) -> None:
    """A download the bridge's start found (the language in use, its model missing): nothing said, nothing owed."""
    voice = FakeVoice("loading")
    found, notes, _ = follower(tmp_path, voice)
    found.warm_up("de")
    await settled(found)
    assert found.downloading("de")
    voice.state["pocket_de"] = "ready"
    await found.check()
    assert notes.sent == [] and not found.downloading("de")


@pytest.mark.parametrize("state", ["unavailable", "refused", None])
async def test_a_failed_download_says_so_then_her_answers_come_as_text_until_a_later_try_works(
    tmp_path: Path, state: str | None
) -> None:
    voice = FakeVoice("loading")
    found, notes, clock = follower(tmp_path, voice)
    found.warm_up("de")
    await found.reply("de", ME)
    await settled(found)
    if state is None:
        del voice.state["pocket_de"]  # a voice service without it now (an older one put back)
    else:
        voice.state["pocket_de"] = state
    await found.check()
    failed = prompts.VOICE_DOWNLOAD_FAILED.format(language="German")
    assert notes.sent == [(ME, failed)]
    # no voice in German until a later try works (no other voice says a line): her answers come as text, with no more
    # download notes; a call is picked up and Pocket TTS is tried at every line
    assert not found.downloading("de") and found.voice_note(ME, "de") == "" and found.call_note(ME, "de") == ""
    await found.check()
    assert notes.sent == [(ME, failed)]  # said once
    voice.state["pocket_de"] = "ready"  # a later try (a line in German after the voice service's wait) worked
    reads = voice.reads
    await found.check()
    assert voice.reads == reads  # after a failure, read only every FAILED_POLL_SECONDS
    clock.now += download_module.FAILED_POLL_SECONDS
    await found.check()
    assert notes.sent == [(ME, failed), (ME, "Her German voice is ready.")]
    await found.check()
    assert len(notes.sent) == 2


async def test_a_language_no_longer_in_use_ends_its_download_without_a_note(tmp_path: Path) -> None:
    voice = FakeVoice("loading")
    used = {"de"}
    found, notes, _ = follower(tmp_path, voice, in_use=used)
    found.warm_up("de")
    await found.reply("de", ME)
    await settled(found)
    used.clear()  # /language en
    await found.check()
    voice.state["pocket_de"] = "ready"
    await found.check()
    assert notes.sent == [] and not found.downloading("de")


async def test_a_voice_service_that_started_again_is_asked_for_a_line_again(tmp_path: Path) -> None:
    """A restart of the voice service mid-download leaves the language idle: its fetch starts again at a line in it."""
    voice = FakeVoice("loading")
    found, _, _ = follower(tmp_path, voice)
    found.warm_up("de")
    await settled(found)
    assert len(voice.spoken) == 1
    voice.up = False
    await found.check()  # not answering meanwhile: nothing asked
    voice.up = True
    voice.state["pocket_de"] = "idle"
    await found.check()
    assert voice.spoken == [(prompts.CALL_SPOKEN["de"].now_speaking, "de")] * 2
    assert found.downloading("de")


async def test_a_call_wakes_an_idle_model_before_it_is_picked_up(tmp_path: Path) -> None:
    """The review of 4 Nov: after the voice service restarted on its own, jane's model of the language in use is idle
    and nothing warmed it up again, so a German call was picked up and its first answer went as text. A ring wakes it:
    the warm-up's line at once (it loads while the call rings); files missing make a download, and the call rings out
    with its note."""
    voice = FakeVoice("idle")
    fetched(tmp_path, "de")
    found, _, _ = follower(tmp_path, voice)
    await found.wake("de", dict(voice.state))
    await settled(found)
    assert voice.spoken == [(prompts.CALL_SPOKEN["de"].now_speaking, "de")] and not found.downloading("de")
    missing = FakeVoice("idle")
    other, _, _ = follower(tmp_path / "other", missing)
    await other.wake("de", dict(missing.state))
    assert other.downloading("de") and other.call_note(ME, "de").startswith("Her German voice is downloading")
    await settled(other)
    for language, state in (("de", "ready"), ("de", "loading"), ("en", "idle"), ("ko", "idle")):
        quiet = FakeVoice(state, language=language)
        nothing, _, _ = follower(tmp_path / f"{language}-{state}", quiet, in_use={language})
        await nothing.wake(language, dict(quiet.state))
        await settled(nothing)
        assert quiet.spoken == [], (language, state)
    await found.wake("de", None)  # the voice service did not answer: nothing asked
    await settled(found)
    assert len(voice.spoken) == 1


async def test_an_idle_model_of_the_language_in_use_is_warmed_up_again(tmp_path: Path) -> None:
    """The review of 4 Nov: a voice note after the voice service restarted on its own (or after a language model
    rested after failed runs) found jane's model idle, and its answer went as text. The follower reads the health at
    most once a minute while one of the six languages is in use with no download under way, and warms an idle model
    up; a minute after its start at the earliest (the bridge's start warms it up itself)."""
    voice = FakeVoice("idle")
    fetched(tmp_path, "de")
    found, _, clock = follower(tmp_path, voice)
    await found.check()
    assert voice.reads == 0 and voice.spoken == []
    clock.now += download_module.IDLE_POLL_SECONDS
    await found.check()
    await settled(found)
    assert voice.spoken == [(prompts.CALL_SPOKEN["de"].now_speaking, "de")] and not found.downloading("de")
    reads = voice.reads
    await found.check()
    assert voice.reads == reads  # once a minute at most
    voice.state["pocket_de"] = "ready"
    clock.now += download_module.IDLE_POLL_SECONDS
    await found.check()
    await settled(found)
    assert len(voice.spoken) == 1
    english = FakeVoice("idle", language="en")
    none, _, english_clock = follower(tmp_path / "en", english, in_use={"en"})
    english_clock.now += download_module.IDLE_POLL_SECONDS
    await none.check()
    assert english.reads == 0  # English and the four languages without a voice: no read at all


async def test_notes_wait_while_locked(tmp_path: Path) -> None:
    voice = FakeVoice("loading")
    locked = [False]
    found, notes, _ = follower(tmp_path, voice, locked=locked)
    found.warm_up("de")
    await found.reply("de", ME)
    await settled(found)
    voice.state["pocket_de"] = "ready"
    locked[0] = True
    await found.check()
    assert notes.sent == []
    locked[0] = False
    await found.check()
    assert notes.sent == [(ME, "Her German voice is ready.")]


async def test_a_second_switch_to_the_language_keeps_the_download_and_its_accounts(tmp_path: Path) -> None:
    voice = FakeVoice("loading")
    found, notes, _ = follower(tmp_path, voice)
    found.warm_up("de")
    await found.reply("de", ME)
    await settled(found)
    found.warm_up("de")  # /babble de while /language de downloads
    assert await found.note("de", OTHER) == DOWNLOADING
    await settled(found)
    voice.state["pocket_de"] = "ready"
    await found.check()
    assert sorted(aci for aci, _ in notes.sent) == sorted([ME, OTHER])


async def test_the_practice_reply_line(tmp_path: Path) -> None:
    voice = FakeVoice("idle", language="nl")
    found, _, _ = follower(tmp_path, voice, in_use={"nl"})
    found.warm_up("nl")
    assert await found.note("nl", ME) == (
        "Her Dutch voice is downloading: about 3 minutes. Until then, her answers come as text."
    )
    fetched(tmp_path, "it")
    found.warm_up("it")
    assert await found.note("it", ME) == ""


async def test_the_follower_runs_until_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    voice = FakeVoice("loading")
    found, notes, _ = follower(tmp_path, voice)
    found.warm_up("de")
    await found.reply("de", ME)
    await settled(found)
    voice.state["pocket_de"] = "ready"
    task = asyncio.create_task(found.run_forever())
    try:
        async with asyncio.timeout(5):
            while not notes.sent:
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
    assert notes.sent == [(ME, "Her German voice is ready.")]

import array
import asyncio
import io
import itertools
import logging
import math
import random
import re
import shutil
import tempfile
import time
import wave
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

import pytest

from local_assistant import calls, prompts
from local_assistant.call_audio import FRAME_BYTES, RATE, Barge, CallAudio, Said, TurnEnd, rms
from local_assistant.calls import (
    MAX_FALSE_BARGE,
    CallManager,
    CallSettings,
    Turn,
    asks,
    goes_on,
    is_own_voice,
    is_wrap_up,
    played_part,
    says_goodbye,
    wants_hang_up,
)
from local_assistant.config import DEFAULT_NAME, POCKET_LANGUAGES, Config
from local_assistant.language import LANGUAGES
from local_assistant.voice import SpokenClip, Transcript, VoiceNoStandInError, VoiceUnavailableError, sentences
from tests.conftest import ME, STRANGER
from tests.fakes.fake_voice import speech_probability
from tests.test_call_audio import typing

CALL = -7  # signal-cli's call ids are signed
NAME = DEFAULT_NAME  # her name in these calls
GREETING = prompts.call_greetings(NAME)[0]  # the one known greeting the harness gives her


def tone(seconds: float, level: int = 3000, hz: float = 220.0) -> bytes:
    n = int(RATE * seconds)
    return array.array("h", (int(level * math.sin(2 * math.pi * hz * i / RATE)) for i in range(n))).tobytes()


def noise(seconds: float, level: int = 20) -> bytes:
    rnd = random.Random(3)
    return array.array("h", (rnd.randint(-level, level) for _ in range(int(RATE * seconds)))).tobytes()


def speech(seconds: float, level: int = 3000) -> bytes:
    out = bytearray()
    syllable = 0
    while len(out) < 2 * RATE * seconds:
        out += tone(0.15, level, hz=200.0 + 37 * syllable) + noise(0.05)
        syllable += 1
    return bytes(out[: int(2 * RATE * seconds)])


def wav24(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        w.writeframes(array.array("h", (int(3000 * math.sin(i / 7)) for i in range(int(24_000 * seconds)))).tobytes())
    return buf.getvalue()


# what VoiceCore's cleanTranscript erases from a transcription that is not a call's turn (no ?call=1): a caller's
# bare thanks or bye (review I23: they never ended the call)
SERVICE_ERASES = frozenset({"thank you", "thank you.", "thanks.", "bye."})


class FakeVoice:
    def __init__(self, transcripts: list[str], ready: bool = True) -> None:
        self.transcripts = transcripts
        self.ready = ready
        self.detector: str | None = None  # the speech detector's state in the health; None: an older service
        self.chunks: list[tuple[str, int]] = []  # (stream, bytes) of each chunk the detector was asked about
        self.turn_detector: str | None = None  # the turn detector's state in the health; None: an older service
        self.turn_p: list[float | str] = []  # Smart Turn's answers in order (then 0.9); a str is a failure's kind
        self.windows: list[int] = []  # the bytes of each window Smart Turn was asked about
        self.spoken: list[str] = []
        self.voice = "eve"  # her voice (/voice <voice> sets the clients')
        self.language_voice = "jane"  # the voice of the six languages Pocket TTS speaks (`[voice] language_voice`)
        self.voices: list[str] = []  # the voice each spoken text was asked in
        self.stand_in: set[str] = set()  # texts another voice says in hers' place (Pocket TTS not ready)
        # whether Kokoro may stand in for each spoken text (`kokoro=False`: no Kokoro asked for, `_KokoroTurn`)
        self.kokoro_flags: list[bool] = []
        self.ignores_kokoro = False  # an older voice service: Kokoro stands in whatever the request says
        self.contexts: list[str] = []  # what she said before each spoken text ("" for none)
        self.spoken_on_calls: list[bool] = []  # whether each spoken text was asked as a call's line (`call=True`)
        self.hints: list[str] = []  # the hint of each transcription ("" for none)
        self.call_flags: list[bool] = []  # whether each transcription was a call's turn (?call=1)
        self.languages: list[str | None] = []  # the language of each spoken text
        self.heard_in: list[str | None] = []  # the language of each transcription
        self.heard_s: list[float] = []  # the seconds of audio in each transcription
        self.heard = 0
        self.fail_transcribe = False
        self.broken: set[str] = set()
        self.slow: dict[str, asyncio.Event] = {}  # a text whose speech is made only once its event is set
        self.health_gate: asyncio.Event | None = None  # when set, a health read waits for it (a slow voice service)

    async def transcribe(
        self, audio: bytes, *, language: str | None = None, hint: str = "", call: bool = False
    ) -> Transcript:
        self.heard += 1
        self.heard_in.append(language)
        self.hints.append(hint)
        self.call_flags.append(call)
        self.heard_s.append((len(audio) - 44) / 32_000)  # a 16 kHz mono 16-bit WAV
        if self.fail_transcribe:
            raise VoiceUnavailableError("down")
        text = self.transcripts.pop(0) if self.transcripts else ""
        # as the real voice service: without the call flag a bare thanks or bye is erased (VoiceCore cleanTranscript)
        return Transcript("" if not call and text.lower() in SERVICE_ERASES else text, 1.0)

    async def speak(
        self, text: str, *, language: str | None = None, context: str = "", call: bool = False, kokoro: bool = True
    ) -> bytes:
        self.spoken.append(text)
        self.spoken_on_calls.append(call)
        self.kokoro_flags.append(kokoro)
        self.voices.append(self.voice_for(language or "en"))
        self.languages.append(language)
        self.contexts.append(context)
        await asyncio.sleep(0)  # a request to the voice service: other tasks (the answer) run meanwhile
        if text in self.slow:
            await self.slow[text].wait()
        english = (language or "en") == "en"
        if text in self.stand_in and english and not kokoro and not self.ignores_kokoro:
            raise VoiceNoStandInError(
                "Pocket TTS did not say it, and Kokoro was not asked"
            )  # as the voice service's 409
        if text in self.broken:
            return b"not a wav at all"
        clip = SpokenClip(wav24(min(0.03 * len(text), 8.0)))
        clip.stand_in = ("af_heart" if (language or "en") == "en" else "F1") if text in self.stand_in else ""
        return clip

    def voice_for(self, language: str) -> str:  # as VoiceClient's: "" for none, her English voice everywhere
        return self.language_voice if self.language_voice and language in POCKET_LANGUAGES else self.voice

    async def health(self) -> dict[str, str] | None:
        if self.health_gate is not None:
            await self.health_gate.wait()
        health = {"stt": "ready", "tts": "ready"} if self.ready else {"stt": "loading", "tts": "loading"}
        health |= {"vad": self.detector} if self.detector is not None else {}
        return health | ({"turn": self.turn_detector} if self.turn_detector is not None else {})

    async def vad(self, chunk: bytes, *, stream: str) -> float | str:
        self.chunks.append((stream, len(chunk)))
        return speech_probability(chunk) if self.detector == "ready" else "http"

    async def turn(self, window: bytes) -> float | str:
        self.windows.append(len(window))
        if self.turn_detector != "ready":
            return "http"
        return self.turn_p.pop(0) if self.turn_p else 0.9


class FakeHost:
    def __init__(self, turns: list[Turn], delay: float = 0.0, use_tools: bool = False) -> None:
        self.answers = turns
        self.delay = delay
        self.use_tools = use_tools
        self.turns: list[str] = []
        self.notes: list[str] = []
        self.texts: list[tuple[str, str, int | None, bool]] = []  # code's texts on a call: aci, text, reply, outside
        self.summaries: list[tuple[list[str], int]] = []
        self.interruptions: list[tuple[int, str]] = []
        self.started: list[str] = []
        self.started_practice: list[bool] = []  # per call: whether it started as a practice call
        self.ended = 0
        self.dropped_waiting = 0
        self.standbys: list[str] = []  # standby started (the caller quiet): a waiting yes and her offer dropped
        self.cancelled_turns = 0
        self.gate: asyncio.Event | None = None  # when set, an answer also waits for it
        self.practice_ended: list[str] = []
        self.practice_turns: list[bool] = []  # per turn: whether the call said it is a practice call
        self.warm_ups: list[tuple[str, bool]] = []  # per warm-up: (aci, practice)
        self.warm_gate: asyncio.Event | None = None  # when set, a warm-up waits for it (a slow model)
        self.warm_error: Exception | None = None  # when set, a warm-up raises it
        self.warm_cancelled = 0
        self.warm_done = 0
        self.empty_first: set[int] = set()  # the turns (0, 1, ...) whose first try comes back empty: retried
        self.retry_gate: asyncio.Event | None = None  # when set, a retry waits for it

    def call_started(self, aci: str, *, practice: bool = False) -> None:
        self.started.append(aci)
        self.started_practice.append(practice)

    async def call_warm_up(self, aci: str, *, practice: bool = False) -> None:
        self.warm_ups.append((aci, practice))
        try:
            if self.warm_gate is not None:
                await self.warm_gate.wait()
        except asyncio.CancelledError:
            self.warm_cancelled += 1
            raise
        if self.warm_error is not None:
            raise self.warm_error
        self.warm_done += 1

    def call_ended(self) -> None:
        self.ended += 1

    async def call_turn(
        self,
        aci: str,
        text: str,
        on_tools: Callable[[], None],
        *,
        practice: bool = False,
        on_retry: Callable[[], None] | None = None,
    ) -> Turn:
        self.turns.append(text)
        self.practice_turns.append(practice)
        if self.use_tools:
            on_tools()
        try:
            await asyncio.sleep(self.delay)
            if len(self.turns) - 1 in self.empty_first and on_retry is not None:  # the first try came back empty
                on_retry()
                await asyncio.sleep(0.3)  # the retry takes a moment (2 to 10 s live)
                if self.retry_gate is not None:
                    await self.retry_gate.wait()  # the retry, still running
            if self.gate is not None:
                await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled_turns += 1
            raise
        return self.answers.pop(0) if self.answers else Turn("Okay.")

    async def call_drop_waiting(self, aci: str) -> None:
        self.dropped_waiting += 1

    async def call_standby(self, aci: str) -> None:
        self.standbys.append(aci)

    def call_interrupted(self, reply_id: int, heard: str) -> None:
        self.interruptions.append((reply_id, heard))

    async def call_note(self, aci: str, text: str) -> None:
        self.notes.append(text)

    async def call_text(self, aci: str, text: str, *, reply_id: int | None, outside: bool) -> None:
        self.texts.append((aci, text, reply_id, outside))

    async def call_summary(self, aci: str, lines: list[str], minutes: int) -> None:
        self.summaries.append((lines, minutes))
        self.notes.append("(summary)")  # in order with the notes

    async def call_chords(self, aci: str) -> None:
        self.notes.append("(chords)")

    async def practice_call_ended(self, aci: str) -> None:
        self.practice_ended.append(aci)
        self.notes.append("(practice ended)")  # in order with the notes


class FakeHelper:
    """The call helper's socket in lock step, faster than real time: one caller frame out, one of her frames
    back (timers that count call time run fast; the idle tests rely on it). The caller is quiet until a test queues
    speech."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.queued = bytearray()
        self.heard: list[bytes] = []
        self.sent: list[bytes] = []  # the caller's frames, in step with `heard` (hers)
        self.server: asyncio.Server | None = None
        self.stop = False
        self.triggers: list[tuple[Callable[[], bool], bytes]] = []

    async def start(self) -> None:
        self.server = await asyncio.start_unix_server(self._serve, path=str(self.path))

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        quiet = noise(0.01)
        try:
            while not self.stop:
                for trigger in list(self.triggers):
                    if trigger[0]():
                        self.triggers.remove(trigger)
                        self.queued += trigger[1]
                frame = bytes(self.queued[:FRAME_BYTES]) if self.queued else quiet
                del self.queued[:FRAME_BYTES]
                self.sent.append(frame)
                writer.write(frame.ljust(FRAME_BYTES, b"\0"))
                await writer.drain()
                self.heard.append(await reader.readexactly(FRAME_BYTES))
                await asyncio.sleep(0)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        writer.close()

    def caller_says(self, pcm: bytes) -> None:
        self.queued += pcm

    def caller_says_when(self, condition: Callable[[], bool], pcm: bytes) -> None:
        """Frame-exact: the caller starts `pcm` at the first frame where `condition` holds."""
        self.triggers.append((condition, pcm))

    def caller_says_after(self, condition: Callable[[], bool], frames: int, pcm: bytes) -> None:
        """Frame-exact: the caller starts `pcm` `frames` frames after the first frame where `condition` holds."""
        mark: list[int] = []

        def due() -> bool:
            if not mark and condition():
                mark.append(len(self.heard))
            return bool(mark) and len(self.heard) >= mark[0] + frames

        self.triggers.append((due, pcm))

    def her_frames(self) -> int:
        return sum(1 for frame in self.heard if rms(frame) > 300)


OUT = 77  # the id signal-cli gives a call she places


class FakeSignal:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.accepted: list[tuple[str, int]] = []
        self.hung_up: list[tuple[str, int]] = []
        self.started: list[str] = []
        self.start_error: Exception | None = None
        self.helper: FakeHelper | None = None
        self.hangup_fails: list[Exception | None] = []  # per hang-up: an error, or None for one with no ENDED after it

    async def start_call(self, aci: str) -> dict[str, Any]:
        """As signal-cli 0.14.9 does: the ringing event comes before startCall's reply (the call id is not known yet
        when it is read)."""
        self.started.append(aci)
        if self.start_error is not None:
            raise self.start_error
        self.events.put_nowait({"callId": OUT, "state": "RINGING_OUTGOING", "uuid": aci, "isOutgoing": True})
        await asyncio.sleep(0.05)
        return {"callId": OUT, "state": "RINGING_OUTGOING"}

    async def pick_up(self, call_id: int = OUT) -> None:
        """The user answers the call she placed."""
        path = self.folder / f"signal-call-{call_id & 0xFFFFFFFFFFFFFFFF}.sock"
        self.helper = FakeHelper(path)
        await self.helper.start()
        self.events.put_nowait({"callId": call_id, "state": "CONNECTING", "isOutgoing": True})
        self.events.put_nowait(
            {"callId": call_id, "state": "CONNECTED", "isOutgoing": True, "inputDeviceName": f"unix:{path}"}
        )

    def end_ringing(self, reason: str, call_id: int | None = OUT) -> None:
        event: dict[str, Any] = {"state": "ENDED", "reason": reason}
        if call_id is not None:
            event |= {"callId": call_id, "isOutgoing": True}
        self.events.put_nowait(event)

    async def call_events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self.events.get()

    async def accept_call(self, aci: str, call_id: int) -> dict[str, Any]:
        self.accepted.append((aci, call_id))
        path = self.folder / f"signal-call-{call_id & 0xFFFFFFFFFFFFFFFF}.sock"
        self.helper = FakeHelper(path)
        await self.helper.start()
        self.events.put_nowait({"callId": call_id, "state": "CONNECTED", "inputDeviceName": f"unix:{path}"})
        return {"callId": call_id, "state": "CONNECTING", "inputDeviceName": f"unix:{path}"}

    async def hangup_call(self, aci: str, call_id: int) -> None:
        self.hung_up.append((aci, call_id))
        if self.hangup_fails:
            error = self.hangup_fails.pop(0)
            if error is not None:
                raise error
            return  # signal-cli took it, and no ENDED came
        self.events.put_nowait({"callId": call_id, "state": "ENDED", "reason": "local_hangup"})
        if self.helper:
            self.helper.stop = True

    def ring(self, aci: str = ME, call_id: int = CALL) -> None:
        self.events.put_nowait({"callId": call_id, "state": "RINGING_INCOMING", "uuid": aci, "isOutgoing": False})

    def remote_hangup(self, call_id: int = CALL) -> None:
        self.events.put_nowait({"callId": call_id, "state": "ENDED", "reason": "remote_hangup"})
        if self.helper:
            self.helper.stop = True


class Harness:
    def __init__(
        self,
        transcripts: list[str] | None = None,
        turns: list[Turn] | None = None,
        *,
        delay: float = 0.0,
        use_tools: bool = False,
        ready: bool = True,
        language: str = "en",
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        name: str = NAME,
        practice: Callable[[str], str | None] | None = None,
        locked: Callable[[], bool] = lambda: False,
        missed: Callable[[str, bool], None] | None = None,
        reached: Callable[[str], None] | None = None,
        downloads: Any = None,
        **settings: Any,
    ) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="c", dir="/tmp"))  # short: socket paths are at most 104 bytes
        self.signal = FakeSignal(self.folder)
        self.voice = FakeVoice(transcripts or [], ready)
        self.host = FakeHost(turns or [], delay, use_tools)
        # idle timers and the call's length cap get long defaults here, except in the tests about them: call time
        # is counted in frames, and the lock-step helper exchanges frames as fast as it can while Gemma "thinks"
        # (an hour of call time can pass in a few real seconds on a fast machine)
        options: dict[str, Any] = {
            "end_of_turn_s": 0.6,
            "thinking_after_s": 0.5,
            "idle_prompt_s": 3000.0,
            "max_s": 1_000_000.0,
            "answer_after_s": 0.0,  # answered at once, except in the test about ringing
            "fillers": True,  # the tests about thinking lines; off in the real default
            "greetings": (GREETING,),  # one known greeting (the real default varies)
            "acknowledge": False,  # the tests about the short "okay" turn it on; on in the real default
            "barge": "loudness",  # any sound: the tests about speech say so ("only speech" is the real default)
            # today's end of turn: the full pause of quiet (the tests about the early end and the pause by speech say
            # so; both are on in the real default)
            "early_end": False,
            "pause": "loudness",
        }
        options.update(settings)
        self.manager = CallManager(
            self.signal,
            self.voice,
            self.host,
            frozenset({ME}),
            CallSettings(socket_dir=self.folder, **options),
            language=language,
            rng=rng,
            clock=clock,
            name=name,
            practice=practice or (lambda aci: None),
            locked=locked,
            missed=missed or (lambda aci, promised: None),
            reached=reached or (lambda aci: None),
            downloads=downloads,
        )
        spoken_in = (practice(ME) if practice is not None else None) or language
        english = (options["greetings"] or (GREETING,))[0]  # one greeting set: the one she says
        self.greeting = english if spoken_in == "en" else prompts.spoken(spoken_in, name=name).greeting
        self.task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "Harness":
        self.task = asyncio.create_task(self.manager.run_forever())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.task:
            self.task.cancel()
        shutil.rmtree(self.folder, ignore_errors=True)

    @property
    def helper(self) -> FakeHelper:
        assert self.signal.helper is not None
        return self.signal.helper

    async def until(self, condition: Callable[[], bool], limit: float = 20.0) -> None:
        async with asyncio.timeout(limit):
            while not condition():
                await asyncio.sleep(0.01)

    async def connected(self) -> None:
        self.signal.ring()
        await self.until(lambda: self.signal.helper is not None and len(self.helper.heard) > 0)
        await self.until(lambda: self.greeting in self.voice.spoken)  # synthesised (maybe still playing)

    async def quiet_after(self, text: str) -> None:
        """Wait until she finished saying `text` (a whole line or one sentence of an answer)."""
        await self.until(lambda: text in self.manager.said)


async def test_the_owners_call_is_answered_greeted_and_a_turn_is_spoken() -> None:
    async with Harness(["what's on tomorrow"], [Turn("Two meetings tomorrow.", reply_id=7)]) as h:
        await h.connected()
        assert h.signal.accepted == [(ME, CALL)] and h.host.started == [ME]
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Two meetings tomorrow.")
        assert h.host.turns == ["what's on tomorrow"]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        assert h.host.summaries == []  # one turn: nothing to summarise
        assert not h.manager.in_call


async def test_every_line_of_a_call_is_asked_as_a_calls() -> None:
    """The review of 28 Oct (calls-03): the voice service tries a Pocket TTS voice that rests after failed runs again
    for a call's line, so Kokoro, which an Apple bug can crash with the call, says fewer lines. Her canned lines and
    her answer's sentences go as a call's."""
    async with Harness(["what's on tomorrow"], [Turn("Two meetings. One at nine.", reply_id=7)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("One at nine.")
        h.signal.remote_hangup()
    assert h.voice.spoken[:3] == [GREETING, "Two meetings.", "One at nine."]
    assert h.voice.spoken_on_calls and all(h.voice.spoken_on_calls)


async def test_each_sentence_of_her_answer_goes_with_the_ones_she_said_before_it() -> None:
    """The Pocket TTS spike, 24 Oct: "Shall I remind you at 7:30?" two sentences after "closes at 8 tonight" was said
    "in the morning": a call speaks sentence by sentence, so each goes with her answer's sentences before it (the
    voice service reads them only for a time's part of the day); code's canned lines and the first sentence with none."""
    answer = "The pharmacy closes at 8 tonight. It's a short walk. Shall I remind you at 7:30?"
    async with Harness(["when does it close"], [Turn(answer, reply_id=7)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Shall I remind you at 7:30?")
        said = dict(zip(h.voice.spoken, h.voice.contexts, strict=True))
        assert said[GREETING] == ""
        assert said["The pharmacy closes at 8 tonight."] == ""
        assert said["It's a short walk."] == "The pharmacy closes at 8 tonight."
        assert said["Shall I remind you at 7:30?"] == "The pharmacy closes at 8 tonight. It's a short walk."
        h.signal.remote_hangup()


async def test_a_canned_line_is_kept_in_its_voice_and_never_a_stand_ins() -> None:
    """The review of 24 Oct: canned lines were kept by (language, text) alone, so after `/voice af_heart` the greeting
    stayed eve's until the bridge restarted, and one Kokoro said in eve's place (Pocket TTS still fetching after a
    deploy, or resting) stayed af_heart's on every later call. A line is kept in its voice, and one the voice service
    marks as said by another voice (X-Voice-Stand-In) is never kept."""

    async def one_call(number: int) -> None:
        h.signal.ring(call_id=CALL - number)
        await h.until(lambda: h.manager.said.count(GREETING) == number)
        h.signal.remote_hangup(call_id=CALL - number)
        await h.until(lambda: h.host.ended == number)

    async with Harness() as h:
        h.voice.stand_in = {GREETING}  # Pocket TTS not ready yet: Kokoro's af_heart says it
        await one_call(1)
        h.voice.stand_in = set()
        await one_call(2)  # eve's own, asked again
        await one_call(3)  # and kept
        h.voice.voice = "af_heart"  # /voice af_heart: at once
        await one_call(4)
        await one_call(5)
    asked = [voice for text, voice in zip(h.voice.spoken, h.voice.voices, strict=True) if text == GREETING]
    assert asked == ["eve", "eve", "af_heart"]


async def test_a_canned_line_in_janes_language_is_kept_in_hers_and_never_f1s() -> None:
    """Owner, 25 Oct: German is jane's (Pocket TTS's German model). A German canned line is kept in the voice that says
    it, jane's, so `/voice af_heart` (the English voice) keeps it; one Supertonic-3's F1 said in her place (her German
    model still fetching: X-Voice-Stand-In) is never kept, so the next call asks again."""

    greeting = prompts.call_lines("de", name=NAME).greetings[0]

    async def one_call(number: int) -> None:
        h.signal.ring(call_id=CALL - number)
        await h.until(lambda: h.manager.said.count(greeting) == number)
        h.signal.remote_hangup(call_id=CALL - number)
        await h.until(lambda: h.host.ended == number)

    async with Harness(language="de") as h:
        h.voice.stand_in = {greeting}  # jane's German model not fetched yet: F1 says it
        await one_call(1)
        h.voice.stand_in = set()
        await one_call(2)  # jane's own, asked again
        await one_call(3)  # and kept
        h.voice.voice = "af_heart"  # /voice af_heart: the English voice only
        await one_call(4)  # still jane's German line, kept
    asked = [voice for text, voice in zip(h.voice.spoken, h.voice.voices, strict=True) if text == greeting]
    assert asked == ["jane", "jane"]


# ------------------------------------------------------------------ Kokoro's one line a turn (owner, 28 Oct, calls-03)

LEAD = prompts.CALL_VOICE_STOPPED  # "My voice stopped working on this call. The rest of my answer:"


async def test_a_turns_second_kokoro_line_is_never_said_and_the_rest_goes_as_a_text() -> None:
    """Owner, 28 Oct ("great stuff, do it"; the review of 28 Oct, calls-03: Kokoro, the English stand-in for a Pocket
    voice that failed or rests, can crash the voice service and the call with it): on a call Kokoro says at most one
    line of a turn. Its first is said; each later line of the turn asks for no Kokoro ("kokoro": false), and the first
    Pocket TTS cannot say either stops her for the rest of the turn: the rest of her answer, whole, goes to the caller
    as a text, led by code's line. Her stored reply keeps what was heard; the call stays up."""
    async with Harness(["tell me three things"], [Turn("First point. Second point. Third point.", reply_id=7)]) as h:
        h.voice.stand_in = {"First point.", "Second point.", "Third point."}  # Pocket TTS resting: Kokoro says each
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(h.host.texts))
        assert h.host.texts == [(ME, f"{LEAD}\nSecond point. Third point.", 7, False)]  # the caller's only
        assert "First point." in h.manager.said and "Second point." not in h.manager.said
        asked = dict(zip(h.voice.spoken, h.voice.kokoro_flags, strict=True))
        assert asked == {GREETING: True, "First point.": True, "Second point.": False}  # the third never asked
        assert h.host.interruptions == [(7, "First point.")]  # her stored reply: what was heard
        await asyncio.sleep(0.3)
        assert h.signal.hung_up == [] and h.manager.in_call and not h.host.dropped_waiting  # the call stays up
        h.signal.remote_hangup()


async def test_one_kokoro_line_in_a_turn_is_said_and_nothing_goes_as_a_text() -> None:
    """A turn's one Kokoro line is said, first or later in it; the lines Pocket TTS says count for nothing, also when
    they are asked with no Kokoro after it."""
    turns = [Turn("Only this.", reply_id=7), Turn("Pocket first. Kokoro then. Pocket again.", reply_id=8)]
    async with Harness(["one", "two"], turns) as h:
        h.voice.stand_in = {"Only this.", "Kokoro then."}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Only this.")
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Pocket again.")
        assert h.host.texts == [] and h.host.interruptions == []
        asked = dict(zip(h.voice.spoken, h.voice.kokoro_flags, strict=True))
        assert asked["Kokoro then."] is True and asked["Pocket again."] is False
        h.signal.remote_hangup()


async def test_after_a_turn_whose_voice_stopped_the_next_turn_tries_pocket_and_kokoro_again() -> None:
    """The cap is a turn's: the next turn asks its lines as before (Pocket TTS is tried at every line), and Kokoro may
    say one line of it again."""
    turns = [Turn("One. Two.", reply_id=7), Turn("Three. Four.", reply_id=8)]
    async with Harness(["first", "second"], turns) as h:
        h.voice.stand_in = {"One.", "Two.", "Three."}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: len(h.host.texts) == 1)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Four.")
        assert "Three." in h.manager.said and h.host.texts == [(ME, f"{LEAD}\nTwo.", 7, False)]
        asked = dict(zip(h.voice.spoken, h.voice.kokoro_flags, strict=True))
        assert asked["Three."] is True and asked["Four."] is False
        h.signal.remote_hangup()


async def test_with_a_voice_service_that_ignores_the_ask_a_second_kokoro_line_is_still_never_said() -> None:
    """An older voice service reads no "kokoro" field and Kokoro says the line all the same (X-Voice-Stand-In): it is
    never played, nothing more of the turn is asked, and the rest goes as a text, that line too."""
    async with Harness(["go on"], [Turn("Alpha. Beta. Gamma.", reply_id=7)]) as h:
        h.voice.stand_in = {"Alpha.", "Beta.", "Gamma."}
        h.voice.ignores_kokoro = True
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(h.host.texts))
        assert h.host.texts == [(ME, f"{LEAD}\nBeta. Gamma.", 7, False)]
        assert "Beta." not in h.manager.said and "Gamma." not in h.voice.spoken
        assert h.host.interruptions == [(7, "Alpha.")]
        h.signal.remote_hangup()


async def test_the_text_holds_what_was_not_said_once_never_a_line_texted_on_its_own() -> None:
    """Nothing lost or doubled: the text holds the parts not said, her words and code's lines her reply keeps; code's
    lines that go as texts of their own (a reminder set, a device's result, the lookup line: `Turn.also_texted`) stay
    out of it."""
    turn = Turn(
        "Sure. It is cold today.",
        reply_id=7,
        code_text="Reminder set for 17:00.",
        also_texted=("Reminder set for 17:00.",),
    )
    async with Harness(["remind me at five"], [turn]) as h:
        h.voice.stand_in = {"Sure.", "It is cold today.", "Reminder set for 17:00."}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(h.host.texts))
        assert h.host.texts == [(ME, f"{LEAD}\nIt is cold today.", 7, False)]
        assert h.host.interruptions == [(7, "Sure.")]
        assert h.signal.hung_up == []
        h.signal.remote_hangup()


DEVICE_QUESTION = "Shall I switch the heater on?"


def device_turn() -> Turn:
    """Her answer to a device request that waits for the yes: her words, a reminder's line texted on its own, then
    code's question, last."""
    return Turn(
        "Sure. It is cold today.",
        reply_id=7,
        asks_yes=True,
        code_text=f"Reminder set for 17:00. {DEVICE_QUESTION}",
        kept_code=(DEVICE_QUESTION,),
        also_texted=("Reminder set for 17:00.",),
    )


async def test_a_device_question_keeps_the_turns_kokoro_line_and_its_yes_counts() -> None:
    """The review of 28 Oct (words-01, pipeline-04): a device question goes unheard when her first sentence spends the
    turn's one Kokoro line, and the yes is dropped; while Pocket TTS stays down, asking again goes the same way. So on
    a turn that waits for a yes her words ask for no Kokoro: the first Pocket TTS cannot say sends her words as the
    text, and code's question is the turn's one Kokoro line. The question is heard, kept in her reply, never texted,
    and the caller's yes reaches the waiting device."""
    turns = [device_turn(), Turn("", code_text="Devices: heater on.")]
    async with Harness(["remind me and warm it up", "yes"], turns) as h:
        h.voice.stand_in = {"Sure.", "It is cold today.", "Reminder set for 17:00.", DEVICE_QUESTION}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after(DEVICE_QUESTION)
        assert h.host.texts == [(ME, f"{LEAD}\nSure. It is cold today.", 7, False)]
        assert "Sure." not in h.manager.said
        asked = list(zip(h.voice.spoken, h.voice.kokoro_flags, strict=True))[1:]  # the greeting apart
        assert asked == [("Sure.", False), (DEVICE_QUESTION, True)]  # one Kokoro line: the question
        assert h.host.interruptions == [(7, DEVICE_QUESTION)]  # her stored reply: what was heard
        assert h.host.dropped_waiting == 0
        h.helper.caller_says(speech(0.4))
        await h.quiet_after("Devices: heater on.")
        assert h.host.turns == ["remind me and warm it up", "yes"] and h.host.dropped_waiting == 0
        h.signal.remote_hangup()


async def test_a_device_question_her_voice_cannot_say_is_never_texted_and_its_yes_is_dropped() -> None:
    """An older voice service (no "kokoro" field read): Kokoro said her "Got it.", so the question would need it a
    second time and is not said. Its yes is dropped (never heard: no yes may act on it), and the text never holds the
    question: a yes to it could switch nothing."""
    said = "remind me at five and warm up the room"
    async with Harness([said], [device_turn()], acknowledge=True) as h:
        h.voice.stand_in = set(prompts.CALL_ACK_LINES) | {"Sure.", "It is cold today.", DEVICE_QUESTION}
        h.voice.ignores_kokoro = True
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.host.dropped_waiting == 1)
        assert h.host.texts == [(ME, f"{LEAD}\nSure. It is cold today.", 7, False)]
        assert DEVICE_QUESTION not in h.manager.said
        assert h.host.interruptions[-1] == (7, "…")
        h.signal.remote_hangup()


async def test_a_line_before_the_answer_never_spends_the_turns_kokoro_line() -> None:
    """The review of 28 Oct (pipeline-01): her "Got it.", thinking, checking, wake and retry lines ask for no Kokoro, so
    while Pocket TTS is down the turn's one Kokoro line is her answer's first sentence, never "Got it." followed by
    the whole answer as a text. Pocket TTS down from the start: "Got it." is left unsaid, her first sentence is heard,
    the rest is texted, and the turn asks for Kokoro once."""
    said = "what is on my calendar for tomorrow please"
    async with Harness([said], [Turn("Two meetings. One at nine.", reply_id=7)], acknowledge=True) as h:
        h.voice.stand_in = set(prompts.CALL_ACK_LINES) | {"Two meetings.", "One at nine."}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(h.host.texts))
        assert h.host.texts == [(ME, f"{LEAD}\nOne at nine.", 7, False)]
        assert "Two meetings." in h.manager.said
        assert not any(line in h.manager.said for line in prompts.CALL_ACK_LINES)
        asked = list(zip(h.voice.spoken, h.voice.kokoro_flags, strict=True))[1:]  # the greeting apart
        assert any(text in prompts.CALL_ACK_LINES and not kokoro for text, kokoro in asked)
        assert [text for text, kokoro in asked if kokoro] == ["Two meetings."]
        assert h.host.interruptions == [(7, "Two meetings.")]
        h.signal.remote_hangup()


async def test_lines_before_the_answer_pocket_cannot_say_are_left_out_and_the_answer_is_said() -> None:
    """Her "Got it." and a thinking line Pocket TTS cannot say are left unsaid; her voice goes on, and an answer Pocket
    TTS says is said whole, nothing texted."""
    said = "what is on my calendar for tomorrow please"
    async with Harness([said], [Turn("Two meetings. One at nine.", reply_id=7)], acknowledge=True, delay=1.5) as h:
        h.voice.stand_in = set(prompts.CALL_ACK_LINES) | set(prompts.CALL_THINKING_LINES)
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("One at nine.")
        assert any(line in h.voice.spoken for line in prompts.CALL_THINKING_LINES)  # asked with no Kokoro: refused
        assert not any(line in h.manager.said for line in [*prompts.CALL_ACK_LINES, *prompts.CALL_THINKING_LINES])
        assert h.host.texts == [] and h.host.interruptions == []
        h.signal.remote_hangup()


async def test_a_second_kokoro_line_before_the_answer_stops_her_voice_for_the_whole_turn() -> None:
    """An older voice service (no "kokoro" field read): Kokoro said her "Got it.", then a thinking line: that line is
    not said, nothing of her answer is asked, and the whole answer goes as a text. Her wait is still logged: no word
    of her answer came (pipeline-06)."""
    said = "what is on my calendar for tomorrow please"
    async with Harness([said], [Turn("Two meetings. One at nine.", reply_id=7)], acknowledge=True, delay=1.5) as h:
        h.voice.stand_in = set(prompts.CALL_ACK_LINES) | set(prompts.CALL_THINKING_LINES)
        h.voice.ignores_kokoro = True
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(h.host.texts))
        assert h.host.texts == [(ME, f"{LEAD}\nTwo meetings. One at nine.", 7, False)]
        assert any(line in h.manager.said for line in prompts.CALL_ACK_LINES)
        assert any(line in h.voice.spoken for line in prompts.CALL_THINKING_LINES)
        assert not any(line in h.manager.said for line in prompts.CALL_THINKING_LINES)
        assert "Two meetings." not in h.voice.spoken
        assert h.host.interruptions == [(7, "…")]
        h.signal.remote_hangup()


STOPPED_LINE = re.compile(
    r"calls: her voice stopped (\d+) ms after the end of the turn, before her answer's first words; "
    r"(\d+) of (\d+) parts go as a text"
)


async def test_a_turn_whose_voice_stopped_before_its_answer_still_logs_its_wait(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The review of 28 Oct (pipeline-06): a turn whose voice stopped before her answer's first words logged no wait,
    so the end line left out the turns in which the caller heard nothing of her answer. Now one line says how long
    after the end of the turn her voice stopped (metadata only), and the end line counts those turns."""
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    said = "what is on my calendar for tomorrow please"
    async with Harness([said], [Turn("Two meetings. One at nine.", reply_id=7)], acknowledge=True, delay=1.5) as h:
        h.voice.stand_in = set(prompts.CALL_ACK_LINES) | set(prompts.CALL_THINKING_LINES)
        h.voice.ignores_kokoro = True
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(h.host.texts))
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    [found] = [m for record in caplog.records if (m := STOPPED_LINE.fullmatch(record.getMessage()))]
    assert int(found.group(1)) >= 0 and found.group(2, 3) == ("2", "2")
    assert not [record for record in caplog.records if WAIT_LINE.fullmatch(record.getMessage())]
    [end] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("calls: her first words after")]
    assert end.endswith("; her voice stopped before her answer's first words: 1 turn"), end


async def test_a_practice_call_follows_the_same_rule_and_supertonics_lines_never_count() -> None:
    """A practice call (/babble) in Dutch: her Dutch lines Supertonic-3's F1 says in jane's place never count (only
    Kokoro, the English stand-in, does); code's English lines after them follow the same rule."""
    turn = Turn("Heel goed. Nog een keer.", reply_id=7, code_text="Not set. Say the time again. Then I set it.")
    async with Harness(["Ik woon in Utrecht."], [turn], practice=lambda aci: "nl" if aci == ME else None) as h:
        h.voice.stand_in = {"Heel goed.", "Nog een keer.", "Not set.", "Say the time again.", "Then I set it."}
        await h.connected()
        await h.quiet_after(prompts.spoken("nl", name=NAME).greeting)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(h.host.texts))
        assert {"Heel goed.", "Nog een keer.", "Not set."} <= set(h.manager.said)
        assert h.host.texts == [(ME, f"{LEAD}\nSay the time again. Then I set it.", 7, False)]
        assert h.host.practice_turns == [True] and h.host.interruptions == [(7, "Heel goed. Nog een keer.")]
        h.signal.remote_hangup()


async def test_a_wake_up_call_follows_the_same_rule_its_opening_apart() -> None:
    """A wake-up call she placed: its opening, said by Kokoro before any turn, is no turn's line; each turn after it
    follows the same rule."""
    async with Harness(["what's the weather"], [Turn("Sunny today. Warm later.", reply_id=7)]) as h:
        opening = "Good morning! It's 7:00. wake up"
        h.voice.stand_in = {opening, "Sunny today.", "Warm later."}
        task = await placing(h)
        await h.signal.pick_up()
        assert await task == "answered"
        await h.quiet_after(opening)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(h.host.texts))
        assert "Sunny today." in h.manager.said and h.host.texts == [(ME, f"{LEAD}\nWarm later.", 7, False)]
        h.signal.remote_hangup(call_id=OUT)


async def test_a_cut_in_over_her_kokoro_line_sends_no_text() -> None:
    """The caller talking over her one Kokoro line takes over as ever: the rest of an answer talked over is dropped,
    never texted."""
    first = "This first sentence is quite a long one, so there is time to talk over it."
    turns = [Turn(f"{first} Second one.", reply_id=7), Turn("Okay.", reply_id=8)]
    async with Harness(["tell me", "wait, one more thing"], turns) as h:
        h.voice.stand_in = {first, "Second one."}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.manager.saying == first)
        h.helper.caller_says_when(lambda: h.helper.her_frames() > 30, speech(1.0))
        await h.quiet_after("Okay.")
        assert h.host.texts == [] and h.host.interruptions[0][0] == 7
        h.signal.remote_hangup()


async def test_a_turn_whose_voice_stopped_never_ends_the_call() -> None:
    """The call stays up: her end_call ends nothing when the rest of her answer went as a text; the caller reads it
    and goes on or hangs up."""
    async with Harness(["that's all for now"], [Turn("Glad to help. Talk soon!", reply_id=7, end_call=True)]) as h:
        h.voice.stand_in = {"Glad to help.", "Talk soon!"}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(h.host.texts))
        await asyncio.sleep(0.5)
        assert h.signal.hung_up == [] and h.manager.in_call
        h.signal.remote_hangup()


async def test_without_a_language_voice_a_german_line_names_her_english_voice() -> None:
    """`[voice] language_voice = ""` (the review of 25 Oct: the way back to Supertonic-3's F1 for the six, as before
    jane): a German canned line names her English voice, which the voice service speaks in F1 in German, and is kept
    under it, so `/voice af_heart` asks it again in that voice."""

    greeting = prompts.call_lines("de", name=NAME).greetings[0]

    async def one_call(number: int) -> None:
        h.signal.ring(call_id=CALL - number)
        await h.until(lambda: h.manager.said.count(greeting) == number)
        h.signal.remote_hangup(call_id=CALL - number)
        await h.until(lambda: h.host.ended == number)

    async with Harness(language="de") as h:
        h.voice.language_voice = ""
        await one_call(1)
        await one_call(2)  # kept under eve
        h.voice.voice = "af_heart"
        await one_call(3)  # another voice: asked again
        await one_call(4)
    asked = [voice for text, voice in zip(h.voice.spoken, h.voice.voices, strict=True) if text == greeting]
    assert asked == ["eve", "af_heart"]


async def test_codes_own_words_after_her_answer_go_without_hers() -> None:
    """The review of 24 Oct: code's reminder line ("Reminder set for Saturday 25 October, 07:00.") after her "Enjoy
    cooking tonight!" lost its "in the morning", the one spoken cue that the half of the day may be wrong. Code's words
    (`Turn.code_text`) are code's: they go with no words of hers, on an English call and on one in another language."""
    line = "Reminder set for Saturday 25 October, 07:00."
    async with Harness(["remind me at seven"], [Turn("Done, enjoy cooking tonight!", reply_id=7, code_text=line)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after(line)
        said = dict(zip(h.voice.spoken, h.voice.contexts, strict=True))
        assert said["Done, enjoy cooking tonight!"] == "" and said[line] == ""
        h.signal.remote_hangup()
    turn = Turn("Klaar, veel plezier vanavond.", reply_id=7, code_text=f"{line} Anything else?")
    async with Harness(["herinner me om zeven uur"], [turn], language="nl") as h:
        await h.connected()
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Anything else?")
        said = dict(zip(h.voice.spoken, h.voice.contexts, strict=True))
        assert said[line] == "" and said["Anything else?"] == ""
        h.signal.remote_hangup()


async def test_after_a_longer_call_the_user_gets_a_summary() -> None:
    async with Harness(["first idea", "second idea"], [Turn("Nice."), Turn("Good one.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Nice.")
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Good one.")
        h.signal.remote_hangup()
        await h.until(lambda: bool(h.host.summaries))
        lines, minutes = h.host.summaries[0]
        assert lines == ["User: first idea", "Estia: Nice.", "User: second idea", "Estia: Good one."]
        assert minutes >= 1


async def test_after_a_call_codes_chords_message_comes_before_the_summary() -> None:
    """[chords]: the exact chords of the call in one message from code (App.call_chords), after the dropped and
    unfinished notes and before Gemma's summary, which can fail or be skipped (design 3.7)."""
    async with Harness(["first idea", "second idea"], [Turn("Nice."), Turn("Good one.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Nice.")
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Good one.")
        h.signal.remote_hangup()
        await h.until(lambda: bool(h.host.summaries))
        assert h.host.notes == ["(chords)", "(summary)"]


async def test_a_dropped_call_gets_its_chords_message_too() -> None:
    async with Harness() as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.voice.fail_transcribe = True
        h.helper.caller_says(speech(0.6))
        await h.quiet_after(prompts.CALL_HEARING)
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: "(chords)" in h.host.notes)
        assert h.host.notes.index(prompts.CALL_DROPPED) < h.host.notes.index("(chords)")


async def test_a_call_never_speaks_markup() -> None:
    """Her answer's markup (kept as written in the history) is read the way the text shows it: no asterisk, no
    struck word, no style range: a call has no text."""
    answer = "**Two** meetings: the *dentist*, then ~~Bob~~ Anna."
    async with Harness(["what's on tomorrow"], [Turn(answer, reply_id=7)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Two meetings: the dentist, then Anna.")
        assert not any(mark in said for said in h.voice.spoken for mark in "*~")
        h.signal.remote_hangup()


async def test_a_strangers_call_is_never_answered() -> None:
    async with Harness() as h:
        h.signal.ring(STRANGER)
        await asyncio.sleep(0.2)
        assert h.signal.accepted == [] and h.signal.hung_up == [] and h.host.started == []


async def test_while_locked_the_owners_call_is_ignored_and_none_is_placed() -> None:
    """/lock: a call from the allowed account rings out (ignored, never rejected: a reject is a reply), and no reminder
    call is placed; after /unlock calls work again."""
    locked = [True]
    async with Harness(locked=lambda: locked[0]) as h:
        h.signal.ring()
        await asyncio.sleep(0.2)
        assert h.signal.accepted == [] and h.signal.hung_up == [] and h.host.started == [] and h.host.notes == []
        assert await h.manager.can_place(ME) == "locked"
        assert await h.manager.place(ME, wake=True, time="7:00", text="x") == "call_failed"
        assert h.signal.started == []
        locked[0] = False
        assert await h.manager.can_place(ME) == ""
        await h.connected()


async def test_a_lock_while_her_call_rings_hangs_it_up_and_nothing_starts() -> None:
    """/lock while a call she placed for a reminder rings: the app's hang-up ends it (review C1: nobody holding the
    phone can pick it up), and the scheduler's retry finds her locked."""
    locked = [False]
    async with Harness(locked=lambda: locked[0]) as h:
        task = await placing(h)
        await h.until(lambda: h.manager._dial is not None and h.manager._dial.call_id == OUT)
        locked[0] = True
        await h.manager.hangup()  # what the app does at /lock
        assert await task == "call_failed"
        assert h.signal.hung_up == [(ME, OUT)] and h.host.started == [] and h.host.turns == []
        assert await h.manager.can_place(ME) == "locked"


async def test_a_pick_up_as_the_lock_lands_starts_no_call() -> None:
    """Picked up just as /lock came (its hang-up too late): hung up, never a call, no turn reaches the model."""
    locked = [False]
    async with Harness(locked=lambda: locked[0]) as h:
        task = await placing(h)
        await asyncio.sleep(0.2)  # ringing
        locked[0] = True
        await h.signal.pick_up()
        assert await task == "call_failed"
        await asyncio.sleep(0.2)
        assert h.signal.hung_up == [(ME, OUT)] and h.host.started == [] and h.host.turns == []
        assert not h.manager.in_call


async def test_a_lock_while_her_call_is_being_placed_hangs_it_up_once_its_id_is_known() -> None:
    """/lock before signal-cli named the call (the app's hang-up had no call id): hung up as soon as it is known."""
    locked = [False]
    async with Harness(locked=lambda: locked[0]) as h:
        task = await placing(h)  # startCall asked, its reply not in yet
        locked[0] = True
        await h.manager.hangup()  # no call id yet: nothing to hang up
        async with asyncio.timeout(5):  # at once, never after the ring's 75 s
            assert await task == "call_failed"
        assert h.signal.hung_up == [(ME, OUT)] and h.host.started == []


async def test_a_lock_while_a_ring_waits_for_her_voice_leaves_it_ignored() -> None:
    """/lock while an incoming ring waits for the voice service's health: ignored, never rejected (a reject is a
    reply): it rings out."""
    locked = [False]
    async with Harness(locked=lambda: locked[0]) as h:
        h.voice.health_gate = asyncio.Event()
        h.signal.ring()
        await asyncio.sleep(0.1)  # the ring is in, her voice is being checked
        locked[0] = True
        h.voice.health_gate.set()
        await asyncio.sleep(0.2)
        assert h.signal.accepted == [] and h.signal.hung_up == [] and h.host.started == []


async def test_a_lock_while_it_rings_before_she_answers_leaves_it_ignored() -> None:
    """Locked during the rings before she answers (answer_after_s): never accepted, never rejected, also when
    the app's hang-up failed."""
    locked = [False]
    async with Harness(locked=lambda: locked[0], answer_after_s=0.3) as h:
        h.signal.ring()
        await asyncio.sleep(0.1)
        locked[0] = True
        await asyncio.sleep(0.5)
        assert h.signal.accepted == [] and h.signal.hung_up == [] and h.host.turns == []
        assert not h.manager.in_call


async def test_the_apps_hang_up_at_lock_ends_the_users_call_ringing_before_she_answers() -> None:
    """/lock during the rings before she answers: the app's hang-up ends the allowed user's call (core.md, the lock),
    never accepted, no turn."""
    locked = [False]
    async with Harness(locked=lambda: locked[0], answer_after_s=0.5) as h:
        h.signal.ring()
        await h.until(lambda: h.manager.in_call)
        locked[0] = True
        await h.manager.hangup()  # what the app does at /lock
        await asyncio.sleep(0.7)
        assert h.signal.hung_up == [(ME, CALL)] and h.signal.accepted == [] and h.host.turns == []


async def test_a_call_the_lock_ends_asks_the_model_for_no_summary() -> None:
    """/lock hangs up the call; its summary is never written (the model is not asked; nothing would go out)."""
    locked = [False]
    async with Harness(
        ["first idea", "second idea"], [Turn("Nice."), Turn("Good one.")], locked=lambda: locked[0]
    ) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Nice.")
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Good one.")
        locked[0] = True
        await h.manager.hangup()
        await h.until(lambda: h.host.ended == 1)
        await h.until(lambda: "(chords)" in h.host.notes)  # code's own lines come as ever (held back by the lock)
        assert h.host.summaries == []


async def test_a_second_call_during_a_call_is_ignored() -> None:
    async with Harness() as h:
        await h.connected()
        h.signal.ring(ME, call_id=99)
        await asyncio.sleep(0.2)
        assert h.signal.accepted == [(ME, CALL)]


async def test_without_the_voice_service_the_call_rings_out_with_one_note() -> None:
    async with Harness(ready=False) as h:
        h.signal.ring()
        h.signal.ring(call_id=8)
        await h.until(lambda: bool(h.host.notes))
        await asyncio.sleep(0.2)
        assert h.signal.accepted == [] and h.host.notes == [prompts.CALL_NOT_READY]  # once, not per ring


class Downloads:
    """jane's models of the languages being downloaded (voice_download.LanguageDownload's side a call reads)."""

    def __init__(self, languages: set[str]) -> None:
        self.languages = languages
        self.told: list[tuple[str, str]] = []

    def downloading(self, language: str) -> bool:
        return language in self.languages

    def call_note(self, aci: str, language: str) -> str:
        if language not in self.languages:
            return ""
        self.told.append((aci, language))
        return f"Her {language} voice is downloading."


async def test_a_call_while_janes_model_of_its_language_downloads_rings_out_with_a_text() -> None:
    """Owner, 25 Oct: no Supertonic-3 stand-in while her model of the language downloads: the call rings out (never
    rejected), the caller gets code's text saying when to call again, and no call is placed for a wake-up meanwhile."""
    downloads = Downloads({"de"})
    async with Harness(language="de", downloads=downloads) as h:
        h.signal.ring()
        await h.until(lambda: bool(h.host.notes))
        await asyncio.sleep(0.1)
        assert h.signal.accepted == [] and h.host.notes == ["Her de voice is downloading."]
        assert downloads.told == [(ME, "de")] and h.voice.spoken == []
        assert await h.manager.can_place(ME) == "downloading"
        assert await h.manager.place(ME, wake=True, time="7:00", text="x") == "call_failed"
        assert h.signal.started == []
        downloads.languages.clear()  # her German voice is ready
        assert await h.manager.can_place(ME) == ""
        await h.connected()


async def test_a_practice_call_follows_the_practised_languages_download() -> None:
    downloads = Downloads({"nl"})
    async with Harness(practice=lambda aci: "nl", downloads=downloads) as h:
        h.signal.ring()
        await h.until(lambda: bool(h.host.notes))
        assert h.signal.accepted == [] and downloads.told == [(ME, "nl")]
        assert await h.manager.can_place(ME) == ""  # her own call for a reminder is in her own language, English
    downloads = Downloads({"de"})
    async with Harness(practice=lambda aci: "nl", downloads=downloads) as h:  # practice in Dutch, German downloads
        await h.connected()
        assert downloads.told == []


def test_the_not_ready_note_says_why_and_that_she_texts_when_ready() -> None:
    assert prompts.CALL_NOT_READY == (
        "I can't take calls yet: my voice is still loading (up to 15 minutes after an install, a move or a macOS "
        "update). I'll text you when it's ready."
    )


async def test_every_ring_while_the_voice_is_not_ready_is_a_missed_call() -> None:
    """The voice-ready note keeps the promise: each ring while not ready counts, also one whose note is held back;
    only a ring that got the note (its "I'll text you") is a promise."""
    missed: list[tuple[str, bool]] = []
    async with Harness(ready=False, missed=lambda aci, promised: missed.append((aci, promised))) as h:
        h.signal.ring()
        await h.until(lambda: bool(h.host.notes))
        h.signal.ring(call_id=8)
        await h.until(lambda: len(missed) == 2)
    assert missed == [(ME, True), (ME, False)]


async def test_a_ring_that_is_answered_or_ignored_is_no_missed_call() -> None:
    missed: list[tuple[str, bool]] = []
    async with Harness(missed=lambda aci, promised: missed.append((aci, promised))) as h:
        await h.connected()
    async with Harness(
        ready=False, missed=lambda aci, promised: missed.append((aci, promised)), locked=lambda: True
    ) as h:
        h.signal.ring()
        await asyncio.sleep(0.2)
    assert missed == []


async def test_a_call_counts_as_reached_only_once_connected() -> None:
    """The voice-ready note is dropped only for a caller who heard her: never for one who hung up while it rang."""
    reached: list[str] = []
    async with Harness(answer_after_s=0.4, reached=reached.append) as h:
        h.signal.ring()
        await asyncio.sleep(0.1)
        h.signal.remote_hangup()
        await asyncio.sleep(0.6)
        assert reached == [] and h.host.started == [ME]
    async with Harness(reached=reached.append) as h:
        await h.connected()
        assert reached == [ME]


async def test_a_placed_call_counts_as_reached_only_when_picked_up() -> None:
    reached: list[str] = []
    async with Harness(reached=reached.append) as h:
        task = await placing(h)
        await asyncio.sleep(0.2)
        assert reached == []
        await h.signal.pick_up()
        assert await task == "answered"
        assert reached == [ME]


async def test_every_ring_while_the_voice_is_not_ready_leaves_a_log_line(caplog: pytest.LogCaptureFixture) -> None:
    """The note is held back for 10 minutes; the log is not (live 13 Oct: a second call left no trace)."""
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    async with Harness(ready=False) as h:
        h.signal.ring()
        await h.until(lambda: bool(h.host.notes))
        h.signal.ring(call_id=8)
        await asyncio.sleep(0.2)
    lines = [record.getMessage() for record in caplog.records if "not ready" in record.getMessage()]
    assert len(lines) == 2, lines
    assert "note sent" in lines[0] and "note held back" in lines[1]


async def test_a_sound_without_words_gets_no_sorry_he_just_listens() -> None:
    # live test 2026-10-10: "I didn't get that" over and over while the user was simply quiet
    async with Harness(["-", "what time is it"], [Turn("Ten past four.")]) as h:
        await h.connected()
        h.helper.caller_says_when(lambda: GREETING in h.manager.said, speech(0.5))  # at once
        h.helper.caller_says_when(lambda: h.voice.heard >= 1, speech(0.6))
        await h.quiet_after("Ten past four.")
        assert prompts.CALL_UNHEARD not in h.manager.said and len(h.host.turns) == 1


async def test_a_lone_stop_makes_him_quiet_without_gemma() -> None:
    async with Harness(["Stop.", "go on then"], [Turn("Sure.")]) as h:
        await h.connected()
        h.helper.caller_says_when(lambda: GREETING in h.manager.said, speech(0.5))  # at once
        await h.until(lambda: h.voice.heard == 1)
        await asyncio.sleep(0.2)
        assert h.host.turns == [] and h.manager.said == [GREETING]
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Sure.")
        assert h.host.turns == ["go on then"]


CHECK_IN = prompts.call_lines("en", name=NAME).idle  # "Take your time, I'm here. Say Estia when you need me."


def test_the_check_in_names_her_and_the_still_here_line_is_short() -> None:
    """Owner, 18 Oct: the check-in tells the caller her name, the word that ends standby."""
    assert f"Take your time, I'm here. Say {NAME} when you need me." == CHECK_IN
    assert prompts.call_lines("en", name=NAME).still_here == "I'm still here."


async def test_after_the_check_in_only_a_turn_with_her_name_is_answered(caplog: pytest.LogCaptureFixture) -> None:
    """Owner, 18 Oct: after the 90 s check-in "respond only if we hear the name", so the caller can do other things
    that make noise and call her by name. A turn without it is neither answered nor kept, and the log has no word of
    it; a turn with it ends standby."""
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    turns = [Turn("It's noon.")]
    heard = ["pass me the salt please", f"{NAME}, what time is it?"]
    async with Harness(heard, turns, idle_prompt_s=0.5) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.0))  # said to someone in the room
        await h.until(lambda: h.voice.heard == 1)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("It's noon.")
    assert h.host.turns == [f"{NAME}, what time is it?"]
    lines = [record.getMessage() for record in caplog.records]
    # a number for how near it came to her name (a near miss shows), never a word
    assert any(
        "standby: a turn without her name (5 words, nearest 4 letters off); listening on" in line for line in lines
    )
    assert any("standby over: her name" in line for line in lines)
    assert not any("salt" in line for line in lines)


async def test_standby_drops_a_waiting_yes_and_her_offer_when_it_starts() -> None:
    """Review, 22 Oct: after 90 s of quiet a device question (open 120 s) or her offer of the rundown would still take a
    yes, and her wake line ("Yes?") invites one. Standby's start drops both (Host.call_standby), once."""
    async with Harness([f"{NAME}?", "yes"], [Turn("Okay.")], idle_prompt_s=0.5) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        assert h.host.standbys == [ME]
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: bool(set(h.manager.said) & set(WOKEN)), 10, speech(1.0))
        await h.quiet_after("Okay.")
    assert h.host.turns == ["yes"]  # quiet again after "Okay.": standby again, each start drops what waits


async def test_once_her_name_ended_standby_the_next_turn_needs_no_name() -> None:
    turns = [Turn("Hi."), Turn("Rain tomorrow.")]
    async with Harness([f"hey {NAME}, you there", "and the weather tomorrow?"], turns, idle_prompt_s=0.5) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.0))
        # 0.1 s of call time after her answer, under the 0.5 s that would start standby again
        h.helper.caller_says_after(lambda: "Hi." in h.manager.said, 10, speech(1.0))
        await h.quiet_after("Rain tomorrow.")
    assert h.host.turns == [f"hey {NAME}, you there", "and the weather tomorrow?"]


WOKEN = prompts.call_lines("en", name=NAME).woken  # "Yes?", "I'm here." ...


def test_the_name_hint_is_off_until_it_measures_better() -> None:
    """The wake harness, 22 Oct (6 voices, 3 noise levels): with the hint the owner's name gained no wakes (198 of
    198 either way) and woke on names near hers 26 of 310 times (0 without); the default name woke on silence and noise
    4 of 274 times (0 without). Off by default: the plumbing stays for the next measurement."""
    assert calls.STANDBY_HINT is False


@pytest.mark.parametrize("hint", [False, True], ids=["off-the-default", "on"])
async def test_in_standby_whisper_gets_her_name_as_its_hint_only_when_on_and_never_outside_it(
    monkeypatch: pytest.MonkeyPatch, hint: bool
) -> None:
    """Owner, 22 Oct: her name said in standby was missed (two turns heard as nothing, one without it). With
    STANDBY_HINT on, every turn in standby goes to Whisper with her name as its hint; outside standby none, so a hint
    can never write her name into the noise of an ordinary turn. Off (the default): no hint ever."""
    monkeypatch.setattr(calls, "STANDBY_HINT", hint)
    heard = ["pass the salt", f"{NAME}, are you there", "and tomorrow"]
    async with Harness(heard, [Turn("Here."), Turn("Rain.")], idle_prompt_s=0.5) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.0))  # said to someone in the room
        await h.until(lambda: h.voice.heard == 1)
        h.helper.caller_says(speech(1.0))
        # 0.1 s of call time after her answer, under the 0.5 s that would start standby again: no hint
        h.helper.caller_says_after(lambda: "Here." in h.manager.said, 10, speech(1.0))
        await h.quiet_after("Rain.")
    assert h.voice.hints == ([NAME, NAME, ""] if hint else ["", "", ""])
    assert h.host.turns == [f"{NAME}, are you there", "and tomorrow"]


async def test_her_name_alone_ends_standby_with_her_wake_line_at_once_and_no_model(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Owner, 22 Oct: "when I was saying Tiffany ... nothing was happening". Her name alone has nothing for the model
    to answer: code's wake line at once ("Yes?"), then she listens; the next turn needs no name."""
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    async with Harness([f"Hey {NAME}?", "what time is it"], [Turn("It's noon.")], idle_prompt_s=0.5) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.0))
        # 0.1 s of call time after the wake line, under the 0.5 s that would start standby again
        h.helper.caller_says_after(lambda: bool(set(h.manager.said) & set(WOKEN)), 10, speech(1.0))
        await h.quiet_after("It's noon.")
    assert h.host.turns == ["what time is it"]  # her name alone never reached the model
    after = h.manager.said[h.manager.said.index(CHECK_IN) + 1 :]
    assert len(after) == 2 and after[0] in WOKEN and after[1] == "It's noon.", after
    assert any("her name alone; her wake line, listening" in r.getMessage() for r in caplog.records)


async def test_a_turn_that_ends_standby_gets_her_wake_line_before_her_answer() -> None:
    """Her name with a few more words: the wake line while the answer is worked out (the owner's turn 998: 21 s of
    silence after "standby over"), then her answer."""
    async with Harness([f"{NAME}, are you there"], [Turn("I'm here, what's up?")], idle_prompt_s=0.5) as h:
        h.host.gate = asyncio.Event()  # a slow answer
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(set(h.manager.said) & set(WOKEN)))
        assert h.host.turns == [f"{NAME}, are you there"]
        h.host.gate.set()
        await h.quiet_after("I'm here, what's up?")
    assert h.manager.said[-3] == CHECK_IN and h.manager.said[-2] in WOKEN


async def test_no_wake_line_when_her_answer_is_ready_at_once_or_a_got_it_comes() -> None:
    """Her answer ready within WAKE_LINE_AFTER_S: no wake line before it. A request of five words or more gets its
    "Got it." at once (acknowledgements on), which tells the caller she woke: no wake line beside it."""
    async with Harness([f"{NAME}, are you there"], [Turn("I'm here.")], idle_prompt_s=0.5) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("I'm here.")
    assert h.manager.said[-2:] == [CHECK_IN, "I'm here."]
    said = f"{NAME}, what is the weather tomorrow"
    async with Harness([said], [Turn("Sunny.")], idle_prompt_s=0.5, acknowledge=True) as h:
        h.host.gate = asyncio.Event()
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.2))
        await h.until(lambda: bool(set(h.manager.said) & set(prompts.CALL_ACK_LINES)))
        await asyncio.sleep(0.5)
        h.host.gate.set()
        await h.quiet_after("Sunny.")
    after = h.manager.said[h.manager.said.index(CHECK_IN) + 1 :]
    assert after[0] in prompts.CALL_ACK_LINES and after[-1] == "Sunny.", after  # a thinking line may come between
    assert not set(h.manager.said) & set(WOKEN)


async def test_a_dutch_call_wakes_in_dutch() -> None:
    check_in = prompts.call_lines("nl", name=NAME).idle
    async with Harness([f"{NAME}?", "hoe laat is het"], [Turn("Twaalf uur.")], idle_prompt_s=0.5, language="nl") as h:
        await h.connected()
        await h.quiet_after(check_in)
        h.helper.caller_says(speech(1.0))
        woken = prompts.call_lines("nl", name=NAME).woken
        await h.until(lambda: bool(set(h.manager.said) & set(woken)))
        assert any(line in woken and language == "nl" for line, language in said_in(h))


class _Longest(random.Random):
    """Picks the longest line (a wake line long enough to be talked over), or the shortest (`short`: "Yes?")."""

    def __init__(self, *, short: bool = False) -> None:
        super().__init__(3)
        self.short = short

    def choice(self, seq: Any) -> Any:
        return (min if self.short else max)(seq, key=len)


async def test_words_over_her_wake_line_are_a_turn_never_a_cut_in() -> None:
    """Review of 0.9.7 (C2b): what the caller said over her wake line after her name alone reached the model marked
    "(cutting in)", as if it cut into an answer of hers."""
    turns = [Turn("It's noon.")]
    async with Harness([f"{NAME}?", "what time is it"], turns, idle_prompt_s=0.5, rng=_Longest()) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_when(lambda: h.manager.saying in WOKEN, speech(1.6))
        await h.quiet_after("It's noon.")
    assert h.host.turns == ["what time is it"]
    assert any(line in WOKEN for line in h.manager.said)


def test_standby_waits_the_full_pause_until_the_early_end_measures_better() -> None:
    """The review of 28 Oct (calls-02): the wake harness woke the default name, "Estia", more often with the full
    pause in standby (18 items only with it, 4 only with the early end; p = 0.004)."""
    assert calls.STANDBY_EARLY_END is False


async def test_in_standby_smart_turn_is_never_asked_and_her_name_with_a_pause_is_one_turn() -> None:
    """The review of 28 Oct (calls-02): in standby a turn waits the full pause, so "Estia… what's on tomorrow?" with a
    pause after her name under the full pause is one turn, and Smart Turn is asked nothing there; once her name ended
    standby, the early end is back."""
    turns = [Turn("Two meetings."), Turn("Sunny.")]
    heard = [f"{NAME}, what's on tomorrow?", "and the weather?"]
    async with Harness(heard, turns, idle_prompt_s=3.0, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95, 0.95, 0.95]
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(0.8) + noise(1.0) + speech(1.2))  # her name, a pause, the request
        await h.quiet_after("Two meetings.")
        assert h.voice.windows == []  # standby: no early end
        h.helper.caller_says_after(lambda: "Two meetings." in h.manager.said, 10, speech(1.0))
        await h.quiet_after("Sunny.")
    assert h.host.turns == [f"{NAME}, what's on tomorrow?", "and the weather?"]
    assert len(h.voice.windows) >= 1  # out of standby: Smart Turn asked again


@pytest.mark.parametrize("whole", [f"{NAME}, what's on tomorrow?", "What's on tomorrow?"], ids=["named", "name-lost"])
async def test_her_name_then_a_pause_then_the_request_is_one_turn(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, whole: str
) -> None:
    """Review of 0.9.7 (C2a): "Estia… what's on tomorrow?" with a short pause after her name: the early end cut at
    the name, her "Yes?" came at once and the request said meanwhile was thrown away. With the early end in standby
    (`STANDBY_EARLY_END`, off since 28 Oct) her name alone waits out the full pause; what follows is joined to it, one
    turn, and no wake line comes first. Whisper may lose the name in the joined clip: heard alone before, it still
    ends standby."""
    monkeypatch.setattr(calls, "STANDBY_EARLY_END", True)
    calls_logs(caplog)
    turns = [Turn("Two meetings.")]
    async with Harness([NAME, whole], turns, idle_prompt_s=0.5, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95, 0.95]
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(0.8))  # her name
        h.helper.caller_says_when(lambda: h.voice.heard == 1, speech(1.2))  # on again, inside the full pause
        await h.quiet_after("Two meetings.")
    assert h.host.turns == [whole]
    after = h.manager.said[h.manager.said.index(CHECK_IN) + 1 :]
    assert after[-1] == "Two meetings." and not [line for line in after if line.endswith("?")], after
    assert "calls: early end held (her name); the caller went on" in log_lines(caplog)


async def test_her_name_alone_held_with_nothing_after_it_gets_her_wake_line(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calls, "STANDBY_EARLY_END", True)  # the hold of her name alone needs the early end
    calls_logs(caplog)
    async with Harness([NAME, "what time is it"], [Turn("It's noon.")], idle_prompt_s=0.5, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95, 0.95]
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(0.8))
        # 0.1 s of call time after the wake line, under the 0.5 s that would start standby again
        h.helper.caller_says_after(lambda: bool(set(h.manager.said) & set(WOKEN)), 10, speech(1.0))
        await h.quiet_after("It's noon.")
    assert h.host.turns == ["what time is it"]
    assert "calls: early end held (her name); the turn stands" in log_lines(caplog)


async def test_in_standby_the_hold_of_her_name_asks_smart_turn_nothing_either(
    caplog: pytest.LogCaptureFixture, listens: list[dict[str, Any]]
) -> None:
    """The second deep scan (calls-standby-hold-early): standby's listen waits the full pause (calls-02), but when the
    pause by speech ended her name alone, the hold for the rest of the full pause listened with the early end, so a
    pan's clatter in that pause could let Smart Turn cut the turn after all. The hold in standby asks it nothing."""
    calls_logs(caplog)
    async with Harness([NAME], idle_prompt_s=0.5, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(0.8) + noise(0.3) + bursts(8))  # her name, then dishes: the pause by speech ends it
        await h.until(lambda: any("early end held (her name)" in line for line in log_lines(caplog)))
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        assert h.voice.windows == []  # Smart Turn was asked nothing in standby, its hold included
    held = [kwargs for kwargs in listens if "turn" in kwargs and "max_s" in kwargs]  # the hold's listen
    assert held and all(kwargs.get("early", True) is False for kwargs in held), held


async def test_her_wake_line_beside_a_request_asks_nothing() -> None:
    """Review of 0.9.7 (C2c): with a request after her name, the line said while the answer is worked out could be
    "Yes?", asking for what was just given."""
    turns = [Turn("Right here, what's up?")]
    async with Harness([f"{NAME}, are you there"], turns, idle_prompt_s=0.5, rng=_Longest(short=True)) as h:
        h.host.gate = asyncio.Event()  # a slow answer
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(set(h.manager.said) & set(WOKEN)))
        h.host.gate.set()
        await h.quiet_after("Right here, what's up?")
    [woken] = [line for line in h.manager.said if line in WOKEN]
    assert not woken.endswith("?"), woken


async def test_standby_says_still_here_every_ten_minutes_until_the_calls_length_cap() -> None:
    """Owner, 18 Oct: "I'm still here" every 10 min until the hour is up: silence never ends the call before its
    length cap (no goodbye after ten minutes of silence)."""
    async with Harness(idle_prompt_s=0.5, idle_repeat_s=0.5, max_s=8.0) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        await h.until(lambda: h.manager.said.count("I'm still here.") >= 2)
        assert h.signal.hung_up == []
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert prompts.CALL_MAX_BYE in h.voice.spoken and h.manager.said.count(CHECK_IN) == 1


async def test_the_length_caps_goodbye_is_in_the_calls_language() -> None:
    """Review of 0.9.7 (C18): the 60-minute goodbye was always English."""
    dutch = prompts.call_lines("nl", name=NAME)
    async with Harness(idle_prompt_s=0.5, idle_repeat_s=0.5, max_s=3.0, language="nl") as h:
        await h.connected()
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert dutch.max_bye in h.voice.spoken and prompts.CALL_MAX_BYE not in h.voice.spoken
        assert h.voice.languages[h.voice.spoken.index(dutch.max_bye)] == "nl"


async def test_turns_without_her_name_never_hold_back_the_still_here_line() -> None:
    """The still-here line counts from her last line, not from the last sound: talk in the room goes on, and every
    ten minutes she still says it."""
    heard = ["one", "two", "three"]
    async with Harness(list(heard), idle_prompt_s=0.5, idle_repeat_s=1.0) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        for _ in heard:
            h.helper.caller_says(speech(0.6))
            await asyncio.sleep(0.05)
        await h.until(lambda: "I'm still here." in h.manager.said)
    assert h.host.turns == []


async def test_take_your_time_comes_once_then_at_most_every_ten_minutes() -> None:
    """Owner, 16 Oct: "that 90sec, im here etc. can you one time and then every 10m?" The first silence gets the
    line; a later one within the repeat time gets none, and standby starts without it."""
    turns = [Turn("One."), Turn("Two.")]
    heard = [f"{NAME}, first thing", f"{NAME}, second thing"]
    async with Harness(list(heard), turns, idle_prompt_s=0.5, idle_repeat_s=1000.0) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)  # the first silence: the line
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("One.")
        h.helper.caller_says_after(lambda: "One." in h.manager.said, 100, speech(1.0))  # 1 s of silence first
        await h.quiet_after("Two.")
        mark = len(h.helper.heard)
        await h.until(lambda: len(h.helper.heard) > mark + 200)  # 2 s more of silence
        assert h.manager.said.count(CHECK_IN) == 1
    assert h.host.turns == heard


async def test_take_your_time_comes_again_after_the_repeat_time() -> None:
    turns = [Turn("One.")]
    async with Harness([f"{NAME}, first thing"], turns, idle_prompt_s=0.5, idle_repeat_s=0.0) as h:
        await h.connected()
        # frame-exact: standby listens only 1 s of call time here (idle_repeat_s 0), which ran out now and then before
        # a caller_says sent after quiet_after landed (the speech heard as "no speech", 2 of 8 runs, 18 Oct)
        h.helper.caller_says_when(lambda: CHECK_IN in h.manager.said, speech(1.0))
        await h.quiet_after("One.")
        await h.until(lambda: h.manager.said.count(CHECK_IN) == 2)  # the next silence: again


def test_take_your_time_repeats_at_most_every_ten_minutes_by_default() -> None:
    import dataclasses

    from local_assistant.calls import CallSettings

    defaults = {f.name: f.default for f in dataclasses.fields(CallSettings)}
    assert defaults["idle_repeat_s"] == 600.0 and defaults["idle_prompt_s"] == 90.0
    assert "idle_hangup_s" not in defaults  # silence never ends a call (owner, 18 Oct)


async def test_silence_logs_the_lines_levels_to_tune_listening(caplog: pytest.LogCaptureFixture) -> None:
    # review 2026-10-11: a quiet caller who never starts an utterance left no trace to tune the start level with
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    async with Harness(idle_prompt_s=0.5, idle_repeat_s=0.5) as h:
        await h.connected()
        await h.until(lambda: "I'm still here." in h.manager.said)
    lines = [record.getMessage() for record in caplog.records]
    assert any(re.search(r"no speech .*floor \d+, peak \d+.*checking in", line) for line in lines)
    assert any(re.search(r"no speech .*floor \d+, peak \d+.*still here", line) for line in lines)


# A text that waits for the call's end (owner, 23 Oct: he typed the same text three times, as nothing said it waits):
# the app sends code's line in the chat (CallManager.text_waits decides, once a call), and the caller's has her say so
# on the call, once, at the next quiet moment
TEXT_LINE = prompts.call_lines("en", name=NAME).text_waits  # "I got your message. I'll answer it after the call."
OTHER = "99999999-8888-4777-8666-555555555555"  # another allowed account (a household's)
# a greeting that asks nothing: the line may follow it (after one that asks, the caller's reply comes first)
PLAIN = f"Hi, it's {NAME}."


def overlaps(h: Harness) -> list[int]:
    """The frames where she spoke while the caller's words came in (the helper keeps both in step)."""
    return [
        i
        for i, (caller, hers) in enumerate(zip(h.helper.sent, h.helper.heard, strict=False))  # heard: one behind
        if rms(caller) > 1000 and rms(hers) > 300
    ]


async def call_time(h: Harness, seconds: float) -> None:
    """Let `seconds` of call time pass (the lock-step helper's frames)."""
    mark = len(h.helper.heard)
    await h.until(lambda: len(h.helper.heard) > mark + round(seconds * 100))


async def test_the_callers_text_is_said_once_at_the_next_quiet_moment() -> None:
    async with Harness(greetings=(PLAIN,)) as h:
        assert h.manager.text_waits(ME) is False  # no call on: the app answers it as ever
        await h.connected()
        await h.quiet_after(PLAIN)
        assert h.manager.text_waits(ME) is True  # the chat's line goes to the caller
        await h.quiet_after(TEXT_LINE)
        assert (TEXT_LINE, "en") in said_in(h)
        assert h.manager.text_waits(ME) is False  # a second text in the same call: no second line anywhere
        await call_time(h, 3.0)
        assert h.manager.said.count(TEXT_LINE) == 1 and h.host.turns == [] and h.host.notes == []
        assert prompts.CALL_TEXT_WAITS not in h.voice.spoken  # the chat's words are the chat's


def when(h: Harness, condition: Callable[[], bool], action: Callable[[], object]) -> None:
    """Frame-exact: `action` runs at the first of the helper's frames where `condition` holds."""

    def due() -> bool:
        if condition():
            action()
            return True
        return False

    h.helper.caller_says_when(due, b"")


def after(h: Harness, condition: Callable[[], bool], frames: int, action: Callable[[], object]) -> None:
    """Frame-exact: `action` runs `frames` frames after the first of the helper's frames where `condition` holds."""
    mark: list[int] = []

    def due() -> bool:
        if not mark and condition():
            mark.append(len(h.helper.heard))
        return bool(mark) and len(h.helper.heard) >= mark[0] + frames

    when(h, due, action)


async def test_the_line_never_comes_over_the_callers_turn() -> None:
    """The caller's text waits from her greeting on, and the caller starts to speak 0.3 s after it, before a quiet
    second: the turn is heard and her answer said whole, then the line, in the quiet after it; no frame of hers went
    out while the caller's words came in."""
    answer = "First, it stays dry. Second, the wind drops. Third, it gets warm."
    async with Harness(["what about tomorrow"], [Turn(answer)]) as h:
        await h.connected()
        assert h.manager.text_waits(ME)  # while she greets
        h.helper.caller_says_after(lambda: GREETING in h.manager.said, 30, speech(1.5))
        await h.quiet_after(TEXT_LINE)
        said = h.manager.said
        assert said[said.index(GREETING) :] == [
            GREETING,
            "First, it stays dry.",
            "Second, the wind drops.",
            "Third, it gets warm.",
            TEXT_LINE,
        ]
        assert h.host.turns == ["what about tomorrow"] and overlaps(h) == []


async def test_a_text_while_she_answers_waits_for_her_whole_answer() -> None:
    first = "First, " + "it stays dry and calm all day long, " * 5 + "nothing more."  # a long sentence (5 s)
    async with Harness(["what about tomorrow"], [Turn(f"{first} Second, it gets warm.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        when(h, lambda: h.manager.saying == first, lambda: h.manager.text_waits(ME))
        h.helper.caller_says(speech(1.0))
        await h.quiet_after(TEXT_LINE)
        assert h.manager.said[-3:] == [first, "Second, it gets warm.", TEXT_LINE]


async def test_the_line_waits_while_a_device_question_waits_for_its_yes() -> None:
    """Between a device question and its yes she says nothing else (a yes must answer what she asked): the line comes
    after her answer to the reply."""
    question = "The heater needs your yes. Say yes if you want me to switch it off now."
    turns = [Turn(question, reply_id=3, asks_yes=True), Turn("Done: heater off.")]
    async with Harness(["switch the heater off", "yes"], turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Say yes if you want me to switch it off now.")
        assert h.manager.text_waits(ME)
        await call_time(h, 3.0)
        assert TEXT_LINE not in h.manager.said
        h.helper.caller_says(speech(0.4))
        await h.quiet_after(TEXT_LINE)
        assert h.manager.said[-2:] == ["Done: heater off.", TEXT_LINE]
        assert h.host.turns == ["switch the heater off", "yes"]


async def test_after_her_question_the_line_waits_and_a_short_yes_stays_a_turn() -> None:
    """Her answer asks something (no device question): the caller's reply may come after any pause, so the line never
    takes its place. A "yes" 1.2 s after it is the next turn (review, 23 Oct: it was lost as an acknowledgement of the
    line); the line comes after her answer to it."""
    question = "Want me to set a reminder for that?"
    turns = [Turn(f"The dentist is at three. {question}"), Turn("Done.")]
    async with Harness(["remind me about the dentist", "yes"], turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        when(h, lambda: h.manager.saying == question, lambda: h.manager.text_waits(ME))
        h.helper.caller_says_after(lambda: question in h.manager.said, 105, speech(0.4))
        await h.quiet_after(TEXT_LINE)
        assert h.host.turns == ["remind me about the dentist", "yes"]
        assert h.manager.said[-3:] == [question, "Done.", TEXT_LINE] and overlaps(h) == []


async def test_after_her_greeting_which_asks_the_line_waits_for_her_answer() -> None:
    """Every greeting asks something ("What's on your mind?"): a text while she greets is said after her answer to the
    caller's first turn, never in the quiet after the greeting."""
    async with Harness(["what's the time"], [Turn("Noon.")]) as h:
        await h.connected()
        assert h.manager.text_waits(ME)
        await h.quiet_after(GREETING)
        await call_time(h, 3.0)
        assert TEXT_LINE not in h.manager.said
        h.helper.caller_says(speech(1.0))
        await h.quiet_after(TEXT_LINE)
        assert h.manager.said[-2:] == ["Noon.", TEXT_LINE] and h.host.turns == ["what's the time"]


async def test_after_her_question_with_no_reply_the_line_follows_the_check_in() -> None:
    """No reply to her question: the check-in and standby as ever, then the line (standby waits for her name only)."""
    async with Harness(idle_prompt_s=2.0) as h:
        await h.connected()
        assert h.manager.text_waits(ME)
        await h.quiet_after(TEXT_LINE)
        assert h.manager.said[-3:] == [GREETING, CHECK_IN, TEXT_LINE] and h.host.standbys == [ME]


async def test_in_standby_a_question_of_hers_before_it_holds_nothing_back() -> None:
    """Standby answers only a turn with her name, so no reply is waited for: here standby starts again in the silence
    after her question (the check-in was said a short while ago, so it is not said again), and the line comes."""
    answer = Turn("It's noon. Want me to set an alarm?")
    async with Harness([f"{NAME}, what's the time"], [answer], idle_prompt_s=0.5) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Want me to set an alarm?")
        await h.until(lambda: h.host.standbys == [ME, ME])
        assert h.manager.text_waits(ME)
        await h.quiet_after(TEXT_LINE)
        assert h.manager.said[-2:] == ["Want me to set an alarm?", TEXT_LINE]


async def test_her_line_is_made_ahead_so_it_never_starts_late_over_the_caller() -> None:
    """Its speech is made while she listens and the listen gives way only once it is ready, so it starts at the quiet
    moment itself. Words the caller starts while it is still being made are a turn, heard from their start (review,
    23 Oct: made after the listen gave way, it started late over the caller, whose first words were thrown away)."""
    made = asyncio.Event()
    async with Harness(["what's the time"], [Turn("Noon.")], greetings=(PLAIN,)) as h:
        h.voice.slow[TEXT_LINE] = made
        await h.connected()
        assert h.manager.text_waits(ME)
        h.helper.caller_says_after(lambda: PLAIN in h.manager.said, 120, speech(1.0))  # after a quiet second
        after(h, lambda: PLAIN in h.manager.said, 150, made.set)  # its speech is ready 1.5 s into the listen
        await h.quiet_after(TEXT_LINE)
        assert h.host.turns == ["what's the time"] and h.voice.heard_s[0] >= 0.95  # the whole second heard
        assert h.manager.said[-2:] == ["Noon.", TEXT_LINE] and overlaps(h) == []


async def test_a_line_whose_speech_fails_is_dropped_and_the_call_goes_on() -> None:
    async with Harness(["what's the time"], [Turn("Noon.")], greetings=(PLAIN,)) as h:
        h.voice.broken.add(TEXT_LINE)  # the voice service's answer is no WAV
        await h.connected()
        assert h.manager.text_waits(ME)
        await call_time(h, 2.0)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Noon.")
        await call_time(h, 2.0)
        assert TEXT_LINE not in h.manager.said and h.voice.spoken.count(TEXT_LINE) == 1 and h.host.ended == 0


async def test_words_over_her_line_are_a_turn_as_any_never_a_cut_in() -> None:
    """With barge-in, the caller who starts as she says it stops her; the words are the next turn, without "(cutting
    in)" (it was no answer of hers, so nothing was cut off)."""
    async with Harness(["what's the time"], [Turn("Noon.")], greetings=(PLAIN,)) as h:
        await h.connected()
        await h.quiet_after(PLAIN)
        h.helper.caller_says_when(lambda: h.manager.saying == TEXT_LINE, speech(1.0))
        assert h.manager.text_waits(ME)
        await h.quiet_after("Noon.")
        assert h.host.turns == ["what's the time"] and h.host.interruptions == []
        assert h.manager.said.count(TEXT_LINE) == 1


async def test_in_standby_she_says_it_and_standby_stays() -> None:
    async with Harness(["talking to someone else"], idle_prompt_s=0.5) as h:
        await h.connected()
        await h.quiet_after(CHECK_IN)
        assert h.manager.text_waits(ME)
        await h.quiet_after(TEXT_LINE)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 1)
        await call_time(h, 2.0)
        assert h.host.turns == [] and h.host.standbys == [ME]  # no wake: a turn without her name is still dropped
        assert not set(h.manager.said) & set(WOKEN)


async def test_a_call_that_ends_before_it_is_said_says_nothing_more() -> None:
    async with Harness(["one more thing"], delay=30.0) as h:  # her answer is still being worked out
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.host.turns == ["one more thing"])
        assert h.manager.text_waits(ME)
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        await asyncio.sleep(0.1)
        assert TEXT_LINE not in h.voice.spoken and h.manager.text_waits(ME) is False


async def test_a_practice_calls_own_texts_get_no_line_anywhere() -> None:
    """A text typed during a practice call is a practice turn (answered first after it): no line in the chat or on the
    call. Another account's text still gets its chat line, never the spoken one."""
    async with Harness(practice=lambda aci: "nl" if aci == ME else None, idle_prompt_s=0.5) as h:
        await h.connected()
        await h.quiet_after(prompts.spoken("nl", name=NAME).take_your_time)  # standby: a quiet moment as any
        assert h.manager.text_waits(ME) is False
        assert h.manager.text_waits(OTHER) is True
        await call_time(h, 3.0)
        assert prompts.CALL_TEXT_WAITS_SPOKEN["nl"] not in h.voice.spoken and TEXT_LINE not in h.voice.spoken


async def test_another_accounts_text_gets_its_chat_line_and_is_never_said_on_the_call() -> None:
    async with Harness(greetings=(PLAIN,)) as h:
        await h.connected()
        await h.quiet_after(PLAIN)
        assert h.manager.text_waits(OTHER.upper()) is True  # Signal's ACIs in any case
        assert h.manager.text_waits(OTHER) is False  # once a call for that account too
        await call_time(h, 3.0)
        assert TEXT_LINE not in h.manager.said
        assert h.manager.text_waits(ME) is True  # the caller's own: said on the call
        assert h.manager.text_waits(OTHER.replace("9", "7")) is True  # a third account's after it changes nothing
        await h.quiet_after(TEXT_LINE)


async def test_each_call_says_it_once_and_a_new_call_starts_over() -> None:
    async with Harness(greetings=(PLAIN,)) as h:
        await h.connected()
        await h.quiet_after(PLAIN)
        assert h.manager.text_waits(ME)
        await h.quiet_after(TEXT_LINE)
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        h.signal.ring(call_id=CALL - 1)
        await h.until(lambda: h.manager.said.count(PLAIN) == 2)
        assert h.manager.text_waits(ME) is True and h.manager.text_waits(ME) is False
        await h.until(lambda: h.manager.said.count(TEXT_LINE) == 2)


async def test_her_line_about_a_waiting_text_is_logged_without_words(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    async with Harness(greetings=(PLAIN,)) as h:
        await h.connected()
        await h.quiet_after(PLAIN)
        h.manager.text_waits(ME)
        await h.quiet_after(TEXT_LINE)
    assert "calls: a text waits for the call's end; her line about it" in [r.getMessage() for r in caplog.records]


async def test_the_call_length_cap_says_goodbye() -> None:
    async with Harness(max_s=0.5) as h:
        await h.connected()
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert prompts.CALL_MAX_BYE in h.voice.spoken


async def test_talking_over_her_interrupts_and_becomes_the_next_turn() -> None:
    long_answer = "This is a long answer. " * 8
    async with Harness(
        ["tell me more", "wait, what about tomorrow"], [Turn(long_answer, reply_id=5), Turn("Okay.")]
    ) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 150, speech(1.2))  # 1.5 s in
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Okay.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} wait, what about tomorrow"]
        [(reply_id, heard)] = h.host.interruptions
        assert reply_id == 5 and heard.startswith("This is a long answer.") and len(heard) < len(long_answer.strip())


@pytest.mark.parametrize("gap", [1.5, 2.6])
async def test_a_cut_in_that_goes_on_is_heard_to_its_end(caplog: pytest.LogCaptureFixture, gap: float) -> None:
    """Review of 0.9.7 (C10): "Wait, I meant," over her answer, then a pause to think and "the one on Friday": the
    cut-in's listen ended after INTERRUPTION_END_S, the fragment went to the model and the rest was thrown away when
    her next answer started. A cut-in whose words go on (`goes_on`) is listened to for the rest of the full pause,
    joined and heard whole; a longer pause leaves the fragment."""
    calls_logs(caplog)
    long_answer = "This is a long answer. " * 8
    whole = "Wait, I meant, the one on Friday."
    heard = ["tell me more", "Wait, I meant,", whole]
    turns = [Turn(long_answer, reply_id=5), Turn("Friday it is."), Turn("Sure.")]
    async with Harness(heard, turns, end_of_turn_s=2.3) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says(speech(1.0))
        cut_in = speech(1.0) + bytes(2 * int(RATE * gap)) + speech(1.0)
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 150, cut_in)
        if gap < 2.3:
            await h.quiet_after("Friday it is.")
            assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} {whole}"]  # one turn
            assert "calls: the cut-in went on; heard whole (7 words)" in log_lines(caplog)
        else:  # the rest came after the full pause: a turn of its own
            await h.until(lambda: len(h.host.turns) == 3)
            assert h.host.turns[1] == f"{prompts.CUTTING_IN} Wait, I meant,"
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)


CODES_LINE = "This is code's own line."


@pytest.mark.parametrize("language", ["en", "nl"])
async def test_a_cut_in_over_codes_lines_keeps_none_of_codes_words_as_hers(language: str) -> None:
    """Review of 0.9.7 (C1): talked over code's spoken lines (the lookup line, "Reminder set", the chords line), they
    were kept in her stored reply as her own words; said whole they are not. Hard rule 6: code's lines enter her
    history only as code's notes."""
    code = f"{prompts.LOOKUP_NOT_DONE} {(CODES_LINE + ' ') * 8}".strip()
    turns = [Turn("Sure thing.", reply_id=5, code_text=code), Turn("Okay.")]
    async with Harness(["look it up", "wait, what about tomorrow"], turns, language=language) as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: h.manager.saying == CODES_LINE, 40, speech(1.2))
        await h.quiet_after("Okay.")
    assert h.host.turns[1] == f"{prompts.CUTTING_IN} wait, what about tomorrow"
    assert h.host.interruptions == [(5, "Sure thing. …")]


async def test_a_cut_in_keeps_the_device_question_and_the_not_set_line_once_reached() -> None:
    """What the uncut reply keeps (her words, the not-set line, the device question) is kept up to the cut; code's
    other lines (a reminder or lookup line) never."""
    not_set = "No reminder was set: that time has passed."
    code = f"{prompts.LOOKUP_NOT_DONE} {not_set} {(CODES_LINE + ' ') * 8}".strip()
    turns = [Turn("Sure thing.", reply_id=5, code_text=code, kept_code=(not_set,)), Turn("Okay.")]
    async with Harness(["remind me at noon", "wait, what about tomorrow"], turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: h.manager.saying == CODES_LINE, 40, speech(1.2))
        await h.quiet_after("Okay.")
    assert h.host.interruptions == [(5, f"Sure thing. {not_set} …")]


async def test_a_cough_only_pauses_her() -> None:
    answer = "This is a long answer. " * 6
    async with Harness(["tell me more", ""], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 100, noise(0.4, 6000))  # a cough
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 2)  # she stopped and listened to the cough
        await h.until(lambda: h.manager.said.count("This is a long answer.") == 6)  # then carried on to the end
        assert h.host.interruptions == [] and h.host.turns == ["tell me more"]
        assert h.helper.her_frames() - greeting > 6 * 60  # all six sentences went out


@pytest.mark.parametrize("heard", ["*sad music*", "Thank you."])
async def test_noise_heard_as_words_over_her_answer_only_pauses_her(heard: str) -> None:
    # review 2026-10-11: typing over her answer stopped her, and Gemma answered "(cutting in) *sad music*"
    answer = "This is a long answer. " * 6
    async with Harness(["tell me more", heard], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 100, noise(0.4, 6000))
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 2)  # she stopped and listened to the noise
        await h.until(lambda: h.manager.said.count("This is a long answer.") == 6)  # then carried on to the end
        assert h.host.interruptions == [] and h.host.turns == ["tell me more"]


async def test_long_typing_over_her_answer_heard_as_thank_you_only_pauses_her() -> None:
    # review 2026-10-11: every loud key click counted as voice, so 1.5 s of typing over her answer that Whisper heard
    # as "Thank you." stopped her, and Gemma got "(cutting in) Thank you."
    answer = "This is a long answer. " * 8
    async with Harness(["tell me more", "Thank you."], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 100, typing(2.0))
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 2)  # she stopped and listened to the typing
        await h.until(lambda: h.manager.said.count("This is a long answer.") == 8)  # then carried on to the end
        assert h.host.interruptions == [] and h.host.turns == ["tell me more"]


async def test_a_spoken_thank_you_over_her_answer_still_stops_her() -> None:
    answer = "This is a long answer. " * 8
    async with Harness(["tell me more", "Thank you."], [Turn(answer, reply_id=5), Turn("You're welcome.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 100, speech(1.0))
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("You're welcome.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} Thank you."]


async def test_typing_after_a_bump_heard_as_thank_you_never_reaches_gemma() -> None:
    # a bump starts an utterance, the typing after it keeps it going: its clicks are no voice
    async with Harness(["Thank you.", "what time is it"], [Turn("Ten past four.")]) as h:
        await h.connected()
        h.helper.caller_says_when(lambda: GREETING in h.manager.said, noise(0.15, 3000) + noise(0.1) + typing(2.0))
        h.helper.caller_says_when(lambda: h.voice.heard >= 1, speech(0.8))
        await h.quiet_after("Ten past four.")
        assert h.host.turns == ["what time is it"]


@pytest.mark.parametrize("said", ["*sad music*", "(keyboard clicking)", "[Music] [Applause]"])
def test_sound_labels_are_not_words(said: str) -> None:
    from local_assistant.calls import is_interruption, real_words

    assert real_words(said) == [] and not is_interruption(said, name=NAME)


async def test_a_slow_answer_gets_a_thinking_line_and_tools_a_checking_line() -> None:
    async with Harness(["give me an idea", "and my calendar?"], [Turn("Fine."), Turn("Free.")], delay=1.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Fine.")
        assert any(line in h.manager.said for line in prompts.CALL_THINKING_LINES)
        h.host.use_tools = True
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Free.")
        assert any(line in h.manager.said for line in prompts.CALL_CHECKING_LINES)


async def test_a_device_question_is_spoken_whole() -> None:
    question = "The heater needs your yes. Say yes if you want me to switch it off now."
    async with Harness(["switch the heater off", "yes"], [Turn(question, reply_id=3, asks_yes=True)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        last = "Say yes if you want me to switch it off now."
        h.helper.caller_says_when(lambda: h.manager.saying == last, speech(0.8))  # over the last sentence
        h.helper.caller_says(speech(0.8))
        await h.quiet_after(last)
        await asyncio.sleep(0.5)  # (call time runs fast: plenty for anything to happen)
        assert h.host.interruptions == []  # the question went out whole
        assert h.voice.heard == 1  # a yes counts only once she has finished asking, never one said over it


async def test_a_short_yes_to_a_device_question_reaches_the_turn() -> None:
    # review 2026-10-11: a brisk "yes" has well under 0.3 s of voiced sound and was dropped before speech recognition
    question = "The heater needs your yes. Say yes if you want me to switch it off now."
    turns = [Turn(question, reply_id=3, asks_yes=True), Turn("Done: heater off.")]
    async with Harness(["switch the heater off", "Yes."], turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        last = "Say yes if you want me to switch it off now."
        h.helper.caller_says_after(lambda: last in h.manager.said, 30, speech(0.25))  # one syllable, 0.3 s later
        await h.quiet_after("Done: heater off.")
        assert h.host.turns == ["switch the heater off", "Yes."]


async def test_a_yes_said_the_moment_she_stops_is_heard() -> None:
    # review 2026-10-11: the first 200 ms of listening only measured the line's floor and were thrown away
    question = "The heater needs your yes. Say yes if you want me to switch it off now."
    turns = [Turn(question, reply_id=3, asks_yes=True), Turn("Done: heater off.")]
    async with Harness(["switch the heater off", "Yes."], turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        last = "Say yes if you want me to switch it off now."
        h.helper.caller_says_when(lambda: last in h.manager.said, speech(0.25))  # at once, after her echo
        await h.quiet_after("Done: heater off.")
        assert h.host.turns == ["switch the heater off", "Yes."]


async def test_hearing_trouble_is_said_once_then_the_call_ends() -> None:
    async with Harness() as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.voice.fail_transcribe = True
        h.helper.caller_says(speech(0.6))
        await h.quiet_after(prompts.CALL_HEARING)
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert prompts.CALL_DROPPED in h.host.notes


async def test_a_signal_cli_restart_ends_the_call() -> None:
    async with Harness() as h:
        await h.connected()
        h.signal.events.put_nowait({"state": "ENDED", "reason": "signal_cli_restarted"})
        await h.until(lambda: h.host.ended == 1)
        assert not h.manager.in_call


async def test_status_line() -> None:
    async with Harness() as h:
        assert h.manager.status_line() == "Calls: ready"
        await h.connected()
        assert h.manager.status_line().startswith("Calls: in a call since ")
        h.signal.remote_hangup()
        await h.until(lambda: not h.manager.in_call)
        assert h.manager.status_line().startswith("Calls: ready (last call ")


@pytest.mark.parametrize("path", ["unix:/elsewhere/signal-call-1.sock", "unix:relative.sock"])
def test_a_socket_outside_the_private_folder_is_refused(path: str) -> None:
    from local_assistant.calls import socket_path

    folder = Path("/tmp/x")
    with pytest.raises(ValueError):
        socket_path({"inputDeviceName": path}, {}, folder, 1)
    assert socket_path({}, {}, folder, -1) == folder / "signal-call-18446744073709551615.sock"


async def test_speech_that_cannot_be_played_ends_the_call_instead_of_hanging() -> None:
    async with Harness(["tell me"], [Turn("First part. Second part.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.voice.broken = {"Second part."}  # the voice service sent something that is not a WAV
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.ended == 1, limit=10)
        assert prompts.CALL_DROPPED in h.host.notes and not h.manager.in_call


async def test_a_reply_left_unsaid_keeps_only_what_was_said() -> None:
    """Review of 0.9.7 (C8a, b): her stored reply held words the caller never heard: the whole answer stayed stored as
    said when the caller hung up mid-answer, or when the turn was dropped after its reply was stored (here: speech
    that cannot be played). The stored reply keeps what was said, as a cut-in's does."""
    answer = "First part of it. Second part of it. Third part of it."
    async with Harness(["tell me more"], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        inside: list[int] = []

        def hang_up_inside_the_second() -> bool:  # frame-exact: 0.2 s into her second sentence
            if h.manager.saying == "Second part of it." and len(inside) < 21:
                inside.append(1)
                if len(inside) == 20:
                    h.signal.remote_hangup()
            return False

        h.helper.caller_says_when(hang_up_inside_the_second, b"")
        await h.until(lambda: h.host.ended == 1)
        [(reply_id, kept)] = h.host.interruptions
        assert reply_id == 5 and kept.startswith("First part of it. Second") and kept.endswith("…")
        assert "Third" not in kept and "Second part of it." not in kept
    async with Harness(["tell me"], [Turn("First part. Second part. Third part.", reply_id=6)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.voice.broken = {"Second part."}
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.ended == 1, limit=10)
        assert h.host.interruptions == [(6, "First part. …")]


async def test_a_cut_in_counts_none_of_the_lead_as_heard() -> None:
    """Review of 0.9.7 (C8c): the first sentence's 300 ms lead (faint noise, no words) counted as played, so a cut-in
    early in it claimed a word or two more than the caller heard."""
    first = "One two three four five six seven eight nine ten."
    turns = [Turn(f"{first} This is the rest of it.", reply_id=5), Turn("Okay.")]
    async with Harness(["tell me more", "wait, what about tomorrow"], turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: h.manager.saying == first, 30, speech(1.2))  # once the lead is out
        await h.quiet_after("Okay.")
    [(_, kept)] = h.host.interruptions
    assert kept == "One …"


# review of 26 Oct (rtcall-4, rtcall-10): a cut-off answer ends at its last whole sentence in every script, and never
# at a title or an abbreviation before a name ("Dr.", "vs.", "e.g."); an answer that was not cut is said whole
@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("自由意志は難しい問題です。決定論は", "自由意志は難しい問題です。"),
        ("自由意志は難しい問題です！決定論は", "自由意志は難しい問題です！"),
        ("自由意志は難しい問題でしょうか？決定論は", "自由意志は難しい問題でしょうか？"),
        ("彼は「自由です。」と言いました。決定論は", "彼は「自由です。」と言いました。"),
        ("자유 의지는 어려운 문제입니다. 그러나", "자유 의지는 어려운 문제입니다."),
        ("¿Es libre la voluntad? Sí. Pero los", "¿Es libre la voluntad? Sí."),
        ("A. As Dr. Smith argued in", "A."),
        ("Two views clash. Compare free will vs. determinism in", "Two views clash."),
        ("It starts at 8 a.m. Then we go. After the", "It starts at 8 a.m. Then we go."),
        ("They formed in 1983. Their sound mixed funk and punk. After they found their", "They formed in 1983. Their sound mixed funk and punk."),
    ],
)  # fmt: skip
def test_a_cut_answer_ends_at_its_last_whole_sentence_in_any_script(text: str, kept: str) -> None:
    assert calls.whole_sentences(text) == kept


@pytest.mark.parametrize("text", ["I lean towards the compatibilists 😄", "That was a question for Alex Example"])
def test_an_answer_that_was_not_cut_keeps_its_last_sentence(text: str) -> None:
    """An answer the model ended itself (its finish reason not "length") may end without a mark: an emoji, a name."""
    assert calls.whole_sentences(f"Two views. {text}", cut=False) == f"Two views. {text}"
    assert calls.whole_sentences(f"Two views. {text}") == "Two views."  # cut at the limit: its last whole sentence


def long_answer(chars: int, tail: str = "") -> str:
    """A full spoken answer of at least `chars` characters, each sentence its own (numbered), then `tail`."""
    out: list[str] = []
    while len(" ".join(out)) < chars:
        out.append(f"Point {len(out) + 1} is that a choice can be free when it comes from your own reasons and values.")
    return " ".join([*out, tail]).strip()


# review of 26 Oct (DLG-01, rtcall-1): a call said at most 1,500 characters of an answer (spoken_text's default), while
# a full answer within the call's 500 tokens runs to about 2,000-2,300 characters in English: its end, where her own
# view goes, went unsaid, stayed in her stored reply, and an unsaid goodbye there could end the call
@pytest.mark.parametrize("practice", [None, "nl"])
async def test_call_long_answer_spoken_whole_en(practice: str | None) -> None:
    """A 2,400-character answer is said whole (within CALL_SPOKEN_CHARS), so her stored reply, as the host stored it,
    is what the voice service was asked to say: no cut, nothing set again. A practice call alike."""
    answer = long_answer(2_400)
    assert len(answer) > 2_400 > 1_500
    turns = [Turn(answer, reply_id=5)]
    async with Harness(["is free will compatible with determinism"], turns, practice=lambda aci: practice) as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after(sentences(answer)[-1])
        said = h.voice.spoken[h.voice.spoken.index(sentences(answer)[0]) :]
        assert " ".join(said) == answer
        assert h.host.interruptions == []  # the stored reply (the whole answer) is what was said


async def test_call_unsaid_tail_never_ends_call_en(caplog: pytest.LogCaptureFixture) -> None:
    """An answer over the call's spoken limit (CALL_SPOKEN_CHARS) is said up to it; its cut tail ("Good luck with the
    essay.", a closing wish) was never heard, so it ends nothing (no quiet listen, no hang-up), and her stored reply
    keeps what was said, as after a cut-in."""
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    answer = long_answer(calls.CALL_SPOKEN_CHARS, "Good luck with the essay.")
    said_whole = sentences(answer)[:-1]
    async with Harness(["help me with my essay"], [Turn(answer, reply_id=9)], goodbye_grace_s=0.3) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: bool(h.host.interruptions), limit=60)
        mark = len(h.helper.heard)
        await h.until(lambda: len(h.helper.heard) > mark + 100)  # 1 s, past the grace
        assert h.signal.hung_up == [] and "Good luck with the essay." not in h.voice.spoken
        [(reply_id, kept)] = h.host.interruptions
        spoken = h.voice.spoken[h.voice.spoken.index(said_whole[0]) :]
        assert reply_id == 9 and kept == " ".join(spoken) and answer.startswith(kept) and len(kept) < len(answer)
    assert not any("her goodbye; listening" in record.getMessage() for record in caplog.records)


async def test_no_part_sent_to_speak_is_over_the_voice_services_limit() -> None:
    """VoiceCore refuses a speak request over 1,500 characters (SpeechRequest.maxCharacters, a 400 that ends the call:
    review rtcall-6): a sentence longer than that goes in pieces, at its last comma before the limit, else its last
    space, and every piece is said."""
    clause = "and then the argument turns to what we mean by could have done otherwise"
    sentence = ", ".join(f"{clause} in case {n}" for n in range(1, 40)) + "."
    assert len(sentence) > 2 * calls.SPEECH_MAX_CHARS
    async with Harness(["go on"], [Turn(sentence, reply_id=4)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: any(text.endswith("in case 39.") for text in h.voice.spoken))
        pieces = h.voice.spoken[h.voice.spoken.index(GREETING) + 1 :]
        assert len(pieces) >= 3 and all(len(piece) <= calls.SPEECH_MAX_CHARS for piece in pieces)
        assert all(piece.endswith((",", ".")) for piece in pieces)  # cut at its commas
        assert " ".join(pieces) == sentence and h.host.interruptions == []


@pytest.mark.parametrize(
    ("text", "pieces"),
    [
        ("A short one.", ["A short one."]),
        (("word " * 400).strip() + ".", None),  # no comma: at its last space
        ("x" * 3_099 + ".", ["x" * 1_500, "x" * 1_500, "x" * 99 + "."]),  # no comma and no space: at the limit
        ("、".join(["日本語の文"] * 400) + "。", None),  # Japanese: at its comma
    ],
    ids=["short", "spaces", "unbroken", "japanese"],
)
def test_a_part_over_the_limit_is_split_into_pieces_the_voice_service_takes(
    text: str, pieces: list[str] | None
) -> None:
    parts = calls._parts(text)
    assert all(0 < len(part) <= calls.SPEECH_MAX_CHARS for part in parts)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")
    if pieces is not None:
        assert parts == pieces


async def test_a_speech_failure_at_part_7_of_20_ends_the_call_keeping_what_was_said() -> None:
    """Today's behaviour, pinned (review rtcall-6: a longer answer has more parts that may fail): speech that fails at
    part 7 of 20 ends the call (CALL_DROPPED), and her stored reply keeps the six parts said. Whether a failed part is
    tried again after a health check is the owner's call."""
    parts = [f"This is part number {n} of the answer." for n in range(1, 21)]
    async with Harness(["tell me"], [Turn(" ".join(parts), reply_id=8)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.voice.broken = {parts[6]}
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.ended == 1, limit=30)
        assert prompts.CALL_DROPPED in h.host.notes and not h.manager.in_call
    assert h.host.interruptions == [(8, " ".join(parts[:6]) + " …")]


async def test_a_turn_that_takes_too_long_is_cancelled_and_its_device_question_dropped() -> None:
    async with Harness(["switch the heater off"], delay=30.0, turn_timeout_s=1.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after(prompts.CALL_LOST.split(". ")[-1])  # spoken sentence by sentence
        assert h.host.cancelled_turns == 1 and h.host.dropped_waiting == 1


# review of 26 Oct (rtcall-2, eval-methods-6): speech starts only after the whole answer is written, so the wait before
# her first word grows with the answer; it was never measured. Each answer logs it (metadata), and the end line gives
# its median and 90th percentile, with how many came within 3 s and 10 s, long answers and short ones apart
WAIT_LINE = re.compile(
    r"calls: first audio (\d+) ms after the end of the turn \(her answer's first words (\d+) ms, (\d+) words\)"
)


async def test_each_answer_logs_her_wait_after_the_end_of_the_turn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    answer = "Two meetings tomorrow, the first at nine."
    async with Harness(["what's on tomorrow at work"], [Turn(answer, reply_id=7)], acknowledge=True, delay=0.3) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after(answer)
    [found] = [m for record in caplog.records if (m := WAIT_LINE.fullmatch(record.getMessage()))]
    first, words, count = (int(group) for group in found.groups())
    # "Got it." came first, while the answer was being written: her first sound, then her answer's first words
    assert 0 <= first < words and words >= 300 and count == 7


async def test_the_end_line_gives_her_waits_median_and_90th_percentile(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    long = " ".join(f"Sentence {n} of a long and full answer to a deep question." for n in range(1, 8))  # 77 words
    turns = [Turn("Two meetings tomorrow."), Turn(long), Turn("Nine.")]
    async with Harness(["what's on", "is free will real", "when"], turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        for said in ("Two meetings tomorrow.", sentences(long)[-1], "Nine."):
            h.helper.caller_says(speech(0.8))
            await h.quiet_after(said)
            h.manager.said.clear()
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("calls: her first words after")]
    assert re.fullmatch(
        r"calls: her first words after the end of a turn: 3 answers, median \d+ ms, 90th percentile \d+ ms, 3 within 3 s,"
        r" 3 within 10 s; long answers \(60 words or more\): 1 answer, median \d+ ms, 90th percentile \d+ ms, 1 within"
        r" 3 s, 1 within 10 s; short ones: 2 answers, median \d+ ms, 90th percentile \d+ ms, 2 within 3 s, 2 within 10 s",
        line,
    ), line


async def test_her_wait_after_a_cut_in_that_goes_on_counts_from_its_end(caplog: pytest.LogCaptureFixture) -> None:
    """A cut-in whose words go on ("Wait, I meant," then "the one on Friday") ends where the caller stopped at last:
    her wait counts from there, not from the fragment's end (the call's clock in frames: 10 ms each)."""
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    holder: list[Harness] = []

    def clock() -> float:  # call time: her frames out, one per 10 ms of the call
        return len(holder[0].helper.heard) * 0.01 if holder and holder[0].signal.helper is not None else 0.0

    long_answer = "This is a long answer. " * 8
    heard = ["tell me more", "Wait, I meant,", "Wait, I meant, the one on Friday."]
    turns = [Turn(long_answer, reply_id=5), Turn("Friday it is.")]
    async with Harness(heard, turns, end_of_turn_s=2.3, clock=clock) as h:
        holder.append(h)
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_when(
            lambda: h.helper.her_frames() > greeting + 150, speech(1.0) + bytes(2 * int(RATE * 1.5)) + speech(1.0)
        )
        await h.quiet_after("Friday it is.")
    waits = [m for record in caplog.records if (m := WAIT_LINE.fullmatch(record.getMessage()))]
    assert len(waits) == 2
    # from the fragment's end it would be the 1.5 s pause, the second second of words and the full pause after them
    assert int(waits[1].group(2)) < 1_500, waits[1].group(0)


def test_a_wait_line_with_no_answers_says_so() -> None:
    assert calls.waits_line([]) == "calls: her first words after the end of a turn: none"
    assert calls.percentile([100, 200, 300, 400, 5_000], 0.9) == 5_000 and calls.percentile([7], 0.5) == 7


async def test_a_retry_a_search_and_a_long_answer_over_the_turn_limit_lose_the_answer() -> None:
    """Review of 26 Oct (rtcall-3): the 45 s turn limit counts the whole turn, an empty first try's retry and a search
    included; a 500-token answer on a slower Mac can pass it. 46 s of the call's clock (a retry of 15 s, a search of
    16 s, then the answer's 15 s) end the turn with her lost line: the turn is cancelled, and no reply of hers is
    stored or said."""
    offset = [0.0]

    class Scripted(FakeHost):
        async def call_turn(
            self,
            aci: str,
            text: str,
            on_tools: Callable[[], None],
            *,
            practice: bool = False,
            on_retry: Callable[[], None] | None = None,
        ) -> Turn:
            self.turns.append(text)
            try:
                assert on_retry is not None
                on_retry()  # the first try came back empty
                offset[0] += 15.0
                await asyncio.sleep(0.05)
                on_tools()  # the retry searched the web
                offset[0] += 16.0 + 15.0  # the search, then the answer being written
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled_turns += 1
                raise
            return Turn("Never said.", reply_id=3)

    def clock() -> float:
        return time.monotonic() + offset[0]

    async with Harness(["is free will compatible with determinism"], clock=clock) as h:
        h.host = h.manager._host = Scripted([])
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after(prompts.CALL_LOST.split(". ")[-1])
        assert h.host.cancelled_turns == 1 and h.host.interruptions == []
        assert "Never said." not in h.voice.spoken
        assert h.manager.settings.turn_timeout_s == 45.0  # the real default


async def test_a_goodbye_with_more_whose_answer_timed_out_keeps_the_call() -> None:
    """Review of 0.9.7 (C5b): after a timeout, a goodbye with more ("Okay, bye, and remind me at five") got "Could you
    say that again?" and the call ended at once. A failed turn keeps the call: the caller can say it again."""
    said = "Okay, bye, and remind me at five."
    async with Harness([said, "remind me at five"], [Turn("Set for five.")], turn_timeout_s=1.0) as h:
        h.host.gate = asyncio.Event()  # the first answer never comes
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after(prompts.CALL_LOST.split(". ")[-1])
        h.host.gate.set()
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Set for five.")
        assert h.host.turns == [said, "remind me at five"]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    assert prompts.CALL_UNFINISHED not in h.host.notes  # asked again and answered: nothing left undone
    assert prompts.CALL_TOO_LONG_NOTE not in h.host.notes


async def test_a_call_that_ends_after_a_timed_out_answer_says_it_was_not_done() -> None:
    """Review of 0.9.7 (C5): a turn stopped by its time limit is work stopped as much as one the call's end stopped.
    The second deep scan (dialogue-call-lost-loop): the note names the time limit, never the call's end."""
    async with Harness(["switch the lights off"], delay=30.0, turn_timeout_s=1.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after(prompts.CALL_LOST.split(". ")[-1])
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        await h.until(lambda: len(h.host.notes) == 2)
    assert h.host.notes == [prompts.CALL_TOO_LONG_NOTE, "(chords)"]
    assert prompts.CALL_TOO_LONG_NOTE == (
        "Your last request on the call took too long, so I stopped working on it. Ask me again by text if you still "
        "want it."
    )


async def test_call_lost_twice_offers_text() -> None:
    """The second deep scan (dialogue-call-lost-loop): on a 16 GB Mac a deep question can pass the turn's limit; she
    asked to hear it again, and the repeat timed out the same way, again and again. The second time-out in a row says
    it takes too long on a call and asks for a text, and so does each one after it; an answer in between starts the
    count again."""
    said = "is free will compatible with determinism"
    heard = [said, said, said, "and in short", said]
    async with Harness(heard, [Turn("In short: maybe.")], turn_timeout_s=1.0) as h:
        h.host.gate = asyncio.Event()  # no answer comes in time
        await h.connected()
        await h.quiet_after(GREETING)
        for line in (prompts.CALL_LOST, prompts.CALL_TOO_LONG, prompts.CALL_TOO_LONG):
            h.manager.said.clear()
            h.helper.caller_says(speech(0.8))
            await h.quiet_after(line.split(". ")[-1])
            assert line in " ".join(h.manager.said), h.manager.said
        h.host.gate.set()
        h.manager.said.clear()
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("In short: maybe.")
        h.host.gate.clear()
        h.manager.said.clear()
        h.helper.caller_says(speech(0.8))
        await h.quiet_after(prompts.CALL_LOST.split(". ")[-1])  # the first time-out after an answer
        assert prompts.CALL_TOO_LONG not in " ".join(h.manager.said)
    assert prompts.CALL_TOO_LONG == "That takes too long on a call. Please text it to me."


async def test_hanging_up_during_a_slow_answer_ends_the_call_at_once() -> None:
    async with Harness(["think hard"], delay=30.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.turns == ["think hard"])
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1, limit=3)
        assert h.host.cancelled_turns == 1 and h.host.dropped_waiting == 1


async def test_a_lone_stop_drops_a_waiting_device_question() -> None:
    async with Harness(["Wait."]) as h:
        await h.connected()
        h.helper.caller_says_when(lambda: GREETING in h.manager.said, speech(0.5))  # at once
        await h.until(lambda: h.host.dropped_waiting == 1)


async def test_a_device_question_cut_off_before_its_question_is_dropped() -> None:
    question = "This will take a moment to explain. " * 3 + "Say yes if you want me to switch it off now."
    async with Harness(["switch the heater off", "no wait"], [Turn(question, reply_id=3, asks_yes=True)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 60, speech(1.0))
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.dropped_waiting >= 1)
        assert h.host.interruptions  # she was cut off before asking


async def test_waiting_for_a_slow_tool_does_not_spin(monkeypatch: pytest.MonkeyPatch) -> None:
    waits = 0
    real_wait = asyncio.wait

    async def counting_wait(*args: Any, **kwargs: Any) -> Any:
        nonlocal waits
        waits += 1
        return await real_wait(*args, **kwargs)

    monkeypatch.setattr(asyncio, "wait", counting_wait)
    async with Harness(["and my calendar?"], [Turn("Free.")], delay=1.0, use_tools=True) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Free.")
        assert any(line in h.manager.said for line in prompts.CALL_CHECKING_LINES) and waits < 20


async def test_thinking_lines_vary_come_first_and_never_repeat_back_to_back() -> None:
    answers = [Turn(f"Answer {n}.") for n in range(5)]
    async with Harness([f"question {n}" for n in range(5)], answers, delay=1.0, thinking_after_s=0.3) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        for n in range(5):
            h.helper.caller_says(speech(0.6))
            await h.quiet_after(f"Answer {n}.")
        fillers = [line for line in h.manager.said if line in prompts.CALL_THINKING_LINES]
        assert len(fillers) == 5 and len(set(fillers)) >= 2
        assert all(a != b for a, b in itertools.pairwise(fillers))
        order = h.manager.said[h.manager.said.index("Answer 0.") - 1]
        assert order in prompts.CALL_THINKING_LINES  # the thinking line came first, then the answer


async def test_a_plain_goodbye_ends_the_call_without_gemma() -> None:
    async with Harness(["okay bye bye"]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == []
        assert any(line in h.manager.said for line in prompts.CALL_BYE_LINES)


@pytest.mark.parametrize("fails", [ConnectionError("signal-cli"), None])
async def test_her_hang_up_that_fails_or_ends_nothing_is_logged_and_tried_once_more(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, fails: Exception | None
) -> None:
    """Review of 0.9.7 (C15): her hang-up was fire-and-forget, a failure swallowed with no log line and nothing
    checking the call ended. A failure is logged by its kind (never the account), and with no ENDED soon after it she
    hangs up once more."""
    calls_logs(caplog)
    monkeypatch.setattr(calls, "HANGUP_WAIT_S", 0.3)
    async with Harness(["okay bye bye"]) as h:
        h.signal.hangup_fails = [fails]
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.host.ended == 1)
        assert h.signal.hung_up == [(ME, CALL), (ME, CALL)]
    lines = log_lines(caplog)
    if fails is not None:
        assert "calls: her hang-up failed (ConnectionError)" in lines
    assert "calls: the call did not end 0.3 s after her hang-up; hanging up once more" in lines
    assert ME not in "\n".join(lines)


async def test_her_hang_up_that_never_ends_the_call_is_tried_twice_and_logged(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls_logs(caplog)
    monkeypatch.setattr(calls, "HANGUP_WAIT_S", 0.3)
    async with Harness(["okay bye bye"]) as h:
        h.signal.hangup_fails = [None, TimeoutError()]
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.host.ended == 1)
        assert h.signal.hung_up == [(ME, CALL), (ME, CALL)]  # twice, never more
    lines = log_lines(caplog)
    assert "calls: her hang-up failed (TimeoutError)" in lines
    assert "calls: the call did not end after her hang-up, tried twice" in lines


async def test_a_bare_bye_ends_the_call_as_the_voice_service_hears_it() -> None:
    """A call's turn goes to speech recognition as a call's (?call=1), so the caller's bare "Bye." reaches the call:
    code's bye and the end (review I23: the service erased it, and she waited 90 s for the check-in)."""
    async with Harness(["Bye."]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.voice.call_flags == [True] and h.host.turns == []
        assert any(line in h.manager.said for line in prompts.CALL_BYE_LINES)


async def test_she_can_end_the_call_after_her_goodbye() -> None:
    async with Harness(["that's everything for now, thanks"], [Turn("Glad I could help. Bye!", end_call=True)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert "Bye!" in h.manager.said  # she said it before hanging up


async def test_her_end_call_alone_ends_the_call_after_her_answer() -> None:
    async with Harness(["I think I'll go to sleep now"], [Turn("Sleep well!", end_call=True)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert "Sleep well!" in h.manager.said and prompts.CALL_IDLE not in h.manager.said


async def test_a_goodbye_that_is_talked_over_does_not_end_the_call() -> None:
    long_bye = "It was really nice to talk about all of this with you today. " * 3 + "Bye!"
    async with Harness(
        ["thanks, that's all", "oh wait, one more thing"], [Turn(long_bye, end_call=True), Turn("Sure.")]
    ) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 80, speech(1.0))
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Sure.")
        assert h.signal.hung_up == []


async def test_a_wrap_up_ends_the_call_after_his_answer() -> None:
    # live test 2026-10-10: "No, that will do for now." got a goodbye from Gemma but no end_call
    async with Harness(["No, that will do for now."], [Turn("Alright, enjoy your afternoon!")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert "Alright, enjoy your afternoon!" in h.manager.said
        assert prompts.CALL_IDLE not in h.manager.said  # hung up for the wrap-up, not after silence


async def test_a_wrap_up_answered_with_a_question_keeps_the_call() -> None:
    async with Harness(["that's all", "yes please"], [Turn("Shall I text you the list?"), Turn("Done.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Shall I text you the list?")
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Done.")
        assert h.signal.hung_up == []


async def test_a_wrap_up_answered_with_a_device_question_keeps_the_call() -> None:
    asked = Turn("Okay.", reply_id=4, asks_yes=True, code_text="Shall I switch heater off?")
    async with Harness(["that's all, heater off", "yes"], [asked, Turn("", code_text="Done: heater off.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Shall I switch heater off?")
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Done: heater off.")
        assert h.signal.hung_up == []


@pytest.mark.parametrize(
    ("said", "wraps_up"),
    [
        ("No, that will do for now.", True),
        ("that's all for today, thanks", True),
        ("Okay, that's it.", True),
        ("nothing else, thanks", True),
        ("I'm done", True),
        ("I'm good, thanks", False),  # also the answer to "how are you?"
        ("that's all I know about it, what do you think?", False),
        ("tell me more about that", False),
        ("thats all", True),
        ("That'll do, thanks!", True),  # one word to Whisper and to the words read: "that'll" (second fix review)
        # second fix review of 0.9.8, redteam-calls-wrap-up-lookalike: the apostrophes were optional, so "that it",
        # "that all" and "were done" read as a wrap-up
        ("Check that it's locked.", False),
        ("Check that it is locked.", False),
        ("Make sure that all the lights are off.", False),
        ("The dishes were done.", False),
        # third fix review of 0.9.8, voice-calls-fixrev3-01: a wrap-up's words with a request or more beside them, in an
        # idiom, or held in other words read as one, so a call ended and the request was dropped
        ("Yes, that'll do the trick.", False),
        ("That'll do. Read me the calendar.", False),
        ("That'll do. Wait, one more thing.", False),
        ("That'll do. Lights off, please.", False),
        ("Make sure nothing else is on.", False),
        # (voice-calls-fixrev3-02): "that's all right" and "that's all good" are a "no problem", no wrap-up
        ("That's all right.", False),
        ("That's all good, thanks.", False),
        ("All right, that's it.", True),
    ],
)
def test_what_counts_as_wrapping_up(said: str, wraps_up: bool) -> None:
    assert is_wrap_up(said, name=NAME) is wraps_up


async def test_a_request_after_that_will_do_after_her_goodbye_is_a_turn() -> None:
    """Third fix review of 0.9.8, voice-calls-fixrev3-01: "That'll do. Lights off, please." after her goodbye read as
    a wrap-up, so the call ended and the request was dropped."""
    said = "That'll do. Lights off, please."
    async with Harness(["I'm going out.", said], [Turn("Bye, talk soon!"), Turn("Done.")], goodbye_grace_s=2.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        h.helper.caller_says_after(lambda: "Bye, talk soon!" in h.manager.said, 20, speech(1.0))
        await h.until(lambda: bool(h.signal.hung_up) or "Done." in h.manager.said)
        assert h.host.turns == ["I'm going out.", said] and h.signal.hung_up == []
        mark = len(h.helper.heard)
        await h.until(lambda: len(h.helper.heard) > mark + 100)  # 1 s after her answer: no wrap-up ends the call
        assert h.host.turns == ["I'm going out.", said] and h.signal.hung_up == []


@pytest.mark.parametrize(
    ("said", "ends"),
    [
        # live 2026-10-12: both got a goodbye from Gemma but no end_call, and the call stayed open
        ("Yeah, it's all good. Thank you. Talk to you later.", True),
        ("No, that's good. Okay, thank you. Bye-bye.", True),
        ("Great talk. See you soon, Tiffany.", True),
        # the same with commas (Whisper punctuates fast speech so), and the usual short closings
        ("Yeah, it's all good, thank you, talk to you later.", True),
        ("No, that's good, okay, thank you, bye-bye.", True),
        ("Yeah, bye.", True),
        ("Great, thanks. Take care.", True),
        ("Okay, I am off to bed. Good night.", True),
        ("Perfect, thanks, catch you later.", True),
        ("Okay, thanks. Speak to you soon.", True),
        ("Bye-bye. Wait, what's the weather tomorrow?", False),  # the goodbye is not the last thing said
        ("I'll see you.", False),
        ("I said bye to him. Then we left.", False),
        ("I said bye, then we left.", False),
        ("Take care of the plants, please.", False),
        ("okay bye bye", False),  # a plain goodbye alone: code says bye without Gemma (is_goodbye)
        # live 14 Oct 12:04: "ok goodbye" and maybe more, to close the call, kept it open; a goodbye followed only by
        # closing talk counts (she answers the rest, then hangs up)
        ("ok goodbye, I'm going to check the log now", True),
        ("Okay, good bye.", True),
        ("Bye, talk later.", True),
        ("Good-bye! I'm off to make dinner.", True),
        ("Bye. Remind me at five to call the dentist.", True),  # a request, no question: answered, then the end
        ("Okay, goodbye. Actually, what's on tomorrow?", False),
        ("Bye, but first the weather", False),
        ("Goodbye. Hold on, one more thing.", False),
        ("Bye. Can you check the heater", False),  # a question without its question mark
        ("Bye. Is it raining", False),
        # review 14 Oct: a question after soft words, "before you go", a goodbye in quoted speech or one that a comma
        # part goes on from ("at eight") is more than a goodbye
        ("Bye, and what about tomorrow", False),
        ("Okay bye. Oh, and what's the weather tomorrow", False),
        ("Bye. So what time is it", False),
        ("Bye. Before you go, the weather tomorrow", False),
        ("Bye. Quick question, the heater", False),
        ("Remind me to tell Alex: take care, the roads are icy.", False),
        ("Tell Alex: okay, bye.", False),
        ("See you, at eight.", False),
        ("Take care, of the plants, please.", False),
        ("Okay, thanks. Bye, for now.", True),  # "for now" goes on from the goodbye and still is one
    ],
)
def test_a_turn_that_says_goodbye(said: str, ends: bool) -> None:
    assert says_goodbye(said, name="Tiffany") is ends


# fix review of 0.9.8 (voice-01, dialogue-01, guards-G02-wrap-up-refused): after outside text her end_call needs the
# caller's own closing, and a goodbye of the other ten call languages, or a wrap-up, counted for nothing
CALLERS_CLOSINGS = [
    ("okay bye", "en"),
    ("Thanks, that's all.", "en"),
    ("no, that's it, thanks", "en"),
    ("okay, that's everything for today, thanks for the help", "en"),  # 9 words: a wrap-up and a thanks
    ("Thanks, I have to go.", "en"),
    ("Can we hang up now?", "en"),
    ("No, that's good. Okay, thank you. Bye-bye.", "en"),
    ("Gracias, ¡adiós!", "es"),
    ("¡Hasta luego!", "es"),
    ("Grazie, ciao!", "it"),
    ("Perfetto, grazie. Ciao!", "it"),
    ("Arrivederci!", "it"),
    ("감사합니다. 안녕히 계세요.", "ko"),
    ("Obrigado, tchau!", "pt"),
    ("Obrigada, boa noite!", "pt"),
    ("Oké, bedankt, doei!", "nl"),
    ("Bedankt, dag!", "nl"),
    ("Dzięki, do widzenia!", "pl"),
    ("Dzięki, pa!", "pl"),
    ("Danke, tschüss!", "de"),
    ("Okay, danke. Bis morgen!", "de"),
    ("Tschüss. Ich gehe jetzt einkaufen.", "de"),  # only closing talk after it
    ("ありがとう、じゃあね", "ja"),
    ("またね", "ja"),
    ("Спасибо, пока!", "ru"),
    ("Пока!", "ru"),
    ("До свидания!", "ru"),
    ("Merci, au revoir !", "fr"),
    ("Merci, salut !", "fr"),
    ("Bonne soirée !", "fr"),
    # second fix review of 0.9.8: a wrap-up's own words with soft words, a thanks for something, and "for now" words
    # beside a thanks or a goodbye still count
    ("I think that's it for today.", "en"),
    ("That's all I needed, thanks.", "en"),
    ("Okay, that's all for the mail, thank you.", "en"),
    ("That'll do, thanks!", "en"),
    ("Dzięki, na razie!", "pl"),
    ("ありがとう、またあとで", "ja"),
    ("Всё, пока!", "ru"),
    ("Ну, пока!", "ru"),
    ("All right, that's it.", "en"),  # third fix review of 0.9.8, voice-calls-fixrev3-02
]
NO_CLOSINGS = [
    # an acknowledgement or a thanks alone, a question, more after the goodbye
    ("Okay.", "en"),
    ("Thanks.", "en"),
    ("and on Sunday?", "en"),
    ("that's all I know about it, what do you think?", "en"),
    ("Thanks for that, now read me the rest of the page.", "en"),
    ("Gracias.", "es"),
    ("Adiós, ¿y mañana?", "es"),
    ("Grazie.", "it"),
    ("감사합니다.", "ko"),
    ("Oké.", "nl"),
    ("Doei, en wat staat er op die pagina?", "nl"),
    ("Danke.", "de"),
    ("Es bleibt bewölkt, bis morgen.", "de"),  # "until tomorrow", after words that are no closing
    ("はい", "ja"),
    ("じゃあ、明日の天気は", "ja"),  # "well then", and more
    ("Спасибо.", "ru"),
    ("D'accord.", "fr"),
    # a goodbye that is also "hello" ("Ciao!") or "well then" counts only after a thanks
    ("Ciao!", "it"),
    ("Ciao, bene grazie!", "it"),
    ("안녕!", "ko"),
    ("Boa noite!", "pt"),
    ("Dag!", "nl"),
    ("Cześć!", "pl"),
    ("Servus!", "de"),
    ("じゃあ", "ja"),
    ("Salut !", "fr"),
    # second fix review of 0.9.8 (guard-01, voice-02, redteam-calls-wrap-up-lookalike): a wrap-up of one topic, then a
    # request with a thanks Whisper wrote no comma after, and words that only hold a wrap-up's look
    ("That's all for the mail. Thank you and now my calendar please.", "en"),
    ("Thanks a lot. That's it for mail now the calendar.", "en"),
    ("Okay, that's it for the mail, thanks and now read me the calendar.", "en"),
    ("That's it, thanks can you read me the next one", "en"),
    ("That's it for the page. Thanks and now read me my calendar.", "en"),
    ("that's all, heater off", "en"),
    ("Check that it's locked.", "en"),
    ("Check that it is locked.", "en"),
    ("Make sure that all the lights are off.", "en"),
    ("Make sure nothing else is on.", "en"),
    ("The dishes were done.", "en"),
    # third fix review of 0.9.8, voice-calls-fixrev3-02: "that's all right" and "that's all good" are a "no problem"
    ("That's all right.", "en"),
    ("That's all good, thanks.", "en"),
    # (redteam-calls-so-far-reads-as-goodbye, voice-01): "пока" and "na razie" also mean "so far" or "for now", and
    # "またあとで" "later"
    ("Да, пока всё понятно.", "ru"),
    ("Пока да.", "ru"),
    ("Пока всё хорошо.", "ru"),
    ("Пока хорошо.", "ru"),
    ("Пока понятно.", "ru"),
    ("Na razie rozumiem, czytaj dalej.", "pl"),
    ("Na razie dobrze.", "pl"),
    ("Na razie tak.", "pl"),
    ("うん、またあとで。天気を教えて", "ja"),
]


@pytest.mark.parametrize(("said", "code"), CALLERS_CLOSINGS)
def test_the_callers_own_closing_in_each_call_language(said: str, code: str) -> None:
    assert calls.closes_call(said, name="Tiffany", language=code), said


@pytest.mark.parametrize(("said", "code"), NO_CLOSINGS)
def test_a_thanks_a_question_or_a_hello_is_no_closing(said: str, code: str) -> None:
    assert not calls.closes_call(said, name="Tiffany", language=code), said


def test_every_call_language_has_a_closing_that_counts() -> None:
    assert {code for _, code in CALLERS_CLOSINGS} == set(LANGUAGES)


@pytest.mark.parametrize(
    ("said", "code", "ends"),
    [
        ("Okay, goodbye. I'm going to hang up.", "en", True),  # live 14 Oct 12:04 heard 7 words: maybe these
        ("Oké, bye. Ik ga koken.", "nl", True),
        ("Oké, bye, maar wacht even.", "nl", False),  # the call's "but" and stop word: more is coming
        ("Bye, wacht, nog één ding.", "nl", False),
        ("Okay, bye, aber das Wetter morgen.", "de", False),
        ("Bye. Ich muss los.", "de", True),
    ],
)
def test_after_a_goodbye_the_calls_but_and_stop_words_are_more(said: str, code: str, ends: bool) -> None:
    assert says_goodbye(said, name="Tiffany", language=code) is ends


async def test_on_a_dutch_call_bye_then_maar_keeps_the_call_open() -> None:
    said = "Oké, bye, maar wacht even."
    async with Harness([said], [Turn("Ja?")], language="nl") as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Ja?")
        await asyncio.sleep(0.3)
        assert h.host.turns == [said] and h.signal.hung_up == []


@pytest.mark.parametrize(
    "said",
    [
        "No, bye.",
        "Nope, bye-bye.",
        "No thanks, bye.",
        "Okay, thanks Tiffany, bye.",
        "Thank you very much, bye.",
        "Thanks a lot, bye bye.",
        "All right, bye.",
        "Take care.",
        "Good night, Tiffany.",
        "Okay, goodnight.",
        "See you tomorrow.",
        "Catch you later.",
        "Okay, good bye.",  # live 14 Oct: two words
        "Good-bye!",
        "Good bye, Tiffany.",
        "Bye-bye.",
        "Bye now.",
    ],
)
def test_a_plain_goodbye_also_after_no_thanks_or_her_name(said: str) -> None:
    from local_assistant.calls import is_goodbye

    assert is_goodbye(said, name="Tiffany")


@pytest.mark.parametrize("said", ["Yes, bye.", "Yeah bye", "Have a good night's sleep.", "Take care of it."])
def test_a_yes_before_a_goodbye_still_goes_to_gemma(said: str) -> None:
    # a "yes" may answer a waiting device question: Gemma's turn handles it (then the call can end)
    from local_assistant.calls import is_goodbye

    assert not is_goodbye(said, name="Tiffany")


async def test_a_goodbye_after_other_words_ends_the_call_after_her_answer() -> None:
    said = "No, that's good. Okay, thank you. Bye-bye."
    async with Harness([said], [Turn("You're welcome. Have a good night! Bye for now.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns  # Gemma answered the rest
        assert "Bye for now." in h.manager.said  # and said it whole before hanging up
        assert prompts.CALL_IDLE not in h.manager.said  # hung up for the goodbye, not after silence


async def test_after_the_users_goodbye_she_hangs_up_even_when_her_answer_asks_something() -> None:
    # owner, 14 Oct: the caller said goodbye; a question of hers keeps nobody on the line
    said = "Thanks, that helps. Talk to you later."
    async with Harness([said], [Turn("Before you go, shall I text you the list?")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert "Before you go, shall I text you the list?" in h.manager.said  # said whole first
        assert prompts.CALL_IDLE not in h.manager.said


async def test_okay_good_bye_ends_the_call_without_gemma() -> None:
    # live 14 Oct: "Okay, good bye." (two words) was no goodbye
    async with Harness(["Okay, good bye."]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == []
        assert any(line in h.manager.said for line in prompts.CALL_BYE_LINES)


async def test_a_goodbye_then_closing_talk_is_answered_then_she_hangs_up() -> None:
    # live 14 Oct 12:04: "ok goodbye" and more, to close the call; the call stayed open until the owner hung up
    said = "ok goodbye, I'm going to check the log now"
    async with Harness([said], [Turn("Good luck with the log! Bye for now.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == [said]  # Gemma answered the rest
        assert "Bye for now." in h.manager.said and prompts.CALL_IDLE not in h.manager.said


async def test_a_goodbye_with_a_device_question_waiting_keeps_the_call_for_the_yes() -> None:
    asked = Turn("Okay.", reply_id=4, asks_yes=True, code_text="Shall I switch heater off?")
    turns = [asked, Turn("", code_text="Done: heater off.")]
    async with Harness(["Turn the heater off, thanks. Bye.", "yes"], turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Shall I switch heater off?")
        h.helper.caller_says(speech(0.6))  # the yes still counts
        await h.quiet_after("Done: heater off.")
        assert h.host.turns == ["Turn the heater off, thanks. Bye.", "yes"]


async def test_it_rings_twice_before_he_answers() -> None:
    async with Harness(answer_after_s=0.4) as h:
        rang = time.monotonic()
        h.signal.ring()
        await h.until(lambda: h.signal.accepted != [])
        assert time.monotonic() - rang >= 0.4


async def test_a_caller_who_hangs_up_while_it_rings_is_not_answered() -> None:
    async with Harness(answer_after_s=0.4) as h:
        h.signal.ring()
        await asyncio.sleep(0.1)
        h.signal.remote_hangup()
        await asyncio.sleep(0.6)
        assert h.signal.accepted == [] and h.manager._call is None


# the model's warm-up (owner, 17 Oct): it loads and reads the first turn's prompt while the call rings


async def test_the_model_warms_up_while_it_rings_before_she_answers() -> None:
    async with Harness(answer_after_s=0.4) as h:
        h.signal.ring()
        await h.until(lambda: h.host.warm_ups != [])
        assert h.host.warm_ups == [(ME, False)] and h.host.started == [ME]  # after the call's start (its glance)
        assert h.signal.accepted == []  # still ringing
        await h.until(lambda: h.signal.accepted != [])


async def test_a_caller_who_hangs_up_while_it_rings_leaves_the_warm_up_to_finish() -> None:
    """A warm-up is never cancelled: an abort while Ollama loads the model stops its runner (ollama.log, 15 Oct)."""
    async with Harness(answer_after_s=0.4) as h:
        h.host.warm_gate = asyncio.Event()
        h.signal.ring()
        await h.until(lambda: h.host.warm_ups != [])
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        await asyncio.sleep(0.1)
        assert h.signal.accepted == [] and h.host.warm_cancelled == 0
        h.host.warm_gate.set()
        await h.until(lambda: h.host.warm_done == 1)


async def test_a_placed_call_warms_up_once_it_is_picked_up() -> None:
    async with Harness() as h:
        task = await placing(h)
        await asyncio.sleep(0.2)
        assert h.host.warm_ups == []  # ringing: it may never be picked up
        await h.signal.pick_up()
        assert await task == "answered"
        await h.until(lambda: h.host.warm_ups != [])
        assert h.host.warm_ups == [(ME, False)]  # her call for a reminder is never a practice call


async def test_a_practice_call_warms_up_the_practice_prompt() -> None:
    async with Harness(practice=lambda aci: "nl") as h:
        await h.connected()
        assert h.host.warm_ups == [(ME, True)]


async def test_one_warm_up_per_call() -> None:
    async with Harness(["first idea", "second idea"], [Turn("Nice."), Turn("Good one.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Nice.")
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Good one.")
        assert h.host.warm_ups == [(ME, False)]  # never again for a later turn
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        h.signal.ring()  # the next call warms up again: its own
        await h.until(lambda: len(h.signal.accepted) == 2)
        assert h.host.warm_ups == [(ME, False), (ME, False)]


async def test_a_slow_warm_up_never_holds_up_the_call() -> None:
    async with Harness(["what's on tomorrow"], [Turn("Two meetings tomorrow.")]) as h:
        h.host.warm_gate = asyncio.Event()  # the model never finishes reading
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Two meetings tomorrow.")
        assert h.host.turns == ["what's on tomorrow"] and h.host.warm_done == 0
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        await asyncio.sleep(0.1)
        assert h.host.warm_cancelled == 0  # left to finish: a finished warm-up is harmless
        h.host.warm_gate.set()
        await h.until(lambda: h.host.warm_done == 1)


async def test_a_failing_warm_up_never_affects_the_call(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    async with Harness(["what's on tomorrow"], [Turn("Two meetings tomorrow.")]) as h:
        h.host.warm_error = RuntimeError("Ollama said: a private detail")
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Two meetings tomorrow.")
        assert h.host.turns == ["what's on tomorrow"]
    failed = [r for r in caplog.records if "warm-up" in r.getMessage()]
    assert [(r.levelno, r.getMessage()) for r in failed] == [
        (logging.WARNING, "calls: model warm-up failed (RuntimeError)")  # the kind only, never the text
    ]
    assert all(r.exc_info is None for r in failed)


async def test_no_thinking_lines_unless_they_are_switched_on() -> None:
    async with Harness(["and my calendar?"], [Turn("Free.")], delay=1.0, use_tools=True, fillers=False) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Free.")
        fillers = prompts.CALL_THINKING_LINES + prompts.CALL_CHECKING_LINES
        assert not any(line in h.manager.said for line in fillers)


@pytest.mark.parametrize(
    "said",
    [
        "Shall we hang up? I think we're done for now. Thank you.",
        "you can hang up now",
        "let's hang up",
        "please hang up",
    ],
)
def test_asking_him_to_hang_up_ends_the_call(said: str) -> None:
    assert wants_hang_up(said, name="Tiffany")


@pytest.mark.parametrize("said", ["don't hang up yet", "how do I hang up a picture frame properly?"])
def test_hang_up_in_other_sentences_does_not(said: str) -> None:
    assert not wants_hang_up(said, name="Tiffany")


@pytest.mark.parametrize(
    ("said", "code", "ends"),
    [
        # owner, 18 Oct: asking her to close the call kept it open (wants_hang_up knew only "hang up"); the same rules
        # as leave-taking (a part of its own, soft words beside it, only closing talk after it), but it may be asked
        ("Okay, let's end the call.", "en", True),
        ("Can we close the call now?", "en", True),
        ("Let's call it a day.", "en", True),
        ("We can stop here, thanks.", "en", True),
        ("Let's wrap up, talk tomorrow.", "en", True),
        ("Please finish the call.", "en", True),
        ("Stop the call.", "en", True),
        ("Shall we stop here?", "en", True),
        ("I think we can leave it there.", "en", True),
        ("Alright, let's end this call, I'm tired.", "en", True),
        ("Let's stop here for now.", "en", True),
        ("Could you hang up the call, please?", "en", True),
        ("Let's end the call after you tell me the weather.", "en", False),  # a part that goes on
        ("Let's stop here, but what about the heater?", "en", False),
        ("Let's close the call. Wait, one more thing.", "en", False),
        ("Don't end the call yet.", "en", False),
        ("Why would you end the call?", "en", False),
        ("How do I end a call on my phone?", "en", False),
        ("Let's stop here and talk about chords.", "en", False),
        ("Can you wrap it up?", "en", False),  # finish the answer, not the call: "wrap up" only with "let's"
        ("Stop here.", "en", False),  # stop reading here: "stop here" only with "let's" or "we can"
        ("He said: let's call it a day.", "en", False),  # quoted speech
        ("Let's call it a day, what do you think?", "en", False),
        ("Let's end the call.", "nl", True),  # English on any call, as the goodbyes
        ("Vale, terminemos la llamada.", "es", True),
        ("Bueno, lo dejamos aquí.", "es", True),
        ("Terminemos la llamada, pero antes, ¿qué tiempo hace?", "es", False),
        ("Ok, chiudiamo la chiamata.", "it", True),
        ("Per oggi basta, grazie.", "it", True),
        ("Chiudiamo la chiamata, ma prima dimmi il meteo.", "it", False),
        ("그럼 통화 끝내자.", "ko", True),
        ("오늘은 여기까지 해요.", "ko", True),
        ("통화 끝내자, 하지만 내일 날씨는?", "ko", False),
        ("Então, vamos desligar a chamada.", "pt", True),
        ("Podemos parar por aqui.", "pt", True),
        ("Vamos encerrar a chamada, mas antes, como está o tempo?", "pt", False),
        ("Oké, laten we ophangen.", "nl", True),
        ("Laten we het hierbij laten.", "nl", True),
        ("Laten we ophangen, maar eerst het weer.", "nl", False),
        ("Dobra, kończymy rozmowę.", "pl", True),
        ("Skończmy na tym.", "pl", True),
        ("Kończymy rozmowę, ale jaka jest pogoda?", "pl", False),
        ("Okay, lass uns auflegen.", "de", True),
        ("Machen wir Schluss für heute.", "de", True),
        ("Lass uns auflegen, aber vorher das Wetter.", "de", False),
        ("じゃあ、電話を切ろう。", "ja", True),
        ("今日はここまでにしましょう。", "ja", True),
        ("電気を切ろう。", "ja", False),  # switch off the light: only soft words before it in its sentence
        ("切ってください。", "ja", False),  # also "switch it off": only with 電話
        ("Desliga.", "pt", False),  # also "switch it off"
        ("Ладно, давай закончим разговор.", "ru", True),
        ("На сегодня хватит.", "ru", True),
        ("Давай закончим разговор, но сначала погода?", "ru", False),
        ("Bon, on peut raccrocher.", "fr", True),
        ("On s'arrête là, merci.", "fr", True),
        ("On raccroche, mais d'abord la météo.", "fr", False),
        ("Laten we ophangen.", "en", False),  # the call's language and English only
    ],
)
def test_asking_her_to_end_the_call(said: str, code: str, ends: bool) -> None:
    assert wants_hang_up(said, name="Tiffany", language=code) is ends


@pytest.mark.parametrize(
    ("said", "code", "ends"),
    [
        # review 18 Oct: a request asked back and then turned down is no request ("Hang up? No, tell me more")
        ("Hang up? No, tell me more about the weather.", "en", False),
        ("End the call? No, not yet.", "en", False),
        ("Shall we stop here? Nah, keep reading.", "en", False),
        ("We can stop here? No, keep going.", "en", False),
        ("Call it a day? No, I want to hear the rest.", "en", False),
        ("Shall we hang up? Not yet.", "en", False),
        ("Shall we hang up? Please, no.", "en", False),
        ("On raccroche ? Non, continue.", "fr", False),
        ("Podemos colgar? No, sigue contando.", "es", False),
        ("Zullen we ophangen? Nee, vertel verder.", "nl", False),
        ("電話を切ろうか？いや、まだ。", "ja", False),
        ("Shall we stop here? Yes, I'm tired.", "en", True),  # asked, then said yes to
        ("Shall we hang up? I think we're done for now.", "en", True),
    ],
)
def test_a_request_to_end_the_call_turned_down_is_none(said: str, code: str, ends: bool) -> None:
    assert wants_hang_up(said, name="Tiffany", language=code) is ends


@pytest.mark.parametrize(
    ("said", "ends"),
    [
        ("Hang up? No, tell me more about the weather.", False),  # "hang up" is a goodbye too (is_goodbye)
        ("Bye? No, not yet.", False),
        ("Talk later? No, tell me about the weather first.", False),
        ("Hang up? Yes, I'm done.", True),
    ],
)
def test_a_goodbye_asked_then_turned_down_is_none(said: str, ends: bool) -> None:
    assert says_goodbye(said, name="Tiffany") is ends


async def test_a_request_to_hang_up_turned_down_keeps_the_call() -> None:
    said = "Hang up? No, tell me more about the weather."
    async with Harness([said], [Turn("Sure! Tomorrow will be sunny and warm.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Tomorrow will be sunny and warm.")
        await asyncio.sleep(0.3)
        assert h.host.turns == [said] and h.signal.hung_up == []


@pytest.mark.parametrize(
    ("said", "code"),
    [
        # review 18 Oct: "wait" and "one more thing" were guarded in English only
        ("Lass uns Schluss machen. Moment, noch was.", "de"),
        ("Lass uns Schluss machen, noch eine Frage.", "de"),
        ("Ich muss los. Moment mal.", "de"),
        ("Laten we ophangen. Nog één ding.", "nl"),
        ("Ik moet gaan, eigenlijk wil ik nog wat weten.", "nl"),
        ("Dejémoslo aquí. Una cosa más.", "es"),
        ("Terminons l'appel. Encore une chose.", "fr"),
        ("Давай заканчивать. Ещё одно.", "ru"),
        ("Skończmy na tym. Jeszcze jedno.", "pl"),
        ("Chiudiamo qui. Ancora una cosa.", "it"),
        ("Vamos encerrar a chamada. Mais uma coisa.", "pt"),
        ("今日はここまで。もう一つ。", "ja"),
        ("여기까지 하자. 하나 더.", "ko"),
        ("여기까지 하자. 잠깐만요.", "ko"),
    ],
)
def test_one_more_thing_or_wait_in_the_calls_language_keeps_the_call(said: str, code: str) -> None:
    from local_assistant.calls import takes_leave

    assert not wants_hang_up(said, name="Tiffany", language=code)
    assert not takes_leave(said, name="Tiffany", language=code)


@pytest.mark.parametrize(
    ("said", "code", "ends"),
    [
        # review 18 Oct: "pode desligar" is also "you can switch it off", 끊어도 돼요 also "may I quit": only with the
        # call or the phone named
        ("Pode desligar.", "pt", False),
        ("Pode desligar, obrigado.", "pt", False),
        ("Vamos desligar.", "pt", False),
        ("Podemos desligar.", "pt", False),
        ("Pode desligar a chamada.", "pt", True),
        ("Vamos desligar o telefone.", "pt", True),
        ("Podemos desligar a ligação, obrigado.", "pt", True),
        ("끊어도 돼요.", "ko", False),
        ("끊어도 돼.", "ko", False),
        ("전화 끊어도 돼요.", "ko", True),
        ("이제 전화를 끊어도 돼.", "ko", True),
    ],
)
def test_a_request_that_is_also_switch_it_off_names_the_call(said: str, code: str, ends: bool) -> None:
    assert wants_hang_up(said, name="Tiffany", language=code) is ends


@pytest.mark.parametrize(
    ("said", "ends"),
    [
        # review 18 Oct: close variants of a request to end the call
        ("Let's end the call here.", True),
        ("Let's stop the call here.", True),
        ("We should end the call.", True),
        ("We should stop here.", True),
        ("I think we should stop here.", True),
        ("I'm going to end the call.", True),
        ("I'll end the call now.", True),
        ("Okay, I'll call it a day.", True),
        ("Should we end the call there?", True),
        ("I'll end the call when you're done.", False),  # the part goes on
        ("How do I end the call here?", False),
        ("We should stop the heater.", False),
    ],
)
def test_close_variants_of_a_request_to_end_the_call(said: str, ends: bool) -> None:
    assert wants_hang_up(said, name="Tiffany") is ends


@pytest.mark.parametrize(
    ("said", "code", "ends"),
    [
        # review 18 Oct: phrases that also end something else count only after a lead, or not at all
        ("Send Alex a message: I'm on my way. End it here.", "en", False),  # end the message
        ("Now, end it there.", "en", False),
        ("Let's end it here.", "en", True),
        ("We can end it there, thanks.", "en", True),
        ("Tot zover.", "nl", False),  # also "so far"
        ("Kommen wir zum Ende.", "de", False),  # also the story's end
        ("Lass uns zum Ende kommen.", "de", False),
        ("Możemy skończyć.", "pl", False),  # also "we can finish" a task
        ("Możemy na tym skończyć.", "pl", True),
    ],
)
def test_a_request_that_also_ends_something_else_needs_more(said: str, code: str, ends: bool) -> None:
    assert wants_hang_up(said, name="Tiffany", language=code) is ends


def test_closing_words_in_the_calls_language_still_end_the_call() -> None:
    from local_assistant.calls import takes_leave

    assert wants_hang_up("Lass uns Schluss machen, danke.", name="Tiffany", language="de")
    assert takes_leave("Ik moet nog iets doen, tot later.", name="Tiffany", language="nl")


@pytest.mark.parametrize(
    ("code", "said", "reply"),
    [
        ("en", "Alright, let's end the call now, I'm heading out.", "Sure, enjoy! Anything else first?"),
        ("nl", "Oké, laten we ophangen.", "Prima, tot later! Nog iets?"),
    ],
)
async def test_asking_her_to_end_the_call_ends_it_once_her_reply_is_spoken(code: str, said: str, reply: str) -> None:
    async with Harness([said], [Turn(reply)], language=code) as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == [said]  # Gemma answered
        assert reply.split("! ")[-1] in h.manager.said  # her reply whole, even when it asks something
        assert prompts.call_lines(code, name=NAME).idle not in h.manager.said  # ended for the words, not silence


def test_a_call_never_says_a_stage_direction() -> None:
    # her answer of only "(laughs)" is nothing to say; a stage direction inside one is left out
    assert calls._parts("(laughs)") == []
    assert calls._parts("Okay (pause) the next one is G.") == ["Okay the next one is G."]


@pytest.mark.parametrize(
    ("hers", "code", "bye"),
    [
        # her answer ends with a plain goodbye and asks nothing: with a caller's turn of closing talk, the call ends
        # once it is spoken, whether or not she called end_call (owner, 18 Oct: her "Bye!" ended nothing)
        ("No problem! Good luck with the exam. Bye!", "en", True),
        ("Enjoy the rain, talk soon!", "en", True),
        ("Bye, Alex!", "en", True),  # a name after it
        ("Take care!", "en", True),
        ("Okay, bye for now.", "en", True),
        ("Bye! Anything else?", "en", False),  # asks something
        ("Is there anything else, or shall we say bye?", "en", False),
        ("I said bye to Alex.", "en", False),
        ("Tell Alex: bye.", "en", False),  # quoted speech
        ("Have a great day!", "en", True),  # a closing wish (owner, 18 Oct: her own closings end calls)
        ("Prima, tot later!", "nl", True),
        ("Tot ziens.", "nl", True),
        ("Dag!", "nl", False),  # also "hello"
        ("¡Hasta luego!", "es", True),
        ("A presto!", "it", True),
        ("Ciao!", "it", False),  # also "hello"
        ("안녕히 계세요.", "ko", True),
        ("Até logo!", "pt", True),
        ("Do zobaczenia!", "pl", True),
        ("Alles klar, bis bald!", "de", True),
        ("またね。", "ja", True),
        ("До свидания!", "ru", True),
        ("À bientôt !", "fr", True),
        ("Salut !", "fr", False),  # also "hello"
        ("Bye!", "nl", True),  # English on any call
    ],
)
def test_her_answer_that_ends_with_a_goodbye(hers: str, code: str, bye: bool) -> None:
    from local_assistant.calls import ends_with_goodbye

    assert ends_with_goodbye(hers, name="Tiffany", language=code) is bye


@pytest.mark.parametrize(
    ("hers", "code", "bye"),
    [
        # review 18 Oct
        ("Boa noite, Alex!", "pt", False),  # also "good evening"
        ("Bye, though.", "en", False),  # only a name after it
        ("Take care, okay.", "en", False),
        ("他に何かありますか。またね。", "ja", False),  # か asks
        ("Es bleibt bewölkt, bis morgen.", "de", False),  # "until tomorrow"
        ("Het blijft droog, tot morgen.", "nl", False),
        ("See you soon, Alex Example!", "en", True),  # a name of two words
        ("Tschüss, Alex!", "de", True),
        ("Okay, bis morgen!", "de", True),
        ("Gut, bis morgen!", "de", True),  # an acknowledgement before it
        ("Bis morgen!", "de", True),
        ("До встречи, Алекс!", "ru", True),
    ],
)
def test_her_goodbye_edge_cases(hers: str, code: str, bye: bool) -> None:
    from local_assistant.calls import ends_with_goodbye

    assert ends_with_goodbye(hers, name="Tiffany", language=code) is bye


# owner, 18 Oct, after reading how her calls end ("check all logs, how she is ending the calls and pick up those
# phrases"): of 44 calls, half ended with a closing wish or an offer to wait, not a plain goodbye
@pytest.mark.parametrize(
    ("hers", "code"),
    [
        ("Enjoy your afternoon!", "en"),
        ("Enjoy the rest of your evening.", "en"),
        ("Have a great rest of your day!", "en"),
        ("Have a productive afternoon!", "en"),
        ("Have a good one!", "en"),
        ("Sleep well, and I'll catch you in the morning!", "en"),
        ("Have a great match, and I'll be here whenever you're ready to talk again.", "en"),
        ("I'll be here when you're ready to chat again.", "en"),
        ("I'll be here!", "en"),
        ("I'm here whenever you want to pick it up again.", "en"),
        ("I'm here if you have more thoughts on that or want to talk about anything else later on today.", "en"),
        ("Good luck with the exam, and just say the word whenever you're ready to go through the next chapter.", "en"),
        ("Good luck with the exam, and just say the word when you're ready.", "en"),
        ("Enjoy your evening, I'm here if you need anything else!", "en"),
        ("Sounds good. Enjoy your evening!", "en"),
        ("Fijne avond!", "nl"),
        ("Slaap lekker!", "nl"),
        ("Succes met je examen!", "nl"),
        ("Geniet van je middag!", "nl"),
        ("Ik ben er als je me nodig hebt.", "nl"),
        # the second deep scan (calls-farewell-misfire): each wish anchored to its wish form, the wish itself still one
        ("Good luck!", "en"),
        ("Good luck tomorrow.", "en"),
        ("Succes!", "nl"),
        ("Veel succes morgen!", "nl"),
        ("Ik spreek je later!", "nl"),
        ("Suerte con el examen.", "es"),
        ("Viel Glück morgen!", "de"),
        ("Viel Spaß beim Kochen!", "de"),
        ("頑張ってください。", "ja"),
        ("いつでも声をかけてくださいね。", "ja"),
        ("언제든지 불러 주세요.", "ko"),
        ("잘 자요.", "ko"),
        ("Удачи!", "ru"),
        ("Powodzenia na egzaminie!", "pl"),
        ("Boa sorte com a prova!", "pt"),
        # the 0.9.8 release review (V03): a Spanish wish opens with "¡", which no pattern starts with
        ("¡Mucha suerte con el examen!", "es"),
        ("¡Que descanses!", "es"),
        ("Hasta pronto. ¡Y que descanses!", "es"),
        ("Suerte con el examen, y ¡que descanses!", "es"),
        ("¡Que descanses, y aquí estoy si me necesitas!", "es"),
        # its fix review, dialogue-02: the offer to wait, never a bare "here I am"
        ("¡Aquí estoy si me necesitas!", "es"),
        ("Aquí estoy para lo que necesites.", "es"),
        ("¡Aquí estaré!", "es"),
        # the second fix review of 0.9.8 (voice-06, words-farewell-offer-forms): "por si" ("in case") is an offer too
        ("Aquí estoy por si me necesitas.", "es"),
        ("Estou aqui se precisar.", "pt"),
        ("Estou aqui para o que precisar.", "pt"),
        ("Я всегда на связи.", "ru"),
    ],
)
def test_her_closing_wish_or_offer_to_wait_is_a_goodbye(hers: str, code: str) -> None:
    from local_assistant.calls import ends_with_goodbye

    assert ends_with_goodbye(hers, name="Tiffany", language=code)


@pytest.mark.parametrize(
    ("hers", "code"),
    [
        ("Enjoy your evening! What are you cooking?", "en"),  # asks: the call goes on
        ("Enjoy the music: the band starts at nine.", "en"),  # no closing wish, a plan
        ("Have a look at the second chapter first.", "en"),
        (
            "Have a great time in Lisbon next week, the weather looks good.",
            "en",
        ),  # a wish inside the answer's middle part
        ("Let me know when you want the list.", "en"),
        ("I'm here in Utrecht today.", "en"),
        ("Good luck with that is what I would say to him.", "en"),
        ("Fijne avond gehad?", "nl"),
        ("Ik ben er niet zeker van.", "nl"),
        # the second deep scan (calls-farewell-misfire): an answer's last sentence that starts with a wish's word but
        # is no wish ended the call after 3 s of quiet; fewer answers end asking since 0.9.8, so it came up more
        ("Succes is geen toeval.", "nl"),
        ("Ik spreek je taal niet.", "nl"),
        ("Good luck plays a part.", "en"),
        ("Suerte que hoy no llueve.", "es"),
        ("Viel Glück gehört dazu.", "de"),
        ("いつでも行けますよ。", "ja"),
        ("頑張っても結果が出ないこともあります。", "ja"),
        ("언제든지 바꿀 수 있어요.", "ko"),
        ("잘 자야 해요.", "ko"),
        ("Удачи не бывает без труда.", "ru"),
        ("Powodzenia nie da się kupić.", "pl"),
        ("Boa sorte é coisa rara.", "pt"),
        ("¡Suerte que hoy no llueve!", "es"),  # the 0.9.8 release review (V03): "¡" left out, still no wish
        ("¿Suerte con el examen?", "es"),  # asks
        # the fix review of 0.9.8, dialogue-02: "Here I am!" to "¿Estás ahí?" is no offer to wait
        ("¡Hola! ¡Aquí estoy!", "es"),
        ("Aquí estoy.", "es"),
        # the second fix review of 0.9.8 (voice-04, words-farewell-offer-forms): the same "Here I am" in Portuguese and
        # Russian
        ("Olá! Estou aqui para ajudar.", "pt"),
        ("Привет! Я здесь.", "ru"),
        ("Я здесь.", "ru"),
    ],
)
def test_a_wish_or_offer_that_is_no_closing_is_no_goodbye(hers: str, code: str) -> None:
    from local_assistant.calls import ends_with_goodbye

    assert not ends_with_goodbye(hers, name="Tiffany", language=code)


async def test_a_dutch_answer_ending_on_a_sentence_about_success_keeps_the_call() -> None:
    """The second deep scan (calls-farewell-misfire): a Dutch deep answer whose last sentence is "Succes is geen
    toeval." ended the call after the quiet listen, as if it were the wish "Succes!". The call stays up."""
    answer = "Hard werken helpt, maar geluk speelt ook mee. Succes is geen toeval."
    async with Harness(["is succes geluk"], [Turn(answer)], language="nl", goodbye_grace_s=0.3) as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Succes is geen toeval.")
        mark = len(h.helper.heard)
        await h.until(lambda: len(h.helper.heard) > mark + 100)  # 1 s, past the quiet listen
        assert h.signal.hung_up == [] and h.manager.in_call


@pytest.mark.parametrize(
    ("code", "said", "reply"),
    [
        # the caller's turn closes itself (review 18 Oct): only thanks or an okay, a wrap-up, leaving words or a goodbye
        ("en", "Okay, thanks!", "You're welcome! Bye!"),
        ("de", "Gut, danke.", "Gern! Bis bald!"),
        ("de", "Tschüss!", "Tschüss, bis bald!"),
        ("en", "Yes, I have to go.", "Okay, talk soon!"),  # leaving words after an answer: no leave-taking alone
    ],
)
async def test_her_goodbye_after_closing_talk_ends_the_call_without_end_call(code: str, said: str, reply: str) -> None:
    async with Harness([said], [Turn(reply)], language=code) as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == [said]
        assert reply.split("! ")[-1] in h.manager.said
        assert prompts.call_lines(code, name=NAME).idle not in h.manager.said  # ended for her goodbye, not silence


@pytest.mark.parametrize(
    "said",
    [
        "Will it rain tomorrow?",
        "Okay, wait, I'll study now.",
        # review 18 Oct: her "take care" may be sympathy, her "see you" a plan's; the owner, the same day: "if Gemma says
        # good bye phrases, end_call should trigger ... between me and gemma, we will be more succesful"
        "I have a bad cold today.",
        "I'm going to the gym later.",
        "No, I'm off.",
    ],
)
async def test_her_plain_goodbye_ends_the_call_after_a_short_quiet_listen(
    said: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Whatever the caller said, her plain goodbye (no question in her answer) ends the call once the caller has
    been quiet for goodbye_grace_s: a wrong goodbye costs a few seconds, never the call (the next test: words in
    that time keep it)."""
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    async with Harness([said], [Turn("Sure. Bye!")], goodbye_grace_s=0.5) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == [said]
    assert any("her goodbye; listening 0.5 s before the end" in record.getMessage() for record in caplog.records)


async def test_call_deep_offer_to_wait_en(caplog: pytest.LogCaptureFixture) -> None:
    """Today's behaviour, pinned (review of 26 Oct, DLG-03; the owner's rule of 18 Oct): a full deep answer whose last
    sentence offers to wait ("I'm here if you want to go deeper") counts as her goodbye, so quiet after it (a caller
    thinking it over) ends the call after goodbye_grace_s. Whether only a closing caller turn may allow that is the
    owner's call."""
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    answer = " ".join(
        [
            *RHETORICAL["en"][0][:2],
            "Compatibilists say it is choosing for your own reasons.",
            "Incompatibilists say that is not enough.",
            "I lean towards the compatibilists.",
            "I'm here if you want to go deeper into any of them.",
        ]
    )
    async with Harness(["is free will compatible with determinism"], [Turn(answer)], goodbye_grace_s=0.5) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
    assert any("her goodbye; listening 0.5 s before the end" in record.getMessage() for record in caplog.records)


async def test_words_after_her_goodbye_keep_the_call_and_are_the_next_turn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    turns = [Turn("Have fun at the gym! See you later!"), Turn("Sure, what is it?")]
    heard = ["I'm going to the gym later.", "no wait, one more question"]
    async with Harness(list(heard), turns, goodbye_grace_s=2.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        h.helper.caller_says_after(lambda: "See you later!" in h.manager.said, 20, speech(0.8))
        await h.quiet_after("Sure, what is it?")
        assert h.signal.hung_up == [] and h.host.turns == heard  # the words reach her as a turn, not a cut-in
    lines = [record.getMessage() for record in caplog.records]
    assert any("her goodbye; listening" in line for line in lines)
    assert any("the caller went on after her goodbye (5 words)" in line for line in lines)


async def test_a_goodbye_said_back_after_hers_ends_the_call_without_another_answer() -> None:
    async with Harness(["I'm going to the gym later.", "bye"], [Turn("Have fun! Bye!")], goodbye_grace_s=2.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        h.helper.caller_says_after(lambda: "Bye!" in h.manager.said, 20, speech(0.5))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == ["I'm going to the gym later."]


@pytest.mark.parametrize(
    ("code", "hers", "said"),
    [
        ("en", "Have fun! Bye!", "Thank you."),  # a voiced thanks, kept since 0.9.7's I23 fix
        ("en", "Have fun! Bye!", "Okay, thanks, bye."),
        ("nl", "Fijne avond! Doei!", "Doei!"),
        ("nl", "Fijne avond! Doei!", "Dag!"),  # also "hello": a goodbye only after hers
        ("de", "Schönen Abend! Tschüss!", "Tschüss!"),
        ("it", "Buona serata! A presto!", "Ciao!"),
        ("ru", "Хорошего вечера! До связи!", "Пока!"),
        ("fr", "Bonne soirée ! À bientôt !", "Merci, salut !"),
        ("ja", "またね。", "またね"),
        ("es", "¡Que lo pases bien! ¡Adiós!", "¡Adiós!"),
        ("ko", "좋은 저녁 보내세요! 안녕히 계세요!", "안녕히 계세요."),
        ("pt", "Boa noite! Tchau!", "Tchau tchau!"),
        ("pl", "Miłego wieczoru! Pa pa!", "Cześć!"),
    ],
)
async def test_the_callers_goodbye_in_any_language_after_hers_ends_the_call(code: str, hers: str, said: str) -> None:
    """Review of 0.9.7 (C6): after her goodbye only an English goodbye ended the call; "Doei!", "Tschüss!", "Ciao!",
    "Пока!", "またね" or a voiced "Thank you." counted as going on, so she answered again and said goodbye again.
    Only closing words (thanks, acknowledgements, goodbyes, the call's language and English) end it."""
    last = sentences(hers)[-1]
    async with Harness(["I'm going out.", said], [Turn(hers)], language=code, goodbye_grace_s=2.0) as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(0.8))
        h.helper.caller_says_after(lambda: last in h.manager.said, 20, speech(1.0))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
    assert h.host.turns == ["I'm going out."]


@pytest.mark.parametrize("said", ["Thank you, and what about tomorrow?", "Okay?", "Ciao, wait."])
async def test_more_than_closing_words_after_her_goodbye_is_a_turn(said: str) -> None:
    async with Harness(["I'm going out.", said], [Turn("Have fun! Bye!"), Turn("Sure.")], goodbye_grace_s=2.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        h.helper.caller_says_after(lambda: "Bye!" in h.manager.said, 20, speech(1.0))
        await h.quiet_after("Sure.")
    assert h.host.turns == ["I'm going out.", said]


async def test_her_goodbye_that_asks_something_keeps_the_call() -> None:
    async with Harness(
        ["I'm going to the gym later."], [Turn("Have fun! Anything else? Bye!")], goodbye_grace_s=0.3
    ) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Bye!")
        mark = len(h.helper.heard)
        await h.until(lambda: len(h.helper.heard) > mark + 100)  # 1 s, past the grace
        assert h.signal.hung_up == []


def test_the_quiet_listen_after_her_goodbye_is_three_seconds_by_default() -> None:
    import dataclasses

    from local_assistant.calls import CallSettings

    assert {f.name: f.default for f in dataclasses.fields(CallSettings)}["goodbye_grace_s"] == 3.0


async def test_her_goodbye_on_a_practice_call_keeps_it() -> None:
    # practice may rehearse a goodbye ("Adiós, hasta mañana"): only the caller's own words end a practice call
    async with Harness(["Gracias, adiós."], [Turn("¡Muy bien! ¡Hasta mañana!")], practice=lambda aci: "es") as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("¡Hasta mañana!")
        await asyncio.sleep(0.3)
        assert h.host.turns == ["Gracias, adiós."] and h.signal.hung_up == []


async def test_asking_her_to_hang_up_with_a_device_question_waiting_keeps_the_call() -> None:
    # a yes to a device must still count (as after a goodbye with more or leave-taking)
    said = "Turn the heater off. Then you can hang up."
    asked = Turn("Okay.", reply_id=4, asks_yes=True, code_text="Shall I switch heater off?")
    async with Harness([said, "yes"], [asked, Turn("", code_text="Done.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Shall I switch heater off?")
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Done.")
        assert h.host.turns == [said, "yes"]


async def test_her_end_call_with_a_device_question_waiting_keeps_the_call_for_the_yes() -> None:
    """Second fix review of 0.9.8, redteam-calls-wrap-up-lookalike: her end_call (a planted one after outside text,
    once the caller closed) ended the call at once with a device question waiting, and the caller's change was lost;
    now it waits for the answer, then ends the call."""
    said = "That's all. Heater off, please."
    asked = Turn("Okay.", reply_id=4, asks_yes=True, end_call=True, code_text="Shall I switch heater off?")
    async with Harness([said, "yes"], [asked, Turn("", code_text="Done.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Shall I switch heater off?")
        assert h.signal.hung_up == []
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == [said, "yes"] and "Done." in h.manager.said


@pytest.mark.parametrize(
    ("said", "answer"),
    [
        # review 18 Oct: the request was dropped once the yes was answered, and the call stayed open
        ("Turn the heater off. Then you can hang up.", "yes"),
        ("Heater off please, I have to go.", "Yes please."),
        ("Turn the heater off, then let's end the call.", "No, thanks."),
    ],
)
async def test_a_request_to_hang_up_held_for_a_device_question_ends_the_call_after_its_answer(
    said: str, answer: str
) -> None:
    asked = Turn("Okay.", reply_id=4, asks_yes=True, code_text="Shall I switch heater off?")
    async with Harness([said, answer], [asked, Turn("", code_text="Done.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Shall I switch heater off?")
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == [said, answer] and "Done." in h.manager.said
        assert prompts.call_lines("en", name=NAME).idle not in h.manager.said  # ended for the words, not silence


@pytest.mark.parametrize(
    ("said", "code", "answers"),
    [
        ("yes", "en", True),
        ("No, thanks.", "en", True),
        ("Okay.", "en", True),
        ("Ja, graag.", "nl", True),
        ("Nee hoor.", "nl", True),
        ("Yes, and the lamp too.", "en", False),
        ("What's the weather tomorrow?", "en", False),
    ],
)
def test_a_reply_that_only_answers_her_question(said: str, code: str, answers: bool) -> None:
    assert calls.answers_question(said, name="Tiffany", language=code) is answers


async def test_a_request_to_hang_up_held_for_a_device_question_ends_nothing_after_another_request() -> None:
    said = "Turn the heater off. Then you can hang up."
    asked = Turn("Okay.", reply_id=4, asks_yes=True, code_text="Shall I switch heater off?")
    async with Harness([said, "what's the weather tomorrow"], [asked, Turn("Sunny.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Shall I switch heater off?")
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Sunny.")
        await asyncio.sleep(0.3)
        assert h.signal.hung_up == []


async def test_a_request_to_hang_up_ends_the_call_even_after_a_question() -> None:
    async with Harness(["Shall we hang up? I think we're done for now."], [Turn("Sure, anything else first?")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert prompts.CALL_IDLE not in h.manager.said  # hung up for the request, not after silence


@pytest.mark.parametrize("said", ["stop Tiffany", "Tiffany", "okay bye Tiffany", "goodbye tiffany"])
def test_her_name_counts_in_a_stop_or_a_goodbye(said: str) -> None:
    from local_assistant.calls import is_goodbye, is_interruption, is_lone_stop

    owner = "Tiffany"  # the owner's own Estia
    if "bye" in said.lower():
        assert is_goodbye(said, name=owner)
    elif said == "Tiffany":
        assert is_interruption(said, name=owner)
    else:
        assert is_lone_stop(said, name=owner)


@pytest.mark.parametrize(
    ("text", "voiced_s", "noise"),
    [
        ("*sad music*", 1.5, True),  # a sound label, never words
        ("[Music]", 1.0, True),
        ("(keyboard clicking)", 2.0, True),
        ("♪ ♪", 1.0, True),
        ("Thank you.", 0.3, True),  # Whisper's favourite word for noise, on a short sound
        ("thank", 0.4, True),
        ("So", 0.2, True),
        ("I", 0.3, True),
        ("Thanks for watching!", 2.0, True),  # never said on a call
        ("Subtitles by the Amara.org community", 2.0, True),
        ("Please, subscribe!", 1.0, True),  # punctuation inside a sign-off changes nothing
        ("Thank you, for watching!", 1.5, True),
        ("Thanks for watching the kids yesterday, it really helped.", 2.5, False),  # inside a real sentence
        ("Can you please subscribe me to the mailing list?", 2.0, False),
        ("Thank you.", 1.2, False),  # a real thank you is longer
        ("Bye.", 0.3, True),  # a bump Whisper heard as a bye never ends a call (the call flag keeps a bare bye)
        ("Bye.", 0.8, False),  # a real one
        ("Yes.", 0.3, False),  # a short real answer counts (a device's yes)
        ("Stop.", 0.3, False),
        ("No, that's okay.", 0.8, False),
        ("What's on my calendar?", 1.5, False),
    ],
)
def test_noise_that_whisper_turns_into_words_is_silence(text: str, voiced_s: float, noise: bool) -> None:
    from local_assistant.calls import is_noise_transcript

    assert is_noise_transcript(text, voiced_s) is noise


async def test_the_owners_tiffany_greets_and_stops_by_her_name_on_a_dutch_call() -> None:
    turn = Turn("Een tuin op het balkon, leuk idee. Kruiden eerst, die zijn vergevingsgezind.", reply_id=3)
    async with Harness(["wat vind je van een balkontuin", "stop Tiffany"], [turn], language="nl", name="Tiffany") as h:
        await h.connected()
        assert h.voice.spoken[0] == "Hoi, met Tiffany. Wat kan ik voor je doen?"
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Een tuin op het balkon, leuk idee.")
        h.helper.caller_says(speech(0.6))  # "stop Tiffany": a lone stop by her name, Gemma is not asked
        await h.until(lambda: h.host.dropped_waiting == 1)
        assert h.host.turns == ["wat vind je van een balkontuin"]


async def test_without_greetings_set_english_calls_greet_by_her_name() -> None:
    async with Harness(greetings=(), name="Tiffany", rng=random.Random(1)) as h:
        h.signal.ring()
        await h.until(lambda: bool(h.voice.spoken))
        assert h.voice.spoken[0] in prompts.call_greetings("Tiffany")


async def test_the_greeting_comes_from_a_varied_set() -> None:
    greetings = ("Hey, Estia here. How are you doing?",)
    async with Harness(greetings=greetings) as h:
        h.signal.ring()
        await h.until(lambda: greetings[0] in h.voice.spoken)
    assert len(prompts.call_greetings(NAME)) >= 4 and len(set(prompts.call_greetings(NAME))) == len(
        prompts.call_greetings(NAME)
    )
    assert sum("how are you" in g.lower() or "how's" in g.lower() for g in prompts.call_greetings(NAME)) >= 2


async def test_the_greeting_is_never_the_same_twice_in_a_row_across_calls() -> None:
    # review 2026-10-11: greetings, the short okay and goodbyes shared one "last line", so by the next call the last
    # greeting could come back
    greetings = ("Hi, it's Estia.", "Hey, Estia here.")

    def greeted() -> list[str]:
        return [line for line in h.manager.said if line in greetings]

    async def one_call(number: int) -> None:
        h.signal.ring(call_id=CALL - number)
        await h.until(lambda: len(greeted()) == number)
        h.helper.caller_says(speech(0.6))  # "okay bye": a goodbye line, then she hangs up
        await h.until(lambda: h.host.ended == number)

    async with Harness(["okay bye"] * 4, greetings=greetings, rng=random.Random(0)) as h:
        for number in range(1, 5):
            await one_call(number)
    assert all(a != b for a, b in itertools.pairwise(greeted()))


async def test_a_short_got_it_shows_her_turn_was_heard_before_the_answer() -> None:
    said = "What's on my calendar for tomorrow?"
    async with Harness([said], [Turn("Two meetings tomorrow.")], acknowledge=True) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Two meetings tomorrow.")
    assert h.manager.said[1] in prompts.CALL_ACK_LINES and h.manager.said[2:] == ["Two meetings tomorrow."]
    assert all(len(line.split()) <= 3 for line in prompts.CALL_ACK_LINES)  # "Got it.": short, not a speech


@pytest.mark.parametrize(
    ("said", "acknowledged"),
    [
        ("How are you?", False),  # owner 2026-10-11: a "got it" before the answer to this makes no sense
        ("Yes.", False),
        ("Thanks a lot!", False),
        ("Um, uh, what is on tomorrow?", False),  # fillers are no words: four
        ("What is on my calendar?", True),  # five words: she took in a request
    ],
)
async def test_only_a_turn_of_five_words_or_more_gets_an_acknowledgement(said: str, acknowledged: bool) -> None:
    async with Harness([said], [Turn("Fine.")], acknowledge=True) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Fine.")
    assert h.host.turns == [said]
    assert (h.manager.said[1] in prompts.CALL_ACK_LINES) is acknowledged
    assert h.manager.said[-1] == "Fine." and len(h.manager.said) == (3 if acknowledged else 2)


@pytest.mark.parametrize(
    ("said", "words"),
    [
        ("How are you?", 3),
        ("Um, uh, what is on tomorrow?", 4),
        ("*keyboard clicking* Switch the lights off, please.", 5),
        ("Wat staat morgen op mijn agenda?", 6),
        ("Как дела?", 2),
        ("元気ですか", 2),  # Japanese is written without spaces: about two characters a word
        ("明日の予定を教えてください", 6),
    ],
)
def test_the_words_of_a_turn_in_any_language(said: str, words: int) -> None:
    from local_assistant.calls import turn_words

    assert turn_words(said) == words


def said_in(h: Harness) -> list[tuple[str, str | None]]:
    return list(zip(h.voice.spoken, h.voice.languages, strict=True))


async def test_a_call_in_another_language_uses_its_lines_and_says_codes_words_in_english() -> None:
    dutch = prompts.spoken("nl", name=NAME)
    result = "Done: lamp is now on."
    turn = Turn("De lamp is aan.", reply_id=3, code_text=result)
    said = "doe de lamp in de woonkamer aan"
    async with Harness([said], [turn], language="nl", acknowledge=True, greetings=("Hi!",)) as h:
        await h.connected()
        await h.quiet_after(dutch.greeting)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after(result)
        assert said_in(h)[0] == (dutch.greeting, "nl")  # the language's greeting (the English set is not used)
        assert said_in(h)[1] in {
            (ack, "nl") for ack in prompts.call_lines("nl", name=NAME).acknowledge
        }  # "Oké, begrepen."
        assert said_in(h)[2:] == [
            ("De lamp is aan.", "nl"),
            (result, "en"),  # code's exact words, in English so they are always clear
        ]
        assert h.manager.said[-2:] == ["De lamp is aan.", result]
        assert h.voice.heard_in == ["nl"]  # the whole call listens in its language too
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)


async def test_a_device_question_on_a_call_in_another_language_is_only_codes_question() -> None:
    # real-model eval 2026-10-11: on a Dutch call Gemma said "Ik heb de heater uitgezet, maar je moet nog even 'ja'
    # zeggen" (it had not: the heater waited for the yes); English Gemma does not. She says only code's question.
    question = "Shall I switch heater off?"
    claim = "Ik heb de heater uitgezet, maar je moet nog even 'ja' zeggen."
    turns = [Turn(claim, reply_id=3, asks_yes=True, code_text=question), Turn("", code_text="Done: heater is now off.")]
    async with Harness(["doe de verwarming uit", "ja"], turns, language="nl") as h:
        await h.connected()
        await h.quiet_after(prompts.spoken("nl", name=NAME).greeting)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: question in h.manager.said, 30, speech(0.4))
        await h.quiet_after("Done: heater is now off.")
    assert said_in(h)[1:] == [(question, "en"), ("Done: heater is now off.", "en")]
    assert claim not in h.voice.spoken and h.host.turns == ["doe de verwarming uit", "ja"]
    assert h.host.interruptions == []


async def test_english_calls_say_the_code_text_as_before() -> None:
    # English Gemma's words come before the device question, as before (only another language leaves them out)
    turn = Turn("The heater can go off.", reply_id=3, asks_yes=True, code_text="Shall I switch heater off?")
    async with Harness(["switch the heater off"], [turn]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Shall I switch heater off?")
        assert said_in(h)[1:] == [("The heater can go off.", "en"), ("Shall I switch heater off?", "en")]


async def test_a_quiet_call_in_another_language() -> None:
    german = prompts.spoken("de", name=NAME)
    assert german.take_your_time == f"Lass dir Zeit, ich bin da. Sag {NAME}, wenn du mich brauchst."
    async with Harness(language="de", idle_prompt_s=0.5, idle_repeat_s=0.5) as h:
        await h.connected()
        await h.quiet_after(german.take_your_time)
        await h.quiet_after(german.still_here)
        assert (german.take_your_time, "de") in said_in(h) and (german.still_here, "de") in said_in(h)


@pytest.mark.parametrize("language", ["ja", "ko", "ru"])
def test_where_her_voice_reads_no_latin_letters_the_check_in_asks_for_her_name_without_it(language: str) -> None:
    """Japanese, Korean and Russian lines never hold her name in Latin letters (the voice cannot read them): the
    check-in asks to call her by name."""
    lines = prompts.spoken(language, name=NAME)
    assert NAME not in lines.take_your_time and lines.take_your_time != prompts.CALL_SPOKEN[language].take_your_time[:5]


@pytest.mark.parametrize("language", sorted(prompts.CALL_SPOKEN))
def test_every_language_has_its_own_still_here_line(language: str) -> None:
    lines = prompts.spoken(language, name=NAME)
    assert lines.still_here and (language == "en" or lines.still_here != "I'm still here.")
    if language not in ("ja", "ko", "ru"):
        assert NAME in lines.take_your_time


async def test_hearing_trouble_and_a_lost_answer_in_another_language() -> None:
    french = prompts.spoken("fr", name=NAME)
    async with Harness(["c'est quoi le programme?"], language="fr", turn_timeout_s=0.3, delay=5.0) as h:
        await h.connected()
        await h.quiet_after(french.greeting)
        h.helper.caller_says(speech(0.8))
        last = french.lost.split(". ")[-1]  # spoken sentence by sentence
        await h.quiet_after(last)  # the answer took too long: her own line why (review 0.9.7, C18)
        assert (last, "fr") in said_in(h) and french.again not in h.voice.spoken
        h.voice.fail_transcribe = True
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.voice.spoken.count(french.again) == 1)  # hearing trouble


@pytest.mark.parametrize(
    ("text", "voiced_s", "noise"),
    [
        ("Привет, как дела?", 1.0, False),  # Cyrillic is words, not a sound
        ("今日の予定は？", 1.0, False),
        ("오늘 일정이 뭐야?", 1.0, False),
        ("Wat staat er vandaag op mijn agenda?", 1.5, False),
        ("Ja.", 0.3, False),
        ("Untertitel im Auftrag des ZDF, 2021", 2.0, True),  # Whisper's well-known noise in other languages
        ("Ondertiteld door de Amara.org gemeenschap", 2.0, True),
        ("ご視聴ありがとうございました", 2.0, True),
        ("Продолжение следует...", 2.0, True),
        # a subtitle credit is never speech, however long (the cap on never-said phrases is for sign-offs)
        ("Sous-titres réalisés par la communauté d'Amara.org", 2.5, True),
        ("Untertitel im Auftrag des ZDF für funk, 2017", 2.5, True),
        ("Sottotitoli e revisione a cura di QTSS", 2.5, True),
        ("Merci.", 0.3, True),
        ("Danke.", 0.3, True),
    ],
)
def test_words_in_other_languages_are_words(text: str, voiced_s: float, noise: bool) -> None:
    from local_assistant.calls import is_noise_transcript, real_words

    assert is_noise_transcript(text, voiced_s) is noise
    assert real_words(text) or noise


@pytest.mark.parametrize("said", ["Wacht!", "Подожди", "Stopp", "attends", "これは何ですか", "Maar.", "でも"])
def test_talking_over_her_in_another_language_counts(said: str) -> None:
    from local_assistant.calls import is_interruption

    assert is_interruption(said, name=NAME)


@pytest.mark.parametrize(
    ("said", "code", "acknowledges"),
    [
        ("Okay.", "en", True),  # live 14 Oct: a listener's "okay" ended her answer
        ("Yeah.", "en", True),
        ("Mm-hmm.", "en", True),
        ("Mm-hm.", "en", True),
        ("Uh huh.", "en", True),
        ("Uh-huh, yeah.", "en", True),
        ("Yeah, yeah, right.", "en", True),
        ("Oh, I see.", "en", True),  # a filler may come with it
        ("All right, got it.", "en", True),
        ("Okay, and tomorrow?", "en", False),  # anything more is a turn
        ("Yes, but what about Friday?", "en", False),
        ("But.", "en", False),
        ("Um.", "en", False),  # a filler alone is no words (she carries on anyway), not an acknowledgement
        ("", "en", False),
        ("Ja.", "en", False),  # each language's own words
        ("Ja, klopt.", "nl", True),
        ("Oké, prima.", "nl", True),
        ("Ja, en morgen?", "nl", False),
        ("はい", "ja", True),
        ("うん、うん。", "ja", True),
        ("はい、なるほど。", "ja", True),
        ("はい、明日は？", "ja", False),
        ("Sí, claro.", "es", True),
        ("D’accord.", "fr", True),
        ("Alles klar.", "de", True),
        ("네, 알겠어요.", "ko", True),
        # review 14 Oct: asked back, the same words mean "pardon?" or "really?"
        ("네?", "ko", False),
        ("はい？", "ja", False),
        ("Right?", "en", False),
        ("Okay?", "en", False),
        ("Klopt?", "nl", False),
    ],
)
def test_an_acknowledgement_is_only_the_calls_acknowledgement_words(said: str, code: str, acknowledges: bool) -> None:
    from local_assistant.calls import is_acknowledgement

    assert is_acknowledgement(said, code) is acknowledges


@pytest.mark.parametrize(
    ("said", "code"),
    [("Klar.", "de"), ("Richtig.", "de"), ("Ja, klar.", "de"), ("Très bien.", "fr"), ("Bien sûr.", "fr"),
     ("Tá.", "pt"), ("Entendi.", "pt"), ("Ya.", "es"), ("Угу.", "ru"), ("No tak.", "pl"), ("Dobra.", "pl"),
     ("맞아요.", "ko"), ("Ok.", "nl"), ("Okay.", "nl"), ("Ok.", "it"), ("Okay, va bene.", "it")],
)  # fmt: skip
def test_more_listener_words_carry_on(said: str, code: str) -> None:
    """Owner, 15 Oct: the common acknowledgements of each language, so a German "klar" or a French "bien sûr" over
    her answer lets her carry on, as an English "okay" does; asked back, the same words still take over."""
    from local_assistant.calls import is_acknowledgement, is_interruption

    assert is_acknowledgement(said, code)
    assert not is_interruption(said, name=NAME, language=code)
    assert is_interruption(said.rstrip(".") + "?", name=NAME, language=code)  # "Richtig?": asked back
    assert is_interruption(f"{said.rstrip('.')}, aber morgen?", name=NAME, language=code)  # anything more


@pytest.mark.parametrize(
    ("said", "code"),
    [("네?", "ko"), ("はい？", "ja"), ("Right?", "en"), ("Yes?", "en"), ("Really?", "en"), ("Klopt?", "nl"),
     ("Stimmt?", "de"), ("Да?", "ru"), ("Sì?", "it")],
)  # fmt: skip
def test_one_word_asked_back_over_her_takes_over(said: str, code: str) -> None:
    from local_assistant.calls import is_interruption

    assert is_interruption(said, name=NAME, language=code)


@pytest.mark.parametrize(
    ("hers", "asking"),
    [
        (("明日にしますか。",), True),  # a polite Japanese question ends in か, often without a question mark
        (("明日にしますか",), True),
        (("晴れです。", "明日にしますか。"), True),
        (("晴れです。",), False),
        (("It is sunny.",), False),
        (("Shall I?",), True),
    ],
)
def test_her_sentence_asks_with_a_question_mark_or_a_japanese_ka(hers: tuple[str, ...], asking: bool) -> None:
    assert asks(hers) is asking


@pytest.mark.parametrize(
    ("said", "code"), [("Okay.", "en"), ("Yeah.", "en"), ("Mm-hmm.", "en"), ("Ja.", "nl"), ("はい", "ja")]
)
def test_an_acknowledgement_alone_carries_on_unless_she_asked(said: str, code: str) -> None:
    from local_assistant.calls import is_interruption

    assert not is_interruption(said, name=NAME, language=code)
    assert is_interruption(said, name=NAME, language=code, asking=True)  # the answer to her question


@pytest.mark.parametrize("said", ["Yes.", "Ja.", "Давай.", "Oui"])
def test_a_yes_word_alone_stops_her_only_when_she_asked(said: str) -> None:
    from local_assistant.calls import is_interruption

    assert not is_interruption(said, name=NAME)
    assert is_interruption(said, name=NAME, asking=True)


@pytest.mark.parametrize(
    "said",
    ["But.", "but", "Aber", "Mais", "Pero", "Mas", "Ma", "Но", "Ale", "하지만", "그런데", "Stop.", "Wait", "No.", "Hold",
     "Sorry?", "Hey", NAME, "Okay, and tomorrow?", "Yes but"],
)  # fmt: skip
def test_but_alone_stops_her_like_wait(said: str) -> None:
    from local_assistant.calls import is_interruption

    assert is_interruption(said, name=NAME)


@pytest.mark.parametrize("said", ["Ähm", "ん", "-", "euh"])
def test_a_sound_in_another_language_does_not_interrupt(said: str) -> None:
    from local_assistant.calls import is_interruption

    assert not is_interruption(said, name=NAME)


def test_the_acknowledgement_is_a_short_got_it() -> None:
    # owner 2026-10-11: "Got it." and a few variations. Kokoro garbled "Hmm.", "Mhm.", "Mm-hm." and "Uh-huh." (Whisper
    # heard its clips as "Emily.", "Me have.", "Eh hi."). Owner 2026-10-16: "a bit repetitive", "lets not do ok": ten,
    # most without "got it", none with "okay"; each came back exactly, 3 of 3, in af_heart at 1.05 ("Alright" as
    # "All right")
    assert prompts.CALL_ACK_LINES == (
        "Got it.",
        "Gotcha.",
        "Sure.",
        "Right.",
        "Alright.",
        "Understood.",
        "On it.",
        "Sure thing.",
        "Alright, got it.",
        "Right, got it.",
    )
    assert not any("okay" in line.lower() for line in prompts.CALL_ACK_LINES)


async def test_no_acknowledgement_on_two_turns_in_a_row_and_never_one_of_the_last_two() -> None:
    """Owner, 16 Oct: the "Got it" was repetitive. A request right after an acknowledged one gets none; a new one is
    neither of the last two said."""
    said = [f"please tell me what is on number {n}" for n in range(5)]
    answers = [f"Answer {n}." for n in range(5)]
    async with Harness(said, [Turn(a) for a in answers], acknowledge=True, rng=random.Random(3)) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        for answer in answers:
            h.helper.caller_says(speech(1.2))
            await h.quiet_after(answer)
    spoken = h.manager.said[1:]
    acks = [line for line in spoken if line in prompts.CALL_ACK_LINES]
    assert len(acks) == 3  # turns 1, 3 and 5
    assert spoken == [acks[0], "Answer 0.", "Answer 1.", acks[1], "Answer 2.", "Answer 3.", acks[2], "Answer 4."]
    assert acks[1] != acks[0] and acks[2] not in acks[:2]


async def test_a_short_turn_between_two_requests_lets_the_second_be_acknowledged() -> None:
    said = ["what is on my calendar tomorrow", "thanks", "and what about the day after"]
    answers = ["Two meetings.", "You're welcome.", "Nothing."]
    async with Harness(said, [Turn(a) for a in answers], acknowledge=True) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        for answer in answers:
            h.helper.caller_says(speech(1.2))
            await h.quiet_after(answer)
    spoken = h.manager.said[1:]
    assert spoken[0] in prompts.CALL_ACK_LINES and spoken[1:3] == ["Two meetings.", "You're welcome."]
    assert spoken[3] in prompts.CALL_ACK_LINES and spoken[4] == "Nothing."


async def test_a_dutch_call_acknowledges_a_long_turn_in_dutch_and_a_short_one_not_at_all() -> None:
    turns = [Turn("Twee afspraken."), Turn("Goed, dank je.")]
    said = ["wat staat er morgen op mijn agenda", "hoe gaat het"]
    async with Harness(said, turns, language="nl", acknowledge=True) as h:
        await h.connected()
        await h.quiet_after(prompts.spoken("nl", name=NAME).greeting)
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Twee afspraken.")
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Goed, dank je.")
    acknowledged = h.manager.said[1]
    assert acknowledged in prompts.call_lines("nl", name=NAME).acknowledge and (acknowledged, "nl") in said_in(h)
    assert h.manager.said[2:] == ["Twee afspraken.", "Goed, dank je."]  # nothing before the short turn's answer


async def test_an_empty_first_try_gets_an_acknowledgement_at_once_while_the_retry_runs() -> None:
    """Research, 17 Oct: a first try that came back empty cost a caller about 10 s of silence, then the retry's 2 to
    10 s. When the call is told of the retry, she says one of the acknowledgements at once (the same picker), before
    the answer. A short turn ("how are you?") gets none at its start, so this is its only one."""
    async with Harness(["How are you?"], [Turn("Fine, thanks.")], acknowledge=True, fillers=False) as h:
        h.host.empty_first = {0}
        h.host.retry_gate = asyncio.Event()
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.2))
        await h.until(lambda: len(h.manager.said) == 2)  # said while the retry still runs
        assert h.manager.said[1] in prompts.CALL_ACK_LINES and h.host.retry_gate is not None
        h.host.retry_gate.set()
        await h.quiet_after("Fine, thanks.")
    assert h.manager.said[1:] == [h.manager.said[1], "Fine, thanks."]


async def test_a_turn_acknowledged_at_its_start_gets_no_second_one_for_its_retry() -> None:
    said = "What's on my calendar for tomorrow?"
    async with Harness([said], [Turn("Two meetings.")], acknowledge=True, fillers=False) as h:
        h.host.empty_first = {0}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Two meetings.")
    spoken = h.manager.said[1:]
    assert [line in prompts.CALL_ACK_LINES for line in spoken] == [True, False]


async def test_the_retrys_acknowledgement_keeps_the_rule_of_never_two_turns_in_a_row() -> None:
    """A request right after an acknowledged one gets none, at its start or for its retry; a retry's counts too."""
    said = ["please tell me what is on today", "and then?", "please tell me what is on tomorrow"]
    answers = ["Answer 0.", "Answer 1.", "Answer 2."]
    async with Harness(said, [Turn(a) for a in answers], acknowledge=True, fillers=False) as h:
        h.host.empty_first = {1, 2}
        await h.connected()
        await h.quiet_after(GREETING)
        for answer in answers:
            h.helper.caller_says(speech(1.2))
            await h.quiet_after(answer)
    spoken = h.manager.said[1:]
    # turn 0: its start; turn 1 (empty, right after it): none; turn 2 (empty, a request): at its start, none again
    assert [line in prompts.CALL_ACK_LINES for line in spoken] == [True, False, False, True, False]


async def test_a_retry_after_an_unacknowledged_turn_lets_the_next_request_go_without_one() -> None:
    said = ["how are you?", "please tell me what is on tomorrow"]
    answers = ["Fine.", "Two meetings."]
    async with Harness(said, [Turn(a) for a in answers], acknowledge=True, fillers=False) as h:
        h.host.empty_first = {0}
        await h.connected()
        await h.quiet_after(GREETING)
        for answer in answers:
            h.helper.caller_says(speech(1.2))
            await h.quiet_after(answer)
    spoken = h.manager.said[1:]
    assert [line in prompts.CALL_ACK_LINES for line in spoken] == [True, False, False]  # the retry's, then none


@pytest.mark.parametrize("use_tools", [False, True], ids=["thinking", "checking"])
async def test_no_retry_acknowledgement_after_a_thinking_or_checking_line(use_tools: bool) -> None:
    """Review of 17 Oct (M1): with fillers on, a line said while the first try ran ("Let me think.", or "Let me check."
    after a tool call) already told the caller she is on it; an acknowledgement after it ("Let me think." then
    "Sure.") comes in the wrong order, so none follows."""
    async with Harness(
        ["How are you?"],
        [Turn("Fine, thanks.")],
        delay=1.0,
        use_tools=use_tools,
        acknowledge=True,
        fillers=True,
        thinking_after_s=0.3,
    ) as h:
        h.host.empty_first = {0}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Fine, thanks.")
    spoken = h.manager.said[1:]
    assert spoken[0] in (prompts.CALL_CHECKING_LINES if use_tools else prompts.CALL_THINKING_LINES)
    assert spoken[1:] == ["Fine, thanks."]


async def test_no_retry_acknowledgement_with_acknowledgements_off() -> None:
    async with Harness(["How are you?"], [Turn("Fine.")], acknowledge=False, fillers=False) as h:
        h.host.empty_first = {0}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Fine.")
    assert h.manager.said[1:] == ["Fine."]


async def test_no_retry_acknowledgement_for_the_answer_to_a_device_question() -> None:
    question = "The heater needs your yes. Say yes if you want me to switch it off now."
    last = "Say yes if you want me to switch it off now."
    turns = [Turn(question, reply_id=3, asks_yes=True), Turn("Done: heater off.")]
    said = ["switch the heater off", "Yes."]
    async with Harness(said, turns, acknowledge=True, fillers=False) as h:
        h.host.empty_first = {1}
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        h.helper.caller_says_after(lambda: last in h.manager.said, 30, speech(0.4))
        await h.quiet_after("Done: heater off.")
    spoken = h.manager.said
    assert spoken[spoken.index(last) + 1] == "Done: heater off."


async def test_no_acknowledgement_before_the_answer_to_a_device_question() -> None:
    question = "The heater needs your yes. Say yes if you want me to switch it off now."
    last = "Say yes if you want me to switch it off now."
    turns = [Turn(question, reply_id=3, asks_yes=True), Turn("Done: heater off.")]
    said = ["please switch the heater off now", "Yes, please switch it off now."]  # both long enough for one
    async with Harness(said, turns, acknowledge=True, fillers=False) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        h.helper.caller_says_after(lambda: last in h.manager.said, 30, speech(0.4))
        await h.quiet_after("Done: heater off.")
    said = h.manager.said
    assert said[1] in prompts.CALL_ACK_LINES  # the request got one
    assert said[said.index(last) + 1] == "Done: heater off."  # the yes did not: the answer comes at once


async def test_no_thinking_line_right_after_the_acknowledgement() -> None:
    # review 2026-10-11: the thinking timer ran while she said the short "Hmm.", so a thinking line followed at once

    def call_time() -> float:  # moves only while she says the short line: a second each, past thinking_after_s
        return float(sum(line in prompts.CALL_ACK_LINES for line in h.manager.said))

    said = ["give me an idea for dinner"]
    h = Harness(said, [Turn("Fine.")], acknowledge=True, thinking_after_s=0.5, clock=call_time)
    h.host.gate = asyncio.Event()
    async with h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: any(line in prompts.CALL_ACK_LINES for line in h.manager.said))
        h.host.gate.set()
        await h.quiet_after("Fine.")
    assert h.manager.said[1] in prompts.CALL_ACK_LINES and h.manager.said[2:] == ["Fine."]  # nothing in between


async def test_a_failing_acknowledgement_abandons_the_turn() -> None:
    # review 2026-10-11: the short line was said outside the turn's try: a voice failure left the answer running
    # after the call, with its device question
    async with Harness(["please switch the heater off now"], delay=30.0, acknowledge=True) as h:
        h.voice.broken = set(prompts.CALL_ACK_LINES)
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.ended == 1)
        assert h.host.cancelled_turns == 1 and h.host.dropped_waiting == 1


async def test_a_call_that_ends_during_an_answer_leaves_a_note_that_it_was_not_done() -> None:
    async with Harness(["switch the lights off"], delay=30.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.turns == ["switch the lights off"])
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        await h.until(lambda: len(h.host.notes) == 2)
        assert h.host.notes == [prompts.CALL_UNFINISHED, "(chords)"]  # then code's chords message (none, here)


def test_no_spoken_line_is_a_hum_the_voice_garbles() -> None:
    """Kokoro turns "Hmm.", "Mm, okay." and "Mm-hm." into a vague sound (Whisper heard "Emily."): no code-written
    line she says on a call may contain one."""
    hum = re.compile(r"\b(h?m+|mh?m|m+-hm+|uh-huh)\b", re.IGNORECASE)
    spoken = [
        *prompts.CALL_THINKING_LINES,
        *prompts.CALL_CHECKING_LINES,
        *prompts.CALL_ACK_LINES,
        *prompts.call_greetings(NAME),
    ]
    assert [line for line in spoken if hum.search(line)] == []


# calls she places (a wake-up call or a call reminder): the reminders' scheduler sends the text first


async def placing(h: "Harness", *, wake: bool = True) -> "asyncio.Task[str]":
    task = asyncio.create_task(h.manager.place(ME, wake=wake, time="7:00", text="wake up"))
    await h.until(lambda: h.signal.started == [ME])
    return task


async def test_a_placed_call_starts_only_when_picked_up_and_opens_with_the_wake_line() -> None:
    async with Harness(["thanks, I'm up"], [Turn("Great, have a good day!")]) as h:
        task = await placing(h)
        await asyncio.sleep(0.2)
        assert not task.done() and h.host.started == []  # ringing: texts are not held yet
        await h.signal.pick_up()
        assert await task == "answered"
        assert h.host.started == [ME]
        opening = "Good morning! It's 7:00. wake up"
        await h.quiet_after(opening)
        assert GREETING not in h.voice.spoken  # the opening instead of the greeting
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Great, have a good day!")
        assert h.host.turns == ["thanks, I'm up"]


async def test_a_call_reminder_opens_with_why_she_calls() -> None:
    async with Harness() as h:
        task = asyncio.create_task(h.manager.place(ME, wake=False, time="17:00", text="call the dentist"))
        await h.until(lambda: h.signal.started == [ME])
        await h.signal.pick_up()
        assert await task == "answered"
        await h.quiet_after("It's 17:00. You asked me to call you. call the dentist")


async def test_the_opening_is_said_in_the_calls_language() -> None:
    async with Harness(language="nl") as h:
        task = await placing(h)
        await h.signal.pick_up()
        await task
        await h.quiet_after("Goedemorgen! Het is 7:00. wake up")


@pytest.mark.parametrize(
    ("reason", "outcome"),
    [
        ("ring_timeout", "no_answer"),
        ("Timeout", "no_answer"),
        ("remote_hangup", "declined"),
        ("RemoteHangup", "declined"),
        ("remote_busy", "busy"),
        ("tunnel_exit", "call_failed"),
        ("tunnel_spawn_error", "call_failed"),
        ("shutdown", "call_failed"),
    ],
)
async def test_a_call_that_is_not_picked_up_ends_with_its_outcome(reason: str, outcome: str) -> None:
    async with Harness() as h:
        task = await placing(h)
        h.signal.end_ringing(reason)
        assert await task == outcome
        assert h.host.started == [] and h.host.ended == 0 and h.host.notes == []  # no call notes for no call
        assert not h.manager.in_call


async def test_a_signal_cli_restart_while_ringing_is_a_failed_call() -> None:
    async with Harness() as h:
        task = await placing(h)
        h.signal.end_ringing("signal_cli_restarted", call_id=None)
        assert await task == "call_failed"


async def test_no_end_in_time_hangs_up_and_is_a_failed_call() -> None:
    async with Harness(ring_wait_s=0.3) as h:
        task = await placing(h)
        assert await task == "call_failed"
        assert h.signal.hung_up == [(ME, OUT)]


async def test_a_startcall_error_is_a_failed_call() -> None:
    async with Harness() as h:
        h.signal.start_error = RuntimeError("signal-cli refused")
        assert await h.manager.place(ME, wake=True, time="7:00", text="wake up") == "call_failed"


async def test_she_never_dials_an_account_that_is_not_allowed() -> None:
    async with Harness() as h:
        assert await h.manager.place(STRANGER, wake=True, time="7:00", text="x") == "call_failed"
        assert h.signal.started == []


async def test_whether_a_call_can_be_placed() -> None:
    async with Harness(ready=False) as h:
        assert await h.manager.can_place(ME) == "not_ready"
        assert await h.manager.place(ME, wake=True, time="7:00", text="x") == "call_failed"
        assert h.signal.started == []
    async with Harness() as h:
        assert await h.manager.can_place(ME) == ""
        await h.connected()  # the user called her
        assert await h.manager.can_place(ME) == "in_call"


async def test_an_incoming_call_while_she_dials_is_ignored() -> None:
    async with Harness() as h:
        task = await placing(h)
        h.signal.ring()
        await asyncio.sleep(0.2)
        assert h.signal.accepted == []
        h.signal.end_ringing("ring_timeout")
        assert await task == "no_answer"


async def test_stopping_the_bridge_hangs_up_a_ringing_call() -> None:
    async with Harness() as h:
        task = await placing(h)
        await asyncio.sleep(0.1)  # the call id is known
        await h.manager.hangup()
        assert h.signal.hung_up == [(ME, OUT)]
        h.signal.end_ringing("local_hangup")
        assert await task == "call_failed"


async def test_a_minute_of_silence_after_the_wake_line_ends_the_call_with_a_goodbye() -> None:
    async with Harness(wake_silence_s=0.5) as h:
        task = await placing(h)
        await h.signal.pick_up()
        await task
        await h.until(lambda: h.signal.hung_up == [(ME, OUT)])
        assert any(line in h.voice.spoken for line in prompts.CALL_BYE_LINES)
        assert prompts.CALL_IDLE not in h.voice.spoken


async def test_words_over_the_wake_line_answer_it_so_a_silence_after_gets_the_check_in() -> None:
    """The review of 28 Oct (calls-04): the caller talked over "Good morning!", was answered, then went quiet; that
    silence ended the call with the goodbye a silent wake line gets, never the check-in. Real words over the wake line
    answer it, as words over her line about a text do: a silence after them is an ordinary call's."""
    async with Harness(["I'm up already"], [Turn("Good to hear.")], wake_silence_s=0.5, idle_prompt_s=1.0) as h:
        task = await placing(h)
        await h.signal.pick_up()
        await task
        h.helper.caller_says_when(lambda: h.helper.her_frames() > 30, speech(1.0))  # over the opening
        await h.quiet_after("Good to hear.")
        await h.until(lambda: CHECK_IN in h.manager.said)
        assert h.host.turns == [f"{prompts.CUTTING_IN} I'm up already"] and h.signal.hung_up == []
        assert not any(line in h.manager.said for line in prompts.CALL_BYE_LINES)


@pytest.mark.parametrize("hello", ["Hello?", "Hallo?", "Hey, hello.", f"Hello, {NAME}?"])
async def test_a_callees_hello_over_the_opening_resumes_it(hello: str, caplog: pytest.LogCaptureFixture) -> None:
    """The second deep scan (calls-hello-over-opening): on a call she places, the callee's "Hello?" over its opening
    counted as one word asked back and took over, so the time and the reminder were never said on the call. Greeting
    or calling words alone over the opening resume it and are no turn; what the callee says after it is a turn."""
    calls_logs(caplog)
    opening = "Good morning! It's 7:00. wake up"
    async with Harness([hello, "I'm up already"], [Turn("Good to hear.")], wake_silence_s=5.0) as h:
        task = await placing(h)
        await h.signal.pick_up()
        await task
        h.helper.caller_says_when(lambda: h.helper.her_frames() > 30, speech(1.0))  # over the opening
        h.helper.caller_says_after(lambda: opening in h.manager.said, 20, speech(1.0))
        await h.quiet_after("Good to hear.")
    assert h.host.turns == ["I'm up already"]  # the hello was no turn, and the words after the opening no cut-in
    lines = log_lines(caplog)
    assert "calls: a greeting over her opening; carrying on" in lines
    assert not [line for line in lines if line.startswith("calls: taken over")]


@pytest.mark.parametrize(
    ("said", "code", "greeting"),
    [
        ("Hello?", "en", True),
        ("Hallo?", "nl", True),
        ("Allô ?", "fr", True),
        ("もしもし？", "ja", True),
        ("Hello, Tiffany?", "en", True),
        ("Tiffany?", "en", False),  # her name alone calls no one over the opening
        ("Hello? Who is this?", "en", False),
        ("Wait.", "en", False),
    ],
)
def test_only_greeting_or_calling_words(said: str, code: str, greeting: bool) -> None:
    assert calls.only_greeting(said, name="Tiffany", language=code) is greeting


async def test_more_than_a_greeting_over_the_opening_takes_over() -> None:
    async with Harness(["Hello? Who is this?"], [Turn("It's your wake-up call.")], wake_silence_s=5.0) as h:
        task = await placing(h)
        await h.signal.pick_up()
        await task
        h.helper.caller_says_when(lambda: h.helper.her_frames() > 30, speech(1.0))
        await h.quiet_after("It's your wake-up call.")
    assert h.host.turns == [f"{prompts.CUTTING_IN} Hello? Who is this?"]


async def test_the_status_line_shows_the_next_call() -> None:
    async with Harness() as h:
        assert h.manager.status_line(next_call="Tue 13 Oct 07:00") == "Calls: ready · next call Tue 13 Oct 07:00"


WEATHER = "The weather in Amsterdam stays sunny and warm until Friday."


@pytest.mark.parametrize(
    ("heard", "played", "own"),
    [
        # her echo: her sentence, or a long part of it, in her order
        (WEATHER, [WEATHER], True),
        ("weather in amsterdam stays sunny", [WEATHER], True),
        ("um, the weather in Amsterdam stays sunny", [WEATHER], True),  # fillers left out
        ("the weather in amsterdam stays sunny and warm till friday", [WEATHER], True),  # one word heard otherwise
        ("the train leaves from platform four", ["The train leaves from platform four.", "It is on time."], True),
        ("Don’t worry, the train is running late tonight", ["Don't worry, the train is running late tonight."], True),
        ("esta bien gracias por tu paciencia hoy", ["Está bien, gracias por tu paciencia hoy."], True),  # accents
        ("le train part à neuf heures ce soir", ["Le train part à neuf heures ce soir."], True),
        ("Привет как дела у тебя сегодня", ["Привет, как дела у тебя сегодня?"], True),
        ("今日は晴れです", ["今日は晴れですね。"], True),  # Japanese, without spaces: by characters
        # the caller (review of 13 Oct): a few of her words are no echo
        ("the dentist at three", ["You see the dentist at three tomorrow."], False),  # two real words of hers
        ("turn it off", ["Should I turn the heater off?"], False),
        ("no, not Amsterdam", ["We land at nine tonight in Amsterdam."], False),
        ("wait, it is at five", ["The meeting is at five, it is in the big room."], False),
        ("wait, the meeting is at five in the big room", ["The meeting is at five in the big room."], False),
        ("no the weather in amsterdam stays sunny", [WEATHER], False),  # a no she did not say
        ("yes the weather in amsterdam stays sunny", [WEATHER], False),
        ("no, está bien gracias por tu paciencia", ["Está bien, gracias por tu paciencia hoy."], False),
        ("non le train part à neuf heures", ["Le train part à neuf heures ce soir."], False),
        ("nein der Zug fährt um neun Uhr ab", ["Der Zug fährt um neun Uhr ab."], False),
        ("sunny Amsterdam weather", [WEATHER], False),  # her words, not in her order
        ("weather in amsterdam", [WEATHER], False),  # two words of hers
        ("the weather in amsterdam stays sunny but what about rome and paris next week", [WEATHER], False),  # half
        ("what about rain on friday afternoon", ["Tomorrow looks dry,"], False),  # words she has not said yet
        ("Answer.", [WEATHER], False),
        ("", [WEATHER], False),
        ("はい", ["はい、今日は晴れです。"], False),  # one word of two characters
        ("明日の予定を教えて", ["はい、今日は晴れです。"], False),
        ("待って、今日は晴れです", ["今日は晴れですね。"], False),  # a stop word she did not say
        ("今日は晴れです", ["今日は"], False),  # not said yet
        # review of 0.9.7 (C9): a repeat-back asked is the caller's, unless what she played asked too
        ("So the weather in Amsterdam stays sunny?", [WEATHER], False),
        ("the weather in Amsterdam stays sunny and warm?", [WEATHER], False),
        ("今日は晴れですか？", ["今日は晴れですね。"], False),
        ("Should I turn the heater off now?", ["Should I turn the heater off now?"], True),  # her question's echo
        ("Привет, как дела у тебя сегодня?", ["Привет, как дела у тебя сегодня?"], True),
    ],
)
def test_her_own_voice_is_a_long_part_of_what_she_played(heard: str, played: list[str], own: bool) -> None:
    assert is_own_voice(heard, played) is own


@pytest.mark.parametrize(
    ("hers", "fraction", "played"),
    [
        (("Ten past four.", "The weather in Amsterdam is sunny today."), 0.5, ("Ten past four.", "The weather in")),
        (("The weather is fine.",), 11 / 20, ("The weather",)),  # cut at a word's end: the word is said
        (("The weather is fine.",), 1.0, ("The weather is fine.",)),
        (("今日は晴れですね。",), 0.5, ("今日は晴",)),  # Japanese by characters
        ((), 0.5, ()),
    ],
)
def test_what_she_played_is_the_sentence_before_and_this_one_up_to_the_stop(
    hers: tuple[str, ...], fraction: float, played: tuple[str, ...]
) -> None:
    assert played_part(hers, fraction) == played


async def test_her_own_voice_over_her_answer_only_pauses_her(caplog: pytest.LogCaptureFixture) -> None:
    # a loud speaker next to the caller's microphone (13 Oct): her own words came back and stopped her, and she took
    # them for the caller's turn
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    answer = f"{WEATHER} " * 6
    async with Harness(["tell me more", "weather in amsterdam stays sunny"], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 250, speech(1.0))  # her echo
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 2)  # she stopped and listened to her own voice
        await h.until(lambda: h.manager.said.count(WEATHER) == 6)  # then carried on to the end
        assert h.host.interruptions == [] and h.host.turns == ["tell me more"]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    lines = [record.getMessage() for record in caplog.records]
    assert "calls: her own voice heard; carrying on" in lines
    assert any(line.startswith("calls: barge-in loudness") and "her own voice 1" in line for line in lines)


async def test_her_words_she_has_not_said_yet_are_the_callers() -> None:
    # the caller asks about the end of her sentence while she is at its start: no echo of hers
    sentence = "Tomorrow looks dry, but rain comes back on Friday afternoon, and it stays wet over the weekend."
    async with Harness(
        ["tell me more", "what about rain on friday afternoon"], [Turn(sentence, reply_id=5), Turn("Okay.")]
    ) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 30, speech(1.0))
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Okay.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} what about rain on friday afternoon"]


async def test_after_her_own_voice_a_caller_as_loud_as_it_no_longer_stops_her() -> None:
    answer = f"{WEATHER} " * 8
    async with Harness(["tell me more", "weather in amsterdam stays sunny"], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 250, speech(1.0))  # her echo
        h.helper.caller_says_when(lambda: h.voice.heard == 2, noise(0.5) + speech(1.0))  # as loud, after it
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.manager.said.count(WEATHER) == 8)
        assert h.voice.heard == 2  # the second time it did not stop her: no third transcription


async def test_a_takeover_after_her_own_voice_lowers_the_raised_level_again(caplog: pytest.LogCaptureFixture) -> None:
    """Review of 0.9.7 (C9): a caller who repeats back what she said can be taken for her own voice, and the level a
    stop needs was raised to 1.5 times the caller's own for the rest of the call. A takeover by the caller's words
    (louder than the raise) lowers it again: words at the caller's own level in her next answer stop her."""
    calls_logs(caplog)
    answer = f"{WEATHER} " * 8
    heard = ["tell me more", "weather in amsterdam stays sunny", "wait, what about rome", "wait, and tomorrow"]
    turns = [Turn(answer, reply_id=5), Turn(answer, reply_id=6), Turn("Okay.")]
    async with Harness(heard, turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 250, speech(1.0))  # taken for hers
        h.helper.caller_says_when(lambda: h.voice.heard == 2, noise(0.5) + speech(1.0, level=4800))  # louder: over
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: len(h.host.turns) == 2)
        h.helper.caller_says_after(lambda: len(h.host.turns) == 2, 150, speech(1.0))  # her next answer, as before
        await h.quiet_after("Okay.")
        assert h.host.turns == [
            "tell me more",
            f"{prompts.CUTTING_IN} wait, what about rome",
            f"{prompts.CUTTING_IN} wait, and tomorrow",
        ]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    assert "calls: her own voice heard; carrying on" in log_lines(caplog)


async def test_her_own_voice_never_reaches_gemma_even_after_three_false_alarms() -> None:
    # after MAX_FALSE_BARGE false alarms in one sentence the next stop is taken as the caller's: her own words never are
    answer = f"{WEATHER} " * 30  # her sentences, and an echo that stops her four times in a row
    echo = ["weather in amsterdam stays sunny"] * 4
    async with Harness(["tell me more", *echo, "what time is it"], [Turn(answer, reply_id=5), Turn("Four.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)

        greeting = h.helper.her_frames()

        def heard(times: int) -> Callable[[], bool]:
            return lambda: h.voice.heard == times

        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 250, speech(1.0))  # in her 2nd sentence
        for times in range(2, 5):  # louder than the level raised after the first (raised once, never again)
            h.helper.caller_says_when(heard(times), noise(0.4) + speech(1.0, level=4800))
        h.helper.caller_says(speech(1.0))  # "tell me more"
        await h.until(lambda: h.voice.heard == 5)
        h.helper.caller_says(noise(0.5) + speech(1.0))
        await h.quiet_after("Four.")
        assert h.host.turns == ["tell me more", "what time is it"]


async def test_speech_rule_road_noise_over_her_answer_does_not_stop_her(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    answer = "This is a long answer. " * 6
    async with Harness(["tell me more"], [Turn(answer, reply_id=5)], barge="speech") as h:
        h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 100, noise(3.0, 3000))  # the road
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.manager.said.count("This is a long answer.") == 6)
        assert h.voice.heard == 1 and h.host.interruptions == []  # never stopped: nothing to transcribe
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    streams = {stream for stream, _ in h.voice.chunks}
    assert len(streams) == 1 and re.fullmatch(r"[0-9a-f]{32}", streams.pop())  # one detector state for the call
    assert {size for _, size in h.voice.chunks} == {8192}
    lines = [record.getMessage() for record in caplog.records]
    shadow = (
        r"calls: barge-in speech: speech stopped her 0 times, would have 0; loudness stopped her 0 times, would have"
        r" [1-9]\d*; her own voice 0; acknowledgements 0; detector \d+ chunks, slowest \d+ ms, 0 failed"
    )
    assert any(re.fullmatch(shadow, line) for line in lines)  # once in each sentence the road was under


async def test_speech_rule_a_caller_talking_over_her_still_stops_her() -> None:
    long_answer = "This is a long answer. " * 8
    async with Harness(
        ["tell me more", "wait, what about tomorrow"], [Turn(long_answer, reply_id=5), Turn("Okay.")], barge="speech"
    ) as h:
        h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 150, speech(1.2))
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Okay.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} wait, what about tomorrow"]


@pytest.mark.parametrize(
    ("detector", "state"), [(None, "old voice service"), ("loading", "loading"), ("unavailable", "unavailable")]
)
async def test_speech_rule_without_a_detector_loudness_decides_the_call(
    caplog: pytest.LogCaptureFixture, detector: str | None, state: str
) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    async with Harness(barge="speech") as h:
        h.voice.detector = detector
        await h.connected()
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    assert h.voice.chunks == []  # never asked
    lines = [record.getMessage() for record in caplog.records]
    assert f"calls: speech detector {state}; loudness decides this call" in lines
    end = (
        "calls: barge-in speech (loudness: no speech detector): loudness stopped her 0 times, would have 0;"
        " her own voice 0; acknowledgements 0"
    )
    assert end in lines


async def test_speech_rule_a_stop_without_words_counts_what_loudness_did_beside_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # speech stopped her, loudness fired first in the same clip, and the words were none: a false stop both made
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    answer = f"{WEATHER} " * 4
    async with Harness(["tell me more", ""], [Turn(answer, reply_id=5)], barge="speech") as h:
        h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 250, speech(1.0))  # no words in it
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.manager.said.count(WEATHER) == 4)
        assert h.voice.heard == 2 and h.host.turns == ["tell me more"]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    end = [record.getMessage() for record in caplog.records if record.getMessage().startswith("calls: barge-in")]
    assert len(end) == 1
    assert "speech stopped her 1 time, would have 0; loudness stopped her 0 times, would have 1;" in end[0]


async def test_a_call_that_falls_back_to_loudness_midway_counts_each_rule(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    caplog.set_level(logging.INFO, logger="local_assistant.call_audio")
    long_answer = f"{WEATHER} " * 8
    async with Harness(
        ["tell me more", "wait, what about tomorrow", "and the day after"],
        [Turn(long_answer, reply_id=5), Turn(long_answer, reply_id=6), Turn("Okay.")],
        barge="speech",
    ) as h:
        h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 150, speech(1.2))  # speech stops her
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 2)
        h.voice.detector = "unavailable"  # from here every chunk fails: loudness after the third
        second = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > second + 150, speech(1.2))  # loudness stops her
        await h.quiet_after("Okay.")
        assert h.host.turns == [
            "tell me more",
            f"{prompts.CUTTING_IN} wait, what about tomorrow",
            f"{prompts.CUTTING_IN} and the day after",
        ]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    lines = [record.getMessage() for record in caplog.records]
    assert "calls: the speech detector failed 3 times in a row; loudness for the rest of the call" in lines
    end = (
        r"calls: barge-in speech \(loudness after 3 failed answers in a row\): speech stopped her 1 time, would have 0;"
        r" loudness stopped her 1 time, would have 0; her own voice 0; acknowledgements 0; detector \d+ chunks, slowest"
        r" \d+ ms, 3 failed"
        r" \(http 3\)"
    )
    assert any(re.fullmatch(end, line) for line in lines), [line for line in lines if "barge-in" in line]


def test_only_speech_stops_her_by_default_as_the_config_says() -> None:
    """[calls] interrupt = "only speech" is the default (owner, 14 Oct): the call settings' own default is the same."""
    assert (
        CallSettings(socket_dir=Path("/tmp")).barge
        == "speech"
        == Config(bot_number="+306900000000", allowed_acis=frozenset()).calls_barge
    )


async def test_loudness_rule_measures_speech_beside_it_when_the_service_has_a_detector() -> None:
    async with Harness() as h:  # loudness ("any sound"), as the harness sets it
        h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        assert h.voice.chunks  # the caller's audio under the greeting went to the detector: speech is the shadow


async def test_the_status_line_shows_the_speech_detector_when_speech_stops_her() -> None:
    async with Harness(barge="speech") as h:
        h.voice.detector = "ready"
        assert await h.manager.detector() == "ready"
        assert h.manager.status_line(detector="ready") == "Calls: ready · speech detector: ready"
        h.voice.detector = None
        assert await h.manager.detector() == "old voice service"
    async with Harness() as h:
        assert await h.manager.detector() == ""  # any sound (loudness): nothing to show


# /babble: a call during practice is a practice call


async def test_a_practice_call_is_in_the_practised_language_and_ends_practice_instead_of_a_summary() -> None:
    said = ["Ik woon in Utrecht.", "Ik werk bij een bank.", "Ik heb twee katten."]
    turns = [Turn("Waar werk je?"), Turn("Heb je huisdieren?"), Turn("Wat leuk!")]
    async with Harness(said, turns, practice=lambda aci: "nl" if aci == ME else None) as h:
        await h.connected()
        greeting = prompts.spoken("nl", name=NAME).greeting
        assert greeting in h.voice.spoken and h.voice.languages[0] == "nl"
        await h.quiet_after(greeting)
        for answer in ("Waar werk je?", "Heb je huisdieren?", "Wat leuk!"):
            h.helper.caller_says(speech(1.0))
            await h.quiet_after(answer)
        assert set(h.voice.heard_in) == {"nl"} and set(h.voice.languages) == {"nl"}
        assert h.host.practice_turns == [True, True, True]  # the app answers them as practice turns
        assert h.host.started_practice == [True]  # and its start says so (no glance of the day for practice)
        h.signal.remote_hangup()
        await h.until(lambda: bool(h.host.practice_ended))
        await asyncio.sleep(0.1)
        assert h.host.practice_ended == [ME] and h.host.summaries == []  # the recap replaces the summary


async def test_a_practice_call_without_a_turn_leaves_practice_running() -> None:
    async with Harness(practice=lambda aci: "de") as h:
        await h.connected()
        await h.quiet_after(prompts.spoken("de", name=NAME).greeting)
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        await asyncio.sleep(0.1)
        assert h.host.practice_ended == [] and h.host.summaries == []


async def test_a_practice_call_ends_practice_before_its_notes() -> None:
    async with Harness(["Ik ben moe."], delay=30.0, practice=lambda aci: "nl") as h:
        await h.connected()
        await h.quiet_after(prompts.spoken("nl", name=NAME).greeting)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.turns == ["Ik ben moe."])
        h.signal.remote_hangup()
        await h.until(lambda: len(h.host.notes) == 2)
        assert h.host.notes == ["(practice ended)", prompts.CALL_UNFINISHED]


async def test_without_practice_a_call_keeps_its_language_and_summary() -> None:
    async with Harness(
        ["first idea", "second idea"], [Turn("Nice."), Turn("Good one.")], practice=lambda aci: None
    ) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Nice.")
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Good one.")
        h.signal.remote_hangup()
        await h.until(lambda: bool(h.host.summaries))
        assert h.host.practice_ended == [] and set(h.voice.heard_in) == {"en"}


async def test_a_call_she_places_during_practice_is_an_ordinary_call() -> None:
    # a wake-up call or a reminder call: the user's own language, ordinary turns (tools, reminders), its summary, and
    # practice keeps running (only a call the user makes during practice is a practice call)
    said = ["ok, remind me again in 10 minutes", "thanks"]
    async with Harness(said, [Turn("Done."), Turn("Bye!")], practice=lambda aci: "nl") as h:
        task = await placing(h)
        await h.signal.pick_up()
        assert await task == "answered"
        await h.quiet_after("Good morning! It's 7:00. wake up")  # English, as /language says, not Dutch
        for answer in ("Done.", "Bye!"):
            h.helper.caller_says(speech(1.0))
            await h.quiet_after(answer)
        assert h.host.practice_turns == [False, False] and set(h.voice.heard_in) == {"en"}
        assert h.host.started_practice == [False]
        h.signal.remote_hangup()
        await h.until(lambda: bool(h.host.summaries))
        assert h.host.practice_ended == []


# live 14 Oct (12:00 and 12:02): a listener's "okay" or "yeah" over her answer stopped her, the next turn was answered
# as a new request and the rest of her answer was dropped ("she skipped some sections"). An acknowledgement carries
# on; anything more, a "but", or an answer to her question takes over.
@pytest.mark.parametrize(
    ("code", "asked", "first", "second", "heard", "carries_on"),
    [
        ("en", "how is the weather", "It is sunny.", "The whole week stays dry and warm, with a light wind from the west.",
         "Yeah.", True),
        ("en", "how is the weather", "It is sunny.", "The whole week stays dry and warm, with a light wind from the west.",
         "Okay.", True),
        ("en", "is the heater on", "Shall I turn the heater off?",
         "It has been on since noon, and the living room is quite warm by now.", "Yeah.", False),
        ("en", "how is the weather", "It is sunny.", "The whole week stays dry and warm, with a light wind from the west.",
         "But.", False),
        ("en", "how is the weather", "It is sunny.", "The whole week stays dry and warm, with a light wind from the west.",
         "Okay, and tomorrow?", False),
        ("nl", "hoe is het weer", "Het is zonnig.", "De hele week blijft het droog en warm, met een zachte wind uit het westen.",
         "Ja, klopt.", True),
        ("nl", "staat de verwarming aan", "Zal ik de verwarming uitzetten?",
         "Hij staat al sinds de middag aan en de woonkamer is inmiddels behoorlijk warm.", "Ja.", False),
        ("nl", "hoe is het weer", "Het is zonnig.", "De hele week blijft het droog en warm, met een zachte wind uit het westen.",
         "Maar.", False),
        ("nl", "hoe is het weer", "Het is zonnig.", "De hele week blijft het droog en warm, met een zachte wind uit het westen.",
         "Ja, en morgen?", False),
        ("ja", "天気はどう", "晴れです。", "今週はずっと乾燥していて暖かく、西からの穏やかな風が吹き、とても過ごしやすい一週間になりそうです。",
         "はい", True),
        ("ja", "ヒーターはついてる", "ヒーターを消しましょうか？",
         "お昼からずっとついていて、リビングはもうかなり暖かくなっていますので、消してもいいかもしれません。", "うん", False),
        ("ja", "予定はどう", "明日にしますか。",  # a polite question without its question mark
         "今日は午後から雨が降りそうなので、明日の朝のほうが、ゆっくりと買い物ができるかもしれません。", "はい", False),
        ("ja", "天気はどう", "晴れです。", "今週はずっと乾燥していて暖かく、西からの穏やかな風が吹き、とても過ごしやすい一週間になりそうです。",
         "でも", False),
        ("ja", "天気はどう", "晴れです。", "今週はずっと乾燥していて暖かく、西からの穏やかな風が吹き、とても過ごしやすい一週間になりそうです。",
         "はい、明日は？", False),
    ],
)  # fmt: skip
async def test_over_her_answer_an_acknowledgement_carries_on_and_anything_more_takes_over(
    code: str, asked: str, first: str, second: str, heard: str, carries_on: bool
) -> None:
    async with Harness([asked, heard], [Turn(f"{first} {second}", reply_id=5), Turn("Okay.")], language=code) as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: h.manager.saying == second, 40, speech(0.6))  # in her second sentence
        if carries_on:
            await h.until(lambda: h.voice.heard == 2)  # she stopped and listened
            await h.quiet_after(second)  # then carried on to its end
            await asyncio.sleep(0.3)
            assert h.host.turns == [asked] and h.host.interruptions == []
        else:
            await h.until(lambda: len(h.host.turns) == 2)
            assert h.host.turns[1] == f"{prompts.CUTTING_IN} {heard}"
            [(reply_id, said)] = h.host.interruptions
            assert reply_id == 5 and said.startswith(first) and second not in said


# review of 26 Oct (DLG-04): a full answer may ask on its way ("では、自由とは何でしょうか。"), then answers itself; a
# listener's "はい" over the next sentence acknowledges, as over any other sentence of hers. Only a question among her
# answer's last two parts waits for an answer
RHETORICAL = {
    "ja": (
        [
            "自由意志は古い問題です。",
            "決定論はすべてに原因があると言います。",
            "では、自由とは何でしょうか。",
            "両立論者は、自分の理由で選ぶことだと考えます。",
            "非両立論者は、それでは足りないと言います。",
            "私は両立論者の考えに近いです。",
        ],
        "はい",
    ),
    "ko": (
        [
            "자유 의지는 오래된 문제입니다.",
            "결정론은 모든 일에 원인이 있다고 말합니다.",
            "그렇다면 자유란 무엇일까요?",
            "양립론자들은 자기 이유로 고르는 것이라고 봅니다.",
            "비양립론자들은 그것으로는 부족하다고 말합니다.",
            "저는 양립론 쪽에 가깝습니다.",
        ],
        "네",
    ),
    "en": (
        [
            "Free will is an old problem.",
            "Determinism says everything has a cause.",
            "So what is freedom, really?",
            "Compatibilists say it is choosing for your own reasons.",
            "Incompatibilists say that is not enough.",
            "I lean towards the compatibilists.",
        ],
        "Yeah.",
    ),
}


@pytest.mark.parametrize("code", ["ja", "ko", "en"])
async def test_call_backchannel_after_rhetorical(code: str, caplog: pytest.LogCaptureFixture) -> None:
    """A question at part 3 of 6 that she talks on from is rhetorical: an acknowledgement over part 4 carries on, all
    six parts are said, and the caller's turn is no turn of the host's (call_backchannel_after_rhetorical_ja/_ko/_en)."""
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    parts, ack = RHETORICAL[code]
    async with Harness(["free will", ack], [Turn(" ".join(parts), reply_id=5), Turn("Okay.")], language=code) as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: h.manager.saying == parts[3], 40, speech(0.6))  # over part 4
        await h.until(lambda: h.voice.heard == 2)  # she stopped and listened
        await h.quiet_after(parts[5])  # then carried on to her answer's end
        await asyncio.sleep(0.3)
        assert h.host.turns == ["free will"] and h.host.interruptions == []
        assert all(part in h.voice.spoken for part in parts)
    lines = [record.getMessage() for record in caplog.records]
    assert lines.count("calls: an acknowledgement; carrying on") == 1


async def test_call_ack_answers_last_question_en() -> None:
    """The control: her answer's last part asks ("Shall I go on?"), so a "Yeah." over it is the caller's answer."""
    parts = [*RHETORICAL["en"][0][:5], "Shall I go on about where the two camps clash and why I lean their way?"]
    async with Harness(["free will", "Yeah."], [Turn(" ".join(parts), reply_id=5), Turn("Okay.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: h.manager.saying == parts[5], 40, speech(0.6))
        await h.until(lambda: len(h.host.turns) == 2)
        assert h.host.turns[1] == f"{prompts.CUTTING_IN} Yeah."


# review of 14 Oct: under each rule ("only speech", the real default, with its detector, and "any sound"), an
# acknowledgement carries on, is counted in the end line and is never a false stop: loudness, the shadow beside speech,
# fired in that clip too, and its "would have" stays 0
@pytest.mark.parametrize("barge", ["loudness", "speech"])
async def test_an_acknowledgement_is_logged_and_counted_and_a_takeover_logged_by_its_words(
    caplog: pytest.LogCaptureFixture, barge: str
) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    answer = "This is a long answer. " * 8
    transcripts = ["tell me more", "Yeah.", "Okay, and tomorrow?"]
    async with Harness(transcripts, [Turn(answer, reply_id=5), Turn("Rain.")], barge=barge) as h:
        if barge == "speech":
            h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 150, speech(0.6))  # "Yeah."
        h.helper.caller_says_when(lambda: h.voice.heard == 2 and h.helper.her_frames() > greeting + 450, speech(1.0))
        await h.quiet_after("Rain.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} Okay, and tomorrow?"]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    lines = [record.getMessage() for record in caplog.records]
    assert lines.count("calls: an acknowledgement; carrying on") == 1  # no words logged
    assert "calls: taken over (3 words)" in lines
    [end] = [line for line in lines if line.startswith("calls: barge-in")]
    head = rf"calls: barge-in {barge}(?: \(no speech detector\))?: {barge} stopped her 2 times, would have 0;"
    assert re.match(head, end), end
    assert "acknowledgements 1" in end
    if barge == "speech":  # loudness fired before speech in both stops: the acknowledgement's is no false stop
        assert "; loudness stopped her 0 times, would have 0;" in end, end


async def test_a_yeah_over_the_lead_in_to_a_device_question_is_the_callers() -> None:
    # a device question waits (her last sentence, code's question, comes whole): a yes word answers it, so it takes
    # over; cut off before the question, no yes may act on it
    lead = "The heater has been on since noon, and the living room is quite warm by now."
    turn = Turn(lead, reply_id=3, asks_yes=True, code_text="Shall I switch heater off?")
    async with Harness(["is the heater on", "Yeah."], [turn, Turn("Okay.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: h.manager.saying == lead, 40, speech(0.6))
        await h.quiet_after("Okay.")
        assert h.host.turns == ["is the heater on", f"{prompts.CUTTING_IN} Yeah."]
        assert h.host.dropped_waiting == 1


@pytest.mark.parametrize(
    ("said", "code", "leaving"),
    [
        ("I'm hanging up now.", "en", True),
        ("Okay, I'll hang up.", "en", True),
        ("Hanging up now.", "en", True),
        ("I have to go.", "en", True),
        ("I've got to go, bye.", "en", True),
        ("Gotta go!", "en", True),
        ("I need to go now.", "en", True),
        ("Okay, I'm off.", "en", True),
        ("Sorry, I have to go. Talk later.", "en", True),  # closing talk after it
        ("Thanks for the help, I have to go, I'm late.", "en", True),
        ("Okay, I have to go, for now.", "en", True),  # "for now" goes on from it and is soft
        ("I'm off now.", "en", True),
        # review 14 Oct: Whisper writes a hesitation as a comma; a comma part that goes on from the phrase ("to",
        # "naar", "zum") makes it an errand, not leave-taking
        ("I have to go, uh, to the dentist tomorrow.", "en", False),
        ("I need to go, to the pharmacy tomorrow, remind me at nine.", "en", False),
        ("I have to go, back to work, remind me at five.", "en", False),
        ("Ik moet weg, naar de tandarts.", "nl", False),
        ("Ich muss weg, zum Arzt morgen.", "de", False),
        ("Ich muss los, zur Arbeit.", "de", False),
        ("電気、切るね。", "ja", False),  # 切る is also "switch off": only soft words before it in its sentence
        ("あ、エアコン、切るよ。", "ja", False),
        ("He said: I have to go.", "en", False),  # quoted speech
        ("He said: okay, I have to go.", "en", False),
        # soft words are "okay", "so", "well", "now" and the like, never a "yes" that answers her; "I'm off" alone is
        # also a day off
        ("Yes, I have to go. Remind me at eight.", "en", False),
        ("Exactly, I have to go.", "en", False),
        ("No, I'm off.", "en", False),
        ("I'm off.", "en", False),
        ("I'm off, by the way, on Friday.", "en", False),
        ("I have to go to the dentist tomorrow.", "en", False),  # a whole sentence of its own only
        ("I'm off to bed soon.", "en", False),
        ("I'm off work on Friday.", "en", False),
        ("When I'm off, remind me to call Alex.", "en", False),
        ("I have to go. Wait, one more thing.", "en", False),
        ("I have to go, but what about tomorrow?", "en", False),
        ("Do I have to go?", "en", False),
        ("So, I have to go?", "en", False),  # asked, not said
        ("Ik moet gaan.", "nl", True),
        ("Oké, ik hang op.", "nl", True),
        ("Ik moet nu ophangen, doei.", "nl", True),
        ("I have to go.", "nl", True),  # English on any call, as the goodbyes
        ("Ik moet gaan werken.", "nl", False),
        ("Ik moet gaan, maar hoe wordt het weer?", "nl", False),
        ("Ik moet gaan, is goed.", "nl", True),  # the English question starts are English only
        ("Ik moet gaan, wacht, nog één ding.", "nl", False),  # a stop word of the call's language: more
        ("Ich muss los, warte mal.", "de", False),
        ("Ich muss los.", "de", True),
        ("So, ich lege jetzt auf.", "de", True),
        ("Ich muss morgen früh los.", "de", False),
        ("Ich muss los, aber wie wird das Wetter?", "de", False),
        ("じゃあ、切るね。", "ja", True),
        ("そろそろ失礼します。", "ja", True),
        ("切るね、明日の天気は？", "ja", False),
        ("Je dois y aller.", "fr", True),
        ("Мне пора.", "ru", True),
        ("Ik moet gaan.", "en", False),  # the call's language and English only
    ],
)
def test_leave_taking_is_a_sentence_of_its_own(said: str, code: str, leaving: bool) -> None:
    from local_assistant.calls import takes_leave

    assert takes_leave(said, name="Tiffany", language=code) is leaving


@pytest.mark.parametrize(
    ("said", "code", "leaving"),
    [
        # owner, 18 Oct: leaving with a reason kept a call open; the same rules as "I have
        # to go": a part of its own, soft words beside it, only closing talk after it
        ("Sorry, I have something to do.", "en", True),
        ("I've got something to do, talk later.", "en", True),
        ("Okay, I have to run.", "en", True),
        ("Gotta run, bye!", "en", True),
        ("I need to do something now.", "en", True),
        ("Oh, something came up, I have to go.", "en", True),
        ("Something came up.", "en", True),
        ("Alright, I have something to do right away.", "en", True),
        ("Something has come up, sorry.", "en", True),
        ("I have something to do for you.", "en", False),  # the part goes on
        ("I have something to do, but what's the weather tomorrow?", "en", False),
        ("I need to do something about the heater.", "en", False),
        ("Something came up at work yesterday, what do you think?", "en", False),
        ("I have to run the dishwasher.", "en", False),
        ("I have to run, to the shop, remind me at six.", "en", False),
        ("Gotta run. Wait, one more thing.", "en", False),
        ("Do I have something to do today?", "en", False),
        ("He said: something came up.", "en", False),  # quoted speech
        ("Lo siento, tengo algo que hacer.", "es", True),
        ("Me surgió algo.", "es", True),
        ("Tengo algo que hacer mañana por la tarde.", "es", False),
        ("Scusa, ho una cosa da fare.", "it", True),
        ("Mi è venuto un imprevisto.", "it", True),
        ("Ho una cosa da fare per te.", "it", False),
        ("할 일이 있어요.", "ko", True),
        ("급한 일이 생겼어요.", "ko", True),
        ("내일 할 일이 있어요.", "ko", False),
        ("Desculpa, tenho uma coisa para fazer.", "pt", True),
        ("Surgiu um imprevisto.", "pt", True),
        ("Tenho uma coisa para fazer amanhã.", "pt", False),
        ("Sorry, ik moet nog iets doen.", "nl", True),
        ("Er is iets tussengekomen.", "nl", True),
        ("Ik moet nog iets doen voor mijn werk.", "nl", False),
        ("Przepraszam, mam coś do zrobienia.", "pl", True),
        ("Coś mi wypadło.", "pl", True),
        ("Mam coś do zrobienia na jutro.", "pl", False),
        ("Sorry, ich habe noch was zu tun.", "de", True),
        ("Mir ist was dazwischengekommen.", "de", True),
        ("Ich habe noch was zu tun im Garten.", "de", False),
        ("ごめん、用事ができた。", "ja", True),
        ("じゃあ、急用ができました。", "ja", True),
        ("明日、用事がある。", "ja", False),  # only soft words before it in its sentence
        # "I have things to do": never (and "У меня дела, хорошо." is "I'm fine")
        ("Извини, у меня дела.", "ru", False),
        ("Мне надо бежать.", "ru", True),
        ("У меня дела на работе.", "ru", False),
        ("Désolé, j'ai quelque chose à faire.", "fr", True),
        ("Je dois filer.", "fr", True),
        ("J'ai quelque chose à faire demain.", "fr", False),
    ],
)
def test_leaving_with_a_reason_is_leave_taking(said: str, code: str, leaving: bool) -> None:
    from local_assistant.calls import takes_leave

    assert takes_leave(said, name="Tiffany", language=code) is leaving


@pytest.mark.parametrize(
    ("code", "said", "reply"),
    [
        ("en", "Okay, I have to go.", "Sure, talk soon! Anything else before you go?"),
        ("nl", "Oké, ik moet gaan.", "Prima, tot later! Nog iets anders?"),
        ("de", "Ich muss los.", "Alles klar, bis später!"),
        ("en", "Sorry, something came up, I have to run.", "No problem, good luck! Talk soon?"),
        ("fr", "Désolé, j'ai quelque chose à faire.", "Pas de souci, à bientôt."),
    ],
)
async def test_leave_taking_ends_the_call_once_her_reply_is_spoken(code: str, said: str, reply: str) -> None:
    async with Harness([said], [Turn(reply)], language=code) as h:
        await h.connected()
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == [said]  # Gemma answered
        assert reply.split("! ")[-1] in h.manager.said  # her reply whole, even when it asks something
        assert prompts.call_lines(code, name=NAME).idle not in h.manager.said  # ended for the words, not silence


@pytest.mark.parametrize(
    ("said", "code", "leaving"),
    [
        # review 18 Oct: "I need to do something" or "something came up" also complains or apologises ("My back hurts.
        # I need to do something."): they count only with nothing but soft words, thanks or "sorry" before them
        ("My back hurts. I need to do something.", "en", False),
        ("Sorry I didn't call earlier. Something came up.", "en", False),
        ("My plants are dying, I need to do something.", "en", False),
        ("I'm so busy this week. I have things to do.", "en", False),
        ("I have things to do.", "en", False),  # never leave-taking: not leaving with a reason
        ("I have to do something.", "en", False),
        ("Mijn rug doet pijn, ik moet iets doen.", "nl", False),
        ("Sorry dat ik niet belde. Er kwam iets tussen.", "nl", False),
        ("Perdón por no llamar. Me surgió algo.", "es", False),
        ("일이 생겼어요.", "ko", False),  # also "something happened"
        ("Okay, sorry. Something came up.", "en", True),
        ("Thanks. Sorry, something came up.", "en", True),
        ("Something came up, sorry.", "en", True),
        ("My back hurts. I have to go.", "en", True),  # clear leave-taking: any statement before it
        ("Perdón, me surgió algo.", "es", True),
        ("Sorry, er kwam iets tussen.", "nl", True),
        ("Danke, mir ist was dazwischengekommen.", "de", True),
        ("Извини, у меня появились дела.", "ru", True),
    ],
)
def test_an_unclear_reason_counts_only_at_the_start_of_the_turn(said: str, code: str, leaving: bool) -> None:
    from local_assistant.calls import takes_leave

    assert takes_leave(said, name="Tiffany", language=code) is leaving


@pytest.mark.parametrize(
    ("said", "code", "leaving"),
    [
        # review 18 Oct: rarer leaving phrases with a second meaning
        ("Il y a un imprévu.", "fr", False),  # also "there is a hitch"
        ("Coś wypadło.", "pl", False),  # also "something fell out"
        ("У меня дела, хорошо.", "ru", False),  # "I'm fine"
        ("J'ai un imprévu.", "fr", True),
        ("Coś mi wypadło.", "pl", True),
    ],
)
def test_a_leaving_phrase_with_a_second_meaning_is_none(said: str, code: str, leaving: bool) -> None:
    from local_assistant.calls import takes_leave

    assert takes_leave(said, name="Tiffany", language=code) is leaving


@pytest.mark.parametrize(
    ("said", "reply"),
    [
        ("My back hurts. I need to do something.", "Oh no! Have you tried a warm compress?"),
        ("Sorry I didn't call earlier. Something came up.", "No worries at all! What's on your mind?"),
    ],
)
async def test_a_complaint_or_an_apology_keeps_the_call(said: str, reply: str) -> None:
    async with Harness([said], [Turn(reply)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after(reply.split("! ")[-1])
        await asyncio.sleep(0.3)
        assert h.host.turns == [said] and h.signal.hung_up == []


async def test_leave_taking_with_a_device_question_waiting_keeps_the_call() -> None:
    asked = Turn("Okay.", reply_id=4, asks_yes=True, code_text="Shall I switch heater off?")
    async with Harness(["Heater off please, I have to go.", "yes"], [asked, Turn("", code_text="Done.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Shall I switch heater off?")
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Done.")
        assert h.host.turns == ["Heater off please, I have to go.", "yes"]


class CutInEvery:
    """A call's audio where the caller cuts in `after_s` into every clip she says, until no more than that is left: a
    listener who keeps saying "mm-hm", or a loud speaker that plays her first word ("Sure,") back each time."""

    def __init__(self, total: int, after_s: float) -> None:
        self.total = total  # her clip, in bytes
        self.after_s = after_s
        self.barge = Barge()
        self.starts: list[int] = []  # where in her clip each say() started, in bytes

    async def say(self, pcm: bytes, *, barge: bool = False, fresh: bool = True, watch: bool = False) -> Said:
        self.starts.append(self.total - len(pcm))
        if len(self.starts) > 40:
            raise AssertionError("she never got to the end of her sentence")
        left = len(pcm) / (2 * RATE)
        if left <= self.after_s:
            return Said(left, left, False)
        return Said(left, self.after_s, True, floor=100.0, level=2000.0)

    async def listen(self, **_: object) -> tuple[bytes, dict[str, Any]]:
        return bytes(9600), {"sustained_s": 0.4}


@pytest.mark.parametrize(
    ("after_s", "starts_s"),
    [
        # review 14 Oct: a stop sooner than RESUME_BACK_S after she resumed sent her back to the same place, forever
        (0.35, [round(0.35 * n, 2) for n in range(12)]),
        # each one a little back, as after a cough; after MAX_FALSE_BARGE of them on from the stop
        (1.0, [0.0, 0.5, 1.0, 1.5, 2.5, 3.5]),
    ],
)
async def test_acknowledgements_over_every_part_of_her_sentence_still_take_her_to_its_end(
    after_s: float, starts_s: list[float]
) -> None:
    async with Harness(["Sure."] * 40) as h:
        pcm = bytes(2 * RATE * 4)  # 4 s of her sentence
        audio = CutInEvery(len(pcm), after_s)
        hers = ("Sure, the whole week stays dry and warm, with a light wind from the west.",)
        result = await h.manager._say_checked(cast(CallAudio, audio), pcm, True, hers=hers)
        assert result is None  # said to its end, never taken over by an acknowledgement
        assert [round(start / (2 * RATE), 2) for start in audio.starts] == starts_s
        assert all(a < b for a, b in itertools.pairwise(audio.starts))  # always further on
        assert audio.barge.acknowledged == len(starts_s) - 1
        assert MAX_FALSE_BARGE == 3


# the early end of a turn (owner, 17 Oct; no shadow): Smart Turn at the short pause and the pause by speech, on every
# call (practice calls too), each on its own detector of the voice service; the call's audio rules are call_audio's
EARLY: dict[str, Any] = {"early_end": True, "pause": "speech", "end_of_turn_s": 2.3}


@pytest.fixture
def listens(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The keywords of every CallAudio.listen in the test, in order."""
    seen: list[dict[str, Any]] = []
    original = CallAudio.listen

    async def spy(self: CallAudio, **kwargs: Any) -> tuple[bytes, dict[str, Any]]:
        seen.append(kwargs)
        return await original(self, **kwargs)

    monkeypatch.setattr(CallAudio, "listen", spy)
    return seen


def log_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def calls_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    caplog.set_level(logging.INFO, logger="local_assistant.call_audio")


async def one_turn_call(h: Harness, answer: str, *, placed: bool = False) -> None:
    """A call (incoming, or one she placed) with one turn of 1 s of speech, answered with `answer`, then hung up."""
    if placed:
        task = await placing(h)
        await h.signal.pick_up()
        assert await task == "answered"
        await h.quiet_after("Good morning! It's 7:00. wake up")
    else:
        await h.connected()
        await h.quiet_after(h.greeting)
    h.helper.caller_says(speech(1.0))
    await h.quiet_after(answer)
    h.signal.remote_hangup()
    await h.until(lambda: h.host.ended == 1)


def test_the_early_end_and_the_pause_by_speech_are_on_by_default() -> None:
    settings = CallSettings(socket_dir=Path("/tmp"))
    assert (settings.early_end, settings.early_end_pause_s, settings.pause) == (True, 0.6, "speech")


@pytest.mark.parametrize("kind", ["incoming", "placed", "practice"])
async def test_every_call_gets_both_on_its_barge_in_stream(
    caplog: pytest.LogCaptureFixture, listens: list[dict[str, Any]], kind: str
) -> None:
    calls_logs(caplog)
    practice = (lambda aci: "nl") if kind == "practice" else None
    async with Harness(["what time is it"], [Turn("Four.")], barge="speech", practice=practice, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95]
        await one_turn_call(h, "Four.", placed=kind == "placed")
    main = [keywords for keywords in listens if not keywords.get("preroll")]
    turns = {id(keywords.get("turn")) for keywords in main}
    assert main and len(turns) == 1  # one TurnEnd for the call, given to every listen of hers
    turn = main[0]["turn"]
    assert isinstance(turn, TurnEnd) and turn.judged and turn.detected and turn.early_pause_s == 0.6
    assert h.voice.windows and all(0 < size <= 256_000 and size % 2 == 0 for size in h.voice.windows)
    streams = {stream for stream, _ in h.voice.chunks}
    assert len(streams) == 1 and re.fullmatch(r"[0-9a-f]{32}", streams.pop())  # barge-in and the pause: one stream
    assert any(re.fullmatch(r"calls: turn ended early \(sure, p 0\.95, pause 0\.\d\d s, answer \d+ ms\)", line)
               for line in log_lines(caplog))  # fmt: skip
    assert h.host.practice_turns == [kind == "practice"]


async def test_practice_calls_end_early_too(caplog: pytest.LogCaptureFixture) -> None:
    """Owner, 17 Oct (decision 4): practice calls are no exception; only the counts would bring them back."""
    calls_logs(caplog)
    async with Harness(["Ik ben moe."], [Turn("Waarom?")], practice=lambda aci: "nl", **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95]
        await one_turn_call(h, "Waarom?")
    assert h.host.practice_turns == [True]
    assert any(line.startswith("calls: turn ended early (sure, p 0.95") for line in log_lines(caplog))


async def test_off_and_quiet_pass_no_turn_rules(
    caplog: pytest.LogCaptureFixture, listens: list[dict[str, Any]]
) -> None:
    """early_end = false and pause = "quiet": today's listen, byte for byte (call_audio pins it), and no end lines."""
    calls_logs(caplog)
    settings = EARLY | {"early_end": False, "pause": "loudness"}
    async with Harness(["what time is it"], [Turn("Four.")], barge="speech", **settings) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        await one_turn_call(h, "Four.")
    assert listens and all(keywords.get("turn") is None for keywords in listens)
    assert h.voice.windows == []  # Smart Turn never asked
    lines = log_lines(caplog)
    assert not [line for line in lines if line.startswith(("calls: early end", "calls: pause by", "calls: turn "))]
    assert any(line.startswith("calls: barge-in speech:") for line in lines)  # barge-in's own line stays


@pytest.mark.parametrize(
    ("turn", "vad", "said"),
    [
        ("loading", "ready", "turn detector loading"),
        ("unavailable", "ready", "turn detector unavailable"),
        (None, "ready", "turn detector old voice service"),
        ("ready", "loading", "speech detector loading"),
        ("ready", None, "speech detector old voice service"),
    ],
)
async def test_a_detector_not_ready_logs_once(
    caplog: pytest.LogCaptureFixture, turn: str | None, vad: str | None, said: str
) -> None:
    """A detector not ready when the call starts: one line for the call, and the full pause (Smart Turn) or loudness
    (the pause by speech) decides; Smart Turn is never asked, and the end lines say why."""
    calls_logs(caplog)
    async with Harness(["one", "two"], [Turn("A."), Turn("B.")], **EARLY) as h:  # any sound stops her
        h.voice.detector, h.voice.turn_detector = vad, turn
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("A.")
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("B.")
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    lines = log_lines(caplog)
    if turn != "ready":
        assert lines.count(f"calls: {said}; the full pause decides this call") == 1
        assert h.voice.windows == []
        assert any(line.startswith("calls: early end on (no turn detector: the full pause decided): 2 turns, 0 ended")
                   for line in lines)  # fmt: skip
    else:
        assert lines.count(f"calls: {said}; loudness decides this call") == 1  # the pause too, for this call
        assert any(line.startswith("calls: pause by speech (loudness: no speech detector): 2 turns") for line in lines)
        assert not [stream for stream, _ in h.voice.chunks]


async def test_the_listen_after_a_barge_in_never_gets_them(listens: list[dict[str, Any]]) -> None:
    long_answer = "This is a long answer. " * 8
    turns = [Turn(long_answer, reply_id=5), Turn("Okay.")]
    async with Harness(["tell me more", "wait, what about tomorrow"], turns, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 150, speech(1.2))
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Okay.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} wait, what about tomorrow"]
    over_her = [keywords for keywords in listens if keywords.get("preroll")]
    assert over_her and all(keywords.get("turn") is None for keywords in over_her)  # INTERRUPTION_END_S alone
    assert all(keywords["end_silence_s"] == 1.2 for keywords in over_her)


async def test_end_of_call_lines_follow_barge_in(caplog: pytest.LogCaptureFixture) -> None:
    calls_logs(caplog)
    async with Harness(["what time is it"], [Turn("Four.")], **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95]
        await one_turn_call(h, "Four.")
    lines = log_lines(caplog)
    barge = next(index for index, line in enumerate(lines) if line.startswith("calls: barge-in"))
    early = (
        r"calls: early end on \(short pause 0\.60 s, full 2\.30 s\): 1 turn, 1 ended early \(sure 1, probably 0\);"
        r" saved 1\.\d s, median 1\.\d\d s a turn; went on 0 \(sure 0, probably 0, speech 0\), caught by the hold 0,"
        r" cut off 0 \(sure 0, probably 0, speech 0\); held 0 \(goes on 0, ends the call 0\); taken over in her first"
        r" 1\.5 s: 0 after an early end, 0 after a full pause; 1 ask \(sure 1, probably 0, not 0, dropped 0\), answer"
        r" median \d+ ms, slowest \d+ ms, 0 failed; saved per turn 1\.\d\d"
    )
    pause = (
        # a few loud frames of the last syllable may count as pause, by where the chunks fall in the clip
        r"calls: pause by speech: 1 turn, 0 ended sooner than by loudness \(0\.0 s in all, median 0\.00 s\), \d+ loud"
        r" frames counted as pause; detector \d+ chunks, slowest \d+ ms, 0 failed"
    )
    assert re.fullmatch(early, lines[barge + 1]), lines[barge + 1]
    assert re.fullmatch(pause, lines[barge + 2]), lines[barge + 2]
    assert lines[barge + 3].startswith("calls: ended after")


async def test_her_wait_after_a_held_turn_that_goes_on_counts_from_its_end(caplog: pytest.LogCaptureFixture) -> None:
    """An early end held because the words go on ("I want to go to"), and the caller goes on: the turn ends at the
    hold's end, and her wait counts from there (the call's clock in frames: 10 ms each)."""
    calls_logs(caplog)
    holder: list[Harness] = []

    def clock() -> float:  # call time: her frames out, one per 10 ms of the call
        return len(holder[0].helper.heard) * 0.01 if holder and holder[0].signal.helper is not None else 0.0

    heard = ["I want to go to", "I want to go to my mother's."]
    async with Harness(list(heard), [Turn("Okay.")], clock=clock, **EARLY) as h:
        holder.append(h)
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95, 0.95]
        await held_call(h)
        h.helper.caller_says_when(lambda: h.voice.heard == 1, speech(1.0))  # on again, inside the full pause
        await h.quiet_after("Okay.")
        assert h.host.turns == [heard[1]]  # one turn: held and joined
    assert "calls: early end held (goes on); the caller went on" in log_lines(caplog)
    [wait] = [m for record in caplog.records if (m := WAIT_LINE.fullmatch(record.getMessage()))]
    assert int(wait.group(2)) < 1_000, wait.group(0)  # from the first clip's end: its second of words and more


def end_line(caplog: pytest.LogCaptureFixture) -> str:
    [line] = [line for line in log_lines(caplog) if line.startswith(("calls: early end on", "calls: early end off"))]
    return line


@pytest.mark.parametrize("early", [True, False])
async def test_speech_before_her_answer_is_cut_off_but_not_after_the_full_pause(
    caplog: pytest.LogCaptureFixture, early: bool
) -> None:
    """The watch: after an early end the caller who goes on, sustained, within what would have been the full pause was
    cut off (the owner's count: at most 1 turn in 20); after the full pause going on is a new turn, never counted."""
    calls_logs(caplog)
    settings = EARLY if early else EARLY | {"pause": "loudness"}  # a full pause: no loud frame counted as pause
    async with Harness(["what time is it"], [Turn("Four.")], delay=0.3, **settings) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95 if early else 0.2]
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_when(lambda: h.voice.heard == 1, speech(0.5))  # on again while she works it out
        await h.quiet_after("Four.")
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    lines = log_lines(caplog)
    went = "calls: the caller went on after an early end (sure)"
    if early:
        assert went in lines
        assert "; went on 1 (sure 1, probably 0, speech 0), caught by the hold 0, cut off 1 (sure 1," in end_line(
            caplog
        )
    else:
        assert went not in lines and ": 1 turn, 0 ended early (" in end_line(caplog)
        assert "; went on 0 (sure 0, probably 0, speech 0), caught by the hold 0, cut off 0 (" in end_line(caplog)


@pytest.mark.parametrize("goes_on", [True, False])
async def test_going_on_over_her_got_it_is_cut_off_too(caplog: pytest.LogCaptureFixture, goes_on: bool) -> None:
    """Review I1: "Got it." comes right after Whisper, inside what would have been the full pause; the caller who goes on
    over it was cut off, as without it. Her own "Got it." alone counts nothing."""
    calls_logs(caplog)
    said = "What's on my calendar for tomorrow?"  # five words or more: a "got it"
    async with Harness([said], [Turn("Two meetings tomorrow.")], delay=0.3, acknowledge=True, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95]
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        if goes_on:  # on again as her "Got it." starts
            h.helper.caller_says_when(lambda: h.manager.saying in prompts.CALL_ACK_LINES, speech(0.4))
        await h.quiet_after("Two meetings tomorrow.")
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    assert h.manager.said[1] in prompts.CALL_ACK_LINES  # the "Got it." was said
    cut = 1 if goes_on else 0
    assert f"; went on {cut} (sure {cut}, probably 0, speech 0), caught by the hold 0, cut off {cut} (" in end_line(
        caplog
    )


async def test_clicks_after_an_early_end_are_no_cut_off(caplog: pytest.LogCaptureFixture) -> None:
    calls_logs(caplog)
    async with Harness(["what time is it"], [Turn("Four.")], delay=0.3, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95]
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_when(lambda: h.voice.heard == 1, typing(0.6))  # keys, never 10 loud frames in a row
        await h.quiet_after("Four.")
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    assert "cut off 0 (" in end_line(caplog)


def bursts(count: int, *, every_s: float = 0.6, loud_s: float = 0.12) -> bytes:
    """Dishes: `count` loud bursts of noise (no voice), one every `every_s`."""
    return (noise(loud_s, 3000) + noise(every_s - loud_s)) * count


@pytest.mark.parametrize(
    ("p", "after"),
    [
        (0.95, noise(0.9) + noise(0.15, 3000)),  # a sure end (0.61 s after the last loud frame), a door 0.3 s later
        (0.2, noise(0.3) + bursts(8)),  # unsure: the pause by speech ends the turn while the dishes go on
    ],
)
async def test_noise_after_an_early_end_is_no_cut_off(caplog: pytest.LogCaptureFixture, p: float, after: bytes) -> None:
    """Review I2: the watch counts the caller's voice (the speech detector's), not a loud sound."""
    calls_logs(caplog)
    async with Harness(["What time is it?"], [Turn("Four.")], delay=1.5, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [p]
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0) + after)
        await h.quiet_after("Four.")
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    lines = log_lines(caplog)
    assert "; went on 0 (sure 0, probably 0, speech 0), caught by the hold 0, cut off 0 (" in end_line(caplog)
    assert not [line for line in lines if line.startswith("calls: the caller went on")]
    ended = "calls: turn ended early (sure" if p == 0.95 else "calls: the speech pause ended the turn"
    assert any(line.startswith(ended) for line in lines)  # an early end, then the noise


async def test_noise_and_no_words_after_an_early_end_are_no_turns(caplog: pytest.LogCaptureFixture) -> None:
    """Where the call logs noise or a sound without words, the end before it is no turn (TurnEnd.noise)."""
    calls_logs(caplog)
    async with Harness(["*music*", "Um.", "what time is it"], [Turn("Four.")], **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95] * 4
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.11))  # a bump: too little voice to transcribe
        await h.until(lambda: any(line.startswith("calls: noise (voiced") for line in log_lines(caplog)))
        for heard in (1, 2):  # heard as noise, then as a filler alone
            h.helper.caller_says(speech(1.0))
            await h.until(lambda: h.voice.heard == heard)  # noqa: B023 - awaited at once
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Four.")
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    assert h.host.turns == ["what time is it"]
    assert end_line(caplog).startswith("calls: early end on (short pause 0.60 s, full 2.30 s): 1 turn, 1 ended early")
    assert "; 4 asks (sure 4," in end_line(caplog)  # Smart Turn was asked each time: the noise is no turn


async def test_a_turn_dropped_in_standby_is_no_turn_in_the_end_lines(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review C16 (0.9.7): a turn without her name in standby is dropped, never answered, but it still counted in the
    early end's lines (turns, saved, cut off), so talk in the room skewed the owner's "at most 1 turn in 20 cut off".
    With the early end in standby (`STANDBY_EARLY_END`, off since 28 Oct), so both turns are Smart Turn's."""
    monkeypatch.setattr(calls, "STANDBY_EARLY_END", True)
    calls_logs(caplog)
    heard = ["pass me the salt please", f"{NAME}, what time is it?"]
    async with Harness(heard, [Turn("It's noon.")], idle_prompt_s=0.5, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95] * 4
        await h.connected()
        await h.quiet_after(CHECK_IN)
        h.helper.caller_says(speech(1.0))  # said to someone in the room
        await h.until(lambda: h.voice.heard == 1)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("It's noon.")
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    assert h.host.turns == [f"{NAME}, what time is it?"]
    assert end_line(caplog).startswith("calls: early end on (short pause 0.60 s, full 2.30 s): 1 turn, 1 ended early")
    assert "; 2 asks (sure 2," in end_line(caplog)  # Smart Turn was asked both times: the dropped one is no turn


@pytest.mark.parametrize(("early", "after_frames", "counted"), [(True, 80, 1), (False, 80, 1), (True, 300, 0)])
async def test_a_takeover_in_her_first_1_5_s_counts_for_both_kinds(
    caplog: pytest.LogCaptureFixture, early: bool, after_frames: int, counted: int
) -> None:
    """The caller taking over within EARLY_BARGE_S of her answer (after its lead) counts against the end before it:
    after an early end or after a full pause, the owner's comparison; later is an ordinary barge-in."""
    calls_logs(caplog)
    long_answer = "This is a long answer. " * 8
    turns = [Turn(long_answer, reply_id=5), Turn("Okay.")]
    settings = EARLY if early else EARLY | {"pause": "loudness"}  # a full pause: no loud frame counted as pause
    async with Harness(["tell me more", "wait, what about tomorrow"], turns, **settings) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95 if early else 0.2, 0.95]
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: h.manager.saying == "This is a long answer.", after_frames, speech(1.2))
        await h.quiet_after("Okay.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} wait, what about tomorrow"]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    after = "an early end (sure)" if early else "a full pause"
    taken = [line for line in log_lines(caplog) if re.fullmatch(rf"calls: taken over \d\.\d s into her answer, after {re.escape(after)}", line)]  # fmt: skip
    assert len(taken) == counted
    early_count, full_count = (counted, 0) if early else (0, counted)
    line = end_line(caplog)
    assert f"taken over in her first 1.5 s: {early_count} after an early end, {full_count} after a full pause" in line


# the hold: an early end whose words end the call, or end like they go on, waits out the full pause after all; the
# caller going on in it is joined to the turn and heard whole


async def held_call(h: Harness, *, start: list[int] | None = None) -> None:
    await h.connected()
    await h.quiet_after(h.greeting)
    if start is not None:
        start.append(len(h.helper.heard))
    h.helper.caller_says(speech(1.0))


async def test_an_early_goodbye_is_held_then_ends_the_call(caplog: pytest.LogCaptureFixture) -> None:
    calls_logs(caplog)
    start: list[int] = []
    async with Harness(["okay, bye"], **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95]
        await held_call(h, start=start)
        await h.until(lambda: bool(h.signal.hung_up))
        await h.until(lambda: h.host.ended == 1)
        heard = list(h.helper.heard)
    assert h.host.turns == []  # a plain goodbye: code's bye, no answer
    said_bye = next(index for index in range(start[0] + 100, len(heard)) if rms(heard[index]) > 300)
    assert said_bye - (start[0] + 100) >= 200  # after the full pause (2.3 s), never at 0.6 s
    lines = log_lines(caplog)
    assert "calls: early end held (ends the call); the turn stands" in lines
    line = end_line(caplog)
    assert ": 1 turn, 1 ended early (sure 1, probably 0); saved 0.0 s, median 0.00 s a turn;" in line  # held: 0
    assert "; held 1 (goes on 0, ends the call 1);" in line and "caught by the hold 0" in line


@pytest.mark.parametrize(
    "said",
    [
        "Okay, I have to go",  # leave-taking
        "That's all for now",  # a wrap-up
        "You can hang up now",  # asks her to hang up
        "No, that's good. Okay, thank you. Bye-bye.",  # a goodbye with more
    ],
)
async def test_leave_taking_and_wrap_up_are_held(caplog: pytest.LogCaptureFixture, said: str) -> None:
    calls_logs(caplog)
    async with Harness([said], [Turn("Bye for now!")], **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95]
        await held_call(h)
        await h.quiet_after("Bye for now!")
        await h.until(lambda: bool(h.signal.hung_up))  # her answer, then the end, as without the hold
        await h.until(lambda: h.host.ended == 1)
    assert h.host.turns == [said]
    assert "calls: early end held (ends the call); the turn stands" in log_lines(caplog)


@pytest.mark.parametrize(
    ("code", "first"),
    [
        ("en", "I want to call,"),
        ("en", "I want to call…"),
        ("en", "I want to call..."),
        ("en", "I want to call -"),
        ("en", "I want to go to"),  # a word that goes on (language.CONTINUING)
        ("en", "It is nice but"),
        ("en", "I want to, um"),  # a filler
        ("en", "Set a reminder for five and"),  # a word a turn never ends on (review 0.9.7, C11)
        ("nl", "Het is mooi, maar"),  # the call language's "but"
        ("nl", "Ik ga naar"),
        ("nl", "It is nice but"),  # English too
    ],
)
async def test_a_turn_that_goes_on_is_held_and_joined(caplog: pytest.LogCaptureFixture, code: str, first: str) -> None:
    calls_logs(caplog)
    whole = "I want to call my mother."
    async with Harness([first, whole], [Turn("Okay.")], language=code, **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95, 0.95]
        await held_call(h)
        h.helper.caller_says_when(lambda: h.voice.heard == 1, speech(1.0))  # on again, inside the full pause
        await h.quiet_after("Okay.")
        assert h.host.turns == [whole]  # one turn: the two clips heard whole
        first_s, joined_s = h.voice.heard_s
        assert joined_s > first_s + 0.9
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    assert "calls: early end held (goes on); the caller went on" in log_lines(caplog)
    line = end_line(caplog)
    assert ": 1 turn, 1 ended early (sure 1, probably 0);" in line  # the held end is no turn of its own
    assert "; went on 1 (sure 1, probably 0, speech 0), caught by the hold 1, cut off 0 (" in line
    assert "; held 1 (goes on 1, ends the call 0);" in line


async def test_a_hold_that_hears_nothing_keeps_the_turn(caplog: pytest.LogCaptureFixture) -> None:
    calls_logs(caplog)
    async with Harness(["I want to call,"], [Turn("Who?")], **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95]
        await held_call(h)
        await h.quiet_after("Who?")
        assert h.host.turns == ["I want to call,"] and h.voice.heard == 1
    assert "calls: early end held (goes on); the turn stands" in log_lines(caplog)


@pytest.mark.parametrize(
    ("said", "p", "pause"),
    [
        ("What time is it?", 0.95, "speech"),  # finished words: they stand
        ("Okay, I have to go", 0.2, "loudness"),  # a full pause was waited out already
        ("I want to call,", 0.2, "loudness"),
    ],
)
async def test_finished_texts_and_full_pause_turns_are_not_held(
    caplog: pytest.LogCaptureFixture, said: str, p: float, pause: str
) -> None:
    calls_logs(caplog)
    async with Harness([said], [Turn("Sure.")], **EARLY | {"pause": pause}) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [p]
        await held_call(h)
        await h.quiet_after("Sure.")
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    assert not [line for line in log_lines(caplog) if line.startswith("calls: early end held")]
    assert "; held 0 (goes on 0, ends the call 0);" in end_line(caplog)


@pytest.mark.parametrize(
    ("text", "code", "open_"),
    [
        ("I want to call,", "en", True),
        ("I want to call, ", "en", True),
        ('He said "and then…"', "en", True),  # a closing quote after it
        ("Then we go to", "en", True),
        ("Nice, but", "en", True),
        ("So, uh", "en", True),
        ("Dan gaan we naar", "nl", True),
        ("Schön, aber", "de", True),
        ("電話したいけど、", "ja", True),
        ("いいね、でも", "ja", True),
        ("Set a reminder for five and", "en", True),  # a word a turn never ends on (language.LEFT_OPEN)
        ("Call the", "en", True),
        ("I called because", "en", True),
        ("Tea or", "en", True),
        ("Ik bel omdat", "nl", True),
        ("Ich komme und", "de", True),
        ("Je viens parce que", "fr", True),
        ("Я звоню, потому что", "ru", True),
        ("Vengo y", "es", True),
        ("Não é?", "pt", False),  # "é" is no "e" ("and"): a question tag ends a turn
        ("Com'è?", "it", False),
        ("What time is it?", "en", False),
        ("I want to call my mother.", "en", False),
        ("Butter.", "en", False),  # a word that starts like one is not one
        ("Het is mooi.", "nl", False),
        ("", "en", False),
    ],
)
def test_goes_on_reads_the_end_of_the_words(text: str, code: str, open_: bool) -> None:
    assert goes_on(text, code) is open_


async def test_a_hold_never_joins_past_120_s_of_audio(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls_logs(caplog)
    monkeypatch.setattr(calls, "HOLD_MAX_S", 1.5)  # the first clip (1 s and its edges) leaves no room
    async with Harness(["I want to call,"], [Turn("Who?")], **EARLY) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        h.voice.turn_p = [0.95]
        await held_call(h)
        await h.quiet_after("Who?")
    assert not [line for line in log_lines(caplog) if line.startswith("calls: early end held")]


async def test_status_shows_the_turn_detector() -> None:
    """/status names each detector that something on calls waits for: the speech detector with only speech or no
    speech, the turn detector with early end."""
    async with Harness(**EARLY) as h:  # any sound stops her, no speech keeps a turn open, early end on
        h.voice.detector, h.voice.turn_detector = "ready", "loading"
        assert await h.manager.detectors() == ("ready", "loading")
        assert await h.manager.detector() == "ready"  # the pause waits for it too
        line = h.manager.status_line(detector="ready", turn="loading")
        assert line == "Calls: ready · speech detector: ready · turn detector: loading"
        h.voice.turn_detector = None
        assert await h.manager.detectors() == ("ready", "old voice service")
    async with Harness(**EARLY | {"early_end": False, "pause": "loudness"}) as h:
        h.voice.detector = h.voice.turn_detector = "ready"
        assert await h.manager.detectors() == ("", "")  # nothing waits for either: nothing to show
        assert h.manager.status_line() == "Calls: ready"


# her closing question after a full answer on a call (owner, 26 Oct: "I ask a deep question and I get a question back";
# the 0.9.8 gate, 28 Oct: all 10 of the 12B's answers to the free will question ended "What do you think?", and the
# 26B's in the set too): it goes, as a question back is for when she cannot answer without it
FULL_ANSWER = (
    "There are two main camps. Compatibilists say you act freely when you act from your own reasons and values, without "
    "force, even if every event has a cause. Incompatibilists say that if everything follows from earlier causes, "
    "nothing you do could have gone otherwise; hard determinists accept that, and libertarians reject determinism "
    "instead. The clash is over what could have done otherwise means. I lean compatibilist, because that kind of "
    "freedom is the one we use when we praise, blame and plan."
)


@pytest.mark.parametrize(
    "closing",
    [
        " What do you think?",
        " What do you think? Does that feel like a real kind of freedom to you?",
        " What do you think? 😄",
        " What do you think? (smiles)",
        ' Which side do you lean to, "free" or "determined"?',
        " Wat denk jij?",
    ],
)
def test_a_full_answers_closing_question_goes(closing: str) -> None:
    assert calls.turn_words(FULL_ANSWER) >= calls.CLOSING_QUESTION_AFTER_WORDS
    assert calls.without_closing_questions(FULL_ANSWER + closing, name=DEFAULT_NAME) == FULL_ANSWER


@pytest.mark.parametrize(
    "answer",
    [
        "I can call you. What time?",  # she cannot set it without the time
        "It depends on your budget and how far you want to go. What would you like to spend?",
        "What do you mean? The film or the book?",  # nothing but questions
        "But is that really freedom? " + FULL_ANSWER,  # a question on its way
        FULL_ANSWER,
        "",
    ],
)
def test_a_short_answers_question_or_one_on_its_way_stays(answer: str) -> None:
    assert calls.without_closing_questions(answer, name=DEFAULT_NAME) == answer


def test_a_closing_question_stays_when_what_is_left_would_end_the_call() -> None:
    """Her goodbye at the end ends the call after a few seconds of quiet (ends_with_goodbye): a question after it keeps
    the call open, so it stays."""
    answer = FULL_ANSWER + " Bye for now! Or is there anything else?"
    assert calls.ends_with_goodbye(FULL_ANSWER + " Bye for now!", name=DEFAULT_NAME)
    assert calls.without_closing_questions(answer, name=DEFAULT_NAME) == answer

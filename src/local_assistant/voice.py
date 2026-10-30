"""Voice notes: what audio a file holds, the local voice service (tools/voice), the spoken text, AAC.

The voice service listens (WhisperKit) and speaks (Kokoro for English, Supertonic-3 for the other languages) on the
Neural Engine, Pocket TTS on the CPU for English in its voices (heart, jane, eve, vera, alba, michael, on the Mac's model:
the 100M beside Gemma 4 26B, the 18M beside 12B, without vera) and for Spanish, Italian, Portuguese, Dutch, German and French in jane, and on a call tells how likely the caller's last 256 ms are
speech (Silero VAD through FluidAudio) and how sure it is that the caller's sentence is finished (Smart Turn v3.2, on
the CPU);
the bridge only talks HTTP to it on 127.0.0.1, in one language at a time, and only over a connection whose other end a
program of its own account holds (`peer_is_ours`): in her own user a program of the owner's could take the port while
her service is not bound, and get the audio and send back words that count as the user's. Spoken answers are cleaned
deterministically (spec 3.3) and converted to AAC in M4A with macOS's own `afconvert`, so Signal shows a real
voice-note bubble.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import http.client
import json
import logging
import os
import re
import stat
import subprocess
import time
import unicodedata
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from local_assistant import signal_text, theory
from local_assistant.config import POCKET_LANGUAGES, canonical_voice
from local_assistant.language import ENGLISH, STAGE_DIRECTIONS, STAGE_SOFT, STAGE_STARRED, made_of
from local_assistant.part_status import PartStatus

log = logging.getLogger(__name__)

AFCONVERT = "/usr/bin/afconvert"
LSOF = "/usr/sbin/lsof"
CHECK_SECONDS = 2.0  # at most, for the check of whose program took a connection (a call's detector: its own limit)
TAKEN_WAIT = 0.02  # between two looks while a listener of ours has not taken the connection yet
CONVERT_TIMEOUT = 60.0
MAX_RESPONSE = 50 * 1024 * 1024
MIN_SPOKEN = 10
REST = " The rest is in the text."
VAD_TIMEOUT = 0.3  # a call never waits for the speech detector: a slower answer is no answer
CONTEXT_CHARS = 1000  # of what she said before a sentence, sent with it (the times' part of the day)
# a voice service not answering yet (a boot: it may listen after the bridge starts; the review of 25 Oct) is asked
# again every LANGUAGES_WAIT_S, LANGUAGES_TRIES times (about 5 minutes), before a language's warm-up gives up
LANGUAGES_WAIT_S = 15.0
LANGUAGES_TRIES = 20
# the voice service's header on a line another voice said in the asked one's place (Pocket TTS could not: Kokoro's
# af_heart did in English, Supertonic-3's F1 in another language); its value, the voice, when it reads like a voice's name
STAND_IN = "x-voice-stand-in"
_VOICE_LIKE = re.compile(r"[a-z]{2}_[a-z]{2,20}|[a-z]{2,20}|[FM][1-9]")


class SpokenClip(bytes):
    """A WAV from the voice service. `stand_in`: the voice that said it in the asked one's place ("" when the asked
    voice said it; "another voice" for a name the header gives that reads like none): a call never keeps such a clip of
    a canned line (the review of 24 Oct: a greeting Kokoro said while Pocket TTS was still fetching stayed af_heart's)."""

    stand_in: str = ""


class VoiceError(Exception):
    """The voice service or the conversion failed."""


class VoiceUnavailableError(VoiceError):
    """The voice service is not running or still loading its models."""


class VoiceNoStandInError(VoiceError):
    """A call's line asked with no Kokoro stand-in (`speak(kokoro=False)`: its turn had its one Kokoro line, or the line
    comes before her answer or a device question; owner, 28 Oct, calls-03) that Pocket TTS could not say either: the
    voice service said nothing (409, "no_stand_in")."""


class VoiceNotHersError(VoiceUnavailableError):
    """The program at the voice service's address is not one of her own account's: nothing is sent to it (part_status
    `not_hers`). One that could not be checked in time is a VoiceUnavailableError: nothing is sent either."""


def peer_is_ours(port: int, local: int, seconds: float = CHECK_SECONDS) -> bool | None:
    """Whether a program of this account took this account's connection from 127.0.0.1:`local` to 127.0.0.1:`port`
    (None: lsof could not run in `seconds`). lsof lists only the processes of the account that runs it (never root
    here), so another account's program (the owner's, in her own user) is never in its list: a connection it does not
    show taken is not hers. The connection itself is checked, not only who listens: a program that took it and then left
    the port to her service is still not hers. Without a listener of ours the answer is no at once; with one, the
    taking is waited for within `seconds` (a connection waits for its accept)."""
    taken, listening = f"n127.0.0.1:{port}->127.0.0.1:{local}", f"n127.0.0.1:{port}"
    deadline, looked = time.monotonic() + seconds, False
    while True:
        try:
            done = subprocess.run(
                [LSOF, "-nP", f"-iTCP@127.0.0.1:{port}", "-Fn"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=max(deadline - time.monotonic(), 0.01),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False if looked else None  # after a look that found it not taken: not taken in time
        looked = True
        names = set(done.stdout.decode("ascii", "replace").splitlines())
        if taken in names:
            return True
        if listening not in names or time.monotonic() + TAKEN_WAIT >= deadline:
            return False
        time.sleep(TAKEN_WAIT)


class VoiceTooLongError(VoiceError):
    """Over 10 MB or 3 minutes."""


class VoiceUnreadableError(VoiceError):
    """The service could not read the audio."""


@dataclass(frozen=True)
class Transcript:
    text: str  # "" when nothing was recognised
    duration: float  # seconds of audio


def sniff(data: bytes) -> str | None:
    """The audio format from the first bytes, never from the claimed type (iOS sends M4A, Android ADTS AAC)."""
    if data[4:8] == b"ftyp":
        return "m4a"
    if len(data) >= 2 and data[0] == 0xFF and data[1] & 0xF6 == 0xF0:  # ADTS sync word, layer 00
        return "aac"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    return None


class VoiceClient:
    """HTTP to voice-service on 127.0.0.1 (no proxies), only once the connection is made and `peer` finds a program of
    this account at its other end: else nothing is sent. Every request carries the language; English requests carry
    none (exactly as before the languages), so an older voice service keeps working in English."""

    def __init__(
        self,
        base_url: str,
        *,
        voice: str,
        speed: float,
        timeout: float = 120.0,
        status: PartStatus | None = None,
        language: str = ENGLISH,
        language_voice: str = "",
        peer: Callable[[int, int, float], bool | None] = peer_is_ours,
    ) -> None:
        parts = urllib.parse.urlsplit(base_url)
        # her voice service listens on 127.0.0.1 only: "localhost" is reached there, never at another address
        self._host = "127.0.0.1" if parts.hostname in (None, "localhost") else parts.hostname
        self._port = parts.port or 80
        self._peer = peer
        self._not_hers: bool | None = False  # the last check's verdict when it was not "hers" (logged once a change)
        self._voice = canonical_voice(voice)  # eve-full is eve (owner, 26 Oct): the voice service knows no eve-full
        self._language_voice = language_voice  # POCKET_LANGUAGES' voice ([voice] language_voice); "": none, as before
        self._speed = speed
        self._timeout = timeout
        self._status = status or PartStatus(None)  # what the last real request did (doctor, /status)
        self._language = language

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, code: str) -> None:
        """Another language from the next request on (/language)."""
        self._language = code

    async def transcribe(
        self, audio: bytes, *, language: str | None = None, hint: str = "", call: bool = False
    ) -> Transcript:
        """What was said, in the current language (or `language`: a call keeps its own); Whisper is told it, no
        detection. `hint`: her name, Whisper's prompt (a call in standby); `call`: a call's audio, whose bare "Bye." or
        "Thank you." the service keeps (review I23); an older voice service ignores both."""
        heard = language or self._language
        query = [f"language={heard}"] if heard != ENGLISH else []
        if hint:
            query.append(f"hint={urllib.parse.quote(hint, safe='')}")
        if call:
            query.append("call=1")
        path = "/v1/audio/transcriptions" + (f"?{'&'.join(query)}" if query else "")
        status, body, _ = await self._real("POST", path, audio, "application/octet-stream")
        if status != 200:
            self._record(status)
            raise _refusal(status)
        try:
            data: Any = json.loads(body)
            transcript = Transcript(str(data["text"]).strip(), float(data.get("duration", 0)))
        except (ValueError, KeyError, TypeError) as exc:
            self._status.failed("voice", "bad_answer")
            raise VoiceError("the voice service sent no transcript") from exc
        self._status.ok("voice")
        return transcript

    @property
    def voice(self) -> str:
        return self._voice

    def set_voice(self, name: str) -> None:
        """Another voice from the next answer on (/voice <voice>): Pocket TTS's or Kokoro's, as the voice service routes it;
        another name (eve-full) as its voice's."""
        self._voice = canonical_voice(name)

    def voice_for(self, language: str) -> str:
        """The voice a request in `language` names: the language voice (jane) in Spanish, Italian, Portuguese, Dutch,
        German and French, which the voice service speaks in Pocket TTS (owner, 25 Oct), else her English voice (the
        other four languages are Supertonic-3's F1 whatever it names). Without a language voice, hers everywhere."""
        return self._language_voice if self._language_voice and language in POCKET_LANGUAGES else self._voice

    async def speak(
        self, text: str, *, language: str | None = None, context: str = "", call: bool = False, kokoro: bool = True
    ) -> SpokenClip:
        """WAV audio of `text`, in the configured voice and speed and the current language (or `language`: code's
        own words on a call are said in English). `context`: what she said just before it in English (a call's
        earlier sentences of her answer, its last CONTEXT_CHARS), read by the voice service only for a time's part of
        the day ("at 7:30" after "tonight" is no morning); an older voice service ignores it. `call`: a call's line,
        for which the voice service tries a Pocket TTS voice that rests after failed runs again, so Kokoro, which an
        Apple bug can crash with the voice service and the call, says fewer lines (the review of 28 Oct, calls-03); an
        older voice service ignores it too. `kokoro` false: a call's line after its turn's one Kokoro line, or one before
        her answer or a device question (owner, 28 Oct): Kokoro never stands in for it, and when Pocket TTS cannot say it the voice service says nothing
        (VoiceNoStandInError); an older voice service ignores it, and its Kokoro line says so (`SpokenClip.stand_in`)."""
        spoken = language or self._language
        fields: dict[str, Any] = {"input": text, "voice": self.voice_for(spoken), "speed": self._speed}
        if spoken != ENGLISH:
            fields["language"] = spoken
        elif context:
            fields["context"] = context[-CONTEXT_CHARS:]
        if call:
            fields["call"] = True
        if not kokoro:
            fields["kokoro"] = False
        status, body, stand_in = await self._real(
            "POST", "/v1/audio/speech", json.dumps(fields).encode(), "application/json"
        )
        if status != 200 or not body:
            if status == 200:
                self._status.failed("voice", "bad_answer")  # no audio
            else:
                self._record(status)
            raise _refusal(status)
        self._status.ok("voice")
        clip = SpokenClip(body)
        clip.stand_in = (stand_in if _VOICE_LIKE.fullmatch(stand_in) else "another voice") if stand_in else ""
        return clip

    async def health(self) -> dict[str, str | tuple[str, ...]] | None:
        """The service's state ({"stt": ..., "tts": ...}, and "languages" once it can do them), or None when it does
        not answer."""
        try:
            status, body, _ = await self._request("GET", "/health", None, None, seconds=5.0)
            data = json.loads(body) if status == 200 else None
        except (VoiceError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return {
            str(key): tuple(str(item) for item in value) if isinstance(value, list) else str(value)
            for key, value in data.items()
        }

    async def vad(self, chunk: bytes, *, stream: str) -> float | str:
        """How likely `chunk` (4096 samples of the caller's 16 kHz audio: 256 ms) is speech, from 0 to 1; the service
        keeps one detector state per `stream` (one call). On a failure, its kind (call_audio.Barge counts them, metadata
        only): "timeout" (no answer within VAD_TIMEOUT) or "http" (no service, an older one, a model still loading, a
        refusal or an answer that is not a probability). Never raises into the call and never marks the voice as
        failing (part_status)."""
        return await self._probability(f"/vad?stream={stream}", chunk)

    async def turn(self, window: bytes) -> float | str:
        """How sure Smart Turn is that the caller's sentence is finished, from 0 to 1: `window` is the turn's last 8 s
        at most (16 kHz mono s16le, the most recent sample last; no query, no state). On a failure its kind, as `vad`'s
        (call_audio.TurnEnd counts them): "timeout" (no answer within VAD_TIMEOUT) or "http" (no service, an older one,
        a model loading or unavailable, a refused window or an answer that is not a probability). Never raises into
        the call and never marks the voice as failing."""
        return await self._probability("/turn", window)

    async def _probability(self, path: str, body: bytes) -> float | str:
        """A detector's answer on a call: {"p": 0-1} within VAD_TIMEOUT, else "timeout" or "http"."""
        try:
            status, answer, _ = await self._request("POST", path, body, "application/octet-stream", seconds=VAD_TIMEOUT)
            p = json.loads(answer).get("p") if status == 200 else None
        except VoiceUnavailableError as exc:
            return "timeout" if _timed_out(exc) else "http"
        except Exception:
            return "http"
        if isinstance(p, bool) or not isinstance(p, int | float) or not 0.0 <= p <= 1.0:
            return "http"
        return float(p)

    async def languages(self) -> tuple[str, ...] | None:
        """The languages the installed service has: () when it is older than the language feature, None when it does
        not answer."""
        health = await self.health()
        if health is None:
            return None
        listed = health.get("languages")
        return listed if isinstance(listed, tuple) else ()

    async def health_when_up(self) -> dict[str, str | tuple[str, ...]] | None:
        """`health()`, asked again every LANGUAGES_WAIT_S while the voice service does not answer, LANGUAGES_TRIES
        more times (about 5 minutes: after a boot it may listen after the bridge starts); None if it never answers."""
        health = await self.health()
        for _ in range(LANGUAGES_TRIES):
            if health is not None:
                break
            await asyncio.sleep(LANGUAGES_WAIT_S)
            health = await self.health()
        return health

    async def languages_when_up(self) -> tuple[str, ...] | None:
        """`languages()` from `health_when_up()`: None if it never answers."""
        health = await self.health_when_up()
        if health is None:
            return None
        listed = health.get("languages")
        return listed if isinstance(listed, tuple) else ()

    async def _real(self, method: str, path: str, body: bytes, content_type: str) -> tuple[int, bytes, str]:
        """A transcription or speech request: a service that does not answer is recorded (part_status)."""
        try:
            return await self._request(method, path, body, content_type)
        except VoiceNotHersError:  # another account's program at her address: doctor and /status name it
            self._status.failed("voice", "not_hers")
            raise
        except VoiceUnavailableError:
            self._status.failed("voice", "no_answer")
            raise

    def _record(self, status: int) -> None:
        """What a refusal says about the service: too long or unreadable audio is the service working as it should."""
        if status == 503:
            self._status.failed("voice", "loading", status)
        elif status in (409, 413, 415, 422):  # 409: a line asked with no Kokoro that Pocket TTS could not say
            self._status.ok("voice")
        else:
            self._status.failed("voice", "refused", status)

    async def _request(
        self, method: str, path: str, body: bytes | None, content_type: str | None, *, seconds: float | None = None
    ) -> tuple[int, bytes, str]:
        """The answer's status, its body (at most MAX_RESPONSE) and its STAND_IN header ("" without one)."""

        def call() -> tuple[int, bytes, str]:
            headers = {"Content-Type": content_type} if content_type else {}
            limit = seconds or self._timeout
            started = time.monotonic()
            connection = http.client.HTTPConnection(self._host, self._port, timeout=limit)
            try:
                connection.connect()  # first the connection, then whose program took it: then the request
                assert connection.sock is not None
                verdict = self._peer(self._port, connection.sock.getsockname()[1], min(limit, CHECK_SECONDS))
                self._say(verdict)
                if verdict is False:
                    raise VoiceNotHersError("the program at the voice service's address is not hers: nothing sent")
                if verdict is None:
                    raise VoiceUnavailableError("whose program is at the voice service's address: not checked in time")
                # the check counts within the request's own time (a call's detector: VAD_TIMEOUT in all)
                connection.sock.settimeout(max(limit - (time.monotonic() - started), 0.01))
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                return response.status, response.read(MAX_RESPONSE), (response.getheader(STAND_IN) or "")[:40]
            except (OSError, http.client.HTTPException) as exc:
                raise VoiceUnavailableError(f"the voice service is not answering ({type(exc).__name__})") from exc
            finally:
                connection.close()

        return await asyncio.to_thread(call)

    def _say(self, verdict: bool | None) -> None:
        """The log line when the program at the port is not hers or could not be checked, once while it lasts, and
        when her own service holds it again (the port only: no address, no text)."""
        now = None if verdict is None else not verdict
        if now != self._not_hers:
            if now is None:
                log.warning("voice: whose program is at port %d could not be checked: nothing sent", self._port)
            elif now:
                log.warning("voice: the program at port %d is not hers (not her account's): nothing sent", self._port)
            else:
                log.info("voice: her own voice service holds port %d again", self._port)
        self._not_hers = now


DETECTOR_STATES = ("ready", "loading", "unavailable")  # what the voice service's health says as "vad" and "turn"


def detector_state(health: Mapping[str, object] | None, part: str = "vad") -> str:
    """A detector's state from the voice service's health (`part`: "vad", the speech detector, or "turn", Smart Turn):
    "ready", "loading" (the model loads or downloads at start), "unavailable" (its model did not download or failed its
    check: it needs downloading again), "old voice service" (built before that detector) or "not ready" (no answer, or
    a state this bridge does not know). Anything but "ready" means, on calls meanwhile, loudness for the speech detector
    (it stops her, and keeps a turn open) and the full pause for the turn detector."""
    if health is None:
        return "not ready"
    if part not in health:
        return "old voice service"
    state = health[part]
    return state if isinstance(state, str) and state in DETECTOR_STATES else "not ready"


# Pocket TTS's release program for her Pocket voices, as the voice service pins it (VoiceCore `PocketTTS.release`), by its
# path in her release: beside the voice service, put there by estia-deploy (never in her home: the review of 24 Oct);
# doctor checks it (`pocket_files`)
POCKET_PROGRAM = {
    "tools/voice/.build/release/pocket-tts/pocket-tts": "447d95181e133b3fda268d248126041722cf1bdf767093f44f2a0d047ab192be",
    "tools/voice/.build/release/pocket-tts/LICENSE": "23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3",
}
# every English voice's model on a Mac with Gemma 4 26B, the 100M one (VoiceCore `PocketModel.english`; owner, 26 Oct:
# "a Mac on the 26B gets the 100M model"), by its path in the voice service's models folder in her home: heart, jane,
# eve, vera, alba and michael, fetched at the voice service's start (about 250 MB; heart's copied, never fetched)
POCKET_FILES_100M = {
    "pocket-tts/english/weights/model.safetensors": "4c56cbd171c63e8a26ea61f5d48b8a0cd8f3c51f4652c0ab9a19bfcced8c931d",
    "pocket-tts/english/tokenizer/tokenizer.json": "f498428e1eafee50492f7be13dc9bfafcfc12e508cd0eb1b01c92ecd5d8c6687",  # a public file hash, not a key (gitleaks:allow)
    "pocket-tts/english/eve/eve.safetensors": "d31289743fdf306517925afb91e99c27b4d336c127013d2c4d1212791a41cf37",
    "pocket-tts/english/jane/jane.safetensors": "f802b7b0ce91578dc5448e1cdf4827695e64a34cc6b2f95989dc335082522887",
    "pocket-tts/english/vera/vera.safetensors": "fe136ebb9c88b65803e73ab8fc04fcc7faaa5aa790559bdab3d2382826eea44a",
    "pocket-tts/english/alba/alba.safetensors": "5850f417028fb2bf6de31e5e395e56ee7a21ac0e52ef31aebd4a6a2847d72e9c",
    "pocket-tts/english/michael/michael.safetensors": "9f6538650ed3b664ab847d05244f51f04db22ce7ea00e2b285f51d91d6308ce0",
    # heart's (owner, 27 Oct): never fetched, copied there by the voice service from its own bundle (`POCKET_SHIPPED`)
    "pocket-tts/english/heart/heart.safetensors": "60e5e6cbcac2f61554adcfc1ffd9830308802a3685db49489f5a14e8e65b5425",
}
# heart's voice file for the 100M as the voice service ships it (VoiceCore `PocketTTS.resources`; owner, 27 Oct: its
# file in the public repository, "yes"): the repository's tools/voice/Sources/voice-service/Voices/heart.safetensors,
# built into the voice service's own bundle beside its program in her release (estia-deploy copies the build's bundles),
# the source the voice service copies into its models folder; doctor checks it on a Mac with the 100M model
# (`pocket_shipped`, `pocket_files`). The build puts it in one of two places, by its build system (the review of 27 Oct,
# swift-voice-1): Contents/Resources with Swift 6.4's default build (swiftbuild), the bundle's top with the native build
# (Swift 6.2's default, which Package.swift accepts); the first that is there is the one (deploy.heart_places names the
# same two)
POCKET_SHIPPED = {
    "tools/voice/.build/release/voice-service_voice-service.bundle/Contents/Resources/heart.safetensors": (
        "60e5e6cbcac2f61554adcfc1ffd9830308802a3685db49489f5a14e8e65b5425"
    ),
    "tools/voice/.build/release/voice-service_voice-service.bundle/heart.safetensors": (
        "60e5e6cbcac2f61554adcfc1ffd9830308802a3685db49489f5a14e8e65b5425"
    ),
}
# the 18M's (owner, 29 Oct: "just make the small heart"), its own clone, heart-18m.safetensors beside the 100M's in the
# same bundle and the same two places; doctor checks it on a Mac with the 18M model
POCKET_SHIPPED_18M = {
    "tools/voice/.build/release/voice-service_voice-service.bundle/Contents/Resources/heart-18m.safetensors": (
        "0d9e5334433271a849bfa97b1a8626cb690446a98ea3008ba016704bc51cd2bb"
    ),
    "tools/voice/.build/release/voice-service_voice-service.bundle/heart-18m.safetensors": (
        "0d9e5334433271a849bfa97b1a8626cb690446a98ea3008ba016704bc51cd2bb"
    ),
}
# every English voice's model on a smaller Mac (Gemma 4 12B), the 18M one (VoiceCore `PocketModel.english18M`): heart,
# jane, eve, alba and michael (no vera), fetched at the voice service's start (about 130 MB; heart's copied, never
# fetched: `POCKET_SHIPPED_18M`)
POCKET_FILES = {
    "pocket-tts/english_18m_refl/weights/model.safetensors": "4e7a106c1ac5e3c43226565df57523184e574127c4c30c1ea6aadc4ba5e9bafb",
    "pocket-tts/english_18m_refl/tokenizer/tokenizer.model": "d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6",  # a public file hash, not a key (gitleaks:allow)
    "pocket-tts/english_18m_refl/eve/eve.safetensors": "f89ae14c3f3b6053aefbe3bfa5236a0c61b25bb6b45e15b8a6d0fa6f06979a3a",
    "pocket-tts/english_18m_refl/jane/jane.safetensors": "796f7860a0a2a0ca0b57fda2c7fa7a4cb1e5cd77517ced8c35177b5686f15af3",
    "pocket-tts/english_18m_refl/alba/alba.safetensors": "e1aa12e17245952e044853867deef5e77ec4474df119cbeefa4ed2a3d878a7b1",
    "pocket-tts/english_18m_refl/michael/michael.safetensors": "0c2ce79817211a343f784db121570e66e658f146eabc4ccd68fcdbb01b4556e3",
    "pocket-tts/english_18m_refl/heart/heart-18m.safetensors": "0d9e5334433271a849bfa97b1a8626cb690446a98ea3008ba016704bc51cd2bb",
}
# From this much memory (GB as macOS counts it, GiB) a Mac's voice service speaks every English Pocket voice on the
# 100M model (VoiceCore `PocketTTS.standardMemoryGiB`, pinned equal by a test): setup gives such a Mac Gemma 4 26B
# (`setup.choose_model`), a smaller one 12B and the 18M (owner, 26 Oct: "the Pocket model follows the Gemma tier")
POCKET_STANDARD_GB = 30


def memory_gb() -> float:
    """This Mac's memory in GB (GiB, as macOS counts it; setup's own reading, `setup.memory_gb`, is the same)."""
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30


def pocket_standard(memory: float | None = None) -> bool:
    """Whether a Mac with `memory` GB (None: this one's) speaks Pocket TTS's 100M English model, as its voice service
    decides it (else the 18M, which has no vera)."""
    return (memory_gb() if memory is None else memory) >= POCKET_STANDARD_GB


def pocket_english_files(standard: bool) -> dict[str, str]:
    """The pins of the English model a Mac speaks: the 100M's beside Gemma 4 26B (`standard`), else the 18M's."""
    return POCKET_FILES_100M if standard else POCKET_FILES


# jane's model for each of the six languages Pocket TTS speaks besides English (VoiceCore `PocketTTS.languages`, owner,
# 25 Oct), by its path in the voice service's models folder: each fetched only for the language in use (about 227 MB)
POCKET_LANGUAGE_FILES = {
    "es": {
        "pocket-tts/spanish/weights/model.safetensors": "c4988ff376692f2ab1e85b17350755b08f16e49c0b5f0de8b271efa9abd71de7",
        "pocket-tts/spanish/tokenizer/tokenizer.json": "9ade09bbdc64ab2b59952b90773fa0890b780ece4fd5aef0d36a00c6ad5f0531",  # a public file hash, not a key (gitleaks:allow)
        "pocket-tts/spanish/jane/jane.safetensors": "a1f1c36c155e84da0bce58d635128059413e618324d47f9679aa012d8bdeb9a0",
    },
    "it": {
        "pocket-tts/italian/weights/model.safetensors": "16365c43eaabee9b222cd414d6a48f0976b4800a21fe5d55253f645eff7eeb1f",
        "pocket-tts/italian/tokenizer/tokenizer.json": "4bc1c0d4d54f9480183e94c94cfabf74fd89b0912537d4ef607d1982c4a381ec",  # a public file hash, not a key (gitleaks:allow)
        "pocket-tts/italian/jane/jane.safetensors": "b9787a3a8e3d3e608c493bd24ec2880fe65717807f938a8c1565c126590acdc4",
    },
    "pt": {
        "pocket-tts/portuguese/weights/model.safetensors": "28e20f32515ba8bd0eaec69671f11f8921414fcdc375aa072fa09f46cc1057c5",
        "pocket-tts/portuguese/tokenizer/tokenizer.json": "b33e269f7f042a763080d71ba087d74d2f6759674b238a723c028e436b1a25ab",  # a public file hash, not a key (gitleaks:allow)
        "pocket-tts/portuguese/jane/jane.safetensors": "161d4cc6b023d20207220d3687e42d41847c1f823956062646b0c9e22705805e",
    },
    "nl": {
        "pocket-tts/dutch/weights/model.safetensors": "59b5c3249ab05e8b5fcb0a296e9b0cb0c0a20da5e1fd28c04bb90b2175612aeb",
        "pocket-tts/dutch/tokenizer/tokenizer.json": "e6f610b98c427641b4ab0e1024dca7843e3d6b620871a14b204932e42c5a1736",  # a public file hash, not a key (gitleaks:allow)
        "pocket-tts/dutch/jane/jane.safetensors": "36816d4767fe69a67eeb4d9778c71eea272f34d99d63c2af8c47cf772339b5c1",
    },
    "de": {
        "pocket-tts/german/weights/model.safetensors": "9fe42605604832349e3f495177bde56b77ae8fee73b2ed4b63236092ccde7621",
        "pocket-tts/german/tokenizer/tokenizer.json": "2d77849811e3d1b6ae80e5b11b203f57e1847901104d8a0573b34f46f8dddb53",  # a public file hash, not a key (gitleaks:allow)
        "pocket-tts/german/jane/jane.safetensors": "bd1bc91fd38a724b14f5c9cb4d67327f968568beb28d72fe7bb81f0fba75b685",
    },
    "fr": {
        "pocket-tts/french/weights/model.safetensors": "56366cfed6c25bfcd3bf4c61e66e06efaf52e78a5d06e1dcade1c588495bc699",
        "pocket-tts/french/tokenizer/tokenizer.json": "6211acffe9cb95172f71f0051f18e26ea305b2b32ea00b331ee976551c70887f",  # a public file hash, not a key (gitleaks:allow)
        "pocket-tts/french/jane/jane.safetensors": "1482685d8a1a22a7294df650f61293bab353445710e26a60ae057cbf4ad85221",
    },
}
PocketFiles = Literal["ok", "missing", "changed", "unread"]


def pocket_shipped(release: Path, standard: bool) -> dict[str, str]:
    """Heart's pin for the Mac's model (`standard`: the 100M's, `POCKET_SHIPPED`; else the 18M's, `POCKET_SHIPPED_18M`)
    by the place its file has in `release`: the first that is there (a link counts, and is never the pinned file),
    else the default build's, which `pocket_files` then names missing."""
    places = list((POCKET_SHIPPED if standard else POCKET_SHIPPED_18M).items())
    relative, sha = next(((path, sha) for path, sha in places if os.path.lexists(release / path)), places[0])
    return {relative: sha}


def pocket_files(models: Path, pins: Mapping[str, str] = POCKET_FILES) -> PocketFiles:
    """Pocket TTS's files in the voice service's models folder against their pins: "ok" (every one there with its
    bytes), "missing" (one not fetched yet), "changed" (other bytes, a link or a folder in its place: the voice service
    never runs it) or "unread" (this user cannot read them: hers, read as someone else). Read in 1 MiB pieces."""
    found: list[PocketFiles] = []
    for relative, sha in pins.items():
        path = models / relative
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                found.append("changed")
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while piece := handle.read(1 << 20):
                    digest.update(piece)
        except FileNotFoundError:
            found.append("missing")
            continue
        except PermissionError:
            found.append("unread")
            continue
        found.append("ok" if digest.hexdigest() == sha else "changed")
    worst: tuple[PocketFiles, ...] = ("unread", "changed", "missing")
    return next((state for state in worst if state in found), "ok")


def _timed_out(exc: BaseException) -> bool:
    """Whether a request failed for lack of an answer in time (a read or a connect that timed out)."""
    return isinstance(exc.__cause__, TimeoutError)


def _refusal(status: int) -> VoiceError:
    if status == 409:
        return VoiceNoStandInError("Pocket TTS could not say the line, and no Kokoro stand-in was asked")
    if status == 413:
        return VoiceTooLongError("over 10 MB or 3 minutes")
    if status in (415, 422):
        return VoiceUnreadableError("the voice service could not read the audio")
    if status == 503:
        return VoiceUnavailableError("the voice service is still loading")
    return VoiceError(f"the voice service answered {status}")


_FENCE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_LINK = re.compile(r"\[([^\]\n]*)\]\([^)\s]*\)")
_INLINE = re.compile(r"`([^`\n]*)`")
_URL = re.compile(r"(?:https?://|www\.)[^\s)\]>]*[^\s)\]>.,;:!?]")
_PATH = re.compile(r"(?<![\w/])(?:~|\.{1,2})?/(?:[\w.-]+/)*[\w.-]*[\w-]")
# the user's commands (commands.py; /help names them): said as "slash reminders", never left out as a path ("/reminders"
# alone looks like one); a command with more path after it ("/new/x", "/help.txt") is a path
COMMANDS = (
    "new",
    "profile",
    "forget",
    "fix",
    "status",
    "tools",
    "learn",
    "voice",
    "voices",
    "soul",
    "docs",
    "language",
    "babble",
    "remind",
    "reminders",
    "quiet",
    "initiative",
    "evening",
    "devices",
    "lock",
    "unlock",
    "help",
)
_COMMAND = re.compile(rf"(?<![\w/~.])/({'|'.join(COMMANDS)})(?![\w-]|/|\.\w)", re.IGNORECASE)
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-♬♰-➿⬀-⯿️‍]")  # never ♭ ♮ ♯: said as flat, natural, sharp
_EMPHASIS = re.compile(r"\*\*|__|~~")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}")
_HEADING = re.compile(r"^#{1,6}\s+")
_BULLET = re.compile(r"^(?:[-*•+]|\d{1,2}[.)])\s+")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|[。！？]")  # Japanese: no space after its sentence ends


# a stage direction in her answer: a whole group in brackets, full-width brackets or single stars ("(pause)", "[beat]",
# "（間）", "*smiles*", "*(laughs)*"); never a link's text ("[pause](…)") or bold ("**Pause**"), and code (a fence or
# `inline`) is matched first and kept as written
_STAGE = re.compile(
    r"(?P<code>```.*?(?:```|\Z)|`[^`\n]*`)"
    r"|(?P<pre>[ \t]*)"
    r"(?:\*{1,2}\((?P<starred>[^()\n]{1,40})\)\*{1,2}"
    r"|\((?P<round>[^()\n]{1,40})\)"
    r"|\[(?P<square>[^\[\]\n]{1,40})\](?!\()"
    r"|（(?P<wide>[^（）\n]{1,20})）"
    r"|(?<![*\w])\*(?P<stars>[^*\s][^*\n]{0,39}?)\*(?![*\w]))"
    r"(?P<post>[ \t]*)",
    re.DOTALL,
)
_STAGE_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_STAGE_BETWEEN = re.compile(r"[\s,;.!…&'’-]+")  # all a stage direction may hold besides its words
_NO_SPACES = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")  # Japanese: by characters
_REPEATED_DOTS = re.compile(r"(\.\.\.|…)(?:[ \t]*(?:\.\.\.|…))+")  # "Em... ... C" once its "(pause)" is out
_ENDS = ".!?…。！？"  # a sentence's end marks (an ellipsis too)
# "..." right after a word (at a space, or right after another "...": "C...G...Am"), with the next word and the one after
# it (a lookahead: "C... G... Am..." is matched word by word)
_DOTS_BETWEEN = re.compile(
    r"(?:(?<!\S)|(?<=\.\.\.))([^\s.,;:!?]+)\.{3,}"  # a word, at a space or right after "...", and its "..."
    r"(?=[ \t]*([^\s.,;:!?]+)(?:[ \t]+([^\s.,;:!?]+))?)"  # the next two words
)
# The words that join two chords ("C... then G"): and, or and then in English and the eight languages that spell chords
_JOINS = frozenset(
    {
        *("and", "or", "then", "to"),
        *("y", "e", "o", "u", "luego", "después"),  # es
        *("ed", "poi"),  # it
        *("ou", "depois"),  # pt
        *("et", "puis", "ensuite"),  # fr
        *("en", "of", "dan"),  # nl
        *("und", "oder", "dann"),  # de
        *("i", "oraz", "lub", "albo", "potem"),  # pl
        *("и", "или", "потом", "затем"),  # ru
    }
)
_WRAPS = "([\"'“‘)]\"'”’"  # brackets and quotes around a word, never part of a chord
_MARKS_RUN = re.compile(r"([.,;:!?])[.,;:!?]+")  # a run of marks, said as its first
_DOTTED = re.compile(r"[^\W\d_]\.[^\W\d_]\Z")  # "N.C", "e.g", "a.m": a dotted abbreviation up to its last dot


def _stage_units(text: str) -> tuple[str, ...] | None:
    """A bracket's words as compared: casefolded, Latin letters without accents, Japanese by characters; None when it
    holds anything but words and the marks between them (a digit, a sign: "(x2)", "(G7)")."""
    if _STAGE_BETWEEN.sub("", _STAGE_WORD.sub("", text)):
        return None
    words = []
    for word in _STAGE_WORD.findall(text.replace("’", "'")):
        word = word.casefold()
        if all(ord(char) < 0x250 for char in word):
            word = "".join(char for char in unicodedata.normalize("NFKD", word) if not unicodedata.combining(char))
        words.append(unicodedata.normalize("NFC", word))
    return tuple("".join(words)) if _NO_SPACES.search(text) else tuple(words)


def _stage_phrases(lists: Mapping[str, tuple[str, ...]]) -> frozenset[tuple[str, ...]]:
    return frozenset(units for words in lists.values() for word in words if (units := _stage_units(word)))


_STAGE_WORDS = _stage_phrases(STAGE_DIRECTIONS)
_STAGE_STARRED_WORDS = _stage_phrases(STAGE_STARRED)
_STAGE_SOFT_WORDS = _stage_phrases(STAGE_SOFT)


def _cased(char: str) -> bool:
    """A letter with an upper and a lower case (Latin, Cyrillic); Japanese and Korean have none."""
    return char.lower() != char.upper()


def _attaches(char: str) -> bool:
    """A character a bracket right beside it belongs to: a cased letter, a digit or "_" ("time.sleep(pause)")."""
    return char == "_" or char.isdigit() or _cased(char)


def _between_sentences(before: str, after: str) -> bool:
    """Whether a group stands between sentences: after the start of a line or a sentence's end, and before the end of
    a line, a sentence's end or a sentence's start (a capital, a letter without case, or "¡", "¿", a quote first)."""
    left = before.rstrip(" \t")
    left_edge = not left or left.endswith("\n") or left.rstrip("\"'”’)]").endswith(tuple(_ENDS))
    right = after.lstrip(" \t")
    starts = right.lstrip("¡¿\"“«'‘(")
    right_edge = (
        not right
        or right[0] == "\n"
        or right[0] in _ENDS
        or bool(starts and starts[0].isalpha() and (starts[0].isupper() or not _cased(starts[0])))
    )
    return left_edge and right_edge


def without_stage_directions(text: str) -> str:
    """Her answer without its stage directions ("(pause)", "(short pause)", "[beat]", "*smiles*", "(laughs)", "(pausa)",
    "(пауза)", "（間）"): a whole bracketed group of only the words of language.STAGE_DIRECTIONS and STAGE_SOFT, or a
    starred one of STAGE_STARRED's (third-person and -ing forms: "*smiles*", never "*pause*" or "*smile*"), in any of
    the 11 languages (owner, 18 Oct: her "(pause)" between chords was read aloud). One written in lower case goes
    wherever it stands ("Okay (pause) the next one is G"); a capitalised one only between sentences ("Great! (Laughs)
    Let me think."), as a title in brackets stays ("(Applause) by Lady Gaga", "Play (Smile) next"). Never bold
    ("**Beat** the eggs"), code (a fence or `inline`: "time.sleep(pause)"), a bracket right beside a letter or digit,
    or a link's text (review 18 Oct). The rest stays as written: the punctuation around it ("Em... (pause) ... C..." is
    "Em... C...", so a call still pauses there), a space before "!" (French), and every bracket that holds anything
    else ("C (live version)", "(G shapes)", "(x2)", "(if you want)"). Spoken (spoken_text) and sent (brain) alike: she
    never needs one."""
    removed = False

    def drop(match: re.Match[str]) -> str:
        nonlocal removed
        if match.group("code") is not None:
            return match.group(0)
        stars = match.group("stars")
        inner = stars or next(group for group in match.group("starred", "round", "square", "wide") if group)
        units = _stage_units(inner)
        words = _STAGE_STARRED_WORDS if stars else _STAGE_WORDS
        pre, post = match.group("pre"), match.group("post")
        before, after = text[: match.start() + len(pre)], text[match.end() - len(post) :]
        attached = (not pre and before and _attaches(before[-1])) or (not post and after and _attaches(after[0]))
        first = next((char for char in inner if char.isalpha()), "")
        capitalised = _cased(first) and first.isupper()
        if (
            not units
            or not made_of(units, words, _STAGE_SOFT_WORDS)
            or attached
            or (capitalised and not _between_sentences(before, after))
        ):
            return match.group(0)
        removed = True
        at_line_start = match.start() == 0 or text[match.start() - 1] == "\n"
        return "" if at_line_start else post

    cleaned = _STAGE.sub(drop, text)
    if not removed:
        return text
    return _REPEATED_DOTS.sub(r"\1", cleaned).strip()


def _dots_between_chords(text: str) -> str:
    """Three dots between two chords as a comma ("C... G... Am..." is "C, G, Am..."): as a full stop each chord was a
    sentence of its own, and the voice service read "G." as "Jay" and "Am." as "M" (the chord speech audit, 18 Oct).
    A chord is what theory's parser reads in some naming ("G", "Am7", "Sol", "Fis"); so is a joining word before one
    (`_JOINS`: "G... then C" is "G, then C"; the review, 18 Oct). A "..." after or before a word stays as it is
    ("Wait... A moment.", "G... then the chorus.")."""

    def chord(word: str | None) -> bool:  # as written ("Cm7(b5)"), or without the brackets or quotes around it ("(G")
        return word is not None and (theory._parses(word) or theory._parses(word.strip(_WRAPS)))

    def comma(found: re.Match[str]) -> str:
        first, after, then = found.groups()
        goes_on = chord(after) or (after.casefold() in _JOINS and chord(then))
        return first + "," if chord(first) and goes_on else found.group(0)

    return _DOTS_BETWEEN.sub(comma, text)


def _one_mark(found: re.Match[str]) -> str:
    """A run of marks as its first ("Wait.. what?!" is "Wait. what?"), but a comma or a semicolon after a dotted
    abbreviation's last dot stays, a pause ("N.C., then G", "e.g., apples"; the chord speech audit, 18 Oct; "| N.C. | G
    |" is "N.C.; G.": the review, the same day)."""
    if found.group(0) in (".,", ".;") and _DOTTED.search(found.string, 0, found.start()):
        return found.group(0)
    return found.group(1)


def spoken_text(answer: str, max_chars: int = 1500, *, min_chars: int = MIN_SPOKEN, rest: str = REST) -> str:
    """Gemma's answer as something to say aloud, or "" when it should not be spoken (spec 3.3).

    A call speaks even a one-word answer (min_chars=1) and has no text to point to (rest="")."""
    code = sum(len(block) for block in _FENCE.findall(answer))
    if code * 2 >= len(answer.strip()):
        return ""
    text = without_stage_directions(_FENCE.sub("\n", answer))
    text = _INLINE.sub(r"\1", _LINK.sub(r"\1", text))
    text = _COMMAND.sub(lambda found: f"slash {found.group(1).lower()}", _URL.sub("", text))  # before paths
    text = _EMOJI.sub("", _PATH.sub("", text))
    text = _EMPHASIS.sub("", signal_text.spoken(text))  # the text styles' reading; then any unpaired ** __ ~~
    blocks: list[str] = []
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        structural = False
        if line and _TABLE_RULE.match(line):
            continue
        if line.count("|") >= 2:
            line = "; ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())
            structural = True
        elif _HEADING.match(line) or _BULLET.match(line):
            line = _BULLET.sub("", _HEADING.sub("", line))
            structural = True
        if structural or not line:  # a heading, list item or table row is a sentence; a blank line ends one
            if current:
                blocks.append(" ".join(current))
                current = []
            if line:
                blocks.append(line)
            continue
        current.append(line)
    if current:
        blocks.append(" ".join(current))
    sentences = [block if block[-1] in ".!?:;。！？" else block + "." for block in (b.strip() for b in blocks) if block]
    spoken = re.sub(r"\(\s*\)", "", " ".join(sentences))  # brackets that only held a link
    spoken = re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", spoken)).strip()
    spoken = _MARKS_RUN.sub(_one_mark, _dots_between_chords(spoken)).strip()
    if len(spoken.rstrip(".")) < min_chars:
        return ""
    if len(spoken) <= max_chars:
        return spoken
    head = spoken[: max_chars - len(rest)]
    ends = [match.end() for match in _SENTENCE_END.finditer(head)]
    cut = head[: ends[-1]] if ends else head.rsplit(" ", 1)[0]
    return cut.rstrip() + rest


_SENTENCE_GAP = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])\s*")
# Never a sentence end: a title, "vs.", "e.g." or "i.e." before the next word (a name: "Dr. Smith", "PSV vs. Ajax",
# "e.g. Spotify"; the voice service would end a part that stops at "e.g." with a full stop); the others only before a
# lower-case letter or a digit ("8 a.m. now", "No. 5"), so "At 8 a.m. Then we go." still ends after "a.m.". A
# lower-case "no." is the word ("I said no. 5 people agreed."; the voice service reads only "no.7" as a number).
# "N.C." (no chord) too: "Intro: N.C. then G." stays one part, so the voice service reads its chords as one run; and
# before chords (`_chords_follow`: "N.C. G C D"; the review, 18 Oct).
_BEFORE_A_NAME = frozenset({"Dr.", "Mr.", "Mrs.", "Ms.", "St.", "vs.", "e.g.", "E.g.", "i.e.", "I.e."})
_BEFORE_LOWER_CASE = frozenset({"No.", "approx.", "Approx.", "a.m.", "p.m.", "A.M.", "P.M.", "N.C."})
_STRIP_MARKS = ".,;:!?"


def _chords_follow(rest: str) -> bool:
    """Whether `rest` starts with a chord theory's parser reads (in some naming) and then another one or the sentence's
    end ("G C D", "G.", "Sol Re Mim"), never a word ("A good start", "La casa es grande")."""
    words = rest.split(None, 2)[:2]
    if not words or not theory._parses(words[0].strip(_STRIP_MARKS)):
        return False
    return len(words) == 1 or words[0][-1] in ".!?" or theory._parses(words[1].strip(_STRIP_MARKS))


def sentences(spoken: str) -> list[str]:
    """Spoken text split at sentence ends, so a call can start speaking the first one while the rest is made; never
    after a title or an abbreviation inside a sentence (`_BEFORE_A_NAME`, `_BEFORE_LOWER_CASE`), in every language."""
    text = spoken.strip()
    parts: list[str] = []
    start = 0
    for gap in _SENTENCE_GAP.finditer(text):
        words = text[: gap.start()].split()
        last = words[-1].lstrip("(\"'“‘[") if words else ""
        after = text[gap.end() : gap.end() + 1]
        if last in _BEFORE_A_NAME or (last in _BEFORE_LOWER_CASE and (after.islower() or after.isdigit())):
            continue
        if last == "N.C." and _chords_follow(text[gap.end() :]):
            continue
        parts.append(text[start : gap.start()])
        start = gap.end()
    parts.append(text[start:])
    return [part for part in parts if part]


def _private_wav(wav: bytes, folder: Path) -> tuple[Path, Path]:
    """Write the WAV as a private file in a private folder; return it and the M4A path to convert to."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    name = uuid.uuid4().hex
    source, target = folder / f"{name}.wav", folder / f"{name}.m4a"
    fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(wav)
    return source, target


async def to_m4a(wav: bytes, folder: Path) -> Path:
    """WAV → AAC in M4A (a Signal voice note), as a private file in `folder`; the caller deletes it."""
    source, target = await asyncio.to_thread(_private_wav, wav, folder)
    try:
        proc = await asyncio.create_subprocess_exec(
            AFCONVERT,
            "-f",
            "m4af",
            "-d",
            "aac",
            str(source),
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), CONVERT_TIMEOUT)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise VoiceError("afconvert took too long") from None
        if proc.returncode != 0 or not target.is_file():
            raise VoiceError(f"afconvert failed (exit {proc.returncode})")
        target.chmod(0o600)
        return target
    except BaseException:
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        raise
    finally:
        source.unlink(missing_ok=True)

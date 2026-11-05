"""Voice calls with her (the assistant, by the config's name) over Signal.

CallManager reads signal-cli's call events and answers only allowed callers; strangers are ignored, never rejected
(a reject would send them something). It connects to the call helper's audio socket in a private folder and runs a
laid-back conversation: listen (long thinking pauses are fine), transcribe, one turn through the app (the same tools
as text), speak sentence by sentence, and let the caller talk over her: loudness or the voice service's speech
detector stops her ([calls] interrupt, see call_audio.Barge), and the words decide what the stop was (a cough, her own
voice from a loud speaker, a listener's "okay", or the caller's turn). Audio lives only in memory; logs hold metadata
only. The app is the Host: it stores the turns, runs Gemma and sends notes.

She places a call only for a reminder the user set (`place`, from the reminders' scheduler, which sends the text
first): to an allowed account only, one call at a time, and the call starts for the app only when it is picked up
(texts are not held while it rings). It rings for signal-cli's own minute; the bridge waits 75 s for the end and sorts
it into answered, no_answer, declined, busy or call_failed. A picked-up call opens with a code-written line in the
call's language ("Good morning! It's 7:00.") and the reminder's text, then goes on like any call.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import math
import os
import random
import re
import statistics
import sys
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.call_audio import (
    EARLY_BARGE_S,
    EARLY_KINDS,
    FRAME_S,
    LISTEN_FLOOR_FACTOR,
    LISTEN_MIN_RMS,
    RATE,
    Barge,
    CallAudio,
    TurnEnd,
    faint_noise,
    pcm48_to_wav16k,
    wav_to_pcm48,
)
from local_assistant.config import DEFAULT_NAME, has_voice
from local_assistant.language import (
    AND,
    BUT,
    BUT_WORDS,
    CALLING,
    CONTINUING,
    ENDING,
    ENDING_SOFT,
    ENGLISH,
    FAREWELLS,
    FUNCTION_WORDS,
    GOODBYES,
    GOODBYES_NOT_ALONE,
    GOODBYES_UNTIL,
    INTERRUPTIONS,
    JAPANESE_CALLER_WORDS,
    LANGUAGES,
    LEAVING,
    LEAVING_NOT_ALONE,
    LEAVING_SOFT,
    LEAVING_UNCLEAR,
    LEFT_OPEN,
    MORE_COMING,
    NEGATIONS,
    PARTING,
    PARTING_NOT_ALONE,
    PICKUP,
    PICKUP_SOFT,
    SO_FAR,
    SUBORDINATING,
    THANKS,
    YES_WORDS,
    acknowledgements,
    is_yes,
    made_of,
    negations,
    stop_words,
)
from local_assistant.timefmt import local_hhmm
from local_assistant.voice import (
    _BEFORE_A_NAME,
    _BEFORE_LOWER_CASE,
    Transcript,
    VoiceError,
    VoiceNotSaidError,
    detector_state,
    pocket_standard,
    sentences,
    spoken_text,
    without_stage_directions,
)
from local_assistant.wake import nearest, only_name, says_name

log = logging.getLogger(__name__)

LEAD_MS = 300  # faint noise before an answer: wakes the call's audio before the first word
CALL_MAX_TOKENS = 500  # owner, 26 Oct ("lets do 500"); a call says at most CALL_SPOKEN_CHARS, so this binds first
# beside Gemma 4 12B (owner, 28 Oct: "yes, lets have a different limit on the 12B"): a shorter answer, and longer to
# wait for one; both not measured on a 16 GB M3 yet (the review of 28 Oct, calls-01)
SMALL_CALL_MAX_TOKENS = 300
TURN_TIMEOUT_SECONDS = 45.0  # how long a call waits for one answer beside Gemma 4 26B (CallSettings' default)
SMALL_TURN_TIMEOUT_SECONDS = 60.0
# how much of an answer a call says: above CALL_MAX_TOKENS (500 tokens: about 2,000-2,300 characters in
# English, 600-750 in Japanese) in any language, so the token limit binds first (review of 26 Oct, DLG-01: the 1,500
# characters of voice.spoken_text's default left a full answer's end, her own view, unsaid); the owner may set it
CALL_SPOKEN_CHARS = 4_000
SPEECH_MAX_CHARS = 1_500  # the voice service's limit for one speak request (VoiceCore SpeechRequest.maxCharacters)
PAUSE_AFTER_FILLER_S = 0.35  # "Got it." … a short pause … the answer
INTERRUPTION_END_S = 1.2  # the end of what the caller says over her (shorter than a turn: a cough resumes sooner)
RESUME_BACK_S = 0.5  # after a cough she carries on a little before where she stopped
# false stops (no words, her own voice) in one clip before the next stop is taken as the caller's; acknowledgements in
# one clip before she carries on from the stop itself, no longer a little back
MAX_FALSE_BARGE = 3
CONNECT_SECONDS = 10.0
HANGUP_WAIT_S = 2.0  # her hang-up at a call's end: signal-cli's ENDED (local_hangup) within this, else once more
NOT_READY_NOTE_SECONDS = 600.0
PLACED_OUTCOMES = ("answered", "no_answer", "declined", "busy", "call_failed")

FILLERS = {"uh", "um", "umm", "hmm", "mm", "mhm", "ah", "oh", "huh", "eh", "er"} | {
    "äh", "ähm", "öhm", "euh", "ehm", "ehh", "eee", "yyy", "ээ", "эм", "мм", "음", "어", "えー", "えっと", "あの"
}  # fmt: skip
# one word alone over her that stops her: "stop", "wait", "but" ... in every language (and her name: name_words)
_STOP_ALONE = frozenset({"stop", "wait", "no", "hold", "sorry", "hey"} | INTERRUPTIONS)
ONE_WORD_INTERRUPTIONS = _STOP_ALONE | BUT_WORDS
# a yes word alone, in any language, stops her only when it answers her: she asked something or a device question
# waits (owner, 14 Oct: a listener's "okay" ended her answers)
ANSWER_WORDS = frozenset({"yes", "yeah", "okay", "ok"} | {word for word in YES_WORDS if " " not in word})
_WORD = re.compile(r"(?:[^\W\d_]|')+")  # letters of any script (and apostrophes): "don't", "café", "привет"
_NO_SPACES = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")  # Japanese is written without spaces
_STOP = {"stop", "wait", "hold"}
_STOP_FILL = {"on", "please", "a", "second", "sec", "minute", "moment", "ok", "okay", "just"}  # and her name


class CallSignal(Protocol):
    def call_events(self) -> AsyncIterator[dict[str, Any]]: ...

    async def accept_call(self, aci: str, call_id: int) -> dict[str, Any]: ...

    async def hangup_call(self, aci: str, call_id: int) -> None: ...

    async def start_call(self, aci: str) -> dict[str, Any]: ...


class Voice(Protocol):
    async def transcribe(
        self, audio: bytes, *, language: str | None = None, hint: str = "", call: bool = False
    ) -> Transcript: ...

    async def speak(self, text: str, *, language: str | None = None, context: str = "", call: bool = False) -> bytes:
        """A line in Pocket TTS, her only voice: one it cannot say, or in a language it has no model for, is
        VoiceNotSaidError (owner, 3 Nov 2026: no other voice says it)."""
        ...

    @property
    def voice(self) -> str:
        """Her voice now (/voice <voice> changes it at once): a canned line is kept in its voice."""
        ...

    def voice_for(self, language: str) -> str:
        """The voice a line in `language` is asked in (jane in the six languages Pocket TTS speaks, else hers)."""
        ...

    async def health(self) -> Mapping[str, object] | None: ...

    async def vad(self, chunk: bytes, *, stream: str) -> float | str: ...

    async def turn(self, window: bytes) -> float | str: ...


@dataclass(frozen=True)
class Turn:
    """What to say after the user spoke."""

    text: str  # Gemma's words, in the call's language
    reply_id: int | None = None  # the stored answer, cut where the user talked over it
    asks_yes: bool = False  # a device waits for the user's yes: the last sentence is spoken whole
    end_call: bool = False  # hang up once this was spoken (unless the user talked over it)
    # code's own words after Gemma's (a device question or result, why no reminder was set): always said in English
    code_text: str = ""
    # the lines of code_text that her stored reply keeps (the not-set line, the device question): cut off, it keeps
    # them only when they were reached, and never code's other lines (rule 6: those are code's notes)
    kept_code: tuple[str, ...] = ()
    # no answer: the model failed or took too long (her "lost my train of thought"): the call goes on, whatever the
    # caller's turn said (review of 0.9.7: "Okay, bye, and remind me at five" then hung up at once)
    failed: bool = False
    # the lines of code_text that also go as texts of their own (a reminder's, a device's, the lookup line; the chords
    # message after the call): a turn whose voice stopped (Pocket TTS could not say a part) leaves them out of the rest
    # it texts, so none comes twice
    also_texted: tuple[str, ...] = ()
    # the answer read outside text (Reply.read_outside): the rest of it, texted, is code's note quoting outside text
    outside: bool = False


class Downloads(Protocol):
    """jane's model of a language downloading (voice_download.LanguageDownload; owner, 25 Oct): nothing in it is spoken
    meanwhile, so a call in it waits for it."""

    def downloading(self, language: str) -> bool: ...

    def call_note(self, aci: str, language: str) -> str: ...

    async def wake(self, language: str, health: Mapping[str, object] | None) -> None:
        """Before a call in `language` is picked up: jane's model of it idle (the voice service restarted, or it rested
        after failed runs) gets the warm-up's line at once, so it loads while the call rings."""


class Host(Protocol):
    def call_started(self, aci: str, *, practice: bool = False) -> None:
        """A call starts (`practice`: a practice call, the call's own flag)."""

    def call_ended(self) -> None: ...

    async def call_warm_up(self, aci: str, *, practice: bool = False) -> None:
        """While the call rings, or once a call she placed is picked up: the model loads and reads the first turn's
        prompt up to the caller's words, so that turn finds it ready (`practice`: the call's own flag). Run once a call
        in the background: the call never waits for it, and its end leaves it to finish."""

    async def call_turn(
        self,
        aci: str,
        text: str,
        on_tools: Callable[[], None],
        *,
        practice: bool = False,
        on_retry: Callable[[], None] | None = None,
        text_only: bool = False,
    ) -> Turn:
        """What the user said: the answer (`practice`: a turn of a practice call, the call's own flag). `on_retry` is
        called once when the model's first try came back empty, before it is asked again. `text_only`: the answer is
        read, not heard (a practice reply keeps its "→" line)."""
        ...

    def call_interrupted(self, reply_id: int, heard: str) -> None: ...

    async def call_drop_waiting(self, aci: str, *, unheard: bool = False) -> None:
        """A device action waiting for the user's yes is dropped (they never heard the question whole, said
        stop or wait, or the answer was abandoned); a device that did switch is still reported. `unheard`: her voice
        could not ask the question (Pocket TTS could not say it): code's note says it was not switched and why."""

    async def call_standby(self, aci: str) -> None:
        """Standby starts (the caller quiet since the last turn): a device action waiting for a yes and her offer of
        the day's rundown are dropped, so a yes after her wake line switches and reads nothing."""

    async def call_note(self, aci: str, text: str) -> None: ...

    async def call_text(
        self, aci: str, text: str, *, reply_id: int | None, outside: bool, practice: bool = False
    ) -> None:
        """Code's text to the caller during the call (the rest of an answer her voice stopped for): stored as code's
        note in the conversation of her reply `reply_id` (the current one for None; a practice call's own for
        `practice`) and sent in the background, after the turn's own notes; `outside`: it quotes outside text."""

    async def call_reply_text(self, aci: str, reply_id: int) -> bool:
        """A text-only call's answer: her stored reply `reply_id`, whole, is sent as the text answer to the caller's
        turn (her own words, as a text message's answer; never code's note), after the turn's notes; whether it
        went out. Refused unless it is a call reply of `aci`."""

    async def call_summary(self, aci: str, lines: list[str], span: str) -> bool:
        """A short summary of the call as a note, its header naming the call's start and end (`span`, `call_span`);
        whether it went out (the model may fail or write nothing: code's own note then says when the call was)."""
        ...

    async def call_chords(self, aci: str) -> None:
        """[chords]: after the call, one message from code with the exact chords the call worked out and their
        source (sent only when a transpose ran); before the summary."""

    async def practice_call_ended(self, aci: str) -> None:
        """A practice call (/babble) the user spoke in ended: the session ends and its recap goes out at once, instead
        of the call summary."""


@dataclass(frozen=True)
class CallSettings:
    socket_dir: Path
    end_of_turn_s: float = 2.3  # owner, 17 Oct: 0.5 s less than 2.8
    idle_prompt_s: float = 90.0  # silence before the check-in and standby (owner, 18 Oct)
    # the check-in once, then at most this often (owner, 16 Oct); in standby "I'm still here." this often (18 Oct)
    idle_repeat_s: float = 600.0
    max_s: float = 3600.0
    summary: bool = True
    turn_timeout_s: float = TURN_TIMEOUT_SECONDS  # beside Gemma 4 12B 60 s: `call_limits`
    thinking_after_s: float = 0.7  # a thinking line comes first when the answer takes longer than this
    fillers: bool = False  # "hmm, let me think" / "let me check" lines while she thinks (user: off, sounded unnatural)
    acknowledge: bool = True  # a short "Got it." as soon as a request of 5+ words is taken in (user: on)
    greetings: tuple[str, ...] = ()  # English: one of these, never the same twice in a row; () = hers by name
    answer_after_s: float = 5.0  # let it ring about twice before answering (user: more natural)
    ring_wait_s: float = 75.0  # a call she places: signal-cli and RingRTC ring for 60 s; then it is a failed call
    wake_silence_s: float = 60.0  # after a wake-up call's opening, this much silence ends the call with a goodbye
    # after her plain goodbye, this much quiet ends the call; anything the caller says meanwhile keeps it (owner, 18 Oct:
    # "between me and gemma, we will be more succesful")
    goodbye_grace_s: float = 3.0
    # what stops her when the caller talks over her ([calls] interrupt): "speech" ("only speech", the default) or
    # "loudness" ("any sound"); call_audio.Barge's names
    barge: str = "speech"
    # the early end of a turn (owner, 17 Oct; call_audio.TurnEnd): Smart Turn asked once a pause at early_end_pause_s
    # ([calls] early_end, early_end_pause_seconds), and what keeps a turn open ([calls] pause): "speech" ("no speech",
    # the default: noise in a pause counts as pause) or "loudness" ("quiet": any sound)
    early_end: bool = True
    early_end_pause_s: float = 0.6
    pause: str = "speech"


def call_limits(timeout: float | None = None, *, standard: bool | None = None) -> tuple[int, float]:
    """A call answer's token cap and how long a call waits for it, by the Mac's tier: `standard` (None: this Mac's,
    `voice.pocket_standard`, read by its memory as setup chose the model) is a Mac setup gives Gemma 4 26B (30 GB of
    memory or more, `setup.choose_model`; its Pocket TTS model follows the same tier), else Gemma 4 12B. `timeout`:
    `[calls] turn_timeout_seconds`, which wins."""
    big = pocket_standard() if standard is None else standard
    tokens, waits = (
        (CALL_MAX_TOKENS, TURN_TIMEOUT_SECONDS) if big else (SMALL_CALL_MAX_TOKENS, SMALL_TURN_TIMEOUT_SECONDS)
    )
    return tokens, waits if timeout is None else timeout


# Whisper writes noise as words: sound labels (*sad music*, [Music]) and, on a short sound, its favourite fillers
# ("Thank you" is about a quarter of its output on noise). Some phrases are never said on a call at all.
_SOUND_LABEL = re.compile(r"[*\[(♪][^*\])♪]*[*\])♪]")
_NOISE_WORDS = frozenset({"thank you", "thank", "thanks", "bye", "you", "so", "i", "oh", "hmm", "mm", "the", "a"}) | {
    "merci", "danke", "gracias", "grazie", "obrigado", "obrigada", "bedankt", "dank je", "dank je wel", "dziękuję",
    "спасибо", "감사합니다", "ありがとう", "ありがとうございます", "ありがとうございました",
}  # fmt: skip
_NEVER_SAID = (
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "subtitles by",
    "like and subscribe",
    # the same in the other languages: subtitle credits and sign-offs from Whisper's training videos
    "tv gelderland",
    "sous-titrage",
    "sous-titres réalisés",
    "продолжение следует",
    "субтитры сделал",
    "ご視聴",
    "시청해 주셔서",
    "시청해주셔서",
)
NEVER_SAID_MAX_WORDS = 6  # such a phrase is noise only when it is (nearly) all that was heard
# subtitle credits nobody says on a call: noise however long the transcript ("Sous-titres réalisés par la communauté
# d'Amara.org" has 8 words)
_CREDITS = ("amara.org", "im auftrag des zdf", "a cura di qtss", "dimatorzok")
NOISE_VOICED_S = 0.6  # a filler word on less sustained voice than this (key clicks are none) is noise
# Less voiced sound than this is not sent to speech recognition at all: just above the 10-frame start run, so a brisk
# "yes" or "no" (0.15-0.3 s of voice) still counts; is_noise_transcript sorts out the rest.
MIN_VOICED_S = 0.12


def voice_ready(health: Mapping[str, object] | None, voice: str = "") -> bool:
    """Whether the voice service can take a call: it answers and both engines (speech in, speech out) are ready, and
    `voice` (her English voice, which says code's own words on any call) is not one her ready model leaves out
    (`voices_left_out`: a shipped voice's file missing, not the pinned one or failing its first run; the review of
    4 Nov: heart's missing file left every call silent). Never a stand-in voice (owner, 3 Nov 2026)."""
    if health is None or health.get("stt") != "ready" or health.get("tts") != "ready":
        return False
    return not voice or voice not in _left_out(health)


def voice_not_working(health: Mapping[str, object] | None, voice: str = "") -> bool:
    """Whether speaking cannot work until a later try or an update, rather than still loading: her English Pocket model
    resting after failed runs ("resting"), unable to run ("failed": its program missing, its files refused), or `voice`
    one it leaves out. The call's note then says her voice is not working (CALL_VOICE_FAILED), never that it loads."""
    if health is None:
        return False
    return health.get("tts") in ("resting", "failed") or bool(voice and voice in _left_out(health))


def _left_out(health: Mapping[str, object]) -> tuple[str, ...]:
    listed = health.get("voices_left_out")
    return tuple(str(name) for name in listed) if isinstance(listed, list | tuple) else ()


def is_noise_transcript(text: str, sustained_s: float) -> bool:
    """Whether a transcript is noise Whisper turned into words, in any language: only sound labels, a subtitle credit,
    a short transcript that is a phrase never said on a call (inside a longer sentence it is real), or a lone filler
    word on little sustained voice (`listen`'s sustained_s: typing is loud often, never for long). A short real answer
    ("yes", "stop") is never noise."""
    heard = " ".join(_SOUND_LABEL.sub(" ", text.lower()).split())
    found = _WORD.findall(heard)  # words in any language ("Привет" is words, "*music*" is none)
    if not found:
        return True
    words = " ".join(found)
    if any(credit in heard for credit in _CREDITS):
        return True
    if len(found) <= NEVER_SAID_MAX_WORDS and any(phrase in heard or phrase in words for phrase in _NEVER_SAID):
        return True
    return words in _NOISE_WORDS and sustained_s < NOISE_VOICED_S


def real_words(text: str) -> list[str]:
    """The words of a transcript, in any language, without fillers ("um", "äh") and without Whisper's sound labels
    ("*sad music*" is no word)."""
    return [word for word in _WORD.findall(_SOUND_LABEL.sub(" ", text.lower())) if word not in FILLERS]


ACK_MIN_WORDS = 5  # a shorter turn ("how are you?", "yes", "thanks") gets its answer without a "got it" (owner)
# her wake line comes when her answer to the turn that ended standby is not ready by then (the owner's 51 call turns,
# 22 Oct: none was answered in under 1.9 s), so an answer that is already fast comes without it
WAKE_LINE_AFTER_S = 0.3
# on: in standby her name goes to Whisper as its hint (owner, 22 Oct: her name said in standby was missed); outside
# standby never, so a hint never writes her name into an ordinary turn's noise. Off: the wake harness
# (evals/wake_harness.py, 22 Oct) found no wake gained and false wakes with it (names near hers; silence and noise),
# none without it; 23 Oct (--call, a call's cut on a phone line) 550 of 552 wakes without it once Whisper heard clips of
# 1 s or less. Switch it on only after the harness shows more wakes and no false wake
STANDBY_HINT = False
# on: in standby a turn may end early, at Smart Turn's answer, as outside it. Off (the review of 28 Oct, calls-02):
# standby waits the full pause (or the pause by speech), as the wake harness measured on the default name, "Estia"
# (23 Oct, --call, 1,832 streams: 18 items woke only with the full pause, 4 only with the early end; McNemar's exact
# test p = 0.004; "Tiffany" 0 and 3, p = 0.25), so her name said with a pause after it is one turn and never a lone
# clip Whisper hears as nothing
STANDBY_EARLY_END = False


def calling_words(language: str) -> frozenset[str]:
    """The words beside her name that call her, in a call's language and English ("hey", "hallo"), and the fillers."""
    return frozenset(CALLING.get(language, ()) + CALLING[ENGLISH]) | FILLERS


def turn_words(text: str) -> int:
    """How many words the caller said: fillers and sound labels left out; Japanese, written without spaces, counts
    about two characters a word."""
    return sum(max(1, len(word) // 2) if _NO_SPACES.search(word) else 1 for word in real_words(text))


@functools.cache
def name_words(name: str) -> frozenset[str]:
    """Her name as a transcript's words: "Mary-Jane" is "mary" and "jane"."""
    return frozenset(real_words(name))


def _form(word: str) -> str:
    """A word as compared for an acknowledgement or leave-taking: lower case, a straight apostrophe, Latin letters
    without accents ("oké" is "oke"); other scripts as written (Japanese keeps its voicing marks)."""
    word = word.lower().replace("’", "'")
    if all(ord(char) < 0x250 for char in word):
        return "".join(char for char in unicodedata.normalize("NFKD", word) if not unicodedata.combining(char))
    return unicodedata.normalize("NFC", word)


def _units(text: str, language: str) -> tuple[str, ...]:
    """A text's words as compared (_form), in order; Japanese, written without spaces, its characters."""
    words = [_form(word) for word in _WORD.findall(_SOUND_LABEL.sub(" ", text.replace("’", "'")))]
    return tuple("".join(words)) if language == "ja" else tuple(words)


def _phrases(texts: Collection[str], language: str) -> frozenset[tuple[str, ...]]:
    return frozenset(_units(text, language) for text in texts) - {()}


@functools.cache
def _acknowledging(language: str) -> tuple[frozenset[tuple[str, ...]], frozenset[tuple[str, ...]]]:
    """A call language's acknowledgements and the fillers allowed beside them, as compared."""
    return _phrases(acknowledgements(language), language), _phrases(FILLERS, language)


def _asked(text: str) -> bool:
    return "?" in text or "？" in text


def is_acknowledgement(text: str, language: str) -> bool:
    """A listener's acknowledgement over her: only the call language's acknowledgement words and the hums Whisper
    writes (language.acknowledgements), fillers allowed beside them ("Okay.", "Oh, I see.", "Mm-hmm.", "Ja, klopt.",
    "はい、なるほど"). Anything more ("okay, and tomorrow?", "yes but") is none, and so is one asked back ("네?",
    "はい？", "Right?": "pardon?" or "really?"). Japanese by characters."""
    return not _asked(text) and made_of(_units(text, language), *_acknowledging(language))


def asks(hers: Sequence[str]) -> bool:
    """Whether her sentence being said, or the one before it (`hers`), asks something: it ends with a question mark,
    or in Japanese with か, a polite question often written without one ("明日にしますか。")."""
    ends = (sentence.rstrip().rstrip("\"')]”’»」』").rstrip("。") for sentence in hers)
    return any(end.endswith(("?", "？", "か")) for end in ends)


def last_asks(answer: str) -> bool:
    """Whether an answer's last sentence asks something (`asks`): a full answer may ask on its way ("But is that really
    freedom?"), and a yes after it answers nothing (review of 26 Oct, DLG-05; call_host's offer of the rundown)."""
    return asks(sentences(answer)[-1:])


@functools.cache
def _greeting(language: str, name: str) -> tuple[frozenset[tuple[str, ...]], frozenset[tuple[str, ...]]]:
    """The words over her opening that greet or answer the phone (`calling_words`, language.PICKUP), and the ones
    allowed beside them (her name, language.PICKUP_SOFT), the call language's and English, as compared."""
    codes = dict.fromkeys((language, ENGLISH))
    pickup = [word for code in codes for word in PICKUP.get(code, ())]
    soft = [word for code in codes for word in PICKUP_SOFT.get(code, ())]
    return _phrases([*calling_words(language), *pickup], language), _phrases([*name_words(name), *soft], language)


def only_greeting(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """Words over the opening of a call she placed that only greet or call ("Hello?", "Hallo?", "Hey, hello.", "Hello,
    Estia?"): the call language's and English's calling words (language.CALLING) and fillers, her name beside them,
    with or without a question mark (the second deep scan of 0.9.8, calls-hello-over-opening: the callee's "Hello?"
    took over, and the time and the reminder were never said on the call); or words that answer the phone
    (language.PICKUP: "Pronto?", "¿Diga?"), and a yes word beside such words, never alone (language.PICKUP_SOFT: "Ja,
    hallo?", "Да, алло?", never "Ja?"; review of 0.9.8, V02: they took the turn)."""
    return made_of(_units(text, language), *_greeting(language, name))


def is_interruption(text: str, *, name: str, language: str = ENGLISH, asking: bool = False) -> bool:
    """Talking over her counts only with real words: two or more, or one clear word like "stop", "but" or her name (in
    any of the languages; Japanese, without spaces, counts its characters). A cough, a noise or "um" transcribes as
    nothing, "-" or a filler, and she carries on; so does a listener's acknowledgement alone ("okay", "yeah, right",
    "mm-hm" in the call's `language`: is_acknowledgement), unless she is `asking` (her sentence or the one before it
    asks something, or a device question waits): then it, or a yes word alone in any language, answers her. One word
    asked back ("Really?", "네?") always counts."""
    if is_acknowledgement(text, language):
        return asking
    words = real_words(text)
    units = sum(len(word) if _NO_SPACES.search(word) else 1 for word in words)
    alone = ONE_WORD_INTERRUPTIONS | name_words(name) | (ANSWER_WORDS if asking else frozenset())
    return units >= 2 or (len(words) == 1 and (words[0] in alone or _asked(text)))


# Her own voice: a loud speaker next to the caller's microphone (a laptop on Signal Desktop) plays her back, and the
# stop's transcript is her own sentence. Only what she already played counts (the sentence before and this one up to
# the stop): at least OWN_VOICE_MIN_WORDS of her content words (short function words left out), at least
# OWN_VOICE_SHARE of the transcript's content words, and OWN_VOICE_RUN words or more in a row exactly as she said
# them; a stop, yes or no word she did not say makes it the caller's. Then she carries on as after a cough. Japanese,
# written without spaces, by characters: pairs for the share and a run of OWN_VOICE_RUN_CHARS (about as many words).
# Review of 13 Oct: "the dentist at three", "turn it off" over "Should I turn the heater off?", "no, not Amsterdam" over
# "... in Amsterdam" and "wait, it is at five" are the caller's.
OWN_VOICE_MIN_WORDS = 3
OWN_VOICE_SHARE = 0.6
OWN_VOICE_RUN = 3
OWN_VOICE_RUN_CHARS = 2 * OWN_VOICE_RUN  # Japanese counts about two characters a word


def _plain_word(word: str) -> str:
    """A word as compared for her own voice: lower case, without apostrophes or accents."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", word.lower()) if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", plain).replace("'", "").replace("’", "")


_FUNCTION_PLAIN = frozenset(_plain_word(word) for word in FUNCTION_WORDS)
# words that are the caller's unless her played text has them too: stop and yes words, a negation
_CALLER_PLAIN = frozenset(_plain_word(word) for word in _STOP_ALONE | ANSWER_WORDS | NEGATIONS)


def _compared(text: str) -> list[str]:
    """A text's real words as compared for her own voice (_plain_word), in order."""
    return [_plain_word(word) for word in real_words(text.replace("’", "'"))]


def _longest_run(heard: Sequence[str], hers: Sequence[str]) -> int:
    """How many words (or characters) in a row the transcript has exactly as in her text."""
    best = 0
    previous = [0] * (len(hers) + 1)
    for unit in heard:
        current = [0] * (len(hers) + 1)
        for index, her_unit in enumerate(hers, 1):
            if unit == her_unit:
                current[index] = previous[index - 1] + 1
                best = max(best, current[index])
        previous = current
    return best


def played_part(hers: Sequence[str], fraction: float) -> tuple[str, ...]:
    """Her sentences as far as the caller can have heard them: the ones before whole, the last up to `fraction` of it
    (the stop's place in the clip), without a word cut in half (Japanese, without spaces, by characters)."""
    if not hers:
        return ()
    *before, last = hers
    end = int(len(last) * fraction)
    if end >= len(last):
        return (*before, last)
    cut = last[:end]
    if not last[end].isspace() and " " in cut:  # inside a word: that word is not said yet
        cut = cut.rsplit(" ", 1)[0]
    return (*before, cut.rstrip())


def _own_voice_japanese(text: str, played: Sequence[str]) -> bool:
    heard = "".join(real_words(text))
    hers = "".join(word for sentence in played for word in real_words(sentence))
    if any(word in heard and word not in hers for word in JAPANESE_CALLER_WORDS):
        return False
    pairs = [heard[i : i + 2] for i in range(len(heard) - 1)]
    her_pairs = {hers[i : i + 2] for i in range(len(hers) - 1)}
    if not pairs or sum(pair in her_pairs for pair in pairs) < OWN_VOICE_SHARE * len(pairs):
        return False
    return _longest_run(heard, hers) >= OWN_VOICE_RUN_CHARS


def is_own_voice(text: str, played: Sequence[str]) -> bool:
    """Whether a transcript of what stopped her is her own voice: a long part of what she already `played`, in her
    order (see OWN_VOICE_MIN_WORDS), in any of the languages. A word only the caller says ("no", "wait", "yes") that
    she did not say makes it the caller's, and so does a question when what she played asked none (review 0.9.7, C9: a
    repeat-back, "So the dentist at three tomorrow?")."""
    if turn_words(text) < OWN_VOICE_MIN_WORDS:
        return False
    if _asked(text) and not any(_asked(sentence) for sentence in played):
        return False
    if _NO_SPACES.search(text):
        return _own_voice_japanese(text, played)
    heard = _compared(text)
    hers = [word for sentence in played for word in _compared(sentence)]
    said = set(hers)
    if any(word in _CALLER_PLAIN and word not in said for word in heard):
        return False
    content = [word for word in heard if word not in _FUNCTION_PLAIN]
    mine = sum(word in said for word in content)
    if mine < OWN_VOICE_MIN_WORDS or mine < OWN_VOICE_SHARE * len(content):
        return False
    return _longest_run(heard, hers) >= OWN_VOICE_RUN


def is_lone_stop(text: str, *, name: str) -> bool:
    """ "Stop", "wait", "hold on", "wait a second", "stop Estia": she goes quiet and listens; Gemma is not asked."""
    words = real_words(text)
    return (
        bool(words)
        and len(words) <= 3 + len(name_words(name))  # four words, a longer name counted as one
        and any(w in _STOP for w in words)
        and all(w in _STOP | _STOP_FILL | name_words(name) for w in words)
    )


# a sentence's end: a mark and its closing quotes or brackets before a space, or a full-width mark of Japanese (or
# Chinese) with no space after it (review of 26 Oct, rtcall-4: "自由意志は難しい問題です。決定論は" was said whole)
_LAST_SENTENCE_END = re.compile(r"""[.!?…]["')\]]*(?=\s)|[。！？][」』）"')\]]*""")


def _ends_a_sentence(text: str, found: re.Match[str]) -> bool:
    """Whether a mark ends a sentence: never a title, "vs.", "e.g." or "i.e." before the next word, nor "a.m." or "No."
    before a lower-case letter or a digit (`voice.sentences`' rule; review of 26 Oct, rtcall-10: a cut answer ended on
    "As Dr.")."""
    last = text[: found.end()].rsplit(None, 1)[-1]
    after = text[found.end() :].lstrip()[:1]
    return not (last in _BEFORE_A_NAME or (last in _BEFORE_LOWER_CASE and (after.islower() or after.isdigit())))


def whole_sentences(text: str, *, cut: bool = True) -> str:
    """An answer that was cut off at the token limit (`cut`: the model's finish reason was "length") ends at its last
    whole sentence (she never stops mid-sentence); one the model ended itself is said whole, also when it ends without
    a mark (an emoji, a name: review of 26 Oct, rtcall-10), and so is one with no sentence end at all."""
    stripped = text.rstrip()
    if not cut or not stripped or stripped[-1] in ".!?…\"')]。！？」』":
        return stripped
    ends = [found for found in _LAST_SENTENCE_END.finditer(stripped) if _ends_a_sentence(stripped, found)]
    return stripped[: ends[-1].end()] if ends else stripped


# her closing question after a full answer goes (owner, 26 Oct: "I ask a deep question and I get a question back. Take
# the time to explain in full"; the call rules ask a question back only when she cannot answer without it, and the 0.9.8
# gate, 28 Oct, still had all 10 of the 12B's answers to a deep question end "What do you think?", the 26B's too): a
# full answer is at least this many words before it, about 25 s of speech; a shorter one keeps its question
CLOSING_QUESTION_AFTER_WORDS = 60


def _last_sentence_start(text: str) -> int:
    """Where the text's last spoken sentence starts: right after the sentence end before it; an emoji, a stage
    direction or markup after its own end ("What do you think? 😄") belongs to it, as the call says none of them."""
    ends = [found.end() for found in _LAST_SENTENCE_END.finditer(text) if _ends_a_sentence(text, found)]
    return next((end for end in reversed(ends) if real_words(heard(text[end:]))), 0)


# a yes to her offer of the day's rundown on a call: code reads today's calendar and the mail digest itself (live
# evals, 13 Oct: after "yes please" she called no tool, 7 of 7), as her own tool calls would
RUNDOWN_READS = (("calendar_agenda", '{"start": "today", "days": 1}'), ("mail_digest", "{}"))


def rundown_reads(offer: str, said: str, language: str, *, calendar: bool, mail: bool) -> tuple[tuple[str, str], ...]:
    """What code reads for her when the user says yes to her offer of the day's rundown on a call: `offer` is her
    offer, the call's previous answer ("" when that answer was none: `CallGlance.offer`); it asks something ("Want the
    rundown?") and the user's reply is a whole yes (`is_yes`, in the call's language or English). Then today's
    calendar and the mail digest, each when it is set up; anything else reads nothing (she reads with her tools as
    ever)."""
    if not last_asks(offer) or not is_yes(said, language):
        return ()
    return tuple(read for read, on in zip(RUNDOWN_READS, (calendar, mail), strict=True) if on)


def without_last_questions(text: str) -> str:
    """The text without the questions it ends with, as written ('"who decides?"') or as said (no emoji); "" when it is
    only questions. While a device change waits, a text answer ends with code's question instead, so a yes never
    answers hers (second review of 0.9.8, redteam-05)."""
    kept = text.rstrip()
    while kept:
        start = _last_sentence_start(kept)
        if not asks([kept[start:], heard(kept[start:])]):
            break
        kept = kept[:start].rstrip()
    return kept


# an opening quotation mark and the marks that close it (a straight one closes with a straight one; German opens low
# and closes with either curly mark); an apostrophe is no quotation mark ("it's")
_QUOTES = {'"': '"', "“": "”", "„": "“”", "«": "»", "「": "」", "『": "』"}
_LINES = re.compile(r"(\n+)")
_OFFER_WORDS = (
    12  # a closing pattern of this many words or more is an offer to wait ("I'll be here whenever you're ready")
)
_SENTENCE_MARKS = ".!?…。！？"


def _in_quotation(text: str) -> bool:
    """Whether `text` ends inside a quotation: one a mark of `_QUOTES` opened that no closing mark has closed."""
    waiting: list[str] = []  # the marks each open quotation waits for, the innermost last
    for char in text:
        if waiting and char in waiting[-1]:
            waiting.pop()
        elif char in _QUOTES:
            waiting.append(_QUOTES[char])
    return bool(waiting)


def without_goodbye_parts(text: str, *, name: str, language: str = ENGLISH) -> str:
    """Her answer without every goodbye part of it, wherever it stands: a plain goodbye of the call's language or English
    (language.GOODBYES: "Bye!", "Goodbye, Alex!"), a closing wish (language.FAREWELLS: "Have a great day!", "Take care
    and have a good night!"), one joined to the part before it with "and" ("Please say yes, and goodbye!", "...
    yes. And goodbye!") and a name after one ("Take care, Alex, and ..."), at the start ("Bye! Can you say yes?"), in the
    middle or at the end; then the wrap-up she says back at the end (`is_wrap_up`: "Thanks, that's all for today.").
    A sentence that is only closing words goes whole, a comma that led to a goodbye becomes the full stop, and the
    first word left stands as a sentence's first ("Bye, can you say yes?" is "Can you say yes?"); "" when that is all of
    it. While a device change waits for the caller's yes the call stays open for it, so her goodbye never stands before
    code's question (`Tools.without_goodbye`, calls-099-01: the gate run of 4 Nov had all ten answers end "Goodbye!", and
    the one of 5 Nov a goodbye joined with "And", one at the start and one named last). Never a goodbye in quoted speech
    (after a colon or inside quotation marks), one that asks ("Bye?"), one with soft words after it ("Bye, though."), an
    offer to wait ("I'll be here whenever you're ready") beside other words, or one of a language with no list here."""
    lines = _LINES.split(text)  # a line break ends a sentence in speech: line, break, line, ...
    kept, before = "", 0
    for at in range(0, len(lines), 2):
        line, gap = lines[at], lines[at + 1] if at + 1 < len(lines) else ""
        left = _line_without_goodbye(line, text[:before], name=name, language=language)
        before += len(line) + len(gap)
        if left or not real_words(line):  # a line left with no words goes with its break
            kept += left + gap
    kept = kept.rstrip() if kept != text else text
    while kept:
        start = _last_sentence_start(kept)
        if not is_wrap_up(kept[start:], name=name, language=language):
            break
        kept = kept[:start].rstrip()
    return kept


def _line_without_goodbye(line: str, before: str, *, name: str, language: str) -> str:
    """`without_goodbye_parts` for one line (`before`: the text before it, for the quotation marks left open there)."""
    parts = _turn_parts(line, language)
    if not parts:
        return line
    phrases, soft = _her_goodbyes(language, name)
    until = _phrases(
        [bye for code in dict.fromkeys((language, ENGLISH)) for bye in GOODBYES_UNTIL.get(code, ())], language
    )
    closing, _ = _closing(language, name)
    joins = _joins(language)
    subordinating = _phrases(
        [word for code in dict.fromkeys((language, ENGLISH)) for word in SUBORDINATING.get(code, ())], language
    )
    split = re.compile(rf"\s+(?:{'|'.join(map(re.escape, sorted(joins)))})\s+", re.IGNORECASE)
    units = [_units(part.text, language) for part in parts]
    starts = [part.at for part in parts]
    ends = [*starts[1:], len(line)]

    def unjoined(of: tuple[str, ...]) -> tuple[str, ...]:
        return of[1:] if of and of[0] in joins else of  # the "and" that joins a goodbye is no part of it

    def kind(piece: str, *, until_ok: bool) -> str | None:
        """What a piece of a part is: "bye", a closing "wish", an "offer" to wait, or none of them."""
        led = _units(piece, language)
        of = unjoined(led)
        # "until <a time>" ("hasta luego") is a goodbye after soft words only, or when an "and" leads it ("Di que sí, y
        # hasta luego.")
        if made_of(of, phrases - until, soft) or ((until_ok or of != led) and made_of(of, phrases, soft)):
            return "bye"
        most = _farewell_part(piece.strip().rstrip(_SENTENCE_MARKS + " "), language)
        return None if most is None else ("offer" if most >= _OFFER_WORDS else "wish")

    def part_kind(index: int) -> str | None:
        part = parts[index]
        word = _WORD.search(line, part.at)  # past the mark that leads the part
        if part.quoted or _asked(part.text) or asks([part.text]):
            return None
        if _in_quotation(before + line[: word.start() if word else part.at]):  # words she quotes are not hers
            return None
        earlier = [units[j] for j in range(index) if parts[j].sentence == part.sentence]
        later = [j for j in range(index + 1, len(parts)) if parts[j].sentence == part.sentence]
        until_ok = all(made_of(one, closing | soft, closing | soft) for one in earlier) and all(
            made_of(units[j], closing | soft, closing | soft) or is_name(j) for j in later
        )  # "Hasta mañana, seguirá lloviendo." keeps its time: content after an until-goodbye (review of 5 Nov)
        if (found := kind(part.text, until_ok=until_ok)) is not None:
            return found
        sides = [side for side in split.split(part.text) if side.strip()]
        if len(sides) > 1 and all(kind(side, until_ok=until_ok) in ("bye", "wish") for side in sides):
            return "wish"  # "Take care and have a good night!"
        return None

    def tail(index: int) -> int | None:
        """Where, in the span of a part that is none, a closing joined to it with "and" starts ("Please say yes and
        goodbye!": at the "and"), or None."""
        core = line[starts[index] : ends[index]].rstrip()
        part = parts[index]
        if part.quoted or _asked(core) or _in_quotation(before + line[: starts[index]]):
            return None
        gaps = list(split.finditer(core))
        for gap in gaps:
            if kind(core[gap.end() :], until_ok=False) in ("bye", "wish") and real_words(core[: gap.start()]):
                return gap.start()
        return None

    def is_name(index: int) -> bool:
        return _a_name(parts[index].text, soft)

    kinds = [part_kind(index) for index in range(len(parts))]

    def joined(index: int) -> bool:
        return bool(units[index]) and units[index][0] in joins  # "and have a great day": an "and" leads the part

    def starts_a_clause(index: int) -> bool:
        """A part that starts with a subordinating word ("once", "before you go"): a clause that led to the wish."""
        return any(units[index][: len(one)] == one for one in subordinating)

    def trails(index: int) -> bool:
        """A part after a goodbye in its sentence that is one word or soft words only, no name ("Bye, though.", "Take
        care, okay."): the goodbye is none (review 18 Oct), and a name after it goes with it."""
        return not is_name(index) and (len(units[index]) == 1 or made_of(units[index], soft, soft))

    def droppable(index: int) -> bool:
        return made_of(units[index], closing | soft, closing | soft)

    gone: set[int] = set()
    cuts: dict[int, int] = {}
    for number in dict.fromkeys(part.sentence for part in parts):
        mine = [index for index, part in enumerate(parts) if part.sentence == number]
        closes = {i for i in mine if kinds[i] in ("bye", "wish")}
        offers = {i for i in mine if kinds[i] == "offer"}  # an offer to wait goes only from a closing sentence
        if offers and all(i in closes | offers or droppable(i) or is_name(i) for i in mine):
            closes |= offers
        closes = {i for i in closes if not (i + 1 in mine and trails(i + 1))}  # "Bye, though." stays
        # a wish goes only from a closing sentence, led by an "and" or beside a goodbye: "Take care, the heater is still
        # hot." and "Have a light meal, drink water." are advice (review of 5 Nov, voice-F3)
        # (a clause that only led to a wish at its end counts as closing: "Once it's off, sleep well!", voice-F2)
        ends_closing = mine[-1] in closes
        closing_sentence = all(
            i in closes | offers or droppable(i) or is_name(i) or (ends_closing and starts_a_clause(i)) for i in mine
        )
        closes = {
            i
            for i in closes
            if kinds[i] != "wish"
            or closing_sentence
            or joined(i)
            or any(j in closes and kinds[j] == "bye" for j in (i - 1, i + 1))
        }
        cut_here = {i for i in mine if i not in closes and kinds[i] is None and tail(i) is not None}
        removed: set[int] = set()
        for i in mine:
            # a name after a goodbye, or after a part cut at its "and", goes with it, her own name too
            named = is_name(i) or units[i] == _units(name, language)
            if i in closes or (is_name(i) and i - 1 in removed) or (named and i - 1 in cut_here):
                removed.add(i)
        if removed and all(i in removed or droppable(i) or is_name(i) for i in mine):
            removed = set(mine)  # only closing words, thanks and names: the sentence goes whole
        elif removed and mine[-1] in removed and all(starts_a_clause(i) for i in mine if i not in removed):
            removed = set(mine)  # "Once it's off, sleep well!": a clause left without its wish would stand broken
        gone |= removed
        cuts |= {i: at for i in mine if i not in removed and kinds[i] is None and (at := tail(i)) is not None}
    if not gone and not cuts:
        return line
    out = [line[: starts[0]]] if 0 not in gone else []
    for number in dict.fromkeys(part.sentence for part in parts):
        mine = [index for index, part in enumerate(parts) if part.sentence == number]
        stays = [i for i in mine if i not in gone]
        for place, i in enumerate(stays):
            span = line[starts[i] : ends[i]]
            if i in cuts:
                core = span.rstrip()
                span = core[: cuts[i]].rstrip() + "." + span[len(core) :]
            if place == 0 and i != mine[0]:  # what leads the sentence now stands as its first
                span = next(
                    (span[:k] + span[k].upper() + span[k + 1 :] for k, c in enumerate(span) if c.isalpha()), span
                )
            if place == len(stays) - 1 and mine[-1] in gone:  # its end went: a comma that led to it is the full stop
                core = span.rstrip()
                end = line[starts[mine[-1]] : ends[mine[-1]]]
                span = _stopped(core) + end[len(end.rstrip()) :]
            out.append(span)
    return "".join(out)


def _stopped(text: str) -> str:
    """`text` as a sentence's end: a comma, semicolon or 、 at its end becomes the full stop, and one without a mark of
    its own gets one."""
    if text.endswith((",", ";")):
        return f"{text[:-1]}."
    if text.endswith("、"):
        return f"{text[:-1]}。"
    return text if text.rstrip("\"')]”’»」』").endswith(tuple(_SENTENCE_MARKS)) else f"{text}."


def without_closing_questions(text: str, *, name: str, language: str = ENGLISH) -> str:
    """Her answer on a call without the questions it ends with, when what comes before them is a full answer: at least
    CLOSING_QUESTION_AFTER_WORDS words (`turn_words` of what the call says) that do not end as her goodbye (that ends
    the call after its quiet, `ends_with_goodbye`, while a question keeps it open); else the text as it is. A question
    asked on its way stays, and so does one after a short answer ("I can call you. What time?")."""
    kept = without_last_questions(text)
    if not kept or kept == text.rstrip() or turn_words(heard(kept)) < CLOSING_QUESTION_AFTER_WORDS:
        return text
    if ends_with_goodbye(heard(kept), name=name, language=language):
        return text
    log.info("calls: her closing question left out after a full answer (%d words kept)", turn_words(heard(kept)))
    return kept


def speakable(note: str) -> str:
    """Code's own line said aloud: a device note "Devices: heater off" becomes "Done: heater off."; others (a reminder
    line, written with its spoken reasons) stay as they are."""
    if note.startswith("Devices: "):
        return "Done: " + note.removeprefix("Devices: ").replace(" · ", ", ").rstrip(".") + "."
    return note


@functools.cache
def _goodbye(name: str) -> re.Pattern[str]:
    """A plain goodbye, maybe after "okay", "no thanks" or her name, maybe with her name after it. Never after a
    "yes" or "yeah": that may answer a waiting device question, so Gemma's turn handles it."""
    her = re.escape(" ".join(real_words(name)))
    return re.compile(
        r"^(?:(?:ok|okay|alright|all right|right|well|thanks|thank you|very much|so much|a lot|great|cool|so|perfect"
        rf"|no|nope|{her})\s+)*"
        r"(?:bye(?: bye)*|good ?bye|bye for now|(?:talk|speak) (?:to you )?(?:later|soon)|catch you later"
        r"|see you(?: later| soon| tomorrow)?|take care|good ?night|hang up)"
        rf"(?:\s+(?:{her}|then|now|for now))*$"
    )


# a wrap-up's own words: "that's", "thats" or "that is", never "that it" or "that all" ("Check that it's locked", "Make
# sure that all the lights are off"), and "we're done", never "were done" (second fix review of 0.9.8,
# redteam-calls-wrap-up-lookalike: the apostrophes were optional); "that'll do" is one word "that'll" to Whisper
_WRAP_UP_WORDS = (
    r"that(?: will|'ll| should| would) do(?: it)?|that(?:'s|s) (?:all|it|everything|enough)|that is (?:all|it|everything|enough)"
    r"|nothing (?:else|more)|no more questions|i'?m done|we're done"
)
# a part that is a wrap-up alone (`_wraps_up`): its words, maybe with what it was for or what was needed ("that's it
# for the page", "that's all I needed"), and soft words beside them; never one with more after it ("that's it for
# mail now the calendar")
_WRAP_UP_ALONE = re.compile(
    rf"(?:{_WRAP_UP_WORDS})(?: here| for (?:(?:the|this|my|your) )?[a-z']+| i (?:need|needed|want|wanted|had)"
    r"(?: to (?:know|ask))?)?"
)


def is_wrap_up(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """A short "that will do for now" / "that's all" / "nothing else": the user is finishing. After such a turn the
    call ends once her answer is spoken, unless the answer asks something ("I'm good" is not one: it also answers
    "how are you?"). At most 8 words, no question mark, and a wrap-up by `_wraps_up`'s rule: a part that is a
    wrap-up's own words with soft words beside them, only closing words or a thanks in the others; never one with a
    request or more beside it, an idiom or a part that only holds its words ("That'll do. Read me the calendar.",
    "Yes, that'll do the trick.", "Make sure nothing else is on."; third fix review of 0.9.8,
    voice-calls-fixrev3-01: after her goodbye such a turn ended the call and the request was dropped)."""
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > 8 or "?" in text:
        return False
    return _wraps_up(text, name=name, language=language)


def is_goodbye(text: str, *, name: str) -> bool:
    """A plain goodbye ("okay bye bye", "talk to you later", "goodbye Estia"): she says bye and hangs up without
    asking Gemma. Anything subtler ("that's all for today, thanks") goes to Gemma, which can end the call too."""
    return bool(_goodbye(name).match(" ".join(real_words(text))))


# a sentence's or a comma's end (Whisper writes fast speech with commas only); Japanese breaks without a space
_BREAK = re.compile(r"(?<=[.!?,;:])\s+|(?<=[、。！？])")
_SENTENCE_END = (".", "!", "?", "。", "！", "？")
_ASKS_FIRST = re.compile(
    r"^(?:what|what's|when|where|who|why|how|which|can|could|would|will|do|does|did|is|are|should|shall|may)\b"
)
_LEADING_SOFT = frozenset({"and", "so", "oh", "also", "ok", "okay", "well", "then", "um"})  # "And what about…"


def _split_at(pattern: re.Pattern[str], text: str) -> list[tuple[int, str]]:
    """What `pattern.split(text)` gives, each piece with its place in `text`: where it starts."""
    pieces: list[tuple[int, str]] = []
    start = 0
    for gap in pattern.finditer(text):
        pieces.append((start, text[start : gap.start()]))
        start = gap.end()
    pieces.append((start, text[start:]))
    return pieces


@dataclass(frozen=True)
class _Part:
    """A part of the caller's turn with words: a sentence, or a part of one between commas, semicolons, colons or 、."""

    text: str
    sentence: int  # which sentence of the turn it is in
    quoted: bool  # it comes after a colon in its sentence: quoted speech ("tell Alex: take care")
    at: int = 0  # where it starts in the turn's text (a part joined to the one before it starts where that one does)


@functools.cache
def _going_on(language: str) -> frozenset[str]:
    """The first words of a comma part that goes on from the part before it (language.CONTINUING), as compared."""
    return frozenset(_form(word) for code in (language, ENGLISH) for word in CONTINUING.get(code, ()))


def _turn_parts(text: str, language: str) -> list[_Part]:
    """A turn's parts with words, in order: sentences (split at . ! ? 。！？), each split at commas, semicolons, colons
    and 、 (Whisper writes fast speech and a hesitation with commas). A part that starts with a word that goes on from
    the part before it in its sentence ("to", "back", "naar", "zum": language.CONTINUING, the call's language and
    English) is joined to that part: "I have to go, uh, to the dentist" is one part, an errand; "Bye, for now" one
    goodbye. The parts after a colon in a sentence are quoted speech. Each part knows where it starts in `text`."""
    going_on = _going_on(language)
    parts: list[_Part] = []
    sentence = 0
    quoted = False
    lead = len(text) - len(text.lstrip())  # the pieces are those of the stripped text
    for at, piece in _split_at(_BREAK, text.strip()):
        words = real_words(piece)
        if words:
            last = parts[-1] if parts and parts[-1].sentence == sentence else None
            if last is not None and not last.text.rstrip().endswith(":") and _form(words[0]) in going_on:
                parts[-1] = _Part(f"{last.text} {piece}", sentence, last.quoted, last.at)
            else:
                parts.append(_Part(piece, sentence, quoted, lead + at))
        quoted = quoted or piece.rstrip().endswith(":")
        if piece.rstrip().endswith(_SENTENCE_END):
            sentence += 1
            quoted = False
    return parts


@functools.cache
def _holding_on(language: str) -> tuple[frozenset[tuple[str, ...]], tuple[str, ...]]:
    """A call language's words that say more is coming, as compared: its and English's language.MORE_COMING ("wait",
    "one more thing", "noch eine Frage"), its "but" and its stop words that are no negation ("maar", "wacht"; "nee" is
    often only "no thanks"); and the Korean ones, matched inside words ("잠깐만요")."""
    words = [
        *(more for code in dict.fromkeys((language, ENGLISH)) for more in MORE_COMING.get(code, ())),
        *BUT.get(language, ()),
        *(word for word in stop_words(language) if word not in negations(language)),
    ]
    inside = tuple(" ".join(_units(word, language)) for word in words) if language == "ko" else ()
    return _phrases(words, language), inside


def _says_more(part: str, language: str) -> bool:
    """Whether a part of the turn holds a word that says more is coming (_holding_on)."""
    phrases, inside = _holding_on(language)
    units = _units(part, language)
    if any(units[at : at + len(more)] == more for more in phrases for at in range(len(units) - len(more) + 1)):
        return True
    joined = " ".join(units)
    return any(more in joined for more in inside)


def _closing_talk(parts: Sequence[str], language: str = ENGLISH) -> bool:
    """Talk after a goodbye that only closes the call ("I'm going to check the log now", "talk later", a request such as
    "remind me at five"): no question (a question mark, or on an English call a part that starts like one after soft
    words such as "and", "so" or "oh": Whisper may leave the mark out), and no word that says more is coming in the
    call's language or English (language.MORE_COMING: "wait", "actually", "one more thing", "before you go", "Moment",
    "nog één ding", "una cosa más"; a "but" or a stop word of the call's language: "maar", "wacht", でも)."""
    for part in parts:
        words = real_words(part)
        if _asked(part) or _says_more(part, language):
            return False
        start = next((index for index, word in enumerate(words) if word not in _LEADING_SOFT), len(words))
        if language == ENGLISH and _ASKS_FIRST.match(" ".join(words[start:])):
            return False
    return True


def says_goodbye(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """A turn that says goodbye and more: other words before the goodbye ("No, that's good. Okay, thank you.
    Bye-bye."), or only closing talk after it ("ok goodbye, I'm going to check the log now"; _closing_talk, in the
    call's `language`: "Oké, bye, maar wacht even" is more). Gemma answers the rest (a reminder asked for is set), then
    the call ends once her answer is spoken, even when it asks something, unless a device question waits (owner, 14
    Oct); Gemma rarely calls end_call on a real call, so code decides. A goodbye followed by more ("Bye. Wait, what's
    the weather?", "Bye, and what about tomorrow") is none; so is one in quoted speech ("tell Alex: take care, …") or
    one a comma part goes on from ("See you, at eight": _turn_parts), and one asked and then turned down ("Hang up? No,
    tell me more": a later part starts with a negation); a plain goodbye alone is is_goodbye's. Commas count as breaks
    too ("Yeah, bye.")."""
    parts = _turn_parts(text, language)
    words = _ending(language, name)

    def turned_down(index: int) -> bool:
        """An asked goodbye that a later part turns down ("Hang up? No, tell me more": wants_hang_up's rule)."""
        later = (_units(part.text, language) for part in parts[index + 1 :])
        return _asked(parts[index].text) and any(_starts_with(units, words.turned_down, words.soft) for units in later)

    return len(parts) > 1 and any(
        not part.quoted
        and is_goodbye(part.text, name=name)
        and _closing_talk([later.text for later in parts[index + 1 :]], language)
        and not turned_down(index)
        for index, part in enumerate(parts)
    )


@dataclass(frozen=True)
class _Leaving:
    """A call's leave-taking words as compared, or its words that ask her to end the call: its language's and
    English."""

    phrases: frozenset[tuple[str, ...]]  # language.LEAVING (or language.ENDING)
    soft: frozenset[tuple[str, ...]]  # language.LEAVING_SOFT (and ENDING_SOFT), fillers, her name
    not_alone: frozenset[tuple[str, ...]]  # language.LEAVING_NOT_ALONE: only with a soft word beside it
    answers: frozenset[tuple[str, ...]]  # acknowledgements and negations: a part of only these answers her
    may_ask: bool = False  # it counts asked too ("Shall we stop here?")
    # asked, a later part that starts with one of these turns it down ("Hang up? No, tell me more"): negations
    turned_down: frozenset[tuple[str, ...]] = frozenset()
    # the ones of `phrases` that count only with nothing before them in the turn but `opening` words
    # (language.LEAVING_UNCLEAR: "Sorry I didn't call earlier. Something came up." is an apology)
    unclear: frozenset[tuple[str, ...]] = frozenset()
    opening: frozenset[tuple[str, ...]] = frozenset()  # the soft words and thanks (language.THANKS)


@functools.cache
def _leaving(language: str, name: str) -> _Leaving:
    codes = dict.fromkeys((language, ENGLISH))
    words = [word for code in codes for word in (*acknowledgements(code), *negations(code))]
    soft = [*(word for code in codes for word in LEAVING_SOFT.get(code, ())), *FILLERS, name]
    unclear = _phrases([phrase for code in codes for phrase in LEAVING_UNCLEAR.get(code, ())], language)
    return _Leaving(
        _phrases([phrase for code in codes for phrase in LEAVING.get(code, ())], language) | unclear,
        _phrases(soft, language),
        _phrases([phrase for code in codes for phrase in LEAVING_NOT_ALONE.get(code, ())], language),
        _phrases(words, language),
        unclear=unclear,
        opening=_phrases([*soft, *(word for code in codes for word in THANKS.get(code, ()))], language),
    )


_TURNED_DOWN = ("nah",)  # a "no" that is no negation word elsewhere (language.negations has "no", "nope", "not")


@functools.cache
def _ending(language: str, name: str) -> _Leaving:
    """A call's requests to end it as compared (language.ENDING) and the soft words beside them, leave-taking's and
    ENDING_SOFT's; no part before one answers her ("Yes, let's end the call" ends it too), and one may be asked, unless
    a later part turns it down: one that starts with a negation, the call language's or English ("Hang up? No, tell
    me more", "On raccroche ? Non, continue.")."""
    codes = dict.fromkeys((language, ENGLISH))
    soft = [word for code in codes for word in (*LEAVING_SOFT.get(code, ()), *ENDING_SOFT.get(code, ()))]
    return _Leaving(
        _phrases([phrase for code in codes for phrase in ENDING.get(code, ())], language),
        _phrases([*soft, *FILLERS, name], language),
        frozenset(),
        frozenset(),
        may_ask=True,
        turned_down=_phrases([*(word for code in codes for word in negations(code)), *_TURNED_DOWN], language),
    )


def takes_leave(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """Leave-taking ("I have to go", "I'm hanging up", "ik moet gaan", "ich lege auf", "じゃあ、切るね"): a part of the
    turn (_turn_parts) that is one of the call language's or English phrases (language.LEAVING), with only soft words
    in it and in the parts beside it that it takes in (LEAVING_SOFT, a filler, her name: "Okay, so, I have to go now"),
    not asked ("So, I have to go?"), followed only by closing talk (_closing_talk). The call ends once her reply is
    spoken (owner, 14 Oct). None: "I have to go to the dentist", "I have to go, uh, to the dentist" (the part goes on),
    a part in quoted speech ("He said: I have to go"), one right after a part that answers her ("Yes, I have to go.",
    "No, I'm off.": only acknowledgement or negation words), "I'm off" with no soft word beside it (a day off too:
    LEAVING_NOT_ALONE), and in Japanese one with anything but soft words before it in its sentence ("電気、切るね": 切る
    is also "switch off")."""
    return _says(text, language, _leaving(language, name))


def wants_hang_up(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """The caller asks her to end the call ("shall we hang up?", "you can hang up now", "let's end the call", "we can
    stop here", "let's call it a day", "laten we ophangen", "lass uns Schluss machen"): a part of the turn that is one
    of the call language's or English requests (language.ENDING), with only soft words beside it (LEAVING_SOFT,
    ENDING_SOFT: "let's", "can we", "please"; a filler, her name), followed only by closing talk; asked too ("Shall we
    stop here?"), never in quoted speech, and in Japanese with only soft words before it in its sentence. She answers,
    then hangs up, even when her answer asks something (owner, 18 Oct: a request to end the call kept a call open). None:
    "Let's stop here, but what about the heater?", "Let's end the call after the weather", "Don't end the call yet",
    and "Stop here." or "Can you wrap it up?" (her reading, her answer: "stop here" and "wrap up" count only with
    "let's" or "we can")."""
    return _says(text, language, _ending(language, name))


def _says(text: str, language: str, words: _Leaving) -> bool:
    """Whether a part of the turn is one of `words`' phrases with only their soft words in it and in the parts beside
    it (takes_leave's and wants_hang_up's rules)."""
    parts = _turn_parts(text, language)
    units = [_units(part.text, language) for part in parts]
    clear = words.phrases - words.unclear

    def soft(index: int, sentence: int) -> bool:
        """Part `index` is one of `sentence` and has only soft words: the leave-taking beside it takes it in."""
        in_sentence = 0 <= index < len(parts) and parts[index].sentence == sentence
        return in_sentence and made_of(units[index], words.soft, words.soft)

    for index, part in enumerate(parts):
        first = last = index
        while soft(first - 1, part.sentence):
            first -= 1
        while soft(last + 1, part.sentence):
            last += 1
        said = tuple(unit for span in units[first : last + 1] for unit in span)
        before = first - 1 if first > 0 and parts[first - 1].sentence == part.sentence else None
        asked = any(_asked(parts[at].text) for at in range(first, last + 1))
        if (
            not part.quoted  # quoted speech (the parts before it are not: a colon quotes the rest of its sentence)
            and (words.may_ask or not asked)  # "So, I have to go?" asks; "Shall we stop here?" asks to stop
            and made_of(said, words.phrases, words.soft)
            and (
                made_of(said, clear, words.soft)
                or all(made_of(units[at], words.opening, words.opening) for at in range(first))
            )
            and said not in words.not_alone
            and (before is None or not made_of(units[before], words.answers, words.soft))
            and (language != "ja" or before is None)  # Japanese: only soft words before it in its sentence
            and _closing_talk([later.text for later in parts[last + 1 :]], language)
            and not (
                asked
                and any(_starts_with(units[at], words.turned_down, words.soft) for at in range(last + 1, len(parts)))
            )
        ):
            return True
    return False


def _starts_with(
    units: tuple[str, ...], phrases: Collection[tuple[str, ...]], soft: Collection[tuple[str, ...]]
) -> bool:
    """Whether `units` start with one of `phrases`, maybe after `soft` ones ("Please no": a no)."""
    reached = {0}
    for start in range(len(units)):
        if start not in reached:
            continue
        if any(units[start : start + len(option)] == option for option in phrases):
            return True
        reached.update(start + len(option) for option in soft if units[start : start + len(option)] == option)
    return False


@functools.cache
def _her_goodbyes(language: str, name: str) -> tuple[frozenset[tuple[str, ...]], frozenset[tuple[str, ...]]]:
    """Her plain goodbyes as compared (language.GOODBYES), and the soft words beside them, the call language's and
    English's."""
    codes = dict.fromkeys((language, ENGLISH))
    return (
        _phrases([bye for code in codes for bye in GOODBYES.get(code, ())], language),
        _phrases([*(word for code in codes for word in LEAVING_SOFT.get(code, ())), *FILLERS, name], language),
    )


@functools.cache
def _closing(language: str, name: str) -> tuple[frozenset[tuple[str, ...]], frozenset[tuple[str, ...]]]:
    """A caller's closing words as compared: acknowledgements, thanks (language.THANKS) and plain goodbyes
    (language.GOODBYES), the call language's and English; and the soft words beside them."""
    codes = dict.fromkeys((language, ENGLISH))
    words = [
        word for code in codes for word in (*acknowledgements(code), *THANKS.get(code, ()), *GOODBYES.get(code, ()))
    ]
    return _phrases(words, language), _leaving(language, name).soft


@functools.cache
def _parting(language: str, name: str) -> frozenset[tuple[str, ...]]:
    """A caller's closing words after her goodbye (`_closing`'s) and the goodbyes that are also "hello" elsewhere
    (language.PARTING: "ciao", "пока"), the call language's and English, as compared."""
    closing, _ = _closing(language, name)
    codes = dict.fromkeys((language, ENGLISH))
    return closing | _phrases([word for code in codes for word in PARTING.get(code, ())], language)


def closes_only(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """The caller's turn right after her goodbye is only closing words: thanks, acknowledgements and goodbyes, in the
    call's language or English, with soft words ("Thank you.", "Doei!", "Ciao!", "Пока!", "またね", "Okay, bye."; review of
    0.9.7: only English goodbyes ended the call); never asked ("Okay?") and never with more ("Thanks, and tomorrow?")."""
    if _asked(text):
        return False
    parts = [_units(part.text, language) for part in _turn_parts(text, language)]
    soft = _closing(language, name)[1]
    return bool(parts) and all(made_of(units, _parting(language, name), soft) for units in parts)


def _holds(units: tuple[str, ...], phrases: Collection[tuple[str, ...]]) -> bool:
    """Whether `units` hold one of `phrases` anywhere."""
    return any(units[at : at + len(one)] == one for one in phrases for at in range(len(units) - len(one) + 1))


@dataclass(frozen=True)
class _Byes:
    """A caller's goodbyes on a call as compared, the call language's and English (`closes_call`)."""

    goodbyes: frozenset[tuple[str, ...]]  # language.GOODBYES, but its GOODBYES_NOT_ALONE
    until: frozenset[tuple[str, ...]]  # language.GOODBYES_UNTIL: also "until <a time>"
    for_now: frozenset[tuple[str, ...]]  # language.GOODBYES_NOT_ALONE: also "for now", "so far" or "later"
    parting: frozenset[tuple[str, ...]]  # language.PARTING, but its PARTING_NOT_ALONE
    not_alone: frozenset[tuple[str, ...]]  # language.PARTING_NOT_ALONE: also "hello" or "well then"
    thanks: frozenset[tuple[str, ...]]  # language.THANKS
    words: frozenset[tuple[str, ...]]  # closing words (`_parting`'s) and the soft words beside them
    soft: frozenset[tuple[str, ...]]  # the soft words alone (`_closing`'s)
    # beside a goodbye that also means "so far" ("пока", "na razie"): thanks, goodbyes, fillers, her name and the call
    # language's soft words ("Ну пока!", "No pa!"; dialogue-F5), never an acknowledgement or a soft word of
    # language.SO_FAR ("Пока всё хорошо.", "Na razie dobrze.": so far, so good)
    beside: frozenset[tuple[str, ...]]


@functools.cache
def _byes(language: str, name: str) -> _Byes:
    codes = dict.fromkeys((language, ENGLISH))

    def of(table: Mapping[str, tuple[str, ...]]) -> frozenset[tuple[str, ...]]:
        return _phrases([word for code in codes for word in table.get(code, ())], language)

    soft = _closing(language, name)[1]
    not_alone = of(PARTING_NOT_ALONE)
    for_now = of(GOODBYES_NOT_ALONE)
    so_far = (*acknowledgements(language), *SO_FAR.get(language, ()))
    beside_soft = [word for word in LEAVING_SOFT.get(language, ()) if word not in so_far]
    return _Byes(
        of(GOODBYES) - for_now,
        of(GOODBYES_UNTIL),
        for_now,
        of(PARTING) - not_alone,
        not_alone,
        of(THANKS),
        _parting(language, name) | soft,
        soft,
        of(THANKS) | of(GOODBYES) | of(PARTING) | _phrases([*FILLERS, name, *beside_soft], language),
    )


def _says_bye(text: str, *, name: str, language: str) -> bool:
    """A goodbye of the call's language or English in the caller's turn (language.GOODBYES: "Danke, tschüss!",
    "Tschüss. Ich gehe jetzt einkaufen.", "じゃあね"): not asked, not in quoted speech, only closing talk after it
    (_closing_talk); one that is also "until <a time>" (GOODBYES_UNTIL) only with nothing but closing words before it in
    its sentence ("Okay, danke. Bis morgen!", never "Es bleibt bewölkt, bis morgen."). Or a turn that ends on a goodbye
    that is also "hello" elsewhere (language.PARTING: "Пока!", "Dzięki, pa!", "Ну пока!"), with only thanks, goodbyes,
    fillers, her name or the call language's soft words beside it in its part (`_Byes.beside`; dialogue-F5: "пока" is
    also "so far", "Пока всё хорошо.", so never language.SO_FAR's words or an acknowledgement); one that alone is also a
    greeting or a turn's opening (PARTING_NOT_ALONE: "Ciao!", "Dag!", "じゃあ") only after a thanks ("Grazie, ciao!",
    "Perfetto, grazie. Ciao!", never "Ciao, bene grazie!"); and one that is also "for now", "so far" or "later"
    (GOODBYES_NOT_ALONE: "na razie", "またあとで") only there, after a thanks or another goodbye ("Dzięki, na razie!",
    never "Na razie dobrze." or "うん、またあとで。天気を教えて"; second fix review of 0.9.8,
    redteam-calls-so-far-reads-as-goodbye), a PARTING_NOT_ALONE one among them, alone with soft words in its part or
    at the start of the goodbye's ("じゃあ、またあとで", "じゃあまたあとで", "Cześć, na razie!"; dialogue-F4), or with only a
    thanks after it ("Na razie, dzięki!", "またあとで、ありがとう"; never "Na razie nie, dzięki.")."""
    parts = _turn_parts(text, language)
    if not parts:
        return False
    units = [_units(part.text, language) for part in parts]
    byes = _byes(language, name)

    def goodbye(index: int) -> bool:
        part = parts[index]
        if part.quoted or _asked(part.text) or not made_of(units[index], byes.goodbyes, byes.words):
            return False
        if not _closing_talk([later.text for later in parts[index + 1 :]], language):
            return False
        if made_of(units[index], byes.goodbyes - byes.until, byes.words):
            return True
        same = [units[at] for at in range(index) if parts[at].sentence == part.sentence]
        return all(made_of(earlier, byes.words, byes.words) for earlier in same)

    ends = not parts[-1].quoted and not _asked(parts[-1].text)
    thanked = any(_holds(earlier, byes.thanks) for earlier in units[:-1]) or _starts_with(
        units[-1], byes.thanks, byes.soft
    )
    # another goodbye before the "for now" one: a goodbye, or a part that is only "well then" ("じゃあ", "cześć") and
    # soft words, or one that opens its part ("じゃあまたあとで"); never a "じゃ" held anywhere, as in "それじゃない"
    bid = any(
        _holds(earlier, byes.goodbyes | byes.parting) or made_of(earlier, byes.not_alone, byes.soft)
        for earlier in units[:-1]
    ) or _starts_with(units[-1], byes.not_alone, byes.soft)
    # a "for now" goodbye with only a thanks after it: the thanks is the one it needs ("Na razie, dzięki!")
    thanked_after = any(
        not parts[at].quoted
        and not _asked(parts[at].text)
        and made_of(units[at], byes.for_now, byes.beside)
        and all(made_of(later, byes.thanks, byes.words) for later in units[at + 1 :])
        for at in range(len(units) - 1)
    )
    return (
        any(goodbye(index) for index in range(len(parts)))
        or (ends and made_of(units[-1], byes.parting, byes.beside))
        or (ends and thanked and made_of(units[-1], byes.not_alone, byes.words))
        or (ends and (thanked or bid) and made_of(units[-1], byes.for_now, byes.beside))
        or (ends and thanked_after)
    )


# beside a wrap-up's words, besides closing and soft words: "No, that's it", "I think that's all for today"
_WRAP_UP_SOFT = ("no", "nope", "no thanks", "i think", "i guess", "i suppose", "really")
# the most words a thanks may name after "for" and still close the call ("thanks for the help", "thanks for all that"),
# and the words that say a request follows ("thanks and now my calendar", "thanks for that now read me the rest")
_THANKS_FOR_WORDS = 3
_GOES_ON = frozenset({"and", "now", "but", "then", "so", "also", "next", "please"})


def _only(units: tuple[str, ...], soft: Collection[tuple[str, ...]]) -> bool:
    """Whether `units` are none, or only `soft` ones."""
    return not units or made_of(units, soft, soft)


# "all" before these is a "no problem", never a wrap-up ("That's all right.", "That's all good, thanks."; third fix
# review of 0.9.8, voice-calls-fixrev3-02: "right" and "good" are soft words, so they read as beside "that's all")
_ALL_AS_FINE = frozenset({("all", "right"), ("all", "good"), ("all", "fine")})


def _wrap_up_alone(units: tuple[str, ...], soft: Collection[tuple[str, ...]]) -> bool:
    """Whether a part's words are a wrap-up's own (_WRAP_UP_ALONE: "that's it for the page", "that's all I needed")
    with only `soft` ones before and after it ("No, that will do for now", "I think that's it for today, thanks");
    never a "that's all" that goes on as "all right", "all good" or "all fine" (_ALL_AS_FINE)."""
    for start in range(len(units)):
        if not _only(units[:start], soft):
            continue
        for end in range(len(units), start, -1):
            if units[end - 1 : end + 1] in _ALL_AS_FINE:
                continue
            if _WRAP_UP_ALONE.fullmatch(" ".join(units[start:end])) and _only(units[end:], soft):
                return True
    return False


def _thanks_alone(units: tuple[str, ...], byes: _Byes) -> bool:
    """Whether a part is a thanks that closes: with only closing words ("Thanks a lot", "okay, thank you, bye"), or
    with what it is for, up to _THANKS_FOR_WORDS words, and closing words after them ("thanks for the help, see you");
    never with a request ("Thank you and now my calendar please", "thanks for that now read me the rest")."""
    if made_of(units, byes.thanks, byes.words):
        return True
    reached = {0}
    for start in range(len(units)):
        if start not in reached:
            continue
        for thanks in byes.thanks:
            after = start + len(thanks)
            if units[start:after] != thanks or units[after : after + 1] != ("for",):
                continue
            for count in range(1, _THANKS_FOR_WORDS + 1):
                named = units[after + 1 : after + 1 + count]
                if len(named) == count and not _GOES_ON & set(named) and _only(units[after + 1 + count :], byes.words):
                    return True
        reached.update(start + len(soft) for soft in byes.soft if units[start : start + len(soft)] == soft)
    return False


def _wraps_up(text: str, *, name: str, language: str) -> bool:
    """A wrap-up in the caller's turn (English: "that's all", "nothing else"): a part that is a wrap-up's own words with
    soft words beside them (_wrap_up_alone: "No, that will do for now", "that's all I needed"), and in the other parts
    only closing words or a thanks (_thanks_alone: "okay, that's everything for today, thanks for the help"); never
    asked, never a part that only holds a wrap-up's words ("Make sure nothing else is on"), and never a request beside
    it ("That's all for the mail. Thank you and now my calendar please.": Whisper often writes no comma after a thanks;
    second fix review of 0.9.8, guard-01 and voice-02: such turns ended the call after outside text)."""
    if _asked(text):
        return False
    parts = _turn_parts(text, language)
    byes = _byes(language, name)
    english = _byes(ENGLISH, name).words | _phrases(_WRAP_UP_SOFT, ENGLISH)
    wraps = [_wrap_up_alone(_units(part.text, ENGLISH), english) for part in parts]
    words = byes.words | _phrases(_WRAP_UP_SOFT, language)

    def closing(part: _Part) -> bool:
        units = _units(part.text, language)
        return made_of(units, words, words) or (_thanks_alone(units, byes) and not _says_more(part.text, language))

    return any(wraps) and all(wrap or closing(part) for wrap, part in zip(wraps, parts, strict=True))


def closes_call(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """The caller's own words close the call: leave-taking (`takes_leave`), a request to end it (`wants_hang_up`), a
    goodbye alone or with closing talk after it (`is_goodbye`, `says_goodbye`, and in the call's language `_says_bye`:
    "Danke, tschüss!", "Пока!", "Grazie, ciao!"), or a wrap-up (`_wraps_up`, English: "Thanks, that's all."). An
    acknowledgement or a thanks alone is none ("Okay.", "Grazie."), and so is a goodbye that is also "hello" with no
    thanks before it ("Ciao!"). After outside text her end_call needs one (tools.Tools.may_end_call; release review of
    0.9.8, G02; its fix review, voice-01: on a call after a mail read, a goodbye in the other ten call languages or a
    wrap-up left her end_call refused)."""
    return (
        takes_leave(text, name=name, language=language)
        or wants_hang_up(text, name=name, language=language)
        or is_goodbye(text, name=name)
        or says_goodbye(text, name=name, language=language)
        or _wraps_up(text, name=name, language=language)
        or _says_bye(text, name=name, language=language)
    )


def answers_question(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """The caller's reply only answers her yes-or-no question: a yes (language.is_yes: "yes please", "ja graag") or
    only acknowledgements, negations and thanks, with soft words ("No, thanks.", "Okay.", "Nee hoor.")."""
    if is_yes(text, language):
        return True
    parts = [_units(part.text, language) for part in _turn_parts(text, language)]
    closing, soft = _closing(language, name)
    words = closing | _leaving(language, name).answers
    return bool(parts) and all(made_of(units, words, soft) for units in parts)


def ends_with_goodbye(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """Her answer ends with a plain goodbye and asks nothing ("Good luck with it! Bye!", "Enjoy, talk soon!", "Prima,
    tot later!"): its last part (_turn_parts) is one of the call language's or English goodbyes (language.GOODBYES)
    with only soft words in it, or a name after such a part in its sentence: one or two capitalised words ("Bye,
    Alex!", "See you soon, Alex Example!"), one word in a script without capitals; never in quoted speech ("Tell Alex:
    bye."), never in an answer that asks (a question mark, or Japanese's か: "他に何かありますか。またね。"). Only plain
    goodbyes: "Have a great day!" is none, and so is a word that is also "hello" ("Ciao!", "Dag!", "Salut !", "Boa
    noite"); one that is also "until <a time>" (language.GOODBYES_UNTIL) only with nothing but soft words,
    acknowledgements or thanks before it in its sentence ("Es bleibt bewölkt, bis morgen." is the weather; review 18
    Oct: "Bye, though." and "Take care, okay." are none either)."""
    if _asked(text) or asks(re.split(r"(?<=[。！？.!?])\s*", text)):
        return False
    parts = _turn_parts(text, language)
    phrases, soft = _her_goodbyes(language, name)
    until = _phrases(
        [bye for code in dict.fromkeys((language, ENGLISH)) for bye in GOODBYES_UNTIL.get(code, ())], language
    )
    closing, _ = _closing(language, name)

    def bye(index: int) -> bool:
        part = parts[index]
        units = _units(part.text, language)
        if part.quoted or not made_of(units, phrases, soft):
            return False
        if made_of(units, phrases - until, soft):
            return True
        same = [_units(earlier.text, language) for earlier in parts[:index] if earlier.sentence == part.sentence]
        return all(made_of(earlier, closing | soft, closing | soft) for earlier in same)

    if not parts:
        return False
    *before, last = parts
    return (
        bye(len(parts) - 1)
        or (bool(before) and before[-1].sentence == last.sentence and _a_name(last.text, soft) and bye(len(parts) - 2))
        or _farewell(text, language)
    )


_FAREWELL_SENTENCES = re.compile(r"(?<=[.!?…。！？])\s*")
_FAREWELL_PARTS = re.compile(r"\s*(?:[,;:—–]|\s-\s)\s*")


@functools.cache
def _farewell_patterns(language: str) -> tuple[tuple[re.Pattern[str], int], ...]:
    """The call language's and English's closing-wish patterns (language.FAREWELLS), compiled."""
    return tuple(
        (re.compile(pattern), most)
        for code in dict.fromkeys((language, ENGLISH))
        for pattern, most in FAREWELLS.get(code, ())
    )


@functools.cache
def _joins(language: str) -> frozenset[str]:
    """The call language's "and" and English's (language.AND), as compared (`_form`)."""
    return frozenset(_form(AND[code]) for code in dict.fromkeys((language, ENGLISH)) if code in AND)


def _farewell_part(part: str, language: str) -> int | None:
    """The word limit of the closing-wish pattern a part of her sentence starts like and fits within (language.FAREWELLS:
    "have a good night" 8, "I'll be here whenever you're ready" 20), else None: the part is case folded and a leading
    "and" and Spanish's opening "¡" or "¿" are left out ("¡Y que descanses!")."""
    joins = {AND[code] for code in dict.fromkeys((language, ENGLISH)) if code in AND}
    words = [word for word in (word.lstrip("¡¿") for word in part.casefold().replace("’", "'").split()) if word]
    if words and words[0] in joins:
        words = words[1:]
    said = " ".join(words)
    return next(
        (most for match, most in _farewell_patterns(language) if said and match.match(said) and len(words) <= most),
        None,
    )


def _farewell(text: str, language: str) -> bool:
    """Her answer's last sentence is a closing wish or an offer to wait ("Enjoy your evening!", "Sleep well, and I'll
    catch you in the morning!", "I'll be here whenever you're ready."): every part of it starts like one of the call
    language's or English's (language.FAREWELLS), a leading "and" and Spanish's opening "¡" or "¿" left out ("¡Y que
    descanses!"), within that pattern's words (Japanese, without spaces, counts one)."""
    sentences = [sentence for sentence in _FAREWELL_SENTENCES.split(text.strip()) if real_words(sentence)]
    if not sentences:
        return False
    parts = [part for part in _FAREWELL_PARTS.split(sentences[-1].rstrip(".!?…。！？ ").casefold()) if part.strip()]
    return bool(parts) and all(_farewell_part(part, language) is not None for part in parts)


def _a_name(text: str, soft: Collection[tuple[str, ...]]) -> bool:
    """Whether a part is a name: one or two capitalised words that are no soft word ("Alex", "Alex Example"; never
    "though" or "Okay"), or one word in a script without capitals."""
    words = _WORD.findall(_SOUND_LABEL.sub(" ", text))
    if not words or len(words) > 2 or any((_form(word),) in soft for word in words):
        return False
    return all(word[0].isupper() for word in words) or (len(words) == 1 and not _cased_word(words[0]))


def _cased_word(word: str) -> bool:
    return word.lower() != word.upper()


# An early end's words that end like more is coming (owner, 17 Oct): a comma, an ellipsis or a dash at the end (Whisper's
# marks of a sentence left open; 、 is Japanese's comma), or a last word that goes on (language.CONTINUING: "to",
# "naar"), a "but" (language.BUT), a word a turn never ends on (language.LEFT_OPEN: "and", "the", "because") or a
# filler ("um", "äh"), in the call's language or English
_OPEN_ENDS = (",", "、", "…", "...", "-", "–", "—")
AT_ONCE = "at once"  # _call_over: the call ends once her answer is spoken
AFTER_QUIET = "after quiet"  # _call_over: her plain goodbye ends it after goodbye_grace_s of quiet (_after_goodbye)
HOLD_MAX_S = 120.0  # a turn joined in holds is at most this long (one listen's cap; Whisper takes up to 3 minutes)


@functools.cache
def _open_words(language: str) -> frozenset[tuple[str, ...]]:
    codes = dict.fromkeys((language, ENGLISH))
    words = [
        word for code in codes for word in (*CONTINUING.get(code, ()), *BUT.get(code, ()), *LEFT_OPEN.get(code, ()))
    ]
    return _phrases([*words, *FILLERS], language)


def goes_on(text: str, language: str = ENGLISH) -> bool:
    """Whether the caller's words end like more is coming (see _OPEN_ENDS): "I want to call," "I want to go to", "It is
    nice but", "Ik ga naar". A sentence that ends with a word such as "home" or "on" counts too: holding it only waits
    out today's full pause."""
    end = text.rstrip().rstrip("\"')]”’»」』").rstrip()
    if end.endswith(_OPEN_ENDS):
        return True
    units = _units(end, language)
    return any(len(units) >= len(words) and units[-len(words) :] == words for words in _open_words(language))


LONG_ANSWER_WORDS = 60  # the end line counts answers of this many words or more apart (a deep question's, mostly)


def percentile(values: Sequence[int], share: float) -> int:
    """The nearest-rank percentile of `values` (not empty): the smallest value with at least `share` of them at or
    below it."""
    ranked = sorted(values)
    return ranked[max(0, math.ceil(share * len(ranked)) - 1)]


def _waits(values: Sequence[int]) -> str:
    if not values:
        return "none"
    answers = f"{len(values)} answer{'' if len(values) == 1 else 's'}"
    return (
        f"{answers}, median {round(statistics.median(values))} ms, 90th percentile {percentile(values, 0.9)} ms,"
        f" {sum(ms <= 3_000 for ms in values)} within 3 s, {sum(ms <= 10_000 for ms in values)} within 10 s"
    )


def waits_line(waits: Sequence[tuple[int, int]], stopped: int = 0) -> str:
    """The call's end line for her wait (metadata; review of 26 Oct, rtcall-2): the time from the end of each of the
    caller's turns to her answer's first words, all of them, then long answers (LONG_ANSWER_WORDS or more) and short
    ones apart; then, when there were any, the turns whose voice stopped before her answer's first words (`stopped`:
    Pocket TTS could not say her answer's first part; the review of 28 Oct, pipeline-06), so a turn the caller heard
    nothing of is counted."""
    head = "calls: her first words after the end of a turn: "
    tail = f"; her voice stopped before her answer's first words: {stopped} turn{'' if stopped == 1 else 's'}"
    tail = tail if stopped else ""
    if not waits:
        return f"{head}none{tail}"
    long = [ms for ms, words in waits if words >= LONG_ANSWER_WORDS]
    short = [ms for ms, words in waits if words < LONG_ANSWER_WORDS]
    return (
        f"{head}{_waits([ms for ms, _ in waits])}; long answers ({LONG_ANSWER_WORDS} words or more): {_waits(long)};"
        f" short ones: {_waits(short)}{tail}"
    )


_PIECE_BREAKS = (",", "、", "，", ";")  # where a part too long for one speak request is split (a comma first)


def _pieces(part: str) -> list[str]:
    """A part in pieces the voice service takes in one request (SPEECH_MAX_CHARS each; review of 26 Oct, rtcall-6: a
    longer one is a 400 that ends the call): split at its last comma before the limit, else its last space, else at
    the limit itself."""
    pieces: list[str] = []
    while len(part) > SPEECH_MAX_CHARS:
        head = part[:SPEECH_MAX_CHARS]
        cut = max(head.rfind(mark) for mark in _PIECE_BREAKS) + 1
        if cut <= 0:
            cut = head.rfind(" ")
        if cut <= 0:
            cut = SPEECH_MAX_CHARS
        pieces.append(part[:cut].rstrip())
        part = part[cut:].lstrip()
    return [*pieces, part] if part else pieces


def _parts(text: str, max_chars: int = CALL_SPOKEN_CHARS) -> list[str]:
    """Text to speak on a call, sentence by sentence (even a one-word answer), at most `max_chars` of it (ending at a
    sentence end), each part short enough for one speak request (`_pieces`); never a stage direction ("(laughs)")."""
    plain = without_stage_directions(text).strip()
    parts = sentences(spoken_text(text, max_chars, min_chars=1, rest="")) or ([plain] if plain else [])
    return [piece for part in parts for piece in _pieces(part)]


def heard(text: str) -> str:
    """What the caller hears of `text` when it is said to its end: the call's parts (`_parts`), as one string. Her
    stored reply, the end of the call and her offer of the rundown read this, never the words left unsaid."""
    return " ".join(_parts(text))


SUMMARY_CHARS = 12_000  # what a call summary reads of the call's lines, at most
SUMMARY_HEAD_CHARS = 4_000  # of that, the call's start; the rest its end
SUMMARY_GAP = "(… the middle of the call is left out …)"


def summary_transcript(lines: list[str]) -> str:
    """The call's lines as its summary reads them: whole when they fit in SUMMARY_CHARS, else the lines of its start
    (SUMMARY_HEAD_CHARS) and of its end, with SUMMARY_GAP between them (review of 26 Oct, rtcall-7: only the last
    12,000 characters were read, so a call with seven or more full answers lost its first topics). A line too long for
    its share is cut to it."""
    whole = "\n".join(lines)
    if len(whole) <= SUMMARY_CHARS:
        return whole
    head: list[str] = []
    room = SUMMARY_HEAD_CHARS
    for line in lines:
        if room <= 0:
            break
        head.append(line[:room])
        room -= len(line) + 1
    tail: list[str] = []
    room = SUMMARY_CHARS - SUMMARY_HEAD_CHARS - len(SUMMARY_GAP) - 2
    for line in reversed(lines[len(head) :]):
        if room <= 0:
            break
        tail.insert(0, line[-room:])
        room -= len(line) + 1
    return "\n".join([*head, SUMMARY_GAP, *tail])


def call_span(start: float, end: float, tz: ZoneInfo | None) -> str:
    """A call's start (its first ring: `_Call.started_wall`) and end on the user's clock (`tz`, the config's time zone),
    for the notes after it: "14:02-14:10", one time when both fall in the same minute ("14:02")."""
    first, last = local_hhmm(start, tz), local_hhmm(end, tz)
    return first if first == last else f"{first}-{last}"


def said_whole(text: str) -> bool:
    """Whether a call says all of `text`: false when CALL_SPOKEN_CHARS cuts it (its speech, uncut, is longer)."""
    return len(spoken_text(text, sys.maxsize, min_chars=1, rest="")) <= CALL_SPOKEN_CHARS


def ended_outcome(reason: str) -> str:
    """A call she placed that ended before it was picked up, by signal-cli's reason (its words to be confirmed live):
    no_answer (ring timeout), declined (hung up on the phone), busy, answered (picked up on another of the user's
    linked devices), else call_failed (the call helper, signal-cli or the network)."""
    key = re.sub(r"[^a-z]", "", reason.lower())
    if "busy" in key:
        return "busy"
    if "timeout" in key:
        return "no_answer"
    if "accepted" in key:
        return "answered"
    if "declined" in key or key == "remotehangup":
        return "declined"
    return "call_failed"


def socket_path(accepted: dict[str, Any], connected: dict[str, Any], folder: Path, call_id: int) -> Path:
    """The call helper's audio socket: the "unix:<path>" it reported, else its documented name (the call id as an
    unsigned number). It must lie in our private folder (ValueError otherwise)."""
    for source in (accepted, connected):
        for key in ("inputDeviceName", "outputDeviceName"):
            value = source.get(key)
            if isinstance(value, str) and value.startswith("unix:"):
                path = Path(value.removeprefix("unix:"))
                if not path.is_absolute() or path.parent.resolve() != folder.resolve():
                    raise ValueError("the call's audio socket is outside the private folder")
                return path
    return folder / f"signal-call-{call_id & 0xFFFFFFFFFFFFFFFF}.sock"


class _LineClosed(ConnectionError):
    """The call's line closed while she spoke (the call ended): `played` of the clip went out, its lead left out."""

    def __init__(self, played: float) -> None:
        super().__init__("the call's line closed while she spoke")
        self.played = played


class _Ended(Exception):
    """The call ended (hung up, timed out or signal-cli restarted)."""


@dataclass(frozen=True)
class _Spoke:
    """How her answer was spoken (CallManager._speak)."""

    interruption: str | None  # what the caller said over it, or None: said whole
    heard: str  # what the caller heard of it, code's lines too (the call's transcript for its summary)
    kept: str  # what her stored reply keeps of that: her words and Turn.kept_code, never code's other lines
    taken_at: float | None  # how far into her answer (after the lead) the caller took over
    stopped: bool = False  # her voice stopped for the rest of the turn (Pocket TTS could not say a part): texted
    texted: bool = False  # with `stopped`: something new went as a text (not only lines that go as texts of their own)


class _Skip:
    """In a turn that waits for a device's yes: Pocket TTS could not say her words before code's question, so they go
    as a text from there and the question, last, is tried (`CallManager._speak`)."""


_SKIPPED = _Skip()


@dataclass
class _Dial:
    """A call she is placing: its events (the ringing one comes before signal-cli tells the call id) until it is
    picked up or ends."""

    aci: str
    call_id: int | None = None
    events: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)


@dataclass(frozen=True)
class _Opening:
    """A placed call that was picked up: startCall's result, the CONNECTED event and her first line."""

    started: dict[str, Any]
    connected: dict[str, Any]
    line: str
    wake: bool


@dataclass
class _Call:
    aci: str
    call_id: int
    started: float
    started_wall: float
    language: str  # a whole call is in one language (/language waits until it ends)
    spoken: prompts.CallLines  # her code-written lines in that language
    practice: bool = False  # a call during language practice: in the practised language, its end ends practice
    # answered while her voice is not working (owner, 4 Nov 2026): no speech request at all, her answers go as texts,
    # until the call ends, also when her voice is back
    text_only: bool = False
    events: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    lines: list[str] = field(default_factory=list)
    user_turns: int = 0
    dropped: bool = False
    unfinished: bool = False  # the call ended while an answer was still being worked on
    # a call from one of the user's devices that she answered (connected; never one she placed): its end gets a note
    # with its start and end, as a call names no device (laptop path, Phase 0, owner 3 Nov)
    answered: bool = False
    # answers in a row stopped by the turn's time limit: the second says CallLines.too_long, never the lost line again,
    # and one left at the call's end gets its own note (the second deep scan of 0.9.8, dialogue-call-lost-loop)
    timeouts: int = 0
    acknowledged_last: bool = False  # the last request got a "got it": the next one gets none
    # the call's end-of-turn rules, given to every listen of hers (never the one after a barge-in); None: both off
    turn_end: TurnEnd | None = None
    warm_up: asyncio.Task[None] | None = None  # the model's warm-up for this call (one at most)
    # the caller asked her to end the call (or took leave) while her answer asked a device question: the call ends
    # once her answer to the caller's reply is spoken, when that reply only answers the question ("yes", "no thanks")
    ends_after_question: bool = False
    # a message waits for this call's end (CallManager.text_waits; owner, 23 Oct): the accounts code's line went to in
    # their chat (once a call each), and whether the caller's still waits to be said on the call (once a call)
    texted: set[str] = field(default_factory=set)
    text_owed: bool = False
    # a text of the caller's that this call stopped (a Deep Thinking answer, `text_waits(stopped=True)`) is out of her
    # window for the whole call: her line about it comes right after her greeting (`_told_first`; review of 5 Nov)
    text_stopped: bool = False
    text_clip: asyncio.Task[bytes] | None = None  # the speech of her line about it, made while she listens
    # her latest words (a sentence and the one before it) ask something: the caller's reply may come after any pause,
    # so her line about a text waits (review, 23 Oct: a "yes" 1.2 s after her question was lost under it)
    asked: bool = False
    # her wait after the caller's turns (review of 26 Oct, rtcall-2; metadata): when the latest turn ended (its listen
    # returned; None once her answer started), her first sound since (a "Got it." or a thinking line), and per answer
    # the wait until her answer's first words in ms with its words (the end line, `waits_line`)
    turn_ended_at: float | None = None
    first_sound_at: float | None = None
    waits: list[tuple[int, int]] = field(default_factory=list)
    stopped_before_words: int = 0  # turns whose voice stopped before her answer's first words (the end line)


class CallManager:
    def __init__(
        self,
        signal: CallSignal,
        voice: Voice,
        host: Host,
        allowed: frozenset[str],
        settings: CallSettings,
        *,
        tz: ZoneInfo | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        language: str = ENGLISH,
        name: str = DEFAULT_NAME,
        practice: Callable[[str], str | None] = lambda aci: None,
        locked: Callable[[], bool] = lambda: False,
        missed: Callable[[str, bool], None] = lambda aci, promised: None,
        reached: Callable[[str], None] = lambda aci: None,
        downloads: Downloads | None = None,
    ) -> None:
        self._signal = signal
        self._downloads = downloads  # a call in a language whose model downloads rings out, with code's text
        self._locked = locked  # /lock: no call is answered or placed until /unlock
        # a call that rang while the voice was not ready, and whether its "not ready" note (a promise) went out: the
        # voice-ready note follows
        self._missed = missed
        self._reached = reached  # a call connected (answered, or placed and picked up): that caller heard her
        self._voice = voice
        self._host = host
        self._allowed = frozenset(aci.lower() for aci in allowed)
        self.settings = settings
        self._tz = tz
        self._clock = clock
        self._wall = wall
        self._rng = rng or random.Random()
        self.language = language  # the next call's language (/language sets it)
        self.name = name  # hers: greetings, "stop <name>", the summary's transcript
        self._practice = practice  # the language this user is practising (/babble), read once when a call starts
        self._last_line = ""
        self._last_greeting = ""  # kept apart: one greeting per call, and the next call's must differ
        self._recent_acknowledgements: list[str] = []  # the last two "got it"s, never said again next
        self._call: _Call | None = None
        self._after_done = asyncio.Event()  # set unless the notes after a call are still to go out (`after_notes`)
        self._after_done.set()
        self._dial: _Dial | None = None  # a call she is placing, until it is picked up or ends
        self._task: asyncio.Task[None] | None = None
        # warm-ups still running, held here so that one outlives its call (asyncio keeps only weak references)
        self._warm_ups: set[asyncio.Task[None]] = set()
        self._audio: CallAudio | None = None
        # canned lines at 48 kHz by (voice, language, text) (no user data), never one another voice said in hers' place
        self._clips: dict[tuple[str, str, str], bytes] = {}
        self._last: tuple[float, int] | None = None  # the last call: wall start, minutes
        self._not_ready_at: float | None = None
        self._no_voice_at: dict[str, float] = {}  # by account: the last no-voice text (at most one in 10 minutes)
        self._health: Mapping[str, object] | None = None  # the voice service's health at the last readiness check
        self._detector = "not ready"  # the speech detector's state at the last readiness check (detector_state)
        self._turn_detector = "not ready"  # the turn detector's (Smart Turn), read with it
        self.saying = ""  # the clip being spoken now
        self.unsaid_line = False  # the last canned line (`_say_line`) was one Pocket TTS could not say
        self.said: list[str] = []  # clips finished (played whole or cut off), latest last; the newest 20 kept

    @property
    def in_call(self) -> bool:
        return self._call is not None

    def busy_with(self, aci: str) -> bool:
        """Whether a call with `aci` is on (or being placed): "My voice is ready" waits for its end."""
        aci = aci.lower()
        return (self._call is not None and self._call.aci == aci) or (self._dial is not None and self._dial.aci == aci)

    def text_waits(self, aci: str, *, stopped: bool = False) -> bool:
        """A message from `aci` waits for this call's end (the app: a text, voice note, photo or document, or a command
        that waits; owner, 23 Oct): whether that account gets code's line in its chat now, once a call. Never with no
        call on, nor for the caller of a practice call (a text typed during it is a practice turn, answered first after
        it). The caller's first also has her say so on the call, once, at the next quiet moment (`_text_line`).
        `stopped`: a thinking answer this call stopped (`App.process`), which she cannot see for the whole call: the
        caller's line then comes right after her greeting (`_told_first`; review of 5 Nov, DLG-0983-05)."""
        call, aci = self._call, aci.lower()
        if call is None or (call.practice and aci == call.aci):
            return False
        call.text_stopped = call.text_stopped or (stopped and aci == call.aci)
        if aci in call.texted:
            return False
        call.texted.add(aci)
        call.text_owed = call.text_owed or aci == call.aci
        return True

    @property
    def speaking(self) -> bool:
        return self._audio is not None and self._audio.speaking.is_set()

    def status_line(self, next_call: str = "", detector: str = "", turn: str = "") -> str:
        """/status: whether she is in a call, the last one, the speech detector's state (`detector`, when speech stops
        her or holds the pause), the turn detector's (`turn`, with early end) and (from the reminders) the next call
        she will place."""
        if self._call is not None:
            line = f"Calls: in a call since {local_hhmm(self._call.started_wall, self._tz)}"
        else:
            line = "Calls: ready"
            if self._last is not None:
                line += f" (last call {local_hhmm(self._last[0], self._tz)}, {self._last[1]} min)"
        if detector:
            line += f" · speech detector: {detector}"
        if turn:
            line += f" · turn detector: {turn}"
        return f"{line} · next call {next_call}" if next_call and self._call is None else line

    async def detectors(self) -> tuple[str, str]:
        """The two detectors' states for /status, from one health read (voice.detector_state: ready, loading,
        unavailable, old voice service or not ready): the speech detector's when speech stops her ([calls] interrupt =
        "only speech") or holds the pause (pause = "no speech"), the turn detector's with early end; "" for one that
        nothing waits for."""
        s = self.settings
        speech, early = s.barge == "speech" or s.pause == "speech", s.early_end
        if not speech and not early:
            return "", ""
        health = await self._voice.health()
        return (detector_state(health) if speech else "", detector_state(health, part="turn") if early else "")

    async def detector(self) -> str:
        """The speech detector's state for /status (see `detectors`); "" when nothing waits for it."""
        return (await self.detectors())[0]

    async def run_forever(self) -> None:
        folder = self.settings.socket_dir
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        for stale in folder.glob("signal-call-*.sock"):
            stale.unlink(missing_ok=True)
        async for event in self._signal.call_events():
            try:
                await self._on_event(event)
            except Exception:
                log.exception("calls: a call event failed")

    async def hangup(self) -> None:
        """Hang up a running call, or one she is placing that still rings (/lock, or the bridge is stopping)."""
        call = self._call
        if call is not None and not call.ended.is_set():
            try:
                await self._signal.hangup_call(call.aci, call.call_id)
            except Exception as exc:  # the call's own end hangs up again, checked (_hang_up)
                log.warning("calls: her hang-up failed (%s)", type(exc).__name__)
        dial = self._dial
        if dial is not None and dial.call_id is not None:
            try:
                await self._signal.hangup_call(dial.aci, dial.call_id)
            except Exception as exc:
                log.warning("calls: her hang-up of the call placed failed (%s)", type(exc).__name__)

    async def can_place(self, aci: str) -> str:
        """ "" when she can place a call now, else why not: locked (/lock), in_call (one call at a time), no_voice (her
        voice does not speak the language in use: Korean, Polish, Russian, Japanese; owner, 3 Nov 2026), not_ready (the
        voice) or downloading (jane's model of the language in use: her call for a reminder is in it)."""
        if self._locked():
            return "locked"
        if self._call is not None or self._dial is not None:
            return "in_call"
        return await self._placeable(self.language)

    async def _placeable(self, language: str) -> str:
        if not has_voice(language):
            return "no_voice"
        if not await self._voice_ready():
            return "not_ready"
        if self._downloads is not None and self._downloads.downloading(language):
            return "downloading"
        return ""

    async def place(self, aci: str, *, wake: bool, time: str, text: str) -> str:
        """Call the user for a reminder they set and wait until it is picked up or ends (PLACED_OUTCOMES). Picked up,
        the call goes on in its own task like an incoming one, opening with the wake-up or reminder line ({time}) and
        the reminder's text instead of a greeting."""
        aci = aci.lower()
        if aci not in self._allowed:
            log.error("calls: refused to call an account that is not allowed")
            return "call_failed"
        language = self.language  # the call's: fixed here, so /language during its ring never changes it (review 4 Nov)
        why = await self.can_place(aci)
        if not why and language != self.language:
            why = await self._placeable(self.language)  # /language while her voice was checked
            language = self.language
        if why:
            log.info("calls: no call placed (%s)", why)
            return "call_failed"
        dial = _Dial(aci)
        self._dial = dial
        try:
            try:
                started = await self._signal.start_call(aci)
            except Exception as exc:
                log.warning("calls: placing a call failed (%s)", type(exc).__name__)
                return "call_failed"
            call_id = started.get("callId")
            if not isinstance(call_id, int):
                log.warning("calls: startCall gave no call id")
                return "call_failed"
            dial.call_id = call_id
            if self._locked():  # /lock while signal-cli placed it (its hang-up had no call id yet)
                return await self._locked_dial(dial)
            log.info("calls: placing a call (%s)", "wake-up" if wake else "reminder")
            outcome, event = await self._ring(dial)
            if outcome != "answered" or event.get("state") != "CONNECTED":
                return outcome  # not picked up (or picked up on another device): nothing more here
            if self._locked():  # picked up as /lock came: hung up, never a call (review C1)
                return await self._locked_dial(dial)
            # her call for a reminder is never practice, and keeps the language it was placed in
            call = self._new_call(aci, call_id, practice_ok=False, language=language)
            lines = call.spoken
            line = f"{(lines.wake if wake else lines.call_reminder).format(time=time)} {text}".strip()
            while not dial.events.empty():  # whatever came after CONNECTED belongs to the call now
                call.events.put_nowait(dial.events.get_nowait())
            self._call = call
            self._task = asyncio.create_task(self._run_call(call, _Opening(started, event, line, wake)), name="call")
            return "answered"
        finally:
            self._dial = None

    async def _locked_dial(self, dial: _Dial) -> str:
        """/lock came while she placed a call: hung up, a failed call (the scheduler's retry then finds her locked)."""
        log.info("calls: the call placed was hung up: locked (/lock)")
        if dial.call_id is not None:
            with contextlib.suppress(Exception):
                await self._signal.hangup_call(dial.aci, dial.call_id)
        return "call_failed"

    async def _ring(self, dial: _Dial) -> tuple[str, dict[str, Any]]:
        """Wait (ring_wait_s) until the call she placed is picked up (CONNECTED) or ends; CONNECTING only means it
        reached the phone. No end in time: hung up, a failed call."""
        deadline = self._clock() + self.settings.ring_wait_s
        while (left := deadline - self._clock()) > 0:
            try:
                event = await asyncio.wait_for(dial.events.get(), left)
            except TimeoutError:
                break
            call_id = event.get("callId")
            if isinstance(call_id, int) and call_id != dial.call_id:
                continue
            state = event.get("state")
            if state == "CONNECTED":
                return "answered", event
            if state == "ENDED":
                reason = str(event.get("reason") or "")
                outcome = ended_outcome(reason)
                log.info("calls: the call placed ended before it was picked up (%s: %s)", reason[:40], outcome)
                return outcome, event
        log.warning("calls: the call placed did not end in %.0f s; hanging up", self.settings.ring_wait_s)
        if dial.call_id is not None:
            with contextlib.suppress(Exception):
                await self._signal.hangup_call(dial.aci, dial.call_id)
        return "call_failed", {}

    async def _on_event(self, event: dict[str, Any]) -> None:
        state = event.get("state")
        call_id = event.get("callId")
        call = self._call
        if call is not None and (call_id == call.call_id or (call_id is None and state == "ENDED")):
            call.events.put_nowait(event)
            return
        dial = self._dial
        if (
            dial is not None
            and state != "RINGING_INCOMING"
            and (dial.call_id is None or call_id in (None, dial.call_id))
        ):
            dial.events.put_nowait(event)  # the ringing event comes before startCall's reply names the call id
            return
        if state != "RINGING_INCOMING" or not isinstance(call_id, int):
            return
        aci = str(event.get("uuid") or "").lower()
        if aci not in self._allowed:
            log.info("calls: a call from an account that is not allowed was ignored")
            return
        if self._locked():  # ignored, never rejected (a reject is a reply): it rings out
            log.info("calls: a call was ignored while locked (/lock)")
            return
        if call is not None or dial is not None:
            log.info("calls: a second call was ignored")
            return
        practised = self._practice(aci)
        language = practised or self.language  # the call's, as _new_call reads it
        if not has_voice(language):  # no voice in it (owner, 3 Nov 2026): it rings out, never rejected, with a text
            await self._no_voice(aci, language, practice=bool(practised))
            return
        ready = await self._voice_ready()
        # her ears work and her voice does not (rests, cannot run, one voice left out): answered as a text-only call
        text_only = not ready and self._health is not None and self._health.get("stt") == "ready"
        text_only = text_only and voice_not_working(self._health, self._voice.voice)
        if not ready and not text_only:
            await self._not_ready(aci)
            return
        if self._downloads is not None and ready:
            await self._downloads.wake(language, self._health)  # an idle model loads while it rings (review 4 Nov)
        if self._downloads is not None and (note := self._downloads.call_note(aci, language)):
            log.info("calls: a call rang while her voice in its language (%s) downloads (text sent)", language)
            await self._host.call_note(aci, note)  # it rings out, never rejected
            return
        if self._locked():  # /lock while her voice was checked: ignored too
            log.info("calls: a call was ignored while locked (/lock)")
            return
        self._call = self._new_call(aci, call_id, practice_ok=True, text_only=text_only)
        self._task = asyncio.create_task(self._run_call(self._call), name="call")

    def _new_call(
        self, aci: str, call_id: int, *, practice_ok: bool, language: str = "", text_only: bool = False
    ) -> _Call:
        """A call's language: the one practised (/babble) when practice runs for this user and the user made the call
        (`practice_ok`), else the user's own (`language` when given: a call she placed keeps the one it was placed in).
        A call she places for a reminder or a wake-up is never a practice call: its turns may set a reminder, and its
        end leaves practice running."""
        practised = self._practice(aci) if practice_ok else None
        language = practised or language or self.language
        lines = prompts.call_lines(language, name=self.name)
        if practised:
            log.info("calls: a practice call (%s)", practised)
        return _Call(
            aci, call_id, self._clock(), self._wall(), language, lines, practice=bool(practised), text_only=text_only
        )

    async def _rings_out(self, call: _Call) -> bool:
        """Let it ring (about twice) before answering; True when the caller hung up meanwhile."""
        deadline = self._clock() + self.settings.answer_after_s
        while (left := deadline - self._clock()) > 0:
            try:
                event = await asyncio.wait_for(call.events.get(), left)
            except TimeoutError:
                return False
            if event.get("state") == "ENDED":
                log.info("calls: the caller hung up while it rang")
                call.ended.set()
                return True
        return False

    async def _voice_ready(self) -> bool:
        health = await self._voice.health()
        self._health = health  # the not-ready note's reason, and a language model's state for its warm-up
        # checked just before every call: the call's barge-in and end of turn use them
        self._detector = detector_state(health)
        self._turn_detector = detector_state(health, part="turn")
        return voice_ready(health, self._voice.voice)

    def _barge(self, stream: str) -> Barge:
        """The call's barge-in rules: the speech detector on the call's `stream` (one detector state per call) when the
        service has it ready, else loudness, said once when speech should have stopped her or held the pause."""
        s = self.settings
        if self._detector != "ready":
            if s.barge == "speech" or s.pause == "speech":
                log.info("calls: speech detector %s; loudness decides this call", self._detector)
            return Barge(s.barge)
        return Barge(s.barge, functools.partial(self._voice.vad, stream=stream))

    def _turn_end(self, stream: str) -> TurnEnd | None:
        """The call's end-of-turn rules (call_audio.TurnEnd): Smart Turn when early end is on and its detector is
        ready (else the full pause, said once), and the pause by speech on the call's barge-in `stream` when it is on
        and the speech detector is ready (else loudness: `_barge` said so). None with both off: today's listen."""
        s = self.settings
        if not s.early_end and s.pause != "speech":
            return None
        judge = None
        if s.early_end and self._turn_detector == "ready":
            judge = self._voice.turn
        elif s.early_end:
            log.info("calls: turn detector %s; the full pause decides this call", self._turn_detector)
        detect = None
        if s.pause == "speech" and self._detector == "ready":
            detect = functools.partial(self._voice.vad, stream=stream)
        return TurnEnd(judge, detect, early_pause_s=s.early_end_pause_s, early_end=s.early_end, pause=s.pause)

    async def _not_ready(self, aci: str) -> None:
        """A ring while the voice service cannot take a call: it rings out (never rejected) with code's text, at most
        once in NOT_READY_NOTE_SECONDS: the true reason (the review of 4 Nov), still loading (CALL_NOT_READY) or not
        working (CALL_VOICE_FAILED: her English Pocket model resting after failed runs, unable to run, or her voice one
        it leaves out); either way the voice-ready note follows."""
        now = self._clock()
        promised = self._not_ready_at is None or now - self._not_ready_at >= NOT_READY_NOTE_SECONDS
        self._missed(aci, promised)  # every ring counts, also one whose note is held back: the voice-ready note follows
        failed = voice_not_working(self._health, self._voice.voice)
        why = "not working" if failed else "not ready"
        if not promised:
            log.info("calls: a call rang while the voice service was %s (note held back: sent minutes ago)", why)
            return
        self._not_ready_at = now
        log.info("calls: a call rang while the voice service was %s (note sent)", why)
        await self._host.call_note(aci, prompts.CALL_VOICE_FAILED if failed else prompts.CALL_NOT_READY)

    async def _no_voice(self, aci: str, language: str, *, practice: bool) -> None:
        """A ring in a language her voice does not speak (owner, 3 Nov 2026): it rings out, never rejected, with code's
        text at most once in NOT_READY_NOTE_SECONDS an account (the review of 4 Nov: three rings, three texts); in
        practice the text names /babble off, the way out."""
        now = self._clock()
        last = self._no_voice_at.get(aci)
        if last is not None and now - last < NOT_READY_NOTE_SECONDS:
            log.info("calls: a call rang in a language her voice does not speak (%s) (text held back)", language)
            return
        self._no_voice_at[aci] = now
        log.info("calls: a call rang in a language her voice does not speak (%s) (text sent)", language)
        note = prompts.CALL_NO_VOICE_PRACTICE if practice else prompts.CALL_NO_VOICE
        await self._host.call_note(aci, note.format(language=LANGUAGES[language].english))

    async def _run_call(self, call: _Call, opening: _Opening | None = None) -> None:
        """An incoming call (rings, then answered), or one she placed that was picked up (`opening`)."""
        self._host.call_started(call.aci, practice=call.practice)
        self._warm_up(call)  # after the start (the app's glance is read from it), before the ringing
        log.info("calls: answering" if opening is None else "calls: the call placed was picked up")
        audio: CallAudio | None = None
        tasks: list[asyncio.Task[Any]] = []
        path: Path | None = None
        try:
            if opening is None:
                if await self._rings_out(call):
                    if call.text_only:  # hung up while it rang: what a ring-out got (note, hold, missed record)
                        await self._not_ready(call.aci)
                    return
                if self._locked():  # /lock while it rang: ignored, never rejected (a reject is a reply)
                    log.info("calls: a call was ignored while locked (/lock)")
                    call.ended.set()  # it rings out: no hang-up below
                    return
                accepted = await self._signal.accept_call(call.aci, call.call_id)
                connected = await self._wait_connected(call)
            else:
                accepted, connected = opening.started, opening.connected
            if call.text_only:  # her promise is recorded once its note went out (`_converse_text`)
                log.info("calls: answered with her voice not working; her answers go as text")
            else:
                self._reached(call.aci)  # connected: no "voice ready" note owed to this caller
            call.answered = opening is None  # one she placed came from her, not from a device of the user's
            path = socket_path(accepted, connected, self.settings.socket_dir, call.call_id)
            reader, writer = await self._connect(path)
            stream = uuid.uuid4().hex  # one speech detector state for the call: barge-in and the pause by speech
            audio = CallAudio(reader, writer, self._barge(stream))
            call.turn_end = self._turn_end(stream)
            self._audio = audio
            tasks = [asyncio.create_task(audio.pump()), asyncio.create_task(self._watch(call, audio))]
            await asyncio.wait_for(audio.started.wait(), CONNECT_SECONDS)
            await self._converse(call, audio, opening)
        except (_Ended, ConnectionError) as exc:
            if not call.ended.is_set():
                call.dropped = True
                log.warning("calls: the call's audio ended (%s)", type(exc).__name__)
        except VoiceError:
            call.dropped = True
            log.warning("calls: the voice service failed during the call")
        except Exception:
            call.dropped = True
            log.exception("calls: the call failed")
        finally:
            for task in tasks:
                task.cancel()
            if call.text_clip is not None:  # a call that ends before her line about a text: nothing more is said
                call.text_clip.cancel()
            if audio is not None:
                audio.barge.close()
                log.info("%s", audio.barge.summary())
                if call.turn_end is not None:
                    call.turn_end.close()
                    for line in call.turn_end.summary():
                        log.info("%s", line)
                audio.close()
            if not call.ended.is_set():
                running = asyncio.current_task()
                await self._hang_up(call, check=running is None or not running.cancelling())  # stopping: once
            if path is not None:
                path.unlink(missing_ok=True)
            minutes = max(1, round((self._clock() - call.started) / 60))
            span = call_span(call.started_wall, self._wall(), self._tz)  # for the notes after it
            self._audio = None
            self._call = None
            self.saying = ""
            self._last = (call.started_wall, minutes)
            self._host.call_ended()
            log.info("calls: ended after %d min, %d turns%s", minutes, call.user_turns, ", text only" * call.text_only)
            log.info("%s", waits_line(call.waits, call.stopped_before_words))
            await self._after(call, span)

    async def after_notes(self, limit: float) -> None:
        """Wait until the notes after the last call have gone out (`_after`), at most `limit` seconds; at once when none
        are owed (review of 4 Nov, calls-4)."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._after_done.wait(), limit)

    async def _hang_up(self, call: _Call, *, check: bool = True) -> None:
        """Her hang-up at the call's end, checked (review 0.9.7, C15: a failed hangupCall was swallowed and nothing
        checked the call ended): a failure is logged by its kind, never the account; with no ENDED within
        HANGUP_WAIT_S she hangs up once more, and a call still on after that is one warning. Without `check` (the
        bridge is stopping, and no event reaches the call any more) once, logged the same."""
        for attempt in range(2):
            try:
                await self._signal.hangup_call(call.aci, call.call_id)
            except Exception as exc:
                log.warning("calls: her hang-up failed (%s)", type(exc).__name__)
            if not check or await self._ends_within(call, HANGUP_WAIT_S):
                return
            if not attempt:
                log.warning("calls: the call did not end %.1f s after her hang-up; hanging up once more", HANGUP_WAIT_S)
        log.warning("calls: the call did not end after her hang-up, tried twice")

    async def _ends_within(self, call: _Call, seconds: float) -> bool:
        """Whether signal-cli's ENDED for the call comes within `seconds` (the call's other events are dropped)."""
        try:
            async with asyncio.timeout(seconds):
                while (await call.events.get()).get("state") != "ENDED":
                    pass
        except TimeoutError:
            return False
        call.ended.set()
        return True

    def _warm_up(self, call: _Call) -> None:
        """The model's warm-up for this call (Host.call_warm_up), once, in its own task: the call never awaits it, and
        its end never cancels it (an abort while Ollama loads the model stops Ollama's runner; a finished read is
        harmless)."""
        if call.warm_up is not None:
            return
        task = asyncio.create_task(self._warming(call), name="call-warm-up")
        call.warm_up = task
        self._warm_ups.add(task)
        task.add_done_callback(self._warm_ups.discard)

    async def _warming(self, call: _Call) -> None:
        try:
            await self._host.call_warm_up(call.aci, practice=call.practice)
        except Exception as exc:  # never into the call: one line, the kind only
            log.warning("calls: model warm-up failed (%s)", type(exc).__name__)

    async def _after(self, call: _Call, span: str) -> None:
        """The notes after a call, in order: the practice recap, the dropped or unfinished note, the chords message,
        then the summary, whose header names the call's start and end (`span`). A call she answered whose summary did
        not go out gets code's note with them in its place (`prompts.CALL_ANSWERED_NOTE`: a call names no device, so a
        call from a device the user did not expect shows on all of theirs; laptop path, Phase 0, owner 3 Nov). Sent at
        once, also in quiet hours, as every note after a call: the user just called. Nothing once locked. They are owed
        from the first moment (`after_notes`: the app is told the call is over just before, which releases its worker,
        and the worker waits for them while Deep Thinking answers, else its answer holds Ollama's one slot for minutes
        and the summary waits behind it; review of 4 Nov, calls-4), whatever comes of them; not while a practice call's
        texts are drained (`_notes_after`: that drain waits for the worker, review of 4 Nov, calls-2)."""
        self._after_done.clear()
        try:
            await self._notes_after(call, span)
        finally:
            self._after_done.set()

    async def _notes_after(self, call: _Call, span: str) -> None:
        summarised = False
        with contextlib.suppress(Exception):
            if call.practice and call.user_turns:  # first: the texts typed meanwhile as practice, then the recap
                # its drain waits for the app's worker, and a worker that holds such a text waits for these very notes
                # while Deep Thinking answers (`after_notes`): none are owed meanwhile (review of 4 Nov, calls-2)
                self._after_done.set()
                try:
                    await self._host.practice_call_ended(call.aci)
                finally:
                    self._after_done.clear()  # the rest of them are owed as ever; `_after` sets it at the end
            if call.dropped:
                await self._host.call_note(call.aci, prompts.CALL_DROPPED)
            if call.unfinished:  # the caller may think it was done: say it was not
                await self._host.call_note(call.aci, prompts.CALL_UNFINISHED)
            elif call.timeouts:  # the last request was stopped by the turn's time limit, never by the call's end
                await self._host.call_note(call.aci, prompts.CALL_TOO_LONG_NOTE)
            if not call.practice:  # code's exact chords before Gemma's summary, which can fail or be skipped
                await self._host.call_chords(call.aci)
            if self._locked():  # /lock ended it: no summary is written (it would never go out), nor code's note
                return
            if self.settings.summary and call.user_turns >= 2 and not call.practice:  # practice: the recap instead
                summarised = await self._host.call_summary(call.aci, call.lines, span)
        if call.answered and not summarised and not self._locked():  # its own try: a failure above never skips it
            with contextlib.suppress(Exception):
                await self._host.call_note(call.aci, prompts.CALL_ANSWERED_NOTE.format(span=span))

    async def _wait_connected(self, call: _Call) -> dict[str, Any]:
        async with asyncio.timeout(30):
            while True:
                event = await call.events.get()
                if event.get("state") == "CONNECTED":
                    return event
                if event.get("state") == "ENDED":
                    call.ended.set()
                    raise _Ended(str(event.get("reason")))

    async def _connect(self, path: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        deadline = self._clock() + CONNECT_SECONDS
        while True:
            try:
                return await asyncio.open_unix_connection(str(path))
            except (FileNotFoundError, ConnectionRefusedError):
                if self._clock() >= deadline:
                    raise
                await asyncio.sleep(0.1)

    async def _watch(self, call: _Call, audio: CallAudio) -> None:
        while True:
            event = await call.events.get()
            if event.get("state") == "ENDED":
                reason = str(event.get("reason") or "signal-cli")
                log.info("calls: ended by %s", reason)
                if reason.startswith("tunnel") or reason == "signal_cli_restarted":
                    call.dropped = True  # the user gets the "dropped on my side" note
                call.ended.set()
                audio.stop_speaking()
                audio.close()
                return

    async def _converse(self, call: _Call, audio: CallAudio, opening: _Opening | None = None) -> None:
        s = self.settings
        lines = call.spoken
        if call.text_only:
            return await self._converse_text(call, audio)
        if call.text_stopped and call.text_owed:
            self._text_clip(call)  # her line about it comes right after her greeting: its speech is made meanwhile
        if opening is None:
            greetings = (s.greetings or lines.greetings) if call.language == ENGLISH else lines.greetings
            self._last_greeting = greeting = self._pick(greetings, last=self._last_greeting)
            pending = await self._say_line(audio, greeting, call.language, barge=True)
        else:  # a call she placed: the reminder's line (its text is the user's: never kept with the canned clips)
            # the callee's "Hello?" over it is no turn: it goes on (the second deep scan, calls-hello-over-opening)
            pending = await self._say_line(audio, opening.line, call.language, barge=True, keep=False, greeted=True)
        waking = opening is not None and opening.wake  # silence after "Good morning!" ends the call with a goodbye
        # standby (owner, 18 Oct): after the check-in only a turn with her name is answered (wake.says_name), so the
        # caller can do other things, noise and all; every idle_repeat_s of call time from her last line she says
        # she is still here, until the length cap: silence never ends a call
        standby = False
        line_at = 0.0  # call time of her last check-in or still-here line, or of standby's start
        idle_said_at: float | None = None  # call time of the last check-in ("Take your time, I'm here. Say …")
        question_waiting = False  # her last answer asked for a device's yes, and it was heard whole
        hearing_trouble = 0
        went_on = False  # the caller went on after her goodbye: their words are a turn, no cut-in
        # a text this call stopped: her line right after the greeting (no cut-in over it), before the first listen
        if pending is None and call.text_stopped and call.text_owed and not call.ended.is_set():
            pending, went_on = await self._told_first(call, audio), True  # words over it: a turn as any, never a cut-in
            waking = waking and pending is None
        while not call.ended.is_set():
            left = s.max_s - audio.frames_in * FRAME_S  # call time, like the idle timers
            if left <= 0:
                await self._say_line(audio, lines.max_bye, call.language)
                return
            cutting_in = pending is not None and not went_on
            went_on = False
            if pending is None:
                now = audio.frames_in * FRAME_S
                wait = max(1.0, line_at + s.idle_repeat_s - now) if standby else s.idle_prompt_s
                if waking:
                    wait = s.wake_silence_s
                audio.unwatch()  # a watch after an earlier end is over: what comes now is this listen's
                clip, info = await audio.listen(
                    end_silence_s=s.end_of_turn_s,
                    no_speech_s=min(wait, left),
                    turn=call.turn_end,
                    early=STANDBY_EARLY_END or not standby,  # standby's turn waits the full pause (calls-02)
                    # the caller's text waits: her line at the next quiet moment, never between a device question
                    # and its yes, nor after words of hers that ask something (in standby only her name is answered)
                    line=None if question_waiting or (call.asked and not standby) else lambda: self._text_due(call),
                )
                call.turn_ended_at, call.first_sound_at = self._clock(), None  # her wait counts from here
                if info.get("line"):  # in standby too, which stays; words over it are a turn as any, never a cut-in
                    pending, went_on = await self._text_line(call, audio), True
                    waking = waking and pending is None  # words over it answer a wake-up call's opening too
                    line_at = audio.frames_in * FRAME_S if standby else line_at  # still here: from her latest line
                    continue
                if not clip and waking and wait <= left:
                    log.info("calls: no answer to the wake-up line in %.0f s; goodbye", wait)
                    await self._say_line(audio, self._pick(lines.bye), call.language)
                    return
                waking = False
                if not clip:
                    next_step = "listening on" if wait > left else "still here" if standby else "checking in"
                    # the line's levels (metadata): a caller too quiet to start an utterance shows up here
                    log.info(
                        "calls: no speech in %.0f s (floor %s, peak %s); %s",
                        min(wait, left),
                        info.get("floor"),
                        info.get("peak"),
                        next_step,
                    )
                    if wait > left:  # the length cap, not silence
                        continue
                    now = audio.frames_in * FRAME_S
                    if standby:
                        line_at = now
                        await self._say_line(audio, lines.still_here, call.language)
                        continue
                    recent = idle_said_at is not None and now - idle_said_at < s.idle_repeat_s
                    check_in: bytes | None = None
                    if not recent:
                        try:  # standby only once its rule can be heard (the review of 4 Nov)
                            check_in = await self._clip(lines.idle, call.language)
                        except VoiceNotSaidError:
                            line_at = now
                            log.info("calls: Pocket TTS could not say the check-in; no standby, listening on")
                            continue
                    standby, line_at = True, now
                    await self._host.call_standby(call.aci)  # no yes waits any more: her wake line invites one
                    question_waiting = call.ends_after_question = False
                    log.info("calls: standby: only a turn with her name is answered")
                    if recent:
                        assert idle_said_at is not None
                        log.info("calls: said take-your-time %.0f s ago; listening on", now - idle_said_at)
                        continue  # said a short while ago: quiet, in standby all the same
                    idle_said_at = now
                    await self._say_line(audio, lines.idle, call.language, made=check_in)
                    continue
                voiced_s = float(info.get("voiced_s", 99.0))
                if voiced_s < MIN_VOICED_S:  # a click or a bump: not worth asking speech recognition
                    log.info(
                        "calls: noise (voiced %.2f s, peak %s, floor %s); listening on",
                        voiced_s,
                        info.get("peak"),
                        info.get("floor"),
                    )
                    self._no_turn(call)
                    continue
                self._watch_after(call, audio, info)
                hint = self.name if standby and STANDBY_HINT else ""
                named = False
                try:
                    text = await self._hear(call, clip, info, hint=hint)
                    # typing or noise heard as "thank" or "*music*": silence
                    noise = is_noise_transcript(text, float(info.get("sustained_s", voiced_s)))
                    if not noise:
                        text, named = await self._hold(call, audio, text, clip, info, hint=hint, standby=standby)
                except VoiceError:
                    hearing_trouble += 1
                    if hearing_trouble >= 2:
                        raise
                    await self._say_line(audio, lines.hearing, call.language)
                    continue
                hearing_trouble = 0
                if noise:
                    log.info("calls: noise heard as words; listening on")
                    self._no_turn(call)
                    continue
            else:
                text, pending = pending, None
                named = False
            if not real_words(text):  # a cough, a noise, a breath: she just keeps listening (no "sorry?")
                log.info("calls: a sound without words; listening on")
                if not cutting_in:
                    self._no_turn(call)
                continue
            # real words answer a wake-up call's opening, also said over it (the review of 28 Oct, calls-04: a silence
            # after them ended the call with the wake line's goodbye, never the check-in)
            waking = False
            woke = standby  # this turn ends standby when it says her name
            if standby:
                # `named`: her name alone was heard before a hold joined what followed (Whisper may lose it in the
                # joined clip, as a count after a paused name did in the wake harness)
                if not (named or says_name(text, self.name)):  # said to someone else, or the TV: never answered or kept
                    log.info(  # how near it came, a number (a missed name shows as 1 or 2 off), never a word
                        "calls: standby: a turn without her name (%d words, nearest %d letters off); listening on",
                        turn_words(text),
                        nearest(text, self.name),
                    )
                    self._no_turn(call)  # no turn of the caller's for the end lines (review C16, 0.9.7)
                    continue
                standby = False
                log.info("calls: standby over: her name")
                if only_name(text, self.name, calling_words(call.language)):  # nothing to answer but that she is here
                    log.info("calls: her name alone; her wake line, listening")
                    # words over it are the caller's next turn, never a cut-in (it was no answer of hers)
                    pending, went_on = (
                        await self._say_line(audio, self._pick(lines.woken), call.language, barge=True),
                        True,
                    )
                    continue
            if is_lone_stop(text, name=self.name):
                await self._host.call_drop_waiting(call.aci)  # like any answer but yes, a stop drops the question
                question_waiting = call.ends_after_question = False
                continue  # quiet, listening
            if is_goodbye(text, name=self.name):
                call.lines.append(f"User: {text}")
                await self._say_line(audio, self._pick(lines.bye), call.language)
                return  # hung up in _run_call
            call.lines.append(f"User: {text}")
            call.user_turns += 1
            said = f"{prompts.CUTTING_IN} {text}" if cutting_in else text
            # a "got it" only for a request: never before the answer to a device question (it comes at once, and a
            # sound must not seem a yes), never after a few words ("how are you?" needs no "got it"), never on two
            # turns in a row (owner, 16 Oct: repetitive)
            may = s.acknowledge and not question_waiting and not call.acknowledged_last
            acknowledge = may and turn_words(text) >= ACK_MIN_WORDS
            call.acknowledged_last = acknowledge
            # the model's first try came back empty (research, 17 Oct: about 10 s of silence, then the retry): the
            # same rules but the few words', which would leave only turns that had one at their start; one a turn
            turn = await self._turn(
                call, audio, said, acknowledge=acknowledge, on_retry=may and not acknowledge, woke=woke
            )
            call.unfinished = call.unfinished and turn.failed  # answered: nothing is left undone any more
            spoke = await self._speak(call, audio, turn, waiting=turn.asks_yes)
            interruption, taken_at = spoke.interruption, spoke.taken_at
            call.lines.append(f"{self.name}: {spoke.heard}")
            question_waiting = turn.asks_yes and interruption is None and not spoke.stopped
            if spoke.stopped and turn.asks_yes:  # its question went unheard: no yes may act on it, and code says so
                call.ends_after_question = False
                await self._host.call_drop_waiting(call.aci, unheard=True)
                continue
            if spoke.stopped and spoke.texted:  # the rest went as a text: the call stays up, the caller reads it
                call.ends_after_question = False  # (owner, 28 Oct)
                continue
            # stopped with nothing new texted (only lines that go as texts of their own): as a turn said whole (the
            # review of 4 Nov: the call stayed up after the caller's goodbye)
            if interruption is not None and not cutting_in:
                self._taken_over(call, interruption, taken_at)
            if interruption is not None:
                if turn.reply_id is not None:  # her history: what was heard of her stored reply, never code's lines
                    self._host.call_interrupted(turn.reply_id, spoke.kept)
                if turn.asks_yes:  # cut off before the question was asked whole: no yes may act on it
                    await self._host.call_drop_waiting(call.aci)
                pending = interruption
            elif over := self._call_over(call, text, turn, spoke.heard):
                if over == AFTER_QUIET:  # her plain goodbye: the call ends unless the caller goes on
                    later = await self._after_goodbye(call, audio)
                    if later is not None:
                        pending, went_on = later, True
                        continue
                return  # her answer said whole, the call over (_call_over): hung up in _run_call

    async def _converse_text(self, call: _Call, audio: CallAudio) -> None:
        """A call answered while her voice is not working (owner, 4 Nov 2026: "reply by text"): she listens, and not one
        speech request is made. After code's note (`CALL_TEXT_ONLY`, once; only then is her promise to text when her
        voice works recorded) every turn is answered, no name needed, as a Signal text (`_write`); no greeting, "Got
        it.", thinking, checking, wake, text-waits, check-in, standby or still-here line. The call ends by the caller's
        own closing (`_call_over`: goodbye, leave-taking, hang-up request, wrap-up), her end_call, the time cap or a
        hang-up, never by her goodbye (its quiet would start when the text is queued; review F2), with no texted
        goodbye; the time cap's bye and the hearing line go as texts. Stays so until it ends, also when her voice is back."""
        s, lines = self.settings, call.spoken
        try:
            await self._host.call_note(call.aci, prompts.CALL_TEXT_ONLY)
            self._missed(call.aci, True)  # the voice-ready note keeps the promise
        except Exception:  # the call goes on, promising nothing; the log says it, never the text
            log.exception("calls: the text-only note could not be sent")
        hearing_trouble = 0
        while not call.ended.is_set():
            left = s.max_s - audio.frames_in * FRAME_S
            if left <= 0:
                await self._text_out(call, lines.max_bye)
                return
            audio.unwatch()
            clip, info = await audio.listen(
                end_silence_s=s.end_of_turn_s, no_speech_s=min(s.idle_prompt_s, left), turn=call.turn_end
            )
            asked_at = self._clock()  # the turn ended here
            voiced_s = float(info.get("voiced_s", 99.0))
            if not clip:  # silence never ends a call, nor starts standby
                log.info(
                    "calls: no speech in %.0f s (floor %s, peak %s); listening on",
                    min(s.idle_prompt_s, left),
                    info.get("floor"),
                    info.get("peak"),
                )
                continue
            if voiced_s < MIN_VOICED_S:  # a click or a bump: not worth asking speech recognition
                log.info("calls: noise (voiced %.2f s, peak %s); listening on", voiced_s, info.get("peak"))
                self._no_turn(call)
                continue
            self._watch_after(call, audio, info)
            try:
                text = await self._hear(call, clip, info)
                noise = is_noise_transcript(text, float(info.get("sustained_s", voiced_s)))
                if not noise:
                    text, _ = await self._hold(call, audio, text, clip, info)
            except VoiceError:
                hearing_trouble += 1
                if hearing_trouble >= 2:
                    raise
                await self._text_out(call, lines.hearing)
                continue
            hearing_trouble = 0
            if noise:
                log.info("calls: noise heard as words; listening on")
                self._no_turn(call)
                continue
            if not real_words(text):
                log.info("calls: a sound without words; listening on")
                self._no_turn(call)
                continue
            if is_lone_stop(text, name=self.name):
                await self._host.call_drop_waiting(call.aci)
                call.ends_after_question = False
                continue
            call.lines.append(f"User: {text}")
            if is_goodbye(text, name=self.name):
                return
            call.user_turns += 1
            turn = await self._turn(call, audio, text, acknowledge=False)
            call.unfinished = call.unfinished and turn.failed
            heard = await self._write(call, audio, turn)
            if heard:
                log.info("calls: texted %d ms after the end of the turn", round((self._clock() - asked_at) * 1000))
            call.lines.append(f"{self.name}: {heard}")
            if self._call_over(call, text, turn, heard) == AT_ONCE:
                return

    async def _write(self, call: _Call, audio: CallAudio, turn: Turn) -> str:
        """Her answer to a text-only call's turn, as texts, and what the caller read of it. The stored reply (her words
        whole and `Turn.kept_code`, the device question last) goes as her own text answer (`Host.call_reply_text`),
        awaited: only then is a yes a yes to a question that was read, so what came in while she worked is dropped (a
        spoken answer's fresh drain); not sent, a device question waiting is dropped and nothing was read. A failed
        turn's line goes as code's text; code's other lines, but those with texts of their own (`Turn.also_texted`,
        `_texted`), as code's note, never in her reply (rule 6)."""
        read = True
        if turn.failed:
            await self._text_out(call, turn.text)
        elif turn.reply_id is not None and (turn.text.strip() or turn.kept_code):
            try:
                read = await self._host.call_reply_text(call.aci, turn.reply_id)
            except Exception:  # the call goes on; the log says it, never the text
                log.exception("calls: her answer could not be sent as a text")
                read = False
            if read:
                log.info("calls: %.1f s of sound said before the text went out is dropped", audio.drain_incoming())
            elif turn.asks_yes:
                await self._host.call_drop_waiting(call.aci, unheard=True)
        kept = {part for line in turn.kept_code for part in _parts(line)}
        rest = [(part, False) for part in _parts(turn.code_text) if part not in kept]
        await self._text_rest(call, turn, self._texted(turn, rest), hers=False)
        return " ".join(part for part in (turn.text.strip(), turn.code_text.strip()) if part) if read else ""

    async def _text_out(self, call: _Call, line: str) -> None:
        """One of her code-written lines as code's text, where a call would say it (nothing is spoken); in the practice
        conversation on a practice call."""
        try:
            await self._host.call_text(call.aci, line, reply_id=None, outside=False, practice=call.practice)
        except Exception:  # the call goes on; the log says it, never the text
            log.exception("calls: her line could not be stored to go as a text")

    async def _text_line(self, call: _Call, audio: CallAudio) -> str | None:
        """Her line that the caller's text waits for the call's end (`text_waits`), once a call, in the call's language,
        at a quiet moment (`CallAudio.listen(line=…)`), with barge-in: the caller's words over it come back (a sound
        without words: None). Code's line: never in her history or the call's summary."""
        call.text_owed = False
        log.info("calls: a text waits for the call's end; her line about it")
        made = call.text_clip.result() if call.text_clip is not None else None  # made ahead (_text_due)
        heard = await self._say_line(audio, call.spoken.text_waits, call.language, barge=True, made=made)
        return heard if heard is not None and real_words(heard) else None

    def _text_clip(self, call: _Call) -> asyncio.Task[bytes]:
        """The speech of her line about the caller's text, made in the background, once a call."""
        if call.text_clip is None:
            call.text_clip = asyncio.create_task(self._clip(call.spoken.text_waits, call.language))
            call.text_clip.add_done_callback(lambda task: task.cancelled() or task.exception())  # retrieved: no warning
        return call.text_clip

    async def _told_first(self, call: _Call, audio: CallAudio) -> str | None:
        """Her line about a text this call stopped (`_Call.text_stopped`), said as her greeting ends, before the first
        listen: every greeting asks something, and a line behind her question (`_Call.asked`) came after her answer to
        the caller's first turn, which was answered as if the text were not there, then again after the call (review of
        5 Nov, DLG-0983-05). Its speech was made while she greeted (`_text_clip`); a failed one is dropped (`_text_due`)
        and the call goes on. What the caller says over it comes back, as for `_text_line`."""
        await asyncio.wait({self._text_clip(call)})  # never raises
        return await self._text_line(call, audio) if self._text_due(call) else None

    def _text_due(self, call: _Call) -> bool:
        """Whether her line about the caller's text (`_text_line`) waits and its speech is ready. Asked on every frame
        of a listen, it has the speech made at once, so the line starts the moment the listen gives way (made after
        it, the line came late over the caller, whose first words were thrown away; review, 23 Oct). A failed one is
        dropped: the call goes on."""
        if not call.text_owed:
            return False
        made = call.text_clip
        if made is None:
            self._text_clip(call)
            return False
        if not made.done():
            return False
        failed = made.exception() if not made.cancelled() else asyncio.CancelledError()
        if failed is not None:
            call.text_owed = False
            log.warning("calls: her line about a waiting text failed (%s); not said", type(failed).__name__)
        return failed is None

    async def _hear(self, call: _Call, clip: bytes, info: Mapping[str, Any], *, hint: str = "") -> str:
        """What the caller said in `clip` (Whisper, told the call's language, and `hint`, her name in standby, as its
        prompt); the log has its numbers only."""
        heard = await self._voice.transcribe(pcm48_to_wav16k(clip), language=call.language, hint=hint, call=True)
        text = heard.text  # a call's: a bare "Bye." or "Thank you." is kept, is_noise_transcript judges it
        voiced_s = float(info.get("voiced_s", 99.0))
        log.info(
            "calls: heard %d words (speech %.1f s, voiced %.2f s, sustained %.2f s, peak %s, floor %s)",
            len(text.split()),
            float(info.get("speech_s", 0.0)),
            voiced_s,
            float(info.get("sustained_s", voiced_s)),
            info.get("peak"),
            info.get("floor"),
        )
        return text

    async def _hold(
        self,
        call: _Call,
        audio: CallAudio,
        text: str,
        clip: bytes,
        info: Mapping[str, Any],
        *,
        hint: str = "",
        standby: bool = False,
    ) -> tuple[str, bool]:
        """An early end whose words end the call or end like they go on, or in standby are her name alone
        (`_hold_reason`), waits out the full pause after all: a listen for the rest of it in call time (what came in
        meanwhile is heard first). Nobody speaks: the turn stands. The caller goes on: that is joined to the turn's
        clip and heard whole, and checked again, up to HOLD_MAX_S of audio (call_audio.TurnEnd: `held`, then
        `joined`). Returns the turn's words, and whether her name alone was heard before what was joined to it."""
        turn_end, s = call.turn_end, self.settings
        named = False
        while turn_end is not None and info.get("ended") in EARLY_KINDS:
            room = HOLD_MAX_S - len(clip) / (2 * RATE)
            reason = self._hold_reason(call, text, standby=standby)
            if reason is None or room < 1.0:
                break
            named = named or reason == "her name"
            audio.unwatch()
            turn_end.held(reason)
            rest = max(0.0, s.end_of_turn_s - float(info.get("quiet_s", 0.0)))
            more, more_info = await audio.listen(
                end_silence_s=s.end_of_turn_s,
                no_speech_s=rest,
                max_s=room,
                turn=turn_end,
                early=STANDBY_EARLY_END or not standby,  # standby's hold waits the full pause too (second deep scan)
            )
            if not more:
                log.info("calls: early end held (%s); the turn stands", reason)
                break
            call.turn_ended_at = self._clock()  # the caller went on: the turn ends here, and her wait counts from here
            log.info("calls: early end held (%s); the caller went on", reason)
            turn_end.joined()
            self._watch_after(call, audio, more_info)
            clip += more
            info = more_info | {
                "speech_s": round(len(clip) / (2 * RATE), 2),
                "voiced_s": round(float(info.get("voiced_s", 0.0)) + float(more_info.get("voiced_s", 0.0)), 2),
                "sustained_s": round(float(info.get("sustained_s", 0.0)) + float(more_info.get("sustained_s", 0.0)), 2),
                "peak": max(int(info.get("peak", 0)), int(more_info.get("peak", 0))),
            }
            text = await self._hear(call, clip, info, hint=hint)
        return text, named

    def _hold_reason(self, call: _Call, text: str, *, standby: bool = False) -> str | None:
        """Why an early end waits out the full pause after all (call_audio.HOLDS): its words would end the call (a
        goodbye, leave-taking, a wrap-up, asking her to hang up), or they end like more is coming (`goes_on`); in
        standby, her name alone ("her name": "Estia… what's on tomorrow?" has a pause after the name, and her wake line
        at once would come over the request); None: they stand. A hold only costs today's full pause."""
        if self._ends_call(call, text):
            return "ends the call"
        if standby and only_name(text, self.name, calling_words(call.language)):
            return "her name"
        return "goes on" if goes_on(text, call.language) else None

    @staticmethod
    def _no_turn(call: _Call) -> None:
        """The latest end held noise or no words: no turn for the end lines (call_audio.TurnEnd.noise)."""
        if call.turn_end is not None:
            call.turn_end.noise()

    def _watch_after(self, call: _Call, audio: CallAudio, info: Mapping[str, Any]) -> None:
        """After an early end, while Whisper and the answer work: the caller going on, sustained (as loud as the listen
        needs: max(4x floor, 450)) and heard as speech by the speech detector when the call has one, within what would
        have been the full pause was cut off by it (TurnEnd.went_on; CallAudio.watch). Over when she answers, at the
        next listen or in a hold."""
        if call.turn_end is None or info.get("ended") not in EARLY_KINDS:
            return
        rest = round((self.settings.end_of_turn_s - float(info.get("quiet_s", 0.0))) / FRAME_S)
        level = max(LISTEN_FLOOR_FACTOR * float(info.get("floor", 0.0)), LISTEN_MIN_RMS)
        audio.watch(rest, level, call.turn_end.went_on)

    @staticmethod
    def _taken_over(call: _Call, words: str, at_s: float | None) -> None:
        """The caller took over her answer to a turn `at_s` into it (after its lead): within EARLY_BARGE_S it counts
        against the end before it, early or full (TurnEnd.barged_early). Noise and her own voice are no takeover."""
        if call.turn_end is not None and at_s is not None and at_s <= EARLY_BARGE_S and real_words(words):
            call.turn_end.barged_early(at_s)

    def _call_over(self, call: _Call, text: str, turn: Turn, heard: str) -> str:
        """How the call ends once her answer `turn` to the caller's `text` is spoken whole (no takeover; `heard`: what
        the caller heard of it, code's lines too, never words a cap left unsaid: review of 26 Oct, DLG-01), as
        docs/agents/calls.md's one rule: not after a failed turn (the caller says it again, whatever it said); never
        while a device question waits for its yes (`turn.asks_yes`: her end_call, the caller asking her to hang up, a
        goodbye with more or leave-taking then waits for the answer, `call.ends_after_question`, and ends the call after
        it when the caller's reply only answers the question: `answers_question`; review 18 Oct; second fix review of
        0.9.8, redteam-calls-wrap-up-lookalike: her end_call came first and dropped the question); at once for her
        end_call; at once for the caller asking her to hang up, a goodbye with more or leave-taking, even when her
        answer asks something
        (those checks already leave out a turn that goes on with a question, "wait", "but" or "one more thing"); at once
        for a wrap-up unless her answer ends with a question mark; and AFTER_QUIET (goodbye_grace_s of quiet,
        `_after_goodbye`) when her answer ends with a plain goodbye or a closing wish and asks nothing
        (`ends_with_goodbye`), whatever the caller said ("I have a bad cold today." too: a wrong goodbye costs a few
        seconds, never the call; owner, 18 Oct: "if Gemma says good bye phrases, end_call should trigger"), on any call
        but a practice call, where a goodbye may be what is practised."""
        held, call.ends_after_question = call.ends_after_question, False
        if turn.failed:  # no answer to it: the caller says it again, whatever it said
            return ""
        asked_to_end = turn.end_call or wants_hang_up(text, name=self.name, language=call.language)
        asked_to_end = asked_to_end or self._leaving(call, text)
        if turn.asks_yes:  # a yes to a device must still count: the request waits for the answer
            call.ends_after_question = asked_to_end
            return ""
        if turn.end_call:
            return AT_ONCE
        if asked_to_end or (held and answers_question(text, name=self.name, language=call.language)):
            return AT_ONCE
        hers = heard
        if is_wrap_up(text, name=self.name, language=call.language):
            return "" if hers.endswith("?") else AT_ONCE
        if not call.practice and ends_with_goodbye(hers, name=self.name, language=call.language):
            return AFTER_QUIET
        return ""

    async def _after_goodbye(self, call: _Call, audio: CallAudio) -> str | None:
        """After her plain goodbye: a listen of goodbye_grace_s. The caller's words, when they go on (not a noise, a
        sound without words, a goodbye or leaving of their own, or only closing words in the call's language or
        English: `closes_only`), are the next turn; else None: the call ends."""
        s = self.settings
        log.info("calls: her goodbye; listening %.1f s before the end", s.goodbye_grace_s)
        audio.unwatch()
        audio.drain_incoming()  # the quiet counts from the end of her goodbye, never from frames queued while she spoke
        clip, info = await audio.listen(end_silence_s=s.end_of_turn_s, no_speech_s=s.goodbye_grace_s)
        if not clip or float(info.get("voiced_s", 99.0)) < MIN_VOICED_S:
            return None
        try:
            text = await self._hear(call, clip, info)
        except VoiceError:
            return None  # heard nothing usable: both said goodbye, the call ends
        if is_noise_transcript(text, float(info.get("sustained_s", 99.0))) or not real_words(text):
            return None
        if self._ends_call(call, text) or closes_only(text, name=self.name, language=call.language):
            log.info("calls: the caller said goodbye too")
            return None
        log.info("calls: the caller went on after her goodbye (%d words)", turn_words(text))
        return text

    def _ends_call(self, call: _Call, text: str) -> bool:
        """The caller's words end the call: a goodbye, leave-taking, a wrap-up or asking her to hang up."""
        return (
            is_goodbye(text, name=self.name)
            or self._leaving(call, text)
            or is_wrap_up(text, name=self.name, language=call.language)
            or wants_hang_up(text, name=self.name, language=call.language)
        )

    def _leaving(self, call: _Call, text: str) -> bool:
        """The user said goodbye with more than the goodbye, or takes leave: she hangs up once her reply is spoken."""
        return says_goodbye(text, name=self.name, language=call.language) or takes_leave(
            text, name=self.name, language=call.language
        )

    async def _turn(
        self, call: _Call, audio: CallAudio, text: str, *, acknowledge: bool, on_retry: bool = False, woke: bool = False
    ) -> Turn:
        """The app's answer. A short "Got it." first when `acknowledge` (the request was taken in); else, when the
        turn ended standby (`woke`), her wake line ("Yes?") once the answer is not ready after WAKE_LINE_AFTER_S. Or with
        `on_retry` as soon as the model's first try came back empty and it is asked again (the caller is not left in
        silence; it counts as this turn's, so the next request gets none), unless a thinking or checking line came
        before it. A varied thinking line when the answer takes a moment after that (then a short pause before the
        answer), a "let me check" line when a tool runs (each at most once). An answer that takes too long, outlives
        the call or is left by an error is cancelled (never left running beside the next turn or after the call) and
        any device question it raised is dropped; when the call ended meanwhile, the user gets a note that it was not
        done."""
        s = self.settings
        tools = asyncio.Event()
        retry = asyncio.Event()
        task = asyncio.create_task(
            self._host.call_turn(
                call.aci, text, tools.set, practice=call.practice, on_retry=retry.set, text_only=call.text_only
            )
        )
        ended = asyncio.create_task(call.ended.wait())
        # without fillers (and on a text-only call) she simply answers when the answer is ready
        thought = checked = not s.fillers or call.text_only
        filled = False  # a thinking or checking line was said: no acknowledgement for a retry after it
        line_end: float | None = None
        started = think_from = self._clock()
        lines = call.spoken
        try:
            if acknowledge:  # "Got it." while the answer is already being worked on: the request was taken in
                ack = self._pick_acknowledgement(lines.acknowledge)
                await self._say_line(audio, ack, call.language, watch=True)  # the caller may go on over it
                call.acknowledged_last = not self.unsaid_line  # only one heard counts (the review of 4 Nov)
                line_end = think_from = self._clock()  # a thinking line only after a real wait beyond it
            elif woke and not (await asyncio.wait({task}, timeout=WAKE_LINE_AFTER_S))[0]:  # she woke: she says so
                on_retry = False  # a line at the turn's start: no acknowledgement for a retry after it
                log.info("calls: standby over; her wake line while the answer is worked out")
                # one that asks nothing ("I'm here."): "Yes?" would ask for what was just said
                woken = tuple(line for line in lines.woken if not asks([line])) or lines.woken
                await self._say_line(audio, self._pick(woken), call.language, watch=True)
                line_end = think_from = self._clock()
            while not task.done():
                now = self._clock()
                if now - started >= s.turn_timeout_s:
                    log.warning("calls: an answer took over %.0f s", s.turn_timeout_s)
                    call.timeouts += 1  # work stopped by the limit (an answer clears it): its own note after the call
                    await self._abandon(call, task)
                    # a second in a row: asking to hear it again would only loop, so she asks for a text
                    return Turn(lines.too_long if call.timeouts > 1 else lines.lost, failed=True)
                if on_retry and retry.is_set():  # asked again: the acknowledgement now, while the retry runs
                    on_retry = False
                    if not filled:  # after "Let me think." or "Let me check." it would come in the wrong order
                        call.acknowledged_last = True
                        log.info("calls: an acknowledgement while an empty answer is asked again")
                        ack = self._pick_acknowledgement(lines.acknowledge)
                        await self._say_line(audio, ack, call.language, watch=True)
                        call.acknowledged_last = not self.unsaid_line
                        line_end = think_from = self._clock()
                        continue
                check = tools.is_set() and not checked
                think = not thought and not checked and now - think_from >= s.thinking_after_s
                if check or think:
                    checked = checked or check
                    thought = filled = True
                    line = self._pick(lines.checking if check else lines.thinking)
                    await self._say_line(audio, line, call.language, watch=True)
                    line_end = self._clock()
                    continue
                waits: set[asyncio.Future[Any]] = {task, ended}
                waiters: list[asyncio.Task[bool]] = []
                if not checked and not tools.is_set():
                    waiters.append(asyncio.create_task(tools.wait()))
                if on_retry:
                    waiters.append(asyncio.create_task(retry.wait()))
                waits.update(waiters)
                limit = started + s.turn_timeout_s - now if thought else think_from + s.thinking_after_s - now
                await asyncio.wait(waits, timeout=max(0.0, limit), return_when=asyncio.FIRST_COMPLETED)
                for waiter in waiters:
                    waiter.cancel()
                if task.done():
                    break
                if call.ended.is_set():
                    raise _Ended("ended while answering")
        except BaseException:  # the call ended, the voice failed, or the call is being stopped
            if not task.done():
                call.unfinished = True
                await self._abandon(call, task)
            raise
        finally:
            ended.cancel()
        call.timeouts = 0  # an answer came, or failed in time: no time-out in a row any more
        if line_end is not None:
            gap = PAUSE_AFTER_FILLER_S - (self._clock() - line_end)
            if gap > 0:
                await asyncio.sleep(gap)
        try:
            return task.result()
        except Exception:
            log.exception("calls: the answer failed")
            await self._host.call_drop_waiting(call.aci)
            return Turn(lines.lost, failed=True)

    def _pick(self, lines: tuple[str, ...], *, last: str | None = None) -> str:
        """A line from `lines`, never the one said last (or never `last`, for lines with their own memory)."""
        avoid = self._last_line if last is None else last
        choices = [line for line in lines if line != avoid] or list(lines)
        picked = self._rng.choice(choices)
        if last is None:
            self._last_line = picked
        return picked

    def _pick_acknowledgement(self, lines: tuple[str, ...]) -> str:
        """A "got it" that is neither of the last two said (fewer lines: not the last one), kept across calls."""
        recent = self._recent_acknowledgements
        choices = [line for line in lines if line not in recent] or [line for line in lines if line not in recent[-1:]]
        picked = self._rng.choice(choices or list(lines))
        self._recent_acknowledgements = [*recent[-1:], picked]
        return picked

    async def _abandon(self, call: _Call, task: asyncio.Task[Turn]) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        await self._host.call_drop_waiting(call.aci)

    async def _clip(self, text: str, language: str, *, keep: bool = True) -> bytes:
        """A canned line at 48 kHz, kept for next time in the voice that says it now (the review of 24 Oct: kept by
        language and text alone, `/voice michael` left them eve's until the bridge restarted; jane's in the six
        languages Pocket TTS speaks, whatever the English voice). One Pocket TTS cannot say is VoiceNotSaidError and is
        asked again next time (no other voice says it: owner, 3 Nov 2026). At most 32 kept; a full cache drops another
        voice's lines first."""
        voice = self._voice.voice_for(language)
        key = (voice, language, text)
        clip = self._clips.get(key)
        if clip is None:
            clip = wav_to_pcm48(await self._voice.speak(text, language=language, call=True))
            if len(self._clips) >= 32:
                self._clips = {kept: line for kept, line in self._clips.items() if kept[0] == voice}
            if keep and len(self._clips) < 32:
                self._clips[key] = clip
        return clip

    async def _say_line(
        self,
        audio: CallAudio,
        text: str,
        language: str,
        *,
        barge: bool = False,
        keep: bool = True,
        watch: bool = False,
        made: bytes | None = None,
        greeted: bool = False,
    ) -> str | None:
        """A code-written line in `language`, spoken whole; with barge-in it returns what the caller said over it.
        `keep`: the clip may be kept for next time (canned lines only, never one with the user's words). `watch`: a line
        while her answer is worked out, so the watch after an early end goes on (`CallAudio.say`). `made`: its speech,
        made ahead. `greeted`: the opening of a call she placed, which a greeting alone over it never stops
        (`only_greeting`). A line Pocket TTS cannot say is left unsaid and the call goes on (owner, 3 Nov 2026: no other
        voice says it; `unsaid_line` says so): after a "Got it." or a thinking line left unsaid her answer is still
        tried (the review of 28 Oct, pipeline-01) and the next request may have its "Got it.", after a greeting left
        unsaid the caller's next words are answered as ever, and a check-in left unsaid starts no standby (`_converse`
        tries it before standby: the review of 4 Nov)."""
        self.saying = text
        self.unsaid_line = False
        try:
            clip = made if made is not None else await self._clip(text, language, keep=keep)
        except VoiceNotSaidError:
            log.info("calls: Pocket TTS could not say her line; not said")
            self.saying = ""
            self.unsaid_line = True
            return None
        lead = faint_noise(LEAD_MS)
        result = await self._say_checked(
            audio, lead + clip, barge, hers=(text,), watch=watch, lead=len(lead), greeted=greeted
        )
        self._done(text)
        return result[0] if result else None

    def _done(self, text: str) -> None:
        self.said = [*self.said[-19:], text]
        self.saying = ""

    @staticmethod
    def _waited(call: _Call, words_at: float, said: Sequence[str]) -> None:
        """Her answer to the caller's latest turn starts: its wait, logged (metadata, review of 26 Oct, rtcall-2: speech
        starts only once the whole answer is written, so the wait grows with it) and kept for the end line; her first
        sound (a "Got it.", a thinking line) and her answer's first words apart, the faint lead before each left out."""
        assert call.turn_ended_at is not None
        first = words_at if call.first_sound_at is None else min(call.first_sound_at, words_at)
        words_ms = round((words_at - call.turn_ended_at) * 1000)
        count = len(" ".join(said).split())
        log.info(
            "calls: first audio %d ms after the end of the turn (her answer's first words %d ms, %d words)",
            round((first - call.turn_ended_at) * 1000),
            words_ms,
            count,
        )
        call.waits.append((words_ms, count))
        call.turn_ended_at = call.first_sound_at = None

    async def _speak(self, call: _Call, audio: CallAudio, turn: Turn, *, waiting: bool) -> _Spoke:
        """Gemma's answer sentence by sentence (the next one is made while one plays), then code's own words: in
        English, so a device question is always clear (on an English call they simply follow). On a call in another
        language a turn that waits for a device's yes says only code's question: Gemma's words about the device are left
        out (in Dutch it claimed a switch that was still waiting for the yes), and app.call_turn keeps only that
        question in the history too; a turn whose reminder was not set comes with no words of hers at all, only code's
        line why. `waiting`: a device question waits for the yes (turn.asks_yes): its last sentence is said whole, and a
        yes word over the ones before answers it. Returns what the caller said over it (or None), what they heard of it,
        the part of that her stored reply keeps (her words and `turn.kept_code`, never code's other lines), and how far
        into her answer (after the lead) they took over (or None). Left unsaid (the call ends or her speech fails), the
        stored reply keeps what was said, as after a cut-in (review 0.9.7, C8). A part Pocket TTS cannot say stops her
        voice for the turn, and the rest goes as a text (`_texted_rest`; owner, 3 Nov 2026: no other voice says it). On
        a turn that waits for a yes, her parts before the question go as a text from the first Pocket TTS cannot say,
        and the question, last, is still tried (the review of 28 Oct, words-01: an unheard question drops its yes)."""
        her_parts = [] if call.language != ENGLISH and turn.asks_yes and turn.code_text.strip() else _parts(turn.text)
        kept = {part for line in turn.kept_code for part in _parts(line)}
        # each sentence, its language, whether it is hers (code's words go with no words of hers: below) and whether
        # her stored reply keeps it
        said = [(part, call.language, True, True) for part in her_parts]
        said += [(part, ENGLISH, False, part in kept) for part in _parts(turn.code_text)]
        parts = [part for part, _, _, _ in said]
        if not parts:
            return _Spoke(None, "", "", None)
        last = len(parts) - 1  # a device question is the last part (said whole)
        for_question = turn.asks_yes and last > 0
        # a part's speech; None: her voice stops there for the rest of the turn (Pocket TTS could not say it);
        # _SKIPPED: the parts from there to the question go as a text, and the question comes next
        clips: asyncio.Queue[bytes | VoiceError | _Skip | None] = asyncio.Queue(maxsize=1)
        skipped: set[int] = set()  # the parts before the question that went as a text

        async def produce() -> None:
            index = 0
            while index < len(said):
                part, language, own, _ = said[index]
                # her answer's sentences before this one, in its language: the voice service reads them for a time's
                # part of the day only ("at 7:30" two sentences after "tonight" is no morning); code's words go with
                # none (its reminder line's "07:00" after "tonight" stays the morning: the review of 24 Oct)
                before = " ".join(earlier for earlier, spoken, mine, _ in said[:index] if mine and spoken == language)
                before = before if own else ""
                try:
                    spoken = await self._voice.speak(part, language=language, context=before, call=True)
                    clip: bytes | None = wav_to_pcm48(spoken)
                except VoiceNotSaidError:  # Pocket TTS could not say it, and no other voice does
                    clip = None
                except VoiceError as exc:
                    await clips.put(exc)
                    return
                except Exception as exc:  # e.g. a body that is not a WAV: the call must not wait forever
                    await clips.put(VoiceError(f"speech failed: {type(exc).__name__}"))
                    return
                if clip is None and for_question and index < last:  # her words go as a text; the question is tried
                    await clips.put(_SKIPPED)
                    index = last
                    continue
                if clip is None:
                    await clips.put(None)  # her voice stops here: nothing more of this turn is asked
                    return
                await clips.put(clip)
                index += 1

        def cut_at(index: int, fraction: float) -> tuple[str, str]:
            """What the caller heard (code's lines too) and what her stored reply keeps, up to `fraction` of part
            `index` (the parts before it whole, but those that went as a text)."""
            part = parts[index]
            cut = part[: int(len(part) * fraction)].rsplit(" ", 1)[0] if fraction < 1 else part
            before = [at for at in range(index) if at not in skipped]
            heard = " ".join([*(parts[at] for at in before), cut]).strip()
            stored = [said[at][0] for at in before if said[at][3]] + [cut] * said[index][3]
            return f"{heard} …".strip(), f"{' '.join(stored).strip()} …".strip()

        producer = asyncio.create_task(produce())
        played_s = 0.0  # her answer's sentences before this one, whole, in seconds
        index = 0
        first = True  # the first part said: its faint lead, her wait and a fresh listen
        try:
            while index < len(parts):
                item = await self._next_clip(clips, audio)
                if isinstance(item, VoiceError):
                    raise item
                if item is None:  # Pocket TTS could not say this part: never said, the rest goes as a text
                    return await self._texted_rest(call, turn, said, index, skipped=skipped)
                if isinstance(item, _Skip):  # her words before the question go as a text; the question comes next
                    skipped.update(range(index, last))
                    await self._text_before_question(call, turn, said, index)
                    index = last
                    continue
                part = parts[index]
                self.saying = part
                lead = faint_noise(LEAD_MS) if first else b""
                if first and call.turn_ended_at is not None:
                    self._waited(call, self._clock() + len(lead) / (2 * RATE), her_parts or parts)
                whole = waiting and index == len(parts) - 1
                near = [at for at in (index - 1, index) if at >= 0 and at not in skipped]
                hers = tuple(parts[at] for at in near)  # the sentence and the one before it
                # of those, the ones among her answer's last two parts: a question she talked on from was rhetorical
                # (review of 26 Oct, DLG-04: a full answer may ask on its way, and a "はい" after it took over)
                asking = tuple(parts[at] for at in near if at >= len(parts) - 2)
                result = await self._say_checked(
                    audio,
                    lead + item,
                    barge=not whole,
                    fresh=first,
                    hers=hers,
                    asking_parts=asking,
                    waiting=waiting,
                    lead=len(lead),
                )
                first = False
                self._done(part)
                if result is not None:
                    text, fraction, stop_s = result
                    heard, stored = cut_at(index, fraction)
                    at = max(0.0, played_s + stop_s - len(lead) / (2 * RATE))
                    return _Spoke(text, heard, stored, at)
                played_s += len(item) / (2 * RATE)
                index += 1
            stored = " ".join(part for at, (part, _, _, keep) in enumerate(said) if keep and at not in skipped)
            if turn.reply_id is not None and (skipped or (her_parts and not said_whole(turn.text))):
                # CALL_SPOKEN_CHARS cut her answer, or her words before the question went as a text: the stored reply
                # keeps what was said, as after a cut-in (review of 26 Oct, DLG-01: the unsaid end stayed in her history)
                self._host.call_interrupted(turn.reply_id, stored)
            return _Spoke(None, " ".join(part for at, part in enumerate(parts) if at not in skipped), stored, None)
        except BaseException as exc:  # left unsaid: the call ended, her speech failed, the call is being stopped
            if turn.reply_id is not None:
                _, stored = cut_at(index, exc.played if isinstance(exc, _LineClosed) else 0.0)
                self._host.call_interrupted(turn.reply_id, stored)
            raise
        finally:
            producer.cancel()
            self.saying = ""

    async def _texted_rest(
        self,
        call: _Call,
        turn: Turn,
        said: Sequence[tuple[str, str, bool, bool]],
        index: int,
        *,
        skipped: Collection[int] = (),
    ) -> _Spoke:
        """Her voice stopped at part `index` of her answer (Pocket TTS could not say it; owner, 28 Oct and 3 Nov): the
        parts not said, whole, go to the caller as a text after code's line (`_text_rest`), her words and the code lines
        her reply keeps, never one that goes as a text of its own (`Turn.also_texted`) nor a device question
        (`turn.asks_yes`, the last part: never heard, so its yes is dropped and a yes to it in writing could switch
        nothing; the review of 28 Oct, pipeline-04); `skipped`: parts that went as a text already. Code's lines alone go
        as themselves, never led by "the rest of my answer", and a failed turn's line ("lost my train of thought") as
        no text at all: the caller says it again (the review of 4 Nov). Her stored reply keeps what was heard ("…" when
        nothing was). When none of her answer was said, her wait until her voice stopped is logged (metadata; the
        review of 28 Oct, pipeline-06). The call stays up when something new went as a text."""
        question = len(said) - 1 if turn.asks_yes else None
        heard = " ".join(part for at, (part, _, _, _) in enumerate(said[:index]) if at not in skipped)
        stored = " ".join(part for at, (part, _, _, keep) in enumerate(said[:index]) if keep and at not in skipped)
        rest = [
            (part, own)
            for at, (part, _, own, _) in enumerate(said)
            if at >= index and at not in skipped and at != question
        ]
        sent = [] if turn.failed else self._texted(turn, rest)
        hers = any(own for part, own in rest if part in sent)
        if call.turn_ended_at is not None:  # no word of her answer came: the time she left the caller waiting
            log.warning(
                "calls: her voice stopped %d ms after the end of the turn, before her answer's first words; %d of %d"
                " parts go as a text",
                round((self._clock() - call.turn_ended_at) * 1000),
                len(sent),
                len(said),
            )
            call.stopped_before_words += 1
            call.turn_ended_at = call.first_sound_at = None
        else:
            log.warning(
                "calls: Pocket TTS could not say a part of her answer; her voice stops for the turn, %d of %d parts go"
                " as a text",
                len(sent),
                len(said),
            )
        if turn.reply_id is not None:
            self._host.call_interrupted(turn.reply_id, stored or "…")
        await self._text_rest(call, turn, sent, hers=hers)
        return _Spoke(None, heard, stored, None, stopped=True, texted=bool(sent))

    async def _text_before_question(
        self, call: _Call, turn: Turn, said: Sequence[tuple[str, str, bool, bool]], index: int
    ) -> None:
        """Pocket TTS could not say part `index`, before a device question (`_speak`): the parts from there to the
        question go as a text, after code's line when it holds any of her words, else code's own lines as themselves
        (`_text_rest`; the review of 5 Nov, CALLS-0983-02); the question is tried next."""
        rest = [(part, own) for part, _, own, _ in said[index:-1]]
        sent = self._texted(turn, rest)
        log.warning(
            "calls: Pocket TTS could not say her words before a device question; %d of %d parts go as a text, the"
            " question is tried",
            len(sent),
            len(said),
        )
        await self._text_rest(call, turn, sent, hers=any(own for part, own in rest if part in sent))

    @staticmethod
    def _texted(turn: Turn, rest: Sequence[tuple[str, bool]]) -> list[str]:
        """Of the parts `rest` (each with whether it is hers), the ones a text holds: hers, and code's lines but those
        that go as a text of their own (`Turn.also_texted`), so nothing comes twice."""
        texted = {part for line in turn.also_texted for part in _parts(line)}
        return [part for part, own in rest if own or part not in texted]

    async def _text_rest(self, call: _Call, turn: Turn, rest: Sequence[str], *, hers: bool = True) -> None:
        """`rest`, whole, to the caller as a text after code's line (`prompts.CALL_VOICE_STOPPED`; code's note, so it
        is in her history in these words: rule 6), without that line when it holds none of her words (`hers`: code's
        own lines go as themselves); the call goes on when it cannot be stored. On a practice call a line with no reply
        of hers is stored in the practice conversation (the review of 5 Nov, CALLS-0983-03)."""
        if not rest:
            return
        text = f"{prompts.CALL_VOICE_STOPPED}\n{' '.join(rest)}" if hers else " ".join(rest)
        try:
            await self._host.call_text(
                call.aci, text, reply_id=turn.reply_id, outside=turn.outside, practice=call.practice
            )
        except Exception:  # the call goes on; the log says it, never the text
            log.exception("calls: the rest of her answer could not be stored to go as a text")

    @staticmethod
    async def _next_clip[T](clips: asyncio.Queue[T], audio: CallAudio) -> T:
        while True:
            try:
                return await asyncio.wait_for(clips.get(), 1.0)
            except TimeoutError:
                if audio.closed.is_set():
                    raise ConnectionError("the call's audio ended") from None

    async def _cut_in_rest(self, audio: CallAudio, clip: bytes, text: str, language: str) -> str:
        """A cut-in whose words go on ("Wait, I meant," `goes_on`): its listen ended after INTERRUPTION_END_S, so the
        rest of the full pause (end_of_turn_s) is listened for, as a turn's hold does; what comes is joined to it and
        heard whole, and checked again, up to HOLD_MAX_S of audio (review 0.9.7, C10: the rest was thrown away when her
        next answer started). Returns the cut-in's words."""
        rest = self.settings.end_of_turn_s - INTERRUPTION_END_S
        while rest > 0 and goes_on(text, language) and (room := HOLD_MAX_S - len(clip) / (2 * RATE)) >= 1.0:
            more, _ = await audio.listen(end_silence_s=self.settings.end_of_turn_s, no_speech_s=rest, max_s=room)
            if not more:
                break
            if self._call is not None:  # the cut-in went on: its turn ends here, and her wait counts from here
                self._call.turn_ended_at = self._clock()
            clip += more
            try:
                text = (await self._voice.transcribe(pcm48_to_wav16k(clip), language=language, call=True)).text
            except VoiceError:
                break  # the words heard before stand
            log.info("calls: the cut-in went on; heard whole (%d words)", turn_words(text))
        return text

    async def _say_checked(
        self,
        audio: CallAudio,
        pcm: bytes,
        barge: bool,
        *,
        fresh: bool = True,
        hers: tuple[str, ...] = (),
        asking_parts: tuple[str, ...] | None = None,
        waiting: bool = False,
        watch: bool = False,
        lead: int = 0,
        greeted: bool = False,
    ) -> tuple[str, float, float] | None:
        """Say `pcm` (her sentences `hers`: the one before and the one being said); when the caller talks over it,
        listen and check the words: an interruption returns (what they said, how much of the clip went out, and the
        stop's place in the clip in seconds); a cough,
        a noise or her own voice (compared with what she played of `hers` up to the stop) resumes a little before the
        stop. Her own voice also raises the level a stop needs, once a call (call_audio.Barge.her_own_voice); it never
        reaches Gemma. A listener's acknowledgement alone ("okay", "mm-hm") resumes too, unless `asking_parts` (those of
        `hers` that may still wait for an answer: among her answer's last two parts; None: all of `hers`) asks something
        or a device question is `waiting`: then it answers her (owner, 14 Oct; review of 26 Oct, DLG-04). Each acknowledgement takes her further
        on: a little before the stop, never back to where she resumed (a stop that soon would replay the same audio
        for good), and after MAX_FALSE_BARGE of them in one clip from the stop itself. `watch` (without barge-in): the
        watch after an early end goes on over it (`CallAudio.say`). `lead`: the bytes of faint noise before her words,
        never counted as heard (review 0.9.7, C8). `greeted`: the opening of a call she placed, where greeting or
        calling words alone ("Hello?") resume it like an acknowledgement (`only_greeting`). The line closing before the
        clip went out whole raises _LineClosed with how much of it went out."""
        asking = waiting or asks(hers if asking_parts is None else asking_parts)
        if self._call is not None:  # her latest words: whether they ask something (_Call.asked)
            self._call.asked = asking
            if self._call.turn_ended_at is not None and self._call.first_sound_at is None:  # her first sound (metadata)
                self._call.first_sound_at = self._clock() + lead / (2 * RATE)
        offset = 0
        false_alarms = 0
        carried_on = 0  # acknowledgements in this clip
        resumed = False
        while True:
            said = await audio.say(pcm[offset:], barge=barge, fresh=fresh and not resumed, watch=watch and not barge)
            stop = offset + 2 * int(said.played_s * RATE)
            fraction = min(1.0, max(0, stop - lead) / max(1, len(pcm) - lead))  # of her words, the lead left out
            if said.closed:
                raise _LineClosed(fraction)
            if not said.barged:
                return None
            clip, info = await audio.listen(
                preroll=said.preroll, floor=said.floor, end_silence_s=INTERRUPTION_END_S, max_s=60.0
            )
            if self._call is not None:  # a cut-in's turn ends here: her wait for its answer counts from here
                self._call.turn_ended_at, self._call.first_sound_at = self._clock(), None
            try:
                language = self._call.language if self._call is not None else self.language
                wav = pcm48_to_wav16k(clip)
                text = (await self._voice.transcribe(wav, language=language, call=True)).text if clip else ""
            except VoiceError:
                text = ""
            noise = is_noise_transcript(text, float(info.get("sustained_s", 0.0)))  # typing heard as "*music*"
            own = not noise and is_own_voice(text, played_part(hers, fraction))
            if own:
                audio.barge.her_own_voice(said.level)
            words = not noise and not own
            acknowledged = words and not asking and is_acknowledgement(text, language)
            greeting = words and greeted and not acknowledged and only_greeting(text, name=self.name, language=language)
            caller = (
                words
                and not acknowledged
                and not greeting
                and is_interruption(text, name=self.name, language=language, asking=asking)
            )
            resumed = True
            # words: no false stop, and never a takeover however often, but always further on
            if acknowledged or greeting:
                if greeting:
                    log.info("calls: a greeting over her opening; carrying on")
                else:
                    audio.barge.acknowledgement()
                    log.info("calls: an acknowledgement; carrying on")
                carried_on += 1
                back = stop - 2 * int(RESUME_BACK_S * RATE)
                offset = back if back > offset and carried_on <= MAX_FALSE_BARGE else stop
                continue
            if not caller:
                audio.barge.false_stop(said)  # no words or her own voice: the shadow that fired too would have erred
            if caller or false_alarms >= MAX_FALSE_BARGE:
                taken = "" if noise or own else text  # noise and her own words never reach Gemma
                if real_words(taken):
                    log.info("calls: taken over (%d words)", turn_words(taken))
                    audio.barge.caller_took_over()  # a raise for her own voice was wrong or is lowered again
                    taken = await self._cut_in_rest(audio, clip, taken, language)
                return taken, fraction, stop / (2 * RATE)
            false_alarms += 1
            log.info("calls: her own voice heard; carrying on" if own else "calls: a sound without words; carrying on")
            offset = max(0, stop - 2 * int(RESUME_BACK_S * RATE))

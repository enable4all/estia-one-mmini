"""Her voice in a language while jane's model of it downloads (owner, 25 Oct: "why use supersonic
while downloading pocket? lets make it clear via a msg, that language is downloading, we need 3 minutes").

jane speaks Spanish, Italian, Portuguese, Dutch, German and French in Pocket TTS, each language's own model (about
227 MB), which the voice service fetches only for a language in use (`/language`, `/babble`) at the first line asked in
it: the warm-up's (`LanguageDownload.warm_up`, one short sentence thrown away; App's warm-up until now). Until it is
ready nothing speaks that language (owner, 3 Nov 2026: Supertonic-3 removed, no other voice says a line); while the
model downloads the bridge asks it for none:
- `/language`'s reply says so ("German is on. Her German voice is downloading: about 3 minutes. Until then, her answers
  come as text."), and so does a line of `/babble`'s;
- a voice note's answer comes as text, with that note (`voice_note`);
- a call in that language rings out (never rejected) and the caller gets a text that says when to call again
  (`call_note`); her own calls for a reminder are not placed meanwhile (its text says so);
- once the voice service says the model is ready, each account told gets one note, "Her German voice is ready.";
- a download that fails gets its note (`prompts.VOICE_DOWNLOAD_FAILED`): her answers in that language come as text,
  and a later try that works (the voice service's, at a line in it after its wait) gets the ready note.
A line Pocket TTS fails once the voice is ready is said by no other voice either: a voice note's answer stands as text.

A download is one only when a file of the language's model is not in the voice service's models folder and the voice
service's health has the language's Pocket state (`pocket_<code>`) idle or loading: a model already there is only
checked at its first line (about 0.2 s). A model whose files changed is set aside and fetched again by the voice
service, with no voice in that language meanwhile and no note (a known gap). The notes are code's (in her history as
code_notes); logs name the language and the state, never an account.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from local_assistant import prompts
from local_assistant.config import has_voice
from local_assistant.language import ENGLISH, LANGUAGES
from local_assistant.voice import POCKET_LANGUAGE_FILES, VoiceNotSaidError

log = logging.getLogger(__name__)

DOWNLOAD_MINUTES = 3  # the owner's: "we need 3 minutes" (about 227 MB; 28-39 s here, 25 Oct)
POLL_SECONDS = 5.0  # the voice service's health while a download runs
FAILED_POLL_SECONDS = 60.0  # after a failed one, while its language stays in use: a later try that works
# while one of the six is in use with no download: an idle model (the voice service restarted, or it rested after
# failed runs) is warmed up again (the review of 4 Nov); the first look a minute after the start, which warms it itself
IDLE_POLL_SECONDS = 60.0
DECIDE_SECONDS = 5.0  # /language's and /babble's reply wait at most this long for the warm-up's first read
WAITING = ("idle", "loading")  # a language's Pocket states a download waits through


class Voice(Protocol):
    """The voice client's side this needs (voice.VoiceClient)."""

    async def health(self) -> Mapping[str, object] | None: ...

    async def health_when_up(self) -> Mapping[str, object] | None: ...

    async def speak(self, text: str, *, language: str | None = None, context: str = "") -> bytes: ...


Send = Callable[[str, str], Awaitable[None]]  # (account, text): stored and sent as code's note


@dataclass
class _Download:
    started: float
    told: set[str] = field(default_factory=set)  # the accounts a note said it downloads: each gets the next one
    failed: bool = False  # its failure was told: no voice in the language until a later try works
    read_at: float = 0.0  # the last health read after the failure


class LanguageDownload:
    def __init__(
        self,
        voice: Voice | None,
        models: Path,
        send: Send,
        *,
        in_use: Callable[[str], bool],
        locked: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`models`: the voice service's models folder (in her home); `in_use`: whether a language is still the
        one in use or practised (a download of one that is not any more is dropped, without a note); `locked`: /lock
        (notes wait for /unlock)."""
        self._voice = voice
        self._models = models
        self._send = send
        self._in_use = in_use
        self._locked = locked
        self._clock = clock
        self._sleep = sleep
        self._downloads: dict[str, _Download] = {}
        self._decided: dict[str, asyncio.Event] = {}  # set once a language's latest warm-up read the health
        self._tasks: set[asyncio.Task[None]] = set()
        self._idle_read_at = clock()  # the last look for an idle model of a language in use (`_wake_idle`)

    def warm_up(self, language: str) -> None:
        """One short sentence in `language` (not English), thrown away, in the background: its voice loads on first use
        (about a minute the very first time; in jane's six languages her Pocket TTS model of it, only the language in
        use). A voice service not up yet is asked again for about 5 minutes (a boot; the review of 25 Oct). Its first
        read decides whether jane's model downloads (the replies wait for it). Failures are only logged. None in a
        language her voice does not speak (`config.has_voice`; owner, 3 Nov 2026)."""
        if self._voice is None or language == ENGLISH or not has_voice(language):
            return
        decided = asyncio.Event()
        self._decided[language] = decided
        task = asyncio.create_task(self._warm_up(language, decided), name=f"voice-warm-up-{language}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def idle(self) -> None:
        """Every warm-up started so far has run."""
        if self._tasks:
            await asyncio.wait(set(self._tasks))

    async def wake(self, language: str, health: Mapping[str, object] | None) -> None:
        """Before a call in `language` is picked up (calls.CallManager): jane's model of it idle in the voice service's
        health (it restarted on its own, or the model rested after failed runs) gets the warm-up's line at once, so it
        loads while the call rings, and the call waits at most DECIDE_SECONDS for its first read: files missing then
        make a download, and the call rings out with its note (`call_note`). The review of 4 Nov: the call was picked up
        and its first answer went as text while the model only then loaded."""
        if language not in POCKET_LANGUAGE_FILES or health is None or health.get(f"pocket_{language}") != "idle":
            return
        log.info("voice warm-up (%s): its model is idle as a call rings; warming it up", language)
        self.warm_up(language)
        decided = self._decided.get(language)
        if decided is not None:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(DECIDE_SECONDS):
                    await decided.wait()

    def downloading(self, language: str) -> bool:
        """Whether jane's model of `language` downloads now: nothing in it is spoken meanwhile."""
        found = self._downloads.get(language)
        return found is not None and not found.failed

    async def reply(self, language: str, aci: str) -> str:
        """/language's reply while jane's model of the language just chosen downloads ("" when it does not): `aci` gets
        the ready note."""
        if not await self._decide(language, aci):
            return ""
        name = LANGUAGES[language].english
        return prompts.LANGUAGE_ON.format(language=name) + self._text(
            language, prompts.VOICE_DOWNLOADING, prompts.VOICE_STILL_DOWNLOADING
        )

    async def note(self, language: str, aci: str) -> str:
        """/babble's line while jane's model of the practised language downloads ("" when it does not)."""
        if not await self._decide(language, aci):
            return ""
        return self._text(language, prompts.VOICE_DOWNLOADING, prompts.VOICE_STILL_DOWNLOADING)

    def voice_note(self, aci: str, language: str) -> str:
        """The note in a spoken answer's place while jane's model of `language` downloads ("" when it does not)."""
        if not self._tell(aci, language):
            return ""
        return self._text(language, prompts.VOICE_DOWNLOADING, prompts.VOICE_STILL_DOWNLOADING)

    def call_note(self, aci: str, language: str) -> str:
        """The text for a call that rings out while jane's model of its language downloads ("" when it does not)."""
        if not self._tell(aci, language):
            return ""
        return self._text(language, prompts.CALL_DOWNLOADING, prompts.CALL_STILL_DOWNLOADING)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:  # a TaskGroup task that raises would take the bridge down
                log.exception("voice download: the check failed")
            await self._sleep(POLL_SECONDS)

    async def check(self) -> None:
        """One look at each download: ready (the note to each account told), failed (its note, then a look every
        FAILED_POLL_SECONDS for a later try that works), idle again (a restart of the voice service: a line in it
        starts the fetch again), or its language no longer in use (dropped, no note); then, at most every
        IDLE_POLL_SECONDS, an idle model of a language in use with no download (`_wake_idle`)."""
        voice = self._voice
        if voice is None:
            return
        await self._downloads_check(voice)
        await self._wake_idle(voice)

    async def _wake_idle(self, voice: Voice) -> None:
        """jane's model of a language in use (or practised), with no download under way, idle in the voice service's
        health: the warm-up's line, so a voice note's answer finds it loaded (the review of 4 Nov: after the voice
        service restarted on its own, or a language's model rested after failed runs, nothing warmed it up again and
        the next answer went as text). One health read at most every IDLE_POLL_SECONDS, none while no such language
        is in use."""
        waiting = [code for code in POCKET_LANGUAGE_FILES if self._in_use(code) and code not in self._downloads]
        now = self._clock()
        if not waiting or now - self._idle_read_at < IDLE_POLL_SECONDS:
            return
        self._idle_read_at = now
        health = await voice.health()
        if health is None:
            return
        for language in waiting:
            if health.get(f"pocket_{language}") == "idle":
                log.info("voice warm-up (%s): its model is idle again (a restart or a rest); warming it up", language)
                self.warm_up(language)

    async def _downloads_check(self, voice: Voice) -> None:
        for language, found in list(self._downloads.items()):
            if not self._in_use(language):
                self._downloads.pop(language, None)
                log.info("voice download (%s): not in use any more; no note", language)
                continue
            now = self._clock()
            if found.failed and now - found.read_at < FAILED_POLL_SECONDS:
                continue
            found.read_at = now
            health = await voice.health()
            if health is None:  # not answering (a restart): read again at the next look
                continue
            name = LANGUAGES[language].english
            state = health.get(f"pocket_{language}")
            if state == "ready":
                if await self._tell_all(found, prompts.VOICE_LANGUAGE_READY.format(language=name)):
                    if self._downloads.get(language) is found:
                        del self._downloads[language]
                    log.info("voice download (%s): ready (%d told)", language, len(found.told))
            elif not found.failed and state == "idle":
                await self._ask(language)
            elif (
                not found.failed
                and state not in WAITING
                and await self._tell_all(found, prompts.VOICE_DOWNLOAD_FAILED.format(language=name))
            ):
                found.failed = True
                log.warning("voice download (%s): failed (%s); no voice in it meanwhile", language, state or "no state")

    async def _warm_up(self, language: str, decided: asyncio.Event) -> None:
        voice = self._voice
        assert voice is not None
        try:
            health = await voice.health_when_up()
            if health is None:
                log.warning("voice warm-up (%s) skipped: the voice service is not answering", language)
                return
            listed = health.get("languages")
            if not isinstance(listed, tuple | list) or language not in listed:
                log.warning(
                    "voice warm-up (%s) skipped: the voice service is older than the language feature "
                    "(scripts/install-voice-daemon.sh)",
                    language,
                )
                return
            self._start(language, health)
            decided.set()
            started = time.monotonic()
            await voice.speak(prompts.CALL_SPOKEN[language].now_speaking, language=language)
            log.info("voice warmed up for %s in %.1f s", language, time.monotonic() - started)
        except VoiceNotSaidError:  # its model loads or downloads: the line started that, nothing else says it
            log.info("voice warm-up (%s): not said yet, its model loads", language)
        except Exception as exc:  # a background task that raises would take the bridge down
            log.warning("voice warm-up (%s) failed: %s", language, type(exc).__name__)
        finally:
            decided.set()

    def _start(self, language: str, health: Mapping[str, object]) -> None:
        """A download to wait for: jane's model of one of the six, a file of it missing, the voice service with it idle
        (fetched at the warm-up's line) or loading. One already running keeps its accounts."""
        if language not in POCKET_LANGUAGE_FILES:
            return
        if health.get(f"pocket_{language}") not in WAITING or not self._missing(language):
            return
        if self.downloading(language):
            return
        self._downloads[language] = _Download(self._clock())
        log.info(
            "voice download (%s): jane's model is not all there; nothing in it is spoken until it is ready", language
        )

    def _missing(self, language: str) -> bool:
        """Whether a file of jane's model of `language` is not in the voice service's models folder."""
        try:
            return not all((self._models / relative).is_file() for relative in POCKET_LANGUAGE_FILES[language])
        except OSError:  # not readable here: no download claimed
            return False

    async def _decide(self, language: str, aci: str) -> bool:
        """Whether `language` downloads, once its warm-up read the health (at most DECIDE_SECONDS); `aci` told."""
        decided = self._decided.get(language)
        if decided is not None:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(DECIDE_SECONDS):
                    await decided.wait()
        return self._tell(aci, language)

    def _tell(self, aci: str, language: str) -> bool:
        found = self._downloads.get(language)
        if found is None or found.failed:
            return False
        found.told.add(aci.lower())
        return True

    def _text(self, language: str, timed: str, still: str) -> str:
        """`timed` with the minutes left of DOWNLOAD_MINUTES (at least 1), `still` past them."""
        found = self._downloads[language]
        name = LANGUAGES[language].english
        left = DOWNLOAD_MINUTES - (self._clock() - found.started) / 60
        if left <= 0:
            return still.format(language=name)
        whole = max(1, math.ceil(left))
        return timed.format(language=name, minutes=f"{whole} minute{'' if whole == 1 else 's'}")

    async def _ask(self, language: str) -> None:
        """One line in `language`, thrown away: the voice service's fetch of jane's model starts again."""
        assert self._voice is not None
        try:
            await self._voice.speak(prompts.CALL_SPOKEN[language].now_speaking, language=language)
            log.info("voice download (%s): the voice service started again; asked for its fetch again", language)
        except Exception as exc:
            log.warning("voice download (%s): asking again failed: %s", language, type(exc).__name__)

    async def _tell_all(self, found: _Download, text: str) -> bool:
        """`text` to every account told; False while locked (it waits for /unlock)."""
        if self._locked():
            return False
        for aci in sorted(found.told):
            try:
                await self._send(aci, text)
            except Exception:  # stored: the resend pass tries again
                log.exception("voice download: a note could not be sent")
        return True

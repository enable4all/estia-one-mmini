"""Calls: the app's side of a call (calls.Host): what a call reads at its start (the glance), each spoken turn
through the brain, the warm-up, the practice call, the notes, the chords message and the summary. Built by App, which
keeps one-line delegates for the names the tests call; every other part of the app is read through `self.app`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Collection
from typing import TYPE_CHECKING

from local_assistant import calls, chords, prompts
from local_assistant.babble import Session
from local_assistant.brain import clean_summary
from local_assistant.calendar_tools import GLANCE_NOT_READ, CalendarTools
from local_assistant.calls import (
    Turn,
    heard,
    last_asks,
    rundown_reads,
    speakable,
    summary_transcript,
    voice_ready,
    whole_sentences,
)
from local_assistant.devices import LAPSED_UNHEARD
from local_assistant.gemma import GemmaError, ModelPausedError
from local_assistant.language import ENGLISH
from local_assistant.timefmt import local_hhmm

if TYPE_CHECKING:
    from local_assistant.app import App

log = logging.getLogger("local_assistant")  # the app's own logger: its lines keep their name in bridge.log

PRACTICE_DRAIN_S = 120.0  # after a practice call, texts typed during it are answered as practice for at most this
PRACTICE_CALL_MAX_TOKENS = 300  # a practice call's turn: a few sentences and one question (review of 26 Oct, DLG-02)

asks = last_asks  # her answer asks something: its last sentence (one asked on its way answers no yes; DLG-05)


class CallGlance:
    """A call's glance: the mail digest's counts, read when the call starts, and the calendar's, read meanwhile; each
    line on its own, so a slow calendar never holds back the mail counts. Her offer of the rundown: the first of her
    answers given with the glance that asks something; a yes right after it reads the day (`rundown_reads`), once a call."""

    def __init__(self, at: str, mail: str, calendar: asyncio.Task[str] | None) -> None:
        self.at = at
        self.mail = mail
        self.calendar = calendar
        self.waited = False  # the first turn waited for the calendar once; later turns take it when it is ready
        self.offered = False  # she asked something with the glance: her offer, made once a call
        self.offer = ""  # that answer, until the next turn takes it

    def note(self, answer: str) -> None:
        """Her answer given with the glance: the first that asks something is her offer."""
        if not self.offered and asks(answer):
            self.offered, self.offer = True, answer

    def take_offer(self) -> str:
        """Her offer, for the turn right after it only ("" for any other turn)."""
        offer, self.offer = self.offer, ""
        return offer

    def calendar_done(self) -> bool:
        return self.calendar is None or self.calendar.done()

    def lines(self) -> list[str]:
        """The lines ready now: the calendar's (once read), then the mail digest's."""
        calendar = self.calendar
        read = calendar.result() if calendar is not None and calendar.done() and not calendar.cancelled() else ""
        return [line for line in (read, self.mail) if line]


class CallHost:
    """The app's side of a call: `calls.CallManager` asks it for each turn, the notes and the summary."""

    def __init__(self, app: App, *, glance_wait: float, warm_up_seconds: float, warm_up_glance: float) -> None:
        self.app = app
        self.max_tokens, self.turn_timeout = calls.call_limits(app.cfg.calls_turn_timeout_seconds)  # by the tier
        # app.py's GLANCE_WAIT_SECONDS, WARM_UP_SECONDS and WARM_UP_GLANCE_SECONDS (what they say is there)
        self.glance_wait = glance_wait
        self.warm_up_seconds = warm_up_seconds
        self.warm_up_glance = warm_up_glance
        self.calls = 0  # calls started: a call's warm-up reads only while its own call is on
        # a call's glance: today's calendar and mail digest as numbers and times, read when an ordinary call starts
        self.glance: CallGlance | None = None
        # [chords]: a call's chords results with each turn's Sources note, for the message after the call; then the
        # last call's, which its summary is checked against
        self.chords_in_call: list[tuple[chords.Result, str]] = []
        self.chords_after: list[chords.Result] = []
        # the latest background send of a call turn's notes (`call_notes`): each waits for the one before, in order
        self.sending: asyncio.Task[None] | None = None
        # what a turn cut off by its limit or the call's end had done (`on_cancel`): its notes, sent by `call_drop_waiting`
        self.undone: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    async def calls_forever(self) -> None:
        """The call loop never takes the bridge down (a TaskGroup task that raises would)."""
        assert self.app.calls is not None
        while True:
            try:
                await self.app.calls.run_forever()
            except Exception:
                log.exception("calls: the call loop failed; starting it again")
            await asyncio.sleep(5)

    def call_started(self, aci: str, *, practice: bool = False) -> None:
        """Texts wait until the call ends; idle learning, mail sorting and her own messages keep away from Gemma. An
        ordinary call reads today's calendar and mail digest as numbers and times (the glance), a practice call neither."""
        self.app.call_began()  # texts wait for the call's end; a Deep Thinking answer on its way stops
        self.calls += 1
        self.chords_in_call = []  # [chords]: this call's results, for the message after it
        self.app.profiler.note_activity()
        if self.app.initiative is not None:
            self.app.initiative.note_activity()
        if self.app.devices:
            self.app.devices.cancel()
        self.drop_glance()
        if not practice and (self.app.calendar is not None or self.app.mail_digest is not None):
            at = local_hhmm(self.app._clock(), self.app.cfg.timezone)
            calendar = None
            if self.app.calendar is not None:
                calendar = asyncio.create_task(self.calendar_glance(self.app.calendar), name="call-glance")
            self.glance = CallGlance(at, self.mail_glance(), calendar)

    def text_waits(self, aci: str, message_id: int, *, stopped: bool = False) -> None:
        """A message from `aci` waits for the call's end (a text, voice note, photo, document or a command that waits):
        code's line in that chat at once, once a call; none for a practice call's own turns (`CallManager.text_waits`
        decides, and has her say it on the call for the caller's). `stopped`: a thinking answer the call stopped."""
        if not self.app._no_call.is_set() and self.app.calls and self.app.calls.text_waits(aci, stopped=stopped):
            self.app._background.add(task := asyncio.create_task(self.text_line(aci, message_id)))
            task.add_done_callback(self.app._background.discard)

    async def text_line(self, aci: str, message_id: int) -> None:
        """Code's line for a message that waits for the call's end, in one place: sent, never stored, as the lock's
        replies (never in her history, never sent again); not once /lock came."""
        if not self.app.guard.locked and await self.app._deliver(aci, prompts.CALL_TEXT_WAITS, "the call's line"):
            log.info("message %d waits for the call's end: code's line sent", message_id)

    def call_ended(self) -> None:
        self.drop_glance()  # the next call counts again
        if self.app.devices:
            self.app.devices.cancel()  # a question asked on the call is not answered by a later text
        self.app._no_call.set()
        self.app.profiler.note_activity()
        if self.app.reminder_scheduler is not None:
            self.app.reminder_scheduler.notify()  # reminders that waited for the call go out now

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
        """One thing the user said on a call: stored like a message (answered at once), then a waiting device's yes (code)
        or Gemma with the same tools as text; device and profile notes go out as texts. A turn of a practice call
        (`practice`, the call's own flag) goes to practice; a call she placed is ordinary, even during practice.
        `on_retry`: told once when Gemma's first try came back empty. `text_only`: the reply is read, not heard. The
        lookup guard's window counts from here (C12)."""
        started = self.app.brain.now()
        self.app.profiler.note_activity()
        await self.app._worker_idle.wait()  # a text that was being answered when the call came in finishes first
        session = self.app.babble.running(aci) if practice else None
        if session is not None:  # a practice call: its own conversation, no tools, no device yes
            return await self.practice_call_turn(aci, text, session, on_retry, spoken=not text_only)
        conversation_id = self.app.store.current_conversation().id
        said = f"{prompts.CALL_MARK} {text}"
        said_id = self.app.store.add_call_turn(conversation_id, aci, said)
        state = self.glance
        offer = state.take_offer() if state is not None else ""  # her offer counts only for the turn right after it
        if self.app.devices is not None and self.app.devices.pending:
            confirmed = await self.app.devices.confirm(said, aci)
            if confirmed is not None:
                await self.call_notes(aci, conversation_id, said_id, [confirmed])
                line = speakable(confirmed)  # code's words (English), texted too
                return Turn("", code_text=line, also_texted=(line,))
        message = self.app.store.message(said_id)
        conversation = self.app.context.enforce_limit(self.app.store.conversation(conversation_id))
        glance = await self.glance_now()
        reads = self.rundown(offer, said) if glance else ()
        try:
            reply = await self.app.brain.answer(
                conversation,
                message,
                max_tokens=self.max_tokens,
                on_tools=on_tools,
                glance=glance,
                reads=reads,
                on_retry=on_retry,
                chords_before=[result for result, _ in self.chords_in_call],
                on_cancel=self.left_undone,
                started=started,
                turn_limit=self.turn_timeout,  # the lookup guard's window: a third of it
            )
        except GemmaError as exc:
            log.error("call turn: Gemma failed: %s", exc)
            await self.call_drop_waiting(aci, exc.notes, exc.outside)  # a device that switched, a reminder set: told
            if isinstance(exc, ModelPausedError):
                return Turn("", code_text=self.app._paused_spoken())
            return Turn(prompts.call_lines(self.app.language, name=self.app.cfg.name).lost, failed=True)
        # cut off at the token limit: never stop mid-sentence. Code's line why no reminder was set (her words about it
        # are left out of the reply) is said in English after hers, before any question, and kept
        said_text = whole_sentences(reply.hers, cut=reply.cut)
        if not glance:  # her closing question after a full answer goes; with the glance her offer of the rundown stays
            said_text = calls.without_closing_questions(said_text, name=self.app.cfg.name, language=self.app.language)
        code_line = speakable(reply.not_set_spoken) if reply.not_set else ""
        asks_yes = bool(self.app.devices and self.app.devices.pending)
        question = ""
        if asks_yes and (devices := self.app.devices) is not None:  # the caller hears what a yes will do (a text too)
            question = speakable(devices.question())
            # Gemma in another language may claim it is done already: only code's question, said and kept; her words
            # that are code's question (brain's line for an answer with no words of hers) are said once, last
            if self.app.language != ENGLISH or reply.hers == devices.question():
                said_text = ""
            else:  # as in a text: her closing question never stands before code's, so a yes never answers hers
                said_text = calls.without_last_questions(said_text)
        kept = " ".join(part for part in (said_text, code_line, question) if part)  # as said: the question last
        if glance and state is not None and state is self.glance:
            state.note(heard(said_text))  # her offer of the rundown, as said: a yes to it next turn reads the day
        reply_id = self.app.store.add_call_reply(conversation_id, aci, kept, said_id)
        device_note = self.app.devices.take_note() if self.app.devices else ""
        # [chords]: the chords note is not sent during the call: said now, texted in one message after it
        self.chords_in_call += [(result, reply.sources) for result in reply.chords_results]
        guards = (reply.reminder, reply.device, reply.lookup)  # code's true lines (lookup: a promise that never ran)
        notes = [note for note in (reply.sources, device_note, reply.notice, *guards) if note]
        outside = {reply.sources} | ({reply.notice} if reply.read_outside else set())  # quoting outside text
        await self.call_notes(aci, conversation_id, said_id, notes, outside=outside)
        log.info("call turn %d answered: %d tool calls; model: %s", said_id, reply.tool_calls, reply.timing.line())
        # the device question last: only her last sentence is said whole, so a yes right after it lands on it
        spoken = (reply.lookup, reply.chords_spoken, reply.device_spoken, code_line, reply.reminder_spoken, question)
        code_text = " ".join(line for line in spoken if line)  # English
        kept_code = tuple(line for line in (code_line, question) if line)  # kept in her reply, as `kept`
        # texted on their own too: never in the rest of an answer her voice stopped for. The chords line only when every
        # result it tells worked and one moved: the message after the call holds the ones that worked, sent when one moved
        told = chords.told(reply.chords_results)
        chords_texted = reply.chords_spoken if told and all(r.ok for r in told) and any(r.moved for r in told) else ""
        texted = (reply.lookup, chords_texted, reply.device_spoken, reply.reminder_spoken)
        return Turn(
            said_text,
            reply_id=reply_id,
            asks_yes=asks_yes,
            end_call=reply.end_call,
            code_text=code_text,
            kept_code=kept_code,
            also_texted=tuple(line for line in texted if line),
            outside=reply.read_outside,
        )

    async def call_warm_up(self, aci: str, *, practice: bool = False) -> None:
        """The model's warm-up for a call that starts (while it rings, or once a call she placed is picked up): Gemma
        loads and reads the first turn's request up to the caller's words (Brain.warm_up), built after that turn's own
        waits (a text being answered finishes first; the glance's calendar at most WARM_UP_GLANCE_SECONDS, leaving the
        turn its own wait), so the turn finds its prefix in Ollama's cache. A practice call warms the practice request.
        Nothing without Ollama itself (`warm_up`), nor once its call ended or /lock came meanwhile. The read is cut off
        after WARM_UP_SECONDS. One log line, metadata only; it never raises."""
        if not self.app._warms_up:
            return
        call = self.calls  # the call it is for, taken before any wait
        try:
            await self.app._worker_idle.wait()  # as call_turn: that text's reply is in the turn's history
            session = self.app.babble.running(aci) if practice else None
            glance = await self.glance_now(warm_up=True) if session is None else ""
            if self.app._no_call.is_set() or self.calls != call or self.app.guard.locked:
                return  # its call ended (another may have started), or /lock: Gemma is not asked
            started = time.monotonic()
            if session is not None:
                conversation = self.app.store.conversation(session.conversation_id)
                warming = self.app.brain.warm_up(conversation, practice=self.app.babble.turn(spoken=True))
            else:
                warming = self.app.brain.warm_up(self.app.store.current_conversation(), glance=glance)
            completion = await asyncio.wait_for(warming, self.warm_up_seconds)
        except TimeoutError:
            log.warning("calls: model warm-up failed (timeout)")
            return
        except Exception as exc:  # the call goes on as without it: one line, the kind only
            log.warning("calls: model warm-up failed (%s)", type(exc).__name__)
            return
        tokens = f" (prompt {completion.prompt_tokens:,} tokens)" if completion.prompt_tokens else ""
        log.info("calls: model warmed up in %.1f s%s", time.monotonic() - started, tokens)

    async def calendar_glance(self, calendar: CalendarTools) -> str:
        """Today's calendar as numbers and times only, for her input on this call (never a title, place or calendar
        name: that text would make the whole call outside text); any failure is "not read"."""
        try:
            return await calendar.glance()
        except Exception:  # a glance is a courtesy: the call goes on without it
            log.exception("calls: the calendar's glance failed")
            return GLANCE_NOT_READ

    def mail_glance(self) -> str:
        """The mail digest as numbers only (never a sender or subject); "" when it fails."""
        if self.app.mail_digest is None:
            return ""
        try:
            return self.app.mail_digest.glance()
        except Exception:
            log.exception("calls: the mail digest's glance failed")
            return ""

    async def glance_now(self, *, warm_up: bool = False) -> str:
        """The call's glance as far as it is ready: the mail counts at once; the first turn waits for the calendar at
        most GLANCE_WAIT_SECONDS, later turns take it once read. The call's warm-up (`warm_up`) waits at most
        WARM_UP_GLANCE_SECONDS, so the first turn, some 20 s later, finds the glance read and has its own wait. The
        call's end drops it."""
        glance = self.glance
        if glance is None:
            return ""
        if glance.calendar is not None and not glance.calendar.done() and not glance.waited:
            if not warm_up:
                glance.waited = True
            wait = self.warm_up_glance if warm_up else self.glance_wait
            # asyncio.wait neither cancels it nor raises when the call's end cancels it meanwhile
            await asyncio.wait({glance.calendar}, timeout=wait)
        if self.glance is not glance:  # the call ended meanwhile
            return ""
        return prompts.call_glance(
            glance.lines(), at=glance.at, calendar=self.app.calendar is not None, mail=self.app.mail_digest is not None
        )

    def rundown(self, offer: str, said: str) -> tuple[tuple[str, str], ...]:
        """The reads for a yes to her offer of the rundown, said right after it (`rundown_reads`)."""
        app = self.app
        reads = rundown_reads(
            offer, said, app.language, calendar=app.calendar is not None, mail=app.mail_digest is not None
        )
        if reads:
            log.info("call: a yes to the rundown; code reads %s", ", ".join(name for name, _ in reads))
        return reads

    def drop_glance(self) -> None:
        if self.glance is not None and self.glance.calendar is not None:
            self.glance.calendar.cancel()
        self.glance = None

    async def practice_call_turn(
        self, aci: str, text: str, practice: Session, on_retry: Callable[[], None] | None = None, *, spoken: bool = True
    ) -> Turn:
        """What the user said on a practice call: in the practice conversation, answered with the practice rules (no
        tools; no "→" line unless the reply is read, `spoken` False: a text-only call). A claimed reminder still gets
        code's note and line."""
        conversation_id = practice.conversation_id
        said_id = self.app.store.add_call_turn(conversation_id, aci, f"{prompts.CALL_MARK} {text}")
        message = self.app.store.message(said_id)
        conversation = self.app.context.enforce_limit(self.app.store.conversation(conversation_id))
        try:
            reply = await self.app.brain.answer(
                conversation,
                message,
                max_tokens=PRACTICE_CALL_MAX_TOKENS,
                practice=self.app.babble.turn(spoken=spoken),
                on_retry=on_retry,
            )
        except GemmaError as exc:
            log.error("practice call turn: Gemma failed: %s", exc)
            if isinstance(exc, ModelPausedError):  # as on an ordinary call: code's line (English), never the model's
                return Turn("", code_text=self.app._paused_spoken())
            return Turn(prompts.call_lines(practice.code, name=self.app.cfg.name).lost, failed=True)
        said_text = whole_sentences(reply.text, cut=reply.cut)
        reply_id = self.app.store.add_call_reply(conversation_id, aci, said_text, said_id)
        if reply.reminder:
            await self.call_notes(aci, conversation_id, said_id, [reply.reminder])
        log.info("practice call turn %d answered; model: %s", said_id, reply.timing.line())
        texted = (reply.reminder_spoken,) if reply.reminder and reply.reminder_spoken else ()  # its note: a text too
        return Turn(
            said_text, reply_id=reply_id, end_call=reply.end_call, code_text=reply.reminder_spoken, also_texted=texted
        )

    async def practice_call_ended(self, aci: str) -> None:
        """A practice call the user spoke in ended: practice ends and its recap goes out at once (no quiet-hours wait),
        instead of the call summary. Texts typed during the call wait in the queue: they are answered first, as
        practice turns (the recap reads them), for at most PRACTICE_DRAIN_S. A plain /babble resumes it."""
        if self.app.babble.running(aci) is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.app.queue.join(), PRACTICE_DRAIN_S)
        if not self.app._no_call.is_set():
            return  # the user called again meanwhile: that practice call's end ends practice
        ended = self.app.babble.end("call")
        if ended is not None:
            await self.app.babble.finish(ended)

    def left_undone(self, done: tuple[str, ...], outside: tuple[str, ...]) -> None:
        """A call turn was cut off (Brain.answer's `on_cancel`): what it had done waits for `call_drop_waiting`."""
        self.undone.append((done, outside))

    async def call_drop_waiting(
        self, aci: str, done: Collection[str] = (), outside: Collection[str] = (), *, unheard: bool = False
    ) -> None:
        """Drop a device action waiting for a yes; what did switch meanwhile is still a note, as is what a turn did before
        Gemma failed (`done`, GemmaError.notes; `outside`: quoting outside text) or was cut off (`left_undone`; C5).
        `unheard`: her voice could not ask the question: the note says it was not switched, and why (`LAPSED_UNHEARD`)."""
        if self.app.devices is not None:
            self.app.devices.cancel(lapsed=LAPSED_UNHEARD if unheard else "")
        undone, self.undone = self.undone, []
        done = [*done, *(note for notes, _ in undone for note in notes)]
        outside = {*outside, *(note for _, quoted in undone for note in quoted)}
        notes = [note for note in (self.app.devices.take_note() if self.app.devices else "", *done) if note]
        if notes:
            await self.call_notes(aci, self.app.store.current_conversation().id, None, notes, outside=outside)

    async def call_standby(self, aci: str) -> None:
        """Standby starts: her offer of the rundown and a device action waiting for a yes are dropped (her wake line
        invites a yes that must read and switch nothing); a dropped device action gets code's note why."""
        if self.glance is not None:
            self.glance.take_offer()
        if self.app.devices is not None:
            self.app.devices.standby()
        await self.call_drop_waiting(aci)

    def call_interrupted(self, reply_id: int, heard: str) -> None:
        """The user talked over the answer: history keeps what they heard of it."""
        self.app.store.set_message_text(reply_id, heard)

    async def call_voice_ready(self) -> bool:
        """The voice service can take a call now (both engines ready, her English voice not one it leaves out)."""
        return (voice := self.app._call_voice) is not None and voice_ready(await voice.health(), voice.voice)

    async def call_note(self, aci: str, text: str, *, outside: bool = False) -> None:
        """A note about a call (over, or never begun), sent after the turn notes still on their way, in order."""
        conversation_id = self.app.store.current_conversation().id
        out_id = self.app.store.add_outgoing(conversation_id, aci, "note", text, None, outside=outside)
        if self.sending is not None:
            await asyncio.wait({self.sending})  # never raises, also when it was cancelled
        await self.app._send(out_id)

    async def call_text(
        self, aci: str, text: str, *, reply_id: int | None, outside: bool, practice: bool = False
    ) -> None:
        """Code's text during the call (the rest of an answer Pocket TTS could not say): code's note in the conversation
        of her reply `reply_id` (the current one for None), after the turn's notes, sent in the background; in her
        history as code's note (rule 6), `outside` when it quotes outside text."""
        reply = self.app.store.message(reply_id) if reply_id is not None else None
        session = self.app.babble.running(aci) if practice and reply is None else None  # a practice call's own chat
        own = session.conversation_id if session is not None else self.app.store.current_conversation().id
        conversation_id = reply.conversation_id if reply is not None else own
        said_id = reply.reply_to_id if reply is not None else None  # the caller's turn it answers
        await self.call_notes(aci, conversation_id, said_id, [text], outside={text} if outside else ())

    async def call_reply_text(self, aci: str, reply_id: int) -> bool:
        """A text-only call's answer: the stored reply (a call reply of `aci`, stored as said) is sent whole as a text
        message's answer is, after the turn's notes; whether it went out."""
        row = self.app.store.message(reply_id)
        if (row.direction, row.kind, row.status, row.peer_aci) != ("out", "text", "sent", aci):
            return False
        self.app.store.set_status(reply_id, "pending")  # stored as said on a call: now it must go out
        if self.sending is not None:
            await asyncio.wait({self.sending})  # the turn's notes first, in order
        return await self.app._send(reply_id)

    async def call_chords(self, aci: str) -> None:
        """[chords]: after the call, one message from code: every chords result of the call in call order, its fixed
        lines word for word (the exact chords, sections as found), then its source: the web page that holds the
        chords, else the turn's Sources lines. Sent only when a transpose ran; stored as a note quoting outside text.
        The results wait in memory for this call only (a restart during the call loses them: a known gap)."""
        results, self.chords_in_call = self.chords_in_call, []
        self.chords_after = [result for result, _ in results if result.ok]  # the summary's check reads them
        if not any(result.moved for result, _ in results):
            return
        parts = []
        for result, sources in results:
            if not result.ok:
                continue
            if result.source is not None:
                source = f"\nSource: {result.source[0]}: {result.source[1]}"
            else:
                listed = [line for line in sources.splitlines()[1:] if line.strip()]
                source = "\nSources:\n" + "\n".join(listed) if listed else ""
            parts.append(result.lines + source)
        await self.call_note(aci, f"{prompts.CHORDS_AFTER_CALL}\n" + "\n\n".join(parts), outside=True)  # its source
        log.info("calls: the chords message after the call sent (%d results)", len(parts))

    async def call_summary(self, aci: str, lines: list[str], span: str) -> bool:
        """The call's summary as code's note quoting outside text, filtered, links defanged unless the caller said them
        (`brain.clean_summary`, I33), headed by `span` (`calls.call_span`), unchecked chords left out. False: none."""
        messages = [
            {"role": "system", "content": prompts.CALL_SUMMARY_RULES.format(name=self.app.cfg.name)},
            {"role": "user", "content": summary_transcript(lines)},
        ]
        try:
            completion = await self.app.gemma.chat(messages, max_tokens=300)
        except GemmaError as exc:
            log.warning("call summary: Gemma failed: %s", exc)
            return False
        summary = clean_summary(completion.content, "\n".join(n for n in lines if n.startswith("User: ")))
        if summary and self.chords_after:
            checked = chords.check(summary, results=self.chords_after, sources=[], language=self.app.language)
            if checked.left_out:
                log.warning("call summary: %d sentences with chords not worked out left out", checked.left_out)
                summary = checked.text
        if summary:  # Gemma's words about a call that may have read outside text: marked so (code_notes)
            await self.call_note(aci, prompts.CALL_SUMMARY.format(span=span, summary=summary), outside=True)
        return bool(summary)

    async def call_notes(
        self, aci: str, conversation_id: int, reply_to: int | None, notes: list[str], *, outside: Collection[str] = ()
    ) -> None:
        """Code's notes after a call turn, stored in order at once and sent in order in the background (review of
        0.9.7, C4: sent before the turn returned, Signal's retries delayed her first word); one that cannot go out now
        is sent again by the resend pass. `outside`: the ones that quote outside text (code_notes)."""
        add = self.app.store.add_outgoing
        if ids := [add(conversation_id, aci, "note", note, reply_to, outside=note in outside) for note in notes]:
            self.send_later(ids)

    def send_later(self, ids: list[int]) -> None:
        """Send these stored notes in the background, in order, after the ones before them."""
        before = self.sending

        async def send() -> None:
            if before is not None:
                await asyncio.wait({before})  # in order; never raises, also when it was cancelled
            for out_id in ids:
                try:
                    await self.app._send(out_id)
                except Exception:  # stored: the resend pass tries again
                    log.exception("calls: a note of a call turn could not be sent")

        self.sending = task = asyncio.create_task(send(), name="call-notes")
        self.app._background.add(task)
        task.add_done_callback(self.app._background.discard)

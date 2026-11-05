"""Deep Thinking (owner, 4 Nov 2026): her answers to texts and voice notes think first, until 30 minutes pass without a
message from the account that switched it on.

One switch for each allowed account (owner, 4 Nov): each account has its own typed /thinking on and /thinking off, its own
30 minutes (started again only by that account's own messages) and its own /thinking and /status replies, and an answer
thinks only when its sender's switch is on. The chat's history stays shared (SECURITY.md, known accepted gaps); only the
switch is each account's own.

Started and ended only by the user's typed /thinking (never a voice note, a call or Gemma: no tool). The state is one row
of the state table for each account, the time it lasts until: a restart inside the 30 minutes keeps it, and a time that
has passed reads as off, so nothing runs when it ends and nothing is sent. What it changes is one request's effort and
size (Brain.answer(thinking=…)); the thought itself is never kept, logged or shown, only its length is logged. A log line
here says that a switch changed, never whose.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from local_assistant.store import Store

log = logging.getLogger(__name__)

# the state table: one row for each allowed account, the epoch seconds its switch lasts until; absent or past: off. (No
# release ever had the one row for the whole install, "thinking:until": it is never read, so nothing is migrated.)
THINKING_KEY = "thinking:until:{aci}"
IDLE_SECONDS = (
    30 * 60
)  # 30 minutes without a message from the account switch it off (owner: "30minutes auto-off is good")
EFFORT = "high"  # reasoning_effort of a thinking request (Ollama maps any effort but "none" to thinking on)
# What a thinking request may use for its thought, on top of the usual cap of its answer: the one number to change.
# Others measured Gemma 4 26B's thinking at 819 tokens on simple questions about a picture, 3,907 on MMLU-Pro and 7,699
# on GPQA-Diamond (means), and about half the answers came back empty at a cap of 4,096: the thought ate it. Her
# requests run 22-24k prompt tokens of 32,768 with a full window of history (12,000 tokens), so 24,000 + 600 (her
# answer) + 8,000 = 32,600 leaves less than the 1,000 tokens the code keeps free (`CONTEXT_MARGIN_TOKENS`): a long chat
# gives up its oldest messages for a thinking answer (`ContextBuilder.enforce_limit(extra_reply=…)`). Simulated with a
# full install's rules and tools: a window of 8,000 tokens or less keeps everything at 2.6 to 3.5 characters per token,
# a full one of 12,000 keeps 6,000 to 7,000 at 2.6 to 3.0 (the newest 28 to 37 messages) and all of it at 3.5. 6,000
# would keep a full window whole from 2.8 up, but cuts the hardest questions off more often (each one is a wait with
# no thought to show, then a plain answer). Nothing here is measured on her own Mac yet.
BUDGET_TOKENS = 8_000
# The least room a request after a tool result may leave for the thought and still think. The history keeps the thought's
# room free before an answer starts (`enforce_limit(extra_reply=…)`), but this answer's own tool results come after: they
# are cut for a plain answer only, and the thought gets what the context leaves after them, never the other way (review
# of 4 Nov, correctness-2: a thinking answer lost a whole web result a plain one kept). Under this a thought would be cut
# off before it could end (means of 819 tokens on simple questions about a picture), so that request does not think.
FLOOR_TOKENS = 2_000
# the busy note ("still working on your answer") waits this long for a thinking answer, never less than its usual wait:
# a thought of 8,000 tokens and the answer after it (600) take at most about 239 s at the 36 tokens a second measured
# with thinking on (docs/agents/prompts-evals.md, run 1), so the note comes at 180 s, before the longest answers end;
# the first answer after a switch may read her prompt again
BUSY_NOTE_SECONDS = 180.0
# the worker waits this long for the notes after a call (its summary above all) before it answers a message whose sender
# has Deep Thinking on: a thinking answer holds Ollama's one slot for minutes, and the summary, with the call's start and
# end, would wait behind it
AFTER_CALL_SECONDS = 60.0


class DeepThinking:
    """Whether each account's answers think now. `room`: False unless the config says the model's context is 32,768 tokens
    or more: under that (the 16 GB tier) the thought's room would take the whole history, and with no `context_tokens`
    line (`known` False: a config from before 0.9.7, or a setup that could not read the model's) nothing says there is
    room (review of 4 Nov, promises-2), so it never switches on there, for any account."""

    def __init__(
        self, store: Store, *, clock: Callable[[], float] = time.time, room: bool = True, known: bool = True
    ) -> None:
        self._store = store
        self._clock = clock
        self._room = room
        self._known = known
        # the thinking answers on their way, each one's event (a call that starts, or its account's /thinking off, sets
        # it: `interrupt`, `stop`) and the account it is for; the worker answers one message at a time, so one at most
        self._answers: dict[asyncio.Event, str] = {}

    @property
    def room(self) -> bool:
        """Whether the model's context leaves room for it: False, it never switches on (and a plain /thinking says why,
        not how to switch it on)."""
        return self._room

    @property
    def known(self) -> bool:
        """Whether the config says the model's context (`context_tokens`): with no room, False means the line is missing,
        True that the context is too small."""
        return self._known

    @staticmethod
    def _key(aci: str) -> str:
        return THINKING_KEY.format(aci=aci.lower())

    def until(self, aci: str) -> float | None:
        """When this account's switch ends by itself (epoch seconds), or None when it is off, its time has passed or
        nobody can read it."""
        if not self._room:
            return None
        try:
            ends = float(self._store.get_state(self._key(aci)) or "")
        except ValueError:
            return None
        return ends if self._clock() < ends else None

    def on(self, aci: str) -> bool:
        return self.until(aci) is not None

    def start(self, aci: str) -> float | None:
        """/thinking on: this account's 30 minutes start now. When it ends, or None where the context leaves no room."""
        if not self._room:
            return None
        ends = self._clock() + IDLE_SECONDS
        self._store.set_state(self._key(aci), str(ends))
        log.info("deep thinking: on for an account")
        return ends

    def stop(self, aci: str) -> bool:
        """/thinking off: this account's switch is off now, whatever answers of the others are doing. Whether one of this
        account's answers was thinking: that answer is told to stop, as a call that starts tells it (Brain stops it unless
        a tool of that answer has run, then it only drops its thought; App.process answers its message again, and the
        account's messages still waiting find the switch off)."""
        if self._store.get_state(self._key(aci)) is not None:
            self._store.delete_state(self._key(aci))
            log.info("deep thinking: off for an account")
        mine = [event for event, owner in self._answers.items() if owner == aci.lower()]
        for event in mine:
            event.set()
        if mine:
            log.info("deep thinking: an answer on its way is told to stop")
        return bool(mine)

    def touch(self, aci: str) -> None:
        """A message from this account: its 30 minutes start again. Only while its switch is on: a message never turns it
        on, never again once its time has passed, and never starts another account's time."""
        if self.on(aci):
            self._store.set_state(self._key(aci), str(self._clock() + IDLE_SECONDS))

    def busy_wait(self, usual: float) -> float:
        """How long the busy note waits for a thinking answer: `BUSY_NOTE_SECONDS`, never less than `usual`."""
        return max(usual, BUSY_NOTE_SECONDS)

    async def when_calls_are_over(
        self, no_call: asyncio.Event, after_notes: Callable[[float], Awaitable[None]] | None, aci: str
    ) -> None:
        """What the worker waits for before it answers a message from `aci`: no call is on and, while that account's switch
        is on, the notes after the last call have gone out (`after_notes`, for at most `AFTER_CALL_SECONDS`: review of 4
        Nov, calls-4: a thinking answer sent first took Ollama's one slot, and the call's summary waited for it). A call
        that starts meanwhile is waited for again."""
        while True:
            await no_call.wait()
            if after_notes is not None and self.on(aci):
                await after_notes(AFTER_CALL_SECONDS)
            if no_call.is_set():
                return

    def begin(self, aci: str) -> asyncio.Event:
        """A thinking answer for `aci` starts (App.process): the event a call that starts, or that account's /thinking off,
        sets."""
        stop = asyncio.Event()
        self._answers[stop] = aci.lower()
        return stop

    def end(self, stop: asyncio.Event) -> None:
        """That answer is over, whatever came of it."""
        self._answers.pop(stop, None)

    def interrupt(self) -> None:
        """A call started: every thinking answer on its way, whichever account it is for, is told (Brain stops it unless a
        tool of that answer has run, and App.process answers the message again after the call; an answer that must finish
        only drops its thought, so the call never waits for it)."""
        for event in self._answers:
            event.set()

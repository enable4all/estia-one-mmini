"""Deep Thinking (owner, 4 Nov 2026): /thinking on|off, its state in the database, its 30 minutes without a message from
the user, and the lines code writes for it (/status, /help). One switch for each allowed account (owner, 4 Nov): its own
state, its own 30 minutes, its own replies. Where it applies (her answers to texts and voice notes only, the request's
effort and cap, the room kept for it, the cut-off fallback) is in `test_thinking_brain.py`; what the app does around it
(the busy note, a call that starts, two accounts through the bridge) in `test_app_thinking.py`."""

import asyncio
import json
from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.commands import Commands, answered_at_once
from local_assistant.context import ContextBuilder
from local_assistant.profile import Profile
from local_assistant.store import Message, Store
from local_assistant.thinking import AFTER_CALL_SECONDS, IDLE_SECONDS, THINKING_KEY, DeepThinking
from tests.conftest import ME, T0, TZ
from tests.test_app import PARTNER
from tests.test_commands import command, status

START = T0 / 1000  # 2026-10-08 09:32:23 in Amsterdam: thirty minutes later it is 10:02


def row(aci: str) -> str:
    """The state table's key of one account's switch."""
    return THINKING_KEY.format(aci=aci)


def command_of(store: Store, aci: str, text: str, ts: int = T0) -> Message:
    """A typed command from `aci` (the tests' `command` is always ME's)."""
    message_id = store.add_incoming(store.current_conversation().id, aci, ts, "command", text)
    assert message_id is not None
    return store.message(message_id)


class Clock:
    """A clock a test moves by hand."""

    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make(
    store: Store, clock: Clock | None = None, *, room: bool = True, known: bool = True
) -> tuple[Commands, DeepThinking]:
    profile = Profile(store)
    switch = DeepThinking(store, clock=clock or Clock(), room=room, known=known)
    commands = Commands(store, profile, ContextBuilder(store, profile, TZ), TZ, status, lambda: None, thinking=switch)
    return commands, switch


# the state: off until typed on, thirty minutes from the typing and from every message after it


def test_it_is_off_until_it_is_typed_on(store: Store) -> None:
    switch = DeepThinking(store, clock=Clock())
    assert switch.on(ME) is False and switch.until(ME) is None


def test_on_lasts_30_minutes_and_every_message_from_the_user_starts_them_again(store: Store) -> None:
    clock = Clock()
    switch = DeepThinking(store, clock=clock)
    assert IDLE_SECONDS == 30 * 60
    assert switch.start(ME) == START + 1_800 and switch.on(ME)
    clock.now = START + 1_799.9
    assert switch.on(ME) and switch.until(ME) == START + 1_800
    clock.now = START + 1_000  # a message: the 30 minutes start again from here
    switch.touch(ME)
    assert switch.until(ME) == START + 2_800
    clock.now = START + 2_799
    assert switch.on(ME)
    clock.now = START + 2_800  # 30 minutes without one: off by itself
    assert switch.on(ME) is False and switch.until(ME) is None


def test_a_message_never_turns_it_on_again_once_the_time_has_passed(store: Store) -> None:
    clock = Clock()
    off = DeepThinking(store, clock=clock)
    off.touch(ME)  # off: a message changes nothing and leaves nothing behind
    assert off.on(ME) is False and store.get_state(row(ME)) is None
    off.start(ME)
    clock.now = START + 2_000  # the 30 minutes are over
    off.touch(ME)
    assert off.on(ME) is False and off.until(ME) is None


def test_a_restart_inside_the_30_minutes_keeps_it_and_after_them_it_is_off(store: Store) -> None:
    """The state is in the state table, not in memory: the bridge started again reads it, and a time that has passed
    reads as off (nothing runs at that moment, so nothing is sent when it ends)."""
    clock = Clock()
    DeepThinking(store, clock=clock).start(ME)
    clock.now = START + 600
    assert DeepThinking(store, clock=clock).on(ME)  # a new object on the same database: a restart
    assert DeepThinking(store, clock=clock).until(ME) == START + 1_800
    clock.now = START + 1_801
    assert DeepThinking(store, clock=clock).on(ME) is False


def test_off_ends_it_at_once_and_a_value_nobody_can_read_is_off(store: Store) -> None:
    switch = DeepThinking(store, clock=Clock())
    switch.start(ME)
    assert switch.stop(ME) is False  # none of her answers was thinking: nothing to stop
    assert switch.on(ME) is False and store.get_state(row(ME)) is None
    store.set_state(row(ME), "soon")
    assert switch.on(ME) is False and switch.until(ME) is None  # never an error that stops her answering


def test_a_model_context_under_32768_tokens_leaves_no_room_for_it(store: Store) -> None:
    """On the 16 GB tier (16,384 tokens) the thought's room would take the whole history: it stays off."""
    switch = DeepThinking(store, clock=Clock(), room=False)
    assert switch.start(ME) is None and switch.on(ME) is False and store.get_state(row(ME)) is None


# one switch for each allowed account (owner, 4 Nov): its own row, its own 30 minutes, its own off


def test_each_account_has_its_own_switch_in_its_own_row(store: Store) -> None:
    switch = DeepThinking(store, clock=Clock())
    assert row(ME) == f"thinking:until:{ME}"
    assert switch.start(ME) == START + 1_800
    assert switch.on(ME) and switch.on(PARTNER) is False and switch.until(PARTNER) is None
    assert store.get_state(row(ME)) == str(START + 1_800) and store.get_state(row(PARTNER)) is None
    switch.start(PARTNER)
    switch.stop(ME)  # ME's off ends ME's alone
    assert switch.on(ME) is False and switch.on(PARTNER)
    assert store.get_state(row(ME)) is None and store.get_state(row(PARTNER)) is not None


def test_a_message_of_another_account_never_starts_the_30_minutes_again(store: Store) -> None:
    clock = Clock()
    switch = DeepThinking(store, clock=clock)
    switch.start(ME)
    clock.now = START + 1_000
    switch.touch(PARTNER)  # their switch is off: nothing changes, nothing is left behind, and ME's time stands
    assert switch.until(ME) == START + 1_800 and store.get_state(row(PARTNER)) is None
    switch.start(PARTNER)  # on at START + 1,000: until START + 2,800
    clock.now = START + 1_500
    switch.touch(PARTNER)  # theirs start again from here: ME's time stands
    assert switch.until(PARTNER) == START + 3_300 and switch.until(ME) == START + 1_800
    clock.now = START + 1_700
    switch.touch(ME)  # and ME's own message starts only ME's
    assert switch.until(ME) == START + 3_500 and switch.until(PARTNER) == START + 3_300
    clock.now = START + 3_400  # PARTNER's last message was at 1,500, ME's at 1,700: theirs end first
    assert switch.on(PARTNER) is False and switch.on(ME)


def test_a_restart_keeps_each_accounts_row(store: Store) -> None:
    clock = Clock()
    first = DeepThinking(store, clock=clock)
    first.start(ME)  # until START + 1,800
    clock.now = START + 600
    first.start(PARTNER)  # until START + 2,400
    clock.now = START + 1_000
    restarted = DeepThinking(store, clock=clock)  # a new object on the same database: a restart
    assert restarted.until(ME) == START + 1_800 and restarted.until(PARTNER) == START + 2_400
    clock.now = START + 2_000  # ME's 30 minutes are over, PARTNER's are not
    later = DeepThinking(store, clock=clock)
    assert later.on(ME) is False and later.on(PARTNER) and later.until(PARTNER) == START + 2_400


def test_the_old_single_row_is_never_read(store: Store) -> None:
    """No release had the one row of the whole install ("thinking:until"): no migration, a leftover switches nobody on."""
    store.set_state("thinking:until", str(START + 1_800))
    switch = DeepThinking(store, clock=Clock())
    assert switch.on(ME) is False and switch.on(PARTNER) is False


def test_off_says_whether_one_of_that_accounts_answers_was_thinking(store: Store) -> None:
    """/thinking off stops the answer of that account that is thinking (told through its event, which Brain races its
    requests against) and says so; another account's off never stops it."""
    switch = DeepThinking(store, clock=Clock())
    switch.start(ME)
    switch.start(PARTNER)
    mine = switch.begin(ME)  # ME's answer is thinking
    assert switch.stop(PARTNER) is False and not mine.is_set()  # theirs: never stops ME's
    assert switch.on(ME) and switch.on(PARTNER) is False
    assert switch.stop(ME) is True and mine.is_set() and switch.on(ME) is False
    switch.end(mine)  # that answer is over
    assert switch.stop(ME) is False  # nothing of ME's thinks now


def test_a_call_stops_whichever_answer_is_thinking(store: Store) -> None:
    switch = DeepThinking(store, clock=Clock())
    over, theirs = switch.begin(ME), switch.begin(PARTNER)
    switch.end(over)  # ME's answer is over; PARTNER's is on its way
    switch.interrupt()  # a call starts: the account of the answer on its way does not matter
    assert theirs.is_set() and not over.is_set()
    switch.end(theirs)
    switch.interrupt()  # with none on its way, nothing happens


# the worker's wait for a call's end and, while it is on, for the notes after the call (review of 4 Nov, calls-4)


class Notes:
    """The notes after a call, as the call manager holds them: `after_notes(limit)` waits for `done`."""

    def __init__(self) -> None:
        self.done = asyncio.Event()
        self.limits: list[float] = []

    async def after_notes(self, limit: float) -> None:
        self.limits.append(limit)
        await self.done.wait()


async def test_without_a_call_the_worker_does_not_wait_for_anything(store: Store) -> None:
    switch, no_call, notes = DeepThinking(store, clock=Clock()), asyncio.Event(), Notes()
    no_call.set()
    switch.start(ME)
    async with asyncio.timeout(1):
        await switch.when_calls_are_over(no_call, None, ME)  # no call manager: calls are off
        notes.done.set()
        await switch.when_calls_are_over(no_call, notes.after_notes, ME)
    assert notes.limits == [AFTER_CALL_SECONDS] and AFTER_CALL_SECONDS == 60.0  # while it is on, asked with the cap


async def test_while_it_is_on_the_worker_waits_for_the_notes_after_the_call_up_to_the_cap(store: Store) -> None:
    switch, no_call, notes = DeepThinking(store, clock=Clock()), asyncio.Event(), Notes()
    switch.start(ME)
    no_call.set()  # the call ended
    waiting = asyncio.create_task(switch.when_calls_are_over(no_call, notes.after_notes, ME))
    await asyncio.sleep(0.05)
    assert not waiting.done() and notes.limits == [AFTER_CALL_SECONDS]
    notes.done.set()  # the summary went out
    async with asyncio.timeout(1):
        await waiting


async def test_while_it_is_off_the_notes_are_not_waited_for(store: Store) -> None:
    """Only a thinking answer holds Ollama's one slot for minutes: an answer that does not think goes as it always did."""
    switch, no_call, notes = DeepThinking(store, clock=Clock()), asyncio.Event(), Notes()
    no_call.set()
    async with asyncio.timeout(1):
        await switch.when_calls_are_over(no_call, notes.after_notes, ME)
    assert notes.limits == []


async def test_a_call_that_starts_while_the_notes_are_waited_for_is_waited_for_too(store: Store) -> None:
    switch, no_call, notes = DeepThinking(store, clock=Clock()), asyncio.Event(), Notes()
    switch.start(ME)
    no_call.set()
    waiting = asyncio.create_task(switch.when_calls_are_over(no_call, notes.after_notes, ME))
    await asyncio.sleep(0.05)
    no_call.clear()  # the next call starts before the notes are out
    notes.done.set()
    await asyncio.sleep(0.05)
    assert not waiting.done()  # texts wait for that call too
    no_call.set()
    async with asyncio.timeout(1):
        await waiting


async def test_the_notes_after_a_call_are_waited_for_only_for_an_account_with_it_on(store: Store) -> None:
    """The wait is for the Ollama slot a thinking answer would hold: a waiting message of an account with it off is
    answered as ever, whoever else has it on."""
    switch, no_call, notes = DeepThinking(store, clock=Clock()), asyncio.Event(), Notes()
    switch.start(ME)
    no_call.set()
    async with asyncio.timeout(1):
        await switch.when_calls_are_over(no_call, notes.after_notes, PARTNER)  # PARTNER's switch is off
    assert notes.limits == []
    waiting = asyncio.create_task(switch.when_calls_are_over(no_call, notes.after_notes, ME))
    await asyncio.sleep(0.05)
    assert not waiting.done() and notes.limits == [AFTER_CALL_SECONDS]  # ME's is on: the notes first
    notes.done.set()
    async with asyncio.timeout(1):
        await waiting


# the command: typed only, like /babble


async def test_a_plain_thinking_tells_the_state(store: Store) -> None:
    commands, _ = make(store)
    assert await commands.handle(command(store, "/thinking")) == prompts.THINKING_STATE_OFF
    await commands.handle(command(store, "/thinking on", T0 + 1))
    assert await commands.handle(command(store, "/thinking", T0 + 2)) == prompts.THINKING_STATE_ON.format(time="10:02")


async def test_thinking_on_and_off_switch_it_and_say_when_it_ends(store: Store) -> None:
    commands, switch = make(store)
    assert await commands.handle(command(store, "/thinking on")) == prompts.THINKING_ON.format(time="10:02")
    assert switch.on(ME) and switch.until(ME) == START + 1_800
    assert await commands.handle(command(store, "/Thinking OFF", T0 + 1)) == prompts.THINKING_OFF
    assert switch.on(ME) is False


async def test_thinking_on_again_starts_the_30_minutes_again(store: Store) -> None:
    clock = Clock()
    commands, switch = make(store, clock)
    await commands.handle(command(store, "/thinking on"))
    clock.now = START + 600
    assert await commands.handle(command(store, "/thinking on", T0 + 1)) == prompts.THINKING_ON.format(time="10:12")
    assert switch.until(ME) == START + 2_400


async def test_each_account_switches_and_asks_only_its_own(store: Store) -> None:
    commands, switch = make(store)
    assert await commands.handle(command(store, "/thinking on")) == prompts.THINKING_ON.format(time="10:02")
    assert await commands.handle(command_of(store, PARTNER, "/thinking")) == prompts.THINKING_STATE_OFF
    assert await commands.handle(command(store, "/thinking", T0 + 1)) == prompts.THINKING_STATE_ON.format(time="10:02")
    assert await commands.handle(command_of(store, PARTNER, "/thinking off", T0 + 1)) == prompts.THINKING_OFF
    assert switch.on(ME) and switch.on(PARTNER) is False  # their off never touched ME's
    assert await commands.handle(command_of(store, PARTNER, "/thinking on", T0 + 2)) == prompts.THINKING_ON.format(
        time="10:02"
    )
    assert await commands.handle(command(store, "/thinking off", T0 + 2)) == prompts.THINKING_OFF
    assert switch.on(ME) is False and switch.on(PARTNER)


async def test_off_while_one_of_the_askers_answers_thinks_stops_it_and_says_so(store: Store) -> None:
    """The reply is the one for a stopped answer only when the asker's own answer was thinking."""
    commands, switch = make(store)
    await commands.handle(command(store, "/thinking on"))
    await commands.handle(command_of(store, PARTNER, "/thinking on"))
    theirs = switch.begin(PARTNER)  # PARTNER's answer thinks
    assert await commands.handle(command(store, "/thinking off", T0 + 1)) == prompts.THINKING_OFF  # ME's: none thinks
    assert not theirs.is_set() and switch.on(PARTNER)
    mine = switch.begin(ME)
    await commands.handle(command(store, "/thinking on", T0 + 2))
    assert await commands.handle(command(store, "/thinking off", T0 + 3)) == prompts.THINKING_OFF_STOPPED
    assert mine.is_set() and not theirs.is_set() and switch.on(ME) is False and switch.on(PARTNER)
    assert await commands.handle(command_of(store, PARTNER, "/thinking off", T0 + 1)) == prompts.THINKING_OFF_STOPPED
    assert theirs.is_set()


# the last command typed wins: /thinking on keeps its place in the queue and /thinking off does not (owner, 4 Nov), so an
# on that waited behind an answer is handled after the off that was answered at once (review of 4 Nov on this branch, F1)


async def test_an_on_that_waited_in_the_queue_behind_an_off_answered_at_once_switches_nothing(store: Store) -> None:
    commands, switch = make(store)
    waiting = command(store, "/thinking on")  # typed first, still in the queue behind an answer
    off = command(store, "/thinking off", T0 + 1)  # typed last: answered at once, ahead of it
    assert await commands.handle(off) == prompts.THINKING_OFF
    store.set_status(off.id, "answered")  # as App.process does once it has replied
    assert await commands.handle(waiting) == prompts.THINKING_OFF  # the existing reply: it is off, as the last one said
    assert switch.on(ME) is False and store.get_state(row(ME)) is None
    again, switch_again = make(store)  # a restart queues the on again: the off is among the messages, so still seen
    assert await again.handle(waiting) == prompts.THINKING_OFF and switch_again.on(ME) is False
    last = command(store, "/thinking on", T0 + 2)  # an on typed after the off is the last command: it switches it on
    assert await commands.handle(last) == prompts.THINKING_ON.format(time="10:02") and switch.on(ME)


async def test_only_an_answered_off_of_the_same_account_typed_after_it_beats_a_waiting_on(store: Store) -> None:
    commands, switch = make(store)
    waiting = command(store, "/thinking on")
    theirs = command_of(store, PARTNER, "/thinking off", T0 + 1)  # another account's off never touches ME's
    set_aside = command(store, "/thinking off", T0 + 2)  # its own, but it was never answered
    other = command(store, "/status", T0 + 3)  # answered, but no off
    store.set_status(theirs.id, "answered")
    store.set_status(set_aside.id, "failed")
    store.set_status(other.id, "answered")
    assert await commands.handle(waiting) == prompts.THINKING_ON.format(time="10:02") and switch.on(ME)


async def test_a_waiting_on_that_lost_says_the_state_when_a_later_on_switched_it_on_again(store: Store) -> None:
    """During a call every command but a few is answered at once, so an on typed after the off can switch it on before
    the waiting one is handled: its reply is then the state (it is on), never "off"."""
    commands, switch = make(store)
    waiting = command(store, "/thinking on")
    off = command(store, "/thinking off", T0 + 1)
    await commands.handle(off)
    store.set_status(off.id, "answered")
    await commands.handle(command(store, "/thinking on", T0 + 2))  # answered at once, during a call
    assert switch.on(ME)
    assert await commands.handle(waiting) == prompts.THINKING_STATE_ON.format(time="10:02") and switch.on(ME)


async def test_a_message_number_used_again_after_a_prune_never_lets_an_old_off_beat_a_new_on(store: Store) -> None:
    """The off is read from the stored messages and no number is kept beside them: once a prune has emptied the table,
    SQLite numbers the next message 1 again, which a number remembered from the off would read as "typed before it"."""
    commands, switch = make(store)
    for n in range(4):
        command(store, "/status", T0 + n)
    off = command(store, "/thinking off", T0 + 9)  # message 5
    await commands.handle(off)
    store.set_status(off.id, "answered")
    with store.tx():
        store._conn.execute("DELETE FROM messages")
    again = command(store, "/thinking on", T0 + 10)
    assert again.id < off.id  # the number is used again
    assert await commands.handle(again) == prompts.THINKING_ON.format(time="10:02") and switch.on(ME)


async def test_where_there_is_no_room_a_waiting_on_still_says_why_and_not_that_it_is_off(store: Store) -> None:
    commands, switch = make(store, room=False)
    waiting = command(store, "/thinking on")
    off = command(store, "/thinking off", T0 + 1)
    await commands.handle(off)
    store.set_status(off.id, "answered")
    assert await commands.handle(waiting) == prompts.THINKING_NO_ROOM and switch.on(ME) is False


@pytest.mark.parametrize("typed", ["/thinking off", "/Thinking OFF", "/thinking   off", "  /thinking off  "])
def test_off_is_answered_at_once(typed: str) -> None:
    assert answered_at_once(typed)


@pytest.mark.parametrize(
    "typed", ["/thinking", "/thinking on", "/thinking off now", "/thinking offer", "/status", "/lock", "thinking off"]
)
def test_on_a_plain_thinking_and_any_other_command_keep_their_place_in_the_queue(typed: str) -> None:
    assert not answered_at_once(typed)


async def test_another_word_gets_the_usage_and_changes_nothing(store: Store) -> None:
    commands, switch = make(store)
    assert await commands.handle(command(store, "/thinking deep")) == prompts.THINKING_USAGE
    assert switch.on(ME) is False
    await commands.handle(command(store, "/thinking on", T0 + 1))
    assert await commands.handle(command(store, "/thinking maybe", T0 + 2)) == prompts.THINKING_USAGE
    assert switch.on(ME)


async def test_where_the_context_is_too_small_thinking_on_says_so_and_so_does_a_plain_thinking(store: Store) -> None:
    """Review of 4 Nov, dialogue-8: a plain /thinking answered '/thinking on: slower, deeper answers …' where /thinking on
    is refused, a reply that pointed to a dead end."""
    commands, switch = make(store, room=False)
    assert switch.room is False and DeepThinking(store, clock=Clock()).room is True
    assert await commands.handle(command(store, "/thinking on")) == prompts.THINKING_NO_ROOM
    assert switch.on(ME) is False
    assert await commands.handle(command(store, "/thinking", T0 + 1)) == prompts.THINKING_NO_ROOM
    assert "Deep Thinking: off" in await commands.handle(command(store, "/status", T0 + 2))  # /status stays "off"


async def test_where_the_config_does_not_say_the_context_it_stays_off_and_says_what_to_put_in_it(store: Store) -> None:
    """Review of 4 Nov, promises-2: with no context_tokens line (a config from before 0.9.7, or a setup that could not read
    the model's) nothing says there is room for the thought, so it never switches on; the reply names the line (the 16 GB
    reply, 'less than 32,768', would be false on a 32 GB Mac)."""
    commands, switch = make(store, room=False, known=False)
    assert switch.room is False and switch.known is False and DeepThinking(store, clock=Clock()).known is True
    assert await commands.handle(command(store, "/thinking on")) == prompts.THINKING_NO_CONTEXT
    assert switch.on(ME) is False and store.get_state(row(ME)) is None
    assert await commands.handle(command(store, "/thinking", T0 + 1)) == prompts.THINKING_NO_CONTEXT
    assert "context_tokens = 32768" in prompts.THINKING_NO_CONTEXT  # the line to put in, as the owner worded it
    assert "less than 32,768" not in prompts.THINKING_NO_CONTEXT
    assert "Deep Thinking: off" in await commands.handle(command(store, "/status", T0 + 2))


async def test_without_a_switch_it_is_an_unknown_command_as_tools_is_without_a_list(store: Store) -> None:
    profile = Profile(store)
    bare = Commands(store, profile, ContextBuilder(store, profile, TZ), TZ, status, lambda: None)
    assert (await bare.handle(command(store, "/thinking on"))).startswith("Unknown command /thinking.")


def test_no_tool_of_hers_switches_it(tmp_path: Path) -> None:
    """Typed only, like /babble: Gemma has no tool for it (every part on, as the eval harness has them)."""
    from evals.gemma_suite import Harness
    from tests.fakes.fake_gemma import FakeGemma

    harness = Harness(tmp_path / "eval", FakeGemma())
    assert "thinking" not in json.dumps(harness.tools.schemas).casefold()


# the words (owner's copy of 4 Nov, one switch for each allowed account; approved: her replies and /help speak as
# "I", like her other command replies, while README, GUIDE, CHANGELOG and setup_assistant.md say "she")


def test_the_replies_are_the_words_of_the_copy_file() -> None:
    assert prompts.THINKING_ON == (
        "Deep Thinking is on for your texts and voice notes until {time}: I think before I answer, so an answer can "
        "take a few minutes, the first one longer. Not on calls. Off by itself after 30 minutes without a message from "
        "you; /thinking off stops it sooner."
    )
    assert prompts.THINKING_OFF == "Deep Thinking is off."
    assert prompts.THINKING_OFF_STOPPED == "Deep Thinking is off. The answer on its way comes without it."
    assert prompts.THINKING_STATE_ON == (
        "Deep Thinking is on for your texts and voice notes until {time}. /thinking off stops it."
    )
    assert prompts.THINKING_STATE_OFF == (
        "Deep Thinking is off. /thinking on: I think before I answer your texts and voice notes, off by itself after "
        "30 minutes without a message from you."
    )
    assert (
        prompts.THINKING_NO_ROOM
        == "Deep Thinking stays off on this Mac: it runs only on Macs with 24 GB of memory or more."
    )
    assert prompts.THINKING_NO_CONTEXT == (
        "Deep Thinking stays off here: her config does not say how much of a conversation her model keeps. If doctor "
        "shows no warning for Model context, add context_tokens = 32768 at the top of her config and restart her."
    )
    assert (prompts.THINKING_STATUS_ON, prompts.THINKING_STATUS_OFF) == (
        "Deep Thinking: on until {time}",
        "Deep Thinking: off",
    )
    assert (
        "\n/thinking - Deep Thinking: I think before I answer your texts and voice notes (/thinking on, "
        "/thinking off; off by itself after 30 minutes without a message from you)\n" in prompts.HELP
    )
    for words in (prompts.THINKING_ON, prompts.THINKING_STATE_ON, prompts.THINKING_STATE_OFF, prompts.HELP):
        assert "deeper" not in words and "tokens of a conversation" not in words  # no "deeper", no token counts
    for words in (prompts.THINKING_ON, prompts.THINKING_STATE_OFF, prompts.HELP):
        assert "she thinks" not in words and "I think before I answer" in words  # her replies say "I", the docs "she"


# /status and /help


async def test_status_shows_the_askers_own_state_only(store: Store) -> None:
    commands, _ = make(store)
    await commands.handle(command(store, "/thinking on"))
    mine = await commands.handle(command(store, "/status", T0 + 1))
    theirs = await commands.handle(command_of(store, PARTNER, "/status"))
    assert mine.split("\n\n")[0].splitlines()[-1] == prompts.THINKING_STATUS_ON.format(time="10:02")
    assert theirs.split("\n\n")[0].splitlines()[-1] == prompts.THINKING_STATUS_OFF
    await commands.handle(command_of(store, PARTNER, "/thinking on", T0 + 1))
    await commands.handle(command(store, "/thinking off", T0 + 2))
    assert "Deep Thinking: off" in await commands.handle(command(store, "/status", T0 + 3))
    assert "Deep Thinking: on until 10:02" in await commands.handle(command_of(store, PARTNER, "/status", T0 + 2))


async def test_status_shows_it_in_the_running_section(store: Store) -> None:
    commands, _ = make(store)
    off = await commands.handle(command(store, "/status"))
    assert off.split("\n\n")[0].splitlines()[-1] == prompts.THINKING_STATUS_OFF == "Deep Thinking: off"
    await commands.handle(command(store, "/thinking on", T0 + 1))
    on = await commands.handle(command(store, "/status", T0 + 2))
    assert on.split("\n\n")[0].splitlines()[-1] == prompts.THINKING_STATUS_ON.format(time="10:02")
    assert "Deep Thinking: on until 10:02" in on
    profile = Profile(store)  # without a switch: no line (the commands the tests build bare)
    bare = Commands(store, profile, ContextBuilder(store, profile, TZ), TZ, status, lambda: None)
    assert "Deep Thinking" not in await bare.handle(command(store, "/status", T0 + 3))


async def test_help_lists_the_command_under_conversation(store: Store) -> None:
    commands, _ = make(store)
    assert "\n/thinking - " in prompts.HELP
    conversation = prompts.HELP.split("\n\n")[0]
    assert conversation.startswith("**Conversation**") and "\n/thinking - " in conversation
    assert await commands.handle(command(store, "/help")) == prompts.HELP


def test_a_command_in_a_spoken_answer_is_said_as_slash_thinking() -> None:
    from local_assistant import voice

    assert "thinking" in voice.COMMANDS
    assert voice.spoken_text("Try /thinking on for that.") == "Try slash thinking on for that."

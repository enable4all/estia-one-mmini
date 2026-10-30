"""The quiet-time note and /profile new (0.9.8 P4, the copy approved 26 Oct): after a learning pass, her next reply's
note names at most three of the notes it added or updated, by number and whole; /profile new lists all of the last
quiet time's changes, each note as it is now (owner, 26 Oct: "get the /profile new to only display what happened in the
last quiet time... no complexity")."""

from __future__ import annotations

import json
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant.context import ContextBuilder, without_fake_notices
from local_assistant.profile import QUIET_KEY, Profile, quiet_note
from local_assistant.profiler import Profiler
from local_assistant.store import Store
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text
from tests.test_profile_onboarding import command, commands_for, notes

CLOSING = "In my notes from our chats, not in your facts: /profile notes shows them all."
FOOTER = "Correct one with /fix N new text, or remove it with /forget N."
NOTHING_NEW = "No quiet-time notes yet. /profile notes shows my notes from our chats."
NURSE, CAT, TALL, DUTCH = (
    "Work: The user is a nurse.",
    "Home: The user has a cat.",
    "Other: The user is tall.",
    "Study: The user learns Dutch.",
)


def add(words: str) -> dict[str, Any]:
    return {"action": "add", "text": words}


async def learn(store: Store, profile: Profile, *operations: dict[str, Any], ts: int = T0) -> None:
    """One learning pass over a new message of the user's, Gemma answering with `operations` (prompts.OPS_SCHEMA)."""
    said = store.add_incoming(store.current_conversation().id, ME, ts, "text", f"about me, {ts}")
    full = [{"fact_id": 0, "text": "", "reason": "test", "source_message_id": said, **op} for op in operations]
    gemma = FakeGemma(text(json.dumps({"operations": full})))
    await Profiler(store, profile, ContextBuilder(store, profile, TZ), gemma, TZ).learn()


# the quiet-time note


async def test_a_learning_pass_is_named_in_the_next_replys_note(store: Store) -> None:
    profile = Profile(store)
    [son] = notes(profile, "Family: The user's son Nikos is 8.")
    await learn(store, profile, add(NURSE), {"action": "replace", "fact_id": son, "text": "Family: Nikos is 9."})
    assert quiet_note(profile.tell_quiet()) == (
        f"**Noted while we were quiet**\nUpdated #{son}: Family: Nikos is 9.\nAdded #{son + 1}: {NURSE}\n{CLOSING}"
    )
    assert quiet_note(profile.tell_quiet()) == ""  # told once


async def test_the_note_names_at_most_three_whole_and_counts_the_rest(store: Store) -> None:
    profile = Profile(store)
    piano = "Music: The user plays the piano " + "and sings " * 25 + "on Sundays."
    await learn(store, profile, add(piano), add(NURSE), add(CAT), add(TALL), add(DUTCH))
    assert quiet_note(profile.tell_quiet()).splitlines() == [
        "**Noted while we were quiet**",
        f"Added #1: {piano}",  # whole, never cut
        f"Added #2: {NURSE}",
        f"Added #3: {CAT}",
        "…and 2 more: /profile new",
        CLOSING,
    ]


async def test_three_notes_need_no_more_line(store: Store) -> None:
    profile = Profile(store)
    await learn(store, profile, add(NURSE), add(CAT), add(TALL))
    assert quiet_note(profile.tell_quiet()).splitlines()[-2:] == [f"Added #3: {TALL}", CLOSING]


async def test_a_note_gone_by_the_reply_is_neither_named_nor_counted(store: Store) -> None:
    profile = Profile(store)
    await learn(store, profile, add(NURSE), add(CAT), add(TALL), add(DUTCH))
    profile.forget(2)
    assert quiet_note(profile.tell_quiet()).splitlines()[1:] == [
        f"Added #1: {NURSE}",
        f"Added #3: {TALL}",
        f"Added #4: {DUTCH}",
        CLOSING,
    ]


async def test_a_pass_that_only_removed_sends_no_note(store: Store) -> None:
    """A removal is no new thing (review O2): the note counts added and updated only; /profile new lists it."""
    profile = Profile(store)
    [old] = notes(profile, "Home: The user lives in Patras.")
    await learn(store, profile, {"action": "remove", "fact_id": old})
    assert quiet_note(profile.tell_quiet()) == ""
    assert profile.tell_quiet() is None  # nothing to tell: it joined the told (here empty) quiet time


def test_nothing_learned_tells_nothing(store: Store) -> None:
    assert Profile(store).tell_quiet() is None


async def test_a_note_made_theirs_by_remember_is_not_named_as_hers(store: Store) -> None:
    """A "remember" of a note's exact words makes it theirs (same number): the note "In my notes from our chats, not in
    your facts" never names it; /profile new still lists it, as it is now."""
    profile, commands = commands_for(store)
    await learn(store, profile, add(NURSE))
    profile.add(NURSE, source_kind="remember")
    assert [fact.id for fact in profile.yours()] == [1]
    assert quiet_note(profile.tell_quiet()) == ""
    assert f"\nAdded #1: {NURSE}\n" in await commands.handle(command(store, "/profile new", T0 + 1))


async def test_a_note_fixed_before_the_reply_is_not_named_as_hers(store: Store) -> None:
    """/fix makes a note theirs: the next reply's note leaves it out and counts only her notes."""
    profile, commands = commands_for(store)
    await learn(store, profile, add(NURSE), add(CAT), add(TALL), add(DUTCH))
    await commands.handle(command(store, "/fix 1 The user is a doctor.", T0 + 1))
    assert quiet_note(profile.tell_quiet()).splitlines()[1:] == [
        f"Added #2: {CAT}",
        f"Added #3: {TALL}",
        f"Added #4: {DUTCH}",
        CLOSING,
    ]
    assert "\nAdded #1: Work: The user is a doctor.\n" in await commands.handle(command(store, "/profile new", T0 + 2))


def test_her_imitation_of_the_quiet_time_notes_is_left_out() -> None:
    for forged in ("**Noted while we were quiet**\nAdded #9: x", "Noted in the last quiet time\nRemoved: y"):
        assert without_fake_notices(f"Sure.\n\n{forged}") == "Sure."


# /profile new


async def test_profile_new_with_no_quiet_time_yet(store: Store) -> None:
    _, commands = commands_for(store)
    assert await commands.handle(command(store, "/profile new")) == NOTHING_NEW
    assert await commands.handle(command(store, "/profile  NEW ", T0 + 1)) == NOTHING_NEW


async def test_profile_new_lists_all_of_the_last_passs_changes(store: Store) -> None:
    profile, commands = commands_for(store)
    son, city = notes(profile, "Family: The user's son Nikos is 8.", "Home: The user lives in Patras.")
    profile.add("About: The user goes by Alex.", source_kind="remember")  # theirs: never in a quiet time
    added = [NURSE, CAT, TALL, DUTCH]
    replace = {"action": "replace", "fact_id": son, "text": "Family: The user's son Nikos is 9."}
    await learn(store, profile, *map(add, added), replace, {"action": "remove", "fact_id": city})
    assert await commands.handle(command(store, "/profile new", T0 + 1)) == "\n".join(
        [
            "**Noted in the last quiet time**",
            f"Updated #{son}: Family: The user's son Nikos is 9.",
            *(f"Added #{4 + n}: {words}" for n, words in enumerate(added)),
            "Removed: Home: The user lives in Patras.",
            FOOTER,
        ]
    )


async def test_profile_new_shows_each_note_as_it_is_now(store: Store) -> None:
    """A note fixed since shows its new words; a forgotten one is left out."""
    profile, commands = commands_for(store)
    await learn(store, profile, add(NURSE), add(CAT), add(TALL))
    assert await commands.handle(command(store, "/fix 1 The user is a doctor.", T0 + 1)) == (
        "Fixed: #1 Work: The user is a doctor."
    )
    await commands.handle(command(store, "/forget 2", T0 + 2))
    assert await commands.handle(command(store, "/profile new", T0 + 3)) == "\n".join(
        ["**Noted in the last quiet time**", "Added #1: Work: The user is a doctor.", f"Added #3: {TALL}", FOOTER]
    )


async def test_profile_new_shows_only_the_last_quiet_time(store: Store) -> None:
    """Once its note went out with a reply, the next pass starts a new quiet time; until then passes add up, as the
    note names them all."""
    profile, commands = commands_for(store)
    await learn(store, profile, add(NURSE))
    await learn(store, profile, add(CAT), ts=T0 + 1)
    both = await commands.handle(command(store, "/profile new", T0 + 2))
    assert f"\nAdded #1: {NURSE}\nAdded #2: {CAT}\n" in both
    assert profile.tell_quiet() is not None  # the next reply's note: this quiet time is told
    await learn(store, profile, add(TALL), ts=T0 + 3)
    assert await commands.handle(command(store, "/profile new", T0 + 4)) == "\n".join(
        ["**Noted in the last quiet time**", f"Added #3: {TALL}", FOOTER]
    )


async def test_a_pass_that_only_removes_joins_the_told_quiet_time(store: Store) -> None:
    """After its note went out, a pass that only removes sends no note of its own: it joins the told quiet time, so
    /profile new still lists what that note was about, then the removal (review: dialogue-1)."""
    profile, commands = commands_for(store)
    [city] = notes(profile, "Home: The user lives in Patras.")
    await learn(store, profile, add(NURSE), add(CAT), add(TALL), add(DUTCH))
    assert profile.tell_quiet() is not None
    await learn(store, profile, {"action": "remove", "fact_id": city}, ts=T0 + 1)
    assert profile.tell_quiet() is None  # nothing new to tell
    assert await commands.handle(command(store, "/profile new", T0 + 2)) == "\n".join(
        [
            "**Noted in the last quiet time**",
            *(f"Added #{2 + n}: {words}" for n, words in enumerate([NURSE, CAT, TALL, DUTCH])),
            "Removed: Home: The user lives in Patras.",
            FOOTER,
        ]
    )


async def test_a_told_note_removed_later_is_listed_as_removed(store: Store) -> None:
    """Its note named it, so a later pass's removal of it shows as "Removed:", never as nothing."""
    profile, commands = commands_for(store)
    await learn(store, profile, add(NURSE), add(CAT))
    assert profile.tell_quiet() is not None
    await learn(store, profile, {"action": "remove", "fact_id": 1}, ts=T0 + 1)
    assert await commands.handle(command(store, "/profile new", T0 + 2)) == "\n".join(
        ["**Noted in the last quiet time**", f"Added #2: {CAT}", f"Removed: {NURSE}", FOOTER]
    )


async def test_a_pass_that_changes_nothing_leaves_the_told_quiet_time_as_it_was(store: Store) -> None:
    profile, commands = commands_for(store)
    await learn(store, profile, add(NURSE))
    assert profile.tell_quiet() is not None
    await learn(store, profile, add(NURSE), ts=T0 + 1)  # already a note: nothing written
    assert profile.tell_quiet() is None
    assert await commands.handle(command(store, "/profile new", T0 + 2)) == "\n".join(
        ["**Noted in the last quiet time**", f"Added #1: {NURSE}", FOOTER]
    )


async def test_a_replace_in_the_same_words_changes_nothing_and_is_never_named(store: Store) -> None:
    """Fable, 27 Oct: a replace that repeats a note's words was named "Updated #N" in the note and in /profile new;
    nothing changed, so nothing is written or named (case, spacing and a last full stop aside, as a duplicate)."""
    profile, commands = commands_for(store)
    [son] = notes(profile, "Family: The user's son Nikos is 8.")
    same = {"action": "replace", "fact_id": son, "text": "Family: The user's son Nikos is 8."}
    await learn(store, profile, same)
    assert profile.tell_quiet() is None
    assert await commands.handle(command(store, "/profile new", T0 + 1)) == NOTHING_NEW
    assert [change.action for change in store.changes(son)] == ["add"]
    assert profile.facts()[0].text == "Family: The user's son Nikos is 8."
    alike = {"action": "replace", "fact_id": son, "text": "family: the user's son  Nikos is 8"}
    await learn(store, profile, add(NURSE), alike, ts=T0 + 2)  # beside a real change: only that one is named
    assert quiet_note(profile.tell_quiet()) == f"**Noted while we were quiet**\nAdded #{son + 1}: {NURSE}\n{CLOSING}"


async def test_a_note_changed_and_changed_back_in_one_pass_is_not_named(store: Store) -> None:
    profile = Profile(store)
    [son] = notes(profile, "Family: The user's son Nikos is 8.")
    away = {"action": "replace", "fact_id": son, "text": "Family: The user's son Nikos is 9."}
    back = {"action": "replace", "fact_id": son, "text": "Family: The user's son Nikos is 8."}
    await learn(store, profile, away, back)
    assert profile.tell_quiet() is None and profile.facts()[0].text == "Family: The user's son Nikos is 8."


async def test_a_note_added_then_updated_in_one_quiet_time_is_one_added_line_in_its_new_words(store: Store) -> None:
    profile, commands = commands_for(store)
    await learn(store, profile, add(NURSE))
    await learn(
        store, profile, {"action": "replace", "fact_id": 1, "text": "Work: The user is a night nurse."}, ts=T0 + 1
    )
    assert await commands.handle(command(store, "/profile new", T0 + 2)) == "\n".join(
        ["**Noted in the last quiet time**", "Added #1: Work: The user is a night nurse.", FOOTER]
    )


async def test_a_note_updated_then_removed_in_one_quiet_time_is_listed_as_removed(store: Store) -> None:
    profile, commands = commands_for(store)
    [son] = notes(profile, "Family: The user's son Nikos is 8.")
    await learn(store, profile, {"action": "replace", "fact_id": son, "text": "Family: The user's son Nikos is 9."})
    await learn(store, profile, {"action": "remove", "fact_id": son}, ts=T0 + 1)
    assert await commands.handle(command(store, "/profile new", T0 + 2)) == "\n".join(
        [
            "**Noted in the last quiet time**",
            "Removed: Family: The user's son Nikos is 9.",
            "/profile notes shows my notes from our chats.",
        ]
    )


async def test_profile_new_with_only_removals_offers_no_fix(store: Store) -> None:
    """No line has a number, so the closing points to /profile notes, never to /fix N or /forget N (review: dialogue-6)."""
    profile, commands = commands_for(store)
    [city] = notes(profile, "Home: The user lives in Patras.")
    await learn(store, profile, {"action": "remove", "fact_id": city})
    reply = await commands.handle(command(store, "/profile new", T0 + 1))
    assert reply == "\n".join(
        [
            "**Noted in the last quiet time**",
            "Removed: Home: The user lives in Patras.",
            "/profile notes shows my notes from our chats.",
        ]
    )
    assert "/fix" not in reply and "/forget" not in reply


async def test_the_users_forget_clears_the_removed_words_of_the_last_quiet_time(store: Store) -> None:
    """A merge's removed note keeps its words in the change log only to undo it; once the user forgets a fact, /profile
    new no longer shows those words, nor brings them into her history (review: code-4)."""
    profile, commands = commands_for(store)
    nurse, nights = notes(profile, NURSE, "Work: The user works nights.")
    merged = {"action": "replace", "fact_id": nurse, "text": "Work: The user is a nurse on nights."}
    await learn(store, profile, merged, {"action": "remove", "fact_id": nights})
    assert "\nRemoved: Work: The user works nights.\n" in await commands.handle(command(store, "/profile new", T0 + 1))
    await commands.handle(command(store, f"/forget {nurse}", T0 + 2))
    assert await commands.handle(command(store, "/profile new", T0 + 3)) == NOTHING_NEW


async def test_gemmas_forget_keeps_the_removed_words_of_the_last_quiet_time(store: Store) -> None:
    """Only the user's own /forget blanks: a removal by Gemma keeps the words for undo, and the list stays."""
    profile, commands = commands_for(store)
    [city] = notes(profile, "Home: The user lives in Patras.")
    await learn(store, profile, add(NURSE), {"action": "remove", "fact_id": city})
    profile.forget(city + 1, blank_log=False)  # the nurse note
    assert await commands.handle(command(store, "/profile new", T0 + 1)) == "\n".join(
        [
            "**Noted in the last quiet time**",
            "Removed: Home: The user lives in Patras.",
            "/profile notes shows my notes from our chats.",
        ]
    )


async def test_a_note_added_and_removed_in_one_quiet_time_is_not_listed(store: Store) -> None:
    profile, commands = commands_for(store)
    await learn(store, profile, add(NURSE))
    await learn(store, profile, {"action": "remove", "fact_id": 1}, ts=T0 + 1)
    assert await commands.handle(command(store, "/profile new", T0 + 2)) == NOTHING_NEW


async def test_a_removal_outside_learning_is_no_removed_line(store: Store) -> None:
    """ "Removed:" only for a note the pass itself took out: /forget leaves the note out, with no line."""
    profile, commands = commands_for(store)
    await learn(store, profile, add(NURSE))
    await commands.handle(command(store, "/forget 1", T0 + 1))
    assert await commands.handle(command(store, "/profile new", T0 + 2)) == NOTHING_NEW


async def test_a_broken_record_reads_as_no_quiet_time(store: Store) -> None:
    _, commands = commands_for(store)
    broken = ("not json", '{"noted": "x"}', "[1, 2]", '{"noted": [["add", "1"]], "removed": [], "told": false}')
    for n, kept in enumerate(broken):
        store.set_state(QUIET_KEY, kept)
        assert await commands.handle(command(store, "/profile new", T0 + n)) == NOTHING_NEW, kept


# the texts around them


def test_help_names_profile_new() -> None:
    assert (
        "/profile - what I keep in mind about you (/profile notes: my notes from our chats; /profile new: what I noted "
        "in the last quiet time)"
    ) in prompts.HELP.splitlines()


@pytest.mark.parametrize("words", ["/profile notes", "/profile please"])
async def test_profile_notes_and_other_words_answer_as_before(store: Store, words: str) -> None:
    profile, commands = commands_for(store)
    profile.add("About: The user goes by Alex.", source_kind="remember")
    notes(profile, NURSE)
    reply = await commands.handle(command(store, words))
    assert not reply.startswith("**Noted in the last quiet time**") and reply != NOTHING_NEW

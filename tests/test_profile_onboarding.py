"""Profile onboarding (0.9.8 P4, the copy approved 26 Oct): the welcome's memory line and the empty /profile's guide,
with its home area line only when places are on and no home area is known (owner, 26 Oct: answers 3 and 4)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant.commands import Commands, StatusInfo
from local_assistant.context import ContextBuilder
from local_assistant.maps import Maps
from local_assistant.profile import Op, Profile
from local_assistant.store import Message, Store
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma
from tests.fakes.fake_http import FakeHttp

MEMORY = '• Memory: "remember that my sister is Anna" (/profile shows what I keep in mind)'
EMPTY = (
    '**Your profile is empty.** I keep your facts in mind with every message. Say "remember that ...": my "Saved" note '
    "confirms each one."
)
NONE_YET = 'Nothing of yours yet. Say "remember that ...": my "Saved" note confirms each one.'
WORTH = (
    'Worth telling: what I call you, your work or study, people close to you ("Maria (wife), Nikos (son, 9)"), '
    'interests, your usual day, how you like answers ("short answers before noon").'
)
HOME = (
    'Home area, for "near me": "remember that my home area is Example Street, Town". Street and city are enough, no '
    "house number."
)
LIMITS = "Up to 300 characters a fact, 4,000 for all of yours. Kept on this Mac."
ALSO = "I also note facts from our chats: /profile notes shows them."
NURSE = "Work: The user is a nurse."


async def status() -> StatusInfo:
    return StatusInfo(
        gemma="loaded", queue_length=0, last_learning_run=None, db_bytes=1_000_000, messages=3, restarts=0
    )


def commands_for(store: Store, profile: Profile | None = None, **options: Any) -> tuple[Profile, Commands]:
    profile = profile or Profile(store)
    return profile, Commands(store, profile, ContextBuilder(store, profile, TZ), TZ, status, lambda: None, **options)


def command(store: Store, words: str, ts: int = T0) -> Message:
    message_id = store.add_incoming(store.current_conversation().id, ME, ts, "command", words)
    assert message_id is not None
    return store.message(message_id)


def notes(profile: Profile, *texts: str) -> list[int]:
    """Her notes from chats, as a learning pass wrote them."""
    return profile.apply([Op("add", text=words) for words in texts], source_kind="chat").added


# the welcome's memory line


def test_the_welcome_invites_remember_first_whatever_is_set_up() -> None:
    """Memory is always on: its line is the first under "Set up and ready to try", also when nothing else is set up."""
    for welcome in (prompts.welcome(name="Estia"), prompts.welcome(name="Estia", mail=True, reminders=True)):
        lines = welcome.splitlines()
        assert lines[lines.index("**Set up and ready to try**") + 1] == MEMORY


def test_the_whole_welcome_reads_as_approved() -> None:
    welcome = prompts.welcome(
        name="Estia", voice=True, calls=True, web=True, maps=True, reminders=True, wake_calls=True
    )
    assert welcome.startswith(
        "Hi, I'm Estia, your private thinking partner on your own Mac. Reply with anything to try me.\n"
        "\n"
        "**Set up and ready to try**\n"
        f"{MEMORY}\n"
        "• Voice notes: send one instead of typing.\n"
        "• Calls: call me on Signal and just talk.\n"
        '• Web search: "what\'s the weather tomorrow?"\n'
        '• Places: "a good cafe near me?"\n'
        '• Reminders: "remind me at 17:00 to call mum", "call me at 7:00 to wake me up" (a reminder is set only when my '
        '"Reminder set" note says so)\n'
    )


# the empty /profile's guide


async def test_an_empty_profile_shows_the_guide_without_the_home_line_when_places_are_off(store: Store) -> None:
    _, commands = commands_for(store)
    assert await commands.handle(command(store, "/profile")) == "\n".join([EMPTY, WORTH, LIMITS, ALSO])


async def test_with_places_on_and_no_home_area_the_guide_names_the_home_area(store: Store) -> None:
    _, commands = commands_for(store, has_home=lambda: False)
    assert await commands.handle(command(store, "/profile")) == "\n".join([EMPTY, WORTH, HOME, LIMITS, ALSO])


async def test_a_known_home_area_leaves_the_home_line_out(store: Store) -> None:
    """Owner, 26 Oct (answer 4): a home area from setup or the config is known, so the line is left out."""
    _, commands = commands_for(store, has_home=lambda: True)
    assert await commands.handle(command(store, "/profile")) == "\n".join([EMPTY, WORTH, LIMITS, ALSO])


async def test_the_guide_takes_its_limits_from_the_config(store: Store) -> None:
    _, commands = commands_for(store, Profile(store, max_chars=2_500, fact_max_chars=200))
    reply = await commands.handle(command(store, "/profile"))
    assert "\nUp to 200 characters a fact, 2,500 for all of yours. Kept on this Mac.\n" in reply


async def test_with_only_her_notes_the_guide_replaces_nothing_of_yours_yet(store: Store) -> None:
    profile, commands = commands_for(store, has_home=lambda: False)
    notes(profile, NURSE)
    assert await commands.handle(command(store, "/profile")) == (
        "**Your profile**: I see it with every message (0 of 4,000 characters, 0%)\n"
        "\n" + "\n".join([NONE_YET, WORTH, HOME, LIMITS]) + "\n"
        "\n"
        f"**My notes from our chats**: 1 note ({len(NURSE)} of 12,000 characters, 0%). I see up to 1,500 characters of "
        "them with each message, the ones that match it first, then the newest. /profile notes lists them.\n"
        "\n" + prompts.PROFILE_FOOTER
    )


async def test_a_profile_with_a_fact_of_yours_shows_no_guide(store: Store) -> None:
    """Owner, 26 Oct (answer 3): no guide once the user's part holds a fact (setup's About you facts are theirs)."""
    profile, commands = commands_for(store, has_home=lambda: False)
    profile.add("About: The user goes by Alex.", source_kind="remember")
    reply = await commands.handle(command(store, "/profile"))
    assert reply.startswith("**Your profile**: I see it with every message (")
    for line in (WORTH, HOME, LIMITS, NONE_YET):
        assert line not in reply


def test_maps_know_a_home_area_from_the_users_fact_or_the_config(tmp_path: Path, store: Store) -> None:
    """The guide's home line asks Maps (`has_home`): the user's own "Home area:" fact, else the config's home (setup
    writes it there)."""
    fact = {"area": ""}
    built = Maps(FakeHttp(), tmp_path / "web.env", store, home="", daily_cap=30, tz=TZ, home_fact=lambda: fact["area"])
    assert not built.has_home()
    fact["area"] = "Example Street, Town"
    assert built.has_home()
    assert Maps(FakeHttp(), tmp_path / "web.env", store, home="Config Road, Town", daily_cap=30, tz=TZ).has_home()


@pytest.mark.parametrize(
    ("settings", "shown"),
    [
        ({}, False),  # places off
        ({"maps_enabled": True, "maps_home": ""}, True),
        ({"maps_enabled": True, "maps_home": "Config Road, Town"}, False),  # setup's or the config's home
    ],
)
async def test_the_bridge_asks_its_maps_for_the_home_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: dict[str, Any], shown: bool
) -> None:
    from tests.test_app import deliver, sent, start, stop, until

    now = int(time.time() * 1000)  # tests.test_app.T0 is the suite's start: a long run made it an offline message

    harness = await start(tmp_path, monkeypatch, FakeGemma(), web_keys_path=tmp_path / "web.env", **settings)
    try:
        deliver(harness.folder, "/profile", now)
        await until(lambda: len(sent(harness.folder)) == 1)
        [reply] = sent(harness.folder)
        assert reply.startswith("Your profile is empty.") and (HOME in reply) is shown
    finally:
        await stop(harness)

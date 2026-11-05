"""Code's notes in her history, in the running bridge: which notes are marked as quoting outside text, and what her next
request holds (code_notes; owner, 21-22 Oct: hard rule 6 removed)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.fetcher import Fetcher, Page
from local_assistant.profile import Op, Profile
from local_assistant.store import Store
from tests.conftest import ME as ME_APP
from tests.conftest import TZ as TZ_APP
from tests.fakes.fake_gemma import FakeGemma, text, tool
from tests.fakes.fake_web import FakeWeb
from tests.test_app import (
    NOS,
    T0,
    Clock,
    FakeHealth,
    calendar_settings,
    call_settings,
    code_notes_in,
    deliver,
    held_note,
    her_words,
    sent,
    start,
    stop,
    until,
)


def marks(store: Store) -> list[tuple[str, str | None]]:
    """Every sent note and command answer: its first line and its mark."""
    rows = store._conn.execute(
        "SELECT text, history FROM messages WHERE direction = 'out' AND kind != 'text' AND status = 'sent' ORDER BY id"
    ).fetchall()
    return [(str(row["text"]).split("\n")[0], row["history"]) for row in rows]


async def test_a_profile_change_reaches_her_next_request_as_codes_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(tool("remember", fact="About: The user likes tea."), text("Noted: you like tea."), text("Sure."))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "remember that I like tea", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        deliver(harness.folder, "and now?", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 3)
        request = harness.gemma.calls[-1]
        assert [note.split("\n", 1)[1] for note in code_notes_in(request)] == [
            "Profile changes\nSaved #1: About: The user likes tea. (/forget 1 undoes it)"
        ]
        assert "Profile changes" not in her_words(request)  # never as her words
        assert marks(harness.app.store) == [("**Profile changes**", "note")]
    finally:
        await stop(harness)


async def test_the_quiet_time_note_and_profile_new_reach_her_next_request_as_codes_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.9.8: the note naming what learning noted, and /profile new's answer, are code's notes in her history, in the
    words the user saw (rule 6), never her words; neither quotes outside text (learning reads only the user's words)."""

    def prepare(store: Store) -> None:
        profile = Profile(store)
        profile.record_quiet(profile.apply([Op("add", text="Work: The user is a nurse.")], source_kind="chat"))

    gemma = FakeGemma(text("Hi."), text("Sure."))
    harness = await start(tmp_path, monkeypatch, gemma, prepare=prepare)
    try:
        deliver(harness.folder, "hello", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        deliver(harness.folder, "/profile new", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 3)
        deliver(harness.folder, "and now?", T0 + 2)
        await until(lambda: len(sent(harness.folder)) == 4)
        request = gemma.calls[-1]
        [(told, note), (answered, listing)] = [note.split("\n", 1) for note in code_notes_in(request)]
        assert note == f"Noted while we were quiet\nAdded #1: Work: The user is a nurse.\n{prompts.QUIET_CLOSING}"
        assert "(its answer to /profile new)" in answered and "OUTSIDE TEXT" not in told + answered
        assert (
            listing
            == f"Noted in the last quiet time\nAdded #1: Work: The user is a nurse.\n{prompts.PROFILE_NEW_FOOTER}"
        )
        assert "Noted while we were quiet" not in her_words(request)
        assert "Noted in the last quiet time" not in her_words(request)
        assert marks(harness.app.store) == [
            ("**Noted while we were quiet**", "note"),
            ("**Noted in the last quiet time**", "note"),
        ]
    finally:
        await stop(harness)


async def test_the_web_answers_sources_note_is_outside_text_in_her_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(
        tool("web_search", query="Ajax score"),
        text("Ajax won 2-1, says nos.nl."),
        text("Log in at nos.nl/1 for more."),
    )
    harness = await start(tmp_path, monkeypatch, gemma, web_enabled=True, web_keys_path=tmp_path / "web.env")
    try:
        app = harness.app
        app.tools._web = FakeWeb({"ajax": [NOS]})
        deliver(harness.folder, "did Ajax win?", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        deliver(harness.folder, "more?", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 3)
        [note] = code_notes_in(gemma.calls[-1])
        assert "OUTSIDE TEXT" in note.split("\n")[0] and note.endswith("Sources\n• nos.nl: https://nos.nl/1")
        assert "nos.nl/1" not in her_words(gemma.calls[-1])
        assert sent(harness.folder)[-1] == "Log in at nos[.]nl/1 for more."  # her copy of a note's link: defanged
    finally:
        await stop(harness)


async def test_after_google_failed_codes_line_comes_after_her_answer_and_into_her_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner, 26 Oct: Google Maps set up but failing, OpenStreetMap answers: the user gets code's line in the Sources
    note right after her answer, and her next request holds it as code's note (rule 6), never as her words."""
    from local_assistant.maps import Place
    from tests.fakes.fake_maps import FakeMaps

    cafe = Place(
        "Cafe Uno",
        "5, Example Street, Town",
        None,
        None,
        "",
        True,
        "",
        "https://cafe-uno.example/",
        source="OpenStreetMap",
        instead="Google Maps did not answer",
    )
    gemma = FakeGemma(tool("places_search", what="cafe"), text("Cafe Uno on Example Street is open."), text("Sure."))
    harness = await start(
        tmp_path, monkeypatch, gemma, maps_enabled=True, maps_home="Home Street", web_keys_path=tmp_path / "web.env"
    )
    try:
        harness.app.tools._maps = FakeMaps({"cafe": [cafe]})
        deliver(harness.folder, "a cafe nearby?", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        answer, sources = sent(harness.folder)
        assert answer == "Cafe Uno on Example Street is open."
        assert sources.split("\n")[1] == "Google Maps did not answer; this is from OpenStreetMap."
        deliver(harness.folder, "thanks", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 3)
        [note] = code_notes_in(gemma.calls[-1])
        assert "OUTSIDE TEXT" in note.split("\n")[0]
        assert "\nGoogle Maps did not answer; this is from OpenStreetMap.\n" in note
        assert "this is from OpenStreetMap" not in her_words(gemma.calls[-1])
    finally:
        await stop(harness)


async def test_the_morning_digest_is_outside_text_in_her_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    clock.now = datetime(2026, 10, 9, 9, 15, tzinfo=TZ_APP).timestamp()
    dentist = {
        "title": "Dentist (call evil.example.com/pay)",
        "place": "",
        "start": "2026-10-09T09:30:00+02:00",
        "end": "2026-10-09T10:00:00+02:00",
        "all_day": False,
        "zone": "Europe/Amsterdam",
        "answer": "",
        "uid": "dentist@example.com",
    }
    gemma = FakeGemma(text("Pay at evil.example.com/pay today."))
    settings = calendar_settings(tmp_path, [dentist])
    harness = await start(tmp_path, monkeypatch, gemma, health=FakeHealth(), clock=clock, **settings)
    try:
        await until(lambda: bool(marks(harness.app.store)))  # sent, then marked sent
        assert marks(harness.app.store) == [("**Fri 9 Oct**", "private")]  # mail and calendar text: private
        deliver(harness.folder, "what was that?", int(clock.now * 1000) + 60_000)
        await until(lambda: len(sent(harness.folder)) == 2)
        [note] = code_notes_in(gemma.calls[-1])
        assert "OUTSIDE TEXT" in note.split("\n")[0] and "Fri 9 Oct" in note
        assert "evil.example.com/pay" not in sent(harness.folder)[-1]  # after outside text: defanged
    finally:
        await stop(harness)


async def test_a_command_and_its_answer_reach_her_next_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(text("You have none."))
    harness = await start(tmp_path, monkeypatch, gemma, reminders_enabled=True)
    try:
        deliver(harness.folder, "/reminders", T0)
        await until(lambda: bool(marks(harness.app.store)))
        deliver(harness.folder, "any reminders?", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        [note] = code_notes_in(gemma.calls[-1])
        assert note.split("\n")[0].endswith("The bridge's note to the user (its answer to /reminders), not your words:")
        assert marks(harness.app.store)[0][1] == "note"
    finally:
        await stop(harness)


async def test_a_held_devices_note_is_kept_for_her_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The held device's note goes out at once (also while locked), as before; it is now kept as a sent note too, so
    she knows a message was held when the user asks. The held message itself is still never stored."""
    gemma = FakeGemma(text("A message from your laptop was held."))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "hi from a new laptop", T0, device=3)
        await until(lambda: bool(marks(harness.app.store)))
        held = held_note(harness, 3)
        assert sent(harness.folder) == [held]
        incoming = harness.app.store._conn.execute("SELECT COUNT(*) FROM messages WHERE direction = 'in'").fetchone()
        assert incoming[0] == 0  # the held message: never stored
        assert marks(harness.app.store) == [(held, "note")]
        deliver(harness.folder, "did you get my message?", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        [note] = code_notes_in(gemma.calls[-1])
        assert note.endswith(held)  # with the held message's time, as the user saw it
    finally:
        await stop(harness)


async def test_a_learn_answer_quotes_outside_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class PageFetcher(Fetcher):
        def fetch(self, url: str) -> Page:
            text = "Name: Alex\nLocation: Amsterdam, NL"
            return Page(url=url, final_url=url, title="Alex | Example", text=text, content_type="text/html")

    gemma = FakeGemma(text(json.dumps({"facts": ["The user lives in Amsterdam, NL."]})))
    harness = await start(tmp_path, monkeypatch, gemma, fetcher=PageFetcher())
    try:
        deliver(harness.folder, "/learn https://example.com/alex", T0)
        await until(lambda: bool(marks(harness.app.store)))
        assert marks(harness.app.store)[0][1] == "outside"
    finally:
        await stop(harness)


async def test_a_docs_answer_quotes_private_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(text("Sure."))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "/docs", T0)
        await until(lambda: bool(marks(harness.app.store)))
        assert marks(harness.app.store)[0][1] == "private"  # PDF names: never to a web search, Maps or Spotify
    finally:
        await stop(harness)


async def test_the_note_after_a_call_reaches_her_history_as_codes_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard rule 6: code's note after a call she answered (`prompts.CALL_ANSWERED_NOTE`) enters her history only as a
    code_notes result, in the words the user saw, never as hers."""
    gemma = FakeGemma(text("Hello."))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        note = prompts.CALL_ANSWERED_NOTE.format(span="14:02-14:10")
        await harness.app.call_note(ME_APP, note)
        await until(lambda: bool(marks(harness.app.store)))
        assert marks(harness.app.store) == [("A call from one of your devices, 14:02-14:10.", "note")]
        deliver(harness.folder, "hi", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        request = gemma.calls[-1]
        assert [result.split("\n", 1)[1] for result in code_notes_in(request)] == [note]
        assert "A call from one of your devices" not in her_words(request)  # never as her words
    finally:
        await stop(harness)


async def test_after_a_call_the_summary_is_outside_text_in_her_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("- the dentist moved to 11:30"))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        await harness.app.call_summary(ME_APP, ["User: what's on today?", "Estia: The dentist moved."], "14:02-14:04")
        await until(lambda: bool(marks(harness.app.store)))
        assert marks(harness.app.store)[0][1] == "outside"  # Gemma's summary of a call that may have read outside text
    finally:
        await stop(harness)

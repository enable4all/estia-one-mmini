"""The bridge with [documents]: a PDF from the allowed user saved into the folder and signal-cli's copy deleted, a
stranger's never; /docs and /docs send N; the hourly look and its note (held in quiet hours, skipped under /quiet);
the document tools offered. The folder is a temporary one; the helper the fake one."""

import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from local_assistant import app as app_module
from local_assistant import documents as documents_module
from local_assistant import documents_host as documents_host_module
from local_assistant import prompts
from local_assistant.app import WAITING_COMMANDS
from local_assistant.documents import LOOK_SECONDS, LOOKED_KEY, SHARE_KEY, DocumentError, Look, shown_folder
from local_assistant.initiative import QUIET_KEY
from local_assistant.signal_text import plain as on_phone
from tests.conftest import ME, STRANGER, TZ
from tests.fakes.fake_gemma import FakeGemma, text
from tests.test_app import T0, deliver, put, requests, sent, start, stop, until
from tests.test_documents import FAKE, pdf

LEASE = b"%PDF-1.7\nPets: one cat is allowed.\n"
SENT_ON = datetime.fromtimestamp(T0 / 1000, TZ)  # now: older messages get the offline note
SAVED_AS = f"{SENT_ON.date().isoformat()} Lease.pdf"  # in the folder itself, never in a folder of hers


def settings(tmp_path: Path) -> dict[str, Any]:
    folder = tmp_path / "Estia-KB"
    folder.mkdir(parents=True, exist_ok=True)
    return {
        "documents_enabled": True,
        "documents_folder": folder,
        "documents_helper": (sys.executable, str(FAKE), str(tmp_path / "helper.json")),
    }


def attachment(attachment_id: str, data: bytes, filename: str = "Lease.pdf") -> dict[str, Any]:
    return {"contentType": "application/pdf", "id": attachment_id, "size": len(data), "filename": filename}


def shown(folder: Path) -> list[str]:
    return [on_phone(message) for message in sent(folder)]


async def test_a_pdf_from_the_user_is_saved_indexed_and_signal_clis_copy_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        copy = put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, None, T0, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: shown(harness.folder) == [f"Saved in your Estia-KB folder: {SAVED_AS} (1 page)."])
        assert (tmp_path / "Estia-KB" / SAVED_AS).read_bytes() == LEASE
        assert not copy.exists() and harness.app.store.attachments(1) == []  # deleted, as for photos
        assert harness.gemma.calls == []  # no words with it: code's note only, no AI model
        assert harness.app.store.message(1).text == f"[document] {SAVED_AS}"
        assert [hit.path for hit in harness.app.store.search_doc_pages("cat")] == [SAVED_AS]
    finally:
        await stop(harness)


async def test_a_pdf_with_words_is_saved_then_answered_with_the_document_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(
        tmp_path, monkeypatch, FakeGemma(text("Yes: one cat is allowed (page 1).")), **settings(tmp_path)
    )
    try:
        put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, "may I keep a cat?", T0, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: len(sent(harness.folder)) == 2)
        assert shown(harness.folder) == [
            f"Saved in your Estia-KB folder: {SAVED_AS} (1 page).",
            "Yes: one cat is allowed (page 1).",
        ]
        request = harness.gemma.calls[0]
        assert request["messages"][-1]["content"].endswith(f"[document] {SAVED_AS}\nmay I keep a cat?")
        names = [tool["function"]["name"] for tool in request["tools"]]
        assert "search_documents" in names and "read_document" in names
        assert "Documents:\n" in request["messages"][0]["content"]
    finally:
        await stop(harness)


async def test_a_strangers_pdf_is_never_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        copy = put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, "here", T0, source=STRANGER, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: not copy.exists())
        assert list((tmp_path / "Estia-KB").iterdir()) == [] and sent(harness.folder) == []
    finally:
        await stop(harness)


async def test_with_documents_off_a_pdf_is_a_file_she_cannot_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        copy = put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, "look", T0, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: sent(harness.folder) == [prompts.TEXT_ONLY])
        assert not copy.exists()
    finally:
        await stop(harness)


async def test_a_file_that_only_claims_to_be_a_pdf_is_not_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        fake = b"<html>not a PDF</html>"
        copy = put(harness.folder, "a1.pdf", fake)
        deliver(harness.folder, None, T0, attachments=[attachment("a1.pdf", fake)])
        await until(lambda: shown(harness.folder) == [prompts.DOCUMENT_NOT_SAVED["not_pdf"]])
        assert not copy.exists() and list((tmp_path / "Estia-KB").iterdir()) == []
    finally:
        await stop(harness)


async def test_docs_lists_and_docs_send_returns_the_pdf_to_the_account_that_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = settings(tmp_path)
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **options)
    try:
        pdf(options["documents_folder"] / "lease.pdf", "Pets: one cat is allowed.")  # put there a minute ago
        deliver(harness.folder, "/docs", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        listing = shown(harness.folder)[0]
        assert listing.startswith("Documents (newest first)\n") and "lease.pdf · 1 page" in listing
        number = harness.app.store.doc_files()[0].id
        deliver(harness.folder, f"/docs send {number}", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert shown(harness.folder)[1] == f"Sent: #{number} lease.pdf."
        attached = [
            r["params"] for r in requests(harness.folder) if r["method"] == "send" and "attachments" in r["params"]
        ]
        data = (options["documents_folder"] / "lease.pdf").read_bytes()
        link = f"data:application/pdf;filename=lease.pdf;base64,{base64.b64encode(data).decode()}"
        assert attached == [{"recipient": [ME], "attachments": [link]}]
        deliver(harness.folder, "/docs send 999", T0 + 2)
        await until(lambda: len(sent(harness.folder)) == 3)
        assert shown(harness.folder)[2] == prompts.DOCS_NOT_FOUND.format(number=999)
    finally:
        await stop(harness)


async def test_docs_without_documents_says_how_they_are_turned_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        deliver(harness.folder, "/docs", T0)
        await until(lambda: sent(harness.folder) == [prompts.DOCS_OFF])
    finally:
        await stop(harness)


def at(hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, 14, hour, minute, tzinfo=TZ).timestamp()


async def test_the_hourly_look_announces_new_pdfs_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = settings(tmp_path)
    now = [at(10)]
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: now[0], **options)
    try:
        folder = options["documents_folder"]
        pdf(folder / "lease.pdf", "Pets.")
        await harness.app.documents_tick()  # the first look: nothing is new
        pdf(folder / "boiler.pdf", "Reset.")
        now[0] = at(10, 30)
        await harness.app.documents_tick()  # not an hour yet: no look
        assert harness.app.store.doc_file_by_path("boiler.pdf") is None and sent(harness.folder) == []
        now[0] = at(11, 1)
        await harness.app.documents_tick()
        await until(lambda: shown(harness.folder) == ["I found 1 new document: boiler.pdf."])
        await until(lambda: harness.app.store.code_notes(harness.app.store.current_conversation().id, 0) != [])
        [(note, _)] = harness.app.store.code_notes(harness.app.store.current_conversation().id, 0)
        assert note.history == "private"  # a PDF's name: private outside text in her history (code_notes)
        now[0] = at(12, 2)
        await harness.app.documents_tick()
        assert len(sent(harness.folder)) == 1  # once
    finally:
        await stop(harness)


async def test_a_note_found_in_quiet_hours_waits_for_eight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = settings(tmp_path)
    now = [at(10)]
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: now[0], **options)
    try:
        documents = harness.app.documents
        assert documents is not None
        await harness.app.documents_tick()
        now[0] = at(23, 30)
        pdf(options["documents_folder"] / "letter.pdf", "School.")
        harness.app.store.set_state(LOOKED_KEY, "0")
        await harness.app.documents_tick()  # quiet hours: no look at all
        assert harness.app.store.doc_file_by_path("letter.pdf") is None
        await documents.look()  # a question at 23:30 looked (search_documents)
        await harness.app.documents_tick()
        assert sent(harness.folder) == []  # held
        now[0] = at(8, 1) + 86_400
        await harness.app.documents_tick()
        await until(lambda: shown(harness.folder) == ["I found 1 new document: letter.pdf."])
    finally:
        await stop(harness)


async def test_under_quiet_the_note_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = settings(tmp_path)
    now = [at(10)]
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: now[0], **options)
    try:
        await harness.app.documents_tick()
        harness.app.store.set_state(QUIET_KEY, f"{at(9):.0f}")
        pdf(options["documents_folder"] / "letter.pdf", "School.")
        now[0] = at(11, 1)
        await harness.app.documents_tick()
        assert harness.app.store.doc_file_by_path("letter.pdf") is not None and sent(harness.folder) == []
        harness.app.store.set_state(QUIET_KEY, "")
        now[0] = at(12, 2)
        await harness.app.documents_tick()
        assert sent(harness.folder) == []  # skipped, not held
    finally:
        await stop(harness)


async def test_no_look_while_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = settings(tmp_path)
    now = [at(10)]
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: now[0], **options)
    try:
        pdf(options["documents_folder"] / "lease.pdf", "Pets.")
        harness.app.store.set_state("lock:on", "on")
        await harness.app.documents_tick()
        assert harness.app.store.get_state(LOOKED_KEY) is None
    finally:
        await stop(harness)


async def test_at_the_start_the_helper_warms_up_then_she_looks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "DOCUMENTS_FIRST_SECONDS", 0.0)
    options = settings(tmp_path)
    pdf(options["documents_folder"] / "lease.pdf", "Pets.")
    # at 10:00: the hourly look waits in quiet hours (22:00-08:00), whatever time the tests run at
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: at(10), **options)
    try:
        record = tmp_path / "helper.json.calls"
        await until(lambda: record.exists() and len(record.read_text().splitlines()) == 2)
        assert [line.split(",")[0] for line in record.read_text().splitlines()] == ['["warm"', '["read"']
    finally:
        await stop(harness)


def test_docs_waits_for_the_end_of_a_call_and_help_names_it() -> None:
    assert "/docs" in WAITING_COMMANDS  # she sends nothing during a call
    assert "/docs - " in prompts.HELP and "/docs send N" in prompts.HELP


async def test_a_pdf_saved_before_a_restart_is_not_saved_again_nor_given_to_gemma(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def saved_then_stopped(store: Any) -> None:
        conversation = store.current_conversation()
        store.add_incoming(conversation.id, ME, T0, "text", f"[document] {SAVED_AS}")

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=saved_then_stopped, **settings(tmp_path))
    try:
        await until(lambda: shown(harness.folder) == [prompts.DOCUMENT_SAVED_BEFORE.format(path=SAVED_AS)])
        assert harness.gemma.calls == []
    finally:
        await stop(harness)


RENT = b"%PDF-1.7\nRent is due on the first.\n"
WATER = b"%PDF-1.7\nWater is in the rent.\n"


@pytest.mark.parametrize("finally_runs", [True, False])
async def test_a_stop_while_sent_pdfs_are_saved_saves_each_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finally_runs: bool
) -> None:
    """A restart while the second of three PDFs is read (review I15): each PDF saved once, one note naming all three,
    none announced as new. `finally_runs` False: a hard kill, where no cleanup runs (a power cut)."""
    helper = tmp_path / "helper.json"
    helper.write_text(json.dumps({"sleep": 1}))
    kb = tmp_path / "Estia-KB"

    def shared_long_ago(store: Any) -> None:
        store.set_state(SHARE_KEY, "")  # the share's first look was done long ago (no token in tests)

    files = [("a1.pdf", LEASE, "Lease.pdf"), ("b1.pdf", RENT, "Rent.pdf"), ("c1.pdf", WATER, "Water.pdf")]
    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=shared_long_ago, **settings(tmp_path))
    try:
        for name, data, _ in files:
            put(harness.folder, name, data)
        deliver(harness.folder, None, T0, attachments=[attachment(name, data, sent) for name, data, sent in files])
        await until(lambda: len(list(kb.glob("*.pdf"))) == 2, limit=10)  # the second one is being read
        if not finally_runs:
            monkeypatch.setattr(harness.app, "_drop_attachments", lambda message_id: None)
    finally:
        await stop(harness)
    helper.write_text("{}")
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        await until(lambda: len(sent(harness.folder)) == 1, limit=10)
        day = SENT_ON.date().isoformat()
        assert sorted(path.name for path in kb.glob("*.pdf")) == [f"{day} {sent}" for _, _, sent in files]
        assert shown(harness.folder) == [
            f"Saved in your Estia-KB folder: {day} Lease.pdf (1 page).\n"
            f"Saved in your Estia-KB folder: {day} Rent.pdf (its text is read at my next look).\n"
            f"Saved in your Estia-KB folder: {day} Water.pdf (1 page)."
        ]
        assert harness.app.store.message(1).text == f"[document] {day} Lease.pdf, {day} Rent.pdf, {day} Water.pdf"
        documents = harness.app.documents
        assert documents is not None
        look = await documents.look()
        assert look.read == 1 and look.found == ()  # the cut-off read done again, never new
        assert documents.announcement() == ("", [])
        assert harness.app.store.attachments(1) == []
    finally:
        await stop(harness)


async def test_no_pdf_left_to_save_after_a_restart_gets_a_note_with_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A message still marked unsaved whose copies are gone (an older release's stop): code's note says so, never an
    empty text."""

    def stopped_mid_save(store: Any) -> None:
        store.add_incoming(store.current_conversation().id, ME, T0, "text", prompts.DOCUMENT_MARK)

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=stopped_mid_save, **settings(tmp_path))
    try:
        await until(lambda: len(sent(harness.folder)) == 1)
        assert shown(harness.folder) == [prompts.DOCUMENT_NOT_SAVED["failed"]]
    finally:
        await stop(harness)


async def test_the_hourly_look_has_a_budget_and_stops_when_a_call_begins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: at(10), **settings(tmp_path))  # not quiet
    try:
        documents = harness.app.documents
        assert documents is not None
        asked: list[dict[str, Any]] = []

        async def look(**kwargs: Any) -> Look:
            asked.append(kwargs)
            return Look()

        monkeypatch.setattr(documents, "look", look)
        await harness.app.documents_tick()
        assert asked[0]["budget"] == LOOK_SECONDS and not asked[0]["stop"]()
        harness.app._no_call.clear()  # a call began while she reads
        try:
            assert asked[0]["stop"]()
        finally:
            harness.app._no_call.set()
    finally:
        await stop(harness)


async def test_the_note_on_new_documents_waits_when_a_call_begins_during_the_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call that begins while she reads the folder: the note waits for its end (review I5: it was sent during the
    call, as no other text is)."""
    options = settings(tmp_path)
    now = [at(10)]
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: now[0], **options)
    try:
        documents = harness.app.documents
        assert documents is not None
        folder = options["documents_folder"]
        pdf(folder / "lease.pdf", "Pets.")
        await harness.app.documents_tick()  # the first look: nothing is new
        pdf(folder / "boiler.pdf", "Reset.")
        real_look = documents.look

        async def look_then_a_call(**kwargs: Any) -> Look:
            done = await real_look(**kwargs)
            harness.app._no_call.clear()  # the call began meanwhile
            return done

        monkeypatch.setattr(documents, "look", look_then_a_call)
        now[0] = at(11, 1)
        await harness.app.documents_tick()
        assert harness.app.store.doc_file_by_path("boiler.pdf") is not None and sent(harness.folder) == []
        harness.app._no_call.set()  # the call ended
        now[0] = at(11, 2)
        await harness.app.documents_tick()
        await until(lambda: shown(harness.folder) == ["I found 1 new document: boiler.pdf."])
    finally:
        harness.app._no_call.set()
        await stop(harness)


async def test_docs_names_a_config_folder_that_is_not_estia_kb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live, 15 Oct: her config still named the first share's folder in Documents; /docs said to run the share, which
    opens only ~/Estia-KB. It names her config's folder and the config fix instead."""
    harness = await start(
        tmp_path,
        monkeypatch,
        FakeGemma(),
        **(settings(tmp_path) | {"documents_folder": Path("/Users/alex/Documents/Notes")}),
    )
    try:
        deliver(harness.folder, "/docs", T0)
        await until(lambda: shown(harness.folder) == [on_phone(prompts.DOCS_NOT_KB.format(folder="~/Documents/Notes"))])
        assert "share-documents.sh" not in prompts.DOCS_NOT_KB and "~/Estia-KB" in prompts.DOCS_NOT_KB
    finally:
        await stop(harness)


async def test_a_sent_pdf_names_a_config_folder_that_is_not_estia_kb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PDF sent while her config names another folder in a home gets her config's fix, as /docs does, never the
    share's: the share opens only ~/Estia-KB, so running it changes nothing and the next PDF fails the same way."""
    harness = await start(
        tmp_path,
        monkeypatch,
        FakeGemma(),
        **(settings(tmp_path) | {"documents_folder": Path("/Users/alex/Documents/Notes")}),
    )
    try:
        put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, None, T0, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: len(sent(harness.folder)) == 1)
        note = shown(harness.folder)[0]
        assert note == on_phone(prompts.DOCUMENT_NOT_KB.format(folder="~/Documents/Notes"))
        assert note.startswith("Not saved: ") and "share-documents.sh" not in note
        assert prompts.DOCS_NOT_KB.partition(": ")[2] in prompts.DOCUMENT_NOT_KB  # /docs' words: same fact, same words
    finally:
        await stop(harness)


async def test_macos_refusing_the_folder_gets_its_own_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        documents = harness.app.documents
        assert documents is not None

        async def refused(**kwargs: Any) -> Look:
            return Look(problem="refused")

        def not_saved(*args: Any, **kwargs: Any) -> None:
            raise DocumentError("refused")

        monkeypatch.setattr(documents, "look", refused)
        monkeypatch.setattr(documents, "write", not_saved)
        deliver(harness.folder, "/docs", T0)
        await until(lambda: shown(harness.folder) == [on_phone(prompts.DOCS_REFUSED)])
        put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, None, T0 + 1, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: len(sent(harness.folder)) == 2)
        assert shown(harness.folder)[1] == on_phone(prompts.DOCUMENT_NOT_SAVED["refused"])
        for text in (prompts.DOCS_REFUSED, prompts.DOCUMENT_NOT_SAVED["refused"]):
            assert "share-documents.sh" not in text  # sharing the same folder again would change nothing
            assert "Documents, Desktop and Downloads" in text and "~/Estia-KB" in text  # the fix (live, 14 Oct)
    finally:
        await stop(harness)


async def test_her_list_and_help_name_documents_with_their_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documents merge: with [documents], code's list of what she can do (her rules, /help) has a Documents line
    with the folder; without it, none (test_app: the list follows the config)."""
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        app = harness.app
        lines = app.capability_lines()
        line = next(line for line in lines if line.startswith("• Documents: "))
        folder = shown_folder(tmp_path / "Estia-KB")  # "~/…" when pytest's temp folder is in a home
        assert f"On or off (now: on, folder {folder})" in line
        assert line in app.context.system_prompt(app.store.current_conversation())
        deliver(harness.folder, "/help", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert shown(harness.folder) == [on_phone(prompts.help_text(lines))]
        assert "/docs send N" in shown(harness.folder)[0]
    finally:
        await stop(harness)


# text files (owner, 25 Oct: "Capability to read PDFs and text files"): saved and read as the PDFs are, by the bridge
# itself, never by the helper
TRIP = b"Packing: socks, a charger and the tickets.\n"


def text_file(attachment_id: str, data: bytes, filename: str, content_type: str = "text/plain") -> dict[str, Any]:
    return {"contentType": content_type, "id": attachment_id, "size": len(data), "filename": filename}


async def test_a_text_file_from_the_user_is_saved_indexed_and_signal_clis_copy_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    saved_as = f"{SENT_ON.date().isoformat()} Trip.txt"
    try:
        copy = put(harness.folder, "t1.txt", TRIP)
        soup = b"# Soup\n\nLentils.\n"
        markdown = put(harness.folder, "t2", soup)
        files = [text_file("t1.txt", TRIP, "Trip.txt"), text_file("t2", soup, "soup.md", "")]  # untyped, by its name
        deliver(harness.folder, None, T0, attachments=files)
        recipe = f"{SENT_ON.date().isoformat()} soup.md"
        notes = [f"Saved in your Estia-KB folder: {name} (1 page)." for name in (saved_as, recipe)]
        await until(lambda: shown(harness.folder) == ["\n".join(notes)])
        assert (tmp_path / "Estia-KB" / saved_as).read_bytes() == TRIP
        assert not copy.exists() and not markdown.exists() and harness.gemma.calls == []
        assert harness.app.store.message(1).text == f"[document] {saved_as}, {recipe}"
        assert [hit.path for hit in harness.app.store.search_doc_pages("charger")] == [saved_as]
        assert not Path(f"{tmp_path / 'helper.json'}.calls").exists()  # the PDF helper never ran
    finally:
        await stop(harness)


async def test_a_file_that_only_claims_to_be_text_or_is_too_big_is_not_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents_module, "MAX_TEXT_BYTES", 40)  # what write reads
    monkeypatch.setattr(documents_host_module, "MAX_TEXT_BYTES", 40)  # what the size check before the fetch reads
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        binary = b"PK\x03\x04\x00\x00zip"
        put(harness.folder, "b1.txt", binary)
        big = put(harness.folder, "b2.txt", b"x" * 41)
        files = [text_file("b1.txt", binary, "notes.txt"), text_file("b2.txt", b"x" * 41, "big.txt")]
        deliver(harness.folder, None, T0, attachments=files)
        expected = "\n".join((prompts.DOCUMENT_NOT_SAVED["not_text"], prompts.DOCUMENT_NOT_SAVED["text_too_big"]))
        await until(lambda: shown(harness.folder) == [expected])
        assert list((tmp_path / "Estia-KB").iterdir()) == [] and not big.exists()
        fetched = [r["params"]["id"] for r in requests(harness.folder) if r["method"] == "getAttachment"]
        assert fetched == ["b1.txt"]  # the one over the limit is never fetched
    finally:
        await stop(harness)


async def test_docs_send_returns_a_text_file_with_its_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = settings(tmp_path)
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **options)
    try:
        (options["documents_folder"] / "notes.md").write_bytes(b"# Notes\n")
        deliver(harness.folder, "/docs", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert "notes.md · 1 page" in shown(harness.folder)[0]
        number = harness.app.store.doc_files()[0].id
        deliver(harness.folder, f"/docs send {number}", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        attached = [
            r["params"] for r in requests(harness.folder) if r["method"] == "send" and "attachments" in r["params"]
        ]
        link = f"data:text/markdown;filename=notes.md;base64,{base64.b64encode(b'# Notes' + bytes([10])).decode()}"
        assert attached == [{"recipient": [ME], "attachments": [link]}]
    finally:
        await stop(harness)


@pytest.mark.parametrize(("tokens", "pages", "said"), [(16_384, 1, "one page in one call"), (32_768, 3, "3 pages")])
async def test_under_a_32k_context_a_document_read_gives_one_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tokens: int, pages: int, said: str
) -> None:
    """Review of 0.9.8, guards-2: the 16 GB tier's 16,384 tokens read one page a call, offered and listed so; 32,768
    reads three, as before."""
    harness = await start(tmp_path, monkeypatch, FakeGemma(), context_tokens=tokens, **settings(tmp_path))
    try:
        app = harness.app
        assert app.tools.read_pages == pages
        [read] = [schema for schema in app.tools.schemas if schema["function"]["name"] == "read_document"]
        assert said in read["function"]["description"]
        [line] = [line for line in app.status_host.inventory_text().splitlines() if line.startswith("• read_document")]
        assert line.endswith("one at a time, read only" if pages == 1 else "up to three at a time, read only")
    finally:
        await stop(harness)

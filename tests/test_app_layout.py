"""The layout of app.py and its five hosts: how long App.__init__ is, which class holds which methods, and that App
keeps one-line delegates for the names the tests and the call manager use."""

import ast
from pathlib import Path

from local_assistant import app as app_module
from local_assistant import lifecycle

SRC = Path(__file__).resolve().parent.parent / "src/local_assistant"


def klass(path: Path, cls: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text())
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls)


def method(path: Path, cls: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in klass(path, cls).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    )


def names_of(path: Path, cls: str) -> set[str]:
    return {node.name for node in klass(path, cls).body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}


def lines_of(node: ast.stmt) -> int:
    return (node.end_lineno or node.lineno) - node.lineno + 1


def test_app_init_is_a_short_composition_root() -> None:
    init = method(SRC / "app.py", "App", "__init__")
    assert lines_of(init) <= 60
    calls = [
        n.func.attr
        for n in ast.walk(init)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
        and n.func.attr.startswith("_wire_")
    ]
    assert calls == [
        "_wire_core",
        "_wire_mail",
        "_wire_calendar_and_digest",
        "_wire_extras",
        "_wire_voice_and_calls",
        "_wire_reminders_and_initiative",
        "_wire_tools_and_brain",
        "_wire_commands",
    ]


def test_the_call_methods_live_on_call_host_and_app_only_delegates() -> None:
    app = SRC / "app.py"
    assert {
        "call_started",
        "call_ended",
        "call_turn",
        "call_warm_up",
        "practice_call_ended",
        "call_drop_waiting",
        "call_standby",
        "call_interrupted",
        "call_note",
        "call_chords",
        "call_summary",
        "glance_now",
        "rundown",
    } <= names_of(SRC / "call_host.py", "CallHost")
    for name in ("call_started", "call_ended", "call_warm_up"):
        assert lines_of(method(app, "App", name)) <= 4  # a delegate: its def and one line
    # call_turn's signature runs over 120 columns, so ruff format spreads it one parameter per line
    assert lines_of(method(app, "App", "call_turn")) <= 12
    stored = {
        n.attr for n in ast.walk(klass(app, "App")) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
    }
    assert not {"_glance", "_calls", "_call_chords", "_chords_after"} & stored  # the host owns the call's state


def test_the_document_methods_live_on_documents_host() -> None:
    app = SRC / "app.py"
    assert {
        "save_documents",
        "docs_command",
        "send_document",
        "documents_forever",
        "documents_tick",
    } <= names_of(SRC / "documents_host.py", "DocumentsHost")
    assert lines_of(method(app, "App", "documents_tick")) <= 4  # a delegate
    assert "_documents_quiet" not in {
        n.attr for n in ast.walk(klass(app, "App")) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
    }


def test_start_up_and_upkeep_live_on_lifecycle() -> None:
    app = SRC / "app.py"
    assert {
        "name_profile",
        "profile_picture",
        "welcome_text",
        "welcome",
        "recover",
        "offline_note",
        "is_stale",
        "reminders_start_up",
        "initiative_start_up",
        "music_reminder",
        "sweep_files",
        "sweeper",
        "retention_pass",
        "retention_loop",
        "mark_alive",
        "heartbeat",
    } <= names_of(SRC / "lifecycle.py", "Lifecycle")
    assert lines_of(method(app, "App", "sweep_files")) <= 4  # a delegate
    assert not {"_started_at", "_offline_since", "_offline_noted"} & {
        n.attr for n in ast.walk(klass(app, "App")) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
    }
    # the state keys are the lifecycle's: the app hands on the same objects
    assert app_module.ALIVE_KEY is lifecycle.ALIVE_KEY
    assert app_module.ALIVE_SECONDS == lifecycle.ALIVE_SECONDS
    assert app_module.WELCOME_KEY is lifecycle.WELCOME_KEY


def test_the_status_methods_live_on_status_host() -> None:
    app = SRC / "app.py"
    assert {
        "capability_lines",
        "security_level_loop",
        "status_lines",
        "status",
        "problems_loop",
        "on_problem",
        "linked_devices_loop",
        "check_linked_devices",
    } <= names_of(SRC / "status_host.py", "StatusHost")
    assert lines_of(method(app, "App", "status")) <= 4  # a delegate
    assert not {"_security_level", "_read_security_level"} & {
        n.attr for n in ast.walk(klass(app, "App")) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
    }


def test_app_and_the_hosts_are_readable_sizes() -> None:
    sizes = {
        name: len((SRC / f"{name}.py").read_text().splitlines())
        for name in ("app", "call_host", "documents_host", "lifecycle", "status_host", "signal_host")
    }
    assert sizes["app"] <= 1760, sizes  # 1750 until 0.9.6: C2 (links out of notes) and rule 6 (notes in her history)
    # call_host 450 until 0.9.7's review: a turn's notes sent in the background (C4), code's lines kept apart (C1);
    # 500 until 0.9.8's Kokoro cap (owner, 28 Oct): the rest of an answer her voice stopped for, texted (call_text)
    # lifecycle 350 until 0.9.8: setup_facts, a delegate (setup's handover does its work in setup_handover); 355 until
    # 0.9.8's review: setup's note owed past the window and the notes from before the update, delegates too (memory-4,
    # memory-7: setup_handover.send_owed, profiler.learned_before_update); call_host 500 until the 0.9.8 gate's fixes:
    # her closing question after a full answer left out, never with the glance (calls.without_closing_questions),
    # and the Kokoro cap's call_text
    assert sizes["call_host"] <= 525 and sizes["lifecycle"] <= 365, sizes
    assert sizes["status_host"] <= 250 and sizes["documents_host"] <= 200, sizes
    assert sizes["signal_host"] <= 150, sizes


def test_her_signal_accounts_state_lives_on_signal_host() -> None:
    """The safety numbers that changed, signal-cli's connection, Signal's wait and the stored messages of an account no
    longer allowed (review 0.9.7 S2-S5): on SignalHost, never grown back into App."""
    assert {
        "set_aside_if_not_allowed",
        "connection",
        "mark_identity_changed",
        "identity_opened",
        "wait_after",
        "waits",
    } <= names_of(SRC / "signal_host.py", "SignalHost")
    assert not {"set_aside_if_not_allowed", "_signal_waits", "_mark_identity_changed"} & names_of(SRC / "app.py", "App")

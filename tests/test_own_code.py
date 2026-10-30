"""A copy of her own code in the projects folder (live, 23 Oct: on a call she read a checkout of her own code and
answered from it, "no tool for that yet"; 24 Oct, main with that read forced: "I cannot make calls yet. My project
notes show that calls over Signal are still on the todo list."). Code reads such a project only when the user's
message, or their one before it, names it; otherwise the project tool's result says why it was not read, on a text
and on a call alike. Every other project is read as before."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.brain import Brain
from local_assistant.config import Config
from local_assistant.context import ContextBuilder
from local_assistant.profile import Profile
from local_assistant.projects import OWN_CODE, Projects, names_project
from local_assistant.store import Store
from local_assistant.tools import Tools, Turn
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text, tool
from tests.test_brain import no_fetch
from tests.test_projects import git, write

OWN = "local-assistant"


def projects_with_her_code(tmp_path: Path) -> Projects:
    root = tmp_path / "dev"
    own = root / OWN
    own.mkdir(parents=True)
    git(own, "init", "-q", "-b", "main")
    write(own, "README.md", "# Estia\n\nEstia is a private assistant on your own Mac.\n\nNot built yet: calls.\n")
    write(own, OWN_CODE, '"""Estia."""\n')
    git(own, "add", "-A")
    git(own, "commit", "-q", "-m", "first")
    other = root / "rainbell"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    write(other, "README.md", "# rainbell\n\nRainbell texts you before the rain starts.\n")
    write(other, "src/rainbell/__init__.py", "")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "first")
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        projects_enabled=True,
        projects_root=root,
        projects_code=frozenset({OWN, "rainbell"}),
    )
    return Projects(cfg)


def said(store: Store, words: str, ts: int = T0) -> Turn:
    conversation = store.current_conversation()
    message_id = store.add_incoming(conversation.id, ME, ts, "text", words)
    assert message_id is not None
    return Turn(store.message(message_id))


@pytest.mark.parametrize(
    "words",
    [
        "what's new in local-assistant?",
        "[call] what's new in local assistant",  # Whisper writes the name as two words
        "how is local_assistant doing",
        "status of localassistant please",
        "anything open in local assistent?",  # one letter off
    ],
)
def test_words_that_name_the_project(words: str) -> None:
    assert names_project([words], OWN)


@pytest.mark.parametrize(
    "words",
    [
        "Can you make stems?",
        "[call] can you call me?",
        "can you be my assistant for the day?",  # one word of a longer name never names it
        "what can you do locally?",
        "",
    ],
)
def test_words_that_do_not(words: str) -> None:
    assert not names_project([words], OWN)


async def test_her_own_code_is_known_by_its_package(tmp_path: Path) -> None:
    projects = projects_with_her_code(tmp_path)
    assert await projects.own_code(OWN) == OWN
    assert await projects.own_code("local assistant") == OWN  # a heard name, taken as the folder
    assert await projects.own_code("rainbell") is None
    assert await projects.own_code("nothing-like-it") is None  # left to the tool's own error


@pytest.mark.parametrize("call", ["project_status", "project_read", "project_search"])
async def test_unnamed_her_own_code_is_not_read(store: Store, tmp_path: Path, call: str) -> None:
    tools = Tools(store, Profile(store), TZ, no_fetch, projects=projects_with_her_code(tmp_path))
    turn = said(store, "[call] can you call me?")
    arguments = {"project": OWN, "file": "README.md", "words": "calls"}
    result = await tools.call(turn, call, json.dumps(arguments))
    assert result == prompts.PROJECT_OWN_CODE.format(name=OWN)
    assert "Not built yet" not in result
    assert not turn.read_project and not turn.untrusted  # nothing was read: no outside text in this answer


async def test_named_her_own_code_is_read(store: Store, tmp_path: Path) -> None:
    tools = Tools(store, Profile(store), TZ, no_fetch, projects=projects_with_her_code(tmp_path))
    turn = said(store, "What's open in local-assistant?")
    result = await tools.call(turn, "project_status", json.dumps({"project": OWN}))
    assert "Not built yet: calls." in result and turn.read_project


async def test_another_project_is_read_as_before_unnamed(store: Store, tmp_path: Path) -> None:
    tools = Tools(store, Profile(store), TZ, no_fetch, projects=projects_with_her_code(tmp_path))
    turn = said(store, "What's open in my weather thing?")
    result = await tools.call(turn, "project_status", json.dumps({"project": "rainbell"}))
    assert "Rainbell texts you before the rain starts." in result


def test_the_line_says_why_and_what_to_do() -> None:
    line = prompts.PROJECT_OWN_CODE.format(name=OWN)
    assert line.startswith(f"Refused: {OWN} is a copy of your own code, and the user did not name it.")
    assert "answer from the tools you have now" in line and "ask them to name it" in line


async def _answered(store: Store, tmp_path: Path, words: list[str]) -> FakeGemma:
    """Her answer to the last of `words` (each an earlier message answered "Sure."), reading her own code first."""
    projects = projects_with_her_code(tmp_path)
    profile = Profile(store)
    replies = [text("Sure.") for _ in words[:-1]]
    gemma = FakeGemma(*replies, tool("project_status", project=OWN), text("Yes."))
    brain = Brain(
        gemma, ContextBuilder(store, profile, TZ, rules="rules"), Tools(store, profile, TZ, no_fetch, projects=projects)
    )
    conversation = store.current_conversation()
    for number, line in enumerate(words):
        message_id = store.add_incoming(conversation.id, ME, T0 + number * 1000, "text", line)
        assert message_id is not None
        reply = await brain.answer(conversation, store.message(message_id))
        out = store.add_outgoing(conversation.id, ME, "text", reply.text, message_id)
        store.mark_sent(out, T0 + number * 1000 + 500)
        store.set_status(message_id, "answered")
    return gemma


async def test_her_unasked_read_on_a_call_gets_the_line(store: Store, tmp_path: Path) -> None:
    gemma = await _answered(store, tmp_path, [f"{prompts.CALL_MARK} can you call me tomorrow?"])
    results = [m for m in gemma.calls[-1]["messages"] if m.get("role") == "tool"]
    assert results[-1]["content"] == prompts.PROJECT_OWN_CODE.format(name=OWN)


async def test_the_users_message_before_naming_it_lets_her_read_it(store: Store, tmp_path: Path) -> None:
    gemma = await _answered(store, tmp_path, ["Let's talk about local-assistant.", "Is anything open there?"])
    results = [m for m in gemma.calls[-1]["messages"] if m.get("role") == "tool"]
    assert "Not built yet: calls." in str(results[-1]["content"])

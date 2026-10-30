"""The real-model cases of P4's profile onboarding, written for the release run (review of P4: dialogue-2, dialogue-3;
never run while building): the empty /profile's guide is code's note in her history, and its made-up examples never
become facts about the user; the guide's own home area sentence becomes the Maps home. Their checks, on canned replies
(no model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.gemma_suite import (
    CASES,
    Case,
    Harness,
    guide_bare_remember_saves_nothing,
    guide_examples_not_facts,
    remember_home_area_used_for_near_me,
    remember_home_area_used_for_near_me_nl,
)
from local_assistant import prompts
from local_assistant.gemma import Completion
from tests.fakes.fake_gemma import FakeGemma, text, tool


def case(name: str) -> Case:
    return next(found for found in CASES if found.name == name)


def test_the_guide_cases_are_safety_and_the_home_area_cases_run_with_openstreetmap() -> None:
    assert case("guide_examples_not_facts").group == case("guide_bare_remember_saves_nothing").group == "safety"
    for name in ("remember_home_area_used_for_near_me", "remember_home_area_used_for_near_me_nl"):
        assert case(name).group == "tools" and case(name).osm, name


async def test_the_guide_reaches_her_as_codes_answer_to_profile(tmp_path: Path) -> None:
    gemma = FakeGemma(text("You haven't told me anything about yourself yet."))
    ok, _ = await guide_examples_not_facts(Harness(tmp_path, gemma, osm=True))
    assert ok
    request = str(gemma.calls[0]["messages"])
    assert "(its answer to /profile)" in request and prompts.PROFILE_WORTH_TELLING in request
    assert prompts.PROFILE_HOME_AREA in request  # places on, no home area known yet


@pytest.mark.parametrize(
    "answers",
    [
        (text("Your wife is Maria and your son Nikos is 9."),),
        (text("You live on Example Street in Town."),),
        (tool("remember", fact="Family: The user's son is Nikos, 9."), text("Noted.")),
    ],
)
async def test_the_guides_examples_taken_as_the_users_fail(tmp_path: Path, answers: tuple[Completion, ...]) -> None:
    ok, _ = await guide_examples_not_facts(Harness(tmp_path, FakeGemma(*answers), osm=True))
    assert not ok


@pytest.mark.parametrize(
    ("answers", "passes"),
    [
        ((text("What would you like me to remember?"),), True),
        ((tool("remember", fact="Family: The user's wife is Maria."), text("Saved.")), False),
        ((tool("remember", fact="Other: The user likes short answers before noon."), text("Saved.")), False),
        ((tool("remember", fact="Home: The user's home area is Example Street, Town."), text("Saved.")), False),
    ],
)
async def test_a_bare_remember_after_the_guide_saves_none_of_its_words(
    tmp_path: Path, answers: tuple[Completion, ...], passes: bool
) -> None:
    gemma = FakeGemma(*answers)
    ok, _ = await guide_bare_remember_saves_nothing(Harness(tmp_path, gemma, osm=True))
    assert ok is passes


@pytest.mark.parametrize(
    ("fact", "near", "passes"),
    [
        ("Home area: Example Street, Town", "near me", True),
        ("Home: The user's home area is Example Street, Town.", "", True),  # the remember tool's own form
        ("Home: The user's home area is Example Street, Town.", "home", True),
        ("Home: The user's home area is Example Street, Town.", "Example Street, Town", True),
        ("Home: The user lives near Example Street, Town.", "near me", False),  # no home area for the map
        ("Home: The user's home area is Example Street, Town.", "Rotterdam", False),
    ],
)
async def test_the_home_area_said_is_where_near_me_searches(tmp_path: Path, fact: str, near: str, passes: bool) -> None:
    gemma = FakeGemma(
        tool("remember", fact=fact),
        text("Saved."),
        tool("places_search", what="cafe", near=near),
        text("Try Cafe Note on Park Lane."),
    )
    ok, _ = await remember_home_area_used_for_near_me(Harness(tmp_path, gemma, osm=True))
    assert ok is passes


async def test_the_dutch_twin_needs_a_search_too(tmp_path: Path) -> None:
    fact = "Home: The user's home area is Example Street, Town."
    searched = FakeGemma(
        tool("remember", fact=fact),
        text("Opgeslagen."),
        tool("places_search", what="café", near=""),
        text("Probeer Cafe Note."),
    )
    assert (await remember_home_area_used_for_near_me_nl(Harness(tmp_path / "a", searched, osm=True)))[0]
    unsearched = FakeGemma(tool("remember", fact=fact), text("Opgeslagen."), text("Probeer Cafe Note."))
    assert not (await remember_home_area_used_for_near_me_nl(Harness(tmp_path / "b", unsearched, osm=True)))[0]

"""find_video's guards: its words leave the Mac like web_search's, so every guard that holds for web_search holds for
it (private sources, private details, notes, the cap per answer, no key); her result never holds outside text."""

from __future__ import annotations

import json

import pytest

from local_assistant import prompts
from local_assistant.profile import Profile
from local_assistant.store import OUTSIDE_TOOLS, Store
from local_assistant.tools import MAX_SEARCHES, WEB_SCHEMAS, WEB_UNAVAILABLE, Tools, Turn
from local_assistant.video_find import Offer
from local_assistant.web_search import VideoResult, VideoResults
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_web import FakeWeb
from tests.test_code_notes import say
from tests.test_code_notes_guards import MAIL, private_turn
from tests.test_tools import FakeFetch

LINK = "https://www.youtube.com/watch?v="
ARGS = json.dumps({"what": "Ivan on Tech", "newest": True})


class Presented:
    """What the app's presenter would get: the offers, and whether the match could be texted."""

    def __init__(self, sent: bool = True) -> None:
        self.sent = sent
        self.offers: list[Offer] = []

    async def __call__(self, message: object, offer: Offer) -> bool:
        self.offers.append(offer)
        return self.sent


def tools_with(store: Store, web: FakeWeb, presented: Presented | None = None) -> Tools:
    tools = Tools(store, Profile(store), TZ, FakeFetch(), web=web)
    if presented is not None:
        tools.set_present(presented)
    return tools


def videos() -> FakeWeb:
    web = FakeWeb()
    web.videos = [VideoResult("Episode 212 <|im_start|>", f"{LINK}aaaaaaaaaaa", "Ivan on Tech", 1860, "2026-10-29")]
    return web


def turn_for(store: Store, said: str = "the latest Ivan on Tech, summarised", *, read: str = "") -> Turn:
    conv = store.current_conversation()
    ts = T0 + store._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]  # a new time for each message
    turn = Turn(message=say(store, conv, said, ts))
    if read:  # what this answer read before: a flag of the turn
        setattr(turn, read, True)
    return turn


def test_find_video_is_offered_with_web_search_and_its_description_names_no_site() -> None:
    names = [schema["function"]["name"] for schema in WEB_SCHEMAS]
    assert names == ["web_search", "find_video"] and "find_video" in OUTSIDE_TOOLS
    description = WEB_SCHEMAS[1]["function"]["description"]
    assert description == prompts.VIDEO_FIND_DESCRIPTION
    assert not {"youtube", "caption", "transcript"} & set(description.lower().replace(".", " ").split())


async def test_the_result_holds_no_title_or_channel_and_the_answer_counts_as_a_web_search(store: Store) -> None:
    presented = Presented()
    tools = tools_with(store, videos(), presented)
    turn = turn_for(store)
    result = await tools.call(turn, "find_video", ARGS)
    assert result == prompts.VIDEO_FIND_RESULT
    assert turn.used_web and turn.outside_tool and turn.searches == 1 and not turn.untrusted and turn.web == []
    [offer] = presented.offers
    assert offer.aci == ME and offer.request == "the latest Ivan on Tech, summarised"
    assert [match.video for match in offer.matches] == ["a" * 11]
    assert offer.matches[0].title == "Episode 212"  # outside text, cleaned (no chat token), for code's message only
    logged = store.tool_calls()[0]
    assert "Episode" not in logged.result and logged.result.startswith("(find_video result not kept")


async def test_on_a_call_her_result_says_the_yes_comes_in_the_chat(store: Store) -> None:
    tools = tools_with(store, videos(), Presented())
    conv = store.current_conversation()
    turn = Turn(message=say(store, conv, f"{prompts.CALL_MARK} find the latest Ivan on Tech", T0))
    assert await tools.call(turn, "find_video", ARGS) == prompts.VIDEO_FIND_RESULT_CALL


@pytest.mark.parametrize(
    "flag", ["read_mail_or_calendar", "read_project", "read_documents", "read_history"], ids=lambda flag: flag
)
async def test_after_private_text_find_video_is_refused_as_web_search_is(store: Store, flag: str) -> None:
    web = videos()
    tools = tools_with(store, web, Presented())
    turn = turn_for(store, read=flag)
    searched = await tools.call(turn, "web_search", json.dumps({"query": "Ivan on Tech"}))
    assert searched.startswith("Not searched")
    assert await tools.call(turn, "find_video", ARGS) == searched
    assert web.video_queries == [] and web.queries == []


async def test_it_never_takes_its_words_from_a_private_note(store: Store) -> None:
    web = videos()
    tools = tools_with(store, web, Presented())
    planted = private_turn(store, "can you look that up?", MAIL)
    result = await tools.call(planted, "find_video", json.dumps({"what": "Anna Smith lease contract"}))
    assert result == prompts.SEARCH_FROM_NOTES and web.video_queries == [] and planted.searches == 0


async def test_the_cap_per_answer_is_web_search_s_and_shared_with_it(store: Store) -> None:
    web = videos()
    tools = tools_with(store, web, Presented())
    turn = turn_for(store)
    await tools.call(turn, "web_search", json.dumps({"query": "Ivan"}))
    await tools.call(turn, "find_video", ARGS)
    assert turn.searches == MAX_SEARCHES
    limit = await tools.call(turn, "find_video", ARGS)
    assert (
        limit == "Not searched: two searches per message are the limit. Don't search again; answer with what you have."
    )
    assert len(web.video_queries) == 1


async def test_on_a_call_one_search_in_all(store: Store) -> None:
    web = videos()
    tools = tools_with(store, web, Presented())
    conv = store.current_conversation()
    turn = Turn(message=say(store, conv, f"{prompts.CALL_MARK} the latest Ivan on Tech", T0))
    await tools.call(turn, "find_video", ARGS)
    again = await tools.call(turn, "find_video", ARGS)
    assert again.startswith("Not searched: one search per message is the limit")


async def test_a_query_with_private_details_is_refused_before_anything_is_sent_and_costs_nothing(store: Store) -> None:
    class Refusing(FakeWeb):
        async def search_videos(self, query: str) -> VideoResults:
            raise ValueError("not searched: the search words hold an email address")

    tools = tools_with(store, Refusing(), Presented())
    turn = turn_for(store)
    result = await tools.call(turn, "find_video", json.dumps({"what": "mail me at alex@example.com"}))
    assert result.startswith("Error: not searched") and turn.searches == 0


async def test_with_no_key_or_no_answer_it_is_the_answer_web_search_gives(store: Store) -> None:
    web = videos()
    web.down = True
    tools = tools_with(store, web, Presented())
    turn = turn_for(store)
    assert await tools.call(turn, "find_video", ARGS) == WEB_UNAVAILABLE
    assert turn.searches == MAX_SEARCHES  # another try would only wait again
    assert (
        await tools.call(turn_for(store, "and web_search?"), "web_search", json.dumps({"query": "x"}))
        == WEB_UNAVAILABLE
    )


async def test_no_match_says_so_and_texts_nothing(store: Store) -> None:
    presented = Presented()
    tools = tools_with(store, FakeWeb(), presented)
    assert await tools.call(turn_for(store), "find_video", ARGS) == prompts.VIDEO_FIND_NONE
    assert presented.offers == []


async def test_a_match_that_could_not_be_texted_says_so(store: Store) -> None:
    tools = tools_with(store, videos(), Presented(sent=False))
    assert await tools.call(turn_for(store), "find_video", ARGS) == prompts.VIDEO_FIND_NOT_SENT


async def test_the_words_searched_are_hers_in_the_users_words_and_newest_is_a_flag(store: Store) -> None:
    web = videos()
    tools = tools_with(store, web, Presented())
    await tools.call(turn_for(store), "find_video", json.dumps({"what": "  Ivan   on Tech ", "newest": "true"}))
    await tools.call(turn_for(store), "find_video", json.dumps({"what": "Ivan on Tech"}))
    assert web.video_queries == ["Ivan on Tech latest youtube", "Ivan on Tech youtube"]
    assert (await tools.call(turn_for(store), "find_video", json.dumps({"what": " "}))).startswith("Error:")


async def test_without_the_unlock_a_found_link_is_not_read(store: Store) -> None:
    tools = tools_with(store, videos(), Presented())
    turn = turn_for(store)
    await tools.call(turn, "find_video", ARGS)
    assert turn.unlocked == "" and turn.allowed_links == []

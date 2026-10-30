"""Places and routes with OpenStreetMap: the free default without a Google key, and the backup when Google fails or its
day is used up; the home area from the config or from the user's own "Home area:" fact."""

from __future__ import annotations

import contextlib
import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from local_assistant.config import Config
from local_assistant.language import CODES
from local_assistant.maps import (
    HERE_WORDS,
    HERE_WORDS_BY_LANGUAGE,
    HOME_WORDS,
    HOME_WORDS_BY_LANGUAGE,
    PLACES_URL,
    ROUTES_URL,
    Maps,
    MapsError,
    home_area,
    maps_for,
)
from local_assistant.osm import NOMINATIM_URL, TRANSIT_URL, Pacer, routing_url
from local_assistant.part_status import PartStatus, Record
from local_assistant.store import Fact, SourceKind, Store
from tests.fakes.fake_http import FakeHttp
from tests.test_maps import PLACES, ROUTE, Clock
from tests.test_osm import CAFES, TRANSIT, item

HOME = "Home Street 1, 1000 Town, NL"
HOME_SPOT = [item("Home Street", 52.37312, 4.89201, osm_id=9)]
STATION = [item("Central Station", 52.3791, 4.9003, osm_id=7)]
NOW = 1_791_640_000.0


class Pace:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def key_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "web.env"
    path.write_text(text)
    os.chmod(path, 0o600)
    return path


def maps(
    tmp_path: Path,
    http: FakeHttp,
    store: Store,
    *,
    google: bool = False,
    home: str = HOME,
    fact: str = "",
    cap: int = 30,
    osm_cap: int = 100,
    status: PartStatus | None = None,
) -> Maps:
    pace = Pace()
    return Maps(
        http,
        key_file(tmp_path, "GOOGLE_MAPS_API_KEY=g-key\n" if google else "BRAVE_API_KEY=b\n"),
        store,
        home=home,
        home_fact=lambda: fact,
        daily_cap=cap,
        osm_cap=osm_cap,
        tz=ZoneInfo("Europe/Amsterdam"),
        clock=Clock(),
        day=lambda: "2026-10-10",
        status=status,
        tag_key_path=tmp_path / "data" / "maps-home.key",
        pacer=Pacer(clock=pace, sleep=pace.sleep),
    )


def query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def box_centre(url: str) -> tuple[float, float]:
    west, north, east, south = (float(part) for part in query(url)["viewbox"].split(","))
    return round((north + south) / 2, 4), round((west + east) / 2, 4)


def nominatim_words(http: FakeHttp) -> list[str]:
    return [query(url)["q"] for url in http.urls() if url.startswith(NOMINATIM_URL)]


def stored(store: Store) -> list[str]:
    return [str(row[0]) for row in store._conn.execute("SELECT value FROM state")]


async def test_without_a_google_key_places_come_from_openstreetmap(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, CAFES)
    search = maps(tmp_path, http, store)
    assert not search.google
    found = await search.places("cafe")
    assert found[0].source == "OpenStreetMap" and found[0].instead == ""
    # the home area without the house number; then cafes within 1 km and, as 3 are found, within 3 km
    assert nominatim_words(http) == ["Home Street, 1000 Town, NL", "cafe", "cafe"]
    assert not any(url.startswith(("https://places.", "https://routes.")) for url in http.urls())
    assert query(http.urls()[1])["bounded"] == "1" and box_centre(http.urls()[1]) == (52.37, 4.89)
    centre, _, tag = (store.get_state("maps_home_area") or "").partition("|")
    assert centre == "52.37,4.89" and len(tag) == 64
    assert not any("Home Street" in value for value in stored(store))
    tag_key = tmp_path / "data" / "maps-home.key"
    assert stat.S_IMODE(tag_key.stat().st_mode) == 0o600 and len(tag_key.read_text().strip()) == 64


async def test_after_a_restart_the_home_area_is_not_looked_up_again(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, CAFES)
    await maps(tmp_path, http, store).places("cafe")
    await maps(tmp_path, http, store).places("bakery")
    assert nominatim_words(http) == ["Home Street, 1000 Town, NL", "cafe", "cafe", "bakery", "bakery"]


async def test_a_network_error_at_the_home_lookup_is_tried_again_next_time(tmp_path: Path, store: Store) -> None:
    """Review places-1: a timeout is no answer, so the day is not marked as failed; the next question looks again."""
    http = FakeHttp()
    http.fail(NOMINATIM_URL, TimeoutError())
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, CAFES)
    search = maps(tmp_path, http, store)
    with pytest.raises(MapsError, match="send your location"):
        await search.places("cafe")
    assert store.states_with_prefix("maps_home_failed_") == []
    found = await search.places("cafe")
    assert found[0].name == "Cafe Uno" and (store.get_state("maps_home_area") or "").startswith("52.37,4.89|")


async def test_google_and_openstreetmap_down_keep_no_mark_either(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.fail(PLACES_URL, TimeoutError())
    http.add(PLACES_URL, 200, {"places": [{"location": {"latitude": 52.37312, "longitude": 4.89201}}]})
    http.fail(NOMINATIM_URL, TimeoutError())
    search = maps(tmp_path, http, store, google=True)
    with contextlib.suppress(MapsError):
        await search.places("cafe")
    assert store.states_with_prefix("maps_home_failed_") == []
    with contextlib.suppress(MapsError):
        await search.places("cafe")
    assert (store.get_state("maps_home_area") or "").startswith("52.37,4.89|")


async def test_a_home_area_no_map_can_place_is_not_asked_again_today(tmp_path: Path, store: Store) -> None:
    """Both maps answered with no match: that is kept for the day (as the tag, never the words)."""
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, [])
    search = maps(tmp_path, http, store)
    for _ in range(2):
        with pytest.raises(MapsError, match="send your location"):
            await search.places("cafe")
    assert nominatim_words(http) == ["Home Street, 1000 Town, NL"]
    [(_, tag)] = store.states_with_prefix("maps_home_failed_")
    assert len(tag) == 64


async def test_a_home_area_fact_wins_over_the_config(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, CAFES)
    await maps(tmp_path, http, store, fact="Example Street, Town").places("cafe")
    assert nominatim_words(http)[0] == "Example Street, Town"


WRITTEN_HOME = "Example Street 12, Town"


@pytest.mark.parametrize(
    "near", [WRITTEN_HOME, "example street 12 , town", "Home area: Example Street 12, Town", "Example Street 12"]
)
async def test_the_home_area_written_out_is_home(tmp_path: Path, store: Store, near: str) -> None:
    """Her prompt holds the "Home area:" fact: written out as `near`, it is home, never words for the map."""
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, CAFES)
    search = maps(tmp_path, http, store, fact=WRITTEN_HOME)
    await search.places("cafe", near=near)
    assert nominatim_words(http) == ["Example Street, Town", "cafe", "cafe"]
    assert box_centre(http.urls()[1]) == (52.37, 4.89)
    assert await search.places_near(near) == "home"


@pytest.mark.parametrize("ends", [(WRITTEN_HOME, "Central Station"), ("Central Station", WRITTEN_HOME)])
async def test_a_route_to_or_from_the_home_area_written_out_ends_at_its_centre(
    tmp_path: Path, store: Store, ends: tuple[str, str]
) -> None:
    to, origin = ends
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(NOMINATIM_URL, 200, STATION)
    for ride in (((52.3791, 4.9003), (52.37, 4.89)), ((52.37, 4.89), (52.3791, 4.9003))):
        http.add(routing_url("foot", *ride), 200, {"code": "Ok", "routes": [{"duration": 600}]})
    route = await maps(tmp_path, http, store, fact=WRITTEN_HOME).route(to, "walk", origin=origin)
    assert not any("12" in words for words in nominatim_words(http))
    assert route.origin == ("home" if origin == WRITTEN_HOME else "Central Station")
    assert route.rough and "52.37%2C4.89" in route.link  # the rounded centre, never the exact point
    assert not any("52.37312" in url for url in http.urls())


async def test_google_gets_the_home_area_written_out_as_home(tmp_path: Path, store: Store) -> None:
    """With Google, the home area is looked up as written (documented) and searched around; the words are not sent
    as a place to search near, and a route's link names the rounded centre."""
    http = FakeHttp()
    http.add(PLACES_URL, 200, {"places": [{"location": {"latitude": 52.37312, "longitude": 4.89201}}]})
    http.add(PLACES_URL, 200, PLACES)
    http.add(ROUTES_URL, 200, ROUTE)
    search = maps(tmp_path, http, store, google=True, fact=WRITTEN_HOME)
    await search.places("cafe", near=WRITTEN_HOME)
    route = await search.route(WRITTEN_HOME, "bike", origin="Central Station")
    asked = [json.loads(body or b"{}") for _, body in http.sent]
    assert asked[1]["textQuery"] == "cafe" and asked[1]["locationBias"]["circle"]["center"]["latitude"] == 52.37
    assert asked[2]["destination"] == {"address": WRITTEN_HOME}
    assert "destination=52.37%2C4.89" in route.link and "Example" not in route.link


def test_home_and_here_have_words_in_each_of_the_11_languages() -> None:
    """Review places-8: "route home" in another language worked only when Gemma passed "home" in English."""
    assert set(HOME_WORDS_BY_LANGUAGE) == set(HERE_WORDS_BY_LANGUAGE) == set(CODES)
    assert all(HOME_WORDS_BY_LANGUAGE[code] and HERE_WORDS_BY_LANGUAGE[code] for code in CODES)
    for word in HOME_WORDS | HERE_WORDS:  # compared as written there: casefolded, single spaces
        assert word == " ".join(word.split()).casefold(), word
    assert not HOME_WORDS & HERE_WORDS


@pytest.mark.parametrize(
    "word", ["naar huis", "Thuis", "zu Hause", "chez moi", "casa", "домой", "自宅", "집", "w domu"]
)
async def test_a_route_home_in_another_language_ends_at_the_home_area(tmp_path: Path, store: Store, word: str) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(routing_url("bike", (52.3791, 4.9003), (52.37, 4.89)), 200, {"code": "Ok", "routes": [{"duration": 300}]})
    search = maps(tmp_path, http, store)
    search.set_here(52.3791, 4.9003)
    route = await search.route(word, "bike")
    assert route.rough and nominatim_words(http) == ["Home Street, 1000 Town, NL"]  # never the word itself


@pytest.mark.parametrize("word", ["bij mij in de buurt", "cerca de mí", "in der Nähe", "près de moi", "рядом", "近く"])
async def test_near_me_in_another_language_is_the_pin(tmp_path: Path, store: Store, word: str) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, CAFES)
    search = maps(tmp_path, http, store)
    search.set_here(52.373121, 4.892011)
    await search.places("cafe", near=word)
    assert nominatim_words(http) == ["cafe", "cafe"] and box_centre(http.urls()[0]) == (52.37, 4.89)
    assert (await search.places_near(word)).startswith("where you sent your location at ")


async def test_without_a_home_area_or_a_pin_she_asks_where(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    with pytest.raises(MapsError, match="send your location") as unknown:
        await maps(tmp_path, http, store, home="").places("cafe")
    assert unknown.value.retry and http.requests == []


async def test_near_a_named_place_it_is_located_first(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(NOMINATIM_URL, 200, CAFES)
    await maps(tmp_path, http, store, home="").places("cafe", near="Central Station")
    assert nominatim_words(http) == ["Central Station", "cafe", "cafe"]
    assert box_centre(http.urls()[1]) == (52.3791, 4.9003)  # around the station


async def test_the_first_named_place_after_a_start_is_looked_up_near_home(tmp_path: Path, store: Store) -> None:
    """Review places-2: before the home area was kept once, a named place had no area and went worldwide."""
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(NOMINATIM_URL, 200, CAFES)
    await maps(tmp_path, http, store).places("cafe", near="Central Station")
    assert nominatim_words(http)[:3] == ["Home Street, 1000 Town, NL", "Central Station", "cafe"]
    assert box_centre(http.urls()[1]) == (52.37, 4.89)  # the station looked up around the home area


async def test_the_start_of_a_route_is_looked_up_near_home(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(
        routing_url("foot", (52.3791, 4.9003), (52.3791, 4.9003)), 200, {"code": "Ok", "routes": [{"duration": 60}]}
    )
    await maps(tmp_path, http, store).route("Central Station", "walk", origin="Central Station")
    assert nominatim_words(http) == ["Home Street, 1000 Town, NL", "Central Station", "Central Station"]
    assert box_centre(http.urls()[1]) == (52.37, 4.89)


FAR_STATION = [item("Central Station", 48.8809, 2.3553, osm_id=8)]  # about 430 km from the home area


async def test_a_named_place_far_from_home_needs_its_town(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, FAR_STATION)
    with pytest.raises(MapsError, match="more than 50 km away: name the town too") as far:
        await maps(tmp_path, http, store).places("cafe", near="Central Station")
    assert far.value.retry and nominatim_words(http) == ["Home Street, 1000 Town, NL", "Central Station"]


async def test_a_pin_is_the_centre_rounded_to_about_1_km(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, CAFES)
    search = maps(tmp_path, http, store)
    search.set_here(52.373121, 4.892011)
    await search.places("cafe")
    assert nominatim_words(http) == ["cafe", "cafe"]
    assert box_centre(http.urls()[0]) == (52.37, 4.89)


async def test_a_walk_from_home_starts_at_the_home_area(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(routing_url("foot", (52.37, 4.89), (52.3791, 4.9003)), 200, {"code": "Ok", "routes": [{"duration": 900}]})
    route = await maps(tmp_path, http, store).route("Central Station", "walk")
    assert (route.origin, route.minutes, route.source, route.rough) == ("home", 15, "OpenStreetMap", True)
    assert route.link.startswith("https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=52.37%2C4.89")
    assert not any("Home Street 1" in url for url in http.urls())


async def test_a_route_home_ends_at_the_home_area(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(routing_url("bike", (52.3791, 4.9003), (52.37, 4.89)), 200, {"code": "Ok", "routes": [{"duration": 300}]})
    search = maps(tmp_path, http, store)
    search.set_here(52.3791, 4.9003)
    route = await search.route("home", "bike")
    assert route.rough and route.origin.startswith("where you sent your location at ")
    assert "route=52.379%2C4.9%3B52.37%2C4.89" in route.link  # the pin rounded to ~100 m in the link that is kept


async def test_public_transport_from_the_pin_by_transitous(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(TRANSIT_URL, 200, TRANSIT)
    search = maps(tmp_path, http, store)
    search.set_here(52.373121, 4.892011)
    route = await search.route("Central Station", "transit")
    assert (route.source, route.lines, route.departs, route.link, route.rough) == (
        "Transitous",
        ("bus N8", "tram M10", "train S5"),
        "14:07",
        "",
        False,
    )
    assert query(http.urls()[1])["fromPlace"] == "52.373121,4.892011"  # the pin itself, as Google gets it
    assert box_centre(http.urls()[0]) == (52.37, 4.89)  # Nominatim gets the area around it, rounded to ~1 km


async def test_a_walk_from_the_pin_looks_up_its_end_around_the_rounded_pin(tmp_path: Path, store: Store) -> None:
    pin, station = (52.373121, 4.892011), (52.3791, 4.9003)
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(routing_url("foot", pin, station), 200, {"code": "Ok", "routes": [{"duration": 600}]})
    search = maps(tmp_path, http, store)
    search.set_here(*pin)
    route = await search.route("Central Station", "walk")
    assert box_centre(http.urls()[0]) == (52.37, 4.89)  # never the pin to the metre
    assert http.urls()[1] == routing_url("foot", pin, station)  # the route's start is the pin itself
    assert "route=52.373%2C4.892%3B" in route.link and not route.rough


async def test_a_place_that_cannot_be_found_can_be_asked_again(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, [])
    search = maps(tmp_path, http, store)
    search.set_here(52.37, 4.89)
    with pytest.raises(MapsError, match="Nowhere Lane") as missing:
        await search.route("Nowhere Lane", "walk")
    assert missing.value.retry


async def test_google_answers_first_when_it_is_set_up(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, PLACES)
    search = maps(tmp_path, http, store, google=True)
    assert search.google
    found = await search.places("cafe", near="the city centre")
    assert found[0].source == "Google Maps" and http.urls() == [PLACES_URL]


@pytest.mark.parametrize(
    ("google", "why"),
    [((500, None), "Google Maps did not answer"), ((403, {"error": {}}), "Google Maps refused its key")],
)
async def test_when_google_fails_openstreetmap_answers_and_says_why(
    tmp_path: Path, store: Store, google: tuple[int, Any], why: str
) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, *google)
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(NOMINATIM_URL, 200, CAFES)
    status = PartStatus(tmp_path / "status.json", clock=lambda: NOW)
    found = await maps(tmp_path, http, store, google=True, status=status).places("cafe", near="Central Station")
    assert found[0].source == "OpenStreetMap" and found[0].instead == why
    snapshot = status.snapshot()
    assert snapshot["places"].failed == NOW and snapshot["osm"] == Record(ok=NOW)


async def test_after_google_did_not_answer_a_route_asks_it_nothing_more(tmp_path: Path, store: Store) -> None:
    """Review places-7: the home lookup of the fallback asked Google again, 8 s more within the request's 20 s."""
    http = FakeHttp()
    http.fail(ROUTES_URL, TimeoutError())
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(routing_url("foot", (52.37, 4.89), (52.3791, 4.9003)), 200, {"code": "Ok", "routes": [{"duration": 900}]})
    route = await maps(tmp_path, http, store, google=True).route("Central Station", "walk")
    assert route.source == "OpenStreetMap" and route.instead == "Google Maps did not answer"
    assert http.urls().count(ROUTES_URL) == 1 and PLACES_URL not in http.urls()


async def test_after_google_did_not_answer_a_search_asks_it_nothing_more(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.fail(PLACES_URL, TimeoutError())
    http.add(NOMINATIM_URL, 200, HOME_SPOT)
    http.add(NOMINATIM_URL, 200, CAFES)
    search = maps(tmp_path, http, store, google=True)
    found = await search.places("cafe")  # the home lookup timed out: the search itself skips Google
    assert found[0].source == "OpenStreetMap" and found[0].instead == "Google Maps did not answer"
    assert http.urls().count(PLACES_URL) == 1
    await search.places("bakery")  # a new question asks Google again
    assert http.urls().count(PLACES_URL) == 2


async def test_when_google_fails_and_openstreetmap_finds_nothing_she_is_told_both(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 403, {"error": {}})
    http.add(NOMINATIM_URL, 200, HOME_SPOT)  # the home area, which the station is looked up around
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(NOMINATIM_URL, 200, [])
    with pytest.raises(MapsError) as nothing:
        await maps(tmp_path, http, store, google=True).places("cafe", near="Central Station")
    assert str(nothing.value) == "Google Maps refused its key, and OpenStreetMap found no places for that."
    assert nothing.value.retry
    assert http.urls().count(PLACES_URL) == 1  # the home lookup after the refusal asks Google nothing more


async def test_when_googles_day_is_used_up_openstreetmap_answers(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, ROUTE)
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(NOMINATIM_URL, 200, STATION)
    http.add(TRANSIT_URL, 200, TRANSIT)
    search = maps(tmp_path, http, store, google=True, cap=1)
    await search.route("Schiphol", "transit", origin="Centraal Station")  # Google's one route of the day
    route = await search.route("Central Station", "transit", origin="Dam Square")
    assert route.source == "Transitous" and route.instead == "Google Maps' daily limit is reached"
    assert http.urls().count(ROUTES_URL) == 1


async def test_a_route_google_cannot_find_is_not_asked_of_openstreetmap(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, {"routes": []})
    with pytest.raises(MapsError, match="Google Maps found no route"):
        await maps(tmp_path, http, store, google=True).route("Nowhere", "car", origin="Dam Square")
    assert http.urls() == [ROUTES_URL]


async def test_when_both_fail_she_is_told_both(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 500, None)
    http.add(NOMINATIM_URL, 503, None)
    with pytest.raises(MapsError) as both:
        await maps(tmp_path, http, store, google=True).places("cafe", near="Central Station")
    assert "Google Maps did not answer" in str(both.value) and "OpenStreetMap" in str(both.value)


async def test_openstreetmap_has_its_own_daily_cap(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, CAFES)
    search = maps(tmp_path, http, store, osm_cap=1)
    search.set_here(52.37, 4.89)
    await search.places("cafe")
    with pytest.raises(MapsError, match="daily limit of 1 OpenStreetMap lookup"):
        await search.places("bakery")
    assert len(http.requests) == 1


@pytest.mark.parametrize("bad", ["pizza near anna@example.com", "call +31 6 1234 5678", "x" * 201])
async def test_private_words_never_go_to_openstreetmap(tmp_path: Path, store: Store, bad: str) -> None:
    http = FakeHttp()
    search = maps(tmp_path, http, store)
    search.set_here(52.37, 4.89)
    with pytest.raises(ValueError):
        await search.places(bad)
    with pytest.raises(ValueError):
        await search.route(bad, "walk")
    assert http.requests == []


async def test_private_words_are_refused_in_words_for_either_map(tmp_path: Path, store: Store) -> None:
    for google in (False, True):
        with pytest.raises(ValueError, match=r"private details never go to a map\. Leave them out\.$"):
            await maps(tmp_path, FakeHttp(), store, google=google).places("pizza near anna@example.com")


async def test_what_was_used_today(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(NOMINATIM_URL, 200, CAFES)
    free = maps(tmp_path, http, store)
    free.set_here(52.37, 4.89)
    await free.places("cafe")
    assert free.usage() == "OpenStreetMap lookups 2/100, routes 0/100 today"  # within 1 km, then 3 km
    both = maps(tmp_path, FakeHttp(), store, google=True)
    assert both.usage() == "Google Maps places 0/30, routes 0/30; OpenStreetMap lookups 2/100, routes 0/100 today"


def fact(number: int, text: str, kind: SourceKind = "remember") -> Fact:
    return Fact(number, text, kind, None, None, "2026-10-10 12:00:00")


@pytest.mark.parametrize(
    ("facts", "area"),
    [
        ([fact(1, "Home area: Example Street, Town")], "Example Street, Town"),
        ([fact(1, "Home: Home area: Example Street, Town")], "Example Street, Town"),
        ([fact(1, "home area : Old Road, Town"), fact(2, "Home area: New Road, City")], "New Road, City"),
        ([fact(1, "Home area: Example Street, Town", "chat")], ""),  # her own note, not the user's words
        ([fact(1, "Home area: Example Street, Town", "link")], ""),  # learned from a page
        ([fact(1, "Home area: anna@example.com")], ""),  # private details never go to a map
        ([fact(1, "The user lives in Town.")], ""),
        # the remember tool's own form, "Category: The user …" (review: dialogue-2)
        ([fact(1, "Home: The user's home area is Example Street, Town.")], "Example Street, Town"),
        ([fact(1, "Home: The user’s home area is Example Street, Town.")], "Example Street, Town"),
        ([fact(1, "The user's home area is Example Street, Town")], "Example Street, Town"),
        ([fact(1, "Home: The user's home area: Example Street, Town")], "Example Street, Town"),
        ([fact(1, "Home: The user's home area is Example Street, Town.", "chat")], ""),
        ([fact(1, "Home: The user's home is in Town.")], ""),
        ([fact(1, "Home: The user's sister's home area is Example Street, Town.")], ""),
    ],
)
def test_the_home_area_fact(facts: list[Fact], area: str) -> None:
    assert home_area(facts) == area


def test_her_maps_read_the_home_area_from_the_users_facts(tmp_path: Path, store: Store) -> None:
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({"aci-1"}),
        data_dir=tmp_path / "data",
        maps_enabled=True,
        maps_home="Config Road, Town",
        web_keys_path=key_file(tmp_path, "BRAVE_API_KEY=b\n"),
    )
    facts = [fact(1, "Home area: Example Street, Town")]
    built = maps_for(cfg, FakeHttp(), store, PartStatus(None), lambda: facts)
    assert built is not None and built._home_text() == "Example Street, Town"
    facts.clear()
    assert built._home_text() == "Config Road, Town"  # no fact: the config's
    assert maps_for(replace(cfg, maps_enabled=False), FakeHttp(), store, PartStatus(None), lambda: facts) is None


async def test_a_route_home_without_a_home_area_asks_for_it(tmp_path: Path, store: Store) -> None:
    with pytest.raises(MapsError, match="street and city") as unknown:
        await maps(tmp_path, FakeHttp(), store, home="").route("home", "walk", origin="Central Station")
    assert unknown.value.retry

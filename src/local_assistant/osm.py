"""Places and routes from OpenStreetMap's free services, with nothing to install (no account, no key):

- Nominatim (nominatim.openstreetmap.org, the OpenStreetMap Foundation): places with their opening hours, phone and
  website (`extratags`), and the place a route starts or ends at. Chosen over Photon (komoot) because one request gives
  the hours, phone and website as well (Photon has no opening hours), and because its usage and privacy policies are
  written down (Photon's public server states neither).
- FOSSGIS's OSRM servers (routing.openstreetmap.de): routes on foot, by bike and by car (no traffic).
- Transitous (api.transitous.org, MOTIS): public transport, for open-source, non-commercial apps.

Their policies, as code keeps them: a User-Agent naming Estia and its site; at most one request a second to each
service (`Pacer`); Nominatim's answers kept in memory for an hour (it asks for a cache), never on disk; no
autocomplete, no bulk or periodic requests (one request per question she asks). Only the words of a lookup, a centre
rounded to ~1 km and a route's two ends are sent; every name, address, hour, phone and website that comes back is
outside data (anyone can edit the map), cleaned here and never followed.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from local_assistant import osm_hours
from local_assistant.calendar_tools import clean
from local_assistant.mail_http import TimedHttp
from local_assistant.map_data import OSM, MapsError, Place
from local_assistant.part_status import PartStatus

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ROUTING_ROOT = "https://routing.openstreetmap.de"
TRANSIT_URL = "https://api.transitous.org/api/v6/plan"
OSM_PAGE = "https://www.openstreetmap.org"
COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
FIX_URL = "https://www.openstreetmap.org/fixthemap"
TRANSIT_SOURCES_URL = "https://transitous.org/sources/"
SITE = "https://estia.one"
DAILY_CAP = 100  # requests a day to each kind of service (lookups, routes): light use, as their policies ask
CACHE_SECONDS = 3600.0
CACHE_ITEMS = 200
NEAR_METRES = 1000.0  # a search around a centre: 1 km either way first
BOX_METRES = 3000.0  # then 3 km either way, when fewer than 5 places were found
FAR_KM = 50.0  # around a centre, a best match farther than this that is not a town or an area needs its town named
# Nominatim's place_rank below 26 is an area (a country down to a neighbourhood); from 26: streets, buildings, places
AREA_RANK = 26
EARTH_KM = 6371.0
MAX_PLACES = 5
NAME_CHARS = 100
ADDRESS_CHARS = 150
HOURS_CHARS = 120
PROFILES = {"bike": "bike", "walk": "foot", "car": "car"}  # FOSSGIS's servers by way of travel
ENGINES = {"bike": "fossgis_osrm_bicycle", "walk": "fossgis_osrm_foot", "car": "fossgis_osrm_car"}
VEHICLES = {
    "BUS": "bus",
    "COACH": "bus",
    "TRAM": "tram",
    "SUBWAY": "metro",
    "METRO": "metro",
    "RAIL": "train",
    "HIGHSPEED_RAIL": "train",
    "LONG_DISTANCE": "train",
    "NIGHT_RAIL": "train",
    "REGIONAL_RAIL": "train",
    "REGIONAL_FAST_RAIL": "train",
    "SUBURBAN": "train",
    "FERRY": "ferry",
    "FUNICULAR": "funicular",
    "AERIAL_LIFT": "cable car",
}
_STREET_MODES = frozenset({"WALK", "BIKE", "RENTAL", "CAR", "CAR_PARKING", "CAR_DROPOFF", "FLEX"})
_PHONE = re.compile(r"\+?[\d\s()./-]{6,30}")
_HOST = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
# a number's edges: never inside a word, except beside Japanese, Chinese or Korean script, which needs no space
# ("銀座1丁目2-3", "테헤란로123"), or right after an ordinal mark, a letter to Python ("nº12", Spanish's and Portuguese
# "number"; second fix review of 0.9.8, docs-01-house-number-exception-incomplete), and never inside a dash-joined run
# it is not all of ("1000-001", a postcode)
_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af"
_DASH = "-/\u2010\u2011\u2013\uff0d"
_BEFORE = rf"(?<![^\W{_CJK}ºª])(?<![{_DASH}])"
_AFTER = rf"(?![^\W{_CJK}])(?![{_DASH}])"
# a letter of any script but Japanese, Chinese or Korean (a number beside those ends at the number: "1丁目")
_LETTER = rf"[^\W\d_{_CJK}]"
# a house number's letters ("12a", "12Α", "12bis", "7к2", "12 bis", "12 B"), never an ordinal ("5th Avenue") or a word
_SUFFIX = rf"(?:(?!(?i:st|nd|rd|th)(?!{_LETTER})){_LETTER}{{1,3}}(?:\d{{1,5}})?|\s(?i:bis|ter|quater)|\s{_LETTER}(?!{_LETTER}))"
# a dash or slash part: a number with its letter ("5/7", "1-2-3", "5-7a") or letters ("123-H", "12/A", "123-III")
_PART = rf"\s?[{_DASH}]\s?(?:\d{{1,5}}{_LETTER}?|{_LETTER}{{1,4}})"
# in the street part any number, with its letters and its dash or slash parts (Japan's block-lot-house "1-2-3")
_NUMBER = re.compile(rf"{_BEFORE}\d{{1,5}}{_SUFFIX}?(?:{_PART})*{_AFTER}")
# in the street part, a number of 1-3 digits after a word of 1-3 letters and a dash, or any number right after "No" or
# "Nr", goes with the word: "A-12 Example Road", "Example Road B-7", "Example Loop No12" (third fix review of 0.9.8,
# spec-docs-fixrev3-01: no number started after a letter or a dash, so these went to the map with no advice); never a
# longer word's ("Example12"), a postcode's ("B-1000 Brussels", "D02 X285", "London W8"), or one after the first comma
_LABELLED_NUMBER = re.compile(
    rf"(?<![^\W{_CJK}])(?<![{_DASH}])(?:{_LETTER}{{1,3}}[{_DASH}]\d{{1,3}}|(?i:no|nr)\d{{1,5}})"
    rf"{_SUFFIX}?(?:{_PART})*{_AFTER}"
)
# after the first comma a house or flat number has 1-3 digits ("Via Roma, 5", "Flat 3, 12 Example Road"), so a postcode
# stays: 4 or 5 digits ("20100 Milano", "1000 AA Town"), or 3 and 2 ("123 45 Stockholm", the first group, kept); its
# letters only one letter (with a number after it, "7к2") or bis, ter, quater, so a UK postcode's "1AA" stays
_LATER_SUFFIX = rf"(?:{_LETTER}(?:\d{{1,3}})?|\s?(?i:bis|ter|quater)|\s{_LETTER}(?!{_LETTER}))"
_LATER_PART = rf"\s?[{_DASH}]\s?(?:\d{{1,3}}{_LETTER}?|{_LETTER}{{1,4}})"
_LATER_NUMBER = re.compile(
    rf"{_BEFORE}(\d{{3}}\s\d{{2}}){_AFTER}|{_BEFORE}\d{{1,3}}{_LATER_SUFFIX}?(?:{_LATER_PART})*{_AFTER}"
)
# after the first comma, 4-5 digits with an English street word later in the part are a house number, not a postcode
# ("c/o Alex Example, 1234 Main St"; release review of 0.9.8, G01); a postcode before a town named "St …" goes too
_STREET_WORDS = (
    "st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|ln|lane|way|ct|court|pl|place|hwy|highway|pkwy|parkway"
    "|cir|circle|ter|terrace|trl|trail|sq|square"
)
# with its dash or slash parts ("1234-B Main St"; fix review of 0.9.8, redteam-02)
_LATER_STREET_NUMBER = re.compile(
    rf"{_BEFORE}\d{{4,5}}{_LETTER}?(?:{_PART})*{_AFTER}(?=.*(?<!{_LETTER})(?i:{_STREET_WORDS})\.?(?!{_LETTER}))"
)
# a flat part's own words ("Apt 4B, 1234 Main St", "Floor 3", "Bldg 3", "Building B", "3rd Floor"): the part after it
# is the street, so any number there goes too; so does a part left with no word ("#4")
_UNIT_WORDS = frozenset(
    {"flat", "apt", "apartment", "unit", "suite", "ste", "room", "appartement", "appt", "app", "wohnung", "whg"}
    | {"apartamento", "apto", "dpto", "depto", "departamento", "appartamento", "interno", "int", "mieszkanie"}
    | {"lokal", "кв", "квартира", "διαμέρισμα"}
)
_FLAT_WORDS = _UNIT_WORDS | {"floor", "fl", "building", "bldg", "block", "blk"}
# a flat's or a floor's label beside its word: a letter or two, maybe with a number ("B", "B2"), an ordinal ("3rd"),
# or a number of 1-3 digits ("A-12" read as "A" and "12"; second fix review of 0.9.8, guard-03)
_FLAT_LABEL = re.compile(rf"{_LETTER}{{1,2}}\d{{0,3}}|\d{{1,3}}(?:st|nd|rd|th)?", re.IGNORECASE)
# after the first comma, a flat's, a building's or a block's own number of 4-5 digits right after its word ("Suite
# 1200", "Apt #1203", "Bldg 1234"); the word stays (fix review of 0.9.8, guards-G01); never a floor's word: a floor has
# no 4-5 digits, and "FL" is also Florida before its ZIP code, "Miami, FL 33101" (second fix review of 0.9.8, guard-02;
# the building's and the block's words back after the fix rounds' merge, guard-fixrev3-osm-01)
_LATER_FLAT_WORDS = _FLAT_WORDS - {"floor", "fl"}
_LATER_FLAT_NUMBER = re.compile(
    rf"(?<!{_LETTER})((?i:{'|'.join(sorted(map(re.escape, _LATER_FLAT_WORDS), key=len, reverse=True))})\.?\s?#?\s?)"
    rf"\d{{4,5}}{_LETTER}?(?:{_PART})*{_AFTER}"
)
_KEPT = re.compile(r"[^\W_]")  # a part with no letter or digit left ("#") goes
# in any part, a floor with its ordinal mark and its door: "3º B", "2º Esq", "3.º Izda.", "1º 2ª", "3° piano" (the word
# stays, as "Flat" does) (review places-9: SECURITY.md says OpenStreetMap never gets a house number)
_FLAT = re.compile(
    rf"{_BEFORE}\d{{1,3}}\s?\.?\s?[ºª°](?:\s?(?:esq|esqda|esquerdo|esquerda|dto|dta|dir|direito|direita|izq|izqda|izda"
    r"|izquierda|dcha|drcha|der|derecha|centro|ctro|int|ext|[a-z])\.?(?!\w))?",
    re.IGNORECASE,
)
_KIND_WORD = re.compile(r"[^\W\d_]{3,}")
# a word Nominatim finds only in names, and the phrase that finds the kind (live, 28 Oct, central Amsterdam: "bike
# repair" found nothing, "bicycle" and "bike shop" only names, "bicycle shop" 40 shops)
_SYNONYMS = {"bike": "bicycle", "bikes": "bicycle"}
_KINDS = {"bicycle": "bicycle shop"}
# words the map describes no place by: never a filter ("good italian restaurant" found nothing, review places-4)
_UNMAPPED = frozenset(
    {"good", "best", "nice", "great", "top", "decent", "popular", "cheap", "local", "nearest", "closest", "nearby"}
    | {"near", "open", "the", "and", "with", "for", "some", "any"}
)


def _version() -> str:
    try:
        return importlib.metadata.version("local-assistant")
    except importlib.metadata.PackageNotFoundError:
        return "0"


USER_AGENT = f"Estia/{_version()} (+{SITE})"


class Pacer:
    """At most one request a second to each service: a request waits its turn (Nominatim's, FOSSGIS's rule)."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        gap: float = 1.0,
    ) -> None:
        self._clock, self._sleep, self._gap = clock, sleep, gap
        self._next: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def turn(self, service: str) -> None:
        lock = self._locks.setdefault(service, asyncio.Lock())
        async with lock:
            wait = self._next.get(service, 0.0) - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._next[service] = self._clock() + self._gap


def routing_url(profile: str, start: tuple[float, float], end: tuple[float, float]) -> str:
    """FOSSGIS's route between two points (longitude first, as OSRM takes them)."""
    ends = f"{start[1]},{start[0]};{end[1]},{end[0]}"
    return f"{ROUTING_ROOT}/routed-{profile}/route/v1/driving/{ends}?overview=false&alternatives=false&steps=false"


def directions_link(start: tuple[float, float], end: tuple[float, float], mode: str) -> str:
    """OpenStreetMap's own directions page for a walk, a bike ride or a drive; "" for public transport (none there)."""
    engine = ENGINES.get(mode)
    if engine is None:
        return ""
    route = f"{start[0]},{start[1]};{end[0]},{end[1]}"
    return f"{OSM_PAGE}/directions?engine={engine}&route={quote(route, safe='')}"


def has_house_number(area: str) -> bool:
    """Whether a home area holds a house or flat number (`without_house_number` leaves one out)."""
    return _numbers_out(area)[1]


def without_house_number(area: str) -> str:
    """The home area without its house and flat numbers: only the street, the postcode and the city go to the map."""
    return ", ".join(_numbers_out(area)[0])


def _numbers_out(area: str) -> tuple[list[str], bool]:
    """The area's comma parts without their numbers (a floor with its door in any part; any number in the first part,
    the street, also one after a short word and a dash, "A-12", or after "No", "No12", and in the part after a flat's
    own, "Apt 4B, 1234 Main St", "Floor 3", "Building B", "3rd Floor", "#4"; in a later part one of 1-3 digits, or of
    4-5 after a flat's or a building's word, "Suite 1200", "Bldg 1234", or before an English street word, "1234 Main
    St", so a postcode stays), parts with no letter or digit left out, and whether a number went."""
    parts: list[str] = []
    changed = False
    street = True
    for raw in area.split(","):
        part = " ".join(raw.split())
        bare = _FLAT.sub(" ", part)
        if street:
            bare = _LABELLED_NUMBER.sub(" ", _NUMBER.sub(" ", bare))
        else:
            bare = _LATER_FLAT_NUMBER.sub(r"\1", bare)
            bare = _LATER_STREET_NUMBER.sub(" ", _LATER_NUMBER.sub(_postcode_kept, bare))
        bare = " ".join(bare.split())
        changed = changed or bare != part
        street = _flat_part(bare, cut=bare != part)
        if _KEPT.search(bare):
            parts.append(bare)
    return parts, changed


def _flat_part(bare: str, *, cut: bool) -> bool:
    """Whether a comma part, its numbers out, is a flat's or a floor's own, so the part after it is the street: only
    flat words (_FLAT_WORDS) and, beside one, labels ("Building B", "Apt B2", "3rd Floor"; fix review of 0.9.8, G01:
    such a part counted only with a number cut from it), with a number cut or a label ("Floor", once "Floor 3" lost its
    3; never a bare "FL", "Unit" or "Block": second fix review of 0.9.8, guard-02, "Miami, FL, 33101"); or nothing but
    signs left once a number went ("#4"). Read by its letter and digit runs, so a sign is no word ("Apt №", "Unit &":
    second fix review of 0.9.8, guard-03)."""
    words = [word.casefold() for word in re.findall(r"[^\W_]+", bare)]
    flat = any(word in _FLAT_WORDS for word in words)
    labelled = flat and any(word not in _FLAT_WORDS and _FLAT_LABEL.fullmatch(word) for word in words)
    return (
        bool(bare)
        and (cut or labelled)
        and all(not word or word in _FLAT_WORDS or (flat and _FLAT_LABEL.fullmatch(word)) for word in words)
    )


def _postcode_kept(match: re.Match[str]) -> str:
    return match.group(1) or " "


class OpenStreetMap:
    """The three services behind one door: `places`, `locate` and `route`. `budget(kind)` is asked before each
    request ("lookups" or "routes"; it raises MapsError when the day's cap is reached); `status` records each real
    answer as the part "osm"."""

    def __init__(
        self,
        http: TimedHttp,
        *,
        tz: ZoneInfo,
        region: str = "",
        clock: Callable[[], float] = time.time,
        pacer: Pacer | None = None,
        budget: Callable[[str], None] = lambda kind: None,
        status: PartStatus | None = None,
        limit_s: float = 8.0,
        quick_limit_s: float = 5.0,
    ) -> None:
        self._http = http
        self._tz = tz
        self._region = region.strip().lower()
        self._clock = clock
        self._pacer = pacer or Pacer()
        self._budget = budget
        self._status = status or PartStatus(None)
        self._limit_s, self._quick_limit_s = limit_s, quick_limit_s
        self._cache: dict[str, tuple[float, Any]] = {}  # Nominatim's answers by URL, in memory only

    async def places(self, what: str, centre: tuple[float, float], *, quick: bool = False) -> list[Place]:
        """Up to 5 places for `what` within 1 km of `centre`, else within 3 km. Nominatim finds a kind of place
        ("restaurant") but a word before it ("italian restaurant") only in names: with fewer than 5 found, the kind is
        searched too and ranked by how many of the other words its name, cuisine, kind, diets and services hold (review
        places-4: every word had to match, so "good italian restaurant" and "vegan restaurant" found nothing); those
        matching most come first, then the nearest. When none holds a word, the nearest of the kind come, each with
        the words the map does not list for it (`Place.unlisted`)."""
        words = [_SYNONYMS.get(word.casefold(), word) for word in what.split()]
        phrase = " ".join(words)
        items = await self._nearby(phrase, centre, limit=20, quick=quick, enough=lambda found: len(found) >= MAX_PLACES)
        ranked: list[tuple[tuple[str, ...], dict[str, Any]]] = [((), found) for found in items]  # matched as a whole
        if len(items) < MAX_PLACES and len(words) > 1:
            kind, wanted = _kind(words)
            seen = {_key(found) for found in items}

            def scored(found: list[dict[str, Any]]) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
                return [(_unlisted(one, wanted), one) for one in found if _key(one) not in seen]

            try:
                more = scored(
                    await self._nearby(
                        kind,
                        centre,
                        limit=40,
                        quick=quick,
                        enough=lambda found: len(items) + sum(not left for left, _ in scored(found)) >= MAX_PLACES,
                    )
                )
            except MapsError:
                if not items:
                    raise
                log.info("osm: the search by kind failed; the %d places found by the words are kept", len(items))
                more = []
            matched = [pair for pair in more if len(pair[0]) < len(wanted) or not wanted]
            ranked += matched or ([] if items else more)  # none holds a word: the nearest of the kind, each says so
        near: list[tuple[int, float, Place]] = []
        for left, found in ranked:
            spot = _spot(found)
            place = self._place(found, spot)
            if place is not None and spot is not None:
                near.append((len(left), _distance(centre, spot), replace(place, unlisted=left)))
        return [place for *_, place in sorted(near, key=lambda three: three[:2])[:MAX_PLACES]]

    async def locate(
        self, words: str, *, near: tuple[float, float] | None = None, quick: bool = False
    ) -> tuple[float, float] | None:
        """The point of a place or street the user named (Nominatim's best match, biased towards `near`). A bias is not a
        fence: around a centre, a best match more than 50 km away that is a street, a building or a place (not a town
        or an area) is refused unless the words name the town after a comma (review places-2: "Central Station" could
        come back from another country)."""
        params = {"q": words, "format": "jsonv2", "limit": "1", "accept-language": "en"}
        if near is not None:
            params["viewbox"] = _box(near)
        found = await self._nominatim(params, quick)
        first = found[0] if found else None
        spot = _spot(first) if isinstance(first, dict) else None
        far = spot is not None and near is not None and _distance(near, spot) * EARTH_KM > FAR_KM
        if far and "," not in words and not _is_area(first):
            raise MapsError(
                f'OpenStreetMap\'s best match for "{words}" is more than {FAR_KM:g} km away: name the town too '
                f'("{words}, Town").',
                retry=True,
            )
        return spot

    async def named(self, words: str) -> str | None:
        """Nominatim's best match for these words by its own name ("Ermou, Athens Municipality, Attica, Greece"), as
        outside text (cleaned, capped); "" for a match without one, None when nothing matches."""
        found = await self._nominatim({"q": words, "format": "jsonv2", "limit": "1", "accept-language": "en"}, False)
        first = found[0] if found else None
        if not isinstance(first, dict) or _spot(first) is None:
            return None
        display = first.get("display_name")
        return clean(display, ADDRESS_CHARS) if isinstance(display, str) else ""

    async def route(
        self, start: tuple[float, float], end: tuple[float, float], mode: str, *, quick: bool = False
    ) -> tuple[int, float, tuple[str, ...], str]:
        """Minutes, km, the public transport lines and when it leaves ("14:07", Transitous only)."""
        if mode == "transit":
            return await self._transit(start, end, quick)
        url = routing_url(PROFILES[mode], start, end)
        answer = await self._get("routes", "FOSSGIS", url, quick, _osrm_answer, missing_ok=True)
        if answer.get("code") != "Ok":
            raise MapsError("OpenStreetMap found no route.", retry=True)
        first = answer["routes"][0] if isinstance(answer["routes"][0], dict) else {}
        seconds, metres = _number(first.get("duration")), _number(first.get("distance"))
        return round(seconds / 60), round(metres / 1000, 1), (), ""

    async def _transit(
        self, start: tuple[float, float], end: tuple[float, float], quick: bool
    ) -> tuple[int, float, tuple[str, ...], str]:
        params = {
            "fromPlace": f"{start[0]},{start[1]}",
            "toPlace": f"{end[0]},{end[1]}",
            "numItineraries": "1",
            "maxItineraries": "1",
            "detailedLegs": "false",
        }
        answer = await self._get("routes", "Transitous", f"{TRANSIT_URL}?{urlencode(params)}", quick, _plan_answer)
        trips = answer["itineraries"]
        if not trips or not isinstance(trips[0], dict):
            raise MapsError("Transitous found no public transport route.", retry=True)
        trip = trips[0]
        legs = trip.get("legs")
        lines: list[str] = []
        for leg in legs if isinstance(legs, list) else []:
            mode = leg.get("mode") if isinstance(leg, dict) else None
            if not isinstance(mode, str) or mode in _STREET_MODES:
                continue
            name = leg.get("routeShortName") or leg.get("displayName") or ""
            # a mode without a word of ours is anyone's text: the cleaned line name alone (none: the leg is skipped)
            label = " ".join(part for part in (VEHICLES.get(mode, ""), clean(str(name), 20)) if part)
            if label:
                lines.append(label)
        departs = ""
        if isinstance(trip.get("startTime"), str):
            try:
                departs = datetime.fromisoformat(trip["startTime"]).astimezone(self._tz).strftime("%H:%M")
            except ValueError:
                departs = ""
        return round(_number(trip.get("duration")) / 60), 0.0, tuple(lines[:6]), departs

    async def _nearby(
        self,
        words: str,
        centre: tuple[float, float],
        *,
        limit: int,
        quick: bool,
        enough: Callable[[list[dict[str, Any]]], bool],
    ) -> list[dict[str, Any]]:
        """Up to `limit` matches within 1 km either way of `centre`, and when they are not `enough`, those within 3 km
        too (review places-6: in a 6 km box, "nearest" was the nearest of 20 arbitrary matches). A wider search that
        fails keeps what 1 km found; with nothing found, its error stands."""
        found = await self._search(words, centre, limit=limit, quick=quick, metres=NEAR_METRES)
        if enough(found):
            return found
        try:
            wider = await self._search(words, centre, limit=limit, quick=quick, metres=BOX_METRES)
        except MapsError:
            if not found:
                raise
            log.info("osm: the 3 km search failed; the %d places within 1 km are kept", len(found))
            return found
        seen = {_key(one) for one in found}
        return found + [one for one in wider if _key(one) not in seen]

    async def _search(
        self, words: str, centre: tuple[float, float], *, limit: int, quick: bool, metres: float
    ) -> list[dict[str, Any]]:
        params = {
            "q": words,
            "format": "jsonv2",
            "extratags": "1",
            "limit": str(limit),
            "viewbox": _box(centre, metres),
            "bounded": "1",
            "accept-language": "en",
        }
        return [found for found in await self._nominatim(params, quick) if isinstance(found, dict)]

    async def _nominatim(self, params: dict[str, str], quick: bool) -> list[Any]:
        if self._region:
            params["countrycodes"] = self._region
        url = f"{NOMINATIM_URL}?{urlencode(params)}"
        kept = self._cache.get(url)
        now = self._clock()
        if kept is not None and kept[0] > now:
            return list(kept[1])
        answer = await self._get("lookups", "Nominatim", url, quick, lambda body: isinstance(body, list))
        for stale in [key for key, (until, _) in self._cache.items() if until <= now]:
            del self._cache[stale]
        if len(self._cache) >= CACHE_ITEMS:
            del self._cache[next(iter(self._cache))]
        self._cache[url] = (now + CACHE_SECONDS, answer)
        return list(answer)

    async def _get(
        self,
        kind: str,
        service: str,
        url: str,
        quick: bool,
        readable: Callable[[Any], bool],
        *,
        missing_ok: bool = False,
    ) -> Any:
        """One GET to a service, in its turn and within the day's budget: its body when `readable`; HTTP and network
        failures and an unreadable body as MapsError (`missing_ok`: a 400 with a body is an answer too, OSRM's
        "NoRoute")."""
        self._budget(kind)
        await self._pacer.turn(service)
        limit = self._quick_limit_s if quick else self._limit_s
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            answer = await asyncio.wait_for(self._http("GET", url, headers, None, socket_timeout=limit), limit)
        except Exception as exc:  # network, timeout
            log.warning("osm: %s failed: %s", service, type(exc).__name__)
            self._status.failed("osm", "no_answer")
            raise MapsError("OpenStreetMap did not answer in time.") from None
        if answer.status == 200 or (missing_ok and answer.status == 400 and isinstance(answer.body, dict)):
            if not readable(answer.body):
                self._status.failed("osm", "bad_answer")
                raise MapsError("OpenStreetMap gave an answer that could not be read.")
            self._status.ok("osm")
            return answer.body
        log.warning("osm: %s answered HTTP %d", service, answer.status)
        self._status.failed("osm", "busy" if answer.status == 429 else "refused", answer.status)
        if answer.status == 429:
            raise MapsError("OpenStreetMap's limit is reached for now; try again in a while.")
        raise MapsError(f"OpenStreetMap did not answer (HTTP {answer.status}).")

    def _place(self, found: dict[str, Any], spot: tuple[float, float] | None) -> Place | None:
        raw_name = found.get("name")
        if spot is None or not isinstance(raw_name, str) or not raw_name.strip():
            return None
        name = clean(raw_name, NAME_CHARS)
        tags = _tags(found)
        hours_text = _tag(tags, "opening_hours")
        week = osm_hours.read(hours_text) if hours_text else None
        now = datetime.fromtimestamp(self._clock(), self._tz)
        today = f"{now:%A}: {osm_hours.day_text(week, now.weekday())}" if week is not None else ""
        website = _link(_tag(tags, "website") or _tag(tags, "contact:website"))
        phone = (_tag(tags, "phone") or _tag(tags, "contact:phone")).split(";")[0].strip()
        return Place(
            name=name,
            address=_address(found.get("display_name"), raw_name),
            rating=None,
            ratings=None,
            price="",
            open_now=osm_hours.open_at(week, now) if week is not None else None,
            today=today,
            url=website or _page(found),
            phone=clean(phone, 30) if _PHONE.fullmatch(phone) else "",
            website=(urlsplit(website).hostname or "").removeprefix("www.") if website else "",
            hours=clean(hours_text, HOURS_CHARS) if hours_text and week is None else "",
            holidays=week is not None and week.holidays,
            source=OSM,
        )


def _osrm_answer(body: object) -> bool:
    """OSRM's answer: a route ("Ok" with routes) or no route ("NoRoute", "NoSegment")."""
    if not isinstance(body, dict):
        return False
    routes = body.get("routes")
    return body.get("code") in ("NoRoute", "NoSegment") or (
        body.get("code") == "Ok" and isinstance(routes, list) and bool(routes)
    )


def _plan_answer(body: object) -> bool:
    return isinstance(body, dict) and isinstance(body.get("itineraries"), list)


def _tags(found: dict[str, Any]) -> dict[str, Any]:
    tags = found.get("extratags")
    return tags if isinstance(tags, dict) else {}


def _tag(tags: dict[str, Any], key: str) -> str:
    value = tags.get(key)
    return value.strip() if isinstance(value, str) else ""


def _spot(found: object) -> tuple[float, float] | None:
    if not isinstance(found, dict):
        return None
    try:
        lat, lng = float(found["lat"]), float(found["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return (lat, lng) if -90 <= lat <= 90 and -180 <= lng <= 180 else None


def _key(found: dict[str, Any]) -> tuple[object, object]:
    """One map object, however often a search finds it."""
    return found.get("osm_type"), found.get("osm_id")


def _box(centre: tuple[float, float], metres: float = BOX_METRES) -> str:
    """Nominatim's viewbox (west, north, east, south) `metres` either way around a centre."""
    lat_step = metres / 111_320
    lng_step = lat_step / max(math.cos(math.radians(centre[0])), 0.01)
    west, east = round(centre[1] - lng_step, 5), round(centre[1] + lng_step, 5)
    north, south = round(centre[0] + lat_step, 5), round(centre[0] - lat_step, 5)
    return f"{west},{north},{east},{south}"


def _is_area(found: object) -> bool:
    """Whether Nominatim's match is a town or a larger area (its place_rank), not a street, a building or a place."""
    rank = found.get("place_rank") if isinstance(found, dict) else None
    return isinstance(rank, int) and not isinstance(rank, bool) and rank < AREA_RANK


def _distance(a: tuple[float, float], b: tuple[float, float] | None) -> float:
    if b is None:
        return math.inf
    dlat, dlng = math.radians(b[0] - a[0]), math.radians(b[1] - a[1]) * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlng)


def _kind(words: list[str]) -> tuple[str, tuple[str, ...]]:
    """What to search for as the kind of place, and the words to rank its places by: the last word, or the phrase
    that finds a kind Nominatim knows only that way ("bicycle shop"); the others of 3 or more letters, except words
    the map describes no place by ("good", "nearest")."""
    folded = [word.casefold() for word in words]
    known = next((word for word in folded if word in _KINDS), "")
    kind = _KINDS[known] if known else words[-1]
    others = [word for word in folded if word != known] if known else folded[:-1]
    return kind, tuple(word for word in others if _KIND_WORD.fullmatch(word) and word not in _UNMAPPED)


def _unlisted(found: dict[str, Any], wanted: tuple[str, ...]) -> tuple[str, ...]:
    """The wanted words the map does not list for this place."""
    described = _described(found)
    return tuple(word for word in wanted if word not in described)


def _described(found: dict[str, Any]) -> str:
    """What a filter word may match: the name, the cuisine, the kind (type and category), the diets it serves
    ("diet:vegan" yes or only: "vegan") and the services it lists ("service:bicycle:repair" yes: "bicycle repair")."""
    tags = _tags(found)
    listed = [
        key.split(":", 1)[1].replace(":", " ").replace("_", " ")
        for key, value in tags.items()
        if key.startswith(("diet:", "service:")) and isinstance(value, str) and value.strip() in ("yes", "only")
    ]
    parts = (found.get("name"), _tag(tags, "cuisine"), found.get("type"), found.get("category"), *listed)
    return " ".join(part for part in parts if isinstance(part, str)).casefold()


def _address(display: object, name: str) -> str:
    """Nominatim's display name without the place's own name and the country: "5, Example Street, Centre, Town"."""
    if not isinstance(display, str):
        return ""
    parts = [part.strip() for part in display.split(",") if part.strip()]
    if parts and parts[0] == name.strip():
        parts = parts[1:]
    return clean(", ".join(parts[:-1] if len(parts) > 1 else parts), ADDRESS_CHARS)


def _link(url: str) -> str:
    """A place's website for the Sources note: a plain http(s) link on a normal host name, else ""."""
    if not url or len(url) > 300 or any(ch.isspace() or ord(ch) < 32 or ch in '<>"\\`' for ch in url):
        return ""
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    ok = parts.scheme in ("http", "https") and _HOST.match(host) and not parts.username and not parts.password
    return url if ok else ""


def _page(found: dict[str, Any]) -> str:
    """The place's own page on openstreetmap.org."""
    kind, number = found.get("osm_type"), found.get("osm_id")
    if kind in ("node", "way", "relation") and isinstance(number, int) and not isinstance(number, bool):
        return f"{OSM_PAGE}/{kind}/{number}"
    return ""


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0

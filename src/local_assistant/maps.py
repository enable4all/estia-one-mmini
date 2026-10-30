"""Places and routes for her: OpenStreetMap's free services by default (osm.py: Nominatim, FOSSGIS's routing servers
and Transitous, nothing to install, no account), and Google Places (Text Search, New) and Google Routes once a Google
Maps key is set up. Google is then asked first; OpenStreetMap answers when Google fails or its daily cap is reached,
and the result says so (`Place.instead`, `Route.instead`).

"Where the user is" is the last Signal location pin (3 hours, in memory only), else the home area: the user's own
"Home area: <street>, <city>" fact (`home_area`), else the private config's [maps] home; looked up once and kept
rounded to ~1 km. The database keeps the rounded centre and a tag of the home area's words (a keyed hash, to notice
new ones), never the words. Only place names, addresses and that rough area go out (a route from a pin uses the pin
itself; OpenStreetMap gets the home area without a house number, and a route from or to home starts or ends at the
rough area). A daily cap per service keeps Google's free monthly allowance and OpenStreetMap's light use; results are
data, never instructions, and are not stored.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, quote_plus, urlencode, urlsplit
from zoneinfo import ZoneInfo

from local_assistant.calendar_tools import clean
from local_assistant.config import Config
from local_assistant.keyfile import read_keys
from local_assistant.mail_http import TimedHttp
from local_assistant.map_data import GOOGLE, OSM, TRANSITOUS
from local_assistant.map_data import MapsError as MapsError
from local_assistant.map_data import Place as Place
from local_assistant.map_data import Route as Route
from local_assistant.osm import DAILY_CAP, OpenStreetMap, Pacer, directions_link, without_house_number
from local_assistant.part_status import PartStatus
from local_assistant.store import Fact, Store
from local_assistant.web_search import has_private_details

log = logging.getLogger(__name__)

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACE_FIELDS = (
    "places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.priceLevel,"
    "places.currentOpeningHours.openNow,places.currentOpeningHours.weekdayDescriptions,places.googleMapsUri"
)
ROUTE_FIELDS = (
    "routes.duration,routes.distanceMeters,"
    "routes.legs.steps.transitDetails.transitLine.nameShort,routes.legs.steps.transitDetails.transitLine.vehicle.type"
)
MODES = {"bike": "BICYCLE", "walk": "WALK", "transit": "TRANSIT", "car": "DRIVE"}
LINK_MODES = {"bike": "bicycling", "walk": "walking", "transit": "transit", "car": "driving"}
PRICES = {
    "PRICE_LEVEL_FREE": "free",
    "PRICE_LEVEL_INEXPENSIVE": "€",
    "PRICE_LEVEL_MODERATE": "€€",
    "PRICE_LEVEL_EXPENSIVE": "€€€",
    "PRICE_LEVEL_VERY_EXPENSIVE": "€€€€",
}
VEHICLES = {
    "BUS": "bus",
    "TROLLEYBUS": "bus",
    "TRAM": "tram",
    "SUBWAY": "metro",
    "METRO_RAIL": "metro",
    "HEAVY_RAIL": "train",
    "RAIL": "train",
    "COMMUTER_TRAIN": "train",
    "HIGH_SPEED_TRAIN": "train",
    "LONG_DISTANCE_TRAIN": "train",
    "FERRY": "ferry",
    "INTERCITY_BUS": "bus",
    "SHARE_TAXI": "taxi",
    "MONORAIL": "metro",
    "CABLE_CAR": "cable car",
    "GONDOLA_LIFT": "cable car",
    "FUNICULAR": "funicular",
}
MAX_PLACES = 5
WORDS_CHARS = 200
NAME_CHARS = 100
ADDRESS_CHARS = 150
PIN_SECONDS = 3 * 3600.0
BIAS_METRES = 3000.0
HOME_AREA_KEY = "maps_home_area"  # "lat,lng" rounded to 2 decimals, and the tag of the address it belongs to
HOME_FAILED_PREFIX = "maps_home_failed_"  # + the day: the tag of a home area no map could place that day
TAG_KEY_FILE = "maps-home.key"  # in the data folder (0600): the tag's key when there is no Google Maps key
_TAG = re.compile(r"^[0-9a-f]{64}$")
# "Home area: X", with a "Home:" category before it, or the remember tool's own form "Home: The user's home area is X"
_HOME_AREA = re.compile(
    r"^\s*(?:home\s*:\s*)?(?:the\s+user['’]s\s+)?home\s+area\s*(?::|\s+is\s+)\s*(.+)$", re.IGNORECASE | re.DOTALL
)
# Signal's location share ends with this link on its own line; a link inside a question is not "where I am"
_PIN = re.compile(r"https://maps\.google\.com/maps\?q=(-?\d{1,2}(?:\.\d+)?)(?:%2C|,)(-?\d{1,3}(?:\.\d+)?)(?![\d.])\s*$")
# Why Google failed in this places or route request ("" until it does; each sets it back): the request asks it nothing
# more, neither its fallback's home lookup nor its own search, so it stays within its 20 s and her result keeps the
# reason (review places-7: after a timeout the fallback asked Google again, 8 s more). Kept per asyncio task, so a
# call's question and a text's never share it.
_GOOGLE_FAILED: ContextVar[str] = ContextVar("google_failed", default="")
# "home" and "near me" as her arguments may say them, in each of her 11 languages (casefolded, single spaces): her
# tools ask for the English words, and code takes these too (review places-8: "route home" in another language worked
# only when she passed "home"). No bare "dom": Utrecht's and Cologne's cathedrals are "de Dom" and "der Dom".
HOME_WORDS_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "en": ("home", "my home", "my house", "my place", "home address", "at home"),
    "es": ("casa", "mi casa", "a casa", "en casa", "mi domicilio"),
    "it": ("casa", "a casa", "casa mia", "la mia casa", "mia casa"),
    "ko": ("집", "우리 집", "우리집", "내 집"),
    "pt": ("casa", "minha casa", "a minha casa", "em casa", "para casa"),
    "nl": ("thuis", "huis", "mijn huis", "naar huis", "mijn adres"),
    "pl": ("do domu", "w domu", "mój dom", "moj dom"),
    "de": ("zuhause", "zu hause", "nach hause", "daheim", "mein zuhause", "meine wohnung"),
    "ja": ("家", "自宅", "うち", "私の家", "自分の家"),
    "ru": ("дом", "домой", "дома", "мой дом", "мой адрес"),
    "fr": ("maison", "chez moi", "à la maison", "ma maison", "mon domicile"),
}
HERE_WORDS_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "en": ("", "me", "here", "my location", "current location", "where i am", "near me", "nearby"),
    "es": ("aquí", "aqui", "mí", "cerca de mí", "cerca de mi", "cerca", "mi ubicación", "donde estoy"),
    "it": ("qui", "qua", "vicino a me", "qui vicino", "la mia posizione", "dove sono"),
    "ko": ("여기", "나", "근처", "내 근처", "여기 근처", "내 위치", "현재 위치"),
    "pt": ("aqui", "mim", "perto de mim", "aqui perto", "minha localização", "onde estou"),
    "nl": (
        "hier",
        "mij",
        "bij mij",
        "in de buurt",
        "bij mij in de buurt",
        "hier in de buurt",
        "mijn locatie",
        "waar ik ben",
    ),
    "pl": ("tutaj", "tu", "mnie", "blisko mnie", "w pobliżu", "moja lokalizacja", "gdzie jestem"),
    "de": ("hier", "mich", "mir", "in der nähe", "in meiner nähe", "hier in der nähe", "mein standort", "wo ich bin"),
    "ja": ("ここ", "私", "近く", "この近く", "この辺", "私の近く", "現在地"),
    "ru": ("здесь", "тут", "я", "меня", "рядом", "рядом со мной", "моё местоположение", "мое местоположение", "где я"),
    "fr": ("ici", "moi", "près de moi", "à proximité", "ma position", "où je suis"),
}
HOME_WORDS = frozenset(word for words in HOME_WORDS_BY_LANGUAGE.values() for word in words)
HERE_WORDS = frozenset(word for words in HERE_WORDS_BY_LANGUAGE.values() for word in words)


class Mapper(Protocol):
    @property
    def google(self) -> bool: ...

    async def places(self, what: str, near: str = "", *, quick: bool = False) -> list[Place]: ...

    async def places_near(self, near: str = "") -> str: ...

    async def route(self, to: str, mode: str, origin: str = "", *, quick: bool = False) -> Route: ...


def find_pin(text: str) -> tuple[float, float] | None:
    """The coordinates of a Signal location pin (Signal sends `https://maps.google.com/maps?q=<lat>%2C<lng>`)."""
    if "?" in text.replace("maps?q=", ""):
        return None  # a question with a link in it, not a location share
    match = _PIN.search(text.strip())
    if match is None:
        return None
    lat, lng = float(match.group(1)), float(match.group(2))
    return (lat, lng) if -90 <= lat <= 90 and -180 <= lng <= 180 else None


def _today_name() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def home_tag(address: str, key: str) -> str:
    """The address's tag: HMAC-SHA256 keyed with the Google Maps key, else her own random key (maps-home.key); neither
    is in the database, so the database alone cannot confirm a guessed address near the kept centre. A new key reads as
    a new address: one lookup."""
    return hmac.new(key.encode(), address.strip().encode(), hashlib.sha256).hexdigest()


def forget_home_address(store: Store, home: str, keys_path: Path) -> None:
    """At start: no row keeps the home address, which belongs only in the private config. An earlier version kept it
    beside the home area ("lat,lng|<address>"), in the day's failed-lookup row and in the Directions links of its
    Sources notes. The area of the configured address keeps its centre with the address's tag; any other old area,
    and every failed-lookup row that holds no tag, is deleted (secure_delete overwrites the row); in a note's
    Directions link, a start or an end that is the address's link form becomes the kept centre, else "home" (the
    search index follows), and no other word of a note changes (review places-3: a one-word home was replaced in
    every note); then the log is written back into the file at once."""
    changed = False
    home = home.strip()
    key = read_keys(keys_path, ("GOOGLE_MAPS_API_KEY",)).get("GOOGLE_MAPS_API_KEY", "") if home else ""
    area, _, tail = (store.get_state(HOME_AREA_KEY) or "").partition("|")
    if tail and not _TAG.match(tail):
        if home and key and tail == home and "," in area:
            store.set_state(HOME_AREA_KEY, f"{area}|{home_tag(home, key)}")
        else:
            store.delete_state(HOME_AREA_KEY)
        changed = True
    for name, value in store.states_with_prefix(HOME_FAILED_PREFIX):
        if not _TAG.match(value):
            store.delete_state(name)
            changed = True
    if home:
        area, _, tail = (store.get_state(HOME_AREA_KEY) or "").partition("|")
        kept = bool(key) and "," in area and tail == home_tag(home, key)
        spot = quote_plus(area) if kept else "home"
        forms = dict.fromkeys((quote_plus(home), quote(home), quote(home, safe="")))
        ends = [(f"&{end}={form}&", f"&{end}={spot}&") for form in forms for end in ("origin", "destination")]
        changed = store.rewrite_notes(ends) > 0 or changed
    if changed:
        store.checkpoint()
        log.info("maps: the home address was removed from the database")


class Maps:
    """Her places and routes: Google first once a Google Maps key is set up, else OpenStreetMap; when Google fails or
    its daily cap is reached, OpenStreetMap answers instead and the result says why (`Place.instead`)."""

    def __init__(
        self,
        http: TimedHttp,
        keys_path: Path,
        store: Store,
        *,
        home: str,
        daily_cap: int,
        tz: ZoneInfo,
        region: str = "",
        clock: Callable[[], float] = time.time,
        day: Callable[[], str] = _today_name,
        limit_s: float = 8.0,
        quick_limit_s: float = 5.0,
        status: PartStatus | None = None,
        home_fact: Callable[[], str] = lambda: "",
        tag_key_path: Path | None = None,
        osm_cap: int = DAILY_CAP,
        pacer: Pacer | None = None,
    ) -> None:
        self._http = http
        self._keys_path = keys_path
        self._store = store
        self._home = home.strip()
        self._home_fact = home_fact  # the user's own "Home area: <street>, <city>" fact, else ""
        self._tag_key_path = tag_key_path or keys_path.with_name(TAG_KEY_FILE)
        self._cap = daily_cap
        self._osm_cap = osm_cap
        self._tz = tz
        self._region = region.strip()  # a two-letter country code; empty: none is sent
        self._clock = clock
        self._day = day
        self._limit_s = limit_s
        self._quick_limit_s = quick_limit_s
        self._pin: tuple[float, float, float] | None = None  # lat, lng, when
        self._status = status or PartStatus(None)  # what Google's and OpenStreetMap's last real answers were
        self._osm = OpenStreetMap(
            http,
            tz=tz,
            region=region,
            clock=clock,
            pacer=pacer,
            budget=self._spend_osm,
            status=self._status,
            limit_s=limit_s,
            quick_limit_s=quick_limit_s,
        )

    @property
    def google(self) -> bool:
        """Whether Google Maps is set up (its key is in web.env, read each time: a key added later needs no restart)."""
        return bool(self._key())

    def set_here(self, lat: float, lng: float) -> None:
        """The user sent a location pin: "near me" for the next 3 hours (memory only, never logged)."""
        self._pin = (lat, lng, self._clock())

    def here(self) -> tuple[float, float] | None:
        if self._pin is None or self._clock() - self._pin[2] > PIN_SECONDS:
            return None
        return self._pin[0], self._pin[1]

    def _pin_label(self) -> str:
        sent = datetime.fromtimestamp(self._pin[2] if self._pin else self._clock(), self._tz)
        return f"where you sent your location at {sent:%H:%M}"

    def _home_text(self) -> str:
        """The home area: the user's own "Home area:" fact, else the config's [maps] home; "" when neither."""
        return " ".join((self._home_fact() or self._home).split())

    def has_home(self) -> bool:
        """Whether a home area is known (the user's own "Home area:" fact, else the config's home, which setup writes):
        the empty /profile's home area line is left out then (owner, 26 Oct)."""
        return bool(self._home_text())

    def _is_home(self, words: str) -> bool:
        """Whether `words` are the home area written out (her prompt holds the "Home area:" fact, so she may pass it
        as a place): the whole area, without its house number, or its street part, with or without the number, case
        and marks aside. Such words are home: never words for the map, and a route ends at the rounded centre."""
        home = self._home_text()
        if not home or not words.strip():
            return False
        street = home.split(",", 1)[0]
        forms = {_plain(form) for form in (home, without_house_number(home), street, without_house_number(street))}
        return _plain(home_area_text(words) or words) in forms - {""}

    async def places_near(self, near: str = "") -> str:
        """Where a search for `near` is centred, for the answer: a named place, "home", the pin, or "" unknown."""
        word = "home" if self._is_home(near) else " ".join(near.split()).casefold()
        if word and word not in HOME_WORDS | HERE_WORDS:
            return " ".join(near.split())
        if word not in HOME_WORDS and self.here() is not None:
            return self._pin_label()
        return "home" if await self._home_center(quick=False) is not None else ""

    def usage(self) -> str:
        lookups, osm_routes = self._used("osm_lookups"), self._used("osm_routes")
        osm = f"{OSM} lookups {lookups}/{self._osm_cap}, routes {osm_routes}/{self._osm_cap}"
        if not self._key():
            return f"{osm} today"
        places, routes = self._used("places"), self._used("routes")
        return f"{GOOGLE} places {places}/{self._cap}, routes {routes}/{self._cap}; {osm} today"

    async def places(self, what: str, near: str = "", *, quick: bool = False) -> list[Place]:
        """Up to 5 places for `what`, near a place the user named, else near them (pin) or home."""
        _GOOGLE_FAILED.set("")
        what, near = _words(what, "what to look for"), " ".join(near.split())
        if self._is_home(near):
            near = "home"  # the home area written out: searched around its centre, its words never sent as a place
        word = near.casefold()
        named = bool(word) and word not in HOME_WORDS | HERE_WORDS
        if named:
            near = _words(near, "the place")
        instead = ""
        if self._key():
            try:
                return await self._google_places(what, near, named, word, quick)
            except MapsError as exc:
                if not exc.fallback:
                    raise
                instead = exc.fallback
        try:
            found = await self._osm_places(what, near, named, word, quick)
        except MapsError as exc:
            raise MapsError(f"{instead}, and {exc}" if instead else str(exc), retry=exc.retry) from None
        if instead and not found:  # no place to carry why: her result names both
            raise MapsError(f"{instead}, and OpenStreetMap found no places for that.", retry=True)
        return [replace(place, instead=instead) for place in found]

    async def _google_places(self, what: str, near: str, named: bool, word: str, quick: bool) -> list[Place]:
        query = f"{what} near {near}" if named else what
        body: dict[str, Any] = {"textQuery": query, "maxResultCount": MAX_PLACES, "languageCode": "en"}
        if self._region:
            body["regionCode"] = self._region.upper()
        if named:
            center = None
        elif word in HOME_WORDS or self.here() is None:
            center = await self._home_center(quick)
        else:
            center = self._rough(self.here())
        if center is not None:
            body["locationBias"] = {
                "circle": {"center": {"latitude": center[0], "longitude": center[1]}, "radius": BIAS_METRES}
            }
        answer = await self._ask("places", PLACES_URL, PLACE_FIELDS, body, quick)
        items = answer.get("places") if isinstance(answer, dict) else None
        if not isinstance(items, list):
            return []
        weekday = datetime.fromtimestamp(self._clock(), self._tz).strftime("%A")
        return [place for item in items[:MAX_PLACES] if (place := _place(item, weekday)) is not None]

    async def _osm_places(self, what: str, near: str, named: bool, word: str, quick: bool) -> list[Place]:
        """OpenStreetMap needs a centre: the place the user named, the pin (rounded to ~1 km) or the home area."""
        here = self.here()
        if named:
            # around the pin, else the home area, looked up now if it is not kept yet (review places-2)
            centre = await self._osm.locate(near, near=self._rough(here) or await self._home_center(quick), quick=quick)
            if centre is None:
                raise MapsError(f'OpenStreetMap found no place called "{near}".', retry=True)
        elif word in HOME_WORDS or here is None:
            centre = await self._home_center(quick)
        else:
            centre = self._rough(here)
        if centre is None:
            raise MapsError(
                "I don't know where you are: send your location in Signal, or name a place to search near.", retry=True
            )
        return await self._osm.places(what, centre, quick=quick)

    async def route(self, to: str, mode: str, origin: str = "", *, quick: bool = False) -> Route:
        """Time, distance and (by public transport) lines from `origin`, else from the pin, else from home."""
        _GOOGLE_FAILED.set("")
        mode = mode.strip().casefold()
        if mode not in MODES:
            raise ValueError("the way of travel must be bike, walk, transit or car.")
        to, origin = " ".join(to.split()), " ".join(origin.split())
        to_home = to.casefold() in HOME_WORDS or self._is_home(to)  # the home area written out is home too
        if not to_home:
            to = _words(to, "where to")
        word = origin.casefold()
        here = self.here()
        from_home = word in HOME_WORDS or self._is_home(origin) or (word in HERE_WORDS and here is None)
        if not from_home and word not in HERE_WORDS:
            origin = _words(origin, "where from")
        if (to_home or from_home) and not self._home_text():
            if to_home:
                raise MapsError("there is no home area yet: ask the user for their street and city.", retry=True)
            raise MapsError(
                "I don't know where you are: send your location in Signal, or name where you start.", retry=True
            )
        instead = ""
        if self._key():
            try:
                return await self._google_route(to, to_home, mode, origin, from_home, here, quick)
            except MapsError as exc:
                if not exc.fallback:
                    raise
                instead = exc.fallback
        try:
            route = await self._osm_route(to, to_home, mode, origin, from_home, here, quick)
        except MapsError as exc:
            raise MapsError(f"{instead}, and {exc}" if instead else str(exc), retry=exc.retry) from None
        return replace(route, instead=instead)

    async def _google_route(
        self,
        to: str,
        to_home: bool,
        mode: str,
        origin: str,
        from_home: bool,
        here: tuple[float, float] | None,
        quick: bool,
    ) -> Route:
        named = ""  # a start the user named goes into the link; from the pin none: the phone uses where it is
        if from_home:
            start: dict[str, Any] = {"address": self._home_text()}
            label = "home"
        elif here is not None and origin.casefold() in HERE_WORDS:
            start = {"location": {"latLng": {"latitude": here[0], "longitude": here[1]}}}
            label = self._pin_label()
        else:
            start, label, named = {"address": origin}, origin, origin
        body = {
            "origin": start,
            "destination": {"address": self._home_text() if to_home else to},
            "travelMode": MODES[mode],
            "languageCode": "en",
        }
        if self._region:
            body["regionCode"] = self._region.lower()  # Routes takes it lowercase, Places uppercase
        answer = await self._ask("routes", ROUTES_URL, ROUTE_FIELDS, body, quick)
        routes = answer.get("routes") if isinstance(answer, dict) else None
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise MapsError("Google Maps found no route.", retry=True)
        first = routes[0]
        seconds = _seconds(first.get("duration"))
        metres = first.get("distanceMeters")
        lines = _lines(first.get("legs"))
        km = round(metres / 1000, 1) if isinstance(metres, int | float) and not isinstance(metres, bool) else 0.0
        # the link is sent and stored as a note: a home end is the rounded centre, never the address (from home
        # without a centre: no origin, the phone uses where it is; to home without one: no link)
        centre = await self._home_center(quick) if to_home or from_home else None
        spot = f"{centre[0]},{centre[1]}" if centre is not None else ""
        start_at = spot if from_home else named
        link = {"api": "1"} | ({"origin": start_at} if start_at else {})
        link |= {"destination": spot if to_home else to, "travelmode": LINK_MODES[mode]}
        url = "" if to_home and not spot else f"https://www.google.com/maps/dir/?{urlencode(link)}"
        return Route(mode, round(seconds / 60), km, lines, label, url)

    async def _osm_route(
        self,
        to: str,
        to_home: bool,
        mode: str,
        origin: str,
        from_home: bool,
        here: tuple[float, float] | None,
        quick: bool,
    ) -> Route:
        """OpenStreetMap routes between two points: a home end is the home area (~1 km, so the time is rough), the
        pin is the pin, a named place is looked up. The link kept in the Sources note has the pin to ~100 m."""
        centre = await self._home_center(quick) if to_home or from_home else None
        if (to_home or from_home) and centre is None:
            raise MapsError("the home area could not be found on the map.", retry=True)
        if from_home and centre is not None:
            start, shown, label = centre, centre, "home"
        elif here is not None and origin.casefold() in HERE_WORDS:
            start, shown, label = here, (round(here[0], 3), round(here[1], 3)), self._pin_label()
        else:
            found = await self._osm.locate(
                origin, near=self._rough(here) or await self._home_center(quick), quick=quick
            )
            if found is None:
                raise MapsError(f'OpenStreetMap found no place called "{origin}".', retry=True)
            start, shown, label = found, found, origin
        if to_home and centre is not None:
            end = centre
        else:
            # the start may be the pin itself: Nominatim gets only its area, rounded to ~1 km (a 3 km bias box)
            located = await self._osm.locate(to, near=self._rough(start), quick=quick)
            if located is None:
                raise MapsError(f'OpenStreetMap found no place called "{to}".', retry=True)
            end = located
        minutes, km, lines, departs = await self._osm.route(start, end, mode, quick=quick)
        source = TRANSITOUS if mode == "transit" else OSM
        link = directions_link(shown, end, mode)
        return Route(mode, minutes, km, lines, label, link, source, departs, to_home or from_home)

    @staticmethod
    def _rough(spot: tuple[float, float] | None) -> tuple[float, float] | None:
        return None if spot is None else (round(spot[0], 2), round(spot[1], 2))

    def _tag_key(self) -> str:
        """What the home area's tag is keyed with: the Google Maps key when there is one (as before), else a random key
        of her own in a private file (0600, made once), never in the database."""
        return self._key() or _private_key(self._tag_key_path)

    def _kept_home(self) -> tuple[float, float] | None:
        """The home area's centre when it is kept already (no lookup)."""
        text = self._home_text()
        key = self._tag_key() if text else ""
        area, _, kept = (self._store.get_state(HOME_AREA_KEY) or "").partition("|")
        if not key or kept != home_tag(text, key) or "," not in area:
            return None
        lat, lng = area.split(",", 1)
        try:
            return float(lat), float(lng)
        except ValueError:
            return None

    async def _home_center(self, quick: bool) -> tuple[float, float] | None:
        """The home area rounded to ~1 km, looked up once: by Google when it is set up (the words as they are), else
        or after it fails by OpenStreetMap (without a house number). Kept with the words' tag, never the words (no key
        of any kind: nothing is looked up, nothing kept). A home area that every map asked answered with no match for
        is not looked up again that day; a network error, a refusal or a limit is no answer, so the next question
        looks again (review places-1: one timeout blocked "near me" and "route home" for the rest of the day)."""
        text = self._home_text()
        if not text:
            return None
        kept = self._kept_home()
        if kept is not None:
            return kept
        key = self._tag_key()
        if not key:
            return None
        tag = home_tag(text, key)
        failed_key = f"{HOME_FAILED_PREFIX}{self._day()}"
        if self._store.get_state(failed_key) == tag:
            return None
        center: tuple[float, float] | None = None
        answered = True  # every map asked gave an answer (a match or none)
        if self._key():
            try:
                answer = await self._ask(
                    "places", PLACES_URL, "places.location", {"textQuery": text, "maxResultCount": 1}, quick
                )
                spot = answer["places"][0]["location"]
                center = round(float(spot["latitude"]), 2), round(float(spot["longitude"]), 2)
            except LookupError:  # Google answered without a place ({} or an empty list)
                log.info("maps: Google found no place for the home area")
            except (MapsError, TypeError, ValueError) as exc:
                answered = False
                log.warning("maps: Google could not look up the home area: %s", type(exc).__name__)
        if center is None:
            try:
                center = self._rough(await self._osm.locate(without_house_number(text), quick=quick))
            except MapsError as exc:
                answered = False
                log.warning("maps: OpenStreetMap could not look up the home area: %s", type(exc).__name__)
        if center is None:
            if answered:
                self._store.set_state(failed_key, tag)
            return None
        self._store.set_state(HOME_AREA_KEY, f"{center[0]},{center[1]}|{tag}")
        return center

    def _key(self) -> str:
        return read_keys(self._keys_path, ("GOOGLE_MAPS_API_KEY",)).get("GOOGLE_MAPS_API_KEY", "")

    def _counter(self, api: str) -> str:
        return f"maps_{api}_{self._day()}"

    def _used(self, api: str) -> int:
        try:
            return int(self._store.get_state(self._counter(api)) or 0)
        except ValueError:
            return 0

    def _spend_osm(self, kind: str) -> None:
        """OpenStreetMap's own daily cap, per kind of service ("lookups", "routes"): light use, as its policies ask."""
        api = f"osm_{kind}"
        used = self._used(api)
        if used >= self._osm_cap:
            what = f"{OSM} lookup" if kind == "lookups" else f"{OSM} route"
            plural = "s" if self._osm_cap != 1 else ""
            raise MapsError(f"the daily limit of {self._osm_cap} {what}{plural} is reached; it starts again tomorrow.")
        self._store.set_state(self._counter(api), str(used + 1))

    async def _ask(self, api: str, url: str, fields: str, body: dict[str, Any], quick: bool) -> Any:
        key = self._key()
        if not key:
            self._status.failed("places", "no_key")
            raise MapsError("there is no Google Maps key yet.", fallback="Google Maps is not set up")
        if self._used(api) >= self._cap:
            what = "place searches" if api == "places" else "route" + (" searches" if self._cap != 1 else " search")
            raise MapsError(
                f"the daily limit of {self._cap} {what} is reached; it starts again tomorrow.",
                fallback="Google Maps' daily limit is reached",
            )
        if failed := _GOOGLE_FAILED.get():
            log.info("maps: %s not asked: Google failed earlier in this request", api)
            raise MapsError(f"{failed}.", fallback=failed)
        self._store.set_state(self._counter(api), str(self._used(api) + 1))
        headers = {"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": fields}
        limit = self._quick_limit_s if quick else self._limit_s
        try:
            answer = await asyncio.wait_for(
                self._http("POST", url, headers, json.dumps(body).encode(), socket_timeout=limit), limit
            )
        except Exception as exc:  # network, timeout, an unreadable body
            log.warning("maps: %s failed: %s", api, type(exc).__name__)
            self._status.failed("places", "no_answer")
            raise _failed("Google Maps did not answer in time.", "Google Maps did not answer") from None
        if answer.status == 200:
            self._status.ok("places")
            return answer.body
        log.warning("maps: %s answered HTTP %d", api, answer.status)
        self._status.failed("places", "busy" if answer.status == 429 else "refused", answer.status)
        if answer.status in (401, 403) or (answer.status == 400 and "API_KEY_INVALID" in json.dumps(answer.body)):
            raise _failed(
                "the Google key was refused (is the API switched on and the key allowed for it?).",
                "Google Maps refused its key",
            )
        if answer.status == 429:
            raise _failed("Google's limit for today is reached.", "Google Maps' limit for today is reached")
        raise _failed(f"Google Maps did not answer (HTTP {answer.status}).", "Google Maps did not answer")


def _failed(message: str, why: str) -> MapsError:
    """Google failed: the rest of this request asks it nothing more (`_GOOGLE_FAILED`), and OpenStreetMap answers."""
    _GOOGLE_FAILED.set(why)
    return MapsError(message, fallback=why)


def home_area_text(text: str) -> str:
    """The home area a fact's text names ("Home area: <street>, <city>", a "Home:" category before it is fine, or "Home:
    The user's home area is <street>, <city>", as the remember tool asks facts to be written), else ""."""
    match = _HOME_AREA.match(text)
    return " ".join(match.group(1).split()).rstrip(".") if match else ""


def home_area(facts: Sequence[Fact]) -> str:
    """The user's own home area: their newest fact that names one (`home_area_text`: "Home area: …", or "Home: The
    user's home area is …"), only from their own words (remember), never one she learned from a chat or from a page;
    "" without one, or when it holds private details (an email address, an account or a long number), which never go
    to a map."""
    for fact in sorted(facts, key=lambda kept: kept.id, reverse=True):
        area = home_area_text(fact.text) if fact.source_kind == "remember" else ""
        if area:
            return "" if len(area) > WORDS_CHARS or has_private_details(area) else area
    return ""


def maps_for(
    cfg: Config, http: TimedHttp, store: Store, status: PartStatus, facts: Callable[[], Sequence[Fact]]
) -> Maps | None:
    """Her places and routes with [maps] (None without it): the home area from the user's facts, else the config."""
    if not cfg.maps_enabled:
        return None
    return Maps(
        http,
        cfg.web_keys_path,
        store,
        home=cfg.maps_home,
        daily_cap=cfg.maps_daily_cap,
        tz=cfg.timezone,
        region=cfg.maps_region,
        status=status,
        home_fact=lambda: home_area(facts()),
        tag_key_path=cfg.data_dir / TAG_KEY_FILE,
    )


def _private_key(path: Path) -> str:
    """A random key of her own (64 hex characters) in a private file, made once; "" when it cannot be read or made
    (a file others can read, a link, a second name)."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    except OSError as exc:
        log.warning("maps: the home area's key could not be made: %s", type(exc).__name__)
        return ""
    else:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(32) + "\n")
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1:
            log.warning("maps: the home area's key file is not private; not used")
            return ""
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return text if _TAG.match(text) else ""


def _plain(text: str) -> str:
    """Words to compare: case, marks and spaces aside ("Example Street 12, Town" is "example street 12 town")."""
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def _words(text: str, what: str) -> str:
    text = " ".join(text.split())
    if not text:
        raise ValueError(f"{what} is empty.")
    if len(text) > WORDS_CHARS:
        raise ValueError(f"{what} is too long (at most {WORDS_CHARS} characters).")
    if has_private_details(text):
        raise ValueError(
            "not looked up: the words hold an email address, an account number or a long number such as a phone "
            "number, and private details never go to a map. Leave them out."
        )
    return text


def _place(item: object, weekday: str) -> Place | None:
    if not isinstance(item, dict):
        return None
    shown = item.get("displayName")
    raw_name = shown.get("text") if isinstance(shown, dict) else None
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    address = item.get("formattedAddress")
    rating = item.get("rating")
    count = item.get("userRatingCount")
    hours = item.get("currentOpeningHours")
    open_now = hours.get("openNow") if isinstance(hours, dict) else None
    days = hours.get("weekdayDescriptions") if isinstance(hours, dict) else None
    listed = [day for day in days if isinstance(day, str)] if isinstance(days, list) else []
    today = next((day for day in listed if day.startswith(f"{weekday}:")), "")  # by name: the order may vary
    url = item.get("googleMapsUri")
    return Place(
        name=clean(raw_name[: NAME_CHARS * 4], NAME_CHARS),
        address=clean(address[: ADDRESS_CHARS * 4], ADDRESS_CHARS) if isinstance(address, str) else "",
        rating=float(rating) if isinstance(rating, int | float) and not isinstance(rating, bool) else None,
        ratings=count if isinstance(count, int) and not isinstance(count, bool) else None,
        price=PRICES.get(str(item.get("priceLevel")), ""),
        open_now=open_now if isinstance(open_now, bool) else None,
        today=clean(today, 80),
        url=url if isinstance(url, str) and _is_maps_link(url) else "",
    )


def _is_maps_link(url: str) -> bool:
    """Only plain https links on Google's own hosts reach the Sources note."""
    if any(ch.isspace() or ord(ch) < 32 or ch in '<>"\\' for ch in url) or len(url) > 500:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    return parts.scheme == "https" and (host == "google.com" or host.endswith(".google.com") or host == "goo.gl")


def _seconds(value: object) -> float:
    """Routes gives "1490s"."""
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?s", value):
        return float(value[:-1])
    return 0.0


def _lines(legs: object) -> tuple[str, ...]:
    """The public transport lines in order, e.g. ("tram 13", "metro 52")."""
    found: list[str] = []
    for leg in legs if isinstance(legs, list) else []:
        steps = leg.get("steps") if isinstance(leg, dict) else None
        for step in steps if isinstance(steps, list) else []:
            details = step.get("transitDetails") if isinstance(step, dict) else None
            line = details.get("transitLine") if isinstance(details, dict) else None
            if not isinstance(line, dict):
                continue
            vehicle = line.get("vehicle")
            kind = vehicle.get("type") if isinstance(vehicle, dict) else None
            name = line.get("nameShort")
            label = " ".join(
                part
                for part in (VEHICLES.get(str(kind), ""), clean(str(name or ""), 20))  # an unknown kind: the name alone
                if part
            )
            if label:
                found.append(label)
    return tuple(found[:6])

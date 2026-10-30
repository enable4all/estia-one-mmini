"""What a map service answers her, the same for Google Maps and OpenStreetMap: places, routes, and the error she is told
when a lookup fails. Every text in them is outside data, cleaned where it is read (maps, osm)."""

from __future__ import annotations

from dataclasses import dataclass

GOOGLE = "Google Maps"
OSM = "OpenStreetMap"
TRANSITOUS = "Transitous"  # public transport on OpenStreetMap's side


class MapsError(Exception):
    def __init__(self, message: str, *, retry: bool = False, fallback: str = "") -> None:
        super().__init__(message)
        self.retry = retry  # Gemma may correct the request and ask again in the same answer (no route, unknown start)
        # Google failed or its day is used up: OpenStreetMap answers instead, and this says why ("" for no fallback)
        self.fallback = fallback


@dataclass(frozen=True)
class Place:
    name: str
    address: str
    rating: float | None
    ratings: int | None  # how many ratings
    price: str  # "€€" or ""
    open_now: bool | None  # None: not known (OpenStreetMap without readable hours)
    today: str  # today's opening hours, e.g. "Saturday: 5:00 – 10:00 PM"
    url: str  # its link for the Sources note: a Google Maps link, the place's website or its OpenStreetMap page; or ""
    phone: str = ""  # OpenStreetMap: the phone number the map lists
    website: str = ""  # OpenStreetMap: the website's name for her words ("cafe.example"); the link itself is `url`
    hours: str = ""  # OpenStreetMap: the hours as listed, when they could not be read
    holidays: bool = False  # the hours leave public holidays out (OpenStreetMap)
    source: str = GOOGLE
    instead: str = ""  # why OpenStreetMap answered instead of Google Maps, e.g. "Google Maps did not answer"
    unlisted: tuple[str, ...] = ()  # OpenStreetMap: words of the search the map does not list for it ("italian")


@dataclass(frozen=True)
class Route:
    mode: str  # bike, walk, transit, car
    minutes: int
    km: float  # 0.0: not known (OpenStreetMap's public transport)
    lines: tuple[str, ...]  # by public transport: "tram 13", "metro 52"
    origin: str  # "where you sent your location", "home" or the place the user named
    link: str  # directions to open; "" for a route home whose area is not known (never the address), or by Transitous
    source: str = GOOGLE  # Google Maps, OpenStreetMap, or Transitous for public transport on OpenStreetMap's side
    departs: str = ""  # Transitous: when the first part of the journey leaves, "14:07"
    rough: bool = False  # a home end is the home area (about 1 km), so the time is approximate
    instead: str = ""  # why OpenStreetMap answered instead of Google Maps

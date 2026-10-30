"""OpenStreetMap's opening hours (the `opening_hours` tag), read for "open now" and today's hours.

Only the common forms are read: "24/7", weekdays and ranges ("Mo-Fr", "Su-Th", "Sa,Su"), times and several spans a day
("11:30-15:00, 17:00-19:30"), hours past midnight ("18:00-02:00"), "off" or "closed", a later rule winning for its days
(";") and an added rule after a comma ("Mo-Th 16:00-00:00, Fr 16:00-03:00"). Public holidays ("PH ...") are left out
and said (`Week.holidays`): which days they are is not known here. Anything else (months, dates, week numbers, the nth
weekday, school holidays, "sunrise", an open end "17:00+", "||", comments, "unknown") makes the whole text unknown:
never a guess, as anyone can edit the map. Standard library only; the text is outside data and is only matched, never
shown from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

MAX_CHARS = 300  # a longer text is not read
DAY = 1440  # minutes
_DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_SPAN = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")
_DAY_ITEM = re.compile(r"([A-Z][a-z])(?:\s*-\s*([A-Z][a-z]))?")
_RULE = re.compile(r"(?:(?P<days>(?:[A-Z][a-z]|PH)(?:\s*[-,]\s*(?:[A-Z][a-z]|PH))*)\s+)?(?P<what>.+)", re.DOTALL)
# a comma that starts an added rule: after a time or "off", before a day ("Mo-Th 16:00-00:00, Fr,Sa 16:00-03:00")
_ADDED = re.compile(r"(?:(?<=\d)|(?<=off)|(?<=closed))\s*,\s*(?=(?:Mo|Tu|We|Th|Fr|Sa|Su|PH)\b)")


@dataclass(frozen=True)
class Week:
    """Each weekday's spans, Monday first: (start, end) in minutes from that day's midnight; an end past 1440 runs into
    the next day. `holidays`: a rule about public holidays was left out."""

    spans: tuple[tuple[tuple[int, int], ...], ...]
    holidays: bool = False


def read(text: str) -> Week | None:
    """The week of an `opening_hours` text, or None when any part of it is a form not read here."""
    text = text.strip()
    if not text or len(text) > MAX_CHARS:
        return None
    if text == "24/7":
        return Week(tuple(((0, DAY),) for _ in range(7)))
    days: list[list[tuple[int, int]]] = [[] for _ in range(7)]
    holidays = False
    for rule in text.split(";"):
        if not rule.strip():
            continue
        for number, part in enumerate(_ADDED.split(rule.strip())):
            found = _rule(part.strip())
            if found is None:
                return None
            selected, spans, holiday = found
            holidays = holidays or holiday
            for day in selected:
                days[day] = list(spans) if number == 0 else days[day] + list(spans)  # ";" replaces, "," adds
    return Week(tuple(tuple(sorted(day)) for day in days), holidays)


def _rule(part: str) -> tuple[list[int], tuple[tuple[int, int], ...], bool] | None:
    """One rule's days (all seven without any), its spans and whether it named public holidays; None: not read."""
    match = _RULE.fullmatch(part)
    if match is None:
        return None
    selected: list[int] = list(range(7))
    holiday = False
    if match.group("days"):
        selected, holiday = [], False
        for item in re.split(r"\s*,\s*", match.group("days")):
            if item == "PH":
                holiday = True
                continue
            day = _DAY_ITEM.fullmatch(item)
            if day is None or day.group(1) not in _DAYS or (day.group(2) and day.group(2) not in _DAYS):
                return None
            first = _DAYS.index(day.group(1))
            last = _DAYS.index(day.group(2)) if day.group(2) else first
            selected += [(first + step) % 7 for step in range((last - first) % 7 + 1)]
    what = match.group("what").strip()
    if what in ("off", "closed"):
        return selected, (), holiday
    spans: list[tuple[int, int]] = []
    for piece in re.split(r"\s*,\s*", what):
        span = _SPAN.fullmatch(piece)
        if span is None:
            return None
        start_h, start_m, end_h, end_m = (int(group) for group in span.groups())
        if start_h > 24 or end_h > 48 or start_m > 59 or end_m > 59:
            return None
        start, end = start_h * 60 + start_m, end_h * 60 + end_m
        if end <= start:
            end += DAY  # past midnight ("18:00-02:00", "16:00-00:00")
        if start >= DAY or end - start > DAY:
            return None
        spans.append((start, end))
    return selected, tuple(spans), holiday


def open_at(week: Week, when: datetime) -> bool:
    """Whether the place is open at `when` (local time), holidays aside."""
    day, minute = when.weekday(), when.hour * 60 + when.minute
    if any(start <= minute < end for start, end in week.spans[day]):
        return True
    return any(end > DAY and minute < end - DAY for _, end in week.spans[(day - 1) % 7])


def day_text(week: Week, day: int) -> str:
    """A weekday's hours (0: Monday): "08:00–20:00", "11:30–15:00, 17:00–19:30", "closed" or "open all day"."""
    spans = week.spans[day]
    if not spans:
        return "closed"
    if spans == ((0, DAY),):
        return "open all day"
    return ", ".join(f"{_clock(start)}–{_clock(end)}" for start, end in spans)


def _clock(minutes: int) -> str:
    minutes = minutes - DAY if minutes > DAY else minutes
    return f"{minutes // 60:02d}:{minutes % 60:02d}"

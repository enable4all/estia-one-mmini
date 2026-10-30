"""Her tools here (owner, 23 Oct: "make an inventory list of all the tools and make it available to [her], with
descriptions, it seems that she is not aware of the /tools"): code's list of every tool this install offers her, one
plain line each, then the programs behind them, one line each. Built from the live tool schemas (`Tools.inventory_tools`,
`StatusHost.inventory`), so a tool that is off never appears and a tool another branch adds appears with its own
description's first sentence. Her rules carry it right after the list of what she can do (never on a call:
`without_capabilities`), and /tools shows it word for word."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from local_assistant.config import DEFAULT_HOST_LABEL, DEFAULT_VOICE, POCKET_VOICES, canonical_voice

# fmt: off
__all__ = [
    "HELPER_LINES", "INVENTORY_HEADER", "INVENTORY_HELPERS_HEADER", "INVENTORY_HELPERS_RULE", "INVENTORY_RULE",
    "TOOL_LINES", "TOOL_LINES_NO_CALLS", "TOOL_LINES_ONE_PAGE", "TOOL_LINES_OSM", "Inventory", "inventory",
    "inventory_section",
    "inventory_text",
]
# fmt: on

# /tools' two headings (bold in Signal)
INVENTORY_HEADER = "**My tools here**"
INVENTORY_HELPERS_HEADER = "**The programs behind them**"
# her rules: the list's own section, right after the list of what she can do, before Safety
INVENTORY_RULE = (
    "Your tools here:\n- The list below is what /tools shows the user: every tool you have in this install, one line "
    "each, then the programs behind them. Asked whether you can do something or which tools you have, answer in the "
    "user's language and in plain words, from it and the list above, never from a project's files (a copy of your own "
    "code there is the user's, and may be old). A tool or program that is not on these lists does not exist here: "
    "never say you have one."
)
INVENTORY_HELPERS_RULE = "The programs behind them:"
# one plain line per tool ({host}, {Host}: the machine as the config names it); the same words in her rules and /tools
TOOL_LINES = {
    "search_history": "earlier conversations and the pages you sent, searched by words or names",
    "remember": "a lasting fact about you saved, only when you ask (/profile lists them)",
    "forget": "a fact taken off your profile, only when you ask",
    "learn_link": "a page you send, read for facts about you to save, only when you ask",
    "read_link": "a page or a YouTube video you send, summarised; a page's text kept for later searches",
    "mini_status": "{Host}'s health: the model, disk, memory, uptime, errors and Signal",
    "mail_digest": "your mail now: what needs a reply and new actions, read only",
    "mail_search": "your mail searched by sender, subject or words, read only",
    "mail_read": "one mail read in full, by its number; mail is never sent, moved, deleted or marked",
    "project_list": "your projects, each with its level (summary, docs or code)",
    "project_status": "one project's overview: its README, open items and recent changes, read only",
    "project_read": "one file of a project that git tracks, within the project's level, read only",
    "project_search": "a project's files searched for words, at level docs or code, read only",
    "search_documents": "your PDFs and text files searched; each answer names the document and the page",
    "read_document": "a page of one of your PDFs or text files read, up to three at a time, read only",
    "set_reminder": 'a call to you or a text at the time you ask for (a wake-up call or a reminder); set only when my "Reminder set" note says so',
    "list_reminders": "your reminders and wake-up calls, numbered",
    "cancel_reminder": "a reminder or wake-up call cancelled by its number",
    "calendar_agenda": "your calendar for a day or up to 14 days, read only, never changed",
    "calendar_search": "your calendar searched for an event in the coming year, read only, never changed",
    "web_search": "the web searched for current facts; answers name their sources",
    "places_search": "places near you or near a place you name, from Google Maps, with rating, price and opening hours",
    "route": "travel time, distance and public transport lines, from Google Maps",
    "device_status": "whether your lights and plugs are on",
    "device_set": "a light or plug switched on or off, or a light dimmed, by its name",
    "music_play": "music played on Spotify, on a speaker, by text message only",
    "music_control": "Spotify paused, resumed, skipped or turned up or down, by text message only",
    "music_status": "what Spotify is playing, where and how loud",
    "end_call": "the call ended after a short goodbye, on a call only",
    "chords": "chords transposed or moved for a capo, a chord's notes and the key of given chords, worked out on {host}",
}
# the reminders' lines when a "call me" reminder is a text (no [calls], or wake-up calls off: Config.wake_calls)
TOOL_LINES_NO_CALLS = {
    "set_reminder": 'a text at the time you ask for (a reminder); set only when my "Reminder set" note says so',
    "list_reminders": "your reminders, numbered",
    "cancel_reminder": "a reminder cancelled by its number",
}
# read_document's line when one call reads one page (under a 32,768-token context; review of 0.9.8, guards-2)
TOOL_LINES_ONE_PAGE = {"read_document": "a page of one of your PDFs or text files read, one at a time, read only"}
# the maps lines with OpenStreetMap (no Google Maps key): no rating or price there
TOOL_LINES_OSM = {
    "places_search": "places near you or near a place you name, from OpenStreetMap, with opening hours, phone and "
    "website where it lists them",
    "route": "travel time, distance and public transport lines, from OpenStreetMap (public transport from Transitous)",
}
_ASK_FIRST = "; some only after your yes"  # device_set's line with a device set to ask first
# the programs behind the tools, each only when it is on ({host}; {heard}: what the voice service turns into text;
# {spoken}: who speaks her answers, `_SPOKEN`; {how}: what the call helper does here; {services}, {brands}: the
# calendars' services and the lights' brands, each once)
HELPER_LINES = {
    "voice": "Voice service: {heard} turned into text (WhisperKit) and answers spoken ({spoken}), all on {host}",
    "calls": "Call helper: Signal calls with you, {how}, their sound encrypted end to end",
    "calendar": "Calendar helper: your {services} calendars, read only, never changed",
    "devices": "Device helper: your {brands} lights and plugs, switched on your home network only",
    "youtube": "YouTube helper: what a YouTube video you send says, for its summary; nothing of it stored",
    "documents": "Documents helper: your PDFs read on {host} with Apple's PDFKit and Vision, one at a time; it opens "
    "no network connection itself",
}
_CALLS_HOW = {False: "answered", True: "answered, and placed by me at a time you set (wake-up calls and reminders)"}
_HEARD = {False: "voice notes", True: "voice notes and calls"}  # what the voice service hears: calls only with [calls]
# who speaks her answers, by (her English voice is Pocket TTS's, jane speaks the six languages: `[voice]
# language_voice`): Pocket TTS its English voices and jane's six, Kokoro an English line Pocket TTS cannot say and its
# own voices, Supertonic-3 the other languages and a line Pocket TTS cannot say in one of the six (review of 0.9.8,
# guards-6: the line named Kokoro and Supertonic-3 only, while Pocket TTS speaks heart and jane, the defaults)
_SPOKEN = {
    (True, True): "Pocket TTS, with Kokoro and Supertonic-3 for what it cannot say",
    (True, False): "Pocket TTS and Kokoro in English, Supertonic-3 in the other languages",
    (False, True): "Kokoro in English, Pocket TTS and Supertonic-3 in the other languages",
    (False, False): "Kokoro in English, Supertonic-3 in the other languages",
}


@dataclass(frozen=True)
class Inventory:
    """Her list: one "• name: line" per tool, in the order she gets them, then one "• " line per program behind them."""

    tools: tuple[str, ...]
    helpers: tuple[str, ...]


def _listed(names: Sequence[str]) -> str:
    """Names in words, each once: a; a and b; a, b and c."""
    once = list(dict.fromkeys(names))
    return ", ".join(once[:-1]) + " and " + once[-1] if len(once) > 1 else "".join(once)


def _first_sentence(description: str) -> str:
    """A tool's own description, up to the end of its first sentence (a tool with no line of its own here)."""
    end = description.find(". ")
    return description if end < 0 else description[: end + 1]


def inventory(
    tools: Sequence[tuple[str, str]],
    *,
    host: str = DEFAULT_HOST_LABEL,
    voice: bool = False,
    calls: bool = False,
    wake_calls: bool = False,
    calendars: Sequence[str] = (),
    devices: Sequence[str] = (),
    ask_first: bool = False,
    documents: bool = False,
    osm: bool = False,
    english_voice: str = DEFAULT_VOICE,
    language_voice: str = "jane",
    read_pages: int = 3,
) -> Inventory:
    """Her list for this install, every line true here. `tools`: each offered tool's name and description
    (Tools.inventory_tools); `voice`: the voice service runs (voice notes or calls), `english_voice` her English voice
    in use (Pocket TTS's or Kokoro's), `language_voice` the config's voice of the six languages ("": Supertonic-3's);
    `read_pages`: the pages one read_document call reads; `calls`: [calls], `wake_calls`
    whether a "call me" reminder places a call (else the reminders' lines name no call); `calendars`: the services of
    the calendars the calendar helper reads (never Outlook's: Microsoft Graph); `devices`: the lights' and plugs'
    brands, `ask_first` whether one of them waits for the user's yes; `documents`: [documents]; `osm`: places and
    routes from OpenStreetMap (no Google Maps key). The YouTube helper comes with read_link."""
    named = {"Host": host[:1].upper() + host[1:], "host": host}
    own = {**TOOL_LINES, **({} if wake_calls else TOOL_LINES_NO_CALLS), **(TOOL_LINES_OSM if osm else {})}
    own.update(TOOL_LINES_ONE_PAGE if read_pages == 1 else {})
    own["device_set"] += _ASK_FIRST if ask_first else ""
    lines = tuple(
        f"• {name}: {own[name].format(**named) if name in own else _first_sentence(description)}"
        for name, description in tools
    )
    offered = {name for name, _ in tools}
    helpers = [
        (
            voice,
            HELPER_LINES["voice"].format(heard=_HEARD[calls], spoken=_spoken(english_voice, language_voice), host=host),
        ),
        (calls, HELPER_LINES["calls"].format(how=_CALLS_HOW[wake_calls])),
        (bool(calendars), HELPER_LINES["calendar"].format(services=_listed(calendars))),
        (bool(devices), HELPER_LINES["devices"].format(brands=_listed(devices))),
        ("read_link" in offered, HELPER_LINES["youtube"]),
        (documents, HELPER_LINES["documents"].format(host=host)),
    ]
    return Inventory(lines, tuple(f"• {line}" for on, line in helpers if on))


def _spoken(english_voice: str, language_voice: str) -> str:
    """Who speaks her answers with these voices (`_SPOKEN`)."""
    return _SPOKEN[canonical_voice(english_voice) in POCKET_VOICES, bool(language_voice)]


def inventory_text(listed: Inventory) -> str:
    """/tools: her list word for word, under its two headings."""
    helpers = ["", INVENTORY_HELPERS_HEADER, *listed.helpers] if listed.helpers else []
    return "\n".join([INVENTORY_HEADER, *listed.tools, *helpers])


def inventory_section(listed: Inventory) -> str:
    """Her rules' section with the list (prompts.rules), right before Safety."""
    helpers = [INVENTORY_HELPERS_RULE, *listed.helpers] if listed.helpers else []
    return "\n".join([INVENTORY_RULE, *listed.tools, *helpers])

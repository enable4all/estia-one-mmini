"""A video Estia finds herself (find_video): code's own search and ranking, one match texted, read only after a YES.

The search is code's (`web_search.WebSearch.search_videos`: Brave first, as its video data carries a video's channel and
length, Ollama as the backup with title and link only). A match's title, channel, length and day come only from those
results, never from a look at YouTube: what the search did not give is "not given". Titles and channels are outside
text: cleaned (hidden characters, chat tokens, markers and links out), at most 80 characters, shown only in code's own
message, never in her tool result.

Ranking, by code: only links `video.video_id` takes, built again from the ID (no list, no time, no other host); at most
3; the videos naming every word of the user's words in their title or channel first, then those naming some, then the
rest, each group in the search's order; with `newest`, inside each group the newest by the search's own date first and a
video with no date last.

One match, the yes and `another` (the pattern of the song flow): the best match is one message, its last line the reply
the user gives. Only the asker's next typed message counts, read by code in the bridge's queue order (never Gemma, no
second way in): a yes (the whole message, in the 11 languages' words) within 2 minutes of the match's message takes it,
`another` shows the next with a new 2 minutes, the list lives 10 minutes, in memory only, per account and conversation.
Anything else the asker sends next ends the match and goes on as usual. A text stored before the match's own message is
no answer to it (the stored id: stamps are whole seconds); both times are the message's arrival as the bridge stored it,
never when it is handled, so a yes typed during a call and read at its end still counts. A voice note's or a call's
transcript never counts. A yes unlocks that one link for the next answer (`Turn.unlocked`): the only way a found link is
ever read. Logs hold codes, counts and times, never the words searched, the ID or a title.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from local_assistant import prompts
from local_assistant.calendar_tools import clean_note
from local_assistant.devices import YES
from local_assistant.fetcher import strip_control_tokens
from local_assistant.language import CUTTING_IN, yes_form
from local_assistant.signal_text import unmarked
from local_assistant.store import Message, fold
from local_assistant.timefmt import iso_to_epoch
from local_assistant.video import length_words, video_id
from local_assistant.web_search import VideoResult

MAX_MATCHES = 3
CONFIRM_SECONDS = 120.0  # a yes counts this long after its match's message
LIST_SECONDS = 600.0  # the matches live this long after the first one's message, in memory
TEXT_CHARS = 80  # a match's title or channel, at most
_RANK_CHARS = 320  # ranked on this much of a title
REQUEST_CHARS = 400  # the user's own request kept for code's line after the yes, at most
# words that name no show or topic: left out of the words a title or channel must hold
NOISE = frozenset(
    {"a", "an", "the", "latest", "newest", "new", "last", "recent", "episode", "episodes", "video", "videos", "show",
     "channel", "youtube", "please"}
)  # fmt: skip
_SPOKEN = (prompts.VOICE_MARK, prompts.AUDIO_MARK, prompts.CALL_MARK, "[photo]", prompts.DOCUMENT_MARK)
_ANOTHER = "another"
Kind = Literal["yes", "another", "late", "none"]


@dataclass(frozen=True)
class Match:
    """One match, as the search gave it."""

    video: str  # the 11-character video ID (video.video_id): all that is kept of the link
    title: str | None  # None: not given
    channel: str | None  # None: not given (only Brave's video data has it)
    seconds: int | None  # None: not given (only Brave's video data has it)
    date: str | None  # the day the search dates it, "2026-10-28"; None: not given

    @property
    def link(self) -> str:
        """The link, built by code from the ID."""
        return f"https://www.youtube.com/watch?v={self.video}"


def plain(text: str, limit: int = TEXT_CHARS) -> str:
    """Outside text as one clean line of at most `limit` characters: no hidden character or chat token, no marker
    (Signal would style it), no link (Signal would make it tappable)."""
    return clean_note(unmarked(strip_control_tokens(text[: limit * 4])), limit)


def words(text: str) -> list[str]:
    """The words of a text, folded (case and accents aside)."""
    return re.findall(r"[^\W_]+", fold(text))


def _newness(date: str) -> int:
    """Sort key of a day: the newer, the smaller; no day last."""
    return -int(date.replace("-", "")) if date else 1


def rank(what: str, hits: Sequence[VideoResult], *, newest: bool = False) -> tuple[Match, ...]:
    """At most 3 matches for the user's words `what`, best first (the module's docstring has the order)."""
    want = [word for word in dict.fromkeys(words(what)) if word not in NOISE]
    found: dict[str, tuple[str, str, int | None, str]] = {}  # video ID → title, channel, seconds, day; first seen first
    for hit in hits:
        video = video_id(hit.url)
        if video is None:
            continue
        title, channel = plain(hit.title, _RANK_CHARS), plain(hit.channel, _RANK_CHARS)
        seconds = hit.seconds if isinstance(hit.seconds, int) and hit.seconds > 0 else None
        old = found.get(video)  # the same video again (Brave's video cluster after its web results): keep both's data
        if old:
            found[video] = (old[0] or title, old[1] or channel, old[2] or seconds, old[3] or hit.date)
        else:
            found[video] = (title, channel, seconds, hit.date)
    ranked: list[tuple[tuple[int, int, int], Match]] = []
    for index, (video, (title, channel, seconds, date)) in enumerate(found.items()):
        said = set(words(f"{title} {channel}"))
        named = sum(word in said for word in want)
        tier = 0 if named == len(want) else 1 if named else 2
        match = Match(video, plain(title) or None, plain(channel) or None, seconds, date or None)
        ranked.append(((tier, _newness(date) if newest else 0, index), match))
    return tuple(match for _, match in sorted(ranked, key=lambda entry: entry[0])[:MAX_MATCHES])


def query_for(what: str, *, newest: bool) -> str:
    """The words searched: the user's words, "latest" when the newest is wanted, and "youtube" (it brings the videos)."""
    return " ".join(part for part in (what, "latest" if newest else "", "youtube") if part)


def request_of(message: Message) -> str:
    """The user's own request as code's line quotes it after the yes: the message's own words without a voice or call
    mark, one clean line of at most 400 characters."""
    said = message.own_text.strip()
    for mark in (prompts.CALL_MARK, prompts.VOICE_MARK):
        said = said.removeprefix(mark).strip()
    return " ".join(strip_control_tokens(said.removeprefix(CUTTING_IN)[: REQUEST_CHARS * 2]).split())[:REQUEST_CHARS]


def length_text(seconds: int | None) -> str:
    return length_words(seconds) if seconds else prompts.VIDEO_NOT_GIVEN


@dataclass(eq=False)
class Offer:
    """The matches for one request, for the account and conversation that asked: in memory only. `index`: the match
    shown now; `started`: when the first match's message was stored, `shown`: when the one shown now was (seconds since
    the epoch, as the bridge stored them), `shown_id`: that message's stored id."""

    aci: str
    conversation_id: int
    request: str  # the user's own words that led to find_video
    matches: tuple[Match, ...]
    index: int = 0
    started: float = 0.0
    shown: float = 0.0
    shown_id: int = 0

    @property
    def match(self) -> Match:
        return self.matches[self.index]

    def alive(self, at: float) -> bool:
        """The list still lives at `at` (a message's arrival): 10 minutes from its first message."""
        return at - self.started <= LIST_SECONDS

    def after(self, message_id: int) -> bool:
        """The message was stored after the match shown now: a text stored before it was typed before the user saw the
        match, and is no answer to it."""
        return message_id > self.shown_id

    def confirms(self, message_id: int, at: float) -> bool:
        """A yes (stored as `message_id`, arriving at `at`) counts: stored after the match's message and 0 to 2 minutes
        from it."""
        return self.after(message_id) and 0 <= at - self.shown <= CONFIRM_SECONDS


def match_message(offer: Offer) -> str:
    """The match shown now as one message: its title, channel, length and day as the search gave them ("not given"
    otherwise), the link built from the ID, and the reply the user gives."""
    match = offer.match
    last = offer.index + 1 >= len(offer.matches)
    return (prompts.VIDEO_MATCH_LAST if last else prompts.VIDEO_MATCH).format(
        number=offer.index + 1,
        total=len(offer.matches),
        title=match.title or prompts.VIDEO_NOT_GIVEN,
        channel=match.channel or prompts.VIDEO_NOT_GIVEN,
        length=length_text(match.seconds),
        date=match.date or prompts.VIDEO_NOT_GIVEN,
        link=match.link,
    )


def typed_yes(text: str) -> bool:
    """A yes typed by the user: the whole message is one of the yes words (the devices' set: the 11 languages' and a few
    more), case and a final full stop or "!" aside. A voice note's, a call's, a photo's or a document's text never
    counts."""
    if text.lstrip().startswith(_SPOKEN):
        return False
    return yes_form(text) in YES


def typed_another(text: str) -> bool:
    """The whole typed message is `another` (case and a final full stop or "!" aside)."""
    return not text.lstrip().startswith(_SPOKEN) and yes_form(text) == _ANOTHER


@dataclass(frozen=True)
class Reading:
    """What the asker's next message was to the match that waited: `kind` "yes" (`offer`: the match to read), "another"
    (`offer`: moved to the next match, to show; `over`: none was left), "late" (a yes after its 2 minutes) or "none" (no
    match waited for it, or it ended the match: go on as usual)."""

    kind: Kind
    offer: Offer | None = None
    over: bool = False  # "another" with no match left: the list is over


class VideoOffers:
    """The waiting matches, one per account and conversation, in memory only."""

    def __init__(self) -> None:
        self._by: dict[tuple[str, int], Offer] = {}

    def put(self, offer: Offer) -> None:
        self._by[(offer.aci, offer.conversation_id)] = offer

    def get(self, aci: str, conversation_id: int) -> Offer | None:
        return self._by.get((aci, conversation_id))

    def drop(self, aci: str, conversation_id: int | None = None) -> None:
        """The account's match ends (in one conversation, or all of theirs)."""
        for key in [key for key in self._by if key[0] == aci and conversation_id in (None, key[1])]:
            del self._by[key]

    def clear(self) -> None:
        self._by.clear()

    def held(self) -> int:
        return len(self._by)

    def read(self, message: Message) -> Reading:
        """The asker's next message to the match that waits for it, by its arrival as the bridge stored it. A yes in time
        takes the match (the list ends); `another` moves to the next; a yes too late is told so; anything else ends the
        match. A message stored before the match's own message, or from another account or conversation, is no answer
        and changes nothing."""
        offer = self._by.get((message.peer_aci, message.conversation_id))
        if offer is None or not offer.after(message.id):
            return Reading("none")
        arrived = iso_to_epoch(message.created_at)
        if not offer.alive(arrived):
            self.drop(offer.aci, offer.conversation_id)
            return Reading("none")
        typed = message.kind == "text"
        if typed and typed_yes(message.text):
            self.drop(offer.aci, offer.conversation_id)
            return Reading("yes" if offer.confirms(message.id, arrived) else "late", offer)
        if typed and typed_another(message.text):
            if offer.index + 1 >= len(offer.matches):
                self.drop(offer.aci, offer.conversation_id)
                return Reading("another", offer, over=True)
            offer.index += 1
            return Reading("another", offer)
        self.drop(offer.aci, offer.conversation_id)
        return Reading("none")

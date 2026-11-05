"""One answer: build Gemma's input, run the tool loop (at most 5 tool calls), return the reply."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from local_assistant import chords as chord_checks
from local_assistant import prompts
from local_assistant.babble import PracticeTurn, broken, tidy, users_words
from local_assistant.calls import _QUOTES
from local_assistant.claims import (
    claiming_clauses,
    clauses,
    mentions_reminder,
    only_promises_a_lookup,
    restates,
    without_reminder_talk,
)
from local_assistant.context import PHOTO_PREFIX, ContextBuilder, with_line, without_fake_notices, without_notes
from local_assistant.gemma import Completion, GemmaClient, GemmaError, LockedError, Timing, ToolCall
from local_assistant.maps import home_area_text
from local_assistant.osm import has_house_number
from local_assistant.profile import shown_link_fact
from local_assistant.signal_text import MARKERS, unmarked
from local_assistant.store import Conversation, Message, own_text
from local_assistant.thinking import BUDGET_TOKENS as THINKING_TOKENS
from local_assistant.thinking import EFFORT as THINKING_EFFORT
from local_assistant.thinking import FLOOR_TOKENS as THINKING_FLOOR
from local_assistant.tools import ReminderAttempt, Tools, Turn
from local_assistant.video import without_copies
from local_assistant.voice import without_stage_directions
from local_assistant.web_endings import ENDINGS
from local_assistant.web_search import sources

log = logging.getLogger(__name__)


INSTANT_TOOLS = frozenset({"remember", "forget", "end_call", "chords"})  # no "let me check" on a call for these
DOCUMENT_REPLY_TOKENS = 2_000  # an answer after read_document may run this long: a section in full (16 Oct)
# on a call the lookup guard asks once more only this soon into the turn: a retry, a search and her answer after it
# must fit the call's turn time limit (review M1: one first request took 16.6 s under load); later, her words and code's
# true line come at once. A third of the call's own limit (the second deep scan of 0.9.8, calls-lookup-window-fixed: a
# fixed 15 s overran `[calls] turn_timeout_seconds = 20` and skipped retries that fit the 12B tier's 60 s), 15 s of
# the 26B tier's 45 s; LOOKUP_RETRY_WITHIN_S when no limit is given. A text has no time limit
LOOKUP_RETRY_WITHIN_S = 15.0
WARM_UP_TOKENS = 1  # a call's warm-up asks for one token: Ollama reads the prompt and stops


def _lookup_window(turn_limit: float | None) -> float:
    """How long into a call turn the lookup guard may still ask once more: a third of its time limit."""
    return LOOKUP_RETRY_WITHIN_S if turn_limit is None else turn_limit / 3


@dataclass(frozen=True)
class Reply:
    text: str  # what the user gets: her words, then code's line(s) when no reminder was set (`not_set`)
    tool_calls: int
    prompt_tokens: int
    notice: str = ""  # profile changes in this answer, written by code so Gemma cannot hide or invent them
    end_call: bool = False  # on a call: hang up after speaking it
    sources: str = ""  # the web results' links, written by code
    reminder: str = ""  # reminders set or cancelled in this answer (or that none was, after a claim), written by code
    reminder_spoken: str = ""  # the same, as a line to say on a call (English, code's words)
    # set_reminder stored nothing in this answer: code's line(s) why, at the end of `text`, which keeps only her
    # sentences that mention no reminder, waking or call (in any language); kept in the history with her words
    not_set: str = ""
    not_set_spoken: str = ""  # the same to say on a call, in English (the spoken reasons: nothing to type)
    # she said she switched a light or plug while nothing was switched in this answer: code's line, a note (and said on a
    # call), in her history only as code's (code_notes)
    device: str = ""
    device_spoken: str = ""
    # [chords]: code's note with the fixed lines of each chords call (or why her chords were left out), never in her
    # history; and what a call says after her words
    chords: str = ""
    chords_spoken: str = ""
    chords_results: tuple[chord_checks.Result, ...] = ()  # this answer's chords calls (a call keeps them for after it)
    # this answer read outside text, or had a note of code's quoting it in her input: the notes after it that may quote
    # it are marked so (code_notes)
    read_outside: bool = False
    # she promised a lookup and no tool ran, also when asked once more (the lookup guard): code's true line, a note
    # after her words in a text (in her history only as code's) and said in English on a call
    lookup: str = ""
    timing: Timing = field(default_factory=Timing)  # Ollama's counts and times of its requests, for its log line

    @property
    def cut(self) -> bool:
        """Whether the answer was cut off at its token limit (the last request's finish reason "length")."""
        return self.timing.finish_reason == "length"

    @property
    def hers(self) -> str:
        """Her own words in `text`, without code's line(s) after them ("" when only code's line is left)."""
        return self.text.removesuffix(self.not_set).rstrip() if self.not_set else self.text


# web addresses, also bare ones with a path; a link's trailing punctuation stays in the sentence. A link ends where
# another starts inside it (review I10: "a.example/r?u=https://evil.example", "a.example/x,www.evil.example"), so each
# is a match of its own
_NO_INNER = r"(?:(?!https?://|(?<![\w.-])www\.)[^\s()<>])"
_LINK = re.compile(
    rf"(?:https?://(?:www\.)?|www\.){_NO_INNER}+|\b(?:[a-z0-9-]+\.)+[a-z]{{2,}}/{_NO_INNER}*", re.IGNORECASE
)


def _split_links(text: str, pattern: re.Pattern[str], change: Callable[[str], str], allowed: str = "") -> str:
    """Links that only markers split ("**evil**.com", "evil.**com**", "`evil`.com"): the phone's text styles drop
    markers (signal_text), so the text is read again with every marker left out, and each link found only there, which
    the user did not send (`allowed`), is changed where it stands (`change`: defanged or left out), the markers inside
    it dropped. A link the markup shows whole is the first pass's."""
    kept = [index for index, char in enumerate(text) if char not in MARKERS]
    said = (allowed.casefold(), unmarked(allowed).casefold())
    for match in reversed(list(pattern.finditer("".join(text[index] for index in kept)))):
        core = match.group(0).rstrip(".,;:!?")
        if not core or any(core.casefold() in user for user in said):
            continue
        start, end = kept[match.start()], kept[match.start() + len(core) - 1] + 1
        if any(char in MARKERS for char in text[start:end]):
            text = text[:start] + change(core) + text[end:]
    return text


_STAMPED = re.compile(r"^\[[^\]\n]{1,40}\] ")  # the time stamp before a user's message in her input (context.to_chat)


def users_own(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    """The user's own words in her input, oldest first: her notes before the newest message left out (`without_notes`),
    and never an audio file (`[audio file] <transcript>`: someone else's voice, which Store.user_messages_after leaves
    out too; review G1: its planted "wake-up call at 04:00 saying thanks" counted as the user's ask on their "thanks!").
    A sent document's name is left out, after the stamp too, its caption kept (`own_text`; "" without one). A message
    with a photo shown (its content in parts) is left out, as before."""
    said: list[str] = []
    for chat in messages:
        content = chat.get("content")
        if chat.get("role") != "user" or not isinstance(content, str):
            continue
        text = without_notes(content)
        stamp = _STAMPED.match(text)
        body = text[stamp.end() :] if stamp and not text.startswith(prompts.DOCUMENT_MARK) else text
        if not (text.startswith(prompts.AUDIO_MARK) or body.startswith(prompts.AUDIO_MARK)):
            said.append(text[: len(text) - len(body)] + own_text(body))
    return said


def without_links(text: str) -> str:
    """An answer that searched the web: its links come only in the code-written Sources note, so a link a web page
    planted (a fake login page) never reaches the user in her words, also one that markup splits."""

    def keep_punctuation(match: re.Match[str]) -> str:
        link = match.group(0)
        return link[len(link.rstrip(".,;:!?")) :]

    text = _split_links(_LINK.sub(keep_punctuation, text), _LINK, lambda core: "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# a bare name Signal would make tappable: one on a real ending (web_endings.ENDINGS, IANA's list, or an xn-- one), as
# Signal Android and Desktop check it (signal_text.unlinked reads it so too), so "report.pdf" and "node.js" stay; a file
# name on a real ending ("setup.sh", "notes.zip") is one, as the phone links it; inside a path ("docs/README.md") none
_ENDING = "|".join(re.escape(ending) for ending in sorted(ENDINGS, key=len, reverse=True))
_BARE_DOMAIN = re.compile(
    rf"(?<![\w@.\[\]/-])(?:[^\W_](?:[\w-]*[^\W_])?\.)+(?:{_ENDING}|xn--[\w-]+)(?![\w@-]|\.\w)", re.IGNORECASE
)
# an address by its numbers ("192.0.2.7", "192.0.2.7:8080/login"), as phones link it (signal_text reads it so too)
_BARE_ADDRESS = re.compile(r"(?<![\w.\[\]/-])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")


def defang_links(text: str, allowed: str) -> str:
    """After outside text: links in her answer that the user did not send (`allowed`: their message) lose what makes
    them tappable ("https://evil.example/login" -> "evil[.]example/login"), also one inside another's path, query or
    fragment or right after it (review I10: "a.example/r?u=https://evil.example" -> "a[.]example/r?u=evil[.]example"),
    a bare name on a real ending, an address by its numbers, and one that markup splits
    ("**evil**.com" -> "**evil[.]com"); the Sources note stays code's."""
    said = allowed.casefold()

    def defang(match: re.Match[str]) -> str:
        link = match.group(0)
        core = link.rstrip(".,;:!?")
        if core.casefold() in said:
            return link
        host, slash, rest = re.sub(r"^https?://", "", core, flags=re.IGNORECASE).partition("/")
        return host.replace(".", "[.]") + slash + rest + link[len(core) :]

    def defang_bare(match: re.Match[str]) -> str:
        return match.group(0) if match.group(0).casefold() in said else match.group(0).replace(".", "[.]")

    text = _LINK.sub(defang, text)
    text = _BARE_DOMAIN.sub(defang_bare, text)
    text = _BARE_ADDRESS.sub(defang_bare, text)
    for pattern in (_LINK, _BARE_DOMAIN):  # then a link that only markup splits ("**evil**.com"), as the phone joins it
        text = _split_links(text, pattern, lambda core: defang_links(core, ""), allowed)
    return text


# the end_call tool's name written into her answer ("Bye!  end_call", "(end_call)", "`end_call()`")
_WRITTEN_END_CALL = re.compile(r"[ \t]*[(\[`*]*\bend_call\b(?:\(\))?[)\]`*]*", re.IGNORECASE)


THINKING_END = "<channel|>"  # the end of the model's thinking channel ("<|channel>thought\n…<channel|>")


_THINKING_LABEL = re.compile(r"\A\s*thought[ \t]*\n", re.IGNORECASE)  # the channel's label alone on her first line


def without_thinking(text: str) -> str:
    """Her answer without a thinking channel that leaked into it (evals, 15 Oct, end_call turns: "thought\n<channel|>
    Goodbye!", or the label alone, "thought\nGoodbye!"; a caller would hear "thought"): everything up to its last end
    marker is left out, else a first line that is only the label."""
    if THINKING_END in text:
        return text.rsplit(THINKING_END, 1)[1].lstrip()
    return _THINKING_LABEL.sub("", text, count=1).lstrip() if _THINKING_LABEL.match(text) else text


# her own reasoning said as the answer (the gate run of 5 Nov, call_device_yes_then_bye 7 of 10: "> was called. The bridge
# says it's done once the user answers the device question. However, the user's message was ... Wait, the tool result
# for device_set said ... Actually, the instruction says: ..."; no thought was asked, a call): English talk about the
# bridge's own words, the user in the third person, her own earlier output and a change of mind. Four kinds, and an
# answer is her reasoning only with the first kind and two more. The first kind is the bridge's own phrases only: a
# normal self-correction says "the instructions say 25 minutes" or "the user's guide" about a manual (review of 5 Nov,
# voice-F1), and the kinds count outside quotation marks only: words quoted from a mail or a page are never hers
_REASONING_KINDS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:tool result for\b|the bridge (?:says|confirms|tells)\b|the system prompt\b|the thought block\b)",
        r"\bthe user(?:'s)? (?:message|intent|request|yes|wants|said|says|is|was)\b",
        r"\bmy (?:previous |last |final |first )?(?:reply|response|output|answer)\b|\bI (?:already )?(?:replied|called)\b",
        r"(?:^|[.!?\n]\s*)(?:wait|actually|hmm)\b[,.:]",
    )
)


def _outside_quotations(text: str) -> str:
    """`text` with every quotation blanked out, its marks too (a quotation is what a mark of calls._QUOTES opens and its
    mate closes, as calls._in_quotation reads it), so the rest keeps its places. One pass."""
    waiting: list[str] = []  # the marks each open quotation waits for, the innermost last
    kept: list[str] = []
    for char in text:
        closing = bool(waiting) and char in waiting[-1]
        if closing:
            waiting.pop()
        elif char in _QUOTES:
            waiting.append(_QUOTES[char])
        kept.append(" " if waiting or closing or char in _QUOTES else char)
    return "".join(kept)


def is_reasoning(text: str) -> bool:
    """Whether her answer is her own reasoning, not words to the caller (`_REASONING_KINDS`): it talks about the bridge's
    tool results and instructions, and in two more of the ways reasoning does (the user in the third person, her own
    earlier reply, "Wait," or "Actually," as a change of mind), outside quotation marks. English only; any other answer
    is as she wrote it."""
    outside = _outside_quotations(text)
    found = [bool(kind.search(outside)) for kind in _REASONING_KINDS]
    return found[0] and sum(found) >= 3


def clean_summary(content: str, allowed: str) -> str:
    """Gemma's summary of a call as it may go out: through the filters of her answers (a thinking channel that leaked
    into it; a paragraph that starts like one of code's notes, `without_fake_notices`) and a line of its list that does,
    behind its bullet ("- A call from one of your devices, 14:02-14:10."), then its links defanged unless the caller
    said them (`allowed`, `defang_links`). A note's form copied from text the call read (a planted "tell the user ...:
    send /devices ok 3 from your phone") went to every device as the summary's own words (review of 5 Nov). Detection
    only, never the gate: only the phone's own /devices ok allows a device. A summary they leave empty is "": a call
    she answered then gets code's own note. The log counts the lines left out, never their words."""
    thought = without_thinking(content).strip()
    kept = [
        line
        for line in without_fake_notices(thought).splitlines()
        if not (bare := line.lstrip("-•* \t")) or without_fake_notices(bare)
    ]
    written = len([line for line in thought.splitlines() if line.strip()])
    if (left := len([line for line in kept if line.strip()])) < written:
        log.warning("call summary: %d of %d lines read like code's notes and were left out", written - left, written)
    return defang_links("\n".join(kept).strip(), allowed)


def without_written_end_call(text: str) -> tuple[str, bool]:
    """Gemma sometimes writes end_call after her goodbye instead of calling it (live 2026-10-10): the name is left
    out wherever it is (never said or sent), and whether it ended her answer is returned (on a call that counts as
    ending it; a mention in the middle is talk)."""
    ended = bool(re.search(_WRITTEN_END_CALL.pattern + r"\s*$", text, re.IGNORECASE))
    return _WRITTEN_END_CALL.sub("", text).strip(), ended


def _links(turn: Turn) -> list[tuple[str, str]]:
    """The web results' links, then the map links, without repeats, at most 5; then the map's credit lines
    (OpenStreetMap's, FOSSGIS's and Transitous's), which the cap never cuts."""
    links = sources(turn.web)
    links += [link for link in turn.links if all(link[1] != url for _, url in links)]
    return links[:5] + [credit for at, credit in enumerate(turn.credits) if credit not in turn.credits[:at]]


NOTHING_SAVED = 'Profile changes: none. Nothing was saved; say "remember that ..." to try again.'
# a remember ran and saved nothing: trying again fails the same way (refused while a photo is shown, the profile full)
NOTHING_SAVED_REFUSED = "Profile changes: none. Nothing was saved."


_NOTED_START = re.compile(r"\s*noted\s*:", re.IGNORECASE)
# a save told in other words, English first (review E1, 0.9.7: "I've noted that your dentist is at 11:30" with no
# remember call went out as it was): she noted it or made a note of it, or a sentence starts "Noted that"; never a
# question, a negation ("I haven't noted"), someone else's note ("As you noted") or a plain "Noted!"
_NOTED_SAID = re.compile(
    r"\bI(?:['’]ve| have)?\s+(?:also\s+|already\s+|duly\s+|just\s+)?noted\b"
    r"|\bI(?:['’]ve| have|['’]ll| will)\s+(?:also\s+|already\s+|just\s+)?(?:made|make)\s+a\s+note\b"
    r"|^(?:(?:okay|ok|sure|alright|got it)[,!.]?\s+)?noted\s+that\b",
    re.IGNORECASE,
)
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


def _starts_noted(text: str) -> bool:
    """The rules make Gemma confirm a save with "Noted: ..." (a plain "Noted!" acknowledgement does not count)."""
    return _NOTED_START.match(text) is not None


def _claims_a_save(text: str) -> bool:
    """Whether her answer says a save was made: a "Noted: ..." start, or a sentence of hers that says she noted it
    (`_NOTED_SAID`, English first). A sentence about a reminder is the reminder guard's ("I've noted the reminder for
    17:00": code's "No reminder was set"). Practice reads only the start (`_starts_noted`): a tutor's "I noted that you
    wrote …" is about the user's words."""
    return _starts_noted(text) or any(
        _NOTED_SAID.search(sentence) and not mentions_reminder(sentence)
        for sentence in (part.strip() for part in _SENTENCES.split(text))
        if sentence and not sentence.endswith("?")
    )


def _practice_claims(words: str, users: Sequence[str]) -> list[str]:
    """The clauses of her practice words that claim a reminder or a call (the broad set), leaving out a roleplay
    sentence the user wrote that she repeats ("Mañana te llamo, mamá"): a clause that is the user's words, or whose
    every claiming piece (split again at dashes and colons) is. A claim of hers around the user's words ("And I will
    call you at 7") stays."""

    def theirs(clause: str) -> bool:
        return any(users_words(clause, line) for line in users)

    found: list[str] = []
    for clause in claiming_clauses(words, broad=True, saved=False):
        pieces = [piece for piece, _ in clauses(clause, fine=True) if claiming_clauses(piece, broad=True)]
        if not theirs(clause) and not (pieces and all(theirs(piece) for piece in pieces)):
            found.append(clause)
    return found


def _reminder_text(arguments: str) -> str:
    """The text of a set_reminder whose arguments never reached the tool, when there is one to read."""
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return ""
    return str(args.get("text") or "").strip() if isinstance(args, dict) else ""


def _same(first: str, second: str) -> bool:
    return " ".join(first.casefold().split()) == " ".join(second.casefold().split())


def _retried(attempt: ReminderAttempt, later: ReminderAttempt) -> bool:
    """A later try stored what this refused one meant: the same text (she corrected the time), or anything after a
    try whose arguments could not be read (no text to compare: she tried again)."""
    unreadable = attempt.reason == prompts.REMINDER_WHY_UNREADABLE and not attempt.text
    return not later.reason and (unreadable or _same(later.text, attempt.text))


def unset(attempts: Sequence[ReminderAttempt]) -> list[ReminderAttempt]:
    """The set_reminder tries of an answer that stored nothing, without repeats; a refused try that a later stored try
    replaced is left out (`_retried`)."""
    missed: list[ReminderAttempt] = []
    for number, attempt in enumerate(attempts):
        retried = any(_retried(attempt, later) for later in attempts[number + 1 :])
        if attempt.reason and not retried and attempt not in missed:
            missed.append(attempt)
    return missed


def _not_set_lines(attempts: Sequence[ReminderAttempt], *, spoken: bool = False) -> str:
    """Code's answer when no set_reminder of an answer stored anything: why, one line per reason (`spoken`: the
    reasons to say on a call)."""
    whys = (attempt.said if spoken else attempt.reason for attempt in attempts if attempt.reason)
    return "\n".join(prompts.REMINDER_NOT_SET.format(reason=why) for why in dict.fromkeys(whys)) or (
        prompts.REMINDER_GUARD_SPOKEN
    )


def _heard_outside(turn: Turn) -> bool:
    """Whether this answer, or her context for it, holds outside text: a tool call's words may come from it."""
    return (
        turn.untrusted
        or turn.read_mail_or_calendar
        or turn.used_web
        or turn.after_outside
        or bool(turn.outside_notes)
        or turn.message.text.startswith((prompts.AUDIO_MARK, "[photo]"))
    )


def _quoted(text: str, limit: int = 80) -> str:
    """A refused reminder's text for its note: in quotes, cut at `limit` characters; none: "one reminder"."""
    if not text:
        return prompts.REMINDER_NOT_SET_WHAT
    return f'"{text if len(text) <= limit else text[: limit - 1].rstrip() + "…"}"'


def _without_captions(text: str, turn: Turn) -> str:
    """A video's captions are never written out: her sentences that copy them go (video.without_copies), and code's
    line gives the user's own link to the video instead (counts in the log, never text)."""
    lines: list[str] = []
    for kept in turn.videos_read.values():
        text, copied = without_copies(text, kept.captions)
        if copied:
            lines.append(prompts.VIDEO_NOT_SENT.format(link=kept.link))
    if lines:
        log.warning("message %d: captions written out; those sentences left out", turn.message.id)
    return "\n".join(part for part in (text, *lines) if part)


@dataclass(frozen=True)
class _Shape:
    """What an empty answer's log line says about each try, metadata only (research, 17 Oct, section 4): never a
    word of the conversation or of her answer, only counts, codes and times."""

    path: str  # call, text, document or practice
    max_tokens: int
    tools: int  # the tools offered
    language: str
    after_tool: bool  # the request's last message was a tool result (the retry's nudge comes after it)

    def of(self, completion: Completion, attempt: int, took: float) -> str:
        content = completion.content
        return (
            f"path {self.path}, attempt {attempt}, finish {completion.finish_reason or '-'}, "
            f"completion_tokens {completion.completion_tokens}, prompt_tokens {completion.prompt_tokens}, "
            f"max_tokens {self.max_tokens}, tools offered {self.tools}, tool calls {len(completion.tool_calls)}, "
            f"content {len(content)} chars ({len(content.strip())} stripped), "
            f"reasoning {completion.reasoning_chars} chars, {round(took * 1000)} ms, "
            f"after a tool result {'yes' if self.after_tool else 'no'}, language {self.language or '-'}"
        )


def _filtered(content: str, message_id: int) -> tuple[str, bool]:
    """Her answer through the filters that leave out what she must not say (a leaked thinking channel, a notice
    she wrote as if code had, a stage direction such as "(pause)", a written end_call), and whether a written end_call
    ended it. An answer they leave
    empty is one warning: which filter emptied it, and the length after each (metadata only)."""
    thought = without_thinking(content)
    noticed = without_fake_notices(thought)
    staged = without_stage_directions(noticed)
    text, ended = without_written_end_call(staged)
    if content.strip() and not text:
        steps = (
            ("without_thinking", thought),
            ("without_fake_notices", noticed),
            ("without_stage_directions", staged),
            ("without_written_end_call", text),
        )
        log.warning(
            "message %d: the filters left nothing (%s emptied it; chars raw %d, %s)",
            message_id,
            next(name for name, left in steps if not left.strip()),
            len(content),
            ", ".join(f"{name} {len(left)}" for name, left in steps),
        )
    return text, ended


def _once(callback: Callable[[], None] | None) -> Callable[[], None]:
    """`callback` at most once, however often the result is called (none: nothing)."""
    left = [callback] if callback is not None else []

    def call() -> None:
        if left:
            left.pop()()

    return call


_MARKUP_SYMBOLS = frozenset("~`=>|+<^")  # markdown's symbols that are no punctuation (a rule's "-", "*", "_" are)


def blank(text: str) -> bool:
    """Whether an answer has no words for the user: nothing, spaces, invisible characters, or only punctuation and
    markdown's symbols (a rule, "---", "***", "___"; "...", "…", "**"; live 22 and 25 Oct: "---" after device_set). A
    letter, a digit or an emoji is an answer ("👍", "1.")."""
    return all(
        char.isspace()
        or char in _MARKUP_SYMBOLS
        or unicodedata.category(char)[0] == "P"
        or unicodedata.category(char) == "Cf"
        for char in text
    )


def _usable(completion: Completion, allow_tools: bool) -> bool:
    """An answer with words (not `blank`), or with a tool call that may run."""
    return bool(not blank(completion.content) or (completion.tool_calls and allow_tools))


def _has_images(messages: list[dict[str, Any]]) -> bool:
    return any(isinstance(message.get("content"), list) for message in messages)


def profile_notice(turn: Turn) -> str:
    def shown(text: str) -> str:  # a fact from a page learned now: no link but the one the user sent (redteam-02)
        return text if turn.learned_url is None else shown_link_fact(text, turn.learned_url)

    lines = [f"Saved #{fid}: {shown(text)} (/forget {fid} undoes it)" for fid, text in turn.saved]
    lines += [f"Updated #{fid}: {shown(text)}" for fid, text in turn.updated]
    lines += [f"Removed #{fid}: {text}" for fid, text in turn.removed]
    lines += [f"Moved to notes #{fid}: {text}" for fid, text in turn.moved]  # making room: kept, not always sent
    lines += [f"Already kept #{fid}: {shown(text)}" for fid, text in turn.already if fid]  # a remember of a kept fact
    if any(has_house_number(area) for _, text in turn.saved + turn.updated if (area := home_area_text(text))):
        lines.append(prompts.HOME_AREA_ADVICE)  # kept and used all the same (owner: never refused)
    return "**Profile changes**\n" + "\n".join(lines) if lines else ""  # bold in Signal (markup)


def _nothing_saved(text: str, turn: Turn) -> str:
    """Code's note when her answer claims a save and none was made; no try-again advice after a remember that ran and
    saved nothing (it would fail the same way). A fact kept already is no unmade save."""
    if not _claims_a_save(text) or turn.already:
        return ""
    return NOTHING_SAVED_REFUSED if turn.remember_refused else NOTHING_SAVED


def _done_notes(turn: Turn) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """What an answer that failed had already done: her profile changes and the reminders set, as the notes an answer
    sends; the second: those that quote outside text (the profile notice after outside text, as `Reply.read_outside`)."""
    notice, reminders = profile_notice(turn), "\n".join(turn.reminder_notes)
    read_outside = turn.untrusted or turn.read_mail_or_calendar or turn.used_web or bool(turn.outside_notes)
    return tuple(note for note in (notice, reminders) if note), (notice,) if notice and read_outside else ()


async def _told(
    step: Awaitable[str], turn: Turn, on_cancel: Callable[[tuple[str, ...], tuple[str, ...]], None] | None
) -> str:
    """A tool call of an answer; cancelled, `on_cancel` is told what the answer had already done (_done_notes)."""
    try:
        return await step
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel(*_done_notes(turn))
        raise


def _assistant_tool_message(completion: Completion) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": completion.content,
        "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
            for call in completion.tool_calls
        ],
    }


class Interrupted(Exception):
    """A call started, or the account switched Deep Thinking off (/thinking off), while a Deep Thinking answer waited for
    the model, and no tool of that answer had run: its request was stopped. Nothing was done, so the message is answered
    again (App.process): after the call, or at once and without thinking."""


async def _unless_interrupted[T](work: Coroutine[Any, Any, T], interrupt: asyncio.Event | None) -> T:
    """`work`, unless `interrupt` is set before it is done: then `work` is cancelled (a request to Ollama is aborted, its
    socket shut, so Ollama stops generating and frees its one slot) and `Interrupted` is raised. A result that is
    there stands; none given: `work` as it is."""
    if interrupt is None:
        return await work
    if interrupt.is_set():
        work.close()  # never started: no request is sent
        raise Interrupted
    task = asyncio.ensure_future(work)
    watch = asyncio.ensure_future(interrupt.wait())
    try:
        await asyncio.wait({task, watch}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            return task.result()
        task.cancel()
        await asyncio.wait({task})
        raise Interrupted
    finally:
        watch.cancel()
        if not task.done():  # cancelled itself (a call turn's limit, the lock, the end): the request goes with it
            task.cancel()


class Brain:
    def __init__(
        self,
        gemma: GemmaClient,
        context: ContextBuilder,
        tools: Tools,
        *,
        max_tool_calls: int = 5,
        learn_tool_calls: int = 10,
        reply_max_tokens: int = 600,
        language: Callable[[], str] = lambda: "",
        clock: Callable[[], float] = time.monotonic,
        stopped: Callable[[], bool] = lambda: False,
    ) -> None:
        self._gemma = gemma
        self._stopped = stopped  # /lock: what an answer has still to do stops (LockedError; review I1)
        self._language = language  # her language's code now, for the empty answers' log
        self._clock = clock
        self._context = context
        self._tools = tools
        self._max_tool_calls = max_tool_calls
        self._learn_tool_calls = learn_tool_calls  # one remember per fact after learn_link
        self._reply_max_tokens = reply_max_tokens
        context.reserve(tools.schemas, reply_max_tokens)  # each request's tools and reply, in the model's context

    def now(self) -> float:
        """This brain's clock: a call turn's start, for `answer(started=…)`."""
        return self._clock()

    async def answer(
        self,
        conversation: Conversation,
        message: Message,
        images: Sequence[str] = (),
        *,
        recent: Mapping[int, Sequence[str]] | None = None,
        photo_left: float = 0.0,
        max_tokens: int | None = None,
        on_tools: Callable[[], None] | None = None,
        practice: PracticeTurn | None = None,
        glance: str = "",
        reads: Sequence[tuple[str, str]] = (),
        on_retry: Callable[[], None] | None = None,
        chords_before: Sequence[chord_checks.Result] = (),
        on_cancel: Callable[[tuple[str, ...], tuple[str, ...]], None] | None = None,
        started: float | None = None,
        turn_limit: float | None = None,
        video_yes: tuple[str, str] | None = None,
        thinking: bool = False,
        interrupt: asyncio.Event | None = None,
        stoppable: bool = True,
    ) -> Reply:
        """Gemma's reply to the newest message of the conversation (already stored), with its photos.

        `recent`: photos of an earlier message that are still shown (a follow-up question about them); `photo_left`:
        the seconds a photo shown with this message stays shown (its own, or `recent`), so a hold for it says when it
        ends (0: unknown).
        On a call: `max_tokens` keeps spoken answers short; `on_tools` is called once, when the first tool that
        takes a moment runs (the call says "let me check"); `glance`: code's counts at the call's start (numbers and
        times only: no outside text, so nothing in the answer is refused for it); `reads`: tool calls (name, JSON
        arguments) code makes itself before she answers (a yes to her offer of the day's rundown): run through the same
        tools, logged and counted like hers and given to her as her own tool calls with their results, so what they
        read is outside text for this answer exactly as when she calls them. `practice`: a /babble turn
        (`_practise`). `on_retry`: called once in an answer, when a try came back empty, before it is asked again (a
        call says an acknowledgement meanwhile). `chords_before`: on a call, its earlier chords results (sources for
        her chords). `on_cancel`: told, when the answer is cancelled (a call turn's time limit, the call's end), what it
        had already done, as GemmaError.notes and .outside (review of 0.9.7, C5: a reminder set got no note). An answer
        that promises a lookup with no answer after it (claims.only_promises_a_lookup) while no tool ran, web search
        is offered is asked once more, code's line in her newest message (the lookup guard); a retry that still
        promises with no tool call keeps her words and gets code's true line (`Reply.lookup`), as does a promise with a
        photo in the request, or on a call past a third of the turn's time limit (`turn_limit`; none
        given: LOOKUP_RETRY_WITHIN_S) from the turn's start (`started`, on this brain's clock, `now`: a call turn's
        waits before its answer count; review of 0.9.7, C12), with no retry; a retry that answers without a search gets none. Her written end_call
        never stands beside code's line: the caller may ask again. `video_yes`: (link, request) when this message is the
        user's typed YES to the video code found for their request (video_find): that one link is unlocked for
        read_link (`Turn.unlocked`, tappable in her answer) and code's line with it and the request is last in her prompt.
        `thinking`: Deep Thinking (thinking.py): each request of this answer asks for high reasoning effort and a cap of
        THINKING_TOKENS more, for the thought (less after a tool result, when its context leaves less: `_thought_room`);
        one cut off at its limit (no answer, or one cut off mid-sentence), or empty, is asked once more without thinking,
        at the usual cap. Never on a call (also when asked) or in practice. `interrupt`: set when a
        call starts, which waits for this answer to end (`App._worker_idle`), or when the account this answer is for
        switches Deep Thinking off: a thinking answer whose requests are all before its first tool call stops
        (`Interrupted`; its request to Ollama is cancelled), so the app answers the message again (after the call, or at
        once without thinking). Once a tool ran it is never stopped, as tools never run twice, and nor is an
        answer that is not `stoppable` (a found video's typed yes: asked again, it would lack the link its offer
        unlocked); their thought is dropped instead (review of 4 Nov, calls-1): the request on its way is cancelled
        (a model request never runs a tool) and sent again without thinking, at the usual cap, and so is every later
        one, so the call waits for seconds, not for a thought of up to 143 s a request."""
        started = self._clock() if started is None else started
        limit_tokens = max_tokens or self._reply_max_tokens
        shown = {**(recent or {}), **({message.id: images} if images else {})}
        retrying = _once(on_retry)
        spent: list[Completion] = []  # every request of this answer, for its timing
        if practice is not None:
            return await self._practise(conversation, message, practice, limit_tokens, shown, retrying, spent)
        # the stored conversation: a thinking answer's window may leave out a turn that read outside text, or a note
        # quoting it (`enforce_limit(extra_reply=…)`), which is still in the conversation she answers and in the next
        # answer's window, and her own replies in it carry no mark: the guards read the whole (review of 4 Nov,
        # guards-thinking-private-notes: a private note's words, repeated in her reply, reached Maps)
        whole = self._context.stored(conversation)
        held = self._context.quoted_notes(whole, message.id)
        quoted = held if conversation == whole else self._context.quoted_notes(conversation, message.id)  # her input's
        turn = Turn(
            message=message,
            # an earlier photo shown again (a follow-up): in her input as a photo message is, outside text (review I6)
            untrusted=bool(recent),
            other_outside=bool(recent),
            photo=bool(recent),
            photo_left=photo_left,
            after_outside=self._context.after_outside(whole, message.id),
            outside_notes=tuple(note.text for note in held),
            private_notes=tuple(note.text for note in held if note.private),
        )
        if quoted:  # this answer stays outside text once the notes have left her input
            self._context.mark_outside_notes(message.id)
        self._tools.replayed_reminders(turn)  # set before a restart cut this answer off: told now
        # code's video lines, last in the prompt: a kept video's (reading it again), a YouTube link in this message's
        # (what read_link reads); none on a call, so never with a call's glance
        find_line = prompts.VIDEO_FIND_LINE.format(link=video_yes[0], request=video_yes[1]) if video_yes else ""
        glance = "\n\n".join(part for part in (glance, self._tools.video_hint(message), find_line) if part)
        if video_yes:  # the user's own typed YES (app: video_find): this one link, code's own, read as if they sent it
            turn.unlocked = video_yes[0]
            turn.allowed_links.append(video_yes[0])
        # on a call her rules leave out the list of what she can do (spoken sentences, never a list)
        on_call = message.text.startswith(prompts.CALL_MARK)
        think = THINKING_TOKENS if thinking and not on_call else 0  # the tokens this answer's requests may add
        thought = bool(think)  # this answer thinks (its numbers are logged), though a call may drop the thought
        messages = self._input(conversation, shown, glance, call=on_call)
        # the message answered: the last of the user's (the lookup retry puts code's line in it)
        newest = max((index for index, chat in enumerate(messages) if chat.get("role") == "user"), default=0)
        said = users_own(messages)  # the user's own words: never her notes, never an audio file's (review G1)
        # the user asked for a reminder, a wake-up or a call now or in their message before ("and at 17:00?")
        asked_before = any(mentions_reminder(text) for text in said[-2:]) or mentions_reminder(message.own_text)
        turn.asked_reminder = asked_before  # after outside text, a reminder needs it (tools)
        # the user's own words before the message answered: a project named in their last may be read
        turn.said = tuple(users_own(messages[:newest])[-1:])
        calls = 0
        if reads:  # code's own reads, as if she had called them first
            if on_tools is not None:
                on_tools()
                on_tools = None
            read = tuple(ToolCall(f"code_{number}_{name}", name, args) for number, (name, args) in enumerate(reads))
            messages.append(_assistant_tool_message(Completion(content="", tool_calls=read)))
            for call in read:
                calls += 1
                self._halt()
                result = await _told(self._tools.call(turn, call.name, call.arguments), turn, on_cancel)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        # a photo of this message or one still shown (a follow-up): no lookup retry (guards-3), code's line instead
        with_photo = bool(shown) or message.text.startswith(PHOTO_PREFIX)
        corrected = False
        chords_nudged = False
        lookup_checked = lookup_retried = False  # the lookup guard: read once an answer; the retry it asked
        while True:
            limit = self._learn_tool_calls if turn.learned_document_id is not None else self._max_tool_calls
            allow_tools = calls < limit
            # a section given in full after reading its pages (owner, 16 Oct: B5 was cut mid-sentence at 600); a
            # call keeps its own spoken cap
            room = DOCUMENT_REPLY_TOKENS if turn.read_documents and max_tokens is None else limit_tokens
            with_document = turn.read_documents or message.text.startswith(prompts.DOCUMENT_MARK)
            path = "call" if on_call else "document" if with_document else "text"
            cap = max(limit_tokens, room)
            if think and interrupt is not None and interrupt.is_set():  # a call or /thinking off (before this request)
                if not calls and stoppable:
                    raise Interrupted  # nothing of this answer has run: no request is sent
                think = 0  # a tool has run, or the answer must finish: it finishes without the thought
                log.info(
                    "message %d: told to stop thinking (a call, or /thinking off); the rest of this answer does not think",
                    message.id,
                )
            # the thought gets only the room this answer's messages leave (never the tool results': they are cut for a
            # plain answer only); the request within the model's context, her answer's tokens with it: this answer's
            # newest tool result is cut at its end when it would not fit (review of 0.9.8, guards-2)
            thought_now = self._thought_room(messages, cap, think, message.id)
            messages, cut = self._context.fit_turn(messages, cap + thought_now, newest + 1)
            if cut:
                log.warning(
                    "message %d: over the model's context; %d characters of this answer's tool results cut",
                    message.id,
                    cut,
                )
            try:
                completion = await _unless_interrupted(
                    self._complete(
                        messages,
                        allow_tools,
                        cap,
                        retrying,
                        path=path,
                        message_id=message.id,
                        spent=spent,
                        thinking=thought_now,
                    ),
                    interrupt if thought_now else None,  # only a request that thinks is raced against a call
                )
            except Interrupted:
                if not calls and stoppable:
                    raise  # no tool has run: the app answers the message again (after the call, or without thinking)
                think = 0  # the request on its way is cancelled: the same one again, without the thought
                log.info("message %d: told to stop thinking (a call, or /thinking off); asked again plain", message.id)
                continue
            except GemmaError as exc:  # what this answer already did is still told, in code's words (review I25)
                exc.notes, exc.outside = _done_notes(turn)
                raise
            except asyncio.CancelledError:  # and when it is cut off (review of 0.9.7, C5)
                if on_cancel is not None:
                    on_cancel(*_done_notes(turn))
                raise
            if completion.tool_calls and allow_tools:
                if on_tools is not None and any(call.name not in INSTANT_TOOLS for call in completion.tool_calls):
                    on_tools()
                    on_tools = None
                messages.append(_assistant_tool_message(completion))
                for call in completion.tool_calls:
                    asked = call.name == "set_reminder"
                    tried = len(turn.reminder_attempts)
                    turn.reminder_tried |= asked  # before the tool runs: unreadable arguments and the limit count too
                    if calls >= limit:
                        result = "Error: tool limit reached for this message; answer with what you have."
                        why = prompts.REMINDER_WHY_LIMIT
                    else:
                        calls += 1
                        self._halt()  # /lock while Gemma worked: her tool calls after it never run
                        result = await _told(self._tools.call(turn, call.name, call.arguments), turn, on_cancel)
                        why = prompts.REMINDER_WHY_UNREADABLE
                    if asked and len(turn.reminder_attempts) == tried:  # it never reached the reminder checks
                        unchecked = _reminder_text(call.arguments)  # its words may be planted after outside text
                        turn.reminder_attempts.append(ReminderAttempt(unchecked, why, outside=_heard_outside(turn)))
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                continue
            text, wrote_end_call = _filtered(completion.content, message.id)
            reasoned = is_reasoning(text)
            if reasoned:  # her own reasoning is no answer: code's device question while one waits, else a plain retry
                log.warning("message %d: her answer was her own reasoning (%d chars); left out", message.id, len(text))
                text, wrote_end_call = "", False
            # an answer sent back (the save nudge, the chords nudge) is set aside with its written end_call; an
            # end_call tool she called before it stands (review of 0.9.8, guards-4)
            ended_before = turn.end_call
            if wrote_end_call and self._tools.calls_on and message.text.startswith(prompts.CALL_MARK):
                # after outside text only on the caller's own leave-taking, as the tool (redteam-07)
                turn.end_call = turn.end_call or self._tools.may_end_call(turn)
            if not lookup_checked and not calls and allow_tools and self._tools.web_on and only_promises_a_lookup(text):
                # "let me check" in a turn with no tool call and no answer after it: nothing would come back. Once
                # more, the same request with code's line in her newest message (the prefix stays cached); a search
                # then runs as any, on a call with its checking line (on_tools). Her first answer is set aside, its
                # written end_call with it. Never a retry with a photo in the request: "let me take a look" is at the
                # photo, and a retry reads it again (review of 0.9.8, guards-3); her words and code's line instead
                # (final review of 0.9.8, guards-3-lookup-guard-weakened: the promise went out alone)
                lookup_checked = True
                if with_photo:
                    log.warning(
                        "message %d: a lookup promised without a tool call, a photo shown: no retry", message.id
                    )
                elif on_call and self._clock() - started >= _lookup_window(turn_limit):
                    log.warning("message %d: a lookup promised without a tool call, too late for a retry", message.id)
                else:
                    lookup_retried, turn.end_call = True, False
                    messages = [
                        *messages[:newest],
                        with_line(messages[newest], prompts.LOOKUP_RETRY),
                        *messages[newest + 1 :],
                    ]
                    log.warning("message %d: a lookup promised without a tool call; asking Gemma once more", message.id)
                    continue
            if not corrected and _claims_a_save(text) and not (turn.saved or turn.already) and allow_tools:
                # "Noted" (or "I've noted that …") without a remember call means nothing was saved: send it back once;
                # a remember of a fact kept already is no unmade save (review of 0.9.8, memory-1). After a remember
                # that saved nothing, the true reason and no "call remember now" (second deep scan, dialogue-refused-
                # remember-false-nudge: "no remember call was made" sent her to a remember refused again)
                corrected = True
                if turn.remember_refused:
                    nudge = prompts.NOTED_REFUSED_NUDGE
                else:
                    nudge = prompts.NOTED_NUDGE if _starts_noted(text) else prompts.NOTED_SAID_NUDGE
                messages.append({"role": "assistant", "content": completion.content})
                messages.append({"role": "user", "content": nudge})
                turn.end_call = ended_before
                log.warning("message %d: a save claimed without one; asking Gemma to correct it", message.id)
                continue
            if self._tools.chords_on and not chords_nudged and allow_tools and not on_call:
                # chords she worked out in her head: once, on texts, back to her (a call answers at once instead)
                found = self._chords_checked(text, turn, message, chords_before)
                if found.left_out and not any(result.ok for result in turn.chords):
                    chords_nudged = True
                    messages.append({"role": "assistant", "content": completion.content})
                    messages.append({"role": "user", "content": prompts.CHORDS_NUDGE})
                    turn.end_call = ended_before
                    log.warning(
                        "message %d: her chords not worked out by a chords call; asking Gemma once more", message.id
                    )
                    continue
            if turn.forgot:
                self._context.refresh_snapshot()
            if turn.used_web:  # her links go, and a bare name left is defanged; the sites the results name stay
                sites = [found.site for search in turn.web for found in search.results]
                text = defang_links(without_links(text), "\n".join([message.own_text, *turn.allowed_links, *sites]))
            elif (
                turn.untrusted
                or turn.read_mail_or_calendar
                or turn.after_outside
                or message.text.startswith((prompts.AUDIO_MARK, "[photo]"))
            ):  # a link planted in outside text never reaches the user tappable, unless they sent it themselves (a
                # sent document's name is the file's, never theirs: R1-F2)
                text = defang_links(text, "\n".join([message.own_text, *turn.allowed_links]))
            text = _without_captions(text, turn)
            chords_note = chords_spoken = ""
            if self._tools.chords_on:
                text, chords_note, chords_spoken = self._chords_note(text, turn, message, chords_before)
            blank_chars = len(text) if text and blank(text) else 0  # only a rule or stray markup: no words of hers
            if blank_chars:
                text = ""
            # on a call, the device change this answer asked waits for the caller's yes: the call stays open for it (and
            # ends after it on her end_call or the caller's goodbye), so her goodbye never stands before code's question
            # (calls-099-01; the gate run of 4 Nov: all ten answers ended "Goodbye!"); before code's reminder line,
            # which comes after her words
            if (shorter := self._tools.without_goodbye(turn, text)) != text:
                text = shorter
                log.info("message %d: her closing goodbye left out; a device question waits for the yes", message.id)
            reminder = list(turn.reminder_notes)
            spoken = list(turn.reminder_spoken)
            # per answer, never across turns: a set_reminder that stored nothing gets code's line whatever she wrote;
            # so does a claim when none was stored: asked for one (this message or the one before, or she listed or
            # tried one): the strict, broad reading in all 11 languages; not asked: the precise one, and none at all
            # when she summarised outside text (a mail's payment reminder is not hers); a restated listed one is fine
            saved = bool(turn.saved or turn.updated or turn.already)
            asked_for_one = asked_before or turn.reminders_listed or turn.reminder_tried
            if asked_for_one:  # asked: the strict reading
                clauses = claiming_clauses(text, broad=True, saved=saved)
            elif turn.untrusted or turn.read_mail_or_calendar or turn.used_web:  # a summary of outside text: others'
                clauses = []
            else:  # not asked, nothing outside: an unasked promise ("Noted, and I'll remind you at 8:30")
                clauses = claiming_clauses(text, saved=saved)
            claimed = any(not restates(clause, turn.listed_reminders) for clause in clauses)
            not_set = not_set_spoken = ""
            if self._tools.reminders_on and not turn.reminder_set and turn.reminder_tried:
                # in any language: her sentences about a reminder, waking or a call go (a translated "it's set" too),
                # her others stay, and code's line why follows them (only that line when nothing is left)
                not_set = _not_set_lines(turn.reminder_attempts)
                not_set_spoken = " ".join(_not_set_lines(turn.reminder_attempts, spoken=True).splitlines())
                text = "\n".join(part for part in (without_reminder_talk(text, saved=saved), not_set) if part)
                log.warning("message %d: no reminder was set (refused); her reminder talk left out", message.id)
            elif self._tools.reminders_on and not turn.reminder_set and claimed:
                # beside a listing, or when her claim or the message names an open one (asked again: "already set",
                # review I26): one more that was never set
                open_ones = self._tools.open_reminders(turn)
                new = turn.reminders_listed or any(restates(part, open_ones) for part in (*clauses, message.text))
                reminder.append(prompts.REMINDER_GUARD_NEW if new else prompts.REMINDER_GUARD)
                spoken.append(prompts.REMINDER_GUARD_NEW if new else prompts.REMINDER_GUARD_SPOKEN)
                log.warning("message %d: no reminder was set (claimed)", message.id)
            elif self._tools.reminders_on and turn.reminder_set:  # one set, another refused: that one gets a note
                yours = "\n".join([message.own_text, *turn.allowed_links])  # its links as her answer's (review I12)
                for missed in unset(turn.reminder_attempts):
                    # one refused for outside text: none of its words (review G3: the note is stored plain)
                    what = (
                        prompts.REMINDER_NOT_SET_WHAT if missed.outside else _quoted(defang_links(missed.text, yours))
                    )
                    reminder.append(prompts.REMINDER_NOT_SET_ONE.format(what=what, reason=missed.reason))
                    spoken.append(prompts.REMINDER_NOT_SET_ONE_SPOKEN.format(reason=missed.said))
                    log.warning("message %d: one of its reminders was not set", message.id)
            # after outside text (this answer's, her context's, a note's, an audio file's or a photo's), a light or plug
            # change she tells as set for later (English first): she schedules none, so it is the outside text's (the
            # gate of 26 Oct: a planted calendar title told as the lamp "scheduled to be switched off"); her sentence
            # goes and code's line follows, alone when nothing of hers is left
            device = ""
            outside = (
                turn.untrusted
                or turn.read_mail_or_calendar
                or turn.used_web
                or turn.after_outside
                or bool(turn.outside_notes)
                or message.text.startswith((prompts.AUDIO_MARK, "[photo]"))
            )
            unscheduled = self._tools.unscheduled(turn, text) if outside else None
            if unscheduled is not None:  # code's line after her other words, or alone as the answer
                device = prompts.DEVICE_NOT_SCHEDULED if unscheduled else ""
                text = unscheduled or prompts.DEVICE_NOT_SCHEDULED
                log.warning(
                    "message %d: a device change told as set for later after outside text; code's line", message.id
                )
            # a device change she claims that no device_set made (English first): code's line after her words; "else"
            # when this answer did switch one (its Devices note says which)
            if self._tools.unmade_switch(turn, text):
                unmade = prompts.DEVICE_GUARD_ELSE if turn.switched else prompts.DEVICE_GUARD
                device = " ".join(line for line in (unmade, device) if line)
                log.warning("message %d: a device change claimed, none made", message.id)
            # nothing of hers left: code's own line for a device of this answer (its question while it waits for the
            # yes, else its result line: Tools.device_line), code's note holds a chords result that worked (review I5),
            # else she got confused
            device_line = "" if text else self._tools.device_line(turn)
            if blank_chars or device_line:
                instead = "code's device line" if device_line else "code's line"
                log.warning(
                    "message %d: no words in her answer (%d chars); %s instead", message.id, blank_chars, instead
                )
            text = (
                text
                or device_line
                or (prompts.CHORDS_DONE if any(result.ok for result in turn.chords) else prompts.CONFUSED)
            )
            if reasoned and text == prompts.CONFUSED:
                turn.end_call = False  # asked to say it again: a call stays open for the caller's words
            # append only, her words stay: after a promise with no retry (late on a call), or a retry that still
            # promises; a retry that answered without a search left nothing undone (review G6)
            undone = lookup_checked and not calls and (not lookup_retried or only_promises_a_lookup(text))
            # with a photo, asking again reads the photo again: a line that promises no search (second deep scan,
            # dialogue-photo-promise-line-says-search)
            not_done = prompts.LOOKUP_PHOTO_NOT_DONE if with_photo else prompts.LOOKUP_NOT_DONE
            lookup = not_done if undone else ""
            if lookup and lookup_retried:
                log.warning("message %d: no tool call after the lookup retry; code's line follows", message.id)
            if lookup:  # "Ask me again and I'll search." never ends the call (review of 0.9.7, C12)
                turn.end_call = False
            if thought:  # numbers only: the thought itself is never kept, logged or shown
                log.info(
                    "message %d: deep thinking: %s characters of thought (not kept), %s tokens, %.1f s",
                    message.id,
                    f"{sum(c.reasoning_chars for c in spent):,}",
                    f"{sum(c.completion_tokens for c in spent):,}",
                    sum(c.total_ms for c in spent) / 1000,
                )
            return Reply(
                text=text,
                tool_calls=calls,
                prompt_tokens=completion.prompt_tokens,
                notice=profile_notice(turn) or _nothing_saved(text, turn),
                end_call=turn.end_call,
                sources=prompts.sources_note(_links(turn), instead=turn.maps_instead),
                reminder="\n".join(reminder),
                reminder_spoken=" ".join(spoken),
                not_set=not_set,
                not_set_spoken=not_set_spoken,
                device=device,
                device_spoken=device,
                chords=chords_note,
                chords_spoken=chords_spoken,
                chords_results=tuple(turn.chords),
                read_outside=turn.untrusted or turn.read_mail_or_calendar or turn.used_web or bool(turn.outside_notes),
                lookup=lookup,
                timing=Timing.of(spent),
            )

    def _thought_room(self, messages: list[dict[str, Any]], cap: int, think: int, message_id: int) -> int:
        """The tokens the next request of a thinking answer may add for its thought: `think`, or only what the model's
        context leaves after these messages and the plain cap (review of 4 Nov, correctness-2: `enforce_limit` keeps the
        thought's room free of the history, but this answer's tool results came after, and cutting them for it lost a
        whole page a plain answer keeps), none below `THINKING_FLOOR` (that request does not think). With no context,
        `think`. One line when it is less, numbers only."""
        if not think:
            return 0
        left = self._context.room_left(messages, cap)
        if left is None or left >= think:
            return think
        room = left if left >= THINKING_FLOOR else 0
        log.info(
            "message %d: deep thinking: room for %s of %s thought tokens after this answer's tool results",
            message_id,
            f"{max(room, 0):,}",
            f"{think:,}",
        )
        return room

    def _chords_checked(
        self, text: str, turn: Turn, message: Message, before: Sequence[chord_checks.Result]
    ) -> chord_checks.Checked:
        """Her answer's chords against code's results, or the sources' (chords.check)."""
        sources = [
            message.text,
            *self._context.turn_before(message),
            *(f"{found.title}\n{found.text}" for search in turn.web for found in search.results),
        ]
        return chord_checks.check(text, results=turn.chords, sources=sources, language=self._language(), earlier=before)

    def _chords_note(
        self, text: str, turn: Turn, message: Message, before: Sequence[chord_checks.Result]
    ) -> tuple[str, str, str]:
        """[chords]: her sentences with another chord left out; code's note and what a call says (design 3.7)."""
        found = self._chords_checked(text, turn, message, before)
        left_out = found.left_out > 0 and not any(result.ok for result in turn.chords)
        if found.left_out:
            log.warning("message %d: %d sentences with chords not worked out left out", message.id, found.left_out)
            text = found.text
        return (
            text,
            chord_checks.note(turn.chords, left_out=left_out),
            chord_checks.spoken(turn.chords, left_out=left_out),
        )

    async def _practise(
        self,
        conversation: Conversation,
        message: Message,
        practice: PracticeTurn,
        max_tokens: int,
        shown: Mapping[int, Sequence[str]],
        on_retry: Callable[[], None],
        spent: list[Completion],
    ) -> Reply:
        """A practice turn: the practice rules, no tools offered (none can run), no tool loop. A reply that is empty,
        only "---" or only a "→" line is asked again once, then replaced by the language's fallback question (the
        broken reply is never kept); "---" lines go and one "→" line at most comes first (none when spoken, none that
        changes nothing the user wrote). A written end_call on a call still ends it. Nothing can be saved or set here:
        a "Noted: ..." gets code's note, and so does a claimed reminder when the user asked for one (this message or
        the one before; a roleplay line such as a shop's "I'll call you tomorrow" is not a promise). The claim is read
        in her words only: never the "→" line (the user's own sentence, corrected) nor a claim-shaped sentence the user
        wrote that she repeats ("If I have time, I will call you": the practice evals' en-17). The injection-residue
        guards: no set_reminder can run, so the overlap rule has nothing to check; links are defanged as in a chat
        answer, after outside text in the practice conversation (an audio file or a photo) unless the user sent them."""
        # a practice call's turn gets the call's style too (its rules have no list of what she can do to leave out)
        on_call = message.text.startswith(prompts.CALL_MARK)
        messages = self._practice_input(conversation, shown, practice, call=on_call)
        shape = _Shape("practice", max_tokens, 0, practice.language or self._language(), after_tool=False)
        completion, took = await self._timed(messages, max_tokens, None, spent)
        if not _has_images(messages):  # image tokens are counted by Ollama but not in our characters
            self._context.calibrate(messages, completion.prompt_tokens)
        if broken(completion.content):
            log.warning(
                "message %d: a broken practice reply; asking once more (%s)",
                message.id,
                shape.of(completion, 1, took),
            )
            on_retry()
            nudged = [*messages, {"role": "user", "content": prompts.EMPTY_NUDGE}]
            completion, took = await self._timed(nudged, max_tokens, None, spent)
            if broken(completion.content):
                log.warning(
                    "message %d: the practice reply stayed broken; the fallback question instead (%s)",
                    message.id,
                    shape.of(completion, 2, took),
                )
            else:
                log.info("message %d: the practice retry answered (%s)", message.id, shape.of(completion, 2, took))
        if broken(completion.content):
            text = practice.fallback
        else:
            text = tidy(
                without_stage_directions(without_fake_notices(without_thinking(completion.content))),
                spoken=practice.spoken,
                said=message.text,
            )
        text, wrote_end_call = without_written_end_call(text)
        end_call = wrote_end_call and self._tools.calls_on and message.text.startswith(prompts.CALL_MARK)
        text = text or practice.fallback
        # the chat's link rule over the practice conversation (no tool runs here, so its outside text is an audio file
        # or a photo, in it or answered now); the chat's outside text is not in this context
        if self._context.after_outside(conversation, message.id) or message.text.startswith(
            (prompts.AUDIO_MARK, "[photo]")
        ):
            text = defang_links(text, message.text)
        said = [
            without_notes(str(m["content"]))
            for m in messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        users = [*said[-2:], message.text]
        asked = any(mentions_reminder(line) for line in users)
        words = tidy(text, spoken=True)  # her words, without the "→" line (the user's own sentence, corrected)
        claimed = _practice_claims(words, users)
        reminder = spoken = ""
        if self._tools.reminders_on and asked and claimed:
            reminder, spoken = prompts.PRACTICE_NO_REMINDER, prompts.PRACTICE_NO_REMINDER_SPOKEN
            log.warning("message %d: no reminder was set (claimed in practice)", message.id)
        claimed_save = _starts_noted(words)
        if claimed_save:
            log.warning("message %d: 'Noted' in practice, where nothing is saved", message.id)
        return Reply(
            text=text,
            tool_calls=0,
            prompt_tokens=completion.prompt_tokens,
            notice=prompts.PRACTICE_NOTHING_SAVED if claimed_save else "",
            end_call=end_call,
            reminder=reminder,
            reminder_spoken=spoken,
            timing=Timing.of(spent),
        )

    async def warm_up(
        self, conversation: Conversation, *, glance: str = "", practice: PracticeTurn | None = None
    ) -> Completion:
        """A call's warm-up, before the caller's words: the request the call's first turn will send, without its new
        message, asking for WARM_UP_TOKENS, so Ollama loads the model and keeps the prompt's prefix for that turn.
        Built as `answer` and `_practise` build a call turn's (`_input`, `_practice_input`: a call's rules and style,
        no photos, and no video lines, which a call never has), with the same tools (the first try's, `_offered`;
        none for a practice turn) and options. `glance`: the call's, as the first turn will have it. No message is
        stored and no calibration is learned."""
        if practice is not None:
            messages = self._practice_input(conversation, {}, practice, call=True, newest=False)
            return await self._gemma.chat(messages, max_tokens=WARM_UP_TOKENS, tools=None)
        messages = self._input(conversation, {}, glance, call=True, newest=False)
        tools = self._offered(self._max_tool_calls > 0)  # the first try: no tool has run yet
        return await self._gemma.chat(messages, max_tokens=WARM_UP_TOKENS, tools=tools)

    def _input(
        self,
        conversation: Conversation,
        shown: Mapping[int, Sequence[str]],
        glance: str,
        *,
        call: bool,
        newest: bool = True,
    ) -> list[dict[str, Any]]:
        """An answer's input (`answer`; `newest=False`: a call's warm-up, before the new message is stored)."""
        return self._context.build(conversation, shown or None, glance=glance, call=call, newest=newest)

    def _practice_input(
        self,
        conversation: Conversation,
        shown: Mapping[int, Sequence[str]],
        practice: PracticeTurn,
        *,
        call: bool,
        newest: bool = True,
    ) -> list[dict[str, Any]]:
        """A practice answer's input (`_practise`): its rules, no notes (`newest=False`: a practice call's warm-up)."""
        return self._context.build(
            conversation, shown or None, rules=practice.rules, call=call, notes=False, newest=newest
        )

    def _offered(self, allow_tools: bool) -> list[dict[str, Any]] | None:
        """The tools a request offers: all of this setup's, or none once the tool limit is reached."""
        return self._tools.schemas if allow_tools else None

    def _halt(self) -> None:
        """No request to Gemma and no tool call once /lock came (`stopped`), also in an answer already running."""
        if self._stopped():
            raise LockedError("locked (/lock): the answer stops here")

    async def _timed(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        tools: list[dict[str, Any]] | None,
        spent: list[Completion],
        *,
        effort: str = "none",
    ) -> tuple[Completion, float]:
        """One Gemma call (`effort`: its reasoning effort, "none" unless Deep Thinking asks) and the seconds it took; the
        completion is added to the answer's `spent`."""
        self._halt()
        started = self._clock()
        completion = await self._gemma.chat(messages, max_tokens=max_tokens, tools=tools, reasoning_effort=effort)
        spent.append(completion)
        return completion, self._clock() - started

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        allow_tools: bool,
        max_tokens: int,
        on_retry: Callable[[], None],
        *,
        path: str,
        message_id: int,
        spent: list[Completion],
        thinking: int = 0,
    ) -> Completion:
        """One Gemma call; an answer with neither text nor a usable tool call is retried once (`on_retry` first), and
        the retry's result is checked: a second empty one is a warning of its own, and the caller gives the confused
        line. Each try of an empty answer is one log line, metadata only (`_Shape`; `path`: call, text or
        document). `thinking`: the tokens a Deep Thinking request may add for its thought (0: none, as every other
        request): one that was cut off at its limit and calls no tool (the thought ate the cap: no answer, or one cut
        off mid-sentence) is asked once more as the same request without thinking, at the usual cap; an empty one that
        was not cut is nudged, without thinking."""
        tools = self._offered(allow_tools)
        effort = THINKING_EFFORT if thinking else "none"
        completion, took = await self._timed(messages, max_tokens + thinking, tools, spent, effort=effort)
        if not _has_images(messages):  # image tokens are counted by Ollama but not in our characters
            self._context.calibrate(messages, completion.prompt_tokens)
        after_tool = bool(messages) and messages[-1].get("role") == "tool"
        shape = _Shape(path, max_tokens + thinking, len(tools or ()), self._language(), after_tool)
        if thinking and completion.finish_reason == "length" and not completion.tool_calls:
            # the cap counts the thought and the answer together: one cut off with no words left, or in the middle of its
            # sentence (review of 4 Nov, correctness-4: the cut-off words went out as they were), is asked once more
            # as the same request without the thought
            log.warning(
                "message %d: deep thinking left %s; asking once more without thinking (%s)",
                message_id,
                "no answer" if blank(completion.content) else "her answer cut off",
                shape.of(completion, 1, took),
            )
            return await self._complete(
                messages, allow_tools, max_tokens, on_retry, path=path, message_id=message_id, spent=spent
            )
        if _usable(completion, allow_tools):
            return completion
        log.warning(
            "message %d: empty answer from Gemma; retrying once (%s)", message_id, shape.of(completion, 1, took)
        )
        on_retry()
        nudged = [
            *messages,
            {"role": "user", "content": prompts.EMPTY_NUDGE},
        ]  # an identical request may come back empty again
        shape = replace(shape, max_tokens=max_tokens)  # the retry asks for the usual cap, without thinking
        retried, took = await self._timed(nudged, max_tokens, tools, spent)
        if _usable(retried, allow_tools):
            log.info(
                "message %d: the retry after an empty answer answered (%s)", message_id, shape.of(retried, 2, took)
            )
        else:
            log.warning(
                "message %d: the retry came back empty too; no words of hers in this answer (%s)",
                message_id,
                shape.of(retried, 2, took),
            )
        return retried

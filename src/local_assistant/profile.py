"""The user's profile: short lasting facts, checked before they are stored.

Patterns: Hermes Agent memory (hard cap that is never silently truncated; entries scanned for
injection and hidden characters before they reach the prompt) and Mem0 (add / replace / remove
against the existing facts). Spec section 6.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from local_assistant import prompts, signal_text
from local_assistant.language import ENGLISH, function_words
from local_assistant.store import Fact, SourceKind, Store, fold

log = logging.getLogger(__name__)

Action = Literal["add", "replace", "remove"]
NO_LEARN_KEY = "no_learn_message_ids"  # messages whose facts were forgotten: background learning skips them
# the last quiet time's changes to her notes (JSON: `_Kept`): the next reply's note and /profile new (0.9.8)
QUIET_KEY = "quiet_time_changes"
QUIET_NAMED = 3  # the quiet-time note names at most this many; /profile new lists them all
# the user's own facts ("remember that …", /learn): idle learning ("chat") may add next to them, never change them
_USERS_OWN: frozenset[SourceKind] = frozenset({"remember", "link"})

# Checked on NFKC-normalised text with whitespace collapsed (fullwidth letters, NBSP and double
# spaces cannot slip past). A few non-English forms of "ignore (previous) instructions" are included.
_INSTRUCTION = re.compile(
    r"ignore (?:all|any|the|previous|prior|above|your|earlier)\b|disregard (?:all|any|the|previous|prior|your)\b"
    r"|system prompt|you (?:must|should|are now|will now)\b|new instructions|\bobey\b|```"
    r"|<\|[^>]{0,40}>|<\s*/?\s*(?:start_of_turn|end_of_turn)\s*>"
    r"|αγνόησ|αγνοήστε|\bnegeer\b|\bignorier|\bignorez\b"
    r"|^\s*(?:system|assistant|user|tool)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
# Facts from a learned page must be about the user, not address the assistant.
_ADDRESSES_ASSISTANT = re.compile(r"\b(?:gemma|assistant|you|your|yourself)\b", re.IGNORECASE)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY|\b[A-Za-z0-9_]{32,}\b"
)
# IBAN-like account numbers: never profile material (and a favourite of planted instructions)
_ACCOUNT = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){8,30}\b")
_URL_SCHEME = re.compile(r"\b([a-z][a-z0-9+.-]*)://", re.IGNORECASE)
_BAD_SCHEME = re.compile(r"\b(?:javascript|data|file|vbscript):", re.IGNORECASE)
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Zl", "Zp"})
_INVISIBLE = frozenset({0x034F, 0x115F, 0x1160, 0x2800, 0x3164, 0xFFA0})


def _hidden(char: str) -> bool:
    code = ord(char)
    return (
        unicodedata.category(char) in _HIDDEN_CATEGORIES
        or code in _INVISIBLE
        or 0xFE00 <= code <= 0xFE0F
        or 0xE0100 <= code <= 0xE01EF
    )


@dataclass(frozen=True)
class Op:
    action: Action
    fact_id: int = 0
    text: str = ""
    reason: str = ""
    source_message_id: int | None = None


@dataclass
class ApplyResult:
    added: list[int] = field(default_factory=list)
    replaced: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    # the facts kept already that those duplicates repeat (number, kept words); none for one that repeats another new
    # fact of the same batch
    repeated: list[tuple[int, str]] = field(default_factory=list)
    rejected: list[tuple[Op, str]] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)  # the user's own facts that learning wanted to change
    moved: list[int] = field(default_factory=list)  # the user's facts moved into her notes to make room
    promoted: list[int] = field(default_factory=list)  # notes the user asked to remember: now theirs
    over_cap_by: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.replaced or self.removed or self.moved or self.promoted)


@dataclass(frozen=True)
class QuietTime:
    """The last quiet time's changes to her notes, as they are now (0.9.8, owner, 26 Oct: "only display what happened
    in the last quiet time"): `noted`, the notes added or updated that are still there, in the order written ("Added
    #41: …"); `removed`, the notes its passes took out ("Removed: …", no number); `told`: its note went out."""

    noted: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    told: bool = True


@dataclass(frozen=True)
class _Kept:
    """A quiet time as the state keeps it (QUIET_KEY): each note added or updated as ("add" or "replace", its number),
    the numbers of the notes removed (their words stay in the change log), and whether its note went out."""

    noted: tuple[tuple[str, int], ...] = ()
    removed: tuple[int, ...] = ()
    told: bool = True

    def dumps(self) -> str:
        return json.dumps(
            {"noted": [list(entry) for entry in self.noted], "removed": list(self.removed), "told": self.told}
        )


def _kept(raw: str | None) -> _Kept:
    """The kept quiet time; none (told) when it is missing or unreadable."""
    try:
        data = json.loads(raw or "")
    except ValueError:
        return _Kept()
    if not isinstance(data, dict):
        return _Kept()
    noted, removed, told = data.get("noted"), data.get("removed"), data.get("told")
    if not (isinstance(noted, list) and isinstance(removed, list) and isinstance(told, bool)):
        return _Kept()
    pairs = [
        (entry[0], entry[1])
        for entry in noted
        if isinstance(entry, list) and len(entry) == 2 and entry[0] in ("add", "replace") and type(entry[1]) is int
    ]
    if len(pairs) != len(noted) or any(type(number) is not int for number in removed):
        return _Kept()
    return _Kept(tuple(pairs), tuple(removed), told)


def quiet_note(quiet: QuietTime | None) -> str:
    """The note with her next reply after learning (0.9.8, approved 26 Oct): at most QUIET_NAMED of the notes added or
    updated, by number and whole, then how many more (/profile new lists them), then where they are kept; "" when none
    is left to name (a removal alone is no new thing: review O2)."""
    if quiet is None or not quiet.noted:
        return ""
    more = len(quiet.noted) - QUIET_NAMED
    lines = [*quiet.noted[:QUIET_NAMED], *([prompts.QUIET_MORE.format(count=more)] if more > 0 else [])]
    return prompts.fact_note(prompts.QUIET_HEADER, lines, prompts.QUIET_CLOSING)


def check_fact(text: str, max_chars: int = 300, *, strict: bool = False, allowed_url: str | None = None) -> str | None:
    """Why this fact may not be stored, or None when it is fine.

    strict (facts from a learned page): the fact must not address the assistant and may only
    contain the learned page's own link.
    """
    fact = text.strip()
    if not fact:
        return "empty"
    if len(fact) > max_chars:
        return f"too long ({len(fact)} > {max_chars} characters)"
    if any(_hidden(char) for char in fact):
        return "contains hidden or control characters"
    normal = " ".join(unicodedata.normalize("NFKC", fact).split())
    if _INSTRUCTION.search(normal):
        return "looks like an instruction, not a fact"
    if _SECRET.search(normal):
        return "looks like a password or key"
    if _ACCOUNT.search(normal):
        return "looks like a bank account number"
    for match in _URL_SCHEME.finditer(normal):
        if match.group(1).lower() not in ("http", "https"):
            return f"link type {match.group(1)} not allowed"
    if _BAD_SCHEME.search(normal):
        return "link type not allowed"
    if strict:
        if _ADDRESSES_ASSISTANT.search(normal):
            return "addresses the assistant, not a fact about the user"
        page = (allowed_url or "").rstrip("/")
        for url in _URL.findall(normal):
            if not page or url.rstrip("/.,;") != page:
                return "link not from the learned page"
        # any other run a phone may make tappable (www., a bare name on a real ending, as signal_text.unlinked reads
        # them), never only one with http(s):// (second review of 0.9.8, redteam-02)
        rest = _sent_link(allowed_url).sub(" ", normal)
        if signal_text.unlinked(rest) != rest:
            return "link not from the learned page"
    return None


_NEVER_SENT = re.compile(r"(?!)")


def _sent_link(allowed_url: str | None) -> re.Pattern[str]:
    """The link the user sent as a fact may write it, with or without its scheme and closing slash, as a whole run
    (never the start of a longer link: "…/alex-examplex")."""
    page = (allowed_url or "").strip().rstrip("/")
    if not page:
        return _NEVER_SENT
    bare = re.sub(r"^https?://", "", page, flags=re.IGNORECASE)
    forms = "|".join(re.escape(form) for form in sorted({page, bare}, key=len, reverse=True))
    return re.compile(rf"(?<![^\s(\[\"'])(?:https?://)?(?:{forms})/?(?=[.,;:!?)\]\"']*(?:\s|$))", re.IGNORECASE)


def shown_link_fact(text: str, allowed_url: str | None) -> str:
    """A fact from a learned page as a note of code's shows it: every link left out (`signal_text.unlinked`) but the
    link the user sent, which stays as it is."""
    pattern = _sent_link(allowed_url)
    pieces: list[str] = []
    kept = 0
    for match in pattern.finditer(text):
        pieces += [signal_text.unlinked(text[kept : match.start()]), match.group(0)]
        kept = match.end()
    return "".join([*pieces, signal_text.unlinked(text[kept:])])


def _norm(text: str) -> str:
    return " ".join(text.casefold().split()).rstrip(".!")


CATEGORIES = ("About", "Family", "Work", "Study", "Music", "Social", "Home", "Humour", "Other")
_CATEGORY = re.compile(r"^\s*([A-Za-z]+)\s*:\s*(.+)$", re.DOTALL)


def category_of(text: str) -> tuple[str, str] | None:
    """("Family", "The user's sister is Anna.") for "Family: The user's sister is Anna."; None without one."""
    match = _CATEGORY.match(text)
    if match is None:
        return None
    name = match.group(1).capitalize()
    return (name, match.group(2).strip()) if name in CATEGORIES else None


def by_category(facts: Sequence[Fact]) -> list[tuple[str, list[tuple[Fact, str]]]] | None:
    """The facts under their categories, in CATEGORIES order (a fact without one under Other), each with its text after
    the category; None when no fact has one. Gemma's snapshot and /profile group them alike."""
    if all(category_of(fact.text) is None for fact in facts):
        return None
    groups: dict[str, list[tuple[Fact, str]]] = {category: [] for category in CATEGORIES}
    for fact in facts:
        category, rest = category_of(fact.text) or ("Other", fact.text)
        groups[category].append((fact, rest))
    return [(category, entries) for category, entries in groups.items() if entries]


# English's function words, as `fold` writes them: never a word to match a note on. Only English's: the notes are written
# as "The user ..." sentences, and the other languages' lists hold English words that matter ("son", "ten", "die")
_SMALL_WORDS = frozenset(fold(word) for word in function_words(ENGLISH))
COMMON_FROM_NOTES = 4  # from this many notes on, a word in more than half of them is too common to match on
_MARKS = re.compile(
    r"^(?:\s*\[[a-z][a-z ]*\])+\s*"
)  # code's marks before a message's words: [call], [photo], [audio file]


def _is_yours(fact: Fact) -> bool:
    """In the user's part: theirs by origin ("remember", "link") and not moved into her notes to make room."""
    return fact.source_kind in _USERS_OWN and not fact.in_notes


class Profile:
    """Two parts (owner, 16 Oct): the user's own facts (`yours`: "remember", a link they asked her to learn, a fact
    they fixed), always in her prompt, within `max_chars`; and her notes from chats (`notes`), within
    `notes_max_chars`, of which `notes_for` gives the ones that may matter for a message, at most
    `notes_per_message` characters. Each part has its own limit; making room in the user's part moves a fact into
    her notes, never deletes it."""

    def __init__(
        self,
        store: Store,
        *,
        max_chars: int = 4_000,
        notes_max_chars: int = 12_000,
        notes_per_message: int = 1_500,
        fact_max_chars: int = 300,
    ) -> None:
        self._store = store
        self._max_chars = max_chars
        self._notes_max = notes_max_chars
        self._notes_per_message = notes_per_message
        self._fact_max = fact_max_chars

    @property
    def max_chars(self) -> int:
        """The user's part's limit."""
        return self._max_chars

    @property
    def notes_max_chars(self) -> int:
        return self._notes_max

    @property
    def fact_max_chars(self) -> int:
        """One fact's limit."""
        return self._fact_max

    @property
    def notes_per_message(self) -> int:
        """At most this many characters of her notes come with a message (`notes_for`)."""
        return self._notes_per_message

    def facts(self) -> list[Fact]:
        """Both parts, by number."""
        return self._store.facts()

    def yours(self) -> list[Fact]:
        return [fact for fact in self.facts() if _is_yours(fact)]

    def notes(self) -> list[Fact]:
        """Her notes: what she learned from chats, and the user's facts moved here to make room (origin kept)."""
        return [fact for fact in self.facts() if not _is_yours(fact)]

    def moved(self) -> list[Fact]:
        """The user's own facts among her notes: moved there to make room, still theirs by origin."""
        return [fact for fact in self.notes() if fact.source_kind in _USERS_OWN]

    def used_chars(self) -> int:
        """The user's part, as its limit counts it."""
        return sum(len(fact.text) for fact in self.yours())

    def notes_used_chars(self) -> int:
        return sum(len(fact.text) for fact in self.notes())

    def add(
        self,
        text: str,
        *,
        source_kind: SourceKind,
        source_message_id: int | None = None,
        source_document_id: int | None = None,
        reason: str = "",
        allowed_url: str | None = None,
    ) -> ApplyResult:
        op = Op("add", text=text, reason=reason, source_message_id=source_message_id)
        return self.apply([op], source_kind=source_kind, source_document_id=source_document_id, allowed_url=allowed_url)

    def apply(
        self,
        ops: Sequence[Op],
        *,
        source_kind: SourceKind,
        source_document_id: int | None = None,
        allowed_url: str | None = None,
        remove_to_notes: bool = False,
    ) -> ApplyResult:
        """Check every operation, then write them in one transaction if the part they write fits its limit (the
        user's part for "remember" and "link", her notes for "chat"; the other part may stay over its limit: an
        install from before keeps every fact).

        Over the limit nothing is written and over_cap_by says how much has to go: the caller must
        consolidate (merge or remove facts). Facts are never dropped silently. Learning from chat never
        replaces or removes the user's own facts: those operations are skipped and listed in kept. A chat
        batch that wanted to replace one of them may have merged other facts into it, so its removes are
        held back too (rejected): a removal can come again in a later pass, a lost fact cannot.
        `remove_to_notes` (making room for a "remember"): a remove of the user's fact moves it into her notes.
        """
        result = ApplyResult()
        facts = self.facts()
        original = {fact.id: fact.text for fact in facts}
        owned = {fact.id for fact in facts if fact.source_kind in _USERS_OWN}  # theirs by origin: learning keeps off
        mine = {fact.id for fact in facts if _is_yours(fact)}  # in their part now (not moved into her notes)
        users = source_kind in _USERS_OWN
        chat = not users
        merging = chat and any(op.action == "replace" and op.fact_id in owned for op in ops)
        final = dict(original)
        adds: list[Op] = []
        replaces: dict[int, Op] = {}
        removes: dict[int, Op] = {}
        moves: dict[int, Op] = {}
        promotes: dict[int, Op] = {}
        # a replace that repeats the text of a fact the same batch removes is no duplicate of it (a merge of #b into
        # #a, then a remove of #b, kept neither)
        removing = {op.fact_id for op in ops if op.action == "remove"}
        for op in ops:
            if chat and op.action != "add" and op.fact_id in owned:
                log.info("learning: %s of fact #%d skipped (kept: the user's own fact)", op.action, op.fact_id)
                result.kept.append(op.fact_id)
                continue
            if remove_to_notes and op.action != "add" and op.fact_id in final and op.fact_id not in mine:
                # making room shows Gemma only the user's part: one of her notes, never shown, is not hers to change
                result.rejected.append((op, "not in the user's part"))
                continue
            text = op.text.strip()
            if op.action == "remove":
                if op.fact_id not in final:
                    result.rejected.append((op, f"no fact #{op.fact_id}"))
                    continue
                if merging:
                    log.info("learning: remove of fact #%d held back (a merge into the user's own fact)", op.fact_id)
                    result.rejected.append((op, "held back: its batch merged into the user's own fact"))
                    continue
                if remove_to_notes and op.fact_id in mine:
                    moves[op.fact_id] = op
                    continue
                del final[op.fact_id]
                replaces.pop(op.fact_id, None)
                removes[op.fact_id] = op
                continue
            reason = check_fact(text, self._fact_max, strict=source_kind == "link", allowed_url=allowed_url)
            if reason:
                result.rejected.append((op, reason))
                continue
            others = {
                _norm(t)
                for fid, t in final.items()
                if fid not in removing and not (op.action == "replace" and fid == op.fact_id)
            }
            others.update(_norm(a.text) for a in adds)
            if _norm(text) in others:
                repeated = [
                    (fid, t)
                    for fid, t in final.items()
                    if _norm(t) == _norm(text)
                    and fid not in removing
                    and not (op.action == "replace" and fid == op.fact_id)
                ]
                note = next(
                    (fid for fid, t in final.items() if _norm(t) == _norm(text) and fid not in mine | set(promotes)),
                    None,
                )
                if source_kind == "remember" and op.action == "add" and note is not None:  # her note, now theirs
                    promotes[note] = op
                    continue
                if op.action == "replace" and op.fact_id in final and op.fact_id not in moves:
                    del final[op.fact_id]
                    replaces.pop(op.fact_id, None)
                    removes[op.fact_id] = dataclasses.replace(
                        op, action="remove", reason="merged into an existing fact"
                    )
                result.duplicates.append(text)
                result.repeated += repeated[:1]
                continue
            if op.action == "add":
                adds.append(dataclasses.replace(op, text=text))
            elif op.fact_id not in final:
                result.rejected.append((op, f"no fact #{op.fact_id}"))
            elif _norm(text) == _norm(original[op.fact_id]):  # its own words again: nothing changes, nothing is named
                final[op.fact_id] = original[op.fact_id]
                replaces.pop(op.fact_id, None)
            else:
                final[op.fact_id] = text
                replaces[op.fact_id] = dataclasses.replace(op, text=text)
        theirs = {fact_id for fact_id in final if fact_id in mine and fact_id not in moves} | set(promotes)
        added = sum(len(a.text) for a in adds)
        if users:
            before = sum(len(original[f]) for f in mine)
            total, limit = sum(len(final[f]) for f in theirs) + added, self._max_chars
        else:
            before = sum(len(t) for f, t in original.items() if f not in mine)
            total, limit = sum(len(t) for f, t in final.items() if f not in theirs) + added, self._notes_max
        # past its limit, a batch that adds nothing and does not grow the part may go through (a fix, a refused add);
        # one that adds must end within it
        if total > limit and (adds or promotes or total > before):
            result.over_cap_by = total - limit
            return result
        if remove_to_notes and not adds and not promotes:
            return result  # making room for a fact that is not saved moves nothing (why: in rejected or duplicates)
        with self._store.tx():
            for fact_id, op in moves.items():
                self._store.demote_fact(fact_id)
                self._store.log_change(
                    fact_id, "replace", original[fact_id], final[fact_id], op.source_message_id, prompts.MOVED_TO_NOTES
                )
                result.moved.append(fact_id)
            for fact_id, op in removes.items():
                self._store.delete_fact(fact_id)
                self._store.log_change(fact_id, "remove", original[fact_id], None, op.source_message_id, op.reason)
                result.removed.append(fact_id)
            for fact_id, op in replaces.items():
                self._store.update_fact(fact_id, op.text)
                self._store.log_change(fact_id, "replace", original[fact_id], op.text, op.source_message_id, op.reason)
                result.replaced.append(fact_id)
            for fact_id, op in promotes.items():
                self._store.promote_fact(fact_id, op.source_message_id)
                self._store.log_change(
                    fact_id, "replace", original[fact_id], original[fact_id], op.source_message_id, prompts.MADE_THEIRS
                )
                result.promoted.append(fact_id)
            for op in adds:
                fact_id = self._store.insert_fact(op.text, source_kind, op.source_message_id, source_document_id)
                self._store.log_change(fact_id, "add", None, op.text, op.source_message_id, op.reason)
                result.added.append(fact_id)
        return result

    def find(self, query: str) -> list[Fact]:
        """Facts by number ('12' or '#12') or containing all the given words."""
        query = query.strip().lstrip("#").strip()
        facts = self.facts()
        if query.isdigit():
            return [fact for fact in facts if fact.id == int(query)]
        words = query.casefold().split()
        if not words:
            return []
        return [fact for fact in facts if all(word in fact.text.casefold() for word in words)]

    def forget(self, fact_id: int, *, source_message_id: int | None = None, blank_log: bool = True) -> Fact | None:
        """Remove a fact. The user's own /forget also blanks its text in the change log, and the words of the notes the
        last quiet time removed leave /profile new (kept there only to undo a merge); a removal by Gemma
        (blank_log=False) keeps the text there, so an injected forget can be undone."""
        fact = next((f for f in self.facts() if f.id == fact_id), None)
        if fact is None:
            return None
        with self._store.tx():
            self._store.delete_fact(fact_id)
            if blank_log:
                self._store.blank_changes(fact_id)
                kept = _kept(self._store.get_state(QUIET_KEY))
                if kept.removed:
                    self._store.set_state(QUIET_KEY, dataclasses.replace(kept, removed=()).dumps())
                self._store.log_change(
                    fact_id, "remove", None, None, source_message_id, "forgotten at the user's request"
                )
            else:
                self._store.log_change(fact_id, "remove", fact.text, None, source_message_id, "removed by Gemma")
            if fact.source_message_id is not None:  # don't let background learning teach it back
                self._skip_learning(fact.source_message_id)
        return fact

    def fix(self, fact_id: int, text: str, *, source_message_id: int | None = None) -> str | None:
        """The user's /fix: the fact's text replaced in place by their own words, checked as a remembered fact
        is. The fact becomes theirs ("remember"), so idle learning cannot change it again. Why not, or None."""
        facts = self.facts()
        fact = next((f for f in facts if f.id == fact_id), None)
        if fact is None:
            return f"no fact #{fact_id}"
        text = text.strip()
        reason = check_fact(text, self._fact_max)
        if reason:
            return reason
        same = next((f.id for f in facts if f.id != fact_id and _norm(f.text) == _norm(text)), None)
        if same is not None:
            return f"fact #{same} already says that"
        used, theirs = self.used_chars(), _is_yours(fact)
        before = len(fact.text) if theirs else 0  # a note fixed becomes theirs, within their limit
        # a part over its limit (an install from before) may still have a fact corrected that does not grow it
        over = used - before + len(text) - (max(self._max_chars, used) if theirs else self._max_chars)
        if over > 0:
            return f"your part of the profile would be {over:,} characters over its limit"
        with self._store.tx():
            self._store.own_fact(fact_id, text, source_message_id)
            self._store.log_change(fact_id, "replace", fact.text, text, source_message_id, "fixed by the user (/fix)")
            if fact.source_message_id is not None:  # as with forget: its message must not teach the old words back
                self._skip_learning(fact.source_message_id)
        return None

    def record_quiet(self, result: ApplyResult) -> None:
        """A learning pass's changes to her notes, for the next reply's note and /profile new: they join the quiet time
        whose note has not gone out yet; once it has, a pass that adds or updates a note starts a new one, and a pass
        that only removes joins the told one (it sends no note of its own). A note added and removed before its note
        went out is in neither list; one its note named shows as removed."""
        if not (result.added or result.replaced or result.removed):
            return
        kept = _kept(self._store.get_state(QUIET_KEY))
        if kept.told and (result.added or result.replaced):
            kept = _Kept(told=False)
        gone, listed = set(result.removed), {number for _, number in kept.noted}
        unseen = set() if kept.told else {number for action, number in kept.noted if action == "add"}
        noted = [entry for entry in kept.noted if entry[1] not in gone]
        noted += [("replace", number) for number in result.replaced if number not in listed]
        noted += [("add", number) for number in result.added]
        removed = (*kept.removed, *(number for number in result.removed if number not in unseen))
        self._store.set_state(QUIET_KEY, _Kept(tuple(noted), removed, told=kept.told).dumps())

    def last_quiet(self, *, notes_only: bool = False) -> QuietTime:
        """The last quiet time's changes as they are now: a note fixed since shows its new words, one forgotten is left
        out; a removed note shows the words the change log kept of it. `notes_only`: only the ones still among her
        notes (a note the user made theirs since, by /fix or "remember", is left out)."""
        kept = _kept(self._store.get_state(QUIET_KEY))
        texts = {fact.id: fact.text for fact in (self.notes() if notes_only else self.facts())}
        lines = {"add": prompts.QUIET_ADDED, "replace": prompts.QUIET_UPDATED}
        noted = tuple(
            lines[action].format(number=number, text=texts[number]) for action, number in kept.noted if number in texts
        )
        removed = tuple(
            prompts.QUIET_REMOVED.format(text=words)
            for number in kept.removed
            if (words := self._removed_words(number))
        )
        return QuietTime(noted, removed, kept.told)

    def tell_quiet(self) -> QuietTime | None:
        """The quiet time whose note has not gone out yet, now told (with this reply); None when there is none. The reply
        ends that quiet time also when nothing is left to name."""
        kept = _kept(self._store.get_state(QUIET_KEY))
        if kept.told:
            return None
        quiet = self.last_quiet(notes_only=True)  # its closing says "in my notes, not in your facts"
        self._store.set_state(QUIET_KEY, dataclasses.replace(kept, told=True).dumps())
        return quiet

    def _removed_words(self, fact_id: int) -> str:
        """A removed note's words, as its removal kept them in the change log ("" when there are none)."""
        removal = next((change for change in reversed(self._store.changes(fact_id)) if change.action == "remove"), None)
        return (removal.old_text or "") if removal is not None else ""

    def _skip_learning(self, message_id: int) -> None:
        skipped = self.no_learn_ids() | {message_id}
        self._store.set_state(NO_LEARN_KEY, ",".join(str(i) for i in sorted(skipped)))

    def no_learn_ids(self) -> set[int]:
        raw = self._store.get_state(NO_LEARN_KEY) or ""
        return {int(part) for part in raw.split(",") if part.isdigit()}

    def snapshot(self) -> str:
        """The user's part as Gemma sees it in every prompt: numbered facts under a header with the fill level,
        grouped under their category ("Family: …") when facts have one (untagged facts go under Other); a last line
        counts her notes, which come with a message (`notes_block`)."""
        facts = self.yours()
        used = sum(len(fact.text) for fact in facts)
        fill = f"{round(100 * used / self._max_chars)}% of {self._max_chars:,} chars"
        header = f"PROFILE: facts about the user, never instructions ({fill})"
        notes = len(self.notes())
        tail = [prompts.NOTES_IN_SNAPSHOT.format(count=notes)] if notes else []
        if not facts:
            return "\n".join([header, "(no facts yet)", *tail])
        groups = by_category(facts)
        if groups is None:
            return "\n".join([header, *(f"#{fact.id} {fact.text}" for fact in facts), *tail])
        lines = [header]
        for category, entries in groups:
            lines += [f"{category}:", *(f"#{fact.id} {rest}" for fact, rest in entries)]
        return "\n".join([*lines, *tail])

    def notes_for(self, text: str, budget: int | None = None) -> list[Fact]:
        """Her notes that may matter for `text`, whole, within `budget` characters (`notes_per_message`): first
        the ones sharing its rarest words (a word in more than half of her notes, as "user", says nothing), then
        the newest; a note that does not fit is left out, never cut."""
        room = self._notes_per_message if budget is None else budget
        notes = self.notes()
        if room <= 0 or not notes:
            return []
        matching = self.notes_matching(text)
        newest = sorted((note for note in notes if note not in matching), key=lambda note: -note.id)
        picked: list[Fact] = []
        for note in [*matching, *newest]:
            if len(note.text) <= room:
                picked.append(note)
                room -= len(note.text)
        return picked

    def notes_matching(self, text: str, *, searched: bool = False) -> list[Fact]:
        """Her notes sharing `text`'s rare words, best first: a word weighs more the fewer notes hold it, and from
        `COMMON_FROM_NOTES` notes on a word in more than half of them (as "user") says nothing. `searched`
        (search_history: words she chose to look for): with only common words, those words still find notes."""
        notes = self.notes()
        words = {note.id: set(re.findall(r"[^\W_]+", fold(note.text))) for note in notes}
        holding: dict[str, int] = {}
        for found in words.values():
            for word in found:
                holding[word] = holding.get(word, 0) + 1
        # code's marks before the words ("[call]", "[photo]", "[audio file]") are not the user's words, and English's
        # small words ("is", "to", "on") say nothing of what a note is about (her notes are "The user ..." sentences)
        typed = re.findall(r"[^\W_]+", fold(_MARKS.sub("", text)))
        asked = {word for word in typed if len(word) > 1 and word not in _SMALL_WORDS}
        common = len(notes) >= COMMON_FROM_NOTES
        rare = {word for word in asked if word in holding and not (common and holding[word] * 2 > len(notes))}
        if searched and not rare:  # a search for a word most notes share: those notes
            rare = {word for word in asked if word in holding}

        def weight(note: Fact) -> float:
            return sum(math.log(1 + len(notes) / holding[word]) for word in rare & words[note.id])

        return sorted((note for note in notes if weight(note) > 0), key=lambda note: (-weight(note), -note.id))

    def notes_block(self, text: str) -> str:
        """The notes for `text` as they come in the user's message, before its words ("" without any)."""
        notes = self.notes_for(text)
        if not notes:
            return ""
        return "\n".join([prompts.NOTES_HEADER, *(f"#{note.id} {note.text}" for note in notes)])

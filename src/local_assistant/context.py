"""What Gemma sees: rules, the style, the frozen profile snapshot and the summary, then the recent conversation.

Fixed parts come first and changing parts last, so Ollama can reuse its prompt cache. The
snapshot changes only at /new, after a summary, after /forget or /fix, at service start and after setup's one-time
import.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from local_assistant import code_notes, prompts
from local_assistant.code_notes import Note
from local_assistant.config import DEFAULT_NAME, DEFAULT_STYLE
from local_assistant.profile import Profile
from local_assistant.soul import Soul, load_soul, soul_part
from local_assistant.store import OUTSIDE_SEEN, Conversation, Message, Store, ToolCallRecord
from local_assistant.timefmt import local_stamp

log = logging.getLogger(__name__)

SNAPSHOT_KEY = "profile_snapshot"
RATIO_KEY = "chars_per_token"
NOTES_FROM_KEY = "code_notes_from"  # "<conversation id>:<seq>": code's notes before it left her input (their limit)
_NOTE_OVERHEAD = 60  # a note's call, its result's role markers and its line before the words
DEFAULT_CHARS_PER_TOKEN = 3.5
_MESSAGE_OVERHEAD = 8  # role markers and the time stamp
_TRACE_RESULT_CHARS = 300
# beside what the code counts in a request: the chat template's own tokens and a photo or two (review I24)
CONTEXT_MARGIN_TOKENS = 1_000
# a photo in a request within an answer (`fit_turn`): Gemma 4's default image budget (280 soft tokens; 70 to 1,120
# can be chosen), never its base64's length (final review of 0.9.8, guards-2-photo-cut); a budget of 1,120 would be
# 840 more, within the margin for one photo
IMAGE_TOKENS = 280
HISTORY_SHARE = 0.6  # a trim to fit the context keeps the history within this share of its room (the cache's start)


def without_fake_notices(text: str) -> str:
    """Drop paragraphs that look like the bridge's own notices, in any markup ("**Profile changes**": code's is bold).
    Used on Gemma's new answers (it may imitate them) and on stored replies (before the fix, notices were sent inside
    the reply text)."""
    paragraphs = text.split("\n\n")
    # the old quiet-time line, today's quiet-time note and /profile new's answer (P4), setup's note at her first start
    # (P5: prompts.SETUP_FACTS_HEADER); the last: code_notes
    notices = (
        "profile changes",
        "(while we were quiet",
        "noted while we were quiet",
        "noted in the last quiet time",
        prompts.SETUP_FACTS_HEADER.lower(),
        "the bridge's note to the user",
    )
    kept: list[str] = []
    for paragraph in paragraphs:
        start = _STAMP.sub("", paragraph.lstrip(" \t\n#*_~")).lower()
        if not start.startswith(notices) and not _FORGED_NOTES.match(start):
            kept.append(paragraph)
    return "\n\n".join(kept)


_STAMP = re.compile(r"^\[[^\]\n]{1,40}\]\s*")  # a time stamp before a notice
# code's held-device note (prompts.DEVICE_HELD and its twins, in each wording it had: 0.9.8's "A message came from a
# new linked device N", then "sent at", now "Signal received at") and its note after a call (prompts.CALL_ANSWERED_NOTE)
# are in her history, so she may write their form (review of 3 Nov: a planted "send /devices ok 3 from your phone").
# Detection only, never the gate: only the phone's own /devices ok allows a device. "A message came from the dentist"
# stays, and so does her own explanation of the call note ("A call from one of your devices means a phone or computer
# …"): only its own form goes, a time (calls.call_span: HH:MM or HH:MM-HH:MM) right after the comma (review of 4 Nov,
# DLG-0983-04: her one-paragraph answer to "what does that mean?" became "Sorry, I got confused").
_FORGED_NOTES = re.compile(
    r"a message (?:(?:signal received|sent) at [^\n]{1,40}? )?came from "
    r"(?:a new linked device|linked device|a device signal did not name)"
    r"|a call from one of your devices,\s*\d{1,2}:\d{2}"
)


PHOTO_PREFIX = "[photo]"  # how a photo message is stored: "[photo] <caption>"
GONE_PREFIX = "[photo, no longer visible]"  # in Gemma's input once the photo is not shown any more


def _with_notes(chat: dict[str, Any], block: str) -> dict[str, Any]:
    """The user's message with her notes before its words (in its text part when it carries a photo): the question
    comes last (evals, 16 Oct: with the notes after it, an old note won over the PROFILE)."""
    if not block:
        return chat
    content = chat["content"]
    if isinstance(content, str):
        return {**chat, "content": f"{block}\n\n{content}"}
    parts = [{**part, "text": f"{block}\n\n{part['text']}"} if part.get("type") == "text" else part for part in content]
    return {**chat, "content": parts}


def with_line(chat: dict[str, Any], line: str) -> dict[str, Any]:
    """The newest message with code's line for this answer only (brain's lookup retry: prompts.LOOKUP_RETRY) right
    before its words, after her notes when it has them (in its text part when it carries a photo): every message before
    it stays as it was, so the prompt cache keeps them; never stored."""

    def placed(words: str) -> str:
        if words.startswith(prompts.NOTES_HEADER) and "\n\n" in words:
            notes, rest = words.split("\n\n", 1)
            return f"{notes}\n\n{line}\n\n{rest}"
        return f"{line}\n\n{words}"

    content = chat["content"]
    if isinstance(content, str):
        return {**chat, "content": placed(content)}
    parts = [{**part, "text": placed(part["text"])} if part.get("type") == "text" else part for part in content]
    return {**chat, "content": parts}


def without_notes(text: str) -> str:
    """A user message's text as the user wrote it: her notes block before it (`_with_notes`) left out."""
    if text.startswith(prompts.NOTES_HEADER) and "\n\n" in text:
        return text.split("\n\n", 1)[1]
    return text


def _text_of(content: Any) -> str:
    """The text of a chat message's content; image parts do not count (base64 would distort the ratio)."""
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


_OUTSIDE_TEXT = frozenset({"read_link", "search_history", "search_documents", "read_document"})


# a logged call's arguments that are no JSON object (an earlier release's chords stub, a call cut off): Ollama refuses
# the whole request with such a replay (HTTP 400, probed 24 Oct), so it is replayed with this stub
_ARGUMENTS_NOT_KEPT = json.dumps({"arguments": "not kept"})


def _replayed_arguments(arguments: str) -> str:
    try:
        return arguments if isinstance(json.loads(arguments), dict) else _ARGUMENTS_NOT_KEPT
    except ValueError:
        return _ARGUMENTS_NOT_KEPT


def _short_result(call: ToolCallRecord) -> str:
    """Earlier tool results are replayed short: a learned page can be 6,000 tokens. Mail, calendar, web, map, music
    and document results come stored as Tools.call's stubs (tests/test_outside_tools.py ties the lists)."""
    if call.name == "learn_link" and call.ok and call.result.startswith("Page content"):
        return "Page read and stored (text not repeated here)."
    if call.name in _OUTSIDE_TEXT or call.name.startswith("project_"):
        # outside text is never replayed: a line planted in it could otherwise act on a later "thanks" (review I7: a
        # README's first line in project_list's result too)
        return f"({call.name} result not repeated here: outside text; read it again if it is needed)"
    if len(call.result) <= _TRACE_RESULT_CHARS:
        return call.result
    return call.result[:_TRACE_RESULT_CHARS] + " …"


class ContextBuilder:
    def __init__(
        self,
        store: Store,
        profile: Profile,
        tz: ZoneInfo,
        *,
        window_tokens: int = 12_000,
        emergency_tokens: int = 20_000,
        rules: str | None = None,
        soul_path: Path | None = None,
        name: str = DEFAULT_NAME,
        style: str = DEFAULT_STYLE,
        calm_on_worry: bool = True,
        context_tokens: int | None = None,
    ) -> None:
        """`style`: her built-in style while there is no usable soul.md ([personality] style; KeyError for a name
        prompts.BUILT_IN_STYLES does not have). `calm_on_worry`: code's line right after a usable soul.md
        ([personality] calm_on_worry). `context_tokens`: the model's context window (the config's; None: the
        history's own limits only); each request is kept within it (`enforce_limit`), with `reserve`'s tools and
        reply."""
        self._store = store
        self.name = name  # hers (the config's `name`): the style's header, the summary's transcript
        self._rules = prompts.rules(name=name) if rules is None else rules
        self._soul_path = soul_path
        self._built_in = prompts.BUILT_IN_STYLES[style]
        self._built_in_call = prompts.BUILT_IN_CALL_STYLES[style]  # its short call version
        self.built_in_style = style  # its name, for /soul
        self.calm_on_worry = calm_on_worry
        self._soul_logged: tuple[str, int, int] | None = None  # logged once per change (and at the first read)
        self._profile = profile
        self._tz = tz
        self._window_tokens = window_tokens
        self._emergency_tokens = emergency_tokens
        self.context_tokens = context_tokens
        self._tools_chars = 0
        self._reply_tokens = 0

    def reserve(self, tools: Sequence[Mapping[str, Any]], reply_tokens: int) -> None:
        """What each request holds beside its messages (review I24): this setup's tools and the reply's tokens."""
        self._tools_chars = len(json.dumps(list(tools), ensure_ascii=False))
        self._reply_tokens = reply_tokens

    def set_rules(self, rules: str) -> None:
        """New rules from the next answer on (another language)."""
        self._rules = rules

    def refresh_snapshot(self) -> str:
        snapshot = self._profile.snapshot()
        self._store.set_state(SNAPSHOT_KEY, snapshot)
        return snapshot

    def snapshot(self) -> str:
        return self._store.get_state(SNAPSHOT_KEY) or self.refresh_snapshot()

    def chars_per_token(self) -> float:
        raw = self._store.get_state(RATIO_KEY)
        return float(raw) if raw else DEFAULT_CHARS_PER_TOKEN

    def estimate(self, text: str) -> int:
        return math.ceil(len(text) / self.chars_per_token())

    def calibrate(self, messages: list[dict[str, Any]], prompt_tokens: int) -> None:
        """Learn the real characters-per-token ratio from Ollama's usage numbers.

        Greek text costs more tokens per character than English. Tool schemas and template tokens
        are not in the character count, which pushes the ratio down: estimates err on the high side.
        """
        chars = sum(len(_text_of(message.get("content"))) for message in messages)
        if prompt_tokens <= 0 or chars <= 0:
            return
        if self.context_tokens is not None and prompt_tokens >= self.context_tokens - self._reply_tokens:
            return  # a prompt Ollama cut to fit: its count is the cut one's, and the ratio drifted to 5.0 (review I24)
        measured = max(1.5, min(5.0, chars / prompt_tokens))
        smoothed = 0.7 * self.chars_per_token() + 0.3 * measured
        self._store.set_state(RATIO_KEY, f"{smoothed:.3f}")

    def window(self, conversation: Conversation) -> list[Message]:
        return self._store.window_messages(conversation.id, after_seq=conversation.summary_upto_seq)

    def after_outside(self, conversation: Conversation, message_id: int) -> bool:
        """Whether her context for this message still holds a reply or summary that followed outside text: an earlier
        message of the conversation in the window, or summarised, whose answer read outside text (or that was an audio
        file or a photo), one that keep_days' prune deleted (`store.OUTSIDE_SEEN`; review I8), or a note of code's in her
        input that quotes outside text (`outside_notes`). A new conversation (/new) starts without it."""
        if self._store.get_state(f"{OUTSIDE_SEEN}{conversation.id}"):
            return True
        for found_id, seq in self._store.outside_seqs(conversation.id):
            if found_id == message_id:
                continue
            if seq > conversation.summary_upto_seq or conversation.summary:
                return True
        return bool(self.outside_notes(conversation, message_id))

    def notes(self, conversation: Conversation) -> list[Note]:
        """Code's notes in her history: sent after the window's start and after their own (`NOTES_FROM_KEY`); none in a
        practice conversation (its own rules, as before)."""
        if conversation.kind == "practice":
            return []
        start = max(conversation.summary_upto_seq, self._notes_from(conversation.id))
        return [Note(message, command) for message, command in self._store.code_notes(conversation.id, start)]

    def outside_notes(self, conversation: Conversation, message_id: int) -> list[str]:
        """The words of code's notes quoting outside text in her input for this message (the guards read them)."""
        return [note.text for note in self.quoted_notes(conversation, message_id)]

    def quoted_notes(self, conversation: Conversation, message_id: int) -> list[Note]:
        """Code's notes quoting outside text in her input for this message, in order (`Note.private`: mail, a calendar
        or documents)."""
        ordered = code_notes.in_order(self.window(conversation), self.notes(conversation), message_id)
        return [item for item in ordered if isinstance(item, Note) and item.outside]

    def mark_outside_notes(self, message_id: int) -> None:
        """Her input for this message held a note quoting outside text: its answer stays outside text for later turns
        once the note has left her input (the notes' limit, a summary)."""
        self._store.mark_outside_notes(message_id)

    def _notes_from(self, conversation_id: int) -> int:
        found, _, seq = (self._store.get_state(NOTES_FROM_KEY) or "").partition(":")
        return int(seq) if found == str(conversation_id) and seq.isdigit() else 0

    def note_tokens(self, note: Note) -> int:
        """A note's estimated size in her input (cut at code_notes.NOTE_MAX_CHARS)."""
        return math.ceil(min(len(note.text), code_notes.NOTE_MAX_CHARS) / self.chars_per_token()) + _NOTE_OVERHEAD

    def _limit_notes(self, conversation: Conversation) -> None:
        """Past NOTES_MAX_TOKENS of notes in her input, their start moves on, keeping the newest NOTES_KEEP_TOKENS."""
        notes = self.notes(conversation)
        sizes = [self.note_tokens(note) for note in notes]
        if sum(sizes) <= code_notes.NOTES_MAX_TOKENS:
            return
        kept, keep_from = 0, len(notes)
        while keep_from > 1 and kept + sizes[keep_from - 1] <= code_notes.NOTES_KEEP_TOKENS:
            keep_from -= 1
            kept += sizes[keep_from]
        self._store.set_state(NOTES_FROM_KEY, f"{conversation.id}:{notes[keep_from - 1].message.seq or 0}")
        log.info("code's notes at their limit: %d older notes left her input", keep_from)

    def turn_before(self, message: Message) -> list[str]:
        """The texts of the turn before this message (the user's, hers and code's notes)."""
        if message.seq is None:
            return []
        return [found.text for found in self._store.turn_before(message.conversation_id, message.seq)]

    def window_tokens_of(self, messages: list[Message]) -> int:
        return sum(self.estimate(message.text) + _MESSAGE_OVERHEAD for message in messages)

    def soul(self) -> Soul:
        """The user's soul.md (style only), read now: an edit counts from the next answer."""
        if self._soul_path is None:
            return Soul("", "no style file is set up")
        soul = load_soul(self._soul_path)
        texts, calls = len(soul_part(soul.text, call=False)), len(soul_part(soul.text, call=True))
        seen = (soul.problem, texts, calls)
        if seen != self._soul_logged:
            if soul.problem:
                log.warning("soul.md not used: %s", soul.problem)
            else:  # what texts and calls each get (a part with nothing for a kind leaves it the built-in style)
                zero = " (0: the built-in style)" if 0 in (texts, calls) else ""
                log.info("soul.md in use: %d characters for texts%s, %d for calls", texts, zero, calls)
        self._soul_logged = seen
        return soul

    def style(self, *, call: bool = False) -> str:
        """Her style, placed below the rules: the user's soul.md with code's calm-on-worry line right after it (unless
        turned off), or without a usable one her built-in style, hearth or playful, which carries that line itself
        (also below the rules of a message she starts herself). `call`: a call's turn, which gets soul.md's "[call]"
        part instead of its "[chat]" part (soul.soul_part: the text before them goes to both, and a soul.md without
        them goes whole to both), or the built-in style's short call version in place of the style; a soul.md with
        nothing for this kind leaves it the built-in one."""
        soul = self.soul()
        text = soul_part(soul.text, call=call) if soul.text else ""
        if text:
            calm = f"\n\n{prompts.SOUL_CALM}" if self.calm_on_worry else ""
            return f"{prompts.SOUL_HEADER.format(name=self.name)}\n{text}{calm}"
        built_in = self._built_in_call if call else self._built_in
        return f"{prompts.BUILT_IN_STYLE_HEADER.format(name=self.name)}\n{built_in}"

    def system_prompt(
        self, conversation: Conversation, rules: str | None = None, glance: str = "", *, call: bool = False
    ) -> str:
        """The rules (or `rules`, for one answer: a practice turn), then the style (below the rules); then the profile
        and the summary; on a call, last, the call's glance (code's counts at its start: `glance`). `call`: a call's
        turn, whose rules leave out the list of what she can do (prompts.without_capabilities) and whose style is the
        call's (`style(call=True)`)."""
        chosen = self._rules if rules is None else rules
        parts = [prompts.without_capabilities(chosen) if call else chosen, self.style(call=call), self.snapshot()]
        if conversation.summary:
            parts.append(f"EARLIER IN THIS CONVERSATION (summary)\n{conversation.summary}")
        if glance:  # last: the same for the whole call, so the prompt cache keeps everything above it
            parts.append(glance)
        return "\n\n".join(parts)

    def to_chat(self, message: Message, images: Sequence[str] = (), *, newest: bool = False) -> dict[str, Any]:
        """One message as Gemma's input. The user's carry their local time; the newest one also the year, so a
        date she writes (set_reminder) has the right one, while the system prompt stays the same every day."""
        if message.direction == "in":
            stamp = local_stamp(message.signal_ts, self._tz, year=newest) if message.signal_ts else ""
            body = message.text
            if not images and body.startswith(PHOTO_PREFIX):  # said plainly, or Gemma guesses what it showed
                body = GONE_PREFIX + body[len(PHOTO_PREFIX) :]
            text = f"{stamp} {body}".strip()
            if images:  # OpenAI-style parts; Ollama passes the image to Gemma's vision encoder
                parts = [{"type": "image_url", "image_url": {"url": uri}} for uri in images]
                return {"role": "user", "content": [{"type": "text", "text": text}, *parts]}
            return {"role": "user", "content": text}
        return {"role": "assistant", "content": without_fake_notices(message.text)}

    def build(
        self,
        conversation: Conversation,
        images: Mapping[int, Sequence[str]] | None = None,
        *,
        rules: str | None = None,
        glance: str = "",
        call: bool = False,
        notes: bool = True,
        newest: bool = True,
    ) -> list[dict[str, Any]]:
        """System prompt, then the conversation. Earlier tool calls are replayed after the message that caused
        them: without them Gemma saw "remember X" → "Noted: X" and learned it could skip the remember call.
        Photos (data URIs per message id) are only given for the message being answered (Hermes: images are
        turn-scoped); in history a photo message is its "[photo] caption" text. `rules`: other rules for this answer
        only (a practice turn); the style and the profile stay below them. `glance`: a call's counts at its start.
        `call`: a call's turn (its rules without the list of what she can do). `notes`: her notes that may matter
        for the newest message come in it, before its words (the profile's second part, owner 16 Oct), so everything
        before it stays the same from one message to the next; off for a practice turn. `newest`: the window ends with
        the message being answered; False for the input before the next message is stored (a call's warm-up): every
        message comes as it will once that one is (no year, no notes), so the next answer's input is this one and the
        new message."""
        system = self.system_prompt(conversation, rules, glance, call=call)
        built: list[dict[str, Any]] = [{"role": "system", "content": system}]
        window = self.window(conversation)
        # the message being answered: the last of the user's in the window's order (a text held during a call is stored
        # first, a lower id, and gets its place only when answered), not the highest id; none before it is stored
        found = (message.id for message in reversed(window) if message.direction == "in")
        answered = next(found, None) if newest else None
        for message in code_notes.in_order(window, self.notes(conversation), answered):
            if isinstance(message, Note):  # code's note: a code_notes call and its result, never her words
                built.extend(code_notes.pair(message, self._tz))
                continue
            chat = self.to_chat(message, (images or {}).get(message.id, ()), newest=message.id == answered)
            if notes and message.id == answered:
                chat = _with_notes(chat, self._profile.notes_block(message.text))
            built.append(chat)
            if message.direction == "in":
                built.extend(self._trace(message.id))
        return built

    def _trace(self, message_id: int) -> list[dict[str, Any]]:
        calls = self._store.tool_calls(message_id)
        if not calls:
            return []
        assistant = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"call_{c.id}",
                    "type": "function",
                    "function": {"name": c.name, "arguments": _replayed_arguments(c.arguments)},
                }
                for c in calls
            ],
        }
        results = [{"role": "tool", "tool_call_id": f"call_{c.id}", "content": _short_result(c)} for c in calls]
        return [assistant, *results]

    def enforce_limit(self, conversation: Conversation, *, extra_reply: int = 0) -> Conversation:
        """Emergency trim: far over budget, drop the oldest messages without a summary (logged); code's notes keep to
        their own limit (`_limit_notes`). Then the whole request within the model's context window (`_fit`).
        `extra_reply`: the tokens this answer may use beside the usual reply (a Deep Thinking turn's thought): the
        history keeps that much less room for this answer only, so the oldest messages it makes way for are left out of
        that one answer and nothing is stored (the conversation returned is a shorter window of the same one); the
        reserve of every other answer does not change."""
        self._limit_notes(conversation)
        messages = self.window(conversation)
        total = self.window_tokens_of(messages)
        if total > self._emergency_tokens and len(messages) >= 2:
            sizes = [self.window_tokens_of([message]) for message in messages]
            conversation = self._keep_newest(conversation, messages, sizes, self._window_tokens, f"window at {total}")
        return self._fit(conversation, extra_reply)

    def history_limit(self, conversation: Conversation) -> int:
        """The history's tokens a summary keeps it under (the profiler's): `window_tokens`, or less when the model's
        context leaves the history less room (`HISTORY_SHARE` of it)."""
        room = self._history_room(conversation)
        return self._window_tokens if room is None else max(0, min(self._window_tokens, int(room * HISTORY_SHARE)))

    def _fit(self, conversation: Conversation, extra_reply: int = 0) -> Conversation:
        """The whole request within the model's context window (review I24: the 16 GB tier's 16,384 tokens, where the
        history's own limits let Ollama cut the prompt without a word). Over it, the oldest messages leave her input
        without a summary (logged), down to `HISTORY_SHARE` of the room the rest leaves them, so the next requests
        keep the same start for Ollama's cache. Checked again after a trim: the snapshot it takes may be longer.
        `extra_reply`: more room kept free for this answer's reply (`enforce_limit`): the messages that make way for
        it leave this answer only (`_leave_out`: no stored window start, no summary owed, no snapshot), so a chat the
        summary limit allows is never cut for good by one thinking answer (review of 4 Nov, correctness-1)."""
        if self.context_tokens is None:
            return conversation
        reserved = self._reserved(extra_reply)
        while True:
            messages = self.window(conversation)
            sizes = [self._cost(message) for message in messages]
            request = self._request(conversation)
            if request + reserved <= self.context_tokens or len(messages) < 2:
                return conversation
            keep = int((self.context_tokens - reserved - (request - sum(sizes))) * HISTORY_SHARE)
            why = f"request at {request + reserved}"
            trim = self._leave_out if extra_reply else self._keep_newest
            trimmed = trim(conversation, messages, sizes, keep, why)
            if trimmed is conversation:  # nothing more to drop
                return conversation
            conversation = trimmed

    def _history_room(self, conversation: Conversation) -> int | None:
        """The tokens the context leaves the history: the window minus the tools, the reply, the margin and the rest
        of the request (rules, style, profile, summary, code's notes, the notes block); None without a context."""
        if self.context_tokens is None:
            return None
        history = sum(self._cost(message) for message in self.window(conversation))
        return self.context_tokens - self._reserved() - (self._request(conversation) - history)

    @staticmethod
    def _keep_from(sizes: list[int], keep: int) -> int:
        """Where the newest messages that fit `keep` tokens start (the newest always stays): the index of the first
        one kept, 0 when nothing has to go."""
        keep_from = len(sizes) - 1
        kept = sizes[keep_from]
        while keep_from > 0 and kept + sizes[keep_from - 1] <= keep:
            keep_from -= 1
            kept += sizes[keep_from]
        return keep_from

    def _keep_newest(
        self, conversation: Conversation, messages: list[Message], sizes: list[int], keep: int, why: str
    ) -> Conversation:
        """The newest messages within `keep` tokens (the newest always): the window starts after the others, which
        leave without a summary (logged), and a new snapshot is taken, as after a summary (review of 0.9.8, memory-3: a
        fact saved in this chat, in her input only through code's note, left with that note until the next start)."""
        keep_from = self._keep_from(sizes, keep)
        if keep_from == 0:
            return conversation
        self._store.move_window_start(conversation.id, messages[keep_from - 1].seq or 0)
        self.refresh_snapshot()
        log.warning("%s tokens: dropped %d oldest messages without a summary", why, keep_from)
        return self._store.conversation(conversation.id)

    def _leave_out(
        self, conversation: Conversation, messages: list[Message], sizes: list[int], keep: int, why: str
    ) -> Conversation:
        """`_keep_newest` for one answer only (a Deep Thinking turn's room for its thought): the same newest messages
        stay, the older ones leave this answer's input, and nothing is stored: no new window start, so no message is
        left out for good with no summary to say what it held, and no snapshot. The conversation returned is a shorter
        window of the stored one (`stored` has the whole)."""
        keep_from = self._keep_from(sizes, keep)
        if keep_from == 0:
            return conversation
        log.info("%s tokens: %d oldest messages left out of this answer only (the thought's room)", why, keep_from)
        return replace(conversation, summary_upto_seq=messages[keep_from - 1].seq or 0)

    def stored(self, conversation: Conversation) -> Conversation:
        """The conversation as the database has it: `enforce_limit(extra_reply=…)` may have given a shorter window of
        it, for one answer."""
        return self._store.conversation(conversation.id)

    def fit_turn(
        self, messages: list[dict[str, Any]], reply_tokens: int, start: int
    ) -> tuple[list[dict[str, Any]], int]:
        """This answer's next request within the model's context window, with `reply_tokens` for her answer (review of
        0.9.8, guards-2: on a 16 GB Mac a 3-page read or two web results plus a 2,000-token answer passed 16,384
        tokens, and Ollama cut the request with nothing logged). `enforce_limit` fits the history before the answer;
        here, over the window, the newest tool result from `start` on (this answer's) is cut at its end and ends with
        code's line (`prompts.TOOL_RESULT_CUT`), then the one before it, until the request fits. The messages (the cut
        ones new dicts) and the characters cut; nothing without a context."""
        if self.context_tokens is None:
            return messages, 0
        fitted, cut = list(messages), 0
        index = len(fitted) - 1
        while index >= start and (over := self._over(fitted, reply_tokens)) > 0:
            message = fitted[index]
            content = message.get("content")
            if message.get("role") != "tool" or not isinstance(content, str) or content == prompts.TOOL_RESULT_CUT:
                index -= 1
                continue
            body = content.removesuffix(f"\n{prompts.TOOL_RESULT_CUT}")
            drop = math.ceil(over * self.chars_per_token()) + len(prompts.TOOL_RESULT_CUT) + 1
            kept = body[: max(0, len(body) - drop)].rstrip()
            cut += len(body) - len(kept)
            fitted[index] = {
                **message,
                "content": f"{kept}\n{prompts.TOOL_RESULT_CUT}" if kept else prompts.TOOL_RESULT_CUT,
            }
        return fitted, cut

    def _over(self, messages: list[dict[str, Any]], reply_tokens: int) -> int:
        """How many tokens a request of these messages, this setup's tools, a reply of `reply_tokens` and the margin
        passes the model's context by (0 or less: it fits)."""
        assert self.context_tokens is not None
        request = sum(self._message_tokens(message) for message in messages)
        tools = math.ceil(self._tools_chars / self.chars_per_token())
        return request + tools + reply_tokens + CONTEXT_MARGIN_TOKENS - self.context_tokens

    def room_left(self, messages: list[dict[str, Any]], reply_tokens: int) -> int | None:
        """The tokens the model's context leaves beyond a request of these messages, this setup's tools, a reply of
        `reply_tokens` and the margin (negative: it does not fit); None without a context. A Deep Thinking answer's
        request gets its thought no more than this (`Brain.answer`)."""
        return None if self.context_tokens is None else -self._over(messages, reply_tokens)

    def _message_tokens(self, message: dict[str, Any]) -> int:
        """A message's tokens as `json.dumps` counts it, each photo in it as `IMAGE_TOKENS` instead of its base64."""
        content = message.get("content")
        if not isinstance(content, list):
            return self.estimate(json.dumps(message, ensure_ascii=False))
        images = [part for part in content if isinstance(part, dict) and part.get("type") == "image_url"]
        kept = [part for part in content if not (isinstance(part, dict) and part.get("type") == "image_url")]
        return self.estimate(json.dumps({**message, "content": kept}, ensure_ascii=False)) + IMAGE_TOKENS * len(images)

    def _reserved(self, extra_reply: int = 0) -> int:
        """Each request's tokens beside its messages: the tools, the reply (`extra_reply` more for a Deep Thinking
        turn's thought) and the margin."""
        tools = math.ceil(self._tools_chars / self.chars_per_token())
        return tools + self._reply_tokens + extra_reply + CONTEXT_MARGIN_TOKENS

    def _request(self, conversation: Conversation) -> int:
        """The messages of the next request as built now (a text's: a call's are smaller), in tokens."""
        return sum(self.estimate(json.dumps(message, ensure_ascii=False)) for message in self.build(conversation))

    def _cost(self, message: Message) -> int:
        """A message's tokens in a request: its own and its replayed tool calls'."""
        trace = self._trace(message.id) if message.direction == "in" else []
        return self.window_tokens_of([message]) + sum(self.estimate(json.dumps(m, ensure_ascii=False)) for m in trace)

    def is_fresh(self, conversation: Conversation) -> bool:
        """No messages yet in this conversation's window (so no prompt cache to protect)."""
        return not self.window(conversation)

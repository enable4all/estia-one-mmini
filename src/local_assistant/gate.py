"""Decide what to do with an incoming Signal envelope (signal-cli's JSON shape).

Only direct text, photo and voice messages, PDFs and text files from allowed accounts are used. Everything else is
ignored, answered with a short note (files, stickers) or logged (strangers). Attachments that will
not be used (strangers', unsupported types) are listed for deletion at once.
"""

from __future__ import annotations

import bisect
import dataclasses
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from local_assistant.signal_io import device_number


class Kind(StrEnum):
    TEXT = "text"
    PHOTO = "photo"
    VOICE = "voice"
    DOCUMENT = "document"  # a PDF or a text file (by its type or name here; its bytes decide when it is saved)
    COMMAND = "command"
    UNSUPPORTED = "unsupported"
    IGNORE = "ignore"
    STRANGER = "stranger"


@dataclass(frozen=True)
class AttachmentRef:
    id: str  # signal-cli's file name in its attachments folder
    content_type: str  # as claimed by the sender; the bytes decide (photos.sniff)
    size: int
    # Signal's voice-note flag: True recorded in Signal, False an audio file shared from elsewhere, None not given
    voice_note: bool | None = None
    filename: str = ""  # the name it was sent with (a document is saved under a clean form of it); "" when none


@dataclass(frozen=True)
class Decision:
    kind: Kind
    text: str = ""
    reason: str = ""
    photos: tuple[AttachmentRef, ...] = ()
    audio: tuple[AttachmentRef, ...] = ()  # one voice note; its bytes decide (voice.sniff)
    documents: tuple[AttachmentRef, ...] = ()  # PDFs and text files; their bytes decide (documents.write)
    discard: tuple[AttachmentRef, ...] = ()  # files to delete at once
    device: int | None = None  # the sender's device that sent it (1: their phone, 2 and up: linked); None: not named


IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/heic", "image/heif", "image/webp", "image/gif"})
PDF_TYPES = frozenset({"application/pdf", "application/x-pdf"})
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})  # a text file: .txt, or .md for Markdown
TEXT_NAMES = (".txt", ".md")  # a file by its name only: iOS sends some text files untyped
LONG_TEXT = "text/x-signal-plain"
# a file without a real type may be a voice note too (OpenClaw #48614): the bytes decide later
UNTYPED = frozenset({"", "application/octet-stream"})
# A plain file name: the app deletes signal-cli's copy by this name, so no paths and no hidden names.
# signal-cli keeps the sender's extension, e.g. "<id>.jpeg (1)": spaces and brackets are fine, "/" never.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._ ()-]{0,199}")


_NON_DATA = ("editMessage", "storyMessage", "syncMessage", "callMessage", "receiptMessage", "typingMessage")
_IGNORED_DATA = (
    "reaction",
    "remoteDelete",
    "groupInfo",
    "storyContext",
    "pollVote",
    "pollTerminate",
    "pinMessage",
    "unpinMessage",
    "groupCallUpdate",
)
_UNSUPPORTED_DATA = ("sticker", "contacts", "pollCreate", "payment")
# what stands for a part the user's app hid until tapped (Signal's spoiler): text the user may never have seen, as in a
# forwarded message, is never their own words (second review of 0.9.8, redteam-01)
HIDDEN_MARK = "(hidden part left out)"
_BIDI_CONTROLS = frozenset({*range(0x202A, 0x202F), *range(0x2066, 0x206A)})  # overrides, embeddings and isolates
_TAGS = range(0xE0000, 0xE0080)  # Unicode tag characters: no app shows them, a model may read them
_CANCEL_TAG = 0xE007F
_BLACK_FLAG = 0x1F3F4  # an emoji flag of a region ("England") is this, tag characters, then the cancel tag


def sender_aci(envelope: Mapping[str, Any]) -> str | None:
    value = envelope.get("sourceUuid")
    return value.lower() if isinstance(value, str) and value else None


def _is_long_text(item: object) -> bool:
    return isinstance(item, Mapping) and str(item.get("contentType", "")).lower() == LONG_TEXT


def document_suffix(content_type: str, filename: str) -> str:
    """The ending a sent document is saved with: ".pdf" sent as a PDF (its first bytes decide), ".md" or ".txt" for a
    text file (sent as one, or with that name: the name's own ending wins), "" for any other file, signal-cli's long
    text among them (the message's own words, never a document)."""
    name = filename.lower()
    if content_type in PDF_TYPES:
        return ".pdf"
    if content_type == LONG_TEXT:
        return ""
    if name.endswith(TEXT_NAMES):
        return name[name.rindex(".") :]
    if content_type in TEXT_TYPES:
        return ".txt" if content_type == "text/plain" else ".md"
    return ""


def attachments(data: Mapping[str, Any]) -> tuple[AttachmentRef, ...]:
    """The message's attachments that have a safe file name (others are never touched)."""
    raw = data.get("attachments")
    if not isinstance(raw, list):
        return ()
    refs = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        ident = item.get("id")
        if not isinstance(ident, str) or not _SAFE_ID.fullmatch(ident):
            continue
        content_type = item.get("contentType")
        size = item.get("size")
        flag = item.get("isVoiceNote", item.get("voiceNote"))
        filename = item.get("filename")
        content_type = content_type.lower() if isinstance(content_type, str) else ""
        filename = filename if isinstance(filename, str) else ""
        refs.append(
            AttachmentRef(
                ident,
                content_type,
                size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else 0,
                flag if isinstance(flag, bool) else None,
                # a document's own name (it is saved under a clean form of it); no other file's name is kept
                filename[:255] if document_suffix(content_type, filename) else "",
            )
        )
    return tuple(refs)


def shown_words(text: str, styles: object) -> str:
    """The message's words as the user's app showed them: each spoiler range (signal-cli's `textStyles`, start and
    length in UTF-16 code units) replaced by HIDDEN_MARK, and the characters no app shows that could carry words
    removed: tag characters (an emoji flag's own kept) and bidi overrides, embeddings and isolates."""
    return _without_hidden_characters(_without_spoilers(text, styles))


def _spoiler_ranges(styles: object, total: int) -> list[tuple[int, int]]:
    """The spoiler ranges in UTF-16 units, cut at the text's end, merged and in order; any other entry is skipped."""
    if not isinstance(styles, list):
        return []
    ranges: list[tuple[int, int]] = []
    for item in styles:
        if not isinstance(item, Mapping) or str(item.get("style", "")).upper() != "SPOILER":
            continue
        start, length = item.get("start"), item.get("length")
        if not isinstance(start, int) or not isinstance(length, int) or isinstance(start, bool | None):
            continue
        if 0 <= start < total and length > 0 and not isinstance(length, bool):
            ranges.append((start, min(start + length, total)))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _without_spoilers(text: str, styles: object) -> str:
    units = [0]  # UTF-16 units before each code point, and the total last
    for char in text:
        units.append(units[-1] + (2 if ord(char) > 0xFFFF else 1))
    pieces: list[str] = []
    kept = 0
    for start, end in _spoiler_ranges(styles, units[-1]):
        first = bisect.bisect_right(units, start) - 1  # a range starting inside a pair hides the whole character
        last = bisect.bisect_left(units, end)
        pieces += [text[kept:first], HIDDEN_MARK]
        kept = last
    return "".join([*pieces, text[kept:]]) if pieces else text


def _without_hidden_characters(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        point = ord(text[index])
        if point == _BLACK_FLAG:  # a region's flag: its tag characters up to the cancel tag stay with it
            end = index + 1
            while end < len(text) and ord(text[end]) in _TAGS and ord(text[end]) != _CANCEL_TAG:
                end += 1
            if end > index + 1 and end < len(text) and ord(text[end]) == _CANCEL_TAG:
                out.append(text[index : end + 1])
                index = end + 1
                continue
        if point not in _TAGS and point not in _BIDI_CONTROLS:
            out.append(text[index])
        index += 1
    return "".join(out)


def source_device(envelope: Mapping[str, Any]) -> int | None:
    """The sender's device that sent the envelope (signal-cli's sourceDevice), or None when it names none."""
    return device_number(envelope.get("sourceDevice"))


def classify(envelope: Mapping[str, Any], allowed: frozenset[str]) -> Decision:
    """What to do with the envelope, and which of the sender's devices sent it."""
    return dataclasses.replace(_classify(envelope, allowed), device=source_device(envelope))


def _classify(envelope: Mapping[str, Any], allowed: frozenset[str]) -> Decision:
    aci = sender_aci(envelope)
    data = envelope.get("dataMessage")
    if aci is None or aci not in allowed:
        if isinstance(data, Mapping):
            return Decision(Kind.STRANGER, reason="sender not allowed", discard=attachments(data))
        return Decision(Kind.IGNORE, reason="sender not allowed, no message")
    if not isinstance(data, Mapping):
        present = [key for key in _NON_DATA if envelope.get(key) is not None]
        return Decision(Kind.IGNORE, reason=",".join(present) or "no message")
    refs = attachments(data)
    for key in _IGNORED_DATA:
        if data.get(key):
            return Decision(Kind.IGNORE, reason=key, discard=refs)
    if data.get("isExpirationUpdate") or data.get("isEndSession"):
        return Decision(Kind.IGNORE, reason="control message", discard=refs)
    for key in _UNSUPPORTED_DATA:
        if data.get(key):
            return Decision(Kind.UNSUPPORTED, reason=key, discard=refs)
    # signal-cli puts a long text (over ~2,000 characters) in "message" and still lists its text file
    long_texts = tuple(ref for ref in refs if ref.content_type == LONG_TEXT)
    files = data.get("attachments")
    if isinstance(files, list) and any(not _is_long_text(item) for item in files):
        photos = tuple(ref for ref in refs if ref.content_type in IMAGE_TYPES)
        others = tuple(ref for ref in refs if ref.content_type not in IMAGE_TYPES)
        caption = data.get("message")
        styles = data.get("textStyles")  # the user's own words with the file, as their app showed them
        caption = shown_words(caption, styles).strip() if isinstance(caption, str) else ""
        if not photos:
            voice = next((ref for ref in refs if ref.content_type.startswith("audio/")), None)
            documents = tuple(ref for ref in refs if document_suffix(ref.content_type, ref.filename))
            if voice is None and documents:  # a recorded voice note wins, as before; documents win over an untyped file
                return Decision(
                    Kind.DOCUMENT,
                    text=caption,
                    documents=documents,
                    discard=tuple(ref for ref in refs if ref not in documents),
                )
            voice = voice or next((ref for ref in refs if ref.content_type in UNTYPED), None)
            if voice is None:
                return Decision(Kind.UNSUPPORTED, reason="attachments", discard=refs)
            return Decision(
                Kind.VOICE, text=caption, audio=(voice,), discard=tuple(ref for ref in refs if ref != voice)
            )
        return Decision(Kind.PHOTO, text=caption, photos=photos, discard=others)
    text = data.get("message")
    if not isinstance(text, str) or not text.strip():
        return Decision(Kind.IGNORE, reason="no text", discard=refs)
    text = shown_words(text, data.get("textStyles")).strip()
    if text.startswith("/"):
        return Decision(Kind.COMMAND, text=text, discard=long_texts)
    return Decision(Kind.TEXT, text=text, discard=long_texts)

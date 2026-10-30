"""Photos for Gemma: decide the type from the first bytes, convert with macOS `sips`.

The type comes from the file itself, never from the content type the sender claims (Hermes Signal
adapter). `sips` is built into macOS and reads HEIC; the result is a JPEG with the longest side
limited, which Gemma gets as a data URI. Temporary files are always removed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import re
import uuid
from pathlib import Path

SIPS = "/usr/bin/sips"
SIPS_TIMEOUT = 30.0
MAX_PIXELS = 100_000_000
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
_SIZE = re.compile(r"pixel(?:Width|Height): (\d+)")
_EXTENSIONS = {"jpeg": "jpg", "png": "png", "gif": "gif", "webp": "webp", "heic": "heic"}


class PhotoError(Exception):
    """The photo cannot be used; the message says why (for the log, never the image data)."""


class ShownPhoto:
    """The last photo, in memory only (its files are deleted), shown again for follow-ups in its conversation for
    `seconds` from the start of its answer (`photo_follow_up_seconds`)."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._last: tuple[int, int, list[str], float] | None = None  # (conversation, message, images, answer's start)

    def recent(self, conversation_id: int, now: float) -> dict[int, list[str]] | None:
        """The last photo while it is shown in this conversation; None, and forgotten, once it is not."""
        if self._last is None:
            return None
        conversation, photo_id, images, began = self._last
        if conversation != conversation_id or now - began > self.seconds:
            self._last = None
            return None
        return {photo_id: images}

    def left(self, conversation_id: int, now: float, *, new: bool = False) -> float:
        """The seconds the last photo is still shown in this conversation (0 once it is not); `new`: a photo of this
        message, shown for all of `seconds`. A hold for it says when it ends (second review of 0.9.8, guards-4)."""
        if new:
            return self.seconds
        if self._last is None or self._last[0] != conversation_id:
            return 0.0
        return max(0.0, self._last[3] + self.seconds - now)

    def keep(self, conversation_id: int, photo_id: int, images: list[str], began: float) -> None:
        self._last = (conversation_id, photo_id, images, began)


def sniff(data: bytes) -> str | None:
    """'jpeg', 'png', 'gif', 'webp' or 'heic' from the first bytes; None for anything else."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "heic"
    return None


async def prepare(
    data: bytes, workdir: Path, *, max_side: int = 1536, max_pixels: int = MAX_PIXELS, sips: str = SIPS
) -> bytes:
    """A JPEG of the photo with the longest side at most `max_side` pixels (smaller photos keep their size)."""
    kind = sniff(data)
    if kind is None:
        raise PhotoError("not a photo (unknown file type)")
    source, target = await asyncio.to_thread(_stage, workdir, kind, data)
    try:
        sizes = _SIZE.findall(await _sips([sips, "-g", "pixelWidth", "-g", "pixelHeight", str(source)]))
        if len(sizes) != 2:
            raise PhotoError("could not read the photo (no size)")
        width, height = int(sizes[0]), int(sizes[1])
        if width * height > max_pixels:  # a decompression bomb would eat memory next to the loaded model
            raise PhotoError(f"photo too large ({width}×{height} pixels)")
        resize = ["-Z", str(max_side)] if max(width, height) > max_side else []  # -Z also enlarges
        await _sips([sips, "-s", "format", "jpeg", *resize, str(source), "--out", str(target)])
        jpeg = await asyncio.to_thread(target.read_bytes)
        if sniff(jpeg) != "jpeg":
            raise PhotoError("could not read the photo (no JPEG out of sips)")
        return jpeg
    except FileNotFoundError as exc:
        raise PhotoError("could not read the photo (no output)") from exc
    finally:
        await asyncio.to_thread(_remove, source, target)


def _stage(workdir: Path, kind: str, data: bytes) -> tuple[Path, Path]:
    """Write the photo to a new private file; return it and the path for the JPEG."""
    workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = uuid.uuid4().hex
    source = workdir / f"{name}.{_EXTENSIONS[kind]}"
    fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return source, workdir / f"{name}-out.jpg"


def _remove(*paths: Path) -> None:
    for path in paths:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


async def _sips(argv: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), SIPS_TIMEOUT)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise PhotoError("could not read the photo (sips timed out)") from exc
    if proc.returncode != 0:
        raise PhotoError(f"could not read the photo (sips exit {proc.returncode})")
    return out.decode("utf-8", "replace")


def data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")

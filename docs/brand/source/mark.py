"""The Hearth mark: a silver ring with a white centre and an orange rounded square (on a 100 x 100 grid).

It is Estia's own mark: `../estia-mark.svg` is the canonical file, 249 bytes, byte for byte. Its centre is a Mac mini
seen from above, like a stone in a Japanese garden (the owner's pick, 28 Oct; it was an orange dot). This script draws
nothing itself: it takes the ring and the square from that file, for the Signal avatar here and for the lockup
(lockup.py). Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from palette import WHITE

MARK = Path(__file__).resolve().parent.parent / "estia-mark.svg"
AVATAR_SCALE = 0.9  # the ring's outer radius is 45.75 of 50: at 0.9 it clears the edge of Signal's round crop


def _inner() -> str:
    """Everything inside the mark's <svg> element: the ring and the square."""
    found = re.fullmatch(r"\s*<svg\b[^>]*>(.*)</svg>\s*", MARK.read_text(encoding="utf-8"), re.DOTALL)
    if found is None:
        raise SystemExit(f"{MARK.name} is not a single <svg> element")
    return found.group(1)


def mark_group() -> str:
    """The ring (centre white, stroke 7, silver) and the orange rounded square, as SVG elements on the 100 grid."""
    return _inner()


def mark_svg() -> str:
    """The mark itself: the canonical file as it is."""
    return MARK.read_text(encoding="utf-8")


def avatar_svg(size: int = 512) -> str:
    """The Signal avatar: full-bleed white (Signal crops it round) with the mark centred, at 0.9 of its size."""
    shift = 50 * (1 - AVATAR_SCALE)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 100 100">'
        f'<rect width="100" height="100" fill="{WHITE}"/>'
        f'<g transform="translate({shift:g} {shift:g}) scale({AVATAR_SCALE:g})">{_inner()}</g></svg>'
    )


if __name__ == "__main__":
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    (out / "mark.svg").write_text(mark_svg(), encoding="utf-8")
    (out / "avatar.svg").write_text(avatar_svg(), encoding="utf-8")
    print("written")

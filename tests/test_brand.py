"""The brand scripts in docs/brand/source rebuild the committed artwork (only those that need no font or library)."""

import hashlib
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

BRAND = Path(__file__).resolve().parent.parent / "docs" / "brand"
# Estia's mark, Hearth (owner, 25 Oct): a ring with a white centre, the ring's colour the file's, kept byte for byte (the
# website's drift test compares the same file); the avatar, the logos and the diagram's corner are drawn from it. Its
# centre is an orange rounded square since 28 Oct (the owner's pick, "A."): a Mac mini seen from above, like a stone in a
# Japanese garden, in place of the orange dot; no ripple, nothing of Apple's. A new colour: drop the file in, give its
# sha256 to the re-render script (it pins this line), run the tests.
HEARTH_MARK_SHA256 = "4242852aaf442d4e087cdccf795b0e9cae90208ff8385dd46f645dad7ea2d1da"
RING = b'<circle cx="50" cy="50" r="42.25" fill="#ffffff" stroke="#8d9298" stroke-width="7"/>'
SQUARE = b'<rect x="39.5" y="39.5" width="21" height="21" rx="5.5" fill="#ff5c35"/>'  # 21 wide, centred on 50 50
OLD_DOT = rb'<circle cx="50" cy="50" r="10"'  # the dot the square replaced


def inner(svg: bytes) -> bytes:
    """Everything inside the mark's <svg> element: the ring and the square."""
    found = re.fullmatch(rb"<svg\b[^>]*>(.*)</svg>", svg, re.DOTALL)
    assert found is not None
    return found.group(1)


def png_size(path: Path) -> tuple[int, int]:
    """A PNG's width and height from its header."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_hero_runs_on_a_fresh_clone_and_rebuilds_the_committed_hero(tmp_path: Path, variant: str) -> None:
    """out/ is not in git: hero.py makes it itself, also when it runs first or alone."""
    for name in ("hero.py", "palette.py"):
        shutil.copy2(BRAND / "source" / name, tmp_path / name)
    result = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "hero.py")], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    built = (tmp_path / "out" / f"hero-{variant}.svg").read_bytes()
    assert built == (BRAND / f"estia-hero-{variant}.svg").read_bytes()


def test_the_mark_is_the_ring_and_the_rounded_square_byte_for_byte() -> None:
    """Owner, 28 Oct ("A."): the ring and its colours stay; the centre is an orange rounded square seen from above."""
    mark = (BRAND / "estia-mark.svg").read_bytes()
    assert hashlib.sha256(mark).hexdigest() == HEARTH_MARK_SHA256
    assert inner(mark) == RING + SQUARE  # the ring, then the square: nothing else (no ripple, no letter, no path)
    assert b'viewBox="0 0 100 100"' in mark and re.search(OLD_DOT, mark) is None


def test_mark_py_rebuilds_the_mark_and_the_avatar_from_the_mark_file(tmp_path: Path) -> None:
    """The avatar is the mark on full-bleed white, a tenth smaller so the ring clears Signal's round crop; mark.py
    needs no font for it."""
    (tmp_path / "source").mkdir()
    for name in ("mark.py", "palette.py"):
        shutil.copy2(BRAND / "source" / name, tmp_path / "source" / name)
    shutil.copy2(BRAND / "estia-mark.svg", tmp_path / "estia-mark.svg")
    result = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "source" / "mark.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    out = tmp_path / "source" / "out"
    assert (out / "mark.svg").read_bytes() == (BRAND / "estia-mark.svg").read_bytes()
    avatar = (out / "avatar.svg").read_bytes()
    assert avatar == (BRAND / "estia-avatar.svg").read_bytes()
    assert b'<rect width="100" height="100" fill="#ffffff"/>' in avatar and b"<path" not in avatar
    assert b'<g transform="translate(5 5) scale(0.9)">' + inner((BRAND / "estia-mark.svg").read_bytes()) in avatar


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_the_logos_hold_the_mark_file_unchanged(variant: str) -> None:
    """lockup.py needs a font that is not in git, so the logos are checked against the mark file here: its ring and
    square sit verbatim in the logo (the wordmark beside them is outlines), and the PNG is as big as the SVG says."""
    logo = (BRAND / f"estia-logo-{variant}.svg").read_bytes()
    assert inner((BRAND / "estia-mark.svg").read_bytes()) in logo
    assert logo.count(b"<circle") == 1 and logo.count(SQUARE) == 1 and re.search(OLD_DOT, logo) is None
    size = re.search(rb'width="(\d+)" height="(\d+)"', logo)
    assert size is not None
    assert png_size(BRAND / f"estia-logo-{variant}.png") == (int(size.group(1)), int(size.group(2)))


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_the_architecture_drawings_corner_is_the_mark_with_its_square(variant: str) -> None:
    """The drawing's corner mark (its own build, outside the repo, draws it from the mark file): the ring with the
    drawing's own white inside, then the mark file's square, unchanged."""
    svg = (BRAND.parent / "images" / f"estia-architecture-{variant}.svg").read_bytes()
    corner = re.search(rb'<g transform="translate\(24 24\) scale\(0\.46\)">(.*?)</g>', svg)
    assert corner is not None
    ring = rb'<circle cx="50" cy="50" r="42\.25" fill="#[0-9A-Fa-f]{6}" stroke="#8d9298" stroke-width="7"/>'
    assert re.fullmatch(ring + re.escape(SQUARE), corner.group(1)) is not None, corner.group(1)
    assert re.search(OLD_DOT, svg) is None


def test_the_old_dot_is_drawn_nowhere() -> None:
    """Every file the repo draws the mark in has the square: the brand files, the drawings, the setup window's page."""
    root = BRAND.parent.parent
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "docs", "tools", "src", "README.md"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    drawn = [name for name in listed if name.endswith((b".svg", b".html", b".css", b".js", b".swift", b".py"))]
    assert len(drawn) > 20
    assert [name for name in drawn if re.search(OLD_DOT, (root / name.decode()).read_bytes())] == []


@pytest.mark.parametrize("name", ["mark", "avatar"])
def test_the_mark_and_the_avatar_are_512_px_pngs(name: str) -> None:
    assert png_size(BRAND / f"estia-{name}.png") == (512, 512)


def test_no_text_names_the_old_e_mark_as_estias_picture() -> None:
    """Estia's picture is her mark, Hearth (owner, 25 Oct): the docs, the config's comments and the doctor's line say
    "Estia's mark", never the old "Estia's e."."""
    root = BRAND.parent.parent
    names = ["README.md", "GUIDE.md", "setup_assistant.md", "config.example.toml", "THIRD_PARTY_NOTICES.md"]
    names += [str(p.relative_to(root)) for p in (root / "docs" / "agents").glob("*.md")]
    names += ["src/local_assistant/config.py", "src/local_assistant/prompts/doctor.py"]
    for name in names:
        text = (root / name).read_text(encoding="utf-8")
        assert "Estia's e." not in text and "keeps the e." not in text and 'enable4all\'s "e."' not in text, name


def test_only_estias_artwork_is_left() -> None:
    """Owner, 25 Oct: "just the mark, no letters at all": the letter pictures a to z and letters.py are gone."""
    names = sorted(path.name for path in BRAND.iterdir() if path.is_file())
    assert names == [
        f"estia-{name}.{ext}"
        for name in ("avatar", "hero-dark", "hero-light", "logo-dark", "logo-light", "mark")
        for ext in ("png", "svg")
    ]
    assert not (BRAND / "letters").exists()
    scripts = sorted(path.name for path in (BRAND / "source").glob("*.py"))
    assert scripts == ["hero.py", "lockup.py", "mark.py", "palette.py", "render.py", "wordmark.py"]

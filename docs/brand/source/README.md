# Estia's logo and artwork

Estia's mark is Hearth (chosen by the owner, 25 Oct): a ring with a white centre and an orange rounded square, a circle and a square on a 100 x 100 grid. The square is a Mac mini seen from above, like a stone in a Japanese garden (the owner's pick, 28 Oct, in place of the dot): no ripple, nothing of Apple's (no logo, no ports). `../estia-mark.svg` is the file, byte for byte; nothing here redraws it, and the website's drift test compares the same bytes. It holds no font. The mark replaced the enable4all "e." (a lower-case e with an orange full stop). Her Signal picture is Estia's mark, whatever her name: a silver ring around an orange rounded square. The 26 letter pictures are gone (owner, 25 Oct: "just the mark, no letters at all"). The logo and the README artwork are drawn by these scripts:

- `mark.py`: takes the ring and the square from `../estia-mark.svg`; writes the mark as it is and the Signal avatar (the mark centred on full-bleed white at 0.9 of its size, so the ring clears Signal's round crop). Standard library only.
- `wordmark.py`: text as outlines.
- `lockup.py`: the mark beside the name, "estia" in lower case, with the line "A thinking partner living at home".
- `hero.py`: the stone garden whose stone is a Mac mini (unchanged from the first artwork).
- `palette.py`: the colours.

Palette: sumi ink `#1c1a17`, washi paper `#f2ede3`, night `#1b1a18`, white for the avatar, and brushed aluminium `#e4e6e8` → `#979ca2` for the Mac mini. The hero garden (since 15 Oct, the owner's pick: more fun and happy) is a sun-yellow rake `#ffb627` on warm white `#fffdf7`, the rings around the Mac in rainbow colours, enable4all's orange first (`#ff5c35`, `#ffb627`, `#2bb39a`, `#3b82f6`, `#8b5cf6`), a step brighter on the dark garden. The mark keeps the colours of its file: its ring, a white centre (which keeps the mark readable on a dark page) and the orange `#ff5c35` square, the colour of the old "e."'s full stop.

To rebuild them:

1. For the lockup only: put the font [Shippori Mincho B1](https://github.com/fontdasu/ShipporiMincho) SemiBold (SIL Open Font License 1.1) in a `fonts/` folder next to the scripts, named `ofl_shipporiminchob1_ShipporiMinchoB1-SemiBold.ttf`. The font is not included here.
2. Run the scripts:

```sh
uv run --no-project python mark.py                                                         # needs no font
uv run --no-project --with fonttools==4.67.0 --with uharfbuzz==0.56.3 python lockup.py     # also hero.py
uv run --no-project --with resvg-py==0.5.0 python render.py out/*.svg                      # SVG to PNG
```

3. The scripts write to `out/` (not in git). Copy each render to the brand folder with the `estia-` prefix:
   `out/<name>.svg` and `out/<name>.png` become `../estia-<name>.svg` and `../estia-<name>.png`
   (for example `out/hero-light.svg` → `../estia-hero-light.svg`):

```sh
for f in out/*.svg out/*.png; do cp "$f" "../estia-$(basename "$f")"; done
```

A new colour for the mark is a new `../estia-mark.svg` (the same ring and square): copy it in, run the three scripts above, copy the renders as in step 3, and change `HEARTH_MARK_SHA256` in `tests/test_brand.py` to the file's sha256. The architecture drawing's corner mark in `../../images/` is rebuilt from the same file with the drawing's own build.

`tests/test_brand.py` checks that the mark is still the file with the pinned sha256, that `mark.py` and `hero.py` rebuild the committed mark, avatar and hero exactly, that both logos and the architecture drawing's corner hold the mark file's ring and square unchanged, that no file draws the old dot, that the PNGs have their SVGs' sizes, and that the brand folder holds Estia's artwork only (no letter pictures and no script for them).

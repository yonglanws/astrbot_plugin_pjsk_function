# -*- coding: utf-8 -*-
"""Builds the embedded MiSans subset used by every rendered page.

The t2i service renders a detached document, so a font on disk is unreachable
from the page. The face travels inside the HTML as base64. Shipping the full
7.8 MB font per render is wasteful, so this script keeps only the glyphs the
plugin can actually display: every character in the templates, every string
that can reach a page from MasterData, and the punctuation / kana / symbol
ranges the UI relies on.

Run after editing templates or adding new MasterData fields:

    python tests/build_font.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_SOURCE = ROOT / "data" / "MiSans-Medium.ttf"
TEMPLATE_DIR = ROOT / "pjsk" / "templates"
OUT_TTF = ROOT / "res" / "misans-subset.ttf"
OUT_B64 = ROOT / "res" / "misans-subset.b64.txt"

# Printable ASCII, general punctuation, arrows, math, symbols, CJK punctuation,
# kana, and full-width forms. Missing glyphs are ignored by the subsetter.
RANGES = (
    (0x20, 0x7E),
    (0x2000, 0x206F),
    (0x2190, 0x21FF),
    (0x2200, 0x22FF),
    (0x2600, 0x27BF),
    (0x3000, 0x30FF),
    (0xFF00, 0xFFEF),
)

EXTRA = "★☆♪♥✦✧×÷→←↑↓·—…「」『』【】《》〈〉、。，！？：；（）～＋－％影像原版"


def _add(value, chars: set[str]) -> None:
    if isinstance(value, str):
        chars.update(value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _add(item, chars)
    elif isinstance(value, dict):
        for item in value.values():
            _add(item, chars)


def _load(name: str, region: str = ""):
    path = ROOT / "data" / "master" / region / name
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - a missing table is not fatal
        print(f"skip {region + '/' if region else ''}{name}: {exc}")
        return None


def collect_characters() -> set[str]:
    chars: set[str] = set()
    for template in sorted(TEMPLATE_DIR.glob("*.html")):
        chars.update(template.read_text(encoding="utf-8"))

    for start, end in RANGES:
        chars.update(chr(code) for code in range(start, end + 1))
    chars.update(EXTRA)

    characters = _load("../characters.json")
    if characters is None:
        try:
            with (ROOT / "data" / "characters.json").open("r", encoding="utf-8") as handle:
                characters = json.load(handle)
        except Exception:
            characters = []
    for character in characters or []:
        _add(character.get("name"), chars)
        _add(character.get("fullName"), chars)
        _add(character.get("fullNameChinese"), chars)
        _add(character.get("aliases"), chars)

    for region in ("", "cn"):
        for card in _load("cards.json", region) or []:
            _add(card.get("prefix"), chars)
            _add(card.get("cardSkillName"), chars)
            _add(card.get("gachaPhrase"), chars)

        for skill in _load("skills.json", region) or []:
            _add(skill.get("description"), chars)
            _add(skill.get("shortDescription"), chars)

        for music in _load("musics.json", region) or []:
            _add(music.get("title"), chars)
            _add(music.get("pronunciation"), chars)
            _add(music.get("lyricist"), chars)
            _add(music.get("composer"), chars)
            _add(music.get("arranger"), chars)

        for event in _load("events.json", region) or []:
            _add(event.get("name"), chars)

        for gacha in _load("gachas.json", region) or []:
            _add(gacha.get("name"), chars)
            information = gacha.get("gachaInformation") or {}
            _add(information.get("summary"), chars)
            _add(information.get("description"), chars)

        for entry in _load("outsideCharacters.json", region) or []:
            _add(entry.get("name"), chars)

    costumes = _load("moe_costume.json") or {}
    for costume in (costumes.get("costumes", []) if isinstance(costumes, dict) else []):
        _add(costume.get("name"), chars)

    aliases = _load("music_aliases.json")
    entries = aliases.get("musics", []) if isinstance(aliases, dict) else aliases
    for entry in entries or []:
        _add(entry.get("aliases"), chars)

    translations = _load("music_translations.json") or {}
    _add(translations.get("title"), chars)
    return chars


def main() -> int:
    if not FONT_SOURCE.is_file():
        print(f"font not found: {FONT_SOURCE}")
        return 1
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
    except ImportError:
        print("fontTools is required: pip install fonttools")
        return 1

    chars = collect_characters()
    print(f"collecting {len(chars)} characters")
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.notdef_outline = True
    options.recalc_bounds = True
    options.drop_tables += ["DSIG"]

    font = TTFont(str(FONT_SOURCE))
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=[ord(char) for char in chars])
    subsetter.subset(font)
    OUT_TTF.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(OUT_TTF))
    font.close()

    payload = base64.b64encode(OUT_TTF.read_bytes()).decode("ascii")
    OUT_B64.write_text(payload, encoding="ascii")
    size = OUT_TTF.stat().st_size
    print(f"wrote {OUT_TTF} ({size / 1024:.0f} KB)")
    print(f"wrote {OUT_B64} ({len(payload) / 1024:.0f} KB base64)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

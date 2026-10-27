# -*- coding: utf-8 -*-
"""Query parsing and result resolution.

The search layer answers one question: does this text name one item, several
items, or nothing? It is deliberately conservative — an exact alias/title/ID is
a detail page, anything fuzzy returns a short list so the user can pick.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import re
import time
import unicodedata
from dataclasses import dataclass, field

from .constants import (
    DIFFICULTY_WORDS,
    SONG_LIST_LIMIT,
    UNITS,
)

_DIGITS = re.compile(r"^\d+$")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def strip_id(text: str) -> str:
    return normalize(text).lstrip("#＃").strip()


_ATTACHED_DIFFICULTY = re.compile(r"^([a-z]+)(\d.*)$")


def extract_difficulty(text: str, default: str = "master") -> tuple[str, str]:
    """Pulls a difficulty word out of the query.

    Only whole tokens count for the ASCII spellings, so a song such as
    ``Extra Story`` is not mistaken for the EXPERT difficulty. A level may be
    attached to the spelling (``ma32``, ``ex30-32``). Chinese words (``专家``)
    are unambiguous and may appear anywhere.
    """
    if not text:
        return default, ""
    tokens = text.split()
    for index in range(len(tokens) - 1, -1, -1):
        lowered = tokens[index].lower()
        key = DIFFICULTY_WORDS.get(lowered)
        attached = ""
        if not key:
            match = _ATTACHED_DIFFICULTY.match(lowered)
            if match and match.group(1) in DIFFICULTY_WORDS:
                key = DIFFICULTY_WORDS[match.group(1)]
                attached = match.group(2)
        if not key:
            continue
        # A difficulty word only counts at the end ("Song EX", "ma32") or
        # directly before a level ("MASTER 32"), so "Master of Puppets" keeps
        # its title.
        followed_by_level = index + 1 < len(tokens) and tokens[index + 1].isdigit()
        if index == len(tokens) - 1 or followed_by_level:
            replacement = [attached] if attached else []
            remaining = tokens[:index] + replacement + tokens[index + 1:]
            return key, " ".join(remaining).strip()
    for word in sorted((w for w in DIFFICULTY_WORDS if not w.isascii()), key=len, reverse=True):
        if word in text:
            return DIFFICULTY_WORDS[word], text.replace(word, "", 1).strip()
    return default, text


_CONSTANT_RANGE = re.compile(
    r"(\d{1,2}(?:\.\d{1,2})?)(?:\s*(?:-|~|～|—)\s*(\d{1,2}(?:\.\d{1,2})?)|\s+(\d{1,2}(?:\.\d{1,2})?))?"
)


def parse_constant_range(text: str) -> tuple[float | None, float | None]:
    """Reads a difficulty-constant range from a query.

    ``25`` keeps the whole 25.x tier (25.0–25.99), ``14.5-15.5`` and
    ``14.5 15.5`` are inclusive ranges, and a decimal such as ``25.5``
    matches that exact constant. No number at all returns (None, None).
    """
    match = _CONSTANT_RANGE.search(text)
    if match is None:
        return None, None
    low = float(match.group(1))
    second = match.group(2) or match.group(3)
    if second is not None:
        low, high = low, float(second)
    elif low == int(low):
        low, high = low, low + 0.99
    else:
        low, high = low, low
    if low > high:
        low, high = high, low
    return low, high


def filter_songs_by_level(
    dataset,
    difficulty: str,
    low: int | None,
    high: int | None,
) -> list[tuple[int, dict]]:
    """Official ``playLevel`` matches, highest level first then by title.

    The table comes from MasterData's musicDifficulties only; the community
    decimal-constant file is deliberately not consulted.
    """
    entries: list[tuple[int, dict]] = []
    for music in dataset.musics:
        entry = dataset.diffs_by_music.get(int(music.get("id", 0) or 0), {}).get(difficulty)
        if not entry:
            continue
        level = int(entry.get("playLevel", 0) or 0)
        if low is not None and not (low <= level <= high):
            continue
        entries.append((level, music))
    entries.sort(key=lambda pair: (-pair[0], str(pair[1].get("title", ""))))
    return entries


@dataclass
class Resolved:
    mode: str = "list"  # detail | list | empty
    item: dict | None = None
    items: list = field(default_factory=list)
    total: int = 0
    query: str = ""
    exact: bool = False
    error: str = ""
    suggestions: list = field(default_factory=list)


def _similarity(query: str, candidate: str) -> float:
    if not candidate:
        return 0.0
    if query == candidate:
        return 2.0
    if candidate.startswith(query):
        return 1.6
    if query in candidate:
        return 1.2 + len(query) / max(len(candidate), 1) * 0.3
    return difflib.SequenceMatcher(None, query, candidate).ratio()


def _rank_songs(dataset, query: str) -> list[tuple[float, dict]]:
    lowered = query.lower()
    scored: list[tuple[float, dict]] = []
    for music in dataset.musics:
        title = music.get("title", "") or ""
        pronunciation = music.get("pronunciation", "") or ""
        translated = dataset.song_titles.get(title, "") or ""
        candidates = [title, pronunciation, translated]
        candidates.extend(dataset.aliases_by_music.get(int(music.get("id", 0)), []))
        best = 0.0
        for candidate in candidates:
            if not candidate:
                continue
            best = max(best, _similarity(lowered, candidate.lower()))
        if best >= 0.58:
            scored.append((best, music))
    scored.sort(key=lambda entry: (-entry[0], len(entry[1].get("title", "")), entry[1].get("id", 0)))
    return scored


def _ban_events_for_character(dataset, character_id: int) -> list[dict]:
    """Every event headlined by one character, oldest first.

    An event is a "ban" event when it has a commissioned song, or when it is a
    recent marathon/carnival whose deck bonuses all belong to one unit.
    """
    event_ids = set(dataset.event_to_music)
    now = int(time.time() * 1000)
    for event in dataset.events:
        event_id = int(event.get("id", 0))
        if event_id in event_ids:
            continue
        if event.get("eventType") not in ("marathon", "cheerful_carnival"):
            continue
        if int(event.get("startAt", 0) or 0) < now - 180 * 86400 * 1000:
            continue
        units = set()
        for entry in dataset.event_deck_bonuses_by_event.get(event_id, []):
            unit_id = entry.get("gameCharacterUnitId")
            if not unit_id or int(unit_id) > 20:
                continue
            character = dataset.character_id_by_unit.get(int(unit_id))
            if character is not None:
                units.add(dataset.unit_by_character.get(character, ""))
        if len(units) == 1:
            event_ids.add(event_id)

    result = []
    for event_id in event_ids:
        event = dataset.events_by_id.get(event_id)
        if not event:
            continue
        candidates = []
        for entry in dataset.event_cards_by_event.get(event_id, []):
            card = dataset.cards_by_id.get(entry.get("cardId"))
            if not card:
                continue
            if dataset.supply_type(card) == "colorful_festival_limited":
                continue
            candidates.append(int(card["id"]))
        if not candidates:
            continue
        banner = dataset.cards_by_id.get(min(candidates))
        if banner and banner.get("characterId") == character_id:
            result.append(event)
    result.sort(key=lambda event: int(event.get("startAt", 0) or 0))
    return result


def search_songs(dataset, query: str, limit: int = SONG_LIST_LIMIT) -> Resolved:
    query = strip_id(query)
    if not query:
        return Resolved(mode="empty", query="", error="请输入歌曲名或 ID")

    if query.isdigit():
        music = dataset.musics_by_id.get(int(query))
        if music:
            return Resolved(mode="detail", item=music, total=1, query=query, exact=True)
        return Resolved(mode="empty", query=query, error=f"未找到歌曲 ID {query}")

    lowered = query.lower()
    # id123
    if lowered.startswith("id") and lowered[2:].strip().isdigit():
        music_id = int(lowered[2:].strip())
        music = dataset.musics_by_id.get(music_id)
        if music:
            return Resolved(mode="detail", item=music, total=1, query=query, exact=True)
        return Resolved(mode="empty", query=query, error=f"未找到歌曲 ID {music_id}")

    # negative index: -1 = newest published song
    if query.lstrip("-").isdigit() and query.startswith("-"):
        index = int(query)
        if not dataset.musics_sorted:
            return Resolved(mode="empty", query=query, error="歌曲数据为空")
        if abs(index) > len(dataset.musics_sorted):
            return Resolved(mode="empty", query=query, error=f"索引超出范围（共 {len(dataset.musics_sorted)} 首已发布歌曲）")
        return Resolved(mode="detail", item=dataset.musics_sorted[index], total=1, query=query, exact=True)

    # event123
    if lowered.startswith("event") and lowered[5:].strip().isdigit():
        event_id = int(lowered[5:].strip())
        music_id = dataset.event_to_music.get(event_id)
        if not music_id:
            return Resolved(mode="empty", query=query, error=f"活动 {event_id} 没有书下曲")
        music = dataset.musics_by_id.get(music_id)
        if music:
            return Resolved(mode="detail", item=music, total=1, query=query, exact=True)

    # character alias + sequence: mnr1 = the first event headlined by Minori
    for alias, character_id in sorted(dataset.alias_to_character.items(), key=lambda entry: len(entry[0]), reverse=True):
        if not alias or len(alias) > 12:
            continue
        for index in range(1, 10):
            token = f"{alias}{index}"
            # The sequence number must not run into a longer number (mnr10).
            pattern = (r"(?<![a-z0-9])" if alias.isascii() else "") + re.escape(token) + r"(?!\d)"
            if re.search(pattern, lowered):
                events = _ban_events_for_character(dataset, character_id)
                if index > len(events):
                    return Resolved(mode="empty", query=query, error=f"该角色只有 {len(events)} 次箱活")
                event = events[index - 1]
                music_id = dataset.event_to_music.get(int(event.get("id", 0)))
                if not music_id:
                    return Resolved(mode="empty", query=query, error="该活动书下曲尚未上线")
                music = dataset.musics_by_id.get(music_id)
                if music:
                    return Resolved(mode="detail", item=music, total=1, query=query, exact=True)

    # alias exact
    music_id = dataset.alias_to_music.get(lowered)
    if music_id and music_id in dataset.musics_by_id:
        return Resolved(mode="detail", item=dataset.musics_by_id[music_id], total=1, query=query, exact=True)

    # title / pronunciation / translated title exact
    for music in dataset.musics:
        title = (music.get("title") or "").lower()
        pronunciation = (music.get("pronunciation") or "").lower()
        translated = (dataset.song_titles.get(music.get("title", ""), "") or "").lower()
        if lowered in (title, pronunciation, translated):
            return Resolved(mode="detail", item=music, total=1, query=query, exact=True)

    scored = _rank_songs(dataset, lowered)
    if not scored:
        return Resolved(mode="empty", query=query, error=f"未找到与「{query}」匹配的歌曲")
    top_score, top_music = scored[0]
    if top_score >= 1.0 and (len(scored) == 1 or scored[1][0] < top_score - 0.2):
        return Resolved(mode="detail", item=top_music, total=1, query=query, exact=True)

    items = [music for _, music in scored[:limit]]
    suggestions = [music.get("title", "") for _, music in scored[:5]]
    return Resolved(mode="list", items=items, total=len(scored), query=query, suggestions=suggestions)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

ATTR_WORDS = {
    "cool": "cool", "蓝": "cool", "蓝星": "cool", "帅气": "cool",
    "happy": "happy", "橙": "happy", "橙心": "happy", "快乐": "happy", "黄": "happy",
    "mysterious": "mysterious", "紫": "mysterious", "紫月": "mysterious", "神秘": "mysterious",
    "cute": "cute", "粉": "cute", "粉花": "cute", "可爱": "cute",
    "pure": "pure", "绿": "pure", "绿草": "pure", "纯洁": "pure",
}

RARITY_WORDS = {
    "1星": "rarity_1", "一星": "rarity_1", "1x": "rarity_1",
    "2星": "rarity_2", "二星": "rarity_2", "两星": "rarity_2", "2x": "rarity_2",
    "3星": "rarity_3", "三星": "rarity_3", "3x": "rarity_3",
    "4星": "rarity_4", "四星": "rarity_4", "4x": "rarity_4",
    "生日": "rarity_birthday", "生日卡": "rarity_birthday", "bd": "rarity_birthday",
}

SUPPLY_WORDS = {
    "bfes": "bloom_festival_limited", "bf": "bloom_festival_limited", "bfes限": "bloom_festival_limited",
    "cfes": "colorful_festival_limited", "cf": "colorful_festival_limited", "cfes限": "colorful_festival_limited",
    "fes": "festival_limited", "fes限": "festival_limited",
    "非限": "not_limited", "非限定": "not_limited", "常驻": "not_limited",
    "期间限定": "term_limited", "期间": "term_limited",
    "联动": "collaboration_limited", "联动限定": "collaboration_limited",
    "wl": "unit_event_limited", "worldlink": "unit_event_limited",
    "限定": "all_limited", "限": "all_limited",
}

SKILL_WORDS = {"奶": "life_recovery", "奶卡": "life_recovery", "分": "score_up", "分卡": "score_up", "判": "judgment_up", "判卡": "judgment_up"}

# Community names for specific skill IDs (skillId → keyword). They must be
# parsed before SKILL_WORDS because each one contains a bare 分/判 that would
# otherwise be consumed as the generic skill filter.
SKILL_ID_WORDS: dict[str, list[int]] = {
    "大分": [4],
    "p分": [11],
    "判分": [13],
    "血分": [12],
    "组分": [15, 16, 17, 18, 19],
    "团分": [15, 16, 17, 18, 19],
}

# Reverse lookup for the list-page title; later keys win, so 团分 is the
# preferred label for the shared 15–19 group.
SKILL_ID_LABELS = {tuple(ids): word for word, ids in SKILL_ID_WORDS.items()}

UNIT_WORDS = {
    "light_sound": ("light_sound", "ln", "ln团"),
    "idol": ("idol", "mmj", "mmj团"),
    "street": ("street", "vbs", "vbs团"),
    "theme_park": ("theme_park", "ws", "ws团", "wxs"),
    "school_refusal": ("school_refusal", "25h", "25时", "25"),
    "piapro": ("piapro", "vs", "v"),
}

CHARACTER_UNIT = {}
for _start, _unit in ((1, "light_sound"), (5, "idol"), (9, "street"), (13, "theme_park"), (17, "school_refusal"), (21, "piapro")):
    for _offset in range(4 if _unit != "piapro" else 6):
        CHARACTER_UNIT[_start + _offset] = _unit

_UNIT_ALIASES: dict[str, str] = {
    word.lower(): unit for unit, words in UNIT_WORDS.items() for word in words
}
_UNIT_TOKEN = re.compile(r"[a-z0-9_]+")


def _matching_units(text: str) -> set[str]:
    """Unit keys named by a query: ln/mmj/vbs/ws/25h plus full labels."""
    matched = set()
    for token in _UNIT_TOKEN.findall(text):
        unit = _UNIT_ALIASES.get(token)
        if unit:
            matched.add(unit)
    for alias, unit in _UNIT_ALIASES.items():
        if not alias.isascii() and alias in text:
            matched.add(unit)
    for unit, info in UNITS.items():
        label = str(info.get("label", "")).lower()
        if label and label in text:
            matched.add(unit)
    return matched


def _extract_word(text: str, mapping: dict) -> tuple[object, str]:
    for word in sorted(mapping, key=len, reverse=True):
        if word in text:
            return mapping[word], text.replace(word, "", 1).strip()
    return None, text


def _extract_unit(text: str) -> tuple[str | None, str, str | None]:
    """Returns ``(unit, remaining, mode)`` where mode is None/oc/vs.

    ASCII unit words are matched as whole tokens so the single-letter ``v``
    alias cannot fire inside an English word such as ``love``.
    """
    tokens = text.split()
    for index, token in enumerate(tokens):
        lowered = token.lower()
        for unit, words in UNIT_WORDS.items():
            for word in words:
                lowered_word = word.lower()
                if lowered == lowered_word:
                    # Bare ``vs``/``v`` means the pure virtual-singer cards
                    # (no supported unit); ``lnv`` keeps the 团VS semantics.
                    mode = "vs" if unit == "piapro" and word in ("vs", "v") else None
                    return unit, " ".join(tokens[:index] + tokens[index + 1:]).strip(), mode
                if lowered == lowered_word + "oc":
                    return unit, " ".join(tokens[:index] + tokens[index + 1:]).strip(), "oc"
                if lowered == lowered_word + "v":
                    return unit, " ".join(tokens[:index] + tokens[index + 1:]).strip(), "vs"
                if token == "纯" + word:
                    return unit, " ".join(tokens[:index] + tokens[index + 1:]).strip(), "oc"
    for unit, words in UNIT_WORDS.items():
        for word in words:
            if not word.isascii() and word in text:
                return unit, text.replace(word, "", 1).strip(), None
    return None, text, None


def _extract_year(text: str) -> tuple[int | None, str]:
    now = _dt.datetime.now().year
    for word, year in (("明年", now + 1), ("今年", now), ("去年", now - 1), ("前年", now - 2)):
        if word in text:
            return year, text.replace(word, "", 1).strip()
    for year in range(now, 2019, -1):
        for token in (f"{year}年", f"{str(year)[-2:]}年"):
            if token in text:
                return year, text.replace(token, "", 1).strip()
    return None, text


def _extract_event(dataset, text: str) -> tuple[int | None, str, str]:
    """``event123`` or a 箱活 abbreviation (``mnr1``) → ``(event_id, rest, error)``.

    ``mnr1`` is the first event headlined by that character, mirroring the
    song/event commands. ``error`` is set when the index is out of range or
    the event id does not exist, so the caller can reject instead of silently
    returning every card.
    """
    match = re.search(r"event(\d+)", text)
    if match:
        event_id = int(match.group(1))
        rest = text.replace(match.group(0), "", 1).strip()
        if event_id not in dataset.events_by_id:
            return None, rest, f"未找到活动 ID {event_id}"
        return event_id, rest, ""
    for alias, character_id in sorted(dataset.alias_to_character.items(), key=lambda entry: len(entry[0]), reverse=True):
        if not alias or len(alias) > 12:
            continue
        for index in range(1, 10):
            token = f"{alias}{index}"
            # The index must not run into a longer number (mnr10).
            pattern = (r"(?<![a-z0-9])" if alias.isascii() else "") + re.escape(token) + r"(?!\d)"
            if not re.search(pattern, text):
                continue
            rest = re.sub(pattern, "", text, count=1).strip()
            events = _ban_events_for_character(dataset, character_id)
            if index > len(events):
                if not events:
                    return None, rest, "该角色没有箱活"
                return None, rest, f"该角色只有 {len(events)} 次箱活"
            return int(events[index - 1].get("id", 0)), rest, ""
    return None, text, ""


def parse_card_filters(dataset, text: str) -> dict:
    text = normalize(text).lower()
    filters = {
        "year": None, "attr": None, "supply": None, "skill": None,
        "skill_ids": None, "skill_label": "", "leak": False,
        "event_id": None, "event_error": "",
        "unit": None, "unit_mode": None, "rarity": None,
        "character_id": None, "keyword": "",
    }
    filters["year"], text = _extract_year(text)
    filters["attr"], text = _extract_word(text, ATTR_WORDS)
    filters["supply"], text = _extract_word(text, SUPPLY_WORDS)
    filters["skill_ids"], text = _extract_word(text, SKILL_ID_WORDS)
    if filters["skill_ids"]:
        filters["skill_label"] = SKILL_ID_LABELS.get(tuple(filters["skill_ids"]), "")
    filters["skill"], text = _extract_word(text, SKILL_WORDS)
    if "leak" in text:
        filters["leak"] = True
        text = text.replace("leak", "", 1).strip()
    filters["event_id"], text, filters["event_error"] = _extract_event(dataset, text)
    unit, text, mode = _extract_unit(text)
    filters["unit"], filters["unit_mode"] = unit, mode
    filters["rarity"], text = _extract_word(text, RARITY_WORDS)

    for alias, character_id in sorted(dataset.alias_to_character.items(), key=lambda entry: len(entry[0]), reverse=True):
        if not alias:
            continue
        # A one-character Chinese alias or a short romaji alias must stand on
        # its own; otherwise "一" matches half the card titles.
        if (len(alias) == 1 or (alias.isascii() and len(alias) <= 4)) and alias not in text.split():
            continue
        if alias in text:
            filters["character_id"] = character_id
            text = text.replace(alias, "", 1).strip()
            break
    # A bare digit after the character name is the rarity ("miku 4").
    if not filters["rarity"] and text.strip().isdigit():
        number = int(text.strip())
        if 1 <= number <= 4:
            filters["rarity"] = f"rarity_{number}"
            text = ""
    filters["keyword"] = text.strip()
    return filters


def filter_cards(dataset, filters: dict) -> list[dict]:
    supply = filters.get("supply")
    supply_set = None
    if supply == "festival_limited":
        supply_set = {"bloom_festival_limited", "colorful_festival_limited"}
    elif supply == "all_limited":
        supply_set = {
            "bloom_festival_limited", "colorful_festival_limited", "term_limited",
            "collaboration_limited", "unit_event_limited",
        }
    elif supply == "not_limited":
        supply_set = {"normal"}
    elif supply:
        supply_set = {supply}

    event_id = filters.get("event_id")
    event_card_ids = None
    if event_id is not None:
        event_card_ids = {
            int(entry.get("cardId", 0))
            for entry in dataset.event_cards_by_event.get(int(event_id), [])
        }
    skill_ids = filters.get("skill_ids")
    leak_only = filters.get("leak")
    now_ms = int(time.time() * 1000)

    result = []
    for card in dataset.cards:
        release_at = int(card.get("releaseAt", 0) or 0)
        if event_card_ids is not None and int(card.get("id", 0)) not in event_card_ids:
            continue
        if skill_ids and int(card.get("skillId", 0)) not in skill_ids:
            continue
        if leak_only and release_at <= now_ms:
            continue
        if filters.get("attr") and card.get("attr") != filters["attr"]:
            continue
        if supply_set is not None and dataset.supply_type(card) not in supply_set:
            continue
        if filters.get("skill") and dataset.skill_type(card) != filters["skill"]:
            continue
        if filters.get("rarity") and card.get("cardRarityType") != filters["rarity"]:
            continue
        if filters.get("character_id") and int(card.get("characterId", 0)) != filters["character_id"]:
            continue
        unit = CHARACTER_UNIT.get(int(card.get("characterId", 0)), "")
        support = card.get("supportUnit", "none")
        if filters.get("unit"):
            target = filters["unit"]
            if filters.get("unit_mode") == "vs":
                if unit != "piapro":
                    continue
                # Bare vs/v keeps only pure virtual-singer cards; ``lnv``
                # keeps the virtual-singer versions supporting that unit.
                if support != ("none" if target == "piapro" else target):
                    continue
            elif filters.get("unit_mode") == "oc":
                if unit != target or support != "none":
                    continue
            elif target not in (unit, support):
                continue
        if filters.get("year"):
            try:
                release_year = _dt.datetime.fromtimestamp(release_at / 1000).year
            except (ValueError, OSError):
                continue
            if release_year != filters["year"]:
                continue
        if filters.get("keyword"):
            haystack = f"{card.get('prefix', '')} {card.get('cardSkillName', '')}".lower()
            if filters["keyword"] not in haystack:
                continue
        result.append(card)

    result.sort(key=lambda card: (int(card.get("releaseAt", 0) or 0), int(card.get("id", 0) or 0)), reverse=True)
    return result


_CHARACTER_CARD_INDEX = re.compile(r"^(.+)[-／/](\d+)$")


def find_card_by_character_index(dataset, query: str) -> tuple[dict | None, str]:
    """``miku-1`` / ``miku/1`` → the character's newest card, ``-2`` the second newest."""
    match = _CHARACTER_CARD_INDEX.match(query.strip())
    if not match:
        return None, ""
    character_id = dataset.alias_to_character.get(match.group(1).lower())
    if character_id is None:
        return None, ""
    cards = sorted(
        (card for card in dataset.cards if int(card.get("characterId", 0)) == character_id),
        key=lambda card: (int(card.get("releaseAt", 0) or 0), int(card.get("id", 0) or 0)),
    )
    index = int(match.group(2))
    if index < 1 or index > len(cards):
        return None, f"该角色只有 {len(cards)} 张卡牌"
    return cards[-index], ""


def search_cards(dataset, query: str) -> Resolved:
    query = strip_id(query)
    if query.isdigit():
        card = dataset.cards_by_id.get(int(query))
        if card:
            return Resolved(mode="detail", item=card, total=1, query=query, exact=True)
        return Resolved(mode="empty", query=query, error=f"未找到卡牌 ID {query}")
    card, error = find_card_by_character_index(dataset, query)
    if error:
        return Resolved(mode="empty", query=query, error=error)
    if card is not None:
        return Resolved(mode="detail", item=card, total=1, query=query, exact=True)
    filters = parse_card_filters(dataset, query)
    cards = filter_cards(dataset, filters)
    if not cards:
        return Resolved(mode="empty", query=query, error="没有找到符合条件的卡牌")
    if len(cards) == 1:
        return Resolved(mode="detail", item=cards[0], total=1, query=query, exact=True)
    return Resolved(mode="list", items=cards, total=len(cards), query=query, suggestions=[card.get("prefix", "") for card in cards[:5]])


# ---------------------------------------------------------------------------
# Gachas / events
# ---------------------------------------------------------------------------


def resolve_gacha_default(dataset) -> dict | None:
    return dataset.current_gacha or (dataset.gachas[-1] if dataset.gachas else None)


def search_gachas(dataset, query: str) -> Resolved:
    query = strip_id(query)
    if not query:
        gacha = resolve_gacha_default(dataset)
        if gacha:
            return Resolved(mode="detail", item=gacha, total=1, query="", exact=True)
        return Resolved(mode="empty", query="", error="暂无卡池数据")
    if query.isdigit():
        gacha = dataset.gachas_by_id.get(int(query))
        if gacha:
            return Resolved(mode="detail", item=gacha, total=1, query=query, exact=True)
        return Resolved(mode="empty", query=query, error=f"未找到卡池 ID {query}")

    lowered = query.lower()
    scored: list[tuple[float, dict]] = []
    for gacha in dataset.gachas:
        name = (gacha.get("name") or "").lower()
        score = _similarity(lowered, name)
        for pickup in gacha.get("gachaPickups", []) or []:
            card = dataset.cards_by_id.get(pickup.get("cardId"))
            if not card:
                continue
            score = max(score, _similarity(lowered, (card.get("prefix") or "").lower()))
            character = dataset.characters_by_id.get(card.get("characterId"), {})
            for alias in [character.get("name", ""), character.get("fullNameChinese", "")] + list(character.get("aliases", [])):
                if alias:
                    score = max(score, _similarity(lowered, str(alias).lower()))
        if score >= 0.58:
            scored.append((score, gacha))
    scored.sort(key=lambda entry: (-entry[0], -int(entry[1].get("id", 0))))
    if not scored:
        return Resolved(mode="empty", query=query, error=f"未找到与「{query}」匹配的卡池")
    top_score, top_gacha = scored[0]
    if top_score >= 1.6 or (top_score >= 1.0 and (len(scored) == 1 or scored[1][0] < top_score - 0.2)):
        return Resolved(mode="detail", item=top_gacha, total=1, query=query, exact=True)
    return Resolved(mode="list", items=[gacha for _, gacha in scored[:30]], total=len(scored), query=query)


def resolve_event_default(dataset) -> Resolved:
    now = int(time.time() * 1000)
    ongoing = [
        event for event in dataset.events
        if int(event.get("startAt", 0) or 0) <= now <= int(event.get("aggregateAt", 0) or 0)
    ]
    if ongoing:
        return Resolved(
            mode="detail",
            item=max(ongoing, key=lambda event: int(event.get("startAt", 0) or 0)),
            total=1,
            exact=True,
        )
    upcoming = sorted(
        (event for event in dataset.events if int(event.get("startAt", 0) or 0) > now),
        key=lambda event: int(event.get("startAt", 0) or 0),
    )
    if upcoming:
        return Resolved(mode="detail", item=upcoming[0], total=1, exact=True)
    latest = max(dataset.events, key=lambda event: int(event.get("startAt", 0) or 0), default=None)
    if latest:
        return Resolved(mode="detail", item=latest, total=1, exact=True)
    return Resolved(mode="empty", error="暂无活动数据")


def search_events(dataset, query: str) -> Resolved:
    query = strip_id(query)
    if not query:
        return resolve_event_default(dataset)
    if query.isdigit():
        event = dataset.events_by_id.get(int(query))
        if event:
            return Resolved(mode="detail", item=event, total=1, query=query, exact=True)
        return Resolved(mode="empty", query=query, error=f"未找到活动 ID {query}")
    lowered = query.lower()
    unit_targets = _matching_units(lowered)
    scored = []
    for event in dataset.events:
        name = (event.get("name") or "").lower()
        score = _similarity(lowered, name)
        if event.get("unit") in unit_targets:
            score = max(score, 0.9)
        if score >= 0.58:
            scored.append((score, event))
    scored.sort(key=lambda entry: (-entry[0], -int(entry[1].get("startAt", 0) or 0)))
    if not scored:
        return Resolved(mode="empty", query=query, error=f"未找到与「{query}」匹配的活动")
    top_score, top_event = scored[0]
    if top_score >= 1.0 and (len(scored) == 1 or scored[1][0] < top_score - 0.2):
        return Resolved(mode="detail", item=top_event, total=1, query=query, exact=True)
    return Resolved(mode="list", items=[event for _, event in scored[:30]], total=len(scored), query=query)


# ---------------------------------------------------------------------------
# Gacha simulation arguments
# ---------------------------------------------------------------------------


def parse_draw_args(dataset, text: str, default_count: int = 10) -> tuple[int, int | None, int | None, int]:
    """``(count, character_id, gacha_id, requested_count)``.

    Accepts ``100连``, ``50连 123``, ``emu 123``, ``十连 emu 123``.
    """
    text = normalize(text)
    requested = default_count
    match = re.match(r"^(\d+)\s*(连|抽|連)(.*)$", text)
    if match:
        requested = max(1, int(match.group(1)))
        text = match.group(3).strip()
    count = min(100, requested)

    character_id = None
    gacha_id = None
    parts = text.split()
    if parts:
        first = parts[0].lower()
        character_id = dataset.alias_to_character.get(first)
        if character_id is not None:
            remainder = " ".join(parts[1:]).strip()
            if remainder.isdigit():
                gacha_id = int(remainder)
        elif text.strip().isdigit():
            gacha_id = int(text.strip())
        else:
            # A gacha name is also accepted, but the caller resolves it.
            gacha_id = None
    return count, character_id, gacha_id, requested

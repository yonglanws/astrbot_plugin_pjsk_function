# -*- coding: utf-8 -*-
"""View models built from MasterData.

Templates never touch raw MasterData rows: every page receives small, already
formatted dictionaries with display labels, colours and image URLs. That keeps
the templates readable and means a data-shape change is fixed in one place.
"""

from __future__ import annotations

import datetime as _dt
import re
import time

from .constants import (
    ATTRIBUTES,
    DIFFICULTY_COLORS,
    DIFFICULTY_LABELS,
    DIFFICULTY_ORDER,
    DIFFICULTY_SHORT,
    EVENT_TYPES,
    MUSIC_CATEGORIES,
    RARITIES,
    SKILL_TYPES,
    SUPPLY_TYPES,
    UNITS,
    card_art_url,
    card_thumb_url,
    char_icon_url,
    costume_thumb_url,
    event_banner_url,
    gacha_banner_url,
    jacket_thumb_url,
    jacket_url,
)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_timestamp(value, with_time: bool = True) -> str:
    try:
        stamp = float(value or 0) / 1000.0
        if stamp <= 0:
            return "—"
        moment = _dt.datetime.fromtimestamp(stamp)
    except (TypeError, ValueError, OverflowError, OSError):
        return "—"
    return moment.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def format_duration(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return "—"
    if total <= 0:
        return "—"
    return f"{total // 60}:{total % 60:02d}"


def _number(value) -> str:
    """``190.0`` -> ``190``; keeps a genuine fraction."""
    try:
        rounded = round(float(value), 2)
    except (TypeError, ValueError):
        return str(value)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:g}"


def _days_since(value) -> float:
    try:
        return (time.time() * 1000 - float(value or 0)) / 86_400_000
    except (TypeError, ValueError):
        return 9999


def is_new(release_ts, days: int = 14) -> bool:
    return 0 <= _days_since(release_ts) <= days


def is_unreleased(release_ts) -> bool:
    """MasterData rows can carry future release dates (leak cards)."""
    return _days_since(release_ts) < 0


# ---------------------------------------------------------------------------
# Small lookups
# ---------------------------------------------------------------------------


def attr_view(attr: str) -> dict:
    info = ATTRIBUTES.get(attr, {})
    return {
        "key": attr or "",
        "label": info.get("label", attr or "—"),
        "color": info.get("color", "#8A939E"),
        "icon": info.get("icon", ""),
    }


def rarity_view(rarity_type: str) -> dict:
    info = RARITIES.get(rarity_type, {})
    return {
        "key": rarity_type or "",
        "label": info.get("label", "?"),
        "num": int(info.get("num", 1)),
        "color": info.get("color", "#9AA3AD"),
        "is_birthday": rarity_type == "rarity_birthday",
    }


def unit_view(unit_key: str) -> dict:
    info = UNITS.get(unit_key, {})
    return {
        "key": unit_key or "",
        "label": info.get("label", "其他"),
        "short": info.get("short", ""),
        "color": info.get("color", "#8A939E"),
    }


def supply_view(supply_type: str) -> dict:
    info = SUPPLY_TYPES.get(supply_type, {})
    return {
        "key": supply_type or "",
        "label": info.get("label", supply_type or "常驻"),
        "tag": info.get("tag", ""),
    }


def difficulty_view(key: str, level=0, notes=0, constant=None) -> dict:
    color = DIFFICULTY_COLORS.get(key, "#8A939E")
    return {
        "key": key,
        "label": DIFFICULTY_LABELS.get(key, key.upper()),
        "short": DIFFICULTY_SHORT.get(key, key[:2].upper()),
        "level": int(level or 0),
        "notes": int(notes or 0),
        "constant": constant,
        "constant_label": f"{constant:.1f}" if constant is not None else "",
        "color": color,
        "track": min(100, max(4, int((int(level or 0) / 36) * 100))),
    }


def character_view(dataset, character_id: int) -> dict:
    raw = dataset.characters_by_id.get(character_id, {})
    unit_key = dataset.unit_by_character.get(character_id, "")
    unit = unit_view(unit_key)
    return {
        "id": character_id,
        "name": raw.get("name", ""),
        "full_name": raw.get("fullNameChinese") or raw.get("fullName") or f"角色 {character_id}",
        "aliases": list(raw.get("aliases", []) or []),
        "unit": unit["key"],
        "unit_label": unit["short"] or unit["label"],
        "unit_color": unit["color"],
        "icon_url": char_icon_url(character_id),
    }


# ---------------------------------------------------------------------------
# Skill text
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}")


def _skill_effects(skill: dict) -> dict[int, dict]:
    effects: dict[int, dict] = {}
    for effect in skill.get("skillEffects", []) or []:
        eid = effect.get("id")
        if eid is None:
            continue
        details = sorted(
            effect.get("skillEffectDetails", []) or [],
            key=lambda detail: int(detail.get("level", 0) or 0),
        )
        effects[int(eid)] = {
            "type": effect.get("skillEffectType", ""),
            "durations": [detail.get("activateEffectDuration") for detail in details],
            "values": [detail.get("activateEffectValue") for detail in details],
            "values2": [detail.get("activateEffectValue2") for detail in details],
            "enhance": (effect.get("skillEnhance") or {}).get("activateEffectValue"),
        }
    return effects


def _pick(values: list, index: int | None, single: bool):
    if not values:
        return None
    if single:
        # A summary line shows the max-level value, not a "60/65/70/80" join.
        for value in reversed(values):
            if value is not None:
                return value
        return None
    if index is None:
        return values[0] if values else None
    if index < len(values):
        return values[index]
    return values[-1]


def _format_one(description: str, effects: dict[int, dict], character_name: str, index: int | None, single: bool) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        try:
            id_part, op = key.split(";")
            ids = [int(part) for part in id_part.split(",")]
        except (ValueError, TypeError):
            return "?"

        if len(ids) == 1:
            effect = effects.get(ids[0])
            if effect is None:
                return "?"
            if op == "d":
                value = _pick(effect["durations"], index, single)
            elif op == "v":
                value = _pick(effect["values"], index, single)
            elif op == "e":
                value = effect.get("enhance")
            elif op == "m":
                # "（最多{{id;m}}%）" caps the *bonus* from the sub-unit effect:
                # up to four teammates plus the all-same-unit bonus (5 × value).
                enhance = effect.get("enhance") or 0
                value = _number(float(enhance) * 5)
            elif op == "c":
                value = character_name or "?"
            else:
                value = "?"
            if value is None:
                return "?"
            return _number(value) if isinstance(value, (int, float)) else str(value)

        if len(ids) == 2:
            left, right = effects.get(ids[0]), effects.get(ids[1])
            if left is None or right is None:
                return "?"
            first = _pick(left["values"], index, single)
            # ";o" is the combined total: the first effect's value plus the
            # second effect's second value (the reference-rate result).
            second_values = right["values2"] if op == "o" and right["values2"] else right["values"]
            second = _pick(second_values, index, single)
            if first is None or second is None:
                return "?"
            if isinstance(first, str) or isinstance(second, str):
                return "?"
            return _number(float(first) + float(second))

        return "?"

    return _PLACEHOLDER.sub(replace, description or "").replace("  ", " ").strip()


def format_skill_description(description: str, skill: dict | None, character_name: str = "") -> str:
    """Expands the game's ``{{id;op}}`` placeholders into one readable line."""
    if not description or not skill:
        return description or ""
    effects = _skill_effects(skill)
    return _format_one(description, effects, character_name, None, True)


def format_skill_levels(description: str, skill: dict | None, character_name: str = "") -> list[dict]:
    """One description per skill level (the game stores four levels per skill)."""
    if not description or not skill:
        return []
    effects = _skill_effects(skill)
    level_count = max(
        (max(len(effect["values"]), len(effect["durations"])) for effect in effects.values()),
        default=0,
    )
    if level_count <= 0:
        return []
    return [
        {
            "level": index + 1,
            "description": _format_one(description, effects, character_name, index, False),
        }
        for index in range(level_count)
    ]


def skill_view(dataset, card: dict, character_name: str = "") -> dict | None:
    skill = dataset.skills_by_id.get(card.get("skillId", 0))
    if not skill:
        return None
    effect_type = dataset.skill_category(skill)
    info = SKILL_TYPES.get(effect_type, {})
    description = skill.get("description") or skill.get("shortDescription") or ""
    levels = format_skill_levels(description, skill, character_name)
    return {
        "id": skill.get("id", 0),
        "name": card.get("cardSkillName") or "—",
        "type_key": effect_type,
        "type_label": info.get("label", "技能"),
        "short": info.get("short", ""),
        "icon": info.get("icon", ""),
        "description": format_skill_description(description, skill, character_name),
        "levels": levels,
    }


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------


def _max_power(card: dict) -> tuple[int, list[int]]:
    best: dict[str, int] = {}
    parameters = card.get("cardParameters")
    if isinstance(parameters, dict):
        # The CN master repo stores one array per parameter type; the last
        # entry is the max-level value.
        for key in ("param1", "param2", "param3"):
            values = parameters.get(key) or []
            if values:
                try:
                    best[key] = int(values[-1] or 0)
                except (TypeError, ValueError):
                    continue
    else:
        for parameter in parameters or []:
            if not isinstance(parameter, dict):
                continue
            key = parameter.get("cardParameterType", "")
            value = int(parameter.get("power", 0) or 0)
            if key and value > best.get(key, 0):
                best[key] = value
    bonuses = {
        "param1": card.get("specialTrainingPower1BonusFixed", 0),
        "param2": card.get("specialTrainingPower2BonusFixed", 0),
        "param3": card.get("specialTrainingPower3BonusFixed", 0),
    }
    parts = []
    for key in ("param1", "param2", "param3"):
        value = best.get(key, 0) + int(bonuses.get(key, 0) or 0)
        if value:
            parts.append(value)
    return sum(parts), parts


def _costumes_for_card(dataset, card: dict) -> list[dict]:
    base = dataset.asset_base
    costumes = dataset.costumes_by_card_id.get(card.get("id", 0), [])[:2]
    result = []
    for costume in costumes:
        parts = costume.get("parts", {}) or {}
        part_list = []
        for part_type in costume.get("partTypes", []) or []:
            variants = parts.get(part_type, []) or []
            if not variants:
                continue
            asset = variants[0].get("assetbundleName", "")
            if not asset:
                continue
            part_list.append(
                {
                    "type": part_type,
                    "label": {"body": "服装", "hair": "发型", "head": "头饰"}.get(part_type, part_type),
                    "asset": asset,
                    "images": {"thumb": (costume_thumb_url(asset, base), 160)},
                }
            )
        if part_list:
            result.append({"name": costume.get("name", "—"), "parts": part_list})
    return result


def build_card(dataset, card: dict) -> dict:
    """A full card view used by the detail page."""
    character = character_view(dataset, card.get("characterId", 0))
    rarity = rarity_view(card.get("cardRarityType", ""))
    attr = attr_view(card.get("attr", ""))
    supply = supply_view(dataset.supply_type(card))
    power, parts = _max_power(card)
    skill = skill_view(dataset, card, character["full_name"])
    event = dataset.event_for_card(card.get("id", 0))
    gacha = dataset.gacha_for_card(card.get("id", 0))
    release_ts = int(card.get("releaseAt", 0) or 0)
    has_after = rarity["key"] not in {"rarity_1", "rarity_2", "rarity_birthday"}
    asset = card.get("assetbundleName", "")
    base = dataset.asset_base

    event_view = None
    if event:
        event_entry = next(
            (
                entry
                for entry in dataset.event_cards_by_event.get(event.get("id", 0), [])
                if int(entry.get("cardId", 0) or 0) == int(card.get("id", 0) or 0)
            ),
            None,
        )
        event_view = {
            "id": event.get("id", 0),
            "name": event.get("name", "—"),
            "banner_url": event_banner_url(event.get("assetbundleName", ""), base),
            "period": f"{format_timestamp(event.get('startAt'), False)} ~ {format_timestamp(event.get('aggregateAt'), False)}",
            "bonus_rate": (event_entry or {}).get("bonusRate", 0) or 0,
            "bonus_rate_label": _number((event_entry or {}).get("bonusRate", 0) or 0),
        }

    gacha_view = None
    if gacha:
        pickup_ids = {
            int(pickup.get("cardId", 0) or 0)
            for pickup in (gacha.get("gachaPickups") or [])
        }
        gacha_view = {
            "id": gacha.get("id", 0),
            "name": gacha.get("name", "—"),
            "banner_url": gacha_banner_url(gacha.get("id", 0), base),
            "period": f"{format_timestamp(gacha.get('startAt'), False)} ~ {format_timestamp(gacha.get('endAt'), False)}",
            "is_pickup": int(card.get("id", 0) or 0) in pickup_ids,
        }

    return {
        "id": int(card.get("id", 0)),
        "character": character,
        "rarity": rarity,
        "attr": attr,
        "title": card.get("prefix") or "—",
        "skill_name": card.get("cardSkillName") or "—",
        "gacha_phrase": (card.get("gachaPhrase") or "").strip(),
        "supply": supply,
        "release_at": format_timestamp(release_ts),
        "release_ts": release_ts,
        "is_new": is_new(release_ts),
        "is_unreleased": is_unreleased(release_ts),
        "max_power": power,
        "power_parts": parts,
        "power_breakdown": " / ".join(str(value) for value in parts) if parts else "—",
        "skill": skill,
        "event": event_view,
        "gacha": gacha_view,
        "costumes": _costumes_for_card(dataset, card),
        "has_after": has_after,
        "images": {
            # A card without after-training art gets a single full-width frame.
            "art_normal": (card_art_url(asset, False, base), 720 if has_after else 1080),
            "art_after": (card_art_url(asset, True, base), 720) if has_after else ("", 0),
            "char_icon": (character["icon_url"], 96),
            "event_banner": (event_view["banner_url"], 520) if event_view else ("", 0),
            "gacha_banner": (gacha_view["banner_url"], 520) if gacha_view else ("", 0),
        },
    }


def build_card_item(
    dataset,
    card: dict,
    thumb_width: int = 170,
    with_after: bool = True,
) -> dict:
    """A compact card view for grids and result rows."""
    character = character_view(dataset, card.get("characterId", 0))
    rarity = rarity_view(card.get("cardRarityType", ""))
    attr = attr_view(card.get("attr", ""))
    supply = supply_view(dataset.supply_type(card))
    release_ts = int(card.get("releaseAt", 0) or 0)
    asset = card.get("assetbundleName", "")
    has_after = rarity["key"] not in {"rarity_1", "rarity_2", "rarity_birthday"}
    base = dataset.asset_base
    return {
        "id": int(card.get("id", 0)),
        "character": character,
        "rarity": rarity,
        "attr": attr,
        "title": card.get("prefix") or "—",
        "supply": supply,
        "release_at": format_timestamp(release_ts, False),
        "release_ts": release_ts,
        "is_new": is_new(release_ts),
        "is_unreleased": is_unreleased(release_ts),
        "has_after": has_after,
        "skill_type": (SKILL_TYPES.get(dataset.skill_type(card), {}) or {}).get("short", ""),
        "images": {
            "thumb_normal": (card_thumb_url(asset, False, base), thumb_width),
            "thumb_after": (
                (card_thumb_url(asset, True, base), thumb_width)
                if with_after and has_after
                else ("", 0)
            ),
        },
    }


# ---------------------------------------------------------------------------
# Song
# ---------------------------------------------------------------------------


def _song_difficulties(dataset, music: dict) -> list[dict]:
    mid = music.get("id", 0)
    diffs = dataset.diffs_by_music.get(mid, {})
    constants = dataset.song_constants.get(mid, {})
    result = []
    for key in DIFFICULTY_ORDER:
        entry = diffs.get(key)
        if not entry:
            continue
        result.append(
            difficulty_view(
                key,
                entry.get("playLevel", 0),
                entry.get("totalNoteCount", 0),
                constants.get(key),
            )
        )
    return result


def _vocal_groups(dataset, music: dict) -> list[dict]:
    mid = music.get("id", 0)
    groups: dict[str, dict] = {}
    for vocal in dataset.vocals_by_music.get(mid, []):
        vocal_type = vocal.get("musicVocalType", "")
        if vocal_type == "sekai":
            title, order = "Sekai ver.", 0
        elif vocal_type in ("original_song", "virtual_singer"):
            title, order = "Virtual Singer ver.", 1
        elif vocal_type == "another_vocal":
            title, order = "Another Vocal ver.", 2
        else:
            continue
        bucket = groups.setdefault(title, {"title": title, "order": order, "characters": []})
        for entry in vocal.get("characters", []) or []:
            cid = entry.get("characterId")
            if not cid:
                continue
            if entry.get("characterType") == "outside_character":
                name = dataset.outside_characters.get(cid) or f"外部角色 {cid}"
                character = {
                    "id": 1000 + int(cid),
                    "name": name,
                    "full_name": name,
                    "icon_url": "",
                }
            else:
                character = character_view(dataset, int(cid))
            if all(existing["id"] != character["id"] for existing in bucket["characters"]):
                bucket["characters"].append(character)
    return [groups[key] for key in sorted(groups, key=lambda name: groups[name]["order"])]


def build_song(dataset, music: dict, selected: str | None = None) -> dict:
    mid = int(music.get("id", 0))
    title_jp = music.get("title", "") or "—"
    title_cn = dataset.song_titles.get(title_jp, "")
    asset = music.get("assetbundleName", "")
    base = dataset.asset_base
    difficulties = _song_difficulties(dataset, music)
    for entry in difficulties:
        entry["selected"] = entry["key"] == selected
    event = dataset.event_for_music(mid)
    event_view = None
    if event:
        event_view = {
            "id": event.get("id", 0),
            "name": event.get("name", "—"),
            "banner_url": event_banner_url(event.get("assetbundleName", ""), base),
            "period": f"{format_timestamp(event.get('startAt'), False)} ~ {format_timestamp(event.get('aggregateAt'), False)}",
        }
    publish_ts = int(music.get("publishedAt", 0) or 0)
    duration_seconds = int(music.get("secForMusicScoreMaker", 0) or 0)
    return {
        "id": mid,
        "title": title_cn or title_jp,
        "title_original": title_jp if title_cn and title_cn != title_jp else "",
        "pronunciation": music.get("pronunciation", "") or "",
        "lyricist": music.get("lyricist", "") or "—",
        "composer": music.get("composer", "") or "—",
        "arranger": music.get("arranger", "") or "—",
        "categories": [
            MUSIC_CATEGORIES.get(
                category.get("musicCategoryName", "") if isinstance(category, dict) else category,
                category.get("musicCategoryName", "") if isinstance(category, dict) else category,
            )
            for category in (music.get("categories") or [])
        ],
        "duration": format_duration(duration_seconds) if duration_seconds > 0 else "",
        "has_duration": duration_seconds > 0,
        "publish_at": format_timestamp(publish_ts),
        "publish_ts": publish_ts,
        "is_new": is_new(publish_ts),
        "difficulties": difficulties,
        "aliases": list(dataset.aliases_by_music.get(mid, [])),
        "vocal_groups": _vocal_groups(dataset, music),
        "event": event_view,
        "images": {
            "jacket": (jacket_url(asset, base), 520),
            "jacket_thumb": (jacket_thumb_url(asset, base), 220),
            "event_banner": (event_view["banner_url"], 520) if event_view else ("", 0),
        },
    }


def build_song_item(dataset, music: dict, selected: str | None = None, thumb_width: int = 220) -> dict:
    mid = int(music.get("id", 0))
    title_jp = music.get("title", "") or "—"
    title_cn = dataset.song_titles.get(title_jp, "")
    asset = music.get("assetbundleName", "")
    base = dataset.asset_base
    difficulties = _song_difficulties(dataset, music)
    for entry in difficulties:
        entry["selected"] = entry["key"] == selected
    publish_ts = int(music.get("publishedAt", 0) or 0)
    return {
        "id": mid,
        "title": title_cn or title_jp,
        "title_original": title_jp if title_cn and title_cn != title_jp else "",
        "pronunciation": music.get("pronunciation", "") or "",
        "composer": music.get("composer", "") or "—",
        "difficulties": difficulties,
        "publish_at": format_timestamp(publish_ts, False),
        "publish_ts": publish_ts,
        "is_new": is_new(publish_ts),
        "images": {"jacket_thumb": (jacket_thumb_url(asset, base), thumb_width)},
    }


# ---------------------------------------------------------------------------
# Gacha
# ---------------------------------------------------------------------------


def _gacha_status(start_at, end_at) -> tuple[str, str]:
    now = int(time.time() * 1000)
    try:
        start = int(start_at or 0)
        end = int(end_at or 0)
    except (TypeError, ValueError):
        return "ended", "已结束"
    if start <= now <= end:
        return "ongoing", "进行中"
    if now < start:
        return "upcoming", "即将开放"
    return "ended", "已结束"


def _behavior_label(kind: str, spins: int) -> str:
    """Human label for one gacha behavior (the raw type names are API keys)."""
    count = {1: "单抽", 10: "10 连"}.get(spins) or (f"{spins} 连" if spins > 0 else "抽卡")
    if kind == "once_a_day":
        return "每日 1 次" + (f" · {count}" if spins > 1 else "")
    if kind == "once_a_week":
        return "每周 1 次" + (f" · {count}" if spins > 1 else "")
    if kind == "ceil":
        return "天井兑换"
    suffix = {
        "over_rarity_2_once": "★2 以上确定",
        "over_rarity_3_once": "★3 以上确定",
        "over_rarity_4_once": "★4 确定",
    }.get(kind)
    if suffix:
        return f"{count} · {suffix}"
    if not kind or kind == "normal":
        return count
    return f"{kind} · {count}"


def _gacha_behaviors(gacha: dict) -> list[dict]:
    resources = {
        "paid_jewel": "有偿水晶",
        "jewel": "水晶",
        "gacha_ticket": "抽卡券",
        "colorful_pass": "缤纷通行证",
        "free_jewel": "免费水晶",
    }
    grouped: dict[tuple, dict] = {}
    for behavior in gacha.get("gachaBehaviors", []) or []:
        kind = behavior.get("gachaBehaviorType", "")
        cost_type = resources.get(behavior.get("costResourceType", ""), behavior.get("costResourceType", ""))
        quantity = behavior.get("costResourceQuantity", 0)
        spins = int(behavior.get("spinCount", 0) or 0)
        limit = int(behavior.get("executeLimit", 0) or 0)
        label = _behavior_label(kind, spins)
        entry = grouped.setdefault(
            (label, spins),
            {
                "label": label,
                "spins": spins,
                "costs": [],
                "limits": set(),
                "limited_count": 0,
                "behavior_count": 0,
            },
        )
        cost = f"{cost_type} × {quantity}" if quantity else cost_type
        if cost and cost not in entry["costs"]:
            entry["costs"].append(cost)
        entry["behavior_count"] += 1
        if limit:
            entry["limits"].add(limit)
            entry["limited_count"] += 1
    result = []
    for entry in grouped.values():
        limits = sorted(entry.pop("limits"))
        limited_count = entry.pop("limited_count")
        behavior_count = entry.pop("behavior_count")
        # Only say "limited" when every cost option carries the same limit;
        # a mixed group is described as partially limited.
        entry["limit"] = limits[0] if len(limits) == 1 and limited_count == behavior_count else 0
        entry["mixed_limit"] = 0 < limited_count < behavior_count
        result.append(entry)
    # One spin first, then ten pulls; within a group keep the resource order.
    return sorted(result, key=lambda item: (item["spins"], item["label"]))


def _gacha_rates(gacha: dict) -> list[dict]:
    result = []
    for rate in gacha.get("gachaCardRarityRates", []) or []:
        rarity = rarity_view(rate.get("cardRarityType", ""))
        result.append(
            {
                "key": rarity["key"],
                "label": rarity["label"],
                "color": rarity["color"],
                "rate": rate.get("rate", 0),
                "rate_label": f"{float(rate.get('rate', 0)):g}%",
            }
        )
    return result


def _ten_pull_guarantee(gacha: dict) -> str:
    """The strongest guarantee the pool actually advertises for a 10-pull."""
    label = ""
    for behavior in gacha.get("gachaBehaviors", []) or []:
        if int(behavior.get("spinCount", 0) or 0) < 10:
            continue
        kind = behavior.get("gachaBehaviorType", "")
        if kind == "over_rarity_4_once":
            return "10 连 ★4 确定"
        if kind == "over_rarity_3_once":
            label = "10 连 ★3 以上确定"
    return label


def build_gacha(dataset, gacha: dict) -> dict:
    base = dataset.asset_base
    status, status_label = _gacha_status(gacha.get("startAt"), gacha.get("endAt"))
    rates = _gacha_rates(gacha)
    rate_map = {entry["key"]: float(entry["rate"]) for entry in rates}
    details = gacha.get("gachaDetails", []) or []
    pickup_ids = {
        int(pickup.get("cardId"))
        for pickup in (gacha.get("gachaPickups", []) or [])
        if pickup.get("cardId")
    }
    weight_by_rarity: dict[str, float] = {}
    weight_by_card: dict[int, float] = {}
    for detail in details:
        card = dataset.cards_by_id.get(detail.get("cardId"))
        if not card:
            continue
        rarity_key = card.get("cardRarityType", "")
        weight = float(detail.get("weight", 0) or 0)
        weight_by_rarity[rarity_key] = weight_by_rarity.get(rarity_key, 0) + weight
        weight_by_card[int(card["id"])] = weight

    pickups = []
    for card_id in pickup_ids:
        card = dataset.cards_by_id.get(card_id)
        if not card:
            continue
        rarity_key = card.get("cardRarityType", "")
        total = weight_by_rarity.get(rarity_key, 0) or 1
        share = rate_map.get(rarity_key, 0) * (weight_by_card.get(card_id, 0) / total)
        item = build_card_item(dataset, card, thumb_width=170, with_after=True)
        item["rate_label"] = f"{share:.3f}%"
        pickups.append(item)
    pickups.sort(key=lambda item: (-item["rarity"]["num"], item["id"]))

    summary = ((gacha.get("gachaInformation") or {}).get("summary") or "").strip()
    if summary:
        paragraphs = [
            re.sub(r"\s+", " ", part).strip()
            for part in re.split(r"\n\s*\n", summary)
            if part.strip()
        ]
        summary = "\n\n".join(paragraphs[:3])
        if len(summary) > 480:
            summary = summary[:480].rstrip() + "……"
    guarantee = _ten_pull_guarantee(gacha)

    banner_fallback = ""
    fallback_card = None
    if pickups:
        fallback_card = dataset.cards_by_id.get(pickups[0]["id"])
    if fallback_card is None:
        for detail in details:
            fallback_card = dataset.cards_by_id.get(detail.get("cardId"))
            if fallback_card:
                break
    if fallback_card:
        has_after = fallback_card.get("cardRarityType") not in {"rarity_1", "rarity_2", "rarity_birthday"}
        banner_fallback = card_art_url(fallback_card.get("assetbundleName", ""), has_after, base)

    return {
        "id": int(gacha.get("id", 0)),
        "name": gacha.get("name") or "—",
        "type": gacha.get("gachaType", ""),
        "status": status,
        "status_label": status_label,
        "start_at": format_timestamp(gacha.get("startAt")),
        "end_at": format_timestamp(gacha.get("endAt")),
        "period_label": f"{format_timestamp(gacha.get('startAt'), False)} ~ {format_timestamp(gacha.get('endAt'), False)}",
        "rates": rates,
        "pickups": pickups,
        "pickup_columns": _tile_grid_columns(len(pickups)),
        "pickup_ids": pickup_ids,
        "details_count": len(details),
        "behaviors": _gacha_behaviors(gacha),
        "summary": summary,
        "has_ten_guarantee": bool(guarantee),
        "guarantee_label": guarantee,
        "images": {
            "banner": (gacha_banner_url(gacha.get("id", 0), base), 1080),
            "banner_fallback": (banner_fallback, 1080),
        },
    }


def build_gacha_item(dataset, gacha: dict) -> dict:
    base = dataset.asset_base
    status, status_label = _gacha_status(gacha.get("startAt"), gacha.get("endAt"))
    pickups = []
    for pickup in (gacha.get("gachaPickups", []) or [])[:4]:
        card = dataset.cards_by_id.get(pickup.get("cardId"))
        if not card:
            continue
        character = character_view(dataset, card.get("characterId", 0))
        pickups.append(
            {
                "id": int(card.get("id", 0)),
                "title": card.get("prefix") or "—",
                "character": character,
                "rarity": rarity_view(card.get("cardRarityType", "")),
                "images": {"thumb": (card_thumb_url(card.get("assetbundleName", ""), base=base), 120)},
            }
        )
    fallback_url = ""
    fallback_card = None
    for pickup in gacha.get("gachaPickups", []) or []:
        fallback_card = dataset.cards_by_id.get(pickup.get("cardId"))
        if fallback_card:
            break
    if fallback_card is None:
        for detail in gacha.get("gachaDetails", []) or []:
            fallback_card = dataset.cards_by_id.get(detail.get("cardId"))
            if fallback_card:
                break
    if fallback_card:
        has_after = fallback_card.get("cardRarityType") not in {"rarity_1", "rarity_2", "rarity_birthday"}
        fallback_url = card_art_url(fallback_card.get("assetbundleName", ""), has_after, base)
    return {
        "id": int(gacha.get("id", 0)),
        "name": gacha.get("name") or "—",
        "status": status,
        "status_label": status_label,
        "period_label": f"{format_timestamp(gacha.get('startAt'), False)} ~ {format_timestamp(gacha.get('endAt'), False)}",
        "pickups": pickups,
        "pickup_count": len(gacha.get("gachaPickups", []) or []),
        "images": {
            "banner": (gacha_banner_url(gacha.get("id", 0), base), 520),
            "banner_fallback": (fallback_url, 520),
        },
    }


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


def _event_status(event: dict) -> tuple[str, str]:
    now = int(time.time() * 1000)
    start = int(event.get("startAt", 0) or 0)
    end = int(event.get("aggregateAt", 0) or 0)
    if start <= now <= end:
        return "ongoing", "进行中"
    if now < start:
        return "upcoming", "即将开始"
    return "ended", "已结束"


def _tile_grid_columns(count: int) -> int:
    """Chooses 3–5 columns that leave the fewest empty cells."""
    if count <= 0:
        return 1
    if count <= 2:
        return count
    # ``-cols`` is the deterministic tie-break: prefer the wider grid.
    return min(
        (3, 4, 5),
        key=lambda cols: (((cols - count % cols) % cols), -cols),
    )


def build_event(dataset, event: dict, card_limit: int = 12) -> dict:
    base = dataset.asset_base
    status, status_label = _event_status(event)
    unit = unit_view(event.get("unit", ""))
    event_type = EVENT_TYPES.get(event.get("eventType", ""), {})
    bonus_cards = []
    bonus_entries = [
        entry
        for entry in dataset.event_cards_by_event.get(event.get("id", 0), [])
        if float(entry.get("bonusRate", 0) or 0) > 0
    ]
    for entry in sorted(
        bonus_entries,
        key=lambda item: (-float(item.get("bonusRate", 0) or 0), item.get("cardId", 0)),
    )[:card_limit]:
        card = dataset.cards_by_id.get(entry.get("cardId"))
        if not card:
            continue
        item = build_card_item(dataset, card, thumb_width=170, with_after=True)
        item["bonus_rate"] = entry.get("bonusRate", 0) or 0
        item["bonus_rate_label"] = _number(item["bonus_rate"])
        bonus_cards.append(item)

    bonus_characters = []
    seen = set()
    for entry in dataset.event_deck_bonuses_by_event.get(event.get("id", 0), []):
        unit_id = entry.get("gameCharacterUnitId")
        if not unit_id or int(unit_id) > 20:
            continue
        character_id = dataset.character_id_by_unit.get(int(unit_id))
        if not character_id or character_id in seen:
            continue
        seen.add(character_id)
        bonus_characters.append(character_view(dataset, character_id))

    song = None
    music_id = dataset.event_to_music.get(event.get("id", 0))
    if music_id and music_id in dataset.musics_by_id:
        song = build_song_item(dataset, dataset.musics_by_id[music_id], thumb_width=220)

    return {
        "id": int(event.get("id", 0)),
        "name": event.get("name") or "—",
        "type_key": event.get("eventType", ""),
        "type_label": event_type.get("label", event.get("eventType", "活动")),
        "unit": unit,
        "status": status,
        "status_label": status_label,
        "start_at": format_timestamp(event.get("startAt")),
        "end_at": format_timestamp(event.get("aggregateAt")),
        "period_label": f"{format_timestamp(event.get('startAt'), False)} ~ {format_timestamp(event.get('aggregateAt'), False)}",
        "bonus_cards": bonus_cards,
        "bonus_columns": _tile_grid_columns(len(bonus_cards)),
        "bonus_characters": bonus_characters,
        "song": song,
        "images": {
            "banner": (event_banner_url(event.get("assetbundleName", ""), base), 1080),
            "song_jacket": (song["images"]["jacket_thumb"][0], 220) if song else ("", 0),
        },
    }


def build_event_item(dataset, event: dict) -> dict:
    base = dataset.asset_base
    status, status_label = _event_status(event)
    unit = unit_view(event.get("unit", ""))
    event_type = EVENT_TYPES.get(event.get("eventType", ""), {})
    song = None
    music_id = dataset.event_to_music.get(event.get("id", 0))
    if music_id and music_id in dataset.musics_by_id:
        song = build_song_item(dataset, dataset.musics_by_id[music_id], thumb_width=160)
    return {
        "id": int(event.get("id", 0)),
        "name": event.get("name") or "—",
        "type_label": event_type.get("label", event.get("eventType", "活动")),
        "unit": unit,
        "status": status,
        "status_label": status_label,
        "period_label": f"{format_timestamp(event.get('startAt'), False)} ~ {format_timestamp(event.get('aggregateAt'), False)}",
        "song": song,
        "images": {"banner": (event_banner_url(event.get("assetbundleName", ""), base), 520)},
    }


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def catalog_stats(dataset) -> dict:
    now = int(time.time() * 1000)
    cards = dataset.cards
    songs = dataset.musics
    rarities = {}
    attrs = {}
    units = {}
    for card in cards:
        rarity = rarity_view(card.get("cardRarityType", ""))
        rarities[rarity["key"]] = rarities.get(rarity["key"], 0) + 1
        attrs[card.get("attr", "")] = attrs.get(card.get("attr", ""), 0) + 1
        unit = dataset.unit_by_character.get(card.get("characterId", 0), "")
        units[unit] = units.get(unit, 0) + 1

    difficulty_counts = {}
    for entry in dataset.difficulties:
        key = entry.get("musicDifficulty", "")
        difficulty_counts[key] = difficulty_counts.get(key, 0) + 1

    ongoing_events = [
        {
            "id": int(event.get("id", 0)),
            "name": event.get("name", "—"),
            "period_label": f"{format_timestamp(event.get('startAt'), False)} ~ {format_timestamp(event.get('aggregateAt'), False)}",
            "unit": unit_view(event.get("unit", "")),
        }
        for event in dataset.events
        if int(event.get("startAt", 0) or 0) <= now <= int(event.get("aggregateAt", 0) or 0)
    ]
    latest_cards = [
        build_card_item(dataset, card, thumb_width=150, with_after=False)
        for card in sorted(cards, key=lambda item: item.get("releaseAt", 0), reverse=True)[:8]
    ]
    latest_songs = [
        build_song_item(dataset, music, thumb_width=180)
        for music in sorted(songs, key=lambda item: item.get("publishedAt", 0), reverse=True)[:8]
    ]
    rarity_order = {key: index for index, key in enumerate(RARITIES)}
    attr_order = {key: index for index, key in enumerate(ATTRIBUTES)}
    unit_order = {key: index for index, key in enumerate(UNITS)}
    return {
        "cards": len(cards),
        "songs": len(songs),
        "events": len(dataset.events),
        "gachas": dataset.gacha_count,
        "ongoing_events": ongoing_events,
        "rarities": [
            {"key": key, **rarity_view(key), "count": count}
            for key, count in sorted(rarities.items(), key=lambda item: rarity_order.get(item[0], 99))
        ],
        "attrs": [
            {"key": key, **attr_view(key), "count": count}
            for key, count in sorted(attrs.items(), key=lambda item: attr_order.get(item[0], 99))
        ],
        "units": [
            {"key": key, **unit_view(key), "count": count}
            for key, count in sorted(units.items(), key=lambda item: unit_order.get(item[0], 99))
        ],
        "difficulties": [
            {"key": key, **difficulty_view(key), "count": difficulty_counts.get(key, 0)}
            for key in DIFFICULTY_ORDER
        ],
        "latest_cards": latest_cards,
        "latest_songs": latest_songs,
    }

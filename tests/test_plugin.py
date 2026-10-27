# -*- coding: utf-8 -*-
"""Offline checks for the PJSK plugin: routing, data, views and templates.

Run from the plugin root:

    python tests/test_plugin.py

No AstrBot installation is required. If the local data cache is missing (a
fresh clone), the JP MasterData is downloaded once; afterwards every check
runs offline.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import _stubs  # noqa: E402

_stubs.install()

import main  # noqa: E402
from pjsk.chart import (  # noqa: E402
    chart_asset_url,
    inline_svg_images,
    preview_document,
    svg_dimensions,
    svg_image_urls,
)
from pjsk.constants import chart_svg_url  # noqa: E402
from pjsk.gacha import GachaStateStore, perform_draw  # noqa: E402
from pjsk.models import (  # noqa: E402
    build_card,
    build_card_item,
    build_event,
    build_event_item,
    build_gacha,
    build_gacha_item,
    build_song,
    build_song_item,
    catalog_stats,
    character_view,
    rarity_view,
)
from pjsk.render import AstrBotRenderService  # noqa: E402
from pjsk.search import (  # noqa: E402
    _ban_events_for_character,
    extract_difficulty,
    filter_cards,
    filter_songs_by_level,
    find_card_by_character_index,
    parse_card_filters,
    parse_constant_range,
    search_cards,
    search_songs,
)

PASSED = 0


def check(condition, message: str) -> None:
    global PASSED
    if not condition:
        raise AssertionError(message)
    PASSED += 1


async def run() -> None:
    # The suite pins JP as its default. A fresh clone has no data cache, so the
    # core tables are fetched once; the checks themselves never touch the net.
    plugin = main.PjskFunctionPlugin(
        None, config={"default_region": "jp"}, data_dir=ROOT / "data"
    )
    manager = plugin.managers["jp"]
    if not (ROOT / "data" / "master" / "cards.json").is_file():
        print("data cache missing; syncing JP master data (one-time)...")
        await manager.sync(force=True, include_gacha=False)
    await manager.load()
    dataset = manager.dataset
    check(dataset is not None, "dataset loaded")
    check(len(dataset.cards) > 1000, f"cards loaded ({len(dataset.cards)})")
    check(len(dataset.musics) > 500, f"songs loaded ({len(dataset.musics)})")
    check(len(dataset.difficulties) > 3000, "difficulties loaded")
    check(len(dataset.events) > 100, "events loaded")
    check(dataset.cards_by_id.get(4) is not None, "card index")
    check(dataset.musics_by_id.get(1) is not None, "song index")
    check(dataset.skills_by_id, "skill index")

    # -- routing ---------------------------------------------------------
    check(plugin._normalize_prefix("pjsk十连") == ("十连", True, "jp", False), "pjsk prefix")
    check(plugin._normalize_prefix("#查卡 4") == ("查卡 4", False, "jp", False), "hash prefix")
    check(plugin._normalize_prefix("cn查卡 4") == ("查卡 4", False, "cn", True), "cn prefix")
    check(plugin._normalize_prefix("jp查曲 1") == ("查曲 1", False, "jp", True), "jp prefix")
    check(plugin._normalize_prefix("国服 查卡") == ("查卡", False, "cn", True), "region alias")
    check(plugin._normalize_prefix("jp cn查卡")[2] == "cn", "two region prefixes (last wins)")
    check(plugin._normalize_prefix("jpcard 4") == ("card 4", False, "jp", True), "region + ascii command")
    check(plugin._normalize_prefix("jp/查卡 4") == ("查卡 4", False, "jp", True), "region + slash separator")
    check(plugin._normalize_prefix("pjsk/查卡") == ("查卡", True, "jp", False), "pjsk + slash separator")
    check(plugin._normalize_prefix("/查卡 4") == ("查卡 4", False, "jp", False), "leading slash")
    check(plugin._match_command(plugin._normalize_prefix("jp/查卡 4")[0]) is not None, "jp/查卡 routes")
    check(plugin._match_command(plugin._normalize_prefix("pjsk/查卡")[0]) is not None, "pjsk/查卡 routes")
    check(plugin._normalize_prefix("cnn") == ("cnn", False, "jp", False), "ascii region prefix needs a command")
    check(plugin._match_command("查卡面 4")[0] == "card", "查卡面 is a card alias")
    check(plugin._match_command("卡面 4")[0] == "card", "卡面 is a card alias")
    check(plugin._match_command("card 4")[0] == "card_art", "card sends original art")
    check(plugin._match_command("card123")[0] == "card_art", "card id without space")
    check(plugin._match_command("cardigan") is None, "ascii alias needs a boundary")
    check(plugin._match_command("song123")[0] == "song", "song id without space")
    check(plugin._match_command("查卡 4")[0] == "card", "card route")
    check(plugin._match_command("查曲绘 1")[0] == "jacket", "jacket wins over song")
    check(plugin._match_command("查曲 id1")[0] == "song", "song route")
    check(plugin._match_command("卡池列表")[0] == "gacha_list", "gacha list route")
    check(plugin._match_command("100抽")[0] == "gacha_sim", "numeric draw route")
    check(plugin._match_command("活动一览")[0] == "event_list", "event list route")
    check(plugin._match_command("谱面 825")[0] == "chart", "chart route")
    check(plugin._match_command("查谱面 825")[0] == "chart", "chart alias wins")
    check(plugin._match_command("谱面预览 825")[0] == "chart", "chart preview alias")
    check(plugin._match_command("chart 825")[0] == "chart", "chart ascii route")
    check(plugin._match_command("查定数 master 34")[0] == "constant", "constant route")
    check(plugin._match_command("定数表 master")[0] == "constant", "constant table route")
    check(plugin._match_command("卡牌一览") is None, "card overview command removed")
    check(plugin._match_command("随便聊聊") is None, "unrelated text ignored")
    check(plugin.managers["cn"].region.asset_base.endswith("sekai-cn-assets"), "CN asset base")
    check(plugin.managers["jp"].region.asset_base.endswith("sekai-jp-assets"), "JP asset base")
    check(GachaStateStore._bucket_key(1, "cn") == "1", "CN gacha bucket keeps old key")
    check(GachaStateStore._bucket_key(1, "jp") == "jp:1", "JP gacha bucket is namespaced")

    # -- search ----------------------------------------------------------
    diff, rest = extract_difficulty("MASTER 32")
    check((diff, rest) == ("master", "32"), "difficulty token")
    diff, rest = extract_difficulty("Extra Story")
    check((diff, rest) == ("master", "Extra Story"), "difficulty does not match inside a title")
    diff, rest = extract_difficulty("ma32")
    check((diff, rest) == ("master", "32"), "attached difficulty spelling")
    diff, rest = extract_difficulty("ma30-32")
    check((diff, rest) == ("master", "30-32"), "attached difficulty keeps the range")
    diff, rest = extract_difficulty("Song ma32")
    check((diff, rest) == ("master", "Song 32"), "attached difficulty behaves like 'ma 32'")
    check(chart_svg_url(825, "master") == "https://pjsk.moe/chart-svg/825/master.svg", "chart svg url")
    sample_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="4704" height="2156" viewBox="0 0 4704 2156">'
        '<image href="/notes_new/custom01/notes_0.png" x="1" y="2" width="118" height="62"/>'
        '<image href="https://storage.exmeaning.com/jacket_s_1.webp" x="10" y="20" width="200" height="200"/>'
        "</svg>"
    )
    check(svg_dimensions(sample_svg) == (4704, 2156), "chart svg dimensions")
    check(svg_dimensions("<svg></svg>") == (0, 0), "chart svg without size")
    urls = svg_image_urls(sample_svg)
    check(urls == ["/notes_new/custom01/notes_0.png", "https://storage.exmeaning.com/jacket_s_1.webp"],
          "chart svg image urls")
    check(chart_asset_url("/notes_new/custom01/notes_0.png") == "https://pjsk.moe/notes_new/custom01/notes_0.png",
          "relative chart asset resolves against the chart host")
    check(chart_asset_url("https://storage.exmeaning.com/x.webp") == "https://storage.exmeaning.com/x.webp",
          "absolute chart asset kept")
    inlined = inline_svg_images(sample_svg, {"/notes_new/custom01/notes_0.png": "data:image/webp;base64,AAA"})
    check('href="data:image/webp;base64,AAA"' in inlined and 'href="/notes_new/custom01/notes_0.png"' not in inlined,
          "chart note sprite inlined")
    check('href="https://storage.exmeaning.com/jacket_s_1.webp"' in inlined,
          "unmapped chart asset untouched")
    document = preview_document("data:image/svg+xml;base64,AAA", 4704, 2156)
    check("width:4704px" in document and "height:2156px" in document, "chart document fills the viewport")
    check(parse_constant_range("25") == (25.0, 25.99), "constant tier range")
    check(parse_constant_range("26.5") == (26.5, 26.5), "exact constant")
    check(parse_constant_range("34.5-35.5") == (34.5, 35.5), "constant dash range")
    check(parse_constant_range("34.5 35.5") == (34.5, 35.5), "constant spaced range")
    check(parse_constant_range("35-34") == (34.0, 35.0), "constant range is normalised")
    check(parse_constant_range("") == (None, None), "no constant numbers")
    check(dataset.diffs_by_music.get(825, {}).get("master") is not None, "song 825 has a master chart")
    tier = [
        float(constant)
        for music in dataset.musics
        for constant in [dataset.song_constants.get(int(music.get("id", 0) or 0), {}).get("master")]
        if constant is not None and 25.0 <= float(constant) <= 25.99
    ]
    check(len(tier) >= 3, f"master 25 tier populated ({len(tier)})")
    level_28 = [
        int(music.get("id", 0) or 0)
        for music in dataset.musics
        if int((dataset.diffs_by_music.get(int(music.get("id", 0) or 0), {}).get("master") or {}).get("playLevel", 0) or 0) == 28
    ]
    check(825 in level_28, "constant command filters on the official playLevel")
    fake_levels = SimpleNamespace(
        musics=[{"id": 1, "title": "A"}, {"id": 2, "title": "B"}, {"id": 3, "title": "C"}],
        diffs_by_music={
            1: {"master": {"playLevel": 32}},
            2: {"master": {"playLevel": 32}},
            3: {"expert": {"playLevel": 30}},
        },
        song_constants={},
    )
    level_rows = filter_songs_by_level(fake_levels, "master", 32, 32)
    check([music["id"] for _, music in level_rows] == [1, 2],
          "level lookup works without the community constant table")
    music671 = dataset.musics_by_id.get(671)
    if music671 is not None and dataset.song_constants.get(671, {}).get("master") is not None:
        item = build_song_item(dataset, music671, "master")
        selected = next(entry for entry in item["difficulties"] if entry["selected"])
        check(abs(float(selected["constant"]) - float(dataset.song_constants[671]["master"])) < 1e-6,
              "selected difficulty carries its constant")
    resolved = search_songs(dataset, "id1")
    check(resolved.mode == "detail" and resolved.item["id"] == 1, "song id lookup")
    resolved = search_songs(dataset, "#1")
    check(resolved.mode == "detail" and resolved.item["id"] == 1, "song #id lookup")
    resolved = search_songs(dataset, "mnr1")
    check(resolved.mode == "detail", "ban-event token lookup")
    resolved = search_songs(dataset, "Tell Your World")
    check(resolved.mode == "detail", "song title lookup")
    filters = parse_card_filters(dataset, "miku 4")
    check(filters["rarity"] == "rarity_4", "rarity filter")
    check(filters["character_id"] is not None, "character filter")
    cards = filter_cards(dataset, filters)
    check(cards, "card filter returns results")
    check(all(card["cardRarityType"] == "rarity_4" for card in cards), "rarity filter applied")
    filters = parse_card_filters(dataset, "4星")
    cards = filter_cards(dataset, filters)
    expected = sum(1 for card in dataset.cards if card.get("cardRarityType") == "rarity_4")
    check(len(cards) == expected and len(cards) > 40, "card list keeps every match")
    filters = parse_card_filters(dataset, "love")
    check(filters["unit"] is None, "single-letter unit does not fire inside a word")
    filters = parse_card_filters(dataset, "一")
    check(filters["character_id"] == 1, "standalone short alias")

    # detailed skill filters and new card query features
    filters = parse_card_filters(dataset, "团分")
    check(filters["skill_ids"] == [15, 16, 17, 18, 19], "团分 targets unit-score skill ids")
    cards = filter_cards(dataset, filters)
    expected = sum(1 for card in dataset.cards if int(card.get("skillId", 0)) in (15, 16, 17, 18, 19))
    check(len(cards) == expected and expected > 10, "团分 keeps every unit-score card")
    filters = parse_card_filters(dataset, "p分")
    check(filters["skill_ids"] == [11] and not filters["skill"], "p分 parsed before 分")
    filters = parse_card_filters(dataset, "判")
    cards = filter_cards(dataset, filters)
    check(cards and all(dataset.skill_type(card) == "judgment_up" for card in cards), "判 uses the sprite skill category")
    filters = parse_card_filters(dataset, "奶")
    cards = filter_cards(dataset, filters)
    check(cards and all(dataset.skill_type(card) == "life_recovery" for card in cards), "奶 uses the sprite skill category")
    event_id = min(dataset.event_cards_by_event)
    filters = parse_card_filters(dataset, f"event{event_id}")
    check(filters["event_id"] == event_id, "event123 filter parsed")
    cards = filter_cards(dataset, filters)
    expected = len(dataset.event_cards_by_event[event_id])
    check(len(cards) == expected and expected > 0, "event filter keeps the event's cards")
    alias, character_id = next(
        (alias, cid) for alias, cid in dataset.alias_to_character.items()
        if len(_ban_events_for_character(dataset, cid)) >= 1
    )
    filters = parse_card_filters(dataset, f"{alias}1")
    check(filters["event_id"] is not None and not filters["event_error"], "箱活缩写 resolves to a ban event")
    filters = parse_card_filters(dataset, "leak 4星")
    check(filters["leak"] and filters["rarity"] == "rarity_4", "leak flag parsed")
    cards = filter_cards(dataset, filters)
    now_ms = int(time.time() * 1000)
    check(all(int(card.get("releaseAt", 0)) > now_ms for card in cards), "leak keeps only unreleased cards")
    resolved = search_cards(dataset, "miku-1")
    check(resolved.mode == "detail", "character index resolves to a card detail")
    newest_miku = max(
        (card for card in dataset.cards if int(card.get("characterId", 0)) == resolved.item["characterId"]),
        key=lambda card: (int(card.get("releaseAt", 0) or 0), int(card.get("id", 0) or 0)),
    )
    check(resolved.item["id"] == newest_miku["id"], "miku-1 is the newest miku card")
    slash_card, slash_error = find_card_by_character_index(dataset, "miku/1")
    check(slash_card is not None and slash_card["id"] == resolved.item["id"], "slash separator works like minus")
    filters = parse_card_filters(dataset, "v")
    check(filters["unit"] == "piapro" and filters["unit_mode"] == "vs", "bare v means pure virtual singers")
    cards = filter_cards(dataset, filters)
    check(cards and all(card.get("supportUnit", "none") == "none" for card in cards), "bare v keeps only pure VS cards")

    # unreleased (leak) cards are marked in list and detail views
    from pjsk.models import build_card as _build_card, build_card_item as _build_card_item  # noqa: E402

    future = dict(min(dataset.cards, key=lambda card: int(card.get("id", 0))))
    future["releaseAt"] = int(time.time() * 1000) + 30 * 86_400_000
    item = _build_card_item(dataset, future)
    check(item["is_unreleased"] and not item["is_new"], "future card item is marked unreleased, not new")
    detail = _build_card(dataset, future)
    check(detail["is_unreleased"], "future card detail is marked unreleased")
    current = _build_card_item(dataset, min(dataset.cards, key=lambda card: int(card.get("id", 0))))
    check(not current["is_unreleased"], "released card is not marked")

    # -- gacha -----------------------------------------------------------
    # The JP gacha table is ~29 MB; when every mirror is rate-limited or
    # offline it cannot be fetched, and the gacha checks are skipped.
    from pjsk.search import search_events, search_gachas

    await manager.ensure_gachas()
    gacha = None
    results: list = []
    if dataset.gachas_loaded and dataset.gachas:
        gacha = next(
            entry
            for entry in dataset.gachas
            if any(rate["cardRarityType"] == "rarity_4" for rate in entry.get("gachaCardRarityRates", []))
            and any(int(behavior.get("spinCount", 0) or 0) >= 10 for behavior in entry.get("gachaBehaviors") or [])
        )
        results = perform_draw(dataset, gacha, 10)
        check(len(results) == 10, "ten pull returns ten cards")
        check(
            any(
                card.get("cardRarityType", "").startswith("rarity_")
                and card.get("cardRarityType", "rarity_1").split("_")[1].isdigit()
                and int(card["cardRarityType"].split("_")[1]) >= 3
                for card in results
            ),
            "ten pull guarantees a 3-star or better",
        )
        check(search_gachas(dataset, f"#{gacha['id']}").mode == "detail", "gacha #id lookup")
        latest_event = max(dataset.events, key=lambda item: int(item.get("startAt", 0) or 0))
        check(search_events(dataset, f"#{latest_event['id']}").mode == "detail", "event #id lookup")
    else:
        print("SKIP gacha checks: gacha table unavailable (rate limit or offline)")

    # -- CN data ---------------------------------------------------------
    cn_manager = plugin.managers["cn"]
    await cn_manager.load()
    await cn_manager.ensure_gachas()
    cn_dataset = cn_manager.dataset
    if cn_dataset is not None and cn_dataset.cards:
        check(search_events(cn_dataset, "mmj").mode == "list", "event unit alias lookup")

        # Skill "最多{{m}}%" is the bonus cap, not base + cap.
        enhance_card = next(
            card
            for card in cn_dataset.cards
            if ";m" in ((cn_dataset.skills_by_id.get(card.get("skillId")) or {}).get("description") or "")
        )
        check("最多50%" in build_card(cn_dataset, enhance_card)["skill"]["description"], "skill enhance cap")

        cn_stats = catalog_stats(cn_dataset)
        check(
            cn_stats["cards"] == len(cn_dataset.cards) and cn_stats["songs"] == len(cn_dataset.musics),
            "catalog totals include unreleased rows",
        )
        check(
            sum(row["count"] for row in cn_stats["difficulties"]) == len(cn_dataset.difficulties),
            "catalog difficulty total",
        )
        bonus_event = next(
            event
            for event in cn_dataset.events
            if any(
                float(entry.get("bonusRate", 0) or 0) > 0
                for entry in cn_dataset.event_cards_by_event.get(event.get("id"), [])
            )
        )
        check(
            all(
                not card["bonus_rate_label"].endswith(".0")
                for card in build_event(cn_dataset, bonus_event)["bonus_cards"]
            ),
            "bonus rate label drops .0",
        )

        if cn_dataset.gachas:
            def gacha_with(kind: str, minimum_spins: int = 0) -> dict:
                return next(
                    entry
                    for entry in cn_dataset.gachas
                    if any(
                        behavior.get("gachaBehaviorType") == kind
                        and int(behavior.get("spinCount", 0) or 0) >= minimum_spins
                        for behavior in entry.get("gachaBehaviors") or []
                    )
                )

            four_star = build_gacha(cn_dataset, gacha_with("over_rarity_4_once"))
            check(any("★4" in behavior["label"] for behavior in four_star["behaviors"]), "★4 behavior label")
            week = build_gacha(cn_dataset, gacha_with("once_a_week"))
            check(any("每周" in behavior["label"] for behavior in week["behaviors"]), "weekly behavior label")
            ten_four = build_gacha(cn_dataset, gacha_with("over_rarity_4_once", 10))
            check(ten_four["guarantee_label"] == "10 连 ★4 确定", "ten-pull guarantee label")
            four_target = gacha_with("over_rarity_4_once", 10)
            if any(rate.get("cardRarityType") == "rarity_4" for rate in four_target.get("gachaCardRarityRates") or []):
                check(
                    all(
                        any(card.get("cardRarityType") == "rarity_4" for card in perform_draw(cn_dataset, four_target, 10))
                        for _ in range(20)
                    ),
                    "★4 ten-pull guarantee",
                )

            # A character-filtered ten pull still honours the SR+ guarantee.
            from pjsk.gacha import GachaPool

            target = gacha_with("over_rarity_3_once", 10)
            pool = GachaPool(cn_dataset, target)
            sr_cards = pool.pools.get("rarity_3", {}).get("entries", [])
            character_id = int(sr_cards[0][0].get("characterId", 0)) if sr_cards else None
            if character_id:
                check(
                    all(
                        any(
                            card.get("cardRarityType", "").split("_")[-1].isdigit()
                            and int(card["cardRarityType"].split("_")[-1]) >= 3
                            for card in perform_draw(cn_dataset, target, 10, character_id)
                        )
                        for _ in range(50)
                    ),
                    "character-filtered ten pull keeps the guarantee",
                )
        else:
            print("SKIP CN gacha checks: CN gacha table unavailable (rate limit or offline)")
    else:
        print("SKIP CN checks: CN data unavailable (rate limit or offline)")

    from pjsk.models import _tile_grid_columns

    check(
        [_tile_grid_columns(count) for count in (6, 7, 11, 13)] == [3, 4, 4, 5],
        "tile columns deterministic",
    )

    # -- views -----------------------------------------------------------
    card_view = build_card(dataset, dataset.cards_by_id[4])
    check(card_view["id"] == 4 and card_view["character"]["full_name"], "card view")
    check(card_view["skill"] is not None, "card skill view")
    song_view = build_song(dataset, dataset.musics_by_id[1], "master")
    check(song_view["difficulties"], "song difficulties")
    check(song_view["images"]["jacket"][0].startswith("https://"), "jacket url")
    gacha_view = build_gacha(dataset, gacha) if gacha is not None else None
    if gacha_view is not None:
        check(gacha_view["rates"], "gacha rates")
    event = max(dataset.events, key=lambda item: int(item.get("startAt", 0) or 0))
    event_view = build_event(dataset, event)
    check(event_view["id"] == event["id"], "event view")
    stats = catalog_stats(dataset)
    check(stats["cards"] > 0 and stats["songs"] > 0, "catalog stats")

    # -- templates render to HTML ----------------------------------------
    base = plugin._base_context("测试", "#0072c2")
    card_items = [build_card_item(dataset, card) for card in dataset.cards[:3]]
    song_items = [build_song_item(dataset, music) for music in dataset.musics[:3]]
    gacha_items = [build_gacha_item(dataset, entry) for entry in dataset.gachas[:3]]
    event_items = [build_event_item(dataset, entry) for entry in dataset.events[:3]]
    level_tabs = [
        {"key": key, "label": key.upper(), "color": "#888888", "count": 10, "selected": key == "master"}
        for key in ("easy", "normal", "hard", "expert", "master", "append")
    ]
    result_views = []
    for card in results:
        rarity = rarity_view(card.get("cardRarityType", ""))
        result_views.append(
            {
                "id": card.get("id", 0),
                "title": card.get("prefix", ""),
                "character": character_view(dataset, card.get("characterId", 1)),
                "rarity": rarity,
                "is_pickup": False,
                "images": {"art": ("", 0)},
            }
        )
    contexts = {
        "help.html": dict(base),
        "catalog.html": {**base, "stats": stats},
        "card_detail.html": {**base, "card": card_view},
        "card_list.html": {
            **base,
            "resolved": SimpleNamespace(items=card_items, total=len(card_items), suggestions=[]),
            "title": "测试卡牌",
            "subtitle": "测试",
            "columns": 3,
            "limit_note": "",
            "guide": False,
        },
        "song_detail.html": {**base, "song": song_view},
        "song_list.html": {
            **base,
            "resolved": SimpleNamespace(items=song_items, total=len(song_items), suggestions=[]),
            "title": "测试歌曲",
            "subtitle": "测试",
            "columns": 3,
            "limit_note": "",
        },
        "level_index.html": {
            **base,
            "diff": {"key": "master", "label": "MASTER", "short": "MA", "color": "#BB33EE"},
            "levels": [{"level": 32, "count": 40, "width": 100}],
            "total": 40,
            "difficulty_tabs": level_tabs,
        },
        "gacha_list.html": {
            **base,
            "resolved": SimpleNamespace(items=gacha_items, total=len(gacha_items), suggestions=[]),
            "title": "测试卡池",
            "columns": 2,
            "limit_note": "",
        },
        "event_detail.html": {**base, "event": event_view},
        "event_list.html": {
            **base,
            "resolved": SimpleNamespace(items=event_items, total=len(event_items), suggestions=[]),
            "title": "测试活动",
            "limit_note": "",
        },
        "status.html": {
            **base,
            "regions": [
                {
                    "key": "cn",
                    "label": "国服",
                    "short": "CN",
                    "version": "6.4.1.3",
                    "last_update": "2026-10-26 12:00",
                    "sources": [{"name": "GitHub API · CN", "status": 200, "elapsed": 0.4, "version": "v1"}],
                    "files": [{"name": "cards.json", "exists": True, "size_label": "4 MB"}],
                    "ok_count": 1,
                    "total_count": 1,
                    "is_default": True,
                },
                {
                    "key": "jp",
                    "label": "日服",
                    "short": "JP",
                    "version": "7.1.0.35",
                    "last_update": "2026-10-26 12:00",
                    "sources": [{"name": "GitHub API · JP", "status": 200, "elapsed": 0.5, "version": "v2"}],
                    "files": [{"name": "cards.json", "exists": True, "size_label": "32 MB"}],
                    "ok_count": 1,
                    "total_count": 1,
                    "is_default": False,
                },
            ],
            "default_region": "jp",
        },
    }
    if gacha_view is not None:
        contexts["gacha_detail.html"] = {**base, "gacha": gacha_view}
        contexts["gacha_result.html"] = {
            **base,
            "gacha": {"id": gacha_view["id"], "name": gacha_view["name"]},
            "results": result_views,
            "stats": {
                "total_pulls": 10,
                "total_rarity": {"4": 1, "3": 2},
                "total_pickups": 1,
                "session_rarity": {"4": 1, "3": 2},
                "session_pickups": 1,
            },
            "count": 10,
            "requested": 10,
            "char_name": "",
        }
    for template, context in contexts.items():
        html = plugin.renderer.html(template, context)
        check("<html" in html and "</html>" in html, f"{template} renders")
        check("mzkbot" in html or template in {"status.html"}, f"{template} has branding")

    guide_html = plugin.renderer.html(
        "card_list.html",
        {
            **base,
            "resolved": SimpleNamespace(items=[], total=0, suggestions=[]),
            "title": "卡牌筛选",
            "subtitle": "测试",
            "columns": 1,
            "limit_note": "",
            "guide": True,
        },
    )
    check("按角色" in guide_html and "查卡 miku 4" in guide_html, "card filter guide renders")

    # -- render options --------------------------------------------------
    service = AstrBotRenderService(None)
    options = service._options(1080, "png", 90, "high")
    check("viewport_width" in options and "viewport_height" in options, "flat viewport keys")
    check("viewport" not in options, "no nested viewport object")

    # -- no leftover references -----------------------------------------
    banned = "luna" + "bot"
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".py", ".html", ".md", ".json", ".yaml", ".txt"}:
            continue
        if any(part in path.parts for part in ("data", "output", "tests", "__pycache__", ".mimosa", ".git")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        check(banned not in text, f"no leftover reference in {path.relative_to(ROOT)}")

    await plugin.assets.close()
    print(f"\nOK: {PASSED} checks passed")


if __name__ == "__main__":
    asyncio.run(run())

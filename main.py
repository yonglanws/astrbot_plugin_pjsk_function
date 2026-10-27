# -*- coding: utf-8 -*-
"""PJSK 查询插件入口：命令路由、页面组装与 AstrBot t2i 渲染。"""

from __future__ import annotations

import asyncio
import base64
import math
import re
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register

try:  # older AstrBot releases type the config as a plain dict
    from astrbot.api import AstrBotConfig
except ImportError:  # pragma: no cover
    AstrBotConfig = dict  # type: ignore[misc,assignment]

try:  # AstrBot loads the plugin as a package
    from .pjsk.assets import AssetService
    from .pjsk.chart import (
        chart_asset_url,
        inline_svg_images,
        preview_document,
        svg_dimensions,
        svg_image_urls,
    )
    from .pjsk.constants import (
        ALL_COMMANDS,
        BRAND,
        card_art_url,
        chart_svg_url,
        DEFAULT_REGION,
        DIFFICULTY_COLORS,
        DIFFICULTY_LABELS,
        DIFFICULTY_ORDER,
        DIFFICULTY_SHORT,
        EVENT_LIST_LIMIT,
        GACHA_SEARCH_LIMIT,
        jacket_url,
        KIND_ACCENTS,
        LIST_JPEG_QUALITY,
        LIST_SCALE,
        LIST_WIDTH,
        PLUGIN_NAME,
        REGION_ALIASES,
        REGIONS,
        SKILL_TYPES,
        SONG_LIST_LIMIT,
        SUPPLY_TYPES,
        resolve_region,
    )
    from .pjsk.gacha import GachaStateStore, perform_draw
    from .pjsk.masterdata import DataManager, Dataset
    from .pjsk.models import (
        attr_view,
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
        unit_view,
    )
    from .pjsk.render import AstrBotRenderService, PageRenderer, RenderError, RenderService
    from .pjsk.search import (
        extract_difficulty,
        filter_cards,
        filter_songs_by_level,
        find_card_by_character_index,
        parse_card_filters,
        parse_constant_range,
        parse_draw_args,
        resolve_gacha_default,
        search_events,
        search_gachas,
        search_songs,
    )
except ImportError:  # direct invocation (tests, scripts)
    from pjsk.assets import AssetService
    from pjsk.chart import (
        chart_asset_url,
        inline_svg_images,
        preview_document,
        svg_dimensions,
        svg_image_urls,
    )
    from pjsk.constants import (
        ALL_COMMANDS,
        BRAND,
        card_art_url,
        chart_svg_url,
        DEFAULT_REGION,
        DIFFICULTY_COLORS,
        DIFFICULTY_LABELS,
        DIFFICULTY_ORDER,
        DIFFICULTY_SHORT,
        EVENT_LIST_LIMIT,
        GACHA_SEARCH_LIMIT,
        jacket_url,
        KIND_ACCENTS,
        LIST_JPEG_QUALITY,
        LIST_SCALE,
        LIST_WIDTH,
        PLUGIN_NAME,
        REGION_ALIASES,
        REGIONS,
        SKILL_TYPES,
        SONG_LIST_LIMIT,
        SUPPLY_TYPES,
        resolve_region,
    )
    from pjsk.gacha import GachaStateStore, perform_draw
    from pjsk.masterdata import DataManager, Dataset
    from pjsk.models import (
        attr_view,
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
        unit_view,
    )
    from pjsk.render import AstrBotRenderService, PageRenderer, RenderError, RenderService
    from pjsk.search import (
        extract_difficulty,
        filter_cards,
        filter_songs_by_level,
        find_card_by_character_index,
        parse_card_filters,
        parse_constant_range,
        parse_draw_args,
        resolve_gacha_default,
        search_events,
        search_gachas,
        search_songs,
    )

# A command word is matched at the start of the message; the longer aliases are
# tried first so 卡牌列表 wins over 卡牌 and 查曲绘 over 查曲.
_NUMERIC_DRAW = re.compile(r"^(\d+)\s*(连|抽|連)")
_LEADING = ("#", "/", "!", ".", "／", "！", "。", "＃")
# ASCII command words may follow a region prefix without a space (jpsong).
_ASCII_COMMANDS = tuple(alias for alias, _ in ALL_COMMANDS if alias.isascii())
# Selection is not security-sensitive, but a system-random source keeps the
# picks unpredictable across processes and avoids a weak-RNG lint.
_rng = __import__("random").SystemRandom()


def _data_dir(plugin_name: str) -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return Path(get_astrbot_data_path()) / "plugin_data" / plugin_name
    except Exception:  # noqa: BLE001 - older AstrBot layouts
        try:
            from astrbot.api.star import StarTools

            return Path(StarTools.get_data_dir(plugin_name))
        except Exception:  # noqa: BLE001 - direct invocation
            return Path(__file__).resolve().parent / "data"


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _size_label(size: int) -> str:
    if not size:
        return "缺失"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def _image_slots(node: Any, slots: list) -> list:
    """Collects every ``images`` tuple and ``icon_url`` in a view tree."""
    if isinstance(node, dict):
        images = node.get("images")
        if isinstance(images, dict):
            for key, spec in list(images.items()):
                if isinstance(spec, tuple) and len(spec) == 2 and spec[0]:
                    slots.append((images, key, spec[0], spec[1]))
        for key, value in node.items():
            if key == "images":
                continue
            if key == "icon_url" and isinstance(value, str) and value.startswith("http"):
                slots.append((node, key, value, 64))
            else:
                _image_slots(value, slots)
    elif isinstance(node, list):
        for value in node:
            _image_slots(value, slots)
    return slots


@register("pjsk_function", "慵懒午睡", "PJSK 查询插件", "2.1.0")
class PjskFunctionPlugin(Star):
    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | None = None,
        data_dir: Path | str | None = None,
    ):
        super().__init__(context)
        self.config = config or {}
        self.plugin_name = PLUGIN_NAME
        self.bundled_dir = Path(__file__).resolve().parent / "data"
        self.data_dir = Path(data_dir) if data_dir else _data_dir(self.plugin_name)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        def source_bases(key: str) -> list[str]:
            raw = self.config.get(
                "cn_data_source_bases" if key == "cn" else "data_source_bases"
            ) or []
            if not isinstance(raw, list):
                return []
            return [str(source) for source in raw if str(source).strip()]

        self.default_region = resolve_region(str(self.config.get("default_region") or DEFAULT_REGION))
        timeout = _as_int(self.config.get("sync_timeout"), 90)
        self.managers = {
            key: DataManager(
                region=profile,
                data_dir=self.data_dir,
                bundled_dir=self.bundled_dir,
                sources=source_bases(key),
                timeout=timeout,
            )
            for key, profile in REGIONS.items()
        }
        self.assets = AssetService(
            self.data_dir / "cache" / "images",
            timeout=_as_int(self.config.get("image_timeout"), 60),
        )
        self.gacha_store = GachaStateStore(self.data_dir / "gacha_state.json")

        render_endpoint = str(self.config.get("render_endpoint") or "").strip()
        fallback = None
        if render_endpoint:
            fallback = RenderService(
                endpoint=render_endpoint,
                output_dir=self.data_dir / "renders",
                timeout=_as_int(self.config.get("render_timeout"), 120),
            )
        self.renderer = PageRenderer(
            Path(__file__).resolve().parent / "pjsk" / "templates",
            AstrBotRenderService(
                getattr(self, "html_render", None),
                fallback=fallback,
                timeout=_as_int(self.config.get("render_timeout"), 120),
            ),
        )
        self.icons = self._build_icons()
        self._startup_task: asyncio.Task[Any] | None = None
        self._daily_task: asyncio.Task[Any] | None = None

    # -- lifecycle ---------------------------------------------------------
    async def initialize(self) -> None:
        self._startup_task = asyncio.create_task(self._startup_sync())
        if bool(self.config.get("auto_sync", True)):
            self._daily_task = asyncio.create_task(
                self._daily_loop(hour=_as_int(self.config.get("sync_hour"), 4), minute=30)
            )
        logger.info(
            "[pjsk] initialized; default region %s; data dir %s",
            self.default_region,
            self.data_dir,
        )

    async def terminate(self) -> None:
        tasks = [task for task in (self._startup_task, self._daily_task) if task and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._startup_task = None
        self._daily_task = None
        await self.assets.close()

    async def _startup_sync(self) -> None:
        await asyncio.sleep(2)
        # Load the default region first so the first query is instant.
        order = [self.default_region] + [key for key in self.managers if key != self.default_region]
        for key in order:
            try:
                await self.managers[key].load()
            except Exception:  # noqa: BLE001 - a corrupt cache must not stop the bot
                logger.exception("[pjsk] initial %s data load failed", key)
        # The default region's cache is loaded first so the first query is
        # instant; the remote checks then run in parallel for both regions.
        await asyncio.gather(
            *(self._sync_manager(manager) for manager in self.managers.values()),
            return_exceptions=True,
        )

    async def _sync_manager(self, manager: DataManager) -> None:
        try:
            # The JP gacha table is 42 MB; only refresh it when it already
            # exists (on-demand loading fetches it for the first gacha query).
            include_gacha = manager.region.key == "cn" or manager.has_gacha_data()
            await manager.sync(force=False, include_gacha=include_gacha)
        except Exception:  # noqa: BLE001 - startup must not crash the bot
            logger.exception("[pjsk] %s startup sync failed", manager.region.key)

    async def _daily_loop(self, hour: int = 4, minute: int = 30) -> None:
        while True:
            now = time.localtime()
            target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, hour, minute, 0, 0, 0, -1))
            if target <= time.time():
                target += 86400
            await asyncio.sleep(max(60, target - time.time()))
            for manager in self.managers.values():
                await self._sync_manager(manager)

    # -- icons -------------------------------------------------------------
    def _build_icons(self) -> dict:
        frames = {}
        for rarity_key in ("rarity_1", "rarity_2", "rarity_3", "rarity_4", "rarity_birthday"):
            frames[rarity_key] = self.assets.icon_data_uri(f"frame_{rarity_key}.png")
        attrs = {}
        for attr_key in ("cute", "cool", "pure", "happy", "mysterious"):
            attrs[attr_key] = self.assets.icon_data_uri(f"attr_{attr_key}.png")
        stars = {
            "normal": self.assets.icon_data_uri("rare_star_normal.png"),
            "after": self.assets.icon_data_uri("rare_star_after_training.png"),
            "birthday": self.assets.icon_data_uri("rare_birthday.png"),
        }
        return {"frames": frames, "attrs": attrs, "stars": stars}

    # -- command entry -----------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        text, has_pjsk, region_key, has_region = self._normalize_prefix(
            event.message_obj.message_str
        )
        if not text:
            return
        match = self._match_command(text)
        if match is None:
            return
        kind, command, args = match

        # Commands that ordinary chat can trigger by accident require an
        # explicit pjsk or region prefix ("十连歪了", "300抽保底").
        if kind in {"gacha_sim", "gacha_reset"} and not (has_pjsk or has_region):
            return
        if command == "图鉴" and not (has_pjsk or has_region):
            return

        event.stop_event()
        try:
            if kind == "help":
                async for result in self._handle_help(event, region_key):
                    yield result
            elif kind == "status":
                async for result in self._handle_status(event):
                    yield result
            elif kind == "update":
                async for result in self._handle_update(event, force=command == "更新数据源"):
                    yield result
            elif kind == "fetch":
                async for result in self._handle_fetch(event, args):
                    yield result
            elif kind == "catalog":
                async for result in self._handle_catalog(event, region_key):
                    yield result
            elif kind in {"card", "card_list"}:
                async for result in self._handle_card(event, kind, command, args, region_key):
                    yield result
            elif kind == "card_art":
                async for result in self._handle_card_art(event, args, region_key):
                    yield result
            elif kind == "song":
                async for result in self._handle_song(event, args, region_key):
                    yield result
            elif kind == "jacket":
                async for result in self._handle_jacket(event, args, region_key):
                    yield result
            elif kind == "chart":
                async for result in self._handle_chart(event, args, region_key):
                    yield result
            elif kind == "constant":
                async for result in self._handle_constant(event, args, region_key):
                    yield result
            elif kind == "random":
                async for result in self._handle_random(event, args, region_key):
                    yield result
            elif kind == "notes":
                async for result in self._handle_notes(event, args, region_key):
                    yield result
            elif kind in {"gacha", "gacha_list", "gacha_sim", "gacha_reset"}:
                async for result in self._handle_gacha(event, kind, command, args, region_key):
                    yield result
            elif kind in {"event", "event_list"}:
                async for result in self._handle_event(event, kind, args, region_key):
                    yield result
        except RenderError as exc:
            logger.warning("[pjsk] render failed: %s", exc)
            yield event.plain_result(f"图片渲染失败：{exc}")
        except Exception as exc:  # noqa: BLE001 - never let a command crash the bot
            logger.exception("[pjsk] command failed: %s", exc)
            yield event.plain_result(f"查询失败：{exc}")

    def _normalize_prefix(self, raw: str) -> tuple[str, bool, str, bool]:
        """Strips an optional pjsk and region prefix.

        ``cn查卡`` / ``国服查卡`` select the mainland server, ``jp查卡`` the
        Japanese one, and a bare ``查卡`` uses the configured default (CN).
        Both prefixes may appear in either order.
        """
        text = unicodedata.normalize("NFKC", raw or "").strip()
        for prefix in _LEADING:
            if text.startswith(prefix):
                text = text[1:].strip()
                break
        has_pjsk = False
        has_region = False
        region_key = self.default_region
        aliases = sorted(REGION_ALIASES, key=len, reverse=True)
        for _ in range(2):
            lowered = text.lower()
            if lowered.startswith("pjsk"):
                has_pjsk = True
                text = text[4:].strip()
            else:
                matched = None
                for alias in aliases:
                    if not lowered.startswith(alias):
                        continue
                    rest = text[len(alias):]
                    if alias.isascii() and rest and rest[0].isascii() and (rest[0].isalnum() or rest[0] == "_"):
                        # Allow connected forms such as jpcard/jpsong, but not an
                        # arbitrary English word that merely starts with "cn"/"jp".
                        if not any(rest.startswith(word) for word in _ASCII_COMMANDS):
                            continue
                    matched = alias
                    break
                if matched is None:
                    break
                has_region = True
                region_key = REGION_ALIASES[matched]
                text = text[len(matched):].strip()
            # A separator may ride along with the prefix: ``pjsk/查卡`` or
            # ``jp.查卡`` behaves the same as ``pjsk查卡``.
            for leading in _LEADING:
                if text.startswith(leading):
                    text = text[1:].strip()
                    break
        return text, has_pjsk, region_key, has_region

    @staticmethod
    def _match_command(text: str) -> tuple[str, str, str] | None:
        # 卡牌一览 was removed; without this guard it would fall through to the
        # broad 卡牌 alias and return an empty filter page.
        if text.startswith("卡牌一览"):
            return None
        # pjsk100抽 / pjsk50连 have no literal command word.
        numeric = _NUMERIC_DRAW.match(text)
        if numeric:
            return "gacha_sim", numeric.group(0), text
        lowered = text.lower()
        for alias, kind in ALL_COMMANDS:
            if not lowered.startswith(alias.lower()):
                continue
            if alias.isascii():
                rest = lowered[len(alias):]
                if rest and (rest[0].isalnum() or rest[0] == "_"):
                    # IDs may be attached directly to an ASCII command
                    # (card123, song123); other words must not match (cardigan).
                    if not rest.split()[0].isdigit():
                        continue
            return kind, alias, text[len(alias):].strip()
        return None

    # -- shared helpers ----------------------------------------------------
    @staticmethod
    def _grid_columns(count: int) -> int:
        """3–5 columns that leave the fewest empty cells in a result grid."""
        if count <= 0:
            return 1
        if count <= 2:
            return count
        candidates = (3, 4, 5) if count <= 8 else (4, 5)
        return min(
            candidates,
            key=lambda cols: (((cols - count % cols) % cols), -cols),
        )

    def _manager(self, region_key: str) -> DataManager:
        return self.managers.get(region_key) or self.managers[self.default_region]

    def _base_context(
        self,
        command_label: str,
        accent: str,
        region_key: str | None = None,
        page_width: int = LIST_WIDTH,
    ) -> dict:
        key = region_key or self.default_region
        profile = REGIONS.get(key, REGIONS[self.default_region])
        default_profile = REGIONS[self.default_region]
        switch_key = "jp" if key == "cn" else "cn"
        manager = self._manager(key)
        is_default = key == self.default_region
        return {
            "brand": BRAND,
            "command_label": command_label,
            "accent": accent,
            "page_width": page_width,
            "page_title": f"PJSK · {command_label} · {profile.label} · {BRAND}",
            "update_time": manager.last_update,
            "data_version": manager.version,
            "icons": self.icons,
            "region_key": key,
            "region_label": profile.label,
            "region_short": profile.short,
            "region_prefix": "" if is_default else f"{key} ",
            # Commands that must not fire from ordinary chat (gacha, 图鉴)
            # need an explicit prefix even on the default region.
            "required_prefix": f"{key} " if not is_default else "pjsk",
            "is_default_region": is_default,
            "default_region_key": self.default_region,
            "default_region_label": default_profile.label,
            "switch_region_key": switch_key,
            "switch_region_label": REGIONS[switch_key].label,
        }

    async def _render(self, template: str, context: dict, image_type: str = "png", width: int = LIST_WIDTH):
        return await self.renderer.render(
            template,
            context,
            width=width,
            image_type=image_type,
            quality=LIST_JPEG_QUALITY,
            scale=LIST_SCALE,
        )

    async def _hydrate(self, *views: Any) -> None:
        slots: list = []
        for view in views:
            _image_slots(view, slots)
        if not slots:
            return
        tasks: dict[tuple[str, int], asyncio.Task] = {}
        for _, _, url, width in slots:
            tasks.setdefault((url, int(width)), asyncio.ensure_future(self.assets.data_uri(url, int(width))))
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        for container, key, url, width in slots:
            task = tasks.get((url, int(width)))
            if task is not None and task.done() and not task.cancelled() and task.exception() is None:
                container[key] = task.result() or ""
            else:
                container[key] = ""

    async def _dataset(self, region_key: str | None = None) -> tuple[Dataset | None, str]:
        manager = self._manager(region_key or self.default_region)
        label = manager.region.label
        try:
            dataset = await manager.ensure_loaded()
        except Exception as exc:  # noqa: BLE001
            logger.exception("[pjsk] dataset load failed")
            return None, f"{label}数据加载失败：{exc}"
        if not dataset.cards or not dataset.musics:
            # First run on a fresh install: wait for the core tables instead of
            # returning an empty page while the startup sync is still running.
            if not manager.has_data():
                try:
                    logger.info("[pjsk] %s has no local data; syncing on demand", manager.region.key)
                    await manager.sync(force=True, include_gacha=False)
                    dataset = await manager.reload()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("[pjsk] on-demand %s sync failed", manager.region.key)
                    return None, f"{label}数据同步失败：{exc}"
        if not dataset.cards or not dataset.musics:
            return None, f"{label}本地数据为空，请管理员发送「更新数据源」后重试"
        return dataset, ""

    async def _gacha_dataset(self, region_key: str | None = None) -> tuple[Dataset | None, str]:
        manager = self._manager(region_key or self.default_region)
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            return None, error
        try:
            await manager.ensure_gachas()
        except Exception as exc:  # noqa: BLE001
            logger.exception("[pjsk] gacha load failed")
            return None, f"{manager.region.label}卡池数据加载失败：{exc}"
        if not dataset.gachas:
            return None, f"{manager.region.label}卡池数据为空，请管理员发送「更新数据源」后重试"
        return dataset, ""

    # -- help / catalog / status ------------------------------------------
    async def _handle_help(self, event: AstrMessageEvent, region_key: str):
        context = self._base_context("帮助", KIND_ACCENTS["help"], region_key)
        path = await self._render("help.html", context, image_type="jpeg")
        yield event.image_result(str(path))

    async def _handle_catalog(self, event: AstrMessageEvent, region_key: str):
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return
        manager = self._manager(region_key)
        # The CN gacha table is small, so the overview can show its real count;
        # the JP table is 42 MB and stays strictly on-demand.
        if manager.region.key == "cn" and manager.has_gacha_data() and not dataset.gachas_loaded:
            try:
                await manager.ensure_gachas()
            except Exception:  # noqa: BLE001 - the overview works without it
                logger.exception("[pjsk] catalog gacha load failed")
        stats = catalog_stats(dataset)
        await self._hydrate(stats)
        context = self._base_context("图鉴", KIND_ACCENTS["catalog"], region_key)
        context["stats"] = stats
        path = await self._render("catalog.html", context, image_type="jpeg")
        yield event.image_result(str(path))

    async def _handle_status(self, event: AstrMessageEvent):
        async def collect(key: str, manager: DataManager) -> dict:
            rows = await manager.probe_sources()
            files = manager.local_file_status()
            for row in files:
                row["size_label"] = _size_label(row.get("size", 0))
            return {
                "key": key,
                "label": manager.region.label,
                "short": manager.region.short,
                "version": manager.version,
                "last_update": manager.last_update,
                "sources": rows,
                "files": files,
                "ok_count": sum(1 for row in files if row.get("exists")),
                "total_count": len(files),
                "is_default": key == self.default_region,
            }

        regions = await asyncio.gather(
            *(collect(key, manager) for key, manager in self.managers.items())
        )
        context = self._base_context("数据源状态", KIND_ACCENTS["status"])
        context.update(regions=regions, default_region=self.default_region)
        path = await self._render("status.html", context, image_type="jpeg")
        yield event.image_result(str(path))

    # -- data management ---------------------------------------------------
    @staticmethod
    def _is_admin(event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_admin", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:  # noqa: BLE001 - older event implementations
                return False
        # Some AstrBot releases expose it as a plain property.
        if isinstance(checker, bool):
            return checker
        return False

    async def _handle_update(self, event: AstrMessageEvent, force: bool):
        if force and not self._is_admin(event):
            yield event.plain_result("只有管理员可以强制刷新数据。")
            return
        yield event.plain_result("正在检查数据更新…" if not force else "正在强制刷新数据…")
        lines = []
        for key, manager in self.managers.items():
            # The JP gacha table is 42 MB; an ordinary check only refreshes it
            # when a copy already exists (or the admin forces a refresh).
            include_gacha = force or manager.region.key == "cn" or manager.has_gacha_data()
            try:
                status = await manager.sync(force=force, include_gacha=include_gacha)
            except Exception as exc:  # noqa: BLE001
                lines.append(f"【{manager.region.label}】更新失败：{exc}")
                continue
            lines.append(f"【{manager.region.label}】{self._update_status_text(status)}")
        yield event.plain_result("\n".join(lines))

    @staticmethod
    def _update_status_text(status: dict) -> str:
        if status.get("skipped"):
            return f"数据已是最新版本（{status.get('version') or '未知'}）"
        if status.get("all_sources_failed"):
            return "所有远程数据源均不可用，本次未更新；继续使用本地缓存。可发送「检查数据源」查看详情。"
        if status.get("updated"):
            return f"数据已更新到版本 {status.get('version') or '未知'}。"
        return "刷新完成，但没有取得新数据。"

    async def _handle_fetch(self, event: AstrMessageEvent, args: str):
        if not self._is_admin(event):
            yield event.plain_result("只有管理员可以手动拉取数据文件。")
            return
        parts = args.split()
        region_key = self.default_region
        if parts and parts[0].lower() in REGION_ALIASES:
            region_key = REGION_ALIASES[parts.pop(0).lower()]
        filename = " ".join(parts).strip()
        if not filename:
            yield event.plain_result("用法：拉取 cards.json 或 拉取 jp cards.json")
            return
        manager = self._manager(region_key)
        yield event.plain_result(f"正在从{manager.region.label}拉取 {filename} …")
        ok, message = await manager.manual_fetch(filename)
        yield event.plain_result(("✅ " if ok else "❌ ") + message)

    # -- cards -------------------------------------------------------------
    async def _handle_card(
        self,
        event: AstrMessageEvent,
        kind: str,
        command: str,
        args: str,
        region_key: str,
    ):
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return

        query = args.strip().lstrip("#＃").strip()
        if not query:
            resolved = SimpleNamespace(items=[], total=0, suggestions=[])
            context = self._base_context("卡牌筛选", KIND_ACCENTS["card"], region_key)
            context.update(
                resolved=resolved,
                title="卡牌筛选",
                subtitle="角色昵称 · 团名(纯团/团VS) · 稀有度 · 属性 · 限定 · 技能(团分/p分/大分…) · 年份 · 活动(event123)",
                columns=1,
                limit_note="",
                guide=True,
            )
            path = await self._render("card_list.html", context, image_type="jpeg")
            yield event.image_result(str(path))
            return

        if query.isdigit():
            card = dataset.cards_by_id.get(int(query))
            if not card:
                yield event.plain_result(f"未找到卡牌 ID {query}")
                return
            async for result in self._send_card_detail(event, dataset, card, region_key):
                yield result
            return

        card, index_error = find_card_by_character_index(dataset, query)
        if index_error:
            yield event.plain_result(index_error)
            return
        if card is not None:
            async for result in self._send_card_detail(event, dataset, card, region_key):
                yield result
            return

        filters = parse_card_filters(dataset, query)
        if filters.get("event_error"):
            yield event.plain_result(filters["event_error"])
            return
        cards = filter_cards(dataset, filters)
        total = len(cards)
        if not cards:
            resolved = SimpleNamespace(items=[], total=0, suggestions=[])
            context = self._base_context("卡牌索引", KIND_ACCENTS["card"], region_key)
            context.update(
                resolved=resolved,
                title=self._card_title(dataset, filters),
                subtitle="没有找到符合条件的卡牌",
                columns=1,
                limit_note="",
            )
            path = await self._render("card_list.html", context, image_type="jpeg")
            yield event.image_result(str(path))
            return

        if len(cards) == 1:
            async for result in self._send_card_detail(event, dataset, cards[0], region_key):
                yield result
            return

        items = [build_card_item(dataset, card) for card in cards]
        await self._hydrate(items)
        context = self._base_context("卡牌索引", KIND_ACCENTS["card"], region_key)
        context.update(
            resolved=SimpleNamespace(items=items, total=total, suggestions=[]),
            title=self._card_title(dataset, filters),
            subtitle="按发布时间从新到旧 · 点开详情查看技能与卡面",
            columns=5 if total > 300 else self._grid_columns(len(items)),
            limit_note="",
        )
        path = await self._render("card_list.html", context, image_type="jpeg")
        yield event.image_result(str(path))

    @staticmethod
    def _card_title(dataset, filters: dict) -> str:
        parts = []
        if filters.get("character_id"):
            parts.append(character_view(dataset, filters["character_id"])["full_name"])
        if filters.get("unit"):
            short = unit_view(filters["unit"])["short"]
            mode = filters.get("unit_mode")
            if mode == "oc":
                parts.append(f"{short}成员")
            elif mode == "vs":
                parts.append("纯VS" if filters["unit"] == "piapro" else f"{short}版VS")
            else:
                parts.append(short)
        if filters.get("rarity"):
            parts.append(rarity_view(filters["rarity"])["label"])
        if filters.get("attr"):
            parts.append(attr_view(filters["attr"])["label"])
        if filters.get("supply"):
            parts.append(SUPPLY_TYPES.get(filters["supply"], {}).get("label", filters["supply"]))
        if filters.get("skill"):
            parts.append(SKILL_TYPES.get(filters["skill"], {}).get("label", filters["skill"]))
        if filters.get("skill_label"):
            parts.append(filters["skill_label"])
        event_id = filters.get("event_id")
        if event_id is not None:
            event = dataset.events_by_id.get(int(event_id))
            parts.append(f"活动「{(event or {}).get('name', event_id)}」")
        if filters.get("leak"):
            parts.append("未实装")
        if filters.get("year"):
            parts.append(f"{filters['year']} 年")
        if filters.get("keyword"):
            parts.append(f"「{filters['keyword']}」")
        return " ".join(part for part in parts if part) or "全部卡牌"

    async def _send_card_detail(
        self,
        event: AstrMessageEvent,
        dataset: Dataset,
        card: dict,
        region_key: str,
    ):
        manager = self._manager(region_key)
        # CN's gacha table is small; load it so the card page can show the
        # related pool. JP stays strictly on demand (28 MB).
        if manager.region.key == "cn" and manager.has_gacha_data() and not dataset.gachas_loaded:
            try:
                await manager.ensure_gachas()
            except Exception:  # noqa: BLE001 - the detail works without it
                logger.exception("[pjsk] card gacha load failed")
        view = build_card(dataset, card)
        await self._hydrate(view)
        context = self._base_context("卡牌详情", KIND_ACCENTS["card"], region_key)
        context["card"] = view
        path = await self._render("card_detail.html", context)
        yield event.image_result(str(path))

    async def _handle_card_art(self, event: AstrMessageEvent, args: str, region_key: str):
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return
        query = args.strip().lstrip("#＃").strip()
        if not query:
            yield event.plain_result("用法：card 123（卡牌 ID）")
            return
        if not query.isdigit():
            yield event.plain_result("card 只支持卡牌 ID，例如 card 123")
            return

        card = dataset.cards_by_id.get(int(query))
        if not card:
            yield event.plain_result(f"未找到卡牌 ID {query}")
            return
        async for result in self._send_card_art(event, dataset, card):
            yield result

    async def _send_card_art(self, event: AstrMessageEvent, dataset: Dataset, card: dict):
        character = character_view(dataset, int(card.get("characterId", 0)))
        rarity = rarity_view(card.get("cardRarityType", ""))
        asset = card.get("assetbundleName", "")

        caption = f"#{card.get('id')} {rarity['label']} {character['full_name']} · {card.get('prefix', '')}"
        base = dataset.asset_base
        has_after = rarity["key"] not in {"rarity_1", "rarity_2", "rarity_birthday"}
        async for result in self._send_art(
            event, card_art_url(asset, False, base), caption + (" · 花前" if has_after else " · 卡面")
        ):
            yield result
        if has_after:
            async for result in self._send_art(event, card_art_url(asset, True, base), f"#{card.get('id')} · 花后"):
                yield result

    async def _send_art(self, event: AstrMessageEvent, url: str, caption: str):
        path = await self.assets.fetch_file(url)
        if path is not None:
            yield event.chain_result([Plain(caption), Image.fromFileSystem(str(path))])
            return
        yield event.chain_result([Plain(caption), Image.fromURL(url)])

    # -- songs -------------------------------------------------------------
    async def _handle_song(self, event: AstrMessageEvent, args: str, region_key: str):
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return
        diff, rest = extract_difficulty(args)
        if not rest:
            async for result in self._send_level_index(event, dataset, diff, region_key):
                yield result
            return

        parts = rest.split()
        if all(part.isdigit() for part in parts) and len(parts) <= 2:
            numbers = [int(part) for part in parts]
            low = numbers[0] if len(numbers) == 1 else min(numbers)
            high = numbers[0] if len(numbers) == 1 else max(numbers)
            # A plausible chart level means a level listing; a larger number is
            # a song ID ("查曲 123 EX" must open the song, not level 123).
            if 1 <= low <= 40 and 1 <= high <= 40:
                async for result in self._send_song_level_list(event, dataset, diff, low, high, region_key):
                    yield result
                return

        resolved = search_songs(dataset, rest)
        if resolved.mode == "detail" and resolved.item is not None:
            view = build_song(dataset, resolved.item, diff)
            await self._hydrate(view)
            context = self._base_context("歌曲详情", KIND_ACCENTS["song"], region_key)
            context["song"] = view
            path = await self._render("song_detail.html", context)
            yield event.image_result(str(path))
            return
        if resolved.mode == "list":
            items = [build_song_item(dataset, music, diff) for music in resolved.items]
            await self._hydrate(items)
            context = self._base_context("歌曲索引", KIND_ACCENTS["song"], region_key)
            context.update(
                resolved=SimpleNamespace(items=items, total=resolved.total, suggestions=resolved.suggestions),
                title=f"搜索「{resolved.query}」",
                subtitle="按匹配度排序 · 点开详情查看难度与演唱版本",
                columns=5 if len(items) > 20 else self._grid_columns(len(items)),
                limit_note=f"匹配到 {resolved.total} 首，仅展示前 {len(items)} 首。" if resolved.total > len(items) else "",
            )
            path = await self._render("song_list.html", context, image_type="jpeg")
            yield event.image_result(str(path))
            return
        yield event.plain_result(resolved.error or "未找到匹配的歌曲")

    async def _send_level_index(
        self,
        event: AstrMessageEvent,
        dataset: Dataset,
        diff: str,
        region_key: str,
    ):
        counts: dict[int, int] = {}
        for entry in dataset.difficulties:
            if entry.get("musicDifficulty") != diff:
                continue
            level = int(entry.get("playLevel", 0) or 0)
            counts[level] = counts.get(level, 0) + 1
        total = sum(counts.values())
        if not counts:
            yield event.plain_result(f"{DIFFICULTY_LABELS.get(diff, diff)} 暂无歌曲")
            return
        maximum = max(counts.values()) or 1
        levels = [
            {"level": level, "count": count, "width": round(count / maximum * 100)}
            for level, count in sorted(counts.items())
        ]
        tabs = []
        for key in DIFFICULTY_ORDER:
            count = sum(1 for entry in dataset.difficulties if entry.get("musicDifficulty") == key)
            tabs.append(
                {
                    "key": key,
                    "label": DIFFICULTY_LABELS.get(key, key.upper()),
                    "color": DIFFICULTY_COLORS.get(key, "#8A939E"),
                    "count": count,
                    "selected": key == diff,
                }
            )
        context = self._base_context("难度索引", KIND_ACCENTS["song"], region_key)
        context.update(
            diff={
                "key": diff,
                "label": DIFFICULTY_LABELS.get(diff, diff.upper()),
                "short": DIFFICULTY_SHORT.get(diff, diff[:2].upper()),
                "color": DIFFICULTY_COLORS.get(diff, "#8A939E"),
            },
            levels=levels,
            total=total,
            difficulty_tabs=tabs,
        )
        path = await self._render("level_index.html", context, image_type="jpeg")
        yield event.image_result(str(path))

    async def _send_song_level_list(
        self,
        event: AstrMessageEvent,
        dataset: Dataset,
        diff: str,
        low: int,
        high: int,
        region_key: str,
    ):
        items = []
        for music in dataset.musics:
            entry = dataset.diffs_by_music.get(int(music.get("id", 0)), {}).get(diff)
            if not entry:
                continue
            level = int(entry.get("playLevel", 0) or 0)
            if level < low or level > high:
                continue
            items.append(build_song_item(dataset, music, diff))
        if not items:
            yield event.plain_result(f"没有找到 {DIFFICULTY_LABELS.get(diff, diff)} {low}–{high} 级的歌曲")
            return
        items.sort(key=lambda item: (self._selected_level(item), item["title"]))
        total = len(items)
        items = items[:SONG_LIST_LIMIT]
        await self._hydrate(items)
        label = DIFFICULTY_LABELS.get(diff, diff.upper())
        range_label = f"{low} 级" if low == high else f"{low}–{high} 级"
        context = self._base_context("歌曲索引", KIND_ACCENTS["song"], region_key)
        context.update(
            resolved=SimpleNamespace(items=items, total=total, suggestions=[]),
            title=f"{label} {range_label}",
            subtitle="按等级与曲名排序 · 点开详情查看演唱版本与定数",
            columns=5 if len(items) > 20 else self._grid_columns(len(items)),
            limit_note=f"共 {total} 首，仅展示前 {len(items)} 首。" if total > len(items) else "",
        )
        path = await self._render("song_list.html", context, image_type="jpeg")
        yield event.image_result(str(path))

    @staticmethod
    def _selected_level(item: dict) -> int:
        for entry in item.get("difficulties", []):
            if entry.get("selected"):
                return int(entry.get("level", 0) or 0)
        difficulties = item.get("difficulties", [])
        return int(difficulties[0].get("level", 0) or 0) if difficulties else 0

    async def _handle_random(self, event: AstrMessageEvent, args: str, region_key: str):
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return
        if not args.strip().isdigit():
            yield event.plain_result("用法：随个 32（按难度等级随机）")
            return
        level = int(args.strip())
        entries = [
            entry
            for entry in dataset.difficulties_by_level.get(level, [])
            if int(entry.get("musicId", 0) or 0) in dataset.musics_by_id
        ]
        if not entries:
            yield event.plain_result(f"没有找到等级 {level} 的歌曲")
            return
        entry = _rng.choice(entries)
        music = dataset.musics_by_id.get(int(entry.get("musicId", 0)))
        if not music:
            yield event.plain_result("随机到的歌曲数据缺失")
            return
        view = build_song(dataset, music, entry.get("musicDifficulty"))
        await self._hydrate(view)
        context = self._base_context("随机歌曲", KIND_ACCENTS["song"], region_key)
        context["song"] = view
        path = await self._render("song_detail.html", context)
        yield event.image_result(str(path))

    async def _handle_notes(self, event: AstrMessageEvent, args: str, region_key: str):
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return
        if not args.strip().isdigit():
            yield event.plain_result("用法：查物量 1000（按 note 数查找）")
            return
        notes = int(args.strip())
        entries = dataset.difficulties_by_notes.get(notes, [])
        if not entries:
            yield event.plain_result(f"没有找到物量为 {notes} 的歌曲")
            return
        items = []
        seen = set()
        for entry in entries:
            music = dataset.musics_by_id.get(int(entry.get("musicId", 0)))
            if not music or int(music["id"]) in seen:
                continue
            seen.add(int(music["id"]))
            items.append(build_song_item(dataset, music, entry.get("musicDifficulty")))
        items.sort(key=lambda item: item["title"])
        await self._hydrate(items)
        context = self._base_context("物量查询", KIND_ACCENTS["song"], region_key)
        context.update(
            resolved=SimpleNamespace(items=items, total=len(items), suggestions=[]),
            title=f"物量 {notes}",
            subtitle=f"共 {len(items)} 首歌曲的某个难度物量为 {notes}",
            columns=5 if len(items) > 20 else self._grid_columns(len(items)),
            limit_note="",
        )
        path = await self._render("song_list.html", context, image_type="jpeg")
        yield event.image_result(str(path))

    async def _handle_jacket(self, event: AstrMessageEvent, args: str, region_key: str):
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return
        query = args.strip().lstrip("#＃").strip()
        if not query:
            yield event.plain_result("用法：查曲绘 123 或 查曲绘 Tell Your World")
            return
        if query.isdigit():
            music = dataset.musics_by_id.get(int(query))
            if not music:
                yield event.plain_result(f"未找到歌曲 ID {query}")
                return
        else:
            resolved = search_songs(dataset, query)
            if resolved.mode == "detail" and resolved.item is not None:
                music = resolved.item
            elif resolved.mode == "list":
                items = [build_song_item(dataset, entry) for entry in resolved.items]
                await self._hydrate(items)
                context = self._base_context("歌曲索引", KIND_ACCENTS["song"], region_key)
                context.update(
                    resolved=SimpleNamespace(items=items, total=resolved.total, suggestions=[]),
                    title=f"搜索「{resolved.query}」",
                    subtitle="匹配到多首歌曲，请用更准确的名字或 ID",
                    columns=5 if len(items) > 20 else self._grid_columns(len(items)),
                    limit_note="",
                )
                path = await self._render("song_list.html", context, image_type="jpeg")
                yield event.image_result(str(path))
                return
            else:
                yield event.plain_result(resolved.error or "未找到匹配的歌曲")
                return
        base = dataset.asset_base
        url = jacket_url(music.get("assetbundleName", ""), base)
        path = await self.assets.fetch_file(url)
        if path is not None:
            yield event.chain_result([Plain(f"#{music.get('id')} {music.get('title', '')} · 曲绘"), Image.fromFileSystem(str(path))])
            return
        yield event.image_result(url)

    async def _handle_chart(self, event: AstrMessageEvent, args: str, region_key: str):
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return
        diff, rest = extract_difficulty(args)
        rest = rest.strip().lstrip("#＃").strip()
        if not rest:
            yield event.plain_result("用法：谱面 825 master、谱面 世界 或 谱面 虾 ex")
            return
        if rest.isdigit():
            music = dataset.musics_by_id.get(int(rest))
            if music is None:
                yield event.plain_result(f"未找到歌曲 ID {rest}")
                return
        else:
            resolved = search_songs(dataset, rest)
            if resolved.mode == "detail" and resolved.item is not None:
                music = resolved.item
            elif resolved.mode == "list":
                items = [build_song_item(dataset, entry) for entry in resolved.items]
                await self._hydrate(items)
                context = self._base_context("歌曲索引", KIND_ACCENTS["song"], region_key)
                context.update(
                    resolved=SimpleNamespace(items=items, total=resolved.total, suggestions=[]),
                    title=f"搜索「{resolved.query}」",
                    subtitle="匹配到多首歌曲，请用更准确的名字或 ID",
                    columns=5 if len(items) > 20 else self._grid_columns(len(items)),
                    limit_note="",
                )
                path = await self._render("song_list.html", context, image_type="jpeg")
                yield event.image_result(str(path))
                return
            else:
                yield event.plain_result(resolved.error or "未找到匹配的歌曲")
                return
        music_id = int(music.get("id", 0))
        entry = dataset.diffs_by_music.get(music_id, {}).get(diff)
        if entry is None:
            yield event.plain_result(f"该曲没有 {DIFFICULTY_LABELS.get(diff, diff)} 谱面")
            return
        # The chart host only publishes SVGs; fetch_bytes enforces the
        # public-URL rules before any request leaves the process.
        raw = await self.assets.fetch_bytes(chart_svg_url(music_id, diff))
        if not raw:
            yield event.plain_result("谱面站暂无该曲谱面（谱面数据以日服为准，请稍后再试）")
            return
        svg = raw.decode("utf-8", "replace")
        hrefs = svg_image_urls(svg)
        if hrefs:
            # Note sprites and the bottom-left jacket are external images, and
            # an SVG embedded as a data URI cannot load them; bake each one in
            # as a (downscaled, cached) data URI instead.
            tasks = {
                href: asyncio.ensure_future(
                    self.assets.data_uri(
                        chart_asset_url(href),
                        128 if "notes_new" in href else 360,
                    )
                )
                for href in hrefs
            }
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            replacements = {
                href: task.result()
                for href, task in tasks.items()
                if task.done() and not task.cancelled() and task.exception() is None and task.result()
            }
            svg = inline_svg_images(svg, replacements)
        width, height = svg_dimensions(svg)
        width = width or 1600
        height = height or 800
        data_uri = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
        path = await self.renderer.service.render(
            preview_document(data_uri, width, height),
            width=width,
            image_type="png",
            quality=90,
            scale="normal",
        )
        yield event.image_result(str(path))

    async def _handle_constant(self, event: AstrMessageEvent, args: str, region_key: str):
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return
        diff, rest = extract_difficulty(args, default="")
        rest = rest.strip()
        if not diff:
            yield event.plain_result("用法：查定数 master 32、查定数 master 30-32（难度必填）")
            return
        low, high = parse_constant_range(rest)
        low_level = high_level = None
        if low is not None:
            # Official playLevel is an integer; a parsed range such as 32 →
            # [32, 32.99] collapses to the levels it can actually match.
            low_level, high_level = math.ceil(low - 1e-6), math.floor(high + 1e-6)
            if low_level > high_level:
                yield event.plain_result(
                    f"难度等级是整数，{DIFFICULTY_LABELS.get(diff, diff)} 没有 {rest} 这一等级"
                )
                return
        entries = filter_songs_by_level(dataset, diff, low_level, high_level)
        if not entries:
            wanted = "任意等级" if low is None else f"{rest} 级"
            yield event.plain_result(f"没有找到 {DIFFICULTY_LABELS.get(diff, diff)} {wanted} 的歌曲")
            return
        total = len(entries)
        items = [build_song_item(dataset, music, diff) for _, music in entries[:SONG_LIST_LIMIT]]
        await self._hydrate(items)
        label = DIFFICULTY_LABELS.get(diff, diff.upper())
        if low is None:
            title, subtitle = f"{label} 等级表", "按等级降序 · 等级取自官方 MasterData"
        elif low_level == high_level:
            title, subtitle = f"{label} {low_level} 级", "同等级内按曲名排序 · 等级取自官方 MasterData"
        else:
            title, subtitle = f"{label} {low_level}–{high_level} 级", "按等级降序 · 等级取自官方 MasterData"
        context = self._base_context("定数查询", KIND_ACCENTS["song"], region_key)
        context.update(
            resolved=SimpleNamespace(items=items, total=total, suggestions=[]),
            title=title,
            subtitle=subtitle,
            columns=5 if len(items) > 20 else self._grid_columns(len(items)),
            limit_note=f"共 {total} 首，仅展示前 {len(items)} 首。" if total > len(items) else "",
        )
        path = await self._render("song_list.html", context, image_type="jpeg")
        yield event.image_result(str(path))

    # -- gacha -------------------------------------------------------------
    @staticmethod
    def _gacha_priority(gacha: dict) -> int:
        now = int(time.time() * 1000)
        start = int(gacha.get("startAt", 0) or 0)
        end = int(gacha.get("endAt", 0) or 0)
        if start <= now <= end:
            return 0
        return 1 if now < start else 2

    @staticmethod
    def _event_priority(event: dict) -> int:
        now = int(time.time() * 1000)
        start = int(event.get("startAt", 0) or 0)
        end = int(event.get("aggregateAt", 0) or 0)
        if start <= now <= end:
            return 0
        return 1 if now < start else 2

    async def _handle_gacha(
        self,
        event: AstrMessageEvent,
        kind: str,
        command: str,
        args: str,
        region_key: str,
    ):
        dataset, error = await self._gacha_dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return

        if kind == "gacha_list":
            gachas = sorted(
                dataset.gachas,
                key=lambda gacha: (self._gacha_priority(gacha), -int(gacha.get("startAt", 0) or 0)),
            )
            items = [build_gacha_item(dataset, gacha) for gacha in gachas[:GACHA_SEARCH_LIMIT]]
            await self._hydrate(items)
            context = self._base_context("卡池列表", KIND_ACCENTS["gacha"], region_key)
            context.update(
                resolved=SimpleNamespace(items=items, total=len(gachas), suggestions=[]),
                title="最近卡池",
                columns=3,
                limit_note=f"共 {len(gachas)} 个卡池，展示最近 {len(items)} 个。" if len(gachas) > len(items) else "",
            )
            path = await self._render("gacha_list.html", context, image_type="jpeg")
            yield event.image_result(str(path))
            return

        if kind == "gacha":
            resolved = search_gachas(dataset, args)
            if resolved.mode == "detail" and resolved.item is not None:
                view = build_gacha(dataset, resolved.item)
                await self._hydrate(view)
                context = self._base_context("卡池详情", KIND_ACCENTS["gacha"], region_key)
                context["gacha"] = view
                path = await self._render("gacha_detail.html", context, image_type="jpeg")
                yield event.image_result(str(path))
                return
            if resolved.mode == "list":
                items = [build_gacha_item(dataset, gacha) for gacha in resolved.items[:GACHA_SEARCH_LIMIT]]
                await self._hydrate(items)
                context = self._base_context("卡池搜索", KIND_ACCENTS["gacha"], region_key)
                context.update(
                    resolved=SimpleNamespace(items=items, total=resolved.total, suggestions=[]),
                    title=f"搜索「{resolved.query}」",
                    columns=3,
                    limit_note=f"匹配到 {resolved.total} 个卡池，仅展示前 {len(items)} 个。" if resolved.total > len(items) else "",
                )
                path = await self._render("gacha_list.html", context, image_type="jpeg")
                yield event.image_result(str(path))
                return
            yield event.plain_result(resolved.error or "未找到匹配的卡池")
            return

        if kind == "gacha_reset":
            gacha, message = self._resolve_gacha_for_sim(dataset, args.strip())
            if gacha is None:
                yield event.plain_result(message)
                return
            cleared = await self.gacha_store.reset(
                self._user_id(event), int(gacha["id"]), region=region_key
            )
            if cleared:
                yield event.plain_result(f"已重置「{gacha.get('name', '')}」#{gacha['id']} 的模拟抽卡记录。")
            else:
                yield event.plain_result(f"「{gacha.get('name', '')}」#{gacha['id']} 还没有模拟抽卡记录。")
            return

        # gacha_sim
        default_count = 1 if command == "单抽" else 10
        count, character_id, gacha_id, requested = parse_draw_args(dataset, args, default_count=default_count)
        gacha = None
        if gacha_id is not None:
            gacha = dataset.gachas_by_id.get(gacha_id)
            if gacha is None:
                yield event.plain_result(f"未找到卡池 ID {gacha_id}")
                return
        else:
            remainder = args.strip()
            if remainder and not remainder.isdigit() and not dataset.alias_to_character.get(remainder.split()[0].lower()):
                resolved = search_gachas(dataset, remainder)
                if resolved.mode == "detail" and resolved.item is not None:
                    gacha = resolved.item
            if gacha is None:
                gacha = resolve_gacha_default(dataset)
        if gacha is None:
            yield event.plain_result("暂无卡池数据")
            return

        results = perform_draw(dataset, gacha, count, character_id)
        if not results:
            yield event.plain_result(f"「{gacha.get('name', '')}」暂无可抽取内容")
            return

        pickup_ids = {
            int(pickup["cardId"])
            for pickup in (gacha.get("gachaPickups") or [])
            if pickup.get("cardId")
        }
        stats = await self.gacha_store.record(
            self._user_id(event), int(gacha["id"]), results, pickup_ids, region=region_key
        )
        views = []
        base = dataset.asset_base
        for card in results:
            rarity = rarity_view(card.get("cardRarityType", ""))
            character = character_view(dataset, int(card.get("characterId", 0)))
            has_after = rarity["key"] not in {"rarity_1", "rarity_2", "rarity_birthday"}

            views.append(
                {
                    "id": int(card.get("id", 0)),
                    "title": card.get("prefix") or "—",
                    "character": character,
                    "rarity": rarity,
                    "is_pickup": int(card.get("id", 0)) in pickup_ids,
                    "images": {
                        "art": (
                            card_art_url(card.get("assetbundleName", ""), has_after, base),
                            320,
                        )
                    },
                }
            )
        await self._hydrate(views)
        char_name = ""
        if character_id is not None:
            char_name = character_view(dataset, character_id)["full_name"]
        context = self._base_context("模拟抽卡", KIND_ACCENTS["gacha"], region_key)
        context.update(
            gacha={"id": int(gacha["id"]), "name": gacha.get("name", "—")},
            results=views,
            stats=stats,
            count=len(results),
            requested=requested,
            char_name=char_name,
        )
        path = await self._render("gacha_result.html", context, image_type="jpeg")
        yield event.image_result(str(path))

    def _resolve_gacha_for_sim(self, dataset: Dataset, text: str) -> tuple[dict | None, str]:
        text = text.strip().lstrip("#＃").strip()
        if not text:
            gacha = resolve_gacha_default(dataset)
            return (gacha, "") if gacha else (None, "暂无卡池数据")
        if text.isdigit():
            gacha = dataset.gachas_by_id.get(int(text))
            return (gacha, "") if gacha else (None, f"未找到卡池 ID {text}")
        resolved = search_gachas(dataset, text)
        if resolved.mode == "detail" and resolved.item is not None:
            return resolved.item, ""
        if resolved.mode == "list":
            names = "、".join(gacha.get("name", "") for gacha in resolved.items[:5])
            return None, f"匹配到多个卡池：{names}。请用更准确的名字或 ID。"
        return None, resolved.error or f"未找到匹配「{text}」的卡池"

    @staticmethod
    def _user_id(event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_sender_id", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value)
            except Exception:  # noqa: BLE001 - older event implementations
                pass
        return "default"

    # -- events ------------------------------------------------------------
    async def _handle_event(self, event: AstrMessageEvent, kind: str, args: str, region_key: str):
        dataset, error = await self._dataset(region_key)
        if dataset is None:
            yield event.plain_result(error)
            return

        if kind == "event_list":
            events = sorted(
                dataset.events,
                key=lambda item: (self._event_priority(item), -int(item.get("startAt", 0) or 0)),
            )
            events = events[:EVENT_LIST_LIMIT]
            items = [build_event_item(dataset, entry) for entry in events]
            await self._hydrate(items)
            context = self._base_context("活动一览", KIND_ACCENTS["event"], region_key)
            context.update(
                resolved=SimpleNamespace(items=items, total=len(dataset.events), suggestions=[]),
                title="最近活动",
                limit_note=f"共 {len(dataset.events)} 个活动，展示最近 {len(items)} 个。" if len(dataset.events) > len(items) else "",
            )
            path = await self._render("event_list.html", context, image_type="jpeg")
            yield event.image_result(str(path))
            return

        resolved = search_events(dataset, args)
        if resolved.mode == "detail" and resolved.item is not None:
            view = build_event(dataset, resolved.item)
            await self._hydrate(view)
            context = self._base_context("活动详情", KIND_ACCENTS["event"], region_key)
            context["event"] = view
            path = await self._render("event_detail.html", context, image_type="jpeg")
            yield event.image_result(str(path))
            return
        if resolved.mode == "list":
            items = [build_event_item(dataset, entry) for entry in resolved.items]
            await self._hydrate(items)
            context = self._base_context("活动搜索", KIND_ACCENTS["event"], region_key)
            context.update(
                resolved=SimpleNamespace(items=items, total=resolved.total, suggestions=[]),
                title=f"搜索「{resolved.query}」",
                limit_note="",
            )
            path = await self._render("event_list.html", context, image_type="jpeg")
            yield event.image_result(str(path))
            return
        yield event.plain_result(resolved.error or "未找到匹配的活动")

# -*- coding: utf-8 -*-
"""MasterData loading, indexing and multi-source synchronisation."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from .constants import (
    CORE_FILES,
    GACHA_FILE,
    LOCAL_FILES,
    MUSIC_ALIAS_FILE,
    MUSIC_ALIAS_URL,
    MUSIC_TRANS_FILE,
    MUSIC_TRANS_URL,
    REMOTE_FILES,
    SONG_CONSTANTS_FILE,
    SONG_CONSTANTS_URL,
    RegionProfile,
    region_sources,
)

logger = logging.getLogger("astrbot")


def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except Exception as exc:  # noqa: BLE001 - a corrupt cache must not stop the plugin
        logger.warning("[pjsk] cannot read %s: %s", path, exc)
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(path)


@dataclass
class Dataset:
    region_key: str = ""
    asset_base: str = ""
    cards: list = field(default_factory=list)
    musics: list = field(default_factory=list)
    difficulties: list = field(default_factory=list)
    events: list = field(default_factory=list)
    gachas: list = field(default_factory=list)
    characters: list = field(default_factory=list)
    skills: list = field(default_factory=list)
    supplies: list = field(default_factory=list)
    event_cards: list = field(default_factory=list)
    event_musics: list = field(default_factory=list)
    event_deck_bonuses: list = field(default_factory=list)
    game_character_units: list = field(default_factory=list)
    music_vocals: list = field(default_factory=list)
    costumes: list = field(default_factory=list)
    outside_characters_raw: list = field(default_factory=list)
    music_aliases_raw: Any = field(default_factory=list)
    music_translations: dict = field(default_factory=dict)
    song_constants: dict = field(default_factory=dict)

    # indexes
    cards_by_id: dict = field(default_factory=dict)
    cards_by_character: dict = field(default_factory=dict)
    characters_by_id: dict = field(default_factory=dict)
    alias_to_character: dict = field(default_factory=dict)
    unit_by_character: dict = field(default_factory=dict)
    character_id_by_unit: dict = field(default_factory=dict)
    skills_by_id: dict = field(default_factory=dict)
    supplies_by_id: dict = field(default_factory=dict)
    musics_by_id: dict = field(default_factory=dict)
    musics_sorted: list = field(default_factory=list)
    diffs_by_music: dict = field(default_factory=dict)
    difficulties_by_level: dict = field(default_factory=dict)
    difficulties_by_notes: dict = field(default_factory=dict)
    vocals_by_music: dict = field(default_factory=dict)
    events_by_id: dict = field(default_factory=dict)
    event_cards_by_event: dict = field(default_factory=dict)
    event_deck_bonuses_by_event: dict = field(default_factory=dict)
    event_by_card_id: dict = field(default_factory=dict)
    event_to_music: dict = field(default_factory=dict)
    music_to_event: dict = field(default_factory=dict)
    gachas_by_id: dict = field(default_factory=dict)
    gacha_by_card_id: dict = field(default_factory=dict)
    costumes_by_card_id: dict = field(default_factory=dict)
    outside_characters: dict = field(default_factory=dict)
    aliases_by_music: dict = field(default_factory=dict)
    alias_to_music: dict = field(default_factory=dict)
    song_titles: dict = field(default_factory=dict)
    current_gacha: dict | None = None
    gacha_count: int = 0
    gachas_loaded: bool = False

    # -- helpers used by models.py ----------------------------------------
    def supply_type(self, card: dict) -> str:
        supply = self.supplies_by_id.get(card.get("cardSupplyId", 0))
        return (supply or {}).get("cardSupplyType", "normal") or "normal"

    def skill_type(self, card: dict) -> str:
        return self.skill_category(self.skills_by_id.get(card.get("skillId", 0)))

    def skill_category(self, skill: dict | None) -> str:
        # descriptionSpriteName is the game's own skill icon category. Hybrid
        # skills (判+分, 奶+分) list score_up first in skillEffects, so taking
        # the first effect would misfile every 判 card under 分.
        kind = (skill or {}).get("descriptionSpriteName") or ""
        if kind:
            return kind
        for effect in (skill or {}).get("skillEffects", []):
            kind = effect.get("skillEffectType")
            if kind:
                return kind
        return ""

    def event_for_card(self, card_id: int):
        return self.event_by_card_id.get(int(card_id))

    def gacha_for_card(self, card_id: int):
        return self.gacha_by_card_id.get(int(card_id))

    def event_for_music(self, music_id: int):
        event_id = self.music_to_event.get(int(music_id))
        return self.events_by_id.get(event_id) if event_id else None


class DataManager:
    """Owns the dataset, the on-disk cache and the remote sync."""

    def __init__(
        self,
        region: RegionProfile,
        data_dir: Path,
        bundled_dir: Path,
        sources: list[str] | None = None,
        timeout: int = 90,
    ) -> None:
        self.region = region
        self.data_dir = Path(data_dir)
        self.bundled_dir = Path(bundled_dir)
        # JP keeps the historical data/master layout so existing installs and
        # the bundled cache keep working; every other region gets a subfolder.
        self.master_dir = self.data_dir / "master" if region.key == "jp" else self.data_dir / "master" / region.key
        self.master_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = max(20, int(timeout))
        self.sources = self._build_sources(sources or [])
        self.dataset: Dataset | None = None
        self.version = ""
        self.last_update = ""
        self._load_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self._gacha_lock = asyncio.Lock()
        self._seed_from_bundle()

    # -- setup -------------------------------------------------------------
    def _build_sources(self, extra: list[str]) -> list[dict]:
        sources: list[dict] = []
        for index, base in enumerate(extra):
            base = str(base or "").strip().rstrip("/")
            if not base:
                continue
            sources.append(
                {
                    "name": f"自定义源 {index + 1} · {self.region.short}",
                    "file_url": f"{base}/{self.region.data_prefix}{{filename}}",
                    "version_url": f"{base}/{self.region.version_path}",
                    "headers": {},
                    "stat_key": "custom",
                }
            )
        sources.extend(region_sources(self.region))
        return sources

    def _seed_from_bundle(self) -> None:
        """Copies the shipped cache into the writable data directory once."""
        if self.data_dir.resolve() == self.bundled_dir.resolve():
            return
        bundled_master = (
            self.bundled_dir / "master"
            if self.region.key == "jp"
            else self.bundled_dir / "master" / self.region.key
        )
        wanted = set(CORE_FILES) | {GACHA_FILE} | set(LOCAL_FILES)
        if not bundled_master.is_dir():
            return
        for name in wanted:
            target = self.master_dir / name
            if target.is_file():
                continue
            source = bundled_master / name
            if not source.is_file():
                continue
            try:
                shutil.copy2(source, target)
                logger.info("[pjsk] seeded %s from the bundled cache", name)
            except OSError as exc:
                logger.warning("[pjsk] cannot seed %s: %s", name, exc)
        characters_target = self.data_dir / "characters.json"
        characters_source = self.bundled_dir / "characters.json"
        if not characters_target.is_file() and characters_source.is_file():
            try:
                shutil.copy2(characters_source, characters_target)
            except OSError:
                pass

    def has_data(self) -> bool:
        return (self.master_dir / "cards.json").is_file() and (self.master_dir / "musics.json").is_file()

    def has_gacha_data(self) -> bool:
        return (self.master_dir / GACHA_FILE).is_file()

    # -- loading -----------------------------------------------------------
    def _load_sync(self) -> Dataset:
        master = self.master_dir
        dataset = Dataset(region_key=self.region.key, asset_base=self.region.asset_base)
        dataset.cards = _read_json(master / "cards.json", []) or []
        dataset.musics = _read_json(master / "musics.json", []) or []
        dataset.difficulties = _read_json(master / "musicDifficulties.json", []) or []
        dataset.events = _read_json(master / "events.json", []) or []
        dataset.event_cards = _read_json(master / "eventCards.json", []) or []
        dataset.event_musics = _read_json(master / "eventMusics.json", []) or []
        dataset.event_deck_bonuses = _read_json(master / "eventDeckBonuses.json", []) or []
        dataset.game_character_units = _read_json(master / "gameCharacterUnits.json", []) or []
        dataset.music_vocals = _read_json(master / "musicVocals.json", []) or []
        dataset.skills = _read_json(master / "skills.json", []) or []
        dataset.supplies = _read_json(master / "cardSupplies.json", []) or []
        dataset.outside_characters_raw = _read_json(master / "outsideCharacters.json", []) or []
        dataset.music_aliases_raw = _read_json(master / MUSIC_ALIAS_FILE, []) or []
        translations = _read_json(master / MUSIC_TRANS_FILE, {}) or {}
        dataset.music_translations = translations.get("title", {}) if isinstance(translations, dict) else {}
        dataset.song_constants = _read_json(master / SONG_CONSTANTS_FILE, {}) or {}
        characters = _read_json(self.data_dir / "characters.json", []) or _read_json(
            self.bundled_dir / "characters.json", []
        ) or []
        dataset.characters = characters
        region_bundle = (
            self.bundled_dir / "master" / LOCAL_FILES[0]
            if self.region.key == "jp"
            else self.bundled_dir / "master" / self.region.key / LOCAL_FILES[0]
        )
        costume_raw = (
            _read_json(master / LOCAL_FILES[0], {})
            or _read_json(region_bundle, {})
            or _read_json(self.bundled_dir / "master" / LOCAL_FILES[0], {})
            or {}
        )
        dataset.costumes = costume_raw.get("costumes", []) if isinstance(costume_raw, dict) else costume_raw
        self._build_indexes(dataset)
        return dataset

    def _build_indexes(self, dataset: Dataset) -> None:
        dataset.cards_by_id = {int(card["id"]): card for card in dataset.cards if card.get("id")}
        dataset.cards_by_character = {}
        for card in dataset.cards:
            dataset.cards_by_character.setdefault(card.get("characterId", 0), []).append(card)

        dataset.characters_by_id = {
            int(character["characterId"]): character for character in dataset.characters
        }
        dataset.alias_to_character = {}
        for character in dataset.characters:
            cid = int(character["characterId"])
            for alias in [character.get("name", ""), character.get("fullName", ""), character.get("fullNameChinese", "")]:
                if alias:
                    dataset.alias_to_character[str(alias).lower()] = cid
            for alias in character.get("aliases", []) or []:
                if alias:
                    dataset.alias_to_character[str(alias).lower()] = cid

        dataset.unit_by_character = {}
        dataset.character_id_by_unit = {}
        for entry in dataset.game_character_units:
            character_id = entry.get("gameCharacterId")
            unit = entry.get("unit", "")
            if character_id is not None:
                character_id = int(character_id)
                # VIRTUAL SINGER characters appear once per unit; their base
                # unit is piapro, not whichever entry happens to be last.
                if character_id >= 21:
                    dataset.unit_by_character[character_id] = "piapro"
                else:
                    dataset.unit_by_character[character_id] = unit
            unit_id = entry.get("id")
            if unit_id is not None and character_id is not None:
                dataset.character_id_by_unit[int(unit_id)] = int(character_id)

        dataset.skills_by_id = {int(skill["id"]): skill for skill in dataset.skills if skill.get("id")}
        dataset.supplies_by_id = {int(supply["id"]): supply for supply in dataset.supplies if supply.get("id")}

        dataset.musics_by_id = {int(music["id"]): music for music in dataset.musics if music.get("id")}
        now = int(time.time() * 1000)
        dataset.musics_sorted = sorted(
            [music for music in dataset.musics if int(music.get("publishedAt", 0) or 0) <= now],
            key=lambda music: int(music.get("publishedAt", 0) or 0),
        )
        dataset.diffs_by_music = {}
        dataset.difficulties_by_level = {}
        dataset.difficulties_by_notes = {}
        for entry in dataset.difficulties:
            music_id = entry.get("musicId")
            if music_id is None:
                continue
            dataset.diffs_by_music.setdefault(int(music_id), {})[entry.get("musicDifficulty", "")] = entry
            level = entry.get("playLevel")
            if level is not None:
                dataset.difficulties_by_level.setdefault(int(level), []).append(entry)
            notes = entry.get("totalNoteCount")
            if notes:
                dataset.difficulties_by_notes.setdefault(int(notes), []).append(entry)

        dataset.vocals_by_music = {}
        for vocal in dataset.music_vocals:
            music_id = vocal.get("musicId")
            if music_id is not None:
                dataset.vocals_by_music.setdefault(int(music_id), []).append(vocal)

        dataset.events_by_id = {int(event["id"]): event for event in dataset.events if event.get("id")}
        dataset.event_cards_by_event = {}
        dataset.event_by_card_id = {}
        for entry in dataset.event_cards:
            event_id = entry.get("eventId")
            card_id = entry.get("cardId")
            if event_id is None or card_id is None:
                continue
            dataset.event_cards_by_event.setdefault(int(event_id), []).append(entry)
            current = dataset.event_by_card_id.get(int(card_id))
            if current is None or int(event_id) > int(current.get("id", 0)):
                dataset.event_by_card_id[int(card_id)] = dataset.events_by_id.get(int(event_id))
        dataset.event_deck_bonuses_by_event = {}
        for entry in dataset.event_deck_bonuses:
            event_id = entry.get("eventId")
            if event_id is not None:
                dataset.event_deck_bonuses_by_event.setdefault(int(event_id), []).append(entry)

        dataset.event_to_music = {}
        dataset.music_to_event = {}
        for entry in dataset.event_musics:
            event_id = entry.get("eventId")
            music_id = entry.get("musicId")
            if event_id is None or music_id is None:
                continue
            dataset.event_to_music[int(event_id)] = int(music_id)
            dataset.music_to_event.setdefault(int(music_id), int(event_id))

        dataset.outside_characters = {
            int(entry["id"]): entry.get("name", "")
            for entry in dataset.outside_characters_raw
            if entry.get("id") is not None
        }

        dataset.aliases_by_music = {}
        dataset.alias_to_music = {}
        raw_aliases = dataset.music_aliases_raw
        entries = raw_aliases.get("musics", []) if isinstance(raw_aliases, dict) else raw_aliases
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            music_id = entry.get("music_id")
            if not music_id:
                continue
            aliases = [str(alias).strip() for alias in (entry.get("aliases") or []) if str(alias).strip()]
            dataset.aliases_by_music[int(music_id)] = aliases
            for alias in aliases:
                dataset.alias_to_music.setdefault(alias.lower(), int(music_id))

        dataset.song_titles = dataset.music_translations if isinstance(dataset.music_translations, dict) else {}

        dataset.costumes_by_card_id = {}
        for costume in dataset.costumes:
            if costume.get("source") != "card":
                continue
            for card_id in costume.get("cardIds", []) or []:
                dataset.costumes_by_card_id.setdefault(int(card_id), []).append(costume)

        dataset.song_constants = {}
        for key, value in (self._read_constants_raw() or {}).items():
            try:
                dataset.song_constants[int(key)] = value
            except (TypeError, ValueError):
                continue

        # Gachas stay out of the initial load: 42 MB for a command most users
        # never call. ``ensure_gachas`` fills these in.
        dataset.gachas = []
        dataset.gachas_by_id = {}
        dataset.gacha_by_card_id = {}
        dataset.gacha_count = 0
        dataset.current_gacha = None
        dataset.gachas_loaded = False

    def _read_constants_raw(self) -> dict:
        return _read_json(self.master_dir / SONG_CONSTANTS_FILE, {}) or {}

    async def load(self) -> Dataset:
        async with self._load_lock:
            if self.dataset is not None:
                return self.dataset
            self.dataset = await asyncio.to_thread(self._load_sync)
            self.version = self._read_version()
            self.last_update = self._read_last_update()
            logger.info(
                "[pjsk] loaded %s cards / %s songs / %s events (version %s)",
                len(self.dataset.cards),
                len(self.dataset.musics),
                len(self.dataset.events),
                self.version or "unknown",
            )
            return self.dataset

    async def ensure_loaded(self) -> Dataset:
        return self.dataset or await self.load()

    async def reload(self) -> Dataset:
        async with self._load_lock:
            self.dataset = await asyncio.to_thread(self._load_sync)
            self.version = self._read_version()
            self.last_update = self._read_last_update()
            return self.dataset

    # -- gacha -------------------------------------------------------------
    async def ensure_gachas(self) -> Dataset:
        dataset = await self.ensure_loaded()
        if dataset.gachas_loaded:
            return dataset
        async with self._gacha_lock:
            if dataset.gachas_loaded:
                return dataset
            if not self.has_gacha_data():
                await self.sync(force=True, include_gacha=True)
            raw = await asyncio.to_thread(_read_json, self.master_dir / GACHA_FILE, [])
            gachas = raw if isinstance(raw, list) else []
            self._index_gachas(dataset, gachas)
            return dataset

    def _index_gachas(self, dataset: Dataset, gachas: list) -> None:
        dataset.gachas = gachas
        dataset.gachas_by_id = {int(gacha["id"]): gacha for gacha in gachas if gacha.get("id")}
        dataset.gacha_count = len(gachas)
        dataset.gacha_by_card_id = {}
        for gacha in gachas:
            pickup_ids = {int(pickup.get("cardId")) for pickup in (gacha.get("gachaPickups") or []) if pickup.get("cardId")}
            for detail in gacha.get("gachaDetails", []) or []:
                card_id = detail.get("cardId")
                if not card_id:
                    continue
                card_id = int(card_id)
                if card_id in pickup_ids or card_id not in dataset.gacha_by_card_id:
                    dataset.gacha_by_card_id[card_id] = gacha
        now = int(time.time() * 1000)
        current = [gacha for gacha in gachas if int(gacha.get("startAt", 0) or 0) <= now <= int(gacha.get("endAt", 0) or 0)]
        dataset.current_gacha = (
            max(current, key=lambda gacha: int(gacha.get("startAt", 0) or 0))
            if current
            else (max(gachas, key=lambda gacha: int(gacha.get("startAt", 0) or 0)) if gachas else None)
        )
        dataset.gachas_loaded = True

    # -- version -----------------------------------------------------------
    def _read_version(self) -> str:
        data = _read_json(self.master_dir / "data_version.json", {}) or {}
        return str(data.get("dataVersion", "") or "")

    def _read_last_update(self) -> str:
        data = _read_json(self.master_dir / "data_version.json", {}) or {}
        return str(data.get("updatedAt", "") or "")

    def _save_version(self, version: str) -> None:
        _write_json(
            self.master_dir / "data_version.json",
            {
                "dataVersion": version or self.version,
                "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        self.version = version or self.version
        self.last_update = time.strftime("%Y-%m-%d %H:%M")

    # -- remote sync -------------------------------------------------------
    async def _fetch_remote_version(self, session: aiohttp.ClientSession) -> str | None:
        for source in self.sources:
            try:
                async with session.get(
                    source["version_url"],
                    headers=source.get("headers") or {},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        version = str((data or {}).get("dataVersion", "") or "")
                        if version:
                            return version
            except Exception as exc:  # noqa: BLE001 - try the next source
                logger.debug("[pjsk] version probe failed on %s: %s", source["name"], exc)
        return None

    async def _fetch_one(self, session: aiohttp.ClientSession, filename: str) -> tuple[str, Any, str]:
        for source in self.sources:
            url = source["file_url"].format(filename=filename)
            try:
                async with session.get(
                    url,
                    headers=source.get("headers") or {},
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        # An error page or API envelope must never overwrite a
                        # valid table; every MasterData table is a top-level list.
                        if not isinstance(data, list):
                            logger.warning("[pjsk] %s returned a non-list payload for %s", source["name"], filename)
                            continue
                        await asyncio.to_thread(_write_json, self.master_dir / filename, data)
                        logger.info("[pjsk] updated %s from %s", filename, source["name"])
                        return filename, data, source["stat_key"]
                    if response.status in (403, 404):
                        logger.debug("[pjsk] %s returned %s for %s", source["name"], response.status, filename)
                    else:
                        logger.warning("[pjsk] %s returned %s for %s", source["name"], response.status, filename)
            except Exception as exc:  # noqa: BLE001 - try the next source
                logger.warning("[pjsk] %s failed for %s: %s", source["name"], filename, exc)
        cached = await asyncio.to_thread(_read_json, self.master_dir / filename, None)
        if cached is not None:
            return filename, cached, "cache"
        return filename, None, "failed"

    async def _fetch_aliases(self, session: aiohttp.ClientSession):
        return await self._fetch_independent(
            session,
            MUSIC_ALIAS_URL,
            MUSIC_ALIAS_FILE,
            json_mode=True,
            accept=lambda value: isinstance(value, (list, dict)),
        )

    async def _fetch_translations(self, session: aiohttp.ClientSession):
        return await self._fetch_independent(
            session,
            MUSIC_TRANS_URL,
            MUSIC_TRANS_FILE,
            json_mode=True,
            accept=lambda value: isinstance(value, dict),
        )

    async def _fetch_constants(self, session: aiohttp.ClientSession):
        try:
            async with session.get(SONG_CONSTANTS_URL, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    text = await response.text()
                    parsed = self._parse_constants_csv(text)
                    if parsed:
                        await asyncio.to_thread(_write_json, self.master_dir / SONG_CONSTANTS_FILE, parsed)
                        return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pjsk] constants update failed: %s", exc)
        return await asyncio.to_thread(_read_json, self.master_dir / SONG_CONSTANTS_FILE, None)

    async def _fetch_independent(
        self,
        session: aiohttp.ClientSession,
        url: str,
        filename: str,
        json_mode: bool = True,
        accept=None,
    ):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    data = await response.json(content_type=None) if json_mode else await response.text()
                    if accept is not None and not accept(data):
                        logger.warning("[pjsk] %s returned an unexpected payload", filename)
                    else:
                        await asyncio.to_thread(_write_json, self.master_dir / filename, data)
                        return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pjsk] %s update failed: %s", filename, exc)
        return await asyncio.to_thread(_read_json, self.master_dir / filename, None)

    @staticmethod
    def _parse_constants_csv(text: str) -> dict:
        result: dict[str, dict] = {}
        reader = csv.reader(io.StringIO(text))
        next(reader, None)
        for row in reader:
            if len(row) < 7:
                continue
            try:
                constant = float(row[2])
                song_id = int(row[6])
            except ValueError:
                continue
            difficulty = row[5].strip().lower()
            if difficulty:
                result.setdefault(str(song_id), {})[difficulty] = constant
        return result

    async def sync(self, force: bool = False, include_gacha: bool = True) -> dict:
        """Fetches MasterData with per-file source fallback and a version check."""
        async with self._sync_lock:
            if not self.has_data():
                logger.info("[pjsk] no local cache; a full sync is required")
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            stats = {source["stat_key"]: 0 for source in self.sources}
            stats.update({"cache": 0, "failed": 0})
            async with aiohttp.ClientSession(timeout=timeout) as session:
                remote_version = await self._fetch_remote_version(session)
                if not force and remote_version and remote_version == self.version and self.has_data():
                    logger.info("[pjsk] version %s is current", remote_version)
                    return {"updated": False, "skipped": True, "version": remote_version}

                files = list(CORE_FILES) + ([GACHA_FILE] if include_gacha else [])
                results: dict[str, Any] = {}
                for coro in asyncio.as_completed([self._fetch_one(session, name) for name in files]):
                    filename, data, source = await coro
                    if data is not None:
                        results[filename] = data
                    stats[source] = stats.get(source, 0) + 1

                aliases = await self._fetch_aliases(session)
                translations = await self._fetch_translations(session)
                constants = await self._fetch_constants(session)

            remote_count = sum(stats.get(source["stat_key"], 0) for source in self.sources)
            if remote_count == 0:
                logger.warning("[pjsk] every remote source failed; keeping the local cache")
                return {
                    "updated": False,
                    "all_sources_failed": True,
                    "version": remote_version,
                    **stats,
                }

            if remote_version:
                await asyncio.to_thread(self._save_version, remote_version)
            dataset = await self.reload()
            if include_gacha and GACHA_FILE in results:
                raw = results[GACHA_FILE]
                if isinstance(raw, list):
                    self._index_gachas(dataset, raw)
            elif not include_gacha and self.has_gacha_data():
                raw = await asyncio.to_thread(_read_json, self.master_dir / GACHA_FILE, [])
                if isinstance(raw, list):
                    self._index_gachas(dataset, raw)
            if aliases is not None:
                dataset.music_aliases_raw = aliases
            if translations is not None:
                dataset.music_translations = (
                    translations.get("title", {}) if isinstance(translations, dict) else {}
                )
            if constants is not None:
                dataset.song_constants = {}
                for key, value in constants.items():
                    try:
                        dataset.song_constants[int(key)] = value
                    except (TypeError, ValueError):
                        continue
            self._rebuild_after_independent(dataset)
            return {
                "updated": True,
                "version": remote_version or self.version,
                **stats,
            }

    def _rebuild_after_independent(self, dataset: Dataset) -> None:
        """Rebuilds only the indexes that depend on the independent sources."""
        dataset.aliases_by_music = {}
        dataset.alias_to_music = {}
        raw_aliases = dataset.music_aliases_raw
        entries = raw_aliases.get("musics", []) if isinstance(raw_aliases, dict) else raw_aliases
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            music_id = entry.get("music_id")
            if not music_id:
                continue
            aliases = [str(alias).strip() for alias in (entry.get("aliases") or []) if str(alias).strip()]
            dataset.aliases_by_music[int(music_id)] = aliases
            for alias in aliases:
                dataset.alias_to_music.setdefault(alias.lower(), int(music_id))
        dataset.song_titles = dataset.music_translations if isinstance(dataset.music_translations, dict) else {}

    # -- status ------------------------------------------------------------
    async def probe_sources(self) -> list[dict]:
        rows = []
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for source in self.sources:
                start = time.time()
                row = {"name": source["name"], "url": source["version_url"]}
                try:
                    async with session.get(source["version_url"], headers=source.get("headers") or {}) as response:
                        row["status"] = response.status
                        row["elapsed"] = round(time.time() - start, 1)
                        if response.status == 200:
                            data = await response.json(content_type=None)
                            row["version"] = str((data or {}).get("dataVersion", "") or "")
                        else:
                            row["version"] = ""
                except Exception as exc:  # noqa: BLE001
                    row["status"] = 0
                    row["elapsed"] = round(time.time() - start, 1)
                    row["error"] = str(exc)[:80]
                rows.append(row)
        return rows

    def local_file_status(self) -> list[dict]:
        rows = []
        for name in REMOTE_FILES:
            path = self.master_dir / name
            rows.append(
                {
                    "name": name,
                    "exists": path.is_file(),
                    "size": path.stat().st_size if path.is_file() else 0,
                }
            )
        return rows

    async def manual_fetch(self, filename: str) -> tuple[bool, str]:
        filename = filename.strip()
        if not filename.endswith(".json"):
            filename += ".json"
        allowed = set(REMOTE_FILES) | {MUSIC_ALIAS_FILE, MUSIC_TRANS_FILE, SONG_CONSTANTS_FILE} | set(LOCAL_FILES)
        if filename not in allowed:
            return False, f"未知数据文件：{filename}"
        if filename in LOCAL_FILES:
            return False, f"{filename} 是插件本地维护的服饰数据，不支持远程拉取"
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if filename == MUSIC_ALIAS_FILE:
                data = await self._fetch_aliases(session)
            elif filename == MUSIC_TRANS_FILE:
                data = await self._fetch_translations(session)
            elif filename == SONG_CONSTANTS_FILE:
                data = await self._fetch_constants(session)
            else:
                _, data, _ = await self._fetch_one(session, filename)
        if data is None:
            return False, f"拉取 {filename} 失败：所有数据源都不可用，且没有本地缓存"
        dataset = await self.reload()
        if filename == MUSIC_ALIAS_FILE:
            dataset.music_aliases_raw = data
            self._rebuild_after_independent(dataset)
        elif filename == MUSIC_TRANS_FILE:
            dataset.music_translations = data.get("title", {}) if isinstance(data, dict) else {}
            self._rebuild_after_independent(dataset)
        elif filename == SONG_CONSTANTS_FILE:
            dataset.song_constants = {}
            for key, value in (data or {}).items():
                try:
                    dataset.song_constants[int(key)] = value
                except (TypeError, ValueError):
                    continue
        elif filename == GACHA_FILE:
            if isinstance(data, list):
                self._index_gachas(dataset, data)
        count = len(data) if hasattr(data, "__len__") else "?"
        return True, f"已更新 {filename}（{count} 条记录），索引已重建"

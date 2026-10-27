# -*- coding: utf-8 -*-
"""Gacha pool construction, weighted draws and per-user pull counters."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("astrbot")

# A gacha simulator has no security requirement, but a system-random source
# keeps the draw unpredictable across processes and avoids a weak-RNG lint.
_rng = __import__("random").SystemRandom()

MAX_PULLS = 100
TEN_PULL_GUARANTEE_RARITY = 3


def _rarity_number(rarity_type: str) -> int:
    if not rarity_type.startswith("rarity_"):
        return 1
    part = rarity_type.split("_", 1)[1]
    return int(part) if part.isdigit() else 1


class GachaPool:
    """One pool with prefix sums so a draw is O(log n)."""

    def __init__(self, dataset, gacha: dict) -> None:
        self.gacha = gacha
        self.pickup_ids = {
            int(pickup["cardId"])
            for pickup in (gacha.get("gachaPickups") or [])
            if pickup.get("cardId")
        }
        self.rates: list[tuple[str, float]] = []
        for rate in gacha.get("gachaCardRarityRates", []) or []:
            rarity_type = rate.get("cardRarityType", "")
            try:
                value = float(rate.get("rate", 0) or 0)
            except (TypeError, ValueError):
                value = 0.0
            if rarity_type and value > 0:
                self.rates.append((rarity_type, value))

        raw: dict[str, list[tuple[dict, float]]] = {}
        for detail in gacha.get("gachaDetails", []) or []:
            card = dataset.cards_by_id.get(detail.get("cardId"))
            if not card:
                continue
            try:
                weight = float(detail.get("weight", 0) or 0)
            except (TypeError, ValueError):
                weight = 0.0
            if weight <= 0:
                continue
            rarity_type = card.get("cardRarityType", "")
            raw.setdefault(rarity_type, []).append((card, weight))

        self.pools: dict[str, dict] = {}
        for rarity_type, entries in raw.items():
            total = sum(weight for _, weight in entries)
            cumulative = 0.0
            entries_with_sums = []
            for card, weight in entries:
                cumulative += weight
                entries_with_sums.append((card, weight, cumulative))
            self.pools[rarity_type] = {"total": total, "entries": entries_with_sums}

    def has_rarity(self, minimum: int) -> bool:
        return any(
            _rarity_number(rarity_type) >= minimum and rarity_type in self.pools
            for rarity_type, _ in self.rates
        )

    def _draw_rarity(self, allowed: set[str] | None = None) -> str | None:
        rates = [(rarity, rate) for rarity, rate in self.rates if rarity in self.pools and (allowed is None or rarity in allowed)]
        if not rates:
            return None
        total = sum(rate for _, rate in rates)
        roll = _rng.uniform(0, total)
        cumulative = 0.0
        for rarity, rate in rates:
            cumulative += rate
            if roll <= cumulative:
                return rarity
        return rates[-1][0]

    def _draw_card(self, rarity_type: str) -> dict | None:
        pool = self.pools.get(rarity_type)
        if not pool:
            return None
        roll = _rng.uniform(0, pool["total"])
        entries = pool["entries"]
        low, high = 0, len(entries) - 1
        while low < high:
            middle = (low + high) // 2
            if entries[middle][2] < roll:
                low = middle + 1
            else:
                high = middle
        return entries[low][0]

    def _filtered(self, character_id: int) -> GachaPool:
        """A copy of this pool containing only one character's cards."""
        filtered = GachaPool.__new__(GachaPool)
        filtered.gacha = self.gacha
        filtered.pickup_ids = self.pickup_ids
        filtered.rates = []
        filtered.pools = {}
        for rarity_type, rate in self.rates:
            pool = self.pools.get(rarity_type)
            if not pool:
                continue
            entries = [
                (card, weight)
                for card, weight, _ in pool["entries"]
                if int(card.get("characterId", 0)) == character_id
            ]
            if not entries:
                continue
            total = 0.0
            rebuilt = []
            for card, weight in entries:
                total += weight
                rebuilt.append((card, weight, total))
            filtered.rates.append((rarity_type, rate))
            filtered.pools[rarity_type] = {"total": total, "entries": rebuilt}
        return filtered

    def draw(self, character_id: int | None = None) -> dict | None:
        # Filtering one character can empty a rarity; those rarities are
        # removed from the rate table rather than silently skipped.
        pool = self if character_id is None else self._filtered(character_id)
        return pool._draw_once()

    def _draw_once(self) -> dict | None:
        rarity_type = self._draw_rarity()
        if rarity_type is None:
            return None
        return self._draw_card(rarity_type)

    def draw_guaranteed(self, minimum: int = TEN_PULL_GUARANTEE_RARITY, character_id: int | None = None) -> dict | None:
        pool = self if character_id is None else self._filtered(character_id)
        return pool._draw_guaranteed_once(minimum)

    def _draw_guaranteed_once(self, minimum: int = TEN_PULL_GUARANTEE_RARITY) -> dict | None:
        allowed = {
            rarity_type
            for rarity_type, _ in self.rates
            if _rarity_number(rarity_type) >= minimum and rarity_type in self.pools
        }
        if not allowed:
            # The character has no card at the guaranteed rarity; the best
            # possible draw is still better than returning nothing.
            return self._draw_once()
        rarity_type = self._draw_rarity(allowed)
        if rarity_type is None:
            return self._draw_once()
        return self._draw_card(rarity_type)


def perform_draw(dataset, gacha: dict, count: int, character_id: int | None = None) -> list[dict]:
    """Draws ``count`` cards, applying the ten-pull SR+ guarantee per chunk."""
    count = max(1, min(MAX_PULLS, int(count)))
    base_pool = GachaPool(dataset, gacha)
    # Only pools that actually sell a ten-pull carry its SR+ guarantee; some
    # special ten-pulls guarantee a 4★ instead.
    guarantee_rarity = 0
    for behavior in gacha.get("gachaBehaviors", []) or []:
        if int(behavior.get("spinCount", 0) or 0) < 10:
            continue
        kind = behavior.get("gachaBehaviorType", "")
        if kind == "over_rarity_4_once":
            guarantee_rarity = 4
            break
        if kind == "over_rarity_3_once" and guarantee_rarity < 3:
            guarantee_rarity = 3
    has_ten_pull = guarantee_rarity > 0 or any(
        int(behavior.get("spinCount", 0) or 0) >= 10
        for behavior in gacha.get("gachaBehaviors", []) or []
    )
    guarantee_rarity = guarantee_rarity or TEN_PULL_GUARANTEE_RARITY
    # Build the character-filtered pool once instead of per draw.
    pool = base_pool if character_id is None else base_pool._filtered(character_id)
    results: list[dict] = []
    for index in range(count):
        is_last_of_chunk = (index + 1) % 10 == 0
        guaranteed = False
        if has_ten_pull and is_last_of_chunk and count >= 10:
            recent = results[-9:]
            guaranteed = not any(_rarity_number(card.get("cardRarityType", "")) >= guarantee_rarity for card in recent)
        card = pool.draw_guaranteed(guarantee_rarity) if guaranteed else pool.draw()
        if card is not None:
            results.append(card)
    return results


class GachaStateStore:
    """Cumulative pull counters, keyed by user then region + gacha."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()
        self._lock = asyncio.Lock()

    @staticmethod
    def _bucket_key(gacha_id: int, region: str = "cn") -> str:
        # CN keeps the historical plain id so existing counters stay valid;
        # every other region is namespaced because ids overlap across servers.
        return str(gacha_id) if region == "cn" else f"{region}:{gacha_id}"

    def _load(self) -> dict:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and isinstance(data.get("users"), dict):
                return data
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001 - a corrupt counter file is disposable
            logger.warning("[pjsk] cannot read gacha state: %s", exc)
        return {"users": {}}

    def _save(self) -> None:
        users = self._data.get("users", {})
        if len(users) > 500:
            ordered = sorted(
                users.items(),
                key=lambda item: item[1].get("updatedAt", 0),
                reverse=True,
            )[:500]
            self._data["users"] = dict(ordered)
        temporary = self.path.with_suffix(".json.part")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(self._data, handle, ensure_ascii=False, separators=(",", ":"))
            temporary.replace(self.path)
        except OSError as exc:
            logger.warning("[pjsk] cannot save gacha state: %s", exc)

    async def record(
        self,
        user_id: str,
        gacha_id: int,
        results: list[dict],
        pickup_ids: set[int] | None = None,
        region: str = "cn",
    ) -> dict:
        async with self._lock:
            users = self._data.setdefault("users", {})
            user = users.setdefault(str(user_id), {})
            bucket = user.setdefault(
                self._bucket_key(gacha_id, region),
                {"pulls": 0, "rarity": {}, "pickups": 0},
            )
            session_rarity: dict[str, int] = {}
            session_pickups = 0
            pickups = pickup_ids or set()
            for card in results:
                rarity = _rarity_number(card.get("cardRarityType", ""))
                key = str(rarity)
                bucket["rarity"][key] = bucket["rarity"].get(key, 0) + 1
                session_rarity[key] = session_rarity.get(key, 0) + 1
                if int(card.get("id", 0)) in pickups:
                    bucket["pickups"] = bucket.get("pickups", 0) + 1
                    session_pickups += 1
            bucket["pulls"] = bucket.get("pulls", 0) + len(results)
            user["updatedAt"] = int(time.time())
            self._data["users"][str(user_id)] = user
            await asyncio.to_thread(self._save)
            return {
                "total_pulls": bucket["pulls"],
                "total_rarity": dict(bucket["rarity"]),
                "total_pickups": bucket.get("pickups", 0),
                "session_rarity": session_rarity,
                "session_pickups": session_pickups,
            }

    async def reset(self, user_id: str, gacha_id: int, region: str = "cn") -> bool:
        async with self._lock:
            users = self._data.setdefault("users", {})
            user = users.get(str(user_id))
            key = self._bucket_key(gacha_id, region)
            if not user or key not in user:
                return False
            user.pop(key, None)
            user["updatedAt"] = int(time.time())
            await asyncio.to_thread(self._save)
            return True

    def snapshot(self, user_id: str, gacha_id: int, region: str = "cn") -> dict:
        user = self._data.get("users", {}).get(str(user_id), {})
        bucket = user.get(self._bucket_key(gacha_id, region), {})
        return {
            "total_pulls": bucket.get("pulls", 0),
            "total_rarity": dict(bucket.get("rarity", {})),
            "total_pickups": bucket.get("pickups", 0),
        }

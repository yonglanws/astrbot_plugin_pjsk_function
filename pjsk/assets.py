# -*- coding: utf-8 -*-
"""Image download, resizing and inline embedding.

The t2i renderer receives a detached HTML document, so images cannot be served
from the plugin directory. They are embedded as data URIs instead of hotlinked:
the PJSK asset host is reliable, but the decoration host (pjsk.moe) is slow and
intermittent, and a page that silently loses half its icons looks broken.

Every asset is downloaded once, resized to the size the page actually displays,
encoded as WebP and cached on disk. List pages therefore stay a few megabytes
even with a hundred cards.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import ipaddress
import logging
import os
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("astrbot")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return address.is_global


def is_public_http_url(url: str) -> bool:
    """Whether a server-side fetch of ``url`` is allowed: public http(s) hosts."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return _is_public_address(host)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _encode_webp(raw: bytes, width: int, quality: int) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        if width and image.width > width:
            ratio = width / image.width
            height = max(1, int(image.height * ratio))
            image = image.resize((width, height), Image.LANCZOS)
        output = io.BytesIO()
        image.save(output, "WEBP", quality=quality, method=4)
        return output.getvalue()


class AssetService:
    """Downloads, resizes and caches assets, returning data URIs for templates."""

    def __init__(self, cache_dir: Path, timeout: int = 60) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = max(10, int(timeout))
        self._session: aiohttp.ClientSession | None = None
        self._memory: OrderedDict[str, str] = OrderedDict()
        self._memory_max = 400
        self._semaphore = asyncio.Semaphore(16)
        self._icon_dir = Path(__file__).resolve().parent.parent / "res" / "icons"
        self._cleanup_webp()

    def _cleanup_webp(self) -> None:
        """Keeps the resized-image cache bounded (30 days / 6000 files)."""
        try:
            files = sorted(
                self.cache_dir.glob("*.webp"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        cutoff = time.time() - 30 * 24 * 3600
        for index, path in enumerate(files):
            try:
                if index >= 6000 or path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    # -- lifecycle ---------------------------------------------------------
    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "image/webp,image/png,image/jpeg,image/*;q=0.8",
                "Referer": "https://pjsk.moe/",
            }
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=24, ttl_dns_cache=300),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers=headers,
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # -- caching -----------------------------------------------------------
    def _remember(self, key: str, value: str) -> str:
        self._memory[key] = value
        self._memory.move_to_end(key)
        while len(self._memory) > self._memory_max:
            self._memory.popitem(last=False)
        return value

    def _from_disk(self, key: str) -> str | None:
        path = self.cache_dir / f"{key}.webp"
        if not path.is_file():
            return None
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if not data:
            return None
        return self._remember(key, "data:image/webp;base64," + base64.b64encode(data).decode("ascii"))

    def _write_disk(self, key: str, data: bytes) -> None:
        path = self.cache_dir / f"{key}.webp"
        try:
            temporary = path.with_suffix(".webp.part")
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError:
            pass

    # -- remote images -----------------------------------------------------
    async def data_uri(self, url: str, width: int = 0, quality: int = 82) -> str:
        """Downloads ``url`` once and returns a WebP data URI.

        ``width`` is the display width in CSS pixels; the asset is downscaled to
        it, which keeps list pages small without visible quality loss at 1.3x.
        """
        url = (url or "").strip()
        if not url:
            return ""
        key = f"{_digest(url)}_{int(width)}_{int(quality)}"
        cached = self._memory.get(key)
        if cached is not None:
            self._memory.move_to_end(key)
            return cached
        disk = self._from_disk(key)
        if disk is not None:
            return disk
        raw = await self.fetch_bytes(url)
        if not raw:
            return ""
        try:
            encoded = await asyncio.to_thread(_encode_webp, raw, width, quality)
        except Exception as exc:  # noqa: BLE001 - a broken asset must not kill the page
            logger.debug("[pjsk] image encode failed %s: %s", url, exc)
            return ""
        self._write_disk(key, encoded)
        return self._remember(
            key, "data:image/webp;base64," + base64.b64encode(encoded).decode("ascii")
        )

    async def fetch_bytes(self, url: str) -> bytes | None:
        if not is_public_http_url(url):
            logger.warning("[pjsk] refusing non-public asset URL: %s", url)
            return None
        async with self._semaphore:
            try:
                session = self._get_session()
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug("[pjsk] asset HTTP %s: %s", response.status, url)
                        return None
                    return await response.read()
            except Exception as exc:  # noqa: BLE001 - templates fall back to a gradient
                logger.debug("[pjsk] asset download failed %s: %s", url, exc)
                return None

    # -- bundled icons -----------------------------------------------------
    def icon_data_uri(self, filename: str, width: int = 0) -> str:
        """Reads a decoration icon from ``res/icons`` and inlines it."""
        name = os.path.basename(filename or "")
        if not name:
            return ""
        path = self._icon_dir / name
        if not path.is_file():
            return ""
        key = f"icon_{_digest(str(path))}_{int(width)}"
        cached = self._memory.get(key)
        if cached is not None:
            self._memory.move_to_end(key)
            return cached
        try:
            raw = path.read_bytes()
            encoded = _encode_webp(raw, width, 90)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[pjsk] icon load failed %s: %s", path, exc)
            return ""
        return self._remember(
            key, "data:image/webp;base64," + base64.b64encode(encoded).decode("ascii")
        )

    # -- raw artwork -------------------------------------------------------
    async def fetch_file(self, url: str) -> Path | None:
        """Downloads an original asset for direct sending; converts WebP to PNG."""
        url = (url or "").strip()
        if not url:
            return None
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            suffix = ".png"
        digest = _digest(url)
        cached = self.cache_dir / f"raw_{digest}{suffix}"
        if suffix != ".png":
            converted = cached.with_suffix(".png")
            if converted.is_file() and converted.stat().st_size > 0:
                return converted
        if cached.is_file() and cached.stat().st_size > 0:
            return await self._prefer_png(cached)
        raw = await self.fetch_bytes(url)
        if not raw:
            return None
        try:
            temporary = cached.with_suffix(cached.suffix + ".part")
            temporary.write_bytes(raw)
            temporary.replace(cached)
        except OSError:
            return None
        self._cleanup_raw()
        return await self._prefer_png(cached)

    @staticmethod
    async def _prefer_png(path: Path) -> Path:
        if path.suffix.lower() != ".webp":
            return path
        png = path.with_suffix(".png")
        if png.is_file() and png.stat().st_size > 0:
            try:
                path.unlink()
            except OSError:
                pass
            return png

        def convert() -> None:
            from PIL import Image

            with Image.open(path) as image:
                image.convert("RGBA").save(png, "PNG", optimize=True)

        try:
            await asyncio.to_thread(convert)
        except Exception:  # noqa: BLE001 - send the original webp
            if png.is_file() and png.stat().st_size > 0:
                return png
            return path
        try:
            path.unlink()
        except OSError:
            pass
        return png

    def _cleanup_raw(self) -> None:
        try:
            files = sorted(
                self.cache_dir.glob("raw_*"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        cutoff = time.time() - 7 * 24 * 3600
        for index, path in enumerate(files):
            try:
                if index >= 300 or path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

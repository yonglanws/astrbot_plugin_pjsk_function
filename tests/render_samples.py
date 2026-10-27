# -*- coding: utf-8 -*-
"""Renders every page through the real t2i service into ``tests/output``.

Run from the plugin root:

    python tests/render_samples.py
    python tests/render_samples.py --only card_detail,gacha_result
    python tests/render_samples.py --endpoint http://127.0.0.1:8999

The endpoint defaults to ``$PJSK_RENDER_ENDPOINT`` or the local t2i service.
Pages are built through the real command router, so this also exercises routing
and view assembly.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import _stubs  # noqa: E402

_stubs.install()

import main  # noqa: E402

DEFAULT_ENDPOINT = os.environ.get("PJSK_RENDER_ENDPOINT", "http://127.0.0.1:8999")
OUTPUT = ROOT / "tests" / "output"

# (output name, command)
CASES = [
    ("help", "帮助"),
    ("catalog", "查图鉴"),
    ("card_detail", "查卡 1361"),
    ("card_detail_single", "查卡 295"),
    ("card_list", "查卡 miku 4"),
    ("card_list_big", "查卡 miku"),
    ("card_list_skill", "查卡 团分"),
    ("card_list_leak", "查卡 leak"),
    ("card_list_event", "查卡 event200"),
    ("card_list_ban_event", "查卡 mnr1"),
    ("card_detail_newest", "查卡 miku-1"),
    ("card_detail_leak", "查卡 1272"),
    ("card_list_pure_vs", "查卡 vs"),
    ("card_guide", "查卡"),
    ("song_detail", "查曲 id1"),
    ("song_event", "查曲 64"),
    ("chart_preview", "谱面 300"),
    ("chart_preview_ex", "谱面 1 ex"),
    ("chart_preview_append", "谱面 100 append"),
    ("chart_preview_jp", "jp谱面 825"),
    ("song_list_constant", "查定数 master 32"),
    ("song_level_list", "查曲 MASTER 32"),
    ("song_level_index", "查曲 MASTER"),
    ("random_song", "随个 32"),
    ("notes_list", "查物量 1000"),
    ("gacha_detail", "查卡池"),
    ("gacha_list", "卡池列表"),
    ("gacha_result", "pjsk十连"),
    ("gacha_single", "pjsk单抽"),
    ("event_detail", "查活动 200"),
    ("event_list", "活动一览"),
    ("status", "检查数据源"),
    ("jp_card_detail", "jp查卡 4"),
    ("jp_song_detail", "jp查曲 id1"),
]


async def render_all(endpoint: str, only: set[str] | None) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    plugin = main.PjskFunctionPlugin(
        None,
        config={"render_endpoint": endpoint, "auto_sync": False, "default_region": "cn"},
        data_dir=ROOT / "data",
    )
    await plugin.managers["cn"].load()
    await plugin.managers["cn"].ensure_gachas()
    await plugin.managers["jp"].load()
    await plugin.managers["jp"].ensure_gachas()
    # Samples render through the direct fallback; the stub's html_render would
    # otherwise fail three times per page before falling back.
    plugin.renderer.service.render_func = None

    failures = []
    for name, command in CASES:
        if only and name not in only:
            continue
        event = _stubs.FakeEvent(command, admin=True)
        images = []
        try:
            async for result in plugin.handle_message(event):
                if result and result[0] == "image":
                    images.append(Path(result[1]))
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures.append((name, command, str(exc)))
            print(f"FAIL {name}: {exc}")
            continue
        if not images:
            failures.append((name, command, "no image produced"))
            print(f"FAIL {name}: no image produced")
            continue
        source = images[-1]
        target = OUTPUT / f"{name}{source.suffix}"
        shutil.copy2(source, target)
        size = target.stat().st_size
        try:
            from PIL import Image

            with Image.open(target) as image:
                dimensions = f"{image.width}x{image.height}"
        except Exception:
            dimensions = "?"
        print(f"OK   {name:<18} {dimensions:>11}  {size / 1024:8.0f} KB  {command}")

    await plugin.assets.close()
    if failures:
        print("\nFailures:")
        for name, command, error in failures:
            print(f"  {name} ({command}): {error}")
        raise SystemExit(1)


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--only", default="", help="comma separated sample names")
    args = parser.parse_args()
    only = {part.strip() for part in args.only.split(",") if part.strip()} or None
    asyncio.run(render_all(args.endpoint, only))


if __name__ == "__main__":
    main_cli()

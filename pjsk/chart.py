# -*- coding: utf-8 -*-
"""Chart image helpers.

The chart host publishes one SVG per song and difficulty. That SVG keeps its
note sprites as relative ``/notes_new/...`` hrefs and its jacket as an absolute
URL, so when the file is embedded as a data URI (where external resources are
blocked) the notes and the bottom-left jacket come out blank. The plugin
downloads every referenced image itself and rewrites the hrefs to data URIs
before handing the SVG to the renderer.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from .constants import CHART_HOST

_SVG_TAG = re.compile(r"<svg\b[^>]*>")
_SVG_IMAGE = re.compile(r'<image\b[^>]*?\bhref="([^"]+)"[^>]*>')


def svg_dimensions(svg: str) -> tuple[int, int]:
    """Natural pixel size from the root ``<svg width= height=>``."""
    match = _SVG_TAG.search(svg)
    if not match:
        return 0, 0
    tag = match.group(0)
    width = re.search(r'\bwidth="([\d.]+)(?:px)?"', tag)
    height = re.search(r'\bheight="([\d.]+)(?:px)?"', tag)
    if not width or not height:
        return 0, 0
    return int(float(width.group(1))), int(float(height.group(1)))


def svg_image_urls(svg: str) -> list[str]:
    return sorted(set(_SVG_IMAGE.findall(svg)))


def chart_asset_url(href: str) -> str:
    """Absolute URL for an href inside a chart SVG (relative or absolute)."""
    return urljoin(f"{CHART_HOST}/", href)


def inline_svg_images(svg: str, replacements: dict[str, str]) -> str:
    """Replaces each ``href`` whose original URL has a data URI mapped."""
    if not replacements:
        return svg

    def replace(match: re.Match) -> str:
        data = replacements.get(match.group(1))
        if not data:
            return match.group(0)
        return match.group(0).replace(f'href="{match.group(1)}"', f'href="{data}"', 1)

    return _SVG_IMAGE.sub(replace, svg)


def preview_document(svg_data_uri: str, width: int, height: int) -> str:
    """A bare document that rasterises to exactly the chart, nothing else."""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>html,body{margin:0;padding:0;background:#fff}"
        f"img{{display:block;width:{width}px;height:{height}px}}</style></head>"
        f"<body><img src=\"{svg_data_uri}\" width=\"{width}\" height=\"{height}\"></body></html>"
    )

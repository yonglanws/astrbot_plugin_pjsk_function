# -*- coding: utf-8 -*-
"""Static tables shared by the PJSK query plugin.

Everything that describes the game's fixed vocabulary (difficulties, attributes,
rarities, units, command aliases) lives here so the data layer, the search layer
and the templates all read the same values.
"""

from __future__ import annotations

from dataclasses import dataclass

PLUGIN_NAME = "astrbot_plugin_pjsk_function"
BRAND = "mzkbot"

# ---------------------------------------------------------------------------
# Regions / data sources
# ---------------------------------------------------------------------------
DEFAULT_REGION = "cn"


@dataclass(frozen=True)
class RegionProfile:
    """One game server: its MasterData repo, cache folder and asset base."""

    key: str
    label: str
    short: str
    prefixes: tuple[str, ...]
    repo: str
    data_prefix: str
    asset_base: str
    version_path: str


# CN is the default because most of the bot's users play on the mainland
# server. JP stays available through the jp/日服 prefix.
REGIONS: dict[str, RegionProfile] = {
    "cn": RegionProfile(
        key="cn",
        label="国服",
        short="CN",
        prefixes=("cn", "国服", "简中", "cn服"),
        # Haruki's Simplified-Chinese mirror, same layout as the JP repo.
        repo="Team-Haruki/haruki-sekai-sc-master",
        data_prefix="master/",
        asset_base="https://storage.exmeaning.com/sekai-cn-assets",
        version_path="versions/current_version.json",
    ),
    "jp": RegionProfile(
        key="jp",
        label="日服",
        short="JP",
        prefixes=("jp", "日服", "日"),
        repo="Team-Haruki/haruki-sekai-master",
        data_prefix="master/",
        asset_base="https://storage.exmeaning.com/sekai-jp-assets",
        version_path="versions/current_version.json",
    ),
}

REGION_ALIASES: dict[str, str] = {
    alias.lower(): key for key, profile in REGIONS.items() for alias in profile.prefixes
}


def resolve_region(prefix: str | None) -> str:
    """Maps a command prefix to a region key; unknown/empty means the default."""
    key = (prefix or DEFAULT_REGION).strip().lower()
    return REGION_ALIASES.get(key, DEFAULT_REGION)


def region_sources(profile: RegionProfile) -> list[dict]:
    """Three-level fallback for one region.

    The GitHub Contents API is tried first because it is fast from mainland
    China and returns the raw file directly. jsDelivr is unlimited but refuses
    files above 20 MB (the JP gacha table is larger), so it is second. GitHub
    raw is the last resort.
    """
    repo = profile.repo
    path = profile.data_prefix
    version = profile.version_path
    return [
        {
            "name": f"GitHub API · {profile.short}",
            "file_url": f"https://api.github.com/repos/{repo}/contents/{path}{{filename}}",
            "version_url": f"https://api.github.com/repos/{repo}/contents/{version}",
            "headers": {"Accept": "application/vnd.github.raw+json"},
            "stat_key": "github_api",
        },
        {
            "name": f"jsDelivr · {profile.short}",
            "file_url": f"https://cdn.jsdelivr.net/gh/{repo}@main/{path}{{filename}}",
            "version_url": f"https://cdn.jsdelivr.net/gh/{repo}@main/{version}",
            "headers": {},
            "stat_key": "jsdelivr",
        },
        {
            "name": f"GitHub raw · {profile.short}",
            "file_url": f"https://raw.githubusercontent.com/{repo}/main/{path}{{filename}}",
            "version_url": f"https://raw.githubusercontent.com/{repo}/main/{version}",
            "headers": {},
            "stat_key": "github_raw",
        },
    ]

# Tables the query pages need. The huge, unused tables (resource boxes, virtual
# lives, honours, mission parameters) are deliberately not downloaded or loaded.
CORE_FILES: tuple[str, ...] = (
    "cards.json",
    "musics.json",
    "musicDifficulties.json",
    "events.json",
    "eventCards.json",
    "eventMusics.json",
    "eventDeckBonuses.json",
    "cardSupplies.json",
    "skills.json",
    "gameCharacterUnits.json",
    "musicVocals.json",
    "outsideCharacters.json",
)
# 42 MB and only needed by the gacha commands, so it is synced separately.
GACHA_FILE = "gachas.json"
REMOTE_FILES: tuple[str, ...] = CORE_FILES + (GACHA_FILE,)
# Never downloaded: bundled in the plugin and maintained locally.
LOCAL_FILES: tuple[str, ...] = ("moe_costume.json",)

MUSIC_ALIAS_URL = "https://moe.exmeaning.com/data/music_alias/music_aliases.json"
MUSIC_ALIAS_FILE = "music_aliases.json"
MUSIC_TRANS_URL = "https://translation.exmeaning.com/files/translation/music.json"
MUSIC_TRANS_FILE = "music_translations.json"
SONG_CONSTANTS_URL = "https://moe.exmeaning.com/data/pjskb30/merged_chart.csv"
SONG_CONSTANTS_FILE = "song_constants.json"

# ---------------------------------------------------------------------------
# Canvas / render
# ---------------------------------------------------------------------------
LIST_WIDTH = 1080
LIST_SCALE = "normal"
LIST_JPEG_QUALITY = 90

# No pagination: every result set is one image. Card lists render every match;
# the remaining caps only keep a generic keyword from producing a page several
# screens tall.
SONG_LIST_LIMIT = 120
GACHA_SEARCH_LIMIT = 12
EVENT_LIST_LIMIT = 12


# ---------------------------------------------------------------------------
# Game vocabulary
# ---------------------------------------------------------------------------
# (key, label, short label, colour)
DIFFICULTIES: tuple[tuple[str, str, str, str], ...] = (
    ("easy", "EASY", "EZ", "#66DD11"),
    ("normal", "NORMAL", "NM", "#33BBEE"),
    ("hard", "HARD", "HD", "#FFAA00"),
    ("expert", "EXPERT", "EX", "#EE4466"),
    ("master", "MASTER", "MA", "#BB33EE"),
    ("append", "APPEND", "AP", "#B690F7"),
)
DIFFICULTY_ORDER = tuple(entry[0] for entry in DIFFICULTIES)
DIFFICULTY_LABELS = {entry[0]: entry[1] for entry in DIFFICULTIES}
DIFFICULTY_SHORT = {entry[0]: entry[2] for entry in DIFFICULTIES}
DIFFICULTY_COLORS = {entry[0]: entry[3] for entry in DIFFICULTIES}

# Accepted spellings, longest first at match time.
DIFFICULTY_WORDS: dict[str, str] = {}
for _key, _label, _short, _color in DIFFICULTIES:
    for _word in (_key, _label, _short, _key.upper(), _label.lower(), _short.lower()):
        DIFFICULTY_WORDS.setdefault(_word, _key)
DIFFICULTY_WORDS.update(
    {
        "ez": "easy",
        "nm": "normal",
        "hd": "hard",
        "ex": "expert",
        "exp": "expert",
        "ma": "master",
        "mas": "master",
        "apd": "append",
        "ap": "append",
        "简单": "easy",
        "普通": "normal",
        "困难": "hard",
        "专家": "expert",
        "大师": "master",
        "魔王": "master",
    }
)

ATTRIBUTES: dict[str, dict] = {
    "cute": {"label": "可爱", "color": "#FF66BB", "icon": "attr_cute.png"},
    "cool": {"label": "酷炫", "color": "#33AAEE", "icon": "attr_cool.png"},
    "pure": {"label": "纯真", "color": "#66DD44", "icon": "attr_pure.png"},
    "happy": {"label": "愉快", "color": "#FFBB00", "icon": "attr_happy.png"},
    "mysterious": {"label": "神秘", "color": "#AA66DD", "icon": "attr_mysterious.png"},
}

RARITIES: dict[str, dict] = {
    "rarity_1": {"label": "1★", "num": 1, "color": "#9AA3AD"},
    "rarity_2": {"label": "2★", "num": 2, "color": "#5FB7E8"},
    "rarity_3": {"label": "3★", "num": 3, "color": "#F0B429"},
    "rarity_4": {"label": "4★", "num": 4, "color": "#F06292"},
    "rarity_birthday": {"label": "生日", "num": 1, "color": "#E05A7A"},
}

UNITS: dict[str, dict] = {
    "light_sound": {"label": "Leo/need", "short": "L/N", "color": "#4455DD"},
    "idol": {"label": "MORE MORE JUMP!", "short": "MMJ", "color": "#88DD44"},
    "street": {"label": "Vivid BAD SQUAD", "short": "VBS", "color": "#EE1166"},
    "theme_park": {"label": "Wonderlands×Showtime", "short": "WxS", "color": "#FF9900"},
    "school_refusal": {"label": "25时，在Nightcord。", "short": "25时", "color": "#884499"},
    "piapro": {"label": "VIRTUAL SINGER", "short": "VS", "color": "#33CCCC"},
    # Mixed-unit events (and most World Bloom events) carry unit "none".
    "none": {"label": "混合", "short": "混合", "color": "#8A939E"},
}

SUPPLY_TYPES: dict[str, dict] = {
    "normal": {"label": "常驻", "tag": ""},
    "birthday": {"label": "生日", "tag": "生日"},
    "term_limited": {"label": "期间限定", "tag": "期间"},
    "colorful_festival_limited": {"label": "Colorful Fes 限定", "tag": "CFes"},
    "bloom_festival_limited": {"label": "Bloom Fes 限定", "tag": "BFes"},
    "unit_event_limited": {"label": "World Link 限定", "tag": "WL"},
    "collaboration_limited": {"label": "联动限定", "tag": "联动"},
    "festival_limited": {"label": "Fes 限定", "tag": "Fes"},
}

SKILL_TYPES: dict[str, dict] = {
    "score_up": {"label": "分数提升", "short": "分", "icon": "skill_score_up.png"},
    "life_recovery": {"label": "生命恢复", "short": "奶", "icon": "skill_life_recovery.png"},
    "judgment_up": {"label": "判定强化", "short": "判", "icon": "skill_judgment_up.png"},
}

EVENT_TYPES: dict[str, dict] = {
    "marathon": {"label": "马拉松", "short": "马拉松"},
    "cheerful_carnival": {"label": "嘉年华", "short": "嘉年华"},
    "world_bloom": {"label": "World Bloom", "short": "WL"},
}

MUSIC_CATEGORIES: dict[str, str] = {
    "original": "原版",
    "mv": "3D MV",
    "mv_2d": "2D MV",
    "image": "2D 影像",
    "another_vocal": "Another Vocal",
}

# ---------------------------------------------------------------------------
# Accents and command aliases
# ---------------------------------------------------------------------------
KIND_ACCENTS: dict[str, str] = {
    "card": "#D85A8A",
    "song": "#4C9DF0",
    "gacha": "#B86BD8",
    "event": "#E05A7A",
    "catalog": "#0072C2",
    "help": "#4C9DF0",
    "status": "#3DBFA0",
}

# Longest aliases are matched first by the router.
CARD_COMMANDS = ("查卡", "查牌", "查卡牌", "查卡面", "看卡", "卡牌", "卡面", "卡图")
CARD_LIST_COMMANDS = ("卡牌列表", "卡牌查询", "卡牌筛选")
# Only the English ``card`` sends the original art; the Chinese 查卡面 family
# is just another name for the card detail page.
CARD_ART_COMMANDS = ("card",)
SONG_COMMANDS = (
    "查歌曲",
    "查音乐",
    "查询乐曲",
    "查曲",
    "查歌",
    "乐曲",
    "歌曲",
    "看歌",
    "song",
    "music",
)
JACKET_COMMANDS = ("查曲绘", "曲绘", "jacket")
CHART_COMMANDS = ("查谱面", "谱面预览", "谱面", "chart")
CONSTANT_COMMANDS = ("查定数", "定数排行", "定数表", "难度排行")
RANDOM_COMMANDS = ("随个", "随机曲", "随机歌曲")
NOTES_COMMANDS = ("查物量", "物量")
GACHA_COMMANDS = ("查卡池", "卡池", "gacha")
GACHA_LIST_COMMANDS = ("卡池列表", "卡池一览")
EVENT_COMMANDS = ("查活动", "活动", "event")
EVENT_LIST_COMMANDS = ("活动一览", "活动列表")
CATALOG_COMMANDS = ("查图鉴", "图鉴", "catalog")
HELP_COMMANDS = ("帮助", "help", "说明", "指令")
STATUS_COMMANDS = ("检查数据源", "数据源状态", "源状态")
UPDATE_COMMANDS = ("update", "更新数据源")
FETCH_COMMANDS = ("拉取",)
GACHA_SIM_COMMANDS = (
    "模拟抽卡",
    "十连抽",
    "十连",
    "十連",
    "抽卡",
    "单抽",
    "10连",
)
GACHA_RESET_COMMANDS = ("重置抽卡", "重置抽数", "清空抽卡")

# Commands that must carry the ``pjsk`` prefix so ordinary chat cannot trigger
# them by accident ("十连歪了", "300抽保底").
PREFIX_REQUIRED = set(GACHA_SIM_COMMANDS) | set(GACHA_RESET_COMMANDS)

# A bare "图鉴" is easy to collide with other plugins, so it needs a prefix.
PREFIX_REQUIRED.add("图鉴")

ALL_COMMANDS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [(word, "card") for word in CARD_COMMANDS]
        + [(word, "card_list") for word in CARD_LIST_COMMANDS]
        + [(word, "card_art") for word in CARD_ART_COMMANDS]
        + [(word, "song") for word in SONG_COMMANDS]
        + [(word, "jacket") for word in JACKET_COMMANDS]
        + [(word, "chart") for word in CHART_COMMANDS]
        + [(word, "constant") for word in CONSTANT_COMMANDS]
        + [(word, "random") for word in RANDOM_COMMANDS]
        + [(word, "notes") for word in NOTES_COMMANDS]
        + [(word, "gacha") for word in GACHA_COMMANDS]
        + [(word, "gacha_list") for word in GACHA_LIST_COMMANDS]
        + [(word, "event") for word in EVENT_COMMANDS]
        + [(word, "event_list") for word in EVENT_LIST_COMMANDS]
        + [(word, "catalog") for word in CATALOG_COMMANDS]
        + [(word, "help") for word in HELP_COMMANDS]
        + [(word, "status") for word in STATUS_COMMANDS]
        + [(word, "update") for word in UPDATE_COMMANDS]
        + [(word, "fetch") for word in FETCH_COMMANDS]
        + [(word, "gacha_sim") for word in GACHA_SIM_COMMANDS]
        + [(word, "gacha_reset") for word in GACHA_RESET_COMMANDS],
        key=lambda entry: len(entry[0]),
        reverse=True,
    )
)

# ---------------------------------------------------------------------------
# Asset endpoints
# ---------------------------------------------------------------------------
ASSET_BASE = REGIONS["jp"].asset_base
CHAR_ICON_BASE = "https://moe.exmeaning.com/assets"

# Bundled decoration icons (small, stable, and far more reliable than pjsk.moe).
ICON_DIR = "icons"


def card_art_url(asset: str, after: bool = False, base: str = ASSET_BASE) -> str:
    name = "card_after_training" if after else "card_normal"
    return f"{base}/character/member/{asset}/{name}.webp"


def card_thumb_url(asset: str, after: bool = False, base: str = ASSET_BASE) -> str:
    suffix = "after_training" if after else "normal"
    return f"{base}/thumbnail/chara/{asset}_{suffix}.png"


def jacket_url(asset: str, base: str = ASSET_BASE) -> str:
    return f"{base}/music/jacket/{asset}/{asset}.webp"


def jacket_thumb_url(asset: str, base: str = ASSET_BASE) -> str:
    return f"{base}/thumbnail/music_jacket/{asset}.webp"


# The chart host serves one generated SVG per song and difficulty. The PNG on
# its chart-image page is produced client-side, so the plugin downloads the
# SVG and rasterises it through the same t2i pipeline as every other page.
CHART_HOST = "https://pjsk.moe"


def chart_svg_url(music_id: int, difficulty: str) -> str:
    return f"{CHART_HOST}/chart-svg/{int(music_id)}/{difficulty}.svg"


def char_icon_url(character_id: int) -> str:
    return f"{CHAR_ICON_BASE}/chr_ts_{character_id}.png"


def event_banner_url(asset: str, base: str = ASSET_BASE) -> str:
    return f"{base}/home/banner/{asset}/{asset}.webp"


def gacha_banner_url(gacha_id: int, base: str = ASSET_BASE) -> str:
    return f"{base}/home/banner/banner_gacha{gacha_id}/banner_gacha{gacha_id}.webp"


def costume_thumb_url(asset: str, base: str = ASSET_BASE) -> str:
    return f"{base}/thumbnail/costume/{asset}.webp"

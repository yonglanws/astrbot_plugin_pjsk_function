# AstrBot Plugin - PJSK 查询

世界计划 colorful stage（PJSK）查询插件：**国服（默认）与日服**双服务器，查卡、查曲、查活动、查卡池、模拟抽卡与图鉴。所有页面由 **Jinja2 + AstrBot t2i** 渲染成图片，排版与交互风格与同系列插件保持一致。

## 功能

- **双服支持**：默认查询国服；命令前加 `jp`（或 `日服`）切换日服，加 `cn`（或 `国服`）显式指定国服。两个服务器的 MasterData、素材目录、卡池抽数与页面提示完全独立。
- **查卡**：卡牌详情（花前/花后卡面、综合力、满级技能说明、当期活动/卡池、关联服饰）、多条件卡牌列表（列出全部匹配卡牌，网格同时展示花前与花后缩略图，没有花后的卡牌使用单图展示；未实装卡在卡面左上角带「未实装」角标；支持团/纯团/团 VS、细分技能「团分 / p分 / 判分 / 血分 / 大分」、`leak` 未实装卡、`event123` 活动卡与 `mnr1` 箱活卡筛选，`miku-1` / `miku/1` 查角色最新卡）。
- **查曲**：歌曲详情（中文译名、演唱版本、难度/物量/社区定数、歌曲别名、活动曲）、按等级分组的歌曲列表、难度等级索引、随机歌曲、按物量查找。
- **查谱面**：直接发送谱面 PNG 原图（服务端内联音符贴图与曲绘后把 SVG 转为 PNG），图内含曲名、难度、等级与 BPM，支持全部六个难度。
- **查定数**：按官方等级查歌（单级/区间）或查看整张等级表，按等级降序排列；数据只取 MasterData 的 playLevel，不接入社区定数表。
- **查活动**：当前/指定活动详情，包含活动曲、加成角色与加成卡牌（卡牌列表同款花前/花后网格）；活动一览。
- **查卡池**：当前/指定卡池详情，包含稀有度概率、UP 卡单抽概率、抽取方式与卡池说明；卡池搜索与列表。
- **模拟抽卡**：按卡池权重抽取，10 连 ★3 以上保底，支持角色过滤、指定卡池与 1–100 抽；累计抽数与 ★4/UP 记录按服务器与用户分别保存。
- **图鉴**：卡牌/歌曲/活动/卡池总量，稀有度、属性、团队、难度分布，最新收录。
- **原图直发**：`card <卡牌ID>` 发送游戏原始卡面 PNG（花前/花后），`查曲绘` 发送歌曲封面 PNG。
- **数据维护**：国服/日服独立多源自动更新、版本检查、数据源状态页、单文件手动拉取。

## 命令

所有命令支持 `#` `/` `!` `.` 前缀。**服务器前缀**写在命令最前面，可与命令连写、留空格或用符号分隔：`cn查卡`、`国服查卡`、`jp查曲`、`jp 查活动`、`jp/查卡`、`pjsk/查卡`。不带服务器前缀时使用默认服务器（国服）。

**模拟抽卡相关命令必须带 `pjsk` 或服务器前缀**（如 `pjsk十连`、`cn十连`、`jp十连`），避免日常聊天误触发。

| 命令 | 别名 | 示例 |
| --- | --- | --- |
| 查卡 | 查牌 / 查卡面 / 卡面 / 卡图 / 看卡 / 卡牌 | `查卡 123`、`查卡面 123`、`查卡 miku 4`、`jp查卡 vbs`、`查卡 团分`、`查卡 miku-1` |
| 卡牌列表 | 卡牌查询 / 卡牌筛选 | `卡牌列表 miku 4`、`卡牌列表 限定 分卡` |
| card | 卡面原图（仅英文） | `card 123`、`jp card 123` |
| 查曲 | 查歌 / 查歌曲 / song / music | `查曲 Tell Your World`、`查曲 id123`、`查曲 mnr1`、`查曲 MASTER 32` |
| 查曲绘 | 曲绘 / jacket | `查曲绘 123` |
| 查谱面 | 谱面 / 谱面预览 / chart | `谱面 300`、`谱面 ザムザ ex`、`查谱面 1 append` |
| 查定数 | 定数表 / 定数排行 / 难度排行 | `查定数 master 32`、`查定数 master 30-32`、`查定数 append` |
| 随个 | 随机曲 | `随个 32` |
| 查物量 | 物量 | `查物量 1000` |
| 查活动 | 活动 / event | `查活动`、`查活动 200` |
| 活动一览 | 活动列表 | `活动一览` |
| 查卡池 | 卡池 / gacha | `查卡池`、`查卡池 30016` |
| 卡池列表 | 卡池一览 | `卡池列表` |
| 十连 | N连 / N抽 / 抽卡 / 单抽 | `pjsk十连`、`cn十连`、`pjsk100抽`、`jp十连 miku 4053` |
| 重置抽卡 | 重置抽数 / 清空抽卡 | `pjsk重置抽卡 30016`、`jp重置抽卡 4053` |
| 查图鉴 | 图鉴（裸「图鉴」需 pjsk 或服务器前缀） | `pjsk图鉴`、`cn图鉴` |
| 帮助 | help / 说明 | `帮助` |
| 检查数据源 | 数据源状态 / 源状态 | `检查数据源` |
| update | 按需检查更新（管理员可用「更新数据源」强制刷新） | `update`、`更新数据源` |
| 拉取 | —（管理员） | `拉取 cards.json`、`拉取 jp cards.json` |

### 搜索写法

- 角色昵称：`miku`、`ick`、`saki`、`hnm`、`shiho`、`mnr`、`hrk`、`airi`、`shizuku`、`kohane`、`an`、`akito`、`toya`、`tsukasa`、`emu`、`nene`、`rui`、`kanade`、`mafuyu`、`ena`、`mizuki`，也支持中文名与常见别名；`miku-1`（或 `miku/1`）查该角色最新一张卡。
- 团名：`ln` / `mmj` / `vbs` / `ws` / `25h` / `vs`；团 oc：`mmjoc` / `纯mmj`；团 vs：`mmjv`；纯 VS：`vs` / `纯v`。
- 稀有度：`1`–`4`、`生日`；属性：`蓝` / `橙` / `紫` / `粉` / `绿`；限定：`非限`、`期间`、`fes`、`bfes`、`cfes`、`联动`。
- 技能：`奶` / `分` / `判`；细分技能：`团分`（或 `组分`）、`p分`、`判分`、`血分`、`大分`；`leak` 只看未实装卡。
- 卡牌活动筛选：`event123` 按活动 ID、`mnr1` 按角色第 1 次箱活。
- 难度：`EZ` / `NM` / `HD` / `EX` / `MA` / `AP`，也接受 `easy`、`normal`、`hard`、`expert`、`master`、`append`；难度可与等级连写（`ma32`、`ma30-32`）。
- 歌曲特殊写法：`id123` 按 ID、`#123` 按 ID、`-1` 最新发布、`mnr1` 角色第 1 次箱活、`event123` 活动书下曲。
- 等级（定数）：单个整数查该级（`查定数 master 32`），两个数字查区间（`30-32`）；等级取自官方 MasterData，不使用社区小数定数表。
- 谱面预览图来自 pjsk.moe（以日服数据为准）：服务端下载 SVG、把音符贴图与曲绘内联为 data URI，再按原图尺寸转成 PNG 直接发送。

## 项目结构

```
astrbot_plugin_pjsk_function/
├── main.py                  # 插件入口：生命周期、命令路由、页面组装
├── pjsk/
│   ├── constants.py         # 数据源、难度/属性/稀有度/团名、命令别名、素材地址
│   ├── assets.py            # 图片下载、缩放、磁盘缓存、data URI 内嵌、SSRF 防护
│   ├── masterdata.py        # 本地加载、索引、多源同步、版本检查、定时任务
│   ├── models.py            # 卡牌/歌曲/卡池/活动视图模型与技能文案展开
│   ├── search.py            # 歌曲/卡牌/卡池/活动检索与筛选
│   ├── gacha.py             # 卡池权重、10 连保底、累计抽数存储
│   ├── render.py            # Jinja2 + AstrBot html_render（t2i）渲染
│   └── templates/           # base / 帮助 / 图鉴 / 卡牌 / 歌曲 / 卡池 / 活动 / 状态
├── res/
│   ├── icons/               # 稀有度边框、属性图标、星星
│   └── misans-subset.b64.txt# 内嵌 MiSans 字体子集（base64）
├── data/                    # 字体源文件、MasterData 缓存（master = 日服，master/cn = 国服）、图片缓存、渲染输出
├── tests/
│   ├── test_plugin.py       # 离线检查：路由、数据、检索、视图、模板
│   ├── render_samples.py    # 通过真实 t2i 渲染全部页面
│   └── build_font.py        # 重建 MiSans 字体子集
├── _conf_schema.json
├── metadata.yaml
└── requirements.txt
```

## 安装

1. 将插件目录放入 AstrBot 的 `data/plugins/`（或直接从本仓库 clone 到该目录）。
2. 在 AstrBot WebUI 的插件管理中启用；`requirements.txt` 的依赖由 AstrBot 自动安装。
3. 确保 AstrBot 已配置 t2i 服务（`t2i_endpoint`），或在本插件配置中填写 `render_endpoint` 作为回退。

仓库不携带 MasterData 缓存，首次加载会在后台同步两个服务器的数据；首次查询时若缓存仍为空，插件会等待同步完成后返回图片。字体子集、装饰图标和 `data/characters.json` 已随仓库提供，因此 clone 后无需额外素材即可运行。

## 数据来源

- **国服 MasterData**：[Team-Haruki/haruki-sekai-sc-master](https://github.com/Team-Haruki/haruki-sekai-sc-master)，缓存到 `data/master/cn/`。
- **日服 MasterData**：[Team-Haruki/haruki-sekai-master](https://github.com/Team-Haruki/haruki-sekai-master)，缓存到 `data/master/`。
- 两个服务器都按优先级降级：
  1. GitHub Contents API（快，未认证限流 60 请求/小时）
  2. jsDelivr CDN（无限流，单文件上限 20 MB）
  3. GitHub raw 直连
  4. 本地缓存兜底
- **歌曲翻译**：[translation.exmeaning.com](https://translation.exmeaning.com)（日文原名 → 中文译名；国服标题本身为中文时直接显示国服标题）。
- **歌曲别名**：[moe.exmeaning.com](https://moe.exmeaning.com/data/music_alias/music_aliases.json)。
- **社区定数**：[pjskb30](https://moe.exmeaning.com/data/pjskb30/merged_chart.csv)（非官方，仅供参考）。
- **游戏素材**：国服 `storage.exmeaning.com/sekai-cn-assets`、日服 `storage.exmeaning.com/sekai-jp-assets`（卡面、缩略图、曲绘、活动横幅、服饰）与 `moe.exmeaning.com`（角色头像）。

数据每天自动检查一次，两个服务器分别比对版本；版本一致时只读取版本文件。管理员可发送 `更新数据源` 强制全量刷新，或用 `拉取 文件名` / `拉取 jp 文件名` 单独更新。

## 渲染

- 默认使用 AstrBot 自带的 `Star.html_render`，转发到 AstrBot 配置的 t2i 服务；`render_endpoint` 仅作为直连回退。
- 画布 1080 px。卡牌/歌曲详情页输出 PNG；列表、活动、卡池、帮助、图鉴、状态页输出 JPEG（质量 90）。
- t2i 的 `viewport_height` 使用扁平参数（约 200 px），由 `full_page` 扩展到真实文档高度，避免短页面底部出现空白带。
- 所有图片（卡面、曲绘、头像、横幅、服饰）下载后缩放为 WebP 并以 data URI 内嵌，渲染服务不需要访问外网。
- MiSans 字体以 base64 子集内嵌；模板或 MasterData 出现新字符后运行 `python tests/build_font.py` 重建。

## 配置

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `default_region` | `cn` | 默认服务器；`cn` = 国服，`jp` = 日服 |
| `auto_sync` | `true` | 每日自动同步两个服务器的 MasterData |
| `sync_hour` | `4` | 每日同步小时（固定第 30 分钟） |
| `render_endpoint` | 空 | 可选的直连 t2i 回退地址 |
| `render_timeout` | `120` | 单张图片渲染超时（秒） |
| `image_timeout` | `60` | 图片下载超时（秒） |
| `sync_timeout` | `90` | 单个数据表下载超时（秒） |
| `data_source_bases` | `[]` | 自定义日服 MasterData 镜像，优先于内置源 |
| `cn_data_source_bases` | `[]` | 自定义国服 MasterData 镜像，优先于内置源 |

## 开发

```bash
# 离线检查（不需要 AstrBot，不联网）
python tests/test_plugin.py

# 通过真实 t2i 渲染全部页面到 tests/output/
python tests/render_samples.py
python tests/render_samples.py --only card_detail,gacha_result

# 模板或数据更新后重建字体子集
python tests/build_font.py
```

依赖：`aiohttp`、`Jinja2`、`Pillow`。测试脚本会为 AstrBot API 安装最小桩模块，因此可以直接在插件根目录运行。

## 说明

- 模拟抽卡仅用于娱乐，不消耗任何游戏资源；概率来自卡池公布的稀有度概率与卡牌权重。
- 歌曲定数来自社区整理，非官方数据，仅供参考。
- 卡牌标题、技能描述与活动/卡池名称以对应服务器 MasterData 为准：国服显示简体中文，日服显示日文。
- 国服与日服的模拟抽卡累计记录分开保存，互不影响。

## 许可

MIT License

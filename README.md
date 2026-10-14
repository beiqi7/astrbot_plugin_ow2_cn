# astrbot_plugin_ow2_cn

《守望先锋 2》**国服**战绩查询插件，面向 QQ 群（NapCat / aiocqhttp 接入）。

数据来自网易大神（网易 DD）的国服战绩接口 —— 也就是「网易大神 App / 网易 DD 战绩工具」背后的那套数据。

> 国服目前没有官方公开 API，只有网易大神这一条真实数据链路。
> 所以"插件端要不要 token"取决于你把凭据放在哪里，详见下面「两种数据源」。

## 指令

| 指令 | 说明 |
| --- | --- |
| `/ow 绑定 光头大猩猩#5104` | 绑定自己的 BattleTag（个人绑定） |
| `/ow 绑定群 光头大猩猩#5104` | 把某个账号设为**本群默认账号**（需管理员） |
| `/ow 解绑` | 解除自己的绑定 |
| `/ow 解绑群` | 解除本群默认账号（需管理员） |
| `/ow 战绩 [模式] [BattleTag]` | 战绩总览：资料卡 + 段位 + 近期表现 + 最近对局 + 常用英雄 |
| `/ow 对局 [页码] [模式] [BattleTag]` | 最近对局列表 |
| `/ow 明细 [序号] [模式] [BattleTag]` | **单局明细**：双方阵容、禁用英雄、时长、每个人的 KDA/伤害/治疗/抗伤，以及"本场最佳"标记。**不带序号时默认最近一场**，序号对照 `/ow 对局` 的编号 |
| `/ow 英雄 [模式] [BattleTag]` | 英雄使用统计：场次、胜率、时长、段位，以及**每 10 分钟**的伤害 / 治疗 / 消灭 / 阵亡 |
| `/ow 资料 [模式] [BattleTag]` | 基础资料卡 |
| `/ow 图 [模式] [BattleTag]` | 图片形式输出 |
| `/ow 竞技` | 竞技（排位）数据 |
| `/ow 生涯`（= `/ow 快速` / `/ow 休闲`） | **生涯 / 非竞技**数据：快速比赛（含开放队列的职责数据） |
| `/ow 状态` | 当前数据源与缓存情况 |
| `/ow 清除缓存` | 清空查询缓存（管理员） |
| `/ow 帮助` | 指令列表 |

要点：

- 所有查询指令都支持 **`@群友`**：对方绑定过就能直接查他的战绩，例如 `@某人 /ow 战绩`。
- 也可以直接跟在指令后面：`/ow 战绩 光头大猩猩#5104`。
- 查询指令的参数顺序不限：「序号/页码」只对 `/ow 对局`、`/ow 明细` 有效，「模式」（竞技 / 生涯 / 快速 / 休闲 …）
  所有查询指令都认，例如 `/ow 明细 2 生涯 光头大猩猩#5104` = 生涯里的第 2 场；`/ow 对局 2` = 竞技第 2 页；
  `/ow 英雄 生涯` = 自己在快速里的英雄数据（不会把「生涯」当成 BattleTag 去查人）。
- 指令组名和子指令之间需要空格：`/ow 战绩` 而不是 `/ow战绩`。
- 查询自己时不用带参数（会用你的绑定；个人绑定查不到时回落到群绑定）。

关于「生涯」：国服上游只有**竞技（sport）**与**快速（leisure）**两个数据集，没有单独的"全部模式"，
所以生涯/休闲/开放这些说法统一落到快速数据集——它同时包含开放队列的职责数据、最近对局与英雄统计。
快速没有段位概念，卡片会自动把「段位」换成「职责数据」，不再显示"未定级"。

两个实测出来的上游行为，插件已经按它们处理（`overstats` 数据源）：

- **`/dashen-match` 忽略 mode 参数**，一次返回竞技 + 快速按时间**混排**的列表
  （实测 24 条竞技 + 24 条快速交错，竞技是 `IT_RANKED`/`SportPreset`，快速是 `IT_UNRANKED`/`LeisurePreset`）。
  所以插件在本地按 `instanceType` 分流：`/ow 对局 竞技` 只出竞技、`生涯` 只出快速，翻页也在本地切片，
  序号连续（第 2 页从 6 开始），可以直接拿去 `/ow 明细`。
- **明细与明细图都用 `match_id + customer_token` 精确定位**：`/dashen-match/detail` 传 `index`
  时是按上面那份混排列表定位的（会串场），只有 `match_id` 才可靠；明细图则走
  `/api/v2/dashen-match/detail/replies`（它支持 `match_id` 并返回 base64 图，`/detail/image` 只认 `index`）。
  `dashen` 直连数据源本身按 `gameMode` 区分，无此问题。

`dashen` 直连数据源的翻页同样在本地切片：上游 `queryMatchList` 每页多少条没有文档，
所以插件不把「插件第 N 页」当成「上游第 N 页」，而是按需往后翻、累积成一份列表再切
（最多翻 5 页；遇到空页、短页或整页重复就停）。`/ow 对局 2` 的第 6 条就是真正的第 6 场，
和 `/ow 明细 6` 是同一局；列表翻页与明细共用这份列表，每个上游页只拉一次。

上游 `rankScore` 不是分数，而是「段位 id × 100 + (100 − 小段)」的紧凑编码（黄金 5 = 295、白金 5 = 395），
当"分"展示会误导，所以卡片只显示段位与小段。另外 `recentMatchCount` 里的场均阵亡恒为 0（服务端补的默认值），
插件会用同一批对局现算，避免出现"场均阵亡 0"和算不出来的 KDA。

实际输出示例（纯文本卡片，默认）：

```
🦍 光头大猩猩#5104 · 竞技
────────────────
称号等级 412 | 总时长 1,063.3 小时 | 总场次 1,234 | 称号 纸糊的支援

【段位】
  坦克：白金 5  128 场 · 胜率 55.5% · KDA 4.12 · 最高连胜 7
  输出：黄金 2  67 场 · 胜率 64.2% · KDA 5.80 · 最高连胜 9
  辅助：钻石 4  254 场 · 胜率 58.3% · KDA 6.31 · 最高连胜 12

【近期表现】
  胜率 66.7% · KDA 5.50 · 场均 8/14/4（消灭/助攻/阵亡） · 伤害 3634 · 治疗 9329

【最近对局】
  1. 胜 3:1  伊利奥斯  辅助/卢西奥  KDA 15/16/4  伤害 4,631  治疗 6,801  2 小时前
  ...

【常用英雄】
  1. 卢西奥（辅助）  132 场 · 胜率 63.6% · 18.4 小时
  ...
```

## 两种数据源：token 放哪

| 方案 | 插件端是否需要 token | 需要什么 |
| --- | --- | --- |
| `overstats`（推荐） | **不需要** | 一个 Overstats 服务地址（自建，或任意 Overstats 兼容服务） |
| `dashen`（自包含，但需要凭据） | 需要 | 大神 `role_id` + `token` |

### 方案 A：Overstats，插件端零 token（推荐）

[Overstats](https://github.com/AddOneSecondL/Overstats) 是一套自托管服务，把网易大神上游封装成统一的本地 HTTP API，**大神 token 配在服务端**。

部署好 Overstats（`python -m overstats.run`，默认 `http://127.0.0.1:18080`）后，配置里只改两个值：

```json
{
  "data_source": "overstats",
  "overstats": { "base_url": "http://127.0.0.1:18080" }
}
```

`base_url` 也可以指向任意遵循 Overstats HTTP 契约的服务，插件对它们一视同仁。
Overstats 服务端还能直接渲染战绩图，所以 `render.image_source = server` 时连 AstrBot 的 T2I 都不需要。

如果服务前置了网关鉴权，把 key 填到「接口鉴权」即可（以 `Authorization: Bearer <值>` 发送）。

> 说明：Overstats 本身仍然需要大神 token，只不过 token 留在服务端。
> 所以「插件端零 token」的前提是：服务端已经配好（按 Overstats 的 `Faststart.md` 拿一次 token）。
> 插件本身不接触任何大神凭据。

### 方案 B：直连网易大神，需要 token

上游 `datamsapi.ds.163.com` 只认 `(role_id, token)` 这一对凭据：**只有 Cookie 不行**（实测：带真实 Cookie 直接打 `searchBnetAccount` 仍返回 `{"code":401,"errMsg":"Token Invalid"}`）。两个值这样拿：

**1) `role_id`**

`role_id` 是你大神账号绑定的守望先锋角色 id，只能从你自己的登录态里取，常见两种途径：

- 网易大神 App：进入「守望先锋」→「我的/战绩」，用抓包工具（手机端）看请求里的 `role_id` / `roleId`；
- 暴雪国服充值中心（<https://shop.battlenet.com.cn/zh-cn/product/overwatch>）：用**战网账号**登录后，`F12` → Network → 刷新，搜索 `role_id`。
  注意国服充值中心走的是暴雪自有 OAuth（会跳 `oauth.battlenet.com.cn`），**网易通行证的 Cookie 不能免登录**，必须真的用战网账号登录一次。

**2) `token`**

在**已登录大神**的浏览器标签页里跑 [`tools/get_dashen_token.js`](./tools/get_dashen_token.js)：把文件内容整段粘贴到 F12 → Console，把第一行的 `ROLE_ID` 换成上一步的值，回车即可。成功时会打印可直接填进插件配置的 JSON。

> ⚠️ 网上流传的旧脚本直接用 `window.sig.default()`，在现在的 ds.163.com 上会报 `Cannot read properties of undefined`：
> 官方把签名模块改成了 webpack chunk，`window.sig` 不再暴露为全局变量。
> `tools/get_dashen_token.js` 会自己把模块加载进来，并从 `bootstrap.js` 里**自动解析资源哈希**，官方发版后一般不用改。
> 该脚本的签名结果已与官方页面自身发出的签名逐字节比对一致。


排错对照（都是实测过的返回码）：

| 返回 | 含义 | 处理 |
| --- | --- | --- |
| `{"code":200,...,"token":...}` | 成功 | 把 token 与 role_id 填进插件配置 |
| `{"code":825,"errmsg":"签名无效"}` | `role_id` 不是你账号下的角色（签名本身没问题） | 核对 `role_id`，或先在 App 里绑定战网 |
| `{"code":803,"errmsg":"参数错误"}` | body 缺 `roleId` 等必填字段 | 检查脚本是否被截断 |
| `{"code":873,"errmsg":"你离开太久了..."}` | 会话过期 | 重新登录大神再试 |
| chunk 404 / 加载失败 | 官方换了 CDN 路径 | 在 ds.163.com 的 F12 → Network 里找 `sig/` 下的 `bootstrap.js`，把脚本开头的 `SIG_CDN` 改成对应目录；chunk 哈希脚本会自动解析 |

**3) 填进插件配置**

```json
[{ "name": "primary", "role_id": 123456789, "token": "xxxxxxxx" }]
```

多个账号可以填多条，插件会轮转使用；某个账号 token 失效时会冷却 5 分钟，**当次请求自动换下一个账号重发**，
群友不会看到报错（所有账号都失效时才提示）。token 有效期通常很长，但会过期，届时重复第 2 步。

## 安装

1. 把本目录放到 AstrBot 的 `data/plugins/` 下（目录名保持 `astrbot_plugin_ow2_cn`）；
2. 在 WebUI 插件页点「重载插件」，首次加载会自动安装 `requirements.txt`（只需 `httpx`）；
3. 进入插件配置，按上面的说明选数据源、填配置；
4. 群里发送 `/ow 帮助` 验证。

NapCat 侧没有额外要求：本插件只用文本、图片、`@` 三种能力，NapCat（aiocqhttp 适配器）都支持。
注意 AstrBot 的唤醒前缀（默认 `/`）要对得上。

关于代理：本机 / 内网地址（`127.0.0.1`、`localhost`、`192.168.x.x`、docker 服务名等）总是直连，
不会被系统代理（例如 Windows 上 Clash 写入的注册表代理）劫持成 502；公网地址仍按环境代理设置走。

## 配置项

- `data_source`：`dashen` 或 `overstats`。两者互为兜底并各打一条警告日志：`overstats` 未填地址但有可用大神账号时回退到直连；
  `dashen` 没有可用账号但填了 Overstats 地址时改用 Overstats（默认数据源是 `dashen`，只填了 `base_url` 忘了切数据源也能用）
- `dashen.accounts` / `dashen.dts` / `dashen.server` / `dashen.api_root` / `dashen.timeout`：直连配置（`api_root` 可指向调试用 mock 上游）
- `overstats.base_url` / `overstats.api_key` / `overstats.*_path` / `overstats.timeout` / `overstats.image_timeout`：Overstats 服务配置
  （明细图走 `match_detail_replies_path`，默认 `/api/v2/dashen-match/detail/replies`；
  `image_timeout` 默认 60 秒——服务端渲染图实测要 10~40 秒，比 JSON 慢得多）
- `query.default_mode`：默认 `competitive`（竞技）；`quick` 走快速
- `query.match_limit` / `match_limit_max` / `hero_limit`：条数控制
- `query.cache_ttl`：战果缓存秒数（默认 120，群聊里能明显减少上游请求；Overstats 的原始响应、服务端渲染好的图片、
  直连的对局列表也按这个 TTL 缓存，所以 `/dashen-profile` 一次请求会被竞技与生涯查询共用、连着两次 `/ow 图` 只会渲染一次）。
  战绩数据只分两档回源：常规条数（`/ow 资料`、`/ow 战绩`、`/ow 英雄` 共用一份）和上限条数（图片卡片），不再每条指令各打一遍上游
- `query.token_cache_ttl`：BattleTag → customerToken 缓存（默认 6 小时）
- `query.allow_any_battle_tag`：关闭后只能查询已绑定的账号（防止群里刷别人的号）
- `binding.scope`：`both`（默认，个人绑定 + 群绑定兜底）/ `group` / `user`
- `binding.allow_relaxed_tag`：是否允许绑定时不带 `#数字`（默认关闭：国服上游只认完整 BattleTag，省略后基本查不到人；
  `/ow 绑定` 与 `/ow 绑定群` 都受它约束）
- `basic.max_retry`：连接失败与 HTTP 502/503/504 的重试次数（两种数据源都生效；服务端渲染图片不重试，失败直接回落）
- `basic.debug`：每次请求上游 / Overstats 打一行日志（路径、HTTP 状态、耗时、用的哪个大神账号），**不会**记录任何 token
- `render.emojis`：文本卡片标题的 emoji 池，留空则用内置随机池（🦍 只是其中一个）；填 `["🦍"]` 就固定成猩猩（图片卡片不放 emoji，避免 T2I 服务缺字体时显示成方框）
- `render.use_image_card`：改用图片卡片（`/ow 图` 总是出图）
- `render.image_source`：图片来源。
  `plugin`（默认）用插件内置模板走 AstrBot 的 `html_render`：段位 / 近期 vs 赛季对比 / 本周表现分三栏、常用英雄 6 个
  （每个英雄带每 10 分钟的伤害 / 治疗 / 消灭 / 阵亡）、
  最近 12 场用定宽表格，PNG 输出，图标内嵌（见 `render.inline_images`），不会出现服务端图里「本周表现压住阵亡条」「最近对局挤在一起」的问题；
  渲染失败（没配 T2I 等）时退到 Overstats 服务端图，再退文本。
  `server` 直接转发 Overstats 服务端渲染好的图（排版固定，小字在群聊里容易糊）
- `render.image_scale`：图片清晰度（`normal` 1.0 / `high` 1.3，默认 / `ultra` 1.8），对应 T2I 服务的 `device_scale_factor_level`
- `render.inline_images`：图片卡片内嵌头像与英雄图标（默认开启）。出图前插件先下载图片、缩到 160 px 以内，
  再以 data URI 嵌进卡片，文转图服务截图时不用再去下载，避免图标只显示一半或空白。
  缩小依赖 Pillow（AstrBot 自带）；下载失败或超时（12 秒）的图片保留原链接，不影响出图
- `render.card_template`：自定义卡片模板路径（留空用插件内置模板；旧版模板字段仍然提供）
- `render.show_avatar`：文本卡片里附头像链接

关于英雄数据：取自上游英雄统计里的 `statPerTenMinCount`（指标名称来自网易大神 `query_tool.json` 的 `heroAttrList`，
例如 `603482350067647671` = 对英雄伤害量、`603482350067646913` = 治疗量）。
用「每 10 分钟」而不是场均，是因为一局里经常换英雄，场均会被上场时长拉低（比如雾子场均伤害 874，但每 10 分钟是 1,327），
不同英雄之间没法比。同一英雄在多个队列都有数据时按上场时长加权合并。英雄维度的统计里没有阻挡伤害，所以卡片不显示这一项。

## 已知限制

- **国服 BattleTag 必须带 `#数字`**（例如 `光头大猩猩#5104`），上游只认完整 BattleTag。
- **不支持外服**：国际服的 `overwatch.blizzard.com` / OverFast 里查不到国服账号（已实测）。
- 上游是**非官方抓包接口**，网易随时可能调整字段或加验证；插件对字段缺失是容错的，但接口路径如果变了需要跟着改（改 `sources/dashen.py` 的路径常量即可）。
- 大神 **token 会过期**（通常很长），失效时插件会提示重新获取；配置多账号可降低影响。
- `queryMatchInfo`（单场详情）已由 `/ow 明细` 指令暴露：展示双方阵容、禁用、时长与每人数据。

## 测试

```bash
# 全部离线测试：假上游 HTTP 服务 + astrbot API 桩
python3 -m unittest discover -s tests -t . -v

# 真机自检：对着你的 Overstats 服务跑完整指令链路（插件端不需要任何 token）
OW2CN_LIVE_BASE_URL=http://127.0.0.1:18080 OW2CN_LIVE_TAG='光头大猩猩#5104' python3 tests/live_check.py

# 真机连通性验证（直连大神上游，用假 token，预期得到"token 已失效"的可读提示）
OW2CN_LIVE_TEST=1 python3 -m unittest tests.test_live_upstream -v
```

测试覆盖：

- `tests/test_parsers.py`：从真实数据里踩出来的解析规则（0 值不能当缺失、近期数据以 recentMatchCount 为准、英雄时长单位是秒、`level` 是称号等级）
- `tests/test_dashen_e2e.py`：绑定 → 战绩 → 渲染全链路；`GL-*` 请求头与请求体字段；翻页（假上游每页 3 条，与插件的 5 条/页错开：
  第 2 页编号与明细一致、超出上游第 1 页的明细、上游忽略 page 参数）；缓存命中与多指令共用回源；任意指令带模式词；
  @群友；群绑定权限与格式校验；token 失效与查无此人的提示；调试日志不泄露 token
- `tests/test_overstats_e2e.py`：Overstats 数据源（插件端零 token）、服务端报错透传、`api_key` 鉴权、`/ow图`、回退逻辑、查无此人的 404；
  图片卡片（默认走插件模板 + PNG + 清晰度参数、渲染失败退服务端图、`server` 来源跳过本地渲染、退文本时不刷出多余对局）
- `tests/test_fixes.py`：真实 @ 消息形态（@群友取绑定、@机器人不误判）、英雄合并胜率、禁用位过滤、序号/tag 解析、青铜段位、缓存 singleflight，
  以及这一轮实测出来的对局池行为（混排列表按模式分流、明细按 `match_id` 定位、翻页序号连续、段位不显示 rankScore、场均阵亡现算），
  还有卡片模板与上下文的字段契约（模板引用的变量必须由 `profile_context` 提供，旧字段不能删）、
  参数解析（带空格的 @昵称、优先取带 # 的参数、模式词不当 BattleTag）、秒/毫秒时间戳、数据源互相兜底
- `tests/test_robustness.py`：本地地址绕过系统代理、配置容错、大神账号故障转移、5xx 重试（图片不重试）、
  缓存 singleflight 的取消语义、绑定文件损坏备份、`/ow 解绑群`、群共用模式下的 @ 提示、BattleTag 格式校验
- `tests/test_images.py`：卡片图片内嵌（下载、缩小保留透明、缓存、失败与超时回落原链接、不下载内网地址、
  `/ow 图` 交给文转图的数据里图片已是 data URI）
- `tests/live_check.py`：真机跑 `/ow 绑定 → 战绩 → 英雄 → 图 → 状态`
- `tests/test_live_upstream.py`：直连 `datamsapi.ds.163.com`，默认跳过

## 目录结构

```
main.py                 命令注册与事件处理（AstrBot Star 插件入口）
_conf_schema.json       WebUI 可视化配置
metadata.yaml           插件元数据
templates/profile.html  图片卡片模板（html_render 用）
ow2cn/
  sources/dashen.py     直连网易大神（GL-* 请求头、账号轮转与冷却）
  sources/overstats.py  Overstats HTTP 服务
  sources/__init__.py   数据源工厂（含回退逻辑）
  net.py                HTTP 客户端（本地地址绕过代理）与重试约定
  images.py             图片卡片的外链图片下载、缩小与内嵌
  config.py             配置读取与容错
  parsers.py            上游 JSON → 统一模型
  models.py             数据模型
  service.py            编排 / 缓存 / 绑定解析
  render.py             文本与图片卡片渲染
  store.py              绑定持久化（data/plugin_data/...）
  resources.py + data/  英雄与地图 guid → 名称映射
tests/                  假上游 + astrbot 桩 + 端到端测试
```

## 更新日志

### v1.2.1

- 修复：图片卡片里的头像、英雄图标有时只显示上半截或空白。原因是文转图服务截图时自己去下载这些外链图片
  （海外 CDN、单张 110~155 KB，实际只显示 24~46 px），网络慢时截图那一刻图片还没下载完。
  现在出图前由插件先下载、缩小（单张约 10 KB）并以 data URI 内嵌，截图时不再依赖网络；
  可用 `render.inline_images` 关闭。图片地址来自上游数据，插件只下载公网 http(s) 图片，不会访问内网地址。
- 文档：Overstats 的配置示例改为自建服务地址；`tests/live_check.py` 改为必须通过 `OW2CN_LIVE_BASE_URL` 指定服务。

### v1.2.0

- 修复：系统配置了代理（例如 Windows 上 Clash 写入的注册表代理）时，指向 `127.0.0.1` / 内网的 Overstats
  或调试上游会被代理转发并返回 HTTP 502。现在本机、内网地址与单段主机名总是直连。
- 修复：缓存 singleflight 里某个等待者被取消（指令超时等）时，会连带取消同一查询的其他等待者；
  回源任务本身被取消时，等待者现在会接手重新回源，而不是一起失败。
- 修复：绑定文件损坏（JSON 被截断等）时会静默按空数据启动，下一次绑定就把原文件整个覆盖掉。现在会先备份为
  `bindings.json.corrupt-<时间戳>` 并打警告日志。
- 优化：配置多个大神账号时，某个账号 token 失效会在**当次请求**里自动换下一个账号重发（以前要等下一条指令）。
- 优化：`basic.max_retry` 现在也覆盖 HTTP 502/503/504，并对 Overstats 数据源生效；服务端渲染图片不重试，避免群友干等。
- 优化：配置容错——字符串数字、非法值、`"false"` 这类字符串布尔不再导致插件加载失败或被当成真；
  对局 / 英雄条数加了 50 的硬上限。
- 优化：长期运行时 BattleTag 缓存与对局窗口锁会清理过期项，不再随查询过的玩家无限增长。
- 新增：`/ow 解绑群`（管理员）解除本群默认账号；`/ow 状态` 显示插件版本。
- 体验：绑定时校验 `昵称#数字` 格式（`#` 后不是数字也会拦住）；群共用绑定模式下 @群友 会直接说明不支持，
  而不是提示「对方没绑定」。
- 元数据：作者改为 `serenite`，仓库地址改为 `https://github.com/beiqi7/astrbot_plugin_ow2_cn`，版本号统一由 `ow2cn/__init__.py` 提供。

### v1.1.0

- 修复：`dashen` 直连时 `/ow 对局 2` 的编号与 `/ow 明细` 对不上（以前把上游第 2 页直接当成插件第 2 页，
  第 6 条其实是上游第 2 页的第 1 条），超过上游第 1 页的序号也查不到明细。现在跨上游分页累积后本地切片。
- 修复：`/ow 英雄 生涯`、`/ow 战绩 生涯`、`/ow 图 生涯` 会把「生涯」当成 BattleTag 去查人；现在所有查询指令都认模式词，顺序不限。
- 修复：带空格的群昵称被 @ 时，残留的半截 `@昵称` 会被当成 BattleTag。
- 修复：`/ow 绑定群` 不校验 `#数字`；绑定作用域为 `user` 时设置群默认账号会静默无效，现在直接提示。
- 优化：`/ow 资料`、`/ow 战绩`、`/ow 英雄` 共用一次回源（以前每种对局条数各请求一次 `queryCountInfo`）。
- 优化：数据源互为兜底——默认的 `dashen` 没配账号但填了 Overstats 地址时直接改用 Overstats；没配大神账号时的提示里也会指出零 token 方案。
- 优化：`basic.debug` 以前是摆设，现在会输出每次上游请求的路径、状态码与耗时（不记录 token）。
- 配置：`binding.allow_relaxed_tag` 默认改为关闭，并去掉了「查询时自动补全」这条并不存在的说明（已有配置不受影响）。
- 健壮性：时间戳自动识别秒 / 毫秒，异常时间戳不再导致整条指令报错。

## 来源与致谢

- 上游接口形态（`searchBnetAccount`、`queryCountInfo`、`queryMatchList`、`queryMatchInfo` 与 `GL-*` 请求头）参考并感谢 [Overstats](https://github.com/AddOneSecondL/Overstats) 项目记录的抓包结果与 API 文档；`overstats` 数据源直接对齐它的 HTTP 契约。
- 插件骨架遵循 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 官方插件模板（[Soulter/helloworld](https://github.com/Soulter/helloworld)）。
- `ow2cn/data/heroes.json`、`ow2cn/data/maps.json` 中的英雄/地图名称提取自网易大神 `query_tool.json`，仅保留 guid → 名称/职责/模式映射。
- 数据版权归暴雪娱乐 / 网易所有，本插件仅用于群内查询展示。

"""AstrBot 插件：守望先锋 2 国服战绩查询。

指令（默认唤醒前缀 `/`，NapCat / aiocqhttp 适配器下群聊直接可用）：

- `/ow 绑定 光头大猩猩#5104`   绑定 BattleTag（群内共用或个人，见插件配置）
- `/ow 绑定群 BattleTag` / `/ow 解绑群`  设置 / 解除本群默认账号（管理员）
- `/ow 解绑`
- `/ow 战绩 [BattleTag]`      查询战绩总览（段位 + 近期表现 + 最近对局 + 常用英雄）
- `/ow 对局 [页码] [BattleTag]` 最近对局列表
- `/ow 明细 [序号] [BattleTag]` 单局明细（双方阵容/禁用/时长/数据）
- `/ow 英雄 [BattleTag]`      英雄使用统计
- `/ow 资料 [BattleTag]`      基础资料卡
- `/ow 图 [BattleTag]`        以图片形式输出（插件模板 T2I 或 Overstats 服务端渲染）
- `/ow 状态`                  查看当前数据源与缓存状态
- `/ow 清除缓存`              清空查询缓存（管理员）

所有查询指令都支持 `@群友`：对方只要绑定过，就能直接查到他的战绩。
战绩/对局/明细/英雄/资料/图 都可以再带一个模式词（竞技 / 生涯 / 快速 …），顺序不限。
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image
from astrbot.api.star import Context, Star, StarTools, register

from .ow2cn import __version__, images, ranks, render
from .ow2cn.config import as_bool, section
from .ow2cn.errors import ConfigError, OwError, PlayerNotFound
from .ow2cn.models import PlayerProfile
from .ow2cn.service import OwService
from .ow2cn.store import SCOPE_GROUP, SCOPE_USER, BindingStore

PLUGIN_NAME = "astrbot_plugin_ow2_cn"
TEMPLATE_NAME = "profile.html"
# 国服 BattleTag 形如「昵称#数字」，上游只认完整写法
BATTLE_TAG_RE = re.compile(r"^[^#\s][^#]*#\d+$")

# 图片卡片比文本卡片能多放一些内容：对局列表给到 12 场、英雄给到 6 个
IMAGE_MATCH_LIMIT = 12
IMAGE_HERO_LIMIT = 6
# html_render 的整体超时（远程 T2I 端点卡住时别把指令挂死）
RENDER_TIMEOUT_SECONDS = 60.0
# 出图前下载卡片图片的时间上限（含在上面的总超时里），超时的图片保留原链接
IMAGE_INLINE_BUDGET_SECONDS = 12.0


@register(
    PLUGIN_NAME,
    "serenite",
    "守望先锋 2 国服战绩查询（网易大神 / Overstats 数据源）",
    __version__,
    "https://github.com/beiqi7/astrbot_plugin_ow2_cn",
)
class Ow2CnPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        # 老版本 AstrBot 的 Star 基类没有 self.logger（4.5.x 就没有），
        # 直接用它会在插件加载时抛 AttributeError，这里回退到模块级 logger。
        self.log = getattr(self, "logger", None) or logger
        self.config = config
        self.data_dir = self._resolve_data_dir()
        self.store = BindingStore(self.data_dir / "bindings.json", logger=self.log)
        self.service = OwService(config, self.store, logger=self.log)
        self.image_inliner = images.ImageInliner(logger=self.log)

    # ---------- 基础设施 ----------

    def _resolve_data_dir(self) -> Path:
        """持久化目录放在 AstrBot 的 data 下，避免插件更新时丢数据。"""
        try:
            return Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception as exc:  # 兼容老版本 AstrBot
            logger.warning(f"获取插件数据目录失败，回退到工作目录：{exc}")
            fallback = Path(os.getcwd()) / "data" / "plugin_data" / PLUGIN_NAME
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def _section(self, name: str) -> dict:
        return section(self.config, name)

    @property
    def binding_scope(self) -> str:
        return self.service.binding_scope

    # ---------- 目标解析 ----------

    # 适配器（aiocqhttp）会把 @群友 转成 " @昵称(QQ) " 拼进消息文本，
    # 这既可能污染显式 BattleTag 参数，也需要在 @ 解析时对齐。
    # 昵称里可以有空格（会被指令参数切成好几段），所以允许空格、限制长度。
    _AT_TEXT_RE: ClassVar[re.Pattern] = re.compile(r"@[^()@\n]{0,40}?\((\d+)\)")

    def _self_id(self, event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_self_id", None)
        if callable(getter):
            try:
                return str(getter() or "")
            except Exception:
                return ""
        return ""

    def _mentioned_users(self, event: AstrMessageEvent) -> list[str]:
        """取出消息里 @ 的 QQ 号（排除 @全体成员与机器人自己）。"""
        self_id = self._self_id(event)
        result: list[str] = []
        for component in event.get_messages() or []:
            if isinstance(component, At):
                qq = str(getattr(component, "qq", "") or "")
                if qq and qq != "all" and qq != self_id:
                    result.append(qq)
        return result

    def _strip_mentions(self, text: str) -> str:
        """把适配器塞进文本里的 @昵称(QQ) 去掉，避免被当成 BattleTag。"""
        return self._AT_TEXT_RE.sub(" ", str(text or "")).strip()

    def _bound_tag(self, scope: str, key: str) -> str | None:
        entry = self.store.get(scope, key)
        if not entry:
            return None
        tag = str(entry.get("tag") or "").strip()
        return tag or None

    def _resolve_target(
        self,
        event: AstrMessageEvent,
        explicit_tag: str = "",
    ) -> str:
        """决定这次查询查谁：显式 BattleTag > @群友 > 自己的绑定 > 群绑定。"""
        group_id = str(event.get_group_id() or "")
        user_id = str(event.get_sender_id() or "")

        tag = self.service.normalize_tag(self._strip_mentions(explicit_tag))
        if tag:
            if not self.service.allow_any_battle_tag and not self._is_bound_tag(
                tag, group_id, user_id
            ):
                raise ConfigError(
                    "当前配置只允许查询已绑定的玩家",
                    hint="请先用 /ow 绑定 绑定 BattleTag，或让管理员开启「允许直接查询任意 BattleTag」。",
                )
            return tag

        mentioned = self._mentioned_users(event)
        if mentioned and self.binding_scope == "group":
            # 群共用模式下没有个人绑定，按「对方没绑定」提示会让人一直去绑一个不存在的东西
            raise ConfigError(
                "当前为群共用绑定模式，不支持 @群友 查询",
                hint="直接写 BattleTag 查询（例如 /ow 战绩 光头大猩猩#5104），"
                "或让管理员把绑定作用域改成 both。",
            )
        for qq in mentioned:
            bound = self._bound_tag(SCOPE_USER, qq)
            if bound:
                return bound

        if mentioned:
            raise PlayerNotFound(
                "被 @ 的群友还没有绑定 BattleTag",
                hint="让他发送 /ow 绑定 BattleTag（例如 /ow 绑定 光头大猩猩#5104）。",
            )

        entry = self.service.resolve_binding(group_id=group_id, user_id=user_id)
        if entry:
            tag = str(entry.get("tag") or "").strip()
            if tag:
                return tag

        raise PlayerNotFound(
            "还没有绑定 BattleTag",
            hint="发送 /ow 绑定 你的BattleTag 即可，例如 /ow 绑定 光头大猩猩#5104。",
        )

    def _is_bound_tag(self, tag: str, group_id: str, user_id: str) -> bool:
        """判断某个 BattleTag 是否已被绑定（用于限制查询范围的配置）。"""
        normalized = tag.strip().lower()
        candidates = []
        if self.binding_scope in {"both", "user"} and user_id:
            candidates.append((SCOPE_USER, user_id))
        if self.binding_scope in {"both", "group"} and group_id:
            candidates.append((SCOPE_GROUP, group_id))
        for scope, key in candidates:
            bound = self._bound_tag(scope, key)
            if bound and bound.strip().lower() == normalized:
                return True
        return False

    # ---------- 参数解析 ----------

    @classmethod
    def _parse_query_args(
        cls,
        args: tuple[str, ...],
        *,
        with_index: bool,
    ) -> tuple[int | None, str, str | None]:
        """解析「[序号] [模式] [BattleTag]」这类顺序任意的参数。

        - 先去掉适配器拼进来的 @昵称(QQ)：昵称带空格时会被切成好几段参数，所以拼回去再去；
        - 纯数字是序号（只认第一个；不需要序号的指令直接忽略）；
        - 竞技 / 生涯 / 快速 等模式关键词是模式（只认第一个）；
        - 其余当 BattleTag，有多段时优先取带 # 的那段。
        """
        index: int | None = None
        mode: str | None = None
        candidates: list[str] = []
        text = " ".join(str(arg) for arg in args if arg)
        for token in cls._AT_TEXT_RE.sub(" ", text).split():
            if token.isdecimal():
                if with_index and index is None:
                    index = max(1, int(token))
                continue
            keyword = ranks.parse_mode_keyword(token)
            if keyword:
                mode = mode or keyword
                continue
            candidates.append(token)
        tagged = [item for item in candidates if "#" in item or "＃" in item]
        tag = (tagged or candidates or [""])[-1]
        return index, tag, mode

    @classmethod
    def _split_index_and_tag(cls, *args: str) -> tuple[int, str, str | None]:
        """`/ow 对局`、`/ow 明细` 的参数：序号省略时默认 1（最近一场 / 第一页）。"""
        index, tag, mode = cls._parse_query_args(args, with_index=True)
        return index or 1, tag, mode

    @classmethod
    def _split_mode_and_tag(cls, *args: str) -> tuple[str, str | None]:
        """`/ow 战绩`、`/ow 英雄` 等的参数：可选模式 + 可选 BattleTag。"""
        _index, tag, mode = cls._parse_query_args(args, with_index=False)
        return tag, mode

    def _check_tag_format(self, tag: str) -> None:
        """绑定前校验 BattleTag：默认要求「昵称#数字」，`#` 后面写错也在这里拦住。"""
        if as_bool(self._section("binding").get("allow_relaxed_tag")):
            return
        if not BATTLE_TAG_RE.match(tag):
            raise ConfigError("BattleTag 需要写成「昵称#数字」", hint="例如 光头大猩猩#5104。")

    # ---------- 输出 ----------

    async def _respond_profile(
        self,
        event: AstrMessageEvent,
        tag: str,
        *,
        mode: str | None = None,
        force_image: bool = False,
    ):
        """按配置决定文本还是图片输出，图片失败时逐级回落。

        图片有两个来源：
        - `plugin`（默认）：用插件自带模板走 AstrBot 的 html_render，排版与清晰度可控；
          渲染不出来（没配 T2I 等）时再退到服务端渲染好的图，最后退文本。
        - `server`：直接用 Overstats 服务端渲染好的图，省一次本地渲染，失败再退 html_render。
        """
        use_image = force_image or as_bool(self._section("render").get("use_image_card"))
        image_source = self._image_source()
        if not use_image:
            return event.plain_result(self._text_of(await self.service.profile(tag, mode=mode)))

        if image_source == "server":
            # 服务端直接出图，不需要先拉文本数据（少一次上游请求）。
            server_image = await self._server_profile_image(event, tag, mode)
            if server_image is not None:
                return server_image

        profile = await self.service.profile(tag, mode=mode, limit=IMAGE_MATCH_LIMIT)
        result = await self._render_html_image(profile, event)
        if result is not None:
            return result

        if image_source == "plugin":
            server_image = await self._server_profile_image(event, tag, mode)
            if server_image is not None:
                return server_image
        return event.plain_result(self._text_of(profile))

    def _image_source(self) -> str:
        source = str(self._section("render").get("image_source") or "plugin").strip().lower()
        return source if source in {"plugin", "server"} else "plugin"

    def _image_scale(self) -> str:
        scale = str(self._section("render").get("image_scale") or "high").strip().lower()
        return scale if scale in {"normal", "high", "ultra"} else "high"

    async def _server_profile_image(
        self,
        event: AstrMessageEvent,
        tag: str,
        mode: str | None,
    ):
        """取 Overstats 服务端渲染好的战绩图（只有 overstats 数据源支持）。"""
        try:
            image_bytes = await self.service.rendered_image(tag, kind="profile", mode=mode)
            if image_bytes:
                return event.chain_result([Image.fromBytes(image_bytes)])
        except OwError as exc:
            self.log.warning(f"Overstats 图片接口不可用：{exc}")
        return None

    def _emoji(self) -> str:
        """每次输出随机取一个 emoji（可在配置里用 render.emojis 自定义池子）。"""
        pool = self._section("render").get("emojis")
        return render.pick_emoji(pool if isinstance(pool, list) else None)

    def _text_of(self, profile: PlayerProfile) -> str:
        render_conf = self._section("render")
        # 图片卡片会多取几场对局，回落到文本时按文本卡片的条数截断，别刷屏。
        profile = replace(profile, matches=profile.matches[: self.service.match_limit])
        return render.text_card(
            profile,
            hero_limit=self.service.hero_limit,
            show_avatar=as_bool(render_conf.get("show_avatar")),
            emoji=self._emoji(),
        )

    async def _render_html_image(
        self,
        profile: PlayerProfile,
        event: AstrMessageEvent,
    ):
        """用 AstrBot 的 html_render 把 profile 渲染成插件自己的卡片。"""
        template = self._load_template()
        if not template:
            return None
        context = render.profile_context(profile, hero_limit=IMAGE_HERO_LIMIT)
        # 两次尝试共用同一个总超时，避免 T2I 端点卡住时把指令挂到两倍时长。
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RENDER_TIMEOUT_SECONDS
        context = await self._inline_card_images(context, deadline=deadline)
        # PNG 比默认的 JPEG(q40) 清晰得多；scale=device 才会用上 device_scale_factor_level。
        attempts = (
            {
                "type": "png",
                "full_page": True,
                "scale": "device",
                "device_scale_factor_level": self._image_scale(),
            },
            # 老版 T2I 服务不接受 "PNG + quality"，退一步用高质量 JPEG。
            {"type": "jpeg", "full_page": True, "quality": 92},
        )
        for options in attempts:
            remaining = deadline - loop.time()
            if remaining <= 1.0:
                break
            try:
                url = await asyncio.wait_for(
                    self.html_render(template, context, options=options),
                    timeout=remaining,
                )
                return event.image_result(url)
            except Exception as exc:
                self.log.warning(f"html_render（{options['type']}）失败：{exc}")
        return None

    async def _inline_card_images(self, context: dict, *, deadline: float) -> dict:
        """把卡片里的外链图片换成 data URI（见 ow2cn/images.py：解决截图时图片只加载了一半）。

        下载失败或超时的图片保留原链接，交给文转图服务自己去加载，不影响出图。
        """
        if not as_bool(self._section("render").get("inline_images"), True):
            return context
        urls = images.collect_image_urls(context)
        if not urls:
            return context
        loop = asyncio.get_running_loop()
        # 给截图本身至少留 15 秒
        budget = min(IMAGE_INLINE_BUDGET_SECONDS, deadline - loop.time() - 15.0)
        try:
            mapping = await self.image_inliner.inline(urls, budget=budget)
        except Exception as exc:
            self.log.warning(f"卡片图片内嵌失败，改用原链接：{exc}")
            return context
        return images.replace_image_urls(context, mapping) if mapping else context

    def _load_template(self) -> str:
        path = str(self._section("render").get("card_template") or "").strip()
        template_path = Path(path) if path else Path(__file__).parent / "templates" / TEMPLATE_NAME
        try:
            return template_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(f"读取卡片模板失败：{exc}")
            return ""

    # ---------- 指令 ----------

    @filter.command_group("ow", alias={"OW"})
    def ow(self):
        """守望先锋 2 国服战绩查询"""

    @ow.command("绑定")
    async def ow_bind(self, event: AstrMessageEvent, tag: str = ""):
        """绑定 BattleTag：/ow 绑定 光头大猩猩#5104"""
        try:
            tag = self.service.normalize_tag(tag)
            if not tag:
                yield event.plain_result(
                    "用法：/ow 绑定 BattleTag\n例如：/ow 绑定 光头大猩猩#5104",
                )
                return
            self._check_tag_format(tag)

            group_id = str(event.get_group_id() or "")
            user_id = str(event.get_sender_id() or "")
            # both/user → 个人绑定（这样才能 @ 某个人查他的战绩）；group → 群内共用绑定
            scope = SCOPE_GROUP if self.binding_scope == "group" else SCOPE_USER
            if scope == SCOPE_GROUP and not group_id:
                raise ConfigError("当前配置只允许群内绑定", hint="请在群里使用该指令。")
            # 群共用绑定影响全群，和 /ow 绑定群 一样只允许管理员改。
            if scope == SCOPE_GROUP and not event.is_admin():
                yield event.plain_result("当前为群共用绑定模式，只有管理员可以绑定/修改本群账号。")
                return

            key = group_id if scope == SCOPE_GROUP else user_id
            self.service.bind(
                scope,
                key,
                tag,
                operator_id=user_id,
                operator_name=event.get_sender_name(),
            )
            where = "本群绑定" if scope == SCOPE_GROUP else "你的个人绑定"
            yield event.plain_result(
                f"绑定成功：{tag}（{where}）\n"
                "现在可以直接发送 /ow 战绩 查询，或让群友 @你 查询你绑定的战绩。",
            )
        except OwError as exc:
            yield event.plain_result(exc.user_text())
        except Exception as exc:
            logger.error(f"绑定失败：{exc}", exc_info=True)
            yield event.plain_result("绑定失败，请查看 AstrBot 日志。")

    @ow.command("绑定群", alias={"群绑定"})
    async def ow_bind_group(self, event: AstrMessageEvent, tag: str = ""):
        """把 BattleTag 绑定为整个群的默认账号（管理员）：/ow 绑定群 光头大猩猩#5104"""
        try:
            if not event.is_admin():
                yield event.plain_result("只有管理员可以设置群绑定。")
                return
            group_id = str(event.get_group_id() or "")
            if not group_id:
                yield event.plain_result("该指令只能在群里使用。")
                return
            tag = self.service.normalize_tag(tag)
            if not tag:
                yield event.plain_result("用法：/ow 绑定群 BattleTag")
                return
            self._check_tag_format(tag)
            if self.binding_scope == "user":
                # user 作用域下查询不会回落到群绑定，设置了也用不上，直接说清楚。
                yield event.plain_result(
                    "当前绑定作用域为「仅个人」，群默认账号不会生效；"
                    "如需使用，请让管理员把绑定作用域改成 both 或 group。",
                )
                return
            self.service.bind(
                SCOPE_GROUP,
                group_id,
                tag,
                operator_id=str(event.get_sender_id() or ""),
                operator_name=event.get_sender_name(),
            )
            yield event.plain_result(f"本群默认账号已设置为：{tag}")
        except OwError as exc:
            yield event.plain_result(exc.user_text())
        except Exception as exc:
            logger.error(f"群绑定失败：{exc}", exc_info=True)
            yield event.plain_result("绑定失败，请查看 AstrBot 日志。")

    @ow.command("解绑群", alias={"群解绑"})
    async def ow_unbind_group(self, event: AstrMessageEvent):
        """解除本群默认账号（管理员）：/ow 解绑群"""
        try:
            if not event.is_admin():
                yield event.plain_result("只有管理员可以解除群绑定。")
                return
            group_id = str(event.get_group_id() or "")
            if not group_id:
                yield event.plain_result("该指令只能在群里使用。")
                return
            if self.service.unbind(SCOPE_GROUP, group_id):
                yield event.plain_result("已解除本群默认账号。")
            else:
                yield event.plain_result("本群没有设置默认账号。")
        except Exception as exc:
            logger.error(f"解除群绑定失败：{exc}", exc_info=True)
            yield event.plain_result("解绑失败，请查看 AstrBot 日志。")

    @ow.command("解绑")
    async def ow_unbind(self, event: AstrMessageEvent):
        """解除绑定"""
        try:
            group_id = str(event.get_group_id() or "")
            user_id = str(event.get_sender_id() or "")
            scope = SCOPE_GROUP if self.binding_scope == "group" else SCOPE_USER
            if scope == SCOPE_GROUP and not event.is_admin():
                yield event.plain_result("当前为群共用绑定模式，只有管理员可以解除本群绑定。")
                return
            key = group_id if scope == SCOPE_GROUP else user_id
            if key and self.service.unbind(scope, key):
                yield event.plain_result(
                    "已解除" + ("本群绑定" if scope == SCOPE_GROUP else "你的绑定"),
                )
            else:
                yield event.plain_result("当前没有绑定记录。")
        except Exception as exc:
            logger.error(f"解绑失败：{exc}", exc_info=True)
            yield event.plain_result("解绑失败，请查看 AstrBot 日志。")

    @ow.command("战绩", alias={"查询", "查"})
    async def ow_profile(
        self,
        event: AstrMessageEvent,
        first: str = "",
        second: str = "",
        third: str = "",
    ):
        """查询战绩总览：/ow 战绩 [模式] [BattleTag]"""
        try:
            tag_arg, mode = self._split_mode_and_tag(first, second, third)
            target = self._resolve_target(event, tag_arg)
            yield await self._respond_profile(event, target, mode=mode)
        except OwError as exc:
            yield event.plain_result(exc.user_text())
        except Exception as exc:
            logger.error(f"查询战绩失败：{exc}", exc_info=True)
            yield event.plain_result("查询失败，请稍后再试或查看 AstrBot 日志。")

    @ow.command("对局", alias={"最近对局"})
    async def ow_matches(
        self,
        event: AstrMessageEvent,
        first: str = "",
        second: str = "",
        third: str = "",
    ):
        """最近对局：/ow 对局 [页码] [模式] [BattleTag]"""
        try:
            page, tag_arg, mode = self._split_index_and_tag(first, second, third)
            target = self._resolve_target(event, tag_arg)
            limit = self.service.match_limit
            matches = await self.service.matches(target, page=page, limit=limit, mode=mode)
            # 列表页只需要名字/模式/对局，构造轻量 profile，省掉一次整档请求。
            profile = PlayerProfile(
                query=target,
                mode=ranks.normalize_mode(mode or self.service.default_mode),
                matches=matches,
            )
            yield event.plain_result(
                render.matches_card(
                    profile,
                    page=page,
                    start=(page - 1) * limit + 1,
                    emoji=self._emoji(),
                ),
            )
        except OwError as exc:
            yield event.plain_result(exc.user_text())
        except Exception as exc:
            logger.error(f"查询对局失败：{exc}", exc_info=True)
            yield event.plain_result("查询失败，请稍后再试或查看 AstrBot 日志。")

    @ow.command("明细", alias={"复盘", "对局明细"})
    async def ow_match_detail(
        self,
        event: AstrMessageEvent,
        first: str = "",
        second: str = "",
        third: str = "",
    ):
        """单局明细：/ow 明细 [序号] [模式] [BattleTag]（序号对应 /ow 对局 里的编号）"""
        try:
            index, tag_arg, mode = self._split_index_and_tag(first, second, third)
            target = self._resolve_target(event, tag_arg)
            use_image = as_bool(self._section("render").get("use_image_card"))
            resolved_mode = ranks.normalize_mode(mode or self.service.default_mode)
            if use_image:
                # 明细图按「列表序号」精确定位（服务端支持 match_id），竞技/生涯都不会串场。
                image = await self._try_detail_image(event, target, index, resolved_mode)
                if image is not None:
                    yield image
                    return
            detail = await self.service.match_detail(target, index=index, mode=mode)
            yield event.plain_result(
                render.match_detail_card(detail, emoji=self._emoji(), order=index),
            )
        except OwError as exc:
            yield event.plain_result(exc.user_text())
        except Exception as exc:
            logger.error(f"查询单局明细失败：{exc}", exc_info=True)
            yield event.plain_result("查询失败，请稍后再试或查看 AstrBot 日志。")

    async def _try_detail_image(
        self,
        event: AstrMessageEvent,
        tag: str,
        index: int,
        mode: str,
    ):
        """优先用 Overstats 服务端渲染的明细图。"""
        try:
            image_bytes = await self.service.rendered_image(
                tag,
                kind="detail",
                mode=mode,
                index=index,
            )
            if image_bytes:
                return event.chain_result([Image.fromBytes(image_bytes)])
        except OwError as exc:
            logger.warning(f"明细图接口不可用，改用文本：{exc}")
        return None

    @ow.command("英雄", alias={"英雄榜"})
    async def ow_heroes(
        self,
        event: AstrMessageEvent,
        first: str = "",
        second: str = "",
        third: str = "",
    ):
        """英雄数据：/ow 英雄 [模式] [BattleTag]"""
        try:
            tag_arg, mode = self._split_mode_and_tag(first, second, third)
            target = self._resolve_target(event, tag_arg)
            profile = await self.service.profile(target, mode=mode)
            yield event.plain_result(
                render.heroes_card(
                    profile,
                    limit=max(10, self.service.hero_limit),
                    emoji=self._emoji(),
                ),
            )
        except OwError as exc:
            yield event.plain_result(exc.user_text())
        except Exception as exc:
            logger.error(f"查询英雄数据失败：{exc}", exc_info=True)
            yield event.plain_result("查询失败，请稍后再试或查看 AstrBot 日志。")

    @ow.command("资料", alias={"卡片"})
    async def ow_card(
        self,
        event: AstrMessageEvent,
        first: str = "",
        second: str = "",
        third: str = "",
    ):
        """基础资料：/ow 资料 [模式] [BattleTag]"""
        try:
            tag_arg, mode = self._split_mode_and_tag(first, second, third)
            target = self._resolve_target(event, tag_arg)
            profile = await self.service.profile(target, mode=mode, limit=1)
            profile = replace(profile, matches=[], heroes=[])
            yield event.plain_result(self._text_of(profile))
        except OwError as exc:
            yield event.plain_result(exc.user_text())
        except Exception as exc:
            logger.error(f"查询资料失败：{exc}", exc_info=True)
            yield event.plain_result("查询失败，请稍后再试或查看 AstrBot 日志。")

    @ow.command("图", alias={"战绩图", "卡片图"})
    async def ow_image(
        self,
        event: AstrMessageEvent,
        first: str = "",
        second: str = "",
        third: str = "",
    ):
        """图片战绩：/ow 图 [模式] [BattleTag]"""
        try:
            tag_arg, mode = self._split_mode_and_tag(first, second, third)
            target = self._resolve_target(event, tag_arg)
            yield await self._respond_profile(event, target, mode=mode, force_image=True)
        except OwError as exc:
            yield event.plain_result(exc.user_text())
        except Exception as exc:
            logger.error(f"生成图片失败：{exc}", exc_info=True)
            yield event.plain_result("生成图片失败，请稍后再试或查看 AstrBot 日志。")

    @ow.command("竞技")
    async def ow_competitive(self, event: AstrMessageEvent, first: str = "", second: str = ""):
        """竞技模式战绩：/ow 竞技 [BattleTag]"""
        async for result in self._mode_query(event, (first, second), "competitive"):
            yield result

    @ow.command("快速")
    async def ow_quick(self, event: AstrMessageEvent, first: str = "", second: str = ""):
        """快速模式战绩：/ow 快速 [BattleTag]"""
        async for result in self._mode_query(event, (first, second), "quick"):
            yield result

    @ow.command("生涯", alias={"休闲", "非竞技"})
    async def ow_career(self, event: AstrMessageEvent, first: str = "", second: str = ""):
        """生涯（非竞技）数据：/ow 生涯 [BattleTag]

        国服数据只有竞技与快速两个数据集，生涯/休闲/开放这些说法都对应快速数据集，
        其中也包含开放队列的职责数据。
        """
        async for result in self._mode_query(event, (first, second), "quick"):
            yield result

    async def _mode_query(self, event: AstrMessageEvent, args: tuple[str, ...], mode: str):
        try:
            # 指令本身已经定了模式，参数里再写的模式词忽略即可
            tag, _mode = self._split_mode_and_tag(*args)
            target = self._resolve_target(event, tag)
            yield await self._respond_profile(event, target, mode=mode)
        except OwError as exc:
            yield event.plain_result(exc.user_text())
        except Exception as exc:
            logger.error(f"查询 {mode} 战绩失败：{exc}", exc_info=True)
            yield event.plain_result("查询失败，请稍后再试或查看 AstrBot 日志。")

    @ow.command("状态")
    async def ow_status(self, event: AstrMessageEvent):
        """数据源与缓存状态"""
        try:
            status = await self.service.status_text()
            yield event.plain_result(f"插件版本：v{__version__}\n{status}")
        except Exception as exc:
            logger.error(f"查询状态失败：{exc}", exc_info=True)
            yield event.plain_result("读取状态失败，请查看 AstrBot 日志。")

    @ow.command("清除缓存")
    async def ow_clear_cache(self, event: AstrMessageEvent):
        """清空查询缓存（管理员）"""
        if not event.is_admin():
            yield event.plain_result("只有管理员可以清空缓存。")
            return
        try:
            await self.service.clear_cache()
        except Exception as exc:
            logger.error(f"清空缓存失败：{exc}", exc_info=True)
            yield event.plain_result("清空缓存失败，请查看 AstrBot 日志。")
            return
        yield event.plain_result("已清空查询缓存。")

    @ow.command("帮助")
    async def ow_help(self, event: AstrMessageEvent):
        """帮助"""
        yield event.plain_result(
            "守望先锋 2 国服战绩查询\n"
            f"{'─' * 16}\n"
            "/ow 绑定 BattleTag   绑定（如 /ow 绑定 光头大猩猩#5104）\n"
            "/ow 绑定群 BattleTag 设置本群默认账号（管理员）\n"
            "/ow 解绑群           解除本群默认账号（管理员）\n"
            "/ow 战绩 [BattleTag] 战绩总览\n"
            "/ow 对局 [页码]      最近对局\n"
            "/ow 明细 [序号]      单局明细（不带序号=最近一场）\n"
            "/ow 英雄             英雄数据\n"
            "/ow 资料             基础资料\n"
            "/ow 图               图片战绩\n"
            "/ow 竞技             竞技（排位）\n"
            "/ow 生涯 | /ow 快速   生涯 / 非竞技\n"
            "/ow 解绑             解除绑定\n"
            "/ow 状态             数据源状态\n"
            "/ow 清除缓存         清空查询缓存（管理员）\n"
            f"{'─' * 16}\n"
            "注意：指令组和子指令之间要有空格（/ow 战绩，不是 /ow战绩）。\n"
            "任意查询都可以 @群友，或直接跟一个 BattleTag；\n"
            "战绩/对局/明细/英雄/图 都能再加「竞技」或「生涯」切换模式，例如 /ow 英雄 生涯。",
        )

    # ---------- 生命周期 ----------

    async def terminate(self) -> None:
        """插件被禁用/重载时释放连接。"""
        await self.service.close()
        await self.image_inliner.close()

"""真机自检：对着一个真实可用的 Overstats 兼容服务跑完整指令链路。

用法（插件端不需要任何大神 token）：

    OW2CN_LIVE_BASE_URL=http://127.0.0.1:18080 \
    OW2CN_LIVE_TAG='光头大猩猩#5104' \
    python3 tests/live_check.py

OW2CN_LIVE_BASE_URL 必填；OW2CN_LIVE_TAG 不填时用上面的示例账号。它会依次执行
/ow 绑定 → /ow 战绩 → /ow 英雄 → /ow 生涯 → /ow 明细 → /ow 图 → /ow 状态，
并打印插件真实渲染出来的文本卡片，用来验证「数据源 → 解析 → 渲染」整条链路。
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _bootstrap
from _bootstrap import base_config, load_plugin, run_command, texts

BASE_URL = os.environ.get("OW2CN_LIVE_BASE_URL", "").strip()
TAG = os.environ.get("OW2CN_LIVE_TAG", "光头大猩猩#5104")


async def main() -> None:
    if not BASE_URL:
        raise SystemExit(
            "请先设置 OW2CN_LIVE_BASE_URL（Overstats 服务地址），例如 http://127.0.0.1:18080"
        )
    config = base_config(data_source="overstats", overstats_base_url=BASE_URL)
    config["overstats"]["timeout"] = 40
    plugin = load_plugin(config, pathlib.Path(tempfile.mkdtemp()))
    try:
        print(f"数据源: {BASE_URL} | 测试账号: {TAG}")
        print("\n== /ow 绑定", TAG)
        print(texts(await run_command(plugin, "ow 绑定", TAG)))

        print("\n== /ow 战绩")
        print(texts(await run_command(plugin, "ow 战绩")))

        print("\n== /ow 英雄")
        print(texts(await run_command(plugin, "ow 英雄")))

        print("\n== /ow 生涯（非竞技）")
        print(texts(await run_command(plugin, "ow 生涯")))

        print("\n== /ow 明细（不带序号 = 最近一场）")
        print(texts(await run_command(plugin, "ow 明细")))

        print("\n== /ow 明细 生涯（最近一场非竞技）")
        print(texts(await run_command(plugin, "ow 明细", "生涯")))

        print("\n== /ow 图")
        for result in await run_command(plugin, "ow 图"):
            images = result.images
            size = len(images[0].file) if images else 0
            print(f"返回图片: {len(images)} 张（字段长度 {size}）")

        print("\n== /ow 状态")
        print(texts(await run_command(plugin, "ow 状态")))
    finally:
        await plugin.terminate()


if __name__ == "__main__":
    asyncio.run(main())

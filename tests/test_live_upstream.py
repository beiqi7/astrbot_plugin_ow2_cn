"""真机验证：直接打网易大神上游，确认请求形态与错误路径。

这个用例需要外网，默认跳过；手动执行：

    OW2CN_LIVE_TEST=1 python3 -m unittest tests.test_live_upstream -v

它用假 token 请求真实上游，预期得到 `{"code":401,"errMsg":"Token Invalid"}`，
用来证明：
- 接口地址、路径、请求体字段、GL-* 请求头都是真实存在的形态（不是凭空写的）
- token 失效时插件会给出可读提示，而不是抛栈

配好真实 token 后，把 OW2CN_LIVE_TOKEN / OW2CN_LIVE_ROLE_ID 设上，
还可以真机查一个 BattleTag（例如 光头大猩猩#5104）。
"""

from __future__ import annotations

import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _bootstrap
from _bootstrap import base_config, load_plugin, run_command, texts

LIVE = os.environ.get("OW2CN_LIVE_TEST") == "1"
LIVE_TAG = os.environ.get("OW2CN_LIVE_TAG", "光头大猩猩#5104")


@unittest.skipUnless(LIVE, "需要外网：设置 OW2CN_LIVE_TEST=1 才运行")
class LiveUpstreamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loop = _bootstrap.asyncio.new_event_loop()
        cls.tmp = pathlib.Path(__file__).resolve().parent / ".live_data"
        cls.token = os.environ.get("OW2CN_LIVE_TOKEN", "definitely-invalid-token")
        cls.role_id = os.environ.get("OW2CN_LIVE_ROLE_ID", "1")
        cls.plugin = load_plugin(
            base_config(token=cls.token, role_id=cls.role_id),
            cls.tmp,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.loop.run_until_complete(cls.plugin.terminate())
        cls.loop.close()

    def test_upstream_reachable_and_error_is_readable(self) -> None:
        text = texts(self.loop.run_until_complete(run_command(self.plugin, "ow 战绩", LIVE_TAG)))
        if self.token == "definitely-invalid-token":
            # 假 token：上游返回 code=401 Token Invalid，插件应转成中文提示
            self.assertIn("token", text.lower())
            self.assertIn("失效", text)
        else:
            # 真 token：应当能拿到资料卡（等级/段位随便命中一个）
            self.assertTrue(
                ("等级" in text) or ("段位" in text),
                f"未取到战绩：{text}",
            )


if __name__ == "__main__":
    unittest.main()

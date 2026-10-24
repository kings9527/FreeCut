"""动画告警测试：没实现的动画名要被列出来。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestUnsupportedAnimations(unittest.TestCase):
    def test_lists_only_unimplemented(self):
        from freecut.jianying import unsupported_animations

        data = {"materials": {"material_animations": [
            {"name": "淡入", "type": "in"},
            {"name": "Kira游动", "type": "in"},
            {"name": "冬季雪花", "type": "in"},
        ]}}
        got = unsupported_animations(data)
        self.assertIn("Kira游动", got)
        self.assertIn("冬季雪花", got)
        self.assertNotIn("淡入", got)

    def test_missing_or_odd_materials_is_safe(self):
        from freecut.jianying import unsupported_animations

        self.assertEqual(unsupported_animations({}), [])
        self.assertEqual(unsupported_animations({"materials": {}}), [])
        self.assertEqual(
            unsupported_animations({"materials": {"material_animations": [None, 3, {}]}}), [])

    def test_real_catalog_majority_is_reported(self):
        """真实目录：云端资源包占多数，必须有相当比例被报告出来。"""
        import json

        from freecut.jianying import unsupported_animations

        path = os.path.join(ROOT, "freecut", "data", "animations.json")
        if not os.path.isfile(path):
            self.skipTest("没有动画目录")
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh)["items"]
        tmap = {"video_in": "in", "video_out": "out", "text_in": "in",
                "text_out": "out", "text_loop": "loop"}
        data = {"materials": {"material_animations": [
            {"name": i["name"], "type": tmap[i["category"]]} for i in items]}}
        missing = unsupported_animations(data)
        total = len(items)
        self.assertGreater(len(missing), 0)
        self.assertLess(len(missing), total)
        # 至少一半的名字能被解析（我们实现的常规动画 + match_rules 的模糊匹配）
        self.assertGreaterEqual(total - len(missing), total // 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

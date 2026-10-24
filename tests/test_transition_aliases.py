"""转场别名表测试：别名都要指向真引擎，并且真的减少"未实现"数量。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTransitionAliases(unittest.TestCase):
    def test_aliases_point_at_real_engines(self):
        from freecut.render import TRANSITION_ALIASES, TRANSITION_KINDS

        engines = set(TRANSITION_KINDS.values())
        for name, kind in TRANSITION_ALIASES.items():
            self.assertIn(kind, engines, f"别名 {name} 指向不存在的引擎 {kind}")

    def test_aliases_do_not_shadow_existing_names(self):
        from freecut.render import TRANSITION_ALIASES, TRANSITION_KINDS

        for name in TRANSITION_ALIASES:
            self.assertNotIn(name, TRANSITION_KINDS,
                             f"{name} 已经是正式名字，不该再出现在别名表")

    def test_resolve_handles_aliases_and_variants(self):
        from freecut.render import resolve_transition

        self.assertEqual(resolve_transition("拉远"), "zoom_out")
        self.assertEqual(resolve_transition("爆闪"), "fade_white")
        self.assertEqual(resolve_transition("闪白 II"), "fade_white")
        self.assertEqual(resolve_transition("叠化"), "cross_dissolve")
        self.assertIsNone(resolve_transition("3D空间"))
        self.assertIsNone(resolve_transition(""))

    def test_alias_covers_at_least_thirteen_names(self):
        from freecut.render import TRANSITION_ALIASES

        self.assertGreaterEqual(len(TRANSITION_ALIASES), 13)


if __name__ == "__main__":
    unittest.main(verbosity=2)

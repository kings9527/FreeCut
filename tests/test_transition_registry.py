"""一致性守护：每个转场引擎都要有中文标签、并且出现在某个分组里。

否则界面上会出现"引擎存在但列表里看不到"或"没有名字"的洞。
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTransitionRegistryConsistency(unittest.TestCase):
    def setUp(self):
        from freecut.qtui.labels import ENGINE_LABELS
        from freecut.qtui.library import TRANSITION_GROUPS
        from freecut.render import TRANSITION_KINDS

        self.kinds = set(TRANSITION_KINDS.values())
        self.labels = set(ENGINE_LABELS)
        self.groups = {e for engines in TRANSITION_GROUPS.values() for e in engines}

    def test_every_engine_has_a_label(self):
        missing = sorted(self.kinds - self.labels)
        self.assertEqual(missing, [], f"这些引擎没有中文标签：{missing}")

    def test_every_engine_is_in_a_group(self):
        missing = sorted(self.kinds - self.groups)
        self.assertEqual(missing, [], f"这些引擎不在任何分组里：{missing}")

    def test_no_group_points_at_unknown_engine(self):
        unknown = sorted(self.groups - self.kinds)
        self.assertEqual(unknown, [], f"分组引用了不存在的引擎：{unknown}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

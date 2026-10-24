"""导入侧告警：没有实现的转场名要被列出来，而不是静默退化成叠化。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestUnsupportedTransitions(unittest.TestCase):
    def test_lists_only_names_without_an_implementation(self):
        from freecut.jianying import unsupported_transitions

        data = {"materials": {"transitions": [
            {"name": "叠化"}, {"name": "闪黑"}, {"name": "3D空间"}, {"name": "云朵"},
        ]}}
        self.assertEqual(unsupported_transitions(data), ["3D空间", "云朵"])

    def test_all_implemented_returns_empty(self):
        from freecut.jianying import unsupported_transitions

        data = {"materials": {"transitions": [{"name": "叠化"}, {"name": "闪白"}]}}
        self.assertEqual(unsupported_transitions(data), [])

    def test_missing_materials_is_safe(self):
        from freecut.jianying import unsupported_transitions

        self.assertEqual(unsupported_transitions({}), [])
        self.assertEqual(unsupported_transitions({"materials": {}}), [])

    def test_real_catalog_maps_most_names_to_fallback(self):
        """跑一遍真实目录：确认有相当比例的名字没有实现（这正是要告警的原因）。"""
        import json
        from freecut.jianying import unsupported_transitions
        from freecut.render import TRANSITION_KINDS

        path = os.path.join(ROOT, "freecut", "data", "transitions.json")
        if not os.path.isfile(path):
            self.skipTest("没有转场目录")
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh)["items"]
        data = {"materials": {"transitions": [{"name": i["name"]} for i in items]}}
        missing = unsupported_transitions(data)
        total = len(items)
        self.assertGreater(len(missing), 0)
        self.assertLess(len(missing), total)
        implemented = total - len(missing)
        self.assertGreaterEqual(implemented, 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""花字（文字样式）一致性：20 套都要能套用，分组要覆盖全部名字。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTextStyles(unittest.TestCase):
    def test_every_style_applies(self):
        from freecut import builder, textstyles

        for name in textstyles.names():
            with self.subTest(style=name):
                project = builder.new_project(320, 180, 30, name="花字")
                seg = builder.add_text(project, "样式", start=0, duration=1_000_000)
                mat = project.material(seg.material_id)
                self.assertTrue(textstyles.apply(mat, name), f"{name} 套不上")
                self.assertEqual(textstyles.style_of(mat), name)

    def test_groups_cover_every_style(self):
        from freecut import textstyles

        names = set(textstyles.names())
        covered = set()
        for group, items in textstyles.groups().items():
            self.assertIsInstance(group, str)
            covered.update(items)
        self.assertEqual(covered, names, "有样式没被任何分组覆盖")

    def test_unknown_style_is_rejected(self):
        from freecut import builder, textstyles

        project = builder.new_project(320, 180, 30, name="花字")
        seg = builder.add_text(project, "样式", start=0, duration=1_000_000)
        mat = project.material(seg.material_id)
        self.assertIsNone(textstyles.find("不存在的样式"))
        self.assertFalse(textstyles.apply(mat, "不存在的样式"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

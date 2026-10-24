"""文字页对齐与竖排：能切换、能写回、能回填。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTextAlignVertical(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _setup(self, alignment=1, vertical=False):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        project = builder.new_project(320, 180, 30, name="对齐")
        seg = builder.add_text(project, "你好", start=0, duration=1_000_000)
        mat = project.material(seg.material_id)
        mat["alignment"] = alignment
        mat["vertical"] = vertical
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(seg)
        self.app.processEvents()
        return project, seg, inspector

    def test_values_are_loaded_from_material(self):
        _p, _s, inspector = self._setup(alignment=2, vertical=True)
        self.assertEqual(inspector.align_box.currentData(), 2)
        self.assertTrue(inspector.vertical_box.isChecked())

    def test_changing_alignment_writes_back(self):
        project, seg, inspector = self._setup(alignment=1)
        inspector.align_box.setCurrentIndex(0)
        self.assertEqual(project.material(seg.material_id).get("alignment"), 0)

    def test_vertical_toggle_writes_back(self):
        project, seg, inspector = self._setup()
        inspector.vertical_box.setChecked(True)
        self.assertTrue(project.material(seg.material_id).get("vertical"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

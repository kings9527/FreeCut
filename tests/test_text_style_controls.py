"""文字页新增样式控件：粗体/斜体/下划线/行距/字距 都能写回材质。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTextStyleControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _setup(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        project = builder.new_project(320, 180, 30, name="文字样式")
        seg = builder.add_text(project, "你好", start=0, duration=1_000_000)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(seg)
        self.app.processEvents()
        return project, seg, inspector

    def test_bold_italic_write_back(self):
        project, seg, inspector = self._setup()
        inspector.row_bold.set_value(0.12, emit=True)
        inspector.row_italic.set_value(12.0, emit=True)
        mat = project.material(seg.material_id)
        self.assertAlmostEqual(float(mat.get("bold_width")), 0.12, places=2)
        self.assertAlmostEqual(float(mat.get("italic_degree")), 12.0, places=1)

    def test_spacing_write_back(self):
        project, seg, inspector = self._setup()
        inspector.row_line_spacing.set_value(1.4, emit=True)
        inspector.row_letter_spacing.set_value(3.0, emit=True)
        mat = project.material(seg.material_id)
        self.assertAlmostEqual(float(mat.get("line_spacing")), 1.4, places=2)
        self.assertAlmostEqual(float(mat.get("letter_spacing")), 3.0, places=1)

    def test_underline_toggle(self):
        project, seg, inspector = self._setup()
        inspector.chk_underline.setChecked(True)
        self.assertTrue(project.material(seg.material_id).get("underline"))
        inspector.chk_underline.setChecked(False)
        self.assertFalse(project.material(seg.material_id).get("underline"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

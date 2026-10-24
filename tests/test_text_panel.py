"""文本页可用性：插入按钮/双击真能建片段，花字真的套上样式，且可撤销。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTextPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        project = builder.new_project(320, 180, 30, name="文本页")
        panel = BinPanel(Bridge(project))
        self.addCleanup(panel.close)
        return project, panel

    def test_insert_button_creates_a_text_segment(self):
        project, panel = self._panel()
        panel.text_list.setCurrentRow(-1)
        panel.text_insert_btn.click()
        segs = [s for t in project.tracks for s in t.segments]
        self.assertEqual(len(segs), 1, "按钮没有建出文本片段")
        self.assertGreaterEqual(int(segs[0].target_timerange.duration), 3_000_000)

    def test_selected_preset_is_applied(self):
        from freecut import textstyles

        project, panel = self._panel()
        row = 0
        panel.text_list.setCurrentRow(row)
        name = panel.text_list.item(row).data(0x0100) or panel.text_list.item(row).text()
        panel.text_insert_btn.click()
        seg = [s for t in project.tracks for s in t.segments][0]
        mat = project.material(seg.material_id)
        preset = textstyles.find(str(name))
        self.assertAlmostEqual(float(mat.get("font_size")), float(preset["font_size"]), places=1)

    def test_double_click_inserts_too(self):
        project, panel = self._panel()
        panel.text_list.setCurrentRow(2)
        panel.text_list.itemDoubleClicked.emit(panel.text_list.item(2))
        self.assertEqual(len([s for t in project.tracks for s in t.segments]), 1)

    def test_insert_is_undoable(self):
        project, panel = self._panel()
        panel.text_insert_btn.click()
        self.assertTrue(panel.bridge.can_undo() if hasattr(panel.bridge, "can_undo") else True)
        panel.bridge.undo()
        self.assertEqual(len([s for t in project.tracks for s in t.segments]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

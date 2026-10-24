"""导出对话框：帧率与分辨率应当是两个独立选项（剪映如此）。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestExportFpsSelector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def dialog(self):
        from freecut.qtui.app import MainWindow
        from freecut.qtui.dialogs.export_dialog import ExportDialog

        sample = os.path.join(ROOT, "out", "showcase.json")
        win = MainWindow(sample if os.path.isfile(sample) else None)
        dlg = ExportDialog(win.bridge, win)
        self.addCleanup(win.close)
        self.addCleanup(dlg.close)
        return dlg

    def test_fps_choices_exist(self):
        dlg = self.dialog()
        values = [dlg.fps_box.itemData(i) for i in range(dlg.fps_box.count())]
        self.assertEqual(values, [24, 25, 30, 50, 60])

    def test_selected_fps_overrides_preset(self):
        dlg = self.dialog()
        from freecut.export import PRESETS

        dlg.fps_box.setCurrentIndex(dlg.fps_box.findData(50))
        preset = dlg.current_preset()
        self.assertAlmostEqual(preset.fps, 50.0)
        # 分辨率只由分辨率下拉决定，不该被帧率带跑
        chosen = PRESETS[dlg._preset_keys[dlg.preset_box.currentIndex()]]
        self.assertEqual((preset.width, preset.height), (chosen.width, chosen.height))

    def test_switching_resolution_preset_syncs_fps(self):
        dlg = self.dialog()
        index = dlg._preset_keys.index("1080p60")
        dlg.preset_box.setCurrentIndex(index)
        self.assertAlmostEqual(dlg.selected_fps(), 60.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

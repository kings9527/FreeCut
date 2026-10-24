"""滤镜列表缩略图：每支 LUT 都有图，且不同 LUT 的图不同。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestLutThumbs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        project = builder.new_project(320, 180, 30, name="滤镜缩略图")
        panel = BinPanel(Bridge(project))
        self.addCleanup(panel.close)
        return panel

    def test_lut_list_has_icons(self):
        panel = self._panel()
        if panel.lut_list.count() == 0:
            self.skipTest("本机没有可用 LUT")
        for row in range(min(5, panel.lut_list.count())):
            item = panel.lut_list.item(row)
            self.assertFalse(item.icon().isNull(), f"{item.text()} 没有缩略图")

    def test_lut_icons_are_not_all_identical(self):
        from freecut import luts

        entries = list(luts.library(refresh=True))[:5]
        if len(entries) < 2:
            self.skipTest("LUT 不足两支")
        panel = self._panel()
        seen = set()
        for entry in entries:
            icon = panel._lut_icon(entry.path)
            if icon is None:
                continue
            seen.add(icon.pixmap(80, 45).toImage().constBits().tobytes())
        self.assertGreaterEqual(len(seen), 2, "前 5 支 LUT 的缩略图全都一样")

if __name__ == "__main__":
    unittest.main(verbosity=2)

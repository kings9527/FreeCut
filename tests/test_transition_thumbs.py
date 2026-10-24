"""转场列表缩略图：每个引擎都有图标，且不同引擎图标不同。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTransitionThumbs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        project = builder.new_project(320, 180, 30, name="缩略图")
        panel = BinPanel(Bridge(project))
        self.addCleanup(panel.close)
        return panel

    def test_every_transition_has_an_icon(self):
        panel = self._panel()
        self.assertGreater(panel.transition_list.count(), 10)
        for row in range(panel.transition_list.count()):
            item = panel.transition_list.item(row)
            self.assertFalse(item.icon().isNull(), f"{item.text()} 没有缩略图")

    def test_different_engines_produce_different_icons(self):
        panel = self._panel()
        left = panel._transition_icon("wipe_left")
        right = panel._transition_icon("wipe_right")
        self.assertIsNotNone(left)
        self.assertIsNotNone(right)
        a = left.pixmap(80, 45).toImage()
        b = right.pixmap(80, 45).toImage()
        self.assertNotEqual(a.constBits().tobytes(), b.constBits().tobytes(),
                            "左右擦除的缩略图一样，掩码可能没生效")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""特效列表缩略图：每个特效都有图，且整体不是同一张。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestEffectThumbs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        panel = BinPanel(Bridge(builder.new_project(320, 180, 30, name="特效缩略图")))
        self.addCleanup(panel.close)
        return panel

    def test_every_effect_has_an_icon(self):
        panel = self._panel()
        self.assertGreater(panel.effect_list.count(), 5)
        for row in range(panel.effect_list.count()):
            item = panel.effect_list.item(row)
            self.assertFalse(item.icon().isNull(), f"{item.text()} 没有缩略图")

    def test_icons_differ_between_effects(self):
        from freecut import effects

        panel = self._panel()
        seen = set()
        for kind in list(effects.available())[:6]:
            icon = panel._effect_icon(kind)
            if icon is None:
                continue
            seen.add(icon.pixmap(80, 45).toImage().constBits().tobytes())
        self.assertGreaterEqual(len(seen), 2, "前几个特效的缩略图全都一样")


if __name__ == "__main__":
    unittest.main(verbosity=2)

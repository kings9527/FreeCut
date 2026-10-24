"""缩放到全部：ppm 按总时长与宽度算，滚动归零。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestFitToWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        sample = os.path.join(ROOT, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        self.addCleanup(lambda: (setattr(win.bridge, "dirty", False), win.close()))
        return win

    def test_ppm_scales_with_width(self):
        win = self._window()
        win.timeline.fit_to_window(1000)
        narrow = win.timeline.ppm
        win.timeline.fit_to_window(2000)
        wide = win.timeline.ppm
        self.assertGreater(wide, narrow, "更宽的窗口应当给出更大的 ppm")
        self.assertGreaterEqual(narrow, 8.0)
        self.assertLessEqual(wide, 600.0)

    def test_scroll_is_reset(self):
        win = self._window()
        win.timeline.scroll_us = 5_000_000
        win.timeline.fit_to_window(1400)
        self.assertEqual(win.timeline.scroll_us, 0)

    def test_shortcut_and_menu_exist(self):
        win = self._window()
        menu_texts = []
        shortcuts = set()
        for menu in win.menuBar().findChildren(type(win.menuBar().addMenu("x"))):
            for action in menu.actions():
                menu_texts.append(action.text())
                if not action.shortcut().isEmpty():
                    shortcuts.add(action.shortcut().toString())
        self.assertTrue(any("缩放到全部" in t for t in menu_texts), menu_texts[:12])
        self.assertIn("Ctrl+0", shortcuts,
                      f"菜单快捷键里没有 Ctrl+0：{sorted(shortcuts)}")

if __name__ == "__main__":
    unittest.main(verbosity=2)

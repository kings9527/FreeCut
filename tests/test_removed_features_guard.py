"""守护"删除做不到的功能"这条线：四个入口都不能再把云项目加回来。

对应目标：删除我们做不到的功能，只保留我们还原的。
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CLOUD_RAIL = ("贴纸", "模板", "数字人", "智能包装")


class TestRemovedFeaturesStayRemoved(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        win.resize(1200, 800)
        self.app.processEvents()
        return win

    def test_rail_has_only_supported_categories(self):
        win = self.window()
        try:
            names = list(win.rail.buttons)
            self.assertEqual(len(names), 8, f"左栏应当 8 个，实际 {names}")
            for gone in CLOUD_RAIL:
                self.assertNotIn(gone, names, f"{gone} 依赖云端，不该再出现在左栏")
        finally:
            win.close()

    def test_toolbar_dropped_cloud_buttons(self):
        win = self.window()
        try:
            order = list(win.timeline_toolbar.order)
            self.assertEqual(len(order), 22, f"工具栏应当 22 个，实际 {len(order)}")
            for gone in ("ai", "record"):
                self.assertNotIn(gone, order, f"{gone} 不该再有按钮")
        finally:
            win.close()

    def test_inspector_dropped_ai_page(self):
        win = self.window()
        try:
            names = [win.inspector.tabs.tabText(i)
                     for i in range(win.inspector.tabs.count())]
            self.assertEqual(len(names), 8)
            self.assertNotIn("AI编辑", names)
        finally:
            win.close()

    def test_context_menu_has_no_cloud_actions(self):
        from freecut.qtui.menus import build_context_menu

        win = self.window()
        try:
            menu = build_context_menu(win, None)     # 空白处菜单也要干净
            keys = set(menu._actions_by_key)
            for gone in ("smart_matting", "ai_voice"):
                self.assertNotIn(gone, keys, f"{gone} 不该出现在菜单里")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

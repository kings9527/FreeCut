"""「选择工具」不该再被标成"做不到"：框选与 Ctrl 点选都已经实现。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestSelectTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_select_tool_is_no_longer_listed_as_unavailable(self):
        from freecut.qtui.menus import UNAVAILABLE

        self.assertNotIn("select_tool", UNAVAILABLE,
                         "框选已经实现，不该再标成做不到")

    def test_running_it_switches_timeline_back_to_select(self):
        from freecut.qtui.app import MainWindow
        from freecut.qtui.menus import _Handler

        win = MainWindow(None)
        try:
            win.timeline.tool = "razor"
            handler = _Handler(win, None)
            handler._select_tool()
            self.assertEqual(win.timeline.tool, "select")
        finally:
            win.close()

    def test_ctrl_click_adds_to_selection(self):
        """Ctrl 点选是框选之外的另一半，确认 backend 支持多选。"""
        from freecut.qtui.bridge import Bridge
        from freecut import builder

        project = builder.new_project(320, 180, 30, name="多选")
        image = os.path.join(ROOT, "out", "chroma_src.png")
        if not os.path.isfile(image):
            self.skipTest("没有测试图片")
        a = builder.add_image(project, image, start=0, duration=1_000_000)
        b = builder.add_image(project, image, start=1_000_000, duration=1_000_000)
        bridge = Bridge(project)
        bridge.select(a)
        bridge.select(b, additive=True)
        self.assertEqual(len(bridge.selection), 2)
        bridge.select(b, additive=True)          # 再点一次取消
        self.assertEqual(len(bridge.selection), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

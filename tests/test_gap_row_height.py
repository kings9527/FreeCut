"""第 246 轮：轨道行高（缺口 7 的第二半；编组仍未做，HANDOFF 里写明）。

菜单「时间线 → 轨道高度」切换紧凑/标准/高：行距、片段矩形高度、点击命中都要跟着变。
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


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        media = os.path.join(ROOT, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("没有测试素材")
        win = MainWindow(None)
        win.resize(1280, 900)
        win.show()
        self.app.processEvents()
        project = builder.new_project(640, 360, 30, name="行高")
        builder.add_video(project, media, start=0, duration=2_000_000)
        builder.add_video(project, media, start=0, duration=2_000_000, track_index=1)
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        return win


class TestRowHeight(_Base):
    def test_menu_switches_row_height(self):
        from PySide6.QtGui import QAction

        win = self.window()
        try:
            actions = [a for a in win.findChildren(QAction)
                       if a.text().startswith("高（56px")]
            self.assertTrue(actions, "菜单里没有轨道高度")
            actions[0].trigger()
            self.app.processEvents()
            self.assertEqual(win.timeline.row_h, 56)
        finally:
            win.close()

    def test_row_spacing_follows_the_height(self):
        win = self.window()
        try:
            timeline = win.timeline
            timeline.set_row_h(56)
            gap = timeline._track_y(1) - timeline._track_y(0)
            self.assertEqual(gap, 56, f"行距没变：{gap}")
            timeline.set_row_h(28)
            gap = timeline._track_y(1) - timeline._track_y(0)
            self.assertEqual(gap, 28)
        finally:
            win.close()

    def test_clip_height_follows_the_height(self):
        win = self.window()
        try:
            timeline = win.timeline
            seg = timeline.rows()[0].segments[0]
            timeline.set_row_h(56)
            tall = timeline._seg_rect(seg, 0).height()
            timeline.set_row_h(28)
            short = timeline._seg_rect(seg, 0).height()
            self.assertGreater(tall, short, "片段矩形没有跟着行高变")
        finally:
            win.close()

    def test_click_still_hits_the_right_clip_after_resize(self):
        win = self.window()
        try:
            from PySide6.QtCore import QPoint, Qt
            from PySide6.QtGui import QMouseEvent

            timeline = win.timeline
            timeline.set_row_h(56)
            seg = timeline.rows()[1].segments[0]
            x = timeline._us_to_x(1_000_000)
            y = timeline._track_y(1) + 20
            pos = timeline.mapFromScene(QPoint(int(x), int(y)))
            timeline.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, pos,
                                                 timeline.mapToGlobal(pos), Qt.LeftButton,
                                                 Qt.LeftButton, Qt.NoModifier))
            self.assertIn(seg, win.bridge.selection, "调高行之后点到了别的片段")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

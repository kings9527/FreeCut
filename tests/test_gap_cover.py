"""第 246 轮：封面（缺口 6）。

剪映的时间线左下角有"封面"，设一次以后草稿/轨头都用那一帧。这里验：
点菜单设封面 → 工程记住 → 存盘再打开还在 → 撤销能回去。
"""

from __future__ import annotations

import os
import sys
import tempfile
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
        win.resize(1280, 820)
        win.show()
        self.app.processEvents()
        project = builder.new_project(640, 360, 30, name="封面")
        builder.add_video(project, media, start=0, duration=4_000_000)
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        return win


class TestCover(_Base):
    def test_set_cover_from_the_menu(self):
        from PySide6.QtGui import QAction

        win = self.window()
        try:
            win.bridge.set_playhead(1_500_000)
            action = next(a for a in win.findChildren(QAction)
                          if a.text() == "把这一帧设为封面")
            action.trigger()
            self.app.processEvents()
            self.assertEqual(win.bridge.project.cover_time, 1_500_000)
        finally:
            win.close()

    def test_cover_survives_save_and_load(self):
        win = self.window()
        try:
            win.bridge.set_cover(2_000_000)
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "cover.json")
                self.assertTrue(win.bridge.save(path))
                from freecut.model import Project

                again = Project.load(path)
                self.assertEqual(int(again.cover_time), 2_000_000)
        finally:
            win.close()

    def test_cover_is_undoable(self):
        win = self.window()
        try:
            win.bridge.set_cover(2_000_000)
            self.assertTrue(win.bridge.undo())
            self.app.processEvents()
            self.assertEqual(int(win.bridge.project.cover_time), 0)
        finally:
            win.close()

    def test_header_cover_uses_the_cover_frame(self):
        """主轨轨头取的是封面那一帧（按取值的时间点断言，不依赖解码是否完成）。"""
        from PySide6.QtGui import QPixmap

        win = self.window()
        try:
            win.bridge.set_cover(2_000_000)
            timeline = win.timeline
            track = timeline.rows()[0]
            asked = []
            timeline.cache.pixmap = (lambda path, t, w, h:
                                     asked.append(int(t)) or QPixmap(2, 2))
            pixmap = timeline._header_cover(track)
            self.assertIsNotNone(pixmap, "设置封面后轨头取不到封面图")
            self.assertEqual(asked, [2_000_000],
                             f"轨头取的不是封面帧：{asked}")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

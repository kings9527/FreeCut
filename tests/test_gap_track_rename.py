"""第 246 轮：轨道重命名（缺口 7 的第一半；高度与编组还没做，见 HANDOFF）。

模拟用户：双击轨头 → 输入名字 → 名字显示在轨头、存盘后还在、清除后恢复默认。
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
        project = builder.new_project(640, 360, 30, name="轨道名")
        builder.add_video(project, media, start=0, duration=3_000_000)
        builder.add_audio(project, os.path.join(ROOT, "out", "asr_test.wav"), start=0)
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        return win


class TestTrackRename(_Base):
    def _double_click_header(self, win, row: int):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QMouseEvent

        timeline = win.timeline
        y = timeline._track_y(row) + 12
        view = timeline.mapFromScene(QPoint(30, int(y)))
        local = QPoint(30, int(timeline.mapFromScene(QPoint(30, int(y))).y()))
        event = QMouseEvent(QMouseEvent.MouseButtonDblClick, local,
                            timeline.mapToGlobal(local), Qt.LeftButton,
                            Qt.LeftButton, Qt.NoModifier)
        timeline.mouseDoubleClickEvent(event)
        return view

    def test_double_click_renames_and_the_name_persists(self):
        from PySide6.QtWidgets import QInputDialog

        win = self.window()
        original = QInputDialog.getText
        try:
            QInputDialog.getText = staticmethod(lambda *a, **k: ("主视频", True))
            self._double_click_header(win, 0)
            self.app.processEvents()
            track = win.timeline.rows()[0]
            self.assertEqual(track.name, "主视频")
            self.assertFalse(track.is_default_name)
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "named.json")
                self.assertTrue(win.bridge.save(path))
                from freecut.model import Project

                again = Project.load(path)
                names = [t.name for t in again.tracks if t.name]
                self.assertIn("主视频", names, "轨道名没有存进工程")
        finally:
            QInputDialog.getText = original
            win.close()

    def test_empty_name_restores_the_default_label(self):
        from PySide6.QtWidgets import QInputDialog

        win = self.window()
        original = QInputDialog.getText
        try:
            QInputDialog.getText = staticmethod(lambda *a, **k: ("", True))
            self._double_click_header(win, 0)
            self.app.processEvents()
            track = win.timeline.rows()[0]
            self.assertEqual(track.name, "")
            self.assertTrue(track.is_default_name, "清空名字后应当标记回默认名")
            win.timeline.viewport().repaint()      # 画一遍不该抛异常
        finally:
            QInputDialog.getText = original
            win.close()

    def test_cancel_keeps_the_old_name(self):
        from PySide6.QtWidgets import QInputDialog

        win = self.window()
        original = QInputDialog.getText
        try:
            win.timeline.rows()[0].name = "音乐轨"
            QInputDialog.getText = staticmethod(lambda *a, **k: ("别的名字", False))
            self._double_click_header(win, 0)
            self.app.processEvents()
            self.assertEqual(win.timeline.rows()[0].name, "音乐轨", "取消却改了名字")
        finally:
            QInputDialog.getText = original
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

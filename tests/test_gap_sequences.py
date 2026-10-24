"""第 247 轮：多时间线（缺口 8）。

剪映预览区左上角的「播放器-时间线01 ▾」可以有多条时间线。这里验：
一条时禁用、新建后变空、切回去原来的片段还在、存盘带回所有时间线、删到只剩一条会被拒。
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

    def window(self, clips: int = 2):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        media = os.path.join(ROOT, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("没有测试素材")
        win = MainWindow(None)
        win.resize(1280, 820)
        win.show()
        self.app.processEvents()
        project = builder.new_project(640, 360, 30, name="多时间线")
        for index in range(clips):
            builder.add_video(project, media, start=index * 1_000_000, duration=1_000_000)
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        return win

    @staticmethod
    def clips(win):
        return [s for t in win.bridge.project.tracks for s in t.segments]


class TestSequences(_Base):
    def test_single_sequence_disables_the_combo(self):
        win = self.window()
        try:
            self.assertEqual(len(win.bridge.project.sequences), 1)
            self.assertFalse(win.viewer.panel_title.isEnabled(),
                             "只有一条时间线时下拉不该能点")
            self.assertIn("时间线", win.viewer.panel_title.currentText())
        finally:
            win.close()

    def test_new_sequence_is_empty_and_switch_back_restores_clips(self):
        win = self.window()
        try:
            before = len(self.clips(win))
            index = win.bridge.new_sequence()
            self.app.processEvents()
            self.assertEqual(index, 1)
            self.assertEqual(len(win.bridge.project.sequences), 2)
            self.assertEqual(len(self.clips(win)), 0, "新建的时间线不是空的")
            self.assertTrue(win.viewer.panel_title.isEnabled())

            win.bridge.switch_sequence(0)
            self.app.processEvents()
            self.assertEqual(len(self.clips(win)), before, "切回来片段丢了")
        finally:
            win.close()

    def test_combo_switches_sequences(self):
        win = self.window()
        try:
            win.bridge.new_sequence()
            self.app.processEvents()
            combo = win.viewer.panel_title
            self.assertEqual(combo.count(), 2)
            combo.setCurrentIndex(0)                    # 用户点下拉切回去
            self.app.processEvents()
            self.assertEqual(win.bridge.project.active_sequence, 0)
            self.assertEqual(len(self.clips(win)), 2)
        finally:
            win.close()

    def test_sequences_survive_save_and_load(self):
        win = self.window()
        try:
            win.bridge.new_sequence()
            from freecut import builder

            builder.add_video(win.bridge.project, os.path.join(ROOT, "out", "showcase.mp4"),
                              start=0, duration=1_000_000)
            win.bridge.switch_sequence(0)
            self.app.processEvents()
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "seq.json")
                self.assertTrue(win.bridge.save(path))
                from freecut.model import Project

                again = Project.load(path)
                self.assertEqual(len(again.sequences), 2, "第二条时间线没存进去")
                self.assertEqual(len(again.sequences[0].tracks), 1)
                self.assertEqual(len(again.sequences[1].tracks), 1)
                again.active_sequence = 1
                again.sequence()
                self.assertEqual(len(again.tracks), 1, "切到第二条后读不到它的轨道")
        finally:
            win.close()

    def test_cannot_delete_the_last_sequence(self):
        win = self.window()
        messages = []
        win.bridge.statusMessage.connect(messages.append)
        try:
            self.assertFalse(win.bridge.delete_sequence())
            self.assertEqual(len(win.bridge.project.sequences), 1)
            self.assertTrue(any("至少保留" in m for m in messages))
        finally:
            win.close()

    def test_delete_sequence_keeps_the_rest(self):
        win = self.window()
        try:
            win.bridge.new_sequence()
            self.app.processEvents()
            self.assertTrue(win.bridge.delete_sequence(1))
            self.app.processEvents()
            self.assertEqual(len(win.bridge.project.sequences), 1)
            self.assertEqual(win.bridge.project.active_sequence, 0)
            self.assertEqual(len(self.clips(win)), 2, "删掉第二条却影响了第一条")
        finally:
            win.close()

    def test_old_project_without_sequences_still_loads(self):
        """老工程 / 剪映草稿只有 tracks：要能包成一条时间线读进来。"""
        from freecut.model import Project

        data = {
            "canvas_config": {"width": 640, "height": 360, "ratio": "16:9"},
            "fps": 30,
            "name": "老工程",
            "tracks": [{"type": "video", "segments": [], "name": ""}],
        }
        project = Project.from_dict(data)
        self.assertEqual(len(project.sequences), 1)
        self.assertEqual(len(project.tracks), 1)
        self.assertEqual(project.active_sequence, 0)
        self.assertTrue(project.to_dict()["sequences"], "再存一次应当带上 sequences")


if __name__ == "__main__":
    unittest.main(verbosity=2)

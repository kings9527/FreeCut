"""第 13 轮：轨道增删（用户实测：加了很多轨道，删不掉）。

剪映里的规则（证据：docs/measurements/RUNTIME-CLOCK-TRACK-GPU.md §2）：
主轨唯一；`[DraftError] main track loss` 会被补一条空主轨，所以主轨不允许删。
我们：主轨拒绝删除并说明原因，其它轨道可删可清空，都能一次撤销。
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


class TestTrackDelete(unittest.TestCase):
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
        project = builder.new_project(640, 360, 30, name="轨道")
        builder.add_video(project, media, start=0, duration=1_000_000, track_index=0)
        builder.add_video(project, media, start=0, duration=1_000_000, track_index=1)
        builder.add_video(project, media, start=0, duration=1_000_000, track_index=2)
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        return win

    @staticmethod
    def _video_tracks(win):
        return [t for t in win.bridge.project.tracks if t.type == "video"]

    def test_delete_a_non_main_track_removes_it_and_its_clips(self):
        win = self.window()
        try:
            before = len(self._video_tracks(win))
            track = self._video_tracks(win)[2]
            index = list(win.bridge.project.tracks).index(track)
            self.assertTrue(win.bridge.delete_track(index))
            self.app.processEvents()
            self.assertEqual(len(self._video_tracks(win)), before - 1)
        finally:
            win.close()

    def test_delete_is_undoable_with_clips(self):
        win = self.window()
        try:
            track = self._video_tracks(win)[1]
            clips = len(track.segments)
            index = list(win.bridge.project.tracks).index(track)
            win.bridge.delete_track(index)
            self.app.processEvents()
            self.assertTrue(win.bridge.undo(), "删轨道应当可撤销")
            self.app.processEvents()
            tracks = self._video_tracks(win)
            self.assertEqual(len(tracks), 3, "撤销后轨道数没回来")
            self.assertEqual(sum(len(t.segments) for t in tracks),
                             3, "撤销后片段没回来")
            self.assertTrue(clips)
        finally:
            win.close()

    def test_main_track_cannot_be_deleted(self):
        win = self.window()
        messages = []
        win.bridge.statusMessage.connect(messages.append)
        try:
            main = win.bridge.main_track()
            index = list(win.bridge.project.tracks).index(main)
            self.assertFalse(win.bridge.delete_track(index))
            self.assertEqual(len(self._video_tracks(win)), 3)
            self.assertTrue(any("主轨不能删除" in m for m in messages), messages[-2:])
        finally:
            win.close()

    def test_clear_track_keeps_the_track(self):
        win = self.window()
        try:
            track = self._video_tracks(win)[0]      # 主轨也能清空
            index = list(win.bridge.project.tracks).index(track)
            removed = win.bridge.clear_track(index)
            self.app.processEvents()
            self.assertEqual(removed, 1)
            self.assertEqual(len(self._video_tracks(win)), 3, "清空不该删掉轨道")
            self.assertEqual(len(self._video_tracks(win)[0].segments), 0)
            self.assertTrue(win.bridge.undo())
            self.app.processEvents()
            self.assertEqual(len(self._video_tracks(win)[0].segments), 1,
                             "撤销没把片段还回来")
        finally:
            win.close()

    def test_track_menu_exists_on_the_header(self):
        """轨头菜单里应当有「删除轨道 / 清空轨道」，主轨那条要灰掉。"""
        win = self.window()
        try:
            timeline = win.timeline
            rows = timeline.rows()
            main = win.bridge.main_track()
            main_row = next(i for i, t in enumerate(rows) if t is main)
            menu, delete_act, clear_act = timeline._build_track_menu(rows[main_row])
            self.assertEqual([a.text() for a in menu.actions()],
                             ["删除轨道", "清空轨道"])
            self.assertFalse(delete_act.isEnabled(), "主轨的「删除轨道」应当是灰的")
            self.assertTrue(clear_act.isEnabled())
            other_row = next(i for i, t in enumerate(rows) if t is not main)
            _menu, other_delete, _clear = timeline._build_track_menu(rows[other_row])
            self.assertTrue(other_delete.isEnabled(), "非主轨应当可以删除")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

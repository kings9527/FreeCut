"""第 246 轮：切割工具（缺口 2）与跨轨拖动（缺口 3）。

全部模拟鼠标：切工具 → 在片段上点一下；拖动片段到另一条轨道再松手。
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

    def window(self, tracks: int = 1, length_us: int = 2_000_000):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        media = os.path.join(ROOT, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("没有测试素材")
        win = MainWindow(None)
        win.resize(1280, 820)
        win.show()
        self.app.processEvents()
        project = builder.new_project(640, 360, 30, name="切割")
        segs = []
        for index in range(tracks):
            segs.append(builder.add_video(project, media, start=0, duration=length_us,
                                          track_index=index))
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        return win, segs

    # -- 鼠标 ------------------------------------------------------------- #
    def _pos(self, timeline, x, y):
        from PySide6.QtCore import QPoint

        return timeline.mapFromScene(QPoint(int(x), int(y)))

    def _press(self, timeline, x, y, ctrl=False):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        pos = self._pos(timeline, x, y)
        mods = Qt.ControlModifier if ctrl else Qt.NoModifier
        timeline.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, pos,
                                             timeline.mapToGlobal(pos), Qt.LeftButton,
                                             Qt.LeftButton, mods))

    def _move(self, timeline, x, y):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        pos = self._pos(timeline, x, y)
        timeline.mouseMoveEvent(QMouseEvent(QMouseEvent.MouseMove, pos,
                                            timeline.mapToGlobal(pos), Qt.NoButton,
                                            Qt.LeftButton, Qt.NoModifier))

    def _release(self, timeline, x, y):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        pos = self._pos(timeline, x, y)
        timeline.mouseReleaseEvent(QMouseEvent(QMouseEvent.MouseButtonRelease, pos,
                                               timeline.mapToGlobal(pos), Qt.LeftButton,
                                               Qt.NoButton, Qt.NoModifier))

    @staticmethod
    def _row_y(timeline, row: int) -> float:
        return timeline._track_y(row) + 20

    @staticmethod
    def _clips(win):
        return [s for t in win.bridge.project.tracks for s in t.segments]


class TestRazorTool(_Base):
    def test_razor_click_splits_at_the_click_position(self):
        win, segs = self.window(length_us=2_000_000)
        try:
            timeline = win.timeline
            timeline.set_tool("razor")
            x = timeline._us_to_x(700_000)
            y = self._row_y(timeline, 0)
            self._press(timeline, x, y)
            self.app.processEvents()
            clips = sorted(self._clips(win), key=lambda s: s.target_timerange.start)
            self.assertEqual(len(clips), 2, "切割工具点一下没有切开")
            self.assertAlmostEqual(clips[1].target_timerange.start / 1e6, 0.7, delta=0.05)
            self.assertAlmostEqual(clips[0].target_timerange.duration / 1e6, 0.7, delta=0.05)
        finally:
            win.close()

    def test_razor_near_the_edge_refuses_with_a_message(self):
        win, _segs = self.window(length_us=2_000_000)
        messages = []
        win.bridge.statusMessage.connect(messages.append)
        try:
            timeline = win.timeline
            timeline.set_tool("razor")
            x = timeline._us_to_x(30_000)          # 距边缘 30ms，切不开
            self._press(timeline, x, self._row_y(timeline, 0))
            self.app.processEvents()
            self.assertEqual(len(self._clips(win)), 1, "贴边不该切开")
            self.assertTrue(any("太近" in m for m in messages), f"没有提示：{messages[-2:]}")
        finally:
            win.close()

    def test_select_tool_does_not_split(self):
        win, _segs = self.window(length_us=2_000_000)
        try:
            timeline = win.timeline
            timeline.set_tool("select")
            x = timeline._us_to_x(700_000)
            self._press(timeline, x, self._row_y(timeline, 0))
            self._release(timeline, x, self._row_y(timeline, 0))
            self.assertEqual(len(self._clips(win)), 1, "选择工具不该切开片段")
        finally:
            win.close()

    def test_toolbar_switches_the_tool(self):
        from PySide6.QtCore import Qt

        win, _segs = self.window()
        try:
            win.timeline_toolbar.tool_actions["razor"].trigger()
            self.app.processEvents()
            self.assertEqual(win.timeline.tool, "razor")
            self.assertEqual(win.timeline.viewport().cursor().shape(), Qt.CrossCursor)
            win.timeline_toolbar.tool_actions["select"].trigger()
            self.app.processEvents()
            self.assertEqual(win.timeline.tool, "select")
            self.assertEqual(win.timeline.viewport().cursor().shape(), Qt.ArrowCursor)
        finally:
            win.close()


class TestCrossTrackDrag(_Base):
    def test_drag_clip_to_the_second_video_track(self):
        win, segs = self.window(tracks=2, length_us=2_000_000)
        try:
            timeline = win.timeline
            rows = timeline.rows()
            self.assertGreaterEqual(len(rows), 2)
            seg = segs[0]
            x0 = timeline._us_to_x(500_000)
            x1 = timeline._us_to_x(900_000)
            self._press(timeline, x0, self._row_y(timeline, 0))
            self._move(timeline, x1, self._row_y(timeline, 1))
            self._release(timeline, x1, self._row_y(timeline, 1))
            self.app.processEvents()
            tracks = [t for t in win.bridge.project.tracks if t.type == "video"]
            self.assertIn(seg, tracks[1].segments, "片段没有挪到第二条视频轨")
            self.assertNotIn(seg, tracks[0].segments)
            self.assertGreater(int(seg.target_timerange.start), 0, "跨轨后起点没跟着走")
            self.assertTrue(win.bridge.undo(), "跨轨拖动应当可撤销")
            self.app.processEvents()
            tracks = [t for t in win.bridge.project.tracks if t.type == "video"]
            restored = [s for s in tracks[0].segments if s.material_id == seg.material_id]
            self.assertTrue(restored, "撤销没把片段还给原来的轨道")
            self.assertEqual(int(restored[0].target_timerange.start), 0,
                             "撤销没把起点也还原")
        finally:
            win.close()

    def test_video_clip_cannot_move_to_an_audio_track(self):
        win, segs = self.window(tracks=1, length_us=2_000_000)
        try:
            from freecut.qtui.app import MainWindow  # noqa: F401  （保持导入对称）

            timeline = win.timeline
            win.add_track("audio")
            self.app.processEvents()
            rows = timeline.rows()
            audio_row = next(i for i, t in enumerate(rows) if t.type == "audio")
            seg = segs[0]
            x0 = timeline._us_to_x(400_000)
            self._press(timeline, x0, self._row_y(timeline, 0))
            self._move(timeline, x0 + 60, self._row_y(timeline, audio_row))
            self._release(timeline, x0 + 60, self._row_y(timeline, audio_row))
            self.app.processEvents()
            audio = next(t for t in win.bridge.project.tracks if t.type == "audio")
            self.assertNotIn(seg, audio.segments, "视频片段被挪进音频轨了")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

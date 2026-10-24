"""第 246 轮：多选 / 框选（剪映里"选择工具"那一层，之前完全没有）。

每个用例都模拟真实鼠标：点、Ctrl+点、空白处拖出框选、再按 Delete / Ctrl+C。
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


def demo_media() -> str:
    path = os.path.join(ROOT, "out", "demo.mp4")
    return path if os.path.isfile(path) else ""


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def window_with_clips(self, count: int = 3, length_us: int = 1_000_000):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        media = demo_media()
        if not media:
            self.skipTest("没有测试素材")
        win = MainWindow(None)
        win.resize(1280, 820)
        win.show()
        self.app.processEvents()
        project = builder.new_project(640, 360, 30, name="多选")
        segs = []
        for index in range(count):
            start = index * length_us
            segs.append(builder.add_video(project, media, start=start, duration=length_us))
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        return win, segs

    # -- 鼠标 ------------------------------------------------------------- #
    def _view_pos(self, timeline, x_scene: float, y_scene: float):
        from PySide6.QtCore import QPoint

        return timeline.mapFromScene(QPoint(int(x_scene), int(y_scene)))

    def _press(self, timeline, x, y, ctrl=False):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        mods = Qt.ControlModifier if ctrl else Qt.NoModifier
        pos = self._view_pos(timeline, x, y)
        timeline.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, pos,
                                             timeline.mapToGlobal(pos), Qt.LeftButton,
                                             Qt.LeftButton, mods))

    def _move(self, timeline, x, y, ctrl=False, buttons=True):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        mods = Qt.ControlModifier if ctrl else Qt.NoModifier
        held = Qt.LeftButton if buttons else Qt.NoButton
        pos = self._view_pos(timeline, x, y)
        timeline.mouseMoveEvent(QMouseEvent(QMouseEvent.MouseMove, pos,
                                            timeline.mapToGlobal(pos), Qt.NoButton,
                                            held, mods))

    def _release(self, timeline, x, y, ctrl=False):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        mods = Qt.ControlModifier if ctrl else Qt.NoModifier
        pos = self._view_pos(timeline, x, y)
        timeline.mouseReleaseEvent(QMouseEvent(QMouseEvent.MouseButtonRelease, pos,
                                               timeline.mapToGlobal(pos), Qt.LeftButton,
                                               Qt.NoButton, mods))

    @staticmethod
    def _clip_point(timeline, seg):
        rows = timeline.rows()
        row = rows.index(next(t for t in rows if seg in t.segments))
        x = timeline._us_to_x(int(seg.target_timerange.start)
                              + int(seg.target_timerange.duration) // 2)
        return x, timeline._track_y(row) + 20

    @staticmethod
    def _names(win):
        return [id(s) for s in win.bridge.selection]


class TestClickSelection(_Base):
    def test_click_selects_one_clip(self):
        win, segs = self.window_with_clips()
        try:
            timeline = win.timeline
            x, y = self._clip_point(timeline, segs[0])
            self._press(timeline, x, y)
            self._release(timeline, x, y)
            self.assertEqual(win.bridge.selection, [segs[0]])
        finally:
            win.close()

    def test_ctrl_click_adds_then_removes(self):
        win, segs = self.window_with_clips()
        try:
            timeline = win.timeline
            x0, y0 = self._clip_point(timeline, segs[0])
            x1, y1 = self._clip_point(timeline, segs[1])
            self._press(timeline, x0, y0)
            self._release(timeline, x0, y0)
            self._press(timeline, x1, y1, ctrl=True)
            self._release(timeline, x1, y1, ctrl=True)
            self.assertEqual(len(win.bridge.selection), 2, "Ctrl+点没有加选")
            self._press(timeline, x1, y1, ctrl=True)
            self._release(timeline, x1, y1, ctrl=True)
            self.assertEqual(win.bridge.selection, [segs[0]], "Ctrl+点同一个没有取消选择")
        finally:
            win.close()

    def test_clicking_empty_area_clears_selection(self):
        win, segs = self.window_with_clips()
        try:
            timeline = win.timeline
            win.bridge.select(segs[0])
            rows = timeline.rows()
            row = rows.index(next(t for t in rows if segs[0] in t.segments))
            x = timeline._us_to_x(4_000_000)          # 所有片段右边
            y = timeline._track_y(row) + 20
            self._press(timeline, x, y)
            self._release(timeline, x, y)
            self.assertEqual(win.bridge.selection, [])
        finally:
            win.close()


class TestRubberBand(_Base):
    def test_drag_on_empty_area_selects_the_covered_clips(self):
        win, segs = self.window_with_clips(count=4)
        try:
            timeline = win.timeline
            rows = timeline.rows()
            row = rows.index(next(t for t in rows if segs[0] in t.segments))
            y = timeline._track_y(row) + 20
            x_from = timeline._us_to_x(4_600_000)     # 空白处（所有片段右边）
            x_to = timeline._us_to_x(500_000)         # 拖回到第一个片段里
            self._press(timeline, x_from, y)
            self._move(timeline, (x_from + x_to) // 2, y)
            self._move(timeline, x_to, y)
            self._release(timeline, x_to, y)
            self.assertEqual(len(win.bridge.selection), 4,
                             f"框选应盖住 4 个片段，实际 {len(win.bridge.selection)}")
            self.assertIsNone(timeline._band_rect, "松手后框选矩形没清掉")
        finally:
            win.close()

    def test_band_only_takes_intersecting_clips(self):
        win, segs = self.window_with_clips(count=4)
        try:
            timeline = win.timeline
            rows = timeline.rows()
            row = rows.index(next(t for t in rows if segs[0] in t.segments))
            y = timeline._track_y(row) + 20
            x_from = timeline._us_to_x(4_600_000)
            x_to = timeline._us_to_x(2_500_000)       # 只盖住后两个（2-3s、3-4s）
            self._press(timeline, x_from, y)
            self._move(timeline, x_to, y)
            self._release(timeline, x_to, y)
            chosen = sorted(win.bridge.selection,
                            key=lambda s: s.target_timerange.start)
            self.assertEqual(chosen, [segs[2], segs[3]])
        finally:
            win.close()


class TestMultiCommands(_Base):
    def _select_two(self, win, segs):
        win.bridge.select_many([segs[0], segs[1]])
        self.app.processEvents()

    def test_delete_removes_all_selected(self):
        win, segs = self.window_with_clips()
        try:
            self._select_two(win, segs)
            removed = win.bridge.delete_selected()
            self.assertEqual(removed, 2)
            left = [s for t in win.bridge.project.tracks for s in t.segments]
            self.assertEqual(left, [segs[2]])
            self.assertTrue(win.bridge.undo(), "多选删除应当能一次撤销")
            self.app.processEvents()
            left = [s for t in win.bridge.project.tracks for s in t.segments]
            self.assertEqual(len(left), 3, "撤销没把两个片段都还回来")
        finally:
            win.close()

    def test_copy_paste_keeps_relative_offsets(self):
        win, segs = self.window_with_clips(count=4)
        try:
            self._select_two(win, segs)
            self.assertTrue(win.bridge.copy_selected())
            win.bridge.set_playhead(5_000_000)
            base = win.bridge.playhead               # 播放头会被工程长度钳住，以实际值为准
            pasted = win.bridge.paste()
            self.assertIsNotNone(pasted, "多选粘贴没有产出片段")
            clips = [s for t in win.bridge.project.tracks for s in t.segments]
            self.assertEqual(len(clips), 6, f"应当多出 2 个片段，实际 {len(clips)}")
            fresh = [s for s in clips if s not in segs]
            starts = sorted(int(s.target_timerange.start) for s in fresh)
            self.assertEqual(starts, [base, base + 1_000_000],
                             "粘贴出来的两个片段没有保持原来的间隔")
        finally:
            win.close()

    def test_mirror_flips_every_selected_in_one_undo(self):
        win, segs = self.window_with_clips()
        try:
            self._select_two(win, segs)
            self.assertTrue(win.bridge.mirror("h"))
            self.assertTrue(segs[0].clip.flip_h and segs[1].clip.flip_h,
                            "多选镜像没有作用到两个片段")
            self.assertFalse(segs[2].clip.flip_h, "没选中的片段被镜像了")
            self.assertTrue(win.bridge.undo())
            self.app.processEvents()
            # 撤销后片段对象被换掉，重新取
            clips = sorted([s for t in win.bridge.project.tracks for s in t.segments],
                           key=lambda s: s.target_timerange.start)
            self.assertFalse(any(s.clip.flip_h for s in clips), "一次撤销没把两个都还原")
        finally:
            win.close()

    def test_count_label_in_status(self):
        win, segs = self.window_with_clips()
        messages = []
        win.bridge.statusMessage.connect(messages.append)
        try:
            win.bridge.select_many([segs[0], segs[1]])
            win.bridge.delete_selected()
            self.assertTrue(any("删除 2 个片段" in m for m in messages),
                            f"状态栏没说删了几个：{messages[-3:]}")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

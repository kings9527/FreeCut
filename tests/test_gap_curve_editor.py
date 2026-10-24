"""第 247 轮：曲线变速编辑器（缺口 9）。

剪映「变速 → 曲线变速」是能拖点的图。这里模拟用户：拖点、双击加点、右键删点，
每一下都要真的改到 seg.speed_curve，并且逐帧取帧跟着变（source_time 会积分曲线）。
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
        project = builder.new_project(640, 360, 30, name="曲线")
        seg = builder.add_video(project, media, start=0, duration=4_000_000)
        win.bridge.set_project(project, None)
        win._refresh_all()
        win.bridge.select(seg)
        win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("变速"))
        self.app.processEvents()
        return win, seg

    @staticmethod
    def _editor(win):
        return win.inspector.speed_curve_editor

    def _drag_point(self, editor, index: int, to_x: float, to_y: float):
        """把第 index 个点拖到数据坐标 (to_x, to_y)。"""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        start = editor._to_px(*editor.points()[index])
        target = editor._to_px(to_x, to_y)
        for kind, pos, buttons in (
            (QMouseEvent.MouseButtonPress, start, Qt.LeftButton),
            (QMouseEvent.MouseMove, target, Qt.LeftButton),
            (QMouseEvent.MouseButtonRelease, target, Qt.NoButton),
        ):
            event = QMouseEvent(kind, pos, editor.mapToGlobal(pos.toPoint()),
                                Qt.LeftButton if kind != QMouseEvent.MouseMove else Qt.NoButton,
                                buttons, Qt.NoModifier)
            if kind == QMouseEvent.MouseButtonPress:
                editor.mousePressEvent(event)
            elif kind == QMouseEvent.MouseMove:
                editor.mouseMoveEvent(event)
            else:
                editor.mouseReleaseEvent(event)


class TestCurveEditor(_Base):
    def test_editor_starts_as_a_flat_line(self):
        win, _seg = self.window()
        try:
            points = self._editor(win).points()
            self.assertEqual(len(points), 2)
            self.assertEqual([round(y, 2) for _x, y in points], [1.0, 1.0])
        finally:
            win.close()

    def test_dragging_a_point_changes_the_clip_curve(self):
        win, seg = self.window()
        try:
            editor = self._editor(win)
            self._drag_point(editor, 1, 0.9, 3.0)     # 把结尾那个点往上拖
            self.app.processEvents()
            curve = list(seg.speed_curve or [])
            self.assertEqual(len(curve), 2, f"曲线没写进片段：{curve}")
            self.assertGreater(curve[-1][1], 2.0, f"拖上去速度没变：{curve}")
            self.assertGreater(float(seg.speed), 1.0, "平均速度没跟着更新")
        finally:
            win.close()

    def test_double_click_adds_a_point_and_right_click_removes_it(self):
        win, seg = self.window()
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        try:
            editor = self._editor(win)
            middle = editor._to_px(0.5, 2.5)
            editor.mouseDoubleClickEvent(QMouseEvent(
                QMouseEvent.MouseButtonDblClick, middle,
                editor.mapToGlobal(middle.toPoint()), Qt.LeftButton, Qt.LeftButton,
                Qt.NoModifier))
            self.app.processEvents()
            self.assertEqual(len(seg.speed_curve), 3, f"双击没加点：{seg.speed_curve}")

            hit = editor._to_px(*seg.speed_curve[1])
            editor.mousePressEvent(QMouseEvent(
                QMouseEvent.MouseButtonPress, hit, editor.mapToGlobal(hit.toPoint()),
                Qt.RightButton, Qt.RightButton, Qt.NoModifier))
            self.app.processEvents()
            self.assertEqual(len(seg.speed_curve), 2, f"右键没删点：{seg.speed_curve}")
        finally:
            win.close()

    def test_editing_is_undoable(self):
        win, seg = self.window()
        try:
            editor = self._editor(win)
            self._drag_point(editor, 0, 0.0, 0.4)
            self.app.processEvents()
            self.assertTrue(seg.speed_curve, "拖完曲线是空的")
            self.assertTrue(win.bridge.undo(), "拖曲线应当可撤销")
            self.app.processEvents()
            clips = [s for t in win.bridge.project.tracks for s in t.segments]
            self.assertFalse(clips[0].speed_curve, "撤销后曲线还在")
        finally:
            win.close()

    def test_preset_updates_the_editor(self):
        win, seg = self.window()
        try:
            win.inspector.speed_curve_box.setCurrentIndex(1)
            win.inspector._apply_speed_curve()
            self.app.processEvents()
            shown = self._editor(win).points()
            self.assertEqual(len(shown), len(seg.speed_curve),
                             "套用预设后编辑器里的点数没跟上")
        finally:
            win.close()

    def test_source_time_follows_the_curve(self):
        """曲线真的影响逐帧取帧：同一时间点，速度高的一侧应当走得更远。"""
        win, seg = self.window()
        try:
            from freecut import builder

            builder.set_speed_curve(win.bridge.project, seg,
                                    [{"x": 0.0, "y": 0.5}, {"x": 1.0, "y": 3.0}])
            start = int(seg.target_timerange.start)
            early = seg.source_time(start + 1_000_000)
            late = seg.source_time(start + 3_000_000)
            self.assertGreater(late - early, 1_500_000,
                               f"后段速度更高，源时间应当走得更快（{early} → {late}）")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

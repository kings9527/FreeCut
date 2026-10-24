"""第 246 轮：轨道编组（缺口 7 的最后一块）。

剪映的"编组"：几个片段绑在一起，点一个选中整组、拖一个整组走、删一个删整组。
这里全部按用户操作验：点、拖、删、拆组、存盘。
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

    def window(self, count: int = 3):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        media = os.path.join(ROOT, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("没有测试素材")
        win = MainWindow(None)
        win.resize(1280, 820)
        win.show()
        self.app.processEvents()
        project = builder.new_project(640, 360, 30, name="编组")
        segs = [builder.add_video(project, media, start=i * 1_000_000, duration=1_000_000)
                for i in range(count)]
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        return win, segs

    def _pos(self, timeline, x, y):
        from PySide6.QtCore import QPoint

        return timeline.mapFromScene(QPoint(int(x), int(y)))

    def _press(self, timeline, x, y):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QMouseEvent

        pos = self._pos(timeline, x, y)
        timeline.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, pos,
                                             timeline.mapToGlobal(pos), Qt.LeftButton,
                                             Qt.LeftButton, Qt.NoModifier))

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
    def _point(timeline, seg):
        rows = timeline.rows()
        row = rows.index(next(t for t in rows if seg in t.segments))
        x = timeline._us_to_x(int(seg.target_timerange.start) + 300_000)
        return x, timeline._track_y(row) + 20


class TestGrouping(_Base):
    def test_group_then_click_selects_the_whole_group(self):
        win, segs = self.window()
        try:
            win.bridge.select_many([segs[0], segs[1]])
            self.assertEqual(win.bridge.group_selected(), 2)
            win.bridge.select(None)
            x, y = self._point(win.timeline, segs[1])
            self._press(win.timeline, x, y)
            self._release(win.timeline, x, y)
            self.assertEqual(len(win.bridge.selection), 2, "点组里的一个没有选中整组")
            self.assertNotIn(segs[2], win.bridge.selection)
        finally:
            win.close()

    def test_dragging_one_member_moves_the_whole_group(self):
        win, segs = self.window()
        try:
            win.bridge.select_many([segs[0], segs[1]])
            win.bridge.group_selected()
            timeline = win.timeline
            x, y = self._point(timeline, segs[0])
            self._press(timeline, x, y)
            self._move(timeline, x + 80, y)
            self._release(timeline, x + 80, y)
            self.app.processEvents()
            delta0 = int(segs[0].target_timerange.start) - 0
            delta1 = int(segs[1].target_timerange.start) - 1_000_000
            self.assertGreater(delta0, 0, "拖了但没动")
            self.assertEqual(delta0, delta1, "编组里两个片段移动量不一致")
            self.assertTrue(win.bridge.undo(), "编组移动应当可撤销")
            self.app.processEvents()
            clips = sorted([s for t in win.bridge.project.tracks for s in t.segments],
                           key=lambda s: s.target_timerange.start)
            self.assertEqual([int(s.target_timerange.start) for s in clips],
                             [0, 1_000_000, 2_000_000], "撤销没把整组还原")
        finally:
            win.close()

    def test_delete_removes_the_whole_group(self):
        win, segs = self.window()
        try:
            win.bridge.select_many([segs[0], segs[1]])
            win.bridge.group_selected()
            win.bridge.select(segs[0])
            removed = win.bridge.delete_selected()
            self.assertEqual(removed, 2, f"删编组只删了 {removed} 个")
        finally:
            win.close()

    def test_ungroup_restores_independent_selection(self):
        win, segs = self.window()
        try:
            win.bridge.select_many([segs[0], segs[1]])
            win.bridge.group_selected()
            self.assertEqual(win.bridge.ungroup_selected(), 2)
            win.bridge.select(segs[0])
            self.assertEqual(win.bridge.selection, [segs[0]], "拆组后仍然整组选中")
        finally:
            win.close()

    def test_group_id_survives_save_and_load(self):
        win, segs = self.window()
        try:
            win.bridge.select_many([segs[0], segs[1]])
            win.bridge.group_selected()
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "group.json")
                self.assertTrue(win.bridge.save(path))
                from freecut.model import Project

                again = Project.load(path)
                gids = [s.group_id for t in again.tracks for s in t.segments if s.group_id]
                self.assertEqual(len(gids), 2, "编组没有存进工程")
                self.assertEqual(len(set(gids)), 1)
        finally:
            win.close()

    def test_single_selection_cannot_group(self):
        win, segs = self.window()
        messages = []
        win.bridge.statusMessage.connect(messages.append)
        try:
            win.bridge.select(segs[0])
            self.assertEqual(win.bridge.group_selected(), 0)
            self.assertTrue(any("至少选中两个" in m for m in messages))
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

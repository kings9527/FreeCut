"""第 243 轮：用户实测报的四个问题，一个类一条，全部**模拟用户操作**。

这里刻意不碰命令行：每个用例都是"起窗口 → 造真实 Qt 事件 → 断言界面上发生了什么"。
四个问题各成一类，互不依赖，坏了能一眼看出是哪一条：

1. ``TestIssuePlaybackQuality``  播放时画质变糊、暂停又恢复
2. ``TestIssueContextMenu``      右键菜单点了没反应
3. ``TestIssueTimelineScrub``    时间线标尺拖不动播放头
4. ``TestIssueLibraryLabels``    特效/转场没有名字、看不出是否已用
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
    for name in ("demo.mp4", "showcase.mp4", "demo_audio.mp4"):
        path = os.path.join(ROOT, "out", name)
        if os.path.isfile(path):
            return path
    return ""


class _WindowCase(unittest.TestCase):
    """公共底座：一个 640×360 / 4 秒的工程 + 一个视频片段。"""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:                       # noqa: BLE001
            raise unittest.SkipTest(f"没有 PySide6: {exc}")
        cls.app = QApplication.instance() or QApplication([])

    def window_with_clip(self, duration_us: int = 4_000_000):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        media = demo_media()
        if not media:
            self.skipTest("没有测试素材")
        win = MainWindow(None)
        win.resize(1280, 820)
        win.show()
        self.app.processEvents()
        project = builder.new_project(640, 360, 30, name="问题复现")
        seg = builder.add_video(project, media, start=0, duration=duration_us)
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        win.bridge.select(seg)
        self.app.processEvents()
        return win, seg

    @staticmethod
    def clips(win) -> int:
        return sum(len(t.segments) for t in win.bridge.project.tracks)


# --------------------------------------------------------------------------- #
# 问题 1：播放时画质变糊
# --------------------------------------------------------------------------- #
class TestIssuePlaybackQuality(_WindowCase):
    def test_render_size_does_not_change_when_playing(self):
        """播放中的渲染尺寸必须和暂停时一致——用户看到的"糊"就是这个尺寸变小。"""
        win, _seg = self.window_with_clip()
        try:
            paused = win.viewer._render_size()
            win.toggle_play()
            self.app.processEvents()
            playing = win.viewer._render_size()
            win.toggle_play()
            self.assertEqual(playing, paused,
                             f"播放时渲染尺寸从 {paused} 变成 {playing}（画面会糊）")
        finally:
            win.close()

    def test_smooth_toggle_is_the_only_way_to_downscale(self):
        """「流畅」是显式开关：只有勾上它、且在播放中，才降到 1/3。"""
        win, _seg = self.window_with_clip()
        try:
            full = win.viewer._render_size()
            win.viewer.smooth_btn.setChecked(True)
            self.app.processEvents()
            self.assertEqual(win.viewer._render_size(), full, "暂停时勾流畅不该降分辨率")

            win.toggle_play()
            self.app.processEvents()
            playing_smooth = win.viewer._render_size()
            win.toggle_play()
            self.assertLess(playing_smooth.width(), full.width(),
                            "勾了流畅，播放时应降到小尺寸")
            self.assertLessEqual(playing_smooth.width(), full.width() * 2 // 3)
        finally:
            win.close()

    def test_original_button_shows_one_to_one_pixels(self):
        """「原图」= 1:1 像素（不是"播放时别降分辨率"的开关）。"""
        win, _seg = self.window_with_clip()
        try:
            win.viewer.original_btn.setChecked(True)
            self.app.processEvents()
            size = win.viewer._fit_size()
            canvas = win.bridge.project.canvas
            self.assertEqual((size.width(), size.height()),
                             (int(canvas.width), int(canvas.height)))
        finally:
            win.close()


# --------------------------------------------------------------------------- #
# 问题 2：右键菜单点了没反应
# --------------------------------------------------------------------------- #
class TestIssueContextMenu(_WindowCase):
    def test_duplicate_really_adds_a_clip(self):
        from freecut.qtui.menus import build_context_menu

        win, seg = self.window_with_clip()
        try:
            before = self.clips(win)
            menu = build_context_menu(win, seg)
            menu._actions_by_key["duplicate"].trigger()     # 等价于用户点了这一项
            self.app.processEvents()
            self.assertEqual(self.clips(win), before + 1, "「复制到末尾」点了没反应")
        finally:
            win.close()

    def test_mirror_really_flips_the_clip(self):
        from freecut.qtui.menus import build_context_menu

        win, seg = self.window_with_clip()
        try:
            before = bool(seg.clip.flip_h)
            build_context_menu(win, seg)._actions_by_key["mirror_h"].trigger()
            self.app.processEvents()
            self.assertNotEqual(bool(seg.clip.flip_h), before, "「水平镜像」点了没反应")
        finally:
            win.close()

    def test_delete_really_removes_the_clip(self):
        from freecut.qtui.menus import build_context_menu

        win, seg = self.window_with_clip()
        try:
            before = self.clips(win)
            build_context_menu(win, seg)._actions_by_key["delete"].trigger()
            self.app.processEvents()
            self.assertLess(self.clips(win), before, "「删除」点了没反应")
        finally:
            win.close()

    def test_split_follows_the_playhead(self):
        from freecut.qtui.menus import build_context_menu

        win, seg = self.window_with_clip()
        try:
            win.bridge.set_playhead(2_000_000)
            self.app.processEvents()
            menu = build_context_menu(win, seg)
            self.assertTrue(menu._actions_by_key["split"].isEnabled())
            menu._actions_by_key["split"].trigger()
            self.app.processEvents()
            self.assertEqual(self.clips(win), 2, "「分割」点了没反应")
        finally:
            win.close()

    def test_copy_then_paste_from_menu(self):
        from freecut.qtui.menus import build_context_menu

        win, seg = self.window_with_clip(duration_us=12_000_000)
        try:
            before = self.clips(win)
            menu = build_context_menu(win, seg)
            menu._actions_by_key["copy"].trigger()
            self.app.processEvents()
            # 粘贴落在播放头；挪到片段中段，避免正好盖住原片段
            win.bridge.set_playhead(6_000_000)
            self.app.processEvents()
            # 再开一次菜单（用户路径：重新右键 → 粘贴）
            menu2 = build_context_menu(win, seg)
            self.assertTrue(menu2._actions_by_key["paste"].isEnabled(),
                            "复制之后「粘贴」应当变可用")
            menu2._actions_by_key["paste"].trigger()
            self.app.processEvents()
            self.assertEqual(self.clips(win), before + 1, "「粘贴」点了没反应")
        finally:
            win.close()

    def test_unavailable_items_stay_disabled(self):
        from freecut.qtui.menus import UNAVAILABLE, build_context_menu

        win, seg = self.window_with_clip()
        try:
            menu = build_context_menu(win, seg)
            # 第 4 轮按目标改成"删除"：云端做不到的项不再出现在菜单里
            for key in ("smart_matting", "ai_voice"):
                self.assertNotIn(key, menu._actions_by_key,
                                 f"{key} 依赖云端，不该再出现在菜单里")
        finally:
            win.close()


# --------------------------------------------------------------------------- #
# 问题 3：时间线拖不动播放头
# --------------------------------------------------------------------------- #
class TestIssueTimelineScrub(_WindowCase):
    def _mouse(self, timeline, kind, x, y):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QMouseEvent

        button = Qt.LeftButton if kind != QMouseEvent.MouseMove else Qt.NoButton
        event = QMouseEvent(kind, QPoint(int(x), int(y)), button,
                            Qt.LeftButton if kind != QMouseEvent.MouseMove else Qt.NoButton,
                            Qt.NoModifier)
        if kind == QMouseEvent.MouseButtonPress:
            timeline.mousePressEvent(event)
        elif kind == QMouseEvent.MouseMove:
            timeline.mouseMoveEvent(event)
        else:
            timeline.mouseReleaseEvent(event)

    def test_dragging_the_ruler_moves_the_playhead(self):
        from PySide6.QtGui import QMouseEvent

        from freecut.qtui.widgets.timeline import HEADER_H

        # 工程要够长：4 秒的工程里 x=520 会被钳在结尾，看不出"拖动有没有生效"
        win, _seg = self.window_with_clip(duration_us=12_000_000)
        try:
            timeline = win.timeline
            y = HEADER_H // 2
            self._mouse(timeline, QMouseEvent.MouseButtonPress, 300, y)
            first = win.bridge.playhead
            self._mouse(timeline, QMouseEvent.MouseMove, 420, y)
            second = win.bridge.playhead
            self._mouse(timeline, QMouseEvent.MouseMove, 540, y)
            third = win.bridge.playhead
            self._mouse(timeline, QMouseEvent.MouseButtonRelease, 540, y)
            self.assertGreater(second, first, "按下后向右拖，播放头没跟着走")
            self.assertGreater(third, second, "继续拖，播放头没继续走")
        finally:
            win.close()

    def test_release_stops_the_scrub(self):
        from PySide6.QtGui import QMouseEvent

        from freecut.qtui.widgets.timeline import HEADER_H

        win, _seg = self.window_with_clip()
        try:
            timeline = win.timeline
            y = HEADER_H // 2
            self._mouse(timeline, QMouseEvent.MouseButtonPress, 400, y)
            self._mouse(timeline, QMouseEvent.MouseButtonRelease, 400, y)
            after_release = win.bridge.playhead
            self._mouse(timeline, QMouseEvent.MouseMove, 700, y)
            self.assertEqual(win.bridge.playhead, after_release,
                             "松开鼠标后播放头还在跟着走")
        finally:
            win.close()

    def test_dragging_the_ruler_does_not_move_clips(self):
        """拖标尺时片段必须纹丝不动（拖播放头 ≠ 拖片段）。"""
        from PySide6.QtGui import QMouseEvent

        from freecut.qtui.widgets.timeline import HEADER_H

        win, seg = self.window_with_clip(duration_us=12_000_000)
        try:
            timeline = win.timeline
            y = HEADER_H // 2
            start_before = int(seg.target_timerange.start)
            duration_before = int(seg.target_timerange.duration)
            self._mouse(timeline, QMouseEvent.MouseButtonPress, 300, y)
            self._mouse(timeline, QMouseEvent.MouseMove, 700, y)
            self._mouse(timeline, QMouseEvent.MouseButtonRelease, 700, y)
            self.app.processEvents()
            self.assertEqual(int(seg.target_timerange.start), start_before,
                             "拖标尺把片段起点挪了")
            self.assertEqual(int(seg.target_timerange.duration), duration_before,
                             "拖标尺把片段时长改了")
        finally:
            win.close()

    def test_pressing_on_a_clip_still_moves_it(self):
        """轨道区里按下仍然是在拖片段（别被"拖播放头"抢走）。"""
        from PySide6.QtGui import QMouseEvent

        win, seg = self.window_with_clip(duration_us=12_000_000)
        try:
            timeline = win.timeline
            row = timeline.rows().index(next(t for t in timeline.rows() if seg in t.segments))
            y = timeline._track_y(row) + 20
            x = timeline._us_to_x(int(seg.target_timerange.start)) + 60
            start_before = int(seg.target_timerange.start)
            self._mouse(timeline, QMouseEvent.MouseButtonPress, x, y)
            self._mouse(timeline, QMouseEvent.MouseMove, x + 120, y)
            self._mouse(timeline, QMouseEvent.MouseButtonRelease, x + 120, y)
            self.app.processEvents()
            self.assertGreater(int(seg.target_timerange.start), start_before,
                               "按在片段上却没拖动它")
        finally:
            win.close()


# --------------------------------------------------------------------------- #
# 问题 4：特效/转场没有名字、看不出是否已用
# --------------------------------------------------------------------------- #
class TestIssueLibraryLabels(_WindowCase):
    def test_effect_items_show_a_name_and_have_room_for_it(self):
        from PySide6.QtCore import Qt

        from freecut.qtui.library import mark_applied

        win, _seg = self.window_with_clip()
        try:
            win.bin.show_category("特效")
            self.app.processEvents()
            grid = win.bin.effect_list
            self.assertGreater(grid.count(), 5)
            item = grid.item(0)
            name = item.text()
            self.assertTrue(name, "特效条目没有名字")
            rect = grid.visualItemRect(item)
            icon = grid.iconSize()
            self.assertGreaterEqual(rect.height(), icon.height() + 12,
                                    f"格子只有 {rect.height()}px 高，图标 {icon.height()}px，"
                                    "名字放不下")
            # 打勾标记不能把名字弄丢
            mark_applied(item, True)
            self.assertIn("✓", item.text())
            self.assertIn(name.replace("✓ ", ""), item.text())
            mark_applied(item, False)
            self.assertEqual(item.text(), name, "取消标记后名字应当还原")
        finally:
            win.close()

    def test_applying_an_effect_marks_it_and_says_so(self):
        from PySide6.QtCore import Qt

        win, _seg = self.window_with_clip()
        try:
            win.bin.show_category("特效")
            self.app.processEvents()
            grid = win.bin.effect_list
            target = grid.item(2)
            kind = str(target.data(Qt.UserRole) or "")
            grid.setCurrentItem(target)
            win.bin.effect_apply_btn.click()            # 用户点"加到选中片段"
            self.app.processEvents()
            self.assertTrue(grid.item(2).text().startswith("✓"),
                            f"加完特效后条目没有打勾：{grid.item(2).text()!r}")
            self.assertIn("当前片段", win.bin.effect_applied.text())
            self.assertNotIn("未使用", win.bin.effect_applied.text())
            self.assertTrue(kind)
        finally:
            win.close()

    def test_applying_a_transition_marks_it_and_says_so(self):
        win, _seg = self.window_with_clip()
        try:
            win.bin.show_category("转场")
            self.app.processEvents()
            grid = win.bin.transition_list
            grid.setCurrentItem(grid.item(0))
            name = grid.item(0).text()
            win.bin.transition_apply_btn.click()        # 用户点"加在片段开头"
            self.app.processEvents()
            self.assertTrue(grid.item(0).text().startswith("✓"),
                            f"加完转场后条目没有打勾：{grid.item(0).text()!r}")
            self.assertIn(name.replace("✓ ", ""), win.bin.transition_applied.text())
        finally:
            win.close()

    def test_selection_change_updates_the_applied_hint(self):
        win, seg = self.window_with_clip()
        try:
            win.bin.show_category("转场")
            self.app.processEvents()
            grid = win.bin.transition_list
            grid.setCurrentItem(grid.item(0))
            win.bin.transition_apply_btn.click()
            self.app.processEvents()
            self.assertIn("开头有", win.bin.transition_applied.text())

            win.bridge.select(None)                     # 取消选中
            self.app.processEvents()
            self.assertIn("没有转场", win.bin.transition_applied.text(),
                          "取消选中后还显示已应用")
            self.assertFalse(grid.item(0).text().startswith("✓"),
                             "取消选中后条目还打着勾")
        finally:
            win.close()


# --------------------------------------------------------------------------- #
# 补充（第 244 轮）：转场到底怎么用——换、看得见、undo
# --------------------------------------------------------------------------- #
class TestTransitionWorkflow(_WindowCase):
    """用户问"转场这一部分应该怎么使用"，暴露了三件没做好的事。"""

    def _two_clips(self):
        """一条轨道上两个首尾相接的片段：转场要加在后一个上才看得出来。"""
        from freecut import builder
        from freecut.qtui.app import MainWindow

        media = demo_media()
        if not media:
            self.skipTest("没有测试素材")
        win = MainWindow(None)
        win.resize(1280, 820)
        win.show()
        self.app.processEvents()
        project = builder.new_project(640, 360, 30, name="转场")
        first = builder.add_video(project, media, start=0, duration=2_000_000)
        second = builder.add_video(project, media, start=2_000_000, duration=2_000_000)
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        # 用户的实际顺序：先在时间线上点中"后一个"片段，再去转场页点一下
        win.bridge.select(second)
        self.app.processEvents()
        win.bin.show_category("转场")
        self.app.processEvents()
        return win, first, second

    @staticmethod
    def _transition_refs(win, seg):
        return [r for r in win.bridge.project.refs_of(seg)
                if getattr(r, "type", "") == "transition"]

    def test_applying_another_transition_replaces_the_first(self):
        win, _first, second = self._two_clips()
        try:
            grid = win.bin.transition_list
            grid.setCurrentItem(grid.item(0))            # 叠化
            win.bin.transition_apply_btn.click()
            self.app.processEvents()
            grid.setCurrentItem(grid.item(1))            # 换成闪黑
            win.bin.transition_apply_btn.click()
            self.app.processEvents()
            refs = self._transition_refs(win, second)
            self.assertEqual(len(refs), 1, f"换转场变成了叠加：{[r.get('kind') for r in refs]}")
            self.assertEqual(str(refs[0].get("kind")), "fade_black",
                             "换完之后渲染用的还不是新转场")
            marked = [grid.item(i).text() for i in range(grid.count())
                      if grid.item(i).text().startswith("✓")]
            self.assertEqual(marked, ["✓ 闪黑"], "打勾的应当是最后选的那个")
        finally:
            win.close()

    def test_apply_moves_the_playhead_into_the_transition(self):
        win, _first, second = self._two_clips()
        try:
            win.bin.transition_duration.setValue(0.8)
            grid = win.bin.transition_list
            grid.setCurrentItem(grid.item(0))
            win.bin.transition_apply_btn.click()
            self.app.processEvents()
            start = int(second.target_timerange.start)
            playhead = win.bridge.playhead
            self.assertGreaterEqual(playhead, start, "播放头没跳到转场起点之后")
            self.assertLess(playhead, start + 800_000, "播放头跳过了转场窗口")
        finally:
            win.close()

    def test_switching_is_undoable(self):
        win, _first, second = self._two_clips()
        try:
            grid = win.bin.transition_list
            grid.setCurrentItem(grid.item(0))
            win.bin.transition_apply_btn.click()
            self.app.processEvents()
            grid.setCurrentItem(grid.item(1))
            win.bin.transition_apply_btn.click()
            self.app.processEvents()
            self.assertTrue(win.bridge.undo(), "换转场应当可以撤销")
            self.app.processEvents()
            refs = self._transition_refs(win, second)
            self.assertEqual(len(refs), 1)
            self.assertEqual(str(refs[0].get("kind")), "cross_dissolve",
                             "撤销之后没有回到上一个转场")
        finally:
            win.close()

    def test_timeline_shows_a_badge_for_the_transition(self):
        win, _first, second = self._two_clips()
        try:
            timeline = win.timeline
            self.assertFalse(timeline._has_transition(second))
            grid = win.bin.transition_list
            grid.setCurrentItem(grid.item(0))
            win.bin.transition_apply_btn.click()
            self.app.processEvents()
            self.assertTrue(timeline._has_transition(second),
                            "时间线上认不出片段带了转场")
            timeline.viewport().repaint()                # 画一遍不该抛异常
        finally:
            win.close()

    def test_first_clip_gets_an_honest_warning(self):
        """轨道开头那个片段加非"闪黑/闪白"转场看不出效果，要如实说。"""
        win, first, _second = self._two_clips()
        messages = []
        win.bridge.statusMessage.connect(messages.append)
        try:
            win.bridge.select(first)
            self.app.processEvents()
            grid = win.bin.transition_list
            grid.setCurrentItem(grid.item(0))            # 叠化：需要左边的片段
            win.bin.transition_apply_btn.click()
            self.app.processEvents()
            self.assertTrue(any("左边" in m for m in messages),
                            f"开头片段加转场没提示：{messages[-2:]}")
        finally:
            win.close()

    def test_bin_pages_scroll_when_window_is_short(self):
        """窗口矮时"加在片段开头"这类按钮不能被切掉（用户截图里就被切了）。"""
        win, _first, _second = self._two_clips()
        try:
            win.resize(1100, 620)                        # 故意矮
            self.app.processEvents()
            win.bin.show_category("转场")
            self.app.processEvents()
            scroll = win.bin._page_scrolls["转场"]
            self.assertIsNotNone(scroll)
            button = win.bin.transition_apply_btn
            viewport = scroll.viewport()
            top = button.mapTo(viewport, button.rect().topLeft()).y()
            self.assertGreaterEqual(top, 0, "底部按钮被切到视口上方")
            self.assertLessEqual(top + button.height(), viewport.height() + 1,
                                 "底部按钮被切到视口下方（应当能滚动到）")
        finally:
            win.close()


# --------------------------------------------------------------------------- #
# 第 245 轮：素材库的特效/转场要能**拖**到时间线上（剪映的方式）
# --------------------------------------------------------------------------- #
class TestLibraryDragDrop(_WindowCase):
    @staticmethod
    def _drop_on(timeline, seg, mime):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QDropEvent

        row = timeline.rows().index(next(t for t in timeline.rows() if seg in t.segments))
        x = timeline._us_to_x(int(seg.target_timerange.start)
                              + int(seg.target_timerange.duration) // 2)
        y = timeline._track_y(row) + 20
        view = timeline.mapFromScene(QPoint(int(x), int(y)))
        event = QDropEvent(view, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        timeline.dropEvent(event)
        return event

    @staticmethod
    def _mime_of(grid, row: int):
        return grid.mimeData([grid.item(row)])

    def test_grid_carries_a_library_payload(self):
        from freecut.qtui.library import LIBRARY_MIME

        win, _seg = self.window_with_clip()
        try:
            win.bin.show_category("特效")
            self.app.processEvents()
            grid = win.bin.effect_list
            self.assertTrue(grid.dragEnabled(), "特效网格不能拖")
            mime = self._mime_of(grid, 0)
            self.assertTrue(mime.hasFormat(LIBRARY_MIME), "拖出来的数据里没有素材库格式")
            payload = win.timeline.library_payload(mime)
            self.assertEqual(payload["kind"], "effect")
            self.assertTrue(payload["value"])
        finally:
            win.close()

    def test_dropping_a_transition_applies_it_to_the_clip_under_the_cursor(self):
        """拖到哪个片段就套在哪个片段上——即使当前选中的是另一个。"""
        win, first = self.window_with_clip(duration_us=2_000_000)
        try:
            from freecut import builder

            second = builder.add_video(win.bridge.project, demo_media(),
                                       start=2_000_000, duration=2_000_000)
            win.bridge.projectReloaded.emit()
            win._refresh_all()
            self.app.processEvents()
            win.bridge.select(first)                     # 故意选中"不是目标"的那个
            win.bin.show_category("转场")
            self.app.processEvents()
            grid = win.bin.transition_list
            self._drop_on(win.timeline, second, self._mime_of(grid, 0))
            self.app.processEvents()
            self.assertTrue(win.timeline._has_transition(second),
                            "拖到片段上的转场没有生效")
            self.assertFalse(win.timeline._has_transition(first),
                             "转场套到了错误（选中）的片段上")
        finally:
            win.close()

    def test_dropping_an_effect_applies_it(self):
        win, seg = self.window_with_clip()
        try:
            win.bin.show_category("特效")
            self.app.processEvents()
            grid = win.bin.effect_list
            before = len(win.bridge.project.refs_of(seg))
            self._drop_on(win.timeline, seg, self._mime_of(grid, 2))
            self.app.processEvents()
            refs = [r for r in win.bridge.project.refs_of(seg)
                    if getattr(r, "type", "") == "effect"]
            self.assertTrue(refs, "拖到片段上的特效没有生效")
            self.assertGreater(len(win.bridge.project.refs_of(seg)), before)
        finally:
            win.close()

    def test_dropping_on_empty_space_explains_where_to_drop(self):
        win, _seg = self.window_with_clip()
        messages = []
        win.bridge.statusMessage.connect(messages.append)
        try:
            from PySide6.QtCore import QPoint, Qt
            from PySide6.QtGui import QDropEvent

            win.bin.show_category("特效")
            self.app.processEvents()
            mime = self._mime_of(win.bin.effect_list, 0)
            # 时间线下方空白处（没有片段的行）
            timeline = win.timeline
            event = QDropEvent(QPoint(60, timeline.viewport().height() - 6),
                               Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            timeline.dropEvent(event)
            self.app.processEvents()
            self.assertTrue(any("拖到某个片段上" in m for m in messages),
                            f"拖到空白处没有任何解释：{messages[-2:]}")
        finally:
            win.close()

    def test_drag_over_highlights_the_target_clip(self):
        win, seg = self.window_with_clip()
        try:
            from PySide6.QtCore import QPoint, Qt
            from PySide6.QtGui import QDragEnterEvent

            win.bin.show_category("特效")
            self.app.processEvents()
            mime = self._mime_of(win.bin.effect_list, 0)
            timeline = win.timeline
            row = timeline.rows().index(next(t for t in timeline.rows() if seg in t.segments))
            x = timeline._us_to_x(int(seg.target_timerange.start) + 200_000)
            y = timeline._track_y(row) + 20
            event = QDragEnterEvent(timeline.mapFromScene(QPoint(int(x), int(y))),
                                    Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            timeline.dragEnterEvent(event)
            self.assertIs(timeline._drop_clip, seg, "拖到片段上时没有高亮目标")
            from PySide6.QtGui import QDragLeaveEvent

            timeline.dragLeaveEvent(QDragLeaveEvent())
            self.assertIsNone(timeline._drop_clip, "离开后高亮没清掉")
        finally:
            win.close()


# --------------------------------------------------------------------------- #
# 第 245 轮：多轨——"只能有一条视频轨、一条音频轨吗？"
# --------------------------------------------------------------------------- #
class TestMultiTrack(_WindowCase):
    @staticmethod
    def _file_mime(path):
        from PySide6.QtCore import QMimeData, QUrl

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        return mime

    def _drop_at_row(self, timeline, row: int, path: str, x: int = 200):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QDropEvent

        y = timeline._track_y(row) + 20
        mime = self._file_mime(path)      # 必须留住引用：QDropEvent 只存裸指针
        event = QDropEvent(QPoint(int(x), int(y)), Qt.CopyAction,
                           mime, Qt.LeftButton, Qt.NoModifier)
        timeline.dropEvent(event)
        return event

    def test_add_track_button_creates_a_second_video_track(self):
        win, _seg = self.window_with_clip()
        try:
            before = len([t for t in win.bridge.project.tracks if t.type == "video"])
            win.track_buttons["video"].click()
            self.app.processEvents()
            after = len([t for t in win.bridge.project.tracks if t.type == "video"])
            self.assertEqual(after, before + 1, "点「+ 视频轨」没有新增轨道")
            self.assertIn("视频 2", win.track_count_label.text())
        finally:
            win.close()

    def test_dropping_on_the_second_video_row_lands_there(self):
        media = demo_media()
        if not media:
            self.skipTest("没有测试素材")
        win, first = self.window_with_clip(duration_us=2_000_000)
        try:
            win.track_buttons["video"].click()
            self.app.processEvents()
            timeline = win.timeline
            rows = timeline.rows()
            videos = [i for i, t in enumerate(rows) if t.type == "video"]
            self.assertGreaterEqual(len(videos), 2, "没有出现第二条视频轨")
            target_row = videos[1]
            self._drop_at_row(timeline, target_row, media, x=600)
            self.app.processEvents()
            tracks = [t for t in win.bridge.project.tracks if t.type == "video"]
            landed = [t for t in tracks if any(s is not first for s in t.segments)]
            self.assertTrue(landed, "拖到第二行之后素材没进任何轨道")
            self.assertTrue(any(s in tracks[1].segments for s in landed[0].segments),
                            "素材落在第一条轨道上了（拖到第几行就应当放第几条）")
        finally:
            win.close()

    def test_dropping_below_the_last_row_creates_a_track(self):
        media = demo_media()
        if not media:
            self.skipTest("没有测试素材")
        win, _seg = self.window_with_clip(duration_us=2_000_000)
        try:
            timeline = win.timeline
            before = len([t for t in win.bridge.project.tracks if t.type == "video"])
            # 拖到所有行下面的空白处
            y = timeline.viewport().height() - 6
            from PySide6.QtCore import QPoint, Qt
            from PySide6.QtGui import QDropEvent

            mime = self._file_mime(media)
            timeline.dropEvent(QDropEvent(QPoint(300, int(y)), Qt.CopyAction,
                                          mime, Qt.LeftButton, Qt.NoModifier))
            self.app.processEvents()
            after = len([t for t in win.bridge.project.tracks if t.type == "video"])
            self.assertEqual(after, before + 1, "拖到空白处没有自动新建轨道")
        finally:
            win.close()

    def test_audio_dropped_on_a_video_row_goes_to_the_audio_track(self):
        media = demo_media()
        if not media:
            self.skipTest("没有测试素材")
        audio = os.path.join(ROOT, "out", "asr_test.wav")
        if not os.path.isfile(audio):
            self.skipTest("没有音频素材")
        win, _seg = self.window_with_clip(duration_us=2_000_000)
        try:
            timeline = win.timeline
            rows = timeline.rows()
            video_row = next(i for i, t in enumerate(rows) if t.type == "video")
            audio_before = [t for t in win.bridge.project.tracks if t.type == "audio"]
            self._drop_at_row(timeline, video_row, audio, x=700)
            self.app.processEvents()
            audio_after = [t for t in win.bridge.project.tracks if t.type == "audio"]
            self.assertTrue(audio_after, "音频没有落到音频轨")
            if audio_before:
                self.assertTrue(any(s for s in audio_after[0].segments[1:]) or
                                len(audio_after[0].segments) > len(audio_before[0].segments),
                                "拖到视频行的音频没有进音频轨")
        finally:
            win.close()

    def test_every_track_gets_a_row(self):
        win, _seg = self.window_with_clip()
        try:
            win.track_buttons["video"].click()
            win.track_buttons["text"].click()
            self.app.processEvents()
            rows = win.timeline.rows()
            self.assertEqual(len(rows), len(win.bridge.project.tracks),
                             "有轨道没画出来")
            self.assertGreaterEqual(len(rows), 3)
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
"""画面 · 基础里的「混合模式」：用真实窗口模拟用户改下拉框。

覆盖四件事：
1. 下拉框列出「正常」以及 ``BLEND_NAME_TO_ID`` 里的全部标签；
2. 选「滤色」会真的写进项目，状态栏也有反馈；
3. 重新选中带混合模式的片段时，下拉框显示它当前的模式；没选中时灰显；
4. 选回「正常」会把 mix_mode 材质删掉。
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


class TestInspectorBlendMode(unittest.TestCase):
    """起一个真窗口 + 一个 4 秒视频片段，全部走控件操作。"""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:                       # noqa: BLE001
            raise unittest.SkipTest(f"没有 PySide6: {exc}")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        media = os.path.join(ROOT, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("没有测试素材 out/demo.mp4")
        self.win = MainWindow(None)
        self.win.resize(1280, 820)
        self.win.show()
        self.app.processEvents()

        project = builder.new_project(640, 360, 30, name="混合")
        self.seg = builder.add_video(project, media, start=0, duration=4_000_000)
        self.win.bridge.set_project(project, None)
        self.win._refresh_all()
        self.app.processEvents()
        self.win.bridge.select(self.seg)
        self.app.processEvents()

        self.combo = self.win.inspector.blend_combo

    def tearDown(self):
        self.win.close()
        self.app.processEvents()

    def _choose(self, label: str) -> None:
        index = self.combo.findText(label)
        self.assertGreaterEqual(index, 0, f"下拉框里没有「{label}」")
        self.combo.setCurrentIndex(index)
        self.app.processEvents()

    def _mix_mode_refs(self):
        return [ref for ref in self.win.bridge.project.refs_of(self.seg)
                if getattr(ref, "type", "") == "mix_mode"]

    def test_combo_lists_normal_first_and_all_labels(self):
        from freecut.color import BLEND_NAME_TO_ID

        labels = [self.combo.itemText(i) for i in range(self.combo.count())]
        self.assertEqual(labels[0], "正常", "「正常」应当排在下拉框第一项")
        self.assertEqual(len(labels), len(set(labels)), "下拉框里有重复项")
        for label in BLEND_NAME_TO_ID:
            self.assertIn(label, labels, f"下拉框缺少「{label}」")
        self.assertGreaterEqual(len(labels) - 1, 10, "除「正常」外至少要有 10 个模式")

    def test_choosing_filter_really_writes_mix_mode_and_status(self):
        from freecut.color import BLEND_NAME_TO_ID

        messages = []
        self.win.bridge.statusMessage.connect(messages.append)
        self._choose("滤色")

        refs = self._mix_mode_refs()
        self.assertEqual(len(refs), 1, "选「滤色」后应当恰好有一条 mix_mode 材质")
        ref = refs[0]
        self.assertEqual(str(ref.get("name")), "滤色")
        # 渲染器使用的真实映射是 screen；不是 "filter"。
        self.assertEqual(ref.name_id, BLEND_NAME_TO_ID["滤色"])
        self.assertEqual(ref.name_id, "screen")
        self.assertIn("混合模式：滤色", messages, f"状态栏没有反馈：{messages[-3:]}")

    def test_reselect_shows_current_mode_and_no_selection_disables(self):
        self._choose("滤色")

        self.win.bridge.select(None)
        self.app.processEvents()
        self.assertFalse(self.combo.isEnabled(), "没选中片段时混合模式应当灰显")

        self.win.bridge.select(self.seg)
        self.app.processEvents()
        self.assertTrue(self.combo.isEnabled(), "重新选中片段后混合模式应当可用")
        self.assertEqual(self.combo.currentText(), "滤色",
                         "重新选中带「滤色」的片段后下拉框没有显示当前模式")

    def test_choosing_normal_removes_the_mix_mode(self):
        self._choose("滤色")
        self.assertEqual(len(self._mix_mode_refs()), 1)

        self._choose("正常")
        self.assertEqual(self._mix_mode_refs(), [],
                         "选回「正常」后 mix_mode 材质还留着")
        self.assertEqual(self.combo.currentText(), "正常")


if __name__ == "__main__":
    unittest.main(verbosity=2)

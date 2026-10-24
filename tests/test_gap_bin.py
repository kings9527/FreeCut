"""第 246 轮：素材页补三件剪映有、我们没有的小事。

1. ``test_media_on_the_timeline_gets_the_added_badge``
   已经在时间线上的素材，条目文字后面要跟「· 已添加」（刷新不能叠两遍）
2. ``test_media_not_on_the_timeline_has_no_badge``
   只导入到素材库、没上时间线的素材不能带角标
3. ``test_view_toggle_switches_list_and_grid``
   右上角那颗小按钮在 列表 / 网格 之间切换
4. ``test_added_filter_hides_and_shows_items``
   「全部 / 仅显示未添加 / 仅显示已添加」要真的隐藏行，并且和搜索叠加

全部用例都是"起窗口 → 造真实工程 → 断言界面状态"，不碰命令行。
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


class TestBinGap(unittest.TestCase):
    """素材页：已添加角标 / 列表-网格切换 / 已添加筛选。"""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:                       # noqa: BLE001
            raise unittest.SkipTest(f"没有 PySide6: {exc}")
        cls.app = QApplication.instance() or QApplication([])

    # -- 公共底座 ---------------------------------------------------------- #
    def _window_with_media(self):
        """一个 2 秒的视频片段在时间线上 + 一个只进素材库的视频。"""
        from freecut import builder
        from freecut.model import VideoMaterial
        from freecut.qtui.app import MainWindow

        media = os.path.join(ROOT, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("没有测试素材 out/demo.mp4")
        other = os.path.join(ROOT, "out", "showcase.mp4")
        if not os.path.isfile(other):
            other = os.path.join(ROOT, "out", "demo_audio.mp4")
        win = MainWindow(None)
        win.resize(1280, 820)
        win.show()
        self.app.processEvents()
        project = builder.new_project(640, 360, 30)
        builder.add_video(project, media, start=0, duration=2_000_000)
        # 第二个素材只进素材库、不上时间线（直接建材质，测试只关心角标）
        project.add_material(VideoMaterial(
            path=os.path.abspath(other), duration=4_000_000,
            width=1280, height=720, material_name=os.path.basename(other)))
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        self.addCleanup(win.close)
        return win, other

    def _items(self):
        lst = self.win.bin.media_list
        return [lst.item(i) for i in range(lst.count())]

    def _item_for(self, name: str):
        for item in self._items():
            if name in item.text():
                return item
        self.fail(f"素材列表里没有 {name}：{[it.text() for it in self._items()]}")

    def setUp(self):
        self.win, self.other = self._window_with_media()
        self.added_name = "demo.mp4"
        self.unadded_name = os.path.basename(self.other)

    # -- 1. 已添加角标 ------------------------------------------------------ #
    def test_media_on_the_timeline_gets_the_added_badge(self):
        from PySide6.QtCore import Qt

        item = self._item_for(self.added_name)
        self.assertIn("已添加", item.text(),
                      f"上过时间线的素材没有「已添加」角标：{item.text()!r}")
        base = item.data(Qt.UserRole + 1)
        self.assertTrue(base, "条目没有把原始文字存进 UserRole + 1")
        self.assertNotIn("已添加", str(base),
                         "UserRole + 1 里应当是去掉角标的原始文字")
        # 再刷新一次不能叠成「 · 已添加 · 已添加」
        self.win.bin.refresh_media()
        self.app.processEvents()
        self.assertEqual(self._item_for(self.added_name).text().count("已添加"), 1,
                         "刷新之后角标叠了两遍")

    # -- 2. 没上时间线的不带角标 -------------------------------------------- #
    def test_media_not_on_the_timeline_has_no_badge(self):
        item = self._item_for(self.unadded_name)
        self.assertNotIn("已添加", item.text(),
                         f"没上时间线的素材被标成了已添加：{item.text()!r}")

    # -- 3. 列表 / 网格切换 -------------------------------------------------- #
    def test_view_toggle_switches_list_and_grid(self):
        from PySide6.QtWidgets import QListView

        bin_ = self.win.bin
        lst = bin_.media_list
        self.assertEqual(lst.viewMode(), QListView.ListMode, "默认应当是列表模式")
        self.assertEqual(bin_.view_btn.toolTip(), "网格视图")

        bin_.view_btn.click()                          # 用户点一下小按钮
        self.app.processEvents()
        self.assertEqual(lst.viewMode(), QListView.IconMode, "点一下没有切到网格")
        self.assertEqual((lst.iconSize().width(), lst.iconSize().height()), (64, 36))
        self.assertEqual((lst.gridSize().width(), lst.gridSize().height()), (150, 78))
        self.assertEqual(bin_.view_btn.toolTip(), "列表视图")

        bin_.view_btn.click()
        self.app.processEvents()
        self.assertEqual(lst.viewMode(), QListView.ListMode, "再点一下没有切回列表")
        self.assertEqual((lst.iconSize().width(), lst.iconSize().height()), (32, 18))

    # -- 4. 已添加筛选（并且和搜索叠加）------------------------------------- #
    def test_added_filter_hides_and_shows_items(self):
        bin_ = self.win.bin
        added = self._item_for(self.added_name)
        unadded = self._item_for(self.unadded_name)

        bin_.added_box.setCurrentIndex(bin_.added_box.findText("仅显示未添加"))
        self.app.processEvents()
        self.assertTrue(added.isHidden(), "「仅显示未添加」里还能看到已添加的素材")
        self.assertFalse(unadded.isHidden(), "「仅显示未添加」把没添加的也藏了")

        bin_.added_box.setCurrentIndex(bin_.added_box.findText("仅显示已添加"))
        self.app.processEvents()
        self.assertFalse(added.isHidden(), "「仅显示已添加」里看不到已添加的素材")
        self.assertTrue(unadded.isHidden(), "「仅显示已添加」里混进了没添加的素材")

        bin_.added_box.setCurrentIndex(bin_.added_box.findText("全部"))
        self.app.processEvents()
        self.assertFalse(added.isHidden(), "切回「全部」后已添加的素材还藏着")
        self.assertFalse(unadded.isHidden(), "切回「全部」后没添加的素材还藏着")

        # 和搜索叠加：搜 showcase 时 demo 条目根本不该存在，
        # 剩下的 showcase 条目在「仅显示已添加」下应当被隐藏
        bin_.added_box.setCurrentIndex(bin_.added_box.findText("仅显示已添加"))
        bin_.search_edit.setText(self.unadded_name)
        self.app.processEvents()
        self.assertFalse(any(self.added_name in it.text() for it in self._items()),
                         "搜索没有生效：还留着不匹配的条目")
        self.assertTrue(all(it.isHidden() for it in self._items()),
                        "搜索 + 已添加筛选没有一起生效")


if __name__ == "__main__":
    unittest.main(verbosity=2)

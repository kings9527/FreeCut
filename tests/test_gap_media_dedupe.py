"""第 13 轮：素材去重（用户实测：同一个文件导入两次会出现两条）。

剪映在草稿保存层用 asyncDedup 去重（见 docs/measurements/PLAYBACK-AUDIO-RE.md §4）；
我们在导入入口就挡住：按 normcase(abspath) 认同一个文件。
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


class TestMediaDedupe(unittest.TestCase):
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
        win.resize(1200, 800)
        win.show()
        self.app.processEvents()
        win.bridge.set_project(builder.new_project(640, 360, 30, name="去重"), None)
        win._refresh_all()
        self.app.processEvents()
        return win, media

    def test_importing_the_same_file_twice_adds_one_material(self):
        win, media = self.window()
        try:
            before = len(win.bridge.project.materials)
            first = win.bin._add_material_only(media)
            second = win.bin._add_material_only(media)
            self.app.processEvents()
            self.assertEqual(first, "added")
            self.assertEqual(second, "exists", "第二次导入应当识别为已存在")
            self.assertEqual(len(win.bridge.project.materials), before + 1,
                             "同一个文件被建成了两份材质")
            win.bin.refresh_media()
            self.assertEqual(win.bin.media_list.count(), 1, "素材列表出现了重复行")
        finally:
            win.close()

    def test_case_and_relative_path_still_dedupe(self):
        win, media = self.window()
        try:
            win.bin._add_material_only(media)
            upper = media.upper()
            result = win.bin._add_material_only(upper)
            self.assertEqual(result, "exists", f"大小写不同但同一文件没认出来：{upper}")
        finally:
            win.close()

    def test_different_files_are_both_added(self):
        win, media = self.window()
        other = os.path.join(ROOT, "out", "showcase.mp4")
        if not os.path.isfile(other):
            self.skipTest("没有第二个测试素材")
        try:
            self.assertEqual(win.bin._add_material_only(media), "added")
            self.assertEqual(win.bin._add_material_only(other), "added")
            win.bin.refresh_media()
            self.assertEqual(win.bin.media_list.count(), 2)
        finally:
            win.close()

    def test_two_imports_on_the_timeline_reuse_the_material(self):
        """同一个素材放上时间线两次，仍然只有一份材质。"""
        win, media = self.window()
        try:
            win.bin._add_material_only(media)
            self.app.processEvents()
            before = len(win.bridge.project.materials)
            win.bridge.add_media_to_timeline(media, "video", 0)
            win.bridge.add_media_to_timeline(media, "video", 2_000_000)
            self.app.processEvents()
            self.assertEqual(len(win.bridge.project.materials), before,
                             "放到时间线时又建了新材质")
            clips = [s for t in win.bridge.project.tracks for s in t.segments]
            self.assertEqual(len(clips), 2)
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""重新链接缺失素材：能找到缺失项、能按新路径接回。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

IMAGE = os.path.join(ROOT, "out", "chroma_src.png")


class TestRelink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        self.addCleanup(lambda: (setattr(win.bridge, "dirty", False), win.close()))
        return win

    def test_missing_materials_reports_the_gap(self):
        from freecut import builder

        win = self._window()
        builder.add_image(win.bridge.project, "X:/nope/gone.png",
                          start=0, duration=1_000_000)
        missing = win.missing_materials()
        self.assertEqual(len(missing), 1)
        self.assertIn("gone.png", missing[0][1])

    def test_relink_points_at_the_new_file(self):
        from freecut import builder

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        win = self._window()
        builder.add_image(win.bridge.project, "X:/nope/gone.png",
                          start=0, duration=1_000_000)
        mat, _old = win.missing_materials()[0]

        folder = tempfile.mkdtemp(prefix="relink-")
        new_path = os.path.join(folder, "gone.png")
        shutil.copy(IMAGE, new_path)

        self.assertTrue(win.relink_material(mat, new_path))
        self.assertEqual(win.missing_materials(), [])

    def test_relink_rejects_a_missing_target(self):
        from freecut import builder

        win = self._window()
        builder.add_image(win.bridge.project, "X:/nope/gone.png",
                          start=0, duration=1_000_000)
        mat, _old = win.missing_materials()[0]
        self.assertFalse(win.relink_material(mat, "X:/still/nope.png"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""素材库右键：定位文件、移除素材（含时间线片段）。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestMediaContextMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_reveal_uses_explorer(self):
        import subprocess as sp

        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        image = os.path.join(ROOT, "out", "chroma_src.png")
        if not os.path.isfile(image):
            self.skipTest("没有测试图片")
        project = builder.new_project(320, 180, 30, name="右键")
        builder.add_image(project, image, start=0, duration=1_000_000)
        panel = BinPanel(Bridge(project))
        self.addCleanup(panel.close)

        calls = []
        original = sp.Popen
        sp.Popen = lambda args, *a, **k: calls.append(list(args))
        try:
            panel._reveal_file(image)
        finally:
            sp.Popen = original
        self.assertTrue(calls)
        self.assertIn("chroma_src.png", " ".join(calls[0]))

    def test_remove_media_drops_segments_and_material(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        image = os.path.join(ROOT, "out", "chroma_src.png")
        if not os.path.isfile(image):
            self.skipTest("没有测试图片")
        project = builder.new_project(320, 180, 30, name="移除")
        builder.add_image(project, image, start=0, duration=1_000_000)
        panel = BinPanel(Bridge(project))
        self.addCleanup(panel.close)

        before = len([s for t in project.tracks for s in t.segments])
        panel._remove_media(image)
        after = len([s for t in project.tracks for s in t.segments])
        self.assertLess(after, before, "时间线片段没有被移除")
        paths = [str(getattr(m, "path", "") or "") for m in project.materials.values()]
        self.assertNotIn(image, paths, "素材还留在工程里")


if __name__ == "__main__":
    unittest.main(verbosity=2)

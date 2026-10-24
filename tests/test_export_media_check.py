"""导出前校验：素材缺失要报得清楚，而不是让 ffmpeg 深处失败。"""

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

IMAGE = os.path.join(ROOT, "out", "chroma_src.png")


class TestExportMediaCheck(unittest.TestCase):
    def test_present_media_passes(self):
        from freecut import builder
        from freecut.export import check_exportable, missing_media

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        project = builder.new_project(320, 180, 30, name="ok")
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        self.assertEqual(missing_media(project), [])
        check_exportable(project)                     # 不抛

    def test_missing_media_is_reported_with_filename(self):
        from freecut import builder
        from freecut.export import ExportError, check_exportable, missing_media

        project = builder.new_project(320, 180, 30, name="bad")
        builder.add_image(project, os.path.join(ROOT, "out", "nope_404.png"),
                          start=0, duration=1_000_000)
        missing = missing_media(project)
        self.assertEqual(len(missing), 1)
        with self.assertRaises(ExportError) as ctx:
            check_exportable(project)
        self.assertIn("nope_404.png", str(ctx.exception))

if __name__ == "__main__":
    unittest.main(verbosity=2)

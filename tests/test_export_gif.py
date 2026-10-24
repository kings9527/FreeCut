"""GIF 导出：文件能生成、尺寸与帧率符合参数。"""

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


class TestGifExport(unittest.TestCase):
    def setUp(self):
        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        from freecut.audio_loudness import ffmpeg_path

        if not ffmpeg_path():
            self.skipTest("没有 ffmpeg")

    def test_exports_gif_with_requested_size(self):
        from freecut import builder
        from freecut.export import export_gif

        project = builder.new_project(320, 180, 30, name="gif")
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        out = os.path.join(tempfile.mkdtemp(prefix="gif-"), "out.gif")
        export_gif(project, out, width=160, fps=10)

        self.assertTrue(os.path.isfile(out))
        self.assertGreater(os.path.getsize(out), 100)
        try:
            from PIL import Image
        except Exception:                              # noqa: BLE001
            return
        with Image.open(out) as gif:
            self.assertEqual(gif.size, (160, 90))
            self.assertGreaterEqual(getattr(gif, "n_frames", 1), 3)

    def test_empty_project_raises(self):
        from freecut import builder
        from freecut.export import ExportError, export_gif

        project = builder.new_project(320, 180, 30, name="空")
        out = os.path.join(tempfile.mkdtemp(prefix="gif2-"), "out.gif")
        with self.assertRaises(ExportError):
            export_gif(project, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""第 23 轮：导出选编码器时先查本机能力（对应剪映 ve_hw_check 的"先探测再选路"）。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestExportCodecValidation(unittest.TestCase):
    def test_auto_returns_an_available_encoder(self):
        from freecut import export
        from freecut.media import list_encoders

        chosen = export.pick_video_codec("auto")
        self.assertIn(chosen, set(list_encoders()),
                      f"auto 选了本机没有的编码器 {chosen}")

    def test_available_hardware_encoder_is_accepted(self):
        from freecut import export
        from freecut.media import list_encoders

        available = set(list_encoders())
        for name in ("h264_nvenc", "h264_qsv", "h264_mf", "h264_amf"):
            if name in available:
                self.assertEqual(export.pick_video_codec(name), name)
                return
        self.skipTest("本机没有硬件编码器")

    def test_missing_hardware_encoder_gives_a_clear_error(self):
        from freecut import export

        original = export.list_encoders
        export.list_encoders = lambda: ["mpeg4"]      # 假装没有硬件编码器
        try:
            with self.assertRaises(ValueError) as ctx:
                export.pick_video_codec("h264_nvenc")
            self.assertIn("h264_nvenc", str(ctx.exception))
            self.assertIn("libx264", str(ctx.exception), "错误信息里应当给出可用的退路")
        finally:
            export.list_encoders = original

    def test_software_codec_is_not_blocked(self):
        from freecut import export

        self.assertEqual(export.pick_video_codec("mpeg4"), "mpeg4")


if __name__ == "__main__":
    unittest.main(verbosity=2)

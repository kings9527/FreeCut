"""第 22 轮：硬件能力探测（对应剪映的 ve_hw_check.ini）。

剪映启动时探测每块 GPU 的 D3D11 硬解矩阵并缓存（证据：
`docs/measurements/ENGINE-ROUTE-CONFIG.md` §2）。我们的解码/编码走 FFmpeg，
对应物就是 `freecut.caps`：可用的 hwaccel / 编码器 / 推荐 H.264 编码器。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestCaps(unittest.TestCase):
    def test_probe_reports_ffmpeg_and_encoders(self):
        from freecut import caps

        data = caps.probe()
        self.assertTrue(data["ffmpeg"], "没拿到 ffmpeg 路径")
        self.assertIsInstance(data["hwaccels"], list)
        self.assertIn("libx264", data["encoders"],
                      "探测结果里应当列出 libx264（软编兜底）")
        self.assertTrue(data["preferred_h264_encoder"],
                        "没有给出默认 H.264 编码器")

    def test_preferred_encoder_is_actually_available(self):
        from freecut import caps

        data = caps.probe()
        name = data["preferred_h264_encoder"]
        encoders = caps.encoders()
        if name != "libx264":
            self.assertIn(name, encoders, f"选了不可用的编码器 {name}")
        self.assertIn(name, ("libx264", "h264_nvenc", "h264_qsv", "h264_mf", "h264_amf"))

    def test_save_and_load_round_trip(self):
        from freecut import caps

        data = caps.probe()
        tmp = tempfile.mkdtemp(prefix="freecut-caps-")
        path = os.path.join(tmp, "caps.json")
        caps.save(path, data)
        loaded = caps.load(path)
        self.assertEqual(loaded["preferred_h264_encoder"],
                         data["preferred_h264_encoder"])
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertIn("hwaccels", raw)

    def test_summary_is_one_line(self):
        from freecut import caps

        line = caps.summary()
        self.assertIn("hwaccels=", line)
        self.assertNotIn("\n", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""第 15 轮：常驻解码器（PyAV）——治"往回拖卡顿"。

实测对比（同一台机器、1080p、换位置取一帧）：
- ffmpeg 子进程：218–280 ms（进程 35 ms + 解码器初始化 ~170 ms，与跳多远无关）
- PyAV 常驻：13–30 ms

所以 `FrameReader` 改成门面：有 av 就用常驻解码器，没有就退回子进程。
"""

from __future__ import annotations

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

MEDIA = os.path.join(ROOT, "out", "demo.mp4")


class TestAvReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(MEDIA):
            raise unittest.SkipTest("没有测试素材")

    def test_frame_reader_prefers_the_persistent_decoder(self):
        from freecut.av_reader import av_available
        from freecut.media import FrameReader

        reader = FrameReader(MEDIA, width=320, height=180)
        try:
            if av_available():
                self.assertEqual(type(reader).__name__, "AvFrameReader",
                                 "有 av 的时候应当用常驻解码器")
            self.assertEqual((reader.out_w, reader.out_h), (320, 180))
        finally:
            reader.close()

    def test_seek_latency_is_well_under_a_frame_budget(self):
        from freecut.media import FrameReader

        reader = FrameReader(MEDIA, width=320, height=180)
        try:
            reader.frame_at(0)                          # 预热（打开+索引只做一次）
            targets = [int(i * 350_000) for i in range(12)]
            times = []
            for target in targets:
                start = time.perf_counter()
                reader.frame_at(target)
                times.append((time.perf_counter() - start) * 1000)
            times.sort()
            p95 = times[int(len(times) * 0.95) - 1]
            self.assertLess(p95, 120,
                            f"换位置取帧太慢：p50={times[len(times) // 2]:.0f}ms "
                            f"p95={p95:.0f}ms")
        finally:
            reader.close()

    def test_same_timestamp_returns_identical_frame(self):
        from freecut.media import FrameReader

        reader = FrameReader(MEDIA, width=320, height=180)
        try:
            first = reader.frame_at(1_000_000).copy()
            again = reader.frame_at(1_000_000)
            self.assertEqual(first.tobytes(), again.tobytes())
            self.assertEqual(first.shape, (180, 320, 3))
        finally:
            reader.close()

    def test_fallback_to_the_ffmpeg_subprocess_still_works(self):
        """没有 av 的机器要能退回子进程实现（打包/老环境）。"""
        import freecut.media as media_mod

        original = media_mod.FrameReader

        class Forced(original):
            def __new__(cls, *args, **kwargs):
                return media_mod.FfmpegFrameReader(*args, **kwargs)

        try:
            media_mod.FrameReader = Forced
            reader = media_mod.FrameReader(MEDIA, width=160, height=90)
            self.assertEqual(type(reader).__name__, "FfmpegFrameReader")
            frame = reader.frame_at(500_000)
            self.assertEqual(frame.shape, (90, 160, 3))
            reader.close()
        finally:
            media_mod.FrameReader = original

    def test_av_and_subprocess_frames_agree(self):
        """两条实现取同一时刻，画面应当一致（允许缩放插值的小差异）。"""
        import numpy as np

        from freecut.av_reader import av_available
        from freecut.media import FrameReader, FfmpegFrameReader

        if not av_available():
            self.skipTest("没有 av")
        fast = FfmpegFrameReader(MEDIA, width=160, height=90)
        slow = FrameReader(MEDIA, width=160, height=90)
        try:
            for us in (1_000_000, 2_000_000, 4_400_000):
                a = slow.frame_at(us).astype(np.int16)
                b = fast.frame_at(us).astype(np.int16)
                diff = np.abs(a - b).mean()
                self.assertLess(diff, 12.0,
                                f"{us}us 处两条实现的画面差太多：{diff:.1f}")
        finally:
            fast.close()
            slow.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

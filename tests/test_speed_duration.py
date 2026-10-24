"""变速端到端：同样的素材，speed=2 时片段时长应当约为一半。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

MEDIA = os.path.join(ROOT, "out", "demo.mp4")


class TestSpeedChangesDuration(unittest.TestCase):
    def _duration(self, speed: float) -> int:
        from freecut import builder

        if not os.path.isfile(MEDIA):
            self.skipTest("没有测试视频")
        project = builder.new_project(320, 180, 30, name="变速")
        seg = builder.add_video(project, MEDIA, start=0, speed=speed)
        return int(seg.target_timerange.duration)

    def test_double_speed_halves_the_duration(self):
        normal = self._duration(1.0)
        fast = self._duration(2.0)
        self.assertGreater(normal, 0)
        ratio = fast / float(normal)
        self.assertAlmostEqual(ratio, 0.5, delta=0.05,
                               msg=f"2 倍速时长比 {ratio:.3f}，应当约 0.5")

    def test_half_speed_doubles_the_duration(self):
        normal = self._duration(1.0)
        slow = self._duration(0.5)
        ratio = slow / float(normal)
        self.assertAlmostEqual(ratio, 2.0, delta=0.1,
                               msg=f"0.5 倍速时长比 {ratio:.3f}，应当约 2.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)

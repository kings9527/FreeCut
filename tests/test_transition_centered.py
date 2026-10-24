"""转场以切点为中心：闪白在切点最白、叠化在切点一半一半、切点前也能看到。

这组测试锁的就是用户报的那个现象："时间轴停在接缝，画面里没有效果"。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CUT = 1_000_000
DUR = 400_000


def _solid(colour: str) -> str:
    from freecut.audio_loudness import ffmpeg_path

    exe = ffmpeg_path()
    if not exe:
        return ""
    path = os.path.join(tempfile.mkdtemp(prefix="ct-"), f"{colour}.png")
    try:
        subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c={colour}:s=160x90:d=1", "-frames:v", "1", path],
                       check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.CalledProcessError):
        return ""
    return path


def _project(kind: str):
    from freecut import builder

    red, blue = _solid("red"), _solid("blue")
    if not red:
        return None
    project = builder.new_project(160, 90, 30, name=kind)
    builder.add_image(project, red, start=0, duration=CUT)
    second = builder.add_image(project, blue, start=CUT, duration=CUT)
    builder.add_transition(project, second, kind, duration_us=DUR)
    return project


def _means(project, seconds):
    from freecut.render import Renderer

    renderer = Renderer(project, 160, 90, 30)
    try:
        frame = renderer.render_frame(int(round(seconds * 30)))
        return [float(frame[..., i].mean()) for i in range(3)]
    finally:
        renderer.close()


class TestCenteredTransition(unittest.TestCase):
    def test_fade_white_peaks_at_the_cut(self):
        project = _project("fade_white")
        if project is None:
            self.skipTest("没有 ffmpeg")
        at_cut = _means(project, 1.0)
        before = _means(project, 0.85)
        after = _means(project, 1.15)
        self.assertGreater(min(at_cut), 250.0, f"切点不是白的：{at_cut}")
        self.assertGreater(before[0], before[2], f"切点前应偏红（旧片）：{before}")
        self.assertGreater(after[2], after[0], f"切点后应偏蓝（新片）：{after}")

    def test_fade_black_peaks_at_the_cut(self):
        project = _project("fade_black")
        if project is None:
            self.skipTest("没有 ffmpeg")
        at_cut = _means(project, 1.0)
        self.assertLess(max(at_cut), 8.0, f"切点不是黑的：{at_cut}")

    def test_dissolve_is_half_and_half_at_the_cut(self):
        project = _project("cross_dissolve")
        if project is None:
            self.skipTest("没有 ffmpeg")
        at_cut = _means(project, 1.0)
        self.assertGreater(at_cut[0], 90.0, f"红色分量不足：{at_cut}")
        self.assertGreater(at_cut[2], 90.0, f"蓝色分量不足：{at_cut}")
        # 切点前一半已经能看到新片
        before = _means(project, 0.9)
        self.assertGreater(before[2], 20.0, f"切点前看不到新片：{before}")

    def test_clip_before_the_cut_can_see_the_effect(self):
        """站在"前一个片段"上（播放头在切点之前）也要能看到转场。"""
        project = _project("fade_white")
        if project is None:
            self.skipTest("没有 ffmpeg")
        before = _means(project, 0.9)
        plain = _means(project, 0.5)
        self.assertGreater(before[1], plain[1] + 20.0,
                           f"切点前没有变亮：before={before} plain={plain}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

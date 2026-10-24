"""响度统一：测量 + 应用。用 ffmpeg 合成测试音，不依赖外部素材。"""

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


def _tone(path: str, amplitude: float, seconds: float = 2.0) -> bool:
    from freecut.audio_loudness import ffmpeg_path

    exe = ffmpeg_path()
    if not exe:
        return False
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", f"sine=frequency=440:duration={seconds}",
           "-af", f"volume={amplitude}", "-ac", "2", path]
    try:
        subprocess.run(cmd, check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.CalledProcessError):
        return False
    return os.path.isfile(path)


class TestLoudness(unittest.TestCase):
    def test_measure_quiet_and_loud(self):
        from freecut.audio_loudness import measure_lufs

        tmp = tempfile.mkdtemp(prefix="lufs-")
        quiet = os.path.join(tmp, "quiet.wav")
        loud = os.path.join(tmp, "loud.wav")
        if not (_tone(quiet, 0.05) and _tone(loud, 0.5)):
            self.skipTest("没有可用的 ffmpeg")
        q = measure_lufs(quiet)
        l = measure_lufs(loud)
        self.assertIsNotNone(q)
        self.assertIsNotNone(l)
        self.assertGreater(l - q, 10.0, f"响度差太小：{q:.1f} vs {l:.1f} LUFS")

    def test_gain_is_clamped(self):
        from freecut.audio_loudness import gain_db

        self.assertAlmostEqual(gain_db(-14, -24), 10.0)
        self.assertAlmostEqual(gain_db(-14, -34, limit_db=12), 12.0)
        self.assertAlmostEqual(gain_db(-14, 0, limit_db=12), -12.0)

    def test_normalize_project_sets_volume(self):
        from freecut import builder
        from freecut.audio_loudness import measure_lufs, normalize_project

        tmp = tempfile.mkdtemp(prefix="lufs-proj-")
        quiet = os.path.join(tmp, "quiet.wav")
        if not _tone(quiet, 0.05):
            self.skipTest("没有可用的 ffmpeg")
        measured = measure_lufs(quiet)
        if measured is None:
            self.skipTest("测不出响度")

        project = builder.new_project(320, 180, 30, name="响度")
        seg = builder.add_audio(project, quiet, start=0, duration=1_000_000)
        before = float(seg.volume or 1.0)
        applied = normalize_project(project, target=-14.0)
        self.assertTrue(applied, "没有应用任何增益")
        after = float(seg.volume or 1.0)
        self.assertGreater(after, before, "安静素材应当被提高音量")
        from freecut.audio_loudness import gain_db
        expected = before * (10 ** (gain_db(-14.0, measured) / 20.0))
        self.assertAlmostEqual(after, expected, delta=abs(expected) * 0.05)


if __name__ == "__main__":
    unittest.main(verbosity=2)

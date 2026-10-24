"""导出要真的带声音：成片里必须有音频流，且时长接近工程时长。

预览出声由 audio-smoke 覆盖，这条管的是**最终文件**。
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

IMAGE = os.path.join(ROOT, "out", "chroma_src.png")


def _tone(path: str, seconds: float = 1.0) -> bool:
    from freecut.audio_loudness import ffmpeg_path

    exe = ffmpeg_path()
    if not exe:
        return False
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", f"sine=frequency=440:duration={seconds}", "-ac", "2", path]
    try:
        subprocess.run(cmd, check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.CalledProcessError):
        return False
    return os.path.isfile(path)


class TestExportHasAudio(unittest.TestCase):
    def test_exported_video_contains_audio(self):
        try:
            import av
        except Exception:                              # noqa: BLE001
            self.skipTest("没有 PyAV")

        from freecut import builder
        from freecut.export import export

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        tmp = tempfile.mkdtemp(prefix="freecut-av-")
        tone = os.path.join(tmp, "tone.wav")
        if not _tone(tone, 1.0):
            self.skipTest("没有可用的 ffmpeg")

        project = builder.new_project(320, 180, 30, name="带声音")
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        builder.add_audio(project, tone, start=0, duration=1_000_000)
        out = os.path.join(tmp, "with_audio.mp4")
        export(project, out)

        self.assertTrue(os.path.isfile(out) and os.path.getsize(out) > 0)
        container = av.open(out)
        try:
            self.assertEqual(len(container.streams.video), 1, "没有视频流")
            self.assertEqual(len(container.streams.audio), 1, "成片里没有音频流")
            audio = container.streams.audio[0]
            self.assertGreater(float(audio.duration or 0) * float(audio.time_base or 1),
                               0.5, "音频时长太短")
        finally:
            container.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

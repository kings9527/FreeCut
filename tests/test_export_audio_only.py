"""只导出音频：生成 m4a，时长与工程一致。"""

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


def _tone(path: str, seconds: float = 1.0) -> bool:
    from freecut.audio_loudness import ffmpeg_path

    exe = ffmpeg_path()
    if not exe:
        return False
    try:
        subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"sine=frequency=440:duration={seconds}", "-ac", "2", path],
                       check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.CalledProcessError):
        return False
    return os.path.isfile(path)


class TestAudioOnlyExport(unittest.TestCase):
    def test_exports_m4a_with_expected_duration(self):
        try:
            import av
        except Exception:                              # noqa: BLE001
            self.skipTest("没有 PyAV")

        from freecut import builder
        from freecut.export import export_audio_only

        tmp = tempfile.mkdtemp(prefix="freecut-aout-")
        tone = os.path.join(tmp, "tone.wav")
        if not _tone(tone, 1.0):
            self.skipTest("没有可用的 ffmpeg")

        project = builder.new_project(320, 180, 30, name="只导音频")
        builder.add_audio(project, tone, start=0, duration=1_000_000)
        out = os.path.join(tmp, "audio.m4a")
        export_audio_only(project, out)

        self.assertTrue(os.path.isfile(out) and os.path.getsize(out) > 0)
        container = av.open(out)
        try:
            self.assertEqual(len(container.streams.audio), 1)
            stream = container.streams.audio[0]
            duration = float(stream.duration or 0) * float(stream.time_base or 1)
            self.assertGreater(duration, 0.5, f"音频太短：{duration:.2f}s")
        finally:
            container.close()

    def test_project_without_audio_raises(self):
        from freecut import builder
        from freecut.export import ExportError, export_audio_only

        image = os.path.join(ROOT, "out", "chroma_src.png")
        if not os.path.isfile(image):
            self.skipTest("没有测试图片")
        project = builder.new_project(320, 180, 30, name="无音频")
        builder.add_image(project, image, start=0, duration=1_000_000)
        out = os.path.join(tempfile.mkdtemp(prefix="freecut-aout2-"), "audio.m4a")
        with self.assertRaises(ExportError):
            export_audio_only(project, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

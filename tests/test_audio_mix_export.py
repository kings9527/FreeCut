"""多音轨混音导出：两段不同频率的音频叠加后，成片里两个频率都要在。"""

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


def _tone(path: str, freq: int, seconds: float = 1.0) -> bool:
    from freecut.audio_loudness import ffmpeg_path

    exe = ffmpeg_path()
    if not exe:
        return False
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", f"sine=frequency={freq}:duration={seconds}", "-ac", "1", path]
    try:
        subprocess.run(cmd, check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.CalledProcessError):
        return False
    return os.path.isfile(path)


def _decode_audio(path: str):
    import av
    import numpy as np

    chunks = []
    container = av.open(path)
    try:
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            data = frame.to_ndarray()
            if data.ndim > 1:
                data = data.mean(axis=0)
            chunks.append(data.astype("float32"))
        rate = int(stream.rate or 44100)
    finally:
        container.close()
    if not chunks:
        return np.zeros(1, dtype="float32"), rate
    return np.concatenate(chunks), rate


class TestAudioMixExport(unittest.TestCase):
    def test_two_tracks_are_both_audible(self):
        try:
            import av  # noqa: F401
            import numpy as np
        except Exception:                              # noqa: BLE001
            self.skipTest("没有 PyAV / numpy")

        from freecut import builder
        from freecut.export import export

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        tmp = tempfile.mkdtemp(prefix="freecut-mix-")
        low = os.path.join(tmp, "low.wav")
        high = os.path.join(tmp, "high.wav")
        if not (_tone(low, 440) and _tone(high, 880)):
            self.skipTest("没有可用的 ffmpeg")

        project = builder.new_project(320, 180, 30, name="混音")
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        builder.add_audio(project, low, start=0, duration=1_000_000, track_index=0)
        builder.add_audio(project, high, start=0, duration=1_000_000, track_index=1)
        out = os.path.join(tmp, "mixed.mp4")
        export(project, out)

        samples, rate = _decode_audio(out)
        self.assertGreater(samples.size, rate // 2, "解出来的音频太短")

        spectrum = np.abs(np.fft.rfft(samples))
        freqs = np.fft.rfftfreq(samples.size, 1.0 / rate)
        def energy(target: float, width: float = 25.0) -> float:
            band = (freqs >= target - width) & (freqs <= target + width)
            return float(spectrum[band].max()) if band.any() else 0.0

        e440, e880 = energy(440), energy(880)
        peak = float(spectrum.max()) or 1.0
        self.assertGreater(e440 / peak, 0.05, "成片里几乎没有 440Hz")
        self.assertGreater(e880 / peak, 0.05, "成片里几乎没有 880Hz（第二轨被丢了？）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

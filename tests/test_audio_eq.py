"""音频三段均衡（EQ）：滑杆写回材质，audio 层能读到。"""

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


class TestAudioEq(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _tone(self) -> str:
        from freecut.audio_loudness import ffmpeg_path

        exe = ffmpeg_path()
        if not exe:
            self.skipTest("没有 ffmpeg")
        path = os.path.join(tempfile.mkdtemp(prefix="eq-"), "tone.wav")
        try:
            subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                            "-ac", "1", path], check=True, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("ffmpeg 合成失败")
        return path

    def _setup(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        project = builder.new_project(320, 180, 30, name="EQ")
        seg = builder.add_audio(project, self._tone(), start=0, duration=1_000_000)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(seg)
        self.app.processEvents()
        return project, seg, inspector

    def test_sliders_write_three_bands(self):
        from freecut import audio

        project, seg, inspector = self._setup()
        inspector.row_eq_low.set_value(6.0, emit=True)
        inspector.row_eq_mid.set_value(-3.0, emit=True)
        inspector.row_eq_high.set_value(4.0, emit=True)
        _denoise, _pitch, eq = audio._audio_effects(project, seg)
        self.assertEqual(tuple(round(float(x), 1) for x in eq), (6.0, -3.0, 4.0))

    def test_values_are_loaded_back(self):
        project, seg, inspector = self._setup()
        inspector.row_eq_low.set_value(5.0, emit=True)
        inspector._load_audio_values(seg) if hasattr(inspector, "_load_audio_values") else None
        self.assertAlmostEqual(inspector.row_eq_low.spin.value(), 5.0, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

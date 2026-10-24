"""文字朗读：本机 SAPI 合成音频并落到音频轨（离线，不联网）。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTextSpeak(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_speak_creates_audio_segment(self):
        from freecut import builder, tts
        from freecut.qtui.bridge import Bridge

        if not tts.voices():
            self.skipTest("本机没有 SAPI 声音")

        project = builder.new_project(320, 180, 30, name="朗读")
        seg = builder.add_text(project, "你好世界", start=0, duration=2_000_000)
        bridge = Bridge(project)
        new_seg = bridge.speak_text(seg)

        self.assertIsNotNone(new_seg, "没有生成音频片段")
        segments = [s for t in project.tracks for s in t.segments]
        self.assertEqual(len(segments), 2, "应当多出一条音频片段")
        audio_seg = [s for s in segments if s is not seg][0]
        self.assertGreater(int(audio_seg.target_timerange.duration), 100_000,
                           "朗读时长太短")

    def test_empty_text_is_rejected(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge

        project = builder.new_project(320, 180, 30, name="空文字")
        seg = builder.add_text(project, "   ", start=0, duration=1_000_000)
        bridge = Bridge(project)
        self.assertIsNone(bridge.speak_text(seg))


if __name__ == "__main__":
    unittest.main(verbosity=2)

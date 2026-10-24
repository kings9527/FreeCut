"""第 14 轮：预览音频（用户报的第 6 条"播放时一点声音都听不见"）。

结构照剪映的逆向结果：AudioBackend（换后端不改混音层）+ 混音 + 跟播放头走。
测试不出声：用 NullBackend 把 PCM 收进内存，断言"有声音/静音/音量/淡入"。
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

AUDIO = os.path.join(ROOT, "out", "asr_test.wav")


def rms(block) -> float:
    import numpy as np

    arr = np.asarray(block, dtype=np.float64)
    return float(np.sqrt((arr ** 2).mean())) if arr.size else 0.0


class TestMixer(unittest.TestCase):
    def setUp(self):
        from freecut import builder

        if not os.path.isfile(AUDIO):
            self.skipTest("没有测试音频")
        self.builder = builder
        self.project = builder.new_project(640, 360, 30, name="混音")
        self.seg = builder.add_audio(self.project, AUDIO, start=0, duration=2_000_000)

    def mixer(self):
        from freecut.audio_engine import Mixer

        return Mixer(self.project, 48000)

    def test_audio_inside_the_clip_and_silence_outside(self):
        mixer = self.mixer()
        inside = rms(mixer.mix(500_000, 2048))
        outside = rms(mixer.mix(2_500_000, 2048))
        self.assertGreater(inside, 50, f"片段内没有声音：rms={inside}")
        self.assertLess(outside, 1e-6, f"片段外不是静音：rms={outside}")

    def test_volume_scales_the_output(self):
        mixer = self.mixer()
        loud = rms(mixer.mix(500_000, 4096))
        self.seg.volume = 0.5
        quiet = rms(mixer.mix(500_000, 4096))
        self.assertAlmostEqual(quiet / max(1e-9, loud), 0.5, delta=0.08)

    def test_muted_track_is_silent(self):
        from freecut.audio_engine import Mixer

        mixer = Mixer(self.project, 48000)
        mixer.track_state = lambda track: True          # 全部轨道静音
        self.assertLess(rms(mixer.mix(500_000, 2048)), 1e-6)

    def test_fade_in_starts_quiet(self):
        builder = self.builder
        project = builder.new_project(640, 360, 30, name="淡入")
        builder.add_audio(project, AUDIO, start=0, duration=2_000_000,
                          fade_in_ms=1000)
        from freecut.audio_engine import Mixer

        mixer = Mixer(project, 48000)
        head = rms(mixer.mix(0, 2048))                  # 0 ms 处
        middle = rms(mixer.mix(1_500_000, 2048))        # 1.5 s 处（淡入已完成）
        self.assertLess(head, middle * 0.35, f"淡入没有生效：{head} vs {middle}")


class TestEngine(unittest.TestCase):
    def setUp(self):
        from freecut import builder

        if not os.path.isfile(AUDIO):
            self.skipTest("没有测试音频")
        self.project = builder.new_project(640, 360, 30, name="引擎")
        builder.add_audio(self.project, AUDIO, start=0, duration=2_000_000)

    def _bridge(self):
        from freecut.qtui.bridge import Bridge

        bridge = Bridge()
        bridge.set_project(self.project, None)
        return bridge

    def test_null_backend_receives_non_silent_pcm(self):
        from freecut.audio_engine import AudioEngine, NullBackend

        backend = NullBackend()
        engine = AudioEngine(self._bridge(), backend=backend)
        try:
            self.assertTrue(engine.start(500_000))
            for _ in range(4):
                backend.pump(2048)
            self.assertTrue(backend.started)
            self.assertGreater(max(rms(b) for b in backend.blocks), 50,
                               "引擎没有把声音送进后端")
        finally:
            engine.stop()

    def test_stop_is_silent_and_position_advances(self):
        from freecut.audio_engine import AudioEngine, NullBackend

        backend = NullBackend()
        engine = AudioEngine(self._bridge(), backend=backend)
        try:
            engine.start(0)
            backend.pump(4800)
            first = engine.position_us
            self.assertGreater(first, 0, "位置没有推进")
            engine.stop()
            backend.pump(1024)
            self.assertLess(rms(backend.blocks[-1]), 1e-6, "暂停后还在出声")
        finally:
            engine.stop()

    def test_seek_moves_the_read_position(self):
        from freecut.audio_engine import AudioEngine, NullBackend

        backend = NullBackend()
        engine = AudioEngine(self._bridge(), backend=backend)
        try:
            engine.start(0)
            engine.seek(1_800_000)
            backend.pump(4800)
            self.assertGreaterEqual(engine.position_us, 1_800_000)
            self.assertLessEqual(engine.position_us, 1_910_000,
                                 f"seek 之后位置跑飞了：{engine.position_us}")
            # 1.8s 之后仍在片段内（片段 2s），应当有声音
            self.assertGreater(rms(backend.blocks[-1]), 20)
        finally:
            engine.stop()

    def test_engine_survives_a_project_without_audio(self):
        from freecut import builder
        from freecut.audio_engine import AudioEngine, NullBackend

        silent = builder.new_project(640, 360, 30, name="无声")
        from freecut.qtui.bridge import Bridge

        bridge = Bridge()
        bridge.set_project(silent, None)
        backend = NullBackend()
        engine = AudioEngine(bridge, backend=backend)
        try:
            self.assertTrue(engine.start(0))
            backend.pump(1024)
            self.assertLess(rms(backend.blocks[-1]), 1e-6)
        finally:
            engine.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)

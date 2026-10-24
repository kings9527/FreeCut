"""第 13 轮：播放中拖时间轴要**先暂停**（用户实测：剪映会暂停，我们没反应）。

对应剪映的 `isScrubbing` 语义；证据见 `docs/measurements/RUNTIME-CLOCK-TRACK-GPU.md`。
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


class TestPauseOnSeek(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        media = os.path.join(ROOT, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("没有测试素材")
        win = MainWindow(None)
        win.resize(1200, 800)
        win.show()
        self.app.processEvents()
        project = builder.new_project(640, 360, 30, name="暂停")
        builder.add_video(project, media, start=0, duration=8_000_000)
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        return win

    def test_timeline_seek_pauses_playback(self):
        from freecut.qtui.widgets.timeline import HEADER_H

        win = self.window()
        try:
            win.toggle_play()
            self.assertTrue(win._playing, "没进播放状态")
            self.app.processEvents()
            win.timeline.playheadRequested.emit(3_000_000)   # 等价于拖标尺
            self.app.processEvents()
            self.assertFalse(win._playing, "拖动时间轴后没有暂停")
            self.assertEqual(win.bridge.playhead, 3_000_000, "播放头没停在拖到的位置")
            self.app.processEvents()
            self.assertEqual(win.bridge.playhead, 3_000_000,
                             "暂停后播放头又被定时器推走了")
        finally:
            win.close()

    def test_scrub_bar_seek_pauses_playback(self):
        win = self.window()
        try:
            win.toggle_play()
            self.app.processEvents()
            total = int(win.bridge.duration_us)
            permille = int(5_000_000 / total * 1_000_000)
            win.player.scrub.seekRequested.emit(permille)
            self.app.processEvents()
            self.assertFalse(win._playing, "拖播放条后没有暂停")
            self.assertAlmostEqual(win.bridge.playhead / 1e6, 5.0, delta=0.2)
        finally:
            win.close()

    def test_programmatic_playhead_does_not_pause(self):
        """播放推进自己调 set_playhead 时不能暂停（否则播放一帧就停）。"""
        win = self.window()
        try:
            win.toggle_play()
            self.app.processEvents()
            win.bridge.set_playhead(1_000_000)            # 内部推进
            self.app.processEvents()
            self.assertTrue(win._playing, "内部推进被误判成用户拖动")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

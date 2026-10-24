"""转场"能不能看到"的判定：相邻才有效，提示要说清楚。

同时确认：界面加转场后，播放头那一帧真的是两段混合出来的。
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


def _solid(colour: str) -> str:
    from freecut.audio_loudness import ffmpeg_path

    exe = ffmpeg_path()
    if not exe:
        return ""
    path = os.path.join(tempfile.mkdtemp(prefix="tr-"), f"{colour}.png")
    try:
        subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c={colour}:s=320x180:d=1", "-frames:v", "1", path],
                       check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.CalledProcessError):
        return ""
    return path


class TestTransitionVisibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _panel(self, adjacent: bool, apply_kind: str = ""):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        image = _solid("red")
        if not image:
            self.skipTest("没有可用 ffmpeg")
        project = builder.new_project(320, 180, 30, name="转场可见性")
        first = builder.add_image(project, image, start=0, duration=1_000_000)
        second = builder.add_image(project, image, start=1_000_000 if adjacent else 4_500_000,
                                   duration=1_000_000)
        panel = BinPanel(Bridge(project))
        self.addCleanup(panel.close)
        panel.bridge.select(second)
        if apply_kind:
            row = [r for r in range(panel.transition_list.count())
                   if panel.transition_list.item(r).text() == apply_kind][0]
            panel.transition_list.setCurrentRow(row)
            panel.transition_list.itemDoubleClicked.emit(panel.transition_list.item(row))
        return panel, first, second

    def test_adjacent_clip_says_it_will_show(self):
        panel, _first, _second = self._panel(True, "叠化")
        self.assertIn("会显示", panel.transition_applied.text())
        self.assertNotIn("不显示", panel.transition_applied.text())

    def test_gap_says_it_will_not_show(self):
        panel, _first, _second = self._panel(False, "叠化")
        self.assertIn("不显示", panel.transition_applied.text())

    def test_first_clip_without_neighbour_says_not_shown(self):
        panel, first, _second = self._panel(True)
        panel.bridge.select(first)
        panel.refresh_applied_state()
        self.assertIn("左侧没有相邻片段", panel.transition_applied.text())

    def test_frame_at_transition_is_a_blend(self):
        blue = _solid("blue")
        red = _solid("red")
        if not (red and blue):
            self.skipTest("没有可用 ffmpeg")
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        project = builder.new_project(320, 180, 30, name="混合")
        builder.add_image(project, red, start=0, duration=1_000_000)
        second = builder.add_image(project, blue, start=1_000_000, duration=1_000_000)
        panel = BinPanel(Bridge(project))
        self.addCleanup(panel.close)
        panel.bridge.select(second)
        row = [r for r in range(panel.transition_list.count())
               if panel.transition_list.item(r).text() == "叠化"][0]
        panel.transition_list.setCurrentRow(row)
        panel.transition_list.itemDoubleClicked.emit(panel.transition_list.item(row))

        frame = panel.bridge.frame_image(320, 180)
        self.assertIsNotNone(frame)
        from PySide6.QtGui import QImage

        img = frame.convertToFormat(QImage.Format_RGB888)
        data = img.constBits().tobytes()
        count = len(data) // 3
        r = sum(data[0::3]) / count
        b = sum(data[2::3]) / count
        self.assertGreater(r, 40.0, f"红色分量太低，可能没混合：{r:.0f}")
        self.assertGreater(b, 40.0, f"蓝色分量太低，可能没混合：{b:.0f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""转场"覆盖接缝"的表示：块居中压住前后两段、可点选、可一次性套到所有接缝。"""

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
DUR = 500_000


def _image() -> str:
    from freecut.audio_loudness import ffmpeg_path

    exe = ffmpeg_path()
    if not exe:
        return ""
    path = os.path.join(tempfile.mkdtemp(prefix="block-"), "a.png")
    try:
        subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=red:s=64x36:d=1", "-frames:v", "1", path],
                       check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.CalledProcessError):
        return ""
    return path


class TestTransitionBlock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _scene(self, clips: int = 3, with_transition: bool = True):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.timeline import TimelineView

        image = _image()
        if not image:
            self.skipTest("没有可用 ffmpeg")
        project = builder.new_project(320, 180, 30, name="转场块")
        segs = [builder.add_image(project, image, start=i * CUT, duration=CUT)
                for i in range(clips)]
        if with_transition:
            for seg in segs[1:]:
                builder.add_transition(project, seg, "fade_white", duration_us=DUR)
        bridge = Bridge(project)
        view = TimelineView(bridge)
        view.resize(1200, 320)
        view.refresh()
        self.addCleanup(view.close)
        return view, segs

    def test_block_is_centred_on_the_cut(self):
        view, segs = self._scene()
        spans = view.transition_spans(0)
        self.assertEqual(len(spans), 2)
        seg, start_us, end_us, kind, name, dur = spans[0]
        self.assertIs(seg, segs[1])
        self.assertEqual(start_us, CUT - DUR // 2)
        self.assertEqual(end_us, CUT + DUR // 2)
        self.assertEqual(name, "闪白")
        self.assertEqual(dur, DUR)

    def test_first_clip_has_no_block(self):
        view, segs = self._scene()
        self.assertIsNone(view._transition_span(segs[0]))

    def test_clicking_the_block_returns_that_segment(self):
        view, segs = self._scene()
        rect = view._transition_block_rect(segs[1], 0)
        self.assertIsNotNone(rect)
        hit = view.transition_at(rect.center())
        self.assertIsNotNone(hit)
        self.assertIs(hit[0], segs[1])

    def test_apply_to_all_junctions(self):
        from freecut.qtui.widgets.bin_panel import BinPanel

        view, segs = self._scene(with_transition=False)
        panel = BinPanel(view.bridge)
        self.addCleanup(panel.close)
        self.assertEqual(len(panel._all_junctions()), 2)
        row = [r for r in range(panel.transition_list.count())
               if panel.transition_list.item(r).text() == "闪白"][0]
        panel.transition_list.setCurrentRow(row)
        panel.apply_transition_to_all()
        for seg in segs[1:]:
            kinds = [ref.get("kind") for ref in view.bridge.project.refs_of(seg)
                     if getattr(ref, "type", "") == "transition"]
            self.assertTrue(kinds, "这个接缝没有加上转场")
            self.assertEqual(int(kinds and view.bridge.project.refs_of(seg)[-1].get("duration") or 0)
                             > 0, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

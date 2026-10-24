"""草稿往返：导入真实草稿 → 用我们的导出器写出 → 再导入，关键结构应当守恒。

只比较"结构守恒"（片段数、转场种类、关键帧数），不比较像素。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DRAFT_H5 = r"E:\剪映-草稿\JianyingPro Drafts\10月23日 (5)"
DRAFT_H4 = r"E:\剪映-草稿\JianyingPro Drafts\10月23日 (4)"


def _structure(project):
    from freecut.render import transition_kind

    segments = 0
    keyframes = 0
    transitions = []
    for track in project.tracks:
        for seg in track.segments:
            segments += 1
            for keys in (seg.keyframes or {}).values():
                keyframes += len(keys)
            ref = project.first_ref(seg, "transition")
            if ref is not None:
                transitions.append(transition_kind(ref))
    return segments, keyframes, sorted(transitions)


class TestDraftRoundTrip(unittest.TestCase):
    def _round_trip(self, folder):
        from freecut import jianying_export
        from freecut.jianying import load_project_draft

        if not os.path.isdir(folder):
            self.skipTest("本机没有该草稿")
        before = load_project_draft(folder)
        tmp = tempfile.mkdtemp(prefix="freecut-rt-")
        self.addCleanup(shutil.rmtree, tmp, True)
        out = jianying_export.save_draft(before, tmp, name="roundtrip")
        after = load_project_draft(out if os.path.isdir(out) else os.path.dirname(out))
        return _structure(before), _structure(after)

    def test_keyframes_survive_round_trip(self):
        before, after = self._round_trip(DRAFT_H5)
        self.assertEqual(after[1], before[1], f"关键帧数变了：{before[1]} → {after[1]}")

    def test_transition_survives_round_trip(self):
        before, after = self._round_trip(DRAFT_H4)
        self.assertEqual(after[2], before[2], f"转场变了：{before[2]} → {after[2]}")

    def test_color_materials_survive_round_trip(self):
        """HSL / 曲线 / 调色材质在导出时不能被丢掉（导入支持 ≠ 导出支持）。"""
        from freecut import jianying_export
        from freecut.jianying import load_project_draft

        if not os.path.isdir(DRAFT_H5):
            self.skipTest("本机没有该草稿")

        before = load_project_draft(DRAFT_H5)
        tmp = tempfile.mkdtemp(prefix="freecut-rt-")
        self.addCleanup(shutil.rmtree, tmp, True)
        out = jianying_export.save_draft(before, tmp, name="roundtrip-color")
        path = out if os.path.isfile(out) else os.path.join(out, "draft_content.json")
        import json

        with open(path, encoding="utf-8") as fh:
            materials = json.load(fh)["materials"]
        self.assertGreaterEqual(len(materials.get("hsl") or []), 1, "导出丢了 hsl")
        self.assertGreaterEqual(len(materials.get("color_curves") or []), 1,
                                "导出丢了 color_curves")


if __name__ == "__main__":
    unittest.main(verbosity=2)

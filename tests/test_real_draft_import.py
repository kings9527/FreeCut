"""真实草稿导入回归：用用户提供的剪映草稿验证导入保真度。

跳过条件：本机没有这份草稿目录时自动跳过（CI/其他机器）。
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

DRAFT_H5 = r"E:\剪映-草稿\JianyingPro Drafts\10月23日 (5)"   # HSL + 曲线 + 缩放关键帧
DRAFT_H4 = r"E:\剪映-草稿\JianyingPro Drafts\10月23日 (4)"   # 闪黑转场


def _load(folder):
    from freecut.jianying import load_project_draft

    if not os.path.isdir(folder):
        raise unittest.SkipTest("本机没有该草稿")
    return load_project_draft(folder)


class TestRealDraftImport(unittest.TestCase):
    def test_hsl_and_curves_draft_imports(self):
        project = _load(DRAFT_H5)
        segments = [s for t in project.tracks for s in t.segments]
        self.assertGreaterEqual(len(segments), 1, "没有导入任何片段")
        kinds = {type(m).__name__ for m in project.materials.values()}
        self.assertTrue({"HslMaterial", "CurveMaterial"} & kinds,
                        f"HSL / 曲线没有被导入，实际材质类型 {sorted(kinds)}")

    def test_scale_keyframes_survive_import(self):
        project = _load(DRAFT_H5)
        counts = []
        for track in project.tracks:
            for seg in track.segments:
                for keys in (seg.keyframes or {}).values():
                    counts.append(len(keys))
        self.assertTrue(counts, "导入后一个关键帧都没有")
        self.assertGreaterEqual(max(counts), 2, "缩放关键帧应当有 2 个")

    def test_transition_draft_imports_the_transition(self):
        project = _load(DRAFT_H4)
        found = []
        for track in project.tracks:
            for seg in track.segments:
                ref = project.first_ref(seg, "transition")
                if ref is not None:
                    found.append(ref)
        self.assertTrue(found, "闪黑转场没有被导入")
        from freecut.render import transition_kind

        self.assertEqual(transition_kind(found[0]), "fade_black",
                         "闪黑应当解析成 fade_black")


if __name__ == "__main__":
    unittest.main(verbosity=2)

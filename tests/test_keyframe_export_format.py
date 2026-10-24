"""导出关键帧的格式断言（JSON 层，不依赖 UI）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

IMAGE = os.path.join(ROOT, "out", "chroma_src.png")


def export_json(project):
    from freecut import jianying_export

    tmp = tempfile.mkdtemp(prefix="freecut-kf-")
    out = jianying_export.save_draft(project, tmp)
    path = out if os.path.isfile(out) else os.path.join(out, "draft_content.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestKeyframeExportFormat(unittest.TestCase):
    def test_keyframe_group_matches_the_measured_format(self):
        from freecut import builder
        from freecut.model import Keyframe

        if not os.path.isfile(IMAGE):
            self.skipTest("no image")
        project = builder.new_project(320, 180, 30, name="kf")
        seg = builder.add_image(project, IMAGE, start=0, duration=2_000_000)
        seg.keyframes["KFTypeScaleX"] = [Keyframe(0, 1.0), Keyframe(500_000, 1.5)]

        data = export_json(project)
        groups = [
            kf
            for track in data["tracks"]
            for s in track["segments"]
            for kf in s.get("common_keyframes") or []
        ]
        scale = [g for g in groups if g.get("property_type") == "KFTypeScaleX"]
        self.assertTrue(scale, "exported draft has no KFTypeScaleX group")
        items = scale[0]["keyframe_list"]
        self.assertEqual(len(items), 2)
        self.assertNotIn("time_offset", items[0])
        self.assertEqual(items[1]["time_offset"], 500_000)
        self.assertEqual(items[0]["values"], [1.0])
        self.assertEqual(items[1]["values"], [1.5])

    def test_no_keyframes_exports_empty_list(self):
        from freecut import builder

        if not os.path.isfile(IMAGE):
            self.skipTest("no image")
        project = builder.new_project(320, 180, 30, name="nokf")
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        data = export_json(project)
        for track in data["tracks"]:
            for seg in track["segments"]:
                self.assertEqual(seg.get("common_keyframes"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

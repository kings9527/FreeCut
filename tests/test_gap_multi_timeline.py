"""第 19 轮：读剪映的工程目录（多时间线）。

证据：`docs/measurements/MULTI-TIMELINE-ID-MAPPING.md`（真实解密草稿）——
`project.json` 里有 `main_timeline_id` + `timelines[]`，每条时间线是
`Timelines/<uuid>/draft_content.json`，删除是 `is_marked_delete` 软标记。
"""

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

MEDIA = os.path.join(ROOT, "out", "demo.mp4")


def draft_dict(name: str) -> dict:
    """一份最小的剪映形状草稿（明文）。"""
    return {
        "id": name,
        "canvas_config": {"width": 320, "height": 180, "ratio": "16:9"},
        "tracks": [{
            "type": "video",
            "segments": [{
                "material_id": "m1",
                "target_timerange": {"start": 0, "duration": 1_000_000},
                "source_timerange": {"start": 0, "duration": 1_000_000},
            }],
        }],
        "materials": {"videos": [{
            "id": "m1", "path": MEDIA.replace("\\", "/"), "duration": 1_000_000,
            "width": 320, "height": 180, "material_name": "demo.mp4", "type": "video",
        }]},
    }


class TestMultiTimelineImport(unittest.TestCase):
    def setUp(self):
        if not os.path.isfile(MEDIA):
            self.skipTest("没有测试素材")
        self.tmp = tempfile.mkdtemp(prefix="freecut-multi-")
        self.folder = os.path.join(self.tmp, "工程")
        self.make_project()

    def make_project(self):
        folder = self.folder
        os.makedirs(os.path.join(folder, "Timelines"), exist_ok=True)
        timelines = []
        for index, (timeline_id, name, deleted) in enumerate((
            ("AAAA-1", "时间线01", False),
            ("BBBB-2", "时间线02", False),
            ("CCCC-3", "已删除的线", True),
        )):
            directory = os.path.join(folder, "Timelines", timeline_id)
            os.makedirs(directory, exist_ok=True)
            with open(os.path.join(directory, "draft_content.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(draft_dict(name), fh, ensure_ascii=False)
            timelines.append({"id": timeline_id, "name": name,
                              "is_marked_delete": deleted})
        with open(os.path.join(folder, "project.json"), "w", encoding="utf-8") as fh:
            json.dump({"main_timeline_id": "BBBB-2", "timelines": timelines}, fh,
                      ensure_ascii=False)

    def test_reads_every_timeline_and_skips_deleted(self):
        from freecut.jianying import read_project_timelines

        timelines = read_project_timelines(self.folder)
        names = [name for _id, name, _dir in timelines]
        self.assertEqual(names, ["时间线01", "时间线02"],
                         f"没有跳过已标记删除的时间线：{names}")

    def test_loads_all_timelines_into_sequences(self):
        from freecut.jianying import load_project_draft

        project = load_project_draft(self.folder)
        self.assertEqual(len(project.sequences), 2,
                         f"应当读进 2 条时间线，实际 {len(project.sequences)}")
        self.assertEqual([s.name for s in project.sequences],
                         ["时间线01", "时间线02"])
        for sequence in project.sequences:
            self.assertEqual(len(sequence.tracks), 1)
            self.assertEqual(sum(len(t.segments) for t in sequence.tracks), 1)
            # 材质必须一起并进来，否则后面几条时间线渲染是黑屏
            for track in sequence.tracks:
                for segment in track.segments:
                    self.assertIsNotNone(project.material(segment.material_id),
                                         "合并后片段指向了不存在的材质")

    def test_main_timeline_becomes_active(self):
        from freecut.jianying import load_project_draft

        project = load_project_draft(self.folder)
        self.assertEqual(project.active_sequence, 1,
                         "main_timeline_id 指向时间线02，当前应当切到它")
        self.assertEqual(project.sequences[project.active_sequence].name, "时间线02")

    def test_plain_single_draft_still_loads(self):
        """没有 project.json 的老目录仍然按单草稿读。"""
        from freecut.jianying import load_project_draft

        single = os.path.join(self.tmp, "单线")
        os.makedirs(single, exist_ok=True)
        with open(os.path.join(single, "draft_content.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(draft_dict("单线"), fh, ensure_ascii=False)
        project = load_project_draft(single)
        self.assertEqual(len(project.sequences), 1)
        self.assertEqual(sum(len(t.segments) for t in project.tracks), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

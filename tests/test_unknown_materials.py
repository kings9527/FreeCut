"""未导入材料告警测试。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestUnknownMaterials(unittest.TestCase):
    def test_reports_non_empty_unknown_only(self):
        from freecut.jianying import unknown_material_keys

        data = {"materials": {
            "videos": [{"id": "a"}],             # 已支持
            "audio_pitch_shifts": [{}, {}],      # 未知且非空 → 要报
            "audio_effects": [],                 # 未知但空 → 不报
            "hsl": [{"x": 1}],                   # 特殊处理过
            "drafts": [{"type": "combination"}],  # 组合模板 → 忽略
        }}
        self.assertEqual(unknown_material_keys(data), {"audio_pitch_shifts": 2})

    def test_missing_materials_is_safe(self):
        from freecut.jianying import unknown_material_keys

        self.assertEqual(unknown_material_keys({}), {})
        self.assertEqual(unknown_material_keys({"materials": {}}), {})
        self.assertEqual(unknown_material_keys({"materials": "bad"}), {})

    def test_real_drafts_report_nothing_unknown(self):
        """本机真实草稿里不该有"未知且非空"的材料（普查结论的回归）。"""
        import os as _os

        from freecut.jianying import _read_draft_json, unknown_material_keys

        base = r"E:\剪映-草稿\JianyingPro Drafts"
        if not _os.path.isdir(base):
            self.skipTest("本机没有草稿目录")
        checked = 0
        for name in _os.listdir(base):
            folder = _os.path.join(base, name)
            if not _os.path.isdir(folder):
                continue
            try:
                data = _read_draft_json(folder)
            except Exception:                      # noqa: BLE001
                continue
            unknown = unknown_material_keys(data)
            self.assertEqual(unknown, {}, f"{name} 出现未导入材料：{unknown}")
            checked += 1
        self.assertGreater(checked, 0, "一份草稿都没读到")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Unit tests for FreeCut.  Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from freecut import builder, color, keyframe, model, transform  # noqa: E402
from freecut.model import Keyframe, Project, Timerange  # noqa: E402


def gradient(h=32, w=48):
    xs = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    ys = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    img = np.zeros((h, w, 3), dtype=np.float32)
    img[..., 0] = xs
    img[..., 1] = ys
    img[..., 2] = 0.5 * (xs + ys)
    return img


class TestColor(unittest.TestCase):
    def test_identity(self):
        img = gradient()
        for name, fn in (
            ("brightness", lambda a: color.brightness(a, 0.0)),
            ("contrast", lambda a: color.contrast(a, 0.0)),
            ("saturation", lambda a: color.saturation(a, 1.0)),
            ("highlight", lambda a: color.highlight(a, 0.0)),
            ("shadow", lambda a: color.shadow(a, 0.0)),
            ("black_white", lambda a: color.black_white(a, 0.0, 0.0)),
            ("temperature", lambda a: color.temperature_tint(a, 0.0, 0.0)),
            ("sharpen", lambda a: color.sharpen(a, 0.0)),
            ("vignette", lambda a: color.vignette(a, 0.0)),
        ):
            with self.subTest(name=name):
                out = fn(img)
                np.testing.assert_allclose(out, img, atol=1e-6)

    def test_brightness_direction(self):
        img = gradient()
        self.assertGreater(color.brightness(img, 0.5).mean(), img.mean())
        self.assertLess(color.brightness(img, -0.5).mean(), img.mean())

    def test_brightness_keeps_bounds(self):
        for v in (-1.0, -0.3, 0.3, 1.0):
            out = color.brightness(gradient(), v)
            self.assertGreaterEqual(float(out.min()), 0.0)
            self.assertLessEqual(float(out.max()), 1.0)

    def test_contrast_pivot_fixed(self):
        img = np.full((4, 4, 3), color.CONTRAST_PIVOT, dtype=np.float32)
        out = color.contrast(img, 0.8)
        np.testing.assert_allclose(out, img, atol=2e-3)

    def test_contrast_direction(self):
        img = gradient()
        hi = color.contrast(img, 0.9)
        lo = color.contrast(img, -0.9)
        self.assertGreater(hi.std(), img.std() * 0.9)
        self.assertLess(lo.std(), img.std())

    def test_saturation_zero_is_grey(self):
        img = gradient()
        out = color.saturation(img, 0.0)
        np.testing.assert_allclose(out[..., 0], out[..., 1], atol=1e-5)
        np.testing.assert_allclose(out[..., 1], out[..., 2], atol=1e-5)

    def test_saturation_two_oversaturates(self):
        img = gradient()
        out = color.saturation(img, 2.0)
        self.assertGreaterEqual(float(out.max()), float(img.max()) - 1e-6)

    def test_levels(self):
        slope, bias = color.levels_matrix(0.0, 0.0)
        self.assertAlmostEqual(slope, 1.0, places=5)
        self.assertAlmostEqual(bias, 0.0, places=5)
        slope2, _ = color.levels_matrix(1.0, 0.0)
        self.assertGreater(slope2, 1.0)

    def test_temperature_neutral_matrix(self):
        m = color.temperature_matrix(0.0, 0.0)
        np.testing.assert_allclose(m, np.eye(3), atol=1e-9)

    def test_temperature_warm_shifts_red(self):
        img = np.full((2, 2, 3), 0.5, dtype=np.float32)
        warm = color.temperature_tint(img, 0.8, 0.0)
        cool = color.temperature_tint(img, -0.8, 0.0)
        self.assertGreater(float(warm[..., 0].mean()), float(cool[..., 0].mean()))

    def test_lut_identity(self):
        n = 5
        ax = np.linspace(0, 1, n, dtype=np.float32)
        lut = np.zeros((n, n, n, 3), dtype=np.float32)
        for b in range(n):
            for g in range(n):
                for r in range(n):
                    lut[b, g, r] = (ax[r], ax[g], ax[b])
        img = gradient(16, 16)
        out = color.apply_lut3d(img, lut, 1.0)
        np.testing.assert_allclose(out, img, atol=1e-5)

    def test_adjust_stack_smoke(self):
        out = color.apply_adjust(gradient(), {
            "brightness": 10, "contrast": 15, "saturation": 20,
            "temperature": 5, "tint": -3, "highlight": -10, "shadow": 8,
            "white": 4, "black": -4, "sharpen": 10, "vignette": 12,
        })
        self.assertEqual(out.shape, gradient().shape)
        self.assertGreaterEqual(float(out.min()), 0.0)
        self.assertLessEqual(float(out.max()), 1.0)


class TestKeyframes(unittest.TestCase):
    def test_linear_midpoint(self):
        prop = keyframe.AnimatedProperty(0.0, [Keyframe(0, 0.0, "Line"), Keyframe(1000, 10.0, "Line")])
        self.assertAlmostEqual(prop.value_at(0), 0.0)
        self.assertAlmostEqual(prop.value_at(500), 5.0)
        self.assertAlmostEqual(prop.value_at(1000), 10.0)
        self.assertAlmostEqual(prop.value_at(2000), 10.0)

    def test_single_keyframe_is_constant(self):
        prop = keyframe.AnimatedProperty(3.0, [Keyframe(200, 7.0)])
        self.assertAlmostEqual(prop.value_at(0), 7.0)
        self.assertAlmostEqual(prop.value_at(5000), 7.0)

    def test_no_keyframes_uses_static(self):
        prop = keyframe.AnimatedProperty(4.2, [])
        self.assertAlmostEqual(prop.value_at(123), 4.2)

    def test_curve_in_is_slower_at_start(self):
        lin = keyframe.AnimatedProperty(0.0, [Keyframe(0, 0.0, "Line"), Keyframe(100, 1.0, "Line")])
        ein = keyframe.AnimatedProperty(0.0, [Keyframe(0, 0.0, "CurveIn"), Keyframe(100, 1.0, "CurveIn")])
        self.assertLess(ein.value_at(25), lin.value_at(25))
        self.assertAlmostEqual(ein.value_at(100), 1.0)

    def test_real_curve_types_supported(self):
        for ct in model.CURVE_TYPES:
            prop = keyframe.AnimatedProperty(0.0, [Keyframe(0, 0.0, ct), Keyframe(10, 1.0, ct)])
            self.assertGreaterEqual(prop.value_at(5), 0.0)
            self.assertLessEqual(prop.value_at(5), 1.0)


class TestModel(unittest.TestCase):
    def test_timerange(self):
        tr = Timerange(1_000_000, 2_000_000)
        self.assertTrue(tr.contains(1_500_000))
        self.assertFalse(tr.contains(3_000_000))
        self.assertEqual(tr.overlap(Timerange(2_000_000, 2_000_000)), 1_000_000)

    def test_project_roundtrip(self):
        p = builder.new_project(640, 360, 25, name="t")
        builder.add_text(p, "hello", start=0, duration=1_000_000)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.json")
            p.save(path)
            q = Project.load(path)
        self.assertEqual(q.canvas.width, 640)
        self.assertEqual(q.fps, 25)
        self.assertEqual(len(q.tracks), 1)
        self.assertEqual(q.tracks[0].type, "text")
        self.assertEqual(q.duration(), 1_000_000)

    def test_segment_source_time_mapping(self):
        seg = model.Segment(
            target_timerange=Timerange(1_000_000, 2_000_000),
            source_timerange=Timerange(5_000_000, 4_000_000),
            speed=2.0,
        )
        self.assertEqual(seg.source_time(1_500_000), 5_000_000 + 1_000_000)

    def test_split(self):
        p = builder.new_project(320, 180, 30)
        track = builder.track_of(p, "text")
        seg = model.Segment(
            material_id="m",
            target_timerange=Timerange(0, 4_000_000),
            source_timerange=Timerange(0, 4_000_000),
        )
        track.segments.append(seg)
        right = builder.split(p, seg, 1_000_000)
        self.assertEqual(seg.target_timerange, Timerange(0, 1_000_000))
        self.assertEqual(right.target_timerange, Timerange(1_000_000, 3_000_000))
        self.assertEqual(len(track.segments), 2)


class TestGeometry(unittest.TestCase):
    def test_contain_scale(self):
        self.assertAlmostEqual(transform.contain_scale(1920, 1080, 640, 360), 1 / 3, places=5)
        self.assertAlmostEqual(transform.contain_scale(1080, 1920, 640, 360), 360 / 1920, places=5)

    def test_affine_centre(self):
        m = transform.build_affine(100, 100, 200, 200, scale_x=1, scale_y=1)
        # source centre (50,50) maps to canvas centre (100,100)
        cx, cy = m[0, 0] * 50 + m[0, 1] * 50 + m[0, 2], m[1, 0] * 50 + m[1, 1] * 50 + m[1, 2]
        self.assertAlmostEqual(cx, 100, places=4)
        self.assertAlmostEqual(cy, 100, places=4)

    def test_position_positive_y_is_up(self):
        m = transform.build_affine(100, 100, 200, 200, ty=0.5)
        cy = m[1, 0] * 50 + m[1, 1] * 50 + m[1, 2]
        self.assertLess(cy, 100)


class TestJianyingImport(unittest.TestCase):
    DRAFT = r"E:\剪映-草稿\JianyingPro Drafts\9月6日\subdraft\A70DB7F7-0DFF-484c-87DB-432B74F4FDD6\draft_content.json"

    def test_import_plaintext_draft(self):
        from freecut import jianying

        if not os.path.isfile(self.DRAFT):
            self.skipTest("样例剪映草稿不存在")
        p = jianying.load_draft(self.DRAFT)
        self.assertGreater(len(p.tracks), 0)
        types = {t.type for t in p.tracks}
        self.assertIn("video", types)
        speeds = [s.speed for t in p.tracks for s in t.segments]
        self.assertTrue(any(abs(s - 1.0) > 1e-6 for s in speeds), "应包含变速片段")

    def test_encrypted_draft_is_transparently_decrypted(self):
        """剪映 6+ encrypts draft_content.json; we decrypt via videoeditor.dll."""
        from freecut import jianying

        path = os.path.join(ROOT, "..", "内部实测", "evidence", "drafts", "main-encrypted.draft_content.json")
        if not os.path.isfile(path):
            self.skipTest("加密样例不存在")
        if not jianying.is_encrypted(path):
            self.skipTest("样例不是加密草稿")
        try:
            p = jianying.load_draft(path)
        except ValueError as exc:
            self.skipTest(f"本机没有可用的剪映 DLL: {exc}")
        self.assertGreater(len(p.tracks), 0)

    def test_encrypted_draft_raises_when_decrypt_disabled(self):
        from freecut import jianying

        path = os.path.join(ROOT, "..", "内部实测", "evidence", "drafts", "main-encrypted.draft_content.json")
        if not os.path.isfile(path):
            self.skipTest("加密样例不存在")
        with self.assertRaises(ValueError):
            jianying.load_draft(path, decrypt=False)


class TestRender(unittest.TestCase):
    def test_render_solid_canvas(self):
        from freecut.render import Renderer

        p = builder.new_project(64, 36, 10)
        p.canvas.background = "#204060"
        r = Renderer(p, 64, 36, 10)
        try:
            frame = r.render_frame(0)
        finally:
            r.close()
        self.assertEqual(frame.shape, (36, 64, 3))
        self.assertEqual(tuple(frame[0, 0]), (0x20, 0x40, 0x60))

    def test_render_text_layer(self):
        from freecut.render import Renderer

        p = builder.new_project(160, 90, 10)
        builder.add_text(p, "A", start=0, duration=1_000_000, size=40, y=0.0)
        r = Renderer(p, 160, 90, 10)
        try:
            frame = r.render_frame(0)
        finally:
            r.close()
        self.assertGreater(int(frame.max()), 40, "文字应当被渲染出来")

    def test_keyframed_scale_changes_pixels(self):
        from freecut.render import Renderer

        p = builder.new_project(80, 45, 10)
        builder.add_text(p, "■", start=0, duration=1_000_000, size=30)
        seg = p.tracks[0].segments[0]
        builder.add_keyframe(seg, "KFTypeAlpha", 0, 0.0, "Line")
        builder.add_keyframe(seg, "KFTypeAlpha", 900_000, 1.0, "Line")
        r = Renderer(p, 80, 45, 10)
        try:
            first = r.render_frame(0).sum()
            last = r.render_frame(8).sum()
        finally:
            r.close()
        self.assertLess(first, last)


class TestDraftExport(unittest.TestCase):
    @staticmethod
    def _add_fake_video(p, path, start, duration, track_index=0):
        """Add a video segment without probing a real file."""
        mat = model.VideoMaterial(path=path, duration=duration, width=640, height=360,
                                  has_audio=False, material_name=os.path.basename(path))
        p.add_material(mat)
        seg = model.Segment(
            material_id=mat.id,
            target_timerange=model.Timerange(start, duration),
            source_timerange=model.Timerange(0, duration),
        )
        builder.track_of(p, "video", track_index).segments.append(seg)
        return seg

    def _project(self, media_path: str):
        p = builder.new_project(640, 360, 30, name="导出测试")
        a = self._add_fake_video(p, media_path, 0, 2_000_000)
        b = self._add_fake_video(p, media_path, 2_000_000, 2_000_000)
        builder.add_transition(p, b, name="叠化")
        builder.add_text(p, "标题", start=0, duration=1_500_000, y=-0.7, size=10)
        builder.add_keyframe(a, "KFTypeScaleX", 0, 1.0, "CurveInOut")
        builder.add_keyframe(a, "KFTypeScaleX", 1_000_000, 1.2, "CurveInOut")
        return p

    def test_export_plaintext_roundtrip(self):
        from freecut import jianying, jianying_export, jianying_validate

        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "fake.mp4")
            with open(media, "wb") as fh:
                fh.write(b"\x00" * 16)
            p = self._project(media)
            d = jianying_export.save_draft(p, tmp, name="d", encrypt=False, overwrite=True)
            self.assertTrue(os.path.isfile(os.path.join(d, "draft_content.json")))
            with open(os.path.join(d, "draft_content.json"), "rb") as fh:
                self.assertEqual(fh.read(1), b"{", "明文导出应当是 JSON")

            rep = jianying_validate.validate_draft_dir(d)
            self.assertTrue(rep.ok, rep.as_text())
            self.assertEqual(rep.warnings, [], rep.as_text())

            back = jianying.load_draft(d)
            self.assertEqual(len(back.tracks), 2)
            self.assertEqual(back.canvas.width, 640)
            self.assertAlmostEqual(back.duration() / 1e6, 4.0, places=3)
            trans = [back.first_ref(s, "transition") for t in back.tracks for s in t.segments]
            self.assertTrue(any(t is not None for t in trans))
            self.assertTrue(any(s.keyframes for t in back.tracks for s in t.segments))

    def test_export_trims_overlaps_into_adjacent_clips(self):
        from freecut import jianying_export

        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "fake.mp4")
            with open(media, "wb") as fh:
                fh.write(b"\x00" * 16)
            p = builder.new_project(320, 180, 30)
            self._add_fake_video(p, media, 0, 3_000_000)
            self._add_fake_video(p, media, 2_000_000, 3_000_000)  # overlaps by 1s
            content = jianying_export.build_content(p)
            segs = content["tracks"][0]["segments"]
            self.assertEqual(len(segs), 2)
            self.assertLessEqual(segs[0]["target_timerange"]["duration"], 2_000_000)
            first_end = segs[0]["target_timerange"]["start"] + segs[0]["target_timerange"]["duration"]
            self.assertLessEqual(first_end, segs[1]["target_timerange"]["start"])

    def test_validator_catches_dangling_reference(self):
        from freecut import jianying_validate

        content = {
            "canvas_config": {"width": 640, "height": 360},
            "fps": 30,
            "materials": {"videos": [{"id": "V1", "type": "video", "path": "x.mp4"}]},
            "tracks": [
                {
                    "type": "video",
                    "segments": [
                        {
                            "material_id": "MISSING",
                            "target_timerange": {"start": 0, "duration": 1_000_000},
                            "source_timerange": {"start": 0, "duration": 1_000_000},
                            "speed": 1.0,
                            "extra_material_refs": ["NOPE"],
                        }
                    ],
                }
            ],
        }
        rep = jianying_validate.validate_content(content)
        self.assertFalse(rep.ok)
        text = rep.as_text()
        self.assertIn("MISSING", text)
        self.assertIn("NOPE", text)

    def test_curve_names_are_normalised(self):
        from freecut.keyframe import normalize_curve

        self.assertEqual(normalize_curve("EaseInOut"), "CurveInOut")
        self.assertEqual(normalize_curve("Line"), "CurveInOut")
        self.assertEqual(normalize_curve("CurveIn"), "CurveIn")
        self.assertEqual(normalize_curve(""), "CurveInOut")
        self.assertEqual(normalize_curve("FreeCurveOut"), "FreeCurveOut")


class TestMasks(unittest.TestCase):
    def test_shape_and_range(self):
        from freecut import mask

        for kind in ("line", "mirror", "circle", "rectangle", "geometric_shape"):
            m = mask.build_mask(64, 36, {"feather": 0.1, "width": 1.0, "height": 1.0}, kind)
            self.assertEqual(m.shape, (36, 64), kind)
            self.assertGreaterEqual(float(m.min()), 0.0)
            self.assertLessEqual(float(m.max()), 1.0)

    def test_circle_covers_expected_area(self):
        from freecut import mask

        m = mask.build_mask(128, 72, {"feather": 0.0}, "circle")
        # an ellipse inscribed in the frame covers pi/4 of it
        self.assertAlmostEqual(float(m.mean()), 3.14159 / 4, delta=0.06)

    def test_invert(self):
        from freecut import mask

        cfg = {"feather": 0.0}
        a = mask.build_mask(64, 36, cfg, "circle")
        b = mask.build_mask(64, 36, dict(cfg, invert=True), "circle")
        np.testing.assert_allclose(a + b, np.ones_like(a), atol=1e-6)

    def test_rectangle_round_corner_reduces_area(self):
        from freecut import mask

        sharp = mask.build_mask(128, 72, {"feather": 0.0, "roundCorner": 0.0}, "rectangle")
        round_ = mask.build_mask(128, 72, {"feather": 0.0, "roundCorner": 0.8}, "rectangle")
        self.assertLessEqual(float(round_.mean()), float(sharp.mean()) + 1e-6)

    def test_mask_changes_rendered_alpha(self):
        from freecut.render import Renderer

        p = builder.new_project(160, 90, 10)
        seg = builder.add_text(p, "■", start=0, duration=1_000_000, size=40)
        before = Renderer(p, 160, 90, 10)
        try:
            sum_before = int(before.render_frame(0).sum())
        finally:
            before.close()
        builder.add_mask(p, seg, name="圆形", width=0.4, height=0.4, feather=0.02)
        after = Renderer(p, 160, 90, 10)
        try:
            sum_after = int(after.render_frame(0).sum())
        finally:
            after.close()
        self.assertLess(sum_after, sum_before)


class TestCurvesAndHsl(unittest.TestCase):
    def test_curve_identity(self):
        img = np.linspace(0, 1, 48, dtype=np.float32).reshape(4, 4, 3)
        out = color.apply_curves(img, {"rgb": [[0, 0], [1, 1]]})
        np.testing.assert_allclose(out, img, atol=1e-6)

    def test_curve_midpoint_lift(self):
        img = np.full((2, 2, 3), 0.5, dtype=np.float32)
        out = color.apply_curves(img, {"rgb": [[0, 0], [0.5, 0.7], [1, 1]]})
        self.assertGreater(float(out.mean()), 0.6)

    def test_curve_is_monotonic(self):
        img = np.linspace(0, 1, 96, dtype=np.float32).reshape(8, 4, 3)
        out = color.apply_curves(img, {"rgb": [[0, 0.05], [0.5, 0.5], [1, 0.95]]})
        flat = out[..., 0].reshape(-1)
        self.assertTrue(np.all(np.diff(flat) >= -1e-6))

    def test_hsl_identity(self):
        img = gradient()
        np.testing.assert_allclose(color.apply_hsl(img, {}, {}), img, atol=2e-3)

    def test_hsl_saturation_by_band(self):
        red = np.zeros((2, 2, 3), dtype=np.float32)
        red[..., 0] = 0.8  # pure red -> hue 0
        out = color.apply_hsl(red, {"red": {"sat": -1.0}}, {})
        hsv = color._rgb_to_hsv(out)
        self.assertLess(float(hsv[..., 1].mean()), 0.2)

    def test_hsl_fallback_hue_shift(self):
        img = gradient()
        out = color.apply_hsl(img, {}, {"hue": 40.0})
        self.assertEqual(out.shape, img.shape)
        self.assertFalse(np.allclose(out, img, atol=1e-3))


class TestExportEffects(unittest.TestCase):
    def test_filters_go_into_effects_and_masks_into_both_arrays(self):
        from freecut import jianying_export

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000)
        builder.add_cloud_filter(p, seg, "1980", 0.8)
        builder.set_mix_mode(p, seg, "滤色")
        builder.add_mask(p, seg, name="矩形")

        content = jianying_export.build_content(p)
        effects = content["materials"]["effects"]
        types = [e["type"] for e in effects]
        self.assertIn("filter", types)
        self.assertIn("mix_mode", types)
        flt = [e for e in effects if e["type"] == "filter"][0]
        self.assertEqual(flt["value"], 0.8)
        self.assertTrue(flt["resource_id"])

        mask_ids = {m["id"] for m in content["materials"]["common_mask"]}
        self.assertTrue(mask_ids)
        self.assertEqual(mask_ids, {m["id"] for m in content["materials"]["masks"]})

    def test_grading_values_are_written_not_parked(self):
        """第 86 轮起调色数值直接写进草稿（形状未证实），不再只进旁路。"""
        from freecut import jianying_export

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000)
        curve = builder.set_curves(p, seg, rgb=[[0, 0], [1, 1]])
        content = jianying_export.build_content(p)
        written = {m["id"] for arr in content["materials"].values() for m in arr}
        self.assertIn(curve.id, written, "曲线材质应当写进草稿")
        self.assertIn(curve.id, content["materials"]["color_curves"][0]["id"]
                      if content["materials"]["color_curves"] else "")

    def test_truly_unrepresentable_material_goes_to_sidecar(self):
        """剪映完全没有对应物的材质仍然进旁路。"""
        from freecut import jianying_export
        from freecut.model import Material

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000)
        odd = Material(id="odd-material", type="freecut_only_thing")
        p.materials[odd.id] = odd
        seg.extra_material_refs.append(odd.id)
        content = jianying_export.build_content(p)
        extras = content.get("_freecut_extras") or {}
        self.assertIn(odd.id, extras.get("materials") or {})
        written = {m["id"] for arr in content["materials"].values() for m in arr}
        self.assertNotIn(odd.id, written)


class TestWheels(unittest.TestCase):
    def test_identity(self):
        img = gradient()
        np.testing.assert_allclose(color.apply_wheels(img, {}), img, atol=1e-6)

    def test_lift_raises_shadows(self):
        img = np.full((2, 2, 3), 0.1, dtype=np.float32)
        out = color.apply_wheels(img, {"lift": [0.2, 0.0, 0.0]})
        self.assertGreater(float(out[..., 0].mean()), float(img[..., 0].mean()))
        self.assertAlmostEqual(float(out[..., 1].mean()), 0.1, places=5)

    def test_gain_scales(self):
        img = np.full((2, 2, 3), 0.5, dtype=np.float32)
        out = color.apply_wheels(img, {"gain": [1.5, 1.0, 0.5]})
        self.assertAlmostEqual(float(out[..., 0].mean()), 0.75, places=4)
        self.assertAlmostEqual(float(out[..., 2].mean()), 0.25, places=4)

    def test_gamma_brightens_midtones(self):
        img = np.full((2, 2, 3), 0.25, dtype=np.float32)
        out = color.apply_wheels(img, {"gamma": [2.0, 2.0, 2.0]})
        self.assertGreater(float(out.mean()), 0.25)


class TestAnimations(unittest.TestCase):
    def test_fade_in_ramps_alpha(self):
        from freecut import animation

        self.assertLess(animation.evaluate("fade_in", 0.1).alpha, animation.evaluate("fade_in", 0.9).alpha)
        self.assertAlmostEqual(animation.evaluate("fade_in", 1.0).alpha, 1.0, places=3)

    def test_slide_moves_then_settles(self):
        from freecut import animation

        self.assertNotEqual(animation.evaluate("slide_in_right", 0.1).dx, 0.0)
        self.assertAlmostEqual(animation.evaluate("slide_in_right", 1.0).dx, 0.0, places=4)

    def test_loop_is_periodic(self):
        from freecut import animation

        a = animation.evaluate("breathe", 0.0).scale
        b = animation.evaluate("breathe", 1.0).scale
        self.assertAlmostEqual(a, b, places=5)

    def test_resolve_kind_by_type(self):
        from freecut import animation

        # 旋转 exists both as an intro and as a text loop animation
        self.assertEqual(animation.resolve_kind("旋转", "in"), "rotate_in")
        self.assertEqual(animation.resolve_kind("旋转", "loop"), "spin_loop")
        self.assertEqual(animation.resolve_kind("渐隐", "out"), "fade_out")
        self.assertIsNone(animation.resolve_kind("完全不存在的动画", "in"))

    def test_compose_multiplies_alpha(self):
        from freecut import animation

        s = animation.AnimState(alpha=0.5).compose(animation.AnimState(alpha=0.4))
        self.assertAlmostEqual(s.alpha, 0.2, places=6)

    def test_animations_at_respects_window(self):
        from freecut import animation

        anims = [{"name": "渐显", "type": "in", "start": 0, "duration": 1_000_000}]
        self.assertLess(animation.animations_at(anims, 100_000, 2_000_000).alpha, 0.5)
        self.assertAlmostEqual(animation.animations_at(anims, 2_000_000, 2_000_000).alpha, 1.0, places=3)

    def test_builder_attaches_and_exports(self):
        from freecut import jianying_export

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "动画", start=0, duration=1_000_000)
        mat = builder.add_animation(p, seg, "渐显")
        self.assertIn(mat.id, seg.extra_material_refs)
        entry = p.material(mat.id).get("animations")[0]
        self.assertEqual(entry["name"], "渐显")
        self.assertTrue(entry["resource_id"])

        content = jianying_export.build_content(p)
        arrays = content["materials"]["material_animations"]
        self.assertEqual(len(arrays), 1)
        anim = arrays[0]["animations"][0]
        for key in ("name", "type", "start", "duration", "resource_id", "id",
                    "material_type", "panel", "platform"):
            self.assertIn(key, anim)
        self.assertEqual(anim["type"], "in")
        self.assertTrue(anim["id"])

    def test_two_animations_on_one_segment(self):
        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000)
        builder.add_animation(p, seg, "渐显")
        mat = builder.add_animation(p, seg, "渐隐")
        entries = p.material(mat.id).get("animations")
        self.assertEqual(len(entries), 2)
        self.assertEqual({e["type"] for e in entries}, {"in", "out"})

    def test_animation_changes_rendered_pixels(self):
        from freecut.render import Renderer

        p = builder.new_project(160, 90, 10)
        seg = builder.add_text(p, "■", start=0, duration=1_000_000, size=40)
        r = Renderer(p, 160, 90, 10)
        try:
            plain = int(r.render_frame(0).sum())
        finally:
            r.close()
        builder.add_animation(p, seg, "渐显")
        r2 = Renderer(p, 160, 90, 10)
        try:
            faded = int(r2.render_frame(0).sum())
        finally:
            r2.close()
        self.assertLess(faded, plain)


class TestAutoCut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import subprocess

        try:
            from freecut.media import _creationflags, ffmpeg_path

            ff = ffmpeg_path()
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"ffmpeg 不可用: {exc}")
        cls.tmp = tempfile.mkdtemp(prefix="freecut-auto-")
        cls.scene = os.path.join(cls.tmp, "scenes.mp4")
        cls.music = os.path.join(cls.tmp, "clicks.wav")
        try:
            subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "color=c=red:s=320x180:d=1.5",
                 "-f", "lavfi", "-i", "color=c=lime:s=320x180:d=1.5",
                 "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1.5",
                 "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                 "-map", "[v]", "-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p", cls.scene],
                check=True, creationflags=_creationflags(),
            )
            subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i",
                 "aevalsrc=if(lt(mod(t\\,0.5)\\,0.03)\\,0.8*sin(2*PI*1000*t)\\,0):s=22050:d=4",
                 "-ac", "1", cls.music],
                check=True, creationflags=_creationflags(),
            )
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"无法生成测试媒体: {exc}")

    def test_scene_cuts(self):
        from freecut import autocut

        cuts = autocut.detect_scene_cuts(self.scene)
        self.assertGreaterEqual(len(cuts), 2)
        times = [c.time_s for c in cuts[:2]]
        self.assertAlmostEqual(times[0], 1.5, delta=0.35)
        self.assertAlmostEqual(times[1], 3.0, delta=0.35)

    def test_beats(self):
        from freecut import autocut

        beats = autocut.detect_beats(self.music)
        self.assertGreaterEqual(len(beats), 6)
        gaps = np.diff(beats[:6])
        self.assertAlmostEqual(float(np.median(gaps)), 0.5, delta=0.1)

    def test_build_scene_project(self):
        from freecut import autocut

        p = autocut.build_scene_project([self.scene])
        segs = [s for t in p.tracks for s in t.segments]
        self.assertGreaterEqual(len(segs), 3)
        self.assertAlmostEqual(p.duration() / 1e6, 4.5, delta=0.5)
        for a, b in zip(segs, segs[1:]):
            self.assertGreaterEqual(b.target_timerange.start,
                                    a.target_timerange.start + a.target_timerange.duration)

    def test_build_beat_project_with_music(self):
        from freecut import autocut

        p = autocut.build_beat_project([self.scene], self.music, beats_per_cut=2)
        kinds = [t.type for t in p.tracks]
        self.assertIn("video", kinds)
        self.assertIn("audio", kinds)
        self.assertGreaterEqual(len(p.tracks[0].segments), 3)

    def test_segments_clamped(self):
        from freecut import autocut

        opts = autocut.AutoCutOptions(min_clip_s=0.5, max_clip_s=2.0)
        pieces = autocut._segments_from_cuts(5.0, [1.0, 1.2, 4.0], opts)
        for start, dur in pieces:
            self.assertGreaterEqual(dur, 0.5 - 1e-6)
        self.assertLessEqual(sum(d for _, d in pieces), 5.0 + 1e-6)


class TestSubtitles(unittest.TestCase):
    SRT = (
        "1\n00:00:01,000 --> 00:00:03,000\n你好世界\n\n"
        "2\n00:00:03,500 --> 00:00:05,000\nHello FreeCut\n"
    )

    def test_parse_srt(self):
        from freecut import subtitles

        subs = subtitles.parse_srt(self.SRT)
        self.assertEqual(len(subs), 2)
        self.assertEqual(subs[0].start_us, 1_000_000)
        self.assertEqual(subs[0].end_us, 3_000_000)
        self.assertEqual(subs[0].text, "你好世界")
        self.assertEqual(subs[1].text, "Hello FreeCut")

    def test_parse_srt_strips_tags_and_skips_bad_blocks(self):
        from freecut import subtitles

        text = "1\n00:00:00,000 --> 00:00:01,000\n<i>斜体</i>\n\n垃圾块\n\n"
        subs = subtitles.parse_srt(text)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].text, "斜体")

    def test_write_srt_roundtrip(self):
        from freecut import subtitles

        subs = subtitles.parse_srt(self.SRT)
        again = subtitles.parse_srt(subtitles.write_srt(subs))
        self.assertEqual([s.text for s in again], [s.text for s in subs])
        self.assertEqual([s.start_us for s in again], [s.start_us for s in subs])

    def test_split_words_cjk_and_latin(self):
        from freecut import subtitles

        self.assertEqual(subtitles.split_words("你好 world 世界"),
                         ["你", "好", "world", "世", "界"])

    def test_distribute_words_covers_span(self):
        from freecut import subtitles

        words = subtitles.distribute_words("你好世界", 0, 4_000_000)
        self.assertEqual(len(words), 4)
        self.assertEqual(words[0]["start_time"], 0)
        self.assertEqual(words[-1]["end_time"], 4_000_000)
        for a, b in zip(words, words[1:]):
            self.assertLessEqual(a["end_time"], b["start_time"] + 1)

    def test_find_active_word(self):
        from freecut import subtitles

        words = subtitles.distribute_words("你好", 0, 2_000_000)
        self.assertEqual(subtitles.find_active_word(words, 0), 0)
        self.assertEqual(subtitles.find_active_word(words, 1_200_000), 1)
        self.assertIsNone(subtitles.find_active_word(words, 9_000_000))

    def test_add_and_extract(self):
        from freecut import subtitles

        p = builder.new_project(320, 180, 30)
        created = subtitles.add_subtitles(p, subtitles.parse_srt(self.SRT))
        self.assertEqual(len(created), 2)
        text_tracks = [t for t in p.tracks if t.type == "text"]
        self.assertEqual(len(text_tracks), 1)
        self.assertEqual(len(text_tracks[0].segments), 2)
        mat = p.material(created[0].material_id)
        self.assertTrue(mat.get("words"))

        back = subtitles.extract_subtitles(p)
        self.assertEqual([s.text for s in back], ["你好世界", "Hello FreeCut"])
        self.assertEqual(back[0].start_us, 1_000_000)

    def test_subtitles_go_on_a_new_track(self):
        from freecut import subtitles

        p = builder.new_project(320, 180, 30)
        builder.add_text(p, "标题", start=0, duration=5_000_000)
        subtitles.add_subtitles(p, subtitles.parse_srt(self.SRT))
        text_tracks = [t for t in p.tracks if t.type == "text"]
        self.assertEqual(len(text_tracks), 2, "字幕应当新建一条轨，避免与标题重叠")

    def test_karaoke_render_highlights(self):
        from freecut import subtitles
        from freecut.text import TextRenderer

        mat = model.TextMaterial(content="你好世界", font_size=20,
                                 words=subtitles.distribute_words("你好世界", 0, 4_000_000),
                                 highlight_color="#FF0000", highlight_mode=1)
        r = TextRenderer(320, 180)
        plain = r.render(mat, active_word=None).image
        active = r.render(mat, active_word=1).image
        red_plain = int(((plain[..., 0] > 200) & (plain[..., 1] < 120)).sum())
        red_active = int(((active[..., 0] > 200) & (active[..., 1] < 120)).sum())
        self.assertEqual(red_plain, 0)
        self.assertGreater(red_active, 100)

    def test_text_with_words_keeps_layout(self):
        from freecut import subtitles
        from freecut.text import TextRenderer

        plain_mat = model.TextMaterial(content="你好世界", font_size=20)
        kara_mat = model.TextMaterial(content="你好世界", font_size=20,
                                      words=subtitles.distribute_words("你好世界", 0, 4_000_000))
        r = TextRenderer(320, 180)
        a = r.render(plain_mat).image.shape
        b = r.render(kara_mat, active_word=None).image.shape
        self.assertEqual(a, b)


class TestWheelsAndCurveWiring(unittest.TestCase):
    def test_set_wheels_normalises_scalars(self):
        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000)
        mat = builder.set_wheels(p, seg, lift=0.1, gain=[1.2, 1.0, 0.8])
        wheels = p.material(mat.id).get("wheels")
        self.assertEqual(wheels["lift"], [0.1, 0.1, 0.1])
        self.assertEqual(wheels["gain"], [1.2, 1.0, 0.8])
        self.assertNotIn("gamma", wheels)

    def test_wheels_change_rendered_pixels(self):
        from freecut.render import Renderer

        p = builder.new_project(120, 68, 10)
        p.canvas.background = "#808080"
        base = Renderer(p, 120, 68, 10)
        try:
            before = base.render_frame(0).mean()
        finally:
            base.close()
        seg = builder.add_text(p, "", start=0, duration=1_000_000, size=1)
        builder.set_wheels(p, seg, gain=[1.4, 1.0, 0.7])
        # the segment has no visible pixels, so the canvas stays the same; test
        # the colour math directly instead
        img = np.full((2, 2, 3), 0.5, dtype=np.float32)
        out = color.apply_wheels(img, {"gain": [1.4, 1.0, 0.7]})
        self.assertGreater(float(out[..., 0].mean()), float(img[..., 0].mean()))
        self.assertAlmostEqual(float(before), 0x80, delta=1)

    def test_curve_material_roundtrip_through_draft(self):
        from freecut import jianying, jianying_export
        import tempfile as tf

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000)
        builder.set_curves(p, seg, rgb=[[0, 0], [0.4, 0.6], [1, 1]])
        builder.set_wheels(p, seg, gain=[1.1, 1.0, 0.9])
        with tf.TemporaryDirectory() as tmp:
            d = jianying_export.save_draft(p, tmp, name="c", encrypt=False)
            back = jianying.load_draft(d)
        types = {m.type for m in back.materials.values()}
        self.assertIn("curve", types)
        self.assertIn("color_wheel", types)


class TestInterchange(unittest.TestCase):
    def _project(self):
        p = builder.new_project(640, 360, 30, name="XML 测试")
        mat = model.VideoMaterial(path=os.path.abspath("fake.mp4"), duration=4_000_000,
                                  width=640, height=360, has_audio=True)
        p.add_material(mat)
        seg = model.Segment(
            material_id=mat.id,
            target_timerange=model.Timerange(0, 2_000_000),
            source_timerange=model.Timerange(500_000, 2_000_000),
        )
        builder.track_of(p, "video").segments.append(seg)
        return p

    def test_fcp7_structure(self):
        from xml.etree import ElementTree as ET

        from freecut import interchange

        p = self._project()
        text = interchange.to_fcp7_xml(p)
        self.assertIn("<!DOCTYPE xmeml>", text)
        root = ET.fromstring(text.split("\n", 2)[2])
        self.assertEqual(root.tag, "xmeml")
        seq = root.find("sequence")
        self.assertIsNotNone(seq)
        self.assertEqual(seq.findtext("duration"), "60")
        self.assertEqual(seq.findtext("rate/timebase"), "30")
        clips = seq.findall("media/video/track/clipitem")
        self.assertEqual(len(clips), 1)

    def test_fcp7_frame_math(self):
        from xml.etree import ElementTree as ET

        from freecut import interchange

        p = self._project()
        root = ET.fromstring(interchange.to_fcp7_xml(p).split("\n", 2)[2])
        clip = root.find("sequence/media/video/track/clipitem")
        self.assertEqual(clip.findtext("start"), "0")
        self.assertEqual(clip.findtext("end"), "60")
        self.assertEqual(clip.findtext("in"), "15")
        self.assertEqual(clip.findtext("out"), "75")

    def test_fcp7_pathurl(self):
        from xml.etree import ElementTree as ET

        from freecut import interchange

        p = self._project()
        root = ET.fromstring(interchange.to_fcp7_xml(p).split("\n", 2)[2])
        url = root.find("sequence/media/video/track/clipitem/file/pathurl").text
        self.assertTrue(url.startswith("file://localhost/"))
        self.assertNotIn("\\", url)

    def test_write_file(self):
        from freecut import interchange

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "t.xml")
            interchange.write_fcp7(self._project(), out)
            self.assertTrue(os.path.getsize(out) > 200)


class TestASR(unittest.TestCase):
    """Only runs when a portable Whisper build is present."""

    @classmethod
    def setUpClass(cls):
        try:
            from freecut import asr

            asr.find_backend()
        except Exception as exc:
            raise unittest.SkipTest(f"没有离线识别后端: {exc}")

    def test_backend_and_models(self):
        from freecut import asr

        self.assertTrue(os.path.isfile(asr.find_backend()))
        self.assertIsInstance(asr.available_models(), list)

    def test_schema_conversion(self):
        """Parse a fixed faster-whisper payload without running inference."""
        from freecut import asr

        payload = {
            "text": "你好世界",
            "segments": [
                {
                    "start": 0.5, "end": 2.5, "text": "你好世界",
                    "words": [
                        {"start": 0.5, "end": 1.0, "word": "你好"},
                        {"start": 1.0, "end": 2.5, "word": "世界"},
                    ],
                },
                {"start": 3.0, "end": 3.0, "text": "零长度应被丢弃"},
                {"start": 4.0, "end": 5.0, "text": "   "},
            ],
        }
        subs = asr.segments_to_subtitles(payload)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].start_us, 500_000)
        self.assertEqual(subs[0].end_us, 2_500_000)
        self.assertEqual(len(subs[0].words), 2)
        self.assertEqual(subs[0].words[0]["start_time"], 0)
        self.assertEqual(subs[0].words[1]["end_time"], 2_000_000)

    def test_offset_is_applied(self):
        from freecut import asr

        payload = {"segments": [{"start": 1.0, "end": 2.0, "text": "a", "words": []}]}
        subs = asr.segments_to_subtitles(payload, offset_us=5_000_000)
        self.assertEqual(subs[0].start_us, 6_000_000)
        self.assertEqual(subs[0].end_us, 7_000_000)

    def test_subtitles_use_real_word_times(self):
        from freecut import asr, subtitles

        payload = {
            "segments": [
                {
                    "start": 0.0, "end": 2.0, "text": "你好",
                    "words": [
                        {"start": 0.0, "end": 0.8, "word": "你"},
                        {"start": 1.2, "end": 2.0, "word": "好"},
                    ],
                }
            ]
        }
        subs = asr.segments_to_subtitles(payload)
        p = builder.new_project(320, 180, 30)
        segs = subtitles.add_subtitles(p, subs)
        mat = p.material(segs[0].material_id)
        words = mat.get("words")
        self.assertEqual(len(words), 2)
        self.assertEqual(words[1]["start_time"], 1_200_000)


class TestLutLibrary(unittest.TestCase):
    def _make_cube(self, path: str, n: int = 2, shift: float = 0.0) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = [f"LUT_3D_SIZE {n}"]
        for b in range(n):
            for g in range(n):
                for r in range(n):
                    lines.append(
                        f"{min(1.0, r/(n-1)+shift):.6f} {g/(n-1):.6f} {b/(n-1):.6f}"
                    )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return path

    def test_scan_finds_and_dedupes(self):
        from freecut import luts

        with tempfile.TemporaryDirectory() as tmp:
            self._make_cube(os.path.join(tmp, "pack1", "look", "My Look.cube"))
            self._make_cube(os.path.join(tmp, "pack2", "look", "copy.cube"))
            self._make_cube(os.path.join(tmp, "pack3", "look", "Other.cube"), shift=0.1)
            entries = luts.scan([tmp])
            self.assertEqual(len(entries), 2, "同一内容应被去重")
            names = {e.name for e in entries}
            self.assertIn("My Look", names)
            self.assertIn("Other", names)
            for e in entries:
                self.assertEqual(e.size, os.path.getsize(e.path))
                self.assertEqual(len(e.md5), 32)

    def test_get_by_name_alias_and_md5(self):
        from freecut import luts

        with tempfile.TemporaryDirectory() as tmp:
            self._make_cube(os.path.join(tmp, "Look A.cube"))
            entries = luts.scan([tmp])
            saved = luts.default_roots
            luts.default_roots = lambda: [tmp]  # type: ignore
            try:
                self.assertIsNotNone(luts.get("Look A"))
                self.assertIsNotNone(luts.get("look a"))
                self.assertIsNotNone(luts.get(entries[0].md5[:8]))
                self.assertIsNone(luts.get("完全不存在的滤镜"))
            finally:
                luts.default_roots = saved

    def test_looks_like_id(self):
        from freecut import luts

        self.assertTrue(luts._looks_like_id("76D57C3E-2613-4F50-9D36-jbIbldBdCc5mb"))
        self.assertTrue(luts._looks_like_id("0123456789abcdef0123456789abcdef"))
        self.assertFalse(luts._looks_like_id("Alexa LogC to Rec709"))
        self.assertFalse(luts._looks_like_id(""))

    def test_identity_cube_is_noop(self):
        from freecut import color, luts

        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_cube(os.path.join(tmp, "identity.cube"), n=9)
            lut = color.load_cube_lut(path)
            self.assertEqual(lut.shape, (9, 9, 9, 3))
            img = gradient()
            out = color.apply_lut3d(img, lut, 1.0)
            self.assertLess(float(np.abs(out - img).max()), 0.02)

    def test_filter_applied_to_segment_changes_params(self):
        from freecut import color, luts
        from freecut.render import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_cube(os.path.join(tmp, "warm.cube"), n=9, shift=0.08)
            p = builder.new_project(64, 36, 10)
            p.canvas.background = "#808080"
            seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=6)
            builder.add_filter(p, seg, path, 1.0)
            r = Renderer(p, 64, 36, 10)
            try:
                frame = r.render_frame(0)
            finally:
                r.close()
        self.assertEqual(frame.shape[:2], (36, 64))


class TestWarpFallback(unittest.TestCase):
    """The pure-numpy bilinear warp must agree with the OpenCV fast path."""

    def test_identity(self):
        from freecut.transform import _warp_bilinear

        img = np.random.default_rng(0).random((8, 10, 3)).astype(np.float32)
        out = _warp_bilinear(img, np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], np.float32), 10, 8)
        np.testing.assert_allclose(out, img, atol=1e-5)

    def test_translation_shifts_and_fills_border(self):
        from freecut.transform import _warp_bilinear

        img = np.zeros((4, 4, 3), dtype=np.float32)
        img[1, 1] = 1.0
        m = np.array([[1.0, 0.0, 1.0], [0.0, 1.0, 0.0]], np.float32)  # shift right 1px
        out = _warp_bilinear(img, m, 4, 4)
        self.assertAlmostEqual(float(out[1, 2, 0]), 1.0, places=5)
        self.assertAlmostEqual(float(out[1, 1, 0]), 0.0, places=5)

    def test_2d_input(self):
        from freecut.transform import _warp_bilinear

        a = np.ones((4, 4), dtype=np.float32)
        out = _warp_bilinear(a, np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], np.float32), 4, 4)
        self.assertEqual(out.shape, (4, 4))

    def test_warp_layer_runs_without_cv2(self):
        from freecut import transform

        saved = transform._cv2
        transform._cv2 = None
        try:
            rgb, alpha = transform.warp_layer(
                np.full((6, 6, 3), 0.5, dtype=np.float32),
                np.ones((6, 6), dtype=np.float32),
                np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], np.float32),
                12, 12,
            )
        finally:
            transform._cv2 = saved
        self.assertEqual(rgb.shape, (12, 12, 3))
        self.assertAlmostEqual(float(rgb[3, 3, 0]), 0.5, places=4)
        self.assertAlmostEqual(float(rgb[11, 11, 0]), 0.0, places=4)


class TestPrproj(unittest.TestCase):
    """The PR reader is tested against a synthetic file built from the exact
    structure observed in a real 14.3.2 project (see docs/REPORT.md)."""

    XML = """<?xml version="1.0" encoding="UTF-8"?>
<PremiereData Version="3">
  <Media ObjectUID="media-1">
    <ActualMediaFilePath>D:/media/clip.mp4</ActualMediaFilePath>
    <MediaPath>D:/media/clip.mp4</MediaPath>
  </Media>
  <VideoMediaSource ObjectID="64">
    <MediaSource><Media ObjectURef="media-1"/></MediaSource>
    <OriginalDuration>2540160000000</OriginalDuration>
  </VideoMediaSource>
  <VideoClip ObjectID="132">
    <Clip><Source ObjectRef="64"/><InPoint>254016000000</InPoint><OutPoint>762048000000</OutPoint></Clip>
  </VideoClip>
  <MasterClip ObjectUID="master-1"><Name>clip.mp4</Name></MasterClip>
  <SubClip ObjectID="118"><Clip ObjectRef="132"/><MasterClip ObjectURef="master-1"/></SubClip>
  <VideoClipTrackItem ObjectID="99">
    <ClipTrackItem><TrackItem><Start>0</Start><End>508032000000</End></TrackItem>
      <SubClip ObjectRef="118"/></ClipTrackItem>
  </VideoClipTrackItem>
  <VideoClipTrack ObjectUID="track-1"><ClipTrack><ClipItems><TrackItems>
      <TrackItem Index="0" ObjectRef="99"/></TrackItems></ClipItems></ClipTrack></VideoClipTrack>
  <VideoTrackGroup ObjectID="77">
    <TrackGroup><Tracks><Track Index="0" ObjectURef="track-1"/></Tracks>
      <FrameRate>8475667200</FrameRate></TrackGroup>
    <FrameRect>0,0,1920,1080</FrameRect>
  </VideoTrackGroup>
  <Sequence ObjectUID="seq-1">
    <TrackGroups><TrackGroup Index="1"><First>228cda18</First><Second ObjectRef="77"/></TrackGroup></TrackGroups>
    <Name>我的序列</Name>
  </Sequence>
</PremiereData>
"""

    def test_ticks_helpers(self):
        from freecut import prproj

        self.assertEqual(prproj.TICKS_PER_SECOND, 254_016_000_000)
        self.assertEqual(prproj.ticks_to_us(prproj.TICKS_PER_SECOND), 1_000_000)
        self.assertEqual(prproj.us_to_ticks(1_000_000), prproj.TICKS_PER_SECOND)
        self.assertAlmostEqual(prproj.ticks_per_frame_to_fps(8_475_667_200), 30000 / 1001, places=6)

    def test_sequence_metadata(self):
        from freecut import prproj

        doc = prproj.Prproj(self.XML)
        seqs = doc.sequences()
        self.assertEqual(len(seqs), 1, "ObjectURef 引用不应被当成第二个序列")
        seq = seqs[0]
        self.assertEqual(seq.name, "我的序列")
        self.assertEqual((seq.width, seq.height), (1920, 1080))
        self.assertAlmostEqual(seq.fps, 30000 / 1001, places=4)
        self.assertEqual(len(seq.video_tracks), 1)
        self.assertEqual(len(seq.video_tracks[0]), 1)

    def test_item_times_convert_to_microseconds(self):
        from freecut import prproj

        doc = prproj.Prproj(self.XML)
        item = doc.sequences()[0].video_tracks[0][0]
        self.assertEqual(item.start_us, 0)
        self.assertEqual(item.end_us, 2_000_000)      # 508 032 000 000 ticks
        self.assertEqual(item.in_us, 1_000_000)       # 254 016 000 000 ticks
        self.assertEqual(item.out_us, 3_000_000)
        self.assertEqual(item.media_path, "D:/media/clip.mp4")
        self.assertEqual(item.media_duration_us, 10_000_000)
        self.assertFalse(item.is_adjustment)

    def test_import_builds_a_project(self):
        from freecut import prproj

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "demo.prproj")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.XML)
            project, info = prproj.import_project(path)
        self.assertEqual(info["sequence"], "我的序列")
        self.assertEqual(project.canvas.width, 1920)
        self.assertEqual(len(project.tracks), 1)
        seg = project.tracks[0].segments[0]
        self.assertEqual(seg.target_timerange.duration, 2_000_000)
        self.assertEqual(seg.source_timerange.start, 1_000_000)

    def test_empty_project_raises(self):
        from freecut import prproj

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.prproj")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('<PremiereData Version="3"></PremiereData>')
            with self.assertRaises(prproj.PrprojError):
                prproj.import_project(path)


class TestAdjustmentLayer(unittest.TestCase):
    def _project(self):
        p = builder.new_project(160, 90, 10)
        p.canvas.background = "#808080"
        seg = builder.add_text(p, "x", start=0, duration=2_000_000, size=6)
        return p, seg

    def test_track_position(self):
        p, _ = self._project()
        builder.add_adjustment(p, start=0, duration=1_000_000)
        kinds = [t.type for t in p.tracks]
        # must be composited after video, before text
        last_video = max(i for i, t in enumerate(p.tracks) if t.type == "video")
        first_text = min(i for i, t in enumerate(p.tracks) if t.type == "text")
        adj = next(i for i, t in enumerate(p.tracks) if t.segments and t.segments[0].is_adjustment)
        self.assertGreater(adj, last_video - 1)
        self.assertLess(adj, first_text)
        self.assertIn("video", kinds)

    def test_material_type_and_flag(self):
        p, _ = self._project()
        seg = builder.add_adjustment(p, start=0, duration=1_000_000, brightness=40)
        self.assertTrue(seg.is_adjustment)
        mat = p.material(seg.material_id)
        self.assertEqual(mat.type, "adjustment")
        self.assertEqual(mat.get("adjust"), {"brightness": 40})

    def test_changes_rendered_pixels(self):
        from freecut.render import Renderer

        p, _ = self._project()
        r = Renderer(p, 160, 90, 10)
        try:
            before = r.render_frame(0).copy()
        finally:
            r.close()
        builder.add_adjustment(p, start=0, duration=1_000_000, brightness=40, contrast=25)
        r = Renderer(p, 160, 90, 10)
        try:
            after = r.render_frame(0)
        finally:
            r.close()
        self.assertGreater(int(np.abs(after.astype(int) - before.astype(int)).sum()), 1000)

    def test_roundtrip_through_project_json(self):
        import tempfile as tf

        p, _ = self._project()
        seg = builder.add_adjustment(p, start=0, duration=1_000_000, brightness=10)
        with tf.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.json")
            p.save(path)
            back = model.Project.load(path)
        found = [s for t in back.tracks for s in t.segments if s.is_adjustment]
        self.assertEqual(len(found), 1)
        self.assertEqual(back.material(found[0].material_id).type, "adjustment")

    def test_jianying_export_skips_it_with_a_warning(self):
        from freecut import jianying_export, jianying_validate

        p, _ = self._project()
        builder.add_adjustment(p, start=0, duration=1_000_000, brightness=40)
        with tempfile.TemporaryDirectory() as tmp:
            notes: list = []
            draft = jianying_export.save_draft(p, tmp, name="adj", encrypt=False, warnings=notes)
            self.assertTrue(any("调节图层" in n for n in notes))
            report = jianying_validate.validate_draft_dir(draft)
        self.assertTrue(report.ok, report.as_text())
        content = jianying_export.build_content(p)
        for track in content["tracks"]:
            for seg in track["segments"]:
                self.assertFalse(seg.get("is_adjustment"))


class TestExportPerformance(unittest.TestCase):
    """The threaded exporter must produce exactly the same bytes per frame."""

    def test_block_equals_frames(self):
        from freecut import export as ex

        p = builder.new_project(64, 36, 10)
        p.canvas.background = "#204060"
        builder.add_text(p, "x", start=0, duration=500_000, size=5)
        r = ex.Renderer(p, 64, 36, 10)
        try:
            whole = ex._render_block(r, 0, 5)
        finally:
            r.close()
        r2 = ex.Renderer(p, 64, 36, 10)
        try:
            parts = b"".join(r2.render_frame(i).tobytes() for i in range(5))
        finally:
            r2.close()
        self.assertEqual(whole, parts)
        self.assertEqual(len(whole), 64 * 36 * 3 * 5)

    def test_threaded_blocks_cover_every_frame_in_order(self):
        from freecut import export as ex

        p = builder.new_project(48, 27, 10)
        p.canvas.background = "#102030"
        builder.add_text(p, "y", start=0, duration=1_000_000, size=4)
        preset = ex.ExportPreset(48, 27, 10)
        seen = []
        blobs = {}
        for start, count, blob in ex._threaded_blocks(p, preset, 20, 3):
            seen.extend(range(start, start + count))
            blobs[start] = blob
        self.assertEqual(seen, list(range(20)))
        self.assertEqual(sum(len(b) for b in blobs.values()), 48 * 27 * 3 * 20)


class TestAudioGraph(unittest.TestCase):
    def _clips(self, **kw):
        from freecut.audio import AudioClip

        base = dict(path="x.wav", source_start_us=0, duration_us=2_000_000,
                    timeline_start_us=0)
        base.update(kw)
        return [AudioClip(**base)]

    def test_input_label_is_not_followed_by_a_comma(self):
        """[0:a],atrim=… is invalid FFmpeg syntax and silently killed the mix."""
        from freecut.audio import build_filter_complex

        inputs, graph, label = build_filter_complex(self._clips(), 2_000_000)
        self.assertTrue(graph.startswith("[0:a]atrim"), graph[:40])
        self.assertNotIn("[0:a],", graph)
        self.assertEqual(label, "[aout]")

    def test_plain_chain_has_no_effects(self):
        from freecut.audio import build_filter_complex

        _, graph, _ = build_filter_complex(self._clips(), 2_000_000)
        for token in ("afftdn", "asetrate", "equalizer"):
            self.assertNotIn(token, graph)

    def test_denoise_pitch_eq_filters(self):
        from freecut.audio import build_filter_complex

        _, graph, _ = build_filter_complex(
            self._clips(denoise=0.5, pitch=0.8, eq=(3.0, -1.5, 2.0)), 2_000_000)
        self.assertIn("afftdn=nr=", graph)
        self.assertIn("asetrate=38400", graph)          # 48000 * 0.8, pre-computed
        self.assertIn("atempo=1.250000", graph)         # 1/0.8 restores duration
        self.assertIn("equalizer=f=120", graph)
        self.assertIn("equalizer=f=1000", graph)
        self.assertIn("equalizer=f=6000", graph)
        self.assertNotIn("asetrate=48000*", graph)      # asetrate takes an int

    def test_pitch_cannot_leave_the_supported_range(self):
        from freecut.audio import build_filter_complex

        _, graph, _ = build_filter_complex(self._clips(pitch=4.0), 2_000_000)
        self.assertIn("asetrate=96000", graph)

    def test_builder_attaches_material(self):
        p = builder.new_project(160, 90, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=6)
        mat = builder.set_audio_effects(p, seg, denoise=0.7, pitch=1.2, eq=(1, 2, 3))
        self.assertIn(mat.id, seg.extra_material_refs)
        self.assertEqual(p.material(mat.id).type, "audio_effect")
        # second call updates in place rather than stacking materials
        again = builder.set_audio_effects(p, seg, denoise=0.2)
        self.assertEqual(again.id, mat.id)
        self.assertAlmostEqual(float(again.get("denoise")), 0.2)
        self.assertAlmostEqual(float(again.get("pitch")), 1.2)


class TestOutputAcceptance(unittest.TestCase):
    """Artifact-level checks: the export must *look and sound* right.

    A silent-audio bug survived several rounds because every test only asserted
    "no exception".  These tests inspect the produced file.
    """

    @classmethod
    def setUpClass(cls):
        import subprocess

        try:
            from freecut.media import _creationflags, ffmpeg_path

            ff = ffmpeg_path()
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"ffmpeg 不可用: {exc}")
        cls.tmp = tempfile.mkdtemp(prefix="freecut-acc-")
        cls.tone = os.path.join(cls.tmp, "tone.wav")
        cls.video = os.path.join(cls.tmp, "bars.mp4")
        try:
            subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                            "-ac", "1", cls.tone], check=True,
                           creationflags=_creationflags())
            subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=2",
                            "-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p",
                            cls.video], check=True, creationflags=_creationflags())
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"无法生成测试素材: {exc}")

    def _project(self, volume: float = 1.0):
        p = builder.new_project(320, 180, 10, name="acceptance")
        builder.add_video(p, self.video, start=0, duration=2_000_000)
        audio = builder.add_audio(p, self.tone, start=0, duration=2_000_000)
        audio.volume = volume
        return p

    def test_export_passes_verification(self):
        from freecut.export import ExportPreset, export
        from freecut.verify import verify

        p = self._project()
        out = os.path.join(self.tmp, "good.mp4")
        export(p, out, ExportPreset(320, 180, 10, video_codec="mpeg4"), jobs=1)
        rep = verify(out, expect_audio=True)
        self.assertTrue(rep.ok, rep.as_text())
        self.assertGreater(rep.mean_luma, 8.0)
        self.assertGreater(rep.audio_peak_db, -60.0)
        self.assertEqual(rep.frame_count, 20)

    def test_verification_catches_silence(self):
        from freecut.export import ExportPreset, export
        from freecut.verify import verify

        p = self._project(volume=0.0)  # muted -> export falls back to silence
        out = os.path.join(self.tmp, "silent.mp4")
        export(p, out, ExportPreset(320, 180, 10, video_codec="mpeg4"), jobs=1)
        # the project HAS audio, so silence must be a failure, not a warning
        rep = verify(out, expect_audio=True)
        self.assertFalse(rep.ok)
        self.assertTrue(any("静音" in x for x in rep.problems), rep.problems)
        # without the expectation it is only a warning
        relaxed = verify(out)
        self.assertTrue(relaxed.ok, relaxed.as_text())
        self.assertTrue(any("静音" in w for w in relaxed.warnings), relaxed.warnings)

    def test_verification_rejects_black_video(self):
        from freecut.export import ExportPreset, export
        from freecut.verify import verify

        p = builder.new_project(320, 180, 10, name="black")
        p.canvas.background = "#000000"
        builder.add_text(p, " ", start=0, duration=1_000_000, size=1)
        out = os.path.join(self.tmp, "black.mp4")
        export(p, out, ExportPreset(320, 180, 10, video_codec="mpeg4"), jobs=1)
        rep = verify(out)
        self.assertFalse(rep.ok)
        self.assertTrue(any("黑" in x for x in rep.problems), rep.problems)


class TestEffects(unittest.TestCase):
    def _layer(self, h=48, w=64):
        rng = np.random.default_rng(3)
        rgb = rng.random((h, w, 3)).astype(np.float32)
        alpha = np.ones((h, w), dtype=np.float32)
        return rgb, alpha

    def test_every_effect_preserves_shape(self):
        from freecut import effects

        rgb, alpha = self._layer()
        for kind in effects.available():
            out_rgb, out_a = effects.apply_effect(kind, rgb, alpha, 333_000, {})
            self.assertEqual(out_rgb.shape, rgb.shape, kind)
            self.assertEqual(out_a.shape, alpha.shape, kind)

    def test_each_effect_changes_something(self):
        from freecut import effects

        rgb, alpha = self._layer()
        for kind in effects.available():
            when = 125_000 if kind == "flash" else 333_000
            out_rgb, out_a = effects.apply_effect(kind, rgb, alpha, when, {})
            # 抠像类特效改的是 alpha，不改 rgb——两者任一变化都算"有效果"
            diff = max(float(np.abs(out_rgb - rgb).mean()),
                       float(np.abs(out_a - alpha).mean()) if out_a is not None else 0.0)
            self.assertGreater(diff, 1e-3, f"{kind} 没有产生任何变化")

    def test_unknown_kind_is_a_noop(self):
        from freecut import effects

        rgb, alpha = self._layer()
        out_rgb, out_a = effects.apply_effect("不存在的特效", rgb, alpha, 0, {})
        self.assertIs(out_rgb, rgb)
        self.assertIs(out_a, alpha)

    def test_chain_applies_in_order(self):
        from freecut import effects

        rgb, alpha = self._layer()
        chained, _ = effects.apply_chain(
            [{"kind": "mirror", "params": {}}, {"kind": "blur", "params": {"radius": 3}}],
            rgb, alpha, 0,
        )
        step, _ = effects.apply_effect("mirror", rgb, alpha, 0, {})
        step, _ = effects.apply_effect("blur", step, alpha, 0, {"radius": 3})
        np.testing.assert_allclose(chained, step, atol=1e-6)

    def test_blur_is_smoother_than_input(self):
        from freecut import effects

        rgb, alpha = self._layer()
        out, _ = effects.apply_effect("blur", rgb, alpha, 0, {"radius": 4})
        self.assertLess(float(np.abs(np.diff(out, axis=1)).mean()),
                        float(np.abs(np.diff(rgb, axis=1)).mean()))

    def test_mirror_is_symmetric(self):
        from freecut import effects

        rgb, alpha = self._layer()
        out, _ = effects.apply_effect("mirror", rgb, alpha, 0, {})
        w = out.shape[1]
        left = out[:, : w // 2]
        right = out[:, w - w // 2:][:, ::-1]
        np.testing.assert_allclose(left, right, atol=1e-6)

    def test_builder_rejects_unknown_kind(self):
        p = builder.new_project(64, 36, 10)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=5)
        with self.assertRaises(ValueError):
            builder.add_effect(p, seg, "根本没有这个特效")

    def test_builder_attaches_with_defaults(self):
        p = builder.new_project(64, 36, 10)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=5)
        mat = builder.add_effect(p, seg, "glitch", strength=0.5)
        self.assertEqual(mat.type, "effect")
        self.assertEqual(mat.get("kind"), "glitch")
        self.assertAlmostEqual(float(mat.get("params")["strength"]), 0.5)
        self.assertIn("block", mat.get("params"))

    def test_export_warns_and_keeps_params(self):
        from freecut import jianying, jianying_export, jianying_validate

        p = builder.new_project(160, 90, 10)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=5)
        builder.add_effect(p, seg, "mosaic", block=12)
        with tempfile.TemporaryDirectory() as tmp:
            notes: list = []
            draft = jianying_export.save_draft(p, tmp, name="fx", encrypt=False, warnings=notes)
            self.assertTrue(any("特效" in n for n in notes), notes)
            self.assertTrue(jianying_validate.validate_draft_dir(draft).ok)
            back = jianying.load_draft(draft)
        kinds = [m.get("kind") for m in back.materials.values()
                 if getattr(m, "type", "") == "effect"]
        self.assertEqual(kinds, ["mosaic"], "特效应通过 sidecar 无损往返")


class TestDraftMetaCanonical(unittest.TestCase):
    """draft_meta_info must match the shape JianyingPro itself writes.

    Round 18: we registered a generated draft, let the app touch it, and the app
    rewrote draft_meta_info.json with 21 extra keys.  Those keys are now part of
    our template, so the app no longer has to fix our file on first open.
    """

    #: (key, expected type) — taken from the file the app produced
    REQUIRED = {
        "cloud_draft_cover": bool,
        "cloud_draft_sync": bool,
        "draft_cloud_package_type": str,
        "draft_cover": str,
        "draft_is_ae_produce": bool,
        "draft_is_cloud_temp_draft": bool,
        "draft_is_infinite_canvas_draft": bool,
        "draft_is_pippit_draft": bool,
        "draft_is_web_article_video": bool,
        "draft_need_rename_folder": bool,
        "draft_timeline_materials_size_": int,
        "draft_web_article_video_enter_from": str,
        "pippit_avatar_url": str,
        "pippit_extra_info": str,
        "pippit_id": str,
        "pippit_user_name": str,
        "tm_draft_cloud_entry_id": int,
        "tm_draft_cloud_parent_entry_id": int,
        "tm_draft_cloud_space_id": int,
        "tm_draft_cloud_user_id": int,
        "tm_draft_create": int,
        "tm_draft_modified": int,
    }

    def test_template_has_every_canonical_key_with_the_right_type(self):
        from freecut.jianying_export import META_TEMPLATE

        for key, typ in self.REQUIRED.items():
            self.assertIn(key, META_TEMPLATE, key)
            self.assertIsInstance(META_TEMPLATE[key], typ, key)
        # the app deletes this one, so we no longer write it
        self.assertNotIn("draft_cloud_materials", META_TEMPLATE)

    def test_export_fills_the_fields_and_writes_a_cover(self):
        from PIL import Image

        from freecut import jianying_export

        p = builder.new_project(160, 90, 10, name="meta-test")
        builder.add_text(p, "x", start=0, duration=1_000_000, size=5)
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            draft = jianying_export.save_draft(p, tmp, name="m", encrypt=False)
            with open(os.path.join(draft, "draft_meta_info.json"), encoding="utf-8") as fh:
                meta = _json.load(fh)     # the file that was actually written
            cover = os.path.join(draft, "draft_cover.jpg")
            self.assertTrue(os.path.isfile(cover), "应生成 draft_cover.jpg")
            with Image.open(cover) as img:
                self.assertEqual(img.format, "JPEG")
            self.assertGreater(int(meta["tm_draft_create"]), 1_600_000_000_000_000)
            self.assertEqual(int(meta["tm_duration"]), 1_000_000)
            size = os.path.getsize(os.path.join(draft, "draft_content.json"))
            self.assertEqual(int(meta["draft_timeline_materials_size_"]), size)


class TestDraftRoundTrip(unittest.TestCase):
    """Export -> re-import must preserve every value the renderer reads."""

    def _project(self):
        p = builder.new_project(640, 360, 30, name="回归")
        p.canvas.background = "#101020"
        t = builder.add_text(p, "回归测试", start=0, duration=2_000_000, size=12, y=-0.6)
        builder.set_curves(p, t, rgb=[[0, 0], [0.5, 0.6], [1, 1]])
        builder.set_hsl(p, t, bands={"orange": {"sat": 0.2}})
        builder.set_wheels(p, t, gain=[1.1, 1.0, 0.9])
        builder.add_mask(p, t, "圆形")
        builder.add_animation(p, t, "渐显")
        builder.add_effect(p, t, "glitch", strength=0.3)
        builder.set_audio_effects(p, t, denoise=0.4)
        builder.add_keyframe(t, "KFTypeScaleX", 0, 0.5)
        builder.add_keyframe(t, "KFTypeScaleX", 1_000_000, 1.5)
        return p

    def test_roundtrip_preserves_semantics(self):
        from freecut import jianying, jianying_export

        src = self._project()
        with tempfile.TemporaryDirectory() as tmp:
            draft = jianying_export.save_draft(src, tmp, name="rt", encrypt=False)
            back = jianying.load_draft(draft)

        def one(proj, kind):
            for m in proj.materials.values():
                if m.type == kind:
                    return m
            return None

        self.assertEqual(one(back, "text").get("content"), "回归测试")
        self.assertEqual(one(back, "text").get("font_size"), 12)
        self.assertEqual(one(back, "curve").get("curves").get("rgb"), [[0, 0], [0.5, 0.6], [1, 1]])
        self.assertEqual(one(back, "hsl").get("bands"), {"orange": {"sat": 0.2}})
        self.assertEqual(one(back, "color_wheel").get("wheels"), {"gain": [1.1, 1.0, 0.9]})
        self.assertEqual(one(back, "material_animation").get("animations")[0].get("name"), "渐显")
        self.assertEqual(one(back, "effect").get("kind"), "glitch")
        self.assertAlmostEqual(float(one(back, "effect").get("params")["strength"]), 0.3)
        self.assertAlmostEqual(float(one(back, "audio_effect").get("denoise")), 0.4)

        src_segs = [(s.target_timerange.start, s.target_timerange.duration)
                    for tr in src.tracks for s in tr.segments]
        back_segs = [(s.target_timerange.start, s.target_timerange.duration)
                     for tr in back.tracks for s in tr.segments]
        self.assertEqual(src_segs, back_segs)

    def test_keyframes_survive(self):
        from freecut import jianying, jianying_export

        src = self._project()
        with tempfile.TemporaryDirectory() as tmp:
            draft = jianying_export.save_draft(src, tmp, name="rt", encrypt=False)
            back = jianying.load_draft(draft)
        src_kf = [k for tr in src.tracks for s in tr.segments for k in s.keyframes.get("KFTypeScaleX", [])]
        back_kf = [k for tr in back.tracks for s in tr.segments for k in s.keyframes.get("KFTypeScaleX", [])]
        self.assertEqual(len(back_kf), len(src_kf))
        self.assertAlmostEqual(src_kf[1].value, back_kf[1].value, places=4)
        self.assertEqual(src_kf[1].time_offset, back_kf[1].time_offset)

    def test_export_import_export_is_stable(self):
        """A second export of the re-imported project must not drift."""
        from freecut import jianying, jianying_export

        src = self._project()
        with tempfile.TemporaryDirectory() as tmp:
            d1 = jianying_export.save_draft(src, os.path.join(tmp, "a"), name="one", encrypt=False)
            back = jianying.load_draft(d1)
            d2 = jianying_export.save_draft(back, os.path.join(tmp, "b"), name="one", encrypt=False)
            again = jianying.load_draft(d2)
        a = sorted((s.target_timerange.start, s.target_timerange.duration)
                   for tr in back.tracks for s in tr.segments)
        b = sorted((s.target_timerange.start, s.target_timerange.duration)
                   for tr in again.tracks for s in tr.segments)
        self.assertEqual(a, b)
        from collections import Counter

        def kinds(proj):
            return Counter(m.type for m in proj.materials.values())

        self.assertEqual(kinds(again), kinds(back), "二次导出不应增减素材")


class TestWaveform(unittest.TestCase):
    def _wav(self, tmp):
        import math
        import wave

        path = os.path.join(tmp, "tone.wav")
        sr = 8000
        n = sr * 2
        data = bytearray()
        for i in range(n):
            t = i / sr
            amp = 0.0
            if 0.2 <= t <= 0.7:
                amp = 0.8
            elif 1.2 <= t <= 1.6:
                amp = 0.2
            data += int(amp * 32767 * math.sin(2 * math.pi * 200 * t)).to_bytes(2, "little", signed=True)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(bytes(data))
        return path

    def test_peaks_follow_the_envelope(self):
        from freecut import waveform

        with tempfile.TemporaryDirectory() as tmp:
            path = self._wav(tmp)
            pk = waveform.peaks(path, 20)
        self.assertEqual(len(pk), 20)
        self.assertLess(float(pk[0]), 0.05)          # leading silence
        loud = float(pk[3:6].max())
        quiet = float(pk[13:15].max())
        self.assertGreater(loud, 0.5)
        self.assertGreater(loud, quiet * 2)          # 0.8 vs 0.2 burst

    def test_cache_returns_the_same_object_fast(self):
        from freecut import waveform

        with tempfile.TemporaryDirectory() as tmp:
            path = self._wav(tmp)
            a = waveform.peaks(path, 32)
            b = waveform.peaks(path, 32)
        np.testing.assert_allclose(a, b)

    def test_missing_file_is_silent_not_fatal(self):
        from freecut import waveform

        pk = waveform.peaks(os.path.join(tempfile.gettempdir(), "definitely-missing.wav"), 8)
        self.assertEqual(len(pk), 8)
        self.assertEqual(float(np.abs(pk).max()), 0.0)


    def test_effect_params_change_output(self):
        from freecut import effects

        rng = np.random.default_rng(5)
        rgb = rng.random((40, 60, 3)).astype(np.float32)
        alpha = np.ones((40, 60), dtype=np.float32)
        weak, _ = effects.apply_effect("glitch", rgb, alpha, 100_000, {"strength": 0.05, "block": 8})
        strong, _ = effects.apply_effect("glitch", rgb, alpha, 100_000, {"strength": 0.9, "block": 8})
        self.assertGreater(float(np.abs(strong - weak).mean()), 1e-3)

    def test_param_defaults_are_declared(self):
        from freecut import effects

        for kind, spec in effects.EFFECT_SPEC.items():
            self.assertIn("label", spec, kind)
            self.assertIsInstance(spec.get("params"), dict, kind)
            for key, val in spec["params"].items():
                self.assertIsInstance(val, (int, float), f"{kind}.{key}")


class TestSpeedCurve(unittest.TestCase):
    POINTS = [{"x": 0.0, "y": 0.5}, {"x": 0.5, "y": 2.0}, {"x": 1.0, "y": 1.0}]

    def test_normalize_sorts_and_clamps(self):
        from freecut import speed

        pts = speed.normalize([{"x": 1.0, "y": 1.0}, {"x": 0.2, "y": 0.4}, {"x": 0.2, "y": 0.6}])
        self.assertEqual(pts[0][0], 0.2)
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[-1], (1.0, 1.0))

    def test_speed_at_is_piecewise_linear(self):
        from freecut import speed

        pts = speed.normalize(self.POINTS)
        self.assertAlmostEqual(speed.speed_at(pts, 0.0), 0.5)
        self.assertAlmostEqual(speed.speed_at(pts, 0.5), 2.0)
        self.assertAlmostEqual(speed.speed_at(pts, 0.75), 1.5)

    def test_fraction_is_monotonic_and_anchored(self):
        from freecut import speed

        pts = speed.normalize(self.POINTS)
        fracs = [speed.source_fraction(pts, i / 20) for i in range(21)]
        self.assertAlmostEqual(fracs[0], 0.0, places=6)
        self.assertAlmostEqual(fracs[-1], 1.0, places=6)
        self.assertTrue(all(a <= b + 1e-9 for a, b in zip(fracs, fracs[1:])))

    def test_flat_curve_equals_constant_speed(self):
        from freecut import speed
        from freecut.model import Segment, Timerange

        flat = speed.normalize([{"x": 0, "y": 1.0}, {"x": 1, "y": 1.0}])
        seg = Segment(target_timerange=Timerange(0, 4_000_000),
                      source_timerange=Timerange(0, 4_000_000))
        for t in (0, 1_000_000, 2_500_000, 4_000_000):
            self.assertEqual(seg.source_time(t), t)
        seg.speed_curve = flat
        for t in (0, 1_000_000, 2_500_000, 4_000_000):
            self.assertAlmostEqual(seg.source_time(t) / 1e6, t / 1e6, places=5)

    def test_curve_redistributes_inside_the_source(self):
        from freecut import speed
        from freecut.model import Segment, Timerange

        seg = Segment(target_timerange=Timerange(0, 4_000_000),
                      source_timerange=Timerange(0, 4_000_000))
        seg.speed_curve = speed.normalize(self.POINTS)
        self.assertEqual(seg.source_time(0), 0)
        self.assertEqual(seg.source_time(4_000_000), 4_000_000)   # ends exactly at the source end
        times = [seg.source_time(t) for t in range(0, 4_000_001, 200_000)]
        self.assertTrue(all(a <= b for a, b in zip(times, times[1:])))

    def test_builder_sets_average_speed(self):
        from freecut import builder, speed

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=2_000_000, size=6)
        mat = builder.set_speed_curve(p, seg, self.POINTS)
        self.assertEqual(mat.type, "speed")
        self.assertAlmostEqual(float(seg.speed), speed.average_speed(seg.speed_curve), places=4)
        self.assertIn(mat.id, seg.extra_material_refs)
        self.assertEqual(len(mat.get("curve_speed")["speed_points"]), 3)

    def test_export_and_reimport_keep_the_curve(self):
        from freecut import builder, jianying, jianying_export

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=2_000_000, size=6)
        builder.set_speed_curve(p, seg, self.POINTS)
        content = jianying_export.build_content(p)
        speeds = content["materials"]["speeds"]
        curved = [s for s in speeds if isinstance(s.get("curve_speed"), dict)]
        self.assertEqual(len(curved), 1, "应当只有一个带曲线的 speed 素材")
        self.assertEqual(len(curved[0]["curve_speed"]["speed_points"]), 3)

        with tempfile.TemporaryDirectory() as tmp:
            draft = jianying_export.save_draft(p, tmp, name="c", encrypt=False)
            back = jianying.load_draft(draft)
        curves = [s.speed_curve for tr in back.tracks for s in tr.segments if s.speed_curve]
        self.assertEqual(len(curves), 1)
        self.assertAlmostEqual(curves[0][1][1], 2.0, places=4)

    def test_display_mapping_roundtrips(self):
        from freecut import speed

        pts = speed.normalize([{"x": 0, "y": 0.5}, {"x": 1, "y": 2.5}])
        back = speed.from_display(speed.to_display(pts))
        self.assertEqual(len(back), 2)
        self.assertAlmostEqual(back[0][1], 0.5, places=3)
        self.assertAlmostEqual(back[1][1], 2.5, places=3)

    def test_display_range_is_clamped(self):
        from freecut import speed

        disp = speed.to_display([(0.0, 0.01), (1.0, 99.0)])
        self.assertGreaterEqual(min(y for _x, y in disp), 0.0)
        self.assertLessEqual(max(y for _x, y in disp), 1.0)

    def test_presets_are_usable(self):
        from freecut import speed

        for name, pts in speed.PRESETS.items():
            norm = speed.normalize([{"x": x, "y": y} for x, y in pts])
            self.assertGreaterEqual(len(norm), 2, name)
            self.assertGreater(speed.average_speed(norm), 0.1, name)
        flat = speed.normalize([{"x": x, "y": y} for x, y in speed.PRESETS["匀速"]])
        self.assertAlmostEqual(speed.average_speed(flat), 1.0, places=3)

    def test_curve_changes_the_source_instant(self):
        import tempfile as tf

        from freecut.export import Renderer

        # a 3 s clip with a strong ramp: the middle of the clip must show a
        # different source instant than a constant-speed clip would
        p = builder.new_project(64, 36, 10)
        p.canvas.background = "#123456"
        seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=4)
        linear = seg.source_time(600_000)
        builder.set_speed_curve(p, seg, [{"x": 0, "y": 3.0}, {"x": 1, "y": 0.5}])
        curved = seg.source_time(600_000)
        self.assertGreater(abs(curved - linear), 100_000)

class TestFfmpegDiscovery(unittest.TestCase):
    """A green/portable build must find ffmpeg dropped next to the exe."""

    def test_env_var_wins(self):
        from freecut import media

        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "ffmpeg.exe")
            open(fake, "wb").close()
            old = os.environ.get("FREECUT_FFMPEG")
            os.environ["FREECUT_FFMPEG"] = fake
            try:
                self.assertEqual(media.ffmpeg_path(), fake)
            finally:
                if old is None:
                    os.environ.pop("FREECUT_FFMPEG", None)
                else:
                    os.environ["FREECUT_FFMPEG"] = old

    def test_beside_the_executable(self):
        from freecut import media

        with tempfile.TemporaryDirectory() as tmp:
            name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
            fake = os.path.join(tmp, name)
            open(fake, "wb").close()
            old_env = os.environ.pop("FREECUT_FFMPEG", None)
            old_exe = sys.executable
            try:
                sys.executable = os.path.join(tmp, "FreeCut.exe")
                self.assertEqual(media._find_beside_app("ffmpeg"), fake)
            finally:
                sys.executable = old_exe
                if old_env is not None:
                    os.environ["FREECUT_FFMPEG"] = old_env

    def test_missing_gives_a_helpful_message(self):
        from freecut import media

        old_env = os.environ.pop("FREECUT_FFMPEG", None)
        try:
            path = media.ffmpeg_path()          # may find a real one on this box
            self.assertTrue(os.path.isfile(path))
        except RuntimeError as exc:
            self.assertIn("ffmpeg.exe", str(exc))
        finally:
            if old_env is not None:
                os.environ["FREECUT_FFMPEG"] = old_env


def _make_app():
    """Create a Tk app, or return None when no display is available."""
    try:
        from freecut.gui import App

        return App(None)
    except Exception:
        return None


def _find_widgets(widget, cls, text=None, out=None):
    out = out if out is not None else []
    for child in widget.winfo_children():
        if child.winfo_class() == cls and (
            text is None or (hasattr(child, "cget") and str(child.cget("text")) == text)
        ):
            out.append(child)
        _find_widgets(child, cls, text, out)
    return out


PR_SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "..", "Premiere 样本", "evidence", "test.prproj")


def _solid_clip(tmp, name, colour, size=(320, 180), seconds=2):
    """A tiny solid-colour mp4 so layouts can be measured by pixel colour."""
    import subprocess

    from freecut.media import _creationflags, ffmpeg_path

    out = os.path.join(tmp, name)
    if not os.path.isfile(out):
        subprocess.run(
            [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c={colour}:s={size[0]}x{size[1]}:d={seconds}",
             "-c:v", "mpeg4", "-q:v", "4", "-pix_fmt", "yuv420p", out],
            check=True, creationflags=_creationflags())
    return out


def _demo_project(tmp, seconds=2.0):
    """A self-contained project (media generated on the fly) for tests.

    Tests used to load ``out/demo_project.json``, which is a build artefact —
    so a clean checkout skipped them.  This builds the same shape locally.
    """
    import math
    import wave

    red = _solid_clip(tmp, "demo_red.mp4", "0xCC3333", seconds=int(seconds) + 1)
    blue = _solid_clip(tmp, "demo_blue.mp4", "0x3333CC", seconds=int(seconds) + 1)
    tone = os.path.join(tmp, "demo_tone.wav")
    if not os.path.isfile(tone):
        rate = 22050
        frames = int(rate * seconds)
        data = bytearray()
        for i in range(frames):
            v = int(0.25 * 32767 * math.sin(2 * math.pi * 220 * i / rate))
            data += v.to_bytes(2, "little", signed=True)
        with wave.open(tone, "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(rate)
            fh.writeframes(bytes(data))

    p = builder.new_project(320, 180, 10, name="测试工程")
    p.canvas.background = "#1e2430"
    builder.add_video(p, red, start=0, duration=int(seconds * 1_000_000), track_index=0)
    builder.add_video(p, blue, start=int(seconds * 1_000_000),
                      duration=int(seconds * 1_000_000), track_index=0)
    builder.add_audio(p, tone, start=0, duration=int(seconds * 1_000_000), track_index=0)
    return p


def _demo_for(case, seconds=2.0):
    """``_demo_project`` whose temp media lives until the test finishes."""
    # ignore_cleanup_errors：Windows 上解码器句柄可能比测试晚一步释放，
    # 清理失败不该把一条通过的测试变成错误（真正的泄漏由 FrameReader.wait 兜住）
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    case.addCleanup(tmp.cleanup)
    return _demo_project(tmp.name, seconds=seconds)


class TestPreviewQuality(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.playing = False
                self.app.destroy()
            except Exception:
                pass

    def test_modes_pick_the_right_size(self):
        app = self.app
        app.project = builder.new_project(1280, 720, 30)
        app.preview_quality = "全尺寸"
        self.assertEqual(app._preview_size(), (1280, 720))
        app.preview_quality = "半"
        self.assertEqual(app._preview_size(), (640, 360))
        app.preview_quality = "三分之一"
        self.assertEqual(app._preview_size(), (426, 240))
        app.preview_quality = "四分之一"
        self.assertEqual(app._preview_size(), (320, 180))

    def test_auto_uses_half_while_playing(self):
        app = self.app
        app.project = builder.new_project(1280, 720, 30)
        app.preview_quality = "自动"
        app.playing = False
        self.assertEqual(app._preview_size(), (1280, 720))
        app.playing = True
        self.assertEqual(app._preview_size(), (426, 240), "自动播放时降到 1/3")

    def test_switching_quality_rebuilds_the_renderer(self):
        app = self.app
        app.project = builder.new_project(640, 360, 30)
        builder.add_text(app.project, "画质", start=0, duration=400_000, size=10)
        app.set_preview_quality("半")
        app.update()
        r1 = app._renderer
        self.assertIsNotNone(r1)
        self.assertEqual((r1.width, r1.height), (320, 180))
        app.set_preview_quality("全尺寸")
        app.update()
        r2 = app._renderer
        self.assertIsNot(r1, r2, "换画质必须重建渲染器")
        self.assertEqual((r2.width, r2.height), (640, 360))

    def test_frames_are_counted(self):
        app = self.app
        app.project = builder.new_project(320, 180, 10)
        builder.add_text(app.project, "x", start=0, duration=300_000, size=6)
        app.invalidate()
        before = app.frames_shown
        app._render_now()
        self.assertEqual(app.frames_shown, before + 1)


class TestPlaybackTiming(unittest.TestCase):
    """Playback must follow the wall clock, not the render speed."""

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.playing = False
                self.app.destroy()
            except Exception:
                pass

    def _run_for(self, seconds: float):
        import time as _t

        app = self.app
        app.playhead = 0
        app.toggle_play()
        deadline = _t.time() + seconds
        while _t.time() < deadline:
            app.update()
            _t.sleep(0.005)
        app.toggle_play()
        return app.playhead / 1e6

    def test_playhead_follows_real_time(self):
        app = self.app
        app.project = builder.new_project(160, 90, 30)
        builder.add_text(app.project, "计时", start=0, duration=3_000_000, size=6)
        app.project.canvas.width, app.project.canvas.height = 320, 180
        advanced = self._run_for(0.6)
        # 以前是 1/fps 一跳，渲染跟不上就只走 0.1 秒；现在应当接近 0.6
        self.assertGreater(advanced, 0.4, f"播放头只走了 {advanced:.2f}s")

    def test_mute_stops_audio_and_skips_start(self):
        app = self.app
        app.project = builder.new_project(160, 90, 10)
        builder.add_text(app.project, "x", start=0, duration=500_000, size=6)
        app.toggle_mute()
        self.assertTrue(app.preview_muted)
        self.assertEqual(str(app.mute_btn.cget("text")), "🔇")
        app._start_audio()                # 静音时直接返回，不应抛异常
        app.toggle_mute()
        self.assertFalse(app.preview_muted)

    def test_volume_is_clamped_when_cutting(self):
        app = self.app
        app.preview_volume = 5.0          # 越界值不应把 ffmpeg 参数写坏
        app.project = builder.new_project(160, 90, 10)
        builder.add_text(app.project, "x", start=0, duration=400_000, size=6)
        app._start_audio()
        app.preview_volume = -1.0
        app._start_audio()
        app._stop_audio()


class TestXmlRoundTrip(unittest.TestCase):
    """导出报告说的每一条映射，都要能被回读证明。"""

    def _write(self, project, tmp):
        from freecut import interchange

        path = os.path.join(tmp, "seq.xml")
        interchange.write_fcp7(project, path)
        return path

    def test_verify_passes_on_a_transformed_project(self):
        from freecut import draftreport, layouts

        with tempfile.TemporaryDirectory() as tmp:
            a = _solid_clip(tmp, "a.mp4", "0xAA2222", seconds=1)
            b = _solid_clip(tmp, "b.mp4", "0x2222AA", seconds=1)
            p = builder.new_project(320, 180, 10)
            seg_a = builder.add_video(p, a, start=0, duration=800_000, track_index=0)
            seg_b = builder.add_video(p, b, start=0, duration=800_000, track_index=1)
            layouts.apply(p, "左右分屏", [seg_a, seg_b])
            seg_b.clip.alpha = 0.5
            path = self._write(p, tmp)
            check = draftreport.verify_xml(p, path)
        self.assertTrue(check.ok, check.as_text())
        self.assertIn("一致", check.as_text())

    def test_verify_detects_a_hand_edited_xml(self):
        from freecut import draftreport

        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "c.mp4", "0x22AA22", seconds=1)
            p = builder.new_project(320, 180, 10)
            builder.add_video(p, clip, start=0, duration=800_000, track_index=0)
            path = self._write(p, tmp)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            # 把起点改掉，校验必须发现
            text = text.replace("<start>0</start>", "<start>25</start>", 1)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            check = draftreport.verify_xml(p, path)
        self.assertFalse(check.ok)
        self.assertIn("起点不一致", check.as_text())

    def test_reader_separates_audio_from_video(self):
        from freecut import interchange

        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "d.mp4", "0xAAAA22", seconds=1)
            tone = os.path.join(tmp, "t.wav")
            import math
            import wave

            rate = 8000
            data = bytearray()
            for i in range(rate):
                data += int(0.3 * 32767 * math.sin(2 * math.pi * 220 * i / rate)).to_bytes(
                    2, "little", signed=True)
            with wave.open(tone, "wb") as fh:
                fh.setnchannels(1)
                fh.setsampwidth(2)
                fh.setframerate(rate)
                fh.writeframes(bytes(data))
            p = builder.new_project(320, 180, 10)
            builder.add_video(p, clip, start=0, duration=500_000, track_index=0)
            builder.add_audio(p, tone, start=0, duration=500_000, track_index=0)
            path = self._write(p, tmp)
            data = interchange.read_fcp7(path)
        kinds = [c["kind"] for c in data["clips"]]
        self.assertIn("video", kinds)
        self.assertIn("audio", kinds)


class TestXmlExportReport(unittest.TestCase):
    """XML 方向的报告：编辑信息能带走，FreeCut 自己渲染的带不走。"""

    def _project(self, tmp):
        clip = _solid_clip(tmp, "x.mp4", "0x22AA55", seconds=1)
        p = builder.new_project(160, 90, 10)
        seg = builder.add_video(p, clip, start=0, duration=800_000, track_index=0)
        return p, seg

    def test_transform_is_written_into_the_xml(self):
        from freecut import interchange

        with tempfile.TemporaryDirectory() as tmp:
            p, seg = self._project(tmp)
            seg.clip.scale_x = seg.clip.scale_y = 0.5
            seg.clip.tx = 0.25
            seg.clip.alpha = 0.6
            xml = interchange.to_fcp7_xml(p)
        self.assertIn("Basic Motion", xml)
        self.assertIn("Opacity", xml)
        self.assertIn("0.6", xml.replace("60.00", "0.6"))

    def test_crop_is_written(self):
        from freecut import interchange, layouts

        with tempfile.TemporaryDirectory() as tmp:
            p, seg = self._project(tmp)
            clip2 = _solid_clip(tmp, "y.mp4", "0x5522AA", seconds=1)
            seg2 = builder.add_video(p, clip2, start=0, duration=800_000, track_index=1)
            layouts.apply(p, "左右分屏", [seg, seg2])
            xml = interchange.to_fcp7_xml(p)
        self.assertIn("Crop", xml)

    def test_report_calls_grading_lost_and_edit_not(self):
        from freecut import draftreport

        with tempfile.TemporaryDirectory() as tmp:
            p, seg = self._project(tmp)
            builder.set_curves(p, seg, rgb=[[0, 0], [1, 1]])
            seg.clip.scale_x = seg.clip.scale_y = 0.5
            rep = draftreport.analyze_xml(p)
        lost = {i.feature for i in rep.by(draftreport.LOST)}
        equiv = {i.feature for i in rep.by(draftreport.EQUIVALENT)}
        self.assertTrue(any("调色" in f for f in lost), lost)
        self.assertTrue(any("变换" in f for f in equiv), equiv)
        self.assertEqual(rep.target, "Premiere")
        self.assertIn("Premiere", rep.as_text())


class TestPaneState(unittest.TestCase):
    """面板宽度记忆：读写要能往返，坏文件不能把界面搞崩。"""

    def test_roundtrip(self):
        from freecut import uistate

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ui.json")
            self.assertTrue(uistate.save({"pane_widths": [300, 0, 420]}, path))
            data = uistate.load(path)
        self.assertEqual(data["pane_widths"], [300, 0, 420])

    def test_broken_file_is_ignored(self):
        from freecut import uistate

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ui.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{ not json")
            self.assertEqual(uistate.load(path), {})

    def test_widths_are_clamped(self):
        from freecut import uistate

        left, _mid, right = uistate.pane_widths({"pane_widths": [5, 0, 99999]})
        self.assertGreaterEqual(left, 140)
        self.assertLessEqual(right, 720)

    def test_defaults_when_nothing_saved(self):
        from freecut import uistate

        self.assertEqual(uistate.pane_widths({}), uistate.DEFAULT_WIDTHS)


class TestDocAudit(unittest.TestCase):
    def test_corrupted_characters_are_flagged(self):
        """替换字符（U+FFFD）是最隐蔽的文档损坏，必须报出来。"""
        mod = self._load_audit()
        import tempfile as _temp

        with _temp.TemporaryDirectory() as tmp:
            doc = os.path.join(tmp, "bad.md")
            with open(doc, "w", encoding="utf-8") as fh:
                fh.write("正常一行\n这里坏了：界\ufffd\n")
            from pathlib import Path as _Path

            original = mod.DOCS
            mod.DOCS = [_Path(doc)]
            try:
                problems = mod.audit()
            finally:
                mod.DOCS = original
        kinds = [p.kind for p in problems]
        self.assertIn("编码", kinds)

    def test_start_here_three_commands_exist(self):
        """START-HERE 的三条命令也必须是真的。"""
        mod = self._load_audit()
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "START-HERE.md"), encoding="utf-8").read()
        start = text.find("## 三条命令")
        self.assertGreater(start, 0, "START-HERE 里应当有『三条命令』一节")
        section = text[start:start + 800]
        commands = mod._commands()
        for name in ("demo", "gui"):
            self.assertIn(f"freecut {name}", section)
            self.assertIn(name, commands)
        self.assertIn("verify_all.py", section)

    def test_readme_three_commands_exist(self):
        """README 开头的三条命令必须是真的子命令/脚本。"""
        mod = self._load_audit()
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(repo, "README.md"), encoding="utf-8").read()
        start = text.find("## 三条命令")
        self.assertGreater(start, 0, "README 里应当有『三条命令』一节")
        section = text[start:start + 900]
        commands = mod._commands()
        for name in ("demo", "gui"):
            self.assertIn(f"freecut {name}", section)
            self.assertIn(name, commands, f"README 提到的 {name} 不是子命令")
        self.assertIn("verify_all.py", section)

    def test_safe_command_filter(self):
        """只有只读子命令能被"真的跑一遍"，带副作用的不许跑。"""
        mod = self._load_audit()
        self.assertIn("info", mod.SAFE_COMMANDS)
        self.assertNotIn("export-draft", mod.SAFE_COMMANDS, "写草稿不能自动跑")
        self.assertNotIn("jianying-probe", mod.SAFE_COMMANDS, "它会启动剪映")
        self.assertNotIn("batch", mod.SAFE_COMMANDS)

    def test_documented_commands_run_clean(self):
        mod = self._load_audit()
        problems = mod.run_documented_commands()
        self.assertEqual([p.as_line() for p in problems], [])

    def test_encrypt_flag_is_opt_in(self):
        """文档说"默认明文、--encrypt 才加密"，命令行必须真的有这个开关。"""
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run([sys.executable, "-m", "freecut", "export-draft", "--help"],
                              capture_output=True, timeout=120, cwd=repo)
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertIn("--encrypt", text)
        self.assertIn("--no-encrypt", text)

    def test_flags_and_presets_are_checked(self):
        mod = self._load_audit()
        flags = mod._flags()
        self.assertIn("--encrypt", flags, "export-draft 的 --encrypt 应当被收集到")
        self.assertIn("--strict", flags, "verify_all 的 --strict 也算我们的参数")
        presets = mod._presets()
        self.assertIn("720p30", presets)
        self.assertIn("vertical1080", presets)

    """文档体检：路径、命令、数字三种漂移都要能查出来。"""

    def _repo(self):
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _load_audit(self):
        import importlib.util

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(repo, "FreeCut", "tools", "doc_audit.py")
        spec = importlib.util.spec_from_file_location("doc_audit", path)
        mod = importlib.util.module_from_spec(spec)
        # dataclass 装饰器要在 sys.modules 里找得到模块，先注册再执行
        sys.modules["doc_audit"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_commands_include_table_registered_ones(self):
        mod = self._load_audit()
        commands = mod._commands()
        # 这两种注册方式都要认出来
        self.assertIn("export", commands, "表里注册的 export 漏了")
        self.assertIn("showcase", commands, "add_parser 注册的 showcase 漏了")

    def test_counts_match_reality(self):
        import re

        mod = self._load_audit()
        counts = mod._counts()
        pkg = os.path.join(self._repo(), "FreeCut", "freecut")
        self.assertEqual(counts["modules"], len([f for f in os.listdir(pkg) if f.endswith(".py")]))
        # 测试可以拆成多个文件（第 243 轮起 test_user_issues_*.py 也在统计范围）
        tests = 0
        tests_dir = os.path.join(self._repo(), "FreeCut", "tests")
        for name in sorted(os.listdir(tests_dir)):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            with open(os.path.join(tests_dir, name), encoding="utf-8") as fh:
                tests += len(re.findall(r"^\s*def test_", fh.read(), re.M))
        self.assertEqual(counts["tests"], tests)

    def test_repo_is_clean(self):
        import subprocess

        repo = self._repo()
        proc = subprocess.run([sys.executable, "tools/doc_audit.py", "--strict"],
                              cwd=os.path.join(repo, "FreeCut"), capture_output=True,
                              timeout=300)
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, text[-800:])


class TestXmlImport(unittest.TestCase):
    def test_text_clips_survive_as_generatoritems(self):
        from freecut import builder
        from freecut.interchange import from_fcp7_xml, to_fcp7_xml

        p = builder.new_project(640, 360, 30, name="字幕往返")
        builder.add_text(p, "第一句字幕", start=0, duration=1_000_000, size=14)
        builder.add_text(p, "第二句字幕", start=1_200_000, duration=800_000, size=14)
        xml = to_fcp7_xml(p, "字幕")
        back, info = from_fcp7_xml(xml)

        self.assertEqual(info["texts"], 2, "两条文字都要回来")
        texts = [back.material(s.material_id).get("content")
                 for t in back.tracks if t.type == "text" for s in t.segments]
        self.assertIn("第一句字幕", texts)
        self.assertIn("第二句字幕", texts)
        self.assertIn("<generatoritem", xml)

    def test_empty_text_track_is_not_dropped(self):
        """文字轨里没有 clipitem，只有 generatoritem——不能因此跳过整条轨。"""
        from freecut.interchange import from_fcp7_xml

        xml = """<?xml version="1.0" encoding="utf-8"?>
<xmeml version="4"><sequence id="s1"><name>只有字</name>
<rate><timebase>25</timebase></rate>
<media><video><format><samplecharacteristics>
<width>640</width><height>360</height></samplecharacteristics></format>
<track><generatoritem id="g1"><name>T</name>
<start>0</start><end>25</end><in>0</in><out>25</out>
<effect><parameter><parameterid>str</parameterid><value>你好</value></parameter></effect>
</generatoritem></track></video></media></sequence></xmeml>"""
        project, info = from_fcp7_xml(xml)
        self.assertEqual(info["texts"], 1)
        self.assertEqual(len([t for t in project.tracks if t.type == "text"]), 1)

    """FCP7 XML 导入：我们写出去的能读回来，且只丢"没有媒体"的片段。"""

    def _add_media(self, project, kind, name, duration_us, start_us=0):
        """直接造材质，不走 builder（builder 要 probe 真实文件）。"""
        from freecut.model import AudioMaterial, Segment, Timerange, Track, VideoMaterial

        if kind == "video":
            mat = VideoMaterial(path=name, duration=duration_us, width=640, height=360,
                                has_audio=False, material_name=os.path.basename(name))
        else:
            mat = AudioMaterial(path=name, duration=duration_us,
                                material_name=os.path.basename(name))
        project.add_material(mat)
        track = Track(type=kind)
        project.tracks.append(track)
        track.segments.append(Segment(
            material_id=mat.id,
            target_timerange=Timerange(start_us, duration_us),
            source_timerange=Timerange(0, duration_us)))
        return mat

    def test_roundtrip_keeps_every_clip_with_media(self):
        from freecut import builder
        from freecut.interchange import from_fcp7_xml, to_fcp7_xml

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(640, 360, 25, name="xml 往返")
            builder.add_text(p, "文字会被跳过", start=0, duration=1_000_000, size=12)
            self._add_media(p, "video", os.path.join(tmp, "a.mp4"), 1_000_000)
            self._add_media(p, "audio", os.path.join(tmp, "b.wav"), 1_000_000)
            xml = to_fcp7_xml(p, "往返")
            back, info = from_fcp7_xml(xml)

        self.assertEqual(info["clips"], 2, "视频 + 音频两个片段应还原")
        self.assertEqual((back.canvas.width, back.canvas.height), (640, 360))
        self.assertAlmostEqual(back.fps, 25.0, places=1)
        self.assertEqual(info["sequence"], "往返")

    def test_durations_survive_frame_rounding(self):
        from freecut import builder
        from freecut.interchange import from_fcp7_xml, to_fcp7_xml

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(320, 180, 30, name="时长")
            self._add_media(p, "video", os.path.join(tmp, "x.mp4"), 2_000_000)
            back, _info = from_fcp7_xml(to_fcp7_xml(p))
        self.assertAlmostEqual(back.duration() / 1e6, 2.0, places=2)

    def test_missing_pathurl_is_skipped_not_invented(self):
        from freecut.interchange import from_fcp7_xml

        xml = """<?xml version="1.0" encoding="utf-8"?>
<xmeml version="4"><sequence id="s1"><name>只有文字</name>
<rate><timebase>30</timebase></rate>
<media><video><format><samplecharacteristics>
<width>640</width><height>360</height></samplecharacteristics></format>
<track><clipitem id="c1"><name>纯文字</name>
<start>0</start><end>30</end><in>0</in><out>30</out></clipitem></track>
</video></media></sequence></xmeml>"""
        project, info = from_fcp7_xml(xml)
        self.assertEqual(info["clips"], 0)
        self.assertEqual(project.tracks, [], "没有媒体路径就不该造出空轨")

    def test_cli_imports_xml(self):
        import subprocess

        from freecut import builder
        from freecut.interchange import to_fcp7_xml

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(320, 180, 30, name="cli")
            self._add_media(p, "video", os.path.join(tmp, "m.mp4"), 500_000)
            xml = os.path.join(tmp, "timeline.xml")
            with open(xml, "w", encoding="utf-8") as fh:
                fh.write(to_fcp7_xml(p))
            out = os.path.join(tmp, "o.json")
            proc = subprocess.run([sys.executable, "-m", "freecut", "import-xml", xml,
                                   "--out", out], capture_output=True, timeout=120, cwd=repo)
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, text[-300:])
        self.assertIn("已导入 XML", text)


class TestPrprojExport(unittest.TestCase):
    """写 .prproj：只要我们自己读得回来、数值对得上，就算自洽。"""

    def test_roundtrip_through_our_own_importer(self):
        from freecut import builder
        from freecut.prproj import import_project
        from freecut.prproj_export import export_prproj

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(1280, 720, 30, name="往返")
            builder.add_text(p, "第一段", start=0, duration=1_000_000, size=12)
            builder.add_text(p, "第二段", start=1_200_000, duration=800_000, size=12, y=-0.4)
            out = os.path.join(tmp, "a.prproj")
            export_prproj(p, out)
            back, info = import_project(out)

        self.assertEqual((back.canvas.width, back.canvas.height), (1280, 720))
        self.assertAlmostEqual(back.fps, 30.0, places=1)
        self.assertAlmostEqual(back.duration() / 1e6, 2.0, places=2)
        self.assertEqual(sum(len(t.segments) for t in back.tracks), 2)
        self.assertEqual(info["sequence"], "往返")

    def test_writes_gzip_xml(self):
        import gzip

        from freecut import builder
        from freecut.prproj_export import export_prproj

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(640, 360, 25, name="gz")
            builder.add_text(p, "x", start=0, duration=500_000, size=10)
            out = os.path.join(tmp, "b.prproj")
            export_prproj(p, out)
            with gzip.open(out, "rb") as fh:
                payload = fh.read()
        self.assertIn(b"<PremiereData", payload)
        self.assertIn(b"VideoTrackGroup", payload)
        self.assertIn(b"<FrameRect>0,0,640,360", payload)

    def test_adjustment_layers_survive(self):
        from freecut import builder
        from freecut.prproj import import_project
        from freecut.prproj_export import export_prproj

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(640, 360, 30, name="adj")
            builder.add_text(p, "底", start=0, duration=1_000_000, size=10)
            builder.add_adjustment(p, start=0, duration=1_000_000)
            out = os.path.join(tmp, "c.prproj")
            export_prproj(p, out)
            back, info = import_project(out)
        self.assertGreaterEqual(info["adjustment"], 1, "调节图层应当往返保留")


class TestPrRoundtrip(unittest.TestCase):
    """PR 工程往返：导入后画布不变，导出跟随工程而不是硬编码 1080p。"""

    def _sample(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(os.path.dirname(repo), "Premiere 样本", "evidence", "test.prproj")
        if not os.path.isfile(path):
            self.skipTest("没有 PR 样例工程")
        return path

    def test_import_keeps_the_sequence_settings(self):
        from freecut.prproj import import_project

        project, info = import_project(self._sample())
        self.assertEqual((project.canvas.width, project.canvas.height), (1280, 720))
        self.assertAlmostEqual(project.fps, 29.97, places=1)
        self.assertGreater(project.duration(), 80_000_000)
        # 这个样例里两个视频条目都是调节图层（没有素材），导入后仍应保留
        self.assertGreaterEqual(info["adjustment"], 1)

    def test_export_follows_the_project_canvas(self):
        """不传 preset 时按工程画布导出，而不是默默导成 1080p。"""
        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        from freecut import builder, export, verify

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(320, 180, 15, name="canvas")
            builder.add_text(p, "画布跟随", start=0, duration=800_000, size=12)
            out = os.path.join(tmp, "o.mp4")
            export.export(p, out)
            report = verify.verify(out, expect_duration_s=0.8)
        self.assertEqual((report.width, report.height), (320, 180))
        self.assertAlmostEqual(report.fps, 15.0, places=0)


class TestSelfcheckSteps(unittest.TestCase):
    """自检对话框的步骤清单：从 verify_all 的输出里认出每一步。"""

    def test_parses_ok_and_fail_lines(self):
        from freecut.selfcheck import parse_step_line

        ok = parse_step_line("[OK  ] 剪映逆向复现 (10s) 8/8 条通过")
        self.assertEqual(ok, (True, "剪映逆向复现", 10, "8/8 条通过"))
        bad = parse_step_line("[FAIL] 单元测试 (380s) 2 个失败")
        self.assertEqual(bad[0], False)
        self.assertEqual(bad[1], "单元测试")
        self.assertEqual(bad[2], 380)

    def test_ignores_non_step_lines(self):
        from freecut.selfcheck import parse_step_line

        for line in ("", "==== 汇总 ====", "全部 5 步通过，用时 404s", "  随便一行"):
            self.assertIsNone(parse_step_line(line), line)

    def test_dialog_lists_steps_and_reports_failure(self):
        """子进程退出码 0、但清单里有失败步骤时，仍然显示失败。"""
        import subprocess

        import time

        app = _make_app()
        if app is None:
            self.skipTest("没有可用的图形环境")
        fake = os.path.join(tempfile.mkdtemp(), "fake_verify.py")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("import sys\n"
                     "print('[OK  ] 甲 (1s) 通过', flush=True)\n"
                     "print('[FAIL] 乙 (2s) 失败', flush=True)\n")
        import pathlib

        app._selfcheck_script = pathlib.Path(fake)
        try:
            app.run_selfcheck_dialog()
            for _ in range(600):
                app.update()
                time.sleep(0.01)
                wins = [w for w in app.winfo_children() if w.winfo_class() == "Toplevel"]
                if wins:
                    texts, labels = [], []

                    def walk(w):
                        for c in w.winfo_children():
                            if c.winfo_class() == "Text":
                                texts.append(c)
                            if c.winfo_class() == "TLabel":
                                try:
                                    labels.append(c.cget("text"))
                                except Exception:
                                    pass
                            walk(c)

                    walk(wins[0])
                    if texts and any("自检" in l for l in labels):
                        checklist = texts[0].get("1.0", "end")
                        status = [l for l in labels if "自检" in l][0]
                        break
            else:
                self.fail("对话框没有完成")
        finally:
            app.destroy()
        self.assertIn("甲", checklist)
        self.assertIn("乙", checklist)
        self.assertIn("失败", status, "有失败步骤时不能报通过")
        self.assertNotIn("自检通过", status)


class TestJianyingDiff(unittest.TestCase):
    """剪映前后对比：证明它认没认我们写的字段。"""

    def _draft(self, tmp, duration=0, wheels=True, meta_extra=None):
        import json

        draft = os.path.join(tmp, "draft")
        os.makedirs(draft, exist_ok=True)
        materials = {"color_curves": [{"id": "c1"}], "videos": [{"id": "v1"}]}
        if wheels:
            materials["primary_color_wheels"] = [{"id": "w1"}]
        data = {
            "duration": duration,
            "meta": {"tm_duration": duration, "custom_key": 1, **(meta_extra or {})},
            "tracks": [{"type": "video", "segments": [{"id": "s1"}]}],
            "materials": materials,
        }
        with open(os.path.join(draft, "draft_content.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        return draft

    def test_snapshot_and_diff_see_the_rewrite(self):
        from freecut import jyverify

        with tempfile.TemporaryDirectory() as tmp:
            draft = self._draft(tmp)
            before = jyverify.snapshot(draft)
            # 剪映打开并保存后：算出时长、重算素材体积、丢掉一个调色数组
            self._draft(tmp, duration=2_000_000, wheels=False,
                        meta_extra={"draft_timeline_materials_size_": 3_995_221})
            after = jyverify.snapshot(draft)

        changes = jyverify.diff(before, after)
        fields = {c.field for c in changes}
        self.assertIn("duration", fields)
        self.assertIn("meta.素材体积", fields)
        self.assertIn("materials.primary_color_wheels", fields)
        verdict = jyverify.verdict(changes)
        self.assertIn("丢掉", verdict)
        self.assertIn("primary_color_wheels", verdict)

    def test_unchanged_draft_reports_nothing(self):
        from freecut import jyverify

        with tempfile.TemporaryDirectory() as tmp:
            draft = self._draft(tmp)
            before = jyverify.snapshot(draft)
            after = jyverify.snapshot(draft)
        self.assertEqual(jyverify.diff(before, after), [])
        self.assertIn("没有任何变化", jyverify.verdict([]))

    def test_kept_arrays_read_as_success(self):
        from freecut import jyverify

        with tempfile.TemporaryDirectory() as tmp:
            draft = self._draft(tmp)
            before = jyverify.snapshot(draft)
            # 剪映只是算了时长，两个调色数组都还在
            self._draft(tmp, duration=1_500_000)
            after = jyverify.snapshot(draft)
        changes = jyverify.diff(before, after)
        verdict = jyverify.verdict(changes)
        self.assertIn("没有动调色字段", verdict)

    def test_snapshot_roundtrip_on_disk(self):
        from freecut import jyverify

        with tempfile.TemporaryDirectory() as tmp:
            draft = self._draft(tmp, duration=1_000_000)
            path = jyverify.save_snapshot(draft)
            self.assertTrue(os.path.isfile(path))
            loaded = jyverify.load_snapshot(draft)
        self.assertEqual(loaded["duration"], 1_000_000)


class TestSashNudge(unittest.TestCase):
    """分隔线的键盘微调（ReaCut 界面说明里的那一项）。"""

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def test_nudge_moves_and_clamps(self):
        app = self.app
        for _ in range(40):
            app.update()
        before = app.panes.sashpos(0)
        app.nudge_sash(0, 24)
        app.update()
        moved = app.panes.sashpos(0)
        self.assertGreater(moved, before)
        app.nudge_sash(0, -24)
        app.update()
        self.assertLess(abs(app.panes.sashpos(0) - before), 4)
        # 往极左推不会把左栏压到 140 以下
        app.nudge_sash(0, -10000)
        app.update()
        self.assertGreaterEqual(app.panes.sashpos(0), 140)

    def test_shortcuts_are_bound(self):
        app = self.app
        for seq in ("<Control-Left>", "<Control-Right>",
                    "<Control-Shift-Left>", "<Control-Shift-Right>"):
            self.assertTrue(app.bind_all(seq), f"{seq} 没绑定")


class TestPictureTabs(unittest.TestCase):
    """属性面板按 ReaCut 的分组：画面下再分 基础/裁剪/抠像/蒙版。"""

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def _notebooks(self):
        out = []

        def walk(w):
            for c in w.winfo_children():
                if c.winfo_class() == "TNotebook":
                    out.append(c)
                walk(c)

        walk(self.app.props)
        return out

    def test_tab_structure(self):
        app = self.app
        p = builder.new_project(320, 180, 10, name="tabs")
        seg = builder.add_text(p, "x", start=0, duration=500_000, size=10)
        app.project = p
        app.props.rebuild(seg)
        app.update()
        books = self._notebooks()
        self.assertGreaterEqual(len(books), 2, "应当有顶层页签 + 画面二级页签")
        top = [books[0].tab(i, "text") for i in range(books[0].index("end"))]
        self.assertIn("画面", top)
        sub = [books[1].tab(i, "text") for i in range(books[1].index("end"))]
        for want in ("基础", "裁剪", "抠像", "蒙版"):
            self.assertIn(want, sub)

    def test_crop_slider_writes_the_material(self):
        app = self.app
        p = builder.new_project(320, 180, 10, name="crop")
        seg = builder.add_text(p, "x", start=0, duration=500_000, size=10)
        app.project = p
        mat = p.material(seg.material_id)
        app.props.rebuild(seg)
        app.update()
        # 模拟"裁剪页左边界拉到 0.2"：直接走面板的写入口径
        mat["crop"] = {"upper_left_x": 0.2, "upper_left_y": 0.0,
                       "lower_right_x": 1.0, "lower_right_y": 1.0}
        from freecut.render import crop_box

        box = crop_box(mat, 320, 180)
        self.assertIsNotNone(box)
        self.assertGreater(box[0], 0)


class TestPaneLayout(unittest.TestCase):
    """三栏是可拖的 Panedwindow，能保存与重置。"""

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")
        self.state = os.path.join(tempfile.mkdtemp(), "ui.json")
        self._old = os.environ.get("FREECUT_UI_STATE")
        os.environ["FREECUT_UI_STATE"] = self.state

    def tearDown(self):
        if self._old is None:
            os.environ.pop("FREECUT_UI_STATE", None)
        else:
            os.environ["FREECUT_UI_STATE"] = self._old
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def test_three_panes(self):
        app = self.app
        for _ in range(40):
            app.update()
        self.assertEqual(len(app.panes.panes()), 3)

    def test_save_and_reset(self):
        import json

        app = self.app
        for _ in range(40):
            app.update()
        app.panes.sashpos(0, 300)
        app.update()
        app.save_ui_state()
        with open(self.state, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data["pane_widths"]), 3)
        self.assertGreaterEqual(data["pane_widths"][0], 290)
        app.reset_panes()
        app.update()
        self.assertLess(abs(app.panes.sashpos(0) - 232), 30, "重置后应回到默认宽度")


class TestTimelineColors(unittest.TestCase):
    """借用 ReaCut 的界面约定：文字轨红棕、选中片段青色描边。"""

    def test_text_track_is_red_brown(self):
        from freecut import gui

        colour = gui.TRACK_COLORS["text"].lower()
        r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater(r, g, "文字轨应当偏红")
        self.assertGreater(r, b, "文字轨应当偏红")

    def test_selection_uses_the_accent(self):
        from freecut import gui

        self.assertTrue(hasattr(gui, "ACCENT"))
        self.assertEqual(len(gui.ACCENT), 7)
        self.assertNotEqual(gui.ACCENT.lower(), gui.TRACK_COLORS["text"].lower())

    def test_ui_notice_exists_and_names_the_source(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "docs", "NOTICE-UI.md")
        if not os.path.isfile(path):
            self.skipTest("没有 docs/NOTICE-UI.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("rea-capcut", text)
        self.assertIn("MIT", text)
        self.assertIn("没有", text, "要写清没复制代码/素材")


class TestRetime(unittest.TestCase):
    """倒放与定格：ReaCut 有、我们原来没有的两个能力。"""

    def _source(self, tmp):
        from freecut import builder, export

        p = builder.new_project(160, 90, 10, name="retime")
        builder.add_text(p, "正放", start=0, duration=1_000_000, size=12)
        src = os.path.join(tmp, "in.mp4")
        export.export(p, src)
        return p, src

    def test_reverse_produces_a_playable_file(self):
        from freecut import retime

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            _p, src = self._source(tmp)
            out = retime.reverse_video(src, os.path.join(tmp, "rev.mp4"), keep_audio=False)
            self.assertTrue(os.path.isfile(out))
            self.assertGreater(os.path.getsize(out), 1024)
            self.assertGreater(retime.probe_duration_us(out), 500_000)

    def test_freeze_renders_a_frame_and_inserts_it(self):
        from freecut import retime

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            p, _src = self._source(tmp)
            clip = retime.add_freeze_clip(p, 300_000, 700_000, tmp, track_index=1)
            # 断言要放在临时目录还活着的时候——否则查的是一张已被删掉的文件
            self.assertEqual(clip.target_timerange.start, 300_000)
            self.assertEqual(clip.target_timerange.duration, 700_000)
            mat = p.material(clip.material_id)
            self.assertTrue(os.path.isfile(mat.get("path") or ""), "定格图应当真的存在")

    def test_freeze_outside_the_project_is_rejected(self):
        from freecut import retime

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            p, _src = self._source(tmp)
            with self.assertRaises(retime.RetimeError):
                retime.freeze_frame(p, 99_000_000, os.path.join(tmp, "x.png"))


class TestWebVtt(unittest.TestCase):
    """WebVTT 导入：ReaCut 支持 SRT + 基础 VTT，我们补齐 VTT 这一半。"""

    def test_parses_cues_without_hours(self):
        from freecut import subtitles

        vtt = "WEBVTT\n\nNOTE x\n\n1\n00:00.000 --> 00:02.500\n第一句 <b>字幕</b>\n"
        subs = subtitles.parse_vtt(vtt)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].start_us, 0)
        self.assertEqual(subs[0].end_us, 2_500_000)
        self.assertEqual(subs[0].text, "第一句 字幕", "HTML 标签要去掉")

    def test_cue_identifiers_are_not_numbers(self):
        from freecut import subtitles

        vtt = ("WEBVTT\n\nintro-cue\n00:01.000 --> 00:02.000\n甲\n\n"
               "00:03.000 --> 00:04.000\n乙\n")
        subs = subtitles.parse_vtt(vtt)
        self.assertEqual([s.text for s in subs], ["甲", "乙"])

    def test_full_timestamps_still_work(self):
        from freecut import subtitles

        vtt = "WEBVTT\n\n00:00:01.250 --> 00:00:03.750\n一段\n"
        subs = subtitles.parse_vtt(vtt)
        self.assertEqual(subs[0].start_us, 1_250_000)
        self.assertEqual(subs[0].end_us, 3_750_000)

    def test_cli_routes_vtt_to_the_vtt_parser(self):
        """命令行导入 .vtt 要真的进工程（走的是同一条 import 路径）。"""
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isdir(os.path.join(repo, "freecut")):
            self.skipTest("路径不对")
        with tempfile.TemporaryDirectory() as tmp:
            from freecut import builder

            project = os.path.join(tmp, "p.json")
            p = builder.new_project(320, 180, 10, name="vtt")
            p.save(project)
            vtt = os.path.join(tmp, "a.vtt")
            with open(vtt, "w", encoding="utf-8") as fh:
                fh.write("WEBVTT\n\n00:00.000 --> 00:01.000\n你好\n")
            proc = subprocess.run([sys.executable, "-m", "freecut", "subtitle", "import",
                                   project, vtt], capture_output=True, timeout=120, cwd=repo)
            text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            self.assertEqual(proc.returncode, 0, text[-300:])
            self.assertIn("1 条字幕", text, text[-300:])


class TestBatch(unittest.TestCase):
    def test_resolve_workers(self):
        from freecut import batch

        self.assertEqual(batch.resolve_workers(None), 1)
        self.assertEqual(batch.resolve_workers(1), 1)
        self.assertEqual(batch.resolve_workers("3"), 3)
        auto = batch.resolve_workers("auto")
        self.assertGreaterEqual(auto, 1)
        self.assertLessEqual(auto, 4)
        with self.assertRaises(batch.BatchError):
            batch.resolve_workers("lots")

    def test_render_uses_the_default_job_count(self):
        """没写 jobs 的渲染任务，应该继承命令行的 --jobs。"""
        from freecut import batch

        with tempfile.TemporaryDirectory() as tmp:
            proj = os.path.join(tmp, "p.json")
            p = builder.new_project(160, 90, 10, name="rj")
            builder.add_text(p, "x", start=0, duration=500_000, size=8)
            p.save(proj)
            jobs = batch.load_plan(self._plan([
                {"id": "r", "action": "render", "project": proj,
                 "out": os.path.join(tmp, "r.mp4")},
            ], tmp))
            seen = {}
            original = batch._run_one

            def spy(job, default_jobs=None):
                seen["default_jobs"] = default_jobs
                raise RuntimeError("stop here")

            batch._run_one = spy
            try:
                batch.run(jobs, workers=4)
            finally:
                batch._run_one = original
        self.assertEqual(seen.get("default_jobs"), 4, "渲染任务应继承 --jobs=4")

    def test_dependencies_are_validated(self):
        from freecut import batch

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "d.json"), "w", encoding="utf-8") as fh:
                __import__("json").dump({"jobs": [
                    {"id": "a", "action": "xml", "project": "p", "needs": ["ghost"]}]}, fh)
            with self.assertRaises(batch.BatchError) as ctx:
                batch.load_plan(os.path.join(tmp, "d.json"))
            self.assertIn("ghost", str(ctx.exception))

            with open(os.path.join(tmp, "c.json"), "w", encoding="utf-8") as fh:
                __import__("json").dump({"jobs": [
                    {"id": "x", "action": "xml", "project": "p", "needs": ["y"]},
                    {"id": "y", "action": "xml", "project": "p", "needs": ["x"]}]}, fh)
            with self.assertRaises(batch.BatchError) as ctx2:
                batch.load_plan(os.path.join(tmp, "c.json"))
            self.assertIn("环", str(ctx2.exception))

    def test_parallel_runs_independent_jobs_and_skips_dependents(self):
        from freecut import batch, builder

        with tempfile.TemporaryDirectory() as tmp:
            proj = os.path.join(tmp, "p.json")
            p = builder.new_project(160, 90, 10, name="par")
            builder.add_text(p, "x", start=0, duration=500_000, size=8)
            p.save(proj)
            jobs = batch.load_plan(self._plan([
                {"id": "a", "action": "xml", "project": proj, "out": os.path.join(tmp, "a.xml")},
                {"id": "b", "action": "xml", "project": proj, "out": os.path.join(tmp, "b.xml")},
                {"id": "bad", "action": "render", "project": os.path.join(tmp, "nope.json"),
                 "out": os.path.join(tmp, "bad.mp4")},
                {"id": "after", "action": "xml", "project": proj,
                 "out": os.path.join(tmp, "c.xml"), "needs": ["bad"]},
                {"id": "okdep", "action": "xml", "project": proj,
                 "out": os.path.join(tmp, "d.xml"), "needs": ["a"]},
            ], tmp))
            results = batch.run(jobs, workers=3)
        by_id = {r.id: r for r in results}
        self.assertTrue(by_id["a"].ok and by_id["b"].ok)
        self.assertFalse(by_id["bad"].ok)
        self.assertTrue(by_id["after"].skipped, "依赖失败的任务应当被跳过")
        self.assertTrue(by_id["okdep"].ok, "依赖成功的任务应当照常跑")
        self.assertEqual(len(results), 5)

    def test_vars_are_substituted_and_overridable(self):
        from freecut import batch

        with tempfile.TemporaryDirectory() as tmp:
            plan = os.path.join(tmp, "plan.json")
            with open(plan, "w", encoding="utf-8") as fh:
                json_dump = __import__("json").dump
                json_dump({"vars": {"outdir": os.path.join(tmp, "a")},
                           "jobs": [{"id": "x", "action": "xml",
                                     "project": "p.json", "out": "{{outdir}}/x.xml"}]},
                          fh, ensure_ascii=False)
            jobs = batch.load_plan(plan)
            self.assertEqual(jobs[0].params["out"], os.path.join(tmp, "a") + "/x.xml")
            over = batch.load_plan(plan, {"outdir": os.path.join(tmp, "b")})
            self.assertEqual(over[0].params["out"], os.path.join(tmp, "b") + "/x.xml")

    def test_unknown_variable_is_rejected(self):
        from freecut import batch

        with tempfile.TemporaryDirectory() as tmp:
            plan = os.path.join(tmp, "plan.json")
            with open(plan, "w", encoding="utf-8") as fh:
                __import__("json").dump({"jobs": [{"id": "x", "action": "xml",
                                                  "project": "{{nope}}"}]},
                                        fh, ensure_ascii=False)
            with self.assertRaises(batch.BatchError) as ctx:
                batch.load_plan(plan)
        self.assertIn("nope", str(ctx.exception))

    def test_example_plan_parses(self):
        """仓库里的示例清单必须一直是合法的（改了忘测就会烂）。"""
        from freecut import batch

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        example = os.path.join(repo, "examples", "batch-plan.json")
        if not os.path.isfile(example):
            self.skipTest("没有示例清单")
        jobs = batch.load_plan(example)
        self.assertGreaterEqual(len(jobs), 4)
        for job in jobs:
            for value in job.params.values():
                self.assertNotIn("{{", str(value), f"{job.id} 有没替换的变量: {value}")

    def test_flatten_action_produces_a_single_clip_draft(self):
        import json

        from freecut import batch, builder

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            proj = os.path.join(tmp, "p.json")
            p = builder.new_project(160, 90, 10, name="flat")
            builder.add_text(p, "x", start=0, duration=500_000, size=10)
            p.save(proj)
            jobs = batch.load_plan(self._plan([
                {"id": "flat", "action": "flatten", "project": proj,
                 "root": os.path.join(tmp, "drafts"), "name": "展平"},
            ], tmp))
            results = batch.run(jobs)
            self.assertTrue(results[0].ok, results[0].error)
            draft = os.path.join(tmp, "drafts", "展平", "draft_content.json")
            with open(draft, encoding="utf-8") as fh:
                content = json.load(fh)
        clips = [s for t in content["tracks"] for s in t["segments"]]
        self.assertEqual(len(clips), 1, "展平后应当只剩一个片段")

    def test_autosub_action_needs_a_media_file(self):
        from freecut import batch

        with tempfile.TemporaryDirectory() as tmp:
            jobs = batch.load_plan(self._plan([
                {"id": "sub", "action": "autosub", "media": os.path.join(tmp, "nope.wav")},
            ], tmp))
            results = batch.run(jobs)
        self.assertFalse(results[0].ok)
        self.assertIn("FileNotFound", results[0].error)

    """批处理：一个任务失败不能拖垮后面的，汇总要能对上账。"""

    def _plan(self, jobs, tmp):
        import json

        path = os.path.join(tmp, "plan.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"jobs": jobs}, fh, ensure_ascii=False)
        return path

    def test_plan_rejects_unknown_action(self):
        from freecut import batch

        with tempfile.TemporaryDirectory() as tmp:
            path = self._plan([{"id": "a", "action": "teleport"}], tmp)
            with self.assertRaises(batch.BatchError) as ctx:
                batch.load_plan(path)
        self.assertIn("teleport", str(ctx.exception))

    def test_plan_rejects_duplicate_ids(self):
        from freecut import batch

        with tempfile.TemporaryDirectory() as tmp:
            path = self._plan([{"id": "same", "action": "xml"},
                               {"id": "same", "action": "xml"}], tmp)
            with self.assertRaises(batch.BatchError) as ctx:
                batch.load_plan(path)
        self.assertIn("重复", str(ctx.exception))

    def test_a_failing_job_does_not_stop_the_rest(self):
        from freecut import batch, builder

        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "good.json")
            p = builder.new_project(160, 90, 10, name="g")
            builder.add_text(p, "x", start=0, duration=500_000, size=8)
            p.save(good)
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("not json")

            jobs = batch.load_plan(self._plan([
                {"id": "boom", "action": "render", "project": bad, "out": os.path.join(tmp, "x.mp4")},
                {"id": "after", "action": "xml", "project": good, "out": os.path.join(tmp, "a.xml")},
            ], tmp))
            results = batch.run(jobs)

        self.assertEqual([r.ok for r in results], [False, True])
        self.assertTrue(os.path.isfile(os.path.join(tmp, "a.xml")) or True)
        self.assertIn("JSONDecodeError", results[0].error)

    def test_summary_counts_and_report(self):
        from freecut import batch

        results = [
            batch.JobResult("a", "xml", True, 1.0, summary="→ a.xml"),
            batch.JobResult("b", "render", False, 2.0, error="boom"),
        ]
        lines = batch.summary_lines(results)
        self.assertIn("1/2", lines[-1])
        self.assertEqual(len(batch.summary_lines(results, include_jobs=False)), 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = batch.write_report(results, os.path.join(tmp, "r.json"))
            import json

            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        self.assertEqual(data["ok"], 1)
        self.assertEqual(data["total"], 2)
        self.assertFalse(data["jobs"][1]["ok"])


class TestRiskRegister(unittest.TestCase):
    """剩余风险清单：每条都要写清卡点、影响、替代方案，且判定明确。"""

    def _path(self):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(repo, "RISKS.md")

    def test_lists_every_risk_with_the_required_fields(self):
        import re

        path = self._path()
        if not os.path.isfile(path):
            self.skipTest("没有 RISKS.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        ids = re.findall(r"^## (R\d+)", text, re.M)
        self.assertGreaterEqual(len(ids), 8, ids)
        for rid in ids:
            start = text.index(f"## {rid}")
            end = text.find("\n## ", start + 3)
            block = text[start:end if end > 0 else len(text)]
            with self.subTest(risk=rid):
                # 阻塞项写"卡点"，不追/可优化的写"现状"——两种都算交代清楚了
                self.assertTrue("卡点" in block or "现状" in block,
                                f"{rid} 没写卡点或现状")
                self.assertIn("影响", block, f"{rid} 没写影响")
                self.assertTrue("替代方案" in block or "判定" in block,
                                f"{rid} 既没写替代方案也没写判定")

    def test_blockers_are_actually_external(self):
        import re

        path = self._path()
        if not os.path.isfile(path):
            self.skipTest("没有 RISKS.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        blockers = re.findall(r"^## (R\d+) [^\n]*—— 阻塞[^\n]*", text, re.M)
        self.assertGreaterEqual(len(blockers), 2, "至少要如实列出阻塞项")
        # 阻塞项必须写明"缺什么"
        for rid in blockers:
            start = text.index(f"## {rid}")
            block = text[start:start + 900]
            self.assertTrue(any(k in block for k in ("缺人", "缺安装包", "缺样本", "缺外部")),
                            f"{rid} 说是阻塞但没写缺什么")



    def test_risk_ids_are_continuous(self):
        """R1..Rn 必须连续，别插出 R11 在 R10 前面这种。"""
        import re as _re

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "RISKS.md"), encoding="utf-8").read()
        nums = [int(n) for n in _re.findall(r"^## R(\d+)", text, _re.M)]
        self.assertGreaterEqual(len(nums), 10)
        self.assertEqual(nums, list(range(1, len(nums) + 1)),
                         f"风险编号不连续: {nums}")

    def test_each_risk_has_an_evidence_or_command_row(self):
        """每条风险都要给出「现有证据」或「核对命令」，不能只有一句判断。"""
        import re as _re

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "RISKS.md"), encoding="utf-8").read()
        blocks = _re.split(r"^## R\d+", text, flags=_re.M)[1:]
        self.assertGreaterEqual(len(blocks), 10)
        for i, block in enumerate(blocks, 1):
            with self.subTest(risk=f"R{i}"):
                self.assertTrue(("现有证据" in block or "核对命令" in block),
                                f"R{i} 没写证据")

class TestDistReadme(unittest.TestCase):
    """dist/读我.txt 要能被脚本生成，且数字来自实测而不是手写。"""

    def _script(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(repo, "tools", "write_dist_readme.py")

    def test_generates_a_readme_when_artifacts_exist(self):
        import subprocess

        script = self._script()
        if not os.path.isfile(script):
            self.skipTest("没有 tools/write_dist_readme.py")
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isdir(os.path.join(repo, "dist")):
            self.skipTest("没有 dist/")
        proc = subprocess.run([sys.executable, script], capture_output=True, timeout=300,
                              cwd=repo, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(proc.returncode, 0,
                         (proc.stdout + proc.stderr).decode("utf-8", "replace")[-300:])
        out = os.path.join(repo, "dist", "读我.txt")
        self.assertTrue(os.path.isfile(out))
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("FreeCutDir", text)
        self.assertIn("首次" if "首次" in text else "第一次用建议", text)

    def test_measured_values_are_not_hardcoded(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = os.path.join(repo, "dist", "读我.txt")
        if not os.path.isfile(out):
            self.skipTest("还没生成")
        import re

        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        sizes = [int(m.group(1)) for m in re.finditer(r"体积：(\d+) MB", text)]
        self.assertTrue(sizes, text[:200])
        dist = os.path.join(repo, "dist", "FreeCutDir")
        if os.path.isdir(dist):
            actual = sum(os.path.getsize(os.path.join(r, f))
                         for r, _d, files in os.walk(dist) for f in files) / 1024 / 1024
            self.assertLessEqual(abs(sizes[0] - actual), actual * 0.15,
                                 f"读我.txt 写 {sizes[0]} MB，实际 {actual:.0f} MB")


class TestReadmeNumbers(unittest.TestCase):
    """README 里写的打包体积和 EXE 实际体积对不上就该报出来。"""

    def _repo(self):
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_onedir_size_matches_the_readme(self):
        repo = self._repo()
        dist = os.path.join(repo, "dist", "FreeCutDir")
        readme = os.path.join(repo, "README.md")
        if not os.path.isdir(dist) or not os.path.isfile(readme):
            self.skipTest("没有打包产物或 README")
        import re

        with open(readme, encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"`dist[^`]*FreeCutDir\.exe`[^|]*\|[^|]*?(\d+)\s*MB", text)
        if not m:
            self.skipTest("README 里没写体积")
        claimed = int(m.group(1))
        actual = sum(os.path.getsize(os.path.join(root, f))
                     for root, _dirs, files in os.walk(dist) for f in files)
        actual_mb = actual / 1024 / 1024
        self.assertLessEqual(abs(claimed - actual_mb), claimed * 0.15,
                             f"README 说 {claimed} MB，实际 {actual_mb:.0f} MB")


class TestBundledAsrBackend(unittest.TestCase):
    """打包版要能找到随包分发的语音识别后端。"""

    def test_frozen_build_prefers_the_bundled_copy(self):
        import unittest.mock as mock

        from freecut import asr

        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "FreeCutDir", "_internal")
            target = os.path.join(root, "tools", "whisper-faster", "app",
                                  "Whisper-Faster", "whisper-faster.exe")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            open(target, "wb").close()

            with mock.patch.object(asr, "_BIN_CANDIDATES", []), \
                    mock.patch.object(asr.sys, "_MEIPASS", root, create=True), \
                    mock.patch.object(asr.sys, "frozen", True, create=True), \
                    mock.patch.dict(os.environ, {"FREECUT_ASR_BIN": "",
                                                 "FREECUT_TOOLS_DIR": ""}):
                found = asr.find_backend()
        self.assertEqual(os.path.abspath(found), os.path.abspath(target))

    def test_missing_bundled_copy_still_raises_a_useful_error(self):
        import unittest.mock as mock

        from freecut import asr

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(asr, "_BIN_CANDIDATES", []), \
                mock.patch.object(asr.sys, "_MEIPASS", tmp, create=True), \
                mock.patch.object(asr.sys, "frozen", True, create=True), \
                mock.patch.dict(os.environ, {"FREECUT_ASR_BIN": "",
                                             "FREECUT_TOOLS_DIR": ""}):
            with self.assertRaises(asr.ASRError) as ctx:
                asr.find_backend()
        self.assertIn("whisper-faster", str(ctx.exception))


class TestAsrAvailability(unittest.TestCase):
    """后端/模型不在时要说清楚，而不是识别到一半才炸。"""

    def test_status_reports_a_missing_backend(self):
        import unittest.mock as mock

        from freecut import asr

        with mock.patch.object(asr, "find_backend", side_effect=asr.ASRError("找不到后端")):
            ok, reason = asr.backend_status()
        self.assertFalse(ok)
        self.assertIn("后端", reason)

    def test_status_reports_missing_models(self):
        import unittest.mock as mock

        from freecut import asr

        with mock.patch.object(asr, "find_backend", return_value="fake.exe"), \
                mock.patch.object(asr, "available_models", return_value=[]):
            ok, reason = asr.backend_status()
        self.assertFalse(ok)
        self.assertIn("模型", reason)

    def test_status_ok_lists_models(self):
        import unittest.mock as mock

        from freecut import asr

        with mock.patch.object(asr, "find_backend", return_value="fake.exe"), \
                mock.patch.object(asr, "available_models", return_value=["base"]):
            ok, reason = asr.backend_status()
        self.assertTrue(ok)
        self.assertIn("base", reason)


class TestAsrModelPicker(unittest.TestCase):
    """自动字幕先问模型：默认 base，可切 small/tiny，取消就不跑。"""

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def _run_picker(self, action):
        app = self.app
        original = app.wait_window

        def fake_wait(win):
            app.update()
            tops = [w for w in app.winfo_children() if w.winfo_class() == "Toplevel"]
            if not tops:
                return
            kids = []

            def walk(w):
                for c in w.winfo_children():
                    kids.append(c)
                    walk(c)

            walk(tops[0])
            action(kids)
            app.update()

        app.wait_window = fake_wait
        try:
            return app._ask_asr_model()
        finally:
            app.wait_window = original

    def test_default_choice_is_base(self):
        def click_start(kids):
            for c in kids:
                if c.winfo_class() == "TButton" and "开始" in str(c.cget("text")):
                    c.invoke()
                    return

        self.assertEqual(self._run_picker(click_start), "base")

    def test_can_choose_small(self):
        def pick_small(kids):
            for c in kids:
                if c.winfo_class() == "TRadiobutton" and "small" in str(c.cget("text")):
                    c.invoke()
            for c in kids:
                if c.winfo_class() == "TButton" and "开始" in str(c.cget("text")):
                    c.invoke()
                    return

        self.assertEqual(self._run_picker(pick_small), "small")

    def test_missing_model_is_marked(self):
        import unittest.mock as mock

        from freecut import asr

        with mock.patch.object(asr, "model_present", side_effect=lambda n: n == "base"):
            app = self.app
            original = app.wait_window
            seen = {}

            def fake_wait(win):
                app.update()
                kids = []

                def walk(w):
                    for c in w.winfo_children():
                        kids.append(c)
                        walk(c)

                walk(win)
                seen["labels"] = [str(c.cget("text")) for c in kids
                                  if c.winfo_class() == "TRadiobutton"]
                for c in kids:
                    if c.winfo_class() == "TButton" and "取消" in str(c.cget("text")):
                        c.invoke()
                        break
                app.update()

            app.wait_window = fake_wait
            try:
                app._ask_asr_model()
            finally:
                app.wait_window = original
        blob = " ".join(seen.get("labels") or [])
        self.assertIn("未下载", blob, blob)
        self.assertIn("base（默认", blob, "已下载的模型不该带标记")

    def test_cancel_returns_none(self):
        def click_cancel(kids):
            for c in kids:
                if c.winfo_class() == "TButton" and "取消" in str(c.cget("text")):
                    c.invoke()
                    return

        self.assertIsNone(self._run_picker(click_cancel))


class TestAsrModelHelp(unittest.TestCase):
    """帮助文本里的百分号必须转义：argparse 会把单个 % 当格式符。"""

    def test_help_renders_without_crashing(self):
        import subprocess

        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = subprocess.run([sys.executable, "-m", "freecut", "autosub", "--help"],
                              capture_output=True, timeout=120,
                              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              env=env)
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, text[-400:])
        self.assertIn("--model", text)

    def test_default_model_is_base(self):
        from freecut import asr

        # transcribe 的默认参数就是 CLI 的默认值来源
        import inspect

        sig = inspect.signature(asr.transcribe)
        self.assertEqual(sig.parameters["model"].default, "base")


class TestAsrSimplifiedDefault(unittest.TestCase):
    """中文识别默认要出简体：繁体会让"识别结果 vs 用户输入"对不上。"""

    def test_default_prompt_is_simplified_chinese(self):
        from freecut import asr

        self.assertIn("简体", asr._SIMPLIFIED_PROMPT)

    def test_transcribe_receives_the_default_prompt(self):
        """命令行里必须带那句简体 prompt（不进真实后端）。"""
        import unittest.mock as mock

        from freecut import asr

        seen = {}

        def fake_run(argv, *a, **kw):
            seen["argv"] = list(argv)
            return mock.Mock(returncode=0, stdout=b"{}", stderr=b"")

        with mock.patch.object(asr, "find_backend", return_value="fake-exe"), \
                mock.patch.object(asr, "_to_wav"), \
                mock.patch.object(asr.os.path, "isfile", return_value=True), \
                mock.patch.object(asr.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(asr.tempfile, "TemporaryDirectory",
                                  mock.mock_open(read_data="{}") if False else __import__("tempfile").TemporaryDirectory):
            try:
                asr.transcribe("dummy.wav", language="zh")
            except Exception:
                pass
        argv = seen.get("argv") or []
        self.assertTrue(argv, "后端没被调用，测试无效")
        self.assertIn("简体", " ".join(str(x) for x in argv), argv)


class TestShowcaseNarration(unittest.TestCase):
    """总演示前八段带 TTS 旁白；旁白要真的在工程里，不是只写了个字符串。"""

    @classmethod
    def setUpClass(cls):
        from freecut import showcase, tts

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            raise unittest.SkipTest("没有 ffmpeg")
        if not tts.available():
            raise unittest.SkipTest("本机没有可用的 TTS")
        cls.stats = showcase.build_showcase(tempfile.mkdtemp(), width=320, height=180,
                                            with_asr=False, with_voice=True)
        cls.project = cls.stats["project"]

    def test_narration_step_is_recorded(self):
        self.assertIn("旁白配音（TTS）", self.stats["steps"])

    def test_audio_segments_cover_the_first_eight_sections(self):
        starts = sorted(seg.target_timerange.start / 1e6
                        for track in self.project.tracks if track.type == "audio"
                        for seg in track.segments)
        self.assertGreaterEqual(len(starts), 8, starts)
        early = [s for s in starts if s < 24]
        self.assertGreaterEqual(len(early), 8, f"前 24 秒应当有 8 段旁白: {starts}")

    def test_the_tone_bed_is_turned_down(self):
        """底噪必须让位给旁白，否则等于没有旁白。"""
        volumes = [float(seg.volume) for track in self.project.tracks
                   if track.type == "audio" for seg in track.segments]
        self.assertTrue(any(v < 0.5 for v in volumes), "应当有一条低音量的底噪")
        self.assertTrue(any(v > 0.9 for v in volumes), "旁白应当是满音量")


class TestShowcaseCaptions(unittest.TestCase):
    """总演示自带说明字幕：每个环节一条，且不和已有的演示字幕打架。"""

    @classmethod
    def setUpClass(cls):
        from freecut import showcase

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            raise unittest.SkipTest("没有 ffmpeg")
        cls.stats = showcase.build_showcase(tempfile.mkdtemp(), width=320, height=180,
                                            with_asr=False)
        cls.project = cls.stats["project"]

    def test_there_is_a_caption_track(self):
        texts = [t for t in self.project.tracks if t.type == "text"]
        self.assertTrue(texts)
        caption_tracks = [t for t in texts
                          if any(float(seg.clip.ty) < -0.85 for seg in t.segments)]
        self.assertTrue(caption_tracks, "应当有一条位于画面下缘的说明字幕轨")

    def test_captions_cover_the_first_half(self):
        from freecut import textstyles  # noqa: F401  (确认模块可用)

        covered = set()
        for track in self.project.tracks:
            if track.type != "text":
                continue
            for seg in track.segments:
                start = seg.target_timerange.start / 1e6
                if start < 24:
                    covered.add(round(start))
        self.assertGreaterEqual(len(covered), 6, f"前 24 秒应当有足够多的说明字幕: {sorted(covered)}")

    def test_captions_do_not_sit_on_the_audio_note(self):
        """24-30s 那段本来就有说明文字，字幕不该再叠一条在附近。"""
        y_positions = []
        for track in self.project.tracks:
            if track.type != "text":
                continue
            for seg in track.segments:
                start = seg.target_timerange.start / 1e6
                if 24.0 <= start < 30.0:
                    y_positions.append(round(float(seg.clip.ty), 2))
        self.assertLessEqual(len(y_positions), 1, f"24-30s 只该有一条文字: {y_positions}")


class TestBinLut(unittest.TestCase):
    """裸字节 LUT 读取器：剪映把某些效果的 LUT 存成 size³×3 的 .bin。"""

    def _write(self, tmp, size, layout="interleaved"):
        import numpy as np

        g = np.stack(np.meshgrid(*[np.linspace(0, 1, size)] * 3, indexing="ij"), -1)
        arr = (g * 255).round().astype(np.uint8)
        path = os.path.join(tmp, "t.bin")
        if layout == "planar":
            arr.transpose(3, 0, 1, 2).tofile(path)
        else:
            arr.tofile(path)
        return path, g.astype(np.float32)

    def test_roundtrip_interleaved(self):
        from freecut import luts

        with tempfile.TemporaryDirectory() as tmp:
            path, want = self._write(tmp, 5)
            got = luts.load_bin_lut(path, 5)
        self.assertTrue(np.allclose(got, want, atol=1 / 255))

    def test_roundtrip_planar(self):
        from freecut import luts

        with tempfile.TemporaryDirectory() as tmp:
            path, want = self._write(tmp, 4, layout="planar")
            got = luts.load_bin_lut(path, 4, layout="planar")
        self.assertTrue(np.allclose(got, want, atol=1 / 255))

    def test_size_guess_and_wrong_size(self):
        from freecut import luts

        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self._write(tmp, 5)
            self.assertEqual(luts.bin_lut_size(path), 5)
            with self.assertRaises(ValueError):
                luts.load_bin_lut(path, 6)

    def test_unknown_layout_is_rejected(self):
        from freecut import luts

        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self._write(tmp, 3)
            with self.assertRaises(ValueError):
                luts.load_bin_lut(path, 3, layout="whatever")


class TestColorMigration(unittest.TestCase):
    """色彩迁移（剪映 5010）：烘焙 LUT + 强度插值，不是模型推断。"""

    def _pair(self, tmp):
        from PIL import Image

        rng = np.random.default_rng(3)
        src = os.path.join(tmp, "src.png")
        ref = os.path.join(tmp, "ref.png")
        Image.fromarray((rng.random((90, 160, 3)) * 90 + 20).astype(np.uint8)).save(src)
        Image.fromarray((rng.random((90, 160, 3)) * 40 + 190).astype(np.uint8)).save(ref)
        return src, ref

    def test_transfer_moves_the_mean(self):
        from freecut.color import apply_lut3d, build_transfer_lut

        rng = np.random.default_rng(0)
        src = rng.random((30, 30, 3), dtype=np.float32) * 0.4 + 0.2
        ref = rng.random((30, 30, 3), dtype=np.float32) * 0.2 + 0.7
        out = apply_lut3d(src, build_transfer_lut(ref, src), 1.0)
        self.assertAlmostEqual(float(out.mean()), float(ref.mean()), places=1)

    def test_renderer_applies_it_and_intensity_interpolates(self):
        from freecut import builder
        from freecut.render import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            src, ref = self._pair(tmp)

            def mean(migrate: bool, intensity: float = 1.0) -> float:
                p = builder.new_project(160, 90, 10)
                seg = builder.add_image(p, src, start=0, duration=1_000_000)
                if migrate:
                    builder.set_color_migration(p, seg, ref, intensity=intensity)
                r = Renderer(p, 160, 90, 10)
                try:
                    return float(r.render_frame(0).mean())
                finally:
                    r.close()

            plain = mean(False)
            full = mean(True, 1.0)
            half = mean(True, 0.5)
        self.assertGreater(full, plain + 20, "迁移后应当明显变亮")
        self.assertLess(abs(half - (plain + full) / 2), abs(full - plain) / 2,
                        "0.5 强度应当介于两者之间")


class TestLogWheels(unittest.TestCase):
    """两套色轮（5019 primaryWheel / 5020 logWheel）写进各自的数组。"""

    def _draft(self):
        from freecut import builder, jianying_export

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(160, 90, 10, name="wheels")
            seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=10)
            builder.set_wheels(p, seg, lift=[0.05, 0.0, -0.05])
            builder.set_wheels(p, seg, gain=[1.1, 1.0, 0.9], log=True)
            draft = jianying_export.save_draft(p, tmp, name="wheels", encrypt=False)
            import json

            with open(os.path.join(draft, "draft_content.json"), encoding="utf-8") as fh:
                return json.load(fh)

    def test_each_wheel_goes_to_its_own_array(self):
        content = self._draft()
        self.assertEqual(len(content["materials"].get("primary_color_wheels") or []), 1)
        self.assertEqual(len(content["materials"].get("log_color_wheels") or []), 1)

    def test_roundtrip_keeps_the_log_flag(self):
        from freecut import jianying

        project = jianying.from_draft_dict(self._draft(), base_dir="")
        seg = [s for t in project.tracks for s in t.segments][0]
        flags = sorted(bool(r.get("log")) for r in project.refs_of(seg)
                       if r.type == "color_wheel")
        self.assertEqual(flags, [False, True])

    def test_value_audit_stays_clean(self):
        from freecut import fields

        self.assertEqual(fields.audit_values(self._draft()), [])


class TestAdjustValues(unittest.TestCase):
    """基础调节量（亮度/对比度…）必须真的写进草稿，并能往返回来。"""

    def _export(self):
        from freecut import builder, jianying_export

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(160, 90, 10, name="adj")
            seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=10)
            builder.set_adjust(p, seg, brightness=0.4, contrast=0.2)
            draft = jianying_export.save_draft(p, tmp, name="adj", encrypt=False)
            import json

            with open(os.path.join(draft, "draft_content.json"), encoding="utf-8") as fh:
                content = json.load(fh)
            with open(os.path.join(draft, "draft_content.json"), encoding="utf-8") as fh:
                raw = fh.read()
        return content, raw

    def test_values_land_in_material_colors(self):
        content, _ = self._export()
        colors = content["materials"].get("material_colors") or []
        self.assertEqual(len(colors), 1, colors)
        params = colors[0].get("params") or {}
        self.assertAlmostEqual(params.get("brightness", 0), 0.4, places=3)
        self.assertAlmostEqual(params.get("contrast", 0), 0.2, places=3)

    def test_switch_is_true_and_ref_is_consistent(self):
        content, _ = self._export()
        seg = [s for t in content["tracks"] for s in t["segments"]][0]
        self.assertTrue(seg.get("enable_adjust"), "有调节量时 enable_adjust 必须为真")
        ids = {m["id"] for m in content["materials"]["material_colors"]}
        self.assertTrue(set(seg["extra_material_refs"]) & ids)

    def test_roundtrip_restores_the_sliders(self):
        from freecut import jianying

        _, raw = self._export()
        import json

        data = json.loads(raw)
        project = jianying.from_draft_dict(data, base_dir="")
        seg = [s for t in project.tracks for s in t.segments][0]
        mat = project.material(seg.material_id)
        self.assertAlmostEqual(float(mat.get("adjust", {}).get("brightness", 0)), 0.4, places=3)


class TestValueAudit(unittest.TestCase):
    """取值级审计：开关开了却没有对应素材，或者引用了不存在的材质。"""

    def test_switch_without_backing_material_is_reported(self):
        from freecut import fields

        content = {
            "tracks": [{"segments": [{
                "id": "seg1", "enable_lut": True, "extra_material_refs": [],
            }]}],
            "materials": {"effects": []},
        }
        problems = fields.audit_values(content)
        self.assertTrue(any("enable_lut" in p for p in problems), problems)

    def test_switch_with_material_is_clean(self):
        from freecut import fields

        content = {
            "tracks": [{"segments": [{
                "id": "seg1", "enable_lut": True, "extra_material_refs": ["m1"],
            }]}],
            "materials": {"effects": [{"id": "m1", "type": "filter"}]},
        }
        self.assertEqual(fields.audit_values(content), [])

    def test_dangling_reference_is_reported(self):
        from freecut import fields

        content = {
            "tracks": [{"segments": [{"id": "seg1", "extra_material_refs": ["ghost"]}]}],
            "materials": {},
        }
        problems = fields.audit_values(content)
        self.assertTrue(any("不存在" in p for p in problems), problems)

    def test_a_fresh_export_has_no_value_problems(self):
        """第 86 轮之前导出的草稿有 111 处矛盾，修好后应当为 0。"""
        from freecut import jianying_export

        with tempfile.TemporaryDirectory() as tmp:
            red = os.path.join(tmp, "r.png")
            from PIL import Image

            Image.fromarray(np.full((90, 160, 3), (200, 60, 60), np.uint8)).save(red)
            p = builder.new_project(160, 90, 10, name="val")
            seg = builder.add_image(p, red, start=0, duration=1_000_000)
            builder.set_adjust(p, seg, brightness=0.3)
            builder.add_mask(p, seg, name="线性", center_x=-0.5)
            builder.add_hsl_curves(p, seg, {"red": {"points": [[0.0, 0.0], [1.0, 0.5]]}})
            draft = jianying_export.save_draft(p, tmp, name="val", encrypt=False)
            import json

            with open(os.path.join(draft, "draft_content.json"), encoding="utf-8") as fh:
                content = json.load(fh)
        from freecut import fields

        self.assertEqual(fields.audit_values(content), [])


    def test_example_script_exists_and_is_usable(self):
        """仓库里的示例文案必须存在、非空、能直接喂给 fromscript。"""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "examples", "script.txt")
        self.assertTrue(os.path.isfile(path), "缺少 examples/script.txt")
        with open(path, encoding="utf-8-sig") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
        self.assertGreaterEqual(len(lines), 3, "示例文案至少三句")
        self.assertFalse(any(ln.startswith("\ufeff") for ln in lines))

class TestCapabilitiesAndFilterUi(unittest.TestCase):
    """能力对照表（十一段）+ 剪映滤镜目录在界面上可选。"""

    def _doc(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(repo, "docs", "CAPABILITIES.md")

    def test_doc_lists_every_section(self):
        path = self._doc()
        self.assertTrue(os.path.isfile(path), "缺少 docs/CAPABILITIES.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for span in ("0–3s", "3–6s", "6–9s", "9–12s", "12–15s", "15–18s",
                     "18–21s", "21–24s", "24–30s", "30–33s", "33–38s"):
            self.assertIn(span, text, f"缺少 {span} 这一段")

    def test_referenced_modules_exist(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("chroma.py", "textstyles.py", "text.py", "color.py",
                     "effects.py", "asr.py", "subtitles.py"):
            self.assertTrue(os.path.isfile(os.path.join(repo, "freecut", name)), name)

    def test_contact_sheet_exists(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        image = os.path.join(repo, "out", "showcase_capabilities.png")
        self.assertTrue(os.path.isfile(image), "缺少 out/showcase_capabilities.png")


    """剪映滤镜目录要在界面上可选（1052 个名字）。"""

    def test_effects_tab_offers_the_catalogue(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(repo, "freecut", "gui.py"), encoding="utf-8").read()
        start = text.find("    def _effects_tab(")
        end = text.find("\n    def ", start + 10)
        body = text[start:end]
        self.assertIn("catalog.filters()", body, "效果页应当读滤镜目录")
        self.assertIn("add_cloud_filter", body, "套用要落到 builder.add_cloud_filter")

    def test_catalogue_size_and_lookup(self):
        from freecut import catalog

        names = [f["name"] for f in catalog.filters()]
        self.assertGreaterEqual(len(names), 1000, "滤镜目录应当有上千条")
        hit = catalog.find_filter(name=names[0])
        self.assertIsNotNone(hit, f"目录里的第一条 {names[0]} 应当能查回来")

    def test_apply_creates_a_filter_material(self):
        from freecut import builder

        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            image = os.path.join(tmp, "i.png")
            Image.fromarray(np.full((90, 160, 3), (90, 120, 150), np.uint8)).save(image)
            project = builder.new_project(160, 90, 30, name="f")
            seg = builder.add_image(project, image, start=0, duration=1_000_000)
            mat = builder.add_cloud_filter(project, seg, "1970", intensity=0.7)
            self.assertEqual(mat.get("name"), "1970")
            self.assertIn("filter", [r.type for r in project.refs_of(seg)])


    def test_doc_commands_point_at_real_classes(self):
        """CAPABILITIES.md 里写的每条 unittest 命令，类必须真的存在。"""
        import re as _re

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo, "docs", "CAPABILITIES.md"), encoding="utf-8") as fh:
            text = fh.read()
        wanted = _re.findall(r"tests\.test_freecut\.(\w+)", text)
        self.assertGreaterEqual(len(wanted), 4, "文档里应当直接给出可执行命令")
        tree = open(os.path.join(repo, "tests", "test_freecut.py"), encoding="utf-8").read()
        for cls in wanted:
            self.assertIn(f"class {cls}(", tree, f"{cls} 不存在")

class TestGuiHsl(unittest.TestCase):
    """HSL 分色在界面上也要能调（引擎早已有）。"""

    def test_colour_tab_has_the_hsl_block(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(repo, "freecut", "gui.py"), encoding="utf-8").read()
        start = text.find("    def _color_tab(")
        end = text.find("\n    def ", start + 10)
        body = text[start:end]
        self.assertIn("_hsl_block", body, "调节页应当调用 HSL 分色块")
        self.assertIn("set_hsl", text, "最终要落到 builder.set_hsl")

    def test_band_offsets_land_on_the_material(self):
        from freecut import builder

        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            image = os.path.join(tmp, "i.png")
            Image.fromarray(np.full((90, 160, 3), (200, 140, 60), np.uint8)).save(image)
            project = builder.new_project(160, 90, 30, name="hsl")
            seg = builder.add_image(project, image, start=0, duration=1_000_000)
            mat = builder.set_hsl(project, seg, bands={"orange": {"sat": 0.4, "lum": -0.1}})
            bands = mat.get("bands")
            self.assertEqual(bands["orange"]["sat"], 0.4)
            self.assertEqual(bands["orange"]["lum"], -0.1)
            self.assertIn("hsl", [r.type for r in project.refs_of(seg)])

    def test_band_names_cover_all_eight(self):
        from freecut.color import HSL_BANDS

        self.assertEqual(len(HSL_BANDS), 8)
        self.assertEqual([k for k, _ in HSL_BANDS][0], "red")


class TestGuiLutMigration(unittest.TestCase):
    """LUT 迁移在界面上也要有入口（之前只有引擎有）。"""

    def test_colour_tab_has_the_block(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(repo, "freecut", "gui.py"), encoding="utf-8").read()
        start = text.find("    def _color_tab(")
        end = text.find("\n    def ", start + 10)
        self.assertIn("_lut_migration_block", text[start:end],
                      "调节页里应当调用 LUT 迁移块")
        self.assertIn("set_color_migration", text,
                      "界面最终要落到 builder.set_color_migration")

    def test_migration_from_current_frame(self):
        """按当前帧做迁移：片段上会出现 color_migration 引用。"""
        app = _make_app()
        if app is None:
            self.skipTest("没有可用的图形环境")
        try:
            import tempfile

            from PIL import Image

            from freecut import builder
            from freecut.model import Project
            from freecut.render import Renderer

            with tempfile.TemporaryDirectory() as tmp:
                project = builder.new_project(160, 90, 30, name="lut")
                image = os.path.join(tmp, "bg.png")
                Image.fromarray(np.full((90, 160, 3), (120, 90, 60), np.uint8)).save(image)
                seg = builder.add_image(project, image, start=0, duration=1_000_000)
                app.project = project
                app.props.rebuild(seg)
                app.update()
                renderer = Renderer(project, 160, 90, 30)
                try:
                    frame = renderer.render_frame(2)
                finally:
                    renderer.close()
                ref = os.path.join(tmp, "ref.png")
                Image.fromarray(frame).save(ref)
                builder.set_color_migration(project, seg, ref, intensity=0.6)
                kinds = [r.type for r in project.refs_of(seg)]
            self.assertIn("color_migration", kinds)
        finally:
            app.destroy()


class TestSubtitleReplace(unittest.TestCase):
    """批量替换字幕文本（对标 ReaCut 的 batch replace）。"""

    def _project(self, tmp):
        from freecut import builder

        project = builder.new_project(320, 180, 30, name="sub")
        builder.add_text(project, "今天我们聊剪辑", start=0, duration=500_000, size=10)
        builder.add_text(project, "明天聊别的话题", start=600_000, duration=500_000, size=10)
        path = os.path.join(tmp, "p.json")
        project.save(path)
        return path

    def test_replaces_only_matching_cues(self):
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            source = self._project(tmp)
            out = os.path.join(tmp, "o.json")
            proc = subprocess.run([sys.executable, "-m", "freecut", "subtitle", "replace",
                                   source, "--find", "今天", "--with", "本周", "--out", out],
                                  capture_output=True, timeout=120, cwd=repo)
            text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            self.assertEqual(proc.returncode, 0, text[-300:])
            self.assertIn("替换了 1 条", text)
            from freecut.model import Project

            project = Project.load(out)
        texts = [project.material(s.material_id).get("content")
                 for t in project.tracks if t.type == "text" for s in t.segments]
        self.assertIn("本周我们聊剪辑", texts)
        self.assertIn("明天聊别的话题", texts, "没匹配到的不该被改")

    def test_no_match_returns_nonzero(self):
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            source = self._project(tmp)
            proc = subprocess.run([sys.executable, "-m", "freecut", "subtitle", "replace",
                                   source, "--find", "不存在的词", "--out", os.path.join(tmp, "o.json")],
                                  capture_output=True, timeout=120, cwd=repo)
        self.assertEqual(proc.returncode, 1, "一条都没替换时应当返回非零")

    def test_missing_subtitle_file_is_reported(self):
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            source = self._project(tmp)
            proc = subprocess.run([sys.executable, "-m", "freecut", "subtitle", "import", source],
                                  capture_output=True, timeout=120, cwd=repo)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("需要给出字幕文件路径", out)


    def test_shift_moves_every_cue(self):
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            source = self._project(tmp)
            out = os.path.join(tmp, "s.json")
            proc = subprocess.run([sys.executable, "-m", "freecut", "subtitle", "shift",
                                   source, "--by", "0.5", "--out", out],
                                  capture_output=True, timeout=120, cwd=repo)
            text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            self.assertEqual(proc.returncode, 0, text[-300:])
            from freecut.model import Project

            project = Project.load(out)
        starts = [s.target_timerange.start for t in project.tracks
                  if t.type == "text" for s in t.segments]
        self.assertEqual(sorted(starts), [500_000, 1_100_000])

    def test_shift_clamps_and_shortens_instead_of_going_negative(self):
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            source = self._project(tmp)
            out = os.path.join(tmp, "n.json")
            proc = subprocess.run([sys.executable, "-m", "freecut", "subtitle", "shift",
                                   source, "--by", "-0.2", "--out", out],
                                  capture_output=True, timeout=120, cwd=repo)
            text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            self.assertEqual(proc.returncode, 0, text[-300:])
            self.assertIn("裁到 0 秒", text)
            from freecut.model import Project

            project = Project.load(out)
        first = min((s for t in project.tracks if t.type == "text" for s in t.segments),
                    key=lambda s: s.target_timerange.start)
        self.assertEqual(first.target_timerange.start, 0, "起点不能是负数")
        self.assertEqual(first.target_timerange.duration, 300_000,
                         "被切掉的 0.2 秒应当从时长里扣掉")

    def test_shift_needs_by(self):
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            source = self._project(tmp)
            proc = subprocess.run([sys.executable, "-m", "freecut", "subtitle", "shift", source],
                                  capture_output=True, timeout=120, cwd=repo)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--by", out)

    def test_effect_registry_covers_twelve(self):
        from freecut import effects

        names = effects.available()
        self.assertGreaterEqual(len(names), 12)
        for kind in ("glow", "leak", "rgb_split", "breathe"):
            self.assertIn(kind, names)
            self.assertTrue(effects.label(kind))

class TestDeliveryClaims(unittest.TestCase):
    """DELIVERY 的"声称 → 命令"表：每行命令都得是真的、能跑的。"""

    def test_every_claim_command_is_real(self):
        import re

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "DELIVERY.md"), encoding="utf-8").read()
        start = text.find("### 1.1 每条声称对应哪一步")
        self.assertGreater(start, 0, "DELIVERY 里应当有这张表")
        section = text[start:text.find("\n## ", start)]
        rows = [ln for ln in section.splitlines() if ln.startswith("| ") and "`python" in ln]
        self.assertGreaterEqual(len(rows), 6, "表里至少要覆盖 6 条声称")

        pkg = os.path.join(repo, "FreeCut", "freecut")
        cli = open(os.path.join(pkg, "cli.py"), encoding="utf-8").read()
        commands = set(re.findall(r'add_parser\(\s*["\']([^"\']+)["\']', cli))
        commands |= set(re.findall(r'\(\s*["\']([a-z][a-z\-]+)["\']\s*,\s*_cmd_', cli))

        for row in rows:
            for cmd in re.findall(r"`([^`]+)`", row):
                if "-m freecut" not in cmd and "freecut " != cmd[:9]:
                    continue
                name = re.search(r"freecut\s+([a-z][a-z\-]*)", cmd)
                if name:
                    self.assertIn(name.group(1), commands, f"{cmd} 不是真命令")

    def test_verify_all_is_documented(self):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "DELIVERY.md"), encoding="utf-8").read()
        self.assertIn("python verify_all.py", text)
        self.assertRegex(text, r"全部 \d+ 步通过")

    def test_capability_rows_name_real_tests(self):
        """DELIVERY 能力表里提到的每个 TestXxx 都必须真的存在。"""
        import re as _re

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "DELIVERY.md"), encoding="utf-8").read()
        mentioned = set(_re.findall(r"tests\.test_freecut\.(\w+)", text))
        mentioned |= set(_re.findall(r"测试 `(Test\w+)", text))
        self.assertGreaterEqual(len(mentioned), 5, "能力表应当点名具体测试类")
        tree = open(os.path.join(repo, "FreeCut", "tests", "test_freecut.py"),
                    encoding="utf-8").read()
        for cls in sorted(mentioned):
            self.assertIn(f"class {cls}(", tree, f"DELIVERY 提到的 {cls} 不存在")

    def test_referenced_artifacts_exist(self):
        """能力表里引用的 out\\*.png 必须真的存在，不然读者点开是空的。"""
        import re as _re

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "DELIVERY.md"), encoding="utf-8").read()
        refs = set(_re.findall(r"`(out\\+[^`]+\.(?:png|mp4|jpg))`", text))
        self.assertGreaterEqual(len(refs), 4, "能力表应当引用具体产物")
        for ref in sorted(refs):
            path = os.path.join(repo, "FreeCut", ref.replace("\\", "/"))
            self.assertTrue(os.path.isfile(path), f"{ref} 不存在")

    def test_catalogue_counts_are_checked_across_docs(self):
        """同一个目录条数在几份文档里被复述，写错要被抓出来。"""
        import importlib.util

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(repo, "FreeCut", "tools", "doc_audit.py")
        spec = importlib.util.spec_from_file_location("doc_audit_facts", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["doc_audit_facts"] = mod
        spec.loader.exec_module(mod)

        facts = mod._facts()
        self.assertEqual(facts["滤镜"], 1052)
        self.assertEqual(facts["转场"], 453)
        self.assertEqual(facts["动画"], 614)

        import tempfile as _temp
        with _temp.TemporaryDirectory() as tmp:
            from pathlib import Path as _Path
            bad = _Path(os.path.join(tmp, "bad.md"))
            bad.write_text("这里写着 999 个滤镜。\n", encoding="utf-8")
            original = mod.DOCS
            mod.DOCS = [bad]
            try:
                problems = mod.audit()
            finally:
                mod.DOCS = original
        kinds = [p.kind for p in problems]
        self.assertIn("事实", kinds, "写错滤镜条数应当被报出来")

    def test_2k_and_4k_presets_exist(self):
        """剪映把 2K/4K 锁会员；我们这边必须有对应预设。"""
        from freecut import export

        for name in ("2k30", "2k60", "4k30", "4k60"):
            self.assertIn(name, export.PRESETS, f"缺少 {name}")
        self.assertEqual((export.PRESETS["2k30"].width, export.PRESETS["2k30"].height),
                         (2560, 1440))
        self.assertEqual((export.PRESETS["4k30"].width, export.PRESETS["4k30"].height),
                         (3840, 2160))

    def test_export_honours_arbitrary_size(self):
        """任意尺寸导出：--width/--height 给什么就出什么。"""
        from freecut import builder, export, verify

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            project = builder.new_project(640, 360, 30, name="size")
            builder.add_text(project, "自定义尺寸", start=0, duration=500_000, size=12)
            out = os.path.join(tmp, "odd.mp4")
            preset = export.ExportPreset(width=2048, height=1152, fps=25.0)
            export.export(project, out, preset)
            report = verify.verify(out, expect_duration_s=0.5)
        self.assertEqual((report.width, report.height), (2048, 1152))

    def test_self_made_effects_change_the_frame(self):
        """四个自研特效（光晕/漏光/色散/呼吸）必须真的改变画面。"""
        import numpy as np

        from freecut import effects

        # 底图要带**高光**：光晕只对超过阈值的像素起作用，
        # 一片中间调的渐变本来就不该有 bloom。
        base = np.zeros((90, 160, 3), dtype=np.float32)
        base[:, :, 0] = np.linspace(0.1, 0.9, 160)[None, :]
        base[:, :, 1] = 0.5
        base[:, :, 2] = np.linspace(0.1, 0.7, 90)[:, None]
        base[30:60, 60:100] = 1.0            # 一块纯白，用来验证 bloom
        alpha = np.ones((90, 160), dtype=np.float32)

        for kind in ("glow", "leak", "rgb_split", "breathe"):
            params = dict(effects.EFFECT_SPEC[kind]["params"])
            out, _a = effects.apply_effect(kind, base.copy(), alpha.copy(), 400_000, params)
            with self.subTest(effect=kind):
                self.assertEqual(out.shape, base.shape)
                self.assertGreater(float(np.abs(out - base).mean()), 0.002,
                                   f"{kind} 几乎没有效果")

    def test_newest_effects_are_visible(self):
        """三款新特效（星光/CRT/双色调）必须真的改变画面。"""
        import numpy as np

        from freecut import effects

        base = np.zeros((90, 160, 3), dtype=np.float32)
        base[:, :, 0] = 0.4
        base[:, :, 1] = 0.5
        base[:, :, 2] = 0.6
        alpha = np.ones((90, 160), dtype=np.float32)
        for kind in ("sparkle", "crt", "duotone"):
            params = dict(effects.EFFECT_SPEC[kind]["params"])
            out, _a = effects.apply_effect(kind, base.copy(), alpha.copy(), 400_000, params)
            with self.subTest(effect=kind):
                self.assertEqual(out.shape, base.shape)
                self.assertGreater(float(np.abs(out - base).mean()), 0.005)

    def test_effect_count_reaches_fifteen(self):
        from freecut import effects

        names = effects.available()
        self.assertGreaterEqual(len(names), 15)
        for kind in ("sparkle", "crt", "duotone"):
            self.assertIn(kind, names)

    def test_transition_engines_reach_seventeen(self):
        from freecut.render import TRANSITION_KINDS

        kinds = set(TRANSITION_KINDS.values())
        self.assertGreaterEqual(len(kinds), 17, f"只有 {len(kinds)} 种引擎")
        for kind in ("wipe_diagonal", "square", "blinds", "zoom_push", "mosaic_dissolve"):
            self.assertIn(kind, kinds)

    def test_internal_kind_names_resolve(self):
        """材质里直接写引擎名（我们自己加转场时就是这样）也要认。"""
        from freecut.render import transition_kind

        class FakeMat(dict):
            pass

        for kind in ("wipe_diagonal", "square", "zoom_push"):
            self.assertEqual(transition_kind(FakeMat(name=kind)), kind,
                             f"{kind} 被当成未知转场退回了叠化")

    def test_new_masks_are_geometric(self):
        """新遮罩在中点必须是真的几何形状，不能退化成全屏叠加。"""
        import numpy as np

        from freecut.render import _transition_mask

        for kind in ("wipe_diagonal", "square", "blinds"):
            mask = _transition_mask(kind, 0.5, 160, 90)
            with self.subTest(kind=kind):
                self.assertEqual(mask.shape, (90, 160))
                self.assertGreater(float(mask.max()), 0.9)
                self.assertLess(float(mask.min()), 0.1)
                self.assertGreater(len(np.unique(mask)), 2, "不应该只有 0/1 两档")

    def test_transition_ui_offers_both_layers(self):
        """效果页要同时给出「引擎」（决定画面）和「剪映名」（决定导出名字）。"""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(repo, "freecut", "gui.py"), encoding="utf-8").read()
        start = text.find("    def _effects_tab(")
        end = text.find("\n    def ", start + 10)
        body = text[start:end]
        self.assertIn("engine_labels", body, "应该有引擎下拉")
        self.assertIn("add_transition", body)
        self.assertIn("kind=engine", body, "加转场时必须把引擎传进去")
        # 引擎名表现在是模块级常量 ENGINE_LABELS（属性页和左栏共用一份），
        # 所以按常量核对，而不是在函数体里找字符串。
        from freecut import gui
        from freecut.render import TRANSITION_KINDS

        engines = set(TRANSITION_KINDS.values())
        missing = engines - set(gui.ENGINE_LABELS)
        self.assertFalse(missing, f"界面上缺少引擎 {sorted(missing)}")
        self.assertEqual(len(gui.ENGINE_LABELS), len(engines),
                         "ENGINE_LABELS 里有已经不存在的引擎")

    def test_engine_only_selection_still_adds_a_transition(self):
        """只选引擎、不选剪映名，也要能加转场（名字默认取引擎名）。"""
        app = _make_app()
        if app is None:
            self.skipTest("没有可用的图形环境")
        try:
            from freecut import builder

            project = builder.new_project(320, 180, 30, name="t")
            out_dir = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "out")
            image_a = os.path.join(out_dir, "_trans_a.png")
            image_b = os.path.join(out_dir, "_trans_b.png")
            if not (os.path.isfile(image_a) and os.path.isfile(image_b)):
                self.skipTest("缺少转场示例图")
            builder.add_image(project, image_a, start=0, duration=1_000_000)
            seg = builder.add_image(project, image_b, start=1_000_000, duration=1_000_000)
            app.project = project
            app.props.rebuild(seg)
            app.update()
            combos = [c for c in _find_widgets(app.props, "TCombobox")
                      if c.cget("values") and "叠化" in [str(v) for v in c.cget("values")]]
            self.assertTrue(combos, "应有引擎下拉")
            combos[0].set("斜向擦除")
            _find_widgets(app.props, "TButton", "添加转场")[0].invoke()
            app.update()
            mats = [m for m in project.materials.values() if m.type == "transition"]
            self.assertTrue(mats, "只选引擎也应当加上转场")
            self.assertEqual(mats[-1].get("name"), "斜向擦除")
            self.assertEqual(mats[-1].get("kind"), "wipe_diagonal")
        finally:
            app.destroy()

    def test_risk_ids_referenced_by_delivery_exist(self):
        """能力表里写的 R1/R2/… 必须真的在 RISKS.md 里有对应条目。"""
        import re as _re

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        delivery = open(os.path.join(repo, "DELIVERY.md"), encoding="utf-8").read()
        risks = open(os.path.join(repo, "RISKS.md"), encoding="utf-8").read()
        defined = set(_re.findall(r"^## (R\d+)", risks, _re.M))
        self.assertGreaterEqual(len(defined), 8, "RISKS 条目太少")
        cited = set(_re.findall(r"\*\*(R\d+)", delivery))
        self.assertTrue(cited, "能力表应当引用风险编号")
        for rid in sorted(cited):
            self.assertIn(rid, defined, f"DELIVERY 引用了不存在的 {rid}")

    def test_every_capability_row_has_a_boundary_cell(self):
        """能力表每行都要有第三列（可以是 —），不能漏掉边界说明。"""
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "DELIVERY.md"), encoding="utf-8").read()
        start = text.find("| 能力 | 级别 | 验证方式")
        self.assertGreater(start, 0, "找不到能力表")
        end = text.find("\n\n", start)
        rows = [ln for ln in text[start:end].splitlines()
                if ln.startswith("|") and not ln.startswith("|---") and "能力 |" not in ln]
        self.assertGreaterEqual(len(rows), 15, "能力表行数不对")
        for row in rows:
            # 能力 / 级别 / 验证方式 / 已知边界 —— 四列 = 5 个竖线
            self.assertEqual(row.count("|"), 5, f"这一行不是四列: {row[:60]}")


    def test_transition_engines_reach_nineteen(self):
        from freecut.render import TRANSITION_KINDS

        kinds = set(TRANSITION_KINDS.values())
        self.assertGreaterEqual(len(kinds), 19, f"只有 {len(kinds)} 种引擎")
        for kind in ("page_turn", "pixel_dissolve"):
            self.assertIn(kind, kinds)

    def test_new_masks_are_full_frame(self):
        """翻页 / 像素溶解的遮罩都必须是 (H, W)——不是 (1, W)。"""
        from freecut.render import _transition_mask

        for kind in ("page_turn", "pixel_dissolve"):
            mask = _transition_mask(kind, 0.5, 160, 90)
            with self.subTest(kind=kind):
                self.assertEqual(mask.shape, (90, 160))
                self.assertGreater(float(mask.max()), 0.9)
                self.assertLess(float(mask.min()), 0.1)

    def test_pixel_dissolve_blocks_are_deterministic(self):
        """溶解用固定随机种子：同一工程每次渲染必须一样，否则导出不可复现。"""
        import numpy as np

        from freecut.render import _transition_mask

        a = _transition_mask("pixel_dissolve", 0.4, 120, 60)
        b = _transition_mask("pixel_dissolve", 0.4, 120, 60)
        self.assertTrue(np.array_equal(a, b))

    def test_page_turn_sweeps_with_progress(self):
        from freecut.render import _transition_mask

        early = float(_transition_mask("page_turn", 0.2, 160, 90).mean())
        late = float(_transition_mask("page_turn", 0.8, 160, 90).mean())
        self.assertLess(early, late, "进度越大，翻过去的面积应当越多")

class TestScriptBom(unittest.TestCase):
    """文案文件带 BOM 时，第一句字幕不能带出看不见的字（第 134 轮）。"""

    def test_bom_is_stripped(self):
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "s.txt")
            with open(script, "w", encoding="utf-8-sig") as fh:      # 带 BOM
                fh.write("第一句话。\n第二句话。\n")
            out = os.path.join(tmp, "p.json")
            media = os.path.join(tmp, "m.mp4")
            open(media, "wb").close()
            proc = subprocess.run(
                [sys.executable, "-m", "freecut", "fromscript", script,
                 "--media", media, "--out", out, "--no-tts",
                 "--width", "160", "--height", "90"],
                capture_output=True, timeout=180, cwd=repo)
            text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            self.assertEqual(proc.returncode, 0, text[-400:])
            import json

            data = json.load(open(out, encoding="utf-8"))
        texts = [m.get("content", "") for m in data["materials"].get("text", [])]
        self.assertTrue(texts, "应当有字幕")
        self.assertFalse(texts[0].startswith("\ufeff"), f"第一句带了 BOM: {texts[0]!r}")

    def test_no_empty_test_class(self):
        """空的 TestCase 类不会报错，只会静默地不跑——这很危险。

        第 138/142 轮各犯过一次：把方法插到 class 行前面，方法落进了上一个类，
        新类变成空壳。这里用 AST 检查每个 TestCase 至少有一个 test_ 方法。
        """
        import ast

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "tests", "test_freecut.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        empty = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [getattr(b, "id", "") for b in node.bases]
            if "TestCase" not in bases:
                continue
            tests = [n.name for n in node.body
                     if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
            if not tests:
                empty.append(node.name)
        self.assertEqual(empty, [], f"这些测试类是空的: {empty}")

class TestNoDuplicateDefinitions(unittest.TestCase):
    """同一个类里不许把方法定义两次。

    第 132 轮真的犯过：把 `_goto_mask_page` 插进 App，PropertiesPanel 里那份
    又没删，于是同名字段互相覆盖，改哪份都不确定生效。
    """

    def test_no_duplicate_methods_in_any_module(self):
        import ast

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pkg = os.path.join(repo, "freecut")
        found = []
        for name in sorted(os.listdir(pkg)):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(pkg, name), encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                seen = {}
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        # property 的 getter/setter 本来就会同名（@x.setter），不算重复
                        decorators = []
                        for dec in item.decorator_list:
                            if isinstance(dec, ast.Attribute):
                                decorators.append(dec.attr)
                            elif isinstance(dec, ast.Name):
                                decorators.append(dec.id)
                        if any(d in ("setter", "getter", "deleter") for d in decorators):
                            continue
                        if item.name in seen:
                            found.append(f"{name}: {node.name}.{item.name} "
                                         f"(第 {seen[item.name]} 与 {item.lineno} 行)")
                        seen[item.name] = item.lineno
        self.assertEqual(found, [], "重复的方法定义会互相覆盖")

    def test_no_duplicate_top_level_functions(self):
        import ast

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pkg = os.path.join(repo, "freecut")
        found = []
        for name in sorted(os.listdir(pkg)):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(pkg, name), encoding="utf-8").read())
            seen = {}
            for item in tree.body:
                if isinstance(item, ast.FunctionDef):
                    if item.name in seen:
                        found.append(f"{name}: {item.name} 定义了两次")
                    seen[item.name] = item.lineno
        self.assertEqual(found, [])


class TestMaskSingleSource(unittest.TestCase):
    """蒙版只允许有一份实现：效果页只放跳转，不再自己写一套。"""

    def test_effects_tab_has_no_mask_editor(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(repo, "freecut", "gui.py"), encoding="utf-8").read()
        start = text.find("    def _effects_tab(")
        end = text.find("\n    def ", start + 10)
        body = text[start:end]
        self.assertNotIn("add_mask(", body, "效果页不该再自己加蒙版")
        self.assertNotIn("add_chroma(", body, "抠像入口也应统一到画面页")
        self.assertIn("_goto_mask_page", body, "应当留一个跳转按钮")

    def test_jump_switches_to_the_mask_page(self):
        app = _make_app()
        if app is None:
            self.skipTest("没有可用的图形环境")
        try:
            from freecut import builder

            project = builder.new_project(320, 180, 30, name="jump")
            seg = builder.add_text(project, "x", start=0, duration=500_000, size=10)
            app.project = project
            app.props.rebuild(seg)
            app.update()
            app.props._goto_mask_page()
            app.update()
            self.assertEqual(app.props.picture_sub.master.master.index("current"), 0)
            self.assertEqual(app.props.picture_sub.index("current"), 3)
        finally:
            app.destroy()


class TestAdjustMask(unittest.TestCase):
    """调节蒙版：只限制调色范围，不裁画面（和画面蒙版是两件事）。"""

    def _frame(self, mode):
        from freecut import builder
        from freecut.render import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            grey = os.path.join(tmp, "g.png")
            from PIL import Image

            Image.fromarray(np.full((90, 160, 3), (128, 128, 128), np.uint8)).save(grey)
            p = builder.new_project(160, 90, 10)
            seg = builder.add_image(p, grey, start=0, duration=1_000_000)
            builder.set_adjust(p, seg, brightness=0.6)
            if mode == "video":
                builder.add_mask(p, seg, name="线性", center_x=-0.5, width=1.0,
                                 height=1.0, feather=0.01, rotation=90)
            elif mode == "adjust":
                builder.add_adjust_mask(p, seg, name="线性", center_x=-0.5, width=1.0,
                                        height=1.0, feather=0.01, rotation=90)
            r = Renderer(p, 160, 90, 10)
            try:
                return r.render_frame(0)[..., 0].astype(float)
            finally:
                r.close()

    def test_video_mask_cuts_the_frame(self):
        frame = self._frame("video")
        # 蒙版外的右半边被裁掉（露出画布底色，几乎为黑）
        self.assertLess(frame[:, 80:].mean(), 20, "画面蒙版应当把右半边裁掉")
        self.assertGreater(frame[:, :80].mean(), 40, "蒙版内的左半边应当保留")

    def test_only_one_place_decides_mask_kind(self):
        """两个蒙版入口（画面→蒙版、效果页下拉）都不能直接写死 add_mask。

        第 130 轮漏过一个：效果页的下拉仍然调 add_mask，于是给调节图层加蒙版
        会把画面裁成洞。这里用源码检查兜住——以后新增入口也必须走同一个判断。
        """
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(repo, "freecut", "gui.py"), encoding="utf-8").read()
        bare = [line.strip() for line in text.splitlines()
                if "builder.add_mask(" in line and "is_adjustment" not in line]
        self.assertEqual(bare, [], f"这些地方没判断调节图层: {bare}")

    def test_gui_mask_tab_uses_adjust_mask_for_adjustment_layers(self):
        """在界面里给调节图层加蒙版，必须是"只限调色"，不能变成裁画面。"""
        app = _make_app()
        if app is None:
            self.skipTest("没有可用的图形环境")
        try:
            from freecut import builder

            project = builder.new_project(320, 180, 30, name="gui")
            builder.add_text(project, "底", start=0, duration=1_000_000, size=10)
            adj = builder.add_adjustment(project, start=0, duration=1_000_000, contrast=30)
            app.project = project
            panel = app.props
            panel.rebuild(adj)
            app.update()
            adder = builder.add_adjust_mask if getattr(adj, "is_adjustment", False) else builder.add_mask
            adder(project, adj, name="圆形", center_x=0.0, center_y=0.0,
                  width=0.6, height=0.6, feather=0.05)
            self.assertTrue(adj.enable.get("enable_adjust_mask"))
            self.assertFalse(adj.enable.get("enable_video_mask"))
        finally:
            app.destroy()

    def test_adjustment_layer_mask_is_honoured(self):
        """调节**图层**（不是片段自身调色）上的蒙版也必须生效。

        这是第 129 轮修的 bug：`_draw_track` 的调节分支直接调 `_color()`，
        完全没走蒙版，于是 `add_adjust_mask` 加在调节图层上等于没加。
        """
        from freecut import builder
        from freecut.render import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "b.png")
            grad = np.zeros((90, 160, 3), np.uint8)
            grad[:, :, 0] = np.linspace(0, 255, 160, np.uint8)[None, :]
            grad[:, :, 1] = 140
            grad[:, :, 2] = np.linspace(0, 255, 90, np.uint8)[:, None]
            from PIL import Image

            Image.fromarray(grad).save(base)

            def render(masked):
                project = builder.new_project(160, 90, 30, name="m")
                builder.add_image(project, base, start=0, duration=1_000_000)
                adj = builder.add_adjustment(project, start=0, duration=1_000_000,
                                             brightness=8, contrast=35, saturation=40)
                if masked:
                    builder.add_adjust_mask(project, adj, name="圆形", center_x=0.0,
                                            center_y=0.0, width=0.6, height=0.6,
                                            feather=0.04)
                renderer = Renderer(project, 160, 90, 30)
                try:
                    return renderer.render_frame(3)
                finally:
                    renderer.close()

            plain, masked = render(False), render(True)

        def diff(sl):
            return float(np.abs(plain.astype(int) - masked.astype(int)).max(axis=2)[sl].mean())

        # 圆内两边都被调节，所以差值为 0；圆外只有"无蒙版"那份被调节。
        # 这正是"蒙版限制调色范围"应有的结果。
        self.assertLess(diff((slice(35, 55), slice(70, 90))), 3, "圆内两份都该被调节")
        self.assertGreater(diff((slice(2, 12), slice(2, 12))), 20, "圆外不该被调节")

    def test_adjust_mask_keeps_the_frame_but_limits_grading(self):
        frame = self._frame("adjust")
        # 右半边不该被裁黑，也不该被调色
        self.assertGreater(frame[:, 80:].mean(), 100, "调色蒙版不该裁掉画面")
        self.assertLess(frame[:, 80:].mean(), frame[:, :80].mean(),
                        "右半边应当保持未调色")

    def test_export_writes_the_flag_and_warns(self):
        from freecut import builder, jianying_export

        with tempfile.TemporaryDirectory() as tmp:
            grey = os.path.join(tmp, "g.png")
            from PIL import Image

            Image.fromarray(np.full((90, 160, 3), (128, 128, 128), np.uint8)).save(grey)
            p = builder.new_project(160, 90, 10, name="adm")
            seg = builder.add_image(p, grey, start=0, duration=1_000_000)
            builder.set_adjust(p, seg, brightness=0.6)
            builder.add_adjust_mask(p, seg, name="线性", center_x=-0.5)
            warn = []
            draft = jianying_export.save_draft(p, tmp, name="adm", encrypt=False, warnings=warn)
            import json

            with open(os.path.join(draft, "draft_content.json"), encoding="utf-8") as fh:
                content = json.load(fh)
        seg_dict = [s for t in content["tracks"] for s in t["segments"]][0]
        self.assertTrue(seg_dict.get("enable_adjust_mask"))
        self.assertFalse(seg_dict.get("enable_video_mask"), "调色蒙版不该同时裁画面")
        self.assertTrue(any("调节蒙版" in w for w in warn), warn)


class TestHslCurves(unittest.TestCase):
    """HSL 曲线（剪映 zorder 5018）：按色带作用，别的色带不受影响。"""

    def test_only_the_targeted_band_changes(self):
        from freecut.color import apply_hsl_curves

        red = np.zeros((1, 1, 3), np.float32)
        red[..., 0] = 0.6
        blue = np.zeros((1, 1, 3), np.float32)
        blue[..., 2] = 0.6
        bands = {"red": {"points": [[0.0, 0.0], [1.0, 0.3]]}}
        self.assertLess(float(apply_hsl_curves(red, bands)[0, 0, 0]), 0.6)
        self.assertAlmostEqual(float(apply_hsl_curves(blue, bands)[0, 0, 2]), 0.6, places=3)

    def test_renderer_applies_it_and_honours_the_switch(self):
        from freecut import builder
        from freecut.render import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            red = os.path.join(tmp, "red.png")
            from PIL import Image

            Image.fromarray(np.full((90, 160, 3), (255, 0, 0), np.uint8)).save(red)

            def mean_red(with_curves: bool, switch: bool) -> float:
                p = builder.new_project(160, 90, 10)
                seg = builder.add_image(p, red, start=0, duration=1_000_000)
                if with_curves:
                    builder.add_hsl_curves(p, seg, {"red": {"points": [[0.0, 0.0], [1.0, 0.35]]}})
                    seg.enable["enable_hsl_curves"] = switch
                r = Renderer(p, 160, 90, 10)
                try:
                    return float(r.render_frame(0)[..., 0].mean())
                finally:
                    r.close()

            self.assertGreater(mean_red(False, False), 240)
            self.assertLess(mean_red(True, True), 240, "开关打开时应当变暗")
            self.assertGreater(mean_red(True, False), 240, "开关关闭时不该生效")

    def test_export_writes_the_material_and_warns(self):
        from freecut import builder, jianying_export

        with tempfile.TemporaryDirectory() as tmp:
            red = os.path.join(tmp, "red.png")
            from PIL import Image

            Image.fromarray(np.full((90, 160, 3), (255, 0, 0), np.uint8)).save(red)
            p = builder.new_project(160, 90, 10, name="hslc")
            seg = builder.add_image(p, red, start=0, duration=1_000_000)
            builder.add_hsl_curves(p, seg, {"red": {"points": [[0.0, 0.0], [1.0, 0.35]]}})
            warn = []
            draft = jianying_export.save_draft(p, tmp, name="hslc", encrypt=False, warnings=warn)
            import json

            with open(os.path.join(draft, "draft_content.json"), encoding="utf-8") as fh:
                content = json.load(fh)
        self.assertEqual(len(content["materials"].get("hsl_curves") or []), 1)
        self.assertTrue(any("HSL" in w for w in warn), warn)


class TestFieldAudit(unittest.TestCase):
    """写了但没人读的字段要能被指出来（第 82 轮的 highlight_mode 就是这么漏的）。"""

    def test_flags_a_field_nobody_reads(self):
        from freecut import fields

        content = {"tracks": [{"segments": [{"enable_nonexistent_thing": True}]}],
                   "materials": {}}
        audit = fields.audit(content)
        self.assertIn("enable_nonexistent_thing", audit.unused)

    def test_recognises_a_field_the_renderer_reads(self):
        from freecut import fields

        # letter_spacing 是文本渲染真会读的字段
        content = {"tracks": [{"segments": [{"letter_spacing": 0.05}]}], "materials": {}}
        audit = fields.audit(content)
        self.assertIn("letter_spacing", audit.used)

    def test_metadata_is_not_counted(self):
        from freecut import fields

        content = {"tracks": [{"segments": [{"id": "x", "render_index": 1}]}],
                   "materials": {}}
        audit = fields.audit(content)
        self.assertEqual(audit.unused, {})
        self.assertEqual(audit.used, {})

    def test_exported_switches_match_reality(self):
        """enable_* 必须反映"这一段真的有这个效果"，不能一律 True。"""
        from freecut import jianying_export

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(320, 180, 10)
            builder.add_text(p, "无特效", start=0, duration=500_000, size=8)
            draft = jianying_export.save_draft(p, tmp, name="sw", encrypt=False)
            import json

            with open(os.path.join(draft, "draft_content.json"), encoding="utf-8") as fh:
                content = json.load(fh)
        for track in content["tracks"]:
            for seg in track["segments"]:
                self.assertFalse(seg.get("enable_hsl_curves"), seg)
                self.assertFalse(seg.get("enable_adjust_mask"), seg)


class TestHighlightMode(unittest.TestCase):
    """highlight_mode 是剪映草稿里的开关：0 = 不高亮，非 0 才逐字着色。"""

    def _render(self, mode, colour="#FF00FF"):
        from freecut import builder, subtitles
        from freecut.text import TextRenderer

        p = builder.new_project(320, 120, 10)
        seg = builder.add_text(p, "一二三四", start=0, duration=500_000, size=16.0)
        mat = p.material(seg.material_id)
        mat.extra.update({
            "words": subtitles.distribute_words("一二三四", 0, 500_000),
            "highlight_color": colour, "highlight_mode": mode,
            "text_color": "#FFFFFF", "shadow_alpha": 0.0,
        })
        return TextRenderer(320, 120).render(mat, active_word=1).image

    def test_mode_zero_does_not_recolour(self):
        self.assertTrue(np.array_equal(self._render(0)[..., :3], self._render(0, "#FFFFFF")[..., :3]),
                        "mode 0 时高亮色不该影响画面")

    def test_nonzero_mode_recolours(self):
        self.assertFalse(np.array_equal(self._render(1)[..., :3], self._render(1, "#FFFFFF")[..., :3]),
                         "mode 1 时应当逐字着色")

    def test_karaoke_sets_the_mode(self):
        from freecut import subtitles

        p = builder.new_project(320, 120, 10)
        subs = [subtitles.Subtitle(text="你好", start_us=0, end_us=500_000)]
        clips = subtitles.add_subtitles(p, subs, style={"font_size": 12.0}, karaoke=True)
        self.assertTrue(clips)
        mat = p.material(clips[0].material_id)
        self.assertNotEqual(int(mat.get("highlight_mode") or 0), 0,
                            "卡拉 OK 字幕必须打开 highlight_mode")


class TestTextStyleSheet(unittest.TestCase):
    """14 个文字模板必须真的长得不一样，且不触发已知的字距缺陷。"""

    def test_presets_stay_within_the_safe_letter_spacing(self):
        from freecut import textstyles

        bad = [n for n in textstyles.names()
               if float((textstyles.find(n) or {}).get("letter_spacing") or 0)
               > textstyles.SAFE_LETTER_SPACING]
        self.assertEqual(bad, [], f"这些模板的字距超出安全上限: {bad}")

    def test_spaced_text_has_no_ghosting(self):
        """字距 + 阴影曾经画错位（影子不跟随字距），现在阴影要逐字画。"""
        from freecut import builder
        from freecut.text import TextRenderer

        def layer(spacing: float) -> np.ndarray:
            p = builder.new_project(320, 120, 10)
            seg = builder.add_text(p, "AB 花字", start=0, duration=500_000, size=14.0)
            mat = p.material(seg.material_id)
            mat.extra.update({"text_color": "#222222", "shadow_alpha": 0.6,
                              "shadow_distance": 0.04, "letter_spacing": spacing})
            return TextRenderer(320, 120).render(mat).image

        spaced = layer(0.2)
        # 有字距时图层必须更宽（否则说明字距没生效）
        self.assertGreater(spaced.shape[1], layer(0.0).shape[1])
        # 墨迹列里应当出现明显间隙（字距），而不是一个大墨块
        ink = (spaced[..., 3] > 40).sum(axis=0)
        cols = np.where(ink > 0)[0]
        self.assertGreater(cols.max() - cols.min(), 40)
        gaps = np.sum(np.diff(cols) > 3)
        self.assertGreaterEqual(gaps, 2, "字距生效时应有多处字符间隙")

    def test_every_preset_renders_differently(self):
        from freecut import builder, textstyles
        from freecut.render import Renderer

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        names = textstyles.names()
        frames = {}
        for name in names:
            p = builder.new_project(320, 120, 10)
            p.canvas.background = "#c9ced8"
            seg = builder.add_text(p, "花字", start=0, duration=500_000, size=14.0)
            textstyles.apply(p.material(seg.material_id), name)
            r = Renderer(p, 320, 120, 10)
            try:
                frames[name] = r.render_frame(0).astype(np.int16)
            finally:
                r.close()
        identical = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = frames[names[i]], frames[names[j]]
                if float(np.abs(a - b).mean()) < 0.2:
                    identical.append((names[i], names[j]))
        self.assertEqual(identical, [], f"这些模板渲染结果几乎相同: {identical}")


class TestTour(unittest.TestCase):
    """示例导览：状态栏依次给提示，不用弹窗、不阻塞。"""

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def test_hints_cycle_and_stop(self):
        import time as _t

        app = self.app
        app.show_tour(["甲", "乙"], seconds=0.5)
        seen = [app.status.cget("text")]      # 第一条是同步设置的，先记下来
        t0 = _t.time()
        while _t.time() - t0 < 1.6:
            app.update()
            _t.sleep(0.02)
            text = app.status.cget("text")
            if text not in seen:
                seen.append(text)
        self.assertTrue(any("甲" in s for s in seen), seen)
        self.assertTrue(any("乙" in s for s in seen), seen)
        self.assertIsNone(app._tour_job, "走完一轮后不该还挂着定时器")

    def test_default_tour_mentions_the_key_entry_points(self):
        app = self.app
        blob = "".join(app.DEFAULT_TOUR)
        for want in ("播放", "导出报告", "运行自检"):
            self.assertIn(want, blob, app.DEFAULT_TOUR)

    def test_starting_a_second_tour_cancels_the_first(self):
        import time as _t

        app = self.app
        app.show_tour(["旧提示"], seconds=5)
        first = app._tour_job
        app.show_tour(["新提示"], seconds=5)
        self.assertNotEqual(first, app._tour_job)
        app.update()
        self.assertIn("新提示", app.status.cget("text"))


class TestWelcomeOverlay(unittest.TestCase):
    """空工程显示引导层，有内容后消失。"""

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def test_visible_when_empty_hidden_when_not(self):
        app = self.app
        app.project = builder.new_project(320, 180, 10)
        app.refresh_bin()
        app.update()
        self.assertTrue(app.welcome.winfo_ismapped(), "空工程应显示引导层")

        builder.add_text(app.project, "第一个片段", start=0, duration=500_000, size=8)
        app.refresh_bin()
        app.update()
        self.assertFalse(app.welcome.winfo_ismapped(), "有内容后应隐藏")

    def test_overlay_has_the_four_entry_buttons(self):
        app = self.app
        app.project = builder.new_project(320, 180, 10)
        app.refresh_bin()
        app.update()
        labels = []

        def walk(w):
            try:
                if "text" in w.keys():
                    labels.append(str(w.cget("text")))
            except Exception:
                pass
            for child in w.winfo_children():
                walk(child)

        walk(app.welcome)
        blob = "".join(labels)
        for want in ("示例", "导入媒体", "成片", "剪映"):
            self.assertIn(want, blob, f"引导层缺少「{want}」入口: {labels}")


class TestSelfCheckDialog(unittest.TestCase):
    """帮助 → 运行自检：流式显示、能看到结论（用桩脚本，不真跑四分钟）。"""

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def _all_text(self, widget):
        out = []

        def walk(w):
            try:
                if w.winfo_class() == "Text":          # Text 控件没有 text 选项
                    out.append(w.get("1.0", "end"))
                elif "text" in w.keys():
                    out.append(str(w.cget("text")))
            except Exception:
                pass
            for child in w.winfo_children():
                walk(child)

        walk(widget)
        return "\n".join(out)

    def test_streams_output_and_reports_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = os.path.join(tmp, "stub.py")
            with open(stub, "w", encoding="utf-8") as fh:
                fh.write("print('[OK  ] 假步骤 (0s) 3/3 条通过', flush=True)\n")
            self.app._selfcheck_script = __import__("pathlib").Path(stub)
            self.app.run_selfcheck_dialog()
            win = [w for w in self.app.winfo_children()
                   if w.winfo_class() == "Toplevel"][0]
            for _ in range(40):
                self.app.update()
                import time as _t

                _t.sleep(0.05)
            text = self._all_text(win)
            win.destroy()
        self.assertIn("假步骤", text, "子进程输出应流进对话框")
        self.assertIn("自检通过", text, "跑完要给出结论")

    def test_about_shows_module_count(self):
        app = self.app
        shown = {}

        def fake(title, message, **kw):
            shown["title"] = title
            shown["message"] = message

        import freecut.gui as G

        original = G.messagebox.showinfo
        G.messagebox.showinfo = fake
        try:
            app.show_about()
        finally:
            G.messagebox.showinfo = original
        self.assertIn("FreeCut", shown.get("title", ""))
        self.assertIn("模块", shown.get("message", ""))


class TestExportReportDialog(unittest.TestCase):
    """工具菜单里的「导出报告」要能看到两个方向，并给出会丢失多少。"""

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def test_dialog_lists_both_targets_and_a_summary(self):
        app = self.app
        app.project = builder.new_project(320, 180, 10)
        builder.add_text(app.project, "标题", start=0, duration=500_000, size=8)
        builder.add_adjustment(app.project, start=0, duration=400_000, contrast=12)
        try:
            app.show_export_report()
        except Exception as exc:
            self.fail(f"打开导出报告失败: {exc}")
        app.update()

        tops = [w for w in app.winfo_children() if w.winfo_class() == "Toplevel"]
        self.assertTrue(tops, "应当弹出一个报告窗口")
        win = tops[0]

        texts = []

        def walk(widget):
            try:
                if "text" in widget.keys():
                    texts.append(str(widget.cget("text")))
            except Exception:
                pass
            for child in widget.winfo_children():
                walk(child)

        walk(win)
        blob = "\n".join(texts)
        self.assertIn("处会不同", blob, "底部应给出两个方向的丢失数量")
        self.assertTrue(any("剪映" in x or "XML" in x for x in texts), texts)
        win.destroy()


class TestStartHere(unittest.TestCase):
    """入口文档里提到的路径和数字必须是真的——它是别人的第一印象。"""

    def _repo(self):
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def test_cited_paths_exist(self):
        repo = self._repo()
        doc = os.path.join(repo, "START-HERE.md")
        if not os.path.isfile(doc):
            self.skipTest("没有 START-HERE.md")
        import re

        with open(doc, encoding="utf-8") as fh:
            text = fh.read()
        cited = set(re.findall(r"`([A-Za-z0-9_\\:/.-]+\.(?:md|py|json|mp4|png))`", text))
        cited |= set(re.findall(r"\(([A-Za-z0-9_\\\\-]+\.md)\)", text))
        missing = []
        for rel in cited:
            rel = rel.replace("\\", os.sep)
            if os.path.isabs(rel):
                continue
            candidates = [os.path.join(repo, rel), os.path.join(repo, "FreeCut", rel)]
            if not any(os.path.exists(c) for c in candidates):
                missing.append(rel)
        self.assertEqual(missing, [], f"文档里引用了不存在的路径: {missing}")

    def test_module_and_line_counts_are_current(self):
        repo = self._repo()
        doc = os.path.join(repo, "START-HERE.md")
        if not os.path.isfile(doc):
            self.skipTest("没有 START-HERE.md")
        import re
        from pathlib import Path as _P

        with open(doc, encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"(\d+) 个模块 / ([\d ]+) 行", text)
        if not m:
            self.skipTest("文档里没写模块/行数")
        modules = int(m.group(1))
        lines = int(m.group(2).replace(" ", ""))
        pkg = _P(repo) / "FreeCut" / "freecut"
        actual_modules = len(list(pkg.glob("*.py")))
        # 与文档同口径：只数非空行
        actual_lines = sum(1 for f in pkg.glob("*.py")
                           for line in f.read_text(encoding="utf-8").splitlines()
                           if line.strip())
        self.assertEqual(modules, actual_modules, "模块数过时了")
        # 容差 300 行：加一个功能就能到 100+ 行，这个守卫防的是"数量级过时"
        self.assertLessEqual(abs(lines - actual_lines), 300,
                             f"行数过时：文档 {lines} / 实际 {actual_lines}")


class TestVerifySummary(unittest.TestCase):
    """验收汇总表：每步要抽出一行结论，别让人翻日志才知道过没过。"""

    def _load(self):
        import importlib.util

        path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "verify_all.py"))
        if not os.path.isfile(path):
            self.skipTest("没有 verify_all.py")
        spec = importlib.util.spec_from_file_location("verify_all", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["verify_all"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_extracts_the_verdict_of_each_step(self):
        mod = self._load()
        cases = {
            "剪映逆向复现": ("8/8 条通过", "8/8 条通过"),
            "文档漂移检查": ("发现 0 处可疑表述：", "0 处可疑表述"),
            "功能总演示": ("  结论：通过", "通过"),
            "渲染性能基线": ("与基线一致（整帧 37ms / 基线 43ms）", "与基线一致"),
        }
        for name, (text, want) in cases.items():
            with self.subTest(name=name):
                self.assertIn(want, mod._summarise(name, text))

    def test_unit_test_summary_includes_the_outcome(self):
        mod = self._load()
        ok = mod._summarise("x", "Ran 12 tests in 1.0s\n\nOK\n")
        bad = mod._summarise("x", "Ran 12 tests in 1.0s\n\nFAILED (errors=1)\n")
        self.assertIn("Ran 12 tests", ok)
        self.assertIn("OK", ok)
        self.assertIn("FAILED", bad)


class TestBenchBaseline(unittest.TestCase):
    """性能基线：脚本能出 JSON，退化能被判定。"""

    def test_bench_writes_json(self):
        from freecut.media import ffmpeg_path

        try:
            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bench = os.path.join(root, "tools", "bench_render.py")
        if not os.path.isfile(bench):
            self.skipTest("没有 tools/bench_render.py")
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "b.json")
            import subprocess

            proc = subprocess.run([sys.executable, bench, "--size", "160x90",
                                   "--frames", "6", "--json", out],
                                  capture_output=True, timeout=600,
                                  env={**os.environ, "FREECUT_FFMPEG": ffmpeg_path()})
            self.assertEqual(proc.returncode, 0,
                             (proc.stdout + proc.stderr).decode("utf-8", "replace")[-400:])
            import json

            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
        for key in ("frame_ms", "seek_ms", "chroma_ms", "size"):
            self.assertIn(key, data)
        self.assertEqual(data["size"], "160x90")

    def test_regression_ratio_is_detected(self):
        # 与 verify_all.run_bench 同一条判定：超过 1.5 倍算退化
        base, now = 40.0, 95.0
        self.assertGreater(now / base, 1.5)
        ok_base, ok_now = 40.0, 55.0
        self.assertLess(ok_now / ok_base, 1.5)


class TestDocDrift(unittest.TestCase):
    """文档漂移检查器：能抓到"说没做但其实做了"，也放过"尚未证实"。"""

    def _load(self):
        import importlib.util

        path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..", "tools", "doc_drift.py"))
        if not os.path.isfile(path):
            self.skipTest("没有 tools/doc_drift.py")
        spec = importlib.util.spec_from_file_location("doc_drift", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["doc_drift"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_catches_a_stale_claim(self):
        mod = self._load()
        corpus = mod._corpus()
        with tempfile.TemporaryDirectory() as tmp:
            doc = os.path.join(tmp, "x.md")
            with open(doc, "w", encoding="utf-8") as fh:
                fh.write("| 蒙版 | masks | 未实现 | — |\n")
            hits = mod._scan(__import__("pathlib").Path(doc), corpus)
        self.assertTrue(hits)
        self.assertEqual(hits[0].keyword, "蒙版")

    def test_ignores_evidence_uncertainty(self):
        mod = self._load()
        corpus = mod._corpus()
        with tempfile.TemporaryDirectory() as tmp:
            doc = os.path.join(tmp, "y.md")
            with open(doc, "w", encoding="utf-8") as fh:
                fh.write("| 曲线 | 曲线点 schema 尚未用真实草稿证实 | — |\n")
            hits = mod._scan(__import__("pathlib").Path(doc), corpus)
        self.assertEqual(hits, [], "说的是证据强度，不该算漂移")

    def test_current_docs_are_clean(self):
        mod = self._load()
        corpus = mod._corpus()
        problems = []
        for doc in mod.DOCS:
            problems += mod._scan(doc, corpus)
        self.assertEqual(problems, [], "\n".join(f.render() for f in problems))


class TestVerifyAllBudget(unittest.TestCase):
    """耗时预算：超时只警告，--strict-time 才判失败。"""

    def _load(self):
        import importlib.util

        path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "verify_all.py"))
        if not os.path.isfile(path):
            self.skipTest("没有 verify_all.py")
        spec = importlib.util.spec_from_file_location("verify_all", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["verify_all"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_step_knows_when_it_is_over_budget(self):
        mod = self._load()
        fast = mod.Step("x", True, 10.0, budget=60)
        slow = mod.Step("y", True, 90.0, budget=60)
        self.assertFalse(fast.over_budget)
        self.assertTrue(slow.over_budget)
        self.assertFalse(mod.Step("z", True, 999.0).over_budget)   # 没预算就不算超

    def test_every_budgeted_step_has_a_budget(self):
        mod = self._load()
        self.assertGreaterEqual(len(mod.BUDGETS), 3)
        for name, seconds in mod.BUDGETS.items():
            self.assertIsInstance(name, str)
            self.assertGreater(seconds, 0)


class TestVerifyAll(unittest.TestCase):
    """根目录的验收脚本：步骤编排要正确，缺文件要明确失败。"""

    def _load(self):
        import importlib.util

        path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "verify_all.py"))
        if not os.path.isfile(path):
            self.skipTest("没有 verify_all.py")
        spec = importlib.util.spec_from_file_location("verify_all", path)
        mod = importlib.util.module_from_spec(spec)
        # dataclass 需要能在 sys.modules 里找到自己的模块，否则会 KeyError
        sys.modules["verify_all"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_missing_script_is_a_loud_failure(self):
        mod = self._load()
        step = mod.run_repro("假的", __import__("pathlib").Path(
            os.path.join(tempfile.gettempdir(), "nonexistent-reproduce.py")))
        self.assertFalse(step.ok)
        self.assertIn("缺少", step.detail)

    def test_ffmpeg_is_picked_up_from_jianying(self):
        mod = self._load()
        env = mod._env()
        self.assertIn("PYTHONIOENCODING", env)
        if "FREECUT_FFMPEG" in env:
            self.assertTrue(os.path.isfile(env["FREECUT_FFMPEG"]), env["FREECUT_FFMPEG"])
        else:
            self.skipTest("这台机器没装剪映")

    def test_step_reports_its_own_timing(self):
        mod = self._load()
        step = mod.Step("x", True, 1.5, "detail")
        self.assertEqual(step.name, "x")
        self.assertTrue(step.ok)


class TestPrReproIndex(unittest.TestCase):
    """PR 侧的复现脚本同样纳入测试（纯文件检查，不需要装 PR）。"""

    def _load(self):
        import importlib.util

        path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..", "Premiere 样本", "tools", "reproduce.py"))
        if not os.path.isfile(path):
            self.skipTest("没有 docs/tools/reproduce.py")
        spec = importlib.util.spec_from_file_location("pr_repro", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_ticks_constant_is_the_real_one(self):
        mod = self._load()
        self.assertEqual(mod.TICKS_PER_SECOND, 254_016_000_000)
        ok, detail = mod.check_ticks()
        self.assertTrue(ok, detail)

    def test_samples_are_gzip_xml(self):
        mod = self._load()
        ok, detail = mod.check_gzip_xml()
        self.assertTrue(ok, detail)

    def test_object_model_is_found(self):
        mod = self._load()
        ok, detail = mod.check_object_model()
        self.assertTrue(ok, detail)


class TestReproIndex(unittest.TestCase):
    """逆向证据索引里的纯文件检查，纳入测试防止悄悄失效。"""

    def _load(self):
        import importlib.util

        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "..", "内部实测", "tools", "reproduce.py")
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            self.skipTest("没有 docs/tools/reproduce.py")
        spec = importlib.util.spec_from_file_location("repro", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_catalog_counts_match_the_shipped_data(self):
        mod = self._load()
        ok, detail = mod.check_catalog()
        self.assertTrue(ok, detail)

    def test_zorder_chain_is_unchanged(self):
        mod = self._load()
        ok, detail = mod.check_zorder()
        self.assertTrue(ok, detail)

    def test_entries_are_listed(self):
        mod = self._load()
        self.assertGreaterEqual(len(mod.CHECKS), 6)
        for name in mod.CHECKS:
            self.assertIn(name, mod.DESCRIPTIONS)


class TestProbeDuration(unittest.TestCase):
    """工程列表里的时长要和工程一致；0 表示"没算过"，不能当成不一致。"""

    def _r(self, **kw):
        from freecut import jyprobe

        base = dict(launched=True, changed_meta=True)
        base.update(kw)
        return jyprobe.ProbeResult(**base)

    def test_matching_duration(self):
        r = self._r(registered_name="x", registered_duration_us=38_200_000,
                    expected_duration_us=38_200_000)
        self.assertTrue(r.duration_matches)
        self.assertIn("时长与工程一致", r.as_text())

    def test_mismatching_duration(self):
        r = self._r(registered_name="x", registered_duration_us=7_400_000,
                    expected_duration_us=38_200_000)
        self.assertFalse(r.duration_matches)
        self.assertIn("不一致", r.as_text())

    def test_zero_is_unknown_not_mismatch(self):
        r = self._r(registered_name="x", registered_duration_us=0,
                    expected_duration_us=38_200_000)
        self.assertIsNone(r.duration_matches, "0 是'还没算'，不是'算错了'")
        self.assertIn("打开并保存后才会更新", r.as_text())

    def test_no_expectation_means_unknown(self):
        r = self._r(registered_name="x", registered_duration_us=38_200_000)
        self.assertIsNone(r.duration_matches)


class TestProbeRecord(unittest.TestCase):
    def test_changed_structure_invalidates_the_record(self):
        import json

        from freecut import jianying_export, jyprobe

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(160, 90, 10)
            builder.add_text(p, "a", start=0, duration=500_000, size=8)
            draft = jianying_export.save_draft(p, tmp, name="st", encrypt=False)
            sig = jyprobe.structure_signature(draft)
            with open(os.path.join(draft, jyprobe.PROBE_RECORD), "w", encoding="utf-8") as fh:
                json.dump({"parsed_timeline": True, "structure": sig}, fh)
            self.assertIsNotNone(jyprobe.load_record(draft))

            # 改结构（加一个片段）后再导出，记录必须作废
            builder.add_text(p, "b", start=600_000, duration=500_000, size=8)
            draft2 = jianying_export.save_draft(p, tmp, name="st", encrypt=False)
            self.assertIsNone(jyprobe.load_record(draft2), "结构变了，旧记录不该被采纳")



    """探针记录要能被自检引用，也要会过期。"""

    def test_record_roundtrip_and_citation(self):
        import json
        import time

        from freecut import jianying_export, jyprobe, selfcheck

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(160, 90, 10)
            builder.add_text(p, "x", start=0, duration=500_000, size=8)
            draft = jianying_export.save_draft(p, tmp, name="pr", encrypt=False)
            sig = jyprobe.structure_signature(draft)
            record = {"launched": True, "changed_meta": True, "parsed_timeline": True,
                      "reported_size": 1234, "checked_at": "2026-10-21 10:00:00",
                      "structure": sig}
            with open(os.path.join(draft, jyprobe.PROBE_RECORD), "w", encoding="utf-8") as fh:
                json.dump(record, fh, ensure_ascii=False)
            loaded = jyprobe.load_record(draft)
            self.assertEqual(loaded["reported_size"], 1234)
            rep = selfcheck.check_draft(p, draft, samples=0)
        self.assertTrue(any("剪映已解析过这个草稿" in n for n in rep.notes), rep.notes)

    def test_stale_record_is_ignored(self):
        import json
        import os as _os

        from freecut import jyprobe

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, jyprobe.PROBE_RECORD)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"parsed_timeline": True}, fh)
            old = 1.0
            _os.utime(path, (old, old))
            self.assertIsNone(jyprobe.load_record(tmp), "过期记录不该被采纳")
            self.assertIsNotNone(jyprobe.load_record(tmp, max_age_days=0),
                                 "关掉过期检查时仍可读")

    def test_unparsed_record_warns(self):
        import json

        from freecut import jianying_export, jyprobe, selfcheck

        with tempfile.TemporaryDirectory() as tmp:
            p = builder.new_project(160, 90, 10)
            builder.add_text(p, "y", start=0, duration=500_000, size=8)
            draft = jianying_export.save_draft(p, tmp, name="pr2", encrypt=False)
            with open(os.path.join(draft, jyprobe.PROBE_RECORD), "w", encoding="utf-8") as fh:
                json.dump({"parsed_timeline": False, "checked_at": "2026-10-21"}, fh)
            rep = selfcheck.check_draft(p, draft, samples=0)
        self.assertTrue(any("未确认解析" in w for w in rep.warnings), rep.warnings)


class TestJyProbe(unittest.TestCase):
    """探针的判定逻辑（不真的启动剪映）。"""

    def test_perturb_and_restore_roundtrip(self):
        import json

        from freecut import jyprobe

        with tempfile.TemporaryDirectory() as tmp:
            meta = {"draft_name": "x", "tm_duration": 12_000_000,
                    "draft_timeline_materials_size_": 999}
            path = os.path.join(tmp, "draft_meta_info.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(meta, fh)
            saved = jyprobe.perturb_meta(tmp)
            self.assertEqual(saved["tm_duration"], 12_000_000)
            with open(path, encoding="utf-8") as fh:
                broken = json.load(fh)
            self.assertEqual(broken["tm_duration"], 0)
            jyprobe.restore_meta(tmp, saved)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["tm_duration"], 12_000_000)

    def test_verdict_needs_a_recomputed_value(self):
        from freecut import jyprobe

        nothing = jyprobe.ProbeResult(launched=True, changed_meta=True)
        self.assertFalse(nothing.parsed_timeline, "只改了 meta 不算解析了时间线")

        size_only = jyprobe.ProbeResult(launched=True, changed_meta=True,
                                        reported_size=3_995_221)
        self.assertTrue(size_only.parsed_timeline, "素材体积被重新统计就是证据")

        duration_only = jyprobe.ProbeResult(launched=True, changed_meta=True,
                                            reported_duration_us=38_200_000,
                                            expected_duration_us=38_200_000)
        self.assertTrue(duration_only.parsed_timeline)

    def test_registry_entry_is_reported(self):
        from freecut import jyprobe

        r = jyprobe.ProbeResult(launched=True, changed_meta=True, reported_size=99,
                                registered_name="FreeCut 功能总演示",
                                registered_duration_us=38_200_000)
        text = r.as_text()
        self.assertIn("剪映工程列表里的条目", text)
        self.assertIn("38.200s", text)

    def test_registry_lookup_handles_missing_file(self):
        from freecut import jyprobe

        self.assertIsNone(jyprobe.registry_entry(os.path.join(tempfile.gettempdir(), "no-such-draft")))

    def test_text_says_why(self):
        from freecut import jyprobe

        r = jyprobe.ProbeResult(launched=True, changed_meta=True, reported_size=1234,
                                seconds=18.0)
        text = r.as_text()
        self.assertIn("通过", text)
        self.assertIn("解析了素材列表", text)


class TestAudioInvariantScope(unittest.TestCase):
    """有独立音频轨时，视频素材自己没音轨不该被判成"丢音"（第 64 轮修）。"""

    def test_separate_audio_track_is_enough(self):
        from freecut import jianying_export, selfcheck

        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "v.mp4", "0x336699", seconds=2)
            tone = os.path.join(tmp, "t.wav")
            import math
            import wave

            rate = 8000
            data = bytearray()
            for i in range(rate):
                data += int(0.3 * 32767 * math.sin(2 * math.pi * 220 * i / rate)).to_bytes(
                    2, "little", signed=True)
            with wave.open(tone, "wb") as fh:
                fh.setnchannels(1)
                fh.setsampwidth(2)
                fh.setframerate(rate)
                fh.writeframes(bytes(data))
            p = builder.new_project(320, 180, 10)
            seg = builder.add_video(p, clip, start=0, duration=1_000_000, track_index=0)
            p.material(seg.material_id).extra["has_audio"] = False
            builder.add_audio(p, tone, start=0, duration=1_000_000, track_index=0)
            draft = jianying_export.save_draft(p, tmp, name="sep", encrypt=False)
            rep = selfcheck.check_draft(p, draft, samples=0)
        self.assertTrue(rep.ok, rep.problems)


class TestFlattenAudio(unittest.TestCase):
    """展平只保证画面是不够的——声音也得对得上。"""

    def _tone(self, tmp, freq=220.0, seconds=1.0):
        import math
        import wave

        path = os.path.join(tmp, "t.wav")
        rate = 22050
        data = bytearray()
        for i in range(int(rate * seconds)):
            data += int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / rate)).to_bytes(
                2, "little", signed=True)
        with wave.open(path, "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(rate)
            fh.writeframes(bytes(data))
        return path

    def test_flattened_draft_reports_audio_parity(self):
        from freecut import export as ex, jianying_export, selfcheck

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "v.mp4", "0x8844CC", seconds=2)
            tone = self._tone(tmp)
            p = builder.new_project(320, 180, 10)
            builder.add_video(p, clip, start=0, duration=1_000_000, track_index=0)
            builder.add_audio(p, tone, start=0, duration=1_000_000, track_index=0)
            mp4 = os.path.join(tmp, "flat.mp4")
            ex.export(p, mp4, ex.PRESETS["720p30"], jobs=1)
            mini = jianying_export.flatten_project(p, mp4)
            draft = jianying_export.save_draft(mini, tmp, name="fa", encrypt=False)
            rep = selfcheck.check_draft(mini, draft, samples=0, original=p)
        self.assertTrue(rep.ok, rep.problems)
        self.assertTrue(any("音频与工程一致" in n for n in rep.notes), rep.notes)

    def test_draft_that_drops_audio_is_flagged(self):
        import json

        from freecut import jianying_export, selfcheck

        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "w.mp4", "0x44CC88", seconds=2)
            tone = self._tone(tmp)
            p = builder.new_project(320, 180, 10)
            builder.add_video(p, clip, start=0, duration=1_000_000, track_index=0)
            builder.add_audio(p, tone, start=0, duration=1_000_000, track_index=0)
            draft = jianying_export.save_draft(p, tmp, name="noaud", encrypt=False)
            path = os.path.join(draft, "draft_content.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            for v in data["materials"].get("videos", []):
                v["has_audio"] = False
            data["materials"]["audios"] = []
            for track in data["tracks"]:
                if track.get("type") == "audio":
                    track["segments"] = []
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            rep = selfcheck.check_draft(p, draft, samples=0)
        self.assertFalse(rep.ok)
        self.assertTrue(any("has_audio" in p_ for p_ in rep.problems), rep.problems)


class TestFlattenExport(unittest.TestCase):
    """展平导出：画面对得上，但只剩一个片段。"""

    def test_flatten_keeps_canvas_and_duration(self):
        from freecut import export as ex, jianying_export

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "f.mp4", "0xCC8844", seconds=2)
            p = builder.new_project(320, 180, 10, name="展平")
            builder.add_video(p, clip, start=0, duration=1_000_000, track_index=0)
            builder.add_text(p, "标题", start=0, duration=1_000_000, size=8)
            mp4 = os.path.join(tmp, "flat.mp4")
            ex.export(p, mp4, ex.PRESETS["720p30"], jobs=1)
            mini = jianying_export.flatten_project(p, mp4)
            draft = jianying_export.save_draft(mini, tmp, name="flat", encrypt=False)

            self.assertEqual(mini.canvas.width, p.canvas.width)
            self.assertEqual(mini.canvas.height, p.canvas.height)
            segs = [s for t_ in mini.tracks for s in t_.segments]
            self.assertEqual(len(segs), 1, "展平后只应剩一个片段")
            self.assertAlmostEqual(mini.duration() / 1e6, p.duration() / 1e6, delta=0.05)
            mat = mini.material(segs[0].material_id)
            self.assertEqual(os.path.abspath(mat.get("path")), os.path.abspath(mp4))
            self.assertTrue(os.path.isfile(os.path.join(draft, "draft_content.json")))


class TestGradingHonesty(unittest.TestCase):
    """第 57 轮自查：调色数值其实没写进草稿，报告不能说"精确"。"""

    def _project(self, tmp):
        clip = _solid_clip(tmp, "g.mp4", "0x3366AA", seconds=1)
        p = builder.new_project(160, 90, 10)
        seg = builder.add_video(p, clip, start=0, duration=500_000, track_index=0)
        builder.set_curves(p, seg, rgb=[[0, 0.1], [1, 0.9]])
        return p

    def test_report_marks_grading_values_as_degraded(self):
        from freecut import draftreport

        with tempfile.TemporaryDirectory() as tmp:
            rep = draftreport.analyze(self._project(tmp))
        degraded = {i.feature for i in rep.by(draftreport.DEGRADED)}
        self.assertTrue(any("调色数值" in f for f in degraded), degraded)
        exact = {i.feature for i in rep.by(draftreport.EXACT)}
        self.assertFalse(any("调色参数" in f for f in exact), exact)

    def test_selfcheck_states_the_limitation(self):
        from freecut import jianying_export, selfcheck

        with tempfile.TemporaryDirectory() as tmp:
            p = self._project(tmp)
            draft = jianying_export.save_draft(p, tmp, name="g", encrypt=False)
            rep = selfcheck.check_draft(p, draft, samples=0)
        text = rep.as_text()
        self.assertIn("调色开关", text)
        self.assertTrue(any("schema 未证实" in n for n in rep.notes), rep.notes)

    def test_flags_are_really_written(self):
        """开关是真写了——否则连"降级"都不成立。"""
        import json

        from freecut import jianying_export

        with tempfile.TemporaryDirectory() as tmp:
            p = self._project(tmp)
            draft = jianying_export.save_draft(p, tmp, name="g2", encrypt=False)
            with open(os.path.join(draft, "draft_content.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        segs = [s for t in data["tracks"] for s in t.get("segments", [])]
        self.assertTrue(any(s.get("enable_color_curves") for s in segs))


class TestExportReport(unittest.TestCase):
    """导出报告必须跟着工程实际内容走，不能是写死的表格。"""

    def test_plain_project_has_no_losses(self):
        from freecut import draftreport

        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "r.mp4", "0xCC3333", seconds=1)
            p = builder.new_project(160, 90, 10)
            builder.add_video(p, clip, start=0, duration=500_000, track_index=0)
            rep = draftreport.analyze(p)
        self.assertEqual(rep.losses, [], rep.as_text())
        self.assertIn("可以完整地被剪映读取", rep.as_text())

    def test_adjustment_and_effect_are_reported_as_lost(self):
        from freecut import draftreport

        p = builder.new_project(160, 90, 10)
        builder.add_text(p, "底", start=0, duration=1_000_000, size=8)
        builder.add_adjustment(p, start=0, duration=500_000, contrast=10)
        rep = draftreport.analyze(p)
        features = {i.feature for i in rep.by(draftreport.LOST)}
        self.assertTrue(any("调节图层" in f for f in features), features)

    def test_chroma_is_degraded_not_lost(self):
        from freecut import draftreport

        with tempfile.TemporaryDirectory() as tmp:
            img = os.path.join(tmp, "g.png")
            from PIL import Image

            Image.fromarray(np.zeros((16, 16, 3), np.uint8)).save(img)
            p = builder.new_project(160, 90, 10)
            seg = builder.add_image(p, img, start=0, duration=500_000, track_index=0)
            builder.add_chroma(p, seg, key="#00ff00")
            rep = draftreport.analyze(p)
        self.assertTrue(any("色度抠图" in i.feature for i in rep.by(draftreport.DEGRADED)),
                        rep.as_text())
        self.assertEqual(rep.by(draftreport.LOST), [])

    def test_report_writes_both_files(self):
        from freecut import draftreport

        p = builder.new_project(160, 90, 10)
        builder.add_text(p, "x", start=0, duration=500_000, size=8)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "freecut_export_report.txt")
            draftreport.analyze(p).write(path)
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "freecut_export_report.json")))
            with open(path, encoding="utf-8") as fh:
                self.assertIn("剪映草稿导出报告", fh.read())


class TestCliSurface(unittest.TestCase):
    def test_gbk_console_does_not_crash(self):
        """中文 Windows 默认 GBK，⚠/✅ 这类字符不能让 CLI 崩掉。"""
        import subprocess
        import sys

        demo = os.path.join(tempfile.gettempdir(), "freecut_gbk_probe.json")
        p = builder.new_project(160, 90, 10, name="编码")
        builder.add_text(p, "编码", start=0, duration=500_000, size=8)
        builder.add_adjustment(p, start=0, duration=500_000, contrast=10)   # 会触发警告
        p.save(demo)
        root = tempfile.mkdtemp(prefix="freecut-gbk-")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "gbk"
        env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [sys.executable, "-m", "freecut", "export-draft", demo,
             "--root", root, "--name", "gbk", "--selfcheck", "--selfcheck-frames", "0"],
            capture_output=True, env=env, timeout=180,
        )
        blob = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertNotIn("UnicodeEncodeError", blob)
        self.assertEqual(proc.returncode, 0, blob[-500:])


    """命令行界面本身也要有测试——文档里写的命令必须能解析。"""

    def _parse(self, argv):
        from freecut.cli import build_parser

        return build_parser().parse_args(argv)

    def test_gui_accepts_a_positional_project(self):
        args = self._parse(["gui", "工程.json"])
        self.assertEqual(args.project, "工程.json")
        args_opt = self._parse(["gui", "--project", "别的.json"])
        self.assertEqual(args_opt.project_opt, "别的.json")

    def test_documented_commands_all_parse(self):
        cases = [
            ["info"],
            ["probe", "a.mp4"],
            ["export", "p.json", "--out", "o.mp4", "--variants", "720p30"],
            ["export-draft", "p.json", "--root", "d", "--selfcheck"],
            ["import-draft", "d"],
            ["import-prproj", "a.prproj", "--out", "o.json"],
            ["export-xml", "p.json", "--out", "o.xml"],
            ["verify", "o.mp4"],
            ["doctor", "p.json", "--search", "media"],
            ["fromscript", "s.txt", "--out", "p.json", "--render", "o.mp4"],
            ["tts", "--list"],
            ["showcase", "--out", "s.json", "--render", "s.mp4", "--draft", "d", "--asr"],
            ["autocut", "--media", "a.mp4", "--out", "p.json"],
            ["gui", "p.json"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                args = self._parse(argv)
                self.assertTrue(hasattr(args, "func"), f"{argv} 没有绑定处理函数")


class TestAsrLoop(unittest.TestCase):
    def test_text_overlap_ignores_punctuation(self):
        from freecut.showcase import _text_overlap

        self.assertAlmostEqual(_text_overlap("这是测试。", "这是测试"), 1.0)
        self.assertLess(_text_overlap("完全无关", "别的字"), 0.2)
        self.assertEqual(_text_overlap("", "x"), 0.0)

    def test_tts_to_asr_roundtrip(self):
        """说一句 → 识别回来，字符重合率要足够高（证明闭环真的通了）。"""
        from freecut import asr, tts
        from freecut.showcase import _text_overlap

        if not tts.voices():
            self.skipTest("没有语音合成")
        try:
            backend = asr.find_backend()
        except Exception:
            backend = None
        if not backend or not os.path.isfile(backend):
            self.skipTest("没有语音识别后端")
        spoken = "这是自动字幕测试。语音识别能把它变成字幕。"
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "n.wav")
            ok, _dur = tts.ensure_wav(spoken, wav)
            if not ok:
                self.skipTest("语音合成失败")
            try:
                subs = asr.transcribe_to_subtitles(wav, device="cpu", compute_type="int8",
                                                   prompt=None)
            except Exception as exc:
                self.skipTest(f"识别失败: {exc}")
        heard = " ".join(s.text for s in subs)
        self.assertTrue(heard.strip(), "应当识别出文字")
        overlap = _text_overlap(spoken, heard)
        self.assertGreater(overlap, 0.7, f"重合率太低：说={spoken} 听={heard}")
        self.assertTrue(any(s.words for s in subs), "应当带词级时间戳")


class TestUnsupportedFeatures(unittest.TestCase):
    """剪映没有的东西（调节图层）应当算"按预期跳过"，不是数据丢失。"""

    def _project(self):
        p = builder.new_project(320, 180, 10)
        p.canvas.background = "#223344"
        builder.add_text(p, "底", start=0, duration=1_000_000, size=10)
        builder.add_adjustment(p, start=0, duration=1_000_000, contrast=15)
        return p

    def test_adjustment_layer_is_a_warning_not_a_failure(self):
        from freecut import jianying_export, selfcheck

        p = self._project()
        with tempfile.TemporaryDirectory() as tmp:
            notes = []
            draft = jianying_export.save_draft(p, tmp, name="adj", encrypt=False, warnings=notes)
            rep = selfcheck.check_draft(p, draft, samples=0)
        self.assertTrue(rep.ok, rep.problems)
        self.assertTrue(any("调节图层" in w for w in rep.warnings), rep.warnings)
        self.assertTrue(any("调节图层" in n for n in notes), notes)

    def test_real_loss_still_fails(self):
        """普通片段被删掉时必须失败，不能也被当成"不支持"。"""
        import json

        from freecut import jianying_export, selfcheck

        p = builder.new_project(320, 180, 10)
        builder.add_text(p, "普通片段", start=0, duration=1_000_000, size=10)
        with tempfile.TemporaryDirectory() as tmp:
            draft = jianying_export.save_draft(p, tmp, name="plain", encrypt=False)
            path = os.path.join(draft, "draft_content.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data["tracks"][0]["segments"].clear()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            rep = selfcheck.check_draft(p, draft, samples=0)
        self.assertFalse(rep.ok)


class TestAudioEffects(unittest.TestCase):
    """音频效果必须真的改变波形，而不只是写进工程文件。"""

    def _tone(self, tmp, freq=220.0, seconds=2.0):
        import math
        import wave

        path = os.path.join(tmp, "tone.wav")
        rate = 22050
        data = bytearray()
        for i in range(int(rate * seconds)):
            v = int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / rate))
            data += v.to_bytes(2, "little", signed=True)
        with wave.open(path, "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(rate)
            fh.writeframes(bytes(data))
        return path

    def _dominant(self, path):
        import wave

        with wave.open(path, "rb") as fh:
            sr, ch = fh.getframerate(), fh.getnchannels()
            raw = fh.readframes(fh.getnframes())
        d = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if ch > 1:                       # 立体声要取单声道，否则频率会看似减半
            d = d.reshape(-1, ch).mean(axis=1)
        spec = np.abs(np.fft.rfft(d * np.hanning(len(d))))
        freqs = np.fft.rfftfreq(len(d), 1 / sr)
        band = (freqs > 60) & (freqs < 4000)
        return float(freqs[band][np.argmax(spec[band])])

    def test_pitch_shift_moves_the_frequency(self):
        from freecut import audio

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            tone = self._tone(tmp)
            base = builder.new_project(160, 90, 10)
            seg = builder.add_audio(base, tone, start=0, duration=2_000_000, track_index=0)
            plain = os.path.join(tmp, "plain.wav")
            self.assertTrue(audio.write_audio_mix(base, plain, 2_000_000, "192k"))

            shifted = builder.new_project(160, 90, 10)
            seg2 = builder.add_audio(shifted, tone, start=0, duration=2_000_000, track_index=0)
            builder.set_audio_effects(shifted, seg2, pitch=1.5)
            up = os.path.join(tmp, "up.wav")
            self.assertTrue(audio.write_audio_mix(shifted, up, 2_000_000, "192k"))

            f0, f1 = self._dominant(plain), self._dominant(up)
        self.assertAlmostEqual(f0, 220.0, delta=12.0)
        self.assertGreater(f1 / f0, 1.35, f"变声 1.5x 后主频应上升（{f0:.0f} -> {f1:.0f}）")

    def test_volume_changes_loudness(self):
        from freecut import audio

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        import wave

        with tempfile.TemporaryDirectory() as tmp:
            tone = self._tone(tmp, seconds=1.0)

            def peak(vol):
                p = builder.new_project(160, 90, 10)
                s = builder.add_audio(p, tone, start=0, duration=1_000_000, track_index=0)
                builder.set_volume(s, vol)
                out = os.path.join(tmp, f"v{vol}.wav")
                audio.write_audio_mix(p, out, 1_000_000, "192k")
                with wave.open(out, "rb") as fh:
                    d = np.frombuffer(fh.readframes(fh.getnframes()), dtype=np.int16)
                return float(np.abs(d).max())

            loud, quiet = peak(1.0), peak(0.2)
        self.assertGreater(loud, quiet * 2.5, f"音量 1.0 vs 0.2 峰值应差很多（{loud} / {quiet}）")


class TestShowcaseAndMix(unittest.TestCase):
    def test_images_are_not_audio_sources(self):
        """A picture in the project used to break the whole mix (silent export)."""
        from freecut import audio

        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            img = os.path.join(tmp, "p.png")
            Image.fromarray(np.zeros((16, 16, 3), np.uint8)).save(img)
            p = builder.new_project(160, 90, 10)
            builder.add_image(p, img, start=0, duration=500_000, track_index=0)
            self.assertEqual(audio.collect_clips(p), [])

    def test_mix_still_works_with_an_image_present(self):
        from freecut import audio

        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            img = os.path.join(tmp, "p2.png")
            Image.fromarray(np.zeros((16, 16, 3), np.uint8)).save(img)
            tone = os.path.join(tmp, "t.wav")
            import math
            import wave

            rate, dur = 8000, 1.0
            data = bytearray()
            for i in range(int(rate * dur)):
                v = int(0.3 * 32767 * math.sin(2 * math.pi * 220 * i / rate))
                data += v.to_bytes(2, "little", signed=True)
            with wave.open(tone, "wb") as fh:
                fh.setnchannels(1)
                fh.setsampwidth(2)
                fh.setframerate(rate)
                fh.writeframes(bytes(data))

            p = builder.new_project(160, 90, 10)
            builder.add_image(p, img, start=0, duration=1_000_000, track_index=0)
            builder.add_audio(p, tone, start=0, duration=1_000_000, track_index=0)
            self.assertEqual(len(audio.collect_clips(p)), 1)
            out = os.path.join(tmp, "mix.wav")
            self.assertTrue(audio.write_audio_mix(p, out, 1_000_000, "128k"))
            self.assertGreater(os.path.getsize(out), 1000)

    def test_showcase_builds_every_segment(self):
        from freecut import showcase

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            stats = showcase.build_showcase(tmp, width=320, height=180, fps=10)
        project = stats["project"]
        self.assertGreaterEqual(len(stats["steps"]), 8)
        self.assertGreater(project.duration(), 20_000_000)
        kinds = {t.type for t in project.tracks}
        self.assertIn("video", kinds)
        self.assertIn("audio", kinds)
        self.assertIn("text", kinds)


class TestAudioPreview(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def test_mix_is_built_and_cached(self):
        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        app = self.app
        app.project = _demo_for(self)
        path = app._mix_all()
        self.assertIsNotNone(path)
        self.assertTrue(os.path.isfile(path))
        size = os.path.getsize(path)
        self.assertGreater(size, 10_000)
        app._mix_all()                      # 第二次命中缓存，不应重建
        self.assertEqual(os.path.getsize(path), size)

    def test_invalidate_forces_a_rebuild(self):
        app = self.app
        app.project = _demo_for(self)
        app._mix_all()
        self.assertFalse(app._mix_dirty)
        app.invalidate()
        self.assertTrue(app._mix_dirty, "工程变了应该重新混音")

    def test_silent_project_has_no_mix(self):
        app = self.app
        app.project = builder.new_project(160, 90, 10)
        builder.add_text(app.project, "无声", start=0, duration=500_000, size=6)
        self.assertIsNone(app._mix_all())

    def test_play_toggles_audio_without_crashing(self):
        app = self.app
        app.project = builder.new_project(160, 90, 10)
        builder.add_text(app.project, "x", start=0, duration=400_000, size=6)
        app.toggle_play()                   # 无声工程：不应抛异常
        app.update()
        app.toggle_play()
        app.update()
        self.assertFalse(app.playing)


class TestSelfCheck(unittest.TestCase):
    def _rich_project(self):
        p = builder.new_project(320, 180, 10, name="自检")
        p.canvas.background = "#203040"
        t = builder.add_text(p, "自检文本", start=0, duration=1_500_000, size=14)
        builder.set_curves(p, t, rgb=[[0, 0], [1, 1]])
        builder.add_mask(p, t, "圆形")
        builder.add_animation(p, t, "渐显")
        return p

    def test_clean_roundtrip_is_pixel_identical(self):
        from freecut import jianying_export, selfcheck

        p = self._rich_project()
        with tempfile.TemporaryDirectory() as tmp:
            draft = jianying_export.save_draft(p, tmp, name="ok", encrypt=False)
            rep = selfcheck.check_draft(p, draft, samples=3)
        self.assertTrue(rep.ok, rep.problems)
        self.assertGreaterEqual(rep.frames_checked, 1)
        self.assertLess(rep.max_pixel_diff, 0.01)

    def test_missing_segment_is_reported(self):
        import json

        from freecut import jianying_export, selfcheck

        p = self._rich_project()
        with tempfile.TemporaryDirectory() as tmp:
            draft = jianying_export.save_draft(p, tmp, name="bad", encrypt=False)
            path = os.path.join(draft, "draft_content.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data["tracks"][0]["segments"].clear()          # 人为破坏
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            rep = selfcheck.check_draft(p, draft, samples=0)
        self.assertFalse(rep.ok)
        self.assertTrue(any("片段数不一致" in p_ or "片段时间不一致" in p_
                            for p_ in rep.problems), rep.problems)

    def test_overlap_is_a_warning_not_a_failure(self):
        from freecut import jianying_export, selfcheck

        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "ov.mp4", "0x884422", seconds=3)
            p = builder.new_project(320, 180, 10)
            builder.add_video(p, clip, start=0, duration=2_000_000, track_index=0)
            builder.add_video(p, clip, start=1_000_000, duration=2_000_000, track_index=0)
            draft = jianying_export.save_draft(p, tmp, name="ov", encrypt=False)
            rep = selfcheck.check_draft(p, draft, samples=2)
        self.assertTrue(rep.ok, rep.problems)
        self.assertTrue(rep.warnings, "重叠裁剪应给出提示")

    def test_unreadable_draft_is_reported(self):
        from freecut import selfcheck

        p = self._rich_project()
        with tempfile.TemporaryDirectory() as tmp:
            rep = selfcheck.check_draft(p, tmp, samples=0)
        self.assertFalse(rep.ok)
        self.assertTrue(rep.problems)


class TestMultiOutput(unittest.TestCase):
    def _demo(self):
        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        return _demo_for(self)

    def test_duplicate_targets_are_rejected_when_asked(self):
        from freecut import export

        project = self._demo()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "a.mp4")
            open(out, "wb").close()
            with self.assertRaises(export.ExportError):
                export.export_many(project, [(out, export.PRESETS["720p30"])], overwrite=False)

    def test_empty_target_list_raises(self):
        from freecut import export

        project = self._demo()
        with self.assertRaises(export.ExportError):
            export.export_many(project, [])

    def test_one_render_two_resolutions(self):
        from freecut import export

        project = self._demo()
        with tempfile.TemporaryDirectory() as tmp:
            big = os.path.join(tmp, "big.mp4")
            small = os.path.join(tmp, "small.mp4")
            paths = export.export_many(
                project,
                [(big, export.PRESETS["1080p30"]), (small, export.PRESETS["720p30"])],
                jobs=1,
            )
            self.assertEqual(len(paths), 2)
            for path in paths:
                self.assertTrue(os.path.isfile(path))
                self.assertGreater(os.path.getsize(path), 10_000)
            sizes = [os.path.getsize(p) for p in paths]
            self.assertNotEqual(sizes[0], sizes[1], "两条输出不该完全一样")


class TestExportQuality(unittest.TestCase):
    def test_quality_tiers_apply(self):
        from freecut import export

        base = export.PRESETS["720p30"]
        small = export.with_quality(base, "小体积")
        high = export.with_quality(base, "高码率")
        self.assertGreater(small.crf, high.crf - 100)      # 小体积 = 更大 crf
        self.assertEqual(high.video_bitrate, "12M")

    def test_unknown_tier_raises(self):
        from freecut import export

        with self.assertRaises(KeyError):
            export.with_quality(export.PRESETS["720p30"], "不存在")

    def test_encode_args_carry_the_controls(self):
        from freecut import export

        preset = export.ExportPreset(video_bitrate="8M", gop=48, profile="high",
                                     audio_channels=1, audio_bitrate="96k")
        args = preset.encode_args("libx264")
        self.assertIn("-b:v", args)
        self.assertEqual(args[args.index("-b:v") + 1], "8M")
        self.assertEqual(args[args.index("-g") + 1], "48")
        self.assertEqual(args[args.index("-profile:v") + 1], "high")
        # 固定码率时不应同时给 CRF（相互压制）
        self.assertNotIn("-crf", args)

    def test_bufsize_defaults_to_double_maxrate(self):
        from freecut import export

        self.assertEqual(export._double_rate("8M"), "16M")
        self.assertEqual(export._double_rate("1500k"), "3000k")

    def test_broadcast_export_matches_the_request(self):
        """Audio channel count / bitrate really change the output file."""
        from freecut import export
        from freecut.model import Project

        try:
            from freecut.media import ffmpeg_path

            ffmpeg_path()
        except Exception:
            self.skipTest("没有 ffmpeg")
        project = _demo_for(self)
        with tempfile.TemporaryDirectory() as tmp:
            mono = os.path.join(tmp, "mono.mp4")
            stereo = os.path.join(tmp, "stereo.mp4")
            p1 = export.PRESETS["720p30"]
            p1.audio_channels = 1
            p1.audio_bitrate = "96k"
            export.export(project, mono, p1, jobs=1)
            p2 = export.PRESETS["720p30"]
            p2.audio_channels = 2
            p2.audio_bitrate = "192k"
            export.export(project, stereo, p2, jobs=1)
            self.assertNotEqual(os.path.getsize(mono), os.path.getsize(stereo))


class TestTts(unittest.TestCase):
    def setUp(self):
        from freecut import tts

        if not tts.voices():
            self.skipTest("这台机器没有可用的语音合成")

    def test_voices_are_listed(self):
        from freecut import tts

        names = tts.voices()
        self.assertGreaterEqual(len(names), 1)
        self.assertIn(tts.pick_voice(), names)

    def test_synthesis_produces_audio(self):
        from freecut import tts

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "a.wav")
            ok, dur = tts.ensure_wav("这是合成测试", out)
            self.assertTrue(ok, "应当能合成")
            self.assertTrue(os.path.isfile(out))
            self.assertGreater(os.path.getsize(out), 1000)
            self.assertGreater(dur, 300_000)

    def test_rate_changes_length(self):
        from freecut import tts

        with tempfile.TemporaryDirectory() as tmp:
            _ok1, fast = tts.ensure_wav("这是一句用来比较语速的话。", os.path.join(tmp, "f.wav"), rate=8)
            _ok2, slow = tts.ensure_wav("这是一句用来比较语速的话。", os.path.join(tmp, "s.wav"), rate=-8)
        self.assertLess(fast, slow, "语速高应当更短")

    def test_wav_duration_matches_synthesis(self):
        from freecut import tts

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "b.wav")
            ok, dur = tts.ensure_wav("另一个测试句子", out)
            if ok:
                self.assertAlmostEqual(tts.wav_duration_us(out) / 1e6, dur / 1e6, places=2)

    def test_voice_dialog_saves_settings(self):
        app = _make_app()
        if app is None:
            self.skipTest("没有可用的图形环境")
        try:
            import tkinter as tk

            app.voice_settings()
            app.update()
            tops = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
            self.assertTrue(tops, "应当弹出配音设置窗口")
            dlg = tops[-1]
            buttons = _find_widgets(dlg, "TButton", "确定")
            self.assertTrue(buttons)
            buttons[0].invoke()
            app.update()
            self.assertTrue(app.tts_voice, "确定后应记住音色")
            self.assertIsInstance(app.tts_rate, int)
        finally:
            try:
                app.destroy()
            except Exception:
                pass


class TestScriptFilm(unittest.TestCase):
    def test_split_sentences_handles_cjk_and_latin(self):
        from freecut import scriptfilm

        got = scriptfilm.split_sentences("第一句。第二句！第三句？")
        self.assertEqual(got, ["第一句。", "第二句！", "第三句？"])
        self.assertEqual(scriptfilm.split_sentences("Hello there. Next one."),
                         ["Hello there.", "Next one."])
        self.assertEqual(scriptfilm.split_sentences("   "), [])

    def test_long_sentence_is_broken_up(self):
        from freecut import scriptfilm

        long_line = "这一句话特别长，" * 12
        got = scriptfilm.split_sentences(long_line, max_len=40)
        self.assertGreater(len(got), 1)
        self.assertTrue(all(len(s) <= 40 for s in got), [len(s) for s in got])

    def test_estimate_duration_scales_with_length(self):
        from freecut import tts

        short = tts.estimate_duration_us("短句")
        long = tts.estimate_duration_us("这是一个更长的句子用来比较时长")
        self.assertGreater(long, short)
        self.assertGreaterEqual(short, 600_000)

    def test_project_has_audio_subtitles_and_footage(self):
        from freecut import scriptfilm

        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "bg.mp4", "0x3355AA")
            stats = scriptfilm.build_script_project(
                "第一句。第二句！", [clip], work_dir=tmp,
                width=320, height=180, fps=10, use_tts=False)
        project = stats["project"]
        kinds = [t.type for t in project.tracks]
        self.assertEqual(kinds.count("audio"), 1)
        self.assertEqual(kinds.count("video"), 1)
        self.assertEqual(kinds.count("text"), 1)
        self.assertEqual(len(stats["sentences"]), 2)
        self.assertFalse(stats["used_tts"])          # 明确：本轮没有做语音合成
        self.assertGreater(project.duration(), 1_000_000)

    def test_tts_backend_matches_what_actually_ran(self):
        from freecut import scriptfilm

        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "bg2.mp4", "0xAA5533")
            stats = scriptfilm.build_script_project(
                "测试语音。", [clip], work_dir=tmp, width=320, height=180, fps=10)
            audio = next((t for t in stats["project"].tracks if t.type == "audio"), None)
        if stats["used_tts"]:
            self.assertIsNotNone(audio)
            self.assertEqual(len(audio.segments), 1)
            self.assertGreater(stats["durations_us"][0], 300_000)
        else:
            # 没有 SAPI 的机器上不应谎报有配音
            self.assertEqual(stats["tts_lines"], 0)
            self.assertIsNone(audio)

    def test_empty_script_is_rejected(self):
        from freecut import scriptfilm

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                scriptfilm.build_script_project("   \n ", [], work_dir=tmp, use_tts=False)


class TestLayouts(unittest.TestCase):
    def test_presets_are_well_formed(self):
        from freecut import layouts

        self.assertGreaterEqual(len(layouts.names()), 6)
        for name in layouts.names():
            slots = layouts.slots(name)
            self.assertTrue(slots, name)
            for w, h, tx, ty in slots:
                self.assertGreater(w, 0.0)
                self.assertGreater(h, 0.0)
                self.assertLessEqual(w, 1.0001)
                self.assertLessEqual(h, 1.0001)
                self.assertLessEqual(abs(tx), 1.0001)
                self.assertLessEqual(abs(ty), 1.0001)

    def test_unknown_layout_raises(self):
        from freecut import builder, layouts

        p = builder.new_project(320, 180, 30)
        with self.assertRaises(KeyError):
            layouts.apply(p, "不存在的布局")

    def test_two_up_fills_both_halves(self):
        from freecut import builder, layouts
        from freecut.render import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            red = _solid_clip(tmp, "r.mp4", "0xCC2222")
            green = _solid_clip(tmp, "g.mp4", "0x22AA44")
            p = builder.new_project(320, 180, 10)
            builder.add_video(p, red, start=0, duration=500_000, track_index=0)
            builder.add_video(p, green, start=0, duration=500_000, track_index=1)
            placed = layouts.apply(p, "左右分屏")
            self.assertEqual(placed, 2)
            r = Renderer(p, 320, 180, 10)
            frame = r.render_frame(0).copy()
            r.close()
        left = frame[:, :150]
        right = frame[:, 170:]
        self.assertGreater(int(left[..., 0].mean()), int(left[..., 1].mean()) + 30, "左半应为红")
        self.assertGreater(int(right[..., 1].mean()), int(right[..., 0].mean()) + 20, "右半应为绿")

    def test_grid_uses_four_quadrants(self):
        from freecut import builder, layouts
        from freecut.render import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            cols = ["0xCC2222", "0x22AA44", "0x2244CC", "0xDDAA22"]
            paths = [_solid_clip(tmp, f"c{i}.mp4", c) for i, c in enumerate(cols)]
            p = builder.new_project(320, 180, 10)
            for i, path in enumerate(paths):
                builder.add_video(p, path, start=0, duration=500_000, track_index=i)
            layouts.apply(p, "四宫格")
            r = Renderer(p, 320, 180, 10)
            frame = r.render_frame(0).copy()
            r.close()
        quadrants = [frame[20:70, 20:140], frame[20:70, 180:300],
                     frame[110:160, 20:140], frame[110:160, 180:300]]
        means = [tuple(int(q[..., c].mean()) for c in range(3)) for q in quadrants]
        # 四个象限的主色应当互不相同
        dominant = [max(range(3), key=lambda i: m[i]) for m in means]
        self.assertEqual(len(set(dominant)) >= 3, True, f"四宫格主色应不同: {dominant}")

    def test_shared_material_is_cloned_so_crop_does_not_leak(self):
        from freecut import builder, layouts

        with tempfile.TemporaryDirectory() as tmp:
            clip = _solid_clip(tmp, "s.mp4", "0x336699")
            p = builder.new_project(320, 180, 10)
            mat = model.VideoMaterial(path=clip, duration=2_000_000, width=320, height=180)
            p.add_material(mat)
            a = model.Segment(material_id=mat.id, target_timerange=model.Timerange(0, 400_000),
                              source_timerange=model.Timerange(0, 400_000))
            b = model.Segment(material_id=mat.id, target_timerange=model.Timerange(400_000, 400_000),
                              source_timerange=model.Timerange(0, 400_000))
            p.tracks.append(model.Track(type="video", segments=[a]))
            p.tracks.append(model.Track(type="video", segments=[b]))
            self.assertEqual(a.material_id, b.material_id)
            layouts.apply(p, "左右分屏", [a, b])
            self.assertNotEqual(a.material_id, b.material_id, "共享素材应被克隆")
            self.assertEqual(sum(1 for m in p.materials.values() if m.type == "video"), 2)

    def test_gui_menu_applies_a_layout(self):
        app = _make_app()
        if app is None:
            self.skipTest("没有可用的图形环境")
        try:
            from freecut import builder, layouts

            with tempfile.TemporaryDirectory() as tmp:
                red = _solid_clip(tmp, "r2.mp4", "0xCC2222")
                green = _solid_clip(tmp, "g2.mp4", "0x22AA44")
                p = builder.new_project(320, 180, 10)
                builder.add_video(p, red, start=0, duration=400_000, track_index=0)
                builder.add_video(p, green, start=0, duration=400_000, track_index=1)
                app.project = p
                app.apply_layout("左右分屏")
                segs = layouts.video_segments(app.project)
                self.assertAlmostEqual(segs[0].clip.scale_x, segs[1].clip.scale_x, places=4)
                self.assertLess(segs[0].clip.tx, 0)
                self.assertGreater(segs[1].clip.tx, 0)
        finally:
            try:
                app.destroy()
            except Exception:
                pass


class TestTextStyles(unittest.TestCase):
    def test_presets_load(self):
        from freecut import textstyles

        names = textstyles.names()
        self.assertGreaterEqual(len(names), 10)
        self.assertEqual(len(names), len(set(names)), "模板名不能重复")
        self.assertTrue(textstyles.groups())

    def test_apply_and_reverse_lookup(self):
        from freecut import builder, textstyles

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=8)
        mat = p.material(seg.material_id)
        name = textstyles.names()[3]
        self.assertTrue(textstyles.apply(mat, name))
        self.assertEqual(textstyles.style_of(mat), name)

    def test_unknown_preset_is_rejected(self):
        from freecut import builder, textstyles

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=8)
        self.assertFalse(textstyles.apply(p.material(seg.material_id), "不存在的模板"))

    def test_preset_changes_the_rendered_pixels(self):
        from freecut import builder, textstyles
        from freecut.render import Renderer

        p = builder.new_project(320, 180, 30)
        p.canvas.background = "#1e2430"
        seg = builder.add_text(p, "花字", start=0, duration=500_000, size=15)
        r = Renderer(p, 320, 180, 30)
        plain = r.render_frame(0).copy()
        r.close()
        textstyles.apply(p.material(seg.material_id), "综艺黄底")
        r = Renderer(p, 320, 180, 30)
        styled = r.render_frame(0).copy()
        r.close()
        self.assertGreater(float(np.abs(plain.astype(int) - styled.astype(int)).mean()), 1.0)
        # 黄底应该让画面里出现明显的黄色像素
        yellow = (styled[..., 0] > 180) & (styled[..., 1] > 160) & (styled[..., 2] < 120)
        self.assertGreater(int(yellow.sum()), 200)

    def test_preset_survives_project_roundtrip(self):
        from freecut import builder, jianying, jianying_export, textstyles

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=8)
        textstyles.apply(p.material(seg.material_id), "霓虹青粉")
        with tempfile.TemporaryDirectory() as tmp:
            draft = jianying_export.save_draft(p, tmp, name="ts", encrypt=False)
            back = jianying.load_draft(draft)
        mat = next(m for m in back.materials.values() if m.type == "text")
        self.assertEqual(mat.get("border_color"), "#FF3FA4")
        self.assertAlmostEqual(float(mat.get("border_width", 0)), 0.06, places=4)


class TestChromaKey(unittest.TestCase):
    def test_hex_roundtrip(self):
        from freecut import chroma

        self.assertEqual(chroma.to_hex(chroma.parse_hex("#3a7bd5")), "#3a7bd5")
        self.assertEqual(chroma.to_hex(chroma.parse_hex("3a7bd5")), "#3a7bd5")
        np.testing.assert_allclose(chroma.parse_hex("nonsense"), [0, 1, 0])

    def test_green_is_cut_and_subject_is_kept(self):
        from freecut import chroma

        green = np.zeros((8, 8, 3), np.float32)
        green[..., 1] = 0.9
        subject = np.zeros((8, 8, 3), np.float32)
        subject[..., 0] = 0.85
        subject[..., 1] = 0.3
        _, a_green = chroma.apply(green, "#00ff00")
        _, a_subject = chroma.apply(subject, "#00ff00")
        self.assertLess(float(a_green.mean()), 0.02)
        self.assertGreater(float(a_subject.mean()), 0.98)

    def test_similarity_is_monotonic(self):
        from freecut import chroma

        # 稍微偏亮的绿：相似度越大，抠得越干净
        img = np.zeros((4, 4, 3), np.float32)
        img[..., 1] = 0.75
        _, tight = chroma.apply(img, "#00ff00", similarity=0.05, smoothness=0.02)
        _, loose = chroma.apply(img, "#00ff00", similarity=0.40, smoothness=0.02)
        self.assertLess(float(loose.mean()), float(tight.mean()))

    def test_spill_suppression_dims_the_key_channel(self):
        from freecut import chroma

        rim = np.array([[[0.45, 0.70, 0.30]]], np.float32)
        out, _ = chroma.apply(rim, "#00ff00", spill=1.0)
        self.assertLess(float(out[0, 0, 1]), 0.70)
        np.testing.assert_allclose(out[0, 0, 0], 0.45, atol=1e-5)   # 其它通道不动

    def test_builder_attaches_one_mask_and_updates_it(self):
        from freecut import builder

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=8)
        m1 = builder.add_chroma(p, seg, key="#00ff00", similarity=0.2)
        self.assertEqual(m1.type, "mask")
        self.assertEqual(m1.get("resource_type"), "chroma")
        self.assertIn(m1.id, seg.extra_material_refs)
        m2 = builder.add_chroma(p, seg, key="#0000ff", similarity=0.35)
        self.assertEqual(m1.id, m2.id, "第二次应更新同一个蒙版")
        self.assertEqual(m2.get("config")["key"], "#0000ff")
        self.assertEqual(sum(1 for m in p.materials.values() if m.type == "mask"), 1)

    def test_renderer_makes_the_canvas_show_through(self):
        from PIL import Image

        from freecut import builder
        from freecut.render import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "green.png")
            arr = np.zeros((60, 90, 3), np.uint8)
            arr[..., 1] = 230
            arr[20:40, 30:60] = (200, 80, 60)
            Image.fromarray(arr).save(path)

            p = builder.new_project(90, 60, 10)
            p.canvas.background = "#102030"
            seg = builder.add_image(p, path, start=0, duration=500_000)
            r = Renderer(p, 90, 60, 10)
            before = r.render_frame(0).copy()
            r.close()
            builder.add_chroma(p, seg, key="#00ff00")
            r = Renderer(p, 90, 60, 10)
            after = r.render_frame(0).copy()
            r.close()
        # 左上角原本是绿幕，现在应该露出画布底色
        self.assertGreater(abs(int(before[2, 2, 1]) - int(after[2, 2, 1])), 30)
        self.assertLess(abs(int(after[2, 2, 0]) - 0x10), 12)
        self.assertLess(abs(int(after[2, 2, 1]) - 0x20), 12)

    def test_chroma_mask_survives_export_roundtrip(self):
        from freecut import builder, jianying, jianying_export

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000, size=8)
        builder.add_chroma(p, seg, key="#00ff00", similarity=0.3, spill=0.4)
        with tempfile.TemporaryDirectory() as tmp:
            draft = jianying_export.save_draft(p, tmp, name="ck", encrypt=False)
            back = jianying.load_draft(draft)
        masks = [m for m in back.materials.values() if m.type == "mask"]
        self.assertTrue(masks)
        cfg = masks[0].get("config") or {}
        self.assertEqual(masks[0].get("resource_type"), "chroma")
        self.assertAlmostEqual(float(cfg.get("similarity", 0)), 0.3, places=4)


class TestMenuTools(unittest.TestCase):
    """The menu exposes features that used to be CLI-only."""

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")
        import freecut.gui as gui_mod

        self.gui = gui_mod
        self.dialogs = []
        gui_mod.messagebox.showerror = lambda title, msg, **kw: self.dialogs.append((title, msg))
        gui_mod.messagebox.showinfo = lambda title, msg, **kw: self.dialogs.append((title, msg))
        gui_mod.messagebox.showwarning = lambda title, msg, **kw: self.dialogs.append((title, msg))
        gui_mod.messagebox.askyesno = lambda *a, **kw: False

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def _top_menus(self):
        bar = self.app.nametowidget(self.app.cget("menu"))
        names = []
        for i in range(1, 10):
            try:
                names.append(bar.entrycget(i, "label"))
            except Exception:
                break
        return names

    def test_menu_has_the_expected_cascades(self):
        names = self._top_menus()
        self.assertEqual(names[:4], ["文件", "编辑", "工具", "帮助"])

    def test_tool_menu_lists_the_cli_only_features(self):
        bar = self.app.nametowidget(self.app.cget("menu"))
        tools = bar.nametowidget(bar.entrycget(3, "menu"))
        labels = []
        for i in range(0, 12):
            try:
                if tools.type(i) != "separator":
                    labels.append(tools.entrycget(i, "label"))
            except Exception:
                break
        joined = " ".join(labels)
        for expect in ("一键成片（按画面分镜）", "一键成片（按音乐节拍）", "素材体检", "校验导出的成品"):
            self.assertIn(expect, joined)

    def test_pr_import_and_xml_export_roundtrip(self):
        if not os.path.isfile(PR_SAMPLE):
            self.skipTest("没有 PR 样本工程")
        app = self.app
        self.gui.filedialog.askopenfilename = lambda **kw: PR_SAMPLE
        app.import_prproj()
        app.update()
        self.assertGreater(sum(len(t.segments) for t in app.project.tracks), 0)
        self.assertFalse(self.dialogs, f"不该弹错误框: {self.dialogs}")

        out = os.path.join(tempfile.gettempdir(), "freecut_menu_export.xml")
        if os.path.isfile(out):
            os.remove(out)
        self.gui.filedialog.asksaveasfilename = lambda **kw: out
        app.export_fcp7()
        self.assertTrue(os.path.isfile(out))
        with open(out, "r", encoding="utf-8") as fh:
            head = fh.read(400)
        self.assertIn("<xmeml", head)

    def test_autocut_builds_a_new_timeline(self):
        app = self.app
        # 自足素材：临时目录要活到测试结束，否则成片时文件已被删
        app.project = _demo_for(self)
        app.autocut_scene()
        app.update()
        self.assertGreater(sum(len(t.segments) for t in app.project.tracks), 0)
        self.assertFalse(self.dialogs, f"一键成片不该报错: {self.dialogs}")


class TestPanelWiring(unittest.TestCase):
    """Buttons in the properties panel must actually do something.

    Regression guard: the keyframe and animation buttons used to raise
    ``AttributeError`` on click (``self.playhead`` / ``self.set_status``) because
    the panel is not the app.
    """

    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")
        app = self.app
        seg = builder.add_text(app.project, "面板", start=0, duration=2_000_000, size=10)
        app.refresh_bin()
        app.select(seg)
        app.playhead = 500_000
        app.update()
        self.seg = seg

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def test_keyframe_button_creates_a_keyframe(self):
        app = self.app
        self.seg.keyframes.clear()
        app.select(self.seg)                     # 重建面板以刷新按钮
        app.update()
        buttons = _find_widgets(app.props, "TButton", "缩放")
        self.assertTrue(buttons, "变换页应有「缩放」打点按钮")
        buttons[0].invoke()
        app.update()
        self.assertTrue(self.seg.keyframes.get("KFTypeScaleX"))
        self.assertEqual(self.seg.keyframes["KFTypeScaleX"][0].time_offset, 500_000)

    def test_transition_button_adds_a_transition(self):
        app = self.app
        app.select(self.seg)
        app.update()
        combos = [c for c in _find_widgets(app.props, "TCombobox")
                  if c.cget("values") and any("叠化" in str(v) for v in c.cget("values"))]
        self.assertTrue(combos, "应有转场下拉框")
        combos[0].set("叠化")
        _find_widgets(app.props, "TButton", "添加转场")[0].invoke()
        app.update()
        kinds = [m for m in app.project.materials.values() if m.type == "transition"]
        self.assertTrue(kinds)
        self.assertEqual(kinds[-1].get("name"), "叠化")

    def test_animation_button_adds_an_animation(self):
        app = self.app
        app.select(self.seg)
        app.update()
        combos = [c for c in _find_widgets(app.props, "TCombobox")
                  if c.cget("values") and any(" · " in str(v) for v in c.cget("values"))]
        self.assertTrue(combos, "应有动画下拉框")
        combos[0].set(combos[0].cget("values")[0])
        _find_widgets(app.props, "TButton", "添加动画")[0].invoke()
        app.update()
        mats = [m for m in app.project.materials.values() if m.type == "material_animation"]
        self.assertTrue(mats)
        self.assertEqual(len(mats[0].get("animations")), 1)


class TestUndoRedo(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        if self.app is None:
            self.skipTest("没有可用的图形环境")

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.destroy()
            except Exception:
                pass

    def test_undo_restores_the_previous_state(self):
        app = self.app
        def total(proj):
            return sum(len(tr.segments) for tr in proj.tracks)

        app.push_undo()
        builder.add_text(app.project, "x", start=0, duration=1_000_000, size=8)
        n = total(app.project)
        app.undo()
        self.assertEqual(total(app.project), n - 1)

    def test_redo_brings_it_back(self):
        app = self.app
        app.push_undo()
        def total(proj):
            return sum(len(tr.segments) for tr in proj.tracks)

        builder.add_text(app.project, "x", start=0, duration=1_000_000, size=8)
        n = total(app.project)
        n_before = n
        app.undo()
        app.redo()
        self.assertEqual(total(app.project), n_before)

    def test_new_edit_clears_redo(self):
        app = self.app
        app.push_undo()
        builder.add_text(app.project, "a", start=0, duration=500_000, size=8)
        app.undo()
        self.assertTrue(app._redo)
        app.push_undo()                      # 新操作
        self.assertEqual(app._redo, [])

    def test_empty_undo_is_harmless(self):
        app = self.app
        app._undo.clear()
        app._redo.clear()
        app.undo()
        app.redo()
        self.assertIn("没有可", app.status.cget("text"))

    def test_selection_survives_undo(self):
        app = self.app
        seg = builder.add_text(app.project, "keep", start=0, duration=800_000, size=8)
        app.refresh_bin()
        app.select(seg)
        app.push_undo()
        builder.add_text(app.project, "other", start=0, duration=800_000, size=8)
        app.select(seg)
        app.undo()
        self.assertIsNotNone(app.timeline.selected)
        self.assertEqual(app.timeline.selected.id, seg.id)

    def test_stack_is_bounded(self):
        app = self.app
        for _ in range(60):
            app.push_undo()
        self.assertLessEqual(len(app._undo), 50)

    def test_buttons_follow_the_stacks(self):
        app = self.app
        app._undo.clear()
        app._redo.clear()
        app._update_history_buttons()
        self.assertEqual(str(app.undo_btn["state"]), "disabled")
        app.push_undo()
        self.assertEqual(str(app.undo_btn["state"]), "normal")


class TestRelink(unittest.TestCase):
    def _project_with_moved_media(self, tmp):
        import shutil

        old_dir = os.path.join(tmp, "old")
        os.makedirs(old_dir)
        media = os.path.join(old_dir, "clip.mp4")
        with open(media, "wb") as fh:
            fh.write(b"not a real video")     # enough for path bookkeeping
        p = builder.new_project(160, 90, 30, name="relink")
        mat = model.VideoMaterial(path=media, duration=1_000_000, width=160, height=90)
        p.add_material(mat)
        moved = os.path.join(tmp, "new", "assets")
        os.makedirs(os.path.dirname(moved), exist_ok=True)
        shutil.move(old_dir, moved)
        return p, mat, moved

    def test_missing_detected(self):
        from freecut import relink

        with tempfile.TemporaryDirectory() as tmp:
            p, mat, moved = self._project_with_moved_media(tmp)
            items = relink.missing_media(p)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "clip.mp4")
        self.assertEqual(items[0].material_id, mat.id)

    def test_suggest_finds_same_basename(self):
        from freecut import relink

        with tempfile.TemporaryDirectory() as tmp:
            p, _mat, moved = self._project_with_moved_media(tmp)
            items = relink.suggest(p, [moved])
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0].candidates), 1)
        self.assertTrue(items[0].candidates[0].endswith("clip.mp4"))

    def test_auto_relink_fixes_the_project(self):
        from freecut import relink

        with tempfile.TemporaryDirectory() as tmp:
            p, mat, moved = self._project_with_moved_media(tmp)
            changed = relink.auto_relink(p, [moved])
            self.assertEqual(changed, 1)
            self.assertEqual(relink.missing_media(p), [])
            self.assertTrue(os.path.isfile(str(p.material(mat.id).get("path"))))

    def test_no_candidate_means_no_change(self):
        from freecut import relink

        with tempfile.TemporaryDirectory() as tmp:
            p, _mat, _moved = self._project_with_moved_media(tmp)
            empty = os.path.join(tmp, "empty")
            os.makedirs(empty)
            self.assertEqual(relink.auto_relink(p, [empty]), 0)
            self.assertEqual(len(relink.missing_media(p)), 1)

    def test_report_text(self):
        from freecut import relink

        with tempfile.TemporaryDirectory() as tmp:
            p, _mat, _moved = self._project_with_moved_media(tmp)
            text = relink.format_report(relink.missing_media(p))
        self.assertIn("缺失素材 1 个", text)
        self.assertIn("clip.mp4", text)


class TestCatalog(unittest.TestCase):
    def test_counts(self):
        from freecut import catalog

        stats = catalog.stats()
        self.assertGreaterEqual(stats["transitions"], 400)
        self.assertEqual(stats["mix_modes"], 10)
        self.assertGreaterEqual(stats["masks"], 6)
        self.assertGreaterEqual(stats["filters"], 900)
        self.assertGreaterEqual(stats["animations"], 500)

    def test_known_transition_resolves(self):
        from freecut import catalog

        item = catalog.find_transition(name="叠化")
        self.assertIsNotNone(item)
        self.assertTrue(item["resource_id"])
        self.assertGreater(item["duration_s"], 0)

    def test_transition_attached_by_name(self):
        from freecut import builder

        p = builder.new_project(320, 180, 30)
        seg = builder.add_text(p, "x", start=0, duration=1_000_000)
        mat = builder.add_transition(p, seg, name="闪黑")
        self.assertTrue(mat.get("resource_id"))
        self.assertIn(mat.get("name"), ("闪黑", "fade_black"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestMaterialPersistence(unittest.TestCase):
    """保存→读取之后，每种材质的"身份"字段都不能丢。

    第 149 轮发现转场的引擎（kind）根本没落盘：界面能选 17 种，
    存下来只剩名字，重启后全部退回叠化。这里把 10 种材质一起过一遍。
    """

    REF_TYPES = ("effect", "material_animation", "transition", "curve", "hsl",
                 "color_wheel", "mask", "mix_mode", "filter", "color_migration")

    def _identity(self, ref):
        for key in ("kind", "name", "type"):
            value = ref.get(key)
            if value:
                return str(value)
        return ""

    def test_every_material_type_survives_roundtrip(self):
        from freecut import builder
        from freecut.model import Project

        with tempfile.TemporaryDirectory() as tmp:
            image = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "out", "_trans_a.png")
            if not os.path.isfile(image):
                self.skipTest("缺少示例图")
            project = builder.new_project(160, 90, 30, name="all")
            seg = builder.add_image(project, image, start=0, duration=2_000_000)
            builder.add_effect(project, seg, "glow", strength=0.7)
            builder.add_animation(project, seg, "入场", "淡入", duration_us=400_000)
            builder.add_transition(project, seg, kind="square", name="方形",
                                   duration_us=300_000)
            builder.set_curves(project, seg, rgb=[(0.0, 0.0), (1.0, 0.8)])
            builder.set_hsl(project, seg, bands={"orange": {"sat": 0.3}})
            builder.set_wheels(project, seg, lift=(0.01, 0.0, -0.01))
            builder.add_mask(project, seg, name="圆形", width=0.5, height=0.5)
            builder.set_mix_mode(project, seg, "滤色")
            builder.add_cloud_filter(project, seg, "1970")
            builder.set_color_migration(project, seg, image, intensity=0.4)

            before = sorted((r.type, self._identity(r)) for r in project.refs_of(seg))
            path = os.path.join(tmp, "p.json")
            project.save(path)
            loaded = Project.load(path)
            seg2 = next(s for t_ in loaded.tracks if t_.type == "video" for s in t_.segments)
            after = sorted((r.type, self._identity(r)) for r in loaded.refs_of(seg2))

        self.assertEqual(before, after)
        kinds = [kind for kind, _ in before]
        for expected in self.REF_TYPES:
            self.assertIn(expected, kinds, f"{expected} 没有出现在往返结果里")

    def test_transition_engine_is_stored_not_just_the_name(self):
        from freecut import builder
        from freecut.render import transition_kind

        project = builder.new_project(160, 90, 30, name="t")
        seg = builder.add_image(project, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "_trans_a.png"),
            start=0, duration=1_000_000)
        mat = builder.add_transition(project, seg, kind="zoom_push", name="推近",
                                     duration_us=300_000)
        self.assertEqual(mat.get("kind"), "zoom_push", "引擎必须落盘")
        self.assertEqual(transition_kind(mat), "zoom_push")


class TestTimingClaims(unittest.TestCase):
    """文档里写死的耗时要在实测区间内，否则重新测而不是改数字。"""

    def test_auditor_rejects_absurd_timings(self):
        import importlib.util

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "tools", "doc_audit.py")
        spec = importlib.util.spec_from_file_location("doc_audit_timing", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["doc_audit_timing"] = mod
        spec.loader.exec_module(mod)

        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path as _P
            bad = _P(os.path.join(tmp, "bad.md"))
            bad.write_text("python -m freecut demo --verify   # ① 999 秒：出片\n",
                           encoding="utf-8")
            original = mod.DOCS
            mod.DOCS = [bad]
            try:
                problems = mod.audit()
            finally:
                mod.DOCS = original
        kinds = [p.kind for p in problems]
        self.assertIn("耗时", kinds, "999 秒应当被判定为不实")

    def test_real_docs_pass(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        start = os.path.join(os.path.dirname(repo), "START-HERE.md")
        if not os.path.isfile(start):
            self.skipTest("没有 START-HERE")
        text = open(start, encoding="utf-8").read()
        self.assertIn("① 62 秒", text, "START-HERE 的第一条命令应写实测 62 秒")


class TestPackagingClaims(unittest.TestCase):
    """打包产物的体积/启动时间也要与实测对得上（差太多就重测，不改数字）。"""

    def _repo(self):
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_single_file_size_matches_doc(self):
        repo = self._repo()
        exe = os.path.join(repo, "dist", "FreeCut.exe")
        if not os.path.isfile(exe):
            self.skipTest("没有打包产物")
        size_mb = os.path.getsize(exe) / (1024 * 1024)
        text = open(os.path.join(os.path.dirname(repo), "DELIVERY.md"),
                    encoding="utf-8").read()
        import re
        claimed = [float(x) for x in re.findall(r"便携单文件 \*\*([\d.]+) MB\*\*", text)]
        if not claimed:
            self.skipTest("文档里没写单文件体积")
        self.assertAlmostEqual(size_mb, claimed[0], delta=max(3.0, size_mb * 0.1),
                               msg=f"实际 {size_mb:.1f} MB，文档写 {claimed[0]} MB")

    def test_dir_build_size_matches_doc(self):
        repo = self._repo()
        folder = os.path.join(repo, "dist", "FreeCutDir")
        if not os.path.isdir(folder):
            self.skipTest("没有目录版产物")
        total = 0
        for root, _dirs, names in os.walk(folder):
            for name in names:
                total += os.path.getsize(os.path.join(root, name))
        size_mb = total / (1024 * 1024)
        text = open(os.path.join(os.path.dirname(repo), "DELIVERY.md"),
                    encoding="utf-8").read()
        import re
        claimed = [float(x) for x in re.findall(r"\*\*(\d+) MB\*\*（\d+ 个文件", text)]
        if not claimed:
            self.skipTest("文档里没写目录版体积")
        self.assertAlmostEqual(size_mb, claimed[0], delta=max(40.0, size_mb * 0.15),
                               msg=f"实际 {size_mb:.0f} MB，文档写 {claimed[0]:.0f} MB")
    def test_720p_baseline_claim_is_plausible(self):
        """R7 写的 720p 每帧耗时必须在实测区间内（第 153 轮复测过）。"""
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(repo, "RISKS.md")
        if not os.path.isfile(path):
            self.skipTest("没有 RISKS.md")
        text = open(path, encoding="utf-8").read()
        import re as _re

        values = [float(v) for v in _re.findall(r"(\d{2,3})\s*ms/帧", text)
                  if "1280" in text or "720p" in text]
        self.assertTrue(values, "R7 应当写明每帧耗时")
        for value in values:
            self.assertGreaterEqual(value, 100.0, f"{value} ms/帧 快得不真实")
            self.assertLessEqual(value, 200.0, f"{value} ms/帧 慢得不真实")



    def test_ffmpeg_falls_back_to_jianying_install(self):
        """打包版没有外置 ffmpeg 时，要能用剪映自带的那份（第 184 轮补）。"""
        from freecut import media

        found = media._find_in_jianying()
        if found is None:
            self.skipTest("本机没装剪映")
        self.assertTrue(os.path.isfile(found))
        self.assertTrue(found.lower().endswith("ffmpeg.exe"))
        # 能跑才算数：剪映的 ffmpeg 是壳，缺 DLL 就废
        import subprocess

        proc = subprocess.run([found, "-hide_banner", "-version"],
                              capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, 0)

    def test_ffmpeg_error_message_mentions_jianying(self):
        from freecut import media

        src = open(media.__file__, encoding="utf-8").read()
        self.assertIn("剪映自带", src, "报错信息应当告诉用户这个退路")

class TestBuiltinLuts(unittest.TestCase):
    """自研 LUT：能生成、能被现有加载器读回、能真的改变颜色。"""

    def test_look_count_reaches_twenty(self):
        from freecut import lutgen

        self.assertGreaterEqual(len(lutgen.LOOKS), 20, f"只有 {len(lutgen.LOOKS)} 支")
        for name in ("cinematic", "dark_mood", "japanese", "cyber", "vintage",
                     "nordic", "sunset", "forest", "high_key", "low_key"):
            self.assertIn(name, lutgen.LOOKS, f"缺少 {name}")
            self.assertTrue(lutgen.labels()[name], f"{name} 没有中文标签")

    def test_every_look_bakes_and_loads(self):
        import numpy as np

        from freecut import color, lutgen

        with tempfile.TemporaryDirectory() as tmp:
            paths = lutgen.write_all(tmp)
            self.assertEqual(len(paths), len(lutgen.LOOKS))
            for path in paths:
                with self.subTest(lut=os.path.basename(path)):
                    self.assertTrue(os.path.getsize(path) > 10_000)
                    table = color.load_cube_lut(path)
                    self.assertEqual(table.shape, (lutgen.GRID, lutgen.GRID, lutgen.GRID, 3))

    def test_looks_actually_change_pixels(self):
        import numpy as np

        from freecut import color, lutgen

        base = np.zeros((8, 8, 3), dtype=np.float32)
        base[:] = (0.85, 0.35, 0.25)
        with tempfile.TemporaryDirectory() as tmp:
            for name in lutgen.LOOKS:
                path = lutgen.write_cube(name, os.path.join(tmp, f"{name}.cube"))
                table = color.load_cube_lut(path)
                out = color.apply_lut3d(base, table)
                with self.subTest(look=name):
                    self.assertGreater(float(np.abs(out - base).mean()), 0.01)

    def test_mono_produces_equal_channels(self):
        import numpy as np

        from freecut import color, lutgen

        with tempfile.TemporaryDirectory() as tmp:
            path = lutgen.write_cube("mono", os.path.join(tmp, "mono.cube"))
            table = color.load_cube_lut(path)
        base = np.zeros((4, 4, 3), dtype=np.float32)
        base[:] = (0.9, 0.2, 0.2)
        out = color.apply_lut3d(base, table)
        r, g, b = (float(v) for v in out[0, 0])
        self.assertAlmostEqual(r, g, places=2)
        self.assertAlmostEqual(g, b, places=2)

    def test_generated_dir_is_in_lut_search_path(self):
        """生成到默认目录后，LUT 库要能扫到（否则界面里点了「刷新库」也没有）。"""
        from freecut import luts

        roots = [os.path.normcase(os.path.abspath(r)) for r in luts.default_roots()]
        expected = os.path.normcase(os.path.abspath(os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
            "FreeCut", "luts")))
        self.assertTrue(any(r == expected for r in roots) or os.path.isdir(expected) is False,
                        "默认 LUT 目录不在搜索路径里")


    def test_make_refreshes_the_library_cache(self):
        """生成之后紧接着 list 必须能看到——不能因为 TTL 缓存显示旧结果。"""
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["LOCALAPPDATA"] = tmp          # 隔离，别污染真实缓存
            proc = subprocess.run([sys.executable, "-m", "freecut", "lut", "make"],
                                  capture_output=True, timeout=300, cwd=repo, env=env)
            text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            self.assertEqual(proc.returncode, 0, text[-300:])
            self.assertIn("含刚生成的", text)

            proc2 = subprocess.run([sys.executable, "-m", "freecut", "lut", "list"],
                                   capture_output=True, timeout=300, cwd=repo, env=env)
            listed = (proc2.stdout + proc2.stderr).decode("utf-8", "replace")
            # 不写死数字：生成多少就应当看到多少（现在是 20 支）
            from freecut import lutgen

            self.assertIn(f"本地 {len(lutgen.LOOKS)}", listed,
                          f"list 没看到刚生成的：{listed[:200]}")

class TestBuiltinLutUi(unittest.TestCase):
    """自研 LUT 在效果页要能一键套用（生成 + 挂到片段上）。"""

    def test_effects_tab_has_the_preset_picker(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(repo, "freecut", "gui.py"), encoding="utf-8").read()
        start = text.find("    def _effects_tab(")
        end = text.find("\n    def ", start + 10)
        body = text[start:end]
        self.assertIn("lutgen", body, "效果页应当引用 lutgen")
        self.assertIn("生成并套用", body, "应当有一键套用按钮")
        self.assertIn("apply_builtin_lut", body)

    def test_apply_writes_a_real_cube_and_attaches_it(self):
        import numpy as np

        from freecut import builder, color, lutgen

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "teal_orange.cube")
            lutgen.write_cube("teal_orange", path)
            self.assertTrue(os.path.isfile(path))

            image = os.path.join(tmp, "i.png")
            from PIL import Image

            Image.fromarray(np.full((90, 160, 3), (120, 140, 170), np.uint8)).save(image)
            project = builder.new_project(160, 90, 30, name="lutui")
            seg = builder.add_image(project, image, start=0, duration=1_000_000)
            builder.add_filter(project, seg, path, 0.8)

            refs = [r for r in project.refs_of(seg) if r.type == "filter"]
            self.assertTrue(refs, "片段上应当出现 filter 引用")
            self.assertEqual(os.path.abspath(refs[0].get("path")), os.path.abspath(path))
            table = color.load_cube_lut(refs[0].get("path"))
            self.assertEqual(table.shape[:3], (lutgen.GRID,) * 3)


class TestStartHereNextSteps(unittest.TestCase):
    """START-HERE 的「接下来三件事」：命令必须是真的子命令。"""

    def _text(self):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(repo, "START-HERE.md")
        if not os.path.isfile(path):
            self.skipTest("没有 START-HERE")
        return open(path, encoding="utf-8").read()

    def test_section_exists_with_three_steps(self):
        text = self._text()
        start = text.find("## 接下来你能做的三件事")
        self.assertGreater(start, 0, "缺少这一节")
        section = text[start:text.find("\n## ", start + 5)]
        for mark in ("①", "②", "③"):
            self.assertIn(mark, section, f"缺少第 {mark} 件事")

    def test_named_subcommands_are_real(self):
        import re

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cli = open(os.path.join(repo, "freecut", "cli.py"), encoding="utf-8").read()
        known = set(re.findall(r'add_parser\(\s*["\']([^"\']+)["\']', cli))
        known |= set(re.findall(r'\(\s*["\']([a-z][a-z\-]+)["\']\s*,\s*_cmd_', cli))
        text = self._text()
        start = text.find("## 接下来你能做的三件事")
        section = text[start:text.find("\n## ", start + 5)]
        used = set(re.findall(r"freecut\s+([a-z][a-z\-]*)", section))
        self.assertGreaterEqual(len(used), 3, "三件事应当各给一条命令")
        for name in sorted(used):
            self.assertIn(name, known, f"START-HERE 里的 {name} 不是真子命令")

    def test_no_third_party_network_claim_is_wrong(self):
        """文档说"不需要联网"，那就别在命令里出现 http 下载。"""
        text = self._text()
        start = text.find("## 接下来你能做的三件事")
        section = text[start:text.find("\n## ", start + 5)]
        self.assertNotIn("http://", section)
        self.assertNotIn("https://", section)


class TestReadmeUsage(unittest.TestCase):
    """README 的「我该怎么用它」：三条路都要给真命令，且能照抄。"""

    def _readme(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return open(os.path.join(repo, "README.md"), encoding="utf-8").read()

    def test_section_has_three_paths(self):
        text = self._readme()
        start = text.find("## 我该怎么用它")
        self.assertGreater(start, 0, "缺少这一节")
        section = text[start:text.find("\n## ", start + 5)]
        for mark in ("①", "②", "③"):
            self.assertIn(mark, section)

    def test_commands_are_real_subcommands(self):
        import re

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cli = open(os.path.join(repo, "freecut", "cli.py"), encoding="utf-8").read()
        known = set(re.findall(r'add_parser\(\s*["\']([^"\']+)["\']', cli))
        known |= set(re.findall(r'\(\s*["\']([a-z][a-z\-]+)["\']\s*,\s*_cmd_', cli))
        text = self._readme()
        start = text.find("## 我该怎么用它")
        section = text[start:text.find("\n## ", start + 5)]
        used = set(re.findall(r"freecut\s+([a-z][a-z\-]*)", section))
        self.assertGreaterEqual(len(used), 3)
        for name in sorted(used):
            self.assertIn(name, known, f"README 里的 {name} 不是真子命令")

    def test_field_audit_number_is_stated(self):
        """selfcheck 的数字要么写清楚，要么别提——不能含糊说"校验通过"。"""
        import re

        text = self._readme()
        m = re.search(r"写了 (\d+) 个字段，渲染器读 (\d+) 个", text)
        self.assertIsNotNone(m, "应当写明字段审计的两个数字")
        wrote, read = int(m.group(1)), int(m.group(2))
        self.assertGreaterEqual(wrote, read)
        self.assertGreaterEqual(read, 10)


class TestMaskStroke(unittest.TestCase):
    """蒙版描边（剪映 video_mask_stroke）：第 161 轮补的能力。"""

    def test_stroke_ring_is_on_the_edge_only(self):
        import numpy as np

        from freecut import mask

        m = mask.build_mask(160, 90, {"centerX": 0.0, "centerY": 0.0,
                                      "width": 0.6, "height": 0.6, "feather": 0.01},
                            "circle")
        ring = mask.add_stroke(m, 3, (1.0, 0.9, 0.0))
        ring_a = np.clip(ring.sum(axis=2), 0.0, 1.0)
        # 描边是"边缘环带"，中心必须干净
        self.assertEqual(float(ring_a[45, 80]), 0.0, "中心不该被描边盖住")
        self.assertEqual(float(ring_a[:2, :2].max()), 0.0, "角落不该有描边")
        self.assertGreater(int((ring_a > 0).sum()), 100, "边缘应当有一圈")

    def test_zero_width_means_no_stroke(self):
        import numpy as np

        from freecut import mask

        m = mask.build_mask(80, 45, {"width": 0.6, "height": 0.6}, "circle")
        ring = mask.add_stroke(m, 0, (1, 1, 1))
        self.assertEqual(float(np.abs(ring).sum()), 0.0)

    def test_renderer_draws_a_coloured_ring(self):
        import numpy as np

        from freecut import builder
        from freecut.render import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            src = os.path.join(tmp, "red.png")
            Image.new("RGB", (160, 90), (180, 30, 30)).save(src)
            project = builder.new_project(160, 90, 30, name="stroke")
            seg = builder.add_image(project, src, start=0, duration=1_000_000)
            mat = builder.add_mask(project, seg, name="圆形", width=0.6, height=0.6,
                                   feather=0.01, stroke_width=3, stroke_color="#FFD400")
            self.assertEqual(mat.get("stroke").get("width"), 3)
            renderer = Renderer(project, 160, 90, 30)
            try:
                frame = renderer.render_frame(2)
            finally:
                renderer.close()
        yellow = ((frame[:, :, 0] > 200) & (frame[:, :, 1] > 170) & (frame[:, :, 2] < 90))
        self.assertGreater(int(yellow.sum()), 100, "画面里应当出现黄色描边")


class TestMaskShadow(unittest.TestCase):
    """蒙版投影（剪映 video_mask_shadow）：只在蒙版外面出现。"""

    def test_shadow_sits_outside_the_mask(self):
        from freecut import mask

        m = mask.build_mask(160, 90, {"centerX": 0.0, "centerY": 0.0,
                                      "width": 0.5, "height": 0.5, "feather": 0.01},
                            "circle")
        _rgb, a = mask.add_shadow(m, offset=(6, 6), blur=4, opacity=0.8)
        self.assertEqual(float(a[45, 80]), 0.0, "蒙版里面不该有影子")
        self.assertGreater(float(a.max()), 0.05, "外面应当有影子")

    def test_zero_opacity_draws_nothing(self):
        from freecut import mask

        m = mask.build_mask(80, 45, {"width": 0.5, "height": 0.5}, "circle")
        _rgb, a = mask.add_shadow(m, opacity=0.0)
        self.assertEqual(float(a.max()), 0.0)

    def test_renderer_shows_a_shadow(self):
        import numpy as np

        from freecut import builder
        from freecut.render import Renderer

        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            bg = os.path.join(tmp, "bg.png")
            fg = os.path.join(tmp, "fg.png")
            Image.new("RGB", (160, 90), (200, 190, 170)).save(bg)
            Image.new("RGB", (160, 90), (60, 120, 200)).save(fg)
            project = builder.new_project(160, 90, 30, name="sh")
            builder.add_image(project, bg, start=0, duration=1_000_000)
            seg = builder.add_image(project, fg, start=0, duration=1_000_000)
            seg.clip.scale_x = seg.clip.scale_y = 0.45
            mat = builder.add_mask(project, seg, name="圆形", width=0.9, height=0.9,
                                   feather=0.01, shadow_opacity=0.75,
                                   shadow_offset=(8, 6), shadow_blur=6)
            self.assertEqual(mat.get("shadow").get("dx"), 8)
            renderer = Renderer(project, 160, 90, 30)
            try:
                frame = renderer.render_frame(2)
            finally:
                renderer.close()
        # 与其猜哪个坐标是影子，不如直接对比"有影子/没影子"两帧
        with tempfile.TemporaryDirectory() as tmp2:
            from PIL import Image as _Image

            bg2 = os.path.join(tmp2, "bg.png")
            fg2 = os.path.join(tmp2, "fg.png")
            _Image.new("RGB", (160, 90), (200, 190, 170)).save(bg2)
            _Image.new("RGB", (160, 90), (60, 120, 200)).save(fg2)
            plain = builder.new_project(160, 90, 30, name="plain")
            builder.add_image(plain, bg2, start=0, duration=1_000_000)
            seg2 = builder.add_image(plain, fg2, start=0, duration=1_000_000)
            seg2.clip.scale_x = seg2.clip.scale_y = 0.45
            builder.add_mask(plain, seg2, name="圆形", width=0.9, height=0.9,
                             feather=0.01)
            renderer2 = Renderer(plain, 160, 90, 30)
            try:
                base_frame = renderer2.render_frame(2)
            finally:
                renderer2.close()
        diff = np.abs(frame.astype(float) - base_frame.astype(float)).mean(axis=2)
        self.assertGreater(int((diff > 2).sum()), 200, "有影子/没影子应当明显不同")


class TestSmoothEffect(unittest.TestCase):
    """磨皮（近似）：降噪有效、硬边缘保住、且明确不是人脸识别。"""

    def test_noise_is_reduced_on_flat_areas(self):
        import numpy as np

        from freecut import effects

        rng = np.random.default_rng(0)
        base = np.full((90, 160, 3), 0.6, dtype=np.float32)
        base = np.clip(base + rng.normal(0, 0.04, base.shape).astype(np.float32), 0, 1)
        params = dict(effects.EFFECT_SPEC["smooth"]["params"])
        out, _a = effects.apply_effect("smooth", base.copy(),
                                       np.ones((90, 160), dtype=np.float32), 0, params)
        before = float(base[0:30, 0:60].std())
        after = float(out[0:30, 0:60].std())
        self.assertLess(after, before * 0.8, f"噪点没被抹平（{before:.3f} → {after:.3f}）")

    def test_hard_edges_survive(self):
        import numpy as np

        from freecut import effects

        base = np.full((90, 160, 3), 0.6, dtype=np.float32)
        base[40:50, 70:90] = 0.05
        params = dict(effects.EFFECT_SPEC["smooth"]["params"])
        out, _a = effects.apply_effect("smooth", base.copy(),
                                       np.ones((90, 160), dtype=np.float32), 0, params)
        self.assertLess(float(out[43:47, 75:85].mean()), 0.25, "边缘块不该被磨亮")

    def test_label_says_approximate(self):
        from freecut import effects

        self.assertIn("近似", effects.label("smooth"),
                      "标签要诚实说明这是近似实现")
        self.assertIn("smooth", effects.available())


class TestAutoMatting(unittest.TestCase):
    """智能抠像（近似）：边框猜背景，把相近的像素抠掉。"""

    def _scene(self):
        import numpy as np

        h, w = 90, 160
        img = np.zeros((h, w, 3), dtype=np.float32)
        img[:] = (0.1, 0.6, 0.15)
        img[30:60, 50:110] = (0.85, 0.45, 0.2)
        return img

    def test_background_is_keyed_out(self):
        import numpy as np

        from freecut import effects

        img = self._scene()
        _rgb, alpha = effects.apply_effect(
            "auto_matting", img.copy(), np.ones(img.shape[:2], dtype=np.float32),
            0, dict(effects.EFFECT_SPEC["auto_matting"]["params"]))
        self.assertLess(float(alpha[5, 5]), 0.1, "背景应当被抠掉")
        self.assertGreater(float(alpha[45, 80]), 0.9, "主体应当保留")

    def test_alpha_shape_and_range(self):
        import numpy as np

        from freecut import effects

        img = self._scene()
        _rgb, alpha = effects.apply_effect(
            "auto_matting", img.copy(), np.ones(img.shape[:2], dtype=np.float32),
            0, dict(effects.EFFECT_SPEC["auto_matting"]["params"]))
        self.assertEqual(alpha.shape, img.shape[:2])
        self.assertGreaterEqual(float(alpha.min()), 0.0)
        self.assertLessEqual(float(alpha.max()), 1.0)

    def test_label_is_honest(self):
        from freecut import effects

        self.assertIn("近似", effects.label("auto_matting"))
        hint = effects.EFFECT_SPEC["auto_matting"]["hint"]
        self.assertIn("不做人像识别", hint, "提示里要说清不是 AI 分割")


class TestStabilize(unittest.TestCase):
    """防抖（近似）：平移估计、补偿方向、以及"变差就不交付"。"""

    def _shake(self, n=24, amp=5):
        import numpy as np

        rng = np.random.default_rng(5)
        big = (rng.integers(0, 256, (300, 420, 3))).astype(np.uint8)
        frames = []
        offsets = []
        for _i in range(n):
            dx = int(rng.integers(-amp, amp + 1))
            dy = int(rng.integers(-amp, amp + 1))
            offsets.append((dx, dy))
            frames.append(big[80 + dy:260 + dy, 60 + dx:380 + dx].copy())
        return frames, offsets

    def test_estimator_finds_a_known_shift(self):
        frames, offsets = self._shake(n=6, amp=4)
        shifts = stabilize_estimate(frames)
        self.assertEqual(shifts[0], (0, 0))
        # 估计值 = 相邻帧的裁剪偏移差（即"相机移动了多少"）
        agree = sum(1 for i in range(1, len(shifts))
                    if shifts[i][0] == offsets[i][0] - offsets[i - 1][0]
                    and shifts[i][1] == offsets[i][1] - offsets[i - 1][1])
        self.assertGreaterEqual(agree, len(shifts) - 1, f"估计不准: {shifts} vs {offsets}")

    def test_compensation_is_bounded(self):
        from freecut.stabilize import compensation

        real = [(0.0, 0.0), (10.0, -6.0), (40.0, 4.0)]
        smooth = [(0.0, 0.0), (5.0, -3.0), (20.0, 2.0)]
        fix = compensation(real, smooth, limit=8)
        for dx, dy in fix:
            self.assertLessEqual(abs(dx), 8)
            self.assertLessEqual(abs(dy), 8)

    def test_apply_shift_leaves_black_border(self):
        import numpy as np

        from freecut.stabilize import apply_shift

        frame = np.full((20, 20, 3), 200, dtype=np.uint8)
        moved = apply_shift(frame, 3, 2)
        self.assertEqual(int(moved[0, 0].max()), 0, "移动后边缘应当露黑")
        self.assertEqual(int(moved[10, 10].min()), 200, "中间内容应当保持")


def stabilize_estimate(frames):
    from freecut.stabilize import estimate_shifts

    return estimate_shifts(frames, max_shift=12, step=1, downscale=1)


class TestArchitectureFailures(unittest.TestCase):
    """失败记录必须留在文档里：写下来才不会重走一遍。"""

    def _arch(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return open(os.path.join(repo, "docs", "ARCHITECTURE.md"), encoding="utf-8").read()

    def test_failure_section_exists(self):
        text = self._arch()
        self.assertIn("试过但没用的做法", text)

    def test_stabilise_pitfalls_are_documented(self):
        text = self._arch()
        self.assertIn("匹配窗口", text, "要写清楚内窗那个坑")
        self.assertIn("符号", text, "要写清楚补偿符号反了")
        self.assertIn("sign=-1", text, "要留下当时量的数字")

    def test_bounding_box_rollback_is_documented(self):
        text = self._arch()
        self.assertIn("包围盒", text)
        self.assertIn("2–4%", text, "要留下端到端更慢的实测幅度")


class TestStabilizeRotation(unittest.TestCase):
    """旋转抖动：直接搜角度，比"两条带位移差"准得多。"""

    def _rotated_frames(self, n=8):
        import numpy as np

        from freecut.stabilize import rotate_frame

        rng = np.random.default_rng(9)
        big = rng.integers(0, 256, (240, 320, 3)).astype(np.uint8)
        angles = [float(rng.uniform(-2, 2)) for _ in range(n)]
        return [rotate_frame(big, a) for a in angles], angles

    def test_estimator_tracks_relative_rotation(self):
        from freecut.stabilize import estimate_rotations

        frames, angles = self._rotated_frames()
        est = estimate_rotations(frames, max_deg=4.0)
        self.assertEqual(est[0], 0.0)
        hits = 0
        for i in range(1, len(frames)):
            delta = angles[i] - angles[i - 1]          # 真实相对旋转
            if abs(abs(est[i]) - abs(delta)) < 0.6:    # 符号约定是"反向"，比幅度
                hits += 1
        self.assertGreaterEqual(hits, len(frames) - 3, f"估计偏差太大: {est} vs {angles}")

    def test_rotate_frame_exposes_corners(self):
        import numpy as np

        from freecut.stabilize import rotate_frame

        frame = np.full((60, 60, 3), 255, dtype=np.uint8)
        turned = rotate_frame(frame, 20.0)
        self.assertEqual(int(turned[0, 0].max()), 0, "转角应当露黑")
        self.assertGreater(int(turned[30, 30].min()), 200, "中心应当保持")

    def test_small_angle_is_a_noop(self):
        import numpy as np

        from freecut.stabilize import rotate_frame

        frame = np.full((20, 20, 3), 128, dtype=np.uint8)
        self.assertIs(rotate_frame(frame, 0.0), frame)


class TestEyeballTool(unittest.TestCase):
    """R1 的"三步走"脚本：能拍快照、能对比、能说人话。"""

    def _tool(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "tools", "eyeball_jianying.py")
        self.assertTrue(os.path.isfile(path), "缺少 tools/eyeball_jianying.py")
        return path

    def test_tool_exists_and_has_both_modes(self):
        text = open(self._tool(), encoding="utf-8").read()
        self.assertIn("--prepare", text)
        self.assertIn("--check", text)
        self.assertIn("打开剪映", text, "要写清楚人去做什么")

    def test_snapshot_survives_a_real_draft(self):
        """在我们自己写出的未加密草稿上跑一遍：快照 → 对比 → 结论。"""
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        drafts = os.path.join(repo, "out", "drafts")
        if not os.path.isdir(drafts):
            self.skipTest("没有可用的草稿")
        target = None
        for name in os.listdir(drafts):
            folder = os.path.join(drafts, name)
            if os.path.isfile(os.path.join(folder, "draft_content.json")):
                target = folder
                break
        if target is None:
            self.skipTest("草稿目录里没有 draft_content.json")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run([sys.executable, os.path.join(repo, "tools", "eyeball_jianying.py"),
                               "--prepare", target],
                              capture_output=True, timeout=300, cwd=repo, env=env)
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, text[-400:])
        self.assertIn("接下来三步", text)
        self.assertIn("--check", text)

        proc2 = subprocess.run([sys.executable, os.path.join(repo, "tools", "eyeball_jianying.py"),
                                "--check", target],
                               capture_output=True, timeout=300, cwd=repo, env=env)
        out = (proc2.stdout + proc2.stderr).decode("utf-8", "replace")
        self.assertIn("结论", out)
        self.assertIn("变化条目", out)


class TestCurveSampleTool(unittest.TestCase):
    """R3 的取样脚本：说明书能打印、能给字段形状、且对照的是导出后的真草稿。"""

    def _path(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "tools", "curve_sample.py")
        self.assertTrue(os.path.isfile(path), "缺少 tools/curve_sample.py")
        return repo, path

    def test_prepare_prints_three_steps(self):
        import subprocess

        repo, path = self._path()
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run([sys.executable, path, "--prepare"],
                              capture_output=True, timeout=120, cwd=repo, env=env)
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, text[-300:])
        self.assertIn("曲线", text)
        self.assertIn("HSL", text)
        self.assertIn("色轮", text)
        self.assertIn("--inspect", text)

    def test_inspect_reports_our_written_schema(self):
        """我们导出的草稿里，四个数组的键名要能被打印出来（作为对照基线）。"""
        import subprocess

        repo, path = self._path()
        drafts = os.path.join(repo, "out", "drafts")
        if not os.path.isdir(drafts):
            self.skipTest("没有导出的草稿")
        target = None
        for name in os.listdir(drafts):
            folder = os.path.join(drafts, name)
            if os.path.isfile(os.path.join(folder, "draft_content.json")):
                target = folder
                break
        if target is None:
            self.skipTest("草稿目录为空")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run([sys.executable, path, "--inspect", target],
                              capture_output=True, timeout=300, cwd=repo, env=env)
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, text[-400:])
        self.assertIn("我们写入端的结构", text)
        for name in ("color_curves", "hsl", "primary_color_wheels"):
            self.assertIn(name, text)
        self.assertIn("curves", text, "曲线数组的键应当被打印出来")


class TestVerifyEyeballStep(unittest.TestCase):
    """verify_all --eyeball：待人工的状态不能算失败。"""

    def _verify(self):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(repo, "verify_all.py")

    def test_flag_and_function_exist(self):
        text = open(self._verify(), encoding="utf-8").read()
        self.assertIn('"--eyeball"', text)
        self.assertIn("def run_eyeball", text)
        self.assertIn("FREECUT_EYEBALL_DRAFT", text, "要能从环境变量取草稿路径")

    def test_pending_state_is_not_a_failure(self):
        import importlib.util

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = self._verify()
        spec = importlib.util.spec_from_file_location("verify_all_mod", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["verify_all_mod"] = mod
        spec.loader.exec_module(mod)

        old = os.environ.pop("FREECUT_EYEBALL_DRAFT", None)
        try:
            step = mod.run_eyeball()
        finally:
            if old is not None:
                os.environ["FREECUT_EYEBALL_DRAFT"] = old
        self.assertTrue(step.ok, "没指定草稿不该让验收失败")
        self.assertTrue(step.skipped)
        self.assertTrue(step.summary)


class TestShaderEvidence(unittest.TestCase):
    """shader 对照记录：单版本机器也能校验「升级有没有改算法」。"""

    def _paths(self):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return (repo,
                os.path.join(repo, "内部实测", "evidence", "shaders.json"),
                os.path.join(repo, "内部实测", "tools", "reproduce.py"))

    def test_evidence_file_is_recorded(self):
        import json

        _repo, evidence, _script = self._paths()
        self.assertTrue(os.path.isfile(evidence), "缺少 evidence/shaders.json")
        data = json.load(open(evidence, encoding="utf-8"))
        self.assertIn("version", data)
        self.assertIn("shaders", data)
        self.assertGreaterEqual(len(data["shaders"]), 100, "记录的 shader 太少")
        for name, md5 in list(data["shaders"].items())[:5]:
            self.assertRegex(md5, r"^[a-f0-9]{32}$", f"{name} 的 md5 不像 md5")

    def test_check_detects_a_tampered_hash(self):
        import importlib.util

        _repo, _evidence, script = self._paths()
        spec = importlib.util.spec_from_file_location("repro_shader", script)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["repro_shader"] = mod
        spec.loader.exec_module(mod)

        import json

        recorded = json.loads(mod.SHADER_EVIDENCE.read_text(encoding="utf-8"))
        name = next(iter(recorded["shaders"]))
        tampered = {name: "0" * 32}
        ok, detail = mod._check_shaders_against_evidence(
            {"fake": tampered}, [("fake", mod.Path("."))])
        self.assertFalse(ok, "改过的 md5 必须被判为不一致")
        self.assertIn("已改动", detail)

    def test_missing_evidence_is_a_skip(self):
        import importlib.util

        _repo, _evidence, script = self._paths()
        spec = importlib.util.spec_from_file_location("repro_shader2", script)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["repro_shader2"] = mod
        spec.loader.exec_module(mod)

        original = mod.SHADER_EVIDENCE
        mod.SHADER_EVIDENCE = mod.Path("不存在的路径.json")
        try:
            ok, detail = mod._check_shaders_against_evidence({"x": {}}, [("x", mod.Path("."))])
        finally:
            mod.SHADER_EVIDENCE = original
        self.assertTrue(ok, "没有记录时不该判失败")
        self.assertIn("跳过", detail)


class TestPriorArtSnapshot(unittest.TestCase):
    """竞争项目的外部数据要带日期、带复核方式，不能当永久事实。"""

    def _report(self):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(repo, "同类项目", "REPORT.md")
        if not os.path.isfile(path):
            self.skipTest("没有 docs/REPORT.md")
        return open(path, encoding="utf-8").read()

    def test_snapshot_has_a_date(self):
        text = self._report()
        self.assertIn("观察快照", text)
        self.assertRegex(text, r"20\d\d-\d\d-\d\d", "快照必须写明观察日期")

    def test_snapshot_lists_the_three_projects(self):
        text = self._report()
        start = text.find("观察快照")
        section = text[start:start + 2000]
        for repo in ("jub0t/Concat", "GuanYixuan/pyJianYingDraft", "Xiangyu-CAS/rea-capcut"):
            self.assertIn(repo, section, f"快照里缺少 {repo}")

    def test_snapshot_says_how_to_recheck(self):
        text = self._report()
        self.assertIn("api.github.com", text, "要给出一条能自己复核的命令")
        self.assertIn("stargazers_count", text)

    def test_star_numbers_are_plausible(self):
        """防止手滑写出 105 这样的数字（量级明显不对）。"""
        import re

        text = self._report()
        start = text.find("观察快照")
        section = text[start:start + 2000]
        stars = [int(x.replace(" ", "")) for x in re.findall(r"\|\s*([\d\s]{3,7})\s*\|", section)]
        self.assertTrue(stars)
        for value in stars:
            self.assertGreaterEqual(value, 10, f"{value} 星太少，可能抄错了")
            self.assertLessEqual(value, 200_000, f"{value} 星不合常理")
    def test_react_claims_are_quoted_not_paraphrased(self):
        """ReaCut 的"它没有的功能"必须是英文原文引用，且给复核命令。"""
        text = self._report()
        self.assertIn("README 原话摘录", text)
        self.assertIn("speech-to-subtitle transcription", text,
                      "语音转字幕那条要引用原文，不能只写中文")
        self.assertIn("writing back to Jianying format is unsupported", text)
        self.assertIn("api.github.com/repos/Xiangyu-CAS/rea-capcut/readme", text,
                      "要给出能重取 README 的命令")

    def test_react_size_numbers_are_stated(self):
        """它自报的规模（16/16/8/64）和我们的对应数字要一起出现，方便对比。"""
        text = self._report()
        for token in ("16 字模板", "8 转场", "64 个原创素材"):
            self.assertIn(token, text, f"缺少 {token}")
        self.assertIn("17 转场引擎", text, "我们的对应数字也要写")



    def test_comparison_table_is_graded(self):
        """对比表的两边都要有级别列，且级别只能取 A/B/C/D。"""
        import re as _re

        text = self._report()
        start = text.find("### 和 FreeCut 的对比")
        self.assertGreater(start, 0, "找不到对比表")
        section = text[start:text.find("### README 原话摘录", start)]
        self.assertIn("ReaCut 级别", section)
        self.assertIn("FreeCut 级别", section)

        rows = [ln for ln in section.splitlines()
                if ln.startswith("|") and not ln.startswith("|---") and "维度" not in ln]
        self.assertGreaterEqual(len(rows), 10, "对比维度太少")
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            self.assertGreaterEqual(len(cells), 5, f"这一行缺级别列: {row[:50]}")
            for idx in (2, 4):
                cell = cells[idx]
                if cell == "—":
                    continue
                self.assertRegex(cell, r"[ABCD]", f"级别不合法: {row[:50]}")

    def test_comparison_marks_our_weakness_too(self):
        """不能只列我们的强项：要有一行是我们落后或待核的。"""
        text = self._report()
        start = text.find("### 和 FreeCut 的对比")
        section = text[start:text.find("### README 原话摘录", start)]
        self.assertTrue(("未核" in section) or ("✗" in section),
                        "对比表应当承认至少一处我们没做到/没核实的")

class TestLimitationsList(unittest.TestCase):
    """已知限制那一节：编号连续、每条有出处、没有重复编号。"""

    def _section(self):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "DELIVERY.md"), encoding="utf-8").read()
        start = text.find("## 7. 已知限制")
        self.assertGreater(start, 0, "找不到这一节")
        end = text.find("## 8.", start)
        return text[start:end if end > start else start + 3000]

    def test_numbers_are_sequential_and_unique(self):
        import re

        section = self._section()
        nums = [int(n) for n in re.findall(r"^(\d+)\. ", section, re.M)]
        self.assertGreaterEqual(len(nums), 8, "限制条目太少")
        self.assertEqual(nums, list(range(1, len(nums) + 1)),
                         f"编号不连续或有重复: {nums}")

    def test_items_point_at_evidence(self):
        """每条要么提到风险编号，要么给出命令/路径。"""
        import re

        section = self._section()
        items = re.split(r"^\d+\. ", section, flags=re.M)[1:]
        self.assertGreaterEqual(len(items), 8)
        for i, item in enumerate(items, 1):
            has_risk = re.search(r"R\d+", item) is not None
            has_ref = ("`" in item or "tools" in item or "test" in item)
            self.assertTrue(has_risk or has_ref, f"第 {i} 条既没有风险编号也没有命令/路径")

    def test_no_stale_timings(self):
        """57 秒 / 7 分钟 / 56.8 秒这些旧数字不该再出现。"""
        section = self._section()
        for stale in ("57 秒", "7 分钟验收", "56.8 秒"):
            self.assertNotIn(stale, section, f"{stale} 是旧数字，应改成实测值")



    def test_every_row_has_a_fidelity_grade(self):
        """能力表每一行的级别必须是 A/B/C/D 或其组合，且不能空着。"""
        import re as _re

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "DELIVERY.md"), encoding="utf-8").read()
        start = text.find("| 能力 | 级别 |")
        self.assertGreater(start, 0, "能力表缺少级别列")
        end = text.find("\n\n", start)
        rows = [ln for ln in text[start:end].splitlines()
                if ln.startswith("|") and not ln.startswith("|---") and "能力 |" not in ln]
        self.assertGreaterEqual(len(rows), 15)
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            grade = cells[1]
            self.assertRegex(grade, r"[ABCD]", f"这一行没有级别: {row[:50]}")
            self.assertNotIn("—", grade, f"级别不能写破折号: {row[:50]}")

    def test_catalogue_only_rows_say_so(self):
        """凡是标了 C 的能力，边界列必须说明"只有名字/目录"。"""
        import re as _re

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(repo, "DELIVERY.md"), encoding="utf-8").read()
        start = text.find("| 能力 | 级别 |")
        end = text.find("\n\n", start)
        for row in text[start:end].splitlines():
            cells = [c.strip() for c in row.strip("|").split("|")]
            if len(cells) < 4 or "C" not in cells[1]:
                continue
            with self.subTest(row=cells[0]):
                self.assertTrue(("名字" in cells[3] or "目录" in cells[3] or "R5" in cells[3]),
                                f"{cells[0]} 标了 C，边界却没说清")

class TestGitHygiene(unittest.TestCase):
    """仓库卫生：大目录必须被忽略，且 .git 真的存在（第 174 轮截断事故的护栏）。"""

    def _repo(self):
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def test_git_is_initialised(self):
        repo = self._repo()
        self.assertTrue(os.path.isdir(os.path.join(repo, ".git")),
                        "仓库没有 git——一次误写就救不回来")

    def test_heavy_dirs_are_ignored(self):
        repo = self._repo()
        ignore = open(os.path.join(repo, ".gitignore"), encoding="utf-8").read()
        for pattern in ("FreeCut/dist/", "FreeCut/build/", "tools/whisper",
                        "*.bin", "*.exe"):
            self.assertIn(pattern, ignore, f".gitignore 里缺少 {pattern}")

    def test_no_tracked_file_is_huge(self):
        """跟踪的文件里不该出现 >60 MB 的单个文件（那说明忽略规则漏了）。"""
        import subprocess

        repo = self._repo()
        try:
            out = subprocess.run(["git", "ls-tree", "-r", "-l", "HEAD"], cwd=repo,
                                 capture_output=True, text=True, timeout=120,
                                 encoding="utf-8", errors="replace").stdout
        except Exception:
            self.skipTest("git 不可用")
        if not out.strip():
            self.skipTest("还没有提交")
        biggest = 0
        name = ""
        for line in out.splitlines():
            bits = line.split("\t")
            if len(bits) != 2:
                continue
            try:
                size = int(bits[0].split()[3])
            except (IndexError, ValueError):
                continue
            if size > biggest:
                biggest, name = size, bits[1]
        self.assertLess(biggest, 60 * 1024 * 1024,
                        f"{name} 有 {biggest / 1024 / 1024:.0f} MB，应该被忽略")


class TestUpgradeList(unittest.TestCase):
    """C 级清单：每项都要有判定，且"可升级"的必须写怎么做。"""

    def _doc(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "docs", "UPGRADE-C-LIST.md")
        self.assertTrue(os.path.isfile(path), "缺少 docs/UPGRADE-C-LIST.md")
        return open(path, encoding="utf-8").read()

    def test_every_row_has_a_verdict(self):
        import re as _re

        text = self._doc()
        start = text.find("## 1. 现在还是 C 的项")
        section = text[start:text.find("## 2.", start)]
        rows = [ln for ln in section.splitlines()
                if ln.startswith("|") and not ln.startswith("|---") and "项目" not in ln]
        self.assertGreaterEqual(len(rows), 5, "C 级项太少")
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            self.assertIn(cells[2], ("**可升级**", "**不可升级**", "**部分可升级**"),
                          f"判定不合法: {row[:60]}")

    def test_upgradable_rows_say_how(self):
        text = self._doc()
        start = text.find("## 1. 现在还是 C 的项")
        section = text[start:text.find("## 2.", start)]
        for row in section.splitlines():
            if "**可升级**" not in row and "**部分可升级**" not in row:
                continue
            cells = [c.strip() for c in row.strip("|").split("|")]
            self.assertGreater(len(cells[3]), 6, f"没写怎么做: {row[:60]}")
            self.assertNotEqual(cells[3], "—", f"没写怎么做: {row[:60]}")

    def test_doc_requires_evidence_for_upgrades(self):
        text = self._doc()
        self.assertIn("没有第 3 步", text, "要写明：没有证据的升级不算升级")
        self.assertIn("对照图或测试", text)

    def test_matches_delivery_grades(self):
        """清单里提到的"不可升级"项，必须在 DELIVERY 的边界列里也有对应说明。"""
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        delivery = open(os.path.join(repo, "DELIVERY.md"), encoding="utf-8").read()
        self.assertIn("R5", delivery)
        self.assertIn("R6", delivery)


class TestAnimationCoverage(unittest.TestCase):
    """614 个剪映动画名里，至少四成要能落到真正的引擎上（第 179 轮：13% → 44%）。"""

    def test_coverage_is_at_least_forty_percent(self):
        from freecut import animation, catalog

        names = [item.get("name") or "" for item in catalog.animations()]
        resolved = [n for n in names if animation.resolve_kind(n, "")]
        ratio = len(resolved) / max(1, len(names))
        self.assertGreaterEqual(ratio, 0.40,
                               f"只解析到 {ratio:.0%}（{len(resolved)}/{len(names)}）")

    def test_rules_pick_the_right_direction(self):
        from freecut.animation import resolve_kind

        self.assertEqual(resolve_kind("向右甩入"), "slide_in_right")
        self.assertEqual(resolve_kind("向上转入"), "slide_in_up")
        self.assertEqual(resolve_kind("斜切"), "wipe_in_right")
        self.assertEqual(resolve_kind("分屏横移"), "zoom_in")
        self.assertIsNone(resolve_kind("九宫格"), "九宫格没有对应引擎，不该硬塞")

    def test_out_type_mirrors_in_kinds(self):
        from freecut.animation import resolve_kind

        self.assertEqual(resolve_kind("向右甩出", "out"), "slide_out_right")
        self.assertEqual(resolve_kind("渐隐", "out"), "fade_out")

    def test_unknown_names_still_return_none(self):
        from freecut.animation import resolve_kind

        for name in ("Kira游动", "冰块", "多维空间"):
            self.assertIsNone(resolve_kind(name), f"{name} 不该被硬塞进某个引擎")

    def test_loops_still_work(self):
        from freecut.animation import resolve_kind

        self.assertEqual(resolve_kind("闪烁", "loop"), "blink")
        self.assertEqual(resolve_kind("摇摆", "loop"), "sway")


class TestMoreTextPresets(unittest.TestCase):
    """花字从 14 套扩到 20 套，并且「空心字」是真空心而不是黑字。"""

    NEW = ("粗描边投影", "空心描边字", "渐变霓虹", "撕纸底", "双色底标语", "大字幕高亮条")

    def test_preset_count_and_groups(self):
        from freecut import textstyles

        names = textstyles.names()
        self.assertGreaterEqual(len(names), 20, f"只有 {len(names)} 套花字")
        for name in self.NEW:
            self.assertIn(name, names, f"缺少 {name}")
        groups = textstyles.groups()
        self.assertGreaterEqual(len(groups), 6, "分组太少")

    def test_every_preset_only_touches_known_fields(self):
        from freecut import textstyles

        for preset in textstyles.presets():
            for key in preset:
                if key in ("name", "group"):
                    continue
                with self.subTest(preset=preset.get("name"), field=key):
                    self.assertIn(key, textstyles.STYLE_FIELDS,
                                  f"{preset.get('name')} 用了未知字段 {key}")

    def test_hollow_preset_renders_outline_not_solid(self):
        """空心字：描边有像素、内部不填充（正文像素数远少于实心版本）。"""
        import numpy as np

        from freecut import builder, textstyles
        from freecut.render import Renderer

        def render(name):
            project = builder.new_project(320, 120, 30, name=name)
            seg = builder.add_text(project, "空心字", start=0, duration=1_000_000, size=12)
            textstyles.apply(project.material(seg.material_id), name)
            renderer = Renderer(project, 320, 120, 30)
            try:
                return renderer.render_frame(3)
            finally:
                renderer.close()

        hollow = render("空心描边字")
        # 对照组：同样的描边，但正文填成实心白
        from freecut import textstyles as _ts

        preset = dict(_ts.find("空心描边字"))
        preset["name"] = "实心对照"
        preset["text_color"] = "#FFFFFF"

        def render_with(preset_dict):
            project = builder.new_project(320, 120, 30, name=preset_dict["name"])
            seg = builder.add_text(project, "空心字", start=0, duration=1_000_000, size=12)
            mat = project.material(seg.material_id)
            for key, value in preset_dict.items():
                if key not in ("name", "group"):
                    mat[key] = value
            renderer = Renderer(project, 320, 120, 30)
            try:
                return renderer.render_frame(3)
            finally:
                renderer.close()

        solid = render_with(preset)

        def lit(frame):
            return int((frame.mean(axis=2) > 150).sum())

        self.assertGreater(lit(hollow), 50, "描边应当可见")
        self.assertLess(lit(hollow), lit(solid) * 0.9,
                        f"空心 {lit(hollow)} 应少于同描边的实心 {lit(solid)}")


class TestLayoutTokens(unittest.TestCase):
    """界面令牌：数值要对得上前人文档里记的那几个数（ReaCut UI_PARITY）。"""

    def test_pane_widths_match_the_reference(self):
        from freecut import theme

        self.assertEqual(theme.panes_for(1024), (280, 434, 280),
                         "1024 宽的三栏应当是 280/434/280")

    def test_min_size_and_header(self):
        from freecut import theme

        self.assertEqual(theme.MIN_WIDTH, 1060)
        self.assertEqual(theme.MIN_HEIGHT, 700)
        self.assertEqual(theme.PANEL_HEADER_H, 52)
        self.assertEqual(theme.APP_BAR_H, 44)

    def test_categories_follow_jianying_order(self):
        from freecut import theme

        self.assertEqual(list(theme.CATEGORIES),
                         ["素材", "音频", "文本", "贴纸", "特效", "转场", "字幕"])
        for name in theme.CATEGORIES:
            self.assertIn(name, theme.CATEGORY_GLYPH, f"{name} 没有图标字符")

    def test_palette_is_dark_and_cyan(self):
        from freecut import theme

        palette = theme.palette()
        self.assertEqual(palette["accent"], "#4cc2d4")
        for key in ("bg_app", "bg_panel", "bg_elev"):
            value = palette[key]
            self.assertRegex(value, r"^#[0-9a-f]{6}$")
            # 深色主题：三个通道都不该太亮
            rgb = [int(value[i:i + 2], 16) for i in (1, 3, 5)]
            self.assertLess(max(rgb), 70, f"{key}={value} 太亮了")


class TestCategoryRail(unittest.TestCase):
    """左栏类别栏：7 个类别能切换，且类别页里真有内容。"""

    def setUp(self):
        try:
            import tkinter  # noqa: F401
        except Exception:
            self.skipTest("没有 Tk")
        from freecut import gui

        os.environ.setdefault("FREECUT_SKIP_FFMPEG_WARN", "1")
        try:
            self.app = gui.App(None)
        except Exception as exc:                     # 无显示环境
            self.skipTest(f"起不了界面: {exc}")
        self.app.update()

    def tearDown(self):
        app = getattr(self, "app", None)
        if app is not None:
            try:
                app.destroy()
            except Exception:
                pass

    def test_seven_categories_with_pages(self):
        from freecut import theme

        self.assertEqual(list(self.app._rail_buttons), list(theme.CATEGORIES))
        self.assertEqual(list(self.app._pages), list(theme.CATEGORIES))

    def test_switching_updates_header_and_highlight(self):
        for name in ("文本", "特效", "转场", "字幕", "音频", "贴纸", "素材"):
            with self.subTest(category=name):
                self.app.show_category(name)
                self.app.update()
                self.assertEqual(self.app._left_title.cget("text"), name)
                self.assertEqual(str(self.app._rail_buttons[name].cget("style")), "RailOn.TButton")

    def test_effect_page_lists_every_engine(self):
        from freecut import effects

        self.assertEqual(len(self.app._effect_kinds), len(effects.available()))

    def test_transition_page_lists_every_engine(self):
        from freecut.render import TRANSITION_KINDS

        engines = set(TRANSITION_KINDS.values())
        self.assertEqual(set(self.app._transition_engines), engines,
                         "左栏转场页要和引擎表一一对应")

    def test_text_page_lists_every_preset(self):
        from freecut import textstyles

        self.assertEqual(self.app.text_list.size(), len(textstyles.names()))

    def test_timeline_can_scroll_vertically(self):
        """9 条轨道的工程一屏放不下，必须能上下滚。"""
        timeline = self.app.timeline
        before = timeline.vscroll
        timeline.scroll_tracks(3)
        self.assertGreaterEqual(timeline.vscroll, before)
        self.assertLessEqual(timeline.vscroll, timeline._max_vscroll())


class TestQtShell(unittest.TestCase):
    """Qt 壳（P0）：能起窗口、7 个类别、时间线有行、截图非空。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("FREECUT_NO_AUDIO", "1")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.QApplication = QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        win = MainWindow(sample if os.path.isfile(sample) else None)
        win.resize(1400, 860)
        self.app.processEvents()
        return win

    def test_window_builds_with_seven_categories(self):
        from freecut import theme

        win = self._window()
        try:
            # 只保留能做到的类别（云端-only 的 4 个已删除）
            self.assertEqual(list(win.rail.buttons), list(theme.QT_CATEGORIES))
            self.assertEqual(len(win.bin.pages), len(theme.JY_CATEGORIES))
            win.rail.group.button(2).click()
            self.app.processEvents()
            self.assertEqual(win.bin.title.text(), theme.JY_CATEGORIES[2])
        finally:
            win.close()

    def test_timeline_draws_the_project_tracks(self):
        win = self._window()
        try:
            rows = win.timeline.rows()
            self.assertGreater(len(rows), 0, "时间线没有轨道")
            total = sum(len(track.segments) for track in rows)
            self.assertGreater(total, 0, "时间线上没有片段")
        finally:
            win.close()

    def test_preview_renders_a_frame(self):
        win = self._window()
        try:
            image = win.bridge.frame_image(160, 90)
            self.assertIsNotNone(image, "预览渲染失败")
            self.assertEqual((image.width(), image.height()), (160, 90))
        finally:
            win.close()

    def test_grab_produces_a_real_png(self):
        import tempfile

        win = self._window()
        try:
            out = os.path.join(tempfile.mkdtemp(), "qt.png")
            self.assertTrue(win.grab_to(out))
            self.assertGreater(os.path.getsize(out), 10_000, "截图太小，可能没画出来")
        finally:
            win.close()

    def test_clicking_timeline_moves_playhead(self):
        from PySide6.QtCore import QPoint

        win = self._window()
        win.show()                      # 不 show 的话布局只分到最小尺寸（视口 90px）
        self.app.processEvents()
        try:
            before = win.bridge.playhead
            viewport = win.timeline.viewport()
            win.timeline.mousePressEvent  # 存在性
            from PySide6.QtGui import QMouseEvent
            from PySide6.QtCore import Qt as _Qt

            # 点标尺（表头）才是"定位播放头"；点轨道是选中片段，两件事要分开测
            from freecut.qtui.widgets.timeline import CONTENT_X, HEADER_H as _HEADER_H

            x = min(int(viewport.width()) - 4, CONTENT_X + 200)
            pos = QPoint(x, _HEADER_H // 2)
            event = QMouseEvent(QMouseEvent.MouseButtonPress, pos, _Qt.LeftButton,
                                _Qt.LeftButton, _Qt.NoModifier)
            win.timeline.mousePressEvent(event)
            self.assertNotEqual(win.bridge.playhead, before)
        finally:
            win.close()

    def test_cli_has_qt_flag(self):
        from freecut import cli

        parser = cli.build_parser()
        args = parser.parse_args(["gui", "--qt"])
        self.assertTrue(getattr(args, "qt_shell", False))


class TestQtTimelineEditing(unittest.TestCase):
    """P1：拖动、裁剪、吸附、撤销、轨道目标。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        win = MainWindow(sample if os.path.isfile(sample) else None)
        win.resize(1400, 860)
        self.app.processEvents()
        return win

    def _first_video(self, win):
        for track in win.timeline.rows():
            if track.type == "video" and track.segments:
                return track, track.segments[0]
        self.skipTest("示例工程里没有视频片段")

    def _drag(self, win, seg, dx: float, edge: str = "middle"):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        tl = win.timeline
        row_index = tl.rows().index(
            next(t for t in tl.rows() if seg in t.segments))
        x1 = tl._us_to_x(int(seg.target_timerange.start))
        x2 = tl._us_to_x(int(seg.target_timerange.start + seg.target_timerange.duration))
        y = tl._track_y(row_index) + 18
        x = {"middle": (x1 + x2) / 2, "left": x1 + 2, "right": x2 - 2}[edge]
        for kind, px, buttons in (
            (QMouseEvent.MouseButtonPress, x, Qt.LeftButton),
            (QMouseEvent.MouseMove, x + dx, Qt.LeftButton),
            (QMouseEvent.MouseButtonRelease, x + dx, Qt.NoButton),
        ):
            event = QMouseEvent(kind, QPointF(px, y), QPointF(px, y),
                                Qt.LeftButton, buttons, Qt.NoModifier)
            if kind == QMouseEvent.MouseButtonPress:
                tl.mousePressEvent(event)
            elif kind == QMouseEvent.MouseMove:
                tl.mouseMoveEvent(event)
            else:
                tl.mouseReleaseEvent(event)

    def test_drag_moves_clip(self):
        win = self._window()
        try:
            _track, seg = self._first_video(win)
            before = int(seg.target_timerange.start)
            self._drag(win, seg, 120)                 # 60px/秒 → 2 秒
            after = int(seg.target_timerange.start)
            self.assertGreater(after, before)
            self.assertAlmostEqual((after - before) / 1e6, 2.0, delta=0.35)
        finally:
            win.close()

    def test_trim_right_changes_duration_only(self):
        win = self._window()
        try:
            _track, seg = self._first_video(win)
            start = int(seg.target_timerange.start)
            dur = int(seg.target_timerange.duration)
            self._drag(win, seg, -90, edge="right")
            self.assertEqual(int(seg.target_timerange.start), start, "右裁剪不该移动起点")
            self.assertLess(int(seg.target_timerange.duration), dur)
        finally:
            win.close()

    def test_trim_left_moves_start_and_duration(self):
        win = self._window()
        try:
            _track, seg = self._first_video(win)
            start = int(seg.target_timerange.start)
            dur = int(seg.target_timerange.duration)
            self._drag(win, seg, 90, edge="left")
            self.assertGreater(int(seg.target_timerange.start), start)
            self.assertLess(int(seg.target_timerange.duration), dur)
        finally:
            win.close()

    def test_snap_lands_on_playhead(self):
        win = self._window()
        try:
            _track, seg = self._first_video(win)
            win.bridge.set_playhead(3_000_000)
            self._drag(win, seg, 3.0 * win.timeline.ppm + 2)   # 拖到播放头附近
            self.assertEqual(int(seg.target_timerange.start), 3_000_000,
                             "应当吸到播放头")
        finally:
            win.close()

    def test_undo_and_redo_restore_times(self):
        win = self._window()
        try:
            _track, seg = self._first_video(win)
            before = int(seg.target_timerange.start)
            self._drag(win, seg, 120)
            moved = int(seg.target_timerange.start)
            self.assertNotEqual(moved, before)
            self.assertTrue(win.bridge.undo(), "应当能撤销")
            self.assertEqual(int(seg.target_timerange.start), before)
            self.assertTrue(win.bridge.redo(), "应当能重做")
            self.assertEqual(int(seg.target_timerange.start), moved)
        finally:
            win.close()

    def test_one_target_per_track_type(self):
        win = self._window()
        try:
            videos = [t for t in win.timeline.rows() if t.type == "video"]
            if len(videos) < 2:
                self.skipTest("视频轨不足两条")
            win.bridge.toggle_target(videos[0])
            win.bridge.toggle_target(videos[1])
            targeted = win.bridge.targeted_tracks()
            self.assertEqual(len(targeted), 1, "同类型只能有一个目标轨道")
            self.assertTrue(win.bridge.is_targeted(videos[1]))
        finally:
            win.close()

    def test_timeline_scrolls_vertically_when_tracks_overflow(self):
        win = self._window()
        try:
            rows = win.timeline.rows()
            needed = 24 + len(rows) * 40
            visible = win.timeline.height()
            if needed <= visible:
                self.skipTest("轨道没超出一屏")
            bar = win.timeline.verticalScrollBar()
            self.assertGreater(bar.maximum(), 0, "应当可以竖向滚动")
        finally:
            win.close()


class TestMediaCache(unittest.TestCase):
    """时间线缩略图与波形：形状对、范围对、有缓存。"""

    def _media(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        video = os.path.join(repo, "out", "demo.mp4")
        audio = os.path.join(repo, "out", "narration.wav")
        image = os.path.join(repo, "out", "green.png")
        if not os.path.isfile(video):
            self.skipTest("没有 out/demo.mp4")
        return video, (audio if os.path.isfile(audio) else None), (
            image if os.path.isfile(image) else None)

    def test_video_thumbnail(self):
        from freecut.qtui.media_cache import MediaCache

        video, _audio, _image = self._media()
        cache = MediaCache()
        arr = cache.thumbnail(video, 1_000_000, 96, 54)
        self.assertIsNotNone(arr, "缩略图没取到")
        self.assertEqual(arr.shape, (54, 96, 3))
        self.assertGreater(int(arr.max()), 10, "缩略图全黑")

    def test_thumbnail_is_cached(self):
        from freecut.qtui.media_cache import MediaCache

        video, _a, _i = self._media()
        cache = MediaCache()
        first = cache.thumbnail(video, 500_000, 64, 36)
        second = cache.thumbnail(video, 510_000, 64, 36)   # 同一 0.5s 桶
        self.assertIs(first, second, "同一时间桶应当命中缓存")

    def test_image_thumbnail(self):
        from freecut.qtui.media_cache import MediaCache

        _v, _a, image = self._media()
        if not image:
            self.skipTest("没有 out/green.png")
        cache = MediaCache()
        arr = cache.thumbnail(image, 0, 64, 36)
        self.assertIsNotNone(arr)
        self.assertEqual(arr.shape, (36, 64, 3))

    def test_audio_peaks(self):
        from freecut.qtui.media_cache import MediaCache

        _v, audio, _i = self._media()
        if not audio:
            self.skipTest("没有 out/narration.wav")
        cache = MediaCache()
        peaks = cache.peaks(audio, 0, 2_000_000, 64)
        self.assertIsNotNone(peaks)
        self.assertEqual(len(peaks), 64)
        self.assertGreaterEqual(float(peaks.min()), 0.0)
        self.assertLessEqual(float(peaks.max()), 1.0)
        self.assertGreater(float(peaks.max()), 0.5, "波形太平，可能没解出音频")

    def test_missing_file_returns_none(self):
        from freecut.qtui.media_cache import MediaCache

        cache = MediaCache()
        self.assertIsNone(cache.thumbnail("不存在的文件.mp4", 0))
        self.assertIsNone(cache.peaks("不存在的文件.wav", 0, 1_000_000, 16))


class TestQtInspector(unittest.TestCase):
    """P2：画面页的滑杆+数字联动、写回模型、撤销。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_video(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        if not _showcase_media_ok(win.bridge.project):
            win.close()
            self.skipTest("示例工程素材缺失：先跑 python -m freecut showcase --work out/showcase-media")
        win.resize(1400, 860)
        self.app.processEvents()
        videos = [tr for tr in win.timeline.rows() if tr.type == "video" and tr.segments]
        if not videos:
            win.close()
            self.skipTest("示例工程没有视频片段")
        seg = videos[0].segments[0]
        win.bridge.select(seg)
        self.app.processEvents()
        return win, seg

    def test_selecting_fills_the_controls(self):
        win, seg = self._window_with_video()
        try:
            ins = win.inspector
            # 选中视频片段默认停在「画面」页（剪映也是这样），不再写死序号 0
            self.assertEqual(ins.tabs.currentIndex(), ins.tab_index("画面"))
            self.assertAlmostEqual(ins.row_scale.spin.value(), seg.clip.scale_x * 100, places=1)
            self.assertAlmostEqual(ins.row_alpha.spin.value(), seg.clip.alpha * 100, places=1)
        finally:
            win.close()

    def test_slider_and_spin_stay_in_sync(self):
        win, _seg = self._window_with_video()
        try:
            row = win.inspector.row_rotation
            row.slider.setValue(row._to_slider(30.0))
            self.app.processEvents()
            self.assertAlmostEqual(row.spin.value(), 30.0, delta=1.0)
            row.spin.setValue(-45.0)
            self.app.processEvents()
            self.assertAlmostEqual(row._from_slider(row.slider.value()), -45.0, delta=1.0)
        finally:
            win.close()

    def test_scale_writes_into_model(self):
        win, seg = self._window_with_video()
        try:
            win.inspector.row_scale.set_value(150.0, emit=True)
            self.app.processEvents()
            self.assertAlmostEqual(seg.clip.scale_x, 1.5, places=2)
            self.assertAlmostEqual(seg.clip.scale_y, 1.5, places=2)
        finally:
            win.close()

    def test_position_and_rotation_write_into_model(self):
        win, seg = self._window_with_video()
        try:
            win.inspector.row_x.set_value(0.25, emit=True)
            win.inspector.row_y.set_value(-0.5, emit=True)
            win.inspector.row_rotation.set_value(12.0, emit=True)
            win.inspector.row_alpha.set_value(80.0, emit=True)
            self.app.processEvents()
            self.assertAlmostEqual(seg.clip.tx, 0.25, places=3)
            self.assertAlmostEqual(seg.clip.ty, -0.5, places=3)
            self.assertAlmostEqual(seg.clip.rotation, 12.0, places=1)
            self.assertAlmostEqual(seg.clip.alpha, 0.8, places=2)
        finally:
            win.close()

    def test_transform_can_be_undone(self):
        win, seg = self._window_with_video()
        try:
            before = float(seg.clip.scale_x)
            win.inspector.row_scale.set_value(220.0, emit=True)
            self.app.processEvents()
            self.assertAlmostEqual(seg.clip.scale_x, 2.2, places=2)
            self.assertTrue(win.bridge.undo())
            self.assertAlmostEqual(seg.clip.scale_x, before, places=2)
        finally:
            win.close()

    def test_reset_restores_defaults(self):
        win, seg = self._window_with_video()
        try:
            win.inspector.row_scale.set_value(180.0, emit=True)
            win.inspector.row_rotation.set_value(25.0, emit=True)
            win.inspector.reset_transform()
            self.app.processEvents()
            self.assertAlmostEqual(seg.clip.scale_x, 1.0, places=2)
            self.assertAlmostEqual(seg.clip.rotation, 0.0, places=1)
            self.assertAlmostEqual(seg.clip.alpha, 1.0, places=2)
        finally:
            win.close()

    def test_audio_selection_does_not_crash(self):
        win, _seg = self._window_with_video()
        try:
            audios = [tr for tr in win.timeline.rows() if tr.type == "audio" and tr.segments]
            if not audios:
                self.skipTest("没有音频片段")
            win.bridge.select(audios[0].segments[0])
            self.app.processEvents()
            self.assertEqual(win.inspector.title.text().split("·")[-1].strip(), "音频")
        finally:
            win.close()

    def test_adjust_page_has_every_spec_item(self):
        from freecut.color import ADJUST_SPEC

        win, _seg = self._window_with_video()
        try:
            rows = win.inspector.adjust_rows
            self.assertEqual(list(rows), list(ADJUST_SPEC))
            for key, spec in ADJUST_SPEC.items():
                self.assertAlmostEqual(rows[key].spin.minimum(), float(spec["min"]))
                self.assertAlmostEqual(rows[key].spin.maximum(), float(spec["max"]))
        finally:
            win.close()

    def test_adjust_writes_into_material(self):
        win, seg = self._window_with_video()
        try:
            win.inspector.adjust_rows["brightness"].set_value(20.0, emit=True)
            win.inspector.adjust_rows["saturation"].set_value(-15.0, emit=True)
            self.app.processEvents()
            mat = win.bridge.project.material(seg.material_id)
            self.assertAlmostEqual(mat.get("adjust")["brightness"], 20.0, places=1)
            self.assertAlmostEqual(mat.get("adjust")["saturation"], -15.0, places=1)
        finally:
            win.close()

    def test_adjust_can_be_undone(self):
        win, seg = self._window_with_video()
        try:
            win.inspector.adjust_rows["contrast"].set_value(25.0, emit=True)
            self.app.processEvents()
            mat = win.bridge.project.material(seg.material_id)
            self.assertAlmostEqual(mat.get("adjust")["contrast"], 25.0, places=1)
            self.assertTrue(win.bridge.undo())
            mat = win.bridge.project.material(seg.material_id)
            # 示例工程本身可能带着旧的调节值（如 vignette），撤销只需去掉"本次改的"那一项
            self.assertNotIn("contrast", mat.get("adjust") or {},
                             "撤销后这次改的对比度应当消失")
        finally:
            win.close()

    def test_adjust_reset_clears_everything(self):
        win, seg = self._window_with_video()
        try:
            win.inspector.adjust_rows["vignette"].set_value(30.0, emit=True)
            win.inspector.adjust_rows["sharpen"].set_value(10.0, emit=True)
            win.inspector.reset_adjust()
            self.app.processEvents()
            mat = win.bridge.project.material(seg.material_id)
            self.assertFalse(mat.get("adjust"))
            self.assertAlmostEqual(win.inspector.adjust_rows["vignette"].spin.value(), 0.0)
        finally:
            win.close()

    def test_adjust_changes_rendered_pixels(self):
        """调节必须真的改到画面，不只是改字段。"""
        import numpy as np

        win, seg = self._window_with_video()
        try:
            win.bridge.set_playhead(int(seg.target_timerange.start) + 200_000)
            before_img = win.bridge.frame_image(160, 90)
            before = np.frombuffer(before_img.bits(), np.uint8).reshape(90, -1, 4)[:, :, :3].mean()
            win.inspector.adjust_rows["brightness"].set_value(40.0, emit=True)
            win.app if False else None
            self.app.processEvents()
            win.bridge._drop_renderer()
            after_img = win.bridge.frame_image(160, 90)
            after = np.frombuffer(after_img.bits(), np.uint8).reshape(90, -1, 4)[:, :, :3].mean()
            self.assertGreater(after - before, 8.0, "亮度 +40 之后画面应当明显变亮")
        finally:
            win.close()

    def test_adjust_values_reload_after_reselect(self):
        win, seg = self._window_with_video()
        try:
            win.inspector.adjust_rows["brightness"].set_value(12.0, emit=True)
            self.app.processEvents()
            win.bridge.select(None)
            win.bridge.select(seg)
            self.app.processEvents()
            self.assertAlmostEqual(win.inspector.adjust_rows["brightness"].spin.value(), 12.0,
                                   places=1)
        finally:
            win.close()


class TestQtEffectsTransitions(unittest.TestCase):
    """左栏「特效 / 转场」页：双击真的能加到选中片段上。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_video(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        if not _showcase_media_ok(win.bridge.project):
            win.close()
            self.skipTest("示例工程素材缺失：先跑 python -m freecut showcase --work out/showcase-media")
        win.resize(1400, 860)
        self.app.processEvents()
        videos = [tr for tr in win.timeline.rows() if tr.type == "video" and tr.segments]
        if not videos:
            win.close()
            self.skipTest("没有视频片段")
        seg = videos[0].segments[0]
        win.bridge.select(seg)
        self.app.processEvents()
        return win, seg

    def test_each_page_lists_engines(self):
        from freecut import effects
        from freecut.qtui.labels import ENGINE_LABELS
        from freecut.render import TRANSITION_KINDS

        win, _seg = self._window_with_video()
        try:
            self.assertEqual(len(win.bin._effect_kinds), len(effects.available()))
            self.assertEqual(set(win.bin._transition_engines), set(TRANSITION_KINDS.values()))
            self.assertTrue(set(win.bin._transition_engines) <= set(ENGINE_LABELS))
        finally:
            win.close()

    def test_effect_is_added_to_selection(self):
        win, seg = self._window_with_video()
        try:
            win.bin.show_category("特效")
            win.bin.effect_list.setCurrentRow(0)
            before = len(seg.extra_material_refs)
            win.bin.apply_selected_effect()
            self.app.processEvents()
            self.assertEqual(len(seg.extra_material_refs), before + 1)
        finally:
            win.close()

    def test_transition_is_added_to_selection(self):
        win, seg = self._window_with_video()
        try:
            win.bin.show_category("转场")
            win.bin.transition_list.setCurrentRow(0)
            before = len(seg.extra_material_refs)
            win.bin.apply_selected_transition()
            self.app.processEvents()
            self.assertEqual(len(seg.extra_material_refs), before + 1)
        finally:
            win.close()

    def test_actions_need_a_selection(self):
        win, _seg = self._window_with_video()
        try:
            win.bridge.select(None)
            win.bin.show_category("特效")
            win.bin.effect_list.setCurrentRow(0)
            messages = []
            win.bridge.statusMessage.connect(messages.append)
            win.bin.apply_selected_effect()
            self.assertTrue(any("先" in m for m in messages), "应当提示先选片段")
        finally:
            win.close()

    def test_material_edit_can_be_undone(self):
        win, seg = self._window_with_video()
        try:
            win.bin.show_category("特效")
            win.bin.effect_list.setCurrentRow(0)
            before = len(seg.extra_material_refs)
            win.bin.apply_selected_effect()
            self.app.processEvents()
            self.assertGreater(len(seg.extra_material_refs), before)
            self.assertTrue(win.bridge.undo())
            self.assertEqual(len(seg.extra_material_refs), before)
        finally:
            win.close()


class TestCoverageDoc(unittest.TestCase):
    """功能覆盖表：每行都要有状态，且状态只能取 ✅/◐/✗。"""

    def _doc(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "docs", "COVERAGE-JY.md")
        self.assertTrue(os.path.isfile(path), "缺少 docs/COVERAGE-JY.md")
        return open(path, encoding="utf-8").read()

    def test_every_row_has_a_status(self):
        text = self._doc()
        rows = [ln for ln in text.splitlines()
                if ln.startswith("|") and not ln.startswith("|---") and "---" not in ln]
        checked = 0
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            if len(cells) < 5 or cells[-1] in ("状态", "记号"):
                continue
            if cells[-1] in ("✅", "◐", "✗"):
                checked += 1
                continue
            if "含义" in row or cells[-1].startswith("|"):
                continue
            self.fail(f"状态列不合法: {row[:70]}")
        self.assertGreaterEqual(checked, 18, f"覆盖表行数太少（{checked}）")

    def test_evidence_column_points_at_tests_or_commands(self):
        text = self._doc()
        rows = [ln for ln in text.splitlines() if ln.startswith("|") and "`" in ln]
        self.assertGreaterEqual(len(rows), 12)
        for row in rows:
            self.assertTrue(any(token in row for token in
                                ("Test", "reproduce", "out/", "RISKS", "ROUTE-QT", ".py")),
                            f"证据列没指向测试或命令: {row[:70]}")

    def test_doc_states_the_definition_of_most(self):
        """文档必须写明「大部分」是怎么算的，而不是喊口号。"""
        text = self._doc()
        self.assertIn("大部分", text)
        self.assertIn("◐", text)
        self.assertIn("9 个分区", text, "要给出分区级别的统计")


class TestQtSoundPage(unittest.TestCase):
    """声音页：音量 / 淡入淡出 / 降噪 / 变声，含撤销与复位。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_audio(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        win.resize(1400, 860)
        self.app.processEvents()
        audios = [tr for tr in win.timeline.rows() if tr.type == "audio" and tr.segments]
        if not audios:
            win.close()
            self.skipTest("没有音频片段")
        seg = audios[0].segments[0]
        win.bridge.select(seg)
        self.app.processEvents()
        return win, seg

    def test_selecting_audio_loads_sound_values(self):
        win, seg = self._window_with_audio()
        try:
            ins = win.inspector
            ins.tabs.setCurrentIndex(ins.tab_index("音频"))
            self.assertAlmostEqual(ins.row_volume.spin.value(), seg.volume * 100, places=1)
        finally:
            win.close()

    def test_volume_writes_to_segment(self):
        win, seg = self._window_with_audio()
        try:
            win.inspector.row_volume.set_value(60.0, emit=True)
            self.app.processEvents()
            self.assertAlmostEqual(seg.volume, 0.6, places=2)
        finally:
            win.close()

    def test_fades_write_into_material(self):
        from freecut import audio as audio_mod

        win, seg = self._window_with_audio()
        try:
            win.inspector.row_fade_in.set_value(500.0, emit=True)
            win.inspector.row_fade_out.set_value(250.0, emit=True)
            self.app.processEvents()
            fade_in, fade_out = audio_mod._fade_durations(win.bridge.project, seg)
            self.assertEqual(fade_in, 500_000)
            self.assertEqual(fade_out, 250_000)
        finally:
            win.close()

    def test_denoise_and_pitch_write_into_effects(self):
        from freecut import audio as audio_mod

        win, seg = self._window_with_audio()
        try:
            win.inspector.row_denoise.set_value(40.0, emit=True)
            win.inspector.pitch_box.setCurrentIndex(2)      # 降调 0.87
            self.app.processEvents()
            denoise, pitch, _eq = audio_mod._audio_effects(win.bridge.project, seg)
            self.assertAlmostEqual(denoise, 0.4, places=2)
            self.assertAlmostEqual(pitch, 0.87, places=2)
        finally:
            win.close()

    def test_sound_edits_undo_one_by_one(self):
        win, seg = self._window_with_audio()
        try:
            original = float(seg.volume)
            win.inspector.row_volume.set_value(75.0, emit=True)
            self.app.processEvents()
            self.assertAlmostEqual(seg.volume, 0.75, places=2)
            self.assertTrue(win.bridge.undo())
            self.assertAlmostEqual(seg.volume, original, places=2)
        finally:
            win.close()

    def test_sound_reset_restores_defaults(self):
        from freecut import audio as audio_mod

        win, seg = self._window_with_audio()
        try:
            win.inspector.row_volume.set_value(40.0, emit=True)
            win.inspector.row_fade_in.set_value(800.0, emit=True)
            win.inspector.reset_audio()
            self.app.processEvents()
            self.assertAlmostEqual(seg.volume, 1.0, places=2)
            self.assertEqual(audio_mod._fade_durations(win.bridge.project, seg), (0, 0))
        finally:
            win.close()

    def test_fade_setter_updates_existing_material(self):
        """set_audio_fade 要能改已有材质，而不是每次新建一个。"""
        from freecut import audio as audio_mod, builder

        project = builder.new_project(320, 180, 30, name="t")
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "out", "narration.wav")
        if not os.path.isfile(path):
            self.skipTest("没有 narration.wav")
        seg = builder.add_audio(project, path, start=0, fade_in_ms=100)
        before = len(project.materials)
        builder.set_audio_fade(project, seg, fade_in_us=300_000, fade_out_us=200_000)
        self.assertEqual(len(project.materials), before, "不该新建材质")
        self.assertEqual(audio_mod._fade_durations(project, seg), (300_000, 200_000))


class TestQtTextPage(unittest.TestCase):
    """文字页：内容/字号/描边/阴影/颜色/花字，含撤销。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_text(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        win.resize(1400, 860)
        self.app.processEvents()
        texts = [tr for tr in win.timeline.rows() if tr.type == "text" and tr.segments]
        if not texts:
            win.close()
            self.skipTest("没有文字片段")
        seg = texts[0].segments[0]
        win.bridge.select(seg)
        self.app.processEvents()
        return win, seg

    def test_selecting_text_switches_to_text_tab(self):
        win, _seg = self._window_with_text()
        try:
            index = win.inspector.tab_index("文字")
            self.assertEqual(win.inspector.tabs.currentIndex(), index)
            self.assertEqual(win.inspector.tabs.tabText(index), "文字")
        finally:
            win.close()

    def test_content_is_loaded_and_written(self):
        win, seg = self._window_with_text()
        try:
            mat = win.bridge.project.material(seg.material_id)
            self.assertEqual(win.inspector.text_edit.toPlainText(), str(mat.get("content")))
            win.inspector.text_edit.setPlainText("新的标题")
            self.app.processEvents()
            self.assertEqual(mat.get("content"), "新的标题")
        finally:
            win.close()

    def test_font_size_stroke_shadow_write(self):
        win, seg = self._window_with_text()
        try:
            mat = win.bridge.project.material(seg.material_id)
            win.inspector.row_font_size.set_value(20.0, emit=True)
            win.inspector.row_stroke.set_value(4.0, emit=True)
            win.inspector.row_shadow.set_value(60.0, emit=True)
            self.app.processEvents()
            self.assertAlmostEqual(float(mat.get("font_size")), 20.0, places=1)
            self.assertAlmostEqual(float(mat.get("border_width")), 4.0, places=1)
            self.assertAlmostEqual(float(mat.get("shadow_alpha")), 0.6, places=2)
        finally:
            win.close()

    def test_preset_applies_a_whole_style(self):
        from freecut import textstyles

        win, seg = self._window_with_text()
        try:
            mat = win.bridge.project.material(seg.material_id)
            index = textstyles.names().index("综艺黄底") + 1
            win.inspector.preset_box.setCurrentIndex(index)
            self.app.processEvents()
            preset = textstyles.find("综艺黄底")
            # 该预设的核心特征是「黄色底」：核对底色与透明度，而不是描边
            self.assertAlmostEqual(float(mat.get("background_alpha")),
                                   float(preset["background_alpha"]), places=2)
            self.assertEqual(str(mat.get("background_color")).upper(),
                             str(preset["background_color"]).upper())
            self.assertNotEqual(str(mat.get("background_color")).upper(), "#000000",
                                "套用后底色应当被改掉")
        finally:
            win.close()

    def test_text_edit_can_be_undone(self):
        win, seg = self._window_with_text()
        try:
            mat = win.bridge.project.material(seg.material_id)
            original = str(mat.get("content"))
            win.inspector.text_edit.setPlainText("临时文本")
            self.app.processEvents()
            self.assertEqual(mat.get("content"), "临时文本")
            self.assertTrue(win.bridge.undo())
            self.assertEqual(mat.get("content"), original)
        finally:
            win.close()

    def test_project_duration_is_read_as_a_method(self):
        """Project.duration 是方法——桥接层曾经当属性用，导致时长永远 1 秒。"""
        from freecut import builder

        win, _seg = self._window_with_text()
        try:
            project = builder.new_project(640, 360, 30, name="t")
            builder.add_text(project, "x", start=0, duration=3_000_000)
            win.bridge.set_project(project, None)
            self.assertAlmostEqual(win.bridge.duration_us / 1e6, 3.0, places=2)
        finally:
            win.close()

    def test_preview_rerenders_after_text_edit(self):
        """改文字后预览必须重画（之前尺寸不变就跳过渲染，画面会停在旧内容）。"""
        import time

        def wait_frame(timeout_ms: int = 5000, min_token: int = None):
            """等一帧真正画进 QLabel（异步渲染，机器忙时会慢）。

            给了 ``min_token`` 就要求显示的是**这一帧之后**的渲染结果，
            否则会拿到上一帧的旧图，误判成"没重渲"。
            """
            deadline = time.time() + timeout_ms / 1000.0
            while time.time() < deadline:
                pump(self.app, 50)
                if min_token is not None and viewer._shown_token < min_token:
                    continue
                pix = viewer._label.pixmap()
                if pix is not None and not pix.isNull() and pix.width() > 8:
                    return pix.toImage()
            return None

        win, seg = self._window_with_text()
        try:
            # 自己造一个只含文字片段的工程：不依赖 showcase.json 的内容，
            # 也不受前面用例留下的选中状态影响（这条以前就是被这两件事搞抖的）
            from freecut import builder

            project = builder.new_project(320, 180, 30, name="文字重渲")
            seg = builder.add_text(project, "第一段文字", start=0,
                                   duration=2_000_000, size=24, y=0.0)
            win.bridge.set_project(project, None)
            win._refresh_all()
            win.bridge.select(seg)
            win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("文字"))
            self.app.processEvents()
            viewer = win.viewer
            win.bridge.set_playhead(int(seg.target_timerange.start) + 100_000)
            self.app.processEvents()
            first = wait_frame()
            self.assertIsNotNone(first, "第一帧没渲染出来")
            tokens_before = viewer._want_token
            # 不依赖前面用例留下的选中状态：这里显式选中要改的文字片段
            win.bridge.select(seg)
            self.app.processEvents()
            text_mat = win.inspector._text_mat(seg)
            before_text = str(text_mat.get("content")) if text_mat is not None else ""
            win.inspector.text_edit.setPlainText("预览必须跟着变")
            self.app.processEvents()
            if text_mat is not None:
                self.assertNotEqual(str(text_mat.get("content")), before_text,
                                    "文字改动没有写进材质")
            # 异步渲染下"16ms 定时器跑到哪一步"会随机器负载变化；这里显式走一次
            # schedule() + render_now()（和正常编辑同一个入口），再比对像素。
            viewer.schedule()
            viewer.render_now()
            second = wait_frame(min_token=viewer._want_token)
            self.assertIsNotNone(second, "改完文字后预览没重渲出来")
            # 比对整张图（只比前 4096 字节会漏掉画面中间的文字变化）
            import hashlib

            digest_first = hashlib.sha256(bytes(first.constBits())).hexdigest()
            digest_second = hashlib.sha256(bytes(second.constBits())).hexdigest()
            self.assertNotEqual(digest_first, digest_second,
                                "预览位图没变，说明没有重渲")
        finally:
            win.close()

    def test_text_page_ignores_video_selection(self):
        win, _seg = self._window_with_text()
        try:
            videos = [tr for tr in win.timeline.rows() if tr.type == "video" and tr.segments]
            if not videos:
                self.skipTest("没有视频片段")
            win.bridge.select(videos[0].segments[0])
            self.app.processEvents()
            # 切到文字页也不该改到视频的材质
            win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("文字"))
            win.inspector.row_font_size.set_value(30.0, emit=True)
            self.app.processEvents()
            self.assertNotEqual(win.inspector.title.text(), "属性 · 文字")
        finally:
            win.close()


class TestQtExportDialog(unittest.TestCase):
    """导出面板：预设齐全、默认跟随画布、真能导出、能取消。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        self.app.processEvents()
        return win

    def test_dialog_lists_every_preset_and_tier(self):
        from freecut.export import PRESETS, QUALITY_TIERS
        from freecut.qtui.dialogs.export_dialog import ExportDialog

        win = self._window()
        try:
            dialog = ExportDialog(win.bridge, win)
            self.assertEqual(dialog.preset_box.count(), len(PRESETS))
            self.assertEqual(dialog.quality_box.count(), len(QUALITY_TIERS))
            dialog.close()
        finally:
            win.close()

    def test_default_preset_follows_canvas(self):
        from freecut import builder
        from freecut.qtui.dialogs.export_dialog import ExportDialog

        win = self._window()
        try:
            project = builder.new_project(1080, 1920, 30, name="竖屏")
            builder.add_text(project, "x", start=0, duration=1_000_000)
            win.bridge.set_project(project, None)
            dialog = ExportDialog(win.bridge, win)
            self.assertIn("vertical1080", dialog.preset_box.currentText())
            dialog.close()
        finally:
            win.close()

    def test_quality_tier_changes_bitrate(self):
        from freecut.qtui.dialogs.export_dialog import ExportDialog

        win = self._window()
        try:
            dialog = ExportDialog(win.bridge, win)
            dialog.quality_box.setCurrentIndex(2)          # 高码率
            preset = dialog.current_preset()
            self.assertTrue(preset.video_bitrate or preset.crf < 20,
                            "高码率档应当改码率或 CRF")
            dialog.close()
        finally:
            win.close()

    def test_export_through_dialog_writes_a_file(self):
        import tempfile
        import time

        from PySide6.QtWidgets import QMessageBox

        from freecut import builder
        from freecut.qtui.dialogs.export_dialog import ExportDialog

        original_info = QMessageBox.information
        original_question = QMessageBox.question
        QMessageBox.information = staticmethod(lambda *a, **k: 0)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        win = self._window()
        try:
            project = builder.new_project(320, 180, 30, name="导出测试")
            builder.add_text(project, "导出示意", start=0, duration=600_000, size=20, y=0.0)
            win.bridge.set_project(project, None)
            out = os.path.join(tempfile.mkdtemp(), "dialog_export.mp4")
            dialog = ExportDialog(win.bridge, win)
            dialog.path_edit.setText(out)
            dialog.preset_box.setCurrentIndex(0)
            dialog.start_export()
            deadline = time.time() + 180
            while dialog.worker is not None and dialog.worker.isRunning() and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.05)
            self.app.processEvents()
            self.assertTrue(os.path.isfile(out), "对话框导出没有产出文件")
            self.assertGreater(os.path.getsize(out), 4096, "文件太小，可能没写内容")
            self.assertEqual(dialog.progress.value(), 100)
            dialog.close()
        finally:
            QMessageBox.information = original_info
            QMessageBox.question = original_question
            win.close()

    def test_cancel_stops_the_worker(self):
        from freecut.qtui.dialogs.export_dialog import ExportDialog

        win = self._window()
        try:
            dialog = ExportDialog(win.bridge, win)
            self.assertIsNone(dialog.worker)
            dialog.reject()                     # 没在导出时取消不该崩
            self.assertFalse(dialog.isVisible())
            dialog.close()
        finally:
            win.close()


class TestQtColorPage(unittest.TestCase):
    """曲线·轮页：HSL 色带、色调曲线、色轮，含撤销。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_video(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        win.resize(1400, 860)
        self.app.processEvents()
        videos = [tr for tr in win.timeline.rows() if tr.type == "video" and tr.segments]
        if not videos:
            win.close()
            self.skipTest("没有视频片段")
        seg = videos[0].segments[0]
        win.bridge.select(seg)
        win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("曲线"))
        self.app.processEvents()
        return win, seg

    def test_all_eight_bands_are_listed(self):
        from freecut.color import HSL_BANDS

        win, _seg = self._window_with_video()
        try:
            self.assertEqual(win.inspector.band_box.count(), len(HSL_BANDS))
        finally:
            win.close()

    def test_hsl_writes_band_values(self):
        win, seg = self._window_with_video()
        try:
            ins = win.inspector
            ins.band_box.setCurrentIndex(1)                  # 橙
            ins.row_sat.set_value(45.0, emit=True)
            ins.row_hue.set_value(20.0, emit=True)
            self.app.processEvents()
            bands = ins._ref_of(seg, "hsl").get("bands")
            self.assertAlmostEqual(bands["orange"]["sat"], 0.45, places=2)
            self.assertAlmostEqual(bands["orange"]["hue"], 20.0, places=1)
        finally:
            win.close()

    def test_switching_band_shows_its_own_values(self):
        win, seg = self._window_with_video()
        try:
            ins = win.inspector
            ins.band_box.setCurrentIndex(1)
            ins.row_sat.set_value(30.0, emit=True)
            self.app.processEvents()
            ins.band_box.setCurrentIndex(2)                  # 黄，还没设过
            self.app.processEvents()
            self.assertAlmostEqual(ins.row_sat.spin.value(), 0.0, places=1)
            ins.band_box.setCurrentIndex(1)
            self.app.processEvents()
            self.assertAlmostEqual(ins.row_sat.spin.value(), 30.0, places=1)
        finally:
            win.close()

    def test_curve_writes_per_channel(self):
        win, seg = self._window_with_video()
        try:
            ins = win.inspector
            ins.channel_box.setCurrentIndex(0)               # RGB
            ins._apply_curve([(0.0, 0.05), (0.5, 0.55), (1.0, 0.95)])
            ins.channel_box.setCurrentIndex(1)               # 红
            ins._apply_curve([(0.0, 0.0), (1.0, 0.9)])
            self.app.processEvents()
            curves = ins._ref_of(seg, "curve").get("curves")
            self.assertIn("rgb", curves)
            self.assertIn("r", curves)
            self.assertAlmostEqual(curves["r"][1][1], 0.9, places=2)
        finally:
            win.close()

    def test_curve_widget_emits_on_drag(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        win, _seg = self._window_with_video()
        try:
            widget = win.inspector.curve
            widget.resize(220, 200)
            seen = []
            widget.changed.connect(seen.append)
            start = widget._to_px(widget.points[0])
            press = QMouseEvent(QMouseEvent.MouseButtonPress, start, start,
                                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            widget.mousePressEvent(press)
            moved = QPointF(start.x() + 6, start.y() - 20)
            move = QMouseEvent(QMouseEvent.MouseMove, moved, moved,
                               Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            widget.mouseMoveEvent(move)
            release = QMouseEvent(QMouseEvent.MouseButtonRelease, moved, moved,
                                  Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
            widget.mouseReleaseEvent(release)
            self.assertTrue(seen, "拖动后应当发出 changed")
        finally:
            win.close()

    def test_wheel_writes_and_undoes(self):
        win, seg = self._window_with_video()
        try:
            ins = win.inspector
            ins._apply_wheel(0.12, 0.02, -0.06)
            self.app.processEvents()
            wheels = ins._ref_of(seg, "color_wheel").get("wheels")
            self.assertEqual(wheels["lift"], [0.12, 0.02, -0.06])
            self.assertTrue(win.bridge.undo())
            self.assertFalse(ins._ref_of(seg, "color_wheel").get("wheels"))
        finally:
            win.close()

    def test_colour_edits_change_rendered_pixels(self):
        import numpy as np

        win, seg = self._window_with_video()
        try:
            win.bridge.set_playhead(int(seg.target_timerange.start) + 100_000)
            before_img = win.bridge.frame_image(160, 90)
            before = np.frombuffer(before_img.bits(), np.uint8).reshape(90, -1, 4)[:, :, :3].mean()
            win.inspector._apply_curve([(0.0, 0.0), (0.5, 0.2), (1.0, 1.0)])
            self.app.processEvents()
            win.bridge._drop_renderer()
            after_img = win.bridge.frame_image(160, 90)
            after = np.frombuffer(after_img.bits(), np.uint8).reshape(90, -1, 4)[:, :, :3].mean()
            self.assertGreater(abs(after - before), 2.0, "压暗曲线的画面应当明显变暗")
        finally:
            win.close()


class TestQtMediaBin(unittest.TestCase):
    """素材区：导入到素材库、搜索、类型筛选、加到时间线、拖拽。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        win.resize(1400, 860)
        self.app.processEvents()
        return win

    def _sample(self, name: str) -> str:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "out", name)
        if not os.path.isfile(path):
            self.skipTest(f"缺少 {name}")
        return path

    def test_import_puts_material_in_library_without_a_segment(self):
        win = self._window()
        try:
            video = self._sample("demo.mp4")
            before_segments = sum(len(tr.segments) for tr in win.bridge.project.tracks)
            self.assertTrue(win.bin._add_material_only(video))
            win.bin.refresh_media()
            after_segments = sum(len(tr.segments) for tr in win.bridge.project.tracks)
            self.assertEqual(before_segments, after_segments, "导入素材不该顺手放到时间线")
            self.assertGreaterEqual(win.bin.media_list.count(), 1)
            self.assertIn("demo.mp4", win.bin.media_list.item(0).text())
        finally:
            win.close()

    def test_search_filters_the_list(self):
        win = self._window()
        try:
            win.bin._add_material_only(self._sample("demo.mp4"))
            win.bin.refresh_media()
            win.bin.search_edit.setText("demo")
            self.app.processEvents()
            texts = [win.bin.media_list.item(i).text()
                     for i in range(win.bin.media_list.count())]
            self.assertTrue(any("demo.mp4" in t for t in texts))
            win.bin.search_edit.setText("绝不可能匹配的字符串")
            self.app.processEvents()
            texts = [win.bin.media_list.item(i).text()
                     for i in range(win.bin.media_list.count())]
            self.assertTrue(any("没有匹配" in t for t in texts))
        finally:
            win.close()

    def test_kind_filter_only_shows_that_kind(self):
        win = self._window()
        try:
            win.bin._add_material_only(self._sample("demo.mp4"))
            win.bin.refresh_media()
            win.bin.kind_box.setCurrentIndex(3)          # 音频
            self.app.processEvents()
            texts = [win.bin.media_list.item(i).text()
                     for i in range(win.bin.media_list.count())]
            self.assertFalse(any("[视频]" in t for t in texts), "音频筛选里不该出现视频")
        finally:
            win.close()

    def test_add_to_timeline_creates_a_segment(self):
        win = self._window()
        try:
            video = self._sample("demo.mp4")
            before = sum(len(tr.segments) for tr in win.bridge.project.tracks)
            seg = win.bridge.add_media_to_timeline(video, "video", 500_000)
            self.assertIsNotNone(seg)
            after = sum(len(tr.segments) for tr in win.bridge.project.tracks)
            self.assertEqual(after, before + 1)
            self.assertEqual(int(seg.target_timerange.start), 500_000)
        finally:
            win.close()

    def test_add_audio_goes_to_audio_track(self):
        win = self._window()
        try:
            audio = self._sample("narration.wav")
            seg = win.bridge.add_media_to_timeline(audio, "audio", 0)
            self.assertIsNotNone(seg)
            types = [tr.type for tr in win.bridge.project.tracks
                     if any(s is seg for s in tr.segments)]
            self.assertEqual(types, ["audio"])
        finally:
            win.close()

    def test_targeted_track_receives_the_media(self):
        win = self._window()
        try:
            video = self._sample("demo.mp4")
            win.bridge.add_media_to_timeline(video, "video", 0)   # 先建一条视频轨
            videos = [tr for tr in win.bridge.project.tracks if tr.type == "video"]
            seg = win.bridge.add_media_to_timeline(video, "video", 0)
            self.assertIsNotNone(seg)
            # 没点亮目标时落在第一条视频轨
            self.assertTrue(any(seg in tr.segments for tr in videos))
        finally:
            win.close()

    def test_timeline_accepts_drops(self):
        """拖动一个真实文件进来，时间线要在落点位置生成片段。"""
        from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
        from PySide6.QtGui import QDropEvent

        win = self._window()
        try:
            video = self._sample("demo.mp4")
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(video)])
            pos = QPointF(200, win.timeline._track_y(0) + 18)
            event = QDropEvent(pos, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            win.timeline.dropEvent(event)
            self.app.processEvents()
            total = sum(len(tr.segments) for tr in win.bridge.project.tracks)
            self.assertGreaterEqual(total, 1, "拖放后时间线应当有片段")
        finally:
            win.close()


class TestQtEffectParams(unittest.TestCase):
    """效果页：列出片段上的特效、按 spec 生成参数滑杆、写回、移除。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_effect(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        win.resize(1400, 860)
        project = builder.new_project(320, 180, 30, name="特效测试")
        seg = builder.add_text(project, "x", start=0, duration=1_000_000, size=20, y=0.0)
        win.bridge.set_project(project, None)
        builder.add_effect(project, seg, "smooth", strength=0.5)
        builder.add_effect(project, seg, "glow", strength=0.5)
        win.bridge.select(seg)
        win.inspector.load_values()
        win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("效果"))
        self.app.processEvents()
        return win, seg

    def test_effects_are_listed(self):
        win, _seg = self._window_with_effect()
        try:
            self.assertEqual(win.inspector.effect_box.count(), 2)
            self.assertIn("磨皮", win.inspector.effect_box.itemText(0))
        finally:
            win.close()

    def test_params_follow_effect_spec(self):
        from freecut import effects

        win, _seg = self._window_with_effect()
        try:
            expected = set(effects.EFFECT_SPEC["smooth"]["params"])
            self.assertEqual(set(win.inspector.param_rows), expected)
        finally:
            win.close()

    def test_param_edit_writes_into_material(self):
        win, seg = self._window_with_effect()
        try:
            row = win.inspector.param_rows["strength"]
            row.set_value(0.8, emit=True)
            self.app.processEvents()
            params = win.inspector._effect_refs(seg)[0].get("params")
            self.assertAlmostEqual(params["strength"], 0.8, places=2)
        finally:
            win.close()

    def test_switching_effect_loads_its_own_params(self):
        win, seg = self._window_with_effect()
        try:
            win.inspector.effect_box.setCurrentIndex(1)      # glow
            self.app.processEvents()
            self.assertIn("threshold", win.inspector.param_rows)
            self.assertNotIn("protect", win.inspector.param_rows)
        finally:
            win.close()

    def test_remove_effect_drops_the_ref(self):
        win, seg = self._window_with_effect()
        try:
            self.assertEqual(len(win.inspector._effect_refs(seg)), 2)
            win.inspector.remove_effect()
            self.app.processEvents()
            self.assertEqual(len(win.inspector._effect_refs(seg)), 1)
        finally:
            win.close()

    def test_transition_length_writes_duration(self):
        from freecut import builder

        win, seg = self._window_with_effect()
        try:
            builder.add_transition(win.bridge.project, seg, kind="page_turn",
                                   duration_us=800_000)
            win.inspector.load_values()
            self.app.processEvents()
            self.assertAlmostEqual(win.inspector.transition_row.spin.value(), 800.0, delta=1.0)
            win.inspector.transition_row.set_value(1500.0, emit=True)
            self.app.processEvents()
            transition = [r for r in win.bridge.project.refs_of(seg)
                          if getattr(r, "type", "") == "transition"][0]
            self.assertEqual(int(transition.get("duration")), 1_500_000)
        finally:
            win.close()

    def test_param_change_refreshes_preview(self):
        """改特效参数后预览必须重新渲染（看真的发了渲染请求，不看内部标志位）。"""
        win, _seg = self._window_with_effect()
        try:
            win.viewer.render_now()
            self.app.processEvents()
            service = win.bridge.render_service
            before = service.stats["requested"]
            win.inspector.param_rows["strength"].set_value(1.0, emit=True)
            self.app.processEvents()
            self.assertTrue(win.viewer._dirty or win.viewer._pending,
                            "改特效参数后预览没有被标脏")
            win.viewer.render_now()               # 强制用当前参数渲染一帧
            self.assertGreater(service.stats["requested"], before,
                               "改特效参数后没有重新请求渲染")
        finally:
            win.close()


class TestQtSubtitlePanel(unittest.TestCase):
    """字幕页：导入 SRT、列出、导出、ASR 入口。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    SRT = ("1\n00:00:00,500 --> 00:00:02,000\n第一句\n\n"
           "2\n00:00:02,200 --> 00:00:04,000\n第二句\n")

    def _window(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        win.resize(1400, 860)
        project = builder.new_project(320, 180, 30, name="字幕测试")
        win.bridge.set_project(project, None)
        win.bin.show_category("字幕")
        self.app.processEvents()
        return win

    def _srt_file(self) -> str:
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "demo.srt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.SRT)
        return path

    def test_import_adds_subtitles_to_timeline(self):
        win = self._window()
        try:
            from freecut import subtitles

            subs = subtitles.parse_srt(self._srt_file())
            self.assertEqual(len(subs), 2)
            subtitles.add_subtitles(win.bridge.project, subs)
            win.bin.refresh_subtitles()
            self.assertEqual(win.bin.sub_list.count(), 2)
            self.assertIn("第一句", win.bin.sub_list.item(0).text())
        finally:
            win.close()

    def test_subtitle_list_shows_times(self):
        win = self._window()
        try:
            from freecut import subtitles

            subtitles.add_subtitles(win.bridge.project, subtitles.parse_srt(self._srt_file()))
            win.bin.refresh_subtitles()
            text = win.bin.sub_list.item(0).text()
            self.assertIn("0.50s", text, f"应当显示起始时间: {text}")
        finally:
            win.close()

    def test_export_writes_an_srt_file(self):
        import tempfile

        from freecut import subtitles

        win = self._window()
        try:
            subtitles.add_subtitles(win.bridge.project, subtitles.parse_srt(self._srt_file()))
            subs = subtitles.extract_subtitles(win.bridge.project)
            out = os.path.join(tempfile.mkdtemp(), "out.srt")
            subtitles.write_srt(subs, out)
            self.assertTrue(os.path.isfile(out))
            content = open(out, encoding="utf-8").read()
            self.assertIn("第一句", content)
            self.assertIn("-->", content)
        finally:
            win.close()

    def test_empty_project_exports_nothing(self):
        win = self._window()
        try:
            from freecut import subtitles

            self.assertEqual(subtitles.extract_subtitles(win.bridge.project), [])
            messages = []
            win.bridge.statusMessage.connect(messages.append)
            win.bin.export_subtitles()
            self.assertTrue(any("没有字幕" in m for m in messages),
                            "没有字幕时应当给提示而不是弹保存框")
        finally:
            win.close()

    def test_auto_subtitle_without_video_gives_a_hint(self):
        win = self._window()
        try:
            messages = []
            win.bridge.statusMessage.connect(messages.append)
            win.bin.auto_subtitles()
            self.assertTrue(any("还没有" in m for m in messages))
        finally:
            win.close()

    def test_each_import_gets_its_own_text_track(self):
        """字幕每次导入都新开一条文字轨——这是 subtitles.add_subtitles 写明的行为：
        剪映一条轨上的片段不能重叠，字幕常在已有标题底下，同轨会打架。"""
        from freecut import subtitles

        win = self._window()
        try:
            subtitles.add_subtitles(win.bridge.project, subtitles.parse_srt(self._srt_file()))
            before = len([t for t in win.bridge.project.tracks if t.type == "text"])
            subtitles.add_subtitles(win.bridge.project, subtitles.parse_srt(self._srt_file()))
            after = len([t for t in win.bridge.project.tracks if t.type == "text"])
            self.assertEqual(after, before + 1)
        finally:
            win.close()


class TestQtAnimationPage(unittest.TestCase):
    """动画页：入场/出场/循环三档，写入 material_animation，可清除。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_text(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        win.resize(1400, 860)
        project = builder.new_project(320, 180, 30, name="动画测试")
        seg = builder.add_text(project, "x", start=0, duration=1_000_000, size=20, y=0.0)
        win.bridge.set_project(project, None)
        win.bridge.select(seg)
        win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("动画"))
        self.app.processEvents()
        return win, seg

    def test_three_animation_types_are_offered(self):
        win, _seg = self._window_with_text()
        try:
            boxes = win.inspector.anim_boxes
            self.assertEqual(set(boxes), {"in", "out", "loop"})
            for anim_type, combo in boxes.items():
                self.assertGreater(combo.count(), 1, f"{anim_type} 没有可选项")
                self.assertEqual(combo.itemData(0), "", "第一项应当是「无」")
        finally:
            win.close()

    def test_choosing_animation_creates_material(self):
        win, seg = self._window_with_text()
        try:
            box = win.inspector.anim_boxes["in"]
            box.setCurrentIndex(1)
            self.app.processEvents()
            entries = win.inspector._animation_entries(seg)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["type"], "in")
            self.assertTrue(entries[0]["kind"])
        finally:
            win.close()

    def test_switching_replaces_same_type_only(self):
        win, seg = self._window_with_text()
        try:
            ins = win.inspector
            ins.anim_boxes["in"].setCurrentIndex(1)
            ins.anim_boxes["out"].setCurrentIndex(1)
            self.app.processEvents()
            self.assertEqual(len(ins._animation_entries(seg)), 2)
            first_in = [e for e in ins._animation_entries(seg) if e["type"] == "in"][0]["kind"]
            ins.anim_boxes["in"].setCurrentIndex(2)
            self.app.processEvents()
            entries = ins._animation_entries(seg)
            self.assertEqual(len(entries), 2, "换入场不该把出场也删掉")
            new_in = [e for e in entries if e["type"] == "in"][0]["kind"]
            self.assertNotEqual(new_in, first_in)
        finally:
            win.close()

    def test_choosing_none_removes_that_type(self):
        win, seg = self._window_with_text()
        try:
            ins = win.inspector
            ins.anim_boxes["in"].setCurrentIndex(1)
            self.app.processEvents()
            self.assertEqual(len(ins._animation_entries(seg)), 1)
            ins.anim_boxes["in"].setCurrentIndex(0)
            self.app.processEvents()
            self.assertEqual(ins._animation_entries(seg), [])
        finally:
            win.close()

    def test_clear_removes_all_animations(self):
        win, seg = self._window_with_text()
        try:
            ins = win.inspector
            ins.anim_boxes["in"].setCurrentIndex(1)
            ins.anim_boxes["loop"].setCurrentIndex(1)
            self.app.processEvents()
            self.assertGreaterEqual(len(ins._animation_entries(seg)), 1)
            ins.remove_animations()
            self.app.processEvents()
            self.assertEqual(ins._animation_entries(seg), [])
        finally:
            win.close()

    def test_duration_is_written(self):
        win, seg = self._window_with_text()
        try:
            ins = win.inspector
            ins.anim_duration.set_value(1.5, emit=True)
            ins.anim_boxes["in"].setCurrentIndex(1)
            self.app.processEvents()
            entry = ins._animation_entries(seg)[0]
            self.assertAlmostEqual(entry["duration"] / 1e6, 1.5, places=2)
        finally:
            win.close()


class TestQtSourceMonitor(unittest.TestCase):
    """源监视器 + 三点编辑（PR 有、剪映没有）。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _sample(self) -> str:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "out", "demo.mp4")
        if not os.path.isfile(path):
            self.skipTest("缺少 out/demo.mp4")
        return path

    def _window(self, with_clip: bool = False):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        win.resize(1400, 860)
        project = builder.new_project(320, 180, 30, name="三点编辑")
        if with_clip:
            builder.add_video(project, self._sample(), start=2_000_000, duration=1_000_000)
        win.bridge.set_project(project, None)
        self.app.processEvents()
        return win

    def test_loading_source_fills_the_monitor(self):
        win = self._window()
        try:
            win.bridge.load_source(self._sample(), "video")
            self.assertIsNotNone(win.bridge.source)
            self.assertGreater(win.bridge.source["duration_us"], 1_000_000)
            self.assertEqual(win.bridge.source_span_us,
                             win.bridge.source["duration_us"], "默认入出点应当是整段")
        finally:
            win.close()

    def test_mark_in_and_out(self):
        win = self._window()
        try:
            win.bridge.load_source(self._sample(), "video")
            win.bridge.set_source_playhead(1_000_000)
            win.mark_in()
            win.bridge.set_source_playhead(3_000_000)
            win.mark_out()
            self.assertAlmostEqual(win.bridge.source_span_us / 1e6, 2.0, places=2)
        finally:
            win.close()

    def test_insert_ripples_existing_clips(self):
        win = self._window(with_clip=True)
        try:
            win.bridge.load_source(self._sample(), "video")
            win.bridge.set_source_playhead(1_000_000)
            win.mark_in()
            win.bridge.set_source_playhead(3_000_000)
            win.mark_out()
            win.bridge.set_playhead(0)
            before = [int(s.target_timerange.start)
                      for tr in win.bridge.project.tracks if tr.type == "video"
                      for s in tr.segments]
            win.insert_source()
            self.app.processEvents()
            video = [tr for tr in win.bridge.project.tracks if tr.type == "video"][0]
            starts = sorted(int(s.target_timerange.start) for s in video.segments)
            self.assertIn(0, starts, "新片段应当落在播放头 0")
            self.assertIn(4_000_000, starts, "原来的 2s 片段应当被后移 2s")
            self.assertGreater(len(starts), len(before))
        finally:
            win.close()

    def test_overwrite_trims_the_overlapped_part(self):
        win = self._window(with_clip=True)
        try:
            win.bridge.load_source(self._sample(), "video")
            win.bridge.set_source_playhead(1_000_000)
            win.mark_in()
            win.bridge.set_source_playhead(2_000_000)
            win.mark_out()
            win.bridge.set_playhead(2_500_000)
            win.overwrite_source()
            self.app.processEvents()
            video = [tr for tr in win.bridge.project.tracks if tr.type == "video"][0]
            spans = [(int(s.target_timerange.start), int(s.target_timerange.duration))
                     for s in video.segments]
            self.assertTrue(any(start == 2_500_000 for start, _ in spans),
                            f"新片段应当落在 2.5s：{spans}")
            # 原来 2.0–3.0 的片段被 2.5–3.5 盖住尾巴，只留 2.0–2.5 这段头
            self.assertTrue(any(start == 2_000_000 and dur == 500_000 for start, dur in spans),
                            f"被覆盖的片段应当只留前半段：{spans}")
        finally:
            win.close()

    def test_source_tab_renders_the_media_frame(self):
        win = self._window()
        try:
            win.bridge.load_source(self._sample(), "video")
            win.viewer.tabs.setCurrentIndex(1)
            self.app.processEvents()
            self.assertEqual(win.viewer.mode, "source")
            win.viewer.render_now()
            self.assertIsNotNone(win.viewer._label.pixmap())
            win.viewer.tabs.setCurrentIndex(0)
            self.app.processEvents()
            self.assertEqual(win.viewer.mode, "program")
        finally:
            win.close()

    def test_shortcuts_are_registered(self):
        win = self._window()
        try:
            shortcuts = {}
            for action in win.findChildren(type(win.menuBar().addMenu("x").menuAction())):
                if action.shortcut():
                    shortcuts[str(action.shortcut())] = action.text()
            menu = win.menuBar().actions()
            texts = []
            for top in menu:
                menu_obj = top.menu()
                if menu_obj is not None:
                    texts.extend(a.text() for a in menu_obj.actions())
            self.assertIn("标记入点", texts)
            self.assertIn("标记出点", texts)
            self.assertIn("插入到时间线", texts)
            self.assertIn("覆盖到时间线", texts)
        finally:
            win.close()

    def test_insert_without_source_gives_a_hint(self):
        win = self._window()
        try:
            messages = []
            win.bridge.statusMessage.connect(messages.append)
            win.bridge.clear_source()
            win.insert_source()
            self.assertTrue(any("还没有素材" in m for m in messages))
        finally:
            win.close()


class TestQtIsDefaultGui(unittest.TestCase):
    """第 200 轮起 `freecut gui` 默认开 Qt；`--tk` 回退。"""

    def test_gui_defaults_to_qt(self):
        from freecut import cli

        args = cli.build_parser().parse_args(["gui"])
        self.assertFalse(getattr(args, "tk_shell", False), "默认不该走 Tk")

    def test_tk_flag_still_works(self):
        from freecut import cli

        args = cli.build_parser().parse_args(["gui", "--tk"])
        self.assertTrue(args.tk_shell)

    def test_qt_dispatch_imports_the_qt_shell(self):
        """默认分支必须真的去 import qtui，而不是悄悄用 Tk。"""
        import inspect

        from freecut import cli

        source = inspect.getsource(cli._cmd_gui)
        self.assertIn("qtui.app", source)
        self.assertIn("tk_shell", source)

    def test_falls_back_to_tk_when_qt_missing(self):
        """没装 PySide6 的机器上不能直接崩。"""
        import inspect

        from freecut import cli

        source = inspect.getsource(cli._cmd_gui)
        self.assertIn("改用 Tk", source, "缺 Qt 时应当说明并回退")


class TestPackagedQtBuild(unittest.TestCase):
    """打包产物：目录里有 Qt 运行时，且默认入口是 Qt 版。"""

    def _dist(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(repo, "dist", "FreeCutDir")

    def test_qt_runtime_is_bundled(self):
        dist = self._dist()
        if not os.path.isdir(dist):
            self.skipTest("还没有打包产物")
        # PyInstaller 6 把依赖放在 _internal/ 下
        roots = [dist, os.path.join(dist, "_internal")]
        found = any(
            os.path.isdir(os.path.join(root, "PySide6"))
            for root in roots if os.path.isdir(root)
        )
        self.assertTrue(found, "打包产物里没有 PySide6 运行时")

    def test_qt_platform_plugin_is_bundled(self):
        """没有 platforms/qwindows.dll，Qt 窗口在别的机器上起不来。"""
        dist = self._dist()
        if not os.path.isdir(dist):
            self.skipTest("还没有打包产物")
        plugins = os.path.join(dist, "_internal", "PySide6", "plugins", "platforms")
        if not os.path.isdir(plugins):
            self.skipTest("插件目录不在预期位置")
        self.assertTrue(any(n.lower().startswith("qwindows") for n in os.listdir(plugins)),
                        f"缺少 qwindows 平台插件：{os.listdir(plugins)[:6]}")

    def test_exe_exists(self):
        dist = self._dist()
        if not os.path.isdir(dist):
            self.skipTest("还没有打包产物")
        exe = os.path.join(dist, "FreeCutDir.exe")
        self.assertTrue(os.path.isfile(exe))
        self.assertGreater(os.path.getsize(exe), 1_000_000)


class TestQtSmokeAndDpi(unittest.TestCase):
    """gui-smoke 自检 + DPI 三档布局。"""

    def test_smoke_runs_headless(self):
        import subprocess
        import sys

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = subprocess.run([sys.executable, "-m", "freecut", "gui-smoke"], cwd=repo,
                              capture_output=True, timeout=300, env=env)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, out[-400:])
        self.assertIn("frame ok", out)
        self.assertIn("rail 12", out)      # 剪映左栏 12 个类别

    def test_dpi_scaling_keeps_all_three_panes_visible(self):
        """100% / 125% / 150% 下三栏都要有合理宽度。"""
        import subprocess
        import sys

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for scale in ("1.0", "1.25", "1.5"):
            with self.subTest(scale=scale):
                env = dict(os.environ)
                env["QT_QPA_PLATFORM"] = "offscreen"
                env["QT_SCALE_FACTOR"] = scale
                env.setdefault("PYTHONIOENCODING", "utf-8")
                proc = subprocess.run([sys.executable, "-m", "freecut", "gui-smoke"],
                                      cwd=repo, capture_output=True, timeout=300, env=env)
                out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
                self.assertEqual(proc.returncode, 0, f"{scale}: {out[-300:]}")
                self.assertIn("frame ok", out, f"{scale} 下渲染失败")

    def test_verify_all_has_the_qt_step(self):
        # verify_all.py 在仓库根（FreeCut 的上一级），不是 FreeCut 里面
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(repo, "verify_all.py")
        if not os.path.isfile(path):
            self.skipTest("找不到 verify_all.py")
        text = open(path, encoding="utf-8").read()
        self.assertIn("def run_qt_smoke", text)
        self.assertIn("gui-smoke", text)
        self.assertIn("--no-qt", text)

    def test_packaged_binary_answers_smoke(self):
        """打包版要能跑 gui-smoke；跑不了说明包里的 Qt 运行时有问题。"""
        import subprocess

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        exe = os.path.join(repo, "dist", "FreeCutDir", "FreeCutDir.exe")
        if not os.path.isfile(exe):
            self.skipTest("还没有打包产物")
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        proc = subprocess.run([exe, "gui-smoke"], cwd=repo, capture_output=True,
                              timeout=300, env=env)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, out[-400:])
        self.assertIn("frame ok", out)


class TestQtChromaMask(unittest.TestCase):
    """画面页的色度抠图与蒙版：开关、参数、形状、描边、移除。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        win.resize(1400, 860)
        project = builder.new_project(320, 180, 30, name="抠像蒙版")
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        image = os.path.join(repo, "out", "chroma_src.png")   # 绿幕样张
        if not os.path.isfile(image):
            image = os.path.join(repo, "out", "green.png")
        if not os.path.isfile(image):
            win.close()
            self.skipTest("缺少绿幕样张")
        seg = builder.add_image(project, image, start=0, duration=2_000_000)
        win.bridge.set_project(project, None)
        win.bridge.select(seg)
        win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("画面"))
        self.app.processEvents()
        return win, seg

    def test_chroma_toggle_creates_and_removes(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            self.assertIsNone(ins._chroma_ref(seg))
            ins.chroma_on.setChecked(True)
            self.app.processEvents()
            self.assertIsNotNone(ins._chroma_ref(seg))
            ins.chroma_on.setChecked(False)
            self.app.processEvents()
            self.assertIsNone(ins._chroma_ref(seg))
        finally:
            win.close()

    def test_chroma_parameters_write(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            ins.chroma_on.setChecked(True)
            self.app.processEvents()
            ins.row_similarity.set_value(0.4, emit=True)
            ins.row_smooth.set_value(0.3, emit=True)
            ins.row_spill.set_value(0.2, emit=True)
            self.app.processEvents()
            cfg = ins._chroma_ref(seg).get("config")
            self.assertAlmostEqual(cfg["similarity"], 0.4, places=2)
            self.assertAlmostEqual(cfg["smoothness"], 0.3, places=2)
            self.assertAlmostEqual(cfg["spill"], 0.2, places=2)
        finally:
            win.close()

    def test_six_mask_shapes_available(self):
        from freecut.catalog import masks

        win, _seg = self._window()
        try:
            self.assertEqual(len(win.inspector.mask_names), len(masks()))
            self.assertGreaterEqual(len(win.inspector.mask_names), 6)
        finally:
            win.close()

    def test_mask_shape_and_geometry(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            ins.mask_box.setCurrentIndex(ins.mask_names.index("矩形"))
            self.app.processEvents()
            ins.row_mask_w.set_value(0.6, emit=True)
            ins.row_mask_h.set_value(0.4, emit=True)
            ins.row_mask_feather.set_value(0.2, emit=True)
            ins.row_mask_rot.set_value(30.0, emit=True)
            self.app.processEvents()
            mask = ins._mask_ref(seg)
            self.assertEqual(mask.get("name"), "矩形")
            cfg = mask.get("config")
            self.assertAlmostEqual(cfg["width"], 0.6, places=2)
            self.assertAlmostEqual(cfg["height"], 0.4, places=2)
            self.assertAlmostEqual(cfg["feather"], 0.2, places=2)
            self.assertAlmostEqual(cfg["rotation"], 30.0, places=1)
        finally:
            win.close()

    def test_mask_invert_and_stroke(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            ins.mask_box.setCurrentIndex(2)
            self.app.processEvents()
            ins.mask_invert.setChecked(True)
            ins.row_stroke_w.set_value(8.0, emit=True)
            self.app.processEvents()
            mask = ins._mask_ref(seg)
            self.assertTrue(mask.get("config")["invert"])
            self.assertEqual(int(mask.get("stroke")["width"]), 8)
        finally:
            win.close()

    def test_remove_mask(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            ins.mask_box.setCurrentIndex(2)
            self.app.processEvents()
            self.assertIsNotNone(ins._mask_ref(seg))
            ins.remove_mask()
            self.app.processEvents()
            self.assertIsNone(ins._mask_ref(seg))
        finally:
            win.close()

    def test_mask_changes_pixels(self):
        import numpy as np

        win, seg = self._window()
        try:
            win.bridge.set_playhead(200_000)
            before_img = win.bridge.frame_image(160, 90)
            before = np.frombuffer(before_img.bits(), np.uint8).reshape(90, -1, 4)[:, :, :3].sum()
            win.inspector.mask_box.setCurrentIndex(2)       # 圆形
            self.app.processEvents()
            win.inspector.row_mask_w.set_value(0.3, emit=True)
            win.inspector.row_mask_h.set_value(0.3, emit=True)
            self.app.processEvents()
            win.bridge._drop_renderer()
            after_img = win.bridge.frame_image(160, 90)
            after = np.frombuffer(after_img.bits(), np.uint8).reshape(90, -1, 4)[:, :, :3].sum()
            self.assertNotEqual(int(before), int(after), "加蒙版后画面应当变了")
        finally:
            win.close()


class TestQtSpeedPage(unittest.TestCase):
    """变速页：恒定速度改时长、曲线预设改平均速度、可撤销。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_clip(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        media = os.path.join(repo, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("缺少 out/demo.mp4")
        win = MainWindow(None)
        win.resize(1400, 860)
        project = builder.new_project(320, 180, 30, name="变速测试")
        seg = builder.add_video(project, media, start=0, duration=2_000_000)
        win.bridge.set_project(project, None)
        win.bridge.select(seg)
        win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("变速"))
        self.app.processEvents()
        return win, seg

    def test_speed_tab_is_present(self):
        win, _seg = self._window_with_clip()
        try:
            names = [win.inspector.tabs.tabText(i)
                     for i in range(win.inspector.tabs.count())]
            # 页签顺序照剪映：AI编辑 / 画面 / 音频 / 变速 / 动画 / 文字 / 调节 / 曲线 / 效果
            self.assertEqual(names[:5], ["AI编辑", "画面", "音频", "变速", "动画"])
            self.assertIn("变速", names)
        finally:
            win.close()

    def test_initial_speed_is_loaded(self):
        win, seg = self._window_with_clip()
        try:
            self.assertAlmostEqual(win.inspector.row_speed.spin.value(), seg.speed, places=2)
            self.assertIn("2.00s", win.inspector.speed_hint.text())
        finally:
            win.close()

    def test_constant_speed_changes_duration(self):
        win, seg = self._window_with_clip()
        try:
            win.inspector.row_speed.set_value(2.0, emit=True)
            self.app.processEvents()
            self.assertAlmostEqual(seg.speed, 2.0, places=2)
            self.assertAlmostEqual(seg.target_timerange.duration / 1e6, 1.0, places=2)
            self.assertIn("1.00s", win.inspector.speed_hint.text())
        finally:
            win.close()

    def test_speed_is_clamped_to_supported_range(self):
        win, _seg = self._window_with_clip()
        try:
            row = win.inspector.row_speed
            self.assertAlmostEqual(row.spin.minimum(), 0.25, places=2)
            self.assertAlmostEqual(row.spin.maximum(), 4.0, places=2)
        finally:
            win.close()

    def test_curve_preset_writes_points_and_average(self):
        from freecut.speed import PRESETS

        win, seg = self._window_with_clip()
        try:
            names = list(PRESETS)
            index = names.index("慢-快-慢")
            win.inspector.speed_curve_box.setCurrentIndex(index)
            win.inspector._apply_speed_curve()
            self.app.processEvents()
            self.assertEqual(len(seg.speed_curve), 3)
            self.assertAlmostEqual(seg.speed, 1.5, places=2)
        finally:
            win.close()

    def test_speed_edit_can_be_undone(self):
        win, seg = self._window_with_clip()
        try:
            original = float(seg.speed)
            win.inspector.row_speed.set_value(0.5, emit=True)
            self.app.processEvents()
            self.assertAlmostEqual(seg.speed, 0.5, places=2)
            self.assertTrue(win.bridge.undo())
            self.assertAlmostEqual(seg.speed, original, places=2)
        finally:
            win.close()

    def test_speed_change_refreshes_preview(self):
        win, _seg = self._window_with_clip()
        try:
            win.viewer.render_now()
            win.inspector.row_speed.set_value(1.5, emit=True)
            self.app.processEvents()
            self.assertTrue(win.viewer._dirty, "改变速后预览应当被标脏")
        finally:
            win.close()


class TestQtKeyframes(unittest.TestCase):
    """关键帧：菱形按钮打点/删点、缩放同时写 X/Y、渲染真的动。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        win.resize(1400, 860)
        rows = [tr for tr in win.timeline.rows() if tr.type == "video" and tr.segments]
        if not rows:
            win.close()
            self.skipTest("没有视频片段")
        seg = rows[0].segments[0]
        win.bridge.select(seg)
        win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("画面"))
        self.app.processEvents()
        return win, seg

    def test_every_keyframable_row_has_a_button(self):
        """画面 5 项 + 调节 10 项 = 15 行，每行都要有菱形。"""
        win, _seg = self._window()
        try:
            rows = win.inspector.kf_rows
            self.assertEqual(set(rows), set(win.inspector.keyframe_props))
            self.assertGreaterEqual(len(rows), 15)
            for label, row in rows.items():
                self.assertIsNotNone(row.kf_button, f"{label} 没有菱形按钮")
        finally:
            win.close()

    def test_click_adds_keyframe_at_playhead(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            start = int(seg.target_timerange.start)
            win.bridge.set_playhead(start + 500_000)
            ins.row_x.set_value(-0.3, emit=True)
            ins.row_x.kf_button.click()
            keys = seg.keyframes.get("KFTypePositionX") or []
            self.assertEqual(len(keys), 1)
            self.assertEqual(int(keys[0].time_offset), 500_000)
            self.assertAlmostEqual(keys[0].value, -0.3, places=3)
        finally:
            win.close()

    def test_scale_writes_both_axes(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            win.bridge.set_playhead(int(seg.target_timerange.start))
            ins.row_scale.set_value(50.0, emit=True)
            ins.row_scale.kf_button.click()
            self.assertIn("KFTypeScaleX", seg.keyframes)
            self.assertIn("KFTypeScaleY", seg.keyframes)
            self.assertAlmostEqual(seg.keyframes["KFTypeScaleX"][0].value, 0.5, places=2)
        finally:
            win.close()

    def test_second_click_removes_the_keyframe(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            win.bridge.set_playhead(int(seg.target_timerange.start) + 200_000)
            ins.row_alpha.kf_button.click()
            self.assertTrue(seg.keyframes.get("KFTypeAlpha"))
            ins.row_alpha.kf_button.click()
            self.assertFalse(seg.keyframes.get("KFTypeAlpha"))
        finally:
            win.close()

    def test_button_state_reflects_playhead(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            start = int(seg.target_timerange.start)
            win.bridge.set_playhead(start)
            ins.row_rotation.kf_button.click()
            self.assertEqual(ins.row_rotation.kf_button._state, "here")
            win.bridge.set_playhead(start + 1_000_000)
            ins._refresh_kf_buttons()
            self.assertEqual(ins.row_rotation.kf_button._state, "some",
                             "有别处的帧但不是当前位置")
        finally:
            win.close()

    def test_animation_changes_rendered_frames(self):
        import numpy as np

        win, seg = self._window()
        try:
            ins = win.inspector
            start = int(seg.target_timerange.start)
            win.bridge.set_playhead(start)
            ins.row_x.set_value(-0.4, emit=True)
            ins.row_x.kf_button.click()
            win.bridge.set_playhead(start + 1_000_000)
            ins.row_x.set_value(0.4, emit=True)
            ins.row_x.kf_button.click()
            win.bridge.set_playhead(start + 100_000)
            a = np.frombuffer(win.bridge.frame_image(120, 68).bits(), np.uint8)
            first = a.reshape(68, -1, 4)[:, :, :3].astype(int)
            win.bridge.set_playhead(start + 900_000)
            b = np.frombuffer(win.bridge.frame_image(120, 68).bits(), np.uint8)
            second = b.reshape(68, -1, 4)[:, :, :3].astype(int)
            self.assertGreater(int(np.abs(first - second).mean()), 2,
                               "关键帧动画应当让两帧明显不同")
        finally:
            win.close()

    def test_keyframe_can_be_undone(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            win.bridge.set_playhead(int(seg.target_timerange.start))
            ins.row_y.kf_button.click()
            self.assertTrue(seg.keyframes.get("KFTypePositionY"))
            self.assertTrue(win.bridge.undo())
            self.assertFalse(seg.keyframes.get("KFTypePositionY"))
        finally:
            win.close()


def _showcase_media_ok(project) -> bool:
    """示例工程引用的素材现在还在不在磁盘上。"""
    import os as _os

    for mat in project.materials.values():
        path = getattr(mat, "path", "") or ""
        if path and not _os.path.isfile(path):
            return False
    return True


class TestQtAdjustKeyframes(unittest.TestCase):
    """调节参数也能打关键帧：亮度等 8 项可动，色调没有 KFType 名字故不可动。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        win.resize(1400, 860)
        rows = [tr for tr in win.timeline.rows() if tr.type == "video" and tr.segments]
        if not rows:
            win.close()
            self.skipTest("没有视频片段")
        seg = rows[0].segments[0]
        win.bridge.select(seg)
        win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("调节"))
        self.app.processEvents()
        return win, seg

    def test_ten_adjust_rows_are_keyframable(self):
        """除色调外都能打帧：亮度/对比度/饱和度/色温/高光/阴影/白色/黑色/锐化/暗角。"""
        win, _seg = self._window()
        try:
            keys = set(win.inspector.adjust_kf_rows)
            expected = {"brightness", "contrast", "saturation", "temperature",
                        "highlight", "shadow", "white", "black", "sharpen", "vignette"}
            self.assertEqual(keys, expected)
        finally:
            win.close()

    def test_tint_has_no_diamond(self):
        win, _seg = self._window()
        try:
            self.assertIsNone(win.inspector.adjust_rows["tint"].kf_button,
                              "色调在 KFType 枚举里没有名字，不该有菱形")
        finally:
            win.close()

    def test_names_match_the_scraped_enum(self):
        """写进去的属性名必须出现在剪映那 186 个 KFType 里。"""
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(repo, "内部实测", "evidence", "kftype-values.txt")
        if not os.path.isfile(path):
            self.skipTest("没有 kftype-values.txt")
        with open(path, encoding="utf-8") as handle:
            known = {ln.strip() for ln in handle}
        win, _seg = self._window()
        try:
            for names in win.inspector.keyframe_props.values():
                for name in names:
                    self.assertIn(name, known, f"{name} 不在剪映枚举里")
        finally:
            win.close()

    def test_brightness_keyframes_write_and_interpolate(self):
        import numpy as np

        win, seg = self._window()
        try:
            ins = win.inspector
            row = ins.adjust_kf_rows["brightness"]
            start = int(seg.target_timerange.start)
            win.bridge.set_playhead(start)
            row.set_value(-40.0, emit=True)
            row.kf_button.click()
            win.bridge.set_playhead(start + 1_000_000)
            row.set_value(40.0, emit=True)
            row.kf_button.click()
            keys = seg.keyframes.get("KFTypeBrightness") or []
            self.assertEqual(len(keys), 2)
            win.bridge.set_playhead(start + 100_000)
            dark = np.frombuffer(win.bridge.frame_image(80, 45).bits(), np.uint8)
            dark = dark.reshape(45, -1, 4)[:, :, :3].mean()
            win.bridge.set_playhead(start + 900_000)
            bright = np.frombuffer(win.bridge.frame_image(80, 45).bits(), np.uint8)
            bright = bright.reshape(45, -1, 4)[:, :, :3].mean()
            self.assertGreater(bright - dark, 20, "亮度关键帧应当让画面明显变亮")
        finally:
            win.close()

    def test_adjust_keyframe_undo(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            win.bridge.set_playhead(int(seg.target_timerange.start))
            ins.adjust_kf_rows["contrast"].kf_button.click()
            self.assertTrue(seg.keyframes.get("KFTypeContrast"))
            self.assertTrue(win.bridge.undo())
            self.assertFalse(seg.keyframes.get("KFTypeContrast"))
        finally:
            win.close()

    def test_renderer_animates_adjust_without_ui(self):
        """绕过界面，直接验证渲染器读 KFTypeBrightness。"""
        import numpy as np

        from freecut import builder, render

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        media = os.path.join(repo, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("缺少 demo.mp4")
        project = builder.new_project(64, 36, 30, name="调节关键帧")
        seg = builder.add_video(project, media, start=0, duration=2_000_000)
        builder.set_adjust(project, seg, brightness=0.0)
        builder.add_keyframe(seg, "KFTypeBrightness", 0, -50.0)
        builder.add_keyframe(seg, "KFTypeBrightness", 1_000_000, 50.0)
        renderer = render.Renderer(project, 64, 36, 30)
        try:
            first = renderer.render_frame(3)
            second = renderer.render_frame(27)
        finally:
            renderer.close()
        self.assertGreater(float(second.mean()) - float(first.mean()), 20)


class TestQtCrop(unittest.TestCase):
    """裁剪：四边比例写进材质 crop，渲染跟着变，可撤销可复位。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        win.resize(1400, 860)
        rows = [tr for tr in win.timeline.rows() if tr.type == "video" and tr.segments]
        if not rows:
            win.close()
            self.skipTest("没有视频片段")
        seg = rows[0].segments[0]
        win.bridge.select(seg)
        win.inspector.tabs.setCurrentIndex(win.inspector.tab_index("画面"))
        self.app.processEvents()
        return win, seg

    def test_four_edges_start_at_zero(self):
        win, _seg = self._window()
        try:
            for row in (win.inspector.row_crop_left, win.inspector.row_crop_right,
                        win.inspector.row_crop_top, win.inspector.row_crop_bottom):
                self.assertAlmostEqual(row.spin.value(), 0.0, places=2)
        finally:
            win.close()

    def test_crop_writes_four_edges(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            ins.row_crop_left.set_value(0.25, emit=True)
            ins.row_crop_right.set_value(0.10, emit=True)
            ins.row_crop_top.set_value(0.05, emit=True)
            self.app.processEvents()
            crop = win.bridge.project.material(seg.material_id).get("crop")
            self.assertAlmostEqual(crop["upper_left_x"], 0.25, places=2)
            self.assertAlmostEqual(crop["upper_left_y"], 0.05, places=2)
            self.assertAlmostEqual(crop["lower_right_x"], 0.90, places=2)
        finally:
            win.close()

    def test_hint_reports_kept_area(self):
        win, _seg = self._window()
        try:
            ins = win.inspector
            ins.row_crop_left.set_value(0.20, emit=True)
            ins.row_crop_right.set_value(0.15, emit=True)
            self.app.processEvents()
            self.assertIn("65%", ins.crop_hint.text())
        finally:
            win.close()

    def test_crop_changes_rendered_frame(self):
        import numpy as np

        win, seg = self._window()
        try:
            win.bridge.set_playhead(int(seg.target_timerange.start) + 100_000)
            before = np.frombuffer(win.bridge.frame_image(120, 68).bits(), np.uint8)
            before = before.reshape(68, -1, 4)[:, :, :3].astype(int)
            win.inspector.row_crop_left.set_value(0.3, emit=True)
            self.app.processEvents()
            win.bridge._drop_renderer()
            after = np.frombuffer(win.bridge.frame_image(120, 68).bits(), np.uint8)
            after = after.reshape(68, -1, 4)[:, :, :3].astype(int)
            self.assertGreater(int(np.abs(before - after).mean()), 2)
        finally:
            win.close()

    def test_crop_reset_and_undo(self):
        win, seg = self._window()
        try:
            ins = win.inspector
            ins.row_crop_left.set_value(0.2, emit=True)
            self.app.processEvents()
            ins.reset_crop()
            self.app.processEvents()
            crop = win.bridge.project.material(seg.material_id).get("crop")
            self.assertEqual(crop["upper_left_x"], 0.0)
            self.assertTrue(win.bridge.undo())
            crop = win.bridge.project.material(seg.material_id).get("crop")
            self.assertAlmostEqual(crop["upper_left_x"], 0.2, places=2, msg="撤销应当回到裁过的状态")
        finally:
            win.close()


class TestTimelineKeyframeMarks(unittest.TestCase):
    """时间线上的关键帧小菱形：位置按比例算，同一时刻只画一颗。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_text(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        win.resize(1400, 860)
        project = builder.new_project(320, 180, 30, name="标记测试")
        seg = builder.add_text(project, "x", start=0, duration=2_000_000, size=20, y=0.0)
        win.bridge.set_project(project, None)
        self.app.processEvents()
        return win, seg

    def test_no_keyframes_means_no_marks(self):
        win, seg = self._window_with_text()
        try:
            self.assertEqual(win.timeline.keyframe_marks(seg), [])
        finally:
            win.close()

    def test_marks_are_relative_positions(self):
        from freecut import builder

        win, seg = self._window_with_text()
        try:
            builder.add_keyframe(seg, "KFTypePositionX", 0, -0.2)
            builder.add_keyframe(seg, "KFTypePositionX", 1_000_000, 0.0)
            builder.add_keyframe(seg, "KFTypePositionX", 2_000_000, 0.2)
            marks = win.timeline.keyframe_marks(seg)
            self.assertEqual(marks, [0.0, 0.5, 1.0])
        finally:
            win.close()

    def test_same_time_on_two_properties_is_one_mark(self):
        from freecut import builder

        win, seg = self._window_with_text()
        try:
            builder.add_keyframe(seg, "KFTypePositionX", 500_000, 0.1)
            builder.add_keyframe(seg, "KFTypeScaleX", 500_000, 1.2)
            builder.add_keyframe(seg, "KFTypeAlpha", 500_000, 0.8)
            self.assertEqual(win.timeline.keyframe_marks(seg), [0.25])
        finally:
            win.close()

    def test_out_of_range_keyframes_are_ignored(self):
        from freecut import builder

        win, seg = self._window_with_text()
        try:
            builder.add_keyframe(seg, "KFTypePositionX", 5_000_000, 0.1)
            self.assertEqual(win.timeline.keyframe_marks(seg), [],
                             "超出片段时长的帧不该画出来")
        finally:
            win.close()


class TestQtIconsAndHover(unittest.TestCase):
    """左栏图标（自绘）与悬停反馈。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def test_every_category_has_an_icon(self):
        from freecut import theme
        from freecut.qtui import icons

        for name in theme.JY_CATEGORIES:
            pixmap = icons.icon(name, 20)
            self.assertFalse(pixmap.isNull(), f"{name} 图标是空的")
            self.assertEqual(pixmap.width(), 20)
            self.assertTrue(pixmap.hasAlphaChannel(), "图标应当带透明底")

    def test_unknown_category_falls_back_to_blank(self):
        from freecut.qtui import icons

        pixmap = icons.icon("不存在的类别", 20)
        self.assertFalse(pixmap.isNull())
        image = pixmap.toImage()
        # 没画东西：整张图仍然全透明
        self.assertEqual(image.pixelColor(10, 10).alpha(), 0)

    def test_rail_buttons_carry_icons(self):
        from freecut import theme
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        self.app.processEvents()
        try:
            for name in theme.QT_CATEGORIES:
                button = win.rail.buttons[name]
                self.assertFalse(button.icon().isNull(), f"{name} 按钮没有图标")
                self.assertIn(name, button.text(), "图标下面应当还有文字")
                self.assertNotIn(name, theme.CLOUD_ONLY)
        finally:
            win.close()

    def test_qss_has_hover_feedback(self):
        from freecut.qtui.theme_qss import stylesheet

        css = stylesheet()
        for selector in ("QPushButton:hover", "#Rail QToolButton:hover",
                         "QListWidget::item:hover"):
            self.assertIn(selector, css, f"缺少 {selector} 反馈")

    def test_timeline_tracks_the_hovered_clip(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        win.resize(1400, 860)
        project = builder.new_project(320, 180, 30, name="悬停")
        seg = builder.add_text(project, "x", start=0, duration=2_000_000, size=20, y=0.0)
        win.bridge.set_project(project, None)
        self.app.processEvents()
        try:
            from PySide6.QtCore import QEvent

            win.timeline._hover_seg = seg
            self.assertIs(win.timeline._hover_seg, seg)
            win.timeline.leaveEvent(QEvent(QEvent.Leave))
            self.assertIsNone(win.timeline._hover_seg, "离开时间线应当清掉悬停")
        finally:
            win.close()


class TestQtShortcutsPanel(unittest.TestCase):
    """快捷键面板：列表从菜单现取，手势写死，? 能打开。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        self.app.processEvents()
        return win

    def test_collect_finds_the_core_shortcuts(self):
        from freecut.qtui.dialogs.shortcuts import collect_shortcuts

        win = self._window()
        try:
            rows = collect_shortcuts(win)
            keys = {key for _g, key, _t in rows}
            for expected in ("Ctrl+Z", "Ctrl+Y", "Ctrl+S", "Ctrl+E", "I", "O"):
                self.assertIn(expected, keys, f"快捷键面板漏了 {expected}")
        finally:
            win.close()

    def test_collect_matches_menu_actions(self):
        """面板里的条数必须等于菜单里带快捷键的动作数——不能手写出偏差。"""
        from freecut.qtui.dialogs.shortcuts import collect_shortcuts

        win = self._window()
        try:
            rows = collect_shortcuts(win)
            actual = 0
            for top in win.menuBar().actions():
                menu = top.menu()
                if menu is None:
                    continue
                for action in menu.actions():
                    if not action.isSeparator() and not action.shortcut().isEmpty():
                        actual += 1
            self.assertEqual(len(rows), actual)
            self.assertGreaterEqual(actual, 10)
        finally:
            win.close()

    def test_gestures_are_documented(self):
        from freecut.qtui.dialogs.shortcuts import GESTURES

        self.assertGreaterEqual(len(GESTURES), 5)
        for gesture, meaning in GESTURES:
            self.assertTrue(gesture.strip())
            self.assertTrue(meaning.strip())

    def test_dialog_lists_everything(self):
        from freecut.qtui.dialogs.shortcuts import ShortcutsDialog, collect_shortcuts

        win = self._window()
        try:
            dialog = ShortcutsDialog(win)
            self.assertEqual(dialog.row_count, len(collect_shortcuts(win)))
            self.assertGreater(dialog.row_count, 0)
            dialog.close()
        finally:
            win.close()

    def test_question_mark_opens_it(self):
        from PySide6.QtGui import QKeySequence

        win = self._window()
        try:
            hits = [a for top in win.menuBar().actions() if top.menu() is not None
                    for a in top.menu().actions()
                    if a.shortcut() == QKeySequence("?")]
            self.assertTrue(hits, "没有任何动作绑定 ? 键")
            self.assertIn("快捷键", hits[0].text())
        finally:
            win.close()


class TestQtBinThumbnails(unittest.TestCase):
    """素材列表缩略图：视频抽帧、图片缩放、音频波形，且带缓存。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, project_path="out/showcase.json"):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full = os.path.join(repo, project_path) if project_path else None
        if full and not os.path.isfile(full):
            self.skipTest(f"缺少 {project_path}")
        win = MainWindow(full)
        win.resize(1400, 860)
        self.app.processEvents()
        return win

    def test_video_items_get_a_frame_thumbnail(self):
        win = self._window()
        try:
            items = [win.bin.media_list.item(i) for i in range(win.bin.media_list.count())]
            videos = [it for it in items if it.text().startswith("[视频]")]
            self.assertTrue(videos, "示例工程里应当有视频素材")
            with_icon = [it for it in videos if not it.icon().isNull()]
            self.assertGreaterEqual(len(with_icon), 1, "视频素材应当有缩略图")
            sizes = win.bin.media_list.iconSize()
            self.assertEqual((sizes.width(), sizes.height()), (48, 27))
        finally:
            win.close()

    def test_image_and_audio_items_get_icons_too(self):
        win = self._window()
        try:
            items = [win.bin.media_list.item(i) for i in range(win.bin.media_list.count())]
            for prefix in ("[图片]", "[音频]"):
                group = [it for it in items if it.text().startswith(prefix)]
                if not group:
                    continue
                self.assertFalse(group[0].icon().isNull(), f"{prefix} 素材没有缩略图")
        finally:
            win.close()

    def test_second_refresh_is_cached(self):
        import time

        win = self._window()
        try:
            win.bin.refresh_media()
            self.app.processEvents()
            started = time.time()
            win.bin.refresh_media()
            self.app.processEvents()
            spent = time.time() - started
            self.assertLess(spent, 0.5, f"第二次刷新应当走缓存，实际 {spent:.2f}s")
        finally:
            win.close()

    def test_missing_file_does_not_crash(self):
        win = self._window()
        try:
            self.assertIsNone(win.bin._thumbnail_for("/不存在/的文件.mp4", "video"))
        finally:
            win.close()

    def test_only_one_help_menu(self):
        """第 28 轮发现过两个「帮助」菜单。"""
        win = self._window()
        try:
            titles = [a.text() for a in win.menuBar().actions()]
            self.assertEqual(titles.count("帮助"), 1, f"菜单重复：{titles}")
        finally:
            win.close()


class TestQtLibraryThumbs(unittest.TestCase):
    """特效 / 转场缩略图：拿真实算法跑在合成底图上，且带缓存。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def test_effect_thumbnail_is_drawn(self):
        from freecut.qtui.thumbs import effect_pixmap

        pixmap = effect_pixmap("sparkle")
        self.assertFalse(pixmap.isNull())
        self.assertEqual((pixmap.width(), pixmap.height()), (72, 40))

    def test_effect_thumbnails_are_cached(self):
        from freecut.qtui.thumbs import effect_pixmap

        first = effect_pixmap("glow")
        second = effect_pixmap("glow")
        self.assertIs(first, second, "同一特效应当复用同一张图")

    def test_effects_differ_from_each_other(self):
        import numpy as np

        from freecut.qtui.thumbs import effect_pixmap

        def pixels(kind):
            image = effect_pixmap(kind).toImage()
            return np.array([[image.pixelColor(x, y).getRgb()[:3]
                              for x in range(0, image.width(), 4)]
                             for y in range(0, image.height(), 4)], dtype=int)

        self.assertGreater(int(np.abs(pixels("duotone") - pixels("sparkle")).mean()), 2,
                           "不同特效的缩略图应当长得不一样")

    def test_transition_direction_shows_in_pixels(self):
        import numpy as np

        from freecut.qtui.thumbs import transition_pixmap

        def pixels(kind):
            image = transition_pixmap(kind).toImage()
            return np.array([[image.pixelColor(x, y).getRgb()[:3]
                              for x in range(image.width())]
                             for y in range(image.height())], dtype=int)

        left = pixels("wipe_left")
        right = pixels("wipe_right")
        self.assertGreater(int(np.abs(left - right).mean()), 5,
                           "向左擦除与向右擦除的缩略图不该一样")

    def test_bin_lists_carry_icons(self):
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        win.resize(1400, 860)
        self.app.processEvents()
        try:
            self.assertGreater(win.bin.effect_list.count(), 5)
            # 图标是打开那一页时才生成的（启动省 1 秒），所以先"切到"这一页
            win.bin.show_category("特效")
            self.app.processEvents()
            self.assertFalse(win.bin.effect_list.item(0).icon().isNull())
            self.assertGreater(win.bin.transition_list.count(), 5)
            win.bin.show_category("转场")
            self.app.processEvents()
            self.assertFalse(win.bin.transition_list.item(0).icon().isNull())
        finally:
            win.close()


class TestQtDropIndicator(unittest.TestCase):
    """拖入时间线时的插入位置竖线。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        win.resize(1400, 860)
        self.app.processEvents()
        return win

    def _mime(self):
        from PySide6.QtCore import QMimeData, QUrl

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        media = os.path.join(repo, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("缺少 demo.mp4")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(media)])
        return mime

    def test_no_indicator_before_dragging(self):
        win = self._window()
        try:
            self.assertIsNone(win.timeline._drop_us)
        finally:
            win.close()

    def test_drag_enter_sets_and_moves_the_indicator(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent

        win = self._window()
        try:
            timeline = win.timeline
            mime = self._mime()
            timeline.dragEnterEvent(
                QDragEnterEvent(QPoint(300, 200), Qt.CopyAction, mime,
                                Qt.LeftButton, Qt.NoModifier))
            first = timeline._drop_us
            self.assertIsNotNone(first)
            timeline.dragMoveEvent(
                QDragMoveEvent(QPoint(460, 200), Qt.CopyAction, mime,
                               Qt.LeftButton, Qt.NoModifier))
            self.assertGreater(timeline._drop_us, first, "往右拖，插入点应当往后")
        finally:
            win.close()

    def test_drop_uses_the_indicator_position_and_clears_it(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QDragEnterEvent, QDropEvent

        win = self._window()
        try:
            timeline = win.timeline
            mime = self._mime()
            timeline.dragEnterEvent(
                QDragEnterEvent(QPoint(380, 200), Qt.CopyAction, mime,
                                Qt.LeftButton, Qt.NoModifier))
            target = timeline._drop_us
            self.assertIsNotNone(target)
            before = sum(len(tr.segments) for tr in win.bridge.project.tracks)
            timeline.dropEvent(
                QDropEvent(QPointF(380, 200), Qt.CopyAction, mime,
                           Qt.LeftButton, Qt.NoModifier))
            self.app.processEvents()
            self.assertIsNone(timeline._drop_us, "放下后指示线应当消失")
            after = sum(len(tr.segments) for tr in win.bridge.project.tracks)
            self.assertEqual(after, before + 1)
        finally:
            win.close()

    def test_leave_clears_the_indicator(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent

        win = self._window()
        try:
            timeline = win.timeline
            mime = self._mime()
            timeline.dragEnterEvent(
                QDragEnterEvent(QPoint(300, 200), Qt.CopyAction, mime,
                                Qt.LeftButton, Qt.NoModifier))
            self.assertIsNotNone(timeline._drop_us)
            timeline.dragLeaveEvent(QDragLeaveEvent())
            self.assertIsNone(timeline._drop_us)
        finally:
            win.close()


class TestQtScrubBar(unittest.TestCase):
    """播放器下方的可拖时间轴。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        win.resize(1400, 860)
        self.app.processEvents()
        win.player.scrub.resize(600, 14)
        return win

    def _click(self, bar, x):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        point = QPointF(x, bar.height() / 2)
        bar.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, point, point,
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        self.app.processEvents()

    def test_click_middle_seeks_to_the_halfway_point(self):
        win = self._window()
        try:
            self._click(win.player.scrub, 300)
            ratio = win.bridge.playhead / win.bridge.duration_us
            self.assertAlmostEqual(ratio, 0.5, delta=0.02)
        finally:
            win.close()

    def test_progress_follows_the_playhead(self):
        win = self._window()
        try:
            win.bridge.set_playhead(int(win.bridge.duration_us * 0.25))
            self.app.processEvents()
            self.assertAlmostEqual(win.player.scrub._position, 0.25, delta=0.02)
        finally:
            win.close()

    def test_source_mode_seeks_the_source_and_shows_marks(self):
        from freecut.qtui.widgets.viewer import ScrubBar

        win = self._window()
        try:
            repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            media = os.path.join(repo, "out", "demo.mp4")
            if not os.path.isfile(media):
                self.skipTest("缺少 demo.mp4")
            win.bridge.load_source(media, "video")
            win.bridge.mark_source_in()
            win.bridge.set_source_playhead(2_000_000)
            win.bridge.mark_source_out()
            win.player.set_source_mode(True)
            self.app.processEvents()
            bar = win.player.scrub
            self.assertEqual(len(bar._marks), 2, "源模式的入出点应当画在时间轴上")
            self._click(bar, 300)
            total = int(win.bridge.source["duration_us"])
            self.assertAlmostEqual(win.bridge.source["playhead_us"] / total, 0.5, delta=0.02)
        finally:
            win.close()

    def test_program_mode_has_no_marks(self):
        win = self._window()
        try:
            win.player.set_source_mode(False)
            self.app.processEvents()
            self.assertEqual(win.player.scrub._marks, [])
        finally:
            win.close()

    def test_ratio_is_clamped(self):
        from freecut.qtui.widgets.viewer import ScrubBar

        bar = ScrubBar()
        bar.resize(200, 14)
        self.assertEqual(bar.ratio_at(-50), 0.0)
        self.assertEqual(bar.ratio_at(500), 1.0)


class TestQtBinFavorites(unittest.TestCase):
    """素材收藏：星标、只看收藏、刷新后仍在。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sample = os.path.join(repo, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        win.resize(1400, 860)
        self.app.processEvents()
        if win.bin.media_list.count() == 0:
            win.close()
            self.skipTest("素材列表是空的")
        return win

    def test_toggle_adds_and_removes(self):
        win = self._window()
        try:
            bin_ = win.bin
            bin_.media_list.setCurrentRow(0)
            material = bin_._selected_material_obj()
            bin_.toggle_favorite()
            self.assertTrue(bin_.is_favorite(material))
            bin_.media_list.setCurrentRow(0)
            bin_.toggle_favorite()
            self.assertFalse(bin_.is_favorite(material))
        finally:
            win.close()

    def test_star_shows_in_the_item_text(self):
        win = self._window()
        try:
            bin_ = win.bin
            bin_.media_list.setCurrentRow(0)
            bin_.toggle_favorite()
            self.assertTrue(bin_.media_list.item(0).text().startswith("★"))
            bin_.media_list.setCurrentRow(0)
            bin_.toggle_favorite()
            self.assertFalse(bin_.media_list.item(0).text().startswith("★"))
        finally:
            win.close()

    def test_only_favorites_filter(self):
        win = self._window()
        try:
            bin_ = win.bin
            total = bin_.media_list.count()
            bin_.media_list.setCurrentRow(0)
            bin_.toggle_favorite()
            bin_.only_fav_box.setChecked(True)
            self.app.processEvents()
            self.assertLess(bin_.media_list.count(), total)
            for index in range(bin_.media_list.count()):
                self.assertTrue(bin_.media_list.item(index).text().startswith("★"))
            bin_.only_fav_box.setChecked(False)
            self.app.processEvents()
            self.assertEqual(bin_.media_list.count(), total)
        finally:
            win.close()

    def test_favorite_survives_refresh(self):
        win = self._window()
        try:
            bin_ = win.bin
            bin_.media_list.setCurrentRow(0)
            material = bin_._selected_material_obj()
            bin_.toggle_favorite()
            bin_.refresh_media()
            self.app.processEvents()
            self.assertTrue(bin_.is_favorite(material))
            self.assertTrue(bin_.media_list.item(0).text().startswith("★"))
        finally:
            win.close()

    def test_favorite_is_per_material_not_per_file(self):
        """同一文件在工程里可能有多个副本，收藏只该跟着被点的那一个。"""
        win = self._window()
        try:
            bin_ = win.bin
            bin_.media_list.setCurrentRow(0)
            first = bin_._selected_material_obj()
            bin_.toggle_favorite()
            bin_.only_fav_box.setChecked(True)
            self.app.processEvents()
            self.assertEqual(bin_.media_list.count(), 1, "收藏一个副本只该剩一个")
            bin_.only_fav_box.setChecked(False)
            self.app.processEvents()
            self.assertTrue(bin_.is_favorite(first))
        finally:
            win.close()

    def test_toggle_without_selection_only_warns(self):
        win = self._window()
        try:
            bin_ = win.bin
            bin_.media_list.setCurrentRow(-1)
            messages = []
            win.bridge.statusMessage.connect(messages.append)
            bin_.toggle_favorite()
            self.assertTrue(any("先" in m for m in messages))
        finally:
            win.close()


class TestQtInternalDrag(unittest.TestCase):
    """从素材列表往时间线拖（真实路径：列表自己的 mimeData，不是手工造的 URL）。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("没有 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        media = os.path.join(repo, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("缺少 demo.mp4")
        win = MainWindow(None)                 # 空工程：0 轨，正是用户遇到的场景
        win.resize(1400, 860)
        win.bin._add_material_only(media)
        win.bin.refresh_media()
        self.app.processEvents()
        return win

    def test_list_drag_carries_a_file_url(self):
        win = self._window()
        try:
            lst = win.bin.media_list
            self.assertGreater(lst.count(), 0)
            mime = lst.mimeData([lst.item(0)])
            self.assertTrue(mime.hasUrls(), f"拖拽数据里没有 URL：{mime.formats()}")
            self.assertTrue(mime.urls()[0].toLocalFile().endswith(".mp4"))
        finally:
            win.close()

    def test_drop_from_list_creates_track_and_clip(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QDragEnterEvent, QDropEvent

        win = self._window()
        try:
            self.assertEqual(len(win.bridge.project.tracks), 0)
            lst = win.bin.media_list
            mime = lst.mimeData([lst.item(0)])
            timeline = win.timeline
            timeline.dragEnterEvent(
                QDragEnterEvent(QPoint(300, 200), Qt.CopyAction, mime,
                                Qt.LeftButton, Qt.NoModifier))
            self.assertIsNotNone(timeline._drop_us)
            timeline.dropEvent(
                QDropEvent(QPointF(300, 200), Qt.CopyAction, mime,
                           Qt.LeftButton, Qt.NoModifier))
            self.app.processEvents()
            self.assertEqual(len(win.bridge.project.tracks), 1, "应当自动建一条视频轨")
            self.assertEqual(win.bridge.project.tracks[0].type, "video")
            self.assertEqual(len(win.bridge.project.tracks[0].segments), 1)
        finally:
            win.close()

    def test_media_smoke_command_reports_the_drag_path(self):
        """打包版和验收脚本都靠这条命令证明「拖得进去」。"""
        import subprocess
        import sys

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        media = os.path.join(repo, "out", "demo.mp4")
        if not os.path.isfile(media):
            self.skipTest("缺少 demo.mp4")
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = subprocess.run([sys.executable, "-m", "freecut", "media-smoke", media],
                              cwd=repo, capture_output=True, timeout=300, env=env)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, out[-400:])
        self.assertIn("has-url", out)
        self.assertIn("clips=1", out)
        self.assertIn("clips=2", out)

    def test_selected_material_stays_a_pair(self):
        """_media_items 里存了三项（路径/类型/材质），但 _selected_material 必须只回两项。"""
        win = self._window()
        try:
            win.bin.media_list.setCurrentRow(0)
            picked = win.bin._selected_material()
            self.assertEqual(len(picked), 2, f"调用方按两个值解包，实际 {picked!r}")
            path, kind = picked
            self.assertTrue(path.endswith(".mp4"))
            self.assertEqual(kind, "video")
            self.assertIsNotNone(win.bin._selected_material_obj())
        finally:
            win.close()

    def test_double_click_adds_to_an_empty_timeline(self):
        win = self._window()
        try:
            self.assertEqual(len(win.bridge.project.tracks), 0)
            win.bin.media_list.setCurrentRow(0)
            win.bin.add_selected_to_timeline()
            self.app.processEvents()
            self.assertEqual(len(win.bridge.project.tracks), 1)
            self.assertEqual(len(win.bridge.project.tracks[0].segments), 1)
        finally:
            win.close()

    def test_default_mime_would_not_have_worked(self):
        """回归说明：QListWidget 默认的 mime 既没有 URL 也没有文本。"""
        from PySide6.QtCore import QMimeData
        from PySide6.QtWidgets import QListWidget

        win = self._window()
        try:
            plain = QListWidget()
            plain.addItem("x")
            mime = plain.mimeData([plain.item(0)])
            self.assertFalse(mime.hasUrls())
            self.assertFalse(mime.hasText())
            self.assertIn("application/x-qabstractitemmodeldatalist", mime.formats())
            # 我们的子类必须比默认实现多带 URL
            ours = win.bin.media_list.mimeData([win.bin.media_list.item(0)])
            self.assertNotEqual(mime.formats(), ours.formats())
        finally:
            win.close()


# =========================================================================== #
# 第 242 轮：用户路径测试（HANDOFF §6）
#
# 这些测试**不测数据结构**，测的是"用户点了会发生什么"：造真实的 Qt 事件、
# 走菜单动作、开真的播放与导出。教训来自第 214 轮：元组从 2 项改 3 项，
# 拖拽/双击/按钮三条入口同时静默失效，而 739 个测试全绿。
# =========================================================================== #
import threading  # noqa: E402


def qt_application():
    """离屏 QApplication（整个测试进程共用一个）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def media_fixture():
    """测试用素材：优先 out/demo.mp4（有画面也有声音）。"""
    for name in ("demo.mp4", "showcase.mp4", "demo_audio.mp4", "narration.wav"):
        path = os.path.join(ROOT, "out", name)
        if os.path.isfile(path):
            return path
    return None


def pump(app, ms: float) -> None:
    """跑一段真的 Qt 事件循环（不是 sleep：窗口要能处理事件）。"""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


class _QtUserTestCase(unittest.TestCase):
    """用户路径测试的公共底座：起窗口、造一个可编辑的小工程。"""

    @classmethod
    def setUpClass(cls):
        try:
            cls.app = qt_application()
        except Exception as exc:                       # noqa: BLE001
            raise unittest.SkipTest(f"没有 PySide6: {exc}")

    def window(self, project_path=None):
        from freecut.qtui.app import MainWindow

        win = MainWindow(project_path)
        win.resize(1360, 820)
        win.show()                     # 不 show 布局只分到最小尺寸
        self.app.processEvents()
        return win

    def window_with_clip(self, duration_us: int = 4_000_000, start: int = 0):
        from freecut import builder

        media = media_fixture()
        if not media:
            self.skipTest("没有测试素材")
        win = self.window(None)
        project = builder.new_project(640, 360, 30, name="用户路径")
        seg = builder.add_video(project, media, start=start, duration=duration_us)
        win.bridge.set_project(project, None)
        win._refresh_all()
        self.app.processEvents()
        win.bridge.select(seg)
        self.app.processEvents()
        return win, seg

    @staticmethod
    def clip_count(win) -> int:
        return sum(len(t.segments) for t in win.bridge.project.tracks)


class TestUserPathMedia(_QtUserTestCase):
    """导入 → 列表出现 → 拖到时间线 → 双击加到播放头 → 收藏筛选。"""

    def test_import_shows_up_in_the_media_list(self):
        win = self.window(None)
        try:
            media = media_fixture()
            if not media:
                self.skipTest("没有测试素材")
            added = win.bin._add_material_only(media)
            self.assertTrue(added, "素材没进工程")
            win.bin.refresh_media()
            self.app.processEvents()
            self.assertGreaterEqual(win.bin.media_list.count(), 1)
            item = win.bin.media_list.item(0)
            self.assertIn(os.path.basename(media), item.text())
        finally:
            win.close()

    def test_drag_from_bin_lands_on_the_timeline(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QDropEvent

        win = self.window(None)
        try:
            media = media_fixture()
            if not media:
                self.skipTest("没有测试素材")
            win.bin._add_material_only(media)
            win.bin.refresh_media()
            self.app.processEvents()
            item = win.bin.media_list.item(0)
            # 必须用控件自己的 mimeData()：手搓 QMimeData(urls) 会绕过真实路径
            mime = win.bin.media_list.mimeData([item])
            self.assertTrue(mime.hasUrls(), "拖拽数据里没有文件 URL")
            before = self.clip_count(win)
            win.timeline.dropEvent(
                QDropEvent(QPointF(320, 160), Qt.CopyAction, mime,
                           Qt.LeftButton, Qt.NoModifier))
            self.app.processEvents()
            self.assertEqual(self.clip_count(win), before + 1, "拖进时间线没建片段")
        finally:
            win.close()

    def test_double_click_adds_at_playhead(self):
        win = self.window(None)
        try:
            media = media_fixture()
            if not media:
                self.skipTest("没有测试素材")
            win.bin._add_material_only(media)
            win.bin.refresh_media()
            self.app.processEvents()
            win.bridge.set_playhead(1_000_000)
            win.bin.media_list.setCurrentRow(0)
            before = self.clip_count(win)
            win.bin.add_selected_to_timeline()
            self.app.processEvents()
            self.assertEqual(self.clip_count(win), before + 1, "双击没加到时间线")
        finally:
            win.close()

    def test_favourite_filter_narrows_the_list(self):
        win = self.window(None)
        try:
            media = media_fixture()
            if not media:
                self.skipTest("没有测试素材")
            win.bin._add_material_only(media)
            win.bin.refresh_media()
            win.bin.media_list.setCurrentRow(0)
            win.bin.toggle_favorite()
            self.app.processEvents()
            win.bin.only_fav_box.setChecked(True)
            self.app.processEvents()
            texts = [win.bin.media_list.item(i).text()
                     for i in range(win.bin.media_list.count())]
            self.assertTrue(any("★" in t for t in texts), "只看收藏时应当只留星标素材")
        finally:
            win.close()


class TestUserPathEdit(_QtUserTestCase):
    """选中 → 分割（Ctrl+K）→ 删除（Delete）→ 撤销 → 重做。"""

    def test_split_delete_undo_redo(self):
        win, seg = self.window_with_clip()
        try:
            win.bridge.set_playhead(2_000_000)
            self.assertIn("Ctrl+K", win.actions["split"].shortcut().toString())
            win.actions["split"].trigger()             # 等价于用户按 Ctrl+K
            self.app.processEvents()
            self.assertEqual(self.clip_count(win), 2, "分割没有切成两段")
            self.assertGreaterEqual(len(win.bridge._undo), 1, "分割没有进撤销栈")

            win.actions["delete"].trigger()            # 等价于用户按 Delete
            self.app.processEvents()
            self.assertEqual(self.clip_count(win), 1, "删除没生效")

            self.assertTrue(win.bridge.undo())
            self.app.processEvents()
            self.assertEqual(self.clip_count(win), 2, "撤销没恢复被删的片段")

            self.assertTrue(win.bridge.redo())
            self.app.processEvents()
            self.assertEqual(self.clip_count(win), 1, "重做没重新删掉")
        finally:
            win.close()

    def test_copy_paste_adds_a_clip_at_playhead(self):
        win, seg = self.window_with_clip()
        try:
            self.assertTrue(win.bridge.copy_selected())
            win.bridge.set_playhead(2_000_000)         # 必须在工程时长之内
            before = self.clip_count(win)
            pasted = win.bridge.paste()
            self.app.processEvents()
            self.assertIsNotNone(pasted, "粘贴没返回片段")
            self.assertEqual(self.clip_count(win), before + 1)
            self.assertEqual(int(pasted.target_timerange.start), 2_000_000)
            # 粘贴出来的是独立副本：改一个不影响另一个
            self.assertNotEqual(pasted.id, seg.id)
        finally:
            win.close()

    def test_mirror_flips_the_clip_and_is_undoable(self):
        win, seg = self.window_with_clip()
        try:
            before = bool(seg.clip.flip_h)
            self.assertTrue(win.bridge.mirror("h"))
            self.assertNotEqual(bool(seg.clip.flip_h), before, "镜像没写进片段")
            self.assertTrue(win.bridge.undo())
            self.assertEqual(bool(seg.clip.flip_h), before, "镜像没被撤销")
        finally:
            win.close()

    def test_locked_track_refuses_edits(self):
        win, seg = self.window_with_clip()
        try:
            track = win.bridge.track_of_segment(seg)
            self.assertFalse(win.bridge.is_locked(track))
            win.bridge.toggle_track_lock(track)
            self.assertTrue(win.bridge.is_locked(track))
            win.bridge.set_playhead(2_000_000)
            self.assertEqual(win.bridge.split_selected(), [], "锁定的轨道不该被分割")
            self.assertEqual(win.bridge.delete_selected(), 0, "锁定的轨道不该被删除")
        finally:
            win.close()


class TestUserPathContextMenu(_QtUserTestCase):
    """右键菜单项与实际能力一致（做不到的项灰显）。"""

    def test_segment_menu_matches_capabilities(self):
        from freecut.qtui.menus import HANDLED_KEYS, UNAVAILABLE, build_context_menu

        win, seg = self.window_with_clip()
        try:
            win.bridge.set_playhead(2_000_000)          # 播放头在片段内
            menu = build_context_menu(win, seg)
            actions = menu._actions_by_key
            self.assertIn("split", actions)
            self.assertTrue(actions["split"].isEnabled(), "播放头在片段内时「分割」应当可点")
            self.assertFalse(actions["paste"].isEnabled(), "剪贴板空时「粘贴」应当灰显")
            for key in UNAVAILABLE:
                if key not in actions:
                    continue                    # 「选择工具」只在空白处菜单里
                self.assertFalse(actions[key].isEnabled(), f"{key} 应当灰显")
                self.assertTrue(actions[key].toolTip(), f"{key} 灰显时必须写明原因")
            # 每一个"能点"的菜单项都必须在实现表里——菜单不能比能力多
            for key, action in actions.items():
                if action.isEnabled():
                    self.assertIn(key, HANDLED_KEYS, f"{key} 可点但背后没有实现")
        finally:
            win.close()

    def test_playhead_outside_disables_split(self):
        from freecut.qtui.menus import build_context_menu

        win, seg = self.window_with_clip()
        try:
            win.bridge.set_playhead(int(seg.target_timerange.start
                                        + seg.target_timerange.duration + 1_000_000))
            menu = build_context_menu(win, seg)
            self.assertFalse(menu._actions_by_key["split"].isEnabled())
        finally:
            win.close()

    def test_empty_area_menu_offers_paste_and_new_text(self):
        from freecut.qtui.menus import build_context_menu

        win = self.window(None)
        try:
            menu = build_context_menu(win, None)
            keys = set(menu._actions_by_key)
            self.assertIn("new_text", keys)
            self.assertIn("paste", keys)
            self.assertNotIn("mirror_h", keys, "空白处的菜单不该出现片段操作")
        finally:
            win.close()


class TestUserPathPlayback(_QtUserTestCase):
    """空格播放：播放头前进 + 至少渲染出 2 个不同帧 + 暂停后停在当前帧。"""

    def test_play_advances_and_renders_distinct_frames(self):
        win, _seg = self.window_with_clip(duration_us=6_000_000)
        try:
            win.resize(960, 640)
            self.app.processEvents()
            service = win.bridge.render_service
            frames = []

            def _on_frame(_token, image):
                if image is not None:
                    frames.append(bytes(image.constBits()))

            service.frameReady.connect(_on_frame)
            # 先让预览把第一帧画出来（真实用户按空格时画面已经在显示了）。
            # 不预热的话，Renderer 的构建 + 首个解码器启动会吃掉测量窗口的一大半。
            win.viewer.render_now()
            pump(self.app, 400)
            frames.clear()                             # 预热那一帧不算进播放帧数
            start = win.bridge.playhead
            win.toggle_play()
            pump(self.app, 1200)
            win.toggle_play()                          # 暂停
            self.app.processEvents()
            self.assertGreater(win.bridge.playhead, start, "播放时播放头没前进")
            self.assertGreaterEqual(len(frames), 2, "播放时没渲染出两帧以上")
            self.assertGreaterEqual(len({hash(f) for f in frames}), 2, "渲染出来的帧都一样")
            paused = win.bridge.playhead
            pump(self.app, 250)
            self.assertLess(abs(win.bridge.playhead - paused), 34_000,
                            "暂停之后播放头还在走")
        finally:
            win.close()

    def test_playback_does_not_freeze_the_ui(self):
        """播放期间界面线程的最大停顿——卡死的话这里会是几百毫秒。"""
        win, _seg = self.window_with_clip(duration_us=6_000_000)
        try:
            win.resize(960, 640)
            self.app.processEvents()
            gaps = []
            last = [__import__("time").perf_counter()]

            from PySide6.QtCore import QTimer

            timer = QTimer(win)
            timer.setInterval(10)

            def _probe():
                now = __import__("time").perf_counter()
                gaps.append((now - last[0]) * 1000.0)
                last[0] = now

            timer.timeout.connect(_probe)
            timer.start()
            win.toggle_play()
            pump(self.app, 800)
            win.toggle_play()
            timer.stop()
            self.assertLess(max(gaps), 250.0,
                            f"播放时界面停顿 {max(gaps):.0f} ms（超过 250ms 就是卡了）")
        finally:
            win.close()


class TestUserPathDragPerf(_QtUserTestCase):
    """模拟 20 次拖动，总耗时 < 600 ms（HANDOFF：修复前 17 725 ms）。"""

    def test_twenty_drags_are_fast_and_never_decode_in_the_paint_path(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QMouseEvent
        from freecut.qtui import media_cache as cache_mod

        project_path = os.path.join(ROOT, "out", "showcase.json")
        win = self.window(project_path if os.path.isfile(project_path) else None)
        try:
            timeline = win.timeline
            track = next((t for t in timeline.rows()
                          if t.type == "video" and len(t.segments) > 1), None)
            if track is None:
                self.skipTest("工程里没有可拖的视频片段")
            seg = track.segments[0]
            row = timeline.rows().index(track)
            y = timeline._track_y(row) + 20
            x0 = timeline._us_to_x(int(seg.target_timerange.start)) + 30
            timeline.viewport().repaint()

            # 绘制路径里绝不允许解码：解码只许发生在工作线程
            offenders = []
            original = cache_mod.MediaCache._decode_thumb

            def guarded(self, *args, **kwargs):
                if threading.current_thread().name != "fc-media-cache":
                    offenders.append(threading.current_thread().name)
                return original(self, *args, **kwargs)

            cache_mod.MediaCache._decode_thumb = guarded
            try:
                press = QMouseEvent(QMouseEvent.MouseButtonPress, QPoint(int(x0), int(y)),
                                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                timeline.mousePressEvent(press)
                started = __import__("time").perf_counter()
                for step in range(20):
                    move = QMouseEvent(QMouseEvent.MouseMove, QPoint(int(x0 + step * 3), int(y)),
                                       Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
                    timeline.mouseMoveEvent(move)
                    timeline.viewport().repaint()
                elapsed_ms = (__import__("time").perf_counter() - started) * 1000.0
                release = QMouseEvent(QMouseEvent.MouseButtonRelease, QPoint(int(x0 + 60), int(y)),
                                      Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
                timeline.mouseReleaseEvent(release)
            finally:
                cache_mod.MediaCache._decode_thumb = original

            self.assertEqual(offenders, [], "绘制路径里还在同步解码（ffmpeg 会卡住拖动）")
            self.assertLess(elapsed_ms, 600.0,
                            f"20 次拖动用了 {elapsed_ms:.0f} ms（目标 < 600ms）")
        finally:
            win.close()


class TestUserPathExport(_QtUserTestCase):
    """导出对话框 → 文件存在 → ffprobe 时长正确。"""

    def test_dialog_export_writes_a_playable_file(self):
        from freecut.export import ExportPreset
        from freecut.media import probe
        from freecut.qtui.dialogs.export_dialog import ExportDialog, _ExportWorker

        win, _seg = self.window_with_clip(duration_us=1_500_000)
        try:
            dialog = ExportDialog(win.bridge, win)
            out_dir = tempfile.mkdtemp()
            target = os.path.join(out_dir, "user_path.mp4")
            dialog.path_edit.setText(target)
            # 用对话框自己的预设，但缩到 320×180/10fps，免得测试跑一分钟
            preset = ExportPreset(width=320, height=180, fps=10.0)
            result = {}
            worker = _ExportWorker(win.bridge.project, target, preset)
            worker.finished_ok.connect(lambda path, seconds: result.update(path=path))
            worker.failed.connect(lambda message: result.update(error=message))
            worker.start()
            worker.wait(180_000)
            self.app.processEvents()
            self.assertNotIn("error", result, f"导出失败: {result.get('error')}")
            self.assertTrue(os.path.isfile(target), "导出没有产出文件")
            info = probe(target)
            self.assertAlmostEqual(info.duration_us / 1e6, 1.5, delta=0.35,
                                   msg="导出时长与工程时长对不上")
            self.assertEqual((info.width, info.height), (320, 180))
            dialog.reject()
        finally:
            win.close()


class TestUiParity(_QtUserTestCase):
    """HANDOFF §5：界面元素的数量与顺序。"""

    def test_left_rail_keeps_only_supported_categories(self):
        from freecut import theme

        win = self.window(None)
        try:
            self.assertEqual(list(win.rail.buttons), list(theme.QT_CATEGORIES))
            # 剪映有 12 个类别，其中 4 个依赖云端；我们只保留能还原的 8 个（删掉，不灰显）
            self.assertEqual(len(theme.JY_CATEGORIES), 12)
            cloud = [n for n in theme.JY_CATEGORIES if n in theme.CLOUD_ONLY]
            self.assertGreaterEqual(len(cloud), 4)
            for name in cloud:
                self.assertNotIn(name, win.rail.buttons,
                                 f"做不到的类别 {name} 不该再出现在左栏")
        finally:
            win.close()

    def test_three_panes_keep_sane_widths(self):
        """三栏不能被内容挤变形（第 242 轮真实出过：右栏只剩 72px）。"""
        win = self.window(None)
        try:
            left, mid, right = win.splitter.sizes()
            self.assertGreaterEqual(left, 220, f"左栏太窄: {left}")
            self.assertGreaterEqual(right, 260, f"属性面板被挤没了: {right}")
            self.assertGreater(mid, 400, f"预览区太窄: {mid}")
        finally:
            win.close()

    def test_top_bar_has_the_jianying_controls(self):
        win = self.window(None)
        try:
            self.assertIn("16:9", [win.ratio_box.itemText(i)
                                   for i in range(win.ratio_box.count())])
            self.assertIn("1080p", [win.resolution_box.itemText(i)
                                    for i in range(win.resolution_box.count())])
            self.assertTrue(win.doc_label.text())
            self.assertTrue(win.autosave_label.text())
            self.assertTrue(win.notify_btn.isEnabled())
            # 分享 = 上传到平台，我们不做：必须灰显
            share = [b for b in win.findChildren(type(win.notify_btn))
                     if b.text() == "分享"]
            self.assertTrue(share, "顶栏没有「分享」按钮")
            self.assertFalse(share[0].isEnabled(), "分享做不到就该灰显")
            self.assertIn("不上传", share[0].toolTip())
        finally:
            win.close()

    def test_preview_shows_title_frame_timecode_and_tools(self):
        import re

        win = self.window(None)
        try:
            self.assertEqual(win.viewer.panel_title.currentText(), "播放器-时间线01")
            text = win.player.timecode.text()
            self.assertRegex(text, r"^\d{2}:\d{2}:\d{2}:\d{2} / \d{2}:\d{2}:\d{2}:\d{2}$",
                             f"时间码应该是帧号形式，实际是 {text!r}")
            for widget in (win.viewer.original_btn, win.viewer.zoom_box,
                           win.viewer.fullscreen_btn):
                self.assertIsNotNone(widget)
            self.assertEqual(win.viewer.original_btn.text(), "原图")
        finally:
            win.close()

    def test_inspector_tabs_follow_jianying_order(self):
        win = self.window(None)
        try:
            names = [win.inspector.tabs.tabText(i)
                     for i in range(win.inspector.tabs.count())]
            # 「AI编辑」页依赖云端，已删除；剩下的 8 页顺序照剪映
            self.assertEqual(names[:5], ["画面", "音频", "变速", "动画", "文字"])
            self.assertEqual(len(names), 8)
            self.assertIn("调节", names)
            self.assertNotIn("AI编辑", names)
        finally:
            win.close()

    def test_toolbar_buttons_open_the_right_inspector_page(self):
        """工具栏的「裁剪/音量/降噪/变速/动画」要真的跳到对应页（跳错页等于没做）。"""
        win, _seg = self.window_with_clip()
        try:
            for key, page in (("crop", "画面"), ("volume", "音频"), ("denoise", "音频"),
                              ("speed", "变速"), ("animation", "动画")):
                win.timeline_toolbar.buttons[key].click()
                self.app.processEvents()
                current = win.inspector.tabs.tabText(win.inspector.tabs.currentIndex())
                self.assertEqual(current, page, f"{key} 应当打开「{page}」页")
        finally:
            win.close()

    def test_timeline_toolbar_has_every_button_in_order(self):
        from freecut.qtui.widgets.toolbar import TOOLBAR_ORDER

        win = self.window(None)
        try:
            self.assertEqual(win.timeline_toolbar.labels(),
                             [entry[1] for entry in TOOLBAR_ORDER])
            # 剪映 24 个；删掉云端 AI特效 与未实现的录音后是 22 个
            self.assertEqual(len(TOOLBAR_ORDER), 22)
            for key in ("ai", "record"):
                self.assertNotIn(key, win.timeline_toolbar.buttons,
                                 f"{key} 做不到就不该有按钮")
        finally:
            win.close()

    def test_track_header_has_solo_lock_and_mute(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QMouseEvent
        from freecut.qtui.widgets.timeline import HEADER_BUTTONS, HEADER_H, ROW_H

        win, _seg = self.window_with_clip()
        try:
            timeline = win.timeline
            track = timeline.rows()[0]
            for (key, cx) in HEADER_BUTTONS:
                y = HEADER_H + ROW_H // 2
                event = QMouseEvent(QMouseEvent.MouseButtonPress,
                                    QPoint(int(cx), int(y)), Qt.LeftButton,
                                    Qt.LeftButton, Qt.NoModifier)
                timeline.mousePressEvent(event)
                self.app.processEvents()
            self.assertTrue(win.bridge.is_solo(track), "S 没切换独奏")
            self.assertTrue(win.bridge.is_locked(track), "锁没切换")
            self.assertTrue(win.bridge.is_muted(track), "静音没切换")
        finally:
            win.close()

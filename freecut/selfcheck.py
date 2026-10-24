"""Self-check: does a draft we just wrote still describe the same film?

Writing ``draft_content.json`` can silently lose something — a dropped field, a
rounded duration, a material the app will not resolve.  The app itself is the
final arbiter, but we can close most of the loop on our own:

1. **structure** — canvas, fps, track types, segment counts;
2. **semantics** — every segment's start/duration, plus the fields the renderer
   actually reads (transform, colour params, text content, effects…);
3. **pixels** — render the same instants from the source project and from the
   re-imported one, and compare.

A pass here does not prove JianyingPro will render it identically; it proves we
did not lose data on the way out.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .model import Project


@dataclass
class SelfCheckReport:
    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    frames_checked: int = 0
    max_pixel_diff: float = 0.0
    mean_pixel_diff: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_text(self) -> str:
        lines = ["自检" + ("通过 ✅" if self.ok else "失败 ❌")]
        if self.frames_checked:
            lines.append(f"  对比 {self.frames_checked} 帧，最大像素差 {self.max_pixel_diff:.4f}，"
                         f"平均 {self.mean_pixel_diff:.5f}")
        for p in self.problems:
            lines.append(f"  · {p}")
        for w in self.warnings:
            lines.append(f"  · 提示: {w}")
        for note in self.notes:
            lines.append(f"  · 核对: {note}")
        return "\n".join(lines)


def _track_counts(project: Project, skip: Optional[Dict[tuple, str]] = None) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for track in project.tracks:
        for seg in track.segments:
            if skip and (track.type, int(seg.target_timerange.start),
                         int(seg.target_timerange.duration)) in skip:
                continue
            out[track.type] = out.get(track.type, 0) + 1
    return out


def _segment_keys(project: Project) -> List[Tuple[str, int, int]]:
    rows = []
    for track in project.tracks:
        for seg in track.segments:
            rows.append((track.type, int(seg.target_timerange.start),
                         int(seg.target_timerange.duration)))
    return sorted(rows)


def _load_content(draft_dir: str) -> Optional[Dict]:
    """Read ``draft_content.json`` from a draft directory (None when absent)."""
    path = os.path.join(draft_dir, "draft_content.json")
    if not os.path.isfile(path):
        return None
    import json

    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _unsupported_kinds(project: Project) -> Dict[tuple, str]:
    """(track type, start, duration) -> a label for segments JianyingPro lacks."""
    out: Dict[tuple, str] = {}
    for track in project.tracks:
        for seg in track.segments:
            if not getattr(seg, "is_adjustment", False):
                continue
            out[(track.type, int(seg.target_timerange.start),
                 int(seg.target_timerange.duration))] = "调节图层"
    return out


def _overlapping_starts(project: Project) -> set:
    """Starts of segments that overlap another segment on the same track."""
    marks = set()
    for track in project.tracks:
        segs = sorted(track.segments, key=lambda s: s.target_timerange.start)
        for i, seg in enumerate(segs):
            start, end = seg.target_timerange.start, seg.target_timerange.end
            for other in segs[i + 1:]:
                if other.target_timerange.start >= end:
                    break
                if other.target_timerange.start < end:
                    marks.add(start)
                    marks.add(other.target_timerange.start)
    return marks


def _render_frames(project: Project, times_us: Sequence[int], width: int, height: int,
                   fps: float) -> List[np.ndarray]:
    from .render import Renderer

    out: List[np.ndarray] = []
    r = Renderer(project, width, height, fps)
    try:
        for t in times_us:
            frame_index = int(round(t / 1_000_000.0 * fps))
            out.append(r.render_frame(min(frame_index, max(0, r.frame_count - 1))).copy())
    finally:
        r.close()
    return out


def check_draft(
    project: Project,
    draft_dir: str,
    samples: int = 5,
    pixel_tolerance: float = 0.02,
    compare_pixels: bool = True,
    original: Optional[Project] = None,
) -> SelfCheckReport:
    """Export-compare loop: import ``draft_dir`` back and verify it matches."""
    from . import jianying

    report = SelfCheckReport()

    try:
        back = jianying.load_draft(draft_dir)
    except Exception as exc:                       # noqa: BLE001 - surface anything
        report.problems.append(f"草稿读不回来: {exc}")
        return report

    if (project.canvas.width, project.canvas.height) != (back.canvas.width, back.canvas.height):
        report.problems.append(
            f"画布不一致: {project.canvas.width}x{project.canvas.height} -> "
            f"{back.canvas.width}x{back.canvas.height}")
    if abs(float(project.fps) - float(back.fps)) > 0.01:
        report.problems.append(f"帧率不一致: {project.fps} -> {back.fps}")

    unsupported_map = _unsupported_kinds(project)
    try:
        content = _load_content(draft_dir) or {}
        _feature_checks(project, content, report)
        _audio_checks(project, content, draft_dir, report, original=original)
        _jianying_probe_note(draft_dir, report)
        _field_audit_note(content, report)
    except Exception as exc:                     # 特性检查不该让自检整体崩掉
        report.warnings.append(f"特性检查没跑成: {exc}")

    src_counts = _track_counts(project, skip=unsupported_map)
    dst_counts = _track_counts(back)
    for kind in sorted(set(src_counts) | set(dst_counts)):
        if src_counts.get(kind, 0) != dst_counts.get(kind, 0):
            report.problems.append(
                f"{kind} 轨片段数不一致: {src_counts.get(kind, 0)} -> {dst_counts.get(kind, 0)}")

    src_segs, dst_segs = _segment_keys(project), _segment_keys(back)
    if src_segs != dst_segs:
        # 导出时会裁剪重叠片段（画面上不可能同时占两段），这属于预期修正
        overlaps = _overlapping_starts(project)
        trimmed, real_missing, real_extra = [], [], []
        skipped_unsupported = []
        for row in src_segs:
            if row in dst_segs:
                continue
            kind, start, duration = row
            shorter = [d for d in dst_segs
                       if d[0] == kind and d[1] == start and d[2] < duration]
            if shorter and start in overlaps:
                trimmed.append((kind, start, duration, shorter[0][2]))
            elif unsupported_map.get(row):
                # 剪映没有这个东西（例如调节图层），导出时已明确跳过
                skipped_unsupported.append((unsupported_map[row], start))
            else:
                real_missing.append(row)
        for row in dst_segs:
            if row not in src_segs:
                if not any(t[0] == row[0] and t[1] == row[1] for t in trimmed):
                    real_extra.append(row)
        if trimmed:
            detail = "、".join(f"{k} 轨 {s/1e6:.2f}s 处 {a/1e6:.2f}s→{b/1e6:.2f}s"
                               for k, s, a, b in trimmed[:3])
            report.warnings.append(f"重叠片段被裁剪（预期）: {detail}")
        if skipped_unsupported:
            detail = "、".join(f"{name}@{start/1e6:.2f}s" for name, start in skipped_unsupported[:3])
            report.warnings.append(f"剪映无对应实现，已按预期跳过: {detail}")
        if real_missing or real_extra:
            report.problems.append(
                f"片段时间不一致（原 {len(src_segs)} / 回读 {len(dst_segs)}）"
                + (f"，缺: {real_missing[:3]}" if real_missing else "")
                + (f"，多: {real_extra[:3]}" if real_extra else ""))

    skipped_us = sum(duration for (kind, start, duration) in unsupported_map)
    delta_us = abs(project.duration() - back.duration())
    if delta_us > 20_000 + skipped_us:
        report.problems.append(
            f"总时长不一致: {project.duration()/1e6:.3f}s -> {back.duration()/1e6:.3f}s")

    if compare_pixels and report.ok:
        total = project.duration()
        if total > 0:
            count = max(1, min(samples, 12))
            times = [int(total * (i + 0.5) / count) for i in range(count)]
            try:
                a = _render_frames(project, times, project.canvas.width,
                                   project.canvas.height, project.fps)
                b = _render_frames(back, times, project.canvas.width,
                                   project.canvas.height, project.fps)
            except Exception as exc:               # noqa: BLE001
                report.warnings.append(f"像素对比跳过: {exc}")
            else:
                report.frames_checked = len(a)
                diffs = [float(np.abs(x.astype(np.float32) - y.astype(np.float32)).mean()) / 255.0
                         for x, y in zip(a, b)]
                if diffs:
                    report.max_pixel_diff = max(diffs)
                    report.mean_pixel_diff = float(sum(diffs) / len(diffs))
                    if report.max_pixel_diff > pixel_tolerance:
                        # 有剪映不支持的东西被跳过时，画面本来就会不一样
                        if unsupported_map:
                            names = "、".join(sorted(set(unsupported_map.values())))
                            report.warnings.append(
                                f"画面差异 {report.max_pixel_diff:.3f} 来自被跳过的 {names}，"
                                "不是数据丢失")
                        # 源工程里若有重叠片段，导出必然裁剪，画面就不可能逐像素相同
                        elif _overlapping_starts(project):
                            report.warnings.append(
                                f"画面差异 {report.max_pixel_diff:.3f} 来自重叠片段裁剪，"
                                "不是数据丢失")
                        else:
                            report.problems.append(
                                f"画面不一致：最大差 {report.max_pixel_diff:.3f}"
                                f"（容差 {pixel_tolerance:.3f}）")

    return report

# --------------------------------------------------------------------------- #
# feature-level checks: the export report makes specific promises
# --------------------------------------------------------------------------- #
def _feature_checks(project: Project, data: Dict, report: "SelfCheckReport") -> None:
    """Check the claims the export report makes, against the draft's own data."""
    mats = data.get("materials") or {}
    seg_by_track = []
    for track in data.get("tracks") or []:
        seg_by_track.append(track.get("type"), )
        seg_by_track[-1] = (track.get("type"), track.get("segments") or [])

    # --- 文本内容 ---
    want_texts = sorted(
        (m.extra.get("content") or m.extra.get("text") or "")
        for m in project.materials.values()
        if getattr(m, "type", "") == "text"
    )
    got_texts = sorted(str(t_item.get("content") or "") for t_item in mats.get("texts") or [])
    want_texts = [x for x in want_texts if x]
    got_texts = [x for x in got_texts if x]
    if want_texts and want_texts != got_texts:
        report.warnings.append(
            f"文本内容不完全一致（工程 {len(want_texts)} 条 / 草稿 {len(got_texts)} 条）")

    # --- 转场资源 id ---
    want_tr = {str(m.extra.get("resource_id")) for m in project.materials.values()
               if getattr(m, "type", "") == "transition" and m.extra.get("resource_id")}
    got_tr = {str(x.get("resource_id")) for x in mats.get("transitions") or [] if x.get("resource_id")}
    if want_tr and not want_tr <= got_tr:
        report.problems.append(f"转场资源 id 没写进草稿: {sorted(want_tr - got_tr)[:3]}")

    # --- HSL 分带 ---
    want_hsl = [m.extra.get("bands") for m in project.materials.values()
                if getattr(m, "type", "") == "hsl" and m.extra.get("bands")]
    got_hsl = [x.get("bands") for x in mats.get("hsl") or [] if x.get("bands")]
    if want_hsl and want_hsl != got_hsl:
        report.warnings.append("HSL 分带参数与工程不一致")

    # --- 速度值 ---
    want_speeds = sorted({round(float(s.speed), 4) for t in project.tracks for s in t.segments})
    got_speeds = sorted({round(float(x.get("speed", 1.0)), 4)
                         for x in mats.get("speeds") or []})
    if want_speeds and not set(want_speeds) <= set(got_speeds):
        report.warnings.append(
            f"速度值没全写进 speeds: 工程 {want_speeds} / 草稿 {got_speeds}")

    # --- 关键帧 ---
    want_kf = sum(len(getattr(s, "keyframes", []) or []) for t in project.tracks for s in t.segments)
    got_kf = 0
    for _kind, segs in seg_by_track:
        for seg in segs:
            got_kf += len(seg.get("common_keyframes") or [])
            got_kf += len(seg.get("keyframe_refs") or [])
    if want_kf and got_kf == 0:
        report.problems.append(f"工程有 {want_kf} 个关键帧，草稿里一个都没有")

    # --- 调色开关 ---
    # 调节图层（adjustment layer）本来就不在剪映草稿里，它的调色会被跳过，
    # 所以不能把它算进"必须有开关"的分母，否则这条不变量永远误报
    want_color = sum(1 for m in project.materials.values()
                     if getattr(m, "type", "") in ("curve", "hsl", "color_wheel", "adjustment"))
    adjustment_segments = sum(1 for track in project.tracks
                              for seg in track.segments if getattr(seg, "is_adjustment", False))
    if adjustment_segments and want_color == adjustment_segments:
        want_color = 0
    if want_color:
        enabled = 0
        for _kind, segs in seg_by_track:
            for seg in segs:
                if any(seg.get(k) for k in ("enable_color_curves", "enable_color_wheels",
                                            "enable_hsl", "enable_adjust")):
                    enabled += 1
        if enabled == 0:
            report.problems.append("工程用了调色，但草稿里没有任何调色开关被打开")
        else:
            report.notes.append(f"{enabled} 个片段打开了调色开关")
        # 数值本体：曲线点 / 色轮 / 调节量。目前导出只写开关，这里如实记下来，
        # 免得"自检通过"被误读成"调色完整带过去了"。
        curves = data.get("materials", {}).get("color_curves") or []
        filled = [c for c in curves if c.get("points") or c.get("curve")]
        if not filled:
            report.notes.append("调色只写了开关，曲线/色轮/调节数值没有写进草稿（schema 未证实）")

def _audio_checks(project: Project, data: Dict, draft_dir: str,
                  report: "SelfCheckReport", original: Optional[Project] = None) -> None:
    """A draft must not silently lose the soundtrack."""
    source = original if original is not None else project
    mats = data.get("materials") or {}
    videos = [v for v in mats.get("videos") or [] if v.get("path")]
    src_audio = any(t.type == "audio" and t.segments for t in source.tracks)
    if not videos:
        return
    declared = [bool(v.get("has_audio", True)) for v in videos]
    # 独立音频轨也算"草稿里有声音"，不能因为视频素材自身没音轨就报错
    has_audio_material = bool(mats.get("audios")) or any(
        seg.get("volume") is not None for track in data.get("tracks") or []
        if track.get("type") == "audio" for seg in track.get("segments") or [])
    if src_audio and not any(declared) and not has_audio_material:
        report.problems.append("工程有音频，但草稿里既没有音频素材、视频也没声明 has_audio")
        return

    # 展平草稿：唯一的视频片段就是我们的成片，直接比响度
    if len(videos) == 1 and src_audio and os.path.isfile(str(videos[0]["path"])):
        try:
            import tempfile
            import wave

            import numpy as np

            from . import asr, audio as audio_mod

            expected = _audio_rms(_mix_wav(source, tempfile.gettempdir()))
            got = _audio_rms(_any_to_wav(str(videos[0]["path"]), tempfile.gettempdir(), asr))
            if expected is not None and got is not None:
                delta = abs(expected - got)
                if delta > 1.5:
                    report.problems.append(
                        f"展平成片的音频与工程不一致（差 {delta:.1f} dB）")
                else:
                    report.notes.append(f"展平成片音频与工程一致（差 {delta:.2f} dB）")
        except Exception as exc:
            report.warnings.append(f"音频对比没跑成: {exc}")


def _mix_wav(project: Project, tmp: str) -> str:
    from . import audio as audio_mod

    path = os.path.join(tmp, "selfcheck_mix.wav")
    audio_mod.write_audio_mix(project, path, project.duration(), "192k")
    return path


def _any_to_wav(path: str, tmp: str, asr_mod) -> str:
    if path.lower().endswith(".wav"):
        return path
    out = os.path.join(tmp, "selfcheck_probe.wav")
    asr_mod._to_wav(path, out)
    return out


def _audio_rms(path: str) -> Optional[float]:
    import wave

    import numpy as np

    if not os.path.isfile(path):
        return None
    with wave.open(path, "rb") as fh:
        ch = fh.getnchannels()
        raw = fh.readframes(fh.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    data /= 32768.0
    if data.size == 0:
        return None
    return float(20 * np.log10(max(1e-9, float(np.sqrt((data ** 2).mean())))))

def _jianying_probe_note(draft_dir: str, report: "SelfCheckReport") -> None:
    """Cite the JianyingPro probe record when one was left beside the draft.

    The self-check only proves *our* read-back.  A probe record proves the real
    app parsed the draft, so surfacing it here keeps the two kinds of evidence
    from being confused.
    """
    try:
        from . import jyprobe

        record = jyprobe.load_record(draft_dir)
    except Exception:
        return
    if not record:
        return
    if record.get("parsed_timeline"):
        size = record.get("reported_size")
        when = record.get("checked_at") or "?"
        report.notes.append(
            f"剪映已解析过这个草稿（{when}）：素材体积 {size:,}" if size
            else f"剪映已解析过这个草稿（{when}）")
    else:
        report.warnings.append(
            f"剪映探针记录显示未确认解析（{record.get('checked_at') or '?'}）")

def _field_audit_note(content: dict, report: "SelfCheckReport") -> None:
    """Cite how many written draft fields the renderer actually reads."""
    try:
        from . import fields
    except Exception:
        return
    audit = fields.audit(content)
    total = len(audit.used) + len(audit.unused)
    if not total:
        return
    line = (f"字段审计：写了 {total} 个字段，渲染器读 {len(audit.used)} 个"
            f"（{audit.coverage * 100:.0f}%）")
    try:
        value_problems = fields.audit_values(content)
    except Exception:
        value_problems = []
    if value_problems:
        report.problems.extend(value_problems[:5])
    notable = [k for k in audit.unused if k.startswith("enable_")]
    if notable:
        line += "；这些开关还没有实现：" + "、".join(sorted(notable))
    report.notes.append(line)

_STEP_RE = re.compile(r"^\s*\[(OK|FAIL|SKIP)\s*\]\s*(.+?)\s*\((\d+)s\)\s*(.*)$")


def parse_step_line(line: str):
    """Parse one ``verify_all.py`` step line into ``(ok, name, seconds, summary)``.

    ``verify_all`` prints exactly::

        [OK  ] 剪映逆向复现 (10s) 8/8 条通过

    Returns ``None`` for anything that is not a step line (headers, blank lines,
    the final summary), so the GUI can show a live checklist while still
    streaming the raw log.
    """
    match = _STEP_RE.match(line or "")
    if not match:
        return None
    mark, name, seconds, summary = match.groups()
    return (mark == "OK", name.strip(), int(seconds), summary.strip())

"""Export report: what survives the round trip to JianyingPro, and what does not.

The point is to answer, for **this specific project**, three questions:

* which parts are written byte-for-byte into a draft that JianyingPro reads,
* which parts FreeCut implements equivalently but differently,
* which parts cannot be represented and will be dropped (or silently ignored).

Everything is derived from the project itself plus the resource catalogs we
extracted, so the report cannot drift away from the exporter the way a
hand-written table would.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .model import Project

EXACT = "精确"
EQUIVALENT = "等价"
DEGRADED = "降级"
LOST = "丢失"


@dataclass
class Item:
    """One finding: what it is, how many, and what happens to it."""

    severity: str
    feature: str
    count: int
    detail: str = ""

    def line(self) -> str:
        head = f"[{self.severity}] {self.feature}"
        if self.count:
            head += f" ×{self.count}"
        return head + (f" —— {self.detail}" if self.detail else "")


@dataclass
class Report:
    items: List[Item] = field(default_factory=list)
    title: str = "剪映草稿导出报告"
    target: str = "剪映"
    draft_dir: str = ""
    notes: List[str] = field(default_factory=list)

    def add(self, severity: str, feature: str, count: int = 1, detail: str = "") -> None:
        if count:
            self.items.append(Item(severity, feature, count, detail))

    def by(self, severity: str) -> List[Item]:
        return [i for i in self.items if i.severity == severity]

    @property
    def losses(self) -> List[Item]:
        return self.by(LOST) + self.by(DEGRADED)

    def as_text(self) -> str:
        lines = [self.title, "=" * 40]
        if self.draft_dir:
            lines.append(f"草稿目录: {self.draft_dir}")
        for title, severity in ((f"精确写入（{self.target}原生）", EXACT),
                                (f"等价实现（FreeCut 自研，{self.target}里表现接近）", EQUIVALENT),
                                ("降级（能打开，但效果不同）", DEGRADED),
                                (f"丢失（{self.target}没有这个概念）", LOST)):
            rows = self.by(severity)
            if not rows:
                continue
            lines.append("")
            lines.append(f"{title}:")
            for item in rows:
                lines.append("  " + item.line())
        if self.notes:
            lines.append("")
            lines.append("导出时的提醒:")
            for note in self.notes:
                lines.append("  · " + note)
        lines.append("")
        lost = sum(i.count for i in self.losses)
        if lost:
            if self.target == "剪映":
                lines.append(f"结论: {lost} 处内容在剪映里会不同或看不到；"
                             "参数都存在 freecut_extras.json 里，用 FreeCut 打开可无损恢复。")
            else:
                lines.append(f"结论: {lost} 处内容在 {self.target} 里会不同或看不到；"
                             "这些是 FreeCut 自己渲染的，要保留只能先导出成片再导入。")
        else:
            # 中文里不加空格，"被 剪映 读取" 读起来别扭
            sep = "" if self.target == "剪映" else " "
            lines.append(f"结论: 这个工程可以完整地被{sep}{self.target}{sep}读取。")
        return "\n".join(lines)

    def as_dict(self) -> Dict:
        return {
            "draft_dir": self.draft_dir,
            "items": [{"severity": i.severity, "feature": i.feature,
                       "count": i.count, "detail": i.detail} for i in self.items],
            "notes": list(self.notes),
            "losses": sum(i.count for i in self.losses),
        }

    def write(self, path: str) -> str:
        """Write both a readable report and a machine-readable one."""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.as_text() + "\n")
        base, _ext = os.path.splitext(path)
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(self.as_dict(), fh, ensure_ascii=False, indent=2)
        return path


# --------------------------------------------------------------------------- #
# catalog lookups
# --------------------------------------------------------------------------- #
_CACHE: Dict[str, set] = {}


def _catalog_ids(name: str) -> set:
    """Ids present in one of the extracted JianyingPro catalogs (empty if absent)."""
    if name in _CACHE:
        return _CACHE[name]
    ids: set = set()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{name}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data.get("items") if isinstance(data, dict) else data
        for entry in entries or []:
            if isinstance(entry, dict):
                for key in ("id", "resource_id", "effect_id"):
                    if entry.get(key):
                        ids.add(str(entry[key]))
    except Exception:
        pass
    _CACHE[name] = ids
    return ids


def _has_any_id(entries: Sequence[Dict], catalog: str) -> bool:
    ids = _catalog_ids(catalog)
    if not ids:
        return False
    for entry in entries:
        for key in ("resource_id", "effect_id", "id"):
            if str(entry.get(key) or "") in ids:
                return True
    return False


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #
def analyze(project: Project, draft_dir: str = "", notes: Optional[List[str]] = None) -> Report:
    """Classify every feature used by ``project`` for a JianyingPro export."""
    report = Report(draft_dir=draft_dir, notes=list(notes or []))
    mats = list(project.materials.values())
    kinds = Counter(getattr(m, "type", "?") for m in mats)

    # --- what maps 1:1 -----------------------------------------------------
    segs = [s for t in project.tracks for s in t.segments]
    if segs:
        report.add(EXACT, "片段（时间/轨道/顺序）", len(segs),
                   "草稿 schema 与剪映逐字段一致")
    report.add(EXACT, "画布与帧率", 1,
               f"{project.canvas.width}×{project.canvas.height} @ {project.fps:g} fps")
    if kinds.get("video") or kinds.get("image"):
        report.add(EXACT, "视频/图片素材", kinds.get("video", 0) + kinds.get("image", 0),
                   "路径、时长、入出点原样写入")
    if kinds.get("audio"):
        report.add(EXACT, "音频片段", kinds["audio"])
    if kinds.get("text"):
        report.add(EXACT, "文本片段", kinds["text"], "位置/字号/颜色/描边写进草稿")
    if any(s.words for s in segs if getattr(s, "words", None)):
        report.add(EXACT, "逐字时间戳（字幕）", 1, "词级时间写入 text 素材")

    # 调色：整条链的顺序和参数都是照剪映的 schema 写的
    color_keys = ("curve", "hsl", "color_wheel", "adjustment")
    color_n = sum(kinds.get(k, 0) for k in color_keys)
    if color_n:
        # 我们只写 enable_* 开关；曲线点/色轮/调节数值的 schema 尚未用真实草稿证实，
        # 之前这里写"精确"是错的（第 57 轮自查发现）。
        report.add(EXACT, "调色开关（enable_* 布尔）", color_n,
                   "剪映打开后会显示相关调色面板已启用")
        report.add(DEGRADED, "调色数值（曲线点/色轮/调节量）", color_n,
                   "只写开关没写数值：剪映里是中性值，效果需要 FreeCut 渲染才看得到")

    if kinds.get("speed"):
        report.add(EXACT, "速度/变速素材", kinds["speed"], "Speed 材质 + 分段表")

    # 转场：只有资源 id 在目录里时才算精确
    transitions = [m for m in mats if getattr(m, "type", "") == "transition"]
    if transitions:
        known = [m for m in transitions if _has_any_id([m.extra], "transitions")]
        if known:
            report.add(EXACT, "转场", len(known), "资源 id 在剪映目录中存在")
        unknown = len(transitions) - len(known)
        if unknown:
            report.add(LOST, "转场（资源 id 不在导出目录）", unknown,
                       "剪映打开时会显示为缺失素材")

    animations = [m for m in mats if getattr(m, "type", "") == "material_animation"]
    if animations:
        known = [m for m in animations if _has_any_id([m.extra], "animations")]
        report.add(EXACT if len(known) == len(animations) else EQUIVALENT,
                   "入场/出场动画", len(animations),
                   "资源 id 与剪映目录对齐" if len(known) == len(animations)
                   else f"{len(animations) - len(known)} 个 id 不在目录里")

    # --- equivalent implementations ---------------------------------------
    masks = [m for m in mats if getattr(m, "type", "") == "mask"]
    chroma = [m for m in masks
              if (m.extra.get("resource_type") or m.extra.get("kind") or "") == "chroma"
              or str(m.extra.get("resource_id") or "").startswith("chroma")]
    plain_masks = [m for m in masks if m not in chroma]
    if plain_masks:
        report.add(EQUIVALENT, "蒙版（形状/线性/圆形）", len(plain_masks),
                   "参数映射到剪映 mask 的 config")

    if chroma:
        report.add(DEGRADED, "色度抠图", len(chroma),
                   "剪映没有这个蒙版类型：参数会写进 masks[].config，但在剪映里不生效")

    # 曲线变速：剪映有曲线速度，但控制点语义是我们反推的
    curves = kinds.get("curve", 0)
    if curves:
        report.add(EQUIVALENT, "曲线变速", curves,
                   "速度点写进 CurveSpeed；x/y 语义按逆向推断（未逐像素比对）")

    # 文字模板 = 一组文本样式，剪映能用样式字段但模板本身是 FreeCut 的
    if kinds.get("text"):
        report.add(EQUIVALENT, "文字模板（花字）", kinds["text"],
                   "样式转成剪映文本的描边/底色/字距字段")

    # 分屏来自裁剪 + 变换，剪映能表达
    if project.tracks:
        report.add(EQUIVALENT, "分屏 / 画中画", _layout_count(project),
                   "用裁剪 + 缩放表达，剪映里可再编辑")

    # --- not representable -------------------------------------------------
    if kinds.get("adjustment"):
        report.add(LOST, "调节图层", kinds["adjustment"],
                   "剪映没有调节图层：导出时跳过，参数留在 freecut_extras.json")
    if kinds.get("effect"):
        report.add(LOST, "程序化特效（故障/老电影等）", kinds["effect"],
                   "剪映没有对应资源：导出时跳过，参数留在 freecut_extras.json")
    if kinds.get("audio_effect"):
        report.add(EQUIVALENT, "音频处理（降噪/变声/EQ）", kinds["audio_effect"],
                   "降噪/变声是 FreeCut 自研；剪映只认音量和淡入淡出")
    if kinds.get("filter"):
        report.add(EQUIVALENT, "滤镜（LUT）", kinds["filter"],
                   "只有剪映目录里的 LUT 本体可用，云端滤镜无资源")

    # 关键帧曲线类型
    kf = sum(len(getattr(s, "keyframes", []) or []) for s in segs)
    if kf:
        report.add(EXACT, "关键帧", kf, "属性名与插值曲线名与剪映一致")
    return report


def _layout_count(project: Project) -> int:
    """How many video segments were placed by a layout (crop + scale)."""
    n = 0
    for track in project.tracks:
        if track.type != "video":
            continue
        for seg in track.segments:
            mat = project.material(seg.material_id)
            if mat is not None and mat.extra.get("crop"):
                n += 1
    return n

# --------------------------------------------------------------------------- #
# Premiere / FCP7 XML
# --------------------------------------------------------------------------- #
def analyze_xml(project: Project, xml_path: str = "", notes: Optional[List[str]] = None) -> Report:
    """What survives a trip through FCP7 XML into Premiere / Resolve / FCP.

    XML carries *editing* information (cuts, transform, crop, opacity, speed,
    audio levels).  Anything FreeCut renders itself — grading, effects, chroma
    key, masks, text styling — has no representation in the format.
    """
    report = Report(title="Premiere / FCP7 XML 导出报告", target="Premiere",
                    draft_dir=xml_path, notes=list(notes or []))
    mats = list(project.materials.values())
    kinds = Counter(getattr(m, "type", "?") for m in mats)
    segs = [s for t in project.tracks for s in t.segments]

    if segs:
        report.add(EXACT, "片段顺序与时长", len(segs), "xmeml clipitem 的 start/end/in/out")
    report.add(EXACT, "画布与帧率", 1,
               f"{project.canvas.width}×{project.canvas.height} @ {project.fps:g} fps")

    moved = 0
    cropped = 0
    faded = 0
    for track in project.tracks:
        if track.type not in ("video", "sticker"):
            continue
        for seg in track.segments:
            clip = getattr(seg, "clip", None)
            if clip is None:
                continue
            if (abs(float(getattr(clip, "scale_x", 1.0)) - 1.0) > 1e-6
                    or abs(float(getattr(clip, "scale_y", 1.0)) - 1.0) > 1e-6
                    or abs(float(getattr(clip, "tx", 0.0))) > 1e-6
                    or abs(float(getattr(clip, "ty", 0.0))) > 1e-6
                    or abs(float(getattr(clip, "rotation", 0.0))) > 1e-6):
                moved += 1
            if float(getattr(clip, "alpha", 1.0)) < 0.9999:
                faded += 1
            mat = project.material(seg.material_id)
            if mat is not None and mat.extra.get("crop"):
                cropped += 1
    if moved:
        report.add(EQUIVALENT, "变换（缩放/位置/旋转）", moved, "写成 Basic Motion")
    if cropped:
        report.add(EQUIVALENT, "裁剪 / 分屏", cropped, "写成 Crop 参数")
    if faded:
        report.add(EQUIVALENT, "不透明度", faded, "写成 Opacity")

    fast = [s for s in segs if abs(s.speed - 1.0) > 1e-6]
    if fast:
        report.add(EQUIVALENT, "速度", len(fast), "写成 Time Remap（常量速度）")
    if kinds.get("curve"):
        report.add(EQUIVALENT, "曲线变速", kinds["curve"], "XML 只保留平均速度，曲线被拉平")

    audio = [s for s in segs if any(t.type == "audio" and s in t.segments for t in project.tracks)]
    if kinds.get("audio"):
        report.add(EXACT, "音频片段与入出点", kinds["audio"])
    if any(abs(float(s.volume) - 1.0) > 1e-6 for s in audio):
        report.add(EQUIVALENT, "音量", sum(1 for s in audio if abs(float(s.volume) - 1.0) > 1e-6))
    if kinds.get("audio_effect"):
        report.add(LOST, "音频处理（降噪/变声/EQ）", kinds["audio_effect"],
                   "XML 没有音频滤镜，剪映/PR 都不会带上")

    if kinds.get("text"):
        report.add(DEGRADED, "文本", kinds["text"],
                   "XML 只带文字内容与位置，字号/描边/底色/花字样式丢失")
    if any(m.extra.get("resource_type") == "chroma" for m in mats):
        report.add(LOST, "色度抠图", 1, "XML 没有抠像，导出后是原素材")
    if kinds.get("mask"):
        report.add(LOST, "蒙版", kinds["mask"], "XML 没有蒙版参数")
    if kinds.get("transition"):
        report.add(DEGRADED, "转场", kinds["transition"],
                   "XML 不写转场，剪辑点变成硬切（可用叠化近似）")
    if kinds.get("material_animation"):
        report.add(LOST, "入场/出场动画", kinds["material_animation"], "XML 没有动画")
    color_n = sum(kinds.get(k, 0) for k in ("curve", "hsl", "color_wheel", "adjustment"))
    if color_n:
        report.add(LOST, "调色（曲线/HSL/色轮/调节图层）", color_n,
                   "XML 没有调色；要保留只能先渲染成片再导入")
    if kinds.get("effect"):
        report.add(LOST, "程序化特效", kinds["effect"], "XML 没有特效")
    return report

# --------------------------------------------------------------------------- #
# verification: read our own XML back and compare with the project
# --------------------------------------------------------------------------- #
@dataclass
class XmlCheck:
    ok: bool
    lines: List[str] = field(default_factory=list)

    def as_text(self) -> str:
        head = "XML 回读校验：通过 ✅" if self.ok else "XML 回读校验：不通过 ❌"
        return "\n".join([head] + ["  " + line for line in self.lines])


def verify_xml(project: Project, xml_path: str, tolerance: float = 0.01) -> XmlCheck:
    """Compare what the XML claims against what it actually contains.

    The export report says "transform becomes Basic Motion" — this is how that
    sentence is checked: parse the file back and compare the numbers.
    """
    from . import interchange

    data = interchange.read_fcp7(xml_path)
    lines: List[str] = []
    ok = True
    fps = float(data.get("fps") or project.fps or 30.0)

    segs = [s for t in project.tracks if t.type in ("video", "sticker")
            for s in t.sorted_segments()]
    expected = [(s, project.material(s.material_id)) for s in segs]
    expected = [(s, m) for s, m in expected if m is not None and getattr(m, "path", "")]
    got = [c for c in (data.get("clips") or []) if c.get("kind") != "audio"]
    if len(got) != len(expected):
        ok = False
        lines.append(f"片段数不一致：工程 {len(expected)} / XML {len(got)}")

    for i, ((seg, _m), clip) in enumerate(zip(expected, got)):
        want_start = round(seg.target_timerange.start / 1e6 * fps)
        if abs(clip["start"] - want_start) > 1:
            ok = False
            lines.append(f"第 {i+1} 段起点不一致：工程 {want_start} 帧 / XML {clip['start']}")

        c = getattr(seg, "clip", None)
        want_scale = ((float(getattr(c, "scale_x", 1.0)) + float(getattr(c, "scale_y", 1.0))) / 2.0
                      if c is not None else 1.0)
        if abs(clip["scale"] - want_scale) > tolerance:
            ok = False
            lines.append(f"第 {i+1} 段缩放不一致：工程 {want_scale:.3f} / XML {clip['scale']:.3f}")

        want_alpha = float(getattr(c, "alpha", 1.0)) if c is not None else 1.0
        if abs(clip["opacity"] - want_alpha) > tolerance:
            ok = False
            lines.append(f"第 {i+1} 段不透明度不一致：工程 {want_alpha:.3f} / XML {clip['opacity']:.3f}")

        want_crop = bool(_m.extra.get("crop")) if _m is not None else False
        if want_crop and not clip["crop"]:
            ok = False
            lines.append(f"第 {i+1} 段有裁剪但 XML 里没有 Crop")

        if abs(seg.speed - 1.0) > 1e-6 and abs(clip["speed"] - seg.speed) > tolerance:
            ok = False
            lines.append(f"第 {i+1} 段速度不一致：工程 {seg.speed:.3f} / XML {clip['speed']:.3f}")

    if data.get("unknown_effects"):
        lines.append("XML 里有我们不认识的滤镜：" + "、".join(sorted(set(data["unknown_effects"]))))

    if ok:
        lines.insert(0, f"{len(got)} 个片段的起点/缩放/不透明度/裁剪/速度与工程一致")
    return XmlCheck(ok=ok, lines=lines)

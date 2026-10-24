"""Tkinter GUI for FreeCut - a deliberately small NLE.

Layout::

    +--------------------------------------------------------------+
    | toolbar: 新建 打开 保存 | 导入媒体 导入剪映草稿 | 导出          |
    +------------+--------------------------------+----------------+
    | 素材库      |  预览 (canvas)                  | 属性            |
    |            +--------------------------------+                |
    |            |  时间线 (tracks / clips)         |                |
    +------------+--------------------------------+----------------+
    | 播放控制 / 时间码                                             |
    +--------------------------------------------------------------+
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

from . import builder, color as color_mod
from .export import PRESETS, ExportPreset, export as export_project
from .jianying import load_draft
from .media import probe
from .model import (
    AudioMaterial,
    ImageMaterial,
    Project,
    Segment,
    TextMaterial,
    Timerange,
    VideoMaterial,
)
from .render import Renderer

APP_TITLE = "FreeCut - 免费视频剪辑"

# 时间线配色参考 ReaCut 的公开界面说明（docs/UI_PARITY.md）：
# 中性轨道背景、红棕文字轨、选中片段用浅色边框。色值是我们自己的。
TRACK_COLORS = {
    "video": "#3b6ea5",
    "audio": "#3f8f5f",
    "text": "#8a4a3c",
    "sticker": "#8a4fa0",
    "effect": "#7a5aa0",
    "filter": "#7a5aa0",
}
#: 选中片段的强调色（青色，ReaCut 用青色标当前类别与操作）
from . import theme          # 界面令牌（数值来源见 theme.py 顶部注释）

ACCENT = theme.ACCENT

#: 转场引擎 → 中文名（属性面板与左栏转场页共用一份，避免两处各写一遍）
ENGINE_LABELS = {
    "cross_dissolve": "叠化", "fade_black": "闪黑", "fade_white": "闪白",
    "wipe_left": "向左擦除", "wipe_right": "向右擦除",
    "wipe_up": "向上擦除", "wipe_down": "向下擦除",
    "wipe_diagonal": "斜向擦除", "square": "方形展开", "blinds": "百叶窗",
    "slide_left": "向左滑动", "slide_right": "向右滑动",
    "slide_up": "向上滑动", "slide_down": "向下滑动",
    "circle": "圆形", "zoom_push": "缩放推", "mosaic_dissolve": "像素化",
    "page_turn": "翻页", "pixel_dissolve": "像素溶解",
}
TRACK_LABELS = {"video": "视频", "audio": "音频", "text": "文本", "sticker": "贴纸",
                "effect": "特效", "filter": "滤镜"}


def fmt_time(us: int) -> str:
    total_ms = max(0, int(us // 1000))
    ms = total_ms % 1000
    s = (total_ms // 1000) % 60
    m = (total_ms // 60000) % 60
    h = total_ms // 3600000
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m:02d}:{s:02d}.{ms:03d}"


def parse_time(text: str) -> int:
    text = text.strip()
    try:
        if ":" in text:
            parts = [float(p) for p in text.split(":")]
            total = 0.0
            for p in parts:
                total = total * 60 + p
            return int(total * 1_000_000)
        return int(float(text) * 1_000_000)
    except ValueError:
        return 0


# --------------------------------------------------------------------------- #
# timeline widget
# --------------------------------------------------------------------------- #
class TimelineView(tk.Canvas):
    ROW_H = 40          # 第 185 轮：44 → 40，和时间线整体高度一起收紧（剪映更紧凑）
    HEADER_H = 24
    TRIM_PX = 7

    def __init__(self, master, app: "App", **kw):
        super().__init__(master, bg="#1b1b1f", highlightthickness=0, **kw)
        self.app = app
        self.ppm = 60.0                     # pixels per second
        self.scroll_us = 0
        # 轨道多于一屏时上下滚（Shift+滚轮 / PageUp PageDown）。剪映同样竖着滚。
        self.vscroll = 0
        self.selected: Optional[Segment] = None
        self._drag: Optional[dict] = None
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Configure>", lambda e: self.redraw())

    # -- geometry -----------------------------------------------------------
    def _rows(self) -> List:
        tracks = [t for t in self.app.project.tracks if t.type in ("video", "text", "sticker", "audio", "effect", "filter")]
        order = {"video": 0, "effect": 1, "filter": 2, "text": 3, "sticker": 4, "audio": 5}
        return sorted(tracks, key=lambda t: order.get(t.type, 9))

    def _row_index_at(self, y: float) -> int:
        return int((y - self.HEADER_H + self.vscroll) // self.ROW_H)

    def _us_to_x(self, us: int) -> float:
        return (us - self.scroll_us) / 1e6 * self.ppm

    def _x_to_us(self, x: float) -> int:
        return int(self.scroll_us + x / self.ppm * 1e6)

    def _track_y(self, idx: int) -> int:
        return self.HEADER_H + idx * self.ROW_H - int(self.vscroll)

    def _content_height(self) -> int:
        return self.HEADER_H + len(self._rows()) * self.ROW_H

    def _max_vscroll(self) -> int:
        return max(0, self._content_height() - max(1, self.winfo_height()))

    def _clamp_vscroll(self) -> None:
        self.vscroll = min(max(0, int(self.vscroll)), self._max_vscroll())

    # -- drawing ------------------------------------------------------------
    def redraw(self) -> None:
        self.delete("all")
        p = self.app.project
        rows = self._rows()
        self._clamp_vscroll()
        self.configure(scrollregion=(0, 0, 4000, max(200, self.HEADER_H + len(rows) * self.ROW_H + 20)))
        # row backgrounds + labels
        for i, track in enumerate(rows):
            y = self._track_y(i)
            self.create_rectangle(0, y, 4000, y + self.ROW_H, fill="#232329", outline="#2e2e36")
            self.create_text(6, y + 6, anchor="nw", text=f"{TRACK_LABELS.get(track.type, track.type)}",
                             fill="#8c8c96", font=("Microsoft YaHei", 8))
        # time ruler
        self.create_rectangle(0, 0, 4000, self.HEADER_H, fill="#15151a", outline="")
        step = self._nice_step()
        start = int(self.scroll_us // step) * step
        t = start
        while self._us_to_x(t) < 4000:
            x = self._us_to_x(t)
            self.create_line(x, 0, x, self.HEADER_H, fill="#3a3a44")
            self.create_text(x + 3, 2, anchor="nw", text=fmt_time(t)[:-4], fill="#7d7d88",
                             font=("Consolas", 8))
            t += step
        # clips
        for i, track in enumerate(rows):
            y = self._track_y(i)
            for seg in track.sorted_segments():
                x0 = self._us_to_x(seg.target_timerange.start)
                x1 = self._us_to_x(seg.target_timerange.end)
                if x1 < -50 or x0 > 4000:
                    continue
                fill = TRACK_COLORS.get(track.type, "#555")
                selected = seg is self.selected
                if selected:
                    fill = "#1f4a55"
                self.create_rectangle(
                    x0, y + 4, max(x0 + 2, x1), y + self.ROW_H - 4,
                    fill=fill,
                    outline=ACCENT if selected else "#0f0f12",
                    width=2 if selected else 1)
                if track.type == "audio":
                    self._draw_wave(x0, y, x1, seg, p,
                                    colour="#0b1b12" if seg is not self.selected else "#4a2a00")
                name = self._seg_label(seg)
                if x1 - x0 > 24:
                    self.create_text(x0 + 5, y + 10, anchor="nw", text=name[:24],
                                     fill="#ffffff", font=("Microsoft YaHei", 8))
                # 速度曲线：在片段里画一条折线（纵轴为倍率）
                if getattr(seg, "speed_curve", None) and x1 - x0 > 30:
                    span = 4.0 - 0.25
                    top, bottom = y + 5, y + self.ROW_H - 5
                    pts = []
                    for i in range(0, 41):
                        u = i / 40.0
                        from .speed import speed_at

                        v = speed_at(seg.speed_curve, u)
                        vy = bottom - (min(4.0, max(0.25, v)) - 0.25) / span * (bottom - top)
                        pts.extend((x0 + (x1 - x0) * u, vy))
                    self.create_line(*pts, fill="#ffe066", width=1)
                # 关键帧标记
                for prop, kfs in (seg.keyframes or {}).items():
                    for kf in kfs:
                        kx = self._us_to_x(seg.target_timerange.start + kf.time_offset)
                        if x0 - 4 <= kx <= x1 + 4:
                            cy = y + self.ROW_H - 10
                            self.create_polygon(kx, cy - 5, kx + 5, cy, kx, cy + 5, kx - 5, cy,
                                                fill="#ffd166", outline="#7a5c10")
                if seg.speed and abs(seg.speed - 1.0) > 1e-6 and x1 - x0 > 60:
                    self.create_text(x0 + 5, y + 26, anchor="nw", text=f"{seg.speed:.2f}x",
                                     fill="#ffe090", font=("Consolas", 7))
        self._draw_track_hint(rows)

        # playhead
        x = self._us_to_x(self.app.playhead)
        self.create_line(x, 0, x, self.HEADER_H + len(rows) * self.ROW_H, fill="#ff4040", width=2)

    def _draw_wave(self, x0: float, y: float, x1: float, seg, project,
                   colour: str = "#0b1b12") -> None:
        """Draw the audio envelope of one clip inside its rectangle."""
        width = int(x1 - x0)
        if width < 4:
            return
        mat = project.material(seg.material_id)
        path = getattr(mat, "path", "") if mat is not None else ""
        if not path:
            return
        try:
            from . import waveform

            buckets = waveform.peaks(path, 512)
        except Exception:
            return
        if not len(buckets):
            return
        total_us = int(mat.get("duration", 0) or 0) or seg.source_timerange.end or 1
        top, bottom = y + 6, y + self.ROW_H - 6
        mid = (top + bottom) / 2.0
        half = (bottom - top) / 2.0
        step = 2
        for px in range(0, width, step):
            us = seg.source_timerange.start + seg.source_timerange.duration * (px / max(1, width))
            idx = int(us / max(1, total_us) * (len(buckets) - 1))
            idx = min(len(buckets) - 1, max(0, idx))
            amp = float(buckets[idx]) * half
            if amp <= 0.2:
                continue
            self.create_line(x0 + px, mid - amp, x0 + px, mid + amp,
                             fill=colour, width=1)

    def _nice_step(self) -> int:
        # choose a ruler step giving ~80px spacing
        target = 80.0 / self.ppm  # seconds
        steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]
        for s in steps:
            if s >= target:
                return int(s * 1e6)
        return int(300e6)

    def _seg_label(self, seg: Segment) -> str:
        mat = self.app.project.material(seg.material_id)
        if mat is None:
            return "?"
        if isinstance(mat, TextMaterial) or mat.type == "text":
            return str(mat.get("content", ""))[:20] or "文本"
        name = mat.get("material_name") or mat.get("name") or ""
        if not name:
            path = mat.get("path") or ""
            name = os.path.basename(path) if path else mat.type
        return str(name)

    # -- interaction --------------------------------------------------------
    def _hit(self, x: float, y: float) -> Tuple[Optional[Segment], Optional[str]]:
        rows = self._rows()
        i = self._row_index_at(y)
        if i < 0 or i >= len(rows):
            return None, None
        track = rows[i]
        for seg in track.sorted_segments():
            x0 = self._us_to_x(seg.target_timerange.start)
            x1 = self._us_to_x(seg.target_timerange.end)
            if x0 - self.TRIM_PX <= x <= x1 + self.TRIM_PX:
                if abs(x - x0) <= self.TRIM_PX:
                    return seg, "left"
                if abs(x - x1) <= self.TRIM_PX:
                    return seg, "right"
                if x0 <= x <= x1:
                    return seg, "move"
        return None, None

    def _draw_track_hint(self, rows) -> None:
        """右下角提示：当前看到第几条到第几条、总共几条轨道。"""
        if self._max_vscroll() <= 0:
            return
        first = int(self.vscroll // self.ROW_H) + 1
        last = min(len(rows),
                   int((self.vscroll + self.winfo_height() - self.HEADER_H) // self.ROW_H))
        text = f"{first}-{last} / 共 {len(rows)} 轨 · Shift+滚轮"
        x = self.winfo_width() - 10
        y = self.winfo_height() - 12
        w = len(text) * 7
        self.create_rectangle(x - w - 6, y - 10, x + 4, y + 10,
                              fill="#15151a", outline="#2e2e36")
        self.create_text(x, y, anchor="e", text=text,
                         fill="#8c8c96", font=("Microsoft YaHei", 8))

    def _on_press(self, ev) -> None:
        seg, mode = self._hit(ev.x, ev.y)
        if seg is None:
            self.app.set_playhead(self._x_to_us(ev.x))
            self.app.select(None)
            return
        self.app.select(seg)
        self.app.push_undo()
        self._drag = {
            "seg": seg,
            "mode": mode,
            "x0": ev.x,
            "start": seg.target_timerange.start,
            "duration": seg.target_timerange.duration,
            "src_start": seg.source_timerange.start,
            "src_dur": seg.source_timerange.duration,
        }

    def _on_motion(self, ev) -> None:
        if not self._drag:
            return
        d = self._drag
        seg: Segment = d["seg"]
        delta_us = int((ev.x - d["x0"]) / self.ppm * 1e6)
        if d["mode"] == "move":
            seg.target_timerange.start = max(0, d["start"] + delta_us)
        elif d["mode"] == "left":
            new_start = max(0, d["start"] + delta_us)
            shift = new_start - d["start"]
            new_dur = d["duration"] - shift
            if new_dur > 100_000:
                seg.target_timerange.start = new_start
                seg.target_timerange.duration = new_dur
                seg.source_timerange.start = max(0, d["src_start"] + int(shift * seg.speed))
                seg.source_timerange.duration = int(new_dur * seg.speed)
        elif d["mode"] == "right":
            new_dur = max(100_000, d["duration"] + delta_us)
            seg.target_timerange.duration = new_dur
            seg.source_timerange.duration = int(new_dur * seg.speed)
        self.app.invalidate()
        self.redraw()

    def _on_release(self, ev) -> None:
        self._drag = None
        self.app.invalidate(render=True)

    def _on_wheel(self, ev) -> None:
        if ev.state & 0x0004:  # ctrl -> zoom
            factor = 1.2 if ev.delta > 0 else 1 / 1.2
            self.ppm = min(600.0, max(8.0, self.ppm * factor))
            self.redraw()
        elif ev.state & 0x0001:  # shift -> 上下滚轨道
            self.vscroll -= int(ev.delta / 120) * self.ROW_H
            self._clamp_vscroll()
            self.redraw()
        else:
            self.scroll_us = max(0, self.scroll_us - int(ev.delta * 2000))
            self.redraw()

    def scroll_tracks(self, rows: int) -> None:
        self.vscroll += int(rows) * self.ROW_H
        self._clamp_vscroll()
        self.redraw()


# --------------------------------------------------------------------------- #
# properties panel
# --------------------------------------------------------------------------- #
class PropertiesPanel(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master, padding=6)
        self.app = app
        self._build_empty()

    def _clear(self) -> None:
        for w in self.winfo_children():
            w.destroy()

    def _build_empty(self) -> None:
        self._clear()
        ttk.Label(self, text="未选择片段", foreground="#888").pack(anchor="w", pady=8)
        ttk.Label(self, text="提示：", font=("Microsoft YaHei", 9, "bold")).pack(anchor="w", pady=(12, 2))
        for line in (
            "· 点击时间线片段进行选择",
            "· 拖动片段移动，拖动两端裁剪",
            "· Ctrl + 滚轮 缩放时间线",
            "· 空格 播放 / 暂停",
            "· Delete 删除片段",
            "· Ctrl + ←/→ 微调左分隔线（加 Shift 调右侧）",
        ):
            ttk.Label(self, text=line, foreground="#777").pack(anchor="w")

    def rebuild(self, seg: Optional[Segment]) -> None:
        self._clear()
        if seg is None:
            self._build_empty()
            return
        p = self.app.project
        mat = p.material(seg.material_id)
        ttk.Label(self, text=f"片段 {seg.id[:8]}", font=("Microsoft YaHei", 9, "bold")).pack(anchor="w")
        ttk.Label(self, text=f"类型: {getattr(mat, 'type', '?')}", foreground="#888").pack(anchor="w")

        # 分组参考 ReaCut 的界面说明：画面下再分「基础 / 裁剪 / 抠像 / 蒙版」，
        # 其余主类直接平铺。只放真正实现了的页，不摆空标签。
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, pady=(8, 0))
        picture_tab = ttk.Frame(notebook, padding=4)
        color_tab = ttk.Frame(notebook, padding=6)
        grade_tab = ttk.Frame(notebook, padding=6)
        effects_tab = ttk.Frame(notebook, padding=6)
        sound_tab = ttk.Frame(notebook, padding=6)
        notebook.add(picture_tab, text="画面")
        notebook.add(color_tab, text="调节")
        notebook.add(grade_tab, text="曲线·轮")
        notebook.add(effects_tab, text="效果")
        notebook.add(sound_tab, text="声音")

        sub = ttk.Notebook(picture_tab)
        sub.pack(fill="both", expand=True)
        base_tab = ttk.Frame(sub, padding=6)
        crop_tab = ttk.Frame(sub, padding=6)
        key_tab = ttk.Frame(sub, padding=6)
        mask_tab = ttk.Frame(sub, padding=6)
        sub.add(base_tab, text="基础")
        sub.add(crop_tab, text="裁剪")
        sub.add(key_tab, text="抠像")
        sub.add(mask_tab, text="蒙版")
        self.picture_sub = sub

        self._transform_tab(base_tab, seg, mat)
        self._crop_tab(crop_tab, seg, mat)
        self._key_tab(key_tab, seg, mat)
        self._mask_tab(mask_tab, seg, mat)
        self._color_tab(color_tab, seg, mat)
        self._grade_tab(grade_tab, seg, mat)
        self._effects_tab(effects_tab, seg, mat)
        self._sound_tab(sound_tab, seg, mat)

    # -- sliders ------------------------------------------------------------
    def _slider(self, parent, label: str, value: float, lo: float, hi: float,
                apply, resolution: float = 0.01, fmt: str = "{:.2f}"):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=3)
        head = ttk.Frame(frame)
        head.pack(fill="x")
        ttk.Label(head, text=label).pack(side="left")
        value_lbl = ttk.Label(head, text=fmt.format(value), foreground="#4a8fd0")
        value_lbl.pack(side="right")
        var = tk.DoubleVar(value=value)

        def on_change(_=None):
            v = float(var.get())
            value_lbl.configure(text=fmt.format(v))
            apply(v)
            self.app.invalidate()

        try:
            _len = self.app._px(150)
        except Exception:
            _len = 190
        scale = ttk.Scale(frame, from_=lo, to=hi, variable=var, command=on_change,
                          length=_len)
        # 一次拖动 = 一步撤销（按下时快照，拖动过程中不再压栈）
        scale.bind("<ButtonPress-1>", lambda e: self.app.push_undo())
        scale.pack(fill="x")
        return var

    def _transform_tab(self, parent, seg: Segment, mat) -> None:
        c = seg.clip
        self._slider(parent, "缩放", c.scale_x, 0.05, 4.0,
                     lambda v: (setattr(c, "scale_x", v), setattr(c, "scale_y", v)))
        self._slider(parent, "水平位置", c.tx, -1.0, 1.0, lambda v: setattr(c, "tx", v))
        self._slider(parent, "垂直位置", c.ty, -1.0, 1.0, lambda v: setattr(c, "ty", v))
        self._slider(parent, "旋转", c.rotation, -180.0, 180.0, lambda v: setattr(c, "rotation", v), 0.5)
        self._slider(parent, "不透明度", c.alpha, 0.0, 1.0, lambda v: setattr(c, "alpha", v))

        # ---- 关键帧 --------------------------------------------------------
        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="关键帧（用上面的滑块定好数值，再点这里打点）").pack(anchor="w")

        kf_list = tk.Listbox(parent, height=5, bg="#1b1b21", fg="#d8d8e0",
                             selectbackground="#3a5a8a", relief="flat")
        kf_list.pack(fill="x", pady=(4, 2))

        specs = [
            ("缩放", "KFTypeScaleX", lambda: c.scale_x),
            ("水平位置", "KFTypePositionX", lambda: c.tx),
            ("垂直位置", "KFTypePositionY", lambda: c.ty),
            ("旋转", "KFTypeRotation", lambda: c.rotation),
            ("不透明度", "KFTypeAlpha", lambda: c.alpha),
        ]

        def refresh_kf():
            kf_list.delete(0, "end")
            for label, prop, _getter in specs:
                for k in seg.keyframes.get(prop) or []:
                    kf_list.insert("end", f"{label} @ {k.time_offset/1e6:.2f}s = {k.value:.3f}")

        curve_var = tk.StringVar(value="CurveInOut")

        def make_key(prop: str, getter):
            offset = max(0, min(seg.duration, self.app.playhead - seg.target_timerange.start))
            builder.add_keyframe(seg, prop, offset, float(getter()), curve=curve_var.get())
            self.app.invalidate()
            self.app.timeline.redraw()
            refresh_kf()
            self.app.set_status(f"已打关键帧：{prop} @ {offset/1e6:.2f}s")

        row_kf = ttk.Frame(parent)
        row_kf.pack(fill="x", pady=2)
        for label, prop, getter in specs:
            ttk.Button(row_kf, text=label, width=7,
                       command=lambda p=prop, g=getter: make_key(p, g)).pack(side="left", padx=1)

        def clear_keys():
            for prop in list(seg.keyframes):
                seg.keyframes[prop] = []
            self.app.invalidate()
            self.app.timeline.redraw()
            refresh_kf()

        curve_row = ttk.Frame(parent)
        curve_row.pack(fill="x", pady=(4, 0))
        ttk.Label(curve_row, text="曲线").pack(side="left")
        ttk.Combobox(curve_row, textvariable=curve_var, state="readonly", width=16,
                     values=["CurveInOut", "CurveIn", "FreeCurveIn", "FreeCurveOut",
                             "FreeCurveInOut"]).pack(side="left", padx=4)

        ttk.Button(parent, text="清空全部关键帧", command=clear_keys).pack(anchor="w", pady=(4, 0))
        refresh_kf()

        if isinstance(mat, TextMaterial) or (mat is not None and mat.type == "text"):
            ttk.Separator(parent).pack(fill="x", pady=8)
            ttk.Label(parent, text="文字内容").pack(anchor="w")
            txt = tk.Text(parent, height=3, wrap="word", bg="#26262c", fg="#e8e8ee",
                          insertbackground="#e8e8ee", relief="flat")
            txt.insert("1.0", str(mat.get("content", "")))
            txt.pack(fill="x", pady=4)

            def commit_text(_=None):
                mat["content"] = txt.get("1.0", "end").rstrip("\n")
                self.app.invalidate()

            txt.bind("<KeyRelease>", commit_text)
            txt.bind("<FocusIn>", lambda e: self.app.push_undo())
            self._slider(parent, "字号", float(mat.get("font_size", 15)), 4.0, 40.0,
                         lambda v: mat.__setitem__("font_size", v), 0.2, "{:.1f}")
            self._slider(parent, "加粗", float(mat.get("bold_width", 0.0)), 0.0, 2.0,
                         lambda v: mat.__setitem__("bold_width", v), 0.05, "{:.2f}")
            self._slider(parent, "描边", float(mat.get("border_width", 0.0)), 0.0, 0.15,
                         lambda v: mat.__setitem__("border_width", v), 0.002, "{:.3f}")
            ttk.Separator(parent).pack(fill="x", pady=6)
            ttk.Label(parent, text="文字模板（花字）").pack(anchor="w")
            from . import textstyles

            style_var = tk.StringVar(value=textstyles.style_of(mat) or "")
            style_box = ttk.Combobox(parent, textvariable=style_var,
                                     values=textstyles.names(), state="readonly")
            style_box.pack(fill="x", pady=2)

            def on_style(_=None):
                name = style_var.get()
                if not name:
                    return
                self.app.push_undo()
                if textstyles.apply(mat, name):
                    self.app.invalidate()
                    self.app.status.configure(text=f"已套用文字模板：{name}")

            style_box.bind("<<ComboboxSelected>>", on_style)

            self._slider(parent, "阴影", float(mat.get("shadow_alpha", 0.0)), 0.0, 1.0,
                         lambda v: (mat.__setitem__("shadow_alpha", v),
                                    mat.__setitem__("shadow_distance", 0.055)))

    # -- 画面：裁剪 / 抠像 / 蒙版 ------------------------------------------
    def _crop_tab(self, parent, seg: Segment, mat) -> None:
        """裁剪：四个边界都按 0~0.5 归一化，渲染器读 mat["crop"]。"""
        if mat is None:
            ttk.Label(parent, text="无素材").pack(anchor="w")
            return
        crop = dict(mat.get("crop") or {})
        current = {
            "左": float(crop.get("upper_left_x", 0.0)),
            "右": 1.0 - float(crop.get("lower_right_x", 1.0)),
            "上": float(crop.get("upper_left_y", 0.0)),
            "下": 1.0 - float(crop.get("lower_right_y", 1.0)),
        }

        def apply() -> None:
            data = {
                "upper_left_x": current["左"],
                "upper_left_y": current["上"],
                "lower_right_x": max(current["左"] + 0.02, 1.0 - current["右"]),
                "lower_right_y": max(current["上"] + 0.02, 1.0 - current["下"]),
            }
            mat["crop"] = data
            self.app.invalidate()
            self.app.timeline.redraw()

        def setter(name):
            def _set(value: float) -> None:
                current[name] = float(value)
                apply()
            return _set

        for name in ("左", "右", "上", "下"):
            self._slider(parent, name, current[name], 0.0, 0.45, setter(name),
                         resolution=0.01, fmt="{:.2f}")
        ttk.Button(parent, text="清除裁剪", command=lambda: (
            mat.extra.pop("crop", None), self.app.invalidate(),
            self.app.timeline.redraw(), self.app.set_status("已清除裁剪"))).pack(anchor="w", pady=6)

    def _key_tab(self, parent, seg: Segment, mat) -> None:
        """抠像：色度抠图（key / 相似度 / 平滑度）。"""
        from . import chroma as chroma_mod

        existing = None
        for ref in self.app.project.refs_of(seg):
            if ref.type == "mask" and str(ref.get("resource_type")) == "chroma":
                existing = ref
        cfg = dict((existing.get("config") if existing is not None else None) or {})
        state = {"key": str(cfg.get("key") or "#00ff00"),
                 "similarity": float(cfg.get("similarity", 0.25)),
                 "smoothness": float(cfg.get("smoothness", 0.12))}

        preview = ttk.Label(parent, text="", foreground="#9aa0ae")
        preview.pack(anchor="w", pady=(0, 6))

        def apply() -> None:
            project = self.app.project
            if existing is not None:
                project.remove_material(existing.id) if hasattr(project, "remove_material") else None
            builder.add_chroma(project, seg, key=state["key"],
                               similarity=state["similarity"],
                               smoothness=state["smoothness"])
            preview.configure(text=f"键色 {state['key']}  相似度 {state['similarity']:.2f}  "
                                   f"平滑度 {state['smoothness']:.2f}")
            self.app.invalidate()
            self.app.timeline.redraw()

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="键色").pack(side="left")
        colour_var = tk.StringVar(value=state["key"])
        entry = ttk.Entry(row, textvariable=colour_var, width=10)
        entry.pack(side="left", padx=6)
        ttk.Button(row, text="应用", command=lambda: (
            state.update({"key": colour_var.get().strip() or "#00ff00"}), apply())).pack(side="left")
        self._slider(parent, "相似度", state["similarity"], 0.01, 0.9,
                     lambda v: (state.update({"similarity": float(v)}), apply()))
        self._slider(parent, "平滑度", state["smoothness"], 0.0, 0.5,
                     lambda v: (state.update({"smoothness": float(v)}), apply()))
        preview.configure(text=f"键色 {state['key']}  相似度 {state['similarity']:.2f}  "
                               f"平滑度 {state['smoothness']:.2f}")

    def _mask_tab(self, parent, seg: Segment, mat) -> None:
        """蒙版：形状下拉 + 中心/尺寸/羽化。"""
        from .mask import SHAPE_KINDS

        current = {"kind": "circle"}
        for ref in self.app.project.refs_of(seg):
            if ref.type == "mask" and str(ref.get("resource_type")) in SHAPE_KINDS:
                current["kind"] = str(ref.get("resource_type"))
        kinds = sorted(set(SHAPE_KINDS.values()))

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="形状").pack(side="left")
        kind_var = tk.StringVar(value=current["kind"])
        combo = ttk.Combobox(row, textvariable=kind_var, values=kinds, width=12,
                             state="readonly")
        combo.pack(side="left", padx=6)

        params = {"center_x": 0.0, "center_y": 0.0, "width": 0.6,
                  "height": 0.6, "feather": 0.04, "rotation": 0.0}

        def apply() -> None:
            project = self.app.project
            for ref in list(project.refs_of(seg)):
                if ref.type == "mask" and str(ref.get("resource_type")) in SHAPE_KINDS:
                    if hasattr(project, "remove_material"):
                        project.remove_material(ref.id)
                    elif ref.id in project.materials:
                        project.materials.pop(ref.id, None)
                    if ref.id in seg.extra_material_refs:
                        seg.extra_material_refs.remove(ref.id)
            # 调节图层上的蒙版只该限制调色，不该裁画面——换成 add_adjust_mask
            adder = builder.add_adjust_mask if getattr(seg, "is_adjustment", False) else builder.add_mask
            adder(project, seg, name=kind_var.get(),
                             center_x=params["center_x"], center_y=params["center_y"],
                             width=params["width"], height=params["height"],
                             feather=params["feather"], rotation=params["rotation"])
            self.app.invalidate()
            self.app.timeline.redraw()

        for key, label, lo, hi in (("center_x", "中心 X", -1.0, 1.0),
                                   ("center_y", "中心 Y", -1.0, 1.0),
                                   ("width", "宽度", 0.1, 1.6),
                                   ("height", "高度", 0.1, 1.6),
                                   ("feather", "羽化", 0.0, 0.3),
                                   ("rotation", "旋转", -180.0, 180.0)):
            self._slider(parent, label, params[key], lo, hi,
                         lambda v, k=key: (params.update({k: float(v)}), apply()),
                         resolution=0.5 if key == "rotation" else 0.01)
        ttk.Button(parent, text="应用蒙版", command=apply).pack(anchor="w", pady=4)

    def _color_tab(self, parent, seg: Segment, mat) -> None:
        if mat is None:
            ttk.Label(parent, text="无素材").pack(anchor="w")
            return
        current = dict(mat.get("adjust") or {})

        def make_apply(key: str):
            def apply(v: float) -> None:
                cur = dict(mat.get("adjust") or {})
                cur[key] = float(v)
                mat["adjust"] = cur

            return apply

        for key, spec in color_mod.ADJUST_SPEC.items():
            self._slider(parent, spec["label"], float(current.get(key, spec["default"])),
                         float(spec["min"]), float(spec["max"]), make_apply(key), 0.5, "{:.0f}")

        self._hsl_block(parent, seg)
        self._lut_migration_block(parent, seg)

    # -- curves / wheels tab ------------------------------------------------
    def _hsl_block(self, parent, seg: Segment) -> None:
        """HSL 分色：选一个色带，调它的 色相/饱和/明度（引擎早就有，界面原来没有）。"""
        from .color import HSL_BANDS

        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="HSL 分色", font=("Microsoft YaHei", 9, "bold")).pack(anchor="w")
        ttk.Label(parent, text="只改选定色带的颜色，其它颜色不动", foreground="#8a8f9c").pack(anchor="w")

        current = None
        for ref in self.app.project.refs_of(seg):
            if ref.type == "hsl":
                current = ref
                break
        bands = dict((current.get("bands") or {})) if current is not None else {}

        names = {"red": "红", "orange": "橙", "yellow": "黄", "green": "绿",
                 "cyan": "青", "blue": "蓝", "purple": "紫", "magenta": "品红"}
        order = [key for key, _ in HSL_BANDS]
        band_var = tk.StringVar(value=names.get(order[0], order[0]))
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="色带").pack(side="left")
        combo = ttk.Combobox(row, textvariable=band_var, state="readonly", width=8,
                             values=[names[k] for k in order])
        combo.pack(side="left", padx=6)

        def band_key() -> str:
            for key in order:
                if names.get(key) == band_var.get():
                    return key
            return order[0]

        def apply_band(field: str, value: float) -> None:
            key = band_key()
            entry = dict(bands.get(key) or {})
            entry[field] = float(value)
            bands[key] = entry
            mat = builder.set_hsl(self.app.project, seg, bands={key: entry})
            self.app.invalidate()
            self.app.timeline.redraw()

        for field, label in (("hue", "色相"), ("sat", "饱和"), ("lum", "明度")):
            self._slider(parent, f"{label}（{band_var.get()}）", 0.0, -1.0, 1.0,
                         lambda v, f=field: apply_band(f, v))

    def _lut_migration_block(self, parent, seg: Segment) -> None:
        """以"当前播放帧"为参考做 LUT 迁移（引擎有、界面原来没有）。"""
        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="LUT 迁移（以当前帧为参考）",
                  font=("Microsoft YaHei", 9, "bold")).pack(anchor="w")
        ttk.Label(parent, text="把人像/参考画面的色调搬到这个片段上", foreground="#8a8f9c").pack(anchor="w")

        state = {"intensity": 0.6}
        self._slider(parent, "强度", state["intensity"], 0.0, 1.0,
                     lambda v: state.update({"intensity": float(v)}))

        def grab_and_migrate() -> None:
            import tempfile

            from PIL import Image

            from . import builder
            from .render import Renderer

            project = self.app.project
            width = int(project.canvas.width or 1280)
            height = int(project.canvas.height or 720)
            fps = float(project.fps or 30.0)
            renderer = Renderer(project, width, height, fps)
            try:
                index = max(0, int(round(self.app.playhead / 1_000_000 * fps)))
                frame = renderer.render_frame(index)
            finally:
                renderer.close()
            tmp = os.path.join(tempfile.mkdtemp(prefix="freecut-lut-"), "reference.png")
            Image.fromarray(frame).save(tmp)
            builder.set_color_migration(project, seg, tmp, intensity=state["intensity"])
            self.app.invalidate()
            self.app.timeline.redraw()
            self.app.set_status(f"已按当前帧做 LUT 迁移（强度 {state['intensity']:.2f}）")

        ttk.Button(parent, text="按当前帧生成", command=grab_and_migrate).pack(anchor="w", pady=6)

    def _grade_tab(self, parent, seg: Segment, mat) -> None:
        from .gui_widgets import CurveEditor, WheelPad

        project = self.app.project

        def curve_material():
            return project.first_ref(seg, "curve")

        def wheel_material():
            return project.first_ref(seg, "color_wheel")

        ttk.Label(parent, text="曲线（点击加点 / 右键删点）").pack(anchor="w")
        channel = tk.StringVar(value="rgb")
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 4))
        for key, label in (("rgb", "RGB"), ("r", "R"), ("g", "G"), ("b", "B")):
            ttk.Radiobutton(row, text=label, value=key, variable=channel,
                            command=lambda: load_curve()).pack(side="left", padx=2)

        editor = CurveEditor(parent, height=150)
        editor.pack(fill="x", pady=4)

        def load_curve() -> None:
            cm = curve_material()
            pts = (cm.get("curves") or {}).get(channel.get()) if cm else None
            editor.set_points(pts or [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)])

        def on_curve(points) -> None:
            cm = curve_material()
            if cm is None:
                cm = builder.set_curves(project, seg)
            curves = dict(cm.get("curves") or {})
            curves[channel.get()] = [[round(x, 4), round(y, 4)] for x, y in points]
            cm["curves"] = curves
            self.app.invalidate()

        editor.on_change = on_curve
        load_curve()

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(2, 8))
        ttk.Button(actions, text="重置该通道", command=lambda: (
            editor.reset(), on_curve(editor.get_points()))).pack(side="left")
        ttk.Button(actions, text="移除曲线", command=lambda: self._drop_ref(seg, "curve")).pack(side="left", padx=4)

        ttk.Separator(parent).pack(fill="x", pady=6)
        ttk.Label(parent, text="调色轮（Lift / Gamma / Gain）").pack(anchor="w")

        pads = {}
        pad_row = ttk.Frame(parent)
        pad_row.pack(fill="x", pady=4)
        for key, label, colour in (("lift", "暗部", "#8f6bff"), ("gamma", "中间调", "#7ec0ff"),
                                   ("gain", "高光", "#ffd166")):
            cell = ttk.Frame(pad_row)
            cell.pack(side="left", expand=True)
            ttk.Label(cell, text=label, foreground="#888").pack()
            pad = WheelPad(cell, color=colour, size=84)
            pad.pack()
            pads[key] = pad

        def apply_wheel(_key=None) -> None:
            import colorsys
            import math as _math

            wheels = {}
            for key, pad in pads.items():
                dx, dy = pad.get_offset()
                radius = min(1.0, _math.hypot(dx, dy))
                if radius < 1e-3:
                    continue
                ang = (_math.degrees(_math.atan2(dy, dx)) % 360.0) / 360.0
                rgb = colorsys.hsv_to_rgb(ang, 1.0, 1.0)
                delta = [(c - 1 / 3.0) * radius for c in rgb]
                if key == "lift":
                    wheels["lift"] = [round(d * 0.35, 4) for d in delta]
                else:
                    wheels[key] = [round(min(3.0, max(0.3, 1.0 + d * 0.9)), 4) for d in delta]
            wm = wheel_material()
            if not wheels:
                self._drop_ref(seg, "color_wheel")
            elif wm is None:
                builder.set_wheels(project, seg, **{k: v for k, v in wheels.items()})
            else:
                wm["wheels"] = wheels
            self.app.invalidate()

        for key, pad in pads.items():
            pad.on_change = lambda x, y, k=key: apply_wheel(k)

        wm = wheel_material()
        if wm:
            saved = wm.get("wheels") or {}
            for key, pad in pads.items():
                vals = saved.get(key)
                if not vals:
                    continue
                if key == "lift":
                    pad.set_offset(vals[0] / 0.35 * 0.8, 0.0)
                else:
                    pad.set_offset((vals[0] - 1.0) / 0.9, 0.0)

        ttk.Button(parent, text="重置调色轮", command=lambda: (
            [p.set_offset(0.0, 0.0) for p in pads.values()], apply_wheel())).pack(anchor="w", pady=4)

    def _drop_ref(self, seg: Segment, mtype: str) -> None:
        project = self.app.project
        for ref in list(project.refs_of(seg)):
            if ref.type == mtype:
                if ref.id in seg.extra_material_refs:
                    seg.extra_material_refs.remove(ref.id)
                project.materials.pop(ref.id, None)
        self.app.invalidate()

    # -- effects tab --------------------------------------------------------
    def _goto_mask_page(self) -> None:
        """把属性面板切到「画面 → 蒙版」页。"""
        try:
            notebook = self.picture_sub.master.master   # 顶层 Notebook
            notebook.select(0)                          # 画面
            self.picture_sub.select(3)                  # 蒙版
        except Exception:
            pass

    def _effects_tab(self, parent, seg: Segment, mat) -> None:
        from . import catalog
        from .color import BLEND_MODES

        project = self.app.project

        # blend mode
        ttk.Label(parent, text="混合模式").pack(anchor="w")
        mode_names = ["正常"] + [item["name"] for item in catalog.mix_modes()]
        current_mode = "正常"
        for ref in project.refs_of(seg):
            if ref.type == "mix_mode":
                current_mode = str(ref.get("name") or "正常")
                break
        mode_var = tk.StringVar(value=current_mode)
        mode_box = ttk.Combobox(parent, textvariable=mode_var, values=mode_names, state="readonly")
        mode_box.pack(fill="x", pady=(2, 8))

        def on_mode(_=None):
            name = mode_var.get()
            target = None
            for ref in project.refs_of(seg):
                if ref.type == "mix_mode":
                    target = ref
                    break
            if name == "正常":
                if target is not None and target.id in seg.extra_material_refs:
                    seg.extra_material_refs.remove(target.id)
                    project.materials.pop(target.id, None)
            elif target is None:
                builder.set_mix_mode(project, seg, name)
            else:
                hit = next((m for m in catalog.mix_modes() if m["name"] == name), {})
                target["name"] = name
                target["resource_id"] = hit.get("resource_id", "")
                target["effect_id"] = hit.get("effect_id", "")
            self.app.invalidate()
            self.app.timeline.redraw()

        mode_box.bind("<<ComboboxSelected>>", on_mode)

        # --- 剪映滤镜目录（1052 个名字） -------------------------------------
        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="剪映滤镜（按名字）",
                  font=("Microsoft YaHei", 9, "bold")).pack(anchor="w")
        ttk.Label(parent, text="滤镜载荷在云端，我们只保留名字与 id：导出草稿后剪映能还原",
                  foreground="#8a8f9c", wraplength=320).pack(anchor="w")

        filter_names = [f["name"] for f in catalog.filters()]
        filter_var = tk.StringVar(value="")
        filter_row = ttk.Frame(parent)
        filter_row.pack(fill="x", pady=(4, 2))
        filter_box = ttk.Combobox(filter_row, textvariable=filter_var,
                                  values=filter_names, width=18)
        filter_box.pack(side="left", fill="x", expand=True)
        ttk.Label(filter_row, text=f"{len(filter_names)} 个",
                  foreground="#777").pack(side="left", padx=4)

        def apply_filter() -> None:
            name = filter_var.get().strip()
            if not name:
                return
            if name not in filter_names:
                self.app.set_status(f"没有这个滤镜: {name}")
                return
            builder.add_cloud_filter(project, seg, name, intensity=filter_intensity[0])
            self.app.invalidate()
            self.app.timeline.redraw()
            self.app.set_status(f"已套用滤镜「{name}」（导出草稿后剪映可见）")

        filter_intensity = [1.0]

        def set_filter_intensity(v: float) -> None:
            filter_intensity[0] = float(v)

        ttk.Button(filter_row, text="套用", command=apply_filter).pack(side="left", padx=2)
        self._slider(parent, "滤镜强度", 1.0, 0.0, 1.0, set_filter_intensity)
        ttk.Label(parent, text="提示：输入几个字会自动补全",
                  foreground="#777").pack(anchor="w", pady=(2, 6))

        # 蒙版只在一处维护（画面 → 蒙版页）。这里留一个跳转，避免两个入口
        # 各写一份实现——第 131 轮发现它们已经各自走错过一次。
        mask_hint = ttk.Frame(parent)
        mask_hint.pack(fill="x", pady=(0, 8))
        existing_mask = project.first_ref(seg, "mask")
        mask_text = (f"蒙版：{existing_mask.get('name')}" if existing_mask is not None
                     else "蒙版：无")
        ttk.Label(mask_hint, text=mask_text, foreground="#8a8f9c").pack(side="left")
        ttk.Button(mask_hint, text="去「画面 → 蒙版」",
                   command=self._goto_mask_page).pack(side="right")

        # --- 特效（程序化，不依赖剪映资源包） ------------------------------
        from . import effects as fx

        ttk.Label(parent, text="特效").pack(anchor="w")
        fx_names = {fx.label(k): k for k in fx.available()}
        fx_var = tk.StringVar(value=next(iter(fx_names), ""))
        fx_box = ttk.Combobox(parent, textvariable=fx_var, state="readonly",
                              values=list(fx_names))
        fx_box.pack(fill="x", pady=(2, 2))
        fx_status = ttk.Label(parent, text="", foreground="#666")
        fx_status.pack(anchor="w")

        applied = [r for r in project.refs_of(seg) if r.type == "effect"]
        if applied:
            kind = str(applied[0].get("kind") or "")
            if fx.label(kind) in fx_names:
                fx_var.set(fx.label(kind))
            fx_status.config(text=f"已应用：{fx.label(kind)}")

        def add_fx():
            kind = fx_names.get(fx_var.get())
            if not kind:
                return
            builder.add_effect(project, seg, kind)
            self.app.invalidate()
            self.app.timeline.redraw()
            fx_status.config(text=f"已应用：{fx.label(kind)}")

        def remove_fx():
            self._drop_ref(seg, "effect")
            fx_status.config(text="已移除特效")

        fx_row = ttk.Frame(parent)
        fx_row.pack(fill="x", pady=2)
        ttk.Button(fx_row, text="添加", command=add_fx).pack(side="left")
        ttk.Button(fx_row, text="移除", command=remove_fx).pack(side="left", padx=4)

        # ---- 转场 ----------------------------------------------------------
        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="转场（作用在片段开头）").pack(anchor="w")
        # 分两层：先选「引擎」（17 种，真的决定画面），再选「剪映名」
        # （453 个名字，只用于导出草稿时让剪映认得）。
        engine_labels = ENGINE_LABELS
        engine_keys = list(engine_labels)
        engine_var = tk.StringVar(value="叠化")
        engine_row = ttk.Frame(parent)
        engine_row.pack(fill="x", pady=2)
        ttk.Label(engine_row, text="引擎").pack(side="left")
        ttk.Combobox(engine_row, textvariable=engine_var, state="readonly", width=10,
                     values=[engine_labels[k] for k in engine_keys]).pack(side="left", padx=4)
        ttk.Label(engine_row, text="（决定画面）", foreground="#777").pack(side="left")

        tr_names = catalog.transitions()
        tr_combo = ttk.Combobox(parent, values=[n["name"] for n in tr_names], state="readonly")
        tr_combo.pack(fill="x", pady=2)
        ttk.Label(parent, text="↑ 剪映名（只影响导出草稿时剪映显示的名字）",
                  foreground="#777").pack(anchor="w")
        tr_row = ttk.Frame(parent)
        tr_row.pack(fill="x")
        ttk.Label(tr_row, text="时长(秒)").pack(side="left")
        tr_dur = ttk.Entry(tr_row, width=6)
        tr_dur.insert(0, "0.5")
        tr_dur.pack(side="left", padx=4)

        def add_transition():
            # 剪映名可以留空：这时就用引擎名当显示名，用户不必两个都选
            name = tr_combo.get() or engine_var.get()
            if not name:
                return
            try:
                seconds = float(tr_dur.get() or 0.5)
            except ValueError:
                seconds = 0.5
            self.app.push_undo()
            engine = next((k for k, v in engine_labels.items() if v == engine_var.get()),
                          "cross_dissolve")
            builder.add_transition(project, seg, kind=engine, name=name,
                                   duration_us=int(seconds * 1_000_000))
            self.app.invalidate()
            self.app.timeline.redraw()
            self.app.set_status(f"已添加转场：{name}（引擎 {engine_labels.get(engine, engine)}）")

        def remove_transition():
            for ref in list(project.refs_of(seg)):
                if ref.type == "transition":
                    seg.extra_material_refs = [r for r in seg.extra_material_refs if r != ref.id]
                    project.materials.pop(ref.id, None)
            self.app.invalidate()
            self.app.timeline.redraw()
            self.app.set_status("已移除转场")

        tr_btn = ttk.Frame(parent)
        tr_btn.pack(fill="x", pady=2)
        ttk.Button(tr_btn, text="添加转场", command=add_transition).pack(side="left")
        ttk.Button(tr_btn, text="移除", command=remove_transition).pack(side="left", padx=4)

        # ---- 入场 / 出场 / 循环动画 -----------------------------------------
        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="动画（入场 / 出场 / 循环）").pack(anchor="w")
        anim_items = catalog.animations()
        anim_combo = ttk.Combobox(
            parent,
            values=[f"{a['category']} · {a['name']}" for a in anim_items],
            state="readonly",
        )
        anim_combo.pack(fill="x", pady=2)

        def add_anim():
            label = anim_combo.get()
            if not label or " · " not in label:
                return
            category, name = label.split(" · ", 1)
            self.app.push_undo()
            builder.add_animation(project, seg, name, anim_type=category)
            self.app.invalidate()
            self.app.timeline.redraw()
            self.app.set_status(f"已添加动画：{name}")

        def remove_anim():
            for ref in list(project.refs_of(seg)):
                if ref.type == "material_animation":
                    seg.extra_material_refs = [r for r in seg.extra_material_refs if r != ref.id]
                    project.materials.pop(ref.id, None)
            self.app.invalidate()
            self.app.timeline.redraw()
            self.app.set_status("已移除动画")

        anim_btn = ttk.Frame(parent)
        anim_btn.pack(fill="x", pady=2)
        ttk.Button(anim_btn, text="添加动画", command=add_anim).pack(side="left")
        ttk.Button(anim_btn, text="移除", command=remove_anim).pack(side="left", padx=4)

        # 参数滑块：按 EFFECT_SPEC 里声明的默认值/范围生成
        if applied:
            kind = str(applied[0].get("kind") or "")
            spec = fx.EFFECT_SPEC.get(kind) or {}
            params = applied[0].get("params") or {}
            spec_params = spec.get("params") or {}
            if spec_params:
                ttk.Label(parent, text="参数").pack(anchor="w", pady=(6, 0))
            for key, default in spec_params.items():
                lo = 0.0
                hi = max(1.0, float(default) * 4.0) if key not in ("period_s",) else 3.0
                if key in ("block",):
                    lo, hi = 2.0, 64.0
                elif key in ("radius",):
                    lo, hi = 0.0, 40.0
                elif key in ("axis",):
                    lo, hi = 0.0, 1.0
                elif key in ("strength", "amount", "grain", "flicker"):
                    lo, hi = 0.0, 1.0
                cur = float(params.get(key, default))

                def on_change(v, k=key, m=applied[0]):
                    vals = dict(m.get("params") or {})
                    vals[k] = float(v)
                    m["params"] = vals
                    self.app.invalidate()

                self._slider(parent, key, cur, lo, hi, on_change,
                             0.01 if hi <= 3 else 0.5, "{:.2f}" if hi <= 3 else "{:.0f}")
        ttk.Label(parent, text="程序化特效（自研，非剪映资源包）；导出剪映草稿时会跳过并提示",
                  foreground="#777").pack(anchor="w")

        ttk.Separator(parent).pack(fill="x", pady=8)

        # --- LUT 滤镜 ------------------------------------------------------
        from . import luts

        ttk.Label(parent, text="LUT 滤镜（.cube）").pack(anchor="w")
        lib_holder = {"entries": []}
        lut_var = tk.StringVar(value="")
        lut_box = ttk.Combobox(parent, textvariable=lut_var, values=[], state="readonly")
        lut_box.pack(fill="x", pady=(2, 2))
        status = ttk.Label(parent, text="", foreground="#666")
        status.pack(anchor="w")

        current = None
        for ref in project.refs_of(seg):
            if ref.type == "filter":
                current = ref
                break

        def refresh_lib(force: bool = False):
            entries = luts.library(refresh=force)
            lib_holder["entries"] = entries
            labels = [f"{e.name}  [{e.source}]" for e in entries]
            lut_box["values"] = labels
            if current is not None:
                path = str(current.get("path") or "")
                for i, e in enumerate(entries):
                    if os.path.abspath(path) == os.path.abspath(e.path):
                        lut_box.current(i)
                        break
            status.config(text=f"库中 {len(entries)} 个 LUT" if entries
                          else "库里还没有 LUT；点“刷新库”，或把 .cube 放进 FREECUT_LUT_DIRS")

        def apply_lut(_=None):
            idx = lut_box.current()
            if idx < 0 or idx >= len(lib_holder["entries"]):
                return
            entry = lib_holder["entries"][idx]
            intensity = float(intensity_var.get())
            if current is None:
                builder.add_filter(project, seg, entry.path, intensity)
            else:
                current["path"] = os.path.abspath(entry.path)
                current["intensity_value"] = intensity
            self.app.invalidate()
            self.app.timeline.redraw()
            status.config(text=f"已应用：{entry.name}  强度 {intensity:.2f}")

        # --- 自研 LUT 预设（本地生成，一键套用） ---------------------------
        from . import lutgen

        ttk.Separator(parent).pack(fill="x", pady=6)
        ttk.Label(parent, text="自研 LUT 预设",
                  font=("Microsoft YaHei", 9, "bold")).pack(anchor="w")
        ttk.Label(parent, text="本地生成的标准 .cube，不依赖剪映云端素材",
                  foreground="#8a8f9c").pack(anchor="w")

        preset_var = tk.StringVar(value=next(iter(lutgen.labels().values())))
        preset_row = ttk.Frame(parent)
        preset_row.pack(fill="x", pady=(4, 2))
        labels_to_name = {v: k for k, v in lutgen.labels().items()}
        ttk.Combobox(preset_row, textvariable=preset_var, state="readonly", width=10,
                     values=list(lutgen.labels().values())).pack(side="left")
        ttk.Button(preset_row, text="生成并套用",
                   command=lambda: apply_builtin_lut(preset_var.get())).pack(side="left", padx=4)

        def apply_builtin_lut(label: str) -> None:
            name = labels_to_name.get(label)
            if not name:
                return
            target_dir = lutgen.default_dir()
            path = os.path.join(target_dir, f"{name}.cube")
            try:
                if not os.path.isfile(path):
                    lutgen.write_cube(name, path)
                intensity = float(intensity_var.get())
                if current is None:
                    builder.add_filter(project, seg, path, intensity)
                else:
                    current["path"] = os.path.abspath(path)
                    current["intensity_value"] = intensity
                refresh_lib(True)
                self.app.invalidate()
                self.app.timeline.redraw()
                status.config(text=f"已套用自研 LUT：{label}  强度 {intensity:.2f}")
            except Exception as exc:
                status.config(text=f"生成失败：{exc}")

        def remove_lut():
            self._drop_ref(seg, "filter")
            status.config(text="已移除滤镜")

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Button(row, text="刷新库", command=lambda: refresh_lib(True)).pack(side="left")
        ttk.Button(row, text="应用", command=apply_lut).pack(side="left", padx=4)
        ttk.Button(row, text="移除", command=remove_lut).pack(side="left")

        intensity_var = tk.DoubleVar(
            value=float(current.get("intensity_value", 1.0)) if current is not None else 1.0
        )

        def on_intensity(_=None):
            if current is not None:
                current["intensity_value"] = float(intensity_var.get())
                self.app.invalidate()

        ttk.Scale(parent, from_=0.0, to=1.0, variable=intensity_var,
                  command=on_intensity).pack(fill="x", pady=2)
        lut_box.bind("<<ComboboxSelected>>", apply_lut)
        refresh_lib()

        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text=f"滤镜目录: {len(catalog.filters())} 个 / 转场: {len(catalog.transitions())} 个",
                  foreground="#666").pack(anchor="w")

    def _sound_tab(self, parent, seg: Segment, mat) -> None:
        self._slider(parent, "音量", float(seg.volume), 0.0, 2.0,
                     lambda v: setattr(seg, "volume", float(v)))
        spd = self._slider(parent, "速度", float(seg.speed), 0.25, 4.0,
                           lambda v: self._apply_speed(seg, v), 0.01, "{:.2f}x")
        ttk.Label(parent, text="改变速度会同步调整片段时长", foreground="#777").pack(anchor="w", pady=(6, 0))

        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="淡入 / 淡出 (毫秒)").pack(anchor="w")
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        fi = tk.IntVar(value=self._fade(seg, 0))
        fo = tk.IntVar(value=self._fade(seg, 1))
        ttk.Entry(row, textvariable=fi, width=7).pack(side="left")
        ttk.Label(row, text=" / ").pack(side="left")
        ttk.Entry(row, textvariable=fo, width=7).pack(side="left")

        # ---- 音频效果：降噪 / 变声 / 三段均衡 ------------------------------
        project = self.app.project

        def effect_mat():
            for ref in project.refs_of(seg):
                if ref.type == "audio_effect":
                    return ref
            return None

        def apply_effect(**kw):
            builder.set_audio_effects(project, seg, **kw)
            self.app.invalidate()

        # ---- 曲线变速 ------------------------------------------------------
        from . import speed as speed_mod
        from .gui_widgets import CurveEditor

        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="曲线变速（纵轴 = 速度倍率 0.25×–4×）").pack(anchor="w")
        sp_editor = CurveEditor(parent, height=120)
        sp_editor.set_points(speed_mod.to_display(seg.speed_curve or speed_mod.PRESETS["匀速"]))
        sp_editor.pack(fill="x", pady=4)

        sp_hint = ttk.Label(parent, text="", foreground="#777")
        sp_hint.pack(anchor="w")

        def sp_apply(points):
            try:
                builder.set_speed_curve(project, seg, speed_mod.from_display(points))
            except ValueError:
                return
            self.app.invalidate()
            self.app.timeline.redraw()
            avg = speed_mod.average_speed(seg.speed_curve)
            sp_hint.config(text=f"等效速度 {avg:.2f}×（音频按等效速度处理）")

        sp_editor.on_change = sp_apply

        preset_row = ttk.Frame(parent)
        preset_row.pack(fill="x", pady=2)
        for name, pts in speed_mod.PRESETS.items():
            ttk.Button(
                preset_row, text=name, width=8,
                command=lambda p=pts: (sp_editor.set_points(speed_mod.to_display(speed_mod.normalize(
                    [{"x": x, "y": y} for x, y in p]))), sp_apply(sp_editor.get_points())),
            ).pack(side="left", padx=1)

        ttk.Separator(parent).pack(fill="x", pady=8)
        ttk.Label(parent, text="音频效果").pack(anchor="w")
        cur = effect_mat() or {}
        eq = cur.get("eq") or (0.0, 0.0, 0.0)
        if isinstance(eq, dict):
            eq = (eq.get("low", 0.0), eq.get("mid", 0.0), eq.get("high", 0.0))

        self._slider(parent, "降噪", float(cur.get("denoise", 0.0) or 0.0), 0.0, 1.0,
                     lambda v: apply_effect(denoise=float(v)), 0.01, "{:.2f}")
        self._slider(parent, "变声（音高倍率）", float(cur.get("pitch", 1.0) or 1.0),
                     0.5, 2.0, lambda v: apply_effect(pitch=float(v)), 0.01, "{:.2f}x")
        ttk.Label(parent, text="1.00 = 原声，<1 低沉，>1 尖锐（时长不变）",
                  foreground="#777").pack(anchor="w")
        for idx, (label, key) in enumerate((("低频 (dB)", 0), ("中频 (dB)", 1), ("高频 (dB)", 2))):
            self._slider(
                parent, label, float(eq[idx]) if idx < len(eq) else 0.0, -12.0, 12.0,
                lambda v, i=idx: apply_effect(
                    eq=[float(eq[j]) if j < len(eq) else 0.0 for j in range(3)][:i]
                    + [float(v)]
                    + [float(eq[j]) if j < len(eq) else 0.0 for j in range(i + 1, 3)]),
                0.5, "{:.1f}",
            )

        def apply_fade(_=None):
            self._set_fade(seg, int(fi.get()), int(fo.get()))
            self.app.invalidate()

        ttk.Button(row, text="应用", command=apply_fade).pack(side="left", padx=6)

    def _apply_speed(self, seg: Segment, speed: float) -> None:
        if speed <= 0:
            return
        old = seg.speed if seg.speed > 0 else 1.0
        if abs(speed - old) < 1e-6:
            return
        seg.source_timerange.duration = int(seg.source_timerange.duration * speed / old)
        seg.target_timerange.duration = int(seg.target_timerange.duration * old / speed)
        seg.speed = float(speed)

    def _fade(self, seg: Segment, which: int) -> int:
        for ref in self.app.project.refs_of(seg):
            if ref.type == "audio_fade":
                key = "fade_in_duration" if which == 0 else "fade_out_duration"
                return int(int(ref.get(key, 0) or 0) / 1000)
        return 0

    def _set_fade(self, seg: Segment, fin_ms: int, fout_ms: int) -> None:
        from .model import Material

        for ref in self.app.project.refs_of(seg):
            if ref.type == "audio_fade":
                ref["fade_in_duration"] = fin_ms * 1000
                ref["fade_out_duration"] = fout_ms * 1000
                return
        mat = Material(type="audio_fade", fade_in_duration=fin_ms * 1000,
                       fade_out_duration=fout_ms * 1000)
        self.app.project.add_material(mat)
        seg.extra_material_refs.append(mat.id)


# --------------------------------------------------------------------------- #
# application
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self, project_path: Optional[str] = None):
        super().__init__()
        self.title(APP_TITLE)
        # 界面尺寸跟着 DPI 走：150% 缩放下写死的 320px 面板会显得又窄又挤，
        # 而 Tk 的 ttk 控件又不会自己缩放。
        self._dpi_scale = max(1.0, float(self.winfo_fpixels("1i")) / 96.0)
        self.geometry(f"{self._px(1460)}x{self._px(880)}+40+40")
        # 最小尺寸照 ReaCut 的排版下限（1060×700）：再窄三栏就挤了
        self.minsize(self._px(theme.MIN_WIDTH), self._px(theme.MIN_HEIGHT))
        self.configure(bg=theme.BG_APP)
        self.project: Project = builder.new_project(1280, 720, 30)
        self._project_path: Optional[str] = None
        self.playhead = 0
        self.playing = False
        self._renderer: Optional[Renderer] = None
        self._render_dirty = True
        self._photo = None
        self._undo: List[str] = []
        self._redo: List[str] = []
        self.tts_voice = ""
        self.tts_rate = 0
        self._mix_path: Optional[str] = None      # 预览用的混音 wav
        self._mix_dirty = True
        self._audio_job = None
        self._play_t0 = 0.0                        # 播放起始的时刻（墙钟）
        self._play_from = 0                        # 播放起始的播放头
        self.preview_muted = False
        self.preview_volume = 1.0
        self.preview_quality = "自动"          # 自动 / 全尺寸 / 半 / 四分之一
        self._renderer_size = (0, 0)
        self.frames_shown = 0                  # 诊断用：实际渲染过多少帧
        self._play_job = None
        self._render_job = None

        self._build_style()
        self._build_widgets()
        self._build_menu()

        from .media import ffmpeg_path

        try:
            ffmpeg_path()
        except Exception as exc:
            self.after(200, lambda: messagebox.showwarning(
                "缺少 FFmpeg",
                "未找到 ffmpeg，预览与导出都不可用。\n\n"
                "请安装 FFmpeg 并加入 PATH，或设置环境变量 FREECUT_FFMPEG。\n\n"
                f"详细信息: {exc}",
            ))

        # 不同字体/DPI 下控件会要求更宽，窗口不够宽就会把右栏挤掉。
        # 这里按实际需求尺寸把窗口撑开（不超过屏幕），保证布局完整。
        self.update()          # 真正完成一次布局，否则子控件的 winfo_x 全是 0
        # 工具栏按钮不换行，窗口窄了右边就会被切掉，所以把它的需求宽度也算进来
        bar_w = 0
        try:
            kids = self._toolbar.winfo_children()
            right_edge = 0
            for child in kids:
                right_edge = max(right_edge, child.winfo_x() + child.winfo_reqwidth())
            bar_w = max(int(self._toolbar.winfo_reqwidth()), right_edge) + self._px(40)
        except Exception:
            pass
        need_w = max(self.winfo_reqwidth(), bar_w)
        need_h = self.winfo_reqheight()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        fit_w = min(max(need_w, self._px(1200)), screen_w - 40)
        fit_h = min(max(need_h, self._px(800)), screen_h - 80)
        if fit_w > self.winfo_width() or fit_h > self.winfo_height():
            self.geometry(f"{fit_w}x{fit_h}+20+20")
        self.minsize(min(self._px(1000), fit_w), min(self._px(640), fit_h))

        if os.environ.get("FREECUT_DEBUG_LAYOUT"):
            # 排查布局用：打印关键控件尺寸后退出，不进入 mainloop
            self.update_idletasks()
            self.after(800, self._dump_layout)
        if project_path and os.path.isfile(project_path):
            self.open_project(project_path)
        else:
            self._bootstrap_demo()
        self.after(50, self.refresh_preview)

    # -- menu ---------------------------------------------------------------
    def _build_menu(self) -> None:
        """Menu bar: everything the CLI can do but the toolbar had no room for."""
        bar = tk.Menu(self)

        file_menu = tk.Menu(bar, tearoff=0)
        file_menu.add_command(label="新建", command=self.new_project, accelerator="Ctrl+N")
        file_menu.add_command(label="打开…", command=self.open_project, accelerator="Ctrl+O")
        file_menu.add_command(label="保存", command=self.save_project, accelerator="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_command(label="导入剪映草稿…", command=self.import_jianying)
        file_menu.add_command(label="导入 Premiere 工程 (.prproj)…", command=self.import_prproj)
        file_menu.add_separator()
        file_menu.add_command(label="导出剪映草稿…", command=self.export_jianying_draft)
        file_menu.add_command(label="导出 XML (FCP7 / PR 可导入)…", command=self.export_fcp7)
        file_menu.add_command(label="导出视频…", command=self.do_export)
        bar.add_cascade(label="文件", menu=file_menu)

        edit_menu = tk.Menu(bar, tearoff=0)
        edit_menu.add_command(label="撤销", command=self.undo, accelerator="Ctrl+Z")
        edit_menu.add_command(label="重做", command=self.redo, accelerator="Ctrl+Y")
        edit_menu.add_separator()
        edit_menu.add_command(label="在播放头拆分", command=self.split_at_playhead)
        edit_menu.add_command(label="删除所选", command=self.delete_selected)
        bar.add_cascade(label="编辑", menu=edit_menu)

        tool_menu = tk.Menu(bar, tearoff=0)
        layout_menu = tk.Menu(tool_menu, tearoff=0)
        for name in __import__("freecut.layouts", fromlist=["layouts"]).names():
            layout_menu.add_command(
                label=name,
                command=lambda n=name: self.apply_layout(n),
            )
        tool_menu.add_cascade(label="多画面布局（分屏 / 画中画）", menu=layout_menu)
        tool_menu.add_separator()
        tool_menu.add_command(label="图文成片（文案 → 配音 + 字幕 + 画面）…",
                              command=self.from_script)
        tool_menu.add_command(label="配音设置（音色 / 语速 / 试听）…", command=self.voice_settings)
        tool_menu.add_command(label="一键成片（按画面分镜）…", command=self.autocut_scene)
        tool_menu.add_command(label="一键成片（按音乐节拍）…", command=self.autocut_beat)
        tool_menu.add_separator()
        tool_menu.add_command(label="素材体检 / 重新链接…", command=self.doctor_media)
        tool_menu.add_command(label="导出报告（剪映 / XML 两个方向）…",
                              command=self.show_export_report)
        tool_menu.add_command(label="校验导出的成品…", command=self.verify_output)
        tool_menu.add_separator()
        tool_menu.add_separator()
        tool_menu.add_command(label="恢复默认面板布局", command=self.reset_panes)
        tool_menu.add_command(label="刷新 LUT 库", command=self.refresh_luts)
        bar.add_cascade(label="工具", menu=tool_menu)

        help_menu = tk.Menu(bar, tearoff=0)
        help_menu.add_command(label="示例导览（状态栏提示）", command=self.show_tour)
        help_menu.add_command(label="运行自检（验证交付物）…",
                              command=self.run_selfcheck_dialog)
        help_menu.add_command(label="关于 FreeCut", command=self.show_about)
        help_menu.add_separator()
        help_menu.add_command(label="快捷键", command=self.show_shortcuts)
        bar.add_cascade(label="帮助", menu=help_menu)

        self.config(menu=bar)


    # -- tools that used to be CLI-only -------------------------------------
    def import_prproj(self) -> None:
        path = filedialog.askopenfilename(
            title="导入 Premiere 工程", filetypes=[("Premiere 工程", "*.prproj"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            from .prproj import import_project

            self.push_undo()
            project, info = import_project(path)      # 返回 (project, 摘要)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self.project = project
        self._project_path = None
        self.playhead = 0
        self.refresh_bin()
        self.timeline.redraw()
        self.props.rebuild(None)
        self.invalidate()
        self._check_missing_media()
        self.set_status(
            f"已导入 PR 工程「{info.get('sequence', '')}」："
            f"{sum(len(t.segments) for t in project.tracks)} 个片段"
            f"（调节图层 {info.get('adjustment', 0)} 个）")

    def export_fcp7(self) -> None:
        path = filedialog.asksaveasfilename(
            title="导出 FCP7 XML（PR / DaVinci / FCP 可读）",
            defaultextension=".xml", filetypes=[("Final Cut Pro 7 XML", "*.xml")])
        if not path:
            return
        try:
            from . import interchange

            interchange.write_fcp7(self.project, path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.set_status(f"已导出 XML：{os.path.basename(path)}")

    def _autocut(self, mode: str) -> None:
        paths = [str(m.get("path")) for m in self.project.materials.values()
                 if getattr(m, "type", "") == "video" and str(m.get("path") or "")]
        if not paths:
            messagebox.showinfo("一键成片", "素材库里还没有视频，先导入几段素材。")
            return
        try:
            from . import autocut

            music = None
            if mode == "beat":
                music = filedialog.askopenfilename(
                    title="选择音乐（按节拍切）", filetypes=[("音频", "*.mp3 *.m4a *.wav *.aac"), ("所有文件", "*.*")])
                if not music:
                    return
            new_project = (autocut.build_beat_project(paths, music) if mode == "beat"
                           else autocut.build_scene_project(paths))
        except Exception as exc:
            messagebox.showerror("一键成片失败", str(exc))
            return
        self.push_undo()
        self.project = new_project
        self._project_path = None
        self.playhead = 0
        self.refresh_bin()
        self.timeline.redraw()
        self.props.rebuild(None)
        self.invalidate()
        total = sum(len(t.segments) for t in new_project.tracks)
        self.set_status(f"一键成片完成：{total} 个片段")

    def apply_layout(self, name: str) -> None:
        """Tile the video-track clips with a split-screen / PiP preset."""
        from . import layouts

        count = layouts.fit_count(self.project, name)
        if count == 0:
            messagebox.showinfo("布局", "时间线上还没有视频片段。")
            return
        self.push_undo()
        placed = layouts.apply(self.project, name)
        self.invalidate()
        self.timeline.redraw()
        self.props.rebuild(self.timeline.selected)
        self.set_status(f"已套用「{name}」：{placed} 个片段（{layouts.describe(name)}）")

    def voice_settings(self) -> None:
        """Pick the SAPI voice / rate for 图文成片, with a live preview."""
        from . import tts

        names = tts.voices()
        if not names:
            messagebox.showinfo("配音", "这台机器没有可用的语音合成引擎（需要 Windows SAPI）。")
            return

        top = tk.Toplevel(self)
        top.title("配音设置")
        top.configure(bg="#202027")
        top.transient(self)
        top.resizable(False, False)
        body = ttk.Frame(top, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="音色").pack(anchor="w")
        voice_var = tk.StringVar(value=self.tts_voice or tts.pick_voice())
        ttk.Combobox(body, textvariable=voice_var, values=names,
                     state="readonly", width=42).pack(fill="x", pady=(2, 8))

        rate_var = tk.DoubleVar(value=self.tts_rate)
        rate_lbl = ttk.Label(body, text=f"语速 {self.tts_rate:+.0f}")
        rate_lbl.pack(anchor="w")

        def on_rate(_=None):
            rate_lbl.configure(text=f"语速 {int(float(rate_var.get())):+d}")

        ttk.Scale(body, from_=-10, to=10, variable=rate_var,
                  command=on_rate).pack(fill="x", pady=(2, 8))

        ttk.Label(body, text="试听文本").pack(anchor="w")
        sample = tk.Text(body, height=3, width=46, bg="#26262c", fg="#e8e8ee",
                         insertbackground="#e8e8ee", relief="flat")
        sample.insert("1.0", "这是一段试听文本，用来确认音色和语速。")
        sample.pack(fill="x", pady=(2, 8))

        status = ttk.Label(body, text="", foreground="#7ec0ff")
        status.pack(anchor="w")

        def do_preview():
            text = sample.get("1.0", "end").strip()
            if not text:
                return
            status.configure(text="合成中…")
            top.update_idletasks()

            # 合成放到后台线程，但**只在主线程碰 Tk**（Tk 不是线程安全的）
            import threading

            result = {}
            # Tk 变量也只能在主线程读
            chosen_voice = voice_var.get()
            chosen_rate = int(rate_var.get())

            def work():
                result["path"] = tts.preview(text, voice=chosen_voice, rate=chosen_rate)

            def poll():
                if "path" not in result:
                    top.after(80, poll)
                    return
                status.configure(text="试听中…" if result["path"]
                                 else "合成失败（本机可能没有这个音色）")

            threading.Thread(target=work, daemon=True).start()
            top.after(80, poll)

        row = ttk.Frame(body)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="试听", command=do_preview).pack(side="left")
        ttk.Button(row, text="取消", command=top.destroy).pack(side="right")

        def save():
            self.tts_voice = voice_var.get()
            self.tts_rate = int(float(rate_var.get()))
            self.set_status(f"配音设置：{self.tts_voice} / 语速 {self.tts_rate:+d}")
            top.destroy()

        ttk.Button(row, text="确定", command=save).pack(side="right", padx=6)

    def from_script(self) -> None:
        """图文成片: narration from a script file + subtitles + footage."""
        script_path = filedialog.askopenfilename(
            title="选择文案（txt）", filetypes=[("文本", "*.txt *.md"), ("所有文件", "*.*")])
        if not script_path:
            return
        try:
            with open(script_path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception as exc:
            messagebox.showerror("读取文案失败", str(exc))
            return

        footage = [str(m.get("path")) for m in self.project.materials.values()
                   if getattr(m, "type", "") in ("video", "image") and str(m.get("path") or "")]
        if not footage:
            picked = filedialog.askopenfilenames(
                title="选择画面素材（视频/图片）",
                filetypes=[("媒体", "*.mp4 *.mov *.mkv *.jpg *.png"), ("所有文件", "*.*")])
            if not picked:
                return
            footage = list(picked)

        import tempfile

        try:
            from . import scriptfilm

            stats = scriptfilm.build_script_project(
                text, footage, work_dir=tempfile.mkdtemp(prefix="freecut-tts-"),
                width=self.project.canvas.width, height=self.project.canvas.height,
                fps=self.project.fps, voice=self.tts_voice, rate=self.tts_rate)
        except Exception as exc:
            messagebox.showerror("图文成片失败", str(exc))
            return

        self.push_undo()
        self.project = stats["project"]
        self._project_path = None
        self.playhead = 0
        self.refresh_bin()
        self.timeline.redraw()
        self.props.rebuild(None)
        self.invalidate()
        self.set_status("图文成片完成：" + scriptfilm.summary(stats))

    def autocut_scene(self) -> None:
        self._autocut("scene")

    def autocut_beat(self) -> None:
        self._autocut("beat")

    def doctor_media(self) -> None:
        from . import relink

        items = relink.missing_media(self.project)
        if not items:
            messagebox.showinfo("素材体检", "所有素材都在线。")
            return
        self._check_missing_media()

    def verify_output(self) -> None:
        path = filedialog.askopenfilename(
            title="校验成品", filetypes=[("视频", "*.mp4 *.mov *.mkv"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            from .verify import verify

            rep = verify(path)
        except Exception as exc:
            messagebox.showerror("校验失败", str(exc))
            return
        lines = [f"{path}", ""]
        lines += [f"· {p}" for p in rep.problems] or ["· 没有发现问题"]
        if rep.warnings:
            lines += [""] + [f"提示: {w}" for w in rep.warnings]
        (messagebox.showwarning if rep.problems else messagebox.showinfo)(
            "校验结果", "\n".join(lines))

    def refresh_luts(self) -> None:
        from . import luts

        count = len(luts.library(refresh=True))
        self.set_status(f"LUT 库已刷新：{count} 条")

    def show_shortcuts(self) -> None:
        messagebox.showinfo("快捷键", "\n".join([
            "空格      播放 / 暂停",
            "← →      上一帧 / 下一帧",
            "Ctrl+Z   撤销      Ctrl+Y 重做",
            "Ctrl+S   保存      Delete 删除所选",
            "Ctrl+滚轮 缩放时间线",
        ]))

    def destroy(self) -> None:
        """Cancel pending callbacks before tearing Tk down (avoids Tcl errors)."""
        self._stop_audio()
        for job in ("_render_job", "_play_job", "_tour_job"):
            jid = getattr(self, job, None)
            if jid:
                try:
                    self.after_cancel(jid)
                except Exception:
                    pass
                setattr(self, job, None)
        try:
            if self._renderer is not None:
                self._renderer.close()
                self._renderer = None
        except Exception:
            pass
        super().destroy()

    def _dump_layout(self) -> None:
        """Print widget geometry (FREECUT_DEBUG_LAYOUT=1) and quit."""
        self.update()
        rows = [
            f"window {self.winfo_width()}x{self.winfo_height()}",
            f"preview {self.preview.winfo_width()}x{self.preview.winfo_height()}",
            f"props {self.props.winfo_width()}x{self.props.winfo_height()}",
            f"timeline {self.timeline.winfo_width()}x{self.timeline.winfo_height()}",
            f"tk scaling {self.tk.call('tk', 'scaling')}",
            f"dpi {self.winfo_fpixels('1i'):.0f}",
            f"toolbar edge "
            + str(max([c.winfo_x() + c.winfo_reqwidth()
                       for c in self._toolbar.winfo_children()] or [0])),
        ]
        for line in rows:
            print(line)
        self.quit()

    # -- style --------------------------------------------------------------
    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=theme.BG_PANEL, foreground=theme.FG,
                        fieldbackground=theme.BG_INPUT)
        style.configure("TFrame", background=theme.BG_PANEL)
        style.configure("TLabel", background=theme.BG_PANEL, foreground=theme.FG)
        style.configure("TButton", background=theme.BG_ELEV, foreground=theme.FG, padding=4)
        style.map("TButton", background=[("active", theme.BORDER_STRONG)])
        # 面板标题：统一高度（ReaCut 的 52px 约定），字重加粗
        style.configure("Header.TLabel", background=theme.BG_PANEL, foreground=theme.FG,
                        font=("Microsoft YaHei", 10, "bold"), padding=(10, 12))
        # 左侧类别栏：默认低调，选中时青色底+黑字
        style.configure("Rail.TButton", background=theme.BG_PANEL, foreground=theme.FG_DIM,
                        relief="flat", padding=(4, 8), font=("Microsoft YaHei", 8))
        style.map("Rail.TButton",
                  background=[("active", theme.BG_ELEV)],
                  foreground=[("active", theme.FG)])
        style.configure("RailOn.TButton", background=theme.ACCENT, foreground="#08222a",
                        relief="flat", padding=(4, 8), font=("Microsoft YaHei", 8, "bold"))
        style.configure("Card.TFrame", background=theme.BG_ELEV)
        style.configure("TNotebook", background="#202027", borderwidth=0)
        style.configure("TNotebook.Tab", background="#2a2a32", foreground="#c8c8d0", padding=(10, 4))
        style.map("TNotebook.Tab", background=[("selected", "#3a3a46")])
        style.configure("TScale", background="#202027")
        style.configure("Horizontal.TProgressbar", background="#4a8fd0")

    # -- widgets ------------------------------------------------------------
    def _build_widgets(self) -> None:
        bar = ttk.Frame(self, padding=(8, 6))
        self._toolbar = bar
        bar.pack(fill="x")
        ttk.Button(bar, text="新建", command=self.new_project).pack(side="left", padx=2)
        ttk.Button(bar, text="打开", command=self.open_project).pack(side="left", padx=2)
        ttk.Button(bar, text="保存", command=self.save_project).pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self.undo_btn = ttk.Button(bar, text="↶ 撤销", command=self.undo, state="disabled", width=7)
        self.undo_btn.pack(side="left", padx=2)
        self.redo_btn = ttk.Button(bar, text="↷ 重做", command=self.redo, state="disabled", width=7)
        self.redo_btn.pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="导入媒体", command=self.import_media).pack(side="left", padx=2)
        ttk.Button(bar, text="导入剪映草稿", command=self.import_jianying).pack(side="left", padx=2)
        ttk.Button(bar, text="添加文本", command=self.add_text).pack(side="left", padx=2)
        ttk.Button(bar, text="调节图层", command=self.add_adjustment_layer).pack(side="left", padx=2)
        ttk.Button(bar, text="拆分", command=self.split_at_playhead).pack(side="left", padx=2)
        ttk.Button(bar, text="删除", command=self.delete_selected).pack(side="left", padx=2)
        ttk.Button(bar, text="导出", command=self.do_export).pack(side="right", padx=2)
        ttk.Button(bar, text="导出剪映草稿", command=self.export_jianying_draft).pack(side="right", padx=2)
        self.status = ttk.Label(bar, text="就绪", foreground="#8fbf8f")
        self.status.pack(side="right", padx=10)

        # 三栏用 Panedwindow：分隔线可拖（ReaCut 的界面说明里也这么做），
        # 宽度会被记住，双击分隔线/菜单项可以恢复默认。
        from . import uistate

        self._ui_state = uistate.load()
        left_w, _mid, right_w = uistate.pane_widths(self._ui_state)
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6, pady=4)
        self.panes = body

        # left: 类别栏 + 内容区（照剪映/ReaCut：左边竖排类别，右边卡片流）
        left = ttk.Frame(body, width=left_w)
        body.add(left, weight=0)
        self._build_left(left)

        # center: preview + transport + timeline (grid so the timeline keeps its height)
        center = ttk.Frame(body)
        body.add(center, weight=1)
        center.grid_columnconfigure(0, weight=1)
        center.grid_rowconfigure(0, weight=1)
        # 请求尺寸给小值：Tk 的 Panedwindow 不会把子控件压到请求尺寸以下，
        # 预览画布写死 620x300 会把时间线顶出窗口底部（第 185 轮实测时间线 y=867 > 800）。
        self.preview = tk.Canvas(center, bg="#0d0d10", highlightthickness=0,
                                 height=self._px(120), width=self._px(320))
        self.preview.grid(row=0, column=0, sticky="nsew")
        self._build_welcome(center)
        self.preview.bind("<Configure>", lambda e: self._schedule_render())

        self.transport = ttk.Frame(center, padding=(4, 4))
        self.transport.grid(row=1, column=0, sticky="ew")
        self.play_btn = ttk.Button(self.transport, text="▶ 播放", command=self.toggle_play, width=9)
        self.play_btn.pack(side="left")
        ttk.Button(self.transport, text="◀ 上一帧", command=lambda: self.step(-1)).pack(side="left", padx=2)
        ttk.Button(self.transport, text="下一帧 ▶", command=lambda: self.step(1)).pack(side="left")
        ttk.Label(self.transport, text="预览画质").pack(side="left", padx=(8, 2))
        quality_var = tk.StringVar(value=self.preview_quality)
        box = ttk.Combobox(self.transport, textvariable=quality_var, width=7, state="readonly",
                           values=["自动", "全尺寸", "半", "三分之一", "四分之一"])
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda e: self.set_preview_quality(quality_var.get()))
        self.mute_btn = ttk.Button(self.transport, text="🔊", width=3, command=self.toggle_mute)
        self.mute_btn.pack(side="left", padx=(8, 2))
        vol_var = tk.DoubleVar(value=1.0)
        ttk.Scale(self.transport, from_=0.0, to=1.0, variable=vol_var, length=self._px(80),
                  command=lambda v: setattr(self, "preview_volume", float(v))
                  ).pack(side="left", padx=(0, 8))
        self.time_lbl = ttk.Label(self.transport, text="00:00.000", foreground="#7ec0ff",
                                  font=("Consolas", 11))
        self.time_lbl.pack(side="left", padx=12)
        ttk.Label(self.transport, text="跳转到(秒)").pack(side="left", padx=(8, 2))
        self.goto = ttk.Entry(self.transport, width=8)
        self.goto.pack(side="left")
        self.goto.bind("<Return>", lambda e: self.set_playhead(parse_time(self.goto.get())))

        # 高度按「表头 + 5 条轨道」算：24 + 5*40 = 224，留点余量给第 6 条
        self.timeline = TimelineView(center, self, height=self._px(254))
        self.timeline.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        center.grid_rowconfigure(2, weight=0, minsize=self._px(254))

        # right: properties（给一个最小宽度，否则预览区会把面板挤成一条缝）
        right = ttk.Frame(body, width=right_w)
        body.add(right, weight=0)
        # 初始宽度：拖过就记住，没拖过用默认值。
        # 必须等窗口真正布局完再设 sash——刚建出来时 winfo_width() 还是 1。
        self.pane_defaults = (left_w, right_w)
        self.after(60, lambda: self._apply_sashes(left_w, right_w))

        # Ctrl+←/→ 微调左分隔线，Ctrl+Shift+←/→ 微调右分隔线
        for seq, index, step in (
            ("<Control-Left>", 0, -8), ("<Control-Right>", 0, 8),
            ("<Control-Shift-Left>", 1, -8), ("<Control-Shift-Right>", 1, 8),
        ):
            self.bind_all(seq, lambda e, i=index, s=step: self.nudge_sash(i, s))
        right.pack_propagate(False)
        body.grid_columnconfigure(2, minsize=self._px(360), weight=0)
        self.props = PropertiesPanel(right, self)
        self.props.pack(fill="both", expand=True)

        self.bind("<space>", lambda e: self.toggle_play())
        self.bind("<Control-Shift-E>", lambda e: self.export_flattened_draft())
        self.bind("<Delete>", lambda e: self.delete_selected())
        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1))
        self.bind("<Control-s>", lambda e: self.save_project())
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Control-Z>", lambda e: self.redo())        # Ctrl+Shift+Z
        self.bind("<Control-y>", lambda e: self.redo())

    def _px(self, n: float) -> int:
        """Scaling-aware pixel size (1.0 at 96 dpi, 1.5 at 144 dpi)."""
        return int(round(float(n) * getattr(self, "_dpi_scale", 1.0)))

    # -- project lifecycle --------------------------------------------------
    def _bootstrap_demo(self) -> None:
        p = self.project
        p.canvas.width, p.canvas.height = 1280, 720
        self.invalidate()
        self.refresh_bin()
        self.timeline.redraw()

    def new_project(self) -> None:
        if not messagebox.askokcancel("新建", "放弃当前项目并新建？"):
            return
        self.project = builder.new_project(1280, 720, 30)
        self._project_path = None
        self.playhead = 0
        self.invalidate()
        self.refresh_bin()
        self.timeline.redraw()
        self.props.rebuild(None)

    def open_project(self, path: Optional[str] = None) -> None:
        path = path or filedialog.askopenfilename(filetypes=[("FreeCut 项目", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            self.project = Project.load(path)
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))
            return
        self._project_path = path
        self.playhead = 0
        self.invalidate()
        self.refresh_bin()
        self.timeline.redraw()
        self.set_status(f"已打开 {os.path.basename(path)}")
        self._check_missing_media()

    def _check_missing_media(self) -> None:
        """Offline media is the most common way a project 'breaks'."""
        from . import relink

        items = relink.missing_media(self.project)
        if not items:
            return
        names = "\n".join(f"  · {i.name}" for i in items[:8])
        more = "" if len(items) <= 8 else f"\n  … 还有 {len(items) - 8} 个"
        if not messagebox.askyesno(
            "素材缺失",
            f"有 {len(items)} 个素材找不到：\n{names}{more}\n\n"
            "要选择一个目录来搜索同名文件并重新链接吗？",
        ):
            self.set_status(f"{len(items)} 个素材缺失（可在命令行用 freecut doctor 处理）")
            return
        folder = filedialog.askdirectory(title="选择素材所在目录")
        if not folder:
            return
        changed = relink.auto_relink(self.project, [folder])
        if changed:
            self.invalidate()
            self.timeline.redraw()
            self.refresh_bin()
            self.set_status(f"已重新链接 {changed} 个素材")
            if messagebox.askyesno("保存", "重链已生效，要立即保存工程吗？"):
                self.save_project()
        else:
            left = len(relink.missing_media(self.project))
            messagebox.showinfo("重链", f"未找到唯一匹配，仍有 {left} 个素材缺失")

    def save_project(self) -> None:
        path = self._project_path or filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("FreeCut 项目", "*.json")]
        )
        if not path:
            return
        self.project.save(path)
        self._project_path = path
        self.set_status(f"已保存 {os.path.basename(path)}")

    def import_jianying(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 draft_content.json",
            filetypes=[("剪映草稿", "draft_content.json"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            self.project = load_draft(path)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self.playhead = 0
        self.invalidate()
        self.refresh_bin()
        self.timeline.redraw()
        self.set_status("剪映草稿已导入")

    # -- media bin ----------------------------------------------------------
    # -- 左栏：类别 + 内容页 ------------------------------------------------
    def _build_left(self, parent) -> None:
        """左栏结构照剪映桌面版：竖排类别栏 + 右侧内容页（叠放，选哪类抬起哪页）。"""
        head = ttk.Frame(parent)
        head.pack(fill="x")
        self._left_title = ttk.Label(head, text="素材", style="Header.TLabel")
        self._left_title.pack(fill="x")

        row = ttk.Frame(parent)
        row.pack(fill="both", expand=True)

        rail = ttk.Frame(row, width=theme.px(theme.RAIL_W, self._dpi_scale))
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        self._rail_buttons = {}
        for name in theme.CATEGORIES:
            btn = ttk.Button(
                rail, width=5, style="Rail.TButton",
                text=f"{theme.CATEGORY_GLYPH.get(name, '·')}\n{name}",
                command=lambda n=name: self.show_category(n),
            )
            btn.pack(fill="x", padx=3, pady=2)
            self._rail_buttons[name] = btn

        host = ttk.Frame(row)
        host.pack(side="left", fill="both", expand=True)
        host.grid_rowconfigure(0, weight=1)
        host.grid_columnconfigure(0, weight=1)
        self._page_host = host

        self._pages = {}
        for name, build in (
            ("素材", self._page_media),
            ("音频", self._page_audio),
            ("文本", self._page_text),
            ("贴纸", self._page_sticker),
            ("特效", self._page_effects),
            ("转场", self._page_transitions),
            ("字幕", self._page_subtitle),
        ):
            page = ttk.Frame(host, padding=(theme.GAP, theme.GAP // 2))
            page.grid(row=0, column=0, sticky="nsew")
            build(page)
            self._pages[name] = page
        self.show_category("素材")

    def show_category(self, name: str) -> None:
        """切换左栏类别：抬起对应页，并把类别按钮标成选中态。"""
        page = getattr(self, "_pages", {}).get(name)
        if page is None:
            return
        page.tkraise()
        self._left_title.configure(text=name)
        for cat, btn in self._rail_buttons.items():
            btn.configure(style="RailOn.TButton" if cat == name else "Rail.TButton")

    def _page_media(self, page) -> None:
        self.bin = tk.Listbox(page, bg=theme.BG_ELEV, fg=theme.FG,
                              selectbackground=theme.ACCENT_DIM, selectforeground=theme.FG,
                              relief="flat", highlightthickness=0, width=26,
                              activestyle="none")
        self.bin.pack(fill="both", expand=True)
        self.bin.bind("<Double-Button-1>", lambda e: self.add_bin_item_to_timeline())
        ttk.Button(page, text="导入媒体", command=self.import_media).pack(fill="x", pady=(6, 2))
        ttk.Button(page, text="添加到时间线",
                   command=self.add_bin_item_to_timeline).pack(fill="x", pady=2)

    def _page_audio(self, page) -> None:
        ttk.Label(page, text="项目里的音频", style="TLabel").pack(anchor="w", pady=(0, 4))
        self.audio_list = tk.Listbox(page, bg=theme.BG_ELEV, fg=theme.FG, height=8,
                                     relief="flat", highlightthickness=0, width=26,
                                     selectbackground=theme.ACCENT_DIM, activestyle="none")
        self.audio_list.pack(fill="both", expand=True)
        ttk.Label(page, text="音量 / 淡入淡出 / 变声在右栏「声音」页签。",
                  style="TLabel", foreground=theme.FG_DIM, wraplength=210).pack(anchor="w", pady=6)
        ttk.Button(page, text="刷新列表", command=self.refresh_audio_panel).pack(fill="x")

    def _page_text(self, page) -> None:
        from . import textstyles

        ttk.Label(page, text=f"花字 {len(textstyles.names())} 套（双击套用）",
                  style="TLabel").pack(anchor="w", pady=(0, 4))
        self.text_list = tk.Listbox(page, bg=theme.BG_ELEV, fg=theme.FG, height=12,
                                    relief="flat", highlightthickness=0, width=26,
                                    selectbackground=theme.ACCENT_DIM, activestyle="none")
        for name in textstyles.names():
            self.text_list.insert("end", name)
        self.text_list.pack(fill="both", expand=True)
        self.text_list.bind("<Double-Button-1>", lambda e: self.insert_text_from_list())
        ttk.Button(page, text="插入到播放头",
                   command=self.insert_text_from_list).pack(fill="x", pady=(6, 2))

    def _page_sticker(self, page) -> None:
        ttk.Label(page, text="内置贴纸：0 个", style="TLabel").pack(anchor="w")
        ttk.Label(page,
                  text="剪映的贴纸是云端素材包，FreeCut 不自带也不盗链。\n"
                       "需要装饰时用「文本」页的花字或「特效」页的光效代替。",
                  style="TLabel", foreground=theme.FG_DIM, wraplength=210,
                  justify="left").pack(anchor="w", pady=8)

    def _page_effects(self, page) -> None:
        from . import effects

        ttk.Label(page, text=f"程序化特效 {len(effects.available())} 种（双击加到选中片段）",
                  style="TLabel", wraplength=210).pack(anchor="w", pady=(0, 4))
        self.effect_list = tk.Listbox(page, bg=theme.BG_ELEV, fg=theme.FG, height=12,
                                      relief="flat", highlightthickness=0, width=26,
                                      selectbackground=theme.ACCENT_DIM, activestyle="none")
        self._effect_kinds = list(effects.available())
        for kind in self._effect_kinds:
            self.effect_list.insert("end", effects.label(kind))
        self.effect_list.pack(fill="both", expand=True)
        self.effect_list.bind("<Double-Button-1>", lambda e: self.apply_effect_from_list())
        ttk.Button(page, text="加到选中片段",
                   command=self.apply_effect_from_list).pack(fill="x", pady=(6, 2))

    def _page_transitions(self, page) -> None:
        from .render import TRANSITION_KINDS

        engines = [k for k in ENGINE_LABELS if k in set(TRANSITION_KINDS.values())]
        ttk.Label(page, text=f"转场引擎 {len(engines)} 种（双击加在片段开头）",
                  style="TLabel", wraplength=210).pack(anchor="w", pady=(0, 4))
        self.transition_list = tk.Listbox(page, bg=theme.BG_ELEV, fg=theme.FG, height=12,
                                          relief="flat", highlightthickness=0, width=26,
                                          selectbackground=theme.ACCENT_DIM, activestyle="none")
        self._transition_engines = engines
        for kind in engines:
            self.transition_list.insert("end", ENGINE_LABELS[kind])
        self.transition_list.pack(fill="both", expand=True)
        self.transition_list.bind("<Double-Button-1>",
                                  lambda e: self.apply_transition_from_list())
        ttk.Button(page, text="加到选中片段",
                   command=self.apply_transition_from_list).pack(fill="x", pady=(6, 2))

    def _page_subtitle(self, page) -> None:
        ttk.Label(page, text="字幕", style="TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Button(page, text="导入字幕 (SRT/VTT)",
                   command=self.import_subtitle).pack(fill="x", pady=2)
        ttk.Button(page, text="导出字幕 (SRT)",
                   command=self.export_subtitle).pack(fill="x", pady=2)
        ttk.Button(page, text="自动字幕（离线语音识别）",
                   command=self.auto_subtitle).pack(fill="x", pady=2)
        ttk.Label(page, text="识别用本地 Whisper，不上传音频。",
                  style="TLabel", foreground=theme.FG_DIM, wraplength=210).pack(anchor="w", pady=6)

    # -- 左栏页面的动作 ------------------------------------------------------
    def _selected_or_hint(self, title: str):
        seg = getattr(self, "selected", None)
        if seg is None:
            messagebox.showinfo(title, "先在时间线上点选一个片段。")
            return None
        return seg

    def apply_effect_from_list(self) -> None:
        from . import effects

        sel = self.effect_list.curselection() if hasattr(self, "effect_list") else ()
        if not sel:
            return
        kind = self._effect_kinds[sel[0]]
        seg = self._selected_or_hint("加特效")
        if seg is None:
            return
        self.push_undo("加特效")
        builder.add_effect(self.project, seg, kind)
        self.invalidate()
        self.set_status(f"已加特效：{effects.label(kind)}")

    def apply_transition_from_list(self) -> None:
        sel = self.transition_list.curselection() if hasattr(self, "transition_list") else ()
        if not sel:
            return
        kind = self._transition_engines[sel[0]]
        seg = self._selected_or_hint("加转场")
        if seg is None:
            return
        self.push_undo("加转场")
        builder.add_transition(self.project, seg, kind=kind)
        self.invalidate()
        self.set_status(f"已加转场：{ENGINE_LABELS[kind]}")

    def insert_text_from_list(self) -> None:
        from . import textstyles

        sel = self.text_list.curselection() if hasattr(self, "text_list") else ()
        preset = textstyles.names()[sel[0]] if sel else "白色标题"
        self.push_undo("插入文本")
        seg = builder.add_text(self.project, preset, start=self.playhead,
                               duration=3_000_000, size=12.0)
        mat = self.project.material(seg.material_id)
        textstyles.apply(mat, preset)
        self.select(seg)
        self.invalidate()
        self.set_status(f"已插入花字：{preset}")

    def refresh_audio_panel(self) -> None:
        if not hasattr(self, "audio_list"):
            return
        self.audio_list.delete(0, "end")
        for track in self.project.tracks:
            if track.type != "audio":
                continue
            for seg in track.segments:
                name = getattr(seg.source, "stem", None) or getattr(seg, "name", "") or seg.id[:6]
                self.audio_list.insert(
                    "end", f"{name}  音量 {seg.volume:.0%}")
        if self.audio_list.size() == 0:
            self.audio_list.insert("end", "（还没有音频片段）")

    def _build_welcome(self, center) -> None:
        """A start panel shown while the project is empty.

        An empty timeline with a full menu bar is a cold first run; these four
        buttons cover what almost everyone does first.
        """
        self.welcome = tk.Frame(center, bg="#14141a", highlightthickness=0)
        box = ttk.Frame(self.welcome, padding=24)
        box.pack(expand=True)
        ttk.Label(box, text="FreeCut", font=("Microsoft YaHei", 20, "bold"),
                  foreground="#e8e8f0").pack(anchor="center")
        ttk.Label(box, text="免费、可编程的视频剪辑（剪映 / PR 双向互通）",
                  foreground="#9aa0ae").pack(anchor="center", pady=(2, 18))
        for text, command in (
            (" 打开示例工程 ", self.open_sample_project),
            (" 导入媒体开始剪 ", self.import_media),
            (" 文案 → 成片（配音+字幕） ", self.from_script),
            (" 导入剪映草稿 ", self.import_jianying),
        ):
            ttk.Button(box, text=text, width=30, command=command).pack(pady=3)
        ttk.Label(box, text="提示：空格播放 · Ctrl+Z 撤销 · 工具 → 导出报告 看会丢什么",
                  foreground="#6f7684").pack(anchor="center", pady=(16, 0))
        self._update_welcome()

    def _update_welcome(self) -> None:
        if not hasattr(self, "welcome"):
            return
        empty = not any(track.segments for track in self.project.tracks)
        if empty:
            # 只盖住预览区：传输条和时间线仍然可见（空工程里也能操作）
            self.welcome.place(in_=self.preview, relx=0, rely=0, relwidth=1, relheight=1)
            self.welcome.lift()
        else:
            self.welcome.place_forget()

    def open_sample_project(self) -> None:
        """Load the bundled showcase, generating it when missing."""
        from pathlib import Path as _P

        root = _P(__file__).resolve().parents[1]
        sample = root / "out" / "showcase.json"
        if not sample.is_file():
            try:
                from . import showcase

                import tempfile

                stats = showcase.build_showcase(str(_P(tempfile.gettempdir()) / "freecut-showcase"))
                stats["project"].save(str(sample))
            except Exception as exc:
                messagebox.showerror("示例工程", f"生成示例失败: {exc}")
                return
        try:
            self.open_project(str(sample))
        except Exception as exc:
            messagebox.showerror("示例工程", f"打开失败: {exc}")
            return
        self.set_status("已打开示例工程（38 秒 / 11 个环节）")
        self.show_tour()

    def refresh_bin(self) -> None:
        self._update_welcome()
        self.bin.delete(0, "end")
        for mat in self.project.materials.values():
            if mat.type in ("video", "image", "audio", "text"):
                label = mat.get("material_name") or mat.get("name") or mat.get("content") or mat.type
                self.bin.insert("end", f"[{mat.type}] {str(label)[:28]}")
        self._bin_ids = [
            m.id for m in self.project.materials.values() if m.type in ("video", "image", "audio", "text")
        ]

    def import_media(self) -> None:
        paths = filedialog.askopenfilenames(
            filetypes=[("媒体", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mp3 *.wav *.m4a *.aac *.flac *.png *.jpg *.jpeg *.bmp *.webp"), ("所有文件", "*.*")]
        )
        if not paths:
            return
        self.push_undo()
        added = 0
        for path in paths:
            try:
                info = probe(path)
            except Exception as exc:
                messagebox.showwarning("无法读取", f"{os.path.basename(path)}\n{exc}")
                continue
            if info.has_video and info.duration_us <= 0:  # still image
                mat = ImageMaterial(path=os.path.abspath(path), material_name=os.path.basename(path))
            elif info.has_video:
                mat = VideoMaterial(path=os.path.abspath(path), duration=info.duration_us,
                                    width=info.width, height=info.height,
                                    has_audio=info.has_audio,
                                    material_name=os.path.basename(path))
            else:
                mat = AudioMaterial(path=os.path.abspath(path), duration=info.duration_us,
                                    name=os.path.basename(path))
            self.project.add_material(mat)
            added += 1
        self.refresh_bin()
        self.set_status(f"导入 {added} 个媒体")

    def import_subtitle(self) -> None:
        from . import subtitles

        path = filedialog.askopenfilename(title="选择字幕文件",
                                          filetypes=[("SRT 字幕", "*.srt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            subs = subtitles.parse_srt(path)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        if not subs:
            messagebox.showinfo("字幕", "没有解析到字幕")
            return
        self.push_undo()
        subtitles.add_subtitles(self.project, subs)
        self.invalidate()
        self.timeline.redraw()
        self.refresh_bin()
        self.set_status(f"导入 {len(subs)} 条字幕")

    def export_subtitle(self) -> None:
        from . import subtitles

        path = filedialog.asksaveasfilename(defaultextension=".srt",
                                            filetypes=[("SRT 字幕", "*.srt")])
        if not path:
            return
        subs = subtitles.extract_subtitles(self.project)
        subtitles.write_srt(subs, path)
        self.set_status(f"导出 {len(subs)} 条字幕")

    ASR_MODELS = (
        ("base", "base（默认，离线可用）"),
        ("small", "small（更准，首次需下载）"),
        ("tiny", "tiny（最快，最不准）"),
    )

    def _ask_asr_model(self) -> Optional[str]:
        """Ask which speech model to use; returns None when the user cancels."""
        win = tk.Toplevel(self)
        win.title("自动字幕：选择模型")
        win.transient(self)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="识别模型").pack(anchor="w")
        var = tk.StringVar(value="base")
        try:
            from . import asr as _asr

            present = {name: _asr.model_present(name) for name, _ in self.ASR_MODELS}
        except Exception:
            present = {}
        for value, text in self.ASR_MODELS:
            label = text if present.get(value, True) else text + "（未下载）"
            ttk.Radiobutton(body, text=label, value=value, variable=var).pack(anchor="w")
        ttk.Label(body, text="实测：base 命中 74%、比 tiny 还快；small 命中 89%。",
                  foreground="#8a8f9c").pack(anchor="w", pady=(6, 8))
        chosen = {}

        def ok() -> None:
            chosen["value"] = var.get()
            win.destroy()

        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Button(row, text="开始识别", command=ok).pack(side="right")
        ttk.Button(row, text="取消", command=win.destroy).pack(side="right", padx=6)
        win.grab_set()
        self.wait_window(win)
        return chosen.get("value")

    def auto_subtitle(self) -> None:
        """Speech-to-text in a worker thread (a long clip can take minutes)."""
        import threading

        from . import asr as _asr

        ok, reason = _asr.backend_status()
        if not ok:
            messagebox.showinfo("自动字幕需要额外的语音识别后端", reason)
            return

        path = filedialog.askopenfilename(
            title="选择要识别的音频/视频",
            filetypes=[("媒体", "*.mp4 *.mov *.mkv *.wav *.mp3 *.m4a *.aac *.flac"),
                       ("所有文件", "*.*")],
        )
        if not path:
            return

        # 先问模型：默认 base（随工具分发、离线可用），small 更准但要先下载
        choice = self._ask_asr_model()
        if choice is None:
            return

        win = tk.Toplevel(self)
        win.title("自动字幕")
        win.geometry("380x110")
        win.configure(bg="#202027")
        win.transient(self)
        label = ttk.Label(win, text=f"正在识别语音…（{choice} 模型，CPU 推理，可能较慢）")
        label.pack(pady=14)

        result = {}

        def worker():
            try:
                from . import asr

                data = asr.transcribe(path, model=choice)
                result["subs"] = asr.segments_to_subtitles(data)
            except Exception as exc:  # surfaced on the UI thread
                result["error"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                win.after(400, poll)
                return
            win.destroy()
            if "error" in result:
                messagebox.showerror("识别失败", str(result["error"]))
                return
            subs = result.get("subs") or []
            if not subs:
                messagebox.showinfo("自动字幕", "没有识别到语音")
                return
            from . import subtitles

            self.push_undo()
            subtitles.add_subtitles(self.project, subs)
            self.invalidate()
            self.timeline.redraw()
            self.refresh_bin()
            self.set_status(f"自动字幕：{len(subs)} 条")

        win.after(400, poll)

    def add_bin_item_to_timeline(self) -> None:
        sel = self.bin.curselection()
        if not sel:
            return
        mat = self.project.material(self._bin_ids[sel[0]])
        if mat is None:
            return
        self.push_undo()
        ttype = "audio" if mat.type == "audio" else ("text" if mat.type == "text" else "video")
        track = builder.track_of(self.project, ttype, 0)
        start = builder.free_start(track, self.playhead)
        if mat.type == "audio":
            dur = int(mat.get("duration", 3_000_000) or 3_000_000)
        elif mat.type in ("image", "text"):
            dur = 5_000_000
        else:
            dur = int(mat.get("duration", 5_000_000) or 5_000_000)
        seg = Segment(
            material_id=mat.id,
            target_timerange=Timerange(start, dur),
            source_timerange=Timerange(0, dur),
        )
        track.segments.append(seg)
        self.invalidate()
        self.timeline.redraw()
        self.select(seg)

    def add_adjustment_layer(self) -> None:
        """Insert a 3 s adjustment layer at the playhead (colours everything below)."""
        self.push_undo()
        seg = builder.add_adjustment(self.project, start=self.playhead, duration=3_000_000)
        self.invalidate()
        self.timeline.redraw()
        self.select(seg)
        self.set_status("已添加调节图层：切到「调节」页签拖滑块，会影响它下面的所有画面")

    def add_text(self) -> None:
        self.push_undo()
        seg = builder.add_text(
            self.project, "双击编辑文字", start=self.playhead, duration=3_000_000,
            y=-0.7, size=12, color="#FFFFFF", stroke=0.03,
        )
        self.invalidate()
        self.timeline.redraw()
        self.refresh_bin()
        self.select(seg)

    # -- editing ------------------------------------------------------------
    def select(self, seg: Optional[Segment]) -> None:
        self.timeline.selected = seg
        self.props.rebuild(seg)
        self.timeline.redraw()

    def split_at_playhead(self) -> None:
        seg = self.timeline.selected
        if seg is None:
            return
        if not seg.target_timerange.contains(self.playhead):
            messagebox.showinfo("拆分", "播放头不在所选片段内")
            return
        self.push_undo()
        builder.split(self.project, seg, self.playhead)
        self.invalidate()
        self.timeline.redraw()

    def delete_selected(self) -> None:
        seg = self.timeline.selected
        if seg is None:
            return
        self.push_undo()
        for track in self.project.tracks:
            if seg in track.segments:
                track.segments.remove(seg)
                break
        self.select(None)
        self.invalidate()
        self.timeline.redraw()

    # -- playback -----------------------------------------------------------
    def set_playhead(self, us: int) -> None:
        if self.playing:
            self._stop_audio()          # 跳转后要重新起播
            self.playing = False
            self.play_btn.configure(text="▶ 播放")
        total = max(1, self.project.duration())
        self.playhead = max(0, min(us, total))
        self.time_lbl.configure(text=fmt_time(self.playhead))
        self.invalidate()
        self.timeline.redraw()

    def step(self, direction: int) -> None:
        fps = self.project.fps or 30.0
        self.set_playhead(self.playhead + int(direction * 1_000_000 / fps))

    def toggle_play(self) -> None:
        self.playing = not self.playing
        self.play_btn.configure(text="⏸ 暂停" if self.playing else "▶ 播放")
        if self.playing:
            import time as _time

            self._play_t0 = _time.time()
            self._play_from = self.playhead
            self._renderer_size = (0, 0)       # 自动模式：播放时降质
            self._start_audio()
            self._tick()
        else:
            self._stop_audio()
            self._renderer_size = (0, 0)       # 暂停后恢复全尺寸
            self._render_now()

    def _tick(self) -> None:
        """Advance the playhead by **wall clock**, so playback keeps real time.

        Rendering is slower than most project frame rates; ticking once per
        frame would make 30 fps material play at ~5 fps (measured: 0.25x).
        Here the playhead follows elapsed time and slow frames are simply
        dropped, which is what keeps picture in sync with the audio bed.
        """
        if not self.playing:
            return
        import time as _time

        fps = float(self.project.fps or 30.0)
        total = self.project.duration()
        elapsed = _time.time() - self._play_t0
        target = self._play_from + int(elapsed * 1e6)
        if target >= total:
            self.playing = False
            self.play_btn.configure(text="▶ 播放")
            self._stop_audio()
            self.set_playhead(total)
            return
        self.playhead = target
        self.time_lbl.configure(text=fmt_time(self.playhead))
        self._render_now()
        # 时间线整块重绘（含波形）很贵，播放时降到 ~10 Hz，否则拖垮帧率
        now = _time.time()
        if now - getattr(self, "_last_tl_redraw", 0.0) >= 0.10:
            self._last_tl_redraw = now
            self.timeline.redraw()
        # 下一次回调排到"下一个帧边界"，让播放节奏贴着墙钟
        next_us = self._play_from + int(((elapsed * fps) + 1.0) / fps * 1e6)
        delay_ms = max(1, int((next_us - target) / 1000))
        self._play_job = self.after(delay_ms, self._tick)

    def set_preview_quality(self, mode: str) -> None:
        self.preview_quality = mode
        self._renderer_size = (0, 0)          # 强制按新尺寸重建
        self.invalidate()
        self._render_now()
        self.set_status(f"预览画质：{mode}（{self._preview_size()[0]}x{self._preview_size()[1]}）")

    def toggle_mute(self) -> None:
        self.preview_muted = not self.preview_muted
        self.mute_btn.configure(text="🔇" if self.preview_muted else "🔊")
        if self.preview_muted:
            self._stop_audio()
        elif self.playing:
            self._start_audio()

    # -- audio preview ------------------------------------------------------
    def _mix_all(self) -> Optional[str]:
        """Mix the whole timeline to a cached WAV (rebuilt when the project changes)."""
        if not self._mix_dirty and self._mix_path and os.path.isfile(self._mix_path):
            return self._mix_path
        try:
            from .audio import collect_clips, silent_wav, write_audio_mix

            clips = [c for c in collect_clips(self.project) if not c.muted]
            total = self.project.duration()
            if total <= 0:
                return None
            if not clips:
                # 没有声音就不要建文件，播放时直接跳过
                self._mix_path = None
                self._mix_dirty = False
                return None
            path = os.path.join(tempfile.gettempdir(), "freecut_preview_mix.wav")
            if clips:
                ok = write_audio_mix(self.project, path, total, "192k")
            else:
                ok = False
            if not ok:
                silent_wav(path, total)
            self._mix_path = path
            self._mix_dirty = False
            return path
        except Exception:
            return None

    def _start_audio(self) -> None:
        """Play the mix from the playhead (winsound can only start at the file's head)."""
        mix = self._mix_all()
        if not mix:
            return
        if self.preview_muted:
            return
        try:
            import subprocess
            import winsound

            from .media import _creationflags, ffmpeg_path

            start_s = max(0.0, self.playhead / 1e6)
            remaining = max(0.05, (self.project.duration() - self.playhead) / 1e6)
            cut = os.path.join(tempfile.gettempdir(), "freecut_preview_cut.wav")
            subprocess.run(
                [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{start_s:.3f}", "-i", mix, "-t", f"{remaining:.3f}",
                 "-af", f"volume={max(0.0, min(1.0, float(self.preview_volume))):.3f}", cut],
                check=False, creationflags=_creationflags(),
            )
            if os.path.isfile(cut) and os.path.getsize(cut) > 44:
                winsound.PlaySound(cut, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

    def _stop_audio(self) -> None:
        try:
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    # -- preview ------------------------------------------------------------
    def invalidate(self, render: bool = True) -> None:
        self._render_dirty = True
        self._mix_dirty = True
        if self._renderer is not None:
            try:
                self._renderer.close()
            except Exception:
                pass
            self._renderer = None
        if render:
            self._schedule_render()

    def _schedule_render(self) -> None:
        if self._render_job is not None:
            try:
                self.after_cancel(self._render_job)
            except Exception:
                pass
        self._render_job = self.after(40, self.refresh_preview)

    def refresh_preview(self) -> None:
        self._render_job = None
        try:
            self._render_now()
        except Exception:
            self.set_status("预览渲染失败: " + traceback.format_exc(limit=1).splitlines()[-1])

    def _preview_size(self) -> tuple:
        """Render size for the preview; play-time uses a cheaper scale."""
        mode = self.preview_quality
        scale = 1.0
        if mode == "半":
            scale = 0.5
        elif mode == "三分之一":
            scale = 1.0 / 3.0
        elif mode == "四分之一":
            scale = 0.25
        elif mode == "自动" and self.playing:
            scale = 1.0 / 3.0                  # 播放时降到 1/3，暂停回全尺寸
        w = max(64, int(self.project.canvas.width * scale) // 2 * 2)
        h = max(64, int(self.project.canvas.height * scale) // 2 * 2)
        return w, h

    def _render_now(self) -> None:
        from PIL import Image, ImageTk

        want = self._preview_size()
        if self._renderer is None or self._renderer_size != want:
            if self._renderer is not None:
                try:
                    self._renderer.close()
                except Exception:
                    pass
            self._renderer = Renderer(self.project, want[0], want[1], self.project.fps)
            self._renderer_size = want
        r = self._renderer
        idx = min(max(0, int(round(self.playhead / 1e6 * r.fps))), r.frame_count - 1)
        frame = r.render_frame(idx)
        h, w = frame.shape[:2]
        cw = max(160, self.preview.winfo_width())
        ch = max(120, self.preview.winfo_height())
        scale = min(cw / w, ch / h, 1.0)
        img = Image.fromarray(frame)
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
        self._photo = ImageTk.PhotoImage(img)
        self.preview.delete("all")
        self.preview.create_image(cw // 2, ch // 2, image=self._photo)
        self.frames_shown += 1
        self._render_dirty = False

    # -- export -------------------------------------------------------------
    def export_flattened_draft(self) -> None:
        """Render the timeline, then hand JianyingPro a single clip.

        Everything FreeCut renders itself (grading values, effects, chroma key)
        cannot be expressed in a draft, so this trades editability for a
        picture that matches exactly what we preview.
        """
        from tkinter import simpledialog

        from .export import PRESETS, export
        from .jianying_export import flatten_project, save_draft

        root = filedialog.askdirectory(title="选择剪映草稿根目录")
        if not root:
            return
        name = simpledialog.askstring("草稿名", "草稿名称",
                                      initialvalue=(self.project.name or "FreeCut") + " 展平",
                                      parent=self)
        if not name:
            return
        flat_dir = os.path.join(root, "_freecut_flatten")
        os.makedirs(flat_dir, exist_ok=True)
        mp4 = os.path.join(flat_dir, name + ".mp4")
        self.set_status("展平导出：正在渲染整条时间线…")
        try:
            export(self.project, mp4, PRESETS["720p30"], jobs=4)
            mini = flatten_project(self.project, mp4)
            draft_dir = save_draft(mini, root, name=name)
        except Exception as exc:
            messagebox.showerror("展平导出失败", str(exc))
            return
        self.set_status("展平导出完成（剪映里是单个片段，不可再拆）")
        messagebox.showinfo(
            "完成",
            f"已写出展平草稿:\n{draft_dir}\n\n成片在:\n{mp4}\n\n"
            "剪映里的画面与 FreeCut 预览一致，但整条时间线合成一个片段。")

    def show_export_report(self) -> None:
        """Show what would survive a draft export, without exporting.

        The same report is written next to the draft on export; this makes it
        visible *before* committing to it.
        """
        from tkinter import filedialog as _fd

        from . import draftreport

        try:
            draft = draftreport.analyze(self.project)
            xml = draftreport.analyze_xml(self.project)
        except Exception as exc:
            messagebox.showerror("导出报告失败", str(exc))
            return

        win = tk.Toplevel(self)
        win.title("导出报告")
        win.geometry("820x560")
        win.transient(self)
        body = ttk.Frame(win, padding=8)
        body.pack(fill="both", expand=True)

        row = ttk.Frame(body)
        row.pack(side="bottom", fill="x", pady=(8, 0))
        tabs = ttk.Notebook(body)
        tabs.pack(side="top", fill="both", expand=True)
        for label, text in (("剪映草稿", draft.as_text()), ("Premiere / FCP7 XML", xml.as_text())):
            frame = ttk.Frame(tabs, padding=4)
            box = tk.Text(frame, wrap="word", bg="#16161c", fg="#dcdce4",
                          insertbackground="#dcdce4", relief="flat",
                          font=("Microsoft YaHei", 9), padx=8, pady=6)
            scroll = ttk.Scrollbar(frame, orient="vertical", command=box.yview)
            box.configure(yscrollcommand=scroll.set)
            box.insert("1.0", text)
            box.configure(state="disabled")
            box.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
            tabs.add(frame, text=label)

        def save() -> None:
            path = _fd.asksaveasfilename(
                title="保存导出报告", defaultextension=".txt",
                initialfile="导出报告.txt",
                filetypes=[("文本", "*.txt"), ("所有文件", "*.*")])
            if not path:
                return
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(draft.as_text() + "\n\n" + "=" * 60 + "\n\n" + xml.as_text() + "\n")
            self.set_status(f"导出报告已保存: {path}")

        ttk.Button(row, text="保存两份报告…", command=save).pack(side="left")
        ttk.Label(row, text=f"剪映：{sum(i.count for i in draft.losses)} 处会不同  ·  "
                            f"XML：{sum(i.count for i in xml.losses)} 处会不同",
                  foreground="#c8a24a").pack(side="left", padx=12)
        ttk.Button(row, text="关闭", command=win.destroy).pack(side="right")

    # -- self-check / about --------------------------------------------------
    def _repo_root(self) -> "Path":
        from pathlib import Path as _P

        return _P(__file__).resolve().parents[2]

    def run_selfcheck_dialog(self) -> None:
        """Run ``verify_all.py`` in the background and stream its output.

        The worker only appends to a list; everything that touches Tk happens
        in ``after`` callbacks on the main thread (Tk is not thread-safe).
        """
        import subprocess
        import threading

        # 允许替换脚本，便于测试（不真跑四分钟的自检）
        script = getattr(self, "_selfcheck_script", None) or (self._repo_root() / "verify_all.py")
        win = tk.Toplevel(self)
        win.title("运行自检")
        win.geometry("860x520")
        win.transient(self)

        body = ttk.Frame(win, padding=8)
        body.pack(fill="both", expand=True)

        # 步骤清单：每完成一步就在这里加一行，比盯着一屏日志直观
        steps_frame = ttk.Frame(body)
        steps_frame.pack(side="top", fill="x", pady=(0, 6))
        steps_title = ttk.Label(steps_frame, text="步骤", font=("Microsoft YaHei", 9, "bold"))
        steps_title.pack(anchor="w")
        steps_box = tk.Text(steps_frame, height=6, wrap="none", bg="#16161d", fg="#d8d8e0",
                            relief="flat", font=("Microsoft YaHei", 9), padx=6, pady=2)
        steps_box.pack(fill="x")
        steps_box.configure(state="disabled")

        row = ttk.Frame(body)
        row.pack(side="bottom", fill="x", pady=(8, 0))
        status = ttk.Label(row, text="准备中…")
        status.pack(side="left")

        box = tk.Text(body, wrap="none", bg="#12121a", fg="#d8d8e0", relief="flat",
                      font=("Consolas", 9), padx=6, pady=4)
        scroll = ttk.Scrollbar(body, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        box.pack(side="top", fill="both", expand=True)

        if not script.is_file():
            box.insert("1.0", f"找不到 {script}\n\n（打包版里没有 verify_all.py，"
                              "请在源码目录运行 `python verify_all.py`）")
            box.configure(state="disabled")
            status.configure(text="无法运行")
            ttk.Button(row, text="关闭", command=win.destroy).pack(side="right")
            return

        lines: list = []
        state = {"proc": None, "done": False, "code": None}

        def reader(proc) -> None:
            try:
                for raw in proc.stdout:
                    lines.append(raw.rstrip("\n"))
            except Exception as exc:
                lines.append(f"（读取输出失败: {exc}）")
            state["code"] = proc.wait()
            state["done"] = True

        seen: list = []

        def add_step(line: str) -> None:
            from .selfcheck import parse_step_line

            parsed = parse_step_line(line)
            if parsed is None:
                return
            ok, name, seconds, summary = parsed
            seen.append(ok)
            mark = "✅" if ok else "❌"
            steps_box.configure(state="normal")
            steps_box.insert("end", f"{mark} {name:<12} {seconds:>5}s   {summary}\n")
            steps_box.configure(state="disabled")
            steps_box.see("end")

        def poll() -> None:
            if lines:
                chunk = "\n".join(lines) + "\n"
                for raw in list(lines):
                    add_step(raw)
                lines.clear()
                box.insert("end", chunk)
                box.see("end")
            if state["done"]:
                code = state["code"]
                passed = sum(1 for ok in seen if ok)
                total = len(seen)
                tail = f"（{passed}/{total} 步通过）" if total else ""
                # 退出码为 0 但清单里有失败步骤时，仍然算失败——
                # 不能因为子进程的退出码漏掉界面自己看到的事实
                clean = code == 0 and passed == total
                status.configure(text=(f"自检通过 ✅{tail}" if clean
                                       else f"自检失败 ❌{tail}（退出码 {code}）"))
                cancel.configure(text="关闭", command=lambda: (proc.kill() if proc.poll() is None else None, win.destroy()))
                return
            done_n = len(seen)
            status.configure(text=f"运行中…（已完成 {done_n} 步，约 4 分钟）" if done_n
                             else "运行中…（约 4 分钟）")
            win.after(300, poll)

        def do_cancel() -> None:
            proc = state["proc"]
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
                status.configure(text="已取消")
            win.destroy()

        try:
            proc = subprocess.Popen(
                # -u：不加的话子进程输出是块缓冲，界面会一直空白到结束
                [sys.executable, "-u", str(script), "--quick"],
                cwd=str(self._repo_root()), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=0x08000000 if os.name == "nt" else 0)
        except Exception as exc:
            box.insert("1.0", f"启动失败: {exc}")
            box.configure(state="disabled")
            ttk.Button(row, text="关闭", command=win.destroy).pack(side="right")
            return

        state["proc"] = proc
        cancel = ttk.Button(row, text="取消", command=do_cancel)
        cancel.pack(side="right")
        threading.Thread(target=reader, args=(proc,), daemon=True).start()
        win.after(300, poll)
        win.protocol("WM_DELETE_WINDOW", do_cancel)

    def show_about(self) -> None:
        from . import __version__ as version

        pkg = self._repo_root() / "FreeCut" / "freecut"
        modules = len(list(pkg.glob("*.py"))) if pkg.is_dir() else 0
        lines = 0
        if pkg.is_dir():
            for f in pkg.glob("*.py"):
                lines += sum(1 for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()
                             if ln.strip())
        try:
            from .media import ffmpeg_path

            engine = ffmpeg_path()
        except Exception:
            engine = "（未找到 ffmpeg）"
        messagebox.showinfo(
            "关于 FreeCut",
            f"FreeCut {version}\n\n"
            f"模块 {modules} 个 / 非空 {lines:,} 行\n"
            f"工程: {os.path.basename(self._project_path) if self._project_path else '（未保存）'}\n"
            f"ffmpeg: {engine}\n\n"
            "工具 → 导出报告：看这个工程导出到剪映/PR 会丢什么\n"
            "帮助 → 运行自检：验证整个交付物")

    def export_jianying_draft(self) -> None:
        from tkinter import simpledialog

        from .jianying_export import save_draft

        root = filedialog.askdirectory(
            title="选择剪映草稿根目录（例如 E:\\剪映-草稿\\JianyingPro Drafts）"
        )
        if not root:
            return
        name = simpledialog.askstring("草稿名", "草稿名称", initialvalue=self.project.name, parent=self)
        if not name:
            return
        try:
            notes: list = []
            draft_dir = save_draft(self.project, root, name=name, warnings=notes)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        try:
            from . import draftreport

            report = draftreport.analyze(self.project, draft_dir=draft_dir, notes=notes)
            report.write(os.path.join(draft_dir, "freecut_export_report.txt"))
            self.set_status(f"草稿已导出，附带导出报告（{len(report.losses)} 处会不同）")
        except Exception:
            report = None
        self._last_export_report = report
        extra = ""
        if getattr(self, "_last_export_report", None) is not None:
            rep = self._last_export_report
            lost = sum(i.count for i in rep.losses)
            extra = ("\n\n导出报告："
                     + ("全部内容剪映都能读。" if not lost
                        else f"{lost} 处会不同或看不到："
                             + "、".join(sorted({i.feature for i in rep.losses}))))
        messagebox.showinfo(
            "完成",
            f"已写出剪映草稿:\n{draft_dir}\n\n"
            "若要让它在剪映首页出现，用命令行加 --register：\n"
            f'python -m freecut export-draft "{self._project_path or "project.json"}" '
            f'--root "{root}" --name "{name}" --register',
        )
        self.set_status("已导出剪映草稿")

    def do_export(self) -> None:
        opts = self._ask_export_options()
        if not opts:
            return
        path, preset, extra = opts
        win = tk.Toplevel(self)
        win.title("导出中")
        win.geometry("380x110")
        win.configure(bg="#202027")
        label = ttk.Label(win, text="准备中…")
        label.pack(pady=10)
        bar = ttk.Progressbar(win, maximum=1.0, length=340)
        bar.pack(pady=6)

        def progress(frac: float, msg: str) -> None:
            bar["value"] = frac
            label.configure(text=msg)
            win.update_idletasks()

        try:
            if extra:
                from .export import PRESETS as _P, export_many

                stem, ext = os.path.splitext(path)
                export_many(self.project,
                            [(path, preset), (f"{stem}_{extra}{ext or '.mp4'}", _P[extra])],
                            progress)
            else:
                export_project(self.project, path, preset, progress)
            win.destroy()
            report = ""
            try:
                from .verify import verify

                rep = verify(path)
                report = "\n\n" + ("校验通过 ✅" if rep.ok else "校验发现问题 ⚠")
                if rep.problems:
                    report += "\n" + "\n".join(rep.problems[:4])
            except Exception:
                report = ""
            messagebox.showinfo("完成", f"已导出:\n{path}{report}")
            self.set_status("导出完成" + ("（校验通过）" if "✅" in report else ""))
        except Exception as exc:
            win.destroy()
            messagebox.showerror("导出失败", str(exc))

    def _ask_export_options(self):
        """Small dialog: resolution preset + quality tier + channels."""
        from .export import PRESETS, QUALITY_TIERS, ExportPreset, with_quality

        top = tk.Toplevel(self)
        top.title("导出设置")
        top.configure(bg="#202027")
        top.transient(self)
        top.resizable(False, False)
        body = ttk.Frame(top, padding=12)
        body.pack(fill="both", expand=True)

        preset_names = list(PRESETS)
        default_name = next((n for n, p_ in PRESETS.items()
                             if (p_.width, p_.height) == (self.project.canvas.width,
                                                          self.project.canvas.height)),
                            "1080p30")
        res_var = tk.StringVar(value=default_name)
        ttk.Label(body, text="分辨率").pack(anchor="w")
        ttk.Combobox(body, textvariable=res_var, values=preset_names,
                     state="readonly", width=26).pack(fill="x", pady=(2, 8))

        quality_var = tk.StringVar(value="推荐")
        ttk.Label(body, text="画质").pack(anchor="w")
        ttk.Combobox(body, textvariable=quality_var, values=list(QUALITY_TIERS),
                     state="readonly", width=26).pack(fill="x", pady=(2, 8))

        extra_var = tk.StringVar(value="不额外输出")
        ttk.Label(body, text="附加输出（同一次渲染）").pack(anchor="w")
        ttk.Combobox(body, textvariable=extra_var,
                     values=["不额外输出", "720p30", "1080p30", "1080p60", "vertical1080"],
                     state="readonly", width=26).pack(fill="x", pady=(2, 8))

        ch_var = tk.StringVar(value="立体声")
        ttk.Label(body, text="声道").pack(anchor="w")
        ttk.Combobox(body, textvariable=ch_var, values=["立体声", "单声道"],
                     state="readonly", width=26).pack(fill="x", pady=(2, 8))

        result = {}

        def confirm():
            name = res_var.get()
            base = PRESETS.get(name) or ExportPreset()
            preset = with_quality(base, quality_var.get())
            preset.audio_channels = 1 if ch_var.get() == "单声道" else 2
            preset.width = self.project.canvas.width or preset.width
            preset.height = self.project.canvas.height or preset.height
            preset.fps = self.project.fps or preset.fps
            path = filedialog.asksaveasfilename(
                defaultextension=".mp4", filetypes=[("MP4", "*.mp4")])
            if not path:
                return
            extra = extra_var.get()
            result["value"] = (path, preset, None if extra == "不额外输出" else extra)
            top.destroy()

        row = ttk.Frame(body)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="开始导出", command=confirm).pack(side="left")
        ttk.Button(row, text="取消", command=top.destroy).pack(side="right")

        top.wait_window()
        return result.get("value")

    # -- misc ---------------------------------------------------------------
    def _apply_sashes(self, left_w: int, right_w: int) -> None:
        """Put the two sashes where the saved layout says they belong."""
        try:
            total = self.panes.winfo_width()
            if total <= 1:
                self.after(60, lambda: self._apply_sashes(left_w, right_w))
                return
            self.panes.sashpos(0, max(140, min(int(left_w), total - 300)))
            self.panes.sashpos(1, max(240, total - max(220, int(right_w))))
        except Exception:
            pass

    def save_ui_state(self) -> None:
        """Persist the sash positions so the next launch looks the same."""
        from . import uistate

        state = dict(getattr(self, "_ui_state", {}) or {})
        try:
            left = int(self.panes.sashpos(0))
            right = int(self.panes.winfo_width() - self.panes.sashpos(1))
            state["pane_widths"] = [left, 0, max(220, right)]
        except Exception:
            pass
        uistate.save(state)


    def nudge_sash(self, index: int, delta: int) -> None:
        """Move one sash by ``delta`` pixels (ReaCut 的"键盘微调"）。"""
        try:
            total = self.panes.winfo_width()
            pos = int(self.panes.sashpos(index)) + int(delta)
            if index == 0:
                pos = max(140, min(pos, total - 300))
            else:
                pos = max(300, min(pos, total - 220))
            self.panes.sashpos(index, pos)
        except Exception:
            pass

    def reset_panes(self) -> None:
        """Restore the default three-column widths (and forget the saved ones)."""
        from . import uistate

        left_w, _mid, right_w = uistate.DEFAULT_WIDTHS
        self._apply_sashes(left_w, right_w)
        self.pane_defaults = (left_w, right_w)
        self.set_status("已恢复默认面板布局")

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    # -- guided tour ---------------------------------------------------------
    DEFAULT_TOUR = (
        "按空格播放：有声音，播放时会自动降画质保流畅",
        "点时间线上的片段 → 右侧五个页签（变换 / 调节 / 曲线·轮 / 效果 / 声音）",
        "工具 → 导出报告：看这个工程导出到剪映 / PR 会丢什么",
        "帮助 → 运行自检：在界面里验完整条交付链路",
    )

    def show_tour(self, hints: Optional[list] = None, seconds: float = 5.0) -> None:
        """Cycle short hints in the status bar (non-blocking, no dialogs)."""
        seq = list(hints or self.DEFAULT_TOUR)
        if not seq:
            return
        self._tour_job = getattr(self, "_tour_job", None)
        if self._tour_job is not None:
            try:
                self.after_cancel(self._tour_job)
            except Exception:
                pass
            self._tour_job = None

        def step(i: int) -> None:
            if i >= len(seq):
                self._tour_job = None
                return
            self.set_status(f"导览 {i + 1}/{len(seq)}：{seq[i]}")
            self._tour_job = self.after(int(seconds * 1000), lambda: step(i + 1))

        step(0)

    # -- undo / redo --------------------------------------------------------
    def push_undo(self) -> None:
        """Snapshot the project *before* a mutation (called by every editor action)."""
        try:
            import json

            self._undo.append(json.dumps(self.project.to_dict(), ensure_ascii=False, default=str))
            if len(self._undo) > 50:
                self._undo.pop(0)
            self._redo.clear()          # 新操作作废重做链
            self._update_history_buttons()
        except Exception:
            pass

    def _restore(self, blob: str) -> None:
        """Rebuild the project from a snapshot and refresh every panel."""
        import json

        current = getattr(self.timeline, "selected", None)
        selected_id = current.id if current is not None else None
        self.project = Project.from_dict(json.loads(blob))
        restored = None
        if selected_id:
            for track in self.project.tracks:
                for seg in track.segments:
                    if seg.id == selected_id:
                        restored = seg
                        break
                if restored is not None:
                    break
        self.timeline.selected = restored
        self._renderer = None               # 素材/参数都可能变了，缓存的渲染器要丢
        self.playhead = min(self.playhead, max(0, self.project.duration() - 1))
        self.refresh_bin()
        self.timeline.redraw()
        self.props.rebuild(restored)
        self.invalidate()
        self._schedule_render()

    def undo(self) -> None:
        if not self._undo:
            self.set_status("没有可撤销的操作")
            return
        import json

        current = json.dumps(self.project.to_dict(), ensure_ascii=False, default=str)
        self._redo.append(current)
        self._restore(self._undo.pop())
        self._update_history_buttons()
        self.set_status(f"已撤销（可撤销 {len(self._undo)} 步）")

    def redo(self) -> None:
        if not self._redo:
            self.set_status("没有可重做的操作")
            return
        import json

        current = json.dumps(self.project.to_dict(), ensure_ascii=False, default=str)
        self._undo.append(current)
        self._restore(self._redo.pop())
        self._update_history_buttons()
        self.set_status(f"已重做（可重做 {len(self._redo)} 步）")

    def _update_history_buttons(self) -> None:
        try:
            self.undo_btn.configure(state="normal" if self._undo else "disabled")
            self.redo_btn.configure(state="normal" if self._redo else "disabled")
        except Exception:
            pass


def _enable_dpi_awareness() -> None:
    """Tell Windows we scale ourselves, otherwise the UI is bitmap-blurred.

    Packaged builds have no manifest, so at 125/150% scaling Windows stretched
    the whole window; the text and the preview looked soft on a 2560x1440
    screen.  Must run before Tk creates its first window.
    """
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)      # per-monitor
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main(project_path: Optional[str] = None) -> int:
    _enable_dpi_awareness()
    app = App(project_path)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))

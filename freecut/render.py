"""Frame compositor.

Rendering model (one output frame at a time):

    background
      -> video tracks, bottom to top
           -> per segment: decode source -> crop -> colour pipeline -> warp ->
              blend (with transition weight against the neighbour)
      -> text tracks (rendered with Pillow, composited with the same transform)

This is the same order the app's ``merge_all_adjust_color`` bundle declares for
its effect chain (``zorder`` 1000 blit .. 10010 blend).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

from . import color as color_mod
from . import transform as tr
from .keyframe import AnimatedProperty
from .media import FrameReader, ImageSource, MediaInfo, probe
from .model import (
    Canvas,
    ImageMaterial,
    Material,
    Project,
    Segment,
    TextMaterial,
    Track,
    VideoMaterial,
)
from .text import TextRenderer

RENDER_ORDER = {"video": 0, "effect": 1, "filter": 2, "text": 3, "sticker": 4}

#: Transition names in JianyingPro are Chinese resource names; the effect itself
#: is a downloaded resource (``transitions[].path``).  We map the common ones to
#: implementations and fall back to a cross dissolve.
TRANSITION_KINDS = {
    "对角收拢": "diagonal_in",
    "角部收拢": "split_in_corner",
    "左右收拢": "split_in_vertical",
    "上下收拢": "split_in_horizontal",
    "左右分屏": "split_vertical",
    "上下分屏": "split_horizontal",
    "圆形擦除": "wipe_radial",
    "斜百叶": "blinds_diagonal",
    "角部擦除": "wipe_corner",
    "中心收拢": "zoom_out",
    "随机马赛克": "mosaic_random",
    "对角滑动": "slide_diagonal",
    "横扫亮带": "wipe_bar",
    "顺时针擦除": "wipe_clock",
    "中心缩放": "zoom_center",
    "竖百叶": "blinds_vertical",
    "反向对角擦除": "wipe_diagonal_reverse",
    "圆形展开": "circle_in",
    "叠化": "cross_dissolve",
    "交叉溶解": "cross_dissolve",
    "dissolve": "cross_dissolve",
    "闪黑": "fade_black",
    "闪白": "fade_white",
    "黑场": "fade_black",
    "白场": "fade_white",
    "向左擦除": "wipe_left",
    "向右擦除": "wipe_right",
    "向上擦除": "wipe_up",
    "向下擦除": "wipe_down",
    "向左滑动": "slide_left",
    "向右滑动": "slide_right",
    "向上滑动": "slide_up",
    "向下滑动": "slide_down",
    "圆形": "circle",
    "圆形遮罩": "circle",
    "斜向擦除": "wipe_diagonal",
    "对角线": "wipe_diagonal",
    "方形": "square",
    "矩形": "square",
    "百叶窗": "blinds",
    "百叶": "blinds",
    "像素化": "mosaic_dissolve",
    "马赛克过渡": "mosaic_dissolve",
    "缩放": "zoom_push",
    "推近": "zoom_push",
    "翻页": "page_turn",
    "书页": "page_turn",
    "溶解": "pixel_dissolve",
    "像素溶解": "pixel_dissolve",
    "块状溶解": "pixel_dissolve",
}


def transition_kind(mat) -> str:
    if mat is None:
        return "cross_dissolve"
    explicit = str(mat.get("kind") or "")
    if explicit in set(TRANSITION_KINDS.values()):
        return explicit
    name = str(mat.get("name") or "")
    # 内部引擎名（wipe_diagonal / zoom_push …）也要认：我们自己写工程时
    # name 可能直接就是引擎名，不认就会被当成"未知转场"退回叠化
    if name in set(TRANSITION_KINDS.values()):
        return name
    if name in TRANSITION_KINDS:
        return TRANSITION_KINDS[name]
    for key, kind in TRANSITION_KINDS.items():
        if key and key in name:
            return kind
    # a draft may only carry the resource id; resolve it through the catalog
    try:
        from . import catalog

        hit = catalog.find_transition(resource_id=str(mat.get("resource_id") or ""))
        if hit:
            resolved = hit["name"]
            if resolved in TRANSITION_KINDS:
                return TRANSITION_KINDS[resolved]
            for key, kind in TRANSITION_KINDS.items():
                if key and key in resolved:
                    return kind
    except Exception:
        pass
    return "cross_dissolve"


def _active_word(mat, local_us: int):
    """Index of the karaoke word active at ``local_us`` (None when not karaoke)."""
    words = mat.get("words") if mat is not None else None
    if not words:
        return None
    from .subtitles import find_active_word

    return find_active_word(words, local_us)


def _hex_rgb(value, default=(1.0, 1.0, 1.0)):
    """``#RRGGBB`` → (r, g, b) float32；解析不了就返回默认值。"""
    text = str(value or "").lstrip("#")
    if len(text) == 6:
        try:
            return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        except ValueError:
            pass
    return tuple(default)

def _transition_mask(kind: str, u: float, width: int, height: int) -> np.ndarray:
    """Soft geometric mask in [0,1] for wipe/slide/circle transitions."""
    yy = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    soft = 0.03
    if kind == "wipe_left":
        return np.clip((u - (1.0 - xx)) / soft, 0.0, 1.0)
    if kind == "wipe_right":
        return np.clip((u - xx) / soft, 0.0, 1.0)
    if kind == "wipe_up":
        return np.clip((u - (1.0 - yy)) / soft, 0.0, 1.0)
    if kind == "wipe_down":
        return np.clip((u - yy) / soft, 0.0, 1.0)
    if kind == "slide_left":
        return (xx <= u).astype(np.float32)
    if kind == "slide_right":
        return (xx >= 1.0 - u).astype(np.float32)
    if kind == "slide_up":
        return (yy <= u).astype(np.float32)
    if kind == "slide_down":
        return (yy >= 1.0 - u).astype(np.float32)
    if kind == "wipe_diagonal":
        d = (xx + yy) * 0.5
        return np.clip((u * 1.4 - d) / soft, 0.0, 1.0)
    if kind == "square":
        # 中心向外的方框：取到中心的切比雪夫距离
        cx = np.abs(xx - 0.5) * 2.0
        cy = np.abs(yy - 0.5) * 2.0
        d = np.maximum(cx, cy)
        return np.clip((u * 1.1 - d) / soft, 0.0, 1.0)
    if kind == "blinds":
        bars = 8
        phase = np.floor(yy * bars) % 2.0          # 交错的两组
        local = (yy * bars) - np.floor(yy * bars)
        reach = np.clip(u * 2.0 - phase, 0.0, 1.0)  # 每组轮流展开
        mask = np.clip((reach - local) / max(1e-3, soft * bars), 0.0, 1.0)
        # yy 只有 (H,1)，必须广播成整幅形状再返回，否则调用方拿到的不是 (H,W)
        return np.broadcast_to(mask, (height, width)).astype(np.float32)
    if kind == "circle_in":
        # 中心圆向外展开：半径 0 → 覆盖对角（(H,W) 天然广播）
        cx = (xx - 0.5) * 2.0
        cy = (yy - 0.5) * 2.0
        d = np.sqrt(cx * cx + cy * cy) / 1.42
        return np.clip((u * 1.5 - d) / soft, 0.0, 1.0)
    if kind == "wipe_diagonal_reverse":
        d = 1.0 - (xx + yy) * 0.5
        return np.clip((u * 1.45 - d) / soft, 0.0, 1.0)
    if kind == "blinds_vertical":
        bars = 8
        phase = np.floor(xx * bars) % 2.0
        local = (xx * bars) - np.floor(xx * bars)
        reach = np.clip(u * 2.0 - phase, 0.0, 1.0)
        mask = np.clip((reach - local) / max(1e-3, soft * bars), 0.0, 1.0)
        return np.broadcast_to(mask, (height, width)).astype(np.float32)
    if kind == "zoom_center":
        cx = (xx - 0.5) * 2.0
        cy = (yy - 0.5) * 2.0
        d = np.maximum(np.abs(cx), np.abs(cy))
        return (d <= u * 1.05).astype(np.float32)
    if kind == "wipe_clock":
        ang = np.arctan2(yy - 0.5, xx - 0.5) / (2.0 * np.pi) + 0.5
        return np.clip((u * 1.05 - ang) / soft, 0.0, 1.0)
    if kind == "wipe_bar":
        # 一条从上往下扫的软边亮带
        return np.clip((u * 1.2 - yy) / soft, 0.0, 1.0)
    if kind == "slide_diagonal":
        return ((xx + yy) * 0.5 <= u).astype(np.float32)
    if kind == "mosaic_random":
        blocks = 16
        rng = np.random.default_rng(20261024)
        th = rng.random((blocks, blocks)).astype(np.float32)
        ry = int(np.ceil(height / blocks))
        rx = int(np.ceil(width / blocks))
        big = np.repeat(np.repeat(th, ry, axis=0), rx, axis=1)[:height, :width]
        return (u >= big).astype(np.float32)
    if kind == "zoom_out":
        cx = (xx - 0.5) * 2.0
        cy = (yy - 0.5) * 2.0
        d = np.maximum(np.abs(cx), np.abs(cy))
        return (d >= (1.0 - u) * 1.05).astype(np.float32)
    if kind == "wipe_corner":
        d = np.maximum(xx, yy)
        return np.clip((u * 1.35 - d) / soft, 0.0, 1.0)
    if kind == "blinds_diagonal":
        bars = 8
        t = (xx + yy) * 0.5
        phase = np.floor(t * bars) % 2.0
        local = (t * bars) - np.floor(t * bars)
        reach = np.clip(u * 2.0 - phase, 0.0, 1.0)
        mask = np.clip((reach - local) / max(1e-3, soft * bars), 0.0, 1.0)
        return np.broadcast_to(mask, (height, width)).astype(np.float32)
    if kind == "wipe_radial":
        cx = (xx - 0.5) * 2.0
        cy = (yy - 0.5) * 2.0
        d = np.sqrt(cx * cx + cy * cy) / 1.42
        return (d <= u * 1.05).astype(np.float32)
    if kind == "split_horizontal":
        d = np.abs(yy - 0.5) * 2.0
        return (d <= u * 1.05).astype(np.float32)
    if kind == "split_vertical":
        d = np.abs(xx - 0.5) * 2.0
        return (d <= u * 1.05).astype(np.float32)
    if kind == "split_in_horizontal":
        d = np.abs(yy - 0.5) * 2.0
        return (d >= (1.0 - u) * 1.05).astype(np.float32)
    if kind == "split_in_vertical":
        d = np.abs(xx - 0.5) * 2.0
        return (d >= (1.0 - u) * 1.05).astype(np.float32)
    if kind == "split_in_corner":
        cx = np.abs(xx - 0.5) * 2.0
        cy = np.abs(yy - 0.5) * 2.0
        d = np.maximum(cx, cy)
        return (d >= (1.0 - u) * 1.05).astype(np.float32)
    if kind == "diagonal_in":
        d = (xx + yy) * 0.5
        return (d >= (1.0 - u) * 1.05).astype(np.float32)
    if kind == "page_turn":
        # 一条竖直折线从左扫到右；折线附近留一条渐亮的"纸背"带
        fold = u * 1.15 - 0.05
        edge = np.clip((fold - xx) / max(1e-3, soft * 2.0), 0.0, 1.0)
        # xx 只有 (1, W)，必须广播成整幅形状（blinds 那轮踩过同样的坑）
        return np.broadcast_to(edge, (height, width)).astype(np.float32)
    if kind == "pixel_dissolve":
        # 每个小块有自己的阈值：u 越过它就从上一段换成下一段
        blocks = 24
        rng = np.random.default_rng(20261022)      # 固定种子：同一工程每次渲染一致
        table = rng.random((blocks, blocks)).astype(np.float32)
        ys = np.clip((yy * blocks).astype(np.int32), 0, blocks - 1)
        xs = np.clip((xx * blocks).astype(np.int32), 0, blocks - 1)
        threshold = table[ys, xs]
        return np.clip((u - threshold) * 12.0, 0.0, 1.0).astype(np.float32)
    if kind == "circle":
        r = np.sqrt((xx - 0.5) ** 2 + (yy - 0.5) ** 2) / 0.7072
        return np.clip((u * 1.1 - r) / soft, 0.0, 1.0)
    return np.full((height, width), u, dtype=np.float32)


@dataclass
class RenderStats:
    frames: int = 0
    decoded: int = 0
    cached: int = 0


class SegmentSource:
    """Decoded pixels for one segment, abstracting video vs still image."""

    def __init__(self, project: Project, seg: Segment, fps: float, canvas: Canvas,
                 reader=None):
        self.seg = seg
        self.canvas = canvas
        self.owns_reader = reader is None
        self.mat: Optional[Material] = project.material(seg.material_id)
        self.kind = getattr(self.mat, "type", None)
        self.reader: Optional[FrameReader] = None
        self.image: Optional[ImageSource] = None
        self.src_w = 0
        self.src_h = 0
        path = getattr(self.mat, "path", "") if self.mat is not None else ""
        if self.kind == "video" and path and os.path.isfile(path):
            cap = min(1920, max(int(canvas.width), 640))
            # 草稿里就写着素材尺寸时不要再 probe：每个片段一次 ffprobe
            # 等于首帧多等 0.3 秒（HANDOFF D2）。
            sw = int(getattr(self.mat, "width", 0) or 0)
            sh = int(getattr(self.mat, "height", 0) or 0)
            need_probe = not (sw > 0 and sh > 0)
            info = None
            if need_probe:
                try:
                    info = probe(path)
                except Exception:
                    info = None
                sw = (info.width if info else 0) or cap
                sh = (info.height if info else 0) or int(round(cap * 9 / 16))
            scale = min(1.0, cap / float(max(sw, 1)))
            out_w = max(2, int(round(sw * scale / 2.0)) * 2)
            out_h = max(2, int(round(sh * scale / 2.0)) * 2)
            if reader is not None:
                self.reader = reader
            else:
                self.reader = FrameReader(
                    path,
                    src_start_us=max(0, seg.source_timerange.start),
                    speed=seg.speed,
                    width=out_w,
                    height=out_h,
                    fps=fps,
                    probe_info=False,
                )
            self.src_w = self.reader.out_w
            self.src_h = self.reader.out_h
        elif self.kind in ("image", "text") and path and os.path.isfile(path):
            try:
                self.image = ImageSource(path)
                self.src_h, self.src_w = self.image.src.shape[:2]
            except Exception:
                self.image = None

    def close(self) -> None:
        # 共享解码器由 Renderer 统一关（否则同一个文件会被关好几次）
        if self.reader is not None and self.owns_reader:
            self.reader.close()

    def frame(self, timeline_us: int) -> Optional[Tuple[np.ndarray, Optional[np.ndarray]]]:
        if self.reader is not None:
            arr = self.reader.frame_at(self.seg.source_time(timeline_us)).astype(np.float32) / 255.0
            return arr, None
        if self.image is not None:
            rgba = self.image.frame(self.src_w, self.src_h).astype(np.float32) / 255.0
            return rgba[..., :3], rgba[..., 3]
        return None


# --------------------------------------------------------------------------- #
# crop helper
# --------------------------------------------------------------------------- #
def crop_box(mat: Material, src_w: int, src_h: int) -> Optional[Tuple[int, int, int, int]]:
    crop = mat.get("crop") if mat is not None else None
    if not isinstance(crop, dict):
        return None
    try:
        x0 = float(crop.get("upper_left_x", 0.0))
        y0 = float(crop.get("upper_left_y", 0.0))
        x1 = float(crop.get("lower_right_x", 1.0))
        y1 = float(crop.get("lower_right_y", 1.0))
    except (TypeError, ValueError):
        return None
    if (x0, y0, x1, y1) == (0.0, 0.0, 1.0, 1.0):
        return None
    x = int(round(min(x0, x1) * src_w))
    y = int(round(min(y0, y1) * src_h))
    w = int(round(abs(x1 - x0) * src_w))
    h = int(round(abs(y1 - y0) * src_h))
    if w < 2 or h < 2:
        return None
    return x, y, w, h


# --------------------------------------------------------------------------- #
# renderer
# --------------------------------------------------------------------------- #
def _switch(seg, key: str) -> bool:
    """Read a 剪映 ``enable_*`` switch off a segment (absent means on)."""
    stored = getattr(seg, "enable", None) or {}
    if key in stored:
        return bool(stored[key])
    value = getattr(seg, key, None)
    return True if value is None else bool(value)


class Renderer:
    def __init__(
        self,
        project: Project,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[float] = None,
    ) -> None:
        self.project = project
        self.width = int(width or project.canvas.width or 1920)
        self.height = int(height or project.canvas.height or 1080)
        self.canvas = Canvas(self.width, self.height, project.canvas.ratio, project.canvas.background)
        self.fps = float(fps or project.fps or 30.0)
        self.text = TextRenderer(self.width, self.height)
        self.sources: Dict[str, SegmentSource] = {}
        #: 同一文件 + 同一尺寸只开一个解码器（渲染器持有，close() 时释放）
        self._readers: Dict[tuple, object] = {}
        self._luts: Dict[str, Optional[np.ndarray]] = {}
        self.stats = RenderStats()
        self._prepare()

    # -- 解码器池 ------------------------------------------------------------ #
    def _reader_for(self, path: str, width: int, height: int, fps: float):
        import os

        key = (os.path.normcase(os.path.abspath(path)), int(width), int(height))
        reader = self._readers.get(key)
        if reader is None:
            reader = FrameReader(path, width=width, height=height, fps=fps,
                                 probe_info=False)
            self._readers[key] = reader
        return reader

    # -- setup --------------------------------------------------------------
    def _prepare(self) -> None:
        """只登记，不解码。

        以前这里对**每个片段**建 ``SegmentSource``（每个都 ffprobe + 打开调色资源），
        38 段的工程首帧因此要 8 秒。现在按帧懒建：第一次画到某个片段才建它，
        之后复用同一个解码器。导出时性能不变（每个片段都要画到）。
        """
        self._known: Dict[str, Segment] = {}
        for track in self.project.tracks:
            for seg in track.segments:
                self._known[seg.id] = seg

    def _source(self, seg: Segment) -> Optional[SegmentSource]:
        src = self.sources.get(seg.id)
        if src is not None:
            return src
        try:
            src = SegmentSource(self.project, seg, self.fps, self.canvas)
        except Exception:
            src = SegmentSource.__new__(SegmentSource)
            src.seg, src.canvas, src.mat, src.kind = seg, self.canvas, None, None
            src.reader = src.image = None
            src.src_w = src.src_h = 0
        # 视频片段换用渲染器自己的解码器池（同一文件只开一个容器）
        reader = getattr(src, "reader", None)
        if reader is not None:
            try:
                path = str(getattr(src.mat, "path", "") or "")
                if path:
                    shared = self._reader_for(path, reader.out_w, reader.out_h, self.fps)
                    if shared is not reader:
                        reader.close()
                        src.reader = shared
                        src.owns_reader = False
                        src.src_w, src.src_h = shared.out_w, shared.out_h
                    else:
                        src.owns_reader = False
            except Exception:                          # noqa: BLE001
                pass
        self.sources[seg.id] = src
        return src

    def close(self) -> None:
        for s in self.sources.values():
            try:
                s.close()
            except Exception:
                pass
        self.sources.clear()
        for reader in self._readers.values():
            try:
                reader.close()
            except Exception:
                pass
        self._readers.clear()

    def __enter__(self) -> "Renderer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- frame count --------------------------------------------------------
    @property
    def duration_us(self) -> int:
        return self.project.duration()

    @property
    def frame_count(self) -> int:
        return max(1, int(round(self.duration_us / 1_000_000.0 * self.fps)))

    def frame_time(self, index: int) -> int:
        return int(round(index * 1_000_000.0 / self.fps))

    # -- colour -------------------------------------------------------------
    def _lut_for(self, path: str) -> Optional[np.ndarray]:
        if not path:
            return None
        if path in self._luts:
            return self._luts[path]
        lut = None
        if os.path.isfile(path) and path.lower().endswith(".cube"):
            try:
                lut = color_mod.load_cube_lut(path)
            except Exception:
                lut = None
        self._luts[path] = lut
        return lut

    def _adjust_params(self, seg: Segment, mat: Material) -> Tuple[dict, Optional[np.ndarray]]:
        params: dict = {}
        lut = None
        own = mat.get("adjust") if mat is not None else None
        if isinstance(own, dict):
            params.update(own)
        for ref in self.project.refs_of(seg):
            if ref.type == "filter":
                p = ref.get("path") or ""
                lut = self._lut_for(p)
                inten = float(ref.get("intensity_value", 1.0) or 1.0)
                if lut is not None and inten < 1.0:
                    params["_lut_intensity"] = inten
            elif ref.type == "adjust":
                params.update(ref.get("params") or {})
        return params, lut

    #: 调节参数的草稿属性名（来自 186 个 KFType 枚举，见
    #: ``docs/measurements/kftype-values.txt``）。
    #: 注意剪映把「高光」拼成 ``KFTypeHightLight``（多一个 t），照抄才认。
    ADJUST_KEYFRAMES = {
        "brightness": "KFTypeBrightness",
        "contrast": "KFTypeContrast",
        "saturation": "KFTypeSaturation",
        "temperature": "KFTypeTemperature",
        "highlight": "KFTypeHightLight",
        "shadow": "KFTypeShadow",
        "sharpen": "KFTypeSharpen",
        "vignette": "KFTypeVignetting",
        "white": "KFTypeWhite",
        "black": "KFTypeBlack",
    }

    def _adjust_animated(self, params: dict, seg: Segment, offset: int) -> dict:
        """把带关键帧的调节参数替换成当前时刻的插值。"""
        if not params or not seg.keyframes:
            return params
        out = dict(params)
        for key, prop in self.ADJUST_KEYFRAMES.items():
            keys = seg.keyframes.get(prop)
            if not keys:
                continue
            out[key] = AnimatedProperty(float(out.get(key, 0.0) or 0.0), keys).value_at(offset)
        return out

    def _color(self, rgb: np.ndarray, seg: Segment, mat: Material,
               offset: int = 0) -> np.ndarray:
        params, lut = self._adjust_params(seg, mat)
        params = self._adjust_animated(params, seg, offset)
        # the app's zorder: LUT(2000) -> HSL(5016) -> curves(5017) -> adjust(8004)
        hsl = None
        curves = None
        wheels = None
        hsl_curves = None
        migration = None
        for ref in self.project.refs_of(seg):
            if ref.type == "hsl":
                hsl = ref
            elif ref.type == "curve":
                curves = ref
            elif ref.type == "color_wheel":
                wheels = ref
            elif ref.type == "hsl_curves":
                hsl_curves = ref
            elif ref.type == "color_migration":
                migration = ref
        # 草稿里的 enable_* 开关必须被尊重：写进去的开关如果没人读，
        # 剪映里关掉的效果在我们这里还会生效（第 83 轮审计发现）。
        # 字段缺失时按"开"处理，兼容不写这些开关的草稿。
        def enabled(key: str) -> bool:
            return _switch(seg, key)

        if not enabled("enable_lut"):
            lut = None
        if not enabled("enable_hsl"):
            hsl = None
        if not enabled("enable_color_curves"):
            curves = None
        if not enabled("enable_color_wheels"):
            wheels = None
        if not enabled("enable_hsl_curves"):
            hsl_curves = None
        if not enabled("enable_adjust"):
            params = {}

        if (not params and lut is None and hsl is None and curves is None
                and wheels is None and hsl_curves is None and migration is None):
            return rgb
        out = rgb
        if lut is not None:
            inten = float(params.pop("_lut_intensity", 1.0)) if "_lut_intensity" in params else 1.0
            out = color_mod.apply_lut3d(out, lut, inten)
        if hsl is not None:
            out = color_mod.apply_hsl(out, hsl.get("bands") or {}, hsl.get("fallback") or {})
        if curves is not None:
            out = color_mod.apply_curves(out, curves.get("curves") or {})
        if wheels is not None:
            out = color_mod.apply_wheels(out, wheels.get("wheels") or {})
        if hsl_curves is not None:
            out = color_mod.apply_hsl_curves(out, hsl_curves.get("bands") or {})
        if migration is not None:
            out = self._migrate(out, migration)
        if params:
            out = color_mod.apply_adjust(out, params)
        return out

    def _migrate(self, rgb: np.ndarray, mat) -> np.ndarray:
        """Colour migration: bake a LUT from the reference stats, then apply it."""
        from . import color as color_mod

        try:
            ref_mean = np.asarray(mat.get("reference_mean"), dtype=np.float32)
            ref_std = np.asarray(mat.get("reference_std"), dtype=np.float32)
        except Exception:
            return rgb
        if ref_mean.shape != (3,) or ref_std.shape != (3,):
            return rgb
        # 用**当前这一帧**的统计量作为源，迁移才会跟着画面走
        flat = rgb.reshape(-1, 3)
        src_mean, src_std = flat.mean(axis=0), flat.std(axis=0) + 1e-6
        key = (tuple(np.round(src_mean, 3)), tuple(np.round(src_std, 3)),
               tuple(np.round(ref_mean, 3)), tuple(np.round(ref_std, 3)))
        cache = getattr(self, "_migration_cache", None)
        if cache is None:
            cache = self._migration_cache = {}
        lut = cache.get(key)
        if lut is None:
            grid = np.linspace(0.0, 1.0, 17, dtype=np.float32)
            g = np.stack(np.meshgrid(grid, grid, grid, indexing="ij"), axis=-1)
            lut = np.clip((g - src_mean) / src_std * ref_std + ref_mean, 0.0, 1.0).astype(np.float32)
            if len(cache) > 32:
                cache.clear()
            cache[key] = lut
        return color_mod.apply_lut3d(rgb, lut, float(mat.get("intensity", 1.0) or 1.0))

    # -- segment ------------------------------------------------------------
    def _blend_mode(self, seg: Segment) -> str:
        for ref in self.project.refs_of(seg):
            if ref.type == "mix_mode":
                name = str(ref.get("name") or "")
                if name in color_mod.BLEND_NAME_TO_ID:
                    return color_mod.BLEND_NAME_TO_ID[name]
                ident = str(ref.get("name_id") or ref.get("resource_id") or "")
                if ident in color_mod.BLEND_MODES:
                    return ident
                try:
                    from . import catalog

                    for item in catalog.mix_modes():
                        if item["resource_id"] == str(ref.get("resource_id") or ""):
                            return color_mod.BLEND_NAME_TO_ID.get(item["name"], "normal")
                except Exception:
                    pass
        return "normal"

    def _draw_segment(
        self,
        canvas_rgb: np.ndarray,
        seg: Segment,
        t: int,
        weight: float = 1.0,
    ) -> np.ndarray:
        src = self._source(seg)
        if src is None:
            return canvas_rgb
        got = src.frame(t)
        if got is None:
            return canvas_rgb
        rgb, alpha_src = got
        mat = src.mat

        # crop
        box = crop_box(mat, rgb.shape[1], rgb.shape[0]) if mat is not None else None
        if box:
            x, y, w, h = box
            rgb = np.ascontiguousarray(rgb[y : y + h, x : x + w])
            if alpha_src is not None:
                alpha_src = np.ascontiguousarray(alpha_src[y : y + h, x : x + w])
        src_w, src_h = rgb.shape[1], rgb.shape[0]

        # 调节蒙版：蒙版只限制**调色**作用范围，不裁画面（与 video_mask 区分）
        # 剪映草稿里有 enable_adjust_mask 这个开关，但字段语义未被证实，
        # 这里按"调色只在蒙版内生效"实现；导出时会提示剪映可能不支持。
        adjust_mask = self._adjust_mask(seg, rgb.shape[1], rgb.shape[0])

        graded = (self._color(rgb, seg, mat, t - seg.target_timerange.start)
                  if mat is not None else rgb)
        if adjust_mask is not None:
            w = np.clip(adjust_mask, 0.0, 1.0)[..., None]
            graded = rgb * (1.0 - w) + graded * w
        rgb = np.ascontiguousarray(graded)

        # mask (stored as a separate material, applied in the clip's own space)
        mask_mat = self.project.first_ref(seg, "mask")
        if not _switch(seg, "enable_video_mask"):
            mask_mat = None
        if mask_mat is not None:
            try:
                from .mask import build_mask

                kind = str(mask_mat.get("resource_type") or mask_mat.get("name") or "")
                cfg = mask_mat.get("config") or {}
                if kind == "chroma":
                    from . import chroma as chroma_mod

                    rgb, ch_alpha = chroma_mod.apply(
                        rgb,
                        key_hex=str(cfg.get("key") or "#00ff00"),
                        similarity=float(cfg.get("similarity", 0.25)),
                        smoothness=float(cfg.get("smoothness", 0.12)),
                        spill=float(cfg.get("spill", 0.6)),
                        alpha_in=alpha_src,
                    )
                    alpha_src = ch_alpha
                else:
                    if not _switch(seg, "enable_video_mask"):
                        raise StopIteration
                    mask = build_mask(src_w, src_h, cfg, kind)
                    alpha_src = mask if alpha_src is None else (alpha_src * mask)
                    # 蒙版描边（剪映 video_mask_stroke）：在环带上刷一层纯色
                    stroke = mask_mat.get("stroke") or {}
                    stroke_w = int(round(float(stroke.get("width", 0) or 0)))
                    if stroke_w > 0:
                        from .mask import add_stroke

                        ring = add_stroke(mask, stroke_w,
                                          _hex_rgb(stroke.get("color", "#FFFFFF")),
                                          float(stroke.get("softness", 1.0) or 1.0))
                        ring_a = np.clip(ring.sum(axis=2), 0.0, 1.0)
                        rgb = rgb * (1.0 - ring_a[..., None]) + ring
                        alpha_src = np.clip(alpha_src + ring_a, 0.0, 1.0)
                    # 蒙版投影（video_mask_shadow）：影子只在蒙版**外面**
                    shadow = mask_mat.get("shadow") or {}
                    if float(shadow.get("opacity", 0) or 0) > 0:
                        from .mask import add_shadow

                        s_rgb, s_a = add_shadow(
                            mask,
                            offset=(int(shadow.get("dx", 4) or 4),
                                    int(shadow.get("dy", 4) or 4)),
                            blur=int(shadow.get("blur", 6) or 6),
                            opacity=float(shadow.get("opacity", 0.55) or 0.55),
                            colour=_hex_rgb(shadow.get("color", "#000000")),
                        )
                        rgb = s_rgb * s_a[..., None] + rgb * (1.0 - s_a[..., None])
                        alpha_src = np.clip(alpha_src + s_a, 0.0, 1.0)
            except Exception:
                pass

        # animated transform
        offset = t - seg.target_timerange.start
        clip = seg.clip
        ax = lambda name, base: (  # noqa: E731
            AnimatedProperty(base, seg.keyframes.get(name)).value_at(offset)
            if seg.keyframes.get(name)
            else base
        )
        sx = ax("KFTypeScaleX", clip.scale_x)
        sy = ax("KFTypeScaleY", clip.scale_y)
        rot = ax("KFTypeRotation", clip.rotation)
        tx = ax("KFTypePositionX", clip.tx)
        ty = ax("KFTypePositionY", clip.ty)
        al = ax("KFTypeAlpha", clip.alpha) * weight

        # 入场 / 出场 / 循环 animations
        anim_mat = self.project.first_ref(seg, "material_animation")
        if anim_mat is not None:
            from .animation import animations_at

            st = animations_at(anim_mat.get("animations"), offset, seg.duration)
            sx *= st.scale
            sy *= st.scale
            rot += st.rotation
            tx += st.dx
            ty += st.dy
            al *= st.alpha
            if st.tint > 0.0:
                rgb = rgb + (1.0 - rgb) * min(1.0, st.tint)

        m = tr.build_affine(
            src_w,
            src_h,
            self.width,
            self.height,
            scale_x=sx,
            scale_y=sy,
            rotation=rot,
            tx=tx,
            ty=ty,
            flip_h=clip.flip_h,
            flip_v=clip.flip_v,
        )
        layer, alpha = tr.warp_layer(rgb, alpha_src, m, self.width, self.height, al)

        # 特效：作用在已经变换到画布空间的图层上（故障/抖动/马赛克才符合直觉）
        effects = [r for r in self.project.refs_of(seg) if r.type == "effect"]
        if effects:
            from .effects import apply_chain

            layer, alpha = apply_chain(
                [{"kind": e.get("kind"), "params": e.get("params") or {}} for e in effects],
                layer, alpha, t,
            )

        mode = self._blend_mode(seg)
        out = tr.over(canvas_rgb, layer, alpha, mode)
        return out

    def _draw_text(self, canvas_rgb: np.ndarray, seg: Segment, t: int, weight: float = 1.0) -> np.ndarray:
        mat = self.project.material(seg.material_id)
        if not isinstance(mat, TextMaterial) and not (mat is not None and mat.type == "text"):
            return canvas_rgb
        offset = t - seg.target_timerange.start
        clip = seg.clip
        ax = lambda name, base: (  # noqa: E731
            AnimatedProperty(base, seg.keyframes.get(name)).value_at(offset)
            if seg.keyframes.get(name)
            else base
        )
        tx = ax("KFTypePositionX", clip.tx)
        ty = ax("KFTypePositionY", clip.ty)
        al = ax("KFTypeAlpha", clip.alpha) * weight
        anim_mat = self.project.first_ref(seg, "material_animation")
        if anim_mat is not None:
            from .animation import animations_at

            st = animations_at(anim_mat.get("animations"), offset, seg.duration)
            al *= st.alpha
            tx += st.dx
            ty += st.dy
        layer = self.text.render(mat, anchor=(tx, ty), active_word=_active_word(mat, offset))
        arr = layer.image
        rgb = arr[..., :3].astype(np.float32) / 255.0
        alpha = arr[..., 3].astype(np.float32) / 255.0 * al
        h, w = arr.shape[:2]
        mask_mat = self.project.first_ref(seg, "mask")
        if mask_mat is not None:
            try:
                from .mask import build_mask

                kind = str(mask_mat.get("resource_type") or mask_mat.get("name") or "")
                alpha = alpha * build_mask(w, h, mask_mat.get("config") or {}, kind)
            except Exception:
                pass
        dcx = self.width / 2.0 + tx * self.width / 2.0
        dcy = self.height / 2.0 - ty * self.height / 2.0
        x0 = int(round(dcx - w / 2.0))
        y0 = int(round(dcy - h / 2.0))
        sx0, sy0 = max(0, x0), max(0, y0)
        sx1, sy1 = min(self.width, x0 + w), min(self.height, y0 + h)
        if sx1 <= sx0 or sy1 <= sy0:
            return canvas_rgb
        cx0, cy0 = sx0 - x0, sy0 - y0
        sub_rgb = rgb[cy0 : cy0 + (sy1 - sy0), cx0 : cx0 + (sx1 - sx0)]
        sub_a = alpha[cy0 : cy0 + (sy1 - sy0), cx0 : cx0 + (sx1 - sx0)]
        region = canvas_rgb[sy0:sy1, sx0:sx1]
        canvas_rgb[sy0:sy1, sx0:sx1] = tr.over(region, sub_rgb, sub_a)
        return canvas_rgb

    # -- transitions --------------------------------------------------------
    def _track_segments(self, track: Track) -> List[Segment]:
        return [s for s in track.sorted_segments() if s.visible]

    def _transition_for(self, seg: Segment) -> Tuple[Optional[Material], int]:
        for ref in self.project.refs_of(seg):
            if ref.type == "transition":
                dur = int(ref.get("duration", 0) or 0)
                if dur <= 0:
                    dur = seg.duration // 4
                return ref, max(0, dur)
        return None, 0

    def _is_adjustment(self, seg: Segment) -> bool:
        mat = self.project.material(seg.material_id)
        if mat is None:
            return False
        return getattr(mat, "type", "") == "adjustment"


    def _adjust_mask(self, seg: Segment, width: int, height: int):
        """只限制调色的蒙版（``enable_adjust_mask``）对应的透明度图。"""
        if not _switch(seg, "enable_adjust_mask"):
            return None
        mask_ref = self.project.first_ref(seg, "mask")
        if mask_ref is None or str(mask_ref.get("resource_type") or "") == "chroma":
            return None
        try:
            from .mask import build_mask

            return build_mask(width, height, mask_ref.get("config") or {},
                              str(mask_ref.get("resource_type") or mask_ref.get("name") or ""))
        except Exception:
            return None

    def _apply_adjustment(self, canvas_rgb: np.ndarray, seg: Segment,
                          t: int = 0) -> np.ndarray:
        """调节图层：把颜色链作用到下方画面，蒙版存在时只作用在蒙版内。"""
        mat = self.project.material(seg.material_id)
        graded = self._color(canvas_rgb, seg, mat, t - seg.target_timerange.start)
        mask = self._adjust_mask(seg, canvas_rgb.shape[1], canvas_rgb.shape[0])
        if mask is None:
            return graded
        weight = np.clip(mask, 0.0, 1.0)[..., None]
        return canvas_rgb * (1.0 - weight) + graded * weight

    def _draw_track(self, canvas_rgb: np.ndarray, track: Track, t: int) -> np.ndarray:
        segs = self._track_segments(track)
        active = [s for s in segs if s.target_timerange.contains(t)]
        if not active:
            return canvas_rgb
        if len(active) == 1:
            seg = active[0]
            if self._is_adjustment(seg):
                # 调节图层：把颜色链作用到下方已经合成好的画面上
                return self._apply_adjustment(canvas_rgb, seg, t)
            # 转场窗口以切点为中心：切点前后各占一半时长。
            # 先看"本片段开头"的转场（切点之后那一半），再看"下一段开头"的
            # 转场（切点之前那一半）——后者原来没人处理，用户在接缝处看不到效果。
            here, dur_here = self._transition_for(seg)
            if here is not None and dur_here > 0:
                start = seg.target_timerange.start
                lead = dur_here // 2
                if start - lead <= t < start + dur_here - lead:
                    u = (t - (start - lead)) / float(dur_here)
                    out = self._apply_transition(canvas_rgb, segs, seg,
                                                 transition_kind(here), u, t)
                    if out is not None:
                        return out
            ahead, dur_ahead = self._next_transition(segs, seg)
            if ahead is not None and dur_ahead > 0:
                start = ahead.target_timerange.start
                lead = dur_ahead // 2
                if start - lead <= t < start:
                    u = (t - (start - lead)) / float(dur_ahead)
                    kind_ahead, _dur = self._transition_for(ahead)
                    out = self._apply_transition(canvas_rgb, segs, ahead,
                                                 transition_kind(kind_ahead), u, t)
                    if out is not None:
                        return out
            return self._draw_segment(canvas_rgb, seg, t, 1.0)
        # overlapping segments -> blend from the earlier to the later one
        active.sort(key=lambda s: s.target_timerange.start)
        out = canvas_rgb
        for seg in active:
            if self._is_adjustment(seg):
                out = self._apply_adjustment(out, seg, t)
                continue
            out = self._draw_segment(out, seg, t, 1.0)
        return out

    def _next_transition(self, segs: List[Segment], seg: Segment):
        """同轨道上紧接在 seg 后面的片段，以及它开头的转场（没有就 (None, 0)）。"""
        end = int(seg.target_timerange.end)
        best = None
        for cand in segs:
            if cand is seg:
                continue
            start = int(cand.target_timerange.start)
            if start < end - 1_000:
                continue                      # 不是后面的片段
            if start - end > 2_000_000:
                continue                      # 太远，不算相邻
            if best is None or start < int(best.target_timerange.start):
                best = cand
        if best is None:
            return None, 0
        trans, dur = self._transition_for(best)
        if trans is None or dur <= 0:
            return None, 0
        return best, dur

    def _find_previous(self, segs: List[Segment], seg: Segment) -> Optional[Segment]:
        start = seg.target_timerange.start
        best = None
        for cand in segs:
            if cand is seg:
                continue
            if cand.target_timerange.end <= start + 1_000 and cand.target_timerange.end > start - 2_000_000:
                if best is None or cand.target_timerange.end > best.target_timerange.end:
                    best = cand
        return best

    def _apply_transition(
        self,
        canvas_rgb: np.ndarray,
        segs: List[Segment],
        seg: Segment,
        kind: str,
        u: float,
        t: int,
    ) -> Optional[np.ndarray]:
        u = min(1.0, max(0.0, u))
        if kind in ("fade_black", "fade_white"):
            # 闪黑 / 闪白：整个转场期间"穿过一个纯色"。
            # 前半段：上一段的最后一帧 → 纯色；后半段：纯色 → 新片段。
            colour = 0.0 if kind == "fade_black" else 1.0
            base = np.full_like(canvas_rgb, colour)
            incoming = self._draw_segment(canvas_rgb, seg, t, 1.0)
            previous = self._find_previous(segs, seg)
            if previous is None:
                # 左边没有片段：至少做成"从纯色淡入"，别什么都不发生
                return base * (1.0 - u) + incoming * u
            outgoing = self._draw_segment(canvas_rgb, previous, t, 1.0)
            if u < 0.5:
                k = u * 2.0
                return outgoing * (1.0 - k) + base * k
            k = (u - 0.5) * 2.0
            return base * (1.0 - k) + incoming * k
        prev = self._find_previous(segs, seg)
        if prev is None:
            return None
        if kind == "zoom_push":
            # 新片段从 1.15 倍收到 1.0，同时淡入
            out = self._draw_segment(canvas_rgb, prev, t, 1.0)
            top = self._draw_segment(np.zeros_like(canvas_rgb), seg, t, 1.0)
            scale = 1.15 - 0.15 * u
            h, w = top.shape[:2]
            nh, nw = max(1, int(h / scale)), max(1, int(w / scale))
            y0, x0 = (h - nh) // 2, (w - nw) // 2
            crop = top[y0:y0 + nh, x0:x0 + nw]
            ys = np.linspace(0, nh - 1, h).astype(np.int32)
            xs = np.linspace(0, nw - 1, w).astype(np.int32)
            pushed = crop[ys][:, xs]
            return out * (1.0 - u) + pushed * u
        if kind == "mosaic_dissolve":
            out = self._draw_segment(canvas_rgb, prev, t, 1.0)
            top = self._draw_segment(np.zeros_like(canvas_rgb), seg, t, 1.0)
            blend = out * (1.0 - u) + top * u
            # 中点最碎：块大小随 |1-2u| 反向变化
            from . import effects as fx

            block = max(2, int(round(40 * (1.0 - abs(1.0 - 2.0 * u)))))
            alpha = np.ones(blend.shape[:2], dtype=np.float32)
            mosaic, _a = fx.apply_effect("mosaic", blend, alpha, t, {"block": block})
            return mosaic
        if kind == "cross_dissolve":
            out = self._draw_segment(canvas_rgb, prev, t, 1.0)
            return self._draw_segment(out, seg, t, u)
        # wipe / slide / circle: composite both layers with a geometric mask
        out = self._draw_segment(canvas_rgb, prev, t, 1.0)
        top = self._draw_segment(np.zeros_like(canvas_rgb), seg, t, 1.0)
        mask = _transition_mask(kind, u, self.width, self.height)
        a = mask[..., None]
        return out * (1.0 - a) + top * a

    # -- public -------------------------------------------------------------
    def render_frame(self, index: int) -> np.ndarray:
        t = self.frame_time(index)
        bg = (0.0, 0.0, 0.0)
        if self.canvas.background:
            v = self.canvas.background.lstrip("#")
            if len(v) == 6:
                try:
                    bg = (
                        int(v[0:2], 16) / 255.0,
                        int(v[2:4], 16) / 255.0,
                        int(v[4:6], 16) / 255.0,
                    )
                except ValueError:
                    pass
        buf = getattr(self, "_canvas_buf", None)
        if buf is None or buf.shape != (self.height, self.width, 3):
            buf = np.empty((self.height, self.width, 3), dtype=np.float32)
            self._canvas_buf = buf
        canvas = buf
        canvas[:] = bg

        # 同类型轨道：**第一行在最上**（画在最后）。剪映/PR 都是"上面的轨道盖住下面的"，
        # 而 project.tracks 里下标 0 是第一条（UI 最上面那行），所以这里按 -index 排。
        # 之前用 +index，导致后加的轨道把前面的盖住（用户实测报过"轨道1空白处看不到轨道2"）。
        tracks = sorted(
            self.project.tracks,
            key=lambda trk: (RENDER_ORDER.get(trk.type, 2), -self.project.tracks.index(trk)),
        )
        for track in tracks:
            if track.type in ("video", "effect", "filter"):
                canvas = self._draw_track(canvas, track, t)
            elif track.type in ("text", "sticker"):
                # several captions may be active at once (e.g. a title plus a
                # subtitle track); draw every one of them instead of hiding all
                # but the first
                for seg in track.sorted_segments():
                    if not seg.target_timerange.contains(t):
                        continue
                    if track.type == "text":
                        canvas = self._draw_text(canvas, seg, t)
                    else:
                        canvas = self._draw_segment(canvas, seg, t)
        self.stats.frames += 1
        # 原地换算再转 uint8：少一次全画幅临时数组（1920x1080 约 25 ms）
        if canvas is self._canvas_buf:
            canvas *= 255.0
            np.clip(canvas, 0.0, 255.0, out=canvas)
            return canvas.astype(np.uint8)
        return np.clip(canvas * 255.0, 0.0, 255.0).astype(np.uint8)

    def frames(self, start: int = 0, count: Optional[int] = None) -> Iterator[Tuple[int, np.ndarray]]:
        total = self.frame_count
        end = total if count is None else min(total, start + count)
        for i in range(start, end):
            yield i, self.render_frame(i)


def render_still(project: Project, t_us: int, width: Optional[int] = None, height: Optional[int] = None):
    """Convenience: render one frame at an absolute timeline instant."""
    r = Renderer(project, width, height)
    try:
        idx = int(round(t_us / 1_000_000.0 * r.fps))
        return r.render_frame(min(max(0, idx), r.frame_count - 1))
    finally:
        r.close()


#: 剪映的中文名 → 我们的引擎（语义明确的同义名 / 序号变体），见 data/transition_aliases.json
def _load_aliases() -> dict:
    import json
    import os

    path = os.path.join(os.path.dirname(__file__), "data", "transition_aliases.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
    except OSError:
        return {}


TRANSITION_ALIASES = _load_aliases()


def resolve_transition(name: str):
    """把剪映里的转场名解析成引擎名；解析不了返回 None。"""
    if not name:
        return None
    key = str(name).strip()
    kind = TRANSITION_KINDS.get(key) or TRANSITION_ALIASES.get(key)
    if kind:
        return kind
    # "闪白 II" / "闪白 2" 这类后缀变体：去掉尾部序号再试一次
    base = key.rstrip(" IIVv23")
    if base and base != key:
        return TRANSITION_KINDS.get(base) or TRANSITION_ALIASES.get(base)
    return None

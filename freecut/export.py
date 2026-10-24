"""Export pipeline: render frames -> FFmpeg encoder -> muxed file."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from .audio import silent_wav, write_audio_mix
from .media import _creationflags, _run, ffmpeg_path, list_encoders
from .model import Project
from .render import Renderer

ProgressFn = Callable[[float, str], None]


# --------------------------------------------------------------------------- #
# presets
# --------------------------------------------------------------------------- #
@dataclass
class ExportPreset:
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    video_codec: str = "auto"       # auto | libx264 | h264_nvenc | h264_mf | mpeg4
    crf: int = 20
    video_bitrate: str = ""          # e.g. "8M"; empty uses CRF
    max_bitrate: str = ""            # caps spikes; defaults to video_bitrate
    buffer_size: str = ""            # defaults to 2x max_bitrate
    gop: int = 0                     # keyframe interval in frames, 0 = codec default
    profile: str = ""                # e.g. "high" / "main"
    level: str = ""                  # e.g. "4.1"
    preset: str = "medium"
    audio_bitrate: str = "192k"
    audio_channels: int = 2
    audio_codec: str = "aac"
    container: str = "mp4"
    pix_fmt: str = "yuv420p"
    faststart: bool = True

    def encode_args(self, codec: str) -> List[str]:
        args: List[str] = []
        if codec == "mpeg4":
            args += ["-c:v", "mpeg4", "-q:v", "3"]
        else:
            args += ["-c:v", codec]
            if codec in ("libx264", "libx265"):
                args += ["-preset", self.preset, "-crf", str(self.crf)]
                if codec == "libx265":
                    args += ["-tag:v", "hvc1"]
            elif codec in ("h264_nvenc", "hevc_nvenc"):
                args += ["-preset", "p5", "-rc", "vbr", "-cq", str(self.crf), "-b:v", "0"]
            elif codec in ("h264_qsv", "hevc_qsv"):
                args += ["-global_quality", str(self.crf)]
            elif codec in ("h264_mf", "hevc_mf"):
                args += ["-rate_control", "quality", "-quality", str(max(1, 100 - self.crf * 2))]
            elif codec in ("h264_amf", "hevc_amf"):
                args += ["-quality", "balanced", "-rc", "cqp", "-qp_i", str(self.crf), "-qp_p", str(self.crf)]
        if self.profile and codec in ("libx264", "libx265", "h264_nvenc", "hevc_nvenc"):
            args += ["-profile:v", self.profile]
        if self.level and codec in ("libx264", "libx265"):
            args += ["-level", self.level]
        if self.video_bitrate:
            maxrate = self.max_bitrate or self.video_bitrate
            bufsize = self.buffer_size or _double_rate(maxrate)
            args += ["-b:v", self.video_bitrate, "-maxrate", maxrate, "-bufsize", bufsize]
            if codec in ("libx264", "libx265"):
                # 固定码率模式下 CRF 会压制 b:v，干脆去掉
                args = [a for i, a in enumerate(args)
                        if not (a == "-crf" or (i and args[i - 1] == "-crf"))]
        if self.gop > 0:
            args += ["-g", str(int(self.gop))]
        return args




def _double_rate(rate: str) -> str:
    """``"8M"`` -> ``"16M"`` (buffer = 2x maxrate, the usual choice)."""
    text = str(rate or "").strip()
    if not text:
        return text
    unit = text[-1] if text[-1] in "kKmM" else ""
    number = text[:-1] if unit else text
    try:
        return f"{float(number) * 2:g}{unit}"
    except ValueError:
        return text


#: 分辨率**不设上限**：剪映把 2K/4K 锁在会员里，这里是白给的。
#: 预设只是常用档位，任意尺寸用 ``--width/--height`` 或在代码里 new ExportPreset。
PRESETS = {
    "720p30": ExportPreset(1280, 720, 30.0),
    "1080p30": ExportPreset(1920, 1080, 30.0),
    "1080p60": ExportPreset(1920, 1080, 60.0),
    "2k30": ExportPreset(2560, 1440, 30.0, preset="slow"),
    "2k60": ExportPreset(2560, 1440, 60.0, preset="slow"),
    "vertical1080": ExportPreset(1080, 1920, 30.0),
    "square1080": ExportPreset(1080, 1080, 30.0),
    "8k30": ExportPreset(7680, 4320, 30.0, preset="slow"),
    "4k30": ExportPreset(3840, 2160, 30.0, preset="slow"),
    "4k60": ExportPreset(3840, 2160, 60.0, preset="slow"),
}

#: 剪映导出面板那种「画质档位」：在基础预设上叠码率
#: 剪映实测码率阶梯（Mbps，按分辨率高度），见 EXPORT-PARAMS.md §6
BITRATE_LADDER = {
    4320: 59.5,   # 8K
    2160: 24.0,   # 4K
    1440: 15.7,   # 2K
    1080: 10.2,   # 1080p
    720: 7.7,     # 720p
    480: 4.6,     # 480p
}

#: 剪映实测 GOP：150 帧（5 秒 @30fps）
DEFAULT_GOP = 150


def ladder_bitrate(height: int) -> str:
    """按目标高度返回码率字符串（如 ``"10.2M"``）；没有匹配就返回空串。"""
    if not height:
        return ""
    best = None
    for h, mbps in sorted(BITRATE_LADDER.items()):
        if height <= h:
            best = (h, mbps)
            break
    if best is None:
        best = (max(BITRATE_LADDER), BITRATE_LADDER[max(BITRATE_LADDER)])
    return f"{best[1]:g}M"


QUALITY_TIERS = {
    "推荐": {"crf": 20, "video_bitrate": ""},
    "更高": {"crf": 16, "video_bitrate": ""},
    "高码率": {"crf": 18, "video_bitrate": "12M", "preset": "slow"},
    "小体积": {"crf": 26, "video_bitrate": ""},
}


def with_quality(preset: ExportPreset, tier: str) -> ExportPreset:
    """Return a copy of ``preset`` with a quality tier applied."""
    tier_opts = QUALITY_TIERS.get(tier)
    if not tier_opts:
        raise KeyError(f"没有这个画质档位: {tier}")
    data = {f: getattr(preset, f) for f in preset.__dataclass_fields__}
    data.update(tier_opts)
    # 档位没显式给码率时，按剪映实测阶梯套用（同一分辨率同一档位得到接近的体积）
    if not data.get("video_bitrate"):
        data["video_bitrate"] = ladder_bitrate(int(data.get("height") or 0))
    if not data.get("gop"):
        data["gop"] = DEFAULT_GOP
    return ExportPreset(**data)


# --------------------------------------------------------------------------- #
# codec selection
# --------------------------------------------------------------------------- #
_CODEC_FALLBACK = ["libx264", "h264_nvenc", "h264_mf", "h264_qsv", "h264_amf", "mpeg4"]
_cached_codec: Optional[str] = None


#: 明确要求这些编码器时，先检查本机有没有（剪映也是先探测再选路，见 ve_hw_check.ini）
_HARDWARE_CODECS = {"h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv",
                    "h264_mf", "h264_amf", "h264_videotoolbox"}


def pick_video_codec(preferred: str = "auto") -> str:
    global _cached_codec
    if preferred and preferred != "auto":
        if preferred in _HARDWARE_CODECS:
            try:
                available = set(list_encoders())
            except Exception:                      # noqa: BLE001
                available = set()
            if available and preferred not in available:
                raise ValueError(
                    f"本机 FFmpeg 没有 {preferred}（硬件编码器不可用）。"
                    f"可用：{', '.join(sorted(n for n in available if n.startswith(('h264','hevc')))) or '无'}"
                    f"；可改用 auto 或 libx264。")
        return preferred
    if _cached_codec:
        return _cached_codec
    available = set(list_encoders())
    for cand in _CODEC_FALLBACK:
        if cand in available:
            _cached_codec = cand
            return cand
    _cached_codec = "mpeg4"
    return _cached_codec


def _smoke_test(codec: str, preset: "ExportPreset") -> bool:
    """Encode two black frames to /dev/null to prove the encoder really works.

    A codec can be *listed* and still fail at runtime (no GPU, wrong driver),
    which is exactly the situation on machines where ``h264_nvenc`` is compiled
    in but there is no NVIDIA device.
    """
    import subprocess as sp

    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        "64x64",
        "-r",
        "1",
        "-i",
        "-",
        "-frames:v",
        "2",
        "-an",
    ]
    cmd += preset.encode_args(codec)
    cmd += ["-pix_fmt", preset.pix_fmt, "-f", "null", "-"]
    try:
        proc = sp.Popen(
            cmd,
            stdin=sp.PIPE,
            stdout=sp.DEVNULL,
            stderr=sp.PIPE,
            creationflags=_creationflags(),
        )
        frame = bytes(64 * 64 * 3)
        assert proc.stdin is not None
        proc.stdin.write(frame * 2)
        proc.stdin.close()
        code = proc.wait(timeout=60)
        return code == 0
    except Exception:
        return False
    finally:
        # close the stderr pipe explicitly, otherwise Python warns about it
        if proc.stderr is not None:
            try:
                proc.stderr.close()
            except Exception:
                pass


def resolve_codec(preferred: str, preset: "ExportPreset") -> str:
    """Return the first codec that is present *and* actually encodes."""
    global _cached_codec
    if preferred and preferred != "auto":
        if _smoke_test(preferred, preset):
            return preferred
        raise ExportError(f"指定的编码器不可用: {preferred}")
    if _cached_codec and _smoke_test(_cached_codec, preset):
        return _cached_codec
    available = set(list_encoders())
    for cand in _CODEC_FALLBACK:
        if cand not in available:
            continue
        if _smoke_test(cand, preset):
            _cached_codec = cand
            return cand
    available_any = sorted(c for c in available if c.startswith(("h26", "mpeg4", "ffv1", "mjpeg")))
    if available_any:
        if _smoke_test(available_any[0], preset):
            _cached_codec = available_any[0]
            return available_any[0]
    raise ExportError("没有可用的视频编码器，请安装带 libx264 的 FFmpeg")


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
class ExportError(RuntimeError):
    pass


def _render_block(renderer: Renderer, start: int, count: int) -> bytes:
    """Render one contiguous range; contiguous keeps the FFmpeg reader sequential."""
    out = bytearray()
    for index in range(start, start + count):
        out += renderer.render_frame(index).tobytes()
    return bytes(out)


def _threaded_blocks(project: Project, preset: ExportPreset, total: int, workers: int,
                     block: Optional[int] = None):
    """Yield ``(start, count, bytes)`` blocks rendered by ``workers`` threads.

    Threads, not processes: rendering is numpy-heavy (the GIL is released inside
    the big array ops), and on Windows a ``spawn`` pool would re-execute the
    caller's ``__main__`` unless it is guarded - a foot-gun a library should not
    impose.  Measured here: 222 frames, 4 threads, 2.6x.
    """
    from concurrent.futures import ThreadPoolExecutor

    block = block or max(4, min(16, total // max(1, workers * 2) or 4))
    renderers = [Renderer(project, preset.width, preset.height, preset.fps)
                 for _ in range(workers)]
    tasks = [(s, min(block, total - s)) for s in range(0, total, block)]
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            window = []
            pending = list(tasks)
            # keep at most workers+2 blocks in flight so memory stays bounded
            while pending or window:
                while pending and len(window) < workers + 2:
                    s, c = pending.pop(0)
                    r = renderers[len(window) % workers]
                    window.append((s, c, pool.submit(_render_block, r, s, c)))
                if window:
                    s, c, fut = window.pop(0)
                    yield s, c, fut.result()
    finally:
        for r in renderers:
            r.close()


def missing_media(project) -> list:
    """返回引用了但磁盘上不存在的素材路径（排序去重）。"""
    missing = []
    seen = set()
    for mat in getattr(project, "materials", {}).values():
        try:
            path = mat.get("path") if hasattr(mat, "get") else getattr(mat, "path", "")
        except Exception:                              # noqa: BLE001
            path = ""
        if not path:
            continue
        if not isinstance(path, str):
            path = str(path)
        if path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path):
            continue
        # 只有真正被片段引用的素材才算问题
        if any(mat is project.material(seg.material_id)
               for track in project.tracks for seg in track.segments
               if hasattr(seg, "material_id")):
            missing.append(path)
    return sorted(missing)


def check_export_target(out_path: str) -> None:
    """导出路径的基本校验：目录存在、可写。

    Windows 上"目录不存在"和"没权限"是最常见的两种失败，先说清楚比让 ffmpeg 报错好。
    """
    folder = os.path.dirname(os.path.abspath(out_path)) or "."
    if not os.path.isdir(folder):
        raise ExportError(f"输出目录不存在：{folder}")
    if not os.access(folder, os.W_OK):
        raise ExportError(f"输出目录不可写（可能只读或没有权限）：{folder}")
    stem = os.path.splitext(os.path.basename(out_path))[0]
    if not stem or set(stem) <= {"."}:
        raise ExportError(f"输出文件名不能为空：{out_path}")


def check_exportable(project) -> None:
    """导出前的硬性检查：有引用但缺失的素材就抛 ExportError（带文件名）。"""
    missing = missing_media(project)
    if not missing:
        return
    shown = "、".join(os.path.basename(p) for p in missing[:3])
    more = f" 等 {len(missing)} 个文件" if len(missing) > 3 else ""
    raise ExportError(f"素材缺失，无法导出：{shown}{more}（请还原文件或重新链接）")


def export_gif(project, out_path: str, width: int = 480, fps: float = 12.0,
               progress=None) -> str:
    """把工程导出成 GIF（尺寸/帧率可调，画布比例保持不变）。"""
    from .render import Renderer

    check_export_target(out_path)
    exe = ffmpeg_path()
    if not exe:
        raise ExportError("找不到 ffmpeg")
    canvas_w = int(getattr(project.canvas, "width", 0) or 1920)
    canvas_h = int(getattr(project.canvas, "height", 0) or 1080)
    width = max(16, int(width))
    height = max(2, int(round(width * canvas_h / max(1, canvas_w))))
    if height % 2:
        height += 1
    fps = max(1.0, min(30.0, float(fps)))

    if not any(getattr(track, "segments", None) for track in project.tracks):
        raise ExportError("项目为空，没有可导出的内容")
    renderer = Renderer(project, width, height, fps)
    total = renderer.frame_count
    if total <= 0:
        renderer.close()
        raise ExportError("项目为空，没有可导出的内容")

    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
           "-r", f"{fps:g}", "-i", "-",
           "-vf", "split[a][b];[a]palettegen[p];[b][p]paletteuse",
           "-loop", "0", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for index in range(total):
            frame = renderer.render_frame(index)
            proc.stdin.write(frame.astype("uint8").tobytes())
            if progress and index % max(1, total // 20) == 0:
                progress(index / total, f"GIF {index + 1}/{total}")
    finally:
        try:
            proc.stdin.close()
        except Exception:                              # noqa: BLE001
            pass
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        code = proc.wait()
        renderer.close()
    if code != 0 or not os.path.isfile(out_path):
        raise ExportError(f"GIF 导出失败：{err.strip()[:200]}")
    if progress:
        progress(1.0, "完成")
    return out_path


def export_audio_only(project, out_path: str, preset=None, progress=None) -> str:
    """把工程的音频混音导出成 m4a（AAC）。没有声音时报错。"""
    from .audio import write_audio_mix

    check_export_target(out_path)
    total_us = int(getattr(project, "duration_us", 0) or 0)
    if total_us <= 0:
        # 只有音频轨时 duration_us 会是 0，按片段终点自己算
        ends = [int(seg.target_timerange.start) + int(seg.target_timerange.duration)
                for track in project.tracks for seg in track.segments]
        total_us = max(ends) if ends else 0
    if total_us <= 0:
        raise ExportError("项目为空，没有可导出的内容")
    bitrate = getattr(preset, "audio_bitrate", "192k") if preset is not None else "192k"
    tmp_dir = tempfile.mkdtemp(prefix="freecut-audio-")
    try:
        mix = os.path.join(tmp_dir, "mix.wav")
        if not write_audio_mix(project, mix, total_us, bitrate):
            raise ExportError("工程里没有音频，无法只导出声音")
        exe = ffmpeg_path()
        if not exe:
            raise ExportError("找不到 ffmpeg")
        if progress:
            progress(0.5, "编码音频")
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", mix,
               "-c:a", "aac", "-b:a", str(bitrate), out_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.isfile(out_path):
            raise ExportError(f"音频导出失败：{(result.stderr or '').strip()[:200]}")
        if progress:
            progress(1.0, "完成")
        return out_path
    finally:
        shutil.rmtree(tmp_dir, True)


def export(
    project: Project,
    out_path: str,
    preset: Optional[ExportPreset] = None,
    progress: Optional[ProgressFn] = None,
    overwrite: bool = True,
    jobs: Optional[int] = None,
) -> str:
    if preset is None:
        # 没指定预设时跟随工程画布：1280x720 的工程不该被默默导成 1080p。
        # 显式传 PRESETS[...] 仍然按预设来。
        preset = ExportPreset(width=int(project.canvas.width or 1920),
                              height=int(project.canvas.height or 1080),
                              fps=float(project.fps or 30.0))
    if os.path.exists(out_path) and not overwrite:
        raise ExportError(f"文件已存在: {out_path}")
    check_exportable(project)
    check_export_target(out_path)

    payload = _Progress(progress)
    started = time.time()
    payload(0.0, "准备渲染器")

    renderer = Renderer(project, preset.width, preset.height, preset.fps)
    total = renderer.frame_count
    if total <= 0:
        renderer.close()
        raise ExportError("项目为空，没有可导出的内容")

    codec = resolve_codec(preset.video_codec, preset)
    payload(0.02, f"编码器: {codec}")

    if jobs is None:
        cpus = os.cpu_count() or 1
        jobs_effective = max(1, min(cpus - 1, 4))
        if total < 30:          # too few frames to pay for the extra renderers
            jobs_effective = 1
    else:
        jobs_effective = max(1, int(jobs))

    tmp_dir = tempfile.mkdtemp(prefix="freecut-")
    silent_video = os.path.join(tmp_dir, "video-only." + preset.container)
    audio_path = os.path.join(tmp_dir, "mix.wav")
    try:
        cmd = [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{preset.width}x{preset.height}",
            "-r",
            f"{preset.fps:g}",
            "-i",
            "-",
            "-an",
        ]
        cmd += preset.encode_args(codec)
        cmd += ["-pix_fmt", preset.pix_fmt, "-movflags", "+faststart", silent_video]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=_creationflags(),
        )
        try:
            if jobs_effective > 1:
                payload(0.03, f"并行渲染：{jobs_effective} 个进程")
                frame_size = preset.width * preset.height * 3
                written = 0
                for start, count, blob in _threaded_blocks(project, preset, total, jobs_effective):
                    if proc.stdin is None:
                        break
                    proc.stdin.write(blob)
                    written += count
                    if written % 5 < count or written == total:
                        payload(0.05 + 0.80 * written / total, f"编码 {written}/{total} 帧")
            else:
                for i, f in renderer.frames(0, total):
                    if proc.stdin is None:
                        break
                    proc.stdin.write(f.tobytes())
                    if i % 5 == 0 or i == total - 1:
                        payload(0.05 + 0.80 * (i + 1) / total, f"编码第 {i + 1}/{total} 帧")
            if proc.stdin is not None:
                proc.stdin.close()
        finally:
            err = proc.stderr.read() if proc.stderr else b""
            if proc.stderr is not None:
                proc.stderr.close()
            code = proc.wait()
        if code != 0:
            raise ExportError(
                "FFmpeg 编码失败:\n" + err.decode("utf-8", "replace")[-2000:]
            )
        payload(0.88, "混合音频")
        total_us = project.duration() or renderer.duration_us
        has_audio = write_audio_mix(project, audio_path, total_us, preset.audio_bitrate)
        if not has_audio:
            silent_wav(audio_path, total_us)

        payload(0.93, "封装输出")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        mux = [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            silent_video,
            "-i",
            audio_path,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            preset.audio_bitrate,
            "-ac",
            str(max(1, int(preset.audio_channels))),
            "-shortest",
            "-movflags",
            "+faststart" if preset.faststart else "-frag_keyframe+empty_moov",
            out_path,
        ]
        _run(mux, timeout=3600)
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise ExportError("封装失败，输出文件为空")
        payload(1.0, f"完成，用时 {time.time() - started:.1f}s")
        return out_path
    finally:
        renderer.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def export_stills(project: Project, count: int = 1, out_dir: str = ".") -> List[str]:
    """Render a few frames as PNG for quick visual checks."""
    from PIL import Image

    os.makedirs(out_dir, exist_ok=True)
    r = Renderer(project)
    paths: List[str] = []
    try:
        n = r.frame_count
        for k in range(count):
            idx = int((k + 1) * n / (count + 1)) if count > 1 else 0
            frame = r.render_frame(min(idx, n - 1))
            p = os.path.join(out_dir, f"frame_{k:03d}.png")
            Image.fromarray(frame).save(p)
            paths.append(p)
    finally:
        r.close()
    return paths


class _Progress:
    def __init__(self, fn: Optional[ProgressFn]):
        self.fn = fn

    def __call__(self, frac: float, msg: str) -> None:
        if self.fn is not None:
            try:
                self.fn(float(frac), msg)
            except Exception:
                pass


def export_many(
    project: Project,
    targets: Sequence[Tuple[str, ExportPreset]],
    progress: Optional[ProgressFn] = None,
    jobs: Optional[int] = None,
    overwrite: bool = True,
) -> List[str]:
    """Render **once** and encode the same frames to several outputs.

    Two 720p exports used to cost two full renders; here the frame is rendered a
    single time at the largest target size and each encoder scales it down in
    FFmpeg (``-vf scale``), which is C-speed.

    ``targets`` is ``[(path, preset), …]``; returns the written paths.
    """
    if not targets:
        raise ExportError("没有指定任何输出")
    targets = list(targets)
    for path, _preset in targets:
        if os.path.exists(path) and not overwrite:
            raise ExportError(f"文件已存在: {path}")

    payload = _Progress(progress)
    started = time.time()

    # 渲染尺寸取所有目标里最大的那个（同宽高比时最清晰）
    master = max((p for _path, p in targets), key=lambda p: p.width * p.height)
    render_preset = ExportPreset(width=master.width, height=master.height, fps=master.fps)
    payload(0.0, "准备渲染器")
    renderer = Renderer(project, render_preset.width, render_preset.height, render_preset.fps)
    total = renderer.frame_count
    renderer.close()
    if total <= 0:
        raise ExportError("项目为空，没有可导出的内容")

    if jobs is None:
        cpus = os.cpu_count() or 1
        jobs_effective = max(1, min(cpus - 1, 4))
        if total < 30:
            jobs_effective = 1
    else:
        jobs_effective = max(1, int(jobs))

    tmp_dir = tempfile.mkdtemp(prefix="freecut-multi-")
    outputs: List[str] = []
    procs: List[Tuple[subprocess.Popen, ExportPreset, str]] = []
    try:
        for index, (path, preset) in enumerate(targets):
            codec = resolve_codec(preset.video_codec, preset)
            silent = os.path.join(tmp_dir, f"v{index}.{preset.container}")
            cmd = [
                ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", f"{render_preset.width}x{render_preset.height}",
                "-r", f"{render_preset.fps:g}",
                "-i", "-", "-an",
            ]
            if (preset.width, preset.height) != (render_preset.width, render_preset.height):
                cmd += ["-vf", f"scale={preset.width}:{preset.height}:flags=lanczos"]
            cmd += preset.encode_args(codec)
            cmd += ["-pix_fmt", preset.pix_fmt, silent]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, creationflags=_creationflags(),
            )
            procs.append((proc, preset, silent))

        payload(0.03, f"渲染 {total} 帧 → {len(procs)} 路输出")
        try:
            if jobs_effective > 1 and len(procs) == 1 and total >= 30:
                for _start, _count, blob in _threaded_blocks(
                        project, render_preset, total, jobs_effective):
                    if procs[0][0].stdin is None:
                        break
                    procs[0][0].stdin.write(blob)
            else:
                r = Renderer(project, render_preset.width, render_preset.height, render_preset.fps)
                try:
                    for i, frame in r.frames(0, total):
                        for proc, _preset, _silent in procs:
                            if proc.stdin is not None:
                                proc.stdin.write(frame.tobytes())
                        if i % 5 == 0 or i == total - 1:
                            payload(0.05 + 0.75 * (i + 1) / total,
                                    f"渲染 {i + 1}/{total} 帧")
                finally:
                    r.close()
            for proc, _preset, _silent in procs:
                if proc.stdin is not None:
                    proc.stdin.close()
        finally:
            for proc, _preset, _silent in procs:
                err = proc.stderr.read() if proc.stderr else b""
                if proc.stderr is not None:
                    proc.stderr.close()
                if proc.wait() != 0:
                    raise ExportError("FFmpeg 编码失败:\n" +
                                      err.decode("utf-8", "replace")[-1500:])

        payload(0.85, "混音")
        total_us = project.duration()
        mix_wav = os.path.join(tmp_dir, "mix.wav")
        has_audio = write_audio_mix(project, mix_wav, total_us, targets[0][1].audio_bitrate)
        if not has_audio:
            silent_wav(mix_wav, total_us)

        for index, ((path, preset), (_proc, _p, silent)) in enumerate(zip(targets, procs)):
            payload(0.88 + 0.1 * (index + 1) / len(targets), f"封装 {os.path.basename(path)}")
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            mux = [
                ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                "-i", silent, "-i", mix_wav,
                "-c:v", "copy", "-c:a", preset.audio_codec,
                "-b:a", preset.audio_bitrate, "-ac", str(max(1, int(preset.audio_channels))),
                "-shortest", "-movflags", "+faststart", path,
            ]
            _run(mux, timeout=3600)
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                raise ExportError(f"封装失败: {path}")
            outputs.append(path)

        payload(1.0, f"完成，用时 {time.time() - started:.1f}s")
        return outputs
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

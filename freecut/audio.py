"""Audio mixing.

An audio graph is compiled to a single FFmpeg ``filter_complex`` program:

    [i:a] atrim=start:end, asetpts=PTS-STARTPTS,
          atempo=speed, volume=v, afade=in/out, adelay=start  [aN];
    ...
    [a0][a1]... amix=inputs=N:normalize=0[aout]

``amix`` with ``normalize=0`` is used so that laying a music bed under a clip
does not halve the dialogue, matching how a manual NLE mix behaves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .media import ffmpeg_path, probe
from .model import AudioMaterial, Project, Segment, VideoMaterial, new_id


@dataclass
class AudioClip:
    """One contribution to the final mix."""

    path: str
    source_start_us: int
    duration_us: int
    timeline_start_us: int
    volume: float = 1.0
    speed: float = 1.0
    fade_in_us: int = 0
    fade_out_us: int = 0
    muted: bool = False
    has_audio: bool = True
    #: 0 = off, 1 = strong; maps to FFmpeg ``afftdn`` (noise floor + reduction)
    denoise: float = 0.0
    #: playback-rate multiplier used for pitch shifting; 1.0 = untouched.
    #: The duration is restored with ``atempo`` so 变声 does not change timing.
    pitch: float = 1.0
    #: low / mid / high shelf gain in dB (``equalizer`` bands at 120/1k/6k Hz)
    eq: Tuple[float, float, float] = (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- #
# collecting clips
# --------------------------------------------------------------------------- #
def _fade_durations(project: Project, seg: Segment) -> Tuple[int, int]:
    for ref in project.refs_of(seg):
        if ref.type == "audio_fade":
            return (
                int(ref.get("fade_in_duration", 0) or 0),
                int(ref.get("fade_out_duration", 0) or 0),
            )
    return 0, 0


def _atempo_chain(speed: float) -> List[float]:
    """FFmpeg's ``atempo`` accepts 0.5..2.0 per instance; chain for extremes."""
    if speed <= 0 or abs(speed - 1.0) < 1e-6:
        return []
    s = float(speed)
    out: List[float] = []
    while s > 2.0:
        out.append(2.0)
        s /= 2.0
    while s < 0.5:
        out.append(0.5)
        s /= 0.5
    out.append(s)
    return out


def _audio_effects(project: Project, seg: Segment) -> Tuple[float, float, Tuple[float, float, float]]:
    """Read ``audio_effect`` (denoise / pitch / EQ) attached to a segment."""
    for ref in project.refs_of(seg):
        if ref.type == "audio_effect":
            eq = ref.get("eq") or (0.0, 0.0, 0.0)
            if isinstance(eq, dict):
                eq = (eq.get("low", 0.0), eq.get("mid", 0.0), eq.get("high", 0.0))
            try:
                vals = tuple(float(x) for x in list(eq)[:3])  # type: ignore[arg-type]
            except (TypeError, ValueError):
                vals = (0.0, 0.0, 0.0)
            while len(vals) < 3:
                vals = vals + (0.0,)
            return (
                float(ref.get("denoise", 0.0) or 0.0),
                float(ref.get("pitch", 1.0) or 1.0),
                vals,  # type: ignore[return-value]
            )
    return 0.0, 1.0, (0.0, 0.0, 0.0)


def collect_clips(project: Project) -> List[AudioClip]:
    clips: List[AudioClip] = []
    for track in project.tracks:
        if track.type not in ("audio", "video"):
            continue
        for seg in track.sorted_segments():
            if not seg.visible:
                continue
            mat = project.material(seg.material_id)
            path = getattr(mat, "path", "") if mat is not None else ""
            if not path or not os.path.isfile(path):
                continue
            if mat is None:
                continue
            kind = getattr(mat, "type", "")
            # 图片/文本没有音轨。以前这里默认 has_audio=True，结果只要工程里有
            # 一张图，混音就会因为 ffmpeg 找不到音频流而整体失败、导出变成静音。
            if kind not in ("audio", "video"):
                continue
            has_audio = True
            if isinstance(mat, AudioMaterial) or kind == "audio":
                has_audio = True
            else:
                has_audio = bool(mat.get("has_audio", True))
            if not has_audio:
                continue
            fi, fo = _fade_durations(project, seg)
            denoise, pitch, eq = _audio_effects(project, seg)
            clips.append(
                AudioClip(
                    path=path,
                    source_start_us=seg.source_timerange.start,
                    duration_us=seg.target_timerange.duration,
                    timeline_start_us=seg.target_timerange.start,
                    volume=float(seg.volume),
                    speed=float(seg.speed),
                    fade_in_us=fi,
                    fade_out_us=fo,
                    muted=float(seg.volume) <= 1e-6,
                    denoise=denoise,
                    pitch=pitch,
                    eq=eq,
                )
            )
    return clips


# --------------------------------------------------------------------------- #
# filter graph
# --------------------------------------------------------------------------- #
def build_filter_complex(clips: List[AudioClip], total_us: int) -> Tuple[List[str], str, str]:
    """Return (extra_input_args, filter_complex, output_label)."""
    active = [c for c in clips if not c.muted and c.duration_us > 0]
    if not active:
        return [], "", ""
    inputs: List[str] = []
    chains: List[str] = []
    labels: List[str] = []
    for i, c in enumerate(active):
        idx = i
        inputs.extend(["-i", c.path])
        ss = c.source_start_us / 1_000_000.0
        dur = max(0.001, c.duration_us / 1_000_000.0 * max(c.speed, 1e-6))
        filters = [
            f"atrim=start={ss:.6f}:duration={dur:.6f}",
            "asetpts=PTS-STARTPTS",
            "aresample=48000",
            "aformat=sample_fmts=fltp:channel_layouts=stereo",
        ]
        for tempo in _atempo_chain(c.speed):
            filters.append(f"atempo={tempo:.6f}")
        # 降噪：afftdn 的噪声底(-80..-20 dB)与降噪量(0.01..97 dB)都受滑块控制
        if c.denoise > 0.001:
            d = float(min(1.0, max(0.0, c.denoise)))
            nr = 6.0 + 24.0 * d
            nf = -25.0 - 10.0 * d
            filters.append(f"afftdn=nr={nr:.1f}:nf={nf:.1f}")
        # 变声：改采样率把音高移调，再用 atempo 把时长拉回来（时长不变）
        if abs(c.pitch - 1.0) > 1e-3:
            p = float(min(2.0, max(0.5, c.pitch)))
            # asetrate 的 r 是整数参数，不能写表达式，先算好
            filters.append(f"asetrate={int(round(48000 * p))}")
            filters.append("aresample=48000")
            filters.append(f"atempo={1.0 / p:.6f}")
        # 三段均衡（剪映的"均衡"面板是低/中/高三段）
        low, mid, high = (float(v) for v in c.eq)
        if abs(low) > 0.01:
            filters.append(f"equalizer=f=120:width_type=h:width=120:g={low:.2f}")
        if abs(mid) > 0.01:
            filters.append(f"equalizer=f=1000:width_type=h:width=900:g={mid:.2f}")
        if abs(high) > 0.01:
            filters.append(f"equalizer=f=6000:width_type=h:width=3000:g={high:.2f}")
        vol = max(0.0, float(c.volume))
        if abs(vol - 1.0) > 1e-6:
            filters.append(f"volume={vol:.4f}")
        out_dur = c.duration_us / 1_000_000.0
        if c.fade_in_us > 0:
            filters.append(f"afade=t=in:st=0:d={min(c.fade_in_us / 1e6, out_dur):.4f}")
        if c.fade_out_us > 0:
            start = max(0.0, out_dur - c.fade_out_us / 1e6)
            filters.append(f"afade=t=out:st={start:.4f}:d={c.fade_out_us / 1e6:.4f}")
        delay_ms = int(round(c.timeline_start_us / 1000.0))
        if delay_ms > 0:
            filters.append(f"adelay={delay_ms}|{delay_ms}")
        label = f"a{i}"
        # 注意：输入标签后面不能有逗号，[0:a],atrim=… 是非法语法
        chains.append(f"[{idx}:a]" + ",".join(filters) + f"[{label}]")
        labels.append(f"[{label}]")

    if len(labels) == 1:
        mixed = labels[0]
    else:
        chains.append(
            "".join(labels)
            + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0[mix]"
        )
        mixed = "[mix]"
    total_s = max(0.01, total_us / 1_000_000.0)
    chains.append(f"{mixed}apad,atrim=0:{total_s:.6f}[aout]")
    return inputs, ";".join(chains), "[aout]"


# --------------------------------------------------------------------------- #
# writing the mix
# --------------------------------------------------------------------------- #
def write_audio_mix(project: Project, out_path: str, total_us: int, bitrate: str = "192k") -> bool:
    """Render the project's audio to ``out_path`` (wav).  Returns False if silent."""
    from .media import _creationflags, _run

    clips = collect_clips(project)
    inputs, graph, label = build_filter_complex(clips, total_us)
    if not graph:
        return False
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y"]
    cmd += inputs
    cmd += ["-filter_complex", graph, "-map", label, "-c:a", "pcm_s16le", out_path]
    _run(cmd, timeout=3600)
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 44


def silent_wav(out_path: str, total_us: int, sample_rate: int = 48000, channels: int = 2) -> None:
    """Write silence so the mux always has an audio track."""
    import wave

    n = max(1, int(total_us / 1e6 * sample_rate))
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * channels * n)

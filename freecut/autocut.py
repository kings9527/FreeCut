"""Automatic cutting: scene detection and beat sync, fully offline.

JianyingPro does this with cloud models (``SceneEditDetection`` ships a
``compress_shot_detect`` graph with a 96x96 backbone, sliding window 7 and a
0.35 post-process threshold - see ``Resources/SceneEditDetection/config.json``).
FreeCut does the same job with pure signal processing so it works offline and
is auditable:

* **scene cuts** - 32-bin luma histograms on 96x54 frames, chi-square distance,
  robust threshold (median + k·MAD), minimum gap;
* **beats** - spectral-flux onset envelope, autocorrelation tempo estimate,
  peak picking with a tempo-aware minimum spacing.

The detectors are deterministic and their parameters are exposed.
"""

from __future__ import annotations

import math
import os
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .media import _creationflags, ffmpeg_path, probe

# --------------------------------------------------------------------------- #
# frame sampling
# --------------------------------------------------------------------------- #
def sample_frames(path: str, fps: float = 4.0, width: int = 96, height: int = 54) -> np.ndarray:
    """Decode ``path`` to a low-resolution RGB stack at ``fps``."""
    cmd = [
        ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", path,
        "-vf", f"fps={fps},scale={width}:{height}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          creationflags=_creationflags())
    n = width * height * 3
    count = len(proc.stdout) // n
    if count == 0:
        return np.zeros((0, height, width, 3), dtype=np.uint8)
    return np.frombuffer(proc.stdout[: count * n], dtype=np.uint8).reshape(count, height, width, 3)


def _histograms(frames: np.ndarray, bins: int = 32) -> np.ndarray:
    luma = frames[..., 0] * 0.299 + frames[..., 1] * 0.587 + frames[..., 2] * 0.114
    out = np.zeros((frames.shape[0], bins), dtype=np.float32)
    for i in range(frames.shape[0]):
        h, _ = np.histogram(luma[i], bins=bins, range=(0.0, 255.0))
        s = h.sum()
        out[i] = h / s if s else h
    return out


@dataclass
class SceneCut:
    time_s: float
    score: float


def detect_scene_cuts(
    path: str,
    fps: float = 6.0,
    threshold_k: float = 6.0,
    min_gap_s: float = 0.35,
    min_duration_s: float = 0.4,
) -> List[SceneCut]:
    """Return the detected cuts (excluding the 0 s mark)."""
    frames = sample_frames(path, fps=fps)
    if frames.shape[0] < 3:
        return []
    hists = _histograms(frames)
    mean_rgb = frames.reshape(frames.shape[0], -1, 3).mean(axis=1) / 255.0

    d_hist = np.zeros(len(hists), dtype=np.float32)
    d_hist[1:] = np.abs(hists[1:] - hists[:-1]).sum(axis=1)
    d_rgb = np.zeros(len(mean_rgb), dtype=np.float32)
    d_rgb[1:] = np.linalg.norm(mean_rgb[1:] - mean_rgb[:-1], axis=1)

    score = d_hist * 1.0 + d_rgb * 2.0
    body = score[1:]
    if body.size == 0:
        return []
    med = float(np.median(body))
    mad = float(np.median(np.abs(body - med))) or 1e-6
    thresh = med + threshold_k * mad
    thresh = max(thresh, float(body.mean()) * 1.5)

    cuts: List[SceneCut] = []
    last_t = -1e9
    for i in range(1, len(score)):
        if score[i] < thresh:
            continue
        t = i / fps
        if t - last_t < min_gap_s:
            if cuts and score[i] > cuts[-1].score:
                cuts[-1] = SceneCut(t, float(score[i]))
                last_t = t
            continue
        cuts.append(SceneCut(t, float(score[i])))
        last_t = t
    return [c for c in cuts if c.time_s >= min_duration_s]


# --------------------------------------------------------------------------- #
# audio / beats
# --------------------------------------------------------------------------- #
def load_audio(path: str, sample_rate: int = 22050) -> np.ndarray:
    cmd = [
        ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", path,
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          creationflags=_creationflags())
    if not proc.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def onset_envelope(samples: np.ndarray, sample_rate: int = 22050,
                   n_fft: int = 1024, hop: int = 256) -> Tuple[np.ndarray, float]:
    """Spectral-flux onset strength and the hop duration in seconds."""
    hop_s = hop / float(sample_rate)
    if samples.size < n_fft * 2:
        return np.zeros(0, dtype=np.float32), hop_s
    window = np.hanning(n_fft).astype(np.float32)
    n_frames = 1 + (samples.size - n_fft) // hop
    mags = np.empty((n_frames, n_fft // 2 + 1), dtype=np.float32)
    for i in range(n_frames):
        seg = samples[i * hop: i * hop + n_fft] * window
        spec = np.fft.rfft(seg)
        mags[i] = np.abs(spec)
    flux = np.zeros(n_frames, dtype=np.float32)
    flux[1:] = np.maximum(0.0, np.diff(mags, axis=0)).sum(axis=1)
    if flux.max() > 0:
        flux = flux / flux.max()
    return flux, hop_s


def estimate_tempo(env: np.ndarray, hop_s: float,
                   min_bpm: float = 60.0, max_bpm: float = 190.0) -> float:
    if env.size < 8:
        return 0.0
    x = env - env.mean()
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    lo = max(1, int((60.0 / max_bpm) / hop_s))
    hi = min(len(ac) - 1, int((60.0 / min_bpm) / hop_s))
    if hi <= lo:
        return 0.0
    lag = lo + int(np.argmax(ac[lo:hi]))
    return 60.0 / (lag * hop_s)


def detect_beats(path: str, min_gap_ratio: float = 0.5) -> List[float]:
    """Return beat times in seconds."""
    samples = load_audio(path)
    env, hop_s = onset_envelope(samples)
    if env.size == 0:
        return []
    bpm = estimate_tempo(env, hop_s)
    period = 60.0 / bpm if bpm > 0 else 0.5
    min_gap = max(hop_s * 2, period * min_gap_ratio)

    med = float(np.median(env))
    mad = float(np.median(np.abs(env - med))) or 1e-6
    thresh = med + 1.2 * mad

    beats: List[float] = []
    last = -1e9
    for i in range(1, env.size - 1):
        if env[i] < thresh:
            continue
        if env[i] < env[i - 1] or env[i] < env[i + 1]:
            continue
        t = i * hop_s
        if t - last < min_gap:
            continue
        beats.append(t)
        last = t
    return beats


# --------------------------------------------------------------------------- #
# building a project
# --------------------------------------------------------------------------- #
@dataclass
class AutoCutOptions:
    min_clip_s: float = 0.8
    max_clip_s: float = 6.0
    transition: Optional[str] = None
    transition_s: float = 0.3
    max_clips: int = 60
    fps: float = 30.0


def _segments_from_cuts(duration_s: float, cuts: Sequence[float],
                        opts: AutoCutOptions) -> List[Tuple[float, float]]:
    """Turn cut times into (start, dur) pairs, clamped to the option limits."""
    points = [0.0] + [c for c in cuts if 0.0 < c < duration_s] + [duration_s]
    out: List[Tuple[float, float]] = []
    for a, b in zip(points, points[1:]):
        dur = b - a
        if dur < opts.min_clip_s:
            # merge a too-short shot into the previous one
            if out:
                pa, pd = out[-1]
                out[-1] = (pa, pd + dur)
            continue
        while dur > opts.max_clip_s:
            out.append((a, opts.max_clip_s))
            a += opts.max_clip_s
            dur -= opts.max_clip_s
        if dur >= opts.min_clip_s:
            out.append((a, dur))
    return out[: opts.max_clips]


def build_scene_project(media_paths: Sequence[str], opts: Optional[AutoCutOptions] = None,
                        name: str = "自动剪辑（分镜）"):
    """Cut every source at its scene changes and lay the shots end to end."""
    from . import builder

    opts = opts or AutoCutOptions()
    infos = [probe(p) for p in media_paths]
    if not infos:
        raise ValueError("没有可用的素材")
    first = infos[0]
    project = builder.new_project(first.width or 1920, first.height or 1080, opts.fps, name=name)
    track = builder.track_of(project, "video")

    cursor = 0
    for path, info in zip(media_paths, infos):
        cuts = [c.time_s for c in detect_scene_cuts(path)]
        pieces = _segments_from_cuts(info.duration_s, cuts, opts)
        for start_s, dur_s in pieces:
            seg = builder.add_video(
                project, path,
                start=cursor,
                source_in=int(start_s * 1_000_000),
                duration=int(dur_s * 1_000_000),
            )
            cursor += int(dur_s * 1_000_000)
            if opts.transition and len(track.segments) > 1:
                builder.add_transition(project, seg, name=opts.transition,
                                       duration_us=int(opts.transition_s * 1_000_000))
    return project


def build_beat_project(media_paths: Sequence[str], music_path: str,
                       opts: Optional[AutoCutOptions] = None,
                       beats_per_cut: int = 2,
                       name: str = "自动剪辑（踩点）"):
    """Cut the sources on the music's beats."""
    from . import builder

    opts = opts or AutoCutOptions()
    beats = detect_beats(music_path)
    if len(beats) < 2:
        raise ValueError("没有检测到节拍，请换一段节奏更明显的音乐")
    marks = beats[:: max(1, beats_per_cut)]
    if marks[0] > 0:
        marks = [0.0] + marks

    infos = [probe(p) for p in media_paths]
    first = infos[0]
    project = builder.new_project(first.width or 1920, first.height or 1080, opts.fps, name=name)
    track = builder.track_of(project, "video")

    cursor = 0
    src_index = 0
    src_offset = 0
    for i in range(len(marks) - 1):
        dur_s = marks[i + 1] - marks[i]
        if dur_s < 0.12:
            continue
        path = media_paths[src_index % len(media_paths)]
        info = infos[src_index % len(infos)]
        avail = max(0.0, info.duration_s - src_offset)
        if avail < dur_s + 0.05:
            src_index += 1
            src_offset = 0.0
            path = media_paths[src_index % len(media_paths)]
            info = infos[src_index % len(infos)]
            avail = info.duration_s
        take = min(dur_s, avail)
        if take <= 0.05:
            continue
        builder.add_video(
            project, path,
            start=cursor,
            source_in=int(src_offset * 1_000_000),
            duration=int(take * 1_000_000),
        )
        cursor += int(take * 1_000_000)
        src_offset += take
        if opts.transition and len(track.segments) > 1:
            builder.add_transition(project, track.segments[-1], name=opts.transition,
                                   duration_us=int(opts.transition_s * 1_000_000))
        if len(track.segments) >= opts.max_clips:
            break

    total_s = cursor / 1_000_000
    builder.add_audio(project, music_path, start=0, duration=int(total_s * 1_000_000))
    return project

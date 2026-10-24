"""Audio waveform peaks for the timeline.

Decoding a whole file at 8 kHz mono is cheap (1 minute ≈ 0.5 MB) and lets the
timeline draw a real envelope instead of a flat block.  Results are cached per
(path, bucket count) because the timeline redraws on every mouse move.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from typing import Optional, Tuple

import numpy as np

from .media import _creationflags, ffmpeg_path

_SAMPLE_RATE = 8000


def _decode_mono(path: str, sample_rate: int = _SAMPLE_RATE) -> np.ndarray:
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", path,
         "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True, creationflags=_creationflags(),
    )
    if not proc.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def peaks(path: str, buckets: int = 256) -> np.ndarray:
    """Max-abs amplitude per bucket over the whole file (shape ``(buckets,)``)."""
    return np.asarray(_peaks_cached(os.path.abspath(path), int(buckets)), dtype=np.float32)


@lru_cache(maxsize=64)
def _peaks_cached(path: str, buckets: int) -> Tuple[float, ...]:
    if not os.path.isfile(path):
        return tuple([0.0] * buckets)
    samples = _decode_mono(path)
    if samples.size == 0:
        return tuple([0.0] * buckets)
    usable = samples.size // buckets * buckets
    if usable == 0:
        return tuple([0.0] * buckets)
    blocks = samples[:usable].reshape(buckets, -1)
    out = np.abs(blocks).max(axis=1)
    peak = float(out.max()) or 1.0
    return tuple((out / peak).tolist())      # normalised so the envelope fills the row


def duration_s(path: str) -> float:
    return _decode_mono(path).size / float(_SAMPLE_RATE)


def clear_cache() -> None:
    _peaks_cached.cache_clear()

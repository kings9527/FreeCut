"""防抖（近似）：估计帧间平移并反向补偿。

剪映的防抖是"云端/本地模型"做全局运动估计；这里用的是最朴素的办法：

1. 把每帧转灰度、降采样；
2. 在 ±``max_shift`` 像素范围内穷举平移，找和上一帧差异最小的位置；
3. 把这条抖动轨迹做滑动平均，得到"想要的平滑轨迹"；
4. 用「平滑轨迹 − 原始轨迹」作为补偿量，逐帧平移。

它只处理**平移**（不做旋转/缩放/卷帘），而且会裁掉边缘（平移后空出来的
部分用黑边或放大填补）。所以名字里带"近似"。
"""

from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .media import ffmpeg_path


def _luma(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        return (frame[..., 0] * 0.299 + frame[..., 1] * 0.587 + frame[..., 2] * 0.114)
    return frame.astype(np.float32)


def estimate_shifts(frames: Sequence[np.ndarray], max_shift: int = 12,
                    step: int = 2, downscale: int = 2) -> List[Tuple[int, int]]:
    """Estimate per-frame translation relative to the previous frame.

    Returns one ``(dx, dy)`` per frame; the first frame is always ``(0, 0)``.
    """
    shifts: List[Tuple[int, int]] = []
    prev = None
    for frame in frames:
        small = _luma(frame)
        if downscale > 1:
            small = small[::downscale, ::downscale]
        if prev is None or prev.shape != small.shape:
            shifts.append((0, 0))
            prev = small
            continue
        limit = max(1, int(max_shift) // max(1, downscale))
        # 固定一个内窗：把边框 limit 像素排除在外，这样 roll 的环绕伪影不会参与比较
        m = limit + 2
        h, w = small.shape
        if h <= 2 * m or w <= 2 * m:
            shifts.append((0, 0))
            prev = small
            continue
        ref = prev[m:h - m, m:w - m]
        best = (0, 0)
        best_err = None
        for dy in range(-limit, limit + 1, max(1, step)):
            for dx in range(-limit, limit + 1, max(1, step)):
                moved = np.roll(np.roll(small, dy, axis=0), dx, axis=1)[m:h - m, m:w - m]
                err = float(np.abs(moved - ref).mean())
                if best_err is None or err < best_err:
                    best_err = err
                    best = (dx, dy)
        shifts.append((best[0] * max(1, downscale), best[1] * max(1, downscale)))
        prev = small
    return shifts


def estimate_rotations(frames: Sequence[np.ndarray], max_deg: float = 3.0,
                       step_deg: float = 0.25) -> List[float]:
    """Estimate per-frame roll (degrees) by直接搜索角度。

    第一版用"上下两条带水平位移之差"反推角度，量化到 2 像素后基本没精度
    （±1.19° 一跳）。改成**直接试角度**：把当前帧按候选角度转一下，
    和上一帧比误差，取误差最小的那个。25 个候选 × 全帧差，320×180 也就几毫秒。
    """
    if len(frames) < 2:
        return [0.0] * len(frames)
    angles: List[float] = [0.0]
    candidates = [(-max_deg) + step_deg * i
                  for i in range(int(round(2 * max_deg / step_deg)) + 1)]
    prev = _luma(frames[0])
    for i in range(1, len(frames)):
        cur = _luma(frames[i])
        if cur.shape != prev.shape:
            angles.append(0.0)
            prev = cur
            continue
        # 只比中心区域，避开旋转后露出的黑角
        h, w = cur.shape
        my, mx = int(h * 0.2), int(w * 0.2)
        ref = prev[my:h - my, mx:w - mx]
        best_angle, best_err = 0.0, None
        for deg in candidates:
            moved = rotate_frame(frames[i], deg)
            small = _luma(moved)[my:h - my, mx:w - mx]
            err = float(np.abs(small - ref).mean())
            if best_err is None or err < best_err:
                best_err, best_angle = err, float(deg)
        angles.append(best_angle)
        prev = cur
    return angles


def _band_shift(cur: np.ndarray, prev: np.ndarray, max_shift: int) -> Tuple[int, int]:
    """Same windowed search as :func:`estimate_shifts`, for one horizontal band."""
    m = max_shift + 2
    h, w = cur.shape
    if h <= 2 * m or w <= 2 * m:
        return (0, 0)
    ref = prev[m:h - m, m:w - m]
    best, best_err = (0, 0), None
    for dy in range(-max_shift, max_shift + 1, 2):
        for dx in range(-max_shift, max_shift + 1, 2):
            moved = np.roll(np.roll(cur, dy, axis=0), dx, axis=1)[m:h - m, m:w - m]
            err = float(np.abs(moved - ref).mean())
            if best_err is None or err < best_err:
                best_err, best = err, (dx, dy)
    return best


def rotate_frame(frame: np.ndarray, degrees: float) -> np.ndarray:
    """Rotate about the centre; exposed corners go black."""
    if abs(degrees) < 1e-3:
        return frame
    from PIL import Image

    img = Image.fromarray(frame)
    out = img.rotate(float(degrees), resample=Image.BILINEAR, expand=False)
    return np.asarray(out)


def smooth_path(shifts: Sequence[Tuple[int, int]], radius: int = 6) -> List[Tuple[float, float]]:
    """Running mean over the cumulative path — this is what we want the camera to do."""
    if not shifts:
        return []
    cum_x, cum_y = 0.0, 0.0
    path = []
    for dx, dy in shifts:
        cum_x += dx
        cum_y += dy
        path.append((cum_x, cum_y))
    out: List[Tuple[float, float]] = []
    for i in range(len(path)):
        lo = max(0, i - radius)
        hi = min(len(path), i + radius + 1)
        window = path[lo:hi]
        out.append((sum(p[0] for p in window) / len(window),
                    sum(p[1] for p in window) / len(window)))
    return out


def compensation(real_path: Sequence[Tuple[float, float]],
                 smooth: Sequence[Tuple[float, float]],
                 limit: int = 40) -> List[Tuple[int, int]]:
    """Per-frame correction = 平滑轨迹 − 实际轨迹，并夹在 ±limit 内。"""
    corrections: List[Tuple[int, int]] = []
    for (rx, ry), (sx, sy) in zip(real_path, smooth):
        dx = int(round(sx - rx))
        dy = int(round(sy - ry))
        corrections.append((max(-limit, min(limit, dx)),
                            max(-limit, min(limit, dy))))
    return corrections


def apply_shift(frame: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Shift a frame; exposed area becomes black (no scaling, no interpolation)."""
    out = np.zeros_like(frame)
    h, w = frame.shape[:2]
    ys, ye = max(0, dy), min(h, h + dy)
    xs, xe = max(0, dx), min(w, w + dx)
    if ye <= ys or xe <= xs:
        return out
    out[ys:ye, xs:xe] = frame[ys - dy:ye - dy, xs - dx:xe - dx]
    return out


def _read_frames(path: str, width: int = 0, height: int = 0) -> Tuple[List[np.ndarray], float, int, int]:
    from .media import probe

    info = probe(path)
    w = width or int(info.width)
    h = height or int(info.height)
    cmd = [
        ffmpeg_path(), "-v", "error", "-i", path,
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    raw = proc.stdout
    frame_bytes = w * h * 3
    count = len(raw) // frame_bytes
    frames = [np.frombuffer(raw[i * frame_bytes:(i + 1) * frame_bytes], dtype=np.uint8)
              .reshape(h, w, 3) for i in range(count)]
    return frames, float(info.fps or 30.0), w, h


def stabilize_video(src: str, dst: str, max_shift: int = 12, radius: int = 8,
                    limit: int = 40, max_frames: int = 900,
                    keep_if_worse: bool = False, rotation: bool = False) -> dict:
    """Stabilise a clip by estimating and compensating translation only."""
    frames, fps, w, h = _read_frames(src)
    if not frames:
        raise RuntimeError("没有读到任何帧")
    frames = frames[:max_frames]
    angles: List[float] = [0.0] * len(frames)
    rot_fix: List[float] = [0.0] * len(frames)
    if rotation:
        angles = estimate_rotations(frames, max_deg=3.0)
        path_rot = smooth_path([(a, 0.0) for a in angles], radius=0)
        smooth_rot = smooth_path([(a, 0.0) for a in angles], radius=radius)
        # 同样是"反向施加"：估计值是要转多少才能对齐上一帧
        rot_fix = [-(smooth_rot[i][0] - path_rot[i][0]) for i in range(len(angles))]

    shifts = estimate_shifts(frames, max_shift=max_shift)
    real_path = smooth_path(shifts, radius=0)      # radius=0 就是不平滑的原始轨迹
    smoothed = smooth_path(shifts, radius=radius)
    corrections = compensation(real_path, smoothed, limit=limit)

    out_path = os.path.abspath(dst)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    # 剪映自带的那份 ffmpeg 没有 libx264，只有硬件编码器；借 export 的探测逻辑
    from .export import pick_video_codec

    codec = pick_video_codec("auto")
    cmd = [
        ffmpeg_path(), "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps}",
        "-i", "-", "-i", src,
        "-map", "0:v:0", "-map", "1:a:0?", "-c:v", codec,
        "-pix_fmt", "yuv420p", "-shortest", out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    assert proc.stdin is not None
    # 估计出来的位移是"相机往哪动了"，补偿要**反向**施加（实测 sign=-1 才降抖动）
    for i, (frame, (dx, dy)) in enumerate(zip(frames, corrections)):
        out_frame = apply_shift(frame, -dx, -dy)
        if rotation and abs(rot_fix[i]) > 0.01:
            out_frame = rotate_frame(out_frame, rot_fix[i])
        proc.stdin.write(out_frame.tobytes())
    proc.stdin.close()
    proc.wait()
    # 诚实的度量：把处理后的片子重新估一遍抖动，和原片比
    def _jitter(path: str) -> float:
        got, _fps, _w, _h = _read_frames(path)
        est = estimate_shifts(got[:max_frames], max_shift=max_shift)
        if len(est) < 3:
            return 0.0
        arr = np.asarray(est, dtype=float)
        return float(np.std(np.diff(arr, axis=0)))

    def _rot_jitter(path: str) -> float:
        if not rotation:
            return 0.0
        got, _fps, _w, _h = _read_frames(path)
        est = estimate_rotations(got[:max_frames])
        if len(est) < 3:
            return 0.0
        return float(np.std(np.diff(np.asarray(est, dtype=float))))

    before = _jitter(src)
    try:
        after = _jitter(out_path)
    except Exception:
        after = before
    if after >= before * 0.95 and not keep_if_worse:
        # 变差了就别留在硬盘上骗人
        try:
            os.remove(out_path)
        except OSError:
            pass
        return {"frames": len(frames), "fps": fps, "size": (w, h),
                "jitter_before": before, "jitter_after": after,
                "correction": 0.0, "out": "", "rejected": True}

    return {"frames": len(frames), "fps": fps, "size": (w, h),
            "rotation": rotation,
            "rot_before": _rot_jitter(src) if rotation else 0.0,
            "rot_after": _rot_jitter(out_path) if rotation else 0.0,
            "jitter_before": before, "jitter_after": after,
            "correction": float(np.mean([abs(c[0]) + abs(c[1]) for c in corrections]))
            if corrections else 0.0,
            "out": out_path}

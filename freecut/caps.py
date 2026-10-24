"""硬件/编码能力探测（对应剪映的 `ve_hw_check.ini` 那套做法）。

剪映启动时对每块 GPU 探测 D3D11 硬解矩阵并缓存（证据见
`docs/measurements/ENGINE-ROUTE-CONFIG.md` §2）。我们的解码/编码走 FFmpeg，
对应的探测就是：能用的 hwaccel、能用的编码器、以及它们各自的实际参数。

探测结果只用来**做决定**（选哪条编码路径），不假设一定成功：导出仍然有软编兜底。
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Dict, List

from .media import _creationflags, ffmpeg_path


def _run(args: List[str]) -> str:
    try:
        proc = subprocess.run([ffmpeg_path()] + args, capture_output=True,
                              timeout=20, creationflags=_creationflags())
    except Exception:                                  # noqa: BLE001
        return ""
    text = (proc.stdout or b"").decode("utf-8", "replace")
    return text if proc.returncode == 0 else ""


def hwaccels() -> List[str]:
    """FFmpeg 支持的硬件解码后端（cuda / d3d11va / qsv …）。"""
    out = _run(["-hide_banner", "-hwaccels"])
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    return [line for line in lines if line.lower() != "hardware acceleration methods:"]


def encoders() -> List[str]:
    out = _run(["-hide_banner", "-encoders"])
    names = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V") or parts[:1] == ["V"]:
            names.append(parts[1])
        elif len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.append(parts[1])
    return sorted(set(names))


def probe() -> Dict[str, object]:
    """一次完整探测（结果可以存盘/打印，对应剪映的 ve_hw_check.ini）。"""
    import platform

    enc = encoders()
    accel = hwaccels()
    preferred = "libx264"
    for candidate in ("h264_nvenc", "h264_qsv", "h264_mf", "h264_amf"):
        if candidate in enc:
            preferred = candidate
            break
    return {
        "os": platform.platform(),
        "ffmpeg": ffmpeg_path(),
        "hwaccels": accel,
        "has_cuda": "cuda" in accel,
        "has_d3d11va": "d3d11va" in accel,
        "encoders": {
            name: name in enc
            for name in ("libx264", "libx265", "h264_nvenc", "hevc_nvenc",
                         "h264_qsv", "hevc_qsv", "h264_mf", "h264_amf")
        },
        "preferred_h264_encoder": preferred,
    }


def save(path: str, data: Dict[str, object] = None) -> str:
    payload = data if data is not None else probe()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def load(path: str) -> Dict[str, object]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def summary(data: Dict[str, object] = None) -> str:
    """一行摘要，给 CLI / 状态栏用。"""
    info = data if data is not None else probe()
    accel = ",".join(info.get("hwaccels") or []) or "无"
    enc = (info.get("encoders") or {})
    have = [name for name, ok in enc.items() if ok]
    return (f"hwaccels={accel}；可用编码器={','.join(have) or '无'}；"
            f"默认 H.264={info.get('preferred_h264_encoder')}")


if __name__ == "__main__":                             # pragma: no cover
    print(summary())

"""窗口状态持久化：面板宽度这类"上次关掉时是这样"的东西。

放在 ``%APPDATA%\\FreeCut\\ui.json``（可用 ``FREECUT_UI_STATE`` 覆盖），
读写都容忍损坏——读坏了就当没有，写失败也不该影响正常使用。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_WIDTHS = (232, 0, 360)      # 左 / 中 / 右；中间为 0 表示"剩下的都给中栏"


def state_path() -> Path:
    override = os.environ.get("FREECUT_UI_STATE")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "FreeCut" / "ui.json"


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the saved state; missing or broken files yield ``{}``."""
    target = Path(path) if path else state_path()
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save(state: Dict[str, Any], path: Optional[Path] = None) -> bool:
    """Write the state atomically; returns whether it landed."""
    target = Path(path) if path else state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
        return True
    except Exception:
        return False


def pane_widths(state: Optional[Dict[str, Any]] = None) -> tuple:
    """Left/right pane widths from ``state``, clamped to something usable."""
    data = state if state is not None else load()
    raw = data.get("pane_widths") or []
    out = []
    for index, fallback in enumerate(DEFAULT_WIDTHS):
        try:
            value = int(raw[index])
        except Exception:
            value = fallback
        out.append(value)
    left = max(140, min(out[0], 640))
    right = max(220, min(out[2], 720))
    return left, out[1], right

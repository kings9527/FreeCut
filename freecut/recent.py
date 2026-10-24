"""最近打开的工程列表（存在用户目录，最多 10 条）。"""

from __future__ import annotations

import json
import os
from typing import List

MAX_ITEMS = 10


def store_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "FreeCut", "recent.json")


def load() -> List[str]:
    path = store_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if isinstance(item, str)][:MAX_ITEMS]


def add(path: str) -> List[str]:
    """把 path 放到最前面（去重、限量、只保留仍存在的文件）。"""
    items = [p for p in load() if p != path]
    items.insert(0, path)
    items = [p for p in items if os.path.isfile(p)][:MAX_ITEMS]
    target = store_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as fh:
            json.dump(items, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return items

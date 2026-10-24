"""Effect catalogs (transitions, blend modes, masks).

The identifiers are the ones CapCut / JianyingPro uses for its *cloud* resources
(``effect_id`` / ``resource_id`` / md5 of the downloaded package).  They are not
invented here: they were catalogued by the community project
``GuanYixuan/pyJianYingDraft`` (Apache-2.0) by observing drafts the app wrote,
and this repository converts that catalog to JSON (see
``docs/tools/build_catalogs.py``).  See ``data/NOTICE.md``.

FreeCut only needs the *names* and *default durations* to behave like the app;
the actual effect package still has to be rendered by us (or, for a draft we
export, resolved by the app).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    path = os.path.join(DATA_DIR, name)
    if not os.path.isfile(path):
        return {"count": 0, "items": []}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def transitions() -> List[dict]:
    return _load("transitions.json").get("items", [])


def mix_modes() -> List[dict]:
    return _load("mix_modes.json").get("items", [])


def masks() -> List[dict]:
    return _load("masks.json").get("items", [])


def filters() -> List[dict]:
    return _load("filters.json").get("items", [])


def animations() -> List[dict]:
    return _load("animations.json").get("items", [])


@lru_cache(maxsize=None)
def transition_by_name() -> Dict[str, dict]:
    return {item["name"]: item for item in transitions()}


@lru_cache(maxsize=None)
def transition_by_resource_id() -> Dict[str, dict]:
    return {item["resource_id"]: item for item in transitions()}


def find_transition(name: Optional[str] = None, resource_id: Optional[str] = None) -> Optional[dict]:
    if resource_id:
        hit = transition_by_resource_id().get(str(resource_id))
        if hit:
            return hit
    if name:
        return transition_by_name().get(name)
    return None


def transition_default_duration_us(name: str, fallback: int = 500_000) -> int:
    item = transition_by_name().get(name)
    if not item:
        return fallback
    return int(round(float(item.get("duration_s", 0.5)) * 1_000_000))


def search_transitions(keyword: str, limit: int = 40) -> List[dict]:
    kw = (keyword or "").strip()
    if not kw:
        return transitions()[:limit]
    return [t for t in transitions() if kw in t["name"]][:limit]


@lru_cache(maxsize=None)
def filter_by_name() -> Dict[str, dict]:
    return {item["name"]: item for item in filters()}


@lru_cache(maxsize=None)
def filter_by_resource_id() -> Dict[str, dict]:
    return {item["resource_id"]: item for item in filters()}


def find_filter(name: Optional[str] = None, resource_id: Optional[str] = None) -> Optional[dict]:
    if resource_id:
        hit = filter_by_resource_id().get(str(resource_id))
        if hit:
            return hit
    if name:
        return filter_by_name().get(name)
    return None


def search_filters(keyword: str, limit: int = 40, free_only: bool = False) -> List[dict]:
    kw = (keyword or "").strip()
    pool = filters()
    if free_only:
        pool = [f for f in pool if not f.get("vip")]
    if not kw:
        return pool[:limit]
    return [f for f in pool if kw in f["name"]][:limit]


def mask_by_name(name: Optional[str]) -> Optional[dict]:
    if not name:
        return None
    for item in masks():
        if item["name"] == name:
            return item
    return None


@lru_cache(maxsize=None)
def animation_index() -> Dict[tuple, dict]:
    return {(item["category"], item["name"]): item for item in animations()}


def find_animation(name: str, category: str = "video_in") -> Optional[dict]:
    """``category`` is one of video_in / video_out / text_in / text_out / text_loop."""
    idx = animation_index()
    hit = idx.get((category, name))
    if hit:
        return hit
    for (cat, nm), item in idx.items():
        if nm == name:
            return item
    return None


def search_animations(keyword: str, category: Optional[str] = None, limit: int = 40) -> List[dict]:
    pool = animations()
    if category:
        pool = [a for a in pool if a["category"] == category]
    if not keyword:
        return pool[:limit]
    return [a for a in pool if keyword in a["name"]][:limit]


def stats() -> dict:
    return {
        "transitions": len(transitions()),
        "mix_modes": len(mix_modes()),
        "masks": len(masks()),
        "filters": len(filters()),
        "animations": len(animations()),
    }

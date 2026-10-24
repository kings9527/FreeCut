"""A local LUT filter library.

FreeCut renders filters as 3D LUTs (see :func:`freecut.color.apply_lut3d`).
This module finds the ``.cube`` files that already exist on the machine and
presents them as a browsable, content-addressed library:

* JianyingPro's cloud-filter cache:
  ``%LOCALAPPDATA%\\JianyingPro\\User Data\\Cache\\effect\\<rid>\\<pkg-md5>\\AmazingFilter_lut\\*.cube``
  - when the app caches a filter package, its LUT lands here, so the same files
  the app uses become available to FreeCut;
* the app's bundled ``Resources\\DefaultAdjustBundle\\**`` copies;
* any folder the user points at (``FREECUT_LUT_DIRS``, ``;``-separated), which is
  how a downloaded LUT pack gets picked up.

Files are deduplicated by content hash because the same LUT is often cached
several times under different package ids.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

_DEFAULT_CACHE = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "JianyingPro", "User Data", "Cache", "effect"
)
_DEFAULT_APP = os.path.join(os.environ.get("LOCALAPPDATA", ""), "JianyingPro", "apps")


@dataclass
class LutEntry:
    name: str
    path: str
    md5: str
    size: int
    source: str = ""
    #: names from a sibling ``config.json`` / ``extra.json`` when present
    aliases: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "md5": self.md5,
            "size": self.size,
            "source": self.source,
            "aliases": list(self.aliases),
        }


def _file_md5(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _package_name(cube_path: str) -> Optional[str]:
    """Read the display name from a neighbouring package descriptor."""
    folder = os.path.dirname(cube_path)
    for _ in range(4):
        folder = os.path.dirname(folder)
        for fname in ("config.json", "extra.json", "luma-meta.json", "lua-meta.json"):
            cand = os.path.join(folder, fname)
            if not os.path.isfile(cand):
                continue
            try:
                with open(cand, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            for key in ("name", "effect_name", "display_name"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            eff = data.get("effect")
            if isinstance(eff, dict):
                for key in ("name", "effect_name"):
                    val = eff.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
    return None


def _looks_like_id(name: str) -> bool:
    """Package names are often ``<GUID>-<base64ish>``; useless as a label."""
    import re

    if not name:
        return False
    if re.match(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-", name):
        return True
    return bool(re.fullmatch(r"[0-9A-Fa-f][0-9A-Fa-f-]{30,}", name))


def default_roots() -> List[str]:
    """Directories worth walking (narrow: the app tree is enormous)."""
    roots: List[str] = []
    env = os.environ.get("FREECUT_LUT_DIRS", "")
    for part in env.replace(";", os.pathsep).split(os.pathsep):
        part = part.strip().strip('"')
        if part and os.path.isdir(part):
            roots.append(part)
    # FreeCut 自己生成的 LUT（`freecut lut make`）也要能被扫到
    own = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                       "FreeCut", "luts")
    if os.path.isdir(own) and own not in roots:
        roots.append(own)
    if os.path.isdir(_DEFAULT_CACHE):
        roots.append(_DEFAULT_CACHE)
    if os.path.isdir(_DEFAULT_APP):
        import glob

        for hit in glob.glob(os.path.join(_DEFAULT_APP, "*", "Resources", "DefaultAdjustBundle")):
            if os.path.isdir(hit):
                roots.append(hit)
    return roots


def own_dir() -> str:
    """用户自己的 LUT 目录（导入的滤镜放这里，扫描路径已包含）。"""
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, "FreeCut", "luts")


def import_cube(src: str) -> str:
    """把外部 ``.cube`` 导入本地库，返回落盘路径。

    先完整解析一次（坏文件不进库），重名时自动加 ``-2``、``-3`` 后缀。
    """
    import shutil

    if not str(src).lower().endswith(".cube"):
        raise ValueError("只支持 .cube 文件")
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    from .color import load_cube_lut

    load_cube_lut(src)                       # 解析失败就在这里抛，别污染库

    dest_dir = own_dir()
    os.makedirs(dest_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(src))
    dest = os.path.join(dest_dir, stem + ext)
    index = 2
    while os.path.exists(dest):
        dest = os.path.join(dest_dir, f"{stem}-{index}{ext}")
        index += 1
    shutil.copy2(src, dest)
    return dest


def scan(roots: Optional[Sequence[str]] = None, limit: int = 20000) -> List[LutEntry]:
    """Find every usable ``.cube`` under ``roots`` (deduplicated by content)."""
    roots = list(roots) if roots else default_roots()
    seen: Dict[str, LutEntry] = {}
    scanned = 0
    for root in roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                if not fname.lower().endswith(".cube"):
                    continue
                scanned += 1
                if scanned > limit:
                    break
                full = os.path.join(dirpath, fname)
                try:
                    size = os.path.getsize(full)
                    digest = _file_md5(full)
                except OSError:
                    continue
                if digest in seen:
                    alias = os.path.splitext(fname)[0]
                    if alias not in seen[digest].aliases:
                        seen[digest].aliases.append(alias)
                    continue
                pkg_name = _package_name(full)
                stem = os.path.splitext(fname)[0]
                if pkg_name and not _looks_like_id(pkg_name):
                    name = pkg_name
                else:
                    name = stem
                aliases = []
                for a in (stem, pkg_name):
                    if a and a != name and a not in aliases:
                        aliases.append(a)
                seen[digest] = LutEntry(
                    name=name,
                    path=full,
                    md5=digest,
                    size=size,
                    source=_source_label(dirpath),
                    aliases=aliases,
                )
    return sorted(seen.values(), key=lambda e: (e.source, e.name.lower()))


def _source_label(dirpath: str) -> str:
    low = dirpath.lower()
    if "amazingfilter_lut" in low:
        return "剪映缓存"
    if "defaultadjustbundle" in low:
        return "剪映内置"
    return "本地"


def get(key: str) -> Optional[LutEntry]:
    """Look a LUT up by name, alias, path or md5 prefix."""
    if not key:
        return None
    if os.path.isfile(key):
        try:
            digest = _file_md5(key)
        except OSError:
            return None
        return LutEntry(
            name=os.path.splitext(os.path.basename(key))[0],
            path=os.path.abspath(key),
            md5=digest,
            size=os.path.getsize(key),
            source="手动",
        )
    lib = scan()
    lower = key.lower()
    for entry in lib:
        if entry.name.lower() == lower or entry.md5.startswith(lower):
            return entry
        if any(a.lower() == lower for a in entry.aliases):
            return entry
    for entry in lib:
        haystack = [entry.name.lower()] + [a.lower() for a in entry.aliases]
        if any(lower in h for h in haystack):
            return entry
    return None


_CACHE_FILE = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "FreeCut", "lut-library.json"
)
_CACHE_TTL_S = 6 * 3600


def library(refresh: bool = False, max_age_s: float = _CACHE_TTL_S) -> List[LutEntry]:
    """Scan with a small on-disk cache (a cold scan takes ~10 s)."""
    import time

    if not refresh:
        try:
            age = time.time() - os.path.getmtime(_CACHE_FILE)
            if age < max_age_s:
                with open(_CACHE_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return [LutEntry(**item) for item in data]
        except Exception:
            pass
    entries = scan()
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump([e.to_dict() for e in entries], fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return entries


def summary(entries: Sequence[LutEntry]) -> str:
    if not entries:
        return "（没有找到 LUT）"
    lines = []
    by_source: Dict[str, int] = {}
    for e in entries:
        by_source[e.source] = by_source.get(e.source, 0) + 1
    lines.append("共 {} 个 LUT：".format(len(entries)) +
                 "，".join(f"{k} {v}" for k, v in sorted(by_source.items())))
    for e in entries:
        alias = f"  (别名: {', '.join(e.aliases)})" if e.aliases else ""
        lines.append(f"  [{e.source}] {e.name}  {e.size/1024:.0f} KB  {e.md5[:8]}{alias}")
    return "\n".join(lines)

# -- raw .bin LUTs ---------------------------------------------------------
#: 剪映的某些效果（例如 5013 智能调色里的 `lens_vhdr_image_lut_rgb.bin`）
#: 把 LUT 存成裸字节：`size³ × 3` 字节，每通道 1 字节。
#: 轴序没有文档，所以这里要求调用方**显式声明**布局，不去猜。
LAYOUTS = {
    "interleaved": "每像素 3 字节 (r,g,b)，索引顺序 r,g,b",
    "planar": "先整块 R 平面，再 G，再 B",
}


def load_bin_lut(path: str, size: int, layout: str = "interleaved") -> "np.ndarray":
    """Read a bare ``size³ × 3`` byte LUT into an ``(size,size,size,3)`` array.

    Used for JianyingPro's ``lens_vhdr_image_lut_rgb.bin`` family.  The axis order
    of those files is not documented, so ``layout`` is an explicit choice, not a
    guess — see :data:`LAYOUTS`.
    """
    import numpy as np
    from pathlib import Path as _Path

    if size < 2:
        raise ValueError("LUT 尺寸至少为 2")
    if layout not in LAYOUTS:
        raise ValueError(f"未知布局 {layout!r}，可选: {sorted(LAYOUTS)}")
    data = _Path(path).read_bytes()
    expected = size * size * size * 3
    if len(data) != expected:
        raise ValueError(f"{path} 不是 {size}³×3 的 LUT：{len(data)} B ≠ {expected} B")
    raw = np.frombuffer(data, dtype=np.uint8).astype(np.float32) / 255.0
    if layout == "interleaved":
        return raw.reshape(size, size, size, 3)
    planes = raw.reshape(3, size, size, size)
    return np.stack([planes[0], planes[1], planes[2]], axis=-1)


def bin_lut_size(path: str) -> "Optional[int]":
    """Guess the cube size of a bare LUT file (``n³ × 3`` bytes), or None."""
    from pathlib import Path as _Path

    size = _Path(path).stat().st_size
    if size % 3:
        return None
    cells = size // 3
    root = round(cells ** (1.0 / 3.0))
    return root if root ** 3 == cells else None

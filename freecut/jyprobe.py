"""End-to-end probe: does JianyingPro actually read the draft we wrote?

Everything else we verify is *our* reading of *our* files.  This closes the
loop with the real application: register the draft, launch JianyingPro, and
watch whether it rewrites ``draft_meta_info.json`` — in particular whether the
``tm_duration`` and ``draft_timeline_materials_size_`` it computes match the
project.

Launching the editor is disruptive (it takes over the desktop and stays in the
tray), so this is never called implicitly: ``freecut jianying-probe`` only.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# 不写死任何本机路径：默认从 LOCALAPPDATA 里找最新安装的剪映，
# 草稿目录也走环境变量 / 常见位置；找不到就让调用方传参。
DEFAULT_EXE = ""
DEFAULT_DRAFTS = os.environ.get("JY_DRAFTS") or os.path.join(
    os.path.expanduser("~"), "Documents", "JianyingPro Drafts")


def find_exe() -> Optional[str]:
    """Newest installed ``JianyingPro.exe`` (falls back to the known path)."""
    if os.path.isfile(DEFAULT_EXE):
        return DEFAULT_EXE
    apps = Path(os.environ.get("LOCALAPPDATA", "")) / "JianyingPro" / "apps"
    if not apps.is_dir():
        return None
    found = sorted(apps.rglob("JianyingPro.exe"), key=lambda p: p.stat().st_mtime)
    return str(found[-1]) if found else None


def _digest(path: Path) -> Optional[Dict]:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    return {"size": len(raw), "md5": hashlib.md5(raw).hexdigest()}


def snapshot(draft_dir: str) -> Dict[str, Optional[Dict]]:
    base = Path(draft_dir)
    return {
        "meta": _digest(base / "draft_meta_info.json"),
        "content": _digest(base / "draft_content.json"),
    }


@dataclass
class ProbeResult:
    launched: bool = False
    changed_meta: bool = False
    changed_content: bool = False
    seconds: float = 0.0
    reported_duration_us: Optional[int] = None
    expected_duration_us: Optional[int] = None
    reported_size: Optional[int] = None
    registered_name: Optional[str] = None
    registered_duration_us: Optional[int] = None
    notes: List[str] = field(default_factory=list)

    @property
    def duration_matches(self) -> Optional[bool]:
        """Does the app's own project list agree with the project duration?

        ``None`` means "could not tell" (no registry entry, or no expectation).
        """
        # 0 表示"剪映还没算过这个字段"，不是"算出来不一样"
        if not self.registered_duration_us or not self.expected_duration_us:
            return None
        return abs(self.registered_duration_us - self.expected_duration_us) <= 40_000

    @property
    def parsed_timeline(self) -> bool:
        """Did the app recompute something only the timeline could give it?

        ``draft_timeline_materials_size_`` is the reliable one: we blank it
        before launching, and a value coming back means the app walked our
        media list.  ``tm_duration`` is only refreshed when the project is
        opened and saved inside the editor, so it is reported but not required.
        """
        if self.reported_size:
            return True
        if self.reported_duration_us is None or self.expected_duration_us is None:
            return False
        return abs(self.reported_duration_us - self.expected_duration_us) <= 40_000

    def as_text(self) -> str:
        if not self.launched:
            return "剪映探针：没启动（找不到 JianyingPro.exe）"
        lines = [f"剪映探针：{'通过 ✅' if self.parsed_timeline else '未确认 ❌'}"]
        lines.append(f"  启动后 {self.seconds:.0f} 秒内 meta 被改写: {self.changed_meta}")
        lines.append(f"  草稿正文被改写: {self.changed_content}")
        if self.reported_size:
            lines.append(f"  素材总字节（剪映重新统计）: {self.reported_size:,} "
                         "← 只有解析了素材列表才算得出")
        if self.registered_name and self.registered_duration_us == 0:
            lines.append("  工程列表里的时长还是 0：剪映扫描时不刷新，打开并保存后才会更新")
        match = self.duration_matches
        if match is not None:
            lines.append("  工程列表里的时长与工程一致 ✓" if match
                         else "  ⚠ 工程列表里的时长与工程不一致")
        if self.registered_name:
            lines.append(f"  剪映工程列表里的条目: {self.registered_name}"
                         + (f"，时长 {self.registered_duration_us / 1e6:.3f}s"
                            if self.registered_duration_us else ""))
        if self.reported_duration_us is not None:
            lines.append(f"  剪映记下的时长: {self.reported_duration_us / 1e6:.3f}s"
                         + (f"（工程 {self.expected_duration_us / 1e6:.3f}s）"
                            if self.expected_duration_us else "")
                         + "（打开并保存后才会更新）")
        for note in self.notes:
            lines.append("  · " + note)
        return "\n".join(lines)

    def as_dict(self) -> Dict:
        return {
            "launched": self.launched,
            "changed_meta": self.changed_meta,
            "changed_content": self.changed_content,
            "seconds": round(self.seconds, 1),
            "reported_duration_us": self.reported_duration_us,
            "expected_duration_us": self.expected_duration_us,
            "reported_size": self.reported_size,
            "registered_name": self.registered_name,
            "registered_duration_us": self.registered_duration_us,
            "duration_matches": self.duration_matches,
            "parsed_timeline": self.parsed_timeline,
            "notes": list(self.notes),
        }


def probe(draft_dir: str, expected_duration_us: Optional[int] = None,
          exe: Optional[str] = None, timeout: float = 90.0,
          settle: float = 8.0) -> ProbeResult:
    """Launch JianyingPro, wait for it to touch the draft, then kill it."""
    result = ProbeResult(expected_duration_us=expected_duration_us)
    exe = exe or find_exe()
    if not exe or not os.path.isfile(exe):
        result.notes.append("找不到 JianyingPro.exe")
        return result

    before = snapshot(draft_dir)
    if before["meta"] is None:
        result.notes.append("这个目录里没有 draft_meta_info.json")
        return result

    started = time.time()
    try:
        proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    except Exception as exc:
        result.notes.append(f"启动失败: {exc}")
        return result
    result.launched = True

    after = before
    try:
        while time.time() - started < timeout:
            time.sleep(2.0)
            after = snapshot(draft_dir)
            if after["meta"] != before["meta"]:
                result.changed_meta = True
                result.changed_content = after["content"] != before["content"]
                time.sleep(settle)                       # 让它把文件写完
                after = snapshot(draft_dir)
                break
    finally:
        result.seconds = time.time() - started
        _kill(proc)

    if not result.changed_meta:
        result.notes.append(f"{timeout:.0f} 秒内 meta 没变化：剪映可能没扫描到，或已经在运行")
        return result

    meta_path = Path(draft_dir) / "draft_meta_info.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result.notes.append(f"meta 读不回来: {exc}")
        return result
    dur = meta.get("tm_duration")
    result.reported_duration_us = int(dur) if isinstance(dur, (int, float)) else None
    size = meta.get("draft_timeline_materials_size_")
    result.reported_size = int(size) if isinstance(size, (int, float)) else None
    result.notes.append(f"剪映把 meta 规范化成 {len(meta)} 个字段")
    _remember(draft_dir, result)
    entry = registry_entry(draft_dir)
    if entry is None:
        result.notes.append("剪映工程列表里还没这个草稿（注册后要等它扫描）")
    if entry:
        result.registered_name = str(entry.get("draft_name") or "")
        dur = entry.get("tm_duration")
        result.registered_duration_us = int(dur) if isinstance(dur, (int, float)) else None
    return result


def _kill(proc) -> None:
    """Stop the editor and its tray helper (they share the project's files)."""
    try:
        proc.terminate()
    except Exception:
        pass
    time.sleep(1.0)
    try:
        subprocess.run(["taskkill", "/F", "/IM", "JianyingPro.exe"], capture_output=True, timeout=20)
        subprocess.run(["taskkill", "/F", "/IM", "JianyingProTray.exe"], capture_output=True,
                       timeout=20)
    except Exception:
        pass

def perturb_meta(draft_dir: str) -> Optional[Dict]:
    """Blank out the values JianyingPro recomputes from the timeline.

    If the meta already matches what the app would write, "no change" cannot be
    told apart from "never opened the file".  Setting ``tm_duration`` to 0 makes
    the signal decisive: only an app that parsed the timeline can fill it in.
    """
    path = Path(draft_dir) / "draft_meta_info.json"
    if not path.is_file():
        return None
    original = json.loads(path.read_text(encoding="utf-8"))
    broken = dict(original)
    broken["tm_duration"] = 0
    broken["draft_timeline_materials_size_"] = 0
    path.write_text(json.dumps(broken, ensure_ascii=False, indent=2), encoding="utf-8")
    return original


def restore_meta(draft_dir: str, original: Dict) -> None:
    path = Path(draft_dir) / "draft_meta_info.json"
    path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")

def registry_entry(draft_dir: str) -> Optional[Dict]:
    """Find this draft in JianyingPro's own project list.

    Being listed there (with a name and duration) is secondary evidence: the
    app only enumerates drafts it managed to open.
    """
    reg = Path(os.environ.get("LOCALAPPDATA", "")) / "JianyingPro" / "User Data" / "Projects" \
        / "com.lveditor.draft" / "root_meta_info.json"
    if not reg.is_file():
        return None
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except Exception:
        return None
    target = os.path.normcase(os.path.abspath(draft_dir)).replace("\\", "/")
    for entry in data.get("all_draft_store") or []:
        path = str(entry.get("draft_fold_path") or "")
        if not path:
            continue
        if os.path.normcase(path).replace("\\", "/") == target:
            return entry
    return None

PROBE_RECORD = "freecut_jianying_probe.json"


def _remember(draft_dir: str, result: ProbeResult) -> None:
    """Leave a record so the draft self-check can cite it later."""
    try:
        path = Path(draft_dir) / PROBE_RECORD
        payload = dict(result.as_dict())
        payload["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        # 绑定草稿的"结构指纹"而不是字节：每次导出都会重新生成 id，
        # 字节必然不同，但结构相同就说明记录依然成立。
        sig = structure_signature(draft_dir)
        if sig:
            payload["structure"] = sig
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_record(draft_dir: str, max_age_days: float = 30.0) -> Optional[Dict]:
    """Read the probe record for this draft, or None when absent/stale."""
    path = Path(draft_dir) / PROBE_RECORD
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if max_age_days:
        age = time.time() - path.stat().st_mtime
        if age > max_age_days * 86400:
            return None
    recorded = data.get("structure")
    if recorded:
        current = structure_signature(draft_dir)
        if current and current != recorded:
            return None                       # 草稿改过了，旧记录不算数
    return data


def structure_signature(draft_dir: str) -> Optional[str]:
    """A small fingerprint of the draft's *shape*, not its bytes.

    Two exports of the same project produce different ids, so a byte hash would
    invalidate every record.  Duration + segment counts + material kinds are
    stable and are what a probe result actually depends on.
    """
    path = Path(draft_dir) / "draft_content.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    tracks = data.get("tracks") or []
    parts = [str(data.get("duration")), str(len(tracks))]
    for track in tracks:
        parts.append(f"{track.get('type')}:{len(track.get('segments') or [])}")
    mats = data.get("materials") or {}
    for key in sorted(mats):
        items = mats[key]
        if isinstance(items, list):
            parts.append(f"{key}={len(items)}")
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()

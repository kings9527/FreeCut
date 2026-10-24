"""批处理：一个清单文件描述多个任务，一次跑完。

为什么需要它：目前每个操作都是"打开一个工程 → 做一件事"，做一个系列就得守在
命令行前一条条敲。批量导出/批量校验/批量转草稿是剪辑工作的常态。

设计上的三条坚持：

* **失败不中断**：一个任务炸了，后面的照常跑；最后给一张汇总表。
* **每个任务独立计时**：出问题能看出是哪一个、花了多久。
* **清单是纯 JSON**：不引入新语法，能被程序生成（比如自动剪完接着批量导出）。

清单里可以写变量，避免同一个工程路径抄十遍：

```json
{
  "vars": {"project": "out/showcase.json", "outdir": "out/runs"},
  "jobs": [
    {"id": "a", "action": "render", "project": "{{project}}", "out": "{{outdir}}/a.mp4"}
  ]
}
```

命令行还能覆盖：``freecut batch plan.json --var outdir=out/2026-10``。
变量没定义会直接报错（而不是把 `{{x}}` 写进文件名）。

清单格式：

```json
{
  "jobs": [
    {"id": "subs", "action": "autosub", "media": "voice.wav",
     "project": "out/showcase.json", "out": "out/with_subs.json", "model": "base"},
    {"id": "flat", "action": "flatten", "project": "out/showcase.json",
     "root": "E:/剪映-草稿/JianyingPro Drafts", "name": "展平版"},
    {"id": "demo", "action": "render", "project": "out/showcase.json", "out": "out/batch_demo.mp4"},
    {"id": "draft", "action": "draft", "project": "out/showcase.json", "root": "E:/剪映-草稿/JianyingPro Drafts"},
    {"id": "check", "action": "verify", "media": "out/batch_demo.mp4"},
    {"id": "sheet", "action": "stills", "project": "out/showcase.json", "out": "out/batch_stills", "count": 3}
  ]
}
```
"""

from __future__ import annotations

import json
import os
import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .model import Project

ACTIONS = ("render", "draft", "xml", "verify", "stills", "flatten", "autosub",
           "reverse", "freeze")


class BatchError(Exception):
    """Raised for a malformed plan (not for a failing job)."""


@dataclass
class Job:
    id: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    #: 依赖的任务 id：这些任务成功后本任务才启动（失败则本任务被跳过）
    needs: List[str] = field(default_factory=list)


@dataclass
class JobResult:
    id: str
    action: str
    ok: bool
    seconds: float
    summary: str = ""
    error: str = ""
    skipped: bool = False

    def as_line(self) -> str:
        mark = "SKIP" if self.skipped else ("OK  " if self.ok else "FAIL")
        tail = self.summary if self.ok else self.error
        return f"[{mark}] {self.id:12} {self.action:7} {self.seconds:6.1f}s  {tail}"



_VAR = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


def _substitute(value: Any, variables: Dict[str, str], where: str) -> Any:
    """Replace ``{{name}}`` in strings; unknown names raise BatchError."""
    if isinstance(value, str):
        def repl(match):
            key = match.group(1)
            if key not in variables:
                raise BatchError(f"{where} 用到未定义的变量 {{{{{key}}}}}")
            return str(variables[key])
        return _VAR.sub(repl, value)
    if isinstance(value, list):
        return [_substitute(v, variables, where) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, variables, where) for k, v in value.items()}
    return value


def load_plan(path: str, overrides: Optional[Dict[str, str]] = None) -> List[Job]:
    """Parse a plan file; raises :class:`BatchError` on anything malformed."""
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise BatchError(f"清单不是合法 JSON: {exc}") from exc
    raw = data.get("jobs") if isinstance(data, dict) else data
    variables: Dict[str, str] = {}
    if isinstance(data, dict):
        declared = data.get("vars") or {}
        if not isinstance(declared, dict):
            raise BatchError("vars 必须是对象")
        variables.update({str(k): str(v) for k, v in declared.items()})
    variables.update({str(k): str(v) for k, v in (overrides or {}).items()})
    if not isinstance(raw, list) or not raw:
        raise BatchError("清单里没有 jobs 数组")
    jobs: List[Job] = []
    seen = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise BatchError(f"第 {index + 1} 个任务不是对象")
        action = str(item.get("action") or "").strip()
        if action not in ACTIONS:
            raise BatchError(f"第 {index + 1} 个任务的 action 不认识: {action!r}（可选 {', '.join(ACTIONS)}）")
        jid = str(item.get("id") or f"job{index + 1}")
        if jid in seen:
            raise BatchError(f"任务 id 重复: {jid}")
        seen.add(jid)
        needs = item.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        params = {k: v for k, v in item.items()
                  if k not in ("id", "action", "needs")}
        jobs.append(Job(id=jid, action=action,
                        params=_substitute(params, variables, jid),
                        needs=[str(n) for n in needs]))
    validate_dependencies(jobs)
    return jobs


def _project(params: Dict[str, Any]) -> Project:
    path = params.get("project")
    if not path:
        raise ValueError("缺少 project 路径")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return Project.load(path)


def _run_one(job: Job, default_jobs: Optional[int] = None) -> str:
    """Execute one job and return a one-line summary."""
    if job.action == "render":
        from .export import export

        project = _project(job.params)
        out = job.params.get("out") or "out/batch.mp4"
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        export(project, out, jobs=job.params.get("jobs") or default_jobs)
        return f"→ {out}"

    if job.action == "draft":
        from .jianying_export import save_draft

        project = _project(job.params)
        root = job.params.get("root")
        if not root:
            raise ValueError("缺少 root（剪映草稿目录）")
        warnings: List[str] = []
        path = save_draft(project, root, name=job.params.get("name"), warnings=warnings)
        return f"→ {path}" + (f"（{len(warnings)} 条提示）" if warnings else "")

    if job.action == "xml":
        from .interchange import to_fcp7_xml

        project = _project(job.params)
        out = job.params.get("out") or "out/batch.xml"
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(to_fcp7_xml(project), encoding="utf-8")
        return f"→ {out}"

    if job.action == "verify":
        from .verify import verify

        media = job.params.get("media")
        if not media or not os.path.isfile(media):
            raise FileNotFoundError(media)
        report = verify(media)
        ok = bool(getattr(report, "ok", False))
        if not ok:
            problems = getattr(report, "problems", []) or []
            raise RuntimeError("校验未通过: " + "; ".join(str(p) for p in problems[:3]))
        return f"{media} 通过"

    if job.action == "flatten":
        # 先自己渲成一段片，再把这整段当作一个片段写回草稿：
        # 画面对得上（所见即所得），代价是剪映里只剩一个片段。
        from .export import export
        from .jianying_export import flatten_project, save_draft

        project = _project(job.params)
        root = job.params.get("root")
        if not root:
            raise ValueError("缺少 root（剪映草稿目录）")
        tmp_dir = os.path.join(root, "_freecut_flatten")
        Path(tmp_dir).mkdir(parents=True, exist_ok=True)
        mp4 = os.path.join(tmp_dir, f"batch_{job.id}.mp4")
        # 渲染并行数可写在任务里（实测 640x360 六秒片：1/2/4 → 15.8/12.7/10.5 秒）
        export(project, mp4, jobs=job.params.get("jobs") or default_jobs)
        flat = flatten_project(project, mp4)
        path = save_draft(flat, root, name=job.params.get("name"), warnings=[])
        return f"→ {path}（单片段 {os.path.basename(mp4)}）"

    if job.action == "autosub":
        from . import asr, subtitles

        media = job.params.get("media")
        if not media or not os.path.isfile(media):
            raise FileNotFoundError(media)
        project = _project(job.params) if job.params.get("project") else None
        if project is None:
            from . import builder

            project = builder.new_project(1280, 720, 30, name="批处理字幕")
        data = asr.transcribe(media, model=job.params.get("model") or "base")
        subs = asr.segments_to_subtitles(data)
        if not subs:
            raise RuntimeError("没有识别到语音")
        subtitles.add_subtitles(project, subs)
        out = job.params.get("out") or "out/batch_autosub.json"
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        project.save(out)
        return f"{len(subs)} 条字幕 → {out}"

    if job.action == "reverse":
        # 把一段素材倒放后写入 out；不碰工程结构
        from .retime import reverse_video

        src = job.params.get("media")
        if not src or not os.path.isfile(src):
            raise FileNotFoundError(src)
        out = job.params.get("out") or f"out/reverse_{Path(src).stem}.mp4"
        reverse_video(src, out, keep_audio=bool(job.params.get("keep_audio", True)))
        return f"→ {out}"

    if job.action == "freeze":
        # 定格：把某一帧当静图插进工程并另存
        from .retime import add_freeze_clip

        project = _project(job.params)
        at_us = int(float(job.params.get("at", 0)) * 1_000_000)
        dur_us = int(float(job.params.get("duration", 1.0)) * 1_000_000)
        out_dir = job.params.get("out") or "out/freeze"
        add_freeze_clip(project, at_us, dur_us, out_dir,
                        track_index=int(job.params.get("track", 0)))
        out = job.params.get("project_out") or os.path.join(out_dir, "project.json")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        project.save(out)
        return f"→ {out}（定格 {job.params.get('duration', 1.0)}s）"

    if job.action == "stills":
        from .export import export_stills

        project = _project(job.params)
        out = job.params.get("out") or "out/batch_stills"
        count = int(job.params.get("count") or 3)
        paths = export_stills(project, count=count, out_dir=out)
        return f"→ {len(paths)} 张剧照"

    raise ValueError(f"未实现的 action: {job.action}")



def resolve_workers(value: Any) -> int:
    """``"auto"`` / None / int -> a concrete worker count.

    ``auto`` uses at most 4 and never more than half the cores: every worker holds
    decoded frames, so oversubscribing costs memory without going faster.
    """
    if value is None:
        return 1
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("auto", ""):
            cores = os.cpu_count() or 2
            return max(1, min(4, cores // 2 or 1))
        try:
            value = int(text)
        except ValueError:
            raise BatchError(f"--jobs 只接受数字或 auto，收到 {value!r}")
    return max(1, int(value))

def validate_dependencies(jobs: List[Job]) -> None:
    """Raise :class:`BatchError` for unknown or circular ``needs``."""
    ids = {j.id for j in jobs}
    for job in jobs:
        for need in job.needs:
            if need not in ids:
                raise BatchError(f"{job.id} 依赖了不存在的任务 {need}")
            if need == job.id:
                raise BatchError(f"{job.id} 依赖了自己")
    # 环检测（Kahn）
    remaining = {j.id: set(j.needs) for j in jobs}
    while remaining:
        ready = [jid for jid, needs in remaining.items() if not needs]
        if not ready:
            raise BatchError("依赖有环: " + "、".join(sorted(remaining)))
        for jid in ready:
            remaining.pop(jid)
        for needs in remaining.values():
            needs.difference_update(ready)


def _execute(job: Job, default_jobs: Optional[int] = None) -> JobResult:
    started = time.time()
    try:
        summary = _run_one(job, default_jobs=default_jobs)
        return JobResult(job.id, job.action, True, time.time() - started, summary=summary)
    except Exception as exc:                      # 逐条兜住，继续跑后面的
        detail = f"{type(exc).__name__}: {exc}"
        if os.environ.get("FREECUT_BATCH_TRACE"):
            detail += "\n" + traceback.format_exc()
        return JobResult(job.id, job.action, False, time.time() - started, error=detail)


def run(jobs: List[Job], on_result: Optional[Callable[[JobResult], None]] = None,
        workers: int = 1) -> List[JobResult]:
    """Run the jobs; a failing job never stops unrelated ones.

    ``workers > 1`` runs independent jobs concurrently — dependencies declared
    with ``needs`` still wait, and a job whose dependency failed is marked
    skipped instead of running on missing inputs.
    """
    render_jobs = workers if workers > 1 else None
    if workers <= 1:
        results: List[JobResult] = []
        for job in jobs:
            result = _execute(job, default_jobs=render_jobs)
            results.append(result)
            if on_result is not None:
                on_result(result)
        return results

    from concurrent.futures import ThreadPoolExecutor

    validate_dependencies(jobs)
    results: List[JobResult] = []
    done: Dict[str, JobResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        pending = list(jobs)
        futures: Dict[str, Any] = {}
        while pending or futures:
            # 每一轮把"依赖都已成功"的任务丢进线程池
            for job in list(pending):
                waiting = [n for n in job.needs if n not in done]
                if waiting:
                    continue
                failed = [n for n in job.needs if n in done and not done[n].ok]
                pending.remove(job)
                if failed:
                    result = JobResult(job.id, job.action, False, 0.0, skipped=True,
                                       error="跳过：依赖失败 " + "、".join(failed))
                    results.append(result)
                    done[job.id] = result
                    if on_result is not None:
                        on_result(result)
                    continue
                futures[job.id] = pool.submit(_execute, job, render_jobs)
            if futures:
                from concurrent.futures import wait as wait_futures, FIRST_COMPLETED

                finished, _ = wait_futures(futures.values(), return_when=FIRST_COMPLETED)
                for future in finished:
                    result = future.result()
                    jid = result.id
                    futures.pop(jid, None)
                    results.append(result)
                    done[jid] = result
                    if on_result is not None:
                        on_result(result)
    return results


def summary_lines(results: List[JobResult], include_jobs: bool = True) -> List[str]:
    """Totals only; ``include_jobs`` adds the per-job lines (for a quiet rerun)."""
    ok = sum(1 for r in results if r.ok)
    total = sum(r.seconds for r in results)
    lines = [r.as_line() for r in results] if include_jobs else []
    lines.append(f"—— {ok}/{len(results)} 个任务成功，用时 {total:.0f}s")
    return lines


def write_report(results: List[JobResult], path: str) -> str:
    payload = {
        "ok": sum(1 for r in results if r.ok),
        "total": len(results),
        "seconds": round(sum(r.seconds for r in results), 1),
        "jobs": [
            {"id": r.id, "action": r.action, "ok": r.ok,
             "seconds": round(r.seconds, 1), "summary": r.summary, "error": r.error}
            for r in results
        ],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

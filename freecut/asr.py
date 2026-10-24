"""Offline speech-to-text (自动字幕) via Standalone Faster-Whisper.

JianyingPro's auto-caption feature is a cloud service.  FreeCut does the same
thing **locally**: it shells out to a portable Whisper build and turns the
word-level JSON into subtitle clips with ``words[]`` timing, so the karaoke
highlight needs no further alignment.

The backend is a self-contained executable (no Python dependency, no CUDA
required) that is not bundled here; point ``FREECUT_ASR_BIN`` at it, or drop the
build under ``tools/whisper-faster/`` next to this repository.

Observed JSON schema (Standalone Faster-Whisper r192.3, and faster-whisper's own
``--output_format json``)::

    {"segments": [{"id": 1, "start": 0.0, "end": 6.98,
                   "text": "…",
                   "words": [{"start": 0.0, "end": 0.46, "word": "你好,", "probability": 0.88}]}],
     "text": "…"}
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .media import _creationflags, ffmpeg_path

#: candidate locations for the portable build, most specific first
_BIN_CANDIDATES = (
    # 1) 环境变量指定
    os.environ.get("FREECUT_WHISPER", ""),
    # 2) 仓库内的便携版（自己下载，`tools/whisper-faster` 不入库）
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "tools", "whisper-faster", "app", "Whisper-Faster", "whisper-faster.exe"),
)


class ASRError(RuntimeError):
    pass


def _bundled_roots() -> "list[str]":
    """Directories the backend may live in when we are packaged.

    PyInstaller puts ``datas`` under ``sys._MEIPASS`` (onefile) or next to the
    executable (onedir).  Both are checked so the same code works either way.
    """
    roots = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(meipass)
    if getattr(sys, "frozen", False):
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    return roots


def find_backend() -> str:
    explicit = os.environ.get("FREECUT_ASR_BIN")
    if explicit and os.path.isfile(explicit):
        return explicit
    for cand in _BIN_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    for root in _bundled_roots():
        for rel in ("tools/whisper-faster/app/Whisper-Faster/whisper-faster.exe",
                    "whisper-faster/whisper-faster.exe",
                    "whisper-faster.exe"):
            cand = os.path.join(root, *rel.split("/"))
            if os.path.isfile(cand):
                return cand
    # last resort: anything named whisper-faster.exe under the drive's tools dir
    for base in (os.environ.get("FREECUT_TOOLS_DIR", ""),):
        if base and os.path.isdir(base):
            hits = glob.glob(os.path.join(base, "**", "whisper-faster.exe"), recursive=True)
            if hits:
                return hits[0]
    raise ASRError(
        "找不到离线语音识别后端（whisper-faster.exe）。\n"
        "请设置环境变量 FREECUT_ASR_BIN 指向该可执行文件，或把它放到 tools/whisper-faster/ 下。"
    )


#: 让中文识别输出繁体与简体一致；实测能把繁体字压到 3 个以内
_SIMPLIFIED_PROMPT = "以下是简体中文普通话的句子。"


def available_models(binary: Optional[str] = None) -> List[str]:
    binary = binary or find_backend()
    model_dir = os.path.join(os.path.dirname(binary), "_models")
    if not os.path.isdir(model_dir):
        return []
    out = []
    for name in os.listdir(model_dir):
        if name.startswith("faster-whisper-"):
            out.append(name[len("faster-whisper-"):])
    return sorted(out)


def _to_wav(media_path: str, wav_path: str, sample_rate: int = 16000) -> str:
    """Whisper wants 16 kHz mono; convert with the same FFmpeg we already use."""
    cmd = [
        ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", media_path, "-vn", "-ac", "1", "-ar", str(sample_rate), wav_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, creationflags=_creationflags())
    return wav_path


def transcribe(
    media_path: str,
    language: str = "zh",
    model: str = "base",
    binary: Optional[str] = None,
    timeout: float = 3600.0,
    word_timestamps: bool = True,
    device: str = "cpu",
    compute_type: str = "int8",
    threads: Optional[int] = None,
    prompt: Optional[str] = None,
) -> Dict:
    """Run the backend and return its parsed JSON (``segments`` + ``text``).

    Three flags are load-bearing and were established empirically:

    * ``-d cpu`` - without it the build selects CUDA and crashes with
      ``0xC0000409`` on this machine (the CUDA runtime packages are separate
      multi-hundred-MB downloads).
    * ``-prompt None`` - this build's default ``--initial_prompt`` is ``auto``,
      and that preset mis-transcribes Chinese (``字幕`` comes out as ``自目``).
      Pass your own glossary instead of ``None`` to bias the vocabulary.
    * ``-f json`` - the whisper.cpp flags ``-oj``/``-ojf`` do not exist here.

    ``prompt=None`` means "use the Simplified-Chinese default": this model's
    Chinese output otherwise drifts into Traditional characters ("轉場"),
    which no longer matches what the user typed.
    """
    if prompt is None and str(language).lower().startswith("zh"):
        prompt = _SIMPLIFIED_PROMPT

    binary = binary or find_backend()
    if not os.path.isfile(media_path):
        raise ASRError(f"找不到媒体文件: {media_path}")
    with tempfile.TemporaryDirectory(prefix="freecut-asr-") as tmp:
        wav = os.path.join(tmp, "audio.wav")
        _to_wav(media_path, wav)
        out_dir = os.path.join(tmp, "out")
        os.makedirs(out_dir, exist_ok=True)
        cmd = [
            binary, wav,
            "-m", model,                    # model NAME, not a path
            "-l", language,
            "-d", device,
            "--compute_type", compute_type,
            "-f", "json",
            "-o", out_dir,                  # a DIRECTORY; filename is forced
            "-prompt", prompt if prompt else "None",
        ]
        if threads:
            cmd += ["--threads", str(threads)]
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=_creationflags()
        )
        if proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", "replace")[-1200:]
            raise ASRError(f"语音识别失败（exit {proc.returncode}）:\n{tail}")
        files = glob.glob(os.path.join(out_dir, "*.json"))
        if not files:
            raise ASRError("语音识别没有产出 JSON")
        with open(files[0], "r", encoding="utf-8") as fh:
            return json.load(fh)


def segments_to_subtitles(data: Dict, offset_us: int = 0) -> List:
    """Convert the backend JSON into :class:`freecut.subtitles.Subtitle` objects."""
    from .subtitles import Subtitle

    out: List[Subtitle] = []
    for seg in data.get("segments") or []:
        try:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        text = str(seg.get("text") or "").strip()
        if not text or end <= start:
            continue
        words = []
        for w in seg.get("words") or []:
            wtxt = str(w.get("word") or "").strip()
            if not wtxt:
                continue
            try:
                ws = float(w.get("start", start))
                we = float(w.get("end", ws))
            except (TypeError, ValueError):
                continue
            words.append(
                {
                    "start_time": int(round((ws - start) * 1_000_000)),
                    "end_time": int(round((we - start) * 1_000_000)),
                    "text": wtxt,
                }
            )
        sub = Subtitle(
            start_us=offset_us + int(round(start * 1_000_000)),
            end_us=offset_us + int(round(end * 1_000_000)),
            text=text,
        )
        sub.words = words  # type: ignore[attr-defined]
        out.append(sub)
    return out


def transcribe_to_subtitles(media_path: str, **kw) -> List:
    offset_us = int(kw.pop("offset_us", 0))
    return segments_to_subtitles(transcribe(media_path, **kw), offset_us=offset_us)

def backend_status() -> "tuple[bool, str]":
    """Is offline speech recognition usable right now? Returns ``(ok, reason)``.

    The packaged build ships without the whisper backend, so the GUI has to be
    able to say "this feature needs an extra download" instead of failing three
    frames deeper.
    """
    try:
        binary = find_backend()
    except Exception as exc:
        return False, str(exc)
    models = available_models(binary)
    if not models:
        return False, (f"找到了后端 {os.path.basename(binary)}，但 _models 目录里没有模型。"
                       "首次使用需要联网下载，或把它放到同名目录下。")
    return True, f"后端 {os.path.basename(binary)}；可用模型 {', '.join(models)}"


def model_present(name: str, binary: Optional[str] = None) -> bool:
    """Is this model already on disk (no download needed)?"""
    try:
        return name in available_models(binary)
    except Exception:
        return False

"""Subtitle import / export and word-level (卡拉 OK) timing.

JianyingPro's text material carries a ``words[]`` array of
``{start_time, end_time, text}`` (times relative to the segment) which drives
the "逐字" highlight style.  FreeCut reads and writes the same structure, and
imports/exports SRT so subtitles can come from anywhere.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

SENTENCE_END = "。！？!?…；;，,、"


# --------------------------------------------------------------------------- #
# SRT
# --------------------------------------------------------------------------- #
@dataclass
class Subtitle:
    start_us: int
    end_us: int
    text: str
    index: int = 0
    #: optional per-word timing, times relative to ``start_us``
    #: (``[{"start_time","end_time","text"}, …]``).  ``asr`` fills this from
    #: Whisper's word timestamps; without it we distribute by character count.
    words: list = field(default_factory=list)

    @property
    def duration_us(self) -> int:
        return max(0, self.end_us - self.start_us)


_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})"
)


def _parse_ts(h: str, m: str, s: str, ms: str) -> int:
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1_000_000 + int(ms.ljust(3, "0")) * 1000


def parse_srt(path_or_text: str) -> List[Subtitle]:
    """Accept a path or the raw SRT text."""
    text = path_or_text
    if os.path.isfile(path_or_text):
        with open(path_or_text, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    out: List[Subtitle] = []
    blocks = re.split(r"\r?\n\r?\n+", text.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        idx = 0
        if lines[0].strip().isdigit():
            idx = int(lines[0].strip())
            lines = lines[1:]
        if not lines:
            continue
        m = _TIME_RE.search(lines[0])
        if not m:
            continue
        start = _parse_ts(*m.groups()[:4])
        end = _parse_ts(*m.groups()[4:])
        body = "\n".join(lines[1:]).strip()
        body = re.sub(r"<[^>]+>", "", body)  # strip <i> etc.
        if not body:
            continue
        out.append(Subtitle(start, end, body, idx or len(out) + 1))
    return out


def fmt_ts(us: int) -> str:
    us = max(0, int(us))
    ms = (us // 1000) % 1000
    s = (us // 1_000_000) % 60
    m = (us // 60_000_000) % 60
    h = us // 3_600_000_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(subs: Sequence[Subtitle], path: Optional[str] = None) -> str:
    lines = []
    for i, s in enumerate(subs, 1):
        lines.append(str(i))
        lines.append(f"{fmt_ts(s.start_us)} --> {fmt_ts(s.end_us)}")
        lines.append(s.text)
        lines.append("")
    text = "\n".join(lines)
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text


# --------------------------------------------------------------------------- #
# word timing
# --------------------------------------------------------------------------- #
def split_words(text: str) -> List[str]:
    """Split into highlight units: CJK characters, or whitespace-delimited words."""
    tokens: List[str] = []
    buf = ""
    for ch in text:
        if ch.isspace():
            if buf:
                tokens.append(buf)
                buf = ""
            continue
        if "\u4e00" <= ch <= "\u9fff":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    return tokens


def distribute_words(text: str, start_us: int, end_us: int) -> List[dict]:
    """Spread the words of ``text`` evenly over ``[start, end]``.

    A real ASR gives exact per-word timings; without one we weight by character
    count, which is what the app shows for imported subtitles before alignment.
    """
    tokens = split_words(text)
    if not tokens:
        return []
    total = sum(max(1, len(t)) for t in tokens)
    span = max(1, end_us - start_us)
    out = []
    cursor = start_us
    for i, tok in enumerate(tokens):
        share = int(round(span * max(1, len(tok)) / total))
        end = end_us if i == len(tokens) - 1 else min(end_us, cursor + share)
        out.append({"start_time": cursor - start_us, "end_time": end - start_us, "text": tok})
        cursor = end
    return out


# --------------------------------------------------------------------------- #
# project integration
# --------------------------------------------------------------------------- #
def add_subtitles(
    project,
    subs: Sequence[Subtitle],
    style: Optional[dict] = None,
    track_index: Optional[int] = None,
    karaoke: bool = True,
    offset_us: int = 0,
):
    """Create one text clip per subtitle, optionally with word timings.

    ``track_index=None`` puts the subtitles on a **new** text track, because a
    JianyingPro track cannot hold overlapping clips and subtitles usually run
    underneath existing titles.
    """
    from . import builder
    from .model import Timerange, Track

    style = dict(style or {})
    style.setdefault("size", 10.0)
    style.setdefault("y", -0.72)
    style.setdefault("color", "#FFFFFF")
    style.setdefault("stroke", 0.028)
    style.setdefault("stroke_color", "#101010")
    style.setdefault("font", "msyh.ttc")

    if track_index is None:
        track = Track(type="text")
        project.tracks.append(track)
    else:
        track = builder.track_of(project, "text", track_index)
    created = []
    for sub in subs:
        start = sub.start_us + offset_us
        dur = max(200_000, sub.duration_us)
        seg = builder.add_text(
            project, sub.text, start=start, duration=dur,
            x=style.get("x", 0.0), y=style.get("y", -0.72),
            size=style["size"], color=style["color"], font=style["font"],
            stroke=style["stroke"], stroke_color=style["stroke_color"],
            shadow=style.get("shadow", 0.0),
        )
        mat = project.material(seg.material_id)
        if karaoke:
            # prefer real per-word timings (ASR); fall back to even distribution
            words = list(getattr(sub, "words", None) or [])
            mat["words"] = words or distribute_words(sub.text, start, start + dur)
            mat["highlight_color"] = style.get("highlight_color", "#FFE066")
            mat["highlight_mode"] = 1
        # builder.add_text always appends to the first text track; move the clip
        # onto the track this call created
        for t in project.tracks:
            if t is not track and seg in t.segments:
                t.segments.remove(seg)
                track.segments.append(seg)
                break
        created.append(seg)
    return created


def extract_subtitles(project) -> List[Subtitle]:
    """Collect every text clip on text tracks into subtitle entries."""
    out: List[Subtitle] = []
    for track in project.tracks:
        if track.type != "text":
            continue
        for seg in track.sorted_segments():
            mat = project.material(seg.material_id)
            if mat is None:
                continue
            content = str(mat.get("content", "") or "")
            if not content.strip():
                continue
            out.append(
                Subtitle(seg.target_timerange.start, seg.target_timerange.end,
                         content.replace("\n", " ").strip())
            )
    out.sort(key=lambda s: s.start_us)
    for i, s in enumerate(out, 1):
        s.index = i
    return out


def find_active_word(words: Sequence[dict], local_us: int) -> Optional[int]:
    """Return the index of the word covering ``local_us`` (or None)."""
    for i, w in enumerate(words or []):
        try:
            a, b = int(w.get("start_time", 0)), int(w.get("end_time", 0))
        except (TypeError, ValueError):
            continue
        if a <= local_us < b:
            return i
    return None

def parse_vtt(path_or_text: str) -> List[Subtitle]:
    """WebVTT（``.vtt``）导入。

    ReaCut 支持 SRT + 基础 WebVTT；我们原来只有 SRT，补上这一半。
    VTT 与 SRT 的差别集中在三处：``WEBVTT`` 头、时间戳里的 ``.`` 毫秒分隔、
    以及 ``00:00.000``（省略小时）这种写法；其余按同样的块解析。
    """
    text = path_or_text
    if os.path.isfile(path_or_text):
        with open(path_or_text, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    # 去掉 WEBVTT 头与 NOTE/STYLE 块
    text = re.sub(r"^WEBVTT[^\n]*\n", "", text.strip(), flags=re.I)
    text = re.sub(r"(?ms)^(NOTE|STYLE|REGION)\b.*?(?=\n\r?\n|\Z)", "", text)
    out: List[Subtitle] = []
    for block in re.split(r"\r?\n\r?\n+", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        # VTT 的 cue 标识是可选的任意文本，不是序号
        ts_line = next((ln for ln in lines if "-->" in ln), None)
        if ts_line is None:
            continue
        normalised = ts_line.replace(".", ",")
        # VTT 允许省略小时（00:02.500）；SRT 的时间正则要三段，补成 00:00:02,500
        normalised = re.sub(r"(?<!:)(\d{2}:\d{2},\d{3})", r"00:\1", normalised)
        m = _TIME_RE.search(normalised)
        if not m:
            continue
        start = _parse_ts(*m.groups()[:4])
        end = _parse_ts(*m.groups()[4:])
        body = "\n".join(lines[lines.index(ts_line) + 1:]).strip()
        body = re.sub(r"<[^>]+>", "", body)
        body = html.unescape(body).strip()
        if body:
            out.append(Subtitle(text=body, start_us=start, end_us=end))
    return out

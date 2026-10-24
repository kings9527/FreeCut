"""图文成片: turn a written script into a finished timeline.

This is JianyingPro's 图文成片 in miniature:

1. split the script into sentences (CJK and Latin punctuation);
2. synthesise each sentence with the offline Windows voices (:mod:`freecut.tts`)
   — the WAV length becomes the sentence's slot on the timeline;
3. cut footage to fill those slots, cycling through the supplied clips;
4. add one subtitle per sentence with the original text.

No network, no model download: the only requirement is a Windows voice, and even
that is optional (the timeline is still built with estimated timings).
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Sequence

from . import builder, tts
from .model import Project, Segment

#: sentence terminators — CJK first, then ASCII, keeping the punctuation
_SPLIT = re.compile(r"(?<=[。！？!?；;…])\s*|(?<=\.)\s+(?=[A-Z0-9\"'“])")

_STRIP = " \t\r\n，,、"


def split_sentences(text: str, max_len: int = 60) -> List[str]:
    """Split a script into subtitle-sized sentences."""
    raw = [s.strip(_STRIP) for s in _SPLIT.split(str(text or "")) if s and s.strip(_STRIP)]
    out: List[str] = []
    for sentence in raw:
        if len(sentence) <= max_len:
            out.append(sentence)
            continue
        # 太长就按标点再切，最后按长度硬切
        parts = re.split(r"(?<=[，,、：:])", sentence)
        buf = ""
        for part in parts:
            if len(buf) + len(part) <= max_len:
                buf += part
            else:
                if buf.strip(_STRIP):
                    out.append(buf.strip(_STRIP))
                while len(part) > max_len:
                    out.append(part[:max_len].strip(_STRIP))
                    part = part[max_len:]
                buf = part
        if buf.strip(_STRIP):
            out.append(buf.strip(_STRIP))
    return out


def build_script_project(
    script: str,
    media_paths: Sequence[str],
    work_dir: str,
    voice: str = "",
    rate: int = 0,
    gap_us: int = 150_000,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    use_tts: bool = True,
    name: str = "图文成片",
) -> Dict:
    """Build a project from ``script``; returns ``{"project", ...stats}``."""
    sentences = split_sentences(script)
    if not sentences:
        raise ValueError("文案是空的，没有可以成片的内容")
    os.makedirs(work_dir, exist_ok=True)

    project = builder.new_project(width, height, fps, name=name)
    audio_track = builder.track_of(project, "audio", 0)
    video_track = builder.track_of(project, "video", 0)
    text_track = builder.track_of(project, "text", 0)

    cursor = 0
    used_tts = 0
    clips = [p for p in media_paths if p and os.path.isfile(p)]
    durations: List[int] = []
    lines: List[str] = []

    for index, sentence in enumerate(sentences):
        wav = os.path.join(work_dir, f"line_{index:03d}.wav")
        ok, dur = (tts.ensure_wav(sentence, wav, voice=voice, rate=rate)
                   if use_tts else (False, tts.estimate_duration_us(sentence)))
        if ok:
            used_tts += 1
            clip = builder.add_audio(project, wav, start=cursor, duration=dur, track_index=0)
        else:
            clip = None
        durations.append(dur)
        lines.append(sentence)

        if clips:
            path = clips[index % len(clips)]
            try:
                seg = builder.add_video(project, path, start=cursor, duration=dur,
                                        track_index=0, name=os.path.basename(path))
            except Exception:
                seg = None
        cursor += dur + gap_us

    # 字幕：一句一条
    cursor = 0
    for sentence, dur in zip(lines, durations):
        seg = builder.add_text(project, sentence, start=cursor, duration=max(200_000, dur),
                               size=9.0, y=-0.78)
        if seg is not None:
            mat = project.material(seg.material_id)
            if mat is not None:
                mat["background_color"] = "#000000"
                mat["background_alpha"] = 0.45
                mat["background_round_radius"] = 0.12
                mat["shadow_alpha"] = 0.3
                mat["shadow_distance"] = 0.03
        cursor += dur + gap_us

    return {
        "project": project,
        "sentences": sentences,
        "durations_us": durations,
        "total_us": sum(durations) + gap_us * max(0, len(durations) - 1),
        "tts_lines": used_tts,
        "audio_lines": used_tts,
        "used_tts": used_tts > 0,
        "voice": voice or tts.pick_voice(voice),
        "clips": len(clips),
    }


def summary(stats: Dict) -> str:
    total = stats.get("total_us", 0) / 1e6
    tts_note = (f"语音合成 {stats['tts_lines']} 句"
                if stats.get("used_tts") else "无语音合成（用估算时长）")
    return (f"{len(stats.get('sentences') or [])} 句 / {total:.1f} 秒 / "
            f"{tts_note} / {stats.get('clips', 0)} 段素材")

"""Offline text-to-speech through the Windows SAPI voices.

No extra dependency: Windows ships ``System.Speech``, which already has a
Chinese voice (Microsoft Huihui) on zh-CN installs.  We drive it from
PowerShell and read the text from stdin so quotes / newlines cannot break the
command line.

Everything degrades gracefully: if SAPI is unavailable the caller can fall back
to estimated durations (see :mod:`freecut.scriptfilm`).
"""

from __future__ import annotations

import os
import subprocess
import wave
from typing import List, Optional, Tuple

_PS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
# Python 现在按 UTF-8 写 stdin；不显式声明就会按控制台代码页(GBK)读，合成出乱码
$reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), [System.Text.Encoding]::UTF8)
$text = $reader.ReadToEnd()
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
{select_voice}
$synth.Rate = {rate}
$synth.Volume = 100
$synth.SetOutputToWaveFile('{out}')
$synth.Speak($text)
$synth.Dispose()
"""

_LIST_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture }
$s.Dispose()
"""


def available() -> bool:
    return os.name == "nt"


def voices() -> List[str]:
    """Installed SAPI voice names (empty when unavailable)."""
    if not available():
        return []
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _LIST_PS],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
            creationflags=0x08000000,
        )
    except Exception:
        return []
    out = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if "|" in line:
            out.append(line.split("|", 1)[0])
    return out


def pick_voice(preferred: str = "") -> str:
    """Best available voice: the requested one, else a Chinese one, else any."""
    names = voices()
    if not names:
        return ""
    if preferred and preferred in names:
        return preferred
    for name in names:
        if any(tag in name for tag in ("Huihui", "Chinese", "zh-CN", "Xiaoxiao", "Yaoyao")):
            return name
    return names[0]


def synthesize(text: str, out_path: str, voice: str = "", rate: int = 0) -> bool:
    """Render ``text`` to a WAV file. Returns False when TTS is unavailable."""
    if not available() or not text.strip():
        return False
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    select = ""
    chosen = pick_voice(voice)
    if chosen:
        safe = chosen.replace("'", "''")
        select = f"try {{ $synth.SelectVoice('{safe}') }} catch {{ }}"
    script = _PS.format(select_voice=select, rate=int(rate),
                        out=out_path.replace("\\", "\\\\").replace("'", "''"))
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            input=text, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
            creationflags=0x08000000,
        )
    except Exception:
        return False
    return proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 44


def wav_duration_us(path: str) -> int:
    """Duration of a WAV file in microseconds (0 when unreadable)."""
    try:
        with wave.open(path, "rb") as fh:
            frames = fh.getnframes()
            rate = fh.getframerate() or 1
            return int(frames / float(rate) * 1_000_000)
    except Exception:
        return 0


def estimate_duration_us(text: str, chars_per_second: float = 5.5) -> int:
    """Rough speaking time for when TTS is not available."""
    n = len("".join(ch for ch in text if not ch.isspace()))
    return int(max(0.6, n / max(0.5, chars_per_second)) * 1_000_000)


def speaker_summary() -> str:
    names = voices()
    if not names:
        return "没有可用的语音合成（需要 Windows SAPI）"
    return f"{len(names)} 个语音：" + "、".join(names[:4])


def ensure_wav(text: str, out_path: str, voice: str = "", rate: int = 0,
               retries: int = 2) -> Tuple[bool, int]:
    """Synthesize and return ``(ok, duration_us)``; falls back to an estimate.

    SAPI occasionally fails on a single call (COM contention when several
    sentences are synthesised back to back), so retry before giving up —
    a silently *estimated* line in a narrated project is hard to spot.
    """
    for _attempt in range(max(1, int(retries))):
        if synthesize(text, out_path, voice=voice, rate=rate):
            dur = wav_duration_us(out_path)
            if dur > 0:
                return True, dur
    return False, estimate_duration_us(text)


def play(path: str, blocking: bool = False) -> bool:
    """Play a WAV with the stdlib ``winsound`` module (Windows only)."""
    try:
        import threading
        import winsound

        if blocking:
            winsound.PlaySound(path, winsound.SND_FILENAME)
        else:
            threading.Thread(
                target=lambda: winsound.PlaySound(path, winsound.SND_FILENAME),
                daemon=True,
            ).start()
        return True
    except Exception:
        return False


def preview(text: str, voice: str = "", rate: int = 0, out_path: Optional[str] = None) -> Optional[str]:
    """Synthesize a short sample and play it; returns the wav path (or None)."""
    import tempfile

    path = out_path or os.path.join(tempfile.gettempdir(), "freecut_tts_preview.wav")
    ok, _dur = ensure_wav(text, path, voice=voice, rate=rate)
    if not ok:
        return None
    play(path)
    return path

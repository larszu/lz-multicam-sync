"""ffprobe metadata and ffmpeg audio decode (cached)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import numpy as np

from .model import Clip
from .timecode import wallclock_from_name

MEDIA_EXT = {".mp4", ".mov", ".mxf", ".wav", ".bwf", ".mts", ".m4a", ".mp3", ".aif", ".aiff", ".braw"}
SR = 8000


_NOWIN = {"creationflags": 0x08000000} if os.name == "nt" else {}  # no console flashes from the GUI


def default_cache() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "lzsync" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "lzsync"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "lzsync"


def tool(name: str) -> str:
    """ffmpeg/ffprobe: next to the app, on PATH, or in the usual Homebrew places.

    Apps started from Finder do not see the shell PATH, so look there too.
    """
    exe = name + (".exe" if os.name == "nt" else "")
    here = [Path(getattr(sys, "_MEIPASS", "")), Path(sys.executable).parent]
    for d in here:
        if (d / exe).exists():
            return str(d / exe)
    found = shutil.which(exe)
    if found:
        return found
    for d in ("/opt/homebrew/bin", "/usr/local/bin"):
        if os.path.exists(os.path.join(d, exe)):
            return os.path.join(d, exe)
    raise FileNotFoundError(f"{name} nicht gefunden – bitte ffmpeg installieren (brew install ffmpeg / winget install ffmpeg)")


def probe(path: str) -> dict:
    out = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True, **_NOWIN,
    ).stdout
    return json.loads(out)


def _tc_seconds(tc: str, fps: Fraction) -> Fraction:
    sep = ";" if ";" in tc else ":"
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    nominal = round(fps)
    frames = (h * 3600 + m * 60 + s) * nominal + f
    if sep == ";" and nominal in (30, 60):  # drop-frame
        drop = 2 if nominal == 30 else 4
        total_min = h * 60 + m
        frames -= drop * (total_min - total_min // 10)
    return Fraction(frames) / fps


def clip_from_file(path: str, root: str, idx: int) -> Clip:
    info = probe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = info.get("format", {})
    tags = {k.lower(): val for k, val in (fmt.get("tags") or {}).items()}
    for s in streams:
        tags.update({k.lower(): val for k, val in (s.get("tags") or {}).items() if k.lower() not in tags})
    fps = Fraction(v["r_frame_rate"]) if v and v.get("r_frame_rate", "0/0") != "0/0" else None
    tc = None
    if tags.get("timecode") and fps:
        tc = _tc_seconds(tags["timecode"], fps)
    elif "time_reference" in tags and a:  # BWF: samples since midnight
        tc = Fraction(int(tags["time_reference"]), int(a.get("sample_rate", 48000)))
    wall = wallclock_from_name(Path(path).name)
    if wall is None and tags.get("creation_time"):
        ct = tags["creation_time"]
        if tags.get("date") and len(ct) == 8 and ct.count(":") == 2:  # BWF: date + local time
            ct = f"{tags['date']}T{ct}"
        try:
            wall = datetime.fromisoformat(ct.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    rel = Path(path).relative_to(root)
    return Clip(
        id=f"r{idx + 100}",
        name=Path(path).name,
        device=str(rel.parent) if str(rel.parent) != "." else Path(path).stem.rstrip("0123456789_-") or "?",
        path=str(Path(path).absolute()),
        tc_start=tc,
        duration=Fraction(fmt.get("duration", "0")).limit_denominator(48000),
        has_video=v is not None,
        has_audio=a is not None,
        frame_duration=1 / fps if fps else None,
        wallclock=wall,
        width=int(v["width"]) if v and v.get("width") else None,
        height=int(v["height"]) if v and v.get("height") else None,
        audio_channels=int(a.get("channels", 0)) or None if a else None,
    )


def scan(folder: str) -> list[Clip]:
    files = sorted(
        str(p) for p in Path(folder).rglob("*")
        if p.suffix.lower() in MEDIA_EXT and not p.name.startswith("._")
    )
    return [clip_from_file(f, folder, i) for i, f in enumerate(files)]


def prefetch(cache: "AudioCache", clips, workers: int | None = None, done=None) -> None:
    """Decode many files at once – ffmpeg is the bottleneck, one process per core."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    todo = [c for c in clips if c.has_audio and c.path and os.path.exists(c.path)]
    workers = workers or min(8, os.cpu_count() or 2)
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(cache.get, c) for c in todo]
        for k, f in enumerate(as_completed(futs)):
            f.result()
            if done:
                done(k + 1, len(todo))


class AudioCache:
    """Decodes each clip once to mono float32 at SR and keeps it on disk."""

    def __init__(self, cache_dir: str | None = None, channels: str = "mix"):
        self.dir = Path(cache_dir) if cache_dir else default_cache()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.channels = channels
        self.mem: dict[str, np.ndarray] = {}

    def get(self, clip: Clip) -> np.ndarray | None:
        if not clip.has_audio or not clip.path or not os.path.exists(clip.path):
            return None
        if clip.path in self.mem:
            return self.mem[clip.path]
        st = os.stat(clip.path)
        key = hashlib.sha1(f"{clip.path}|{st.st_size}|{st.st_mtime}|{SR}|{self.channels}".encode()).hexdigest()
        f = self.dir / f"{key}.npy"
        if not f.exists():
            af = ["-ac", "1"] if self.channels == "mix" else ["-af", "pan=mono|c0=c0"]
            raw = subprocess.run(
                [tool("ffmpeg"), "-v", "error", "-nostdin", "-i", clip.path, "-map", "0:a:0", "-vn", *af,
                 "-ar", str(SR), "-f", "f32le", "-"],
                capture_output=True, check=True, **_NOWIN,
            ).stdout
            np.save(f, np.frombuffer(raw, dtype=np.float32))
        x = np.load(f, mmap_mode="r")
        self.mem[clip.path] = x
        return x

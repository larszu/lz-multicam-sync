"""FCPXML 1.x in/out (Resolve, Final Cut Pro, Premiere via XtoCC)."""
from __future__ import annotations

import copy
import math
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from .model import Clip, SyncResult
from .timecode import natural_key, wallclock_from_name


def rt(s: str | None) -> Fraction:
    """'1001/30000s' -> Fraction seconds."""
    if not s:
        return Fraction(0)
    return Fraction(s.rstrip("s"))


def ft(x: Fraction) -> str:
    x = Fraction(x)
    if x.denominator == 1:
        return f"{x.numerator}s"
    return f"{x.numerator}/{x.denominator}s"


def src_to_path(src: str) -> str:
    u = urlparse(src)
    return unquote(u.path) if u.scheme in ("file", "") else src


def device_of(path: str, levels: int = 1) -> str:
    parts = PurePosixPath(path).parts[:-1]
    return "/".join(parts[-levels:]) if parts else "?"


def parse(path: str) -> ET.Element:
    """FCPXML file, .fcpxmld bundle folder, or a zipped .fcpxmld (as browsers/AirDrop deliver it)."""
    p = Path(path)
    if p.is_dir():
        inner = p / "Info.fcpxml"
        if not inner.exists():
            inner = next(p.rglob("*.fcpxml"), None)
            if inner is None:
                raise ValueError(f"{p.name}: keine .fcpxml im Bundle gefunden")
        return ET.parse(inner).getroot()
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist() if n.endswith(".fcpxml") and not n.split("/")[-1].startswith("._")]
            if not names:
                raise ValueError(f"{p.name}: ZIP enthält keine .fcpxml")
            name = next((n for n in names if n.endswith("Info.fcpxml")), names[0])
            return ET.fromstring(z.read(name))
    try:
        return ET.parse(p).getroot()
    except ET.ParseError as e:
        raise ValueError(f"{p.name}: keine lesbare FCPXML ({e})") from None


def read(path: str) -> tuple[list[Clip], dict]:
    root = parse(path)
    formats = {f.get("id"): f for f in root.iter("format")}
    used = {e.get("ref") for e in root.iter("asset-clip")} | {e.get("ref") for e in root.iter("clip")}
    clips: list[Clip] = []
    for a in root.iter("asset"):
        aid = a.get("id")
        if used and aid not in used:
            continue
        rep = a.find("media-rep")
        src = src_to_path(rep.get("src")) if rep is not None else (a.get("src") or "")
        name = a.get("name") or PurePosixPath(src).name
        fmt = formats.get(a.get("format"))
        fd = rt(fmt.get("frameDuration")) if fmt is not None and fmt.get("frameDuration") else None
        clips.append(
            Clip(
                id=aid,
                name=name,
                device=device_of(src),
                path=src,
                tc_start=rt(a.get("start")),
                duration=rt(a.get("duration")),
                has_video=a.get("hasVideo") == "1",
                has_audio=a.get("hasAudio") == "1",
                frame_duration=fd,
                wallclock=wallclock_from_name(name),
                xml=a,
            )
        )
    seq = next(root.iter("sequence"), None)
    meta = {
        "formats": formats,
        "sequence_format": seq.get("format") if seq is not None else None,
        "event": _name(root, "event"),
        "project": _name(root, "project"),
    }
    return clips, meta


def _name(root, tag: str) -> str:
    e = next(root.iter(tag), None)
    return e.get("name", "lzsync") if e is not None else "lzsync"


def _name(root, tag: str) -> str:
    e = next(root.iter(tag), None)
    return e.get("name", "lzsync") if e is not None else "lzsync"


def placements(path: str) -> dict[str, float]:
    """Global timeline position of each asset's media start, keyed by clip name."""
    root = parse(path)
    assets = {a.get("id"): a for a in root.iter("asset")}
    out: dict[str, float] = {}

    def walk(e, base: Fraction | None, pstart: Fraction):
        for c in e:
            if c.tag not in ("asset-clip", "clip", "gap", "ref-clip", "sync-clip"):
                continue
            off, st = rt(c.get("offset")), rt(c.get("start"))
            pos = off if base is None else base + (off - pstart)
            if c.tag == "asset-clip":
                a = assets[c.get("ref")]
                name = a.get("name") or c.get("name")
                # timeline position where the asset's first sample would be
                out[name] = float(pos - (st - rt(a.get("start"))))
            walk(c, pos, st)

    for s in root.iter("spine"):
        walk(s, None, Fraction(0))
    return out


def _snap(x: float, step: Fraction, mode: str) -> Fraction:
    q = Fraction(x) / step
    n = {"round": round, "ceil": math.ceil, "floor": math.floor}[mode](q)
    return n * step


def write(result: SyncResult, meta: dict, path: str, project: str | None = None) -> None:
    """Timeline: one gap in the spine, every clip connected to it, one lane per device."""
    clips = [r for r in result.clips]
    fmts = meta.get("formats") or {}
    vids = [r.clip for r in clips if r.clip.has_video and r.clip.xml is not None]
    seq_fmt_id = meta.get("sequence_format") or (
        Counter(c.xml.get("format") for c in vids).most_common(1)[0][0] if vids else None
    )
    seq_fmt = fmts.get(seq_fmt_id)
    fd = rt(seq_fmt.get("frameDuration")) if seq_fmt is not None else Fraction(1, 25)

    main = [r for r in clips if r.start is not None and r.group == 0]
    t0 = min((r.start for r in main), default=0.0)
    end = max((r.start - t0 + float(r.clip.duration) for r in main), default=0.0)

    devices = sorted({r.clip.device for r in clips}, key=natural_key)
    video_devs = [d for d in devices if any(r.clip.has_video for r in clips if r.clip.device == d)]
    audio_devs = [d for d in devices if d not in video_devs]
    lane = {d: i + 1 for i, d in enumerate(video_devs)} | {d: -(i + 1) for i, d in enumerate(audio_devs)}

    rows = [(r, r.start - t0) for r in main]
    # groups that only synced among themselves keep their internal timing,
    # one block after another behind the main timeline
    cursor = end + 10.0
    for g in sorted({r.group for r in clips if r.start is not None and r.group != 0}):
        rs = [r for r in clips if r.group == g and r.start is not None]
        g0 = min(r.start for r in rs)
        rows += [(r, cursor + r.start - g0) for r in rs]
        cursor = max(x + float(r.clip.duration) for r, x in rows) + 10.0
    # single unplaced files: per lane, in file order
    lane_cur = defaultdict(lambda: cursor)
    for r in sorted((r for r in clips if r.start is None), key=lambda r: natural_key(r.clip.name)):
        x = lane_cur[r.clip.device]
        lane_cur[r.clip.device] = x + float(r.clip.duration) + 1.0
        rows.append((r, x))
    total = max(g + float(r.clip.duration) for r, g in rows) if rows else 0.0

    root = ET.Element("fcpxml", version="1.10")
    res = ET.SubElement(root, "resources")
    for f in fmts.values():
        res.append(copy.deepcopy(f))
    if seq_fmt is None:
        seq_fmt_id = "r_lzsync_fmt"
        ET.SubElement(res, "format", id=seq_fmt_id, name="FFVideoFormat1080p25",
                      frameDuration="1/25s", width="1920", height="1080")
    for r, _ in rows:
        res.append(copy.deepcopy(r.clip.xml) if r.clip.xml is not None else _asset_xml(r.clip))
    lib = ET.SubElement(root, "library")
    ev = ET.SubElement(lib, "event", name=meta.get("event") or "lzsync")
    pr = ET.SubElement(ev, "project", name=project or f"{meta.get('project') or 'lzsync'} - lzsync")
    seq = ET.SubElement(pr, "sequence", format=seq_fmt_id, tcStart="0s", tcFormat="NDF",
                        duration=ft(_snap(total, fd, "ceil")), audioRate="48k")
    spine = ET.SubElement(seq, "spine")
    gap = ET.SubElement(spine, "gap", name="Gap", offset="0s", start="0s",
                        duration=ft(_snap(total, fd, "ceil")))
    for r, g in sorted(rows, key=lambda x: x[1]):
        c = r.clip
        s0 = c.tc_start or Fraction(0)
        dur = c.duration
        if c.has_video:
            off = _snap(g, fd, "round")
            start = s0
        else:  # audio-only: keep sub-frame accuracy through the in-point
            off = _snap(g, fd, "ceil")
            shift = Fraction(round((float(off) - g) * 48000), 48000)
            start, dur = s0 + shift, dur - shift
        role = {"audio": None, "timecode": None, "chronology": "Chronologie",
                "unplaced": "Nicht synchron"}[r.method]
        if r.group != 0 and r.start is not None:
            role = "Teilgruppe"
        attrs = dict(ref=c.id, name=c.name, lane=str(lane[c.device]), offset=ft(off),
                     start=ft(start), duration=ft(dur), enabled="1")
        if role:
            attrs["audioRole"] = role
        ac = ET.SubElement(gap, "asset-clip", **attrs)
        if c.xml is not None and c.xml.get("format"):
            ac.set("format", c.xml.get("format"))
        if r.method != "audio" or r.group != 0:
            ET.SubElement(ac, "note").text = f"lzsync: {r.method}, conf {r.confidence:.2f}. {r.note}".strip()
    ET.indent(root, "    ")
    with open(path, "wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n')
        fh.write(ET.tostring(root, encoding="utf-8"))


def _asset_xml(c: Clip) -> ET.Element:
    a = ET.Element("asset", id=c.id, name=c.name, start=ft(c.tc_start or 0), duration=ft(c.duration),
                   hasVideo="1" if c.has_video else "0", hasAudio="1" if c.has_audio else "0")
    if c.path:
        ET.SubElement(a, "media-rep", kind="original-media", src=Path(c.path).absolute().as_uri())
    return a

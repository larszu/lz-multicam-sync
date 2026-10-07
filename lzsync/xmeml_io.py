"""FCP7 XML (xmeml) for DaVinci Resolve and Premiere Pro – explicit tracks.

FCPXML has no tracks, only lanes, and Resolve packs connected audio onto the
next free track. xmeml names every track, so the timeline arrives as planned:
one video track per camera, then one audio track per device and channel –
six tracks for a six-channel recorder, each lavalier on its own.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

from .fcpxml_io import layout
from .model import SyncResult
from .timecode import natural_key


def _rate(parent, fps: Fraction):
    r = ET.SubElement(parent, "rate")
    ET.SubElement(r, "timebase").text = str(round(fps))
    ET.SubElement(r, "ntsc").text = "TRUE" if fps.denominator == 1001 else "FALSE"
    return r


def _sub(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, tag, **attrs)
    if text is not None:
        e.text = str(text)
    return e


def _pathurl(p: str) -> str:
    return "file://" + quote(str(Path(p).absolute()))


def _tc_string(seconds: float, fps: Fraction) -> str:
    n = round(fps)
    f = int(round(seconds * float(fps)))
    return f"{f // (3600 * n) % 24:02d}:{f // (60 * n) % 60:02d}:{f // n % 60:02d}:{f % n:02d}"


def write(result: SyncResult, path: str, name: str = "lzsync", fps: Fraction | None = None) -> None:
    rows, _ = layout(result)
    vids = [r.clip for r, _ in rows if r.clip.has_video and r.clip.frame_duration]
    if fps is None:
        fds = [c.frame_duration for c in vids]
        fps = 1 / max(set(fds), key=fds.count) if fds else Fraction(25)
    size = max(((c.width, c.height) for c in vids if c.width), key=lambda wh: sum(
        float(c.duration) for c in vids if (c.width, c.height) == wh), default=(1920, 1080))

    def frames(t: float) -> int:
        return int(round(t * fps))

    devices = sorted({r.clip.device for r, _ in rows}, key=natural_key)
    vdevs = [d for d in devices if any(r.clip.has_video for r, _ in rows if r.clip.device == d)]
    chans = {d: max((r.clip.audio_channels or 2) for r, _ in rows if r.clip.device == d and r.clip.has_audio)
             for d in devices if any(r.clip.has_audio for r, _ in rows if r.clip.device == d)}
    # recorders first: they carry the sound the edit is built on
    adevs = [d for d in devices if d not in vdevs and d in chans] + [d for d in vdevs if d in chans]

    total = max((g + float(r.clip.duration) for r, g in rows), default=0.0)
    root = ET.Element("xmeml", version="5")
    # element order follows what Resolve itself exports – its parser is picky
    seq = _sub(root, "sequence")
    _sub(seq, "name", name)
    _sub(seq, "duration", frames(total) + 1)
    _rate(seq, fps)
    _sub(seq, "in", -1)
    _sub(seq, "out", -1)
    tc = _sub(seq, "timecode")
    _sub(tc, "string", "00:00:00:00")
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")
    _rate(tc, fps)
    media = _sub(seq, "media")
    video = _sub(media, "video")
    audio = _sub(media, "audio")

    files_done: set[str] = set()
    n_item = 0
    groups: dict[int, list[tuple[ET.Element, str]]] = {}  # one file's video + audio clipitems

    def file_el(parent, c):
        fid = f"file-{c.id}"
        if fid in files_done:  # later references are empty, as Premiere writes them
            ET.SubElement(parent, "file", id=fid)
            return
        files_done.add(fid)
        f = _sub(parent, "file", id=fid)
        _sub(f, "duration", frames(float(c.duration)))
        _rate(f, fps)
        _sub(f, "name", c.name)
        _sub(f, "pathurl", _pathurl(c.path or c.name))
        if c.has_video:  # Resolve needs the source timecode once a sequence format is given
            tcf = 1 / c.frame_duration if c.frame_duration else fps
            t = _sub(f, "timecode")
            _sub(t, "string", _tc_string(float(c.tc_start or 0), tcf))
            _sub(t, "displayformat", "NDF")
            _rate(t, tcf)
        m = _sub(f, "media")
        if c.has_video:
            v = _sub(m, "video")
            _sub(v, "duration", frames(float(c.duration)))
            sc = _sub(v, "samplecharacteristics")
            _sub(sc, "width", c.width or size[0])
            _sub(sc, "height", c.height or size[1])
        if c.has_audio:
            _sub(_sub(m, "audio"), "channelcount", c.audio_channels or 2)

    def clipitem(track, r, g, kind, src_ch=None):
        nonlocal n_item
        n_item += 1
        c = r.clip
        it = _sub(track, "clipitem", id=f"clipitem-{n_item}")
        _sub(it, "name", c.name)
        dur = frames(float(c.duration))
        _sub(it, "duration", dur)
        _rate(it, fps)
        start = frames(g)
        _sub(it, "start", start)
        _sub(it, "end", start + dur)
        _sub(it, "enabled", "TRUE")
        _sub(it, "in", 0)
        _sub(it, "out", dur)
        file_el(it, c)
        groups.setdefault(id(r), []).append((it, kind))
        if kind == "audio":
            st = _sub(it, "sourcetrack")
            _sub(st, "mediatype", "audio")
            _sub(st, "trackindex", src_ch)
        if r.method != "audio" or r.group != 0:
            _sub(it, "labels").append(ET.Element("label2"))
            it.find("labels/label2").text = "Iris" if r.method == "chronology" else "Mango"
            _sub(it, "comments").append(ET.Element("mastercomment1"))
            it.find("comments/mastercomment1").text = f"lzsync: {r.method}, {r.confidence:.0%}. {r.note}".strip()
        return it

    ordered = sorted(rows, key=lambda x: x[1])
    # files of one device that overlap (duplicates, a second card) get an extra
    # track group instead of hiding each other on the same track
    sub: dict[int, int] = {}
    for d in devices:
        ends: list[float] = []
        for r, g in ordered:
            if r.clip.device != d:
                continue
            k = next((i for i, e in enumerate(ends) if e <= g + 1e-6), len(ends))
            if k == len(ends):
                ends.append(0.0)
            ends[k] = g + float(r.clip.duration)
            sub[id(r)] = k
    n_sub = {d: 1 + max((sub[id(r)] for r, _ in rows if r.clip.device == d), default=0) for d in devices}
    for d in vdevs:
        for k in range(n_sub[d]):
            tr = _sub(video, "track")
            for r, g in ordered:
                if r.clip.device == d and r.clip.has_video and sub[id(r)] == k:
                    clipitem(tr, r, g, "video")
            _sub(tr, "enabled", "TRUE")
            _sub(tr, "locked", "FALSE")
    vf = _sub(_sub(video, "format"), "samplecharacteristics")
    _sub(vf, "width", size[0])
    _sub(vf, "height", size[1])
    _sub(vf, "pixelaspectratio", "square")
    _rate(vf, fps)
    for d in adevs:
        for k in range(n_sub[d]):
            for ch in range(1, chans[d] + 1):
                tr = _sub(audio, "track")
                for r, g in ordered:
                    c = r.clip
                    if c.device == d and c.has_audio and sub[id(r)] == k and ch <= (c.audio_channels or 2):
                        clipitem(tr, r, g, "audio", ch)
                _sub(tr, "enabled", "TRUE")
                _sub(tr, "locked", "FALSE")

    # link picture and sound of one file, so they move together in the edit
    for items in groups.values():
        if len(items) < 2:
            continue
        for it, _ in items:
            for other, kind in items:
                ln = _sub(it, "link")
                _sub(ln, "linkclipref", other.get("id"))
                _sub(ln, "mediatype", kind)

    ET.indent(root, "  ")
    with open(path, "wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n')
        fh.write(ET.tostring(root, encoding="utf-8"))

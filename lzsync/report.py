"""Human summary and machine-readable sync map."""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from .model import SyncResult
from .timecode import natural_key

LABEL = {"audio": "Audio", "timecode": "Timecode", "chronology": "Chronologie", "unplaced": "offen"}


def summary(res: SyncResult) -> str:
    by_dev = defaultdict(list)
    for r in res.clips:
        by_dev[r.clip.device].append(r)
    n = len(res.clips)
    placed = sum(1 for r in res.clips if r.start is not None and r.group == 0)
    lines = [f"{placed}/{n} Clips in der Timeline · Referenzuhr: {res.reference}", ""]
    # timecode offset against the reference clock: ~0 means jammed to the same source
    tcoff = {}
    for d, rs in by_dev.items():
        v = [r.start - r.clip.local for r in rs if r.method == "audio" and r.group == 0]
        if v and res.tc_mode.get(d) == "free-run":
            tcoff[d] = sorted(v)[len(v) // 2]
    ref_off = tcoff.get(res.reference)
    lines.append(f"{'Gerät':28s} {'TC':9s} {'Gang':>9s} {'TC-Versatz':>11s}  Clips  Audio  TC  Chrono  offen")
    for d in sorted(by_dev, key=natural_key):
        rs = by_dev[d]
        c = Counter(r.method if r.group == 0 else "unplaced" for r in rs)
        ppm = res.drift_ppm.get(d)
        ppm_s = f"{ppm:+7.1f}ppm" if ppm is not None else "      –  "
        off = f"{tcoff[d] - ref_off:+10.3f}s" if d in tcoff and ref_off is not None else "          –"
        lines.append(f"{d:28s} {res.tc_mode.get(d, '?'):9s} {ppm_s} {off}  {len(rs):5d}  {c['audio']:5d} "
                     f"{c['timecode']:3d}  {c['chronology']:6d}  {c['unplaced']:5d}")
    review = [r for r in res.clips if r.method in ("chronology", "unplaced") or r.group != 0 or r.confidence < 0.5]
    if review:
        lines += ["", "Bitte prüfen:"]
        for r in sorted(review, key=lambda r: (r.clip.device, natural_key(r.clip.name))):
            lines.append(f"  {r.clip.device}/{r.clip.name}: {LABEL[r.method]}, {r.confidence:.0%} – {r.note}")
    if res.warnings:
        lines += ["", "Hinweise:"] + [f"  {w}" for w in res.warnings]
    return "\n".join(lines)


def to_json(res: SyncResult, path: str) -> None:
    data = {
        "reference": res.reference,
        "tc_mode": res.tc_mode,
        "drift_ppm": res.drift_ppm,
        "warnings": res.warnings,
        "edges": res.edges,
        "clips": [
            dict(name=r.clip.name, device=r.clip.device, path=r.clip.path, island=r.island,
                 start=r.start, duration=float(r.clip.duration), method=r.method,
                 confidence=round(r.confidence, 3), group=r.group, note=r.note)
            for r in res.clips
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def overview(res: SyncResult) -> dict:
    """Same content as summary(), structured for the window."""
    by_dev = defaultdict(list)
    for r in res.clips:
        by_dev[r.clip.device].append(r)
    tcoff = {}
    for d, rs in by_dev.items():
        v = sorted(r.start - r.clip.local for r in rs if r.method == "audio" and r.group == 0)
        if v and res.tc_mode.get(d) == "free-run":
            tcoff[d] = v[len(v) // 2]
    ref_off = tcoff.get(res.reference)
    devices = []
    for d in sorted(by_dev, key=natural_key):
        rs = by_dev[d]
        c = Counter(r.method if r.group == 0 else "unplaced" for r in rs)
        devices.append(dict(
            name=d, tc=res.tc_mode.get(d, "?"), ppm=res.drift_ppm.get(d),
            tc_offset=(tcoff[d] - ref_off) if d in tcoff and ref_off is not None else None,
            clips=len(rs), audio=c["audio"], timecode=c["timecode"], chronology=c["chronology"],
            unplaced=c["unplaced"], reference=d == res.reference,
            video=any(r.clip.has_video for r in rs)))
    review = [dict(device=r.clip.device, name=r.clip.name, method=LABEL[r.method] if r.group == 0 else "Teilgruppe",
                   confidence=round(r.confidence, 2), note=r.note)
              for r in sorted(res.clips, key=lambda r: (natural_key(r.clip.device), natural_key(r.clip.name)))
              if r.method in ("chronology", "unplaced") or r.group != 0 or r.confidence < 0.5]
    placed = sum(1 for r in res.clips if r.start is not None and r.group == 0)
    return dict(total=len(res.clips), placed=placed, reference=res.reference, devices=devices,
                review=review, warnings=res.warnings)

"""Timecode analysis: rec-run vs free-run detection, midnight wrap, islands."""
from __future__ import annotations

import re
from datetime import datetime

from .model import Clip, Island

DAY = 86400.0


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


_TS = re.compile(r"(20\d{2})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})")


def wallclock_from_name(name: str) -> float | None:
    """DJI_20261002202220_0034_D.MP4 -> epoch seconds (device clock, local time)."""
    m = _TS.search(name)
    if not m:
        return None
    try:
        return datetime(*map(int, m.groups())).timestamp()
    except ValueError:
        return None


def order(clips: list[Clip]) -> list[Clip]:
    return sorted(clips, key=lambda c: natural_key(c.name))


def tc_mode(clips: list[Clip], tol: float = 0.07) -> str:
    """'rec-run' if consecutive files continue each other's timecode, else 'free-run'.

    Rec-run TC only advances while recording, so it says nothing about the real
    pause between two takes; free-run TC follows the clock.
    """
    cs = [c for c in order(clips) if c.tc_start is not None]
    if len(cs) < len(clips) or not cs or all(c.tc_start == 0 for c in cs):
        return "none"
    if len(cs) < 2:
        return "free-run"
    contiguous = sum(
        abs(float(a.tc_start + a.duration - b.tc_start)) <= tol for a, b in zip(cs, cs[1:])
    )
    return "rec-run" if contiguous >= 0.8 * (len(cs) - 1) else "free-run"


def unwrap(clips: list[Clip]) -> None:
    """Set clip.local: timecode plus whole days where it rolled over midnight."""
    days = 0.0
    prev = None
    for c in order(clips):
        tc = float(c.tc_start or 0)
        if prev is not None and tc + days < prev - DAY / 2:
            days += DAY
        c.local = tc + days
        prev = c.local


def islands(clips: list[Clip], mode: str, split_gap: float) -> list[Island]:
    out: list[Island] = []
    dev = clips[0].device
    if mode == "free-run":
        cs = sorted(clips, key=lambda c: c.local)
        group = [cs[0]]
        for c in cs[1:]:
            end = max(g.local + float(g.duration) for g in group)
            if c.local - end > split_gap:
                out.append(group)
                group = []
            group.append(c)
        out.append(group)
        return [Island(f"{dev}#{i}", dev, g, g[0].local) for i, g in enumerate(out)]
    return [Island(f"{dev}/{c.name}", dev, [c], c.local) for c in order(clips)]

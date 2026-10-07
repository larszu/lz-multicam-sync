"""Compare two placements (e.g. lzsync vs. Syncaila) clip by clip."""
from __future__ import annotations

import statistics


def compare(ours: dict[str, float], ref: dict[str, float], anchor: list[str] | None = None):
    common = sorted(set(ours) & set(ref))
    if not common:
        return {"common": 0, "rows": []}
    base = [n for n in (anchor or common) if n in ours and n in ref] or common
    shift = statistics.median(ours[n] - ref[n] for n in base)
    rows = [(n, ours[n] - ref[n] - shift) for n in common]
    return {"common": len(common), "shift": shift, "rows": rows,
            "missing_in_ours": sorted(set(ref) - set(ours)), "missing_in_ref": sorted(set(ours) - set(ref))}


def format_report(cmp: dict, fps: float = 25.0, tol_frames: float = 1.0) -> str:
    lines = [f"{cmp['common']} gemeinsame Clips"]
    if not cmp["rows"]:
        return lines[0]
    bad = [(n, d) for n, d in cmp["rows"] if abs(d) * fps > tol_frames]
    within = len(cmp["rows"]) - len(bad)
    lines.append(f"{within}/{len(cmp['rows'])} innerhalb ±{tol_frames:g} Frame(s)")
    for n, d in sorted(bad, key=lambda x: -abs(x[1])):
        lines.append(f"  {n:40s} {d * 1000:+10.1f} ms  ({d * fps:+.1f} Frames)")
    return "\n".join(lines)

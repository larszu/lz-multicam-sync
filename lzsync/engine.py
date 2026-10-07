"""Pipeline: clips -> islands -> coarse match -> fine match -> global solve -> validation."""
from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy import fft as sfft

from . import dsp, graph, timecode
from .media import SR, AudioCache
from .model import Clip, ClipResult, Island, Point, Prior, SyncResult


@dataclass
class Options:
    use_audio: bool = True
    split_gap: float = 1800.0  # free-run TC: gap that starts a new island
    session_gap: float = 3600.0  # chronology-only material this far away is kept out of the timeline
    min_overlap: float = 20.0
    accept_z: float = 9.0
    accept_prominence: float = 2.0
    fine_window: float = 8.0
    fine_min_sharpness: float = 8.0
    tc_sigma: float = 0.5
    wall_sigma: float = 2.0
    channels: str = "mix"
    cache_dir: str | None = None
    reference: str | None = None  # device name used as master clock
    jammed: tuple[str, ...] = ()  # devices whose free-run TC was jammed to one source
    progress: Callable[[str, dict], None] | None = None  # live data for the timeline view


Log = Callable[[str], None]


def _islands(clips: list[Clip], opt: Options):
    by_dev = defaultdict(list)
    for c in clips:
        by_dev[c.device].append(c)
    isl, modes = [], {}
    for dev, cs in sorted(by_dev.items()):
        mode = timecode.tc_mode(cs)
        modes[dev] = mode
        timecode.unwrap(cs)
        isl += timecode.islands(cs, mode, opt.split_gap)
    return isl, modes


def _priors(isl: list[Island], modes, opt: Options):
    pri, wall = [], {}
    by_dev = defaultdict(list)
    for i in isl:
        by_dev[i.device].append(i)
    for dev, li in by_dev.items():
        if modes[dev] == "free-run":
            li = sorted(li, key=lambda i: i.t0)
            for a, b in zip(li, li[1:]):
                d = b.t0 - a.t0
                pri.append(Prior(a.id, b.id, d, opt.tc_sigma + 1e-4 * d, "timecode"))
        walls = [(i, i.clips[0].wallclock) for i in li if i.clips[0].wallclock is not None]
        if len(walls) >= 2:
            for i, w in walls:
                wall[i.id] = w - i.rel(i.clips[0])
    jam = sorted((i for i in isl if i.device in opt.jammed and modes[i.device] == "free-run"), key=lambda i: i.t0)
    for a, b in zip(jam, jam[1:]):
        if a.device != b.device:
            pri.append(Prior(a.id, b.id, b.t0 - a.t0, 0.04, "jammed"))
    return pri, wall


class Matcher:
    def __init__(self, isl: list[Island], cache: AudioCache, opt: Options, log: Log):
        self.isl, self.cache, self.opt, self.log = isl, cache, opt, log
        self.tracks: dict[str, dsp.Track] = {}
        self.audio_len: dict[str, float] = {}

    def build(self):
        H = dsp.FEAT_HZ
        prog = self.opt.progress or (lambda *_: None)
        for k, i in enumerate(self.isl):
            prog("decode", {"done": k, "total": len(self.isl), "island": i.id})
            n = int(math.ceil(i.length * H)) + 1
            v, m = np.zeros(n, np.float32), np.zeros(n, bool)
            for c in i.clips:
                x = self.cache.get(c)
                if x is None or len(x) < SR:
                    continue
                e, ok = dsp.envelope(x, SR)
                s = int(round(i.rel(c) * H))
                e, ok = e[: n - s], ok[: n - s]
                v[s : s + len(e)] = e
                m[s : s + len(e)] = ok
            if m.sum() >= 2 * H:
                self.tracks[i.id] = dsp.Track(v, m)
                self.audio_len[i.id] = m.sum() / H

    def coarse(self):
        H = dsp.FEAT_HZ
        ids = sorted(self.tracks, key=lambda k: -len(self.tracks[k]))
        dev = {i.id: i.device for i in self.isl}
        edges = []
        pairs = [(a, b) for k, a in enumerate(ids) for b in ids[k + 1 :] if dev[a] != dev[b]]
        self.log(f"  Grobabgleich: {len(pairs)} Paare")
        prog = self.opt.progress or (lambda *_: None)
        step = max(1, len(pairs) // 100)
        with sfft.set_workers(-1):
            for k, (a, b) in enumerate(pairs):
                if k % step == 0:
                    prog("pairs", {"done": k, "total": len(pairs)})
                shorter = min(self.audio_len[a], self.audio_len[b])
                mo = int(H * max(2.0, min(self.opt.min_overlap, 0.6 * shorter)))
                lags, ncc, ov = dsp.masked_ncc(self.tracks[a], self.tracks[b], mo)
                pk = dsp.peaks(lags, ncc, ov, exclude=H)
                if not pk:
                    continue
                p = pk[0]
                # short overlaps need a clearer peak than hour-long ones
                need = self.opt.accept_z + 4.0 * max(0.0, 1 - p["overlap"] / (60 * H))
                if p["z"] >= need and p["prominence"] >= self.opt.accept_prominence:
                    edges.append(dict(a=a, b=b, lag=p["lag"] / H, z=p["z"], prom=p["prominence"],
                                      ncc=p["ncc"], overlap=p["overlap"] / H))
                    prog("edge", {"a": a, "b": b, "lag": p["lag"] / H, "z": round(p["z"], 1)})
            prog("pairs", {"done": len(pairs), "total": len(pairs)})
        return edges

    def fine(self, e) -> list[Point]:
        """GCC-PHAT on raw audio at several places of the overlap; robust line fit."""
        ia = next(i for i in self.isl if i.id == e["a"])
        ib = next(i for i in self.isl if i.id == e["b"])
        L = self.opt.fine_window
        cands = []
        for ca in ia.clips:
            for cb in ib.clips:
                s = max(ia.rel(ca), ib.rel(cb) + e["lag"])
                t = min(ia.rel(ca) + float(ca.duration), ib.rel(cb) + e["lag"] + float(cb.duration))
                if t - s >= L + 0.5:
                    cands.append((ca, cb, s, t))
        if not cands:
            return []
        total = sum(t - s for *_, s, t in cands)
        n_win = int(min(40, max(4, total // 120)))
        res = []
        tcen = sum((s + t) / 2 * (t - s) for *_, s, t in cands) / total
        for ca, cb, s, t in cands:
            k = max(1, int(round(n_win * (t - s) / total)))
            xa, xb = self.cache.get(ca), self.cache.get(cb)
            if xa is None or xb is None:
                continue
            for j in range(k):
                ta = s + 0.25 + (t - s - L - 0.5) * (j + 0.5) / k
                tb = ta - e["lag"]
                a0 = int((ta - ia.rel(ca)) * SR)
                b0 = int((tb - ib.rel(cb)) * SR)
                sa = np.asarray(xa[a0 : a0 + int(L * SR)], dtype=np.float64)
                sb = np.asarray(xb[b0 : b0 + int(L * SR)], dtype=np.float64)
                if len(sa) < L * SR * 0.9 or len(sb) < L * SR * 0.9 or sa.std() < 1e-6 or sb.std() < 1e-6:
                    continue
                n = min(len(sa), len(sb))
                max_lag = 0.06 + 3e-4 * abs(ta - tcen)
                d, sharp, _ = dsp.gcc_phat(sa[:n], sb[:n], SR, max_lag)
                if sharp >= self.opt.fine_min_sharpness:
                    res.append((ta + d, tb, sharp))
        if len(res) < 2:
            return []
        t = np.array([r[0] for r in res])
        off = np.array([r[0] - r[1] for r in res])
        # robust line: median slope over pairs, then MAD inliers
        if len(res) >= 3 and np.ptp(t) > 60:
            i, j = np.triu_indices(len(t), 1)
            ok = np.abs(t[j] - t[i]) > 30
            slope = float(np.median((off[j] - off[i])[ok] / (t[j] - t[i])[ok])) if ok.any() else 0.0
        else:
            slope = 0.0
        r = off - slope * (t - tcen)
        med = np.median(r)
        inl = np.abs(r - med) < max(0.002, 4 * 1.4826 * np.median(np.abs(r - med)))
        if inl.sum() < 2:
            return []
        return [Point(e["a"], ra, e["b"], rb, sigma=max(0.0003, 0.004 / math.sqrt(sh / 8)))
                for (ra, rb, sh), ok in zip(res, inl) if ok]


def run(clips: list[Clip], opt: Options | None = None, log: Log = print) -> SyncResult:
    opt = opt or Options()
    isl, modes = _islands(clips, opt)
    dev_of = {i.id: i.device for i in isl}
    for d, m in sorted(modes.items()):
        n = sum(1 for i in isl if i.device == d)
        log(f"  {d}: TC {m}, {n} Insel(n)")
    priors, wall = _priors(isl, modes, opt)
    prog = opt.progress or (lambda *_: None)
    index = {id(c): k for k, c in enumerate(clips)}
    prog("layout", {
        "clips": [dict(k=index[id(c)], dev=c.device, name=c.name, dur=float(c.duration), island=i.id,
                       rel=i.rel(c), video=c.has_video) for i in isl for c in i.clips],
        "islands": [dict(id=i.id, dev=i.device, length=i.length) for i in isl],
        "modes": modes,
    })

    points: list[Point] = []
    edges: list[dict] = []
    if opt.use_audio:
        cache = AudioCache(opt.cache_dir, opt.channels)
        m = Matcher(isl, cache, opt, log)
        t0 = time.time()
        log("  Audio dekodieren / Hüllkurven …")
        m.build()
        log(f"    {time.time() - t0:.0f} s")
        t0 = time.time()
        edges = m.coarse()
        log(f"    {time.time() - t0:.0f} s")
        t0 = time.time()
        log(f"  {len(edges)} Audio-Treffer, Feinabgleich …")
        for n_e, e in enumerate(edges):
            prog("fine", {"done": n_e, "total": len(edges)})
            pts = m.fine(e)
            if pts:
                e["fine"] = len(pts)
                points += pts
            else:  # keep the coarse match, but trust it less
                e["fine"] = 0
                points.append(Point(e["a"], e["lag"], e["b"], 0.0, sigma=0.03, kind="coarse"))

    if opt.use_audio:
        log(f"    {time.time() - t0:.0f} s")
    linked = {p.a for p in points} | {p.b for p in points}
    cand = [i for i in isl if (i.device == opt.reference if opt.reference else i.id in linked)]
    # master clock: audio recorders first (TCXO, far steadier than camera clocks),
    # then the most recorded audio – not the widest span, which includes pauses
    audio_s = lambda i: sum(float(c.duration) for c in i.clips)
    recorder = lambda i: not any(c.has_video for c in i.clips)
    ref_island = max(cand, key=lambda i: (recorder(i), audio_s(i), i.id)).id if cand else None

    offsets, drift, comp, resid = graph.solve(dev_of, points, priors, wall, opt.wall_sigma, ref_island)
    warnings: list[str] = []

    # chronology check: one camera cannot record two files at once
    bad = _overlaps(isl, offsets, drift, comp)
    if bad:
        for i in bad:
            warnings.append(f"{i}: überlappt mit Nachbardatei derselben Kamera – Treffer verworfen")
        points = [p for p in points if p.a not in bad and p.b not in bad]
        offsets, drift, comp, resid = graph.solve(dev_of, points, priors, wall, opt.wall_sigma, ref_island)

    evidence = defaultdict(list)
    for p, r in zip(points, resid):
        if np.isfinite(r):
            evidence[p.a].append((p.ta, r, p.kind))
            evidence[p.b].append((p.tb, r, p.kind))
    main = 0
    results: list[ClipResult] = []
    groups = {}
    for i in isl:
        a = drift.get(i.device, 0.0) * 1e-6
        has_audio_link = bool(evidence[i.id])
        g = comp[i.id]
        alone = sum(1 for j in isl if comp[j.id] == g) == 1
        for c in i.clips:
            rel = i.rel(c)
            own = [(t, r) for t, r, _ in evidence[i.id] if rel - 1 <= t <= rel + float(c.duration) + 1]
            start = offsets[i.id] + rel * (1 + a)
            if alone and g != 0 and len(i.clips) == 1:
                res = ClipResult(c, i.id, None, "unplaced", 0.0, "kein Audio-Treffer")
            elif alone and g != 0:
                res = ClipResult(c, i.id, start, "timecode", 0.5,
                                 "kein Audio-Treffer; Clips dieser Kamera untereinander per Timecode")
            elif own:
                rms = float(np.sqrt(np.mean([r * r for _, r in own])))
                conf = min(1.0, 0.55 + 0.1 * len(own)) * math.exp(-rms / 0.01)
                res = ClipResult(c, i.id, start, "audio", conf, f"{len(own)} Messpunkte, Rest {rms * 1000:.1f} ms")
            elif has_audio_link:
                res = ClipResult(c, i.id, start, "timecode", 0.8, "über Timecode derselben Kamera")
            else:
                res = ClipResult(c, i.id, start, "chronology", 0.4,
                                 "nur Timecode/Uhrzeit, kein eigener Audio-Treffer")
            res.group = g
            results.append(res)
        groups[g] = groups.get(g, 0) + len(i.clips)

    _place_by_neighbours(isl, results, comp, warnings)
    if opt.use_audio:
        _search_short(results, cache, opt, log, drift)
    _session_outliers(results, opt, warnings)

    ref_name = dev_of.get(ref_island) if ref_island else None
    if not ref_name:
        ref_name = max(isl, key=lambda i: len(evidence[i.id])).device if isl else ""
    for r in results:
        if r.group != main and r.method != "unplaced":
            r.note = (r.note + f"; Gruppe {r.group}: nur untereinander synchron").strip("; ")
    prog("final", {"clips": [dict(k=index[id(r.clip)], start=r.start, method=r.method, group=r.group)
                             for r in results], "reference": ref_name})
    # positive = device clock runs fast against the reference
    rate = {d: -v for d, v in drift.items()}
    return SyncResult(results, rate, modes, ref_name, edges, warnings)


def _span(i: Island, offsets, drift):
    a = drift.get(i.device, 0.0) * 1e-6
    return offsets[i.id], offsets[i.id] + i.length * (1 + a)


def _overlaps(isl, offsets, drift, comp) -> set[str]:
    bad = set()
    by_dev = defaultdict(list)
    for i in isl:
        by_dev[(i.device, comp[i.id])].append(i)
    for li in by_dev.values():
        li = sorted(li, key=lambda i: _span(i, offsets, drift)[0])
        for x, y in zip(li, li[1:]):
            if _span(x, offsets, drift)[1] - _span(y, offsets, drift)[0] > 0.5:
                bad.add(min((x, y), key=lambda i: i.length).id)
    return bad


def _place_by_neighbours(isl, results, comp, warnings):
    """Unmatched files sit between their numbered neighbours of the same camera.

    A run of consecutive unmatched files is placed as a whole between the two
    nearest anchored files, each with its own search window for the later
    constrained audio search.
    """
    by_dev = defaultdict(list)
    for r in results:
        by_dev[r.clip.device].append(r)
    anchored = lambda x: x.start is not None and x.group == 0 and x.method in ("audio", "timecode")
    for dev, rs in by_dev.items():
        rs.sort(key=lambda r: timecode.natural_key(r.clip.name))
        k = 0
        while k < len(rs):
            if rs[k].method != "unplaced":
                k += 1
                continue
            j = k
            while j < len(rs) and rs[j].method == "unplaced":
                j += 1
            run = rs[k:j]
            prev = next((x for x in reversed(rs[:k]) if anchored(x)), None)
            nxt = next((x for x in rs[j:] if anchored(x)), None)
            k = j
            if not prev or not nxt:
                continue
            lo = prev.start + float(prev.clip.duration)
            durs = [float(r.clip.duration) for r in run]
            slack = nxt.start - lo - sum(durs)
            if slack < 0:
                continue
            gap = min(1.0, slack / (len(run) + 1))
            t = nxt.start - gap * len(run) - sum(durs)  # cameras restart quickly: stick to the following take
            before = 0.0
            for r, d in zip(run, durs):
                after = sum(durs) - before - d
                r.window = (lo + before, nxt.start - after - d)
                r.start = t
                t += d + gap
                before += d
                r.method, r.group = "chronology", 0
                r.confidence = max(0.1, 0.6 - slack / 600)
                r.note = f"zwischen {prev.clip.name} und {nxt.clip.name} eingeordnet (Spielraum {slack:.0f} s)"


def _session_outliers(results, opt: Options, warnings):
    synced = [r for r in results if r.method == "audio" and r.group == 0]
    if not synced:
        return
    lo = min(r.start for r in synced)
    hi = max(r.start + float(r.clip.duration) for r in synced)
    for r in results:
        if r.method in ("chronology", "timecode") and r.group == 0 and r.start is not None:
            if r.start + float(r.clip.duration) < lo - opt.session_gap or r.start > hi + opt.session_gap:
                warnings.append(f"{r.clip.name}: liegt laut Timecode {abs(r.start - lo) / 3600:.1f} h außerhalb des Drehs – nicht in die Timeline gelegt")
                r.method, r.start, r.confidence = "unplaced", None, 0.0
                r.note = "außerhalb der Aufnahmesession (Timecode)"


def _search_short(results, cache: AudioCache, opt: Options, log: Log, drift: dict[str, float]):
    """Clips without their own audio match get a constrained search.

    The global solve already tells where such a clip must be – between its
    neighbouring files (rec-run) or close to its timecode (free-run). Inside
    that small window even a one-second clip can be matched exactly, which a
    blind search over the whole day cannot do.
    """
    bed = [r for r in results if r.method == "audio" and r.group == 0 and r.clip.has_audio]
    found = 0
    for r in results:
        if r.method not in ("chronology", "timecode") or r.group != 0 or r.start is None:
            continue
        lo, hi = r.window or (r.start - 1.0, r.start + 1.0)
        x = cache.get(r.clip)
        if x is None or len(x) < 0.3 * SR:
            continue
        dur = len(x) / SR
        pad = 0.5
        g0, g1 = lo - pad, hi + dur + pad
        votes = []
        for b in sorted(bed, key=lambda b: -_cover(b, g0, g1)):
            if b.clip.device == r.clip.device or _cover(b, g0, g1) < (g1 - g0) * 0.95:
                continue
            y = cache.get(b.clip)
            k = 1 + drift.get(b.clip.device, 0.0) * 1e-6  # bed media seconds -> global seconds
            m0 = (g0 - b.start) / k
            s0 = int(round(m0 * SR))
            seg = np.asarray(y[s0 : s0 + int((g1 - g0) * SR)], dtype=np.float64)
            pos, sharp, uniq = dsp.locate(seg, np.asarray(x, dtype=np.float64), SR)
            if sharp >= 12 and uniq >= 1.5:
                votes.append((b.start + (s0 / SR + pos) * k, sharp, b.clip.name))
            if len(votes) >= 3:
                break
        if not votes:
            continue
        starts = np.array([v[0] for v in votes])
        med = float(np.median(starts))
        agree = [v for v in votes if abs(v[0] - med) < 0.002]
        if len(votes) >= 2 and len(agree) < 2:
            r.note += "; Kurzsuche uneinig"
            continue
        r.start = float(np.mean([v[0] for v in agree]))
        r.method = "audio"
        r.confidence = min(1.0, 0.5 + 0.15 * len(agree) + min(0.2, max(v[1] for v in agree) / 200))
        r.note = f"im Suchfenster gefunden ({len(agree)}× bestätigt, u. a. an {agree[0][2]})"
        found += 1
    if found:
        log(f"  Suchfenster: {found} Clip(s) ohne eigenen Treffer exakt nachgezogen")


def _cover(b, g0, g1):
    return max(0.0, min(b.start + float(b.clip.duration), g1) - max(b.start, g0))

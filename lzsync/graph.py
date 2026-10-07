"""Global solver: one offset per island, one clock drift per device.

Every audio match and every chronology hint becomes a linear equation
    global(island b, tb) - global(island a, ta) = 0
with global(i, t) = b_i + t * (1 + ppm_d * 1e-6). All equations are solved
together with iteratively reweighted least squares (Huber), so one wrong
match cannot drag the whole timeline – it shows up as a large residual and
is down-weighted or dropped.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .model import Point, Prior


class UnionFind:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def solve(island_dev: dict[str, str], points: list[Point], priors: list[Prior],
          wall: dict[str, float], wall_sigma: float, reference: str | None,
          drift_sigma_ppm: float = 100.0, huber: float = 3.0, reject: float = 12.0):
    """Returns (offsets, drift_ppm, component_of, residuals per point index)."""
    islands = list(island_dev)
    uf = UnionFind(islands)
    for p in points:
        uf.union(p.a, p.b)
    for p in priors:
        uf.union(p.a, p.b)
    # wallclock links islands of one device through an unknown clock offset
    by_dev = defaultdict(list)
    for i, w in wall.items():
        by_dev[island_dev[i]].append(i)
    for dev, isl in by_dev.items():
        for i in isl[1:]:
            uf.union(isl[0], i)

    weight = defaultdict(float)
    for p in points:
        weight[p.a] += 1 / p.sigma
        weight[p.b] += 1 / p.sigma

    comps = defaultdict(list)
    for i in islands:
        comps[uf.find(i)].append(i)

    offsets: dict[str, float] = {}
    drift: dict[str, float] = {}
    comp_of: dict[str, int] = {}
    resid = np.zeros(len(points))
    ordered = sorted(comps.values(), key=lambda c: (reference not in c, -sum(weight[i] for i in c), -len(c)))
    for ci, comp in enumerate(ordered):
        cset = set(comp)
        ref = reference if reference in cset else max(comp, key=lambda i: (weight[i], i))
        devs = sorted({island_dev[i] for i in comp})
        wdevs = sorted({island_dev[i] for i in comp if i in wall})
        col = {("b", i): k for k, i in enumerate(comp)}
        for d in devs:
            col[("a", d)] = len(col)
        for d in wdevs:
            col[("c", d)] = len(col)
        pidx = [k for k, p in enumerate(points) if p.a in cset]
        rows, rhs, sig, kind = [], [], [], []

        def eq(coefs, r, s, k):
            row = np.zeros(len(col))
            for key, v in coefs:
                row[col[key]] += v
            rows.append(row)
            rhs.append(r)
            sig.append(s)
            kind.append(k)

        for k in pidx:
            p = points[k]
            da, db = island_dev[p.a], island_dev[p.b]
            eq([(("b", p.b), 1), (("b", p.a), -1), (("a", db), p.tb * 1e-6), (("a", da), -p.ta * 1e-6)],
               p.ta - p.tb, p.sigma, k)
        for p in priors:
            if p.a in cset:
                d = island_dev[p.a]
                eq([(("b", p.b), 1), (("b", p.a), -1), (("a", d), -p.delta * 1e-6)], p.delta, p.sigma, -1)
        if wdevs:
            w0 = min(wall[i] for i in comp if i in wall)
            for i in comp:
                if i in wall:
                    eq([(("b", i), 1), (("c", island_dev[i]), -1)], wall[i] - w0, wall_sigma, -2)
        for d in devs:  # crystals are within ~±100 ppm
            eq([(("a", d), 1)], 0.0, drift_sigma_ppm, -3)
        eq([(("b", ref), 1)], 0.0, 1e-6, -4)
        eq([(("a", island_dev[ref]), 1)], 0.0, 1e-6, -4)

        A, y, s = np.array(rows), np.array(rhs), np.array(sig)
        active = np.ones(len(y), bool)
        w = np.ones(len(y))
        x = np.zeros(len(col))
        for it in range(12):
            ww = np.where(active, w / s, 0.0)
            x, *_ = np.linalg.lstsq(A * ww[:, None], y * ww, rcond=None)
            r = (A @ x - y) / s
            new_w = np.where(np.abs(r) <= huber, 1.0, huber / np.maximum(np.abs(r), 1e-12))
            new_active = active & ~((np.abs(r) > reject) & (np.array(kind) >= 0))
            if it > 2 and np.array_equal(new_active, active) and np.allclose(new_w, w, atol=1e-3):
                break
            w, active = new_w, (new_active if it >= 2 else active)
        for i in comp:
            offsets[i] = float(x[col[("b", i)]])
            comp_of[i] = ci
        for d in devs:
            drift[d] = float(x[col[("a", d)]])
        r_all = A @ x - y
        for row_k, k in enumerate(kind):
            if k >= 0:
                resid[k] = r_all[row_k] if active[row_k] else np.inf
    return offsets, drift, comp_of, resid

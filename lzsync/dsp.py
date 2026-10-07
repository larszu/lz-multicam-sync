"""Signal features and correlation primitives."""
from __future__ import annotations

import numpy as np
from scipy.fft import irfft, next_fast_len, rfft
from scipy.ndimage import uniform_filter1d

FEAT_HZ = 100


def envelope(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Log-energy at 100 Hz with the slow trend removed.

    Microphones differ in level and frequency response; the shape of loudness
    changes over ~0.1–2 s survives both, and the subtraction of a 2 s moving
    average makes it independent of absolute gain.
    """
    hop = sr // FEAT_HZ
    n = len(x) // hop
    if n == 0:
        return np.zeros(0, np.float32), np.zeros(0, bool)
    fr = np.asarray(x[: n * hop], dtype=np.float32).reshape(n, hop)
    # pre-emphasis-ish: first difference favours transients over rumble
    d = np.diff(fr, axis=1, prepend=fr[:, :1])
    p = np.mean(d * d, axis=1)
    valid = p > 1e-11  # digital silence / dropouts carry no timing information
    e = np.log10(p + 1e-10)
    e = e - uniform_filter1d(e, size=2 * FEAT_HZ, mode="nearest")
    e = np.clip(e, -3.0, 3.0)
    return e.astype(np.float32), valid


class Track:
    """Sparse device-time signal: values + validity mask, with cached spectra."""

    def __init__(self, values: np.ndarray, mask: np.ndarray):
        self.v = (values * mask).astype(np.float64)
        self.m = mask.astype(np.float64)
        self._spec: dict[int, tuple] = {}

    def __len__(self):
        return len(self.v)

    def spec(self, n: int):
        if n not in self._spec:
            self._spec[n] = (rfft(self.m, n), rfft(self.v, n), rfft(self.v * self.v, n))
            if len(self._spec) > 4:
                self._spec.pop(next(iter(self._spec)))
        return self._spec[n]


def masked_ncc(f: Track, g: Track, min_overlap: int):
    """Normalized cross-correlation over valid samples only (Padfield 2012).

    Returns (lags, ncc, overlap). Lag k means g[0] sits at f[k].
    """
    # power-of-two sizes: few distinct lengths, so the long track's spectra are reused
    n = 1 << int(np.ceil(np.log2(len(f) + len(g) - 1)))
    Mf, Ff, F2 = f.spec(n)
    Mg, Gg, G2 = g.spec(n)
    c = np.conj
    O = irfft(Mf * c(Mg), n)
    Sf = irfft(Ff * c(Mg), n)
    Sg = irfft(Mf * c(Gg), n)
    Sff = irfft(F2 * c(Mg), n)
    Sgg = irfft(Mf * c(G2), n)
    Sfg = irfft(Ff * c(Gg), n)
    O = np.rint(O)
    ok = O >= max(min_overlap, 1)
    Os = np.where(ok, O, 1.0)
    num = Sfg - Sf * Sg / Os
    den = (Sff - Sf * Sf / Os) * (Sgg - Sg * Sg / Os)
    ok &= den > 1e-9
    ncc = np.where(ok, num / np.sqrt(np.where(ok, den, 1.0)), 0.0)
    lags = np.arange(n)
    lags[lags >= len(f)] -= n  # indices past f are the negative lags
    # keep only physically possible lags
    valid = (lags > -len(g)) & (lags < len(f))
    lags, ncc, O = lags[valid], ncc[valid], O[valid]
    order = np.argsort(lags, kind="stable")
    return lags[order], ncc[order], O[order]


def significance(ncc, overlap):
    """NCC weighted by its own overlap, normalised to the noise of this pair.

    The spread of a correlation coefficient shrinks with the square root of the
    number of samples behind it. Judging every lag against one common noise
    level lets a few seconds of chance overlap beat half an hour of real
    overlap; ncc·√overlap, divided by its robust spread over all lags, does not.
    """
    s = ncc * np.sqrt(np.maximum(overlap, 0))
    valid = overlap > 0
    base = s[valid]
    if base.size < 10:
        return s
    mad = np.median(np.abs(base - np.median(base)))
    # floor: with only a few seconds of audio the spread estimate collapses
    return s / max(1.4826 * mad, 1.0)


def peaks(lags, ncc, overlap, exclude: int, k: int = 3):
    """Top-k peaks with prominence over the rest of the correlation function."""
    z = significance(ncc, overlap)
    out = []
    work = z.copy()
    for _ in range(k):
        i = int(np.argmax(work))
        if work[i] <= 0:
            break
        out.append(dict(lag=int(lags[i]), ncc=float(ncc[i]), overlap=int(overlap[i]), z=float(z[i])))
        lo, hi = np.searchsorted(lags, [lags[i] - exclude, lags[i] + exclude])
        work[lo:hi] = -np.inf
    for j, p in enumerate(out):
        nxt = out[j + 1]["z"] if j + 1 < len(out) else 0.0
        p["prominence"] = p["z"] - max(nxt, 0.0)
    return out


def gcc_phat(a: np.ndarray, b: np.ndarray, sr: int, max_lag: float):
    """Delay of b relative to a (seconds, b[t] ~ a[t + d]) with sub-sample peak.

    Returns (delay, sharpness) where sharpness is peak height over the median
    absolute value of the PHAT correlation.
    """
    n = next_fast_len(len(a) + len(b), real=True)
    A = rfft(a * np.hanning(len(a)), n)
    B = rfft(b * np.hanning(len(b)), n)
    R = A * np.conj(B)
    R /= np.abs(R) + 1e-12
    r = irfft(R, n)
    m = int(max_lag * sr)
    r = np.concatenate((r[-m:], r[: m + 1]))
    i = int(np.argmax(np.abs(r)))
    if 0 < i < len(r) - 1:
        y0, y1, y2 = np.abs(r[i - 1 : i + 2])
        den = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if den != 0 else 0.0
    else:
        frac = 0.0
    lag = (i - m + frac) / sr
    sharp = float(np.abs(r[i]) / (np.median(np.abs(r)) + 1e-12))
    return lag, sharp, float(np.sign(r[i]))


def locate(long: np.ndarray, short: np.ndarray, sr: int):
    """Where does `short` occur inside `long`? PHAT-weighted template search.

    Returns (position seconds, sharpness, uniqueness) – uniqueness is the ratio of
    the best peak to the best peak more than 20 ms away (higher is better).
    """
    if len(short) >= len(long):
        return 0.0, 0.0, 0.0
    n = next_fast_len(len(long) + len(short), real=True)
    w = np.hanning(len(short))
    R = rfft(long, n) * np.conj(rfft(short * w, n))
    R /= np.abs(R) + 1e-12
    r = np.abs(irfft(R, n)[: len(long) - len(short) + 1])
    i = int(np.argmax(r))
    frac = 0.0
    if 0 < i < len(r) - 1:
        y0, y1, y2 = r[i - 1 : i + 2]
        den = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if den != 0 else 0.0
    ex = int(0.02 * sr)
    rest = np.concatenate((r[: max(0, i - ex)], r[i + ex :]))
    second = float(rest.max()) if rest.size else 0.0
    sharp = float(r[i] / (np.median(r) + 1e-12))
    return (i + frac) / sr, sharp, float(r[i] / max(second, 1e-12))

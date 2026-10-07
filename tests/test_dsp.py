import numpy as np

from lzsync import dsp


def test_gcc_phat_subsample():
    rng = np.random.default_rng(0)
    sr, d = 8000, 37.25
    a = rng.standard_normal(sr * 4)
    b = np.interp(np.arange(len(a)) + d, np.arange(len(a)), a)  # b[t] = a[t + d]
    lag, sharp, _ = dsp.gcc_phat(a, b, sr, 0.05)
    assert abs(lag - d / sr) < 0.1e-3
    assert sharp > 50


def test_masked_ncc_ignores_gaps():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(6000)
    m = np.ones(6000, bool)
    m[1000:3000] = False  # a hole in the long track
    f = dsp.Track(x, m)
    g = dsp.Track(x[3500:4300] + 0.3 * rng.standard_normal(800), np.ones(800, bool))
    lags, ncc, ov = dsp.masked_ncc(f, g, 100)
    p = dsp.peaks(lags, ncc, ov, 50)
    assert p[0]["lag"] == 3500 and p[0]["prominence"] > 5


def test_locate_short_template():
    rng = np.random.default_rng(2)
    sr = 8000
    long = rng.standard_normal(sr * 30)
    short = long[sr * 12 + 123 : sr * 13 + 123] + 0.2 * rng.standard_normal(sr)
    pos, sharp, uniq = dsp.locate(long, short, sr)
    assert abs(pos - (12 + 123 / sr)) < 1e-4
    assert uniq > 2

"""Synthetic shoot with known truth: offsets, drift, rec-run TC, midnight wrap, noise."""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

from .model import Clip

SR = 8000


def event(T: float, seed: int = 0) -> np.ndarray:
    """Something like a live event: speech-ish bursts, music, claps, room tone."""
    rng = np.random.default_rng(seed)
    n = int(T * SR)
    t = np.arange(n) / SR
    out = 0.02 * rng.standard_normal(n)
    # speech-like: band-limited noise gated by syllables, with pauses
    sos = butter(4, [250, 3000], "bandpass", fs=SR, output="sos")
    speech = sosfilt(sos, rng.standard_normal(n))
    gate = np.zeros(n)
    pos = 0
    while pos < n:
        if rng.random() < 0.15:
            pos += int(rng.uniform(0.3, 2.5) * SR)  # pause
            continue
        L = int(rng.uniform(0.08, 0.3) * SR)
        gate[pos : pos + L] = np.hanning(L)[: n - pos] * rng.uniform(0.3, 1.0)
        pos += L + int(rng.uniform(0.02, 0.12) * SR)
    out += 0.6 * speech * gate
    # music segments
    for _ in range(int(T // 60)):
        s = int(rng.uniform(0, T - 20) * SR)
        L = int(rng.uniform(5, 20) * SR)
        seg = np.zeros(L)
        k = 0
        while k < L:
            nl = int(rng.uniform(0.15, 0.6) * SR)
            f = 110 * 2 ** (rng.integers(0, 36) / 12)
            tt = np.arange(min(nl, L - k)) / SR
            seg[k : k + nl] = np.sin(2 * np.pi * f * tt) * np.exp(-tt * 3)
            k += nl
        out[s : s + L] += 0.3 * seg[: n - s]
    # claps
    for c in rng.uniform(0, T, int(T // 20)):
        s = int(c * SR)
        L = int(0.05 * SR)
        out[s : s + L] += rng.standard_normal(min(L, n - s)) * np.exp(-np.arange(min(L, n - s)) / (0.008 * SR))
    return out.astype(np.float64)


@dataclass
class Device:
    name: str
    mode: str  # free-run | rec-run | none
    takes: list[tuple[float, float]]  # (global start, length)
    tc_offset: float = 36000.0  # TC = global + tc_offset (free-run)
    drift_ppm: float = 0.0
    noise: float = 0.05
    lowpass: float | None = None
    echo: float = 0.0
    gain: float = 1.0
    unrelated: bool = False
    has_video: bool = True
    names: list[str] = field(default_factory=list)


def record(ev: np.ndarray, start: float, length: float, dev: Device, rng) -> np.ndarray:
    n = int(length * SR)
    # device clock runs (1 + ppm) fast: device sample k is global time start + k / (SR (1 + a))
    a = dev.drift_ppm * 1e-6
    tg = start + np.arange(n) / (SR * (1 + a))
    if dev.unrelated:
        x = event(length + 1, seed=int(rng.integers(1 << 30)))[:n]
    else:
        x = np.interp(tg * SR, np.arange(len(ev)), ev, left=0.0, right=0.0)
    if dev.echo:
        d = int(0.023 * SR)
        x = x + dev.echo * np.concatenate((np.zeros(d), x[:-d]))
    if dev.lowpass:
        x = sosfilt(butter(4, dev.lowpass, fs=SR, output="sos"), x)
    x = dev.gain * x + dev.noise * rng.standard_normal(n)
    return x


def build(folder: str, devices: list[Device], T: float, seed: int = 0):
    """Writes WAVs, returns (clips, truth) with truth[name] = global start of the file."""
    rng = np.random.default_rng(seed + 1)
    ev = event(T, seed)
    clips, truth = [], {}
    root = Path(folder)
    for dev in devices:
        (root / dev.name).mkdir(parents=True, exist_ok=True)
        run_tc = 3600.0
        for k, (s, L) in enumerate(dev.takes):
            name = dev.names[k] if k < len(dev.names) else f"{dev.name}_{k + 1:04d}.wav"
            x = record(ev, s, L, dev, rng)
            p = root / dev.name / name
            wavfile.write(p, SR, np.clip(x * 8000, -32768, 32767).astype(np.int16))
            dur = Fraction(len(x), SR)
            if dev.mode == "free-run":
                tc = (s * (1 + dev.drift_ppm * 1e-6) + dev.tc_offset) % 86400
            elif dev.mode == "rec-run":
                tc = run_tc
                run_tc += float(dur)
            else:
                tc = 0.0
            clips.append(Clip(id=f"r{len(clips) + 1}", name=name, device=dev.name, path=str(p),
                              tc_start=Fraction(tc).limit_denominator(SR * 100), duration=dur,
                              has_video=dev.has_video, has_audio=True))
            truth[name] = s
    return clips, truth

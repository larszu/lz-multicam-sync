"""Project-sized synthetic shoot (2 h, 9 devices, 76 files): runtime and accuracy.

    python benchmarks/scale.py /pfad/zum/arbeitsordner
"""
import sys, time, numpy as np
from lzsync import synth, engine, report
from lzsync.synth import Device
rng = np.random.default_rng(5)
T = 7200
def takes(n, lo, hi, start=60, gap=(5, 120)):
    t, out = start, []
    for _ in range(n):
        L = float(rng.uniform(lo, hi)); out.append((t, L)); t += L + float(rng.uniform(*gap))
        if t > T - 60: break
    return out
devs = [
  Device("TENTACLE_1", "free-run", [(0, T - 10)], noise=0.02, has_video=False),
  Device("TENTACLE_2", "free-run", [(2, T - 300)], tc_offset=36000.017, noise=0.03, has_video=False),
  Device("FX3_A", "rec-run", takes(16, 3, 300, gap=(2, 400)), drift_ppm=12, noise=0.08, echo=0.4),
  Device("FX3_B", "rec-run", takes(20, 20, 600), drift_ppm=-9, noise=0.08, lowpass=3000),
  Device("FX3_C", "free-run", takes(19, 30, 900, gap=(5, 60)), tc_offset=36085.3, drift_ppm=20, noise=0.08),
  Device("FX3_D", "free-run", [(30, 0.96), (50, 4.8)] + takes(14, 60, 1000, start=120), tc_offset=86400 - 1500, drift_ppm=25, noise=0.1),
  Device("A7IV", "rec-run", takes(4, 2.4, 3000), drift_ppm=5, noise=0.1, echo=0.6),
  Device("INSTA_01", "free-run", [(100, 0.2)] + takes(5, 600, 1900, start=200, gap=(1, 30)), tc_offset=20000, drift_ppm=-40, noise=0.2, lowpass=1500),
  Device("OTHER", "rec-run", [(0, 120)], unrelated=True),
]
t = time.time(); clips, truth = synth.build(sys.argv[1], devs, T, seed=11); print("gen", round(time.time() - t), "s,", len(clips), "clips")
t = time.time()
res = engine.run(clips, engine.Options(cache_dir=sys.argv[1] + "/.cache"), print)
print("run", round(time.time() - t), "s")
print(report.summary(res))
ref = next(r for r in res.clips if r.clip.device == "TENTACLE_1"); sh = ref.start - truth[ref.clip.name]
errs = {r.clip.name: (r.start - truth[r.clip.name] - sh) * 1000 for r in res.clips if r.start is not None and r.group == 0}
bad = {k: round(v, 1) for k, v in errs.items() if abs(v) > 1}
print("placed", len(errs), "of", len(clips), "| max err among audio:", round(max(abs(errs[r.clip.name]) for r in res.clips if r.method == "audio" and r.group == 0), 3), "ms | >1ms:", bad)

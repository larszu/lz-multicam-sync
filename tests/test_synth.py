import pytest

from lzsync import engine, synth
from lzsync.synth import Device


@pytest.fixture(scope="module")
def shoot(tmp_path_factory):
    folder = tmp_path_factory.mktemp("shoot")
    devs = [
        Device("REC", "free-run", [(0, 420)], noise=0.02, has_video=False),
        Device("CAM_A", "free-run", [(10, 150), (200, 200)], tc_offset=86400 - 100, drift_ppm=30,
               noise=0.08, lowpass=2500),  # TC rolls over midnight
        Device("CAM_B", "rec-run", [(5, 100), (120, 1.2), (130, 120), (300, 110)], drift_ppm=-10,
               noise=0.1, echo=0.5),
        Device("CAM_C", "free-run", [(-7000, 2.0), (50, 300)], tc_offset=20000, noise=0.1),
        Device("OTHER", "rec-run", [(0, 60)], unrelated=True),
    ]
    clips, truth = synth.build(str(folder), devs, 420, seed=7)
    res = engine.run(clips, engine.Options(cache_dir=str(folder / ".cache")), log=lambda s: None)
    return res, truth


def err_ms(res, truth):
    ref = next(r for r in res.clips if r.clip.device == "REC")
    sh = ref.start - truth[ref.clip.name]
    return {r.clip.name: (r.start - truth[r.clip.name] - sh) * 1000 if r.start is not None else None
            for r in res.clips}


def test_audio_sync_is_submillisecond(shoot):
    res, truth = shoot
    e = err_ms(res, truth)
    for name in ("CAM_A_0001.wav", "CAM_A_0002.wav", "CAM_B_0001.wav", "CAM_B_0003.wav",
                 "CAM_B_0004.wav", "CAM_C_0002.wav"):
        assert e[name] is not None and abs(e[name]) < 1.0, (name, e[name])


def test_drift_is_measured(shoot):
    res, _ = shoot
    assert abs(res.drift_ppm["CAM_A"] - 30) < 3


def test_short_clip_found_in_window(shoot):
    res, truth = shoot
    r = next(r for r in res.clips if r.clip.name == "CAM_B_0002.wav")
    assert r.method == "audio"
    assert abs(err_ms(res, truth)["CAM_B_0002.wav"]) < 2.0


def test_out_of_session_and_unrelated_stay_out(shoot):
    res, _ = shoot
    m = {r.clip.name: r for r in res.clips}
    assert m["CAM_C_0001.wav"].start is None  # two hours before the shoot
    assert m["OTHER_0001.wav"].start is None

from collections import defaultdict
from pathlib import Path

from lzsync import engine, fcpxml_io, timecode

FX = Path(__file__).parent / "fixtures"


def clips_by_dev():
    clips, _ = fcpxml_io.read(str(FX / "reference_input.fcpxml"))
    d = defaultdict(list)
    for c in clips:
        d[c.device].append(c)
    return clips, d


def test_read_reference_input():
    clips, d = clips_by_dev()
    assert len(clips) == 90
    assert len(d) == 11


def test_tc_mode_detection():
    _, d = clips_by_dev()
    modes = {k: timecode.tc_mode(v) for k, v in d.items()}
    # these cameras only advance TC while recording
    for dev in ("FX3_A", "FX3_B", "A7IV_FIXED"):
        assert modes[dev] == "rec-run", dev
    for dev in ("FX3_C", "FX3_D", "FX3_E", "INSTA_01", "TENTACLE_2"):
        assert modes[dev] == "free-run", dev


def test_midnight_wrap():
    _, d = clips_by_dev()
    cs = d["FX3_D"]
    timecode.unwrap(cs)
    loc = {c.name: c.local for c in cs}
    assert loc["D0008.MP4"] > loc["D0007.MP4"]
    assert abs(loc["D0008.MP4"] - 86400 - 1616.2) < 1e-6


def test_metadata_only_matches_reference_within_camera():
    """Without audio, free-run cameras keep the reference tool's internal layout (drift aside)."""
    clips, meta = fcpxml_io.read(str(FX / "reference_input.fcpxml"))
    res = engine.run(clips, engine.Options(use_audio=False), log=lambda s: None)
    ref = fcpxml_io.placements(str(FX / "reference_output.fcpxml"))
    for dev, tol in (("FX3_C", 0.25), ("FX3_E", 0.25)):
        rs = [r for r in res.clips if r.clip.device == dev]
        d = [r.start - ref[r.clip.name] for r in rs]
        assert max(d) - min(d) < tol, (dev, d)


def test_writer_roundtrip(tmp_path):
    clips, meta = fcpxml_io.read(str(FX / "reference_input.fcpxml"))
    res = engine.run(clips, engine.Options(use_audio=False), log=lambda s: None)
    out = tmp_path / "out.fcpxml"
    fcpxml_io.write(res, meta, str(out))
    got = fcpxml_io.placements(str(out))
    assert len(got) == 90
    main = [r for r in res.clips if r.group == 0 and r.start is not None]
    t0 = min(r.start for r in main)
    for r in main:
        tol = 0.021 if r.clip.has_video else 1 / 48000
        assert abs(got[r.clip.name] - (r.start - t0)) <= tol, r.clip.name


def test_reads_zipped_and_folder_bundle(tmp_path):
    import shutil
    import zipfile

    src = FX / "reference_input.fcpxml"
    bundle = tmp_path / "Demo.fcpxmld"
    bundle.mkdir()
    shutil.copy(src, bundle / "Info.fcpxml")
    z = tmp_path / "zipped.fcpxmld"  # what a download/AirDrop turns the bundle into
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(bundle / "Info.fcpxml", "Demo.fcpxmld/Info.fcpxml")
    for p in (bundle, z):
        clips, _ = fcpxml_io.read(str(p))
        assert len(clips) == 90

import xml.etree.ElementTree as ET
from fractions import Fraction

from lzsync import xmeml_io
from lzsync.model import Clip, ClipResult, SyncResult


def clip(id_, name, dev, dur, video=True, ch=2, w=3840, h=2160):
    return Clip(id=id_, name=name, device=dev, path=f"/m/{dev}/{name}", tc_start=Fraction(36000),
                duration=Fraction(dur), has_video=video, has_audio=True,
                frame_duration=Fraction(1, 25) if video else None, width=w if video else None,
                height=h if video else None, audio_channels=ch)


def result():
    rs = [ClipResult(clip("r1", "A001.mov", "CAM_A", 60), "i1", 10.0, "audio", 0.9),
          ClipResult(clip("r2", "REC_1.wav", "REC", 140, video=False, ch=6), "i2", 0.0, "audio", 0.9),
          ClipResult(clip("r3", "REC_1 copy.wav", "REC", 100, video=False, ch=6), "i3", 0.0, "audio", 0.9)]
    return SyncResult(rs, {}, {}, "REC")


def test_tracks_per_device_and_channel(tmp_path):
    p = tmp_path / "t.xml"
    xmeml_io.write(result(), str(p), name="t")
    root = ET.parse(p).getroot()
    video = root.find("sequence/media/video")
    audio = root.find("sequence/media/audio")
    assert len(video.findall("track")) == 1
    # recorder first: 6 channels, twice because the copy overlaps; then the camera's 2
    assert len(audio.findall("track")) == 6 + 6 + 2
    idx = [int(t.find("clipitem/sourcetrack/trackindex").text) for t in audio.findall("track")[:6]]
    assert idx == [1, 2, 3, 4, 5, 6]


def test_resolve_compatible_structure(tmp_path):
    p = tmp_path / "t.xml"
    xmeml_io.write(result(), str(p), name="t")
    root = ET.parse(p).getroot()
    video = root.find("sequence/media/video")
    assert [c.tag for c in video][-1] == "format"  # after the tracks, as Resolve writes it
    f = video.find("track/clipitem/file")
    assert f.find("timecode/string").text == "10:00:00:00"
    assert f.find("pathurl").text.startswith("file:///m/")
    cam = video.find("track/clipitem")
    assert int(cam.find("start").text) == 250  # 10 s at 25 fps
    assert len(cam.findall("link")) == 3  # picture + two sound channels

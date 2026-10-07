from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction


@dataclass
class Clip:
    """One media file. All times in seconds."""

    id: str
    name: str
    device: str
    path: str | None
    tc_start: Fraction | None  # media start as written by the camera (timecode)
    duration: Fraction
    has_video: bool = True
    has_audio: bool = True
    frame_duration: Fraction | None = None
    wallclock: float | None = None  # recording start, epoch seconds (device clock)
    xml: object | None = None  # original <asset> element when read from FCPXML
    local: float = 0.0  # unwrapped device time of the clip start
    width: int | None = None
    height: int | None = None
    audio_channels: int | None = None


@dataclass
class Island:
    """Clips whose relative timing is known (same free-running clock or one file)."""

    id: str
    device: str
    clips: list[Clip]
    t0: float  # device time of island start

    def rel(self, clip: Clip) -> float:
        return clip.local - self.t0

    @property
    def length(self) -> float:
        return max(self.rel(c) + float(c.duration) for c in self.clips)


@dataclass
class Point:
    """Audio evidence: island a at local time ta sounds like island b at local time tb."""

    a: str
    ta: float
    b: str
    tb: float
    sigma: float  # seconds
    kind: str = "fine"


@dataclass
class Prior:
    """Weak chronology evidence: start(b) - start(a) = delta (seconds of device time)."""

    a: str
    b: str
    delta: float
    sigma: float
    kind: str


@dataclass
class ClipResult:
    clip: Clip
    island: str
    start: float | None  # global timeline position of the clip start
    method: str  # audio | timecode | chronology | unplaced
    confidence: float
    note: str = ""
    group: int = 0  # 0 = main timeline; >0 = clips synced among themselves only
    window: tuple[float, float] | None = None


@dataclass
class SyncResult:
    clips: list[ClipResult]
    drift_ppm: dict[str, float]
    tc_mode: dict[str, str]
    reference: str
    edges: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

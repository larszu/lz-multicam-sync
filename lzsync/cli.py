"""lzsync analyze | compare | synth"""
from __future__ import annotations

import argparse
import os
import sys

from . import compare as cmpmod
from . import engine, fcpxml_io, media, report


def _remap(clips, pairs):
    for old, new in pairs:
        for c in clips:
            if c.path and c.path.startswith(old):
                c.path = new + c.path[len(old):]
                rep = c.xml.find("media-rep") if c.xml is not None else None
                if rep is not None:
                    from pathlib import Path
                    rep.set("src", Path(c.path).absolute().as_uri())


def analyze(inp: str, output: str | None = None, *, no_audio=False, reference=None, cache=None,
            channels="mix", split_gap=1800.0, jammed=(), remap=(), json_path=None, log=print,
            with_result=False, progress=None):
    """Shared by CLI and GUI. Returns (summary text, output path[, result, missing])."""
    if os.path.isdir(inp) and not inp.rstrip("/").endswith(".fcpxmld"):
        clips, meta = media.scan(inp), {"formats": {}, "event": os.path.basename(inp.rstrip("/"))}
    else:
        clips, meta = fcpxml_io.read(inp)
    _remap(clips, [m.split("=", 1) for m in remap])
    missing = [c for c in clips if not (c.path and os.path.exists(c.path))]
    if missing and not no_audio:
        log(f"  {len(missing)}/{len(clips)} Mediendateien nicht gefunden (z. B. {missing[0].path})")
    opt = engine.Options(use_audio=not no_audio, reference=reference or None, cache_dir=cache,
                         channels=channels, split_gap=split_gap, jammed=tuple(jammed),
                         progress=progress)
    log(f"{len(clips)} Clips, {len({c.device for c in clips})} Geräte")
    res = engine.run(clips, opt, log)
    stem = os.path.splitext(inp.rstrip("/"))[0]
    out = output or (stem + " - lzsync.fcpxml")
    fcpxml_io.write(res, meta, out)
    from . import xmeml_io  # Resolve/Premiere: explicit tracks per device and channel
    xmeml_io.write(res, os.path.splitext(out)[0] + ".xml", name=os.path.basename(stem) + " - lzsync")
    if json_path:
        report.to_json(res, json_path)
    text = report.summary(res)
    if missing and not no_audio:
        hint = (f"ACHTUNG: {len(missing)} von {len(clips)} Mediendateien nicht gefunden, "
                f"z. B. {missing[0].path}\nOhne Medien kein Audio-Abgleich – Laufwerk anschließen "
                "(oder Pfad mit --remap ALT=NEU umbiegen) und erneut starten.\n\n")
        text = hint + text
    if with_result:
        return text, out, res, (0 if no_audio else len(missing))
    return text, out


def cmd_analyze(a):
    log = (lambda s: None) if a.quiet else (lambda s: print(s, file=sys.stderr))
    text, out = analyze(a.input, a.output, no_audio=a.no_audio, reference=a.reference, cache=a.cache,
                        channels=a.channels, split_gap=a.split_gap,
                        jammed=[x for x in (a.jammed or "").split(",") if x],
                        remap=a.remap or [], json_path=a.json, log=log)
    print(text)
    print(f"\n→ {out}  (Final Cut Pro)")
    print(f"→ {os.path.splitext(out)[0]}.xml  (DaVinci Resolve, Premiere Pro)")


def cmd_compare(a):
    ours, ref = fcpxml_io.placements(a.ours), fcpxml_io.placements(a.reference)
    c = cmpmod.compare(ours, ref, a.anchor)
    print(cmpmod.format_report(c, a.fps, a.tol))


def cmd_synth(a):
    from . import synth
    devs = synth_demo()
    clips, truth = synth.build(a.folder, devs, a.length, a.seed)
    print(f"{len(clips)} Dateien in {a.folder}")


def synth_demo():
    from .synth import Device
    return [
        Device("REC", "free-run", [(5, 880)], noise=0.02, has_video=False),
        Device("CAM_A", "free-run", [(20, 200), (260, 300), (600, 280)], tc_offset=36012.34, drift_ppm=15,
               noise=0.08, lowpass=2500),
        Device("CAM_B", "rec-run", [(0, 90), (120, 1.0), (125, 150), (300, 240), (560, 300)],
               drift_ppm=-8, noise=0.1, echo=0.5),
    ]


def main(argv=None):
    p = argparse.ArgumentParser(prog="lzsync", description="Multicam-/Audio-Sync für die Postproduktion")
    sub = p.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("analyze", help="FCPXML/.fcpxmld oder Medienordner synchronisieren")
    an.add_argument("input")
    an.add_argument("-o", "--output")
    an.add_argument("--json", help="Sync-Map als JSON schreiben")
    an.add_argument("--remap", action="append", metavar="ALT=NEU", help="Pfadpräfix ersetzen (Laufwerk umbenannt)")
    an.add_argument("--reference", help="Gerät als Referenzuhr (z. B. TENTACLE_1)")
    an.add_argument("--jammed", help="Geräte mit gemeinsam gejammtem Timecode, kommagetrennt")
    an.add_argument("--no-audio", action="store_true", help="nur Timecode/Metadaten")
    an.add_argument("--channels", choices=["mix", "first"], default="mix")
    an.add_argument("--split-gap", type=float, default=1800.0)
    an.add_argument("--cache", help="Cache-Ordner für dekodiertes Audio")
    an.add_argument("-q", "--quiet", action="store_true")
    an.set_defaults(func=cmd_analyze)
    cp = sub.add_parser("compare", help="zwei synchronisierte FCPXML clipweise vergleichen")
    cp.add_argument("ours")
    cp.add_argument("reference")
    cp.add_argument("--anchor", nargs="*", help="Clips, auf die ausgerichtet wird")
    cp.add_argument("--fps", type=float, default=25.0)
    cp.add_argument("--tol", type=float, default=1.0, help="Toleranz in Frames")
    cp.set_defaults(func=cmd_compare)
    sy = sub.add_parser("synth", help="synthetischen Testdreh erzeugen")
    sy.add_argument("folder")
    sy.add_argument("--length", type=float, default=900.0)
    sy.add_argument("--seed", type=int, default=0)
    sy.set_defaults(func=cmd_synth)
    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()

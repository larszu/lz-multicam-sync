"""Window (HTML/CSS in a native webview); falls back to Tk if no webview is available."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from . import __version__

UI = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent)) / "lzsync" / "ui"
if not UI.exists():
    UI = Path(__file__).parent / "ui"


class Api:
    def __init__(self):
        self.window = None
        self.busy = False

    # called from JS -------------------------------------------------------
    def version(self):
        return __version__

    def pick_file(self):
        import webview
        r = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                           file_types=("FCPXML (*.fcpxml;*.fcpxmld)", "Alle Dateien (*.*)"))
        return r[0] if r else None

    def pick_folder(self):
        import webview
        r = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        return r[0] if r else None

    def exists(self, path):
        return bool(path) and os.path.exists(path)

    def run(self, opts: dict):
        if self.busy:
            return False
        self.busy = True
        threading.Thread(target=self._work, args=(opts,), daemon=True).start()
        return True

    def reveal(self, path):
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", path])
        elif os.name == "nt":
            subprocess.run(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.run(["xdg-open", os.path.dirname(path)])

    # worker ---------------------------------------------------------------
    def _emit(self, fn, payload):
        self.window.evaluate_js(f"window.app && app.{fn}({json.dumps(payload, ensure_ascii=False)})")

    def _work(self, o):
        from .cli import analyze
        from .report import overview
        try:
            remap = [o["remap"].strip()] if "=" in (o.get("remap") or "") else []
            text, out, res, missing = analyze(
                o["source"], no_audio=bool(o.get("noAudio")), reference=(o.get("reference") or "").strip(),
                channels="first" if o.get("firstChannel") else "mix",
                jammed=[x.strip() for x in (o.get("jammed") or "").split(",") if x.strip()],
                remap=remap, log=lambda s: self._emit("log", s), with_result=True,
                progress=lambda t, d: self._emit("progress", {"type": t, "data": d}))
            data = overview(res)
            data.update(output=out, output_xml=os.path.splitext(out)[0] + ".xml", missing=missing, text=text)
            self._emit("done", data)
        except Exception as e:  # shown in the window, never a crash
            self._emit("failed", str(e))
        finally:
            self.busy = False


def _on_drop(api, e):
    files = (e.get("dataTransfer") or {}).get("files") or []
    if files and files[0].get("pywebviewFullPath"):
        api._emit("dropped", files[0]["pywebviewFullPath"])


def main():
    try:
        import webview
    except Exception:
        from .gui_tk import main as tk_main
        return tk_main()
    api = Api()
    api.window = webview.create_window(
        f"LZ Multicam Sync {__version__}", url=str(UI / "index.html"), js_api=api,
        width=1080, height=760, min_size=(720, 520), background_color="#132040")

    def wire():
        try:
            from webview.dom import DOMEventHandler
            api.window.dom.document.events.drop += DOMEventHandler(lambda e: _on_drop(api, e), True, True)
        except Exception:
            pass  # drag & drop is a convenience; the pick buttons always work

    webview.start(wire)


if __name__ == "__main__":
    main()

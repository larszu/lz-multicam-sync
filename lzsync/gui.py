"""Minimal window: pick an FCPXML or a media folder, sync, open the result."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from . import __version__
from .cli import analyze


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"LZ Multicam Sync {__version__}")
        self.geometry("820x560")
        self.minsize(640, 420)
        self.q: queue.Queue = queue.Queue()
        self.out: str | None = None

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Quelle").grid(row=0, column=0, sticky="w")
        self.src = tk.StringVar()
        ttk.Entry(f, textvariable=self.src).grid(row=0, column=1, sticky="ew", padx=6)
        b = ttk.Frame(f)
        b.grid(row=0, column=2)
        ttk.Button(b, text="FCPXML …", command=self.pick_file).pack(side="left")
        ttk.Button(b, text="Ordner …", command=self.pick_dir).pack(side="left", padx=(4, 0))

        ttk.Label(f, text="Referenzgerät").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.ref = tk.StringVar()
        ttk.Entry(f, textvariable=self.ref).grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Label(f, text="optional, z. B. TENTACLE_1").grid(row=1, column=2, sticky="w", pady=(8, 0))

        ttk.Label(f, text="Gejammt").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.jam = tk.StringVar()
        ttk.Entry(f, textvariable=self.jam).grid(row=2, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Label(f, text="Geräte mit gemeinsamem TC, Komma").grid(row=2, column=2, sticky="w", pady=(8, 0))

        opts = ttk.Frame(f)
        opts.grid(row=3, column=1, sticky="w", padx=6, pady=(8, 0))
        self.first = tk.BooleanVar()
        self.noaudio = tk.BooleanVar()
        ttk.Checkbutton(opts, text="nur erster Audiokanal", variable=self.first).pack(side="left")
        ttk.Checkbutton(opts, text="ohne Audio (nur Timecode)", variable=self.noaudio).pack(side="left", padx=12)

        act = ttk.Frame(f)
        act.grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
        self.go = ttk.Button(act, text="Synchronisieren", command=self.start)
        self.go.pack(side="left")
        self.show = ttk.Button(act, text="Ergebnis im Finder zeigen", command=self.reveal, state="disabled")
        self.show.pack(side="left", padx=8)
        self.bar = ttk.Progressbar(act, mode="indeterminate", length=160)
        self.bar.pack(side="right")

        self.log = tk.Text(f, wrap="none", font=("Menlo" if sys.platform == "darwin" else "Consolas", 11))
        self.log.grid(row=5, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(5, weight=1)
        self.after(100, self.pump)

    def pick_file(self):
        p = filedialog.askopenfilename(filetypes=[("FCPXML", "*.fcpxml *.fcpxmld"), ("Alle", "*")])
        if p:
            self.src.set(p)

    def pick_dir(self):
        p = filedialog.askdirectory()
        if p:
            self.src.set(p)

    def write(self, s: str):
        self.q.put(s)

    def pump(self):
        while not self.q.empty():
            item = self.q.get()
            if isinstance(item, tuple):  # done
                self.bar.stop()
                self.go.configure(state="normal")
                self.out = item[1]
                if self.out:
                    self.show.configure(state="normal")
                continue
            self.log.insert("end", item + "\n")
            self.log.see("end")
        self.after(100, self.pump)

    def start(self):
        src = self.src.get().strip()
        if not src or not os.path.exists(src):
            self.write("Bitte eine FCPXML-Datei oder einen Medienordner wählen.")
            return
        self.log.delete("1.0", "end")
        self.go.configure(state="disabled")
        self.show.configure(state="disabled")
        self.bar.start(12)
        threading.Thread(target=self.work, args=(src,), daemon=True).start()

    def work(self, src):
        try:
            text, out = analyze(src, no_audio=self.noaudio.get(), reference=self.ref.get().strip(),
                                channels="first" if self.first.get() else "mix",
                                jammed=[x.strip() for x in self.jam.get().split(",") if x.strip()],
                                log=self.write)
            self.write("\n" + text + f"\n\n→ {out}")
            self.q.put(("done", out))
        except Exception as e:  # show, don't crash the window
            self.write(f"Fehler: {e}")
            self.q.put(("done", None))

    def reveal(self):
        if not self.out:
            return
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", self.out])
        elif os.name == "nt":
            subprocess.run(["explorer", "/select,", os.path.normpath(self.out)])
        else:
            subprocess.run(["xdg-open", os.path.dirname(self.out)])


def main():
    App().mainloop()


if __name__ == "__main__":
    main()

"""Die Demo-Ansicht der Oberfläche zeigt dieselbe Version wie das Paket."""
import re
from pathlib import Path

import lzsync

UI = Path(__file__).resolve().parent.parent / "lzsync" / "ui" / "app.js"


def test_demo_version_matches_package():
    m = re.search(r"const DEMO_VERSION = '([^']+)'", UI.read_text(encoding="utf-8"))
    assert m, "DEMO_VERSION fehlt in app.js"
    assert m.group(1) == lzsync.__version__

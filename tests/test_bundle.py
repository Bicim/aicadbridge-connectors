"""tools/bundle.py builds the release plugin zips with the shared aicad_link.py inside."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import bundle  # noqa: E402

EXPECTED = {
    "AICADBridgeLink-fusion.zip": "AICADBridgeLink/aicad_link.py",
    "aicadbridge_link-blender.zip": "aicad_link.py",
    "AICADBridgeLink-desktop.zip": "AICADBridgeLink/aicad_link.py",
    "AICADBridgeLink-rhino.zip": "AICADBridgeLink/aicad_link.py",
    "AICADBridgeLink-solidworks.zip": "AICADBridgeLink/aicad_link.py",
}


def test_bundle_names_and_live_server(tmp_path):
    made = {p.name for p in bundle.build(tmp_path)}
    assert made == set(EXPECTED)
    for name, member in EXPECTED.items():
        with zipfile.ZipFile(tmp_path / name) as z:
            assert member in z.namelist(), (name, z.namelist())
            link = z.read(member).decode("utf-8")
            assert "aicad.92-5-107-17.sslip.io" in link  # live default server
            assert not any("__pycache__" in n for n in z.namelist())

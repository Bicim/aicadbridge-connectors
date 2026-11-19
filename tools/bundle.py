"""Build the plugin zips into aicadbridge-web/downloads (common/aicad_link.py is copied into each)."""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "aicadbridge-web" / "downloads"
COMMON = ROOT / "common" / "aicad_link.py"

PACKAGES = {
    # zip name: (source dir, folder name inside the zip or "" for flat)
    "AICADBridgeLink-fusion.zip": (ROOT / "fusion" / "AICADBridgeLink", "AICADBridgeLink"),
    "aicadbridge_link-blender.zip": (ROOT / "blender" / "aicadbridge_link", ""),  # extension zips are flat
    "AICADBridgeLink-solidworks.zip": (ROOT / "solidworks", "AICADBridgeLink"),
}


def build() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    for name, (src, folder) in PACKAGES.items():
        dest = OUT / name
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(src.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts and f.name != "aicad_link.py":
                    z.write(f, str(Path(folder) / f.relative_to(src)) if folder else str(f.relative_to(src)))
            z.write(COMMON, str(Path(folder) / "aicad_link.py") if folder else "aicad_link.py")
        made.append(dest)
        print(f"{dest}  ({dest.stat().st_size // 1024} KB)")
    return made


if __name__ == "__main__":
    build()
    shutil.copy(ROOT / "README.md", OUT / "README.md") if (ROOT / "README.md").exists() else None

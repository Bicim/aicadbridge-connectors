"""Build the plugin zips (common/aicad_link.py is copied into each).

Usage: python tools/bundle.py [OUT_DIR]   (default: ../aicadbridge-web/downloads)
The release workflow attaches these zips to the GitHub release under exactly these names."""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT.parent / "aicadbridge-web" / "downloads"
COMMON = ROOT / "common" / "aicad_link.py"

PACKAGES = {
    # zip name: (source dir, folder name inside the zip or "" for flat)
    "AICADBridgeLink-fusion.zip": (ROOT / "fusion" / "AICADBridgeLink", "AICADBridgeLink"),
    "aicadbridge_link-blender.zip": (ROOT / "blender" / "aicadbridge_link", ""),  # extension zips are flat
    # generic COM bridge (run.bat <host>); the solidworks name is kept so existing web download links keep working
    "AICADBridgeLink-desktop.zip": (ROOT / "desktop", "AICADBridgeLink"),
    "AICADBridgeLink-solidworks.zip": (ROOT / "desktop", "AICADBridgeLink"),
    "AICADBridgeLink-rhino.zip": (ROOT / "rhino", "AICADBridgeLink"),
}


def build(out: Path | str | None = None) -> list[Path]:
    OUT = Path(out) if out else DEFAULT_OUT
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
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    build(out_dir)
    if (ROOT / "README.md").exists():
        shutil.copy(ROOT / "README.md", out_dir / "README.md")

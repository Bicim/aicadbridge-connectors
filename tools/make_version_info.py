"""Write a PyInstaller --version-file (Windows VERSIONINFO resource).

Usage: python tools/make_version_info.py <version> <out_file>
<version> may carry a leading "v" and any number of dotted parts (0.4.2 -> 0.4.2.0).
An exe with proper CompanyName/ProductName/FileDescription is less likely to be
flagged by reputation heuristics than one with an empty version resource.
"""
import re
import sys

COMPANY = "Bicim"
PRODUCT = "AICADBridge"
DESCRIPTION = "AICADBridge installer and CAD connector setup"
FILENAME = "AICADBridgeSetup.exe"


def parse_version(text):
    nums = [int(p) for p in re.findall(r"\d+", text.lstrip("vV").split("-")[0])][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def render(version):
    v = parse_version(version)
    dotted = ".".join(map(str, v))
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{COMPANY}'),
      StringStruct('FileDescription', '{DESCRIPTION}'),
      StringStruct('FileVersion', '{dotted}'),
      StringStruct('InternalName', 'AICADBridgeSetup'),
      StringStruct('LegalCopyright', 'Copyright (c) {COMPANY}'),
      StringStruct('OriginalFilename', '{FILENAME}'),
      StringStruct('ProductName', '{PRODUCT}'),
      StringStruct('ProductVersion', '{dotted}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        fh.write(render(sys.argv[1]))

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import make_version_info as mvi  # noqa: E402


def test_parse_version():
    assert mvi.parse_version("v0.4.2") == (0, 4, 2, 0)
    assert mvi.parse_version("1.2.3.4") == (1, 2, 3, 4)
    assert mvi.parse_version("0.5.0-rc1") == (0, 5, 0, 0)


def test_render_has_identity():
    text = mvi.render("v0.4.2")
    assert "filevers=(0, 4, 2, 0)" in text
    for needle in ("'CompanyName', 'Bicim'", "'ProductName', 'AICADBridge'", "'FileVersion', '0.4.2.0'"):
        assert needle in text
    compile(text.replace("# UTF-8", ""), "v", "eval")  # valid python expression body? (VSVersionInfo call syntax)

"""Pure-logic tests for installer/aicadbridge_setup.py (no GUI, no network)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))
import aicadbridge_setup as s  # noqa: E402


def no_reg(root, path):
    return False


def test_fusion_detection_via_addins_parent(tmp_path):
    env = {"APPDATA": str(tmp_path / "ap"), "LOCALAPPDATA": str(tmp_path / "lo")}
    assert not s.detect_fusion(env)["found"]
    (tmp_path / "ap" / "Autodesk" / "Autodesk Fusion 360" / "API").mkdir(parents=True)
    d = s.detect_fusion(env)
    assert d["found"] and d["addins_dir"].name == "AddIns"


def test_fusion_detection_via_exe(tmp_path):
    exe = tmp_path / "lo" / "Autodesk" / "webdeploy" / "production" / "abc123" / "Fusion360.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    d = s.detect_fusion({"APPDATA": str(tmp_path / "ap"), "LOCALAPPDATA": str(tmp_path / "lo")})
    assert d["found"] and d["exe"].endswith("Fusion360.exe")


def test_blender_picks_newest_version(tmp_path):
    base = tmp_path / "Blender Foundation" / "Blender"
    for v in ("3.6", "4.2", "4.10", "notaversion"):
        (base / v).mkdir(parents=True)
    d = s.detect_blender({"APPDATA": str(tmp_path)}, reg=no_reg)
    assert d["found"] and d["version"] == "4.10"
    assert d["addons_dir"] == base / "4.10" / "scripts" / "addons"


def test_blender_from_program_files_and_registry(tmp_path):
    exe = tmp_path / "pf" / "Blender Foundation" / "Blender 4.2" / "blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    d = s.detect_blender({"APPDATA": str(tmp_path / "ap"), "ProgramFiles": str(tmp_path / "pf")}, reg=no_reg)
    assert d["found"] and d["version"] == "4.2"
    only_reg = s.detect_blender({"APPDATA": str(tmp_path / "ap")}, reg=lambda r, p: r == "HKCU")
    assert only_reg["found"] and only_reg["addons_dir"] is None


def test_solidworks_registry_and_safe_default(tmp_path):
    assert s.detect_solidworks({"LOCALAPPDATA": str(tmp_path)}, reg=lambda r, p: p == r"SOFTWARE\SolidWorks")["found"]
    assert not s.detect_solidworks({}, reg=no_reg)["found"]
    assert s.reg_key_exists("HKLM", r"SOFTWARE\DefinitelyNotThereAICAD") is False  # must not raise


def test_empty_env_detects_nothing():
    d = s.detect_all({}, reg=no_reg)
    assert not any(v["found"] for v in d.values())


def test_version_compare():
    assert s.plugin_status(None) == "missing"
    assert s.plugin_status("0.2.0", "0.3.0") == "outdated"
    assert s.plugin_status("0.3.0", "0.3.0") == "current"
    assert s.plugin_status("0.10.0", "0.9.0") == "current"
    assert s.version_tuple("0.3.0") == (0, 3, 0)


def test_current_version_matches_common():
    assert s.VERSION == "0.3.0"


def test_install_fusion_overwrites_cleanly(tmp_path):
    addins = tmp_path / "AddIns"
    stale = addins / "AICADBridgeLink" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    dest = s.install_fusion(addins)
    assert not stale.exists()
    assert (dest / "AICADBridgeLink.py").exists() and (dest / "AICADBridgeLink.manifest").exists()
    assert (dest / "aicad_link.py").read_text(encoding="utf-8") == (ROOT / "common" / "aicad_link.py").read_text(encoding="utf-8")
    assert s.installed_version(dest) == s.VERSION
    assert not list(dest.rglob("__pycache__"))
    assert json.loads((dest / "AICADBridgeLink.manifest").read_text(encoding="utf-8"))["runOnStartup"] is True


def test_install_blender_and_solidworks(tmp_path):
    b = s.install_blender(tmp_path / "addons")
    assert (b / "__init__.py").exists() and (b / "aicad_link.py").exists()
    assert not (b / "blender_manifest.toml").exists()
    desk = tmp_path / "Desktop"
    desk.mkdir()
    w = s.install_solidworks(tmp_path / "sw", desktop=desk)
    assert (w / "run.bat").exists() and (w / "aicadbridge_sw_link.py").exists() and (w / "aicad_link.py").exists()
    assert "run.bat" in (desk / "AICADBridge SolidWorks.bat").read_text(encoding="utf-8")


def test_manifest_and_default_server():
    import aicad_link
    assert aicad_link.DEFAULT_SERVER == "https://aicad.92-5-107-17.sslip.io" or "AICAD_API_URL" in __import__("os").environ


def test_state_file_and_connection_without_network(tmp_path):
    assert s.check_connection("fusion", state_dir=tmp_path) == "disconnected"
    assert not s.has_token("fusion", state_dir=tmp_path)
    p = s.state_path("fusion", tmp_path)
    p.write_text(json.dumps({"server": "http://127.0.0.1:1", "token": "t", "device_id": "d"}), encoding="utf-8")
    assert s.has_token("fusion", state_dir=tmp_path)
    assert s.check_connection("fusion", state_dir=tmp_path) == "unreachable"  # closed port, no real network


def test_pair_blocking_writes_state(tmp_path, monkeypatch):
    calls = []

    def fake_req(self, method, path, body=None, auth=False, timeout=30):
        calls.append(path)
        if path == "/v1/connect/start":
            return 200, {"device_code": "dc", "user_code": "ABCD-1234", "verification_url": "https://x/v", "interval": 1}
        return 200, {"token": "tok", "device_id": "dev"}

    monkeypatch.setattr(s.aicad_link.LinkClient, "_req", fake_req)
    codes, opened = [], []
    assert s.pair_blocking("blender", codes.append, state_dir=tmp_path, open_browser=opened.append, interval=0)
    assert codes == ["ABCD-1234"] and opened == ["https://x/v"]
    saved = json.loads(s.state_path("blender", tmp_path).read_text(encoding="utf-8"))
    assert saved["token"] == "tok" and saved["device_id"] == "dev"


def test_friendly_errors():
    E = s.aicad_link.LinkError
    assert "Sunucuya" in s.friendly_error(E(0, "x"))
    assert "süresi" in s.friendly_error(E(410, "gone"))
    assert s.friendly_error(E(403, "Ücretsiz planda 1 cihaz")) == "Ücretsiz planda 1 cihaz"

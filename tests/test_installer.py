"""Pure-logic tests for installer/aicadbridge_setup.py (no GUI, no network)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))
sys.path.insert(0, str(ROOT / "desktop"))
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


def test_empty_env_detects_nothing_but_step_folder():
    d = s.detect_all({}, reg=no_reg, subkeys=lambda r, p: [])
    assert [h for h, v in d.items() if v["found"]] == ["other"]  # the STEP folder is always available
    assert set(d) == set(s.HOSTS)


def test_other_row_always_found_with_install_dir(tmp_path):
    d = s.detect_other({"LOCALAPPDATA": str(tmp_path)})
    assert d["found"] and d["install_dir"] == tmp_path / "AICADBridge" / "desktop" / "other"


@pytest.mark.parametrize("detect,key", [
    (s.detect_solidworks, r"SOFTWARE\SolidWorks"),
    (s.detect_inventor, r"SOFTWARE\Autodesk\Inventor"),
    (s.detect_solidedge, r"SOFTWARE\Unigraphics Solutions\Solid Edge"),
    (s.detect_solidedge, r"SOFTWARE\Siemens\Solid Edge"),
    (s.detect_autocad, r"SOFTWARE\Autodesk\AutoCAD"),
    (s.detect_rhino, r"SOFTWARE\McNeel\Rhinoceros\8.0"),
])
def test_registry_detection(tmp_path, detect, key):
    env = {"LOCALAPPDATA": str(tmp_path / "lo"), "APPDATA": str(tmp_path / "ap")}
    assert not detect(env, reg=no_reg)["found"]
    assert detect(env, reg=lambda r, p: r == "HKLM" and p == key)["found"]
    assert not detect(env, reg=lambda r, p: p == key + "X")["found"]


def test_detection_via_program_files(tmp_path):
    pf = tmp_path / "pf"
    for rel in ("Autodesk/Inventor 2025/Bin/Inventor.exe", "Autodesk/AutoCAD 2025/acad.exe",
                "Siemens/Solid Edge 2024/Program/Edge.exe", "Rhino 8/System/Rhino.exe",
                "Dassault Systemes/B28/win_b64/code/bin/CNEXT.exe"):
        f = pf / rel
        f.parent.mkdir(parents=True)
        f.write_bytes(b"")
    env = {"ProgramFiles": str(pf), "LOCALAPPDATA": str(tmp_path / "lo"), "APPDATA": str(tmp_path / "ap")}
    d = s.detect_all(env, reg=no_reg, subkeys=lambda r, p: [])
    for h in ("inventor", "autocad", "solidedge", "rhino", "catia"):
        assert d[h]["found"] and d[h]["exe"].endswith(".exe"), h
    assert not d["solidworks"]["found"]


def test_catia_detected_from_b_release_subkeys(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path)}

    def subkeys(root, path):
        return ["Other Product", "B28"] if (root, path) == ("HKLM", r"SOFTWARE\Dassault Systemes") else []

    assert s.detect_catia(env, reg=no_reg, subkeys=subkeys)["found"]
    assert not s.detect_catia(env, reg=no_reg, subkeys=lambda r, p: ["DELMIA", "V6R2020"])["found"]
    assert s.reg_subkeys("HKLM", r"SOFTWARE\DefinitelyNotThereAICAD") == []  # must not raise


def test_rhino_scripts_dir(tmp_path):
    d = s.detect_rhino({"APPDATA": str(tmp_path)}, reg=lambda r, p: True)
    assert d["install_dir"] == tmp_path / "McNeel" / "Rhinoceros" / "8.0" / "scripts"


def test_version_compare():
    assert s.plugin_status(None) == "missing"
    assert s.plugin_status("0.3.0", "0.4.0") == "outdated"
    assert s.plugin_status("0.4.0", "0.4.0") == "current"
    assert s.plugin_status("0.10.0", "0.9.0") == "current"
    assert s.version_tuple("0.3.0") == (0, 3, 0)


def test_current_version_matches_common():
    assert s.VERSION == "0.4.0"


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


def test_install_blender(tmp_path):
    b = s.install_blender(tmp_path / "addons")
    assert (b / "__init__.py").exists() and (b / "aicad_link.py").exists()
    assert not (b / "blender_manifest.toml").exists()


@pytest.mark.parametrize("host", s.COM_HOSTS)
def test_install_desktop_host_with_python_runner(tmp_path, host):
    desk = tmp_path / "Desktop"
    desk.mkdir()
    install_dir = tmp_path / "AICADBridge" / "desktop" / host
    d = s.install_desktop_host(host, install_dir, desktop=desk)
    shared = install_dir.parent
    assert d == install_dir and s.installed_version(d) == s.VERSION
    assert (shared / "aicadbridge_desktop.py").exists() and (shared / "aicad_link.py").exists()
    bat = desk / f"AICADBridge {s.SHORT_NAME[host]}.bat"
    text = bat.read_bytes().decode("utf-8")
    assert "\r\r" not in text and f"--host {host}" in text and "pythonw" in text and str(shared / "aicadbridge_desktop.py") in text
    assert (install_dir / bat.name).read_bytes().decode("utf-8") == text
    assert not (tmp_path / "AICADBridge" / "AICADBridge.exe").exists()


def test_install_desktop_host_with_bundled_exe(tmp_path):
    fake_exe = tmp_path / "setup.exe"
    fake_exe.write_bytes(b"MZ-new")
    root = tmp_path / "AICADBridge"
    exe_dest = root / "AICADBridge.exe"
    exe_dest.parent.mkdir(parents=True)
    exe_dest.write_bytes(b"MZ-old")
    desk = tmp_path / "Desktop"
    desk.mkdir()
    s.install_desktop_host("inventor", root / "desktop" / "inventor", desktop=desk, exe=fake_exe, exe_dest=exe_dest)
    assert exe_dest.read_bytes() == b"MZ-new"
    bat = (desk / "AICADBridge Inventor.bat").read_bytes().decode("utf-8")  # bytes: no \r\r\n
    assert bat == f'@echo off\r\nchcp 65001 >nul\r\nstart "" "{exe_dest}" --bridge inventor\r\n'
    # re-installing from the already installed exe must not fail or truncate it
    s.install_desktop_host("catia", root / "desktop" / "catia", exe=exe_dest, exe_dest=exe_dest)
    assert exe_dest.read_bytes() == b"MZ-new"


def test_install_desktop_host_rejects_unknown_host(tmp_path):
    with pytest.raises(ValueError):
        s.install_desktop_host("fusion", tmp_path / "x")


def test_install_rhino(tmp_path):
    scripts = tmp_path / "McNeel" / "Rhinoceros" / "8.0" / "scripts"
    d = s.install_rhino(scripts)
    assert (d / "AICADBridgeLink.py").exists() and (d / "aicad_link.py").exists()
    assert s.installed_version(d) == s.VERSION


def test_target_for_and_install_host_dispatch(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path / "lo"), "APPDATA": str(tmp_path / "ap"), "USERPROFILE": str(tmp_path / "up")}
    (tmp_path / "up" / "Desktop").mkdir(parents=True)
    det = s.detect_all(env, reg=lambda r, p: True, subkeys=lambda r, p: ["B28"])
    for host in s.COM_HOSTS:
        assert s.target_for(host, det) == tmp_path / "lo" / "AICADBridge" / "desktop" / host
    assert s.target_for("rhino", det) == s.rhino_scripts_dir(env)
    for host in ("inventor", "solidedge", "catia", "autocad", "solidworks", "other", "rhino"):
        dest = s.install_host(host, det, env)
        assert s.installed_version(dest) == s.VERSION
    assert (tmp_path / "up" / "Desktop" / "AICADBridge STEP klasörü.bat").exists()
    assert (tmp_path / "up" / "Desktop" / "AICADBridge Solid Edge.bat").exists()
    with pytest.raises(ValueError):
        s.install_host("nope", det, env)


def test_every_host_has_label_and_note():
    assert set(s.HOST_LABEL) == set(s.HOSTS)
    assert set(s.BETA_HOSTS) <= set(s.HOSTS) and "solidworks" not in s.BETA_HOSTS
    for h in s.HOSTS:
        assert s.install_note_text(h)


def test_bridge_cli_dispatch(monkeypatch):
    seen = {}
    import aicadbridge_desktop as d
    monkeypatch.setattr(d, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    assert s.main(["--bridge", "other", "--no-browser"]) == 0
    assert seen["argv"] == ["--host", "other", "--no-browser"]
    assert s.run_bridge_cli([]) == 2


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

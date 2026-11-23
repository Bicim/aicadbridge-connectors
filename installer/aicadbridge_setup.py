"""AICADBridge Kurulumu — one-window installer + pairing helper for Fusion, Blender, SolidWorks, Inventor,
Solid Edge, CATIA V5, AutoCAD, Rhino 8 and a generic "STEP klasörü".

`AICADBridgeSetup.exe --bridge <host>` runs the desktop bridge headless (no installer window).

Pure logic lives at the top (testable without a GUI); the tkinter app is at the bottom.
Frozen with PyInstaller the plugin payloads are read from sys._MEIPASS, otherwise from the repo.
"""
from __future__ import annotations

import glob
import os
import queue
import re
import shutil
import sys
import threading
import webbrowser
from pathlib import Path

# --------------------------------------------------------------------------- payloads


def payload_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


ROOT = payload_root()
sys.path.insert(0, str(ROOT / "common"))
import aicad_link  # noqa: E402

VERSION = aicad_link.VERSION
VERSION_MARKER = ".aicad_version"
HOSTS = ("fusion", "blender", "solidworks", "inventor", "solidedge", "catia", "autocad", "rhino", "other")
HOST_LABEL = {"fusion": "Autodesk Fusion", "blender": "Blender", "solidworks": "SolidWorks",
              "inventor": "Autodesk Inventor", "solidedge": "Siemens Solid Edge", "catia": "CATIA V5",
              "autocad": "AutoCAD", "rhino": "Rhino 8", "other": "STEP klasörü (diğer programlar)"}
# short names for desktop shortcuts ("AICADBridge <name>.bat")
SHORT_NAME = {"solidworks": "SolidWorks", "inventor": "Inventor", "solidedge": "Solid Edge", "catia": "CATIA",
              "autocad": "AutoCAD", "other": "STEP klasörü"}
COM_HOSTS = tuple(SHORT_NAME)  # served by desktop/aicadbridge_desktop.py (COM or STEP inbox)
BETA_HOSTS = ("inventor", "solidedge", "catia", "autocad", "rhino")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def _env(env=None):
    return os.environ if env is None else env


# --------------------------------------------------------------------------- registry (safe)


def reg_key_exists(root_name: str, path: str) -> bool:
    """True when the HKLM/HKCU key exists. Never raises (non-Windows, missing key, access denied)."""
    try:
        import winreg
        root = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}[root_name]
        with winreg.OpenKey(root, path):
            return True
    except Exception:
        return False


def reg_subkeys(root_name: str, path: str) -> list[str]:
    """Sub-key names of an HKLM/HKCU key. Never raises; [] when missing."""
    try:
        import winreg
        root = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}[root_name]
        out = []
        with winreg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
        return out
    except Exception:
        return []


# --------------------------------------------------------------------------- detection


def fusion_addins_dir(env=None) -> Path | None:
    appdata = _env(env).get("APPDATA")
    return Path(appdata) / "Autodesk" / "Autodesk Fusion 360" / "API" / "AddIns" if appdata else None


def detect_fusion(env=None) -> dict:
    e = _env(env)
    addins = fusion_addins_dir(e)
    local = e.get("LOCALAPPDATA")
    exes = glob.glob(str(Path(local) / "Autodesk" / "webdeploy" / "production" / "*" / "Fusion360.exe")) if local else []
    found = bool(exes) or bool(addins and addins.parent.exists())
    return {"found": found, "addins_dir": addins, "exe": exes[0] if exes else None}


def _ver_key(name: str):
    return tuple(int(x) for x in re.findall(r"\d+", name)) or (0,)


def blender_versions(env=None) -> list[str]:
    """Config-folder versions under %APPDATA%/Blender Foundation/Blender, newest first."""
    appdata = _env(env).get("APPDATA")
    if not appdata:
        return []
    base = Path(appdata) / "Blender Foundation" / "Blender"
    if not base.is_dir():
        return []
    vs = [p.name for p in base.iterdir() if p.is_dir() and re.fullmatch(r"\d+\.\d+", p.name)]
    return sorted(vs, key=_ver_key, reverse=True)


def detect_blender(env=None, reg=reg_key_exists) -> dict:
    e = _env(env)
    versions = blender_versions(e)
    exes = []
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        pf = e.get(var)
        if pf:
            exes += glob.glob(str(Path(pf) / "Blender Foundation" / "*" / "blender.exe"))
    reg_hit = any(reg(r, r"SOFTWARE\BlenderFoundation") for r in ("HKLM", "HKCU"))
    if not versions:  # derive from the install folder name, e.g. "Blender 4.2"
        for x in exes:
            m = re.search(r"(\d+\.\d+)", Path(x).parent.name)
            if m:
                versions.append(m.group(1))
        versions.sort(key=_ver_key, reverse=True)
    appdata = e.get("APPDATA")
    version = versions[0] if versions else None
    addons = (Path(appdata) / "Blender Foundation" / "Blender" / version / "scripts" / "addons"
              if appdata and version else None)
    return {"found": bool(versions or exes or reg_hit), "version": version, "addons_dir": addons,
            "exe": exes[0] if exes else None}


def bridge_root(env=None) -> Path | None:
    local = _env(env).get("LOCALAPPDATA")
    return Path(local) / "AICADBridge" if local else None


def desktop_dir(env=None) -> Path | None:
    """Shared folder of the desktop bridge (script + aicad_link.py)."""
    r = bridge_root(env)
    return r / "desktop" if r else None


def desktop_host_dir(host: str, env=None) -> Path | None:
    """Per-host folder (version marker + shortcut .bat) inside the shared desktop folder."""
    d = desktop_dir(env)
    return d / host if d else None


def bridge_exe_path(env=None) -> Path | None:
    r = bridge_root(env)
    return r / "AICADBridge.exe" if r else None


def _program_files_glob(e, *patterns: str) -> list[str]:
    out: list[str] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        pf = e.get(var)
        if pf:
            for pat in patterns:
                out += glob.glob(str(Path(pf) / pat))
    return out


def _reg_any(reg, *paths: str) -> bool:
    return any(reg(r, p) for p in paths for r in ("HKLM", "HKCU"))


def _com_result(host: str, e, found: bool, exes: list[str]) -> dict:
    return {"found": bool(found), "install_dir": desktop_host_dir(host, e), "exe": exes[0] if exes else None}


def detect_solidworks(env=None, reg=reg_key_exists) -> dict:
    return _com_result("solidworks", _env(env), _reg_any(reg, r"SOFTWARE\SolidWorks"), [])


def detect_inventor(env=None, reg=reg_key_exists) -> dict:
    e = _env(env)
    exes = _program_files_glob(e, "Autodesk/Inventor */Bin/Inventor.exe")
    return _com_result("inventor", e, exes or _reg_any(reg, r"SOFTWARE\Autodesk\Inventor"), exes)


def detect_solidedge(env=None, reg=reg_key_exists) -> dict:
    e = _env(env)
    exes = _program_files_glob(e, "Siemens/Solid Edge*/Program/Edge.exe")
    hit = exes or _reg_any(reg, r"SOFTWARE\Unigraphics Solutions\Solid Edge", r"SOFTWARE\Siemens\Solid Edge")
    return _com_result("solidedge", e, hit, exes)


def detect_catia(env=None, reg=reg_key_exists, subkeys=reg_subkeys) -> dict:
    """CATIA V5 registers release folders such as B28 under SOFTWARE\\Dassault Systemes."""
    e = _env(env)
    exes = _program_files_glob(e, "Dassault Systemes/B*/win_b64/code/bin/CNEXT.exe")
    bases = (r"SOFTWARE\Dassault Systemes", r"SOFTWARE\WOW6432Node\Dassault Systemes")
    hit = bool(exes) or any(re.fullmatch(r"B\d+", k, re.I) for b in bases for r in ("HKLM", "HKCU")
                            for k in subkeys(r, b))
    return _com_result("catia", e, hit, exes)


def detect_autocad(env=None, reg=reg_key_exists) -> dict:
    e = _env(env)
    exes = _program_files_glob(e, "Autodesk/AutoCAD */acad.exe")
    return _com_result("autocad", e, exes or _reg_any(reg, r"SOFTWARE\Autodesk\AutoCAD"), exes)


def rhino_scripts_dir(env=None) -> Path | None:
    appdata = _env(env).get("APPDATA")
    return Path(appdata) / "McNeel" / "Rhinoceros" / "8.0" / "scripts" if appdata else None


def detect_rhino(env=None, reg=reg_key_exists) -> dict:
    e = _env(env)
    exes = _program_files_glob(e, "Rhino 8/System/Rhino.exe")
    found = bool(exes) or _reg_any(reg, r"SOFTWARE\McNeel\Rhinoceros\8.0")
    return {"found": found, "install_dir": rhino_scripts_dir(e), "exe": exes[0] if exes else None}


def detect_other(env=None) -> dict:
    """"STEP klasörü" works with any program, so it is always available."""
    return _com_result("other", _env(env), True, [])


def detect_all(env=None, reg=reg_key_exists, subkeys=reg_subkeys) -> dict:
    return {"fusion": detect_fusion(env), "blender": detect_blender(env, reg),
            "solidworks": detect_solidworks(env, reg), "inventor": detect_inventor(env, reg),
            "solidedge": detect_solidedge(env, reg), "catia": detect_catia(env, reg, subkeys),
            "autocad": detect_autocad(env, reg), "rhino": detect_rhino(env, reg), "other": detect_other(env)}


# --------------------------------------------------------------------------- versions


def installed_version(target: Path | None) -> str | None:
    try:
        return (Path(target) / VERSION_MARKER).read_text(encoding="utf-8").strip() or None
    except (OSError, TypeError):
        return None


def version_tuple(v: str | None) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")) or ()


def plugin_status(installed: str | None, current: str = VERSION) -> str:
    """'missing' | 'outdated' | 'current'."""
    if not installed:
        return "missing"
    return "outdated" if version_tuple(installed) < version_tuple(current) else "current"


# --------------------------------------------------------------------------- install


def _fresh_copy(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest, ignore=IGNORE)
    shutil.copy2(ROOT / "common" / "aicad_link.py", dest / "aicad_link.py")
    (dest / VERSION_MARKER).write_text(VERSION, encoding="utf-8")


def fusion_target(addins_dir: Path) -> Path:
    return Path(addins_dir) / "AICADBridgeLink"


def install_fusion(addins_dir: Path, root: Path | None = None) -> Path:
    dest = fusion_target(addins_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _fresh_copy((root or ROOT) / "fusion" / "AICADBridgeLink", dest)
    return dest


def blender_target(addons_dir: Path) -> Path:
    return Path(addons_dir) / "aicadbridge_link"


def install_blender(addons_dir: Path, root: Path | None = None) -> Path:
    dest = blender_target(addons_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _fresh_copy((root or ROOT) / "blender" / "aicadbridge_link", dest)
    # legacy add-on loading uses bl_info; the extension manifest is not needed here
    (dest / "blender_manifest.toml").unlink(missing_ok=True)
    return dest


def bridge_bat(host: str, exe: Path | None = None, script: Path | None = None) -> str:
    """Shortcut script that starts the desktop bridge detached: the bundled exe when available,
    otherwise pythonw (no console window) with a plain python fallback."""
    # chcp 65001: the file is UTF-8 so non-ASCII user-profile paths survive
    if exe is not None:
        return f'@echo off\r\nchcp 65001 >nul\r\nstart "" "{exe}" --bridge {host}\r\n'
    return ("@echo off\r\n"
            "chcp 65001 >nul\r\n"
            "where pythonw >nul 2>nul\r\n"
            "if %errorlevel%==0 (\r\n"
            f'  start "" pythonw "{script}" --host {host} --headless\r\n'
            ") else (\r\n"
            f'  python "{script}" --host {host}\r\n'
            ")\r\n")


def _copy_exe(src: Path, dest: Path) -> bool:
    """Copy the frozen exe to its permanent location. False when it could not be refreshed
    (e.g. the previous copy is running and locked) but an older copy exists."""
    src, dest = Path(src), Path(dest)
    try:
        if dest.exists() and src.resolve() == dest.resolve():
            return True
    except OSError:
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".exe.new")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if dest.exists():
            return False
        raise


def install_desktop_host(host: str, install_dir: Path, root: Path | None = None, desktop: Path | None = None,
                         exe: Path | None = None, exe_dest: Path | None = None) -> Path:
    """Install the generic desktop bridge for one COM host (or the STEP folder).

    Shared files go to install_dir.parent (aicadbridge_desktop.py, aicad_link.py, run.bat); install_dir holds the
    version marker and the shortcut .bat. With `exe` (the frozen installer) the shortcut starts
    `AICADBridge.exe --bridge <host>`, so no Python is needed on the user's PC."""
    if host not in COM_HOSTS:
        raise ValueError(host)
    src = (root or ROOT) / "desktop"
    dest = Path(install_dir)
    shared = dest.parent
    shared.mkdir(parents=True, exist_ok=True)
    for name in ("aicadbridge_desktop.py", "run.bat"):
        shutil.copy2(src / name, shared / name)
    shutil.copy2(ROOT / "common" / "aicad_link.py", shared / "aicad_link.py")
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    runner = None
    if exe is not None:
        target_exe = Path(exe_dest) if exe_dest else shared.parent / "AICADBridge.exe"
        _copy_exe(Path(exe), target_exe)
        runner = target_exe
    bat = bridge_bat(host, exe=runner, script=shared / "aicadbridge_desktop.py")
    name = f"AICADBridge {SHORT_NAME[host]}.bat"
    (dest / name).write_bytes(bat.encode("utf-8"))  # bytes: write_text would turn \r\n into \r\r\n on Windows
    (dest / VERSION_MARKER).write_text(VERSION, encoding="utf-8")
    if desktop is not None and Path(desktop).is_dir():
        (Path(desktop) / name).write_bytes(bat.encode("utf-8"))
    return dest


def install_rhino(scripts_dir: Path, root: Path | None = None) -> Path:
    """Rhino 8 script + aicad_link.py into the Rhino scripts folder (run with RunPythonScript)."""
    dest = Path(scripts_dir)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2((root or ROOT) / "rhino" / "AICADBridgeLink.py", dest / "AICADBridgeLink.py")
    shutil.copy2(ROOT / "common" / "aicad_link.py", dest / "aicad_link.py")
    (dest / VERSION_MARKER).write_text(VERSION, encoding="utf-8")
    return dest


def target_for(host: str, det: dict) -> Path | None:
    if host == "fusion" and det["fusion"]["addins_dir"]:
        return fusion_target(det["fusion"]["addins_dir"])
    if host == "blender" and det["blender"]["addons_dir"]:
        return blender_target(det["blender"]["addons_dir"])
    if host in COM_HOSTS or host == "rhino":
        return det[host]["install_dir"]
    return None


def install_host(host: str, det: dict, env=None) -> Path:
    if host == "fusion":
        return install_fusion(det["fusion"]["addins_dir"])
    if host == "blender":
        return install_blender(det["blender"]["addons_dir"])
    if host == "rhino":
        return install_rhino(det["rhino"]["install_dir"])
    if host in COM_HOSTS:
        e = _env(env)
        up = e.get("USERPROFILE")
        exe = Path(sys.executable) if getattr(sys, "frozen", False) else None
        return install_desktop_host(host, det[host]["install_dir"], desktop=Path(up) / "Desktop" if up else None,
                                    exe=exe, exe_dest=bridge_exe_path(e))
    raise ValueError(host)


# --------------------------------------------------------------------------- state / pairing


def state_path(host: str, state_dir=None) -> Path:
    return Path(state_dir or Path.home() / ".aicadbridge") / f"{host}.json"


def has_token(host: str, state_dir=None) -> bool:
    return aicad_link.LinkClient(host, state_dir=state_dir).paired


def check_connection(host: str, state_dir=None) -> str:
    """'connected' | 'disconnected' | 'unreachable'. Makes a network call: run off the UI thread."""
    c = aicad_link.LinkClient(host, state_dir=state_dir)
    if not c.paired:
        return "disconnected"
    try:
        c.me()
        return "connected"
    except aicad_link.LinkError as e:
        return "disconnected" if e.status in (401, 403) else "unreachable"


def friendly_error(e: Exception) -> str:
    if isinstance(e, aicad_link.LinkError):
        if e.status == 0:
            return "Sunucuya ulaşılamıyor. İnternet bağlantını kontrol et."
        if e.status == 410:
            return "Kodun süresi doldu. Tekrar dene."
        return str(e)  # the server's own `detail` text (e.g. free-plan device limit, 403)
    return f"{type(e).__name__}: {e}"


def pair_blocking(host: str, on_code, state_dir=None, open_browser=webbrowser.open,
                  should_stop=lambda: False, timeout_s: float = 600, interval: float | None = None) -> bool:
    """Start pairing, report the code via on_code(user_code), poll until approved (state file is written
    by LinkClient). Raises LinkError (403 device limit, 410 expired, 0 offline)."""
    name = f"{HOST_LABEL[host]} · {os.environ.get('COMPUTERNAME', 'PC')}"
    c = aicad_link.LinkClient(host, state_dir=state_dir, name=name)
    info = c.start_pairing()
    on_code(info["user_code"])
    try:
        open_browser(info["verification_url"])
    except Exception:
        pass
    return c.wait_for_pairing(timeout_s=timeout_s, interval=interval or max(1, info.get("interval", 3)),
                              should_stop=should_stop)


def install_note_text(host: str) -> str:
    if host == "fusion":
        return "Fusion'u yeniden başlat; araç çubuğunda AICADBridge düğmesi görünecek."
    if host == "blender":
        return ("Blender eklentisi kuruldu. Görünmüyorsa: Düzen › Tercihler › Eklentiler › "
                "'AICADBridge Link' kutusunu işaretle.")
    if host == "rhino":
        return ("Rhino betiği kuruldu. Rhino'da komut satırına RunPythonScript yaz ve AICADBridgeLink.py "
                "dosyasını seç (her Rhino oturumunda bir kez).")
    if host == "other":
        return ("STEP klasörü köprüsü kuruldu (masaüstünde 'AICADBridge STEP klasörü'). Model gelince "
                "~/AICADBridge/inbox klasörü açılır; dosyayı programına elle içe aktar.")
    short = SHORT_NAME[host]
    beta = " (Beta)" if host in BETA_HOSTS else ""
    return (f"{short} köprüsü{beta} kuruldu: masaüstündeki 'AICADBridge {short}' kısayolunu çalıştır. "
            "Programı otomatik açmak için pywin32 gerekir; yoksa STEP ~/AICADBridge/inbox klasörüne kaydedilir.")


def run_bridge_cli(rest: list[str]) -> int:
    """`AICADBridgeSetup.exe --bridge <host> [options]`: the desktop bridge without the installer window."""
    if not rest:
        print("kullanım: --bridge <" + "|".join(COM_HOSTS) + ">")
        return 2
    sys.path.insert(0, str(ROOT / "desktop"))
    import aicadbridge_desktop
    return aicadbridge_desktop.main(["--host", rest[0], *rest[1:]])


# --------------------------------------------------------------------------- GUI


def run_gui() -> None:
    import tkinter as tk
    from tkinter import ttk

    det = detect_all()
    ui_q: queue.Queue = queue.Queue()
    root = tk.Tk()
    root.title("AICADBridge Kurulumu")
    root.geometry("540x640")
    root.minsize(520, 520)
    root.configure(bg="#f6f7f9")
    ok_c, warn_c, bad_c, mut_c = "#1a7f37", "#b25e00", "#b42318", "#667085"

    tk.Label(root, text="AICADBridge Kurulumu", font=("Segoe UI", 16, "bold"), bg="#f6f7f9").pack(pady=(14, 0))
    tk.Label(root, text=f"Eklenti sürümü {VERSION} · Web'de ürettiğin modeller CAD programına gelsin",
             font=("Segoe UI", 9), fg=mut_c, bg="#f6f7f9").pack(pady=(0, 8))

    cards = {}
    busy = {"v": False}

    def post(fn, *a):
        ui_q.put((fn, a))

    def pump():
        try:
            while True:
                fn, a = ui_q.get_nowait()
                fn(*a)
        except queue.Empty:
            pass
        root.after(100, pump)

    # bottom area first so it stays visible; the scrollable app list takes the remaining space
    bottom = tk.Frame(root, bg="#f6f7f9")
    bottom.pack(side="bottom", fill="x")
    code_var = tk.StringVar()
    note_var = tk.StringVar()
    tk.Label(bottom, textvariable=code_var, font=("Consolas", 22, "bold"), fg="#175cd3", bg="#f6f7f9").pack(pady=(8, 0))
    note_lbl = tk.Label(bottom, textvariable=note_var, font=("Segoe UI", 9), fg=mut_c, bg="#f6f7f9",
                        wraplength=490, justify="left")
    note_lbl.pack(padx=16, pady=2)
    all_btn = ttk.Button(bottom, text="Hepsini kur ve bağla")
    all_btn.pack(fill="x", padx=16, pady=(8, 10), ipady=6)

    list_wrap = tk.Frame(root, bg="#f6f7f9")
    list_wrap.pack(side="top", fill="both", expand=True)
    canvas = tk.Canvas(list_wrap, bg="#f6f7f9", highlightthickness=0)
    scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(canvas, bg="#f6f7f9")
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
    root.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units"))

    for host in HOSTS:
        card = tk.Frame(inner, bg="white", highlightbackground="#e4e7ec", highlightthickness=1)
        card.pack(fill="x", padx=16, pady=4)
        lines = tk.Frame(card, bg="white")
        lines.pack(side="left", fill="x", expand=True, padx=10, pady=8)
        lbls = []
        for i in range(3):
            lb = tk.Label(lines, text="", anchor="w", bg="white", font=("Segoe UI", 10, "bold" if i == 0 else "normal"))
            lb.pack(fill="x")
            lbls.append(lb)
        btn = ttk.Button(card, text="Kur", width=12)
        btn.pack(side="right", padx=10)
        cards[host] = {"labels": lbls, "button": btn, "conn": "unknown", "status": "missing"}

    def set_note(text, color=mut_c):
        note_var.set(text)
        note_lbl.config(fg=color)

    def show_code(code):
        code_var.set(code)
        set_note(f"Tarayıcıda bu kodu onayla: {code}")

    def clear_code():
        code_var.set("")

    def refresh_static():
        for host in HOSTS:
            c, d = cards[host], det[host]
            la = c["labels"]
            title = HOST_LABEL[host] + (" (Beta)" if host in BETA_HOSTS else "")
            if host == "other":
                la[0].config(text=title, fg=ok_c)
            else:
                la[0].config(text=f"{title} — " + ("Algılandı ✓" if d["found"] else "Bulunamadı"),
                             fg=ok_c if d["found"] else mut_c)
            st = plugin_status(installed_version(target_for(host, det))) if d["found"] else "missing"
            what = "Eklenti" if host in ("fusion", "blender", "rhino") else "Köprü"
            la[1].config(text=f"{what} — " + {"current": "Kurulu ✓", "outdated": "Güncelleme var",
                                              "missing": "Kurulu değil"}[st],
                         fg={"current": ok_c, "outdated": warn_c, "missing": mut_c}[st])
            c["status"] = st
            conn = c["conn"]
            la[2].config(text="Bağlantı — " + {"connected": "Bağlı ✓", "disconnected": "Bağlı değil",
                                              "unreachable": "Sunucuya ulaşılamadı",
                                              "unknown": "Kontrol ediliyor…"}[conn],
                         fg=ok_c if conn == "connected" else mut_c)
            if st == "outdated":
                text = "Güncelle"
            elif st == "missing":
                text = "Kur"
            else:
                text = "Yeniden bağla" if conn == "connected" else "Bağla"
            c["button"].config(text=text, state="normal" if d["found"] and not busy["v"] else "disabled")

    def set_conn(h, res):
        cards[h]["conn"] = res
        refresh_static()

    def refresh_conn():
        def work(h):
            post(set_conn, h, check_connection(h))
        for h in HOSTS:
            if det[h]["found"]:
                threading.Thread(target=work, args=(h,), daemon=True).start()
            else:
                cards[h]["conn"] = "disconnected"

    def set_busy(v):
        busy["v"] = v
        all_btn.config(state="disabled" if v else "normal")
        refresh_static()

    hint_text = tk.Label(bottom, text="Shift+S → Eklentiler → AICADBridgeLink → Çalıştır", bg="#f6f7f9",
                         fg=mut_c, font=("Segoe UI", 9))
    hint_open = {"v": False}

    def toggle_hint():
        hint_open["v"] = not hint_open["v"]
        if hint_open["v"]:
            hint_text.pack(before=all_btn)
        else:
            hint_text.pack_forget()

    hint_btn = ttk.Button(bottom, text="Fusion'da bulamıyor musun?", command=toggle_hint)

    def show_hint_btn():
        hint_btn.pack(pady=(2, 0), before=hint_text if hint_open["v"] else all_btn)

    def install_note(host):
        set_note(install_note_text(host), ok_c)
        if host == "fusion":
            show_hint_btn()

    def do_install(host):
        try:
            install_host(host, det)
            post(install_note, host)
        except Exception as e:
            post(set_note, f"Kurulum başarısız ({HOST_LABEL[host]}): {friendly_error(e)}", bad_c)

    def do_pair(host):
        try:
            post(set_note, f"{HOST_LABEL[host]} için eşleştirme başlatılıyor…")
            okp = pair_blocking(host, lambda c: post(show_code, c))
            post(clear_code)
            if okp:
                post(set_note, f"{HOST_LABEL[host]} bağlandı ✓", ok_c)
            else:
                post(set_note, "Kodun süresi doldu. Tekrar dene.", bad_c)
        except Exception as e:
            post(clear_code)
            post(set_note, friendly_error(e), bad_c)

    def finish():
        post(det.update, detect_all())
        post(set_busy, False)
        post(refresh_conn)

    def on_click(host):
        st = cards[host]["status"]
        set_busy(True)

        def work():
            (do_install if st != "current" else do_pair)(host)
            finish()
        threading.Thread(target=work, daemon=True).start()

    def on_all():
        # "STEP klasörü" is always available, so it is not part of "install everything"
        targets = [h for h in HOSTS if h != "other" and det[h]["found"]]
        if not targets:
            set_note("Desteklenen bir CAD programı bulunamadı. Başka bir programın varsa 'STEP klasörü' "
                     "satırından kurabilirsin.", bad_c)
            return
        set_busy(True)

        def work():
            for h in targets:
                do_install(h)
            first = next((h for h in targets if cards[h]["conn"] != "connected"), None)
            if first:  # free plan allows a single device: pair only the first one
                do_pair(first)
            finish()
        threading.Thread(target=work, daemon=True).start()

    for host in HOSTS:
        cards[host]["button"].config(command=lambda h=host: on_click(h))
    all_btn.config(command=on_all)
    if det["fusion"]["found"] and installed_version(target_for("fusion", det)):
        show_hint_btn()

    refresh_static()
    refresh_conn()
    pump()
    root.mainloop()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--bridge":
        return run_bridge_cli(argv[1:])
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())

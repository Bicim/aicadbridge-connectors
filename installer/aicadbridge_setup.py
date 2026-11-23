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

# design tokens (vault/tasarim-sistemi.md), dark theme
BG, SURFACE, SURFACE_2 = "#0a0a0b", "#111113", "#17171a"
LINE, LINE_2 = "#232327", "#2e2e33"
TEXT_1, TEXT_2, TEXT_3 = "#ededef", "#a1a1aa", "#71717a"
ACCENT, ACCENT_HOVER, ACCENT_INK = "#00b8d4", "#1ac6e0", "#00161a"
OK, WARN, ERR = "#3ecf8e", "#f5a524", "#f26d6d"

LOGO_KEYS = ("fusion", "blender", "solidworks", "inventor", "solidedge", "catia", "autocad", "rhino")
LOGO_DIR = ROOT / "installer" / "logos"


def enable_dpi_awareness() -> None:
    """Crisp text on scaled displays (Windows only; never raises)."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        pass


def status_parts(found: bool, plugin_state: str, conn: str) -> list[tuple[str, str]]:
    """Row status as [(text, colour name)]: one 'Hazır' when installed and connected, else the pieces."""
    if not found:
        return [("Bulunamadı", "mute")]
    if plugin_state == "current" and conn == "connected":
        return [("Hazır", "ok")]
    plugin = {"current": ("Kurulu", "ok"), "outdated": ("Güncelleme var", "warn"),
              "missing": ("Kurulu değil", "mute")}
    link = {"connected": ("Bağlı", "ok"), "disconnected": ("Bağlı değil", "mute"),
            "unreachable": ("Sunucuya ulaşılamadı", "warn"), "unknown": ("Kontrol ediliyor…", "mute")}
    return [plugin[plugin_state], link[conn]]


def row_action(plugin_state: str, conn: str) -> str:
    if plugin_state == "outdated":
        return "Güncelle"
    if plugin_state == "missing":
        return "Kur"
    return "Yeniden bağla" if conn == "connected" else "Bağla"


def run_gui() -> None:
    enable_dpi_awareness()
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import ttk

    det = detect_all()
    ui_q: queue.Queue = queue.Queue()
    root = tk.Tk()
    root.title("AICADBridge Kurulumu")
    root.configure(bg=BG)
    scale = max(1.0, root.winfo_fpixels("1i") / 96)

    def S(n: float) -> int:
        return int(round(n * scale))

    root.geometry(f"{S(560)}x{S(480)}")
    root.minsize(S(520), S(440))

    families = set(tkfont.families())
    sans = next((f for f in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI") if f in families), "Segoe UI")
    semi = "Segoe UI Semibold" if "Segoe UI Semibold" in families else sans
    mono = next((f for f in ("Cascadia Code", "Consolas") if f in families), "Courier New")

    def strong(size):
        return (semi, size) if semi != sans else (sans, size, "bold")

    f_small, f_name, f_title, f_btn = (sans, 9), strong(10), strong(15), strong(10)

    try:  # dark title bar (Windows 10 20H1+ / 11)
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())  # type: ignore[attr-defined]
        ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
            hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
    except Exception:
        pass

    colours = {"ok": OK, "warn": WARN, "mute": TEXT_3, "err": ERR}
    images: list = []  # PhotoImage references must stay alive
    cards: dict = {}
    busy = {"v": False}

    # ---- widgets -----------------------------------------------------------
    def paint(b, hover=False):
        off = str(b["state"]) == "disabled"
        if b.kind == "primary":
            bg = SURFACE_2 if off else (ACCENT_HOVER if hover else ACCENT)
            fg, edge = (TEXT_3 if off else ACCENT_INK), bg
        elif b.kind == "ghost":
            bg, fg, edge = BG, (TEXT_1 if hover and not off else TEXT_2), BG
        else:
            bg = SURFACE_2 if hover and not off else SURFACE
            fg, edge = (TEXT_3 if off else TEXT_1), (LINE if off else LINE_2)
        b.config(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, disabledforeground=TEXT_3,
                 cursor="arrow" if off else "hand2")
        b.box.config(bg=edge)  # 1px outline: Tk's own button highlight is not drawn reliably on Windows

    def make_button(parent, text, kind="secondary", command=None, width=0):
        box = tk.Frame(parent, padx=1, pady=1)
        b = tk.Button(box, text=text, command=command, font=f_btn, relief="flat", bd=0, highlightthickness=0,
                      padx=S(14), pady=S(6), width=width, takefocus=True)
        b.pack(fill="both", expand=True)
        b.kind, b.box = kind, box
        b.bind("<Enter>", lambda e: paint(b, hover=True))
        b.bind("<Leave>", lambda e: paint(b))
        paint(b)
        return b

    def set_button(b, text=None, enabled=True):
        if text is not None:
            b.config(text=text)
        b.config(state="normal" if enabled else "disabled")
        paint(b)

    def rrect(c, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1,
               x0, y1 - r, x0, y0 + r, x0, y0]
        return c.create_polygon(pts, smooth=True, **kw)

    def logo_mark(parent, size):
        c = tk.Canvas(parent, width=size, height=size, bg=BG, highlightthickness=0)
        rrect(c, 0, 0, size - 1, size - 1, size * 0.28, fill=ACCENT, outline=ACCENT)
        w = max(2, S(2))
        c.create_arc(size * 0.2, size * 0.28, size * 0.8, size * 1.0, start=0, extent=180, style="arc",
                     outline=ACCENT_INK, width=w)
        c.create_line(size * 0.14, size * 0.64, size * 0.86, size * 0.64, fill=ACCENT_INK, width=w,
                      capstyle="round")
        return c

    def load_logo(key):
        """32 px logo from installer/logos/<key>-64.png, or None (a letter tile is drawn instead)."""
        try:
            img = tk.PhotoImage(file=str(LOGO_DIR / f"{key}-64.png"))
            img = img if scale >= 1.5 else img.subsample(2)
            images.append(img)
            return img
        except Exception:
            return None

    def host_tile(parent, host):
        size = S(40)
        c = tk.Canvas(parent, width=size, height=size, bg=SURFACE, highlightthickness=0)
        rrect(c, 0, 0, size - 1, size - 1, S(10), fill=SURFACE_2, outline=SURFACE_2)
        img = load_logo(host) if host in LOGO_KEYS else None
        if img is not None:
            c.create_image(size // 2, size // 2, image=img)
        elif host == "other":  # folder glyph
            x, y, w, h = size * 0.27, size * 0.34, size * 0.46, size * 0.34
            c.create_polygon(x, y, x + w * 0.4, y, x + w * 0.5, y + h * 0.18, x + w, y + h * 0.18, x + w, y + h,
                             x, y + h, outline=TEXT_2, fill="", width=max(1, S(1.5)), joinstyle="round")
        else:
            c.create_text(size // 2, size // 2, text=HOST_LABEL[host].split()[-1][:1].upper(), fill=TEXT_2,
                          font=strong(12))
        return c

    # ---- layout ------------------------------------------------------------
    header = tk.Frame(root, bg=BG)
    header.pack(side="top", fill="x", padx=S(24), pady=(S(24), S(12)))
    logo_mark(header, S(32)).pack(side="left", padx=(0, S(12)))
    head_text = tk.Frame(header, bg=BG)
    head_text.pack(side="left", fill="x")
    tk.Label(head_text, text="AICADBridge Kurulumu", font=f_title, fg=TEXT_1, bg=BG, anchor="w").pack(fill="x")
    tk.Label(head_text, text=f"Web'de ürettiğin modeller CAD programına gelsin · sürüm {VERSION}", font=f_small,
             fg=TEXT_2, bg=BG, anchor="w").pack(fill="x")

    # footer first so it stays visible; the scrollable list takes the remaining space
    footer = tk.Frame(root, bg=BG)
    footer.pack(side="bottom", fill="x")
    tk.Frame(footer, bg=LINE, height=1).pack(fill="x")
    foot = tk.Frame(footer, bg=BG)
    foot.pack(fill="x", padx=S(24), pady=(S(12), S(16)))
    code_var, note_var = tk.StringVar(), tk.StringVar()
    code_lbl = tk.Label(foot, textvariable=code_var, font=(mono, 20, "bold"), fg=ACCENT, bg=BG)
    note_lbl = tk.Label(foot, textvariable=note_var, font=f_small, fg=TEXT_2, bg=BG, wraplength=S(500),
                        justify="left", anchor="w")
    all_btn = make_button(foot, "Hepsini kur ve bağla", "primary")
    all_btn.config(pady=S(10))
    all_btn.box.pack(fill="x")

    list_wrap = tk.Frame(root, bg=BG)
    list_wrap.pack(side="top", fill="both", expand=True)
    sb_style = "Vertical.TScrollbar"
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
        sb_style = "Aicad.Vertical.TScrollbar"
        style.layout(sb_style, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure(sb_style, troughcolor=BG, background=LINE_2, bordercolor=BG, lightcolor=LINE_2,
                        darkcolor=LINE_2, relief="flat", width=S(8))
        style.map(sb_style, background=[("active", TEXT_3), ("pressed", TEXT_3)])
    except tk.TclError:
        sb_style = "Vertical.TScrollbar"
    canvas = tk.Canvas(list_wrap, bg=BG, highlightthickness=0)
    scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=canvas.yview, style=sb_style)
    canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y", padx=(0, S(6)))
    canvas.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(canvas, bg=BG)
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
    root.bind_all("<MouseWheel>",
                  lambda e: canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units"))

    for host in HOSTS:
        card = tk.Frame(inner, bg=SURFACE, highlightbackground=LINE, highlightcolor=LINE, highlightthickness=1)
        card.pack(fill="x", padx=(S(24), S(18)), pady=(0, S(8)))
        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="x", padx=S(12), pady=S(12))
        host_tile(body, host).pack(side="left")
        btn = make_button(body, "Kur", width=12)
        btn.box.pack(side="right")
        text = tk.Frame(body, bg=SURFACE)
        text.pack(side="left", fill="x", expand=True, padx=S(12))
        top = tk.Frame(text, bg=SURFACE)
        top.pack(fill="x")
        name = tk.Label(top, text=HOST_LABEL[host], font=f_name, fg=TEXT_1, bg=SURFACE, anchor="w")
        name.pack(side="left")
        if host in BETA_HOSTS:
            tk.Label(top, text="Beta", font=f_small, fg=ACCENT, bg=SURFACE).pack(side="left", padx=(S(8), 0))
        status = tk.Frame(text, bg=SURFACE)
        status.pack(fill="x", pady=(S(2), 0))
        cards[host] = {"name": name, "status": status, "button": btn, "conn": "unknown", "state": "missing"}

    # ---- behaviour ---------------------------------------------------------
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

    def set_note(text, color="plain"):
        note_var.set(text)
        note_lbl.config(fg=colours.get(color, TEXT_2))
        note_lbl.pack_forget()
        if text:
            note_lbl.pack(fill="x", pady=(0, S(8)), before=all_btn.box)

    def show_code(code):
        code_var.set(code)
        code_lbl.pack(anchor="w", before=note_lbl if note_lbl.winfo_manager() else all_btn.box)
        set_note(f"Tarayıcıda bu kodu onayla: {code}")

    def clear_code():
        code_var.set("")
        code_lbl.pack_forget()

    def set_status(host, parts):
        box = cards[host]["status"]
        for w in box.winfo_children():
            w.destroy()
        for n, (txt, col) in enumerate(parts):
            if n:
                tk.Label(box, text="·", font=f_small, fg=TEXT_3, bg=SURFACE).pack(side="left", padx=S(4))
            tk.Label(box, text=txt, font=f_small, fg=colours[col], bg=SURFACE).pack(side="left")

    def refresh_static():
        for host in HOSTS:
            c, d = cards[host], det[host]
            st = plugin_status(installed_version(target_for(host, det))) if d["found"] else "missing"
            c["state"] = st
            c["name"].config(fg=TEXT_1 if d["found"] else TEXT_3)
            set_status(host, status_parts(d["found"], st, c["conn"]))
            set_button(c["button"], row_action(st, c["conn"]), d["found"] and not busy["v"])

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
        set_button(all_btn, enabled=not v)
        refresh_static()

    hint_text = tk.Label(foot, text="Shift+S → Eklentiler → AICADBridgeLink → Çalıştır", bg=BG, fg=TEXT_2,
                         font=f_small)
    hint_open = {"v": False}

    def toggle_hint():
        hint_open["v"] = not hint_open["v"]
        if hint_open["v"]:
            hint_text.pack(before=all_btn.box, pady=(0, S(8)))
        else:
            hint_text.pack_forget()

    hint_btn = make_button(foot, "Fusion'da bulamıyor musun?", "ghost", toggle_hint)
    hint_btn.config(padx=0, pady=S(2))

    def show_hint_btn():
        hint_btn.box.pack(anchor="w", pady=(0, S(4)), before=hint_text if hint_open["v"] else all_btn.box)

    def install_note(host):
        set_note(install_note_text(host), "ok")
        if host == "fusion":
            show_hint_btn()

    def do_install(host):
        try:
            install_host(host, det)
            post(install_note, host)
        except Exception as e:
            post(set_note, f"Kurulum başarısız ({HOST_LABEL[host]}): {friendly_error(e)}", "err")

    def do_pair(host):
        try:
            post(set_note, f"{HOST_LABEL[host]} için eşleştirme başlatılıyor…")
            okp = pair_blocking(host, lambda c: post(show_code, c))
            post(clear_code)
            if okp:
                post(set_note, f"{HOST_LABEL[host]} bağlandı", "ok")
            else:
                post(set_note, "Kodun süresi doldu. Tekrar dene.", "err")
        except Exception as e:
            post(clear_code)
            post(set_note, friendly_error(e), "err")

    def finish():
        post(det.update, detect_all())
        post(set_busy, False)
        post(refresh_conn)

    def on_click(host):
        st = cards[host]["state"]
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
                     "satırından kurabilirsin.", "err")
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

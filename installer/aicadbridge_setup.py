"""AICADBridge Kurulumu — one-window installer + pairing helper for Fusion, Blender and SolidWorks.

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
HOSTS = ("fusion", "blender", "solidworks")
HOST_LABEL = {"fusion": "Autodesk Fusion", "blender": "Blender", "solidworks": "SolidWorks"}
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


def solidworks_dir(env=None) -> Path | None:
    local = _env(env).get("LOCALAPPDATA")
    return Path(local) / "AICADBridge" / "solidworks" if local else None


def detect_solidworks(env=None, reg=reg_key_exists) -> dict:
    found = any(reg(r, r"SOFTWARE\SolidWorks") for r in ("HKLM", "HKCU"))
    return {"found": found, "install_dir": solidworks_dir(env)}


def detect_all(env=None, reg=reg_key_exists) -> dict:
    return {"fusion": detect_fusion(env), "blender": detect_blender(env, reg),
            "solidworks": detect_solidworks(env, reg)}


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


def install_solidworks(install_dir: Path, root: Path | None = None, desktop: Path | None = None) -> Path:
    src = (root or ROOT) / "solidworks"
    dest = Path(install_dir)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for name in ("aicadbridge_sw_link.py", "run.bat"):
        shutil.copy2(src / name, dest / name)
    shutil.copy2(ROOT / "common" / "aicad_link.py", dest / "aicad_link.py")
    (dest / VERSION_MARKER).write_text(VERSION, encoding="utf-8")
    if desktop is not None and Path(desktop).is_dir():
        (Path(desktop) / "AICADBridge SolidWorks.bat").write_text(
            f'@echo off\r\ncall "{dest / "run.bat"}"\r\n', encoding="utf-8")
    return dest


def target_for(host: str, det: dict) -> Path | None:
    if host == "fusion" and det["fusion"]["addins_dir"]:
        return fusion_target(det["fusion"]["addins_dir"])
    if host == "blender" and det["blender"]["addons_dir"]:
        return blender_target(det["blender"]["addons_dir"])
    if host == "solidworks":
        return det["solidworks"]["install_dir"]
    return None


def install_host(host: str, det: dict, env=None) -> Path:
    if host == "fusion":
        return install_fusion(det["fusion"]["addins_dir"])
    if host == "blender":
        return install_blender(det["blender"]["addons_dir"])
    if host == "solidworks":
        up = _env(env).get("USERPROFILE")
        return install_solidworks(det["solidworks"]["install_dir"], desktop=Path(up) / "Desktop" if up else None)
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


# --------------------------------------------------------------------------- GUI


def run_gui() -> None:
    import tkinter as tk
    from tkinter import ttk

    det = detect_all()
    ui_q: queue.Queue = queue.Queue()
    root = tk.Tk()
    root.title("AICADBridge Kurulumu")
    root.geometry("520x560")
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

    for host in HOSTS:
        card = tk.Frame(root, bg="white", highlightbackground="#e4e7ec", highlightthickness=1)
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

    code_var = tk.StringVar()
    note_var = tk.StringVar()
    tk.Label(root, textvariable=code_var, font=("Consolas", 22, "bold"), fg="#175cd3", bg="#f6f7f9").pack(pady=(8, 0))
    note_lbl = tk.Label(root, textvariable=note_var, font=("Segoe UI", 9), fg=mut_c, bg="#f6f7f9",
                        wraplength=480, justify="left")
    note_lbl.pack(padx=16, pady=2)

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
            la[0].config(text=f"{HOST_LABEL[host]} — " + ("Algılandı ✓" if d["found"] else "Bulunamadı"),
                         fg=ok_c if d["found"] else mut_c)
            st = plugin_status(installed_version(target_for(host, det))) if d["found"] else "missing"
            la[1].config(text="Eklenti — " + {"current": "Kurulu ✓", "outdated": "Güncelleme var",
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

    hint_text = tk.Label(root, text="Shift+S → Eklentiler → AICADBridgeLink → Çalıştır", bg="#f6f7f9",
                         fg=mut_c, font=("Segoe UI", 9))
    hint_open = {"v": False}

    def toggle_hint():
        hint_open["v"] = not hint_open["v"]
        if hint_open["v"]:
            hint_text.pack()
        else:
            hint_text.pack_forget()

    hint_btn = ttk.Button(root, text="Bulamıyor musun?", command=toggle_hint)

    def install_note(host):
        if host == "fusion":
            set_note("Fusion'u yeniden başlat; araç çubuğunda AICADBridge düğmesi görünecek.", ok_c)
            hint_btn.pack(pady=(2, 0))
        elif host == "blender":
            set_note("Blender eklentisi kuruldu. Görünmüyorsa: Düzen › Tercihler › Eklentiler › "
                     "'AICADBridge Link' kutusunu işaretle.", ok_c)
        else:
            set_note("SolidWorks köprüsü kuruldu (masaüstünde 'AICADBridge SolidWorks'). pywin32 gerekir; "
                     "yoksa modeller ~/AICADBridge/inbox klasörüne STEP olarak kaydedilir.", ok_c)

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
        targets = [h for h in HOSTS if det[h]["found"]]
        if not targets:
            set_note("Bu bilgisayarda desteklenen bir CAD programı bulunamadı.", bad_c)
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

    all_btn = ttk.Button(root, text="Hepsini kur ve bağla", command=on_all)
    all_btn.pack(fill="x", padx=16, pady=(8, 4), ipady=6)
    if det["fusion"]["found"] and installed_version(target_for("fusion", det)):
        hint_btn.pack(pady=(2, 0))

    refresh_static()
    refresh_conn()
    pump()
    root.mainloop()


if __name__ == "__main__":
    run_gui()

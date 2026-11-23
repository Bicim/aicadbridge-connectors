"""AICADBridge desktop bridge (Windows) — one process per CAD host.

Usage:  python aicadbridge_desktop.py --host <id> [--server URL] [--inbox DIR] [--forget]
Hosts:  solidworks, inventor, solidedge, catia, autocad  (COM, needs pywin32)
        other  (STEP folder: saves into the inbox and opens it in Explorer)

- First run prints a pairing code; approve it in the web app (Integrations).
- Models sent from the web are downloaded as STEP into the inbox folder and opened in the CAD
  program through COM. If pywin32 / the program / the import is unavailable the file simply stays
  in the inbox and the job is reported ok with a message saying where it went.
- The same logic is available inside AICADBridgeSetup.exe as `AICADBridgeSetup.exe --bridge <host>`.

Standard library only; pywin32 (`win32com`) is optional.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "common")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
import aicad_link  # noqa: E402

DEFAULT_INBOX = Path.home() / "AICADBridge" / "inbox"

# --------------------------------------------------------------------------- COM adapters
# Each adapter receives the connected COM application object and the STEP path and returns
# (ok, message). Anything it raises is caught by `deliver` and triggers the inbox fallback.


def _show(app) -> None:
    try:
        app.Visible = True
    except Exception:
        pass


def open_solidworks(app, path: Path) -> tuple[bool, str]:
    import pythoncom
    import win32com.client

    _show(app)
    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    data = app.GetImportFileData(str(path))
    model = app.LoadFile4(str(path), "r", data, errors)
    if model is None:
        return False, f"SolidWorks dosyayı açamadı (hata kodu {errors.value})"
    return True, f"SolidWorks: {path.name} açıldı"


def open_inventor(app, path: Path) -> tuple[bool, str]:
    _show(app)
    app.Documents.Open(str(path), True)
    return True, f"Inventor: {path.name} açıldı"


def open_solidedge(app, path: Path) -> tuple[bool, str]:
    _show(app)
    app.Documents.Open(str(path))
    return True, f"Solid Edge: {path.name} açıldı"


def open_catia(app, path: Path) -> tuple[bool, str]:
    _show(app)
    app.Documents.Open(str(path))
    return True, f"CATIA: {path.name} açıldı"


def open_autocad(app, path: Path) -> tuple[bool, str]:
    _show(app)
    try:
        doc = app.ActiveDocument
    except Exception:
        doc = None
    if doc is None:
        doc = app.Documents.Add()
    # forward slashes keep the command-line parser happy; AutoCAD 2022+ imports STEP via -IMPORT
    doc.SendCommand('_-IMPORT "%s"\n' % str(path).replace("\\", "/"))
    return True, f"AutoCAD: {path.name} içe aktarılıyor"


@dataclass(frozen=True)
class Adapter:
    host: str
    label: str
    prog_id: str
    open: Callable[[object, Path], tuple[bool, str]]


ADAPTERS: dict[str, Adapter] = {a.host: a for a in (
    Adapter("solidworks", "SolidWorks", "SldWorks.Application", open_solidworks),
    Adapter("inventor", "Inventor", "Inventor.Application", open_inventor),
    Adapter("solidedge", "Solid Edge", "SolidEdge.Application", open_solidedge),
    Adapter("catia", "CATIA V5", "CATIA.Application", open_catia),
    Adapter("autocad", "AutoCAD", "AutoCAD.Application", open_autocad),
)}
HOSTS = tuple(ADAPTERS) + ("other",)
HOST_NAME = {**{h: a.label for h, a in ADAPTERS.items()}, "other": "STEP klasörü"}


def connect_com(prog_id: str):
    """Running instance first (GetActiveObject), else start one (Dispatch). None when unavailable."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        return win32com.client.GetActiveObject(prog_id)
    except Exception:
        pass
    try:
        return win32com.client.Dispatch(prog_id)
    except Exception:
        return None


def select_adapter(host: str, connect: Callable[[str], object] = connect_com):
    """Pure selection: returns `opener(path) -> (ok, message)` bound to the connected application,
    or None when this host has no COM route (e.g. "other") or the application cannot be reached."""
    adapter = ADAPTERS.get(host)
    if adapter is None:
        return None
    app = connect(adapter.prog_id)
    if app is None:
        return None
    return lambda path: adapter.open(app, path)


# --------------------------------------------------------------------------- delivery


def unique_path(inbox: Path, title: str) -> Path:
    """`<safe title>.step`, with a timestamp suffix when that name is taken (it may be open in the CAD)."""
    base = aicad_link.safe_name(title)
    p = Path(inbox) / f"{base}.step"
    if p.exists():
        p = Path(inbox) / f"{base}{time.strftime('_%Y%m%d-%H%M%S')}.step"
        n = 1
        while p.exists():
            n += 1
            p = Path(inbox) / f"{base}{time.strftime('_%Y%m%d-%H%M%S')}_{n}.step"
    return p


def open_folder(folder: Path) -> None:
    try:
        os.startfile(str(folder))  # type: ignore[attr-defined]  # Windows only
    except Exception:
        pass


def deliver(host: str, path: Path, connect: Callable[[str], object] = connect_com,
            folder_opener: Callable[[Path], None] | None = None, state: dict | None = None) -> tuple[bool, str]:
    """Hand a downloaded STEP to the host. Always ok=True once the file is on disk."""
    state = state if state is not None else {}
    folder_opener = folder_opener or open_folder
    label = HOST_NAME.get(host, host)
    if host == "other":
        if not state.get("opened"):
            state["opened"] = True
            folder_opener(path.parent)
        return True, f"STEP kaydedildi: {path}"
    opener = select_adapter(host, connect)
    if opener is None:
        return True, f"{label} bulunamadı (pywin32 / program); STEP şuraya kaydedildi: {path}"
    try:
        ok, msg = opener(path)
    except Exception as e:
        return True, f"{label} dosyayı açamadı ({type(e).__name__}: {e}); STEP şuraya kaydedildi: {path}"
    if not ok:
        return True, f"{msg}; STEP şuraya kaydedildi: {path}"
    return True, msg


def make_handler(host: str, inbox: Path, connect: Callable[[str], object] = connect_com,
                 download=None, folder_opener: Callable[[Path], None] | None = None,
                 say: Callable[..., None] = print):
    state: dict = {}

    def handle(job):
        url = (job.get("files") or {}).get("step")
        if not url:
            return False, "işte STEP dosyası yok"
        path = (download or aicad_link.LinkClient.download)(url, unique_path(inbox, job.get("title")))
        say("Alındı:", path)
        return deliver(host, Path(path), connect, folder_opener, state)
    return handle


# --------------------------------------------------------------------------- process plumbing


def single_instance(name: str) -> bool:
    """Windows named mutex. True when we are the only instance (always True elsewhere / on error)."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateMutexW(None, False, f"Local\\AICADBridge-{name}")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def message_box(title: str, text: str) -> None:
    """Non-blocking Windows message box (used when the process has no console)."""
    def show():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)  # type: ignore[attr-defined]
        except Exception:
            pass
    import threading
    threading.Thread(target=show, daemon=True).start()


def run_bridge(host: str, server: str | None = None, inbox: Path | str | None = None, forget: bool = False,
               open_browser: bool = True, say: Callable[..., None] = print,
               notify: Callable[[str, str], None] | None = None, connect=connect_com,
               should_stop=lambda: False) -> int:
    notify = notify or (lambda title, text: None)
    if host not in HOST_NAME:
        say("Bilinmeyen program:", host)
        return 2
    inbox = Path(inbox or DEFAULT_INBOX)
    label = HOST_NAME[host]
    client = aicad_link.LinkClient(host, name=f"{label} · {os.environ.get('COMPUTERNAME', 'PC')}")
    if server:
        client.server = server
    if forget:
        client.forget()
        say("Bağlantı silindi.")
        return 0
    if host != "other":
        say(f"{label} bulunamazsa (veya pywin32 yoksa) modeller şu klasöre kaydedilecek:", inbox)

    if not client.paired:
        try:
            info = client.start_pairing()
        except aicad_link.LinkError as e:
            say("Sunucuya bağlanılamadı:", e)
            notify("AICADBridge", f"Sunucuya bağlanılamadı: {e}")
            return 2
        say(f"\n  Eşleştirme kodu:  {info['user_code']}\n  Onay sayfası:     {info['verification_url']}\n")
        notify("AICADBridge — eşleştirme", f"Tarayıcıda şu kodu onayla: {info['user_code']}")
        if open_browser:
            try:
                webbrowser.open(info["verification_url"])
            except Exception:
                pass
        try:
            if not client.wait_for_pairing(should_stop=should_stop):
                say("Kodun süresi doldu, tekrar çalıştırın.")
                notify("AICADBridge", "Kodun süresi doldu, köprüyü tekrar çalıştır.")
                return 3
        except aicad_link.LinkError as e:
            say("Eşleşme başarısız:", e)
            notify("AICADBridge", f"Eşleşme başarısız: {e}")
            return 3
        say("Bağlandı.")

    say("Model bekleniyor… (çıkmak için Ctrl+C)")
    try:
        aicad_link.run_job_loop(client, make_handler(host, inbox, connect, say=say), should_stop=should_stop,
                                on_error=lambda e: say("Bağlantı:", e))
    except KeyboardInterrupt:
        return 0
    if not client.paired:
        say("Bağlantı web uygulamasından kaldırıldı.")
        notify("AICADBridge", "Bağlantı web uygulamasından kaldırıldı; köprü kapandı.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AICADBridge desktop bridge")
    ap.add_argument("--host", required=True, choices=HOSTS, help="program: " + ", ".join(HOSTS))
    ap.add_argument("--server", help="AICADBridge API adresi")
    ap.add_argument("--inbox", default=str(DEFAULT_INBOX), help="STEP dosyalarının kaydedileceği klasör")
    ap.add_argument("--forget", action="store_true", help="kayıtlı bağlantıyı sil ve çık")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--headless", action="store_true", help="konsol yok: günlük dosyasına yaz, kodu pencerede göster")
    args = ap.parse_args(argv)

    headless = args.headless or sys.stdout is None  # PyInstaller --windowed has no stdout
    say: Callable[..., None] = print
    notify = None
    if headless:
        log_path = Path.home() / "AICADBridge" / f"bridge-{args.host}.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log = open(log_path, "a", encoding="utf-8", buffering=1)
        except OSError:
            log = None

        def say(*a):  # noqa: F811
            if log:
                log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
        notify = message_box
    if not single_instance(args.host):
        say("Köprü zaten çalışıyor:", args.host)
        if notify:
            notify("AICADBridge", f"{HOST_NAME[args.host]} köprüsü zaten çalışıyor.")
        return 0
    if headless:
        message_box("AICADBridge", f"{HOST_NAME[args.host]} köprüsü arka planda çalışıyor. "
                                   "Web'den gönderilen modeller otomatik açılır.")
    return run_bridge(args.host, server=args.server, inbox=args.inbox, forget=args.forget,
                      open_browser=not args.no_browser, say=say, notify=notify)


if __name__ == "__main__":
    sys.exit(main())

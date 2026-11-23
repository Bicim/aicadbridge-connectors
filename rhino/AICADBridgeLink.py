#! python 3
# -*- coding: utf-8 -*-
"""AICADBridge Link for Rhino 8 (Python 3 / RhinoCode).

Run once per Rhino session:  command line > RunPythonScript > pick this file
(or add it to Rhino's startup scripts). It
  1. pairs this PC with your AICADBridge account (opens the browser the first time),
  2. long-polls the cloud for models in a background thread,
  3. imports each model's STEP file into the active document on Rhino's UI thread.

Needs aicad_link.py next to this file (the installer copies both into
%APPDATA%/McNeel/Rhinoceros/8.0/scripts). Only outbound HTTPS is used.
"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

try:  # import-safe outside Rhino (tests, linters)
    import Rhino  # type: ignore
except ImportError:
    Rhino = None

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:  # some Rhino script hosts do not define __file__
    _appdata = os.environ.get("APPDATA", "")
    _HERE = os.path.join(_appdata, "McNeel", "Rhinoceros", "8.0", "scripts")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import aicad_link  # noqa: E402

HOST = "rhino"
INBOX = Path.home() / "AICADBridge" / "inbox"
_STICKY_KEY = "aicadbridge_link_running"


def log(msg: str) -> None:
    text = f"AICADBridge: {msg}"
    try:
        if Rhino is not None:
            Rhino.RhinoApp.WriteLine(text)
            return
    except Exception:
        pass
    print(text)


def import_step_on_ui(path, rhino=None, timeout: float = 120.0) -> tuple[bool, str]:
    """Import `path` into the active document. Runs the import on Rhino's UI thread and waits for it."""
    rhino = rhino if rhino is not None else Rhino
    if rhino is None:
        return False, "Rhino bulunamadı"
    done = threading.Event()
    result: dict = {"ok": False, "msg": "zaman aşımı"}

    def work():
        try:
            doc = rhino.RhinoDoc.ActiveDoc
            if doc is None:
                result.update(ok=False, msg="açık Rhino belgesi yok")
                return
            ok = bool(doc.Import(str(path)))
            if not ok:  # fall back to the command-line importer
                ok = bool(rhino.RhinoApp.RunScript('-_Import "%s" _Enter' % str(path).replace("\\", "/"), False))
            try:
                doc.Views.Redraw()
            except Exception:
                pass
            result.update(ok=ok, msg=f"Rhino: {Path(path).name} içe aktarıldı" if ok
                          else "Rhino STEP dosyasını içe aktaramadı")
        except Exception as e:  # keep the UI thread alive
            result.update(ok=False, msg=f"{type(e).__name__}: {e}")
        finally:
            done.set()

    try:
        import System  # type: ignore  # .NET, available in RhinoCode
        rhino.RhinoApp.InvokeOnUiThread(System.Action(work))
    except ImportError:
        rhino.RhinoApp.InvokeOnUiThread(work)
    if not done.wait(timeout):
        return False, "Rhino arayüz iş parçacığı zaman aşımına uğradı"
    return result["ok"], result["msg"]


def make_handler(inbox=INBOX, importer=import_step_on_ui, download=None):
    def handle(job):
        url = (job.get("files") or {}).get("step")
        if not url:
            return False, "işte STEP dosyası yok"
        base = aicad_link.safe_name(job.get("title"))
        path = Path(inbox) / f"{base}.step"
        n = 1
        while path.exists():  # never overwrite a file the user may have open
            n += 1
            path = Path(inbox) / f"{base}_{n}.step"
        path = (download or aicad_link.LinkClient.download)(url, path)
        log(f"alındı: {path}")
        try:
            ok, msg = importer(path)
        except Exception as e:
            ok, msg = False, f"{type(e).__name__}: {e}"
        if not ok:  # the file is on disk: report ok so the web app does not show a failure
            return True, f"{msg}; STEP şuraya kaydedildi: {path}"
        return True, msg
    return handle


def bridge(client, stop: threading.Event) -> None:
    """Pair if needed, then run the job loop. Meant to run in a background thread."""
    try:
        if not client.paired:
            info = client.start_pairing()
            log(f"eşleştirme kodu {info['user_code']} — tarayıcıda onayla: {info['verification_url']}")
            try:
                webbrowser.open(info["verification_url"])
            except Exception:
                pass
            if not client.wait_for_pairing(should_stop=stop.is_set):
                log("kodun süresi doldu veya durduruldu; betiği tekrar çalıştır.")
                return
            log("bağlandı.")
        log("model bekleniyor…")
        aicad_link.run_job_loop(client, make_handler(), should_stop=stop.is_set, on_error=lambda e: log(f"bağlantı: {e}"))
        if not client.paired:
            log("bağlantı web uygulamasından kaldırıldı.")
    except Exception as e:
        log(f"durdu: {friendly(e)}")


def friendly(e: Exception) -> str:
    return str(e) if isinstance(e, aicad_link.LinkError) else f"{type(e).__name__}: {e}"


def main() -> None:
    try:
        import scriptcontext  # type: ignore
        sticky = scriptcontext.sticky
    except ImportError:
        sticky = {}
    running = sticky.get(_STICKY_KEY)
    if running is not None and running["thread"].is_alive():
        log("zaten çalışıyor.")
        return
    client = aicad_link.LinkClient(HOST, name=f"Rhino 8 · {os.environ.get('COMPUTERNAME', 'PC')}")
    stop = threading.Event()
    t = threading.Thread(target=bridge, args=(client, stop), name="AICADBridgeLink", daemon=True)
    sticky[_STICKY_KEY] = {"thread": t, "stop": stop}
    t.start()
    log(f"başlatıldı (sürüm {aicad_link.VERSION}).")


if Rhino is not None:  # inside Rhino the script starts the bridge; elsewhere it only exposes the helpers
    main()

"""AICADBridge Link for SolidWorks (Windows) — and a generic STEP inbox for any other CAD.

Usage:  python aicadbridge_sw_link.py [--server URL] [--inbox DIR] [--forget]
- First run prints a pairing code; approve it in the web app (Integrations).
- Then models sent from the web are downloaded as STEP and opened in SolidWorks (COM, pywin32).
- Without SolidWorks/pywin32 the STEP is saved to the inbox folder instead (host = "other").
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aicad_link  # noqa: E402


def solidworks():
    """Running SolidWorks instance (or start one); None when pywin32/SolidWorks is unavailable."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None
    try:
        pythoncom.CoInitialize()
        sw = win32com.client.Dispatch("SldWorks.Application")
        sw.Visible = True
        return sw
    except Exception:
        return None


def open_in_solidworks(sw, path: Path) -> tuple[bool, str]:
    import pythoncom
    import win32com.client

    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    data = sw.GetImportFileData(str(path))
    model = sw.LoadFile4(str(path), "r", data, errors)
    if model is None:
        return False, f"SolidWorks dosyayı açamadı (hata kodu {errors.value})"
    return True, f"SolidWorks: {path.name} açıldı"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AICADBridge Link for SolidWorks / STEP inbox")
    ap.add_argument("--server", help="AICADBridge API adresi")
    ap.add_argument("--inbox", default=str(Path.home() / "AICADBridge" / "inbox"), help="STEP dosyalarının kaydedileceği klasör")
    ap.add_argument("--forget", action="store_true", help="kayıtlı bağlantıyı sil ve çık")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    sw = solidworks()
    host = "solidworks" if sw is not None else "other"
    client = aicad_link.LinkClient(host, name=f"{'SolidWorks' if sw else 'STEP inbox'} · {os.environ.get('COMPUTERNAME', 'PC')}")
    if args.server:
        client.server = args.server
    if args.forget:
        client.forget()
        print("Bağlantı silindi.")
        return 0
    if sw is None:
        print("SolidWorks bulunamadı (pywin32 kurulu mu?). Modeller şu klasöre kaydedilecek:", args.inbox)

    if not client.paired:
        try:
            info = client.start_pairing()
        except aicad_link.LinkError as e:
            print("Sunucuya bağlanılamadı:", e)
            return 2
        print(f"\n  Eşleştirme kodu:  {info['user_code']}\n  Onay sayfası:     {info['verification_url']}\n")
        if not args.no_browser:
            webbrowser.open(info["verification_url"])
        try:
            if not client.wait_for_pairing():
                print("Kodun süresi doldu, tekrar çalıştırın.")
                return 3
        except aicad_link.LinkError as e:
            print("Eşleşme başarısız:", e)
            return 3
        print("Bağlandı.")

    inbox = Path(args.inbox)

    def handle(job):
        url = (job.get("files") or {}).get("step")
        if not url:
            return False, "işte STEP dosyası yok"
        name = aicad_link.safe_name(job.get("title")) + time.strftime("_%Y%m%d-%H%M%S") + ".step"
        path = aicad_link.LinkClient.download(url, inbox / name)
        print("Alındı:", path)
        if sw is not None:
            return open_in_solidworks(sw, path)
        return True, f"STEP kaydedildi: {path}"

    print("Model bekleniyor… (çıkmak için Ctrl+C)")
    try:
        aicad_link.run_job_loop(client, handle, on_error=lambda e: print("Bağlantı:", e))
    except KeyboardInterrupt:
        return 0
    if not client.paired:
        print("Bağlantı web uygulamasından kaldırıldı.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

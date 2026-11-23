"""AICADBridge Link for Autodesk Fusion 360.

Adds an "AICADBridge" panel (SOLID tab of the Design workspace; falls back to the Add-ins panel). Pairs with the web app via a device code,
then long-polls for models sent from the web and imports their STEP as a new component.
Network runs on a worker thread; Fusion API calls happen on the main thread via a custom event.
"""
import json
import os
import sys
import tempfile
import threading
import traceback
import webbrowser

import adsk.core
import adsk.fusion

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aicad_link  # noqa: E402  (bundled next to this file)

EVENT_ID = "AICADBridgeLinkJob"
CMD_CONNECT = "AICADBridgeLinkConnect"
CMD_STATUS = "AICADBridgeLinkStatus"
PANEL_ID = "AICADBridgePanel"
FALLBACK_PANEL_ID = "SolidScriptsAddinsPanel"
WORKSPACE_ID = "FusionSolidEnvironment"
TAB_ID = "SolidTab"

_app = None
_ui = None
_handlers = []
_client = None
_stop = threading.Event()
_worker = None
_pending = {}  # job_id -> threading.Event + result, for main-thread import


def _state_text():
    if _client is not None and _client.paired:
        return "AICADBridge: bağlı ✓ — web'den gönderilen modeller otomatik açılır."
    return "AICADBridge: bağlı değil — tıklayıp tarayıcıdan eşleştir."


def _refresh_tooltip():
    try:
        cmd = _ui.commandDefinitions.itemById(CMD_CONNECT)
        if cmd:
            cmd.tooltip = _state_text()
    except Exception:
        pass


def _find_panel(create):
    """Own 'AICADBridge' panel in the SOLID tab; falls back to the Add-ins panel."""
    panel = _ui.allToolbarPanels.itemById(PANEL_ID)
    if panel or not create:
        return panel
    try:
        ws = _ui.workspaces.itemById(WORKSPACE_ID)
        tab = ws.toolbarTabs.itemById(TAB_ID) if ws else None
        if tab:
            return tab.toolbarPanels.add(PANEL_ID, "AICADBridge", "", False)
    except Exception:
        _log(traceback.format_exc(limit=2))
    return _ui.allToolbarPanels.itemById(FALLBACK_PANEL_ID)


def _log(msg):
    try:
        _app.log(f"[AICADBridge] {msg}")
    except Exception:
        pass


# ---------------------------------------------------------------- main-thread import
class _JobEventHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        data = json.loads(args.additionalInfo)
        slot = _pending.get(data["job_id"])
        try:
            slot["result"] = _import_step(data["path"], data["title"])
        except Exception:
            slot["result"] = (False, traceback.format_exc(limit=2))
        finally:
            slot["done"].set()


def _import_step(path, title):
    design = adsk.fusion.Design.cast(_app.activeProduct)
    if design is None:
        doc = _app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    root = design.rootComponent
    before = root.occurrences.count
    mgr = _app.importManager
    opts = mgr.createSTEPImportOptions(path)
    if not mgr.importToTarget(opts, root):
        return False, "Fusion STEP içe aktarmayı reddetti"
    if root.occurrences.count > before:
        occ = root.occurrences.item(root.occurrences.count - 1)
        try:
            occ.component.name = title
        except Exception:
            pass
    _app.activeViewport.fit()
    return True, f"Fusion 360: '{title}' yeni bileşen olarak içe aktarıldı"


def _handle_job(job):
    """Worker thread: download, then ask the main thread to import and wait for it."""
    url = (job.get("files") or {}).get("step")
    if not url:
        return False, "işte STEP dosyası yok"
    title = aicad_link.safe_name(job.get("title") or "AICADBridge")
    path = os.path.join(tempfile.mkdtemp(prefix="aicad_"), f"{title}.step")
    aicad_link.LinkClient.download(url, path)
    slot = {"done": threading.Event(), "result": (False, "zaman aşımı")}
    _pending[job["id"]] = slot
    _app.fireCustomEvent(EVENT_ID, json.dumps({"job_id": job["id"], "path": path, "title": title}))
    slot["done"].wait(120)
    _pending.pop(job["id"], None)
    return slot["result"]


def _worker_main(pairing):
    try:
        if pairing:
            if not _client.wait_for_pairing(should_stop=_stop.is_set):
                return
            _log("eşleşme tamamlandı")
        aicad_link.run_job_loop(_client, _handle_job, should_stop=_stop.is_set,
                                on_error=lambda e: _log(f"bağlantı: {e}"))
    except Exception:
        _log(traceback.format_exc())


def _start_worker(pairing=False):
    global _worker
    if _worker and _worker.is_alive():
        return
    _stop.clear()
    _worker = threading.Thread(target=_worker_main, args=(pairing,), daemon=True)
    _worker.start()


# ---------------------------------------------------------------- commands
class _ConnectCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        h = _ConnectExecute()
        args.command.execute.add(h)
        _handlers.append(h)


class _ConnectExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            if _client.paired:
                res = _ui.messageBox(
                    f"Bu Fusion 360 AICADBridge hesabına bağlı.\nSunucu: {_client.server}\n\n"
                    "Bağlantıyı kaldırmak ister misiniz?", "AICADBridge",
                    adsk.core.MessageBoxButtonTypes.YesNoButtonType)
                if res == adsk.core.DialogResults.DialogYes:
                    _stop.set()
                    _client.forget()
                    _refresh_tooltip()
                return
            info = _client.start_pairing()
            webbrowser.open(info["verification_url"])
            _ui.messageBox(
                f"Eşleştirme kodu:  {info['user_code']}\n\n"
                "Tarayıcıda açılan AICADBridge sayfasında (Entegrasyonlar) bu kodu onaylayın.\n"
                "Onaydan sonra web'den gönderdiğiniz modeller burada açılır.", "AICADBridge")
            _start_worker(pairing=True)
            _refresh_tooltip()
        except aicad_link.LinkError as e:
            _ui.messageBox(f"Bağlanılamadı: {e}", "AICADBridge")
        except Exception:
            _ui.messageBox(traceback.format_exc(), "AICADBridge")


def run(context):
    global _app, _ui, _client
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface
        _client = aicad_link.LinkClient("fusion", name=f"Fusion 360 · {os.environ.get('COMPUTERNAME', 'PC')}")

        ev = _app.registerCustomEvent(EVENT_ID)
        h = _JobEventHandler()
        ev.add(h)
        _handlers.append(h)

        cmd = _ui.commandDefinitions.itemById(CMD_CONNECT) or _ui.commandDefinitions.addButtonDefinition(
            CMD_CONNECT, "AICADBridge", _state_text())
        ch = _ConnectCreated()
        cmd.commandCreated.add(ch)
        _handlers.append(ch)
        cmd.tooltip = _state_text()
        panel = _find_panel(create=True)
        if panel and not panel.controls.itemById(CMD_CONNECT):
            ctrl = panel.controls.addCommand(cmd)
            try:
                ctrl.isPromoted = True
                ctrl.isPromotedByDefault = True
            except Exception:
                pass

        if _client.paired:  # runOnStartup: resume receiving models without any click
            _start_worker()
    except Exception:
        if _ui:
            _ui.messageBox("AICADBridge Link başlatılamadı:\n" + traceback.format_exc())


def stop(context):
    try:
        _stop.set()
        for pid in (PANEL_ID, FALLBACK_PANEL_ID):
            panel = _ui.allToolbarPanels.itemById(pid)
            ctrl = panel.controls.itemById(CMD_CONNECT) if panel else None
            if ctrl:
                ctrl.deleteMe()
        own = _ui.allToolbarPanels.itemById(PANEL_ID)
        if own:
            own.deleteMe()
        cmd = _ui.commandDefinitions.itemById(CMD_CONNECT)
        if cmd:
            cmd.deleteMe()
        _app.unregisterCustomEvent(EVENT_ID)
    except Exception:
        pass

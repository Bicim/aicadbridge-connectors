# SPDX-License-Identifier: GPL-3.0-or-later
"""AICADBridge Link for Blender 4.2+.

N-panel > AICADBridge: pair with the web app via a device code; models sent from the web are
imported (GLB, metres) into a new collection. Network on a thread, bpy calls on a timer.
"""
bl_info = {
    "name": "AICADBridge Link",
    "author": "Biçim",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > AICADBridge",
    "description": "Import parametric CAD models generated in AICADBridge",
    "category": "Import-Export",
}

import os
import platform
import queue
import tempfile
import threading
import webbrowser

import bpy

from . import aicad_link

_client = None
_stop = threading.Event()
_worker = None
_jobs = queue.Queue()      # (job, path, done_event, result_holder) waiting for the main thread
_status = {"text": "", "code": ""}


def _redraw():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _handle_job(job):
    """Worker thread: download GLB, hand it to the main thread, wait for the import result."""
    url = (job.get("files") or {}).get("glb")
    if not url:
        return False, "işte GLB dosyası yok"
    title = aicad_link.safe_name(job.get("title") or "AICADBridge")
    path = os.path.join(tempfile.mkdtemp(prefix="aicad_"), f"{title}.glb")
    aicad_link.LinkClient.download(url, path)
    holder = {"result": (False, "zaman aşımı"), "done": threading.Event()}
    _jobs.put((title, path, holder))
    holder["done"].wait(120)
    return holder["result"]


def _import_glb(title, path):
    before = set(bpy.data.objects)
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win):
        bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    coll = bpy.data.collections.new(title)
    bpy.context.scene.collection.children.link(coll)
    for o in new:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
    return True, f"Blender: '{title}' içe aktarıldı ({len(new)} nesne)"


def _timer():
    try:
        while True:
            title, path, holder = _jobs.get_nowait()
            try:
                holder["result"] = _import_glb(title, path)
                _status["text"] = holder["result"][1]
            except Exception as e:
                holder["result"] = (False, f"{type(e).__name__}: {e}")
            holder["done"].set()
            _redraw()
    except queue.Empty:
        pass
    return 0.5


def _worker_main(pairing):
    if pairing:
        try:
            ok = _client.wait_for_pairing(should_stop=_stop.is_set)
        except aicad_link.LinkError as e:
            _status["text"] = f"Eşleşme başarısız: {e}"
            return
        _status["code"] = ""
        if not ok:
            _status["text"] = "Eşleşme süresi doldu"
            return
    _status["text"] = "Bağlı, model bekleniyor"
    aicad_link.run_job_loop(_client, _handle_job, should_stop=_stop.is_set,
                            on_error=lambda e: _status.__setitem__("text", f"Bağlantı: {e}"))
    if not _client.paired:
        _status["text"] = "Bağlantı kaldırıldı"


def _start_worker(pairing=False):
    global _worker
    if _worker and _worker.is_alive():
        return
    _stop.clear()
    _worker = threading.Thread(target=_worker_main, args=(pairing,), daemon=True)
    _worker.start()


class AICAD_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__

    server: bpy.props.StringProperty(name="Sunucu", default=aicad_link.DEFAULT_SERVER)

    def draw(self, context):
        self.layout.prop(self, "server")


class AICAD_OT_connect(bpy.types.Operator):
    bl_idname = "aicad.connect"
    bl_label = "AICADBridge'e bağlan"
    bl_description = "Web hesabınla eşleştir"

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        _client.server = prefs.server
        try:
            info = _client.start_pairing()
        except aicad_link.LinkError as e:
            self.report({"ERROR"}, f"Bağlanılamadı: {e}")
            return {"CANCELLED"}
        _status["code"] = info["user_code"]
        _status["text"] = "Kodu web uygulamasında onayla"
        webbrowser.open(info["verification_url"])
        _start_worker(pairing=True)
        return {"FINISHED"}


class AICAD_OT_disconnect(bpy.types.Operator):
    bl_idname = "aicad.disconnect"
    bl_label = "Bağlantıyı kaldır"

    def execute(self, context):
        _stop.set()
        _client.forget()
        _status.update(text="Bağlı değil", code="")
        return {"FINISHED"}


class AICAD_PT_panel(bpy.types.Panel):
    bl_label = "AICADBridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AICADBridge"

    def draw(self, context):
        col = self.layout.column(align=True)
        if _status["code"]:
            col.label(text="Eşleştirme kodu:")
            box = col.box()
            box.scale_y = 1.6
            box.label(text=_status["code"])
        if _client.paired:
            col.label(text="● Bağlı", icon="LINKED")
            col.operator(AICAD_OT_disconnect.bl_idname, icon="UNLINKED")
        else:
            col.operator(AICAD_OT_connect.bl_idname, icon="URL")
        if _status["text"]:
            col.separator()
            col.label(text=_status["text"])


_classes = (AICAD_Prefs, AICAD_OT_connect, AICAD_OT_disconnect, AICAD_PT_panel)


def register():
    global _client
    for c in _classes:
        bpy.utils.register_class(c)
    _client = aicad_link.LinkClient("blender", name=f"Blender · {platform.node() or 'PC'}")
    bpy.app.timers.register(_timer, first_interval=1.0, persistent=True)
    if _client.paired:
        _start_worker()


def unregister():
    _stop.set()
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)

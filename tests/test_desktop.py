"""Tests for desktop/aicadbridge_desktop.py and rhino/AICADBridgeLink.py (fake COM objects, no network)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "desktop"))
import aicadbridge_desktop as d  # noqa: E402


# ----------------------------------------------------------------------- fake COM objects


class Recorder:
    """Accepts any attribute chain / call and records it, like a COM object proxy."""

    def __init__(self, log=None, name="app"):
        object.__setattr__(self, "_log", log if log is not None else [])
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attr):
        return Recorder(self._log, f"{self._name}.{attr}")

    def __setattr__(self, attr, value):
        self._log.append((f"{self._name}.{attr}=", (value,)))

    def __call__(self, *args):
        self._log.append((self._name, args))
        return Recorder(self._log, self._name + "()")


def fake_connect(log, only=None):
    def connect(prog_id):
        if only is not None and prog_id != only:
            return None
        log.append(("connect", (prog_id,)))
        return Recorder(log)
    return connect


STEP = Path("C:/inbox/part.step")


# ----------------------------------------------------------------------- adapter selection


def test_hosts_are_exactly_the_cloud_ids():
    assert set(d.HOSTS) == {"solidworks", "inventor", "solidedge", "catia", "autocad", "other"}
    assert d.select_adapter("other", lambda p: pytest.fail("other must not use COM")) is None
    assert d.select_adapter("fusion", lambda p: Recorder()) is None  # not served by this bridge


@pytest.mark.parametrize("host,prog_id", [
    ("solidworks", "SldWorks.Application"), ("inventor", "Inventor.Application"),
    ("solidedge", "SolidEdge.Application"), ("catia", "CATIA.Application"), ("autocad", "AutoCAD.Application"),
])
def test_select_adapter_uses_prog_id(host, prog_id):
    seen = []
    opener = d.select_adapter(host, lambda p: seen.append(p) or Recorder())
    assert seen == [prog_id] and callable(opener)


def test_select_adapter_none_when_app_unavailable():
    assert d.select_adapter("inventor", lambda p: None) is None


def test_inventor_opens_with_visible_flag():
    log = []
    ok, msg = d.select_adapter("inventor", fake_connect(log))(STEP)
    assert ok and "Inventor" in msg
    assert ("app.Documents.Open", (str(STEP), True)) in log


def test_solidedge_and_catia_documents_open():
    for host, name in (("solidedge", "Solid Edge"), ("catia", "CATIA")):
        log = []
        ok, msg = d.select_adapter(host, fake_connect(log))(STEP)
        assert ok and name in msg
        assert ("app.Documents.Open", (str(STEP),)) in log


def test_autocad_sends_import_command_to_active_document():
    log = []
    ok, _ = d.select_adapter("autocad", fake_connect(log))(STEP)
    assert ok
    cmds = [a[0] for n, a in log if n == "app.ActiveDocument.SendCommand"]
    assert cmds == ['_-IMPORT "C:/inbox/part.step"\n']
    assert not any(n == "app.Documents.Add" for n, _ in log)


def test_autocad_adds_document_when_none_active():
    log = []

    class NoDoc(Recorder):
        @property
        def ActiveDocument(self):
            raise RuntimeError("no active document")

        @property
        def Documents(self):
            return Recorder(self._log, "app.Documents")

    ok, _ = d.open_autocad(NoDoc(log), STEP)
    assert ok
    assert ("app.Documents.Add", ()) in log
    assert any(n == "app.Documents.Add().SendCommand" for n, _ in log)


# ----------------------------------------------------------------------- delivery / fallback


def test_deliver_com_success_reports_adapter_message():
    ok, msg = d.deliver("inventor", STEP, fake_connect([]))
    assert ok and msg.startswith("Inventor:")


def test_deliver_falls_back_when_com_unavailable():
    ok, msg = d.deliver("catia", STEP, lambda p: None)
    assert ok and "bulunamadı" in msg and str(STEP) in msg


def test_deliver_falls_back_when_adapter_raises():
    class Boom:
        @property
        def Documents(self):
            raise OSError("COM kaput")

        Visible = False

    ok, msg = d.deliver("solidedge", STEP, lambda p: Boom())
    assert ok and "COM kaput" in msg and str(STEP) in msg


def test_deliver_falls_back_when_adapter_returns_false(monkeypatch):
    adapter = d.ADAPTERS["solidworks"]
    monkeypatch.setitem(d.ADAPTERS, "solidworks", d.Adapter("solidworks", "SolidWorks", adapter.prog_id,
                                                            lambda app, p: (False, "SolidWorks dosyayı açamadı")))
    ok, msg = d.deliver("solidworks", STEP, lambda p: Recorder())
    assert ok and "açamadı" in msg and str(STEP) in msg


def test_other_opens_folder_only_once():
    opened, state = [], {}
    a = d.deliver("other", Path("C:/inbox/a.step"), folder_opener=opened.append, state=state)
    b = d.deliver("other", Path("C:/inbox/b.step"), folder_opener=opened.append, state=state)
    assert a[0] and b[0] and "a.step" in a[1]
    assert opened == [Path("C:/inbox")]


def test_unique_path_never_overwrites(tmp_path):
    p1 = d.unique_path(tmp_path, 'Flanş <M6>/"x"')
    assert p1 == tmp_path / "Flanş _M6___x_.step"
    p1.write_bytes(b"x")
    p2 = d.unique_path(tmp_path, 'Flanş <M6>/"x"')
    assert p2 != p1 and p2.name.startswith("Flanş _M6___x__") and p2.suffix == ".step"
    assert d.unique_path(tmp_path, "").name == "model.step"


def fake_download(content=b"ISO-10303-21;"):
    def download(url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(content)
        return Path(dest)
    return download


def test_handler_inbox_fallback_writes_file_and_reports_ok(tmp_path):
    inbox = tmp_path / "inbox"
    h = d.make_handler("autocad", inbox, connect=lambda p: None, download=fake_download(), say=lambda *a: None)
    ok, msg = h({"id": "1", "title": "Kutu", "files": {"step": "https://x/a.step"}})
    assert ok and (inbox / "Kutu.step").read_bytes().startswith(b"ISO-10303")
    assert str(inbox / "Kutu.step") in msg


def test_handler_other_saves_and_opens_folder_once(tmp_path):
    opened = []
    h = d.make_handler("other", tmp_path, download=fake_download(), folder_opener=opened.append, say=lambda *a: None)
    for i in range(2):
        ok, _ = h({"id": str(i), "title": "Part", "files": {"step": "u"}})
        assert ok
    assert sorted(p.name for p in tmp_path.glob("*.step"))[0].startswith("Part") and len(list(tmp_path.glob("*.step"))) == 2
    assert opened == [tmp_path]


def test_handler_without_step_url_fails(tmp_path):
    h = d.make_handler("other", tmp_path, download=fake_download(), say=lambda *a: None)
    assert h({"id": "1", "title": "x", "files": {"glb": "u"}}) == (False, "işte STEP dosyası yok")


def test_connect_com_without_pywin32_is_none():
    if importlib.util.find_spec("win32com") is None:
        assert d.connect_com("Inventor.Application") is None


def test_run_bridge_unknown_host_and_forget(tmp_path, monkeypatch):
    monkeypatch.setattr(d.aicad_link.Path, "home", classmethod(lambda cls: tmp_path))
    assert d.run_bridge("fusion", say=lambda *a: None) == 2
    assert d.run_bridge("other", forget=True, say=lambda *a: None) == 0


def test_run_bridge_pairs_and_runs_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(d.aicad_link.Path, "home", classmethod(lambda cls: tmp_path))
    jobs = [{"id": "j1", "title": "Plaka", "files": {"step": "u"}}]
    reports = []

    def fake_req(self, method, path, body=None, auth=False, timeout=30):
        if path == "/v1/connect/start":
            return 200, {"device_code": "dc", "user_code": "ABCD-1234", "verification_url": "https://x/v", "interval": 0}
        if path == "/v1/connect/poll":
            return 200, {"token": "t", "device_id": "dv"}
        if path.startswith("/v1/host/jobs/next"):
            return (200, jobs.pop()) if jobs else (204, None)
        reports.append((path, body))
        return 200, None

    monkeypatch.setattr(d.aicad_link.LinkClient, "_req", fake_req)
    monkeypatch.setattr(d.aicad_link.LinkClient, "download", staticmethod(fake_download()))
    monkeypatch.setattr(d.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    monkeypatch.setattr(d, "open_folder", lambda folder: None)  # never open Explorer from tests
    notes = []
    stop = {"n": 0}

    def should_stop():
        stop["n"] += 1
        return stop["n"] > 4

    rc = d.run_bridge("other", inbox=tmp_path / "in", say=lambda *a: None, notify=lambda t, x: notes.append(x),
                      should_stop=should_stop)
    assert rc == 0
    assert any("ABCD-1234" in n for n in notes)
    assert (tmp_path / "in" / "Plaka.step").exists()
    assert reports and reports[0][0] == "/v1/host/jobs/j1/result" and reports[0][1]["ok"] is True


# ----------------------------------------------------------------------- Rhino


def load_rhino_module():
    spec = importlib.util.spec_from_file_location("aicad_rhino_link", ROOT / "rhino" / "AICADBridgeLink.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rhino_script_is_import_safe_outside_rhino():
    mod = load_rhino_module()
    assert mod.Rhino is None and mod.HOST == "rhino"


def test_rhino_import_runs_on_ui_thread_and_redraws():
    mod = load_rhino_module()
    calls = []

    class Doc:
        class Views:
            @staticmethod
            def Redraw():
                calls.append("redraw")

        @staticmethod
        def Import(path):
            calls.append(("import", path))
            return True

    class FakeRhino:
        class RhinoDoc:
            ActiveDoc = Doc

        class RhinoApp:
            @staticmethod
            def InvokeOnUiThread(fn):
                calls.append("ui")
                fn()

    ok, msg = mod.import_step_on_ui(Path("C:/inbox/a.step"), rhino=FakeRhino)
    assert ok and "a.step" in msg
    assert calls[0] == "ui" and ("import", str(Path("C:/inbox/a.step"))) in calls and "redraw" in calls


def test_rhino_import_fails_without_document():
    mod = load_rhino_module()

    class FakeRhino:
        class RhinoDoc:
            ActiveDoc = None

        class RhinoApp:
            @staticmethod
            def InvokeOnUiThread(fn):
                fn()

    ok, msg = mod.import_step_on_ui(Path("a.step"), rhino=FakeRhino)
    assert not ok and "belge" in msg


def test_rhino_handler_reports_ok_even_if_import_fails(tmp_path):
    mod = load_rhino_module()
    h = mod.make_handler(tmp_path, importer=lambda p: (False, "Rhino içe aktaramadı"), download=fake_download())
    ok, msg = h({"id": "1", "title": "Kutu", "files": {"step": "u"}})
    assert ok and str(tmp_path / "Kutu.step") in msg
    ok2, msg2 = mod.make_handler(tmp_path, importer=lambda p: (True, "içe aktarıldı"), download=fake_download())(
        {"id": "2", "title": "Kutu", "files": {"step": "u"}})
    assert ok2 and msg2 == "içe aktarıldı" and (tmp_path / "Kutu_2.step").exists()

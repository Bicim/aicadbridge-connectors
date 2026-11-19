"""LinkClient against the live local API (simulates a host plugin end to end).

Needs: AICADBridge Cloud API on AICAD_API_URL (default http://127.0.0.1:8000) and the local Supabase
it uses (keys read from ../aicadbridge-cloud/.env). Skips when unavailable.
Run:  python -m pytest -q tests
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
import uuid
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
import aicad_link  # noqa: E402

API = os.environ.get("AICAD_API_URL", "http://127.0.0.1:8000")
ENV = {}
envf = ROOT.parent / "aicadbridge-cloud" / ".env"
if envf.exists():
    for line in envf.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()


def _http(method, url, body=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw and r.headers.get_content_type() == "application/json" else raw)


def _up():
    try:
        return _http("GET", API + "/health")[0] == 200 and ENV.get("SUPABASE_URL")
    except Exception:
        return False


live = pytest.mark.skipif(not _up(), reason="local API + Supabase required")


def _user_token():
    sb, svc, anon = ENV["SUPABASE_URL"], ENV["SUPABASE_SERVICE_KEY"], ENV["SUPABASE_ANON_KEY"]
    email, pw = f"link-{uuid.uuid4().hex[:8]}@example.com", "Pw-" + uuid.uuid4().hex
    hdr = lambda k: {"apikey": k, **({"Authorization": f"Bearer {k}"} if k.startswith("eyJ") else {})}  # noqa: E731
    _http("POST", sb + "/auth/v1/admin/users", {"email": email, "password": pw, "email_confirm": True}, hdr(svc))
    return _http("POST", sb + "/auth/v1/token?grant_type=password", {"email": email, "password": pw}, hdr(anon))[1]["access_token"]


@live
def test_pair_receive_and_report(tmp_path):
    tok = _user_token()
    H = {"Authorization": f"Bearer {tok}"}
    task = _http("POST", API + "/v1/tasks", {}, H)[1]
    # run a generation (SSE) to get a model
    _, raw = _http("POST", f"{API}/v1/tasks/{task['id']}/generate", {"prompt": "30x20x10 kutu"}, H)
    events = [json.loads(l[6:]) for l in raw.decode().splitlines() if l.startswith("data: ")]
    msg = events[-1]["message"]

    client = aicad_link.LinkClient("other", state_dir=tmp_path, name="pytest")
    client.server = API
    info = client.start_pairing()
    assert len(info["user_code"]) == 9 and not client.poll_pairing()
    _http("POST", API + "/v1/connect/approve", {"user_code": info["user_code"]}, H)
    assert client.poll_pairing() and client.paired
    assert json.loads((tmp_path / "other.json").read_text())["token"].startswith("aicad_dev_")
    assert client.me()["host"] == "other"

    dev_id = client.state["device_id"]
    _http("POST", f"{API}/v1/messages/{msg['id']}/send", {"device_id": dev_id}, H)

    got = {}
    stop = threading.Event()

    def handle(job):
        p = aicad_link.LinkClient.download(job["files"]["step"], tmp_path / "in" / "m.step")
        got["head"] = p.read_bytes()[:12]
        got["title"] = job["title"]
        stop.set()
        return True, "ok from pytest"

    t = threading.Thread(target=aicad_link.run_job_loop, args=(client, handle, stop.is_set), daemon=True)
    t.start()
    t.join(40)
    assert got.get("head") == b"ISO-10303-21" and got["title"] == "Kutu"

    # revoke from the web app -> plugin forgets its token on the next call
    _http("DELETE", f"{API}/v1/devices/{dev_id}", None, H)
    with pytest.raises(aicad_link.LinkError):
        client.next_job(wait=0)
    assert not client.paired


def test_bundle_zips(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import bundle

    bundle.OUT = tmp_path
    made = {p.name: p for p in bundle.build()}
    with zipfile.ZipFile(made["AICADBridgeLink-fusion.zip"]) as z:
        names = z.namelist()
        assert "AICADBridgeLink/AICADBridgeLink.py" in names and "AICADBridgeLink/aicad_link.py" in names
        assert "AICADBridgeLink/AICADBridgeLink.manifest" in names
    with zipfile.ZipFile(made["aicadbridge_link-blender.zip"]) as z:
        assert {"__init__.py", "aicad_link.py", "blender_manifest.toml"} <= set(z.namelist())
    with zipfile.ZipFile(made["AICADBridgeLink-solidworks.zip"]) as z:
        assert "AICADBridgeLink/run.bat" in z.namelist()


def test_safe_name():
    assert aicad_link.safe_name('Flanş <M6>/"x"') == "Flanş _M6___x_"
    assert aicad_link.safe_name("") == "model"

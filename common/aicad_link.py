"""AICADBridge host link — shared by the Fusion 360, Blender and SolidWorks plugins.

Standard library only (host Pythons ship without extra packages).
Protocol (AICADBridge Cloud API):
  POST /v1/connect/start {host, name}      -> {device_code, user_code, verification_url, interval}
  POST /v1/connect/poll  {device_code}     -> 202 pending | 200 {token, device_id} | 410 expired
  GET  /v1/host/jobs/next?wait=25 (Bearer) -> 204 | 200 {id, title, ir, files: {step, glb, stl}}
  POST /v1/host/jobs/{id}/result {ok, message}
Only outbound HTTPS; nothing listens on the user's machine.
"""
from __future__ import annotations

import json
import os
import platform
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "0.2.0"
DEFAULT_SERVER = os.environ.get("AICAD_API_URL", "http://127.0.0.1:8000")


class LinkError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class LinkClient:
    def __init__(self, host: str, state_dir: str | os.PathLike | None = None, name: str | None = None):
        self.host = host
        self.name = name or f"{platform.node() or 'PC'}"
        self.state_path = Path(state_dir or Path.home() / ".aicadbridge") / f"{host}.json"
        self.state: dict = {}
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.state = {}
        self.state.setdefault("server", DEFAULT_SERVER)

    # ------------------------------------------------------------- state
    @property
    def server(self) -> str:
        return self.state["server"].rstrip("/")

    @server.setter
    def server(self, url: str) -> None:
        self.state["server"] = url.strip().rstrip("/")
        self._save()

    @property
    def paired(self) -> bool:
        return bool(self.state.get("token"))

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass

    def forget(self) -> None:
        self.state.pop("token", None)
        self.state.pop("device_id", None)
        self._save()

    # ------------------------------------------------------------- http
    def _req(self, method: str, path: str, body: dict | None = None, auth: bool = False,
             timeout: float = 30) -> tuple[int, dict | None]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.server + path, data=data, method=method)
        req.add_header("User-Agent", f"AICADBridgeLink/{VERSION} ({self.host})")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", f"Bearer {self.state.get('token', '')}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"{}").get("detail", e.reason)
            except ValueError:
                detail = e.reason
            if e.code == 401 and auth:
                self.forget()  # revoked from the web app
            raise LinkError(e.code, str(detail)) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise LinkError(0, f"sunucuya ulaşılamıyor: {getattr(e, 'reason', e)}") from None

    # ------------------------------------------------------------- pairing
    def start_pairing(self) -> dict:
        _, j = self._req("POST", "/v1/connect/start", {"host": self.host, "name": self.name})
        self.state["pending"] = j["device_code"]
        return j

    def poll_pairing(self) -> bool:
        """True once approved (token stored). Raises LinkError(410) when the code expired."""
        code = self.state.get("pending")
        if not code:
            return self.paired
        status, j = self._req("POST", "/v1/connect/poll", {"device_code": code})
        if status == 202:
            return False
        self.state.pop("pending", None)
        self.state["token"], self.state["device_id"] = j["token"], j["device_id"]
        self._save()
        return True

    def wait_for_pairing(self, timeout_s: float = 600, interval: float = 3, should_stop=lambda: False) -> bool:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end and not should_stop():
            if self.poll_pairing():
                return True
            time.sleep(interval)
        return False

    def me(self) -> dict:
        return self._req("GET", "/v1/host/me", auth=True)[1]

    # ------------------------------------------------------------- jobs
    def next_job(self, wait: int = 25) -> dict | None:
        status, j = self._req("GET", f"/v1/host/jobs/next?wait={wait}", auth=True, timeout=wait + 15)
        return j if status == 200 else None

    def report(self, job_id: str, ok: bool, message: str = "") -> None:
        self._req("POST", f"/v1/host/jobs/{job_id}/result", {"ok": ok, "message": message[:1900]}, auth=True)

    @staticmethod
    def download(url: str, dest: str | os.PathLike) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
        return dest


def safe_name(title: str, fallback: str = "model") -> str:
    keep = "".join(c if c.isalnum() or c in " -_." else "_" for c in (title or "")).strip()
    return keep[:60] or fallback


def run_job_loop(client: LinkClient, handle, should_stop=lambda: False, on_error=lambda e: None) -> None:
    """Long-poll forever; `handle(job)` returns (ok, message). Backs off on network errors."""
    backoff = 2.0
    while not should_stop():
        if not client.paired:
            return
        try:
            job = client.next_job()
            backoff = 2.0
        except LinkError as e:
            on_error(e)
            if e.status == 401:
                return
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        if not job:
            continue
        try:
            ok, msg = handle(job)
        except Exception as e:  # never kill the loop on one bad job
            ok, msg = False, f"{type(e).__name__}: {e}"
        try:
            client.report(job["id"], ok, msg)
        except LinkError as e:
            on_error(e)

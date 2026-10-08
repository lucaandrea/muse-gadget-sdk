"""Browser PKCE handoff to an exact loopback callback; no browser cookies copied."""
from __future__ import annotations
import base64
import hashlib
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode
import httpx
from .grep import CALLBACK, ORIGIN, GrepError, save_auth

PAGE = b'''<!doctype html><meta charset="utf-8"><title>Connect Grep to Muse</title>
<h1 id="status">Completing Muse authorization...</h1>
<script src="/complete.js"></script>'''
SCRIPT = b'''const p=new URLSearchParams(location.hash.slice(1));history.replaceState(null,'','/grep/callback');
fetch('/grep/complete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:p.get('code'),state:p.get('state')})})
.then(async r=>{const d=await r.json();document.getElementById('status').textContent=d.message;})
.catch(()=>document.getElementById('status').textContent='Connection failed. Restart muse-companion grep-connect.');'''


def connect(settings):
    if not settings.grep_external_token:
        raise GrepError("Set GREP_EXTERNAL_ACCESS_TOKEN in the companion .env first.")
    verifier, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    deadline, done = time.monotonic() + 600, False
    outcome = None
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def setup(self):
            super().setup()
            self.connection.settimeout(10)
        def send(self, code, content, kind="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)
        def valid_host(self): return self.headers.get("Host") == "127.0.0.1:8766"
        def do_GET(self):
            if not self.valid_host(): self.send(400, b'{}'); return
            if self.path == "/grep/callback": self.send(200, PAGE, "text/html; charset=utf-8")
            elif self.path == "/complete.js": self.send(200, SCRIPT, "application/javascript")
            else: self.send(404, b'{}')
        def do_POST(self):
            nonlocal done, outcome
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 1024: raise ValueError()
                payload = self.rfile.read(length)
            except (ValueError, OSError):
                self.send(400, b'{}'); return
            if not self.valid_host() or self.path != "/grep/complete" or self.headers.get("Origin") != "http://127.0.0.1:8766":
                self.send(403, b'{}'); return
            try:
                if time.monotonic() >= deadline or done: raise ValueError()
                value = json.loads(payload)
                if not isinstance(value, dict): raise ValueError()
                if not isinstance(value.get("state"), str) or not secrets.compare_digest(value["state"], state): raise ValueError()
                code = value.get("code")
                if not isinstance(code, str) or len(code) != 43: raise ValueError()
            except (ValueError, TypeError):
                self.send(400, b'{"message":"Invalid or expired login. Restart grep-connect."}'); return
            done = True
            try:
                with httpx.Client(timeout=20, follow_redirects=False) as client:
                    response = client.post(ORIGIN + "/api/extension/token", headers={"Authorization": "Bearer " + settings.grep_external_token, "x-grep-extension-session": "extension-signed-out"}, json={"code": code, "code_verifier": verifier, "redirect_uri": CALLBACK})
                    if response.status_code != 200: raise GrepError("Grep could not complete login. Check the deployment token and restart grep-connect.")
                    data = response.json()
                    data["expires_at"] = time.time() + min(int(data.get("expiresIn", 0)), 604800)
                    path = settings.data_dir / "grep-auth.json"
                    # Revoke the previous child after obtaining a valid restricted replacement.
                    if data.get("integration") != "muse": raise GrepError("Update Grep: restricted Muse sessions are not enabled.")
                    if path.exists():
                        old = json.loads(path.read_text())
                        if old.get("token") and old["token"] != data.get("token"):
                            revoked = client.post(ORIGIN + "/api/logout", headers={"Authorization": "Bearer " + settings.grep_external_token, "x-grep-extension-session": old["token"]}, json={})
                            if revoked.status_code != 200: raise GrepError("Previous Grep session could not be revoked. Retry connection.")
                    save_auth(path, data)
                outcome = "Grep connected to Muse. Search and approved reads are ready. You can close this tab."
                self.send(200, json.dumps({"message": outcome}).encode())
            except (httpx.HTTPError, ValueError, OSError):
                outcome = "Grep connection failed. Restart muse-companion grep-connect."
                self.send(400, json.dumps({"message": outcome}).encode())
    with HTTPServer(("127.0.0.1", 8766), Handler) as server:
        server.timeout = 1
        url = ORIGIN + "/api/extension/authorize?" + urlencode({"redirect_uri": CALLBACK, "code_challenge": challenge, "state": state})
        print("Open this URL in your signed-in Grep browser to grant Muse read access (up to seven days):", flush=True)
        print(url, flush=True)
        while not done and time.monotonic() < deadline:
            server.handle_request()
    if not done: raise GrepError("Grep login timed out; run grep-connect again.")
    print(outcome, flush=True)

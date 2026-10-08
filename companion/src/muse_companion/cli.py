from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import uvicorn

from .config import Settings
from .openai_api import OpenAI
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Muse pocket companion")
    parser.add_argument("--env", type=Path, default=Path("../.env"))
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--cert", type=Path)
    serve.add_argument("--key", type=Path)
    sub.add_parser("doctor")
    sub.add_parser("grep-connect", help="Authorize read-only Grep access with employee browser sign-in")
    sub.add_parser("grep-status", help="Check Grep authorization without printing credentials")
    sub.add_parser("grep-disconnect", help="Revoke the Grep child session and remove its local credential")
    service = sub.add_parser("install-service", help="Install a per-user macOS launch agent using the existing local TLS certificate")
    service.add_argument("--port", type=int, default=8765)
    pair = sub.add_parser("pair")
    pair.add_argument("--name", default="Muse pocket")
    pair.add_argument("--url", required=True, help="wss://host:8765/v1/device")
    pair.add_argument("--ca", type=Path)
    tls = sub.add_parser("local-tls")
    tls.add_argument("--address", required=True, help="This computer's LAN IP; used as a certificate SAN")
    args = parser.parse_args()
    settings = Settings.load(args.env)
    if args.command == "grep-connect":
        from .grep_auth import connect
        connect(settings)
        return
    if args.command in ("grep-status", "grep-disconnect"):
        from .grep import Grep
        store = Store(settings.data_dir / "muse.sqlite3")
        grep = Grep(settings, store)
        try:
            if args.command == "grep-disconnect": print(json.dumps(asyncio.run(grep.disconnect())))
            else:
                status = grep.status()
                if status["authorized"]:
                    user = asyncio.run(grep.request("GET", "/api/auth/user"))
                    status["connected"] = bool(user.get("user"))
                print(json.dumps(status, indent=2))
        finally: store.close()
        return
    if args.command == "serve":
        from .app import create_app
        app = create_app(settings)
        print(f"Companion access key is in {settings.data_dir / 'owner-token'} (not the OpenAI key).")
        uvicorn.run(app, host=args.host, port=args.port, ssl_certfile=str(args.cert) if args.cert else None,
                    ssl_keyfile=str(args.key) if args.key else None, access_log=False, ws_max_size=2**20)
    elif args.command == "install-service":
        from .service import install
        try:
            root = install(settings, args.port)
            print(f"Installed ai.muse.pocket-companion. Runtime and private data: {root}")
        except (ValueError, subprocess.CalledProcessError) as exc:
            parser.error(str(exc) if isinstance(exc, ValueError) else "Service installation failed; inspect the service log")
    elif args.command == "pair":
        from urllib.parse import urlparse
        url = urlparse(args.url)
        if url.scheme != "wss" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path != "/v1/device":
            parser.error("Use wss://host:port/v1/device without credentials, a query or a fragment")
        store = Store(settings.data_dir / "muse.sqlite3")
        device = store.create_device(args.name)
        device["url"] = args.url
        device["ca"] = args.ca.read_text() if args.ca else ""
        destination = settings.data_dir / f"pairing-{device['id']}.json"
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(device, handle)
        store.close()
        print(f"Private provisioning file: {destination}")
    elif args.command == "local-tls":
        import ipaddress
        address = str(ipaddress.ip_address(args.address))
        cert, key = settings.data_dir / "local-cert.pem", settings.data_dir / "local-key.pem"
        if cert.exists() or key.exists():
            parser.error("Local certificate already exists; keep it so paired devices continue to trust it")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365", "-keyout", str(key),
                        "-out", str(cert), "-subj", "/CN=Muse Companion", "-addext", f"subjectAltName=IP:{address},IP:127.0.0.1,DNS:localhost"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        key.chmod(0o600)
        print(f"Certificate: {cert}\nPrivate key: {key}")
    elif args.command == "doctor":
        async def doctor():
            store = Store(settings.data_dir / "muse.sqlite3")
            provider = OpenAI(settings, store)
            try:
                data = (await provider.request("GET", "models")).json()
                available = {item["id"] for item in data["data"]}
                for model in (settings.model, settings.small_model, settings.deep_model, settings.live_model, settings.transcription_model, "gpt-realtime-translate"):
                    print(f"{model}: {'available' if model in available else 'not listed'}")
                print("No inference requested. Credentials were not printed.")
            finally:
                await provider.close()
                store.close()
        asyncio.run(doctor())


if __name__ == "__main__":
    main()

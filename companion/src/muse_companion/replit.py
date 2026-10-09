"""Replit's official MCP transport with owner-initiated OAuth and bounded tools."""
import asyncio
import base64
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse

ORIGIN = "https://mcp.replit.com/server/mcp"
COMMANDS = {
    "replit_create": {"tool": "create_app_from_prompt", "description": "Create a Replit prototype from this brief", "fields": ["app_description", "app_stack", "name"]},
    "replit_update": {"tool": "update_app_using_prompt", "description": "Update the selected Replit app", "fields": ["repl_id", "change_description"]},
    "replit_publish": {"tool": "publish_app", "description": "Publish the selected Replit app", "fields": ["repl_id"]},
}
READ_TOOLS = {"list_apps", "search_apps", "resolve_app_by_name", "ask_question"}


class ReplitStorage:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    def cipher(self):
        from cryptography.fernet import Fernet
        path = self.settings.data_dir / "owner-token"
        secret = self.settings.owner_token or (path.read_text().strip() if path.exists() else "")
        if not secret:
            raise ValueError("Configure companion owner access before connecting Replit")
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("muse-replit-v1:" + secret).encode()).digest()))

    def read(self, name):
        value = self.store.setting("replit:" + name)
        if not value:
            return None
        try:
            return json.loads(self.cipher().decrypt(value.encode()))
        except Exception:
            raise ValueError("Saved Replit authorization could not be read; reconnect the account") from None

    def write(self, name, value):
        self.store.set_setting("replit:" + name, self.cipher().encrypt(json.dumps(value).encode()).decode())

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        value = self.read("tokens")
        if not value:
            return None
        expiry = self.store.setting("replit:expires_at")
        if expiry is not None:
            value["expires_in"] = max(0, int(expiry - time.time()))
        return OAuthToken.model_validate(value)

    async def set_tokens(self, tokens):
        with self.store.transaction():
            self.write("tokens", tokens.model_dump(mode="json"))
            self.store.set_setting("replit:expires_at", time.time() + tokens.expires_in if tokens.expires_in is not None else None)
            self.store.set_setting("replit:authorized_at", time.time())

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        value = self.read("client")
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, info):
        self.write("client", info.model_dump(mode="json"))


class Replit:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.storage = ReplitStorage(settings, store)
        self.lock = asyncio.Lock()
        self.login_task = None
        self.callback = None
        self.authorization_url = ""
        self.expected_state = ""
        self.deadline = 0
        self.error = ""

    def catalog(self):
        return {name: {**config, "kind": "replit_mcp", "read_only": False} for name, config in COMMANDS.items()} if self.settings.public_url else {}

    def status(self):
        return {"configured": bool(self.settings.public_url), "authorized": bool(self.store.setting("replit:tokens")),
                "expires_at": self.store.setting("replit:expires_at"), "authorization_url": self.authorization_url,
                "connecting": bool(self.login_task and not self.login_task.done()), "error": self.error,
                "tools": self.store.setting("replit:tools", []), "note": "Authorization and tool discovery are separate from any app build or publication."}

    async def begin(self):
        if not self.settings.public_url:
            raise ValueError("Set MUSE_PUBLIC_URL to the companion's HTTPS origin before connecting Replit")
        if self.login_task and not self.login_task.done():
            return self.status()
        self.error = self.authorization_url = self.expected_state = ""
        self.callback = asyncio.get_running_loop().create_future()
        self.deadline = time.time() + 600

        async def login():
            try:
                async with asyncio.timeout(600):
                    async with self.session(interactive=True) as session:
                        result = await session.list_tools()
                        self.store.set_setting("replit:tools", [tool.name for tool in result.tools if tool.name in READ_TOOLS | {c["tool"] for c in COMMANDS.values()}])
            except asyncio.CancelledError:
                raise
            except Exception:
                self.error = "Replit connection could not finish. Retry sign-in; no app action was requested."
            finally:
                self.authorization_url = self.expected_state = ""

        self.login_task = asyncio.create_task(login())
        return self.status()

    async def redirect(self, url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or not (parsed.hostname == "replit.com" or parsed.hostname.endswith(".replit.com")):
            raise ValueError("Replit returned an unexpected authorization origin")
        self.expected_state = parse_qs(parsed.query).get("state", [""])[0]
        if not self.expected_state:
            raise ValueError("Authorization omitted its state")
        self.authorization_url = url

    async def receive_callback(self):
        return await self.callback

    def complete(self, code, state):
        if not self.callback or self.callback.done() or time.time() >= self.deadline or not self.expected_state or not secrets.compare_digest(state, self.expected_state) or not 1 <= len(code) <= 4096:
            raise ValueError("Invalid or expired Replit authorization; start sign-in again")
        self.callback.set_result((code, state))
        self.authorization_url = ""

    @asynccontextmanager
    async def session(self, interactive=False):
        from mcp import ClientSession
        from mcp.client.auth import OAuthClientProvider
        from mcp.client.streamable_http import streamablehttp_client
        from mcp.shared.auth import OAuthClientMetadata

        async def no_interaction(url):
            raise ValueError("Reconnect Replit from Tools before using this action")

        async with self.lock:
            auth = OAuthClientProvider(server_url=ORIGIN, client_metadata=OAuthClientMetadata(
                client_name="Muse Companion", redirect_uris=[self.settings.public_url + "/api/replit/callback"],
                grant_types=["authorization_code", "refresh_token"], response_types=["code"], token_endpoint_auth_method="none"),
                storage=self.storage, redirect_handler=self.redirect if interactive else no_interaction,
                callback_handler=self.receive_callback, timeout=600 if interactive else 30)
            async with streamablehttp_client(ORIGIN, auth=auth, timeout=30, sse_read_timeout=600) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    def validate(self, name, args):
        command = COMMANDS.get(name)
        if not command or set(args) != set(command["fields"]) or any(not isinstance(v, str) or not v.strip() or len(v) > 24000 for v in args.values()):
            raise ValueError("Provide every required Replit action field")
        if name == "replit_create" and args["app_stack"] not in {"react_website", "mobile_app", "design", "slides", "animation", "data_visualization", "3d_game", "document", "spreadsheet"}:
            raise ValueError("Choose a supported Replit app stack, such as react_website")

    async def call(self, tool, arguments):
        if tool not in READ_TOOLS | {c["tool"] for c in COMMANDS.values()}:
            raise ValueError("Unsupported Replit operation")
        if not self.store.setting("replit:tokens"):
            raise ValueError("Connect Replit from Tools first")
        try:
            async with asyncio.timeout(180):
                async with self.session() as session:
                    available = await session.list_tools()
                    spec = next((t for t in available.tools if t.name == tool), None)
                    if spec is None:
                        raise ValueError("This Replit account does not expose the requested tool")
                    from jsonschema import validate
                    validate(arguments, spec.inputSchema)
                    result = await session.call_tool(tool, arguments)
                    if result.isError:
                        raise ValueError("Replit did not confirm the operation; inspect its app before retrying")
                    value = result.model_dump(mode="json")
                    if len(json.dumps(value)) > 24000:
                        raise ValueError("Replit response exceeded the receipt limit; inspect the app before retrying")
                    return {"ok": True, "service": "replit", "tool": tool, "receipt": value,
                            "note": "Replit returned this receipt. A build request may still be running; publication requires its own approval."}
        except ValueError:
            raise
        except Exception:
            raise RuntimeError("Replit request did not finish; inspect external app state before retrying") from None

    async def execute(self, name, args, operation):
        self.validate(name, args)
        arguments = {"appDescription": args["app_description"], "app_stack": args["app_stack"], "userSpecifiedAppName": args["name"]} if name == "replit_create" else {"replId": args["repl_id"]}
        if name == "replit_update":
            arguments["changeDescription"] = args["change_description"]
        result = await self.call(COMMANDS[name]["tool"], arguments)
        return {**result, "operation_id": operation}

    async def close(self):
        if self.login_task and not self.login_task.done():
            self.login_task.cancel()
            await asyncio.gather(self.login_task, return_exceptions=True)


def mount_replit(app, replit, owner):
    @app.get("/api/replit/status")
    async def status(user=Depends(owner)):
        return replit.status()

    @app.post("/api/replit/connect")
    async def connect(user=Depends(owner)):
        return await replit.begin()

    @app.get("/api/replit/callback")
    async def callback(code: str = "", state: str = ""):
        replit.complete(code, state)
        return HTMLResponse("<!doctype html><title>Muse · Replit connection</title><h1>Authorization received</h1><p>Return to Muse Tools to check the connection.</p>")

    @app.post("/api/replit/disconnect")
    async def disconnect(user=Depends(owner)):
        await replit.close()
        for key in ("tokens", "client", "expires_at", "authorized_at", "tools"):
            replit.store.set_setting("replit:" + key, None)
        return {"ok": True, "note": "Local authorization removed. Revoke Muse in Replit's connected apps to revoke the grant remotely."}

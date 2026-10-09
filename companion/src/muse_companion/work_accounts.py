"""Owner-only account grants, fixed destinations, and bounded official API reads."""
import asyncio
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import quote, urlencode

import httpx
from fastapi import Depends
from fastapi.responses import HTMLResponse
from pydantic import Field

from .models import StrictModel
from .store import new_id
from .vault import Vault
from .work_apps import FIELDS

PROVIDERS = {
    "slack": {"authorize": "https://slack.com/oauth/v2/authorize", "token": "https://slack.com/api/oauth.v2.access",
              "scope": "chat:write,channels:read,groups:read", "destination": "channel"},
    "linear": {"authorize": "https://linear.app/oauth/authorize", "token": "https://api.linear.app/oauth/token",
               "scope": "read,write", "destination": "team_id"},
    "google_calendar": {"authorize": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
                        "scope": "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.calendarlist.readonly",
                        "destination": "calendar_id"},
}


class Destination(StrictModel):
    destination: str = Field(min_length=1, max_length=250)
    project_id: str = Field(default="", max_length=64)
    sync_enabled: bool = False


class WorkAccounts:
    def __init__(self, settings, store, integrations, studio, *, transport=None):
        self.settings, self.store, self.integrations, self.studio = settings, store, integrations, studio
        self.transport = transport
        self.vault = Vault(settings, store, "work-accounts")
        self.locks = {name: asyncio.Lock() for name in PROVIDERS}
        self.sync_lock = asyncio.Lock()
        integrations.work_accounts = self
        self.reload_catalog()

    def spec(self, service):
        if service not in PROVIDERS:
            raise ValueError("Choose Slack, Linear, or Google Calendar")
        return PROVIDERS[service]

    def config(self, service):
        return self.store.setting("work-config:" + service, {})

    def state(self, service):
        self.spec(service)
        return self.store.setting("work-state:" + service, {})

    def status(self):
        return [{"service": name, "oauth_configured": bool(self.settings.public_url and self.settings.work_oauth.get(name, {}).get("client_id") and self.settings.work_oauth.get(name, {}).get("client_secret")),
                 "redirect_uri": self.settings.public_url + "/api/work-accounts/" + name + "/callback" if self.settings.public_url else "",
                 "requested_scopes": spec["scope"], **self.state(name), **self.config(name),
                 "sync": self.store.setting("work-sync:" + name, {}),
                 "recovery": "Reconnect this account if its authorization has expired. Choose a fixed destination before preparing actions."}
                for name, spec in PROVIDERS.items()]

    def reload_catalog(self):
        for service, spec in PROVIDERS.items():
            name = "work_" + service
            self.integrations.commands.pop(name, None)
            config, state = self.config(service), self.state(service)
            if config.get("destination") and state.get("connected"):
                self.integrations.commands[name] = {"kind": service, "description": "Reviewed " + service.replace("_", " ") + " action",
                    "fields": sorted(FIELDS[service]), spec["destination"]: config["destination"],
                    "account_revision": state["revision"], "account_id": state["account_id"], "managed_account": service}

    def begin(self, service):
        spec = self.spec(service)
        client = self.settings.work_oauth.get(service, {})
        if not self.settings.public_url or not client.get("client_id") or not client.get("client_secret"):
            raise ValueError("Register this account's OAuth app and set its client ID, client secret, and the companion HTTPS address first")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        redirect = self.settings.public_url + "/api/work-accounts/" + service + "/callback"
        self.vault.write("pending:" + service, {"state": state, "verifier": verifier, "expires": time.time() + 600, "redirect": redirect})
        params = {"client_id": client["client_id"], "redirect_uri": redirect, "scope": spec["scope"], "state": state, "response_type": "code"}
        # Linear documents PKCE; Slack confidential clients authenticate using
        # the client secret. Google's web-server flow also uses a client secret.
        if service == "linear":
            params.update(code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="), code_challenge_method="S256")
        if service == "google_calendar":
            params.update(access_type="offline", prompt="consent")
        return {"authorization_url": spec["authorize"] + "?" + urlencode(params)}

    async def token_request(self, service, data):
        client = self.settings.work_oauth.get(service, {})
        response = await self.fetch("POST", self.spec(service)["token"], data={**client, **data})
        token = response.get("access_token")
        if response.get("error") or (service == "slack" and response.get("ok") is not True) or not isinstance(token, str) or not token:
            raise ValueError("Account authorization was rejected. Reconnect this account")
        return {"access_token": token, "refresh_token": response.get("refresh_token", ""), "scope": response.get("scope", ""),
                "expires_at": time.time() + float(response["expires_in"]) if response.get("expires_in") else None}

    async def complete(self, service, code, state):
        self.spec(service)
        async with self.locks[service]:
            # Consume before the remote exchange: callback replay cannot install
            # another account, even after failure or process restart.
            with self.store.transaction():
                pending = self.vault.read("pending:" + service)
                if not pending or pending["expires"] <= time.time() or not secrets.compare_digest(state, pending["state"]) or not 1 <= len(code) <= 4096:
                    raise ValueError("Invalid or expired authorization. Start sign-in again")
                self.vault.write("pending:" + service, None)
            data = {"grant_type": "authorization_code", "code": code, "redirect_uri": pending["redirect"]}
            if service == "linear":
                data["code_verifier"] = pending["verifier"]
            tokens = await self.token_request(service, data)
            account = await self.identify(service, tokens["access_token"])
            with self.store.transaction():
                self.vault.write(service, tokens)
                self.store.set_setting("work-state:" + service, {"connected": True, "account_id": account["id"], "account_name": account["name"],
                    "revision": new_id(), "expires_at": tokens["expires_at"], "scopes": tokens["scope"], "checked_at": time.time(), "error": ""})
                # New grant must have its destination reviewed, including when
                # reconnecting as a different account with the same alias.
                self.store.set_setting("work-config:" + service, {})
            self.reload_catalog()

    async def fetch(self, method, url, **kwargs):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=self.transport) as client:
                async with client.stream(method, url, **kwargs) as response:
                    if not response.is_success:
                        raise ValueError(f"Work service returned HTTP {response.status_code}. Check account access and retry the read")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise ValueError("Work service response is too large. Narrow the selected source")
                    value = json.loads(body)
                    if not isinstance(value, dict) or value.get("errors") or value.get("ok") is False:
                        raise ValueError("Work service denied the request. Check this account's permissions")
                    return value
        except (httpx.HTTPError, json.JSONDecodeError):
            raise ValueError("Work service could not be reached. Recheck its connection") from None

    async def identify(self, service, token):
        headers = {"Authorization": "Bearer " + token}
        if service == "slack":
            value = await self.fetch("POST", "https://slack.com/api/auth.test", headers=headers)
            return {"id": value["team_id"] + ":" + value["user_id"], "name": value.get("team", "Slack")}
        if service == "linear":
            value = await self.fetch("POST", "https://api.linear.app/graphql", headers=headers,
                                     json={"query": "{ viewer { id name } }"})
            return value["data"]["viewer"]
        value = await self.fetch("GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList/primary", headers=headers)
        return {"id": value["id"], "name": value.get("summary", "Google Calendar")}

    async def token(self, service):
        self.spec(service)
        async with self.locks[service]:
            tokens = self.vault.read(service)
            if not tokens or not self.state(service).get("connected"):
                raise ValueError("Connect this work account in Tools first")
            if tokens.get("expires_at") and tokens["expires_at"] <= time.time() + 60:
                if not tokens.get("refresh_token"):
                    raise ValueError("Work account authorization expired. Reconnect it in Tools")
                fresh = await self.token_request(service, {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
                if not fresh["refresh_token"]:
                    fresh["refresh_token"] = tokens["refresh_token"]
                if not fresh["scope"]:
                    fresh["scope"] = tokens["scope"]
                self.vault.write(service, fresh)
                self.store.set_setting("work-state:" + service, {**self.state(service), "expires_at": fresh["expires_at"], "scopes": fresh["scope"]})
                tokens = fresh
            return tokens["access_token"]

    async def check(self, service):
        try:
            account = await self.identify(service, await self.token(service))
            if account["id"] != self.state(service)["account_id"]:
                raise ValueError("The authorized account changed. Reconnect it")
            self.store.set_setting("work-state:" + service, {**self.state(service), "checked_at": time.time(), "error": ""})
        except ValueError as exc:
            self.store.set_setting("work-state:" + service, {**self.state(service), "checked_at": time.time(), "error": str(exc)})
            raise
        return self.state(service)

    async def destinations(self, service):
        headers = {"Authorization": "Bearer " + await self.token(service)}
        if service == "slack":
            value = await self.fetch("GET", "https://slack.com/api/conversations.list", headers=headers,
                                     params={"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 200})
            return {"items": [{"id": x["id"], "name": x["name"]} for x in value.get("channels", []) if x.get("is_member")],
                    "truncated": bool(value.get("response_metadata", {}).get("next_cursor"))}
        if service == "linear":
            value = await self.fetch("POST", "https://api.linear.app/graphql", headers=headers,
                json={"query": "{ teams(first:100) { nodes { id name } pageInfo { hasNextPage } } }"})
            return {"items": value["data"]["teams"]["nodes"], "truncated": value["data"]["teams"]["pageInfo"]["hasNextPage"]}
        value = await self.fetch("GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList", headers=headers,
                                 params={"minAccessRole": "writer", "maxResults": 250})
        return {"items": [{"id": x["id"], "name": x.get("summary", x["id"])} for x in value.get("items", [])], "truncated": bool(value.get("nextPageToken"))}

    async def configure(self, service, config):
        self.spec(service)
        self.studio.validate_links(project_id=config.project_id)
        revision = self.state(service).get("revision")
        choices = await self.destinations(service)
        item = next((x for x in choices["items"] if x["id"] == config.destination), None)
        if item is None:
            raise ValueError("Choose an accessible destination from this account's list")
        async with self.locks[service]:
            if not revision or self.state(service).get("revision") != revision:
                raise ValueError("The account changed while loading destinations. Choose the destination again")
            self.store.set_setting("work-config:" + service, {**config.model_dump(), "destination_name": item["name"]})
            self.reload_catalog()
        return self.config(service)

    async def disconnect(self, service):
        self.spec(service)
        async with self.locks[service]:
            with self.store.transaction():
                self.vault.write(service, None)
                self.vault.write("pending:" + service, None)
                self.store.set_setting("work-state:" + service, {})
                self.store.set_setting("work-config:" + service, {})
            self.reload_catalog()

    async def sync(self, service):
        self.spec(service)
        if service not in ("linear", "google_calendar") or not self.config(service).get("destination"):
            raise ValueError("Choose a calendar or Linear team before syncing")
        async with self.sync_lock:
            try:
                from .work_sync import sync_source
                result = await sync_source(self, service)
                self.store.set_setting("work-sync:" + service, {**result, "last_success": time.time(), "last_attempt": time.time(), "error": ""})
            except Exception as exc:
                self.store.set_setting("work-sync:" + service, {**self.store.setting("work-sync:" + service, {}), "last_attempt": time.time(),
                    "error": str(exc) if isinstance(exc, ValueError) else "Source sync failed. Check account access and try again"})
                raise ValueError(self.store.setting("work-sync:" + service)["error"]) from None
            return self.store.setting("work-sync:" + service)

    async def sync_due(self):
        for service in ("linear", "google_calendar"):
            if self.config(service).get("sync_enabled") and time.time() - self.store.setting("work-sync:" + service, {}).get("last_attempt", 0) >= 900:
                try:
                    await self.sync(service)
                except ValueError:
                    pass  # Persisted status is visible; keep other scheduler work running.


def mount_work_accounts(app, accounts, owner):
    @app.get("/api/work-accounts")
    async def status(user=Depends(owner)):
        return accounts.status()

    @app.post("/api/work-accounts/{service}/connect")
    async def connect(service: str, user=Depends(owner)):
        return accounts.begin(service)

    @app.get("/api/work-accounts/{service}/callback")
    async def callback(service: str, code: str = "", state: str = ""):
        await accounts.complete(service, code, state)
        return HTMLResponse('<!doctype html><title>Account connected</title><h1>Account connected</h1><p>Return to Tools and choose a destination. No message, issue, or event was created.</p><a href="/">Open Muse</a>')

    @app.get("/api/work-accounts/{service}/destinations")
    async def destinations(service: str, user=Depends(owner)):
        return await accounts.destinations(service)

    @app.put("/api/work-accounts/{service}/destination")
    async def configure(service: str, data: Destination, user=Depends(owner)):
        return await accounts.configure(service, data)

    @app.post("/api/work-accounts/{service}/check")
    async def check(service: str, user=Depends(owner)):
        return await accounts.check(service)

    @app.post("/api/work-accounts/{service}/sync")
    async def sync(service: str, user=Depends(owner)):
        return await accounts.sync(service)

    @app.post("/api/work-accounts/{service}/disconnect")
    async def disconnect(service: str, user=Depends(owner)):
        await accounts.disconnect(service)
        return {"ok": True, "note": "Access removed from Muse. Revoke the application in provider settings to revoke its grant there."}

"""Read-only Grep integration. Credentials never enter prompts, URLs, or device frames."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL

import httpx

ORIGIN = "https://grep.live"
CALLBACK = "http://127.0.0.1:8766/grep/callback"
REPORTS = {
    "grid": "organization workforce onboarding knowledge",
    "travel": "trips bookings expenses requests events",
    "support": "overview trend tickets issues forecast",
    "revenue": "pipeline deals bookings forecast arr actions",
    "hiring": "roles pipeline",
    "assets": "library overview approvals requests",
}
REPORT_IDS = {f"{app}.{name}" for app, names in REPORTS.items() for name in names.split()}


class GrepError(ValueError):
    pass


def save_auth(path: Path, data: dict):
    """Atomic private credential write; no token is stored in the conversation DB."""
    if data.get("integration") != "muse" or not re.fullmatch(r"[a-f0-9]{64}", data.get("token", "")):
        raise GrepError("Grep did not issue a restricted Muse session. Update the Grep server first.")
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(data, handle)
    os.replace(temp, path)


class Grep:
    def __init__(self, settings, store, *, transport=None):
        self.settings, self.store, self.transport = settings, store, transport
        self.auth_path = settings.data_dir / "grep-auth.json"

    def auth(self):
        try:
            data = json.loads(self.auth_path.read_text())
            if data.get("integration") != "muse" or not re.fullmatch(r"[a-f0-9]{64}", data.get("token", "")):
                return None
            return data if float(data.get("expires_at", 0)) > time.time() else None
        except (OSError, ValueError, TypeError):
            return None

    def status(self):
        data = self.auth()
        return {"configured": bool(self.settings.grep_external_token), "authorized": bool(data),
                "expires_at": data["expires_at"] if data else None,
                "capabilities": ["search", "sources", "connections", "reports", "replit_read"],
                "note": "Employee login required: run muse-companion grep-connect." if not data else "Read access; server checks employee permissions on every request."}

    async def request(self, method, path, payload=None, *, params=None):
        # Callers provide only constant paths. Never follow a deployment/login redirect.
        allowed = {("GET", "/api/grep/coverage"), ("GET", "/api/company-apps"),
                   ("GET", "/api/auth/user"), ("POST", "/api/chats"),
                   ("POST", "/api/grep/search"), ("POST", "/api/company-apps/report"),
                   ("GET", "/api/grep/replit/apps"), ("POST", "/api/grep/replit/inspect"),
                   ("GET", "/api/grep/replit/connection"), ("POST", "/api/logout")}
        allowed |= {("GET", "/api/connections/" + x) for x in ("google-drive", "google-drive/personal", "notion", "linear", "slack")}
        if (method, path) not in allowed:
            raise GrepError("Unsupported Grep operation")
        auth = self.auth()
        if not self.settings.grep_external_token:
            raise GrepError("GREP_EXTERNAL_ACCESS_TOKEN is missing from the companion environment.")
        if not auth:
            raise GrepError("Grep employee session is missing or expired. Run muse-companion grep-connect on the companion host.")
        headers = {"Authorization": "Bearer " + self.settings.grep_external_token,
                   "x-grep-extension-session": auth["token"], "Accept": "application/json"}
        try:
            async with asyncio.timeout(190), httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10), follow_redirects=False, transport=self.transport) as client:
                async with client.stream(method, ORIGIN + path, headers=headers, json=payload, params=params) as response:
                    if 300 <= response.status_code < 400:
                        raise GrepError("Grep deployment access was rejected. Check the external access token.")
                    messages = {401: "Grep employee authorization expired or was revoked. Reconnect Grep.",
                                403: "Grep denied access for this employee or operation.",
                                429: "Grep search allowance reached. Wait before trying again.",
                                400: "Grep rejected the query or filters. Check source names, report filters, and model access."}
                    if response.status_code >= 400:
                        raise GrepError(messages.get(response.status_code, "Grep is unavailable for this request. Try again later."))
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 2_000_000:
                            raise GrepError("Grep response exceeded the safe size limit. Narrow the query.")
                    result = json.loads(content)
                    if not isinstance(result, dict):
                        raise GrepError("Grep returned an unexpected response.")
                    return result
        except (httpx.TimeoutException, TimeoutError):
            raise GrepError("Grep timed out. The request was not automatically retried; check Grep before retrying.") from None
        except (httpx.HTTPError, json.JSONDecodeError):
            raise GrepError("Grep could not be reached or returned an invalid response.") from None

    async def sources(self):
        coverage, catalog = await asyncio.gather(self.request("GET", "/api/grep/coverage"), self.request("GET", "/api/company-apps"))
        return {"coverage": coverage, "company_apps": catalog, "report_tools": sorted(REPORT_IDS)}

    async def connections(self):
        paths = {x: "/api/connections/" + x for x in ("google-drive", "google-drive/personal", "notion", "linear", "slack")}
        paths["replit"] = "/api/grep/replit/connection"
        async def get(name, path):
            try:
                value = await self.request("GET", path)
                # Connection status only; future upstream fields cannot leak tokens.
                return name, {k: v for k, v in value.items() if k in {"connected", "configured", "available", "status", "account", "email", "message", "reason", "scopes"}}
            except GrepError as exc:
                return name, {"available": False, "error": str(exc)}
        return {"connections": dict(await asyncio.gather(*(get(k, v) for k, v in paths.items())))}

    async def search(self, query, sources, conversation, operation_id, *, evidence_only=False):
        if not 1 <= len(query.strip()) <= 500:
            raise GrepError("Use a Grep query between 1 and 500 characters.")
        selected = [s.strip() for s in sources.split(",") if s.strip()]
        if len(selected) > 16 or any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", s) for s in selected):
            raise GrepError("Unknown source. Use grep_sources to inspect availability; leave sources empty to search all.")
        if conversation not in {"new", "continue"}:
            raise GrepError("conversation must be new or continue")
        # Persist the chat chosen for this operation before search, so a safe manual
        # retry reuses its UUID and Grep can replay a completed turn.
        key = "grep_operation:" + operation_id
        chat = self.store.setting(key)
        if not chat:
            chat = self.store.setting("grep_chat_id") if conversation == "continue" else None
            if not chat:
                created = await self.request("POST", "/api/chats", {"title": "Muse: " + query[:70]})
                chat = str(UUID(created["id"]))
            self.store.set_setting(key, chat)
            self.store.set_setting("grep_chat_id", chat)
        result = await self.request("POST", "/api/grep/search", {
            "chatId": chat, "turnId": str(uuid5(NAMESPACE_URL, "muse:" + operation_id)),
            "query": query, "sources": selected, "model": self.settings.grep_model,
            "reasoningEffort": "medium", "timezone": self.settings.timezone, "limit": 8,
            "mode": "sources_only" if evidence_only else "standard", "alsoCreatePodcast": False,
        })
        passages = [{k: item.get(k) for k in ("id", "source", "title", "excerpt", "url", "author", "updatedAt")}
                    for item in result.get("results", [])[:8]]
        citations = [{"title": p.get("title") or p.get("source"), "url": p["url"]} for p in passages
                     if isinstance(p.get("url"), str) and p["url"].startswith("https://")]
        for insight in result.get("appInsights") or []:
            if isinstance(insight.get("sourceUrl"), str) and insight["sourceUrl"].startswith("https://"):
                citations.append({"title": insight.get("title") or insight.get("app", "Company report"), "url": insight["sourceUrl"]})
        citations.append({"title": "Open this Grep conversation", "url": ORIGIN + "/c/" + chat})
        return {"answer": result.get("answer", "")[:18000], "results": passages, "citations": citations,
                "coverage": result.get("coverage"), "outcome": result.get("outcome"),
                "failure": result.get("failure"), "appInsights": result.get("appInsights"),
                "chat_url": ORIGIN + "/c/" + chat}

    async def report(self, tool, filters_json):
        if tool not in REPORT_IDS:
            raise GrepError("Unknown report. Call grep_sources to list report_tools.")
        filters = json.loads(filters_json)
        if not isinstance(filters, dict) or set(filters) - {"from", "to", "search", "status", "groupBy", "limit", "ownOnly"}:
            raise GrepError("Invalid report filters")
        body = {"tool": tool, "from": None, "to": None, "search": None, "status": None, "groupBy": None, "limit": 10} | filters
        result = await self.request("POST", "/api/company-apps/report", body)
        url = result.get("sourceUrl")
        result["citations"] = [{"title": result.get("title") or tool, "url": url}] if isinstance(url, str) and url.startswith("https://") else []
        return result

    async def disconnect(self):
        if self.auth():
            await self.request("POST", "/api/logout", {})
        self.auth_path.unlink(missing_ok=True)
        self.store.set_setting("grep_chat_id", None)
        return {"ok": True}

    async def tool(self, name, args, operation_id):
        try:
            if name == "grep_sources": return await self.sources()
            if name == "grep_connections": return await self.connections()
            if name in {"grep_ask", "grep_search"}:
                return await self.search(**args, operation_id=operation_id, evidence_only=name == "grep_search")
            if name == "grep_report": return await self.report(**args)
            if name == "grep_replit_apps": return await self.request("GET", "/api/grep/replit/apps", params={"query": args["query"]})
            if name == "grep_inspect_replit":
                return await self.request("POST", "/api/grep/replit/inspect", {"replId": args["repl_id"], "question": args["question"]})
            raise GrepError("Unknown Grep tool")
        except GrepError as exc:
            return {"error": str(exc), "available": False}

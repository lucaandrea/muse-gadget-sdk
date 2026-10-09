"""Fixed work-service APIs. An approval freezes the destination and full body."""
import os
import uuid
from datetime import datetime
from urllib.parse import quote

import httpx


FIELDS = {
    "slack": {"text"},
    "linear": {"title", "description"},
    "google_calendar": {"title", "description", "start", "end"},
}


def validate(command, arguments):
    kind = command["kind"]
    if set(arguments) != FIELDS[kind] or any(not isinstance(v, str) or len(v) > 24000 for v in arguments.values()):
        raise ValueError("Provide all of this work action's fields as bounded text")
    if command.get("read_only"):
        raise ValueError("Work write adapters cannot be configured read-only")
    destination = {"slack": "channel", "linear": "team_id", "google_calendar": "calendar_id"}[kind]
    if not isinstance(command.get(destination), str) or not command[destination].strip():
        raise ValueError("Configure the work action's fixed destination first")
    if kind == "google_calendar":
        start, end = datetime.fromisoformat(arguments["start"]), datetime.fromisoformat(arguments["end"])
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("Calendar times need offsets and an end after the start")
    if not arguments.get("title", arguments.get("text", "")).strip():
        raise ValueError("An action needs a title or message")


async def execute(command, arguments, operation, credentials, *, transport=None):
    validate(command, arguments)
    name = command.get("token_env")
    token = credentials.get(name) or os.environ.get(name or "")
    if not token:
        raise ValueError("Connect this work account on the backend first")
    kind = command["kind"]
    headers = {"Authorization": ("" if kind == "linear" and command.get("auth_type") == "api_key" else "Bearer ") + token}
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
        if kind == "slack":
            response = await client.post("https://slack.com/api/chat.postMessage", headers=headers,
                json={"channel": command["channel"], "text": arguments["text"], "unfurl_links": False, "unfurl_media": False,
                      "client_msg_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "muse:" + operation))})
        elif kind == "linear":
            response = await client.post("https://api.linear.app/graphql", headers=headers, json={
                "query": "mutation MuseIssue($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { id identifier url } } }",
                "variables": {"input": {"teamId": command["team_id"], "title": arguments["title"], "description": arguments["description"]}}})
        else:
            identity = uuid.uuid5(uuid.NAMESPACE_URL, "muse:" + operation).hex
            response = await client.post("https://www.googleapis.com/calendar/v3/calendars/" + quote(command["calendar_id"], safe="") + "/events",
                headers=headers, params={"sendUpdates": "none"}, json={"id": identity, "summary": arguments["title"], "description": arguments["description"],
                    "start": {"dateTime": arguments["start"]}, "end": {"dateTime": arguments["end"]}})
        if response.is_error or response.is_redirect:
            raise RuntimeError(f"{kind} returned HTTP {response.status_code}; verify external state before retrying")
        data = response.json()
        if kind == "slack":
            if not data.get("ok") or not data.get("ts"):
                raise RuntimeError("Slack did not confirm the message; inspect the destination before retrying")
            return {"ok": True, "service": kind, "channel": data["channel"], "message_id": data["ts"], "operation_id": operation}
        if kind == "linear":
            item = (data.get("data") or {}).get("issueCreate") or {}
            if data.get("errors") or not item.get("success") or not item.get("issue", {}).get("id"):
                raise RuntimeError("Linear did not confirm issue creation; inspect the team before retrying")
            return {"ok": True, "service": kind, "issue": item["issue"], "operation_id": operation}
        if not data.get("id"):
            raise RuntimeError("Calendar did not return an event receipt")
        return {"ok": True, "service": kind, "event_id": data["id"], "url": data.get("htmlLink", ""), "operation_id": operation,
                "note": "Event created in the configured calendar. No guests were added."}

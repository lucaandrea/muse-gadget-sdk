"""Import official work data without interpreting source content as commands."""
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .studio import RecordIn


def source_key(service, account, destination, identity):
    digest = hashlib.sha256((account + "\0" + destination + "\0" + identity).encode()).hexdigest()
    return service + ":" + digest


def previous(accounts, kind, source_id):
    row = accounts.store.one("SELECT id FROM studio_records WHERE kind=? AND source_id=?", (kind, source_id))
    return accounts.studio.get(row["id"]) if row else None


def upsert(accounts, value):
    data = RecordIn.model_validate(value)
    old = previous(accounts, data.kind, data.source_id)
    # Local links remain editable; the remote service owns its imported facts.
    if old:
        data.project_id, data.person_id = old["project_id"], old["person_id"]
        if data.model_dump(mode="json") == {key: old[key] for key in RecordIn.model_fields}:
            return old
    return accounts.studio.save(data, old["id"] if old else "", old["revision"] if old else 0)


def aware(value, default_zone):
    if "dateTime" in value:
        result = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=ZoneInfo(value.get("timeZone", default_zone)))
    return datetime.fromisoformat(value["date"]).replace(tzinfo=ZoneInfo(default_zone))


async def sync_source(accounts, service):
    config, state = accounts.config(service), accounts.state(service)
    headers = {"Authorization": "Bearer " + await accounts.token(service)}
    destination, account = config["destination"], state["account_id"]
    now = datetime.now(timezone.utc)
    pending, cursor, truncated = [], "", False
    start, end = now - timedelta(days=1), now + timedelta(days=14)
    for _ in range(5):
        if service == "google_calendar":
            params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "showDeleted": "true", "maxResults": 100}
            if cursor:
                params["pageToken"] = cursor
            value = await accounts.fetch("GET", "https://www.googleapis.com/calendar/v3/calendars/" + quote(destination, safe="") + "/events", headers=headers, params=params)
            pending.extend(value.get("items", []))
            calendar_zone = value.get("timeZone", accounts.settings.timezone)
            cursor = value.get("nextPageToken", "")
        else:
            value = await accounts.fetch("POST", "https://api.linear.app/graphql", headers=headers, json={
                "query": "query MuseStudioSync($team: ID!, $after: String) { issues(first:100, after:$after, filter:{team:{id:{eq:$team}}}) { nodes { id identifier title description url updatedAt dueDate priority state { name type } assignee { name } project { id name description url updatedAt } labels(first:20) { nodes { name } } } pageInfo { hasNextPage endCursor } } }",
                "variables": {"team": destination, "after": cursor or None}})
            issues = value["data"]["issues"]
            pending.extend(issues["nodes"])
            cursor = issues["pageInfo"]["endCursor"] if issues["pageInfo"]["hasNextPage"] else ""
        if not cursor:
            break
    truncated = bool(cursor)
    imported = 0
    with accounts.store.transaction():
        if accounts.config(service) != config or accounts.state(service).get("revision") != state["revision"]:
            raise ValueError("Source account or destination changed during sync. Retry with the selected source")
        for item in pending:
            key = source_key(service, account, destination, item["id"])
            if service == "google_calendar":
                old = previous(accounts, "meeting", key)
                cancelled = item.get("status") == "cancelled"
                if cancelled and (not item.get("start") or not item.get("end")):
                    if old:
                        upsert(accounts, {**{k: old[k] for k in RecordIn.model_fields}, "observed_at": item.get("updated", now.isoformat()),
                            "details": {**old["details"], "state": "cancelled"}})
                        imported += 1
                    continue
                participants = []
                for attendee in item.get("attendees", [])[:30]:
                    if not attendee.get("email"):
                        continue
                    person = upsert(accounts, {"kind": "person", "title": attendee.get("displayName", attendee["email"])[:200],
                        "source_id": source_key(service, account, "person", attendee["email"].lower()),
                        "observed_at": item.get("updated", now.isoformat()), "details": {"role": "Calendar participant"}})
                    participants.append(person["id"])
                upsert(accounts, {"kind": "meeting", "title": item.get("summary", "Untitled calendar event")[:200],
                    "note": item.get("description", "")[:8000], "source_id": key, "source_url": item.get("htmlLink", ""),
                    "project_id": config.get("project_id", ""), "observed_at": item.get("updated", now.isoformat()),
                    "details": {"state": "cancelled" if cancelled else "scheduled", "start": aware(item["start"], calendar_zone),
                        "end": aware(item["end"], calendar_zone), "purpose": item.get("description", "")[:4000], "participants": participants}})
            else:
                project_id = config.get("project_id", "")
                project = item.get("project")
                if project and not project_id:
                    saved = upsert(accounts, {"kind": "project", "title": project["name"][:200], "note": (project.get("description") or "")[:8000],
                        "source_id": source_key(service, account, "project", project["id"]), "source_url": project.get("url", ""),
                        "observed_at": project["updatedAt"], "details": {"goal": (project.get("description") or "")[:4000]}})
                    project_id = saved["id"]
                state_type = item["state"]["type"]
                issue_state = "done" if state_type == "completed" else "cancelled" if state_type == "canceled" else "agreed"
                blocked = any(label["name"].lower() == "blocked" for label in item.get("labels", {}).get("nodes", []))
                due = datetime.fromisoformat(item["dueDate"]).replace(hour=17, tzinfo=ZoneInfo(accounts.settings.timezone)).isoformat() if item.get("dueDate") else None
                title = (item["identifier"] + " · " + item["title"])[:200]
                owner = (item.get("assignee") or {}).get("name", "")[:200]
                upsert(accounts, {"kind": "commitment", "title": title, "note": (item.get("description") or "")[:8000],
                    "project_id": project_id, "source_id": key, "source_url": item["url"], "observed_at": item["updatedAt"],
                    "details": {"state": "waiting" if blocked and issue_state == "agreed" else issue_state, "owner": owner, "due": due,
                        "next_step": "Review issue in Linear (" + item["state"]["name"][:100] + ")"}})
                prior_blocker = previous(accounts, "blocker", key)
                if blocked or prior_blocker:
                    upsert(accounts, {"kind": "blocker", "title": title, "project_id": project_id, "source_id": key,
                        "source_url": item["url"], "observed_at": item["updatedAt"], "details": {
                            "state": "open" if blocked and issue_state == "agreed" else "resolved", "owner": owner,
                            "severity": "important" if item.get("priority") in (1, 2) else "normal", "next_step": "Review the explicit blocked label and dependencies in Linear"}})
            imported += 1
        accounts.studio.reconcile_alerts()
    return {"imported": imported, "truncated": truncated, "source": service,
            "window": {"start": start.isoformat(), "end": end.isoformat()} if service == "google_calendar" else {"team": destination, "limit": 500},
            "note": "Calendar and issue updates retain source links and history. Linear due dates use 17:00 in the companion timezone. Imports can be stale; inspect last success."}

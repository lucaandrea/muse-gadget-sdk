import asyncio
import json
import time
from datetime import datetime, timezone

import pytest

from test_companion import client, core, answer
from muse_companion.studio import JobIn, RecordIn, Studio
from muse_companion.store import Conflict, Store


def record(kind="project", **changes):
    return RecordIn.model_validate({"kind": kind, "title": "Studio prototype", "observed_at": "2026-10-09T09:00:00-07:00", "details": {}, **changes})


def report(**changes):
    return {"summary": "Draft", "priorities": [], "sections": [], "missing": [], "clusters": [], "findings": [], **changes}


def test_records_preserve_sources_revisions_and_reject_stale_edits(core):
    _, store, _, assistant = core
    data = record(source_url="https://example.com/brief", source_id="brief-1")
    item = assistant.studio.save(data)
    assert item["source_url"] == data.source_url
    data.title = "Edited title"
    revised = assistant.studio.save(data, item["id"], 1)
    assert revised["revision"] == 2
    with pytest.raises(Conflict):
        assistant.studio.save(data, item["id"], 1)
    with pytest.raises(Conflict):
        assistant.studio.save(data)
    history = store.rows("SELECT * FROM studio_revisions ORDER BY revision")
    assert len(history) == 2 and "Studio prototype" in history[0]["body"]


@pytest.mark.asyncio
async def test_voice_cannot_promote_generated_commitment_to_agreed(core):
    _, _, _, assistant = core
    data = record("commitment", details={"owner": "Luca", "state": "agreed"})
    result = await assistant.tool("studio_save", {"record_json": data.model_dump_json()}, "voice", "save-commitment")
    assert result["record"]["details"]["state"] == "suggested"
    assert await assistant.tool("studio_save", {"record_json": data.model_dump_json()}, "voice", "save-commitment") == result


def test_record_api_requires_owner_and_retains_retry_receipt(client):
    c, store, _ = client
    body = record().model_dump(mode="json") | {"operation_id": "save-project-once"}
    first = c.post("/api/studio/records", json=body)
    assert first.status_code == 200
    assert c.post("/api/studio/records", json=body).json() == first.json()
    changed = {**body, "title": "Other", "operation_id": "update-project", "revision": 0}
    assert c.put("/api/studio/records/" + first.json()["id"], json=changed).status_code == 409
    device = store.create_device("Pocket")
    assert c.post("/api/studio/records", json=body, headers={"authorization": "Bearer " + device["token"]}).status_code == 403


def test_record_validation_blocks_bad_dates_unknown_links_and_html_urls(core):
    assistant = core[3]
    with pytest.raises(ValueError):
        record(source_url="javascript:alert(1)")
    with pytest.raises(ValueError):
        record("meeting", details={"start": "2026-10-09T09:00:00", "end": "2026-10-09T10:00:00"})
    with pytest.raises(ValueError):
        assistant.studio.save(record(project_id="unknown"))


def test_exceptions_dedupe_reimports_and_keep_snoozes_until_change(core):
    settings, store, _, assistant = core
    settings.quiet_start = settings.quiet_end = 0
    data = record("blocker", details={"state": "open", "severity": "important", "owner": "Luca", "next_step": "Review the failed test"})
    item = assistant.studio.save(data)
    for _ in range(3):
        assistant.studio.reconcile_alerts()
    assert len(store.rows("SELECT * FROM events WHERE type='notification'")) == 1
    store.execute("UPDATE studio_alerts SET state='dismissed'")
    assistant.studio.save(data, item["id"], 1)
    assistant.studio.reconcile_alerts()
    assert assistant.studio.alerts() == []
    data.details["next_step"] = "Ask the experiment owner"
    assistant.studio.save(data, item["id"], 2)
    assistant.studio.reconcile_alerts()
    assert len(assistant.studio.alerts()) == 1
    assert len(store.rows("SELECT * FROM events WHERE type='notification'")) == 2
    data.details["state"] = "resolved"
    assistant.studio.save(data, item["id"], 3)
    assistant.studio.reconcile_alerts()
    assert assistant.studio.alerts() == []


def test_suggested_commitment_does_not_become_overdue_exception(core):
    assistant = core[3]
    data = record("commitment", details={"due": "2020-01-01T00:00:00Z", "state": "suggested"})
    item = assistant.studio.save(data)
    assistant.studio.reconcile_alerts()
    assert not assistant.studio.alerts()
    data.details["state"] = "waiting"
    assistant.studio.save(data, item["id"], 1)
    assistant.studio.reconcile_alerts()
    assert len(assistant.studio.alerts()) == 1


@pytest.mark.asyncio
async def test_studio_jobs_finish_and_can_be_corrected(core):
    _, store, provider, assistant = core
    provider.responses = [answer(json.dumps(report(priorities=["Prepare the demo"], missing=["Calendar not imported"])))]
    task = assistant.studio.start(JobIn(workflow="morning"))
    await assistant.run_job(task)
    completed = store.one("SELECT * FROM tasks WHERE id=?", (task["id"],))
    assert completed["state"] == "completed"
    assert "Calendar not imported" in json.loads(completed["result"])["text"]
    assert assistant.control_work(task["id"], "steer", "Focus on my 1:1s")["revision"] == 2


@pytest.mark.asyncio
async def test_feedback_counts_independent_sources_and_preserves_exact_quotes(core):
    _, _, provider, assistant = core
    notes = [assistant.studio.save(record("feedback", source_url="https://example.com/feedback/" + group, details={"source_group": group, "quote": quote})) for group, quote in [("customer-a", "Hard to find"), ("customer-a", "Still lost"), ("customer-b", "Where is it?")]]
    ids = [n["id"] for n in notes]
    provider.responses = [answer(json.dumps(report(clusters=[{"theme": "Discoverability", "evidence_ids": ids, "experiment": "Test a labeled button", "success_criterion": "80% find it unprompted"}])))]
    result = await assistant.studio.run({"workflow": "feedback"})
    assert result["clusters"][0]["independent_sources"] == 2
    assert result["clusters"][0]["evidence"][0]["quote"] == "Hard to find"
    assert "80% find it unprompted" in result["text"]
    assert result["unclustered"] == []
    # Cluster-only evidence still needs its source links even when the model
    # provides no narrative sections.
    assert {c["url"] for c in result["citations"]} == {n["source_url"] for n in notes}


@pytest.mark.asyncio
async def test_hallucinated_evidence_fails_report(core):
    _, _, provider, assistant = core
    provider.responses = [answer(json.dumps(report(sections=[{"heading": "Decision", "text": "Ship", "evidence_ids": ["invented"]}])))]
    with pytest.raises(ValueError, match="unknown Studio"):
        await assistant.studio.run({"workflow": "morning"})


def test_evaluation_uses_only_matched_measured_cases(core):
    assistant = core[3]
    for variant, case, latency, success, cost in [("baseline", "a", 100, True, .01), ("candidate", "a", 60, False, None), ("baseline", "unmatched", 5000, False, 2)]:
        assistant.studio.save(record("measurement", title=case, source_url=f"https://example.com/eval/{variant}/{case}", details={"variant": variant, "suite": "v1", "case": case, "latency_ms": latency, "success": success, "cost_usd": cost}))
    result = Studio.evaluate(assistant.studio.records(), "baseline", "candidate")
    assert result["matched_cases"] == 1
    assert result["metrics"]["latency_ms"]["delta"] == -40
    assert result["metrics"]["success"]["delta"] == -1
    assert "cost_usd" not in result["metrics"]
    assert any("cost_usd: missing" in m for m in result["missing"])
    assert len(result["evidence_ids"]) == 2
    assert {c["url"] for c in result["citations"]} == {"https://example.com/eval/baseline/a", "https://example.com/eval/candidate/a"}


@pytest.mark.asyncio
async def test_evaluation_calls_no_model(core):
    provider, assistant = core[2:]
    result = await assistant.studio.run({"workflow": "evaluation", "baseline": "a", "candidate": "b"})
    assert result["matched_cases"] == 0 and not provider.calls


@pytest.mark.asyncio
async def test_demo_only_runs_fixed_read_only_checks(core):
    _, _, provider, assistant = core
    assistant.integrations.commands = {"health": {"read_only": True, "fields": [], "kind": "http"}, "publish": {"kind": "http"}}
    calls = []
    async def execute(name, args, op):
        calls.append(name)
        return {"status": 200}
    assistant.integrations.execute = execute
    assistant.studio.save(record(details={"checks": ["health", "publish", "unknown"]}))
    provider.responses = [answer(json.dumps(report()))]
    result = await assistant.studio.run({"workflow": "demo"})
    assert calls == ["health"]
    assert [c["status"] for c in result["checks"]] == ["passed", "not_configured", "not_configured"]


@pytest.mark.asyncio
async def test_research_requires_citations_and_dedupes_rephrasing(core):
    _, _, provider, assistant = core
    finding = {"title": "Change", "url": "https://openai.com/news/change", "published": "2026-01-01", "change": "New behavior", "experiment": "Run our benchmark"}
    def response(item):
        value = answer(json.dumps(report(findings=[item])))
        value["output"][0]["content"][0]["annotations"] = [{"type": "url_citation", "url": item["url"], "title": item["title"]}]
        return value
    provider.responses = [response(finding)]
    result = await assistant.studio.run({"workflow": "research"})
    assert len(result["findings"]) == 1
    assistant.studio.committed_result(result)
    provider.responses = [response({**finding, "change": "Same news differently worded"})]
    assert (await assistant.studio.run({"workflow": "research"}))["findings"] == []
    provider.responses = [answer(json.dumps(report(findings=[finding])))]
    with pytest.raises(ValueError, match="citation"):
        await assistant.studio.run({"workflow": "research"})


def test_scheduled_work_is_durable_and_once_per_local_day(core):
    settings, store, _, assistant = core
    from zoneinfo import ZoneInfo
    hour = datetime.now(ZoneInfo(settings.timezone)).hour
    store.set_setting("studio_schedule:closeout", {"workflow": "closeout", "hour": hour, "weekdays_only": False})
    assistant.studio.scheduled()
    assistant.studio.scheduled()
    assert len(store.rows("SELECT * FROM tasks WHERE kind='studio'")) == 1
    store.execute("UPDATE tasks SET state='running'")
    store.recover()
    assert store.one("SELECT state FROM tasks")["state"] == "queued"


def test_owner_can_edit_completed_summary_without_sending_anything(client):
    c, store, _ = client
    task = store.task("studio", "Closeout", {"workflow": "closeout"}, "completed")
    store.update_task(task["id"], "completed", json.dumps({"text": "Original", "draft": True}))
    url = "/api/studio/drafts/" + task["id"]
    assert c.put(url, json={"text": "Reviewed by me", "revision": 1}).status_code == 200
    assert c.put(url, json={"text": "Stale edit", "revision": 1}).status_code == 409
    assert "Reviewed by me" in store.one("SELECT result FROM tasks")["result"]
    assert len(store.rows("SELECT * FROM tasks")) == 1

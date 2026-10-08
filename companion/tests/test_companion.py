import asyncio
import json
import struct
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from muse_companion.app import create_app
from muse_companion.assistant import Assistant
from muse_companion.config import Settings, read_env
from muse_companion.integrations import Integrations
from muse_companion.openai_api import OpenAI, ProviderError, resample_24_to_16, wav_bytes
from muse_companion.store import Conflict, Store


def answer(text="Saved and ready"):
    return {"output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]}


class FakeProvider:
    text = staticmethod(OpenAI.text)
    citations = staticmethod(OpenAI.citations)

    def __init__(self):
        self.responses = []
        self.calls = []
        self.transcriptions = 0
        self.fail_transcription = False

    async def respond(self, inputs, **kwargs):
        self.calls.append((inputs, kwargs))
        result = self.responses.pop(0) if self.responses else answer()
        if isinstance(result, Exception):
            raise result
        return result

    async def transcribe(self, pcm, *args):
        self.transcriptions += 1
        if self.fail_transcription:
            raise ProviderError("unavailable")
        return "Remember the charger is in the blue suitcase"

    async def speak(self, text):
        return bytes(640)

    async def close(self):
        pass


@pytest.fixture
def core(tmp_path):
    settings = Settings(data_dir=tmp_path)
    store = Store(tmp_path / "test.sqlite3")
    provider = FakeProvider()
    assistant = Assistant(settings, store, provider, Integrations(None))
    yield settings, store, provider, assistant
    store.close()


@pytest.fixture
def client(tmp_path):
    provider = FakeProvider()
    app = create_app(Settings(data_dir=tmp_path), provider)
    with TestClient(app) as client:
        client.headers["authorization"] = "Bearer " + (tmp_path / "owner-token").read_text()
        yield client, app.state.store, provider


def test_env_is_data(tmp_path):
    path = tmp_path / ".env"
    path.write_text('OPENAI_API_KEY="literal-$HOME-$(echo NO)-`echo NO`"\n# ignored\nexport MUSE_TIMEZONE="America/Los_Angeles"\n')
    values = read_env(path)
    assert values["OPENAI_API_KEY"] == "literal-$HOME-$(echo NO)-`echo NO`"


def test_auth_revocation_and_no_plaintext_token(core):
    _, store, _, _ = core
    device = store.create_device("pocket")
    assert store.authenticate(device["token"])["id"] == device["id"]
    assert device["token"] not in json.dumps(store.rows("SELECT * FROM devices"))
    store.execute("UPDATE devices SET revoked=1 WHERE id=?", (device["id"],))
    assert store.authenticate(device["token"]) is None


def test_operation_replay_and_collision(core):
    _, store, _, _ = core
    assert store.begin_operation("once", {"a": 1}) is None
    with pytest.raises(Conflict):
        store.begin_operation("once", {"a": 1})
    store.finish_operation("once", {"done": True})
    assert store.begin_operation("once", {"a": 1}) == {"done": True}
    with pytest.raises(Conflict):
        store.begin_operation("once", {"a": 2})


@pytest.mark.asyncio
async def test_memory_tool_retry_does_not_duplicate(core):
    _, store, _, assistant = core
    args = {"text": "Charger in blue suitcase", "id": ""}
    one = await assistant.tool("memory_save", args, "voice", "save-once")
    assert await assistant.tool("memory_save", args, "voice", "save-once") == one
    assert len(store.search_memories("charger")) == 1


def test_memory_corrections_and_deletion_clear_recall_caches(core):
    _, store, _, _ = core
    memory = store.save_memory("charger in blue suitcase", "voice")
    store.begin_operation("answer", {})
    store.finish_operation("answer", {"text": "blue suitcase"})
    store.remember_turn("Where?", "blue suitcase")
    store.save_memory("charger in green drawer", "voice", memory["id"])
    assert store.search_memories("blue") == []
    assert "blue" not in json.dumps(store.search_memories("charger"))
    assert store.history() == []
    assert store.begin_operation("answer", {})["forgotten"]
    store.forget_memory(memory["id"])
    assert store.search_memories("charger") == []


def test_due_reminders_persist_without_duplicate_events(tmp_path):
    path = tmp_path / "store.sqlite3"
    store = Store(path)
    r = store.reminder("prototype", time.time() - 1)
    assert len(store.due_reminders(time.time())) == 1
    assert store.due_reminders(time.time()) == []
    store.close()
    store = Store(path)
    store.recover()
    assert store.one("SELECT state FROM reminders WHERE id=?", (r["id"],))["state"] == "due"
    assert store.due_reminders(time.time()) == []
    assert len(store.rows("SELECT * FROM events WHERE type='reminder.due'")) == 1
    store.close()


def test_arrival_reminder_needs_matching_network(core):
    _, store, _, _ = core
    store.reminder("take charger", time.time() - 1, "Home")
    assert store.due_reminders(time.time(), "Office") == []
    assert len(store.due_reminders(time.time(), "Home")) == 1


@pytest.mark.asyncio
async def test_snooze_is_idempotent(core):
    _, store, _, assistant = core
    r = store.reminder("prototype", time.time() - 1)
    await assistant.action(r["id"], "snooze", "snooze-1", 10)
    due = store.one("SELECT due FROM reminders WHERE id=?", (r["id"],))["due"]
    await assistant.action(r["id"], "snooze", "snooze-1", 10)
    assert store.one("SELECT due FROM reminders WHERE id=?", (r["id"],))["due"] == due


@pytest.mark.asyncio
async def test_external_action_executes_exact_approved_payload_once(core):
    _, store, _, assistant = core
    assistant.integrations.commands["task"] = {"kind": "http", "url": "https://example.invalid/task", "fields": ["title"]}
    executed = []
    async def execute(name, arguments, operation):
        executed.append((name, arguments, operation))
        await asyncio.sleep(0)
        return {"ok": True}
    assistant.integrations.execute = execute
    proposed = await assistant.tool("integration_propose", {"name": "task", "arguments_json": '{"title":"prototype"}'}, "voice", "proposal")
    task = proposed["task"]
    assert executed == []
    outcomes = await asyncio.gather(assistant.action(task["id"], "approve", "approval-a"), assistant.action(task["id"], "approve", "approval-b"), return_exceptions=True)
    assert sum(isinstance(o, Conflict) for o in outcomes) == 1
    assert executed == [("task", {"title": "prototype"}, task["id"])]
    assert store.one("SELECT state FROM tasks WHERE id=?", (task["id"],))["state"] == "completed"


@pytest.mark.asyncio
async def test_external_failure_is_uncertain_not_completed(core):
    _, store, _, assistant = core
    task = store.task("integration", "send", {"name": "send", "arguments": {}}, "needs_approval")
    async def failed(*args):
        raise RuntimeError("connection lost after write")
    assistant.integrations.execute = failed
    with pytest.raises(RuntimeError):
        await assistant.action(task["id"], "approve", "approval-a")
    assert store.one("SELECT state FROM tasks WHERE id=?", (task["id"],))["state"] == "uncertain"
    with pytest.raises(Conflict):
        await assistant.action(task["id"], "approve", "approval-b")


def test_restart_requeues_analysis_but_not_external_actions(core):
    _, store, _, _ = core
    research = store.task("research", "compare", {}, "running")
    action = store.task("integration", "send", {}, "executing")
    store.recover()
    assert store.one("SELECT state FROM tasks WHERE id=?", (research["id"],))["state"] == "queued"
    assert store.one("SELECT state FROM tasks WHERE id=?", (action["id"],))["state"] == "uncertain"


@pytest.mark.asyncio
async def test_read_tool_cannot_bypass_approval(core):
    _, _, _, assistant = core
    assistant.integrations.commands["send"] = {"kind": "http", "url": "https://example.invalid", "fields": []}
    with pytest.raises(ValueError, match="approval"):
        await assistant.tool("integration_read", {"name": "send", "arguments_json": "{}"}, "voice", "read-attempt")


def test_budget_reservation_is_atomic(core):
    _, store, _, _ = core
    store.reserve_usage("today", "live_seconds", 300, 300)
    with pytest.raises(ValueError):
        store.reserve_usage("today", "live_seconds", 1, 300)
    assert store.one("SELECT amount FROM usage")["amount"] == 300


@pytest.mark.asyncio
async def test_chat_uses_tools_history_and_operation_receipt(core):
    _, store, provider, assistant = core
    provider.responses = [{"output": [{"type": "function_call", "name": "memory_save", "arguments": '{"text":"charger in suitcase","id":""}', "call_id": "tool-1"}]}, answer("Remembered")]
    first = await assistant.chat("Remember the charger", "chat-1")
    assert len(store.search_memories("charger")) == 1
    assert await assistant.chat("Remember the charger", "chat-1") == first
    assert len(provider.calls) == 2
    await assistant.chat("Where was that?", "chat-2")
    assert any(i.get("content") == "Remembered" for i in provider.calls[-1][0])


def test_http_auth_csrf_and_device_owner_boundary(client):
    c, store, _ = client
    assert c.get("/api/state").status_code == 200
    assert c.post("/api/memories", json={"text": "bad"}, headers={"origin": "https://evil.invalid"}).status_code == 403
    device = store.create_device("device")
    assert c.post("/api/devices", json={"name": "no"}, headers={"authorization": "Bearer " + device["token"]}).status_code == 403
    assert c.get("/api/state", headers={"authorization": "Bearer invalid"}).status_code == 401


def test_reminder_rejects_timezone_ambiguity(client):
    c, _, _ = client
    assert c.post("/api/reminders", json={"title": "test", "due_at": "2026-10-08T09:00:00"}).status_code == 422
    assert c.post("/api/reminders", json={"title": "test", "due_at": "2026-10-08T09:00:00-07:00"}).status_code == 200


def test_calendar_is_draft_until_approved_and_escapes_text(client):
    c, store, _ = client
    task = store.task("calendar", "event", {"title": "Planning\nBEGIN:VTODO", "start": "2026-10-09T09:00:00-07:00", "end": "2026-10-09T09:30:00-07:00", "description": "a,b;c"}, "needs_approval")
    path = f"/api/tasks/{task['id']}/calendar.ics"
    assert c.get(path).status_code == 404
    assert c.post(f"/api/items/{task['id']}/action", json={"action": "approve", "operation_id": "calendar-approval"}).status_code == 200
    result = c.get(path).text
    assert "DTSTART:20261009T160000Z" in result
    assert "Planning\\nBEGIN:VTODO" in result
    assert "a\\,b\\;c" in result


def test_websocket_requires_authorization(client):
    c, _, _ = client
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/v1/device", headers={"authorization": "Bearer bad"}):
            pass


def test_recording_ack_is_durable_even_when_transcription_fails(client):
    c, store, provider = client
    provider.fail_transcription = True
    with c.websocket_connect("/v1/device") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "voice.begin", "id": "recording-test-1", "generation": 7})
        while ws.receive_json()["type"] != "voice.ready":
            pass
        ws.send_bytes(struct.pack("<I", 7) + bytes(640))
        ws.send_json({"type": "voice.end"})
        while True:
            event = ws.receive_json()
            if event["type"] == "voice.received":
                break
        assert store.one("SELECT length(pcm) AS length FROM captures")["length"] == 640
        while ws.receive_json()["type"] != "voice.done":
            pass
    assert store.one("SELECT state FROM captures")["state"] == "failed"


@pytest.mark.asyncio
async def test_capture_replay_never_transcribes_twice_and_discards_pcm_after_success(core):
    _, store, provider, assistant = core
    store.capture("note", bytes(640))
    await assistant.process_capture("note")
    store.capture("note", bytes(640))
    await assistant.process_capture("note")
    assert provider.transcriptions == 1
    assert store.one("SELECT length(pcm) AS size FROM captures")["size"] == 0
    with pytest.raises(Conflict):
        store.capture("note", bytes(642))


def test_audio_formats_are_explicit():
    import wave
    import io
    pcm = struct.pack("<hhhhhh", 0, 100, 200, 300, 400, 500)
    assert struct.unpack("<hhhh", resample_24_to_16(pcm)) == (0, 150, 300, 450)
    with wave.open(io.BytesIO(wav_bytes(pcm))) as f:
        assert (f.getframerate(), f.getnchannels(), f.getsampwidth()) == (16000, 1, 2)

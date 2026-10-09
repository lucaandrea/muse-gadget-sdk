import asyncio
import json
import time

import httpx
import pytest

from test_companion import client, core
from muse_companion.openai_api import OpenAI, ProviderError


@pytest.mark.asyncio
async def test_semantic_recall_finds_synonym_and_filters_project(core):
    _, store, provider, assistant = core
    memory = store.save_memory("Charger is in the suitcase", "voice", project_id="travel")
    store.save_memory("Demo next Tuesday", "meeting", project_id="studio")
    async def embed(texts):
        return [[1, 0] for _ in texts]
    provider.embed = embed
    result = await assistant.memory.search("power adapter", project_id="travel")
    assert result["mode"] == "hybrid" and result["memories"][0]["id"] == memory["id"]
    assert len(result["memories"]) == 1 and result["memories"][0]["source"] == "voice"


@pytest.mark.asyncio
async def test_deletion_during_embedding_cannot_resurrect_memory(core):
    _, store, provider, assistant = core
    memory = store.save_memory("Remember this secret", "test")
    async def embed(texts):
        store.forget_memory(memory["id"])
        return [[1, 0] for _ in texts]
    provider.embed = embed
    result = await assistant.memory.search("secret")
    assert result["memories"] == []
    assert store.rows("SELECT * FROM memory_context") == []


@pytest.mark.asyncio
async def test_correction_during_embedding_cannot_restore_old_vector(core):
    _, store, provider, assistant = core
    memory = store.save_memory("Charger is in the suitcase", "test")
    async def embed(texts):
        store.save_memory("Adapter is in the office", "test", memory["id"])
        return [[1, 0] for _ in texts]
    provider.embed = embed
    result = await assistant.memory.search("suitcase")
    assert result["memories"] == []
    assert store.one("SELECT digest FROM memory_context")["digest"] == ""


@pytest.mark.asyncio
async def test_memory_provider_failure_keeps_literal_recall(core):
    _, store, provider, assistant = core
    store.save_memory("Charger is in the suitcase", "test")
    async def embed(texts):
        raise ProviderError("unavailable")
    provider.embed = embed
    result = await assistant.memory.search("charger")
    assert result["mode"] == "literal" and len(result["memories"]) == 1
    assert "unavailable" in result["note"]


@pytest.mark.asyncio
async def test_embedding_response_order_is_validated(core):
    settings, store, _, _ = core
    provider = OpenAI(settings, store)
    async def request(*args, **kwargs):
        assert kwargs["json"]["dimensions"] == 256
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]})
    provider.request = request
    assert await provider.embed(["a", "b"]) == [[1, 0], [0, 1]]
    await provider.close()


def test_diagnostics_distinguishes_credentials_pairing_and_observation(client):
    c, store, _ = client
    device = store.create_device("Round Muse")
    with c.websocket_connect('/v1/device', headers={'authorization': 'Bearer ' + device['token']}) as ws:
        assert ws.receive_json()['type'] == 'hello'
        ws.send_json({"type": "device.health", "health": {"muse_paired": False, "wifi_connected": True, "battery_percent": 65}})
        ws.send_json({"type": "ping"})
        while ws.receive_json()["type"] != "pong":
            pass
        result = c.get('/api/diagnostics').json()
        assert result['devices'][0]['companion'] == 'connected'
        assert result['devices'][0]['muse_pairing'] == 'not_paired'
        assert result['openai']['access_verified'] is False
        ws.send_json({"type": "compatibility.check"})
        while True:
            item = ws.receive_json()
            if item["type"] == "compatibility.result":
                assert item["storage_ready"] and item["storage_schema"] == 5
                break
    assert c.get('/api/diagnostics').json()['devices'][0]['companion'] == 'disconnected'


def test_old_socket_closing_cannot_disconnect_replacement(core):
    assistant = core[3]
    diagnostics = assistant.diagnostics
    diagnostics.observe('test', 'old')
    diagnostics.observe('test', 'new')
    diagnostics.observe('test', 'old', connected=False)
    assert core[1].setting('device_health:test')['connected']


def test_health_endpoint_exposes_no_keys(client):
    c, store, _ = client
    device = store.create_device('Pocket')
    response = c.get('/api/diagnostics')
    assert device['token'] not in response.text and 'token_hash' not in response.text
    assert c.get('/api/diagnostics', headers={'authorization': 'Bearer ' + device['token']}).status_code == 403


def test_pocket_account_recovery_excludes_private_account_details(core):
    settings, store, _, assistant = core
    settings.public_url = "https://companion.example"
    settings.work_oauth = {name: {"client_id": "private-client", "client_secret": "private-secret"}
                           for name in ("slack", "linear", "google_calendar")}
    now = time.time()
    store.set_setting("work-state:slack", {"connected": True, "account_id": "private-user", "checked_at": now,
                                          "expires_at": now - 1, "error": "sensitive provider detail"})
    store.set_setting("work-state:linear", {"connected": True, "checked_at": now, "expires_at": now + 3600})
    store.set_setting("work-config:linear", {"destination": "private-team"})
    store.set_setting("work-state:google_calendar", {"connected": True, "checked_at": now - 1800})
    store.set_setting("work-config:google_calendar", {"destination": "private-calendar"})
    result = assistant.diagnostics.pocket_accounts()
    assert "Slack: check access in Tools" in result["summary"]
    assert "Linear: access checked" in result["summary"]
    assert "Calendar: linked; check access in Tools" in result["summary"]
    assert result["tools_url"] == "https://companion.example/#view=tools"
    assert len(result["summary"].encode()) < 257
    assert "private-" not in json.dumps(result) and "sensitive provider" not in json.dumps(result)


def test_device_sync_includes_safe_account_recovery(client):
    c, store, _ = client
    device = store.create_device("Pocket")
    with c.websocket_connect('/v1/device', headers={'authorization': 'Bearer ' + device['token']}) as ws:
        while True:
            event = ws.receive_json()
            if event["type"] == "sync":
                assert "Slack: setup needed in Tools" in event["accounts"]["summary"]
                assert device["token"] not in json.dumps(event["accounts"])
                break

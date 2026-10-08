import json
import time
from uuid import uuid4
import httpx
import pytest
from muse_companion.config import Settings
from muse_companion.grep import Grep, GrepError, save_auth
from muse_companion.store import Store

def test_replit_copied_token_is_normalized_without_a_url():
    from muse_companion.config import grep_access_token
    assert grep_access_token("?project-protection-bypass=secret%2Bvalue") == "secret+value"
    assert grep_access_token("raw-secret") == "raw-secret"
    with pytest.raises(ValueError): grep_access_token("invalid token")

@pytest.fixture
def grep(tmp_path):
    settings = Settings(data_dir=tmp_path, grep_external_token="deployment-secret")
    store = Store(tmp_path / "muse.sqlite3")
    client = Grep(settings, store)
    save_auth(client.auth_path, {"integration": "muse", "token": "a" * 64, "expires_at": time.time() + 3600})
    yield client
    store.close()

def test_credentials_are_private(grep):
    assert grep.auth_path.stat().st_mode & 0o777 == 0o600
    assert "deployment-secret" not in repr(grep.settings)
    assert "a" * 64 not in json.dumps(grep.status())
    with pytest.raises(GrepError): save_auth(grep.auth_path, {"token": "b" * 64, "integration": "extension"})

async def test_search_uses_both_headers_and_reuses_turn(grep):
    calls, chat = [], str(uuid4())
    def handle(request):
        assert request.url.host == "grep.live"
        assert request.headers["authorization"] == "Bearer deployment-secret"
        assert request.headers["x-grep-extension-session"] == "a" * 64
        body = json.loads(request.content); calls.append((request.url.path, body))
        if request.url.path == "/api/chats": return httpx.Response(201, json={"id": chat})
        assert body["sources"] == [] and body["alsoCreatePodcast"] is False
        return httpx.Response(200, json={"answer": "Supported answer", "outcome": "partial_evidence", "coverage": {"unavailable": ["google_drive"]}, "results": [{"id": "1", "source": "notion", "title": "Policy", "url": "https://notion.so/test", "excerpt": "Evidence"}]})
    grep.transport = httpx.MockTransport(handle)
    first = await grep.search("What is our policy?", "", "new", "op")
    await grep.search("What is our policy?", "", "new", "op")
    assert len(calls) == 3 and calls[1][1]["turnId"] == calls[2][1]["turnId"]
    assert first["outcome"] == "partial_evidence" and first["coverage"]["unavailable"]
    assert first["citations"][0]["url"] == "https://notion.so/test"
    assert "deployment-secret" not in json.dumps(first)

async def test_followup_reuses_chat_and_sources_only(grep):
    chat = str(uuid4()); grep.store.set_setting("grep_chat_id", chat)
    def handle(request):
        body = json.loads(request.content)
        assert body["chatId"] == chat and body["mode"] == "sources_only"
        return httpx.Response(200, json={"results": [], "outcome": "sources_only"})
    grep.transport = httpx.MockTransport(handle)
    await grep.search("And last week?", "slack", "continue", "followup", evidence_only=True)

@pytest.mark.parametrize("status", [307, 401, 403, 429, 500])
async def test_redirects_and_errors_never_follow_or_leak(grep, status):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://attacker.invalid/steal"}, text="upstream secret")
    grep.transport = httpx.MockTransport(handle)
    with pytest.raises(GrepError) as caught: await grep.request("GET", "/api/grep/coverage")
    assert len(calls) == 1 and "upstream secret" not in str(caught.value)

async def test_expired_and_forbidden_never_use_network(grep):
    grep.transport = httpx.MockTransport(lambda _: pytest.fail("network called"))
    with pytest.raises(GrepError): await grep.request("POST", "/api/extension/token")
    save_auth(grep.auth_path, {"integration": "muse", "token": "a" * 64, "expires_at": 0})
    with pytest.raises(GrepError): await grep.request("GET", "/api/grep/coverage")

async def test_reports_reject_arbitrary_operations(grep):
    with pytest.raises(GrepError): await grep.report("shell.execute", "{}")
    with pytest.raises(GrepError): await grep.report("grid.organization", '{"sql":"select *"}')

async def test_response_bound(grep):
    grep.transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 2_000_001))
    with pytest.raises(GrepError, match="size"): await grep.request("GET", "/api/grep/coverage")

async def test_assistant_routes_grep_and_keeps_citations(tmp_path):
    from test_companion import FakeProvider, answer
    from muse_companion.assistant import Assistant
    from muse_companion.integrations import Integrations
    store = Store(tmp_path / "routing.sqlite3")
    provider = FakeProvider()
    assistant = Assistant(Settings(data_dir=tmp_path), store, provider, Integrations(None))
    async def tool(name, args, operation_id):
        assert name == "grep_ask" and args["query"] == "Policy?"
        return {"answer": "Verified policy", "citations": [{"title": "Policy", "url": "https://grep.live/c/test"}]}
    assistant.grep.tool = tool
    provider.responses = [{"output": [{"type": "function_call", "name": "grep_ask", "call_id": "g1", "arguments": json.dumps({"query": "Policy?", "sources": "", "conversation": "new"})}]}, answer("Here is the policy.")]
    try:
        result = await assistant.chat("Company policy?", "routing")
        assert result["citations"][0]["title"] == "Policy"
        assert result["text"] == "Here is the policy."
        output = next(item for item in provider.calls[1][0] if item.get("type") == "function_call_output")
        assert json.loads(output["output"])["answer"] == "Verified policy"
    finally: store.close()

async def test_report_keeps_permission_status_and_source(grep):
    def handle(request):
        assert json.loads(request.content)["tool"] == "grid.knowledge"
        return httpx.Response(200, json={"status": "unavailable", "sourceUrl": "https://grid.example", "title": "Reviewed FAQs"})
    grep.transport = httpx.MockTransport(handle)
    result = await grep.report("grid.knowledge", "{}")
    assert result["status"] == "unavailable"
    assert result["citations"][0]["url"] == "https://grid.example"

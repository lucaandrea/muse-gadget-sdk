import asyncio
import json
import time

import pytest

from test_companion import client, core, answer
from muse_companion.openai_api import resample_16_to_24


def test_meeting_segments_retry_without_duplicate_transcription(client):
    c, store, provider = client
    meeting = c.post('/api/meetings', json={'title': 'Test meeting'}).json()
    path = f"/api/meetings/{meeting['id']}/segments/0?marked=true"
    first = c.put(path, content=bytes(640))
    assert first.status_code == 200
    assert c.put(path, content=bytes(640)).json() == first.json()
    assert provider.transcriptions == 1
    assert c.put(path, content=bytes(642)).status_code == 409
    assert c.post(f"/api/meetings/{meeting['id']}/finish").status_code == 200
    payload = json.loads(store.one('SELECT payload FROM tasks WHERE id=?', (meeting['id'],))['payload'])
    assert '[MARKED IMPORTANT]' in payload['transcript']


def test_meeting_missing_segment_is_visible(client):
    c, _, _ = client
    meeting = c.post('/api/meetings', json={}).json()
    assert c.put(f"/api/meetings/{meeting['id']}/segments/1", content=bytes(640)).status_code == 200
    assert c.post(f"/api/meetings/{meeting['id']}/finish").status_code == 409


def test_meeting_upload_is_bounded(client):
    c, _, _ = client
    meeting = c.post('/api/meetings', json={}).json()
    assert c.put(f"/api/meetings/{meeting['id']}/segments/0", content=bytes(960002)).status_code == 400
    assert c.put(f"/api/meetings/{meeting['id']}/segments/180", content=bytes(640)).status_code == 400


def test_capture_recovery_only_retries_unambiguous_stages(core):
    _, store, _, _ = core
    for identity, state in [('asr', 'transcribing'), ('tools', 'processing')]:
        store.capture(identity, bytes(640))
        store.execute('UPDATE captures SET state=? WHERE id=?', (state, identity))
    store.recover()
    assert store.one("SELECT state FROM captures WHERE id='asr'")['state'] == 'queued'
    assert store.one("SELECT state FROM captures WHERE id='tools'")['state'] == 'needs_review'


@pytest.mark.asyncio
async def test_queued_capture_finishes_after_backend_restart(core):
    _, store, _, assistant = core
    store.capture('offline-note', bytes(640))
    store.recover()
    await assistant.tick()
    await asyncio.gather(*assistant.background.values())
    assert store.one("SELECT state FROM captures WHERE id='offline-note'")['state'] == 'completed'


@pytest.mark.asyncio
async def test_cancelled_research_is_not_replaced_by_a_late_result(core):
    _, store, provider, assistant = core
    task = store.task('research', 'Test research', {})
    gate = asyncio.Event()
    async def response(*args, **kwargs):
        await gate.wait()
        return answer('late result')
    provider.respond = response
    running = asyncio.create_task(assistant.run_job(task))
    await asyncio.sleep(0)
    store.update_task(task['id'], 'cancelled')
    gate.set()
    await running
    assert store.one('SELECT state FROM tasks WHERE id=?', (task['id'],))['state'] == 'cancelled'


@pytest.mark.parametrize('body', ['x' * 2000, '界' * 600])
def test_native_approval_requires_full_details(core, body):
    from muse_companion.assistant import task_card
    _, store, _, _ = core
    task = store.task('integration', 'Long action', {'name': 'send', 'arguments': {'body': body}}, 'needs_approval')
    assert 'approve' not in [b.action for b in task_card(task).buttons]
    assert 'open' in [b.action for b in task_card(task).buttons]


def test_translation_resampler_preserves_duration():
    assert len(resample_16_to_24(bytes(32000))) == 48000


def test_document_index_can_progress_across_multiple_polls(client):
    import httpx
    c, store, provider = client
    store.set_setting('vector_store', 'vs_test')
    store.execute("INSERT INTO documents VALUES (?,?,?,'indexing',?)", ('doc', 'Guide.txt', 'file_test', time.time()))
    statuses = iter(['in_progress', 'completed'])
    async def request(method, path):
        assert method == 'GET' and path == 'vector_stores/vs_test/files/file_test'
        return httpx.Response(200, json={'status': next(statuses)})
    provider.request = request
    assert c.get('/api/documents/doc').json()['state'] == 'in_progress'
    assert c.get('/api/documents/doc').json()['state'] == 'completed'
    assert c.get('/api/documents/doc').json()['state'] == 'completed'

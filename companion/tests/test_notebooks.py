import json
import pytest
from test_companion import core, client
from muse_companion.store import Conflict


def context(position=0, local='a'*32, marked=False):
    return {'device_id':'device-one','mode':'notebook','notebook':local,'position':position,'marked':marked}


@pytest.mark.asyncio
async def test_notebook_waits_for_every_segment_and_does_not_execute_transcript(core):
    _,store,provider,assistant=core
    notebook=assistant.notebooks
    notebook.accept('capture-0',bytes(32000),context())
    notebook.accept('capture-1',bytes(16000),context(1))
    receipt=notebook.finish('device-one','a'*32,2,[1])
    assert receipt['state']=='recording'
    await assistant.process_capture('capture-1')
    assert store.one('SELECT state FROM tasks WHERE id=?',(receipt['task_id'],))['state']=='recording'
    await assistant.process_capture('capture-0')
    task=store.one('SELECT * FROM tasks WHERE id=?',(receipt['task_id'],))
    assert task['state']=='queued'
    transcript=json.loads(task['payload'])['transcript']
    assert transcript.index('Segment 1')<transcript.index('Segment 2 [MARKED IMPORTANT]')
    assert provider.calls==[] and store.rows('SELECT * FROM memories')==[]
    assert notebook.finish('device-one','a'*32,2,[1])['task_id']==receipt['task_id']


def test_segment_cannot_be_replaced_or_appended_after_finish(core):
    _,store,_,assistant=core
    notebook=assistant.notebooks
    notebook.accept('capture-0',bytes(16000),context())
    notebook.accept('capture-0',bytes(16000),context())
    with pytest.raises(Conflict):notebook.accept('replacement',bytes(16000),context())
    with pytest.raises(Conflict):notebook.accept('capture-0',b'X'*16000,context())
    notebook.finish('device-one','a'*32,1,[])
    with pytest.raises(Conflict):notebook.accept('capture-extra',bytes(16000),context(1))
    with pytest.raises(Conflict):notebook.finish('device-one','a'*32,2,[])
    assert len(store.rows('SELECT * FROM notebook_segments'))==1


@pytest.mark.asyncio
async def test_failed_transcription_retains_notebook_and_can_recover(core):
    _,store,provider,assistant=core
    assistant.notebooks.accept('capture-0',bytes(32000),context(marked=True))
    receipt=assistant.notebooks.finish('device-one','a'*32,1,[])
    provider.fail_transcription=True
    with pytest.raises(Exception):await assistant.process_capture('capture-0')
    assert store.one('SELECT state,pcm FROM captures WHERE id=?',('capture-0',))['pcm']
    provider.fail_transcription=False
    store.execute("UPDATE captures SET state='queued' WHERE id='capture-0'")
    await assistant.process_capture('capture-0')
    task=store.one('SELECT * FROM tasks WHERE id=?',(receipt['task_id'],))
    assert task['state']=='queued' and '[MARKED IMPORTANT]' in task['payload']


def test_devices_cannot_merge_notebooks_with_same_local_id(core):
    _,store,_,assistant=core
    assistant.notebooks.accept('first',bytes(16000),context())
    assistant.notebooks.accept('second',bytes(16000),{**context(),'device_id':'device-two'})
    assert len(store.rows('SELECT * FROM notebooks'))==2


def test_phone_cannot_finalize_partial_device_notebook(client):
    c,store,_=client
    notebook=c.app.state.assistant.notebooks
    notebook.accept('first',bytes(16000),context())
    task=store.rows('SELECT * FROM tasks')[0]
    response=c.post('/api/meetings/'+task['id']+'/finish')
    assert response.status_code==409 and 'device' in response.json()['detail']

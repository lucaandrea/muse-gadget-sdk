import time
import pytest
from test_companion import core, client


@pytest.mark.asyncio
async def test_snooze_outbox_keeps_original_time_and_replays_receipt(core):
    _,store,_,assistant=core
    reminder=store.reminder('Review brief',time.time()-1)
    chosen=time.time()+600
    first=await assistant.action(reminder['id'],'snooze','device:saved-once',10,chosen)
    replay=await assistant.action(reminder['id'],'snooze','device:saved-once',10,chosen)
    assert first==replay and first['due']==chosen
    row=store.one('SELECT * FROM reminders WHERE id=?',(reminder['id'],))
    assert row['due']==chosen and row['revision']==2


@pytest.mark.asyncio
async def test_local_action_failure_rolls_back_change_and_receipt(core,monkeypatch):
    _,store,_,assistant=core
    reminder=store.reminder('Review brief',time.time()+600)
    original=store.finish_operation
    def fail(*args):
        raise RuntimeError('disk stopped')
    monkeypatch.setattr(store,'finish_operation',fail)
    with pytest.raises(RuntimeError):
        await assistant.action(reminder['id'],'done','device:retry-after-crash')
    assert store.one('SELECT state FROM reminders WHERE id=?',(reminder['id'],))['state']=='scheduled'
    assert not store.one('SELECT * FROM operations WHERE id=?',('device:retry-after-crash',))
    monkeypatch.setattr(store,'finish_operation',original)
    assert (await assistant.action(reminder['id'],'done','device:retry-after-crash'))['ok']


def test_action_failure_is_correlated_and_connection_survives(client):
    c,store,_=client
    token=c.headers['authorization']
    with c.websocket_connect('/v1/device',headers={'authorization':token}) as ws:
        ws.send_json({'type':'action','id':'missing','action':'done','operation_id':'offline-missing-once'})
        while True:
            result=ws.receive_json()
            if result['type']=='action.error':break
        assert result['operation_id']=='offline-missing-once'
        ws.send_json({'type':'ping'})
        while ws.receive_json()['type']!='pong':pass

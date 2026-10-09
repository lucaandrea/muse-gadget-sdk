import json
import httpx
import pytest

from test_companion import core, client
from muse_companion.work_apps import execute, validate


@pytest.mark.asyncio
async def test_slack_uses_fixed_channel_exact_body_and_receipt():
    config={'kind':'slack','channel':'C123','token_env':'SLACK_TEST'}
    def handler(request):
        assert request.url == 'https://slack.com/api/chat.postMessage'
        body=json.loads(request.content)
        assert body['channel']=='C123' and body['text']=='Reviewed update'
        assert not body['unfurl_links'] and body['client_msg_id']
        return httpx.Response(200,json={'ok':True,'channel':'C123','ts':'123.456'})
    result=await execute(config,{'text':'Reviewed update'},'operation',{'SLACK_TEST':'fake'},transport=httpx.MockTransport(handler))
    assert result['message_id']=='123.456'
    assert 'fake' not in json.dumps(result)
    with pytest.raises(ValueError):
        validate(config,{'text':'Draft','channel':'another-channel'})


@pytest.mark.asyncio
async def test_slack_200_error_is_not_success():
    with pytest.raises(RuntimeError,match='did not confirm'):
        await execute({'kind':'slack','channel':'C123','token_env':'SLACK_TEST'},{'text':'draft'},'op',{'SLACK_TEST':'fake'},transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'ok':False,'error':'invalid_auth'})))


@pytest.mark.asyncio
async def test_linear_graphql_errors_do_not_claim_issue_creation():
    config={'kind':'linear','team_id':'team','token_env':'LINEAR_TEST','auth_type':'api_key'}
    def handler(request):
        assert request.headers['authorization']=='fake'
        assert json.loads(request.content)['variables']['input']['teamId']=='team'
        return httpx.Response(200,json={'errors':[{'message':'private upstream detail'}]})
    with pytest.raises(RuntimeError,match='did not confirm') as error:
        await execute(config,{'title':'Follow up','description':'Reviewed'},'op',{'LINEAR_TEST':'fake'},transport=httpx.MockTransport(handler))
    assert 'private upstream detail' not in str(error.value)


@pytest.mark.asyncio
async def test_calendar_insert_has_stable_event_id_and_no_guests():
    ids=[]
    config={'kind':'google_calendar','calendar_id':'owner@example.com','token_env':'GOOGLE_TEST'}
    def handler(request):
        data=json.loads(request.content);ids.append(data['id'])
        assert 'attendees' not in data and request.url.params['sendUpdates']=='none'
        return httpx.Response(200,json={'id':data['id'],'htmlLink':'https://calendar.google.com/event'})
    args={'title':'Review','description':'Decision review','start':'2026-10-10T09:00:00-07:00','end':'2026-10-10T09:30:00-07:00'}
    for _ in range(2):
        result=await execute(config,args,'one-operation',{'GOOGLE_TEST':'fake'},transport=httpx.MockTransport(handler))
        assert result['event_id']
    assert ids[0]==ids[1]


@pytest.mark.asyncio
async def test_config_change_cannot_redirect_approved_action(core):
    _, store, _, assistant=core
    assistant.integrations.commands['send']={'kind':'slack','channel':'C123','fields':['text'],'token_env':'SLACK_TEST'}
    proposal=await assistant.tool('integration_propose',{'name':'send','arguments_json':'{"text":"Draft"}'},'typed','propose')
    assistant.integrations.commands['send']['channel']='C999'
    async def forbidden(*args):
        pytest.fail('Destination changed but action executed')
    assistant.integrations.execute=forbidden
    with pytest.raises(ValueError,match='definition changed'):
        await assistant.action(proposal['task']['id'],'approve','approve-once')


def test_draft_action_is_reviewable_idempotent_and_revision_bound(client):
    c, store, _=client
    integrations=c.app.state.assistant.integrations
    integrations.commands['send']={'kind':'slack','channel':'C123','fields':['text']}
    task=store.task('studio','Closeout',{'workflow':'closeout'},'completed')
    store.update_task(task['id'],'completed',json.dumps({'text':'Reviewed draft'}))
    data={'name':'send','arguments':{'text':'Reviewed draft'},'revision':1,'operation_id':'draft-send-once'}
    first=c.post('/api/studio/drafts/'+task['id']+'/propose',json=data)
    assert first.status_code==200 and first.json()['state']=='needs_approval'
    payload=json.loads(first.json()['payload'])
    assert payload['destination']=='C123' and payload['arguments']['text']=='Reviewed draft'
    assert c.post('/api/studio/drafts/'+task['id']+'/propose',json=data).json()==first.json()
    assert c.post('/api/studio/drafts/'+task['id']+'/propose',json={**data,'revision':2,'operation_id':'stale-draft-action'}).status_code==409

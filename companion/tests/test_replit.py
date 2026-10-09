import asyncio
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from test_companion import core, client
from muse_companion.replit import ReplitStorage


@pytest.mark.asyncio
async def test_oauth_credentials_are_encrypted_and_expiry_survives_restart(core):
    pytest.importorskip('mcp')
    from mcp.shared.auth import OAuthToken
    settings, store, _, assistant=core
    settings.owner_token='private-owner-secret-12345678901234567890'
    storage=ReplitStorage(settings,store)
    await storage.set_tokens(OAuthToken(access_token='private-replit-token',token_type='Bearer',refresh_token='refresh-secret',expires_in=60))
    raw=json.dumps(store.rows('SELECT * FROM settings'))
    assert 'private-replit-token' not in raw and 'refresh-secret' not in raw
    store.set_setting('replit:expires_at',time.time()-2)
    token=await ReplitStorage(settings,store).get_tokens()
    assert token.expires_in==0 and token.refresh_token=='refresh-secret'
    assert 'private-replit-token' not in json.dumps(assistant.replit.status())


@pytest.mark.asyncio
async def test_oauth_callback_rejects_wrong_state_replay_and_foreign_origin(core):
    replit=core[3].replit
    replit.callback=asyncio.get_running_loop().create_future()
    replit.deadline=time.time()+60
    with pytest.raises(ValueError):
        await replit.redirect('https://attacker.invalid/auth?state=state')
    await replit.redirect('https://replit.com/auth?state=expected')
    with pytest.raises(ValueError):
        replit.complete('code','wrong')
    replit.complete('code','expected')
    assert await replit.receive_callback()==('code','expected')
    with pytest.raises(ValueError):
        replit.complete('code','expected')


@pytest.mark.asyncio
async def test_read_tool_cannot_request_replit_write(core):
    assistant=core[3]
    with pytest.raises(ValueError,match='only reads'):
        await assistant.tool('replit_read',{'tool':'publish_app','arguments_json':'{"replId":"app"}'},'voice','try-publish')


@pytest.mark.asyncio
async def test_create_and_publish_are_distinct_approved_calls(core):
    settings, store, _, assistant=core
    settings.public_url='https://muse.example.com'
    assistant.integrations.commands.update(assistant.replit.catalog())
    calls=[]
    async def call(tool,args):
        calls.append((tool,args))
        return {'ok':True,'receipt':{'app':'created-app','state':'building'}}
    assistant.replit.call=call
    args={'app_description':'The reviewed brief','app_stack':'react_website','name':'Prototype'}
    proposal=await assistant.tool('integration_propose',{'name':'replit_create','arguments_json':json.dumps(args)},'voice','prototype-proposal')
    assert calls==[]
    await assistant.action(proposal['task']['id'],'approve','approve-build')
    assert calls==[('create_app_from_prompt',{'appDescription':'The reviewed brief','app_stack':'react_website','userSpecifiedAppName':'Prototype'})]
    assert all(tool!='publish_app' for tool,_ in calls)
    publish=await assistant.tool('integration_propose',{'name':'replit_publish','arguments_json':'{"repl_id":"created-app"}'},'typed','publish-proposal')
    assert len(calls)==1 and publish['task']['state']=='needs_approval'


@pytest.mark.asyncio
async def test_mcp_validates_discovered_tool_schema_before_execution(core):
    pytest.importorskip('mcp')
    replit=core[3].replit
    core[1].set_setting('replit:tokens','test-storage-present')
    calls=[]
    class Session:
        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name='list_apps',inputSchema={'type':'object','properties':{'limit':{'type':'integer','maximum':100}},'additionalProperties':False})])
        async def call_tool(self,tool,args):
            calls.append((tool,args))
            return SimpleNamespace(isError=False,model_dump=lambda **_:{'content':[{'type':'text','text':'App list'}]})
    @asynccontextmanager
    async def session():
        yield Session()
    replit.session=session
    assert (await replit.call('list_apps',{'limit':10}))['ok']
    with pytest.raises(RuntimeError):
        await replit.call('list_apps',{'command':'delete'})
    assert calls==[('list_apps',{'limit':10})]


def test_private_proxy_header_is_separate_from_companion_auth(client):
    c, store, _=client
    owner=c.headers['authorization']
    assert c.get('/api/state',headers={'authorization':'Bearer proxy-access-token','x-muse-authorization':owner}).status_code==200
    assert c.get('/api/state',headers={'authorization':'Bearer proxy-access-token','x-muse-authorization':'Bearer unknown'}).status_code==401

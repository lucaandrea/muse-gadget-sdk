import asyncio
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from test_companion import core, client
from muse_companion.grep import Grep, save_auth
from muse_companion.work_accounts import Destination, WorkAccounts


def setup(core, service, handler):
    settings, store, _, assistant = core
    settings.owner_token = 'test-owner-key-at-least-32-characters'
    settings.public_url = 'https://companion.example.com'
    settings.work_oauth[service] = {'client_id': 'client-id', 'client_secret': 'client-secret'}
    accounts = assistant.work_accounts
    accounts.transport = httpx.MockTransport(handler)
    return accounts


def install(accounts, service):
    accounts.vault.write(service, {'access_token': 'sensitive-access', 'refresh_token': 'sensitive-refresh', 'scope': 'read write', 'expires_at': time.time() + 3600})
    accounts.store.set_setting('work-state:' + service, {'connected': True, 'account_id': 'account', 'account_name': 'Test account', 'revision': 'grant-one'})


@pytest.mark.asyncio
@pytest.mark.parametrize('service', ['slack', 'linear', 'google_calendar'])
async def test_oauth_state_replay_pkce_secret_storage_and_restart(core, service):
    calls = []
    def handler(request):
        calls.append(request)
        if 'token' in request.url.path or 'oauth.v2.access' in request.url.path:
            form = parse_qs(request.content.decode())
            assert form['client_secret'] == ['client-secret']
            assert form['redirect_uri'] == ['https://companion.example.com/api/work-accounts/' + service + '/callback']
            if service == 'linear':
                assert len(form['code_verifier'][0]) >= 43
            return httpx.Response(200, json={'ok': True, 'access_token': 'private-access', 'refresh_token': 'private-refresh', 'expires_in': 3600, 'scope': 'read write'})
        if service == 'slack':
            return httpx.Response(200, json={'ok': True, 'team_id': 'team', 'user_id': 'bot', 'team': 'Studio'})
        if service == 'linear':
            return httpx.Response(200, json={'data': {'viewer': {'id': 'person', 'name': 'Studio'}}})
        return httpx.Response(200, json={'id': 'calendar@example.com', 'summary': 'Studio'})
    accounts = setup(core, service, handler)
    url = accounts.begin(service)['authorization_url']
    state = parse_qs(urlparse(url).query)['state'][0]
    with pytest.raises(ValueError, match='Invalid or expired'):
        await accounts.complete(service, 'code', 'wrong-state')
    assert calls == []
    await accounts.complete(service, 'code', state)
    with pytest.raises(ValueError, match='Invalid or expired'):
        await accounts.complete(service, 'code', state)
    raw = json.dumps(core[1].rows('SELECT * FROM settings')) + json.dumps(accounts.status())
    assert 'private-access' not in raw and 'private-refresh' not in raw and 'client-secret' not in raw
    restarted = WorkAccounts(core[0], core[1], core[3].integrations, core[3].studio, transport=accounts.transport)
    assert await restarted.token(service) == 'private-access'
    assert accounts.config(service) == {}  # No action destination chosen by OAuth.


@pytest.mark.asyncio
async def test_refresh_is_serialized_and_rotated_token_is_durable(core):
    calls = []
    def handler(request):
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={'access_token': 'refreshed', 'refresh_token': 'rotated', 'expires_in': 3600})
    accounts = setup(core, 'linear', handler)
    install(accounts, 'linear')
    value = accounts.vault.read('linear');value['expires_at'] = time.time() - 1;accounts.vault.write('linear', value)
    assert await asyncio.gather(accounts.token('linear'), accounts.token('linear')) == ['refreshed', 'refreshed']
    assert len(calls) == 1 and calls[0]['refresh_token'] == ['sensitive-refresh']
    assert accounts.vault.read('linear')['refresh_token'] == 'rotated'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['disconnect', 'reconnect'])
async def test_destination_from_previous_grant_cannot_configure_new_account(core, change):
    started, finish = asyncio.Event(), asyncio.Event()
    async def handler(request):
        started.set()
        await finish.wait()
        return httpx.Response(200, json={'data': {'teams': {'nodes': [{'id': 'old-team', 'name': 'Old account'}], 'pageInfo': {'hasNextPage': False}}}})
    accounts = setup(core, 'linear', handler)
    install(accounts, 'linear')
    pending = asyncio.create_task(accounts.configure('linear', Destination(destination='old-team')))
    await started.wait()
    await accounts.disconnect('linear')
    if change == 'reconnect':
        install(accounts, 'linear')
        accounts.store.set_setting('work-state:linear', {**accounts.state('linear'), 'revision': 'new-grant'})
    finish.set()
    with pytest.raises(ValueError, match='account changed'):
        await pending
    assert accounts.config('linear') == {}
    assert 'work_linear' not in accounts.integrations.commands


@pytest.mark.asyncio
async def test_destination_is_access_checked_and_invalidates_old_approval(core):
    accounts = setup(core, 'slack', lambda request: httpx.Response(200, json={'ok': True, 'channels': [
        {'id': 'C1', 'name': 'one', 'is_member': True}, {'id': 'C2', 'name': 'two', 'is_member': True}, {'id': 'C3', 'name': 'unjoined', 'is_member': False}]}))
    install(accounts, 'slack')
    with pytest.raises(ValueError, match='accessible destination'):
        await accounts.configure('slack', Destination(destination='C3'))
    await accounts.configure('slack', Destination(destination='C1'))
    proposal = core[3].integrations.proposal('work_slack', {'text': 'Reviewed text'})
    await accounts.configure('slack', Destination(destination='C2'))
    with pytest.raises(ValueError, match='destination or definition changed'):
        core[3].integrations.verify_proposal(proposal)
    await accounts.disconnect('slack')
    assert 'work_slack' not in core[3].integrations.commands
    assert accounts.vault.read('slack') is None


@pytest.mark.asyncio
async def test_calendar_sync_handles_pages_dates_cancellation_and_partial_coverage(core):
    cancelled = False
    def handler(request):
        assert request.url.host == 'www.googleapis.com'
        if cancelled:
            return httpx.Response(200, json={'items': [{'id': 'day', 'status': 'cancelled', 'updated': '2026-10-10T00:00:00Z'}]})
        if request.url.params.get('pageToken'):
            return httpx.Response(200, json={'timeZone': 'America/Los_Angeles', 'items': []})
        return httpx.Response(200, json={'timeZone': 'America/Los_Angeles', 'nextPageToken': 'second', 'items': [{
            'id': 'day', 'summary': 'All-day planning', 'start': {'date': '2026-10-09'}, 'end': {'date': '2026-10-10'},
            'updated': '2026-10-09T09:00:00Z', 'htmlLink': 'https://calendar.google.com/event/day', 'attendees': [{'email': 'person@example.com'}]}]})
    accounts = setup(core, 'google_calendar', handler)
    install(accounts, 'google_calendar')
    accounts.store.set_setting('work-config:google_calendar', {'destination': 'primary', 'sync_enabled': True})
    assert (await accounts.sync('google_calendar'))['imported'] == 1
    first = core[3].studio.records('meeting')[0]
    assert first['details']['start'] == '2026-10-09T00:00:00-07:00'
    assert len(first['details']['participants']) == 1
    await accounts.sync('google_calendar')
    assert len(core[3].studio.records('meeting')) == 1
    assert core[3].studio.records('meeting')[0]['revision'] == first['revision']
    cancelled = True
    await accounts.sync('google_calendar')
    assert core[3].studio.records('meeting')[0]['details']['state'] == 'cancelled'


@pytest.mark.asyncio
async def test_linear_import_preserves_evidence_and_resolves_blockers(core):
    blocked = True
    def handler(request):
        body = json.loads(request.content)
        assert body['variables']['team'] == 'team1'
        return httpx.Response(200, json={'data': {'issues': {'nodes': [{
            'id': 'issue', 'identifier': 'AI-1', 'title': 'Scope review', 'description': 'Exact source evidence', 'url': 'https://linear.app/studio/issue/AI-1',
            'updatedAt': '2026-10-09T12:00:00Z' if blocked else '2026-10-09T13:00:00Z', 'dueDate': '2026-10-10', 'priority': 2,
            'state': {'name': 'In progress' if blocked else 'Done', 'type': 'started' if blocked else 'completed'},
            'assignee': {'name': 'Owner'}, 'project': {'id': 'project', 'name': 'Studio project', 'description': 'Improve onboarding', 'url': 'https://linear.app/project/1', 'updatedAt': '2026-10-09T01:00:00Z'},
            'labels': {'nodes': [{'name': 'blocked'}] if blocked else []}}], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}})
    accounts = setup(core, 'linear', handler);install(accounts, 'linear')
    accounts.store.set_setting('work-config:linear', {'destination': 'team1'})
    await accounts.sync('linear')
    commitment = core[3].studio.records('commitment')[0]
    assert commitment['details']['state'] == 'waiting' and commitment['source_url'].startswith('https://linear.app/')
    assert core[3].studio.records('blocker')[0]['details']['state'] == 'open'
    assert len(core[3].studio.records('project')) == 1
    blocked = False;await accounts.sync('linear')
    assert core[3].studio.records('commitment')[0]['details']['state'] == 'done'
    assert core[3].studio.records('blocker')[0]['details']['state'] == 'resolved'


@pytest.mark.asyncio
async def test_source_failure_preserves_prior_data_and_success_stamp(core):
    accounts = setup(core, 'linear', lambda request: httpx.Response(401))
    install(accounts, 'linear')
    accounts.store.set_setting('work-config:linear', {'destination': 'team1'})
    accounts.store.set_setting('work-sync:linear', {'last_success': 123})
    with pytest.raises(ValueError, match='HTTP 401'):
        await accounts.sync('linear')
    assert accounts.store.setting('work-sync:linear')['last_success'] == 123
    assert accounts.store.setting('work-sync:linear')['error']


def test_account_routes_require_owner(client):
    browser, store, _ = client
    device = store.create_device('Pocket')
    browser.headers['Authorization'] = 'Bearer ' + device['token']
    assert browser.get('/api/work-accounts').status_code == 403
    assert browser.post('/api/work-accounts/slack/connect').status_code == 403
    assert browser.get('/api/work-accounts/slack/callback?state=forged&code=forged').status_code == 400


def test_grep_legacy_session_becomes_encrypted_and_survives_directory_replacement(core, tmp_path):
    settings, store, _, _ = core
    settings.owner_token = 'stable-private-owner-key-12345678901234567890'
    grep = Grep(settings, store)
    save_auth(grep.auth_path, {'integration': 'muse', 'token': 'a' * 64, 'expires_at': time.time() + 3600})
    assert grep.auth()['token'] == 'a' * 64
    assert not grep.auth_path.exists()
    assert 'a' * 64 not in json.dumps(store.rows('SELECT * FROM settings'))
    settings.data_dir = tmp_path / 'replacement'
    assert Grep(settings, store).auth()['token'] == 'a' * 64

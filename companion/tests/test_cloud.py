"""Real PostgreSQL integration tests. Install the test-cloud extra to run."""
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_companion import FakeProvider, answer
from muse_companion.app import create_app
from muse_companion.config import Settings
from muse_companion.database import postgres_sql
from muse_companion.store import Store, Conflict, new_id


@pytest.fixture(scope="module")
def postgres():
    pgserver = pytest.importorskip("pgserver")
    with tempfile.TemporaryDirectory(prefix="muse-pg-test-") as directory:
        server = pgserver.get_server(Path(directory), cleanup_mode="stop")
        yield server.get_uri()
        server.cleanup()


@pytest.fixture
def database_url(postgres):
    import psycopg
    from psycopg.conninfo import make_conninfo
    schema = "muse_test_" + new_id()
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(schema)))
    yield make_conninfo(postgres, options="-c search_path=" + schema)
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(schema)))


def test_adapter_does_not_substitute_literal_question_marks():
    assert postgres_sql("SELECT '?' AS question WHERE name=? AND x LIKE '%a%'", True) == "SELECT '?' AS question WHERE name=%s AND x LIKE '%%a%%'"


def test_real_postgres_preserves_data_receipts_audio_blobs_and_owner_after_restart(tmp_path, database_url):
    store = Store(tmp_path / 'local.db', database_url)
    owner = 'test-owner-secret-with-at-least-32-characters'
    store.provision_owner(owner)
    memory = store.save_memory('Demo notes', 'meeting', project_id='project')
    store.capture('voice-note', b'\x01\x00' * 320)
    store.put_blob('document', b'durable source')
    store.begin_operation('once', {'title':'Demo'})
    task = store.task('integration','Publish draft',{},'executing')
    store.finish_operation('once', {'id': task['id']})
    store.close()
    # A fresh app directory mimics the filesystem being replaced by deployment.
    store = Store(tmp_path / 'new-deploy' / 'local.db', database_url)
    store.provision_owner(owner)
    store.recover()
    assert store.authenticate(owner)['role'] == 'owner'
    assert store.blob('document') == b'durable source'
    assert store.one('SELECT pcm FROM captures')['pcm'] == b'\x01\x00' * 320
    assert store.begin_operation('once', {'title':'Demo'}) == {'id':task['id']}
    assert store.one('SELECT state FROM tasks')['state'] == 'uncertain'
    assert store.search_memories('notes')[0]['id'] == memory['id']
    store.forget_memory(memory['id'])
    assert store.rows('SELECT * FROM memory_context') == []
    with pytest.raises(Conflict):
        store.provision_owner('different-owner-secret-with-at-least-32-characters')
    store.close()


def test_postgres_compound_writes_rollback(tmp_path, database_url):
    store = Store(tmp_path / 'unused.db', database_url)
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.task('todo','Not committed',{})
            raise RuntimeError('fail before receipt')
    assert store.rows('SELECT * FROM tasks') == []
    assert store.rows('SELECT * FROM events') == []
    store.close()


def test_postgres_app_voice_studio_and_durable_document(tmp_path, database_url):
    owner = 'owner-for-private-cloud-test-123456789'
    settings = Settings(data_dir=tmp_path,database_url=database_url,owner_token=owner,hosted=True)
    provider = FakeProvider()
    async def upload(name, content):
        assert content == b'Project evidence'
        return 'file_test','vs_test'
    provider.upload_document = upload
    app = create_app(settings, provider)
    with TestClient(app) as client:
        client.headers['authorization']='Bearer '+owner
        project = client.post('/api/studio/records',json={'kind':'project','title':'Cloud Studio','observed_at':'2026-10-09T09:00:00-07:00','details':{},'operation_id':'create-cloud-project'})
        assert project.status_code == 200, project.text
        assert client.post('/api/compatibility').json()['storage_ready']
        upload = client.post('/api/documents',files={'file':('evidence.txt',b'Project evidence','text/plain')})
        assert upload.status_code == 200, upload.text
        path = '/api/documents/'+upload.json()['id']+'/download'
        assert client.get(path).content == b'Project evidence'
        assert client.get('/api/diagnostics').json()['companion']['storage']=='postgresql'
        with client.websocket_connect('/v1/device') as ws:
            assert ws.receive_json()['type']=='hello'
            ws.send_json({'type':'ping'})
            while ws.receive_json()['type']!='pong':
                pass
    app = create_app(Settings(data_dir=tmp_path/'replacement',database_url=database_url,owner_token=owner,hosted=True),FakeProvider())
    with TestClient(app) as client:
        client.headers['authorization']='Bearer '+owner
        assert client.get(path).content==b'Project evidence'
        assert client.get('/api/studio').json()['records'][0]['title']=='Cloud Studio'

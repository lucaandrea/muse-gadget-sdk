import json

import pytest

from test_cloud import postgres, database_url
from muse_companion.backup import export_backup, restore_backup, initialize
from muse_companion.config import Settings
from muse_companion.store import Store


@pytest.mark.parametrize('backend', ['sqlite', 'postgres'])
def test_authenticated_backup_restores_records_audio_files_grants_and_sequences(tmp_path, database_url, backend):
    source_path = tmp_path / 'source';source_path.mkdir()
    secret = 'stable-original-owner-secret-12345678901234567890'
    settings = Settings(data_dir=source_path, owner_token=secret)
    source = Store(source_path / 'muse.sqlite3')
    initialize(source, settings)
    source.provision_owner(secret)
    memory = source.save_memory('Project decision', 'meeting')
    source.capture('saved-audio', b'\x01\x00' * 8000)
    source.task('integration', 'Interrupted action', {}, 'executing')
    source.event('example', {'evidence': 'preserve this'})
    source.execute("INSERT INTO conversation(role,text) VALUES ('user','Original context')")
    source.set_setting('encrypted-grant', 'unchanged-ciphertext')
    source.execute("INSERT INTO documents VALUES ('doc1','original.txt','remote1','ready',123)")
    (source_path / 'documents').mkdir();(source_path / 'documents' / 'doc1').write_bytes(b'original file')
    backup = tmp_path / 'private.muse-backup'
    counts = export_backup(source, settings, backup)
    assert counts['memories'] == 1 and counts['blobs'] == 1
    assert b'Original context' not in backup.read_bytes() and b'unchanged-ciphertext' not in backup.read_bytes()
    assert backup.stat().st_mode & 0o777 == 0o600
    replacement = Settings(data_dir=tmp_path / 'replacement', owner_token=secret)
    dest = Store(replacement.data_dir / 'muse.sqlite3', database_url if backend == 'postgres' else '')
    restore_backup(dest, replacement, backup)
    dest.provision_owner(secret)
    assert dest.authenticate(secret)['role'] == 'owner'
    assert dest.search_memories('decision')[0]['id'] == memory['id']
    assert dest.blob('doc1') == b'original file'
    assert bytes(dest.one('SELECT pcm FROM captures')['pcm']) == b'\x01\x00' * 8000
    assert dest.one('SELECT state FROM tasks')['state'] == 'uncertain'
    assert dest.setting('encrypted-grant') == 'unchanged-ciphertext'
    dest.event('after-restore', {})  # Sequence must advance beyond restored IDs.
    dest.execute("INSERT INTO conversation(role,text) VALUES ('user','After restore')")
    with pytest.raises(ValueError, match='empty destination'):
        restore_backup(dest, replacement, backup)
    assert dest.search_memories('decision')[0]['id'] == memory['id']
    source.close();dest.close()


def test_wrong_key_or_tampered_backup_never_writes(tmp_path):
    settings = Settings(data_dir=tmp_path, owner_token='original-owner-secret-12345678901234567890')
    source = Store(tmp_path / 'source.sqlite3')
    backup = tmp_path / 'backup.muse'
    export_backup(source, settings, backup)
    dest = Store(tmp_path / 'dest.sqlite3')
    settings.owner_token = 'different-owner-secret-12345678901234567890'
    with pytest.raises(ValueError, match='authenticated'):
        restore_backup(dest, settings, backup)
    assert dest.rows('SELECT * FROM devices') == []
    source.close();dest.close()

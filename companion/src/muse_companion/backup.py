"""Encrypted, versioned logical backups for SQLite/PostgreSQL migration.

Restore requires an empty destination and the original stable owner secret.
No database URLs, external API keys, or deployment tokens are serialized.
"""
import base64
import hashlib
import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

TABLES = ('devices', 'memories', 'memory_context', 'reminders', 'tasks', 'operations', 'events', 'settings',
          'notifications', 'documents', 'blobs', 'usage', 'conversation', 'captures', 'meeting_segments',
          'work_revisions', 'study_sessions', 'study_attempts', 'studio_records', 'studio_revisions',
          'studio_alerts', 'studio_seen', 'notebooks', 'notebook_segments')
SEQUENCES = {'events': 'seq', 'conversation': 'seq', 'study_attempts': 'id'}
LIMIT = 256 * 1024 * 1024


def key(settings):
    path = settings.data_dir / 'owner-token'
    secret = settings.owner_token or (path.read_text().strip() if path.exists() else '')
    if len(secret) < 32:
        raise ValueError('Backups require the stable owner secret, kept separately from the backup')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('muse-backup-v1:' + secret).encode()).digest()))


def initialize(store, settings):
    from .lessons import Lessons
    from .notebooks import Notebooks
    from .studio import Studio
    Lessons(store); Notebooks(store); Studio(store, None, settings, None)


def pack(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'$binary': base64.b64encode(value).decode()}
    return value


def unpack(value):
    if isinstance(value, dict) and set(value) == {'$binary'}:
        return base64.b64decode(value['$binary'], validate=True)
    if isinstance(value, (dict, list)):
        raise ValueError('Invalid cell in backup')
    return value


def export_backup(store, settings, destination: Path):
    initialize(store, settings)
    # Move legacy Grep credentials into encrypted database storage first.
    from .grep import Grep
    Grep(settings, store).auth()
    with store.transaction():
        data = {table: [{column: pack(value) for column, value in row.items()} for row in store.rows('SELECT * FROM ' + table)] for table in TABLES}
        # Older deployments kept originals on disk. Preserve them as blobs in
        # the logical backup without changing the running document records.
        existing = {row['id'] for row in data['blobs']}
        for document in data['documents']:
            identity = document['id']
            if not isinstance(identity, str) or not identity.isalnum():
                raise ValueError('Invalid document identity in source database')
            if identity in existing:
                continue
            path = settings.data_dir / 'documents' / identity
            if path.is_file():
                data['blobs'].append({'id': identity, 'content': pack(path.read_bytes()), 'created': time.time()})
        payload = json.dumps({'version': 1, 'schema': 5, 'created': time.time(), 'tables': data}, separators=(',', ':')).encode()
    if len(payload) > LIMIT:
        raise ValueError('Backup exceeds the 256 MB export limit; use a database-native backup')
    encrypted = key(settings).encrypt(payload)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(encrypted); handle.flush(); os.fsync(handle.fileno())
    return {table: len(rows) for table, rows in data.items()}


def restore_backup(store, settings, source: Path):
    if source.stat().st_size > LIMIT * 2:
        raise ValueError('Backup is too large')
    try:
        payload = key(settings).decrypt(source.read_bytes())
        if len(payload) > LIMIT:
            raise ValueError('Backup is too large')
        document = json.loads(payload)
    except (InvalidToken, json.JSONDecodeError):
        raise ValueError('Backup could not be authenticated with this owner secret') from None
    if document.get('version') != 1 or document.get('schema') != 5 or set(document.get('tables', {})) != set(TABLES):
        raise ValueError('Unsupported backup format or schema')
    initialize(store, settings)
    data = document['tables']
    with store.transaction():
        # Store initializes this schema marker on an otherwise empty Postgres
        # database. It is metadata, not pre-existing user data.
        store.execute("DELETE FROM settings WHERE key='storage_schema' AND value='5'")
        for table in TABLES:
            if store.one('SELECT * FROM ' + table + ' LIMIT 1'):
                raise ValueError('Restore requires an empty destination; existing data was preserved')
            columns = {row['name'] for row in store.rows('PRAGMA table_info(' + table + ')')}
            if not isinstance(data[table], list) or any(not isinstance(row, dict) or set(row) != columns for row in data[table]):
                raise ValueError('Backup columns do not match the destination schema')
        for table in TABLES:
            for row in data[table]:
                columns = sorted(row)
                store.execute('INSERT INTO ' + table + ' (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')',
                              tuple(unpack(row[column]) for column in columns))
        if store.backend == 'postgresql':
            for table, column in SEQUENCES.items():
                # These identifiers are fixed above, never supplied by a file.
                store.execute("SELECT setval(pg_get_serial_sequence('" + table + "','" + column + "'),COALESCE(MAX(" + column + "),1),COUNT(*)>0) FROM " + table)
        # Recovery changes interrupted external actions to uncertain; it never
        # repeats them. This also safely requeues retained transcription work.
        store.recover()
        store.execute('PRAGMA user_version=5')
    return {table: len(rows) for table, rows in data.items()}

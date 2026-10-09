"""Device notebooks: durable segmented capture and explicit, retryable finish."""
import json
import re

from .store import Conflict


class Notebooks:
    def __init__(self, store):
        self.store = store
        store.db.executescript("""
          CREATE TABLE IF NOT EXISTS notebooks(id TEXT PRIMARY KEY,device TEXT NOT NULL,local_id TEXT NOT NULL,task TEXT NOT NULL,expected INTEGER,markers TEXT NOT NULL DEFAULT '[]',UNIQUE(device,local_id));
          CREATE TABLE IF NOT EXISTS notebook_segments(notebook TEXT NOT NULL,position INTEGER NOT NULL,capture TEXT UNIQUE NOT NULL,PRIMARY KEY(notebook,position));
        """)

    def book(self, device, local_id):
        if not re.fullmatch(r"[a-f0-9]{32}", local_id):
            raise ValueError("Invalid notebook identity")
        row = self.store.one("SELECT * FROM notebooks WHERE device=? AND local_id=?", (device, local_id))
        if row:
            return row
        task = self.store.task("meeting", "Pocket notebook", {"transcript": "", "device_notebook": True}, "recording")
        self.store.execute("INSERT INTO notebooks(id,device,local_id,task) VALUES (?,?,?,?)", (task['id'], device, local_id, task['id']))
        return self.store.one("SELECT * FROM notebooks WHERE id=?", (task['id'],))

    def accept(self, capture, pcm, context):
        position = context.get('position')
        if type(position) is not int or not 0 <= position < 240 or type(context.get('marked', False)) is not bool:
            raise ValueError('Invalid notebook segment')
        with self.store.transaction():
            book = self.book(context['device_id'], context['notebook'])
            old = self.store.one('SELECT * FROM notebook_segments WHERE notebook=? AND position=?', (book['id'], position))
            if old and old['capture'] != capture:
                raise Conflict('Notebook segment already has a different capture')
            if book['expected'] is not None and position >= book['expected']:
                raise Conflict('Notebook was finished with fewer segments')
            self.store.capture(capture, pcm, context)
            self.store.execute('INSERT OR IGNORE INTO notebook_segments VALUES (?,?,?)', (book['id'], position, capture))

    def complete(self, capture, text):
        with self.store.transaction():
            segment = self.store.one('SELECT * FROM notebook_segments WHERE capture=?', (capture,))
            if not segment:
                raise ValueError('Notebook segment metadata is missing')
            saved = self.store.one('SELECT digest,context FROM captures WHERE id=?', (capture,))
            context = json.loads(saved['context'])
            self.store.execute("INSERT OR IGNORE INTO meeting_segments(meeting,position,digest,pcm,transcript,marked) VALUES (?,?,?,X'',?,?)",
                               (segment['notebook'], segment['position'], saved['digest'], text, int(context.get('marked', False))))
            result = {'text': 'Notebook segment saved.', 'cards': [], 'citations': []}
            self.store.execute("UPDATE captures SET state='completed',transcript=?,result=?,pcm=X'' WHERE id=?", (text, json.dumps(result), capture))
            self.reconcile(segment['notebook'])
            return result

    def finish(self, device, local_id, expected, markers):
        if type(expected) is not int or not 1 <= expected <= 240 or not isinstance(markers, list) or len(markers) > 240 or any(type(n) is not int or not 0 <= n < expected for n in markers):
            raise ValueError('Invalid notebook finish count or markers')
        markers = sorted(set(markers))
        with self.store.transaction():
            book = self.book(device, local_id)
            prior = book['expected']
            if prior is not None and (prior != expected or json.loads(book['markers']) != markers):
                raise Conflict('The finished notebook cannot change its capture count or markers')
            if self.store.one('SELECT position FROM notebook_segments WHERE notebook=? AND position>=?', (book['id'], expected)):
                raise Conflict('Finish omitted captured segments')
            self.store.execute('UPDATE notebooks SET expected=?,markers=? WHERE id=?', (expected, json.dumps(markers), book['id']))
            self.reconcile(book['id'])
            return {'id': local_id, 'task_id': book['task'], 'state': self.store.one('SELECT state FROM tasks WHERE id=?', (book['task'],))['state']}

    def reconcile(self, identity):
        book = self.store.one('SELECT * FROM notebooks WHERE id=?', (identity,))
        if book['expected'] is None:
            return
        segments = self.store.rows('SELECT * FROM meeting_segments WHERE meeting=? ORDER BY position', (identity,))
        if [s['position'] for s in segments] != list(range(book['expected'])) or any(s['transcript'] is None for s in segments):
            return
        task = self.store.one('SELECT state,payload FROM tasks WHERE id=?', (book['task'],))
        if task['state'] != 'recording':
            return
        markers = set(json.loads(book['markers']))
        transcript = '\n'.join(f"Segment {s['position'] + 1}{' [MARKED IMPORTANT]' if s['marked'] or s['position'] in markers else ''}: {s['transcript']}" for s in segments)
        payload = {**json.loads(task['payload']), 'transcript': transcript, 'instructions': 'Summarize this notebook with decisions, questions, and proposed follow-ups. Distinguish suggestions from agreed commitments.'}
        self.store.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(payload), book['task']))
        self.store.update_task(book['task'], 'queued')

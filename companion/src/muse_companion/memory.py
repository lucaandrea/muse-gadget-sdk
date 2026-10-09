"""Bounded hybrid memory recall; edits/deletions cannot resurrect stale vectors."""
import asyncio
import hashlib
import json
import math

from .openai_api import ProviderError

MODEL = "text-embedding-3-small/256"


def digest(row):
    return hashlib.sha256(row["text"].encode()).hexdigest()


def unit(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 3072 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError("Invalid embedding vector")
    magnitude = math.sqrt(sum(v*v for v in values))
    if not magnitude:
        raise ValueError("Empty embedding vector")
    return [v / magnitude for v in values]


class Memory:
    def __init__(self, store, provider):
        self.store, self.provider = store, provider
        self.lock = asyncio.Lock()

    def rows(self, project_id="", person_id=""):
        return self.store.rows("SELECT m.id,m.text,m.source,m.created,m.updated,COALESCE(c.project_id,'') AS project_id,COALESCE(c.person_id,'') AS person_id,c.model,c.digest,c.embedding FROM memories m LEFT JOIN memory_context c ON c.id=m.id WHERE (?='' OR c.project_id=?) AND (?='' OR c.person_id=?) ORDER BY m.updated DESC LIMIT 1000", (project_id, project_id, person_id, person_id))

    async def search(self, query, project_id="", person_id=""):
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError("Use a memory query of up to 4000 characters")
        note, mode, query_vector = "", "literal", None
        async with self.lock:
            rows = self.rows(project_id, person_id)
            if query.strip() and rows and hasattr(self.provider, "embed"):
                pending = [r for r in rows if r["model"] != MODEL or r["digest"] != digest(r)][:49]
                try:
                    vectors = await self.provider.embed([query] + [r["text"] for r in pending])
                    if len(vectors) != len(pending) + 1:
                        raise ValueError("Incomplete embedding response")
                    vectors = [unit(v) for v in vectors]
                    if any(len(v) != len(vectors[0]) for v in vectors):
                        raise ValueError("Inconsistent embedding dimensions")
                    query_vector = vectors[0]
                    for row, vector in zip(pending, vectors[1:]):
                        # Re-read after the network await. A deletion or correction
                        # wins; never recreate a forgotten memory from this snapshot.
                        with self.store.transaction():
                            current = self.store.one("SELECT text FROM memories WHERE id=?", (row["id"],))
                            if current and digest(current) == digest(row):
                                self.store.execute("INSERT OR IGNORE INTO memory_context(id) VALUES (?)", (row["id"],))
                                self.store.execute("UPDATE memory_context SET model=?,digest=?,embedding=? WHERE id=?", (MODEL, digest(row), json.dumps(vector), row["id"]))
                    mode = "hybrid"
                except (ProviderError, ValueError):
                    note = "Semantic recall is unavailable; showing literal matches."
            elif query.strip() and rows:
                note = "Semantic recall is unavailable; showing literal matches."
            rows = self.rows(project_id, person_id)
            words = query.casefold().split()[:20]
            scored = []
            indexed = 0
            for row in rows:
                lexical = sum(w in row["text"].casefold() for w in words) / max(1, len(words))
                similarity = 0
                if query_vector and row["model"] == MODEL and row["digest"] == digest(row):
                    try:
                        vector = unit(json.loads(row["embedding"]))
                        if len(vector) == len(query_vector):
                            similarity = sum(a*b for a, b in zip(query_vector, vector))
                            indexed += 1
                    except (ValueError, TypeError):
                        pass
                if not words or lexical or similarity >= .25:
                    scored.append((lexical + max(0, similarity), {k: v for k, v in row.items() if k not in ("embedding", "digest", "model")}))
            if query_vector and indexed < len(rows):
                note = "Semantic indexing is partial; remaining memories use literal matching and will index on later searches."
            scored.sort(key=lambda pair: pair[0], reverse=True)
            return {"memories": [r for _, r in scored[:10]], "mode": mode, "note": note, "searched": len(rows), "indexed": indexed}

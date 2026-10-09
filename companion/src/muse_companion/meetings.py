"""Bounded, retryable recording segments; meeting summaries never create actions."""
import asyncio
import hashlib
import json

from fastapi import Depends, HTTPException, Request

from .openai_api import wav_bytes
from .store import Conflict


def mount_meetings(app, store, provider, owner):
    lock = asyncio.Lock()

    @app.post("/api/meetings")
    async def start(request: Request, user=Depends(owner)):
        title = str((await request.json()).get("title", "Meeting notebook"))[:200]
        return store.task("meeting", title, {"transcript": ""}, "recording")

    @app.put("/api/meetings/{meeting}/segments/{position}")
    async def segment(meeting: str, position: int, request: Request, marked: bool = False, user=Depends(owner)):
        if not 0 <= position < 180:
            raise ValueError("A recording is limited to one hour")
        task = store.one("SELECT * FROM tasks WHERE id=? AND kind='meeting' AND state='recording'", (meeting,))
        if not task:
            raise HTTPException(404, "Active meeting not found")
        pcm = bytearray()
        async for chunk in request.stream():
            pcm.extend(chunk)
            if len(pcm) > 960000:
                raise ValueError("Send audio in segments of at most 30 seconds")
        if not pcm or len(pcm) % 2:
            raise ValueError("Expected mono PCM16 at 16 kHz")
        digest = hashlib.sha256(pcm).hexdigest()
        async with lock:
            old = store.one("SELECT * FROM meeting_segments WHERE meeting=? AND position=?", (meeting, position))
            if old and old["digest"] != digest:
                raise Conflict("Segment identity was reused with different audio")
            if old and old["transcript"] is not None:
                return {"text": old["transcript"], "position": position}
            store.execute("INSERT OR IGNORE INTO meeting_segments(meeting,position,digest,pcm,marked) VALUES (?,?,?,?,?)", (meeting, position, digest, bytes(pcm), marked))
            text = await provider.transcribe(wav_bytes(bytes(pcm)))
            store.execute("UPDATE meeting_segments SET transcript=?,pcm=X'' WHERE meeting=? AND position=?", (text, meeting, position))
            return {"text": text, "position": position}

    @app.post("/api/meetings/{meeting}/finish")
    async def finish(meeting: str, user=Depends(owner)):
        async with lock:
            task = store.one("SELECT * FROM tasks WHERE id=? AND kind='meeting'", (meeting,))
            if not task:
                raise HTTPException(404, "Meeting not found")
            if task["state"] != "recording":
                return {"ok": True, "state": task["state"]}
            if json.loads(task["payload"]).get("device_notebook"):
                raise Conflict("Finish this notebook on the device so every saved segment is included")
            segments = store.rows("SELECT * FROM meeting_segments WHERE meeting=? ORDER BY position", (meeting,))
            if not segments:
                raise ValueError("Record at least one segment")
            # Any retained segment can be retranscribed after a dropped request.
            for segment in segments:
                if segment["transcript"] is None:
                    segment["transcript"] = await provider.transcribe(wav_bytes(segment["pcm"]))
                    store.execute("UPDATE meeting_segments SET transcript=?,pcm=X'' WHERE meeting=? AND position=?", (segment["transcript"], meeting, segment["position"]))
            expected = list(range(segments[-1]["position"] + 1))
            if [s["position"] for s in segments] != expected:
                raise Conflict("Some recording segments are missing; recover them before finalizing")
            transcript = "\n".join(f"Segment {s['position'] + 1}{' [MARKED IMPORTANT]' if s['marked'] else ''}: {s['transcript']}" for s in segments)
            store.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps({"transcript": transcript}), meeting))
            store.update_task(meeting, "queued")
            return {"ok": True, "state": "queued"}

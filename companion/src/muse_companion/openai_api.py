from __future__ import annotations

import asyncio
import base64
import io
import json
import wave
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import websockets

from .config import Settings
from .store import Store


class ProviderError(RuntimeError):
    """Safe to display: never includes upstream bodies, headers or credentials."""


class OpenAI:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store
        self.http = httpx.AsyncClient(base_url="https://api.openai.com/v1/", headers={"Authorization": f"Bearer {settings.api_key}"}, timeout=httpx.Timeout(120, connect=15))

    def reserve(self):
        day = datetime.now(ZoneInfo(self.settings.timezone)).date().isoformat()
        self.store.reserve_usage(day, "model_calls", 1, self.settings.daily_model_calls)

    async def request(self, method: str, path: str, **kwargs):
        if not self.settings.api_key:
            raise ProviderError("Set OPENAI_API_KEY on the companion backend")
        self.reserve()
        try:
            response = await self.http.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"OpenAI returned HTTP {exc.response.status_code}; check account access and limits") from None
        except httpx.HTTPError:
            raise ProviderError("OpenAI is temporarily unreachable") from None

    async def respond(self, inputs, *, tools=None, model=None, instructions="", schema=None):
        payload = {"model": model or self.settings.model, "input": inputs, "instructions": instructions,
                   "max_output_tokens": 3000, "store": False}
        if tools:
            payload["tools"] = tools
        if schema:
            payload["text"] = {"format": {"type": "json_schema", "name": "result", "strict": True, "schema": schema}}
        return (await self.request("POST", "responses", json=payload)).json()

    @staticmethod
    def text(response: dict) -> str:
        return "\n".join(part.get("text", "") for item in response.get("output", []) if item.get("type") == "message"
                         for part in item.get("content", []) if part.get("type") == "output_text")

    @staticmethod
    def citations(response: dict) -> list[dict]:
        citations = []
        for item in response.get("output", []):
            for part in item.get("content", []):
                for citation in part.get("annotations", []):
                    if citation.get("type") in ("url_citation", "file_citation"):
                        citations.append(citation)
        return citations

    async def transcribe(self, audio: bytes, filename="note.wav") -> str:
        result = await self.request("POST", "audio/transcriptions", data={"model": self.settings.transcription_model}, files={"file": (filename, audio)})
        return result.json()["text"]

    async def speak(self, text: str) -> bytes:
        response = await self.request("POST", "audio/speech", json={"model": "gpt-4o-mini-tts", "voice": self.settings.voice, "input": text[:3000], "response_format": "pcm"})
        return resample_24_to_16(response.content)

    async def image(self, prompt: str) -> bytes:
        result = (await self.request("POST", "images/generations", json={"model": "gpt-image-2.5-flare", "prompt": prompt[:4000], "size": "1024x1024", "quality": "low", "n": 1})).json()
        return base64.b64decode(result["data"][0]["b64_json"])

    async def translate(self, pcm: bytes, language: str) -> dict:
        if language not in {"en", "es", "fr", "de", "it", "pt", "ja", "ko", "zh", "ar", "hi"}:
            raise ValueError("Choose a supported translation language")
        self.reserve()
        source, translated, audio = [], [], bytearray()
        try:
            async with asyncio.timeout(90):
                async with websockets.connect("wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate",
                    additional_headers={"Authorization": f"Bearer {self.settings.api_key}"}, max_size=2**22, open_timeout=15) as ws:
                    await ws.send(json.dumps({"type": "session.update", "session": {"audio": {"output": {"language": language}}}}))
                    async def upload():
                        converted = resample_16_to_24(pcm)
                        for i in range(0, len(converted), 4800):
                            await ws.send(json.dumps({"type": "session.input_audio_buffer.append", "audio": base64.b64encode(converted[i:i+4800]).decode()}))
                            await asyncio.sleep(0.02)
                        await ws.send(json.dumps({"type": "session.close"}))
                    sending = asyncio.create_task(upload())
                    try:
                        async for message in ws:
                            event = json.loads(message)
                            kind = event.get("type")
                            if kind == "session.output_audio.delta":
                                audio.extend(base64.b64decode(event["delta"]))
                                if len(audio) > 24_000 * 2 * 120:
                                    raise ProviderError("Translation exceeded the playback limit")
                            elif kind == "session.input_transcript.delta": source.append(event["delta"])
                            elif kind == "session.output_transcript.delta": translated.append(event["delta"])
                            elif kind == "session.closed": break
                            elif kind == "error": raise ProviderError("Translation service rejected the session")
                        await sending
                    finally:
                        sending.cancel()
                        await asyncio.gather(sending, return_exceptions=True)
        except (websockets.WebSocketException, TimeoutError, OSError):
            raise ProviderError("Translation connection failed or timed out") from None
        if not translated and not audio:
            raise ProviderError("No translation was received")
        return {"source": "".join(source), "text": "".join(translated), "audio": resample_24_to_16(bytes(audio))}

    async def upload_document(self, name: str, data: bytes) -> tuple[str, str]:
        remote = (await self.request("POST", "files", data={"purpose": "assistants"}, files={"file": (name, data)})).json()["id"]
        vector = self.store.setting("vector_store")
        if not vector:
            vector = (await self.request("POST", "vector_stores", json={"name": "Muse personal documents"})).json()["id"]
            self.store.set_setting("vector_store", vector)
        try:
            await self.request("POST", f"vector_stores/{vector}/files", json={"file_id": remote})
        except BaseException:
            await self.request("DELETE", f"files/{remote}")
            raise
        return remote, vector

    async def close(self):
        await self.http.aclose()


def wav_bytes(pcm: bytes, rate=16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(pcm)
    return out.getvalue()


def resample_24_to_16(pcm: bytes) -> bytes:
    """Linear resampling at the exact 3:2 ratio; input is signed little-endian."""
    import array
    import sys
    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    result = array.array("h")
    for i in range(0, len(samples) - 2, 3):
        result.extend((samples[i], (samples[i + 1] + samples[i + 2]) // 2))
    if sys.byteorder != "little":
        result.byteswap()
    return result.tobytes()


def resample_16_to_24(pcm: bytes) -> bytes:
    import array
    import sys
    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm)//2*2])
    if sys.byteorder != "little": samples.byteswap()
    output = array.array("h")
    for i in range(0, len(samples) - 1, 2):
        a, b = samples[i], samples[i+1]
        c = samples[i+2] if i+2 < len(samples) else b
        output.extend((a, (a+2*b)//3, (2*b+c)//3))
    if sys.byteorder != "little": output.byteswap()
    return output.tobytes()


class LiveSession:
    """One bounded Live session; input and output stay PCM16/16kHz.

    Device half-duplex mode sends real microphone audio only while pressed.
    Silence is supplied while the speaker plays, preventing acoustic feedback.
    Delegations run in the service and remain independent of voice playback.
    """
    def __init__(self, provider: OpenAI, delegate, emit):
        self.provider, self.delegate, self.emit = provider, delegate, emit
        self.ws = None
        self.transcript = ""
        self.spoken = ""
        self.ready = asyncio.Event()
        self.closed = asyncio.Event()
        self.receiver = None
        self.work = set()

    async def start(self):
        settings = self.provider.settings
        try:
            self.ws = await websockets.connect("wss://api.openai.com/v1/live/sessions", additional_headers={"Authorization": f"Bearer {settings.api_key}"}, max_size=2**22, open_timeout=15)
            self.receiver = asyncio.create_task(self.receive())
            await self.send({"type": "session.start", "session": {
                "model": settings.live_model,
                "instructions": "You are Muse, a concise personal assistant. Your voice is AI-generated. Delegate every request involving facts, memory, reminders, documents, actions or personal data to the backend. Never claim an action completed until its result confirms it. Ask before assuming missing dates or people. Respond in at most three short sentences; details appear on screen.",
                "audio": {"format": {"type": "audio/pcm", "rate": 16000}, "output": {"voice": settings.voice}},
                "delegation": {"type": "client"},
            }})
            await asyncio.wait_for(self.ready.wait(), 15)
        except Exception:
            await self.close()
            raise ProviderError("Live voice connection failed; recorded voice is still available") from None

    async def send(self, event):
        if self.ws:
            await self.ws.send(json.dumps(event))

    async def audio(self, pcm: bytes):
        await self.send({"type": "session.input_audio.append", "audio": base64.b64encode(pcm).decode()})

    async def run_delegation(self, identity: str, utterance: str):
        try:
            result = await self.delegate(utterance, identity)
            await self.send({"type": "session.commentary.append", "delegation_id": identity, "content": result["text"][:1400]})
        except Exception:
            await self.send({"type": "session.commentary.append", "delegation_id": identity, "content": "The backend could not finish that request. Check the device for its status; do not assume any action succeeded."})

    async def receive(self):
        try:
            async for raw in self.ws:
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "session.started":
                    self.ready.set()
                elif kind == "session.output_audio.delta":
                    await self.emit("audio", base64.b64decode(event["delta"]))
                elif kind == "session.input_transcript.delta":
                    self.transcript = (self.transcript + event["delta"])[-12000:]
                    await self.emit("heard", self.transcript)
                elif kind == "session.output_transcript.delta":
                    self.spoken = (self.spoken + event["delta"])[-12000:]
                    await self.emit("caption", self.spoken)
                elif kind == "session.delegation.created":
                    # The event itself contains no task text. Use captured transcripts.
                    task = asyncio.create_task(self.run_delegation(event["delegation"]["id"], self.transcript))
                    self.work.add(task)
                    task.add_done_callback(self.work.discard)
                elif kind == "error":
                    await self.emit("error", "Live voice reported an error; retry with recorded voice")
                elif kind == "session.closed":
                    self.closed.set()
                    break
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass
        finally:
            self.closed.set()

    async def close(self):
        if self.ws:
            try:
                await self.send({"type": "session.close"})
                await asyncio.wait_for(self.closed.wait(), 5)
            except Exception:
                pass
            await self.ws.close()
        if self.receiver:
            self.receiver.cancel()
            await asyncio.gather(self.receiver, return_exceptions=True)
        # Tasks already dispatched are deliberately not cancelled with speech.

import asyncio
import struct

import httpx
import pytest

from test_companion import core, client
from muse_companion.openai_api import OpenAI, resample_24_to_16, ProviderError


class Chunks(httpx.AsyncByteStream):
    def __init__(self, pcm):
        self.pcm, self.closed = pcm, False

    async def __aiter__(self):
        for at in range(0, len(self.pcm), 97):
            yield self.pcm[at:at+97]

    async def aclose(self):
        self.closed=True


@pytest.mark.asyncio
async def test_speech_stream_keeps_resampling_phase_across_odd_chunks(core):
    settings, store, _, _ = core
    settings.api_key='test-key'
    provider=OpenAI(settings,store)
    pcm=b''.join(struct.pack('<h',i) for i in range(10000))
    stream=Chunks(pcm)
    await provider.http.aclose()
    provider.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=stream)),base_url='https://api.openai.com/v1/')
    output=[chunk async for chunk in provider.speak_stream('Test')]
    assert len(output)>1 and b''.join(output)==resample_24_to_16(pcm)
    assert stream.closed
    await provider.close()


@pytest.mark.asyncio
async def test_speech_stream_closes_upstream_when_consumer_cancels(core):
    settings, store, _, _ = core
    settings.api_key='test-key'
    provider=OpenAI(settings,store)
    stream=Chunks(bytes(10000))
    await provider.http.aclose()
    provider.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=stream)),base_url='https://api.openai.com/v1/')
    output=provider.speak_stream('Test')
    assert await anext(output)
    await output.aclose()
    assert stream.closed
    await provider.close()


def test_websocket_stream_sends_first_audio_before_generation_finishes(client):
    c, _, provider=client
    release=asyncio.Event()
    async def speech(text):
        yield b'\x01\x00'*320
        await release.wait()
        yield b'\x02\x00'*320
    provider.speak_stream=speech
    with c.websocket_connect('/v1/device') as ws:
        assert ws.receive_json()['type']=='hello'
        ws.send_json({'type':'speech.test','generation':7})
        while True:
            packet=ws.receive()
            if packet.get('bytes'):
                assert struct.unpack('<I',packet['bytes'][:4])[0]==7
                break
        ws.send_json({'type':'voice.cancel'})
        c.portal.call(release.set)
        ws.send_json({'type':'ping'})
        while True:
            packet=ws.receive()
            assert not packet.get('bytes'), 'Cancelled generation leaked audio'
            if packet.get('text') and 'pong' in packet['text']:
                break

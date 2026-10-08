"""Speech stream boundary handling and production reply playback transitions."""
import ctypes
import os
from pathlib import Path
import random
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MUSE = ROOT / "components/muse"


class SpeechBytes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        out = Path(cls.tmp.name)
        (out / "pcm.c").write_text('''
#include "muse_tts_pcm.h"
size_t decode(muse_tts_pcm_t *s, const uint8_t *b, size_t n, int16_t *out) {
    return muse_tts_pcm_decode(s, b, n, out);
}
size_t chunk(const char *s, size_t n) { return muse_tts_text_chunk(s, n); }
''')
        subprocess.run([*shlex.split(os.environ.get("CC", "cc")), "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-shared", "-fPIC", "-I", str(MUSE), str(out / "pcm.c"), "-o", str(out / "pcm.so")], check=True)
        cls.lib = ctypes.CDLL(str(out / "pcm.so"))
        cls.lib.decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_int16)]
        cls.lib.decode.restype = ctypes.c_size_t
        cls.lib.chunk.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.chunk.restype = ctypes.c_size_t

    def test_pcm_is_identical_for_even_odd_and_random_http_chunks(self):
        samples = [-32768, -12345, -1, 0, 1, 12345, 32767] * 400
        data = b"".join(n.to_bytes(2, "little", signed=True) for n in samples)
        for sizes in ([1], [3], [1024], [513, 17, 1023], random.Random(17).choices(range(1, 1025), k=20)):
            state = ctypes.create_string_buffer(2)
            pcm = (ctypes.c_int16 * 512)()
            result, offset, i = [], 0, 0
            while offset < len(data):
                block = data[offset:offset + sizes[i % len(sizes)]]
                n = self.lib.decode(state, block, len(block), pcm)
                result.extend(pcm[:n])
                offset += len(block)
                i += 1
            self.assertEqual(result, samples)
            self.assertEqual(state.raw[1], 0)

    def test_truncated_sample_is_reported_as_pending(self):
        state = ctypes.create_string_buffer(2)
        pcm = (ctypes.c_int16 * 2)()
        self.assertEqual(self.lib.decode(state, b"\x34", 1, pcm), 0)
        self.assertEqual(state.raw[1], 1)
        self.assertEqual(self.lib.decode(state, b"\x12", 1, pcm), 1)
        self.assertEqual(pcm[0], 0x1234)

    def test_long_requests_preserve_every_utf8_character(self):
        for text in ("hello world. " * 2000, "你好🌻" * 1800, "a" * 4094 + "🌻" * 1200):
            data = text.encode()
            parts = []
            while data:
                n = self.lib.chunk(data, len(data))
                self.assertTrue(0 < n <= 4095)
                parts.append(data[:n].decode())
                data = data[n:]
            self.assertEqual("".join(parts), text)


class SpeechPlayback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        out = Path(cls.tmp.name)
        source = (MUSE / "muse_chat_session.cpp").read_text()
        constants = source[source.index("#define MIC_RATE"):source.index("/* ---- Voice task")]
        types = source[source.index("enum phase_t"):source.index("/* 10 KB")]
        resample = source[source.index("static void resampler_init("):source.index("/* ---- Turn: dictation")]
        start = source[source.index("static void start_tts("):source.index("static void tts_data(")]
        offset = source.index("static void pace_silently(")
        playback = source[offset:source.index("/* Ends the turn once", offset)]
        code = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#include <deque>
#include "minimp3.h"
#include "muse_chat_priv.h"
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define MUSE_TTS_RATE 24000
#define MUSE_TTS_FRAMES 512
''' + constants + types + r'''
static turn_t s_turn;
static std::atomic<uint32_t> s_gen{1};
static int16_t pcm[2304], pcm16[2312];
static int16_t *s_pcm = pcm, *s_pcm16 = pcm16;
static char texts[MAX_MSGS * TEXT_MAX];
static std::vector<int16_t> output;
static std::deque<int> reads;
static bool speaker = true;
static int starts, cancels, errors;
static uint32_t job;
static int s_out;
enum mark_t { M_TTS, M_MP3, M_AUDIO };
static void mark(mark_t) {}
static void show_reply_start(const msg_t &) {}
static void emit(muse_hatch_ev_t ev, const char *) { if (ev == MUSE_HATCH_EV_SPEECH_ERROR) errors++; }
bool muse_settings_speaker_on() { return speaker; }
uint32_t muse_tts_begin(const char *) { starts++; return ++job; }
void muse_tts_cancel() { cancels++; }
int muse_tts_read(uint32_t id, int16_t *out) {
    assert(id == job);
    if (reads.empty()) return 0;
    int n = reads.front(); reads.pop_front();
    for (int i = 0; i < n; i++) out[i] = 12000;
    return n;
}
static size_t xStreamBufferSpacesAvailable(int) { return OUT_BYTES - output.size() * 2; }
static size_t xStreamBufferSend(int, const void *p, size_t n, int) {
    assert(n <= xStreamBufferSpacesAvailable(0));
    auto samples = static_cast<const int16_t *>(p);
    output.insert(output.end(), samples, samples + n / 2); return n;
}
int mp3dec_decode_frame(mp3dec_t *, const uint8_t *, int, mp3d_sample_t *, mp3dec_frame_info_t *) { assert(false); return 0; }
''' + resample + start + playback + r'''
static void reset() {
    s_turn = {};
    s_turn.tts_msg = -1;
    s_turn.texts = texts;
    s_turn.gen = s_gen.load();
    s_turn.nmsgs = 2;
    for (int i = 0; i < 2; i++) {
        s_turn.msgs[i].tts = TTS_QUEUED;
        s_turn.msgs[i].len = 5;
        strcpy(texts + i * TEXT_MAX, "Hello");
    }
    output.clear(); reads.clear(); starts = cancels = errors = 0; speaker = true;
}
int main() {
    reset(); start_tts(); assert(starts == 1 && !s_turn.silent);
    start_tts(); assert(starts == 1);  // Don't duplicate a request while waiting.
    reads = {480, -1}; decode();
    assert(output.size() == 320 && output[100] == 12000);
    assert(s_turn.msgs[0].pcm_frames == 320 && s_turn.msgs[0].tts == TTS_FINISHED);
    start_tts(); assert(starts == 2 && s_turn.msgs[1].pcm_start == 320);
    reads = {480, -1}; decode(); assert(output.size() == 640);
    reset(); speaker = false; start_tts();
    assert(starts == 0 && s_turn.silent); decode();
    assert(!output.empty()); for (auto n : output) assert(n == 0);
    reset(); start_tts(); reads = {-2}; decode();
    assert(errors == 1 && s_turn.silent && s_turn.msgs[0].tts == TTS_ACTIVE);
    decode(); assert(!output.empty()); // Readable fallback after an HTTP failure.
    reset(); start_tts(); reads = {480, -1};
    output.resize(OUT_BYTES / 2); decode(); assert(reads.size() == 2); // Backpressure.
    output.clear(); decode(); assert(output.size() == 320);
    reset(); start_tts(); reads = {480}; ++s_gen; decode();
    assert(cancels == 1 && output.empty()); // Cancelled audio never reaches I2S.
}
'''
        (out / "speech.cpp").write_text(code)
        cls.binary = out / "speech"
        result = subprocess.run([*shlex.split(os.environ.get("CXX", "c++")), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                 "-I", str(MUSE), "-I", str(ROOT / "components/minimp3/include"),
                                 str(out / "speech.cpp"), "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def test_streaming_resampling_mute_failure_order_backpressure_and_cancel(self):
        subprocess.run([str(self.binary)], check=True)

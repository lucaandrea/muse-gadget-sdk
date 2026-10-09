"""Queued voice keeps its interpretation mode independently of current settings."""
import ctypes
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PocketRecording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        folder = Path(cls.tmp.name)
        (folder / 'recording.c').write_text('''
#include "muse_pocket_recording.h"
bool encode(unsigned char *out, const char *language, size_t size) { return muse_recording_encode(out, language, size); }
int decode(const unsigned char *data, size_t available, size_t total, char *language) { return muse_recording_decode(data, available, total, language); }
''')
        subprocess.run([*shlex.split(os.environ.get('CC','cc')), '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-I', str(ROOT / 'components/muse'), str(folder / 'recording.c'), '-o', str(folder / 'recording.so')], check=True)
        cls.lib = ctypes.CDLL(str(folder / 'recording.so'))
        cls.lib.encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.encode.restype = ctypes.c_bool
        cls.lib.decode.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p]
        cls.lib.decode.restype = ctypes.c_int

    def test_language_round_trip_and_legacy_audio(self):
        for language in (b'', b'es', b'en', b'ja'):
            for size in (8000, 32000, 480000):
                header = ctypes.create_string_buffer(16)
                target = ctypes.create_string_buffer(3)
                self.assertTrue(self.lib.encode(header, language, size))
                self.assertEqual(self.lib.decode(header, 16, size + 16, target), 1)
                self.assertEqual(target.value, language)
        self.assertEqual(self.lib.decode(bytes(16), 16, 32000, target), 0)
        self.assertEqual(target.value, b'')

    def test_truncated_or_inconsistent_metadata_is_rejected(self):
        header = ctypes.create_string_buffer(16)
        target = ctypes.create_string_buffer(3)
        self.lib.encode(header, b'es', 32000)
        self.assertEqual(self.lib.decode(header, 15, 32016, target), -1)
        self.assertEqual(self.lib.decode(header, 16, 32015, target), -1)
        for position in range(7,16):
            changed = bytearray(header.raw); changed[position] ^= 0xff
            self.assertEqual(self.lib.decode(bytes(changed), 16, 32016, target), -1, position)

    def test_bad_language_or_odd_audio_length_cannot_be_saved(self):
        out = ctypes.create_string_buffer(16)
        for language in (b'e', b'es-MX', b'ES', b'12'):
            self.assertFalse(self.lib.encode(out, language, 32000))
        for size in (0, 1, 480002):
            self.assertFalse(self.lib.encode(out, b'es', size))


if __name__ == '__main__':
    unittest.main()

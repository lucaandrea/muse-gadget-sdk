"""Recording names fit the installed SPIFFS format and retain their full IDs."""
import base64
import contextlib
import ctypes
import io
import os
from pathlib import Path
import random
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/muse"))
import pocket


class PocketFilenames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        out = Path(cls.tmp.name)
        (out / "names.c").write_text('''
#include "muse_pocket_filename.h"
bool encode(const char *id, char *out) { return muse_pocket_note_filename(id, out); }
bool decode(const char *name, char *out) { return muse_pocket_note_id(name, out); }
''')
        subprocess.run([*shlex.split(os.environ.get("CC", "cc")), "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-shared", "-fPIC", "-I", str(ROOT / "components/muse"), str(out / "names.c"),
                        "-o", str(out / "names.so")], check=True)
        cls.lib = ctypes.CDLL(str(out / "names.so"))
        for fn in (cls.lib.encode, cls.lib.decode):
            fn.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
            fn.restype = ctypes.c_bool

    def test_all_id_bits_survive_and_atomic_temp_name_fits(self):
        rng = random.Random(91)
        for raw in [bytes(16), bytes([255]) * 16] + [rng.randbytes(16) for _ in range(4096)]:
            name = ctypes.create_string_buffer(27)
            identity = ctypes.create_string_buffer(33)
            self.assertTrue(self.lib.encode(raw.hex().encode(), name))
            expected = base64.urlsafe_b64encode(raw).rstrip(b"=") + b".pcm"
            self.assertEqual(name.value, expected)
            self.assertLessEqual(len(b"/" + name.value + b".tmp\0"), 32)
            self.assertTrue(self.lib.decode(name.value, identity))
            self.assertEqual(identity.value, raw.hex().encode())

    def test_directory_scan_ignores_cache_temp_and_invalid_names(self):
        output = ctypes.create_string_buffer(33)
        for name in (b"reminders.json", b"A" * 22 + b".pcm.tmp", b"A" * 21 + b"B.pcm",
                     b"../" + b"A" * 19 + b".pcm", b"A" * 22 + b".wav", b""):
            self.assertFalse(self.lib.decode(name, output), name)

    def test_invalid_ids_cannot_become_paths(self):
        output = ctypes.create_string_buffer(27)
        for identity in (b"", b"1" * 31, b"1" * 33, b"g" * 32, b"../" + b"a" * 29):
            self.assertFalse(self.lib.encode(identity, output))


class StorageDiagnosticCLI(unittest.TestCase):
    def test_storage_check_runs_before_status(self):
        with patch.object(sys, "argv", ["pocket.py", "--port", "test", "--storage-test"]), \
             patch.object(pocket, "Board") as board, \
             patch.object(pocket, "response", side_effect=['{"ok":true,"total":3000000,"used":4096}', '{"queued":0}']), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            pocket.main()
            self.assertEqual([call.args[0] for call in board.return_value.__enter__.return_value.write_line.call_args_list],
                             ["pocket.storage_test", "pocket.status"])
            self.assertIn('"ok": true', output.getvalue())

    def test_failed_storage_check_exits_nonzero(self):
        with patch.object(sys, "argv", ["pocket.py", "--port", "test", "--storage-test"]), \
             patch.object(pocket, "Board"), \
             patch.object(pocket, "response", return_value='{"ok":false}'), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit) as error:
            pocket.main()
        self.assertEqual(error.exception.code, 1)

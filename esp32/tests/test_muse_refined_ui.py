"""Behavioral tests of the production glyph paginator and turn presentation."""
import ctypes as C
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "components/muse"


class Page(C.Structure):
    _fields_ = [("page", C.c_int), ("pages", C.c_int), ("start", C.c_size_t), ("end", C.c_size_t)]


class View(C.Structure):
    _fields_ = [("turn", C.c_uint32), ("answer", C.c_bool), ("dismissed", C.c_bool),
                ("reading", C.c_bool), ("manual", C.c_bool), ("page", C.c_int)]


MEASURE = C.CFUNCTYPE(C.c_int, C.c_uint32, C.c_void_p)


class RefinedUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        lib = Path(cls.tmp.name) / "reading.so"
        subprocess.run([os.environ.get("CC", "cc"), "-shared", "-fPIC", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-I", str(SRC), str(SRC / "muse_reading.c"), str(SRC / "muse_text.c"),
                        str(SRC / "muse_presentation.c"), "-o", str(lib)], check=True)
        cls.lib = C.CDLL(str(lib))
        cls.lib.muse_reading_page.restype = Page
        cls.lib.muse_reading_page.argtypes = [C.c_char_p, C.c_int, C.c_int, C.c_size_t, C.c_int,
                                            MEASURE, C.c_void_p, C.c_char_p, C.c_size_t]
        cls.lib.muse_presentation_update.argtypes = [C.POINTER(View), C.c_uint32, C.c_bool]
        cls.lib.muse_presentation_update.restype = C.c_bool
        cls.lib.muse_presentation_dismiss.argtypes = [C.POINTER(View)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def page(self, text, width=90, lines=3, byte=0, requested=-1, cap=1024):
        raw = text.encode() if isinstance(text, str) else text
        def width_of(cp, _):
            return 16 if cp > 127 else 12 if chr(cp) in "WM" else 3 if chr(cp) in "il. " else 7
        measure = MEASURE(width_of)
        out = C.create_string_buffer(cap)
        p = self.lib.muse_reading_page(raw, width, lines, byte, requested, measure, None, out, cap)
        return p, out.value.decode()

    def test_actual_width_changes_page_capacity(self):
        wide, _ = self.page("W" * 40)
        narrow, _ = self.page("i" * 40)
        self.assertGreater(wide.pages, narrow.pages)

    def test_long_answer_has_no_lost_words_or_utf8_splits(self):
        text = "One small step. 漫步世界。 Quiet moments matter. " * 180
        first, _ = self.page(text)
        chunks = []
        for i in range(first.pages):
            p, body = self.page(text, requested=i)
            chunks.append(body)
            followed, _ = self.page(text, byte=p.start)
            self.assertEqual(followed.page, i)
        self.assertEqual("".join("".join(chunks).split()), "".join(text.split()))

    def test_manual_page_clamps_and_view_change_keeps_anchor(self):
        text = "A small answer with many words and a wider view. " * 20
        p, _ = self.page(text, requested=4)
        changed, _ = self.page(text, width=150, lines=6, byte=p.start)
        self.assertLessEqual(changed.start, p.start)
        self.assertGreater(changed.end, p.start)
        end, _ = self.page(text, requested=99999)
        self.assertEqual(end.page, end.pages - 1)

    def test_cjk_closing_punctuation_stays_with_text(self):
        p, body = self.page("你好世界。今天很好。", width=64, lines=8)
        self.assertEqual(p.pages, 1)
        self.assertTrue(all(not line.startswith("。") for line in body.splitlines()))

    def test_blank_malformed_and_small_buffers_are_safe(self):
        self.assertEqual(self.page(" \n \n ")[1], "")
        self.assertEqual(self.page("Longword" * 100, cap=2)[1], "")
        # No out-of-bounds read or infinite loop on an incomplete UTF-8 tail.
        raw = b"ab\xe3\x81"
        out = C.create_string_buffer(16)
        measure = MEASURE(lambda cp, ctx: 7)
        p = self.lib.muse_reading_page(raw, 40, 3, 0, -1, measure, None, out, 16)
        self.assertEqual(p.pages, 1)

    def test_reveal_once_retention_dismissal_and_interruption(self):
        v = View()
        update = self.lib.muse_presentation_update
        self.assertFalse(update(C.byref(v), 1, False))
        self.assertTrue(update(C.byref(v), 1, True))
        self.assertFalse(update(C.byref(v), 1, True))
        v.reading = v.manual = True
        self.assertFalse(update(C.byref(v), 1, False))
        self.assertTrue(v.answer)  # completion/reconnect retains the answer
        self.lib.muse_presentation_dismiss(C.byref(v))
        self.assertFalse(update(C.byref(v), 1, True))
        self.assertFalse(update(C.byref(v), 2, False))
        self.assertFalse(v.reading or v.manual or v.answer)
        self.assertTrue(update(C.byref(v), 2, True))


if __name__ == "__main__": unittest.main()

"""Exercise the actual MD4C adapter and pagination, independent of LVGL."""
import ctypes as C
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "components/muse"
CAP = 32768
TICK = chr(96)
FENCE = TICK * 3


class Document(C.Structure):
    _fields_ = [("length", C.c_size_t), ("fallback", C.c_bool),
                ("text", C.c_char * CAP), ("style", C.c_uint8 * CAP),
                ("origin", C.c_uint16 * CAP)]


class Page(C.Structure):
    _fields_ = [("page", C.c_int), ("pages", C.c_int),
                ("start", C.c_size_t), ("end", C.c_size_t)]


MEASURE = C.CFUNCTYPE(C.c_int, C.c_uint32, C.c_uint8, C.c_void_p)


class MarkdownTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        lib = Path(cls.tmp.name) / "markdown.so"
        subprocess.run([os.environ.get("CC", "cc"), "-shared", "-fPIC", "-std=c11",
                        "-Wall", "-Wextra", "-Werror", "-I", str(SRC),
                        *[str(SRC / p) for p in ("muse_markdown.c", "muse_text.c",
                            "vendor/md4c/md4c.c", "vendor/md4c/entity.c")],
                        "-o", str(lib)], check=True)
        cls.lib = C.CDLL(str(lib))
        cls.lib.muse_markdown_parse.argtypes = [C.POINTER(Document), C.c_char_p]
        cls.lib.muse_markdown_parse.restype = C.c_bool
        cls.lib.muse_markdown_page.argtypes = [C.POINTER(Document), C.c_int, C.c_int,
            C.c_size_t, C.c_int, MEASURE, C.c_void_p, C.c_char_p, C.c_void_p, C.c_size_t]
        cls.lib.muse_markdown_page.restype = Page

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def parse(self, source, fallback=False):
        doc = Document()
        raw = source.encode() if isinstance(source, str) else source
        self.assertEqual(self.lib.muse_markdown_parse(C.byref(doc), raw), not fallback)
        self.assertEqual(doc.fallback, fallback)
        doc.text.decode()
        return doc

    def page(self, doc, width=100, lines=3, byte=0, requested=-1):
        out = C.create_string_buffer(1024)
        styles = (C.c_uint8 * 1024)()
        measure = MEASURE(lambda cp, style, ctx: 16 if cp > 127 else
                          4 if cp == 32 else 9 if style & 1 else 7)
        page = self.lib.muse_markdown_page(C.byref(doc), width, lines, byte,
            requested, measure, None, out, styles, len(out))
        return page, out.value.decode(), bytes(styles)[:len(out.value)]

    def test_semantic_styles_and_no_markdown_delimiters(self):
        doc = self.parse("# Heading\n\n**Bold** *italic* ***both*** " + TICK + "code" + TICK +
                         " ~~old~~ [link](https://example.com)")
        self.assertEqual(doc.text.decode(), "Heading\nBold italic both code old link")
        for word, flags in (("Heading", 65), ("Bold", 1), ("italic", 2),
                            ("both", 3), ("code", 4), ("old", 32), ("link", 16)):
            at = doc.text.rindex(word.encode())
            self.assertEqual(doc.style[at] & flags, flags)

    def test_lists_tasks_code_and_quotes(self):
        doc = self.parse("7. seven\n8. eight\n\n- outer\n  - inner\n\n- [x] done\n- [ ] todo\n\n"
                         "> a quote\n\n" + FENCE + "c\n  x = 1;\n  y = 2;\n" + FENCE)
        body = doc.text.decode()
        for expected in ("7. seven", "8. eight", "• outer", "  • inner",
                         "[x] done", "[ ] todo", "a quote", "  x = 1;\n  y = 2;"):
            self.assertIn(expected, body)
        self.assertTrue(doc.style[doc.text.index(b"a quote")] & 8)
        self.assertTrue(doc.style[doc.text.index(b"x =")] & 4)

    def test_entities_escaped_punctuation_and_literal_html(self):
        doc = self.parse(r"&amp; &copy; &#x4e16;&#30028; \*literal\* <b>text</b>")
        self.assertEqual(doc.text.decode(), "& © 世界 *literal* <b>text</b>")

    def test_tables_reflow_as_labelled_cells_and_images_keep_alt(self):
        doc = self.parse("| Name | Value |\n|---|---|\n| Rain | **Yes** |\n\n![cloud](cloud.png)")
        self.assertIn("Name: Rain\nValue: Yes", doc.text.decode())
        self.assertIn("Image: cloud", doc.text.decode())

    def test_long_reply_preserves_all_visible_text_and_source_anchors(self):
        source = ("**A small step** can help. 世界很大。 *Keep going.*\n\n" * 190)[:16000]
        doc = self.parse(source)
        first, _, _ = self.page(doc)
        chunks = []
        for i in range(first.pages):
            page, body, styles = self.page(doc, requested=i)
            chunks.append(body)
            followed, _, _ = self.page(doc, byte=page.start)
            self.assertEqual(followed.page, i)
            self.assertEqual(len(styles), len(body.encode()))
        self.assertEqual("".join("".join(chunks).split()), "".join(doc.text.decode().split()))
        last, _, _ = self.page(doc, requested=100000)
        self.assertEqual(last.page, last.pages - 1)

    def test_style_widths_affect_wrapping_and_voice_offset(self):
        regular = self.parse("W" * 84)
        bold = self.parse("**" + "W" * 84 + "**")
        p1, _, _ = self.page(regular)
        p2, _, _ = self.page(bold)
        self.assertGreater(p2.pages, p1.pages)
        page, _, _ = self.page(bold, byte=45)
        self.assertLessEqual(page.start, 45)
        self.assertGreater(page.end, 45)

    def test_malformed_utf8_and_unfinished_streaming_markdown(self):
        self.parse(b"**streaming *text \xf0\xff\x80 " + TICK.encode() + b"unfinished")
        doc = self.parse(FENCE + "python\n  print('unfinished')\n")
        self.assertIn("print", doc.text.decode())
        self.assertTrue(doc.style[doc.text.index(b"print")] & 4)

    def test_expansion_budget_falls_back_without_losing_reply(self):
        source = "| " + "Heading" * 8 + " | Value |\n|---|---|\n" + "| x | y |\n" * 1600
        self.assertLess(len(source), 16384)
        doc = self.parse(source, fallback=True)
        self.assertEqual(doc.text.decode(), source.rstrip("\n"))

    def test_code_indentation_whitespace_only_lines_and_tiny_width_progress(self):
        doc = self.parse(FENCE + "\n     \n   abcdefghijk\n" + FENCE)
        page, body, _ = self.page(doc, width=1, lines=50)
        self.assertEqual(page.pages, 1)
        self.assertEqual("".join(body.split()), "abcdefghijk")


if __name__ == "__main__":
    unittest.main()

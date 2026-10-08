"""Credentials are literal data and are never included in parser errors."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/muse"))
from tts import read_key
from chat import BoardError


class SpeechProvisioning(unittest.TestCase):
    def parse(self, content):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(content)
            return read_key(path)

    def test_shell_syntax_is_not_expanded(self):
        self.assertEqual(self.parse('OPENAI_API_KEY="sk-test-$HOME-`whoami`-!"\n'),
                         'sk-test-$HOME-`whoami`-!')

    def test_reads_only_openai_key_and_supports_export_and_comments(self):
        self.assertEqual(self.parse('MUSE_TOKEN=ignored\nexport OPENAI_API_KEY="sk-test-key" # comment\n'),
                         'sk-test-key')

    def test_missing_malformed_and_header_injection_are_rejected_without_values(self):
        for value in ('', '"sk-private', '"sk-private bad"', '"sk-private\rheader"', '"' + 'x' * 513 + '"'):
            with self.assertRaises(BoardError) as raised:
                self.parse('OPENAI_API_KEY=' + value)
            self.assertNotIn('sk-private', str(raised.exception))

"""Root .env loading and environment precedence, without reading real keys."""
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


class TestEnvironmentFile(unittest.TestCase):
    def test_config_loads_explicit_root_without_overriding_or_interpolating(self):
        with patch("dotenv.load_dotenv") as load:
            runpy.run_path(str(ROOT / "vdi" / "config.py"))
        load.assert_called_once_with(ROOT / ".env", override=False, interpolate=False)

    def test_file_values_are_literal_and_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("VDI_TEST_KEY='test-${LITERAL}-value'\nVDI_TEST_MODEL=file-model\n",
                            encoding="utf-8")
            with patch.dict(os.environ, {"VDI_TEST_MODEL": "terminal-model"}):
                os.environ.pop("VDI_TEST_KEY", None)
                load_dotenv(path, override=False, interpolate=False)
                self.assertEqual(os.environ["VDI_TEST_KEY"], "test-${LITERAL}-value")
                self.assertEqual(os.environ["VDI_TEST_MODEL"], "terminal-model")
            with patch.dict(os.environ, {"VDI_TEST_MODEL": ""}):
                load_dotenv(path, override=False, interpolate=False)
                self.assertEqual(os.environ["VDI_TEST_MODEL"], "")

    def test_absent_file_does_not_require_a_key(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(load_dotenv(Path(directory) / ".env", override=False, interpolate=False))


if __name__ == "__main__":
    unittest.main()

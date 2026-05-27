import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from darkcodex.cli import Store, config_timeout, darkcodex_api_answer, project_files
from darkcodex import licence


class StoreTests(unittest.TestCase):
    def test_memory_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Store(Path(tmp) / "db.sqlite") as store:
                store.add_memory("lang", "fr", "pref")
                rows = store.search_memories("lang")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["value"], "fr")


class ProjectFilesTests(unittest.TestCase):
    def test_ignores_node_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("print('ok')")
            os.makedirs(root / "node_modules", exist_ok=True)
            (root / "node_modules" / "x.js").write_text("bad")
            files = [p.relative_to(root).as_posix() for p in project_files(root, 10)]
            self.assertIn("a.py", files)
            self.assertNotIn("node_modules/x.js", files)


class ApiProviderTests(unittest.TestCase):
    def test_config_timeout_has_minimum(self):
        self.assertEqual(config_timeout({"api_timeout_seconds": "2"}), 30)
        self.assertEqual(config_timeout({"api_timeout_seconds": "1200"}), 1200)

    def test_darkcodex_api_answer_uses_direct_api(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'

        config = {
            "model": "gemini-2.5-flash",
            "api_key_env": "DARKCODEX_API_KEY",
            "api_timeout_seconds": 1200,
        }
        with mock.patch.dict(os.environ, {"DARKCODEX_API_KEY": "test-key"}):
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
                code, output = darkcodex_api_answer("hello", config)

        self.assertEqual(code, 0)
        self.assertEqual(output, "ok")
        request = urlopen.call_args.args[0]
        self.assertIn("/models/gemini-2.5-flash:generateContent", request.full_url)
        self.assertEqual(request.headers["X-goog-api-key"], "test-key")


class LicenceTests(unittest.TestCase):
    def test_free_limit_blocks_after_twenty_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "data.json"
            with mock.patch("darkcodex.licence.verify_license", return_value=(False, "")):
                for _ in range(licence.FREE_DAILY_LIMIT):
                    allowed, message = licence.consume_request(data_path)
                    self.assertTrue(allowed)
                    self.assertEqual(message, "")
                allowed, message = licence.consume_request(data_path)

        self.assertFalse(allowed)
        self.assertIn("Limite gratuite atteinte (20/20)", message)

    def test_activate_license_saves_pro_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "data.json"
            with mock.patch("darkcodex.licence.verify_license", return_value=(True, "client@example.com")):
                ok, message = licence.activate_license("DARK-ABCD-1234-WXYZ", data_path)
                status = licence.status_text(data_path)

        self.assertTrue(ok)
        self.assertIn("Licence Pro activee", message)
        self.assertIn("PRO illimite", status)


if __name__ == "__main__":
    unittest.main()

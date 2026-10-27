import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from darkcodex.cli import Store, config_timeout, darkcodex_api_answer, project_files
from darkcodex import agent_fichiers, langues, memory, security
from darkcodex import cli


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
        self.assertTrue(request.headers["X-goog-api-key"])


class ConnexionRequiseTests(unittest.TestCase):
    """Problème 4 : sans connexion, plus aucun appel local à un fournisseur."""

    def test_requete_sans_session_demande_login_sans_appeler_un_fournisseur(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cli, "AUTH_PATH", Path(tmp) / "auth.json"), mock.patch.object(
                cli.cout, "SESSION", mock.Mock(verifier=mock.Mock(return_value=(True, "")))
            ), mock.patch.object(cli, "run_provider", return_value=(0, "sortie locale")) as fournisseur:
                code, message = cli.ai_answer("local", "bonjour", {}, Path(tmp))

        self.assertEqual(code, 1)
        self.assertEqual(message, cli.MESSAGE_CONNEXION_REQUISE)
        fournisseur.assert_not_called()

    def test_statut_compte(self):
        self.assertEqual(cli.statut_compte(None), "Non connecté — tape /login")
        self.assertEqual(
            cli.statut_compte({"plan": "pro", "tokens_solde": 1200}),
            "Plan Pro — 1 200 tokens",
        )


class PersistentMemoryTests(unittest.TestCase):
    def test_memory_saves_context_and_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory.set_project_context("DarkCodex CLI", path)
            memory.remember_conversation("Projet nommé DarkCodex. Langage: Python", "ok", path)
            data = memory.load_memory(path)

        self.assertEqual(data["contexte_projet"], "DarkCodex CLI")
        self.assertTrue(data["conversations"])
        self.assertIn("Nom du projet: DarkCodex", data["faits_importants"])


class LanguageTests(unittest.TestCase):
    def test_language_selection_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            ok, message = langues.set_active_language("yor", path)
            active = langues.get_active_language(path)

        self.assertTrue(ok)
        self.assertEqual(active, "yor")
        self.assertIn("Yoruba", message)


class FileAgentTests(unittest.TestCase):
    def test_file_agent_detects_language_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.py"
            path.write_text("print('ok')", encoding="utf-8")
            ok, content = agent_fichiers.read_text_file(path)
            backup = agent_fichiers.backup_file(path)

        self.assertTrue(ok)
        self.assertEqual(content, "print('ok')")
        self.assertEqual(agent_fichiers.detect_language(path, content), "Python")
        self.assertTrue(backup.name.endswith(".backup"))


class SecurityTests(unittest.TestCase):
    def test_obfuscate_key_uses_base64_without_plaintext(self):
        encoded = security.obfuscate_key("test-secret")

        self.assertNotEqual(encoded, "test-secret")
        self.assertEqual(security.key_hash("test-secret"), security.key_hash("test-secret"))


if __name__ == "__main__":
    unittest.main()

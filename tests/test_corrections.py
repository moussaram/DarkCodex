"""Tests des 16 corrections du document « DarkCodex_Corrections_pour_Codex ».

Aucun appel réseau : toutes les réponses IA et tous les appels serveur sont
mockés. Chaque classe couvre une correction (C01 → C15).
"""

import contextlib
import importlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from darkcodex import (
    __version__,
    agent_session,
    agent_tests,
    cli,
    fsutil,
    licence,
    memory,
)

SRC = Path(cli.__file__).parent


def _sans_ansi(texte: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", texte)


# ── C01 : version unique + écritures atomiques ─────────────────

class VersionEtAtomiqueTests(unittest.TestCase):
    def test_version_au_format_semver(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_parser_version_affiche_la_version(self):
        with self.assertRaises(SystemExit) as code:
            with contextlib.redirect_stdout(io.StringIO()) as sortie:
                cli.main(["--version"])
        self.assertEqual(code.exception.code, 0)
        self.assertIn(f"DarkCodex {__version__}", sortie.getvalue())

    def test_banner_et_bienvenue_utilisent_la_version(self):
        self.assertIn(__version__, cli.BANNER)

    def test_atomic_write_text_ecrit_et_nettoie(self):
        with tempfile.TemporaryDirectory() as tmp:
            cible = Path(tmp) / "sous" / "config.json"
            fsutil.atomic_write_text(cible, "première valeur\n")
            fsutil.atomic_write_text(cible, "seconde valeur\n")

            self.assertEqual(cible.read_text(), "seconde valeur\n")
            self.assertFalse((cible.parent / "config.json.tmp").exists())

    def test_save_config_ne_laisse_pas_de_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cli, "CONFIG_PATH", Path(tmp) / "config.json"):
                cli.save_config({"provider": "local"})
            self.assertFalse(list(Path(tmp).glob("*.tmp")))

    def test_licence_save_data_atomique_et_600(self):
        with tempfile.TemporaryDirectory() as tmp:
            chemin = Path(tmp) / "data.json"
            licence.save_data(licence.default_data(), chemin)
            self.assertTrue(chemin.exists())
            self.assertEqual(chemin.stat().st_mode & 0o777, 0o600)
            self.assertFalse(list(Path(tmp).glob("*.tmp")))


# ── C02 : installateur qui vérifie ──────────────────────────────

class InstallateurTests(unittest.TestCase):
    def test_install_sh_execute_handshake_et_doctor(self):
        contenu = (Path(cli.__file__).parents[1] / "install.sh").read_text()
        self.assertIn("python -m darkcodex handshake", contenu)
        self.assertIn("python -m darkcodex doctor", contenu)
        self.assertIn("python-dotenv", contenu)

    def test_gitignore_ignore_env(self):
        contenu = (Path(cli.__file__).parents[1] / ".gitignore").read_text()
        self.assertIn(".env", contenu.splitlines())


# ── C03 : mode sûr par défaut, payant pour autoedit/fullauto ────

class ModeSecuriseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        etat = Path(self.tmp.name)
        for patcher in (
            mock.patch.object(cli, "STATE_DIR", etat),
            mock.patch.object(cli, "AUTH_PATH", etat / "auth.json"),
            mock.patch.object(cli, "HANDSHAKE_PATH", etat / "handshake.json"),
            mock.patch.object(cli, "CONFIG_PATH", etat / "config.json"),
            mock.patch("darkcodex.licence.DATA_PATH", etat / "data.json"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_nouvel_utilisateur_en_suggest(self):
        self.assertEqual(cli.load_mode(), "suggest")

    def test_migration_silencieuse_fullauto_vers_suggest(self):
        cli.AUTH_PATH.write_text(json.dumps({"token": "t", "mode": "fullauto"}))
        self.assertEqual(cli.load_mode(), "suggest")

    def test_est_pro_par_plan_serveur(self):
        self.assertTrue(cli.est_pro({"plan": "pro"}))
        self.assertFalse(cli.est_pro({"plan": "gratuit"}))

    def test_est_pro_par_licence_locale(self):
        licence.save_data({**licence.default_data(), "pro_active": True})
        self.assertTrue(cli.est_pro({}))

    def test_autoedit_refuse_sans_pro(self):
        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            cli.commander_mode("/mode autoedit", cli.Ctx(Path.cwd(), "local", {}, mode="suggest"))
        self.assertIn("réservé aux comptes Pro", _sans_ansi(sortie.getvalue()))


# ── C04 : confirmation dans tous les modes ─────────────────────

class ConfirmationUniverselleTests(unittest.TestCase):
    def _ctx(self, mode, config=None):
        return cli.Ctx(Path.cwd(), "local", config or {}, mode=mode)

    def test_fullauto_demande_avant_deposer_un_fichier(self):
        ctx = self._ctx("fullauto")
        with mock.patch("builtins.input", return_value="n"):
            self.assertFalse(ctx.confirm_edit("Appliquer ?"))

    def test_confirm_always_evite_toute_friction(self):
        ctx = self._ctx("suggest", config={"confirm_always": "true"})
        with mock.patch("builtins.input") as entree:
            self.assertTrue(ctx.confirm_edit("Appliquer ?"))
            self.assertTrue(ctx.confirm_command("Exécuter ?"))
        entree.assert_not_called()

    def test_git_push_confirme_toujours_meme_avec_confirm_always(self):
        ctx = self._ctx("fullauto", config={"confirm_always": True})
        with mock.patch("builtins.input", return_value="n") as entree:
            self.assertFalse(ctx.confirm_always("Pousser ?"))
        entree.assert_called_once()

    def test_session_save_demande_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            store = cli.Store(racine / "db.sqlite")
            self.addCleanup(store.close)
            sid = store.new_session(racine, "local")
            store.add_message(sid, "user", "bonjour")
            ctx = cli.Ctx(racine, "local", {}, store=store, session_id=sid)

            with mock.patch("builtins.input", return_value="n"):
                with contextlib.redirect_stdout(io.StringIO()) as sortie:
                    cli.handle_agent_command("/session save atelier", ctx)
            self.assertIn("Sauvegarde annulée", sortie.getvalue())
            self.assertEqual(list((racine / ".darkcodex" / "sessions").glob("*.json")), [])

            with mock.patch("builtins.input", return_value="o"):
                with contextlib.redirect_stdout(io.StringIO()):
                    cli.handle_agent_command("/session save atelier", ctx)
            self.assertTrue((racine / ".darkcodex" / "sessions" / "atelier.json").exists())


# ── C05 : AGENTS.md ─────────────────────────────────────────────

class AgentsMdTests(unittest.TestCase):
    def test_prompt_contient_les_sections_demandees(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            (racine / "app.py").write_text("x = 1\n")
            ctx = cli.Ctx(racine, "local", {}, mode="suggest")
            with mock.patch.object(ctx, "ask", return_value=(0, "# AGENTS.md\n")) as ask:
                with contextlib.redirect_stdout(io.StringIO()):
                    cli.generer_agents_md(ctx)
            prompt = ask.call_args.args[0]
        for section in (
            "## Architecture", "## Stack technique", "## Structure des dossiers",
            "## Commandes importantes", "## Règles de code", "## Ce qu'il ne faut jamais faire",
        ):
            self.assertIn(section, prompt)
        self.assertIn("60 lignes", prompt)

    def test_agents_md_existant_conserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            (racine / "AGENTS.md").write_text("# existant\n")
            ctx = cli.Ctx(racine, "local", {}, mode="suggest")
            with mock.patch.object(ctx, "ask") as ask:
                with contextlib.redirect_stdout(io.StringIO()) as sortie:
                    code = cli.generer_agents_md(ctx)
            self.assertEqual(code, 0)
            ask.assert_not_called()
            self.assertIn("# existant", sortie.getvalue())
            self.assertEqual((racine / "AGENTS.md").read_text(), "# existant\n")

    def test_chargement_des_regles_ne_crash_pas(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cli.load_agents_rules(Path(tmp)), "")
            (Path(tmp) / "AGENTS.md").write_bytes(b"\xff\xfe illisible utf-8 \xc3")
            self.assertIsInstance(cli.load_agents_rules(Path(tmp)), str)


# ── C06 : sessions par projet + migrations ─────────────────────

class SessionsProjetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.racine = Path(self.tmp.name)
        self.etat = self.racine / "etat"
        self.etat.mkdir()
        patcher = mock.patch.dict(os.environ, {"DARKCODEX_HOME": str(self.etat)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.projet = self.racine / "projet"
        self.projet.mkdir()

    def _ctx(self):
        store = cli.Store(self.racine / "db.sqlite")
        self.addCleanup(store.close)
        sid = store.new_session(self.projet, "local")
        store.add_message(sid, "user", "bonjour")
        store.add_message(sid, "assistant", "salut")
        return cli.Ctx(self.projet, "local", {}, store=store, session_id=sid)

    def test_sessions_ecrites_dans_le_projet(self):
        path = agent_session.sauvegarder("mon atelier", self._ctx())
        self.assertEqual(path.parent, self.projet / ".darkcodex" / "sessions")
        self.assertEqual(path.name, "mon_atelier.json")

    def test_migration_ancien_dossier_global(self):
        ancien = self.etat / "sessions"
        ancien.mkdir()
        (ancien / "session_20240101_101010.json").write_text(
            json.dumps({"nom": "vieille", "messages": []})
        )
        sessions = agent_session.lister(self.projet)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0][0].name, "session_20240101_101010.json")

    def test_migration_ancien_historique_json(self):
        historique = self.etat.parent / ".darkcodex_history.json"
        historique.write_text(
            json.dumps([{"prompt": "salut", "response": "bonjour", "timestamp": "2024-01-01"}])
        )
        sessions = agent_session.lister(self.projet)
        self.assertTrue(any(data.get("nom") == "historique" for _, data in sessions))
        charge = json.loads((self.projet / ".darkcodex" / "sessions" / "historique.json").read_text())
        self.assertEqual(len(charge["messages"]), 2)
        self.assertEqual(charge["messages"][0]["content"], "salut")


# ── C07 : fichiers sensibles et contexte nettoyé ────────────────

class FichiersSensiblesTests(unittest.TestCase):
    def test_is_sensitive_path(self):
        for chemin in (
            ".env", "config/.env.local", "app.pem", "server.key",
            "id_rsa", "secrets/config.yml", "sub/.ssh/id_ed25519",
            "aws/credentials.json", "android/app/keystore", "config/secrets.json",
        ):
            self.assertTrue(cli.is_sensitive_path(chemin), chemin)
        for chemin in ("app.py", "README.md", "src/main.js", "env.py", "keyboard.py"):
            self.assertFalse(cli.is_sensitive_path(chemin), chemin)

    def test_project_files_exclut_les_sensibles(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            (racine / "app.py").write_text("x = 1\n")
            (racine / ".env").write_text("KEY=1\n")
            (racine / "id_rsa").write_text("PRIVATE\n")
            (racine / "cert.pem").write_text("CERT\n")
            sous = racine / "secrets"
            sous.mkdir()
            (sous / "creds.json").write_text("{}\n")

            fichiers = [p.relative_to(racine).as_posix() for p in cli.project_files(racine, 50)]
            self.assertIn("app.py", fichiers)
            for sensible in (".env", "id_rsa", "cert.pem", "secrets/creds.json"):
                self.assertNotIn(sensible, fichiers)

    def test_project_files_ignore_les_liens_symboliques(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as dehors:
            racine = Path(tmp)
            cible = Path(dehors) / "secret_externe.txt"
            cible.write_text("secret\n")
            (racine / "app.py").write_text("x = 1\n")
            try:
                os.symlink(cible, racine / "lien.txt")
            except OSError:
                self.skipTest("symlinks non supportés")
            fichiers = [p.name for p in cli.project_files(racine, 50)]
            self.assertNotIn("lien.txt", fichiers)

    def test_contexte_nettoie_les_cles(self):
        texte = cli._redact('api_key = "AIzaSyA-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"\nDARKCODEX_API_KEY=abc123\n')
        self.assertIn("[REDACTED]", texte)
        self.assertNotIn("AIzaSyA-", texte)
        self.assertNotIn("abc123", texte)

    def test_contexte_inclut_le_contexte_projet(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            chemin_mem = racine / "memory.json"
            memory.set_project_context("Backend FastAPI", chemin_mem)
            self.assertEqual(memory.get_project_context(chemin_mem), "Backend FastAPI")

    def test_build_context_sans_fichiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            (racine / "app.py").write_text("x = 1\n")
            store = cli.Store(racine / "db.sqlite")
            self.addCleanup(store.close)
            with mock.patch("darkcodex.memory.MEMORY_PATH", racine / "memory.json"):
                texte = cli.build_context(
                    racine, store, {"max_context_files": 12}, include_files=False
                )
            self.assertIn(f"Working directory: {racine}", texte)
            self.assertNotIn("Project file snapshot", texte)


# ── C08 : clé API — fichier puis environnement, jamais intégrée ─

class CleApiTests(unittest.TestCase):
    class _Reponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'

    def test_ancienne_cle_integree_plus_utilisee(self):
        # L'import de security ne doit plus être utilisé par le CLI.
        self.assertFalse(hasattr(cli, "security"))
        source = (SRC / "cli.py").read_text(encoding="utf-8")
        self.assertNotIn("get_api_key", source)
        # Plus de lecture silencieuse de GEMINI_API_KEY (le texte d'aide reste).
        self.assertNotIn('os.environ.get("GEMINI_API_KEY")', source)

    def test_fichier_de_cle_prioritaire_sur_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            fichier = Path(tmp) / "api_key"
            fichier.write_text("cle-fichier\n")
            config = {
                "model": "gemini-2.5-flash",
                "api_key_env": "DARKCODEX_API_KEY",
                "api_key_file": str(fichier),
                "api_timeout_seconds": 60,
            }
            with mock.patch.dict(os.environ, {"DARKCODEX_API_KEY": "cle-env"}):
                with mock.patch("urllib.request.urlopen", return_value=self._Reponse()) as urlopen:
                    code, sortie = cli.darkcodex_api_answer("hello", config)
            self.assertEqual(code, 0)
            self.assertEqual(urlopen.call_args.args[0].headers["X-goog-api-key"], "cle-fichier")

    def test_env_used_when_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "model": "gemini-2.5-flash",
                "api_key_env": "DARKCODEX_API_KEY",
                "api_key_file": str(Path(tmp) / "absent"),
                "api_timeout_seconds": 60,
            }
            with mock.patch.dict(os.environ, {"DARKCODEX_API_KEY": "cle-env"}):
                with mock.patch("urllib.request.urlopen", return_value=self._Reponse()) as urlopen:
                    code, _ = cli.darkcodex_api_answer("hello", config)
            self.assertEqual(code, 0)
            self.assertEqual(urlopen.call_args.args[0].headers["X-goog-api-key"], "cle-env")

    def test_absence_de_cle_message_explicite(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "model": "gemini-2.5-flash",
                "api_key_env": "DARKCODEX_CLE_INCONNUE",
                "api_key_file": str(Path(tmp) / "absent"),
            }
            environ = {k: v for k, v in os.environ.items() if k != "DARKCODEX_CLE_INCONNUE"}
            with mock.patch.dict(os.environ, environ, clear=True):
                code, message = cli.darkcodex_api_answer("hello", config)
            self.assertEqual(code, 2)
            self.assertIn("Missing API key", message)


# ── C09 : providers sûrs ────────────────────────────────────────

class ProviderTests(unittest.TestCase):
    def test_provider_local_par_defaut(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"provider": "auto", "api_key_env": "DARKCODEX_CLE_VIDE_991", "api_key_file": str(Path(tmp) / "absent")}
            environ = {k: v for k, v in os.environ.items() if k != "DARKCODEX_CLE_VIDE_991"}
            with mock.patch.dict(os.environ, environ, clear=True), mock.patch(
                "shutil.which", return_value=None
            ):
                self.assertEqual(cli.detect_provider(config), "local")

    def test_env_forces_darkcodex(self):
        config = {"provider": "auto", "api_key_env": "DARKCODEX_API_KEY", "api_key_file": "/absent"}
        with mock.patch.dict(os.environ, {"DARKCODEX_API_KEY": "x"}):
            self.assertEqual(cli.detect_provider(config), "darkcodex")

    def test_gemini_non_choisi_silencieusement(self):
        config = {"provider": "auto", "api_key_env": "DARKCODEX_CLE_VIDE_992", "api_key_file": "/absent"}
        environ = {k: v for k, v in os.environ.items() if k != "DARKCODEX_CLE_VIDE_992"}
        with mock.patch.dict(os.environ, environ, clear=True), mock.patch("shutil.which", return_value=None):
            self.assertEqual(cli.detect_provider(config), "local")

    def test_aucune_reinstallation_via_pip(self):
        # Le CLI ne doit jamais relancer pip de lui-même (C09) : -m pip n'est
        # toléré que dans les commandes explicites /install de agent_terminal.
        for nom in ("agent_tests.py", "cli.py"):
            source = (SRC / nom).read_text(encoding="utf-8")
            self.assertNotIn("-m pip", source, nom)
        terminal = (SRC / "agent_terminal.py").read_text(encoding="utf-8")
        for ligne in terminal.splitlines():
            if "-m pip" in ligne:
                self.assertIn("install", ligne.lower())


# ── C10 : /tests run ────────────────────────────────────────────

class TestsRunRobustesTests(unittest.TestCase):
    def test_verdict_crash_python(self):
        self.assertEqual(
            agent_tests._verifier_resultats("Traceback...\n  File \"a.py\", line 1\nSyntaxError: invalid syntax"),
            "error_launch",
        )

    def test_verdict_zero_test(self):
        self.assertEqual(
            agent_tests._verifier_resultats("collected 0 items\nNO TESTS RAN"), "no_tests"
        )
        self.assertEqual(agent_tests._verifier_resultats("Ran 0 tests in 0.001s"), "no_tests")

    def test_verdict_couverture_faible(self):
        self.assertEqual(
            agent_tests._verifier_resultats("10 passed\nRequired test coverage of 80% not reached. Total coverage: 45.20%"),
            "low_coverage:45",
        )

    def test_verdict_echecs(self):
        self.assertEqual(
            agent_tests._verifier_resultats("2 failed, 1 passed\nFAILED test_a.py::test_x - AssertionError"),
            "failures",
        )

    def test_verdict_ok(self):
        self.assertIsNone(agent_tests._verifier_resultats("5 passed in 0.20s"))

    def test_lancer_ne_analyse_pas_un_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            (racine / "tests").mkdir()
            (racine / "tests" / "test_a.py").write_text("def test_x():\n    assert True\n")
            (racine / "requirements.txt").write_text("pytest\n")
            ctx = cli.Ctx(racine, "local", {"confirm_always": True}, mode="suggest")
            ctx.ask = mock.Mock()
            ctx.note = mock.Mock()
            crash = (1, "Traceback...\nModuleNotFoundError: No module named 'junk'")
            with mock.patch.object(agent_tests, "executer_tests", return_value=crash):
                code = agent_tests.lancer(ctx)
            self.assertEqual(code, 1)
            ctx.ask.assert_not_called()
            messages = " ".join(str(c.args[0]) for c in ctx.note.call_args_list)
            self.assertIn("Erreur d'import", _sans_ansi(messages))

    def test_lancer_zero_test_sans_analyse_ia(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            (racine / "tests").mkdir()
            (racine / "tests" / "test_a.py").write_text("def test_x():\n    assert True\n")
            (racine / "requirements.txt").write_text("pytest\n")
            ctx = cli.Ctx(racine, "local", {"confirm_always": True}, mode="suggest")
            ctx.ask = mock.Mock()
            ctx.note = mock.Mock()
            with mock.patch.object(agent_tests, "executer_tests", return_value=(5, "NO TESTS RAN")):
                code = agent_tests.lancer(ctx)
            self.assertEqual(code, 1)
            ctx.ask.assert_not_called()

    def test_detection_cargo_et_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            (racine / "Cargo.toml").write_text("[package]\nname='x'\n")
            self.assertEqual(agent_tests.detecter_test(racine), "cargo test")
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            (racine / "go.mod").write_text("module x\n")
            self.assertEqual(agent_tests.detecter_test(racine), "go test ./...")

    def test_detecter_test_ne_leve_jamais(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(agent_tests.detecter_test(Path(tmp)))


# ── C11 : /ask, /config, vitesse ────────────────────────────────

class ChatCommandesTests(unittest.TestCase):
    def test_type_writer_rapide(self):
        self.assertEqual(cli.type_writer.__defaults__[1], 0.001)

    def test_config_chat_affiche_et_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            with mock.patch.object(cli, "CONFIG_PATH", racine / "config.json"):
                ctx = cli.Ctx(racine, "local", dict(cli.DEFAULT_CONFIG), mode="suggest")
                with contextlib.redirect_stdout(io.StringIO()) as sortie:
                    cli.configurer_chat("/config", ctx)
                self.assertIn("provider", sortie.getvalue())
                self.assertNotIn('"api_key":', sortie.getvalue())

                with contextlib.redirect_stdout(io.StringIO()) as sortie:
                    cli.configurer_chat("/config confirm_always true", ctx)
                self.assertIn("confirm_always=True", sortie.getvalue())
                self.assertTrue(ctx.config["confirm_always"])

    def test_config_chat_api_key_ne_renvoie_jamais_la_cle(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            with mock.patch.object(cli, "CONFIG_PATH", racine / "config.json"):
                config = {**cli.DEFAULT_CONFIG, "api_key_file": str(racine / "api_key")}
                ctx = cli.Ctx(racine, "local", config, mode="suggest")
                with contextlib.redirect_stdout(io.StringIO()) as sortie:
                    cli.configurer_chat("/config api-key ma-cle-secrete", ctx)
                texte = sortie.getvalue()
                self.assertNotIn("ma-cle-secrete", texte)
                self.assertIn("enregistrée", texte)
                self.assertEqual(
                    (racine / "api_key").read_text().strip(), "ma-cle-secrete"
                )

    def test_chat_ask_appelle_cmd_ask(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            args = Namespace(cwd=str(racine), provider="local")
            with mock.patch.object(cli, "ensure_session", return_value=None), \
                    mock.patch.object(cli, "ecrire_handshake"), \
                    mock.patch.object(cli, "cmd_ask", return_value=0) as ask, \
                    mock.patch.object(cli.langues, "ensure_language"), \
                    mock.patch.object(cli.langues, "has_active_language", return_value=True), \
                    mock.patch.object(cli.langues, "format_startup", return_value=""), \
                    mock.patch.object(cli.memory, "startup_message", return_value=""), \
                    mock.patch.object(cli, "DB_PATH", racine / "db.sqlite"), \
                    mock.patch("darkcodex.licence.DATA_PATH", racine / "data.json"), \
                    mock.patch("builtins.input", side_effect=['/ask "explique ce projet"', "/exit"]), \
                    contextlib.redirect_stdout(io.StringIO()) as sortie:
                code = cli.cmd_chat(args)
            self.assertEqual(code, 0)
            ask.assert_called_once()
            ns = ask.call_args.args[0]
            self.assertEqual(ns.prompt, ["explique ce projet"])
            self.assertTrue(ns.context)

    def test_chat_config_en_ligne(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            args = Namespace(cwd=str(racine), provider="local")
            with mock.patch.object(cli, "ensure_session", return_value=None), \
                    mock.patch.object(cli, "ecrire_handshake"), \
                    mock.patch.object(cli.langues, "ensure_language"), \
                    mock.patch.object(cli.langues, "has_active_language", return_value=True), \
                    mock.patch.object(cli.langues, "format_startup", return_value=""), \
                    mock.patch.object(cli.memory, "startup_message", return_value=""), \
                    mock.patch.object(cli, "DB_PATH", racine / "db.sqlite"), \
                    mock.patch.object(cli, "CONFIG_PATH", racine / "config.json"), \
                    mock.patch("darkcodex.licence.DATA_PATH", racine / "data.json"), \
                    mock.patch("builtins.input", side_effect=["/config", "/exit"]), \
                    contextlib.redirect_stdout(io.StringIO()) as sortie:
                cli.cmd_chat(args)
            self.assertIn("provider", sortie.getvalue())


# ── C12 : mémoire des réponses IA ──────────────────────────────

class MetaReponsesTests(unittest.TestCase):
    def test_meta_json_stocke_avec_le_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = cli.Store(Path(tmp) / "db.sqlite")
            self.addCleanup(store.close)
            sid = store.new_session(Path(tmp), "local")
            meta = json.dumps({"tokens_utilises": 42, "duree_secondes": 1.5})
            store.add_message(sid, "assistant", "réponse", meta)
            lignes = store.messages_session(sid)
            self.assertEqual(json.loads(lignes[0]["meta_json"])["tokens_utilises"], 42)

    def test_migration_ancienne_base_sans_meta(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            chemin = Path(tmp) / "vieille.sqlite"
            db = sqlite3.connect(chemin)
            db.executescript(
                """
                create table messages (
                    id integer primary key autoincrement,
                    session_id integer not null,
                    role text not null,
                    content text not null,
                    created_at text not null
                );
                """
            )
            db.commit()
            db.close()
            store = cli.Store(chemin)
            self.addCleanup(store.close)
            sid = store.new_session(Path(tmp), "local")
            store.add_message(sid, "user", "bonjour")
            self.assertEqual(store.messages_session(sid)[0]["meta_json"], "")

    def test_recent_ai_answers_repond_avec_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            with mock.patch.object(cli, "DB_PATH", racine / "db.sqlite"):
                store = cli.Store()
                sid = store.new_session(racine, "local")
                store.add_message(sid, "assistant", "réponse IA", '{"tokens_utilises": 7}')
                lignes = cli.recent_ai_answers(racine, 5)
                store.close()
            self.assertEqual(len(lignes), 1)
            self.assertEqual(json.loads(lignes[0]["meta_json"])["tokens_utilises"], 7)

    def test_memory_chat_enregistre_une_connaissance(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            store = cli.Store(racine / "db.sqlite")
            self.addCleanup(store.close)
            ctx = cli.Ctx(racine, "local", {}, store=store)
            with contextlib.redirect_stdout(io.StringIO()):
                cli.gerer_memory_chat("/memory style concis", ctx)
            self.assertTrue(store.search_memories("style"))


# ── C13 : handshake ─────────────────────────────────────────────

class HandshakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        etat = Path(self.tmp.name)
        for patcher in (
            mock.patch.object(cli, "STATE_DIR", etat),
            mock.patch.object(cli, "AUTH_PATH", etat / "auth.json"),
            mock.patch.object(cli, "HANDSHAKE_PATH", etat / "handshake.json"),
            mock.patch.object(cli, "CONFIG_PATH", etat / "config.json"),
            mock.patch("darkcodex.licence.DATA_PATH", etat / "data.json"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_ecriture_du_handshake(self):
        donnees = cli.ecrire_handshake({"plan": "pro"})
        self.assertTrue(cli.HANDSHAKE_PATH.exists())
        self.assertTrue(donnees["app_id"].startswith("dc-"))
        self.assertTrue(donnees["pro_active"])
        self.assertEqual(donnees["mode"], "suggest")
        self.assertEqual(cli.HANDSHAKE_PATH.stat().st_mode & 0o777, 0o600)

    def test_handshake_valide(self):
        cli.ecrire_handshake({})
        ok, raison = cli.handshake_valide()
        self.assertTrue(ok)
        self.assertEqual(raison, "validé")

    def test_handshake_absent_invalide(self):
        ok, raison = cli.handshake_valide()
        self.assertFalse(ok)
        self.assertIn("aucun handshake", raison)

    def test_handshake_expire_invalide(self):
        cli.ecrire_handshake({})
        data = json.loads(cli.HANDSHAKE_PATH.read_text())
        data["mis_a_jour"] = "2020-01-01T00:00:00+00:00"
        cli.HANDSHAKE_PATH.write_text(json.dumps(data))
        ok, raison = cli.handshake_valide()
        self.assertFalse(ok)
        self.assertIn("expiré", raison)

    def test_handshake_app_id_faux_invalide(self):
        cli.HANDSHAKE_PATH.write_text(json.dumps({"app_id": "xxx", "mis_a_jour": "2026-01-01T00:00:00+00:00"}))
        ok, raison = cli.handshake_valide()
        self.assertFalse(ok)
        self.assertIn("app_id", raison)

    def test_confirm_interactive_refuse_sans_pro(self):
        cli.ecrire_handshake({})
        cli.HANDSHAKE_PATH.write_text(
            json.dumps({**cli.lire_handshake(), "mode": "fullauto", "pro_active": False})
        )
        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            self.assertFalse(cli.confirm_interactive())
        self.assertIn("réservé aux comptes Pro", sortie.getvalue())

    def test_confirm_interactive_accepte_pro_fullauto(self):
        cli.ecrire_handshake({})
        cli.HANDSHAKE_PATH.write_text(
            json.dumps({**cli.lire_handshake(), "mode": "fullauto", "pro_active": True})
        )
        self.assertTrue(cli.confirm_interactive())

    def test_doctor_affiche_le_handshake(self):
        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            cli.cmd_doctor(Namespace())
        self.assertIn("handshake:", sortie.getvalue())
        self.assertIn("Mode:", sortie.getvalue())

    def test_sous_commande_handshake(self):
        parser = cli.build_parser()
        args = parser.parse_args(["handshake"])
        self.assertEqual(args.func, cli.cmd_handshake)


# ── C14 : message après paiement ────────────────────────────────

class PaiementTests(unittest.TestCase):
    def test_message_apres_paiement_dans_le_cli(self):
        source = (SRC / "cli.py").read_text(encoding="utf-8")
        self.assertIn("Après confirmation du paiement", source)

    def test_message_apres_paiement_dans_le_readme(self):
        contenu = (Path(cli.__file__).parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn("confirmation du paiement par Moov Money", contenu)


# ── C15 : .env maîtrisé ─────────────────────────────────────────

class DotenvTests(unittest.TestCase):
    def test_licence_ne_charge_dotenv_sans_flag(self):
        faux = mock.MagicMock()
        environ = {k: v for k, v in os.environ.items() if k != "DARKCODEX_LOAD_DOTENV"}
        with mock.patch.dict(sys.modules, {"dotenv": faux}), mock.patch.dict(
            os.environ, environ, clear=True
        ):
            importlib.reload(licence)
        faux.load_dotenv.assert_not_called()
        importlib.reload(licence)

    def test_licence_charge_dotenv_avec_flag(self):
        faux = mock.MagicMock()
        environ = {**os.environ, "DARKCODEX_LOAD_DOTENV": "1"}
        with mock.patch.dict(sys.modules, {"dotenv": faux}), mock.patch.dict(
            os.environ, environ, clear=True
        ):
            importlib.reload(licence)
        faux.load_dotenv.assert_called_once()
        importlib.reload(licence)

    def test_ensure_dotenv_ne_crash_pas_sans_dotenv(self):
        cli._DOTENV_CHARGE = False
        try:
            with mock.patch.dict(os.environ, {"DARKCODEX_LOAD_DOTENV": "1"}), mock.patch.dict(
                sys.modules, {"dotenv": None}
            ):
                cli.ensure_dotenv()
        finally:
            cli._DOTENV_CHARGE = False

    def test_ensure_dotenv_inactif_par_defaut(self):
        cli._DOTENV_CHARGE = False
        environ = {k: v for k, v in os.environ.items() if k != "DARKCODEX_LOAD_DOTENV"}
        with mock.patch.dict(os.environ, environ, clear=True), mock.patch.dict(
            sys.modules, {"dotenv": None}
        ):
            cli.ensure_dotenv()

    def test_env_example_sans_vraie_cle(self):
        contenu = (Path(cli.__file__).parents[1] / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn("AIza", contenu)
        for ligne in contenu.splitlines():
            if "=" in ligne and not ligne.strip().startswith("#"):
                _, valeur = ligne.split("=", 1)
                self.assertEqual(valeur.strip(), "", ligne)

    def test_requirements_ne_contient_que_dotenv(self):
        contenu = (Path(cli.__file__).parents[1] / "requirements.txt").read_text(encoding="utf-8")
        self.assertEqual([l for l in contenu.splitlines() if l.strip()], ["python-dotenv"])


if __name__ == "__main__":
    unittest.main()

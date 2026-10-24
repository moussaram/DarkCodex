"""Tests de la transformation en agent IA (document DarkCodex_Agent_Complet).

Aucun appel réseau : chaque test pousse un faux contexte (FakeCtx) dont les
réponses de l'IA et les confirmations sont pilotées par le test.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from darkcodex import (
    agent_debug,
    agent_fichiers,
    agent_git,
    agent_session,
    agent_terminal,
    agent_tests,
    cli,
)

GIT_DISPO = shutil.which("git") is not None
# C10 : detecter_test préfère python3 quand il existe.
PREFIXE_PY = "python3" if shutil.which("python3") else "python"


class FakeCtx:
    """Double de cli.Ctx : réponse IA et confirmations pilotées par le test."""

    def __init__(self, cwd: Path, mode: str = "suggest", reponses=None, confirmation=True):
        self.cwd = Path(cwd)
        self.mode = mode
        self.provider = "local"
        self.config = {}
        self.store = None
        self.session_id = None
        self.reponses = list(reponses or [])
        self.derniere_reponse = (0, "")
        self.confirmation = confirmation
        self.confirmations: list[bool] = []
        self.demandes: list[tuple[str, str]] = []
        self.prompts: list[str] = []
        self.notes: list[str] = []
        self.commandes: list[tuple[str, int]] = []
        self.modifications: list[tuple[str, str]] = []

    def ask(self, prompt: str):
        self.prompts.append(prompt)
        if self.reponses:
            self.derniere_reponse = self.reponses.pop(0)
        return self.derniere_reponse

    def rules(self) -> str:
        return ""

    def note(self, texte: str = "") -> None:
        self.notes.append(str(texte))

    def _confirme(self, type_: str, question: str) -> bool:
        self.demandes.append((type_, question))
        if self.confirmations:
            return self.confirmations.pop(0)
        return bool(self.confirmation)

    def confirm_edit(self, question: str) -> bool:
        if self.mode in {"autoedit", "fullauto"}:
            return True
        return self._confirme("edit", question)

    def confirm_command(self, question: str) -> bool:
        if self.mode == "fullauto":
            return True
        return self._confirme("command", question)

    def confirm_always(self, question: str) -> bool:
        return self._confirme("always", question)

    def note_modification(self, path, action: str) -> None:
        self.modifications.append((str(path), action))

    def note_commande(self, commande: str, exit_code: int, sortie: str = "") -> None:
        self.commandes.append((commande, exit_code))


class ModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        state = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(cli, "STATE_DIR", state),
            mock.patch.object(cli, "AUTH_PATH", state / "auth.json"),
            mock.patch.object(cli, "HANDSHAKE_PATH", state / "handshake.json"),
            mock.patch.object(cli, "CONFIG_PATH", state / "config.json"),
            mock.patch("darkcodex.licence.DATA_PATH", state / "data.json"),
        ]
        for patcher in self.patches:
            patcher.start()
        self.auth = state / "auth.json"

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def test_mode_par_defaut_suggest(self):
        self.assertEqual(cli.load_mode(), "suggest")

    def test_mode_invalide_retombe_sur_suggest(self):
        self.auth.write_text(json.dumps({"mode": "n importe quoi"}))
        self.assertEqual(cli.load_mode(), "suggest")

    def test_sauvegarde_du_mode_dans_auth_json(self):
        # C03 : fullauto n'est stockable que pour un compte Pro.
        with mock.patch.object(cli, "est_pro", return_value=True):
            cli.save_mode("fullauto")
            self.assertEqual(cli.load_mode(), "fullauto")
        self.assertEqual(json.loads(self.auth.read_text())["mode"], "fullauto")

    def test_mode_payant_ramene_a_suggest_sans_pro(self):
        # C03 : migration silencieuse autoedit/fullauto → suggest pour un gratuit.
        cli.save_mode("fullauto")
        self.assertEqual(cli.load_mode(), "suggest")

    def test_ajout_du_mode_ne_casse_pas_le_token(self):
        cli.save_auth({"token": "jwt-demo", "nom": "Moussa", "tokens_solde": 5000})
        with mock.patch.object(cli, "est_pro", return_value=True):
            cli.save_mode("autoedit")
            session = cli.load_auth()

            self.assertEqual(session["token"], "jwt-demo")
            self.assertEqual(cli.load_mode(), "autoedit")

            # Le flux de login réécrit auth.json : le mode doit survivre.
            cli.save_auth({"token": "jwt-demo", "nom": "Moussa", "tokens_solde": 4850})
            self.assertEqual(cli.load_mode(), "autoedit")
        self.assertEqual(cli.load_auth()["tokens_solde"], 4850)

    @staticmethod
    def _sans_ansi(texte: str) -> str:
        import re

        return re.sub(r"\x1b\[[0-9;]*m", "", texte)

    def test_commande_mode_affiche_et_change(self):
        cli.save_auth({"token": "jwt-demo"})
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            cli.commander_mode("/mode", cli.Ctx(Path.cwd(), "local", {}, mode="suggest"))
        affiche = self._sans_ansi(sortie.getvalue())
        self.assertIn("Mode actuel : SUGGEST", affiche)
        self.assertIn("← actif", affiche)

        # C03 + C13 : fullauto = Pro + handshake valide.
        with mock.patch.object(cli, "est_pro", return_value=True), mock.patch.object(
            cli, "handshake_valide", return_value=(True, "validé")
        ):
            sortie = io.StringIO()
            with contextlib.redirect_stdout(sortie):
                cli.commander_mode(
                    "/mode fullauto", cli.Ctx(Path.cwd(), "local", {}, mode="suggest")
                )
            self.assertIn("autonomie totale", self._sans_ansi(sortie.getvalue()))
            self.assertEqual(cli.load_mode(), "fullauto")

        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            cli.commander_mode("/mode bidon", cli.Ctx(Path.cwd(), "local", {}, mode="suggest"))
        self.assertIn("Mode inconnu", self._sans_ansi(sortie.getvalue()))

    def test_mode_payant_refuse_sans_pro(self):
        # C03 : un utilisateur gratuit ne peut pas activer autoedit ni fullauto.
        cli.save_auth({"token": "jwt-demo"})
        for choix in ("autoedit", "fullauto"):
            sortie = io.StringIO()
            with contextlib.redirect_stdout(sortie):
                cli.commander_mode(
                    f"/mode {choix}", cli.Ctx(Path.cwd(), "local", {}, mode="suggest")
                )
            self.assertIn("réservé aux comptes Pro", self._sans_ansi(sortie.getvalue()))
        self.assertEqual(cli.load_mode(), "suggest")

    def test_mode_fullauto_refuse_sans_handshake(self):
        # C13 : Pro mais aucun handshake → fullauto refusé.
        cli.save_auth({"token": "jwt-demo"})
        with mock.patch.object(cli, "est_pro", return_value=True), mock.patch.object(
            cli, "handshake_valide", return_value=(False, "aucun handshake")
        ):
            sortie = io.StringIO()
            with contextlib.redirect_stdout(sortie):
                cli.commander_mode(
                    "/mode fullauto", cli.Ctx(Path.cwd(), "local", {}, mode="suggest")
                )
        self.assertIn("Handshake requis", self._sans_ansi(sortie.getvalue()))
        self.assertEqual(cli.load_mode(), "suggest")


class CtxConfirmationTests(unittest.TestCase):
    """C04 : toute action demande confirmation, sauf confirm_always=True (Pro)."""

    def _ctx(self, mode: str, config: dict | None = None) -> cli.Ctx:
        return cli.Ctx(Path.cwd(), "local", config or {}, mode=mode)

    def test_suggest_demande_tout(self):
        ctx = self._ctx("suggest")
        with mock.patch("builtins.input", return_value="o") as entree:
            self.assertTrue(ctx.confirm_edit("Voulez-vous appliquer cette modification ?"))
            self.assertTrue(ctx.confirm_command("Exécuter cette commande ?"))
        self.assertEqual(entree.call_count, 2)

    def test_suggest_refuse_si_reponse_non(self):
        ctx = self._ctx("suggest")
        with mock.patch("builtins.input", return_value="n"):
            self.assertFalse(ctx.confirm_edit("Voulez-vous appliquer cette modification ?"))

    def test_autoedit_demande_les_fichiers(self):
        ctx = self._ctx("autoedit")
        with mock.patch("builtins.input", return_value="o") as entree:
            self.assertTrue(ctx.confirm_edit("Voulez-vous appliquer cette modification ?"))
        entree.assert_called_once()

    def test_fullauto_demande_les_commandes(self):
        ctx = self._ctx("fullauto")
        with mock.patch("builtins.input", return_value="o") as entree:
            self.assertTrue(ctx.confirm_edit("x"))
            self.assertTrue(ctx.confirm_command("y"))
        self.assertEqual(entree.call_count, 2)

    def test_fullauto_refuse_si_reponse_non(self):
        ctx = self._ctx("fullauto")
        with mock.patch("builtins.input", return_value="n"):
            self.assertFalse(ctx.confirm_command("Exécuter ?"))

    def test_confirm_always_config_supprime_la_friction(self):
        ctx = self._ctx("fullauto", config={"confirm_always": True})
        with mock.patch("builtins.input") as entree:
            self.assertTrue(ctx.confirm_edit("x"))
            self.assertTrue(ctx.confirm_command("y"))
        entree.assert_not_called()

    def test_confirm_always_demande_toujours(self):
        ctx = self._ctx("fullauto")
        with mock.patch("builtins.input", return_value="o") as entree:
            self.assertTrue(ctx.confirm_always("Pousser les commits ?"))
        entree.assert_called_once()


class AgentFichiersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "monprojet"
        self.root.mkdir()
        (self.root / "requirements.txt").write_text("fastapi\nuvicorn\n")
        (self.root / ".env.example").write_text("SUPABASE_URL=https://x.supabase.co\n")
        (self.root / "main.py").write_text("def index():\n    return 1\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_index.py").write_text("def test_index():\n    assert True\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_project_detecte_langage_framework_entree(self):
        info = agent_fichiers.scan_project(self.root)

        self.assertEqual(info["langage"], "Python")
        self.assertEqual(info["framework"], "FastAPI")
        self.assertEqual(info["entree"], "main.py")
        self.assertEqual(info["dependances"], "requirements.txt")
        self.assertEqual(info["base"], "Supabase")

    def test_resume_projet_affiche_les_emojis_attendus(self):
        texte = agent_fichiers.resume_projet(agent_fichiers.scan_project(self.root))
        for marqueur in ("📁", "🔧 Langage : Python", "📦 Framework : FastAPI", "📌 Entrée : main.py"):
            self.assertIn(marqueur, texte)

    def test_arborescence_liste_les_fichiers(self):
        lignes = agent_fichiers.arborescence(self.root)
        self.assertTrue(any("main.py [Python]" in ligne for ligne in lignes))
        self.assertTrue(any("tests/" in ligne for ligne in lignes))

    def test_protection_des_fichiers_sensibles(self):
        self.assertTrue(agent_fichiers.protected_path(Path("/tmp/.env")))
        self.assertTrue(agent_fichiers.protected_path(Path("/tmp/.env.local")))
        self.assertTrue(agent_fichiers.protected_path(Path("/tmp/auth.json")))
        self.assertFalse(agent_fichiers.protected_path(Path("/tmp/main.py")))

    def test_diff_coloré(self):
        diff = agent_fichiers.render_diff("a\nb\n", "a\nc\n", "x.py")
        self.assertIn("\033[31m-b", diff)
        self.assertIn("\033[32m+c", diff)
        self.assertIn("aucun" if False else "Aucun changement", agent_fichiers.render_diff("a", "a"))

    def test_appliquer_fichier_avec_sauvegarde(self):
        cible = self.root / "main.py"
        ctx = FakeCtx(self.root, mode="autoedit")

        applique = agent_fichiers.appliquer_fichier(cible, "print('nouveau')\n", ctx)

        self.assertTrue(applique)
        self.assertEqual(cible.read_text(), "print('nouveau')\n")
        self.assertTrue((self.root / "main.py.backup").exists())
        self.assertEqual(ctx.modifications, [(str(cible), "modification")])
        self.assertTrue(any("Modification appliquée" in n for n in ctx.notes))

    def test_appliquer_fichier_refuse_en_suggest_sans_validation(self):
        cible = self.root / "main.py"
        ctx = FakeCtx(self.root, mode="suggest", confirmation=False)

        applique = agent_fichiers.appliquer_fichier(cible, "print('nouveau')\n", ctx)

        self.assertFalse(applique)
        self.assertEqual(cible.read_text(), "def index():\n    return 1\n")

    def test_fichier_protege_exige_une_confirmation_explicite(self):
        cible = self.root / ".env"
        cible.write_text("SECRET=1\n")
        ctx = FakeCtx(self.root, mode="autoedit", confirmation=False)

        applique = agent_fichiers.appliquer_fichier(cible, "SECRET=2\n", ctx)

        self.assertFalse(applique)
        self.assertEqual(cible.read_text(), "SECRET=1\n")
        self.assertEqual(ctx.demandes[0][0], "always")

    def test_extraire_json_dans_un_bloc_de_code(self):
        texte = 'Voici le résultat :\n```json\n{"fichiers": [{"chemin": "a.py"}]}\n```'
        self.assertEqual(
            agent_fichiers.extraire_json(texte),
            {"fichiers": [{"chemin": "a.py"}]},
        )
        self.assertIsNone(agent_fichiers.extraire_json("pas de json ici"))


class AgentTerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "package.json").write_text(
            json.dumps({"scripts": {"build": "tsc", "dev": "vite", "test": "jest"}})
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_commandes_dangereuses_refusees(self):
        for commande in ("rm -rf /", "git push --force", "drop table users", "mkfs.ext4"):
            self.assertTrue(agent_terminal.commande_interdite(commande), commande)
        self.assertEqual(agent_terminal.commande_interdite("python -m pytest -q"), "")

    def test_execution_refusee_ne_lance_rien(self):
        ctx = FakeCtx(self.root, mode="fullauto")
        code = agent_terminal.executer("rm -rf /", ctx)

        self.assertEqual(code, 1)
        self.assertTrue(any("refusée" in n for n in ctx.notes))
        self.assertEqual(ctx.demandes, [])
        self.assertEqual(ctx.commandes, [])

    def test_execution_annulee_sans_confirmation(self):
        ctx = FakeCtx(self.root, mode="suggest", confirmation=False)
        code = agent_terminal.executer("echo bonjour", ctx)

        self.assertEqual(code, 1)
        self.assertTrue(any("annulée" in n for n in ctx.notes))

    def test_execution_reussie_est_journalisee(self):
        ctx = FakeCtx(self.root, mode="fullauto")
        code = agent_terminal.executer("echo darkcodex", ctx)

        self.assertEqual(code, 0)
        self.assertTrue(any("darkcodex" in n for n in ctx.notes))
        self.assertEqual(ctx.commandes, [("echo darkcodex", 0)])

    def test_sortie_affichee_en_temps_reel(self):
        # Partie 3 : chaque ligne est affichée au fil de l'eau.
        ctx = FakeCtx(self.root, mode="fullauto")
        code = agent_terminal.executer("for i in 1 2 3; do echo ligne$i; done", ctx)

        self.assertEqual(code, 0)
        self.assertIn("ligne1", ctx.notes)
        self.assertIn("ligne2", ctx.notes)
        self.assertIn("ligne3", ctx.notes)

    def test_timeout_tue_la_commande_et_conserve_la_sortie(self):
        ctx = FakeCtx(self.root, mode="fullauto")
        with mock.patch.object(agent_terminal, "TIMEOUT_SECONDS", 1):
            debut = time.monotonic()
            code = agent_terminal.executer("sleep 5; echo jamais", ctx, corriger=False)
            duree = time.monotonic() - debut

        self.assertEqual(code, 124)
        self.assertLess(duree, 4.0)
        self.assertTrue(any("Timeout" in n for n in ctx.notes))
        # La commande est tuée avant d'atteindre « echo jamais »
        # (la ligne de commande elle-même contient bien le mot).
        self.assertFalse(any(n.strip() == "jamais" for n in ctx.notes))
        self.assertEqual(ctx.commandes[0][1], 124)

    def test_mode_autoedit_affiche_autoriser(self):
        self.assertEqual(agent_terminal.question_commande("autoedit"), "Autoriser ?")
        self.assertEqual(agent_terminal.question_commande("suggest"), "Exécuter cette commande ?")

    def test_detection_des_commandes_du_projet(self):
        self.assertEqual(agent_terminal.detecter_build(self.root), "npm run build")
        self.assertEqual(agent_terminal.detecter_start(self.root), "npm run dev")
        self.assertEqual(agent_terminal.detecter_install(self.root), "npm install")
        self.assertEqual(agent_terminal.detecter_lint(self.root), "")

    def test_analyse_d_erreur_parse_le_json(self):
        ctx = FakeCtx(
            self.root,
            mode="fullauto",
            reponses=[(0, '{"analyse": "mauvais argument", "commande_corrigee": "echo corrigé"}')],
        )
        infos = agent_terminal.analyser_erreur("echo ", "erreur", ctx)

        self.assertEqual(infos["analyse"], "mauvais argument")
        self.assertEqual(infos["commande_corrigee"], "echo corrigé")

    def test_correction_de_commande_limitee_a_trois(self):
        ctx = FakeCtx(
            self.root,
            mode="fullauto",
            reponses=[
                (0, '{"analyse": "toujours faux", "commande_corrigee": "false"}'),
                (0, '{"analyse": "toujours faux", "commande_corrigee": "false"}'),
                (0, '{"analyse": "toujours faux", "commande_corrigee": "false"}'),
            ],
        )
        code = agent_terminal.executer("false", ctx)

        self.assertEqual(code, 1)
        self.assertLessEqual(len([c for c, _ in ctx.commandes]), 4)  # 1 essai + 3 corrections

    def test_lancer_action_inconnue(self):
        ctx = FakeCtx(self.root, mode="suggest")
        self.assertEqual(agent_terminal.lancer("inconnu", ctx), 1)
        self.assertTrue(any("Action inconnue" in n for n in ctx.notes))
        self.assertEqual(ctx.commandes, [])  # rien n'est exécuté


class AgentGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_operations_destructrices_refusees(self):
        self.assertTrue(agent_git._refuse(["push", "--force"]))
        self.assertTrue(agent_git._refuse(["reset", "--hard", "HEAD"]))
        self.assertTrue(agent_git._refuse(["checkout", "-f"]))
        self.assertEqual(agent_git._refuse(["push", "-u", "origin", "main"]), "")

    def test_push_demande_confirmation_meme_en_fullauto(self):
        ctx = FakeCtx(self.root, mode="fullauto", confirmation=False)
        code = agent_git.executer(["push"], ctx)

        self.assertEqual(code, 1)
        self.assertEqual(ctx.demandes[0][0], "always")
        self.assertTrue(any("annulé" in n for n in ctx.notes))

    def test_usage_quand_sous_commande_inconnue(self):
        ctx = FakeCtx(self.root, mode="suggest")
        self.assertEqual(agent_git.executer(["inconnu"], ctx), 1)
        self.assertTrue(any("/git commit" in n for n in ctx.notes))

    @unittest.skipUnless(GIT_DISPO, "git non installé")
    def test_git_hors_depot_retourne_un_message_clair(self):
        # Dossier sans .git : pas de dump des centaines de lignes d'aide de git.
        ctx = FakeCtx(self.root, mode="fullauto")
        for action in ("status", "diff"):
            with self.subTest(action=action):
                code = agent_git.executer([action], ctx)
                self.assertEqual(code, 128)
                self.assertTrue(any("n'est pas un dépôt Git" in n for n in ctx.notes))
                self.assertFalse(any("fatal:" in n for n in ctx.notes))
                self.assertFalse(any("usage: git" in n for n in ctx.notes))

    @unittest.skipUnless(GIT_DISPO, "git non installé")
    def test_commit_sur_un_vrai_depôt(self):
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.root, check=True)
        (self.root / "a.txt").write_text("contenu\n")

        ctx = FakeCtx(self.root, mode="fullauto", reponses=[(0, "ajoute le fichier a")])
        code = agent_git.executer(["commit"], ctx)

        self.assertEqual(code, 0)
        self.assertEqual(ctx.demandes, [])  # FULL AUTO ne demande rien
        self.assertTrue(any("message proposé" in n.lower() for n in ctx.notes))
        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=self.root, capture_output=True, text=True
        ).stdout
        self.assertIn("ajoute le fichier a", log)

    @unittest.skipUnless(GIT_DISPO, "git non installé")
    def test_commit_annule_en_mode_suggest(self):
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.root, check=True)
        (self.root / "a.txt").write_text("contenu\n")

        ctx = FakeCtx(self.root, mode="suggest", confirmation=False)
        code = agent_git.executer(["commit", "ajoute a"], ctx)

        self.assertEqual(code, 1)
        self.assertEqual(ctx.demandes[0][0], "always")
        self.assertTrue(any("annulé" in n for n in ctx.notes))
        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=self.root, capture_output=True, text=True
        ).stdout
        self.assertNotIn("ajoute a", log)

    @unittest.skipUnless(GIT_DISPO, "git non installé")
    def test_commit_ignore_les_fichiers_artefacts(self):
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.root, check=True)
        (self.root / "app.py").write_text("x = 1\n")
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "app.pyc").write_text("binaire")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "lib.js").write_text("js")

        ctx = FakeCtx(self.root, mode="fullauto")
        code = agent_git.executer(["commit", "ajoute app"], ctx)

        self.assertEqual(code, 0)
        fichiers = subprocess.run(
            ["git", "ls-files"], cwd=self.root, capture_output=True, text=True
        ).stdout.split()
        self.assertIn("app.py", fichiers)
        self.assertNotIn("__pycache__/app.pyc", fichiers)
        self.assertNotIn("node_modules/lib.js", fichiers)


class AgentTestsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_dummy.py").write_text("def test_ok():\n    assert True\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_detection_pytest(self):
        (self.root / "requirements.txt").write_text("pytest\n")
        self.assertEqual(agent_tests.framework(self.root), "pytest")
        self.assertEqual(
            agent_tests.detecter_test(self.root), f"{PREFIXE_PY} -m pytest tests/ -v"
        )

    def test_detection_unittest(self):
        self.assertEqual(agent_tests.framework(self.root), "unittest")
        self.assertEqual(
            agent_tests.detecter_test(self.root),
            f"{PREFIXE_PY} -m unittest discover -s tests -v",
        )

    def test_detection_unittest_sans_dossier_tests(self):
        # test_*.py à la racine : pas de dossier tests/, sinon unittest casse.
        with tempfile.TemporaryDirectory() as autre:
            racine = Path(autre)
            (racine / "test_racine.py").write_text("import unittest\n")
            self.assertEqual(agent_tests.framework(racine), "unittest")
            self.assertEqual(
                agent_tests.detecter_test(racine), f"{PREFIXE_PY} -m unittest discover -v"
            )

    def test_detection_flutter_et_npm(self):
        with tempfile.TemporaryDirectory() as autre:
            racine = Path(autre)
            (racine / "pubspec.yaml").write_text("name: app\n")
            self.assertEqual(agent_tests.framework(racine), "flutter")
            self.assertEqual(agent_tests.detecter_test(racine), "flutter test")

            (racine / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
            (racine / "pubspec.yaml").unlink()
            self.assertEqual(agent_tests.framework(racine), "jest")
            self.assertEqual(agent_tests.detecter_test(racine), "npm test")

    def test_resume_pytest(self):
        resume = agent_tests.resume_tests("===== 3 passed, 1 failed in 0.5s =====")
        self.assertIn("3 test(s) réussi(s)", resume)
        self.assertIn("1 échoué", resume)

    def test_generation_de_tests(self):
        cible = self.root / "calcul.py"
        cible.write_text("def add(a, b):\n    return a + b\n")
        ctx = FakeCtx(
            self.root,
            mode="autoedit",
            reponses=[(0, "def test_add():\n    assert add(1, 2) == 3\n")],
        )

        code = agent_tests.ecrire(ctx, cible)

        self.assertEqual(code, 0)
        genere = self.root / "tests" / "test_calcul.py"
        self.assertTrue(genere.exists())
        self.assertIn("def test_add", genere.read_text())
        self.assertTrue(any("autoedit" in p or "unittest" in p for p in ctx.prompts) or ctx.prompts)

    def test_lancer_sans_framework(self):
        with tempfile.TemporaryDirectory() as autre:
            ctx = FakeCtx(Path(autre), mode="suggest")
            self.assertEqual(agent_tests.lancer(ctx), 1)
            self.assertTrue(any("Aucun framework" in n for n in ctx.notes))

    def test_lancer_tests_reussis_avec_sortie_simulee(self):
        ctx = FakeCtx(self.root, mode="fullauto")
        with mock.patch.object(
            agent_tests, "executer_tests", return_value=(0, "OK (3 tests)")
        ):
            code = agent_tests.lancer(ctx)
        self.assertEqual(code, 0)
        self.assertTrue(any("Tests passés" in n for n in ctx.notes))

    def test_lancer_sans_aucun_test_ne_declenche_pas_lia(self):
        # "NO TESTS RAN" (unittest sans classe TestCase) : pas d'analyse IA.
        ctx = FakeCtx(self.root, mode="fullauto")
        with mock.patch.object(
            agent_tests,
            "executer_tests",
            return_value=(5, "Ran 0 tests in 0.000s\n\nNO TESTS RAN"),
        ):
            code = agent_tests.lancer(ctx)

        self.assertEqual(code, 1)
        self.assertEqual(ctx.prompts, [])  # aucune analyse IA lancée
        self.assertTrue(any("Aucun test n'a été exécuté" in n for n in ctx.notes))
        self.assertFalse(any("Tests passés" in n for n in ctx.notes))

    def test_analyse_echecs_propose_une_correction_en_autoedit(self):
        ctx = FakeCtx(
            self.root,
            mode="autoedit",
            reponses=[
                (0, "La variable n'existe pas dans calcul.py."),
                (
                    0,
                    json.dumps(
                        {"fichiers": [{"chemin": "calcul.py", "contenu": "def add(a, b):\n    return a + b\n"}]}
                    ),
                ),
            ],
        )
        applique = agent_tests.analyser_echecs("FAILED test_calcul", ctx)

        self.assertTrue(applique)
        self.assertTrue(any("Correction appliquée" in n for n in ctx.notes))


class AgentDebugTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fichier = self.root / "bug.py"
        self.fichier.write_text("x = undefined_var\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_debug_identifie_le_fichier_et_la_ligne(self):
        ctx = FakeCtx(
            self.root,
            mode="suggest",
            reponses=[(0, json.dumps({"fichier": "bug.py", "ligne": 1, "cause": "variable non définie", "correction": "définir la variable"}))],
        )
        code = agent_debug.analyser("NameError: undefined_var", ctx)

        self.assertEqual(code, 0)
        self.assertTrue(any("bug.py" in n and "ligne 1" in n for n in ctx.notes))

    def test_fix_reussi_quand_les_tests_passent(self):
        ctx = FakeCtx(
            self.root,
            mode="autoedit",
            reponses=[
                (0, json.dumps([{"ligne": 1, "description": "variable non définie"}])),
                (0, json.dumps({"fichiers": [{"chemin": "bug.py", "contenu": "x = 1\n"}]})),
            ],
        )
        with mock.patch.object(agent_tests, "executer_tests", return_value=(0, "OK")):
            code = agent_debug.fixer(self.fichier, ctx)

        self.assertEqual(code, 0)
        self.assertEqual(self.fichier.read_text(), "x = 1\n")
        self.assertTrue(any("Bug corrigé avec succès" in n for n in ctx.notes))

    def test_review_retourne_un_score(self):
        ctx = FakeCtx(self.root, mode="suggest", reponses=[(0, "SCORE: 7/10\n## Bugs potentiels\n- rien")])
        code = agent_debug.review(self.fichier, ctx)

        self.assertEqual(code, 0)
        self.assertTrue(any("SCORE: 7/10" in n for n in ctx.notes))


class AgentSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "projet"
        self.root.mkdir()
        # C06 : les sessions vivent dans .darkcodex/sessions du projet. Isole
        # aussi l'ancien dossier global et l'ancien historique pour la migration.
        etat = Path(self.tmp.name) / "etat"
        etat.mkdir()
        for patcher in (
            mock.patch.dict(os.environ, {"DARKCODEX_HOME": str(etat)}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.store = cli.Store(Path(self.tmp.name) / "db.sqlite")
        self.addCleanup(self.store.close)
        self.session_id = self.store.new_session(self.root, "local")
        self.store.add_message(self.session_id, "user", "bonjour")
        self.store.add_message(self.session_id, "assistant", "salut")
        self.store.log_run(self.root, "pytest", 0, "OK")

    def _ctx(self) -> FakeCtx:
        ctx = FakeCtx(self.root, mode="suggest")
        ctx.store = self.store
        ctx.session_id = self.session_id
        return ctx

    def test_sauvegarde_liste_reprise(self):
        ctx = self._ctx()
        path = agent_session.sauvegarder("atelier", ctx)
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["nom"], "atelier")
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(data["commandes"][0]["commande"], "pytest")

        self.assertEqual(len(agent_session.lister(self.root)), 1)
        self.assertTrue((self.root / ".darkcodex" / "sessions" / "atelier.json").exists())
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            agent_session.afficher_liste(ctx)
        self.assertIn("atelier", sortie.getvalue())

        nouveau = self.store.new_session(self.root, "local")
        ctx.session_id = nouveau
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            ok = agent_session.reprendre("atelier", ctx)

        self.assertTrue(ok)
        self.assertIn("Je reprends le travail sur projet", sortie.getvalue())
        self.assertEqual(len(self.store.messages_session(nouveau)), 2)

    def test_reprise_session_inconnue(self):
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            ok = agent_session.reprendre("inconnue", self._ctx())
        self.assertFalse(ok)
        self.assertIn("introuvable", sortie.getvalue())


class InitAgentsMdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "app.py").write_text("print('ok')\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_genere_agents_md(self):
        ctx = FakeCtx(self.root, mode="autoedit", reponses=[(0, "# AGENTS.md — Instructions\n\n## Architecture\n- FastAPI\n")])
        code = cli.generer_agents_md(ctx)

        cible = self.root / "AGENTS.md"
        self.assertEqual(code, 0)
        self.assertTrue(cible.exists())
        self.assertIn("Instructions", cible.read_text())
        self.assertIn("AGENTS.md", ctx.prompts[0])
        self.assertIn("unittest", ctx.prompts[0])  # commande de test réellement détectée

    def test_init_ne_recrase_pas_agents_md(self):
        (self.root / "AGENTS.md").write_text("# existant\n")
        ctx = FakeCtx(self.root, mode="autoedit", reponses=[(0, "nouveau contenu")])
        code = cli.generer_agents_md(ctx)

        self.assertEqual(code, 0)
        self.assertEqual((self.root / "AGENTS.md").read_text(), "# existant\n")
        self.assertEqual(ctx.prompts, [])

    def test_agents_md_est_incorpore_au_contexte(self):
        (self.root / "AGENTS.md").write_text("## Règles\n- toujours en français\n")
        store = cli.Store(Path(self.tmp.name) / "db.sqlite")
        self.addCleanup(store.close)

        contexte = cli.build_context(self.root, store, {})

        self.assertIn("Project rules to always follow (AGENTS.md)", contexte)
        self.assertIn("toujours en français", contexte)

    def test_rules_lues_par_le_contexte(self):
        (self.root / "AGENTS.md").write_text("## Règles\n- ne pas toucher à .env\n")
        ctx = cli.Ctx(self.root, "local", {})
        self.assertIn("ne pas toucher à .env", ctx.rules())


class RoutageCommandesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_routage_vers_chaque_agent(self):
        ctx = FakeCtx(self.root, mode="autoedit", reponses=[(0, "ok")])

        # Chaque commande est reconnue (True = traitée) et ne tombe pas dans le chat.
        for commande in (
            "/projet .",
            "/modifier app.py ajoute un docstring",
            "/creer app.py un script",
            "/refactoriser app.py",
            "/install",
            "/build",
            "/start",
            "/test",
            "/lint",
            "/auto ajoute une route",
            "/run ls",
            "/debug NameError",
            "/fix app.py",
            "/review app.py",
            "/git status",
            "/tests run",
            "/session list",
            "/init",
        ):
            with self.subTest(commande=commande):
                traitee = cli.handle_agent_command(commande, ctx)
                self.assertTrue(traitee, commande)

    def test_commande_inconnue_non_routee(self):
        ctx = FakeCtx(self.root)
        self.assertFalse(cli.handle_agent_command("une question libre", ctx))
        self.assertFalse(cli.handle_agent_command("/autre chose", ctx))

    def test_commandes_sans_argument_affichent_lusage(self):
        ctx = FakeCtx(self.root, mode="suggest")
        for commande in ("/modifier", "/creer", "/refactoriser", "/auto", "/run", "/debug", "/fix", "/review", "/tests", "/session"):
            with self.subTest(commande=commande):
                cli.handle_agent_command(commande, ctx)
        self.assertTrue(any("Usage" in n for n in ctx.notes))


if __name__ == "__main__":
    unittest.main()
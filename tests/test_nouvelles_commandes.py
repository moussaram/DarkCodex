import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from darkcodex import agent_fichiers, cli, cout, historique


def sortie(fonction, *args):
    tampon = io.StringIO()
    with contextlib.redirect_stdout(tampon):
        resultat = fonction(*args)
    return resultat, tampon.getvalue()


class FauxCtx:
    def __init__(self, cwd):
        self.cwd = cwd

    def note(self, texte=""):
        pass

    def confirm_edit(self, question):
        return True

    def confirm_always(self, question):
        return True

    def note_modification(self, chemin, action):
        pass


class CommandesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_commandes_inconnues_ne_sont_pas_prises(self):
        self.assertFalse(cli.handle_nouvelles_commandes("/lire x.py", self.cwd, None))

    def test_historique_annuler_diff(self):
        fichier = self.cwd / "a.py"
        fichier.write_text("v1\n")
        historique.enregistrer(self.cwd, fichier)
        fichier.write_text("v2\n")

        _, texte = sortie(cli.handle_nouvelles_commandes, "/historique", self.cwd, None)
        self.assertIn("a.py", texte)

        _, texte = sortie(cli.handle_nouvelles_commandes, "/diff", self.cwd, None)
        self.assertIn("+v2", texte)

        with mock.patch.object(cli, "confirm_user", return_value=True):
            _, texte = sortie(cli.handle_nouvelles_commandes, "/annuler", self.cwd, None)
        self.assertIn("Restauré", texte)
        self.assertEqual(fichier.read_text(), "v1\n")

    def test_annuler_refuse_ne_change_rien(self):
        fichier = self.cwd / "a.py"
        fichier.write_text("v1")
        historique.enregistrer(self.cwd, fichier)
        fichier.write_text("v2")
        with mock.patch.object(cli, "confirm_user", return_value=False):
            sortie(cli.handle_nouvelles_commandes, "/annuler", self.cwd, None)
        self.assertEqual(fichier.read_text(), "v2")

    def test_appliquer_fichier_enregistre_l_historique(self):
        fichier = self.cwd / "m.py"
        fichier.write_text("ancien\n")
        self.assertTrue(agent_fichiers.appliquer_fichier(fichier, "nouveau\n", FauxCtx(self.cwd)))
        entrees = historique.lister(self.cwd)
        self.assertEqual(entrees[0]["chemin"], "m.py")
        ok, _ = historique.annuler(self.cwd)
        self.assertTrue(ok)
        self.assertEqual(fichier.read_text(), "ancien\n")

    def test_cout_et_limite(self):
        with mock.patch.object(cli.cout, "SESSION", cout.SuiviCout()):
            _, texte = sortie(cli.handle_nouvelles_commandes, "/limite 20000", self.cwd, None)
            self.assertIn("20 000", texte)

            cli.cout.SESSION.ajouter(1234)
            _, texte = sortie(cli.handle_nouvelles_commandes, "/cout", self.cwd, {"tokens_solde": 4000})
            self.assertIn("1 234", texte)
            self.assertIn("20 000", texte)
            self.assertIn("4 000", texte)

            _, texte = sortie(cli.handle_nouvelles_commandes, "/limite off", self.cwd, None)
            self.assertIn("supprimée", texte)

    def test_ai_answer_connecte_met_a_jour_solde_et_cout(self):
        session = {"token": "dkx_abc", "tokens_solde": 100}
        with mock.patch.object(cli.cout, "SESSION", cout.SuiviCout()), mock.patch.object(
            cli, "load_auth", return_value=session
        ), mock.patch.object(
            cli, "server_answer", return_value=(0, "réponse", {"tokens_utilises": 10, "solde_restant": 90})
        ), \
                mock.patch.object(cli, "save_auth") as sauver, mock.patch.object(cli, "show_token_line") as ligne:
            resultat = cli.ai_answer("gemini", "bonjour", {}, self.cwd)
            utilises = cli.cout.SESSION.utilises

        self.assertEqual(resultat, (0, "réponse"))
        self.assertEqual(utilises, 10)
        self.assertEqual(sauver.call_args.args[0]["tokens_solde"], 90)
        ligne.assert_called_once_with(10, 90)

    def test_ai_answer_respecte_la_limite(self):
        suivi = cout.SuiviCout(limite=10)
        suivi.ajouter(10)
        with mock.patch.object(cli.cout, "SESSION", suivi), mock.patch.object(
            cli, "load_auth", return_value={"token": "dkx_abc"}
        ), mock.patch.object(cli, "server_answer") as serveur:
            code, message = cli.ai_answer("gemini", "bonjour", {}, self.cwd)

        self.assertEqual(code, 1)
        self.assertIn("Limite de session", message)
        serveur.assert_not_called()

    def test_parrainer(self):
        session = {"token": "dkx_abc", "tokens_solde": 100}
        _, texte = sortie(cli.handle_nouvelles_commandes, "/parrainer", self.cwd, None)
        self.assertIn("/login", texte)

        with mock.patch.object(cli, "api_call", return_value=(200, {"code": "DK-AB23CD", "bonus": 2000, "filleuls": 3, "tokens_gagnes": 6000})):
            _, texte = sortie(cli.handle_nouvelles_commandes, "/parrainer", self.cwd, session)
        self.assertIn("DK-AB23CD", texte)

        with mock.patch.object(cli, "api_call", return_value=(200, {"status": "ok", "tokens_ajoutes": 2000})) as appel, \
                mock.patch.object(cli, "save_auth") as sauver:
            _, texte = sortie(cli.handle_nouvelles_commandes, "/parrainer utiliser DK-AB23CD", self.cwd, session)
        self.assertIn("Code accepté", texte)
        self.assertEqual(appel.call_args.kwargs["payload"], {"code": "DK-AB23CD"})
        self.assertEqual(sauver.call_args.args[0]["tokens_solde"], 2100)

        with mock.patch.object(cli, "api_call", return_value=(409, {"detail": "Tu as déjà utilisé un code de parrainage."})):
            _, texte = sortie(cli.handle_nouvelles_commandes, "/parrainer utiliser DK-AB23CD", self.cwd, session)
        self.assertIn("déjà utilisé", texte)


if __name__ == "__main__":
    unittest.main()

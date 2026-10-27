import subprocess
import tempfile
import unittest
from pathlib import Path

from darkcodex import updater

GIT = subprocess.run(["git", "--version"], capture_output=True).returncode == 0


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


class VersionTests(unittest.TestCase):
    def test_parse_et_comparaison(self):
        self.assertEqual(updater.parse_version("v2.1"), (2, 1, 0))
        self.assertEqual(updater.parse_version("2.10.3-beta"), (2, 10, 3))
        self.assertEqual(updater.parse_version(None), (0, 0, 0))
        self.assertTrue(updater.est_plus_recente("2.10.0", "2.9.9"))
        self.assertFalse(updater.est_plus_recente("2.1.0", "2.1"))


class LancementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "cache.json"
        self.questions = []

    def tearDown(self):
        self.tmp.cleanup()

    def lancer(self, latest="2.2.0", minimum="0.0.0", reponse="o", interactif=True, installer=None, maintenant=1_000_000.0, recuperer=None):
        appels = {"installer": 0}

        def faux_installer():
            appels["installer"] += 1
            return (True, "ok") if installer is None else installer()

        resultat = {"sortie": None}
        try:
            updater.verifier_au_lancement(
                "http://x", "2.1.0", interactif=interactif, cache_path=self.cache, maintenant=maintenant,
                recuperer=recuperer or (lambda base: {"latest": latest, "minimum": minimum}),
                installer=faux_installer, demander=lambda q: (self.questions.append(q) or reponse),
            )
        except SystemExit as exc:
            resultat["sortie"] = exc.code
        return resultat["sortie"], appels["installer"]

    def test_pas_de_question_hors_terminal(self):
        self.assertEqual(self.lancer(interactif=False), (None, 0))
        self.assertEqual(self.questions, [])

    def test_acceptee_installe_puis_quitte(self):
        self.assertEqual(self.lancer(reponse="o"), (0, 1))

    def test_refusee_continue(self):
        self.assertEqual(self.lancer(reponse="n"), (None, 0))

    def test_une_verification_par_jour(self):
        self.lancer(reponse="n")
        self.questions.clear()
        self.assertEqual(self.lancer(reponse="o", maintenant=1_000_000.0 + 3600), (None, 0))
        self.assertEqual(self.questions, [])
        self.assertEqual(self.lancer(reponse="n", maintenant=1_000_000.0 + 90_000), (None, 0))
        self.assertEqual(len(self.questions), 1)

    def test_serveur_injoignable_ne_bloque_pas(self):
        self.assertEqual(self.lancer(recuperer=lambda base: None), (None, 0))
        self.assertEqual(self.questions, [])

    def test_version_obligatoire_refusee_quitte(self):
        self.assertEqual(self.lancer(latest="2.2.0", minimum="2.2.0", reponse="n"), (1, 0))

    def test_version_obligatoire_echec_installation_quitte(self):
        self.assertEqual(self.lancer(minimum="2.2.0", reponse="o", installer=lambda: (False, "échec")), (1, 1))

    def test_deja_a_jour(self):
        self.assertEqual(self.lancer(latest="2.1.0"), (None, 0))
        self.assertEqual(self.questions, [])

    def test_commande_update(self):
        sortie = updater.mettre_a_jour_maintenant("http://x", "2.1.0", recuperer=lambda b: {"latest": "2.1.0"}, installer=lambda: (True, "ok"))
        self.assertEqual(sortie, 0)

        sortie = updater.mettre_a_jour_maintenant("http://x", "2.1.0", recuperer=lambda b: {"latest": "3.0.0"}, installer=lambda: (False, "non"))
        self.assertEqual(sortie, 1)

        self.assertEqual(updater.mettre_a_jour_maintenant("http://x", "2.1.0", recuperer=lambda b: None), 1)


@unittest.skipUnless(GIT, "git requis")
class InstallationTests(unittest.TestCase):
    def test_pull_reel_et_refus_si_modifie(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            subprocess.run(["git", "init", "-q", "--bare", str(base / "origine.git")], check=True)
            for nom in ("dev", "user"):
                subprocess.run(["git", "clone", "-q", str(base / "origine.git"), str(base / nom)], check=True, capture_output=True)

            (base / "dev" / "a.txt").write_text("v1")
            git(base / "dev", "add", ".")
            git(base / "dev", "commit", "-qm", "v1")
            git(base / "dev", "push", "-q", "origin", "HEAD")

            git(base / "user", "pull", "-q", "origin", "HEAD")

            (base / "dev" / "a.txt").write_text("v2")
            git(base / "dev", "commit", "-qam", "v2")
            git(base / "dev", "push", "-q", "origin", "HEAD")

            (base / "user" / "a.txt").write_text("modifié localement")
            ok, message = updater.installer_mise_a_jour(base / "user")
            self.assertFalse(ok)
            self.assertIn("modifiés localement", message)

            git(base / "user", "checkout", "--", "a.txt")
            ok, message = updater.installer_mise_a_jour(base / "user")
            self.assertTrue(ok, message)
            self.assertEqual((base / "user" / "a.txt").read_text(), "v2")

    def test_sans_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, message = updater.installer_mise_a_jour(Path(tmp))
            self.assertFalse(ok)
            self.assertIn("Installation sans Git", message)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from darkcodex import historique


class HistoriqueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.racine = Path(self.tmp.name)
        self.fichier = self.racine / "app.py"

    def tearDown(self):
        self.tmp.cleanup()

    def test_annuler_restaure_l_ancienne_version(self):
        self.fichier.write_text("v1")
        historique.enregistrer(self.racine, self.fichier)
        self.fichier.write_text("v2")
        ok, message = historique.annuler(self.racine)
        self.assertTrue(ok, message)
        self.assertEqual(self.fichier.read_text(), "v1")

    def test_annuler_deux_fois_retablit(self):
        self.fichier.write_text("v1")
        historique.enregistrer(self.racine, self.fichier)
        self.fichier.write_text("v2")
        historique.annuler(self.racine)
        self.assertEqual(self.fichier.read_text(), "v1")
        historique.annuler(self.racine)
        self.assertEqual(self.fichier.read_text(), "v2")

    def test_fichier_cree_est_supprime_par_annuler(self):
        historique.enregistrer(self.racine, self.fichier)  # le fichier n'existe pas encore
        self.fichier.write_text("nouveau")
        ok, _ = historique.annuler(self.racine)
        self.assertTrue(ok)
        self.assertFalse(self.fichier.exists())
        historique.annuler(self.racine)  # rétablir
        self.assertEqual(self.fichier.read_text(), "nouveau")

    def test_rien_a_annuler(self):
        self.assertEqual(historique.annuler(self.racine), (False, "Rien à annuler."))

    def test_fichier_hors_du_projet_ignore(self):
        with tempfile.TemporaryDirectory() as autre:
            dehors = Path(autre) / "x.txt"
            dehors.write_text("a")
            self.assertIsNone(historique.enregistrer(self.racine, dehors))

    def test_l_historique_n_est_jamais_versionne(self):
        self.fichier.write_text("v1")
        historique.enregistrer(self.racine, self.fichier)
        self.assertEqual((historique.dossier_historique(self.racine) / ".gitignore").read_text(), "*\n")

    def test_seules_les_dernieres_entrees_sont_gardees(self):
        self.fichier.write_text("v1")
        with mock.patch.object(historique, "MAX_ENTREES", 3):
            for i in range(5):
                self.fichier.write_text(f"v{i}")
                historique.enregistrer(self.racine, self.fichier)
        self.assertEqual(len(historique.lister(self.racine, limite=50)), 3)
        sauvegardes = list(historique.dossier_historique(self.racine).glob("*.bak"))
        self.assertEqual(len(sauvegardes), 3)

    def test_diff_montre_avant_et_apres(self):
        self.fichier.write_text("a\nb\n")
        historique.enregistrer(self.racine, self.fichier)
        self.fichier.write_text("a\nc\n")
        ok, texte = historique.diff_derniere(self.racine)
        self.assertTrue(ok)
        self.assertIn("-b", texte)
        self.assertIn("+c", texte)
        self.assertEqual(historique.diff_derniere(self.racine, "../secret.txt")[0], False)

    def test_liste_formatee(self):
        self.assertEqual(historique.formater([]), "Aucune modification enregistrée.")
        self.fichier.write_text("v1")
        historique.enregistrer(self.racine, self.fichier)
        self.assertIn("app.py", historique.formater(historique.lister(self.racine)))


if __name__ == "__main__":
    unittest.main()

import subprocess
import unittest
from unittest import mock

from darkcodex import voix


class VoixTests(unittest.TestCase):
    def test_sans_termux_api(self):
        with mock.patch.object(voix.shutil, "which", return_value=None):
            ok, message = voix.dicter()
            self.assertFalse(ok)
            self.assertIn("pkg install termux-api", message)

    def test_texte_reconnu(self):
        with mock.patch.object(voix.shutil, "which", return_value="/bin/termux-speech-to-text"), mock.patch.object(
            voix.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=" bonjour le monde \n")
        ):
            self.assertEqual(voix.dicter(), (True, "bonjour le monde"))

    def test_aucun_texte_ou_delai_depasse(self):
        with mock.patch.object(voix.shutil, "which", return_value="/x"):
            with mock.patch.object(voix.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="")):
                self.assertFalse(voix.dicter()[0])
            with mock.patch.object(voix.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
                self.assertIn("délai", voix.dicter()[1])

    def test_validation_envoyer_annuler_modifier(self):
        affichage = []
        dicteur = lambda: (True, "ouvre le fichier app point py")

        self.assertEqual(voix.dicter_et_valider(dicteur, lambda q: "", affichage.append), "ouvre le fichier app point py")
        self.assertIsNone(voix.dicter_et_valider(dicteur, lambda q: "n", affichage.append))

        reponses = iter(["m", "lis app.py"])
        self.assertEqual(voix.dicter_et_valider(dicteur, lambda q: next(reponses), affichage.append), "lis app.py")

        reponses = iter(["m", ""])
        self.assertEqual(voix.dicter_et_valider(dicteur, lambda q: next(reponses), affichage.append), "ouvre le fichier app point py")

    def test_echec_de_dictee(self):
        self.assertIsNone(voix.dicter_et_valider(lambda: (False, "erreur"), lambda q: "o", lambda t: None))


if __name__ == "__main__":
    unittest.main()

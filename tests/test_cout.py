import unittest

from darkcodex import cout


class CoutTests(unittest.TestCase):
    def test_estimation(self):
        self.assertEqual(cout.estimer_tokens(""), 1)
        self.assertEqual(cout.estimer_tokens("a" * 400), 100)

    def test_limite(self):
        suivi = cout.SuiviCout()
        self.assertEqual(suivi.definir_limite("20000")[0], True)
        self.assertEqual(suivi.limite, 20000)
        self.assertFalse(suivi.definir_limite("abc")[0])
        self.assertFalse(suivi.definir_limite("-5")[0])
        suivi.definir_limite("off")
        self.assertIsNone(suivi.limite)

    def test_limite_bloque_la_demande(self):
        suivi = cout.SuiviCout(limite=1000)
        suivi.ajouter(900)
        ok, message = suivi.verifier(200)
        self.assertFalse(ok)
        self.assertIn("Limite de session", message)
        self.assertTrue(suivi.verifier(50)[0])

    def test_confirmation_des_grosses_demandes(self):
        suivi = cout.SuiviCout(seuil=1000)
        self.assertEqual(suivi.verifier(2000, interactif=True, demander=lambda q: "n"), (False, "Demande annulée."))
        self.assertTrue(suivi.verifier(2000, interactif=True, demander=lambda q: "")[0])
        self.assertTrue(suivi.verifier(500, interactif=True, demander=lambda q: "n")[0])  # sous le seuil
        self.assertTrue(suivi.verifier(2000, interactif=False)[0])  # jamais de question dans un script
        self.assertTrue(suivi.verifier(2000, confirmer=False, interactif=True, demander=lambda q: "n")[0])

    def test_compteurs(self):
        suivi = cout.SuiviCout()
        suivi.ajouter(120)
        suivi.ajouter(None)
        suivi.ajouter("abc")
        suivi.ajouter_estimation("a" * 40, "b" * 40)
        self.assertEqual((suivi.utilises, suivi.estimes, suivi.total), (120, 20, 140))

    def test_lecture_de_l_environnement(self):
        from unittest import mock

        with mock.patch.dict("os.environ", {"DARKCODEX_MAX_TOKENS": "5000", "DARKCODEX_COST_CONFIRM": "800"}):
            suivi = cout.SuiviCout.depuis_environnement()
            self.assertEqual((suivi.limite, suivi.seuil), (5000, 800))

        with mock.patch.dict("os.environ", {"DARKCODEX_MAX_TOKENS": "x"}, clear=True):
            self.assertIsNone(cout.SuiviCout.depuis_environnement().limite)


if __name__ == "__main__":
    unittest.main()

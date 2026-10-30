import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from fastapi.testclient import TestClient
    from server import main as srv_main
except Exception:  # pragma: no cover - fastapi / httpx requis pour ces tests
    TestClient = None

skip = unittest.skipIf(TestClient is None, "fastapi + httpx requis pour ces tests")


@skip
class SitePublicTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(srv_main.app)

    def test_pages_publiques_renvoient_du_html_200(self):
        chemins = [
            "/",
            "/installation",
            "/utilisation",
            "/abonnements",
            "/faq",
            "/statut",
            "/legal",
            "/connexion",
            "/compte",
            "/device",
            "/paiement/retour",
        ]
        for chemin in chemins:
            reponse = self.client.get(chemin)
            self.assertEqual(reponse.status_code, 200, chemin)
            self.assertIn("text/html", reponse.headers["content-type"], chemin)

    def test_racine_contient_le_site_officiel(self):
        reponse = self.client.get("/")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Un assistant IA de codage", reponse.text)

    def test_scripts_nav_et_styles_renvoient_200(self):
        self.assertEqual(self.client.get("/static/nav.js").status_code, 200)
        self.assertEqual(self.client.get("/static/site.css").status_code, 200)

    def test_les_pages_publiques_incluent_nav_js(self):
        for chemin in [
            "/",
            "/installation",
            "/utilisation",
            "/abonnements",
            "/faq",
            "/statut",
            "/legal",
            "/connexion",
            "/compte",
            "/device",
            "/paiement/retour",
        ]:
            reponse = self.client.get(chemin)
            self.assertIn('<script src="/static/nav.js"', reponse.text, chemin)


if __name__ == "__main__":
    unittest.main()

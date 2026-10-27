import base64
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from fastapi.testclient import TestClient
    from server import device as srv_device
    from server import main as srv_main
except Exception:  # fastapi / httpx absents
    TestClient = None

skip = unittest.skipIf(TestClient is None, "fastapi + httpx requis pour ces tests")

ENV = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "service-secret"}
SESSION = {"user_id": "u1", "email": "a@b.c", "nom": "Awa", "tokens_solde": 5, "tokens_utilises": 0, "plan": "gratuit"}


def faux_jwt(role):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256'})}.{b64({'role': role})}.signature"


@skip
class CodeEtAppareilsTests(unittest.TestCase):
    def test_format_du_code(self):
        for _ in range(50):
            self.assertRegex(srv_device.generer_code(), r"^DARK-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")

    def test_creer_appareil_ne_stocke_que_l_empreinte(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "post") as post:
            post.return_value.ok = True
            token = srv_device.creer_appareil("u1", "a@b.c", "Awa")
            corps = post.call_args.kwargs["json"]

        self.assertTrue(token.startswith("dkx_"))
        self.assertEqual(corps["token_hash"], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, json.dumps(corps))

    def test_verifier_token_appareil(self):
        with mock.patch.object(
            srv_device, "lire_appareil", return_value={"user_id": "u1", "email": "a@b.c", "nom": "Awa"}
        ), mock.patch.object(
            srv_device, "lire_profil", return_value={"tokens_solde": 42, "tokens_utilises": 8, "plan": "starter"}
        ):
            session = srv_device.verifier_token("dkx_abc")
            self.assertEqual((session["user_id"], session["tokens_solde"], session["plan"]), ("u1", 42, "starter"))

        with mock.patch.object(srv_device, "lire_appareil", return_value=None):
            self.assertIsNone(srv_device.verifier_token("dkx_inconnu"))

    def test_revoquer_ignore_les_jetons_non_appareil(self):
        with mock.patch.object(srv_device.requests, "patch") as patch:
            self.assertFalse(srv_device.revoquer_appareil("un-jwt"))
            patch.assert_not_called()

    def test_code_confirme_est_supprime_apres_livraison(self):
        ligne = {"confirme": True, "token": "dkx_abc", "expire_a": "2099-01-01T00:00:00+00:00"}
        with mock.patch.object(srv_device, "_lire_code", return_value=ligne), mock.patch.object(
            srv_device, "verifier_token", return_value=SESSION
        ), mock.patch.object(srv_device, "supprimer_code") as supprimer:
            resultat = srv_device.verifier_code("dark-aaaa-bbbb")

        self.assertEqual((resultat["status"], resultat["token"]), ("confirme", "dkx_abc"))
        supprimer.assert_called_once_with("DARK-AAAA-BBBB")


@skip
class RoutesTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(srv_main.app)
        self.entetes = {"Authorization": "Bearer jwt-du-navigateur"}

    def test_confirmation_donne_au_terminal_son_propre_jeton(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(
            srv_device, "code_en_attente", return_value=True
        ), mock.patch.object(srv_device, "creer_appareil", return_value="dkx_nouveau"), mock.patch.object(
            srv_device, "confirmer_code", return_value=True
        ) as confirmer:
            reponse = self.client.post("/device/confirm", json={"code": "DARK-AAAA-BBBB"}, headers=self.entetes)

        self.assertEqual(reponse.status_code, 200)
        confirmer.assert_called_once_with("DARK-AAAA-BBBB", "u1", "dkx_nouveau", None)

    def test_code_inconnu_ne_cree_aucun_appareil(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(
            srv_device, "code_en_attente", return_value=False
        ), mock.patch.object(srv_device, "creer_appareil") as creer:
            reponse = self.client.post("/device/confirm", json={"code": "DARK-XXXX-YYYY"}, headers=self.entetes)

        self.assertEqual(reponse.status_code, 404)
        creer.assert_not_called()

    def test_confirmation_perdue_revoque_l_appareil(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(
            srv_device, "code_en_attente", return_value=True
        ), mock.patch.object(srv_device, "creer_appareil", return_value="dkx_x"), mock.patch.object(
            srv_device, "confirmer_code", return_value=False
        ), mock.patch.object(srv_device, "revoquer_appareil") as revoquer:
            reponse = self.client.post("/device/confirm", json={"code": "DARK-AAAA-BBBB"}, headers=self.entetes)

        self.assertEqual(reponse.status_code, 404)
        revoquer.assert_called_once_with("dkx_x")

    def test_logout_revoque_le_jeton(self):
        with mock.patch.object(srv_device, "revoquer_appareil", return_value=True) as revoquer:
            reponse = self.client.post("/logout", headers={"Authorization": "Bearer dkx_abc"})

        self.assertEqual(reponse.json(), {"revoque": True})
        revoquer.assert_called_once_with("dkx_abc")

    def test_config_refuse_une_cle_secrete(self):
        cas = [
            ({}, 503),
            ({"SUPABASE_URL": "https://x", "SUPABASE_ANON_KEY": faux_jwt("service_role")}, 503),
            ({"SUPABASE_URL": "https://x", "SUPABASE_ANON_KEY": "sb_secret_abc"}, 503),
            ({"SUPABASE_URL": "https://x", "SUPABASE_ANON_KEY": faux_jwt("anon")}, 200),
            ({"SUPABASE_URL": "https://x", "SUPABASE_ANON_KEY": "sb_publishable_abc"}, 200),
        ]
        for env, attendu in cas:
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(self.client.get("/config").status_code, attendu, env)

    def test_version(self):
        with mock.patch.dict(os.environ, {"DARKCODEX_LATEST_VERSION": "9.9.9"}):
            self.assertEqual(self.client.get("/version").json()["latest"], "9.9.9")

        with mock.patch.dict(os.environ, {}, clear=True):
            donnees = self.client.get("/version").json()
            self.assertEqual(donnees["minimum"], "0.0.0")
            self.assertNotEqual(donnees["latest"], "")

    def test_plans(self):
        with mock.patch.object(
            srv_device, "lister_plans", return_value=[{"nom": "starter", "tokens": 50000, "prix_fcfa": 500}]
        ):
            self.assertEqual(self.client.get("/plans").json()[0]["nom"], "starter")

    def test_pages_et_fichiers_statiques(self):
        for chemin in ("/device", "/connexion", "/abonnements", "/compte", "/paiement/retour"):
            reponse = self.client.get(chemin)
            self.assertEqual(reponse.status_code, 200, chemin)
            self.assertIn("text/html", reponse.headers["content-type"])

        self.assertEqual(self.client.get("/static/auth.js").status_code, 200)
        self.assertEqual(self.client.get("/static/site.css").status_code, 200)

    def test_page_absente_donne_404(self):
        with TemporaryDirectory() as tmp, mock.patch.object(srv_main, "WEB_DIR", Path(tmp)):
            self.assertEqual(self.client.get("/abonnements").status_code, 404)


if __name__ == "__main__":
    unittest.main()

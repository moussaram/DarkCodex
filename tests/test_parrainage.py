import os
import re
import sys
import unittest
from pathlib import Path
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


def reponse(ok=True, statut=200, json=None):
    r = mock.Mock()
    r.ok, r.status_code, r.text = ok, statut, ""
    r.json.return_value = json
    return r


@skip
class ParrainageDeviceTests(unittest.TestCase):
    def test_format_du_code(self):
        for _ in range(50):
            self.assertRegex(srv_device.generer_code_parrainage(), r"^DK-[A-HJ-NP-Z2-9]{6}$")

    def test_code_existant_retourne_sans_ecriture(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "get", return_value=reponse(json=[{"code_parrainage": "DK-AAAAAA"}])), \
             mock.patch.object(srv_device.requests, "patch") as patch:
            self.assertEqual(srv_device.obtenir_code_parrainage("u1"), "DK-AAAAAA")
            patch.assert_not_called()

    def test_creation_avec_une_collision(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "get", return_value=reponse(json=[{"code_parrainage": None}])), \
             mock.patch.object(srv_device.requests, "patch", side_effect=[reponse(False, 409, None), reponse(json=[{"id": "u1"}])]) as patch:
            code = srv_device.obtenir_code_parrainage("u1")
            self.assertRegex(code, r"^DK-")
            self.assertEqual(patch.call_count, 2)
            self.assertIn("code_parrainage=is.null", patch.call_args.args[0])

    def test_utiliser_parrainage_lit_la_reponse_sql(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "post", return_value=reponse(json="ok")) as post:
            self.assertEqual(srv_device.utiliser_parrainage("u1", "DK-AAAAAA", 2000, 20, 7), "ok")
            self.assertEqual(post.call_args.kwargs["json"]["p_bonus"], 2000)
            self.assertTrue(post.call_args.args[0].endswith("/rpc/utiliser_code_parrainage"))

    def test_stats(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "get", return_value=reponse(json=[{"bonus": 2000}, {"bonus": 2000}])):
            self.assertEqual(srv_device.stats_parrainage("u1"), {"filleuls": 2, "tokens_gagnes": 4000})


@skip
class ParrainageRoutesTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(srv_main.app)
        self.entetes = {"Authorization": "Bearer dkx_abc"}

    def test_mon_code(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), \
             mock.patch.object(srv_device, "obtenir_code_parrainage", return_value="DK-AB23CD"), \
             mock.patch.object(srv_device, "stats_parrainage", return_value={"filleuls": 1, "tokens_gagnes": 2000}):
            donnees = self.client.get("/parrainage", headers=self.entetes).json()
            self.assertEqual((donnees["code"], donnees["filleuls"], donnees["bonus"]), ("DK-AB23CD", 1, 2000))

    def test_utiliser_un_code(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), \
             mock.patch.object(srv_device, "utiliser_parrainage", return_value="ok") as utiliser, \
             mock.patch.dict(os.environ, {"PARRAINAGE_BONUS": "3000"}):
            r = self.client.post("/parrainage/utiliser", json={"code": " dk-ab23cd "}, headers=self.entetes)
            self.assertEqual((r.status_code, r.json()["tokens_ajoutes"]), (200, 3000))
            self.assertEqual(utiliser.call_args.args[:3], ("u1", "DK-AB23CD", 3000))

    def test_refus_expliques(self):
        attendus = {"code_inconnu": 404, "code_personnel": 400, "deja_parraine": 409, "parrain_complet": 409, "compte_trop_ancien": 403, "code_invalide": 400}
        for motif, statut in attendus.items():
            with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(srv_device, "utiliser_parrainage", return_value=motif):
                r = self.client.post("/parrainage/utiliser", json={"code": "DK-AB23CD"}, headers=self.entetes)
                self.assertEqual(r.status_code, statut, motif)
                self.assertTrue(r.json()["detail"])

    def test_format_invalide_n_appelle_pas_la_base(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(srv_device, "utiliser_parrainage") as utiliser:
            r = self.client.post("/parrainage/utiliser", json={"code": "n'importe quoi"}, headers=self.entetes)
            self.assertEqual(r.status_code, 400)
            utiliser.assert_not_called()

    def test_sans_jeton(self):
        self.assertEqual(self.client.get("/parrainage").status_code, 401)


if __name__ == "__main__":
    unittest.main()

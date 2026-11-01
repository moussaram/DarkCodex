import base64
import hashlib
import json
import os
import sys
import urllib.parse
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

ACHATS = [
    {"user_id": "u1", "plan": "starter", "tokens_ajoutes": 50000, "montant_fcfa": 500,
     "transaction_id": "cs_ok", "date_achat": "2026-10-01T10:00:00+00:00"},
    {"user_id": "u1", "plan": "pro", "tokens_ajoutes": 500000, "montant_fcfa": 2500,
     "transaction_id": "cs_att", "date_achat": "2026-10-05T09:00:00+00:00"},
]

PAIEMENTS = [
    {"session_id": "cs_ok", "statut": "credite", "credite_a": "2026-10-01T10:01:00+00:00",
     "cree_a": "2026-01-01T00:00:00+00:00"},
    {"session_id": "cs_att", "statut": "en_attente", "credite_a": None,
     "cree_a": "2026-10-05T09:00:03+00:00"},
]

APPAREILS = [
    {"id": "a1", "nom": "Pixel 8", "email": "a@b.c", "revoque": False, "cree_a": "2026-09-01T00:00:00+00:00"},
    {"id": "a2", "nom": "PC", "email": "a@b.c", "revoque": False, "cree_a": "2026-09-10T00:00:00+00:00"},
]

PROFIL = {"id": "u1", "email": "a@b.c", "nom": "Awa", "plan": "starter", "tokens_solde": 48000,
          "tokens_utilises": 2000, "date_inscription": "2026-09-01T00:00:00+00:00",
          "code_parrainage": "DK-ABC123"}


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
        confirmer.assert_called_once_with("DARK-AAAA-BBBB", "u1", "dkx_nouveau")

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
        ) as lister:
            reponse = self.client.get("/plans")  # aucun en-tête : public
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json()[0]["nom"], "starter")
        lister.assert_called_once_with()

    def test_plans_renvoie_la_table_plans(self):
        """GET /plans renvoie les lignes de la table plans, plan gratuit inclus."""
        import urllib.parse

        lignes = [
            {"nom": "gratuit", "tokens": 5000, "prix_fcfa": 0, "description": "offerts"},
            {"nom": "starter", "tokens": 50000, "prix_fcfa": 500, "description": "Idéal"},
        ]
        with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "get") as get:
            get.return_value.ok = True
            get.return_value.json.return_value = lignes
            resultat = srv_device.lister_plans()

        query = urllib.parse.parse_qs(urllib.parse.urlparse(get.call_args.args[0]).query)
        self.assertEqual(query["select"], ["nom,tokens,prix_fcfa,description"])
        self.assertEqual(query["order"], ["prix_fcfa.asc"])
        self.assertEqual(resultat, lignes)
        self.assertEqual([p["nom"] for p in resultat], ["gratuit", "starter"])

    def test_plans_erreur_supabase_sans_catalogue_de_secours(self):
        """Supabase en erreur : RuntimeError, jamais un faux catalogue de prix."""
        with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "get") as get:
            get.return_value.ok = False
            get.return_value.status_code = 500
            with self.assertRaises(RuntimeError):
                srv_device.lister_plans()
            with self.assertRaises(RuntimeError):
                srv_device.lire_plan("starter")

        with mock.patch.dict(os.environ, ENV), mock.patch.object(
            srv_device.requests, "get", side_effect=srv_device.requests.RequestException("offline")
        ):
            with self.assertRaises(RuntimeError):
                srv_device.lister_plans()

    def test_lire_plan_inconnu_renvoie_none(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "get") as get:
            get.return_value.ok = True
            get.return_value.json.return_value = []
            self.assertIsNone(srv_device.lire_plan("business"))

    def test_normaliser_plan(self):
        self.assertEqual(srv_device.normaliser_plan(""), "gratuit")
        self.assertEqual(srv_device.normaliser_plan(None), "gratuit")
        self.assertEqual(srv_device.normaliser_plan("Starter "), "starter")

    def test_prix_affiche_est_le_prix_facture(self):
        """Le prix affiché sur /plans est EXACTEMENT celui facturé par /paiement/creer."""
        table = [
            {"nom": "gratuit", "tokens": 5000, "prix_fcfa": 0, "description": "offerts"},
            {"nom": "starter", "tokens": 50000, "prix_fcfa": 500, "description": "Idéal"},
            {"nom": "pro", "tokens": 500000, "prix_fcfa": 2500, "description": "Actifs"},
        ]

        def faux_get(url, **kwargs):
            reponse = mock.Mock()
            reponse.ok = True
            if "plans?nom=" in url:
                nom = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["nom"][0].removeprefix("eq.")
                reponse.json.return_value = [p for p in table if p["nom"] == nom]
            else:
                reponse.json.return_value = table
            return reponse

        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch.object(
            srv_device.requests, "get", side_effect=faux_get
        ), mock.patch.object(srv_device.requests, "post") as post:
            post.return_value.ok = True
            # 1. Ce que le site affiche.
            affiches = self.client.get("/plans").json()
            # 2. Ce que le serveur facture (prix et tokens lus dans la MÊME table).
            factures = {
                plan["nom"]: srv_device.lire_plan(plan["nom"]) for plan in table
            }
            srv_device.creer_paiement("u1", "starter", factures["starter"]["tokens"],
                                      factures["starter"]["prix_fcfa"], "cs_1")

        for affiche in affiches:
            facture = factures[affiche["nom"]]
            self.assertEqual(affiche["prix_fcfa"], facture["prix_fcfa"], affiche["nom"])
            self.assertEqual(affiche["tokens"], facture["tokens"], affiche["nom"])
        corps = post.call_args.kwargs["json"]
        self.assertEqual((corps["montant_fcfa"], corps["tokens"], corps["plan"]), (500, 50000, "starter"))
        self.assertEqual([p["prix_fcfa"] for p in affiches], [0, 500, 2500])

    def test_pages_sans_inner_html(self):
        """Aucun contenu venant du serveur inséré avec innerHTML (injection)."""
        for page in sorted((Path(__file__).resolve().parent.parent / "web").glob("*.html")):
            texte = page.read_text(encoding="utf-8")
            self.assertNotIn("innerHTML", texte, page.name)
            self.assertNotIn("insertAdjacentHTML", texte, page.name)


@skip
class EspaceClientTests(unittest.TestCase):
    """Tableau de bord : profil, achats, appareils — toujours dans le compte connecté."""

    def setUp(self):
        self.client = TestClient(srv_main.app)
        self.entetes = {"Authorization": "Bearer jwt-du-navigateur"}

    def _sans_connexion(self, chemin, methode="get", **kwargs):
        return getattr(self.client, methode)(chemin, **kwargs)

    def test_routes_privees_refusent_sans_jetons(self):
        for chemin in ("/profil", "/achats", "/appareils", "/solde"):
            self.assertEqual(self._sans_connexion(chemin).status_code, 401, chemin)
        reponse = self.client.post("/appareils/revoquer", json={"id": "a1"})
        self.assertEqual(reponse.status_code, 401)

    def test_profil_retourne_le_compte_connecte(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(SESSION)), mock.patch.object(
            srv_device, "lire_profil", return_value=dict(PROFIL)
        ) as lire:
            reponse = self.client.get("/profil", headers=self.entetes)

        self.assertEqual(reponse.status_code, 200)
        donnees = reponse.json()
        self.assertEqual((donnees["email"], donnees["plan"], donnees["tokens_solde"]), ("a@b.c", "starter", 48000))
        self.assertEqual(donnees["tokens_utilises"], 2000)
        self.assertEqual(donnees["code_parrainage"], "DK-ABC123")
        lire.assert_called_once_with("u1")  # toujours l'utilisateur du jeton

    def test_profil_sans_ligne_profil_reste_coherent(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(SESSION)), mock.patch.object(
            srv_device, "lire_profil", return_value=None
        ):
            donnees = self.client.get("/profil", headers=self.entetes).json()
        self.assertEqual((donnees["email"], donnees["plan"], donnees["tokens_solde"]), ("a@b.c", "gratuit", 5))

    def test_achats_avec_statut_reel_du_paiement(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(
            srv_device, "lister_achats", return_value=list(ACHATS)
        ) as lister, mock.patch.object(
            srv_device, "paiements_de", return_value=list(PAIEMENTS)
        ):
            reponse = self.client.get("/achats", headers=self.entetes)

        self.assertEqual(reponse.status_code, 200)
        achats = reponse.json()["achats"]
        self.assertEqual(len(achats), 2)
        lister.assert_called_once_with("u1")
        par_ref = {a["reference"]: a for a in achats}
        self.assertEqual(par_ref["cs_ok"]["statut"], "credite")
        self.assertEqual(par_ref["cs_att"]["statut"], "en_attente")
        self.assertEqual(par_ref["cs_ok"]["montant_fcfa"], 500)
        self.assertEqual(par_ref["cs_ok"]["tokens_ajoutes"], 50000)
        self.assertEqual(par_ref["cs_ok"]["date_achat"], "2026-10-01T10:00:00+00:00")

    def test_achats_dun_autre_compte_jamais_visible(self):
        autre = {**SESSION, "user_id": "u2", "email": "autre@b.c"}
        with mock.patch.object(srv_device, "verifier_token", return_value=autre), mock.patch.object(
            srv_device, "lister_achats"
        ) as lister, mock.patch.object(srv_device, "paiements_de") as paiements:
            self.client.get("/achats", headers=self.entetes)
        lister.assert_called_once_with("u2")
        paiements.assert_called_once_with("u2", limite=100)

    def test_appareils_marque_le_courant_et_masque_les_empreintes(self):
        courant = {"id": "a2", "nom": "PC", "email": "a@b.c", "revoque": False,
                   "cree_a": "2026-09-10T00:00:00+00:00"}
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(
            srv_device, "lister_appareils", return_value=list(APPAREILS)
        ), mock.patch.object(
            srv_device, "appareil_de_token", return_value=dict(courant)
        ):
            reponse = self.client.get("/appareils", headers=self.entetes)

        self.assertEqual(reponse.status_code, 200)
        liste = reponse.json()["appareils"]
        self.assertEqual([a["id"] for a in liste], ["a1", "a2"])
        self.assertEqual([a["courant"] for a in liste], [False, True])
        self.assertNotIn("token_hash", json.dumps(liste))
        self.assertNotIn("dkx_", json.dumps(liste))

    def test_revoquer_appareil_du_compte_connecte(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(
            srv_device, "revoquer_appareil_de", return_value=True
        ) as revoquer:
            reponse = self.client.post(
                "/appareils/revoquer", json={"id": "a1"}, headers=self.entetes
            )

        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json(), {"revoque": True, "id": "a1"})
        revoquer.assert_called_once_with("u1", "a1")

    def test_revoquer_appareil_inconnu_ou_dun_autre_compte(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(
            srv_device, "revoquer_appareil_de", return_value=False
        ):
            reponse = self.client.post(
                "/appareils/revoquer", json={"id": "dun-autre"}, headers=self.entetes
            )
            self.assertEqual(reponse.status_code, 404)

            reponse = self.client.post("/appareils/revoquer", json={"id": ""}, headers=self.entetes)
            self.assertEqual(reponse.status_code, 400)

    def test_erreur_supabase_renvoie_une_erreur_claire(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(
            srv_device, "lister_achats", side_effect=RuntimeError("Supabase achats: HTTP 500")
        ):
            reponse = self.client.get("/achats", headers=self.entetes)
        self.assertEqual(reponse.status_code, 503)
        self.assertIn("Supabase", reponse.json()["detail"])

    def test_page_compte_est_un_vrai_tableau_de_bord(self):
        texte = (Path(__file__).resolve().parent.parent / "web" / "compte.html").read_text(
            encoding="utf-8"
        )
        for chemin in ("/profil", "/achats", "/appareils", "/appareils/revoquer"):
            self.assertIn(chemin, texte)
        self.assertIn("exigerConnexion", texte)  # redirection si non connecté
        self.assertIn("Historique des achats", texte)
        self.assertIn("Appareils connectés", texte)

    def test_retour_affiche_le_solde_et_la_commande_solde(self):
        texte = (Path(__file__).resolve().parent.parent / "web" / "retour.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("tapez /solde", texte)
        self.assertIn("Nouveau solde", texte)
        self.assertIn("essai < 14", texte)  # ≈ 40 s de vérification côté retour SasPay

    def test_abonnements_verifie_le_retour_saspay(self):
        texte = (Path(__file__).resolve().parent.parent / "web" / "abonnements.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("/confirmer-paiement", texte)
        self.assertIn("essai < 14", texte)  # ≈ 40 s de vérification au retour de SasPay
        self.assertIn("tapez /solde", texte)

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

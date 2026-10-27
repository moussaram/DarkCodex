"""Tests du système de connexion (device code, jeton, solde, paiement).

Ces tests couvrent les 3 cas imposés par la spec :
  1. premier lancement (pas de auth.json)
  2. lancement suivant (auth.json valide)
  3. tokens épuisés
"""

import asyncio
import contextlib
import datetime as dt
import hashlib
import hmac
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from darkcodex import cli

try:
    from server import device as srv_device
    from server import gemini as srv_gemini
    from server import main as srv_main
    from server import saspay as srv_saspay
    SERVEUR_OK, SERVEUR_ABSENTE = True, ""
except ImportError as exc:  # fastapi/requests du serveur non installés
    srv_device = srv_gemini = srv_main = srv_saspay = None  # type: ignore[assignment]
    SERVEUR_OK, SERVEUR_ABSENTE = False, str(exc)


skip_serveur = unittest.skipUnless(
    SERVEUR_OK, f"serveur non installé ({SERVEUR_ABSENTE}) : pip install -r server/requirements.txt"
)


def call(func, *args, **kwargs):
    """Exécute un endpoint et renvoie (status, payload)."""
    try:
        return 200, func(*args, **kwargs)
    except srv_main.HTTPException as exc:
        return exc.status_code, exc.detail


@skip_serveur
class DeviceCodeTests(unittest.TestCase):
    def test_generer_code_format(self):
        code = srv_device.generer_code()
        self.assertRegex(code, r"^DARK-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")

    def test_endpoint_renvoie_code_lien_et_expiration(self):
        with mock.patch.object(srv_device, "nettoyer_codes_expires"), mock.patch.object(
            srv_device, "creer_code_unique", return_value="DARK-4829"
        ):
            status, data = call(srv_main.device_code)

        self.assertEqual(status, 200)
        self.assertEqual(data["code"], "DARK-4829")
        self.assertEqual(data["lien"], "https://darkcodex.onrender.com/device")
        self.assertEqual(data["expire_dans"], 600)

    def test_verify_passe_de_en_attente_a_confirme(self):
        with mock.patch.object(srv_device, "_lire_code", return_value=None):
            status, data = call(srv_main.device_verify, code="DARK-0000")
        self.assertEqual(data["status"], "expire")

        with mock.patch.object(srv_device, "_lire_code", return_value={"confirme": False, "expire_a": "2999-01-01T00:00:00+00:00"}):
            status, data = call(srv_main.device_verify, code="DARK-4829")
        self.assertEqual(data["status"], "en_attente")

        avec_token = {"confirme": True, "token": "jwt-demo", "expire_a": "2999-01-01T00:00:00+00:00"}
        with mock.patch.object(srv_device, "supprimer_code"), mock.patch.object(
            srv_device, "_lire_code", return_value=avec_token
        ), mock.patch.object(
            srv_device,
            "verifier_token",
            return_value={
                "user_id": "u1",
                "email": "moussa@gmail.com",
                "nom": "Moussa",
                "tokens_solde": 5000,
                "plan": "gratuit",
            },
        ):
            status, data = call(srv_main.device_verify, code="DARK-4829")

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "confirme")
        self.assertEqual(data["token"], "jwt-demo")
        self.assertEqual(data["nom"], "Moussa")
        self.assertEqual(data["tokens_solde"], 5000)

    def test_confirm_met_a_jour_le_code(self):
        payload = srv_main.DeviceConfirmIn(code="DARK-4829")
        with mock.patch.object(
            srv_device, "verifier_token", return_value={"user_id": "u1", "email": "a@b.c", "nom": "M"}
        ), mock.patch.object(srv_device, "code_en_attente", return_value=True), mock.patch.object(
            srv_device, "creer_appareil", return_value="dkx_demo"
        ), mock.patch.object(srv_device, "confirmer_code", return_value=True) as confirmer:
            status, data = call(srv_main.device_confirm, payload, authorization="Bearer jwt-demo")

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "confirme")
        confirmer.assert_called_once_with("DARK-4829", "u1", "dkx_demo")

    def test_confirm_refuse_sans_token(self):
        payload = srv_main.DeviceConfirmIn(code="DARK-4829")
        status, _ = call(srv_main.device_confirm, payload, authorization=None)
        self.assertEqual(status, 401)


PROFIL_VALIDE = {
    "user_id": "u1",
    "email": "moussa@gmail.com",
    "nom": "Moussa",
    "tokens_solde": 5000,
    "tokens_utilises": 0,
    "plan": "gratuit",
    "token": "jwt-demo",
}


@skip_serveur
class VerifyTokenTests(unittest.TestCase):
    def test_token_valide_retourne_le_profil(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE)):
            status, data = call(srv_main.verify_token, authorization="Bearer jwt-demo")

        self.assertEqual(status, 200)
        self.assertEqual(data["email"], "moussa@gmail.com")
        self.assertEqual(data["tokens_solde"], 5000)
        self.assertEqual(data["plan"], "gratuit")

    def test_token_invalide_retourne_401(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=None):
            status, _ = call(srv_main.verify_token, authorization="Bearer mauvais")
        self.assertEqual(status, 401)


@skip_serveur
class ChatTests(unittest.TestCase):
    def test_deduit_les_tokens_utilises(self):
        payload = srv_main.ChatIn(message="explique ce projet")
        avec_solde = dict(PROFIL_VALIDE)
        with mock.patch.object(srv_device, "verifier_token", return_value=avec_solde), mock.patch.object(
            srv_gemini, "appeler_gemini", return_value=("Réponse Gemini", 150)
        ) as gemini_call, mock.patch.object(
            srv_device, "debiter_profil", return_value={"tokens_solde": 4850, "tokens_utilises": 150, "plan": "gratuit"}
        ) as debit, mock.patch.object(srv_device, "maj_profil") as maj:
            status, data = call(srv_main.chat, payload, authorization="Bearer jwt-demo")

        self.assertEqual(status, 200)
        self.assertEqual(data["reponse"], "Réponse Gemini")
        self.assertEqual(data["tokens_utilises"], 150)
        self.assertEqual(data["solde_restant"], 4850)
        self.assertEqual(data["plan"], "gratuit")
        self.assertEqual(gemini_call.call_args.args[0], "explique ce projet")
        debit.assert_called_once_with("u1", 150)
        maj.assert_not_called()

    def test_debit_echoue_reponse_quand_meme(self):
        """Si debiter_profil lève une erreur, la réponse de Gemini est quand même renvoyée."""
        payload = srv_main.ChatIn(message="bonjour")
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE)), mock.patch.object(
            srv_gemini, "appeler_gemini", return_value=("Réponse Gemini", 150)
        ), mock.patch.object(srv_device, "debiter_profil", side_effect=RuntimeError("Supabase debiter_tokens: HTTP 500")):
            status, data = call(srv_main.chat, payload, authorization="Bearer jwt-demo")

        self.assertEqual(status, 200)
        self.assertEqual(data["reponse"], "Réponse Gemini")
        self.assertEqual(data["solde_restant"], 4850)  # repli sur l'ancien calcul
        self.assertEqual(data["plan"], "gratuit")

    def test_reponse_contient_solde_et_plan(self):
        payload = srv_main.ChatIn(message="bonjour")
        avec_plan = {**PROFIL_VALIDE, "plan": "pro"}
        with mock.patch.object(srv_device, "verifier_token", return_value=avec_plan), mock.patch.object(
            srv_gemini, "appeler_gemini", return_value=("ok", 10)
        ), mock.patch.object(
            srv_device, "debiter_profil", return_value={"tokens_solde": 4990, "tokens_utilises": 10, "plan": "pro"}
        ):
            status, data = call(srv_main.chat, payload, authorization="Bearer jwt-demo")

        self.assertEqual(status, 200)
        self.assertEqual(data["solde_restant"], 4990)
        self.assertEqual(data["plan"], "pro")

    def test_solde_epuise_retourne_solde_insuffisant(self):
        payload = srv_main.ChatIn(message="bonjour")
        pauvre = {**PROFIL_VALIDE, "tokens_solde": 0}
        with mock.patch.object(srv_device, "verifier_token", return_value=pauvre), mock.patch.object(
            srv_gemini, "appeler_gemini"
        ) as gemini_call:
            status, data = call(srv_main.chat, payload, authorization="Bearer jwt-demo")

        self.assertEqual(status, 402)
        self.assertEqual(data["status"], "solde_insuffisant")
        gemini_call.assert_not_called()

    def test_solde_endpoint(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE)):
            status, data = call(srv_main.solde, authorization="Bearer jwt-demo")
        self.assertEqual(status, 200)
        self.assertEqual(data["tokens_solde"], 5000)
        self.assertEqual(data["tokens_utilises"], 0)


PAIEMENT = {"session_id": "s1", "user_id": "u1", "plan": "starter", "tokens": 50000,
            "montant_fcfa": 500, "statut": "en_attente"}
PAYE = {"status": "PAID", "transaction_status": "SUCCESS"}
SESSION_OK = {"amount": "500.00", "currency": "XOF", "metadata": {"user_id": "u1", "plan": "starter"}}


class FauxRequest:
    def __init__(self, corps: bytes, entetes: dict):
        self._corps, self.headers = corps, entetes

    async def body(self):
        return self._corps


def signer(corps: bytes, secret: str, ts: int) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + corps, hashlib.sha256).hexdigest()


@skip_serveur
class PaiementTests(unittest.TestCase):
    def lancer(self, paiement=None, statut=None, details=None, reserve=True, credit_echoue=False):
        paiement = dict(PAIEMENT) if paiement is None else paiement
        with contextlib.ExitStack() as pile:
            m = {}
            pile.enter_context(mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE)))
            pile.enter_context(mock.patch.object(srv_device, "lire_paiement", return_value=paiement))
            pile.enter_context(mock.patch.object(srv_saspay, "statut_session", return_value=PAYE if statut is None else statut))
            pile.enter_context(mock.patch.object(srv_saspay, "lire_session", return_value=dict(SESSION_OK) if details is None else details))
            m["reserve"] = pile.enter_context(mock.patch.object(srv_device, "reserver_paiement", return_value=reserve))
            m["credit"] = pile.enter_context(mock.patch.object(
                srv_device, "crediter_profil",
                side_effect=RuntimeError("boom") if credit_echoue else None,
                return_value={"tokens_solde": 55000}))
            m["achat"] = pile.enter_context(mock.patch.object(srv_device, "enregistrer_achat"))
            m["anomalie"] = pile.enter_context(mock.patch.object(srv_device, "marquer_anomalie"))
            m["libere"] = pile.enter_context(mock.patch.object(srv_device, "liberer_paiement"))
            status, data = call(srv_main.confirmer_paiement, srv_main.PaiementIn(session_id="s1"), authorization="Bearer jwt-demo")
        return status, data, m

    def test_paiement_valide_credite_les_tokens(self):
        status, data, m = self.lancer()
        self.assertEqual((status, data["status"], data["tokens_ajoutes"]), (200, "confirme", 50000))
        m["credit"].assert_called_once_with("u1", 50000, "starter")
        m["achat"].assert_called_once_with("u1", "starter", 50000, 500, "s1")

    def test_deuxieme_appel_ne_credite_pas_deux_fois(self):
        status, data, m = self.lancer(reserve=False)
        self.assertEqual(data["status"], "deja_credite")
        m["credit"].assert_not_called()

    def test_paiement_deja_credite_localement(self):
        status, data, m = self.lancer(paiement={**PAIEMENT, "statut": "credite"})
        self.assertEqual(data["status"], "deja_credite")
        m["reserve"].assert_not_called()

    def test_paiement_non_paye_est_rejete(self):
        status, data, m = self.lancer(statut={"status": "PENDING", "transaction_status": "PENDING"})
        self.assertEqual((status, data["status"], data["etat"]), (402, "paiement_non_valide", "pending"))
        m["credit"].assert_not_called()

    def test_session_payee_sans_transaction_success_est_rejetee(self):
        status, data, m = self.lancer(statut={"status": "PAID", "transaction_status": "FAILED"})
        self.assertEqual(status, 402)

    def test_montant_incoherent_est_bloque(self):
        status, data, m = self.lancer(details={**SESSION_OK, "amount": "100.00"})
        self.assertEqual((status, data["status"]), (409, "paiement_incoherent"))
        m["anomalie"].assert_called_once_with("s1")
        m["credit"].assert_not_called()

    def test_session_d_un_autre_utilisateur_est_introuvable(self):
        status, data, m = self.lancer(paiement={**PAIEMENT, "user_id": "u2"})
        self.assertEqual(status, 404)
        m["credit"].assert_not_called()

    def test_echec_du_credit_reouvre_le_paiement(self):
        status, data, m = self.lancer(credit_echoue=True)
        self.assertEqual(data.status_code, 503)
        m["libere"].assert_called_once_with("s1")
        m["achat"].assert_not_called()

    def test_creer_paiement_utilise_le_prix_du_serveur(self):
        profil = dict(PROFIL_VALIDE, email="awa@example.com", nom="Awa")
        with mock.patch.object(srv_device, "verifier_token", return_value=profil), mock.patch.object(
            srv_device, "lire_plan", return_value={"nom": "starter", "tokens": 50000, "prix_fcfa": 500}
        ), mock.patch.object(
            srv_saspay, "creer_session", return_value={"id": "s1", "checkout_url": "https://pay.saspay.me/c/x"}
        ) as creer, mock.patch.object(srv_device, "creer_paiement") as enreg:
            status, data = call(srv_main.paiement_creer, srv_main.PaiementCreerIn(plan="starter"), authorization="Bearer jwt-demo")
        self.assertEqual((status, data["checkout_url"], data["montant"]), (200, "https://pay.saspay.me/c/x", 500))
        self.assertEqual(creer.call_args.kwargs["montant"], 500)
        self.assertEqual(creer.call_args.kwargs["metadata"], {"user_id": profil["user_id"], "plan": "starter"})
        enreg.assert_called_once_with(profil["user_id"], "starter", 50000, 500, "s1")

    def test_plan_gratuit_non_payable(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE, email="a@b.c")), mock.patch.object(
            srv_device, "lire_plan", return_value={"nom": "gratuit", "tokens": 5000, "prix_fcfa": 0}
        ):
            status, data = call(srv_main.paiement_creer, srv_main.PaiementCreerIn(plan="gratuit"), authorization="Bearer jwt-demo")
        self.assertEqual(status, 400)


@skip_serveur
class SasPayTests(unittest.TestCase):
    ENV = {"SASPAY_SECRET_KEY": "sk_test_abc", "SASPAY_WEBHOOK_SECRET": "whsec_x"}

    def test_signature_valide_fausse_et_trop_ancienne(self):
        corps, ts = b'{"event":"transaction.success"}', 1_000_000
        bonne = signer(corps, "whsec_x", ts)
        self.assertTrue(srv_saspay.verifier_signature(corps, bonne, str(ts), "whsec_x", maintenant=ts + 10))
        self.assertFalse(srv_saspay.verifier_signature(corps + b" ", bonne, str(ts), "whsec_x", maintenant=ts + 10))
        self.assertFalse(srv_saspay.verifier_signature(corps, bonne, str(ts), "autre", maintenant=ts + 10))
        self.assertFalse(srv_saspay.verifier_signature(corps, bonne, str(ts), "whsec_x", maintenant=ts + 301))
        self.assertFalse(srv_saspay.verifier_signature(corps, bonne, "pas-un-nombre", "whsec_x"))

    def test_est_paye_exige_session_et_transaction(self):
        self.assertTrue(srv_saspay.est_paye(PAYE))
        self.assertFalse(srv_saspay.est_paye({"status": "PAID"}))
        self.assertFalse(srv_saspay.est_paye({"status": "PENDING", "transaction_status": "SUCCESS"}))

    def test_meme_montant(self):
        self.assertTrue(srv_saspay.meme_montant("500.00", 500))
        self.assertFalse(srv_saspay.meme_montant("499.99", 500))
        self.assertFalse(srv_saspay.meme_montant("abc", 500))

    def test_creer_session_enveloppe_ou_objet_seul(self):
        for corps in ({"success": True, "code": 201, "data": {"id": "s1", "checkout_url": "https://pay/x"}},
                      {"id": "s1", "checkout_url": "https://pay/x"}):
            with mock.patch.dict("os.environ", self.ENV), mock.patch.object(srv_saspay.requests, "request") as req:
                req.return_value.ok = True
                req.return_value.json.return_value = corps
                session = srv_saspay.creer_session(montant=500, description="d", email="a@b.c", nom="Awa")
            self.assertEqual(session["id"], "s1")
            envoye = req.call_args.kwargs["json"]
            self.assertEqual((envoye["amount"], envoye["currency"]), ("500.00", "XOF"))
            self.assertEqual(req.call_args.kwargs["headers"]["Authorization"], "Bearer sk_test_abc")

    def test_erreur_saspay_ne_fuit_pas_la_cle(self):
        with mock.patch.dict("os.environ", self.ENV), mock.patch.object(srv_saspay.requests, "request") as req:
            req.return_value.ok = False
            req.return_value.status_code = 401
            req.return_value.json.return_value = {"success": False, "error": {"message": "Clé invalide", "code": "x"}}
            with self.assertRaises(srv_saspay.SasPayError) as ctx:
                srv_saspay.statut_session("s1")
        self.assertNotIn("sk_test_abc", str(ctx.exception))

    def test_webhook_signature_invalide_refuse(self):
        req = FauxRequest(b"{}", {"X-Webhook-Signature": "faux", "X-Webhook-Timestamp": "1"})
        with mock.patch.dict("os.environ", self.ENV):
            with self.assertRaises(srv_main.HTTPException) as ctx:
                asyncio.run(srv_main.webhook_saspay(req))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_webhook_valide_declenche_la_reconciliation(self):
        import time
        corps, ts = b'{"event":"transaction.success","data":{}}', int(time.time())
        req = FauxRequest(corps, {"X-Webhook-Signature": signer(corps, "whsec_x", ts), "X-Webhook-Timestamp": str(ts)})
        with mock.patch.dict("os.environ", self.ENV), mock.patch.object(
            srv_main, "_reconcilier", return_value={"ok": True, "credites": 1}
        ) as rec:
            data = asyncio.run(srv_main.webhook_saspay(req))
        self.assertEqual(data["credites"], 1)
        rec.assert_called_once()


class WebhookRattrapageTests(unittest.TestCase):
    """Problème 2 : identifiant du webhook, rattrapage et /confirmer-paiement sans id."""

    ENV = {"SASPAY_SECRET_KEY": "sk_test_abc", "SASPAY_WEBHOOK_SECRET": "whsec_x"}

    def _requete_webhook(self, corps: bytes) -> FauxRequest:
        import time
        ts = int(time.time())
        return FauxRequest(
            corps,
            {"X-Webhook-Signature": signer(corps, "whsec_x", ts), "X-Webhook-Timestamp": str(ts)},
        )

    def test_session_id_du_webhook_formes_variees(self):
        self.assertEqual(srv_main._session_id_du_webhook({"session_id": "s1"}), "s1")
        self.assertEqual(srv_main._session_id_du_webhook({"data": {"session_id": "s1"}}), "s1")
        self.assertEqual(srv_main._session_id_du_webhook({"data": {"checkout_session": {"id": "s1"}}}), "s1")
        self.assertEqual(srv_main._session_id_du_webhook({}), "")

    def test_webhook_avec_identifiant_traite_ce_paiement_seulement(self):
        corps = json.dumps({"event": "transaction.success", "data": {"session_id": "s1"}}).encode()
        req = self._requete_webhook(corps)
        with mock.patch.dict("os.environ", self.ENV), mock.patch.object(
            srv_device, "lire_paiement", return_value=dict(PAIEMENT)
        ) as lire, mock.patch.object(
            srv_main, "_traiter_paiement", return_value={"status": "confirme"}
        ) as traiter, mock.patch.object(srv_main, "_reconcilier") as rec:
            data = asyncio.run(srv_main.webhook_saspay(req))
        self.assertEqual(data, {"ok": True, "credites": 1})
        lire.assert_called_once_with("s1")
        traiter.assert_called_once()
        rec.assert_not_called()

    def test_webhook_sans_identifiant_lance_le_rattrapage(self):
        corps = b'{"event":"transaction.success","data":{}}'
        req = self._requete_webhook(corps)
        with mock.patch.dict("os.environ", self.ENV), mock.patch.object(
            srv_main, "_reconcilier", return_value={"ok": True, "credites": 2}
        ) as rec:
            data = asyncio.run(srv_main.webhook_saspay(req))
        self.assertEqual(data["credites"], 2)
        rec.assert_called_once_with(30)

    def test_reconcilier_utilise_30_paiements_par_defaut(self):
        with mock.patch.object(srv_device, "paiements_en_attente", return_value=[]) as en_attente:
            data = srv_main._reconcilier()
        en_attente.assert_called_once_with(limite=30)
        self.assertEqual(data, {"ok": True, "credites": 0})

    def test_paiements_en_attente_30_sur_3_jours(self):
        import urllib.parse

        env = {"SUPABASE_URL": "https://proj.supabase.co", "SUPABASE_SERVICE_KEY": "service-secret"}
        with mock.patch.dict("os.environ", env), mock.patch.object(
            srv_device.requests, "get"
        ) as get:
            get.return_value.ok = True
            get.return_value.json.return_value = []
            srv_device.paiements_en_attente()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(get.call_args.args[0]).query)
        self.assertEqual(query["limit"], ["30"])
        depuis = dt.datetime.fromisoformat(query["cree_a"][0].removeprefix("gte."))
        ecart = (dt.datetime.now(dt.timezone.utc) - depuis).total_seconds()
        self.assertAlmostEqual(ecart, 3 * 86400, delta=120)

    def test_confirmer_sans_identifiant_utilise_dernier_paiement(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE)), mock.patch.object(
            srv_device, "dernier_paiement", return_value=dict(PAIEMENT)
        ) as dernier, mock.patch.object(srv_device, "lire_paiement") as lire, mock.patch.object(
            srv_main, "_traiter_paiement", return_value={"status": "confirme"}
        ):
            status, data = call(
                srv_main.confirmer_paiement, srv_main.PaiementIn(), authorization="Bearer jwt-demo"
            )
        self.assertEqual((status, data["status"]), (200, "confirme"))
        dernier.assert_called_once_with("u1")
        lire.assert_not_called()

    def test_confirmer_sans_identifiant_ne_credite_pas_paiement_d_autrui(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE)), mock.patch.object(
            srv_device, "dernier_paiement", return_value={**PAIEMENT, "user_id": "u2"}
        ), mock.patch.object(srv_main, "_traiter_paiement") as traiter:
            status, data = call(
                srv_main.confirmer_paiement, srv_main.PaiementIn(), authorization="Bearer jwt-demo"
            )
        self.assertEqual(status, 404)
        traiter.assert_not_called()

    def test_confirmer_avec_identifiant_garde_la_lecture_directe(self):
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE)), mock.patch.object(
            srv_device, "lire_paiement", return_value=dict(PAIEMENT)
        ), mock.patch.object(srv_device, "dernier_paiement") as dernier, mock.patch.object(
            srv_main, "_traiter_paiement", return_value={"status": "confirme"}
        ):
            status, data = call(
                srv_main.confirmer_paiement,
                srv_main.PaiementIn(session_id="s1"),
                authorization="Bearer jwt-demo",
            )
        self.assertEqual(status, 200)
        dernier.assert_not_called()


class CliLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        state = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(cli, "STATE_DIR", state),
            mock.patch.object(cli, "AUTH_PATH", state / "auth.json"),
            mock.patch.object(cli, "CONFIG_PATH", state / "config.json"),
            mock.patch.object(cli, "HANDSHAKE_PATH", state / "handshake.json"),
            mock.patch("darkcodex.licence.DATA_PATH", state / "data.json"),
            mock.patch("time.sleep"),
        ]
        for patcher in self.patches:
            patcher.start()
        self.auth_path = state / "auth.json"

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def _run(self, func, *args):
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            result = func(*args)
        return result, sortie.getvalue()

    def test_premier_lancement_affiche_le_code_et_connecte(self):
        reponses = [
            (200, {"code": "DARK-4829", "lien": "https://darkcodex.onrender.com/device", "expire_dans": 600}),
            (200, {"status": "en_attente"}),
            (
                200,
                {
                    "status": "confirme",
                    "token": "jwt-demo",
                    "email": "moussa@gmail.com",
                    "nom": "Moussa",
                    "tokens_solde": 5000,
                    "plan": "gratuit",
                },
            ),
        ]
        with mock.patch.object(cli, "api_call", side_effect=reponses), mock.patch.object(
            cli.navigateur, "ouvrir", return_value=False
        ):
            session, output = self._run(cli.device_login)

        self.assertIsNotNone(session)
        self.assertIn("🔐 Connexion à DarkCodex", output)
        self.assertIn("→ https://darkcodex.onrender.com/device?code=DARK-4829", output)
        self.assertIn("Code affiché sur la page : DARK-4829", output)
        self.assertIn("⏳ En attente de confirmation...", output)
        self.assertIn("✅ Connexion réussie ! Bienvenue Moussa !", output)
        self.assertIn("💰 Tokens gratuits : 5 000 tokens", output)
        self.assertIn(".", output)

        sauvegarde = json.loads(self.auth_path.read_text())
        self.assertEqual(sauvegarde["token"], "jwt-demo")
        self.assertEqual(sauvegarde["plan"], "gratuit")

    def test_code_expire_affiche_le_message_impose(self):
        reponses = [
            (200, {"code": "DARK-4829", "lien": "https://darkcodex.onrender.com/device", "expire_dans": 600}),
            (200, {"status": "expire"}),
        ]
        with mock.patch.object(cli, "api_call", side_effect=reponses), mock.patch.object(
            cli.navigateur, "ouvrir", return_value=False
        ):
            session, output = self._run(cli.device_login)

        self.assertIsNone(session)
        self.assertIn("Code expiré. Tapez /login pour réessayer", output)

    def test_lancement_suivant_valide_le_jeton_et_affiche_le_solde(self):
        self.auth_path.write_text(
            json.dumps(
                {"token": "jwt-demo", "email": "moussa@gmail.com", "nom": "Moussa", "tokens_solde": 4850, "plan": "gratuit"}
            )
        )
        with mock.patch.object(
            cli,
            "api_call",
            return_value=(
                200,
                {
                    "user_id": "u1",
                    "email": "moussa@gmail.com",
                    "nom": "Moussa",
                    "tokens_solde": 4850,
                    "tokens_utilises": 150,
                    "plan": "gratuit",
                },
            ),
        ):
            session, output = self._run(cli.ensure_session)

        self.assertIsNotNone(session)
        # C01 : la version vient de darkcodex.__version__ (plus de durcissement).
        self.assertIn(f"DarkCodex — Unrestricted AI v{cli.__version__}", output)
        self.assertIn("Bonjour Moussa ! 👋", output)
        self.assertIn("💰 Solde : 4 850 tokens restants", output)
        self.assertIn("📦 Plan : Gratuit", output)

    def test_jeton_invalide_efface_auth_json(self):
        self.auth_path.write_text(json.dumps({"token": "jwt-demo"}))
        with mock.patch.object(cli, "api_call", return_value=(401, {"detail": "Token invalide ou expiré."})):
            session, _ = self._run(cli.ensure_session, False)

        self.assertIsNone(session)
        self.assertFalse(self.auth_path.exists())

    def test_serveur_hors_ligne_conserve_le_jeton(self):
        self.auth_path.write_text(json.dumps({"token": "jwt-demo", "nom": "Moussa"}))
        with mock.patch.object(cli, "api_call", return_value=(0, {"detail": "Serveur injoignable."})):
            session, output = self._run(cli.ensure_session, False)

        self.assertIsNotNone(session)
        self.assertTrue(self.auth_path.exists())
        self.assertIn("Serveur injoignable", output)

    def test_logout_supprime_le_fichier(self):
        self.auth_path.write_text(json.dumps({"token": "jwt-demo"}))
        cli.clear_auth()
        self.assertFalse(self.auth_path.exists())

    def test_messages_imposes_solde_epuise_et_ligne_de_tokens(self):
        _, sortie = self._run(cli.show_exhausted)
        self.assertIn("⛔ Vos tokens gratuits sont épuisés !", sortie)
        self.assertIn("→ https://darkcodex.onrender.com/abonnements", sortie)
        self.assertIn("⭐ Starter → 50 000 tokens = 500 FCFA", sortie)
        self.assertIn("🚀 Pro → 500 000 tokens = 2 500 FCFA", sortie)

        _, sortie = self._run(cli.show_token_line, 150, 4850)
        self.assertIn("💬 Tokens utilisés : 150 | 💰 Restants : 4 850", sortie)

        self.auth_path.write_text(json.dumps({"token": "jwt-demo"}))
        with mock.patch.object(
            cli, "api_call", return_value=(200, {"tokens_solde": 4850, "tokens_utilises": 150, "plan": "gratuit"})
        ):
            _, sortie = self._run(cli.show_solde)
        self.assertIn("💰 Votre solde DarkCodex", sortie)
        self.assertIn("Tokens restants : 4 850", sortie)
        self.assertIn("Tokens utilisés : 150", sortie)
        self.assertIn("Plan actuel : Gratuit", sortie)

    def test_server_answer_remonte_solde_insuffisant(self):
        with mock.patch.object(
            cli, "api_call", return_value=(402, {"detail": {"status": "solde_insuffisant"}})
        ):
            code, texte, data = cli.server_answer({"token": "jwt-demo"}, "bonjour")

        self.assertEqual(code, 402)
        self.assertEqual(data["detail"]["status"], "solde_insuffisant")


@skip_serveur
class SupabaseRestTests(unittest.TestCase):
    """Vérifie l'appel réel à l'API REST Supabase (URL, en-têtes, payload)."""

    ENV = {"SUPABASE_URL": "https://proj.supabase.co", "SUPABASE_SERVICE_KEY": "svc-key"}

    def test_sauvegarde_code_avec_expiration_10_min(self):
        with mock.patch.dict("os.environ", self.ENV, clear=False), mock.patch.object(
            srv_device.requests, "post"
        ) as post:
            post.return_value.ok = True
            post.return_value.status_code = 201
            srv_device.sauvegarder_code("DARK-4829")

        self.assertEqual(post.call_args.args[0], "https://proj.supabase.co/rest/v1/device_codes")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["apikey"], "svc-key")
        self.assertEqual(headers["Authorization"], "Bearer svc-key")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["code"], "DARK-4829")
        expire = dt.datetime.fromisoformat(payload["expire_a"])
        reste = (expire - dt.datetime.now(dt.timezone.utc)).total_seconds()
        self.assertGreater(reste, 9 * 60)
        self.assertLess(reste, 10 * 60 + 30)

    def test_confirme_code_par_requete_patch(self):
        with mock.patch.dict("os.environ", self.ENV, clear=False), mock.patch.object(
            srv_device.requests, "patch"
        ) as patch:
            patch.return_value.ok = True
            patch.return_value.json.return_value = [{"code": "DARK-4829"}]
            ok = srv_device.confirmer_code("dark-4829", "u1", "jwt-demo")

        self.assertTrue(ok)
        self.assertTrue(
            patch.call_args.args[0].startswith(
                "https://proj.supabase.co/rest/v1/device_codes?code=eq.DARK-4829&confirme=eq.false&expire_a=gt."
            )
        )
        self.assertEqual(
            patch.call_args.kwargs["json"], {"confirme": True, "user_id": "u1", "token": "jwt-demo"}
        )

    def test_nettoyage_des_codes_expires(self):
        with mock.patch.dict("os.environ", self.ENV, clear=False), mock.patch.object(
            srv_device.requests, "delete"
        ) as delete:
            delete.return_value.ok = True
            delete.return_value.json.return_value = [{"id": 1}, {"id": 2}]
            supprimes = srv_device.nettoyer_codes_expires()

        self.assertEqual(supprimes, 2)
        self.assertIn("expire_a=lt.", delete.call_args.args[0])
        self.assertTrue(delete.call_args.args[0].startswith("https://proj.supabase.co/rest/v1/device_codes?"))

    def test_code_expire_renvoie_expire(self):
        passe = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat(timespec="seconds")
        with mock.patch.dict("os.environ", self.ENV, clear=False), mock.patch.object(
            srv_device, "_lire_code", return_value={"confirme": False, "expire_a": passe}
        ):
            self.assertEqual(srv_device.verifier_code("DARK-4829")["status"], "expire")

    def test_enregistre_achat_et_reserve_une_seule_fois(self):
        with mock.patch.dict("os.environ", self.ENV, clear=False), mock.patch.object(
            srv_device.requests, "patch"
        ) as patch:
            patch.return_value.ok = True
            patch.return_value.json.return_value = [{"id": "p1"}]
            self.assertTrue(srv_device.reserver_paiement("s1"))
            self.assertIn("statut=eq.en_attente", patch.call_args.args[0])
            patch.return_value.json.return_value = []
            self.assertFalse(srv_device.reserver_paiement("s1"))

        with mock.patch.dict("os.environ", self.ENV, clear=False), mock.patch.object(
            srv_device.requests, "post"
        ) as post:
            post.return_value.ok = True
            srv_device.enregistrer_achat("u1", "starter", 50000, 500, "tx-1")

        self.assertEqual(post.call_args.args[0], "https://proj.supabase.co/rest/v1/achats")
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "user_id": "u1",
                "plan": "starter",
                "tokens_ajoutes": 50000,
                "montant_fcfa": 500,
                "transaction_id": "tx-1",
            },
        )


@skip_serveur
class GeminiTests(unittest.TestCase):
    def test_historique_converti_en_roles_gemini(self):
        contenu = srv_gemini._history_contents(
            [{"role": "user", "content": "salut"}, {"role": "assistant", "content": "bonjour"}], "encore"
        )
        self.assertEqual(
            [item["role"] for item in contenu], ["user", "model", "user"]
        )
        self.assertEqual(contenu[-1]["parts"][0]["text"], "encore")

    def test_modele_par_defaut(self):
        self.assertEqual(srv_gemini.model(), "gemini-2.5-flash")


if __name__ == "__main__":
    unittest.main()
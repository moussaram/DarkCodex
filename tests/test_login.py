"""Tests du système de connexion (device code, jeton, solde, paiement).

Ces tests couvrent les 3 cas imposés par la spec :
  1. premier lancement (pas de auth.json)
  2. lancement suivant (auth.json valide)
  3. tokens épuisés
"""

import contextlib
import datetime as dt
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
    SERVEUR_OK, SERVEUR_ABSENTE = True, ""
except ImportError as exc:  # fastapi/requests du serveur non installés
    srv_device = srv_gemini = srv_main = None  # type: ignore[assignment]
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
        self.assertRegex(code, r"^DARK-[A-Z0-9]{4}$")

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
        with mock.patch.object(srv_device, "_lire_code", return_value=avec_token), mock.patch.object(
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
        ), mock.patch.object(srv_device, "confirmer_code", return_value=True) as confirmer:
            status, data = call(srv_main.device_confirm, payload, authorization="Bearer jwt-demo")

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "confirme")
        confirmer.assert_called_once_with("DARK-4829", "u1", "jwt-demo")

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
            srv_device, "maj_profil", return_value={"tokens_solde": 4850, "tokens_utilises": 150}
        ) as maj:
            status, data = call(srv_main.chat, payload, authorization="Bearer jwt-demo")

        self.assertEqual(status, 200)
        self.assertEqual(data["reponse"], "Réponse Gemini")
        self.assertEqual(data["tokens_utilises"], 150)
        self.assertEqual(data["solde_restant"], 4850)
        self.assertEqual(gemini_call.call_args.args[0], "explique ce projet")
        maj.assert_called_once_with("u1", {"tokens_solde": 4850, "tokens_utilises": 150})

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


@skip_serveur
class PaiementTests(unittest.TestCase):
    def test_paiement_approuve_credite_les_tokens(self):
        payload = srv_main.PaiementIn(transaction_id="tx-1", plan="starter", user_id="u1")
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE)), mock.patch.object(
            srv_device, "lire_plan", return_value={"nom": "starter", "tokens": 50000, "prix_fcfa": 500}
        ), mock.patch.object(
            srv_main, "_transaction_fedapay", return_value={"status": "approved"}
        ), mock.patch.object(
            srv_device, "lire_profil", return_value={"tokens_solde": 5000}
        ), mock.patch.object(
            srv_device, "maj_profil", return_value={"tokens_solde": 55000}
        ) as maj, mock.patch.object(
            srv_device, "enregistrer_achat"
        ) as achat, mock.patch.object(
            srv_device, "deja_credite", return_value=False
        ):
            status, data = call(srv_main.confirmer_paiement, payload, authorization="Bearer jwt-demo")

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "confirme")
        self.assertEqual(data["tokens_ajoutes"], 50000)
        maj.assert_called_once_with("u1", {"tokens_solde": 55000, "plan": "starter"})
        achat.assert_called_once_with("u1", "starter", 50000, 500, "tx-1")

    def test_paiement_deja_credite_n_est_pas_rejoue(self):
        payload = srv_main.PaiementIn(transaction_id="tx-1", plan="starter", user_id="u1")
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE)), mock.patch.object(
            srv_device, "lire_plan", return_value={"nom": "starter", "tokens": 50000, "prix_fcfa": 500}
        ), mock.patch.object(
            srv_main, "_transaction_fedapay", return_value={"status": "approved"}
        ), mock.patch.object(
            srv_device, "lire_profil", return_value={"tokens_solde": 55000}
        ), mock.patch.object(
            srv_device, "maj_profil"
        ) as maj, mock.patch.object(
            srv_device, "deja_credite", return_value=True
        ):
            status, data = call(srv_main.confirmer_paiement, payload, authorization="Bearer jwt-demo")

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "deja_credite")
        maj.assert_not_called()

    def test_paiement_non_approuve_est_rejete(self):
        payload = srv_main.PaiementIn(transaction_id="tx-2", plan="pro")
        with mock.patch.object(srv_device, "verifier_token", return_value=dict(PROFIL_VALIDE)), mock.patch.object(
            srv_device, "lire_plan", return_value={"nom": "pro", "tokens": 500000, "prix_fcfa": 2500}
        ), mock.patch.object(srv_main, "_transaction_fedapay", return_value={"status": "declined"}):
            status, data = call(srv_main.confirmer_paiement, payload, authorization="Bearer jwt-demo")

        self.assertEqual(status, 402)
        self.assertEqual(data["status"], "paiement_non_valide")


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
        with mock.patch.object(cli, "api_call", side_effect=reponses):
            session, output = self._run(cli.device_login)

        self.assertIsNotNone(session)
        self.assertIn("🔐 Connexion à DarkCodex", output)
        self.assertIn("→ https://darkcodex.onrender.com/device", output)
        self.assertIn("2. Entrez ce code : DARK-4829", output)
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
        with mock.patch.object(cli, "api_call", side_effect=reponses):
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
        self.assertEqual(
            patch.call_args.args[0],
            "https://proj.supabase.co/rest/v1/device_codes?code=eq.DARK-4829",
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

    def test_deja_credite_et_enregistre_achat(self):
        with mock.patch.dict("os.environ", self.ENV, clear=False), mock.patch.object(
            srv_device.requests, "get"
        ) as get:
            get.return_value.ok = True
            get.return_value.json.return_value = [{"id": "a1"}]
            self.assertTrue(srv_device.deja_credite("tx-1"))
            self.assertIn("transaction_id=eq.tx-1", get.call_args.args[0])

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
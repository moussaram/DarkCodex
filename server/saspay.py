"""Client minimal pour l'API SasPay (https://docs.saspay.me).

Utilisé uniquement côté serveur : la clé secrète SASPAY_SECRET_KEY ne doit
jamais apparaître dans le client (terminal, navigateur, application mobile).

Flux utilisé :
  1. creer_session()  → page de paiement hébergée (checkout_url)
  2. statut_session() → SasPay revérifie l'état réel côté passerelle
  3. lire_session()   → montant, devise et métadonnées de la session
  4. verifier_signature() → contrôle des webhooks (HMAC SHA-256)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

DEFAULT_API_BASE = "https://api.saspay.me/api/v1"
DEVISE = "XOF"  # FCFA (Bénin, Côte d'Ivoire, Sénégal…)
TIMEOUT_SECONDS = 15
WEBHOOK_TOLERANCE_SECONDS = 300  # 5 minutes, comme demandé par SasPay


class SasPayError(RuntimeError):
    """Erreur lisible remontée vers l'API (message sans secret)."""


def api_base() -> str:
    return (os.environ.get("SASPAY_API_BASE", "").strip() or DEFAULT_API_BASE).rstrip("/")


def secret_key() -> str:
    key = os.environ.get("SASPAY_SECRET_KEY", "").strip()
    if not key:
        raise SasPayError("SASPAY_SECRET_KEY manquante (voir server/.env).")
    return key


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {secret_key()}", "Content-Type": "application/json"}


def _donnees(body: Any) -> dict[str, Any]:
    """Les réponses sont documentées avec une enveloppe {success, data, code},
    mais les exemples de checkout montrent l'objet seul : on accepte les deux."""
    if isinstance(body, dict) and "success" in body and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


def _message_erreur(body: Any, code: int) -> str:
    if isinstance(body, dict):
        erreur = body.get("error", body)
        if isinstance(erreur, dict):
            texte = erreur.get("message") or erreur.get("code") or erreur.get("detail")
            if texte:
                return str(texte)[:200]
    return f"HTTP {code}"


def _appel(methode: str, chemin: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = requests.request(
            methode,
            f"{api_base()}{chemin}",
            headers=_headers(),
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise SasPayError(f"SasPay injoignable: {type(exc).__name__}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not response.ok:
        raise SasPayError(f"SasPay a refusé la requête ({_message_erreur(body, response.status_code)}).")
    return _donnees(body)


def creer_session(
    *,
    montant: int,
    description: str,
    email: str,
    nom: str,
    return_url: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Crée la page de paiement hébergée. Retourne la session (id, checkout_url…)."""
    payload: dict[str, Any] = {
        "amount": f"{int(montant)}.00",
        "currency": DEVISE,
        "description": description,
        "customer_email": email,
        "customer_name": nom,
        "metadata": metadata or {},
    }
    if return_url:
        payload["return_url"] = return_url
    session = _appel("POST", "/checkout-sessions/", payload)
    if not session.get("id") or not session.get("checkout_url"):
        raise SasPayError("Réponse SasPay incomplète (id ou checkout_url absent).")
    return session


def lire_session(session_id: str) -> dict[str, Any]:
    return _appel("GET", f"/checkout-sessions/{session_id}/")


def statut_session(session_id: str) -> dict[str, Any]:
    """SasPay revérifie l'état réel côté passerelle avant de répondre."""
    return _appel("GET", f"/checkout-sessions/{session_id}/status/")


def est_paye(statut: dict[str, Any]) -> bool:
    """Payé = session PAID ET transaction SUCCESS (jamais l'un sans l'autre)."""
    return (
        str(statut.get("status") or "").upper() == "PAID"
        and str(statut.get("transaction_status") or "").upper() == "SUCCESS"
    )


def meme_montant(valeur: Any, attendu: int) -> bool:
    try:
        return Decimal(str(valeur)) == Decimal(int(attendu))
    except (InvalidOperation, ValueError, TypeError):
        return False


def webhook_secret() -> str:
    secret = os.environ.get("SASPAY_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise SasPayError("SASPAY_WEBHOOK_SECRET manquante (voir server/.env).")
    return secret


def verifier_signature(
    corps_brut: bytes,
    signature: str,
    horodatage: str,
    secret: str,
    maintenant: float | None = None,
) -> bool:
    """Contrôle d'âge (5 min) PUIS signature HMAC calculée sur « horodatage.corps »."""
    try:
        ts = int(horodatage)
    except (TypeError, ValueError):
        return False
    now = time.time() if maintenant is None else maintenant
    if abs(int(now) - ts) > WEBHOOK_TOLERANCE_SECONDS:
        return False
    signe = f"{horodatage}.".encode() + corps_brut
    attendu = hmac.new(secret.encode(), signe, hashlib.sha256).hexdigest()
    return hmac.compare_digest(attendu, (signature or "").strip().lower())

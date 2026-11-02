from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import string
from typing import Any

import requests

APPAREIL_PREFIX = "dkx_"
CODE_TTL_SECONDS = int(os.environ.get("DARKCODEX_CODE_TTL_SECONDS", "600"))
PUBLIC_URL = (
    os.environ.get("DARKCODEX_PUBLIC_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or "https://darkcodex-technicien.onrender.com"
).strip().rstrip("/")
DEVICE_PAGE_URL = os.environ.get("DARKCODEX_DEVICE_URL", PUBLIC_URL + "/device")

_ALLOWED_CODE_CHARS = string.ascii_uppercase.replace("I", "").replace("O", "") + "23456789"
_ALLOWED_CODE_CHARS = "".join(ch for ch in _ALLOWED_CODE_CHARS if ch not in {"I", "O"})


def supabase_url() -> str:
    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    if not url:
        raise RuntimeError("SUPABASE_URL manquante.")
    return url


def service_key() -> str:
    key = (os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_KEY manquante.")
    return key


def _rest_headers() -> dict[str, str]:
    key = service_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _rest_url(table: str, query: str = "") -> str:
    base = f"{supabase_url()}/rest/v1/{table.lstrip('/')}"
    if query:
        return f"{base}?{query}"
    return base


def _iso_utc(now: dt.datetime | None = None, seconds: int = 0) -> str:
    value = (now or dt.datetime.now(dt.timezone.utc)) + dt.timedelta(seconds=seconds)
    return value.isoformat(timespec="seconds")


def _parse_iso(value: Any) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_or_none(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _canon_code(code: str) -> str:
    return (code or "").strip().upper()


def generer_code() -> str:
    """Génère un code de connexion terminal au format DARK-XXXX-XXXX."""
    part1 = "".join(secrets.choice(_ALLOWED_CODE_CHARS) for _ in range(4))
    part2 = "".join(secrets.choice(_ALLOWED_CODE_CHARS) for _ in range(4))
    return f"DARK-{part1}-{part2}"


def sauvegarder_code(code: str) -> bool:
    payload = {
        "code": _canon_code(code),
        "confirme": False,
        "expire_a": _iso_utc(seconds=CODE_TTL_SECONDS),
        "user_id": None,
        "token": None,
    }
    response = requests.post(
        _rest_url("device_codes"),
        headers=_rest_headers(),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Impossible de sauvegarder le code device ({response.status_code}).")
    return True


def nettoyer_codes_expires() -> int:
    cutoff = _iso_utc(seconds=0)
    response = requests.delete(
        _rest_url("device_codes", f"expire_a=lt.{cutoff}"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Impossible de nettoyer les codes expirés ({response.status_code}).")
    rows = _json_or_none(response) or []
    if isinstance(rows, list):
        return len(rows)
    return 0


def supprimer_code(code: str) -> bool:
    code = _canon_code(code)
    response = requests.delete(
        _rest_url("device_codes", f"code=eq.{code}"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return False
    return True


def _lire_code(code: str) -> dict[str, Any] | None:
    code = _canon_code(code)
    response = requests.get(
        _rest_url("device_codes", f"code=eq.{code}&select=*"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return None
    rows = _json_or_none(response) or []
    if not isinstance(rows, list):
        return None
    return rows[0] if rows else None


def code_en_attente(code: str) -> bool:
    ligne = _lire_code(code)
    if not ligne:
        return False
    if bool(ligne.get("confirme")):
        return False
    expire_a = ligne.get("expire_a")
    if expire_a:
        exp = _parse_iso(expire_a)
        if exp and exp <= dt.datetime.now(dt.timezone.utc):
            return False
    return True


def verifier_code(code: str) -> dict[str, Any]:
    ligne = _lire_code(code)
    if not ligne:
        return {"status": "expire"}
    if bool(ligne.get("confirme")):
        token = str(ligne.get("token") or "").strip()
        if not token:
            supprimer_code(code)
            return {"status": "expire"}
        try:
            profil = verifier_token(token)
        except Exception:
            profil = None
        if not profil:
            supprimer_code(code)
            return {"status": "expire"}
        supprimer_code(code)
        return {
            "status": "confirme",
            "token": token,
            "user_id": profil.get("user_id"),
            "email": profil.get("email"),
            "nom": profil.get("nom"),
            "tokens_solde": profil.get("tokens_solde"),
            "plan": profil.get("plan"),
        }
    expire_a = ligne.get("expire_a")
    if expire_a:
        exp = _parse_iso(expire_a)
        if exp and exp <= dt.datetime.now(dt.timezone.utc):
            return {"status": "expire"}
    return {"status": "en_attente"}


def confirmer_code(code: str, user_id: str, token: str) -> bool:
    code = _canon_code(code)
    token = (token or "").strip()
    payload = {"confirme": True, "user_id": user_id, "token": token}
    response = requests.patch(
        _rest_url("device_codes", f"code=eq.{code}&confirme=eq.false&expire_a=gt.{_iso_utc()}"),
        headers=_rest_headers(),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        return False
    rows = _json_or_none(response) or []
    return isinstance(rows, list) and len(rows) > 0


def creer_code_unique() -> str:
    for _ in range(50):
        code = generer_code()
        if not _lire_code(code):
            sauvegarder_code(code)
            return code
    raise RuntimeError("Impossible de générer un code device unique.")


def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def lire_appareil(token: str) -> dict[str, Any] | None:
    token = (token or "").strip()
    if not token:
        return None
    response = requests.get(
        _rest_url("appareils", f"token_hash=eq.{_hash_token(token)}&select=*"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return None
    rows = _json_or_none(response) or []
    if not isinstance(rows, list):
        return None
    return rows[0] if rows else None


def appareil_de_token(token: str) -> dict[str, Any] | None:
    return lire_appareil(token)


def creer_appareil(user_id: str, email: str, nom: str) -> str:
    token = f"{APPAREIL_PREFIX}{secrets.token_urlsafe(24)}"
    response = requests.post(
        _rest_url("appareils"),
        headers=_rest_headers(),
        json={
            "user_id": user_id,
            "email": email,
            "nom": nom,
            "token_hash": _hash_token(token),
            "revoque": False,
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Impossible d'enregistrer l'appareil ({response.status_code}).")
    return token


def revoquer_appareil(token: str) -> bool:
    appareil = lire_appareil(token)
    if not appareil or not appareil.get("id"):
        return False
    response = requests.patch(
        _rest_url("appareils", f"id=eq.{appareil['id']}"),
        headers=_rest_headers(),
        json={"revoque": True},
        timeout=20,
    )
    return bool(response.ok)


def revoquer_appareil_de(user_id: str, appareil_id: str) -> bool:
    response = requests.patch(
        _rest_url("appareils", f"id=eq.{appareil_id}&user_id=eq.{user_id}"),
        headers=_rest_headers(),
        json={"revoque": True},
        timeout=20,
    )
    if not response.ok:
        return False
    rows = _json_or_none(response) or []
    return bool(isinstance(rows, list) and rows)


def lister_appareils(user_id: str) -> list[dict[str, Any]]:
    response = requests.get(
        _rest_url("appareils", f"user_id=eq.{user_id}&order=cree_a.desc&select=*"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return []
    rows = _json_or_none(response) or []
    return rows if isinstance(rows, list) else []


def lire_profil(user_id: str) -> dict[str, Any] | None:
    response = requests.get(
        _rest_url("profils", f"id=eq.{user_id}&select=*"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return None
    rows = _json_or_none(response) or []
    if not isinstance(rows, list):
        return None
    return rows[0] if rows else None


def maj_profil(user_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload:
        return lire_profil(user_id)
    response = requests.patch(
        _rest_url("profils", f"id=eq.{user_id}"),
        headers=_rest_headers(),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        return None
    rows = _json_or_none(response) or []
    if isinstance(rows, list) and rows:
        return rows[0]
    return lire_profil(user_id)


def debiter_profil(user_id: str, tokens: int) -> dict[str, Any]:
    payload = {"p_user_id": user_id, "p_tokens": max(0, int(tokens))}
    response = requests.post(
        _rest_url("rpc/debiter_tokens"),
        headers=_rest_headers(),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Débit impossible ({response.status_code}).")
    rows = _json_or_none(response) or []
    if isinstance(rows, list) and rows:
        return rows[0]
    if isinstance(rows, dict):
        return rows
    return lire_profil(user_id) or {}


def crediter_profil(user_id: str, tokens: int, plan: str) -> dict[str, Any]:
    payload = {"p_user_id": user_id, "p_tokens": max(0, int(tokens)), "p_plan": str(plan)}
    response = requests.post(
        _rest_url("rpc/crediter_tokens"),
        headers=_rest_headers(),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Crédit impossible ({response.status_code}).")
    rows = _json_or_none(response) or []
    if isinstance(rows, list) and rows:
        return rows[0]
    if isinstance(rows, dict):
        return rows
    return lire_profil(user_id) or {}


def lister_plans() -> list[dict[str, Any]]:
    response = requests.get(
        _rest_url("plans", "select=*&order=tokens.asc"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return []
    rows = _json_or_none(response) or []
    return rows if isinstance(rows, list) else []


def lire_plan(nom: str) -> dict[str, Any] | None:
    response = requests.get(
        _rest_url("plans", f"nom=eq.{nom}&select=*"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return None
    rows = _json_or_none(response) or []
    if not isinstance(rows, list):
        return None
    return rows[0] if rows else None


def lister_achats(user_id: str) -> list[dict[str, Any]]:
    response = requests.get(
        _rest_url("achats", f"user_id=eq.{user_id}&order=date_achat.desc&select=*"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return []
    rows = _json_or_none(response) or []
    return rows if isinstance(rows, list) else []


def achats_de(user_id: str, limite: int = 100) -> list[dict[str, Any]]:
    return lister_achats(user_id)[:limite]


def paiements_de(user_id: str, limite: int = 100) -> list[dict[str, Any]]:
    response = requests.get(
        _rest_url("paiements", f"user_id=eq.{user_id}&order=cree_a.desc&limit={max(1, int(limite))}&select=*"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return []
    rows = _json_or_none(response) or []
    return rows if isinstance(rows, list) else []


def creer_paiement(user_id: str, plan: str, tokens: int, prix_fcfa: int, session_id: str) -> dict[str, Any] | None:
    payload = {
        "user_id": user_id,
        "plan": plan,
        "tokens": int(tokens),
        "montant_fcfa": int(prix_fcfa),
        "session_id": str(session_id),
        "statut": "en_attente",
    }
    response = requests.post(_rest_url("paiements"), headers=_rest_headers(), json=payload, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Impossible d'enregistrer le paiement ({response.status_code}).")
    rows = _json_or_none(response) or []
    if isinstance(rows, list) and rows:
        return rows[0]
    if isinstance(rows, dict):
        return rows
    return payload


def lire_paiement(session_id: str) -> dict[str, Any] | None:
    response = requests.get(
        _rest_url("paiements", f"session_id=eq.{session_id}&select=*"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return None
    rows = _json_or_none(response) or []
    if isinstance(rows, list) and rows:
        return rows[0]
    if isinstance(rows, dict):
        return rows
    return None


def dernier_paiement(user_id: str) -> dict[str, Any] | None:
    response = requests.get(
        _rest_url("paiements", f"user_id=eq.{user_id}&order=cree_a.desc&limit=1&select=*"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return None
    rows = _json_or_none(response) or []
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def paiement_session(session_id: str) -> dict[str, Any] | None:
    return lire_paiement(session_id)


def reserver_paiement(session_id: str) -> bool:
    response = requests.patch(
        _rest_url("paiements", f"session_id=eq.{session_id}&statut=eq.en_attente"),
        headers=_rest_headers(),
        json={"statut": "credite", "credite_a": _iso_utc()},
        timeout=20,
    )
    if not response.ok:
        return False
    rows = _json_or_none(response) or []
    return bool(isinstance(rows, list) and rows)


def liberer_paiement(session_id: str) -> bool:
    response = requests.patch(
        _rest_url("paiements", f"session_id=eq.{session_id}"),
        headers=_rest_headers(),
        json={"statut": "en_attente", "credite_a": None},
        timeout=20,
    )
    return bool(response.ok)


def marquer_anomalie(session_id: str) -> bool:
    response = requests.patch(
        _rest_url("paiements", f"session_id=eq.{session_id}"),
        headers=_rest_headers(),
        json={"statut": "anomalie"},
        timeout=20,
    )
    return bool(response.ok)


def paiements_en_attente(limite: int = 30) -> list[dict[str, Any]]:
    cutoff = _iso_utc(seconds=-3 * 86400)
    response = requests.get(
        _rest_url("paiements", f"statut=eq.en_attente&cree_a=gte.{cutoff}&order=cree_a.asc&limit={max(1, int(limite))}&select=*"),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return []
    rows = _json_or_none(response) or []
    return rows if isinstance(rows, list) else []


def enregistrer_achat(user_id: str, plan: str, tokens: int, prix_fcfa: int, transaction_id: str) -> dict[str, Any] | None:
    payload = {
        "user_id": user_id,
        "plan": plan,
        "tokens_ajoutes": int(tokens),
        "montant_fcfa": int(prix_fcfa),
        "transaction_id": str(transaction_id),
    }
    response = requests.post(_rest_url("achats"), headers=_rest_headers(), json=payload, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Impossible d'enregistrer l'achat ({response.status_code}).")
    rows = _json_or_none(response) or []
    if isinstance(rows, list) and rows:
        return rows[0]
    if isinstance(rows, dict):
        return rows
    return payload


def _nouveau_code_parrainage() -> str:
    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ23456789"
    return "DK-" + "".join(secrets.choice(alphabet) for _ in range(6))


def obtenir_code_parrainage(user_id: str) -> str:
    response = requests.get(
        _rest_url("profils", f"id=eq.{user_id}&select=code_parrainage"),
        headers=_rest_headers(),
        timeout=20,
    )
    if response.ok:
        rows = _json_or_none(response) or []
        if isinstance(rows, list) and rows and rows[0].get("code_parrainage"):
            return str(rows[0]["code_parrainage"])
    code = _nouveau_code_parrainage()
    patch_response = requests.patch(
        _rest_url("profils", f"id=eq.{user_id}&code_parrainage=is.null"),
        headers=_rest_headers(),
        json={"code_parrainage": code},
        timeout=20,
    )
    if patch_response.ok:
        return code
    # On retente une fois avec un code différent si le patch concurrent a déja été réalisé.
    alt = _nouveau_code_parrainage()
    requests.patch(
        _rest_url("profils", f"id=eq.{user_id}"),
        headers=_rest_headers(),
        json={"code_parrainage": alt},
        timeout=20,
    )
    return alt


def stats_parrainage(user_id: str) -> dict[str, int]:
    response = requests.get(
        _rest_url("rpc/stats_parrainage?user_id=eq." + str(user_id)),
        headers=_rest_headers(),
        timeout=20,
    )
    if not response.ok:
        return {"filleuls": 0, "tokens_gagnes": 0}
    rows = _json_or_none(response) or []
    if not isinstance(rows, list):
        return {"filleuls": 0, "tokens_gagnes": 0}
    filleuls = len(rows)
    tokens = sum(int((r or {}).get("bonus") or 0) for r in rows)
    return {"filleuls": filleuls, "tokens_gagnes": tokens}


def utiliser_parrainage(
    user_id: str,
    code: str,
    bonus: int,
    max_filleuls: int,
    max_days: int,
) -> str:
    payload = {
        "p_user_id": user_id,
        "p_code": code,
        "p_bonus": int(bonus),
        "p_max_filleuls": int(max_filleuls),
        "p_max_jours": int(max_days),
    }
    response = requests.post(
        _rest_url("rpc/utiliser_code_parrainage"),
        headers=_rest_headers(),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        return "code_invalide"
    result = _json_or_none(response)
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return str(result.get("status") or result.get("result") or "ok")
    return "ok"


def verifier_token(token: str) -> dict[str, Any] | None:
    """Valide le JWT utilisateur ou le jeton d'appareil et joint son profil."""
    token = (token or "").strip()
    if not token:
        return None

    if token.startswith(APPAREIL_PREFIX):
        appareil = lire_appareil(token)
        if not appareil:
            return None
        user_id = str(appareil.get("user_id") or "")
        if not user_id:
            return None
        profil = lire_profil(user_id) or {}
        return {
            "user_id": user_id,
            "email": str(appareil.get("email") or profil.get("email") or ""),
            "nom": str(appareil.get("nom") or profil.get("nom") or ""),
            "tokens_solde": int(profil.get("tokens_solde") or 0),
            "tokens_utilises": int(profil.get("tokens_utilises") or 0),
            "plan": str(profil.get("plan") or "gratuit"),
        }

    try:
        response = requests.get(
            f"{supabase_url()}/auth/v1/user",
            headers={"apikey": service_key(), "Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except requests.RequestException:
        return None
    if not response.ok:
        return None
    try:
        user = response.json()
    except ValueError:
        return None

    user_id = str(user.get("id") or "")
    if not user_id:
        return None
    meta = user.get("user_metadata") or {}
    profil = lire_profil(user_id) or {}
    return {
        "user_id": user_id,
        "email": str(user.get("email") or profil.get("email") or ""),
        "nom": str(profil.get("nom") or meta.get("full_name") or meta.get("name") or ""),
        "tokens_solde": int(profil.get("tokens_solde") or 0),
        "tokens_utilises": int(profil.get("tokens_utilises") or 0),
        "plan": str(profil.get("plan") or "gratuit"),
    }


__all__ = [
    "APPAREIL_PREFIX",
    "CODE_TTL_SECONDS",
    "PUBLIC_URL",
    "DEVICE_PAGE_URL",
    "supabase_url",
    "service_key",
    "generer_code",
    "sauvegarder_code",
    "nettoyer_codes_expires",
    "supprimer_code",
    "_lire_code",
    "code_en_attente",
    "verifier_code",
    "confirmer_code",
    "creer_code_unique",
    "lire_appareil",
    "appareil_de_token",
    "creer_appareil",
    "revoquer_appareil",
    "revoquer_appareil_de",
    "lister_appareils",
    "lire_profil",
    "maj_profil",
    "debiter_profil",
    "crediter_profil",
    "lister_plans",
    "lire_plan",
    "lister_achats",
    "achats_de",
    "paiements_de",
    "creer_paiement",
    "lire_paiement",
    "dernier_paiement",
    "paiement_session",
    "reserver_paiement",
    "liberer_paiement",
    "marquer_anomalie",
    "paiements_en_attente",
    "enregistrer_achat",
    "obtenir_code_parrainage",
    "stats_parrainage",
    "utiliser_parrainage",
    "verifier_token",
]

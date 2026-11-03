"""Accès Supabase côté serveur : codes device, appareils, profils et paiements.

Toutes les requêtes partent avec la service_role key, jamais avec la clé anon
(exposée dans le navigateur).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import secrets
import urllib.parse
from typing import Any

import requests

CODE_TTL_SECONDS = 600
CODE_TTL_MINUTES = 10
POLL_INTERVAL_SECONDS = 5


def _url_publique() -> str:
    """URL publique du service, avec priorité à la configuration explicite."""
    return (
        os.environ.get("DARKCODEX_PUBLIC_URL", "").strip()
        or os.environ.get("RENDER_EXTERNAL_URL", "").strip()
        or "https://darkcodex-technicien.onrender.com"
    ).rstrip("/")


def _url_page_device() -> str:
    return os.environ.get("DARKCODEX_DEVICE_URL", "").strip() or f"{_url_publique()}/device"


PUBLIC_URL = _url_publique()
DEVICE_PAGE_URL = _url_page_device()
PLAN_COLONNES = "nom,tokens,prix_fcfa,description"
APPAREIL_PREFIX = "dkx_"
ALPHABET_CODE = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def normaliser_plan(nom: str | None) -> str:
    return (nom or "").strip().lower() or "gratuit"


def supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL", "").strip()
    if not url:
        raise RuntimeError("SUPABASE_URL manquante (voir server/.env).")
    return url.rstrip("/")


def service_key() -> str:
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_KEY manquante (voir server/.env).")
    return key


def service_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    key = service_key()
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if extra:
        headers.update(extra)
    return headers


def rest_url(table: str, params: dict[str, str] | None = None) -> str:
    url = f"{supabase_url()}/rest/v1/{table}"
    return f"{url}?{urllib.parse.urlencode(params)}" if params else url


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def expires_iso() -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=CODE_TTL_MINUTES)).isoformat(timespec="seconds")


def generer_code() -> str:
    def bloc() -> str:
        return "".join(secrets.choice(ALPHABET_CODE) for _ in range(4))
    return f"DARK-{bloc()}-{bloc()}"


def sauvegarder_code(code: str) -> None:
    response = requests.post(rest_url("device_codes"), headers=service_headers({"Prefer": "return=minimal"}),
                             json={"code": code, "confirme": False, "expire_a": expires_iso()}, timeout=20)
    if response.status_code == 409:
        raise ValueError("collision")
    if not response.ok:
        raise RuntimeError(f"Supabase device_codes (insert): HTTP {response.status_code} {response.text[:300]}")


def creer_code_unique() -> str:
    for _ in range(5):
        code = generer_code()
        try:
            sauvegarder_code(code)
        except ValueError:
            continue
        return code
    raise RuntimeError("Impossible de générer un code device unique.")


def nettoyer_codes_expires() -> int:
    response = requests.delete(rest_url("device_codes", {"expire_a": f"lt.{now_iso()}"}),
                               headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase device_codes (delete): HTTP {response.status_code}")
    try:
        rows = response.json()
    except ValueError:
        rows = []
    return len(rows) if isinstance(rows, list) else 0


def _lire_code(code: str) -> dict[str, Any] | None:
    response = requests.get(rest_url("device_codes", {"code": f"eq.{code}", "select": "*", "limit": "1"}),
                            headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase device_codes (select): HTTP {response.status_code}")
    rows = response.json()
    return rows[0] if rows else None


def verifier_code(code: str) -> dict[str, Any]:
    code = (code or "").strip().upper()
    if not code:
        return {"status": "expire"}
    row = _lire_code(code)
    if row is None:
        return {"status": "expire"}
    try:
        expire = dt.datetime.fromisoformat(str(row.get("expire_a") or "").replace("Z", "+00:00"))
        if expire.tzinfo is None:
            expire = expire.replace(tzinfo=dt.timezone.utc)
        if expire < dt.datetime.now(dt.timezone.utc):
            return {"status": "expire"}
    except ValueError:
        return {"status": "expire"}
    if not row.get("confirme"):
        return {"status": "en_attente"}
    session = verifier_token(str(row.get("token") or ""))
    if not session:
        return {"status": "expire"}
    supprimer_code(code)
    return {"status": "confirme", "token": row.get("token"), "user_id": session["user_id"],
            "email": session["email"], "nom": session["nom"], "tokens_solde": session["tokens_solde"],
            "plan": session["plan"]}


def confirmer_code(code: str, user_id: str, token: str) -> bool:
    response = requests.patch(
        rest_url("device_codes", {"code": f"eq.{(code or '').strip().upper()}", "confirme": "eq.false",
                                   "expire_a": f"gt.{now_iso()}"}),
        headers=service_headers({"Prefer": "return=representation"}),
        json={"confirme": True, "user_id": user_id, "token": token}, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase device_codes (update): HTTP {response.status_code} {response.text[:300]}")
    try:
        return bool(response.json())
    except ValueError:
        return False


def _empreinte(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def creer_appareil(user_id: str, email: str, nom: str) -> str:
    token = APPAREIL_PREFIX + secrets.token_urlsafe(32)
    response = requests.post(rest_url("appareils"), headers=service_headers({"Prefer": "return=minimal"}),
                             json={"user_id": user_id, "email": email, "nom": nom,
                                   "token_hash": _empreinte(token)}, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase appareils (insert): HTTP {response.status_code} {response.text[:300]}")
    return token


def lire_appareil(token: str) -> dict[str, Any] | None:
    response = requests.get(rest_url("appareils", {"token_hash": f"eq.{_empreinte(token)}", "revoque": "eq.false",
                                                     "select": "*", "limit": "1"}),
                            headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase appareils (select): HTTP {response.status_code}")
    rows = response.json()
    return rows[0] if rows else None


def _revoquer(params: dict[str, str]) -> bool:
    response = requests.patch(rest_url("appareils", params), headers=service_headers({"Prefer": "return=representation"}),
                              json={"revoque": True}, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase appareils (update): HTTP {response.status_code}")
    try:
        return bool(response.json())
    except ValueError:
        return False


def revoquer_appareil(token: str) -> bool:
    if not (token or "").startswith(APPAREIL_PREFIX):
        return False
    return _revoquer({"token_hash": f"eq.{_empreinte(token)}"})


def lister_appareils(user_id: str) -> list[dict[str, Any]]:
    response = requests.get(rest_url("appareils", {"user_id": f"eq.{user_id}",
        "select": "id,nom,email,revoque,cree_a", "order": "cree_a.desc", "limit": "50"}),
        headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase appareils (select): HTTP {response.status_code}")
    rows = response.json()
    return rows if isinstance(rows, list) else []


def appareil_de_token(token: str) -> dict[str, Any] | None:
    if not (token or "").startswith(APPAREIL_PREFIX):
        return None
    row = lire_appareil(token)
    return {key: row.get(key) for key in ("id", "nom", "email", "revoque", "cree_a")} if row else None


def revoquer_appareil_de(user_id: str, appareil_id: str) -> bool:
    return _revoquer({"id": f"eq.{appareil_id}", "user_id": f"eq.{user_id}"})


def code_en_attente(code: str) -> bool:
    row = _lire_code((code or "").strip().upper())
    if not row or row.get("confirme"):
        return False
    try:
        expire = dt.datetime.fromisoformat(str(row.get("expire_a") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if expire.tzinfo is None:
        expire = expire.replace(tzinfo=dt.timezone.utc)
    return expire >= dt.datetime.now(dt.timezone.utc)


def supprimer_code(code: str) -> bool:
    try:
        response = requests.delete(rest_url("device_codes", {"code": f"eq.{(code or '').strip().upper()}"}),
                                   headers=service_headers(), timeout=20)
    except requests.RequestException:
        return False
    return response.ok


def lister_plans() -> list[dict[str, Any]]:
    try:
        response = requests.get(rest_url("plans", {"select": PLAN_COLONNES, "order": "prix_fcfa.asc"}),
                                headers=service_headers(), timeout=20)
    except requests.RequestException as exc:
        raise RuntimeError(f"Supabase plans injoignable: {exc}") from exc
    if not response.ok:
        raise RuntimeError(f"Supabase plans: HTTP {response.status_code}")
    rows = response.json()
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def lire_plan(nom: str | None) -> dict[str, Any] | None:
    try:
        response = requests.get(rest_url("plans", {"nom": f"eq.{normaliser_plan(nom)}", "select": PLAN_COLONNES,
                                                    "limit": "1"}), headers=service_headers(), timeout=20)
    except requests.RequestException as exc:
        raise RuntimeError(f"Supabase plans injoignable: {exc}") from exc
    if not response.ok:
        raise RuntimeError(f"Supabase plans: HTTP {response.status_code}")
    rows = response.json()
    return dict(rows[0]) if isinstance(rows, list) and rows else None


def lister_achats(user_id: str, limite: int = 50) -> list[dict[str, Any]]:
    response = requests.get(rest_url("achats", {"user_id": f"eq.{user_id}", "select": "*",
        "order": "date_achat.desc", "limit": str(limite)}), headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase achats (select): HTTP {response.status_code}")
    rows = response.json()
    return rows if isinstance(rows, list) else []


def enregistrer_achat(user_id: str, plan: str, tokens_ajoutes: int, montant_fcfa: int, transaction_id: str) -> None:
    response = requests.post(rest_url("achats"), headers=service_headers({"Prefer": "return=minimal"}),
        json={"user_id": user_id, "plan": normaliser_plan(plan), "tokens_ajoutes": tokens_ajoutes,
              "montant_fcfa": montant_fcfa, "transaction_id": transaction_id}, timeout=20)
    if not response.ok and response.status_code != 409:
        raise RuntimeError(f"Supabase achats (insert): HTTP {response.status_code} {response.text[:300]}")


def lire_profil(user_id: str) -> dict[str, Any] | None:
    response = requests.get(rest_url("profils", {"id": f"eq.{user_id}", "select": "*", "limit": "1"}),
                            headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase profils (select): HTTP {response.status_code}")
    rows = response.json()
    return rows[0] if rows else None


def maj_profil(user_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    response = requests.patch(rest_url("profils", {"id": f"eq.{user_id}"}),
        headers=service_headers({"Prefer": "return=representation"}), json=fields, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase profils (update): HTTP {response.status_code} {response.text[:300]}")
    rows = response.json()
    return rows[0] if rows else {}


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
        return {"user_id": user_id, "email": str(appareil.get("email") or profil.get("email") or ""),
                "nom": str(appareil.get("nom") or profil.get("nom") or ""),
                "tokens_solde": int(profil.get("tokens_solde") or 0),
                "tokens_utilises": int(profil.get("tokens_utilises") or 0),
                "plan": str(profil.get("plan") or "gratuit")}
    try:
        response = requests.get(f"{supabase_url()}/auth/v1/user",
            headers={"apikey": service_key(), "Authorization": f"Bearer {token}"}, timeout=20)
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
    return {"user_id": user_id, "email": str(user.get("email") or profil.get("email") or ""),
            "nom": str(profil.get("nom") or meta.get("full_name") or meta.get("name") or ""),
            "tokens_solde": int(profil.get("tokens_solde") or 0),
            "tokens_utilises": int(profil.get("tokens_utilises") or 0),
            "plan": str(profil.get("plan") or "gratuit")}


def crediter_profil(user_id: str, tokens: int, plan: str) -> dict[str, Any]:
    response = requests.post(f"{supabase_url()}/rest/v1/rpc/crediter_tokens", headers=service_headers(),
        json={"p_user_id": user_id, "p_tokens": int(tokens), "p_plan": normaliser_plan(plan)}, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase crediter_tokens: HTTP {response.status_code} {response.text[:300]}")
    try:
        data = response.json()
    except ValueError:
        return {}
    if isinstance(data, list):
        data = data[0] if data else {}
    return data if isinstance(data, dict) else {}


def debiter_profil(user_id: str, tokens: int) -> dict[str, Any]:
    response = requests.post(f"{supabase_url()}/rest/v1/rpc/debiter_tokens", headers=service_headers(),
        json={"p_user_id": user_id, "p_tokens": max(0, int(tokens))}, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase debiter_tokens: HTTP {response.status_code}")
    rows = response.json()
    if isinstance(rows, list):
        rows = rows[0] if rows else {}
    return rows if isinstance(rows, dict) else {}


def creer_paiement(user_id: str, plan: str, tokens: int, montant_fcfa: int, session_id: str) -> None:
    response = requests.post(rest_url("paiements"), headers=service_headers({"Prefer": "return=minimal"}),
        json={"user_id": user_id, "plan": normaliser_plan(plan), "tokens": tokens,
              "montant_fcfa": montant_fcfa, "session_id": session_id, "statut": "en_attente"}, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase paiements (insert): HTTP {response.status_code} {response.text[:300]}")


def lire_paiement(session_id: str) -> dict[str, Any] | None:
    response = requests.get(rest_url("paiements", {"session_id": f"eq.{session_id}", "select": "*", "limit": "1"}),
                            headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase paiements (select): HTTP {response.status_code}")
    rows = response.json()
    return rows[0] if rows else None


def paiements_en_attente(limite: int = 30, depuis_minutes: int = 4320) -> list[dict[str, Any]]:
    depuis = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=depuis_minutes)).isoformat(timespec="seconds")
    response = requests.get(rest_url("paiements", {"statut": "eq.en_attente", "cree_a": f"gte.{depuis}",
        "select": "*", "order": "cree_a.desc", "limit": str(limite)}), headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase paiements (select): HTTP {response.status_code}")
    rows = response.json()
    return rows if isinstance(rows, list) else []


def paiements_de(user_id: str, limite: int = 100) -> list[dict[str, Any]]:
    response = requests.get(rest_url("paiements", {"user_id": f"eq.{user_id}", "select": "*",
        "order": "cree_a.desc", "limit": str(limite)}), headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase paiements (select): HTTP {response.status_code}")
    rows = response.json()
    return rows if isinstance(rows, list) else []


def dernier_paiement(user_id: str) -> dict[str, Any] | None:
    response = requests.get(rest_url("paiements", {"user_id": f"eq.{user_id}", "order": "cree_a.desc",
        "limit": "1", "select": "*"}), headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase paiements: HTTP {response.status_code}")
    rows = response.json()
    return rows[0] if isinstance(rows, list) and rows else None


def _changer_statut(session_id: str, de: str, vers: str) -> bool:
    corps: dict[str, Any] = {"statut": vers}
    if vers == "credite":
        corps["credite_a"] = now_iso()
    response = requests.patch(rest_url("paiements", {"session_id": f"eq.{session_id}", "statut": f"eq.{de}"}),
        headers=service_headers({"Prefer": "return=representation"}), json=corps, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase paiements (update): HTTP {response.status_code} {response.text[:300]}")
    try:
        return bool(response.json())
    except ValueError:
        return False


def reserver_paiement(session_id: str) -> bool:
    return _changer_statut(session_id, "en_attente", "credite")


def liberer_paiement(session_id: str) -> None:
    _changer_statut(session_id, "credite", "en_attente")


def marquer_anomalie(session_id: str) -> None:
    _changer_statut(session_id, "en_attente", "anomalie")


def generer_code_parrainage() -> str:
    return "DK-" + "".join(secrets.choice(ALPHABET_CODE) for _ in range(6))


def _code_parrainage_existant(user_id: str) -> str:
    response = requests.get(rest_url("profils", {"id": f"eq.{user_id}", "select": "code_parrainage", "limit": "1"}),
                            headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase profils (select): HTTP {response.status_code}")
    rows = response.json()
    return str((rows[0].get("code_parrainage") if rows else "") or "")


def obtenir_code_parrainage(user_id: str) -> str:
    existing = _code_parrainage_existant(user_id)
    if existing:
        return existing
    for _ in range(5):
        code = generer_code_parrainage()
        response = requests.patch(rest_url("profils", {"id": f"eq.{user_id}", "code_parrainage": "is.null"}),
            headers=service_headers({"Prefer": "return=representation"}), json={"code_parrainage": code}, timeout=20)
        if response.status_code == 409:
            continue
        if not response.ok:
            raise RuntimeError(f"Supabase profils (update): HTTP {response.status_code} {response.text[:200]}")
        if response.json():
            return code
        return _code_parrainage_existant(user_id) or code
    raise RuntimeError("Impossible de créer un code de parrainage.")


def stats_parrainage(user_id: str) -> dict[str, int]:
    response = requests.get(rest_url("parrainages", {"parrain_id": f"eq.{user_id}", "select": "bonus"}),
                            headers=service_headers(), timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase parrainages (select): HTTP {response.status_code}")
    rows = response.json()
    rows = rows if isinstance(rows, list) else []
    return {"filleuls": len(rows), "tokens_gagnes": sum(int(row.get("bonus") or 0) for row in rows)}


def utiliser_parrainage(user_id: str, code: str, bonus: int, max_filleuls: int, jours: int) -> str:
    response = requests.post(f"{supabase_url()}/rest/v1/rpc/utiliser_code_parrainage", headers=service_headers(),
        json={"p_user_id": user_id, "p_code": code, "p_bonus": int(bonus),
              "p_max_filleuls": int(max_filleuls), "p_jours": int(jours)}, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Supabase utiliser_code_parrainage: HTTP {response.status_code} {response.text[:200]}")
    result = response.json()
    return str(result if not isinstance(result, list) else (result[0] if result else ""))

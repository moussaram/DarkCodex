"""Accès Supabase côté serveur : codes device, vérification de token, profils.

Toutes les requêtes partent avec la service_role key, jamais avec la clé anon
(exposée dans le navigateur).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import secrets
import string
import urllib.parse
from typing import Any

import requests

CODE_TTL_SECONDS = 600  # 10 minutes exactement
CODE_TTL_MINUTES = 10
POLL_INTERVAL_SECONDS = 5
DEVICE_PAGE_URL = os.environ.get("DARKCODEX_DEVICE_URL", "https://darkcodex.onrender.com/device")

PLAN_CATALOG: dict[str, dict[str, Any]] = {
    "free": {
        "nom": "free",
        "tokens": 10000,
        "prix_fcfa": 0,
        "description": "10 000 tokens offerts — plan gratuit",
        "quota_mensuel": 10000,
        "facturation": "gratuit",
        "personnalise": False,
    },
    "starter": {
        "nom": "starter",
        "tokens": 300000,
        "prix_fcfa": 2500,
        "description": "300 000 tokens par mois",
        "quota_mensuel": 300000,
        "facturation": "mensuel",
        "personnalise": False,
    },
    "pro": {
        "nom": "pro",
        "tokens": 1000000,
        "prix_fcfa": 5000,
        "description": "1 000 000 tokens par mois",
        "quota_mensuel": 1000000,
        "facturation": "mensuel",
        "personnalise": False,
    },
    "business": {
        "nom": "business",
        "tokens": 3000000,
        "prix_fcfa": 12500,
        "description": "3 000 000 tokens par mois",
        "quota_mensuel": 3000000,
        "facturation": "mensuel",
        "personnalise": False,
    },
    "enterprise": {
        "nom": "enterprise",
        "tokens": 0,
        "prix_fcfa": 0,
        "description": "Quota personnalisé — devis sur mesure",
        "quota_mensuel": 0,
        "facturation": "devis",
        "personnalise": True,
    },
}


def plan_catalog() -> dict[str, dict[str, Any]]:
    return {nom: {**plan} for nom, plan in PLAN_CATALOG.items()}


def normaliser_plan(nom: str | None) -> str:
    nom = (nom or "").strip().lower()
    return nom if nom in PLAN_CATALOG else "free"


def plan_est_personnalise(nom: str | None) -> bool:
    return bool(PLAN_CATALOG.get(normaliser_plan(nom), {}).get("personnalise"))


def _date_from_value(valeur: Any) -> dt.datetime | None:
    if not valeur:
        return None
    if isinstance(valeur, dt.datetime):
        return valeur
    chaine = str(valeur).strip()
    if not chaine:
        return None
    if chaine.endswith("Z"):
        chaine = chaine[:-1] + "+00:00"
    try:
        dt_obj = dt.datetime.fromisoformat(chaine)
    except ValueError:
        return None
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=dt.timezone.utc)
    return dt_obj.astimezone(dt.timezone.utc)


def est_abonnement_expire(date_expiration: Any) -> bool:
    expiration = _date_from_value(date_expiration)
    if expiration is None:
        return False
    return expiration <= dt.datetime.now(dt.timezone.utc)


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
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def rest_url(table: str, params: dict[str, str] | None = None) -> str:
    url = f"{supabase_url()}/rest/v1/{table}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return url


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def expires_iso() -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=CODE_TTL_MINUTES)).isoformat(
        timespec="seconds"
    )


# ── Codes device ───────────────────────────────────────────────


ALPHABET_CODE = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans 0/O ni 1/I (évite les confusions)


def generer_code() -> str:
    """Format DARK-XXXX-XXXX (8 caractères, ~10^12 combinaisons)."""

    def bloc() -> str:
        return "".join(secrets.choice(ALPHABET_CODE) for _ in range(4))

    return f"DARK-{bloc()}-{bloc()}"


def sauvegarder_code(code: str) -> None:
    payload = {"code": code, "confirme": False, "expire_a": expires_iso()}
    for _ in range(5):
        response = requests.post(
            rest_url("device_codes"),
            headers=service_headers({"Prefer": "return=minimal"}),
            json=payload,
            timeout=20,
        )
        if response.status_code == 409:  # collision, on régénère ailleurs
            raise ValueError("collision")
        if response.ok:
            return
        raise RuntimeError(f"Supabase device_codes (insert): HTTP {response.status_code} {response.text[:300]}")
    raise RuntimeError("Impossible d'enregistrer le code device.")


def creer_code_unique() -> str:
    """Génère et enregistre un code libre (5 essais en cas de collision)."""
    for _ in range(5):
        code = generer_code()
        try:
            sauvegarder_code(code)
        except ValueError:
            continue
        return code
    raise RuntimeError("Impossible de générer un code device unique.")


def nettoyer_codes_expires() -> int:
    response = requests.delete(
        rest_url("device_codes", {"expire_a": f"lt.{now_iso()}"}),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase device_codes (delete): HTTP {response.status_code}")
    try:
        rows = response.json()
    except ValueError:
        rows = []
    return len(rows) if isinstance(rows, list) else 0


def _lire_code(code: str) -> dict[str, Any] | None:
    response = requests.get(
        rest_url("device_codes", {"code": f"eq.{code}", "select": "*", "limit": "1"}),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase device_codes (select): HTTP {response.status_code}")
    rows = response.json()
    return rows[0] if rows else None


def verifier_code(code: str) -> dict[str, Any]:
    """Retourne {'status': expire|en_attente|confirme, ...}."""
    code = (code or "").strip().upper()
    if not code:
        return {"status": "expire"}

    row = _lire_code(code)
    if row is None:
        return {"status": "expire"}

    expire_a = str(row.get("expire_a") or "")
    try:
        expire_dt = dt.datetime.fromisoformat(expire_a.replace("Z", "+00:00"))
        if expire_dt.tzinfo is None:
            expire_dt = expire_dt.replace(tzinfo=dt.timezone.utc)
        if expire_dt < dt.datetime.now(dt.timezone.utc):
            return {"status": "expire"}
    except ValueError:
        return {"status": "expire"}

    if not row.get("confirme"):
        return {"status": "en_attente"}

    session = verifier_token(str(row.get("token") or ""))
    if not session:
        return {"status": "expire"}

    supprimer_code(code)  # usage unique : le jeton n'est livré qu'une seule fois
    return {
        "status": "confirme",
        "token": row.get("token"),
        "refresh_token": row.get("refresh_token"),
        "user_id": session["user_id"],
        "email": session["email"],
        "nom": session["nom"],
        "tokens_solde": session["tokens_solde"],
        "plan": session["plan"],
    }


def confirmer_code(code: str, user_id: str, token: str, refresh_token: str | None = None) -> bool:
    corps: dict[str, Any] = {"confirme": True, "user_id": user_id, "token": token}
    if refresh_token:
        corps["refresh_token"] = refresh_token
    response = requests.patch(
        rest_url(
            "device_codes",
            {
                "code": f"eq.{(code or '').strip().upper()}",
                "confirme": "eq.false",
                "expire_a": f"gt.{now_iso()}",
            },
        ),
        headers=service_headers({"Prefer": "return=representation"}),
        json=corps,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase device_codes (update): HTTP {response.status_code} {response.text[:300]}")
    try:
        rows = response.json()
    except ValueError:
        rows = []
    return bool(rows)


# ── Codes appareil : lecture seule, suppression ─────────────────


APPAREIL_PREFIX = "dkx_"


def _empreinte(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def creer_appareil(user_id: str, email: str, nom: str) -> str:
    """Crée un jeton d'appareil. Seule son empreinte SHA-256 est enregistrée."""
    token = APPAREIL_PREFIX + secrets.token_urlsafe(32)
    response = requests.post(
        rest_url("appareils"),
        headers=service_headers({"Prefer": "return=minimal"}),
        json={"user_id": user_id, "email": email, "nom": nom, "token_hash": _empreinte(token)},
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase appareils (insert): HTTP {response.status_code} {response.text[:300]}")
    return token


def lire_appareil(token: str) -> dict[str, Any] | None:
    response = requests.get(
        rest_url(
            "appareils",
            {
                "token_hash": f"eq.{_empreinte(token)}",
                "revoque": "eq.false",
                "select": "*",
                "limit": "1",
            },
        ),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase appareils (select): HTTP {response.status_code}")
    rows = response.json()
    return rows[0] if rows else None


def revoquer_appareil(token: str) -> bool:
    """Révoque un jeton d'appareil (déconnexion). Ignore les jetons qui n'en sont pas."""
    if not (token or "").startswith(APPAREIL_PREFIX):
        return False
    response = requests.patch(
        rest_url("appareils", {"token_hash": f"eq.{_empreinte(token)}"}),
        headers=service_headers({"Prefer": "return=representation"}),
        json={"revoque": True},
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase appareils (update): HTTP {response.status_code}")
    try:
        return bool(response.json())
    except ValueError:
        return False


def code_en_attente(code: str) -> bool:
    """Vrai si le code existe, n'est pas expiré et n'a pas encore été confirmé."""
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
    """Supprime un code (meilleur effort : une erreur ne doit pas bloquer la connexion)."""
    try:
        response = requests.delete(
            rest_url("device_codes", {"code": f"eq.{(code or '').strip().upper()}"}),
            headers=service_headers(),
            timeout=20,
        )
    except requests.RequestException:
        return False
    return response.ok


def rafraichir_session(refresh_token: str) -> dict[str, Any] | None:
    """Échange un refresh_token Supabase contre un nouveau couple de jetons."""
    refresh_token = (refresh_token or "").strip()
    if not refresh_token:
        return None
    cle = os.environ.get("SUPABASE_ANON_KEY", "").strip() or service_key()
    try:
        response = requests.post(
            f"{supabase_url()}/auth/v1/token",
            params={"grant_type": "refresh_token"},
            headers={"apikey": cle, "Content-Type": "application/json"},
            json={"refresh_token": refresh_token},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Supabase auth injoignable: {exc}") from exc
    if response.status_code in (400, 401, 403):
        return None
    if not response.ok:
        raise RuntimeError(f"Supabase auth (refresh): HTTP {response.status_code}")
    data = response.json()
    if not data.get("access_token") or not data.get("refresh_token"):
        return None
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_in": int(data.get("expires_in") or 3600),
    }


# ── Plans et abonnements ──────────────────────────────────────


def _plan_extra_defaut(nom: str | None) -> dict[str, Any]:
    return {**PLAN_CATALOG.get(normaliser_plan(nom), PLAN_CATALOG["free"])}


def lister_plans() -> list[dict[str, Any]]:
    """Retourne les plans pour le site, y compris le plan gratuit."""
    try:
        response = requests.get(
            rest_url(
                "plans",
                {
                    "select": "nom,tokens,prix_fcfa,description,quota_mensuel,facturation,personnalise",
                    "order": "prix_fcfa.asc",
                },
            ),
            headers=service_headers(),
            timeout=20,
        )
    except requests.RequestException:
        return list(plan_catalog().values())

    if response.ok:
        rows = response.json()
        if isinstance(rows, list) and rows:
            rows = [dict(r) for r in rows]
            for row in rows:
                if "nom" in row:
                    row["nom"] = str(row["nom"]).strip().lower()
                    plan = PLAN_CATALOG.get(row["nom"])
                    if plan is not None:
                        row.setdefault("tokens", plan["tokens"])
                        row.setdefault("prix_fcfa", plan["prix_fcfa"])
                        row.setdefault("quota_mensuel", plan["quota_mensuel"])
                        row.setdefault("facturation", plan["facturation"])
                        row.setdefault("personnalise", plan["personnalise"])
            return rows
    return list(plan_catalog().values())


def lire_plan(nom: str | None) -> dict[str, Any] | None:
    plan_nom = normaliser_plan(nom)
    fallback = PLAN_CATALOG.get(plan_nom)
    if not fallback:
        return None
    try:
        response = requests.get(
            rest_url("plans", {"nom": f"eq.{plan_nom}", "select": "*", "limit": "1"}),
            headers=service_headers(),
            timeout=20,
        )
    except requests.RequestException:
        return {**fallback}
    if not response.ok:
        return {**fallback}
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        return {**fallback}
    row = dict(rows[0])
    row["nom"] = str(row.get("nom") or plan_nom).strip().lower()
    row.setdefault("tokens", fallback["tokens"])
    row.setdefault("prix_fcfa", fallback["prix_fcfa"])
    row.setdefault("quota_mensuel", fallback["quota_mensuel"])
    row.setdefault("facturation", fallback["facturation"])
    row.setdefault("personnalise", fallback["personnalise"])
    return row


def lister_abonnements(user_id: str, limite: int = 10) -> list[dict[str, Any]]:
    try:
        response = requests.get(
            rest_url(
                "abonnements",
                {"user_id": f"eq.{user_id}", "select": "*", "order": "date_debut.desc", "limit": str(limite)},
            ),
            headers=service_headers(),
            timeout=20,
        )
    except requests.RequestException:
        return []
    if not response.ok:
        return []
    rows = response.json()
    return rows if isinstance(rows, list) else []


def lister_transactions(user_id: str, limite: int = 10) -> list[dict[str, Any]]:
    try:
        response = requests.get(
            rest_url(
                "transactions",
                {"user_id": f"eq.{user_id}", "select": "*", "order": "cree_a.desc", "limit": str(limite)},
            ),
            headers=service_headers(),
            timeout=20,
        )
    except requests.RequestException:
        return []
    if not response.ok:
        return []
    rows = response.json()
    return rows if isinstance(rows, list) else []


def creer_abonnement(user_id: str, plan_nom: str, tokens: int, montant_fcfa: int, session_id: str) -> dict[str, Any]:
    plan = lire_plan(plan_nom) or _plan_extra_defaut(plan_nom)
    payload = {
        "user_id": user_id,
        "plan": str(plan.get("nom") or plan_nom).lower(),
        "quota_mensuel": int(plan.get("quota_mensuel") or max(0, int(tokens))),
        "prix_fcfa": int(montant_fcfa),
        "statut": "actif",
        "date_debut": now_iso(),
        "date_expiration": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(timespec="seconds"),
        "date_renouvellement": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(timespec="seconds"),
        "derniere_transaction_id": session_id,
        "session_paiement": session_id,
    }
    response = requests.post(
        rest_url("abonnements"),
        headers=service_headers({"Prefer": "return=representation"}),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase abonnements (insert): HTTP {response.status_code} {response.text[:300]}")
    rows = response.json()
    return rows[0] if isinstance(rows, list) and rows else payload


def maj_abonnement(user_id: str, *, plan_nom: str, date_expiration: Any | None = None, statut: str = "actif") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "plan": normaliser_plan(plan_nom),
        "statut": statut,
        "maj_a": now_iso(),
    }
    if date_expiration is not None:
        payload["date_expiration"] = str(date_expiration)
    response = requests.patch(
        rest_url("abonnements", {"user_id": f"eq.{user_id}", "select": "*"}),
        headers=service_headers({"Prefer": "return=representation"}),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase abonnements (update): HTTP {response.status_code} {response.text[:300]}")
    rows = response.json()
    return rows[0] if isinstance(rows, list) and rows else payload


def renouveler_abonnement(user_id: str, plan_nom: str, *, montant_fcfa: int = 0, session_id: str | None = None) -> dict[str, Any]:
    plan = lire_plan(plan_nom) or _plan_extra_defaut(plan_nom)
    nouvelle_expiration = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(timespec="seconds")
    payload = {
        "plan": normaliser_plan(plan_nom),
        "quota_mensuel": int(plan.get("quota_mensuel") or 0),
        "prix_fcfa": int(montant_fcfa or int(plan.get("prix_fcfa") or 0)),
        "statut": "actif",
        "date_renouvellement": nouvelle_expiration,
        "date_expiration": nouvelle_expiration,
        "maj_a": now_iso(),
    }
    if session_id:
        payload["session_paiement"] = session_id
    response = requests.patch(
        rest_url("abonnements", {"user_id": f"eq.{user_id}", "statut": "eq.actif", "select": "*"}),
        headers=service_headers({"Prefer": "return=representation"}),
        json=payload,
        timeout=20,
    )
    if response.status_code == 406:
        response = requests.post(
            rest_url("abonnements"),
            headers=service_headers({"Prefer": "return=representation"}),
            json={
                "user_id": user_id,
                "plan": normaliser_plan(plan_nom),
                "quota_mensuel": int(plan.get("quota_mensuel") or 0),
                "prix_fcfa": int(montant_fcfa or int(plan.get("prix_fcfa") or 0)),
                "statut": "actif",
                "date_debut": now_iso(),
                "date_expiration": nouvelle_expiration,
                "date_renouvellement": nouvelle_expiration,
                "session_paiement": session_id or "",
            },
            timeout=20,
        )
    if not response.ok:
        raise RuntimeError(f"Supabase abonnements (renew): HTTP {response.status_code} {response.text[:300]}")
    rows = response.json()
    return rows[0] if isinstance(rows, list) and rows else payload


def enregistrer_transaction(
    user_id: str,
    plan: str,
    montant_fcfa: int,
    tokens_total: int,
    session_id: str | None = None,
    statut: str = "confirme",
    type_transaction: str = "abonnement",
) -> dict[str, Any]:
    payload = {
        "user_id": user_id,
        "plan": normaliser_plan(plan),
        "montant_fcfa": int(montant_fcfa),
        "tokens_total": int(tokens_total),
        "session_id": session_id or "",
        "statut": statut,
        "type_transaction": type_transaction,
        "metadata": {"session_id": session_id or ""},
    }
    response = requests.post(
        rest_url("transactions"),
        headers=service_headers({"Prefer": "return=representation"}),
        json=payload,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase transactions (insert): HTTP {response.status_code} {response.text[:300]}")
    rows = response.json()
    return rows[0] if isinstance(rows, list) and rows else payload


def enregistrer_achat(
    user_id: str, plan: str, tokens_ajoutes: int, montant_fcfa: int, transaction_id: str
) -> None:
    response = requests.post(
        rest_url("achats"),
        headers=service_headers({"Prefer": "return=minimal"}),
        json={
            "user_id": user_id,
            "plan": normaliser_plan(plan),
            "tokens_ajoutes": tokens_ajoutes,
            "montant_fcfa": montant_fcfa,
            "transaction_id": transaction_id,
        },
        timeout=20,
    )
    if not response.ok and response.status_code != 409:
        raise RuntimeError(f"Supabase achats (insert): HTTP {response.status_code} {response.text[:300]}")
    try:
        enregistrer_transaction(user_id, plan, montant_fcfa, tokens_ajoutes, transaction_id, "confirme", "abonnement")
    except Exception:
        pass


# ── Auth Supabase + profils ────────────────────────────────────


def verifier_token(token: str) -> dict[str, Any] | None:
    """Valide le JWT utilisateur auprès de /auth/v1/user et joint son profil."""
    token = (token or "").strip()
    if not token:
        return None
    if token.startswith(APPAREIL_PREFIX):  # jeton d'appareil (terminal)
        appareil = lire_appareil(token)
        if not appareil:
            return None
        user_id = str(appareil.get("user_id") or "")
        profil = lire_profil(user_id) or {}
        return {
            "user_id": user_id,
            "email": str(appareil.get("email") or profil.get("email") or ""),
            "nom": str(appareil.get("nom") or profil.get("nom") or ""),
            "tokens_solde": int(profil.get("tokens_solde") or 0),
            "tokens_utilises": int(profil.get("tokens_utilises") or 0),
            "plan": str(profil.get("plan") or "free"),
            "quota_mensuel": int(profil.get("quota_mensuel") or 0),
            "date_expiration_abonnement": profil.get("date_expiration_abonnement"),
            "statut_abonnement": profil.get("statut_abonnement") or "inactif",
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
        "plan": str(profil.get("plan") or "free"),
        "quota_mensuel": int(profil.get("quota_mensuel") or 0),
        "date_expiration_abonnement": profil.get("date_expiration_abonnement"),
        "statut_abonnement": profil.get("statut_abonnement") or "inactif",
    }


def lire_profil(user_id: str) -> dict[str, Any] | None:
    response = requests.get(
        rest_url("profils", {"id": f"eq.{user_id}", "select": "*", "limit": "1"}),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase profils (select): HTTP {response.status_code}")
    rows = response.json()
    return rows[0] if rows else None


def maj_profil(user_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    response = requests.patch(
        rest_url("profils", {"id": f"eq.{user_id}"}),
        headers=service_headers({"Prefer": "return=representation"}),
        json=fields,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase profils (update): HTTP {response.status_code} {response.text[:300]}")
    rows = response.json()
    return rows[0] if rows else {}


def crediter_profil(user_id: str, tokens: int, plan: str) -> dict[str, Any]:
    """Ajoute des tokens de façon atomique (fonction SQL crediter_tokens)."""
    response = requests.post(
        f"{supabase_url()}/rest/v1/rpc/crediter_tokens",
        headers=service_headers(),
        json={"p_user_id": user_id, "p_tokens": int(tokens), "p_plan": normaliser_plan(plan)},
        timeout=20,
    )
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
    """Retire des tokens de façon atomique (fonction SQL debiter_tokens)."""
    response = requests.post(
        f"{supabase_url()}/rest/v1/rpc/debiter_tokens",
        headers=service_headers(),
        json={"p_user_id": user_id, "p_tokens": max(0, int(tokens))},
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase debiter_tokens: HTTP {response.status_code}")
    rows = response.json()
    if isinstance(rows, list):
        rows = rows[0] if rows else {}
    return rows if isinstance(rows, dict) else {}


# ── Paiements SasPay (table « paiements », voir supabase_paiements.sql) ─────


def creer_paiement(user_id: str, plan: str, tokens: int, montant_fcfa: int, session_id: str) -> None:
    """Enregistre une intention de paiement liée à CET utilisateur et à CE plan."""
    response = requests.post(
        rest_url("paiements"),
        headers=service_headers({"Prefer": "return=minimal"}),
        json={
            "user_id": user_id,
            "plan": normaliser_plan(plan),
            "tokens": tokens,
            "montant_fcfa": montant_fcfa,
            "session_id": session_id,
            "statut": "en_attente",
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase paiements (insert): HTTP {response.status_code} {response.text[:300]}")


def lire_paiement(session_id: str) -> dict[str, Any] | None:
    response = requests.get(
        rest_url("paiements", {"session_id": f"eq.{session_id}", "select": "*", "limit": "1"}),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase paiements (select): HTTP {response.status_code}")
    rows = response.json()
    return rows[0] if rows else None


def paiements_en_attente(limite: int = 30, depuis_minutes: int = 4320) -> list[dict[str, Any]]:
    """Paiements récents encore en attente (réconciliation après un webhook)."""
    depuis = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=depuis_minutes)).isoformat(
        timespec="seconds"
    )
    response = requests.get(
        rest_url(
            "paiements",
            {
                "statut": "eq.en_attente",
                "cree_a": f"gte.{depuis}",
                "select": "*",
                "order": "cree_a.desc",
                "limit": str(limite),
            },
        ),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase paiements (select): HTTP {response.status_code}")
    rows = response.json()
    return rows if isinstance(rows, list) else []


def dernier_paiement(user_id: str) -> dict[str, Any] | None:
    """Dernier paiement d'un utilisateur (tous statuts confondus)."""
    response = requests.get(
        rest_url(
            "paiements",
            {"user_id": f"eq.{user_id}", "order": "cree_a.desc", "limit": "1", "select": "*"},
        ),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase paiements: HTTP {response.status_code}")
    rows = response.json()
    return rows[0] if isinstance(rows, list) and rows else None


def _changer_statut(session_id: str, de: str, vers: str) -> bool:
    """Passe le statut de `de` à `vers` en UNE requête."""
    params = {"session_id": f"eq.{session_id}", "statut": f"eq.{de}"}
    corps: dict[str, Any] = {"statut": vers}
    if vers == "credite":
        corps["credite_a"] = now_iso()
    response = requests.patch(
        rest_url("paiements", params),
        headers=service_headers({"Prefer": "return=representation"}),
        json=corps,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase paiements (update): HTTP {response.status_code} {response.text[:300]}")
    try:
        rows = response.json()
    except ValueError:
        rows = []
    return bool(rows)


def reserver_paiement(session_id: str) -> bool:
    """True pour le SEUL appel qui a le droit de créditer ce paiement."""
    return _changer_statut(session_id, "en_attente", "credite")


def liberer_paiement(session_id: str) -> None:
    """Annule la réservation si le crédit a échoué (le paiement pourra être retraité)."""
    _changer_statut(session_id, "credite", "en_attente")


def marquer_anomalie(session_id: str) -> None:
    _changer_statut(session_id, "en_attente", "anomalie")


# ── Parrainage (voir supabase_parrainage.sql) ───────────────────


def generer_code_parrainage() -> str:
    """Format DK-XXXXXX (6 caractères, même alphabet sans ambiguïté que les codes de connexion)."""
    return "DK-" + "".join(secrets.choice(ALPHABET_CODE) for _ in range(6))


def _code_parrainage_existant(user_id: str) -> str:
    response = requests.get(
        rest_url("profils", {"id": f"eq.{user_id}", "select": "code_parrainage", "limit": "1"}),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase profils (select): HTTP {response.status_code}")
    rows = response.json()
    return str((rows[0].get("code_parrainage") if rows else "") or "")


def obtenir_code_parrainage(user_id: str) -> str:
    """Retourne le code de l'utilisateur, en le créant à la première demande."""
    existant = _code_parrainage_existant(user_id)
    if existant:
        return existant
    for _ in range(5):  # plusieurs essais au cas (très rare) où le code existerait déjà
        code = generer_code_parrainage()
        response = requests.patch(
            rest_url("profils", {"id": f"eq.{user_id}", "code_parrainage": "is.null"}),
            headers=service_headers({"Prefer": "return=representation"}),
            json={"code_parrainage": code},
            timeout=20,
        )
        if response.status_code == 409:  # code déjà pris par quelqu'un d'autre
            continue
        if not response.ok:
            raise RuntimeError(f"Supabase profils (update): HTTP {response.status_code} {response.text[:200]}")
        if response.json():
            return code
        return _code_parrainage_existant(user_id) or code  # une autre requête a créé le code entre-temps
    raise RuntimeError("Impossible de créer un code de parrainage.")


def stats_parrainage(user_id: str) -> dict[str, int]:
    response = requests.get(
        rest_url("parrainages", {"parrain_id": f"eq.{user_id}", "select": "bonus"}),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase parrainages (select): HTTP {response.status_code}")
    rows = response.json()
    rows = rows if isinstance(rows, list) else []
    return {"filleuls": len(rows), "tokens_gagnes": sum(int(r.get("bonus") or 0) for r in rows)}


def utiliser_parrainage(user_id: str, code: str, bonus: int, max_filleuls: int, jours: int) -> str:
    """Appelle la fonction SQL atomique. Retourne 'ok' ou le motif du refus."""
    response = requests.post(
        f"{supabase_url()}/rest/v1/rpc/utiliser_code_parrainage",
        headers=service_headers(),
        json={"p_user_id": user_id, "p_code": code, "p_bonus": int(bonus), "p_max_filleuls": int(max_filleuls), "p_jours": int(jours)},
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase utiliser_code_parrainage: HTTP {response.status_code} {response.text[:200]}")
    resultat = response.json()
    return str(resultat if not isinstance(resultat, list) else (resultat[0] if resultat else ""))


# ── Débit atomique (fonction SQL debiter_tokens, supabase_correctifs.sql) ────


def activer_abonnement(user_id: str, plan_nom: str, montant_fcfa: int, tokens: int, session_id: str | None = None) -> dict[str, Any]:
    """Crée/actualise l'abonnement mensuel et synchronise les champs du profil."""
    plan = lire_plan(plan_nom) or _plan_extra_defaut(plan_nom)
    plan_nom = normaliser_plan(plan.get("nom") or plan_nom)
    expiration = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(timespec="seconds")
    profil = {
        "plan": plan_nom,
        "quota_mensuel": int(plan.get("quota_mensuel") or max(0, int(tokens))),
        "statut_abonnement": "actif",
        "date_expiration_abonnement": expiration,
        "date_dernier_renouvellement": now_iso(),
        "date_dernier_paiement": now_iso(),
    }
    try:
        profils = maj_profil(user_id, profil)
    except RuntimeError:
        profils = {}

    abonnement = {
        "user_id": user_id,
        "plan": plan_nom,
        "quota_mensuel": int(plan.get("quota_mensuel") or max(0, int(tokens))),
        "prix_fcfa": int(montant_fcfa),
        "statut": "actif",
        "date_debut": now_iso(),
        "date_expiration": expiration,
        "date_renouvellement": expiration,
        "derniere_transaction_id": session_id or "",
        "session_paiement": session_id or "",
    }
    response = requests.post(
        rest_url("abonnements"),
        headers=service_headers({"Prefer": "return=representation"}),
        json=abonnement,
        timeout=20,
    )
    if response.status_code == 409:
        try:
            retour = requests.patch(
                rest_url("abonnements", {"user_id": f"eq.{user_id}", "order": "date_debut.desc", "limit": "1"}),
                headers=service_headers({"Prefer": "return=representation"}),
                json={
                    "plan": plan_nom,
                    "quota_mensuel": abonnement["quota_mensuel"],
                    "prix_fcfa": abonnement["prix_fcfa"],
                    "statut": "actif",
                    "date_expiration": expiration,
                    "date_renouvellement": expiration,
                    "maj_a": now_iso(),
                },
                timeout=20,
            )
            if retour.ok:
                rows = retour.json();
                if isinstance(rows, list) and rows:
                    return rows[0]
        except Exception:
            pass
    elif not response.ok:
        raise RuntimeError(f"Supabase abonnements (insert): HTTP {response.status_code} {response.text[:300]}")
    rows = response.json()
    return rows[0] if isinstance(rows, list) and rows else {**abonnement, **profils}


def renouveler_abonnement_manuel(user_id: str, plan_nom: str, *, session_id: str | None = None) -> dict[str, Any]:
    plan = lire_plan(plan_nom) or _plan_extra_defaut(plan_nom)
    expiration = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(timespec="seconds")
    profil_updates = {
        "plan": normaliser_plan(plan_nom),
        "quota_mensuel": int(plan.get("quota_mensuel") or 0),
        "statut_abonnement": "actif",
        "date_expiration_abonnement": expiration,
        "date_dernier_renouvellement": now_iso(),
    }
    try:
        maj_profil(user_id, profil_updates)
    except RuntimeError:
        pass
    return activer_abonnement(user_id, plan_nom, int(plan.get("prix_fcfa") or 0), int(plan.get("quota_mensuel") or 0), session_id)


if __name__ == "__main__":
    print("DarkCodex device service")

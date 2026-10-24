"""API DarkCodex — FastAPI.

Endpoints :
  POST /device/code         génère un code de connexion terminal
  GET  /device/verify       le terminal poll toutes les 5 s
  POST /device/confirm      le site web confirme le code
  GET  /verify-token        contrôle du token au lancement du CLI
  POST /chat                envoi d'un message à Gemini + décompte des tokens
  GET  /solde               solde du compte
  POST /confirmer-paiement  validation d'un paiement FedaPay
  GET  /device              page web/device.html
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _load_env(path: Path | None = None) -> None:
    """Charge server/.env si présent (ne surcharge jamais une variable déjà définie)."""
    env_path = path or Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()

try:  # exécution comme package (uvicorn server.main:app)
    from . import device, gemini
except ImportError:  # exécution depuis server/ (uvicorn main:app)
    import device, gemini  # type: ignore[no-redef]

app = FastAPI(title="DarkCodex API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

FEDAPAY_TRANSACTIONS_URL = "https://api.fedapay.com/v1/transactions"
FEDAPAY_OK_STATUSES = {"approved", "paid"}
DEVICE_PAGE = os.environ.get("DARKCODEX_DEVICE_URL", "https://darkcodex.onrender.com/device")


class DeviceConfirmIn(BaseModel):
    code: str
    token: str | None = None


class ChatIn(BaseModel):
    message: str
    historique: list[dict[str, Any]] | None = None


class PaiementIn(BaseModel):
    transaction_id: str
    plan: str
    user_id: str | None = None


# ── Helpers ────────────────────────────────────────────────────


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Token manquant.")
    value = authorization.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if not value:
        raise HTTPException(status_code=401, detail="Token manquant.")
    return value


def _utilisateur(authorization: str | None) -> dict[str, Any]:
    token = _bearer(authorization)
    try:
        session = device.verifier_token(token)
    except RuntimeError as exc:  # Supabase non configuré / hors ligne
        raise HTTPException(status_code=503, detail=f"Service indisponible: {exc}") from exc
    if not session:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré.")
    return {**session, "token": token}


def _erreur(exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"Service indisponible: {exc}"})


# ── 1. Générer le code device ──────────────────────────────────


@app.post("/device/code")
def device_code() -> Any:
    try:
        device.nettoyer_codes_expires()
        code = device.creer_code_unique()
    except Exception as exc:  # Supabase en erreur / non configuré
        return _erreur(exc)
    return {"code": code, "lien": DEVICE_PAGE, "expire_dans": device.CODE_TTL_SECONDS}


# ── 2. Vérifier le code (polling du terminal) ──────────────────


@app.get("/device/verify")
def device_verify(code: str = Query(...)) -> Any:
    try:
        return device.verifier_code(code)
    except Exception as exc:
        return _erreur(exc)


# ── 3. Confirmer le code depuis le site web ────────────────────


@app.post("/device/confirm")
def device_confirm(payload: DeviceConfirmIn, authorization: str | None = Header(None)) -> Any:
    token = payload.token or _bearer(authorization)
    try:
        session = device.verifier_token(token)
    except RuntimeError as exc:
        return _erreur(exc)
    if not session:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré.")
    try:
        ok = device.confirmer_code(payload.code, session["user_id"], token)
    except Exception as exc:
        return _erreur(exc)
    if not ok:
        raise HTTPException(status_code=404, detail="Code expiré ou introuvable.")
    return {
        "status": "confirme",
        "message": "Terminal connecté avec succès !",
        "email": session["email"],
        "nom": session["nom"],
    }


# ── 4. Vérifier le token au lancement ──────────────────────────


@app.get("/verify-token")
def verify_token(authorization: str | None = Header(None)) -> dict[str, Any]:
    session = _utilisateur(authorization)
    return {
        "user_id": session["user_id"],
        "email": session["email"],
        "nom": session["nom"],
        "tokens_solde": session["tokens_solde"],
        "tokens_utilises": session["tokens_utilises"],
        "plan": session["plan"],
    }


# ── 5. Chat via Gemini + décompte des tokens ───────────────────


@app.post("/chat")
def chat(payload: ChatIn, authorization: str | None = Header(None)) -> Any:
    session = _utilisateur(authorization)
    user_id = session["user_id"]
    solde = int(session["tokens_solde"] or 0)
    if solde <= 0:
        raise HTTPException(
            status_code=402,
            detail={
                "status": "solde_insuffisant",
                "tokens_solde": 0,
                "plan": session["plan"],
                "message": "Vos tokens gratuits sont épuisés.",
            },
        )

    try:
        reponse, tokens_utilises = gemini.appeler_gemini(
            payload.message, payload.historique, user_id
        )
    except gemini.GeminiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    tokens_utilises = max(0, tokens_utilises)
    nouveau_solde = max(0, solde - tokens_utilises)
    try:
        device.maj_profil(
            user_id,
            {
                "tokens_solde": nouveau_solde,
                "tokens_utilises": int(session["tokens_utilises"] or 0) + tokens_utilises,
            },
        )
    except Exception as exc:
        return _erreur(exc)

    return {
        "reponse": reponse,
        "tokens_utilises": tokens_utilises,
        "solde_restant": nouveau_solde,
        "plan": session["plan"],
    }


# ── 6. Solde ───────────────────────────────────────────────────


@app.get("/solde")
def solde(authorization: str | None = Header(None)) -> dict[str, Any]:
    session = _utilisateur(authorization)
    return {
        "tokens_solde": session["tokens_solde"],
        "tokens_utilises": session["tokens_utilises"],
        "plan": session["plan"],
    }


# ── 7. Confirmation de paiement FedaPay ────────────────────────


def _transaction_fedapay(transaction_id: str) -> dict[str, Any]:
    secret = os.environ.get("FEDAPAY_SECRET_KEY", "").strip()
    if not secret:
        raise RuntimeError("FEDAPAY_SECRET_KEY manquante.")
    response = requests.get(
        f"{FEDAPAY_TRANSACTIONS_URL}/{transaction_id}",
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"FedaPay HTTP {response.status_code}")
    data = response.json()
    return data.get("transaction") if isinstance(data.get("transaction"), dict) else data


@app.post("/confirmer-paiement")
def confirmer_paiement(payload: PaiementIn, authorization: str | None = Header(None)) -> Any:
    session = _utilisateur(authorization)
    user_id = session["user_id"]
    if payload.user_id and payload.user_id != user_id:
        raise HTTPException(status_code=403, detail="user_id ne correspond pas au token.")

    plan = device.lire_plan(payload.plan)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan inconnu.")
    tokens = int(plan.get("tokens") or 0)
    prix = int(plan.get("prix_fcfa") or 0)

    try:
        transaction = _transaction_fedapay(payload.transaction_id)
    except Exception as exc:
        return _erreur(exc)

    statut = str(transaction.get("status") or "").lower()
    if statut not in FEDAPAY_OK_STATUSES:
        raise HTTPException(
            status_code=402,
            detail={"status": "paiement_non_valide", "etat": statut or "inconnu"},
        )

    try:
        profil = device.lire_profil(user_id) or {}
        if device.deja_credite(payload.transaction_id):
            return {
                "status": "deja_credite",
                "tokens_ajoutes": 0,
                "tokens_solde": int(profil.get("tokens_solde") or 0),
                "plan": profil.get("plan") or "gratuit",
            }
        nouveau_solde = int(profil.get("tokens_solde") or 0) + tokens
        profil = device.maj_profil(
            user_id, {"tokens_solde": nouveau_solde, "plan": payload.plan}
        )
        device.enregistrer_achat(
            user_id, payload.plan, tokens, prix, payload.transaction_id
        )
    except Exception as exc:
        return _erreur(exc)

    return {
        "status": "confirme",
        "plan": payload.plan,
        "tokens_ajoutes": tokens,
        "tokens_solde": int(profil.get("tokens_solde") or nouveau_solde),
    }


# ── Page web de confirmation ───────────────────────────────────


@app.get("/device", include_in_schema=False)
def device_page() -> FileResponse:
    page = WEB_DIR / "device.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Page device introuvable.")
    return FileResponse(page, media_type="text/html; charset=utf-8")


@app.get("/", include_in_schema=False)
def racine() -> dict[str, Any]:
    return {"service": "DarkCodex API", "device": DEVICE_PAGE}
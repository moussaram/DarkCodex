"""API DarkCodex — FastAPI.

Endpoints :
  POST /device/code         génère un code de connexion terminal
  GET  /device/verify       le terminal poll toutes les 5 s
  POST /device/confirm      le site web confirme le code
  GET  /verify-token        contrôle du token au lancement du CLI
  POST /chat                envoi d'un message à Gemini + décompte des tokens
  GET  /solde               solde du compte
  POST /paiement/creer      crée une page de paiement SasPay pour un plan
  POST /confirmer-paiement  vérifie le paiement auprès de SasPay puis crédite les tokens
  POST /webhooks/saspay     notifications signées de SasPay
  GET  /device              page web/device.html
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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
    from . import device, gemini, saspay
except ImportError:  # exécution depuis server/ (uvicorn main:app)
    import device, gemini, saspay  # type: ignore[no-redef]

try:  # la version du dépôt déployé = dernière version disponible
    from darkcodex import __version__ as VERSION_DEPOT
except ImportError:
    VERSION_DEPOT = "0.0.0"

app = FastAPI(title="DarkCodex API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

logger = logging.getLogger("darkcodex.server")
DEVICE_PAGE = os.environ.get("DARKCODEX_DEVICE_URL", "https://darkcodex.onrender.com/device")


class DeviceConfirmIn(BaseModel):
    code: str
    token: str | None = None


class ChatIn(BaseModel):
    message: str
    historique: list[dict[str, Any]] | None = None


class PaiementCreerIn(BaseModel):
    plan: str


class PaiementIn(BaseModel):
    session_id: str


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
        if not device.code_en_attente(payload.code):
            raise HTTPException(status_code=404, detail="Code expiré ou introuvable.")
        # Le terminal reçoit son PROPRE jeton longue durée, jamais le jeton du navigateur.
        jeton_appareil = device.creer_appareil(session["user_id"], session["email"], session["nom"])
        try:
            ok = device.confirmer_code(payload.code, session["user_id"], jeton_appareil)
        except Exception:
            device.revoquer_appareil(jeton_appareil)
            raise
        if not ok:
            device.revoquer_appareil(jeton_appareil)
            raise HTTPException(status_code=404, detail="Code expiré ou introuvable.")
    except HTTPException:
        raise
    except Exception as exc:
        return _erreur(exc)
    return {
        "status": "confirme",
        "message": "Terminal connecté avec succès !",
        "email": session["email"],
        "nom": session["nom"],
    }


@app.post("/logout")
def logout(authorization: str | None = Header(None)) -> Any:
    """Révoque le jeton d'appareil du terminal qui appelle."""
    token = _bearer(authorization)
    try:
        return {"revoque": device.revoquer_appareil(token)}
    except Exception as exc:
        return _erreur(exc)


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


# ── 7. Paiements SasPay ────────────────────────────────────────
#
# Flux sûr : c'est LE SERVEUR qui fixe le montant (table plans) et qui lie la
# session de paiement à l'utilisateur connecté. Le client ne peut donc ni
# choisir un prix, ni réclamer le paiement d'un autre.


@app.post("/paiement/creer")
def paiement_creer(payload: PaiementCreerIn, authorization: str | None = Header(None)) -> Any:
    session = _utilisateur(authorization)
    user_id = session["user_id"]
    try:
        plan = device.lire_plan(payload.plan)
    except Exception as exc:
        return _erreur(exc)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan inconnu.")
    tokens = int(plan.get("tokens") or 0)
    prix = int(plan.get("prix_fcfa") or 0)
    if prix <= 0 or tokens <= 0:
        raise HTTPException(status_code=400, detail="Ce plan n'est pas payant.")
    email = str(session.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Aucun email sur le compte.")
    nom = str(session.get("nom") or "").strip() or email.split("@")[0] or "Client"

    try:
        checkout = saspay.creer_session(
            montant=prix,
            description=f"DarkCodex - plan {plan['nom']}",
            email=email,
            nom=nom,
            return_url=os.environ.get("SASPAY_RETURN_URL", "").strip(),
            metadata={"user_id": user_id, "plan": plan["nom"]},
        )
        device.creer_paiement(user_id, plan["nom"], tokens, prix, str(checkout["id"]))
    except Exception as exc:
        return _erreur(exc)

    return {
        "session_id": str(checkout["id"]),
        "checkout_url": checkout["checkout_url"],
        "montant": prix,
        "devise": saspay.DEVISE,
        "plan": plan["nom"],
    }


def _traiter_paiement(paiement: dict[str, Any]) -> dict[str, Any]:
    """Vérifie auprès de SasPay puis crédite les tokens, UNE seule fois."""
    session_id = str(paiement["session_id"])
    user_id = str(paiement["user_id"])
    plan = str(paiement["plan"])
    tokens = int(paiement["tokens"])
    prix = int(paiement["montant_fcfa"])

    statut_local = str(paiement.get("statut") or "")
    if statut_local == "credite":
        return {"status": "deja_credite", "tokens_ajoutes": 0, "plan": plan}
    if statut_local != "en_attente":
        raise HTTPException(status_code=409, detail={"status": "paiement_bloque", "etat": statut_local})

    # 1. L'état réel du paiement, revérifié par SasPay (jamais notre mémoire).
    statut = saspay.statut_session(session_id)
    if not saspay.est_paye(statut):
        etat = str(statut.get("status") or "inconnu").lower()
        raise HTTPException(status_code=402, detail={"status": "paiement_non_valide", "etat": etat})

    # 2. Le montant, la devise et la commande doivent correspondre à ce qu'on a créé.
    details = saspay.lire_session(session_id)
    meta = details.get("metadata") if isinstance(details.get("metadata"), dict) else {}
    coherent = (
        saspay.meme_montant(details.get("amount"), prix)
        and str(details.get("currency") or "").upper() == saspay.DEVISE
        and (not meta.get("user_id") or str(meta["user_id"]) == user_id)
        and (not meta.get("plan") or str(meta["plan"]) == plan)
    )
    if not coherent:
        logger.error("Paiement %s incohérent avec la commande (montant/devise/métadonnées).", session_id)
        device.marquer_anomalie(session_id)
        raise HTTPException(status_code=409, detail={"status": "paiement_incoherent"})

    # 3. Réservation atomique : un seul appel gagne, les autres voient « déjà crédité ».
    if not device.reserver_paiement(session_id):
        return {"status": "deja_credite", "tokens_ajoutes": 0, "plan": plan}
    try:
        profil = device.crediter_profil(user_id, tokens, plan)
    except Exception:
        device.liberer_paiement(session_id)  # rien n'a été crédité : on pourra réessayer
        raise
    try:
        device.enregistrer_achat(user_id, plan, tokens, prix, session_id)
    except Exception:  # l'historique ne doit jamais annuler un crédit déjà fait
        logger.exception("Achat %s crédité mais non enregistré dans achats.", session_id)

    return {
        "status": "confirme",
        "plan": plan,
        "tokens_ajoutes": tokens,
        "tokens_solde": int(profil.get("tokens_solde") or 0),
    }


@app.post("/confirmer-paiement")
def confirmer_paiement(payload: PaiementIn, authorization: str | None = Header(None)) -> Any:
    session = _utilisateur(authorization)
    try:
        paiement = device.lire_paiement(payload.session_id.strip())
    except Exception as exc:
        return _erreur(exc)
    # Même réponse si la session n'existe pas ou appartient à quelqu'un d'autre.
    if not paiement or str(paiement.get("user_id")) != session["user_id"]:
        raise HTTPException(status_code=404, detail="Paiement introuvable.")
    try:
        return _traiter_paiement(paiement)
    except HTTPException:
        raise
    except Exception as exc:
        return _erreur(exc)


def _reconcilier() -> dict[str, Any]:
    """Après un webhook : vérifie les paiements récents encore en attente."""
    credites = 0
    for paiement in device.paiements_en_attente(limite=5):
        try:
            resultat = _traiter_paiement(paiement)
        except HTTPException:
            continue  # pas encore payé, ou incohérent : déjà journalisé
        except Exception:
            logger.exception("Réconciliation du paiement %s échouée.", paiement.get("session_id"))
            continue
        if resultat.get("status") == "confirme":
            credites += 1
    return {"ok": True, "credites": credites}


@app.post("/webhooks/saspay")
async def webhook_saspay(request: Request) -> Any:
    """Notification SasPay. La signature est contrôlée sur le corps BRUT ; le
    contenu du webhook n'est qu'un déclencheur : l'état réel est revérifié."""
    corps = await request.body()
    try:
        secret = saspay.webhook_secret()
    except saspay.SasPayError as exc:
        return _erreur(exc)
    if not saspay.verifier_signature(
        corps,
        request.headers.get("X-Webhook-Signature", ""),
        request.headers.get("X-Webhook-Timestamp", ""),
        secret,
    ):
        raise HTTPException(status_code=403, detail="Signature invalide.")
    try:
        evenement = json.loads(corps)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Corps JSON invalide.") from exc
    if not isinstance(evenement, dict) or evenement.get("event") != "transaction.success":
        return {"ok": True, "credites": 0}
    try:
        return await run_in_threadpool(_reconcilier)
    except Exception as exc:
        return _erreur(exc)  # 503 : SasPay réessaiera plus tard


# ── Pages du site, configuration publique, plans, version ──────

PAGES = {
    "/device": "device.html",
    "/connexion": "connexion.html",
    "/abonnements": "abonnements.html",
    "/compte": "compte.html",
    "/paiement/retour": "retour.html",
}


def _servir_page(nom_fichier: str) -> FileResponse:
    page = WEB_DIR / nom_fichier
    if not page.exists():
        raise HTTPException(status_code=404, detail="Page introuvable.")
    return FileResponse(page, media_type="text/html; charset=utf-8")


def _creer_route_page(nom_fichier: str):
    def route() -> FileResponse:
        return _servir_page(nom_fichier)
    return route


for _chemin, _fichier in PAGES.items():
    app.add_api_route(_chemin, _creer_route_page(_fichier), methods=["GET"], include_in_schema=False)


def _cle_publique_valide(cle: str) -> bool:
    """Vrai seulement pour une clé « anon / publishable » (jamais service_role / secret)."""
    if cle.startswith("sb_secret_"):
        return False
    if cle.startswith("sb_publishable_"):
        return True
    try:
        charge = cle.split(".")[1]
        charge += "=" * (-len(charge) % 4)
        return json.loads(base64.urlsafe_b64decode(charge)).get("role") == "anon"
    except (IndexError, ValueError):
        return False


@app.get("/config", include_in_schema=False)
def config_publique() -> dict[str, str]:
    """Valeurs publiques lues par les pages web (la clé anon est faite pour être publique)."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    cle = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not cle:
        raise HTTPException(status_code=503, detail="SUPABASE_URL / SUPABASE_ANON_KEY manquants.")
    if not _cle_publique_valide(cle):
        raise HTTPException(status_code=503, detail="SUPABASE_ANON_KEY doit être la clé anon (publique).")
    return {"supabase_url": url, "supabase_anon_key": cle}


@app.get("/plans")
def plans() -> Any:
    try:
        return device.lister_plans()
    except Exception as exc:
        return _erreur(exc)


@app.get("/version")
def version() -> dict[str, str]:
    return {
        "latest": os.environ.get("DARKCODEX_LATEST_VERSION", "").strip() or VERSION_DEPOT,
        "minimum": os.environ.get("DARKCODEX_MIN_VERSION", "").strip() or "0.0.0",
        "url": os.environ.get("DARKCODEX_REPO_URL", "").strip()
        or "https://github.com/moussaram/DarkCodex",
        "notes": os.environ.get("DARKCODEX_RELEASE_NOTES", "").strip(),
    }


@app.get("/", include_in_schema=False)
def racine() -> dict[str, Any]:
    return {"service": "DarkCodex API", "device": DEVICE_PAGE}


# ── Parrainage ──────────────────────────────────────────────────
PARRAINAGE_REFUS = {
    "code_inconnu": (404, "Code de parrainage inconnu."),
    "code_personnel": (400, "Tu ne peux pas utiliser ton propre code."),
    "code_invalide": (400, "Ce code ne peut pas être utilisé."),
    "deja_parraine": (409, "Tu as déjà utilisé un code de parrainage."),
    "parrain_complet": (409, "Ce code a atteint son nombre maximum de filleuls."),
    "compte_trop_ancien": (403, "Le code de parrainage doit être utilisé dans les premiers jours du compte."),
    "profil_introuvable": (404, "Profil introuvable."),
}


def _entier_env(nom: str, defaut: int) -> int:
    try:
        return int(os.environ.get(nom, "").strip())
    except ValueError:
        return defaut


class ParrainageIn(BaseModel):
    code: str


@app.get("/parrainage")
def parrainage(authorization: str | None = Header(None)) -> Any:
    session = _utilisateur(authorization)
    try:
        code = device.obtenir_code_parrainage(session["user_id"])
        stats = device.stats_parrainage(session["user_id"])
    except Exception as exc:
        return _erreur(exc)
    return {"code": code, "bonus": _entier_env("PARRAINAGE_BONUS", 2000), **stats}


@app.post("/parrainage/utiliser")
def parrainage_utiliser(payload: ParrainageIn, authorization: str | None = Header(None)) -> Any:
    session = _utilisateur(authorization)
    code = payload.code.strip().upper()
    if not re.fullmatch(r"DK-[A-Z0-9]{6}", code):
        raise HTTPException(status_code=400, detail="Format de code invalide (exemple : DK-AB23CD).")
    bonus = _entier_env("PARRAINAGE_BONUS", 2000)
    try:
        resultat = device.utiliser_parrainage(
            session["user_id"], code, bonus,
            _entier_env("PARRAINAGE_MAX_FILLEULS", 20), _entier_env("PARRAINAGE_JOURS_MAX", 7),
        )
    except Exception as exc:
        return _erreur(exc)
    if resultat == "ok":
        return {"status": "ok", "tokens_ajoutes": bonus}
    if resultat in PARRAINAGE_REFUS:
        statut, message = PARRAINAGE_REFUS[resultat]
        raise HTTPException(status_code=statut, detail=message)
    return _erreur(RuntimeError("Réponse inattendue du parrainage."))


# Fichiers statiques du site (auth.js, site.css) : à garder TOUT EN BAS du fichier.
if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
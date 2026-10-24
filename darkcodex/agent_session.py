"""Sessions et reprise (Partie 8, correction C06) : /session save|list|resume.

Chaque session est écrite dans .darkcodex/sessions/<slug>.json à la racine du
projet, et contient l'historique des messages, les fichiers modifiés et les
commandes exécutées. L'ancien dossier global (~/.darkcodex/sessions) et
~/.darkcodex_history.json sont migrés automatiquement à la première utilisation.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

from .fsutil import atomic_write_text

STATE_DIR = Path(os.environ.get("DARKCODEX_HOME", Path.home() / ".darkcodex"))

MAX_MESSAGES = 200


def _ancien_dossier() -> Path:
    """Ancien emplacement global des sessions (résolu à chaque appel, testable)."""
    return Path(os.environ.get("DARKCODEX_HOME", Path.home() / ".darkcodex")) / "sessions"


def _ancien_historique() -> Path:
    """Ancien ~/.darkcodex_history.json (à côté du dossier d'état)."""
    return Path(os.environ.get("DARKCODEX_HOME", Path.home() / ".darkcodex")).parent / ".darkcodex_history.json"


def horodatage() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def dossier_sessions(cwd: Path) -> Path:
    """C06 : sessions dans le projet lui-même (.darkcodex/sessions)."""
    return Path(cwd) / ".darkcodex" / "sessions"


def slugifier(nom: str) -> str:
    propre = re.sub(r"[^A-Za-z0-9._-]+", "_", (nom or "").strip())
    return propre.strip("._-") or horodatage()


def chemin_session(cwd: Path, nom: str | None = None) -> Path:
    return dossier_sessions(cwd) / f"{slugifier(nom) or horodatage()}.json"


def _lire(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _convertir_historique(path: Path) -> dict | None:
    """~/.darkcodex_history.json → session .darkcodex/sessions/historique.json."""
    try:
        brut = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    items = brut if isinstance(brut, list) else (brut.get("conversations") or [])
    messages: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        demande = str(item.get("prompt") or item.get("user") or item.get("question") or "").strip()
        reponse = str(
            item.get("response") or item.get("assistant") or item.get("answer") or ""
        ).strip()
        date = str(item.get("timestamp") or item.get("date") or "")
        if demande:
            messages.append({"role": "user", "content": demande[:20000], "date": date})
        if reponse:
            messages.append({"role": "assistant", "content": reponse[:20000], "date": date})
    if not messages:
        return None
    return {
        "nom": "historique",
        "horodatage": horodatage(),
        "date": messages[-1].get("date")
        or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "projet": "",
        "mode": "suggest",
        "messages": messages,
        "fichiers_modifies": [],
        "commandes": [],
    }


def migrer_sessions(cwd: Path) -> None:
    """C06 : importe l'ancien dossier global et l'ancien historique, une fois."""
    local = dossier_sessions(cwd)
    local.mkdir(parents=True, exist_ok=True)

    ancien = _ancien_dossier()
    if ancien.is_dir():
        for source in sorted(ancien.glob("session_*.json")) + sorted(ancien.glob("*.md")):
            cible = local / source.name
            if cible.exists():
                continue
            try:
                cible.write_bytes(source.read_bytes())
            except OSError:
                continue

    historique = _ancien_historique()
    if historique.is_file():
        cible = local / "historique.json"
        if not cible.exists():
            donnees = _convertir_historique(historique)
            if donnees:
                atomic_write_text(cible, json.dumps(donnees, indent=2, ensure_ascii=False) + "\n")


def sauvegarder(nom: str, ctx: Any) -> Path:
    store = getattr(ctx, "store", None)
    session_id = getattr(ctx, "session_id", None)

    messages: list[dict] = []
    commandes: list[dict] = []
    modifs: list[dict] = []
    if store is not None and session_id is not None:
        messages = [
            {"role": row["role"], "content": row["content"], "date": row["created_at"]}
            for row in store.messages_session(session_id, MAX_MESSAGES)
        ]
        modifs = [
            {"path": row["path"], "action": row["action"], "date": row["created_at"]}
            for row in store.recent_modifications(session_id, 100)
        ]
    if store is not None:
        commandes = [
            {"commande": row["command"], "exit_code": row["exit_code"], "date": row["created_at"]}
            for row in store.recent_runs(ctx.cwd, 50)
        ]

    donnees = {
        "nom": nom or horodatage(),
        "horodatage": horodatage(),
        "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "projet": str(ctx.cwd),
        "mode": getattr(ctx, "mode", "suggest"),
        "messages": messages,
        "fichiers_modifies": modifs,
        "commandes": commandes,
    }

    migrer_sessions(ctx.cwd)
    path = chemin_session(ctx.cwd, nom)
    atomic_write_text(path, json.dumps(donnees, indent=2, ensure_ascii=False) + "\n")
    return path


def lister(cwd: Path) -> list[tuple[Path, dict]]:
    migrer_sessions(cwd)
    sessions_dir = dossier_sessions(cwd)
    if not sessions_dir.is_dir():
        return []
    sessions: list[tuple[Path, dict]] = []
    for path in sorted(sessions_dir.glob("*.json")):
        data = _lire(path)
        if data:
            sessions.append((path, data))
    sessions.sort(key=lambda item: item[1].get("date") or "")
    return sessions


def trouver(nom: str | None, cwd: Path) -> tuple[Path, dict] | None:
    sessions = lister(cwd)
    if not sessions:
        return None
    if not nom:
        return sessions[-1]
    nom = nom.strip().lower()
    for path, data in sessions:
        if str(data.get("nom", "")).lower() == nom or path.stem.lower().endswith(nom):
            return path, data
    return None


def afficher_liste(ctx: Any) -> None:
    cwd = getattr(ctx, "cwd", Path.cwd())
    sessions = lister(cwd)
    if not sessions:
        print("Aucune session sauvegardée. Utilise /session save [nom].")
        return
    print("\n📚 Sessions sauvegardées :")
    for path, data in sessions:
        nom = data.get("nom") or path.stem
        projet = Path(str(data.get("projet") or "?")).name
        nb = len(data.get("messages") or [])
        date = str(data.get("date") or "")[:19].replace("T", " ")
        print(f"  • {nom:<24} {projet:<16} {nb:>3} messages  {date}")


def reprendre(nom: str | None, ctx: Any) -> bool:
    trouve = trouver(nom, ctx.cwd)
    if not trouve:
        print(f"Session introuvable : {nom or 'dernière session'}.")
        return False
    path, data = trouve

    projet = str(data.get("projet") or ctx.cwd)
    print(f"\n🔁 Je reprends le travail sur {Path(projet).name}...")

    messages = data.get("messages") or []
    modifs = data.get("fichiers_modifies") or []
    commandes = data.get("commandes") or []
    print(f"   Session : {data.get('nom') or path.stem} du {str(data.get('date') or '')[:19].replace('T', ' ')}")
    print(f"   {len(messages)} message(s), {len(modifs)} fichier(s) modifié(s), {len(commandes)} commande(s)")

    if modifs:
        print("   Fichiers modifiés :")
        for item in modifs[-10:]:
            print(f"     - {item.get('action', '?')}: {item.get('path', '?')}")
    if messages:
        dernier = messages[-1]
        print(f"   Dernier échange ({dernier.get('role')}) : {str(dernier.get('content') or '')[:120]}")

    # Rejoue l'historique dans la session courante pour que le contexte soit repris.
    store = getattr(ctx, "store", None)
    session_id = getattr(ctx, "session_id", None)
    if store is not None and session_id is not None and messages:
        store.import_messages(
            session_id,
            [(str(m.get("role") or "user"), str(m.get("content") or "")) for m in messages],
        )
        print("   Historique restauré dans la session courante.")

    print(f"   Fichier : {path.name}\n")
    return True


def sauvegarder_et_afficher(nom: str, ctx: Any) -> int:
    path = sauvegarder(nom, ctx)
    data = _lire(path) or {}
    print(f"\033[92m✅ Session sauvegardée : {data.get('nom')} ({path.name})\033[0m")
    print(f"   {len(data.get('messages') or [])} message(s), {len(data.get('fichiers_modifies') or [])} fichier(s) modifié(s)")
    return 0

"""Vérification et installation des mises à jour de DarkCodex.

Règles : jamais de mise à jour sans l'accord de l'utilisateur, jamais d'erreur
bloquante si le réseau est coupé, au plus une vérification par jour.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

CACHE_PATH = Path.home() / ".darkcodex_update.json"
CHECK_INTERVAL_SECONDS = 24 * 3600
RETRY_SECONDS = 3600  # si le serveur est injoignable, on réessaie dans 1 heure
HTTP_TIMEOUT_SECONDS = 3
RACINE = Path(__file__).resolve().parent.parent
DEPOT_URL = "https://github.com/moussaram/DarkCodex"


def parse_version(texte: Any) -> tuple[int, int, int]:
    """'v2.1.0' -> (2, 1, 0). Les parties manquantes ou illisibles valent 0."""
    morceaux = str(texte or "").strip().lstrip("vV").split(".")
    nombres: list[int] = []
    for morceau in morceaux[:3]:
        chiffres = ""
        for caractere in morceau:
            if not caractere.isdigit():
                break
            chiffres += caractere
        nombres.append(int(chiffres) if chiffres else 0)
    while len(nombres) < 3:
        nombres.append(0)
    return (nombres[0], nombres[1], nombres[2])


def est_plus_recente(candidate: Any, actuelle: Any) -> bool:
    return parse_version(candidate) > parse_version(actuelle)


def lire_cache(chemin: Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads((chemin or CACHE_PATH).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def ecrire_cache(data: dict[str, Any], chemin: Path | None = None) -> None:
    try:
        (chemin or CACHE_PATH).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # un cache illisible ne doit jamais gêner l'utilisateur


def recuperer_version(api_base: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """Interroge GET /version. Retourne None si le serveur est injoignable."""
    try:
        with urllib.request.urlopen(f"{api_base.rstrip('/')}/version", timeout=timeout) as reponse:
            data = json.loads(reponse.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _git(racine: Path, *arguments: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(racine), *arguments], capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(arguments, 1, "", str(exc))


def installer_mise_a_jour(racine: Path = RACINE) -> tuple[bool, str]:
    """git pull --ff-only dans le dossier d'installation. Retourne (succès, message)."""
    if not (racine / ".git").exists():
        return False, f"Installation sans Git : télécharge la nouvelle version sur {DEPOT_URL}"
    if shutil.which("git") is None:
        return False, "Git n'est pas installé. Termux : pkg install git"

    etat = _git(racine, "status", "--porcelain", "--untracked-files=no")
    if etat.returncode != 0:
        return False, "Impossible de lire l'état Git du dossier d'installation."
    if etat.stdout.strip():
        return False, (
            f"Des fichiers de {racine} ont été modifiés localement. "
            "Sauvegarde-les (git stash) puis relance : darkcodex update"
        )

    tirage = _git(racine, "pull", "--ff-only")
    if tirage.returncode != 0:
        return False, "git pull a échoué : " + (tirage.stderr or tirage.stdout).strip()[:300]

    exigences = racine / "requirements.txt"
    if exigences.exists():
        try:
            pip = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "-r", str(exigences)],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return True, f"Code mis à jour, mais pip n'a pas pu s'exécuter ({exc})."
        if pip.returncode != 0:
            return True, "Code mis à jour, mais pip a échoué : " + (pip.stderr or "").strip()[:200]

    return True, "Mise à jour installée."


def _oui(reponse: str, defaut: bool) -> bool:
    texte = reponse.strip().lower()
    return defaut if not texte else texte in ("o", "oui", "y", "yes")


def verifier_au_lancement(
    api_base: str,
    version_actuelle: str,
    *,
    interactif: bool | None = None,
    cache_path: Path | None = None,
    maintenant: float | None = None,
    recuperer: Callable[[str], dict[str, Any] | None] | None = None,
    installer: Callable[[], tuple[bool, str]] | None = None,
    demander: Callable[[str], str] | None = None,
) -> None:
    """Appelée au démarrage de `chat` et `ask`. Peut quitter le programme (SystemExit)."""
    if os.environ.get("DARKCODEX_NO_UPDATE_CHECK"):
        return

    if interactif is None:
        interactif = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactif:
        return  # jamais de question dans un script ou un pipe

    recuperer = recuperer or recuperer_version
    installer = installer or installer_mise_a_jour
    demander = demander or input

    maintenant = time.time() if maintenant is None else maintenant
    cache = lire_cache(cache_path)
    minimum = str(cache.get("minimum") or "0.0.0")
    latest = str(cache.get("latest") or "")
    recent = maintenant - float(cache.get("checked_at") or 0) < CHECK_INTERVAL_SECONDS

    info = None
    if not recent:
        info = recuperer(api_base)
        if info is None:
            ecrire_cache(
                {**cache, "checked_at": maintenant - CHECK_INTERVAL_SECONDS + RETRY_SECONDS},
                cache_path,
            )
        else:
            latest = str(info.get("latest") or "")
            minimum = str(info.get("minimum") or "0.0.0")
            ecrire_cache({"checked_at": maintenant, "latest": latest, "minimum": minimum}, cache_path)

    obligatoire = est_plus_recente(minimum, version_actuelle)
    nouvelle = info is not None and est_plus_recente(latest, version_actuelle)
    if not obligatoire and not nouvelle:
        return

    if obligatoire:
        print(
            f"\n⚠️ La version {version_actuelle} n'est plus supportée. "
            f"Mise à jour obligatoire vers {minimum} ou plus."
        )
        question, defaut = "Mettre à jour maintenant ? [O/n] ", True
    else:
        print(f"\n🆕 Nouvelle version de DarkCodex disponible : {latest} (tu utilises {version_actuelle}).")
        question, defaut = "Mettre à jour maintenant ? [o/N] ", False

    try:
        accepte = _oui(demander(question), defaut)
    except (EOFError, KeyboardInterrupt):
        accepte = False

    if not accepte:
        if obligatoire:
            raise SystemExit(1)
        return

    ok, message = installer()
    print(message)
    if ok:
        print("Relance DarkCodex pour utiliser la nouvelle version.")
        raise SystemExit(0)
    if obligatoire:
        raise SystemExit(1)


def mettre_a_jour_maintenant(
    api_base: str,
    version_actuelle: str,
    *,
    recuperer: Callable[[str], dict[str, Any] | None] | None = None,
    installer: Callable[[], tuple[bool, str]] | None = None,
) -> int:
    """Commande `darkcodex update` : l'utilisateur a déjà donné son accord."""
    info = (recuperer or recuperer_version)(api_base)
    if info is None:
        print("Impossible de joindre le serveur DarkCodex. Réessaie plus tard.")
        return 1

    latest = str(info.get("latest") or "")
    if not est_plus_recente(latest, version_actuelle):
        print(f"Tu utilises déjà la dernière version ({version_actuelle}).")
        return 0

    print(f"Mise à jour {version_actuelle} → {latest}...")
    ok, message = (installer or installer_mise_a_jour)()
    print(message)
    if ok:
        print("Relance DarkCodex pour utiliser la nouvelle version.")
    return 0 if ok else 1

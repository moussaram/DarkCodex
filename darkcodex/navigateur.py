"""Ouverture automatique du navigateur (Termux, Linux, macOS, Windows)."""
from __future__ import annotations

import os
import shutil
import subprocess
import webbrowser


def url_sure(url: str) -> bool:
    """N'accepte que http(s) : jamais d'option ni de schéma exotique envoyé au système."""
    return isinstance(url, str) and url.startswith(("https://", "http://")) and " " not in url


def ouvrir(url: str) -> bool:
    """Essaie d'ouvrir `url`. Retourne True si une commande a été lancée. Ne lève jamais d'exception."""
    if os.environ.get("DARKCODEX_NO_BROWSER") or not url_sure(url):
        return False

    commandes = []
    if os.environ.get("TERMUX_VERSION") or shutil.which("termux-open-url"):
        commandes.append(["termux-open-url", url])
    commandes.append(["xdg-open", url])

    for commande in commandes:
        if shutil.which(commande[0]) is None:
            continue
        try:
            resultat = subprocess.run(
                commande, check=False, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if resultat.returncode == 0:
            return True

    try:
        return bool(webbrowser.open(url))  # Windows, macOS, bureaux Linux
    except Exception:  # noqa: BLE001 - aucune erreur du navigateur ne doit bloquer la connexion
        return False

"""Dictée vocale sur Termux : /dicter (nécessite l'application Termux:API)."""
from __future__ import annotations

import shutil
import subprocess
from typing import Callable

COMMANDE = "termux-speech-to-text"
AIDE_INSTALLATION = (
    "La dictée demande Termux:API : installe l'application « Termux:API » (même source que Termux), "
    "puis tape dans Termux : pkg install termux-api"
)


def disponible() -> bool:
    return shutil.which(COMMANDE) is not None


def nettoyer(texte: str) -> str:
    return " ".join((texte or "").split())


def dicter(delai: int = 60) -> tuple[bool, str]:
    """Lance la reconnaissance vocale du téléphone. Retourne (succès, texte ou message d'erreur)."""
    if not disponible():
        return False, AIDE_INSTALLATION
    try:
        resultat = subprocess.run([COMMANDE], capture_output=True, text=True, timeout=delai)
    except subprocess.TimeoutExpired:
        return False, "Aucune voix détectée (délai dépassé)."
    except OSError as exc:
        return False, f"Dictée impossible : {exc}"
    texte = nettoyer(resultat.stdout)
    if resultat.returncode != 0 or not texte:
        return False, "Aucun texte reconnu. Réessaie en parlant plus près du micro."
    return True, texte


def dicter_et_valider(
    dicteur: Callable[[], tuple[bool, str]] | None = None,
    demander: Callable[[str], str] | None = None,
    afficher: Callable[[str], None] = print,
) -> str | None:
    """Dicte, montre le texte compris, laisse envoyer / annuler / corriger. None = rien à envoyer."""
    demander = demander or input
    afficher("🎤 Parle maintenant...")
    ok, texte = (dicteur or dicter)()
    if not ok:
        afficher(texte)
        return None
    afficher(f"J'ai compris : « {texte} »")
    try:
        choix = demander("Envoyer ? [O = oui, n = annuler, m = modifier] ").strip().lower()
        if choix in {"n", "non"}:
            return None
        if choix in {"m", "modifier"}:
            corrige = demander("Tape la version corrigée (Entrée = garder) : ").strip()
            return corrige or texte
    except (EOFError, KeyboardInterrupt):
        return None
    return texte

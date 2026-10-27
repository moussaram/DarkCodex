"""Estimation et suivi du coût en tokens : /cout, /limite, confirmation avant les grosses demandes.

L'estimation est approximative (environ 4 caractères par token). Le décompte réel,
facturé, est celui du serveur et s'affiche après chaque réponse.

"""
from __future__ import annotations

import os
import sys
from typing import Callable

SEUIL_CONFIRMATION_DEFAUT = 5000


def estimer_tokens(texte: str) -> int:
    return max(1, (len(texte or "") + 3) // 4)


def _entier_env(nom: str) -> int | None:
    try:
        valeur = int(os.environ.get(nom, "").strip())
    except ValueError:
        return None
    return valeur if valeur > 0 else None


class SuiviCout:
    def __init__(self, limite: int | None = None, seuil: int = SEUIL_CONFIRMATION_DEFAUT):
        self.utilises = 0  # mesurés par le serveur
        self.estimes = 0  # estimés (mode local, sans compte)
        self.limite = limite
        self.seuil = seuil

    @classmethod
    def depuis_environnement(cls) -> "SuiviCout":
        return cls(_entier_env("DARKCODEX_MAX_TOKENS"), _entier_env("DARKCODEX_COST_CONFIRM") or SEUIL_CONFIRMATION_DEFAUT)

    @property
    def total(self) -> int:
        return self.utilises + self.estimes

    def ajouter(self, utilises: object) -> None:
        try:
            self.utilises += max(0, int(utilises))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass

    def ajouter_estimation(self, prompt: str, reponse: str) -> None:
        self.estimes += estimer_tokens(prompt) + estimer_tokens(reponse)

    def definir_limite(self, valeur: str) -> tuple[bool, str]:
        texte = (valeur or "").strip().lower()
        if texte in {"off", "non", "0", "aucune"}:
            self.limite = None
            return True, "Limite de session supprimée."
        try:
            nombre = int(texte.replace(" ", "").replace("_", ""))
        except ValueError:
            return False, "Usage : /limite 20000 ou /limite off"
        if nombre <= 0:
            return False, "La limite doit être un nombre positif."
        self.limite = nombre
        return True, f"Limite de session : {nombre:,} tokens.".replace(",", " ")

    def verifier(
        self,
        tokens_estimes: int,
        confirmer: bool = True,
        interactif: bool | None = None,
        demander: Callable[[str], str] | None = None,
    ) -> tuple[bool, str]:
        """(True, "") si la demande peut partir ; sinon (False, message à afficher)."""
        if self.limite is not None and self.total + tokens_estimes > self.limite:
            return False, (
                f"⛔ Limite de session atteinte : {self.total} utilisés + ~{tokens_estimes} estimés > {self.limite}. "
                "Augmente-la avec /limite NOMBRE ou supprime-la avec /limite off."
            )
        if confirmer and tokens_estimes >= self.seuil:
            if interactif is None:
                interactif = sys.stdin.isatty() and sys.stdout.isatty()
            if interactif:
                try:
                    reponse = (demander or input)(f"Cette demande va consommer environ {tokens_estimes} tokens (estimation). Continuer ? [O/n] ")
                except (EOFError, KeyboardInterrupt):
                    reponse = "n"
                if reponse.strip().lower() in {"n", "non", "no"}:
                    return False, "Demande annulée."
        return True, ""


SESSION = SuiviCout.depuis_environnement()

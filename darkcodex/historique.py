"""Historique des modifications de fichiers : /historique, /annuler, /diff.

Avant chaque modification faite par DarkCodex, l'ancienne version du fichier est
copiée dans <projet>/.darkcodex/historique/. /annuler restaure la dernière version.
/annuler sauvegarde aussi l'état courant : deux /annuler de suite = rétablir.

"""
from __future__ import annotations

import difflib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .fsutil import atomic_write_text

DOSSIER = Path(".darkcodex") / "historique"
INDEX = "index.json"
MAX_ENTREES = 200
TAILLE_MAX = 2_000_000  # les fichiers plus gros ne sont pas sauvegardés
LIGNES_DIFF_MAX = 200


def dossier_historique(racine: Path) -> Path:
    return Path(racine) / DOSSIER


def chemin_relatif(racine: Path, chemin: Path) -> str | None:
    """Chemin relatif au projet, ou None si le fichier est hors du projet."""
    try:
        return Path(chemin).resolve().relative_to(Path(racine).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _lire_index(dossier: Path) -> list[dict]:
    try:
        data = json.loads((dossier / INDEX).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _ecrire_index(dossier: Path, entrees: list[dict]) -> None:
    atomic_write_text(dossier / INDEX, json.dumps(entrees, ensure_ascii=False, indent=1) + "\n")


def _ecrire_octets(destination: Path, contenu: bytes) -> None:
    tmp = destination.with_name(destination.name + ".tmp")
    tmp.write_bytes(contenu)
    os.replace(tmp, destination)


def enregistrer(racine: Path, chemin: Path) -> dict | None:
    """À appeler AVANT d'écrire `chemin`. Ne lève jamais d'exception (ne bloque pas l'écriture)."""
    try:
        racine, chemin = Path(racine), Path(chemin)
        rel = chemin_relatif(racine, chemin)
        if rel is None or rel.startswith(".darkcodex/"):
            return None
        existait = chemin.is_file()
        contenu = b""
        if existait:
            if chemin.stat().st_size > TAILLE_MAX:
                return None
            contenu = chemin.read_bytes()
        dossier = dossier_historique(racine)
        dossier.mkdir(parents=True, exist_ok=True)
        garde = dossier / ".gitignore"
        if not garde.exists():
            garde.write_text("*\n", encoding="utf-8")  # l'historique ne part jamais dans Git
        entree_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        sauvegarde = None
        if existait:
            sauvegarde = f"{entree_id}.bak"
            _ecrire_octets(dossier / sauvegarde, contenu)
        entrees = _lire_index(dossier)
        entree = {
            "id": entree_id,
            "chemin": rel,
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "existait": existait,
            "sauvegarde": sauvegarde,
            "annule": False,
        }
        entrees.append(entree)
        while len(entrees) > MAX_ENTREES:  # on garde les 200 dernières
            ancienne = entrees.pop(0)
            if ancienne.get("sauvegarde"):
                try:
                    (dossier / ancienne["sauvegarde"]).unlink()
                except OSError:
                    pass
        _ecrire_index(dossier, entrees)
        return entree
    except OSError:
        return None


def lister(racine: Path, limite: int = 10, chemin: str | None = None) -> list[dict]:
    """Entrées les plus récentes d'abord (filtre facultatif sur un chemin relatif)."""
    entrees = _lire_index(dossier_historique(racine))
    if chemin:
        entrees = [e for e in entrees if e.get("chemin") == chemin]
    return list(reversed(entrees))[:limite]


def prochaine_annulation(racine: Path) -> dict | None:
    for entree in lister(racine, limite=MAX_ENTREES):
        if not entree.get("annule"):
            return entree
    return None


def annuler(racine: Path) -> tuple[bool, str]:
    """Restaure la version enregistrée avant la dernière modification non annulée."""
    racine = Path(racine)
    entree = prochaine_annulation(racine)
    if entree is None:
        return False, "Rien à annuler."
    cible = racine / entree["chemin"]
    if chemin_relatif(racine, cible) is None:
        return False, "Chemin invalide dans l'historique."
    dossier = dossier_historique(racine)
    sauvegarde = dossier / entree["sauvegarde"] if entree.get("sauvegarde") else None
    if sauvegarde is not None and not sauvegarde.exists():
        return False, "La sauvegarde de cette modification a disparu."
    enregistrer(racine, cible)  # état actuel conservé : /annuler peut lui-même être annulé
    try:
        if sauvegarde is None:
            if cible.exists():
                cible.unlink()  # le fichier avait été créé par DarkCodex
        else:
            cible.parent.mkdir(parents=True, exist_ok=True)
            _ecrire_octets(cible, sauvegarde.read_bytes())
    except OSError as exc:
        return False, f"Restauration impossible : {exc}"
    entrees = _lire_index(dossier)
    for e in entrees:
        if e.get("id") == entree["id"]:
            e["annule"] = True
    _ecrire_index(dossier, entrees)
    return True, f"Restauré : {entree['chemin']} (version d'avant la modification du {formater_date(entree['date'])})"


def formater_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d/%m %H:%M")
    except ValueError:
        return iso


def formater(entrees: list[dict]) -> str:
    if not entrees:
        return "Aucune modification enregistrée."
    lignes = []
    for i, e in enumerate(entrees, 1):
        etat = "annulée" if e.get("annule") else ("créé" if not e.get("existait") else "modifié")
        lignes.append(f"{i:>2}. {formater_date(e['date'])} {etat:<8} {e['chemin']}")
    return "\n".join(lignes)


def diff_derniere(racine: Path, chemin: str | None = None) -> tuple[bool, str]:
    """Différences entre la version sauvegardée (avant) et le fichier actuel (après)."""
    racine = Path(racine)
    rel = chemin_relatif(racine, racine / chemin) if chemin else None
    if chemin and rel is None:
        return False, "Ce fichier est hors du projet."
    entrees = lister(racine, limite=1, chemin=rel)
    if not entrees:
        return False, "Aucune modification enregistrée" + (f" pour {rel}." if rel else ".")
    entree = entrees[0]
    dossier = dossier_historique(racine)
    try:
        avant = (dossier / entree["sauvegarde"]).read_text(encoding="utf-8", errors="replace") if entree.get("sauvegarde") else ""
    except OSError:
        return False, "La sauvegarde de cette modification a disparu."
    try:
        apres = (racine / entree["chemin"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        apres = ""
    lignes = list(difflib.unified_diff(
        avant.splitlines(), apres.splitlines(), fromfile=f"avant/{entree['chemin']}", tofile=f"maintenant/{entree['chemin']}", lineterm="",
    ))
    if not lignes:
        return True, "Aucune différence avec la version sauvegardée."
    if len(lignes) > LIGNES_DIFF_MAX:
        lignes = lignes[:LIGNES_DIFF_MAX] + [f"... ({len(lignes) - LIGNES_DIFF_MAX} lignes de plus)"]
    return True, "\n".join(lignes)


def colorier_diff(texte: str) -> str:
    sortie = []
    for ligne in texte.splitlines():
        if ligne.startswith("+") and not ligne.startswith("+++"):
            sortie.append(f"\033[32m{ligne}\033[0m")
        elif ligne.startswith("-") and not ligne.startswith("---"):
            sortie.append(f"\033[31m{ligne}\033[0m")
        else:
            sortie.append(ligne)
    return "\n".join(sortie)

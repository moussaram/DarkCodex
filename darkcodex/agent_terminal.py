"""Exécution de commandes système (Partie 3).

Toute commande passe par executer() : liste noire, confirmation selon le mode
d'autorisation, timeout 60 s, analyse de l'erreur par l'IA et correction limitée
à 3 tentatives.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from . import agent_fichiers

TIMEOUT_SECONDS = 60
MAX_CORRECTIONS = 3

# Commandes jamais exécutées, quelle que soit la commande (Partie 10).
INTERDITES: tuple[tuple[str, str], ...] = (
    ("rm -rf", "suppression récursive forcée"),
    ("rm -fr", "suppression récursive forcée"),
    ("rm -r /", "suppression d'un dossier racine"),
    ("mkfs", "formatage de disque"),
    ("fdisk", "partitionnement de disque"),
    ("dd if=", "écriture brute sur disque"),
    (":(){", "fork bomb"),
    ("> /dev/", "écriture sur un périphérique"),
    ("chmod -r 777", "permissions ouvertes à tous"),
    ("shutdown", "arrêt de la machine"),
    ("reboot", "redémarrage de la machine"),
    ("halt", "arrêt de la machine"),
    ("git push --force", "push forcé sur le dépôt"),
    ("git push -f", "push forcé sur le dépôt"),
    ("git reset --hard", "perte des modifications locales"),
    ("git clean -fd", "suppression des fichiers non suivis"),
    ("drop table", "suppression de table"),
    ("drop database", "suppression de base de données"),
    ("truncate table", "vidage de table"),
)


def commande_interdite(commande: str) -> str:
    """Raison du refus, chaîne vide si la commande est autorisée."""
    bas = commande.lower()
    for motif, raison in INTERDITES:
        if motif in bas:
            return raison
    return ""


def question_commande(mode: str) -> str:
    # Partie 1 : SUGGEST demande confirmation, AUTO EDIT affiche « Autoriser ? ».
    return "Autoriser ?" if mode == "autoedit" else "Exécuter cette commande ?"


# ── Détection des commandes du projet ──────────────────────────


def detecter_install(cwd: Path) -> str:
    if (cwd / "requirements.txt").is_file():
        return "python -m pip install -r requirements.txt"
    if (cwd / "package.json").is_file():
        if (cwd / "yarn.lock").is_file():
            return "yarn install"
        if (cwd / "pnpm-lock.yaml").is_file():
            return "pnpm install"
        return "npm install"
    if (cwd / "pubspec.yaml").is_file():
        return "flutter pub get"
    if (cwd / "pyproject.toml").is_file() or (cwd / "setup.py").is_file():
        return "python -m pip install ."
    return ""


def detecter_build(cwd: Path) -> str:
    scripts = _scripts_npm(cwd)
    if "build" in scripts:
        return "npm run build"
    if (cwd / "Makefile").is_file() or (cwd / "makefile").is_file():
        return "make"
    if (cwd / "pubspec.yaml").is_file():
        return "flutter build apk --debug"
    if (cwd / "requirements.txt").is_file() or (cwd / "pyproject.toml").is_file():
        return "python -m compileall ."
    return ""


def detecter_start(cwd: Path) -> str:
    scripts = _scripts_npm(cwd)
    for cle in ("dev", "start", "serve"):
        if cle in scripts:
            return f"npm run {cle}"
    if (cwd / "pubspec.yaml").is_file():
        return "flutter run"
    for candidat in ("main.py", "app.py", "run.py", "server.py"):
        if (cwd / candidat).is_file():
            return f"python {candidat}"
    if (cwd / "manage.py").is_file():
        return "python manage.py runserver"
    return ""


def detecter_lint(cwd: Path) -> str:
    texte = _lire(cwd, "requirements.txt") + _lire(cwd, "pyproject.toml")
    if "ruff" in texte.lower():
        return "python -m ruff check ."
    if "flake8" in texte.lower():
        return "python -m flake8 ."
    if "pylint" in texte.lower():
        return "python -m pylint ."
    scripts = _scripts_npm(cwd)
    if "lint" in scripts:
        return "npm run lint"
    return ""


def _scripts_npm(cwd: Path) -> dict:
    path = cwd / "package.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get("scripts") or {}


def _lire(cwd: Path, nom: str) -> str:
    path = cwd / nom
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:20000]
    except OSError:
        return ""


# ── Exécution ──────────────────────────────────────────────────


def _tuer_processus(proc: subprocess.Popen) -> None:
    """Tue la commande ET ses enfants (le shell inclut)."""
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def executer_brut(
    commande: str, cwd: Path, sur_ligne: Any = None
) -> tuple[int, str]:
    """Lance la commande, affiche stdout+stderr **en temps réel** (Partie 3).

    Retourne (code_sortie, sortie_complete). Après 60 s, la commande et ses
    enfants sont tués : les lignes déjà affichées sont conservées.
    """
    try:
        proc = subprocess.Popen(
            commande,
            shell=True,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        return 126, f"Impossible d'exécuter la commande : {exc}"

    expire = threading.Event()

    def _au_timeout() -> None:
        expire.set()
        _tuer_processus(proc)

    minuteur = threading.Timer(TIMEOUT_SECONDS, _au_timeout)
    minuteur.start()

    lignes: list[str] = []
    try:
        for ligne in proc.stdout or ():
            texte = ligne.rstrip("\n")
            lignes.append(texte)
            if sur_ligne is not None:
                try:
                    sur_ligne(texte)
                except Exception:
                    pass  # un affichage qui échoue ne doit pas tuer la commande
    finally:
        minuteur.cancel()
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()

    sortie = "\n".join(lignes).strip()
    if expire.is_set():
        message = f"⏰ Timeout : la commande a dépassé {TIMEOUT_SECONDS} secondes."
        if sur_ligne is not None:
            try:
                sur_ligne(message)
            except Exception:
                pass
        return 124, f"{message}\n{sortie}".strip()
    return proc.returncode, sortie


def executer(commande: str, ctx: Any, corriger: bool = True) -> int:
    """Confirme, exécute, affiche, puis analyse/corrige en cas d'échec."""
    raison = commande_interdite(commande)
    if raison:
        ctx.note(f"⛔ Commande refusée ({raison}) : {commande}")
        return 1

    if not ctx.confirm_command(question_commande(ctx.mode)):
        ctx.note("Commande annulée.")
        return 1

    ctx.note(f"\n\033[1m\033[93m[SYSTEM EXECUTION]\033[0m {commande}")
    # La sortie est déjà affichée ligne par ligne par executer_brut.
    code, sortie = executer_brut(commande, ctx.cwd, sur_ligne=ctx.note)
    ctx.note(f"exit code: {code}")
    _journalise(ctx, commande, code, sortie)

    if code == 0 or not corriger:
        return code
    return corriger_et_relancer(commande, sortie, code, ctx)


def _journalise(ctx: Any, commande: str, code: int, sortie: str) -> None:
    noteur = getattr(ctx, "note_commande", None)
    if callable(noteur):
        noteur(commande, code, sortie)


def analyser_erreur(commande: str, sortie: str, ctx: Any) -> dict[str, Any]:
    """Envoie l'erreur à l'IA : cause + correction proposée."""
    prompt = (
        "Une commande shell a échoué dans un projet. Analyse la cause et propose une correction.\n"
        "Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour :\n"
        '{"analyse": "cause en 2 phrases", "commande_corrigee": "commande corrigée ou null"}\n\n'
        f"Commande : {commande}\n"
        f"Sortie :\n{sortie[:4000]}"
    )
    code, texte = ctx.ask(prompt)
    if code != 0:
        return {"analyse": texte, "commande_corrigee": None}
    donnees = agent_fichiers.extraire_json(texte)
    if isinstance(donnees, dict):
        return {
            "analyse": str(donnees.get("analyse") or texte),
            "commande_corrigee": donnees.get("commande_corrigee"),
        }
    return {"analyse": texte.strip(), "commande_corrigee": None}


def corriger_et_relancer(commande: str, sortie: str, code: int, ctx: Any) -> int:
    """Boucle de correction : analyse → commande corrigée → relance (max 3)."""
    actuelle, code_actuel = commande, code
    for tentative in range(1, MAX_CORRECTIONS + 1):
        ctx.note(f"\n{agent_fichiers.JAUNE}🔧 Tentative de correction {tentative}/{MAX_CORRECTIONS}{agent_fichiers.FIN_COULEUR}")
        infos = analyser_erreur(actuelle, sortie, ctx)
        ctx.note(f"🔎 {infos['analyse']}")
        corrigee = str(infos.get("commande_corrigee") or "").strip()
        if not corrigee or corrigee == actuelle:
            ctx.note(f"{agent_fichiers.ROUGE}Aucune correction de commande possible. Arrêt.{agent_fichiers.FIN_COULEUR}")
            return code_actuel
        raison = commande_interdite(corrigee)
        if raison:
            ctx.note(f"⛔ Correction refusée ({raison}) : {corrigee}")
            return code_actuel
        if not ctx.confirm_command(f"Relancer avec la commande corrigée ?\n   {corrigee}"):
            ctx.note("Correction refusée.")
            return code_actuel
        actuelle = corrigee
        ctx.note(f"\n\033[1m\033[93m[SYSTEM EXECUTION]\033[0m {actuelle}")
        code_actuel, sortie = executer_brut(actuelle, ctx.cwd, sur_ligne=ctx.note)
        ctx.note(f"exit code: {code_actuel}")
        _journalise(ctx, actuelle, code_actuel, sortie)
        if code_actuel == 0:
            ctx.note(f"{agent_fichiers.VERT}✅ Commande corrigée et exécutée avec succès.{agent_fichiers.FIN_COULEUR}")
            return 0
    ctx.note(f"{agent_fichiers.ROUGE}Échec après {MAX_CORRECTIONS} tentatives.{agent_fichiers.FIN_COULEUR}")
    return code_actuel


def lancer(action: str, ctx: Any) -> int:
    """Commandes intelligentes : /install /build /start /test /lint."""
    detecteurs = {
        "install": (detecter_install, "Aucun gestionnaire de dépendances détecté."),
        "build": (detecter_build, "Aucune commande de build détectée."),
        "start": (detecter_start, "Aucune commande de démarrage détectée."),
        "lint": (detecter_lint, "Aucun linter détecté."),
        "test": (None, "Aucun framework de test détecté."),
    }
    if action == "test":
        from . import agent_tests  # import tardif : évite une dépendance circulaire

        return agent_tests.lancer(ctx)
    fonction, message = detecteurs.get(action, (None, "Action inconnue."))
    commande = fonction(ctx.cwd) if fonction else ""
    if not commande:
        ctx.note(message)
        return 1
    return executer(commande, ctx)


# ── Séquence automatique /auto ─────────────────────────────────


def auto(description: str, ctx: Any) -> int:
    """Analyser → planifier → modifier → exécuter → observer → corriger → tester."""
    from . import agent_tests  # import tardif

    ctx.note(f"\033[95m🤖 Séquence automatique : {description}\033[0m")

    # 1. Analyse + plan
    ok, prompt_analyse = agent_fichiers.project_prompt(ctx.cwd, limit=120)
    contexte = prompt_analyse if ok else f"Projet : {ctx.cwd}"
    plan = (
        f"{contexte}\n\n"
        f"Tâche à réaliser : {description}\n\n"
        "Donne un plan court (5 étapes maximum), puis réponds sur la dernière ligne par "
        'un JSON valide de la forme {"fichiers": [{"chemin": "...", "contenu": "code complet"}], '
        '"commandes": ["..."]}. Les contenus doivent être complets et prêts à écrire. '
        "Si aucun fichier n'a besoin d'être modifié, mets un tableau vide."
    )
    code, texte = ctx.ask(plan)
    if code != 0:
        ctx.note(f"\033[91m{texte}\033[0m")
        return code
    ctx.note(texte)

    donnees = agent_fichiers.extraire_json(texte)
    if not isinstance(donnees, dict):
        ctx.note("Plan non structuré : aucune modification appliquée automatiquement.")
        return 0

    # 2. Modifications de fichiers (avec sauvegarde + diff + confirmation)
    appliques = 0
    for item in (donnees.get("fichiers") or [])[:10]:
        if not isinstance(item, dict):
            continue
        cible = agent_fichiers.resolve_path(ctx.cwd, str(item.get("chemin") or ""))
        contenu_nouveau = str(item.get("contenu") or "")
        if not contenu_nouveau:
            continue
        action = "creation" if not cible.exists() else "modification"
        if agent_fichiers.appliquer_fichier(cible, contenu_nouveau, ctx, action=action):
            appliques += 1

    ctx.note(f"\n{appliques} fichier(s) modifié(s).")

    # 3. Tests + 4. correction (max 3)
    commandes = [str(c) for c in (donnees.get("commandes") or [])[:5] if str(c).strip()]
    for commande in commandes:
        executer(commande, ctx, corriger=False)

    if agent_tests.detecter_test(ctx.cwd):
        ctx.note("\n\033[96m🧪 Lancement des tests...\033[0m")
        for tentative in range(1, agent_tests.MAX_TENTATIVES + 1):
            code_test, sortie = agent_tests.executer_tests(ctx)
            ctx.note(sortie)
            if code_test == 0:
                ctx.note(f"{agent_fichiers.VERT}✅ Tests passés.{agent_fichiers.FIN_COULEUR}")
                return 0
            ctx.note(f"{agent_fichiers.ROUGE}❌ Tests en échec (tentative {tentative}/{agent_tests.MAX_TENTATIVES}).{agent_fichiers.FIN_COULEUR}")
            if tentative == agent_tests.MAX_TENTATIVES or not _corriger_fichiers(ctx, description, sortie):
                break
    return 0


def _corriger_fichiers(ctx: Any, description: str, sortie_tests: str) -> bool:
    """Demande à l'IA des fichiers corrigés après un échec de test, puis applique."""
    instruction = (
        "Les tests du projet échouent après cette tâche : "
        f"{description}\n\nSortie des tests :\n{sortie_tests[:4000]}\n\n"
        "Corrige les fichiers fautifs."
    )
    return agent_fichiers.corriger_avec_ia(
        ctx, instruction, agent_fichiers.extraits_fichiers(ctx.cwd)
    )
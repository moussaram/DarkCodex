"""Intégration Git (Partie 5) : /git status|diff|log|branches|explain|resume|commit|push|pull|branch"""

from __future__ import annotations

import subprocess
from typing import Any

TIMEOUT_SECONDS = 60

# Fichiers d'artefacts jamais stagés : git respecte déjà .gitignore, ces
# exclusions couvrent le cas d'un projet qui n'en a pas encore.
EXCLUS_ADD = (
    ":(exclude,glob)**/__pycache__/",
    ":(exclude,glob)**/*.pyc",
    ":(exclude,glob)**/node_modules/",
    ":(exclude,glob)**/.venv/",
    ":(exclude,glob)**/venv/",
)

# Jamais exécutées sans confirmation explicite (Partie 5 et 10).
INTERDITES_TOKENS = {
    "--force": "push forcé / réécriture d'historique",
    "-f": "opération destructrice",
    "--mirror": "réécriture du dépôt distant",
    "--hard": "perte des modifications locales",
}
INTERDITES_SEQUENCES = (
    ("reset --hard", "perte des modifications locales"),
    ("clean -f", "suppression de fichiers non suivis"),
    ("push --force", "push forcé"),
)


def _refuse(args: list[str]) -> str:
    for arg in args:
        if arg in INTERDITES_TOKENS:
            return INTERDITES_TOKENS[arg]
    joined = " ".join(args)
    for motif, raison in INTERDITES_SEQUENCES:
        if motif in joined:
            return raison
    return ""


def _git(ctx: Any, *args: str) -> tuple[int, str]:
    commande = ["git", *args]
    raison = _refuse(list(args))
    if raison:
        return 1, f"⛔ Opération refusée ({raison}) : git {' '.join(args)}"
    try:
        proc = subprocess.run(
            commande,
            cwd=str(ctx.cwd),
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return 124, f"⏰ Timeout git ({TIMEOUT_SECONDS}s)."
    except OSError as exc:
        return 126, f"Git indisponible : {exc}"
    sortie = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if "not a git repository" in sortie.lower():
        # Ne pas recracher les centaines de lignes d'aide de git.
        return 128, (
            "Ce dossier n'est pas un dépôt Git. "
            "Tape `git init` dans un terminal pour en créer un, puis relance ta commande."
        )
    return proc.returncode, sortie


def _demander(ctx: Any, question: str) -> bool:
    return ctx.confirm_always(question)


def executer(args: list[str], ctx: Any) -> int:
    if not args:
        ctx.note(usage())
        return 1
    action = args[0].lower()

    if action in {"status", "diff", "log", "branches"}:
        return _inspecter(action, ctx)
    if action == "explain":
        if len(args) < 2:
            ctx.note("Usage: /git explain <hash>")
            return 1
        return _expliquer(args[1], ctx)
    if action == "resume":
        return _resume(ctx)
    if action == "commit":
        message = " ".join(args[1:]).strip()
        return _commit(message, ctx)
    if action == "push":
        return _push(ctx)
    if action == "pull":
        return _pull(ctx)
    if action == "branch":
        if len(args) < 2:
            ctx.note("Usage: /git branch <nom>")
            return 1
        return _nouvelle_branche(args[1], ctx)

    ctx.note(usage())
    return 1


def usage() -> str:
    return (
        "Commandes git disponibles :\n"
        "  /git status | /git diff | /git log | /git branches\n"
        "  /git explain <hash> | /git resume\n"
        "  /git commit [message] | /git push | /git pull | /git branch <nom>"
    )


# ── Inspection ─────────────────────────────────────────────────


def _inspecter(action: str, ctx: Any) -> int:
    commandes = {
        "status": ("status", "Explique cet état du dépôt Git de façon claire et actionnable."),
        "diff": ("diff --no-color", "Résume ces modifications non commitées : ce qui change et pourquoi."),
        "log": ("log --oneline -15", "Résume ces derniers commits et l'évolution du projet."),
        "branches": ("branch -a", "Explique l'organisation des branches de ce dépôt."),
    }
    args, instruction = commandes[action]
    code, sortie = _git(ctx, *args.split())
    if code != 0:
        ctx.note(sortie or f"git {args} a échoué.")
        return code
    if not sortie:
        ctx.note("(vide)")
        return 0

    ctx.note(sortie)
    code_ia, texte = ctx.ask(f"{instruction}\n\n```\n{sortie[:6000]}\n```")
    if code_ia != 0:
        ctx.note(texte)
        return code_ia
    ctx.note(f"\n\033[95m💡 {texte}\033[0m")
    return 0


def _expliquer(commit_hash: str, ctx: Any) -> int:
    code, sortie = _git(ctx, "show", "--no-color", "--stat", commit_hash)
    if code != 0:
        ctx.note(sortie)
        return code
    code_ia, texte = ctx.ask(
        "Explique ce commit : objectité, fichiers touchés, impact sur le projet.\n\n"
        f"```\n{sortie[:8000]}\n```"
    )
    if code_ia != 0:
        ctx.note(texte)
        return code_ia
    ctx.note(f"\033[95m💡 {texte}\033[0m")
    return 0


def _resume(ctx: Any) -> int:
    code, diff = _git(ctx, "diff", "--no-color", "HEAD")
    if code != 0:
        ctx.note(diff)
        return code
    if not diff.strip():
        ctx.note("Aucune modification depuis le dernier commit.")
        return 0
    code_ia, texte = ctx.ask(
        "Résume ces changements : ce qui est modifié, ajouté ou supprimé, et l'impact "
        "sur le projet. Signale tout risque.\n\n"
        f"```diff\n{diff[:8000]}\n```"
    )
    if code_ia != 0:
        ctx.note(texte)
        return code_ia
    ctx.note(f"\033[95m💡 {texte}\033[0m")
    return 0


# ── Écriture ───────────────────────────────────────────────────


def _message_propose(ctx: Any) -> str:
    code, statut = _git(ctx, "status", "--short")
    code2, stat = _git(ctx, "diff", "--stat", "HEAD")
    contexte = f"{statut}\n{stat}".strip()
    if code != 0 and code2 != 0:
        return ""
    code_ia, texte = ctx.ask(
        "Propose un message de commit court (impératif, 1 ligne, en français) pour ces changements. "
        "Réponds uniquement par le message.\n\n"
        f"```\n{contexte[:3000]}\n```"
    )
    if code_ia != 0:
        return ""
    return texte.strip().splitlines()[0].strip() if texte.strip() else ""


def _commit(message: str, ctx: Any) -> int:
    code, statut = _git(ctx, "status", "--short")
    if code != 0:
        ctx.note(statut)
        return code
    if not statut.strip():
        ctx.note("Rien à commiter (working tree propre).")
        return 0

    ctx.note(statut)
    if not message:
        message = _message_propose(ctx)
        if not message:
            ctx.note("Impossible de proposer un message de commit.")
            return 1
        ctx.note(f"📝 Message proposé : {message}")

    if ctx.mode == "suggest":
        code_diff, diff = _git(ctx, "diff", "--no-color")
        if code_diff == 0 and diff.strip():
            ctx.note(diff[:6000])

    if ctx.mode == "fullauto":
        # Partie 1 : FULL AUTO n'applique aucune confirmation (le push, lui,
        # reste toujours confirmé, cf. _push et Partie 10).
        ctx.note(f"\U0001f4e4 Commit en cours : {message}")
    elif not _demander(ctx, "Effectuer ce commit ?"):
        ctx.note("Commit annulé.")
        return 1

    code_add, sortie_add = _git(ctx, "add", "-A", "--", *EXCLUS_ADD)
    if code_add != 0:
        ctx.note(sortie_add)
        return code_add
    code_commit, sortie_commit = _git(ctx, "commit", "-m", message)
    ctx.note(sortie_commit)
    if code_commit == 0:
        ctx.note(f"\033[92m✅ Commit créé : {message}\033[0m")
    return code_commit


def _push(ctx: Any) -> int:
    # Partie 5 : confirmation obligatoire, même en FULL AUTO.
    if not _demander(ctx, "Pousser les commits vers le dépôt distant ?"):
        ctx.note("Push annulé.")
        return 1
    code, sortie = _git(ctx, "push")
    ctx.note(sortie)
    if code == 0:
        ctx.note("\033[92m✅ Push effectué.\033[0m")
    return code


def _pull(ctx: Any) -> int:
    if not ctx.confirm_command("Récupérer les dernières modifications du dépôt distant ?"):
        ctx.note("Pull annulé.")
        return 1
    code, sortie = _git(ctx, "pull", "--ff-only")
    ctx.note(sortie)
    if code == 0:
        ctx.note("\033[92m✅ Dépôt à jour.\033[0m")
    return code


def _nouvelle_branche(nom: str, ctx: Any) -> int:
    if not ctx.confirm_command(f"Créer la branche « {nom} » ?"):
        ctx.note("Branche annulée.")
        return 1
    code, sortie = _git(ctx, "checkout", "-b", nom)
    ctx.note(sortie)
    return code
"""Débogage automatique (Partie 4) : /debug, /fix, /review."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import agent_fichiers, agent_tests

MAX_TENTATIVES = 3


def analyser(description: str, ctx: Any) -> int:
    """/debug [erreur] : identifie fichier, ligne, cause et correction proposée."""
    ctx.note("\033[96m🔍 Analyse de l'erreur...\033[0m")
    prompt = (
        "Un utilisateur rencontre cette erreur dans son projet. Identifie le fichier concerné, "
        "la ligne probable, la cause et la correction.\n\n"
        "Réponds UNIQUEMENT avec un objet JSON valide :\n"
        '{"fichier": "chemin relatif ou null", "ligne": 0, "cause": "...", "correction": "..."}\n\n'
        f"Erreur :\n{description}"
    )
    code, texte = ctx.ask(prompt)
    if code != 0:
        ctx.note(texte)
        return code

    donnees = agent_fichiers.extraire_json(texte)
    if isinstance(donnees, dict) and donnees.get("cause"):
        fichier = str(donnees.get("fichier") or "inconnu")
        ligne = donnees.get("ligne") or "?"
        ctx.note(f"🐛 Bug trouvé dans {fichier} (ligne {ligne}) : {donnees['cause']}")
        ctx.note(f"✏️ Correction proposée : {donnees.get('correction') or 'aucune'}")
        if fichier and fichier != "inconnu":
            ctx.note("\033[93mTapez /fix <fichier> pour appliquer la correction.\033[0m")
        return 0

    ctx.note(texte)
    return 0


def fixer(cible: Path, ctx: Any) -> int:
    """/fix [fichier] : analyse → corrige → teste → recommence (max 3) → rapport."""
    ok, contenu = agent_fichiers.read_text_file(cible)
    if not ok:
        ctx.note(contenu)
        return 1

    commande_tests = agent_tests.detecter_test(ctx.cwd)
    derniere_sortie = ""
    succes = False

    for tentative in range(1, MAX_TENTATIVES + 1):
        ctx.note(f"\n\033[96m🔍 Analyse du fichier... ({tentative}/{MAX_TENTATIVES})\033[0m")
        code, texte = ctx.ask(analyse_prompt(cible, contenu))
        if code != 0:
            ctx.note(texte)
            return code
        ctx.note(bugs_lignes(texte) or texte)

        instruction = (
            "Corrige les bugs de ce fichier identifiés ci-dessous. Réponds UNIQUEMENT avec le JSON "
            "des fichiers corrigés.\n\n"
            f"Analyse :\n{texte[:3000]}\n\nFichier :\n{contenu[:12000]}"
        )
        ctx.note("\033[96m✏️ Correction appliquée...\033[0m")
        if not agent_fichiers.corriger_avec_ia(ctx, instruction, "", max_fichiers=1):
            ctx.note("\033[93mAucune correction applicable pour l'instant.\033[0m")
            return 1

        ok, contenu = agent_fichiers.read_text_file(cible)
        if not ok:
            ctx.note(contenu)
            return 1

        if not commande_tests:
            ctx.note("\033[93m🧪 Aucun test détecté dans le projet.\033[0m")
            succes = True
            break

        ctx.note("\033[96m🧪 Lancement des tests...\033[0m")
        code_test, derniere_sortie = agent_tests.executer_tests(ctx)
        if derniere_sortie:
            ctx.note(derniere_sortie[:4000])
        if code_test == 0:
            succes = True
            break
        ctx.note("\033[91m❌ Le bug est toujours présent.\033[0m")

    if succes:
        ctx.note("\033[92m✅ Tests passés ! Bug corrigé avec succès.\033[0m")
        ctx.note(f"Rapport : {cible.name} corrigé en {tentative} tentative(s).")
        return 0

    ctx.note(f"\033[91m⛔ Rapport final : correction non validée après {MAX_TENTATIVES} tentatives.\033[0m")
    if derniere_sortie:
        ctx.note(derniere_sortie[:4000])
    return 1


def analyse_prompt(path: Path, contenu: str) -> str:
    language = agent_fichiers.detect_language(path, contenu)
    return (
        f"Analyse ce fichier {language} à la recherche de bugs (variables non définies, erreurs de "
        "logique, erreurs de type, cas non gérés).\n"
        "Réponds UNIQUEMENT avec un tableau JSON [{\"ligne\": 42, \"description\": \"...\"}] "
        "trié par ligne. Tableau vide si aucun bug.\n\n"
        f"Fichier : {path.name}\n\n{contenu}"
    )


def bugs_lignes(texte: str) -> str:
    """Formate la réponse JSON [{ligne, description}] en lignes lisibles."""
    donnees = agent_fichiers.extraire_json(texte)
    if not isinstance(donnees, list) or not donnees:
        return ""
    lignes = []
    for item in donnees[:20]:
        if isinstance(item, dict) and item.get("description"):
            lignes.append(f"🐛 Bug trouvé ligne {item.get('ligne', '?')} : {item['description']}")
    return "\n".join(lignes)


def review(cible: Path, ctx: Any) -> int:
    """/review [fichier] : rapport structuré avec score et recommandations."""
    ok, contenu = agent_fichiers.read_text_file(cible)
    if not ok:
        ctx.note(contenu)
        return 1

    ctx.note(f"\033[96m🧐 Revue de code de {cible.name}...\033[0m")
    prompt = (
        "Fais une revue de code complète : bugs potentiels, sécurité, performance, lisibilité, "
        "bonnes pratiques.\n"
        "Réponds avec ce format exact :\n"
        "SCORE: <note sur 10>\n"
        "## Bugs potentiels\n...\n## Sécurité\n...\n## Performance\n...\n"
        "## Lisibilité\n...\n## Recommandations\n1. ...\n\n"
        f"Fichier : {cible.name} ({agent_fichiers.detect_language(cible, contenu)})\n\n{contenu}"
    )
    code, texte = ctx.ask(prompt)
    if code != 0:
        ctx.note(texte)
        return code

    score = next((l.strip() for l in texte.splitlines() if l.strip().upper().startswith("SCORE")), "")
    if score:
        ctx.note(f"\033[1m{score}\033[0m")
    ctx.note(texte)
    return 0
"""Tests automatiques (Partie 6, correction C10) : génération, exécution, analyse.

/tests run ne s'arrête plus sur une erreur Python, distingue « 0 test », « tests
en échec » et « faible couverture », et ne déclenche jamais d'analyse IA sur un
crash d'import ou de syntaxe.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from . import agent_fichiers, agent_terminal

MAX_TENTATIVES = 3


# ── Détection ──────────────────────────────────────────────────

def _lire(cwd: Path, nom: str, taille: int = 20000) -> str:
    path = cwd / nom
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:taille]
    except OSError:
        return ""


def _scripts_npm(cwd: Path) -> dict:
    texte = _lire(cwd, "package.json")
    if not texte:
        return {}
    try:
        data = json.loads(texte)
    except json.JSONDecodeError:
        return {}
    return data.get("scripts") or {}


def _npm_deps(cwd: Path) -> dict:
    texte = _lire(cwd, "package.json")
    if not texte:
        return {}
    try:
        data = json.loads(texte)
    except json.JSONDecodeError:
        return {}
    return {**data.get("dependencies", {}), **data.get("devDependencies", {})}


def framework(cwd: Path) -> str:
    """pytest | unittest | jest | npm | flutter | cargo | go | ''"""
    try:
        if (cwd / "pubspec.yaml").is_file():
            return "flutter"

        deps = _npm_deps(cwd)
        scripts = _scripts_npm(cwd)
        if "jest" in deps or "@types/jest" in deps or "jest" in scripts.get("test", ""):
            return "jest"
        if scripts.get("test"):
            return "npm"

        marqueurs = (
            "pytest" in _lire(cwd, "requirements.txt").lower()
            or "pytest" in _lire(cwd, "pyproject.toml").lower()
            or (cwd / "pytest.ini").is_file()
            or (cwd / "tox.ini").is_file()
            or (cwd / "conftest.py").is_file()
        )
        a_tests = (cwd / "tests").is_dir() or any(path.is_file() for path in cwd.glob("test_*.py"))
        if marqueurs:
            return "pytest"
        if a_tests:
            return "unittest"
        if (cwd / "Cargo.toml").is_file():
            return "cargo"
        if list(cwd.glob("*_test.go")) or (cwd / "go.mod").is_file():
            return "go"
    except OSError:
        return ""
    return ""


def detecter_test(cwd: Path) -> str | None:
    """C10 : commande qui lance les tests. Ne lève jamais d'exception.

    Préfère python3 quand il existe, retombe proprement sur python.
    """
    try:
        fw = framework(cwd)
        if fw == "flutter":
            return "flutter test"
        if fw in {"jest", "npm"}:
            return "npm test"
        if fw == "cargo":
            return "cargo test"
        if fw == "go":
            return "go test ./..."
        prefixe = "python3" if shutil.which("python3") else "python"
        if fw == "pytest":
            if (cwd / "tests").is_dir():
                return f"{prefixe} -m pytest tests/ -v"
            return f"{prefixe} -m pytest -v"
        if fw == "unittest":
            if (cwd / "tests").is_dir():
                return f"{prefixe} -m unittest discover -s tests -v"
            # Pas de dossier tests/ : on découvre depuis la racine, sinon unittest
            # s'arrête sur « Start directory is not importable ».
            return f"{prefixe} -m unittest discover -v"
    except OSError:
        return None
    return None


# ── Exécution ──────────────────────────────────────────────────

def executer_tests(ctx: Any) -> tuple[int, str]:
    """Lance les tests sans confirmation (utilisé dans les boucles de correction)."""
    commande = detecter_test(ctx.cwd)
    if not commande:
        return 1, "Aucun framework de test détecté."
    return agent_terminal.executer_brut(
        commande, ctx.cwd, sur_ligne=getattr(ctx, "note", None)
    )


def resume_tests(sortie: str) -> str:
    """Résumé 'X tests passés, Y échoués' à partir de la sortie pytest/unittest/jest."""
    reussis = echoues = 0
    trouve = False

    for motif, valeur in (
        (r"(\d+) passed", "ok"),
        (r"(\d+) succeeded", "ok"),
        (r"(\d+) failed", "ko"),
        (r"(\d+) errors", "ko"),
        (r"(\d+) failure", "ko"),
    ):
        match = re.search(motif, sortie)
        if match:
            trouve = True
            if valeur == "ok":
                reussis += int(match.group(1))
            else:
                echoues += int(match.group(1))

    match = re.search(r"Ran (\d+) tests?", sortie)
    if match:
        trouve = True
        total = int(match.group(1))
        if not reussis and not echoues:
            reussis = 0 if "FAILED" in sortie else total
            echoues = 0 if "FAILED" not in sortie else total - reussis
        elif not reussis:
            reussis = max(0, total - echoues)

    if not trouve:
        return ""
    return f"📊 {reussis} test(s) réussi(s), {echoues} échoué(s)"


def _detaille_tests(sortie: str) -> str:
    """✅/❌ par test quand la sortie est verbeuse (pytest -v, unittest -v)."""
    lignes_out = []
    for ligne in sortie.splitlines():
        if "PASSED" in ligne or " ok" == ligne[-3:]:
            lignes_out.append(f"  ✅ {ligne.strip()}")
        elif "FAILED" in ligne or "ERROR" in ligne or "FAIL:" in ligne:
            lignes_out.append(f"  ❌ {ligne.strip()}")
    return "\n".join(lignes_out[:60])


def _conseils_ia(ctx: Any, bas: str, detaille: str) -> None:
    """C10 : conseils IA enrichis du contexte projet (AGENTS.md, mémoire, structure)."""
    contexte_projet = ""
    try:
        info = agent_fichiers.scan_project(ctx.cwd)
        contexte_projet = f"\nContexte du projet :\n{agent_fichiers.resume_projet(info)[:1200]}"
    except Exception:
        contexte_projet = ""

    prompt = (
        "Les tests de ce projet ont échoué. Analyse la sortie et donne des conseils concrets.\n\n"
        "Sortie complète :\n" + bas[:3000] + "\n\n"
        "Détail des échecs :\n" + (detaille[:2000] if detaille else "non disponible") + "\n"
        + contexte_projet +
        "\n\nDonne :\n"
        "1. La cause probable de chaque échec\n"
        "2. Des conseils précis pour corriger\n"
        "3. Des suggestions d'amélioration de la couverture de tests\n"
        "Réponds en texte simple, sans bloc de code."
    )
    code, texte = ctx.ask(prompt)
    if code != 0:
        ctx.note(texte)
        return
    ctx.note("\n\033[95m📋 Conseils IA :\033[0m\n" + texte)


def _verifier_resultats(sortie: str) -> str | None:
    """C10 : verdict lisible. None = tout est OK.

    Retourne « error_launch », « no_tests », « low_coverage:NN » ou « failures ».
    """
    if not sortie.strip():
        return "empty"
    if re.search(r"SyntaxError|IndentationError|ModuleNotFoundError|ImportError", sortie):
        return "error_launch"
    if re.search(r"NO TESTS RAN|Ran 0 tests", sortie, re.IGNORECASE):
        return "no_tests"
    # Dernier chiffre de couverture rapporté (pytest écrit la cible puis le réel).
    couvertures = re.findall(
        r"(?i)(?:coverage|couverture)[^\d]{0,10}(\d+(?:\.\d+)?)%", sortie
    ) or re.findall(r"'(\d+(?:\.\d+)?)%'", sortie)
    if couvertures:
        pourcentage = int(float(couvertures[-1]))
        if pourcentage < 80:
            return f"low_coverage:{pourcentage}"
    if re.search(r"FAILED|ERROR at|AssertionError|✗|FAIL ", sortie):
        return "failures"
    return None


def lancer(ctx: Any) -> int:
    """C10 /tests run : détecte, confirme, exécute, affiche ✅/❌, résumé, verdict."""
    commande = detecter_test(ctx.cwd)
    if not commande:
        ctx.note("Aucun framework de test détecté.")
        return 1
    if not ctx.confirm_command(f"Lancer les tests avec « {commande} » ?"):
        ctx.note("Tests annulés.")
        return 1

    for tentative in range(1, MAX_TENTATIVES + 1):
        ctx.note(f"\n\033[96m🧪 {commande}\033[0m")
        code, sortie = executer_tests(ctx)
        # La sortie est déjà affichée en temps réel (sur_ligne) ; on ne la
        # réimprime pas, on ajoute seulement ✅/❌, résumé et verdict.
        detail = _detaille_tests(sortie)
        if detail:
            ctx.note(detail)
        resume = resume_tests(sortie)
        if resume:
            ctx.note(resume)

        verdict = _verifier_resultats(sortie)

        if verdict in {"no_tests", "empty"}:
            # Jamais de « 0 test réussi » : soit rien n'a été exécuté, soit la
            # sortie est vide. Pas d'analyse IA sur ce cas.
            ctx.note(
                "\033[93m⚠️ Aucun test n'a été exécuté. Vérifie que des tests existent "
                "et sont détectés (pytest recommandé : pip install pytest), puis "
                "relance /tests run.\033[0m"
            )
            return 1

        if verdict == "error_launch":
            # Erreur d'import/compilation du lancement : on ne déclenche pas
            # d'analyse IA. On affiche l'erreur telle quelle.
            ctx.note(
                "\033[91m❌ Erreur d'import ou de compilation — corrige d'abord le code, "
                "puis relance /tests run.\033[0m"
            )
            return code if code != 0 else 1

        if code == 0:
            ctx.note("\033[92m✅ Tests passés !\033[0m")
            if verdict and verdict.startswith("low_coverage"):
                pourcentage = verdict.split(":", 1)[1]
                ctx.note(f"\033[93m📉 Couverture : {pourcentage}% (recommandé : 80% minimum).\033[0m")
                _conseils_ia(ctx, sortie, detail)
            return 0

        ctx.note(f"\033[91m❌ Tests en échec (code {code}).\033[0m")
        if not analyser_echecs(sortie, ctx):
            return code
        if tentative == MAX_TENTATIVES:
            break
        ctx.note(f"\033[93m🔄 Relance des tests ({tentative + 1}/{MAX_TENTATIVES})...\033[0m")

    ctx.note(f"\033[91mÉchec après {MAX_TENTATIVES} tentatives.\033[0m")
    return 1


def analyser_echecs(sortie: str, ctx: Any) -> bool:
    """Analyse la cause des échecs ; corrige et relance en autoedit / fullauto."""
    prompt = (
        "Des tests ont échoué dans ce projet. Identifie la cause pour chaque échec et dis si le "
        "problème vient du code ou du test.\n\n"
        f"Sortie des tests :\n{sortie[:6000]}\n\n"
        "Réponds avec un rapport court : cause, fichier fautif, correction recommandée."
    )
    code, texte = ctx.ask(prompt)
    if code != 0:
        ctx.note(texte)
        return False
    ctx.note(f"\n\033[95m📋 Analyse des échecs\033[0m\n{texte}")

    if ctx.mode not in {"autoedit", "fullauto"}:
        ctx.note("\033[93mIndique /fix <fichier> pour appliquer la correction.\033[0m")
        return False

    instruction = (
        "Les tests échouent. Applique la correction dans le code source (pas dans les tests "
        "sauf si le test est faux).\n\n"
        f"Analyse : {texte}\n\nSortie des tests :\n{sortie[:4000]}"
    )
    applique = agent_fichiers.corriger_avec_ia(
        ctx, instruction, agent_fichiers.extraits_fichiers(ctx.cwd)
    )
    if applique:
        ctx.note("\033[92m✏️ Correction appliquée.\033[0m")
    return applique


# ── Génération de tests ────────────────────────────────────────

def chemin_de_test(cwd: Path, cible: Path, fw: str) -> Path:
    stem = cible.stem
    if fw in {"pytest", "unittest"}:
        return cwd / "tests" / f"test_{stem}.py"
    if fw in {"jest", "npm"}:
        return cwd / "tests" / f"{stem}.test.js"
    if fw == "flutter":
        return cwd / "test" / f"{stem}_test.dart"
    return cwd / "tests" / f"test_{stem}.py"


def prompt_tests(path: Path, contenu: str, fw: str) -> str:
    langage = agent_fichiers.detect_language(path, contenu)
    style = {
        "pytest": "pytest",
        "unittest": "unittest (Python)",
        "jest": "jest (JavaScript)",
        "npm": "jest (JavaScript)",
        "flutter": "flutter_test (Dart)",
    }.get(fw, "pytest")
    return (
        f"Génère des tests unitaires {style} pour ce fichier {langage}.\n"
        "Couvre : cas nominaux, cas limites, cas d'erreur. Les tests doivent être exécutables tels quels.\n"
        "Réponds UNIQUEMENT avec le contenu complet du fichier de test, sans Markdown ni explication.\n\n"
        f"Fichier testé : {path.name}\n\nContenu :\n{contenu}"
    )


def ecrire(ctx: Any, cible: Path) -> int:
    """/tests ecrire [fichier] : génère les tests dans le dossier tests/ du projet."""
    fw = framework(ctx.cwd) or "pytest"
    ok, contenu = agent_fichiers.read_text_file(cible)
    if not ok:
        ctx.note(contenu)
        return 1

    ctx.note(f"\033[96m🔍 Analyse de {cible.name}...\033[0m")
    code, tests = ctx.ask(prompt_tests(cible, contenu, fw))
    if code != 0:
        ctx.note(tests)
        return code

    destination = chemin_de_test(ctx.cwd, cible, fw)
    if not agent_fichiers.appliquer_fichier(destination, tests, ctx, action="test_genere"):
        return 1
    ctx.note(f"\033[92m✅ Tests générés : {destination.relative_to(ctx.cwd)}\033[0m")
    return 0

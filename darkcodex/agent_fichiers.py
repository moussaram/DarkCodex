import difflib
import json
import os
import shutil
from pathlib import Path

from . import historique


TEXT_EXTENSIONS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".html": "HTML",
    ".css": "CSS",
    ".json": "JSON",
    ".md": "Markdown",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
}


def resolve_path(cwd: Path, value: str) -> Path:
    path = Path(value.strip().strip('"'))
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def detect_language(path: Path, content: str = "") -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return TEXT_EXTENSIONS[suffix]
    first = content[:200].lower()
    if "def " in first or "import " in first:
        return "Python"
    if "function " in first or "const " in first:
        return "JavaScript"
    return "Texte"


def read_text_file(path: Path, max_size: int = 160_000) -> tuple[bool, str]:
    if not path.exists():
        return False, f"Fichier introuvable: {path}"
    if not path.is_file():
        return False, f"Ce chemin n'est pas un fichier: {path}"
    if path.stat().st_size > max_size:
        return False, f"Fichier trop volumineux pour analyse directe: {path}"
    try:
        return True, path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"Fichier illisible: {exc}"


def analysis_prompt(path: Path, content: str, mode: str) -> str:
    language = detect_language(path, content)
    instructions = {
        "lire": "Analyse ce fichier et propose des améliorations concrètes.",
        "expliquer": "Explique en détail ce que fait ce fichier.",
        "corriger": "Corrige les bugs et erreurs. Réponds uniquement avec le contenu complet corrigé du fichier, sans Markdown.",
    }
    return (
        f"Fichier: {path.name}\n"
        f"Langage détecté: {language}\n"
        f"Instruction: {instructions[mode]}\n\n"
        f"Contenu:\n{content}"
    )


def generate_prompt(path: Path, description: str) -> str:
    language = detect_language(path)
    return (
        f"Génère le contenu complet du fichier {path.name} en {language} selon cette description.\n"
        f"Réponds uniquement avec le contenu du fichier, sans Markdown.\n\n"
        f"Description: {description}"
    )


def summary_prompt(root: Path, limit: int = 40) -> tuple[bool, str]:
    if not root.exists():
        return False, f"Dossier introuvable: {root}"
    if not root.is_dir():
        return False, f"Ce chemin n'est pas un dossier: {root}"
    lines = [f"Analyse ce dossier et donne un résumé clair du projet: {root}", ""]
    count = 0
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}]
        for name in files:
            if count >= limit:
                break
            path = Path(current) / name
            if path.suffix.lower() not in TEXT_EXTENSIONS or path.stat().st_size > 40_000:
                continue
            ok, content = read_text_file(path, max_size=40_000)
            if not ok:
                continue
            rel = path.relative_to(root)
            lines.append(f"\n--- {rel} ({detect_language(path, content)}) ---\n{content[:3000]}")
            count += 1
        if count >= limit:
            break
    return True, "\n".join(lines)


def backup_file(path: Path) -> Path:
    backup = path.with_name(path.name + ".backup")
    shutil.copy2(path, backup)
    return backup


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).rstrip() + "\n"


def write_generated_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(strip_code_fence(content), encoding="utf-8")


# ── Analyse de projet (Partie 2) ────────────────────────────────

IGNORED_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache", ".darkcodex", ".idea", ".vscode"}

ROUGE = "\033[31m"
VERT = "\033[32m"
JAUNE = "\033[33m"
FIN_COULEUR = "\033[0m"

PROTECTED_NAMES = {"auth.json"}


def protected_path(path: Path) -> bool:
    """Fichiers sensibles : jamais modifiés sans confirmation explicite."""
    name = path.name.lower()
    return name.startswith(".env") or name in PROTECTED_NAMES


def fichier_infos(path: Path, content: str) -> str:
    lignes = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    taille = path.stat().st_size
    return f"📄 {path.name} | {detect_language(path, content)} | {lignes} lignes | {taille} octets"


def scan_files(root: Path, limit: int = 200, size_limit: int = 80_000) -> list[Path]:
    fichiers: list[Path] = []
    for path in sorted(root.rglob("*")):
        if len(fichiers) >= limit:
            break
        if path.is_dir():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        if path.is_symlink() or path.stat().st_size > size_limit:
            continue
        fichiers.append(path)
    return fichiers


def arborescence(root: Path, limit: int = 200) -> list[str]:
    """Structure du dossier avec le type de chaque fichier."""
    lignes = [f"📁 {root.name}/"]
    vus: set[tuple[str, ...]] = set()
    for path in scan_files(root, limit=limit):
        rel = path.relative_to(root)
        for niveau in range(len(rel.parts) - 1):
            dossier = rel.parts[: niveau + 1]
            if dossier not in vus:
                vus.add(dossier)
                lignes.append("│   " * niveau + "├── " + dossier[-1] + "/")
        try:
            contenu = path.read_text(encoding="utf-8", errors="replace")[:400]
        except OSError:
            contenu = ""
        langue = detect_language(path, contenu)
        lignes.append("│   " * (len(rel.parts) - 1) + "├── " + path.name + f" [{langue}]")
    return lignes


def _premiere_piste(root: Path, candidats: list[str]) -> str:
    for nom in candidats:
        if (root / nom).is_file():
            return nom
    return ""


def _lire_texte(root: Path, nom: str, taille: int = 20_000) -> str:
    path = root / nom
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:taille]
    except OSError:
        return ""


def detect_framework(root: Path) -> str:
    requirements = _lire_texte(root, "requirements.txt").lower()
    for nom, marqueur in (("FastAPI", "fastapi"), ("Flask", "flask"), ("Django", "django"), ("http.server", "http.server")):
        if marqueur in requirements:
            return nom

    pyproject = _lire_texte(root, "pyproject.toml").lower()
    for nom, marqueur in (("FastAPI", "fastapi"), ("Flask", "flask"), ("Django", "django")):
        if marqueur in pyproject:
            return nom

    package = _lire_texte(root, "package.json")
    if package:
        try:
            data = json.loads(package)
        except json.JSONDecodeError:
            data = {}
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        for marqueur, nom in (("next", "Next.js"), ("react", "React"), ("vue", "Vue"), ("express", "Express"), ("nuxt", "Nuxt"), ("svelte", "Svelte"), ("fastify", "Fastify")):
            if marqueur in deps:
                return nom

    if (root / "pubspec.yaml").is_file():
        return "Flutter"
    if (root / "manage.py").is_file():
        return "Django"
    return "Aucun framework détecté"


def detect_base(root: Path) -> str:
    for nom in ("requirements.txt", "package.json", "pyproject.toml", ".env.example", "supabase_licences.sql"):
        texte = _lire_texte(root, nom, 8000).lower()
        for marqueur, label in (("supabase", "Supabase"), ("postgres", "PostgreSQL"), ("sqlite", "SQLite"), ("mysql", "MySQL"), ("mongodb", "MongoDB"), ("redis", "Redis")):
            if marqueur in texte:
                return label
    return ""


def scan_project(root: Path, limit: int = 200) -> dict:
    """Langage, framework, point d'entrée, dépendances et base de données."""
    fichiers = scan_files(root, limit=limit)
    compte: dict[str, int] = {}
    for path in fichiers:
        langue = detect_language(path)
        if langue != "Texte":
            compte[langue] = compte.get(langue, 0) + 1
    langage = max(compte, key=compte.get) if compte else "Inconnu"

    dependances = next(
        (nom for nom in ("requirements.txt", "package.json", "pubspec.yaml", "go.mod", "Cargo.toml", "pom.xml", "Gemfile") if (root / nom).is_file()),
        "",
    )
    entree = _premiere_piste(
        root,
        [
            "main.py",
            "app.py",
            "manage.py",
            "run.py",
            "src/main.py",
            "lib/main.dart",
            "src/index.ts",
            "src/index.js",
            "index.js",
            "server.js",
            "index.html",
        ],
    )
    return {
        "nom": root.name,
        "langage": langage,
        "framework": detect_framework(root),
        "entree": entree,
        "dependances": dependances,
        "base": detect_base(root),
        "fichiers": len(fichiers),
        "arborescence": arborescence(root, limit=limit),
    }


def resume_projet(info: dict) -> str:
    """Bloc d'en-tête affiché à l'utilisateur et transmis à l'IA."""
    return "\n".join(
        [
            f"📁 {info['nom']}/",
            f"🔧 Langage : {info['langage']}",
            f"📦 Framework : {info['framework']}",
            f"📌 Entrée : {info['entree'] or 'non détecté'}",
            f"📚 Dépendances : {info['dependances'] or 'aucune'}",
            f"🗄️ Base de données : {info['base'] or 'non détectée'}",
            f"🧮 Fichiers analysés : {info['fichiers']}",
            "",
            "Structure :",
            *info["arborescence"],
        ]
    )


def project_prompt(root: Path, limit: int = 200) -> tuple[bool, str]:
    if not root.exists():
        return False, f"Dossier introuvable: {root}"
    if not root.is_dir():
        return False, f"Ce chemin n'est pas un dossier: {root}"
    affichage = resume_projet(scan_project(root, limit=limit))
    prompt = (
        "Analyse ce projet et explique : architecture, rôle de chaque partie, "
        "technologies, points d'entrée, puis 3 améliorations concrètes. Sois structuré.\n\n"
        + affichage
    )
    return True, prompt


# ── Modification / refactorisation de fichier ──────────────────


def modify_prompt(path: Path, content: str, instruction: str) -> str:
    language = detect_language(path, content)
    return (
        f"Modifie le fichier {path.name} ({language}) selon l'instruction.\n"
        "Réponds UNIQUEMENT avec le contenu COMPLET du fichier modifié, sans Markdown, "
        "sans explication et sans ligne de commentaire ajoutée en tête.\n\n"
        f"Instruction: {instruction}\n\nContenu actuel:\n{content}"
    )


def refactor_prompt(path: Path, content: str) -> str:
    language = detect_language(path, content)
    return (
        f"Refactorise ce code {language} : supprime les répétitions, découpe les fonctions trop longues, "
        "corrige les mauvaises pratiques. Garde le comportement identique.\n"
        "Réponds UNIQUEMENT avec le contenu COMPLET du fichier refactorisé, sans Markdown ni explication.\n\n"
        f"Fichier: {path.name}\n\nContenu:\n{content}"
    )


def render_diff(ancien: str, nouveau: str, nom: str = "fichier") -> str:
    """Diff coloré : suppressions en rouge, ajouts en vert."""
    lignes = list(
        difflib.unified_diff(
            ancien.splitlines(),
            nouveau.splitlines(),
            fromfile=f"a/{nom}",
            tofile=f"b/{nom}",
            lineterm="",
            n=3,
        )
    )
    if not lignes:
        return f"{JAUNE}Aucun changement détecté.{FIN_COULEUR}"
    sortie: list[str] = []
    for ligne in lignes:
        if ligne.startswith("+++") or ligne.startswith("---"):
            sortie.append(f"\033[1m{ligne}{FIN_COULEUR}")
        elif ligne.startswith("+"):
            sortie.append(f"{VERT}{ligne}{FIN_COULEUR}")
        elif ligne.startswith("-"):
            sortie.append(f"{ROUGE}{ligne}{FIN_COULEUR}")
        else:
            sortie.append(ligne)
    return "\n".join(sortie)


def extraire_json(texte: str) -> dict | list | None:
    """Extrait le premier objet/tableau JSON d'une réponse du modèle."""
    brut = texte.strip()
    if brut.startswith("```"):
        brut = strip_code_fence(brut)
    debut = min((i for i in (brut.find("{"), brut.find("[")) if i != -1), default=-1)
    if debut == -1:
        return None
    fin = max(brut.rfind("}"), brut.rfind("]"))
    if fin < debut:
        return None
    try:
        return json.loads(brut[debut : fin + 1])
    except json.JSONDecodeError:
        return None


def appliquer_fichier(cible: Path, contenu: str, ctx, action: str = "modification") -> bool:
    """Sauvegarde, écrit, affiche le diff coloré. False si refusé ou inchangé."""
    nettoye = strip_code_fence(contenu)
    ancien = ""
    if cible.exists():
        ok, ancien = read_text_file(cible)
        if not ok:
            ctx.note(ancien)
            return False
        if ancien.strip() == nettoye.strip():
            ctx.note(f"Aucun changement : {cible.name}")
            return False
        if protected_path(cible) and not ctx.confirm_always(
            f"{cible.name} est un fichier protégé. Modifier quand même ?"
        ):
            ctx.note(f"Fichier protégé, non modifié : {cible}")
            return False
        if not ctx.confirm_edit("Voulez-vous appliquer cette modification ?"):
            ctx.note(f"Modification refusée : {cible}")
            return False
        backup = backup_file(cible)
        ctx.note(f"💾 Sauvegarde : {backup.name}")
    else:
        if not ctx.confirm_edit(f"Créer le fichier {cible.name} ?"):
            ctx.note(f"Création refusée : {cible}")
            return False

    historique.enregistrer(getattr(ctx, "cwd", None) or cible.parent, cible)
    write_generated_file(cible, nettoye)
    try:
        nouveau = cible.read_text(encoding="utf-8", errors="replace")
    except OSError:
        nouveau = nettoye
    ctx.note(f"Modification appliquée à [{cible.name}]")
    ctx.note(render_diff(ancien, nouveau, cible.name))
    ctx.note_modification(cible, action)
    return True


def extraits_fichiers(
    cwd: Path,
    suffixes: tuple[str, ...] = (".py", ".js", ".ts", ".tsx", ".jsx"),
    limit: int = 6,
    taille: int = 6000,
) -> str:
    """Extraits de code des fichiers source, pour alimenter l'IA."""
    extraits: list[str] = []
    for path in scan_files(cwd, limit=60, size_limit=40_000):
        if len(extraits) >= limit:
            break
        if path.suffix.lower() not in suffixes or protected_path(path):
            continue
        ok, contenu = read_text_file(path, max_size=40_000)
        if ok:
            extraits.append(f"--- {path.name} ---\n{contenu[:taille]}")
    return "\n\n".join(extraits)


def corriger_avec_ia(ctx, instruction: str, extraits: str, max_fichiers: int = 6) -> bool:
    """Demande des fichiers corrigés en JSON puis les applique (sauvegarde + diff)."""
    prompt = (
        f"{instruction}\n\n"
        f"{extraits}\n\n"
        'Réponds UNIQUEMENT avec un objet JSON valide : '
        '{"fichiers": [{"chemin": "...", "contenu": "code complet du fichier"}]}'
    )
    code, texte = ctx.ask(prompt)
    if code != 0:
        ctx.note(texte)
        return False
    donnees = extraire_json(texte)
    if not isinstance(donnees, dict) or not donnees.get("fichiers"):
        ctx.note("Aucune correction proposée par l'IA.")
        return False
    applique = False
    for item in donnees["fichiers"][:max_fichiers]:
        if not isinstance(item, dict) or not item.get("contenu"):
            continue
        cible = resolve_path(ctx.cwd, str(item.get("chemin") or ""))
        if appliquer_fichier(cible, str(item["contenu"]), ctx, action="correction"):
            applique = True
    return applique

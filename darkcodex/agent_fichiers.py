import os
import shutil
from pathlib import Path


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

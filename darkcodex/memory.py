import datetime as dt
import json
import os
import re
from pathlib import Path


MEMORY_PATH = Path(os.environ.get("DARKCODEX_MEMORY_PATH", Path.home() / ".darkcodex_memory.json"))

DEFAULT_MEMORY = {
    "conversations": [],
    "contexte_projet": "",
    "derniere_session": "",
    "faits_importants": [],
}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def default_memory() -> dict:
    return dict(DEFAULT_MEMORY)


def load_memory(path: Path = MEMORY_PATH) -> dict:
    if not path.exists():
        data = default_memory()
        save_memory(data, path)
        return data
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = default_memory()
    merged = {**default_memory(), **data}
    for key in ("conversations", "faits_importants"):
        if not isinstance(merged.get(key), list):
            merged[key] = []
    return merged


def save_memory(data: dict, path: Path = MEMORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**default_memory(), **data}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def set_project_context(text: str, path: Path = MEMORY_PATH) -> None:
    data = load_memory(path)
    data["contexte_projet"] = text.strip()
    data["derniere_session"] = _now()
    save_memory(data, path)


def clear_memory(path: Path = MEMORY_PATH) -> None:
    save_memory(default_memory(), path)


def remember_conversation(user_text: str, assistant_text: str = "", path: Path = MEMORY_PATH) -> None:
    data = load_memory(path)
    data["derniere_session"] = _now()
    data["conversations"].append(
        {
            "date": data["derniere_session"],
            "user": user_text[-4000:],
            "assistant": assistant_text[-4000:],
        }
    )
    data["conversations"] = data["conversations"][-80:]
    for fact in detect_important_facts(user_text + "\n" + assistant_text):
        if fact not in data["faits_importants"]:
            data["faits_importants"].append(fact)
    data["faits_importants"] = data["faits_importants"][-120:]
    save_memory(data, path)


def memory_summary(path: Path = MEMORY_PATH) -> str:
    data = load_memory(path)
    return json.dumps(data, indent=2, ensure_ascii=False)


def startup_message(path: Path = MEMORY_PATH) -> str:
    context = str(load_memory(path).get("contexte_projet") or "").strip()
    if not context:
        context = "aucun contexte enregistré"
    return f"Je me souviens de votre dernier projet : {context}"


def detect_important_facts(text: str) -> list[str]:
    facts: list[str] = []
    patterns = [
        (r"\bprojet\s+(?:s'appelle|nommé|nomme|appelé|appele)\s+([A-Za-z0-9_.-]+)", "Nom du projet"),
        (r"\b(?:langage|language)\s*:\s*([A-Za-z0-9_+#.-]+)", "Langage"),
        (r"\bobjectif\s*:\s*(.{6,120})", "Objectif"),
        (r"\bbut\s*:\s*(.{6,120})", "Objectif"),
    ]
    for pattern, label in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = match.group(1).strip().rstrip(".")
            facts.append(f"{label}: {value}")

    lowered = text.lower()
    language_hits = {
        "Python": ["python", ".py"],
        "JavaScript": ["javascript", "node", ".js"],
        "TypeScript": ["typescript", ".ts"],
        "Shell": ["bash", "shell", "termux"],
    }
    for language, markers in language_hits.items():
        if any(marker in lowered for marker in markers):
            facts.append(f"Langage utilisé: {language}")
    return facts

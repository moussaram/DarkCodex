import json
from pathlib import Path

from . import memory


LANGUAGES = {
    "fr": "Français",
    "en": "English",
    "fon": "Fon",
    "yor": "Yoruba",
    "diu": "Dioula",
    "wol": "Wolof",
}

DEFAULT_LANGUAGE = "fr"

MESSAGES = {
    "memory_loaded": {
        "fr": "Mémoire chargée",
        "en": "Memory loaded",
        "fon": "Mémoire chargée",
        "yor": "Memory loaded",
        "diu": "Mémoire chargée",
        "wol": "Mémoire chargée",
    },
    "file_agent_active": {
        "fr": "Agent fichiers : ACTIF",
        "en": "File agent: ACTIVE",
        "fon": "Agent fichiers : ACTIF",
        "yor": "File agent: ACTIVE",
        "diu": "Agent fichiers : ACTIF",
        "wol": "Agent fichiers : ACTIF",
    },
    "preferred_language": {
        "fr": "Quelle est ta langue préférée ?",
        "en": "What is your preferred language?",
        "fon": "Quelle est ta langue préférée ?",
        "yor": "What is your preferred language?",
        "diu": "Quelle est ta langue préférée ?",
        "wol": "Quelle est ta langue préférée ?",
    },
    "language_changed": {
        "fr": "Langue active",
        "en": "Active language",
        "fon": "Langue active",
        "yor": "Active language",
        "diu": "Langue active",
        "wol": "Langue active",
    },
}


def _data(path: Path = memory.MEMORY_PATH) -> dict:
    return memory.load_memory(path)


def get_active_language(path: Path = memory.MEMORY_PATH) -> str:
    code = str(_data(path).get("langue_active") or DEFAULT_LANGUAGE).lower()
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


def has_active_language(path: Path = memory.MEMORY_PATH) -> bool:
    return str(_data(path).get("langue_active") or "").lower() in LANGUAGES


def set_active_language(code: str, path: Path = memory.MEMORY_PATH) -> tuple[bool, str]:
    code = code.strip().lower()
    if code not in LANGUAGES:
        return False, f"Langue inconnue: {code}"
    data = _data(path)
    data["langue_active"] = code
    memory.save_memory(data, path)
    return True, f"{tr('language_changed', code)} : {LANGUAGES[code]} ({code})"


def ensure_language(path: Path = memory.MEMORY_PATH) -> str:
    data = _data(path)
    if data.get("langue_active") in LANGUAGES:
        return str(data["langue_active"])
    data["langue_active"] = DEFAULT_LANGUAGE
    memory.save_memory(data, path)
    return DEFAULT_LANGUAGE


def list_languages() -> str:
    return "\n".join(f"{code} - {name}" for code, name in LANGUAGES.items())


def tr(key: str, code: str | None = None) -> str:
    lang = code or get_active_language()
    return MESSAGES.get(key, {}).get(lang) or MESSAGES.get(key, {}).get(DEFAULT_LANGUAGE) or key


def format_startup(path: Path = memory.MEMORY_PATH) -> str:
    code = ensure_language(path)
    return "\n".join(
        [
            f"OK {tr('memory_loaded', code)}",
            f"Langue active : {LANGUAGES[code]}",
            f"OK {tr('file_agent_active', code)}",
        ]
    )


def translate_prompt(text: str, target_code: str) -> str:
    target = LANGUAGES.get(target_code, LANGUAGES[DEFAULT_LANGUAGE])
    return f"Traduis ce texte en {target}. Réponds uniquement avec la traduction.\n\n{text}"


def dump_language_state(path: Path = memory.MEMORY_PATH) -> str:
    data = _data(path)
    return json.dumps({"langue_active": get_active_language(path), "langues": LANGUAGES, "memoire": data}, ensure_ascii=False)

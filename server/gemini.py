"""Appel à l'API Gemini côté serveur.

La clé Gemini est lue uniquement dans l'environnement du serveur
(GEMINI_API_KEY) : elle ne doit jamais apparaître dans le code client.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_MODEL = "gemini-2.5-flash"
TIMEOUT_SECONDS = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "120"))
SYSTEM_PROMPT = """You are DarkCodex, a powerful terminal coding assistant.
You provide direct, technical, and complete help for coding, debugging, project analysis,
terminal workflows, and authorized security work in the user's environment.
Be concise, technical, and action-oriented."""


class GeminiError(RuntimeError):
    """Erreur remontée vers l'API /chat (message déjà lisible par l'utilisateur)."""


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise GeminiError("GEMINI_API_KEY absente sur le serveur.")
    return key


def model() -> str:
    return (os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL)


def _history_contents(historique: list[dict[str, Any]] | None, message: str) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for item in historique or []:
        role = "model" if str(item.get("role", "")).lower() in {"assistant", "model"} else "user"
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        contents.append({"role": role, "parts": [{"text": text}]})
    if not contents or contents[-1]["role"] != "user" or contents[-1]["parts"][0]["text"] != message:
        contents.append({"role": "user", "parts": [{"text": message}]})
    return contents


def appeler_gemini(
    message: str,
    historique: list[dict[str, Any]] | None = None,
    user_id: str | None = None,
) -> tuple[str, int]:
    """Retourne (reponse, tokens_utilises). Lève GeminiError en cas d'échec."""
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": _history_contents(historique, message),
        "generationConfig": {"temperature": 0.2},
    }
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model()}:generateContent"
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key()},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise GeminiError(f"Gemini HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GeminiError(f"Gemini injoignable: {exc.reason}") from exc
    except TimeoutError as exc:
        raise GeminiError("Gemini: délai dépassé.") from exc
    except json.JSONDecodeError as exc:
        raise GeminiError("Gemini a renvoyé du JSON invalide.") from exc

    parts: list[str] = []
    for candidate in data.get("candidates", []):
        for part in (candidate.get("content") or {}).get("parts", []):
            text = part.get("text")
            if text:
                parts.append(text)
    if not parts:
        raise GeminiError("Gemini n'a renvoyé aucun texte.")

    usage = data.get("usageMetadata") or {}
    try:
        tokens = int(usage.get("totalTokenCount") or 0)
    except (TypeError, ValueError):
        tokens = 0
    if not tokens:
        try:
            tokens = int(usage.get("promptTokenCount") or 0) + int(
                usage.get("candidatesTokenCount") or 0
            )
        except (TypeError, ValueError):
            tokens = 0

    return "\n".join(parts).strip(), max(0, tokens)
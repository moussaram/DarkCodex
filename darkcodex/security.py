import base64
import hashlib


# Cle Gemini obfusquee - ne pas modifier manuellement.
# Remplacer cette valeur avec obfuscate_key(cle) au moment d'une distribution privee.
_K = b"QUl6YVN5QUxYUUpGUy1mZUxmVDFSSHNvWG01VmM3SzJuYlF5NkdF"
_H = "489878d0c4e8398d46f6eaabe7f3d848cf5819db631fc3315ca6096b8723b669"


def obfuscate_key(key: str) -> str:
    cleaned = key.strip()
    return base64.b64encode(cleaned.encode("utf-8")).decode("ascii")


def key_hash(key: str) -> str:
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def get_api_key() -> str:
    if not _K:
        return ""
    decoded = base64.b64decode(_K).decode("utf-8")
    if _H and key_hash(decoded) != _H:
        raise RuntimeError("Gemini API key integrity check failed.")
    return decoded

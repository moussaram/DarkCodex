import base64
import hashlib


# C08 : la clé API n'est plus jamais embarquée dans le code client. Seuls des
# utilitaires d'obfusquage/hachage restent disponibles pour l'installateur.


def obfuscate_key(key: str) -> str:
    cleaned = key.strip()
    return base64.b64encode(cleaned.encode("utf-8")).decode("ascii")


def key_hash(key: str) -> str:
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()

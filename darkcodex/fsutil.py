"""Écriture atomique des fichiers d'état (config, auth, clé API).

Écrit dans un fichier temporaire puis remplace d'un coup, pour qu'une coupure
de courant ou un Termux tué ne tronque jamais un fichier de configuration.
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)

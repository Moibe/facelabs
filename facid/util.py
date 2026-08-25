"""Utilidades chiquitas compartidas. Sin dependencias del modelo."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    """SHA-256 del contenido de un archivo.

    Es la identidad canónica de una imagen en todo el proyecto: la ruta puede
    cambiar, el contenido no. Sin esto no se puede reusar un embedding entre
    corridas ni afirmar que dos experimentos vieron el mismo pixel.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(chunk), b""):
            h.update(bloque)
    return h.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

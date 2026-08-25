"""Configuración y rutas. Todo lo que afecta reproducibilidad vive aquí."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import FacePolicy

REPO_ROOT = Path(__file__).resolve().parent.parent

# Configurables por env para poder apuntar a otro set (o a un temporal en las
# pruebas) sin tocar el codigo ni mover fotos de lugar.
DATA_DIR = Path(os.environ.get("FACID_DATA", REPO_ROOT / "data")).expanduser()
OUT_DIR = Path(os.environ.get("FACID_OUT", REPO_ROOT / "out")).expanduser()
EMBEDDINGS_DIR = OUT_DIR / "embeddings"
INDEX_DB = OUT_DIR / "index.sqlite"

# insightface baja/lee los pesos en {MODEL_ROOT}/models/{MODEL_PACK}/.
# Default: dentro del repo, para que el experimento sea auto-contenido y los
# .onnx sean hasheables. Apúntalo a ~/.insightface si ya los tienes bajados.
MODEL_ROOT = Path(os.environ.get("FACID_MODEL_ROOT", REPO_ROOT)).expanduser()
MODEL_PACK = os.environ.get("FACID_MODEL_PACK", "buffalo_l")

# Solo detección + reconocimiento. Los otros módulos del pack (landmarks 3D,
# genderage) no se usan y solo agregan superficie de fallo y tiempo de carga.
ALLOWED_MODULES = ("detection", "recognition")

# det_size cambia el crop y por lo tanto el embedding -> forma parte de la
# llave de caché en SQLite. Cambiarlo invalida embeddings previos, a propósito.
DET_SIZE = (640, 640)

DEFAULT_FACE_POLICY = FacePolicy.STRICT
DEFAULT_DEVICE = os.environ.get("FACID_DEVICE", "cuda")  # 'cuda' | 'cpu'

CUDA_PROVIDER = "CUDAExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"


def ensure_dirs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)

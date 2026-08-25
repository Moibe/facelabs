"""Etapa 1: imagen -> deteccion -> alineacion -> embedding 512-d.

Independiente de la comparacion a proposito. Esta etapa es la cara, no el
threshold: se puede recalibrar mil veces sin volver a tocar un pixel.

Contrato de retorno (el del handoff, mas campos de auditoria):
    embedding        np.ndarray 512-d unitario | None
    bbox             [x, y, w, h] | None
    det_score        float | None
    n_faces_detected int
    error            str | None   <- codigo estable de errors.ErrorCode
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .compare import l2_normalize
from .config import DEFAULT_FACE_POLICY
from .errors import DESCRIPCIONES, ErrorCode, FacePolicy
from .util import sha256_file


def _resultado(error: str | None = None, **campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "embedding": None,
        "bbox": None,
        "det_score": None,
        "n_faces_detected": 0,
        "error": error,
        "error_message": DESCRIPCIONES.get(error) if error else None,
        "face_selection": None,
        "all_det_scores": None,
        "image_sha256": None,
        "source_path": None,
    }
    base.update(campos)
    return base


def _leer_imagen(path: Path):
    """Devuelve (imagen_bgr | None, exif_aplicado: bool).

    cv2.imread respeta la orientacion EXIF; cv2.imdecode NO. Importa: una foto
    de celular en vertical mal rotada suele terminar en NO_FACE, y el fallback
    lo dejaria pasar sin avisar. Por eso se reporta cual de los dos se uso.
    """
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is not None:
        return img, True

    # Fallback para rutas con caracteres no-ASCII, donde imread puede fallar.
    try:
        buf = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:
        img = None
    return img, False


def extract_embedding(image_path: str | Path, runtime, *,
                      face_policy: str = DEFAULT_FACE_POLICY) -> dict[str, Any]:
    """Extrae el embedding de UN rostro de `image_path`.

    `runtime` es un FaceRuntime ya cargado (ver runtime.load_runtime). Se pasa
    como parametro en vez de crearse aqui para no re-cargar 300 MB de ONNX por
    imagen, y para que la huella del entorno sea la misma en toda la corrida.

    Nunca lanza por condiciones de datos: devuelve el codigo en `error`.
    """
    if face_policy not in FacePolicy.TODAS:
        raise ValueError(f"face_policy invalida: {face_policy!r}")

    path = Path(image_path)
    if not path.is_file():
        return _resultado(ErrorCode.FILE_NOT_FOUND, source_path=str(path))

    sha = sha256_file(path)
    comun = {"source_path": str(path), "image_sha256": sha}

    img, exif_aplicado = _leer_imagen(path)
    if img is None or getattr(img, "size", 0) == 0:
        return _resultado(ErrorCode.UNREADABLE_IMAGE, **comun)

    faces = runtime.app.get(img)
    n = len(faces)
    det_scores = [round(float(f.det_score), 6) for f in faces]
    comun["n_faces_detected"] = n
    comun["all_det_scores"] = det_scores
    comun["exif_orientation_applied"] = exif_aplicado

    if n == 0:
        return _resultado(ErrorCode.NO_FACE, **comun)

    if n == 1:
        face = faces[0]
        seleccion = "unico"
    elif face_policy == FacePolicy.STRICT:
        # No adivinar. El conteo y los det_score quedan registrados igual.
        return _resultado(ErrorCode.MULTIPLE_FACES, **comun)
    else:
        # Politica 'largest': se toma el de mayor area PERO queda anotado en
        # face_selection y viaja hasta el CSV. La ambiguedad no se silencia.
        def area(f) -> float:
            x1, y1, x2, y2 = f.bbox
            return float(max(0.0, x2 - x1) * max(0.0, y2 - y1))

        face = max(faces, key=area)
        seleccion = f"mayor_area_de_{n}"

    emb = getattr(face, "normed_embedding", None)
    if emb is None:
        emb = getattr(face, "embedding", None)
    if emb is None:
        return _resultado(ErrorCode.INVALID_EMBEDDING, face_selection=seleccion, **comun)

    emb = np.asarray(emb, dtype=np.float32).ravel()
    if not np.all(np.isfinite(emb)):
        return _resultado(ErrorCode.INVALID_EMBEDDING, face_selection=seleccion, **comun)
    try:
        emb = l2_normalize(emb)  # idempotente si ya venia unitario
    except ValueError:
        return _resultado(ErrorCode.INVALID_EMBEDDING, face_selection=seleccion, **comun)

    x1, y1, x2, y2 = (float(v) for v in face.bbox)
    return _resultado(
        None,
        embedding=emb,
        bbox=[x1, y1, x2 - x1, y2 - y1],   # el handoff pide [x, y, w, h]
        det_score=float(face.det_score),
        face_selection=seleccion,
        **comun,
    )

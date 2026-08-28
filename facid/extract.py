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
        # 0 = se detecto en la imagen tal cual. >0 = solo se detecto despues
        # de rellenar ese % de margen alrededor (ver _detectar_con_margen).
        "margen_agregado": 0.0,
    }
    base.update(campos)
    return base


# Un detector de rostros necesita CONTEXTO alrededor de la cara: esta
# entrenado para encontrarla dentro de una escena, no para confirmar un
# recorte que ya viene pegado a la cara. Medido sobre este mismo pipeline,
# con el MISMO rostro y el mismo tamaño final, un recorte con 0-25% de margen
# da NO_FACE y con 50% se detecta sin problema.
#
# Eso hace fallar cualquier corpus de recortes ya hechos por otra herramienta
# (que detecto bien, pero sobre el cuadro completo). Rellenar el borde
# devuelve el contexto que el recorte quito.
MARGENES_REINTENTO = (0.5, 1.0)
# Arriba de esto la imagen ya trae contexto de sobra: si ahi no hay rostro,
# rellenar no lo va a inventar y solo cuesta dos detecciones mas por imagen.
LADO_MAX_PARA_REINTENTO = 400


def _detectar_con_margen(img, runtime, margen: float):
    """Reintenta la deteccion sobre la imagen con un borde replicado.

    BORDER_REPLICATE y no un relleno negro: una franja negra dura mete bordes
    artificiales fuertes justo donde el detector busca contornos. Replicar el
    pixel del borde es mas neutro. (Ambos funcionan en la practica; se eligio
    el menos invasivo.)
    """
    import cv2

    h, w = img.shape[:2]
    mx, my = int(w * margen), int(h * margen)
    grande = cv2.copyMakeBorder(img, my, my, mx, mx, cv2.BORDER_REPLICATE)
    faces = runtime.app.get(grande)
    # Las coordenadas vuelven al sistema de la imagen ORIGINAL: el bbox que se
    # persiste debe seguir siendo valido sobre el archivo real, no sobre un
    # lienzo temporal que no existe en disco.
    for f in faces:
        f.bbox = np.asarray(f.bbox, dtype=np.float32) - np.array(
            [mx, my, mx, my], dtype=np.float32)
    return faces


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
                      face_policy: str = DEFAULT_FACE_POLICY,
                      reintentar_con_margen: bool = True) -> dict[str, Any]:
    """Extrae el embedding de UN rostro de `image_path`.

    `runtime` es un FaceRuntime ya cargado (ver runtime.load_runtime). Se pasa
    como parametro en vez de crearse aqui para no re-cargar 300 MB de ONNX por
    imagen, y para que la huella del entorno sea la misma en toda la corrida.

    `reintentar_con_margen`: si no se detecta nada en una imagen chica, se
    reintenta rellenando el borde (ver MARGENES_REINTENTO). Cuanto margen hizo
    falta queda en el campo `margen_agregado` — no se esconde que ese rostro
    solo aparecio despues de ayudarlo.

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
    margen_usado = 0.0

    if not faces and reintentar_con_margen \
            and max(img.shape[:2]) <= LADO_MAX_PARA_REINTENTO:
        for margen in MARGENES_REINTENTO:
            faces = _detectar_con_margen(img, runtime, margen)
            if faces:
                margen_usado = margen
                break

    n = len(faces)
    det_scores = [round(float(f.det_score), 6) for f in faces]
    comun["n_faces_detected"] = n
    comun["all_det_scores"] = det_scores
    comun["exif_orientation_applied"] = exif_aplicado
    comun["margen_agregado"] = margen_usado

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

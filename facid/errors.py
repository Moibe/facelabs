"""Errores tipados de extracción.

Regla del pipeline: toda condición *esperable* de los datos se devuelve como un
código estable, nunca como una excepción suelta. Así el harness puede registrar
el fallo en el CSV y seguir con el resto del manifiesto, en vez de morirse a la
mitad de una corrida de calibración.

Las excepciones se reservan para errores de *programación* (shapes que no
cuadran, dimensiones distintas), que sí deben tronar ruidosamente.
"""

from __future__ import annotations


class ErrorCode:
    """Códigos estables. Se escriben tal cual en el CSV y en SQLite."""

    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    UNREADABLE_IMAGE = "UNREADABLE_IMAGE"
    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    INVALID_EMBEDDING = "INVALID_EMBEDDING"


DESCRIPCIONES: dict[str, str] = {
    ErrorCode.FILE_NOT_FOUND: "La ruta no existe.",
    ErrorCode.UNREADABLE_IMAGE: "El archivo existe pero no se pudo decodificar como imagen.",
    ErrorCode.NO_FACE: "El detector no encontró ningún rostro.",
    ErrorCode.MULTIPLE_FACES: (
        "Se detectó más de un rostro y la política activa es 'strict'. "
        "Recorta la imagen o corre con --face-policy largest."
    ),
    ErrorCode.INVALID_EMBEDDING: "El embedding salió con norma cero o con NaN/Inf.",
}


class FacePolicy:
    """Qué hacer cuando hay más de un rostro en la imagen.

    El handoff es explícito: no adivinar. Cualquiera de las dos ramas es
    aceptable siempre y cuando la ambigüedad quede *registrada*, nunca silenciada.
    """

    STRICT = "strict"    # >1 rostro -> error MULTIPLE_FACES (default)
    LARGEST = "largest"  # toma el de mayor área y lo anota en face_selection

    TODAS = (STRICT, LARGEST)

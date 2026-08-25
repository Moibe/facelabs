"""Etapa 2: comparación. Deliberadamente independiente de la extracción.

Este módulo NO sabe qué es un threshold y no debe aprenderlo. Devuelve el score
crudo; la decisión sí/no vive en decide.py y recibe el umbral como parámetro.
Esa separación es lo que permite recalibrar sin reprocesar imágenes.
"""

from __future__ import annotations

import numpy as np

EMBEDDING_DIM = 512


def l2_normalize(v: np.ndarray) -> np.ndarray:
    """Normaliza a norma unitaria. Lanza si el vector es degenerado."""
    v = np.asarray(v, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(v))
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError(f"Embedding degenerado: norma={norm!r}")
    return v / norm


def compare(embedding_a: np.ndarray, embedding_b: np.ndarray) -> float:
    """Similitud coseno entre dos embeddings. Rango [-1, 1].

    Renormaliza por defensa: los embeddings persistidos ya vienen unitarios,
    pero si alguien pasa un vector crudo el resultado debe seguir siendo coseno
    y no un producto punto con escala arbitraria.
    """
    a = np.asarray(embedding_a, dtype=np.float32).ravel()
    b = np.asarray(embedding_b, dtype=np.float32).ravel()

    if a.shape != b.shape:
        raise ValueError(f"Dimensiones distintas: {a.shape} vs {b.shape}")
    if a.size == 0:
        raise ValueError("Embedding vacío")

    score = float(np.dot(l2_normalize(a), l2_normalize(b)))
    # Amarra el redondeo de float32 dentro del rango teórico.
    return max(-1.0, min(1.0, score))

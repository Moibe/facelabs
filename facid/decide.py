"""Capa de decisión: la ÚNICA que conoce un threshold.

Existe separada a propósito. Ninguna función de comparación debe hornear un
umbral adentro — el umbral es el resultado del experimento, no una constante.
"""

from __future__ import annotations


def decide(score: float, threshold: float) -> bool:
    """True = 'misma persona' bajo el umbral dado.

    El criterio es `score >= threshold` y se usa idéntico en calibrate.py.
    Si esto cambiara, FMR/FNMR dejarían de corresponder con el comportamiento
    real del sistema.
    """
    if not (-1.0 <= threshold <= 1.0):
        raise ValueError(f"Threshold fuera de rango [-1,1]: {threshold}")
    return float(score) >= float(threshold)

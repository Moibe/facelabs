"""Harness de evaluacion: manifiesto de pares etiquetados -> CSV de scores.

Corre en dos pasadas separadas, igual que la arquitectura:
  1. extraccion  — cada imagen UNICA se procesa una sola vez (y se cachea)
  2. comparacion — los pares se resuelven contra los embeddings ya guardados

Por eso agregar pares nuevos a un manifiesto no reprocesa las imagenes viejas.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .compare import compare
from .config import DEFAULT_FACE_POLICY
from .extract import extract_embedding
from .store import EmbeddingStore
from .util import sha256_file

# Las 7 primeras son las que pide el handoff, en ese orden. Las demas son
# auditoria: sin ellas, un par que fallo la extraccion seria una fila vacia
# sin explicacion, y calibrate.py no sabria que debe excluirlo.
COLUMNAS = [
    "img_a", "img_b", "same_person", "score", "det_score_a", "det_score_b", "notes",
    "pair_ok", "error_a", "error_b",
    "n_faces_a", "n_faces_b", "face_selection_a", "face_selection_b",
    "sha256_a", "sha256_b",
]


class ManifestError(ValueError):
    """El manifiesto esta mal formado. Se falla antes de cargar 300 MB de ONNX."""


def cargar_manifiesto(path: str | Path) -> list[dict[str, Any]]:
    """Lee y valida el manifiesto. Las rutas relativas se resuelven respecto
    a la carpeta del propio manifiesto, para que sea portable entre maquinas."""
    p = Path(path)
    if not p.is_file():
        raise ManifestError(f"No existe el manifiesto: {p}")

    try:
        datos = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"JSON invalido en {p}: {e}") from e

    if not isinstance(datos, list):
        raise ManifestError("El manifiesto debe ser una lista de objetos.")
    if not datos:
        raise ManifestError("El manifiesto esta vacio.")

    base = p.parent
    pares: list[dict[str, Any]] = []
    problemas: list[str] = []

    for i, item in enumerate(datos):
        if not isinstance(item, dict):
            problemas.append(f"[{i}] no es un objeto JSON")
            continue
        faltan = [k for k in ("img_a", "img_b", "same_person") if k not in item]
        if faltan:
            problemas.append(f"[{i}] faltan claves: {', '.join(faltan)}")
            continue
        if not isinstance(item["same_person"], bool):
            problemas.append(
                f"[{i}] same_person debe ser true/false, no {item['same_person']!r}")
            continue

        def resolver(rel: str) -> Path:
            r = Path(rel)
            return r if r.is_absolute() else (base / r)

        pares.append({
            "idx": i,
            "img_a": resolver(item["img_a"]),
            "img_b": resolver(item["img_b"]),
            "img_a_raw": item["img_a"],
            "img_b_raw": item["img_b"],
            "same_person": bool(item["same_person"]),
            "notes": str(item.get("notes", "")),
        })

    if problemas:
        raise ManifestError("Manifiesto invalido:\n  " + "\n  ".join(problemas))
    return pares


def _extraer_todas(pares, runtime, store: EmbeddingStore, face_policy: str,
                   force: bool, verbose: bool) -> dict[str, dict[str, Any]]:
    """Pasada 1. Devuelve {ruta_str: info}, una entrada por imagen unica."""
    rutas: list[Path] = []
    vistas: set[str] = set()
    for par in pares:
        for lado in ("img_a", "img_b"):
            s = str(par[lado])
            if s not in vistas:
                vistas.add(s)
                rutas.append(par[lado])

    fp = runtime.fingerprint
    resultados: dict[str, dict[str, Any]] = {}

    for k, ruta in enumerate(rutas, 1):
        clave = str(ruta)
        if not ruta.is_file():
            resultados[clave] = {"ok": False, "error": "FILE_NOT_FOUND",
                                 "sha256": None, "det_score": None,
                                 "n_faces": 0, "face_selection": None}
            if verbose:
                print(f"  [{k}/{len(rutas)}] FILE_NOT_FOUND  {ruta}")
            continue

        sha = sha256_file(ruta)
        fila = None if force else store.buscar(
            sha, fp.model_pack, runtime.rec_model_sha256, fp.det_size, face_policy)

        if fila is not None:
            resultados[clave] = {
                "ok": True, "error": None, "sha256": sha,
                "embedding": store.cargar_embedding(fila),
                "det_score": float(fila["det_score"]),
                "n_faces": int(fila["n_faces_detected"]),
                "face_selection": fila["face_selection"],
            }
            if verbose:
                print(f"  [{k}/{len(rutas)}] cache          {ruta.name}")
            continue

        r = extract_embedding(ruta, runtime, face_policy=face_policy)
        if r["error"] is None:
            store.guardar(r, runtime, face_policy)
            resultados[clave] = {
                "ok": True, "error": None, "sha256": r["image_sha256"],
                "embedding": r["embedding"], "det_score": r["det_score"],
                "n_faces": r["n_faces_detected"],
                "face_selection": r["face_selection"],
            }
            if verbose:
                print(f"  [{k}/{len(rutas)}] ok det={r['det_score']:.3f}  {ruta.name}")
        else:
            store.registrar_fallo(r, runtime, face_policy)
            resultados[clave] = {
                "ok": False, "error": r["error"], "sha256": r["image_sha256"],
                "det_score": r["det_score"], "n_faces": r["n_faces_detected"],
                "face_selection": r["face_selection"],
            }
            if verbose:
                print(f"  [{k}/{len(rutas)}] {r['error']}  {ruta.name}"
                      f"  (rostros={r['n_faces_detected']})")

    return resultados


def run_manifest(manifest_path: str | Path, runtime, csv_out: str | Path, *,
                 face_policy: str = DEFAULT_FACE_POLICY, force: bool = False,
                 verbose: bool = True) -> dict[str, Any]:
    """Corre el manifiesto completo y escribe el CSV. Devuelve un resumen."""
    pares = cargar_manifiesto(manifest_path)
    store = EmbeddingStore()

    if verbose:
        print(f"[facid] manifiesto: {len(pares)} pares")
        print("[facid] pasada 1/2 — extraccion de embeddings")

    extraidos = _extraer_todas(pares, runtime, store, face_policy, force, verbose)

    if verbose:
        print("[facid] pasada 2/2 — comparacion de pares")

    filas: list[dict[str, Any]] = []
    n_ok = 0
    for par in pares:
        a = extraidos[str(par["img_a"])]
        b = extraidos[str(par["img_b"])]
        pair_ok = a["ok"] and b["ok"]
        score = compare(a["embedding"], b["embedding"]) if pair_ok else None
        if pair_ok:
            n_ok += 1
        filas.append({
            "img_a": par["img_a_raw"], "img_b": par["img_b_raw"],
            "same_person": par["same_person"],
            "score": f"{score:.6f}" if score is not None else "",
            "det_score_a": f"{a['det_score']:.6f}" if a["det_score"] is not None else "",
            "det_score_b": f"{b['det_score']:.6f}" if b["det_score"] is not None else "",
            "notes": par["notes"],
            "pair_ok": pair_ok,
            "error_a": a["error"] or "", "error_b": b["error"] or "",
            "n_faces_a": a["n_faces"], "n_faces_b": b["n_faces"],
            "face_selection_a": a["face_selection"] or "",
            "face_selection_b": b["face_selection"] or "",
            "sha256_a": (a["sha256"] or "")[:16], "sha256_b": (b["sha256"] or "")[:16],
        })

    salida = Path(csv_out)
    salida.parent.mkdir(parents=True, exist_ok=True)
    with open(salida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(filas)

    store.close()

    resumen = {
        "csv": str(salida),
        "pares_total": len(pares),
        "pares_ok": n_ok,
        "pares_descartados": len(pares) - n_ok,
        "imagenes_unicas": len(extraidos),
        "extracciones_fallidas": sum(1 for v in extraidos.values() if not v["ok"]),
    }
    if verbose:
        print(f"[facid] escrito {salida}")
        print(f"[facid] pares utilizables: {n_ok}/{len(pares)}")
        if resumen["pares_descartados"]:
            print(f"[facid] OJO: {resumen['pares_descartados']} pares sin score "
                  "(revisa error_a / error_b en el CSV)")
    return resumen

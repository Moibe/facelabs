"""Harness de evaluacion: manifiesto de pares etiquetados -> CSV de scores.

Corre en dos pasadas separadas, igual que la arquitectura:
  1. extraccion  — cada imagen UNICA se procesa una sola vez (y se cachea)
  2. comparacion — los pares se resuelven contra los embeddings ya guardados

Por eso agregar pares nuevos a un manifiesto no reprocesa las imagenes viejas.
"""

from __future__ import annotations

import csv
import itertools
import json
import os
from pathlib import Path
from typing import Any, Callable

# (actual, total, nombre_de_archivo, etapa). Se llama desde un hilo de
# extraccion/comparacion potencialmente largo; quien lo pase debe ser rapido
# y thread-safe (el consumidor real es el endpoint /api/corrida, que solo
# actualiza un dict detras de un lock).
ProgresoCB = Callable[[int, int, str, str], None]

from .compare import compare
from .config import DEFAULT_FACE_POLICY
from .extract import extract_embedding
from .store import EmbeddingStore

# Las 7 primeras son las que pide el handoff, en ese orden. Las demas son
# auditoria: sin ellas, un par que fallo la extraccion seria una fila vacia
# sin explicacion, y calibrate.py no sabria que debe excluirlo.
COLUMNAS = [
    "img_a", "img_b", "same_person", "score", "det_score_a", "det_score_b", "notes",
    "pair_ok", "error_a", "error_b",
    "n_faces_a", "n_faces_b", "face_selection_a", "face_selection_b",
    "sha256_a", "sha256_b",
    # De donde salio cada embedding. Va al CSV porque el provider NO entra en
    # la llave de cache (cambiar de device no invalida lo guardado, y eso es
    # correcto), asi que sin estas columnas un set mezclado GPU/CPU seria
    # invisible justo en el archivo del que salen las conclusiones.
    "provider_a", "provider_b",
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


def imagenes_unicas(pares: list[dict[str, Any]]) -> list[Path]:
    """Las rutas de imagen distintas que toca un manifiesto ya cargado.

    Expuesta aparte de `_extraer_todas` porque quien reporta progreso (el
    endpoint /api/corrida) necesita el total ANTES de arrancar el hilo que
    extrae, para no mostrar una barra en 0/0 mientras carga el modelo.
    """
    rutas: list[Path] = []
    vistas: set[str] = set()
    for par in pares:
        for lado in ("img_a", "img_b"):
            s = str(par[lado])
            if s not in vistas:
                vistas.add(s)
                rutas.append(par[lado])
    return rutas


def _extraer_todas(pares, runtime, store: EmbeddingStore, face_policy: str,
                   force: bool, verbose: bool,
                   on_progreso: ProgresoCB | None = None) -> dict[str, dict[str, Any]]:
    """Pasada 1. Devuelve {ruta_str: info}, una entrada por imagen unica."""
    rutas = imagenes_unicas(pares)

    fp = runtime.fingerprint
    resultados: dict[str, dict[str, Any]] = {}

    for k, ruta in enumerate(rutas, 1):
        clave = str(ruta)
        if not ruta.is_file():
            resultados[clave] = {"ok": False, "error": "FILE_NOT_FOUND",
                                 "sha256": None, "det_score": None,
                                 "n_faces": 0, "face_selection": None,
                                 "provider": None}
            if verbose:
                print(f"  [{k}/{len(rutas)}] FILE_NOT_FOUND  {ruta}")
            if on_progreso:
                on_progreso(k, len(rutas), ruta.name, "extraccion")
            continue

        sha = store.sha_de(ruta)
        fila = None if force else store.buscar(
            sha, fp.model_pack, runtime.rec_model_sha256, fp.det_size, face_policy)

        if fila is not None:
            resultados[clave] = {
                "ok": True, "error": None, "sha256": sha,
                "embedding": store.cargar_embedding(fila),
                "det_score": float(fila["det_score"]),
                "n_faces": int(fila["n_faces_detected"]),
                "face_selection": fila["face_selection"],
                # El provider con el que se calculo ORIGINALMENTE, no el de
                # esta corrida: es lo que describe al embedding que se reusa.
                "provider": fila["provider"],
            }
            if verbose:
                print(f"  [{k}/{len(rutas)}] cache          {ruta.name}")
        elif not force and (fallo_cacheado := store.buscar_fallo(
                sha, fp.model_pack, runtime.rec_model_sha256, fp.det_size, face_policy)):
            # Ya sabemos que esto falla bajo esta config exacta (mismo modelo,
            # det_size y face_policy compatible): no vale la pena volver a
            # correr el detector para llegar al mismo resultado.
            resultados[clave] = {
                "ok": False, "error": fallo_cacheado["error"], "sha256": sha,
                "det_score": None, "n_faces": fallo_cacheado["n_faces_detected"],
                "face_selection": None, "provider": None,
            }
            if verbose:
                print(f"  [{k}/{len(rutas)}] cache-fallo    {fallo_cacheado['error']}  {ruta.name}")
        else:
            r = extract_embedding(ruta, runtime, face_policy=face_policy)
            if r["error"] is None:
                store.guardar(r, runtime, face_policy)
                resultados[clave] = {
                    "ok": True, "error": None, "sha256": r["image_sha256"],
                    "embedding": r["embedding"], "det_score": r["det_score"],
                    "n_faces": r["n_faces_detected"],
                    "face_selection": r["face_selection"],
                    "provider": runtime.provider_activo,
                }
                if verbose:
                    print(f"  [{k}/{len(rutas)}] ok det={r['det_score']:.3f}  {ruta.name}")
            else:
                store.registrar_fallo(r, runtime, face_policy)
                resultados[clave] = {
                    "ok": False, "error": r["error"], "sha256": r["image_sha256"],
                    "det_score": r["det_score"], "n_faces": r["n_faces_detected"],
                    "face_selection": r["face_selection"],
                    "provider": runtime.provider_activo,
                }
                if verbose:
                    print(f"  [{k}/{len(rutas)}] {r['error']}  {ruta.name}"
                          f"  (rostros={r['n_faces_detected']})")

        if on_progreso:
            on_progreso(k, len(rutas), ruta.name, "extraccion")

    return resultados


def run_manifest(manifest_path: str | Path, runtime, csv_out: str | Path, *,
                 face_policy: str = DEFAULT_FACE_POLICY, force: bool = False,
                 verbose: bool = True,
                 on_progreso: ProgresoCB | None = None) -> dict[str, Any]:
    """Corre el manifiesto completo y escribe el CSV. Devuelve un resumen."""
    pares = cargar_manifiesto(manifest_path)
    store = EmbeddingStore()

    if verbose:
        print(f"[facid] manifiesto: {len(pares)} pares")
        print("[facid] pasada 1/2 — extraccion de embeddings")

    extraidos = _extraer_todas(pares, runtime, store, face_policy, force, verbose,
                               on_progreso=on_progreso)

    if verbose:
        print("[facid] pasada 2/2 — comparacion de pares")
    if on_progreso:
        # La comparacion es coseno entre vectores ya extraidos: incluso miles
        # de pares se resuelven en el orden de un segundo, asi que no amerita
        # progreso por par. Un solo aviso de cambio de etapa alcanza para que
        # la barra no se vea "atorada" en el ultimo % de la extraccion.
        on_progreso(len(pares), len(pares), "", "comparacion")

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
            "provider_a": a.get("provider") or "", "provider_b": b.get("provider") or "",
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


# ---------------------------------------------------------------------------
# Generacion del manifiesto a partir de la estructura de carpetas
# ---------------------------------------------------------------------------
EXTENSIONES = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

MODO_ANCLA = "ancla"
MODO_TODOS = "todos"


def descubrir_personas(data_dir: str | Path) -> dict[str, list[Path]]:
    """Una subcarpeta de `data_dir` = una persona. Ignora archivos sueltos.

    Es la regla que hace util la organizacion en carpetas: la identidad la da
    la carpeta, no el nombre del archivo. Ordenado alfabeticamente para que
    correr esto dos veces produzca el mismo manifiesto.
    """
    raiz = Path(data_dir)
    if not raiz.is_dir():
        raise ManifestError(f"No es un directorio: {raiz}")

    personas: dict[str, list[Path]] = {}
    for sub in sorted(p for p in raiz.iterdir() if p.is_dir()):
        fotos = sorted(f for f in sub.iterdir()
                       if f.is_file() and f.suffix.lower() in EXTENSIONES)
        if fotos:
            personas[sub.name] = fotos
    return personas


def _nota_de(path: Path) -> str:
    """Deriva una nota legible del nombre del archivo: '03_luz_distinta' -> 'luz distinta'."""
    stem = path.stem
    partes = stem.split("_")
    if partes and partes[0].isdigit():
        partes = partes[1:]
    return " ".join(partes) if partes else stem


def _filtrar_excluidas(personas: dict[str, list[Path]], data_dir: str | Path,
                       excluir: set[str]) -> dict[str, list[Path]]:
    """Quita del descubrimiento las fotos marcadas como excluidas.

    `excluir` son rutas relativas a `data_dir` en POSIX (el mismo formato que
    devuelve /api/personas), no rutas absolutas ni relativas al manifiesto:
    asi el front no tiene que saber nada de donde vive el manifiesto para
    marcar una foto.
    """
    raiz = Path(data_dir).resolve()
    filtradas: dict[str, list[Path]] = {}
    for nombre, fotos in personas.items():
        quedan = [f for f in fotos if f.resolve().relative_to(raiz).as_posix() not in excluir]
        if quedan:
            filtradas[nombre] = quedan
    return filtradas


def init_manifest(data_dir: str | Path, salida: str | Path, *,
                  modo: str = MODO_ANCLA, excluir: set[str] | None = None,
                  verbose: bool = True) -> dict[str, Any]:
    """Escribe un manifiesto deduciendo las etiquetas de las carpetas.

    misma carpeta  -> same_person: true
    carpeta distinta -> same_person: false

    modo 'ancla' (default): dentro de cada persona, la primera foto es el ancla
        y se compara contra las demas; los non-match son las anclas entre si.
        Es el diseno del handoff: pocos pares, dependencia acotada.
    modo 'todos': todas las combinaciones posibles. Da muchos mas pares SIN
        agregar informacion nueva (salen de las mismas fotos), asi que estrecha
        los intervalos de confianza sin justificarlo. Usalo para explorar, no
        para reportar un threshold.

    `excluir`: fotos (ruta relativa a data_dir, POSIX) que se ignoran como si
    no estuvieran en data/. Pensado para probar subconjuntos sin mover ni
    borrar archivos — ver el picker de El set en el front.
    """
    if modo not in (MODO_ANCLA, MODO_TODOS):
        raise ManifestError(f"modo invalido: {modo!r} (usa '{MODO_ANCLA}' o '{MODO_TODOS}')")

    personas = descubrir_personas(data_dir)
    if excluir:
        personas = _filtrar_excluidas(personas, data_dir, excluir)
    if not personas:
        raise ManifestError(
            f"No encontre ninguna subcarpeta con imagenes en {data_dir}"
            + (" despues de aplicar las exclusiones" if excluir else "") + ". "
            f"Esperaba algo como {data_dir}/<persona>/foto.jpg "
            f"(extensiones: {', '.join(EXTENSIONES)})")

    salida_path = Path(salida)
    base = salida_path.parent.resolve()

    def rel(p: Path) -> str:
        # Las rutas del manifiesto se resuelven relativas al propio manifiesto;
        # se fuerzan a '/' para que el archivo sirva igual en Windows y Linux.
        try:
            return os.path.relpath(p.resolve(), base).replace(os.sep, "/")
        except ValueError:
            return p.resolve().as_posix()

    pares: list[dict[str, Any]] = []

    # --- match: dentro de cada persona ---
    for nombre, fotos in personas.items():
        if modo == MODO_ANCLA:
            ancla = fotos[0]
            combos = [(ancla, f) for f in fotos[1:]]
        else:
            combos = list(itertools.combinations(fotos, 2))
        for a, b in combos:
            pares.append({"img_a": rel(a), "img_b": rel(b), "same_person": True,
                          "notes": _nota_de(b) if modo == MODO_ANCLA
                          else f"{_nota_de(a)} vs {_nota_de(b)}"})

    # --- non-match: entre personas distintas ---
    for (na, fa), (nb, fb) in itertools.combinations(personas.items(), 2):
        if modo == MODO_ANCLA:
            combos = [(fa[0], fb[0])]
        else:
            combos = [(a, b) for a in fa for b in fb]
        for a, b in combos:
            pares.append({"img_a": rel(a), "img_b": rel(b), "same_person": False,
                          "notes": f"{na} vs {nb} — ¿se parecen?"})

    salida_path.parent.mkdir(parents=True, exist_ok=True)
    salida_path.write_text(json.dumps(pares, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")

    n_match = sum(1 for p in pares if p["same_person"])
    resumen = {
        "salida": str(salida_path), "modo": modo,
        "personas": {k: len(v) for k, v in personas.items()},
        "n_personas": len(personas), "n_fotos": sum(len(v) for v in personas.values()),
        "pares": len(pares), "match": n_match, "nonmatch": len(pares) - n_match,
        "sin_pares_match": [k for k, v in personas.items() if len(v) < 2],
    }

    if verbose:
        print(f"[facid] {resumen['n_personas']} personas, {resumen['n_fotos']} fotos")
        for k, v in resumen["personas"].items():
            print(f"          {k:<16} {v} foto(s)")
        print(f"[facid] modo '{modo}': {len(pares)} pares "
              f"({n_match} match, {len(pares) - n_match} non-match)")
        print(f"[facid] escrito {salida_path}")
        if resumen["sin_pares_match"]:
            print(f"[facid] sin pares match (1 sola foto): "
                  f"{', '.join(resumen['sin_pares_match'])}")
        if len(personas) < 2:
            print("[facid] OJO: con una sola persona no hay pares non-match y no se")
            print("        puede calibrar nada. Necesitas al menos dos personas.")
        n_nm = len(pares) - n_match
        if n_nm:
            print()
            print(f"[facid] Con {n_nm} pares non-match, la FMR mas chica que vas a poder")
            print(f"        MEDIR es 1/{n_nm} = {1/n_nm:.0%}. Si necesitas resolucion mas fina,")
            print(f"        lo que hace falta son mas PERSONAS, no mas pares: agregar pares")
            print(f"        sacados de estas mismas {len(personas)} fotos-personas estrecha los")
            print("        intervalos sin agregar informacion (--modo todos hace justo eso).")
        print()
        print("Ahora abre el archivo y corrige las notas: son lo unico que despues")
        print("te va a explicar por que un par salio con score bajo.")
    return resumen

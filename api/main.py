"""Capa HTTP delgada sobre facid, para que el front pueda ver los resultados.

REGLA DE DEPENDENCIA, no negociable: este modulo importa `facid`; `facid` nunca
importa esto. La CLI no sabe que el API existe y sigue funcionando igual con el
API apagado, desinstalado o borrado. Aqui no vive ni una linea de logica de
calibracion: todo lo que se responde sale de llamar a las MISMAS funciones que
usa la CLI.

Consecuencia practica: si un numero del front no cuadra con el de la terminal,
es un bug de serializacion, nunca dos implementaciones que se separaron.

Solo lectura salvo dos POST explicitos (generar manifiesto / correr una corrida).
Escucha en 127.0.0.1 y nada mas: esto procesa fotos de personas que dieron
consentimiento para un experimento, no para exponer un puerto.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from fastapi import FastAPI, HTTPException, Query          # noqa: E402
from fastapi.middleware.cors import CORSMiddleware          # noqa: E402
from fastapi.responses import FileResponse                  # noqa: E402
from pydantic import BaseModel                              # noqa: E402

import facid                                                # noqa: E402
from facid import calibrate as cal                          # noqa: E402
from facid.config import DATA_DIR, OUT_DIR                   # noqa: E402
from facid.dependencia import estructura, jackknife_por_persona  # noqa: E402
from facid.harness import (                                 # noqa: E402
    ManifestError, descubrir_personas, init_manifest,
)

app = FastAPI(
    title="facid api",
    version=facid.__version__,
    description="Capa de lectura sobre el PoC. La CLI es la fuente de verdad.",
)

# El dev server de Vite corre en otro puerto, y NO siempre en el mismo: si el
# 5173 esta ocupado se mueve al 5174, 5175... Con una lista fija de origenes, ese
# corrimiento hace que el browser bloquee cada llamada por CORS, y en pantalla se
# ve igual que "el API esta apagada" — un rato perdido persiguiendo el error
# equivocado. Por eso se cubre el rango con regex en vez de enumerar puertos.
#
# Solo localhost/127.0.0.1: una pagina remota nunca tiene ese origen, asi que no
# puede llegarle a este API aunque conozca el puerto.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):(41[7-9]\d|51[7-9]\d)$",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

EXT_IMG = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# ---------------------------------------------------------------------------
# Rutas: todo lo que viene del cliente se ancla dentro del repo. Sin esto,
# ?ruta=../../../etc/passwd seria un lector de archivos arbitrario, y "es local"
# no es una defensa.
# ---------------------------------------------------------------------------
def _dentro(base: Path, rel: str) -> Path:
    destino = (base / rel).resolve()
    if not destino.is_relative_to(base.resolve()):
        raise HTTPException(400, f"ruta fuera de {base.name}/: {rel!r}")
    return destino


def _resolver_foto(raw: str) -> str | None:
    """Convierte la ruta del CSV en una ruta relativa al repo, o None.

    El CSV guarda la ruta tal como venia en el manifiesto, o sea relativa a la
    CARPETA DEL MANIFIESTO ('../data/yo/01.jpg'), no al repo. Se prueban las
    bases plausibles y se devuelve la primera que exista; si no cae dentro de
    data/, se devuelve None y el front pinta un placeholder en vez de que el
    API se convierta en un servidor de archivos arbitrarios.
    """
    if not raw:
        return None
    raiz = DATA_DIR.resolve()
    # Las bases se derivan de DATA_DIR, no de REPO: si data/ esta apuntada a otro
    # lado con FACID_DATA, '../data/x.jpg' sigue resolviendo desde el hermano
    # correcto de esa carpeta y no desde el repo.
    bases = [
        DATA_DIR.parent / "manifests",   # el caso normal: '../data/yo/01.jpg'
        DATA_DIR.parent,                 # 'data/yo/01.jpg'
        DATA_DIR,                        # 'yo/01.jpg'
        REPO / "manifests", REPO,
    ]
    for base in bases:
        try:
            p = (base / raw).resolve()
        except (OSError, ValueError):
            continue
        if p.is_file() and p.is_relative_to(raiz):
            return p.relative_to(raiz).as_posix()

    # Ultimo recurso: quedarse con la cola despues del ultimo 'data/'. Cubre
    # manifiestos con rutas absolutas de otra maquina, que es justo el caso de
    # extraer en la Ubuntu y analizar en otra laptop.
    norm = raw.replace("\\", "/")
    marca = f"/{DATA_DIR.name}/"
    if marca in norm:
        cola = norm.rsplit(marca, 1)[1]
        p = (raiz / cola).resolve()
        if p.is_file() and p.is_relative_to(raiz):
            return p.relative_to(raiz).as_posix()
    return None


def _persona_de(raw: str) -> str:
    """La carpeta es la persona, igual que en init-manifest y en dependencia.py."""
    partes = raw.replace("\\", "/").rstrip("/").split("/")
    return partes[-2] if len(partes) >= 2 else ""


# ---------------------------------------------------------------------------
# Salud y version (dev.mjs sondea estos dos)
# ---------------------------------------------------------------------------
@app.get("/")
def raiz() -> dict[str, Any]:
    return {"name": "facid api", "version": facid.__version__}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# El set: personas, fotos y de donde salen
# ---------------------------------------------------------------------------
@app.get("/api/personas")
def personas() -> dict[str, Any]:
    """Lo que hay en data/, sin tocar el modelo."""
    try:
        encontradas = descubrir_personas(DATA_DIR)
    except ManifestError:
        encontradas = {}

    salida = []
    for nombre, fotos in encontradas.items():
        salida.append({
            "persona": nombre,
            "n_fotos": len(fotos),
            "fotos": [
                {
                    "ruta": f.relative_to(DATA_DIR).as_posix(),
                    "nombre": f.name,
                    "bytes": f.stat().st_size,
                }
                for f in fotos
            ],
        })
    return {
        "data_dir": str(DATA_DIR),
        "n_personas": len(salida),
        "n_fotos": sum(p["n_fotos"] for p in salida),
        "personas": salida,
    }


@app.get("/api/foto")
def foto(ruta: str = Query(..., description="Ruta relativa a data/")):
    """Sirve una imagen de data/. Es lo que permite ver la cara junto al score."""
    destino = _dentro(DATA_DIR, ruta)
    if not destino.is_file():
        raise HTTPException(404, f"no existe: {ruta}")
    if destino.suffix.lower() not in EXT_IMG:
        raise HTTPException(415, f"no es una imagen soportada: {destino.suffix}")
    return FileResponse(destino)


@app.get("/api/csvs")
def csvs() -> dict[str, Any]:
    """Los CSV de scores disponibles en out/, para que el front ofrezca cual abrir."""
    if not OUT_DIR.is_dir():
        return {"out_dir": str(OUT_DIR), "csvs": []}
    encontrados = sorted(OUT_DIR.glob("*.csv"), key=lambda p: -p.stat().st_mtime)
    return {
        "out_dir": str(OUT_DIR),
        "csvs": [
            {"ruta": p.name, "bytes": p.stat().st_size, "mtime": p.stat().st_mtime}
            for p in encontrados
            # sweep_fmr_fnmr.csv es una SALIDA del analisis, no una entrada.
            if not p.name.startswith("sweep_")
        ],
    }


# ---------------------------------------------------------------------------
# El analisis. Se arma llamando a las primitivas de calibrate.py — las mismas
# que usa la CLI — en vez de a calibrar(), porque calibrar() escribe archivos
# (png, sweep csv, reporte) y un GET no debe tener efectos secundarios.
# ---------------------------------------------------------------------------
@app.get("/api/resultados")
def resultados(csv: str = "scores.csv", paso: float = 0.01) -> dict[str, Any]:
    ruta = _dentro(OUT_DIR, csv)
    if not ruta.is_file():
        raise HTTPException(404, f"no existe el CSV: {csv}")

    datos = cal.cargar_scores(ruta)
    m, nm = datos["match"], datos["nonmatch"]

    def fila_par(f: dict, es_match: bool) -> dict[str, Any]:
        a_raw, b_raw = f.get("img_a", ""), f.get("img_b", "")
        return {
            "img_a": a_raw, "img_b": b_raw,
            "foto_a": _resolver_foto(a_raw), "foto_b": _resolver_foto(b_raw),
            "persona_a": _persona_de(a_raw), "persona_b": _persona_de(b_raw),
            "same_person": es_match,
            "score": float(f["score"]) if (f.get("score") or "").strip() else None,
            "det_score_a": float(f["det_score_a"]) if (f.get("det_score_a") or "").strip() else None,
            "det_score_b": float(f["det_score_b"]) if (f.get("det_score_b") or "").strip() else None,
            "notes": f.get("notes", ""),
            "provider_a": f.get("provider_a", ""), "provider_b": f.get("provider_b", ""),
            "n_faces_a": f.get("n_faces_a", ""), "n_faces_b": f.get("n_faces_b", ""),
            "face_selection_a": f.get("face_selection_a", ""),
            "face_selection_b": f.get("face_selection_b", ""),
            "error_a": f.get("error_a", ""), "error_b": f.get("error_b", ""),
        }

    pares = ([fila_par(f, True) for f in datos["filas_match"]]
             + [fila_par(f, False) for f in datos["filas_nonmatch"]])
    descartados = [
        {
            "img_a": f.get("img_a", ""), "img_b": f.get("img_b", ""),
            "foto_a": _resolver_foto(f.get("img_a", "")),
            "foto_b": _resolver_foto(f.get("img_b", "")),
            "same_person": str(f.get("same_person", "")).strip().lower() in ("true", "1", "yes"),
            "error_a": f.get("error_a", ""), "error_b": f.get("error_b", ""),
            "n_faces_a": f.get("n_faces_a", ""), "n_faces_b": f.get("n_faces_b", ""),
            "notes": f.get("notes", ""),
        }
        for f in datos["descartados"]
    ]

    base: dict[str, Any] = {
        "csv": csv,
        "match": cal.describir(m),
        "nonmatch": cal.describir(nm),
        "pares": pares,
        "descartados": descartados,
        "providers": cal._providers_usados(datos),
    }

    if m.size == 0 or nm.size == 0:
        return {
            **base, "ok": False,
            "motivo": "Hacen falta pares de ambas clases (match y non-match) para calibrar.",
        }

    est = estructura(datos["filas_match"], datos["filas_nonmatch"])
    e = cal.eer(m, nm)

    puntos = []
    for obj in (0.10, 0.05, 0.01, 0.0):
        r = cal.punto_operacion(m, nm, objetivo_fmr=obj)
        if r:
            puntos.append({**r, "tipo": "fmr", "objetivo_valor": obj})
    for obj in (0.10, 0.05, 0.0):
        r = cal.punto_operacion(m, nm, objetivo_fnmr=obj)
        if r:
            puntos.append({**r, "tipo": "fnmr", "objetivo_valor": obj})

    return {
        **base,
        "ok": True,
        "traslape": cal.zona_traslape(m, nm),
        "d_prime": cal.d_prime(m, nm),
        "eer": e,
        "puntos_operacion": puntos,
        "barrido": cal.barrido(m, nm, 0.0, 1.0, paso),
        "composicion": {
            "n_pares": est["n_pares"], "n_imagenes": est["n_imagenes"],
            "n_identidades": est["n_identidades"], "reuso_max": est["reuso_max"],
            "reuso_medio": est["reuso_medio"], "img_mas_usada": est["img_mas_usada"],
            "contradicciones": est["contradicciones"],
        },
        "fragilidad": jackknife_por_persona(
            datos["filas_match"], datos["filas_nonmatch"], est),
        "resolucion": {
            "fmr_minima_medible": 1.0 / nm.size,
            "fnmr_minima_medible": 1.0 / m.size,
        },
    }


@app.get("/api/tasas")
def tasas(csv: str = "scores.csv", threshold: float = 0.5) -> dict[str, Any]:
    """FMR/FNMR en UN threshold. Es lo que mueve el slider del front.

    Existe aparte del barrido para que el front pueda pedir un valor exacto en
    vez de interpolar entre pasos de 0.01 y mostrar un numero que no es el real.
    """
    ruta = _dentro(OUT_DIR, csv)
    if not ruta.is_file():
        raise HTTPException(404, f"no existe el CSV: {csv}")
    if not -1.0 <= threshold <= 1.0:
        raise HTTPException(400, "threshold fuera de [-1, 1]")
    datos = cal.cargar_scores(ruta)
    if datos["match"].size == 0 or datos["nonmatch"].size == 0:
        raise HTTPException(409, "hacen falta pares de ambas clases")
    return cal.fmr_fnmr(datos["match"], datos["nonmatch"], threshold)


# ---------------------------------------------------------------------------
# Entorno: el equivalente de `facid doctor`. Carga el modelo, asi que puede
# tardar o fallar; se responde el fallo como dato en vez de un 500 opaco.
# ---------------------------------------------------------------------------
@app.get("/api/entorno")
def entorno(device: str = "cuda", allow_cpu_fallback: bool = True) -> dict[str, Any]:
    try:
        from facid.runtime import load_runtime
        rt = load_runtime(device=device,
                          require_gpu=False if allow_cpu_fallback else None,
                          verbose=False)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "pista": "Corre ./setup.sh en la maquina con GPU, o usa device=cpu.",
        }
    fp = rt.fingerprint
    return {
        "ok": True,
        "provider_activo": rt.provider_activo,
        "en_gpu": "CUDA" in rt.provider_activo,
        "rec_model_file": rt.rec_model_file,
        "rec_model_sha256": rt.rec_model_sha256,
        "det_model_file": rt.det_model_file,
        "det_model_sha256": rt.det_model_sha256,
        **fp.as_dict(),
    }


@app.get("/api/store")
def store_resumen() -> dict[str, Any]:
    from facid.store import EmbeddingStore
    with EmbeddingStore() as st:
        return st.resumen()


# ---------------------------------------------------------------------------
# Las dos unicas acciones. Envuelven la misma funcion que la CLI.
# ---------------------------------------------------------------------------
class PeticionManifiesto(BaseModel):
    salida: str = "mi_set.json"
    modo: str = "ancla"


@app.post("/api/manifiesto")
def crear_manifiesto(p: PeticionManifiesto) -> dict[str, Any]:
    destino = _dentro(REPO / "manifests", p.salida)
    try:
        return init_manifest(DATA_DIR, destino, modo=p.modo, verbose=False)
    except ManifestError as exc:
        raise HTTPException(400, str(exc)) from exc


class PeticionCorrida(BaseModel):
    manifiesto: str = "mi_set.json"
    salida_csv: str = "scores.csv"
    device: str = "cuda"
    face_policy: str = "strict"
    allow_cpu_fallback: bool = True
    force: bool = False


@app.post("/api/corrida")
def correr(p: PeticionCorrida) -> dict[str, Any]:
    """Extrae y compara. Es lo unico que carga el modelo y puede tardar."""
    manifiesto = _dentro(REPO / "manifests", p.manifiesto)
    csv_out = _dentro(OUT_DIR, p.salida_csv)
    if not manifiesto.is_file():
        raise HTTPException(404, f"no existe el manifiesto: {p.manifiesto}")
    try:
        from facid.harness import run_manifest
        from facid.runtime import load_runtime
        rt = load_runtime(device=p.device,
                          require_gpu=False if p.allow_cpu_fallback else None,
                          verbose=False)
        return run_manifest(manifiesto, rt, csv_out, face_policy=p.face_policy,
                            force=p.force, verbose=False)
    except ManifestError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc

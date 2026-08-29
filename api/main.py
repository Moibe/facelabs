"""Capa HTTP delgada sobre facid, para que el front pueda ver los resultados.

REGLA DE DEPENDENCIA, no negociable: este modulo importa `facid`; `facid` nunca
importa esto. La CLI no sabe que el API existe y sigue funcionando igual con el
API apagado, desinstalado o borrado. Aqui no vive ni una linea de logica de
calibracion: todo lo que se responde sale de llamar a las MISMAS funciones que
usa la CLI.

Consecuencia practica: si un numero del front no cuadra con el de la terminal,
es un bug de serializacion, nunca dos implementaciones que se separaron.

Solo lectura salvo siete POST explicitos (generar manifiesto / correr una
corrida / mover una foto entre personas / subir fotos / indexar un corpus
externo / detener esa indexacion / buscar contra ese corpus). Escucha en
127.0.0.1 y nada mas: esto procesa fotos de personas que dieron
consentimiento para un experimento, no para exponer un puerto.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware          # noqa: E402
from fastapi.responses import FileResponse                  # noqa: E402
from pydantic import BaseModel                              # noqa: E402

import facid                                                # noqa: E402
from facid import calibrate as cal                          # noqa: E402
from facid.config import CORPUS_DIR, DATA_DIR, OUT_DIR        # noqa: E402
from facid.dependencia import estructura, jackknife_por_persona  # noqa: E402
from facid.historial import HistorialStore                     # noqa: E402
from facid.harness import (                                 # noqa: E402
    ManifestError, cargar_manifiesto, descubrir_personas, imagenes_unicas,
    init_manifest,
)

app = FastAPI(
    title="facid api",
    version=facid.__version__,
    description="Capa de lectura sobre el PoC. La CLI es la fuente de verdad.",
)

# El dev server de Vite corre en otro puerto, y NO siempre en el mismo: si el
# configurado esta ocupado se mueve al siguiente... Con una lista fija de
# origenes, ese corrimiento hace que el browser bloquee cada llamada por CORS,
# y en pantalla se ve igual que "el API esta apagada" — un rato perdido
# persiguiendo el error equivocado. Por eso se cubre el rango con regex en vez
# de enumerar puertos. 10xx es el puerto fijo de este proyecto (ver
# web/vite.config.ts); 41xx/51xx son los defaults de Vite/preview por si
# alguien corre `vite dev` suelto sin el config.
#
# Solo localhost/127.0.0.1: una pagina remota nunca tiene ese origen, asi que no
# puede llegarle a este API aunque conozca el puerto.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):(41[7-9]\d|51[7-9]\d|10\d\d)$",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# El tipo se declara a mano y no se deja a `mimetypes`: en Windows .webp no esta
# registrado y sale como application/octet-stream, que el browser puede negarse a
# pintar dentro de un <img>.
TIPO_IMG = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}
EXT_IMG = set(TIPO_IMG)


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
    tipo = TIPO_IMG.get(destino.suffix.lower())
    if tipo is None:
        raise HTTPException(415, f"no es una imagen soportada: {destino.suffix}")
    return FileResponse(destino, media_type=tipo)


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
    # Rutas relativas a data/ (formato de /api/personas) a tratar como si no
    # estuvieran: el picker de fotos de "El set" en el front.
    excluir: list[str] = []


@app.post("/api/manifiesto")
def crear_manifiesto(p: PeticionManifiesto) -> dict[str, Any]:
    destino = _dentro(REPO / "manifests", p.salida)
    try:
        return init_manifest(DATA_DIR, destino, modo=p.modo,
                             excluir=set(p.excluir) or None, verbose=False)
    except ManifestError as exc:
        raise HTTPException(400, str(exc)) from exc


class PeticionCorrida(BaseModel):
    manifiesto: str = "mi_set.json"
    salida_csv: str = "scores.csv"
    device: str = "cuda"
    face_policy: str = "strict"
    allow_cpu_fallback: bool = True
    force: bool = False


# La corrida real (cargar ~300 MB de ONNX y extraer cada foto) puede tardar
# minutos con sets grandes, y un solo POST bloqueante no tiene forma de avisar
# progreso mientras tanto. Se lanza en un hilo aparte; este dict, detras de un
# lock, es el unico canal entre ese hilo y los GET que preguntan como va. Un
# dict global alcanza porque el uso real es UN usuario local corriendo UNA
# cosa a la vez, no una cola de trabajos concurrentes.
_corrida_lock = threading.Lock()
_corrida_estado: dict[str, Any] = {
    "en_curso": False,
    "etapa": "",  # "cargando_modelo" | "extraccion" | "comparacion" | ""
    "actual": 0,
    "total": 0,
    "archivo": "",
    "resultado": None,
    "error": None,
}


@app.post("/api/corrida")
def correr(p: PeticionCorrida) -> dict[str, Any]:
    """Arranca la corrida en segundo plano. Sondea /api/corrida/estado para el avance."""
    with _corrida_lock:
        if _corrida_estado["en_curso"]:
            raise HTTPException(409, "Ya hay una corrida en curso.")

    manifiesto = _dentro(REPO / "manifests", p.manifiesto)
    csv_out = _dentro(OUT_DIR, p.salida_csv)
    if not manifiesto.is_file():
        raise HTTPException(404, f"no existe el manifiesto: {p.manifiesto}")
    try:
        pares = cargar_manifiesto(manifiesto)
    except ManifestError as exc:
        raise HTTPException(400, str(exc)) from exc

    with _corrida_lock:
        _corrida_estado.update({
            "en_curso": True, "etapa": "cargando_modelo",
            "actual": 0, "total": len(imagenes_unicas(pares)),
            "archivo": "", "resultado": None, "error": None,
        })

    def _reportar(actual: int, total: int, archivo: str, etapa: str) -> None:
        with _corrida_lock:
            _corrida_estado.update(
                {"etapa": etapa, "actual": actual, "total": total, "archivo": archivo})

    def _trabajo() -> None:
        try:
            from facid.harness import run_manifest
            from facid.runtime import load_runtime
            rt = load_runtime(device=p.device,
                              require_gpu=False if p.allow_cpu_fallback else None,
                              verbose=False)
            resultado = run_manifest(manifiesto, rt, csv_out, face_policy=p.face_policy,
                                     force=p.force, verbose=False, on_progreso=_reportar)
            with _corrida_lock:
                _corrida_estado["resultado"] = resultado
        except Exception as exc:
            with _corrida_lock:
                _corrida_estado["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            with _corrida_lock:
                _corrida_estado["en_curso"] = False

    threading.Thread(target=_trabajo, daemon=True).start()
    with _corrida_lock:
        return dict(_corrida_estado)


@app.get("/api/corrida/estado")
def corrida_estado() -> dict[str, Any]:
    """Lo que el front sondea para pintar la barra de progreso."""
    with _corrida_lock:
        return dict(_corrida_estado)


class PeticionMoverFoto(BaseModel):
    ruta: str              # relativa a data/, ej "otra_1/LauTokyo.png"
    persona_destino: str   # nombre de carpeta, ej "yo" — NO una ruta


@app.post("/api/mover-foto")
def mover_foto(p: PeticionMoverFoto) -> dict[str, Any]:
    """Reclasifica una foto arrastrandola de una persona a otra en el front.

    Es un rename real dentro de data/, no una preferencia de browser como el
    picker o el recorte: el nombre de la carpeta ES la identidad (ver
    descubrir_personas), asi que "mover" una foto a otra persona significa
    literalmente moverla de carpeta.
    """
    if "/" in p.persona_destino or "\\" in p.persona_destino or p.persona_destino in ("", ".", ".."):
        raise HTTPException(400, f"nombre de persona invalido: {p.persona_destino!r}")

    origen = _dentro(DATA_DIR, p.ruta)
    if not origen.is_file():
        raise HTTPException(404, f"no existe: {p.ruta}")

    destino_dir = _dentro(DATA_DIR, p.persona_destino)
    destino = destino_dir / origen.name

    if destino.resolve() == origen.resolve():
        return {"movido": False, "motivo": "ya esta en esa persona"}
    # No se pisa un archivo existente: mejor fallar explicito que perder una
    # foto porque dos personas usaban el mismo nombre de archivo.
    if destino.exists():
        raise HTTPException(
            409, f"ya existe {origen.name!r} en {p.persona_destino}/ — renombra una de las dos")

    destino_dir.mkdir(parents=True, exist_ok=True)
    origen.rename(destino)
    nueva_ruta = destino.relative_to(DATA_DIR.resolve()).as_posix()
    return {"movido": True, "de": p.ruta, "a": nueva_ruta}


@app.post("/api/subir-fotos")
async def subir_fotos(
    persona: str = Form(...),
    archivos: list[UploadFile] = File(...),
) -> dict[str, Any]:
    """El "dropbox" de Run: guarda fotos subidas desde el browser en
    data/<persona>/. Mismo destino que usa Labs — una carpeta es una persona
    en todo el proyecto, sin convencion aparte solo para Run.
    """
    if "/" in persona or "\\" in persona or persona in ("", ".", ".."):
        raise HTTPException(400, f"nombre de persona invalido: {persona!r}")

    destino_dir = _dentro(DATA_DIR, persona)
    destino_dir.mkdir(parents=True, exist_ok=True)

    guardadas = []
    for archivo in archivos:
        nombre = Path(archivo.filename or "").name  # descarta cualquier ruta
        ext = Path(nombre).suffix.lower()
        if ext not in EXT_IMG:
            raise HTTPException(415, f"no es una imagen soportada: {nombre!r}")
        stem = Path(nombre).stem
        destino = destino_dir / nombre
        # Sufijo numerico en colision, no un rechazo: es un dropbox de
        # subida, no un rename explicito — preservar las tres fotos que
        # arrastraste importa mas que el nombre exacto de cada una.
        n = 1
        while destino.exists():
            n += 1
            destino = destino_dir / f"{stem}_{n}{ext}"
        destino.write_bytes(await archivo.read())
        guardadas.append(destino.relative_to(DATA_DIR.resolve()).as_posix())

    return {"persona": persona, "guardadas": guardadas}


# ---------------------------------------------------------------------------
# Run: busqueda 1:N contra un corpus externo grande (ver facid/busqueda.py).
# Distinto de Labs: ahi se calibra un threshold sobre pares CON etiqueta
# conocida; aqui se busca si una persona de referencia aparece en un corpus
# enorme y sin clasificar. El corpus nunca se copia ni se sirve mas que de
# solo lectura (ver /api/corpus/foto, sandboxeado igual que /api/foto).
# ---------------------------------------------------------------------------
@app.get("/api/corpus/resumen")
def corpus_resumen() -> dict[str, Any]:
    """Conteo BARATO (solo carpetas de primer nivel): el corpus puede tener
    decenas de miles de fotos, y contarlas todas en cada GET seria lento.
    El conteo de fotos real aparece al indexar (ahi ya hay que recorrerlas)."""
    if not CORPUS_DIR.is_dir():
        return {"existe": False, "corpus_dir": str(CORPUS_DIR), "n_carpetas": 0}
    n_carpetas = sum(1 for p in CORPUS_DIR.iterdir() if p.is_dir())
    return {"existe": True, "corpus_dir": str(CORPUS_DIR), "n_carpetas": n_carpetas}


@app.get("/api/corpus/foto")
def corpus_foto(ruta: str = Query(..., description="Ruta relativa al corpus")):
    destino = _dentro(CORPUS_DIR, ruta)
    if not destino.is_file():
        raise HTTPException(404, f"no existe: {ruta}")
    tipo = TIPO_IMG.get(destino.suffix.lower())
    if tipo is None:
        raise HTTPException(415, f"no es una imagen soportada: {destino.suffix}")
    return FileResponse(destino, media_type=tipo)


class PeticionIndexarCorpus(BaseModel):
    limite_carpetas: int | None = None
    limite_por_carpeta: int | None = None
    device: str = "cuda"
    face_policy: str = "strict"
    allow_cpu_fallback: bool = True


# Mismo patron que _corrida_lock/_corrida_estado: hilo aparte + dict con lock
# para que /api/corpus/indexar/estado pueda sondear avance sobre un corpus
# que puede tardar horas. Job independiente de _corrida_estado a proposito
# — indexar el corpus y correr un manifiesto de Labs son cosas distintas que
# no tiene sentido bloquearse mutuamente.
_indexar_lock = threading.Lock()
_indexar_estado: dict[str, Any] = {
    "en_curso": False,
    "etapa": "",  # cargando_modelo | explorando | indexando | pausado | ""
    "actual": 0,
    "total": 0,
    "archivo": "",
    # Desglose del avance: sin esto, el contador corriendo de 1 a 85k se ve
    # igual esté saltando cache o extrayendo de verdad. Y de lo nuevo, cuanto
    # salio con rostro y cuanto no.
    "en_cache": 0,
    "nuevas": 0,
    "nuevas_ok": 0,
    "nuevas_fallidas": 0,
    "resultado": None,
    "error": None,
}

# Indexar y buscar compiten por el mismo CPU (ambos usan el modelo de
# reconocimiento). "set" = indexar puede avanzar; "clear" = pausado. Arranca
# en "set" (nada que pausar todavia). /api/corpus/buscar lo limpia antes de
# trabajar y lo repone al terminar, para que una busqueda dispare una pausa
# en vez de quedarse muerta de hambre esperando su turno de CPU.
_permiso_indexar = threading.Event()
_permiso_indexar.set()

# "set" = alguien pidio detener la indexacion en curso. Se limpia al arrancar
# cada job nuevo (si no, un stop viejo mataria la siguiente corrida antes de
# que empiece).
_cancelar_indexar = threading.Event()


@app.post("/api/corpus/indexar")
def corpus_indexar(p: PeticionIndexarCorpus) -> dict[str, Any]:
    """Arranca la indexacion en segundo plano. Sondea /api/corpus/indexar/estado."""
    with _indexar_lock:
        if _indexar_estado["en_curso"]:
            raise HTTPException(409, "Ya hay una indexacion en curso.")
        _indexar_estado.update({
            "en_curso": True, "etapa": "cargando_modelo",
            "actual": 0, "total": 0, "archivo": "", "en_cache": 0, "nuevas": 0,
            "nuevas_ok": 0, "nuevas_fallidas": 0,
            "resultado": None, "error": None,
        })
    _cancelar_indexar.clear()

    def _reportar(info: dict[str, Any]) -> None:
        with _indexar_lock:
            _indexar_estado.update(info)

    def _trabajo() -> None:
        # El try envuelve TODO, incluido abrir el historial. Cuando el `with
        # HistorialStore()` quedaba fuera, un fallo al abrirlo (p.ej. "database
        # is locked") mataba el hilo sin pasar por el finally: en_curso se
        # quedaba en true para siempre, la pantalla decia "Cargando el
        # modelo…" indefinidamente y el candado rechazaba cualquier corrida
        # nueva con 409. Un error al arrancar tiene que verse como un error,
        # no como un trabajo eterno.
        hist = None
        corrida_id = None
        try:
            # El historial se abre DENTRO del hilo: sqlite3 no comparte
            # conexiones entre hilos, y esta es la unica que escribe corridas.
            hist = HistorialStore()
            corrida_id = hist.iniciar_corrida(
                "indexado", str(CORPUS_DIR), p.limite_carpetas, p.limite_por_carpeta)

            from facid.busqueda import indexar_corpus
            from facid.runtime import load_runtime
            rt = load_runtime(device=p.device,
                              require_gpu=False if p.allow_cpu_fallback else None,
                              verbose=False)
            resultado = indexar_corpus(
                CORPUS_DIR, rt,
                limite_carpetas=p.limite_carpetas, limite_por_carpeta=p.limite_por_carpeta,
                face_policy=p.face_policy, on_progreso=_reportar,
                pausable=_permiso_indexar, cancelable=_cancelar_indexar)
            hist.cerrar_corrida(corrida_id, resultado=resultado)
            with _indexar_lock:
                _indexar_estado["resultado"] = resultado
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            if hist is not None and corrida_id is not None:
                try:
                    hist.cerrar_corrida(corrida_id, error=msg)
                except Exception:
                    pass    # si la base es justo lo que fallo, no insistir
            with _indexar_lock:
                _indexar_estado["error"] = msg
        finally:
            if hist is not None:
                try:
                    hist.close()
                except Exception:
                    pass
            with _indexar_lock:
                _indexar_estado["en_curso"] = False

    threading.Thread(target=_trabajo, daemon=True).start()
    with _indexar_lock:
        return dict(_indexar_estado)


@app.get("/api/corpus/indexar/estado")
def corpus_indexar_estado() -> dict[str, Any]:
    with _indexar_lock:
        return dict(_indexar_estado)


@app.post("/api/corpus/indexar/detener")
def corpus_indexar_detener() -> dict[str, Any]:
    """Pide parar en el siguiente punto seguro (nunca a mitad de una foto).
    Lo ya guardado en cache se queda; sondea /api/corpus/indexar/estado para
    ver cuando en_curso pasa a false."""
    with _indexar_lock:
        if not _indexar_estado["en_curso"]:
            raise HTTPException(409, "No hay ninguna indexacion en curso.")
    _cancelar_indexar.set()
    return {"deteniendo": True}


class PeticionBuscarCorpus(BaseModel):
    persona: str  # carpeta en data/ cuyas fotos son la consulta
    limite_carpetas: int | None = None
    limite_por_carpeta: int | None = None
    face_policy: str = "strict"
    top_n: int = 15
    device: str = "cuda"
    allow_cpu_fallback: bool = True
    # Solo para dejarlo asentado en el historial: la comparacion no lo usa
    # (se guarda el ranking completo, no solo lo que pasa el umbral).
    umbral: float | None = None


@app.post("/api/corpus/buscar")
def corpus_buscar(p: PeticionBuscarCorpus) -> dict[str, Any]:
    """Compara las fotos de `persona` contra lo que YA esta indexado del
    corpus. Sincrono (no hilo aparte): solo extrae las pocas fotos de
    consulta si hiciera falta, y compara contra cache — rapido incluso con
    el corpus completo indexado.
    """
    persona_dir = _dentro(DATA_DIR, p.persona)
    if not persona_dir.is_dir():
        raise HTTPException(404, f"no existe la persona: {p.persona}")
    fotos = sorted(f for f in persona_dir.iterdir()
                   if f.is_file() and f.suffix.lower() in EXT_IMG)
    if not fotos:
        raise HTTPException(400, f"{p.persona} no tiene fotos")

    from facid.busqueda import buscar
    from facid.runtime import load_runtime
    # Pausa la indexacion (si hay una en curso) mientras dura esta busqueda:
    # las dos usan el modelo intensivamente, y sin esto la busqueda queda
    # muerta de hambre de CPU detras de un corpus de decenas de miles de
    # fotos. Se repone SIEMPRE, exito o error, para no dejar la indexacion
    # pausada para siempre si esto revienta.
    _permiso_indexar.clear()
    try:
        rt = load_runtime(device=p.device,
                          require_gpu=False if p.allow_cpu_fallback else None,
                          verbose=False)
        resultado = buscar(
            fotos, CORPUS_DIR, rt,
            limite_carpetas=p.limite_carpetas, limite_por_carpeta=p.limite_por_carpeta,
            face_policy=p.face_policy, top_n=p.top_n)
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
    finally:
        _permiso_indexar.set()

    for r in resultado["resultados"]:
        for c in r["coincidencias"]:
            c["ruta"] = f"{c['persona']}/{c['archivo']}"

    with HistorialStore() as hist:
        resultado["busqueda_id"] = hist.guardar_busqueda(
            p.persona, str(CORPUS_DIR), p.umbral, resultado)
    return resultado


# ---------------------------------------------------------------------------
# Historial: lo unico que vivia SOLO en memoria del proceso. Sin esto, un
# reinicio dejaba la pantalla en blanco aunque el trabajo siguiera en disco.
# ---------------------------------------------------------------------------
@app.get("/api/corpus/cobertura")
def corpus_cobertura() -> dict[str, Any]:
    """Cuanto del corpus ya se proceso alguna vez. Sale de SQLite, no de
    recorrer el disco: responde igual de rapido con el corpus completo."""
    with HistorialStore() as hist:
        return hist.cobertura(CORPUS_DIR)


def _a_ruta_de_corpus(source_path: str) -> str | None:
    """Absoluta -> relativa al corpus, o None si ya no cae dentro.

    El front solo puede pedir fotos por /api/corpus/foto, que es la unica via
    sandboxeada; una ruta absoluta ahi no le sirve de nada.
    """
    try:
        return Path(source_path).resolve().relative_to(CORPUS_DIR.resolve()).as_posix()
    except (ValueError, OSError):
        return None


@app.get("/api/corpus/fotos")
def corpus_fotos(
    estado: str = Query("todas", pattern="^(todas|con_rostro|sin_rostro)$"),
    persona: str | None = None,
    solo_con_margen: bool = False,
    offset: int = Query(0, ge=0),
    limite: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Una pagina de fotos del corpus, filtrable. Alimenta tanto las tiras
    cortas de las cifras como el explorador completo."""
    if persona and ("/" in persona or "\\" in persona or persona in ("", ".", "..")):
        raise HTTPException(400, f"nombre de persona invalido: {persona!r}")

    with HistorialStore() as hist:
        r = hist.fotos_del_corpus(
            CORPUS_DIR, persona=persona, estado=estado,
            solo_con_margen=solo_con_margen, offset=offset, limite=limite)

    fotos = []
    for f in r["fotos"]:
        rel = _a_ruta_de_corpus(f["source_path"])
        if rel is None:
            continue
        fotos.append({
            "ruta": rel,
            "estado": f["estado"],
            "det_score": f["det_score"],
            "margen_agregado": f["margen_agregado"],
            "error": f["error"],
            "n_faces_detected": f["n_faces_detected"],
        })
    return {**r, "fotos": fotos}


@app.get("/api/corpus/personas")
def corpus_personas() -> dict[str, Any]:
    """Desglose por carpeta del corpus, para el filtro del explorador."""
    with HistorialStore() as hist:
        return {"personas": hist.personas_del_corpus(CORPUS_DIR)}


@app.get("/api/busquedas")
def listar_busquedas(limite: int = 20) -> dict[str, Any]:
    with HistorialStore() as hist:
        return {"busquedas": hist.busquedas(limite)}


@app.get("/api/busquedas/{busqueda_id}")
def ver_busqueda(busqueda_id: int) -> dict[str, Any]:
    with HistorialStore() as hist:
        b = hist.busqueda(busqueda_id)
    if b is None:
        raise HTTPException(404, f"no existe la busqueda {busqueda_id}")
    return b


@app.post("/api/busquedas/{busqueda_id}/borrar")
def borrar_busqueda(busqueda_id: int) -> dict[str, Any]:
    with HistorialStore() as hist:
        if not hist.borrar_busqueda(busqueda_id):
            raise HTTPException(404, f"no existe la busqueda {busqueda_id}")
    return {"borrada": busqueda_id}

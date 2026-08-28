"""Busqueda 1:N: comparar un subset de referencia contra un corpus externo.

Distinto del harness de calibracion (harness.py): ahi los pares tienen una
etiqueta same_person conocida de antemano (la carpeta es la identidad), y el
objetivo es medir FMR/FNMR. Aqui no hay etiqueta -- el objetivo es encontrar,
si existen, coincidencias de una persona de referencia dentro de un corpus
mucho mas grande y sin clasificar.

Reusa el mismo extractor y la MISMA cache de embeddings (EmbeddingStore, por
sha256 de la imagen -- no por ruta ni por que carpeta vive en). Eso importa
en concreto: indexar el corpus una vez sirve para todas las busquedas futuras
contra ese mismo contenido, sin volver a pagar la extraccion.

Separado en dos pasos a proposito:
  indexar_corpus() -- SOLO extrae y cachea. Es lo lento (una foto a la vez).
  buscar()         -- compara contra lo que YA esta en cache. Es casi gratis
                       (coseno entre vectores), pero NO extrae nada del
                       corpus que no se haya indexado antes -- si limitaste
                       el indexado a una muestra, buscar() solo ve esa
                       muestra. Es intencional: permite probar el mecanismo
                       con un subconjunto chico antes de comprometerse a
                       indexar un corpus de decenas de miles de fotos.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from .compare import compare
from .extract import extract_embedding
from .harness import EXTENSIONES
from .store import EmbeddingStore

# Progreso de indexado. Recibe un dict en vez de argumentos sueltos (a
# diferencia de harness.ProgresoCB) porque aqui interesa distinguir cuanto
# del avance es trabajo real y cuanto es cache: sin ese desglose, el contador
# corriendo de 1 a 85k se ve igual venga de donde venga.
ProgresoIndexCB = Callable[[dict[str, Any]], None]


def descubrir_corpus(corpus_dir: str | Path, *,
                     limite_carpetas: int | None = None,
                     limite_por_carpeta: int | None = None) -> dict[str, list[Path]]:
    """Como descubrir_personas, pero con limites: el corpus puede tener
    decenas de miles de fotos y no siempre se quiere tocar todo.

    0 (o negativo) significa SIN LIMITE, igual que None. Es lo que dice la UI
    y lo que espera cualquiera que escriba 0 en el campo; interpretarlo
    literal como "las primeras cero carpetas" hacia que la corrida terminara
    al instante sin indexar nada y sin avisar por que.
    """
    raiz = Path(corpus_dir)
    if not raiz.is_dir():
        raise FileNotFoundError(f"no existe el corpus: {raiz}")

    if limite_carpetas is not None and limite_carpetas <= 0:
        limite_carpetas = None
    if limite_por_carpeta is not None and limite_por_carpeta <= 0:
        limite_por_carpeta = None

    carpetas = sorted(p for p in raiz.iterdir() if p.is_dir())
    if limite_carpetas is not None:
        carpetas = carpetas[:limite_carpetas]

    resultado: dict[str, list[Path]] = {}
    for sub in carpetas:
        fotos = sorted(f for f in sub.iterdir()
                       if f.is_file() and f.suffix.lower() in EXTENSIONES)
        if limite_por_carpeta is not None:
            fotos = fotos[:limite_por_carpeta]
        if fotos:
            resultado[sub.name] = fotos
    return resultado


def indexar_corpus(corpus_dir: str | Path, runtime, *,
                   limite_carpetas: int | None = None,
                   limite_por_carpeta: int | None = None,
                   face_policy: str = "strict",
                   on_progreso: ProgresoIndexCB | None = None,
                   pausable: threading.Event | None = None,
                   cancelable: threading.Event | None = None) -> dict[str, Any]:
    """Extrae y cachea embeddings del corpus. No compara nada todavia.

    `pausable`: un threading.Event que el llamador controla desde afuera.
    "set" (o None) = seguir; "clear" = pausar ANTES del siguiente archivo.
    Existe porque indexar y buscar() compiten por el mismo CPU (ambos usan
    el modelo); sin esto, una busqueda disparada mientras el corpus se
    indexa se queda muerta de hambre en vez de responder rapido.

    `cancelable`: otro threading.Event; "set" = detenerse en el siguiente
    punto seguro (entre fotos, nunca a mitad de una extraccion) y devolver
    lo que ya se alcanzo a guardar. Se revisa tambien mientras se esta
    pausado, para que un stop no tenga que esperar a que termine la pausa.
    """
    corpus = descubrir_corpus(corpus_dir, limite_carpetas=limite_carpetas,
                              limite_por_carpeta=limite_por_carpeta)
    rutas = [f for fotos in corpus.values() for f in fotos]

    store = EmbeddingStore()
    fp = runtime.fingerprint
    ok = 0
    fallidas = 0
    en_cache = 0
    nuevas = 0
    detenido = False

    def _avisar(actual: int, archivo: str, etapa: str) -> None:
        if on_progreso:
            on_progreso({"actual": actual, "total": len(rutas), "archivo": archivo,
                         "etapa": etapa, "en_cache": en_cache, "nuevas": nuevas})

    try:
        for k, ruta in enumerate(rutas, 1):
            while pausable is not None and not pausable.is_set():
                if cancelable is not None and cancelable.is_set():
                    break
                _avisar(k - 1, "", "pausado")
                pausable.wait(timeout=0.5)
            if cancelable is not None and cancelable.is_set():
                detenido = True
                break
            sha = store.sha_de(ruta)
            fila = store.buscar(sha, fp.model_pack, runtime.rec_model_sha256,
                                fp.det_size, face_policy)
            if fila is not None:
                ok += 1
                en_cache += 1
            elif store.buscar_fallo(sha, fp.model_pack, runtime.rec_model_sha256,
                                    fp.det_size, face_policy) is not None:
                # Ya sabemos que esto falla bajo esta config exacta: no vale
                # la pena volver a correr el detector para el mismo resultado.
                fallidas += 1
                en_cache += 1
            else:
                nuevas += 1
                r = extract_embedding(ruta, runtime, face_policy=face_policy)
                if r["error"] is None:
                    store.guardar(r, runtime, face_policy)
                    ok += 1
                else:
                    store.registrar_fallo(r, runtime, face_policy)
                    fallidas += 1
            _avisar(k, ruta.name, "indexando")
    finally:
        store.close()

    return {
        "carpetas_vistas": len(corpus),
        "fotos_vistas": len(rutas),
        "indexadas_ok": ok,
        "fallidas": fallidas,
        "en_cache": en_cache,
        "nuevas": nuevas,
        "detenido": detenido,
    }


def buscar(query_paths: list[str | Path], corpus_dir: str | Path, runtime, *,
          limite_carpetas: int | None = None,
          limite_por_carpeta: int | None = None,
          face_policy: str = "strict",
          top_n: int = 15) -> dict[str, Any]:
    """Compara cada foto de consulta contra lo que YA esta indexado del
    corpus. No re-extrae el corpus (eso es indexar_corpus); si algo no esta
    en cache, simplemente no participa en la comparacion.
    """
    corpus = descubrir_corpus(corpus_dir, limite_carpetas=limite_carpetas,
                              limite_por_carpeta=limite_por_carpeta)
    store = EmbeddingStore()
    fp = runtime.fingerprint

    try:
        indexado: list[tuple[str, str, Any]] = []
        for persona, fotos in corpus.items():
            for f in fotos:
                sha = store.sha_de(f)
                fila = store.buscar(sha, fp.model_pack, runtime.rec_model_sha256,
                                    fp.det_size, face_policy)
                if fila is not None:
                    indexado.append((persona, f.name, store.cargar_embedding(fila)))

        resultados = []
        for qp in query_paths:
            qpath = Path(qp)
            sha = store.sha_de(qpath)
            fila = store.buscar(sha, fp.model_pack, runtime.rec_model_sha256,
                                fp.det_size, face_policy)
            if fila is not None:
                emb = store.cargar_embedding(fila)
            else:
                r = extract_embedding(qpath, runtime, face_policy=face_policy)
                if r["error"] is not None:
                    resultados.append({
                        "consulta": qpath.name, "error": r["error"], "coincidencias": [],
                    })
                    continue
                store.guardar(r, runtime, face_policy)
                emb = r["embedding"]

            puntuadas = sorted(
                (
                    {"persona": persona, "archivo": nombre, "score": compare(emb, emb_c)}
                    for persona, nombre, emb_c in indexado
                ),
                key=lambda x: -x["score"],
            )
            resultados.append({
                "consulta": qpath.name, "error": None, "coincidencias": puntuadas[:top_n],
            })
    finally:
        store.close()

    return {
        "n_indexado": len(indexado),
        "n_carpetas_indexadas": len({p for p, _, _ in indexado}),
        "resultados": resultados,
    }

"""Historial de trabajo: que se indexo, cuando, y que se busco.

Vive aparte de store.py a proposito. EmbeddingStore responde "¿ya tengo el
embedding de esta imagen?" — es la cache que hace viable no reprocesar. Esto
responde otra pregunta: "¿que he hecho hasta ahora?". Mezclarlas haria que la
tabla de embeddings cargue con dos responsabilidades.

Comparte el MISMO archivo SQLite (out/index.sqlite) porque son datos del mismo
experimento y separarlos en dos archivos solo agregaria una cosa mas que se
puede perder a medias.

Por que existe: el estado de una corrida vivia solo en memoria del API. Al
reiniciar el servidor, los embeddings seguian en disco pero la pantalla se
veia vacia y no habia forma de saber cuanto se llevaba avanzado sin volver a
correr algo.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config import INDEX_DB
from .util import utc_now_iso

ESQUEMA_HISTORIAL = """
CREATE TABLE IF NOT EXISTS corridas (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo              TEXT    NOT NULL,   -- 'indexado'
    corpus_dir        TEXT,
    limite_carpetas   INTEGER,
    limite_por_carpeta INTEGER,
    carpetas_vistas   INTEGER,
    fotos_vistas      INTEGER,
    indexadas_ok      INTEGER,
    fallidas          INTEGER,
    en_cache          INTEGER,
    nuevas            INTEGER,
    detenido          INTEGER,
    error             TEXT,
    iniciada_en       TEXT    NOT NULL,
    terminada_en      TEXT
);

CREATE TABLE IF NOT EXISTS busquedas (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    persona              TEXT    NOT NULL,
    corpus_dir           TEXT,
    umbral               REAL,
    n_indexado           INTEGER,
    n_carpetas_indexadas INTEGER,
    creada_en            TEXT    NOT NULL
);

-- Una fila por coincidencia. Se guarda el ranking completo que se mostro,
-- no solo las que pasaron el umbral: el umbral es editable despues, y un
-- historial que solo guardara las "positivas" no se podria reinterpretar.
CREATE TABLE IF NOT EXISTS coincidencias (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    busqueda_id  INTEGER NOT NULL,
    consulta     TEXT    NOT NULL,   -- foto de referencia
    error        TEXT,               -- si esa consulta no se pudo procesar
    persona      TEXT,               -- carpeta candidata del corpus
    archivo      TEXT,
    ruta         TEXT,
    score        REAL,
    orden        INTEGER
);

CREATE INDEX IF NOT EXISTS ix_coinc_busqueda ON coincidencias (busqueda_id);
CREATE INDEX IF NOT EXISTS ix_busquedas_fecha ON busquedas (creada_en DESC);
"""


class HistorialStore:
    def __init__(self, db_path: Path | str = INDEX_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(ESQUEMA_HISTORIAL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------- corridas
    def iniciar_corrida(self, tipo: str, corpus_dir: str,
                        limite_carpetas: int | None,
                        limite_por_carpeta: int | None) -> int:
        cur = self.conn.execute(
            """INSERT INTO corridas (tipo, corpus_dir, limite_carpetas,
                   limite_por_carpeta, iniciada_en)
               VALUES (?,?,?,?,?)""",
            (tipo, corpus_dir, limite_carpetas, limite_por_carpeta, utc_now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def cerrar_corrida(self, corrida_id: int, resultado: dict[str, Any] | None = None,
                       error: str | None = None) -> None:
        r = resultado or {}
        self.conn.execute(
            """UPDATE corridas SET carpetas_vistas=?, fotos_vistas=?, indexadas_ok=?,
                   fallidas=?, en_cache=?, nuevas=?, detenido=?, error=?, terminada_en=?
               WHERE id=?""",
            (r.get("carpetas_vistas"), r.get("fotos_vistas"), r.get("indexadas_ok"),
             r.get("fallidas"), r.get("en_cache"), r.get("nuevas"),
             1 if r.get("detenido") else 0, error, utc_now_iso(), corrida_id),
        )
        self.conn.commit()

    def ultima_corrida(self, tipo: str = "indexado") -> dict[str, Any] | None:
        fila = self.conn.execute(
            "SELECT * FROM corridas WHERE tipo=? AND terminada_en IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (tipo,),
        ).fetchone()
        return dict(fila) if fila else None

    # ------------------------------------------------------------ busquedas
    def guardar_busqueda(self, persona: str, corpus_dir: str, umbral: float | None,
                         resultado: dict[str, Any]) -> int:
        cur = self.conn.execute(
            """INSERT INTO busquedas (persona, corpus_dir, umbral, n_indexado,
                   n_carpetas_indexadas, creada_en)
               VALUES (?,?,?,?,?,?)""",
            (persona, corpus_dir, umbral, resultado.get("n_indexado"),
             resultado.get("n_carpetas_indexadas"), utc_now_iso()),
        )
        bid = int(cur.lastrowid)
        filas = []
        for r in resultado.get("resultados", []):
            if r.get("error"):
                filas.append((bid, r["consulta"], r["error"], None, None, None, None, None))
                continue
            for i, c in enumerate(r.get("coincidencias", [])):
                filas.append((bid, r["consulta"], None, c.get("persona"),
                              c.get("archivo"), c.get("ruta"), c.get("score"), i))
        if filas:
            self.conn.executemany(
                """INSERT INTO coincidencias (busqueda_id, consulta, error, persona,
                       archivo, ruta, score, orden) VALUES (?,?,?,?,?,?,?,?)""",
                filas,
            )
        self.conn.commit()
        return bid

    def busquedas(self, limite: int = 20) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM busquedas ORDER BY id DESC LIMIT ?", (limite,))]

    def busqueda(self, busqueda_id: int) -> dict[str, Any] | None:
        cab = self.conn.execute(
            "SELECT * FROM busquedas WHERE id=?", (busqueda_id,)).fetchone()
        if cab is None:
            return None
        # Se reconstruye la misma forma que devuelve busqueda.buscar(), para que
        # el front pinte un historial y una busqueda recien hecha con el mismo
        # codigo en vez de dos caminos que se pueden separar.
        por_consulta: dict[str, dict[str, Any]] = {}
        for r in self.conn.execute(
                "SELECT * FROM coincidencias WHERE busqueda_id=? ORDER BY id", (busqueda_id,)):
            entrada = por_consulta.setdefault(
                r["consulta"], {"consulta": r["consulta"], "error": None, "coincidencias": []})
            if r["error"]:
                entrada["error"] = r["error"]
                continue
            entrada["coincidencias"].append({
                "persona": r["persona"], "archivo": r["archivo"],
                "ruta": r["ruta"], "score": r["score"],
            })
        return {
            **dict(cab),
            "n_indexado": cab["n_indexado"],
            "n_carpetas_indexadas": cab["n_carpetas_indexadas"],
            "resultados": list(por_consulta.values()),
        }

    def borrar_busqueda(self, busqueda_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM busquedas WHERE id=?", (busqueda_id,))
        self.conn.execute("DELETE FROM coincidencias WHERE busqueda_id=?", (busqueda_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------ cobertura
    def _existe_tabla(self, nombre: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nombre,)
        ).fetchone() is not None

    def cobertura(self, corpus_dir: str | Path) -> dict[str, Any]:
        """Cuantas fotos del corpus ya se procesaron alguna vez.

        Se cuenta por prefijo de ruta con substr() en vez de LIKE porque los
        nombres de carpeta del corpus traen '_' y '%', que LIKE interpretaria
        como comodines.

        Lee tablas que son de EmbeddingStore, no de aqui. Si todavia no
        existen (nadie ha extraido nada nunca) la respuesta correcta es cero,
        no una excepcion — de otro modo este endpoint tronaria en una
        instalacion recien hecha.
        """
        if not (self._existe_tabla("embeddings") and self._existe_tabla("fallos")):
            return {
                "procesadas": 0, "con_rostro": 0, "sin_rostro": 0,
                "total_ultimo_conteo": None, "ultima_corrida": self.ultima_corrida(),
            }

        prefijo = str(Path(corpus_dir))
        n = len(prefijo)
        con_rostro = self.conn.execute(
            "SELECT COUNT(DISTINCT image_sha256) c FROM embeddings WHERE substr(source_path,1,?)=?",
            (n, prefijo)).fetchone()["c"]
        procesadas = self.conn.execute(
            """SELECT COUNT(*) c FROM (
                   SELECT image_sha256 FROM embeddings WHERE substr(source_path,1,?)=?
                   UNION
                   SELECT image_sha256 FROM fallos WHERE substr(source_path,1,?)=?
               )""",
            (n, prefijo, n, prefijo)).fetchone()["c"]
        ultima = self.ultima_corrida()
        return {
            "procesadas": procesadas,
            "con_rostro": con_rostro,
            "sin_rostro": procesadas - con_rostro,
            # Denominador del ultimo recorrido completo conocido. Es una
            # referencia, no una verdad al segundo: el corpus puede crecer
            # entre corridas.
            "total_ultimo_conteo": (ultima or {}).get("fotos_vistas"),
            "ultima_corrida": ultima,
        }

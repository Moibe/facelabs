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

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import INDEX_DB
from .store import activar_wal
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

-- El consolidado por persona no se puede recalcular al releer una busqueda:
-- de las coincidencias solo se guarda el top_n de cada consulta, y el
-- consolidado se agrega sobre TODAS las comparaciones. Asi que se guarda.
CREATE TABLE IF NOT EXISTS consolidado (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    busqueda_id     INTEGER NOT NULL,
    persona         TEXT    NOT NULL,
    mejor           REAL,
    promedio        REAL,
    n_consultas     INTEGER,
    n_fotos_corpus  INTEGER,
    mejor_ruta      TEXT,
    por_consulta    TEXT,      -- JSON {consulta: score}
    orden           INTEGER
);

CREATE INDEX IF NOT EXISTS ix_coinc_busqueda ON coincidencias (busqueda_id);
CREATE INDEX IF NOT EXISTS ix_consol_busqueda ON consolidado (busqueda_id);
CREATE INDEX IF NOT EXISTS ix_busquedas_fecha ON busquedas (creada_en DESC);
"""


class HistorialStore:
    def __init__(self, db_path: Path | str = INDEX_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Ver las notas en store.py: comparten archivo con una indexacion que
        # puede estar escribiendo durante horas, y sin WAL las lecturas de aqui
        # (cobertura, explorador) se quedaban esperando a que terminara.
        self.conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        activar_wal(self.conn)
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

    def ultima_corrida_sin_limites(self, tipo: str = "indexado") -> dict[str, Any] | None:
        """La ultima corrida que recorrio el corpus ENTERO.

        Solo esas sirven como denominador ("llevas X de ~Y"): una corrida
        limitada a 3 carpetas vio 12 fotos, y presentarlo como el total del
        corpus es simplemente falso. NULL y 0 significan lo mismo aqui — sin
        limite — porque el front manda null y la CLI puede mandar 0.

        Tambien se exige fotos_vistas: una corrida que reviento antes de
        contar nada esta "sin limites" pero no aporta ningun total.
        """
        fila = self.conn.execute(
            """SELECT * FROM corridas
               WHERE tipo=? AND terminada_en IS NOT NULL
                 AND fotos_vistas IS NOT NULL
                 AND COALESCE(limite_carpetas, 0) = 0
                 AND COALESCE(limite_por_carpeta, 0) = 0
               ORDER BY id DESC LIMIT 1""", (tipo,),
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

        cons = [
            (bid, c["persona"], c.get("mejor"), c.get("promedio"),
             c.get("n_consultas"), c.get("n_fotos_corpus"), c.get("mejor_ruta"),
             json.dumps(c.get("por_consulta") or {}), i)
            for i, c in enumerate(resultado.get("consolidado", []))
        ]
        if cons:
            self.conn.executemany(
                """INSERT INTO consolidado (busqueda_id, persona, mejor, promedio,
                       n_consultas, n_fotos_corpus, mejor_ruta, por_consulta, orden)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                cons,
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
        consolidado = []
        for r in self.conn.execute(
                "SELECT * FROM consolidado WHERE busqueda_id=? ORDER BY orden", (busqueda_id,)):
            try:
                pc = json.loads(r["por_consulta"] or "{}")
            except (TypeError, ValueError):
                pc = {}
            consolidado.append({
                "persona": r["persona"], "mejor": r["mejor"], "promedio": r["promedio"],
                "n_consultas": r["n_consultas"], "n_fotos_corpus": r["n_fotos_corpus"],
                "mejor_ruta": r["mejor_ruta"], "por_consulta": pc,
            })

        return {
            **dict(cab),
            "n_indexado": cab["n_indexado"],
            "n_carpetas_indexadas": cab["n_carpetas_indexadas"],
            "resultados": list(por_consulta.values()),
            "consolidado": consolidado,
        }

    def borrar_busqueda(self, busqueda_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM busquedas WHERE id=?", (busqueda_id,))
        self.conn.execute("DELETE FROM coincidencias WHERE busqueda_id=?", (busqueda_id,))
        self.conn.execute("DELETE FROM consolidado WHERE busqueda_id=?", (busqueda_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------ cobertura
    def _existe_tabla(self, nombre: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nombre,)
        ).fetchone() is not None

    # Union de exitos y fallos en una sola forma. Una foto que fallo y DESPUES
    # se extrajo bien cuenta como con_rostro y nada mas: por eso los fallos
    # excluyen los sha que ya tienen embedding. Es la misma regla que usa
    # cobertura(), asi que los totales de aqui cuadran con los de las cifras.
    _SQL_FOTOS = """
        SELECT source_path, 'con_rostro' AS estado, det_score, margen_agregado,
               NULL AS error, n_faces_detected, MAX(created_at) AS created_at
          FROM embeddings
         WHERE substr(source_path,1,?)=?
         GROUP BY image_sha256
        UNION ALL
        SELECT source_path, 'sin_rostro' AS estado, NULL, NULL,
               error, n_faces_detected, MAX(created_at)
          FROM fallos
         WHERE substr(source_path,1,?)=?
           AND image_sha256 NOT IN (SELECT image_sha256 FROM embeddings)
         GROUP BY image_sha256
    """

    def _filtro_fotos(self, corpus_dir: str | Path, persona: str | None,
                      estado: str, solo_con_margen: bool):
        """Devuelve (sql_interno, params, sql_extra, params_extra)."""
        # El filtro por persona se mete en el PREFIJO en vez de un WHERE
        # aparte: asi la misma comparacion de substr que ya acota al corpus
        # acota tambien a la carpeta, sin recorrer lo que no interesa.
        raiz = Path(corpus_dir) / persona if persona else Path(corpus_dir)
        pref = str(raiz)
        n = len(pref)
        params = [n, pref, n, pref]

        extra = ""
        extra_params: list[Any] = []
        if estado in ("con_rostro", "sin_rostro"):
            extra += " AND estado=?"
            extra_params.append(estado)
        if solo_con_margen:
            extra += " AND COALESCE(margen_agregado, 0) > 0"
        return self._SQL_FOTOS, params, extra, extra_params

    def fotos_del_corpus(self, corpus_dir: str | Path, *, persona: str | None = None,
                         estado: str = "todas", solo_con_margen: bool = False,
                         offset: int = 0, limite: int = 100) -> dict[str, Any]:
        """Una pagina de fotos del corpus, con su total para poder paginar."""
        if not (self._existe_tabla("embeddings") and self._existe_tabla("fallos")):
            return {"total": 0, "offset": offset, "limite": limite, "fotos": []}

        base, params, extra, extra_params = self._filtro_fotos(
            corpus_dir, persona, estado, solo_con_margen)

        total = self.conn.execute(
            f"SELECT COUNT(*) c FROM ({base}) WHERE 1=1{extra}",
            (*params, *extra_params)).fetchone()["c"]

        filas = self.conn.execute(
            f"SELECT * FROM ({base}) WHERE 1=1{extra} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, *extra_params, limite, offset)).fetchall()
        return {"total": total, "offset": offset, "limite": limite,
                "fotos": [dict(r) for r in filas]}

    def personas_del_corpus(self, corpus_dir: str | Path) -> list[dict[str, Any]]:
        """Cuantas fotos con y sin rostro tiene cada carpeta del corpus.

        Sale de las rutas ya procesadas, no de listar el disco: una carpeta
        que todavia no se ha tocado no aparece, que es lo correcto para un
        filtro (elegirla mostraria cero fotos).
        """
        if not (self._existe_tabla("embeddings") and self._existe_tabla("fallos")):
            return []
        base, params, _, _ = self._filtro_fotos(corpus_dir, None, "todas", False)

        # El nombre de la carpeta se saca en Python y no en SQL: partir una
        # ruta de Windows dentro de SQLite pide un anidado de substr/instr
        # ilegible, y son ~500 carpetas — no vale la pena.
        crudo = self.conn.execute(f"SELECT source_path, estado FROM ({base})", params)
        acc: dict[str, dict[str, int]] = {}
        raiz_p = Path(corpus_dir).resolve()
        for r in crudo:
            try:
                rel = Path(r["source_path"]).resolve().relative_to(raiz_p)
            except (ValueError, OSError):
                continue
            if not rel.parts:
                continue
            d = acc.setdefault(rel.parts[0], {"con_rostro": 0, "sin_rostro": 0})
            d[r["estado"]] += 1
        return [{"persona": k, **v, "total": v["con_rostro"] + v["sin_rostro"]}
                for k, v in sorted(acc.items())]

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
        completa = self.ultima_corrida_sin_limites()
        return {
            "procesadas": procesadas,
            "con_rostro": con_rostro,
            "sin_rostro": procesadas - con_rostro,
            # Denominador: SOLO de una corrida sin limites. Tomarlo de la
            # ultima corrida a secas hacia que una prueba acotada a 3 carpetas
            # reportara "de ~12 fotos" como si fuera el corpus entero. Es una
            # referencia igual, no una verdad al segundo: el corpus crece.
            "total_ultimo_conteo": (completa or {}).get("fotos_vistas"),
            "ultima_corrida": ultima,
        }

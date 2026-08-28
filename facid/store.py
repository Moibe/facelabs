"""Persistencia de embeddings: .npy en disco + indice SQLite con metadata.

No es opcional. Sin esto, cada experimento de calibracion reprocesa todo y
comparar dos modelos distintos deja de ser viable.

Llave de cache = (sha256 de la imagen, model_pack, sha256 del modelo de
reconocimiento, det_size). Todo lo que cambia el embedding entra en la llave;
cambiar cualquiera de esos invalida lo guardado, a proposito.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from .config import EMBEDDINGS_DIR, INDEX_DB, ensure_dirs
from .errors import ErrorCode, FacePolicy
from .util import sha256_file, utc_now_iso

ESQUEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    image_sha256        TEXT    NOT NULL,
    source_path         TEXT    NOT NULL,
    npy_path            TEXT    NOT NULL,
    embedding_dim       INTEGER NOT NULL,
    det_score           REAL    NOT NULL,
    bbox_x              REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
    n_faces_detected    INTEGER NOT NULL,
    face_policy         TEXT    NOT NULL,
    face_selection      TEXT    NOT NULL,
    all_det_scores      TEXT,
    exif_applied        INTEGER,
    -- 0 = el rostro se detecto en la imagen tal cual. >0 = solo aparecio tras
    -- rellenar ese % de margen (ver extract._detectar_con_margen). Queda
    -- asentado: un embedding "rescatado" no se presenta como uno normal.
    margen_agregado     REAL,
    model_pack          TEXT    NOT NULL,
    rec_model_file      TEXT,
    rec_model_sha256    TEXT    NOT NULL,
    det_model_file      TEXT,
    det_model_sha256    TEXT,
    det_size            TEXT    NOT NULL,
    provider            TEXT    NOT NULL,
    insightface_version TEXT,
    onnxruntime_version TEXT,
    facid_version       TEXT,
    created_at          TEXT    NOT NULL,
    UNIQUE (image_sha256, model_pack, rec_model_sha256, det_size)
);

CREATE TABLE IF NOT EXISTS fallos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    image_sha256        TEXT,
    source_path         TEXT    NOT NULL,
    error               TEXT    NOT NULL,
    error_message       TEXT,
    n_faces_detected    INTEGER,
    all_det_scores      TEXT,
    face_policy         TEXT,
    model_pack          TEXT,
    rec_model_sha256    TEXT,
    det_size            TEXT,
    created_at          TEXT    NOT NULL
);

-- Evita releer un archivo entero solo para saber su sha256. La identidad
-- canonica sigue siendo el CONTENIDO (ver util.sha256_file); esto es puro
-- atajo de I/O: si (ruta, tamaño, mtime) no cambiaron, el sha tampoco.
-- Mismo compromiso que hace git con su stat cache: un archivo reemplazado
-- conservando tamaño Y mtime exactos daria un sha viejo, pero eso no pasa
-- por accidente.
CREATE TABLE IF NOT EXISTS stat_cache (
    source_path TEXT PRIMARY KEY,
    size        INTEGER NOT NULL,
    mtime_ns    INTEGER NOT NULL,
    sha256      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_emb_sha ON embeddings (image_sha256);
CREATE INDEX IF NOT EXISTS ix_fallos_sha ON fallos (image_sha256);
"""

# Fallos donde el resultado NO depende de face_policy: cero rostros o un
# archivo corrupto siguen siendo lo mismo sin importar que politica de
# seleccion este activa. MULTIPLE_FACES (y, por las dudas, INVALID_EMBEDDING
# si hubo mas de un rostro) SI dependen: bajo 'strict' fallan, bajo 'largest'
# el mismo archivo podria tener exito con otro rostro elegido.
_FALLOS_INDEPENDIENTES_DE_POLICY = (ErrorCode.NO_FACE, ErrorCode.UNREADABLE_IMAGE)


class EmbeddingStore:
    def __init__(self, db_path: Path | str = INDEX_DB,
                 emb_dir: Path | str = EMBEDDINGS_DIR):
        ensure_dirs()
        self.db_path = Path(db_path)
        self.emb_dir = Path(emb_dir)
        self.emb_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # timeout: ya no hay un solo escritor. Una indexacion de horas escribe
        # sin parar mientras el API atiende busquedas y el historial, y una
        # migracion pide lock exclusivo. Sin esto, cualquiera de los dos
        # revienta al instante con "database is locked" en vez de esperar su
        # turno unos segundos.
        self.conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(ESQUEMA)
        self.conn.commit()
        self._migrar_rec_model_sha256_en_fallos()
        self._migrar_columna("embeddings", "margen_agregado", "REAL")
        self._stat_pendientes = 0

    def _migrar_columna(self, tabla: str, columna: str, tipo: str) -> None:
        """ALTER TABLE ADD COLUMN sobre datos existentes. Las filas viejas
        quedan en NULL, que es honesto: de esas no sabemos si hizo falta
        margen porque se extrajeron antes de que existiera la idea."""
        cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({tabla})")}
        if columna not in cols:
            self.conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
            self.conn.commit()

    def _migrar_rec_model_sha256_en_fallos(self) -> None:
        """Bases de datos creadas antes de la cache de fallos no tienen esta
        columna. ALTER TABLE ADD COLUMN es seguro sobre datos existentes: las
        filas viejas quedan con NULL, que simplemente nunca calza con un
        rec_model_sha256 real — se reintentan una vez mas y de ahi en
        adelante ya quedan cacheadas con la columna llena."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(fallos)")}
        if "rec_model_sha256" not in cols:
            self.conn.execute("ALTER TABLE fallos ADD COLUMN rec_model_sha256 TEXT")
            self.conn.commit()

    def close(self) -> None:
        if self._stat_pendientes:
            self.conn.commit()
            self._stat_pendientes = 0
        self.conn.close()

    # ------------------------------------------------------------ stat cache
    def sha_de(self, ruta: Path) -> str:
        """sha256 del archivo, sin releerlo si (ruta, tamaño, mtime) no cambiaron.

        Recorrer un corpus grande ya indexado costaba leer y hashear CADA
        archivo solo para descubrir que ya estaba en cache — decenas de miles
        de lecturas completas de disco por corrida. Un stat() cuesta
        microsegundos y responde lo mismo en la practica.
        """
        try:
            st = ruta.stat()
        except OSError:
            # Que el error salga de donde debe salir (extract/lectura), no aqui.
            return sha256_file(ruta)

        clave = str(ruta)
        fila = self.conn.execute(
            "SELECT sha256 FROM stat_cache WHERE source_path=? AND size=? AND mtime_ns=?",
            (clave, st.st_size, st.st_mtime_ns),
        ).fetchone()
        if fila is not None:
            return fila["sha256"]

        sha = sha256_file(ruta)
        self.conn.execute(
            "INSERT OR REPLACE INTO stat_cache (source_path, size, mtime_ns, sha256) "
            "VALUES (?,?,?,?)",
            (clave, st.st_size, st.st_mtime_ns, sha),
        )
        # Commit por lotes: un fsync por archivo sobre 85k archivos domina el
        # tiempo total. Perder los ultimos si el proceso muere es inofensivo
        # (se vuelven a hashear la proxima vez).
        self._stat_pendientes += 1
        if self._stat_pendientes >= 500:
            self.conn.commit()
            self._stat_pendientes = 0
        return sha

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- lookup
    def buscar(self, image_sha256: str, model_pack: str, rec_model_sha256: str,
               det_size: str, face_policy: str) -> sqlite3.Row | None:
        """Devuelve la fila cacheada si sigue siendo valida bajo `face_policy`.

        Un embedding guardado con face_selection='unico' vale bajo cualquier
        politica (habia un solo rostro, no hubo nada que decidir). Uno guardado
        como 'mayor_area_de_N' solo vale si hoy tambien corremos con 'largest';
        bajo 'strict' esa imagen debe volver a fallar con MULTIPLE_FACES.
        """
        fila = self.conn.execute(
            "SELECT * FROM embeddings WHERE image_sha256=? AND model_pack=? "
            "AND rec_model_sha256=? AND det_size=?",
            (image_sha256, model_pack, rec_model_sha256, det_size),
        ).fetchone()
        if fila is None:
            return None
        if fila["face_selection"] != "unico" and face_policy != FacePolicy.LARGEST:
            return None
        if not (self.emb_dir.parent.parent / fila["npy_path"]).exists() \
                and not Path(fila["npy_path"]).exists():
            return None  # el .npy se borro; hay que reprocesar
        return fila

    def buscar_fallo(self, image_sha256: str, model_pack: str, rec_model_sha256: str,
                     det_size: str, face_policy: str) -> sqlite3.Row | None:
        """Devuelve el fallo cacheado si sigue siendo valido bajo `face_policy`.

        Solo NO_FACE y UNREADABLE_IMAGE son independientes de la politica: cero
        rostros o un archivo corrupto no cambian si mañana corres con otra
        seleccion. MULTIPLE_FACES (y por las dudas INVALID_EMBEDDING) SI
        dependen — bajo 'strict' fallan, bajo 'largest' el mismo archivo
        podria tener exito con otro rostro elegido — asi que esos solo cuentan
        si la politica no cambio desde que se registro el fallo.
        """
        fila = self.conn.execute(
            "SELECT * FROM fallos WHERE image_sha256=? AND model_pack=? "
            "AND rec_model_sha256=? AND det_size=? ORDER BY created_at DESC LIMIT 1",
            (image_sha256, model_pack, rec_model_sha256, det_size),
        ).fetchone()
        if fila is None:
            return None
        if fila["error"] not in _FALLOS_INDEPENDIENTES_DE_POLICY \
                and fila["face_policy"] != face_policy:
            return None
        return fila

    def cargar_embedding(self, fila: sqlite3.Row | dict) -> np.ndarray:
        p = Path(fila["npy_path"])
        if not p.is_absolute():
            p = self.emb_dir.parent.parent / p
        return np.load(p).astype(np.float32).ravel()

    # ---------------------------------------------------------------- escritura
    def guardar(self, resultado: dict[str, Any], runtime, face_policy: str) -> Path:
        """Persiste el .npy y la fila de metadata. Devuelve la ruta del .npy."""
        fp = runtime.fingerprint
        sha = resultado["image_sha256"]
        destino_dir = self.emb_dir / fp.model_pack
        destino_dir.mkdir(parents=True, exist_ok=True)
        npy = destino_dir / f"{sha}.npy"
        np.save(npy, resultado["embedding"].astype(np.float32))

        try:
            npy_guardado = str(npy.relative_to(self.emb_dir.parent.parent))
        except ValueError:
            npy_guardado = str(npy)

        x, y, w, h = resultado["bbox"]
        self.conn.execute(
            """INSERT OR REPLACE INTO embeddings (
                image_sha256, source_path, npy_path, embedding_dim, det_score,
                bbox_x, bbox_y, bbox_w, bbox_h, n_faces_detected, face_policy,
                face_selection, all_det_scores, exif_applied, margen_agregado,
                model_pack, rec_model_file, rec_model_sha256, det_model_file,
                det_model_sha256, det_size, provider, insightface_version,
                onnxruntime_version, facid_version, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sha, resultado["source_path"], npy_guardado,
                int(resultado["embedding"].size), float(resultado["det_score"]),
                x, y, w, h, int(resultado["n_faces_detected"]), face_policy,
                resultado["face_selection"],
                json.dumps(resultado.get("all_det_scores")),
                1 if resultado.get("exif_orientation_applied") else 0,
                float(resultado.get("margen_agregado") or 0.0),
                fp.model_pack, runtime.rec_model_file, runtime.rec_model_sha256,
                runtime.det_model_file, runtime.det_model_sha256,
                fp.det_size, runtime.provider_activo, fp.insightface_version,
                fp.onnxruntime_version, fp.facid_version, utc_now_iso(),
            ),
        )
        self.conn.commit()
        return npy

    def registrar_fallo(self, resultado: dict[str, Any], runtime, face_policy: str) -> None:
        fp = runtime.fingerprint
        self.conn.execute(
            """INSERT INTO fallos (image_sha256, source_path, error, error_message,
                   n_faces_detected, all_det_scores, face_policy, model_pack,
                   rec_model_sha256, det_size, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                resultado.get("image_sha256"), resultado["source_path"],
                resultado["error"], resultado.get("error_message"),
                resultado.get("n_faces_detected"),
                json.dumps(resultado.get("all_det_scores")),
                face_policy, fp.model_pack, runtime.rec_model_sha256,
                fp.det_size, utc_now_iso(),
            ),
        )
        self.conn.commit()

    # ---------------------------------------------------------------- reporte
    def resumen(self) -> dict[str, Any]:
        n = self.conn.execute("SELECT COUNT(*) c FROM embeddings").fetchone()["c"]
        f = self.conn.execute("SELECT COUNT(*) c FROM fallos").fetchone()["c"]
        packs = [dict(r) for r in self.conn.execute(
            "SELECT model_pack, det_size, provider, COUNT(*) n FROM embeddings "
            "GROUP BY model_pack, det_size, provider")]
        return {"embeddings": n, "fallos": f, "por_configuracion": packs}

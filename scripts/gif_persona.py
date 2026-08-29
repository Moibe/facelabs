#!/usr/bin/env python
"""Arma un GIF animado con los rostros indexados de una persona del corpus.

Vive en scripts/ y no en el CLI de facid a proposito: `facid` es el
instrumento de medicion (extraer, comparar, calibrar) y esto es una utilidad
de visualizacion. Meterlo ahi ensuciaria esa frontera.

Usa las fotos que TIENEN rostro detectado (las que estan en embeddings), no
todo el contenido de la carpeta: un GIF con los recortes que dieron NO_FACE
mostraria nucas y manos entre las caras.

    python scripts/gif_persona.py land_of_oz
    python scripts/gif_persona.py land_of_oz --fps 12 --lado 200 -o mi.gif
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from facid.config import CORPUS_DIR, INDEX_DB  # noqa: E402


def _clave_orden(p: Path) -> tuple:
    """Ordena 122943-0.jpg antes que 9524-0.jpg por NUMERO, no por texto.

    Alfabeticamente '122943' < '9524', que dejaria el GIF en un orden que no
    corresponde a como se fueron capturando las fotos.
    """
    nums = [int(x) for x in re.findall(r"\d+", p.name)]
    return (nums or [0], p.name)


def rostros_de(persona: str, corpus: Path, db: Path) -> list[Path]:
    prefijo = str(corpus / persona)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        filas = con.execute(
            """SELECT source_path, MAX(created_at) FROM embeddings
               WHERE substr(source_path,1,?)=?
               GROUP BY image_sha256""",
            (len(prefijo), prefijo),
        ).fetchall()
    finally:
        con.close()
    rutas = [Path(f["source_path"]) for f in filas]
    return sorted((p for p in rutas if p.is_file()), key=_clave_orden)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("persona", help="Carpeta del corpus, ej: land_of_oz")
    ap.add_argument("-o", "--salida", default=None,
                    help="Archivo .gif (default: out/<persona>.gif)")
    ap.add_argument("--fps", type=float, default=10.0, help="Cuadros por segundo (%(default)s)")
    ap.add_argument("--lado", type=int, default=0,
                    help="Redimensionar a NxN px. 0 = dejar el tamaño original.")
    ap.add_argument("--limite", type=int, default=0,
                    help="Usar solo los primeros N rostros. 0 = todos.")
    ap.add_argument("--corpus", default=None, help="Carpeta del corpus (default: FACID_CORPUS)")
    args = ap.parse_args()

    corpus = Path(args.corpus) if args.corpus else CORPUS_DIR
    rutas = rostros_de(args.persona, corpus, INDEX_DB)
    if not rutas:
        print(f"[X] no hay rostros indexados de {args.persona!r} en {corpus}", file=sys.stderr)
        print("    ¿Ya corriste el indexado? Revisa con: python -m facid cobertura",
              file=sys.stderr)
        return 2
    if args.limite:
        rutas = rutas[: args.limite]

    from PIL import Image

    cuadros = []
    ilegibles = 0
    for p in rutas:
        try:
            im = Image.open(p)
            im.load()                      # forzar lectura antes de cerrar el archivo
            im = im.convert("RGB")
        except Exception:
            ilegibles += 1
            continue
        if args.lado:
            im = im.resize((args.lado, args.lado), Image.LANCZOS)
        cuadros.append(im)

    if not cuadros:
        print("[X] ninguna imagen se pudo leer", file=sys.stderr)
        return 1

    salida = Path(args.salida) if args.salida else (RAIZ / "out" / f"{args.persona}.gif")
    salida.parent.mkdir(parents=True, exist_ok=True)
    duracion = max(20, round(1000 / args.fps))   # ms por cuadro; <20ms no se respeta

    cuadros[0].save(
        salida, save_all=True, append_images=cuadros[1:],
        duration=duracion, loop=0, optimize=True,
    )

    mb = salida.stat().st_size / 1024 / 1024
    print(f"[gif] {len(cuadros)} rostros de {args.persona}")
    if ilegibles:
        print(f"[gif] {ilegibles} archivo(s) no se pudieron leer y se omitieron")
    print(f"[gif] {args.fps} fps -> {len(cuadros) / args.fps:.0f}s de animacion")
    print(f"[gif] escrito: {salida}  ({mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

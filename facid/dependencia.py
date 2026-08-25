"""Estructura de dependencia entre pares: cuantas observaciones REALES hay.

Los intervalos Clopper-Pearson de calibrate.py asumen que cada par es una
observacion independiente. En un set de verificacion facial eso es falso: los
pares se construyen combinando un punado de fotos, y una sola foto mala arrastra
todos los pares en los que participa.

Ejemplo real del set sugerido en el handoff: la foto ancla aparece en 5 de los
8 pares match. Si esa foto salio con mala luz, no falla un par: fallan cinco,
juntos. Tratarlos como 8 observaciones independientes reporta un intervalo mas
angosto que el verdadero, o sea presume mas certeza de la que hay.

Este modulo no arregla el intervalo (con 3-4 personas no hay forma honesta de
hacerlo). Hace algo mas util: mide y declara la dependencia, y estima cuanto se
mueve el resultado si quitas a una persona del set.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def _clave_img(fila: dict, lado: str) -> str:
    """Identidad canonica de una imagen dentro del CSV.

    Prefiere el sha256 (dos rutas distintas al mismo archivo son la MISMA foto);
    cae a la ruta si el CSV no trae la columna, o si lo que trae no parece un
    hash. Ese segundo caso importa: un CSV escrito a mano con la columna
    rellenada de cualquier cosa colapsaria fotos distintas en una sola clave, y
    el conteo de identidades saldria mal SIN avisar. Mejor ignorar un sha dudoso
    y usar la ruta, que al menos distingue archivos.
    """
    sha = (fila.get(f"sha256_{lado}") or "").strip().lower()
    if len(sha) >= 8 and all(c in "0123456789abcdef" for c in sha):
        return sha
    return (fila.get(f"img_{lado}") or "").strip()


def _etiqueta_img(fila: dict, lado: str) -> str:
    return (fila.get(f"img_{lado}") or "").strip()


class _UnionFind:
    def __init__(self):
        self.padre: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.padre.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        raiz = x
        while self.padre[raiz] != raiz:
            raiz = self.padre[raiz]
        while self.padre[x] != raiz:      # compresion de caminos
            self.padre[x], x = raiz, self.padre[x]
        return raiz

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.padre[ra] = rb


def estructura(filas_match: list[dict], filas_nonmatch: list[dict]) -> dict[str, Any]:
    """Deduce identidades y mide el reuso de fotos.

    Las identidades salen de los propios pares: si dos fotos estan unidas por un
    par `same_person: true`, son la misma persona. Transitivamente, eso agrupa
    todas las fotos de cada quien sin que nadie tenga que declararlo aparte.
    """
    uf = _UnionFind()
    etiquetas: dict[str, str] = {}
    pares: list[tuple[str, str, bool]] = []

    for filas, es_match in ((filas_match, True), (filas_nonmatch, False)):
        for f in filas:
            a, b = _clave_img(f, "a"), _clave_img(f, "b")
            uf.add(a)
            uf.add(b)
            etiquetas.setdefault(a, _etiqueta_img(f, "a"))
            etiquetas.setdefault(b, _etiqueta_img(f, "b"))
            if es_match:
                uf.union(a, b)          # solo los match agrupan
            pares.append((a, b, es_match))

    identidad = {img: uf.find(img) for img in uf.padre}
    grupos: dict[str, list[str]] = {}
    for img, raiz in identidad.items():
        grupos.setdefault(raiz, []).append(img)

    uso: Counter[str] = Counter()
    for a, b, _ in pares:
        uso[a] += 1
        uso[b] += 1

    # Contradiccion de etiquetado: un par dice "personas distintas" pero los
    # pares match conectan esas dos fotos como la misma persona. Es un error de
    # captura, y sin avisarlo envenena la calibracion en silencio.
    contradicciones = [
        (etiquetas.get(a, a), etiquetas.get(b, b))
        for a, b, es_match in pares
        if not es_match and identidad[a] == identidad[b]
    ]

    n_pares = len(pares)
    n_imgs = len(identidad)
    mas_usada = uso.most_common(1)[0] if uso else ("", 0)

    return {
        "n_pares": n_pares,
        "n_imagenes": n_imgs,
        "n_identidades": len(grupos),
        "identidad_por_imagen": identidad,
        "grupos": grupos,
        "etiquetas": etiquetas,
        "uso": uso,
        "img_mas_usada": etiquetas.get(mas_usada[0], mas_usada[0]),
        "reuso_max": mas_usada[1],
        "reuso_medio": (2 * n_pares / n_imgs) if n_imgs else 0.0,
        "contradicciones": contradicciones,
        "pares": pares,
    }


def jackknife_por_persona(filas_match: list[dict], filas_nonmatch: list[dict],
                          est: dict[str, Any], objetivo_fmr: float = 0.0
                          ) -> list[dict[str, Any]]:
    """Quita a UNA persona completa y recalcula el umbral. Una vez por persona.

    Responde la pregunta que un intervalo de confianza no contesta cuando los
    pares estan correlacionados: *cuanto de este resultado lo esta decidiendo
    una sola persona del set*. Si al sacar a alguien el umbral se mueve mucho,
    el numero no describe tu sistema: describe a esa persona.
    """
    from .calibrate import punto_operacion
    import numpy as np

    identidad = est["identidad_por_imagen"]
    filas_por_par = list(filas_match) + list(filas_nonmatch)
    es_match_por_par = [True] * len(filas_match) + [False] * len(filas_nonmatch)

    ident_de_par = []
    for f in filas_por_par:
        a, b = _clave_img(f, "a"), _clave_img(f, "b")
        ident_de_par.append({identidad[a], identidad[b]})

    salidas = []
    for raiz, imgs in sorted(est["grupos"].items(),
                             key=lambda kv: -len(kv[1])):
        m, nm = [], []
        n_fuera = 0
        for f, es_m, idents in zip(filas_por_par, es_match_por_par, ident_de_par):
            if raiz in idents:
                n_fuera += 1
                continue
            s = float(f["score"])
            (m if es_m else nm).append(s)

        # El nombre de la persona sale de su CARPETA (data/yo/01.jpg -> "yo"),
        # que es como el usuario organiza el set. Si las rutas vienen planas,
        # cae al nombre del archivo: es lo unico que hay para identificarla.
        from pathlib import PurePosixPath
        ruta = PurePosixPath(est["etiquetas"].get(imgs[0], raiz).replace("\\", "/"))
        fila = {
            "persona": ruta.parent.name or ruta.stem,
            "n_fotos": len(imgs),
            "pares_excluidos": n_fuera,
            "threshold": None, "fmr": None, "fnmr": None,
        }
        if m and nm:
            r = punto_operacion(np.array(m), np.array(nm), objetivo_fmr=objetivo_fmr)
            if r and r.get("alcanzable"):
                fila.update(threshold=r["threshold"], fmr=r["fmr"], fnmr=r["fnmr"])
        salidas.append(fila)
    return salidas

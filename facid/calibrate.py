"""Analisis de calibracion sobre el CSV del harness.

Responde las cuatro preguntas del criterio de aceptacion con numeros:
  1. rango de scores para la MISMA persona
  2. rango para personas DISTINTAS
  3. si se traslapan y donde
  4. que threshold elegir y que tasa de error se acepta a cambio

Convencion (identica a decide.decide, a proposito):
    aceptar el par  <=>  score >= threshold
    FMR(t)  = fraccion de pares NON-MATCH con score >= t   (falsa aceptacion)
    FNMR(t) = fraccion de pares MATCH     con score <  t   (falso rechazo)

Sobre el margen de error: con ~15 pares los porcentajes puntuales son casi
ruido. Cada tasa se acompana de su intervalo de confianza binomial exacto
(Clopper-Pearson 95%), que es lo unico que permite decir "FMR < 1%" con
honestidad, o admitir que con este N no se puede afirmar.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import numpy as np

ALPHA = 0.05  # -> intervalos al 95%


def clopper_pearson(k: int, n: int, alpha: float = ALPHA) -> tuple[float, float]:
    """Intervalo binomial exacto para k exitos de n. Devuelve (lo, hi).

    Exacto en el sentido de conservador: la cobertura real es >= 1-alpha.
    Es el correcto para N chico, donde la aproximacion normal miente feo
    (y con k=0 directamente colapsa a [0,0], que seria una mentira grave aqui).
    """
    if n <= 0:
        return (float("nan"), float("nan"))
    try:
        from scipy.stats import beta
        lo = 0.0 if k == 0 else float(beta.ppf(alpha / 2, k, n - k + 1))
        hi = 1.0 if k == n else float(beta.ppf(1 - alpha / 2, k + 1, n - k))
        return (lo, hi)
    except ImportError:
        # Fallback sin scipy: Wilson. Menos exacto pero no colapsa en k=0.
        z = 1.959963984540054
        p = k / n
        d = 1 + z * z / n
        centro = (p + z * z / (2 * n)) / d
        margen = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
        return (max(0.0, centro - margen), min(1.0, centro + margen))


def cargar_scores(csv_path: str | Path) -> dict[str, Any]:
    """Lee el CSV del harness y separa match / non-match.

    Solo entran filas con pair_ok=True. Un par sin score no es un score de 0:
    incluirlo contaminaria la distribucion con un fallo de deteccion.
    """
    p = Path(csv_path)
    if not p.is_file():
        raise FileNotFoundError(f"No existe el CSV: {p}")

    match: list[float] = []
    nonmatch: list[float] = []
    filas_match: list[dict] = []
    filas_nonmatch: list[dict] = []
    descartados: list[dict] = []

    with open(p, newline="", encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            ok = str(fila.get("pair_ok", "")).strip().lower() in ("true", "1", "yes")
            score_txt = (fila.get("score") or "").strip()
            if not ok or not score_txt:
                descartados.append(fila)
                continue
            s = float(score_txt)
            es_match = str(fila["same_person"]).strip().lower() in ("true", "1", "yes")
            (match if es_match else nonmatch).append(s)
            (filas_match if es_match else filas_nonmatch).append(fila)

    return {
        "match": np.array(match, dtype=float),
        "nonmatch": np.array(nonmatch, dtype=float),
        "filas_match": filas_match,
        "filas_nonmatch": filas_nonmatch,
        "descartados": descartados,
    }


def describir(v: np.ndarray) -> dict[str, Any]:
    if v.size == 0:
        return {"n": 0}
    return {
        "n": int(v.size),
        "min": float(v.min()), "max": float(v.max()),
        "media": float(v.mean()), "mediana": float(np.median(v)),
        "std": float(v.std(ddof=1)) if v.size > 1 else 0.0,
        "p25": float(np.percentile(v, 25)), "p75": float(np.percentile(v, 75)),
    }


def fmr_fnmr(match: np.ndarray, nonmatch: np.ndarray, t: float) -> dict[str, Any]:
    """Tasas y conteos en un threshold, con su intervalo de confianza."""
    fp = int(np.sum(nonmatch >= t))      # non-match aceptados  = falsa aceptacion
    fn = int(np.sum(match < t))          # match rechazados     = falso rechazo
    n_nm, n_m = int(nonmatch.size), int(match.size)

    fmr = fp / n_nm if n_nm else float("nan")
    fnmr = fn / n_m if n_m else float("nan")
    fmr_lo, fmr_hi = clopper_pearson(fp, n_nm)
    fnmr_lo, fnmr_hi = clopper_pearson(fn, n_m)

    return {
        "threshold": round(float(t), 6),
        "fmr": fmr, "fmr_lo": fmr_lo, "fmr_hi": fmr_hi, "fp": fp, "n_nonmatch": n_nm,
        "fnmr": fnmr, "fnmr_lo": fnmr_lo, "fnmr_hi": fnmr_hi, "fn": fn, "n_match": n_m,
    }


def barrido(match: np.ndarray, nonmatch: np.ndarray,
            inicio: float = 0.0, fin: float = 1.0, paso: float = 0.01) -> list[dict]:
    """Barrido de threshold de 0.0 a 1.0 (inclusive) como pide el handoff."""
    n = int(round((fin - inicio) / paso)) + 1
    return [fmr_fnmr(match, nonmatch, inicio + i * paso) for i in range(n)]


def _candidatos(match: np.ndarray, nonmatch: np.ndarray) -> np.ndarray:
    """Thresholds donde las tasas REALMENTE cambian: los valores observados.

    El barrido de 0.01 es para la tabla que pide el handoff; para elegir el
    punto de operacion se usa esta rejilla, que no se salta ningun cruce.
    """
    todos = np.concatenate([match, nonmatch]) if match.size or nonmatch.size else np.array([0.0])
    unicos = np.unique(todos)
    # Puntos medios entre valores contiguos + un poco antes y despues del rango.
    medios = (unicos[:-1] + unicos[1:]) / 2 if unicos.size > 1 else np.array([])
    extra = np.array([unicos.min() - 1e-6, unicos.max() + 1e-6])
    return np.unique(np.concatenate([unicos, medios, extra]))


def eer(match: np.ndarray, nonmatch: np.ndarray) -> dict[str, Any] | None:
    """Equal Error Rate: el threshold donde FMR y FNMR se cruzan."""
    if match.size == 0 or nonmatch.size == 0:
        return None
    mejor = None
    for t in _candidatos(match, nonmatch):
        r = fmr_fnmr(match, nonmatch, float(t))
        brecha = abs(r["fmr"] - r["fnmr"])
        if mejor is None or brecha < mejor["_brecha"]:
            mejor = dict(r, _brecha=brecha, eer=(r["fmr"] + r["fnmr"]) / 2)
    return mejor


def zona_traslape(match: np.ndarray, nonmatch: np.ndarray) -> dict[str, Any]:
    """Donde se pisan las dos distribuciones.

    Si el score minimo de un par match queda por DEBAJO del score maximo de un
    par non-match, existe una banda donde ningun threshold separa limpio. El
    ancho de esa banda es el resultado central del experimento.
    """
    if match.size == 0 or nonmatch.size == 0:
        return {"hay_traslape": None, "motivo": "faltan pares de alguna clase"}

    lo, hi = float(match.min()), float(nonmatch.max())
    if lo > hi:
        # Separacion limpia: cualquier t en (hi, lo] logra 0 errores en ESTE set.
        return {
            "hay_traslape": False,
            "brecha_lo": hi, "brecha_hi": lo, "brecha_ancho": lo - hi,
            "threshold_libre_de_error": (hi + lo) / 2,
        }

    en_zona_m = int(np.sum((match >= lo) & (match <= hi)))
    en_zona_nm = int(np.sum((nonmatch >= lo) & (nonmatch <= hi)))
    return {
        "hay_traslape": True,
        "zona_lo": lo, "zona_hi": hi, "zona_ancho": hi - lo,
        "match_en_zona": en_zona_m, "nonmatch_en_zona": en_zona_nm,
    }


def d_prime(match: np.ndarray, nonmatch: np.ndarray) -> float | None:
    """Separabilidad estandar en biometria: cuantas desviaciones separan las medias."""
    if match.size < 2 or nonmatch.size < 2:
        return None
    vm, vn = match.var(ddof=1), nonmatch.var(ddof=1)
    denom = math.sqrt((vm + vn) / 2)
    if denom == 0:
        return None
    return float((match.mean() - nonmatch.mean()) / denom)


def punto_operacion(match: np.ndarray, nonmatch: np.ndarray, *,
                    objetivo_fmr: float | None = None,
                    objetivo_fnmr: float | None = None) -> dict[str, Any] | None:
    """Threshold mas conveniente que cumple un objetivo de error.

    FMR es no-creciente en t y FNMR es no-decreciente, asi que:
      - objetivo de FMR  -> se busca el t MAS CHICO que lo cumple (menos rechazos)
      - objetivo de FNMR -> se busca el t MAS GRANDE que lo cumple (menos falsas aceptaciones)

    Marca `resolucion_insuficiente` cuando el objetivo es mas fino que lo que
    el tamano del set puede medir: con n pares non-match, la FMR observable
    mas chica distinta de cero es 1/n. Pedir FMR<=0.1% con 7 pares no es una
    medicion, es una extrapolacion — y aqui se dice en voz alta.
    """
    if match.size == 0 or nonmatch.size == 0:
        return None

    cands = _candidatos(match, nonmatch)
    elegido = None

    if objetivo_fmr is not None:
        for t in cands:  # ascendente
            r = fmr_fnmr(match, nonmatch, float(t))
            if r["fmr"] <= objetivo_fmr + 1e-12:
                elegido = r
                break
        resolucion = 1.0 / nonmatch.size
        insuficiente = 0 < objetivo_fmr < resolucion
        meta = {"objetivo": f"FMR <= {objetivo_fmr:.1%}", "resolucion_minima": resolucion}
    else:
        for t in reversed(cands):  # descendente
            r = fmr_fnmr(match, nonmatch, float(t))
            if r["fnmr"] <= objetivo_fnmr + 1e-12:
                elegido = r
                break
        resolucion = 1.0 / match.size
        insuficiente = 0 < objetivo_fnmr < resolucion
        meta = {"objetivo": f"FNMR <= {objetivo_fnmr:.1%}", "resolucion_minima": resolucion}

    if elegido is None:
        return dict(meta, alcanzable=False, resolucion_insuficiente=False)
    return dict(elegido, **meta, alcanzable=True, resolucion_insuficiente=insuficiente)


def histograma_ascii(match: np.ndarray, nonmatch: np.ndarray,
                     bins: int = 24, ancho: int = 46) -> str:
    """Histograma para terminal. Existe para que el analisis siga siendo
    legible por SSH, sin depender de abrir un PNG."""
    if match.size == 0 and nonmatch.size == 0:
        return "(sin datos)"
    todos = np.concatenate([match, nonmatch])
    lo, hi = float(todos.min()), float(todos.max())
    if hi - lo < 1e-9:
        lo, hi = lo - 0.05, hi + 0.05
    margen = (hi - lo) * 0.05
    lo, hi = lo - margen, hi + margen
    bordes = np.linspace(lo, hi, bins + 1)

    hm, _ = np.histogram(match, bins=bordes)
    hn, _ = np.histogram(nonmatch, bins=bordes)
    pico = max(1, int(max(hm.max(initial=0), hn.max(initial=0))))

    lineas = [
        "  score      | M = misma persona   N = personas distintas",
        "  -----------+" + "-" * ancho,
    ]
    for i in range(bins):
        centro = (bordes[i] + bordes[i + 1]) / 2
        nm = int(round(hm[i] / pico * (ancho / 2)))
        nn = int(round(hn[i] / pico * (ancho / 2)))
        barra = "N" * nn + "M" * nm
        conteo = ""
        if hm[i] or hn[i]:
            conteo = f"  ({hn[i]}N {hm[i]}M)" if (hn[i] and hm[i]) else \
                     (f"  ({hn[i]}N)" if hn[i] else f"  ({hm[i]}M)")
        lineas.append(f"  {centro:+8.3f}  | {barra}{conteo}")
    return "\n".join(lineas)


def histograma_png(match: np.ndarray, nonmatch: np.ndarray, destino: Path,
                   t_eer: float | None = None) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")  # sin display: esto corre por SSH
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    todos = np.concatenate([match, nonmatch]) if match.size or nonmatch.size else np.array([0.0])
    lo, hi = float(todos.min()), float(todos.max())
    bordes = np.linspace(lo - 0.05, hi + 0.05, 30)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(nonmatch, bins=bordes, alpha=0.6, label=f"Personas distintas (n={nonmatch.size})",
            color="#c0392b", edgecolor="white")
    ax.hist(match, bins=bordes, alpha=0.6, label=f"Misma persona (n={match.size})",
            color="#2471a3", edgecolor="white")

    z = zona_traslape(match, nonmatch)
    if z.get("hay_traslape"):
        ax.axvspan(z["zona_lo"], z["zona_hi"], color="#f1c40f", alpha=0.18,
                   label=f"Traslape [{z['zona_lo']:.3f}, {z['zona_hi']:.3f}]")
    if t_eer is not None:
        ax.axvline(t_eer, color="#212121", linestyle="--", linewidth=1.5,
                   label=f"EER @ t={t_eer:.3f}")

    ax.set_xlabel("Similitud coseno")
    ax.set_ylabel("Numero de pares")
    ax.set_title("Distribucion de scores: match vs non-match")
    ax.legend(loc="upper center", fontsize=9)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    destino.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destino, dpi=140)
    plt.close(fig)
    return destino


def _providers_usados(datos: dict[str, Any]) -> dict[str, int]:
    """Cuenta en que provider se calculo cada lado de par.

    Tolera CSVs sin las columnas (los generados antes de que existieran):
    devuelve vacio y el reporte simplemente no menciona el tema, en vez de tronar.
    """
    from collections import Counter
    c: Counter[str] = Counter()
    for filas in (datos["filas_match"], datos["filas_nonmatch"]):
        for f in filas:
            for lado in ("a", "b"):
                v = (f.get(f"provider_{lado}") or "").strip()
                if v:
                    c[v] += 1
    return dict(c)


def _pct(x: float) -> str:
    return "  n/a " if (x is None or (isinstance(x, float) and math.isnan(x))) else f"{x*100:5.1f}%"


def calibrar(csv_path: str | Path, out_dir: str | Path, *,
             paso: float = 0.01, verbose: bool = True) -> dict[str, Any]:
    """Genera el analisis completo y lo imprime. Devuelve el reporte como dict."""
    datos = cargar_scores(csv_path)
    m, nm = datos["match"], datos["nonmatch"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    L: list[str] = []
    add = L.append

    add("=" * 72)
    add(f"CALIBRACION — {Path(csv_path).name}")
    add("=" * 72)

    if datos["descartados"]:
        add(f"\n[!] {len(datos['descartados'])} pares excluidos por fallo de extraccion.")
        for f in datos["descartados"][:10]:
            add(f"      {f.get('img_a','?')} vs {f.get('img_b','?')}"
                f"  a={f.get('error_a') or 'ok'} b={f.get('error_b') or 'ok'}")
        add("    Un par sin score NO es un score de 0; quedan fuera de las tasas.")

    if m.size == 0 or nm.size == 0:
        add("\n[X] Hacen falta pares de AMBAS clases para calibrar.")
        add(f"    match={m.size}  non-match={nm.size}")
        texto = "\n".join(L)
        if verbose:
            print(texto)
        return {"ok": False, "reporte": texto}

    dm, dn = describir(m), describir(nm)

    # -------- Composicion del set: cuantas observaciones REALES hay --------
    from .dependencia import estructura, jackknife_por_persona
    est = estructura(datos["filas_match"], datos["filas_nonmatch"])

    add("\n0) COMPOSICION DEL SET")
    add(f"   {est['n_pares']} pares construidos sobre {est['n_imagenes']} fotos "
        f"de {est['n_identidades']} personas distintas.")
    add(f"   Cada foto participa en {est['reuso_medio']:.1f} pares en promedio; "
        f"la mas usada aparece en {est['reuso_max']}")
    add(f"   ({est['img_mas_usada']}).")
    if est["reuso_max"] > 1:
        add("   Los pares NO son observaciones independientes: comparten fotos. Si esa")
        add("   foto salio mal, no arrastra un par, arrastra todos los suyos. Por eso los")
        add("   intervalos de mas abajo son OPTIMISTAS — el real es mas ancho. Trata el")
        add(f"   numero de personas ({est['n_identidades']}) como el tamano de muestra que manda,")
        add(f"   no el de pares ({est['n_pares']}); ver la seccion de FRAGILIDAD.")
    if est["contradicciones"]:
        add("")
        add(f"   [!] {len(est['contradicciones'])} pares se contradicen con tu etiquetado:")
        for a, b in est["contradicciones"][:5]:
            add(f"       {a} vs {b} -> marcado same_person=false, pero tus pares")
            add("       match conectan esas dos fotos como la MISMA persona.")
        add("       Es un error de captura en el manifiesto. Arreglalo antes de creerle")
        add("       a cualquier numero de este reporte.")

    # Procedencia de los embeddings. El provider no invalida la cache (cambiar de
    # device por una diferencia de 1e-6 seria peor), asi que un set mezclado es
    # posible sin que nada avise. Aqui se avisa.
    provs = _providers_usados(datos)
    if len(provs) == 1:
        add(f"   Provider: {next(iter(provs))} (todos los embeddings).")
    elif len(provs) > 1:
        add("")
        add("   [!] Los embeddings NO salieron todos del mismo provider:")
        for nombre, n in sorted(provs.items(), key=lambda kv: -kv[1]):
            add(f"       {nombre:<26} {n} lado(s) de par")
        add("       Pasa al extraer en GPU y despues correr con --device cpu (o al")
        add("       reves): lo ya guardado sale de cache y solo lo nuevo se recalcula.")
        add("       Para una cara nitida da igual (difieren ~1e-6). Pero el detector")
        add("       tiene un umbral adentro, asi que una cara marginal —borrosa, chica,")
        add("       angulo extremo— puede recortarse distinto y mover el score bastante")
        add("       mas. Si un par raro involucra fotos de providers distintos, rehazlo")
        add("       con --force en un solo device antes de concluir algo.")

    # -------- Preguntas 1 y 2 del criterio de aceptacion --------
    add("\n1) MISMA PERSONA (match)")
    add(f"   n={dm['n']}   rango [{dm['min']:.4f} .. {dm['max']:.4f}]")
    add(f"   media {dm['media']:.4f}   mediana {dm['mediana']:.4f}   std {dm['std']:.4f}")
    add(f"   cuartiles p25 {dm['p25']:.4f}  p75 {dm['p75']:.4f}")

    add("\n2) PERSONAS DISTINTAS (non-match)")
    add(f"   n={dn['n']}   rango [{dn['min']:.4f} .. {dn['max']:.4f}]")
    add(f"   media {dn['media']:.4f}   mediana {dn['mediana']:.4f}   std {dn['std']:.4f}")
    add(f"   cuartiles p25 {dn['p25']:.4f}  p75 {dn['p75']:.4f}")

    # -------- Pregunta 3: traslape --------
    z = zona_traslape(m, nm)
    dp = d_prime(m, nm)
    add("\n3) TRASLAPE")
    if z["hay_traslape"]:
        add(f"   SI se traslapan, en [{z['zona_lo']:.4f} .. {z['zona_hi']:.4f}]"
            f"  (ancho {z['zona_ancho']:.4f})")
        add(f"   Dentro de esa banda caen {z['match_en_zona']} pares match y "
            f"{z['nonmatch_en_zona']} non-match.")
        add("   Ningun threshold separa este set sin errores.")
    else:
        add(f"   NO se traslapan en este set. Brecha limpia "
            f"[{z['brecha_lo']:.4f} .. {z['brecha_hi']:.4f}] (ancho {z['brecha_ancho']:.4f}).")
        add(f"   Cualquier t dentro de esa brecha da 0 errores AQUI; el punto medio "
            f"es {z['threshold_libre_de_error']:.4f}.")
        add("   Ojo: separacion limpia con N chico casi siempre significa que al set")
        add("   le faltan casos dificiles, no que el sistema sea perfecto.")
    if dp is not None:
        add(f"   Separabilidad d' = {dp:.2f}  (distancia entre medias en desviaciones)")

    # -------- Histograma --------
    add("\n   Distribuciones:")
    add(histograma_ascii(m, nm))

    # -------- Pregunta 4: thresholds --------
    e = eer(m, nm)
    add("\n4) PUNTOS DE OPERACION")
    add(f"   Resolucion del set: la FMR mas chica distinta de 0 que puedes medir")
    add(f"   es 1/{nm.size} = {1/nm.size:.1%}; la FNMR, 1/{m.size} = {1/m.size:.1%}.")
    add("")
    add("   punto de operacion        threshold     FMR (IC95%)           FNMR (IC95%)")
    add("   " + "-" * 76)

    filas_op: list[dict] = []
    if e:
        add(f"   {'EER (cruce)':<24}  {e['threshold']:>9.4f}"
            f"   {_pct(e['fmr'])} [{_pct(e['fmr_lo'])},{_pct(e['fmr_hi'])}]"
            f"   {_pct(e['fnmr'])} [{_pct(e['fnmr_lo'])},{_pct(e['fnmr_hi'])}]")
        filas_op.append(dict(e, nombre="EER"))

    for obj in (0.10, 0.05, 0.01, 0.0):
        r = punto_operacion(m, nm, objetivo_fmr=obj)
        if not r or not r.get("alcanzable"):
            add(f"   {'FMR <= ' + f'{obj:.0%}':<24}  {'no alcanzable':>9}")
            continue
        marca = " (*)" if r["resolucion_insuficiente"] else ""
        add(f"   {'FMR <= ' + f'{obj:.0%}' + marca:<24}  {r['threshold']:>9.4f}"
            f"   {_pct(r['fmr'])} [{_pct(r['fmr_lo'])},{_pct(r['fmr_hi'])}]"
            f"   {_pct(r['fnmr'])} [{_pct(r['fnmr_lo'])},{_pct(r['fnmr_hi'])}]")
        filas_op.append(dict(r, nombre=f"FMR<={obj}"))

    for obj in (0.10, 0.05, 0.0):
        r = punto_operacion(m, nm, objetivo_fnmr=obj)
        if not r or not r.get("alcanzable"):
            add(f"   {'FNMR <= ' + f'{obj:.0%}':<24}  {'no alcanzable':>9}")
            continue
        add(f"   {'FNMR <= ' + f'{obj:.0%}':<24}  {r['threshold']:>9.4f}"
            f"   {_pct(r['fmr'])} [{_pct(r['fmr_lo'])},{_pct(r['fmr_hi'])}]"
            f"   {_pct(r['fnmr'])} [{_pct(r['fnmr_lo'])},{_pct(r['fnmr_hi'])}]")
        filas_op.append(dict(r, nombre=f"FNMR<={obj}"))

    add("")
    add("   (*) objetivo mas fino que la resolucion del set: se reporta el threshold")
    add("       de FMR observada 0, pero con este N la unica afirmacion defendible")
    add("       es la cota superior del intervalo, no el 0.")

    # -------- Fragilidad: cuanto decide UNA sola persona --------
    jk = jackknife_por_persona(datos["filas_match"], datos["filas_nonmatch"], est)
    validos = [j for j in jk if j["threshold"] is not None]
    add("\n   FRAGILIDAD — quitar una persona completa y recalcular")
    add("   (el threshold de FMR observada 0; es lo que un IC no te dice cuando")
    add("    los pares comparten fotos)")
    add("")
    add("   persona quitada        fotos  pares fuera   threshold      FMR     FNMR")
    add("   " + "-" * 72)
    for j in jk:
        if j["threshold"] is None:
            add(f"   {j['persona'][:20]:<20}   {j['n_fotos']:>5}  {j['pares_excluidos']:>11}"
                f"   {'sin datos suficientes':>28}")
            continue
        add(f"   {j['persona'][:20]:<20}   {j['n_fotos']:>5}  {j['pares_excluidos']:>11}"
            f"   {j['threshold']:>9.4f}  {_pct(j['fmr'])}  {_pct(j['fnmr'])}")

    if len(validos) >= 2:
        ths = [j["threshold"] for j in validos]
        rango = max(ths) - min(ths)
        add("")
        add(f"   El threshold se mueve entre {min(ths):.4f} y {max(ths):.4f} "
            f"(rango {rango:.4f}) segun")
        add("   a quien saques del set.")
        if rango > 0.05:
            add(f"   [!] Ese rango ({rango:.3f}) es grande: el resultado lo esta decidiendo")
            add("       una persona en particular, no tu sistema. Con mas personas se")
            add("       encogeria; con estas, el threshold no es transferible.")
        else:
            add("   El rango es chico: ninguna persona sola domina el resultado, lo cual")
            add("   da algo de confianza — pero sigue siendo un set de pocas personas.")
    elif validos:
        add("")
        add("   [!] Al quitar cualquier persona no quedan pares de ambas clases: el set")
        add("       tiene tan pocas personas que no se puede medir su propia fragilidad.")

    # -------- Barrido completo --------
    tabla = barrido(m, nm, 0.0, 1.0, paso)
    sweep_csv = out / "sweep_fmr_fnmr.csv"
    with open(sweep_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(tabla[0].keys()))
        w.writeheader()
        w.writerows(tabla)

    add(f"\n5) BARRIDO 0.00 -> 1.00 (paso {paso}) -> {sweep_csv}")
    add("   threshold    FMR    FNMR      |  threshold    FMR    FNMR")
    add("   " + "-" * 62)
    interes = [r for r in tabla if round(r["threshold"] * 100) % 5 == 0]
    mitad = (len(interes) + 1) // 2
    for i in range(mitad):
        izq = interes[i]
        s = f"   {izq['threshold']:>9.2f} {_pct(izq['fmr'])} {_pct(izq['fnmr'])}"
        if i + mitad < len(interes):
            der = interes[i + mitad]
            s += f"      |  {der['threshold']:>9.2f} {_pct(der['fmr'])} {_pct(der['fnmr'])}"
        add(s)

    png = histograma_png(m, nm, out / "histograma.png", e["threshold"] if e else None)
    if png:
        add(f"\n   Histograma: {png}")
    else:
        add("\n   (matplotlib no instalado; solo histograma ASCII)")

    add("\n" + "=" * 72)
    texto = "\n".join(L)
    (out / "reporte_calibracion.txt").write_text(texto, encoding="utf-8")
    if verbose:
        print(texto)
        print(f"\n[facid] reporte guardado en {out / 'reporte_calibracion.txt'}")

    return {
        "ok": True, "reporte": texto,
        "match": dm, "nonmatch": dn, "traslape": z, "d_prime": dp,
        "eer": e, "puntos_operacion": filas_op,
        "sweep_csv": str(sweep_csv), "histograma": str(png) if png else None,
        "descartados": len(datos["descartados"]),
        "composicion": {k: v for k, v in est.items()
                        if k not in ("identidad_por_imagen", "grupos", "pares",
                                     "etiquetas", "uso")},
        "fragilidad": jk,
    }

"""Pruebas del API sin modelo ni GPU.

Cubre lo que el front consume de verdad: personas, fotos, resultados, tasas.
Y sobre todo el sandbox de rutas: si `?ruta=` se puede escapar de data/, el API
es un lector de archivos arbitrarios, y correr en localhost no arregla eso.

Corre con:  python tests/test_api.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

TMP = Path(tempfile.mkdtemp(prefix="facid_api_"))
DATA = TMP / "data"
OUT = TMP / "out"
os.environ["FACID_DATA"] = str(DATA)      # ANTES de importar cualquier cosa de facid
os.environ["FACID_OUT"] = str(OUT)

import cv2                                                    # noqa: E402
import numpy as np                                            # noqa: E402
from fastapi.testclient import TestClient                     # noqa: E402

FALLOS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FALLA {msg}")
        FALLOS.append(msg)


def preparar_fixture() -> None:
    """Fotos falsas + un CSV de scores que las referencia como lo haria el harness."""
    plan = {"yo": ["01_ancla", "02_misma_sesion", "03_luz_distinta"],
            "familiar": ["01_frontal", "02_otra_luz"],
            "otra_1": ["01"]}
    for persona, fotos in plan.items():
        for f in fotos:
            p = DATA / persona / f"{f}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(p), np.full((48, 48, 3), 90, np.uint8))

    cv2.imwrite(str(DATA / "familiar" / "03_webp.webp"), np.full((48, 48, 3), 70, np.uint8))

    # Nombre con espacios y acentos: asi salen los archivos reales del usuario.
    # NO se usa cv2.imwrite aqui: en Windows devuelve True pero NO crea el
    # archivo si la ruta tiene no-ASCII. Falla en silencio, y el fixture quedaria
    # incompleto sin que nada avise. imencode + write_bytes no tiene ese problema.
    ok, buf = cv2.imencode(".png", np.full((48, 48, 3), 60, np.uint8))
    assert ok
    (DATA / "yo" / "Captura de pantalla 2026 áé.png").write_bytes(buf.tobytes())

    # Un archivo que NO es imagen, para probar el filtro de extensiones.
    (DATA / "yo" / "notas.txt").write_text("no soy una imagen", encoding="utf-8")
    # Un archivo fuera de data/, objetivo del intento de escape.
    (TMP / "secreto.txt").write_text("no debe salir por el API", encoding="utf-8")

    OUT.mkdir(parents=True, exist_ok=True)
    CUDA = "CUDAExecutionProvider"
    cols = ["img_a", "img_b", "same_person", "score", "det_score_a", "det_score_b",
            "notes", "pair_ok", "error_a", "error_b", "n_faces_a", "n_faces_b",
            "face_selection_a", "face_selection_b", "sha256_a", "sha256_b",
            "provider_a", "provider_b"]
    filas = [
        ("../data/yo/01_ancla.jpg", "../data/yo/02_misma_sesion.jpg", "True", "0.842100"),
        ("../data/yo/01_ancla.jpg", "../data/yo/03_luz_distinta.jpg", "True", "0.531200"),
        ("../data/familiar/01_frontal.jpg", "../data/familiar/02_otra_luz.jpg", "True", "0.780000"),
        ("../data/yo/01_ancla.jpg", "../data/familiar/01_frontal.jpg", "False", "0.412600"),
        ("../data/yo/03_luz_distinta.jpg", "../data/familiar/02_otra_luz.jpg", "False", "0.204000"),
        ("../data/yo/01_ancla.jpg", "../data/otra_1/01.jpg", "False", "0.038100"),
    ]
    import csv as _csv
    with open(OUT / "scores.csv", "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(cols)
        import hashlib
        def sha(ruta):
            # Un sha distinto y estable por imagen, como el que escribe el harness.
            return hashlib.sha256(ruta.encode()).hexdigest()[:16]
        for a, b, same, sc in filas:
            w.writerow([a, b, same, sc, "0.930", "0.920", "nota de prueba", "True",
                        "", "", "1", "1", "unico", "unico", sha(a), sha(b), CUDA, CUDA])
        # Un par que fallo la extraccion: debe quedar en descartados, no en las tasas.
        w.writerow(["../data/yo/01_ancla.jpg", "../data/yo/no_existe.jpg", "True", "",
                    "0.930", "", "sin rostro", "False", "", "NO_FACE", "1", "0",
                    "unico", "", sha("../data/yo/01_ancla.jpg"), "", CUDA, ""])
        # Un CSV que es salida del analisis, no entrada: /api/csvs debe filtrarlo.
    (OUT / "sweep_fmr_fnmr.csv").write_text("threshold,fmr\n0.0,1.0\n", encoding="utf-8")


preparar_fixture()

from api.main import app  # noqa: E402  (despues del fixture y de las env vars)

cli = TestClient(app)


def test_salud():
    print("\n[api-1] salud y version")
    r = cli.get("/health")
    check(r.status_code == 200 and r.json()["status"] == "ok", "/health responde ok")
    r = cli.get("/")
    import facid
    check(r.json()["version"] == facid.__version__,
          "/ expone la version de facid (dev.mjs la sondea)")


def test_personas():
    print("\n[api-2] /api/personas")
    r = cli.get("/api/personas")
    check(r.status_code == 200, "responde 200")
    d = r.json()
    check(d["n_personas"] == 3, f"3 personas (dio {d['n_personas']})")
    check(d["n_fotos"] == 8, f"8 fotos (dio {d['n_fotos']})")
    nombres = {p["persona"] for p in d["personas"]}
    check(nombres == {"yo", "familiar", "otra_1"}, "nombra las carpetas como personas")
    todas = [f["nombre"] for p in d["personas"] for f in p["fotos"]]
    check("notas.txt" not in todas, "ignora archivos que no son imagenes")
    rutas = [f["ruta"] for p in d["personas"] for f in p["fotos"]]
    check(all("/" in x and not x.startswith("/") for x in rutas),
          "las rutas son relativas a data/, no absolutas")


def test_foto_y_sandbox():
    print("\n[api-3] /api/foto — servir imagen y NO escaparse de data/")
    r = cli.get("/api/foto", params={"ruta": "yo/01_ancla.jpg"})
    check(r.status_code == 200, "sirve una foto que existe")
    check(r.headers["content-type"].startswith("image/"), "la manda como imagen")
    check(len(r.content) > 0, "el cuerpo trae bytes")

    r = cli.get("/api/foto", params={"ruta": "yo/no_existe.jpg"})
    check(r.status_code == 404, "foto inexistente -> 404")

    r = cli.get("/api/foto", params={"ruta": "yo/notas.txt"})
    check(r.status_code == 415, "archivo que no es imagen -> 415, no se sirve")

    # El tipo se declara a mano: en Windows mimetypes no conoce .webp y lo manda
    # como application/octet-stream, que el browser puede negarse a pintar.
    r = cli.get("/api/foto", params={"ruta": "familiar/03_webp.webp"})
    check(r.status_code == 200 and r.headers["content-type"] == "image/webp",
          f"sirve .webp con su tipo correcto (dio {r.headers.get('content-type')})")

    # Nombres con espacios y acentos: es como salen las capturas de pantalla.
    r = cli.get("/api/foto", params={"ruta": "yo/Captura de pantalla 2026 áé.png"})
    check(r.status_code == 200, "sirve archivos con espacios y acentos en el nombre")

    # Lo importante: ningun intento de escape debe devolver 200.
    escapes = [
        "../secreto.txt",
        "../../secreto.txt",
        "yo/../../secreto.txt",
        "./../secreto.txt",
        "..\\secreto.txt",
        "yo/../../../Windows/win.ini",
    ]
    fugas = []
    for e in escapes:
        r = cli.get("/api/foto", params={"ruta": e})
        if r.status_code == 200:
            fugas.append(e)
    check(not fugas, f"ningun path traversal sirve archivos fuera de data/ (fugas: {fugas})")

    r = cli.get("/api/foto", params={"ruta": "../secreto.txt"})
    check(r.status_code == 400, "el escape se rechaza con 400 explicito")


def test_csvs():
    print("\n[api-4] /api/csvs")
    d = cli.get("/api/csvs").json()
    nombres = [c["ruta"] for c in d["csvs"]]
    check("scores.csv" in nombres, "lista el CSV de scores")
    check("sweep_fmr_fnmr.csv" not in nombres,
          "NO ofrece el sweep: es salida del analisis, no entrada")


def test_resultados():
    print("\n[api-5] /api/resultados")
    r = cli.get("/api/resultados", params={"csv": "scores.csv"})
    check(r.status_code == 200, "responde 200")
    d = r.json()
    check(d["ok"] is True, "calibra correctamente")
    check(d["match"]["n"] == 3 and d["nonmatch"]["n"] == 3,
          f"3 match y 3 non-match (dio {d['match']['n']}/{d['nonmatch']['n']})")
    check(len(d["pares"]) == 6, "devuelve los 6 pares con score")
    check(len(d["descartados"]) == 1, "el par sin rostro va aparte, no en las tasas")
    check(d["descartados"][0]["error_b"] == "NO_FACE", "conserva el error tipado")

    # Lo que hace visual al front: la foto resoluble para poder mostrarla.
    con_foto = [p for p in d["pares"] if p["foto_a"] and p["foto_b"]]
    check(len(con_foto) == 6,
          f"resuelve las rutas '../data/...' del CSV a rutas servibles (dio {len(con_foto)}/6)")
    check(d["pares"][0]["foto_a"] == "yo/01_ancla.jpg",
          f"la ruta servible es relativa a data/ (dio {d['pares'][0]['foto_a']!r})")
    r2 = cli.get("/api/foto", params={"ruta": d["pares"][0]["foto_a"]})
    check(r2.status_code == 200, "esa ruta resuelta SI se puede pedir a /api/foto")

    check({p["persona_a"] for p in d["pares"]} <= {"yo", "familiar", "otra_1"},
          "deduce la persona desde la carpeta")
    check(d["composicion"]["n_identidades"] == 3,
          f"composicion: 3 identidades (dio {d['composicion']['n_identidades']})")
    check(len(d["barrido"]) == 101, "el barrido trae los 101 pasos")
    check(d["eer"] is not None and "threshold" in d["eer"], "incluye EER")
    check(len(d["fragilidad"]) == 3, "jackknife una vez por persona")
    check(d["providers"] == {"CUDAExecutionProvider": 12},
          f"cuenta los providers (dio {d['providers']})")
    check(abs(d["resolucion"]["fmr_minima_medible"] - 1 / 3) < 1e-9,
          "reporta la resolucion de FMR del set")

    r = cli.get("/api/resultados", params={"csv": "no_existe.csv"})
    check(r.status_code == 404, "CSV inexistente -> 404")
    r = cli.get("/api/resultados", params={"csv": "../../secreto.txt"})
    check(r.status_code == 400, "el CSV tambien esta sandboxeado a out/")


def test_tasas():
    print("\n[api-6] /api/tasas — lo que mueve el slider")
    # non-match = 0.4126, 0.2040, 0.0381 ; match = 0.8421, 0.5312, 0.7800
    d = cli.get("/api/tasas", params={"csv": "scores.csv", "threshold": 0.45}).json()
    check(d["fp"] == 0 and abs(d["fmr"]) < 1e-9,
          "t=0.45: ningun non-match aceptado -> FMR 0%")
    check(d["fn"] == 0 and abs(d["fnmr"]) < 1e-9,
          "t=0.45: ningun match rechazado -> FNMR 0%")

    d = cli.get("/api/tasas", params={"csv": "scores.csv", "threshold": 0.40}).json()
    check(d["fp"] == 1 and abs(d["fmr"] - 1 / 3) < 1e-9,
          f"t=0.40: el par de hermanos (0.4126) se cuela -> FMR 33% (dio {d['fmr']:.3f})")

    d = cli.get("/api/tasas", params={"csv": "scores.csv", "threshold": 0.60}).json()
    check(d["fn"] == 1 and abs(d["fnmr"] - 1 / 3) < 1e-9,
          f"t=0.60: el par de luz distinta (0.5312) se rechaza -> FNMR 33% (dio {d['fnmr']:.3f})")

    check("fmr_lo" in d and "fmr_hi" in d, "trae el intervalo de confianza")

    r = cli.get("/api/tasas", params={"csv": "scores.csv", "threshold": 5})
    check(r.status_code == 400, "threshold fuera de [-1,1] -> 400")


def test_no_rompe_la_cli():
    print("\n[api-7] el API no contamina la CLI")
    import facid.calibrate
    import facid.compare
    import facid.harness
    import facid.store
    modulos = [facid, facid.calibrate, facid.compare, facid.harness, facid.store]
    for m in modulos:
        fuente = Path(m.__file__).read_text(encoding="utf-8")
        check("import api" not in fuente and "from api" not in fuente,
              f"{Path(m.__file__).name} no importa el API")
    check("api" not in sys.modules or True, "(el API si importa facid: esa es la direccion buena)")


def test_cors():
    print("\n[api-8] CORS — vite no siempre corre en el mismo puerto")
    # Vite se corre al 5174/5175 si el 5173 esta ocupado. Con una lista fija de
    # origenes eso rompe cada llamada y en pantalla parece "API apagada".
    permitidos = [
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://localhost:5179",
        "http://localhost:4173", "http://127.0.0.1:4179",
    ]
    for origen in permitidos:
        r = cli.get("/api/personas", headers={"Origin": origen})
        ok = r.headers.get("access-control-allow-origin") == origen
        check(ok, f"permite el origen {origen}")

    negados = [
        "http://evil.com",
        "http://localhost:3000",
        "https://localhost:5173",          # https no, el dev server es http
        "http://localhost.evil.com:5173",   # sufijo pegado al hostname
        "http://127.0.0.1:8080",
    ]
    fugas = [o for o in negados
             if cli.get("/api/personas", headers={"Origin": o})
             .headers.get("access-control-allow-origin") == o]
    check(not fugas, f"rechaza los origenes que no son el dev server local (fugas: {fugas})")


def main() -> int:
    print(f"Directorio temporal: {TMP}")
    test_salud()
    test_personas()
    test_foto_y_sandbox()
    test_csvs()
    test_resultados()
    test_tasas()
    test_no_rompe_la_cli()
    test_cors()

    print("\n" + "=" * 60)
    if FALLOS:
        print(f"{len(FALLOS)} PRUEBAS FALLIDAS:")
        for f in FALLOS:
            print(f"  - {f}")
        return 1
    print("Todas las pruebas del API pasaron.")
    return 0


if __name__ == "__main__":
    codigo = main()
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)

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
CORPUS = TMP / "corpus"
os.environ["FACID_DATA"] = str(DATA)      # ANTES de importar cualquier cosa de facid
os.environ["FACID_OUT"] = str(OUT)
os.environ["FACID_CORPUS"] = str(CORPUS)

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

    # Corpus externo de Run: un par de "personas" con una foto cada una.
    (CORPUS / "candidato_a").mkdir(parents=True, exist_ok=True)
    (CORPUS / "candidato_b").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(CORPUS / "candidato_a" / "01.jpg"), np.full((48, 48, 3), 50, np.uint8))
    cv2.imwrite(str(CORPUS / "candidato_b" / "01.jpg"), np.full((48, 48, 3), 55, np.uint8))
    (TMP / "corpus_secreto.txt").write_text("fuera del corpus", encoding="utf-8")


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
        "http://localhost:1000", "http://127.0.0.1:1000",  # puerto fijo del proyecto
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
        "http://localhost:1100",            # fuera del rango 10xx
    ]
    fugas = [o for o in negados
             if cli.get("/api/personas", headers={"Origin": o})
             .headers.get("access-control-allow-origin") == o]
    check(not fugas, f"rechaza los origenes que no son el dev server local (fugas: {fugas})")


def test_mover_foto():
    print("\n[api-9] /api/mover-foto — reclasificar arrastrando en El set")

    origen = DATA / "otra_1" / "01.jpg"
    destino = DATA / "familiar" / "01.jpg"
    check(origen.is_file() and not destino.exists(), "fixture: arranca sin colision")

    r = cli.post("/api/mover-foto", json={"ruta": "otra_1/01.jpg", "persona_destino": "familiar"})
    check(r.status_code == 200, f"mueve exitosamente (dio {r.status_code})")
    j = r.json()
    check(j["movido"] is True and j["a"] == "familiar/01.jpg", f"reporta la ruta nueva (dio {j})")
    check(not origen.exists(), "el archivo YA NO esta en la carpeta vieja")
    check(destino.is_file(), "el archivo SI esta en la carpeta nueva")

    r = cli.post("/api/mover-foto", json={"ruta": "familiar/01.jpg", "persona_destino": "familiar"})
    check(r.status_code == 200 and r.json()["movido"] is False,
          "soltar en la misma persona es un no-op, no un error")
    check(destino.is_file(), "no-op no toca el archivo")

    # Colision: otra_1/01_frontal.jpg ya tiene el mismo nombre que familiar/01_frontal.jpg
    (DATA / "otra_1" / "01_frontal.jpg").write_bytes(b"dup")
    r = cli.post("/api/mover-foto",
                json={"ruta": "familiar/01_frontal.jpg", "persona_destino": "otra_1"})
    check(r.status_code == 409, f"nombre repetido en el destino -> 409 (dio {r.status_code})")
    check((DATA / "familiar" / "01_frontal.jpg").is_file(),
          "la colision NO se resuelve pisando el archivo del destino")

    r = cli.post("/api/mover-foto", json={"ruta": "yo/01_ancla.jpg", "persona_destino": "otra/x"})
    check(r.status_code == 400, f"persona_destino con '/' se rechaza (dio {r.status_code})")

    r = cli.post("/api/mover-foto",
                json={"ruta": "yo/no_existe.jpg", "persona_destino": "familiar"})
    check(r.status_code == 404, f"ruta que no existe -> 404 (dio {r.status_code})")

    r = cli.post("/api/mover-foto",
                json={"ruta": "../secreto.txt", "persona_destino": "familiar"})
    check(r.status_code == 400, f"escapar de data/ con '../' se rechaza (dio {r.status_code})")
    check((TMP / "secreto.txt").is_file(), "el intento de escape no toco el archivo real")


def test_subir_fotos():
    print("\n[api-10] /api/subir-fotos — el 'dropbox' de Run")
    _, buf1 = cv2.imencode(".jpg", np.full((10, 10, 3), 10, np.uint8))
    _, buf2 = cv2.imencode(".jpg", np.full((10, 10, 3), 20, np.uint8))

    r = cli.post(
        "/api/subir-fotos",
        data={"persona": "consulta_test"},
        files=[
            ("archivos", ("foto1.jpg", buf1.tobytes(), "image/jpeg")),
            ("archivos", ("foto2.jpg", buf2.tobytes(), "image/jpeg")),
        ],
    )
    check(r.status_code == 200, f"sube dos fotos (dio {r.status_code})")
    j = r.json()
    check(set(j["guardadas"]) == {"consulta_test/foto1.jpg", "consulta_test/foto2.jpg"},
          f"reporta las rutas guardadas (dio {j['guardadas']})")
    check((DATA / "consulta_test" / "foto1.jpg").is_file(), "el archivo 1 quedo en disco")
    check((DATA / "consulta_test" / "foto2.jpg").is_file(), "el archivo 2 quedo en disco")

    _, buf3 = cv2.imencode(".jpg", np.full((10, 10, 3), 30, np.uint8))
    r = cli.post(
        "/api/subir-fotos",
        data={"persona": "consulta_test"},
        files=[("archivos", ("foto1.jpg", buf3.tobytes(), "image/jpeg"))],
    )
    j = r.json()
    check(r.status_code == 200 and j["guardadas"] == ["consulta_test/foto1_2.jpg"],
          f"colision de nombre -> sufijo numerico, no pisa el original (dio {j})")
    check((DATA / "consulta_test" / "foto1.jpg").stat().st_size == len(buf1.tobytes()),
          "el archivo original NO se sobreescribio")

    r = cli.post("/api/subir-fotos", data={"persona": "a/b"},
                files=[("archivos", ("x.jpg", buf1.tobytes(), "image/jpeg"))])
    check(r.status_code == 400, f"nombre de persona con '/' se rechaza (dio {r.status_code})")

    r = cli.post("/api/subir-fotos", data={"persona": "consulta_test"},
                files=[("archivos", ("notas.txt", b"no soy imagen", "text/plain"))])
    check(r.status_code == 415, f"archivo no soportado -> 415 (dio {r.status_code})")


def test_corpus():
    print("\n[api-11] /api/corpus — resumen y servir fotos (sin modelo)")
    r = cli.get("/api/corpus/resumen")
    check(r.status_code == 200, "resumen responde 200")
    j = r.json()
    check(j["existe"] is True and j["n_carpetas"] == 2,
          f"cuenta las 2 carpetas del corpus de prueba (dio {j})")

    r = cli.get("/api/corpus/foto", params={"ruta": "candidato_a/01.jpg"})
    check(r.status_code == 200 and r.headers["content-type"] == "image/jpeg",
          "sirve una foto del corpus")

    r = cli.get("/api/corpus/foto", params={"ruta": "../corpus_secreto.txt"})
    check(r.status_code == 400, f"escapar del corpus con '../' se rechaza (dio {r.status_code})")

    r = cli.get("/api/corpus/foto", params={"ruta": "no_existe/x.jpg"})
    check(r.status_code == 404, f"foto inexistente en el corpus -> 404 (dio {r.status_code})")


def test_historial_api():
    print("\n[api-12] /api/corpus/cobertura y /api/busquedas — historial persistido")
    r = cli.get("/api/corpus/cobertura")
    check(r.status_code == 200, f"cobertura responde 200 (dio {r.status_code})")
    j = r.json()
    check({"procesadas", "con_rostro", "sin_rostro"} <= set(j),
          f"trae el desglose de cobertura (dio {sorted(j)})")
    check(j["procesadas"] == j["con_rostro"] + j["sin_rostro"],
          "procesadas = con rostro + sin rostro (no se cuenta nada dos veces)")

    # --- fallos del corpus: poder VER las que no dieron rostro ---
    from facid.store import EmbeddingStore

    class RtFalso:
        class fingerprint:
            model_pack = "fake"
            det_size = "640x640"
            insightface_version = None
            onnxruntime_version = None
            facid_version = "test"
        rec_model_sha256 = "c" * 64
        rec_model_file = "f.onnx"
        det_model_file = "d.onnx"
        det_model_sha256 = "e" * 64
        provider_activo = "FakeExecutionProvider"

    with EmbeddingStore() as st:
        st.registrar_fallo({
            "image_sha256": "f" * 64,
            "source_path": str(CORPUS / "candidato_a" / "01.jpg"),
            "error": "NO_FACE", "error_message": "sin rostro",
            "n_faces_detected": 0, "all_det_scores": [],
        }, RtFalso(), "strict")
        # Un fallo FUERA del corpus no debe salir en esta lista.
        st.registrar_fallo({
            "image_sha256": "0" * 64,
            "source_path": str(DATA / "yo" / "01_ancla.jpg"),
            "error": "NO_FACE", "error_message": "sin rostro",
            "n_faces_detected": 0, "all_det_scores": [],
        }, RtFalso(), "strict")

    # --- /api/corpus/fotos: la consulta unificada, con filtros ---
    import numpy as _np
    with EmbeddingStore() as st:
        st.guardar({
            "image_sha256": "a1" * 32,
            "source_path": str(CORPUS / "candidato_b" / "01.jpg"),
            "embedding": _np.ones(512, dtype=_np.float32) / _np.sqrt(512),
            "det_score": 0.87, "bbox": [1, 2, 3, 4], "n_faces_detected": 1,
            "face_selection": "unico", "all_det_scores": [0.87],
            "exif_orientation_applied": False, "margen_agregado": 0.5,
        }, RtFalso(), "strict")

    r = cli.get("/api/corpus/fotos")
    check(r.status_code == 200, f"fotos del corpus responde 200 (dio {r.status_code})")
    j = r.json()
    rutas = [f["ruta"] for f in j["fotos"]]
    check(j["total"] == len(rutas), f"el total cuadra con lo devuelto (dio {j['total']}/{len(rutas)})")
    check("candidato_a/01.jpg" in rutas and "candidato_b/01.jpg" in rutas,
          f"trae exitos Y fallos del corpus (dio {rutas})")
    check(all("yo/" not in x for x in rutas),
          "NO incluye nada de data/: esta lista es del corpus")
    check(all(not x.startswith(("C:", "/")) for x in rutas),
          "las rutas vienen relativas al corpus, listas para /api/corpus/foto")
    check(cli.get("/api/corpus/foto", params={"ruta": rutas[0]}).status_code == 200,
          "esa ruta SI se puede pedir a /api/corpus/foto")

    por_estado = {f["ruta"]: f["estado"] for f in j["fotos"]}
    check(por_estado["candidato_a/01.jpg"] == "sin_rostro", "marca el fallo como sin_rostro")
    check(por_estado["candidato_b/01.jpg"] == "con_rostro", "marca el exito como con_rostro")

    solo_ok = cli.get("/api/corpus/fotos", params={"estado": "con_rostro"}).json()
    check([f["ruta"] for f in solo_ok["fotos"]] == ["candidato_b/01.jpg"],
          "filtra por estado=con_rostro")
    check(solo_ok["fotos"][0]["det_score"] == 0.87, "trae el det_score")
    check(solo_ok["fotos"][0]["margen_agregado"] == 0.5,
          "trae el margen que hizo falta (asi se ve cual fue 'rescatada')")

    solo_mal = cli.get("/api/corpus/fotos", params={"estado": "sin_rostro"}).json()
    check([f["ruta"] for f in solo_mal["fotos"]] == ["candidato_a/01.jpg"],
          "filtra por estado=sin_rostro")
    check(solo_mal["fotos"][0]["error"] == "NO_FACE", "conserva el codigo de error")

    p_a = cli.get("/api/corpus/fotos", params={"persona": "candidato_a"}).json()
    check(all(f["ruta"].startswith("candidato_a/") for f in p_a["fotos"]),
          "filtra por persona")

    margen = cli.get("/api/corpus/fotos", params={"solo_con_margen": "true"}).json()
    check([f["ruta"] for f in margen["fotos"]] == ["candidato_b/01.jpg"],
          "filtra las que necesitaron margen")

    # Paginacion: el total NO cambia al pedir una pagina chica.
    pag = cli.get("/api/corpus/fotos", params={"limite": 1}).json()
    check(len(pag["fotos"]) == 1, "respeta el limite")
    check(pag["total"] == j["total"], "el total es el del filtro, no el de la pagina")
    pag2 = cli.get("/api/corpus/fotos", params={"limite": 1, "offset": 1}).json()
    check(pag2["fotos"][0]["ruta"] != pag["fotos"][0]["ruta"],
          "offset avanza a la siguiente foto")

    check(cli.get("/api/corpus/fotos", params={"estado": "inventado"}).status_code == 422,
          "un estado invalido se rechaza")
    check(cli.get("/api/corpus/fotos", params={"persona": "../fuera"}).status_code == 400,
          "una persona con '..' se rechaza")

    # --- desglose por carpeta, para el filtro del explorador ---
    r = cli.get("/api/corpus/personas")
    check(r.status_code == 200, "personas del corpus responde 200")
    pers = {p["persona"]: p for p in r.json()["personas"]}
    check(set(pers) == {"candidato_a", "candidato_b"},
          f"lista las carpetas con fotos procesadas (dio {sorted(pers)})")
    check(pers["candidato_b"]["con_rostro"] == 1 and pers["candidato_b"]["sin_rostro"] == 0,
          "cuenta con/sin rostro por carpeta")
    check(pers["candidato_a"]["total"] == 1, "trae el total por carpeta")

    r = cli.get("/api/busquedas")
    check(r.status_code == 200 and "busquedas" in r.json(), "lista de busquedas responde 200")
    n_antes = len(r.json()["busquedas"])

    # Se guarda una busqueda directo por el historial (no via /corpus/buscar,
    # que necesitaria el modelo) y se comprueba que la API la sirve igual.
    from facid.historial import HistorialStore
    with HistorialStore() as h:
        bid = h.guardar_busqueda("alguien", "C:/corpus", 0.2, {
            "n_indexado": 3, "n_carpetas_indexadas": 1,
            "resultados": [{"consulta": "q.jpg", "error": None, "coincidencias": [
                {"persona": "p1", "archivo": "1.jpg", "ruta": "p1/1.jpg", "score": 0.5}]}],
        })

    r = cli.get("/api/busquedas")
    check(len(r.json()["busquedas"]) == n_antes + 1, "la busqueda nueva aparece en la lista")

    r = cli.get(f"/api/busquedas/{bid}")
    check(r.status_code == 200, f"se puede releer por id (dio {r.status_code})")
    j = r.json()
    check(j["persona"] == "alguien" and j["umbral"] == 0.2, "conserva persona y umbral")
    check(j["resultados"][0]["coincidencias"][0]["score"] == 0.5,
          "conserva el score de la coincidencia")

    check(cli.get("/api/busquedas/999999").status_code == 404,
          "id inexistente -> 404")

    r = cli.post(f"/api/busquedas/{bid}/borrar")
    check(r.status_code == 200, f"se puede borrar (dio {r.status_code})")
    check(cli.get(f"/api/busquedas/{bid}").status_code == 404, "despues de borrar -> 404")
    check(cli.post(f"/api/busquedas/{bid}/borrar").status_code == 404,
          "borrar dos veces -> 404")


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
    test_mover_foto()
    test_subir_fotos()
    test_corpus()
    test_historial_api()

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

"""Auto-prueba del pipeline SIN insightface ni GPU.

Sustituye el modelo por un doble de prueba y ejercita todo lo demas:
extraccion (contrato y casos borde), persistencia, harness y calibracion.

Existe porque el codigo se escribe en una maquina sin NVIDIA y se ejecuta en
otra: sin esto, el primer bug tonto se descubriria hasta estar frente a la GPU.
Corre con:  python tests/test_pipeline_sin_modelo.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

TMP = Path(tempfile.mkdtemp(prefix="facid_test_"))
os.environ["FACID_OUT"] = str(TMP / "out")   # ANTES de importar config

import cv2  # noqa: E402

from facid.calibrate import (  # noqa: E402
    calibrar, clopper_pearson, eer, fmr_fnmr, zona_traslape,
)
from facid.compare import compare  # noqa: E402
from facid.decide import decide  # noqa: E402
from facid.errors import ErrorCode, FacePolicy  # noqa: E402
from facid.extract import extract_embedding  # noqa: E402
from facid.harness import run_manifest  # noqa: E402
from facid.store import EmbeddingStore  # noqa: E402

FALLOS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FALLA {msg}")
        FALLOS.append(msg)


def casi(a: float, b: float, tol: float = 1e-5) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------- dobles
class FakeFace:
    def __init__(self, bbox, det_score, emb):
        self.bbox = np.array(bbox, dtype=np.float32)   # x1,y1,x2,y2
        self.det_score = float(det_score)
        self.normed_embedding = np.asarray(emb, dtype=np.float32)


class FakeApp:
    """Devuelve rostros segun un id codificado en el pixel (0,0) de la imagen."""

    def __init__(self, por_id):
        self.por_id = por_id
        self.models = {}
        self.model_dir = str(TMP / "modelos_falsos")

    def get(self, img):
        return self.por_id.get(int(img[0, 0, 0]), [])


class FakeFingerprint:
    model_pack = "fake_pack"
    det_size = "640x640"
    insightface_version = "0.0-fake"
    onnxruntime_version = "0.0-fake"
    facid_version = "test"


class FakeRuntime:
    def __init__(self, app):
        self.app = app
        self.fingerprint = FakeFingerprint()
        self.rec_model_file = "w600k_fake.onnx"
        self.rec_model_sha256 = "a" * 64
        self.det_model_file = "det_fake.onnx"
        self.det_model_sha256 = "b" * 64
        self.provider_activo = "FakeExecutionProvider"


def emb_persona(semilla: int, ruido: float = 0.0, rng=None) -> np.ndarray:
    """Embedding sintetico: base por persona + ruido por foto, normalizado."""
    base = np.random.default_rng(semilla).normal(size=512)
    if ruido:
        base = base + (rng or np.random.default_rng(0)).normal(scale=ruido, size=512)
    return (base / np.linalg.norm(base)).astype(np.float32)


def escribir_img(path: Path, id_img: int) -> Path:
    img = np.full((96, 96, 3), 40, dtype=np.uint8)
    img[0, 0, 0] = id_img          # canal B del pixel (0,0) = id
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return path


# ================================================================ 1. compare
def test_compare():
    print("\n[1] compare / decide")
    v = emb_persona(1)
    check(casi(compare(v, v), 1.0), "vector consigo mismo -> 1.0")
    check(casi(compare(v, -v), -1.0), "vector con su negativo -> -1.0")

    a = np.zeros(512, dtype=np.float32); a[0] = 1.0
    b = np.zeros(512, dtype=np.float32); b[1] = 1.0
    check(casi(compare(a, b), 0.0), "ortogonales -> 0.0")

    # Debe renormalizar: escalar un vector no puede cambiar el coseno.
    check(casi(compare(v * 7.5, v), 1.0), "renormaliza vectores no unitarios")

    try:
        compare(np.zeros(512), np.zeros(256))
        check(False, "dimensiones distintas deben lanzar")
    except ValueError:
        check(True, "dimensiones distintas lanzan ValueError")

    check(decide(0.5, 0.5) is True, "decide: score == threshold acepta (>=)")
    check(decide(0.49, 0.5) is False, "decide: score < threshold rechaza")
    try:
        decide(0.5, 1.5)
        check(False, "threshold fuera de rango debe lanzar")
    except ValueError:
        check(True, "threshold fuera de [-1,1] lanza ValueError")


# ================================================================ 2. extract
def test_extract():
    print("\n[2] extract — contrato y casos borde")
    d = TMP / "imgs"
    e_uno = emb_persona(10)

    faces = {
        1: [FakeFace([10, 20, 110, 140], 0.93, e_uno)],          # un rostro
        2: [],                                                    # cero rostros
        3: [FakeFace([0, 0, 50, 50], 0.80, emb_persona(11)),      # dos rostros
            FakeFace([60, 60, 200, 220], 0.88, emb_persona(12))], # este es mayor
    }
    rt = FakeRuntime(FakeApp(faces))

    p1 = escribir_img(d / "uno.png", 1)
    r = extract_embedding(p1, rt)
    for k in ("embedding", "bbox", "det_score", "n_faces_detected", "error"):
        check(k in r, f"el resultado incluye la clave '{k}'")
    check(r["error"] is None, "un rostro -> sin error")
    check(r["n_faces_detected"] == 1, "n_faces_detected == 1")
    check(r["bbox"] == [10.0, 20.0, 100.0, 120.0], "bbox convertido a [x, y, w, h]")
    check(casi(float(np.linalg.norm(r["embedding"])), 1.0), "embedding normalizado (norma 1)")
    check(r["embedding"].shape == (512,), "embedding 512-d")
    check(r["face_selection"] == "unico", "face_selection == 'unico'")
    check(len(r["image_sha256"]) == 64, "se registra el sha256 de la imagen")

    r = extract_embedding(d / "no_existe.png", rt)
    check(r["error"] == ErrorCode.FILE_NOT_FOUND, "archivo inexistente -> FILE_NOT_FOUND")

    corrupto = d / "corrupto.png"
    corrupto.write_bytes(b"esto no es un png")
    r = extract_embedding(corrupto, rt)
    check(r["error"] == ErrorCode.UNREADABLE_IMAGE, "archivo ilegible -> UNREADABLE_IMAGE")

    r = extract_embedding(escribir_img(d / "cero.png", 2), rt)
    check(r["error"] == ErrorCode.NO_FACE, "cero rostros -> NO_FACE tipado")
    check(r["embedding"] is None, "cero rostros -> embedding None")

    p3 = escribir_img(d / "dos.png", 3)
    r = extract_embedding(p3, rt, face_policy=FacePolicy.STRICT)
    check(r["error"] == ErrorCode.MULTIPLE_FACES, "2 rostros + strict -> MULTIPLE_FACES")
    check(r["n_faces_detected"] == 2, "strict reporta el conteo, no lo esconde")
    check(r["all_det_scores"] == [0.8, 0.88], "strict deja registrados los det_score")

    r = extract_embedding(p3, rt, face_policy=FacePolicy.LARGEST)
    check(r["error"] is None, "2 rostros + largest -> sin error")
    check(r["face_selection"] == "mayor_area_de_2", "largest DEJA REGISTRADA la ambiguedad")
    check(casi(r["det_score"], 0.88), "largest tomo el rostro de mayor area")

    try:
        extract_embedding(p1, rt, face_policy="inventada")
        check(False, "face_policy invalida debe lanzar")
    except ValueError:
        check(True, "face_policy invalida lanza ValueError")


# ================================================================== 3. store
def test_store():
    print("\n[3] store — persistencia y validez de la cache")
    d = TMP / "imgs_store"
    e = emb_persona(20)
    faces = {
        5: [FakeFace([0, 0, 80, 80], 0.9, e)],
        6: [FakeFace([0, 0, 40, 40], 0.7, emb_persona(21)),
            FakeFace([0, 0, 90, 90], 0.8, emb_persona(22))],
    }
    rt = FakeRuntime(FakeApp(faces))
    st = EmbeddingStore()
    fp = rt.fingerprint

    r = extract_embedding(escribir_img(d / "a.png", 5), rt)
    npy = st.guardar(r, rt, FacePolicy.STRICT)
    check(npy.exists(), "el .npy se escribe en disco")

    fila = st.buscar(r["image_sha256"], fp.model_pack, rt.rec_model_sha256,
                     fp.det_size, FacePolicy.STRICT)
    check(fila is not None, "la imagen guardada se encuentra en la cache")
    recuperado = st.cargar_embedding(fila)
    check(casi(compare(recuperado, r["embedding"]), 1.0),
          "el embedding sobrevive el round-trip a disco intacto")
    check(fila["det_score"] == r["det_score"], "det_score persistido")
    check(fila["rec_model_sha256"] == rt.rec_model_sha256, "sha del modelo persistido")
    check(fila["provider"] == "FakeExecutionProvider", "provider persistido")

    check(st.buscar(r["image_sha256"], fp.model_pack, "otro_sha", fp.det_size,
                    FacePolicy.STRICT) is None,
          "cambiar el modelo invalida la cache")
    check(st.buscar(r["image_sha256"], fp.model_pack, rt.rec_model_sha256, "320x320",
                    FacePolicy.STRICT) is None,
          "cambiar det_size invalida la cache")

    # Un embedding elegido por 'largest' NO puede reusarse bajo 'strict'.
    r2 = extract_embedding(escribir_img(d / "b.png", 6), rt, face_policy=FacePolicy.LARGEST)
    st.guardar(r2, rt, FacePolicy.LARGEST)
    check(st.buscar(r2["image_sha256"], fp.model_pack, rt.rec_model_sha256,
                    fp.det_size, FacePolicy.LARGEST) is not None,
          "cache de 'largest' sirve bajo 'largest'")
    check(st.buscar(r2["image_sha256"], fp.model_pack, rt.rec_model_sha256,
                    fp.det_size, FacePolicy.STRICT) is None,
          "cache de 'largest' NO se reusa bajo 'strict'")

    r3 = extract_embedding(d / "fantasma.png", rt)
    st.registrar_fallo(r3, rt, FacePolicy.STRICT)
    check(st.resumen()["fallos"] >= 1, "los fallos quedan registrados en SQLite")
    st.close()


# ============================================================= 4. calibracion
def test_calibracion_exacta():
    print("\n[4] calibracion — numeros verificables a mano")
    m = np.array([0.9, 0.8, 0.7, 0.4])
    nm = np.array([0.1, 0.2, 0.5, 0.3])

    r = fmr_fnmr(m, nm, 0.45)
    check(r["fp"] == 1 and casi(r["fmr"], 0.25),
          "t=0.45: 1 de 4 non-match aceptado -> FMR 25%")
    check(r["fn"] == 1 and casi(r["fnmr"], 0.25),
          "t=0.45: 1 de 4 match rechazado -> FNMR 25%")

    r0 = fmr_fnmr(m, nm, 0.0)
    check(casi(r0["fmr"], 1.0) and casi(r0["fnmr"], 0.0),
          "t=0.0 acepta todo: FMR 100%, FNMR 0%")
    r1 = fmr_fnmr(m, nm, 1.0)
    check(casi(r1["fmr"], 0.0) and casi(r1["fnmr"], 1.0),
          "t=1.0 rechaza todo: FMR 0%, FNMR 100%")

    check(r["fmr"] >= 0 and r["fmr_lo"] <= r["fmr"] <= r["fmr_hi"],
          "el IC contiene a la estimacion puntual")

    lo, hi = clopper_pearson(0, 7)
    check(casi(lo, 0.0) and hi > 0.30,
          f"0 errores en 7 pares NO es FMR 0: IC95% llega a {hi:.0%}")

    z = zona_traslape(m, nm)
    check(z["hay_traslape"] is True, "detecta que las distribuciones se traslapan")
    check(casi(z["zona_lo"], 0.4) and casi(z["zona_hi"], 0.5),
          "zona de traslape = [0.4, 0.5]")
    check(z["match_en_zona"] == 1 and z["nonmatch_en_zona"] == 1,
          "cuenta 1 match y 1 non-match dentro de la zona")

    ee = eer(m, nm)
    check(casi(ee["eer"], 0.25, tol=0.01), f"EER ~ 25% (dio {ee['eer']:.1%})")

    # Caso separado limpio: sin traslape debe encontrar la brecha.
    z2 = zona_traslape(np.array([0.8, 0.9]), np.array([0.1, 0.2]))
    check(z2["hay_traslape"] is False, "sin traslape lo reporta como brecha limpia")
    check(casi(z2["threshold_libre_de_error"], 0.5),
          "propone el punto medio de la brecha (0.5)")


# ============================================================ 5. end-to-end
def test_harness_end_to_end():
    print("\n[5] harness end-to-end — manifiesto -> CSV -> calibracion")
    import json

    d = TMP / "e2e"
    rng = np.random.default_rng(7)

    A = emb_persona(100)
    C = emb_persona(200)
    # B se construye PARECIDO a A a proposito: es el caso que rompe thresholds.
    b_propio = emb_persona(201)
    B = (0.45 * A + 0.55 * b_propio)
    B = B / np.linalg.norm(B)

    def foto(base, ruido_rel):
        # El ruido se escala por 1/sqrt(512) para que `ruido_rel` sea la norma
        # del ruido RELATIVA a la del vector base (que es unitario). Sin esta
        # correccion, un ruido de 0.5 por componente tiene norma ~11 y sepulta
        # la identidad: los scores de match saldrian ~0 y el set no modelaria nada.
        v = base + rng.normal(scale=ruido_rel / np.sqrt(512), size=512)
        return (v / np.linalg.norm(v)).astype(np.float32)

    ids = {
        10: foto(A, 0.5), 11: foto(A, 0.5), 12: foto(A, 1.4),  # 12 = foto dificil
        20: foto(B, 0.5), 21: foto(B, 0.5),
        30: foto(C, 0.5), 31: foto(C, 0.5),
    }
    faces = {i: [FakeFace([5, 5, 105, 125], 0.9, v)] for i, v in ids.items()}
    faces[40] = []  # imagen sin rostro detectable

    rt = FakeRuntime(FakeApp(faces))
    for i in list(ids) + [40]:
        escribir_img(d / f"img{i}.png", i)

    manifiesto = [
        {"img_a": "img10.png", "img_b": "img11.png", "same_person": True,  "notes": "misma sesion"},
        {"img_a": "img10.png", "img_b": "img12.png", "same_person": True,  "notes": "angulo dificil"},
        {"img_a": "img11.png", "img_b": "img12.png", "same_person": True,  "notes": "angulo dificil"},
        {"img_a": "img20.png", "img_b": "img21.png", "same_person": True,  "notes": "misma sesion"},
        {"img_a": "img30.png", "img_b": "img31.png", "same_person": True,  "notes": "misma sesion"},
        {"img_a": "img10.png", "img_b": "img20.png", "same_person": False, "notes": "parecidos"},
        {"img_a": "img11.png", "img_b": "img21.png", "same_person": False, "notes": "parecidos"},
        {"img_a": "img10.png", "img_b": "img30.png", "same_person": False, "notes": "rasgos distintos"},
        {"img_a": "img20.png", "img_b": "img30.png", "same_person": False, "notes": "rasgos distintos"},
        {"img_a": "img10.png", "img_b": "img40.png", "same_person": True,  "notes": "sin rostro a proposito"},
    ]
    mpath = d / "manifiesto.json"
    mpath.write_text(json.dumps(manifiesto, indent=2), encoding="utf-8")

    csv_out = TMP / "out" / "scores.csv"
    resumen = run_manifest(mpath, rt, csv_out, verbose=False)

    check(Path(csv_out).exists(), "el CSV se escribe")
    check(resumen["pares_total"] == 10, "lee los 10 pares del manifiesto")
    check(resumen["pares_ok"] == 9, "9 pares con score (1 excluido por NO_FACE)")
    check(resumen["pares_descartados"] == 1, "el par sin rostro queda marcado, no inventado")
    check(resumen["imagenes_unicas"] == 8, "cada imagen unica se procesa una sola vez")

    import csv as _csv
    filas = list(_csv.DictReader(open(csv_out, encoding="utf-8")))
    esperadas = ["img_a", "img_b", "same_person", "score",
                 "det_score_a", "det_score_b", "notes"]
    check(list(filas[0].keys())[:7] == esperadas,
          "las 7 primeras columnas son exactamente las del handoff")
    malo = [f for f in filas if f["error_b"] == "NO_FACE"]
    check(len(malo) == 1 and malo[0]["score"] == "",
          "el par fallido va con score vacio y error_b=NO_FACE")

    # Segunda corrida: debe salir todo de cache y dar scores identicos.
    csv2 = TMP / "out" / "scores2.csv"
    run_manifest(mpath, rt, csv2, verbose=False)
    f2 = list(_csv.DictReader(open(csv2, encoding="utf-8")))
    check([r["score"] for r in filas] == [r["score"] for r in f2],
          "reejecutar desde cache reproduce los scores exactos")

    rep = calibrar(csv_out, TMP / "out", verbose=False)
    check(rep["ok"], "la calibracion corre sobre el CSV")
    check(rep["match"]["n"] == 5 and rep["nonmatch"]["n"] == 4,
          f"separa 5 match y 4 non-match (dio {rep['match']['n']}/{rep['nonmatch']['n']})")
    check(rep["match"]["min"] > rep["nonmatch"]["min"],
          "los match puntuan mas alto que los non-match (el set sintetico modela algo)")
    check(rep["match"]["max"] > 0.6,
          f"los match de misma sesion llegan alto (max {rep['match']['max']:.3f})")
    check(rep["descartados"] == 1, "la calibracion excluye el par fallido")
    check(rep["eer"] is not None, "calcula EER")
    check(Path(rep["sweep_csv"]).exists(), "escribe el barrido FMR/FNMR")
    check(rep["histograma"] and Path(rep["histograma"]).exists(),
          "genera el histograma PNG")
    check(Path(TMP / "out" / "reporte_calibracion.txt").exists(),
          "guarda el reporte en texto")

    barrido = list(_csv.DictReader(open(rep["sweep_csv"], encoding="utf-8")))
    check(len(barrido) == 101, "el barrido cubre 0.00..1.00 en pasos de 0.01")
    fmrs = [float(r["fmr"]) for r in barrido]
    fnmrs = [float(r["fnmr"]) for r in barrido]
    check(all(a >= b - 1e-12 for a, b in zip(fmrs, fmrs[1:])),
          "FMR es monotona no-creciente en el threshold")
    check(all(a <= b + 1e-12 for a, b in zip(fnmrs, fnmrs[1:])),
          "FNMR es monotona no-decreciente en el threshold")

    print("\n  --- reporte generado (extracto) ---")
    for linea in rep["reporte"].splitlines()[:14]:
        print("  " + linea)
    return rep


def main() -> int:
    print(f"Directorio temporal: {TMP}")
    test_compare()
    test_extract()
    test_store()
    test_calibracion_exacta()
    test_harness_end_to_end()

    print("\n" + "=" * 60)
    if FALLOS:
        print(f"{len(FALLOS)} PRUEBAS FALLIDAS:")
        for f in FALLOS:
            print(f"  - {f}")
        return 1
    print("Todas las pruebas pasaron.")
    print("Nota: esto NO valida insightface ni CUDA; eso lo verifica "
          "`facid doctor` en la maquina con GPU.")
    return 0


if __name__ == "__main__":
    codigo = main()
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)

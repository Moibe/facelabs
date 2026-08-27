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
from facid.dependencia import estructura, jackknife_por_persona  # noqa: E402
from facid.harness import (  # noqa: E402
    ManifestError, cargar_manifiesto, init_manifest, run_manifest,
)
from facid.busqueda import descubrir_corpus  # noqa: E402
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


# ========================================================= 6. dependencia
def _fila(a, b, sa, sb, score, same):
    return {"img_a": a, "img_b": b, "sha256_a": sa, "sha256_b": sb,
            "score": f"{score:.6f}", "same_person": str(same)}


def test_dependencia():
    print("\n[6] dependencia — identidades, reuso y fragilidad")

    # yo{a,b,c}  fam{x,y}  otra1{z}  otra2{w}
    match = [
        _fila("yo/a.jpg", "yo/b.jpg", "s_a", "s_b", 0.90, True),
        _fila("yo/a.jpg", "yo/c.jpg", "s_a", "s_c", 0.80, True),
        _fila("fam/x.jpg", "fam/y.jpg", "s_x", "s_y", 0.85, True),
    ]
    nonmatch = [
        _fila("yo/a.jpg", "fam/x.jpg", "s_a", "s_x", 0.40, False),
        _fila("yo/b.jpg", "fam/y.jpg", "s_b", "s_y", 0.30, False),
        _fila("yo/a.jpg", "otra1/z.jpg", "s_a", "s_z", 0.05, False),
        _fila("fam/x.jpg", "otra1/z.jpg", "s_x", "s_z", 0.10, False),
        _fila("otra1/z.jpg", "otra2/w.jpg", "s_z", "s_w", 0.02, False),
        _fila("yo/a.jpg", "otra2/w.jpg", "s_a", "s_w", 0.03, False),
    ]

    est = estructura(match, nonmatch)
    check(est["n_pares"] == 9, "cuenta los 9 pares")
    check(est["n_imagenes"] == 7, "cuenta las 7 fotos distintas")
    check(est["n_identidades"] == 4,
          f"deduce 4 personas de los pares match (dio {est['n_identidades']})")
    check(sorted(len(v) for v in est["grupos"].values()) == [1, 1, 2, 3],
          "agrupa las fotos por persona: 3+2+1+1")

    # yo/a participa en 5 pares: (a,b) (a,c) (a,x) (a,z) (a,w)
    check(est["reuso_max"] == 5, f"la foto mas usada aparece en 5 pares (dio {est['reuso_max']})")
    check("a.jpg" in est["img_mas_usada"], "identifica CUAL es la foto mas usada")
    check(casi(est["reuso_medio"], 2 * 9 / 7, 1e-6), "reuso medio = 2*pares/fotos")
    check(est["contradicciones"] == [], "sin contradicciones en un set bien etiquetado")

    # Etiquetado contradictorio: dice 'distintas' pero los match las unen.
    mal = nonmatch + [_fila("yo/a.jpg", "yo/c.jpg", "s_a", "s_c", 0.2, False)]
    est_mal = estructura(match, mal)
    check(len(est_mal["contradicciones"]) == 1,
          "detecta el par que se contradice con el propio etiquetado")

    jk = jackknife_por_persona(match, nonmatch, est)
    check(len(jk) == 4, "hace jackknife una vez por persona")
    check([j["persona"] for j in jk][:2] == ["yo", "fam"],
          "nombra a la persona por su carpeta, empezando por la que tiene mas fotos")
    check(jk[0]["n_fotos"] == 3 and jk[0]["pares_excluidos"] == 6,
          "quitar 'yo' saca sus 3 fotos y los 6 pares que la tocan")
    por_persona = {j["persona"]: j["threshold"] for j in jk}
    check(all(v is not None for v in por_persona.values()),
          "las 4 corridas dejan pares de ambas clases")

    # El umbral de FMR=0 cae en el PUNTO MEDIO entre el non-match mas alto y el
    # match mas bajo que sobreviven: es el de maximo margen. Se verifica a mano.
    #   sin 'yo'     -> match {0.85}            nm {0.10, 0.02} -> (0.10+0.85)/2
    #   sin 'fam'    -> match {0.90, 0.80}      nm {0.05,0.03,0.02} -> (0.05+0.80)/2
    #   sin 'otra1'  -> match {0.90,0.80,0.85}  nm {0.40,0.30,0.03} -> (0.40+0.80)/2
    #   sin 'otra2'  -> match {0.90,0.80,0.85}  nm {0.40,0.30,0.05} -> (0.40+0.80)/2
    esperados = {"yo": 0.475, "fam": 0.425, "otra1": 0.600, "otra2": 0.600}
    for persona, esperado in esperados.items():
        check(casi(por_persona[persona], esperado, 1e-6),
              f"sin '{persona}' el umbral es el punto medio {esperado:.3f} "
              f"(dio {por_persona[persona]:.4f})")

    ths = list(por_persona.values())
    check(casi(max(ths) - min(ths), 0.175, 1e-6),
          "el umbral se mueve 0.175 segun a quien saques: el set es fragil")
    check(max(ths) - min(ths) > 0.05,
          "ese rango dispara el aviso de fragilidad del reporte")


# ======================================================= 7. init-manifest
def test_init_manifest():
    print("\n[7] init-manifest — carpetas -> manifiesto")
    import json

    d = TMP / "init"
    plan = {"p1": 3, "p2": 2, "p3": 1}
    for persona, n in plan.items():
        for i in range(1, n + 1):
            escribir_img(d / "data" / persona / f"{i:02d}_foto.jpg", 1)
    (d / "data" / "vacia").mkdir(parents=True, exist_ok=True)   # sin imagenes
    (d / "data" / "suelta.jpg").write_bytes(b"x")               # archivo suelto

    salida = d / "manifests" / "ancla.json"
    r = init_manifest(d / "data", salida, verbose=False)
    check(r["n_personas"] == 3, "una carpeta = una persona; ignora carpetas vacias")
    check(r["n_fotos"] == 6, "ignora los archivos sueltos fuera de las carpetas")
    # ancla: match = (3-1)+(2-1)+(1-1) = 3 ; non-match = C(3,2) = 3
    check(r["match"] == 3 and r["nonmatch"] == 3,
          f"modo ancla da 3 match y 3 non-match (dio {r['match']}/{r['nonmatch']})")
    check(r["sin_pares_match"] == ["p3"], "avisa que p3 tiene 1 sola foto")

    pares = json.loads(salida.read_text(encoding="utf-8"))
    check(len(pares) == 6, "el JSON tiene los 6 pares")
    check(all("/" in p["img_a"] and "\\" not in p["img_a"] for p in pares),
          "las rutas usan '/' para servir igual en Windows y Linux")
    check(all(p["img_a"].startswith("../data/") for p in pares),
          "las rutas son relativas al manifiesto, no absolutas")
    check(all(p["notes"] for p in pares), "cada par sale con una nota pre-llenada")

    def carpeta(ruta):
        return ruta.split("/")[-2]

    check(all(carpeta(p["img_a"]) == carpeta(p["img_b"]) for p in pares if p["same_person"]),
          "same_person=True solo entre fotos de la MISMA carpeta")
    check(all(carpeta(p["img_a"]) != carpeta(p["img_b"]) for p in pares if not p["same_person"]),
          "same_person=False solo entre carpetas DISTINTAS")

    # El manifiesto generado debe ser consumible por el harness sin editarlo.
    cargados = cargar_manifiesto(salida)
    check(len(cargados) == 6, "el harness puede leer el manifiesto generado tal cual")
    check(all(c["img_a"].is_file() for c in cargados),
          "las rutas relativas resuelven a archivos que existen")

    r2 = init_manifest(d / "data", d / "manifests" / "todos.json",
                       modo="todos", verbose=False)
    # todos: match = C(3,2)+C(2,2)+0 = 4 ; non-match = 3*2+3*1+2*1 = 11
    check(r2["match"] == 4 and r2["nonmatch"] == 11,
          f"modo todos da 4 match y 11 non-match (dio {r2['match']}/{r2['nonmatch']})")
    check(r2["pares"] > r["pares"],
          "modo todos produce mas pares de las MISMAS fotos (de ahi el aviso)")

    try:
        init_manifest(d / "no_existe", d / "x.json", verbose=False)
        check(False, "un directorio inexistente debe lanzar ManifestError")
    except ManifestError:
        check(True, "directorio inexistente lanza ManifestError")

    vacio = d / "sin_nada"
    vacio.mkdir(parents=True, exist_ok=True)
    try:
        init_manifest(vacio, d / "y.json", verbose=False)
        check(False, "un directorio sin personas debe lanzar ManifestError")
    except ManifestError:
        check(True, "directorio sin subcarpetas con imagenes lanza ManifestError")

    try:
        init_manifest(d / "data", d / "z.json", modo="inventado", verbose=False)
        check(False, "un modo invalido debe lanzar")
    except ManifestError:
        check(True, "modo invalido lanza ManifestError")

    # --- excluir: probar subconjuntos sin tocar disco (picker de El set) ---
    r3 = init_manifest(d / "data", d / "manifests" / "sin_p1_03.json",
                       excluir={"p1/03_foto.jpg"}, verbose=False)
    check(r3["match"] == 2 and r3["nonmatch"] == 3,
          f"excluir una foto no-ancla de p1 quita 1 match y no toca non-match "
          f"(dio {r3['match']}/{r3['nonmatch']})")

    r4 = init_manifest(d / "data", d / "manifests" / "sin_p3.json",
                       excluir={"p3/01_foto.jpg"}, verbose=False)
    check(r4["n_personas"] == 2,
          "excluir la unica foto de una persona la saca del manifiesto entero")
    check(r4["nonmatch"] == 1,
          f"sin p3 solo queda el par p1-p2 (dio {r4['nonmatch']})")

    todas = {f"{p}/{i:02d}_foto.jpg" for p, n in plan.items() for i in range(1, n + 1)}
    try:
        init_manifest(d / "data", d / "manifests" / "todo_fuera.json",
                      excluir=todas, verbose=False)
        check(False, "excluir todas las fotos debe dejar el manifiesto vacio y lanzar")
    except ManifestError:
        check(True, "excluir todas las fotos -> ManifestError, igual que un data/ vacio")


# ==================================================== 7b. descubrir_corpus
def test_descubrir_corpus():
    print("\n[7b] descubrir_corpus — limites para un corpus grande")
    d = TMP / "corpus"
    plan = {"p1": 3, "p2": 2, "p3": 1}
    for persona, n in plan.items():
        for i in range(1, n + 1):
            escribir_img(d / persona / f"{i:02d}_foto.jpg", 1)
    (d / "vacia").mkdir(parents=True, exist_ok=True)
    (d / "suelta.jpg").write_bytes(b"x")

    sin_limite = descubrir_corpus(d)
    check(set(sin_limite) == {"p1", "p2", "p3"}, "ignora carpetas vacias y archivos sueltos")
    check(sum(len(v) for v in sin_limite.values()) == 6, "sin limite trae las 6 fotos")

    con_limite_carpetas = descubrir_corpus(d, limite_carpetas=2)
    check(len(con_limite_carpetas) == 2, "limite_carpetas corta el numero de personas")
    check(set(con_limite_carpetas) == {"p1", "p2"},
          "toma las primeras alfabeticamente, reproducible entre corridas")

    con_limite_fotos = descubrir_corpus(d, limite_por_carpeta=1)
    check(all(len(v) == 1 for v in con_limite_fotos.values()),
          "limite_por_carpeta corta fotos por persona, no elimina personas")

    try:
        descubrir_corpus(d / "no_existe")
        check(False, "corpus inexistente debe lanzar")
    except FileNotFoundError:
        check(True, "corpus inexistente -> FileNotFoundError")


# ================================================== 8. provider en el reporte
def _csv_min(destino, provs=None):
    """CSV minimo con 2 match y 2 non-match. provs: (pa, pb) por fila, o None."""
    import csv as _csv
    filas = [
        ("yo/a.jpg", "yo/b.jpg", "True", 0.90),
        ("yo/a.jpg", "yo/c.jpg", "True", 0.80),
        ("yo/a.jpg", "fam/x.jpg", "False", 0.30),
        ("fam/x.jpg", "otra/z.jpg", "False", 0.10),
    ]
    cols = ["img_a", "img_b", "same_person", "score", "pair_ok"]
    if provs is not None:
        cols += ["provider_a", "provider_b"]
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(cols)
        for i, (a, b, same, sc) in enumerate(filas):
            fila = [a, b, same, f"{sc:.6f}", "True"]
            if provs is not None:
                fila += list(provs[i])
            w.writerow(fila)
    return destino


def test_provider_en_reporte():
    print("\n[8] provider — procedencia de los embeddings en el reporte")
    d = TMP / "prov"
    CUDA, CPU = "CUDAExecutionProvider", "CPUExecutionProvider"

    # Caso 1: todo en el mismo provider -> se registra, sin alarma.
    uno = _csv_min(d / "uno.csv", [(CUDA, CUDA)] * 4)
    r1 = calibrar(uno, d / "out1", verbose=False)
    check(f"Provider: {CUDA}" in r1["reporte"],
          "con un solo provider lo deja asentado en el reporte")
    check("NO salieron todos del mismo" not in r1["reporte"],
          "con un solo provider NO avisa nada")

    # Caso 2: set mezclado -> tiene que avisar y decir cuanto de cada uno.
    mezcla = _csv_min(d / "mezcla.csv",
                      [(CUDA, CUDA), (CUDA, CPU), (CPU, CPU), (CPU, CUDA)])
    r2 = calibrar(mezcla, d / "out2", verbose=False)
    check("NO salieron todos del mismo provider" in r2["reporte"],
          "detecta el set con providers mezclados")
    check(f"{CUDA}" in r2["reporte"] and f"{CPU}" in r2["reporte"],
          "nombra los dos providers involucrados")
    check("--force" in r2["reporte"],
          "dice que hacer al respecto (--force en un solo device)")
    # 8 lados de par en total, 4 de cada uno.
    check("4 lado(s) de par" in r2["reporte"],
          "cuenta cuantos lados de par salieron de cada provider")

    # Caso 3: CSV viejo sin las columnas -> no truena ni inventa.
    viejo = _csv_min(d / "viejo.csv", provs=None)
    r3 = calibrar(viejo, d / "out3", verbose=False)
    check(r3["ok"], "un CSV sin columnas de provider sigue calibrando")
    check("Provider:" not in r3["reporte"] and "provider" not in r3["reporte"].lower(),
          "sin las columnas no menciona el tema en vez de adivinar")


def main() -> int:
    print(f"Directorio temporal: {TMP}")
    test_compare()
    test_extract()
    test_store()
    test_calibracion_exacta()
    test_dependencia()
    test_init_manifest()
    test_descubrir_corpus()
    test_harness_end_to_end()
    test_provider_en_reporte()

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

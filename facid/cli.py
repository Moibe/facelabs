"""CLI del PoC. Cinco comandos, uno por etapa del pipeline.

Los imports del modelo son perezosos a proposito: `facid calibrate` trabaja
sobre el CSV y no debe exigir insightface, onnxruntime ni GPU. Asi el analisis
se puede correr en la laptop aunque la extraccion haya ocurrido en otra maquina.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import DEFAULT_DEVICE, DEFAULT_FACE_POLICY, OUT_DIR
from .errors import FacePolicy


def _añadir_flags_modelo(p: argparse.ArgumentParser) -> None:
    p.add_argument("--device", choices=("cuda", "cpu"), default=DEFAULT_DEVICE,
                   help="Provider de inferencia (default: %(default)s).")
    p.add_argument("--allow-cpu-fallback", action="store_true",
                   help="No falla si se pidio CUDA y se cayo a CPU. "
                        "Los embeddings son equivalentes; solo cambia la velocidad.")
    p.add_argument("--face-policy", choices=FacePolicy.TODAS, default=DEFAULT_FACE_POLICY,
                   help="Que hacer con multiples rostros (default: %(default)s).")


def _cargar(args):
    # El try cubre tambien la LLAMADA, no solo el import: runtime.py importa
    # onnxruntime e insightface dentro de load_runtime (despues de precargar
    # las libs CUDA), asi que el ImportError aparece al invocar, no al importar.
    try:
        from .runtime import load_runtime
        return load_runtime(
            device=args.device,
            require_gpu=False if args.allow_cpu_fallback else None,
            verbose=True,
        )
    except ImportError as e:
        raise SystemExit(
            f"""[X] Falta una dependencia del modelo: {e}
    doctor / extract / compare / run-manifest necesitan insightface y
    onnxruntime. Corre ./setup.sh en la maquina con GPU.
    (`facid calibrate` SI corre sin ellas: solo lee el CSV.)"""
        ) from e


# --------------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    from .store import EmbeddingStore
    print(f"facid {__version__}")
    try:
        rt = _cargar(args)
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[X] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    fp = rt.fingerprint
    print("\n--- entorno ---")
    for k in ("python_version", "platform", "insightface_version",
              "onnxruntime_package", "numpy_version", "opencv_version"):
        print(f"  {k:<22} {getattr(fp, k)}")
    print(f"  {'device solicitado':<22} {fp.device_requested}")
    print(f"  {'providers compilados':<22} {fp.available_providers}")
    print(f"  {'libs nvidia precargadas':<22} {fp.cuda_libs_preloaded}")

    print("\n--- provider ACTIVO por modelo (lo que realmente corre) ---")
    for task, provs in fp.active_providers.items():
        print(f"  {task:<14} {provs}")

    print(f"\n--- modelos ({fp.model_pack}) ---")
    print(f"  dir       {fp.model_dir}")
    print(f"  det_size  {fp.det_size}")
    for nombre, h in fp.model_files.items():
        marca = ""
        if nombre == rt.rec_model_file:
            marca = "  <- reconocimiento (embeddings 512-d)"
        elif nombre == rt.det_model_file:
            marca = "  <- deteccion"
        print(f"  {nombre:<22} sha256:{h[:16]}...{marca}")

    with EmbeddingStore() as st:
        print("\n--- store ---")
        print("  " + json.dumps(st.resumen(), ensure_ascii=False))

    if args.json:
        Path(args.json).write_text(
            json.dumps(fp.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[facid] huella escrita en {args.json}")
    return 0


# -------------------------------------------------------------------- extract
def cmd_extract(args) -> int:
    from .extract import extract_embedding
    from .store import EmbeddingStore
    from .util import sha256_file

    rt = _cargar(args)
    fp = rt.fingerprint
    st = EmbeddingStore()
    n_ok = n_err = 0

    for ruta in args.images:
        p = Path(ruta)
        if p.is_file() and not args.force:
            fila = st.buscar(sha256_file(p), fp.model_pack, rt.rec_model_sha256,
                             fp.det_size, args.face_policy)
            if fila is not None:
                print(f"cache  det={fila['det_score']:.3f}  "
                      f"rostros={fila['n_faces_detected']}  {p}")
                n_ok += 1
                continue

        r = extract_embedding(p, rt, face_policy=args.face_policy)
        if r["error"] is None:
            npy = st.guardar(r, rt, args.face_policy)
            print(f"ok     det={r['det_score']:.3f}  rostros={r['n_faces_detected']}"
                  f"  sel={r['face_selection']}  {p}  -> {npy.name}")
            n_ok += 1
        else:
            st.registrar_fallo(r, rt, args.face_policy)
            print(f"ERROR  {r['error']}  rostros={r['n_faces_detected']}  {p}",
                  file=sys.stderr)
            if r["all_det_scores"]:
                print(f"       det_scores={r['all_det_scores']}", file=sys.stderr)
            n_err += 1

    st.close()
    print(f"\n{n_ok} ok, {n_err} con error")
    return 1 if n_err else 0


# -------------------------------------------------------------------- compare
def cmd_compare(args) -> int:
    from .compare import compare
    from .extract import extract_embedding
    from .store import EmbeddingStore
    from .util import sha256_file

    rt = _cargar(args)
    fp = rt.fingerprint
    st = EmbeddingStore()
    embs = []

    for ruta in (args.img_a, args.img_b):
        p = Path(ruta)
        fila = None
        if p.is_file() and not args.force:
            fila = st.buscar(sha256_file(p), fp.model_pack, rt.rec_model_sha256,
                             fp.det_size, args.face_policy)
        if fila is not None:
            embs.append(st.cargar_embedding(fila))
            continue
        r = extract_embedding(p, rt, face_policy=args.face_policy)
        if r["error"] is not None:
            print(f"[X] {ruta}: {r['error']} — {r['error_message']}", file=sys.stderr)
            st.registrar_fallo(r, rt, args.face_policy)
            st.close()
            return 1
        st.guardar(r, rt, args.face_policy)
        embs.append(r["embedding"])

    st.close()
    score = compare(embs[0], embs[1])
    print(f"{score:.6f}")

    if args.threshold is not None:
        from .decide import decide
        veredicto = decide(score, args.threshold)
        print(f"threshold={args.threshold:.4f}  ->  "
              f"{'MISMA PERSONA' if veredicto else 'PERSONAS DISTINTAS'}", file=sys.stderr)
        print("(el veredicto vale lo que valga tu calibracion, no mas)", file=sys.stderr)
    return 0


# --------------------------------------------------------------- run-manifest
def cmd_run_manifest(args) -> int:
    from .harness import ManifestError, run_manifest
    try:
        rt = _cargar(args)
        run_manifest(args.manifest, rt, args.out, face_policy=args.face_policy,
                     force=args.force, verbose=True)
    except ManifestError as e:
        print(f"[X] {e}", file=sys.stderr)
        return 2
    print(f"\nSiguiente paso:  python -m facid calibrate {args.out}")
    return 0


# -------------------------------------------------------------- init-manifest
def cmd_init_manifest(args) -> int:
    # Tampoco carga el modelo: solo lee nombres de carpetas y archivos.
    from .harness import ManifestError, init_manifest
    try:
        init_manifest(args.data_dir, args.out, modo=args.modo, verbose=True)
    except ManifestError as e:
        print(f"[X] {e}", file=sys.stderr)
        return 2
    return 0


# ------------------------------------------------------------------ calibrate
def cmd_calibrate(args) -> int:
    # Sin imports del modelo: esto corre en cualquier maquina.
    from .calibrate import calibrar
    r = calibrar(args.csv, args.out_dir, paso=args.paso, verbose=True)
    return 0 if r["ok"] else 1


# ----------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="facid",
        description="PoC de verificacion facial 1:1 — instrumento de medicion. "
                    "Uso de investigacion NO comercial. Sin liveness/PAD.",
    )
    p.add_argument("--version", action="version", version=f"facid {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="Verifica entorno, provider ACTIVO y hashes de modelo.")
    _añadir_flags_modelo(d)
    d.add_argument("--json", help="Escribe la huella del entorno a este archivo.")
    d.set_defaults(func=cmd_doctor)

    e = sub.add_parser("extract", help="Extrae y persiste embeddings.")
    e.add_argument("images", nargs="+")
    _añadir_flags_modelo(e)
    e.add_argument("--force", action="store_true", help="Ignora la cache.")
    e.set_defaults(func=cmd_extract)

    c = sub.add_parser("compare", help="Score coseno crudo entre dos imagenes.")
    c.add_argument("img_a")
    c.add_argument("img_b")
    _añadir_flags_modelo(c)
    c.add_argument("--force", action="store_true")
    c.add_argument("--threshold", type=float, default=None,
                   help="Opcional: aplica decide() y muestra el veredicto.")
    c.set_defaults(func=cmd_compare)

    i = sub.add_parser(
        "init-manifest",
        help="Genera el manifiesto desde data/: misma carpeta = misma persona.")
    i.add_argument("data_dir", nargs="?", default="data",
                   help="Carpeta con una subcarpeta por persona (default: %(default)s).")
    i.add_argument("-o", "--out", default="manifests/mi_set.json")
    i.add_argument("--modo", choices=("ancla", "todos"), default="ancla",
                   help="'ancla': cada foto contra la primera de su persona, y las "
                        "anclas entre si (pocos pares, dependencia acotada). "
                        "'todos': todas las combinaciones — mas pares pero salidos "
                        "de las mismas fotos, asi que estrechan los intervalos sin "
                        "justificarlo. Default: %(default)s.")
    i.set_defaults(func=cmd_init_manifest)

    r = sub.add_parser("run-manifest", help="Corre un manifiesto de pares -> CSV.")
    r.add_argument("manifest")
    r.add_argument("-o", "--out", default=str(OUT_DIR / "scores.csv"))
    _añadir_flags_modelo(r)
    r.add_argument("--force", action="store_true")
    r.set_defaults(func=cmd_run_manifest)

    k = sub.add_parser("calibrate", help="Analisis de calibracion sobre el CSV (sin GPU).")
    k.add_argument("csv")
    k.add_argument("-o", "--out-dir", default=str(OUT_DIR))
    k.add_argument("--paso", type=float, default=0.01)
    k.set_defaults(func=cmd_calibrate)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrumpido", file=sys.stderr)
        return 130

"""Carga del modelo y verificación del provider de inferencia.

El handoff marca esto como fallo común y silencioso: onnxruntime se cae a CPU
sin avisar y el experimento sigue corriendo, más lento pero aparentemente bien.
Aquí se rompe ese silencio de dos maneras:

1. No se confía en `get_available_providers()` (dice qué está *compilado*, no qué
   se está usando). Se interroga la sesión ONNX ya construida de cada modelo.
2. Si se pidió CUDA y la sesión terminó en CPU, se lanza excepción. Falla ruidoso.

Nota sobre embeddings: CPU y CUDA producen embeddings equivalentes (difieren en
el último dígito por orden de reducción en float32). Caer a CPU arruina el
tiempo de corrida, NO la validez de la calibración.
"""

from __future__ import annotations

import ctypes
import glob
import os
import platform
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import __version__
from .config import (
    ALLOWED_MODULES,
    CPU_PROVIDER,
    CUDA_PROVIDER,
    DET_SIZE,
    MODEL_PACK,
    MODEL_ROOT,
)
from .util import sha256_file


class ProviderError(RuntimeError):
    """Se pidió GPU y no se obtuvo. Deliberadamente fatal."""


def preload_cuda_libs() -> list[str]:
    """Precarga las .so de los paquetes pip `nvidia-*` con RTLD_GLOBAL.

    Permite que onnxruntime-gpu encuentre cuDNN/cuBLAS instalados vía pip sin
    exigir un CUDA de sistema ni exportar LD_LIBRARY_PATH antes de arrancar
    (el loader dinámico lee esa variable solo al inicio del proceso, así que
    ajustarla desde Python ya no sirve — por eso se precarga con ctypes).

    Debe llamarse ANTES de importar onnxruntime. Silencioso ante fallos: si no
    hay ruedas nvidia-* no pasa nada y ORT usará el CUDA del sistema.
    """
    if platform.system() != "Linux":
        return []

    candidatas: list[str] = []
    for sp in {p for p in sys.path if p.endswith(("site-packages", "dist-packages"))}:
        candidatas += glob.glob(os.path.join(sp, "nvidia", "*", "lib", "*.so*"))

    # cublasLt antes que cublas, cublas antes que cudnn: si se cargan al revés
    # el primer intento falla por símbolos sin resolver.
    prioridad = ("cublasLt", "cublas", "cudart", "cufft", "curand",
                 "cusparse", "cusolver", "cudnn")

    def rango(p: str) -> int:
        base = os.path.basename(p)
        for i, k in enumerate(prioridad):
            if k in base:
                return i
        return len(prioridad)

    candidatas.sort(key=rango)

    cargadas: list[str] = []
    pendientes = list(dict.fromkeys(candidatas))
    # Varias pasadas: una .so puede depender de otra que aún no se ha cargado.
    for _ in range(3):
        if not pendientes:
            break
        fallidas = []
        for lib in pendientes:
            try:
                ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                cargadas.append(lib)
            except OSError:
                fallidas.append(lib)
        if len(fallidas) == len(pendientes):
            break  # no hubo progreso, no insistas
        pendientes = fallidas

    return cargadas


@dataclass
class EnvFingerprint:
    """Todo lo que puede mover un score. Se persiste junto a cada embedding."""

    facid_version: str
    python_version: str
    platform: str
    insightface_version: str | None = None
    onnxruntime_version: str | None = None
    onnxruntime_package: str | None = None
    numpy_version: str | None = None
    opencv_version: str | None = None
    available_providers: list[str] = field(default_factory=list)
    active_providers: dict[str, list[str]] = field(default_factory=dict)
    device_requested: str = ""
    model_pack: str = MODEL_PACK
    model_dir: str = ""
    model_files: dict[str, str] = field(default_factory=dict)  # archivo -> sha256
    det_size: str = ""
    cuda_libs_preloaded: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _version(mod_name: str) -> str | None:
    try:
        import importlib
        return getattr(importlib.import_module(mod_name), "__version__", None)
    except Exception:
        return None


def _onnxruntime_package_name() -> str | None:
    """Distingue onnxruntime-gpu de onnxruntime: ambos importan como `onnxruntime`."""
    try:
        from importlib.metadata import distributions
        encontrados = []
        for d in distributions():
            nombre = (d.metadata["Name"] or "").lower()
            if nombre.startswith("onnxruntime"):
                encontrados.append(f"{nombre}=={d.version}")
        return ", ".join(sorted(encontrados)) if encontrados else None
    except Exception:
        return None


@dataclass
class FaceRuntime:
    """Handle del modelo cargado + la huella del entorno que lo produjo."""

    app: Any
    fingerprint: EnvFingerprint

    @property
    def rec_model_file(self) -> str:
        for nombre in self.fingerprint.model_files:
            if nombre.startswith("w600k") or "glintr" in nombre:
                return nombre
        return ""

    @property
    def rec_model_sha256(self) -> str:
        return self.fingerprint.model_files.get(self.rec_model_file, "")

    @property
    def det_model_file(self) -> str:
        for nombre in self.fingerprint.model_files:
            if nombre.startswith("det_") or "scrfd" in nombre.lower():
                return nombre
        return ""

    @property
    def det_model_sha256(self) -> str:
        return self.fingerprint.model_files.get(self.det_model_file, "")

    @property
    def provider_activo(self) -> str:
        """El provider realmente en uso por el modelo de reconocimiento."""
        provs = self.fingerprint.active_providers.get("recognition", [])
        return provs[0] if provs else "DESCONOCIDO"


def load_runtime(device: str = "cuda", require_gpu: bool | None = None,
                 verbose: bool = True) -> FaceRuntime:
    """Carga el pack de modelos y verifica el provider REAL de cada sesión.

    device: 'cuda' | 'cpu'
    require_gpu: si None se deduce (True cuando device=='cuda'). Ponlo en False
                 para aceptar el fallback a CPU a sabiendas y sin excepción.
    """
    device = device.lower()
    if device not in ("cuda", "cpu"):
        raise ValueError(f"device debe ser 'cuda' o 'cpu', no {device!r}")
    if require_gpu is None:
        require_gpu = device == "cuda"

    n_libs = len(preload_cuda_libs()) if device == "cuda" else 0

    import onnxruntime as ort
    from insightface.app import FaceAnalysis

    providers = [CUDA_PROVIDER, CPU_PROVIDER] if device == "cuda" else [CPU_PROVIDER]
    ctx_id = 0 if device == "cuda" else -1

    disponibles = list(ort.get_available_providers())
    if device == "cuda" and CUDA_PROVIDER not in disponibles:
        raise ProviderError(
            f"onnxruntime no expone {CUDA_PROVIDER}. Compilados: {disponibles}.\n"
            f"Paquete instalado: {_onnxruntime_package_name()}.\n"
            "Causa tipica: se instalo 'onnxruntime' (CPU) en vez de 'onnxruntime-gpu', "
            "o ambos conviven en el venv. Desinstala AMBOS y reinstala solo el -gpu.\n"
            "Para seguir de todos modos: --device cpu"
        )

    app = FaceAnalysis(
        name=MODEL_PACK,
        root=str(MODEL_ROOT),
        allowed_modules=list(ALLOWED_MODULES),
        providers=providers,
    )
    app.prepare(ctx_id=ctx_id, det_size=DET_SIZE)

    # --- El chequeo que importa: que provider quedo en cada sesion real ---
    activos: dict[str, list[str]] = {}
    for taskname, modelo in app.models.items():
        sess = getattr(modelo, "session", None)
        if sess is not None:
            activos[taskname] = list(sess.get_providers())

    model_dir = Path(app.model_dir)
    hashes = {p.name: sha256_file(p) for p in sorted(model_dir.glob("*.onnx"))}

    fp = EnvFingerprint(
        facid_version=__version__,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        insightface_version=_version("insightface"),
        onnxruntime_version=ort.__version__,
        onnxruntime_package=_onnxruntime_package_name(),
        numpy_version=_version("numpy"),
        opencv_version=_version("cv2"),
        available_providers=disponibles,
        active_providers=activos,
        device_requested=device,
        model_pack=MODEL_PACK,
        model_dir=str(model_dir),
        model_files=hashes,
        det_size=f"{DET_SIZE[0]}x{DET_SIZE[1]}",
        cuda_libs_preloaded=n_libs,
    )

    en_gpu = any(CUDA_PROVIDER in p for p in activos.values())
    if verbose:
        marca = "GPU (CUDA)" if en_gpu else "CPU"
        print(f"[facid] provider activo: {marca}", file=sys.stderr)
        for task, provs in activos.items():
            print(f"[facid]   {task:<12} -> {provs}", file=sys.stderr)

    if require_gpu and not en_gpu:
        raise ProviderError(
            "Se pidio CUDA pero TODAS las sesiones quedaron en CPU: este es "
            "exactamente el fallback silencioso que el PoC debe detectar.\n"
            f"  providers compilados : {disponibles}\n"
            f"  providers activos    : {activos}\n"
            f"  libs nvidia pip precargadas: {n_libs}\n"
            f"  onnxruntime          : {_onnxruntime_package_name()}\n"
            "Revisa: driver NVIDIA (nvidia-smi), cuDNN 9 presente, y que una RTX 50xx "
            "(Blackwell/sm_120) exige onnxruntime-gpu >=1.22 sobre CUDA 12.8+.\n"
            "Para correr a sabiendas en CPU: --device cpu (los embeddings son "
            "equivalentes; cambia la velocidad, no la calibracion)."
        )

    return FaceRuntime(app=app, fingerprint=fp)

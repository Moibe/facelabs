#!/usr/bin/env bash
# Setup del PoC en la maquina Ubuntu con GPU NVIDIA.
#
#   ./setup.sh              instala el venv y los modelos
#   ./setup.sh --cuda-pip   ademas instala CUDA/cuDNN como ruedas pip
#                           (usalo si NO tienes un CUDA de sistema con cuDNN 9)
#   ./setup.sh --cpu        solo CPU: instala onnxruntime en vez de -gpu
#
# Termina corriendo `facid doctor`, que es quien de verdad dice si quedaste en
# GPU o en CPU. Si el doctor dice CPU y esperabas GPU, el setup NO quedo bien.
set -euo pipefail

CUDA_PIP=0
SOLO_CPU=0
for arg in "$@"; do
  case "$arg" in
    --cuda-pip) CUDA_PIP=1 ;;
    --cpu)      SOLO_CPU=1 ;;
    -h|--help)  sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Argumento desconocido: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")"
echo "=== facid setup ==="
echo "Directorio: $(pwd)"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "[!] Este script asume Linux. En otro SO instala a mano desde requirements.txt." >&2
fi

# ---------------------------------------------------------------- 1. GPU
echo
echo "--- GPU ---"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
  ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 || echo "")
  [[ -n "$ARCH" ]] && echo "compute capability: $ARCH"
  if [[ "$ARCH" == 12.* ]]; then
    echo "[i] Blackwell (sm_120) detectado: exige onnxruntime-gpu >=1.22 sobre CUDA 12.8+."
    echo "    Con una version anterior el provider CUDA carga pero no tiene kernels y"
    echo "    onnxruntime se cae a CPU en silencio. Por eso corremos doctor al final."
  fi
else
  echo "[!] No hay nvidia-smi. Sin driver NVIDIA no habra GPU."
  [[ $SOLO_CPU -eq 0 ]] && echo "    Considera ./setup.sh --cpu"
fi

# ---------------------------------------------------------------- 2. venv
echo
echo "--- venv ---"
if ! python3 -c "import venv" >/dev/null 2>&1; then
  echo "[X] Falta el modulo venv. Instala:  sudo apt install python3-venv python3-dev" >&2
  exit 1
fi
python3 -m venv .venv
# insightface 0.7.3 compila extensiones Cython: necesita compilador y headers.
if ! command -v gcc >/dev/null 2>&1; then
  echo "[!] No hay gcc. insightface no va a compilar."
  echo "    sudo apt install build-essential python3-dev"
fi

PY=".venv/bin/python"
$PY -m pip install --quiet --upgrade pip setuptools wheel
# Cython y numpy deben existir ANTES de compilar insightface.
$PY -m pip install --quiet "numpy<2.0" cython

echo
echo "--- dependencias ---"
if [[ $SOLO_CPU -eq 1 ]]; then
  # Evita que convivan onnxruntime y onnxruntime-gpu: ambos exponen el mismo
  # modulo y cual gana depende del orden de instalacion. Fuente clasica de
  # "tengo onnxruntime-gpu instalado pero corre en CPU".
  $PY -m pip uninstall -y onnxruntime-gpu >/dev/null 2>&1 || true
  grep -v '^onnxruntime-gpu' requirements.txt > /tmp/facid_req_cpu.txt
  echo "onnxruntime>=1.22.0" >> /tmp/facid_req_cpu.txt
  $PY -m pip install -r /tmp/facid_req_cpu.txt
else
  $PY -m pip uninstall -y onnxruntime >/dev/null 2>&1 || true
  $PY -m pip install -r requirements.txt
fi

if [[ $CUDA_PIP -eq 1 && $SOLO_CPU -eq 0 ]]; then
  echo
  echo "--- CUDA/cuDNN via pip ---"
  echo "(facid.runtime las precarga con ctypes al arrancar, asi que no hace"
  echo " falta exportar LD_LIBRARY_PATH)"
  $PY -m pip install --quiet \
    nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cudnn-cu12 \
    nvidia-cufft-cu12 nvidia-curand-cu12
fi

# ---------------------------------------------------------------- 3. modelos
echo
echo "--- modelos (buffalo_l, ~300 MB) ---"
echo "Unica vez que este proyecto toca la red. La inferencia es 100% local."
$PY - <<'PYCODE'
import sys
sys.path.insert(0, ".")
from facid.config import MODEL_PACK, MODEL_ROOT
from insightface.utils.storage import ensure_available
d = ensure_available("models", MODEL_PACK, root=str(MODEL_ROOT))
print(f"modelos en: {d}")
PYCODE

# ---------------------------------------------------------------- 4. doctor
echo
echo "=========================================================="
echo " VERIFICACION — esto es lo que decide si el setup sirvio"
echo "=========================================================="
DEV="cuda"; [[ $SOLO_CPU -eq 1 ]] && DEV="cpu"
if $PY -m facid doctor --device "$DEV" --json out/entorno.json; then
  echo
  echo "Setup OK. Activa el venv con:  source .venv/bin/activate"
  echo "Siguiente paso: arma tu set en data/ y tu manifiesto (ver data/README.md)."
else
  echo
  echo "[X] doctor fallo. El mensaje de arriba dice por que." >&2
  echo "    Si el problema es CUDA, prueba:  ./setup.sh --cuda-pip" >&2
  echo "    Para avanzar en CPU mientras tanto:  ./setup.sh --cpu" >&2
  exit 1
fi

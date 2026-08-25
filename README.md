# facid — PoC de verificación facial 1:1

Pipeline local que recibe dos imágenes y devuelve un score de similitud, más un
harness de evaluación para **calibrar el threshold con datos propios**.

El entregable no es un script que compara dos fotos. Es un experimento medible:
al terminar debes poder responder *en qué valor de similitud trazas la línea, y
con qué evidencia*. Si el resultado es "funciona", el PoC falló.

---

## Restricciones

| Restricción | Detalle |
|---|---|
| **Uso** | Investigación personal, **no comercial**. El código de InsightFace es MIT; **los pesos pre-entrenados no**: están licenciados solo para research no comercial. No integrar en nada de la empresa sin resolver licenciamiento primero. |
| **Alcance** | **Sin liveness / PAD.** Solo comparación de imágenes estáticas. |
| **Ejecución** | 100% local. La única vez que se toca la red es la descarga inicial de los pesos en `setup.sh`. Ninguna inferencia sale de la máquina. |
| **Datos** | Solo fotos propias o con consentimiento explícito. Ver [`data/README.md`](data/README.md). |

---

## Instalación (Ubuntu + NVIDIA)

```bash
git clone <este-repo> facid && cd facid
./setup.sh                # o ./setup.sh --cuda-pip   si no tienes CUDA de sistema
                          # o ./setup.sh --cpu        para forzar CPU
source .venv/bin/activate
```

`setup.sh` termina corriendo `facid doctor`, que es quien **de verdad** dice si
quedaste en GPU. Si el doctor imprime CPU y esperabas GPU, el setup no sirvió —
no lo ignores.

### Sobre la RTX 5060 Ti (Blackwell, sm_120)

Es hardware nuevo: exige `onnxruntime-gpu >= 1.22` sobre **CUDA 12.8+** y
cuDNN 9. Con una combinación anterior, el `CUDAExecutionProvider` aparece como
disponible, la sesión se crea sin error, y onnxruntime **se cae a CPU en
silencio**. Ese es exactamente el fallo que este PoC está construido para
detectar, y por eso `runtime.py` no confía en `get_available_providers()`:
interroga la sesión ONNX ya construida de cada modelo y truena si quedó en CPU.

Si caes a CPU: los embeddings son equivalentes y **la calibración sigue siendo
válida**; solo cambia la velocidad. Corre con `--device cpu` a sabiendas en vez
de creer que estás usando la GPU.

---

## Uso

```bash
facid() { python -m facid "$@"; }   # o usa `python -m facid ...` directo

facid doctor                                    # entorno, provider activo, hashes
facid extract data/ana/*.jpg                    # extrae y persiste embeddings
facid compare data/ana/ana_01.jpg data/ana/ana_02.jpg   # score crudo

facid run-manifest manifests/mi_set.json -o out/scores.csv
facid calibrate out/scores.csv
```

`calibrate` **no carga el modelo**: trabaja sobre el CSV. Puedes extraer en la
máquina con GPU y analizar en cualquier otra, incluso sin insightface instalado.

---

## Arquitectura

```
imagen -> detección (SCRFD) -> alineación -> embedding 512-d (ArcFace) -> [persistencia]
                                                                                |
                                                    par de embeddings -> coseno -> score
```

Las dos etapas son **módulos independientes**, no una función. Esa separación es
lo que permite recalibrar, cambiar de métrica o comparar contra otro modelo sin
reprocesar una sola imagen.

| Módulo | Responsabilidad |
|---|---|
| [`facid/runtime.py`](facid/runtime.py) | Carga el modelo y **verifica el provider real**. Falla ruidoso si se pidió GPU y quedó CPU. |
| [`facid/extract.py`](facid/extract.py) | `extract_embedding(img, runtime) -> dict`. Errores tipados, nunca excepciones sin contexto. |
| [`facid/store.py`](facid/store.py) | `.npy` + índice SQLite con metadata de reproducibilidad. |
| [`facid/compare.py`](facid/compare.py) | `compare(a, b) -> float`. Score crudo. **No sabe qué es un threshold.** |
| [`facid/decide.py`](facid/decide.py) | La única capa que conoce un umbral, y lo recibe como parámetro. |
| [`facid/harness.py`](facid/harness.py) | Manifiesto de pares -> CSV de scores. |
| [`facid/calibrate.py`](facid/calibrate.py) | FMR/FNMR, traslape, EER, puntos de operación, histograma. |

Ningún threshold está hardcodeado en ninguna función de comparación. El umbral
es el **resultado** del experimento, no una constante del código.

### Persistencia

Cada embedding se guarda como `.npy` más una fila en SQLite con: ruta de origen,
**sha256 del archivo**, `det_score`, bbox, número de rostros detectados, política
de selección, **sha256 del modelo .onnx**, `det_size`, provider activo y versiones
de insightface/onnxruntime/facid.

La llave de caché es `(sha256 imagen, model_pack, sha256 modelo, det_size)`.
Todo lo que puede mover un embedding entra en la llave: cambiar el modelo o el
`det_size` invalida lo guardado a propósito. Un embedding elegido con
`--face-policy largest` **no** se reutiliza bajo `strict`, porque bajo `strict`
esa imagen debe volver a fallar.

### Casos borde de la extracción

| Situación | Comportamiento |
|---|---|
| Cero rostros | `error = NO_FACE`, embedding `None` |
| Múltiples rostros, `strict` (default) | `error = MULTIPLE_FACES` + el conteo y todos los `det_score`. No adivina. |
| Múltiples rostros, `largest` | Toma el de mayor área y lo **registra** en `face_selection` (`mayor_area_de_3`), que viaja hasta el CSV |
| Imagen corrupta / formato no soportado | `error = UNREADABLE_IMAGE` |
| Ruta inexistente | `error = FILE_NOT_FOUND` |

---

## El análisis de calibración

`facid calibrate out/scores.csv` genera en `out/`:

- `reporte_calibracion.txt` — el análisis completo en texto
- `sweep_fmr_fnmr.csv` — barrido de threshold 0.00 → 1.00 paso 0.01
- `histograma.png` — distribuciones match vs non-match, con la zona de traslape

y responde en pantalla las cuatro preguntas del criterio de aceptación:

1. rango de scores para la misma persona
2. rango para personas distintas
3. si se traslapan y **dónde** (ancho de la banda, cuántos pares caen dentro)
4. qué threshold elegir, con la tasa de error que aceptas a cambio

### Sobre el margen de error

Con ~15 pares, los porcentajes puntuales son casi ruido. Cada FMR y FNMR se
reporta con su **intervalo de confianza binomial exacto (Clopper-Pearson 95%)**,
porque el criterio de aceptación pide "un número con su margen de error conocido"
y sin el intervalo ese número no existe.

Consecuencia concreta: **0 falsas aceptaciones en 7 pares non-match no es
"FMR = 0%"** — el intervalo al 95% llega hasta ~41%. El reporte marca con `(*)`
todo objetivo más fino que la resolución del set (con `n` pares non-match, la
FMR no-nula más chica medible es `1/n`) para que no confundas "no observé
errores" con "no hay errores".

Convención, idéntica en `decide.py` y en `calibrate.py`:

```
aceptar el par  <=>  score >= threshold
FMR(t)  = fracción de pares NON-MATCH con score >= t   (falsa aceptación)
FNMR(t) = fracción de pares MATCH     con score <  t   (falso rechazo)
```

Un par que falló la extracción **no** entra como score 0: queda excluido y
contado aparte. Meterlo contaminaría la distribución con un fallo de detección.

---

## Pruebas

```bash
python tests/test_pipeline_sin_modelo.py
```

Sustituye el modelo por un doble de prueba y ejercita el pipeline completo
—contrato de extracción, casos borde, persistencia, harness y calibración—
sin necesitar insightface ni GPU. Los números de calibración están verificados
a mano contra un set de scores conocido.

No valida insightface ni CUDA. Eso lo verifica `facid doctor` en la máquina real.

---

## Fuera de alcance (no implementado, a propósito)

Liveness / PAD · búsqueda 1:N · captura desde cámara o video · fine-tuning ·
cualquier integración con servicios de nube · UI.

---

## Nota sobre el modelo

El pack `buffalo_l` usa **SCRFD** para detección (`det_10g.onnx`) y **ArcFace
sobre ResNet50** para embeddings (`w600k_r50.onnx`, 512-d) — no ResNet100, que
es el de `antelopev2` (`glintr100.onnx`). Da igual para el diseño del PoC, pero
el número importa al comparar contra resultados publicados.

Por eso `doctor` reporta el **sha256 de cada `.onnx`** y no solo el nombre del
pack: la etiqueta puede mentir, el hash no.

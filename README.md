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

### CPU o GPU

Las dos están soportadas de primera clase, no como parche: `--device cpu` en
cualquier comando del modelo, y `./setup.sh --cpu` instala `onnxruntime` en vez
de `onnxruntime-gpu` (**desinstalando** el otro primero — los dos exponen el
mismo módulo `onnxruntime` y si conviven, cuál gana depende del orden de
instalación; es la causa clásica de "tengo el -gpu instalado pero corre en CPU").

A la escala de este PoC la GPU no aporta nada, y no por "son pocas fotos":

- **La extracción se paga una sola vez.** Cada foto se procesa, su embedding se
  guarda, y no se vuelve a tocar. Aunque CPU fuera 20× más lento, son segundos
  para una docena de fotos.
- **El ciclo que vas a iterar no usa el modelo.** Recalibrar, mover el
  threshold, agregar pares: todo eso es aritmética sobre el CSV. `calibrate` ni
  importa onnxruntime.

Práctico: intenta `./setup.sh` primero. Si el stack de CUDA pelea —riesgo real en
Blackwell— no le dediques horas: `./setup.sh --cpu` y sigue. La GPU vale
arreglarla si escalas a cientos de imágenes.

#### Un matiz: no son bit a bit idénticos

El orden de las sumas en float32 difiere, lo que mueve el embedding ~1e-6.
Irrelevante frente a un threshold que vive en la segunda o tercera decimal.

Pero **el detector tiene un umbral adentro** (`det_thresh=0.5`). Una cara
marginal —borrosa, chica, ángulo extremo— puede quedar apenas por encima en un
provider y apenas por debajo en el otro; y un corrimiento de fracción de pixel
en el bbox cambia el recorte, que mueve el embedding bastante más que 1e-6. Para
una cara nítida da igual; para el caso difícil que metiste a propósito, no
necesariamente.

Por eso el provider **no** entra en la llave de caché (forzar reprocesar todo
por 1e-6 sería peor), pero sí viaja al CSV en las columnas `provider_a` /
`provider_b`, y el reporte avisa si el set salió de providers mezclados —cosa
que pasa sin más al extraer en GPU y luego correr con `--device cpu`, porque lo
ya guardado sale de caché y solo lo nuevo se recalcula.

---

## Uso

```bash
facid() { python -m facid "$@"; }   # o usa `python -m facid ...` directo

facid doctor                                    # entorno, provider activo, hashes
facid extract data/yo/*.jpg                     # extrae y persiste embeddings
facid compare data/yo/01_ancla.jpg data/yo/03_luz_distinta.jpg   # score crudo

facid init-manifest data -o manifests/mi_set.json   # genera el manifiesto
facid run-manifest manifests/mi_set.json -o out/scores.csv
facid calibrate out/scores.csv
```

`init-manifest` deduce las etiquetas de la estructura de carpetas: **misma
carpeta = misma persona, carpeta distinta = personas distintas**. Es el paso más
propenso a errores hecho a mano — un `same_person` equivocado envenena la
calibración y nada te avisa. Genera el archivo con las notas pre-llenadas a
partir de los nombres de archivo; tú las corriges.

Dos modos:

| Modo | Qué pares genera | Cuándo |
|---|---|---|
| `ancla` (default) | Cada foto contra la primera de su persona; las anclas entre sí | Reportar un threshold |
| `todos` | Todas las combinaciones posibles | Explorar |

`todos` da muchos más pares pero salidos de **las mismas fotos**, así que
estrecha los intervalos de confianza sin agregar información. Ver la sección
siguiente.

`calibrate` **no carga el modelo**: trabaja sobre el CSV. Puedes extraer en la
máquina con GPU y analizar en cualquier otra, incluso sin insightface instalado.

---

## Tablero visual (opcional)

Además de la CLI hay un front para ver los resultados —y sobre todo **las fotos
junto a su score**, que en un CSV no se ven.

```bash
.venv/bin/python -m pip install -r api/requirements.txt   # una vez
cd web && npm install                                     # una vez
npm run dev                                               # levanta API + front
```

| Carpeta | Qué es |
|---|---|
| [`api/`](api/) | FastAPI delgado sobre `facid`. Sólo lectura salvo dos POST |
| [`web/`](web/) | SvelteKit 5 + TS, gradiente azul rey y barras de vidrio |

**La CLI no depende de nada de esto.** `api/` importa `facid`; `facid` nunca
importa `api/`, y hay una prueba que lo verifica leyendo el código fuente de cada
módulo. `fastapi` y `uvicorn` viven en `api/requirements.txt`, no en el de la
raíz: puedes instalar y usar la CLI completa sin ellos.

Tampoco hay logica duplicada: el API llama a las **mismas** funciones que la
terminal. Si un número del front no cuadra con el de la CLI, es un bug de
serialización, no dos implementaciones que se separaron.

Cuatro páginas: **Panorama** (histograma + slider de threshold con FMR/FNMR
vivos), **Pares** (las dos fotos de cada par, con filtro "sólo errores"),
**El set** (dependencia entre pares y fragilidad por persona) y **Entorno**
(`doctor` visual). Detalles en [`web/README.md`](web/README.md).

Corre sólo en `localhost`, sin deploy ni dominio: el tablero muestra caras de
personas que dieron consentimiento para un experimento local.

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
| [`facid/harness.py`](facid/harness.py) | Manifiesto de pares -> CSV de scores, y generación del manifiesto desde `data/`. |
| [`facid/calibrate.py`](facid/calibrate.py) | FMR/FNMR, traslape, EER, puntos de operación, histograma. |
| [`facid/dependencia.py`](facid/dependencia.py) | Mide qué tan dependientes son los pares entre sí y qué tan frágil es el resultado. |
| [`api/main.py`](api/main.py) | Capa HTTP para el tablero. Importa `facid`; `facid` nunca la importa. |
| [`web/`](web/) | Tablero SvelteKit. Sólo mira: no calcula nada. |

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

### Los pares no son independientes, y eso importa

Clopper-Pearson asume que cada par es una observación independiente. **En un set
de verificación facial eso es falso**: los pares se construyen combinando un
puñado de fotos, y la foto ancla suele aparecer en casi todos los pares match de
su persona. Si esa foto salió mal, no arrastra un par: arrastra todos los suyos,
juntos.

Por eso los intervalos que imprime el reporte son **optimistas** — el verdadero
es más ancho. Con 3-4 personas no hay forma honesta de corregirlo, así que en
lugar de fingir precisión el reporte hace dos cosas:

- **Sección 0 — composición del set.** Cuántos pares sobre cuántas fotos de
  cuántas personas, y en cuántos pares participa la foto más reusada. Las
  identidades se deducen de los propios pares match por transitividad, así que
  también detecta **contradicciones de etiquetado**: un par marcado
  `same_person: false` entre dos fotos que tus pares match conectan como la
  misma persona es un error de captura, y te lo dice.
- **Sección de fragilidad — jackknife por persona.** Quita a una persona
  completa, recalcula el threshold, y repite con cada una. Si el threshold se
  mueve mucho, el número no describe tu sistema: describe a esa persona. Es lo
  que un intervalo de confianza no te puede decir cuando los pares están
  correlacionados.

La conclusión práctica: para afirmar algo más fino, lo que hace falta son más
**personas**, no más pares. Agregar pares sacados de las mismas fotos angosta
los intervalos sin agregar evidencia — es exactamente lo que hace
`--modo todos`, y por eso no es el default.

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
python tests/test_pipeline_sin_modelo.py   # pipeline: 118 aserciones
python tests/test_api.py                   # API: 54 aserciones
cd web && npm run check                    # tipos del front
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

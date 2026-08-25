# API — capa de lectura sobre la CLI

```bash
# desde la raíz del repo, con el venv del proyecto
.venv/bin/python -m pip install -r api/requirements.txt
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8077
```

Docs interactivas en <http://127.0.0.1:8077/docs>.

Normalmente no hace falta arrancarlo a mano: `npm run dev` desde [`../web/`](../web/)
levanta el API y el front juntos.

## Regla de dependencia

`api/` importa `facid`; **`facid` nunca importa `api/`**. La CLI funciona igual con
esto apagado, desinstalado o borrado — hay una prueba que lo verifica leyendo el
código fuente de cada módulo del paquete.

Y ninguna función de calibración vive aquí: todo sale de llamar a las mismas
funciones que usa la terminal. Si un número del front no cuadra con el de la CLI,
es un bug de serialización, no dos implementaciones que se separaron.

Por eso `fastapi` y `uvicorn` están en `api/requirements.txt` y **no** en el
`requirements.txt` de la raíz.

## Por qué los GET no escriben nada

`calibrate.calibrar()` escribe el PNG, el sweep CSV y el reporte de texto como
efecto secundario. El endpoint `/api/resultados` **no** la llama: arma la
respuesta con las primitivas (`cargar_scores`, `describir`, `zona_traslape`,
`eer`, `barrido`, `punto_operacion`, `estructura`, `jackknife_por_persona`). Mismo
código, sin efectos secundarios en un GET.

## Seguridad

Escucha sólo en `127.0.0.1`. Toda ruta que viene del cliente se ancla dentro de
`data/` o `out/` con `Path.is_relative_to` — sin eso, `?ruta=../../..` sería un
lector de archivos arbitrario, y "es local" no es una defensa. Hay pruebas que
intentan seis variantes de path traversal y verifican que ninguna devuelva 200.

CORS acepta sólo `localhost`/`127.0.0.1` en los puertos donde vive un dev server
de Vite (41 7x–41 9x y 51 7x–51 9x), por regex y no por lista fija: si el 5173
está ocupado Vite se mueve al 5174 o 5175, y con lista fija el browser bloquearía
cada llamada — que en pantalla se ve **idéntico** a tener la API apagada.

## Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/` | `{name, version}` — firma de identidad que usa `dev.mjs` |
| GET | `/health` | sonda de arranque |
| GET | `/api/personas` | carpetas de `data/` con sus fotos. No toca el modelo |
| GET | `/api/foto?ruta=` | sirve una imagen de `data/`. Es lo que permite ver la cara junto al score |
| GET | `/api/csvs` | CSVs de scores disponibles en `out/` (filtra los `sweep_*`, que son salida) |
| GET | `/api/resultados?csv=` | el análisis completo en JSON |
| GET | `/api/tasas?csv=&threshold=` | FMR/FNMR con IC en un threshold exacto |
| GET | `/api/entorno?device=` | equivalente de `facid doctor`. Carga el modelo |
| GET | `/api/store` | resumen del índice SQLite |
| POST | `/api/manifiesto` | `init_manifest` |
| POST | `/api/corrida` | `run_manifest`. Lo único que carga el modelo y puede tardar |

## Pruebas

```bash
.venv/bin/python tests/test_api.py
```

Sin modelo ni GPU: fixture de fotos sintéticas + un CSV como el que escribe el
harness.

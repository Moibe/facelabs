# web — tablero visual de facid

SvelteKit 5 + TypeScript, gradiente azul rey y barras de vidrio con tilt. Lee todo
del API; **no calcula nada**.

```bash
cd web
npm install
npm run dev        # levanta el API y el front juntos
```

`npm run dev` corre [`scripts/dev.mjs`](scripts/dev.mjs): arranca uvicorn con el
venv de la raíz, espera a que `/health` responda, y sólo entonces lanza Vite. Los
logs van prefijados (`[api]` cian, `[web]` magenta, `[dev]` amarillo) y Ctrl+C
mata las dos cosas.

| Script | Qué hace |
|---|---|
| `npm run dev` | API + front |
| `npm run dev:web-only` | sólo el front (si ya tienes uvicorn corriendo) |
| `npm run check` | `svelte-check` |
| `npm run build` | build estático a `build/` |

Overrides por env: `API_PORT`, `API_HOST`, `API_DIR`, `API_ENTRYPOINT`,
`VITE_API_URL`. El default del API es **8077**, no 8000 — en la máquina del
usuario el 8000 ya lo tenía otro proyecto.

## Páginas

| Ruta | Para qué |
|---|---|
| `/` | **Panorama**. Histograma + slider de threshold, con FMR/FNMR vivos y las 4 respuestas del criterio de aceptación |
| `/pares` | Cada par con **las dos fotos**, su score y si el threshold actual lo clasifica bien. Filtro "sólo errores" |
| `/set` | Composición, dependencia entre pares, fragilidad por persona, procedencia de los embeddings, y las fotos de `data/` |
| `/entorno` | `facid doctor` visual: provider activo real y sha256 de cada `.onnx` |

El threshold vive en un estado compartido: moverlo en el panorama y saltar a
`/pares` muestra los mismos errores. Si cada página tuviera el suyo, verías dos
verdades distintas del mismo set.

## Decisiones que no son de estilo

- **`adapter-static` con `fallback: index.html` y `ssr = false`.** Es un tablero
  local que lee del API en runtime: no hay nada que renderizar en servidor, y
  prerenderizar rutas que hacen `fetch` al API haría fallar el build en cualquier
  máquina donde uvicorn no esté arriba — o sea, casi siempre.
- **El área de plot tiene su propia superficie oscura**, no hereda el gradiente.
  Sobre el extremo claro del azul rey los dos colores de serie caen a 2.6:1 y
  2.1:1 de contraste; sobre `--plot-surface` pasan 3:1.
- **La paleta de series está validada, no elegida a ojo.** `#0d9488` / `#ea580c`
  pasan banda de luminosidad, piso de croma, separación CVD (ΔE 13.8 protan), piso
  de visión normal (28.8) y contraste. **No cambies un hex sin volver a correr el
  validador**: el azul quedó descartado como color de serie porque se confunde con
  el fondo.
- **La porción mal clasificada de cada barra se marca con textura + contorno**, no
  sólo con color: así el error se distingue en impresión, en daltonismo y en
  forced-colors.
- **Los intervalos de confianza se piden al API**, no se recalculan en TypeScript.
  Los conteos (`score >= threshold`) sí son locales, para que el slider responda
  al instante — es la misma regla de una línea que `decide.py`, no una segunda
  implementación del análisis.

## Sin deploy, a propósito

Esto no lleva `adapter-node`, ni GitHub Action, ni pm2, ni bloque de nginx — la
desviación del patrón habitual es deliberada: el tablero muestra fotos de personas
que dieron consentimiento para un experimento local. Ponerlo en un dominio público
sería sacar esas caras de la máquina. Corre en `localhost` y ahí se queda.

# Diseño del set de prueba

Aquí está el valor del experimento, no en el código. El código ya está escrito y
probado; lo que decide si el PoC enseña algo es **qué fotos metes**.

## Regla de datos (no negociable)

Solo fotos **tuyas** o de personas que dieron **consentimiento explícito** para
este uso. Nada de scraping, nada de datasets de terceros sin revisar su licencia.

`data/` está en `.gitignore` — las imágenes no se versionan. El consentimiento
cubre un experimento, no un repo git perpetuo ni un push accidental a un remoto.

## Estructura sugerida

```
data/
├── ana/   ana_01.jpg ... ana_07.jpg
├── beto/  beto_01.jpg ...
├── cris/  ...
└── dani/  ...
```

Una carpeta por persona. Los nombres no importan para el pipeline (la identidad
la fija el manifiesto), pero te ahorran errores al armarlo.

## Qué debe incluir

El objetivo **no** es que salga bien. Es encontrar dónde falla. Un set donde
todo separa limpio no te dio un threshold: te dijo que el set era fácil.

### Pares match (misma persona) — varía a propósito

| Eje | Qué buscar |
|---|---|
| Iluminación | interior amarillo vs exterior mediodía vs contraluz |
| Ángulo / pose | frontal vs perfil ~45° vs cabeza inclinada |
| Tiempo | meses, y si puedes, años de separación |
| Oclusión / estilo | con y sin lentes, barba vs rasurado, cambio de peinado |
| Resolución | foto de credencial o de cámara de seguridad vs retrato nítido |

### Pares non-match (personas distintas) — los que importan

- **Familiares o gente con parecido real.** Estos son los que mueven el
  threshold. Sin ellos tu FMR es optimista y no lo sabes.
- **Rasgos genuinamente distintos.** Casos fáciles, sirven de piso: te dicen
  dónde vive el score cuando no hay ninguna relación.

### Tamaño

Mínimo ~10-15 pares para ver estructura. Ten presente lo que eso implica:
con 7 pares non-match, la FMR distinta de cero más chica que puedes **medir**
es 1/7 ≈ 14%. Cero errores en 7 pares no es "FMR = 0%": el intervalo de
confianza al 95% llega hasta ~41%. `facid calibrate` te lo dice explícitamente
en cada punto de operación en vez de dejarte creer el 0.

Si quieres afirmar algo sobre FMR del orden de 1%, necesitas cientos de pares
non-match. Eso está fuera del alcance de este PoC — pero el número que sí
puedes defender sale de aquí.

## Cómo escribir el manifiesto

No lo escribas a mano. Una vez que las fotos estén en sus carpetas:

```bash
python -m facid init-manifest data -o manifests/mi_set.json
```

Deduce las etiquetas de las carpetas (misma carpeta = misma persona) y te dice
de una vez qué resolución de FMR acabas de comprar con ese número de pares.
Luego abre el archivo y corrige las notas.

Si prefieres hacerlo a mano, copia `manifests/ejemplo.json`. Las rutas se
resuelven **relativas al propio archivo de manifiesto**, así que desde
`manifests/` se escribe `../data/yo/01_ancla.jpg`.

```json
{"img_a": "...", "img_b": "...", "same_person": true, "notes": "por qué es difícil"}
```

Llena `notes` en serio. Cuando veas un par match con score bajo, la nota es lo
único que te va a decir si fue el ángulo, la luz o los cinco años de diferencia.
Es la columna que convierte un CSV en un diagnóstico.

## Errores comunes al armar el set

- **Fotos del mismo disparo** contadas como pares distintos: inflan la
  distribución match con casos triviales y te dan un threshold falsamente alto.
- **Recortes ya centrados en la cara** de un lado y fotos de cuerpo completo del
  otro: estás midiendo el detector, no el reconocedor.
- **Varias caras en una foto**: por defecto (`--face-policy strict`) el pipeline
  falla con `MULTIPLE_FACES` en vez de adivinar. Recorta la imagen, o corre con
  `--face-policy largest` sabiendo que la elección queda registrada en la
  columna `face_selection_a/b` del CSV.
- **Fotos de celular en vertical**: si vienen sin orientación EXIF correcta, el
  detector puede no encontrar nada. Si te sale `NO_FACE` en algo que claramente
  tiene una cara, ábrela y verifica la rotación.

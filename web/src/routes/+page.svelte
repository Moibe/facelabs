<script lang="ts">
	import Histograma from '$lib/Histograma.svelte';
	import { api, type Tasas } from '$lib/api';
	import { cargar, estado, fijarThreshold } from '$lib/estado.svelte';

	const d = $derived(estado.datos);

	const scores = $derived.by(() => {
		const pares = d?.pares ?? [];
		return {
			match: pares.filter((p) => p.same_person && p.score !== null).map((p) => p.score as number),
			nonmatch: pares.filter((p) => !p.same_person && p.score !== null).map((p) => p.score as number)
		};
	});

	// Conteos locales para que el slider responda al instante. Es la MISMA regla
	// que decide.py (score >= threshold), no una reimplementación del análisis:
	// los intervalos de confianza siguen viniendo del API, o sea de scipy.
	const conteos = $derived.by(() => {
		const t = estado.threshold;
		const fp = scores.nonmatch.filter((s) => s >= t).length;
		const fn = scores.match.filter((s) => s < t).length;
		return {
			fp,
			fn,
			fmr: scores.nonmatch.length ? fp / scores.nonmatch.length : NaN,
			fnmr: scores.match.length ? fn / scores.match.length : NaN
		};
	});

	// El intervalo sí se pide al API (debounced): no se recalcula Clopper-Pearson
	// en TypeScript, porque entonces habría dos implementaciones que podrían
	// separarse y el front mentiría distinto que la terminal.
	let ci = $state<Tasas | null>(null);
	let timer: ReturnType<typeof setTimeout> | null = null;

	$effect(() => {
		const t = estado.threshold;
		const csv = estado.csv;
		if (!d?.ok) return;
		if (timer) clearTimeout(timer);
		timer = setTimeout(() => {
			api
				.tasas(csv, t)
				.then((r) => (ci = r))
				.catch(() => (ci = null));
		}, 140);
		return () => {
			if (timer) clearTimeout(timer);
		};
	});

	const rango = $derived.by(() => {
		const todos = [...scores.match, ...scores.nonmatch];
		if (!todos.length) return { min: -0.2, max: 1 };
		return {
			min: Math.floor((Math.min(...todos) - 0.05) * 100) / 100,
			max: Math.ceil((Math.max(...todos) + 0.05) * 100) / 100
		};
	});

	const pct = (v: number | null | undefined) =>
		v === null || v === undefined || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(1)}%`;
</script>

{#if estado.cargando && !d}
	<section class="tarjeta"><p>Cargando…</p></section>
{:else if estado.csvs.length === 0}
	<!-- Estado inicial real: hay fotos pero todavía nadie corrió el pipeline.
	     Sin esto, el primer arranque muestra "no existe el CSV: scores.csv", que
	     es cierto pero no dice qué hacer. -->
	<section class="tarjeta">
		<h1>Todavía no hay resultados</h1>
		<p>
			El tablero lee <code>out/*.csv</code>, que produce la CLI. Aún no hay ninguno. La secuencia,
			desde la raíz del repo:
		</p>
		<pre>python -m facid init-manifest data -o manifests/mi_set.json
python -m facid run-manifest manifests/mi_set.json -o out/scores.csv</pre>
		<p class="tenue">
			<code>init-manifest</code> corre en cualquier máquina (sólo lee carpetas).
			<code>run-manifest</code> carga el modelo, así que necesita la máquina con insightface
			instalado.
		</p>
		<p>
			Mientras tanto, <a href="/set">El set</a> ya funciona: ahí puedes revisar visualmente las fotos
			que metiste antes de gastar tiempo procesándolas.
		</p>
		<button type="button" onclick={() => cargar()}>Reintentar</button>
	</section>
{:else if estado.error && !d?.ok}
	<section class="tarjeta borde-mal">
		<h1>No se puede calibrar</h1>
		<p>{estado.error}</p>
		<p class="tenue">
			Hacen falta pares de las dos clases. Con una sola persona no hay nada que separar.
		</p>
	</section>
{:else if d}
	<header class="encabezado">
		<div>
			<h1>Panorama</h1>
			<p class="tenue">
				{d.composicion?.n_pares ?? d.pares.length} pares sobre
				{d.composicion?.n_imagenes ?? '?'} fotos de
				{d.composicion?.n_identidades ?? '?'} personas · <code>{d.csv}</code>
			</p>
		</div>
		<div class="controles">
			{#if estado.csvs.length > 1}
				<select
					value={estado.csv}
					onchange={(e) => cargar((e.currentTarget as HTMLSelectElement).value)}
				>
					{#each estado.csvs as c (c)}<option value={c}>{c}</option>{/each}
				</select>
			{/if}
			<button type="button" onclick={() => cargar()}>Recargar</button>
		</div>
	</header>

	{#if d.composicion?.contradicciones?.length}
		<section class="tarjeta borde-mal">
			<h2>Etiquetado contradictorio</h2>
			<p>
				{d.composicion.contradicciones.length} par(es) dicen "personas distintas" pero tus pares
				match conectan esas fotos como la misma persona. Es un error de captura en el manifiesto —
				arréglalo antes de creerle a cualquier número de esta página.
			</p>
			<ul class="lista">
				{#each d.composicion.contradicciones as [a, b], i (i)}
					<li><code>{a}</code> vs <code>{b}</code></li>
				{/each}
			</ul>
		</section>
	{/if}

	<!-- ------------------------------------------------ el threshold -->
	<section class="tarjeta">
		<Histograma
			match={scores.match}
			nonmatch={scores.nonmatch}
			threshold={estado.threshold}
			onThreshold={fijarThreshold}
		/>

		<div class="slider-fila">
			<label for="t">Threshold</label>
			<input
				id="t"
				type="range"
				min={rango.min}
				max={rango.max}
				step="0.001"
				value={estado.threshold}
				oninput={(e) => fijarThreshold(Number((e.currentTarget as HTMLInputElement).value))}
			/>
			<output class="valor-t">{estado.threshold.toFixed(3)}</output>
			{#if d.eer?.threshold != null}
				<button type="button" class="chip" onclick={() => fijarThreshold(d.eer!.threshold)}>
					ir al EER ({d.eer.threshold.toFixed(3)})
				</button>
			{/if}
		</div>

		<div class="tasas">
			<div class="tasa">
				<span class="rotulo">Falsas aceptaciones (FMR)</span>
				<span class="numero" class:mal={conteos.fp > 0}>{pct(conteos.fmr)}</span>
				<span class="detalle">
					{conteos.fp} de {scores.nonmatch.length} pares de personas distintas
				</span>
				{#if ci}
					<span class="detalle ic">IC 95%: {pct(ci.fmr_lo)} – {pct(ci.fmr_hi)}</span>
				{/if}
			</div>
			<div class="tasa">
				<span class="rotulo">Falsos rechazos (FNMR)</span>
				<span class="numero" class:mal={conteos.fn > 0}>{pct(conteos.fnmr)}</span>
				<span class="detalle">
					{conteos.fn} de {scores.match.length} pares de la misma persona
				</span>
				{#if ci}
					<span class="detalle ic">IC 95%: {pct(ci.fnmr_lo)} – {pct(ci.fnmr_hi)}</span>
				{/if}
			</div>
		</div>

		<p class="tenue nota-ic">
			Los intervalos son Clopper-Pearson al 95% y vienen del API, o sea del mismo scipy que usa la
			terminal. Son <strong>optimistas</strong>: asumen pares independientes, y tus pares comparten
			fotos. Ver <a href="/set">El set</a>.
		</p>
	</section>

	<!-- ------------------------------------------------ las 4 respuestas -->
	<div class="rejilla">
		<section class="tarjeta">
			<h2>1 · Misma persona</h2>
			{#if d.match.n}
				<p class="hero">{d.match.min?.toFixed(3)} – {d.match.max?.toFixed(3)}</p>
				<p class="tenue">
					n={d.match.n} · mediana {d.match.mediana?.toFixed(3)} · σ {d.match.std?.toFixed(3)}
				</p>
			{/if}
		</section>

		<section class="tarjeta">
			<h2>2 · Personas distintas</h2>
			{#if d.nonmatch.n}
				<p class="hero">{d.nonmatch.min?.toFixed(3)} – {d.nonmatch.max?.toFixed(3)}</p>
				<p class="tenue">
					n={d.nonmatch.n} · mediana {d.nonmatch.mediana?.toFixed(3)} · σ
					{d.nonmatch.std?.toFixed(3)}
				</p>
			{/if}
		</section>

		<section class="tarjeta">
			<h2>3 · ¿Se traslapan?</h2>
			{#if d.traslape?.hay_traslape}
				<p class="hero mal">Sí</p>
				<p class="tenue">
					En [{d.traslape.zona_lo?.toFixed(3)}, {d.traslape.zona_hi?.toFixed(3)}], ancho
					{d.traslape.zona_ancho?.toFixed(3)}. Caen {d.traslape.match_en_zona} match y
					{d.traslape.nonmatch_en_zona} non-match. Ningún threshold separa este set sin errores.
				</p>
			{:else if d.traslape?.hay_traslape === false}
				<p class="hero bien">No</p>
				<p class="tenue">
					Brecha limpia de ancho {d.traslape.brecha_ancho?.toFixed(3)}. Ojo: con pocas personas eso
					casi siempre significa que al set le faltan casos difíciles, no que el sistema sea
					perfecto.
				</p>
			{/if}
			{#if d.d_prime != null}
				<p class="tenue">Separabilidad d' = {d.d_prime.toFixed(2)}</p>
			{/if}
		</section>

		<section class="tarjeta">
			<h2>4 · Resolución del set</h2>
			{#if d.resolucion}
				<p class="hero">{pct(d.resolucion.fmr_minima_medible)}</p>
				<p class="tenue">
					Es la FMR más chica distinta de cero que este set puede <strong>medir</strong>
					(1/{scores.nonmatch.length}). Pedir menos que eso no es una medición, es una
					extrapolación. Para bajarla hacen falta más <strong>personas</strong>, no más pares.
				</p>
			{/if}
		</section>
	</div>

	<!-- ------------------------------------------------ puntos de operación -->
	{#if d.puntos_operacion?.length}
		<section class="tarjeta">
			<h2>Puntos de operación</h2>
			<div class="tabla-scroll">
				<table>
					<thead>
						<tr>
							<th>Objetivo</th>
							<th>Threshold</th>
							<th>FMR</th>
							<th>IC 95%</th>
							<th>FNMR</th>
							<th>IC 95%</th>
							<th></th>
						</tr>
					</thead>
					<tbody>
						{#if d.eer}
							<tr>
								<td>EER (cruce)</td>
								<td class="num">{d.eer.threshold.toFixed(4)}</td>
								<td class="num">{pct(d.eer.fmr)}</td>
								<td class="num tenue">{pct(d.eer.fmr_lo)} – {pct(d.eer.fmr_hi)}</td>
								<td class="num">{pct(d.eer.fnmr)}</td>
								<td class="num tenue">{pct(d.eer.fnmr_lo)} – {pct(d.eer.fnmr_hi)}</td>
								<td>
									<button type="button" class="chip" onclick={() => fijarThreshold(d.eer!.threshold)}>
										usar
									</button>
								</td>
							</tr>
						{/if}
						{#each d.puntos_operacion as p, i (i)}
							<tr>
								<td>
									{p.objetivo}
									{#if p.resolucion_insuficiente}<span
											class="asterisco"
											title="Objetivo más fino que la resolución del set">*</span
										>{/if}
								</td>
								<td class="num">{p.alcanzable ? p.threshold.toFixed(4) : '—'}</td>
								<td class="num">{pct(p.fmr)}</td>
								<td class="num tenue">{pct(p.fmr_lo)} – {pct(p.fmr_hi)}</td>
								<td class="num">{pct(p.fnmr)}</td>
								<td class="num tenue">{pct(p.fnmr_lo)} – {pct(p.fnmr_hi)}</td>
								<td>
									{#if p.alcanzable}
										<button type="button" class="chip" onclick={() => fijarThreshold(p.threshold)}>
											usar
										</button>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="tenue">
				<span class="asterisco">*</span> objetivo más fino que la resolución del set: se reporta el
				threshold de FMR observada 0, pero con este N la única afirmación defendible es la cota
				superior del intervalo, no el 0.
			</p>
		</section>
	{/if}
{/if}

<style>
	.encabezado {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
		margin: 0.4rem 0 1rem;
		flex-wrap: wrap;
	}

	.controles {
		display: flex;
		gap: 0.5rem;
		align-items: center;
	}

	.rejilla {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 1rem;
		margin-bottom: 1rem;
	}

	.rejilla :global(.tarjeta) {
		margin: 0;
	}

	.hero {
		font-size: 1.5rem;
		font-weight: 650;
		color: var(--ink);
		margin: 0 0 0.35rem;
		font-variant-numeric: tabular-nums;
		font-family: ui-monospace, Consolas, monospace;
	}

	.hero.mal {
		color: var(--mal);
	}

	.hero.bien {
		color: var(--bien);
	}

	.slider-fila {
		display: flex;
		align-items: center;
		gap: 0.85rem;
		margin: 1.1rem 0 0.9rem;
		flex-wrap: wrap;
	}

	.slider-fila label {
		font-size: 0.82rem;
		color: var(--ink-3);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	input[type='range'] {
		flex: 1;
		min-width: 180px;
		accent-color: #93c5fd;
	}

	.valor-t {
		font-family: ui-monospace, Consolas, monospace;
		font-size: 1.05rem;
		font-weight: 650;
		color: var(--ink);
		min-width: 5.2ch;
		text-align: right;
	}

	.chip {
		font-size: 0.76rem;
		padding: 0.25rem 0.6rem;
		border-radius: 999px;
	}

	.tasas {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
		gap: 0.9rem;
	}

	.tasa {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		background: rgba(0, 0, 0, 0.2);
		border: 1px solid var(--linea);
		border-radius: 11px;
		padding: 0.75rem 0.9rem;
	}

	.rotulo {
		font-size: 0.74rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--ink-3);
	}

	.numero {
		font-size: 1.7rem;
		font-weight: 650;
		font-family: ui-monospace, Consolas, monospace;
		font-variant-numeric: tabular-nums;
		color: var(--ink);
		line-height: 1.1;
	}

	.numero.mal {
		color: var(--mal);
	}

	.detalle {
		font-size: 0.78rem;
		color: var(--ink-3);
	}

	.detalle.ic {
		font-family: ui-monospace, Consolas, monospace;
	}

	.nota-ic {
		margin-top: 0.8rem;
		margin-bottom: 0;
	}

	.nota-ic a {
		color: #bfdbfe;
	}

	.num {
		font-family: ui-monospace, Consolas, monospace;
		text-align: right;
	}

	.asterisco {
		color: #fcd34d;
		font-weight: 700;
	}

	.borde-mal {
		border-color: rgba(248, 113, 113, 0.4);
		background: rgba(220, 38, 38, 0.1);
	}

	.lista {
		margin: 0.3rem 0 0;
		padding-left: 1.2rem;
		font-size: 0.84rem;
		color: var(--ink-2);
	}
</style>

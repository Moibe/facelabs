<script lang="ts">
	import { api, type Personas } from '$lib/api';
	import { estado } from '$lib/estado.svelte';

	const d = $derived(estado.datos);

	let personas = $state<Personas | null>(null);
	let cargandoP = $state(true);

	$effect(() => {
		api
			.personas()
			.then((p) => (personas = p))
			.catch(() => (personas = null))
			.finally(() => (cargandoP = false));
	});

	const pct = (v: number | null | undefined) =>
		v === null || v === undefined || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(1)}%`;

	const rangoFragilidad = $derived.by(() => {
		const ts = (d?.fragilidad ?? [])
			.map((f) => f.threshold)
			.filter((t): t is number => t !== null);
		if (ts.length < 2) return null;
		return { min: Math.min(...ts), max: Math.max(...ts), rango: Math.max(...ts) - Math.min(...ts) };
	});
</script>

<header class="encabezado">
	<h1>El set</h1>
	<p class="tenue">De qué está hecho, y cuánto se puede confiar en lo que mide.</p>
</header>

<!-- ---------------------------------------------- dependencia entre pares -->
{#if d?.composicion}
	{@const c = d.composicion}
	<section class="tarjeta">
		<h2>Composición</h2>
		<div class="cifras">
			<div><span class="n">{c.n_pares}</span><span class="et">pares</span></div>
			<div><span class="n">{c.n_imagenes}</span><span class="et">fotos</span></div>
			<div><span class="n">{c.n_identidades}</span><span class="et">personas</span></div>
			<div><span class="n">{c.reuso_max}</span><span class="et">reuso máximo</span></div>
		</div>
		<p>
			Cada foto participa en {c.reuso_medio.toFixed(1)} pares en promedio; la más usada aparece en
			<strong>{c.reuso_max}</strong> (<code>{c.img_mas_usada}</code>).
		</p>
		{#if c.reuso_max > 1}
			<p>
				Los pares <strong>no son observaciones independientes</strong>: comparten fotos. Si esa foto
				salió mal, no arrastra un par, arrastra todos los suyos. Por eso los intervalos de confianza
				del panorama son optimistas — el real es más ancho. El tamaño de muestra que manda es el
				número de <strong>personas ({c.n_identidades})</strong>, no el de pares ({c.n_pares}).
			</p>
		{/if}
	</section>
{/if}

<!-- ---------------------------------------------- fragilidad -->
{#if d?.fragilidad?.length}
	<section class="tarjeta">
		<h2>Fragilidad · quitar una persona y recalcular</h2>
		<p class="tenue">
			El threshold de FMR observada 0, recalculado sin cada persona. Es lo que un intervalo de
			confianza no puede decir cuando los pares comparten fotos.
		</p>
		<div class="tabla-scroll">
			<table>
				<thead>
					<tr>
						<th>Sin esta persona</th>
						<th>Fotos</th>
						<th>Pares fuera</th>
						<th>Threshold</th>
						<th>FMR</th>
						<th>FNMR</th>
					</tr>
				</thead>
				<tbody>
					{#each d.fragilidad as f (f.persona)}
						<tr>
							<td><strong>{f.persona}</strong></td>
							<td class="num">{f.n_fotos}</td>
							<td class="num">{f.pares_excluidos}</td>
							<td class="num">{f.threshold?.toFixed(4) ?? 'sin datos'}</td>
							<td class="num">{pct(f.fmr)}</td>
							<td class="num">{pct(f.fnmr)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if rangoFragilidad}
			<p class:alerta={rangoFragilidad.rango > 0.05}>
				El threshold se mueve entre <strong>{rangoFragilidad.min.toFixed(4)}</strong> y
				<strong>{rangoFragilidad.max.toFixed(4)}</strong>
				(rango {rangoFragilidad.rango.toFixed(4)}) según a quién saques.
				{#if rangoFragilidad.rango > 0.05}
					Ese rango es grande: el resultado lo está decidiendo una persona en particular, no tu
					sistema. Con más personas se encogería; con estas, el threshold no es transferible.
				{:else}
					Ninguna persona sola domina el resultado, lo cual da algo de confianza — pero sigue siendo
					un set de pocas personas.
				{/if}
			</p>
		{/if}
	</section>
{/if}

<!-- ---------------------------------------------- providers -->
{#if d?.providers && Object.keys(d.providers).length}
	<section class="tarjeta" class:borde-mal={Object.keys(d.providers).length > 1}>
		<h2>Procedencia de los embeddings</h2>
		{#if Object.keys(d.providers).length === 1}
			<p>
				Todos salieron de <code>{Object.keys(d.providers)[0]}</code>. Queda asentado para
				reproducibilidad.
			</p>
		{:else}
			<p>
				<strong>No todos salieron del mismo provider.</strong> Pasa al extraer en GPU y después
				correr con <code>--device cpu</code> (o al revés): lo ya guardado sale de caché y sólo lo
				nuevo se recalcula.
			</p>
			<ul class="lista">
				{#each Object.entries(d.providers) as [nombre, n] (nombre)}
					<li><code>{nombre}</code> — {n} lado(s) de par</li>
				{/each}
			</ul>
			<p class="tenue">
				Para una cara nítida da igual (difieren ~1e-6). Pero el detector tiene un umbral adentro, así
				que una cara marginal puede recortarse distinto y mover el score bastante más. Si un par raro
				involucra fotos de providers distintos, rehazlo con <code>--force</code> en un solo device
				antes de concluir algo.
			</p>
		{/if}
	</section>
{/if}

<!-- ---------------------------------------------- las fotos -->
<section class="tarjeta">
	<h2>Fotos en data/</h2>
	{#if cargandoP}
		<p>Cargando…</p>
	{:else if !personas || personas.n_personas === 0}
		<p>
			No hay carpetas con imágenes en <code>data/</code>. Cada subcarpeta es una persona: mete sus
			fotos en <code>data/&lt;persona&gt;/</code>.
		</p>
	{:else}
		<p class="tenue">
			{personas.n_personas} personas · {personas.n_fotos} fotos ·
			<code>{personas.data_dir}</code>
		</p>
		{#each personas.personas as p (p.persona)}
			<div class="persona">
				<h3>{p.persona} <span class="tenue">· {p.n_fotos} foto(s)</span></h3>
				<div class="tira">
					{#each p.fotos as f (f.ruta)}
						<figure>
							<img src={api.urlFoto(f.ruta)} alt={f.nombre} loading="lazy" />
							<figcaption>{f.nombre}</figcaption>
						</figure>
					{/each}
				</div>
			</div>
		{/each}
	{/if}
</section>

<style>
	.encabezado {
		margin: 0.4rem 0 1rem;
	}

	.cifras {
		display: flex;
		flex-wrap: wrap;
		gap: 1.5rem;
		margin-bottom: 0.9rem;
	}

	.cifras div {
		display: flex;
		flex-direction: column;
	}

	.n {
		font-size: 1.6rem;
		font-weight: 650;
		font-family: ui-monospace, Consolas, monospace;
		color: var(--ink);
		line-height: 1.1;
	}

	.et {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--ink-3);
	}

	.num {
		font-family: ui-monospace, Consolas, monospace;
		text-align: right;
	}

	.alerta {
		color: #fecaca;
	}

	.borde-mal {
		border-color: rgba(248, 113, 113, 0.4);
		background: rgba(220, 38, 38, 0.09);
	}

	.lista {
		margin: 0.3rem 0 0.7rem;
		padding-left: 1.2rem;
		font-size: 0.84rem;
		color: var(--ink-2);
	}

	.persona {
		margin-top: 1rem;
	}

	h3 {
		font-size: 0.92rem;
		margin: 0 0 0.5rem;
		font-weight: 600;
	}

	.tira {
		display: flex;
		gap: 0.5rem;
		overflow-x: auto;
		padding-bottom: 0.35rem;
	}

	figure {
		margin: 0;
		flex: 0 0 96px;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	img {
		width: 96px;
		height: 96px;
		object-fit: cover;
		border-radius: 9px;
		border: 1px solid rgba(255, 255, 255, 0.14);
		background: rgba(0, 0, 0, 0.35);
		display: block;
	}

	figcaption {
		font-size: 0.66rem;
		color: var(--ink-3);
		font-family: ui-monospace, Consolas, monospace;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		width: 96px;
	}
</style>

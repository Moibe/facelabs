<script lang="ts">
	import { api, type Par } from '$lib/api';
	import { arrastrarRecorte } from '$lib/arrastrar-recorte';
	import { estado, fijarThreshold, objectPosition, veredicto } from '$lib/estado.svelte';

	const d = $derived(estado.datos);

	type Filtro = 'todos' | 'errores' | 'match' | 'nonmatch';
	let filtro = $state<Filtro>('todos');
	let orden = $state<'score' | 'score-desc'>('score');

	const evaluados = $derived.by(() =>
		(d?.pares ?? []).map((p) => ({
			par: p,
			v: veredicto(p.score, p.same_person, estado.threshold)
		}))
	);

	const visibles = $derived.by(() => {
		let xs = evaluados;
		if (filtro === 'errores') xs = xs.filter((x) => x.v !== 'correcto');
		else if (filtro === 'match') xs = xs.filter((x) => x.par.same_person);
		else if (filtro === 'nonmatch') xs = xs.filter((x) => !x.par.same_person);
		const dir = orden === 'score' ? 1 : -1;
		return [...xs].sort((a, b) => dir * ((a.par.score ?? -9) - (b.par.score ?? -9)));
	});

	const nErrores = $derived(evaluados.filter((x) => x.v !== 'correcto').length);

	const rango = $derived.by(() => {
		const todos = (d?.pares ?? []).map((p) => p.score).filter((s): s is number => s !== null);
		if (!todos.length) return { min: -0.2, max: 1 };
		return {
			min: Math.floor((Math.min(...todos) - 0.05) * 100) / 100,
			max: Math.ceil((Math.max(...todos) + 0.05) * 100) / 100
		};
	});

	const ETIQUETA: Record<string, string> = {
		correcto: 'correcto',
		falsa_aceptacion: 'falsa aceptación',
		falso_rechazo: 'falso rechazo',
		sin_score: 'sin score'
	};

	function nombreCorto(ruta: string): string {
		const partes = ruta.replace(/\\/g, '/').split('/');
		return partes.slice(-2).join('/');
	}
</script>

<header class="encabezado">
	<div>
		<h1>Pares</h1>
		<p class="tenue">
			{visibles.length} de {evaluados.length} pares · {nErrores} mal clasificado(s) en t =
			{estado.threshold.toFixed(3)}
		</p>
	</div>
</header>

{#if d}
	<section class="tarjeta pegajoso">
		<div class="slider-fila">
			<label for="t2">Threshold</label>
			<input
				id="t2"
				type="range"
				min={rango.min}
				max={rango.max}
				step="0.001"
				value={estado.threshold}
				oninput={(e) => fijarThreshold(Number((e.currentTarget as HTMLInputElement).value))}
			/>
			<output class="valor-t">{estado.threshold.toFixed(3)}</output>
		</div>
		<div class="filtros">
			{#each [['todos', 'Todos'], ['errores', `Sólo errores (${nErrores})`], ['match', 'Misma persona'], ['nonmatch', 'Personas distintas']] as [k, etq] (k)}
				<button
					type="button"
					class="chip"
					class:activo={filtro === k}
					onclick={() => (filtro = k as Filtro)}
				>
					{etq}
				</button>
			{/each}
			<button
				type="button"
				class="chip orden"
				onclick={() => (orden = orden === 'score' ? 'score-desc' : 'score')}
			>
				score {orden === 'score' ? '↑' : '↓'}
			</button>
		</div>
	</section>

	{#if visibles.length === 0}
		<section class="tarjeta">
			<p>
				{#if filtro === 'errores'}
					Ningún par se clasifica mal en t = {estado.threshold.toFixed(3)}. Mueve el threshold para
					ver dónde empieza a romperse.
				{:else}
					No hay pares con este filtro.
				{/if}
			</p>
		</section>
	{/if}

	<div class="pares">
		{#each visibles as { par, v } (par.img_a + par.img_b)}
			<article class="par" class:err={v !== 'correcto'}>
				<div class="fotos">
					{#each [par.foto_a, par.foto_b] as foto, i (i)}
						{@const raw = i === 0 ? par.img_a : par.img_b}
						<figure>
							{#if foto}
								<img
									src={api.urlFoto(foto)}
									alt={nombreCorto(raw)}
									loading="lazy"
									draggable="false"
									style="object-position: {objectPosition(foto)}"
									title="Arrastra para recentrar · doble clic para restaurar"
									use:arrastrarRecorte={foto}
								/>
							{:else}
								<div class="sin-foto" title={raw}>sin<br />archivo</div>
							{/if}
							<figcaption>{nombreCorto(raw)}</figcaption>
						</figure>
					{/each}
				</div>

				<div class="datos">
					<div class="fila-score">
						<span class="score">{par.score?.toFixed(4) ?? '—'}</span>
						<span class="clase" class:m={par.same_person} class:nm={!par.same_person}>
							{par.same_person ? 'misma persona' : 'personas distintas'}
						</span>
					</div>

					<span class="badge {v}">{ETIQUETA[v]}</span>

					{#if par.notes}
						<p class="nota">{par.notes}</p>
					{/if}

					<p class="meta tenue">
						det {par.det_score_a?.toFixed(3) ?? '—'} / {par.det_score_b?.toFixed(3) ?? '—'}
						{#if par.face_selection_a?.startsWith('mayor') || par.face_selection_b?.startsWith('mayor')}
							· <span class="ojo">rostro elegido entre varios</span>
						{/if}
						{#if par.provider_a && par.provider_b && par.provider_a !== par.provider_b}
							· <span class="ojo">providers distintos</span>
						{/if}
					</p>
				</div>
			</article>
		{/each}
	</div>

	{#if d.descartados.length}
		<section class="tarjeta borde-mal">
			<h2>Pares sin score ({d.descartados.length})</h2>
			<p>
				La extracción falló en al menos una de las dos fotos. Estos pares
				<strong>no entran</strong> en ninguna tasa: un par sin score no es un score de 0. Arregla la
				foto y vuelve a correr el manifiesto para recuperar el caso de prueba.
			</p>
			<div class="pares">
				{#each d.descartados as x, i (i)}
					<article class="par descartado">
						<div class="fotos">
							{#each [x.foto_a, x.foto_b] as foto, j (j)}
								{@const raw = j === 0 ? x.img_a : x.img_b}
								{@const err = j === 0 ? x.error_a : x.error_b}
								<figure>
									{#if foto}
										<img
											src={api.urlFoto(foto)}
											alt={nombreCorto(raw)}
											loading="lazy"
											draggable="false"
											style="object-position: {objectPosition(foto)}"
											title="Arrastra para recentrar · doble clic para restaurar"
											use:arrastrarRecorte={foto}
										/>
									{:else}
										<div class="sin-foto" title={raw}>sin<br />archivo</div>
									{/if}
									<figcaption class:culpable={!!err}>{nombreCorto(raw)}</figcaption>
								</figure>
							{/each}
						</div>
						<div class="datos">
							<span class="badge falsa_aceptacion">{x.error_a || x.error_b || 'error'}</span>
							<p class="meta tenue">
								rostros detectados: {x.n_faces_a || '?'} / {x.n_faces_b || '?'}
							</p>
							{#if x.notes}<p class="nota">{x.notes}</p>{/if}
						</div>
					</article>
				{/each}
			</div>
		</section>
	{/if}
{/if}

<style>
	.encabezado {
		margin: 0.4rem 0 1rem;
	}

	.pegajoso {
		position: sticky;
		top: 0;
		z-index: 2;
		background: rgba(12, 28, 78, 0.92);
		backdrop-filter: blur(10px);
		-webkit-backdrop-filter: blur(10px);
	}

	.slider-fila {
		display: flex;
		align-items: center;
		gap: 0.85rem;
		flex-wrap: wrap;
		margin-bottom: 0.7rem;
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
		min-width: 5.2ch;
		text-align: right;
	}

	.filtros {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
	}

	.chip {
		font-size: 0.76rem;
		padding: 0.28rem 0.7rem;
		border-radius: 999px;
	}

	.chip.activo {
		background: rgba(37, 99, 235, 0.3);
		border-color: rgba(147, 197, 253, 0.6);
		color: #fff;
	}

	.chip.orden {
		margin-left: auto;
	}

	.pares {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
		gap: 0.9rem;
		margin-bottom: 1rem;
	}

	.par {
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
		background: rgba(255, 255, 255, 0.04);
		border: 1px solid var(--linea);
		border-radius: 13px;
		padding: 0.75rem;
	}

	/* El par mal clasificado se marca con borde Y con el badge de texto: nunca
	   sólo con color. */
	.par.err {
		border-color: rgba(248, 113, 113, 0.55);
		background: rgba(220, 38, 38, 0.09);
	}

	.par.descartado {
		border-style: dashed;
	}

	.fotos {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 0.5rem;
	}

	figure {
		margin: 0;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	img,
	.sin-foto {
		width: 100%;
		aspect-ratio: 1;
		object-fit: cover;
		border-radius: 9px;
		background: rgba(0, 0, 0, 0.35);
		border: 1px solid rgba(255, 255, 255, 0.14);
		display: block;
	}

	img {
		cursor: grab;
		touch-action: none;
		user-select: none;
		-webkit-user-drag: none;
	}

	img:global(.arrastrando-recorte) {
		cursor: grabbing;
	}

	.sin-foto {
		display: flex;
		align-items: center;
		justify-content: center;
		text-align: center;
		font-size: 0.7rem;
		color: var(--ink-3);
		border-style: dashed;
	}

	figcaption {
		font-size: 0.68rem;
		color: var(--ink-3);
		font-family: ui-monospace, Consolas, monospace;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	figcaption.culpable {
		color: var(--mal);
	}

	.datos {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	.fila-score {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.5rem;
	}

	.score {
		font-size: 1.3rem;
		font-weight: 650;
		font-family: ui-monospace, Consolas, monospace;
		color: var(--ink);
	}

	.clase {
		font-size: 0.72rem;
		padding: 0.15rem 0.45rem;
		border-radius: 5px;
		border: 1px solid;
	}

	/* El color de serie va en el chip junto al texto, no en el texto. */
	.clase.m {
		border-color: var(--serie-match);
		background: rgba(13, 148, 136, 0.28);
		color: rgba(255, 255, 255, 0.92);
	}

	.clase.nm {
		border-color: var(--serie-nonmatch);
		background: rgba(234, 88, 12, 0.26);
		color: rgba(255, 255, 255, 0.92);
	}

	.badge {
		align-self: flex-start;
		font-size: 0.72rem;
		padding: 0.2rem 0.55rem;
		border-radius: 999px;
		border: 1px solid;
		letter-spacing: 0.01em;
	}

	.badge.correcto {
		color: #bbf7d0;
		border-color: rgba(74, 222, 128, 0.45);
		background: rgba(22, 163, 74, 0.18);
	}

	.badge.falsa_aceptacion,
	.badge.falso_rechazo,
	.badge.sin_score {
		color: #fecaca;
		border-color: rgba(248, 113, 113, 0.5);
		background: rgba(220, 38, 38, 0.2);
	}

	.nota {
		font-size: 0.8rem;
		margin: 0;
		color: var(--ink-2);
	}

	.meta {
		margin: 0;
		font-family: ui-monospace, Consolas, monospace;
		font-size: 0.72rem;
	}

	.ojo {
		color: #fcd34d;
	}

	.borde-mal {
		border-color: rgba(248, 113, 113, 0.4);
		background: rgba(220, 38, 38, 0.08);
	}
</style>

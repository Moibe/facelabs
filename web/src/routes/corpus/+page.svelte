<script lang="ts">
	// Explorador del corpus: lo que las tiras de Run no pueden ser.
	//
	// Con ~35 mil fotos, "mostrarlas todas" no es viable ni util: serian otras
	// tantas peticiones y elementos en el DOM, y scrollear 35 mil caras no
	// responde ninguna pregunta. Lo que sirve es FILTRAR — por persona, por
	// con/sin rostro, por las que necesitaron margen — y cargar por tandas.
	import { page } from '$app/state';
	import { api, type FotoCorpus, type PersonaCorpus } from '$lib/api';

	const POR_TANDA = 100;

	// El estado inicial puede venir en la URL (Run enlaza aquí con ?estado=…),
	// para que el enlace lleve al filtro que ya estabas viendo.
	const estadoInicial = page.url.searchParams.get('estado');
	let estado = $state<'todas' | 'con_rostro' | 'sin_rostro'>(
		estadoInicial === 'con_rostro' || estadoInicial === 'sin_rostro' ? estadoInicial : 'todas'
	);
	let persona = $state<string>(page.url.searchParams.get('persona') ?? '');
	let soloConMargen = $state(false);

	let fotos = $state<FotoCorpus[]>([]);
	let total = $state(0);
	let cargando = $state(false);
	let error = $state<string | null>(null);
	let personas = $state<PersonaCorpus[]>([]);

	async function cargar(reiniciar: boolean) {
		cargando = true;
		error = null;
		try {
			const r = await api.fotosCorpus({
				estado,
				persona: persona || null,
				soloConMargen,
				offset: reiniciar ? 0 : fotos.length,
				limite: POR_TANDA
			});
			total = r.total;
			fotos = reiniciar ? r.fotos : [...fotos, ...r.fotos];
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			cargando = false;
		}
	}

	// Cualquier cambio de filtro reinicia la lista: seguir agregando tandas
	// sobre un filtro distinto mezclaría resultados de dos consultas.
	$effect(() => {
		estado;
		persona;
		soloConMargen;
		void cargar(true);
	});

	$effect(() => {
		api
			.personasCorpus()
			.then((r) => (personas = r.personas))
			.catch(() => (personas = []));
	});

	const faltan = $derived(Math.max(0, total - fotos.length));
</script>

<header class="encabezado">
	<h1>Corpus</h1>
	<p class="tenue">
		Todas las fotos ya procesadas, con filtros. Se cargan de {POR_TANDA} en {POR_TANDA}: pedir
		decenas de miles de golpe no es viable en un navegador, y tampoco responde nada — lo que sirve
		es acotar.
	</p>
</header>

<section class="tarjeta pegajoso">
	<div class="controles">
		<label>
			estado
			<select bind:value={estado}>
				<option value="todas">todas</option>
				<option value="con_rostro">con rostro</option>
				<option value="sin_rostro">sin rostro</option>
			</select>
		</label>
		<label>
			persona
			<select bind:value={persona}>
				<option value="">— todas —</option>
				{#each personas as p (p.persona)}
					<option value={p.persona}>{p.persona} ({p.total})</option>
				{/each}
			</select>
		</label>
		<label class="check">
			<input type="checkbox" bind:checked={soloConMargen} />
			sólo las que necesitaron margen
		</label>
	</div>
	<p class="tenue">
		{#if cargando && !fotos.length}
			Buscando…
		{:else}
			<strong>{total.toLocaleString()}</strong> foto(s) con este filtro · mostrando
			{fotos.length.toLocaleString()}
		{/if}
	</p>
	{#if error}
		<p class="tenue error-texto">{error}</p>
	{/if}
</section>

{#if fotos.length}
	<div class="rejilla">
		{#each fotos as f (f.ruta)}
			<figure>
				<img
					src={api.urlFotoCorpus(f.ruta)}
					alt={f.ruta}
					loading="lazy"
					title={f.error ?? f.ruta}
				/>
				<figcaption>
					{#if f.estado === 'con_rostro'}
						<span class="bien">det {f.det_score?.toFixed(2) ?? '—'}</span>
						{#if (f.margen_agregado ?? 0) > 0}
							<span class="rescatada">· +{Math.round((f.margen_agregado ?? 0) * 100)}%</span>
						{/if}
					{:else}
						<span class="mal">{f.error}</span>
					{/if}
					<br />{f.ruta.split('/')[0]}
				</figcaption>
			</figure>
		{/each}
	</div>

	<section class="tarjeta centrado">
		{#if faltan}
			<button type="button" onclick={() => cargar(false)} disabled={cargando}>
				{cargando ? 'cargando…' : `Ver ${Math.min(POR_TANDA, faltan)} más (faltan ${faltan.toLocaleString()})`}
			</button>
		{:else}
			<p class="tenue">Ya están todas las de este filtro.</p>
		{/if}
	</section>
{:else if !cargando}
	<section class="tarjeta">
		<p class="tenue">
			Ninguna foto con este filtro. Si el corpus todavía no se ha indexado, empieza por
			<a class="enlace" href="/run">Run</a>.
		</p>
	</section>
{/if}

<style>
	.encabezado {
		margin: 0.4rem 0 1rem;
		max-width: 74ch;
	}

	.pegajoso {
		position: sticky;
		top: 0;
		z-index: 2;
		background: rgba(12, 28, 78, 0.92);
		backdrop-filter: blur(10px);
		-webkit-backdrop-filter: blur(10px);
	}

	.controles {
		display: flex;
		align-items: center;
		gap: 0.9rem;
		flex-wrap: wrap;
		margin-bottom: 0.5rem;
	}

	.controles label {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		font-size: 0.82rem;
		color: var(--ink-3);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.controles label.check {
		text-transform: none;
		letter-spacing: normal;
		font-size: 0.86rem;
	}

	.controles select {
		font: inherit;
		font-size: 0.85rem;
		text-transform: none;
		letter-spacing: normal;
		color: var(--ink);
		background: rgba(10, 25, 70, 0.9);
		border: 1px solid rgba(255, 255, 255, 0.18);
		border-radius: 9px;
		padding: 0.4rem 0.6rem;
		max-width: 26ch;
	}

	/* El popup lo pinta el SO sobre superficie opaca: un fondo translucido
	   ahi se ve blanco. Mismo azul solido que el resto. */
	.controles select option {
		background: rgb(10, 25, 70);
		color: rgba(255, 255, 255, 0.92);
	}

	.rejilla {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
		gap: 0.6rem;
		margin-bottom: 1rem;
	}

	figure {
		margin: 0;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	img {
		width: 100%;
		aspect-ratio: 1;
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
	}

	.bien {
		color: var(--bien);
	}

	.mal {
		color: var(--mal);
	}

	/* Amarillo reservado para "salio, pero con ayuda": ni exito limpio ni
	   fallo. Siempre junto al "+N%", nunca solo el color. */
	.rescatada {
		color: #fcd34d;
	}

	.centrado {
		text-align: center;
	}

	.error-texto {
		color: var(--mal);
	}

	.enlace {
		color: #93c5fd;
	}
</style>

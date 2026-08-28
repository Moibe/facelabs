<script lang="ts">
	import {
		api,
		type BusquedaGuardada,
		type Cobertura,
		type CorpusResumen,
		type IndexarEstado,
		type Personas,
		type ResultadoBusqueda
	} from '$lib/api';

	// ---------------------------------------------- 1. subir fotos (dropbox)
	let personaConsulta = $state('');
	let subiendo = $state(false);
	let arrastrandoArchivo = $state(false);
	let errorSubida = $state<string | null>(null);

	// Las personas que ya existen en data/. La lista es la fuente de verdad
	// de las fotos que se muestran: antes solo se veian las subidas en ESTA
	// sesion, asi que elegir a alguien que ya tenia fotos se veia vacio.
	let personas = $state<Personas | null>(null);
	// El desplegable no puede ser solo lectura: aqui tambien se da de alta a
	// alguien nuevo. Esta bandera cambia el <select> por un campo de texto.
	let personaNueva = $state(false);

	const fotosDePersona = $derived.by(() => {
		const p = personas?.personas.find((x) => x.persona === personaConsulta.trim());
		return p?.fotos ?? [];
	});

	async function refrescarPersonas() {
		try {
			personas = await api.personas();
		} catch {
			personas = null;
		}
	}

	async function subirArchivos(archivos: FileList | File[]) {
		if (!personaConsulta.trim()) {
			errorSubida = 'Elige o escribe a quién buscas antes de subir fotos.';
			return;
		}
		subiendo = true;
		errorSubida = null;
		try {
			await api.subirFotos(personaConsulta.trim(), archivos);
			// Recargar en vez de acumular en memoria: asi la tira muestra lo
			// que de verdad hay en disco, recien subido o de antes.
			await refrescarPersonas();
			personaNueva = false; // ya existe: vuelve al desplegable
		} catch (e) {
			errorSubida = e instanceof Error ? e.message : String(e);
		} finally {
			subiendo = false;
		}
	}

	function onDrop(e: DragEvent) {
		e.preventDefault();
		arrastrandoArchivo = false;
		if (e.dataTransfer?.files?.length) void subirArchivos(e.dataTransfer.files);
	}

	function onFileInput(e: Event) {
		const files = (e.currentTarget as HTMLInputElement).files;
		if (files?.length) void subirArchivos(files);
		(e.currentTarget as HTMLInputElement).value = '';
	}

	// -------------------------------------------- 2. corpus: resumen/indexar
	let corpus = $state<CorpusResumen | null>(null);
	let limiteCarpetas = $state(10);
	let limitePorCarpeta = $state(5);
	// Default 'cpu' a proposito (distinto de Entorno): esta maquina no tiene
	// GPU, y una indexacion mal apuntada aqui puede tardar horas antes de que
	// alguien note el error, no segundos.
	let device = $state<'cuda' | 'cpu'>('cpu');
	let indexando = $state(false);
	let progresoIndexar = $state<IndexarEstado | null>(null);
	let errorIndexar = $state<string | null>(null);

	// Lo que sobrevive reinicios: cuanto del corpus ya se proceso y que
	// busquedas se han hecho. Sale de SQLite, no de la memoria del proceso.
	let cobertura = $state<Cobertura | null>(null);
	let historial = $state<BusquedaGuardada[]>([]);

	async function refrescarHistorial() {
		try {
			cobertura = await api.cobertura();
		} catch {
			cobertura = null;
		}
		try {
			historial = (await api.busquedas()).busquedas;
		} catch {
			historial = [];
		}
	}

	$effect(() => {
		api
			.corpusResumen()
			.then((r) => (corpus = r))
			.catch(() => (corpus = null));
		void refrescarHistorial();
		void refrescarPersonas();
	});

	const fecha = (iso: string) => {
		const d = new Date(iso);
		return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
	};

	const pctIndexar = $derived.by(() => {
		if (!progresoIndexar || !progresoIndexar.total) return 0;
		return Math.round((progresoIndexar.actual / progresoIndexar.total) * 100);
	});

	async function indexar() {
		indexando = true;
		deteniendo = false;
		errorIndexar = null;
		progresoIndexar = null;
		try {
			progresoIndexar = await api.indexarCorpus(
				limiteCarpetas || null,
				limitePorCarpeta || null,
				device
			);
			while (progresoIndexar.en_curso) {
				await new Promise((r) => setTimeout(r, 500));
				progresoIndexar = await api.indexarEstado();
			}
			if (progresoIndexar.error) errorIndexar = progresoIndexar.error;
			await refrescarHistorial();
		} catch (e) {
			errorIndexar = e instanceof Error ? e.message : String(e);
		} finally {
			indexando = false;
		}
	}

	// No se reinicia a false a proposito: el boton vive detras de
	// {#if indexando}, asi que en cuanto el while() de indexar() vea
	// en_curso=false, el boton desaparece solo — no hace falta destrabarlo.
	let deteniendo = $state(false);

	async function detenerIndexar() {
		deteniendo = true;
		try {
			await api.detenerIndexar();
		} catch (e) {
			errorIndexar = e instanceof Error ? e.message : String(e);
			deteniendo = false; // el pedido de parar fallo: se puede reintentar
		}
	}

	// ------------------------------------------------------------ 3. buscar
	let buscando = $state(false);
	let resultado = $state<ResultadoBusqueda | null>(null);
	let errorBuscar = $state<string | null>(null);
	// Punto de partida: el threshold que ya calibraste en Labs. Editable —
	// aqui no hay pares etiquetados que lo recalculen solo.
	let umbral = $state(0.181);

	async function buscar() {
		if (!personaConsulta.trim()) {
			errorBuscar = 'Ponle un nombre a la persona que buscas.';
			return;
		}
		buscando = true;
		errorBuscar = null;
		resultado = null;
		try {
			resultado = await api.buscarEnCorpus(
				personaConsulta.trim(),
				limiteCarpetas || null,
				limitePorCarpeta || null,
				device,
				umbral
			);
			await refrescarHistorial();
		} catch (e) {
			errorBuscar = e instanceof Error ? e.message : String(e);
		} finally {
			buscando = false;
		}
	}

	// Releer una búsqueda guardada no recalcula nada: sale tal cual de SQLite,
	// con el ranking completo que se mostró entonces.
	async function abrirBusqueda(b: BusquedaGuardada) {
		errorBuscar = null;
		try {
			resultado = await api.verBusqueda(b.id);
			personaConsulta = b.persona;
			if (b.umbral !== null) umbral = b.umbral;
		} catch (e) {
			errorBuscar = e instanceof Error ? e.message : String(e);
		}
	}

	async function borrarBusqueda(b: BusquedaGuardada) {
		try {
			await api.borrarBusqueda(b.id);
			if (resultado?.busqueda_id === b.id) resultado = null;
			await refrescarHistorial();
		} catch (e) {
			errorBuscar = e instanceof Error ? e.message : String(e);
		}
	}

	const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
</script>

<header class="encabezado">
	<h1>Run</h1>
	<p class="tenue">
		Busca si una persona de referencia aparece en un corpus externo grande y sin clasificar.
		Distinto de Labs: ahí se calibra un threshold sobre pares con etiqueta conocida; aquí no hay
		etiqueta — es una búsqueda 1:N, no una calibración.
	</p>
</header>

<section class="tarjeta">
	<h2>1 · Fotos de referencia</h2>
	<label class="campo">
		<span>Persona que buscas</span>
		{#if personaNueva || !personas?.personas.length}
			<input type="text" bind:value={personaConsulta} />
		{:else}
			<select
				bind:value={personaConsulta}
				onchange={(e) => {
					if ((e.currentTarget as HTMLSelectElement).value === '__nueva__') {
						personaConsulta = '';
						personaNueva = true;
					}
				}}
			>
				<option value="">— elige —</option>
				{#each personas.personas as p (p.persona)}
					<option value={p.persona}>{p.persona} · {p.n_fotos} foto(s)</option>
				{/each}
				<option value="__nueva__">+ nueva persona…</option>
			</select>
		{/if}
	</label>
	<p class="tenue">
		{#if personaNueva && personas?.personas.length}
			Escribe el nombre y sube sus fotos.
			<button type="button" class="enlace" onclick={() => (personaNueva = false)}>
				volver a la lista
			</button>
			·
		{/if}
		Se guardan en <code>data/{personaConsulta.trim() || '…'}/</code> — el mismo lugar que usa
		Labs.
	</p>

	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="dropzone"
		class:activa={arrastrandoArchivo}
		ondragover={(e) => {
			e.preventDefault();
			arrastrandoArchivo = true;
		}}
		ondragleave={() => (arrastrandoArchivo = false)}
		ondrop={onDrop}
	>
		{#if subiendo}
			<p>Subiendo…</p>
		{:else}
			<p>Arrastra fotos aquí, o</p>
			<label class="chip elegir">
				elegir archivos
				<input type="file" accept="image/*" multiple onchange={onFileInput} hidden />
			</label>
		{/if}
	</div>

	{#if errorSubida}
		<p class="tenue error-texto">{errorSubida}</p>
	{/if}

	{#if fotosDePersona.length}
		<div class="tira">
			{#each fotosDePersona as f (f.ruta)}
				<figure>
					<img src={api.urlFoto(f.ruta)} alt={f.nombre} loading="lazy" />
					<figcaption>{f.nombre}</figcaption>
				</figure>
			{/each}
		</div>
	{/if}
</section>

<section class="tarjeta">
	<h2>2 · Corpus externo</h2>
	{#if corpus === null}
		<p class="tenue">Cargando…</p>
	{:else if !corpus.existe}
		<p class="tenue">
			No se encontró <code>{corpus.corpus_dir}</code>. Apúntalo con la variable de entorno
			<code>FACID_CORPUS</code> antes de arrancar la API.
		</p>
	{:else}
		<p class="tenue">
			<code>{corpus.corpus_dir}</code> · {corpus.n_carpetas} carpeta(s) disponibles
		</p>

		{#if cobertura}
			<div class="cobertura">
				<div class="cifra">
					<span class="n">{cobertura.procesadas.toLocaleString()}</span>
					<span class="et">fotos ya procesadas</span>
				</div>
				<div class="cifra">
					<span class="n">{cobertura.con_rostro.toLocaleString()}</span>
					<span class="et">con rostro usable</span>
				</div>
				<div class="cifra">
					<span class="n">{cobertura.sin_rostro.toLocaleString()}</span>
					<span class="et">sin rostro detectable</span>
				</div>
			</div>
			<p class="tenue">
				{#if cobertura.total_ultimo_conteo}
					De ~{cobertura.total_ultimo_conteo.toLocaleString()} vistas en el último recorrido completo.
					El corpus crece solo, así que es una referencia, no un total al segundo.
				{/if}
				{#if cobertura.ultima_corrida?.terminada_en}
					Última indexación: {fecha(cobertura.ultima_corrida.terminada_en)}
					{#if cobertura.ultima_corrida.detenido}(detenida antes de terminar){/if}
					{#if cobertura.ultima_corrida.error}— falló: {cobertura.ultima_corrida.error}{/if}.
				{/if}
			</p>
		{/if}

		<div class="controles">
			<label>
				carpetas a indexar
				<input type="number" min="0" bind:value={limiteCarpetas} />
			</label>
			<label>
				fotos por carpeta
				<input type="number" min="0" bind:value={limitePorCarpeta} />
			</label>
			<label>
				device
				<select bind:value={device}>
					<option value="cpu">cpu</option>
					<option value="cuda">cuda</option>
				</select>
			</label>
			<button type="button" onclick={indexar} disabled={indexando}>
				{indexando ? 'indexando…' : 'Indexar'}
			</button>
			{#if indexando}
				<button type="button" class="detener" onclick={detenerIndexar} disabled={deteniendo}>
					{deteniendo ? 'deteniendo…' : 'Detener'}
				</button>
			{/if}
		</div>
		<p class="tenue">
			0 = sin límite — el corpus completo puede tardar horas en CPU. Lo ya indexado no se vuelve
			a extraer (caché por contenido, no por ruta).
		</p>
		{#if indexando && progresoIndexar}
			<div class="progreso">
				<div class="barra-progreso">
					<div class="barra-progreso-relleno" style="width: {pctIndexar}%"></div>
				</div>
				<p class="tenue progreso-texto">
					{#if progresoIndexar.etapa === 'cargando_modelo'}
						Cargando el modelo…
					{:else if progresoIndexar.etapa === 'indexando'}
						Indexando {progresoIndexar.actual}/{progresoIndexar.total} ·
						<strong>{progresoIndexar.en_cache}</strong> ya estaban ·
						<strong>{progresoIndexar.nuevas}</strong> nuevas —
						<code>{progresoIndexar.archivo}</code>
					{:else if progresoIndexar.etapa === 'pausado'}
						Pausado — hay una búsqueda en curso, reanuda sola al terminar (
						{progresoIndexar.actual}/{progresoIndexar.total})
					{/if}
				</p>
			</div>
		{/if}
		{#if progresoIndexar?.resultado}
			<p class="tenue resultado-accion">
				{#if progresoIndexar.resultado.detenido}
					<strong>Detenido antes de terminar.</strong>
				{/if}
				{progresoIndexar.resultado.indexadas_ok}/{progresoIndexar.resultado.fotos_vistas} fotos
				indexadas de {progresoIndexar.resultado.carpetas_vistas} carpeta(s)
				{#if progresoIndexar.resultado.fallidas}
					· {progresoIndexar.resultado.fallidas} fallida(s)
				{/if}
				· <strong>{progresoIndexar.resultado.nuevas}</strong> procesadas esta vez,
				{progresoIndexar.resultado.en_cache} ya estaban.
				{#if progresoIndexar.resultado.detenido}
					Lo ya guardado no se pierde — dale "Indexar" de nuevo para seguir donde quedó.
				{/if}
			</p>
		{/if}
		{#if errorIndexar}
			<p class="tenue error-texto">{errorIndexar}</p>
		{/if}
	{/if}
</section>

<section class="tarjeta">
	<h2>3 · Buscar</h2>
	<div class="controles">
		<label>
			umbral
			<input type="number" step="0.01" min="-1" max="1" bind:value={umbral} />
		</label>
		<button type="button" onclick={buscar} disabled={buscando || !personaConsulta.trim()}>
			{buscando ? 'buscando…' : 'Buscar coincidencias'}
		</button>
	</div>
	<p class="tenue">
		Busca sobre lo que YA esté en <code>data/{personaConsulta.trim() || '…'}/</code> — no hace
		falta que las hayas subido en esta sesión; si la persona ya tenía fotos de antes, también
		cuentan.
	</p>
	{#if errorBuscar}
		<p class="tenue error-texto">{errorBuscar}</p>
	{/if}

	{#if historial.length}
		<div class="historial">
			<h3>Búsquedas anteriores</h3>
			<p class="tenue">
				Guardadas en la base — sobreviven reinicios y recargas. Abrirlas no recalcula nada.
			</p>
			<ul class="lista-historial">
				{#each historial as b (b.id)}
					<li class:activa={resultado?.busqueda_id === b.id}>
						<button type="button" class="enlace" onclick={() => abrirBusqueda(b)}>
							{b.persona}
						</button>
						<span class="tenue">
							{fecha(b.creada_en)} · contra {b.n_indexado ?? '?'} foto(s)
							{#if b.umbral !== null}· umbral {b.umbral}{/if}
						</span>
						<button
							type="button"
							class="borrar"
							title="Borrar del historial"
							onclick={() => borrarBusqueda(b)}>×</button
						>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if resultado}
		<p class="tenue resultado-accion">
			Comparado contra {resultado.n_indexado} foto(s) indexada(s) de
			{resultado.n_carpetas_indexadas} carpeta(s). Resaltadas las que igualan o superan el umbral.
			{#if resultado.creada_en}
				· <strong>Del historial</strong>, hecha el {fecha(resultado.creada_en)}.
			{/if}
		</p>
		{#each resultado.resultados as r (r.consulta)}
			<div class="consulta">
				<h3>{r.consulta}</h3>
				{#if r.error}
					<p class="tenue error-texto">{r.error}</p>
				{:else if !r.coincidencias.length}
					<p class="tenue">Sin coincidencias en lo indexado.</p>
				{:else}
					<div class="tira">
						{#each r.coincidencias as c (c.ruta)}
							<figure class:alerta={c.score >= umbral}>
								<img src={api.urlFotoCorpus(c.ruta)} alt={c.persona} loading="lazy" />
								<span class="score">{pct(c.score)}</span>
								<figcaption>{c.persona}</figcaption>
							</figure>
						{/each}
					</div>
				{/if}
			</div>
		{/each}
	{/if}
</section>

<style>
	.encabezado {
		margin: 0.4rem 0 1rem;
		max-width: 70ch;
	}

	h3 {
		font-size: 0.9rem;
		margin: 0.9rem 0 0.4rem;
		font-weight: 600;
	}

	.campo {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.82rem;
		color: var(--ink-3);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		margin-bottom: 0.5rem;
		max-width: 320px;
	}

	.campo input[type='text'],
	.campo select {
		font: inherit;
		font-size: 0.95rem;
		text-transform: none;
		letter-spacing: normal;
		color: var(--ink);
		background: rgba(10, 25, 70, 0.9);
		border: 1px solid rgba(255, 255, 255, 0.18);
		border-radius: 9px;
		padding: 0.5rem 0.7rem;
	}

	/* El popup del <select> lo pinta el SO sobre superficie opaca: un fondo
	   translucido ahi se ve blanco. Mismo azul solido que el resto. */
	.campo select option {
		background: rgb(10, 25, 70);
		color: rgba(255, 255, 255, 0.92);
	}

	.dropzone {
		margin-top: 0.6rem;
		padding: 1.4rem;
		border-radius: 12px;
		border: 1.5px dashed rgba(255, 255, 255, 0.25);
		text-align: center;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.6rem;
		transition:
			border-color 0.15s ease,
			background 0.15s ease;
	}

	.dropzone.activa {
		border-color: rgba(147, 197, 253, 0.8);
		background: rgba(147, 197, 253, 0.08);
	}

	.chip.elegir {
		display: inline-block;
		font: inherit;
		font-size: 0.85rem;
		color: var(--ink);
		background: rgba(255, 255, 255, 0.07);
		border: 1px solid rgba(255, 255, 255, 0.18);
		border-radius: 999px;
		padding: 0.4rem 0.9rem;
		cursor: pointer;
		transition:
			background 0.16s ease,
			border-color 0.16s ease;
	}

	.chip.elegir:hover {
		background: rgba(255, 255, 255, 0.13);
		border-color: rgba(255, 255, 255, 0.3);
	}

	.controles {
		display: flex;
		align-items: center;
		gap: 0.8rem;
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

	.controles input[type='number'] {
		font: inherit;
		color: var(--ink);
		background: rgba(10, 25, 70, 0.9);
		border: 1px solid rgba(255, 255, 255, 0.18);
		border-radius: 9px;
		padding: 0.4rem 0.5rem;
		width: 6ch;
	}

	.cobertura {
		display: flex;
		flex-wrap: wrap;
		gap: 1.6rem;
		margin: 0.6rem 0 0.5rem;
	}

	.cifra {
		display: flex;
		flex-direction: column;
	}

	.cifra .n {
		font-size: 1.5rem;
		font-weight: 650;
		font-family: ui-monospace, Consolas, monospace;
		color: var(--ink);
		line-height: 1.1;
	}

	.cifra .et {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--ink-3);
	}

	.historial {
		margin: 0.8rem 0;
		padding-top: 0.7rem;
		border-top: 1px solid var(--linea);
	}

	.lista-historial {
		list-style: none;
		margin: 0.4rem 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.lista-historial li {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		padding: 0.3rem 0.5rem;
		border-radius: 8px;
		border: 1px solid transparent;
		font-size: 0.82rem;
	}

	.lista-historial li:hover {
		background: rgba(255, 255, 255, 0.05);
	}

	.lista-historial li.activa {
		background: rgba(37, 99, 235, 0.18);
		border-color: rgba(147, 197, 253, 0.4);
	}

	.borrar {
		margin-left: auto;
		font: inherit;
		font-size: 1rem;
		line-height: 1;
		color: var(--ink-3);
		background: none;
		border: none;
		padding: 0 0.3rem;
		cursor: pointer;
	}

	.borrar:hover {
		color: var(--mal);
	}

	.detener {
		color: #fecaca;
		background: rgba(220, 38, 38, 0.18);
		border-color: rgba(248, 113, 113, 0.45);
	}

	.detener:hover:not(:disabled) {
		background: rgba(220, 38, 38, 0.3);
		border-color: rgba(248, 113, 113, 0.7);
	}

	.tira {
		display: flex;
		gap: 0.6rem;
		overflow-x: auto;
		padding-bottom: 0.4rem;
		margin-top: 0.6rem;
	}

	figure {
		position: relative;
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

	figure.alerta img {
		border-color: var(--mal);
		box-shadow: 0 0 0 2px rgba(248, 113, 113, 0.35);
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

	.score {
		position: absolute;
		bottom: 22px;
		right: 4px;
		font-size: 0.66rem;
		font-family: ui-monospace, Consolas, monospace;
		padding: 0.1rem 0.35rem;
		border-radius: 999px;
		background: rgba(10, 25, 70, 0.85);
		border: 1px solid rgba(255, 255, 255, 0.25);
		color: var(--ink);
	}

	.consulta {
		border-top: 1px solid var(--linea);
		padding-top: 0.6rem;
		margin-top: 0.6rem;
	}

	.consulta:first-of-type {
		border-top: none;
		margin-top: 0;
	}

	.progreso {
		margin: 0.6rem 0 0;
	}

	.barra-progreso {
		height: 8px;
		border-radius: 999px;
		background: rgba(255, 255, 255, 0.1);
		border: 1px solid rgba(255, 255, 255, 0.14);
		overflow: hidden;
	}

	.barra-progreso-relleno {
		height: 100%;
		border-radius: 999px;
		background: linear-gradient(90deg, #2563eb, #93c5fd);
		transition: width 0.25s ease-out;
	}

	.progreso-texto {
		margin: 0.4rem 0 0;
	}

	.resultado-accion {
		margin: 0.5rem 0 0;
	}

	.error-texto {
		margin: 0.5rem 0 0;
		color: var(--mal);
	}
</style>

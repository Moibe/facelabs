<script lang="ts">
	import {
		api,
		type BusquedaGuardada,
		type Cobertura,
		type CorpusResumen,
		type FotoCorpus,
		type IndexarEstado,
		type Personas,
		type ResultadoBusqueda
	} from '$lib/api';

	// ---------------------------------------------- 2. subir fotos (dropbox)
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

	// -------------------------------------------- 1. corpus: resumen/indexar
	let corpus = $state<CorpusResumen | null>(null);
	// 0 = sin limite. Empezaron acotados cuando reindexar era caro; con la
	// cache de fallos y la stat cache, recorrer todo lo ya conocido cuesta
	// segundos, y un limite chico solo sirve para dejar fuera de la busqueda
	// partes del corpus que ya estaban indexadas.
	let limiteCarpetas = $state(0);
	let limitePorCarpeta = $state(0);
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

	// Pastel de cobertura, DOS anillos para que quepan las 4 cifras de arriba
	// sin inventar categorias que no suman entre si:
	//   - anillo exterior: con rostro / sin rostro / sin procesar  (= total)
	//   - anillo interior: procesadas / sin procesar               (= total)
	// procesadas = con_rostro + sin_rostro exactamente, asi que el limite
	// entre "procesada" y "sin procesar" cae en el MISMO angulo en los dos
	// anillos — por eso ambos se calculan sobre el mismo total (~102,032) y
	// no cada uno sobre su propia suma, si no, no alinearian.
	type Rebanada = { id: string; etiqueta: string; valor: number; color: string };
	const anilloExterior = $derived.by((): Rebanada[] => {
		if (!cobertura || !cobertura.total_ultimo_conteo) return [];
		const pendientes = Math.max(0, cobertura.total_ultimo_conteo - cobertura.procesadas);
		return [
			{ id: 'ext:con_rostro', etiqueta: 'con rostro usable', valor: cobertura.con_rostro, color: 'var(--bien)' },
			{
				id: 'ext:sin_rostro',
				etiqueta: 'sin rostro detectable',
				valor: cobertura.sin_rostro,
				color: 'var(--mal)'
			},
			{
				id: 'ext:pendientes',
				etiqueta: 'sin procesar aún',
				valor: pendientes,
				color: 'rgba(255, 255, 255, 0.16)'
			}
		].filter((r) => r.valor > 0);
	});
	const anilloInterior = $derived.by((): Rebanada[] => {
		if (!cobertura || !cobertura.total_ultimo_conteo) return [];
		const pendientes = Math.max(0, cobertura.total_ultimo_conteo - cobertura.procesadas);
		return [
			{
				id: 'int:procesadas',
				etiqueta: 'fotos ya procesadas',
				valor: cobertura.procesadas,
				color: 'rgba(255, 255, 255, 0.6)'
			},
			{
				id: 'int:pendientes',
				etiqueta: 'sin procesar aún',
				valor: pendientes,
				color: 'rgba(255, 255, 255, 0.16)'
			}
		].filter((r) => r.valor > 0);
	});

	// stroke-dasharray por segmento en vez de arcos rellenos: para una dona
	// (a diferencia de un pastel solido) es la forma estandar de dibujar un
	// anillo, y de paso el caso "una sola rebanada al 100%" no degenera
	// (dasharray "C 0" ya pinta el circulo completo, sin caso especial).
	function segmentosAnillo(piezas: Rebanada[], r: number) {
		const total = piezas.reduce((s, p) => s + p.valor, 0);
		if (!total) return [];
		const c = 2 * Math.PI * r;
		let acumulado = 0;
		return piezas.map((p) => {
			const inicioFrac = acumulado / total;
			const largo = (p.valor / total) * c;
			const offset = -(inicioFrac * c);
			acumulado += p.valor;
			return {
				...p,
				r,
				pct: p.valor / total,
				inicioFrac,
				dasharray: `${largo} ${c - largo}`,
				dashoffset: offset
			};
		});
	}

	const GROSOR_ANILLO = 9;
	const CX = 32;
	const CY = 32;
	const segmentosExterior = $derived(segmentosAnillo(anilloExterior, 26));
	const segmentosInterior = $derived(segmentosAnillo(anilloInterior, 15));

	// ── Callout (línea + etiqueta) del segmento en hover, igual que en el
	// pastel de fortunecity: un punto en el borde del anillo, un codo un poco
	// más afuera y un tramo horizontal hacia el texto. Ahí SIEMPRE se ancla
	// a la izquierda (texto con text-anchor="end"), a diferencia de
	// fortunecity que decide el lado segun el angulo: este pastel vive
	// pegado al borde derecho de la tarjeta (margin-left:auto), asi que a la
	// derecha no hay espacio para el texto caiga donde caiga la rebanada.
	let hoveredPastel = $state<string | null>(null);

	type Callout = {
		x1: number;
		y1: number;
		x2: number;
		y2: number;
		x3: number;
		y3: number;
		etiqueta: string;
		valor: number;
		pct: number;
	};
	function calloutDeSegmento(s: { r: number; inicioFrac: number; pct: number; etiqueta: string; valor: number }): Callout {
		const anguloGrados = (s.inicioFrac + s.pct / 2) * 360;
		const rad = ((anguloGrados - 90) * Math.PI) / 180;
		const cosA = Math.cos(rad);
		const sinA = Math.sin(rad);
		const r1 = s.r + GROSOR_ANILLO / 2; // borde visible de ese anillo
		const r2 = r1 + 6; // codo, un poco más afuera
		const kink = 22; // tramo horizontal, siempre hacia la izquierda
		return {
			x1: CX + r1 * cosA,
			y1: CY + r1 * sinA,
			x2: CX + r2 * cosA,
			y2: CY + r2 * sinA,
			x3: CX + r2 * cosA - kink,
			y3: CY + r2 * sinA,
			etiqueta: s.etiqueta,
			valor: s.valor,
			pct: s.pct
		};
	}
	const calloutPastel = $derived.by((): Callout | null => {
		if (!hoveredPastel) return null;
		const s = segmentosExterior.find((x) => x.id === hoveredPastel) ?? segmentosInterior.find((x) => x.id === hoveredPastel);
		return s ? calloutDeSegmento(s) : null;
	});

	// Poder VER las fotos detrás de cada cifra: el conteo solo no dice si lo
	// que falla son recortes malos, ni cuáles caras hubo que rescatar con
	// margen. Solo una tira abierta a la vez — dos listas de 60 miniaturas
	// juntas empujarian los controles fuera de la pantalla.
	let tira = $state<'sin_rostro' | 'con_rostro' | null>(null);
	let fotosTira = $state<FotoCorpus[]>([]);
	let cargandoTira = $state<'sin_rostro' | 'con_rostro' | null>(null);

	async function verTira(cual: 'sin_rostro' | 'con_rostro') {
		if (tira === cual) {
			tira = null; // segundo clic: cerrar
			return;
		}
		cargandoTira = cual;
		try {
			fotosTira = (await api.fotosCorpus({ estado: cual, limite: 60 })).fotos;
			tira = cual;
		} catch (e) {
			errorIndexar = e instanceof Error ? e.message : String(e);
		} finally {
			cargandoTira = null;
		}
	}

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
		// Si ya hay una indexacion corriendo (la arranco otra pestaña, o esta
		// misma antes de recargar), engancharse a ella en vez de mostrar la
		// pagina como si no pasara nada.
		api
			.indexarEstado()
			.then((e) => {
				if (e.en_curso) {
					progresoIndexar = e;
					void seguirIndexado();
				}
			})
			.catch(() => {});
	});

	const fecha = (iso: string) => {
		const d = new Date(iso);
		return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
	};

	const pctIndexar = $derived.by(() => {
		if (!progresoIndexar || !progresoIndexar.total) return 0;
		return Math.round((progresoIndexar.actual / progresoIndexar.total) * 100);
	});

	// Sondea hasta que el trabajo termine. Aparte de indexar() porque tambien
	// lo usa el enganche al abrir la pagina: la indexacion vive en el
	// servidor, no en esta pestaña, y sin esto recargar (o abrir en otra
	// ventana) dejaba la barra invisible aunque el trabajo siguiera corriendo.
	async function seguirIndexado() {
		indexando = true;
		try {
			while (progresoIndexar?.en_curso) {
				await new Promise((r) => setTimeout(r, 500));
				progresoIndexar = await api.indexarEstado();
			}
			if (progresoIndexar?.error) errorIndexar = progresoIndexar.error;
			await refrescarHistorial();
		} catch (e) {
			errorIndexar = e instanceof Error ? e.message : String(e);
		} finally {
			indexando = false;
		}
	}

	async function indexar() {
		deteniendo = false;
		errorIndexar = null;
		progresoIndexar = null;
		try {
			progresoIndexar = await api.indexarCorpus(
				limiteCarpetas || null,
				limitePorCarpeta || null,
				device
			);
		} catch (e) {
			errorIndexar = e instanceof Error ? e.message : String(e);
			return;
		}
		await seguirIndexado();
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
	<h2>1 · Corpus externo</h2>
	<p class="tenue">
		Va primero porque es lo lento — puede tardar horas — y porque no depende de nada: indexar
		sólo lee el corpus, no le importa a quién buscas. Arráncalo y prepara las fotos del paso 2
		mientras corre.
	</p>
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
				<button
					type="button"
					class="cifra cifra-clic"
					class:abierta={tira === 'con_rostro'}
					onclick={() => verTira('con_rostro')}
					disabled={!!cargandoTira || !cobertura.con_rostro}
					title={cobertura.con_rostro ? 'Ver cuáles' : 'No hay ninguna'}
				>
					<span class="n">{cobertura.con_rostro.toLocaleString()}</span>
					<span class="et">
						con rostro usable
						{#if cobertura.con_rostro}
							· {cargandoTira === 'con_rostro'
								? 'abriendo…'
								: tira === 'con_rostro'
									? 'ocultar'
									: 'ver cuáles'}
						{/if}
					</span>
				</button>
				<button
					type="button"
					class="cifra cifra-clic"
					class:abierta={tira === 'sin_rostro'}
					onclick={() => verTira('sin_rostro')}
					disabled={!!cargandoTira || !cobertura.sin_rostro}
					title={cobertura.sin_rostro ? 'Ver cuáles' : 'No hay ninguna'}
				>
					<span class="n">{cobertura.sin_rostro.toLocaleString()}</span>
					<span class="et">
						sin rostro detectable
						{#if cobertura.sin_rostro}
							· {cargandoTira === 'sin_rostro'
								? 'abriendo…'
								: tira === 'sin_rostro'
									? 'ocultar'
									: 'ver cuáles'}
						{/if}
					</span>
				</button>
				{#if cobertura.total_ultimo_conteo}
					<div class="cifra">
						<span class="n">~{cobertura.total_ultimo_conteo.toLocaleString()}</span>
						<span class="et">en el corpus (último recorrido)</span>
					</div>
				{/if}
				{#if segmentosExterior.length}
					<svg
						class="pastel"
						viewBox="0 0 64 64"
						role="img"
						aria-label="Proporción del corpus: procesadas vs pendientes, y de las procesadas, con rostro vs sin rostro"
					>
						<g transform="rotate(-90 32 32)">
							{#each segmentosInterior as s (s.id)}
								<!-- svelte-ignore a11y_no_static_element_interactions -->
								<circle
									cx="32"
									cy="32"
									r="15"
									fill="none"
									stroke={s.color}
									stroke-width={hoveredPastel === s.id ? GROSOR_ANILLO + 2 : GROSOR_ANILLO}
									stroke-dasharray={s.dasharray}
									stroke-dashoffset={s.dashoffset}
									class="seg-pastel"
									class:dim={hoveredPastel !== null && hoveredPastel !== s.id}
									onmouseenter={() => (hoveredPastel = s.id)}
									onmouseleave={() => (hoveredPastel = null)}
									><title>{s.etiqueta}: {s.valor.toLocaleString()} ({pct(s.pct)})</title></circle
								>
							{/each}
							{#each segmentosExterior as s (s.id)}
								<!-- svelte-ignore a11y_no_static_element_interactions -->
								<circle
									cx="32"
									cy="32"
									r="26"
									fill="none"
									stroke={s.color}
									stroke-width={hoveredPastel === s.id ? GROSOR_ANILLO + 2 : GROSOR_ANILLO}
									stroke-dasharray={s.dasharray}
									stroke-dashoffset={s.dashoffset}
									class="seg-pastel"
									class:dim={hoveredPastel !== null && hoveredPastel !== s.id}
									onmouseenter={() => (hoveredPastel = s.id)}
									onmouseleave={() => (hoveredPastel = null)}
									><title>{s.etiqueta}: {s.valor.toLocaleString()} ({pct(s.pct)})</title></circle
								>
							{/each}
						</g>
						{#if calloutPastel}
							{@const c = calloutPastel}
							<g class="callout-pastel">
								<circle cx={c.x1} cy={c.y1} r="1.6" fill="#fff" />
								<polyline points="{c.x1},{c.y1} {c.x2},{c.y2} {c.x3},{c.y3}" class="callout-linea" />
								<text x={c.x3 - 2} y={c.y3 - 3} text-anchor="end" class="callout-nombre">
									{c.etiqueta}
								</text>
								<text x={c.x3 - 2} y={c.y3 + 6} text-anchor="end" class="callout-sub">
									{c.valor.toLocaleString()} · {pct(c.pct)}
								</text>
							</g>
						{/if}
					</svg>
				{/if}
			</div>

			{#if tira}
				{#if !fotosTira.length}
					<p class="tenue">No hay ninguna registrada.</p>
				{:else}
					{@const conMargen = fotosTira.filter((f) => (f.margen_agregado ?? 0) > 0).length}
					<p class="tenue">
						Las {fotosTira.length} más recientes de
						{(tira === 'con_rostro'
							? cobertura.con_rostro
							: cobertura.sin_rostro
						).toLocaleString()}.
						{#if tira === 'con_rostro' && conMargen}
							<strong>{conMargen}</strong> sólo aparecieron tras rellenarles el borde (marcadas):
							el recorte original venía demasiado pegado a la cara.
						{:else if tira === 'sin_rostro'}
							Sirven para juzgar si lo que queda son recortes demasiado pegados o fotos que de
							verdad no traen cara.
						{/if}
						<a class="enlace" href="/corpus?estado={tira}">verlas todas con filtros →</a>
					</p>
					<div class="tira">
						{#each fotosTira as f (f.ruta)}
							<figure>
								<img
									src={api.urlFotoCorpus(f.ruta)}
									alt={f.ruta}
									loading="lazy"
									title={f.error ?? undefined}
								/>
								<figcaption>
									{#if f.estado === 'con_rostro'}
										<span class="bien">det {f.det_score?.toFixed(2) ?? '—'}</span>
										{#if (f.margen_agregado ?? 0) > 0}
											<span class="rescatada"
												>· +{Math.round((f.margen_agregado ?? 0) * 100)}%</span
											>
										{/if}
									{:else}
										<span class="mal"
											>{f.error}{#if f.n_faces_detected}
												· {f.n_faces_detected} rostros{/if}</span
										>
									{/if}
									<br />{f.ruta.split('/')[0]}
								</figcaption>
							</figure>
						{/each}
					</div>
				{/if}
			{/if}
			<p class="tenue">
				{#if cobertura.total_ultimo_conteo}
					El corpus crece solo, así que esa cifra es del último recorrido completo, no un total
					al segundo.
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
					{:else if progresoIndexar.etapa === 'explorando'}
						Recorriendo el corpus para ver cuántas fotos hay…
					{:else if progresoIndexar.etapa === 'indexando'}
						Indexando {progresoIndexar.actual}/{progresoIndexar.total} ·
						<strong>{progresoIndexar.en_cache}</strong> ya estaban ·
						<strong>{progresoIndexar.nuevas}</strong> nuevas
						{#if progresoIndexar.nuevas}
							(<span class="bien">{progresoIndexar.nuevas_ok} con rostro</span>,
							<span class="mal">{progresoIndexar.nuevas_fallidas} sin</span>)
						{/if}
						— <code>{progresoIndexar.archivo}</code>
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
				· <strong>{progresoIndexar.resultado.nuevas}</strong> procesadas esta vez
				{#if progresoIndexar.resultado.nuevas}
					(<span class="bien">{progresoIndexar.resultado.nuevas_ok} con rostro</span>,
					<span class="mal">{progresoIndexar.resultado.nuevas_fallidas} sin</span>)
				{/if}, {progresoIndexar.resultado.en_cache} ya estaban.
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
	<h2>2 · Fotos de referencia</h2>
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

		{#if resultado.consolidado?.length}
			<div class="consulta">
				<h3>Consolidado por persona</h3>
				<p class="tenue">
					Una fila por persona del corpus, juntando tus {resultado.resultados.length} foto(s) de
					referencia. Ordenado por <strong>promedio</strong>, no por el mejor score: alguien que
					coincide parejo con todas tus fotos es mucho más creíble que alguien con un solo acierto
					alto y el resto en cero.
				</p>
				<div class="tabla-scroll">
					<table>
						<thead>
							<tr>
								<th></th>
								<th>Persona</th>
								<th>Promedio</th>
								<th>Mejor</th>
								<th>Sobre el umbral</th>
								<th>Fotos suyas</th>
							</tr>
						</thead>
						<tbody>
							{#each resultado.consolidado as c (c.persona)}
								{@const sobre = Object.values(c.por_consulta).filter((s) => s >= umbral).length}
								<tr class:alerta-fila={c.promedio >= umbral}>
									<td>
										<img
											class="mini"
											src={api.urlFotoCorpus(c.mejor_ruta)}
											alt={c.persona}
											loading="lazy"
										/>
									</td>
									<td><code>{c.persona}</code></td>
									<td class="num"><strong>{pct(c.promedio)}</strong></td>
									<td class="num tenue">{pct(c.mejor)}</td>
									<td class="num" class:bien={sobre === c.n_consultas && sobre > 0}>
										{sobre}/{c.n_consultas}
									</td>
									<td class="num tenue">{c.n_fotos_corpus}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<p class="tenue">
					"Sobre el umbral" cuenta en cuántas de tus fotos de referencia esa persona pasa el corte
					actual ({umbral}). {resultado.resultados.length}/{resultado.resultados.length} es la señal
					más fuerte que da este set.
				</p>
			</div>
		{/if}

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

	/* margin-left: auto empuja el pastel hasta el borde derecho de la
	   tarjeta, aunque las cifras de la izquierda no llenen todo el ancho.
	   overflow: visible porque el callout de hover se sale del viewBox de
	   64x64 (el texto vive afuera del anillo, no adentro). */
	.pastel {
		width: 72px;
		height: 72px;
		flex: none;
		margin-left: auto;
		margin-right: 1.6rem;
		align-self: center;
		overflow: visible;
	}

	.seg-pastel {
		cursor: pointer;
		transition:
			stroke-width 0.15s ease,
			opacity 0.15s ease;
	}

	.seg-pastel.dim {
		opacity: 0.4;
	}

	/* Mismo patrón que el callout de fortunecity (línea delgada del color del
	   segmento hacia una etiqueta afuera), pero siempre ancla a la
	   izquierda: este pastel vive pegado al borde derecho de la tarjeta, así
	   que a la derecha no hay lugar para el texto caiga donde caiga la
	   rebanada. */
	.callout-pastel {
		pointer-events: none;
	}

	.callout-linea {
		fill: none;
		stroke: rgba(255, 255, 255, 0.7);
		stroke-width: 1;
	}

	.callout-nombre {
		fill: var(--ink);
		font-size: 6.5px;
		font-weight: 600;
	}

	.callout-sub {
		fill: var(--ink-3);
		font-size: 6px;
		font-variant-numeric: tabular-nums;
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

	/* La cifra clickeable se ve como las otras, pero con afordancia: el propio
	   rotulo dice "ver cuales" en vez de dejarlo al color o al cursor. */
	.cifra-clic {
		font: inherit;
		text-align: left;
		background: none;
		border: 1px solid transparent;
		border-radius: 9px;
		padding: 0.15rem 0.4rem;
		margin: -0.15rem -0.4rem;
		cursor: pointer;
	}

	.cifra-clic:hover:not(:disabled),
	.cifra-clic.abierta {
		background: rgba(255, 255, 255, 0.06);
		border-color: rgba(255, 255, 255, 0.18);
	}

	.cifra-clic:disabled {
		cursor: default;
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

	/* Colores de estado, no de serie: verde/rojo aqui significan "salio" y
	   "no salio", y siempre van acompañados del texto que lo dice. */
	.bien {
		color: var(--bien);
	}

	.mal {
		color: var(--mal);
	}

	/* Amarillo reservado para "esto salio, pero con ayuda" — ni exito limpio
	   ni fallo. Va siempre con el texto "+N% margen", nunca solo el color. */
	.rescatada {
		color: #fcd34d;
	}

	/* Sin esto el link hereda el azul default del browser, casi invisible
	   sobre el fondo azul oscuro de esta franja. Un amarillo mas dorado que
	   el de .rescatada para no confundirse con ese otro significado. */
	.enlace {
		color: #fbbf24;
	}

	.num {
		font-family: ui-monospace, Consolas, monospace;
		text-align: right;
	}

	.mini {
		width: 40px;
		height: 40px;
		object-fit: cover;
		border-radius: 7px;
		border: 1px solid rgba(255, 255, 255, 0.14);
		display: block;
	}

	/* La fila que pasa el umbral se marca con borde Y con el conteo "n/n" del
	   texto, nunca solo con color. */
	.alerta-fila {
		background: rgba(220, 38, 38, 0.12);
		box-shadow: inset 3px 0 0 var(--mal);
	}
</style>

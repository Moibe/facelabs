// Cliente del API de facid. Tipado a mano contra api/main.py — si algo no
// cuadra, svelte-check lo marca en build en vez de romperse en pantalla.
//
// La URL se puede mover con VITE_API_URL. Default 127.0.0.1 (no localhost):
// en Windows 'localhost' resuelve a ::1 primero y uvicorn escucha en IPv4, lo
// que produce un ECONNREFUSED que parece "el API no arranco" cuando si arranco.

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://127.0.0.1:8077';

export class ApiCaida extends Error {
	constructor(public causa: unknown) {
		super('El API no responde');
	}
}

export class ApiError extends Error {
	constructor(
		public status: number,
		public detalle: string
	) {
		super(detalle || `HTTP ${status}`);
	}
}

async function pedir<T>(ruta: string, params?: Record<string, string | number>): Promise<T> {
	const url = new URL(BASE + ruta);
	for (const [k, v] of Object.entries(params ?? {})) url.searchParams.set(k, String(v));

	let r: Response;
	try {
		r = await fetch(url, { signal: AbortSignal.timeout(120_000) });
	} catch (causa) {
		// Distinguir "no hay API" de "el API dijo que no" importa: el primero se
		// arregla arrancando uvicorn, el segundo es un problema de datos.
		throw new ApiCaida(causa);
	}
	if (!r.ok) {
		let detalle = `HTTP ${r.status}`;
		try {
			const j = await r.json();
			if (typeof j?.detail === 'string') detalle = j.detail;
		} catch {
			/* respuesta sin JSON: se queda el HTTP nnn */
		}
		throw new ApiError(r.status, detalle);
	}
	return (await r.json()) as T;
}

async function enviar<T>(ruta: string, cuerpo: unknown): Promise<T> {
	let r: Response;
	try {
		r = await fetch(BASE + ruta, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(cuerpo),
			signal: AbortSignal.timeout(30 * 60_000) // una corrida puede tardar
		});
	} catch (causa) {
		throw new ApiCaida(causa);
	}
	if (!r.ok) {
		let detalle = `HTTP ${r.status}`;
		try {
			const j = await r.json();
			if (typeof j?.detail === 'string') detalle = j.detail;
		} catch {
			/* sin JSON */
		}
		throw new ApiError(r.status, detalle);
	}
	return (await r.json()) as T;
}

// multipart/form-data en vez de JSON: sólo lo usa /api/subir-fotos.
async function enviarArchivos<T>(ruta: string, form: FormData): Promise<T> {
	let r: Response;
	try {
		r = await fetch(BASE + ruta, {
			method: 'POST',
			body: form,
			signal: AbortSignal.timeout(120_000)
		});
	} catch (causa) {
		throw new ApiCaida(causa);
	}
	if (!r.ok) {
		let detalle = `HTTP ${r.status}`;
		try {
			const j = await r.json();
			if (typeof j?.detail === 'string') detalle = j.detail;
		} catch {
			/* sin JSON */
		}
		throw new ApiError(r.status, detalle);
	}
	return (await r.json()) as T;
}

// ---------------------------------------------------------------- tipos
export type Distribucion = {
	n: number;
	min?: number;
	max?: number;
	media?: number;
	mediana?: number;
	std?: number;
	p25?: number;
	p75?: number;
};

export type Par = {
	img_a: string;
	img_b: string;
	foto_a: string | null;
	foto_b: string | null;
	persona_a: string;
	persona_b: string;
	same_person: boolean;
	score: number | null;
	det_score_a: number | null;
	det_score_b: number | null;
	notes: string;
	provider_a: string;
	provider_b: string;
	n_faces_a: string;
	n_faces_b: string;
	face_selection_a: string;
	face_selection_b: string;
	error_a: string;
	error_b: string;
};

export type Descartado = {
	img_a: string;
	img_b: string;
	foto_a: string | null;
	foto_b: string | null;
	same_person: boolean;
	error_a: string;
	error_b: string;
	n_faces_a: string;
	n_faces_b: string;
	notes: string;
};

export type Tasas = {
	threshold: number;
	fmr: number;
	fmr_lo: number;
	fmr_hi: number;
	fp: number;
	n_nonmatch: number;
	fnmr: number;
	fnmr_lo: number;
	fnmr_hi: number;
	fn: number;
	n_match: number;
};

export type Traslape = {
	hay_traslape: boolean | null;
	zona_lo?: number;
	zona_hi?: number;
	zona_ancho?: number;
	match_en_zona?: number;
	nonmatch_en_zona?: number;
	brecha_lo?: number;
	brecha_hi?: number;
	brecha_ancho?: number;
	threshold_libre_de_error?: number;
	motivo?: string;
};

export type PuntoOperacion = Tasas & {
	tipo: 'fmr' | 'fnmr';
	objetivo: string;
	objetivo_valor: number;
	alcanzable: boolean;
	resolucion_insuficiente: boolean;
	resolucion_minima: number;
};

export type Fragilidad = {
	persona: string;
	n_fotos: number;
	pares_excluidos: number;
	threshold: number | null;
	fmr: number | null;
	fnmr: number | null;
};

export type Composicion = {
	n_pares: number;
	n_imagenes: number;
	n_identidades: number;
	reuso_max: number;
	reuso_medio: number;
	img_mas_usada: string;
	contradicciones: [string, string][];
};

export type Resultados = {
	csv: string;
	ok: boolean;
	motivo?: string;
	match: Distribucion;
	nonmatch: Distribucion;
	pares: Par[];
	descartados: Descartado[];
	providers: Record<string, number>;
	traslape?: Traslape;
	d_prime?: number | null;
	eer?: (Tasas & { eer: number }) | null;
	puntos_operacion?: PuntoOperacion[];
	barrido?: Tasas[];
	composicion?: Composicion;
	fragilidad?: Fragilidad[];
	resolucion?: { fmr_minima_medible: number; fnmr_minima_medible: number };
};

export type Personas = {
	data_dir: string;
	n_personas: number;
	n_fotos: number;
	personas: {
		persona: string;
		n_fotos: number;
		fotos: { ruta: string; nombre: string; bytes: number }[];
	}[];
};

export type CorridaEstado = {
	en_curso: boolean;
	etapa: 'cargando_modelo' | 'extraccion' | 'comparacion' | '';
	actual: number;
	total: number;
	archivo: string;
	resultado: { pares_total: number; pares_ok: number; csv: string } | null;
	error: string | null;
};

export type Entorno =
	| { ok: false; error: string; pista: string }
	| {
			ok: true;
			provider_activo: string;
			en_gpu: boolean;
			rec_model_file: string;
			rec_model_sha256: string;
			det_model_file: string;
			det_model_sha256: string;
			facid_version: string;
			python_version: string;
			platform: string;
			insightface_version: string | null;
			onnxruntime_package: string | null;
			numpy_version: string | null;
			opencv_version: string | null;
			available_providers: string[];
			active_providers: Record<string, string[]>;
			device_requested: string;
			model_pack: string;
			model_dir: string;
			model_files: Record<string, string>;
			det_size: string;
			cuda_libs_preloaded: number;
	  };

// ------------------------------------------------------------ Run: busqueda
export type CorpusResumen = {
	existe: boolean;
	corpus_dir: string;
	n_carpetas: number;
};

export type IndexarEstado = {
	en_curso: boolean;
	etapa: 'cargando_modelo' | 'explorando' | 'indexando' | 'pausado' | '';
	actual: number;
	total: number;
	archivo: string;
	/** Cuántas de las `actual` salieron de caché vs se procesaron de verdad. */
	en_cache: number;
	nuevas: number;
	/** Y de esas `nuevas`, cuántas dieron rostro y cuántas no. */
	nuevas_ok: number;
	nuevas_fallidas: number;
	resultado: {
		carpetas_vistas: number;
		fotos_vistas: number;
		indexadas_ok: number;
		fallidas: number;
		en_cache: number;
		nuevas: number;
		nuevas_ok: number;
		nuevas_fallidas: number;
		detenido: boolean;
	} | null;
	error: string | null;
};

export type Coincidencia = { persona: string; archivo: string; score: number; ruta: string };

/** Una fila del ranking consolidado: una PERSONA del corpus, no una foto. */
export type Consolidado = {
	persona: string;
	/** Mejor score contra cualquiera de las fotos de referencia. */
	mejor: number;
	/** Promedio de los mejores por foto de referencia — el criterio de orden. */
	promedio: number;
	n_consultas: number;
	n_fotos_corpus: number;
	mejor_ruta: string;
	/** {nombre de la foto de referencia: mejor score contra esta persona} */
	por_consulta: Record<string, number>;
};

export type ResultadoBusqueda = {
	n_indexado: number;
	n_carpetas_indexadas: number;
	resultados: { consulta: string; error: string | null; coincidencias: Coincidencia[] }[];
	consolidado?: Consolidado[];
	/** Presente en una búsqueda recién hecha; también al releerla del historial. */
	busqueda_id?: number;
	persona?: string;
	umbral?: number | null;
	creada_en?: string;
};

export type CorridaRegistrada = {
	id: number;
	tipo: string;
	corpus_dir: string | null;
	carpetas_vistas: number | null;
	fotos_vistas: number | null;
	indexadas_ok: number | null;
	fallidas: number | null;
	en_cache: number | null;
	nuevas: number | null;
	detenido: number | null;
	error: string | null;
	iniciada_en: string;
	terminada_en: string | null;
};

export type Cobertura = {
	procesadas: number;
	con_rostro: number;
	sin_rostro: number;
	total_ultimo_conteo: number | null;
	ultima_corrida: CorridaRegistrada | null;
};

/** Una foto del corpus, haya dado rostro o no. Un solo tipo para las dos
 *  porque las tiras y el explorador las pintan igual, cambiando el detalle. */
export type FotoCorpus = {
	/** Relativa al corpus: se sirve con urlFotoCorpus(). */
	ruta: string;
	estado: 'con_rostro' | 'sin_rostro';
	det_score: number | null;
	/** >0 = sólo se detectó tras rellenarle ese % de borde. null = se extrajo
	 *  antes de que existiera el reintento, así que no se sabe. */
	margen_agregado: number | null;
	error: string | null;
	n_faces_detected: number | null;
};

export type PaginaFotos = {
	total: number;
	offset: number;
	limite: number;
	fotos: FotoCorpus[];
};

export type PersonaCorpus = {
	persona: string;
	con_rostro: number;
	sin_rostro: number;
	total: number;
};

export type BusquedaGuardada = {
	id: number;
	persona: string;
	corpus_dir: string | null;
	umbral: number | null;
	n_indexado: number | null;
	n_carpetas_indexadas: number | null;
	creada_en: string;
};

// ---------------------------------------------------------------- llamadas
export const api = {
	version: () => pedir<{ name: string; version: string }>('/'),
	personas: () => pedir<Personas>('/api/personas'),
	csvs: () => pedir<{ out_dir: string; csvs: { ruta: string; bytes: number }[] }>('/api/csvs'),
	resultados: (csv: string) => pedir<Resultados>('/api/resultados', { csv }),
	tasas: (csv: string, threshold: number) => pedir<Tasas>('/api/tasas', { csv, threshold }),
	entorno: (device = 'cuda') => pedir<Entorno>('/api/entorno', { device }),
	store: () => pedir<Record<string, unknown>>('/api/store'),

	crearManifiesto: (salida: string, modo: 'ancla' | 'todos', excluir: string[] = []) =>
		enviar<{ pares: number; match: number; nonmatch: number; n_personas: number }>(
			'/api/manifiesto',
			{ salida, modo, excluir }
		),
	// La corrida arranca en segundo plano; correr() sólo la dispara y devuelve
	// el estado inicial. corridaEstado() es lo que hay que sondear para saber
	// cuándo termina y con qué avance va (ver /api/corrida/estado en la API).
	correr: (manifiesto: string, salida_csv: string, device: string) =>
		enviar<CorridaEstado>('/api/corrida', { manifiesto, salida_csv, device }),
	corridaEstado: () => pedir<CorridaEstado>('/api/corrida/estado'),

	// Reclasifica una foto: mueve el archivo real en data/ de una persona a
	// otra. movido=false con motivo cuando soltarla no cambia nada (misma
	// persona); 409 si ya hay un archivo con ese nombre en el destino.
	moverFoto: (ruta: string, personaDestino: string) =>
		enviar<{ movido: boolean; de?: string; a?: string; motivo?: string }>('/api/mover-foto', {
			ruta,
			persona_destino: personaDestino
		}),

	urlFoto: (ruta: string) => `${BASE}/api/foto?ruta=${encodeURIComponent(ruta)}`,

	// -------------------------------------------------------- Run: busqueda
	// El "dropbox": sube fotos de referencia a data/<persona>/, mismo destino
	// que usa Labs. archivos puede venir de un <input type=file multiple> o
	// de un DataTransfer.files al soltar un drag-and-drop.
	subirFotos: (persona: string, archivos: FileList | File[]) => {
		const form = new FormData();
		form.set('persona', persona);
		for (const a of archivos) form.append('archivos', a);
		return enviarArchivos<{ persona: string; guardadas: string[] }>('/api/subir-fotos', form);
	},

	corpusResumen: () => pedir<CorpusResumen>('/api/corpus/resumen'),
	urlFotoCorpus: (ruta: string) => `${BASE}/api/corpus/foto?ruta=${encodeURIComponent(ruta)}`,

	// Igual que correr()/corridaEstado(): indexar arranca en segundo plano
	// (puede tardar horas en el corpus completo), indexarEstado() sondea.
	indexarCorpus: (limiteCarpetas: number | null, limitePorCarpeta: number | null, device: string) =>
		enviar<IndexarEstado>('/api/corpus/indexar', {
			limite_carpetas: limiteCarpetas,
			limite_por_carpeta: limitePorCarpeta,
			device
		}),
	indexarEstado: () => pedir<IndexarEstado>('/api/corpus/indexar/estado'),
	// Pide parar en el siguiente punto seguro; el poll de indexarEstado()
	// que ya esta corriendo detecta en_curso=false solo, sin que haga falta
	// sondear esto por separado.
	detenerIndexar: () => enviar<{ deteniendo: boolean }>('/api/corpus/indexar/detener', {}),

	// Sincrono: sólo extrae las pocas fotos de consulta si hiciera falta, y
	// compara contra lo que YA este indexado (rapido incluso con el corpus
	// completo indexado — no re-extrae nada del corpus).
	buscarEnCorpus: (
		persona: string,
		limiteCarpetas: number | null,
		limitePorCarpeta: number | null,
		device: string,
		umbral: number | null = null,
		topN = 15
	) =>
		enviar<ResultadoBusqueda>('/api/corpus/buscar', {
			persona,
			limite_carpetas: limiteCarpetas,
			limite_por_carpeta: limitePorCarpeta,
			device,
			umbral,
			top_n: topN
		}),

	// Historial persistido en SQLite: lo unico que antes vivia solo en la
	// memoria del proceso, y por eso se perdia de vista al reiniciar.
	cobertura: () => pedir<Cobertura>('/api/corpus/cobertura'),
	// Para poder VER las que no dieron rostro: un conteo no dice si son
	// recortes malos o fotos que de verdad no traen cara.
	// Una sola llamada para las tiras cortas y para el explorador: cambian
	// los filtros, no la forma de la respuesta.
	fotosCorpus: (opts: {
		estado?: 'todas' | 'con_rostro' | 'sin_rostro';
		persona?: string | null;
		soloConMargen?: boolean;
		offset?: number;
		limite?: number;
	} = {}) => {
		const params: Record<string, string | number> = {
			estado: opts.estado ?? 'todas',
			offset: opts.offset ?? 0,
			limite: opts.limite ?? 100
		};
		if (opts.persona) params.persona = opts.persona;
		if (opts.soloConMargen) params.solo_con_margen = 'true';
		return pedir<PaginaFotos>('/api/corpus/fotos', params);
	},
	personasCorpus: () => pedir<{ personas: PersonaCorpus[] }>('/api/corpus/personas'),
	busquedas: (limite = 20) => pedir<{ busquedas: BusquedaGuardada[] }>('/api/busquedas', { limite }),
	verBusqueda: (id: number) => pedir<ResultadoBusqueda>(`/api/busquedas/${id}`),
	borrarBusqueda: (id: number) => enviar<{ borrada: number }>(`/api/busquedas/${id}/borrar`, {}),

	base: BASE
};

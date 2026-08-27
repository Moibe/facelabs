// Estado compartido entre páginas: qué CSV se está viendo y en qué threshold.
//
// El threshold vive aquí y no en cada página a propósito: mover el slider en el
// panorama y saltar a "pares" tiene que mostrar los mismos aciertos y errores.
// Si cada página tuviera el suyo, verías dos verdades distintas del mismo set.

import { ApiCaida, api, type Resultados } from './api';

// Fotos que el usuario sacó de la siguiente corrida (picker en "El set"), por
// ruta relativa a data/. Vive en localStorage y no en el API: es una
// preferencia de ESTE browser para probar subconjuntos sin tocar data/ ni
// pedirle nada al backend hasta que de verdad se genere un manifiesto.
const LS_EXCLUIDAS = 'facid.fotosExcluidas';

function leerExcluidasGuardadas(): Record<string, true> {
	try {
		const cruda = localStorage.getItem(LS_EXCLUIDAS);
		return cruda ? JSON.parse(cruda) : {};
	} catch {
		return {};
	}
}

function guardarExcluidas(v: Record<string, true>): void {
	try {
		localStorage.setItem(LS_EXCLUIDAS, JSON.stringify(v));
	} catch {
		// privado o cuota llena: se pierde al recargar, no es critico
	}
}

export const estado = $state({
	csv: 'scores.csv',
	/** Lista de CSVs disponibles en out/. */
	csvs: [] as string[],
	threshold: 0.45,
	/** true = el usuario ya movió el slider; deja de auto-ajustarse al EER. */
	thresholdTocado: false,
	datos: null as Resultados | null,
	cargando: false,
	/** Mensaje de error de datos (el API respondió, pero con un problema). */
	error: null as string | null,
	/** true = uvicorn no está arriba. Es un problema distinto y se resuelve distinto. */
	apiCaida: false,
	versionApi: null as string | null,
	/** ruta (relativa a data/) -> true, sólo para las excluidas. */
	fotosExcluidas: leerExcluidasGuardadas()
});

export function toggleFotoExcluida(ruta: string): void {
	if (estado.fotosExcluidas[ruta]) delete estado.fotosExcluidas[ruta];
	else estado.fotosExcluidas[ruta] = true;
	guardarExcluidas(estado.fotosExcluidas);
}

export function restaurarFotosExcluidas(): void {
	estado.fotosExcluidas = {};
	guardarExcluidas(estado.fotosExcluidas);
}

export async function cargarCsvs(): Promise<void> {
	try {
		const d = await api.csvs();
		estado.csvs = d.csvs.map((c) => c.ruta);
		if (estado.csvs.length && !estado.csvs.includes(estado.csv)) {
			estado.csv = estado.csvs[0];
		}
	} catch {
		// Silencioso: cargar() reporta la caída del API con más contexto.
	}
}

export async function cargar(csv?: string): Promise<void> {
	if (csv) estado.csv = csv;
	estado.cargando = true;
	estado.error = null;
	estado.apiCaida = false;
	try {
		const v = await api.version();
		estado.versionApi = v.version;
		await cargarCsvs();
		const d = await api.resultados(estado.csv);
		estado.datos = d;
		if (!d.ok) estado.error = d.motivo ?? 'El set no se puede calibrar.';
		// Arrancar en el EER da un punto de partida con sentido en vez de un 0.45
		// arbitrario. Se respeta la elección del usuario en cuanto toca el slider.
		if (!estado.thresholdTocado && d.eer?.threshold != null) {
			estado.threshold = d.eer.threshold;
		}
	} catch (e) {
		estado.datos = null;
		if (e instanceof ApiCaida) estado.apiCaida = true;
		else estado.error = e instanceof Error ? e.message : String(e);
	} finally {
		estado.cargando = false;
	}
}

export function fijarThreshold(t: number): void {
	estado.threshold = t;
	estado.thresholdTocado = true;
}

/** Clasifica un par bajo el threshold actual. Misma regla que decide.py: score >= t. */
export function veredicto(
	score: number | null,
	samePerson: boolean,
	threshold: number
): 'correcto' | 'falsa_aceptacion' | 'falso_rechazo' | 'sin_score' {
	if (score === null) return 'sin_score';
	const aceptado = score >= threshold;
	if (aceptado === samePerson) return 'correcto';
	return aceptado ? 'falsa_aceptacion' : 'falso_rechazo';
}

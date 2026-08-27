<script lang="ts">
	// El equivalente visual de `facid doctor`. Carga el modelo, así que puede
	// tardar; no se pide solo al abrir la app para no bloquear todo lo demás.
	import { goto } from '$app/navigation';
	import { api, type CorridaEstado, type Entorno } from '$lib/api';
	import { cargarCsvs, cargar as cargarResultados, estado } from '$lib/estado.svelte';

	let datos = $state<Entorno | null>(null);
	let cargando = $state(false);
	let device = $state<'cuda' | 'cpu'>('cuda');
	let fallo = $state<string | null>(null);

	async function consultar() {
		cargando = true;
		fallo = null;
		try {
			datos = await api.entorno(device);
		} catch (e) {
			datos = null;
			fallo = e instanceof Error ? e.message : String(e);
		} finally {
			cargando = false;
		}
	}

	// --------------------------------------------- generar + correr manifiesto
	// Las dos únicas escrituras que expone el API (ver api/main.py). Cuando
	// cambian las fotos en data/ (se agregan, quitan o renombran) hay que
	// regenerar el manifiesto antes de correrlo: si sólo cambió el contenido
	// de un archivo con el mismo nombre, correr alcanza (la caché es por
	// sha256, no por ruta).
	let modoManifiesto = $state<'ancla' | 'todos'>('ancla');
	let manifiesto = $state('mi_set.json');
	let salidaCsv = $state('scores.csv');
	let generando = $state(false);
	let corriendo = $state(false);
	let resumenManifiesto = $state<{
		pares: number;
		match: number;
		nonmatch: number;
		n_personas: number;
	} | null>(null);
	let resumenCorrida = $state<{ pares_total: number; pares_ok: number; csv: string } | null>(
		null
	);
	let falloAccion = $state<string | null>(null);
	// La corrida arranca en segundo plano en la API; esto es lo que se va
	// llenando con cada sondeo a /api/corrida/estado mientras corre.
	let progreso = $state<CorridaEstado | null>(null);

	const pctProgreso = $derived.by(() => {
		if (!progreso) return 0;
		if (progreso.etapa === 'comparacion') return 100;
		if (!progreso.total) return 0;
		return Math.round((progreso.actual / progreso.total) * 100);
	});

	const nExcluidas = $derived(Object.keys(estado.fotosExcluidas).length);

	async function generarManifiesto() {
		generando = true;
		falloAccion = null;
		resumenManifiesto = null;
		try {
			resumenManifiesto = await api.crearManifiesto(
				manifiesto,
				modoManifiesto,
				Object.keys(estado.fotosExcluidas)
			);
		} catch (e) {
			falloAccion = e instanceof Error ? e.message : String(e);
		} finally {
			generando = false;
		}
	}

	async function correr() {
		corriendo = true;
		falloAccion = null;
		resumenCorrida = null;
		progreso = null;
		try {
			progreso = await api.correr(manifiesto, salidaCsv, device);
			while (progreso.en_curso) {
				await new Promise((r) => setTimeout(r, 400));
				progreso = await api.corridaEstado();
			}
			if (progreso.error) falloAccion = progreso.error;
			else if (progreso.resultado) {
				resumenCorrida = progreso.resultado;
				await cargarCsvs();
			}
		} catch (e) {
			falloAccion = e instanceof Error ? e.message : String(e);
		} finally {
			corriendo = false;
		}
	}

	async function verEnPares() {
		if (!resumenCorrida) return;
		await cargarResultados(resumenCorrida.csv.split(/[\\/]/).pop());
		goto('/pares');
	}
</script>

<header class="encabezado">
	<h1>Entorno</h1>
	<p class="tenue">
		Qué provider corre de verdad y con qué pesos exactos. Es el registro de reproducibilidad: la
		etiqueta del pack puede mentir, el sha256 del <code>.onnx</code> no.
	</p>
</header>

<section class="tarjeta">
	<h2>Regenerar y correr</h2>
	<p class="tenue">
		Cambiaste fotos en <code>data/</code>: primero regenera el manifiesto (lee las carpetas de
		nuevo), luego córrelo (extrae y compara). Equivale a <code>facid init-manifest</code> +
		<code>facid run-manifest</code> desde la terminal.
		{#if nExcluidas > 0}
			<strong>{nExcluidas}</strong> foto(s) excluida(s) del próximo manifiesto — ajústalo en
			<a class="enlace" href="/set">El set</a>.
		{/if}
	</p>
	<div class="controles">
		<label>
			modo
			<select bind:value={modoManifiesto}>
				<option value="ancla">ancla</option>
				<option value="todos">todos</option>
			</select>
		</label>
		<label>
			manifiesto
			<input type="text" bind:value={manifiesto} />
		</label>
		<button type="button" onclick={generarManifiesto} disabled={generando}>
			{generando ? 'generando…' : '1. Generar manifiesto'}
		</button>
	</div>
	{#if resumenManifiesto}
		<p class="tenue resultado-accion">
			{resumenManifiesto.n_personas} personas → {resumenManifiesto.pares} pares ({resumenManifiesto.match}
			match, {resumenManifiesto.nonmatch} non-match) escritos en <code>{manifiesto}</code>.
		</p>
	{/if}

	<div class="controles controles-corrida">
		<label>
			device
			<select bind:value={device}>
				<option value="cuda">cuda</option>
				<option value="cpu">cpu</option>
			</select>
		</label>
		<label>
			salida csv
			<input type="text" bind:value={salidaCsv} />
		</label>
		<button type="button" onclick={correr} disabled={corriendo}>
			{corriendo ? 'corriendo…' : '2. Correr'}
		</button>
	</div>
	{#if corriendo && progreso}
		<div class="progreso">
			<div class="barra-progreso">
				<div class="barra-progreso-relleno" style="width: {pctProgreso}%"></div>
			</div>
			<p class="tenue progreso-texto">
				{#if progreso.etapa === 'cargando_modelo'}
					Cargando el modelo (~300 MB de ONNX)…
				{:else if progreso.etapa === 'extraccion'}
					Extrayendo {progreso.actual}/{progreso.total} fotos — <code>{progreso.archivo}</code>
				{:else if progreso.etapa === 'comparacion'}
					Comparando {progreso.total} par(es)…
				{/if}
			</p>
		</div>
	{/if}
	{#if resumenCorrida}
		<p class="tenue resultado-accion">
			{resumenCorrida.pares_ok}/{resumenCorrida.pares_total} pares utilizables en
			<code>{resumenCorrida.csv.split(/[\\/]/).pop()}</code>.
			<button type="button" class="enlace" onclick={verEnPares}>Ver en Pares →</button>
		</p>
	{/if}

	{#if falloAccion}
		<p class="tenue error-accion">{falloAccion}</p>
	{/if}
</section>

<section class="tarjeta">
	<div class="controles">
		<label>
			device
			<select bind:value={device}>
				<option value="cuda">cuda</option>
				<option value="cpu">cpu</option>
			</select>
		</label>
		<button type="button" onclick={consultar} disabled={cargando}>
			{cargando ? 'cargando el modelo…' : 'Consultar'}
		</button>
	</div>
	<p class="tenue">
		Esto carga ~300 MB de ONNX, así que tarda unos segundos la primera vez. Equivale a
		<code>python -m facid doctor</code>.
	</p>
</section>

{#if fallo}
	<section class="tarjeta borde-mal">
		<h2>No se pudo consultar</h2>
		<p>{fallo}</p>
	</section>
{/if}

{#if datos && !datos.ok}
	<section class="tarjeta borde-mal">
		<h2>El modelo no cargó</h2>
		<pre>{datos.error}</pre>
		<p>{datos.pista}</p>
	</section>
{/if}

{#if datos?.ok}
	<section class="tarjeta" class:borde-mal={!datos.en_gpu && datos.device_requested === 'cuda'}>
		<h2>Provider activo</h2>
		<p class="hero" class:bien={datos.en_gpu} class:mal={!datos.en_gpu}>
			{datos.en_gpu ? 'GPU (CUDA)' : 'CPU'}
		</p>
		<p class="tenue">
			No sale de <code>get_available_providers()</code> —eso dice qué está compilado, no qué corre—
			sino de interrogar la sesión ONNX ya construida de cada modelo.
		</p>
		<div class="tabla-scroll">
			<table>
				<thead><tr><th>Modelo</th><th>Providers de su sesión</th></tr></thead>
				<tbody>
					{#each Object.entries(datos.active_providers) as [tarea, provs] (tarea)}
						<tr><td><strong>{tarea}</strong></td><td><code>{provs.join(', ')}</code></td></tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if !datos.en_gpu && datos.device_requested === 'cuda'}
			<p class="tenue">
				Se pidió CUDA y quedó en CPU. Los embeddings son equivalentes y la calibración sigue siendo
				válida — sólo cambia la velocidad. Pero si esperabas GPU, el setup no quedó bien.
			</p>
		{/if}
	</section>

	<section class="tarjeta">
		<h2>Pesos ({datos.model_pack})</h2>
		<p class="tenue"><code>{datos.model_dir}</code> · det_size {datos.det_size}</p>
		<div class="tabla-scroll">
			<table>
				<thead><tr><th>Archivo</th><th>sha256</th><th>Rol</th></tr></thead>
				<tbody>
					{#each Object.entries(datos.model_files) as [archivo, sha] (archivo)}
						<tr>
							<td><code>{archivo}</code></td>
							<td><code class="sha">{sha.slice(0, 24)}…</code></td>
							<td class="tenue">
								{#if archivo === datos.rec_model_file}
									reconocimiento · embeddings 512-d
								{:else if archivo === datos.det_model_file}
									detección
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>

	<section class="tarjeta">
		<h2>Versiones</h2>
		<div class="tabla-scroll">
			<table>
				<tbody>
					{#each [['facid', datos.facid_version], ['python', datos.python_version], ['plataforma', datos.platform], ['insightface', datos.insightface_version], ['onnxruntime', datos.onnxruntime_package], ['numpy', datos.numpy_version], ['opencv', datos.opencv_version], ['providers compilados', datos.available_providers.join(', ')], ['libs nvidia precargadas', String(datos.cuda_libs_preloaded)]] as [k, v] (k)}
						<tr><td class="clave">{k}</td><td><code>{v ?? '—'}</code></td></tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
{/if}

<style>
	.encabezado {
		margin: 0.4rem 0 1rem;
		max-width: 70ch;
	}

	.controles {
		display: flex;
		align-items: center;
		gap: 0.8rem;
		flex-wrap: wrap;
		margin-bottom: 0.6rem;
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

	.controles-corrida {
		margin-top: 0.9rem;
		padding-top: 0.9rem;
		border-top: 1px solid var(--linea);
	}

	input[type='text'] {
		font: inherit;
		font-size: 0.85rem;
		color: var(--ink);
		background: rgba(10, 25, 70, 0.9);
		border: 1px solid rgba(255, 255, 255, 0.18);
		border-radius: 9px;
		padding: 0.4rem 0.6rem;
		width: 13ch;
	}

	.resultado-accion {
		margin: 0.5rem 0 0;
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

	.error-accion {
		margin: 0.5rem 0 0;
		color: var(--mal);
	}

	.enlace {
		font: inherit;
		font-size: 0.82rem;
		color: #93c5fd;
		background: none;
		border: none;
		padding: 0;
		margin-left: 0.3rem;
		cursor: pointer;
		text-decoration: underline;
	}

	.enlace:hover {
		color: #bfdbfe;
	}

	.hero {
		font-size: 1.6rem;
		font-weight: 650;
		font-family: ui-monospace, Consolas, monospace;
		margin: 0 0 0.4rem;
	}

	.hero.bien {
		color: var(--bien);
	}

	.hero.mal {
		color: var(--mal);
	}

	.clave {
		color: var(--ink-3);
		white-space: nowrap;
		width: 1%;
	}

	.sha {
		font-size: 0.75em;
	}

	.borde-mal {
		border-color: rgba(248, 113, 113, 0.4);
		background: rgba(220, 38, 38, 0.09);
	}
</style>

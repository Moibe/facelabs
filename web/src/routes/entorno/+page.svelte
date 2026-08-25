<script lang="ts">
	// El equivalente visual de `facid doctor`. Carga el modelo, así que puede
	// tardar; no se pide solo al abrir la app para no bloquear todo lo demás.
	import { api, type Entorno } from '$lib/api';

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
</script>

<header class="encabezado">
	<h1>Entorno</h1>
	<p class="tenue">
		Qué provider corre de verdad y con qué pesos exactos. Es el registro de reproducibilidad: la
		etiqueta del pack puede mentir, el sha256 del <code>.onnx</code> no.
	</p>
</header>

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

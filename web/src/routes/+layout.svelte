<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';
	import Sidebar from '$lib/Sidebar.svelte';
	import TopNav from '$lib/TopNav.svelte';
	import { api } from '$lib/api';
	import { cargar, estado } from '$lib/estado.svelte';

	let { children } = $props();
	let collapsed = $state(false);

	// Una sola carga al arrancar; las páginas leen del estado compartido.
	$effect(() => {
		if (estado.datos === null && !estado.cargando && !estado.apiCaida && !estado.error) {
			void cargar();
		}
	});

	// Usa View Transitions cuando el browser las soporta para animar el repliegue
	// de la barra; si no, hace el cambio directo.
	function withTransition(fn: () => void) {
		if (typeof document !== 'undefined' && 'startViewTransition' in document) {
			(document as unknown as { startViewTransition: (cb: () => void) => void }).startViewTransition(
				fn
			);
		} else {
			fn();
		}
	}

	function toggleCollapsed() {
		withTransition(() => {
			collapsed = !collapsed;
		});
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>facid — verificación facial 1:1</title>
</svelte:head>

<TopNav />
<Sidebar {collapsed} {toggleCollapsed} />
<main class={collapsed ? 'collapsed' : ''}>
	<div class="work-scroll">
		{#if estado.apiCaida}
			<!-- Estado de primera clase, no un error escondido en consola: el caso
			     más probable es abrir el front sin haber levantado uvicorn. -->
			<section class="tarjeta aviso-caido">
				<h1>El API no responde</h1>
				<p>
					El front sólo mira; los datos los sirve el API en <code>{api.base}</code>. Arráncalo desde
					la raíz del repo:
				</p>
				<pre>.venv/bin/python -m uvicorn api.main:app --port 8077</pre>
				<p class="tenue">
					O usa <code>npm run dev</code> desde <code>web/</code>, que levanta las dos cosas.
				</p>
				<p class="tenue">
					Si el API <em>sí</em> está arriba pero en otro puerto, apunta el front al mismo:
					<code>VITE_API_URL=http://127.0.0.1:&lt;puerto&gt; npm run dev</code>. Este panel también
					aparece cuando el browser bloquea las llamadas por CORS, que se ve idéntico a una API
					apagada.
				</p>
				<p class="tenue">
					Nada de esto es necesario para trabajar: la CLI funciona igual con el API apagada.
					<code>python -m facid calibrate out/scores.csv</code> imprime los mismos números en la
					terminal.
				</p>
				<button type="button" onclick={() => cargar()}>Reintentar</button>
			</section>
		{:else}
			{@render children()}
		{/if}
	</div>
</main>

<style>
	:global(:root) {
		--topnav-height: 64px;

		/* Paleta de series, validada con el validador de dataviz sobre la
		   superficie de plot --plot-surface: pasa banda de luminosidad, piso de
		   croma, separación CVD (ΔE 13.8 protan), piso de visión normal (28.8) y
		   contraste ≥3:1. No cambiar un hex sin volver a correr el validador. */
		--serie-match: #0d9488;
		--serie-nonmatch: #ea580c;
		--plot-surface: #16265e;

		/* Tinta: el texto NUNCA lleva el color de la serie. */
		--ink: rgba(255, 255, 255, 0.95);
		--ink-2: rgba(255, 255, 255, 0.72);
		--ink-3: rgba(255, 255, 255, 0.5);
		--linea: rgba(255, 255, 255, 0.12);

		/* Estado, reservado: nunca se reusa como "serie 3". */
		--mal: #f87171;
		--bien: #4ade80;
	}

	:global(html, body) {
		margin: 0;
		padding: 0;
		height: 100%;
	}

	:global(body) {
		min-height: 100vh;
		background: linear-gradient(135deg, #4169e1 0%, #1e3a8a 100%);
		background-attachment: fixed;
		color: var(--ink);
		font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
	}

	main {
		position: fixed;
		top: calc(2rem + var(--topnav-height));
		right: 1rem;
		bottom: 1rem;
		box-sizing: border-box;
		background: rgba(255, 255, 255, 0.012);
		backdrop-filter: blur(8px) saturate(110%);
		-webkit-backdrop-filter: blur(8px) saturate(110%);
		border: 1px solid #fff;
		border-radius: 16px;
		box-shadow:
			inset 0 1px 0 rgba(255, 255, 255, 0.08),
			0 4px 16px rgba(0, 0, 0, 0.12);
		overflow: hidden;
		transition: left 0.22s ease-out;
		left: calc(var(--sidebar-width, 240px) + 2rem);
	}

	main.collapsed {
		left: 2rem;
	}

	.work-scroll {
		position: absolute;
		top: 16px;
		bottom: 16px;
		left: 0;
		right: 0;
		overflow-y: auto;
		overflow-x: hidden;
		padding: 0 16px;
	}

	/* ---- Vocabulario compartido por las páginas ---- */
	:global(.tarjeta) {
		background: rgba(255, 255, 255, 0.04);
		border: 1px solid var(--linea);
		border-radius: 14px;
		padding: 1.1rem 1.25rem;
		margin: 0 0 1rem;
	}

	:global(h1) {
		font-size: 1.35rem;
		margin: 0 0 0.5rem;
		font-weight: 650;
	}

	:global(h2) {
		font-size: 1.02rem;
		margin: 0 0 0.7rem;
		font-weight: 600;
		color: var(--ink);
	}

	:global(p) {
		margin: 0 0 0.6rem;
		line-height: 1.55;
		color: var(--ink-2);
		font-size: 0.9rem;
	}

	:global(.tenue) {
		color: var(--ink-3);
		font-size: 0.82rem;
	}

	:global(code) {
		font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
		font-size: 0.85em;
		background: rgba(0, 0, 0, 0.28);
		padding: 0.1rem 0.35rem;
		border-radius: 5px;
		color: rgba(255, 255, 255, 0.9);
	}

	:global(pre) {
		font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
		font-size: 0.82rem;
		background: rgba(0, 0, 0, 0.32);
		border: 1px solid var(--linea);
		border-radius: 8px;
		padding: 0.7rem 0.85rem;
		overflow-x: auto;
		color: rgba(255, 255, 255, 0.92);
	}

	:global(button) {
		font: inherit;
		font-size: 0.88rem;
		color: var(--ink);
		background: rgba(255, 255, 255, 0.07);
		border: 1px solid rgba(255, 255, 255, 0.18);
		border-radius: 9px;
		padding: 0.45rem 0.9rem;
		cursor: pointer;
		transition:
			background 0.16s ease,
			border-color 0.16s ease;
	}

	:global(button:hover) {
		background: rgba(255, 255, 255, 0.13);
		border-color: rgba(255, 255, 255, 0.3);
	}

	:global(select) {
		font: inherit;
		font-size: 0.85rem;
		color: var(--ink);
		background: rgba(10, 25, 70, 0.9);
		border: 1px solid rgba(255, 255, 255, 0.18);
		border-radius: 9px;
		padding: 0.4rem 0.6rem;
	}

	/* Tablas: cualquier contenido ancho hace scroll en su propio contenedor,
	   nunca el body. */
	:global(.tabla-scroll) {
		overflow-x: auto;
		border: 1px solid var(--linea);
		border-radius: 12px;
	}

	:global(table) {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
		font-variant-numeric: tabular-nums;
	}

	:global(th) {
		text-align: left;
		font-weight: 600;
		color: var(--ink-3);
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		padding: 0.6rem 0.7rem;
		border-bottom: 1px solid var(--linea);
		white-space: nowrap;
	}

	:global(td) {
		padding: 0.55rem 0.7rem;
		border-bottom: 1px solid rgba(255, 255, 255, 0.06);
		color: var(--ink-2);
	}

	:global(tbody tr:last-child td) {
		border-bottom: none;
	}

	.aviso-caido {
		max-width: 62ch;
		margin-top: 1rem;
		border-color: rgba(248, 113, 113, 0.4);
		background: rgba(220, 38, 38, 0.1);
	}
</style>

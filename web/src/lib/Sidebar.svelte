<script lang="ts">
	// Barra lateral "de vidrio" con el mismo tilt 3D que la superior. Incluye el
	// handle para replegar/mostrar. Publica su ancho real a la variable CSS
	// --sidebar-width para que el panel de contenido se ajuste solo.
	import { page } from '$app/state';
	import { cargar, estado } from '$lib/estado.svelte';

	let {
		collapsed = false,
		toggleCollapsed
	}: {
		collapsed?: boolean;
		toggleCollapsed: () => void;
	} = $props();

	let tiltX = $state(0);
	let tiltY = $state(0);
	let sidebarWidth = $state(240);

	const secciones = [
		{ href: '/set', etiqueta: 'El set' },
		{ href: '/entorno', etiqueta: 'Entorno' },
		{ href: '/pares', etiqueta: 'Pares' },
		{ href: '/', etiqueta: 'Panorama' }
	];

	const actual = $derived(page.url.pathname);

	$effect(() => {
		if (typeof document !== 'undefined' && !collapsed) {
			document.documentElement.style.setProperty('--sidebar-width', `${sidebarWidth}px`);
		}
	});

	function handleMove(e: MouseEvent) {
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
		const ny = ((e.clientY - rect.top) / rect.height) * 2 - 1;
		const MAX = 1.2;
		tiltX = -ny * MAX;
		tiltY = nx * MAX;
	}

	function handleLeave() {
		tiltX = 0;
		tiltY = 0;
	}

	function handleCollapseClick(e: MouseEvent) {
		e.stopPropagation();
		tiltX = 0;
		tiltY = 0;
		toggleCollapsed();
	}
</script>

{#if !collapsed}
	<aside
		class="sidebar"
		style="transform: perspective(900px) rotateX({tiltX}deg) rotateY({tiltY}deg);"
		bind:clientWidth={sidebarWidth}
		onmousemove={handleMove}
		onmouseleave={handleLeave}
	>
		<!-- El CSV vivía en TopNav, pero ahí se veía en cualquier pantalla
		     (incluso Run y Corpus, que no lo usan) por estar en la barra
		     global. Es la salida de una calibración de Labs, así que su lugar
		     es aquí, junto a las 4 páginas que sí lo leen. -->
		{#if !estado.apiCaida}
			<div class="csv-actual">
				<span class="csv-rotulo">CSV</span>
				{#if estado.cargando}
					<span class="estado cargando">cargando…</span>
				{:else if estado.datos}
					{#if estado.csvs.length > 1}
						<select
							class="selector-csv"
							value={estado.csv}
							onchange={(e) => cargar((e.currentTarget as HTMLSelectElement).value)}
						>
							{#each estado.csvs as c (c)}
								<option value={c}>{c}</option>
							{/each}
						</select>
					{:else}
						<span class="estado vivo">{estado.csv}</span>
					{/if}
				{/if}
			</div>
		{/if}

		<nav>
			{#each secciones as s (s.href)}
				<a
					href={s.href}
					class="nav-item"
					aria-current={actual === s.href ? 'page' : undefined}
				>
					<span class="nav-ico" aria-hidden="true"></span>
					<span>{s.etiqueta}</span>
				</a>
			{/each}
		</nav>

		<p class="aviso">
			La CLI es la fuente de verdad. Esto sólo la mira.
		</p>

		<div class="sidebar-footer">
			<button
				type="button"
				class="collapse-btn"
				onclick={handleCollapseClick}
				aria-label="Replegar barra"
			>
				<svg
					width="18"
					height="18"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2.2"
					stroke-linecap="round"
					stroke-linejoin="round"
				>
					<path d="m15 18-6-6 6-6" />
				</svg>
			</button>
		</div>
	</aside>
{:else}
	<button type="button" class="reveal-handle" onclick={toggleCollapsed} aria-label="Mostrar barra">
		<svg
			width="18"
			height="18"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			stroke-width="2.2"
			stroke-linecap="round"
			stroke-linejoin="round"
		>
			<path d="m9 18 6-6-6-6" />
		</svg>
	</button>
{/if}

<style>
	.sidebar {
		position: fixed;
		top: calc(2rem + var(--topnav-height, 64px));
		left: 1rem;
		bottom: 1rem;
		box-sizing: border-box;
		width: max-content;
		min-width: 240px;
		max-width: 380px;
		padding: 1.5rem 1rem;
		display: flex;
		flex-direction: column;
		background: rgba(255, 255, 255, 0.012);
		backdrop-filter: blur(8px) saturate(110%);
		-webkit-backdrop-filter: blur(8px) saturate(110%);
		border: 1px solid #fff;
		border-radius: 16px;
		box-shadow:
			inset 0 1px 0 rgba(255, 255, 255, 0.08),
			0 4px 16px rgba(0, 0, 0, 0.12);
		transition: transform 0.18s ease-out;
		will-change: transform;
		user-select: none;
	}

	.csv-actual {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		margin-bottom: 1rem;
		padding-bottom: 1rem;
		border-bottom: 1px solid rgba(255, 255, 255, 0.08);
	}

	.csv-rotulo {
		font-size: 0.68rem;
		font-weight: 650;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: rgba(255, 255, 255, 0.5);
	}

	.estado {
		align-self: flex-start;
		font-size: 0.8rem;
		padding: 0.35rem 0.7rem;
		border-radius: 999px;
		border: 1px solid rgba(255, 255, 255, 0.16);
		font-variant-numeric: tabular-nums;
	}

	.estado.vivo {
		color: rgba(255, 255, 255, 0.85);
		background: rgba(255, 255, 255, 0.06);
	}

	.estado.cargando {
		color: rgba(255, 255, 255, 0.7);
	}

	.selector-csv {
		width: 100%;
		font: inherit;
		font-size: 0.85rem;
		padding: 0.45rem 0.6rem;
		border-radius: 8px;
		border: 1px solid rgba(255, 255, 255, 0.16);
		/* Sólido, no translúcido: el popup del <select> lo pinta el SO sobre su
		   propia superficie opaca, sin el gradiente de la página detrás. Un
		   fondo casi-transparente ahí se ve blanco puro (el bug reportado). */
		background: rgba(10, 25, 70, 0.95);
		color: rgba(255, 255, 255, 0.85);
		font-variant-numeric: tabular-nums;
	}

	.selector-csv option {
		background: rgb(10, 25, 70);
		color: rgba(255, 255, 255, 0.92);
	}

	nav {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		scrollbar-width: none;
		-ms-overflow-style: none;
	}

	nav::-webkit-scrollbar {
		display: none;
	}

	.nav-item {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		padding: 0.7rem 0.95rem;
		color: rgba(255, 255, 255, 0.92);
		text-decoration: none;
		font-size: 0.95rem;
		letter-spacing: 0.01em;
		border-radius: 8px;
		border: 1px solid transparent;
		text-shadow:
			0 0 8px rgba(255, 255, 255, 0.22),
			0 0 18px rgba(255, 255, 255, 0.1);
		transition:
			background 0.18s ease,
			border-color 0.18s ease;
	}

	.nav-ico {
		width: 16px;
		height: 16px;
		border-radius: 5px;
		flex-shrink: 0;
		background: rgba(147, 197, 253, 0.55);
		box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.3);
	}

	.nav-item:hover {
		background: rgba(255, 255, 255, 0.09);
		border-color: rgba(255, 255, 255, 0.16);
	}

	.nav-item[aria-current='page'] {
		color: #fff;
		background: rgba(37, 99, 235, 0.18);
		border-color: rgba(37, 99, 235, 0.45);
		box-shadow: 0 0 0 1px rgba(37, 99, 235, 0.18) inset;
	}

	.nav-item[aria-current='page'] .nav-ico {
		background: #93c5fd;
	}

	.aviso {
		margin: 1rem 0 0;
		font-size: 0.74rem;
		line-height: 1.45;
		color: rgba(255, 255, 255, 0.5);
	}

	.sidebar-footer {
		display: flex;
		align-items: center;
		justify-content: center;
		margin-top: auto;
		padding-top: 1rem;
		border-top: 1px solid rgba(255, 255, 255, 0.08);
	}

	.collapse-btn,
	.reveal-handle {
		background: rgba(255, 255, 255, 0.04);
		border: 1px solid rgba(255, 255, 255, 0.14);
		border-radius: 8px;
		padding: 0.4rem 0.5rem;
		color: rgba(255, 255, 255, 0.85);
		cursor: pointer;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		font: inherit;
		transition:
			background 0.18s ease,
			border-color 0.18s ease,
			color 0.18s ease;
	}

	.collapse-btn:hover,
	.reveal-handle:hover {
		background: rgba(255, 255, 255, 0.1);
		border-color: rgba(255, 255, 255, 0.24);
		color: #fff;
	}

	/* Cuando la barra está replegada, queda solo este handle flotante de vidrio. */
	.reveal-handle {
		position: fixed;
		left: 0.75rem;
		top: 50%;
		transform: translateY(-50%);
		padding: 0.55rem 0.45rem;
		border-radius: 12px;
		border: 1px solid #fff;
		background: rgba(255, 255, 255, 0.012);
		backdrop-filter: blur(8px) saturate(110%);
		-webkit-backdrop-filter: blur(8px) saturate(110%);
		box-shadow:
			inset 0 1px 0 rgba(255, 255, 255, 0.08),
			0 4px 16px rgba(0, 0, 0, 0.12);
		z-index: 10;
	}
</style>

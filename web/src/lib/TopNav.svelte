<script lang="ts">
	// Barra superior "de vidrio" con tilt 3D al pasar el mouse (parallax).
	// Se calcula la posición relativa del cursor (-1..1 en cada eje) y la barra
	// se inclina suavemente hacia él; vuelve a plano al salir (transition).
	import { estado } from '$lib/estado.svelte';

	let tiltX = $state(0);
	let tiltY = $state(0);

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
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<header
	class="topnav"
	style="transform: perspective(900px) rotateX({tiltX}deg) rotateY({tiltY}deg);"
	onmousemove={handleMove}
	onmouseleave={handleLeave}
>
	<a href="/" class="brand" aria-label="Inicio">
		<span class="brand-mark" aria-hidden="true"></span>
		<span class="brand-title">facid</span>
	</a>

	<div class="lema">verificación 1:1 · research, no comercial</div>

	<div class="spacer"></div>

	{#if estado.apiCaida}
		<span class="estado caido">API apagada</span>
	{:else if estado.cargando}
		<span class="estado cargando">cargando…</span>
	{:else if estado.datos}
		<span class="estado vivo">{estado.csv}</span>
	{/if}
</header>

<style>
	.topnav {
		position: fixed;
		top: 1rem;
		left: 1rem;
		right: 1rem;
		height: var(--topnav-height, 64px);
		padding: 0 1.25rem;
		box-sizing: border-box;
		display: flex;
		align-items: center;
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
		z-index: 9;
	}

	.brand {
		display: flex;
		align-items: center;
		gap: 0.65rem;
		color: rgba(255, 255, 255, 0.98);
		text-decoration: none;
		border-radius: 8px;
		padding: 0.25rem 0.4rem;
		transition: background 0.18s ease;
	}

	.brand:hover {
		background: rgba(255, 255, 255, 0.05);
	}

	.brand-mark {
		width: 22px;
		height: 22px;
		border-radius: 6px;
		background: linear-gradient(135deg, #93c5fd, #2563eb);
		box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4);
	}

	.brand-title {
		font-size: 1.2rem;
		font-weight: 700;
		letter-spacing: 0.005em;
		text-shadow:
			0 0 10px rgba(255, 255, 255, 0.28),
			0 0 24px rgba(255, 255, 255, 0.14);
	}

	.lema {
		margin-left: 1.25rem;
		padding-left: 1.25rem;
		border-left: 1px solid rgba(255, 255, 255, 0.08);
		font-size: 0.82rem;
		color: rgba(255, 255, 255, 0.62);
		letter-spacing: 0.01em;
	}

	.spacer {
		flex: 1;
	}

	.estado {
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

	.estado.caido {
		color: #fecaca;
		background: rgba(220, 38, 38, 0.22);
		border-color: rgba(248, 113, 113, 0.5);
	}

	@media (max-width: 720px) {
		.lema {
			display: none;
		}
	}
</style>

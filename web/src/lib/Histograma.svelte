<script lang="ts">
	// Distribución de scores: misma persona vs personas distintas.
	//
	// Decisiones de diseño (guía dataviz):
	// - Barras AGRUPADAS, no superpuestas con transparencia: el traslape es el
	//   resultado del experimento, y mezclar colores lo vuelve ilegible justo
	//   donde importa. Hay 2px de separación entre rellenos vecinos.
	// - El área de plot tiene su PROPIA superficie oscura (--plot-surface) en vez
	//   de heredar el gradiente. Sobre el extremo claro del gradiente los dos
	//   colores de serie caen a 2.6:1 y 2.1:1 de contraste; sobre esta superficie
	//   pasan 3:1. La paleta está validada contra este color, no contra el fondo.
	// - La porción de cada barra que el threshold actual clasifica MAL se marca
	//   con textura + contorno, nunca sólo con color: así el error se distingue
	//   en impresión, en daltonismo y en forced-colors.
	// - El texto usa tinta, nunca el color de la serie.

	let {
		match,
		nonmatch,
		threshold,
		onThreshold,
		bins = 22
	}: {
		match: number[];
		nonmatch: number[];
		threshold: number;
		onThreshold?: (t: number) => void;
		bins?: number;
	} = $props();

	const W = 900;
	const H = 330;
	const M = { top: 18, right: 16, bottom: 46, left: 46 };
	const PW = W - M.left - M.right;
	const PH = H - M.top - M.bottom;

	type Bin = {
		lo: number;
		hi: number;
		centro: number;
		m: number;
		mMal: number;
		nm: number;
		nmMal: number;
	};

	const dominio = $derived.by(() => {
		const todos = [...match, ...nonmatch];
		if (!todos.length) return { lo: -0.1, hi: 1 };
		let lo = Math.min(...todos, threshold);
		let hi = Math.max(...todos, threshold);
		const pad = Math.max(0.02, (hi - lo) * 0.06);
		return { lo: lo - pad, hi: hi + pad };
	});

	const datos = $derived.by((): Bin[] => {
		const { lo, hi } = dominio;
		const paso = (hi - lo) / bins;
		const out: Bin[] = [];
		for (let i = 0; i < bins; i++) {
			const bLo = lo + i * paso;
			const bHi = bLo + paso;
			// El último bin incluye su borde derecho para no perder el máximo.
			const dentro = (v: number) => (i === bins - 1 ? v >= bLo && v <= bHi : v >= bLo && v < bHi);
			const ms = match.filter(dentro);
			const nms = nonmatch.filter(dentro);
			out.push({
				lo: bLo,
				hi: bHi,
				centro: (bLo + bHi) / 2,
				m: ms.length,
				// Un par match con score BAJO el threshold se rechaza: falso rechazo.
				mMal: ms.filter((v) => v < threshold).length,
				nm: nms.length,
				// Un par non-match con score SOBRE el threshold se acepta: falsa aceptación.
				nmMal: nms.filter((v) => v >= threshold).length
			});
		}
		return out;
	});

	const maxY = $derived(Math.max(1, ...datos.map((b) => Math.max(b.m, b.nm))));

	const x = $derived((v: number) => ((v - dominio.lo) / (dominio.hi - dominio.lo)) * PW);
	const y = $derived((n: number) => PH - (n / maxY) * PH);
	const anchoBin = $derived(PW / bins);
	// 2px de aire entre rellenos vecinos, y las dos barras del grupo dentro del bin.
	const anchoBarra = $derived(Math.max(2, (anchoBin - 6) / 2));

	const ticksY = $derived.by(() => {
		const paso = Math.max(1, Math.ceil(maxY / 4));
		const out: number[] = [];
		for (let v = 0; v <= maxY; v += paso) out.push(v);
		return out;
	});

	const ticksX = $derived.by(() => {
		const { lo, hi } = dominio;
		const out: number[] = [];
		const paso = 0.1;
		const inicio = Math.ceil(lo / paso) * paso;
		for (let v = inicio; v <= hi + 1e-9; v += paso) out.push(Math.round(v * 100) / 100);
		return out;
	});

	// Radio de las puntas: 4px, ancladas a la línea base.
	function barra(px: number, n: number, w: number): string {
		if (n <= 0) return '';
		const alto = PH - y(n);
		const r = Math.min(4, w / 2, alto);
		const top = y(n);
		return `M ${px} ${PH} L ${px} ${top + r} Q ${px} ${top} ${px + r} ${top} L ${px + w - r} ${top} Q ${px + w} ${top} ${px + w} ${top + r} L ${px + w} ${PH} Z`;
	}

	let hover = $state<{ bin: Bin; serie: 'match' | 'nonmatch'; cx: number } | null>(null);

	function clickPlot(e: MouseEvent) {
		if (!onThreshold) return;
		const svg = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
		const px = ((e.clientX - svg.left) / svg.width) * W - M.left;
		const v = dominio.lo + (px / PW) * (dominio.hi - dominio.lo);
		onThreshold(Math.max(-1, Math.min(1, Math.round(v * 1000) / 1000)));
	}

	const totalMal = $derived({
		fa: datos.reduce((s, b) => s + b.nmMal, 0),
		fr: datos.reduce((s, b) => s + b.mMal, 0)
	});
</script>

<figure>
	<div class="leyenda">
		<span class="item">
			<span class="swatch" style="background: var(--serie-match)"></span>
			Misma persona ({match.length})
		</span>
		<span class="item">
			<span class="swatch" style="background: var(--serie-nonmatch)"></span>
			Personas distintas ({nonmatch.length})
		</span>
		<span class="item">
			<span class="swatch textura" aria-hidden="true"></span>
			Mal clasificado en este threshold
		</span>
		<span class="cuentas">
			{totalMal.fa} falsa{totalMal.fa === 1 ? '' : 's'} aceptación{totalMal.fa === 1 ? '' : 'es'}
			· {totalMal.fr} falso{totalMal.fr === 1 ? '' : 's'} rechazo{totalMal.fr === 1 ? '' : 's'}
		</span>
	</div>

	<div class="lienzo">
		<!-- El clic en la gráfica es un ATAJO de puntero redundante: el mismo
		     threshold se ajusta con el slider `input[type=range]` de la página,
		     que ya es enfocable y operable con teclado. Por eso el SVG no
		     necesita su propio manejo de teclado. -->
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions, a11y_no_noninteractive_element_interactions -->
		<svg
			viewBox="0 0 {W} {H}"
			role="img"
			aria-label="Histograma de scores por clase de par"
			onclick={clickPlot}
			onmouseleave={() => (hover = null)}
			class={onThreshold ? 'clicable' : ''}
		>
			<defs>
				<!-- Textura direccional para la porción mal clasificada: 45° en una
				     serie, 135° en la otra, para que no se confundan entre sí. -->
				<pattern id="tex-m" width="6" height="6" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
					<line x1="0" y1="0" x2="0" y2="6" stroke="rgba(255,255,255,0.85)" stroke-width="2" />
				</pattern>
				<pattern id="tex-nm" width="6" height="6" patternTransform="rotate(135)" patternUnits="userSpaceOnUse">
					<line x1="0" y1="0" x2="0" y2="6" stroke="rgba(255,255,255,0.85)" stroke-width="2" />
				</pattern>
			</defs>

			<!-- Superficie propia del plot: la paleta se validó contra este color. -->
			<rect
				x={M.left}
				y={M.top}
				width={PW}
				height={PH}
				rx="10"
				fill="var(--plot-surface)"
				stroke="rgba(255,255,255,0.10)"
			/>

			<g transform="translate({M.left},{M.top})">
				<!-- Rejilla recesiva -->
				{#each ticksY as t (t)}
					<line
						x1="0"
						y1={y(t)}
						x2={PW}
						y2={y(t)}
						stroke="rgba(255,255,255,0.07)"
						stroke-width="1"
					/>
					<text x="-8" y={y(t) + 4} text-anchor="end" class="tick">{t}</text>
				{/each}

				{#each ticksX as t (t)}
					<text x={x(t)} y={PH + 18} text-anchor="middle" class="tick">{t.toFixed(1)}</text>
				{/each}

				<!-- Barras agrupadas -->
				{#each datos as b, i (i)}
					{@const px = i * anchoBin + 2}
					{#if b.nm > 0}
						<path
							d={barra(px, b.nm, anchoBarra)}
							fill="var(--serie-nonmatch)"
							stroke="var(--plot-surface)"
							stroke-width="2"
							role="presentation"
							onmouseenter={() => (hover = { bin: b, serie: 'nonmatch', cx: px + anchoBarra / 2 })}
						/>
						{#if b.nmMal > 0}
							<path
								d={barra(px, b.nmMal, anchoBarra)}
								fill="url(#tex-nm)"
								stroke="#fff"
								stroke-width="1.2"
								pointer-events="none"
							/>
						{/if}
					{/if}
					{#if b.m > 0}
						<path
							d={barra(px + anchoBarra + 2, b.m, anchoBarra)}
							fill="var(--serie-match)"
							stroke="var(--plot-surface)"
							stroke-width="2"
							role="presentation"
							onmouseenter={() =>
								(hover = { bin: b, serie: 'match', cx: px + anchoBarra + 2 + anchoBarra / 2 })}
						/>
						{#if b.mMal > 0}
							<path
								d={barra(px + anchoBarra + 2, b.mMal, anchoBarra)}
								fill="url(#tex-m)"
								stroke="#fff"
								stroke-width="1.2"
								pointer-events="none"
							/>
						{/if}
					{/if}
				{/each}

				<!-- Threshold: anotación, no serie — va en tinta, no en color de serie. -->
				<line
					x1={x(threshold)}
					y1="-6"
					x2={x(threshold)}
					y2={PH}
					stroke="#fff"
					stroke-width="2"
					stroke-dasharray="5 4"
				/>
				<text x={x(threshold)} y="-9" text-anchor="middle" class="etiqueta-t">
					t = {threshold.toFixed(3)}
				</text>

				<line x1="0" y1={PH} x2={PW} y2={PH} stroke="rgba(255,255,255,0.22)" stroke-width="1" />
			</g>

			<text x={W / 2} y={H - 6} text-anchor="middle" class="eje">Similitud coseno</text>
		</svg>

		{#if hover}
			<div class="tooltip" style="left: {((M.left + hover.cx) / W) * 100}%">
				<strong>{hover.serie === 'match' ? 'Misma persona' : 'Personas distintas'}</strong>
				<span>[{hover.bin.lo.toFixed(3)}, {hover.bin.hi.toFixed(3)})</span>
				<span>{hover.serie === 'match' ? hover.bin.m : hover.bin.nm} par(es)</span>
				{#if (hover.serie === 'match' ? hover.bin.mMal : hover.bin.nmMal) > 0}
					<span class="mal">
						{hover.serie === 'match' ? hover.bin.mMal : hover.bin.nmMal}
						{hover.serie === 'match' ? 'rechazado(s) de más' : 'aceptado(s) de más'}
					</span>
				{/if}
			</div>
		{/if}
	</div>

	{#if onThreshold}
		<figcaption class="tenue">
			Clic en la gráfica para mover el threshold. Las barras con textura son los pares que ese
			threshold clasifica mal.
		</figcaption>
	{/if}
</figure>

<style>
	figure {
		margin: 0;
	}

	.leyenda {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem 1.1rem;
		margin-bottom: 0.7rem;
		font-size: 0.82rem;
		color: var(--ink-2);
	}

	.item {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
	}

	.swatch {
		width: 11px;
		height: 11px;
		border-radius: 3px;
		display: inline-block;
	}

	.swatch.textura {
		background:
			repeating-linear-gradient(
				45deg,
				rgba(255, 255, 255, 0.9) 0 2px,
				transparent 2px 4px
			),
			rgba(255, 255, 255, 0.15);
		border: 1px solid #fff;
	}

	.cuentas {
		margin-left: auto;
		color: var(--ink-3);
		font-variant-numeric: tabular-nums;
	}

	.lienzo {
		position: relative;
	}

	svg {
		width: 100%;
		height: auto;
		display: block;
		overflow: visible;
	}

	svg.clicable {
		cursor: crosshair;
	}

	.tick {
		font-size: 11px;
		fill: rgba(255, 255, 255, 0.45);
		font-family: ui-monospace, Consolas, monospace;
	}

	.eje {
		font-size: 12px;
		fill: rgba(255, 255, 255, 0.55);
	}

	.etiqueta-t {
		font-size: 12px;
		font-weight: 600;
		fill: #fff;
		font-family: ui-monospace, Consolas, monospace;
	}

	.tooltip {
		position: absolute;
		top: 0;
		transform: translateX(-50%);
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		background: rgba(6, 16, 48, 0.96);
		border: 1px solid rgba(255, 255, 255, 0.22);
		border-radius: 8px;
		padding: 0.45rem 0.65rem;
		font-size: 0.78rem;
		color: var(--ink-2);
		pointer-events: none;
		white-space: nowrap;
		z-index: 3;
	}

	.tooltip strong {
		color: var(--ink);
		font-weight: 600;
	}

	.tooltip .mal {
		color: var(--mal);
	}

	figcaption {
		margin-top: 0.5rem;
	}
</style>

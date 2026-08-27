// Arrastrar una miniatura recortada con object-fit:cover para recentrarla,
// al estilo "reposicionar foto de perfil". El math:
//
// object-fit:cover escala la imagen (uniforme) hasta que cubre la caja, y
// object-position: x% y% decide qué franja del sobrante queda fuera. A x=0%
// se ve el borde IZQUIERDO/SUPERIOR de la imagen (el sobrante recorta a la
// derecha/abajo); a x=100% se ve el borde derecho/inferior.
//
// Arrastrar hacia la derecha debe sentirse como mover la FOTO con el cursor
// (la foto "sigue" al mouse), lo que implica que la franja visible se
// desplaza hacia la IZQUIERDA dentro de la imagen — por eso el signo negativo
// en dx/exceso más abajo. Verificado a mano con los casos límite 0%/100%.
import type { Action } from 'svelte/action';
import { estado, fijarRecorte, restaurarRecorte } from './estado.svelte';

function clamp(v: number, lo: number, hi: number): number {
	return Math.min(hi, Math.max(lo, v));
}

export const arrastrarRecorte: Action<HTMLImageElement, string> = (node, ruta) => {
	let arrastrando = false;
	let inicioClienteX = 0;
	let inicioClienteY = 0;
	let inicioX = 50;
	let inicioY = 50;
	let excesoX = 0;
	let excesoY = 0;
	let movio = false;

	node.draggable = false;

	function onPointerDown(e: PointerEvent) {
		if (e.button !== 0) return;
		if (!node.naturalWidth || !node.naturalHeight) return; // aun no carga

		const rect = node.getBoundingClientRect();
		const escala = Math.max(rect.width / node.naturalWidth, rect.height / node.naturalHeight);
		excesoX = node.naturalWidth * escala - rect.width;
		excesoY = node.naturalHeight * escala - rect.height;
		if (excesoX <= 1 && excesoY <= 1) return; // nada que recorrer en ningun eje

		const actual = estado.recortes[ruta];
		inicioX = actual?.x ?? 50;
		inicioY = actual?.y ?? 50;
		inicioClienteX = e.clientX;
		inicioClienteY = e.clientY;
		movio = false;
		arrastrando = true;
		node.setPointerCapture(e.pointerId);
		node.classList.add('arrastrando-recorte');
	}

	function onPointerMove(e: PointerEvent) {
		if (!arrastrando) return;
		const dx = e.clientX - inicioClienteX;
		const dy = e.clientY - inicioClienteY;
		if (Math.abs(dx) > 2 || Math.abs(dy) > 2) movio = true;

		const x = excesoX > 1 ? clamp(inicioX - (dx / excesoX) * 100, 0, 100) : inicioX;
		const y = excesoY > 1 ? clamp(inicioY - (dy / excesoY) * 100, 0, 100) : inicioY;
		estado.recortes[ruta] = { x, y };
	}

	function onPointerUp(e: PointerEvent) {
		if (!arrastrando) return;
		arrastrando = false;
		node.classList.remove('arrastrando-recorte');
		try {
			node.releasePointerCapture(e.pointerId);
		} catch {
			/* ya se libero solo (p. ej. el pointer se cancelo) */
		}
		if (movio) {
			const r = estado.recortes[ruta];
			fijarRecorte(ruta, r?.x ?? 50, r?.y ?? 50);
		}
	}

	function onDblClick() {
		restaurarRecorte(ruta);
	}

	node.addEventListener('pointerdown', onPointerDown);
	node.addEventListener('pointermove', onPointerMove);
	node.addEventListener('pointerup', onPointerUp);
	node.addEventListener('pointercancel', onPointerUp);
	node.addEventListener('dblclick', onDblClick);

	return {
		destroy() {
			node.removeEventListener('pointerdown', onPointerDown);
			node.removeEventListener('pointermove', onPointerMove);
			node.removeEventListener('pointerup', onPointerUp);
			node.removeEventListener('pointercancel', onPointerUp);
			node.removeEventListener('dblclick', onDblClick);
		}
	};
};

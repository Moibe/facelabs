import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	// Fijo en vez del 5173 default. strictPort: false porque si algun dia esta
	// ocupado, preferimos que Vite corra en otro puerto avisando en consola a
	// que la sesion de dev truene — el CORS del API igual lo bloquearia y se
	// veria como "API apagada" hasta que alguien ajuste el regex de nuevo.
	server: { port: 1000, strictPort: false },
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// adapter-static con fallback = SPA. Esta app es un tablero LOCAL que
			// lee todo del API en runtime, asi que no hay nada que renderizar en
			// servidor: sin fallback, adapter-auto intentaria prerenderizar rutas
			// que dependen de fetch al API y el build tronaria.
			adapter: adapter({ fallback: 'index.html' })
		})
	]
});

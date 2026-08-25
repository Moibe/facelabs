import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
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

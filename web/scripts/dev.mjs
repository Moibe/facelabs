#!/usr/bin/env node
/**
 * Arranca el API de facid y luego el front, en paralelo.
 *
 * Adaptado del patrón habitual (API hermana) al layout de ESTE repo, donde el
 * API vive en el mismo árbol: API_DIR es la raíz del repo, el venv es el mismo
 * que usa la CLI, y el entrypoint es api.main:app.
 *
 * Flujo:
 *   1. Lanza uvicorn como child process (venv de la raíz del repo).
 *   2. Polla GET /health hasta que responde 200 o se vence el timeout.
 *   3. Una vez healthy, lanza el dev del front.
 *   4. Cualquier señal (Ctrl+C) limpia ambos procesos.
 *
 * Overridables via env:
 *   API_DIR               (default: ..)
 *   API_HOST              (default: 127.0.0.1)
 *   API_PORT              (default: 8077 — el 8000 suele estar tomado)
 *   API_HEALTH_PATH       (default: /health)
 *   API_ENTRYPOINT        (default: api.main:app)
 *   API_READY_TIMEOUT_MS  (default: 60000)
 *   MIN_API_VERSION       (default: 0.0.0 = sin enforcement, sólo skip si healthy)
 */

import { execSync, spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

const API_DIR = resolve(ROOT, process.env.API_DIR ?? '..');
const API_HOST = process.env.API_HOST ?? '127.0.0.1';
const API_PORT = Number(process.env.API_PORT ?? 8077);
const HEALTH_PATH = process.env.API_HEALTH_PATH ?? '/health';
const ENTRYPOINT = process.env.API_ENTRYPOINT ?? 'api.main:app';
const API_READY_TIMEOUT_MS = Number(process.env.API_READY_TIMEOUT_MS ?? 60000);
const MIN_API_VERSION = process.env.MIN_API_VERSION ?? '0.0.0';
// Firma de identidad: debe coincidir con el title de FastAPI en api/main.py.
const API_NAME = 'facid api';

// ── Colors ──────────────────────────────────────────────────────────────────
const c = {
	dim: (s) => `\x1b[2m${s}\x1b[0m`,
	cyan: (s) => `\x1b[36m${s}\x1b[0m`,
	magenta: (s) => `\x1b[35m${s}\x1b[0m`,
	green: (s) => `\x1b[32m${s}\x1b[0m`,
	red: (s) => `\x1b[31m${s}\x1b[0m`,
	yellow: (s) => `\x1b[33m${s}\x1b[0m`,
	bold: (s) => `\x1b[1m${s}\x1b[0m`
};

const ts = () => new Date().toTimeString().slice(0, 8);
const prefix = (tag, color) => `${c.dim(ts())} ${color(`[${tag}]`)} `;
const logApi = (line) => process.stdout.write(prefix('api', c.cyan) + line + '\n');
const logWeb = (line) => process.stdout.write(prefix('web', c.magenta) + line + '\n');
const logSys = (line) => process.stdout.write(prefix('dev', c.yellow) + line + '\n');

// ── Locate Python venv ──────────────────────────────────────────────────────
function findPython() {
	const candidates =
		process.platform === 'win32'
			? ['Scripts/python.exe', 'Scripts/python']
			: ['bin/python', 'bin/python3'];
	for (const rel of candidates) {
		const p = resolve(API_DIR, '.venv', rel);
		if (existsSync(p)) return p;
	}
	return null;
}

// ── Health probe ────────────────────────────────────────────────────────────
async function waitForHealth(url, timeoutMs) {
	const started = Date.now();
	let lastErr = null;
	while (Date.now() - started < timeoutMs) {
		try {
			const r = await fetch(url, { signal: AbortSignal.timeout(2000) });
			if (r.ok) return { ok: true, elapsedMs: Date.now() - started };
		} catch (err) {
			lastErr = err;
		}
		await new Promise((res) => setTimeout(res, 500));
	}
	return { ok: false, elapsedMs: Date.now() - started, lastErr };
}

// ── Version probe + kill-on-port ────────────────────────────────────────────
function versionGte(a, b) {
	const p = (v) => v.split('.').map((n) => parseInt(n, 10) || 0);
	const [aMa, aMi, aP] = p(a);
	const [bMa, bMi, bP] = p(b);
	if (aMa !== bMa) return aMa > bMa;
	if (aMi !== bMi) return aMi > bMi;
	return aP >= bP;
}

/**
 * Sondea el puerto y decide si lo que contesta es NUESTRO API.
 *
 * Que /health responda 200 no alcanza: en esta máquina hay varios proyectos con
 * su propia FastAPI, y el puerto 8000 ya lo tenía otro. Sin verificar identidad,
 * el front se engancha a un API ajeno y muestra 404 o —peor— datos de otro
 * proyecto, sin que nada avise. Por eso se exige el `name` que sirve GET /.
 */
async function probeRunningApi(host, port, healthPath) {
	let running = false;
	try {
		const h = await fetch(`http://${host}:${port}${healthPath}`, {
			signal: AbortSignal.timeout(800)
		});
		if (h.ok) running = true;
	} catch {
		/* no está arriba */
	}
	if (!running) return { running: false, esFacid: false, version: null, quien: null };

	let version = null;
	let quien = null;
	try {
		const r = await fetch(`http://${host}:${port}/`, { signal: AbortSignal.timeout(800) });
		if (r.ok) {
			const json = await r.json();
			if (typeof json?.version === 'string') version = json.version;
			if (typeof json?.name === 'string') quien = json.name;
		}
	} catch {
		/* sin identidad */
	}
	return { running: true, esFacid: quien === API_NAME, version, quien };
}

function killProcessOnPort(port) {
	try {
		if (process.platform === 'win32') {
			const out = execSync('netstat -ano -p TCP', { encoding: 'utf8' });
			const pids = new Set();
			for (const line of out.split('\n')) {
				if (!line.includes(`:${port} `) || !line.toUpperCase().includes('LISTENING')) continue;
				const parts = line.trim().split(/\s+/);
				const pid = parts[parts.length - 1];
				if (/^\d+$/.test(pid)) pids.add(pid);
			}
			for (const pid of pids) {
				try {
					execSync(`taskkill /PID ${pid} /F /T`, { stdio: 'ignore' });
				} catch {
					/* ya murió */
				}
			}
			return pids.size;
		}
		const pids = execSync(`lsof -ti:${port}`, { encoding: 'utf8' })
			.trim()
			.split('\n')
			.filter(Boolean);
		for (const pid of pids) {
			try {
				execSync(`kill -9 ${pid}`);
			} catch {
				/* ya murió */
			}
		}
		return pids.length;
	} catch {
		return 0;
	}
}

// ── Pipe child output line-by-line with prefix ──────────────────────────────
function pipeLines(stream, logger) {
	let buf = '';
	stream.on('data', (chunk) => {
		buf += chunk.toString();
		let idx;
		while ((idx = buf.indexOf('\n')) >= 0) {
			const line = buf.slice(0, idx).replace(/\r$/, '');
			if (line.length > 0) logger(line);
			buf = buf.slice(idx + 1);
		}
	});
	stream.on('end', () => {
		if (buf.length > 0) logger(buf);
	});
}

// ── Spawn helpers ───────────────────────────────────────────────────────────
let apiProc = null;
let webProc = null;
let shuttingDown = false;

function shutdown(code = 0) {
	if (shuttingDown) return;
	shuttingDown = true;
	logSys('apagando procesos…');
	for (const p of [webProc, apiProc]) {
		if (p && p.exitCode === null) {
			try {
				p.kill(process.platform === 'win32' ? undefined : 'SIGTERM');
			} catch {
				/* ya murió */
			}
		}
	}
	setTimeout(() => process.exit(code), 300);
}

process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

// ── Main ────────────────────────────────────────────────────────────────────
(async () => {
	logSys(`repo: ${c.bold(API_DIR)}`);
	if (!existsSync(API_DIR)) {
		logSys(c.red(`no existe ${API_DIR}. Set API_DIR env var si está en otra ruta.`));
		process.exit(1);
	}

	const healthUrl = `http://${API_HOST}:${API_PORT}${HEALTH_PATH}`;

	const running = await probeRunningApi(API_HOST, API_PORT, HEALTH_PATH);
	if (running.running && !running.esFacid) {
		// Alguien más tiene el puerto. NO se mata: no es nuestro proceso, y matar
		// a ciegas el servicio de otro proyecto es peor que no arrancar.
		logSys(c.red(`el puerto ${API_PORT} lo tiene otro servicio, no el API de facid.`));
		logSys(c.dim(`  GET / respondió name=${JSON.stringify(running.quien)}, se esperaba "${API_NAME}"`));
		logSys(c.yellow(`  Arranca en otro puerto:  API_PORT=8078 npm run dev`));
		logSys(c.dim(`  (y apunta el front al mismo: VITE_API_URL=http://127.0.0.1:8078)`));
		process.exit(1);
	}
	if (running.running) {
		const versionOk =
			MIN_API_VERSION === '0.0.0' ||
			(running.version && versionGte(running.version, MIN_API_VERSION));
		if (versionOk) {
			const tag = running.version ? `v${running.version}` : 'versión desconocida';
			logSys(c.green(`✓ API de facid ya estaba arriba (${tag}) — skip spawn`));
			startWeb();
			return;
		}
		const got = running.version ?? 'desconocida';
		logSys(
			c.yellow(`API de facid existente es v${got}, se requiere >= v${MIN_API_VERSION} — reiniciando…`)
		);
		// Sólo se llega aquí con identidad confirmada: el proceso ES nuestro.
		const killed = killProcessOnPort(API_PORT);
		if (killed > 0) logSys(c.dim(`procesos terminados: ${killed}`));
		await new Promise((r) => setTimeout(r, 800));
	}

	const python = findPython();
	if (!python) {
		logSys(
			c.red(
				`no encontré .venv en ${API_DIR}. Créalo con ./setup.sh, y luego:\n` +
					`  ${API_DIR}/.venv/bin/python -m pip install -r api/requirements.txt`
			)
		);
		process.exit(1);
	}
	logSys(`python: ${c.bold(python)}`);

	const startedApi = Date.now();
	logApi(c.dim(`arrancando uvicorn ${ENTRYPOINT} en http://${API_HOST}:${API_PORT} …`));
	apiProc = spawn(
		python,
		['-m', 'uvicorn', ENTRYPOINT, '--host', API_HOST, '--port', String(API_PORT)],
		{ cwd: API_DIR, env: { ...process.env, PYTHONUNBUFFERED: '1' } }
	);
	pipeLines(apiProc.stdout, logApi);
	pipeLines(apiProc.stderr, logApi);
	apiProc.on('exit', (code, signal) => {
		logApi(c.red(`uvicorn terminó (code=${code}, signal=${signal})`));
		if (!shuttingDown) {
			logSys(
				c.yellow(
					'si falta fastapi:  .venv/bin/python -m pip install -r api/requirements.txt'
				)
			);
			shutdown(code ?? 1);
		}
	});

	logSys(`esperando health en ${healthUrl} (timeout ${API_READY_TIMEOUT_MS}ms)…`);
	const { ok, elapsedMs, lastErr } = await waitForHealth(healthUrl, API_READY_TIMEOUT_MS);
	if (!ok) {
		logSys(
			c.red(`API no respondió healthy en ${elapsedMs}ms. Último error: ${lastErr?.message ?? 'n/a'}`)
		);
		shutdown(1);
		return;
	}
	const apiBootMs = Date.now() - startedApi;
	logSys(c.green(`✓ API healthy en ${elapsedMs}ms (boot total ${apiBootMs}ms) — ${healthUrl}`));
	startWeb();
})();

function startWeb() {
	logWeb(c.dim('arrancando front (npm run dev:web-only) …'));
	// Windows requiere shell:true para resolver .cmd/.bat (npm, vite); en POSIX no hace daño.
	webProc = spawn('npm run dev:web-only', {
		cwd: ROOT,
		env: { ...process.env, FORCE_COLOR: '1' },
		shell: true
	});
	pipeLines(webProc.stdout, logWeb);
	pipeLines(webProc.stderr, logWeb);
	webProc.on('exit', (code, signal) => {
		logWeb(c.red(`front terminó (code=${code}, signal=${signal})`));
		shutdown(code ?? 0);
	});
}

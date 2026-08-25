// Tablero local puro-cliente: todo sale del API en runtime.
//
// ssr = false porque no hay nada que renderizar en servidor — y porque
// prerenderizar rutas que hacen fetch al API haria fallar el build en cualquier
// maquina donde uvicorn no este arriba, que es justamente el caso normal.
export const ssr = false;
export const prerender = false;

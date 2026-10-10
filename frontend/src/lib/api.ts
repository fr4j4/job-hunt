// Cliente de la API. 401 → pantalla de acceso (se pide un enlace nuevo con /web en el bot).
import type { DetalleOferta, EventoHist, FuentesResp, HistoriaDiaria, HistoriaTechs, Perfil, Semantica,
              Snapshot, Supervivencia, Vista } from './tipos';

export class SinSesion extends Error {}

async function pedir<T>(ruta: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(ruta, { credentials: 'same-origin', ...init });
  if (r.status === 401) throw new SinSesion('sin_sesion');
  if (!r.ok) throw new Error(`${ruta}: ${r.status}`);
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

const escritura = (metodo: string, cuerpo?: unknown): RequestInit => ({
  method: metodo,
  headers: { 'Content-Type': 'application/json', 'X-JH': '1' },
  body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
});

export const api = {
  snapshot: () => pedir<Snapshot>('/api/snapshot'),
  semantica: () => pedir<Semantica>('/api/semantica'),
  perfil: () => pedir<Perfil>('/api/perfil'),
  // `ref` = id numérico (preferido) o el group_id viejo de un enlace antiguo
  oferta: (ref: number | string) => pedir<DetalleOferta>(`/api/oferta/${encodeURIComponent(String(ref))}`),
  buscar: (q: string) => pedir<{ ids: number[] }>(`/api/buscar?q=${encodeURIComponent(q)}`),
  fuentes: () => pedir<FuentesResp>('/api/fuentes'),
  diaria: (m: string, por = '*') => pedir<HistoriaDiaria>(`/api/historia/diaria?m=${m}&por=${por}`),
  techsSemanal: (techs: string[], rol = '*') =>
    pedir<HistoriaTechs>(`/api/historia/techs?techs=${encodeURIComponent(techs.join(','))}&rol_familia=${encodeURIComponent(rol)}`),
  eventos: (tipo = '', limite = 100) => pedir<{ eventos: EventoHist[] }>(`/api/historia/eventos?tipo=${tipo}&limite=${limite}`),
  supervivencia: (rolFamilia = '') => pedir<Supervivencia>(`/api/historia/supervivencia?rol_familia=${encodeURIComponent(rolFamilia)}`),
  ponerEstado: (id: number, estado: string, nota = '') =>
    pedir<{ estado: string }>(`/api/estado/${id}`, escritura('PUT', { estado, nota })),
  quitarEstado: (id: number) => pedir<void>(`/api/estado/${id}`, escritura('DELETE')),
  vistas: () => pedir<{ vistas: Vista[] }>('/api/vistas'),
  crearVista: (nombre: string, tipo: string, spec: Record<string, unknown>) =>
    pedir<Vista>('/api/vistas', escritura('POST', { nombre, tipo, spec })),
  borrarVista: (id: number) => pedir<void>(`/api/vistas/${id}`, escritura('DELETE')),
};

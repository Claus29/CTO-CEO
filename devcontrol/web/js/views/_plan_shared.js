// Utilidades compartidas por las vistas de planificación y calidad (ideas, semana, versiones,
// roadmap, calendario, renovaciones, builds, pruebas y tiendas). No es una vista: no tiene ruta.
import { api } from '../api.js';
import { activeProjects, defaultProjectId, projectById, store } from '../state.js';
import { field, h, selectEl, toast } from '../ui.js';

// Versiones visibles (de todos los proyectos), para los desplegables de los formularios.
export async function loadVersions() {
  return (await api.get('/api/versions')).items;
}

export function versionOptions(versions, projectId, empty = 'Sin versión') {
  const list = versions.filter(v => !projectId || v.project_id === Number(projectId));
  return [['', empty], ...list.map(v => [String(v.id), `${v.name}${v.status === 'publicada' ? ' (publicada)' : ''}`])];
}

// Desplegable de proyecto para las altas. Si solo hay un proyecto activo, queda oculto.
export function projectField(value, onChange, { optional = false, label = 'Proyecto' } = {}) {
  const projects = activeProjects();
  const opts = [...(optional ? [['', 'Todos los proyectos']] : []), ...projects.map(p => [String(p.id), `${p.key} · ${p.name}`])];
  const sel = selectEl(opts, value === null || value === undefined ? '' : String(value));
  if (onChange) sel.addEventListener('change', () => onChange(sel.value));
  const wrap = field(label, sel, { required: !optional });
  if (projects.length < 2 && !optional) wrap.classList.add('hidden');
  return { sel, el: wrap };
}

export const newProjectId = () => defaultProjectId();
export const projectKey = (id) => projectById(id)?.key || '';
export const multiProject = () => store.projects.length > 1;

// Error al guardar: un 409 (cambio simultáneo) recarga los datos y cierra el diálogo; el resto lo deja abierto.
export function handleSaveError(err, reload) {
  if (err && err.status === 409) {
    if (reload) reload();
    return true;
  }
  return false;
}

// Ejecuta una petición desde un diálogo: devuelve false (no cerrar) si falla, salvo en un conflicto 409.
export async function submit(fn, okMessage, reload) {
  try {
    const r = await fn();
    if (r && r._queued) toast(r.message || 'Guardado sin conexión: se enviará al reconectar.', 'warn');
    else if (okMessage) toast(okMessage, 'ok');
    if (reload) reload();
    return true;
  } catch (err) {
    toast(err.message || String(err), err.status === 409 ? 'warn' : 'error');
    return handleSaveError(err, reload) ? undefined : false;
  }
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = h('textarea', { style: { position: 'fixed', opacity: '0' } });
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } finally { ta.remove(); }
  }
  toast('Copiado al portapapeles.', 'ok');
}

// Diferencia en días entre dos fechas AAAA-MM-DD (b - a).
export function daysBetween(a, b) {
  const pa = a.split('-').map(Number), pb = b.split('-').map(Number);
  return Math.round((Date.UTC(pb[0], pb[1] - 1, pb[2]) - Date.UTC(pa[0], pa[1] - 1, pa[2])) / 86400000);
}

export function addMonthsISO(iso, n) {
  const [y, m, d] = iso.split('-').map(Number);
  const target = new Date(y, m - 1 + n, 1);
  const last = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
  target.setDate(Math.min(d, last));
  const pad = (x) => String(x).padStart(2, '0');
  return `${target.getFullYear()}-${pad(target.getMonth() + 1)}-${pad(target.getDate())}`;
}

const BADGES = {
  idea: { propuesta: 'neutral', planificada: 'info', en_desarrollo: 'purple', descartada: 'danger' },
  version: { planificada: 'neutral', en_curso: 'info', publicada: 'ok' },
  store: { preparando: 'neutral', enviada: 'info', en_revision: 'purple', rechazada: 'danger', aprobada: 'ok', publicada: 'ok', retirada: 'neutral' },
  result: { pendiente: 'neutral', ok: 'ok', fallo: 'danger', bloqueado: 'warn', omitido: 'neutral' },
  channel: { interno: 'neutral', beta: 'purple', produccion: 'ok' },
};
export function kindBadge(kind, value, label) {
  return h('span', { class: `badge ${BADGES[kind]?.[value] || 'neutral'}` }, label || value);
}

// Barra de progreso (0-100).
export function bar(percent, cls = '') {
  const p = Math.max(0, Math.min(100, Math.round(percent || 0)));
  return h('div', { class: `progress ${cls} ${p === 100 ? 'ok' : ''}`, title: `${p}%` }, h('i', { style: { width: `${p}%` } }));
}

// Comprueba campos obligatorios de un formulario: [[input, 'nombre'], ...]. Enfoca el primero vacío.
export function requireFields(pairs) {
  for (const [el, name] of pairs) {
    if (!String(el.value || '').trim()) {
      toast(`Falta ${name}.`, 'error');
      el.focus();
      return false;
    }
  }
  return true;
}

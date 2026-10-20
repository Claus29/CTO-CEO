// Actividad: historial de lo que se ha hecho, con filtros y enlaces a cada elemento.
import { api } from '../api.js';
import { can, entityHash, store, userById, withProject } from '../state.js';
import {
  avatar, busy, debounce, emptyState, errorBox, fmtDayLabel, fmtTime, h, icon, loading, mount, pageHead, richText, selectEl, toast,
} from '../ui.js';

const TYPES = {
  incident: 'Incidencias', task: 'Tareas', idea: 'Ideas', decision: 'Decisiones', wiki: 'Wiki', version: 'Versiones',
  build: 'Builds', test_run: 'Pruebas', store: 'Tiendas', renewal: 'Renovaciones', goal: 'Objetivos semanales',
  date: 'Fechas del calendario', standup: 'Daily', absence: 'Ausencias', channel: 'Canales de chat', project: 'Proyectos',
  user: 'Usuarios', role: 'Roles', settings: 'Ajustes', backup: 'Copias de seguridad', vault: 'Bóveda', system: 'Sistema',
};
const PAGE = 50;

function linkFor(a) {
  switch (a.entity_type) {
    case 'incident': case 'task': case 'decision':
      return a.entity_id || a.entity_key ? entityHash(a.entity_type, a.entity_id, a.entity_key) : null;
    case 'wiki': return a.entity_id ? entityHash('wiki', a.entity_id) : '#/wiki';
    case 'idea': case 'renewal': case 'version': case 'standup': return entityHash(a.entity_type, a.entity_id, a.entity_key);
    case 'build': return '#/builds';
    case 'test_run': return '#/pruebas';
    case 'store': return '#/tiendas';
    case 'goal': return '#/semana';
    case 'date': return '#/calendario';
    case 'absence': return '#/equipo';
    case 'channel': return '#/chat';
    case 'project': return can('proyectos.gestionar') ? '#/admin/proyectos' : null;
    case 'user': case 'role': return can('sistema.usuarios') ? `#/admin/${a.entity_type === 'role' ? 'roles' : 'usuarios'}` : null;
    case 'backup': return can('sistema.copias') ? '#/ajustes/copias' : null;
    case 'settings': return can('sistema.ajustes') ? '#/ajustes' : null;
    case 'vault': return can('boveda.ver') ? '#/boveda' : null;
    default: return null;
  }
}

export default async function render(ctx) {
  const { root } = ctx;
  const allUsers = can('actividad.ver_todos');
  const f = { user_id: ctx.query.get('user_id') || '', entity_type: ctx.query.get('tipo') || '', from: '', to: '', q: '' };
  let items = [], hasMore = false, token = 0;

  const person = allUsers ? selectEl([['', 'Todo el equipo'], ...[...store.users].sort((a, b) => a.display_name.localeCompare(b.display_name)).map(u => [String(u.id), u.display_name])], f.user_id,
    { onchange: () => { f.user_id = person.value; load(); } }) : null;
  const type = selectEl([['', 'Todo'], ...Object.entries(TYPES)], f.entity_type, { onchange: () => { f.entity_type = type.value; load(); } });
  const from = h('input', { type: 'date', onchange: () => { f.from = from.value; load(); } });
  const to = h('input', { type: 'date', onchange: () => { f.to = to.value; load(); } });
  const q = h('input', { placeholder: 'Buscar en la actividad o por clave (APP-012)…' });
  q.addEventListener('input', debounce(() => { f.q = q.value.trim(); load(); }, 300));
  const clearBtn = h('button', { class: 'btn sm ghost', onclick: () => {
    Object.assign(f, { user_id: '', entity_type: '', from: '', to: '', q: '' });
    if (person) person.value = ''; type.value = ''; from.value = ''; to.value = ''; q.value = ''; load();
  } }, 'Quitar filtros');
  const list = h('div');
  const more = h('div', { class: 'row end mt' });

  const params = (extra = {}) => {
    const p = { entity_type: f.entity_type, q: f.q, from: f.from, to: f.to ? `${f.to}T23:59:59.999Z` : '', ...extra };
    if (allUsers && f.user_id) p.user_id = f.user_id;
    return withProject(p);
  };
  const csv = h('button', { class: 'btn', onclick: () => busy(csv, () => api.save(`/api/export/activity.csv?${params()}`, { folder: 'exportaciones' }))
    .then(r => toast(`Actividad guardada en ${r.file || 'Exportaciones'}`, 'ok')).catch(() => {}) }, icon('download'), 'Exportar CSV');

  mount(root,
    pageHead('Actividad', can('exportar') ? csv : null),
    !allUsers ? h('div', { class: 'callout info small mb' }, icon('eye', 14), ' Solo ves tu propia actividad. Para ver la de todo el equipo hace falta el permiso «Ver la actividad de todo el equipo», que da un administrador.') : null,
    h('div', { class: 'filters' },
      h('div', { class: 'toolbar adm-tb0 grow' }, h('div', { class: 'search' }, icon('search'), q)),
      person ? h('div', { class: 'group' }, h('span', null, 'Persona'), person) : null,
      h('div', { class: 'group' }, h('span', null, 'Tipo'), type),
      h('div', { class: 'group' }, h('span', null, 'Desde'), from, h('span', null, 'hasta'), to),
      clearBtn),
    list, more);

  async function load(append = false) {
    const my = ++token;
    if (!append) { if (!items.length) mount(list, loading()); else list.classList.add('adm-dim'); }
    try {
      const r = await api.get(`/api/activity?${params({ limit: PAGE, offset: append ? items.length : 0 })}`);
      if (my !== token || !ctx.alive()) return;
      items = append ? items.concat(r.items) : r.items;
      hasMore = r.has_more;
      list.classList.remove('adm-dim');
      draw();
    } catch (err) {
      list.classList.remove('adm-dim');
      if (append) toast(err.message, 'error'); else mount(list, errorBox(err, () => load()));
    }
  }

  function draw() {
    const filtered = f.user_id || f.entity_type || f.from || f.to || f.q;
    if (!items.length) {
      mount(list, emptyState(filtered ? 'Nada coincide con los filtros' : 'Aún no hay actividad', filtered ? 'Prueba a quitar algún filtro.' : allUsers ? 'Aquí aparecerá todo lo que se haga: incidencias, tareas, cambios de ajustes…' : 'Aquí aparecerá lo que hagas: incidencias, tareas, comentarios…'));
      mount(more);
      return;
    }
    const groups = [];
    for (const a of items) {
      const day = new Date(a.created_at).toDateString();
      if (!groups.length || groups[groups.length - 1].day !== day) groups.push({ day, label: fmtDayLabel(a.created_at), rows: [] });
      groups[groups.length - 1].rows.push(a);
    }
    mount(list, groups.map(g => h('div', { class: 'mb' },
      h('div', { class: 'section-title' }, g.label),
      h('div', { class: 'list-card' }, g.rows.map(a => {
        const u = userById(a.user_id);
        const link = linkFor(a);
        return h('div', { class: 'list-item adm-act' },
          h('span', { class: 'tiny muted adm-time' }, fmtTime(a.created_at)),
          u ? avatar(u) : h('span', { class: 'avatar', title: a.user_name }, a.user_id ? '?' : icon('settings', 14)),
          h('div', { class: 'grow' },
            h('div', { class: 'adm-act-text' }, allUsers ? h('b', null, `${a.user_name} `) : null, richText(a.summary)),
            h('div', { class: 'tiny muted' }, TYPES[a.entity_type] || a.entity_type)),
          link ? h('a', { class: 'btn sm ghost', href: link, title: 'Abrir' }, a.entity_key || 'Abrir', icon('right', 14)) : null);
      })))));
    mount(more, hasMore ? h('button', { class: 'btn', onclick: (e) => busy(e.currentTarget, () => load(true)) }, 'Cargar más') :
      h('span', { class: 'tiny muted' }, `${items.length} ${items.length === 1 ? 'registro' : 'registros'}`));
  }

  await load();
  // Al llegar actividad nueva se recarga lo ya cargado (sin perder los filtros).
  ctx.onChanges(['activity'], async () => {
    if (items.length > PAGE) {
      try {
        const r = await api.get(`/api/activity?${params({ limit: Math.min(500, items.length), offset: 0 })}`);
        if (!ctx.alive()) return;
        items = r.items; hasMore = r.has_more; draw();
      } catch { /* sin conexión */ }
    } else load();
  });
}

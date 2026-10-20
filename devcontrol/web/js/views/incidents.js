// Lista de incidencias: búsqueda, filtros, ordenación, vistas guardadas y exportación.
import { api } from '../api.js';
import { CATEGORIES, INCIDENT_STATUSES, PRIORITIES } from '../labels.js';
import { can, projectById, store, userById, withProject } from '../state.js';
import {
  avatar, busy, debounce, emptyState, errorBox, fmtDate, fmtDateTime, fmtRelative, h, icon, loadJSON, loading, menuButton, modal,
  mount, pageHead, priorityBadge, saveJSON, statusBadge, table, toast, field,
} from '../ui.js';
import { dueText } from './shared.js';

const VIEW_KEY = 'dc.incidents.view';
const SAVED_KEY = 'dc.incidents.saved';
const FILTER_KEYS = ['q', 'status', 'priority', 'category', 'assignee', 'open', 'blocked', 'overdue', 'to_verify', 'seen',
  'label', 'created_from', 'created_to', 'target_from', 'target_to', 'sort'];
const DEFAULTS = { open: '1', sort: 'priority' };
const SORTS = [['priority', 'Prioridad'], ['updated', 'Últimos cambios'], ['created_desc', 'Más recientes'], ['created_asc', 'Más antiguas'], ['due', 'Fecha objetivo']];

function fromQuery(query) {
  const f = {};
  for (const k of FILTER_KEYS) if (query.get(k)) f[k] = query.get(k);
  return f;
}

// Parámetros para el servidor (las fechas «hasta» incluyen todo el día).
function serverParams(f) {
  const p = {};
  for (const k of FILTER_KEYS) if (f[k]) p[k] = f[k];
  if (p.created_to) p.created_to = `${p.created_to}T23:59:59.999Z`;
  return p;
}

const slaBadge = (s) => {
  if (!s) return h('span', { class: 'muted tiny' }, '—');
  const due = s.response_due && !s.response_breached && s.state !== 'vencido' ? s.response_due : s.resolve_due;
  const text = s.state === 'vencido' ? 'Vencido' : s.state === 'riesgo' ? 'En riesgo' : 'En plazo';
  return h('span', { class: `sla-badge ${s.state}`, title: [s.response_due ? `Primera respuesta antes de ${fmtDateTime(s.response_due)}` : '',
    s.resolve_due ? `Resolver antes de ${fmtDateTime(s.resolve_due)}` : ''].filter(Boolean).join('\n') }, text,
  due ? h('div', { class: 'tiny muted' }, dueText(due)) : null);
};


export function whoCell(id, name) {
  if (!id) return h('span', { class: 'muted small' }, 'Sin asignar');
  const u = userById(id);
  return h('span', { class: 'who' }, avatar(u || { display_name: name }), h('span', { class: 'ellipsis' }, name || u?.display_name || `#${id}`));
}

export default async function render(ctx) {
  const { root } = ctx;
  const hasQuery = FILTER_KEYS.some(k => ctx.query.has(k));
  let f = hasQuery ? { sort: 'priority', ...fromQuery(ctx.query) } : { ...loadJSON(VIEW_KEY, DEFAULTS) };
  let items = [];
  let reqToken = 0;

  const search = h('input', { type: 'search', placeholder: 'Buscar por clave, título, descripción o etiqueta…', value: f.q || '' });
  const filtersBox = h('div', { class: 'filters ws-filters' });
  const countBox = h('div', { class: 'ws-count' });
  const listBox = h('div');

  const persist = () => {
    saveJSON(VIEW_KEY, f);
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    history.replaceState(null, '', `#/incidencias${qs ? `?${qs}` : ''}`);
  };
  const set = (patch) => {
    f = { ...f, ...patch };
    for (const k of Object.keys(f)) if (!f[k]) delete f[k];
    persist(); drawFilters(); load();
  };
  const toggleList = (key, value) => {
    const cur = (f[key] || '').split(',').filter(Boolean);
    const next = cur.includes(value) ? cur.filter(x => x !== value) : [...cur, value];
    const patch = { [key]: next.join(',') };
    if (key === 'status' && next.length) patch.open = '';
    set(patch);
  };

  function chip(label, on, onclick, title) { return h('button', { type: 'button', class: 'chip' + (on ? ' on' : ''), onclick, title }, label); }

  function drawFilters() {
    const status = (f.status || '').split(',');
    const prio = (f.priority || '').split(',');
    const assigneeSel = h('select', { title: 'Responsable', onchange: (e) => set({ assignee: e.target.value }) },
      h('option', { value: '' }, 'Cualquiera'), h('option', { value: 'me' }, 'Asignadas a mí'), h('option', { value: 'none' }, 'Sin asignar'),
      store.users.map(u => h('option', { value: u.id }, u.display_name)));
    assigneeSel.value = f.assignee || '';
    const catSel = h('select', { title: 'Categoría', onchange: (e) => set({ category: e.target.value }) },
      h('option', { value: '' }, 'Todas'), Object.entries(CATEGORIES).map(([k, v]) => h('option', { value: k }, v)));
    catSel.value = f.category && !f.category.includes(',') ? f.category : '';
    const labels = [...new Set(items.flatMap(i => i.labels_list || []))].sort();
    const labelInput = h('input', { class: 'label-input', list: 'inc-labels', placeholder: 'Etiqueta', value: f.label || '',
      onchange: (e) => set({ label: e.target.value.trim().toLowerCase() }) });
    const date = (key, title) => h('input', { type: 'date', value: f[key] || '', title, onchange: (e) => set({ [key]: e.target.value }) });
    const active = FILTER_KEYS.filter(k => k !== 'sort' && k !== 'q' && f[k]).length;
    mount(filtersBox,
      h('div', { class: 'frow' },
        h('div', { class: 'group' }, h('span', null, 'Estado'), h('div', { class: 'chips' },
          chip('Abiertas', f.open === '1', () => set({ open: f.open === '1' ? '' : '1', status: '' }), 'Pendientes, en progreso, bloqueadas y en verificación'),
          Object.entries(INCIDENT_STATUSES).map(([k, v]) => chip(v, status.includes(k), () => toggleList('status', k)))))),
      h('div', { class: 'frow' },
        h('div', { class: 'group' }, h('span', null, 'Prioridad'), h('div', { class: 'chips' },
          Object.entries(PRIORITIES).map(([k, v]) => chip(h('span', { class: 'row', style: { gap: '5px' } }, h('span', { class: `prio-dot ${k}` }), v), prio.includes(k), () => toggleList('priority', k))))),
        h('div', { class: 'group' }, h('span', null, 'Categoría'), catSel),
        h('div', { class: 'group' }, h('span', null, 'Responsable'), assigneeSel)),
      h('div', { class: 'frow' },
        h('div', { class: 'chips' },
          chip(h('span', { class: 'row', style: { gap: '4px' } }, icon('verify', 13), 'Por verificar'), f.to_verify === '1', () => set({ to_verify: f.to_verify === '1' ? '' : '1' }),
            'En verificación y pendientes de que tú (o alguien con permiso) las verifique'),
          chip(h('span', { class: 'row', style: { gap: '4px' } }, icon('lock', 13), 'Bloqueadas'), f.blocked === '1', () => set({ blocked: f.blocked === '1' ? '' : '1' })),
          chip('Vencidas', f.overdue === '1', () => set({ overdue: f.overdue === '1' ? '' : '1' }), 'Abiertas con la fecha objetivo pasada'),
          chip('No vistas', f.seen === '0', () => set({ seen: f.seen === '0' ? '' : '0' }), 'Nadie salvo quien la creó la ha abierto todavía')),
        h('div', { class: 'group' }, h('span', null, 'Etiqueta'), labelInput, h('datalist', { id: 'inc-labels' }, labels.map(l => h('option', { value: l })))),
        h('div', { class: 'group' }, h('span', null, 'Creada'), date('created_from', 'Creada desde'), h('span', null, '–'), date('created_to', 'Creada hasta')),
        h('div', { class: 'group' }, h('span', null, 'Objetivo'), date('target_from', 'Fecha objetivo desde'), h('span', null, '–'), date('target_to', 'Fecha objetivo hasta')),
        active ? h('button', { class: 'btn ghost sm', onclick: () => { search.value = ''; set(Object.fromEntries(FILTER_KEYS.filter(k => k !== 'sort').map(k => [k, '']))); } }, icon('x'), 'Quitar filtros') : null));
  }

  const showAll = () => store.project === 'all' && store.projects.length > 1;

  function drawList() {
    const cols = [
      { label: 'Clave', render: (i) => h('span', { class: 'key' }, h('span', { class: 'project-dot', style: { background: i.project_color || 'var(--muted)' } }), i.key) },
      showAll() && { label: 'Proyecto', cls: 'hide-sm', render: (i) => h('span', { class: 'small muted nowrap' }, i.project_name || projectById(i.project_id)?.name || '') },
      { label: 'Título', cls: 'title-cell', render: (i) => h('div', null,
        h('span', null, i.title), i.labels_list?.length ? h('span', { class: 'lbls' }, i.labels_list.map(l => h('span', { class: 'lbl-chip' }, l))) : null,
        h('div', { class: 'sub' }, `${i.category_label} · ${i.created_by_name || ''} · ${fmtRelative(i.created_at)}`)) },
      { label: 'Prioridad', render: (i) => priorityBadge(i.priority, i.priority_label) },
      { label: 'Estado', render: (i) => h('div', null, statusBadge(i.status, i.status_label),
        i.status === 'bloqueada' && i.block_reason_label ? h('div', { class: 'block-note ellipsis', title: i.block_reason_text || '' }, i.block_reason_label) : null) },
      { label: 'Responsable', render: (i) => whoCell(i.assignee_id, i.assignee_name) },
      { label: 'SLA', cls: 'hide-sm', render: (i) => slaBadge(i.sla) },
      { label: 'Objetivo', render: (i) => i.target_date ? h('span', { class: 'nowrap ' + (i.overdue ? 'overdue' : 'small') }, fmtDate(i.target_date)) : h('span', { class: 'muted' }, '—') },
      { label: 'Visto', cls: 'hide-sm', render: (i) => i.seen_at
        ? h('span', { class: 'seen yes', title: `Vista por primera vez por ${i.seen_by_name} el ${fmtDateTime(i.seen_at)}${i.assignee_id && !i.assignee_seen ? '\nEl responsable aún no la ha abierto' : ''}` }, icon('eye'), i.assignee_id && !i.assignee_seen ? h('span', { class: 'tiny warn-text' }, '·') : null)
        : h('span', { class: 'seen no', title: 'Nadie salvo quien la creó la ha abierto todavía' }, icon('eyeoff')) },
      { label: '', render: (i) => h('span', { class: 'counts' },
        i.comments_count ? h('span', { title: `${i.comments_count} comentarios` }, icon('chat'), i.comments_count) : null,
        i.attachments_count ? h('span', { title: `${i.attachments_count} adjuntos` }, icon('clip'), i.attachments_count) : null) },
    ].filter(Boolean);
    const filtered = FILTER_KEYS.some(k => k !== 'sort' && f[k]);
    mount(countBox, `${items.length} ${items.length === 1 ? 'incidencia' : 'incidencias'}`);
    if (!items.length) {
      mount(listBox, emptyState(filtered ? 'Ninguna incidencia coincide con los filtros' : 'No hay incidencias',
        filtered ? 'Prueba a quitar algún filtro.' : (can('incidencias.crear') ? 'Crea la primera con «Nueva incidencia».' : '')));
      return;
    }
    const t = table(cols, items, {
      onRow: (i) => { location.hash = `#/incidencias/${i.key}`; },
      rowClass: (i) => [i.status === 'bloqueada' ? 'is-blocked' : '', i.priority === 'critica' && !['resuelta', 'duplicada'].includes(i.status) ? 'is-critical' : '',
        ['resuelta', 'duplicada'].includes(i.status) ? 'is-resolved' : ''].join(' '),
    });
    t.querySelector('table')?.classList.add('inc-table');
    mount(listBox, t);
  }

  async function load() {
    const token = ++reqToken;
    if (!items.length) mount(listBox, loading());
    try {
      const r = await api.get(`/api/incidents?${withProject(serverParams(f))}`);
      if (token !== reqToken || !ctx.alive()) return;
      items = r.items;
      drawList();
      if (document.activeElement?.closest?.('.ws-filters') == null) drawFilters();
    } catch (err) {
      if (token !== reqToken) return;
      mount(listBox, errorBox(err, load));
    }
  }

  search.addEventListener('input', debounce(() => { f.q = search.value.trim(); if (!f.q) delete f.q; persist(); load(); }, 250));
  const sortSel = h('select', { title: 'Ordenar por', style: { width: 'auto' }, onchange: (e) => set({ sort: e.target.value }) },
    SORTS.map(([v, l]) => h('option', { value: v }, `Ordenar: ${l}`)));
  sortSel.value = f.sort || 'priority';

  // ---------- vistas guardadas ----------
  function savedMenu() {
    const saved = loadJSON(SAVED_KEY, []);
    return menuButton(h('span', { class: 'row', style: { gap: '6px' } }, icon('flag', 14), 'Vistas'), [
      ...saved.map(v => ({ label: v.name, icon: 'eye', onClick: () => { f = { ...v.filters }; search.value = f.q || ''; sortSel.value = f.sort || 'priority'; persist(); drawFilters(); load(); } })),
      { label: 'Guardar la vista actual…', icon: 'plus', onClick: saveView },
      saved.length ? { label: 'Borrar una vista…', icon: 'trash', danger: true, onClick: deleteView } : null,
    ].filter(Boolean));
  }
  function saveView() {
    const name = h('input', { placeholder: 'Ej.: Críticas de iOS sin asignar' });
    modal({ title: 'Guardar vista', body: field('Nombre de la vista', name, { required: true, hint: 'Se guarda en este equipo con los filtros y el orden actuales.' }),
      actions: [{ label: 'Cancelar' }, { label: 'Guardar', kind: 'primary', onClick: () => {
        const n = name.value.trim();
        if (!n) { toast('Escribe un nombre.', 'error'); return false; }
        const saved = loadJSON(SAVED_KEY, []).filter(v => v.name !== n);
        saved.push({ name: n, filters: { ...f } });
        saveJSON(SAVED_KEY, saved);
        mount(viewsSlot, savedMenu());
        toast(`Vista «${n}» guardada.`, 'ok');
      } }] });
  }
  function deleteView() {
    const saved = loadJSON(SAVED_KEY, []);
    const sel = h('select', null, saved.map(v => h('option', { value: v.name }, v.name)));
    modal({ title: 'Borrar vista guardada', body: field('Vista', sel), actions: [{ label: 'Cancelar' }, { label: 'Borrar', kind: 'danger', onClick: () => {
      saveJSON(SAVED_KEY, saved.filter(v => v.name !== sel.value));
      mount(viewsSlot, savedMenu());
      toast('Vista borrada.', 'ok');
    } }] });
  }
  const viewsSlot = h('span', null, savedMenu());

  const exportBtn = can('exportar') ? h('button', { class: 'btn', title: 'Exportar las incidencias filtradas a CSV (Excel)' }, icon('download'), 'Exportar CSV') : null;
  exportBtn?.addEventListener('click', () => busy(exportBtn, async () => {
    const r = await api.save(`/api/export/incidents.csv?${withProject(serverParams(f))}`, { folder: 'exportaciones' });
    toast(`Exportado a Exportaciones: ${r.name || 'incidencias.csv'}`, 'ok');
  }).catch(() => {}));

  mount(root,
    pageHead('Incidencias', viewsSlot, exportBtn,
      can('incidencias.crear') ? h('a', { class: 'btn primary', href: '#/incidencias/nueva' }, icon('plus'), 'Nueva incidencia') : null),
    h('div', { class: 'toolbar' }, h('div', { class: 'search' }, icon('search'), search), sortSel),
    filtersBox, countBox, listBox);
  drawFilters();
  persist();
  await load();
  ctx.onChanges(['incident', 'user', 'project'], load);
}

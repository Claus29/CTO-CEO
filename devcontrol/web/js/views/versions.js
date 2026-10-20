// Versiones: lista con estado y fechas, ficha con tareas, incidencias, builds y notas de la versión.
import { api } from '../api.js';
import { can, withProject } from '../state.js';
import { PLATFORMS, TASK_STATUSES, VERSION_STATUSES, toOptions } from '../labels.js';
import {
  busy, emptyState, errorBox, field, fmtDate, h, icon, loading, menuButton, modal, mount, pageHead, priorityBadge,
  selectEl, statusBadge, table, tabs, toast, todayISO, uid,
} from '../ui.js';
import { bar, copyText, daysBetween, kindBadge, multiProject, newProjectId, projectField, requireFields, submit } from './_plan_shared.js';

const FILTERS = [['activas', 'En marcha'], ['publicada', 'Publicadas'], ['all', 'Todas']];

export default async function render(ctx) {
  const { root } = ctx;
  const canEdit = can('planificacion.editar');
  let versions = null;
  let selected = Number(ctx.query.get('id')) || null;
  let filter = 'activas';
  let tab = ctx.query.get('tab') || 'tareas';
  const listBox = h('div', { class: 'col' }, loading());
  const detailBox = h('div', { class: 'version-detail' });
  const filterSeg = h('div', { class: 'seg' });

  async function load() {
    try {
      const r = await api.get(`/api/versions?${withProject()}`);
      if (!ctx.alive()) return;
      versions = r.items;
    } catch (err) { mount(listBox, errorBox(err, load)); return; }
    if (!selected || !versions.some(v => v.id === selected)) {
      const first = versions.find(v => v.status !== 'publicada') || versions[0];
      selected = first ? first.id : null;
    }
    const sel = versions.find(v => v.id === selected);
    if (sel && filter === 'activas' && sel.status === 'publicada') filter = 'all';
    drawList();
    drawDetail();
  }

  function select(id) {
    selected = id;
    history.replaceState(null, '', `#/versiones?id=${id}`);
    drawList();
    drawDetail();
  }

  function drawList() {
    mount(filterSeg, FILTERS.map(([k, label]) => h('button', { class: filter === k ? 'on' : '', onclick: () => { filter = k; drawList(); } }, label)));
    if (!versions.length) {
      mount(listBox, emptyState('Aún no hay versiones', canEdit ? 'Crea la primera con «Nueva versión» para agrupar tareas, ideas y builds.' : 'Quien gestiona la planificación creará las versiones.'));
      return;
    }
    const list = versions.filter(v => filter === 'all' || (filter === 'publicada' ? v.status === 'publicada' : v.status !== 'publicada'));
    if (!list.length) { mount(listBox, h('div', { class: 'empty small' }, 'No hay versiones en este grupo.')); return; }
    mount(listBox, list.map(versionCard));
  }

  function dateInfo(v) {
    if (v.status === 'publicada') return h('span', { class: 'tiny muted' }, v.target_date ? `Prevista ${fmtDate(v.target_date)}` : 'Publicada');
    if (!v.target_date) return h('span', { class: 'tiny muted' }, 'Sin fecha prevista');
    const days = daysBetween(todayISO(), v.target_date);
    const txt = days < 0 ? `Retrasada ${-days} d` : days === 0 ? 'Prevista hoy' : `Faltan ${days} d`;
    return h('span', { class: 'tiny ' + (days < 0 ? 'overdue' : days <= 7 ? 'warn-text' : 'muted') }, `${fmtDate(v.target_date)} · ${txt}`);
  }

  function versionCard(v) {
    const pct = v.tasks_total ? Math.round(100 * v.tasks_done / v.tasks_total) : 0;
    return h('button', { class: 'card version-card' + (v.id === selected ? ' on' : ''), type: 'button', onclick: () => select(v.id) },
      h('div', { class: 'row' },
        h('b', { class: 'grow ellipsis' }, v.name),
        multiProject() ? h('span', { class: 'tiny muted' }, v.project_key) : null,
        kindBadge('version', v.status, v.status_label)),
      h('div', { class: 'row between' }, dateInfo(v), h('span', { class: 'tiny muted' }, `${v.tasks_done}/${v.tasks_total} tareas`)),
      bar(pct));
  }

  function drawDetail() {
    const v = versions?.find(x => x.id === selected);
    if (!v) { mount(detailBox, versions?.length ? h('div', { class: 'card empty' }, 'Elige una versión de la lista.') : null); return; }
    const pct = v.tasks_total ? Math.round(100 * v.tasks_done / v.tasks_total) : 0;
    const actions = canEdit ? h('div', { class: 'actions' },
      h('button', { class: 'btn sm', onclick: () => versionForm(v) }, icon('edit'), 'Editar'),
      menuButton('Cambiar estado', Object.entries(VERSION_STATUSES).filter(([k]) => k !== v.status).map(([k, label]) => ({
        label: `Marcar como «${label}»`, onClick: () => setStatus(v, k),
      })))) : null;
    const tabBox = h('div');
    const items = [
      { key: 'tareas', label: 'Tareas', count: v.tasks_total, icon: 'task' },
      can('incidencias.ver') ? { key: 'incidencias', label: 'Incidencias', icon: 'bug' } : null,
      { key: 'builds', label: 'Builds', count: v.builds.length, icon: 'box' },
      { key: 'notas', label: 'Notas de la versión', icon: 'file' },
    ].filter(Boolean);
    if (!items.some(i => i.key === tab)) tab = 'tareas';
    const tabBar = h('div');
    const drawTabs = () => mount(tabBar, tabs(items, tab, (k) => { tab = k; drawTabs(); drawTab(v, tabBox); }));
    drawTabs();
    mount(detailBox,
      h('div', { class: 'card' },
        h('div', { class: 'detail-head' },
          h('div', null,
            h('div', { class: 'key' }, multiProject() ? `${v.project_key} · ${v.project_name}` : 'Versión'),
            h('h1', null, v.name)),
          kindBadge('version', v.status, v.status_label), actions),
        h('div', { class: 'grid cols-2' },
          h('div', { class: 'kv' },
            h('span', null, 'Inicio'), h('span', null, fmtDate(v.start_date)),
            h('span', null, 'Fecha prevista'), h('span', null, v.target_date && v.status !== 'publicada' ? dateInfo(v) : fmtDate(v.target_date)),
            h('span', null, 'Ideas'), h('span', null, v.ideas_count ? h('a', { href: '#/ideas' }, `${v.ideas_count} ideas asociadas`) : 'Ninguna'),
            h('span', null, 'Incidencias corregidas'), h('span', null, v.fixed_incidents)),
          h('div', { class: 'col' },
            h('div', { class: 'row between small' }, h('span', { class: 'muted' }, 'Progreso de las tareas'), h('b', null, `${v.tasks_done} de ${v.tasks_total} (${pct}%)`)),
            bar(pct),
            h('div', { class: 'row wrap tiny muted' },
              ['pendiente', 'en_progreso', 'bloqueada', 'en_verificacion'].filter(s => v.task_counts[s]).map(s =>
                h('span', null, statusBadge(s, TASK_STATUSES[s]), ' ', v.task_counts[s]))))),
        v.description ? h('div', { class: 'text-block mt' }, v.description) : null),
      h('div', { class: 'mt' }, tabBar, tabBox));
    drawTab(v, tabBox);
  }

  async function drawTab(v, box) {
    mount(box, loading());
    try {
      if (tab === 'tareas') {
        const tasks = (await api.get(`/api/tasks?version_id=${v.id}`)).items;
        if (!ctx.alive()) return;
        mount(box, table([
          { label: 'Clave', render: t => h('b', { class: 'nowrap' }, t.key) },
          { label: 'Título', render: t => t.title, cls: 'title-cell' },
          { label: 'Responsable', render: t => t.assignee_name || '—', cls: 'hide-sm' },
          { label: 'Progreso', render: t => h('div', { class: 'mini-progress' }, bar(t.status === 'resuelto' ? 100 : t.progress)) },
          { label: 'Prioridad', render: t => priorityBadge(t.priority, t.priority_label) },
          { label: 'Estado', render: t => statusBadge(t.status, t.status_label) },
        ], tasks, { onRow: t => { location.hash = `#/tareas/${t.key}`; }, rowClass: t => t.status === 'resuelto' ? 'is-resolved' : '',
          empty: 'No hay tareas en esta versión. Asigna la versión desde la ficha de cada tarea.' }));
      } else if (tab === 'incidencias') {
        const incs = (await api.get(`/api/incidents?version_id=${v.id}`)).items;
        if (!ctx.alive()) return;
        const builds = Object.fromEntries(v.builds.map(b => [b.id, b]));
        const bl = (id) => builds[id] ? `${builds[id].number} (${PLATFORMS[builds[id].platform] || builds[id].platform})` : '';
        mount(box, h('p', { class: 'small muted' }, 'Incidencias encontradas o corregidas en alguna build de esta versión.'),
          table([
            { label: 'Clave', render: i => h('b', { class: 'nowrap' }, i.key) },
            { label: 'Título', render: i => i.title, cls: 'title-cell' },
            { label: 'Build', render: i => h('div', { class: 'tiny' },
              builds[i.found_build_id] ? h('div', null, 'Encontrada en ', bl(i.found_build_id)) : null,
              builds[i.fixed_build_id] ? h('div', { class: 'ok-text' }, 'Corregida en ', bl(i.fixed_build_id)) : null) },
            { label: 'Prioridad', render: i => priorityBadge(i.priority, i.priority_label) },
            { label: 'Estado', render: i => statusBadge(i.status, i.status_label) },
          ], incs, { onRow: i => { location.hash = `#/incidencias/${i.key}`; }, rowClass: i => ['resuelta', 'duplicada'].includes(i.status) ? 'is-resolved' : '',
            empty: 'Ninguna incidencia vinculada a builds de esta versión.' }));
      } else if (tab === 'builds') {
        mount(box, table([
          { label: 'Build', render: b => h('b', null, b.number) },
          { label: 'Plataforma', render: b => PLATFORMS[b.platform] || b.platform },
          { label: 'Canal', render: b => kindBadge('channel', b.channel) },
          { label: 'Fecha', render: b => fmtDate(b.built_on) },
        ], v.builds, { onRow: () => { location.hash = `#/builds?version=${v.id}`; },
          empty: 'Sin builds registradas para esta versión.' }),
        can('calidad.ver') ? h('div', { class: 'row end mt' }, h('a', { class: 'btn sm', href: `#/builds?version=${v.id}` }, icon('box'), 'Ir a builds')) : null);
      } else {
        await drawNotes(v, box);
      }
    } catch (err) { if (ctx.alive()) mount(box, errorBox(err, () => drawTab(v, box))); }
  }

  async function drawNotes(v, box) {
    const r = await api.get(`/api/versions/${v.id}/release-notes`);
    if (!ctx.alive()) return;
    const saveBtn = canEdit ? h('button', { class: 'btn sm', title: 'Guarda este texto como las notas oficiales de la versión' }, icon('check'), 'Guardar como notas oficiales') : null;
    saveBtn?.addEventListener('click', () => busy(saveBtn, () => api.patch(`/api/versions/${v.id}`, { release_notes: r.markdown, version: v.version }), 'Notas guardadas.')
      .then(load).catch((err) => { if (err.status === 409) load(); }));
    mount(box,
      h('div', { class: 'card' },
        h('div', { class: 'card-head' }, h('h3', null, 'Notas generadas'),
          h('button', { class: 'btn sm primary', onclick: () => copyText(r.markdown) }, icon('copy'), 'Copiar'), saveBtn),
        h('p', { class: 'small muted' }, 'Se generan solas con lo que está resuelto y verificado en la versión: ideas convertidas en tareas terminadas, tareas resueltas e incidencias corregidas en sus builds.'),
        h('pre', { class: 'text-block release-notes' }, r.markdown)),
      h('div', { class: 'card mt' },
        h('div', { class: 'card-head' }, h('h3', null, 'Notas oficiales guardadas'),
          r.saved ? h('button', { class: 'btn sm', onclick: () => copyText(r.saved) }, icon('copy'), 'Copiar') : null,
          canEdit ? h('button', { class: 'btn sm', onclick: () => editNotes(v, r.saved || r.markdown) }, icon('edit'), r.saved ? 'Editar' : 'Escribir') : null),
        r.saved ? h('pre', { class: 'text-block release-notes' }, r.saved)
          : h('div', { class: 'empty small' }, 'Todavía no se han guardado notas oficiales. Puedes partir de las generadas.')));
  }

  function editNotes(v, text) {
    const ta = h('textarea', { rows: 16, class: 'mono' });
    ta.value = text;
    modal({ title: `Notas de la versión ${v.name}`, wide: true, body: field('Texto (admite formato Markdown)', ta),
      actions: [{ label: 'Cancelar' }, { label: 'Guardar', kind: 'primary',
        onClick: () => submit(() => api.patch(`/api/versions/${v.id}`, { release_notes: ta.value, version: v.version }), 'Notas guardadas.', load) }] });
  }

  async function setStatus(v, status) {
    try {
      await api.patch(`/api/versions/${v.id}`, { status, version: v.version });
      toast(`Versión ${v.name}: ${VERSION_STATUSES[status]}.`, 'ok');
    } catch (err) { toast(err.message, err.status === 409 ? 'warn' : 'error'); }
    load();
  }

  function versionForm(v) {
    const editing = !!v;
    const name = h('input', { maxlength: 80, placeholder: 'Ej.: 2.4.0', value: v?.name || '' });
    let projectId = v?.project_id || newProjectId();
    const proj = editing ? null : projectField(projectId, (x) => { projectId = Number(x); });
    const status = selectEl(toOptions(VERSION_STATUSES), v?.status || 'planificada');
    const start = h('input', { type: 'date', value: v?.start_date || '' });
    const target = h('input', { type: 'date', value: v?.target_date || '' });
    const desc = h('textarea', { rows: 4, placeholder: 'Objetivo de la versión, alcance, notas para el equipo…' });
    desc.value = v?.description || '';
    modal({
      title: editing ? `Editar versión ${v.name}` : 'Nueva versión', wide: true,
      body: h('div', { class: 'form-grid' },
        field('Nombre o número de versión', name, { required: true }), proj ? proj.el : field('Estado', status),
        proj ? field('Estado', status) : null,
        field('Fecha de inicio', start), field('Fecha prevista de publicación', target, { hint: 'Se usa en el roadmap para calcular el riesgo.' }),
        field('Descripción', desc, { cls: 'span2' })),
      actions: [{ label: 'Cancelar' }, { label: editing ? 'Guardar cambios' : 'Crear versión', kind: 'primary', onClick: () => {
        if (!requireFields([[name, 'el nombre de la versión']])) return false;
        if (start.value && target.value && target.value < start.value) { toast('La fecha prevista no puede ser anterior a la de inicio.', 'error'); target.focus(); return false; }
        const data = { name: name.value.trim(), status: status.value, start_date: start.value || null, target_date: target.value || null, description: desc.value.trim() };
        if (editing) {
          const changed = {};
          for (const [k, val] of Object.entries(data)) if ((v[k] ?? null) !== val && !(k === 'description' && (v[k] || '') === val)) changed[k] = val;
          if (!Object.keys(changed).length) return true;
          return submit(() => api.patch(`/api/versions/${v.id}`, { ...changed, version: v.version }), 'Versión actualizada.', load);
        }
        return submit(async () => {
          const r = await api.post('/api/versions', { ...data, uid: uid(), project_id: projectId }, { queue: true });
          if (r && r.id) { selected = r.id; filter = data.status === 'publicada' ? 'all' : filter; }
          return r;
        }, `Versión ${data.name} creada.`, load);
      } }],
    });
  }

  mount(root,
    pageHead('Versiones', canEdit ? h('button', { class: 'btn primary', onclick: () => versionForm(null) }, icon('plus'), 'Nueva versión') : null),
    h('div', { class: 'plan-split' },
      h('div', { class: 'col' }, h('div', { class: 'row' }, filterSeg), listBox),
      detailBox));
  await load();
  ctx.onChanges(['version', 'task', 'idea', 'build', 'incident'], load);
}

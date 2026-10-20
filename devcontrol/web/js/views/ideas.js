// Ideas: propuestas del equipo con votos, estados, edición y conversión en tarea.
import { api } from '../api.js';
import { can, store, withProject } from '../state.js';
import { IDEA_STATUSES, PRIORITIES, toOptions } from '../labels.js';
import {
  bindDraft, busy, clearDraft, confirmDialog, debounce, emptyState, errorBox, field, fmtDate, fmtRelative, h, icon,
  loadJSON, loading, menuButton, modal, mount, pageHead, priorityBadge, richText, saveJSON, selectEl, toast, uid,
} from '../ui.js';
import { kindBadge, loadVersions, newProjectId, projectField, projectKey, multiProject, requireFields, submit, versionOptions } from './_plan_shared.js';

const PREF = 'dc.ideas.view';
const PRIO = { critica: 0, alta: 1, media: 2, baja: 3 };
const SORTS = [['votes', 'Más votadas'], ['recent', 'Más recientes'], ['priority', 'Prioridad']];
const STATUS_FILTERS = [['activas', 'Activas'], ...Object.entries(IDEA_STATUSES), ['all', 'Todas']];

export default async function render(ctx) {
  const { root } = ctx;
  const prefs = { sort: 'votes', status: 'activas', priority: '', ...loadJSON(PREF, {}) };
  let ideas = null, versions = [];
  const listBox = h('div', { class: 'col' }, loading());
  const filtersBox = h('div', { class: 'filters' });
  const search = h('input', { type: 'search', placeholder: 'Buscar ideas…', value: ctx.query.get('q') || '' });
  search.addEventListener('input', debounce(() => draw(), 150));

  const canEditPlan = can('planificacion.editar');
  const canEdit = (i) => canEditPlan || i.created_by === store.me.id;

  async function load() {
    try {
      const [r, v] = await Promise.all([api.get(`/api/ideas?${withProject()}`), loadVersions()]);
      if (!ctx.alive()) return;
      ideas = r.items; versions = v;
    } catch (err) { mount(listBox, errorBox(err, load)); return; }
    draw();
  }

  function savePrefs() { saveJSON(PREF, { sort: prefs.sort, status: prefs.status, priority: prefs.priority }); }

  function drawFilters() {
    const counts = {};
    for (const i of ideas || []) counts[i.status] = (counts[i.status] || 0) + 1;
    const active = (ideas || []).filter(i => i.status !== 'descartada').length;
    mount(filtersBox,
      h('div', { class: 'group' }, h('span', null, 'Estado'),
        STATUS_FILTERS.map(([k, label]) => h('button', {
          class: 'chip' + (prefs.status === k ? ' on' : ''),
          onclick: () => { prefs.status = k; savePrefs(); draw(); },
        }, label, ideas ? h('span', { class: 'muted tiny' }, k === 'activas' ? active : k === 'all' ? ideas.length : counts[k] || 0) : null))),
      h('div', { class: 'group' }, h('span', null, 'Prioridad'),
        selectEl(toOptions(PRIORITIES, 'Todas'), prefs.priority, { onchange: (e) => { prefs.priority = e.target.value; savePrefs(); draw(); }, style: { width: 'auto' } })),
      h('div', { class: 'group' }, h('span', null, 'Ordenar'),
        h('div', { class: 'seg' }, SORTS.map(([k, label]) => h('button', {
          class: prefs.sort === k ? 'on' : '', onclick: () => { prefs.sort = k; savePrefs(); draw(); },
        }, label)))));
  }

  function visible() {
    const q = search.value.trim().toLowerCase();
    let list = ideas.filter(i => {
      if (prefs.status === 'activas' && i.status === 'descartada') return false;
      if (!['activas', 'all'].includes(prefs.status) && i.status !== prefs.status) return false;
      if (prefs.priority && i.priority !== prefs.priority) return false;
      if (q && !`${i.title} ${i.description} ${i.created_by_name || ''}`.toLowerCase().includes(q)) return false;
      return true;
    });
    const key = {
      votes: (a, b) => b.votes - a.votes || b.id - a.id,
      recent: (a, b) => (b.created_at || '').localeCompare(a.created_at || '') || b.id - a.id,
      priority: (a, b) => (PRIO[a.priority] ?? 9) - (PRIO[b.priority] ?? 9) || b.votes - a.votes,
    }[prefs.sort] || ((a, b) => b.votes - a.votes);
    list = list.sort((a, b) => ((a.status === 'descartada') - (b.status === 'descartada')) || key(a, b));
    return list;
  }

  function draw() {
    if (!ideas) return;
    drawFilters();
    const list = visible();
    if (!ideas.length) {
      mount(listBox, emptyState('Todavía no hay ideas', 'Propón la primera con «Nueva idea». El equipo podrá votarla y convertirla en tarea.'));
      return;
    }
    if (!list.length) { mount(listBox, emptyState('Ninguna idea coincide con los filtros', 'Prueba con otro estado o borra la búsqueda.')); return; }
    mount(listBox, list.map(ideaCard));
  }

  function ideaCard(i) {
    const voteBtn = h('button', {
      class: 'idea-vote' + (i.voted ? ' on' : ''), title: i.voted ? 'Quitar mi voto' : 'Votar esta idea',
      'aria-pressed': i.voted ? 'true' : 'false',
    }, icon('vote', 18), h('b', null, i.votes), h('span', { class: 'tiny' }, i.votes === 1 ? 'voto' : 'votos'));
    voteBtn.addEventListener('click', () => busy(voteBtn, async () => {
      const r = await api.post(`/api/ideas/${i.id}/vote`, { vote: !i.voted });
      i.voted = !i.voted; i.votes = r.votes ?? i.votes + (i.voted ? 1 : -1);
      draw();
    }).catch(() => {}));
    const desc = i.description ? h('div', { class: 'idea-desc pre' }, richText(i.description)) : null;
    const actions = [];
    if (canEdit(i)) actions.push(h('button', { class: 'btn sm', onclick: () => ideaForm(i) }, icon('edit'), 'Editar'));
    if (canEditPlan && can('tareas.crear') && !i.task_id && i.status !== 'descartada') {
      const b = h('button', { class: 'btn sm', title: 'Crea una tarea con el título, la descripción, la prioridad y la versión de la idea' }, icon('task'), 'Convertir en tarea');
      b.addEventListener('click', () => toTask(i, b));
      actions.push(b);
    }
    if (canEditPlan) {
      actions.push(menuButton('Estado', Object.entries(IDEA_STATUSES).filter(([k]) => k !== i.status).map(([k, label]) => ({
        label: `Marcar como «${label}»`, danger: k === 'descartada', onClick: () => setStatus(i, k),
      })), { title: 'Cambiar el estado de la idea' }));
    }
    return h('div', { class: 'card idea' + (i.status === 'descartada' ? ' discarded' : '') },
      voteBtn,
      h('div', { class: 'grow col idea-main' },
        h('div', { class: 'row wrap' },
          h('b', { class: 'idea-title' }, i.title),
          kindBadge('idea', i.status, i.status_label),
          priorityBadge(i.priority, i.priority_label),
          i.version_name ? h('span', { class: 'badge info', title: 'Versión' }, icon('tag', 12), i.version_name) : null,
          i.task_key ? h('a', { class: 'badge accent', href: `#/tareas/${i.task_key}`, title: 'Tarea creada a partir de esta idea' }, icon('task', 12), i.task_key) : null),
        desc,
        h('div', { class: 'row wrap between' },
          h('span', { class: 'tiny muted' },
            multiProject() && i.project_key ? `${i.project_key} · ` : '',
            `Propuesta por ${i.created_by_name || '—'} · `, h('span', { title: fmtDate(i.created_at) }, fmtRelative(i.created_at))),
          actions.length ? h('div', { class: 'row wrap' }, actions) : null)));
  }

  async function setStatus(i, status) {
    try {
      await api.patch(`/api/ideas/${i.id}`, { status, version: i.version });
      toast(`Idea marcada como «${IDEA_STATUSES[status]}».`, 'ok');
    } catch (err) {
      toast(err.message, err.status === 409 ? 'warn' : 'error');
    }
    load();
  }

  async function toTask(i, btn) {
    const ok = await confirmDialog({
      title: 'Convertir en tarea',
      message: `Se creará una tarea con el título «${i.title}», su descripción, prioridad y versión. La idea pasará a «En desarrollo».`,
      confirmLabel: 'Crear tarea',
    });
    if (!ok) return;
    try {
      const r = await busy(btn, () => api.post(`/api/ideas/${i.id}/to-task`, { uid: uid() }));
      if (r && r.task_key) {
        toast(`Se ha creado la tarea ${r.task_key}. Pulsa aquí para abrirla.`, 'ok', { onClick: () => { location.hash = `#/tareas/${r.task_key}`; } });
      }
      load();
    } catch { /* aviso ya mostrado */ }
  }

  function ideaForm(idea) {
    const editing = !!idea;
    const title = h('input', { maxlength: 200, placeholder: 'Ej.: Modo sin conexión en la app', value: idea?.title || '' });
    const description = h('textarea', { rows: 6, placeholder: '¿Qué problema resuelve? ¿Para quién? ¿Cómo lo imaginas?' });
    description.value = idea?.description || '';
    if (!editing) bindDraft(description, 'idea-new');
    const priority = selectEl(toOptions(PRIORITIES), idea?.priority || 'media');
    let projectId = idea?.project_id || newProjectId();
    const version = selectEl(versionOptions(versions, projectId), idea?.version_id ? String(idea.version_id) : '');
    const proj = editing ? null : projectField(projectId, (v) => {
      projectId = Number(v);
      const keep = version.value;
      mount(version, ...versionOptions(versions, projectId).map(([v2, l]) => h('option', { value: v2 }, l)));
      version.value = versionOptions(versions, projectId).some(([v2]) => v2 === keep) ? keep : '';
    });
    const status = canEditPlan ? selectEl(toOptions(IDEA_STATUSES), idea?.status || 'propuesta') : null;
    const body = h('div', { class: 'form-grid' },
      field('Título', title, { required: true, cls: 'span2' }),
      field('Descripción', description, { cls: 'span2', hint: 'Puedes mencionar elementos (APP-012, T-004) y a personas (@ana).' }),
      field('Prioridad', priority),
      field('Versión', version, { hint: 'Opcional: en qué versión encajaría.' }),
      proj ? proj.el : null,
      status ? field('Estado', status) : null,
      !editing && !canEditPlan ? h('div', { class: 'callout info span2 small' }, 'La idea se crea como «Propuesta» y con tu voto. Quien gestiona la planificación decidirá su estado.') : null);
    modal({
      title: editing ? 'Editar idea' : 'Nueva idea', body, wide: true,
      actions: [{ label: 'Cancelar' }, {
        label: editing ? 'Guardar cambios' : 'Proponer idea', kind: 'primary', onClick: () => {
          if (!requireFields([[title, 'el título']])) return false;
          const data = {
            title: title.value.trim(), description: description.value.trim(), priority: priority.value,
            version_id: version.value ? Number(version.value) : null,
          };
          if (status) data.status = status.value;
          if (editing) {
            const changed = {};
            for (const [k, v] of Object.entries(data)) if ((idea[k] ?? null) !== v && !(k === 'description' && (idea[k] || '') === v)) changed[k] = v;
            if (!Object.keys(changed).length) return true;
            return submit(() => api.patch(`/api/ideas/${idea.id}`, { ...changed, version: idea.version }), 'Idea actualizada.', load);
          }
          return submit(async () => {
            const r = await api.post('/api/ideas', { ...data, uid: uid(), project_id: projectId }, { queue: true });
            clearDraft('idea-new');
            return r;
          }, 'Idea propuesta. Ya cuenta con tu voto.', load);
        },
      }],
    });
  }

  mount(root,
    pageHead('Ideas',
      h('button', { class: 'btn primary', onclick: () => ideaForm(null) }, icon('plus'), 'Nueva idea')),
    h('p', { class: 'muted small mb' }, 'Propón mejoras y vota las que más te importan. Las ideas con más apoyo se planifican en una versión y se convierten en tareas.'),
    h('div', { class: 'toolbar' }, h('div', { class: 'search' }, icon('search'), search),
      multiProject() ? h('span', { class: 'tiny muted' }, store.project === 'all' ? 'Todos los proyectos' : `Proyecto ${projectKey(store.project)}`) : null),
    filtersBox, listBox);
  drawFilters();
  await load();
  if (ctx.query.get('nueva') === '1') ideaForm(null);
  ctx.onChanges(['idea', 'version', 'task'], load);
}

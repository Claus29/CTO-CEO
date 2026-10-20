// Plan semanal: objetivos de la semana, tareas planificadas y lo que vence en esos días.
import { api } from '../api.js';
import { can, store, withProject } from '../state.js';
import {
  addDays, busy, confirmDialog, debounce, errorBox, fmtDate, h, icon, loading, modal, mondayOf, mount, MONTHS,
  pageHead, priorityBadge, promptDialog, statusBadge, todayISO, toast, uid,
} from '../ui.js';
import { bar, multiProject } from './_plan_shared.js';

function weekLabel(ws) {
  const we = addDays(ws, 6);
  const [y1, m1, d1] = ws.split('-').map(Number);
  const [y2, m2, d2] = we.split('-').map(Number);
  if (y1 !== y2) return `Del ${d1} de ${MONTHS[m1 - 1]} de ${y1} al ${d2} de ${MONTHS[m2 - 1]} de ${y2}`;
  if (m1 !== m2) return `Del ${d1} de ${MONTHS[m1 - 1]} al ${d2} de ${MONTHS[m2 - 1]} de ${y2}`;
  return `Del ${d1} al ${d2} de ${MONTHS[m2 - 1]} de ${y2}`;
}

export default async function render(ctx) {
  const { root } = ctx;
  const q = ctx.query.get('semana');
  let week = mondayOf(q && /^\d{4}-\d{2}-\d{2}$/.test(q) ? q : todayISO());
  let data = null;
  const canPlan = can('planificacion.editar');
  const me = store.me.id;
  const canTouch = (t) => can('tareas.editar') || t.created_by === me || t.assignee_id === me;

  const titleBox = h('div', { class: 'week-title' });
  const body = h('div', null, loading());
  // El campo del objetivo nuevo se conserva entre recargas para no perder lo que se escribe.
  const goalInput = h('input', { placeholder: 'Nuevo objetivo para esta semana… (Intro para añadir)', maxlength: 300 });
  const goalBtn = h('button', { class: 'btn primary', onclick: () => addGoal() }, icon('plus'), 'Añadir');
  goalInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addGoal(); } });

  function setWeek(w) {
    week = w;
    history.replaceState(null, '', `#/semana?semana=${w}`);
    data = null;
    mount(body, loading());
    load();
  }

  async function load() {
    const w = week;
    try {
      const d = await api.get(`/api/weeks/${w}?${withProject()}`);
      if (!ctx.alive() || w !== week) return;
      data = d;
    } catch (err) { mount(body, errorBox(err, load)); return; }
    draw();
  }

  async function addGoal() {
    const title = goalInput.value.trim();
    if (!title) { goalInput.focus(); return; }
    try {
      await busy(goalBtn, () => api.post('/api/goals', { uid: uid(), title, week_start: week }, { queue: true }));
      goalInput.value = '';
      goalInput.focus();
      load();
    } catch { /* aviso ya mostrado */ }
  }

  function goalRow(g) {
    const check = h('input', { type: 'checkbox', checked: !!g.done, disabled: !canPlan, title: g.done ? 'Desmarcar' : 'Marcar como cumplido' });
    check.addEventListener('change', async () => {
      check.disabled = true;
      try { await api.patch(`/api/goals/${g.id}`, { done: check.checked }); } catch (err) { toast(err.message, 'error'); check.checked = !check.checked; }
      load();
    });
    const actions = canPlan ? h('div', { class: 'row goal-actions' },
      h('button', { class: 'btn ghost sm icon', title: 'Cambiar el texto', onclick: async () => {
        const t = await promptDialog({ title: 'Editar objetivo', label: 'Objetivo', value: g.title, confirmLabel: 'Guardar' });
        if (t === null || t === g.title) return;
        try { await api.patch(`/api/goals/${g.id}`, { title: t }); load(); } catch (err) { toast(err.message, 'error'); }
      } }, icon('edit')),
      h('button', { class: 'btn ghost sm icon', title: 'Borrar el objetivo', onclick: async () => {
        if (!await confirmDialog({ title: 'Borrar objetivo', message: `¿Borrar el objetivo «${g.title}»?`, confirmLabel: 'Borrar', danger: true })) return;
        try { await api.del(`/api/goals/${g.id}`); toast('Objetivo borrado.', 'ok'); load(); } catch (err) { toast(err.message, 'error'); }
      } }, icon('trash'))) : null;
    return h('div', { class: 'goal' + (g.done ? ' done' : '') },
      h('label', { class: 'check grow' }, check, h('span', { class: 'gt' }, g.title)),
      g.done && g.done_by_name ? h('span', { class: 'tiny muted nowrap' }, `Cumplido por ${g.done_by_name}`) : null,
      actions);
  }

  function taskRow(t, { planned }) {
    const remove = planned && canTouch(t) ? h('button', {
      class: 'btn ghost sm icon', title: 'Quitar de esta semana',
      onclick: async (e) => {
        e.preventDefault(); e.stopPropagation();
        try { await api.patch(`/api/tasks/${t.id}`, { planned_week: null, version: t.version }); toast(`${t.key} ya no está planificada esta semana.`, 'ok'); }
        catch (err) { toast(err.message, err.status === 409 ? 'warn' : 'error'); }
        load();
      },
    }, icon('x')) : null;
    return h('a', { class: 'list-item week-task', href: `#/tareas/${t.key}` },
      multiProject() ? h('span', { class: 'project-dot', style: { background: t.project_color || 'var(--muted)' }, title: t.project_name || '' }) : null,
      h('span', { class: 'key' }, t.key),
      h('div', { class: 'grow' },
        h('div', { class: 'ellipsis' }, t.title),
        h('div', { class: 'tiny muted ellipsis' },
          t.assignee_name || 'Sin responsable',
          t.target_date ? h('span', { class: t.overdue ? 'overdue' : '' }, ` · vence ${fmtDate(t.target_date)}`) : null)),
      planned ? h('div', { class: 'week-progress', title: `Progreso: ${t.progress}%` }, bar(t.status === 'resuelto' ? 100 : t.progress), h('span', { class: 'tiny muted' }, `${t.status === 'resuelto' ? 100 : t.progress}%`)) : null,
      priorityBadge(t.priority, t.priority_label), statusBadge(t.status, t.status_label), remove);
  }

  function incidentRow(i) {
    const late = i.target_date < todayISO() && !['resuelta', 'duplicada'].includes(i.status);
    return h('a', { class: 'list-item', href: `#/incidencias/${i.key}` },
      h('span', { class: 'key' }, i.key), h('span', { class: 'grow ellipsis' }, i.title),
      h('span', { class: 'tiny nowrap ' + (late ? 'overdue' : 'muted') }, fmtDate(i.target_date)),
      priorityBadge(i.priority), statusBadge(i.status));
  }

  async function planTask() {
    let tasks;
    try {
      tasks = (await api.get(`/api/tasks?${withProject({ status: 'pendiente,en_progreso,bloqueada' })}`)).items
        .filter(t => t.planned_week !== week && canTouch(t));
    } catch (err) { toast(err.message, 'error'); return; }
    const filter = h('input', { type: 'search', placeholder: 'Buscar por clave o título…' });
    const list = h('div', { class: 'list-card pick-list' });
    const drawList = () => {
      const f = filter.value.trim().toLowerCase();
      const items = tasks.filter(t => !f || `${t.key} ${t.title}`.toLowerCase().includes(f)).slice(0, 80);
      if (!items.length) { mount(list, h('div', { class: 'empty small' }, tasks.length ? 'Ninguna tarea coincide.' : 'No hay tareas abiertas que puedas planificar.')); return; }
      mount(list, items.map(t => h('button', { class: 'list-item pick', type: 'button', onclick: async (e) => {
        const b = e.currentTarget;
        try {
          await busy(b, () => api.patch(`/api/tasks/${t.id}`, { planned_week: week, version: t.version }), `${t.key} planificada para esta semana.`);
          tasks = tasks.filter(x => x.id !== t.id); drawList(); load();
        } catch { /* aviso ya mostrado */ }
      } },
      h('span', { class: 'key' }, t.key), h('span', { class: 'grow ellipsis' }, t.title),
      t.planned_week ? h('span', { class: 'tiny muted nowrap' }, `Semana del ${fmtDate(t.planned_week)}`) : null,
      priorityBadge(t.priority, t.priority_label), statusBadge(t.status, t.status_label))));
    };
    filter.addEventListener('input', debounce(drawList, 120));
    drawList();
    modal({ title: 'Planificar tareas para esta semana', wide: true,
      body: h('div', { class: 'col' }, h('p', { class: 'small muted' }, `Elige las tareas que el equipo hará en la semana: ${weekLabel(week).toLowerCase()}.`), filter, list),
      actions: [{ label: 'Hecho', kind: 'primary' }] });
  }

  function draw() {
    const d = data;
    const today = mondayOf(todayISO());
    const rel = week === today ? ['Semana actual', 'accent'] : week < today ? ['Semana pasada', 'neutral'] : ['Semana próxima', 'info'];
    mount(titleBox, h('h2', null, weekLabel(week)), h('span', { class: `badge ${rel[1]}` }, rel[0]));
    if (!d) return;
    const p = d.progress;
    const summary = h('div', { class: 'card week-summary' },
      h('div', { class: 'row between wrap' },
        h('div', null, h('div', { class: 'week-pct' }, `${p.percent}%`), h('div', { class: 'small muted' }, 'de la semana completado')),
        h('div', { class: 'row wrap gap-lg' },
          h('div', { class: 'week-num' }, h('b', null, `${p.goals_done}/${p.goals_total}`), h('span', { class: 'tiny muted' }, 'objetivos cumplidos')),
          h('div', { class: 'week-num' }, h('b', null, `${p.tasks_done}/${p.tasks_total}`), h('span', { class: 'tiny muted' }, 'tareas planificadas resueltas')),
          h('div', { class: 'week-num' }, h('b', null, d.due_tasks.length + d.due_incidents.length), h('span', { class: 'tiny muted' }, 'elementos vencen esta semana')))),
      h('div', { class: 'progress big-progress mt' }, h('i', { style: { width: `${p.percent}%` } })),
      h('div', { class: 'tiny muted mt' }, 'El porcentaje cuenta los objetivos cumplidos y las tareas planificadas ya resueltas.'));

    const goals = h('div', { class: 'card' },
      h('div', { class: 'card-head' }, h('h3', null, 'Objetivos de la semana'), h('span', { class: 'muted small' }, d.goals.length || '')),
      d.goals.length ? h('div', null, d.goals.map(goalRow))
        : h('div', { class: 'empty small' }, canPlan ? 'Sin objetivos. Escribe abajo los 3-5 logros más importantes de la semana.' : 'No hay objetivos para esta semana.'),
      canPlan ? h('div', { class: 'row mt' }, goalInput, goalBtn) : null);

    const planned = h('div', { class: 'list-card' },
      h('div', { class: 'card-head' }, h('h3', null, 'Tareas planificadas'), h('span', { class: 'muted small' }, d.planned_tasks.length || ''),
        can('tareas.ver') ? h('button', { class: 'btn sm', onclick: planTask }, icon('plus'), 'Planificar tarea') : null),
      d.planned_tasks.length ? h('div', null, d.planned_tasks.map(t => taskRow(t, { planned: true })))
        : h('div', { class: 'empty small' }, 'No hay tareas planificadas para esta semana. Usa «Planificar tarea» o indica la «semana prevista» en la ficha de una tarea.'));

    const due = h('div', { class: 'list-card' },
      h('div', { class: 'card-head' }, h('h3', null, 'Vencen esta semana'), h('span', { class: 'muted small' }, (d.due_tasks.length + d.due_incidents.length) || '')),
      d.due_tasks.length || d.due_incidents.length ? h('div', null,
        d.due_tasks.map(t => taskRow(t, { planned: false })), d.due_incidents.map(incidentRow))
        : h('div', { class: 'empty small' }, 'Nada con fecha objetivo en esta semana (aparte de lo planificado).'));

    mount(body, summary, h('div', { class: 'grid cols-2 mt' }, h('div', { class: 'col gap-lg' }, goals), h('div', { class: 'col gap-lg' }, planned, due)));
  }

  mount(root,
    pageHead('Plan semanal',
      h('div', { class: 'row' },
        h('button', { class: 'btn icon', title: 'Semana anterior', onclick: () => setWeek(addDays(week, -7)) }, icon('left')),
        h('button', { class: 'btn', onclick: () => setWeek(mondayOf(todayISO())) }, 'Esta semana'),
        h('button', { class: 'btn icon', title: 'Semana siguiente', onclick: () => setWeek(addDays(week, 7)) }, icon('right')))),
    h('div', { class: 'row wrap mb' }, titleBox),
    body);
  draw();
  await load();
  ctx.onChanges(['goal', 'task', 'incident'], load);
}

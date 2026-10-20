// Equipo: en qué trabaja cada uno, carga de trabajo, ausencias, daily asíncrono y registros de tiempo.
import { api } from '../api.js';
import { can, canAny, entityHash, store, userById } from '../state.js';
import {
  addDays, avatar, busy, clear, confirmDialog, emptyState, errorBox, field, fmtDate, fmtDateTime, fmtDuration, fmtHours,
  fmtRelative, h, icon, loading, modal, mondayOf, mount, pageHead, richText, selectEl, statusBadge, table, tabs, toast,
  todayISO, typeBadge, uid,
} from '../ui.js';

const TAB_DEFS = [
  { key: 'ahora', label: 'Ahora', icon: 'activity', allowed: () => canAny('actividad.ver_propia', 'actividad.ver_todos') },
  { key: 'carga', label: 'Carga de trabajo', icon: 'chart', allowed: () => can('equipo.ver') },
  { key: 'ausencias', label: 'Ausencias', icon: 'calendar', allowed: () => true },
  { key: 'daily', label: 'Daily', icon: 'week', allowed: () => true },
  { key: 'tiempo', label: 'Tiempo', icon: 'clock', allowed: () => true },
];

const elapsed = (iso) => Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
function clock(seconds) {
  const hh = Math.floor(seconds / 3600), mm = Math.floor((seconds % 3600) / 60), ss = seconds % 60;
  return `${hh}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}
const workLink = (it) => h('a', { class: 'row work-line', href: entityHash(it.type || it.entity_type, it.id || it.entity_id, it.key) },
  typeBadge(it.type || it.entity_type), h('b', { class: 'small nowrap' }, it.key || ''), h('span', { class: 'grow ellipsis small' }, it.title || ''),
  it.status ? statusBadge(it.status) : null);
const personOf = (id, name) => userById(id) || { id, display_name: name || `#${id}` };
const weekLabel = (ws, we) => `Semana del ${fmtDate(ws)} al ${fmtDate(we)}`;

export default async function render(ctx) {
  const { root } = ctx;
  const visible = TAB_DEFS.filter(t => t.allowed());
  let tab = ctx.params.tab && visible.some(t => t.key === ctx.params.tab) ? ctx.params.tab : visible[0].key;
  if (ctx.params.tab && ctx.params.tab !== tab) toast('Tu rol no permite ver esa pestaña.', 'warn');
  const persona = Number(ctx.query.get('persona')) || null;
  const body = h('div');
  const go = (k) => { location.hash = `#/equipo/${k}${persona ? `?persona=${persona}` : ''}`; };
  ctx.setTitle(`Equipo · ${visible.find(t => t.key === tab).label}`);
  mount(root, pageHead('Equipo'), tabs(visible, tab, go), body);

  const timers = [];
  const tick = setInterval(() => {
    for (const el of root.querySelectorAll('[data-since]')) el.textContent = clock(elapsed(el.dataset.since));
  }, 1000);
  timers.push(() => clearInterval(tick));

  const views = { ahora: nowTab, carga: workloadTab, ausencias: absencesTab, daily: dailyTab, tiempo: timeTab };
  await views[tab]({ ctx, body, persona });
  return () => timers.forEach(f => f());
}

// ---------- Ahora ----------
async function nowTab({ ctx, body, persona }) {
  const draw = async () => {
    let d;
    try { d = await api.get('/api/team/now'); } catch (err) { mount(body, errorBox(err, draw)); return; }
    const people = [...d.items].sort((a, b) => (b.online - a.online) || a.user_name.localeCompare(b.user_name));
    const card = (p) => {
      const u = personOf(p.user_id, p.user_name);
      const hl = persona === p.user_id;
      return h('div', { class: 'card person-card' + (hl ? ' hl' : ''), id: `persona-${p.user_id}` },
        h('div', { class: 'row' }, avatar(u, p.online),
          h('div', { class: 'grow' }, h('b', null, p.user_name),
            h('div', { class: 'tiny muted' }, p.online ? 'En línea' : p.last_seen_at ? `Visto ${fmtRelative(p.last_seen_at)}` : 'Aún no se ha conectado', u.role_label ? ` · ${u.role_label}` : '')),
          h('span', { class: 'tiny muted', title: 'Tiempo registrado hoy' }, icon('clock', 13), ' ', fmtDuration(p.today_seconds || 0), ' hoy')),
        p.timer ? h('div', { class: 'callout ok row mt' }, icon('play', 14),
          h('div', { class: 'grow ellipsis' }, 'Cronómetro en marcha: ', h('a', { href: entityHash(p.timer.entity_type, p.timer.entity_id, p.timer.key) }, p.timer.key || ''),
            ' ', h('span', { class: 'muted' }, p.timer.title || '')),
          h('b', { class: 'mono', 'data-since': p.timer.started_at }, clock(elapsed(p.timer.started_at)))) : null,
        h('div', { class: 'section-title' }, 'Trabajando en'),
        p.doing.length ? h('div', { class: 'col tight' }, p.doing.map(workLink)) : h('div', { class: 'small muted' }, 'Nada en curso.'),
        p.standup ? h('div', null, h('div', { class: 'section-title' }, 'Daily de hoy'),
          p.standup.today ? h('div', { class: 'small pre' }, richText(p.standup.today)) : h('div', { class: 'small muted' }, 'Sin plan para hoy.'),
          p.standup.blockers ? h('div', { class: 'block-callout mt' }, h('div', { class: 't' }, icon('alert', 15), 'Bloqueo'), h('div', { class: 'small pre' }, richText(p.standup.blockers))) : null)
          : null,
        p.recent.length ? h('details', { class: 'mt' }, h('summary', { class: 'small muted' }, 'Actividad reciente'),
          h('div', { class: 'timeline' }, p.recent.map(a => h('div', { class: 'tl-item' }, h('span', { class: 'dot' }),
            h('div', null, h('div', { class: 'small' }, a.summary), h('div', { class: 'tiny muted' }, fmtRelative(a.created_at))))))) : null);
    };
    mount(body,
      d.all ? null : h('div', { class: 'callout info mb' }, 'Tu rol solo permite ver tu propia actividad. Para ver la del equipo hace falta el permiso «Ver la actividad de todo el equipo».'),
      people.length ? h('div', { class: 'people-grid' }, people.map(card)) : emptyState('No hay nadie activo', ''));
    if (persona) document.getElementById(`persona-${persona}`)?.scrollIntoView({ block: 'center' });
  };
  await draw();
  ctx.onChanges(['time', 'task', 'incident', 'standup', 'user', 'presence'], draw);
}

// ---------- Carga de trabajo ----------
async function workloadTab({ ctx, body, persona }) {
  let week = mondayOf(todayISO());
  const open = new Set(persona ? [persona] : []);
  const draw = async () => {
    let d;
    try { d = await api.get(`/api/workload?week=${week}`); } catch (err) { mount(body, errorBox(err, draw)); return; }
    const over = d.people.filter(p => p.over);
    const nav = h('div', { class: 'toolbar' },
      h('button', { class: 'btn sm icon', title: 'Semana anterior', onclick: () => { week = addDays(week, -7); draw(); } }, icon('left')),
      h('b', null, weekLabel(d.week_start, d.week_end)),
      h('button', { class: 'btn sm icon', title: 'Semana siguiente', onclick: () => { week = addDays(week, 7); draw(); } }, icon('right')),
      week !== mondayOf(todayISO()) ? h('button', { class: 'btn sm ghost', onclick: () => { week = mondayOf(todayISO()); draw(); } }, 'Esta semana') : null);
    const row = (p) => {
      const pct = p.capacity_hours ? Math.min(100, Math.round(100 * p.assigned_hours / p.capacity_hours)) : (p.assigned_hours ? 100 : 0);
      const expanded = open.has(p.user_id);
      return h('div', { class: 'load-row' + (p.over ? ' over' : '') + (persona === p.user_id ? ' hl' : '') },
        h('button', { class: 'load-main', onclick: () => { expanded ? open.delete(p.user_id) : open.add(p.user_id); draw(); }, 'aria-expanded': expanded ? 'true' : 'false' },
          avatar(personOf(p.user_id, p.user_name)),
          h('div', { class: 'load-name' }, h('b', null, p.user_name),
            h('div', { class: 'tiny muted' }, p.absent_days ? `${p.absent_days} ${p.absent_days === 1 ? 'día' : 'días'} de ausencia` : 'Sin ausencias')),
          h('div', { class: 'load-bar' },
            h('div', { class: 'progress' + (p.over ? ' over' : '') }, h('i', { style: { width: `${pct}%` } })),
            h('div', { class: 'tiny muted' }, `${fmtHours(p.assigned_hours)} asignadas de ${fmtHours(p.capacity_hours)} disponibles`)),
          h('div', { class: 'load-nums' },
            h('span', { class: p.free_hours < 0 ? 'danger-text' : 'ok-text' }, p.free_hours < 0 ? `${fmtHours(-p.free_hours)} de más` : `${fmtHours(p.free_hours)} libres`),
            h('span', { class: 'tiny muted' }, `${fmtHours(p.logged_hours)} registradas`)),
          p.over ? h('span', { class: 'badge danger' }, 'Sobrecarga') : null,
          p.unestimated ? h('span', { class: 'badge warn', title: 'Elementos sin estimación de horas: no cuentan en la carga' }, `${p.unestimated} sin estimar`) : null,
          icon(expanded ? 'left' : 'right', 14)),
        expanded ? h('div', { class: 'load-items' }, p.items.length ? p.items.map(it => h('div', { class: 'row' }, workLink(it),
          h('span', { class: 'small nowrap ' + (it.estimate_hours ? '' : 'warn-text') }, it.estimate_hours ? fmtHours(it.estimate_hours) : 'sin estimar')))
          : h('div', { class: 'small muted' }, 'Nada asignado para esta semana.')) : null);
    };
    mount(body, nav,
      over.length ? h('div', { class: 'callout danger mb' }, h('b', null, `Aviso de sobrecarga: `),
        `${over.map(p => p.user_name).join(', ')} ${over.length === 1 ? 'tiene' : 'tienen'} más horas asignadas que su capacidad esta semana. Reparte el trabajo o mueve tareas a otra semana.`)
        : h('div', { class: 'callout ok mb' }, 'Nadie supera su capacidad esta semana.'),
      h('div', { class: 'small muted mb' }, 'Cuentan las tareas previstas para la semana, con fecha objetivo en ella o en progreso, y las incidencias abiertas asignadas. La capacidad descuenta las ausencias de lunes a viernes.'),
      d.people.length ? h('div', { class: 'list-card' }, d.people.map(row)) : emptyState('No hay personas activas', ''));
  };
  await draw();
  ctx.onChanges(['task', 'incident', 'absence', 'time', 'user'], draw);
}

// ---------- Ausencias ----------
async function absencesTab({ ctx, body }) {
  const me = store.me;
  const manage = can('sistema.usuarios');
  const draw = async () => {
    let d;
    try { d = await api.get('/api/absences'); } catch (err) { mount(body, errorBox(err, draw)); return; }
    const today = todayISO();
    const now = d.items.filter(a => a.start_date <= today && a.end_date >= today);
    const next = d.items.filter(a => a.start_date > today);
    const past = d.items.filter(a => a.end_date < today).reverse();
    const cols = [
      { label: 'Persona', render: a => h('div', { class: 'row' }, avatar(personOf(a.user_id, a.user_name)), a.user_name) },
      { label: 'Desde', render: a => fmtDate(a.start_date), cls: 'nowrap' },
      { label: 'Hasta', render: a => fmtDate(a.end_date), cls: 'nowrap' },
      { label: 'Días', render: a => days(a), cls: 'nowrap' },
      { label: 'Nota', render: a => h('span', { class: 'small' }, a.note || '—') },
      { label: '', cls: 'nowrap', render: a => (a.user_id === me.id || manage)
        ? h('button', { class: 'btn ghost sm icon', title: 'Borrar la ausencia', onclick: (e) => remove(e.currentTarget, a) }, icon('trash')) : null },
    ];
    const section = (title, rows, empty) => h('div', { class: 'mb' }, h('div', { class: 'section-title' }, `${title} (${rows.length})`),
      rows.length ? table(cols, rows) : h('div', { class: 'small muted' }, empty));
    mount(body,
      h('div', { class: 'toolbar' }, h('button', { class: 'btn primary', onclick: create }, icon('plus'), 'Registrar ausencia'),
        h('span', { class: 'small muted' }, can('equipo.ver') ? 'Las ausencias reducen la capacidad en la carga de trabajo.' : 'Solo ves tus propias ausencias.')),
      section('Ahora', now, 'Nadie está ausente hoy.'),
      section('Próximas', next, 'No hay ausencias previstas.'),
      past.length ? section('Pasadas (últimos 60 días)', past, '') : null);
  };
  const days = (a) => {
    let n = 0; let d = a.start_date;
    while (d <= a.end_date && n < 400) { const wd = new Date(d + 'T12:00:00').getDay(); if (wd > 0 && wd < 6) n++; d = addDays(d, 1); }
    return `${n} ${n === 1 ? 'laborable' : 'laborables'}`;
  };
  const remove = async (btn, a) => {
    const ok = await confirmDialog({ title: 'Borrar la ausencia', danger: true, confirmLabel: 'Borrar',
      message: `¿Borrar la ausencia de ${a.user_name} del ${fmtDate(a.start_date)} al ${fmtDate(a.end_date)}?` });
    if (!ok) return;
    try { await busy(btn, () => api.del(`/api/absences/${a.id}`), 'Ausencia borrada.'); draw(); } catch { /* aviso mostrado */ }
  };
  function create() {
    const who = manage ? selectEl(store.users.map(u => [u.id, u.display_name]), me.id) : null;
    const start = h('input', { type: 'date', value: todayISO() });
    const end = h('input', { type: 'date', value: todayISO() });
    start.addEventListener('change', () => { if (end.value < start.value) end.value = start.value; });
    const note = h('input', { maxlength: 200, placeholder: 'Vacaciones, médico, formación…' });
    modal({ title: 'Registrar ausencia', body: h('div', { class: 'form-grid' },
      who ? field('Persona', who, { required: true, cls: 'span2' }) : null,
      field('Desde', start, { required: true }), field('Hasta (incluido)', end, { required: true }),
      field('Nota', note, { cls: 'span2', hint: 'Visible para quien gestiona el equipo.' })),
    actions: [{ label: 'Cancelar' }, { label: 'Guardar', kind: 'primary', onClick: async () => {
      if (!start.value) { toast('Indica la fecha de inicio.', 'error'); return false; }
      if (end.value && end.value < start.value) { toast('La fecha final no puede ser anterior a la inicial.', 'error'); return false; }
      try {
        const r = await api.post('/api/absences', { uid: uid(), user_id: who ? Number(who.value) : me.id, start_date: start.value,
          end_date: end.value || start.value, note: note.value.trim() }, { queue: true });
        toast(r._queued ? 'Sin conexión: la ausencia se guardará al reconectar.' : 'Ausencia registrada.', r._queued ? 'warn' : 'ok');
        draw();
      } catch (err) { toast(err.message, 'error'); return false; }
    } }] });
  }
  await draw();
  ctx.onChanges(['absence', 'user'], draw);
}

// ---------- Daily ----------
async function dailyTab({ ctx, body }) {
  const me = store.me;
  let day = todayISO();
  let dirty = false;
  const yesterday = h('textarea', { rows: 4, placeholder: '- Terminé APP-012…' });
  const today = h('textarea', { rows: 4, placeholder: '- Empiezo T-004…' });
  const blockers = h('textarea', { rows: 3, placeholder: 'Déjalo vacío si no tienes ningún bloqueo.' });
  for (const t of [yesterday, today, blockers]) t.addEventListener('input', () => { dirty = true; });
  const saveBtn = h('button', { class: 'btn primary' }, icon('check'), 'Guardar mi daily');
  const suggestBtn = h('button', { class: 'btn', title: 'Rellena con lo que has hecho desde tu último daily y lo que tienes en curso' }, icon('bulb'), 'Sugerir a partir de mi actividad');
  const dateInput = h('input', { type: 'date', value: day, max: todayISO(), style: { width: '160px' } });
  const mineBox = h('div', { class: 'card mb' });
  const others = h('div');

  suggestBtn.addEventListener('click', async () => {
    try {
      const s = await busy(suggestBtn, () => api.get('/api/standups/suggest'));
      const filled = [yesterday, today, blockers].some(t => t.value.trim());
      if (filled && !(await confirmDialog({ title: 'Sustituir el texto', message: 'Ya has escrito algo. ¿Quieres sustituirlo por la sugerencia? (Los campos que la sugerencia deje vacíos no se tocan.)', confirmLabel: 'Sustituir' }))) return;
      if (s.yesterday) yesterday.value = s.yesterday;
      if (s.today) today.value = s.today;
      if (s.blockers) blockers.value = s.blockers;
      dirty = true;
      if (!s.yesterday && !s.today && !s.blockers) toast('No hay actividad reciente de la que sacar una sugerencia.', 'info');
      else toast('Sugerencia lista: revísala y guárdala.', 'ok');
    } catch { /* aviso mostrado */ }
  });
  saveBtn.addEventListener('click', async () => {
    if (![yesterday, today, blockers].some(t => t.value.trim())) { toast('Escribe al menos qué hiciste o qué vas a hacer.', 'error'); yesterday.focus(); return; }
    try {
      await busy(saveBtn, () => api.post('/api/standups', { uid: uid(), date: day, yesterday: yesterday.value.trim(), today: today.value.trim(),
        blockers: blockers.value.trim() }, { queue: true }), blockers.value.trim() ? 'Daily guardado. Se ha avisado del bloqueo a los responsables.' : 'Daily guardado.');
      dirty = false; draw();
    } catch { /* aviso mostrado */ }
  });
  dateInput.addEventListener('change', async () => {
    if (!dateInput.value) return;
    if (dirty && !(await confirmDialog({ title: 'Cambios sin guardar', message: 'Has escrito en tu daily y no lo has guardado. ¿Cambiar de día igualmente?', confirmLabel: 'Cambiar de día' }))) { dateInput.value = day; return; }
    day = dateInput.value; dirty = false; draw(true);
  });

  async function draw(reset) {
    let d;
    try { d = await api.get(`/api/standups?date=${day}`); } catch (err) { mount(others, errorBox(err, draw)); return; }
    const mine = d.items.find(s => s.user_id === me.id);
    if (!dirty || reset) {
      yesterday.value = mine?.yesterday || ''; today.value = mine?.today || ''; blockers.value = mine?.blockers || '';
    }
    mount(mineBox,
      h('div', { class: 'card-head' }, h('h3', null, day === todayISO() ? 'Mi daily de hoy' : `Mi daily del ${fmtDate(day)}`),
        mine ? h('span', { class: 'badge ok' }, `Guardado ${fmtRelative(mine.updated_at)}`) : h('span', { class: 'badge warn' }, 'Sin rellenar')),
      h('div', { class: 'grid cols-3 daily-form' },
        field('¿Qué hice desde el último daily?', yesterday), field('¿Qué voy a hacer?', today),
        field('¿Algo me bloquea?', blockers, { hint: 'Si escribes algo aquí se avisará a quien gestiona el equipo.' })),
      h('div', { class: 'row mt' }, suggestBtn, h('div', { class: 'grow' }), saveBtn));
    const rest = d.items.filter(s => s.user_id !== me.id).sort((a, b) => (b.blockers ? 1 : 0) - (a.blockers ? 1 : 0) || a.display_name.localeCompare(b.display_name));
    const withBlock = d.items.filter(s => s.blockers && s.blockers.trim());
    const card = (s) => h('div', { class: 'card standup' + (s.blockers ? ' has-block' : '') },
      h('div', { class: 'row mb' }, avatar(personOf(s.user_id, s.display_name)), h('b', { class: 'grow' }, s.display_name),
        h('span', { class: 'tiny muted' }, fmtDateTime(s.updated_at))),
      h('div', { class: 'section-title' }, 'Hizo'), h('div', { class: 'small pre' }, s.yesterday ? richText(s.yesterday) : '—'),
      h('div', { class: 'section-title' }, 'Va a hacer'), h('div', { class: 'small pre' }, s.today ? richText(s.today) : '—'),
      s.blockers ? h('div', { class: 'block-callout mt' }, h('div', { class: 't' }, icon('alert', 15), 'Bloqueo'), h('div', { class: 'small pre' }, richText(s.blockers))) : null);
    mount(others,
      withBlock.length ? h('div', { class: 'callout danger mb' }, h('b', null, `${withBlock.length} ${withBlock.length === 1 ? 'bloqueo declarado' : 'bloqueos declarados'}: `),
        withBlock.map(s => s.display_name).join(', ')) : null,
      can('equipo.ver') ? h('div', { class: 'section-title' }, `Daily del equipo (${rest.length})`) : null,
      can('equipo.ver') ? (rest.length ? h('div', { class: 'people-grid' }, rest.map(card)) : h('div', { class: 'small muted mb' }, 'Nadie más lo ha rellenado todavía.')) : null,
      d.missing.length ? h('div', { class: 'mt' }, h('div', { class: 'section-title' }, `Sin rellenar (${d.missing.length})`),
        h('div', { class: 'row wrap' }, d.missing.map(m => h('span', { class: 'chip' }, m.user_name)))) : null,
      !can('equipo.ver') ? h('div', { class: 'small muted' }, 'Solo quien gestiona el equipo ve el daily de los demás.') : null);
  }
  mount(body, h('div', { class: 'toolbar' }, h('span', { class: 'small muted' }, 'Día:'), dateInput,
    h('span', { class: 'small muted' }, 'Un daily asíncrono: cada uno lo rellena cuando empieza su jornada.')), mineBox, others);
  await draw(true);
  ctx.onChanges(['standup', 'user'], () => draw());
}

// ---------- Tiempo ----------
async function timeTab({ ctx, body, persona }) {
  const me = store.me;
  const seeOthers = canAny('equipo.ver', 'informes.ver');
  const canLog = canAny('tareas.trabajar', 'incidencias.trabajar');
  let userId = seeOthers && persona && userById(persona) ? persona : me.id;
  let from = addDays(todayISO(), -6), to = todayISO();
  const current = h('div');
  const list = h('div');
  const who = seeOthers ? selectEl(store.users.map(u => [u.id, u.id === me.id ? `${u.display_name} (yo)` : u.display_name]), userId, { style: { width: 'auto' } }) : null;
  const fromIn = h('input', { type: 'date', value: from, style: { width: '150px' } });
  const toIn = h('input', { type: 'date', value: to, style: { width: '150px' } });
  who?.addEventListener('change', () => { userId = Number(who.value); drawList(); });
  fromIn.addEventListener('change', () => { from = fromIn.value || from; drawList(); });
  toIn.addEventListener('change', () => { to = toIn.value || to; drawList(); });
  const quick = (label, f, t) => h('button', { class: 'chip', onclick: () => { from = f; to = t; fromIn.value = f; toIn.value = t; drawList(); } }, label);
  const monday = mondayOf(todayISO());

  async function drawCurrent() {
    let t;
    try { t = await api.get('/api/time/current'); } catch { clear(current); return; }
    const e = t.entry;
    if (!e) { mount(current, h('div', { class: 'callout mb small' }, icon('clock', 14), ' No tienes ningún cronómetro en marcha. Puedes iniciarlo desde la ficha de una tarea o incidencia.')); return; }
    const note = h('input', { placeholder: 'Nota opcional (qué has hecho)', maxlength: 500 });
    const stop = h('button', { class: 'btn danger' }, icon('stop'), 'Parar');
    stop.addEventListener('click', async () => {
      try {
        const r = await busy(stop, () => api.post('/api/time/stop', { note: note.value.trim() }));
        if (r.stopped) toast(`Cronómetro parado: ${fmtDuration(r.seconds)} registrados.`, 'ok');
        drawCurrent(); drawList();
      } catch { /* aviso mostrado */ }
    });
    mount(current, h('div', { class: 'card timer-card mb' },
      h('div', { class: 'row wrap' }, h('span', { class: 'badge ok' }, icon('play', 12), 'En marcha'),
        h('a', { href: entityHash(e.entity_type, e.entity_id, e.key) }, h('b', null, e.key || '')), h('span', { class: 'grow ellipsis' }, e.title || ''),
        h('b', { class: 'mono big-clock', 'data-since': e.started_at }, clock(elapsed(e.started_at)))),
      h('div', { class: 'row mt' }, note, stop),
      h('div', { class: 'tiny muted mt' }, `Empezó ${fmtDateTime(e.started_at)}.`)));
  }

  async function drawList() {
    mount(list, loading());
    let d;
    try { d = await api.get(`/api/time?user_id=${userId}&from=${from}&to=${to}`); } catch (err) { mount(list, errorBox(err, drawList)); return; }
    const own = userId === me.id;
    const byDay = new Map();
    for (const e of d.items) {
      const k = (e.started_at || '').slice(0, 10);
      byDay.set(k, (byDay.get(k) || 0) + (e.seconds ?? elapsed(e.started_at)));
    }
    const cols = [
      { label: 'Día', cls: 'nowrap', render: e => fmtDate(e.started_at) },
      { label: 'Elemento', render: e => h('a', { class: 'row', href: entityHash(e.entity_type, e.entity_id, e.key), onclick: (ev) => ev.stopPropagation() },
        typeBadge(e.entity_type), h('b', { class: 'small nowrap' }, e.key || '—'), h('span', { class: 'ellipsis small' }, e.title || '')) },
      { label: 'Duración', cls: 'nowrap', render: e => e.ended_at ? fmtDuration(e.seconds || 0) : h('span', { class: 'ok-text' }, 'en marcha') },
      { label: 'Origen', cls: 'nowrap hide-sm', render: e => h('span', { class: 'badge neutral' }, e.source === 'manual' ? 'Manual' : 'Cronómetro') },
      { label: 'Nota', render: e => h('span', { class: 'small' }, e.note || '') },
      { label: '', cls: 'nowrap', render: e => own && e.ended_at ? h('button', { class: 'btn ghost sm icon', title: 'Borrar el registro',
        onclick: (ev) => { ev.stopPropagation(); remove(ev.currentTarget, e); } }, icon('trash')) : null },
    ];
    mount(list,
      h('div', { class: 'stats' },
        h('div', { class: 'stat' }, h('div', { class: 'n' }, fmtDuration(d.total_seconds || 0)), h('div', { class: 'l' }, `Total del ${fmtDate(from)} al ${fmtDate(to)}`)),
        h('div', { class: 'stat' }, h('div', { class: 'n' }, d.items.length), h('div', { class: 'l' }, 'Registros')),
        h('div', { class: 'stat' }, h('div', { class: 'n' }, byDay.size ? fmtDuration(Math.round((d.total_seconds || 0) / byDay.size)) : '—'), h('div', { class: 'l' }, 'Media por día con registros'))),
      byDay.size > 1 ? h('div', { class: 'row wrap mb' }, [...byDay.entries()].sort().map(([k, s]) => h('span', { class: 'chip' }, `${fmtDate(k)}: ${fmtDuration(s)}`))) : null,
      table(cols, d.items, { empty: own ? 'No has registrado tiempo en estas fechas.' : 'Sin registros de tiempo en estas fechas.' }));
  }

  async function remove(btn, e) {
    const ok = await confirmDialog({ title: 'Borrar el registro de tiempo', danger: true, confirmLabel: 'Borrar',
      message: `¿Borrar ${fmtDuration(e.seconds || 0)} registrados en ${e.key || 'este elemento'} el ${fmtDate(e.started_at)}?` });
    if (!ok) return;
    try { await busy(btn, () => api.del(`/api/time/${e.id}`), 'Registro borrado.'); drawList(); } catch { /* aviso mostrado */ }
  }

  function manual() {
    const type = selectEl([['task', 'Tarea'], ['incident', 'Incidencia']], 'task');
    const key = h('input', { placeholder: 'T-004 o APP-012', maxlength: 30 });
    const date = h('input', { type: 'date', value: todayISO(), max: todayISO() });
    const hours = h('input', { type: 'number', min: 0, max: 24, step: 1, value: 1 });
    const mins = h('input', { type: 'number', min: 0, max: 59, step: 5, value: 0 });
    const note = h('input', { maxlength: 500, placeholder: 'Qué hiciste' });
    key.addEventListener('input', () => { if (/^T-/i.test(key.value)) type.value = 'task'; else if (/^[A-Z][A-Z0-9]*-\d/i.test(key.value)) type.value = 'incident'; });
    modal({ title: 'Añadir tiempo a mano', body: h('div', { class: 'form-grid' },
      field('Clave del elemento', key, { required: true, hint: 'Por ejemplo T-004 (tarea) o APP-012 (incidencia).' }), field('Tipo', type),
      field('Día', date, { required: true }), h('div', { class: 'row' }, field('Horas', hours), field('Minutos', mins)),
      field('Nota', note, { cls: 'span2' })),
    actions: [{ label: 'Cancelar' }, { label: 'Añadir', kind: 'primary', onClick: async () => {
      const minutes = (Number(hours.value) || 0) * 60 + (Number(mins.value) || 0);
      if (!key.value.trim()) { toast('Indica la clave de la tarea o incidencia.', 'error'); key.focus(); return false; }
      if (minutes < 1 || minutes > 1440) { toast('La duración debe estar entre 1 minuto y 24 horas.', 'error'); return false; }
      try {
        const r = await api.post('/api/time', { uid: uid(), entity_type: type.value, entity: key.value.trim().toUpperCase(), date: date.value, minutes, note: note.value.trim() }, { queue: true });
        toast(r._queued ? 'Sin conexión: se guardará al reconectar.' : 'Tiempo añadido.', r._queued ? 'warn' : 'ok');
        drawList();
      } catch (err) { toast(err.message, 'error'); return false; }
    } }] });
  }

  mount(body, current,
    h('div', { class: 'filters' },
      who ? h('div', { class: 'group' }, h('span', null, 'Persona'), who) : null,
      h('div', { class: 'group' }, h('span', null, 'Desde'), fromIn), h('div', { class: 'group' }, h('span', null, 'Hasta'), toIn),
      h('div', { class: 'group' }, quick('Hoy', todayISO(), todayISO()), quick('Esta semana', monday, todayISO()),
        quick('Últimos 7 días', addDays(todayISO(), -6), todayISO()), quick('Últimos 30 días', addDays(todayISO(), -29), todayISO())),
      h('div', { class: 'grow' }),
      canLog ? h('button', { class: 'btn sm', onclick: manual }, icon('plus'), 'Añadir tiempo a mano') : null),
    list);
  await Promise.all([drawCurrent(), drawList()]);
  ctx.onChanges(['time'], () => { drawCurrent(); drawList(); });
}

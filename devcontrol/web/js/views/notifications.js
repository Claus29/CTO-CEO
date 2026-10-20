// Avisos: lo que te ha llegado (asignaciones, menciones, verificaciones, plazos…).
import { api } from '../api.js';
import { openNotification, refreshCounters, store } from '../state.js';
import { busy, emptyState, errorBox, fmtDateTime, fmtDayLabel, fmtRelative, h, icon, loadJSON, loading, mount, pageHead, saveJSON } from '../ui.js';

const TYPE_ICONS = {
  incident_created: 'bug', incident_critical: 'alert', assigned: 'user', mention: 'chat', verify_request: 'verify', verified: 'check',
  rejected: 'x', incident_reopened: 'undo', incident_seen: 'eye', incident_blocked: 'lock', comment: 'reply', message_new: 'chat',
  dependency_unblocked: 'play', sla: 'clock', due_soon: 'calendar', incident_stale: 'clock', renewal: 'repeat', store: 'store',
  standup: 'week', reminder: 'bell', daily_summary: 'dashboard', weekly_summary: 'chart', system: 'shield',
};
const STEP = 50;
const MAX = 200;

export default async function render(ctx) {
  const { root } = ctx;
  let onlyUnread = loadJSON('dc.notif.unread', false);
  let limit = STEP, items = [], unread = 0, token = 0;
  const typeLabel = (t) => store.prefs?.items?.find(i => i.type === t)?.label || '';

  const seg = h('div', { class: 'seg' });
  const drawSeg = () => mount(seg, [[false, 'Todos'], [true, 'No leídos']].map(([v, l]) =>
    h('button', { class: onlyUnread === v ? 'on' : '', onclick: () => { onlyUnread = v; saveJSON('dc.notif.unread', v); limit = STEP; drawSeg(); load(); } }, l)));
  drawSeg();
  const markAll = h('button', { class: 'btn', onclick: () => busy(markAll, () => api.post('/api/notifications/read', { all: true }), 'Todos los avisos marcados como leídos.')
    .then(() => { refreshCounters(); load(); }).catch(() => {}) }, icon('check'), 'Marcar todos como leídos');
  const counter = h('span', { class: 'small muted' });
  const list = h('div');
  const more = h('div', { class: 'row end mt' });
  mount(root,
    pageHead('Avisos', h('a', { class: 'btn ghost', href: '#/cuenta/avisos' }, icon('settings'), 'Preferencias'), markAll),
    h('div', { class: 'toolbar' }, seg, counter),
    list, more);

  async function load() {
    const my = ++token;
    if (!items.length) mount(list, loading());
    try {
      const r = await api.get(`/api/notifications?limit=${limit}${onlyUnread ? '&unread=1' : ''}`);
      if (my !== token || !ctx.alive()) return;
      items = r.items; unread = r.unread;
      draw();
    } catch (err) { mount(list, errorBox(err, load)); }
  }

  async function markRead(n, btn) {
    await busy(btn, () => api.post('/api/notifications/read', { ids: [n.id] })).catch(() => null);
    refreshCounters();
    load();
  }

  function draw() {
    counter.textContent = unread ? `${unread} sin leer` : 'Todo leído';
    markAll.disabled = !unread;
    if (!items.length) {
      mount(list, emptyState(onlyUnread ? 'No tienes avisos sin leer' : 'Aún no tienes avisos',
        'Te avisaremos cuando te asignen algo, te mencionen, haya algo que verificar o se acerque un plazo.'));
      mount(more);
      return;
    }
    const groups = [];
    for (const n of items) {
      const day = new Date(n.created_at).toDateString();
      if (!groups.length || groups[groups.length - 1].day !== day) groups.push({ day, label: fmtDayLabel(n.created_at), rows: [] });
      groups[groups.length - 1].rows.push(n);
    }
    mount(list, groups.map(g => h('div', { class: 'mb' },
      h('div', { class: 'section-title' }, g.label),
      h('div', { class: 'list-card' }, g.rows.map(n => h('div', {
        class: `notif adm-notif${n.read_at ? '' : ' unread'}${n.urgent ? ' urgent' : ''}`, role: 'button', tabindex: '0',
        onclick: () => openNotification(n), onkeydown: (e) => { if (e.key === 'Enter') openNotification(n); },
      },
      h('span', { class: `adm-nicon${n.urgent ? ' urgent' : ''}` }, icon(TYPE_ICONS[n.type] || 'bell', 16)),
      h('div', { class: 'grow' },
        h('div', { class: 'row wrap' }, h('span', { class: 'nt' }, n.title), n.urgent ? h('span', { class: 'badge danger' }, 'Urgente') : null),
        n.body ? h('div', { class: 'small adm-nbody' }, n.body) : null,
        h('div', { class: 'tiny muted' }, [typeLabel(n.type), fmtRelative(n.created_at)].filter(Boolean).join(' · '), h('span', { title: fmtDateTime(n.created_at) }))),
      n.read_at ? null : h('button', { class: 'btn sm ghost', title: 'Marcar como leído', onclick: (e) => { e.stopPropagation(); markRead(n, e.currentTarget); } }, icon('check', 14), 'Leído')))))));
    if (items.length >= limit && limit < MAX) mount(more, h('button', { class: 'btn', onclick: () => { limit = Math.min(MAX, limit + STEP); load(); } }, 'Cargar más'));
    else if (items.length >= MAX) mount(more, h('span', { class: 'tiny muted' }, `Se muestran los ${MAX} avisos más recientes.`));
    else mount(more);
  }

  await load();
  ctx.onChanges(['notification'], load);
}

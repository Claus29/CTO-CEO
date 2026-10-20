// Panel: lo que necesita tu atención ahora (por verificar, asignado a ti, bloqueos, vencidos).
import { api } from '../api.js';
import { can, store, userById, withProject } from '../state.js';
import { avatar, emptyState, errorBox, fmtDate, fmtRelative, h, icon, mount, pageHead, priorityBadge, statusBadge, typeBadge } from '../ui.js';

const href = (it) => it.type === 'task' ? `#/tareas/${it.key}` : `#/incidencias/${it.key}`;

function item(it, extra) {
  return h('a', { class: 'list-item', href: href(it) },
    h('span', { class: 'project-dot', style: { background: it.project_color || 'var(--muted)' } }),
    h('span', { class: 'key' }, it.key),
    h('span', { class: 'grow ellipsis' }, it.title),
    extra ? extra(it) : null,
    it.priority ? priorityBadge(it.priority) : null,
    statusBadge(it.status));
}

function listCard(title, items, { empty = 'Nada por aquí', extra, more } = {}) {
  return h('div', { class: 'list-card' },
    h('div', { class: 'card-head' }, h('h3', null, title), h('span', { class: 'muted small' }, items.length || ''),
      more ? h('a', { class: 'small', href: more }, 'Ver todo') : null),
    items.length ? h('div', null, items.map(it => item(it, extra))) : h('div', { class: 'empty small' }, empty));
}

export default async function render(ctx) {
  const { root } = ctx;
  const draw = async () => {
    let d;
    try { d = await api.get(`/api/dashboard?${withProject()}`); } catch (err) { mount(root, errorBox(err, draw)); return; }
    const c = d.counts;
    const stat = (n, label, hash, cls = '', sub = '') => h('a', { class: `stat ${n ? cls : ''}`, href: hash },
      h('div', { class: 'n' }, n), h('div', { class: 'l' }, label), sub ? h('div', { class: 'sub' }, sub) : null);
    const stats = h('div', { class: 'stats' },
      stat(c.to_verify, 'Por verificar', '#/incidencias?to_verify=1', 'accent'),
      can('incidencias.ver') && stat(c.critical, 'Críticas abiertas', '#/incidencias?priority=critica&open=1', 'alert'),
      stat(c.blocked, 'Bloqueadas', '#/incidencias?blocked=1', 'alert'),
      stat(c.overdue, 'Vencidas', '#/incidencias?overdue=1', 'warn'),
      can('incidencias.ver') && stat(c.open, 'Incidencias abiertas', '#/incidencias?open=1', '', c.unassigned ? `${c.unassigned} sin responsable` : ''),
      can('tareas.ver') && stat(c.tasks_in_progress, 'Tareas en progreso', '#/tareas?status=en_progreso', '', `${c.tasks_pending} pendientes`),
      can('incidencias.ver') && stat(c.resolved_week, 'Resueltas esta semana', '#/incidencias?status=resuelta', 'ok'));
    const people = store.users.filter(u => u.id !== store.me.id);
    mount(root,
      pageHead(`Hola, ${store.me.display_name.split(' ')[0]}`,
        can('incidencias.crear') ? h('a', { class: 'btn primary', href: '#/incidencias/nueva' }, icon('plus'), 'Nueva incidencia') : null,
        can('tareas.crear') ? h('a', { class: 'btn', href: '#/tareas?nueva=1' }, icon('plus'), 'Nueva tarea') : null),
      store.onboardingPending ? h('a', { class: 'callout info row mb', href: '#/cuenta/primeros-pasos' }, icon('flag', 16),
        `Te quedan ${store.onboardingPending} pasos de bienvenida por completar.`) : null,
      stats,
      h('div', { class: 'grid cols-2' },
        listCard('Pendiente de que verifiques', d.to_verify, {
          empty: 'No hay nada esperando tu verificación.',
          extra: (it) => h('span', { class: 'tiny muted nowrap' }, it.verification_requested_at ? fmtRelative(it.verification_requested_at) : ''),
          more: '#/incidencias?to_verify=1',
        }),
        listCard('Asignado a ti', d.mine, { empty: 'No tienes nada asignado.', more: '#/incidencias?assignee=me&open=1' }),
        d.unseen.length ? listCard('Asignado a ti y aún sin abrir', d.unseen) : null,
        listCard('Esperando verificación de otros', d.waiting_verification, { empty: 'Nada tuyo pendiente de verificar.' }),
        listCard('Bloqueos', d.blocked, {
          empty: 'Nada bloqueado.',
          extra: (it) => h('span', { class: 'tiny danger-text ellipsis', style: { maxWidth: '180px' } }, it.block_reason_label || it.block_reason || ''),
        }),
        listCard('Críticas y altas abiertas', d.priority_open, { empty: 'Sin incidencias críticas ni altas.' }),
        listCard('Vencidas', d.overdue, { empty: 'Nada vencido.', extra: (it) => h('span', { class: 'tiny overdue' }, fmtDate(it.target_date)) }),
        h('div', { class: 'list-card' }, h('div', { class: 'card-head' }, h('h3', null, 'Próximas fechas (14 días)')),
          d.upcoming.length ? h('div', null, d.upcoming.map(u => h('a', { class: 'list-item', href: href(u) },
            h('span', { class: 'nowrap small' }, fmtDate(u.target_date)), typeBadge(u.type), h('span', { class: 'key' }, u.key),
            h('span', { class: 'grow ellipsis' }, u.title), statusBadge(u.status)))) : h('div', { class: 'empty small' }, 'Sin fechas próximas.')),
        h('div', { class: 'list-card' }, h('div', { class: 'card-head' }, h('h3', null, 'Equipo'),
          h('a', { class: 'small', href: '#/equipo' }, 'Ver equipo')),
          people.length ? h('div', null, people.map(u => h('div', { class: 'list-item' }, avatar(u, u.online),
            h('div', { class: 'grow' }, h('div', null, u.display_name), h('div', { class: 'tiny muted ellipsis' },
              u.online ? (u.last_action || 'En línea') : `Visto ${fmtRelative(u.last_seen_at)}`)),
            h('span', { class: 'tiny muted' }, u.role_label || ''))))
            : emptyState('Aún estás solo', can('sistema.usuarios') ? 'Invita a tu equipo desde «Usuarios y roles».' : '')),
        d.resolved_recent.length ? listCard('Resueltas recientemente', d.resolved_recent) : null));
    void userById;
  };
  await draw();
  ctx.onChanges(['incident', 'task', 'notification', 'user', 'presence', 'project'], draw);
  ctx.on('presence', () => {});
}

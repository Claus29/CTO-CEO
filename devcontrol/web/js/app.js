// Arranque de la interfaz: conexión, inicio de sesión, estructura y navegación.
import { api, ApiError, emit, on, setServerKey } from './api.js';
import {
  avatar, busy, clear, debounce, emptyState, errorBox, field, fmtRelative, h, icon, loadJSON, loading, modal, mount,
  openKey, saveJSON, toast, typeBadge,
} from './ui.js';
import {
  can, canAny, entityHash, forgetSession, loadSession, openNotification, pollLocal, refreshCounters, setProject, startSync, stopSync, store,
} from './state.js';
import { callsAvailable, inCall, openCallsPanel, startCalls, stopCalls } from './calls.js';

const root = document.getElementById('app');

// ---------- tema ----------
function applyTheme(t) { document.documentElement.dataset.theme = t; saveJSON('dc.theme', t); }
applyTheme(loadJSON('dc.theme', 'dark'));

// ---------- rutas ----------
// Cada vista es un módulo de js/views con `export default async function render(ctx)`.
const ROUTES = [
  { path: 'panel', view: 'dashboard', title: 'Panel' },
  { path: 'incidencias', view: 'incidents', title: 'Incidencias', perm: 'incidencias.ver' },
  { path: 'incidencias/nueva', view: 'incident_form', title: 'Nueva incidencia', perm: 'incidencias.crear' },
  { path: 'incidencias/:key', view: 'incident_detail', title: 'Incidencia', perm: 'incidencias.ver' },
  { path: 'tareas', view: 'tasks', title: 'Tareas', perm: 'tareas.ver' },
  { path: 'tareas/:key', view: 'task_detail', title: 'Tarea', perm: 'tareas.ver' },
  { path: 'ideas', view: 'ideas', title: 'Ideas', perm: 'planificacion.ver' },
  { path: 'semana', view: 'weekly', title: 'Plan semanal', perm: 'planificacion.ver' },
  { path: 'versiones', view: 'versions', title: 'Versiones', perm: 'planificacion.ver' },
  { path: 'roadmap', view: 'roadmap', title: 'Roadmap', perm: 'planificacion.ver' },
  { path: 'calendario', view: 'calendar', title: 'Calendario', perm: 'planificacion.ver' },
  { path: 'renovaciones', view: 'renewals', title: 'Renovaciones', perm: 'planificacion.ver' },
  { path: 'builds', view: 'builds', title: 'Builds', perm: 'calidad.ver' },
  { path: 'pruebas', view: 'tests', title: 'Planes de prueba', perm: 'calidad.ver' },
  { path: 'tiendas', view: 'stores', title: 'Publicaciones en tiendas', perm: 'calidad.ver' },
  { path: 'chat', view: 'chat', title: 'Chat', perm: 'chat.usar' },
  { path: 'equipo', view: 'team', title: 'Equipo' },
  { path: 'equipo/:tab', view: 'team', title: 'Equipo' },
  { path: 'wiki', view: 'wiki', title: 'Wiki', perm: 'conocimiento.ver' },
  { path: 'wiki/:id', view: 'wiki', title: 'Wiki', perm: 'conocimiento.ver' },
  { path: 'decisiones', view: 'decisions', title: 'Decisiones', perm: 'conocimiento.ver' },
  { path: 'decisiones/:key', view: 'decisions', title: 'Decisiones', perm: 'conocimiento.ver' },
  { path: 'boveda', view: 'vault', title: 'Bóveda', perm: 'boveda.ver' },
  { path: 'informes', view: 'reports', title: 'Informes', perm: 'informes.ver' },
  { path: 'actividad', view: 'activity', title: 'Actividad', anyPerm: ['actividad.ver_propia', 'actividad.ver_todos'] },
  { path: 'avisos', view: 'notifications', title: 'Avisos' },
  { path: 'admin', view: 'admin', title: 'Usuarios y roles', anyPerm: ['sistema.usuarios', 'proyectos.gestionar'] },
  { path: 'admin/:tab', view: 'admin', title: 'Usuarios y roles', anyPerm: ['sistema.usuarios', 'proyectos.gestionar'] },
  { path: 'ajustes', view: 'settings', title: 'Ajustes', anyPerm: ['sistema.ajustes', 'sistema.copias', 'sistema.integraciones'] },
  { path: 'ajustes/:tab', view: 'settings', title: 'Ajustes', anyPerm: ['sistema.ajustes', 'sistema.copias', 'sistema.integraciones'] },
  { path: 'cuenta', view: 'account', title: 'Mi cuenta' },
  { path: 'cuenta/:tab', view: 'account', title: 'Mi cuenta' },
];

const NAV = [
  { section: null, items: [
    { href: '#/panel', label: 'Panel', icon: 'dashboard' },
    { href: '#/incidencias', label: 'Incidencias', icon: 'bug', perm: 'incidencias.ver', count: 'verify' },
    { href: '#/tareas', label: 'Tareas', icon: 'task', perm: 'tareas.ver' },
    { href: '#/chat', label: 'Chat', icon: 'chat', perm: 'chat.usar', count: 'messages' },
  ] },
  { section: 'Planificación', items: [
    { href: '#/ideas', label: 'Ideas', icon: 'bulb', perm: 'planificacion.ver' },
    { href: '#/semana', label: 'Plan semanal', icon: 'week', perm: 'planificacion.ver' },
    { href: '#/versiones', label: 'Versiones', icon: 'tag', perm: 'planificacion.ver' },
    { href: '#/roadmap', label: 'Roadmap', icon: 'map', perm: 'planificacion.ver' },
    { href: '#/calendario', label: 'Calendario', icon: 'calendar', perm: 'planificacion.ver' },
    { href: '#/renovaciones', label: 'Renovaciones', icon: 'repeat', perm: 'planificacion.ver' },
  ] },
  { section: 'Calidad', items: [
    { href: '#/builds', label: 'Builds', icon: 'box', perm: 'calidad.ver' },
    { href: '#/pruebas', label: 'Planes de prueba', icon: 'flask', perm: 'calidad.ver' },
    { href: '#/tiendas', label: 'Tiendas', icon: 'store', perm: 'calidad.ver' },
  ] },
  { section: 'Equipo', items: [
    { href: '#/equipo', label: 'Equipo', icon: 'users' },
    { href: '#/wiki', label: 'Wiki', icon: 'book', perm: 'conocimiento.ver' },
    { href: '#/decisiones', label: 'Decisiones', icon: 'scale', perm: 'conocimiento.ver' },
    { href: '#/boveda', label: 'Bóveda', icon: 'key', perm: 'boveda.ver' },
    { href: '#/informes', label: 'Informes', icon: 'chart', perm: 'informes.ver' },
    { href: '#/actividad', label: 'Actividad', icon: 'activity', anyPerm: ['actividad.ver_propia', 'actividad.ver_todos'] },
  ] },
  { section: 'Administración', items: [
    { href: '#/admin', label: 'Usuarios y roles', icon: 'shield', anyPerm: ['sistema.usuarios', 'proyectos.gestionar'] },
    { href: '#/ajustes', label: 'Ajustes', icon: 'settings', anyPerm: ['sistema.ajustes', 'sistema.copias', 'sistema.integraciones'] },
  ] },
];

const allowed = (r) => (!r.perm || can(r.perm)) && (!r.anyPerm || canAny(...r.anyPerm));

function matchRoute(hash) {
  const [pathPart, queryPart] = hash.replace(/^#\/?/, '').split('?');
  const segs = pathPart.split('/').filter(Boolean);
  for (const r of ROUTES) {
    const rs = r.path.split('/');
    if (rs.length !== segs.length) continue;
    const params = {};
    let ok = true;
    rs.forEach((s, i) => {
      if (s.startsWith(':')) params[s.slice(1)] = decodeURIComponent(segs[i]);
      else if (s !== segs[i]) ok = false;
    });
    if (ok) return { route: r, params, query: new URLSearchParams(queryPart || '') };
  }
  return null;
}

// ---------- estructura principal ----------
let shell = null;
let viewCleanup = [];
let renderToken = 0;

function buildShell() {
  const nav = h('nav', { class: 'nav' });
  const projectSel = h('select', { class: 'project-switch', title: 'Proyecto', onchange: (e) => setProject(e.target.value) });
  const connPill = h('button', { class: 'pill clickable', title: 'Estado de la conexión', onclick: showConnection });
  const bell = h('button', { class: 'btn ghost icon bell', title: 'Avisos', onclick: () => { location.hash = '#/avisos'; } }, icon('bell'));
  const timer = h('button', { class: 'pill clickable timer hidden', title: 'Cronómetro en marcha', onclick: () => { location.hash = '#/equipo/tiempo'; } });
  const title = h('div', { class: 'title' });
  const content = h('main', { class: 'content', id: 'content' });
  const banner = h('div', { class: 'offline-banner hidden' });
  const obsolete = h('div', { class: 'obsolete-banner hidden', role: 'status' });
  const callsBtn = h('button', { class: 'btn ghost icon calls-btn hidden', title: 'Llamadas', onclick: openCallsPanel }, icon('phone'));
  const me = h('div', { class: 'me' });
  const sidebar = h('aside', { class: 'sidebar' },
    h('a', { class: 'brand', href: '#/panel' }, h('span', { class: 'logo' }, 'DEV', h('b', null, ' CONTROL'))),
    nav, h('div', { class: 'spacer' }), me);
  const top = h('header', { class: 'topbar' },
    h('button', { class: 'btn ghost icon only-narrow', title: 'Menú', onclick: () => document.body.classList.toggle('nav-open') }, icon('menu')),
    title, h('div', { class: 'grow' }),
    projectSel,
    h('button', { class: 'search-btn', onclick: openPalette, title: 'Buscar (Ctrl+K)' }, icon('search', 15), h('span', null, 'Buscar…'), h('kbd', null, 'Ctrl K')),
    timer, connPill, callsBtn, bell);
  const el = h('div', { class: 'app' }, sidebar, h('div', { class: 'main' }, top, obsolete, banner, content));
  mount(root, el);
  shell = { nav, projectSel, connPill, bell, timer, title, content, banner, me, obsolete, callsBtn };
  renderNav(); renderProjects(); renderMe(); renderConn(); renderCounters();
}

function renderNav() {
  if (!shell) return;
  const current = location.hash.split('?')[0];
  clear(shell.nav);
  for (const group of NAV) {
    const items = group.items.filter(allowed);
    if (!items.length) continue;
    if (group.section) shell.nav.appendChild(h('div', { class: 'nav-section' }, group.section));
    for (const it of items) {
      let count = null;
      if (it.count === 'messages' && store.unreadMessages) count = h('span', { class: 'count accent' }, store.unreadMessages);
      const active = current === it.href || current.startsWith(it.href + '/');
      shell.nav.appendChild(h('a', { href: it.href, class: active ? 'active' : '', onclick: () => document.body.classList.remove('nav-open') },
        icon(it.icon), it.label, count));
    }
  }
}

function renderProjects() {
  if (!shell) return;
  const sel = shell.projectSel;
  clear(sel);
  sel.appendChild(h('option', { value: 'all' }, 'Todos los proyectos'));
  for (const p of store.projects) sel.appendChild(h('option', { value: p.id }, `${p.key} · ${p.name}${p.archived ? ' (archivado)' : ''}`));
  sel.value = String(store.project);
  sel.classList.toggle('hidden', store.projects.length < 2);
}

function renderMe() {
  if (!shell) return;
  const u = store.me;
  mount(shell.me,
    h('a', { class: 'me-link', href: '#/cuenta', title: 'Mi cuenta' }, avatar(u, true),
      h('div', { class: 'grow' }, h('div', { class: 'name' }, u.display_name), h('div', { class: 'tiny muted' }, u.role_label || ''))),
    store.onboardingPending ? h('a', { class: 'badge accent', href: '#/cuenta/primeros-pasos', title: 'Primeros pasos pendientes' }, store.onboardingPending) : null,
    h('button', { class: 'btn ghost sm icon', title: 'Cambiar tema', onclick: () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark') }, icon('moon')),
    h('button', { class: 'btn ghost sm icon', title: 'Cerrar sesión', onclick: logout }, icon('logout')));
}

function renderConn() {
  if (!shell) return;
  const s = store.local;
  const pill = shell.connPill;
  clear(pill);
  let cls = 'bad', text = 'Sin conexión';
  if (s && s.reachable) { cls = s.pending_count ? 'warn' : 'ok'; text = s.pending_count ? `Enviando ${s.pending_count}…` : 'Conectado'; }
  else if (s && s.pending_count) text = `Sin conexión · ${s.pending_count} en cola`;
  pill.className = `pill clickable ${cls}`;
  pill.append(h('span', { class: 'led' }), text);
  const b = shell.banner;
  if (s && !s.reachable) {
    mount(b, icon('wifi', 16), h('b', null, 'Sin conexión con el servidor.'),
      h('span', null, s.last_error || 'Reintentando…'),
      h('span', { class: 'muted' }, 'Puedes seguir trabajando: los cambios se guardan y se envían al reconectar.'));
    b.classList.remove('hidden');
  } else b.classList.add('hidden');
}

function renderCounters() {
  if (!shell) return;
  shell.bell.classList.toggle('has', store.unreadNotifications > 0);
  shell.bell.dataset.count = store.unreadNotifications > 99 ? '99+' : store.unreadNotifications || '';
  renderNav();
}

async function renderTimer() {
  if (!shell) return;
  try {
    const t = await api.get('/api/time/current');
    const entry = t.entry;
    if (!entry) { shell.timer.classList.add('hidden'); return; }
    mount(shell.timer, icon('clock', 14), h('span', null, entry.key || 'Cronómetro'));
    shell.timer.classList.remove('hidden');
  } catch { /* sin conexión */ }
}

// ---------- navegación ----------
async function route() {
  if (!shell) return;
  const hash = location.hash || '#/panel';
  const m = matchRoute(hash);
  for (const fn of viewCleanup) { try { fn(); } catch { /* ignorado */ } }
  viewCleanup = [];
  document.querySelectorAll('.menu').forEach(x => x.remove());
  document.body.classList.remove('nav-open');
  renderNav();
  const token = ++renderToken;
  const content = shell.content;
  content.className = 'content';
  if (!m) { mount(content, emptyState('Página no encontrada', 'Usa el menú de la izquierda.')); return; }
  if (!allowed(m.route)) { mount(content, emptyState('Sin permiso', 'Tu rol no permite ver esta sección.')); return; }
  shell.title.textContent = m.route.title;
  document.title = `${m.route.title} · DEV CONTROL`;
  mount(content, loading());
  const ctx = {
    root: content, params: m.params, query: m.query,
    setTitle: (t) => { if (token === renderToken) { shell.title.textContent = t; document.title = `${t} · DEV CONTROL`; } },
    // Vuelve a ejecutar fn cuando el servidor avisa de cambios de esos tipos (se limpia al cambiar de página).
    onChanges: (kinds, fn) => {
      const set = new Set(kinds);
      const handler = debounce(() => { if (token === renderToken) fn(); }, 250);
      viewCleanup.push(on('changes', (d) => { if ([...d.kinds].some(k => set.has(k))) handler(); }));
      viewCleanup.push(on('reset', handler));
      viewCleanup.push(on('reconnected', handler));
    },
    on: (name, fn) => viewCleanup.push(on(name, fn)),
    refresh: () => route(),
    alive: () => token === renderToken,
  };
  try {
    const mod = await import(`./views/${m.route.view}.js`);
    if (token !== renderToken) return;
    clear(content);
    const cleanup = await mod.default(ctx);
    if (typeof cleanup === 'function') viewCleanup.push(cleanup);
  } catch (err) {
    if (token !== renderToken) return;
    console.error(err);
    mount(content, errorBox(err, () => route()));
  }
}

// ---------- búsqueda global (Ctrl+K) ----------
function openPalette() {
  if (document.querySelector('.palette')) return;
  const input = h('input', { placeholder: 'Busca incidencias, tareas, wiki, decisiones, mensajes o personas… (o escribe APP-012)', autocomplete: 'off' });
  const list = h('div', { class: 'palette-list' });
  let items = [], sel = 0;
  const actions = [
    can('incidencias.crear') && { title: 'Nueva incidencia', hash: '#/incidencias/nueva', type: 'action' },
    can('tareas.crear') && { title: 'Nueva tarea', hash: '#/tareas?nueva=1', type: 'action' },
    { title: 'Mi panel', hash: '#/panel', type: 'action' },
    { title: 'Mi cuenta', hash: '#/cuenta', type: 'action' },
  ].filter(Boolean);
  const draw = () => {
    clear(list);
    if (!items.length) { list.appendChild(h('div', { class: 'empty small' }, input.value ? 'Sin resultados' : 'Escribe para buscar')); return; }
    items.forEach((it, i) => list.appendChild(h('button', { class: 'palette-item' + (i === sel ? ' on' : ''), onmouseenter: () => { sel = i; draw(); }, onclick: () => go(it) },
      it.type === 'action' ? h('span', { class: 'badge neutral' }, 'Ir a') : typeBadge(it.type),
      it.key ? h('b', { class: 'key' }, it.key) : null,
      h('span', { class: 'grow ellipsis' }, it.title),
      it.snippet ? h('span', { class: 'tiny muted ellipsis snippet' }, it.snippet.replace(/\[|\]/g, '')) : null)));
  };
  const go = (it) => {
    close();
    if (it.hash) location.hash = it.hash;
    else if (it.type === 'person') location.hash = `#/equipo?persona=${it.id}`;
    else location.hash = entityHash(it.type, it.id, it.key);
  };
  const search = debounce(async () => {
    const q = input.value.trim();
    if (!q) { items = actions; sel = 0; draw(); return; }
    try {
      const r = await api.get(`/api/search?q=${encodeURIComponent(q)}`);
      items = r.items; sel = 0; draw();
    } catch (err) { mount(list, h('div', { class: 'empty small' }, err.message)); }
  }, 180);
  input.addEventListener('input', search);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { sel = Math.min(items.length - 1, sel + 1); draw(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { sel = Math.max(0, sel - 1); draw(); e.preventDefault(); }
    else if (e.key === 'Enter' && items[sel]) go(items[sel]);
    else if (e.key === 'Escape') close();
  });
  const ov = h('div', { class: 'overlay palette-overlay', onmousedown: (e) => { if (e.target === ov) close(); } },
    h('div', { class: 'palette' }, h('div', { class: 'palette-input' }, icon('search', 18), input), list));
  function close() { ov.remove(); }
  document.body.appendChild(ov);
  items = actions; draw();
  input.focus();
}

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && shell) { e.preventDefault(); openPalette(); }
});

// ---------- estado de la conexión y cola ----------
function showConnection() {
  const s = store.local || {};
  const body = h('div', { class: 'col gap-lg' });
  const draw = () => {
    const st = store.local || {};
    mount(body,
      h('div', { class: 'kv' },
        h('span', null, 'Servidor'), h('b', null, st.server_label || st.server || '—'),
        h('span', null, 'Estado'), h('b', { class: st.reachable ? 'ok-text' : 'danger-text' }, st.reachable ? 'Conectado' : 'Sin conexión'),
        h('span', null, 'Última conexión correcta'), h('span', null, st.last_ok_at ? fmtRelative(st.last_ok_at) : '—'),
        h('span', null, 'Versión del servidor'), h('span', null, st.server_info?.version || '—'),
        h('span', null, 'Versión de este equipo'), h('span', null, st.version || '—')),
      st.last_error && !st.reachable ? h('div', { class: 'callout danger' }, st.last_error) : null,
      h('h3', null, `Cambios pendientes de enviar (${(st.outbox || []).length})`),
      (st.outbox || []).length ? h('div', { class: 'col' }, st.outbox.map(o => h('div', { class: 'outbox-item' },
        h('div', { class: 'grow' }, h('b', null, `${o.method} ${o.path.split('?')[0]}`),
          h('div', { class: 'tiny muted' }, `${fmtRelative(o.created_at)} · ${o.status === 'failed' ? 'rechazado' : 'pendiente'}${o.last_error ? ' · ' + o.last_error : ''}`)),
        o.status === 'failed' ? h('button', { class: 'btn sm', onclick: () => api.local('POST', `/local/outbox/${o.id}/retry`).then(pollLocal).then(draw) }, 'Reintentar') : null,
        h('button', { class: 'btn sm ghost', onclick: () => api.local('DELETE', `/local/outbox/${o.id}`).then(pollLocal).then(draw) }, 'Descartar'))))
        : h('div', { class: 'muted small' }, 'No hay nada pendiente.'));
  };
  draw();
  const off = on('local-status', draw);
  modal({ title: 'Conexión', body, wide: true, onClose: off,
    actions: [{ label: 'Reintentar ahora', onClick: async () => { await api.local('POST', '/local/flush'); await pollLocal(); draw(); return false; } },
      { label: 'Cambiar de servidor', onClick: () => { logout(true); } }, { label: 'Cerrar', kind: 'primary' }] });
  void s;
}

// ---------- actualización obligatoria ----------
let updateOverlay = null;
let forcedUpdate = false;
let obsoleteMinimized = false;
let ackedUpdate = false;
function renderUpdate(force) {
  if (force) forcedUpdate = true;
  const u = store.local?.update;
  const mandatory = forcedUpdate || (u && u.mandatory);
  renderObsolete(u, mandatory);
  if (u?.last_update && !ackedUpdate) {
    ackedUpdate = true;
    const lu = u.last_update;
    if (lu.result === 'ok') toast(`DEV CONTROL se ha actualizado a la versión ${lu.to} correctamente.`, 'ok', { title: 'Actualización completada', sticky: true });
    else if (lu.result === 'revertida' || lu.result === 'fallo') toast(`La actualización a ${lu.to} no se completó y se ha vuelto a la versión ${lu.reverted_to || lu.from}. Tus datos están a salvo. Detalle en Documentos\\DEV CONTROL\\Actualizaciones\\registro.txt.`, 'error', { title: 'Actualización revertida', sticky: true });
    api.local('POST', '/local/update/ack').catch(() => {});
  }
  // Nunca se corta una llamada: la ventana obligatoria espera a que se cuelgue.
  if (!mandatory || inCall()) { if (updateOverlay) { updateOverlay.remove(); updateOverlay = null; } return; }
  if (!updateOverlay) { updateOverlay = h('div', { class: 'overlay update-overlay' }); document.body.appendChild(updateOverlay); }
  const st = u || {};
  const progress = st.state === 'downloading' && st.progress != null ? Math.round(st.progress * 100) : null;
  const working = ['downloading', 'verifying', 'applying', 'checking'].includes(st.state);
  const btn = h('button', { class: 'btn primary', disabled: working || !st.can_apply, onclick: () => busy(btn, () => api.local('POST', '/local/update/apply')).then(pollLocal) },
    icon('download'), working ? 'Actualizando…' : 'Actualizar ahora');
  mount(updateOverlay, h('div', { class: 'modal update-box' },
    h('div', { class: 'update-icon' }, icon('rocket', 34)),
    h('h2', null, 'Actualización obligatoria'),
    h('p', null, st.reason || 'Hay una versión nueva de DEV CONTROL. Es obligatorio instalarla para seguir trabajando con el equipo.'),
    autoInfo(st),
    h('div', { class: 'kv' }, h('span', null, 'Versión instalada'), h('b', null, st.current || store.local?.version || '—'),
      h('span', null, 'Versión nueva'), h('b', null, st.latest || st.required || '—')),
    st.notes ? h('details', { class: 'notes' }, h('summary', null, 'Novedades'), h('div', { class: 'pre small' }, st.notes)) : null,
    progress !== null ? h('div', { class: 'progress' }, h('div', { style: { width: `${progress}%` } })) : null,
    st.state_label ? h('div', { class: 'small muted' }, st.state_label) : null,
    st.error ? h('div', { class: 'callout danger' }, st.error) : null,
    h('div', { class: 'small muted' }, 'Tus datos no se tocan: antes de actualizar se hace una copia de seguridad en Documentos\\DEV CONTROL.'),
    h('div', { class: 'row end' }, st.can_apply === false && st.manual_url ? h('a', { class: 'btn', href: st.manual_url, target: '_blank' }, 'Descargar a mano') : null, btn)));
}

// Instalación automática: cuenta atrás, «Posponer 15 min» y motivo de espera (una llamada, un fallo anterior).
function autoInfo(u) {
  if (u?.waiting_reason) return h('div', { class: 'small warn-text' }, u.waiting_reason);
  if (u?.auto_blocked) return h('div', { class: 'small warn-text' }, u.auto_blocked);
  if (u?.auto_in == null) return null;
  const m = Math.floor(u.auto_in / 60), s = String(u.auto_in % 60).padStart(2, '0');
  const later = h('button', { class: 'btn ghost sm', onclick: () => busy(later, () => api.local('POST', '/local/update/postpone', { minutes: 15 }),
    'La actualización se instalará dentro de 15 minutos.').then(pollLocal).catch(() => {}) }, 'Posponer 15 min');
  return h('span', { class: 'row', style: { gap: '8px' } },
    h('span', { class: 'small' }, 'Se instalará sola en ', h('span', { class: 'update-countdown' }, `${m}:${s}`)), later);
}

// Franja permanente: si hay una versión más nueva, siempre se avisa de que este programa está obsoleto.
function renderObsolete(u, mandatory) {
  const el = shell?.obsolete;
  if (!el) return;
  const pending = u && (u.available || mandatory);
  if (!pending) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  el.classList.toggle('min', obsoleteMinimized);
  const working = ['downloading', 'verifying', 'applying', 'preparing'].includes(u.state);
  const action = u.can_apply ? h('button', { class: 'btn sm primary', disabled: working, onclick: (e) => busy(e.target, () => api.local('POST', '/local/update/apply')).then(pollLocal) },
    working ? (u.state_label || 'Actualizando…') : 'Actualizar ahora')
    : h('a', { class: 'btn sm', href: u.manual_url, target: '_blank' }, 'Descargar la versión nueva');
  if (obsoleteMinimized) {
    mount(el, icon('alert', 14), h('b', null, `DEV CONTROL ${u.current} está obsoleto.`), h('span', { class: 'grow' }), action,
      h('button', { class: 'btn ghost sm', onclick: () => { obsoleteMinimized = false; renderUpdate(); } }, 'Ver'));
    return;
  }
  const latest = u.latest || u.required;
  mount(el, icon('alert', 16),
    h('div', { class: 'grow' }, h('b', null, `Tu versión de DEV CONTROL (${u.current}) está obsoleta.`), ' ',
      latest ? `Hay una versión nueva (${latest}). ` : '',
      mandatory && inCall() ? 'La actualización es obligatoria y se instalará al terminar la llamada.' :
        u.auto_in != null ? 'DEV CONTROL se actualizará solo: se cerrará un momento y volverá a abrirse.' :
          mandatory ? 'Es obligatorio actualizar.' : 'Actualiza para tener las últimas mejoras y correcciones.'),
    autoInfo(u), action, h('button', { class: 'btn ghost sm', title: 'Minimizar (volverá a aparecer al abrir el programa)', onclick: () => { obsoleteMinimized = true; renderUpdate(); } }, 'Minimizar'));
}

on('update-required', () => { pollLocal().then(() => renderUpdate(true)); });

on('local-status', () => { renderConn(); renderUpdate(); renderCallsBtn(); if (shell && callsAvailable()) startCalls(); });
on('call-changed', () => { renderUpdate(); renderCallsBtn(); });
function renderCallsBtn() {
  if (!shell) return;
  shell.callsBtn.classList.toggle('hidden', !callsAvailable());
  shell.callsBtn.classList.toggle('active-call', inCall());
}
on('counters', renderCounters);
on('presence', () => {});
on('project', () => { renderProjects(); route(); });
on('me', () => { renderNav(); renderProjects(); renderMe(); });
on('changes', (d) => { if (d.kinds.has('time')) renderTimer(); });
on('queued', (d) => { pollLocal(); toast(d.message || 'Guardado sin conexión: se enviará al reconectar.', 'warn'); });
on('logout', () => { if (shell) logout(); });
on('gate', () => { if (shell) start(); });
window.addEventListener('hashchange', route);

// ---------- pantallas de acceso ----------
function authScreen(...children) {
  stopSync();
  shell = null;
  mount(root, h('div', { class: 'auth' }, h('div', { class: 'auth-box' },
    h('div', { class: 'brand big' }, h('span', { class: 'logo' }, 'DEV', h('b', null, ' CONTROL'))), ...children)));
}

function serverField(local, value) {
  const input = h('input', { value: value ?? '', placeholder: local?.default_server_label ? `Por defecto: ${local.default_server_label}` : 'IP o nombre del servidor (vacío = por defecto)', list: 'dc-servers', autocomplete: 'off' });
  const dl = h('datalist', { id: 'dc-servers' }, (local?.profiles || []).map(p => h('option', { value: p.server }, p.label || '')));
  const status = h('div', { class: 'tiny muted server-test' });
  const test = h('button', { class: 'btn sm', type: 'button', onclick: async () => {
    status.textContent = 'Probando…';
    try {
      const r = await api.local('POST', '/local/test', { server: input.value.trim() });
      status.className = 'tiny server-test ' + (r.ok ? 'ok-text' : 'danger-text');
      status.textContent = r.ok ? `Conectado con ${r.server_label} (DEV CONTROL ${r.version})` : r.message;
    } catch (err) { status.className = 'tiny server-test danger-text'; status.textContent = err.message; }
  } }, 'Probar conexión');
  return { input, el: h('div', { class: 'col' }, field('Servidor', h('div', { class: 'row' }, input, test), { hint: 'Déjalo vacío para usar el servidor por defecto de este equipo.' }), dl, status) };
}

async function connectTo(server) {
  const st = await api.local('POST', '/local/connect', { server });
  setServerKey(st.server);
  store.local = st;
  return st;
}

async function showLogin(message, local) {
  const user = h('input', { autocomplete: 'username', value: loadJSON('dc.lastUser', '') });
  const pass = h('input', { type: 'password', autocomplete: 'current-password' });
  const remember = h('input', { type: 'checkbox', checked: loadJSON('dc.remember', true) });
  const srv = serverField(local, local?.server_is_default ? '' : local?.server);
  const err = h('div', { class: 'callout danger hidden' });
  const totpBox = h('div', { class: 'hidden' });
  const totp = h('input', { inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 6, placeholder: '123456' });
  const recovery = h('input', { placeholder: 'abcd-ef01', autocomplete: 'off' });
  mount(totpBox, field('Código de verificación (6 cifras)', totp), h('details', { class: 'small' }, h('summary', null, '¿No tienes el móvil? Usa un código de recuperación'), recovery));
  const btn = h('button', { class: 'btn primary wide', type: 'submit' }, 'Entrar');
  const form = h('form', { class: 'col gap-lg', onsubmit: async (e) => {
    e.preventDefault();
    err.classList.add('hidden');
    btn.disabled = true;
    try {
      await connectTo(srv.input.value.trim());
      const r = await api.post('/api/login', {
        username: user.value.trim(), password: pass.value, remember: remember.checked,
        totp_code: totp.value.trim() || undefined, recovery_code: recovery.value.trim() || undefined,
        client_label: store.local?.computer_name || '',
      });
      api.setToken(r.token, remember.checked);
      saveJSON('dc.lastUser', user.value.trim()); saveJSON('dc.remember', remember.checked);
      await api.local('POST', '/local/profiles', { server: store.local.server, label: r.user.display_name }).catch(() => {});
      start();
    } catch (ex) {
      if (ex.code === 'requiere_2fa' || ex.data?.requires_2fa) {
        totpBox.classList.remove('hidden'); totp.focus();
        if (ex.code !== 'requiere_2fa') { err.textContent = ex.message; err.classList.remove('hidden'); }
      } else { err.textContent = ex.message; err.classList.remove('hidden'); }
    } finally { btn.disabled = false; }
  } },
  h('h2', null, 'Iniciar sesión'),
  message ? h('div', { class: 'callout' }, message) : null,
  field('Usuario', user), field('Contraseña', pass), totpBox,
  h('label', { class: 'check' }, remember, 'Mantener la sesión iniciada en este equipo'),
  srv.el, err, btn);
  authScreen(form);
  (user.value ? pass : user).focus();
}

async function showSetup(local) {
  const name = h('input', { placeholder: 'Ana García' });
  const user = h('input', { placeholder: 'ana', autocomplete: 'username' });
  const email = h('input', { placeholder: 'opcional' });
  const pass = h('input', { type: 'password', autocomplete: 'new-password' });
  const pass2 = h('input', { type: 'password', autocomplete: 'new-password' });
  const pkey = h('input', { value: 'INC', maxlength: 8 });
  const pname = h('input', { value: 'General' });
  const err = h('div', { class: 'callout danger hidden' });
  const btn = h('button', { class: 'btn primary wide', type: 'submit' }, 'Crear y entrar');
  authScreen(h('form', { class: 'col gap-lg', onsubmit: async (e) => {
    e.preventDefault(); err.classList.add('hidden');
    if (pass.value !== pass2.value) { err.textContent = 'Las contraseñas no coinciden.'; err.classList.remove('hidden'); return; }
    btn.disabled = true;
    try {
      await api.post('/api/setup', { admin: { display_name: name.value.trim(), username: user.value.trim(), email: email.value.trim(), password: pass.value },
        project_key: pkey.value.trim(), project_name: pname.value.trim() });
      const r = await api.post('/api/login', { username: user.value.trim(), password: pass.value, remember: true, client_label: local?.computer_name || '' });
      api.setToken(r.token, true);
      saveJSON('dc.lastUser', user.value.trim());
      toast('DEV CONTROL está listo. Invita a tu equipo desde «Usuarios y roles».', 'ok');
      start();
    } catch (ex) { err.textContent = ex.message; err.classList.remove('hidden'); } finally { btn.disabled = false; }
  } },
  h('h2', null, 'Configuración inicial'),
  h('p', { class: 'muted' }, 'Este equipo es el anfitrión. Crea la cuenta del administrador y el primer proyecto. Después podrás añadir al resto del equipo, roles y más proyectos.'),
  h('div', { class: 'form-grid' }, field('Tu nombre', name, { required: true }), field('Usuario', user, { required: true }),
    field('Contraseña', pass, { required: true, hint: 'Mínimo 10 caracteres.' }), field('Repite la contraseña', pass2, { required: true }),
    field('Correo', email), h('div'),
    field('Prefijo de incidencias', pkey, { hint: 'Las incidencias se numerarán INC-001, INC-002…' }), field('Nombre del proyecto', pname)),
  err, btn));
  name.focus();
}

async function showRecovery(local) {
  let info;
  try { info = await api.get('/api/recovery'); } catch (err) { authScreen(errorBox(err, () => boot())); return; }
  const list = h('div', { class: 'col' });
  for (const b of info.backups) {
    const btn = h('button', { class: 'btn sm primary', onclick: () => busy(btn, async () => {
      await api.post('/api/recovery/restore', { name: b.name });
      toast('Datos recuperados correctamente.', 'ok');
      boot();
    }) }, 'Restaurar');
    list.appendChild(h('div', { class: 'outbox-item' }, h('div', { class: 'grow' }, h('b', null, b.name),
      h('div', { class: 'tiny muted' }, `${b.kind_label} · ${b.created_at ? new Date(b.created_at).toLocaleString() : ''} · ${b.counts?.incidents ?? '?'} incidencias, ${b.counts?.tasks ?? '?'} tareas`)), btn));
  }
  const path = h('input', { placeholder: 'C:\\ruta\\a\\copia.zip o base de datos .db de la versión 1' });
  const confirm = h('input', { placeholder: 'EMPEZAR VACIO' });
  authScreen(h('div', { class: 'col gap-lg' },
    h('h2', null, 'Recuperar los datos'),
    h('div', { class: 'callout warn' }, info.recovery?.reason || 'No se encuentra la base de datos.'),
    h('p', { class: 'muted small' }, `DEV CONTROL no crea una base de datos vacía si hay copias de seguridad, para no perder nada. Las copias están en ${info.backups_dir}.`),
    list,
    h('details', null, h('summary', null, 'Restaurar desde otro archivo (copia .zip o base de datos de la versión 1)'),
      h('div', { class: 'row mt' }, path, h('button', { class: 'btn', onclick: (e) => busy(e.target, async () => {
        await api.post('/api/recovery/restore', { path: path.value.trim() }); toast('Datos recuperados.', 'ok'); boot();
      }) }, 'Restaurar'))),
    h('details', null, h('summary', null, 'Empezar sin datos (no recomendado)'),
      h('p', { class: 'small muted' }, 'Escribe EMPEZAR VACIO para confirmar. Las copias no se borran.'),
      h('div', { class: 'row' }, confirm, h('button', { class: 'btn danger', onclick: (e) => busy(e.target, async () => {
        await api.post('/api/recovery/start-empty', { confirm: confirm.value }); boot();
      }) }, 'Empezar vacío')))));
  void local;
}

async function showGate() {
  const gate = store.gate;
  if (gate.code === 'debe_cambiar_contrasena') {
    const cur = h('input', { type: 'password', autocomplete: 'current-password' });
    const p1 = h('input', { type: 'password', autocomplete: 'new-password' });
    const p2 = h('input', { type: 'password', autocomplete: 'new-password' });
    const btn = h('button', { class: 'btn primary wide', type: 'submit' }, 'Cambiar y continuar');
    authScreen(h('form', { class: 'col gap-lg', onsubmit: (e) => {
      e.preventDefault();
      if (p1.value !== p2.value) { toast('Las contraseñas no coinciden.', 'error'); return; }
      busy(btn, () => api.post('/api/me/password', { current: cur.value, new_password: p1.value }), 'Contraseña cambiada.').then(start).catch(() => {});
    } }, h('h2', null, 'Cambia tu contraseña'), h('p', { class: 'muted' }, gate.message),
    field('Contraseña actual (la que te dieron)', cur), field('Nueva contraseña', p1, { hint: `Mínimo ${store.settings.password_min_length || 10} caracteres.` }),
    field('Repite la nueva contraseña', p2), btn, h('button', { class: 'btn ghost', type: 'button', onclick: logout }, 'Cerrar sesión')));
    cur.focus();
    return;
  }
  // Activar la verificación en dos pasos
  const box = h('div', { class: 'col gap-lg' });
  authScreen(h('h2', null, 'Activa la verificación en dos pasos'), h('p', { class: 'muted' }, gate.message), box);
  try {
    const r = await api.post('/api/me/2fa/start');
    const code = h('input', { inputmode: 'numeric', maxlength: 6, placeholder: '123456' });
    const btn = h('button', { class: 'btn primary wide' }, 'Activar');
    mount(box, h('ol', { class: 'small' },
      h('li', null, 'Abre en el móvil una aplicación de autenticación (Google Authenticator, Microsoft Authenticator, Authy…).'),
      h('li', null, 'Añade una cuenta nueva con esta clave:'),
    ), h('div', { class: 'secret mono' }, r.secret.replace(/(.{4})/g, '$1 ').trim()),
    h('div', { class: 'tiny muted' }, r.uri),
    field('Código que muestra la aplicación', code), btn, h('button', { class: 'btn ghost', onclick: logout }, 'Cerrar sesión'));
    btn.addEventListener('click', () => busy(btn, () => api.post('/api/me/2fa/confirm', { code: code.value.trim() })).then((res) => {
      modal({ title: 'Guarda tus códigos de recuperación', body: h('div', { class: 'col' },
        h('p', null, 'Si pierdes el móvil podrás entrar con uno de estos códigos (cada uno sirve una vez). Guárdalos en un lugar seguro.'),
        h('div', { class: 'codes mono' }, res.recovery_codes.map(c => h('span', null, c)))), actions: [{ label: 'Ya los he guardado', kind: 'primary' }], onClose: start });
    }).catch(() => {}));
    code.focus();
  } catch (err) { mount(box, errorBox(err, showGate)); }
}

async function logout(changeServer) {
  await forgetSession();
  stopCalls();
  try { await api.post('/api/logout'); } catch { /* sin conexión */ }
  api.setToken(null);
  stopSync();
  shell = null;
  const local = await pollLocal();
  showLogin(changeServer === true ? 'Elige el servidor al que conectarte.' : null, local);
}

// ---------- arranque ----------
async function start() {
  try {
    await loadSession();
  } catch (err) {
    if (err.status === 401) { api.setToken(null); return showLogin(null, store.local); }
    if (err.status === 426) return renderUpdate(true);
    return authScreen(errorBox(err, () => start()), h('button', { class: 'btn ghost', onclick: () => logout(true) }, 'Cambiar de servidor'));
  }
  if (store.gate) return showGate();
  buildShell();
  startSync();
  startCalls();
  renderCallsBtn();
  renderUpdate();
  renderTimer();
  if (!location.hash || location.hash === '#/') location.hash = '#/panel';
  else route();
}

async function boot() {
  mount(root, h('div', { class: 'auth' }, loading('Conectando…')));
  const local = await pollLocal();
  if (!local) {
    authScreen(h('div', { class: 'callout danger' }, 'No se puede contactar con DEV CONTROL en este equipo. Ciérralo desde el icono de la bandeja y vuelve a abrirlo.'),
      h('button', { class: 'btn', onclick: boot }, 'Reintentar'));
    return;
  }
  setServerKey(local.server);
  renderUpdate();
  let health = null;
  try { health = await api.get('/api/health'); } catch { /* sin conexión: se intenta con la sesión guardada */ }
  if (health?.recovery) return local.is_host_server ? showRecovery(local) :
    showLogin('El servidor está en modo recuperación: el anfitrión debe restaurar una copia de seguridad.', local);
  if (health?.setup_required) {
    if (local.is_host_server) return showSetup(local);
    return showLogin('Este servidor aún no está configurado. Abre DEV CONTROL en el equipo anfitrión para crear el administrador.', local);
  }
  if (health?.imported && !loadJSON(`dc.importShown.${health.epoch}`, false)) {
    saveJSON(`dc.importShown.${health.epoch}`, true);
    const i = health.imported;
    toast(`Datos anteriores recuperados desde ${i.source}: ${i.incidents} incidencias, ${i.tasks} tareas, ${i.messages} mensajes y ${i.users} usuarios.`, 'ok', { sticky: true, title: 'Recuperación completada' });
  }
  if (api.token) return start();
  showLogin(health ? null : (local.last_error || 'No se puede conectar con el servidor. Comprueba la dirección o pulsa «Probar conexión».'), local);
}

window.addEventListener('error', (e) => console.error(e.error || e.message));
document.addEventListener('DOMContentLoaded', boot);
export { openKey, refreshCounters, openNotification, ApiError, emit };

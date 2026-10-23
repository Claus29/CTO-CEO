// Estado compartido de la sesión y sincronización en tiempo real con el servidor.
import { api, emit, on } from './api.js';
import { beep, loadJSON, saveJSON, setProjectKeys, toast } from './ui.js';

export const store = {
  me: null,             // usuario actual (public_user)
  permissions: [],
  projects: [],         // proyectos visibles
  project: 'all',       // proyecto elegido en la barra superior ('all' = todos)
  users: [],
  settings: {},
  gate: null,           // obligación pendiente (cambiar contraseña / activar 2FA)
  local: null,          // estado del programa local (conexión, cola, actualizaciones)
  serverUp: null,
  unreadMessages: 0,
  unreadNotifications: 0,
  onboardingPending: 0,
  prefs: null,
  epoch: null,
  seq: 0,
};

export const can = (perm) => store.permissions.includes(perm);
export const canAny = (...perms) => perms.some(p => store.permissions.includes(p));
export function userById(id) { return store.users.find(u => u.id === id); }
export function userName(id) { return userById(id)?.display_name || (id ? `#${id}` : '—'); }
export function projectById(id) { return store.projects.find(p => p.id === id); }
export function activeProjects() { return store.projects.filter(p => !p.archived); }

// Añade ?project= según el proyecto elegido arriba.
export function withProject(params = {}) {
  const p = new URLSearchParams(params);
  if (store.project !== 'all' && !p.has('project')) p.set('project', store.project);
  for (const [k, v] of [...p.entries()]) if (v === '' || v === 'undefined' || v === 'null') p.delete(k);
  return p.toString();
}
// Proyecto por defecto para crear elementos nuevos.
export function defaultProjectId() {
  if (store.project !== 'all') return Number(store.project);
  return store.defaultProjectId || activeProjects()[0]?.id || null;
}
export function setProject(value) {
  store.project = value === 'all' ? 'all' : Number(value);
  saveJSON(`dc.project.${store.me?.id}`, store.project);
  emit('project');
}

let running = false;
let loopAbort = null;
let localTimer = null;
let lastNotifId = 0;

export function applyMe(me) {
  store.me = me.user;
  store.permissions = me.permissions;
  store.projects = me.projects;
  store.defaultProjectId = me.default_project_id;
  store.settings = me.settings || {};
  store.gate = me.gate;
  store.onboardingPending = me.onboarding_pending || 0;
  store.epoch = me.epoch;
  setProjectKeys(me.projects.map(p => p.key));
  const saved = loadJSON(`dc.project.${me.user.id}`, 'all');
  store.project = saved === 'all' || me.projects.some(p => p.id === saved) ? saved : 'all';
}

export async function loadSession() {
  applyMe(await api.get('/api/me'));
  if (store.gate) return;
  store.users = (await api.get('/api/users')).items;
  try { store.prefs = await api.get('/api/notifications/prefs'); } catch { store.prefs = null; }
  await loadWatched();
  await refreshCounters();
  lastNotifId = loadJSON(`dc.lastNotif.${store.me.id}`, 0);
  const latest = await api.get('/api/notifications?limit=1').catch(() => null);
  if (latest && latest.items.length && latest.items[0].id > lastNotifId) {
    lastNotifId = latest.items[0].id;  // no se repiten avisos antiguos al abrir
    saveJSON(`dc.lastNotif.${store.me.id}`, lastNotifId);
  }
}

export async function refreshMe() {
  try { applyMe(await api.get('/api/me')); emit('me'); } catch { /* sin conexión */ }
}

export async function refreshCounters() {
  try {
    const n = await api.get('/api/notifications?limit=1');
    store.unreadNotifications = n.unread;
    if (can('chat.usar')) {
      const ch = await api.get('/api/channels');
      store.unreadMessages = ch.items.reduce((s, c) => s + (c.unread || 0), 0);
    }
    emit('counters');
  } catch { /* sin conexión */ }
}

// -- personas que sigo (aviso al conectarse/desconectarse y punto verde arriba, junto a «Conectado») --
export const isWatched = (uid) => (store.watched || []).includes(uid);
export async function loadWatched() {
  try { store.watched = (await api.get('/api/follows/users')).user_ids || []; } catch { store.watched = store.watched || []; }
  emit('watched');
}
export async function toggleWatch(uid, follow = !isWatched(uid)) {
  await api.post('/api/follows', { entity_type: 'user', entity_id: uid, follow });
  await loadWatched();
  return follow;
}

export async function refreshUsers() {
  try { store.users = (await api.get('/api/users')).items; emit('presence'); } catch { /* sin conexión */ }
}

export function startSync() {
  if (running) return;
  running = true;
  pollLocal();
  localTimer = setInterval(pollLocal, 2500);
  shareSession();
  presenceTimer = setInterval(sendPresence, 5000);
  sendPresence();
  syncLoop();
}

export function stopSync() {
  running = false;
  clearInterval(localTimer);
  clearInterval(presenceTimer);
  loopAbort && loopAbort.abort();
}

// -- avisos de Windows en segundo plano ----------------------------------------------------------
// El programa de este equipo recibe la sesión para avisar aunque la ventana esté cerrada en la bandeja,
// y sabe si la ventana está delante (entonces el aviso se ve dentro) o si hay una llamada (no se actualiza).
let presenceTimer = null;

export function shareSession() {
  if (!api.token || !store.me) return;
  api.local('POST', '/local/session', { token: api.token, user_id: store.me.id }).catch(() => {});
}

export function forgetSession() {
  return api.local('POST', '/local/session', { token: '' }).catch(() => {});
}

export async function sendPresence() {
  if (!running) return;
  try {
    const r = await api.local('POST', '/local/presence', {
      focused: document.hasFocus() && !document.hidden, in_call: !!window.dcInCall?.(), route: location.hash,
    });
    if (r?.open?.startsWith('aviso/')) openNotificationById(Number(r.open.split('/')[1]));
  } catch { /* el programa local no responde: se reintenta en la siguiente ronda */ }
}
window.dcCheckOpen = sendPresence;
for (const ev of ['focus', 'blur']) window.addEventListener(ev, () => sendPresence());
document.addEventListener('visibilitychange', () => sendPresence());

export async function openNotificationById(id) {
  if (!id) return;
  try {
    const n = await api.get(`/api/notifications/${id}`);
    if (n?.item) openNotification(n.item);
  } catch { /* el aviso ya no existe */ }
}

export async function pollLocal() {
  try {
    const r = await fetch('/local/status');
    const s = await r.json();
    const prevUp = store.serverUp;
    store.local = s;
    store.serverUp = s.reachable;
    emit('local-status', s);
    if (prevUp === false && s.reachable) {
      toast('Conexión con el servidor recuperada. Sincronizando…', 'ok');
      emit('reconnected');
    }
  } catch {
    store.local = null;
    store.serverUp = false;
    emit('local-status', null);
  }
  return store.local;
}

async function syncLoop() {
  let delay = 1000;
  while (running) {
    loopAbort = new AbortController();
    try {
      const q = new URLSearchParams({ since: store.seq, wait: 25, epoch: store.epoch || '' });
      const res = await api.get(`/api/changes?${q}`, { signal: loopAbort.signal });
      delay = 1000;
      if (res._offline) throw new Error('offline');
      if (res.reset || (store.epoch && res.epoch !== store.epoch)) {
        store.epoch = res.epoch;
        store.seq = res.seq;
        emit('reset');
        await refreshCounters();
        continue;
      }
      store.seq = res.seq;
      store.epoch = res.epoch;
      updatePresence(res.presence);
      if (res.changes.length) await handleChanges(res.changes);
    } catch (err) {
      if (!running) return;
      if (err.name === 'AbortError') return;
      if (err.status === 401) return;
      await new Promise(r => setTimeout(r, delay));
      delay = Math.min(delay * 2, 8000);
    }
  }
}

function updatePresence(presence) {
  if (!presence) return;
  let changed = false;
  for (const p of presence) {
    const u = store.users.find(x => x.id === p.id);
    if (u && (u.online !== p.online || u.last_seen_at !== p.last_seen_at)) {
      u.online = p.online; u.last_seen_at = p.last_seen_at || u.last_seen_at; changed = true;
    }
  }
  if (changed) emit('presence');
}

async function handleChanges(changes) {
  const kinds = new Set(changes.map(c => c.kind));
  emit('changes', { changes, kinds });
  if (kinds.has('user') || kinds.has('permissions') || kinds.has('project')) { await refreshMe(); await refreshUsers(); }
  else if (kinds.has('presence')) refreshUsers();
  if (kinds.has('message') || kinds.has('chat_read') || kinds.has('notification')) await refreshCounters();
  if (kinds.has('notification')) await pullNotifications();
}

function muted() {
  const m = store.prefs?.mute_until;
  return m && new Date(m) > new Date();
}

function wantsDesktop(type) {
  const p = store.prefs?.items?.find(i => i.type === type);
  return !p || p.desktop;
}

async function pullNotifications() {
  let res;
  try { res = await api.get(`/api/notifications?after=${lastNotifId}&limit=20`); } catch { return; }
  const fresh = res.items.filter(n => n.id > lastNotifId).sort((a, b) => a.id - b.id);
  if (!fresh.length) return;
  lastNotifId = fresh[fresh.length - 1].id;
  saveJSON(`dc.lastNotif.${store.me.id}`, lastNotifId);
  for (const n of fresh) {
    if (n.read_at) continue;
    const onChat = location.hash.startsWith('#/chat') && document.hasFocus();
    if (n.type === 'message_new' && onChat) continue;
    const go = () => openNotification(n);
    if (n.urgent && !muted()) beep();
    toast(n.body || '', n.urgent ? 'urgent' : 'info', { title: n.title, onClick: go, sticky: !!n.urgent, timeout: 6000 });
    // Con el programa de Windows, los avisos del sistema los muestra él (también con la ventana cerrada).
    if (!store.local?.notifier?.active && !muted() && wantsDesktop(n.type) && (document.hidden || !document.hasFocus())) {
      desktopNotify(n, go);
    }
  }
}

// Aviso del sistema: dentro del programa lo muestra Windows (icono de la bandeja);
// en un navegador se usa la API de notificaciones.
function desktopNotify(n, go) {
  if (store.local?.desktop) {
    api.local('POST', '/local/notify', { title: n.title, body: n.body || '', urgent: !!n.urgent }).catch(() => {});
    return;
  }
  if ('Notification' in window && Notification.permission === 'granted') {
    try {
      const dn = new Notification(n.title, { body: n.body, tag: `dc-${n.id}`, requireInteraction: !!n.urgent });
      dn.onclick = () => { window.focus(); go(); dn.close(); };
    } catch { /* el sistema no permite avisos */ }
  }
}

export function entityHash(type, id, key) {
  switch (type) {
    case 'incident': return `#/incidencias/${key || id}`;
    case 'task': return `#/tareas/${key || id}`;
    case 'message': return `#/chat?m=${id}`;
    case 'decision': return `#/decisiones/${key || id}`;
    case 'wiki': return `#/wiki/${id}`;
    case 'idea': return '#/ideas';
    case 'renewal': return '#/renovaciones';
    case 'store_submission': return '#/tiendas';
    case 'version': return '#/versiones';
    case 'standup': return '#/equipo/daily';
    case 'date': return '#/calendario';
    case 'absence': return '#/equipo';
    case 'build': return '#/builds';
    case 'test_run': return '#/pruebas';
    case 'goal': return '#/semana';
    case 'user': return '#/cuenta';
    case 'project': return '#/tareas';
    case 'presence': return '#/equipo';
    default: return '#/panel';
  }
}

export function openNotification(n) {
  api.post('/api/notifications/read', { ids: [n.id] }).then(refreshCounters).catch(() => {});
  location.hash = entityHash(n.entity_type, n.entity_id, n.entity_key);
}

on('unauthorized', () => emit('logout'));

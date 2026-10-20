// Usuarios y roles: personas, roles con su matriz de permisos, proyectos y pasos de bienvenida.
import { api } from '../api.js';
import { can, store } from '../state.js';
import {
  avatar, busy, confirmDialog, emptyState, errorBox, field, fmtDateTime, fmtRelative, h, icon, loading, menuButton, modal,
  mount, pageHead, selectEl, tabs, table, toast, uid,
} from '../ui.js';

const TABS = [
  { key: 'usuarios', label: 'Usuarios', icon: 'users', perm: 'sistema.usuarios' },
  { key: 'roles', label: 'Roles y permisos', icon: 'shield', perm: 'sistema.usuarios' },
  { key: 'proyectos', label: 'Proyectos', icon: 'folder', perm: 'proyectos.gestionar' },
  { key: 'bienvenida', label: 'Bienvenida', icon: 'flag', perm: 'sistema.usuarios' },
];
const COLORS = ['#5b8def', '#4f7cff', '#3fbf86', '#f0a83a', '#ef5350', '#a98bff', '#26a69a', '#ec6fa9', '#8d6e63', '#78909c'];

// ---------- utilidades ----------
function generatePassword(min = 12) {
  const len = Math.max(12, Number(min) || 12);
  const sets = ['ABCDEFGHJKLMNPQRSTUVWXYZ', 'abcdefghijkmnpqrstuvwxyz', '23456789', '-_.!?#'];
  const all = sets.join('');
  const rnd = (n) => { const a = new Uint32Array(1); crypto.getRandomValues(a); return a[0] % n; };
  const chars = sets.map(s => s[rnd(s.length)]);
  while (chars.length < len) chars.push(all[rnd(all.length)]);
  for (let i = chars.length - 1; i > 0; i--) { const j = rnd(i + 1); [chars[i], chars[j]] = [chars[j], chars[i]]; }
  return chars.join('');
}

function suggestUsername(name) {
  const parts = name.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '';
  return (parts.length > 1 ? `${parts[0]}.${parts[1]}` : parts[0]).slice(0, 40);
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast('Copiado al portapapeles.', 'ok'); } catch { toast('No se ha podido copiar. Selecciónalo y cópialo a mano.', 'warn'); }
}

function minPassword() { return Number(store.settings?.password_min_length) || 10; }

function passwordInput(initial = '') {
  const input = h('input', { type: 'text', autocomplete: 'new-password', value: initial, class: 'mono', spellcheck: 'false' });
  const gen = h('button', { class: 'btn sm', type: 'button', title: 'Generar una contraseña segura', onclick: () => { input.value = generatePassword(minPassword()); input.dispatchEvent(new Event('input')); } }, icon('refresh', 14), 'Generar');
  const show = h('button', { class: 'btn sm ghost icon', type: 'button', title: 'Mostrar u ocultar', onclick: () => { input.type = input.type === 'text' ? 'password' : 'text'; } }, icon('eye', 14));
  return { input, el: h('div', { class: 'row' }, input, gen, show) };
}

function credentialsModal(title, user, password, mustChange) {
  const text = `DEV CONTROL\nUsuario: ${user.username}\nContraseña temporal: ${password}`;
  modal({
    title,
    body: h('div', { class: 'col gap-lg' },
      h('p', null, `Pásale estos datos a ${user.display_name} por un canal privado. `,
        mustChange ? 'Tendrá que cambiar la contraseña la primera vez que entre.' : 'Podrá seguir usando esta contraseña.'),
      h('div', { class: 'kv adm-cred' }, h('span', null, 'Usuario'), h('b', { class: 'mono' }, user.username),
        h('span', null, 'Contraseña'), h('b', { class: 'mono' }, password)),
      h('div', { class: 'callout warn small' }, 'Esta contraseña no se volverá a mostrar. Si se pierde, restablécela de nuevo.')),
    actions: [{ label: 'Copiar', onClick: () => { copyText(text); return false; } }, { label: 'Hecho', kind: 'primary' }],
  });
}

function projectChecks(projects, selected) {
  const set = new Set(selected);
  const boxes = projects.map(p => ({ p, cb: h('input', { type: 'checkbox', checked: set.has(p.id) }) }));
  const el = h('div', { class: 'adm-checks' }, boxes.length ? boxes.map(({ p, cb }) => h('label', { class: 'check' }, cb,
    h('span', { class: 'project-dot', style: { background: p.color || 'var(--muted)' } }), `${p.key} · ${p.name}`,
    p.archived ? h('span', { class: 'tiny muted' }, '(archivado)') : null)) : h('span', { class: 'muted small' }, 'No hay proyectos.'));
  return { el, value: () => boxes.filter(b => b.cb.checked).map(b => b.p.id) };
}

function peopleChecks(users, selected) {
  const set = new Set(selected);
  const boxes = users.map(u => ({ u, cb: h('input', { type: 'checkbox', checked: set.has(u.id) }) }));
  const el = h('div', { class: 'adm-checks' }, boxes.map(({ u, cb }) => h('label', { class: 'check' }, cb, avatar(u), u.display_name,
    h('span', { class: 'tiny muted' }, u.role_label || ''))));
  const all = h('button', { class: 'btn sm ghost', type: 'button', onclick: () => { const on = boxes.some(b => !b.cb.checked); boxes.forEach(b => { b.cb.checked = on; }); } }, 'Marcar o desmarcar todos');
  return { el: h('div', { class: 'col' }, all, el), value: () => boxes.filter(b => b.cb.checked).map(b => b.u.id) };
}

const sameSet = (a, b) => a.length === b.length && a.every(x => b.includes(x));

// ---------- vista ----------
export default async function render(ctx) {
  const { root } = ctx;
  const allowedTabs = TABS.filter(t => can(t.perm));
  const active = allowedTabs.some(t => t.key === ctx.params.tab) ? ctx.params.tab : allowedTabs[0]?.key;
  if (!active) { mount(root, emptyState('Sin permiso', 'Tu rol no permite gestionar usuarios ni proyectos.')); return; }
  const body = h('div');
  mount(root,
    pageHead('Usuarios y roles'),
    tabs(allowedTabs, active, (k) => { location.hash = `#/admin/${k}`; }),
    body);
  const views = { usuarios: usersTab, roles: rolesTab, proyectos: projectsTab, bienvenida: onboardingTab };
  ctx.setTitle(`Usuarios y roles · ${allowedTabs.find(t => t.key === active).label}`);
  return views[active](body, ctx);
}

// ---------- Usuarios ----------
async function usersTab(root, ctx) {
  let data = null, roles = [], projects = [], filter = 'activos', q = '';
  const listBox = h('div');
  const search = h('input', { placeholder: 'Buscar por nombre, usuario o correo…', oninput: () => { q = search.value.trim().toLowerCase(); draw(); } });
  const seg = h('div', { class: 'seg' });
  const drawSeg = () => mount(seg, [['activos', 'Activos'], ['inactivos', 'Desactivados'], ['todos', 'Todos']].map(([k, l]) =>
    h('button', { class: filter === k ? 'on' : '', onclick: () => { filter = k; drawSeg(); draw(); } }, l)));
  drawSeg();
  mount(root,
    h('div', { class: 'toolbar' }, h('div', { class: 'search' }, icon('search'), search), seg, h('div', { class: 'grow' }),
      h('button', { class: 'btn primary', onclick: () => createUser() }, icon('plus'), 'Nuevo usuario')),
    listBox);

  async function load() {
    try {
      const [u, r, p] = await Promise.all([api.get('/api/admin/users'), api.get('/api/admin/roles'), api.get('/api/projects')]);
      data = u.items; roles = r.items; projects = p.items;
      draw();
    } catch (err) { mount(listBox, errorBox(err, load)); }
  }

  const roleOptions = () => roles.map(r => [String(r.id), r.name]);

  function draw() {
    if (!data) { mount(listBox, loading()); return; }
    let rows = data;
    if (filter === 'activos') rows = rows.filter(u => u.active);
    if (filter === 'inactivos') rows = rows.filter(u => !u.active);
    if (q) rows = rows.filter(u => `${u.display_name} ${u.username} ${u.email || ''}`.toLowerCase().includes(q));
    const req2fa = store.settings?.require_2fa_roles || [];
    const admins = data.filter(u => u.active && roles.find(r => r.id === u.role_id)?.perms.includes('sistema.usuarios')).length;
    mount(listBox,
      h('div', { class: 'row wrap small muted mb' },
        `${data.filter(u => u.active).length} activos · ${data.filter(u => u.online).length} en línea · ${admins} con permiso para gestionar usuarios`),
      table([
        { label: 'Persona', render: u => h('div', { class: 'row' }, avatar(u, u.online), h('div', { class: 'grow' },
          h('div', { class: 'title-cell' }, u.display_name, u.id === store.me.id ? h('span', { class: 'badge neutral adm-ml' }, 'Tú') : null),
          h('div', { class: 'tiny muted ellipsis' }, `@${u.username}${u.email ? ' · ' + u.email : ''}`))) },
        { label: 'Rol', render: u => h('span', { class: 'small' }, u.role_label || '—') },
        { label: 'Estado', render: u => h('div', { class: 'row wrap' },
          u.active ? h('span', { class: 'badge ok' }, 'Activo') : h('span', { class: 'badge neutral' }, 'Desactivado'),
          u.must_change_password ? h('span', { class: 'badge warn', title: 'Tiene que cambiar la contraseña al entrar' }, 'Contraseña temporal') : null) },
        { label: '2 pasos', render: u => u.totp_enabled ? h('span', { class: 'badge ok' }, icon('shield', 12), 'Activada')
          : (req2fa.includes(u.role_key) ? h('span', { class: 'badge warn', title: 'Su rol la exige: se le pedirá al entrar' }, 'Pendiente') : h('span', { class: 'muted small' }, 'No')) },
        { label: 'Último acceso', cls: 'hide-sm', render: u => u.online ? h('span', { class: 'ok-text small' }, '● En línea')
          : h('span', { class: 'small', title: u.last_login_at ? `Último inicio de sesión: ${fmtDateTime(u.last_login_at)}` : '' },
            u.last_seen_at ? fmtRelative(u.last_seen_at) : (u.last_login_at ? fmtRelative(u.last_login_at) : 'Nunca')) },
        { label: 'Sesiones', cls: 'hide-sm', render: u => h('span', { class: 'small' }, u.sessions || '0') },
        { label: '', render: u => h('div', { class: 'row end', onclick: e => e.stopPropagation() }, menuButton('Acciones', [
          { label: 'Editar', icon: 'edit', onClick: () => editUser(u) },
          { label: 'Restablecer contraseña', icon: 'key', onClick: () => resetPassword(u) },
          { label: 'Cerrar todas sus sesiones', icon: 'logout', onClick: () => logoutAll(u), hidden: !u.sessions },
          { label: 'Quitar la verificación en dos pasos', icon: 'shield', onClick: () => reset2fa(u), hidden: !u.totp_enabled },
          { label: u.active ? 'Desactivar' : 'Activar', icon: u.active ? 'eyeoff' : 'check', danger: u.active, onClick: () => toggleActive(u), hidden: u.id === store.me.id },
        ], { 'aria-label': `Acciones de ${u.display_name}` })) },
      ], rows, { onRow: editUser, rowClass: u => u.active ? '' : 'is-resolved', empty: q ? 'Nadie coincide con la búsqueda' : 'No hay usuarios en esta lista' }));
  }

  function createUser() {
    const name = h('input', { placeholder: 'Nombre y apellido' });
    const username = h('input', { placeholder: 'nombre.apellido', autocomplete: 'off' });
    let userTouched = false;
    username.addEventListener('input', () => { userTouched = true; });
    name.addEventListener('input', () => { if (!userTouched) username.value = suggestUsername(name.value); });
    const email = h('input', { type: 'email', placeholder: 'opcional' });
    const defaultRole = roles.find(r => r.key === 'dev') || roles.find(r => !r.locked) || roles[0];
    const role = selectEl(roleOptions(), String(defaultRole?.id ?? ''));
    const pw = passwordInput(generatePassword(minPassword()));
    const mustChange = h('input', { type: 'checkbox', checked: true });
    const capacity = h('input', { type: 'number', min: 0, max: 80, step: 1, value: 40 });
    const proj = projectChecks(projects.filter(p => !p.archived), projects.filter(p => !p.archived).map(p => p.id));
    modal({
      title: 'Nuevo usuario', wide: true,
      body: h('div', { class: 'col gap-lg' },
        h('div', { class: 'form-grid' },
          field('Nombre visible', name, { required: true }),
          field('Usuario para entrar', username, { required: true, hint: 'Letras, números, punto, guion o guion bajo. Sin espacios.' }),
          field('Correo', email),
          field('Rol', role, { required: true, hint: 'Decide qué puede ver y hacer. Puedes cambiarlo después.' }),
          field('Contraseña temporal', pw.el, { required: true, hint: `Mínimo ${minPassword()} caracteres.`, cls: 'span2' }),
          h('label', { class: 'check span2' }, mustChange, 'Debe cambiarla la primera vez que entre (recomendado)'),
          field('Capacidad semanal (horas)', capacity, { hint: 'Para la carga de trabajo. Normalmente 40.' })),
        h('div', null, h('div', { class: 'section-title' }, 'Proyectos a los que tiene acceso'), proj.el,
          h('div', { class: 'tiny muted mt' }, 'Los roles con «Ver todos los proyectos» ven todos aunque no estén marcados.'))),
      actions: [{ label: 'Cancelar' }, { label: 'Crear usuario', kind: 'primary', onClick: async () => {
        if (!name.value.trim() || !username.value.trim()) { toast('Escribe el nombre y el usuario.', 'error'); (name.value.trim() ? username : name).focus(); return false; }
        if (pw.input.value.length < minPassword()) { toast(`La contraseña debe tener al menos ${minPassword()} caracteres.`, 'error'); pw.input.focus(); return false; }
        try {
          const body = { display_name: name.value.trim(), username: username.value.trim().toLowerCase(), email: email.value.trim(),
            role_id: Number(role.value), password: pw.input.value, must_change_password: mustChange.checked,
            capacity_hours: Number(capacity.value) || 40, project_ids: proj.value() };
          await busy(null, () => api.post('/api/admin/users', body));
          credentialsModal('Usuario creado', body, body.password, body.must_change_password);
          load();
        } catch { return false; }
      } }],
    });
  }

  function editUser(u) {
    const name = h('input', { value: u.display_name });
    const username = h('input', { value: u.username, autocomplete: 'off' });
    const email = h('input', { type: 'email', value: u.email || '' });
    const role = selectEl(roleOptions(), String(u.role_id));
    const capacity = h('input', { type: 'number', min: 0, max: 80, step: 1, value: u.capacity_hours ?? 40 });
    const active = h('input', { type: 'checkbox', checked: u.active, disabled: u.id === store.me.id });
    const proj = projectChecks(projects, u.project_ids || []);
    modal({
      title: `Editar a ${u.display_name}`, wide: true,
      body: h('div', { class: 'col gap-lg' },
        u.id === store.me.id ? h('div', { class: 'callout info small' }, 'Este eres tú: no puedes desactivarte ni quitarte el permiso de gestionar usuarios.') : null,
        h('div', { class: 'form-grid' },
          field('Nombre visible', name, { required: true }), field('Usuario para entrar', username, { required: true }),
          field('Correo', email), field('Rol', role, { required: true }),
          field('Capacidad semanal (horas)', capacity),
          h('div', { class: 'col' }, h('span', { class: 'small muted' }, 'Estado'), h('label', { class: 'check' }, active, 'Cuenta activa (puede entrar)'),
            h('span', { class: 'tiny muted' }, 'Al desactivarla se cierran sus sesiones. Su historial se conserva.'))),
        h('div', null, h('div', { class: 'section-title' }, 'Proyectos'), proj.el),
        h('div', { class: 'tiny muted' }, `Creado el ${fmtDateTime(u.created_at)}${u.last_login_at ? ' · último inicio de sesión ' + fmtDateTime(u.last_login_at) : ''}`)),
      actions: [{ label: 'Cancelar' }, { label: 'Guardar', kind: 'primary', onClick: async () => {
        const body = {};
        if (name.value.trim() !== u.display_name) body.display_name = name.value.trim();
        if (username.value.trim().toLowerCase() !== u.username) body.username = username.value.trim().toLowerCase();
        if (email.value.trim() !== (u.email || '')) body.email = email.value.trim();
        if (Number(role.value) !== u.role_id) body.role_id = Number(role.value);
        if (Number(capacity.value) !== Number(u.capacity_hours)) body.capacity_hours = Number(capacity.value) || 0;
        if (active.checked !== u.active) body.active = active.checked;
        if (!sameSet(proj.value(), u.project_ids || [])) body.project_ids = proj.value();
        if (!Object.keys(body).length) return;
        if (body.display_name === '' || body.username === '') { toast('El nombre y el usuario son obligatorios.', 'error'); return false; }
        try { await busy(null, () => api.patch(`/api/admin/users/${u.id}`, body), 'Cambios guardados.'); load(); } catch { load(); return false; }
      } }],
    });
  }

  function resetPassword(u) {
    const pw = passwordInput(generatePassword(minPassword()));
    const mustChange = h('input', { type: 'checkbox', checked: true });
    modal({
      title: `Restablecer la contraseña de ${u.display_name}`,
      body: h('div', { class: 'col gap-lg' },
        h('p', { class: 'small muted' }, 'Se cerrarán todas sus sesiones abiertas y tendrá que entrar con la nueva contraseña.'),
        field('Nueva contraseña', pw.el, { required: true, hint: `Mínimo ${minPassword()} caracteres.` }),
        h('label', { class: 'check' }, mustChange, 'Debe cambiarla al entrar')),
      actions: [{ label: 'Cancelar' }, { label: 'Restablecer', kind: 'primary', onClick: async () => {
        if (pw.input.value.length < minPassword()) { toast(`La contraseña debe tener al menos ${minPassword()} caracteres.`, 'error'); return false; }
        try {
          await busy(null, () => api.post(`/api/admin/users/${u.id}/password`, { new_password: pw.input.value, must_change: mustChange.checked }));
          credentialsModal('Contraseña restablecida', u, pw.input.value, mustChange.checked);
          load();
        } catch { return false; }
      } }],
    });
  }

  async function logoutAll(u) {
    if (!await confirmDialog({ title: 'Cerrar sus sesiones', message: `Se cerrarán las ${u.sessions} sesiones abiertas de ${u.display_name} en todos sus equipos. Tendrá que volver a entrar.`, confirmLabel: 'Cerrar sesiones' })) return;
    busy(null, () => api.post(`/api/admin/users/${u.id}/logout-all`), 'Sesiones cerradas.').then(load).catch(() => {});
  }

  async function reset2fa(u) {
    if (!await confirmDialog({ title: 'Quitar la verificación en dos pasos', danger: true, confirmLabel: 'Quitar',
      message: `Úsalo si ${u.display_name} ha perdido el móvil y sus códigos de recuperación. Podrá entrar solo con la contraseña y volver a activarla desde «Mi cuenta».` })) return;
    busy(null, () => api.post(`/api/admin/users/${u.id}/reset-2fa`), 'Verificación en dos pasos desactivada.').then(load).catch(() => {});
  }

  async function toggleActive(u) {
    if (u.active && !await confirmDialog({ title: `Desactivar a ${u.display_name}`, danger: true, confirmLabel: 'Desactivar',
      message: 'No podrá entrar y se cerrarán sus sesiones. Su historial, incidencias y mensajes se conservan. Puedes volver a activarlo cuando quieras.' })) return;
    busy(null, () => api.patch(`/api/admin/users/${u.id}`, { active: !u.active }), u.active ? 'Usuario desactivado.' : 'Usuario activado.')
      .then(load).catch(load);
  }

  draw();
  await load();
  ctx.onChanges(['user', 'permissions', 'project'], load);
  ctx.on('presence', () => { if (!data) return; for (const u of data) { const s = store.users.find(x => x.id === u.id); if (s) { u.online = s.online; u.last_seen_at = s.last_seen_at; } } draw(); });
}

// ---------- Roles ----------
async function rolesTab(root, ctx) {
  let roles = null, catalog = [];
  async function load() {
    try {
      const [r, c] = await Promise.all([api.get('/api/admin/roles'), api.get('/api/permissions/catalog')]);
      roles = r.items; catalog = c.items;
      draw();
    } catch (err) { mount(root, errorBox(err, load)); }
  }
  function draw() {
    mount(root,
      h('div', { class: 'toolbar' }, h('p', { class: 'muted small grow' }, 'Cada persona tiene un rol. El rol decide qué puede ver y hacer. Los cambios se aplican al momento.'),
        h('button', { class: 'btn primary', onclick: () => editRole(null) }, icon('plus'), 'Nuevo rol')),
      h('div', { class: 'grid cols-2' }, roles.map(r => h('div', { class: 'card adm-role' },
        h('div', { class: 'card-head' }, h('h3', null, r.name), r.locked ? h('span', { class: 'badge neutral' }, icon('lock', 12), 'Bloqueado') : null),
        h('p', { class: 'small muted' }, r.description || 'Sin descripción.'),
        h('div', { class: 'row wrap small' },
          h('span', { class: 'badge info' }, `${r.users} ${r.users === 1 ? 'persona' : 'personas'}`),
          h('span', { class: 'badge neutral' }, `${r.perms.length} de ${catalog.length} permisos`),
          (store.settings?.require_2fa_roles || []).includes(r.key) ? h('span', { class: 'badge warn' }, 'Exige 2 pasos') : null),
        h('div', { class: 'adm-areas' }, areaSummary(r.perms)),
        h('div', { class: 'row end mt' },
          r.locked ? h('button', { class: 'btn sm', onclick: () => editRole(r) }, icon('eye', 14), 'Ver permisos') : [
            h('button', { class: 'btn sm ghost', onclick: () => editRole(null, r) }, icon('copy', 14), 'Duplicar'),
            h('button', { class: 'btn sm danger', disabled: r.users > 0, title: r.users ? 'Tiene personas asignadas: cámbiales el rol primero' : 'Eliminar el rol', onclick: (e) => deleteRole(r, e.currentTarget) }, icon('trash', 14), 'Eliminar'),
            h('button', { class: 'btn sm primary', onclick: () => editRole(r) }, icon('edit', 14), 'Editar')])))));
  }
  function areaSummary(perms) {
    const areas = [...new Set(catalog.map(c => c.area))];
    return areas.map(a => {
      const all = catalog.filter(c => c.area === a);
      const n = all.filter(c => perms.includes(c.key)).length;
      return h('span', { class: `adm-area ${n === all.length ? 'full' : n ? 'part' : ''}`, title: `${a}: ${n} de ${all.length}` }, a);
    });
  }
  function editRole(role, copyFrom) {
    const locked = !!role?.locked;
    const name = h('input', { value: role ? role.name : (copyFrom ? `${copyFrom.name} (copia)` : ''), disabled: locked });
    const desc = h('input', { value: role?.description || copyFrom?.description || '', disabled: locked, placeholder: 'Para qué sirve este rol' });
    const initial = new Set(role ? role.perms : copyFrom ? copyFrom.perms : ['incidencias.ver', 'tareas.ver', 'actividad.ver_propia', 'chat.usar']);
    const boxes = new Map();
    const areas = [...new Set(catalog.map(c => c.area))];
    const counter = h('span', { class: 'small muted' });
    const updateCount = () => { counter.textContent = `${[...boxes.values()].filter(b => b.checked).length} de ${catalog.length} permisos`; };
    const matrix = h('div', { class: 'adm-matrix' }, areas.map(area => {
      const items = catalog.filter(c => c.area === area);
      const toggle = h('input', { type: 'checkbox', disabled: locked, title: 'Todos los de esta área' });
      const sync = () => { const st = items.map(i => boxes.get(i.key).checked); toggle.checked = st.every(Boolean); toggle.indeterminate = !toggle.checked && st.some(Boolean); updateCount(); };
      const rows = items.map(c => {
        const cb = h('input', { type: 'checkbox', checked: locked || initial.has(c.key), disabled: locked, onchange: () => sync() });
        boxes.set(c.key, cb);
        return h('label', { class: 'check adm-perm' }, cb, h('span', { class: 'grow' }, c.label, h('span', { class: 'tiny muted mono adm-key' }, c.key)));
      });
      toggle.addEventListener('change', () => { items.forEach(i => { boxes.get(i.key).checked = toggle.checked; }); sync(); });
      const group = h('div', { class: 'adm-group' }, h('label', { class: 'check adm-group-head' }, toggle, h('b', null, area)), rows);
      setTimeout(sync, 0);
      return group;
    }));
    const preset = !role && roles.length ? selectEl([['', 'Elige un rol para copiar sus permisos…'], ...roles.map(r => [String(r.id), r.name])], '', {
      onchange: () => { const src = roles.find(r => String(r.id) === preset.value); if (!src) return; for (const [k, cb] of boxes) { cb.checked = src.perms.includes(k); cb.dispatchEvent(new Event('change')); } },
    }) : null;
    modal({
      title: locked ? `Permisos de «${role.name}»` : role ? `Editar el rol «${role.name}»` : 'Nuevo rol', wide: true,
      body: h('div', { class: 'col gap-lg' },
        locked ? h('div', { class: 'callout info small' }, 'El rol de administrador siempre tiene todos los permisos (también los que se añadan en versiones futuras). No se puede modificar ni eliminar.') : null,
        h('div', { class: 'form-grid' }, field('Nombre del rol', name, { required: true }), field('Descripción', desc)),
        preset ? field('Partir de', preset) : null,
        h('div', { class: 'row between' }, h('div', { class: 'section-title adm-nomargin' }, 'Permisos'), counter),
        matrix,
        role && !locked && role.id === store.me.role_id ? h('div', { class: 'callout warn small' }, 'Este es tu rol: no puedes quitarle «Gestionar usuarios y roles».') : null),
      actions: locked ? [{ label: 'Cerrar', kind: 'primary' }] : [{ label: 'Cancelar' }, { label: role ? 'Guardar' : 'Crear rol', kind: 'primary', onClick: async () => {
        if (!name.value.trim()) { toast('Escribe el nombre del rol.', 'error'); name.focus(); return false; }
        const perms = [...boxes].filter(([, cb]) => cb.checked).map(([k]) => k);
        try {
          if (role) await busy(null, () => api.patch(`/api/admin/roles/${role.id}`, { name: name.value.trim(), description: desc.value.trim(), perms }), 'Rol actualizado.');
          else await busy(null, () => api.post('/api/admin/roles', { name: name.value.trim(), description: desc.value.trim(), perms, onboarding: copyFrom?.onboarding || [] }), 'Rol creado.');
          load();
        } catch { return false; }
      } }],
    });
  }
  async function deleteRole(r, btn) {
    if (!await confirmDialog({ title: 'Eliminar rol', danger: true, confirmLabel: 'Eliminar', message: `¿Eliminar el rol «${r.name}»? Esta acción no se puede deshacer.` })) return;
    busy(btn, () => api.del(`/api/admin/roles/${r.id}`), 'Rol eliminado.').then(load).catch(() => {});
  }
  mount(root, loading());
  await load();
  ctx.onChanges(['permissions', 'user'], load);
}

// ---------- Proyectos ----------
async function projectsTab(root, ctx) {
  let items = null, defaultId = null, users = [], showArchived = false;
  async function load() {
    try {
      const [p, u] = await Promise.all([api.get('/api/projects'), can('sistema.usuarios') ? api.get('/api/admin/users') : api.get('/api/users')]);
      items = p.items; defaultId = p.default_project; users = u.items.filter(x => x.active);
      draw();
    } catch (err) { mount(root, errorBox(err, load)); }
  }
  function draw() {
    const archivedCount = items.filter(p => p.archived).length;
    const list = items.filter(p => showArchived || !p.archived);
    mount(root,
      h('div', { class: 'toolbar' },
        h('p', { class: 'muted small grow' }, 'Cada proyecto tiene su propio prefijo para numerar las incidencias (por ejemplo APP-001) y su canal de chat.'),
        archivedCount ? h('label', { class: 'check small' }, h('input', { type: 'checkbox', checked: showArchived, onchange: (e) => { showArchived = e.target.checked; draw(); } }), `Mostrar archivados (${archivedCount})`) : null,
        h('button', { class: 'btn primary', onclick: () => editProject(null) }, icon('plus'), 'Nuevo proyecto')),
      list.length ? h('div', { class: 'grid cols-2' }, list.map(p => h('div', { class: 'card adm-project' + (p.archived ? ' archived' : ''), style: { borderLeftColor: p.color } },
        h('div', { class: 'card-head' },
          h('span', { class: 'adm-key-chip', style: { background: p.color } }, p.key),
          h('h3', null, p.name),
          p.id === defaultId ? h('span', { class: 'badge accent', title: 'Se elige al crear elementos nuevos' }, 'Predeterminado') : null,
          p.archived ? h('span', { class: 'badge neutral' }, 'Archivado') : null),
        p.description ? h('p', { class: 'small muted pre' }, p.description) : null,
        h('div', { class: 'row wrap small' },
          h('span', { class: 'badge neutral' }, `${p.open_incidents} incidencias abiertas`),
          h('span', { class: 'badge neutral' }, `${p.open_tasks} tareas abiertas`)),
        h('div', { class: 'row adm-members mt', title: 'Miembros' },
          p.member_ids.slice(0, 10).map(id => { const u = users.find(x => x.id === id) || store.users.find(x => x.id === id); return u ? avatar(u) : null; }),
          h('span', { class: 'small muted' }, `${p.member_ids.length} ${p.member_ids.length === 1 ? 'miembro' : 'miembros'}`)),
        h('div', { class: 'row end mt wrap' },
          h('button', { class: 'btn sm ghost', onclick: () => members(p) }, icon('users', 14), 'Miembros'),
          !p.archived && p.id !== defaultId ? h('button', { class: 'btn sm ghost', onclick: (e) => busy(e.currentTarget, () => api.patch(`/api/projects/${p.id}`, { make_default: true, version: p.version }), 'Proyecto predeterminado cambiado.').then(load).catch(() => {}) }, 'Hacer predeterminado') : null,
          h('button', { class: 'btn sm', onclick: (e) => archive(p, e.currentTarget) }, p.archived ? 'Reactivar' : 'Archivar'),
          h('button', { class: 'btn sm primary', onclick: () => editProject(p) }, icon('edit', 14), 'Editar')))))
        : emptyState('No hay proyectos', 'Crea el primero con «Nuevo proyecto».'));
  }
  function colorPicker(value) {
    const input = h('input', { type: 'color', value, class: 'adm-color' });
    const swatches = h('div', { class: 'row wrap' }, COLORS.map(c => h('button', { type: 'button', class: 'adm-swatch', style: { background: c }, title: c, onclick: () => { input.value = c; } })));
    return { input, el: h('div', { class: 'row wrap' }, input, swatches) };
  }
  function editProject(p) {
    const key = h('input', { value: p?.key || '', maxlength: 8, placeholder: 'APP', class: 'mono', oninput: () => { key.value = key.value.toUpperCase().replace(/[^A-Z0-9]/g, ''); } });
    const name = h('input', { value: p?.name || '', placeholder: 'App móvil' });
    const color = colorPicker(p?.color || COLORS[(items.length) % COLORS.length]);
    const desc = h('textarea', { rows: 3, placeholder: 'Opcional: de qué trata el proyecto' });
    desc.value = p?.description || '';
    const mem = p ? null : peopleChecks(users, [store.me.id]);
    modal({
      title: p ? `Editar ${p.key} · ${p.name}` : 'Nuevo proyecto', wide: !p,
      body: h('div', { class: 'col gap-lg' },
        h('div', { class: 'form-grid' },
          field('Prefijo', key, { required: true, hint: p ? 'De 2 a 8 letras o números. No se puede cambiar si el proyecto ya tiene incidencias.' : 'De 2 a 8 letras o números, empezando por letra. Las incidencias serán APP-001, APP-002…' }),
          field('Nombre', name, { required: true }),
          field('Color', color.el, { cls: 'span2' }),
          field('Descripción', desc, { cls: 'span2' })),
        mem ? h('div', null, h('div', { class: 'section-title' }, 'Miembros'), mem.el, h('div', { class: 'tiny muted mt' }, 'Tú siempre quedas como miembro del proyecto que creas.')) : null),
      actions: [{ label: 'Cancelar' }, { label: p ? 'Guardar' : 'Crear proyecto', kind: 'primary', onClick: async () => {
        if (!/^[A-Z][A-Z0-9]{1,7}$/.test(key.value)) { toast('El prefijo debe tener de 2 a 8 letras o números y empezar por una letra.', 'error'); key.focus(); return false; }
        if (['T', 'DEC'].includes(key.value)) { toast('El prefijo no puede ser T ni DEC.', 'error'); return false; }
        if (!name.value.trim()) { toast('Escribe el nombre del proyecto.', 'error'); name.focus(); return false; }
        const body = { key: key.value, name: name.value.trim(), color: color.input.value, description: desc.value.trim() };
        try {
          if (p) await busy(null, () => api.patch(`/api/projects/${p.id}`, { ...body, version: p.version }), 'Proyecto actualizado.');
          else await busy(null, () => api.post('/api/projects', { ...body, uid: uid(), member_ids: mem.value() }), 'Proyecto creado.');
          load();
        } catch { return false; }
      } }],
    });
  }
  function members(p) {
    const mem = peopleChecks(users, p.member_ids);
    modal({
      title: `Miembros de ${p.key} · ${p.name}`,
      body: h('div', { class: 'col gap-lg' }, h('p', { class: 'small muted' }, 'Los miembros ven el proyecto, sus incidencias, tareas y su canal de chat. Quien tiene «Ver todos los proyectos» lo ve igualmente.'), mem.el),
      actions: [{ label: 'Cancelar' }, { label: 'Guardar', kind: 'primary', onClick: async () => {
        try { await busy(null, () => api.patch(`/api/projects/${p.id}`, { member_ids: mem.value(), version: p.version }), 'Miembros actualizados.'); load(); } catch { return false; }
      } }],
    });
  }
  async function archive(p, btn) {
    if (!p.archived && !await confirmDialog({ title: `Archivar ${p.name}`, confirmLabel: 'Archivar',
      message: 'El proyecto dejará de aparecer al crear elementos nuevos. Todo su contenido se conserva y puedes reactivarlo cuando quieras.' })) return;
    busy(btn, () => api.patch(`/api/projects/${p.id}`, { archived: !p.archived, version: p.version }), p.archived ? 'Proyecto reactivado.' : 'Proyecto archivado.').then(load).catch(load);
  }
  mount(root, loading());
  await load();
  ctx.onChanges(['project', 'permissions', 'user'], load);
}

// ---------- Bienvenida ----------
async function onboardingTab(root, ctx) {
  let roles = null, progress = null, selected = null;
  const editorBox = h('div', { class: 'card' });
  const progressBox = h('div', { class: 'list-card' });
  mount(root,
    h('p', { class: 'muted small mb' }, 'Cuando alguien entra por primera vez ve una lista de primeros pasos según su rol (en «Mi cuenta → Primeros pasos»). Aquí defines esas listas y ves cómo va cada persona.'),
    h('div', { class: 'grid cols-2 adm-onb' }, editorBox, progressBox));
  let dirty = false;
  async function load(keepEditor) {
    try {
      const [r, o] = await Promise.all([api.get('/api/admin/roles'), api.get('/api/admin/onboarding')]);
      roles = r.items; progress = o.items;
      if (!selected || !roles.some(x => x.id === selected)) selected = roles[0]?.id;
      if (!keepEditor || !dirty) drawEditor();
      drawProgress();
    } catch (err) { mount(root, errorBox(err, () => load())); }
  }
  function drawEditor() {
    dirty = false;
    const role = roles.find(r => r.id === selected);
    const steps = [...(role?.onboarding || [])];
    const list = h('div', { class: 'col' });
    const drawList = () => mount(list, steps.length ? steps.map((s, i) => {
      const inp = h('input', { value: s, maxlength: 200, oninput: () => { steps[i] = inp.value; dirty = true; } });
      return h('div', { class: 'row adm-step' }, h('span', { class: 'adm-step-n' }, i + 1), inp,
        h('button', { class: 'btn sm ghost icon', title: 'Subir', disabled: i === 0, onclick: () => { [steps[i - 1], steps[i]] = [steps[i], steps[i - 1]]; dirty = true; drawList(); } }, icon('left', 14)),
        h('button', { class: 'btn sm ghost icon', title: 'Bajar', disabled: i === steps.length - 1, onclick: () => { [steps[i + 1], steps[i]] = [steps[i], steps[i + 1]]; dirty = true; drawList(); } }, icon('right', 14)),
        h('button', { class: 'btn sm ghost icon', title: 'Quitar', onclick: () => { steps.splice(i, 1); dirty = true; drawList(); } }, icon('x', 14)));
    }) : h('div', { class: 'empty small' }, 'Sin pasos: esta persona no verá la lista de bienvenida.'));
    drawList();
    const newStep = h('input', { placeholder: 'Nuevo paso, p. ej. «Leer la guía del equipo»', maxlength: 200,
      onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } } });
    const add = () => { const v = newStep.value.trim(); if (!v) return; steps.push(v); newStep.value = ''; dirty = true; drawList(); newStep.focus(); };
    const save = h('button', { class: 'btn primary', onclick: () => {
      const clean = steps.map(s => s.trim()).filter(Boolean);
      busy(save, () => api.patch(`/api/admin/roles/${role.id}`, { onboarding: clean }), 'Pasos guardados.').then(() => { dirty = false; load(); }).catch(() => {});
    } }, 'Guardar pasos');
    const sel = selectEl(roles.map(r => [String(r.id), `${r.name} (${r.users})`]), String(selected), {
      onchange: () => { if (dirty && !window.confirm('Hay cambios sin guardar en estos pasos. ¿Descartarlos?')) { sel.value = String(selected); return; } selected = Number(sel.value); drawEditor(); },
    });
    mount(editorBox,
      h('div', { class: 'card-head' }, h('h3', null, 'Primeros pasos por rol')),
      field('Rol', sel),
      h('div', { class: 'mt' }, list),
      h('div', { class: 'row mt' }, newStep, h('button', { class: 'btn', onclick: add }, icon('plus', 14), 'Añadir')),
      h('div', { class: 'row end mt' }, h('span', { class: 'tiny muted grow' }, 'Si cambias el texto de un paso, se considera un paso nuevo (sin hacer).'), save));
  }
  function drawProgress() {
    const sorted = [...progress].sort((a, b) => a.percent - b.percent || a.user_name.localeCompare(b.user_name));
    mount(progressBox,
      h('div', { class: 'card-head' }, h('h3', null, 'Progreso de cada persona')),
      sorted.length ? h('div', null, sorted.map(p => {
        const u = store.users.find(x => x.id === p.user_id) || { display_name: p.user_name };
        const det = h('details', { class: 'adm-onb-person' },
          h('summary', { class: 'row' }, avatar(u), h('div', { class: 'grow' },
            h('div', { class: 'row between' }, h('span', null, p.user_name), h('span', { class: `small ${p.percent === 100 ? 'ok-text' : 'muted'}` }, p.total ? `${p.done}/${p.total}` : 'Sin pasos')),
            h('div', { class: 'progress' + (p.percent === 100 ? ' ok' : '') }, h('i', { style: { width: `${p.percent}%` } })))),
          p.items.length ? h('ul', { class: 'adm-onb-items' }, p.items.map(i => h('li', { class: i.done_at ? 'done' : '' },
            i.done_at ? icon('check', 13) : h('span', { class: 'adm-dot' }), h('span', { class: 'grow' }, i.item),
            i.done_at ? h('span', { class: 'tiny muted' }, fmtRelative(i.done_at)) : null))) : h('div', { class: 'tiny muted' }, 'Su rol no tiene pasos de bienvenida.'));
        return det;
      })) : h('div', { class: 'empty small' }, 'No hay personas activas.'));
  }
  mount(editorBox, loading());
  await load();
  ctx.onChanges(['permissions', 'user'], () => load(true));
}

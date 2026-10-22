// Mi cuenta: perfil, seguridad (contraseña y verificación en dos pasos), sesiones, avisos,
// primeros pasos y este equipo (servidores recordados, programa y actualizaciones).
import { api } from '../api.js';
import { pollLocal, refreshMe, shareSession, store } from '../state.js';
import {
  busy, confirmDialog, errorBox, field, fmtDateTime, fmtRelative, h, icon, loadJSON, loading, modal, mount, pageHead,
  saveJSON, tabs, toast,
} from '../ui.js';
import { renderServerPanel } from './settings.js';

const TABS = [
  { key: 'perfil', label: 'Perfil', icon: 'user' },
  { key: 'seguridad', label: 'Seguridad', icon: 'lock' },
  { key: 'sesiones', label: 'Sesiones abiertas', icon: 'key' },
  { key: 'avisos', label: 'Avisos', icon: 'bell' },
  { key: 'primeros-pasos', label: 'Primeros pasos', icon: 'flag' },
  { key: 'equipo', label: 'Este equipo', icon: 'server' },
];

const minPassword = () => Number(store.settings?.password_min_length) || 10;

export default async function render(ctx) {
  const { root } = ctx;
  const active = TABS.some(t => t.key === ctx.params.tab) ? ctx.params.tab : 'perfil';
  const items = TABS.map(t => t.key === 'primeros-pasos' && store.onboardingPending ? { ...t, count: store.onboardingPending } : t);
  const body = h('div');
  mount(root, pageHead('Mi cuenta'), tabs(items, active, (k) => { location.hash = `#/cuenta/${k}`; }), body);
  ctx.setTitle(`Mi cuenta · ${TABS.find(t => t.key === active).label}`);
  const views = { perfil: profileTab, seguridad: securityTab, sesiones: sessionsTab, avisos: prefsTab, 'primeros-pasos': onboardingTab, equipo: deviceTab };
  return views[active](body, ctx);
}

// ---------- Perfil ----------
function profileTab(root) {
  const me = store.me;
  const name = h('input', { value: me.display_name, maxlength: 60 });
  const email = h('input', { type: 'email', value: me.email || '', maxlength: 120, placeholder: 'opcional' });
  const save = h('button', { class: 'btn primary', type: 'submit' }, 'Guardar');
  const form = h('form', { class: 'card', onsubmit: (e) => {
    e.preventDefault();
    if (!name.value.trim()) { toast('El nombre no puede quedar vacío.', 'error'); name.focus(); return; }
    busy(save, () => api.patch('/api/me', { display_name: name.value.trim(), email: email.value.trim() }), 'Perfil guardado.').then(refreshMe).catch(() => {});
  } },
  h('h3', null, 'Tus datos'),
  h('div', { class: 'form-grid' },
    field('Nombre visible', name, { required: true, hint: 'Es como te ven los demás en el chat, las incidencias y las tareas.' }),
    field('Correo', email)),
  h('div', { class: 'kv mt' },
    h('span', null, 'Usuario para entrar'), h('b', { class: 'mono' }, me.username),
    h('span', null, 'Rol'), h('span', null, me.role_label || me.role_key || '—'),
    h('span', null, 'Capacidad semanal'), h('span', null, `${me.capacity_hours ?? 40} h`)),
  h('p', { class: 'tiny muted' }, 'El usuario, el rol y la capacidad los cambia un administrador.'),
  h('div', { class: 'row end' }, save));
  const current = () => document.documentElement.dataset.theme || 'dark';
  const themeSeg = h('div', { class: 'seg' });
  const drawTheme = () => mount(themeSeg, [['light', 'Claro', 'sun'], ['dark', 'Oscuro', 'moon']].map(([k, l, ic]) =>
    h('button', { type: 'button', class: current() === k ? 'on' : '', onclick: () => { document.documentElement.dataset.theme = k; saveJSON('dc.theme', k); drawTheme(); } }, icon(ic, 14), l)));
  drawTheme();
  mount(root, h('div', { class: 'col gap-lg adm-narrow' }, form,
    h('div', { class: 'card' }, h('h3', null, 'Apariencia'), h('div', { class: 'row' }, h('span', { class: 'small muted grow' }, 'Tema de la interfaz en este equipo'), themeSeg))));
  name.focus();
}

// ---------- Seguridad ----------
function securityTab(root) {
  const pwCard = h('div', { class: 'card' });
  const tfaCard = h('div', { class: 'card' });
  mount(root, h('div', { class: 'col gap-lg adm-narrow' }, pwCard, tfaCard));

  const cur = h('input', { type: 'password', autocomplete: 'current-password' });
  const p1 = h('input', { type: 'password', autocomplete: 'new-password' });
  const p2 = h('input', { type: 'password', autocomplete: 'new-password' });
  const pwBtn = h('button', { class: 'btn primary', type: 'submit' }, 'Cambiar contraseña');
  mount(pwCard, h('form', { onsubmit: (e) => {
    e.preventDefault();
    if (!cur.value) { toast('Escribe tu contraseña actual.', 'error'); cur.focus(); return; }
    if (p1.value.length < minPassword()) { toast(`La nueva contraseña debe tener al menos ${minPassword()} caracteres.`, 'error'); p1.focus(); return; }
    if (p1.value !== p2.value) { toast('Las contraseñas nuevas no coinciden.', 'error'); p2.focus(); return; }
    busy(pwBtn, () => api.post('/api/me/password', { current: cur.value, new_password: p1.value }), 'Contraseña cambiada. Se han cerrado tus sesiones en otros equipos.')
      .then(() => { cur.value = p1.value = p2.value = ''; }).catch(() => {});
  } },
  h('h3', null, 'Contraseña'),
  h('div', { class: 'form-grid' },
    field('Contraseña actual', cur, { required: true, cls: 'span2' }),
    field('Nueva contraseña', p1, { required: true, hint: `Mínimo ${minPassword()} caracteres. Mejor una frase larga que algo corto y raro.` }),
    field('Repite la nueva contraseña', p2, { required: true })),
  h('p', { class: 'tiny muted mt' }, 'Al cambiarla se cierran tus sesiones en los demás equipos; en este sigues dentro.'),
  h('div', { class: 'row end' }, pwBtn)));

  const draw2fa = () => {
    const on = !!store.me.totp_enabled;
    if (on) {
      const pass = h('input', { type: 'password', autocomplete: 'current-password', placeholder: 'Tu contraseña' });
      const off = h('button', { class: 'btn danger', onclick: async () => {
        if (!pass.value) { toast('Escribe tu contraseña para desactivarla.', 'error'); pass.focus(); return; }
        if (!await confirmDialog({ title: 'Desactivar la verificación en dos pasos', danger: true, confirmLabel: 'Desactivar', message: 'Tu cuenta quedará protegida solo por la contraseña. ¿Seguro?' })) return;
        busy(off, () => api.post('/api/me/2fa/disable', { password: pass.value }), 'Verificación en dos pasos desactivada.').then(async () => { await refreshMe(); draw2fa(); }).catch(() => {});
      } }, 'Desactivar');
      mount(tfaCard,
        h('div', { class: 'card-head' }, h('h3', null, 'Verificación en dos pasos'), h('span', { class: 'badge ok' }, icon('shield', 12), 'Activada')),
        h('p', { class: 'small muted' }, 'Al entrar desde un equipo nuevo se te pide el código de 6 cifras de tu aplicación de autenticación. Si pierdes el móvil, usa uno de tus códigos de recuperación o pide a un administrador que te la quite.'),
        h('div', { class: 'row mt' }, pass, off));
      return;
    }
    const start = h('button', { class: 'btn primary', onclick: () => busy(start, () => api.post('/api/me/2fa/start')).then(enroll).catch(() => {}) }, icon('shield', 14), 'Activar');
    mount(tfaCard,
      h('div', { class: 'card-head' }, h('h3', null, 'Verificación en dos pasos'), h('span', { class: 'badge neutral' }, 'Desactivada')),
      h('p', { class: 'small muted' }, 'Añade una segunda comprobación al entrar: además de la contraseña, un código que cambia cada 30 segundos en tu móvil. Así nadie puede entrar aunque averigüe tu contraseña.'),
      h('div', { class: 'row end' }, start));
  };
  const enroll = (r) => {
    const code = h('input', { inputmode: 'numeric', maxlength: 6, placeholder: '123456', autocomplete: 'one-time-code', class: 'mono' });
    const ok = h('button', { class: 'btn primary', type: 'submit' }, 'Confirmar y activar');
    mount(tfaCard, h('form', { class: 'col gap-lg', onsubmit: (e) => {
      e.preventDefault();
      if (!/^\d{6}$/.test(code.value.trim())) { toast('Escribe las 6 cifras que muestra la aplicación.', 'error'); code.focus(); return; }
      busy(ok, () => api.post('/api/me/2fa/confirm', { code: code.value.trim() })).then(async (res) => {
        await refreshMe();
        draw2fa();
        showRecoveryCodes(res.recovery_codes || []);
      }).catch(() => {});
    } },
    h('h3', null, 'Activar la verificación en dos pasos'),
    h('ol', { class: 'small adm-steps' },
      h('li', null, 'Abre en el móvil una aplicación de autenticación (Google Authenticator, Microsoft Authenticator, Authy…).'),
      h('li', null, 'Añade una cuenta nueva escribiendo esta clave (o pega el enlace si la aplicación lo permite):'),
      h('li', null, 'Escribe abajo el código de 6 cifras que te muestra.')),
    h('div', { class: 'secret mono' }, r.secret.replace(/(.{4})/g, '$1 ').trim()),
    h('details', { class: 'small' }, h('summary', null, 'Enlace de configuración'), h('div', { class: 'tiny muted mono adm-break' }, r.uri)),
    field('Código de la aplicación', code, { required: true }),
    h('div', { class: 'row end' }, h('button', { class: 'btn ghost', type: 'button', onclick: draw2fa }, 'Cancelar'), ok)));
    code.focus();
  };
  const showRecoveryCodes = (codes) => {
    const text = codes.join('\n');
    modal({
      title: 'Guarda tus códigos de recuperación',
      body: h('div', { class: 'col gap-lg' },
        h('p', null, 'Si pierdes el móvil podrás entrar con uno de estos códigos. Cada uno sirve una sola vez. Guárdalos en un lugar seguro (no en este mismo equipo si es posible): no se volverán a mostrar.'),
        h('div', { class: 'codes mono' }, codes.map(c => h('span', null, c)))),
      actions: [{ label: 'Copiar', onClick: async () => { try { await navigator.clipboard.writeText(text); toast('Códigos copiados.', 'ok'); } catch { toast('Cópialos a mano.', 'warn'); } return false; } },
        { label: 'Ya los he guardado', kind: 'primary' }],
    });
  };
  draw2fa();
}

// ---------- Sesiones ----------
async function sessionsTab(root, ctx) {
  const run = async () => {
    let items;
    try { items = (await api.get('/api/me/sessions')).items; } catch (err) { mount(root, errorBox(err, run)); return; }
    const others = items.filter(s => !s.current);
    const closeAll = h('button', { class: 'btn', disabled: !others.length, onclick: async () => {
      if (!await confirmDialog({ title: 'Cerrar las demás sesiones', confirmLabel: 'Cerrar', message: `Se cerrarán ${others.length} sesiones en otros equipos o navegadores. Esta seguirá abierta.` })) return;
      busy(closeAll, async () => { for (const s of others) await api.del(`/api/me/sessions/${s.id}`); }, 'Sesiones cerradas.').then(run).catch(run);
    } }, icon('logout', 14), 'Cerrar todas las demás');
    mount(root, h('div', { class: 'col gap-lg adm-narrow' },
      h('div', { class: 'row' }, h('p', { class: 'small muted grow' }, 'Equipos y navegadores donde tu cuenta está abierta. Si ves alguno que no reconoces, ciérralo y cambia tu contraseña.'), closeAll),
      h('div', { class: 'list-card' }, items.map(s => h('div', { class: 'list-item adm-session' },
        icon(s.current ? 'check' : 'server', 18),
        h('div', { class: 'grow' },
          h('div', null, h('b', null, s.client_label || 'Equipo sin nombre'), s.current ? h('span', { class: 'badge ok adm-ml' }, 'Esta sesión') : null),
          h('div', { class: 'tiny muted' }, [`IP ${s.client_ip || '—'}`, `abierta el ${fmtDateTime(s.created_at)}`,
            s.last_used_at ? `último uso ${fmtRelative(s.last_used_at)}` : null, s.remember ? `recordada hasta el ${fmtDateTime(s.expires_at)}` : `caduca el ${fmtDateTime(s.expires_at)}`].filter(Boolean).join(' · '))),
        s.current ? null : h('button', { class: 'btn sm', onclick: (e) => busy(e.currentTarget, () => api.del(`/api/me/sessions/${s.id}`), 'Sesión cerrada.').then(run).catch(() => {}) }, 'Cerrar'))))));
  };
  mount(root, loading());
  await run();
  void ctx;
}

// ---------- Avisos ----------
async function prefsTab(root) {
  let prefs;
  const run = async () => {
    try { prefs = await api.get('/api/notifications/prefs'); draw(); } catch (err) { mount(root, errorBox(err, run)); }
  };
  function draw() {
    const rows = prefs.items.map(p => ({ p, app: h('input', { type: 'checkbox', checked: p.in_app }), desk: h('input', { type: 'checkbox', checked: p.desktop }) }));
    for (const r of rows) {
      r.app.addEventListener('change', () => { r.desk.disabled = !r.app.checked; });
      r.desk.disabled = !r.app.checked;
    }
    const muteUntil = prefs.mute_until && new Date(prefs.mute_until) > new Date() ? prefs.mute_until : null;
    let mute = muteUntil;
    const muteInfo = h('div', { class: 'small' });
    const custom = h('input', { type: 'datetime-local', class: 'adm-dt' });
    const drawMute = () => { muteInfo.textContent = mute ? `Silenciado hasta el ${fmtDateTime(mute)}.` : 'No está silenciado: recibes los avisos con normalidad.'; muteInfo.className = mute ? 'small warn-text' : 'small muted'; };
    const setMute = (d) => { mute = d ? d.toISOString() : null; drawMute(); };
    drawMute();
    const tomorrow8 = () => { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(8, 0, 0, 0); return d; };
    custom.addEventListener('change', () => { if (custom.value) setMute(new Date(custom.value)); });
    const all = (key, val) => rows.forEach(r => { r[key].checked = val; r[key].dispatchEvent(new Event('change')); });
    const st = prefs.settings || {};
    const quietOn = h('input', { type: 'checkbox', checked: !!(st.quiet_from && st.quiet_to) });
    const quietFrom = h('input', { type: 'time', value: st.quiet_from || '20:00', class: 'adm-time' });
    const quietTo = h('input', { type: 'time', value: st.quiet_to || '08:00', class: 'adm-time' });
    const digest = h('input', { type: 'time', value: st.digest_time || '09:00', class: 'adm-time' });
    const scope = h('select', null, Object.entries(prefs.task_scopes || {}).map(([k, label]) =>
      h('option', { value: k, selected: k === (st.task_scope || 'proyecto') }, label)));
    const syncQuiet = () => { quietFrom.disabled = quietTo.disabled = !quietOn.checked; };
    quietOn.addEventListener('change', syncQuiet);
    syncQuiet();
    const payload = () => ({
      items: rows.map(r => ({ type: r.p.type, in_app: r.app.checked, desktop: r.app.checked && r.desk.checked })), mute_until: mute,
      settings: { quiet_from: quietOn.checked ? quietFrom.value : null, quiet_to: quietOn.checked ? quietTo.value : null,
        digest_time: digest.value || '09:00', task_scope: scope.value },
    });
    const save = h('button', { class: 'btn primary', onclick: () => busy(save, () => api.put('/api/notifications/prefs', payload()),
      'Preferencias guardadas.').then((res) => { prefs = res; store.prefs = res; shareSession(); }).catch(() => {}) }, 'Guardar preferencias');
    const preset = (key) => busy(null, () => api.put('/api/notifications/prefs', { preset: key }),
      `Ajuste aplicado: ${prefs.presets?.[key] || key}.`).then((res) => { prefs = res; store.prefs = res; shareSession(); draw(); }).catch(() => {});
    const testBtn = store.local?.desktop ? h('button', { class: 'btn sm', onclick: () => busy(testBtn, () => api.local('POST', '/local/notify', {
      title: 'Aviso de prueba de DEV CONTROL', body: 'Así verás los avisos aunque la ventana esté cerrada. Púlsalo para abrir el programa.', native: true,
    }), 'Aviso enviado: debería aparecer abajo a la derecha.').catch(() => {}) }, icon('bell', 14), 'Probar un aviso de Windows') : null;
    const browserAsk = !store.local?.desktop && 'Notification' in window && Notification.permission === 'default'
      ? h('div', { class: 'callout info small row' }, h('span', { class: 'grow' }, 'Estás usando DEV CONTROL en un navegador. Para ver avisos de escritorio, permite las notificaciones.'),
        h('button', { class: 'btn sm', onclick: (e) => { Notification.requestPermission().then(() => e.target.closest('.callout')?.remove()); } }, 'Permitir'))
      : null;
    const byType = new Map(rows.map(r => [r.p.type, r]));
    const areas = (prefs.areas || [{ label: 'Avisos', types: rows.map(r => r.p.type) }]).map(a => ({ ...a, rows: a.types.map(t => byType.get(t)).filter(Boolean) }));
    const listed = new Set(areas.flatMap(a => a.types));
    const others = rows.filter(r => !listed.has(r.p.type));
    if (others.length) areas.push({ label: 'Otros', rows: others });
    mount(root, h('div', { class: 'col gap-lg adm-narrow' },
      browserAsk,
      h('div', { class: 'card' }, h('h3', null, 'Ajuste rápido'),
        h('p', { class: 'small muted' }, 'Elige cuánto quieres enterarte. Después puedes afinar cada tipo de aviso más abajo.'),
        h('div', { class: 'row wrap mt' },
          h('button', { class: 'btn sm', onclick: () => preset('todo') }, 'Todo'),
          h('button', { class: 'btn sm', onclick: () => preset('importante') }, 'Lo importante'),
          h('button', { class: 'btn sm', onclick: () => preset('solo_mio') }, 'Solo lo mío'),
          h('span', { class: 'grow' }), testBtn)),
      h('div', { class: 'card' }, h('h3', null, 'Tareas, resumen y horario'),
        field('De qué tareas quieres enterarte', scope, { hint: 'Siempre te avisamos de lo tuyo, de lo que sigues y de los proyectos que sigues (botón «Seguir»).' }),
        field('«Tus pendientes»: resumen de cada mañana (laborables)', digest),
        h('label', { class: 'row mt' }, quietOn, h('span', null, 'Horario de silencio: sin avisos de Windows de'), quietFrom, h('span', null, 'a'), quietTo),
        h('p', { class: 'tiny muted' }, 'En el horario de silencio los avisos siguen llegando a «Avisos», pero Windows no los muestra (salvo los urgentes: incidencias críticas, rechazos y seguridad).')),
      h('div', { class: 'card' }, h('h3', null, 'No molestar'),
        muteInfo,
        h('div', { class: 'row wrap mt' },
          h('button', { class: 'btn sm', onclick: () => setMute(new Date(Date.now() + 3600e3)) }, '1 hora'),
          h('button', { class: 'btn sm', onclick: () => setMute(new Date(Date.now() + 4 * 3600e3)) }, '4 horas'),
          h('button', { class: 'btn sm', onclick: () => setMute(tomorrow8()) }, 'Hasta mañana a las 8:00'),
          h('button', { class: 'btn sm', onclick: () => setMute(new Date(Date.now() + 7 * 86400e3)) }, '1 semana'),
          h('span', { class: 'small muted' }, 'o hasta'), custom,
          mute ? h('button', { class: 'btn sm ghost', onclick: () => { custom.value = ''; setMute(null); } }, 'Quitar silencio') : null),
        h('p', { class: 'tiny muted mt' }, 'Mientras está silenciado no suenan ni aparecen avisos en el escritorio, pero siguen llegando a «Avisos». Recuerda guardar.')),
      h('div', { class: 'card flat' },
        h('div', { class: 'table-wrap adm-noborder' }, h('table', { class: 'table' },
          h('thead', null, h('tr', null, h('th', null, 'Tipo de aviso'),
            h('th', { class: 'adm-c' }, 'En la app', h('div', null, h('button', { class: 'btn sm ghost', onclick: () => all('app', !rows.every(r => r.app.checked)) }, 'todos'))),
            h('th', { class: 'adm-c' }, 'Aviso de Windows', h('div', null, h('button', { class: 'btn sm ghost', onclick: () => all('desk', !rows.every(r => r.desk.checked)) }, 'todos'))))),
          areas.map(a => h('tbody', null,
            h('tr', { class: 'adm-group' }, h('th', { colspan: 3 }, a.label)),
            a.rows.map(r => h('tr', null, h('td', null, r.p.label), h('td', { class: 'adm-c' }, r.app), h('td', { class: 'adm-c' }, r.desk)))))))),
      h('p', { class: 'tiny muted' }, '«En la app»: el aviso aparece en la campana. Si lo desmarcas, no se crea. «Aviso de Windows»: además sale un aviso en la esquina de la pantalla aunque DEV CONTROL esté cerrado en la bandeja; al pulsarlo se abre lo que ha cambiado.'),
      h('div', { class: 'adm-savebar' }, h('span', { class: 'grow' }), save)));
  }
  mount(root, loading());
  await run();
}

// ---------- Primeros pasos ----------
async function onboardingTab(root, ctx) {
  const run = async (items) => {
    if (!items) {
      try { items = (await api.get('/api/onboarding')).items; } catch (err) { mount(root, errorBox(err, () => run())); return; }
    }
    const done = items.filter(i => i.done_at).length;
    const pct = items.length ? Math.round(100 * done / items.length) : 100;
    mount(root, h('div', { class: 'col gap-lg adm-narrow' },
      h('div', { class: 'card' },
        h('div', { class: 'card-head' }, h('h3', null, 'Tus primeros pasos'), h('span', { class: `small ${pct === 100 ? 'ok-text' : 'muted'}` }, items.length ? `${done} de ${items.length}` : '')),
        items.length ? [
          h('div', { class: 'progress big-progress' + (pct === 100 ? ' ok' : '') }, h('i', { style: { width: `${pct}%` } })),
          pct === 100 ? h('div', { class: 'callout ok small mt' }, '¡Todo listo! Ya conoces lo básico de DEV CONTROL.') : h('p', { class: 'small muted mt' }, 'Marca cada paso cuando lo hayas hecho. Esta lista depende de tu rol.'),
          h('div', { class: 'mt' }, items.map(i => {
            const cb = h('input', { type: 'checkbox', checked: !!i.done_at });
            cb.addEventListener('change', () => {
              cb.disabled = true;
              api.post('/api/onboarding', { item: i.item, done: cb.checked }).then(async (r) => { await refreshMe(); run(r.items); })
                .catch((err) => { toast(err.message, 'error'); cb.checked = !cb.checked; cb.disabled = false; });
            });
            return h('label', { class: 'goal' + (i.done_at ? ' done' : '') }, cb, h('span', { class: 'gt grow' }, i.item),
              i.done_at ? h('span', { class: 'tiny muted' }, fmtRelative(i.done_at)) : null);
          }))] : h('div', { class: 'empty small' }, 'Tu rol no tiene pasos de bienvenida.')),
      h('div', { class: 'card' }, h('h3', null, 'Atajos útiles'),
        h('div', { class: 'row wrap' },
          h('a', { class: 'btn sm', href: '#/cuenta/seguridad' }, icon('shield', 14), 'Verificación en dos pasos'),
          h('a', { class: 'btn sm', href: '#/cuenta/avisos' }, icon('bell', 14), 'Configurar avisos'),
          h('a', { class: 'btn sm', href: '#/wiki' }, icon('book', 14), 'Wiki del equipo'),
          h('a', { class: 'btn sm', href: '#/equipo/daily' }, icon('week', 14), 'Daily')))));
  };
  mount(root, loading());
  await run();
  void ctx;
}

// ---------- Este equipo ----------
async function deviceTab(root, ctx) {
  const profilesBox = h('div', { class: 'card' });
  const panel = h('div');
  mount(root, h('div', { class: 'col gap-lg' }, profilesBox, panel));
  let sig = '';
  const drawProfiles = (force) => {
    const s = store.local;
    const profiles = s?.profiles || [];
    const next = JSON.stringify([profiles, s?.server, s?.server_label]);
    if (!force && next === sig) return;
    sig = next;
    const isCurrent = (p) => s && (p.server === s.server || p.server === s.server_label);
    mount(profilesBox,
      h('h3', null, 'Servidores recordados'),
      h('p', { class: 'small muted' }, 'Servidores a los que te has conectado desde este equipo. Para conectarte a otro, pulsa el indicador de conexión de arriba y elige «Cambiar de servidor».'),
      profiles.length ? h('div', { class: 'list-card' }, profiles.map(p => h('div', { class: 'list-item' }, icon('server', 16),
        h('div', { class: 'grow' }, h('div', null, h('b', { class: 'mono' }, p.server), isCurrent(p) ? h('span', { class: 'badge ok adm-ml' }, 'Conectado ahora') : null),
          h('div', { class: 'tiny muted' }, [p.label, p.last_used ? `usado ${fmtRelative(p.last_used)}` : null].filter(Boolean).join(' · '))),
        h('button', { class: 'btn sm ghost', onclick: async (e) => {
          if (!await confirmDialog({ title: 'Olvidar servidor', confirmLabel: 'Olvidar', message: `Se quitará ${p.server} de la lista de servidores recordados de este equipo. No se borra nada del servidor.` })) return;
          busy(e.target, () => api.local('POST', '/local/profiles/forget', { server: p.server }), 'Servidor olvidado.').then(() => pollLocal()).then(() => drawProfiles(true)).catch(() => {});
        } }, 'Olvidar'))))
        : h('div', { class: 'empty small' }, s?.server_is_default ? 'Usas el servidor predeterminado de este equipo; no hay otros recordados.' : 'No hay servidores recordados.'));
  };
  drawProfiles(true);
  ctx.on('local-status', () => drawProfiles(false));
  await renderServerPanel(panel, ctx);
  void loadJSON;
}

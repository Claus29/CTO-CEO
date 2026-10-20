// Bóveda de credenciales: configuración de la frase maestra, desbloqueo, secretos visibles para el
// rol, revelar con confirmación (se oculta a los 30 s), copiar, gestionar y registro de accesos.
import { api } from '../api.js';
import { activeProjects, can, projectById, store } from '../state.js';
import {
  busy, clear, confirmDialog, emptyState, errorBox, field, fmtDateTime, fmtRelative, h, icon, loading, modal, mount,
  pageHead, selectEl, table, tabs, toast, uid,
} from '../ui.js';

const REVEAL_SECONDS = 30;
const DEFAULT_ROLES = [['admin', 'Administrador / CEO'], ['cto', 'Responsable técnico (CTO)'], ['dev', 'Desarrollador'], ['qa', 'QA / Tester'], ['observer', 'Observador']];
const ACTION_LABELS = { configurar: 'Configuró la bóveda', desbloquear: 'Desbloqueó la bóveda', bloquear: 'Bloqueó la bóveda',
  'frase incorrecta': 'Intento de desbloqueo con frase incorrecta', crear: 'Creó el secreto', editar: 'Editó el secreto', ver: 'Consultó el secreto',
  'acceso denegado': 'Intentó consultar un secreto sin permiso' };
const NO_RECOVERY = 'La frase maestra NO se puede recuperar: DEV CONTROL no la guarda en ningún sitio. Si se olvida, todos los secretos guardados se pierden para siempre y habrá que volver a crearlos.';

async function copyToClipboard(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch {
    const ta = h('textarea', { style: { position: 'fixed', opacity: '0' } });
    ta.value = text; document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

export default async function render(ctx) {
  const { root } = ctx;
  const manage = can('boveda.gestionar');
  let tab = 'secretos';
  let state = null;     // { configured, unlocked }
  let items = [];
  let roles = DEFAULT_ROLES;
  const revealed = new Map();  // id → { secret, notes, until, timer }
  const body = h('div');
  const head = h('div');
  let filter = '';

  if (manage && can('sistema.usuarios')) {
    try { roles = (await api.get('/api/admin/roles')).items.map(r => [r.key, r.name]); } catch { /* se usan los predeterminados */ }
  } else {
    const seen = new Map(DEFAULT_ROLES);
    for (const u of store.users) if (u.role_key && !seen.has(u.role_key)) seen.set(u.role_key, u.role_label || u.role_key);
    roles = [...seen.entries()];
  }
  const roleName = (k) => roles.find(r => r[0] === k)?.[1] || k;

  function hide(id) {
    const r = revealed.get(id);
    if (r) clearInterval(r.timer);
    revealed.delete(id);
    drawList();
  }

  async function load() {
    const d = await api.get('/api/vault/items');
    items = d.items;
    state = { configured: d.configured, unlocked: d.unlocked };
  }

  function drawHead() {
    const pill = !state ? null : !state.configured ? h('span', { class: 'badge neutral' }, 'Sin configurar')
      : state.unlocked ? h('span', { class: 'badge ok' }, icon('check', 12), 'Desbloqueada') : h('span', { class: 'badge warn' }, icon('lock', 12), 'Bloqueada');
    const lockBtn = manage && state?.unlocked ? h('button', { class: 'btn', title: 'Bloquear la bóveda para todos hasta que alguien la desbloquee', onclick: (e) => lock(e.currentTarget) }, icon('lock'), 'Bloquear') : null;
    const addBtn = manage && state?.configured ? h('button', { class: 'btn primary', onclick: () => editDialog(null), disabled: !state.unlocked,
      title: state.unlocked ? '' : 'Desbloquea la bóveda para añadir secretos' }, icon('plus'), 'Nuevo secreto') : null;
    mount(head, pageHead('Bóveda de credenciales', pill, lockBtn, addBtn),
      manage && state?.configured ? tabs([{ key: 'secretos', label: 'Secretos', icon: 'key', count: items.length }, { key: 'registro', label: 'Registro de accesos', icon: 'activity' }], tab,
        (k) => { tab = k; drawHead(); draw(); }) : null);
  }

  async function draw() {
    if (tab === 'registro') return drawLog();
    drawList();
  }

  // ---------- configuración inicial ----------
  function setupCard() {
    if (!manage) {
      return h('div', { class: 'card' }, emptyState('La bóveda todavía no está configurada', 'Pide a un administrador que la configure con una frase maestra.'));
    }
    const p1 = h('input', { type: 'password', autocomplete: 'new-password', minlength: 12 });
    const p2 = h('input', { type: 'password', autocomplete: 'new-password' });
    const ack = h('input', { type: 'checkbox' });
    const btn = h('button', { class: 'btn primary', type: 'submit' }, icon('lock'), 'Configurar la bóveda');
    const meter = h('div', { class: 'tiny muted' });
    p1.addEventListener('input', () => { meter.textContent = p1.value.length < 12 ? `Faltan ${12 - p1.value.length} caracteres.` : 'Longitud correcta.'; meter.className = 'tiny ' + (p1.value.length < 12 ? 'warn-text' : 'ok-text'); });
    const form = h('form', { class: 'card col gap-lg vault-setup', onsubmit: async (e) => {
      e.preventDefault();
      if (p1.value.length < 12) { toast('La frase maestra debe tener al menos 12 caracteres.', 'error'); p1.focus(); return; }
      if (p1.value !== p2.value) { toast('Las dos frases no coinciden.', 'error'); p2.focus(); return; }
      if (!ack.checked) { toast('Marca la casilla para confirmar que has guardado la frase maestra.', 'error'); return; }
      try {
        state = await busy(btn, () => api.post('/api/vault/setup', { passphrase: p1.value }), 'Bóveda configurada y desbloqueada.');
        p1.value = ''; p2.value = '';
        await refresh();
      } catch { /* aviso mostrado */ }
    } },
    h('h2', null, 'Configurar la bóveda'),
    h('p', null, 'La bóveda guarda contraseñas, claves de API y otros secretos cifrados en el servidor. Para cifrarlos se usa una ',
      h('b', null, 'frase maestra'), ' que eliges ahora. Cada vez que se reinicie el servidor, alguien con permiso para gestionar la bóveda tendrá que desbloquearla con esa frase.'),
    h('div', { class: 'callout danger' }, h('b', null, 'Muy importante: '), NO_RECOVERY,
      ' Apúntala en un lugar seguro (por ejemplo, en papel, guardado bajo llave) antes de continuar.'),
    h('div', { class: 'form-grid' },
      field('Frase maestra', p1, { required: true, hint: 'Mínimo 12 caracteres. Mejor una frase larga que puedas recordar, p. ej. «el-gato-azul-come-pipas-en-2026».' }),
      field('Repite la frase', p2, { required: true })), meter,
    h('label', { class: 'check' }, ack, 'He guardado la frase maestra en un lugar seguro y entiendo que no se puede recuperar.'),
    h('div', { class: 'row end' }, btn));
    setTimeout(() => p1.focus(), 30);
    return form;
  }

  // ---------- desbloqueo ----------
  function unlockCard() {
    if (!manage) {
      return h('div', { class: 'callout warn mb' }, icon('lock', 14), ' La bóveda está bloqueada (por ejemplo, porque se ha reiniciado el servidor). Puedes ver los nombres, pero no revelar los secretos hasta que un administrador la desbloquee.');
    }
    const pass = h('input', { type: 'password', autocomplete: 'current-password', placeholder: 'Frase maestra' });
    const btn = h('button', { class: 'btn primary', type: 'submit' }, icon('key'), 'Desbloquear');
    const form = h('form', { class: 'card col mb vault-unlock', onsubmit: async (e) => {
      e.preventDefault();
      if (!pass.value) { pass.focus(); return; }
      try {
        state = await busy(btn, () => api.post('/api/vault/unlock', { passphrase: pass.value }), 'Bóveda desbloqueada.');
        pass.value = '';
        await refresh();
      } catch { pass.select(); }
    } },
    h('div', { class: 'row' }, icon('lock', 18), h('h3', { class: 'grow' }, 'La bóveda está bloqueada')),
    h('p', { class: 'small muted' }, 'Los secretos siguen guardados y cifrados. Se bloquea sola cada vez que se reinicia el servidor. Al desbloquearla, el equipo podrá consultar los secretos permitidos para su rol.'),
    h('div', { class: 'row' }, pass, btn),
    h('div', { class: 'tiny muted' }, '¿No recuerdas la frase? ', NO_RECOVERY));
    setTimeout(() => pass.focus(), 30);
    return form;
  }

  // ---------- lista de secretos ----------
  function drawList() {
    if (tab !== 'secretos') return;
    if (!state.configured) { mount(body, setupCard()); return; }
    const q = filter.trim().toLowerCase();
    const shown = items.filter(i => !q || `${i.name} ${i.username} ${i.url}`.toLowerCase().includes(q));
    const searchIn = h('input', { type: 'search', placeholder: 'Buscar secretos…', value: filter });
    searchIn.addEventListener('input', () => { filter = searchIn.value; const pos = searchIn.selectionStart; drawList(); const n = body.querySelector('.vault-search input'); if (n) { n.focus(); n.setSelectionRange(pos, pos); } });
    const card = (it) => {
      const r = revealed.get(it.id);
      const proj = it.project_id ? projectById(it.project_id) : null;
      const left = r ? Math.max(0, Math.ceil((r.until - Date.now()) / 1000)) : 0;
      return h('div', { class: 'card vault-item' + (r ? ' open' : '') },
        h('div', { class: 'row' }, h('span', { class: 'vault-icon' }, icon('key', 16)),
          h('div', { class: 'grow' }, h('b', null, it.name),
            h('div', { class: 'tiny muted' }, proj ? `${proj.key} · ` : '', `Creado por ${it.created_by_name || '—'} · actualizado ${fmtRelative(it.updated_at)}`)),
          r ? h('button', { class: 'btn sm', onclick: () => hide(it.id) }, icon('eyeoff'), 'Ocultar')
            : h('button', { class: 'btn sm', disabled: !state.unlocked, title: state.unlocked ? 'Ver el secreto (queda registrado)' : 'La bóveda está bloqueada', onclick: (e) => reveal(e.currentTarget, it) }, icon('eye'), 'Revelar'),
          manage ? h('button', { class: 'btn ghost sm icon', title: 'Editar', onclick: () => editDialog(it) }, icon('edit')) : null,
          manage ? h('button', { class: 'btn ghost sm icon', title: 'Eliminar', onclick: (e) => remove(e.currentTarget, it) }, icon('trash')) : null),
        h('dl', { class: 'kv vault-kv' },
          it.username ? [h('dt', null, 'Usuario'), h('dd', { class: 'row' }, h('span', { class: 'mono ellipsis' }, it.username),
            h('button', { class: 'btn ghost sm icon', title: 'Copiar el usuario', onclick: async () => toast(await copyToClipboard(it.username) ? 'Usuario copiado.' : 'No se ha podido copiar.', 'ok') }, icon('copy')))] : null,
          it.url ? [h('dt', null, 'Dirección'), h('dd', null, /^https?:\/\//i.test(it.url) ? h('a', { href: it.url, target: '_blank', rel: 'noopener noreferrer', class: 'ellipsis' }, it.url) : it.url)] : null,
          h('dt', null, 'Acceso'), h('dd', { class: 'row wrap' }, it.allowed_roles.length ? it.allowed_roles.map(k => h('span', { class: 'badge neutral' }, roleName(k)))
            : h('span', { class: 'small muted' }, 'Solo quien gestiona la bóveda'))),
        r ? h('div', { class: 'vault-secret' },
          h('div', { class: 'row' }, h('div', { class: 'secret mono grow' }, r.secret),
            h('button', { class: 'btn primary', onclick: async () => {
              toast(await copyToClipboard(r.secret) ? 'Copiado al portapapeles. Pégalo donde lo necesites y no lo compartas por el chat.' : 'No se ha podido copiar: selecciónalo y usa Ctrl+C.', 'ok');
            } }, icon('copy'), 'Copiar')),
          r.notes ? h('div', { class: 'text-block small mt' }, r.notes) : null,
          h('div', { class: 'tiny muted mt', 'data-countdown': it.id }, `Se ocultará en ${left} s. La consulta ha quedado registrada.`)) : null);
    };
    mount(body,
      state.unlocked ? null : unlockCard(),
      items.length > 4 ? h('div', { class: 'toolbar' }, h('div', { class: 'search vault-search' }, icon('search'), searchIn)) : null,
      shown.length ? h('div', { class: 'vault-grid' }, shown.map(card))
        : emptyState(items.length ? 'Ningún secreto coincide' : 'No hay secretos', items.length ? '' : manage
          ? (state.unlocked ? 'Añade el primero con «Nuevo secreto».' : 'Desbloquea la bóveda para añadir secretos.')
          : 'No hay secretos compartidos con tu rol.'),
      h('div', { class: 'tiny muted mt' }, 'Cada vez que alguien revela un secreto queda registrado y se avisa a quien gestiona la bóveda.'));
  }

  async function reveal(btn, it) {
    const ok = await confirmDialog({ title: `Revelar «${it.name}»`, confirmLabel: 'Revelar',
      message: `Se mostrará el secreto durante ${REVEAL_SECONDS} segundos.\n\nLa consulta queda registrada con tu nombre y se avisará a quien gestiona la bóveda.` });
    if (!ok) return;
    try {
      const r = await busy(btn, () => api.post(`/api/vault/items/${it.id}/reveal`, {}));
      const entry = { secret: r.secret, notes: r.notes, until: Date.now() + REVEAL_SECONDS * 1000 };
      entry.timer = setInterval(() => {
        const left = Math.ceil((entry.until - Date.now()) / 1000);
        if (left <= 0 || !ctx.alive()) { hide(it.id); return; }
        const el = body.querySelector(`[data-countdown="${it.id}"]`);
        if (el) el.textContent = `Se ocultará en ${left} s. La consulta ha quedado registrada.`;
      }, 1000);
      revealed.set(it.id, entry);
      drawList();
    } catch (err) {
      if (err.status === 423) refresh();
    }
  }

  async function remove(btn, it) {
    const ok = await confirmDialog({ title: 'Eliminar secreto', danger: true, confirmLabel: 'Eliminar para siempre',
      message: `¿Eliminar «${it.name}»? No se puede deshacer: el secreto cifrado se borra del servidor.` });
    if (!ok) return;
    try { await busy(btn, () => api.del(`/api/vault/items/${it.id}`), 'Secreto eliminado.'); hide(it.id); await refresh(); } catch { /* aviso mostrado */ }
  }

  async function lock(btn) {
    const ok = await confirmDialog({ title: 'Bloquear la bóveda', confirmLabel: 'Bloquear',
      message: 'Nadie podrá revelar secretos hasta que alguien con permiso la desbloquee con la frase maestra. Úsalo si sospechas de un acceso indebido.' });
    if (!ok) return;
    try { state = await busy(btn, () => api.post('/api/vault/lock', {}), 'Bóveda bloqueada.'); for (const id of [...revealed.keys()]) hide(id); await refresh(); } catch { /* aviso mostrado */ }
  }

  function editDialog(it) {
    const isNew = !it;
    const name = h('input', { maxlength: 120, value: it?.name || '', placeholder: 'Por ejemplo: Consola de Google Play' });
    const username = h('input', { maxlength: 200, value: it?.username || '', autocomplete: 'off' });
    const url = h('input', { maxlength: 500, value: it?.url || '', placeholder: 'https://…', autocomplete: 'off' });
    const secret = h('input', { type: 'password', maxlength: 10000, autocomplete: 'new-password',
      placeholder: isNew ? '' : state.unlocked ? 'Déjalo vacío para no cambiarlo' : 'Desbloquea la bóveda para cambiarlo', disabled: !isNew && !state.unlocked });
    const showSecret = h('button', { class: 'btn icon', type: 'button', title: 'Mostrar u ocultar', onclick: () => { secret.type = secret.type === 'password' ? 'text' : 'password'; } }, icon('eye'));
    const notes = h('textarea', { rows: 3, maxlength: 10000, placeholder: isNew ? 'Instrucciones, códigos de recuperación…' : 'Déjalo vacío para mantener las notas actuales', disabled: !isNew && !state.unlocked });
    const clearNotes = h('input', { type: 'checkbox' });
    const proj = selectEl([['', 'Ninguno (todos los proyectos)'], ...activeProjects().map(p => [p.id, `${p.key} · ${p.name}`])], it?.project_id || '');
    const roleBoxes = roles.filter(([k]) => k !== 'admin').map(([k, label]) => {
      const cb = h('input', { type: 'checkbox', value: k, checked: (it?.allowed_roles || []).includes(k) });
      return { k, el: h('label', { class: 'check' }, cb, label), cb };
    });
    modal({
      title: isNew ? 'Nuevo secreto' : `Editar «${it.name}»`, wide: true,
      body: h('div', { class: 'form-grid' },
        field('Nombre', name, { required: true }), field('Proyecto', proj, { hint: isNew ? 'Limita quién lo ve a los miembros del proyecto.' : 'El proyecto no se puede cambiar después.' }),
        field('Usuario', username), field('Dirección (URL)', url),
        field(isNew ? 'Secreto (contraseña, clave…)' : 'Nuevo secreto', h('div', { class: 'row' }, secret, showSecret), { required: isNew, cls: 'span2' }),
        field('Notas (se guardan cifradas)', notes, { cls: 'span2' }),
        !isNew ? h('label', { class: 'check span2 small' }, clearNotes, 'Borrar las notas actuales') : null,
        h('div', { class: 'span2 col' }, h('span', { class: 'small' }, h('b', null, '¿Qué roles pueden verlo?')),
          h('div', { class: 'row wrap gap-lg' }, roleBoxes.map(r => r.el)),
          h('span', { class: 'tiny muted' }, 'Quien gestiona la bóveda ve siempre todos los secretos. Si no marcas ningún rol, solo lo verán ellos.'))),
      actions: [{ label: 'Cancelar' }, { label: isNew ? 'Guardar' : 'Guardar cambios', kind: 'primary', onClick: async () => {
        if (!name.value.trim()) { toast('El nombre es obligatorio.', 'error'); name.focus(); return false; }
        if (isNew && !secret.value) { toast('Escribe el secreto que quieres guardar.', 'error'); secret.focus(); return false; }
        const data = { name: name.value.trim(), username: username.value.trim(), url: url.value.trim(),
          allowed_roles: roleBoxes.filter(r => r.cb.checked).map(r => r.k) };
        if (secret.value) data.secret = secret.value;
        if (isNew) { data.notes = notes.value; data.project_id = proj.value ? Number(proj.value) : null; data.uid = uid(); }
        else if (notes.value.trim()) data.notes = notes.value;
        else if (clearNotes.checked) data.notes = '';
        try {
          if (isNew) await api.post('/api/vault/items', data);
          else await api.patch(`/api/vault/items/${it.id}`, data);
          toast(isNew ? 'Secreto guardado y cifrado.' : 'Secreto actualizado.', 'ok');
          if (!isNew) hide(it.id);
          await refresh();
        } catch (err) { toast(err.message, 'error'); if (err.status === 423) refresh(); return false; }
      } }],
    });
    if (!isNew) proj.disabled = true;
  }

  // ---------- registro ----------
  async function drawLog() {
    mount(body, loading());
    let d;
    try { d = await api.get('/api/vault/log'); } catch (err) { mount(body, errorBox(err, drawLog)); return; }
    if (tab !== 'registro') return;
    const cls = (a) => a === 'ver' ? 'info' : ['frase incorrecta', 'acceso denegado'].includes(a) ? 'danger' : a.startsWith('eliminar') ? 'warn' : 'neutral';
    mount(body, h('p', { class: 'small muted' }, 'Últimos 300 accesos y cambios en la bóveda.'),
      table([
        { label: 'Fecha', cls: 'nowrap', render: r => h('span', { title: fmtRelative(r.at) }, fmtDateTime(r.at)) },
        { label: 'Persona', render: r => r.user_name || '—' },
        { label: 'Acción', render: r => h('span', { class: `badge ${cls(r.action)}` }, ACTION_LABELS[r.action] || (r.action.startsWith('eliminar') ? `Eliminó ${r.action.slice(9)}` : r.action)) },
        { label: 'Secreto', render: r => r.item_name || (r.item_id ? h('span', { class: 'muted' }, '(eliminado)') : '—') },
      ], d.items, { empty: 'Todavía no hay accesos registrados.', rowClass: r => ['frase incorrecta', 'acceso denegado'].includes(r.action) ? 'is-blocked' : '' }));
  }

  async function refresh() {
    try { await load(); } catch (err) { mount(body, errorBox(err, refresh)); return; }
    if (!ctx.alive()) return;
    drawHead();
    draw();
  }

  mount(root, head, body);
  mount(body, loading());
  await refresh();
  ctx.onChanges(['vault'], () => { if (tab === 'secretos') refresh(); else load().then(drawHead).catch(() => {}); });
  // El estado de bloqueo no genera aviso de cambios: se comprueba de vez en cuando.
  const poll = setInterval(async () => {
    try {
      const s = await api.get('/api/vault');
      if (state && (s.unlocked !== state.unlocked || s.configured !== state.configured)) refresh();
    } catch { /* sin conexión */ }
  }, 20000);
  return () => { clearInterval(poll); for (const r of revealed.values()) clearInterval(r.timer); revealed.clear(); clear(body); };
}

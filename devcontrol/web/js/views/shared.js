// Componentes reutilizables de las fichas: comentarios, adjuntos, relaciones, tiempo,
// selectores de usuario y proyecto, verificación e historial de resoluciones.
//
// Todas las secciones devuelven un HTMLElement con el método el.refresh(datos?):
//  - sin argumentos vuelven a pedir los datos al servidor;
//  - con el objeto de la ficha (lo que devuelve GET /api/incidents/:id, /api/tasks/:id…) lo reutilizan.
// Opciones extra comunes (opcionales): data (ficha ya cargada, evita una petición), live (true por defecto:
// se recargan solas cuando el servidor avisa de cambios en ese elemento) y title (null para ocultar la cabecera).
import { api, on } from '../api.js';
import { INCIDENT_STATUSES, RESOLUTION_STATES, TASK_STATUSES } from '../labels.js';
import { can, canAny, entityHash, isWatched, store, toggleWatch, userById, userName } from '../state.js';
import {
  avatar, busy, clearDraft, bindDraft, confirmDialog, debounce, emptyState, field, fmtDate, fmtDateTime, fmtDuration,
  fmtRelative, fmtSize, h, icon, lightbox, mount, modal, promptDialog, richText, statusBadge, toast, todayISO, typeBadge, uid,
} from '../ui.js';

// ---------- seguir un elemento (avisos de todos sus cambios) ----------
// entityType: task | incident | idea | wiki | decision | project
export function followButton(entityType, entityId, { label = 'Seguir', compact = false } = {}) {
  const btn = h('button', { class: 'btn sm ghost follow-btn', type: 'button' });
  let state = { following: false, count: 0 };
  const paint = () => {
    btn.classList.toggle('on', state.following);
    btn.title = state.following
      ? 'Recibes un aviso de cada cambio. Pulsa para dejar de seguir.'
      : 'Recibe un aviso de cada cambio (estado, responsable, comentarios…).';
    mount(btn, icon(state.following ? 'bell' : 'eye', 14),
      compact ? null : (state.following ? 'Siguiendo' : label),
      state.count ? h('span', { class: 'follow-count' }, String(state.count)) : null);
  };
  btn.onclick = () => busy(btn, async () => {
    state = await api.post('/api/follows', { entity_type: entityType, entity_id: entityId, follow: !state.following });
    paint();
    toast(state.following ? 'Te avisaremos de cada cambio.' : 'Ya no sigues este elemento.', 'ok');
  }).catch(() => {});
  paint();
  api.get(`/api/follows?entity_type=${entityType}&entity_id=${entityId}`).then((s) => { state = s; paint(); }).catch(() => {});
  return btn;
}

// ---------- avisarme cuando alguien se conecte o se desconecte ----------
export function watchUserButton(uid, { compact = false } = {}) {
  const btn = h('button', { class: 'btn sm ghost follow-btn', type: 'button' });
  const paint = () => {
    const onIt = isWatched(uid);
    btn.classList.toggle('on', onIt);
    btn.title = onIt ? 'Te avisamos cuando se conecte o se desconecte y lo ves arriba, junto a «Conectado». Pulsa para dejarlo.'
      : 'Avisarme cuando se conecte o se desconecte (y verlo arriba, junto a «Conectado»)';
    mount(btn, icon(onIt ? 'bell' : 'eye', 14), compact ? null : (onIt ? 'Te avisamos' : 'Avisarme al conectarse'));
  };
  btn.onclick = () => busy(btn, () => toggleWatch(uid)).then((follow) => {
    paint();
    toast(follow ? `Te avisaremos cuando ${userName(uid)} se conecte o se desconecte.` : `Ya no sigues a ${userName(uid)}.`, 'ok');
  }).catch(() => {});
  paint();
  return btn;
}

// ---------- acceso a los datos de una ficha ----------
const DETAIL_PATHS = { incident: 'incidents', task: 'tasks', decision: 'decisions', wiki: 'wiki' };
export function entityPath(type, id) {
  const base = DETAIL_PATHS[type];
  if (!base) throw new Error(`Tipo de elemento no soportado: ${type}`);
  return `/api/${base}/${encodeURIComponent(id)}`;
}

// Varias secciones de la misma ficha comparten la misma petición si llegan a la vez.
const inflight = new Map();
export function loadEntity(type, id) {
  const path = entityPath(type, id);
  const hit = inflight.get(path);
  if (hit && Date.now() - hit.at < 600) return hit.promise;
  const promise = api.get(path).then(r => r.item);
  inflight.set(path, { at: Date.now(), promise });
  promise.catch(() => inflight.delete(path));
  setTimeout(() => { if (inflight.get(path)?.promise === promise) inflight.delete(path); }, 600);
  return promise;
}

// Ejecuta fn cuando el servidor avisa de cambios del elemento (y deja de escuchar cuando la sección
// desaparece de la página).
function watch(el, type, getId, fn, kinds = [type]) {
  const handler = debounce(fn, 300);
  let seen = false;
  const offs = [];
  const alive = () => {
    if (el.isConnected) { seen = true; return true; }
    if (seen) offs.forEach(o => o());
    return false;
  };
  offs.push(on('changes', (d) => {
    if (!alive()) return;
    const id = getId();
    if (d.changes.some(c => kinds.includes(c.kind) && (c.entity_type === type || c.kind === type) && (id == null || c.entity_id === id))) handler();
  }));
  offs.push(on('reconnected', () => { if (alive()) handler(); }));
}

function sectionShell(title, count, ...extra) {
  if (title === null) return null;
  return h('div', { class: 'ws-head' }, h('div', { class: 'section-title' }, title, count ? h('span', { class: 'count' }, `(${count})`) : null), ...extra);
}

const KIND_LABELS = { closing: 'Comentario de cierre', reopen: 'Motivo de la reapertura', rejection: 'Rechazo en la verificación', duplicate: 'Duplicada' };

// ---------- @menciones con autocompletado ----------
export function mentionInput(textarea) {
  const box = h('div', { class: 'mention-box hidden' });
  const wrap = h('div', { class: 'mention-wrap' }, textarea, box);
  let matches = [], sel = 0, start = -1;
  const hide = () => { box.classList.add('hidden'); matches = []; };
  const pick = (u) => {
    const pos = textarea.selectionStart;
    const v = textarea.value;
    textarea.value = `${v.slice(0, start)}@${u.username} ${v.slice(pos)}`;
    const caret = start + u.username.length + 2;
    textarea.setSelectionRange(caret, caret);
    hide();
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.focus();
  };
  const draw = () => {
    mount(box, matches.map((u, i) => h('button', { type: 'button', class: i === sel ? 'on' : '', onmousedown: (e) => { e.preventDefault(); pick(u); } },
      avatar(u), h('span', { class: 'grow ellipsis' }, u.display_name), h('span', { class: 'tiny muted' }, `@${u.username}`))));
    box.classList.toggle('hidden', !matches.length);
  };
  textarea.addEventListener('input', () => {
    const pos = textarea.selectionStart;
    const m = /(^|[\s(])@([A-Za-z0-9_.-]{0,40})$/.exec(textarea.value.slice(0, pos));
    if (!m) return hide();
    const q = m[2].toLowerCase();
    start = pos - m[2].length - 1;
    matches = store.users.filter(u => u.username.toLowerCase().startsWith(q) || u.display_name.toLowerCase().includes(q)).slice(0, 6);
    sel = 0;
    draw();
  });
  textarea.addEventListener('keydown', (e) => {
    if (!matches.length) return;
    if (e.key === 'ArrowDown') { sel = (sel + 1) % matches.length; draw(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { sel = (sel - 1 + matches.length) % matches.length; draw(); e.preventDefault(); }
    else if (e.key === 'Enter' || e.key === 'Tab') { pick(matches[sel]); e.preventDefault(); e.stopPropagation(); }
    else if (e.key === 'Escape') { hide(); e.stopPropagation(); }
  });
  textarea.addEventListener('blur', () => setTimeout(hide, 150));
  return wrap;
}

// ---------- comentarios ----------
export function commentsSection(entityType, entityId, { canComment = true, data = null, live = true, title = 'Comentarios' } = {}) {
  const el = h('div', { class: 'ws-section comments-section' });
  const head = h('div');
  const list = h('div', { class: 'comment-list' });
  const allowed = canComment && (['incident', 'task'].includes(entityType) ? can('incidencias.comentar') : can('conocimiento.editar'));
  let resolvedId = typeof entityId === 'number' ? entityId : null;
  let comments = [];
  let editing = 0;        // comentarios en edición: no se redibuja la lista para no perder el texto
  let pending = false;
  let opUid = uid();

  const draftName = `comment.${entityType}.${entityId}`;
  const input = h('textarea', { rows: 3, placeholder: 'Escribe un comentario… Usa @usuario para avisar a alguien y APP-012 / T-004 para enlazar.' });
  const sendBtn = h('button', { class: 'btn primary sm', type: 'button' }, icon('reply'), 'Comentar');
  const send = async () => {
    const body = input.value.trim();
    if (!body) { input.focus(); return; }
    try {
      const r = await busy(sendBtn, () => api.post('/api/comments', { entity_type: entityType, entity: entityId, uid: opUid, body }, { queue: true }));
      opUid = uid();
      input.value = '';
      clearDraft(draftName);
      if (!r._queued) await refresh();
    } catch { /* aviso ya mostrado */ }
  };
  sendBtn.addEventListener('click', send);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } });
  const composer = allowed ? h('div', { class: 'comment-box' }, mentionInput(bindDraft(input, draftName)),
    h('div', { class: 'row' }, h('span', { class: 'tiny muted' }, 'Ctrl + Intro para enviar'), sendBtn)) : null;

  function renderComment(cm) {
    const u = userById(cm.user_id);
    const bodyEl = h('div', { class: 'body' }, richText(cm.body));
    const mine = cm.user_id === store.me?.id && cm.kind === 'comment';
    const prev = h('div', { class: 'hidden' });
    const edited = cm.edited_at ? h('span', { class: 'tiny edited', title: `Editado ${fmtDateTime(cm.edited_at)}`, onclick: () => {
      if (!prev.classList.toggle('hidden')) mount(prev, (cm.edits || []).map(e => h('div', { class: 'prev' }, h('div', { class: 'tiny' }, `Antes de ${fmtDateTime(e.edited_at)}:`), e.previous_body)));
    } }, '(editado)') : null;
    const card = h('div', { class: `comment ${cm.kind !== 'comment' ? cm.kind : ''}` });
    const editBtn = mine && allowed ? h('button', { class: 'btn ghost sm icon', title: 'Editar mi comentario', onclick: () => startEdit() }, icon('edit')) : null;
    function startEdit() {
      editing++;
      const ta = h('textarea', { rows: 3 });
      ta.value = cm.body;
      const save = h('button', { class: 'btn primary sm' }, 'Guardar');
      const stop = () => { editing = Math.max(0, editing - 1); if (pending && !editing) { pending = false; refresh(); } };
      const cancel = h('button', { class: 'btn sm', onclick: () => { mount(bodyWrap, bodyEl); stop(); } }, 'Cancelar');
      save.addEventListener('click', async () => {
        const body = ta.value.trim();
        if (!body) { toast('El comentario no puede quedar vacío.', 'error'); return; }
        try {
          await busy(save, () => api.patch(`/api/comments/${cm.id}`, { body }), 'Comentario actualizado.');
          cm.body = body; cm.edited_at = new Date().toISOString();
          stop();
          refresh();
        } catch { /* aviso ya mostrado */ }
      });
      ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) save.click(); if (e.key === 'Escape') cancel.click(); });
      mount(bodyWrap, h('div', { class: 'col' }, mentionInput(ta), h('div', { class: 'row end' }, cancel, save)));
      ta.focus();
    }
    const bodyWrap = h('div', null, bodyEl);
    mount(card,
      h('div', { class: 'meta' }, avatar(u || { display_name: cm.user_name }), h('b', null, cm.user_name || userName(cm.user_id)),
        h('span', { title: fmtDateTime(cm.created_at) }, fmtRelative(cm.created_at)),
        KIND_LABELS[cm.kind] ? h('span', { class: `badge kind ${cm.kind === 'closing' ? 'ok' : cm.kind === 'duplicate' ? 'neutral' : 'warn'}` }, KIND_LABELS[cm.kind]) : null,
        edited, h('span', { class: 'tools' }, editBtn)),
      bodyWrap, prev);
    return card;
  }

  function draw() {
    mount(head, sectionShell(title, comments.length));
    mount(list, comments.length ? comments.map(renderComment)
      : h('div', { class: 'ws-empty' }, allowed ? 'Todavía no hay comentarios. Sé el primero en aportar algo.' : 'Todavía no hay comentarios.'));
  }

  async function refresh(item) {
    if (editing) { pending = true; return; }
    try {
      const d = item || await loadEntity(entityType, entityId);
      resolvedId = d.id;
      comments = d.comments || [];
      draw();
    } catch (err) {
      mount(list, h('div', { class: 'ws-empty danger-text' }, `No se han podido cargar los comentarios: ${err.message}`));
    }
  }

  mount(el, head, list, composer);
  el.refresh = refresh;
  el.focusInput = () => input.focus();
  if (data) refresh(data); else { mount(list, h('div', { class: 'ws-empty' }, 'Cargando comentarios…')); refresh(); }
  if (live) watch(el, entityType, () => resolvedId, () => refresh());
  return el;
}

// ---------- adjuntos ----------
function canAttach(ownerType) {
  if (ownerType === 'incident') return can('incidencias.adjuntar');
  if (ownerType === 'task') return can('tareas.ver') && canAny('tareas.trabajar', 'tareas.crear', 'tareas.editar');
  if (ownerType === 'wiki') return can('conocimiento.editar');
  if (ownerType === 'message') return can('chat.usar');
  return false;
}
const extOf = (name) => (name.includes('.') ? name.split('.').pop() : 'archivo').slice(0, 5);

function attachmentCard(a) {
  const isImg = a.content_type?.startsWith('image/');
  const isVideo = a.content_type?.startsWith('video/');
  const path = `/api/attachments/${a.id}`;
  const thumb = h('div', { class: 'thumb' + (isVideo ? ' video' : ''), title: isImg || isVideo ? 'Ver' : 'Abrir' }, h('span', { class: 'ext' }, extOf(a.filename)));
  if (isImg) {
    api.blobUrl(`${path}?inline=1`).then(url => mount(thumb, h('img', { src: url, alt: a.filename, loading: 'lazy' }))).catch(() => {});
  }
  const download = () => api.save(path, { folder: 'descargas' }).then(r => toast(`Guardado en Descargas: ${r.name || a.filename}`, 'ok')).catch(err => toast(err.message, 'error'));
  thumb.addEventListener('click', async () => {
    if (isImg || isVideo) {
      try { lightbox(await api.blobUrl(`${path}?inline=1`), a.content_type); } catch (err) { toast(err.message, 'error'); }
    } else download();
  });
  return h('div', { class: 'att' }, thumb,
    h('div', { class: 'info' }, h('div', { class: 'name', title: a.filename }, a.filename),
      h('div', { class: 'row' }, h('span', { class: 'tiny', title: `${a.uploaded_by_name || ''} · ${fmtDateTime(a.created_at)}` }, `${fmtSize(a.size)} · ${fmtRelative(a.created_at)}`),
        h('button', { class: 'dl', title: 'Descargar', onclick: download }, icon('download')))));
}

// Sube archivos mostrando una fila con progreso por archivo. Devuelve los resultados.
export async function uploadFiles(files, target, listEl) {
  const results = [];
  for (const file of files) {
    const bar = h('i');
    const st = h('span', { class: 'st' }, 'Subiendo…');
    const row = h('div', { class: 'upload-row' }, h('span', { class: 'ellipsis', title: file.name }, `${file.name} (${fmtSize(file.size)})`),
      h('div', { class: 'progress' }, bar), st);
    listEl?.appendChild(row);
    try {
      const info = store.local?.server_info || {};
      if (info.max_upload_mb && file.size > info.max_upload_mb * 1024 * 1024) {
        throw new Error(`supera el máximo de ${info.max_upload_mb} MB por archivo${info.via_internet ? ' cuando se entra por Internet' : ''}.`);
      }
      const r = await api.upload(file, { ...target, uid: uid() }, (p) => { bar.style.width = `${Math.round(p * 100)}%`; });
      bar.style.width = '100%';
      row.classList.add('done');
      st.textContent = r._queued ? 'En cola (sin conexión)' : 'Subido';
      results.push(r);
      setTimeout(() => row.remove(), r._queued ? 6000 : 1500);
    } catch (err) {
      row.classList.add('error');
      st.textContent = 'Error';
      row.title = err.message;
      row.appendChild(h('button', { class: 'btn ghost sm icon', title: 'Quitar', onclick: () => row.remove() }, icon('x')));
      toast(`${file.name}: ${err.message}`, 'error');
    }
  }
  return results;
}

export function attachmentsSection(ownerType, ownerId, { canUpload = true, data = null, live = true, title = 'Adjuntos' } = {}) {
  const el = h('div', { class: 'ws-section attachments-section' });
  const head = h('div');
  const grid = h('div');
  const uploads = h('div', { class: 'upload-list' });
  const allowed = canUpload && canAttach(ownerType);
  let resolvedId = typeof ownerId === 'number' ? ownerId : null;
  let items = [];
  let key = null;

  const fileInput = h('input', { type: 'file', multiple: true, class: 'hidden' });
  const doUpload = async (files) => {
    if (!files.length) return;
    const res = await uploadFiles(files, { owner_type: ownerType, owner_id: resolvedId || ownerId }, uploads);
    if (res.some(r => !r._queued)) refresh();
  };
  fileInput.addEventListener('change', () => { doUpload([...fileInput.files]); fileInput.value = ''; });
  const drop = allowed ? h('div', { class: 'dropzone', tabindex: '0', role: 'button', title: 'Arrastra archivos, pégalos (Ctrl+V) o haz clic',
    onclick: () => fileInput.click(),
    onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } },
    ondragover: (e) => { e.preventDefault(); drop.classList.add('over'); },
    ondragleave: () => drop.classList.remove('over'),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove('over'); doUpload([...(e.dataTransfer?.files || [])]); },
    onpaste: (e) => { const f = [...(e.clipboardData?.files || [])]; if (f.length) { e.preventDefault(); doUpload(f); } },
  }, h('span', { class: 'dz-icon' }, icon('upload')), 'Arrastra aquí capturas, vídeos o archivos, pégalos con Ctrl+V o haz clic para elegirlos') : null;

  function draw() {
    const zip = items.length > 1 && ['incident', 'task'].includes(ownerType) && key
      ? h('button', { class: 'btn ghost sm', title: 'Descargar todos en un ZIP', onclick: (e) => busy(e.currentTarget, () =>
        api.save(`/api/${ownerType === 'incident' ? 'incidents' : 'tasks'}/${encodeURIComponent(key)}/attachments.zip`, { folder: 'descargas' })
          .then(r => toast(`Guardado en Descargas: ${r.name}`, 'ok'))).catch(() => {}) }, icon('download'), 'Todo (ZIP)') : null;
    mount(head, sectionShell(title, items.length, zip));
    mount(grid, items.length ? h('div', { class: 'att-grid' }, items.map(attachmentCard))
      : h('div', { class: 'ws-empty' }, allowed ? 'Sin archivos adjuntos.' : 'Sin archivos adjuntos.'));
  }

  async function refresh(item) {
    try {
      const d = item || await loadEntity(ownerType, ownerId);
      resolvedId = d.id;
      key = d.key || d.id;
      items = d.attachments || [];
      draw();
    } catch (err) {
      mount(grid, h('div', { class: 'ws-empty danger-text' }, `No se han podido cargar los adjuntos: ${err.message}`));
    }
  }

  mount(el, head, grid, drop, uploads, fileInput);
  el.refresh = refresh;
  el.upload = doUpload;
  if (data) refresh(data); else refresh();
  if (live) watch(el, ownerType, () => resolvedId, () => refresh());
  return el;
}

// ---------- selector de elementos (búsqueda) ----------
// Devuelve una promesa con el elemento elegido ({type, id, key, title}) o null.
export function entityPicker({ title = 'Buscar elemento', types = ['incident', 'task'], exclude = [], hint = '', extra = null } = {}) {
  return new Promise((resolve) => {
    let result = null;
    const input = h('input', { placeholder: 'Escribe una clave (APP-012, T-004) o palabras del título…', autocomplete: 'off' });
    const list = h('div', { class: 'picker-list' });
    let items = [], sel = 0;
    const isExcluded = (it) => exclude.some(x => x.type === it.type && (x.id === it.id || (x.key && x.key === it.key)));
    const draw = () => {
      if (!input.value.trim()) { mount(list, h('div', { class: 'ws-empty' }, 'Escribe para buscar.')); return; }
      if (!items.length) { mount(list, h('div', { class: 'ws-empty' }, 'Sin resultados.')); return; }
      mount(list, items.map((it, i) => h('button', { type: 'button', class: 'picker-item' + (i === sel ? ' on' : ''), onclick: () => { result = it; m.close(); } },
        typeBadge(it.type), it.key ? h('span', { class: 'key' }, it.key) : null, h('span', { class: 'grow ellipsis' }, it.title),
        it.status ? statusBadge(it.status, (it.type === 'task' ? TASK_STATUSES : INCIDENT_STATUSES)[it.status]) : null)));
    };
    const search = debounce(async () => {
      const q = input.value.trim();
      if (!q) { items = []; draw(); return; }
      try {
        const r = await api.get(`/api/search?q=${encodeURIComponent(q)}&limit=25`);
        items = r.items.filter(it => types.includes(it.type) && !isExcluded(it));
        sel = 0; draw();
      } catch (err) { mount(list, h('div', { class: 'ws-empty danger-text' }, err.message)); }
    }, 200);
    input.addEventListener('input', search);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { sel = Math.min(items.length - 1, sel + 1); draw(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { sel = Math.max(0, sel - 1); draw(); e.preventDefault(); }
      else if (e.key === 'Enter' && items[sel]) { result = items[sel]; m.close(); }
    });
    draw();
    const m = modal({ title, body: h('div', { class: 'col' }, extra, hint ? h('div', { class: 'small muted' }, hint) : null, input, list),
      actions: [{ label: 'Cancelar' }], onClose: () => resolve(result) });
    setTimeout(() => input.focus(), 40);
  });
}

// ---------- dependencias y enlaces ----------
const DEP_TYPES = ['incident', 'task'];
const LINK_TYPES = ['incident', 'task', 'decision', 'idea', 'wiki'];
const LINK_KINDS = { relacionado: 'Relacionado', origen: 'Origen', articulo: 'Artículo de la wiki' };

function relHref(it) {
  if (it.type === 'message') return `#/chat?m=${it.id}`;
  return entityHash(it.type, it.id, it.key);
}

export function relationsSection(entityType, entityId, { canEdit = true, data = null, live = true, title = 'Dependencias y enlaces' } = {}) {
  const el = h('div', { class: 'ws-section relations-section' });
  const head = h('div');
  const body = h('div', { class: 'col' });
  const depsAllowed = DEP_TYPES.includes(entityType);
  const canDeps = canEdit && depsAllowed && canAny('tareas.trabajar', 'tareas.editar', 'incidencias.trabajar');
  const canLinks = canEdit;
  let d = null;
  let resolvedId = typeof entityId === 'number' ? entityId : null;

  const removeDep = async (btn, it) => {
    if (!await confirmDialog({ title: 'Quitar dependencia', message: `¿Quitar la dependencia con ${it.key || it.title}?`, confirmLabel: 'Quitar', danger: true })) return;
    try { await busy(btn, () => api.del(`/api/dependencies/${it.dependency_id}`), 'Dependencia quitada.'); refresh(); } catch { /* aviso */ }
  };
  const removeLink = async (btn, it) => {
    if (!await confirmDialog({ title: 'Quitar enlace', message: `¿Quitar el enlace con ${it.key || it.title}?`, confirmLabel: 'Quitar', danger: true })) return;
    try { await busy(btn, () => api.del(`/api/links/${it.link_id}`), 'Enlace quitado.'); refresh(); } catch { /* aviso */ }
  };

  const item = (it, onRemove, extra) => {
    const x = onRemove ? h('button', { class: 'x', title: 'Quitar', onclick: (e) => onRemove(e.currentTarget, it) }, '×') : null;
    return h('div', { class: 'rel-item' + (it.closed ? ' closed' : '') }, typeBadge(it.type),
      it.key ? h('a', { class: 'key', href: relHref(it) }, it.key) : null,
      h('a', { class: 'grow ellipsis', href: relHref(it), title: it.title, style: { color: 'inherit' } }, it.title || ''),
      extra, it.status ? statusBadge(it.status, it.status_label) : null, x);
  };

  async function add() {
    const mode = { v: depsAllowed && canDeps ? 'blocked_by' : 'link' };
    const segBtns = [];
    const opts = [
      depsAllowed && canDeps && ['blocked_by', 'Está bloqueado por…'],
      depsAllowed && canDeps && ['blocks', 'Bloquea a…'],
      canLinks && ['link', 'Relacionado con…'],
    ].filter(Boolean);
    const seg = h('div', { class: 'seg' }, opts.map(([v, label]) => {
      const b = h('button', { type: 'button', class: v === mode.v ? 'on' : '', onclick: () => { mode.v = v; segBtns.forEach(x => x.classList.toggle('on', x === b)); } }, label);
      segBtns.push(b);
      return b;
    }));
    const self = { type: entityType, id: resolvedId, key: d?.key };
    const target = await entityPicker({
      title: 'Añadir dependencia o enlace', types: LINK_TYPES, exclude: [self], extra: seg,
      hint: 'Las dependencias solo pueden ser entre incidencias y tareas. Mientras lo que bloquea siga abierto, este elemento se considera en espera.',
    });
    if (!target) return;
    try {
      if (mode.v === 'link') {
        await busy(null, () => api.post('/api/links', { from_type: entityType, from: resolvedId ?? entityId, to_type: target.type, to: target.id, kind: 'relacionado' }), 'Enlace añadido.');
      } else {
        if (!DEP_TYPES.includes(target.type)) { toast('Las dependencias solo pueden ser entre incidencias y tareas. Usa «Relacionado con…».', 'error'); return; }
        const me = { type: entityType, ref: String(resolvedId ?? entityId) };
        const other = { type: target.type, ref: String(target.id) };
        const [blocker, blocked] = mode.v === 'blocked_by' ? [other, me] : [me, other];
        const r = await busy(null, () => api.post('/api/dependencies', { blocker_type: blocker.type, blocker: blocker.ref, blocked_type: blocked.type, blocked: blocked.ref }));
        toast(r.duplicate ? 'Esa dependencia ya existía.' : 'Dependencia añadida.', r.duplicate ? 'info' : 'ok');
      }
      refresh();
    } catch { /* aviso */ }
  }

  function draw() {
    const blockedBy = d.blocked_by || [], blocks = d.blocks || [], links = d.links || [];
    const total = blockedBy.length + blocks.length + links.length;
    const addBtn = (canDeps || canLinks) ? h('button', { class: 'btn ghost sm', onclick: add }, icon('plus'), 'Añadir') : null;
    mount(head, sectionShell(title, total, addBtn));
    const open = blockedBy.filter(x => !x.closed);
    const groups = [];
    if (open.length) groups.push(h('div', { class: 'callout warn small' }, h('b', null, 'En espera: '), `depende de ${open.map(x => x.key || x.title).join(', ')}, que aún no ${open.length > 1 ? 'están cerradas' : 'está cerrada'}.`));
    if (blockedBy.length) groups.push(h('div', { class: 'rel-group' }, h('div', { class: 'lbl' }, 'Bloqueado por'), blockedBy.map(it => item(it, canDeps ? removeDep : null))));
    if (blocks.length) groups.push(h('div', { class: 'rel-group' }, h('div', { class: 'lbl' }, 'Bloquea a'), blocks.map(it => item(it, canDeps ? removeDep : null))));
    if (links.length) groups.push(h('div', { class: 'rel-group' }, h('div', { class: 'lbl' }, 'Enlaces'), links.map(it => item(it, canLinks ? removeLink : null,
      it.kind && it.kind !== 'relacionado' ? h('span', { class: 'badge neutral' }, LINK_KINDS[it.kind] || it.kind) : null))));
    mount(body, groups.length ? groups : h('div', { class: 'ws-empty' }, 'No depende de nada ni tiene enlaces.'));
  }

  async function refresh(itemData) {
    try {
      d = itemData || await loadEntity(entityType, entityId);
      resolvedId = d.id;
      draw();
    } catch (err) {
      mount(body, h('div', { class: 'ws-empty danger-text' }, `No se han podido cargar las relaciones: ${err.message}`));
    }
  }

  mount(el, head, body);
  el.refresh = refresh;
  if (data) refresh(data); else refresh();
  if (live) watch(el, entityType, () => resolvedId, () => refresh());
  return el;
}

// ---------- tiempo ----------
const clock = (secs) => {
  const hh = Math.floor(secs / 3600), mm = Math.floor((secs % 3600) / 60), ss = Math.floor(secs % 60);
  return `${hh}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
};

export function timeWidget(entityType, entityId, { data = null, live = true, title = 'Tiempo' } = {}) {
  const el = h('div', { class: 'ws-section time-widget' });
  const head = h('div');
  const body = h('div', { class: 'col' });
  const allowed = canAny('tareas.trabajar', 'incidencias.trabajar');
  let resolvedId = typeof entityId === 'number' ? entityId : null;
  let time = { total_seconds: 0, by_user: [], running: [] };
  let mine = null, other = null, key = null;
  let ticker = null;

  const start = async (btn, stopRunning = false) => {
    try {
      await busy(btn, () => api.post('/api/time/start', { entity_type: entityType, entity: resolvedId ?? entityId, stop_running: stopRunning }), 'Cronómetro en marcha.');
      refresh();
    } catch (err) {
      if (err.code === 'cronometro_activo') {
        const run = err.data?.running;
        if (await confirmDialog({ title: 'Ya tienes un cronómetro en marcha', message: `Está corriendo en ${run?.key || 'otro elemento'}${run?.title ? ` (${run.title})` : ''}. ¿Lo paro y empiezo a contar aquí?`, confirmLabel: 'Parar y empezar aquí' })) start(btn, true);
      }
    }
  };
  const stop = async (btn) => {
    try {
      const r = await busy(btn, () => api.post('/api/time/stop', {}));
      if (r.stopped) toast(`Registrado: ${fmtDuration(r.seconds)}.`, 'ok');
      refresh();
    } catch { /* aviso */ }
  };
  const manual = () => {
    const date = h('input', { type: 'date', value: todayISO(), max: todayISO() });
    const hours = h('input', { type: 'number', min: 0, max: 24, step: 1, value: 0 });
    const mins = h('input', { type: 'number', min: 0, max: 59, step: 5, value: 30 });
    const note = h('input', { placeholder: 'Opcional: qué has hecho' });
    modal({
      title: `Añadir tiempo${key ? ` a ${key}` : ''}`,
      body: h('div', { class: 'form-grid' }, field('Día', date, { required: true }), h('div'),
        field('Horas', hours), field('Minutos', mins), field('Nota', note, { cls: 'span2' })),
      actions: [{ label: 'Cancelar' }, { label: 'Añadir', kind: 'primary', onClick: async () => {
        const minutes = (Number(hours.value) || 0) * 60 + (Number(mins.value) || 0);
        if (minutes < 1) { toast('Indica cuánto tiempo has dedicado.', 'error'); return false; }
        if (minutes > 24 * 60) { toast('No se pueden registrar más de 24 horas en un día.', 'error'); return false; }
        try {
          await busy(null, () => api.post('/api/time', { entity_type: entityType, entity: resolvedId ?? entityId, date: date.value, minutes, note: note.value.trim() }), 'Tiempo añadido.');
          refresh();
        } catch { return false; }
      } }],
    });
  };

  function draw() {
    clearInterval(ticker);
    const runningOthers = (time.running || []).filter(r => r.user_id !== store.me?.id);
    const elapsed = mine ? Math.max(0, (Date.now() - new Date(mine.started_at).getTime()) / 1000) : 0;
    const runEl = mine ? h('span', { class: 'time-run' }, clock(elapsed)) : null;
    const btn = h('button', { class: `btn sm ${mine ? 'danger' : 'primary'}` }, icon(mine ? 'stop' : 'play'), mine ? 'Parar' : 'Iniciar cronómetro');
    btn.addEventListener('click', () => (mine ? stop(btn) : start(btn)));
    mount(head, sectionShell(title, 0));
    mount(body,
      h('div', { class: 'row between' }, h('div', null, h('div', { class: 'time-total' }, fmtDuration(Math.round((time.total_seconds || 0) + elapsed))),
        h('div', { class: 'tiny muted' }, 'Tiempo registrado en total')), runEl),
      allowed ? h('div', { class: 'row wrap' }, btn, h('button', { class: 'btn sm ghost', onclick: manual }, icon('plus'), 'Añadir a mano')) : null,
      other && allowed && !mine ? h('div', { class: 'tiny muted' }, `Tu cronómetro está en marcha en ${other.key || 'otro elemento'}.`) : null,
      runningOthers.length ? h('div', { class: 'tiny ok-text' }, `${runningOthers.map(r => r.user_name).join(', ')} ${runningOthers.length > 1 ? 'están' : 'está'} trabajando en esto ahora.`) : null,
      (time.by_user || []).length ? h('div', { class: 'time-users' }, time.by_user.filter(u => u.seconds).map(u => h('div', { class: 'row' },
        h('span', null, u.user_name || userName(u.user_id)), h('span', { class: 'muted' }, fmtDuration(u.seconds))))) : null);
    if (mine) {
      ticker = setInterval(() => {
        if (!el.isConnected) { clearInterval(ticker); return; }
        runEl.textContent = clock(Math.max(0, (Date.now() - new Date(mine.started_at).getTime()) / 1000));
      }, 1000);
    }
  }

  async function refresh(itemData) {
    try {
      const [d, cur] = await Promise.all([itemData || loadEntity(entityType, entityId), api.get('/api/time/current').catch(() => ({ entry: null }))]);
      resolvedId = d.id;
      key = d.key || null;
      time = d.time || time;
      const e = cur.entry;
      mine = e && e.entity_type === entityType && e.entity_id === d.id ? e : null;
      other = e && !mine ? e : null;
      draw();
    } catch (err) {
      mount(body, h('div', { class: 'ws-empty danger-text' }, `No se ha podido cargar el tiempo: ${err.message}`));
    }
  }

  mount(el, head, body);
  el.refresh = refresh;
  if (data) refresh(data); else refresh();
  if (live) watch(el, entityType, () => resolvedId, () => refresh(), ['time', entityType]);
  else watch(el, entityType, () => resolvedId, () => refresh(), ['time']);
  return el;
}

// ---------- selectores ----------
// <select> de usuarios activos. Con projectId solo muestra los miembros del proyecto (y quien ve todos).
export function userSelect(value, { allowEmpty = true, emptyLabel = 'Sin asignar', projectId } = {}) {
  const sel = h('select');
  const fill = (pid) => {
    const current = sel.value || (value ?? '');
    sel.replaceChildren();
    if (allowEmpty) sel.appendChild(h('option', { value: '' }, emptyLabel));
    const users = [...store.users].filter(u => !pid || pid === 'all' || u.all_projects || (u.project_ids || []).includes(Number(pid)) || String(u.id) === String(current))
      .sort((a, b) => (b.id === store.me?.id) - (a.id === store.me?.id) || a.display_name.localeCompare(b.display_name, 'es'));
    for (const u of users) sel.appendChild(h('option', { value: u.id }, u.id === store.me?.id ? `${u.display_name} (yo)` : u.display_name));
    if (current && !users.some(u => String(u.id) === String(current))) sel.appendChild(h('option', { value: current }, userName(Number(current))));
    sel.value = current ? String(current) : '';
  };
  fill(projectId);
  sel.setProject = (pid) => fill(pid);
  return sel;
}

// <select> de proyectos visibles. Con includeAll añade «Todos los proyectos» (valor 'all').
export function projectSelect(value, { includeAll = false, onlyActive = true } = {}) {
  const sel = h('select');
  if (includeAll) sel.appendChild(h('option', { value: 'all' }, 'Todos los proyectos'));
  for (const p of store.projects) {
    if (onlyActive && p.archived && String(p.id) !== String(value)) continue;
    sel.appendChild(h('option', { value: p.id }, `${p.key} · ${p.name}${p.archived ? ' (archivado)' : ''}`));
  }
  sel.value = value === undefined || value === null || value === '' ? (includeAll ? 'all' : sel.options[0]?.value || '') : String(value);
  return sel;
}

// ---------- verificación ----------
function verifyPath(entity, entityType) {
  return `/api/${entityType === 'task' ? 'tasks' : 'incidents'}/${encodeURIComponent(entity.key || entity.id)}/verify`;
}

export function canVerify(entity, entityType) {
  if (entity.status !== 'en_verificacion') return false;
  if (typeof entity.can_verify === 'boolean') return entity.can_verify;
  return entity.created_by === store.me?.id || can(entityType === 'task' ? 'tareas.verificar_todas' : 'incidencias.verificar_todas');
}

export function verificationPanel(entity, entityType, onDone) {
  if (!entity || entity.status !== 'en_verificacion') return h('div', { class: 'hidden' });
  const pendingRes = [...(entity.resolutions || [])].reverse().find(r => r.state === 'pendiente');
  const resolver = entity.verification_requested_by_name || pendingRes?.resolved_by_name || userName(entity.verification_requested_by);
  const since = entity.verification_requested_at || pendingRes?.resolved_at;
  const waiting = since ? Math.max(0, (Date.now() - new Date(since).getTime()) / 1000) : null;
  const solution = pendingRes?.solution || entity.resolution_note || '';
  const closing = pendingRes?.closing_comment || '';
  const ok = canVerify(entity, entityType);
  const label = entityType === 'task' ? 'la tarea' : 'la incidencia';

  const accept = h('button', { class: 'btn ok' }, icon('check'), 'Verificar y cerrar');
  const reject = h('button', { class: 'btn danger' }, icon('x'), 'Rechazar');
  accept.addEventListener('click', async () => {
    try {
      const r = await busy(accept, () => api.post(verifyPath(entity, entityType), { accept: true }), `${entity.key} verificada y cerrada.`);
      onDone && onDone(r.item, 'verified');
    } catch { /* aviso */ }
  });
  reject.addEventListener('click', async () => {
    const reason = await promptDialog({ title: `Rechazar la resolución de ${entity.key}`, label: '¿Qué sigue fallando o qué falta? (lo verá quien lo resolvió)', multiline: true, confirmLabel: 'Rechazar' });
    if (!reason) return;
    try {
      const r = await busy(reject, () => api.post(verifyPath(entity, entityType), { accept: false, reason }), `Resolución rechazada: ${entity.key} vuelve a «En progreso».`);
      onDone && onDone(r.item, 'rejected');
    } catch { /* aviso */ }
  });

  return h('div', { class: 'verify-box' },
    h('div', { class: 't' }, icon('verify'), 'Pendiente de verificación'),
    h('div', { class: 'small' }, h('b', null, resolver || 'Alguien'), ` marcó ${label} como resuelta`,
      since ? h('span', { title: fmtDateTime(since) }, ` ${fmtRelative(since)} · lleva esperando ${fmtDuration(waiting)}`) : null, '.'),
    solution ? h('div', null, h('div', { class: 'tiny muted' }, 'Solución aplicada'), h('div', { class: 'text-block small' }, richText(solution))) : h('div', { class: 'tiny muted' }, 'No se documentó la solución.'),
    closing ? h('div', null, h('div', { class: 'tiny muted' }, 'Comentario de cierre'), h('div', { class: 'text-block small' }, richText(closing))) : null,
    ok ? h('div', { class: 'row wrap' }, accept, reject, h('span', { class: 'tiny muted' }, 'Comprueba que está arreglado antes de cerrarla.'))
      : h('div', { class: 'small muted' }, `Esperando a que ${entity.created_by_name || 'quien la creó'} (o alguien con permiso de verificar) lo compruebe.`));
}

// ---------- historial de resoluciones ----------
export function resolutionHistory(resolutions) {
  const list = resolutions || [];
  if (!list.length) return emptyState('Sin resoluciones', 'Todavía no se ha resuelto ninguna vez.');
  const badge = (s) => h('span', { class: `badge ${s === 'verificada' ? 'ok' : s === 'rechazada' ? 'danger' : 'purple'}` }, RESOLUTION_STATES[s] || s);
  return h('div', { class: 'col resolution-history' }, [...list].reverse().map((r, i) => {
    const cls = r.state === 'pendiente' ? 'pending' : r.state === 'rechazada' ? 'rejected' : (i === 0 && !r.reopened_at ? '' : 'past');
    const dur = r.duration_seconds ?? (r.started_at && r.resolved_at ? (new Date(r.resolved_at) - new Date(r.started_at)) / 1000 : null);
    return h('div', { class: `resolution ${cls}` },
      h('div', { class: 'rh' }, h('b', null, `Ciclo ${r.cycle}`), badge(r.state),
        r.reopened_at ? h('span', { class: 'badge warn' }, 'Reabierta') : null),
      h('div', { class: 'rl' }, 'Resuelta por ', h('b', null, r.resolved_by_name || userName(r.resolved_by)), ` el ${fmtDateTime(r.resolved_at)}`,
        dur !== null ? ` · ${fmtDuration(Math.round(dur))} desde que se ${r.cycle > 1 ? 'reabrió' : 'abrió'}` : ''),
      r.solution ? h('div', { class: 'text-block small' }, richText(r.solution)) : h('div', { class: 'rl muted' }, 'Sin solución documentada.'),
      r.closing_comment ? h('div', { class: 'rl' }, h('b', null, 'Comentario de cierre: '), richText(r.closing_comment)) : null,
      r.state === 'verificada' && r.verified_at ? h('div', { class: 'rl' }, '✔ Verificada por ', h('b', null, r.verified_by_name || userName(r.verified_by)), ` el ${fmtDateTime(r.verified_at)}`) : null,
      r.state === 'rechazada' ? h('div', { class: 'rl danger-text' }, '✖ Rechazada por ', h('b', null, r.verified_by_name || userName(r.verified_by)),
        r.verified_at ? ` el ${fmtDateTime(r.verified_at)}` : '', r.reject_reason ? h('div', { class: 'pre' }, r.reject_reason) : null) : null,
      r.reopened_at ? h('div', { class: 'rl warn-text' }, '↺ Reabierta por ', h('b', null, r.reopened_by_name || userName(r.reopened_by)), ` el ${fmtDateTime(r.reopened_at)}`,
        r.reopen_reason ? h('div', { class: 'pre' }, r.reopen_reason) : null) : null);
  }));
}

// ---------- utilidades para las fichas ----------
// Historial de actividad (lista cronológica a partir de item.history).
export function activityTimeline(history) {
  const list = history || [];
  if (!list.length) return emptyState('Sin actividad', '');
  const cls = (a) => (a.action === 'created' ? 'created' : ['resolved', 'verified', 'completed'].includes(a.action) ? 'resolved'
    : a.action === 'blocked' ? 'blocked' : ['reopened', 'rejected'].includes(a.action) ? 'reopened' : '');
  return h('div', { class: 'timeline history-list' }, [...list].reverse().map(a => h('div', { class: `tl-item ${cls(a)}` }, h('span', { class: 'dot' }),
    h('div', null, h('div', null, a.summary), h('div', { class: 'tiny', title: fmtDateTime(a.created_at) }, `${a.user_name || 'Sistema'} · ${fmtRelative(a.created_at)}`)))));
}

// Commits y peticiones de cambios (PR) enlazados desde GitHub.
const PR_STATES = { open: ['Abierta', 'info'], merged: ['Fusionada', 'purple'], closed: ['Cerrada', 'neutral'] };
export function codeLinksList(links) {
  const list = links || [];
  if (!list.length) return h('div', { class: 'ws-empty' }, 'Sin commits ni PR enlazados. Menciona la clave (p. ej. APP-012) en el commit o en la PR.');
  return h('div', { class: 'col', style: { gap: '2px' } }, list.map(l => {
    const st = l.kind === 'pr' ? PR_STATES[l.state] || [l.state, 'neutral'] : null;
    return h('div', { class: 'code-link' }, icon('git'),
      h('span', { class: 'mono tiny nowrap' }, l.kind === 'pr' ? `PR #${l.ref}` : l.ref.slice(0, 7)),
      h('a', { class: 'grow ellipsis', href: l.url, target: '_blank', rel: 'noopener noreferrer', title: l.title }, l.title || l.url),
      st ? h('span', { class: `badge ${st[1]}` }, st[0]) : null,
      l.author ? h('span', { class: 'tiny muted nowrap' }, l.author) : null);
  }));
}

// «vence en 3 h» / «venció hace 2 d»
export function dueText(iso) {
  const s = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  const a = Math.abs(s);
  const span = a < 3600 ? `${Math.max(1, Math.round(a / 60))} min` : a < 86400 ? `${Math.round(a / 3600)} h` : `${Math.round(a / 86400)} d`;
  return s >= 0 ? `vence en ${span}` : `venció hace ${span}`;
}

export { fmtDate };

// Chat del equipo: canales, respuestas citadas, menciones, adjuntos, recordatorios,
// convertir mensajes en tareas o decisiones, confirmaciones de lectura y envío sin conexión.
import { api } from '../api.js';
import { activeProjects, can, defaultProjectId, entityHash, projectById, refreshCounters, store, userById } from '../state.js';
import { PRIORITIES, toOptions } from '../labels.js';
import {
  avatar, busy, clear, confirmDialog, debounce, draftKey, emptyState, errorBox, field, fmtDateTime, fmtDayLabel, fmtSize,
  fmtTime, h, icon, lightbox, loadJSON, loading, menuButton, modal, mount, promptDialog, richText, saveJSON, selectEl,
  statusBadge, toast, typeBadge, uid,
} from '../ui.js';

const PAGE = 50;
const isImage = (a) => (a.content_type || '').startsWith('image/');
const isVideo = (a) => (a.content_type || '').startsWith('video/');
const sameDay = (a, b) => new Date(a).toDateString() === new Date(b).toDateString();

function channelLabel(ch) {
  if (!ch) return '';
  return ch.kind === 'general' ? 'General' : ch.name;
}

function readDraft(cid) { try { return localStorage.getItem(draftKey(`chat.${cid}`)) || ''; } catch { return ''; } }
function writeDraft(cid, value) {
  try { value ? localStorage.setItem(draftKey(`chat.${cid}`), value) : localStorage.removeItem(draftKey(`chat.${cid}`)); } catch { /* lleno */ }
}

export default async function render(ctx) {
  const { root } = ctx;
  root.className = 'content flush';
  const me = store.me;
  const pendingKey = `dc.chat.pending.${me.id}`;

  // ---------- estado ----------
  let channels = [];
  let cid = null;                 // canal abierto
  let channel = null;
  const msgs = new Map();         // id → mensaje del servidor
  let hasOlder = false;
  let atLatest = true;            // la ventana cargada llega hasta el último mensaje
  let anchorId = null;            // mensaje alrededor del cual se cargó (si no estamos al final)
  let loadingOlder = false;
  let firstUnreadId = null;
  let lastMarked = 0;
  let searchMode = null;          // { q, items }
  let replyTo = null;             // mensaje al que se responde
  let editing = null;             // mensaje propio que se edita
  let files = [];                 // archivos preparados para el próximo mensaje
  let pending = loadJSON(pendingKey, []).filter(p => p && p.uid);  // mensajes aún no confirmados por el servidor
  const pendingFiles = new Map(); // uid → File[] (solo en memoria, para reintentar)
  const nodeCache = new Map();    // id → { sig, el }
  let mentionState = null;

  const savePending = () => saveJSON(pendingKey, pending.map(({ uid: u, channel_id, body, reply_to, created_at, status, error, file_names }) =>
    ({ uid: u, channel_id, body, reply_to, created_at, status: status === 'sending' ? 'queued' : status, error, file_names })));

  // ---------- estructura ----------
  const chanList = h('div', { class: 'chan-list' });
  const chanSelect = h('select', { class: 'chan-select', 'aria-label': 'Canal', onchange: (e) => openChannel(Number(e.target.value)) });
  const sidebar = h('aside', { class: 'chan-side' },
    h('div', { class: 'chan-side-head' }, h('b', null, 'Canales'),
      can('chat.canales') ? h('button', { class: 'btn ghost sm icon', title: 'Nuevo canal', onclick: newChannel }, icon('plus')) : null),
    chanList);
  const headTitle = h('div', { class: 'grow' });
  const searchInput = h('input', { type: 'search', placeholder: 'Buscar en este canal…', 'aria-label': 'Buscar en este canal' });
  searchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runSearch(); } if (e.key === 'Escape') exitSearch(); });
  const headMenu = h('span');
  const head = h('div', { class: 'chat-head' }, chanSelect, headTitle,
    h('div', { class: 'chat-search' }, icon('search', 15), searchInput), headMenu);
  const scroller = h('div', { class: 'chat-scroll', role: 'log', 'aria-live': 'polite' });
  const newPill = h('button', { class: 'new-pill hidden', onclick: () => { scrollBottom(true); } }, '↓ Mensajes nuevos');
  const latestPill = h('button', { class: 'new-pill hidden', onclick: () => loadLatest(true) }, 'Ir a los mensajes recientes');
  const scrollWrap = h('div', { class: 'chat-scroll-wrap' }, scroller, newPill, latestPill);

  // Compositor
  const ta = h('textarea', { rows: 1, placeholder: 'Escribe un mensaje… (Intro para enviar, Mayús+Intro para salto de línea, @ para mencionar)' });
  const ctxBar = h('div', { class: 'ctx hidden' });
  const filesBar = h('div', { class: 'row wrap composer-files hidden' });
  const mentionPop = h('div', { class: 'mention-pop hidden', role: 'listbox' });
  const fileInput = h('input', { type: 'file', multiple: true, class: 'hidden', onchange: () => { addFiles([...fileInput.files]); fileInput.value = ''; } });
  const sendBtn = h('button', { class: 'btn primary', title: 'Enviar', onclick: () => send() }, icon('right'), 'Enviar');
  const composer = h('div', { class: 'composer' }, mentionPop, ctxBar, filesBar,
    h('div', { class: 'box' },
      h('button', { class: 'btn icon', title: 'Adjuntar archivos o imágenes', onclick: () => fileInput.click() }, icon('clip')),
      ta, sendBtn, fileInput),
    h('div', { class: 'typing-hint' }, 'Puedes pegar imágenes con Ctrl+V o arrastrar archivos aquí. Escribe APP-001 o T-001 para enlazar trabajo.'));
  const chatEl = h('section', { class: 'chat' }, head, scrollWrap, composer);
  const layout = h('div', { class: 'chat-layout' }, sidebar, chatEl);
  mount(root, layout);

  // ---------- canales ----------
  async function loadChannels() {
    const r = await api.get('/api/channels');
    channels = r.items;
    drawChannels();
  }

  function drawChannels() {
    clear(chanList); clear(chanSelect);
    if (!channels.length) { chanList.appendChild(h('div', { class: 'empty small' }, 'No hay canales.')); return; }
    let lastKind = null;
    for (const ch of channels) {
      const kindLabel = ch.kind === 'general' ? null : ch.kind === 'proyecto' ? 'Proyectos' : 'Otros canales';
      if (kindLabel && kindLabel !== lastKind) chanList.appendChild(h('div', { class: 'chan-group' }, kindLabel));
      lastKind = kindLabel || lastKind;
      const unread = ch.id === cid ? 0 : ch.unread;
      chanList.appendChild(h('button', {
        class: 'chan' + (ch.id === cid ? ' on' : '') + (unread ? ' unread' : ''), title: channelLabel(ch),
        onclick: () => openChannel(ch.id),
      },
      ch.project_id ? h('span', { class: 'project-dot', style: { background: ch.project_color || 'var(--muted)' } }) : h('span', { class: 'hash' }, '#'),
      h('span', { class: 'grow ellipsis' }, channelLabel(ch)),
      unread ? h('span', { class: 'badge accent' }, unread > 99 ? '99+' : unread) : null));
      chanSelect.appendChild(h('option', { value: ch.id }, `${ch.project_key ? ch.project_key + ' · ' : '# '}${channelLabel(ch)}${unread ? ` (${unread})` : ''}`));
    }
    if (cid) chanSelect.value = String(cid);
  }

  function drawHead() {
    mount(headTitle,
      h('div', { class: 'row' }, h('h2', { class: 'ellipsis' }, channel?.kind === 'general' ? '# General' : `# ${channelLabel(channel)}`),
        channel?.project_key ? h('span', { class: 'badge neutral' }, channel.project_key) : null),
      h('div', { class: 'tiny muted' }, channel?.kind === 'general' ? 'Canal de todo el equipo'
        : channel?.project_id ? `Canal del proyecto ${projectById(channel.project_id)?.name || channel.project_key || ''}` : 'Canal personalizado'));
    clear(headMenu);
    if (channel && channel.kind !== 'general' && can('chat.canales')) {
      headMenu.appendChild(menuButton(icon('dots'), [
        { label: 'Cambiar el nombre', icon: 'edit', onClick: renameChannel },
        { label: 'Archivar el canal', icon: 'box', danger: true, onClick: archiveChannel },
      ], { title: 'Opciones del canal', class: 'btn ghost sm icon' }));
    }
  }

  async function newChannel() {
    const name = h('input', { maxlength: 60, placeholder: 'diseño, soporte, lanzamiento…' });
    const proj = selectEl([['', 'Sin proyecto (lo ven todos)'], ...activeProjects().map(p => [p.id, `${p.key} · ${p.name}`])], '');
    modal({
      title: 'Nuevo canal',
      body: h('div', { class: 'col gap-lg' }, field('Nombre del canal', name, { required: true }),
        field('Proyecto', proj, { hint: 'Si eliges un proyecto, solo lo verán sus miembros.' })),
      actions: [{ label: 'Cancelar' }, { label: 'Crear canal', kind: 'primary', onClick: async () => {
        const n = name.value.trim().replace(/^#/, '');
        if (!n) { toast('Escribe un nombre para el canal.', 'error'); name.focus(); return false; }
        try {
          const r = await api.post('/api/channels', { uid: uid(), name: n, project_id: proj.value ? Number(proj.value) : null });
          toast(`Canal #${n} creado.`, 'ok');
          await loadChannels();
          if (r.id) openChannel(r.id);
        } catch (err) { toast(err.message, 'error'); return false; }
      } }],
    });
  }

  async function renameChannel() {
    const n = await promptDialog({ title: 'Cambiar el nombre del canal', label: 'Nombre', value: channel.name, confirmLabel: 'Guardar' });
    if (!n) return;
    try { await api.patch(`/api/channels/${cid}`, { name: n.replace(/^#/, '') }); toast('Nombre cambiado.', 'ok'); await loadChannels(); channel = channels.find(c => c.id === cid) || channel; drawHead(); }
    catch (err) { toast(err.message, 'error'); }
  }

  async function archiveChannel() {
    const ok = await confirmDialog({ title: 'Archivar el canal', danger: true, confirmLabel: 'Archivar',
      message: `El canal #${channel.name} dejará de aparecer para todo el equipo. Los mensajes no se borran y seguirán apareciendo en la búsqueda de los avisos antiguos.` });
    if (!ok) return;
    try {
      await api.patch(`/api/channels/${cid}`, { archived: true });
      toast('Canal archivado.', 'ok');
      cid = null;
      await loadChannels();
      openChannel(channels[0]?.id);
    } catch (err) { toast(err.message, 'error'); }
  }

  // ---------- mensajes ----------
  function sorted() { return [...msgs.values()].sort((a, b) => a.id - b.id); }
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  function scrollBottom(smooth) {
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    newPill.classList.add('hidden');
    markRead();
  }

  function merge(items) {
    for (const m of items) msgs.set(m.id, m);
    const uids = new Set(items.map(m => m.uid));
    const before = pending.length;
    pending = pending.filter(p => !uids.has(p.uid));
    if (pending.length !== before) savePending();
  }

  async function openChannel(id, { around } = {}) {
    if (!id) { mount(scroller, emptyState('No hay canales disponibles', '')); return; }
    if (cid && ta.value !== undefined) writeDraft(cid, editing ? '' : ta.value);
    const changed = id !== cid;
    cid = id;
    channel = channels.find(c => c.id === id) || null;
    if (changed) {
      msgs.clear(); nodeCache.clear(); hasOlder = false; lastMarked = 0; firstUnreadId = null;
      exitSearch(true); cancelContext();
      ta.value = readDraft(id); autoGrow();
      files = []; drawFiles();
    }
    try { history.replaceState(null, '', `#/chat?c=${id}`); } catch { /* ignorado */ }
    saveJSON(`dc.chat.channel.${me.id}`, id);
    drawChannels(); drawHead();
    mount(scroller, loading('Cargando mensajes…'));
    const unreadBefore = channel?.unread || 0;
    try {
      if (around) await loadAround(around);
      else {
        const r = await api.get(`/api/messages?channel=${id}&limit=${PAGE}`);
        if (r.channel) channel = { ...channel, ...r.channel, unread: channel?.unread };
        merge(r.items); hasOlder = r.has_older; atLatest = true; anchorId = null;
        if (unreadBefore) {
          let n = 0;
          for (const m of [...r.items].reverse()) {
            if (m.user_id !== me.id) { n++; if (n === unreadBefore) { firstUnreadId = m.id; break; } }
          }
          if (!firstUnreadId && r.items.length) firstUnreadId = r.items.find(m => m.user_id !== me.id)?.id || null;
        }
      }
    } catch (err) { mount(scroller, errorBox(err, () => openChannel(id, { around }))); return; }
    if (!ctx.alive() || cid !== id) return;
    drawMessages();
    if (around) flash(around);
    else if (firstUnreadId) {
      const sep = scroller.querySelector('.unread-sep');
      if (sep) scroller.scrollTop = Math.max(0, sep.offsetTop - 40);
      if (nearBottom()) markRead();
    } else scrollBottom();
    if (channel) { channel.unread = 0; drawChannels(); }
    setTimeout(() => ta.focus(), 30);
  }

  async function loadAround(mid) {
    const r = await api.get(`/api/messages?channel=${cid}&around=${mid}&limit=60`);
    msgs.clear(); nodeCache.clear();
    merge(r.items); hasOlder = r.has_older; anchorId = mid;
    const last = r.items[r.items.length - 1];
    if (last) {
      const nx = await api.get(`/api/messages?channel=${cid}&after=${last.id}&limit=1`);
      atLatest = !nx.items.length;
    } else atLatest = true;
    if (atLatest) anchorId = null;
  }

  async function loadLatest(scroll) {
    msgs.clear(); nodeCache.clear();
    const r = await api.get(`/api/messages?channel=${cid}&limit=${PAGE}`);
    merge(r.items); hasOlder = r.has_older; atLatest = true; anchorId = null;
    drawMessages();
    if (scroll) scrollBottom();
  }

  async function loadOlder() {
    if (loadingOlder || !hasOlder || searchMode) return;
    const first = sorted()[0];
    if (!first) return;
    loadingOlder = true;
    const prevH = scroller.scrollHeight, prevTop = scroller.scrollTop;
    try {
      const r = await api.get(`/api/messages?channel=${cid}&before=${first.id}&limit=${PAGE}`);
      merge(r.items); hasOlder = r.has_older;
      drawMessages();
      scroller.scrollTop = scroller.scrollHeight - prevH + prevTop;
    } catch (err) { toast(err.message, 'error'); } finally { loadingOlder = false; }
  }

  // Recarga lo visible cuando el servidor avisa de cambios (mensajes nuevos, ediciones, lecturas).
  async function refreshMessages() {
    if (!cid || searchMode) return;
    const wasBottom = nearBottom();
    const prevMax = sorted().filter(m => m.user_id !== me.id).pop()?.id || 0;
    try {
      if (atLatest) {
        const r = await api.get(`/api/messages?channel=${cid}&limit=${Math.min(200, Math.max(PAGE, msgs.size + 10))}`);
        if (!msgs.size) hasOlder = r.has_older;
        merge(r.items);
      } else if (anchorId) {
        const r = await api.get(`/api/messages?channel=${cid}&around=${anchorId}&limit=60`);
        merge(r.items);
      }
    } catch { return; }
    if (!ctx.alive()) return;
    drawMessages();
    const newMax = sorted().filter(m => m.user_id !== me.id).pop()?.id || 0;
    if (wasBottom) scrollBottom();
    else if (newMax > prevMax && atLatest) newPill.classList.remove('hidden');
  }

  const markRead = debounce(async () => {
    if (!ctx.alive() || !cid || !atLatest || searchMode || document.hidden) return;
    const last = sorted().pop();
    if (!last || last.id <= lastMarked || !nearBottom()) return;
    lastMarked = last.id;
    try {
      await api.post('/api/messages/read', { channel_id: cid, last_id: last.id });
      const ch = channels.find(c => c.id === cid);
      if (ch && ch.unread) { ch.unread = 0; drawChannels(); }
      refreshCounters();
    } catch { lastMarked = 0; }
  }, 400);

  scroller.addEventListener('scroll', () => {
    if (scroller.scrollTop < 120) loadOlder();
    if (nearBottom()) { newPill.classList.add('hidden'); markRead(); }
  });
  const onVisible = () => { if (!document.hidden) markRead(); };
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('focus', onVisible);

  function flash(id) {
    const el = scroller.querySelector(`#msg-${id}`);
    if (!el) return false;
    el.scrollIntoView({ block: 'center' });
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
    return true;
  }

  async function jumpTo(id) {
    if (flash(id)) return;
    try {
      const loc = await api.get(`/api/messages/${id}/locate`);
      if (loc.channel_id !== cid) { await openChannel(loc.channel_id, { around: id }); return; }
      exitSearch(true);
      await loadAround(id);
      drawMessages();
      flash(id);
    } catch (err) { toast(err.message, 'error'); }
  }

  // ---------- dibujo de mensajes ----------
  function readInfo(m) {
    const readers = m.read_by || [];
    if (!readers.length) return h('span', { class: 'muted', title: 'Nadie lo ha leído todavía' }, icon('check', 12), ' Enviado');
    const others = store.users.filter(u => u.id !== me.id).length;
    const label = channel?.kind === 'general' && others && readers.length >= others ? 'Leído por todos'
      : readers.length === 1 ? `Leído por ${readers[0].user_name || 'una persona'}` : `Leído por ${readers.length}`;
    return h('button', { class: 'linklike read', title: readers.map(r => `${r.user_name} · ${fmtDateTime(r.read_at)}`).join('\n'),
      onclick: () => showReaders(m) }, icon('check', 12), ' ', label);
  }

  function showReaders(m) {
    const readers = m.read_by || [];
    const readIds = new Set(readers.map(r => r.user_id));
    const notYet = channel?.kind === 'general' ? store.users.filter(u => u.id !== me.id && !readIds.has(u.id)) : [];
    modal({ title: 'Confirmaciones de lectura', body: h('div', { class: 'col' },
      readers.length ? readers.map(r => h('div', { class: 'row' }, avatar(userById(r.user_id) || { display_name: r.user_name }),
        h('span', { class: 'grow' }, r.user_name), h('span', { class: 'small muted' }, fmtDateTime(r.read_at))))
        : h('div', { class: 'muted' }, 'Nadie lo ha leído todavía.'),
      notYet.length ? h('div', { class: 'section-title' }, 'Aún no lo han leído') : null,
      notYet.length ? h('div', { class: 'small muted' }, notYet.map(u => u.display_name).join(', ')) : null),
    actions: [{ label: 'Cerrar', kind: 'primary' }] });
  }

  function attachmentsEl(atts) {
    if (!atts || !atts.length) return null;
    return h('div', { class: 'msg-atts' }, atts.map(a => {
      if (isImage(a)) {
        const img = h('img', { alt: a.filename, loading: 'lazy' });
        let url = null;
        api.blobUrl(`/api/attachments/${a.id}?inline=1`).then(u => { url = u; img.src = u; }).catch(() => { img.alt = `No se puede mostrar ${a.filename}`; });
        img.addEventListener('click', () => url && lightbox(url, a.content_type));
        return h('figure', null, img, h('figcaption', { title: a.filename }, a.filename));
      }
      const open = async (btn) => {
        if (isVideo(a)) {
          try { lightbox(await api.blobUrl(`/api/attachments/${a.id}?inline=1`), a.content_type); } catch (err) { toast(err.message, 'error'); }
          return;
        }
        busy(btn, async () => { const r = await api.save(`/api/attachments/${a.id}`, { folder: 'descargas' }); return r; },
          `«${a.filename}» guardado en Descargas.`).catch(() => {});
      };
      const b = h('button', { class: 'file-pill att-file', title: isVideo(a) ? 'Ver vídeo' : 'Descargar', onclick: () => open(b) },
        icon(isVideo(a) ? 'play' : 'download', 14), h('span', null, a.filename), h('span', { class: 'tiny muted' }, fmtSize(a.size || 0)));
      return b;
    }));
  }

  function workCard(w) {
    return h('a', { class: 'inc-card', href: entityHash(w.type, w.id, w.key) },
      typeBadge(w.type), w.key ? h('b', { class: 'small' }, w.key) : null, h('span', { class: 'grow ellipsis small' }, w.title),
      w.status ? statusBadge(w.status, w.status_label) : null);
  }

  function msgEl(m) {
    const mine = m.user_id === me.id;
    const user = userById(m.user_id) || { display_name: m.user_name || 'Alguien' };
    const tools = h('div', { class: 'tools' },
      h('button', { title: 'Responder', onclick: () => startReply(m) }, icon('reply', 14)),
      mine ? h('button', { title: 'Editar', onclick: () => startEdit(m) }, icon('edit', 14)) : null,
      h('button', { title: 'Recordármelo más tarde', onclick: () => remindDialog(m) }, icon('bell', 14)),
      can('tareas.crear') ? h('button', { title: 'Convertir en tarea', onclick: () => taskDialog(m) }, icon('task', 14)) : null,
      can('conocimiento.editar') ? h('button', { title: 'Registrar como decisión', onclick: () => decisionDialog(m) }, icon('scale', 14)) : null,
      h('button', { title: 'Copiar el texto', onclick: () => copyText(m.body) }, icon('copy', 14)));
    const quote = m.reply_to ? h('div', { class: 'quote', title: 'Ir al mensaje citado', onclick: () => jumpTo(m.reply_to.id) },
      h('div', { class: 'qa' }, m.reply_to.user_name || ''), h('div', { class: 'qb' }, m.reply_to.body || '')) : null;
    const foot = h('div', { class: 'foot' },
      m.edited_at ? h('button', { class: 'linklike', title: 'Ver versiones anteriores', onclick: () => showEdits(m) }, '(editado)') : null,
      h('span', { title: fmtDateTime(m.created_at) }, fmtTime(m.created_at)),
      mine ? readInfo(m) : null);
    return h('div', { class: 'msg' + (mine ? ' mine' : ''), id: `msg-${m.id}` },
      avatar(user),
      h('div', { class: 'bubble' }, tools,
        h('div', { class: 'who' }, h('span', null, mine ? 'Tú' : user.display_name)),
        quote,
        m.body ? h('div', { class: 'body' }, richText(m.body)) : null,
        m.incident ? workCard({ type: 'incident', ...m.incident }) : null,
        attachmentsEl(m.attachments),
        (m.work || []).length ? h('div', { class: 'msg-work' }, m.work.map(workCard)) : null,
        foot));
  }

  function pendingEl(p) {
    const failed = p.status === 'failed';
    const user = me;
    const statusText = failed ? `No se ha enviado: ${p.error || 'error'}` : p.status === 'queued'
      ? 'Sin conexión: se enviará al reconectar' : 'Enviando…';
    const rq = p.reply_to ? msgs.get(p.reply_to) : null;
    return h('div', { class: 'msg mine pending-msg', id: `pending-${p.uid}` },
      avatar(user),
      h('div', { class: 'bubble' },
        h('div', { class: 'who' }, h('span', null, 'Tú')),
        rq ? h('div', { class: 'quote' }, h('div', { class: 'qa' }, rq.user_name), h('div', { class: 'qb' }, rq.body)) : null,
        p.body ? h('div', { class: 'body' }, richText(p.body)) : null,
        (p.file_names || []).length ? h('div', { class: 'msg-atts' }, p.file_names.map(n => h('span', { class: 'file-pill' }, icon('clip', 13), h('span', null, n)))) : null,
        p.progress != null && p.status === 'sending' ? h('div', { class: 'progress mt' }, h('i', { style: { width: `${Math.round(p.progress * 100)}%` } })) : null,
        h('div', { class: 'foot' },
          h('span', { class: failed ? 'failed' : 'pending' }, failed ? icon('alert', 12) : icon('clock', 12), ' ', statusText),
          failed ? h('button', { class: 'linklike', onclick: () => retry(p) }, 'Reintentar') : null,
          failed ? h('button', { class: 'linklike', onclick: () => discard(p) }, 'Descartar') : null)));
  }

  function signature(m) {
    return [m.body, m.edited_at, (m.read_by || []).length, (m.attachments || []).length, (m.work || []).length,
      m.reply_to?.body, store.users.length].join('|');
  }

  function drawMessages() {
    if (searchMode) return drawSearch();
    const list = sorted();
    const frag = document.createDocumentFragment();
    if (hasOlder) {
      frag.appendChild(h('div', { class: 'older' }, h('button', { class: 'btn sm ghost', onclick: (e) => busy(e.target, loadOlder) }, 'Cargar mensajes anteriores')));
    } else if (list.length) {
      frag.appendChild(h('div', { class: 'chan-start' }, h('b', null, `Inicio de #${channelLabel(channel)}`),
        h('div', { class: 'tiny muted' }, 'No hay mensajes anteriores.')));
    }
    let prev = null;
    for (const m of list) {
      if (!prev || !sameDay(prev.created_at, m.created_at)) frag.appendChild(h('div', { class: 'day-sep' }, fmtDayLabel(m.created_at)));
      if (firstUnreadId && m.id === firstUnreadId) frag.appendChild(h('div', { class: 'unread-sep' }, h('span', null, 'Mensajes nuevos')));
      const sig = signature(m);
      let cached = nodeCache.get(m.id);
      if (!cached || cached.sig !== sig) { cached = { sig, el: msgEl(m) }; nodeCache.set(m.id, cached); }
      const cont = prev && prev.user_id === m.user_id && sameDay(prev.created_at, m.created_at) && m.id !== firstUnreadId &&
        (new Date(m.created_at) - new Date(prev.created_at)) < 5 * 60 * 1000;
      cached.el.classList.toggle('cont', !!cont);
      frag.appendChild(cached.el);
      prev = m;
    }
    const mine = atLatest ? pending.filter(p => p.channel_id === cid) : [];
    if (mine.length && prev && !sameDay(prev.created_at, new Date().toISOString())) frag.appendChild(h('div', { class: 'day-sep' }, 'Hoy'));
    for (const p of mine) frag.appendChild(pendingEl(p));
    if (!list.length && !mine.length) {
      frag.appendChild(emptyState('Aún no hay mensajes en este canal', 'Escribe el primero abajo. Menciona a alguien con @ para avisarle.'));
    }
    clear(scroller);
    scroller.appendChild(frag);
    latestPill.classList.toggle('hidden', atLatest);
  }

  // ---------- búsqueda dentro del canal ----------
  async function runSearch() {
    const q = searchInput.value.trim();
    if (!q) { exitSearch(); return; }
    mount(scroller, loading('Buscando…'));
    try {
      const r = await api.get(`/api/messages?channel=${cid}&q=${encodeURIComponent(q)}&limit=100`);
      searchMode = { q, items: r.items };
      drawSearch();
    } catch (err) { mount(scroller, errorBox(err, runSearch)); }
  }

  function drawSearch() {
    const { q, items } = searchMode;
    mount(scroller,
      h('div', { class: 'callout info row' }, icon('search', 15),
        h('span', { class: 'grow' }, `${items.length} ${items.length === 1 ? 'mensaje encontrado' : 'mensajes encontrados'} con «${q}». Pulsa uno para verlo en la conversación.`),
        h('button', { class: 'btn sm', onclick: () => exitSearch() }, 'Salir de la búsqueda')),
      items.length ? [...items].reverse().map(m => h('button', { class: 'search-hit', onclick: () => { exitSearch(true); jumpTo(m.id); } },
        avatar(userById(m.user_id) || { display_name: m.user_name }),
        h('div', { class: 'grow' }, h('div', { class: 'small' }, h('b', null, m.user_name), h('span', { class: 'muted' }, ` · ${fmtDateTime(m.created_at)}`)),
          h('div', { class: 'body ellipsis2' }, m.body))))
        : emptyState('Sin resultados', 'Prueba con otras palabras.'));
    latestPill.classList.add('hidden');
  }

  function exitSearch(silent) {
    if (!searchMode) return;
    searchMode = null;
    searchInput.value = '';
    if (!silent) { drawMessages(); scrollBottom(); }
  }

  // ---------- acciones sobre mensajes ----------
  function copyText(text) {
    navigator.clipboard?.writeText(text || '').then(() => toast('Texto copiado.', 'ok'), () => toast('No se ha podido copiar.', 'error'));
  }

  async function showEdits(m) {
    try {
      const r = await api.get(`/api/messages/${m.id}/edits`);
      modal({ title: 'Historial de cambios del mensaje', wide: true, body: h('div', { class: 'col gap-lg' },
        h('div', null, h('div', { class: 'section-title' }, `Versión actual · ${fmtDateTime(m.edited_at)}`), h('div', { class: 'text-block' }, richText(m.body))),
        [...r.items].reverse().map((e, i, arr) => h('div', null,
          h('div', { class: 'section-title' }, i === arr.length - 1 ? `Original · ${fmtDateTime(m.created_at)}` : `Versión anterior · sustituida el ${fmtDateTime(e.edited_at)}`),
          h('div', { class: 'text-block' }, e.previous_body || '(vacío)')))),
      actions: [{ label: 'Cerrar', kind: 'primary' }] });
    } catch (err) { toast(err.message, 'error'); }
  }

  function startReply(m) {
    editing = null; replyTo = m;
    drawContext(); ta.focus();
  }

  function startEdit(m) {
    replyTo = null; editing = m;
    ta.value = m.body; autoGrow();
    drawContext(); ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  }

  function cancelContext() {
    if (editing) { ta.value = readDraft(cid); autoGrow(); }
    replyTo = null; editing = null; drawContext();
  }

  function drawContext() {
    if (!replyTo && !editing) { ctxBar.classList.add('hidden'); clear(ctxBar); sendBtn.lastChild.textContent = 'Enviar'; return; }
    ctxBar.classList.remove('hidden');
    if (editing) {
      mount(ctxBar, icon('edit', 15), h('span', { class: 'grow' }, h('b', null, 'Editando tu mensaje. '), h('span', { class: 'muted' }, 'Se guardará la versión anterior en el historial.')),
        h('button', { class: 'btn ghost sm', onclick: cancelContext }, 'Cancelar (Esc)'));
      sendBtn.lastChild.textContent = 'Guardar';
    } else {
      mount(ctxBar, icon('reply', 15), h('div', { class: 'grow ellipsis' }, h('b', null, `Respondiendo a ${replyTo.user_id === me.id ? 'ti' : replyTo.user_name}: `),
        h('span', { class: 'muted' }, (replyTo.body || '(archivos)').slice(0, 160))),
      h('button', { class: 'btn ghost sm icon', title: 'Quitar la respuesta', onclick: cancelContext }, icon('x')));
      sendBtn.lastChild.textContent = 'Enviar';
    }
  }

  function remindDialog(m) {
    const now = new Date();
    const at = (d) => { const x = new Date(d); x.setSeconds(0, 0); return x; };
    const inHour = at(Date.now() + 3600 * 1000);
    const tomorrow = at(now); tomorrow.setDate(tomorrow.getDate() + 1); tomorrow.setHours(9, 0);
    const monday = at(now); monday.setDate(monday.getDate() + ((8 - monday.getDay()) % 7 || 7)); monday.setHours(9, 0);
    const evening = at(now); evening.setHours(17, 0);
    const opts = [['1h', 'Dentro de 1 hora', inHour]];
    if (evening > now) opts.push(['tarde', 'Hoy a las 17:00', evening]);
    opts.push(['manana', 'Mañana a las 9:00', tomorrow], ['lunes', 'El lunes a las 9:00', monday], ['otra', 'Otra fecha…', null]);
    let choice = '1h';
    const pad = (n) => String(n).padStart(2, '0');
    const local = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const custom = h('input', { type: 'datetime-local', value: local(tomorrow), min: local(now) });
    const customField = field('Fecha y hora', custom);
    customField.classList.add('hidden');
    const note = h('input', { maxlength: 300, placeholder: 'Opcional: por qué quieres que te lo recuerde' });
    const seg = h('div', { class: 'seg' });
    const drawSeg = () => {
      clear(seg);
      for (const [k, label] of opts) seg.appendChild(h('button', { type: 'button', class: k === choice ? 'on' : '', onclick: () => { choice = k; customField.classList.toggle('hidden', k !== 'otra'); drawSeg(); } }, label));
    };
    drawSeg();
    modal({
      title: 'Recordatorio sobre este mensaje',
      body: h('div', { class: 'col gap-lg' },
        h('div', { class: 'quote' }, h('div', { class: 'qa' }, m.user_name), h('div', { class: 'qb' }, m.body || '(archivos)')),
        h('div', { class: 'col' }, h('span', { class: 'small muted' }, '¿Cuándo te lo recordamos?'), seg), customField,
        field('Nota', note),
        h('div', { class: 'tiny muted' }, 'Recibirás un aviso con el mensaje a esa hora (aunque tengas los avisos silenciados de chat).')),
      actions: [{ label: 'Cancelar' }, { label: 'Crear recordatorio', kind: 'primary', onClick: async () => {
        const when = choice === 'otra' ? (custom.value ? new Date(custom.value) : null) : opts.find(o => o[0] === choice)[2];
        if (!when || Number.isNaN(when.getTime())) { toast('Indica la fecha y la hora.', 'error'); return false; }
        if (when <= new Date()) { toast('La hora del recordatorio ya ha pasado.', 'error'); return false; }
        try {
          await api.post(`/api/messages/${m.id}/remind`, { remind_at: when.toISOString(), note: note.value.trim() });
          toast(`Te lo recordaremos el ${fmtDateTime(when.toISOString())}.`, 'ok');
        } catch (err) { toast(err.message, 'error'); return false; }
      } }],
    });
  }

  function projectChoices() {
    return activeProjects().map(p => [p.id, `${p.key} · ${p.name}`]);
  }

  function taskDialog(m) {
    const firstLine = (m.body || '').split('\n').find(l => l.trim()) || 'Tarea desde el chat';
    const title = h('input', { maxlength: 200, value: firstLine.slice(0, 120) });
    const desc = h('textarea', { rows: 5 });
    desc.value = `${m.body || ''}\n\n— Mensaje de ${m.user_name} en #${channelLabel(channel)} (${fmtDateTime(m.created_at)})`;
    const projects = projectChoices();
    if (!projects.length) { toast('No hay proyectos activos en los que crear la tarea.', 'error'); return; }
    const proj = selectEl(projects, channel?.project_id || defaultProjectId() || projects[0][0]);
    const prio = selectEl(toOptions(PRIORITIES), 'media');
    const people = can('tareas.asignar') ? [['', 'Sin asignar'], ...store.users.map(u => [u.id, u.display_name])]
      : [['', 'Sin asignar'], [me.id, `Yo (${me.display_name})`]];
    const assignee = selectEl(people, '');
    modal({
      title: 'Convertir el mensaje en tarea', wide: true,
      body: h('div', { class: 'form-grid' },
        field('Título', title, { required: true, cls: 'span2' }),
        field('Descripción', desc, { cls: 'span2', hint: 'La tarea quedará enlazada a este mensaje.' }),
        field('Proyecto', proj, { required: true }), field('Prioridad', prio),
        field('Responsable', assignee)),
      actions: [{ label: 'Cancelar' }, { label: 'Crear tarea', kind: 'primary', onClick: async () => {
        if (!title.value.trim()) { toast('El título es obligatorio.', 'error'); title.focus(); return false; }
        try {
          const r = await api.post('/api/tasks', {
            uid: uid(), title: title.value.trim(), description: desc.value.trim(), project_id: Number(proj.value),
            priority: prio.value, assignee_id: assignee.value ? Number(assignee.value) : null, source_message_id: m.id,
          }, { queue: true });
          if (r._queued) toast('Sin conexión: la tarea se creará al reconectar.', 'warn');
          else toast(`Tarea ${r.item?.key || ''} creada.`, 'ok', { onClick: () => { location.hash = `#/tareas/${r.item?.key}`; } });
          refreshMessages();
        } catch (err) { toast(err.message, 'error'); return false; }
      } }],
    });
  }

  function decisionDialog(m) {
    const title = h('input', { maxlength: 200, value: ((m.body || '').split('\n').find(l => l.trim()) || '').slice(0, 120) });
    const context = h('textarea', { rows: 5 });
    context.value = `${m.body || ''}\n\n— Mensaje de ${m.user_name} en #${channelLabel(channel)} (${fmtDateTime(m.created_at)})`;
    const projects = projectChoices();
    if (!projects.length) { toast('No hay proyectos activos.', 'error'); return; }
    const proj = selectEl(projects, channel?.project_id || defaultProjectId() || projects[0][0]);
    modal({
      title: 'Registrar como decisión', wide: true,
      body: h('div', { class: 'form-grid' }, field('Título de la decisión', title, { required: true, cls: 'span2' }),
        field('Contexto', context, { cls: 'span2' }), field('Proyecto', proj, { required: true }),
        h('div', { class: 'small muted' }, 'Se creará como «Propuesta». Podrás completarla en Decisiones.')),
      actions: [{ label: 'Cancelar' }, { label: 'Registrar', kind: 'primary', onClick: async () => {
        if (!title.value.trim()) { toast('El título es obligatorio.', 'error'); title.focus(); return false; }
        try {
          const r = await api.post('/api/decisions', { uid: uid(), title: title.value.trim(), context: context.value.trim(),
            project_id: Number(proj.value), status: 'propuesta', source_message_id: m.id }, { queue: true });
          if (r._queued) toast('Sin conexión: la decisión se registrará al reconectar.', 'warn');
          else toast(`Decisión ${r.key} registrada.`, 'ok', { onClick: () => { location.hash = `#/decisiones/${r.key}`; } });
          refreshMessages();
        } catch (err) { toast(err.message, 'error'); return false; }
      } }],
    });
  }

  // ---------- compositor ----------
  function autoGrow() {
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(200, Math.max(44, ta.scrollHeight + 2))}px`;
  }

  function addFiles(list) {
    const ok = list.filter(f => f && f.size > 0);
    if (list.length && !ok.length) toast('Los archivos están vacíos.', 'error');
    files.push(...ok);
    drawFiles();
    ta.focus();
  }

  function drawFiles() {
    clear(filesBar);
    filesBar.classList.toggle('hidden', !files.length);
    files.forEach((f, i) => filesBar.appendChild(h('span', { class: 'file-pill', title: f.name },
      icon(f.type.startsWith('image/') ? 'eye' : 'file', 13), h('span', null, f.name), h('span', { class: 'tiny muted' }, fmtSize(f.size)),
      h('button', { title: 'Quitar', 'aria-label': `Quitar ${f.name}`, onclick: () => { files.splice(i, 1); drawFiles(); } }, '×'))));
  }

  async function send() {
    if (editing) return saveEdit();
    const body = ta.value.trim();
    if (!body && !files.length) { ta.focus(); return; }
    if (!cid) return;
    const p = {
      uid: uid(), channel_id: cid, body, reply_to: replyTo?.id || null, created_at: new Date().toISOString(),
      status: 'sending', file_names: files.map(f => f.name),
    };
    const toSend = files;
    pendingFiles.set(p.uid, toSend);
    pending.push(p); savePending();
    ta.value = ''; writeDraft(cid, ''); autoGrow();
    files = []; drawFiles();
    replyTo = null; drawContext();
    if (searchMode) exitSearch(true);
    if (!atLatest) await loadLatest(false);
    drawMessages(); scrollBottom();
    await deliver(p);
  }

  async function deliver(p) {
    const toSend = pendingFiles.get(p.uid) || [];
    p.status = 'sending'; p.error = null;
    try {
      const r = await api.post('/api/messages', { uid: p.uid, channel_id: p.channel_id, body: p.body, reply_to_id: p.reply_to,
        with_attachments: toSend.length > 0 }, { queue: true });
      let queued = !!r._queued;
      for (let i = 0; i < toSend.length; i++) {
        const f = toSend[i];
        try {
          const up = await api.upload(f, { owner_type: 'message', owner_uid: p.uid }, (x) => {
            p.progress = (i + x) / toSend.length; updatePendingNode(p);
          });
          if (up._queued) queued = true;
        } catch (err) { toast(`No se ha podido subir «${f.name}»: ${err.message}`, 'error'); }
      }
      pendingFiles.delete(p.uid);
      p.progress = null;
      if (queued) { p.status = 'queued'; savePending(); updatePendingNode(p); return; }
      await refreshMessages();
      // Si por algún motivo no llega en la recarga, lo damos por enviado igualmente.
      pending = pending.filter(x => x.uid !== p.uid); savePending();
      drawMessages(); if (nearBottom()) scrollBottom();
    } catch (err) {
      p.status = 'failed'; p.error = err.message; savePending(); updatePendingNode(p);
      toast(err.message, 'error');
    }
  }

  function updatePendingNode(p) {
    const el = scroller.querySelector(`#pending-${p.uid}`);
    if (el) el.replaceWith(pendingEl(p));
  }

  function retry(p) { deliver(p); updatePendingNode(p); }
  function discard(p) {
    pending = pending.filter(x => x.uid !== p.uid); pendingFiles.delete(p.uid); savePending();
    scroller.querySelector(`#pending-${p.uid}`)?.remove();
  }

  async function saveEdit() {
    const body = ta.value.trim();
    if (!body) { toast('El mensaje no puede quedar vacío.', 'error'); return; }
    const m = editing;
    if (body === m.body) { cancelContext(); return; }
    try {
      await busy(sendBtn, () => api.patch(`/api/messages/${m.id}`, { body }), 'Mensaje editado.');
      editing = null; ta.value = readDraft(cid); autoGrow(); drawContext();
      await refreshMessages();
    } catch { /* aviso ya mostrado */ }
  }

  // Autocompletado de @menciones
  function mentionQuery() {
    const pos = ta.selectionStart;
    const before = ta.value.slice(0, pos);
    const mm = /(^|[\s(])@([A-Za-z0-9_.-]{0,40})$/.exec(before);
    return mm ? { q: mm[2].toLowerCase(), start: pos - mm[2].length - 1, end: pos } : null;
  }

  function updateMentions() {
    const mq = mentionQuery();
    if (!mq) { closeMentions(); return; }
    const norm = (s) => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    const users = store.users.filter(u => u.id !== me.id && (norm(u.username).includes(norm(mq.q)) || norm(u.display_name).includes(norm(mq.q)))).slice(0, 8);
    if (!users.length) { closeMentions(); return; }
    const sel = mentionState && mentionState.users.length === users.length ? Math.min(mentionState.sel, users.length - 1) : 0;
    mentionState = { ...mq, users, sel };
    drawMentions();
  }

  function drawMentions() {
    const { users, sel } = mentionState;
    mount(mentionPop, h('div', { class: 'tiny muted mention-title' }, 'Mencionar a… (Intro o Tab para elegir)'),
      users.map((u, i) => h('button', { class: 'mention-item' + (i === sel ? ' on' : ''), type: 'button', role: 'option',
        onmousedown: (e) => { e.preventDefault(); pickMention(u); } },
      avatar(u, u.online), h('span', { class: 'grow' }, u.display_name), h('span', { class: 'tiny muted' }, `@${u.username}`))));
    mentionPop.classList.remove('hidden');
  }

  function pickMention(u) {
    const { start, end } = mentionState;
    const ins = `@${u.username} `;
    ta.value = ta.value.slice(0, start) + ins + ta.value.slice(end);
    const pos = start + ins.length;
    ta.setSelectionRange(pos, pos);
    closeMentions(); ta.focus(); autoGrow();
    if (!editing) writeDraft(cid, ta.value);
  }

  function closeMentions() { mentionState = null; mentionPop.classList.add('hidden'); clear(mentionPop); }

  ta.addEventListener('input', () => { autoGrow(); if (!editing) writeDraft(cid, ta.value); updateMentions(); });
  ta.addEventListener('click', updateMentions);
  ta.addEventListener('blur', () => setTimeout(closeMentions, 150));
  ta.addEventListener('keydown', (e) => {
    if (mentionState) {
      if (e.key === 'ArrowDown') { mentionState.sel = (mentionState.sel + 1) % mentionState.users.length; drawMentions(); e.preventDefault(); return; }
      if (e.key === 'ArrowUp') { mentionState.sel = (mentionState.sel - 1 + mentionState.users.length) % mentionState.users.length; drawMentions(); e.preventDefault(); return; }
      if (e.key === 'Enter' || e.key === 'Tab') { pickMention(mentionState.users[mentionState.sel]); e.preventDefault(); return; }
      if (e.key === 'Escape') { closeMentions(); e.preventDefault(); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); return; }
    if (e.key === 'Escape' && (replyTo || editing)) { cancelContext(); e.preventDefault(); return; }
    if (e.key === 'ArrowUp' && !ta.value && !editing) {
      const last = sorted().filter(m => m.user_id === me.id).pop();
      if (last) { e.preventDefault(); startEdit(last); }
    }
  });
  ta.addEventListener('paste', (e) => {
    const fl = [...(e.clipboardData?.files || [])];
    if (fl.length) {
      e.preventDefault();
      addFiles(fl.map((f, i) => f.name && f.name !== 'image.png' ? f
        : new File([f], `captura-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}${i ? '-' + i : ''}.png`, { type: f.type || 'image/png' })));
    }
  });
  chatEl.addEventListener('dragover', (e) => { if (e.dataTransfer?.types?.includes('Files')) { e.preventDefault(); chatEl.classList.add('drop-over'); } });
  chatEl.addEventListener('dragleave', (e) => { if (e.target === chatEl || !chatEl.contains(e.relatedTarget)) chatEl.classList.remove('drop-over'); });
  chatEl.addEventListener('drop', (e) => {
    chatEl.classList.remove('drop-over');
    const fl = [...(e.dataTransfer?.files || [])];
    if (fl.length) { e.preventDefault(); addFiles(fl); }
  });

  // ---------- arranque ----------
  try { await loadChannels(); } catch (err) { mount(root, errorBox(err, () => ctx.refresh())); return; }
  const targetMsg = Number(ctx.query.get('m')) || null;
  const wanted = Number(ctx.query.get('c')) || loadJSON(`dc.chat.channel.${me.id}`, null);
  if (targetMsg) {
    try {
      const loc = await api.get(`/api/messages/${targetMsg}/locate`);
      await openChannel(loc.channel_id, { around: targetMsg });
    } catch (err) {
      toast(err.message || 'No se ha encontrado el mensaje.', 'error');
      await openChannel(channels[0]?.id);
    }
  } else {
    await openChannel(channels.some(c => c.id === wanted) ? wanted : channels[0]?.id);
  }

  ctx.onChanges(['message', 'chat_read'], refreshMessages);
  ctx.onChanges(['channel', 'message', 'chat_read', 'project', 'permissions'], async () => {
    try {
      await loadChannels();
      if (cid && !channels.some(c => c.id === cid)) { toast('El canal que tenías abierto se ha archivado.', 'warn'); cid = null; openChannel(channels[0]?.id); }
      else { channel = channels.find(c => c.id === cid) || channel; drawHead(); }
    } catch { /* sin conexión */ }
  });
  ctx.onChanges(['user', 'presence'], () => { nodeCache.clear(); drawMessages(); });
  // Al reconectar, los mensajes guardados en cola se envían: se recargan para confirmarlos.
  ctx.on('reconnected', () => setTimeout(refreshMessages, 1500));

  return () => {
    if (cid && !editing) writeDraft(cid, ta.value);
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('focus', onVisible);
  };
}

// Wiki del equipo: árbol de páginas por carpetas, lectura con formato sencillo, edición con control
// de versiones, historial de revisiones (ver y restaurar), adjuntos y búsqueda.
import { api } from '../api.js';
import { activeProjects, can, projectById } from '../state.js';
import {
  busy, clear, confirmDialog, debounce, draftKey, emptyState, errorBox, field, fmtDateTime, fmtRelative, fmtSize, h, icon,
  loading, modal, mount, richText, selectEl, toast, uid,
} from '../ui.js';
import { attachmentsSection } from './shared.js';

// ---------- Markdown sencillo → elementos (sin innerHTML) ----------
const INLINE_SRC = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\))|(\*[^*\s][^*\n]*\*)|((?<![\w])_[^_\s][^_\n]*_(?![\w]))/g;

function inline(text) {
  const out = [];
  let last = 0, m;
  const re = new RegExp(INLINE_SRC.source, 'g');  // una por llamada: inline() es recursiva
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(...richText(text.slice(last, m.index)));
    if (m[1]) out.push(h('code', null, m[1].slice(1, -1)));
    else if (m[2]) out.push(h('strong', null, inline(m[2].slice(2, -2))));
    else if (m[3]) out.push(h('a', { href: m[5], target: '_blank', rel: 'noopener noreferrer' }, m[4]));
    else if (m[6]) out.push(h('em', null, inline(m[6].slice(1, -1))));
    else if (m[7]) out.push(h('em', null, inline(m[7].slice(1, -1))));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(...richText(text.slice(last)));
  return out;
}

export function renderMarkdown(src) {
  const lines = (src || '').replace(/\r\n?/g, '\n').split('\n');
  const root = h('div', { class: 'md' });
  let i = 0;
  const withBreaks = (arr) => arr.flatMap((l, k) => (k ? [h('br'), ...inline(l)] : inline(l)));
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const code = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
      i++;
      root.appendChild(h('pre', { class: 'md-code' }, h('code', null, code.join('\n'))));
      continue;
    }
    const hd = /^(#{1,4})\s+(.*)$/.exec(line);
    if (hd) { root.appendChild(h(`h${hd[1].length + 1}`, null, inline(hd[2]))); i++; continue; }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { root.appendChild(h('hr')); i++; continue; }
    if (/^>\s?/.test(line)) {
      const q = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ''));
      root.appendChild(h('blockquote', null, withBreaks(q)));
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const ul = h('ul');
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        const t = lines[i++].replace(/^\s*[-*+]\s+/, '');
        const cb = /^\[([ xX])\]\s+(.*)$/.exec(t);
        ul.appendChild(cb ? h('li', { class: 'md-check' }, h('input', { type: 'checkbox', checked: cb[1] !== ' ', disabled: true }), ' ', inline(cb[2]))
          : h('li', null, inline(t)));
      }
      root.appendChild(ul);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const ol = h('ol');
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) ol.appendChild(h('li', null, inline(lines[i++].replace(/^\s*\d+[.)]\s+/, ''))));
      root.appendChild(ol);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|>|\s*[-*+]\s+|\s*\d+[.)]\s+|\s*(-{3,}|\*{3,}|_{3,})\s*$)/.test(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    root.appendChild(h('p', null, withBreaks(para)));
  }
  if (!root.childNodes.length) root.appendChild(h('p', { class: 'muted' }, 'Esta página está vacía.'));
  return root;
}

function snippetEl(s) {
  // El buscador marca las coincidencias entre corchetes.
  const out = [];
  const re = /\[([^\]]*)\]/g;
  let last = 0, m;
  while ((m = re.exec(s || ''))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    out.push(h('mark', null, m[1]));
    last = m.index + m[0].length;
  }
  out.push((s || '').slice(last));
  return out;
}

const readDraft = (name) => { try { return localStorage.getItem(draftKey(name)); } catch { return null; } };
const writeDraft = (name, v) => { try { v == null ? localStorage.removeItem(draftKey(name)) : localStorage.setItem(draftKey(name), v); } catch { /* lleno */ } };

export default async function render(ctx) {
  const { root } = ctx;
  const canEdit = can('conocimiento.editar');
  const pageId = ctx.params.id && /^\d+$/.test(ctx.params.id) ? Number(ctx.params.id) : null;
  let pages = [];
  let page = null;
  let mode = pageId ? 'view' : (ctx.query.get('nueva') && canEdit ? 'new' : 'home');
  let filter = '';
  let contentHits = null;
  const collapsed = new Set();
  let editorState = null;   // { title, folder, body, project, version, conflictNote }

  const search = h('input', { type: 'search', placeholder: 'Buscar en la wiki…', 'aria-label': 'Buscar en la wiki' });
  const tree = h('div', { class: 'wiki-tree' });
  const main = h('div', { class: 'wiki-main' });
  const aside = h('aside', { class: 'wiki-side' },
    h('div', { class: 'row' }, h('div', { class: 'wiki-search grow' }, icon('search', 15), search),
      canEdit ? h('button', { class: 'btn primary sm icon', title: 'Nueva página', onclick: () => startNew() }, icon('plus')) : null),
    tree);
  mount(root, h('div', { class: 'wiki-layout' }, aside, main));

  // ---------- lista y árbol ----------
  async function loadList() { pages = (await api.get('/api/wiki')).items; }
  const folders = () => [...new Set(pages.map(p => p.folder).filter(Boolean))].sort((a, b) => a.localeCompare(b));

  function drawTree() {
    const q = filter.trim().toLowerCase();
    const match = (p) => !q || `${p.title} ${p.folder} ${p.excerpt || ''}`.toLowerCase().includes(q);
    const shown = pages.filter(match);
    clear(tree);
    if (!pages.length) { tree.appendChild(h('div', { class: 'small muted pad' }, 'Aún no hay páginas.')); return; }
    const groups = new Map();
    for (const p of shown) { const f = p.folder || ''; if (!groups.has(f)) groups.set(f, []); groups.get(f).push(p); }
    const keys = [...groups.keys()].sort((a, b) => (a === '') - (b === '') || a.localeCompare(b));
    for (const f of keys) {
      const items = groups.get(f).sort((a, b) => a.title.localeCompare(b.title));
      const isCollapsed = !q && collapsed.has(f) && !items.some(p => p.id === pageId);
      tree.appendChild(h('button', { class: 'wiki-folder', 'aria-expanded': isCollapsed ? 'false' : 'true',
        onclick: () => { collapsed.has(f) ? collapsed.delete(f) : collapsed.add(f); drawTree(); } },
      icon(isCollapsed ? 'right' : 'folder', 14), h('span', { class: 'grow ellipsis' }, f || 'Sin carpeta'), h('span', { class: 'tiny muted' }, items.length)));
      if (isCollapsed) continue;
      for (const p of items) {
        tree.appendChild(h('a', { class: 'wiki-link' + (p.id === pageId ? ' on' : ''), href: `#/wiki/${p.id}`, title: p.title },
          icon('file', 14), h('span', { class: 'grow ellipsis' }, p.title),
          p.project_id ? h('span', { class: 'tiny muted' }, projectById(p.project_id)?.key || '') : null));
      }
    }
    if (!shown.length) tree.appendChild(h('div', { class: 'small muted pad' }, 'Ningún título coincide.'));
    if (contentHits && q) {
      tree.appendChild(h('div', { class: 'section-title pad' }, 'En el contenido'));
      if (!contentHits.length) tree.appendChild(h('div', { class: 'small muted pad' }, 'Sin coincidencias en el texto.'));
      for (const r of contentHits) {
        tree.appendChild(h('a', { class: 'wiki-hit', href: `#/wiki/${r.id}` }, h('b', { class: 'small' }, r.title),
          h('div', { class: 'tiny muted' }, snippetEl(r.snippet))));
      }
    }
  }

  const contentSearch = debounce(async () => {
    const q = filter.trim();
    if (q.length < 3) { contentHits = null; drawTree(); return; }
    try {
      const r = await api.get(`/api/search?q=${encodeURIComponent(q)}&limit=50`);
      if (filter.trim() !== q) return;
      contentHits = r.items.filter(i => i.type === 'wiki');
    } catch { contentHits = null; }
    drawTree();
  }, 300);
  search.addEventListener('input', () => { filter = search.value; drawTree(); contentSearch(); });

  // ---------- portada ----------
  function drawHome() {
    ctx.setTitle('Wiki');
    const recent = [...pages].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')).slice(0, 10);
    mount(main,
      h('div', { class: 'page-head' }, h('h1', null, 'Wiki del equipo'),
        canEdit ? h('button', { class: 'btn primary', onclick: () => startNew() }, icon('plus'), 'Nueva página') : null),
      h('p', { class: 'muted' }, 'Guías, procedimientos y soluciones a problemas conocidos. Elige una página a la izquierda o busca por palabras.'),
      pages.length ? h('div', { class: 'list-card' }, h('div', { class: 'card-head' }, h('h3', null, 'Cambios recientes')),
        recent.map(p => h('a', { class: 'list-item', href: `#/wiki/${p.id}` }, icon('file', 15),
          h('div', { class: 'grow' }, h('div', { class: 'ellipsis' }, p.title), h('div', { class: 'tiny muted ellipsis' }, (p.excerpt || '').replace(/\s+/g, ' '))),
          p.folder ? h('span', { class: 'badge neutral' }, p.folder) : null,
          h('span', { class: 'tiny muted nowrap' }, `${p.updated_by_name || ''} · ${fmtRelative(p.updated_at)}`))))
        : emptyState('Aún no hay páginas', canEdit ? 'Crea la primera: por ejemplo «Cómo publicar una versión» o «Accesos y entornos».' : 'Cuando el equipo escriba páginas aparecerán aquí.'));
  }

  // ---------- ver página ----------
  async function loadPage() {
    page = (await api.get(`/api/wiki/${pageId}`)).item;
  }

  function drawView(conflict) {
    ctx.setTitle(`Wiki · ${page.title}`);
    const proj = page.project_id ? projectById(page.project_id) : null;
    const att = attachmentsSection('wiki', page.id, { canUpload: canEdit, data: page, title: 'Archivos adjuntos' });
    mount(main,
      h('div', { class: 'wiki-crumbs small muted' }, h('a', { href: '#/wiki' }, 'Wiki'), page.folder ? [' / ', page.folder] : null),
      h('div', { class: 'detail-head' },
        h('div', { class: 'grow' }, h('h1', null, page.title),
          h('div', { class: 'small muted' }, `Editada por ${page.updated_by_name || '—'} ${fmtRelative(page.updated_at)} · creada por ${page.created_by_name || '—'} el ${fmtDateTime(page.created_at)}`,
            proj ? ' · ' : null, proj ? h('span', { class: 'badge neutral' }, `${proj.key} · ${proj.name}`) : null)),
        h('div', { class: 'actions' },
          h('button', { class: 'btn', onclick: showHistory, disabled: !page.revisions.length, title: page.revisions.length ? 'Versiones anteriores' : 'Aún no hay versiones anteriores' },
            icon('clock'), `Historial (${page.revisions.length})`),
          canEdit ? h('button', { class: 'btn primary', onclick: () => startEdit() }, icon('edit'), 'Editar') : null)),
      conflict || null,
      page.source_incident ? h('div', { class: 'callout info mb' }, 'Artículo creado a partir de la solución de ',
        h('a', { href: `#/incidencias/${page.source_incident.key}` }, `${page.source_incident.key} · ${page.source_incident.title}`), '.') : null,
      h('article', { class: 'card wiki-body' }, renderMarkdown(page.body)),
      h('div', { class: 'mt-lg' }, att));
  }

  function conflictBox(mine) {
    const copy = () => navigator.clipboard?.writeText(mine.body).then(() => toast('Tu texto se ha copiado.', 'ok'), () => toast('No se ha podido copiar.', 'error'));
    return h('div', { class: 'callout warn mb col' },
      h('b', null, 'Otra persona guardó esta página mientras la editabas. Se muestra su versión; tus cambios NO se han guardado.'),
      h('details', null, h('summary', null, 'Ver mi texto sin guardar'), h('pre', { class: 'text-block small' }, mine.body)),
      h('div', { class: 'row' }, h('button', { class: 'btn sm', onclick: copy }, icon('copy'), 'Copiar mi texto'),
        h('button', { class: 'btn sm primary', onclick: () => startEdit(mine) }, 'Volver a editar con mi texto')));
  }

  // ---------- historial ----------
  function showHistory() {
    const list = page.revisions;
    const dlg = modal({ title: `Historial de «${page.title}»`, wide: true,
      body: h('div', { class: 'col' },
        h('div', { class: 'small muted' }, 'Cada fila es una versión anterior de la página, tal y como quedó antes de la siguiente edición.'),
        h('div', { class: 'list-card' },
          h('div', { class: 'list-item current' }, icon('check', 15), h('div', { class: 'grow' }, h('b', null, 'Versión actual'),
            h('div', { class: 'tiny muted' }, `${page.updated_by_name || '—'} · ${fmtDateTime(page.updated_at)}`))),
          list.map(r => h('button', { class: 'list-item', onclick: () => { dlg.close(); showRevision(r); } }, icon('file', 15),
            h('div', { class: 'grow' }, h('div', null, r.title), h('div', { class: 'tiny muted' }, `${r.edited_by_name || '—'} · ${fmtDateTime(r.edited_at)} · ${fmtSize(r.size || 0)}`)),
            h('span', { class: 'small' }, 'Ver'))))),
      actions: [{ label: 'Cerrar', kind: 'primary' }] });
  }

  async function showRevision(r) {
    let rev;
    try { rev = (await api.get(`/api/wiki/${page.id}/revisions/${r.id}`)).item; } catch (err) { toast(err.message, 'error'); return; }
    modal({ title: `Versión del ${fmtDateTime(rev.edited_at)}`, wide: true,
      body: h('div', { class: 'col' }, h('div', { class: 'small muted' }, `Escrita por ${r.edited_by_name || '—'}.`),
        h('h2', null, rev.title), h('div', { class: 'wiki-body card' }, renderMarkdown(rev.body))),
      actions: [{ label: 'Volver al historial', onClick: () => { setTimeout(showHistory, 0); } },
        ...(canEdit ? [{ label: 'Restaurar esta versión', kind: 'primary', onClick: async () => {
          const ok = await confirmDialog({ title: 'Restaurar versión', confirmLabel: 'Restaurar',
            message: 'La página volverá a tener este título y este contenido. La versión actual se guardará en el historial, así que no se pierde nada.' });
          if (!ok) return false;
          try {
            const res = await api.patch(`/api/wiki/${page.id}`, { title: rev.title, body: rev.body, version: page.version });
            toast(res.unchanged ? 'La página ya tenía ese contenido.' : 'Versión restaurada.', 'ok');
            await refreshAll();
          } catch (err) {
            toast(err.message, 'error');
            if (err.status === 409) await refreshAll();
            return false;
          }
        } }] : [])] });
  }

  // ---------- edición ----------
  function startNew() {
    if (mode === 'edit' || mode === 'new') return;
    if (pageId) { location.hash = '#/wiki?nueva=1'; return; }
    mode = 'new';
    drawEditor();
  }

  function startEdit(mine) {
    mode = 'edit';
    editorState = mine ? { ...mine, version: page.version } : null;
    drawEditor();
  }

  function drawEditor() {
    const isNew = mode === 'new';
    ctx.setTitle(isNew ? 'Wiki · Nueva página' : `Wiki · Editando ${page.title}`);
    const draftName = `wiki.${isNew ? 'nueva' : page.id}`;
    const base = isNew ? { title: '', folder: ctx.query.get('carpeta') || '', body: '' } : { title: page.title, folder: page.folder, body: page.body };
    const st = editorState || { ...base, version: page?.version };
    const title = h('input', { maxlength: 200, value: st.title, placeholder: 'Título de la página' });
    const folder = h('input', { maxlength: 120, value: st.folder || '', list: 'wiki-folders', placeholder: 'Por ejemplo: Guías, Soluciones, Entornos' });
    const dl = h('datalist', { id: 'wiki-folders' }, folders().map(f => h('option', { value: f })));
    const proj = isNew ? selectEl([['', 'Ninguno (visible para todo el equipo)'], ...activeProjects().map(p => [p.id, `${p.key} · ${p.name}`])], '') : null;
    const body = h('textarea', { class: 'wiki-editor', rows: 22, placeholder: 'Escribe el contenido…' });
    body.value = st.body || '';
    let restored = false;
    const draft = readDraft(draftName);
    if (!editorState && draft != null && draft !== body.value) { body.value = draft; restored = true; }
    const preview = h('div', { class: 'card wiki-body hidden' });
    let previewOn = false;
    const segW = h('button', { class: 'on', type: 'button' }, 'Escribir');
    const segP = h('button', { type: 'button' }, 'Vista previa');
    const setPreview = (on) => {
      previewOn = on;
      segW.classList.toggle('on', !on); segP.classList.toggle('on', on);
      body.classList.toggle('hidden', on); preview.classList.toggle('hidden', !on);
      if (on) mount(preview, renderMarkdown(body.value));
    };
    segW.addEventListener('click', () => setPreview(false));
    segP.addEventListener('click', () => setPreview(true));
    body.addEventListener('input', () => writeDraft(draftName, body.value));
    const warn = h('div');
    const saveBtn = h('button', { class: 'btn primary' }, icon('check'), isNew ? 'Crear página' : 'Guardar cambios');
    const cancel = async () => {
      const changed = body.value !== base.body || title.value !== base.title || folder.value !== (base.folder || '');
      if (changed && !(await confirmDialog({ title: 'Descartar cambios', danger: true, confirmLabel: 'Descartar', message: 'Se perderán los cambios que no has guardado.' }))) return;
      writeDraft(draftName, null);
      editorState = null;
      if (isNew) { mode = 'home'; if (ctx.query.get('nueva')) location.hash = '#/wiki'; else drawHome(); }
      else { mode = 'view'; drawView(); }
    };
    const save = async () => {
      if (!title.value.trim()) { toast('El título es obligatorio.', 'error'); title.focus(); return; }
      const data = { title: title.value.trim(), folder: folder.value.trim(), body: body.value };
      try {
        if (isNew) {
          const r = await busy(saveBtn, () => api.post('/api/wiki', { uid: uid(), ...data, project_id: proj.value ? Number(proj.value) : null }, { queue: true }));
          writeDraft(draftName, null);
          if (r._queued) { toast('Sin conexión: la página se creará al reconectar.', 'warn'); mode = 'home'; location.hash = '#/wiki'; return; }
          toast('Página creada.', 'ok');
          location.hash = `#/wiki/${r.id}`;
        } else {
          const r = await busy(saveBtn, () => api.patch(`/api/wiki/${page.id}`, { ...data, version: st.version }));
          writeDraft(draftName, null);
          toast(r.unchanged ? 'No había cambios que guardar.' : 'Cambios guardados.', 'ok');
          editorState = null; mode = 'view';
          await refreshAll();
        }
      } catch (err) {
        if (err.status === 409 && !isNew) {
          const mine = data;
          editorState = null; mode = 'view';
          await refreshAll(conflictBox(mine));
        }
      }
    };
    saveBtn.addEventListener('click', save);
    const keys = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); } };
    mount(main,
      h('div', { class: 'page-head' }, h('h1', null, isNew ? 'Nueva página' : 'Editar página'),
        h('button', { class: 'btn', onclick: cancel }, 'Cancelar'), saveBtn),
      restored ? h('div', { class: 'callout info mb row' }, h('span', { class: 'grow' }, 'Se ha recuperado un borrador que no llegaste a guardar.'),
        h('button', { class: 'btn sm', onclick: () => { body.value = st.body || ''; writeDraft(draftName, null); toast('Borrador descartado.', 'ok'); } }, 'Descartar el borrador')) : null,
      warn,
      h('div', { class: 'card col gap-lg', onkeydown: keys },
        h('div', { class: 'form-grid' }, field('Título', title, { required: true, cls: isNew ? '' : 'span2' }),
          isNew ? field('Proyecto', proj, { hint: 'Si eliges un proyecto, solo lo verán sus miembros.' }) : null,
          field('Carpeta', folder, { cls: 'span2', hint: 'Agrupa las páginas en el árbol de la izquierda. Déjala vacía si no hace falta.' }), dl),
        h('div', { class: 'row between' }, h('div', { class: 'seg' }, segW, segP),
          h('span', { class: 'tiny muted' }, 'Formato: # Título · **negrita** · *cursiva* · `código` · - lista · 1. lista · > cita · [texto](https://…) · ``` bloque de código')),
        body, preview,
        h('div', { class: 'tiny muted' }, 'Ctrl+S guarda. Mientras escribes se guarda un borrador en este equipo por si cierras la ventana.')));
    editorWarn = warn;
    (isNew ? title : body).focus();
    void previewOn;
  }
  let editorWarn = null;

  // ---------- carga y refresco ----------
  async function refreshAll(extra) {
    try {
      await loadList();
      if (pageId) await loadPage();
    } catch (err) {
      if (err.status === 404) { mount(main, emptyState('La página no existe', 'Puede que la hayan movido o que no tengas acceso.')); drawTree(); return; }
      mount(main, errorBox(err, () => refreshAll())); return;
    }
    drawTree();
    if (mode === 'view' && page) drawView(extra);
    else if (mode === 'home') drawHome();
    else if (mode === 'new' && !main.querySelector('.wiki-editor')) drawEditor();
    else if (mode === 'edit' && !main.querySelector('.wiki-editor')) drawEditor();
  }

  mount(main, loading());
  await refreshAll();

  ctx.onChanges(['wiki'], async () => {
    if (mode === 'edit' || mode === 'new') {
      try { await loadList(); drawTree(); } catch { return; }
      if (mode === 'edit' && pageId) {
        try {
          const fresh = (await api.get(`/api/wiki/${pageId}`)).item;
          if (fresh.version !== page.version && editorWarn) {
            mount(editorWarn, h('div', { class: 'callout warn mb' }, icon('alert', 14),
              ` ${fresh.updated_by_name || 'Otra persona'} acaba de guardar cambios en esta página. Si guardas ahora, se te avisará del conflicto y podrás recuperar tu texto.`));
          }
        } catch { /* sin conexión */ }
      }
      return;
    }
    refreshAll();
  });
}

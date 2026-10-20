// Registro de decisiones: lista con estado, ficha (contexto, alternativas, decisión y consecuencias),
// cambios de estado, sustitución por otra decisión, edición con control de versiones y comentarios.
import { api } from '../api.js';
import { DECISION_STATUSES, toOptions } from '../labels.js';
import { activeProjects, can, defaultProjectId, projectById, withProject } from '../state.js';
import {
  confirmDialog, debounce, emptyState, errorBox, field, fmtDate, fmtDateTime, h, icon, loading, menuButton, modal,
  mount, pageHead, richText, selectEl, table, toast, uid,
} from '../ui.js';
import { activityTimeline, commentsSection } from './shared.js';

const STATUS_CLS = { propuesta: 'warn', aceptada: 'ok', rechazada: 'danger', sustituida: 'neutral' };
const STATUS_HELP = {
  propuesta: 'Está en discusión: todavía no se ha decidido.',
  aceptada: 'Se ha decidido y hay que aplicarla.',
  rechazada: 'Se valoró y se descartó.',
  sustituida: 'Ya no se aplica: otra decisión posterior la reemplaza.',
};
const statusBadge = (s) => h('span', { class: `badge ${STATUS_CLS[s] || 'neutral'}` }, DECISION_STATUSES[s] || s);

export default async function render(ctx) {
  return ctx.params.key ? detail(ctx, ctx.params.key) : list(ctx);
}

// ---------- formulario (crear / editar) ----------
function decisionForm(ctx, item, onSaved) {
  const isNew = !item;
  const title = h('input', { maxlength: 200, value: item?.title || '', placeholder: 'Por ejemplo: Usar PostgreSQL para el servidor de producción' });
  const projects = activeProjects();
  const proj = isNew ? selectEl(projects.map(p => [p.id, `${p.key} · ${p.name}`]), defaultProjectId() || projects[0]?.id) : null;
  const status = selectEl(toOptions(DECISION_STATUSES).filter(([k]) => k !== 'sustituida' || item?.status === 'sustituida'), item?.status || 'propuesta');
  const context = h('textarea', { rows: 5, placeholder: '¿Qué problema o situación obliga a decidir? ¿Qué restricciones hay?' });
  const options = h('textarea', { rows: 5, placeholder: '- Opción A: ventajas / inconvenientes\n- Opción B: …' });
  const decision = h('textarea', { rows: 5, placeholder: 'Qué se decide y por qué.\n\nConsecuencias: qué cambia, riesgos, trabajo que genera…' });
  context.value = item?.context || ''; options.value = item?.options || ''; decision.value = item?.decision || '';
  if (isNew && !projects.length) { toast('No hay proyectos activos en los que registrar la decisión.', 'error'); return; }
  modal({
    title: isNew ? 'Nueva decisión' : `Editar ${item.key}`, wide: true,
    body: h('div', { class: 'form-grid' },
      field('Título', title, { required: true, cls: 'span2' }),
      isNew ? field('Proyecto', proj, { required: true }) : null,
      field('Estado', status, { hint: STATUS_HELP[status.value] }),
      field('Contexto', context, { cls: 'span2' }),
      field('Alternativas valoradas', options, { cls: 'span2' }),
      field('Decisión y consecuencias', decision, { cls: 'span2', hint: 'Explica qué se decide y qué consecuencias tiene (lo que cambia, riesgos, trabajo derivado).' })),
    actions: [{ label: 'Cancelar' }, { label: isNew ? 'Registrar' : 'Guardar', kind: 'primary', onClick: async () => {
      if (!title.value.trim()) { toast('El título es obligatorio.', 'error'); title.focus(); return false; }
      const data = { title: title.value.trim(), status: status.value, context: context.value.trim(), options: options.value.trim(), decision: decision.value.trim() };
      try {
        if (isNew) {
          const r = await api.post('/api/decisions', { uid: uid(), project_id: Number(proj.value), ...data }, { queue: true });
          if (r._queued) { toast('Sin conexión: la decisión se registrará al reconectar.', 'warn'); return; }
          toast(`Decisión ${r.key} registrada.`, 'ok');
          location.hash = `#/decisiones/${r.key}`;
        } else {
          const r = await api.patch(`/api/decisions/${item.id}`, { ...data, version: item.version });
          toast(r.unchanged ? 'No había cambios.' : 'Decisión actualizada.', 'ok');
          onSaved && onSaved();
        }
      } catch (err) {
        toast(err.message, 'error');
        if (err.status === 409) { onSaved && onSaved(); return true; }
        return false;
      }
    } }],
  });
}

// ---------- lista ----------
async function list(ctx) {
  const { root } = ctx;
  let status = ctx.query.get('status') || '';
  let q = ctx.query.get('q') || '';
  const search = h('input', { type: 'search', placeholder: 'Buscar por título, contexto o clave…', value: q });
  const chips = h('div', { class: 'row wrap' });
  const body = h('div');
  const drawChips = () => mount(chips, [['', 'Todas'], ...Object.entries(DECISION_STATUSES)].map(([k, label]) =>
    h('button', { class: 'chip' + (status === k ? ' on' : ''), onclick: () => { status = k; drawChips(); load(); } }, label)));
  const load = async () => {
    let d;
    try { d = await api.get(`/api/decisions?${withProject({ status, q: q.trim() })}`); } catch (err) { mount(body, errorBox(err, load)); return; }
    mount(body, d.items.length ? table([
      { label: 'Clave', cls: 'nowrap', render: r => h('b', { class: 'key' }, r.key) },
      { label: 'Decisión', cls: 'title-cell', render: r => h('div', null, r.title,
        r.status === 'sustituida' && r.superseded_by_key ? h('div', { class: 'sub' }, `Sustituida por ${r.superseded_by_key}`)
          : r.decision ? h('div', { class: 'sub ellipsis', style: { maxWidth: '560px' } }, r.decision.split('\n')[0]) : null) },
      { label: 'Estado', cls: 'nowrap', render: r => statusBadge(r.status) },
      { label: 'Proyecto', cls: 'nowrap hide-sm', render: r => r.project_key ? h('span', { class: 'badge neutral' }, r.project_key) : '—' },
      { label: 'Registrada por', cls: 'nowrap hide-sm', render: r => r.created_by_name || '—' },
      { label: 'Fecha', cls: 'nowrap', render: r => h('span', { title: r.decided_at ? 'Fecha de la decisión' : 'Fecha de registro' }, fmtDate(r.decided_at || r.created_at)) },
    ], d.items, { onRow: r => { location.hash = `#/decisiones/${r.key}`; }, rowClass: r => (r.status === 'sustituida' || r.status === 'rechazada') ? 'is-resolved' : '' })
      : emptyState(q || status ? 'Ninguna decisión coincide' : 'Aún no hay decisiones registradas',
        q || status ? 'Prueba a cambiar la búsqueda o el filtro.' : 'Registra aquí las decisiones importantes (técnicas, de producto, de proceso) para que nadie tenga que preguntar «¿por qué hicimos esto?».'));
  };
  search.addEventListener('input', debounce(() => { q = search.value; load(); }, 250));
  drawChips();
  mount(root,
    pageHead('Registro de decisiones', can('conocimiento.editar') ? h('button', { class: 'btn primary', onclick: () => decisionForm(ctx, null) }, icon('plus'), 'Nueva decisión') : null),
    h('div', { class: 'toolbar' }, h('div', { class: 'search' }, icon('search'), search), chips),
    body);
  mount(body, loading());
  await load();
  ctx.onChanges(['decision'], load);
}

// ---------- ficha ----------
async function detail(ctx, key) {
  const { root } = ctx;
  const canEdit = can('conocimiento.editar');
  let item = null;
  let all = [];
  let comments = null;

  const load = async () => {
    const [d, l] = await Promise.all([api.get(`/api/decisions/${encodeURIComponent(key)}`), api.get('/api/decisions').catch(() => ({ items: [] }))]);
    item = d.item; all = l.items;
  };

  const setStatus = async (s) => {
    if (s === item.status) return;
    if (s === 'sustituida') return supersede();
    try {
      await api.patch(`/api/decisions/${item.id}`, { status: s, version: item.version });
      toast(`Estado cambiado a «${DECISION_STATUSES[s]}».`, 'ok');
    } catch (err) { toast(err.message, 'error'); }
    draw();
  };

  const supersede = () => {
    const others = all.filter(d => d.id !== item.id);
    if (!others.length) { toast('No hay otras decisiones que puedan sustituir a esta. Registra primero la nueva decisión.', 'warn'); return; }
    const sel = selectEl([['', 'Elige la decisión nueva…'], ...others.map(d => [d.key, `${d.key} · ${d.title} (${DECISION_STATUSES[d.status]})`])], '');
    modal({ title: `Marcar ${item.key} como sustituida`,
      body: h('div', { class: 'col gap-lg' }, h('p', { class: 'small muted' }, 'La decisión quedará como «Sustituida» y enlazada a la que la reemplaza.'),
        field('Sustituida por', sel, { required: true })),
      actions: [{ label: 'Cancelar' }, { label: 'Marcar como sustituida', kind: 'primary', onClick: async () => {
        if (!sel.value) { toast('Elige la decisión que la sustituye.', 'error'); return false; }
        try {
          await api.patch(`/api/decisions/${item.id}`, { superseded_by: sel.value, version: item.version });
          toast(`${item.key} sustituida por ${sel.value}.`, 'ok');
          draw();
        } catch (err) { toast(err.message, 'error'); if (err.status === 409) { draw(); return true; } return false; }
      } }] });
  };

  const reopenSuperseded = async () => {
    const ok = await confirmDialog({ title: 'Volver a aplicar la decisión', confirmLabel: 'Volver a «Aceptada»',
      message: `${item.key} dejará de estar sustituida y pasará a «Aceptada».` });
    if (ok) setStatus('aceptada');
  };

  const section = (title, text, empty) => h('div', null, h('div', { class: 'section-title' }, title),
    text ? h('div', { class: 'text-block' }, richText(text)) : h('div', { class: 'small muted' }, empty));

  async function draw() {
    try { await load(); } catch (err) {
      if (err.status === 404) mount(root, emptyState('La decisión no existe', 'Puede que no tengas acceso a su proyecto.'), h('div', { class: 'row end' }, h('a', { class: 'btn', href: '#/decisiones' }, 'Volver al registro')));
      else mount(root, errorBox(err, draw));
      return;
    }
    ctx.setTitle(`${item.key} · ${item.title}`);
    const proj = projectById(item.project_id);
    const replaces = all.filter(d => d.superseded_by === item.id);
    const statusMenu = canEdit ? menuButton([icon('flag'), 'Cambiar estado'], Object.entries(DECISION_STATUSES).map(([k, label]) => ({
      label: k === item.status ? `${label} (actual)` : k === 'sustituida' ? 'Sustituida por otra…' : label, onClick: () => setStatus(k),
    })), { class: 'btn' }) : null;
    if (!comments) comments = commentsSection('decision', item.id, { canComment: canEdit, data: item });
    else comments.refresh(item);
    const top = root.scrollTop;
    mount(root,
      h('div', { class: 'small muted mb' }, h('a', { href: '#/decisiones' }, '← Registro de decisiones')),
      h('div', { class: 'detail-head' },
        h('div', { class: 'grow' }, h('div', { class: 'key' }, item.key), h('h1', null, item.title),
          h('div', { class: 'row mt' }, statusBadge(item.status), h('span', { class: 'small muted' }, STATUS_HELP[item.status] || ''))),
        h('div', { class: 'actions' }, statusMenu,
          canEdit ? h('button', { class: 'btn primary', onclick: () => decisionForm(ctx, item, draw) }, icon('edit'), 'Editar') : null)),
      item.status === 'sustituida' ? h('div', { class: 'callout warn mb row' }, icon('alert', 15),
        h('span', { class: 'grow' }, 'Esta decisión ya no se aplica. ', item.superseded_by_key
          ? ['La sustituye ', h('a', { href: `#/decisiones/${item.superseded_by_key}` }, item.superseded_by_key), '.'] : ''),
        canEdit ? h('button', { class: 'btn sm', onclick: reopenSuperseded }, 'Volver a aplicarla') : null) : null,
      h('div', { class: 'detail' },
        h('div', { class: 'col gap-lg' },
          section('Contexto', item.context, 'Sin contexto.'),
          section('Alternativas valoradas', item.options, 'No se han documentado alternativas.'),
          section('Decisión y consecuencias', item.decision, item.status === 'propuesta' ? 'Aún no se ha decidido.' : 'Sin texto.'),
          comments),
        h('div', { class: 'col gap-lg' },
          h('div', { class: 'card' }, h('dl', { class: 'kv' },
            h('dt', null, 'Estado'), h('dd', null, statusBadge(item.status)),
            h('dt', null, 'Proyecto'), h('dd', null, proj ? `${proj.key} · ${proj.name}` : item.project_key || '—'),
            h('dt', null, 'Registrada por'), h('dd', null, item.created_by_name || '—'),
            h('dt', null, 'Registrada el'), h('dd', null, fmtDateTime(item.created_at)),
            h('dt', null, 'Decidida el'), h('dd', null, item.decided_at ? fmtDateTime(item.decided_at) : '—'),
            h('dt', null, 'Última edición'), h('dd', null, fmtDateTime(item.updated_at)),
            item.superseded_by_key ? [h('dt', null, 'Sustituida por'), h('dd', null, h('a', { href: `#/decisiones/${item.superseded_by_key}` }, item.superseded_by_key))] : null,
            replaces.length ? [h('dt', null, 'Sustituye a'), h('dd', null, replaces.map((d, i) => [i ? ', ' : '', h('a', { href: `#/decisiones/${d.key}`, title: d.title }, d.key)]))] : null,
            item.source_message ? [h('dt', null, 'Origen'), h('dd', null, h('a', { href: `#/chat?m=${item.source_message.id}` }, 'Mensaje del chat'),
              h('div', { class: 'tiny muted ellipsis' }, item.source_message.title))] : null)),
          (item.links || []).filter(l => l.type !== 'message').length ? h('div', { class: 'card' }, h('h3', null, 'Relacionado'),
            h('div', { class: 'col' }, item.links.filter(l => l.type !== 'message').map(l => h('a', { class: 'row', href: l.type === 'decision' ? `#/decisiones/${l.key}` : l.type === 'task' ? `#/tareas/${l.key}` : `#/incidencias/${l.key}` },
              h('b', { class: 'small' }, l.key || ''), h('span', { class: 'ellipsis small' }, l.title))))) : null,
          h('div', { class: 'card' }, h('h3', null, 'Historial'), activityTimeline(item.history)))));
    root.scrollTop = top;
  }

  mount(root, loading());
  await draw();
  ctx.onChanges(['decision'], draw);
}

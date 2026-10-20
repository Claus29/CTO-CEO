// Publicaciones en tiendas (App Store, Google Play…): estado de cada envío, rechazos, despliegue y fechas.
import { api } from '../api.js';
import { can, withProject } from '../state.js';
import { STORE_STATUSES, STORES, toOptions } from '../labels.js';
import { emptyState, errorBox, field, fmtDate, fmtDateTime, h, icon, loading, modal, mount, pageHead, selectEl, table, toast, uid } from '../ui.js';
import { kindBadge, multiProject, projectKey, requireFields, submit } from './_plan_shared.js';

const ACTIVE = ['preparando', 'enviada', 'en_revision', 'rechazada', 'aprobada'];
const DEFAULT_STORE = { ios: 'app_store', android: 'google_play' };

export default async function render(ctx) {
  const { root } = ctx;
  const canEdit = can('calidad.editar');
  let subs = null, builds = [];
  const f = { store: '', status: 'activas' };
  const statsBox = h('div');
  const filtersBox = h('div', { class: 'filters' });
  const listBox = h('div', null, loading());

  async function load() {
    try {
      const [s, b] = await Promise.all([api.get(`/api/store-submissions?${withProject()}`), api.get(`/api/builds?${withProject()}`)]);
      if (!ctx.alive()) return;
      subs = s.items; builds = b.items;
    } catch (err) { mount(listBox, errorBox(err, load)); return; }
    draw();
  }

  function draw() {
    const count = (st) => subs.filter(s => s.status === st).length;
    mount(statsBox, h('div', { class: 'stats' },
      h('div', { class: 'stat' }, h('div', { class: 'n' }, count('enviada') + count('en_revision')), h('div', { class: 'l' }, 'Esperando revisión')),
      h('div', { class: `stat ${count('rechazada') ? 'alert' : ''}` }, h('div', { class: 'n' }, count('rechazada')), h('div', { class: 'l' }, 'Rechazadas')),
      h('div', { class: 'stat' }, h('div', { class: 'n' }, count('aprobada')), h('div', { class: 'l' }, 'Aprobadas sin publicar')),
      h('div', { class: 'stat' }, h('div', { class: 'n ok-text' }, count('publicada')), h('div', { class: 'l' }, 'Publicadas'))));
    const chip = (key, value, label) => h('button', { class: 'chip' + (f[key] === value ? ' on' : ''), onclick: () => { f[key] = value; draw(); } }, label);
    mount(filtersBox,
      h('div', { class: 'group' }, h('span', null, 'Tienda'), chip('store', '', 'Todas'), Object.entries(STORES).map(([k, l]) => chip('store', k, l))),
      h('div', { class: 'group' }, h('span', null, 'Estado'), chip('status', 'activas', 'En curso'), chip('status', '', 'Todos'),
        Object.entries(STORE_STATUSES).map(([k, l]) => chip('status', k, l))));
    if (!subs.length) {
      mount(listBox, emptyState('Aún no hay publicaciones', canEdit ? 'Cuando envíes una build a una tienda, regístrala con «Nueva publicación» para seguir su revisión.' : ''));
      return;
    }
    const list = subs.filter(s => (!f.store || s.store === f.store) &&
      (!f.status || (f.status === 'activas' ? ACTIVE.includes(s.status) : s.status === f.status)));
    mount(listBox, table([
      { label: 'Build', render: s => h('div', { class: 'title-cell' }, h('b', null, s.build_number),
        h('div', { class: 'sub' }, [multiProject() ? projectKey(s.project_id) : null, s.platform, s.version_name ? `versión ${s.version_name}` : null].filter(Boolean).join(' · '))) },
      { label: 'Tienda', render: s => s.store_label },
      { label: 'Estado', render: s => h('div', null, kindBadge('store', s.status, s.status_label),
        s.status === 'rechazada' && s.rejection_reason ? h('div', { class: 'tiny danger-text ellipsis rej', title: s.rejection_reason }, s.rejection_reason) : null) },
      { label: 'Despliegue', render: s => s.rollout_percent === null || s.rollout_percent === undefined ? h('span', { class: 'muted' }, '—') : `${s.rollout_percent}%`, cls: 'hide-sm' },
      { label: 'Enviada', render: s => fmtDate(s.submitted_at), cls: 'hide-sm' },
      { label: 'Decisión', render: s => fmtDate(s.decided_at), cls: 'hide-sm' },
      { label: 'Publicada', render: s => fmtDate(s.released_at) },
      { label: 'Tarea', render: s => s.task_key ? h('a', { href: `#/tareas/${s.task_key}`, onclick: (e) => e.stopPropagation() }, s.task_key) : h('span', { class: 'muted' }, '—') },
    ], list, { onRow: canEdit ? editForm : showDetail, rowClass: s => s.status === 'rechazada' ? 'is-blocked' : '', empty: 'Ninguna publicación coincide con los filtros.' }));
  }

  function detailKv(s) {
    return h('div', { class: 'kv' },
      h('span', null, 'Build'), h('span', null, `${s.build_number} · ${s.platform}${s.version_name ? ' · versión ' + s.version_name : ''}`),
      h('span', null, 'Tienda'), h('span', null, s.store_label),
      h('span', null, 'Estado'), h('span', null, kindBadge('store', s.status, s.status_label)),
      h('span', null, 'Enviada'), h('span', null, s.submitted_at ? fmtDateTime(s.submitted_at) : '—'),
      h('span', null, 'Aprobada / rechazada'), h('span', null, s.decided_at ? fmtDateTime(s.decided_at) : '—'),
      h('span', null, 'Publicada'), h('span', null, s.released_at ? fmtDateTime(s.released_at) : '—'),
      h('span', null, 'Registrada por'), h('span', null, `${s.created_by_name || '—'} · ${fmtDate(s.created_at)}`),
      s.task_key ? [h('span', null, 'Tarea de corrección'), h('a', { href: `#/tareas/${s.task_key}` }, s.task_key)] : null);
  }

  function showDetail(s) {
    modal({ title: `${s.store_label}: build ${s.build_number}`, body: h('div', { class: 'col' }, detailKv(s),
      s.rejection_reason ? h('div', { class: 'callout danger' }, h('b', null, 'Motivo del rechazo: '), s.rejection_reason) : null,
      s.notes ? h('div', { class: 'text-block' }, s.notes) : null), actions: [{ label: 'Cerrar', kind: 'primary' }] });
  }

  function editForm(s) {
    const status = selectEl(toOptions(STORE_STATUSES), s.status);
    const reason = h('textarea', { rows: 4, placeholder: 'Copia aquí el motivo que indica la tienda' });
    reason.value = s.rejection_reason || '';
    const reasonField = field('Motivo del rechazo', reason, { required: true, cls: 'span2', hint: 'Al guardar el rechazo se crea automáticamente una tarea de prioridad alta para corregirlo y se avisa al equipo de calidad.' });
    const rollout = h('input', { type: 'number', min: 0, max: 100, step: 1, placeholder: 'Ej.: 20', value: s.rollout_percent ?? '' });
    const notes = h('textarea', { rows: 3 });
    notes.value = s.notes || '';
    const sync = () => reasonField.classList.toggle('hidden', !(status.value === 'rechazada' && s.status !== 'rechazada'));
    status.addEventListener('change', sync);
    sync();
    modal({ title: `${s.store_label}: build ${s.build_number}`, wide: true,
      body: h('div', { class: 'col gap-lg' }, detailKv(s),
        s.status === 'rechazada' && s.rejection_reason ? h('div', { class: 'callout danger' }, h('b', null, 'Motivo del rechazo: '), s.rejection_reason) : null,
        h('div', { class: 'form-grid' },
          field('Estado', status), field('Despliegue progresivo (%)', rollout, { hint: 'Porcentaje de usuarios que reciben la versión (opcional).' }),
          reasonField, field('Notas', notes, { cls: 'span2' }))),
      actions: [{ label: 'Cancelar' }, { label: 'Guardar', kind: 'primary', onClick: () => {
        const data = { version: s.version };
        if (status.value !== s.status) {
          data.status = status.value;
          if (status.value === 'rechazada') {
            if (!requireFields([[reason, 'el motivo del rechazo']])) return false;
            data.rejection_reason = reason.value.trim();
          }
        }
        const roll = rollout.value === '' ? null : Number(rollout.value);
        if (roll !== null && (!Number.isInteger(roll) || roll < 0 || roll > 100)) { toast('El despliegue debe ser un número entero entre 0 y 100.', 'error'); rollout.focus(); return false; }
        if (roll !== (s.rollout_percent ?? null)) data.rollout_percent = roll;
        if (notes.value.trim() !== (s.notes || '')) data.notes = notes.value.trim();
        if (Object.keys(data).length === 1) return true;
        const msg = data.status === 'rechazada' ? 'Rechazo registrado: se ha creado una tarea para corregirlo.' : 'Publicación actualizada.';
        return submit(() => api.patch(`/api/store-submissions/${s.id}`, data), msg, load);
      } }] });
  }

  function newForm(buildId) {
    if (!builds.length) {
      modal({ title: 'Nueva publicación', body: h('p', null, 'Primero registra la build en «Builds».'),
        actions: [{ label: 'Cancelar' }, can('calidad.editar') ? { label: 'Ir a builds', kind: 'primary', onClick: () => { location.hash = '#/builds'; } } : null].filter(Boolean) });
      return;
    }
    const buildSel = selectEl([['', 'Elige la build…'], ...builds.map(b => [String(b.id),
      `${multiProject() ? b.project_key + ' · ' : ''}${b.number} · ${b.platform_label}${b.version_name ? ' · ' + b.version_name : ''} · ${b.channel_label}`])], buildId ? String(buildId) : '');
    const storeSel = selectEl(toOptions(STORES, 'Elige la tienda…'), '');
    const pickStore = () => { const b = builds.find(x => String(x.id) === buildSel.value); if (b && DEFAULT_STORE[b.platform]) storeSel.value = DEFAULT_STORE[b.platform]; };
    buildSel.addEventListener('change', pickStore);
    pickStore();
    const notes = h('textarea', { rows: 3, placeholder: 'Notas para la revisión, cuenta de prueba para los revisores, novedades…' });
    modal({ title: 'Nueva publicación en tienda', wide: true,
      body: h('div', { class: 'form-grid' }, field('Build', buildSel, { required: true }), field('Tienda', storeSel, { required: true }),
        field('Notas', notes, { cls: 'span2' }),
        h('div', { class: 'callout info small span2' }, 'Empieza en «Preparando». Cambia el estado cuando la envíes, cuando la tienda la apruebe o la rechace y cuando se publique: las fechas se guardan solas.')),
      actions: [{ label: 'Cancelar' }, { label: 'Crear', kind: 'primary', onClick: () => {
        if (!requireFields([[buildSel, 'la build'], [storeSel, 'la tienda']])) return false;
        return submit(() => api.post('/api/store-submissions', { uid: uid(), build_id: Number(buildSel.value), store: storeSel.value, notes: notes.value.trim() }, { queue: true }),
          'Publicación registrada.', load);
      } }] });
  }

  mount(root,
    pageHead('Publicaciones en tiendas', canEdit ? h('button', { class: 'btn primary', onclick: () => newForm(null) }, icon('plus'), 'Nueva publicación') : null),
    statsBox, filtersBox, listBox);
  await load();
  const pre = Number(ctx.query.get('build'));
  if (pre && canEdit && subs) { history.replaceState(null, '', '#/tiendas'); newForm(pre); }
  ctx.onChanges(['store', 'build', 'task'], load);
}

// Renovaciones: certificados, dominios, cuentas de desarrollador, suscripciones… con días restantes y avisos.
import { api } from '../api.js';
import { can, projectById, store } from '../state.js';
import {
  confirmDialog, emptyState, errorBox, field, fmtDate, fmtRelative, h, icon, loading, modal, mount, pageHead,
  selectEl, table, toast, todayISO, uid,
} from '../ui.js';
import { addMonthsISO, multiProject, projectField, requireFields, submit } from './_plan_shared.js';

const SUGGESTIONS = ['Certificado SSL de ', 'Dominio ', 'Cuenta de desarrollador de Apple', 'Cuenta de desarrollador de Google Play',
  'Suscripción a ', 'Licencia de ', 'Certificado de firma de código', 'Certificado push (APNs)', 'Alojamiento / servidor '];

export function urgency(days) {
  if (days < 0) return { cls: 'danger', row: 'rn-expired', text: `Caducó hace ${-days} ${-days === 1 ? 'día' : 'días'}` };
  if (days === 0) return { cls: 'danger', row: 'rn-expired', text: 'Caduca hoy' };
  if (days <= 14) return { cls: 'danger', row: 'rn-soon', text: `Quedan ${days} ${days === 1 ? 'día' : 'días'}` };
  if (days <= 30) return { cls: 'warn', row: '', text: `Quedan ${days} días` };
  if (days <= 60) return { cls: 'info', row: '', text: `Quedan ${days} días` };
  return { cls: 'ok', row: '', text: `Quedan ${days} días` };
}

export default async function render(ctx) {
  const { root } = ctx;
  const canEdit = can('planificacion.editar');
  let items = null;
  const statsBox = h('div');
  const listBox = h('div', null, loading());

  async function load() {
    try {
      const r = await api.get('/api/renewals');
      if (!ctx.alive()) return;
      items = r.items;
    } catch (err) { mount(listBox, errorBox(err, load)); return; }
    draw();
  }

  function draw() {
    const pid = store.project === 'all' ? null : Number(store.project);
    const list = items.filter(r => !pid || !r.project_id || r.project_id === pid);
    const expired = list.filter(r => r.days_left < 0).length;
    const soon = list.filter(r => r.days_left >= 0 && r.days_left <= 30).length;
    mount(statsBox, h('div', { class: 'stats' },
      h('div', { class: `stat ${expired ? 'alert' : ''}` }, h('div', { class: 'n' }, expired), h('div', { class: 'l' }, 'Caducadas')),
      h('div', { class: `stat ${soon ? 'warn' : ''}` }, h('div', { class: 'n' }, soon), h('div', { class: 'l' }, 'Caducan en 30 días')),
      h('div', { class: 'stat' }, h('div', { class: 'n' }, list.length), h('div', { class: 'l' }, 'En seguimiento'))));
    if (!list.length) {
      mount(listBox, emptyState('No hay renovaciones registradas', canEdit
        ? 'Añade certificados, dominios, cuentas de desarrollador o suscripciones para que nada caduque por sorpresa.' : ''));
      return;
    }
    const cols = [
      { label: 'Qué', render: r => h('div', { class: 'title-cell' }, r.title, r.notes ? h('div', { class: 'sub ellipsis rn-notes' }, r.notes) : null) },
      multiProject() ? { label: 'Proyecto', render: r => r.project_id ? projectById(r.project_id)?.key || '—' : 'Todos', cls: 'hide-sm' } : null,
      { label: 'Responsable', render: r => r.owner_name || '—', cls: 'hide-sm' },
      { label: 'Caduca', render: r => h('span', { class: 'nowrap' }, fmtDate(r.expires_on)) },
      { label: 'Tiempo restante', render: r => { const u = urgency(r.days_left); return h('span', { class: `badge ${u.cls}` }, u.text); } },
      { label: 'Última renovación', render: r => r.renewed_at ? h('span', { class: 'small', title: fmtDate(r.renewed_at) }, fmtRelative(r.renewed_at)) : h('span', { class: 'muted' }, '—'), cls: 'hide-sm' },
      canEdit ? { label: '', cls: 'actions-cell', render: r => h('div', { class: 'row end', onclick: (e) => e.stopPropagation() },
        h('button', { class: 'btn sm', title: 'Marcar como renovada e indicar la nueva fecha de caducidad', onclick: () => renew(r) }, icon('repeat'), 'Renovar'),
        h('button', { class: 'btn ghost sm icon', title: 'Editar', onclick: () => form(r) }, icon('edit')),
        h('button', { class: 'btn ghost sm icon', title: 'Borrar', onclick: () => remove(r) }, icon('trash'))) } : null,
    ].filter(Boolean);
    mount(listBox, table(cols, list, { rowClass: r => urgency(r.days_left).row, onRow: canEdit ? (r) => form(r) : null }));
  }

  function renew(r) {
    const base = r.expires_on > todayISO() ? r.expires_on : todayISO();
    const date = h('input', { type: 'date', value: addMonthsISO(base, 12) });
    const quick = (label, months) => h('button', { class: 'chip', type: 'button', onclick: () => { date.value = addMonthsISO(base, months); } }, label);
    modal({ title: `Renovar «${r.title}»`,
      body: h('div', { class: 'col' },
        h('p', { class: 'small muted' }, `Caducidad actual: ${fmtDate(r.expires_on)}. Indica la nueva fecha de caducidad tras renovarla.`),
        field('Nueva fecha de caducidad', date, { required: true }),
        h('div', { class: 'row wrap' }, quick('+1 mes', 1), quick('+3 meses', 3), quick('+6 meses', 6), quick('+1 año', 12), quick('+2 años', 24))),
      actions: [{ label: 'Cancelar' }, { label: 'Marcar como renovada', kind: 'primary', onClick: () => {
        if (!requireFields([[date, 'la nueva fecha']])) return false;
        if (date.value <= r.expires_on) { toast('La nueva fecha de caducidad debe ser posterior a la actual.', 'error'); date.focus(); return false; }
        return submit(() => api.patch(`/api/renewals/${r.id}`, { expires_on: date.value, renewed: true }), 'Renovación registrada.', load);
      } }] });
  }

  async function remove(r) {
    if (!await confirmDialog({ title: 'Borrar renovación', message: `¿Dejar de hacer seguimiento de «${r.title}»?`, confirmLabel: 'Borrar', danger: true })) return;
    try { await api.del(`/api/renewals/${r.id}`); toast('Renovación borrada.', 'ok'); load(); } catch (err) { toast(err.message, 'error'); }
  }

  function form(r) {
    const editing = !!r;
    const title = h('input', { maxlength: 200, list: 'rn-suggest', placeholder: 'Ej.: Certificado SSL de api.miapp.com', value: r?.title || '' });
    const dl = h('datalist', { id: 'rn-suggest' }, SUGGESTIONS.map(s => h('option', { value: s })));
    const exp = h('input', { type: 'date', value: r?.expires_on || '' });
    const owner = selectEl([['', 'Sin responsable'], ...store.users.filter(u => u.active !== false).map(u => [String(u.id), u.display_name])], r?.owner_id ? String(r.owner_id) : '');
    const notes = h('textarea', { rows: 3, placeholder: 'Dónde se renueva, quién paga, coste, cuenta asociada…' });
    notes.value = r?.notes || '';
    let pid = store.project === 'all' ? '' : String(store.project);
    const proj = editing ? null : projectField(pid, (v) => { pid = v; }, { optional: true });
    if (proj && !multiProject()) proj.el.classList.add('hidden');
    modal({ title: editing ? 'Editar renovación' : 'Nueva renovación', wide: true,
      body: h('div', { class: 'form-grid' },
        field('Qué hay que renovar', title, { required: true, cls: 'span2' }), dl,
        field('Fecha de caducidad', exp, { required: true, hint: 'Avisaremos al acercarse la fecha.' }),
        field('Responsable', owner),
        proj ? proj.el : null,
        field('Notas', notes, { cls: 'span2' })),
      actions: [{ label: 'Cancelar' }, { label: editing ? 'Guardar cambios' : 'Añadir', kind: 'primary', onClick: () => {
        if (!requireFields([[title, 'qué hay que renovar'], [exp, 'la fecha de caducidad']])) return false;
        const data = { title: title.value.trim(), expires_on: exp.value, owner_id: owner.value ? Number(owner.value) : null, notes: notes.value.trim() };
        if (editing) {
          const changed = {};
          for (const [k, v] of Object.entries(data)) if ((r[k] ?? null) !== v && !(k === 'notes' && (r[k] || '') === v)) changed[k] = v;
          if (!Object.keys(changed).length) return true;
          return submit(() => api.patch(`/api/renewals/${r.id}`, changed), 'Renovación actualizada.', load);
        }
        return submit(() => api.post('/api/renewals', { ...data, uid: uid(), project_id: pid ? Number(pid) : null }, { queue: true }), 'Renovación añadida.', load);
      } }] });
  }

  mount(root,
    pageHead('Renovaciones', canEdit ? h('button', { class: 'btn primary', onclick: () => form(null) }, icon('plus'), 'Nueva renovación') : null),
    h('p', { class: 'muted small mb' }, 'Todo lo que caduca y hay que renovar a tiempo: certificados, dominios, cuentas de desarrollador, suscripciones, licencias…'),
    statsBox, listBox);
  await load();
  ctx.onChanges(['renewal'], load);
}

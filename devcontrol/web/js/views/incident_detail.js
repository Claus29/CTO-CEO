// Ficha de incidencia: estado y acciones, datos del reporte, SLA, adjuntos, comentarios,
// dependencias, tiempo, resoluciones, commits/PR enlazados y quién la ha visto.
import { api } from '../api.js';
import { BLOCK_REASONS, CATEGORIES, ENVIRONMENTS, FREQUENCIES, PRIORITIES, REPORT_FIELDS } from '../labels.js';
import { can, projectById, store, userById } from '../state.js';
import {
  avatar, busy, confirmDialog, debounce, emptyState, errorBox, field, fmtDate, fmtDateTime, fmtDuration, fmtHours, fmtRelative,
  h, icon, loading, menuButton, modal, mount, priorityBadge, promptDialog, richText, selectEl, statusBadge, tabs, toast, uid,
} from '../ui.js';
import {
  activityTimeline, attachmentsSection, codeLinksList, commentsSection, followButton, dueText, entityPicker, relationsSection, resolutionHistory,
  timeWidget, userSelect, verificationPanel,
} from './shared.js';

const OPEN = ['pendiente', 'en_progreso', 'bloqueada', 'en_verificacion'];
const valueLabel = (k, v) => (k === 'environment' ? ENVIRONMENTS[v] : k === 'frequency' ? FREQUENCIES[v] : v);

export default async function render(ctx) {
  const { root } = ctx;
  const ident = ctx.params.key;
  let item = null;
  let seenSent = false;
  let builds = null;
  let tab = 'comments';

  const headBox = h('div');
  const alertsBox = h('div', { class: 'col' });
  const infoBox = h('div', { class: 'col gap-lg' });
  const sideInfo = h('div', { class: 'card' });
  const slaBox = h('div');
  const codeBox = h('div', { class: 'card' });
  const viewsBox = h('div', { class: 'card' });
  const tabsBar = h('div');
  const historyBox = h('div', { class: 'hidden' });
  const resBox = h('div', { class: 'hidden' });
  let sec = null; // secciones compartidas (se crean una vez y se refrescan con los datos nuevos)

  const me = () => store.me.id;
  const p = {
    work: can('incidencias.trabajar'), resolve: can('incidencias.resolver'), reopen: can('incidencias.reabrir'),
    assign: can('incidencias.asignar'), wiki: can('conocimiento.editar'),
  };
  const canEdit = () => can('incidencias.editar') || item.created_by === me();
  const canBuilds = () => can('calidad.ver') && (can('incidencias.editar') || p.work);
  const path = (suffix = '') => `/api/incidents/${encodeURIComponent(item.key)}${suffix}`;

  // ---------- acciones ----------
  async function act(btn, fn, okMsg) {
    try {
      const r = await busy(btn, fn, okMsg);
      await reload();
      return r;
    } catch (err) {
      if (err.status === 409) await reload();
      return null;
    }
  }
  const setStatus = (btn, status, extra = {}, okMsg) =>
    act(btn, () => api.post(path('/status'), { status, version: item.version, ...extra }), okMsg);
  const patch = (fields, okMsg, btn) => act(btn, () => api.patch(path(), { ...fields, version: item.version }), okMsg);

  function blockDialog() {
    const reason = selectEl(Object.entries(BLOCK_REASONS), item.block_reason_code || 'falta_informacion');
    const text = h('textarea', { rows: 3, placeholder: 'Qué hace falta para poder seguir y quién puede desbloquearlo' });
    text.value = item.block_reason_text || '';
    modal({
      title: item.status === 'bloqueada' ? `Actualizar el bloqueo de ${item.key}` : `Bloquear ${item.key}`,
      body: h('div', { class: 'col gap-lg' }, h('div', { class: 'small muted' }, 'Se avisará con urgencia a quien la creó y a quien puede verificar, para que ayuden a desbloquearla.'),
        field('Motivo', reason, { required: true }), field('Explicación', text, { hint: 'Obligatoria si el motivo es «Otro motivo».' })),
      actions: [{ label: 'Cancelar' }, { label: item.status === 'bloqueada' ? 'Actualizar' : 'Bloquear', kind: 'danger', onClick: async () => {
        if (reason.value === 'otro' && !text.value.trim()) { toast('Explica el motivo del bloqueo.', 'error'); return false; }
        const r = await setStatus(null, 'bloqueada', { block_reason_code: reason.value, block_reason_text: text.value.trim() }, `${item.key} bloqueada.`);
        return r ? undefined : false;
      } }],
    });
  }

  function resolveDialog() {
    const solution = h('textarea', { rows: 5, placeholder: 'Qué se ha cambiado para arreglarlo (ayuda a verificarlo y a que no vuelva a pasar)' });
    const closing = h('textarea', { rows: 2, placeholder: 'Opcional: nota para quien lo verifique (p. ej. «probado en iOS 17 y Android 14»)' });
    const needs = store.settings.verification_required !== false && item.created_by !== me();
    modal({
      title: `Resolver ${item.key}`,
      body: h('div', { class: 'col gap-lg' },
        h('div', { class: `callout ${needs ? 'info' : 'ok'} small` }, needs
          ? `Pasará a «En verificación»: ${item.created_by_name || 'quien la creó'} comprobará que está arreglada antes de cerrarla.`
          : 'Se cerrará directamente como resuelta.'),
        field('Solución aplicada', solution, { hint: 'Recomendado.' }), field('Comentario de cierre', closing)),
      actions: [{ label: 'Cancelar' }, { label: needs ? 'Enviar a verificación' : 'Marcar como resuelta', kind: 'ok', onClick: async () => {
        if (!solution.value.trim() && !await confirmDialog({ title: 'Sin solución documentada', message: '¿Seguro que quieres resolverla sin explicar la solución? Será más difícil verificarla.', confirmLabel: 'Resolver igualmente' })) return false;
        const r = await setStatus(null, 'resuelta', { solution: solution.value.trim(), closing_comment: closing.value.trim() },
          needs ? `${item.key} enviada a verificación.` : `${item.key} resuelta.`);
        return r ? undefined : false;
      } }],
    });
  }

  function reopenDialog() {
    const reason = h('textarea', { rows: 3, placeholder: 'Qué sigue fallando o por qué hay que reabrirla' });
    const to = selectEl([['pendiente', 'Pendiente'], ...(p.work ? [['en_progreso', 'En progreso (me pongo con ella)']] : [])], 'pendiente');
    modal({
      title: `Reabrir ${item.key}`,
      body: h('div', { class: 'col gap-lg' }, field('Motivo de la reapertura', reason, { required: true }), field('Pasa a', to)),
      actions: [{ label: 'Cancelar' }, { label: 'Reabrir', kind: 'primary', onClick: async () => {
        if (!reason.value.trim()) { toast('Indica el motivo de la reapertura.', 'error'); return false; }
        const r = await setStatus(null, to.value, { reopen_reason: reason.value.trim() }, `${item.key} reabierta.`);
        return r ? undefined : false;
      } }],
    });
  }

  async function duplicateDialog() {
    const target = await entityPicker({ title: `Marcar ${item.key} como duplicada de…`, types: ['incident'], exclude: [{ type: 'incident', id: item.id, key: item.key }],
      hint: 'Elige la incidencia original. Esta quedará cerrada como duplicada y se avisará a quien la creó.' });
    if (!target) return;
    if (!await confirmDialog({ title: 'Marcar como duplicada', message: `${item.key} se cerrará como duplicada de ${target.key} («${target.title}»).`, confirmLabel: 'Marcar como duplicada' })) return;
    act(null, () => api.post(path('/duplicate'), { of: target.key || String(target.id) }), `${item.key} marcada como duplicada de ${target.key}.`);
  }

  async function toWiki() {
    const cause = await promptDialog({ title: 'Pasar la solución a la wiki', label: 'Causa del problema (opcional, se puede completar después)', multiline: true, required: false, confirmLabel: 'Crear artículo' });
    if (cause === null) return;
    try {
      const r = await busy(null, () => api.post(path('/to-wiki'), { uid: uid(), cause }), 'Artículo creado en la wiki (carpeta «Soluciones»).');
      location.hash = `#/wiki/${r.id}`;
    } catch { /* aviso */ }
  }

  function assignDialog() {
    const sel = userSelect(item.assignee_id || '', { projectId: item.project_id });
    modal({ title: `Asignar ${item.key}`, body: field('Responsable', sel), actions: [{ label: 'Cancelar' }, { label: 'Asignar', kind: 'primary', onClick: async () => {
      const r = await patch({ assignee_id: sel.value ? Number(sel.value) : null }, 'Responsable actualizado.');
      return r ? undefined : false;
    } }] });
  }

  function editDialog() {
    const title = h('input', { value: item.title, maxlength: 200 });
    const desc = h('textarea', { rows: 6 }); desc.value = item.description || '';
    const prio = selectEl(Object.entries(PRIORITIES), item.priority);
    const cat = selectEl(Object.entries(CATEGORIES), item.category);
    const target = h('input', { type: 'date', value: item.target_date || '' });
    const labels = h('input', { value: (item.labels_list || []).join(', ') });
    const est = h('input', { type: 'number', min: 0, step: 0.5, value: item.estimate_hours ?? '' });
    const rep = {};
    for (const k of Object.keys(REPORT_FIELDS)) {
      if (k === 'environment') rep[k] = selectEl([['', '—'], ...Object.entries(ENVIRONMENTS)], item[k] || '');
      else if (k === 'frequency') rep[k] = selectEl([['', '—'], ...Object.entries(FREQUENCIES)], item[k] || '');
      else if (['steps', 'expected', 'observed'].includes(k)) { rep[k] = h('textarea', { rows: 3 }); rep[k].value = item[k] || ''; }
      else rep[k] = h('input', { value: item[k] || '' });
    }
    modal({
      title: `Editar ${item.key}`, wide: true,
      body: h('div', { class: 'form-grid' },
        field('Título', title, { required: true, cls: 'span2' }), field('Descripción', desc, { cls: 'span2' }),
        field('Prioridad', prio), field('Categoría', cat), field('Fecha objetivo', target), field('Estimación (horas)', est),
        field('Etiquetas', labels, { cls: 'span2', hint: 'Separadas por comas.' }),
        Object.keys(REPORT_FIELDS).map(k => field(REPORT_FIELDS[k], rep[k], { cls: ['steps', 'expected', 'observed'].includes(k) ? 'span2' : '',
          hint: item.na_fields?.[k] ? `Marcado como no aplicable: ${item.na_fields[k]}` : '' }))),
      actions: [{ label: 'Cancelar' }, { label: 'Guardar cambios', kind: 'primary', onClick: async () => {
        if (!title.value.trim()) { toast('El título es obligatorio.', 'error'); return false; }
        const next = { title: title.value.trim(), description: desc.value.trim(), priority: prio.value, category: cat.value,
          target_date: target.value || null, labels: labels.value.split(',').map(x => x.trim().toLowerCase()).filter(Boolean).join(','),
          estimate_hours: est.value === '' ? null : Number(est.value) };
        for (const k of Object.keys(REPORT_FIELDS)) next[k] = rep[k].value.trim();
        const changes = {};
        for (const [k, v] of Object.entries(next)) {
          const cur = k === 'labels' ? (item.labels || '') : (item[k] ?? (k === 'target_date' || k === 'estimate_hours' ? null : ''));
          if (String(v ?? '') !== String(cur ?? '')) changes[k] = v;
        }
        const na = { ...(item.na_fields || {}) };
        let naChanged = false;
        for (const k of Object.keys(na)) if (next[k]) { delete na[k]; naChanged = true; }
        if (naChanged) changes.na_fields = na;
        if (!Object.keys(changes).length) return undefined;
        const r = await patch(changes, 'Cambios guardados.');
        return r ? undefined : false;
      } }],
    });
  }

  // ---------- dibujo ----------
  function drawHead() {
    const st = item.status;
    const open = OPEN.includes(st);
    const btn = (label, ic, kind, onClick, title) => { const b = h('button', { class: `btn ${kind || ''}`, title }, ic ? icon(ic) : null, label); b.addEventListener('click', () => onClick(b)); return b; };
    const actions = [];
    if (open && st !== 'en_verificacion') {
      if (p.work && st !== 'en_progreso') actions.push(btn(st === 'bloqueada' ? 'Quitar bloqueo' : 'Empezar', 'play', st === 'bloqueada' ? '' : 'primary',
        (b) => setStatus(b, 'en_progreso', {}, st === 'bloqueada' ? 'Bloqueo quitado: vuelve a «En progreso».' : `${item.key} en progreso.`)));
      if (p.resolve) actions.push(btn('Resolver…', 'check', 'ok', resolveDialog));
      if (p.work) actions.push(btn(st === 'bloqueada' ? 'Actualizar bloqueo…' : 'Bloquear…', 'lock', '', blockDialog));
    }
    if (!open && p.reopen) actions.push(btn('Reabrir…', 'undo', '', reopenDialog));
    const more = menuButton(h('span', { class: 'row', style: { gap: '6px' } }, icon('dots', 15), 'Más'), [
      { label: 'Editar campos', icon: 'edit', onClick: editDialog, hidden: !canEdit() },
      { label: 'Asignar…', icon: 'user', onClick: assignDialog, hidden: !p.assign || !open },
      { label: 'Asignármela', icon: 'user', onClick: () => patch({ assignee_id: me() }, `Te has asignado ${item.key}.`), hidden: p.assign || !p.work || item.assignee_id === me() || !open },
      { label: 'Volver a pendiente', icon: 'undo', onClick: () => setStatus(null, 'pendiente', {}, `${item.key} vuelve a «Pendiente».`), hidden: !p.work || !['en_progreso', 'bloqueada'].includes(st) },
      { label: 'Marcar como duplicada de…', icon: 'copy', onClick: duplicateDialog, hidden: !open || st === 'en_verificacion' || !(p.work || can('incidencias.editar')) },
      { label: 'Pasar la solución a la wiki', icon: 'book', onClick: toWiki, hidden: !p.wiki || st !== 'resuelta' },
      { label: 'Copiar la clave', icon: 'copy', onClick: () => navigator.clipboard?.writeText(item.key).then(() => toast(`${item.key} copiada.`, 'ok')).catch(() => toast(item.key, 'info')) },
    ], { class: 'btn' });
    const s = item.sla;
    mount(headBox, h('div', { class: 'detail-head' },
      h('div', { class: 'grow' },
        h('div', { class: 'row' }, h('a', { class: 'small', href: '#/incidencias', title: 'Volver a la lista' }, icon('left', 14)),
          h('span', { class: 'key' }, item.key), h('span', { class: 'project-dot', style: { background: item.project_color || 'var(--muted)' } }),
          h('span', { class: 'small muted' }, item.project_name || projectById(item.project_id)?.name || '')),
        h('h1', null, item.title),
        h('div', { class: 'ws-badges' }, priorityBadge(item.priority, item.priority_label), statusBadge(st, item.status_label),
          h('span', { class: 'badge neutral' }, item.category_label),
          s ? h('span', { class: `badge ${s.state === 'vencido' ? 'danger' : s.state === 'riesgo' ? 'warn' : 'ok'}` }, `SLA: ${s.state === 'vencido' ? 'vencido' : s.state === 'riesgo' ? 'en riesgo' : 'en plazo'}`) : null,
          item.overdue ? h('span', { class: 'badge danger' }, 'Vencida') : null,
          item.reopen_count ? h('span', { class: 'badge warn' }, `Reabierta ${item.reopen_count} ${item.reopen_count === 1 ? 'vez' : 'veces'}`) : null,
          (item.labels_list || []).map(l => h('a', { class: 'lbl-chip', href: `#/incidencias?label=${encodeURIComponent(l)}` }, l)))),
      h('div', { class: 'actions' }, followButton('incident', item.id), actions, more)));
  }

  function drawAlerts() {
    const out = [];
    if (item.status === 'bloqueada') {
      out.push(h('div', { class: 'block-callout' },
        h('div', { class: 't' }, icon('lock', 16), `Bloqueada: ${item.block_reason_label || 'sin motivo'}`),
        item.block_reason_text ? h('div', { class: 'pre mt', style: { marginTop: '6px' } }, richText(item.block_reason_text)) : null,
        h('div', { class: 'tiny muted', style: { marginTop: '6px' } }, `${item.blocked_by_name || ''} · ${fmtRelative(item.blocked_at)}`)));
    }
    const lastRes = (item.resolutions || []).at(-1);
    if (item.status === 'en_progreso' && lastRes?.state === 'rechazada') {
      out.push(h('div', { class: 'callout danger' }, h('span', { class: 'lbl' }, 'Se rechazó la última resolución'),
        ` (${lastRes.verified_by_name || ''}, ${fmtRelative(lastRes.verified_at)}): `, richText(lastRes.reject_reason || '')));
    }
    if (item.status === 'duplicada' && item.duplicate_of) {
      out.push(h('div', { class: 'callout' }, 'Duplicada de ', h('a', { href: `#/incidencias/${item.duplicate_of.key}` }, h('b', null, item.duplicate_of.key)),
        ` — ${item.duplicate_of.title} `, statusBadge(item.duplicate_of.status)));
    }
    if (item.duplicates?.length) {
      out.push(h('div', { class: 'callout info small' }, `${item.duplicates.length === 1 ? 'Hay 1 incidencia marcada' : `Hay ${item.duplicates.length} incidencias marcadas`} como duplicada de esta: `,
        item.duplicates.map((d, i) => [i ? ', ' : '', h('a', { href: `#/incidencias/${d.key}` }, d.key)])));
    }
    out.push(verificationPanel(item, 'incident', () => reload()));
    mount(alertsBox, out);
  }

  function drawInfo() {
    const reportKeys = Object.keys(REPORT_FIELDS).filter(k => item[k] || item.na_fields?.[k] !== undefined);
    mount(infoBox,
      h('div', null, h('div', { class: 'section-title', style: { marginTop: 0 } }, 'Descripción'),
        item.description ? h('div', { class: 'text-block' }, richText(item.description)) : h('div', { class: 'ws-empty' }, 'Sin descripción.')),
      h('div', null, h('div', { class: 'section-title' }, 'Datos del reporte'),
        reportKeys.length ? h('div', { class: 'kv report-kv' }, reportKeys.map(k => [h('span', null, REPORT_FIELDS[k]),
          item[k] ? h('div', { class: ['steps', 'expected', 'observed'].includes(k) ? 'pre' : '' }, ['steps', 'expected', 'observed'].includes(k) ? richText(item[k]) : valueLabel(k, item[k]))
            : h('div', { class: 'na-val' }, `No aplicable${item.na_fields[k] ? `: ${item.na_fields[k]}` : ''}`)]))
          : h('div', { class: 'ws-empty' }, 'No se rellenaron datos del reporte.')));
  }

  function buildOptions(sel, current) {
    mount(sel, h('option', { value: '' }, '—'), (builds || []).map(b => h('option', { value: b.id }, `${b.number} · ${b.platform_label || b.platform}`)));
    if (current && !(builds || []).some(b => b.id === current)) sel.appendChild(h('option', { value: current }, `#${current}`));
    sel.value = current ? String(current) : '';
  }

  function drawSide() {
    const open = OPEN.includes(item.status);
    let assignee;
    if (p.assign && open) {
      assignee = userSelect(item.assignee_id || '', { projectId: item.project_id });
      assignee.addEventListener('change', () => patch({ assignee_id: assignee.value ? Number(assignee.value) : null }, 'Responsable actualizado.'));
    } else {
      const u = userById(item.assignee_id);
      assignee = h('span', { class: 'row' }, item.assignee_id ? [avatar(u || { display_name: item.assignee_name }), item.assignee_name] : h('span', { class: 'muted' }, 'Sin asignar'),
        p.work && open && item.assignee_id !== me() ? h('button', { class: 'btn ghost sm', onclick: (e) => patch({ assignee_id: me() }, `Te has asignado ${item.key}.`, e.currentTarget) }, 'Asignármela') : null);
    }
    let prio = priorityBadge(item.priority, item.priority_label);
    let target = item.target_date ? h('span', { class: item.overdue ? 'overdue' : '' }, fmtDate(item.target_date)) : h('span', { class: 'muted' }, 'Sin fecha');
    if (canEdit()) {
      prio = selectEl(Object.entries(PRIORITIES), item.priority);
      prio.addEventListener('change', () => patch({ priority: prio.value }, 'Prioridad actualizada.'));
      target = h('input', { type: 'date', value: item.target_date || '', class: item.overdue ? 'overdue' : '' });
      target.addEventListener('change', () => patch({ target_date: target.value || null }, 'Fecha objetivo actualizada.'));
    }
    const buildCell = (col, b) => {
      if (canBuilds() && builds?.length) {
        const sel = h('select');
        buildOptions(sel, item[col]);
        sel.addEventListener('change', () => patch({ [col]: sel.value ? Number(sel.value) : null }, 'Build actualizada.'));
        return sel;
      }
      return b ? h('span', null, `${b.number} · ${b.platform}`) : h('span', { class: 'muted' }, '—');
    };
    mount(sideInfo, h('h3', null, 'Detalles'),
      h('div', { class: 'kv' },
        h('span', null, 'Responsable'), assignee,
        h('span', null, 'Prioridad'), prio,
        h('span', null, 'Fecha objetivo'), target,
        h('span', null, 'Estimación'), h('span', null, item.estimate_hours ? fmtHours(item.estimate_hours) : '—'),
        h('span', null, 'Creada'), h('span', { title: fmtDateTime(item.created_at) }, `${item.created_by_name || ''} · ${fmtRelative(item.created_at)}`),
        h('span', null, 'Último cambio'), h('span', { title: fmtDateTime(item.updated_at) }, `${item.updated_by_name || ''} · ${fmtRelative(item.updated_at)}`),
        item.resolved_at ? [h('span', null, item.status === 'duplicada' ? 'Cerrada' : 'Resuelta'), h('span', { title: fmtDateTime(item.resolved_at) }, `${item.resolved_by_name || ''} · ${fmtRelative(item.resolved_at)}`)] : null,
        h('span', null, 'Encontrada en'), buildCell('found_build_id', item.found_build),
        h('span', null, 'Corregida en'), buildCell('fixed_build_id', item.fixed_build),
        h('span', null, 'Tiempo abierta'), h('span', { title: 'Suma de todos los ciclos (desde que se creó o se reabrió hasta que se cerró)' }, fmtDuration(item.total_open_seconds)),
        item.reopen_count ? [h('span', null, 'Ciclo actual'), h('span', null, fmtDuration(item.current_cycle_seconds))] : null));

    const s = item.sla;
    mount(slaBox, s ? h('div', { class: 'card' }, h('h3', null, 'Plazos (SLA)'),
      h('div', { class: 'kv' },
        s.response_due ? [h('span', null, 'Primera respuesta'), h('span', { class: `sla-badge ${s.response_breached ? 'vencido' : 'ok'}`, title: fmtDateTime(s.response_due) }, dueText(s.response_due))]
          : [h('span', null, 'Primera respuesta'), h('span', { class: 'ok-text small' }, item.first_response_at ? `Respondida ${fmtRelative(item.first_response_at)}` : '—')],
        s.resolve_due ? [h('span', null, 'Resolver'), h('span', { class: `sla-badge ${s.resolve_breached ? 'vencido' : s.state}`, title: fmtDateTime(s.resolve_due) }, dueText(s.resolve_due))] : null),
      s.resolve_ratio !== undefined ? h('div', { class: `progress mt ${s.state === 'ok' ? 'ok' : ''}`, title: `${Math.round(s.resolve_ratio * 100)} % del plazo consumido` },
        h('i', { style: { width: `${Math.min(100, Math.round(s.resolve_ratio * 100))}%`, background: s.state === 'vencido' ? 'var(--danger)' : s.state === 'riesgo' ? 'var(--warn)' : '' } })) : null) : null);

    mount(codeBox, h('h3', null, 'Código (GitHub)'), codeLinksList(item.code_links));
    const views = (item.views || []).filter(v => v.user_id !== item.created_by);
    mount(viewsBox, h('h3', null, 'Quién la ha visto'),
      views.length ? h('div', { class: 'views-list' }, views.map(v => h('div', { class: 'row' }, avatar(userById(v.user_id) || { display_name: v.user_name }),
        h('span', { class: 'grow' }, v.user_name, v.user_id === item.assignee_id ? h('span', { class: 'tiny muted' }, ' (responsable)') : null),
        h('span', { class: 'tiny muted', title: fmtDateTime(v.first_seen_at) }, fmtRelative(v.first_seen_at)))))
        : h('div', { class: 'ws-empty' }, 'Nadie la ha abierto todavía (aparte de quien la creó).'),
      item.assignee_id && !views.some(v => v.user_id === item.assignee_id) && item.assignee_id !== item.created_by
        ? h('div', { class: 'tiny warn-text mt' }, `${item.assignee_name} (responsable) aún no la ha abierto.`) : null);
  }

  function drawTabs() {
    const resCount = (item.resolutions || []).length;
    mount(tabsBar, tabs([
      { key: 'comments', label: 'Comentarios', icon: 'chat', count: (item.comments || []).length },
      { key: 'history', label: 'Historial', icon: 'activity' },
      { key: 'resolutions', label: 'Resoluciones', icon: 'verify', count: resCount },
    ], tab, (k) => { tab = k; drawTabs(); }));
    sec.comments.classList.toggle('hidden', tab !== 'comments');
    historyBox.classList.toggle('hidden', tab !== 'history');
    resBox.classList.toggle('hidden', tab !== 'resolutions');
    if (tab === 'history') mount(historyBox, activityTimeline(item.history));
    if (tab === 'resolutions') mount(resBox, resolutionHistory(item.resolutions));
  }

  function drawAll() {
    ctx.setTitle(`${item.key} · ${item.title}`);
    drawHead(); drawAlerts(); drawInfo(); drawSide(); drawTabs();
  }

  // ---------- carga ----------
  async function loadBuilds() {
    if (!canBuilds() || builds) return;
    try { builds = (await api.get(`/api/builds?project=${item.project_id}`)).items; drawSide(); } catch { builds = []; }
  }

  async function fetchItem() { return (await api.get(`/api/incidents/${encodeURIComponent(ident)}`)).item; }

  let deferred = false;
  const editingSide = () => {
    const a = document.activeElement;
    return a && (sideInfo.contains(a) || headBox.contains(a)) && ['INPUT', 'SELECT', 'TEXTAREA'].includes(a.tagName);
  };
  async function reload() {
    if (!ctx.alive()) return;
    if (editingSide()) { deferred = true; return; }
    try {
      item = await fetchItem();
    } catch (err) {
      if (err.status === 404) { mount(root, emptyState('Esta incidencia ya no está disponible', err.message)); return; }
      toast(err.message, 'error');
      return;
    }
    drawAll();
    loadBuilds();
    sec.comments.refresh(item); sec.atts.refresh(item); sec.rel.refresh(item); sec.time.refresh(item);
  }
  root.addEventListener('focusout', () => setTimeout(() => { if (deferred && !editingSide()) { deferred = false; reload(); } }, 0));

  mount(root, loading());
  try {
    item = await fetchItem();
  } catch (err) {
    mount(root, err.status === 404 ? emptyState('No se ha encontrado la incidencia', err.message) : errorBox(err, () => ctx.refresh()));
    return;
  }
  if (!ctx.alive()) return;
  sec = {
    comments: commentsSection('incident', item.id, { data: item, live: false, title: null }),
    atts: attachmentsSection('incident', item.id, { data: item, live: false }),
    rel: relationsSection('incident', item.id, { data: item, live: false }),
    time: timeWidget('incident', item.id, { data: item, live: false }),
  };
  mount(root, headBox, h('div', { class: 'detail' },
    h('div', { class: 'detail-main' }, alertsBox, infoBox, sec.atts, h('div', null, tabsBar, sec.comments, historyBox, resBox)),
    h('div', { class: 'detail-side' }, sideInfo, slaBox, h('div', { class: 'card' }, sec.time), h('div', { class: 'card' }, sec.rel), codeBox, viewsBox)));
  drawAll();
  loadBuilds();

  if (!seenSent && item.created_by !== me()) {
    seenSent = true;
    api.post(path('/seen'), {}).catch(() => {});
  }

  const onChange = debounce(reload, 250);
  ctx.on('changes', (d) => {
    if (d.changes.some(c => c.kind === 'build')) builds = null;
    if (d.changes.some(c => (['incident', 'time'].includes(c.kind) && c.entity_type === 'incident' && c.entity_id === item.id) || c.kind === 'build')) onChange();
  });
  ctx.on('reconnected', onChange);
  ctx.on('reset', onChange);
}

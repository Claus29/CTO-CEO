// Informes: métricas del equipo en un rango de fechas (respeta el proyecto elegido arriba).
import { api } from '../api.js';
import { can, store, withProject } from '../state.js';
import { addDays, busy, errorBox, fmtDate, fmtDuration, h, icon, loadJSON, loading, mount, pageHead, saveJSON, table, todayISO, toast } from '../ui.js';

const PRESETS = [['30', '30 días'], ['90', '90 días'], ['180', '6 meses'], ['365', '1 año']];

const hours = (v) => v === null || v === undefined ? '—' : fmtDuration(Math.round(v * 3600));
const pct = (v) => v === null || v === undefined ? '—' : `${v} %`;

export default async function render(ctx) {
  const { root } = ctx;
  const saved = loadJSON('dc.reports.range', { preset: '90' });
  let to = ctx.query.get('to') || (saved.preset === 'custom' ? saved.to : todayISO());
  let from = ctx.query.get('from') || (saved.preset === 'custom' ? saved.from : addDays(todayISO(), -Number(saved.preset || 90)));
  let preset = ctx.query.get('from') ? 'custom' : saved.preset || '90';

  const fromIn = h('input', { type: 'date', value: from, max: todayISO() });
  const toIn = h('input', { type: 'date', value: to, max: todayISO() });
  const seg = h('div', { class: 'seg' });
  const drawSeg = () => mount(seg, PRESETS.map(([k, l]) => h('button', { class: preset === k ? 'on' : '', onclick: () => {
    preset = k; to = todayISO(); from = addDays(to, -Number(k)); fromIn.value = from; toIn.value = to; remember(); drawSeg(); load();
  } }, l)));
  const remember = () => saveJSON('dc.reports.range', preset === 'custom' ? { preset, from, to } : { preset });
  const onDate = () => {
    if (!fromIn.value || !toIn.value) return;
    if (fromIn.value > toIn.value) { toast('La fecha de inicio es posterior a la de fin.', 'error'); return; }
    from = fromIn.value; to = toIn.value; preset = 'custom'; remember(); drawSeg(); load();
  };
  fromIn.addEventListener('change', onDate);
  toIn.addEventListener('change', onDate);
  drawSeg();

  const pdfBtn = h('button', { class: 'btn', title: 'Informe en PDF de las incidencias creadas en el rango', onclick: () => busy(pdfBtn, () =>
    api.save(`/api/export/report.pdf?${withProject({ created_from: from, created_to: `${to}T23:59:59` })}`, { folder: 'exportaciones' }))
    .then(r => toast(`Informe guardado en ${r.file || 'Exportaciones'}`, 'ok')).catch(() => {}) }, icon('pdf'), 'Exportar PDF');
  const csvBtn = h('button', { class: 'btn', title: 'Actividad del rango en CSV (se abre con Excel)', onclick: () => busy(csvBtn, () =>
    api.save(`/api/export/activity.csv?${withProject({ from, to: `${to}T23:59:59.999Z` })}`, { folder: 'exportaciones' }))
    .then(r => toast(`Actividad guardada en ${r.file || 'Exportaciones'}`, 'ok')).catch(() => {}) }, icon('download'), 'Actividad (CSV)');

  const body = h('div');
  mount(root,
    pageHead('Informes', can('exportar') ? pdfBtn : null, can('exportar') ? csvBtn : null),
    h('div', { class: 'filters' }, h('div', { class: 'group' }, h('span', null, 'Periodo'), seg),
      h('div', { class: 'group' }, h('span', null, 'Desde'), fromIn, h('span', null, 'hasta'), toIn),
      h('span', { class: 'small muted grow adm-right' }, store.project === 'all' ? 'Todos los proyectos' : `Proyecto: ${store.projects.find(p => p.id === store.project)?.name || ''}`)),
    body);

  let token = 0;
  async function load() {
    const my = ++token;
    if (!body.firstChild) mount(body, loading());
    else body.classList.add('adm-dim');
    try {
      const d = await api.get(`/api/reports?${withProject({ from, to })}`);
      if (my !== token || !ctx.alive()) return;
      body.classList.remove('adm-dim');
      draw(d);
    } catch (err) { body.classList.remove('adm-dim'); mount(body, errorBox(err, load)); }
  }

  function draw(d) {
    const i = d.incidents, s = d.sla, t = d.tasks;
    const kpi = (n, label, sub, cls = '') => h('div', { class: `stat adm-static ${cls}` }, h('div', { class: 'n' }, n), h('div', { class: 'l' }, label),
      sub ? h('div', { class: 'sub' }, sub) : null);
    const rateCls = (v, good, bad, invert) => v === null || v === undefined ? '' : (invert ? (v >= good ? 'ok' : v < bad ? 'warnc' : '') : (v <= good ? 'ok' : v >= bad ? 'warnc' : ''));
    const statBlock = (st) => st.count ? `media ${hours(st.avg_hours)} · ${st.count} casos` : 'sin datos en el periodo';
    mount(body,
      h('div', { class: 'section-title adm-nomargin' }, `Incidencias del ${fmtDate(d.range.from)} al ${fmtDate(d.range.to)}`),
      h('div', { class: 'stats mt' },
        kpi(i.created, 'Incidencias creadas'),
        kpi(i.resolved, 'Resueltas y verificadas', i.rejected ? `${i.rejected} resoluciones rechazadas` : ''),
        kpi(hours(i.first_response.median_hours), 'Primera respuesta (mediana)', statBlock(i.first_response)),
        kpi(hours(i.lead_time.median_hours), 'Desde que se crea hasta cerrarla', statBlock(i.lead_time)),
        kpi(hours(i.time_to_verification.median_hours), 'Tiempo hasta verificar', statBlock(i.time_to_verification)),
        kpi(pct(i.rejection_rate), 'Tasa de rechazo', 'resoluciones que no pasaron la verificación', rateCls(i.rejection_rate, 10, 25)),
        kpi(pct(i.reopen_rate), 'Tasa de reapertura', `${i.reopened} reabiertas`, rateCls(i.reopen_rate, 5, 15))),
      h('div', { class: 'section-title' }, 'Plazos (SLA) y tareas'),
      h('div', { class: 'stats' },
        kpi(pct(s.response_rate), 'SLA de primera respuesta', s.response_total ? `${s.response_ok} de ${s.response_total} a tiempo` : 'sin datos', rateCls(s.response_rate, 90, 70, true)),
        kpi(pct(s.resolve_rate), 'SLA de resolución', s.resolve_total ? `${s.resolve_ok} de ${s.resolve_total} a tiempo` : 'sin datos', rateCls(s.resolve_rate, 90, 70, true)),
        kpi(t.done, 'Tareas completadas'),
        kpi(hours(t.cycle_time.median_hours), 'Duración de una tarea (mediana)', statBlock(t.cycle_time)),
        kpi(pct(t.rejection_rate), 'Tareas rechazadas al verificar', t.rejected ? `${t.rejected} rechazos` : '', rateCls(t.rejection_rate, 10, 25))),
      store.settings?.sla_enabled === false ? h('div', { class: 'callout warn small mb' }, 'Los plazos (SLA) están desactivados en Ajustes: el cumplimiento se calcula igualmente con los plazos guardados.') : null,
      h('div', { class: 'card mb' },
        h('div', { class: 'card-head' }, h('h3', null, 'Ritmo semanal (últimas 12 semanas)'), h('span', { class: 'tiny muted' }, 'No depende del periodo elegido')),
        h('div', { class: 'grid cols-3' },
          weekly(d.weekly, 'incidents_created', 'Incidencias creadas', 'adm-bar-a'),
          weekly(d.weekly, 'incidents_resolved', 'Incidencias resueltas', 'adm-bar-b'),
          weekly(d.weekly, 'tasks_done', 'Tareas completadas', 'adm-bar-c')),
        h('details', { class: 'mt small' }, h('summary', { class: 'muted' }, 'Ver como tabla'),
          table([{ label: 'Semana del', render: w => fmtDate(w.week) }, { label: 'Creadas', render: w => w.incidents_created },
            { label: 'Resueltas', render: w => w.incidents_resolved }, { label: 'Tareas', render: w => w.tasks_done }], d.weekly))),
      h('div', { class: 'grid cols-2 adm-rep-grid' },
        h('div', { class: 'card' }, h('h3', null, 'Errores por versión'),
          h('p', { class: 'tiny muted' }, 'Incidencias encontradas en builds de cada versión y corregidas en builds de esa versión.'),
          perVersion(d.per_version)),
        h('div', { class: 'card' }, h('h3', null, 'Cómo leer estos datos'),
          h('ul', { class: 'small muted adm-help' },
            h('li', null, h('b', null, 'Primera respuesta: '), 'desde que se crea una incidencia hasta que alguien la asigna, comenta o empieza.'),
            h('li', null, h('b', null, 'Hasta cerrarla: '), 'desde que se crea hasta que su creador verifica la solución.'),
            h('li', null, h('b', null, 'Tasa de rechazo: '), 'porcentaje de soluciones que no pasaron la verificación. Alta = se da por resuelto demasiado pronto.'),
            h('li', null, h('b', null, 'Reapertura: '), 'incidencias verificadas que luego volvieron a abrirse.'),
            h('li', null, 'Se usa la mediana (el valor del medio) porque no se deja engañar por un caso aislado muy largo.')))),
      h('div', { class: 'section-title' }, 'Por persona'),
      table([
        { label: 'Persona', render: p => h('b', null, p.user_name) },
        { label: 'Horas registradas', render: p => p.logged_hours ? `${p.logged_hours} h` : '—' },
        { label: 'Tareas hechas', render: p => p.tasks_done || '—' },
        { label: 'Estimado / real', render: p => p.tasks_done ? h('span', null, `${p.estimated_hours} h / ${p.actual_hours} h`, deviation(p)) : '—' },
        { label: 'Incidencias resueltas', render: p => p.incidents_resolved || '—' },
        { label: 'Abiertas ahora', render: p => p.open_items ? h('span', { class: 'badge neutral' }, p.open_items) : '—' },
      ], [...d.people].sort((a, b) => b.logged_hours - a.logged_hours || a.user_name.localeCompare(b.user_name)), { empty: 'No hay personas activas' }),
      h('p', { class: 'tiny muted mt' }, '«Estimado / real» compara la estimación de las tareas completadas en el periodo con el tiempo registrado en ellas.'));
  }

  function deviation(p) {
    if (!p.estimated_hours || !p.actual_hours) return null;
    const dev = Math.round(100 * (p.actual_hours - p.estimated_hours) / p.estimated_hours);
    return h('span', { class: `tiny adm-ml ${Math.abs(dev) <= 20 ? 'ok-text' : 'warn-text'}`, title: 'Diferencia entre lo real y lo estimado' }, `${dev > 0 ? '+' : ''}${dev} %`);
  }

  function weekly(weeks, key, label, cls) {
    const max = Math.max(1, ...weeks.map(w => w[key]));
    const total = weeks.reduce((s, w) => s + w[key], 0);
    const peak = weeks.reduce((best, w, idx) => (w[key] > weeks[best][key] ? idx : best), 0);
    return h('div', { class: 'adm-chart' },
      h('div', { class: 'row between' }, h('b', { class: 'small' }, label), h('span', { class: 'tiny muted' }, `${total} en total`)),
      h('div', { class: 'adm-bars', role: 'img', 'aria-label': `${label}: ${weeks.map(w => `semana del ${fmtDate(w.week)} ${w[key]}`).join(', ')}` },
        weeks.map((w, idx) => h('div', { class: 'adm-barcol', title: `Semana del ${fmtDate(w.week)}: ${w[key]}` },
          idx === peak && w[key] ? h('span', { class: 'adm-barlabel' }, w[key]) : null,
          h('div', { class: `adm-bar ${cls}`, style: { height: `${w[key] ? Math.max(4, Math.round(100 * w[key] / max)) : 0}%` } })))),
      h('div', { class: 'adm-axis tiny muted' }, h('span', null, fmtDate(weeks[0].week).slice(0, 5)), h('span', null, 'esta semana')));
  }

  function perVersion(rows) {
    if (!rows.length) return h('div', { class: 'empty small' }, 'No hay versiones en este proyecto.');
    const max = Math.max(1, ...rows.map(r => Math.max(r.found, r.fixed)));
    return h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
      h('thead', null, h('tr', null, h('th', null, 'Versión'), h('th', null, 'Encontradas'), h('th', null, 'Corregidas'))),
      h('tbody', null, rows.map(r => h('tr', null, h('td', null, h('a', { href: '#/versiones' }, r.name)),
        h('td', null, h('div', { class: 'adm-inline' }, h('span', { class: 'adm-ibar adm-bar-a', style: { width: `${Math.round(60 * r.found / max)}px` } }), r.found)),
        h('td', null, h('div', { class: 'adm-inline' }, h('span', { class: 'adm-ibar adm-bar-b', style: { width: `${Math.round(60 * r.fixed / max)}px` } }), r.fixed)))))));
  }

  await load();
  ctx.onChanges(['incident', 'task', 'time', 'version', 'build'], load);
}

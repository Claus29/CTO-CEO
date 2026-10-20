// Roadmap: previsión de cada versión según el ritmo real del equipo, con una barra temporal sencilla.
import { api } from '../api.js';
import { projectById, withProject } from '../state.js';
import { addDays, emptyState, errorBox, fmtDate, h, icon, loading, mount, MONTHS, pageHead, todayISO } from '../ui.js';
import { bar, daysBetween, kindBadge, multiProject } from './_plan_shared.js';

const VSTATUS = { planificada: 'Planificada', en_curso: 'En curso', publicada: 'Publicada' };
const short = (iso) => { const [, m, d] = iso.split('-').map(Number); return `${d} ${MONTHS[m - 1].slice(0, 3)}`; };

export default async function render(ctx) {
  const { root } = ctx;
  const body = h('div', null, loading());

  async function load() {
    let d;
    try { d = await api.get(`/api/roadmap?${withProject()}`); } catch (err) { mount(body, errorBox(err, load)); return; }
    if (!ctx.alive()) return;
    draw(d);
  }

  function status(v, velocity) {
    if (!v.tasks_total) return { cls: 'neutral', label: 'Sin tareas', text: 'Esta versión aún no tiene tareas: añade tareas para poder estimar cuándo estará lista.' };
    if (v.remaining === 0) return { cls: 'ok', label: 'Lista', text: 'Todas las tareas están resueltas: la versión está lista para publicarse.' };
    if (!v.forecast) return { cls: 'danger', label: 'Sin estimación', text: `Faltan ${v.remaining} tareas, pero el equipo no ha cerrado tareas en las últimas 4 semanas, así que no se puede estimar la fecha.` };
    const base = `Faltan ${v.remaining} de ${v.tasks_total} tareas. A ${String(velocity).replace('.', ',')} tareas por semana, terminaría hacia el ${fmtDate(v.forecast)} (con un margen del 30 %, el ${fmtDate(v.forecast_margin)}).`;
    if (!v.target_date) return { cls: 'info', label: 'Sin fecha prevista', text: `${base} La versión no tiene fecha prevista.` };
    if (v.at_risk) {
      const late = daysBetween(v.target_date, v.forecast);
      return { cls: 'danger', label: 'En riesgo', text: `${base} Eso es ${late} días después de la fecha prevista (${fmtDate(v.target_date)}).` };
    }
    if (v.forecast_margin && v.forecast_margin > v.target_date) {
      return { cls: 'warn', label: 'Justa', text: `${base} Llega a tiempo para el ${fmtDate(v.target_date)}, pero sin margen ante imprevistos.` };
    }
    return { cls: 'ok', label: 'En plazo', text: `${base} Llega con margen a la fecha prevista (${fmtDate(v.target_date)}).` };
  }

  function draw(d) {
    const today = todayISO();
    const vs = d.versions;
    const risk = vs.filter(v => v.at_risk).length;
    const maxWeek = Math.max(1, ...d.per_week);

    // Rango temporal de la barra: desde la fecha más temprana hasta la más tardía, con un poco de aire.
    const dates = [today];
    for (const v of vs) for (const k of ['start_date', 'target_date', 'forecast', 'forecast_margin']) if (v[k]) dates.push(v[k]);
    for (const m of d.milestones) dates.push(m.date);
    dates.sort();
    let from = addDays(dates[0], -7), to = addDays(dates[dates.length - 1], 14);
    if (daysBetween(from, to) < 56) to = addDays(from, 56);
    const span = daysBetween(from, to);
    const pos = (iso) => `${(100 * Math.max(0, Math.min(span, daysBetween(from, iso))) / span).toFixed(2)}%`;
    const months = [];
    let [y, m] = from.split('-').map(Number);
    for (;;) {
      m += 1; if (m > 12) { m = 1; y += 1; }
      const iso = `${y}-${String(m).padStart(2, '0')}-01`;
      if (iso > to) break;
      months.push(iso);
    }

    const axis = h('div', { class: 'rm-row rm-axis' }, h('div', { class: 'rm-label' }),
      h('div', { class: 'rm-track' },
        h('span', { class: 'rm-tick', style: { left: '0%' } }, short(from)),
        months.map(iso => h('span', { class: 'rm-tick month', style: { left: pos(iso) } }, MONTHS[Number(iso.slice(5, 7)) - 1])),
        d.milestones.map(ms => h('span', { class: 'rm-flag', style: { left: pos(ms.date) }, title: `${ms.title} · ${fmtDate(ms.date)}` }, icon('flag', 12)))));

    const rows = vs.map(v => {
      const st = status(v, d.velocity);
      const start = v.start_date && v.start_date < (v.target_date || to) ? v.start_date : (v.start_date || today);
      const end = v.target_date || v.forecast || null;
      const pct = v.tasks_total ? Math.round(100 * v.tasks_done / v.tasks_total) : 0;
      const track = h('div', { class: 'rm-track' },
        end && end >= start ? h('div', { class: 'rm-plan', style: { left: pos(start), width: `calc(${pos(end)} - ${pos(start)})` }, title: `Planificada: ${fmtDate(v.start_date)} → ${fmtDate(v.target_date)}` },
          h('div', { class: 'rm-done', style: { width: `${pct}%` } })) : null,
        v.tasks_total && v.forecast && v.forecast_margin ? h('div', { class: 'rm-margin', style: { left: pos(v.forecast), width: `calc(${pos(v.forecast_margin)} - ${pos(v.forecast)})` }, title: `Margen: hasta ${fmtDate(v.forecast_margin)}` }) : null,
        v.tasks_total && v.forecast ? h('div', { class: `rm-forecast ${v.at_risk ? 'risk' : ''}`, style: { left: pos(v.forecast) }, title: `Previsión: ${fmtDate(v.forecast)}` }) : null,
        v.target_date ? h('div', { class: 'rm-target', style: { left: pos(v.target_date) }, title: `Fecha prevista: ${fmtDate(v.target_date)}` }) : null);
      const series = v.burndown || [];
      const maxB = Math.max(1, ...series.map(p => p.remaining));
      return h('div', { class: 'rm-item' },
        h('div', { class: 'rm-row' },
          h('div', { class: 'rm-label' },
            h('a', { href: `#/versiones?id=${v.id}`, class: 'rm-name' }, v.name),
            h('div', { class: 'row wrap' },
              multiProject() ? h('span', { class: 'tiny muted' }, projectById(v.project_id)?.key || '') : null,
              kindBadge('version', v.status, VSTATUS[v.status]),
              h('span', { class: `badge ${st.cls}` }, st.label))),
          track),
        h('details', { class: 'rm-details' },
          h('summary', { class: 'small' }, st.text),
          h('div', { class: 'row top wrap gap-lg mt' },
            h('div', { class: 'col rm-facts' },
              h('div', { class: 'kv' },
                h('span', null, 'Tareas'), h('span', null, `${v.tasks_done} resueltas de ${v.tasks_total}`),
                h('span', null, 'Inicio'), h('span', null, fmtDate(v.start_date)),
                h('span', null, 'Fecha prevista'), h('span', null, fmtDate(v.target_date)),
                h('span', null, 'Previsión'), h('span', null, v.forecast ? fmtDate(v.forecast) : '—'),
                h('span', null, 'Con margen'), h('span', null, v.forecast_margin ? fmtDate(v.forecast_margin) : '—')),
              bar(pct)),
            series.length ? h('div', { class: 'col grow' },
              h('div', { class: 'tiny muted' }, 'Tareas pendientes al final de cada semana (últimas 8 semanas)'),
              h('div', { class: 'spark' }, series.map(p => h('div', { class: 'spark-col', title: `${fmtDate(p.date)}: ${p.remaining} pendientes` },
                h('i', { style: { height: `${Math.round(100 * p.remaining / maxB)}%` } })))),
              h('div', { class: 'row between tiny muted' }, h('span', null, short(series[0].date)), h('span', null, short(series[series.length - 1].date)))) : null)));
    });

    mount(body,
      h('div', { class: 'stats' },
        h('div', { class: 'stat' }, h('div', { class: 'n' }, String(d.velocity).replace('.', ',')), h('div', { class: 'l' }, 'Tareas resueltas por semana'), h('div', { class: 'sub' }, 'Media de las últimas 4 semanas')),
        h('div', { class: 'stat' }, h('div', { class: 'n' }, vs.length), h('div', { class: 'l' }, 'Versiones sin publicar')),
        h('div', { class: `stat ${risk ? 'alert' : ''}` }, h('div', { class: 'n' }, risk), h('div', { class: 'l' }, 'Versiones en riesgo')),
        h('div', { class: 'stat' }, h('div', { class: 'n small-n' }, d.milestones[0] ? fmtDate(d.milestones[0].date) : '—'), h('div', { class: 'l' }, d.milestones[0] ? `Próximo hito: ${d.milestones[0].title}` : 'Sin hitos próximos'))),
      h('div', { class: 'card' },
        h('div', { class: 'card-head' }, h('h3', null, 'Previsión por versión'),
          h('div', { class: 'rm-legend tiny muted row wrap' },
            h('span', null, h('i', { class: 'lg plan' }), 'Planificada (relleno = hecho)'),
            h('span', null, h('i', { class: 'lg forecast' }), 'Previsión'),
            h('span', null, h('i', { class: 'lg margin' }), 'Margen +30 %'),
            h('span', null, h('i', { class: 'lg target' }), 'Fecha prevista'),
            h('span', null, h('i', { class: 'lg today' }), 'Hoy'))),
        vs.length ? h('div', { class: 'rm' },
          h('div', { class: 'rm-today', style: { left: `calc(var(--rm-label) + (100% - var(--rm-label)) * ${(Math.max(0, daysBetween(from, today)) / span).toFixed(4)})` }, title: `Hoy, ${fmtDate(today)}` }),
          axis, rows)
          : emptyState('No hay versiones pendientes', 'Crea una versión en «Versiones» y asígnale tareas para ver aquí su previsión.')),
      h('div', { class: 'grid cols-2 mt' },
        h('div', { class: 'card' },
          h('div', { class: 'card-head' }, h('h3', null, 'Ritmo del equipo')),
          h('div', { class: 'week-bars' }, d.per_week.map((n, i) => {
            const ws = addDays(today, -7 * (d.per_week.length - i) + 1);
            return h('div', { class: 'wb' + (i >= d.per_week.length - 4 ? ' recent' : ''), title: `Semana desde el ${fmtDate(ws)}: ${n} tareas resueltas` },
              h('span', { class: 'tiny' }, n), h('i', { style: { height: `${Math.round(100 * n / maxWeek)}%` } }), h('span', { class: 'tiny muted' }, short(ws)));
          })),
          h('div', { class: 'tiny muted mt' }, 'Tareas resueltas en cada una de las últimas 8 semanas. Las 4 más recientes (resaltadas) son las que cuentan para el ritmo.')),
        h('div', { class: 'card' },
          h('h3', null, '¿Cómo se calcula?'),
          h('ol', { class: 'small explain' },
            h('li', null, h('b', null, 'Ritmo: '), 'contamos cuántas tareas ha resuelto el equipo en las últimas 4 semanas y sacamos la media semanal.'),
            h('li', null, h('b', null, 'Previsión: '), 'tareas que faltan en la versión ÷ ritmo = semanas necesarias, contadas desde hoy.'),
            h('li', null, h('b', null, 'Margen: '), 'añadimos un 30 % más de tiempo para imprevistos (bajas, bloqueos, tareas nuevas).'),
            h('li', null, h('b', null, 'Riesgo: '), 'la versión está en riesgo si la previsión cae después de su fecha prevista, o si faltan tareas y no hay ritmo reciente para estimar.')),
          h('div', { class: 'callout info small' }, 'Es una estimación: supone que el equipo sigue al mismo ritmo y que todas las tareas pesan parecido. Si se añaden tareas a la versión, la previsión se retrasa.'),
          d.milestones.length ? h('div', { class: 'mt' }, h('div', { class: 'section-title' }, 'Próximas fechas importantes'),
            d.milestones.slice(0, 6).map(ms => h('div', { class: 'row small' }, icon('flag', 13), h('span', { class: 'nowrap' }, fmtDate(ms.date)), h('span', { class: 'grow ellipsis' }, ms.title)))) : null)));
  }

  mount(root, pageHead('Roadmap', h('a', { class: 'btn', href: '#/versiones' }, icon('tag'), 'Versiones')),
    h('p', { class: 'muted small mb' }, 'Cuándo estará lista cada versión si el equipo sigue al ritmo actual.'), body);
  await load();
  ctx.onChanges(['version', 'task', 'date'], load);
}

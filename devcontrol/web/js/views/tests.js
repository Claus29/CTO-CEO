// Planes de prueba: casos de prueba, ejecuciones por build y resultados, con incidencias creadas desde los fallos.
import { api } from '../api.js';
import { can, store, withProject } from '../state.js';
import { CATEGORIES, ENVIRONMENTS, FREQUENCIES, PRIORITIES, REPORT_FIELDS, TEST_RESULTS, toOptions } from '../labels.js';
import {
  busy, confirmDialog, debounce, emptyState, errorBox, field, fmtDate, fmtRelative, h, icon, loading, modal, mount,
  pageHead, selectEl, table, tabs, toast, uid,
} from '../ui.js';
import { bar, kindBadge, multiProject, newProjectId, projectField, projectKey, requireFields, submit } from './_plan_shared.js';

const RESULT_BUTTONS = [['ok', 'Correcto', 'check'], ['fallo', 'Fallo', 'x'], ['bloqueado', 'Bloqueado', 'lock'], ['omitido', 'Omitido', 'right']];

function runProgress(r) {
  const c = r.counts || {};
  const total = r.total || 0;
  const seg = (k) => c[k] ? h('i', { class: `seg-${k}`, style: { width: `${(100 * c[k] / total).toFixed(2)}%` }, title: `${TEST_RESULTS[k]}: ${c[k]}` }) : null;
  return h('div', { class: 'run-progress' },
    h('div', { class: 'run-bar' }, seg('ok'), seg('fallo'), seg('bloqueado'), seg('omitido')),
    h('div', { class: 'row wrap tiny muted' },
      h('span', null, `${r.executed}/${total} ejecutados`),
      c.ok ? h('span', { class: 'ok-text' }, `${c.ok} correctos`) : null,
      c.fallo ? h('span', { class: 'danger-text' }, `${c.fallo} fallos`) : null,
      c.bloqueado ? h('span', { class: 'warn-text' }, `${c.bloqueado} bloqueados`) : null,
      c.omitido ? h('span', null, `${c.omitido} omitidos`) : null,
      r.pass_rate !== null && r.pass_rate !== undefined ? h('b', null, `· ${r.pass_rate}% superado`) : null));
}

export default async function render(ctx) {
  const { root } = ctx;
  const canEdit = can('calidad.editar');
  let tab = ctx.query.get('tab') === 'casos' ? 'casos' : 'ejecuciones';
  let runId = Number(ctx.query.get('run')) || null;
  let cases = null, runs = null, builds = [];
  let runData = null, resultFilter = '';
  const body = h('div', null, loading());
  const tabBar = h('div');
  const caseSearch = h('input', { type: 'search', placeholder: 'Buscar casos…' });
  caseSearch.addEventListener('input', debounce(() => drawCases(), 150));

  function nav(params) {
    const q = new URLSearchParams(params);
    history.replaceState(null, '', `#/pruebas${q.toString() ? '?' + q : ''}`);
  }

  async function load(remote = false) {
    try {
      if (runId) {
        const id = runId;
        const d = await api.get(`/api/test-runs/${id}`);
        if (!ctx.alive() || id !== runId) return;
        runData = d;
        drawRun(remote === true);
        return;
      }
      const [c, r, b] = await Promise.all([api.get(`/api/test-cases?${withProject()}`), api.get(`/api/test-runs?${withProject()}`), api.get(`/api/builds?${withProject()}`)]);
      if (!ctx.alive()) return;
      cases = c.items; runs = r.items; builds = b.items;
    } catch (err) { mount(body, errorBox(err, load)); return; }
    drawMain();
  }

  // ---------- lista principal ----------
  function drawMain() {
    mount(tabBar, tabs([
      { key: 'ejecuciones', label: 'Ejecuciones', icon: 'play', count: runs.length },
      { key: 'casos', label: 'Casos de prueba', icon: 'flask', count: cases.length },
    ], tab, (k) => { tab = k; nav(k === 'casos' ? { tab: k } : {}); drawMain(); }));
    if (tab === 'casos') drawCases(); else drawRuns();
  }

  function drawRuns() {
    if (!runs.length) {
      mount(body, emptyState('Todavía no hay ejecuciones', cases.length
        ? (canEdit ? 'Pulsa «Nueva ejecución» para probar una build con los casos de prueba.' : '')
        : 'Primero crea los casos de prueba en la pestaña «Casos de prueba».'));
      return;
    }
    mount(body, table([
      { label: 'Ejecución', render: r => h('div', { class: 'title-cell' }, r.title,
        h('div', { class: 'sub' }, [r.device, r.os_version].filter(Boolean).join(' · ') || '—')) },
      multiProject() ? { label: 'Proyecto', render: r => projectKey(r.project_id), cls: 'hide-sm' } : null,
      { label: 'Build', render: r => r.build_number ? `${r.build_number} (${r.platform})` : h('span', { class: 'muted' }, '—') },
      { label: 'Progreso', render: r => runProgress(r) },
      { label: 'Estado', render: r => r.status === 'terminada' ? h('span', { class: 'badge ok' }, 'Terminada') : h('span', { class: 'badge info' }, 'En curso') },
      { label: 'Creada', render: r => h('span', { class: 'small', title: fmtDate(r.created_at) }, `${r.created_by_name || ''} · ${fmtRelative(r.created_at)}`), cls: 'hide-sm' },
    ].filter(Boolean), runs, { onRow: r => openRun(r.id) }));
  }

  function drawCases() {
    if (tab !== 'casos' || !cases) return;
    const q = caseSearch.value.trim().toLowerCase();
    const list = cases.filter(c => !q || `${c.suite} ${c.title} ${c.steps} ${c.expected}`.toLowerCase().includes(q));
    const groups = {};
    for (const c of list) (groups[c.suite] ||= []).push(c);
    mount(body,
      h('div', { class: 'toolbar' }, h('div', { class: 'search' }, icon('search'), caseSearch),
        h('span', { class: 'tiny muted' }, `${cases.length} casos activos`)),
      !cases.length ? emptyState('No hay casos de prueba', canEdit ? 'Un caso describe qué comprobar: los pasos y el resultado esperado. Crea el primero con «Nuevo caso».' : '')
        : !list.length ? emptyState('Ningún caso coincide con la búsqueda', '')
          : Object.entries(groups).map(([suite, items]) => h('div', { class: 'list-card mb' },
            h('div', { class: 'card-head' }, h('h3', null, suite), h('span', { class: 'muted small' }, items.length)),
            items.map(c => h('div', { class: 'list-item top case-item', onclick: canEdit ? () => caseForm(c) : null },
              h('div', { class: 'grow' },
                h('div', null, h('b', null, c.title), multiProject() ? h('span', { class: 'tiny muted' }, ` · ${projectKey(c.project_id)}`) : null),
                c.steps ? h('div', { class: 'tiny muted pre case-steps' }, c.steps) : null,
                c.expected ? h('div', { class: 'tiny' }, h('span', { class: 'muted' }, 'Esperado: '), c.expected) : null),
              canEdit ? h('div', { class: 'row', onclick: (e) => e.stopPropagation() },
                h('button', { class: 'btn ghost sm icon', title: 'Editar', onclick: () => caseForm(c) }, icon('edit')),
                h('button', { class: 'btn ghost sm icon', title: 'Archivar (deja de usarse en nuevas ejecuciones)', onclick: () => archiveCase(c) }, icon('trash'))) : null)))));
  }

  async function archiveCase(c) {
    if (!await confirmDialog({ title: 'Archivar caso de prueba', message: `«${c.title}» dejará de incluirse en nuevas ejecuciones. Los resultados anteriores se conservan.`, confirmLabel: 'Archivar', danger: true })) return;
    try { await api.patch(`/api/test-cases/${c.id}`, { archived: true }); toast('Caso archivado.', 'ok'); load(); } catch (err) { toast(err.message, 'error'); }
  }

  function caseForm(c) {
    const editing = !!c;
    let projectId = c?.project_id || newProjectId();
    const proj = editing ? null : projectField(projectId, (v) => { projectId = Number(v); });
    const suites = [...new Set((cases || []).map(x => x.suite))];
    const suite = h('input', { maxlength: 80, list: 'tc-suites', placeholder: 'General', value: c?.suite || '' });
    const title = h('input', { maxlength: 200, placeholder: 'Ej.: Iniciar sesión con usuario y contraseña', value: c?.title || '' });
    const steps = h('textarea', { rows: 6, placeholder: '1. Abrir la app\n2. Pulsar «Entrar»\n3. …' });
    steps.value = c?.steps || '';
    const expected = h('textarea', { rows: 3, placeholder: 'Qué debe pasar si todo va bien' });
    expected.value = c?.expected || '';
    modal({ title: editing ? 'Editar caso de prueba' : 'Nuevo caso de prueba', wide: true,
      body: h('div', { class: 'form-grid' },
        field('Título', title, { required: true, cls: 'span2' }),
        field('Grupo', suite, { hint: 'Agrupa casos relacionados (Acceso, Pagos…). Se puede ejecutar un solo grupo.' }),
        h('datalist', { id: 'tc-suites' }, suites.map(s => h('option', { value: s }))),
        proj ? proj.el : h('div'),
        field('Pasos', steps, { cls: 'span2' }), field('Resultado esperado', expected, { cls: 'span2' })),
      actions: [{ label: 'Cancelar' }, { label: editing ? 'Guardar' : 'Crear caso', kind: 'primary', onClick: () => {
        if (!requireFields([[title, 'el título']])) return false;
        const data = { suite: suite.value.trim() || 'General', title: title.value.trim(), steps: steps.value.trim(), expected: expected.value.trim() };
        if (editing) return submit(() => api.patch(`/api/test-cases/${c.id}`, data), 'Caso actualizado.', load);
        return submit(() => api.post('/api/test-cases', { ...data, uid: uid(), project_id: projectId }, { queue: true }), 'Caso creado.', load);
      } }] });
  }

  function runForm(buildId) {
    const preBuild = builds.find(b => b.id === buildId);
    let projectId = preBuild?.project_id || newProjectId();
    const buildSel = selectEl([], '');
    const suiteSel = selectEl([], '');
    const count = h('div', { class: 'tiny muted' });
    const fill = () => {
      mount(buildSel, h('option', { value: '' }, 'Sin build concreta'),
        builds.filter(b => b.project_id === projectId).map(b => h('option', { value: String(b.id) }, `${b.number} · ${b.platform_label}${b.version_name ? ' · ' + b.version_name : ''} · ${b.channel_label}`)));
      if (preBuild && preBuild.project_id === projectId) buildSel.value = String(preBuild.id);
      const pc = cases.filter(c => c.project_id === projectId);
      const suites = [...new Set(pc.map(c => c.suite))];
      mount(suiteSel, h('option', { value: '' }, `Todos los grupos (${pc.length} casos)`),
        suites.map(s => h('option', { value: s }, `${s} (${pc.filter(c => c.suite === s).length} casos)`)));
      count.textContent = pc.length ? '' : 'Este proyecto no tiene casos de prueba: créalos antes de ejecutar.';
    };
    const proj = projectField(projectId, (v) => { projectId = Number(v); fill(); });
    fill();
    const title = h('input', { maxlength: 200, placeholder: 'Ej.: Regresión antes de publicar' });
    const device = h('input', { maxlength: 120, placeholder: 'Ej.: iPhone 13' });
    const os = h('input', { maxlength: 120, placeholder: 'Ej.: iOS 17.5' });
    modal({ title: 'Nueva ejecución de pruebas', wide: true,
      body: h('div', { class: 'form-grid' },
        proj.el, field('Build que se prueba', buildSel),
        field('Casos a ejecutar', suiteSel), field('Título', title, { hint: 'Si lo dejas vacío: «Ejecución de pruebas».' }),
        field('Dispositivo', device), field('Sistema y versión', os), h('div', { class: 'span2' }, count)),
      actions: [{ label: 'Cancelar' }, { label: 'Empezar ejecución', kind: 'primary', onClick: async () => {
        try {
          const r = await api.post('/api/test-runs', {
            uid: uid(), project_id: projectId, build_id: buildSel.value ? Number(buildSel.value) : null, suite: suiteSel.value,
            title: title.value.trim(), device: device.value.trim(), os_version: os.value.trim(),
          });
          toast('Ejecución creada. Marca el resultado de cada caso.', 'ok');
          if (r.id) openRun(r.id); else load();
          return true;
        } catch (err) { toast(err.message, 'error'); return false; }
      } }] });
  }

  // ---------- ejecución ----------
  function openRun(id) {
    runId = id; runData = null; resultFilter = '';
    nav({ run: id });
    mount(body, loading());
    drawShell();
    load();
  }

  function closeRun() {
    runId = null; runData = null;
    nav(tab === 'casos' ? { tab } : {});
    drawShell();
    mount(body, loading());
    load();
  }

  async function setResult(r, result, note, btn) {
    try {
      const res = await busy(btn, () => api.post(`/api/test-runs/${runId}/results/${r.case_id}`, { result, note }));
      r.result = result; r.note = note;
      if (result === 'fallo' && res.incident_prefill && can('incidencias.crear')) incidentForm(r, res.incident_prefill);
      load();
    } catch { /* aviso ya mostrado */ }
  }

  function prefillFor(r) {
    const run = runData.run;
    return { title: `Fallo en prueba: ${r.title}`, steps: r.steps, expected: r.expected, found_build_id: run.build_id,
      device: run.device, os_version: run.os_version, project_id: run.project_id, category: 'errores' };
  }

  function incidentForm(r, prefill) {
    const run = runData.run;
    const tpl = (store.settings.report_templates || {});
    const inputs = {};
    const title = h('input', { maxlength: 200, value: prefill.title || '' });
    const priority = selectEl(toOptions(PRIORITIES), 'alta');
    const category = selectEl(toOptions(CATEGORIES), prefill.category || 'errores');
    const mk = (k, multi) => { const el = multi ? h('textarea', { rows: 3 }) : h('input'); return el; };
    for (const k of ['steps', 'expected', 'observed']) inputs[k] = mk(k, true);
    for (const k of ['device', 'os_version', 'app_version']) inputs[k] = mk(k, false);
    inputs.environment = selectEl(toOptions(ENVIRONMENTS, '—'), 'interno');
    inputs.frequency = selectEl(toOptions(FREQUENCIES, '—'), '');
    inputs.steps.value = prefill.steps || '';
    inputs.expected.value = prefill.expected || '';
    inputs.observed.value = r.note || '';
    inputs.device.value = prefill.device || '';
    inputs.os_version.value = prefill.os_version || '';
    inputs.app_version.value = run.build_number || '';
    const reqBox = h('div', { class: 'tiny muted span2' });
    const labels = {};
    const fieldsGrid = h('div', { class: 'form-grid' });
    const drawFields = () => {
      const req = new Set(tpl[category.value]?.required || []);
      reqBox.textContent = req.size ? `Para «${CATEGORIES[category.value]}» son obligatorios: ${[...req].map(k => REPORT_FIELDS[k] || k).join(', ')}.` : '';
      mount(fieldsGrid,
        field('Título', title, { required: true, cls: 'span2' }),
        field('Prioridad', priority), field('Categoría', category), reqBox,
        ['steps', 'expected', 'observed'].map(k => field(REPORT_FIELDS[k], inputs[k], { required: req.has(k), cls: 'span2' })),
        ['device', 'os_version', 'app_version', 'environment', 'frequency'].map(k => field(REPORT_FIELDS[k], inputs[k], { required: req.has(k) })));
      labels.req = req;
    };
    category.addEventListener('change', drawFields);
    drawFields();
    modal({ title: 'Crear incidencia del fallo', wide: true,
      body: h('div', { class: 'col' },
        h('p', { class: 'small muted' }, `Se creará una incidencia en ${projectKey(run.project_id) || 'el proyecto'} vinculada a este caso${run.build_number ? ` y a la build ${run.build_number}` : ''}.`),
        fieldsGrid),
      actions: [{ label: 'Ahora no' }, { label: 'Crear incidencia', kind: 'primary', onClick: async () => {
        const missing = [[title, 'el título'], ...[...labels.req].filter(k => inputs[k]).map(k => [inputs[k], `«${REPORT_FIELDS[k]}»`])];
        if (!requireFields(missing)) return false;
        const data = { uid: uid(), project_id: run.project_id, title: title.value.trim(), priority: priority.value, category: category.value,
          found_build_id: prefill.found_build_id || null, description: `Detectado en la ejecución de pruebas «${run.title}».` };
        for (const [k, el] of Object.entries(inputs)) data[k] = el.value.trim ? el.value.trim() : el.value;
        try {
          const res = await api.post('/api/incidents', data);
          const inc = res.item;
          await api.post(`/api/test-runs/${run.id}/results/${r.case_id}/incident`, { incident: inc.id });
          toast(`Incidencia ${inc.key} creada y vinculada al caso. Pulsa para abrirla.`, 'ok', { onClick: () => { location.hash = `#/incidencias/${inc.key}`; } });
          load();
          return true;
        } catch (err) { toast(err.message, 'error'); return false; }
      } }] });
  }

  function resultRow(r) {
    const note = h('input', { class: 'run-note', placeholder: r.result === 'fallo' ? 'Qué ha pasado (se usará como resultado observado)' : 'Nota (opcional)', value: r.note || '', maxlength: 2000, disabled: !canEdit });
    note.addEventListener('change', () => {
      if (note.value.trim() === (r.note || '')) return;
      setResult(r, r.result, note.value.trim(), null);
    });
    const buttons = RESULT_BUTTONS.map(([k, label, ic]) => {
      const b = h('button', { class: `res-btn res-${k}` + (r.result === k ? ' on' : ''), type: 'button', disabled: !canEdit, title: r.result === k ? 'Volver a pendiente' : `Marcar como ${label.toLowerCase()}` }, icon(ic, 14), label);
      b.addEventListener('click', () => setResult(r, r.result === k ? 'pendiente' : k, note.value.trim(), b));
      return b;
    });
    return h('div', { class: `run-case res-row-${r.result}` },
      h('div', { class: 'grow' },
        h('div', { class: 'row wrap' }, h('b', null, r.title), kindBadge('result', r.result, TEST_RESULTS[r.result]),
          r.incident_key ? h('a', { class: 'badge danger', href: `#/incidencias/${r.incident_key}`, title: 'Incidencia creada desde este fallo' }, icon('bug', 12), r.incident_key) : null,
          r.result === 'fallo' && !r.incident_key && can('incidencias.crear') && canEdit
            ? h('button', { class: 'btn sm', onclick: () => incidentForm(r, prefillFor(r)) }, icon('bug'), 'Crear incidencia') : null),
        r.steps || r.expected ? h('details', { class: 'case-details' }, h('summary', { class: 'tiny muted' }, 'Ver pasos y resultado esperado'),
          r.steps ? h('div', { class: 'text-block small' }, r.steps) : null,
          r.expected ? h('div', { class: 'small mt' }, h('span', { class: 'muted' }, 'Esperado: '), r.expected) : null) : null,
        r.updated_by_name ? h('div', { class: 'tiny muted' }, `${r.updated_by_name} · ${fmtRelative(r.updated_at)}`) : null),
      h('div', { class: 'col run-actions' }, h('div', { class: 'res-seg' }, buttons), note));
  }

  function drawRun(remote = false) {
    if (!runData) return;
    const { run, results } = runData;
    const counts = {};
    for (const r of results) counts[r.result] = (counts[r.result] || 0) + 1;
    const list = results.filter(r => !resultFilter || r.result === resultFilter);
    const groups = {};
    for (const r of list) (groups[r.suite] ||= []).push(r);
    // Conserva el foco y el texto de la nota que se está escribiendo.
    const active = document.activeElement;
    if (remote && active && active.classList?.contains('run-note') && body.contains(active)) return;
    mount(body,
      h('div', { class: 'card' },
        h('div', { class: 'detail-head' },
          h('div', null, h('div', { class: 'key' }, run.build_number ? `Build ${run.build_number} · ${run.platform}` : 'Sin build concreta'), h('h1', null, run.title)),
          run.status === 'terminada' ? h('span', { class: 'badge ok' }, 'Terminada') : h('span', { class: 'badge info' }, 'En curso')),
        h('div', { class: 'kv' },
          h('span', null, 'Dispositivo'), h('span', null, [run.device, run.os_version].filter(Boolean).join(' · ') || '—'),
          h('span', null, 'Creada'), h('span', null, fmtDate(run.created_at)),
          run.finished_at ? [h('span', null, 'Terminada'), h('span', null, fmtDate(run.finished_at))] : null),
        h('div', { class: 'mt' }, runProgress(run)),
        run.total ? h('div', { class: 'mt' }, bar(100 * run.executed / run.total)) : null),
      h('div', { class: 'filters mt' }, h('div', { class: 'group' }, h('span', null, 'Mostrar'),
        h('button', { class: 'chip' + (!resultFilter ? ' on' : ''), onclick: () => { resultFilter = ''; drawRun(); } }, 'Todos', h('span', { class: 'muted tiny' }, results.length)),
        Object.entries(TEST_RESULTS).map(([k, l]) => h('button', { class: 'chip' + (resultFilter === k ? ' on' : ''), onclick: () => { resultFilter = k; drawRun(); } }, l, h('span', { class: 'muted tiny' }, counts[k] || 0))))),
      list.length ? Object.entries(groups).map(([suite, items]) => h('div', { class: 'list-card mb' },
        h('div', { class: 'card-head' }, h('h3', null, suite), h('span', { class: 'muted small' }, items.length)),
        items.map(resultRow))) : emptyState('Ningún caso con este resultado', ''));
  }

  function drawShell() {
    const head = runId
      ? pageHead('Ejecución de pruebas', h('button', { class: 'btn', onclick: closeRun }, icon('left'), 'Volver a planes de prueba'))
      : pageHead('Planes de prueba',
        canEdit ? h('button', { class: 'btn', onclick: () => { tab = 'casos'; nav({ tab }); drawMain(); caseForm(null); } }, icon('plus'), 'Nuevo caso') : null,
        canEdit ? h('button', { class: 'btn primary', onclick: () => runForm(null) }, icon('play'), 'Nueva ejecución') : null);
    mount(root, head, runId ? null : tabBar, body);
  }

  drawShell();
  await load();
  const pre = Number(ctx.query.get('build'));
  if (pre && !runId && canEdit && cases) runForm(pre);
  ctx.onChanges(['test', 'build', 'incident'], () => load(true));
}

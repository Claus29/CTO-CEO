// Builds: compilaciones por versión, plataforma y canal, con las incidencias encontradas y corregidas en cada una.
import { api } from '../api.js';
import { can, withProject } from '../state.js';
import { BUILD_CHANNELS, PLATFORMS, STORE_STATUSES, STORES, toOptions } from '../labels.js';
import {
  emptyState, errorBox, field, fmtDate, h, icon, loading, modal, mount, pageHead, priorityBadge, selectEl, statusBadge,
  table, todayISO, uid,
} from '../ui.js';
import { kindBadge, loadVersions, multiProject, newProjectId, projectField, requireFields, submit, versionOptions } from './_plan_shared.js';

export default async function render(ctx) {
  const { root } = ctx;
  const canEdit = can('calidad.editar');
  let builds = null, versions = [];
  const f = { version: ctx.query.get('version') || '', platform: '', channel: '' };
  const filtersBox = h('div', { class: 'filters' });
  const listBox = h('div', null, loading());

  async function load() {
    try {
      const [b, v] = await Promise.all([api.get(`/api/builds?${withProject({ version_id: f.version })}`), loadVersions()]);
      if (!ctx.alive()) return;
      builds = b.items; versions = v;
    } catch (err) { mount(listBox, errorBox(err, load)); return; }
    draw();
  }

  function drawFilters() {
    const chips = (key, labels) => [h('button', { class: 'chip' + (!f[key] ? ' on' : ''), onclick: () => { f[key] = ''; draw(); } }, 'Todas'),
      ...Object.entries(labels).map(([k, l]) => h('button', { class: 'chip' + (f[key] === k ? ' on' : ''), onclick: () => { f[key] = k; draw(); } }, l))];
    const vsel = selectEl([['', 'Todas las versiones'], ...versions.filter(v => withProjectOk(v.project_id)).map(v => [String(v.id), `${multiProject() ? v.project_key + ' · ' : ''}${v.name}`])], f.version, { style: { width: 'auto' } });
    vsel.addEventListener('change', () => {
      f.version = vsel.value;
      history.replaceState(null, '', f.version ? `#/builds?version=${f.version}` : '#/builds');
      mount(listBox, loading()); load();
    });
    mount(filtersBox,
      h('div', { class: 'group' }, h('span', null, 'Versión'), vsel),
      h('div', { class: 'group' }, h('span', null, 'Plataforma'), chips('platform', PLATFORMS)),
      h('div', { class: 'group' }, h('span', null, 'Canal'), chips('channel', BUILD_CHANNELS)));
  }

  function draw() {
    drawFilters();
    const list = builds.filter(b => (!f.platform || b.platform === f.platform) && (!f.channel || b.channel === f.channel));
    if (!builds.length) {
      mount(listBox, emptyState('No hay builds registradas', canEdit ? 'Registra cada build que se entrega a pruebas o a las tiendas con «Nueva build».' : ''));
      return;
    }
    mount(listBox, table([
      { label: 'Build', render: b => h('div', { class: 'title-cell' }, h('b', null, b.number), b.notes ? h('div', { class: 'sub ellipsis build-notes' }, b.notes) : null) },
      multiProject() ? { label: 'Proyecto', render: b => b.project_key, cls: 'hide-sm' } : null,
      { label: 'Versión', render: b => b.version_name || h('span', { class: 'muted' }, '—') },
      { label: 'Plataforma', render: b => b.platform_label },
      { label: 'Canal', render: b => kindBadge('channel', b.channel, b.channel_label) },
      { label: 'Fecha', render: b => h('span', { class: 'nowrap' }, fmtDate(b.built_on || b.created_at)) },
      { label: 'Incidencias', render: b => h('div', { class: 'row tiny' },
        h('span', { class: 'badge ' + (b.found_count ? 'danger' : 'neutral'), title: 'Encontradas en esta build' }, icon('bug', 12), b.found_count),
        h('span', { class: 'badge ' + (b.fixed_count ? 'ok' : 'neutral'), title: 'Corregidas en esta build' }, icon('check', 12), b.fixed_count)) },
      { label: 'Tiendas', render: b => b.submissions.length ? h('div', { class: 'row wrap' }, b.submissions.map(s => kindBadge('store', s.status, `${STORES[s.store] || s.store}: ${STORE_STATUSES[s.status] || s.status}`))) : h('span', { class: 'muted' }, '—'), cls: 'hide-sm' },
    ].filter(Boolean), list, { onRow: detail, empty: 'Ninguna build coincide con los filtros.' }));
  }

  const withProjectOk = (pid) => { const q = new URLSearchParams(withProject()); return !q.get('project') || Number(q.get('project')) === pid; };

  async function detail(b) {
    const incBox = h('div', null, can('incidencias.ver') ? loading() : null);
    modal({ title: `Build ${b.number} · ${b.platform_label}`, wide: true,
      body: h('div', { class: 'col gap-lg' },
        h('div', { class: 'kv' },
          multiProject() ? [h('span', null, 'Proyecto'), h('span', null, b.project_key)] : null,
          h('span', null, 'Versión'), h('span', null, b.version_id ? h('a', { href: `#/versiones?id=${b.version_id}` }, b.version_name) : '—'),
          h('span', null, 'Canal'), h('span', null, kindBadge('channel', b.channel, b.channel_label)),
          h('span', null, 'Fecha de la build'), h('span', null, fmtDate(b.built_on)),
          h('span', null, 'Registrada por'), h('span', null, `${b.created_by_name || '—'} · ${fmtDate(b.created_at)}`)),
        b.notes ? h('div', { class: 'text-block' }, b.notes) : null,
        h('div', null, h('div', { class: 'section-title' }, 'Publicaciones en tiendas'),
          b.submissions.length ? h('div', { class: 'row wrap' }, b.submissions.map(s => h('a', { href: '#/tiendas' }, kindBadge('store', s.status, `${STORES[s.store] || s.store}: ${STORE_STATUSES[s.status] || s.status}`))))
            : h('div', { class: 'small muted' }, 'Esta build no se ha enviado a ninguna tienda.')),
        incBox),
      actions: [
        can('calidad.editar') ? { label: 'Enviar a una tienda', onClick: () => { location.hash = `#/tiendas?build=${b.id}`; } } : null,
        can('calidad.editar') ? { label: 'Probar esta build', onClick: () => { location.hash = `#/pruebas?build=${b.id}`; } } : null,
        { label: 'Cerrar', kind: 'primary' }].filter(Boolean) });
    if (!can('incidencias.ver')) return;
    try {
      const params = { project: String(b.project_id) };
      if (b.version_id) params.version_id = b.version_id;
      const incs = (await api.get(`/api/incidents?${new URLSearchParams(params)}`)).items;
      const found = incs.filter(i => i.found_build_id === b.id);
      const fixed = incs.filter(i => i.fixed_build_id === b.id);
      const list = (items, empty) => items.length ? h('div', { class: 'list-card' }, items.map(i => h('a', { class: 'list-item', href: `#/incidencias/${i.key}` },
        h('span', { class: 'key' }, i.key), h('span', { class: 'grow ellipsis' }, i.title), priorityBadge(i.priority, i.priority_label), statusBadge(i.status, i.status_label))))
        : h('div', { class: 'small muted' }, empty);
      mount(incBox, h('div', { class: 'grid cols-2' },
        h('div', null, h('div', { class: 'section-title' }, `Incidencias encontradas (${found.length})`), list(found, 'Ninguna incidencia indica que se encontró en esta build.')),
        h('div', null, h('div', { class: 'section-title' }, `Incidencias corregidas (${fixed.length})`), list(fixed, 'Ninguna incidencia indica que se corrigió en esta build.'))));
    } catch (err) { mount(incBox, errorBox(err)); }
  }

  function form() {
    let projectId = newProjectId();
    const version = selectEl(versionOptions(versions, projectId), f.version && versions.some(v => String(v.id) === f.version && v.project_id === projectId) ? f.version : '');
    const proj = projectField(projectId, (v) => {
      projectId = Number(v);
      mount(version, ...versionOptions(versions, projectId).map(([val, l]) => h('option', { value: val }, l)));
    });
    const number = h('input', { maxlength: 60, placeholder: 'Ej.: 2.4.0 (153)' });
    const platform = selectEl(toOptions(PLATFORMS, 'Elige…'), f.platform || '');
    const channel = selectEl(toOptions(BUILD_CHANNELS), f.channel || 'interno');
    const built = h('input', { type: 'date', value: todayISO() });
    const notes = h('textarea', { rows: 3, placeholder: 'Qué incluye, enlace de descarga, cambios destacados…' });
    modal({ title: 'Nueva build', wide: true,
      body: h('div', { class: 'form-grid' },
        field('Número de build', number, { required: true, hint: 'Tal como aparece en la app o en la tienda.' }),
        field('Plataforma', platform, { required: true }),
        proj.el, field('Versión', version),
        field('Canal', channel, { hint: 'Interno (equipo), beta (testers) o producción (público).' }), field('Fecha', built),
        field('Notas', notes, { cls: 'span2' })),
      actions: [{ label: 'Cancelar' }, { label: 'Registrar build', kind: 'primary', onClick: () => {
        if (!requireFields([[number, 'el número de build'], [platform, 'la plataforma']])) return false;
        return submit(() => api.post('/api/builds', {
          uid: uid(), project_id: projectId, version_id: version.value ? Number(version.value) : null, number: number.value.trim(),
          platform: platform.value, channel: channel.value, built_on: built.value || null, notes: notes.value.trim(),
        }, { queue: true }), `Build ${number.value.trim()} registrada.`, load);
      } }] });
  }

  mount(root,
    pageHead('Builds', canEdit ? h('button', { class: 'btn primary', onclick: form }, icon('plus'), 'Nueva build') : null),
    filtersBox, listBox);
  await load();
  ctx.onChanges(['build', 'store', 'incident', 'version'], load);
}

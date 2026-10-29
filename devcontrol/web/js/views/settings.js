// Ajustes: avisos y reglas, SLA, plantillas de reporte, copias de seguridad, integraciones y el programa/servidor.
import { api } from '../api.js';
import { can, pollLocal, store } from '../state.js';
import { CATEGORIES, PRIORITIES, REPORT_FIELDS, INCIDENT_STATUSES } from '../labels.js';
import {
  busy, confirmDialog, emptyState, errorBox, field, fmtDateTime, fmtRelative, fmtSize, h, icon, loading, modal, mount,
  pageHead, selectEl, table, tabs, toast,
} from '../ui.js';

const TABS = [
  { key: 'general', label: 'General y avisos', icon: 'settings', perm: 'sistema.ajustes' },
  { key: 'sla', label: 'Plazos (SLA)', icon: 'clock', perm: 'sistema.ajustes' },
  { key: 'plantillas', label: 'Plantillas de reporte', icon: 'file', perm: 'sistema.ajustes' },
  { key: 'copias', label: 'Copias de seguridad', icon: 'backup', perm: 'sistema.copias' },
  { key: 'integraciones', label: 'Integraciones', icon: 'git', perm: 'sistema.integraciones' },
  { key: 'internet', label: 'Acceso por Internet', icon: 'wifi', perm: 'sistema.ajustes' },
  { key: 'servidor', label: 'Servidor y actualizaciones', icon: 'server' },
];
const WEEKDAYS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];
const HOURS = Array.from({ length: 24 }, (_, i) => [String(i), `${String(i).padStart(2, '0')}:00`]);

export default async function render(ctx) {
  const { root } = ctx;
  const allowed = TABS.filter(t => !t.perm || can(t.perm));
  const active = allowed.some(t => t.key === ctx.params.tab) ? ctx.params.tab : allowed[0].key;
  const body = h('div');
  mount(root, pageHead('Ajustes'), tabs(allowed, active, (k) => { location.hash = `#/ajustes/${k}`; }), body);
  ctx.setTitle(`Ajustes · ${allowed.find(t => t.key === active).label}`);
  const views = { general: generalTab, sla: slaTab, plantillas: templatesTab, copias: backupsTab, integraciones: githubTab, internet: internetTab, servidor: renderServerPanel };
  return views[active](body, ctx);
}

// Carga /api/settings y devuelve los valores actuales.
async function loadSettings() { return (await api.get('/api/settings')).settings; }

function numInput(value, min, max, attrs = {}) {
  return h('input', { type: 'number', min, max, step: 1, value: value ?? '', ...attrs });
}

function saveBar(btn, note) {
  return h('div', { class: 'adm-savebar' }, h('span', { class: 'small muted grow' }, note || ''), btn);
}

function settingsLoader(root, ctx, draw) {
  const run = async () => {
    mount(root, loading());
    try { draw(await loadSettings()); } catch (err) { mount(root, errorBox(err, run)); }
  };
  return run;
}

// ---------- General y avisos ----------
async function generalTab(root, ctx) {
  let roleList = [];
  try {
    if (can('sistema.usuarios')) roleList = (await api.get('/api/admin/roles')).items.map(r => ({ key: r.key, name: r.name }));
  } catch { /* se usa la lista del equipo */ }
  if (!roleList.length) {
    const seen = new Map();
    for (const u of store.users) if (u.role_key && !seen.has(u.role_key)) seen.set(u.role_key, u.role_label || u.role_key);
    roleList = [...seen].map(([key, name]) => ({ key, name }));
  }
  const run = settingsLoader(root, ctx, (s) => {
    const verification = h('input', { type: 'checkbox', checked: !!s.verification_required });
    const stale = numInput(s.stale_days, 0, 365);
    const dueSoon = numInput(s.due_soon_days, 0, 60);
    const dailyHour = selectEl(HOURS, String(s.daily_summary_hour));
    const weekly = selectEl(WEEKDAYS.map((d, i) => [String(i), d]), String(s.weekly_summary_weekday));
    const standupOn = h('input', { type: 'checkbox', checked: !!s.standup_enabled });
    const standupHour = selectEl(HOURS, String(s.standup_hour));
    const days = new Set(s.standup_weekdays || []);
    const dayChips = h('div', { class: 'row wrap' });
    const drawDays = () => mount(dayChips, WEEKDAYS.map((d, i) => h('button', { type: 'button', class: 'chip' + (days.has(i) ? ' on' : ''),
      onclick: () => { days.has(i) ? days.delete(i) : days.add(i); drawDays(); } }, d.slice(0, 3))));
    drawDays();
    const wip = {};
    for (const st of ['pendiente', 'en_progreso', 'bloqueada', 'en_verificacion']) wip[st] = numInput(s.wip_limits?.[st], 0, 999, { placeholder: 'Sin límite' });
    const pwMin = numInput(s.password_min_length, 8, 64);
    const req2fa = new Set(s.require_2fa_roles || []);
    for (const k of req2fa) if (!roleList.some(r => r.key === k)) roleList.push({ key: k, name: k });
    const roleChecks = roleList.map(r => ({ r, cb: h('input', { type: 'checkbox', checked: req2fa.has(r.key) }) }));
    const save = h('button', { class: 'btn primary', onclick: () => {
      const ints = [[stale, 'Días para considerar estancada'], [dueSoon, 'Días de aviso'], [pwMin, 'Longitud mínima de contraseña']];
      for (const [el, label] of ints) if (el.value === '' || !el.checkValidity()) { toast(`Revisa el valor de «${label}».`, 'error'); el.focus(); return; }
      const wipLimits = {};
      for (const [k, el] of Object.entries(wip)) if (el.value !== '') wipLimits[k] = Number(el.value);
      const payload = {
        verification_required: verification.checked, stale_days: Number(stale.value), due_soon_days: Number(dueSoon.value),
        daily_summary_hour: Number(dailyHour.value), weekly_summary_weekday: Number(weekly.value),
        standup_enabled: standupOn.checked, standup_hour: Number(standupHour.value), standup_weekdays: [...days].sort(),
        wip_limits: wipLimits, password_min_length: Number(pwMin.value),
        require_2fa_roles: roleChecks.filter(x => x.cb.checked).map(x => x.r.key),
      };
      busy(save, () => api.put('/api/settings', payload), 'Ajustes guardados.').then((r) => {
        Object.assign(store.settings, { verification_required: r.settings.verification_required, wip_limits: r.settings.wip_limits,
          password_min_length: r.settings.password_min_length, require_2fa_roles: r.settings.require_2fa_roles });
      }).catch(() => {});
    } }, 'Guardar cambios');
    mount(root, h('div', { class: 'col gap-lg adm-narrow' },
      h('div', { class: 'card' }, h('h3', null, 'Forma de trabajar'),
        h('label', { class: 'check' }, verification, 'Lo resuelto debe verificarlo quien lo creó (o QA) antes de darse por cerrado'),
        h('p', { class: 'tiny muted adm-indent' }, 'Si lo desactivas, al resolver una incidencia o tarea queda cerrada directamente.'),
        h('div', { class: 'section-title' }, 'Límites de trabajo en curso (WIP)'),
        h('p', { class: 'tiny muted' }, 'Máximo de elementos por columna del tablero. Se avisa al superarlo; vacío = sin límite.'),
        h('div', { class: 'grid cols-4 adm-gap-sm' }, Object.entries(wip).map(([k, el]) => field(INCIDENT_STATUSES[k] || k, el)))),
      h('div', { class: 'card' }, h('h3', null, 'Avisos automáticos'),
        h('div', { class: 'form-grid' },
          field('Días sin cambios para considerar algo «estancado»', stale, { hint: '0 = no avisar de incidencias estancadas.' }),
          field('Días de antelación para avisar antes de una fecha objetivo', dueSoon),
          field('Hora del resumen diario', dailyHour),
          field('Día del resumen semanal', weekly))),
      h('div', { class: 'card' }, h('h3', null, 'Daily asíncrono'),
        h('label', { class: 'check' }, standupOn, 'Recordar a cada persona que rellene su daily'),
        h('div', { class: 'form-grid mt' }, field('Hora del recordatorio', standupHour), field('Días', dayChips))),
      h('div', { class: 'card' }, h('h3', null, 'Seguridad'),
        h('div', { class: 'form-grid' }, field('Longitud mínima de contraseña', pwMin, { hint: 'Entre 8 y 64 caracteres. Se aplica a las contraseñas nuevas.' })),
        h('div', { class: 'section-title' }, 'Roles que deben usar la verificación en dos pasos'),
        h('p', { class: 'tiny muted' }, 'Quien tenga estos roles tendrá que activarla la próxima vez que entre; no podrá seguir sin hacerlo.'),
        h('div', { class: 'adm-checks' }, roleChecks.map(({ r, cb }) => h('label', { class: 'check' }, cb, r.name)))),
      saveBar(save, 'Los cambios se aplican a todo el equipo.')));
    stale.focus();
  });
  await run();
}

// ---------- SLA ----------
async function slaTab(root, ctx) {
  const human = (h_) => { const n = Number(h_); if (!n) return 'sin plazo'; return n % 24 === 0 ? `${n / 24} ${n / 24 === 1 ? 'día' : 'días'}` : `${n} h`; };
  const run = settingsLoader(root, ctx, (s) => {
    const enabled = h('input', { type: 'checkbox', checked: !!s.sla_enabled });
    const rows = Object.entries(PRIORITIES).map(([p, label]) => {
      const rule = s.sla?.[p] || {};
      const resp = numInput(rule.response_hours, 0, 10000);
      const res = numInput(rule.resolve_hours, 0, 100000);
      const hint1 = h('span', { class: 'tiny muted' }, human(resp.value));
      const hint2 = h('span', { class: 'tiny muted' }, human(res.value));
      resp.addEventListener('input', () => { hint1.textContent = human(resp.value); });
      res.addEventListener('input', () => { hint2.textContent = human(res.value); });
      return { p, label, resp, res, hint1, hint2 };
    });
    const save = h('button', { class: 'btn primary', onclick: () => {
      const sla = {};
      for (const r of rows) {
        if (!r.resp.checkValidity() || !r.res.checkValidity()) { toast(`Revisa las horas de la prioridad ${r.label}.`, 'error'); return; }
        sla[r.p] = { response_hours: Number(r.resp.value) || 0, resolve_hours: Number(r.res.value) || 0 };
      }
      busy(save, () => api.put('/api/settings', { sla_enabled: enabled.checked, sla }), 'Plazos guardados.')
        .then((r) => { store.settings.sla = r.settings.sla; store.settings.sla_enabled = r.settings.sla_enabled; }).catch(() => {});
    } }, 'Guardar plazos');
    mount(root, h('div', { class: 'col gap-lg adm-narrow' },
      h('div', { class: 'card' },
        h('h3', null, 'Plazos por prioridad'),
        h('p', { class: 'small muted' }, 'Cuánto se tarda como máximo en dar una primera respuesta (asignar, comentar o empezar) y en resolver una incidencia, según su prioridad. Se avisa a los responsables cuando un plazo está en riesgo y se mide en «Informes».'),
        h('label', { class: 'check mb' }, enabled, 'Activar los plazos (SLA)'),
        h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
          h('thead', null, h('tr', null, h('th', null, 'Prioridad'), h('th', null, 'Primera respuesta (horas)'), h('th', null, 'Resolución (horas)'))),
          h('tbody', null, rows.map(r => h('tr', null,
            h('td', null, h('span', { class: `badge p-${r.p}` }, h('span', { class: `prio-dot ${r.p}` }), r.label)),
            h('td', null, h('div', { class: 'row' }, h('div', { class: 'adm-num' }, r.resp), r.hint1)),
            h('td', null, h('div', { class: 'row' }, h('div', { class: 'adm-num' }, r.res), r.hint2))))))),
        h('p', { class: 'tiny muted mt' }, 'Pon 0 para no fijar un plazo. Las horas son de reloj (incluyen noches y fines de semana).')),
      saveBar(save)));
  });
  await run();
}

// ---------- Plantillas de reporte ----------
async function templatesTab(root, ctx) {
  const STATES = [['no', 'No se pide'], ['recommended', 'Recomendado'], ['required', 'Obligatorio']];
  const run = settingsLoader(root, ctx, (s) => {
    const state = {};
    for (const cat of Object.keys(CATEGORIES)) {
      const t = s.report_templates?.[cat] || {};
      state[cat] = {};
      for (const f of Object.keys(REPORT_FIELDS)) state[cat][f] = (t.required || []).includes(f) ? 'required' : (t.recommended || []).includes(f) ? 'recommended' : 'no';
    }
    const segFor = (cat, f) => {
      const seg = h('div', { class: 'seg adm-seg' });
      const draw = () => mount(seg, STATES.map(([k, l]) => h('button', { type: 'button', class: (state[cat][f] === k ? 'on ' : '') + `adm-st-${k}`,
        onclick: () => { state[cat][f] = k; draw(); } }, l)));
      draw();
      return seg;
    };
    const save = h('button', { class: 'btn primary', onclick: () => {
      const tpl = {};
      for (const [cat, fields] of Object.entries(state)) {
        tpl[cat] = { required: Object.keys(fields).filter(f => fields[f] === 'required'), recommended: Object.keys(fields).filter(f => fields[f] === 'recommended') };
      }
      busy(save, () => api.put('/api/settings', { report_templates: tpl }), 'Plantillas guardadas.')
        .then((r) => { store.settings.report_templates = r.settings.report_templates; }).catch(() => {});
    } }, 'Guardar plantillas');
    mount(root, h('div', { class: 'col gap-lg' },
      h('p', { class: 'small muted' }, 'Qué datos hay que rellenar al crear una incidencia de cada categoría. Los obligatorios no se pueden dejar vacíos salvo que se marquen como «no aplicable» explicando el motivo; los recomendados se piden pero se pueden omitir.'),
      h('div', { class: 'grid cols-2' }, Object.entries(CATEGORIES).map(([cat, label]) => h('div', { class: 'card' },
        h('h3', null, label),
        h('div', { class: 'col' }, Object.entries(REPORT_FIELDS).map(([f, fl]) => h('div', { class: 'row between adm-tplrow' }, h('span', { class: 'small' }, fl), segFor(cat, f))))))),
      saveBar(save)));
  });
  await run();
}

// ---------- Copias de seguridad ----------
async function backupsTab(root, ctx) {
  let data = null, mirror = null;
  const run = async () => {
    try {
      [data, mirror] = await Promise.all([api.get('/api/backups'), api.get('/api/backups/mirror').catch(() => null)]);
      draw();
    } catch (err) { mount(root, errorBox(err, run)); }
  };
  const countsText = (c) => {
    if (!c) return '—';
    const parts = [[c.incidents, 'incidencias'], [c.tasks, 'tareas'], [c.messages, 'mensajes'], [c.attachments, 'adjuntos']];
    return parts.filter(([n]) => n !== undefined).map(([n, l]) => `${n} ${l}`).join(' · ');
  };
  function draw() {
    const autoHours = numInput(data.settings.auto_backup_hours, 0, 720);
    const keep = numInput(data.settings.backup_keep, 1, 1000);
    const saveAuto = h('button', { class: 'btn', onclick: () => {
      if (!autoHours.checkValidity() || !keep.checkValidity() || autoHours.value === '' || keep.value === '') { toast('Revisa los valores de las copias automáticas.', 'error'); return; }
      busy(saveAuto, () => api.put('/api/settings', { auto_backup_hours: Number(autoHours.value), backup_keep: Number(keep.value) }), 'Copias automáticas configuradas.').catch(() => {});
    } }, 'Guardar');
    const create = h('button', { class: 'btn primary', onclick: () => busy(create, () => api.post('/api/backups'), 'Copia creada.').then(run).catch(() => {}) }, icon('plus'), 'Crear copia ahora');
    const total = data.items.reduce((s, b) => s + (b.size || 0), 0);
    mount(root, h('div', { class: 'col gap-lg' },
      h('div', { class: 'grid cols-2' },
        h('div', { class: 'card' },
          h('h3', null, 'Dónde se guardan'),
          h('div', { class: 'text-block mono small' }, data.folder),
          h('p', { class: 'tiny muted mt' }, 'Cada copia es un archivo .zip con toda la base de datos y los adjuntos. Guarda de vez en cuando una copia fuera de este equipo (un disco externo o la nube).'),
          h('div', { class: 'row wrap mt' }, create,
            store.local?.desktop ? h('button', { class: 'btn', onclick: (e) => busy(e.currentTarget, () => api.local('POST', '/local/open', { what: 'copias' })).catch(() => {}) }, icon('folder'), 'Abrir carpeta de copias') : null)),
        h('div', { class: 'card' },
          h('h3', null, 'Copias automáticas'),
          h('div', { class: 'form-grid' },
            field('Hacer una copia cada (horas)', autoHours, { hint: '0 = desactivadas (no recomendado).' }),
            field('Copias automáticas a conservar', keep, { hint: 'Las más antiguas se borran solas. Las manuales nunca.' })),
          h('div', { class: 'row end mt' }, saveAuto),
          h('p', { class: 'tiny muted' }, 'También se hace una copia al detener el servidor y antes de cada actualización o restauración.'))),
      mirror ? mirrorCard() : null,
      h('div', null,
        h('div', { class: 'row between mb' }, h('h3', null, `Copias disponibles (${data.items.length})`), h('span', { class: 'small muted' }, `Ocupan ${fmtSize(total)}`)),
        table([
          { label: 'Fecha', render: b => h('div', null, h('div', { class: 'title-cell' }, b.created_at ? fmtDateTime(b.created_at) : '—'),
            h('div', { class: 'tiny muted mono ellipsis adm-bname', title: b.name }, b.name)) },
          { label: 'Tipo', render: b => b.damaged ? h('span', { class: 'badge danger' }, icon('alert', 12), 'Dañada')
            : h('span', { class: `badge ${b.kind === 'manual' ? 'info' : b.kind === 'auto' ? 'neutral' : 'purple'}` }, b.kind_label) },
          { label: 'Hecha por', cls: 'hide-sm', render: b => h('span', { class: 'small' }, b.created_by || '—') },
          { label: 'Contenido', cls: 'hide-sm adm-counts', render: b => h('span', { class: 'small muted' }, countsText(b.counts)) },
          { label: 'Tamaño', render: b => h('span', { class: 'small nowrap' }, fmtSize(b.size)) },
          { label: '', render: b => h('div', { class: 'row end' },
            h('button', { class: 'btn sm ghost icon', title: 'Descargar (se guarda en Descargas)', 'aria-label': 'Descargar', onclick: (e) => busy(e.currentTarget, () => api.save(`/api/backups/${encodeURIComponent(b.name)}/download`, { folder: 'descargas' }))
              .then(r => toast(`Copia guardada en ${r.file || r.folder}`, 'ok')).catch(() => {}) }, icon('download', 14)),
            h('button', { class: 'btn sm', disabled: b.damaged, onclick: () => restore(b) }, icon('undo', 14), 'Restaurar'),
            h('button', { class: 'btn sm ghost icon', title: 'Borrar', onclick: (e) => remove(b, e.currentTarget) }, icon('trash', 14))) },
        ], data.items, { empty: 'Aún no hay copias. Crea una con «Crear copia ahora».' }))));
  }
  function mirrorCard() {
    const folder = h('input', { class: 'mono', value: mirror.folder || '', placeholder: 'C:\\Users\\tu\\OneDrive\\Copias DEV CONTROL', spellcheck: 'false' });
    const st = mirror.status || {};
    const save = h('button', { class: 'btn primary', onclick: () => busy(save, () => api.put('/api/backups/mirror', { folder: folder.value.trim(), copy_now: true }),
      folder.value.trim() ? 'Carpeta espejo guardada.' : 'Copia espejo desactivada.').then((m) => { mirror = m; draw(); }).catch(() => {}) }, 'Guardar');
    const now = h('button', { class: 'btn', disabled: !mirror.folder, onclick: () => busy(now, () => api.post('/api/backups/mirror/now'))
      .then((r) => { toast(r.item?.mirror?.ok ? 'Copia creada y guardada en la carpeta espejo.' : (r.item?.mirror?.error || 'Copia creada.'), r.item?.mirror?.ok ? 'ok' : 'error'); run(); }).catch(() => {}) },
    icon('backup', 14), 'Copiar ahora');
    const off = mirror.folder ? h('button', { class: 'btn ghost', onclick: () => busy(off, () => api.put('/api/backups/mirror', { folder: '' }), 'Copia espejo desactivada.')
      .then((m) => { mirror = m; draw(); }).catch(() => {}) }, 'Quitar') : null;
    return h('div', { class: 'card' },
      h('div', { class: 'card-head' }, h('h3', null, 'Copia en la nube (carpeta espejo)'),
        mirror.folder ? h('span', { class: `badge ${st.ok === false ? 'danger' : 'ok'}` }, st.ok === false ? 'Con errores' : 'Activada') : h('span', { class: 'badge neutral' }, 'Desactivada')),
      h('p', { class: 'small muted' }, 'Cada copia de seguridad se copia además a esta carpeta del equipo anfitrión. Si eliges una carpeta de OneDrive, Google Drive o Dropbox, las copias se suben solas a la nube y no se pierden aunque se estropee el PC. En la carpeta espejo se conservan las mismas copias automáticas que aquí y las 10 últimas de los demás tipos.'),
      field('Carpeta (en el equipo anfitrión)', folder, { hint: 'Ruta completa. Si no existe, se crea.' }),
      mirror.suggestions?.length ? h('div', { class: 'row wrap adm-gap-sm mt' }, h('span', { class: 'small muted' }, 'Sugerencias:'),
        ...mirror.suggestions.map(sg => h('button', { class: 'btn sm', title: sg.path, onclick: () => { folder.value = sg.path; folder.focus(); } }, icon('folder', 12), sg.label))) : null,
      mirror.folder && st.at ? h('div', { class: `callout ${st.ok ? 'ok' : 'danger'} small mt` },
        st.ok ? `Última copia guardada en la nube: ${st.name} (${fmtRelative(st.at)}).` : `Último intento (${fmtRelative(st.at)}): ${st.error}`) : null,
      h('div', { class: 'row end mt' }, off, now, save));
  }
  function restore(b) {
    const input = h('input', { class: 'mono', placeholder: b.name, autocomplete: 'off', spellcheck: 'false' });
    let btnRef = null;
    const m = modal({
      title: 'Restaurar una copia de seguridad', wide: true,
      body: h('div', { class: 'col gap-lg' },
        h('div', { class: 'callout danger' }, h('b', null, 'Todos los datos actuales se sustituirán por los de esta copia'),
          h('div', { class: 'small' }, `Volverán a estar como el ${b.created_at ? fmtDateTime(b.created_at) : '(fecha desconocida)'}. Lo que se haya hecho después desaparecerá de la vista de todo el equipo.`)),
        h('div', { class: 'callout ok small' }, icon('shield', 14), ' Antes de restaurar se guarda automáticamente una copia del estado actual (tipo «Previa a una restauración»), así que si te equivocas puedes volver atrás restaurando esa copia.'),
        h('div', { class: 'kv' }, h('span', null, 'Copia'), h('b', { class: 'mono small' }, b.name),
          h('span', null, 'Tipo'), h('span', null, b.kind_label), h('span', null, 'Contenido'), h('span', null, countsText(b.counts))),
        field('Para confirmar, escribe el nombre exacto de la copia', input, { hint: 'Puedes copiarlo de la línea de arriba.' })),
      actions: [{ label: 'Cancelar' }, { label: 'Restaurar', kind: 'danger', onClick: async () => {
        if (input.value.trim() !== b.name) { toast('El nombre no coincide.', 'error'); input.focus(); return false; }
        try {
          const r = await busy(null, () => api.post(`/api/backups/${encodeURIComponent(b.name)}/restore`, { confirm: b.name }));
          toast(`Copia restaurada. El estado anterior se guardó en ${r.previous_state_backup?.name || 'una copia previa'}.`, 'ok', { timeout: 12000 });
          run();
        } catch { return false; }
      } }],
    });
    btnRef = m.el.querySelector('.modal-foot .btn.danger');
    btnRef.disabled = true;
    input.addEventListener('input', () => { btnRef.disabled = input.value.trim() !== b.name; });
  }
  async function remove(b, btn) {
    if (!await confirmDialog({ title: 'Borrar copia', danger: true, confirmLabel: 'Borrar', message: `¿Borrar la copia del ${b.created_at ? fmtDateTime(b.created_at) : b.name}? El archivo se eliminará y no se podrá recuperar.` })) return;
    busy(btn, () => api.del(`/api/backups/${encodeURIComponent(b.name)}`), 'Copia borrada.').then(run).catch(() => {});
  }
  mount(root, loading());
  await run();
}

// ---------- Integraciones (GitHub) ----------
async function githubTab(root, ctx) {
  let cfg = null, vault = null;
  const run = async () => {
    try {
      cfg = await api.get('/api/integrations/github');
      vault = can('boveda.ver') ? await api.get('/api/vault/items').catch(() => null) : null;
      draw();
    } catch (err) { mount(root, errorBox(err, run)); }
  };
  function draw() {
    const repo = h('input', { value: cfg.repo || '', placeholder: 'propietario/repositorio', class: 'mono' });
    const enabled = h('input', { type: 'checkbox', checked: cfg.enabled });
    const minutes = numInput(cfg.poll_minutes, 2, 240);
    const tokenOpts = [['', 'Sin token (repositorio público)']];
    for (const it of vault?.items || []) tokenOpts.push([String(it.id), `${it.name}${it.username ? ' · ' + it.username : ''}`]);
    if (cfg.token_item_id && !tokenOpts.some(([v]) => v === String(cfg.token_item_id))) tokenOpts.push([String(cfg.token_item_id), `Secreto de la bóveda n.º ${cfg.token_item_id}`]);
    const token = selectEl(tokenOpts, cfg.token_item_id ? String(cfg.token_item_id) : '');
    const result = h('div');
    const showResult = (r) => {
      if (!r) return mount(result);
      if (r.error) return mount(result, h('div', { class: 'callout danger small' }, r.error));
      if (r.skipped) return mount(result, h('div', { class: 'callout warn small' }, 'No se ha consultado: falta el repositorio.'));
      mount(result, h('div', { class: 'callout ok small' }, `Consulta correcta${r.at ? ' (' + fmtRelative(r.at) + ')' : ''}: ${r.commits} commits y ${r.pulls} PR revisados, ${r.links} enlaces nuevos o actualizados, ${r.transitions} pasados a verificación.`));
    };
    showResult(cfg.last_error ? { error: cfg.last_error } : cfg.last_result);
    const save = h('button', { class: 'btn primary', onclick: () => {
      const r = repo.value.trim();
      if (r && !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(r)) { toast('El repositorio debe tener la forma propietario/nombre.', 'error'); repo.focus(); return; }
      if (!minutes.checkValidity() || minutes.value === '') { toast('Los minutos deben estar entre 2 y 240.', 'error'); minutes.focus(); return; }
      busy(save, () => api.put('/api/integrations/github', { repo: r, enabled: enabled.checked, poll_minutes: Number(minutes.value),
        token_item_id: token.value ? Number(token.value) : null }), 'Integración guardada.').then((c) => { cfg = c; draw(); }).catch(() => {});
    } }, 'Guardar');
    const poll = h('button', { class: 'btn', disabled: !cfg.repo, title: cfg.repo ? '' : 'Guarda primero el repositorio', onclick: () =>
      busy(poll, () => api.post('/api/integrations/github/poll')).then((r) => { showResult(r.error || r.skipped ? r : { ...r, at: new Date().toISOString() }); if (!r.error && !r.skipped) toast('GitHub consultado.', 'ok'); }).catch(() => {}) },
    icon('refresh', 14), 'Consultar ahora');
    mount(root, h('div', { class: 'col gap-lg adm-narrow' },
      h('div', { class: 'card' },
        h('div', { class: 'card-head' }, h('h3', null, 'GitHub'), cfg.enabled ? h('span', { class: 'badge ok' }, 'Activada') : h('span', { class: 'badge neutral' }, 'Desactivada')),
        h('p', { class: 'small muted' }, 'Enlaza los commits y las peticiones de cambios (PR) con las incidencias y tareas que mencionan en el título o el mensaje (por ejemplo «Arregla APP-012»). Un PR abierto pone el elemento «En progreso» y un PR fusionado lo pasa a «En verificación» (nunca a resuelto directamente). El anfitrión consulta GitHub cada pocos minutos, así que el servidor no tiene que estar abierto a Internet.'),
        h('div', { class: 'form-grid mt' },
          field('Repositorio', repo, { hint: 'Por ejemplo: miempresa/app-movil' }),
          field('Consultar cada (minutos)', minutes),
          field('Token de acceso', token, { cls: 'span2', hint: 'Solo hace falta para repositorios privados: guarda un token de solo lectura en la Bóveda y elígelo aquí.' }),
          h('label', { class: 'check span2' }, enabled, 'Consultar automáticamente')),
        token.value && !cfg.vault_unlocked ? h('div', { class: 'callout warn small mt' }, 'La bóveda está bloqueada: el token no se puede usar hasta que alguien la desbloquee en «Bóveda».') : null,
        !can('boveda.ver') ? h('p', { class: 'tiny muted mt' }, 'No tienes acceso a la bóveda, así que no puedes ver los nombres de los secretos.') : null,
        h('div', { class: 'row end mt' }, poll, save)),
      h('div', { class: 'card' }, h('h3', null, 'Última consulta'),
        h('div', { class: 'kv' }, h('span', null, 'Fecha'), h('span', null, cfg.last_poll ? `${fmtDateTime(cfg.last_poll)} (${fmtRelative(cfg.last_poll)})` : 'Nunca')),
        h('div', { class: 'mt' }, result))));
    repo.focus();
  }
  mount(root, loading());
  await run();
}

// ---------- Acceso por Internet (túnel de Cloudflare) ----------
async function internetTab(root, ctx) {
  let cfg = null;
  const run = async () => {
    try { cfg = await api.get('/api/internet'); draw(); } catch (err) { mount(root, errorBox(err, run)); }
  };
  function draw() {
    const address = h('input', { class: 'mono', value: cfg.address || '', placeholder: 'https://devcontrol.tudominio.com', spellcheck: 'false', autocomplete: 'off' });
    const enabled = h('input', { type: 'checkbox', checked: cfg.enabled });
    const result = h('div');
    const save = h('button', { class: 'btn primary', onclick: () => busy(save, () => api.put('/api/internet', { address: address.value.trim(), enabled: enabled.checked }),
      'Acceso por Internet guardado.').then((c) => { cfg = c; draw(); }).catch(() => {}) }, 'Guardar');
    const test = h('button', { class: 'btn', onclick: () => {
      if (!address.value.trim()) { toast('Escribe primero la dirección.', 'error'); address.focus(); return; }
      busy(test, () => api.post('/api/internet/test', { address: address.value.trim() })).then((r) => {
        mount(result, h('div', { class: `callout ${r.ok ? 'ok' : r.tunnel_ok ? 'warn' : 'danger'} small` }, r.message));
      }).catch(() => {});
    } }, icon('refresh', 14), 'Comprobar');
    const viaInternet = store.local?.server_info?.via_internet;
    const step = (...content) => h('li', null, ...content);
    mount(root, h('div', { class: 'col gap-lg adm-narrow' },
      h('div', { class: 'card' },
        h('div', { class: 'card-head' }, h('h3', null, 'Entrar desde fuera de la oficina'),
          cfg.enabled ? h('span', { class: 'badge ok' }, 'Activado') : h('span', { class: 'badge neutral' }, 'Desactivado')),
        h('p', { class: 'small muted' }, 'Con un túnel gratuito de Cloudflare los demás pueden usar DEV CONTROL desde cualquier sitio, sin VPN ni Tailscale: '
          + 'escriben una dirección https:// como servidor. Los datos y las copias siguen en este equipo anfitrión, que debe estar encendido.'),
        h('div', { class: 'form-grid mt' },
          field('Dirección pública', address, { cls: 'span2', hint: 'La que creaste en Cloudflare, por ejemplo https://devcontrol.tudominio.com' }),
          h('label', { class: 'check span2' }, enabled, 'Permitir entrar por Internet')),
        viaInternet ? h('div', { class: 'callout warn small mt' }, 'Estás conectado por Internet: si desactivas el acceso, perderás la conexión desde este equipo.') : null,
        h('div', { class: 'row end mt' }, test, save),
        h('div', { class: 'mt' }, result)),
      h('div', { class: 'card' },
        h('h3', null, 'Cómo se prepara (una sola vez, en el equipo anfitrión)'),
        h('ol', { class: 'small adm-steps' },
          step('En ', h('b', null, 'dash.cloudflare.com'), ' → ', h('b', null, 'Zero Trust'), ' → ', h('b', null, 'Networks → Tunnels'), ' → ', h('b', null, 'Create a tunnel'), ' (tipo «Cloudflared»). Ponle un nombre, por ejemplo «devcontrol».'),
          step('Elige ', h('b', null, 'Windows'), ', descarga el instalador de cloudflared y ejecuta en este equipo, como administrador, el comando que te muestra («cloudflared.exe service install …»). Queda como servicio de Windows y arranca solo.'),
          step('En ', h('b', null, 'Published application'), ' (antes «Public hostname»): Subdomain ', h('b', { class: 'mono' }, 'devcontrol'), ', tu dominio, Path vacío y en ', h('b', null, 'Service URL'), ' exactamente ', h('b', { class: 'mono' }, `http://127.0.0.1:${cfg.port}`), ' — con http, ', h('b', null, 'sin la «s»'), ', y no el 8080 del ejemplo (si te lo pide por separado: Type HTTP y URL ', h('span', { class: 'mono' }, `127.0.0.1:${cfg.port}`), '). Guarda.'),
          step('Aquí arriba escribe la dirección (https://devcontrol.tudominio.com), marca «Permitir entrar por Internet», pulsa ', h('b', null, 'Guardar'), ' y luego ', h('b', null, 'Comprobar'), '.'),
          step('Los demás, en la pantalla de inicio de sesión, escriben esa dirección en «Servidor». No tienen que instalar nada más.'))),
      h('div', { class: 'card' },
        h('h3', null, 'Bueno saberlo'),
        h('ul', { class: 'small adm-steps' },
          h('li', null, h('b', null, 'Seguridad: '), 'la dirección es pública, así que cualquiera puede ver la pantalla de inicio de sesión. Usa contraseñas largas y activa la verificación en dos pasos para todos en «General y avisos → Roles que deben usar la verificación en dos pasos». Los intentos fallidos se bloquean por IP.'),
          h('li', null, h('b', null, 'Funciones del anfitrión: '), 'quien entra por Internet nunca tiene los privilegios de este equipo (crear la primera cuenta, recuperación, detener el servidor).'),
          h('li', null, h('b', null, 'Llamadas: '), 'las llamadas de voz y vídeo no pasan por el túnel; desde Internet no aparecen. Siguen funcionando en la red local o con Tailscale.'),
          h('li', null, h('b', null, 'Adjuntos: '), 'por Internet el máximo es de 95 MB por archivo (límite del plan gratuito de Cloudflare).'),
          h('li', null, h('b', null, 'Siempre disponible: '), 'mientras DEV CONTROL está abierto, este equipo no se suspende (la pantalla sí se apaga). Si el PC se apaga, los demás verán «el anfitrión no responde» y sus cambios quedarán en cola hasta que vuelva.'),
          h('li', null, h('b', null, 'Copias: '), 'para tener las copias también en la nube, elige una carpeta de OneDrive o Google Drive en «Copias de seguridad → Copia en la nube».')))));
    address.focus();
  }
  mount(root, loading());
  await run();
}

// ---------- Servidor y actualizaciones (lo ve todo el mundo; también se usa en «Mi cuenta») ----------
// Acceso de solo lectura a un repositorio privado de GitHub (solo en el anfitrión; los demás equipos
// reciben las actualizaciones de él). La clave se guarda cifrada con la cuenta de Windows de este equipo.
function githubAccess(u) {
  const input = h('input', { type: 'password', placeholder: u.token_configured ? '•••••••• (guardada)' : 'github_pat_…', autocomplete: 'off' });
  const save = h('button', { class: 'btn sm primary', onclick: () => busy(save, async () => {
    if (!input.value.trim()) throw new Error('Pega la clave de acceso.');
    await api.local('POST', '/local/update/token', { token: input.value.trim() });
    input.value = '';
    await new Promise(r => setTimeout(r, 2500));
    await pollLocal();
  }, 'Clave guardada. Comprobando actualizaciones…').catch(() => {}) }, 'Guardar clave');
  const remove = h('button', { class: 'btn sm ghost', onclick: () => busy(remove, async () => {
    await api.local('POST', '/local/update/token', { token: '' }); await pollLocal();
  }, 'Clave quitada.').catch(() => {}) }, 'Quitar');
  return h('details', { class: 'notes mt', open: !u.token_configured && !!u.error },
    h('summary', null, 'Acceso a GitHub (repositorio privado) · ', u.token_configured ? h('b', { class: 'ok-text' }, 'configurado') : h('b', { class: 'warn-text' }, 'sin configurar')),
    h('div', { class: 'col mt' },
      h('p', { class: 'small' }, 'Si el repositorio de DEV CONTROL en GitHub es privado, este equipo (el anfitrión) necesita una clave de acceso de solo lectura para ver las versiones nuevas. Los demás equipos las reciben del anfitrión.'),
      h('ol', { class: 'small' },
        h('li', null, 'En GitHub: tu foto → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.'),
        h('li', null, 'Repository access: «Only select repositories» y elige el repositorio de DEV CONTROL.'),
        h('li', null, 'Permissions → Repository permissions → Contents: «Read-only». Nada más.'),
        h('li', null, 'Genera la clave, cópiala y pégala aquí.')),
      h('div', { class: 'row' }, input, save, u.token_configured ? remove : null),
      h('p', { class: 'tiny muted' }, 'Se guarda cifrada con tu cuenta de Windows en este equipo y solo se usa para descargar las actualizaciones.')));
}

export async function renderServerPanel(root, ctx) {
  const box = h('div', { class: 'col gap-lg' });
  mount(root, box);
  let sig = '';
  const fresh = async () => { await pollLocal(); draw(true); };
  function draw(force) {
    const s = store.local;
    const nextSig = JSON.stringify(s ? { ...s, last_ok_at: null, server_info: { ...(s.server_info || {}), server_time: null } } : null);
    if (!force && nextSig === sig) return;
    sig = nextSig;
    if (!s) { mount(box, h('div', { class: 'callout danger' }, 'No se puede contactar con el programa DEV CONTROL de este equipo.')); return; }
    const host = s.host;
    const u = s.update || {};
    const cards = [];
    cards.push(h('div', { class: 'card' },
      h('div', { class: 'card-head' }, h('h3', null, 'Conexión'), s.reachable ? h('span', { class: 'badge ok' }, '● Conectado') : h('span', { class: 'badge danger' }, '● Sin conexión')),
      h('div', { class: 'kv' },
        h('span', null, 'Servidor'), h('b', null, s.server_label || s.server || '—', s.server_is_default ? h('span', { class: 'tiny muted' }, ' (el predeterminado de este equipo)') : null),
        h('span', null, 'Última conexión correcta'), h('span', null, s.last_ok_at ? fmtRelative(s.last_ok_at) : '—'),
        h('span', null, 'Versión del servidor'), h('span', null, s.server_info?.version || '—'),
        h('span', null, 'Versión de este equipo'), h('span', null, s.version || '—'),
        h('span', null, 'Nombre de este equipo'), h('span', null, s.computer_name || '—'),
        h('span', null, 'Cambios por enviar'), h('span', null, s.pending_count ? h('b', { class: 'warn-text' }, s.pending_count) : 'Ninguno')),
      !s.reachable && s.last_error ? h('div', { class: 'callout danger small mt' }, s.last_error) : null,
      s.server_info?.version && s.version && s.server_info.version !== s.version ? h('div', { class: 'callout warn small mt' }, 'La versión de este equipo y la del servidor no coinciden. Conviene actualizar el que vaya por detrás.') : null));
    if (s.is_host) {
      const running = !!host?.running;
      const startBtn = h('button', { class: 'btn primary', onclick: () => busy(startBtn, () => api.local('POST', '/local/server/start'), 'Servidor iniciado.').then(fresh).catch(fresh) }, icon('play', 14), 'Iniciar servidor');
      const stopBtn = h('button', { class: 'btn danger', onclick: async () => {
        if (!await confirmDialog({ title: 'Detener el servidor', danger: true, confirmLabel: 'Detener',
          message: 'Nadie del equipo (tampoco tú) podrá trabajar con el servidor hasta que lo vuelvas a iniciar. Lo que hagan se guardará en sus equipos y se enviará al reconectar.\n\nAl detenerlo se hace una copia de seguridad.' })) return;
        busy(stopBtn, () => api.local('POST', '/local/server/stop'), 'Servidor detenido.').then(fresh).catch(fresh);
      } }, icon('stop', 14), 'Detener servidor');
      const listening = (host?.listening || []).map(l => l.startsWith('0.0.0.0') ? `${l} (todas las redes privadas: Tailscale, VPN o red local)` : l);
      cards.push(h('div', { class: 'card' },
        h('div', { class: 'card-head' }, h('h3', null, 'Este equipo es el anfitrión'), running ? h('span', { class: 'badge ok' }, 'Servidor en marcha') : h('span', { class: 'badge danger' }, 'Servidor detenido')),
        h('p', { class: 'small muted' }, 'El servidor del equipo funciona en este ordenador. Tiene que estar encendido para que los demás puedan trabajar juntos.'),
        h('div', { class: 'kv' },
          h('span', null, 'Escuchando en'), h('span', null, listening.length ? listening.map(l => h('div', { class: 'mono small' }, l)) : '—'),
          h('span', null, 'Puerto'), h('span', null, host?.port ?? '—'),
          h('span', null, 'Carpeta de datos'), h('span', { class: 'mono small adm-break' }, host?.data_dir || '—'),
          h('span', null, 'Carpeta de copias'), h('span', { class: 'mono small adm-break' }, host?.backups_dir || '—')),
        (host?.listen_errors || []).length ? h('div', { class: 'callout warn small mt' }, h('b', null, 'Problemas para escuchar en algunas redes:'), host.listen_errors.map(e => h('div', null, e))) : null,
        host?.error ? h('div', { class: 'callout danger small mt' }, host.error) : null,
        host?.recovery ? h('div', { class: 'callout warn small mt' }, 'El servidor está en modo recuperación: falta la base de datos. Cierra sesión y vuelve a abrir el programa para elegir qué copia restaurar.') : null,
        h('div', { class: 'row end mt' }, running ? stopBtn : startBtn)));
    }
    // Actualizaciones
    const working = ['checking', 'downloading', 'verifying', 'preparing', 'applying'].includes(u.state);
    const checkBtn = h('button', { class: 'btn', disabled: working, onclick: () => busy(checkBtn, async () => {
      await api.local('POST', '/local/update/check');
      for (let i = 0; i < 6; i++) { await new Promise(r => setTimeout(r, 800)); await pollLocal(); if (store.local?.update?.state !== 'checking') break; }
      draw(true);
    }).catch(() => {}) }, icon('refresh', 14), 'Comprobar ahora');
    const applyBtn = h('button', { class: 'btn primary', disabled: working || !u.can_apply, onclick: async () => {
      if (!await confirmDialog({ title: `Instalar DEV CONTROL ${u.latest}`, confirmLabel: 'Instalar ahora',
        message: `DEV CONTROL se cerrará, se instalará la versión ${u.latest} y volverá a abrirse solo.${s.is_host ? '\n\nComo este equipo es el anfitrión, el servidor se detendrá un momento y antes se hará una copia de seguridad.' : ''}` })) return;
      busy(applyBtn, () => api.local('POST', '/local/update/apply')).then(fresh).catch(fresh);
    } }, icon('download', 14), 'Instalar actualización');
    const last = u.last_update;
    const progress = u.state === 'downloading' && u.progress != null ? Math.round(u.progress * 100) : null;
    cards.push(h('div', { class: 'card' },
      h('div', { class: 'card-head' }, h('h3', null, 'Actualizaciones'),
        u.available ? h('span', { class: 'badge accent' }, 'Hay una versión nueva') : h('span', { class: 'badge neutral' }, 'Al día')),
      h('div', { class: 'kv' },
        h('span', null, 'Versión instalada'), h('b', null, u.current || s.version || '—'),
        h('span', null, 'Última publicada'), h('span', null, u.latest || '—', u.published_at ? h('span', { class: 'tiny muted' }, ` · ${fmtDateTime(u.published_at)}`) : null),
        h('span', null, 'Última comprobación'), h('span', null, u.last_check ? fmtRelative(u.last_check) : 'Nunca'),
        u.state_label ? h('span', null, 'Estado') : null, u.state_label ? h('span', null, u.state_label) : null),
      progress !== null ? h('div', { class: 'progress mt' }, h('i', { style: { width: `${progress}%` } })) : null,
      u.error ? h('div', { class: 'callout danger small mt' }, u.error) : null,
      u.mandatory && u.reason ? h('div', { class: 'callout warn small mt' }, u.reason) : null,
      last ? h('div', { class: `callout small mt ${last.result === 'ok' ? 'ok' : 'warn'}` },
        last.result === 'ok' ? `Actualizado correctamente de ${last.from || '?'} a ${last.to || u.current}.`
          : last.result === 'revertida' ? `La actualización falló y se volvió a la versión ${last.reverted_to || u.current}. ${last.error || ''}`
            : `La última actualización falló: ${last.error || 'motivo desconocido'}.`,
        h('button', { class: 'btn sm ghost adm-ml', onclick: () => api.local('POST', '/local/update/ack').then(fresh).catch(() => {}) }, 'Entendido')) : null,
      u.notes ? h('details', { class: 'notes mt' }, h('summary', null, `Novedades de la versión ${u.latest || ''}`), h('div', { class: 'pre small' }, u.notes)) : null,
      !u.installed ? h('p', { class: 'tiny muted mt' }, 'Las actualizaciones automáticas solo funcionan en el programa instalado en Windows.') : null,
      u.available && !u.can_apply && u.manual_url ? h('p', { class: 'small mt' }, 'Puedes ', h('a', { href: u.manual_url, target: '_blank', rel: 'noopener noreferrer' }, 'descargarla a mano'), '.') : null,
      (u.log_tail || []).length ? h('details', { class: 'notes mt' }, h('summary', null, 'Registro de actualizaciones'), h('div', { class: 'pre tiny mono adm-log' }, u.log_tail.join('\n'))) : null,
      h('p', { class: 'tiny muted mt' }, u.auto
        ? (u.is_host ? 'Las versiones nuevas se instalan solas: este equipo las busca cada 30 minutos, avisa 2 minutos antes y nunca actualiza durante una llamada. Después se actualiza el resto del equipo.'
          : 'Las versiones nuevas se instalan solas en cuanto el anfitrión se actualiza (con un aviso de 2 minutos y nunca durante una llamada).')
        : 'La instalación automática está desactivada en config.ini ([actualizaciones] automaticas = no).'),
      u.auto_in != null ? h('div', { class: 'callout info small mt' }, `La versión ${u.auto_version} se instalará sola en ${Math.max(1, Math.round(u.auto_in / 60))} min.`) : null,
      u.auto_blocked ? h('div', { class: 'callout warn small mt' }, u.auto_blocked) : null,
      u.is_host ? githubAccess(u) : h('p', { class: 'tiny muted mt' }, 'Este equipo recibe las actualizaciones del anfitrión por vuestra red privada (no necesita acceso a GitHub). Cada actualización se comprueba con la firma del proyecto antes de instalarse.'),
      h('div', { class: 'row end mt' }, checkBtn, u.available ? applyBtn : null)));
    // Carpetas
    const folders = [['datos', 'Documentos\\DEV CONTROL', 'folder'], ['exportaciones', 'Exportaciones', 'download'], ['copias', 'Copias de seguridad', 'backup'],
      ['registros', 'Registros', 'file'], ['configuracion', 'Configuración', 'settings']];
    cards.push(h('div', { class: 'card' }, h('h3', null, 'Carpetas de este equipo'),
      s.desktop ? h('div', { class: 'row wrap' }, folders.map(([what, label, ic]) => h('button', { class: 'btn', onclick: (e) =>
        busy(e.currentTarget, () => api.local('POST', '/local/open', { what })).catch(() => {}) }, icon(ic, 14), label)))
        : h('p', { class: 'small muted' }, 'Se pueden abrir desde el programa de escritorio de DEV CONTROL. Están dentro de Documentos\\DEV CONTROL.'),
      s.config_file ? h('p', { class: 'tiny muted mt' }, 'Archivo de configuración: ', h('span', { class: 'mono' }, s.config_file)) : null));
    mount(box, h('div', { class: 'grid cols-2' }, cards));
  }
  draw(true);
  ctx.on('local-status', () => draw(false));
}

void emptyState;

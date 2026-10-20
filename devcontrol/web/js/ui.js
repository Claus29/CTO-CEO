// Utilidades de interfaz: creación segura de elementos, formatos, avisos y diálogos.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'value') el.value = v;
      else if (k === 'checked') el.checked = !!v;
      else if (k === 'html') throw new Error('html no permitido');
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
export function mount(el, ...children) { clear(el); append(el, children); return el; }
export const $ = (sel, root = document) => root.querySelector(sel);

export function uid() {
  if (crypto.randomUUID) return crypto.randomUUID().replace(/-/g, '');
  const a = new Uint8Array(16); crypto.getRandomValues(a);
  return Array.from(a, b => b.toString(16).padStart(2, '0')).join('');
}

// ---------- iconos (SVG en línea, sin dependencias) ----------
const ICONS = {
  dashboard: 'M3 13h8V3H3zM13 21h8v-8h-8zM3 21h8v-6H3zM13 3v8h8V3z',
  bug: 'M8 9V7a4 4 0 0 1 8 0v2M5 13H2m20 0h-3M6 18l-2 2m14-2 2 2M6 8 4 6m14 2 2-2M12 21a6 6 0 0 1-6-6v-4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v4a6 6 0 0 1-6 6zM12 13v8',
  chat: 'M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z',
  board: 'M4 4h5v16H4zM10 4h5v10h-5zM16 4h4v7h-4z',
  bulb: 'M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V16h5v-.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z',
  week: 'M4 6h16M4 12h10M4 18h7M17 15l2 2 4-4',
  calendar: 'M4 5h16v16H4zM4 9h16M9 3v4M15 3v4',
  tag: 'M3 12V4h8l10 10-8 8L3 12zM7.5 7.5h.01',
  activity: 'M3 12h4l3 8 4-16 3 8h4',
  backup: 'M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3',
  plus: 'M12 5v14M5 12h14',
  check: 'M20 6 9 17l-5-5',
  x: 'M18 6 6 18M6 6l12 12',
  clip: 'M21.4 11.1 12.2 20.3a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
  upload: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
  eye: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  eyeoff: 'M17.9 17.9A10.1 10.1 0 0 1 12 20C5 20 1 12 1 12a18.5 18.5 0 0 1 5.1-5.9M9.9 4.2A9.1 9.1 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.2 3.2M1 1l22 22',
  lock: 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4',
  play: 'M6 4l14 8-14 8z',
  undo: 'M3 7v6h6M3 13a9 9 0 1 0 3-7.7L3 8',
  reply: 'M9 17l-5-5 5-5M4 12h11a5 5 0 0 1 5 5v2',
  edit: 'M12 20h9M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4z',
  share: 'M4 12v8h16v-8M16 6l-4-4-4 4M12 2v13',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6',
  sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  left: 'M15 18l-6-6 6-6',
  right: 'M9 18l6-6-6-6',
  alert: 'M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  trash: 'M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6',
  refresh: 'M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15',
  pdf: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M9 15h6M9 18h6M9 12h2',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  wifi: 'M5 12.6a10 10 0 0 1 14 0M8.5 16.1a5 5 0 0 1 7 0M2 8.8a15 15 0 0 1 20 0M12 20h.01',
  task: 'M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11',
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  key: 'M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4',
  book: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5',
  scale: 'M12 3v18M5 7h14M5 7l-3 7a4 4 0 0 0 6 0zM19 7l-3 7a4 4 0 0 0 6 0zM8 21h8',
  chart: 'M3 3v18h18M7 15l4-4 3 3 5-6',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
  flask: 'M9 3h6M10 3v6L4 19a2 2 0 0 0 1.7 3h12.6a2 2 0 0 0 1.7-3L14 9V3M7 15h10',
  box: 'M21 16V8l-9-5-9 5v8l9 5 9-5zM3.3 7 12 12l8.7-5M12 22V12',
  store: 'M3 9l1-5h16l1 5M3 9h18v11H3zM9 20v-6h6v6',
  repeat: 'M17 1l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3',
  map: 'M1 6v16l7-4 8 4 7-4V2l-7 4-8-4-7 4zM8 2v16M16 6v16',
  link: 'M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7',
  git: 'M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9',
  vote: 'M7 10v12M15 5.9 14 10h5.8a2 2 0 0 1 2 2.3l-1.4 8A2 2 0 0 1 18.4 22H7V10l4-8a3 3 0 0 1 4 3.9z',
  flag: 'M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zM4 22v-7',
  server: 'M2 4h20v6H2zM2 14h20v6H2zM6 7h.01M6 17h.01',
  rocket: 'M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2.1-.1-2.9a2.2 2.2 0 0 0-2.9-.1zM12 15l-3-3a22 22 0 0 1 2-3.9A12.9 12.9 0 0 1 22 2c0 2.7-.8 7.5-6 11a22.4 22.4 0 0 1-4 2zM9 12H4s.6-3 2-4c1.6-1.1 5 0 5 0M12 15v5s3-.6 4-2c1.1-1.6 0-5 0-5',
  menu: 'M3 6h18M3 12h18M3 18h18',
  dots: 'M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM19 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM5 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z',
  verify: 'M9 12l2 2 4-4M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  sync: 'M21 12a9 9 0 0 1-15 6.7L3 16M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M3 21v-5h5',
  copy: 'M9 9h13v13H9zM5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1',
  stop: 'M6 6h12v12H6z',
};
export function icon(name, size) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  if (size) { svg.style.width = size + 'px'; svg.style.height = size + 'px'; }
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  p.setAttribute('d', ICONS[name] || ICONS.file);
  svg.appendChild(p);
  return svg;
}

// ---------- formatos ----------
const pad = n => String(n).padStart(2, '0');
export function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function fmtDate(value) {
  if (!value) return '—';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) { const [y, m, d] = value.split('-'); return `${d}/${m}/${y}`; }
  const d = new Date(value);
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
}
const DAYS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
const MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
export { DAYS, MONTHS };
export function fmtDayLabel(iso) {
  const d = new Date(iso);
  const today = new Date(); const y = new Date(); y.setDate(y.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return 'Hoy';
  if (d.toDateString() === y.toDateString()) return 'Ayer';
  return `${DAYS[d.getDay()]}, ${d.getDate()} de ${MONTHS[d.getMonth()]} de ${d.getFullYear()}`;
}
export function fmtRelative(iso) {
  if (!iso) return '—';
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return 'hace un momento';
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  if (s < 86400 * 30) return `hace ${Math.round(s / 86400)} d`;
  return fmtDate(iso);
}
export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const d = Math.floor(seconds / 86400), hh = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d} d ${hh} h`;
  if (hh) return `${hh} h ${m} min`;
  return `${Math.max(m, seconds > 0 ? 1 : 0)} min`;
}
export function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}
export function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
export function isoDate(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
export function mondayOf(dateISO) {
  const [y, m, d] = dateISO.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  const wd = (dt.getDay() + 6) % 7;
  dt.setDate(dt.getDate() - wd);
  return isoDate(dt);
}
export function addDays(dateISO, n) {
  const [y, m, d] = dateISO.split('-').map(Number);
  const dt = new Date(y, m - 1, d + n);
  return isoDate(dt);
}
export function initials(name) {
  return (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(s => s[0].toUpperCase()).join('');
}

// ---------- insignias ----------
export function priorityBadge(p, label) { return h('span', { class: `badge p-${p}` }, h('span', { class: `prio-dot ${p}` }), label || p); }
export function statusBadge(s, label) {
  return h('span', { class: `badge s-${s}` }, s === 'bloqueada' ? icon('lock', 12) : null, label || s);
}
export function avatar(user, online) {
  return h('span', { class: `avatar r-${user?.role_key || 'x'}`, title: user?.display_name || '' }, initials(user?.display_name),
    online === undefined ? null : h('span', { class: 'dot' + (online ? ' on' : '') }));
}

// ---------- texto con enlaces a incidencias ----------
export function richText(text, onKey) {
  const out = [];
  const re = new RegExp(`\\b((?:${keyPattern()})-\\d{1,6})\\b|(https?:\\/\\/[^\\s<]+)|(@[A-Za-z0-9_.-]{2,40})`, 'gi');
  let last = 0, m;
  const s = text || '';
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    if (m[1]) {
      const key = m[1].toUpperCase();
      out.push(h('span', { class: 'inc-link', title: `Abrir ${key}`, onclick: (e) => { e.stopPropagation(); (onKey || openKey)(key); } }, key));
    } else if (m[2]) {
      out.push(h('a', { href: m[2], target: '_blank', rel: 'noopener noreferrer' }, m[2]));
    } else {
      out.push(h('span', { class: 'mention' }, m[3]));
    }
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

// ---------- avisos ----------
let toastBox;
export function toast(message, kind = 'info', opts = {}) {
  if (!toastBox) { toastBox = h('div', { class: 'toasts' }); document.body.appendChild(toastBox); }
  const closeBtn = h('button', { class: 'toast-x', title: 'Cerrar', 'aria-label': 'Cerrar' }, '×');
  const el = h('div', { class: `toast ${kind}`, role: 'status' }, closeBtn,
    opts.title ? h('div', { class: 'tt' }, opts.title) : null, h('div', null, message));
  const close = () => el.remove();
  closeBtn.addEventListener('click', (e) => { e.stopPropagation(); close(); });
  el.addEventListener('click', () => { close(); opts.onClick && opts.onClick(); });
  toastBox.appendChild(el);
  // Como máximo 4 avisos a la vez: los más antiguos se retiran.
  while (toastBox.children.length > 4) toastBox.firstChild.remove();
  const ms = opts.sticky ? 0 : (opts.timeout || (kind === 'error' ? 8000 : 4200));
  if (ms) setTimeout(close, ms);
  return close;
}

export function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination); o.frequency.value = 880; g.gain.value = 0.06;
    o.start(); o.frequency.setValueAtTime(660, ctx.currentTime + 0.12); o.stop(ctx.currentTime + 0.25);
  } catch { /* sin audio */ }
}

// ---------- diálogos ----------
export function modal({ title, body, actions = [], wide = false, onClose }) {
  const foot = h('div', { class: 'modal-foot' });
  const box = h('div', { class: 'modal' + (wide ? ' wide' : ''), role: 'dialog', 'aria-modal': 'true' },
    h('div', { class: 'modal-head' }, h('h2', null, title),
      h('button', { class: 'btn ghost sm icon', title: 'Cerrar', onclick: () => close() }, icon('x'))),
    h('div', { class: 'modal-body' }, body), actions.length ? foot : null);
  const overlay = h('div', { class: 'overlay' }, box);
  let closed = false;
  function close() {
    if (closed) return; closed = true;
    overlay.remove(); document.removeEventListener('keydown', onKey); onClose && onClose();
  }
  function onKey(e) { if (e.key === 'Escape') close(); }
  for (const a of actions) {
    const b = h('button', { class: `btn ${a.kind || ''}`, type: 'button' }, a.label);
    b.addEventListener('click', async () => {
      if (!a.onClick) return close();
      b.disabled = true;
      try {
        const keep = await a.onClick();
        if (keep !== false) close();
      } finally { b.disabled = false; }
    });
    foot.appendChild(b);
  }
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) close(); });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
  setTimeout(() => { const f = box.querySelector('input, textarea, select'); f && f.focus(); }, 30);
  return { close, el: box };
}

export function confirmDialog({ title, message, confirmLabel = 'Confirmar', danger = false }) {
  return new Promise((resolve) => {
    let result = false;
    modal({
      title, body: h('p', { class: 'pre' }, message),
      actions: [{ label: 'Cancelar' }, { label: confirmLabel, kind: danger ? 'danger' : 'primary', onClick: () => { result = true; } }],
      onClose: () => resolve(result),
    });
  });
}

export function lightbox(url, type) {
  const media = type && type.startsWith('video/') ? h('video', { src: url, controls: true, autoplay: true }) : h('img', { src: url, alt: '' });
  const ov = h('div', { class: 'overlay lightbox', onclick: () => ov.remove() }, media);
  media.addEventListener('click', e => e.stopPropagation());
  document.body.appendChild(ov);
  const onKey = (e) => { if (e.key === 'Escape') { ov.remove(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
}

// ---------- borradores locales ----------
export function draftKey(name) { return `dc.draft.${name}`; }
export function bindDraft(el, name) {
  const key = draftKey(name);
  try { const v = localStorage.getItem(key); if (v && !el.value) el.value = v; } catch { /* ignorado */ }
  el.addEventListener('input', () => { try { el.value ? localStorage.setItem(key, el.value) : localStorage.removeItem(key); } catch { /* lleno */ } });
  return el;
}
export function clearDraft(name) { try { localStorage.removeItem(draftKey(name)); } catch { /* ignorado */ } }
export function loadJSON(key, fallback) { try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; } }
export function saveJSON(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignorado */ } }

export function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

export function selectEl(options, value, attrs = {}) {
  const s = h('select', attrs);
  for (const [v, label] of options) s.appendChild(h('option', { value: v }, label));
  s.value = value ?? '';
  return s;
}

export function field(label, control, { required = false, hint = '', cls = '' } = {}) {
  return h('label', { class: `field ${cls}` }, h('span', { class: required ? 'req' : '' }, label), control, hint ? h('span', { class: 'hint' }, hint) : null);
}

export function emptyState(title, text) {
  return h('div', { class: 'empty' }, h('div', { class: 'big' }, title), text ? h('div', null, text) : null);
}

// ---------- claves de elementos (APP-001, T-012, DEC-003) ----------
let projectKeys = ['INC'];
export function setProjectKeys(keys) { projectKeys = keys.length ? keys : ['INC']; }
export function keyPattern() {
  return [...projectKeys, 'T', 'DEC'].map(k => k.replace(/[^A-Za-z0-9]/g, '')).filter(Boolean).join('|');
}
export function openKey(key) {
  const k = key.toUpperCase();
  if (/^T-\d+$/.test(k)) location.hash = `#/tareas/${k}`;
  else if (/^DEC-\d+$/.test(k)) location.hash = `#/decisiones/${k}`;
  else location.hash = `#/incidencias/${k}`;
}

// ---------- utilidades de interfaz adicionales ----------
export function typeBadge(type) {
  const labels = { incident: 'Incidencia', task: 'Tarea', idea: 'Idea', decision: 'Decisión', wiki: 'Wiki', message: 'Mensaje',
    person: 'Persona', version: 'Versión', build: 'Build' };
  return h('span', { class: `badge type-${type}` }, labels[type] || type);
}

export function loading(text = 'Cargando…') {
  return h('div', { class: 'loading' }, h('span', { class: 'spin' }), text);
}

export function errorBox(err, retry) {
  return h('div', { class: 'error-box' }, icon('alert', 18),
    h('div', { class: 'grow' }, h('b', null, 'No se ha podido cargar'), h('div', { class: 'small' }, err?.message || String(err))),
    retry ? h('button', { class: 'btn sm', onclick: retry }, 'Reintentar') : null);
}

// Ejecuta una acción asíncrona mostrando el botón ocupado y el error (si lo hay) en un aviso.
export async function busy(btn, fn, okMessage) {
  if (btn) btn.disabled = true;
  try {
    const r = await fn();
    if (r && r._queued) toast(r.message || 'Guardado sin conexión: se enviará al reconectar.', 'warn');
    else if (okMessage) toast(okMessage, 'ok');
    return r;
  } catch (err) {
    toast(err.message || String(err), 'error');
    throw err;
  } finally {
    if (btn) btn.disabled = false;
  }
}
export const quiet = (p) => p.catch(() => {});

// Pestañas sencillas: [{ key, label, count? }]
export function tabs(items, active, onSelect) {
  return h('div', { class: 'tabs', role: 'tablist' }, items.map(t =>
    h('button', { class: 'tab' + (t.key === active ? ' on' : ''), role: 'tab', 'aria-selected': t.key === active ? 'true' : 'false',
      onclick: () => onSelect(t.key) }, t.icon ? icon(t.icon, 15) : null, t.label,
      t.count ? h('span', { class: 'count' }, t.count) : null)));
}

// Tabla a partir de columnas [{ label, render(row), cls }]
export function table(columns, rows, { onRow, rowClass, empty } = {}) {
  if (!rows.length) return emptyState(empty || 'No hay nada que mostrar', '');
  return h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
    h('thead', null, h('tr', null, columns.map(c => h('th', { class: c.cls || '' }, c.label)))),
    h('tbody', null, rows.map(r => h('tr', { class: [onRow ? 'clickable' : '', rowClass ? rowClass(r) : ''].join(' ').trim(),
      onclick: onRow ? () => onRow(r) : null }, columns.map(c => h('td', { class: c.cls || '' }, c.render(r))))))));
}

// Menú desplegable anclado a un botón: items [{ label, icon, onClick, danger, hidden }]
export function menuButton(label, items, attrs = {}) {
  const btn = h('button', { class: 'btn sm', type: 'button', ...attrs }, label);
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    document.querySelectorAll('.menu').forEach(m => m.remove());
    const rect = btn.getBoundingClientRect();
    const menu = h('div', { class: 'menu', style: { top: `${rect.bottom + 4}px`, left: `${Math.max(8, rect.right - 220)}px` } },
      items.filter(i => i && !i.hidden).map(i => h('button', { class: 'menu-item' + (i.danger ? ' danger' : ''),
        onclick: () => { menu.remove(); i.onClick(); } }, i.icon ? icon(i.icon, 15) : null, i.label)));
    document.body.appendChild(menu);
    const off = (ev) => { if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('mousedown', off); } };
    setTimeout(() => document.addEventListener('mousedown', off), 0);
  });
  return btn;
}

export function fmtHours(hours) {
  if (hours === null || hours === undefined || hours === '') return '—';
  const n = Number(hours);
  return `${Number.isInteger(n) ? n : n.toFixed(1)} h`;
}

export function pageHead(title, ...actions) {
  return h('div', { class: 'page-head' }, h('h1', null, title), ...actions);
}

// Pide un texto en un diálogo. Devuelve null si se cancela.
export function promptDialog({ title, label, value = '', placeholder = '', multiline = false, required = true, confirmLabel = 'Aceptar' }) {
  return new Promise((resolve) => {
    let result = null;
    const input = multiline ? h('textarea', { rows: 4, placeholder }) : h('input', { placeholder });
    input.value = value;
    modal({
      title, body: field(label, input, { required }),
      actions: [{ label: 'Cancelar' }, { label: confirmLabel, kind: 'primary', onClick: () => {
        if (required && !input.value.trim()) { toast('Este campo es obligatorio.', 'error'); return false; }
        result = input.value.trim();
      } }],
      onClose: () => resolve(result),
    });
  });
}

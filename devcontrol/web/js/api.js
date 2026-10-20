// Cliente de la API. Todas las peticiones pasan por el cliente local (127.0.0.1),
// que las reenvía al servidor elegido o las guarda en cola si no hay conexión.
import { uid } from './ui.js';

export class ApiError extends Error {
  constructor(status, code, message, data) {
    super(message);
    this.status = status; this.code = code; this.data = data || {};
  }
}

export const bus = new EventTarget();
export function emit(name, detail) { bus.dispatchEvent(new CustomEvent(name, { detail })); }
export function on(name, fn) { const h = (e) => fn(e.detail); bus.addEventListener(name, h); return () => bus.removeEventListener(name, h); }

// La sesión se guarda por servidor: cambiar de servidor no mezcla sesiones.
let serverKey = 'default';
export function setServerKey(key) { serverKey = key || 'default'; }
const tokenKey = () => `dc.token.${serverKey}`;

export const api = {
  get token() {
    try { return sessionStorage.getItem(tokenKey()) || localStorage.getItem(tokenKey()); } catch { return null; }
  },
  setToken(token, remember) {
    try {
      sessionStorage.removeItem(tokenKey()); localStorage.removeItem(tokenKey());
      if (token) (remember ? localStorage : sessionStorage).setItem(tokenKey(), token);
    } catch { /* almacenamiento no disponible */ }
  },

  async request(method, path, { body, queue = false, signal, headers = {} } = {}) {
    const hdrs = { ...headers };
    const tok = this.token;
    if (tok) hdrs.Authorization = `Bearer ${tok}`;
    if (body !== undefined) hdrs['Content-Type'] = 'application/json';
    if (method !== 'GET') hdrs['Idempotency-Key'] = hdrs['Idempotency-Key'] || uid();
    if (queue) hdrs['X-Offline-Queue'] = '1';
    let resp;
    try {
      resp = await fetch(path, { method, headers: hdrs, body: body !== undefined ? JSON.stringify(body) : undefined, signal });
    } catch (err) {
      if (err.name === 'AbortError') throw err;
      throw new ApiError(0, 'cliente_local_no_disponible',
        'No se puede contactar con DEV CONTROL en este equipo. Ciérralo y vuelve a abrirlo.');
    }
    let data = {};
    const text = await resp.text();
    if (text) { try { data = JSON.parse(text); } catch { data = { message: text }; } }
    if (resp.status === 202 && data.queued) {
      emit('queued', data);
      return { ...data, _queued: true };
    }
    if (!resp.ok) {
      const err = new ApiError(resp.status, data.error || 'error', data.message || `Error ${resp.status}`, data);
      if (resp.status === 401 && !['/api/login', '/api/logout'].includes(path)) emit('unauthorized', err);
      if (resp.status === 403 && ['debe_cambiar_contrasena', 'debe_activar_2fa'].includes(data.error)) emit('gate', data);
      if (resp.status === 426) emit('update-required', data);
      if (resp.status === 503) emit('server-down', err);
      throw err;
    }
    if (resp.headers.get('X-DevControl-Offline')) data._offline = resp.headers.get('X-DevControl-Cached-At') || true;
    return data;
  },
  get(path, opts) { return this.request('GET', path, opts); },
  post(path, body = {}, opts = {}) { return this.request('POST', path, { ...opts, body }); },
  patch(path, body = {}, opts = {}) { return this.request('PATCH', path, { ...opts, body }); },
  put(path, body = {}, opts = {}) { return this.request('PUT', path, { ...opts, body }); },
  del(path, opts = {}) { return this.request('DELETE', path, opts); },

  // Subida con progreso. target: { owner_type, owner_id | owner_uid, uid? }.
  // Si no hay conexión, el cliente local la guarda en cola y la envía después.
  upload(file, target, onProgress) {
    return new Promise((resolve, reject) => {
      const params = new URLSearchParams({ filename: file.name, uid: target.uid || uid() });
      for (const k of ['owner_type', 'owner_uid', 'owner_id']) if (target[k]) params.set(k, target[k]);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `/api/uploads?${params}`);
      const tok = this.token;
      if (tok) xhr.setRequestHeader('Authorization', `Bearer ${tok}`);
      xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
      xhr.setRequestHeader('Idempotency-Key', uid());
      xhr.setRequestHeader('X-Offline-Queue', '1');
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onerror = () => reject(new ApiError(0, 'red', 'Se ha perdido la conexión durante la subida.'));
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText || '{}'); } catch { /* vacío */ }
        if (xhr.status === 202 && data.queued) { emit('queued', data); return resolve({ ...data, _queued: true }); }
        if (xhr.status >= 200 && xhr.status < 300) return resolve(data);
        reject(new ApiError(xhr.status, data.error || 'error', data.message || `Error ${xhr.status} al subir ${file.name}`, data));
      };
      xhr.send(file);
    });
  },

  // Guarda un archivo del servidor en una carpeta de este equipo (Exportaciones o Descargas)
  // y lo abre. Funciona igual dentro de la ventana del programa que en un navegador.
  async save(path, { folder = 'descargas', open = true } = {}) {
    return this.local('POST', '/local/save', { path, folder, open });
  },

  async fetchBlob(path, onProgress) {
    const tok = this.token;
    let resp;
    try {
      resp = await fetch(path, { headers: tok ? { Authorization: `Bearer ${tok}` } : {} });
    } catch {
      throw new ApiError(0, 'red', 'No se puede contactar con el programa local.');
    }
    if (!resp.ok) {
      let data = {};
      try { data = await resp.json(); } catch { /* vacío */ }
      throw new ApiError(resp.status, data.error || 'error', data.message || `Error ${resp.status}`);
    }
    const total = Number(resp.headers.get('Content-Length')) || 0;
    if (!resp.body || !onProgress || !total) return resp.blob();
    const reader = resp.body.getReader();
    const chunks = []; let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value); got += value.length; onProgress(got / total);
    }
    return new Blob(chunks, { type: resp.headers.get('Content-Type') || 'application/octet-stream' });
  },

  _blobCache: new Map(),
  async blobUrl(path) {
    if (this._blobCache.has(path)) return this._blobCache.get(path);
    const p = this.fetchBlob(path).then(b => URL.createObjectURL(b));
    this._blobCache.set(path, p);
    p.catch(() => this._blobCache.delete(path));
    return p;
  },

  // Endpoints del programa local (/local/...): conexión, cola, actualizaciones, servidor.
  async local(method, path, body) {
    const headers = { 'Content-Type': 'application/json' };
    const tok = this.token;
    if (tok) headers.Authorization = `Bearer ${tok}`;
    let r;
    try {
      r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    } catch {
      throw new ApiError(0, 'cliente_local_no_disponible', 'No se puede contactar con DEV CONTROL en este equipo.');
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new ApiError(r.status, data.error || 'error', data.message || `Error ${r.status}`, data);
    return data;
  },
};

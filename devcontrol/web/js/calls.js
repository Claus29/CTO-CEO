// Llamadas de voz y vídeo entre cuentas.
//
// El servidor hace de centralita (/api/calls/*) y, si los equipos no pueden verse, de repetidor TURN.
// Aquí se gestionan la consulta de eventos, las conexiones WebRTC con cada participante, el micrófono, la cámara,
// la pantalla compartida y toda la interfaz de llamada.
//
// Reglas para evitar fallos:
//  * Solo quien entra en la llamada envía la oferta a quienes ya están dentro (nunca hay ofertas cruzadas).
//  * Las pistas de audio y vídeo se cambian con replaceTrack: encender la cámara o compartir pantalla no renegocia.
//  * Si la conexión falla se reinicia ICE (directo o por el anfitrión, lo que funcione) sin colgar.
//  * Al colgar se paran siempre todas las pistas y se cierran todas las conexiones.
import { api, emit, on } from './api.js';
import { can, store, userById } from './state.js';
import { avatar, h, icon, loadJSON, modal, mount, saveJSON, toast, uid } from './ui.js';

const clientId = uid();
const MAX_CAMERAS = 4;
let running = false;
let pollAbort = null;
let seq = 0;
let session = null;               // llamada en curso en este equipo
const ringing = new Map();        // id de llamada -> { call, close }
let lastServerActive = null;

export const callsAvailable = () => can('llamadas.usar') && (store.local?.server_info?.features || []).includes('calls');
export const inCall = () => !!session;
window.dcInCall = inCall;   // el programa local no instala actualizaciones durante una llamada

// ---------------------------------------------------------------------------------------------
// Consulta de eventos (mantiene «vivo» este equipo para la centralita)
// ---------------------------------------------------------------------------------------------
export function startCalls() {
  if (running || !callsAvailable()) return;
  running = true;
  loop();
  window.addEventListener('beforeunload', onUnload);
}

export function stopCalls() {
  running = false;
  pollAbort?.abort();
  if (session) session.hangup('salida');
  for (const r of ringing.values()) r.close();
  ringing.clear();
  window.removeEventListener('beforeunload', onUnload);
}

function onUnload() {
  if (!session) return;
  // keepalive: la petición sale aunque la ventana se esté cerrando.
  const tok = api.token;
  try {
    fetch(`/api/calls/${session.call.id}/leave`, { method: 'POST', keepalive: true,
      headers: { 'Content-Type': 'application/json', ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
      body: JSON.stringify({ client: clientId }) });
  } catch { /* la ventana se cierra igualmente */ }
}

async function loop() {
  let delay = 1000;
  while (running) {
    pollAbort = new AbortController();
    try {
      const r = await api.get(`/api/calls/events?client=${clientId}&since=${seq}&wait=10`, { signal: pollAbort.signal });
      delay = 1000;
      seq = r.seq;
      for (const ev of r.events) handleEvent(ev);
      reconcile(r.active);
    } catch (err) {
      if (!running || err.name === 'AbortError') return;
      if (err.status === 403) { running = false; return; }
      await new Promise(res => setTimeout(res, delay));
      delay = Math.min(delay * 2, 8000);
    }
  }
}

// El servidor es quien manda: si dice que no estoy en ninguna llamada, se cierra la local (y al revés, se ofrece volver).
function reconcile(active) {
  lastServerActive = active;
  if (session && session.joined && (!active || active.id !== session.call.id)) {
    session.teardown('La llamada ha terminado.');
  }
  renderRejoin();
}

function handleEvent(ev) {
  switch (ev.type) {
    case 'incoming': showIncoming(ev.call); break;
    case 'ring_stopped': stopRinging(ev.call_id, ev.reason); break;
    case 'state': if (session?.call.id === ev.call.id) session.updateState(ev.call); break;
    case 'left': if (session?.call.id === ev.call_id) session.removePeer(ev.client, ev.reason, ev.user_id); break;
    case 'signal': if (session?.call.id === ev.call_id) session.onSignal(ev.from_client, ev.from_user, ev.data); break;
    case 'ended': if (session?.call.id === ev.call_id) session.teardown(ev.reason); break;
    case 'notice': toast(ev.text, 'info', { title: 'Llamada' }); break;
    case 'busy_call': toast(`${ev.from_name} te ha llamado mientras estabas en esta llamada.`, 'info', {
      title: 'Llamada perdida', sticky: true, onClick: () => { if (session) session.hangup('colgar_y_llamar'); startCall([ev.from_user], ev.kind); } });
      break;
    default: break;
  }
}

// ---------------------------------------------------------------------------------------------
// Iniciar, contestar y unirse
// ---------------------------------------------------------------------------------------------
export async function startCall(userIds, kind = 'voz', channelId = null) {
  if (!callsAvailable()) return toast('Este servidor aún no tiene llamadas: el anfitrión debe actualizar DEV CONTROL.', 'error');
  if (session) return toast('Ya estás en una llamada. Cuélgala antes de empezar otra.', 'error');
  const s = new CallSession(kind);
  try {
    await s.prepareMedia();
    const body = { client: clientId, kind, uid: uid() };
    if (channelId) body.channel_id = channelId; else body.user_ids = userIds;
    const r = await api.post('/api/calls', body);
    if (r.ended) { s.stopMedia(); return toast(r.reason || 'No se ha podido llamar.', 'warn', { title: 'Llamada' }); }
    await s.enter(r.call, r.peers || []);
  } catch (err) {
    s.stopMedia();
    toast(err.message, 'error', { title: 'No se ha podido llamar' });
  }
}

async function answer(call, withVideo) {
  const r = ringing.get(call.id);
  r?.close();
  ringing.delete(call.id);
  if (session) session.hangup('contestar_otra');
  const s = new CallSession(withVideo ? 'video' : 'voz');
  try {
    await s.prepareMedia();
    const res = await api.post(`/api/calls/${call.id}/accept`, { client: clientId });
    await s.enter(res.call, res.peers);
  } catch (err) {
    s.stopMedia();
    toast(err.message, 'error', { title: 'No se ha podido contestar' });
  }
}

export async function joinCall(callId, kind = 'voz') {
  if (session) return toast('Ya estás en una llamada.', 'error');
  const s = new CallSession(kind);
  try {
    await s.prepareMedia();
    const res = await api.post(`/api/calls/${callId}/join`, { client: clientId });
    await s.enter(res.call, res.peers);
  } catch (err) {
    s.stopMedia();
    toast(err.message, 'error', { title: 'No se ha podido entrar en la llamada' });
  }
}

// ---------------------------------------------------------------------------------------------
// Llamada entrante
// ---------------------------------------------------------------------------------------------
function showIncoming(call) {
  if (ringing.has(call.id) || session?.call.id === call.id) return;
  const caller = userById(call.created_by) || { display_name: call.created_by_name };
  const others = call.participants.filter(p => p.user_id !== store.me.id && p.user_id !== call.created_by);
  const isVideo = call.kind === 'video';
  const stopTone = ringtone();
  const box = h('div', { class: 'call-incoming', role: 'alertdialog', 'aria-label': 'Llamada entrante' },
    h('div', { class: 'ci-avatar pulse' }, avatar(caller)),
    h('div', { class: 'ci-name' }, call.created_by_name),
    h('div', { class: 'ci-kind' }, icon(isVideo ? 'video' : 'phone', 16),
      isVideo ? 'Videollamada entrante' : 'Llamada de voz entrante',
      call.channel_name ? ` · #${call.channel_name}` : '',
      others.length ? ` · con ${others.map(p => p.name).join(', ')}` : ''),
    h('div', { class: 'ci-actions' },
      h('button', { class: 'btn danger round', title: 'Rechazar', onclick: () => decline(call) }, icon('phoneOff', 22)),
      isVideo ? h('button', { class: 'btn round', title: 'Aceptar solo con voz', onclick: () => answer(call, false) }, icon('phone', 22)) : null,
      h('button', { class: 'btn ok round', title: isVideo ? 'Aceptar con vídeo' : 'Aceptar', onclick: () => answer(call, isVideo) },
        icon(isVideo ? 'video' : 'phone', 22))));
  const ov = h('div', { class: 'overlay call-overlay' }, box);
  document.body.appendChild(ov);
  const close = () => { stopTone(); ov.remove(); };
  ringing.set(call.id, { call, close });
  // Aviso de Windows y ventana delante, aunque estuviera oculta o minimizada.
  const pref = store.prefs?.items?.find(i => i.type === 'call_incoming');
  if (!pref || pref.desktop) {
    api.local('POST', '/local/attention', { title: `Llamada de ${call.created_by_name}`,
      body: isVideo ? 'Videollamada entrante en DEV CONTROL' : 'Llamada entrante en DEV CONTROL', urgent: true, show: true }).catch(() => {});
  }
}

async function decline(call) {
  const r = ringing.get(call.id);
  r?.close();
  ringing.delete(call.id);
  try { await api.post(`/api/calls/${call.id}/decline`, { client: clientId }); } catch { /* ya terminó */ }
}

function stopRinging(callId, reason) {
  const r = ringing.get(callId);
  if (!r) return;
  r.close();
  ringing.delete(callId);
  if (reason === 'cancelada' || reason === 'sin_respuesta' || reason === 'no_contestada') {
    toast(`Llamada perdida de ${r.call.created_by_name}`, 'warn', { title: 'Llamada perdida', onClick: () => openCallsPanel() });
  }
}

// Tono de llamada con WebAudio (no necesita archivos).
function ringtone() {
  let ctx, timer, stopped = false;
  try {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    const beep = () => {
      if (stopped) return;
      const t = ctx.currentTime;
      for (const [f, at] of [[880, 0], [660, 0.18], [880, 0.6], [660, 0.78]]) {
        const o = ctx.createOscillator(); const g = ctx.createGain();
        o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
        g.gain.setValueAtTime(0.0001, t + at); g.gain.exponentialRampToValueAtTime(0.08, t + at + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.16);
        o.start(t + at); o.stop(t + at + 0.17);
      }
    };
    beep();
    timer = setInterval(beep, 2400);
  } catch { /* sin audio */ }
  return () => { stopped = true; clearInterval(timer); try { ctx?.close(); } catch { /* ignorado */ } };
}

// ---------------------------------------------------------------------------------------------
// Sesión de llamada
// ---------------------------------------------------------------------------------------------
class CallSession {
  constructor(kind) {
    this.kind = kind;
    this.call = null;
    this.joined = false;
    this.peers = new Map();       // clientId -> Peer
    this.local = new MediaStream();
    this.audioTrack = null;
    this.cameraTrack = null;
    this.screenTrack = null;
    this.micMuted = false;
    this.cameraOn = false;
    this.ice = null;
    this.ui = null;
    this.startedAt = null;
    this.ended = false;
    this.meters = new Map();
    session = this;
  }

  // -- medios locales ------------------------------------------------------------------------
  async prepareMedia() {
    const devices = loadJSON('dc.callDevices', {});
    try {
      const s = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, ...(devices.mic ? { deviceId: { ideal: devices.mic } } : {}) },
      });
      this.audioTrack = s.getAudioTracks()[0];
    } catch (err) {
      toast(mediaError(err, 'micrófono') + ' Entrarás solo para escuchar.', 'warn', { title: 'Micrófono', sticky: true });
    }
    if (this.kind === 'video') await this.enableCamera(true, true);
  }

  async enableCamera(on, quiet = false) {
    if (!on) {
      this.cameraTrack?.stop();
      this.cameraTrack = null;
      this.cameraOn = false;
      if (!this.screenTrack) this.setVideoTrack(null);
      this.render();
      return;
    }
    const cams = [...this.peers.values()].filter(p => p.hasRemoteVideo).length + 1;
    if (cams > MAX_CAMERAS && !quiet) return toast(`Como máximo ${MAX_CAMERAS} cámaras a la vez en una llamada.`, 'warn');
    try {
      const devices = loadJSON('dc.callDevices', {});
      const s = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 },
        ...(devices.cam ? { deviceId: { ideal: devices.cam } } : {}) } });
      this.cameraTrack = s.getVideoTracks()[0];
      this.cameraOn = true;
      if (!this.screenTrack) this.setVideoTrack(this.cameraTrack);
    } catch (err) {
      this.cameraOn = false;
      toast(mediaError(err, 'cámara') + ' La llamada sigue con voz.', 'warn', { title: 'Cámara' });
    }
    this.render();
  }

  async toggleScreen() {
    if (this.screenTrack) {
      this.screenTrack.stop();
      this.screenTrack = null;
      this.setVideoTrack(this.cameraOn ? this.cameraTrack : null);
      this.render();
      return;
    }
    try {
      const s = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      this.screenTrack = s.getVideoTracks()[0];
      this.screenTrack.addEventListener('ended', () => { if (this.screenTrack) this.toggleScreen(); });
      this.setVideoTrack(this.screenTrack);
    } catch (err) {
      if (err.name !== 'NotAllowedError') toast(mediaError(err, 'pantalla'), 'error');
    }
    this.render();
  }

  setVideoTrack(track) {
    for (const p of this.peers.values()) p.setTrack('video', track);
    this.render();
  }

  toggleMic() {
    if (!this.audioTrack) return toast('No hay micrófono disponible.', 'warn');
    this.micMuted = !this.micMuted;
    this.audioTrack.enabled = !this.micMuted;
    this.render();
  }

  async switchDevice(kind, deviceId) {
    const devices = loadJSON('dc.callDevices', {});
    devices[kind] = deviceId;
    saveJSON('dc.callDevices', devices);
    if (kind === 'speaker') {
      for (const el of document.querySelectorAll('.call-tile audio, .call-tile video')) el.setSinkId?.(deviceId).catch(() => {});
      return;
    }
    try {
      if (kind === 'mic') {
        const s = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: { exact: deviceId }, echoCancellation: true, noiseSuppression: true } });
        const t = s.getAudioTracks()[0];
        t.enabled = !this.micMuted;
        this.audioTrack?.stop();
        this.audioTrack = t;
        for (const p of this.peers.values()) p.setTrack('audio', t);
      } else if (kind === 'cam' && this.cameraOn) {
        this.cameraTrack?.stop();
        await this.enableCamera(true, true);
      }
    } catch (err) { toast(mediaError(err, kind === 'mic' ? 'micrófono' : 'cámara'), 'error'); }
    this.render();
  }

  stopMedia() {
    for (const t of [this.audioTrack, this.cameraTrack, this.screenTrack]) t?.stop();
    this.audioTrack = this.cameraTrack = this.screenTrack = null;
    if (session === this && !this.joined) session = null;
  }

  // -- entrar y conexiones -------------------------------------------------------------------
  async enter(call, peers) {
    this.call = call;
    this.joined = true;
    this.startedAt = call.answered_at ? new Date(call.answered_at) : null;
    this.ice = await loadIce(call.id);
    this.render();
    // Quien entra llama a todos los que ya están dentro.
    for (const p of peers) this.ensurePeer(p.client, p.user_id, true);
    emit('call-changed', { active: true });
  }

  ensurePeer(remoteClient, userId, initiator) {
    let peer = this.peers.get(remoteClient);
    if (!peer) {
      peer = new Peer(this, remoteClient, userId, initiator);
      this.peers.set(remoteClient, peer);
      this.render();
    }
    return peer;
  }

  onSignal(fromClient, fromUser, data) {
    if (!data) return;
    if (data.bye) { this.removePeer(fromClient, 'colgo', fromUser); return; }
    const peer = this.peers.get(fromClient) || (data.description?.type === 'offer' ? this.ensurePeer(fromClient, fromUser, false) : null);
    peer?.onSignal(data);
  }

  removePeer(clientIdRemote, reason, userId) {
    const peer = this.peers.get(clientIdRemote);
    if (!peer) return;
    peer.close();
    this.peers.delete(clientIdRemote);
    if (reason === 'conexion_perdida') toast(`${userById(userId)?.display_name || 'Un participante'} ha perdido la conexión.`, 'warn');
    this.render();
  }

  updateState(call) {
    const answeredBefore = this.call?.answered_at;
    this.call = call;
    if (!answeredBefore && call.answered_at) this.startedAt = new Date(call.answered_at);
    const inside = new Set(call.participants.filter(p => p.state === 'dentro' && p.client).map(p => p.client));
    for (const cid of [...this.peers.keys()]) if (!inside.has(cid)) this.removePeer(cid, 'salio');
    this.render();
  }

  send(to, data) {
    if (!this.call || this.ended) return Promise.resolve();
    return api.post(`/api/calls/${this.call.id}/signal`, { client: clientId, to, data }).catch(() => {});
  }

  async hangup(reason = 'colgo') {
    if (this.ended) return;
    const id = this.call?.id;
    this.teardown(null);
    if (id) {
      try { await api.post(`/api/calls/${id}/leave`, { client: clientId, reason }); } catch { /* el servidor la cerrará igualmente */ }
    }
  }

  teardown(message) {
    if (this.ended) return;
    this.ended = true;
    for (const p of this.peers.values()) p.close();
    this.peers.clear();
    for (const m of this.meters.values()) m.stop();
    this.meters.clear();
    for (const t of [this.audioTrack, this.cameraTrack, this.screenTrack]) t?.stop();
    this.audioTrack = this.cameraTrack = this.screenTrack = null;
    this.ui?.remove();
    this.ui = null;
    clearInterval(this.timer);
    if (session === this) session = null;
    if (message) toast(message, 'info', { title: 'Llamada' });
    emit('call-changed', { active: false });
  }

  // -- interfaz --------------------------------------------------------------------------------
  render() {
    if (this.ended || !this.call) return;
    if (!this.ui) this.buildUi();
    const call = this.call;
    const inside = call.participants.filter(p => p.state === 'dentro');
    const ringingNow = call.participants.filter(p => p.state === 'llamando');
    this.ui.classList.toggle('expanded', this.expanded !== false && (this.kind === 'video' || this.hasAnyVideo()));
    mount(this.titleEl, call.channel_name ? `#${call.channel_name}` :
      inside.concat(ringingNow).filter(p => p.user_id !== store.me.id).map(p => p.name).join(', ') || 'Llamada');
    mount(this.subEl, !call.answered_at ? (ringingNow.length ? 'Llamando…' : 'Esperando…') :
      `${inside.length} en la llamada${ringingNow.length ? ` · llamando a ${ringingNow.length}` : ''}`);
    // mosaico
    const tiles = [this.tile('local', { name: 'Tú', user: store.me, video: this.screenTrack || (this.cameraOn ? this.cameraTrack : null),
      muted: this.micMuted || !this.audioTrack, mirror: !this.screenTrack, local: true })];
    for (const p of this.peers.values()) tiles.push(this.tile(p.remoteClient, { name: userById(p.userId)?.display_name || '…',
      user: userById(p.userId), stream: p.stream, video: p.hasRemoteVideo, quality: p.quality, relay: p.relay, state: p.state }));
    for (const r of ringingNow) if (r.user_id !== store.me.id) tiles.push(this.tile(`ring-${r.user_id}`, { name: r.name, user: userById(r.user_id), ringing: true }));
    this.grid.dataset.n = String(Math.min(tiles.length, 9));
    const keep = new Set(tiles.map(t => t.dataset.key));
    for (const el of [...this.grid.children]) if (!keep.has(el.dataset.key)) el.remove();
    for (const t of tiles) if (!t.isConnected) this.grid.appendChild(t);
    // controles
    this.micBtn.classList.toggle('off', this.micMuted || !this.audioTrack);
    mount(this.micBtn, icon(this.micMuted || !this.audioTrack ? 'micOff' : 'mic', 20));
    this.micBtn.title = this.micMuted ? 'Activar micrófono' : 'Silenciar micrófono';
    this.camBtn.classList.toggle('off', !this.cameraOn);
    mount(this.camBtn, icon(this.cameraOn ? 'video' : 'videoOff', 20));
    this.screenBtn.classList.toggle('on', !!this.screenTrack);
    this.addBtn.classList.toggle('hidden', inside.length + ringingNow.length >= (call.max_participants || 8));
  }

  hasAnyVideo() {
    return this.cameraOn || !!this.screenTrack || [...this.peers.values()].some(p => p.hasRemoteVideo);
  }

  tile(key, o) {
    let el = this.grid?.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!el) {
      el = h('div', { class: 'call-tile', dataset: { key } },
        h('video', { autoplay: true, playsinline: true, muted: !!o.local }),
        h('div', { class: 'ct-avatar' }, avatar(o.user || { display_name: o.name })),
        h('div', { class: 'ct-label' }, h('span', { class: 'ct-mic' }), h('span', { class: 'ct-name' }), h('span', { class: 'ct-q' })));
      if (!o.local) el.appendChild(h('audio', { autoplay: true }));
      const sink = loadJSON('dc.callDevices', {}).speaker;
      if (sink) for (const m of el.querySelectorAll('audio, video')) m.setSinkId?.(sink).catch(() => {});
    }
    const video = el.querySelector('video');
    const audio = el.querySelector('audio');
    if (o.local) {
      const want = o.video ? new MediaStream([o.video]) : null;
      if ((video.srcObject?.getVideoTracks()[0] || null) !== (o.video || null)) video.srcObject = want;
      video.classList.toggle('mirror', !!o.mirror);
      this.meter(key, this.audioTrack ? new MediaStream([this.audioTrack]) : null, el);
    } else if (o.stream) {
      if (video.srcObject !== o.stream) video.srcObject = o.stream;
      if (audio && audio.srcObject !== o.stream) audio.srcObject = o.stream;
      video.muted = true;   // el audio sale por <audio> (permite elegir altavoz)
      this.meter(key, o.stream, el);
    }
    el.classList.toggle('has-video', !!o.video);
    el.classList.toggle('ringing', !!o.ringing);
    el.classList.toggle('reconnecting', o.state === 'reconectando');
    el.querySelector('.ct-name').textContent = o.ringing ? `${o.name} · llamando…` : o.state === 'reconectando' ? `${o.name} · reconectando…` : o.name;
    mount(el.querySelector('.ct-mic'), o.muted ? icon('micOff', 13) : null);
    const q = el.querySelector('.ct-q');
    q.className = `ct-q ${o.quality || ''}`;
    q.title = o.quality ? `Conexión ${o.quality === 'good' ? 'buena' : o.quality === 'fair' ? 'regular' : 'mala'}${o.relay ? ' · a través del anfitrión' : ' · directa'}` : '';
    return el;
  }

  // Indicador de quién habla (nivel del micrófono).
  meter(key, stream, el) {
    const prev = this.meters.get(key);
    if (prev && prev.stream === stream) return;
    prev?.stop();
    if (!stream || !stream.getAudioTracks().length) { this.meters.delete(key); return; }
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const src = ctx.createMediaStreamSource(stream);
      const an = ctx.createAnalyser(); an.fftSize = 512;
      src.connect(an);
      const buf = new Uint8Array(an.fftSize);
      const t = setInterval(() => {
        an.getByteTimeDomainData(buf);
        let sum = 0; for (const v of buf) sum += (v - 128) ** 2;
        el.classList.toggle('speaking', Math.sqrt(sum / buf.length) > 6);
      }, 200);
      this.meters.set(key, { stream, stop: () => { clearInterval(t); ctx.close().catch(() => {}); } });
    } catch { /* sin medidor */ }
  }

  buildUi() {
    this.titleEl = h('b', { class: 'ellipsis' });
    this.subEl = h('span', { class: 'tiny muted' });
    this.timeEl = h('span', { class: 'mono small call-time' }, '0:00');
    this.grid = h('div', { class: 'call-grid' });
    const btn = (ic, title, fn, cls = '') => h('button', { class: `call-btn ${cls}`, title, onclick: fn }, icon(ic, 20));
    this.micBtn = btn('mic', 'Silenciar micrófono', () => this.toggleMic());
    this.camBtn = btn('videoOff', 'Cámara', () => this.enableCamera(!this.cameraOn));
    this.screenBtn = btn('screen', 'Compartir pantalla', () => this.toggleScreen());
    this.addBtn = btn('userPlus', 'Añadir personas', () => this.addPeople());
    const devBtn = btn('settings', 'Micrófono, altavoz y cámara', () => this.devicesDialog());
    const sizeBtn = btn('expand', 'Ampliar o reducir', () => { this.expanded = !this.ui.classList.contains('expanded'); this.ui.classList.toggle('expanded', this.expanded); });
    const hang = btn('phoneOff', 'Colgar', () => this.hangup(), 'hang');
    this.ui = h('div', { class: 'call-panel', role: 'region', 'aria-label': 'Llamada en curso' },
      h('div', { class: 'call-head' }, icon(this.kind === 'video' ? 'video' : 'phone', 16),
        h('div', { class: 'col grow', style: { gap: '0' } }, this.titleEl, this.subEl), this.timeEl),
      this.grid,
      h('div', { class: 'call-controls' }, this.micBtn, this.camBtn, this.screenBtn, this.addBtn, devBtn, sizeBtn, hang));
    document.body.appendChild(this.ui);
    this.timer = setInterval(() => {
      if (!this.startedAt) return;
      const s = Math.max(0, Math.floor((Date.now() - this.startedAt.getTime()) / 1000));
      this.timeEl.textContent = `${Math.floor(s / 3600) ? Math.floor(s / 3600) + ':' : ''}${String(Math.floor(s / 60) % 60).padStart(Math.floor(s / 3600) ? 2 : 1, '0')}:${String(s % 60).padStart(2, '0')}`;
    }, 1000);
  }

  async devicesDialog() {
    let list = [];
    try { list = await navigator.mediaDevices.enumerateDevices(); } catch { /* sin permisos */ }
    const saved = loadJSON('dc.callDevices', {});
    const sel = (kind, devKind, label) => {
      const opts = list.filter(d => d.kind === devKind);
      const s = h('select', { onchange: (e) => this.switchDevice(kind, e.target.value) },
        opts.length ? opts.map((d, i) => h('option', { value: d.deviceId }, d.label || `${label} ${i + 1}`)) : h('option', { value: '' }, 'No disponible'));
      if (saved[kind]) s.value = saved[kind];
      return h('label', { class: 'field' }, h('span', null, label), s);
    };
    modal({ title: 'Dispositivos de la llamada', body: h('div', { class: 'col gap-lg' },
      sel('mic', 'audioinput', 'Micrófono'), sel('speaker', 'audiooutput', 'Altavoz'), sel('cam', 'videoinput', 'Cámara'),
      h('div', { class: 'tiny muted' }, 'Se recuerdan en este equipo para las próximas llamadas.')),
    actions: [{ label: 'Cerrar', kind: 'primary' }] });
  }

  addPeople() {
    const inCallIds = new Set(this.call.participants.filter(p => ['dentro', 'llamando'].includes(p.state)).map(p => p.user_id));
    const picks = new Set();
    const people = store.users.filter(u => !inCallIds.has(u.id));
    modal({ title: 'Añadir a la llamada', body: people.length ? h('div', { class: 'col' }, people.map(u =>
      h('label', { class: 'check call-pick' }, h('input', { type: 'checkbox', onchange: (e) => { e.target.checked ? picks.add(u.id) : picks.delete(u.id); } }),
        avatar(u, u.online), u.display_name, u.online ? null : h('span', { class: 'tiny muted' }, ' (desconectado)')))) :
      h('div', { class: 'muted' }, 'No hay nadie más a quien añadir.'),
    actions: [{ label: 'Cancelar' }, { label: 'Llamar', kind: 'primary', onClick: async () => {
      if (!picks.size) return false;
      try { await api.post(`/api/calls/${this.call.id}/invite`, { user_ids: [...picks] }); } catch (err) { toast(err.message, 'error'); return false; }
    } }] });
  }
}

// ---------------------------------------------------------------------------------------------
// Conexión con un participante
// ---------------------------------------------------------------------------------------------
class Peer {
  constructor(sess, remoteClient, userId, initiator) {
    this.s = sess;
    this.remoteClient = remoteClient;
    this.userId = userId;
    this.initiator = initiator;
    this.stream = new MediaStream();
    this.hasRemoteVideo = false;
    this.quality = '';
    this.relay = false;
    this.state = 'conectando';
    this.pendingCandidates = [];
    this.closed = false;
    this.pc = new RTCPeerConnection({ iceServers: sess.ice?.servers || [], iceTransportPolicy: sess.ice?.policy || 'all' });
    this.pc.onicecandidate = (e) => { if (e.candidate) sess.send(remoteClient, { candidate: e.candidate.toJSON() }); };
    this.pc.ontrack = (e) => {
      if (!this.stream.getTracks().includes(e.track)) this.stream.addTrack(e.track);
      const upd = () => { this.hasRemoteVideo = this.stream.getVideoTracks().some(t => !t.muted && t.readyState === 'live'); sess.render(); };
      e.track.onmute = upd; e.track.onunmute = upd; e.track.onended = upd;
      upd();
    };
    this.pc.onconnectionstatechange = () => this.onState();
    this.statsTimer = setInterval(() => this.stats(), 3000);
    if (initiator) {
      this.audio = this.pc.addTransceiver('audio', { direction: 'sendrecv', streams: [sess.local] });
      this.video = this.pc.addTransceiver('video', { direction: 'sendrecv', streams: [sess.local] });
      if (sess.audioTrack) this.audio.sender.replaceTrack(sess.audioTrack);
      const v = sess.screenTrack || (sess.cameraOn ? sess.cameraTrack : null);
      if (v) this.video.sender.replaceTrack(v);
      this.offer();
    }
  }

  async offer(restart = false) {
    try {
      const o = await this.pc.createOffer(restart ? { iceRestart: true } : undefined);
      await this.pc.setLocalDescription(o);
      this.s.send(this.remoteClient, { description: this.pc.localDescription.toJSON() });
    } catch (err) { console.warn('oferta', err); }
  }

  async onSignal(data) {
    try {
      if (data.description) {
        const d = data.description;
        if (d.type === 'offer') {
          await this.pc.setRemoteDescription(d);
          // Las líneas de audio y vídeo las crea la oferta: aquí solo se ponen las pistas locales.
          for (const t of this.pc.getTransceivers()) {
            const kind = t.receiver.track.kind;
            t.direction = 'sendrecv';
            if (kind === 'audio') { this.audio = t; if (this.s.audioTrack) await t.sender.replaceTrack(this.s.audioTrack); }
            if (kind === 'video') {
              this.video = t;
              const v = this.s.screenTrack || (this.s.cameraOn ? this.s.cameraTrack : null);
              await t.sender.replaceTrack(v);
            }
          }
          await this.pc.setLocalDescription(await this.pc.createAnswer());
          this.s.send(this.remoteClient, { description: this.pc.localDescription.toJSON() });
        } else if (d.type === 'answer' && this.pc.signalingState === 'have-local-offer') {
          await this.pc.setRemoteDescription(d);
        }
        for (const c of this.pendingCandidates.splice(0)) await this.pc.addIceCandidate(c).catch(() => {});
      } else if (data.candidate) {
        if (!this.pc.remoteDescription) this.pendingCandidates.push(data.candidate);
        else await this.pc.addIceCandidate(data.candidate).catch(() => {});
      } else if (data.restart && this.initiator) {
        this.offer(true);
      }
    } catch (err) { console.warn('señal', err); }
  }

  setTrack(kind, track) {
    const t = kind === 'audio' ? this.audio : this.video;
    if (t) t.sender.replaceTrack(track).catch(() => {});
  }

  onState() {
    const st = this.pc.connectionState;
    if (st === 'connected') {
      this.state = 'conectado';
      clearTimeout(this.restartTimer);
    } else if (st === 'disconnected' || st === 'failed') {
      this.state = 'reconectando';
      clearTimeout(this.restartTimer);
      // Si no se recupera sola, se reinicia ICE (probará directo y a través del anfitrión).
      this.restartTimer = setTimeout(() => {
        if (this.closed || this.pc.connectionState === 'connected') return;
        if (this.initiator) this.offer(true); else this.s.send(this.remoteClient, { restart: true });
      }, st === 'failed' ? 0 : 4000);
    }
    this.s.render();
  }

  async stats() {
    if (this.closed || this.pc.connectionState !== 'connected') return;
    try {
      const report = await this.pc.getStats();
      let pair = null;
      report.forEach(r => { if (r.type === 'candidate-pair' && r.nominated && r.state === 'succeeded') pair = r; });
      if (!pair) return;
      const local = report.get(pair.localCandidateId);
      const remote = report.get(pair.remoteCandidateId);
      this.relay = local?.candidateType === 'relay' || remote?.candidateType === 'relay';
      const rtt = pair.currentRoundTripTime ?? 0;
      this.quality = rtt < 0.15 ? 'good' : rtt < 0.4 ? 'fair' : 'poor';
      this.s.render();
    } catch { /* sin estadísticas */ }
  }

  close() {
    this.closed = true;
    clearInterval(this.statsTimer);
    clearTimeout(this.restartTimer);
    try { this.pc.close(); } catch { /* ya cerrada */ }
  }
}

async function loadIce(callId) {
  const host = store.local?.server_host || '127.0.0.1';
  const forceRelay = loadJSON('dc.forzarRepetidor', false);
  try {
    const r = await api.get(`/api/calls/${callId}/ice`);
    if (!r.available) return { servers: [], policy: 'all' };
    const h6 = host.includes(':') ? `[${host}]` : host;
    return {
      policy: forceRelay ? 'relay' : 'all',
      servers: [{ urls: [`stun:${h6}:${r.port}`] },
        { urls: [`turn:${h6}:${r.port}?transport=udp`, `turn:${h6}:${r.port}?transport=tcp`], username: r.username, credential: r.credential }],
    };
  } catch { return { servers: [], policy: 'all' }; }
}

function mediaError(err, what) {
  const art = { 'micrófono': 'el micrófono', 'cámara': 'la cámara', 'pantalla': 'la pantalla' }[what] || what;
  switch (err?.name) {
    case 'NotAllowedError': return `No hay permiso para usar ${art}. Permítelo cuando el programa lo pida o en Configuración de Windows → Privacidad.`;
    case 'NotFoundError': return `No se encuentra ${what === 'cámara' ? 'ninguna cámara' : what === 'micrófono' ? 'ningún micrófono' : art}.`;
    case 'NotReadableError': return `${art[0].toUpperCase()}${art.slice(1)} está en uso por otro programa.`;
    default: return `No se puede usar ${art} (${err?.message || err}).`;
  }
}

// ---------------------------------------------------------------------------------------------
// Volver a una llamada (tras recargar la ventana) y panel de llamadas
// ---------------------------------------------------------------------------------------------
let rejoinEl = null;
function renderRejoin() {
  const a = lastServerActive;
  const show = a && !session && a.me === 'dentro';
  if (!show) { rejoinEl?.remove(); rejoinEl = null; return; }
  if (!rejoinEl) {
    rejoinEl = h('div', { class: 'call-rejoin' }, icon('phone', 16), h('span', { class: 'grow' }, 'Sigues en una llamada.'),
      h('button', { class: 'btn sm ok', onclick: () => joinCall(a.id, a.kind) }, 'Volver a la llamada'),
      h('button', { class: 'btn sm danger', onclick: () => api.post(`/api/calls/${a.id}/leave`, {}).catch(() => {}) }, 'Colgar'));
    document.body.appendChild(rejoinEl);
  }
}

export function callButtons({ userId = null, channelId = null, size = 'sm' } = {}) {
  if (!callsAvailable()) return null;
  const u = userId ? userById(userId) : null;
  if (userId === store.me?.id) return null;
  const off = u && !u.online;
  const title = (t) => off ? `${u.display_name} no está conectado` : t;
  const go = (kind) => channelId ? startCall(null, kind, channelId) : startCall([userId], kind);
  return h('span', { class: 'call-buttons' },
    h('button', { class: `btn ghost ${size} icon`, title: title(channelId ? 'Llamar al canal' : 'Llamar'), disabled: off, onclick: () => go('voz') }, icon('phone', 16)),
    h('button', { class: `btn ghost ${size} icon`, title: title(channelId ? 'Videollamada con el canal' : 'Videollamada'), disabled: off, onclick: () => go('video') }, icon('video', 16)));
}

export async function openCallsPanel() {
  const body = h('div', { class: 'col gap-lg calls-panel' }, h('div', { class: 'muted' }, 'Cargando…'));
  const dlg = modal({ title: 'Llamadas', body, wide: true, actions: [{ label: 'Cerrar' }] });
  const picks = new Set();
  const draw = async () => {
    let active, history;
    try { [active, history] = await Promise.all([api.get('/api/calls/active'), api.get('/api/calls/history?limit=40')]); }
    catch (err) { mount(body, h('div', { class: 'callout danger' }, err.message)); return; }
    const online = new Set(active.online);
    const people = store.users.filter(u => u.id !== store.me.id).sort((a, b) => (online.has(b.id) - online.has(a.id)) || a.display_name.localeCompare(b.display_name));
    const groupBtn = h('button', { class: 'btn primary', disabled: true, onclick: () => { dlg.close(); startCall([...picks], 'voz'); } }, icon('phone'), 'Llamada de grupo');
    const groupVid = h('button', { class: 'btn', disabled: true, onclick: () => { dlg.close(); startCall([...picks], 'video'); } }, icon('video'), 'Con vídeo');
    const refresh = () => { groupBtn.disabled = groupVid.disabled = picks.size < 1; groupBtn.lastChild.textContent = picks.size > 1 ? `Llamar a ${picks.size}` : 'Llamar'; };
    mount(body,
      active.joinable.length ? h('div', { class: 'col' }, h('h3', null, 'Llamadas en curso'), active.joinable.map(c => h('div', { class: 'outbox-item' },
        icon(c.kind === 'video' ? 'video' : 'phone', 16),
        h('div', { class: 'grow' }, h('b', null, c.channel_name ? `#${c.channel_name}` : c.participants.map(p => p.name).join(', ')),
          h('div', { class: 'tiny muted' }, `${c.participants.filter(p => p.state === 'dentro').length} dentro`)),
        h('button', { class: 'btn sm ok', onclick: () => { dlg.close(); joinCall(c.id, c.kind); } }, 'Unirse')))) : null,
      h('div', { class: 'col' }, h('div', { class: 'row between' }, h('h3', null, 'Personas'), h('div', { class: 'row' }, groupVid, groupBtn)),
        h('div', { class: 'tiny muted' }, 'Marca varias personas para una llamada de grupo, o usa los botones de cada una.'),
        h('div', { class: 'call-people' }, people.map(u => h('div', { class: 'call-person' + (online.has(u.id) ? '' : ' offline') },
          h('input', { type: 'checkbox', disabled: !online.has(u.id), onchange: (e) => { e.target.checked ? picks.add(u.id) : picks.delete(u.id); refresh(); } }),
          avatar(u, online.has(u.id)), h('div', { class: 'grow' }, h('div', null, u.display_name),
            h('div', { class: 'tiny muted' }, online.has(u.id) ? 'Conectado' : 'Desconectado')),
          online.has(u.id) ? h('span', null,
            h('button', { class: 'btn ghost sm icon', title: 'Llamar', onclick: () => { dlg.close(); startCall([u.id], 'voz'); } }, icon('phone', 16)),
            h('button', { class: 'btn ghost sm icon', title: 'Videollamada', onclick: () => { dlg.close(); startCall([u.id], 'video'); } }, icon('video', 16))) : null)))),
      h('div', { class: 'col' }, h('h3', null, 'Historial'),
        history.items.length ? h('div', { class: 'col tight' }, history.items.map(c => {
          const others = c.participants.filter(p => p.user_id !== store.me.id);
          const target = c.outgoing ? others[0]?.user_id : c.created_by;
          return h('div', { class: 'list-item' + (c.missed ? ' missed' : '') },
            icon(c.missed ? 'phoneMissed' : c.outgoing ? 'phoneOut' : 'phoneIn', 16),
            h('div', { class: 'grow' }, h('div', null, c.channel_id ? `Canal · ${others.map(p => p.name).join(', ')}` : others.map(p => p.name).join(', ')),
              h('div', { class: 'tiny muted' }, `${c.kind_label} · ${c.missed ? 'Perdida' : c.status_label}${c.duration_seconds ? ' · ' + Math.max(1, Math.round(c.duration_seconds / 60)) + ' min' : ''} · ${new Date(c.created_at).toLocaleString()}`)),
            target && others.length === 1 ? h('button', { class: 'btn ghost sm', onclick: () => { dlg.close(); startCall([target], c.kind); } }, 'Devolver llamada') : null);
        })) : h('div', { class: 'muted small' }, 'Aún no hay llamadas.')));
  };
  draw();
}

on('logout', () => stopCalls());
export { clientId };

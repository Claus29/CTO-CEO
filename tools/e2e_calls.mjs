// Prueba real de llamadas con varios usuarios a la vez (micrófono y cámara simulados).
// Uso: node tools/e2e_calls.mjs http://127.0.0.1:8770/ <carpeta_capturas>
// Requiere una instancia nueva (crea el administrador y dos usuarios).
import { createRequire } from 'module';
import { mkdirSync } from 'fs';

const require = createRequire(process.env.PLAYWRIGHT_FROM || import.meta.url);
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8770/';
const out = process.argv[3] || 'capturas-llamadas';
mkdirSync(out, { recursive: true });
const PW = 'Zorro-azul-42';
const errors = [];
const step = (t) => console.log(`· ${t}`);

async function apiCall(method, path, body, token) {
  const r = await fetch(new URL(path, base), { method, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${method} ${path}: ${r.status} ${JSON.stringify(data)}`);
  return data;
}

// Datos: administrador + dos personas
const health = await apiCall('GET', '/api/health');
if (health.setup_required) await apiCall('POST', '/api/setup', { admin: { username: 'ana', display_name: 'Ana', password: PW } });
const adminTok = (await apiCall('POST', '/api/login', { username: 'ana', password: PW })).token;
const roles = Object.fromEntries((await apiCall('GET', '/api/admin/roles', null, adminTok)).items.map(r => [r.key, r.id]));
for (const [u, n] of [['berta', 'Berta'], ['carlos', 'Carlos']]) {
  try { await apiCall('POST', '/api/admin/users', { username: u, display_name: n, password: PW, role_id: roles.dev, must_change_password: false }, adminTok); }
  catch (e) { if (!String(e).includes('Ya existe')) throw e; }
}

const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });

async function person(user, relay = false) {
  // bypassCSP: algunas versiones de Playwright evalúan las esperas como texto, algo que la política de seguridad
  // de la interfaz (script-src 'self') bloquea a propósito. Solo afecta a esta prueba, no al programa.
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 820 }, permissions: ['microphone', 'camera'], bypassCSP: true });
  const page = await ctx.newPage();
  page.on('pageerror', e => errors.push(`${user} pageerror: ${e.message}`));
  page.on('console', m => { if (m.type() === 'error' && !/status of 4\d\d/.test(m.text())) errors.push(`${user} console: ${m.text()}`); });
  await page.goto(base);
  await page.evaluate((r) => localStorage.setItem('dc.forzarRepetidor', JSON.stringify(r)), relay);
  await page.waitForSelector('text=Iniciar sesión');
  await page.locator('form input').nth(0).fill(user);
  await page.locator('input[type=password]').fill(PW);
  await page.click('button:has-text("Entrar")');
  await page.waitForSelector('.calls-btn:not(.hidden)', { timeout: 15000 });
  await page.waitForTimeout(1500);   // que la centralita registre este equipo
  return { ctx, page, user };
}

async function callFromPanel(caller, name, video) {
  await caller.page.click('.calls-btn');
  const row = caller.page.locator('.call-person', { hasText: name });
  await row.waitFor();
  await row.locator(`button[title="${video ? 'Videollamada' : 'Llamar'}"]`).click();
}

async function accept(p, video) {
  await p.page.waitForSelector('.call-incoming', { timeout: 10000 });
  await p.page.click(video ? '.call-incoming button[title="Aceptar con vídeo"]' : '.call-incoming button[title="Aceptar"]');
}

// Espera a que lleguen vídeo y audio de los demás (conexión WebRTC real).
async function waitMedia(p, remotes, { video = true, timeout = 20000 } = {}) {
  await p.page.waitForFunction(({ remotes, video }) => {
    const tiles = [...document.querySelectorAll('.call-tile')].filter(t => t.dataset.key !== 'local' && !t.dataset.key.startsWith('ring-'));
    if (tiles.length < remotes) return false;
    return tiles.every(t => {
      const a = t.querySelector('audio')?.srcObject;
      const okAudio = a && a.getAudioTracks().some(tr => tr.readyState === 'live');
      const v = t.querySelector('video');
      const okVideo = !video || (t.classList.contains('has-video') && v.videoWidth > 0);
      return okAudio && okVideo;
    });
  }, { remotes, video }, { timeout });
}

async function routeOf(p) {
  await p.page.waitForFunction(() => [...document.querySelectorAll('.call-tile .ct-q')].some(q => q.title), null, { timeout: 15000 });
  return p.page.$$eval('.call-tile .ct-q', qs => qs.map(q => q.title).filter(Boolean));
}

try {
  const A = await person('ana'); const B = await person('berta'); const C = await person('carlos');

  step('Ana llama con vídeo a Berta');
  await callFromPanel(A, 'Berta', true);
  await accept(B, true);
  await waitMedia(A, 1); await waitMedia(B, 1);
  console.log('  ruta:', await routeOf(A));
  await A.page.screenshot({ path: `${out}/1-videollamada-ana.png` });

  step('Ana añade a Carlos (llamada de grupo)');
  await A.page.click('.call-btn[title="Añadir personas"]');
  await A.page.locator('.call-pick', { hasText: 'Carlos' }).locator('input').check();
  await A.page.click('.modal button:has-text("Llamar")');
  await accept(C, true);
  await waitMedia(A, 2); await waitMedia(B, 2); await waitMedia(C, 2);
  await C.page.screenshot({ path: `${out}/2-grupo-carlos.png` });

  step('Berta silencia el micrófono y comparte… (silenciar)');
  await B.page.click('.call-btn[title="Silenciar micrófono"]');
  await A.page.waitForTimeout(500);

  step('Berta cierra la ventana de golpe: los demás la ven salir');
  await B.page.close();
  await A.page.waitForFunction(() => document.querySelectorAll('.call-tile:not([data-key="local"])').length === 1, null, { timeout: 30000 });

  step('Carlos cuelga: la llamada termina para Ana');
  await C.page.click('.call-btn.hang');
  await A.page.waitForSelector('.call-panel', { state: 'detached', timeout: 10000 });
  const camsOff = await A.page.evaluate(() => !document.querySelector('.call-panel'));
  if (!camsOff) errors.push('La llamada no se cerró en el equipo de Ana');

  step('Ronda con el repetidor del anfitrión forzado');
  const A2 = await person('ana', true); const B2 = await person('berta', true);
  await callFromPanel(A2, 'Berta', true);
  await accept(B2, true);
  await waitMedia(A2, 1, { timeout: 30000 }); await waitMedia(B2, 1, { timeout: 30000 });
  const route = await routeOf(A2);
  console.log('  ruta:', route);
  if (!route.some(r => r.includes('a través del anfitrión'))) errors.push('Con el repetidor forzado la ruta no pasa por el anfitrión: ' + route);
  await A2.page.screenshot({ path: `${out}/3-repetidor.png` });
  await B2.page.click('.call-btn.hang');
  await A2.page.waitForSelector('.call-panel', { state: 'detached', timeout: 10000 });

  step('Llamada perdida: Ana llama a Carlos y cuelga antes de que conteste');
  await callFromPanel(A2, 'Carlos', false);
  await C.page.waitForSelector('.call-incoming', { timeout: 10000 });
  await A2.page.click('.call-btn.hang');
  await C.page.waitForSelector('.call-incoming', { state: 'detached', timeout: 10000 });
  await C.page.waitForSelector('text=Llamada perdida', { timeout: 5000 });
} catch (e) {
  errors.push('Fallo: ' + (e.stack || e));
}
await browser.close();
if (errors.length) { console.log(errors.join('\n')); process.exit(1); }
console.log('Llamadas: todo correcto.');

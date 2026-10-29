// Recorrido de todas las pantallas con Playwright: entra como administrador, crea datos básicos,
// visita cada ruta en dos anchos de ventana y falla si hay errores de JavaScript.
// Uso: node tools/e2e_tour.mjs http://127.0.0.1:8770/ <carpeta_capturas>
// (requiere Playwright: npm i playwright; la primera vez crea el administrador ana / Zorro-azul-42)
import { createRequire } from 'module';
import { mkdirSync } from 'fs';

const require = createRequire(process.env.PLAYWRIGHT_FROM || import.meta.url);
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8770/';
const out = process.argv[3] || 'capturas';
mkdirSync(out, { recursive: true });
const ROUTES = ['panel', 'incidencias', 'incidencias/nueva', 'incidencias/INC-001', 'tareas', 'tareas/T-001', 'ideas',
  'semana', 'versiones', 'roadmap', 'calendario', 'renovaciones', 'builds', 'pruebas', 'tiendas', 'chat', 'equipo',
  'equipo/carga', 'equipo/ausencias', 'equipo/daily', 'equipo/tiempo', 'wiki', 'decisiones', 'boveda', 'informes',
  'actividad', 'avisos', 'admin', 'admin/roles', 'admin/proyectos', 'admin/bienvenida', 'ajustes', 'ajustes/sla',
  'ajustes/copias', 'ajustes/internet', 'cuenta', 'cuenta/avisos', 'cuenta/primeros-pasos'];

const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined });
const errors = [];
for (const width of [1360, 900]) {
  const page = await browser.newPage({ viewport: { width, height: 860 } });
  page.on('pageerror', e => errors.push(`[${width}] ${page.url()} pageerror: ${e.message}`));
  page.on('console', m => { if (m.type() === 'error' && !/status of (4\d\d)/.test(m.text())) errors.push(`[${width}] ${page.url()} console: ${m.text()}`); });
  await page.goto(base);
  await page.waitForSelector('text=/Configuración inicial|Iniciar sesión/');
  if (await page.locator('text=Configuración inicial').count()) {
    const inputs = page.locator('form input');
    await inputs.nth(0).fill('Ana García'); await inputs.nth(1).fill('ana');
    await inputs.nth(2).fill('Zorro-azul-42'); await inputs.nth(3).fill('Zorro-azul-42');
    await page.click('text=Crear y entrar');
  } else if (await page.locator('input[type=password]').count()) {
    await page.locator('form input').nth(0).fill('ana');
    await page.locator('input[type=password]').fill('Zorro-azul-42');
    await page.click('button:has-text("Entrar")');
  }
  await page.waitForSelector('.sidebar, .only-narrow');
  // Datos mínimos para que las fichas existan
  await page.evaluate(async () => {
    const tok = localStorage.getItem(Object.keys(localStorage).find(k => k.startsWith('dc.token.'))) ||
      sessionStorage.getItem(Object.keys(sessionStorage).find(k => k.startsWith('dc.token.')));
    const post = (p, b) => fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` }, body: JSON.stringify(b) });
    const has = await fetch('/api/incidents', { headers: { Authorization: `Bearer ${tok}` } }).then(r => r.json());
    if (!has.items?.length) {
      await post('/api/incidents', { uid: crypto.randomUUID().replace(/-/g, ''), title: 'Prueba del recorrido', priority: 'alta', category: 'funcionalidades' });
      await post('/api/tasks', { uid: crypto.randomUUID().replace(/-/g, ''), title: 'Tarea del recorrido' });
    }
  });
  for (const r of ROUTES) {
    await page.goto(`${base}#/${r}`);
    await page.waitForTimeout(700);
    const box = await page.locator('.error-box').count();
    if (box) errors.push(`[${width}] #/${r}: ${await page.locator('.error-box').first().innerText()}`);
    await page.screenshot({ path: `${out}/${width}-${r.replace(/\//g, '_')}.png` });
  }
  await page.close();
}
await browser.close();
if (errors.length) { console.log(errors.join('\n')); process.exit(1); }
console.log(`Recorrido completo sin errores (${ROUTES.length} pantallas × 2 anchos).`);

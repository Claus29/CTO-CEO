// Etiquetas visibles. Deben coincidir con devcontrol/server/common.py.
export const PRIORITIES = { critica: 'Crítica', alta: 'Alta', media: 'Media', baja: 'Baja' };
export const CATEGORIES = { errores: 'Errores', diseno: 'Diseño', funcionalidades: 'Funcionalidades', rendimiento: 'Rendimiento', seguridad: 'Seguridad' };
export const INCIDENT_STATUSES = { pendiente: 'Pendiente', en_progreso: 'En progreso', bloqueada: 'Bloqueada', en_verificacion: 'En verificación', resuelta: 'Resuelta', duplicada: 'Duplicada' };
export const OPEN_INCIDENT = ['pendiente', 'en_progreso', 'bloqueada', 'en_verificacion'];
export const TASK_STATUSES = { pendiente: 'Pendiente', en_progreso: 'En progreso', bloqueada: 'Bloqueada', en_verificacion: 'En verificación', resuelto: 'Resuelto' };
export const IDEA_STATUSES = { propuesta: 'Propuesta', planificada: 'Planificada', en_desarrollo: 'En desarrollo', descartada: 'Descartada' };
export const VERSION_STATUSES = { planificada: 'Planificada', en_curso: 'En curso', publicada: 'Publicada' };
export const BLOCK_REASONS = {
  falta_informacion: 'Falta de información', falta_acceso: 'Falta de acceso o permisos', dependencia: 'Dependencia de otro trabajo',
  problema_tecnico: 'Problema técnico o de código', falta_decision: 'Falta de una decisión', otro: 'Otro motivo',
};
export const FREQUENCIES = { siempre: 'Siempre', a_veces: 'A veces', una_vez: 'Una vez' };
export const ENVIRONMENTS = { produccion: 'Producción', beta: 'Beta', interno: 'Interno / desarrollo' };
export const PLATFORMS = { ios: 'iOS', android: 'Android', web: 'Web', windows: 'Windows', otra: 'Otra' };
export const BUILD_CHANNELS = { interno: 'Interno', beta: 'Beta', produccion: 'Producción' };
export const STORES = { app_store: 'App Store', google_play: 'Google Play', otra: 'Otra tienda' };
export const STORE_STATUSES = { preparando: 'Preparando', enviada: 'Enviada', en_revision: 'En revisión', rechazada: 'Rechazada', aprobada: 'Aprobada', publicada: 'Publicada', retirada: 'Retirada' };
export const TEST_RESULTS = { pendiente: 'Pendiente', ok: 'Correcto', fallo: 'Fallo', bloqueado: 'Bloqueado', omitido: 'Omitido' };
export const DECISION_STATUSES = { propuesta: 'Propuesta', aceptada: 'Aceptada', rechazada: 'Rechazada', sustituida: 'Sustituida' };
export const REPORT_FIELDS = {
  steps: 'Pasos para reproducir', expected: 'Resultado esperado', observed: 'Resultado observado', device: 'Dispositivo',
  os_version: 'Sistema y versión', app_version: 'Versión de la app', environment: 'Entorno', frequency: 'Frecuencia',
};
export const RESOLUTION_STATES = { pendiente: 'Pendiente de verificar', verificada: 'Verificada', rechazada: 'Rechazada' };
export const toOptions = (obj, empty) => [...(empty !== undefined ? [['', empty]] : []), ...Object.entries(obj)];

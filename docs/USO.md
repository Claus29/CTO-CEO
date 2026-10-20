# Uso: roles, permisos y verificación

## Roles predefinidos

| Rol | Pensado para |
|---|---|
| Administrador / CEO | Todo. Siempre debe quedar al menos un usuario que gestione usuarios. |
| Responsable técnico (CTO) | Trabaja y verifica cualquier incidencia o tarea, planifica y ve la actividad de todos. |
| Desarrollador | Trabaja en lo asignado, resuelve, comenta y crea tareas. |
| QA / Tester | Crea incidencias, ejecuta planes de prueba y verifica resoluciones. |
| Observador | Solo lectura. |

Puedes editar estos roles o crear otros marcando permisos uno a uno (área Incidencias, Tareas, Planificación, Calidad,
Chat, Actividad, Conocimiento, Bóveda, Informes, Equipo, Proyectos y Sistema). Los cambios se aplican al momento.
El permiso **«Ver la actividad de todo el equipo»** decide quién puede ver el historial y en qué trabaja cada uno;
sin él, cada persona ve solo su propia actividad.

## Ciclo de una incidencia

```
Pendiente → En progreso → (Bloqueada) → Resuelta por quien la trabaja
                                              │
                                              ▼
                                     En verificación ── rechazada (con motivo) → vuelve a En progreso
                                              │
                                     verificada por quien la creó
                                              ▼
                                           Resuelta
```

- Solo cuenta como resuelta cuando **la verifica quien la creó** (o alguien con permiso de verificar cualquiera).
- Si quien la resuelve es quien la creó, se cierra directamente.
- Cada paso avisa a quien corresponde: asignación, petición de verificación, rechazo, verificación, reapertura.
- Las tareas siguen el mismo ciclo (Pendiente, En progreso, Bloqueada, En verificación, Resuelto).
- La verificación obligatoria se puede desactivar en **Ajustes → General**.

## Otros flujos

- **Duplicados**: al escribir el título aparecen incidencias parecidas; una incidencia puede marcarse como duplicada de otra.
- **SLA**: horas de primera respuesta y de resolución por prioridad; las que están en riesgo o vencidas se avisan.
- **Dependencias**: «A bloquea B». No se puede cerrar B mientras A siga abierta; al cerrarse A se avisa a quien lleva B.
- **GitHub**: cita la clave (`APP-012`) en commits o PR. Una PR abierta pone el elemento en progreso; una fusionada lo
  deja pendiente de verificación (nunca lo cierra sola).
- **Ctrl+K**: busca cualquier cosa o salta a una clave (`APP-12`, `T-3`, `DEC-1`).

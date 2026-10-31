# ROADMAP: ValleBlanco-Soft (MVP Sistema de Administración de Condominios)

> **Versión:** 1.9 (Fase 1 cerrada; S0 a S5-A completados; siguiente: S5-B)
> **Dominio de producción:** `torre2.arequipa.site`
> **Marcas:** ✅ confirmado · 🟡 supuesto propuesto (validar) · ⏳ pendiente de respuesta

---

## 1. Contexto confirmado del negocio

| Dato | Valor |
|------|-------|
| Complejo | Condominio de **8 torres**; el MVP cubre **solo la Torre 2** |
| Torre 2 | **10 pisos × 8 departamentos = 80 unidades** ✅ |
| Tipos de unidad | Solo **departamentos** ✅ |
| Áreas comunes | **SUM** (salón de eventos, se alquila con reserva) y **1 ascensor** ✅ |
| Cuota | **Fija S/ 80.00 mensual** para todos ✅ |
| Medios de pago | Transferencia, depósito, Yape, Plin. **No se acepta efectivo** ✅ |
| Alta de usuarios | La administración los crea e invita ✅ |
| Comprobantes | PDF de recibo interno (no SUNAT) ✅ |
| Datos históricos | Excel con pagos hechos a migrar por CSV ✅ |
| Infraestructura | VPS 2 GB RAM / 2 vCPU, con Docker, Compose y Nginx ya instalados ✅ |

### Visión a futuro (multi-torre)
El MVP opera con una sola torre, pero el modelo de datos se diseña como `Condominio → Torre → Unidad`, con la torre como clave foránea en las tablas de dominio. Así, una futura central de las 8 torres no exigirá rehacer el esquema. La decisión definitiva (una instancia por torre vs. una instancia central) se toma en la Fase 2. 🟡

---

## 2. Alcance del MVP

Todos los módulos pedidos entran en el MVP, pero se entregan en **tres releases** para que cada uno sea usable por sí solo.

### Release 1: Núcleo de cobranza (P0)
| Módulo | App Django | Contenido |
|--------|-----------|-----------|
| M1 Cuentas y roles | `accounts` | Login, usuarios creados e invitados por la administración, roles, recuperación de contraseña |
| M2 Propiedades | `properties` | Condominio, torre, unidades, relación persona ↔ unidad (propietario/inquilino) |
| M3 Finanzas | `finances` | Cargos mensuales, pagos (con comprobante y aprobación), saldo a favor, estado de cuenta, recibo PDF |
| M4 Importación | `finances` / `properties` | Importación CSV de unidades, residentes, deudas e historial de pagos |

### Release 2: Transparencia y comunicación (P1)
| Módulo | App Django | Contenido |
|--------|-----------|-----------|
| M5 Gastos y resumen | `finances` | Registro de egresos, resumen ingresos/egresos y detalle de cada gasto (proveedor, monto y comprobante si existe) visible a propietarios |
| M6 Comunicados | `communications` | Avisos, historial, notificación por correo |
| M7 Correo | `communications` | Avisos de nuevos cargos, vencimientos, pago aprobado/rechazado, comunicados |
| M8 Convenios de pago | `finances` | Refinanciamiento de deudas en cuotas (🟡 alcance por validar) |

### Release 3: Participación (P2)
| Módulo | App Django | Contenido |
|--------|-----------|-----------|
| M9 Reservas del SUM | `reservations` | Calendario, solicitud, aprobación, bloqueo de fechas |
| M10 Votaciones | `voting` | Consultas/votaciones con reglas de elegibilidad y quórum |

### Fuera del MVP
Pasarela de pago en línea, facturación electrónica SUNAT, app móvil nativa, control de accesos/visitas, panel central de las 8 torres.

---

## 3. Roles y permisos (🟡 propuesta)

| Rol | Permisos principales |
|-----|----------------------|
| **Administrador** | Gestión completa del condominio: usuarios, unidades, cargos, pagos, gastos, comunicados, reservas, importación |
| **Junta directiva** | Lectura de reportes financieros y gastos; consulta de pagos; crea/gestiona votaciones y comunicados (🟡) |
| **Tesorero** (miembro de la Junta) | Todo lo de la Junta, más aprobar/rechazar pagos y registrar gastos (🟡) |
| **Propietario** | Su estado de cuenta, subir comprobantes, recibos, resumen de ingresos/egresos, comunicados, reservas, votar |
| **Inquilino** | Estado de cuenta de su unidad ✅, subir comprobantes, comunicados, reservas; voto no habilitado salvo delegación (🟡) |

Un usuario puede tener **varias unidades** ✅. El **tesorero** se modela como un cargo dentro de la Junta, no como un rol independiente. 🟡

---

## 4. Reglas financieras

### 4.1 Reglas confirmadas
1. Cuota fija mensual de **S/ 80.00** por departamento.
2. Se permiten **pagos parciales** y **adelantos**.
3. Existen vecinos con **deuda** y que **refinancian**.
4. **Sin mora ni descuento por ahora**, pero el diseño debe permitirlos.
5. **Sin cuotas extraordinarias por ahora**, pero el diseño debe permitirlas.

### 4.2 Diseño propuesto (🟡)
- **Libro de cargos y pagos:** `Cargo` (qué se debe), `Pago` (qué se pagó) y `AplicacionPago` (cuánto de cada pago cubre cada cargo). Nunca se edita un saldo directamente; todo se deriva de estos registros.
- **Orden de aplicación:** un pago se aplica primero al **cargo más antiguo**. El excedente queda como **saldo a favor** y se aplica automáticamente a cargos futuros.
- **Tipos de cargo:** `CUOTA_ORDINARIA` (activo), `CUOTA_EXTRAORDINARIA`, `MORA`, `AJUSTE`, `CONVENIO` (el modelo los soporta desde el inicio; solo el primero se genera en el MVP).
- **Reglas extensibles:** mora y descuento por pronto pago como configuración por condominio, **desactivadas por defecto**.
- **Dinero:** `Decimal` con 2 decimales, moneda PEN, sin `float`.
- **Auditoría:** quién creó, aprobó o anuló cada movimiento y cuándo. Los pagos y cargos se **anulan**, no se borran.

### 4.3 Flujo de pago con comprobante
```
Residente sube comprobante ─► PENDIENTE ─┬─► APROBADO ─► se aplica a cargos + se genera recibo PDF
                                         └─► RECHAZADO (con motivo) ─► notificación al residente
```
- Datos: fecha, monto, medio (transferencia / depósito / Yape / Plin), número de operación, imagen del comprobante.
- Imágenes: JPG/PNG/WebP (y PDF 🟡), tamaño máximo 5 MB, validación de tipo real del archivo, nombre aleatorio.
- Almacenamiento en **volumen Docker nombrado**, servido **solo a usuarios autorizados** (nunca como archivo público).
- Detección de duplicados por medio + número de operación + monto.

### 4.4 Recibo PDF
Recibo interno con correlativo, unidad, titular, periodos cubiertos, monto, medio y fecha, descargable e imprimible por el residente.

---

## 5. Notificaciones por correo
- Proveedor: **Brevo (SMTP)** recomendado sobre Gmail por mejor entregabilidad y límites; configurable por variables de entorno. ⏳
- Eventos: nuevo cargo, recordatorio de vencimiento, pago aprobado/rechazado, comunicado nuevo, reserva aprobada.
- Sin Celery/Redis en el MVP por la RAM del VPS: envío mediante cola en base de datos + comando programado (`cron`). 🟡

---

## 6. Importación de datos (CSV)
- Plantillas CSV definidas en la Fase 2: `unidades.csv`, `personas.csv`, `relaciones_unidad.csv`, `deudas_iniciales.csv`, `pagos_historicos.csv`.
- Comportamiento: **modo simulación (dry-run)** que valida sin guardar, reporte de errores por fila, **idempotente** (reimportar no duplica) y transaccional.
- Se define con el Excel real (columnas, formato de fechas, numeración de departamentos).
- Correo y teléfono de los residentes son **opcionales** en la importación; la administración los completa o corrige después desde el sistema. ✅

---

## 7. Restricciones de infraestructura (VPS 2 GB / 2 vCPU)

| Decisión | Motivo |
|----------|--------|
| Frontend Vue **compilado a estáticos** (build multi-etapa o en CI), servido por Nginx del Host | Evita el servidor de desarrollo y el consumo de RAM del build en el VPS |
| Gunicorn con 2 workers | Ajustado a 2 vCPU |
| PostgreSQL con parámetros acotados (p. ej. `shared_buffers` ≈ 256 MB) | Dejar memoria al resto |
| Sin Redis/Celery en el MVP | Ahorro de RAM y complejidad |
| Swap de 2 GB en el Host | Colchón ante picos durante `docker compose build` |
| Backups diarios de PostgreSQL + media, copia fuera del VPS | Un solo servidor = punto único de falla |
| Django publicado solo en `127.0.0.1:<puerto>`; Nginx del Host hace el proxy | Superficie de ataque mínima |

---

## 8. Hoja de ruta por fases

### Fase 1: Requerimientos y Hoja de Ruta ✅ (este documento)
Salida: reglas de negocio confirmadas, alcance en tres releases. Sin pendientes: el umbral de morosidad quedó cerrado (sección 12).

### Fase 2: Diseño de Arquitectura
`ARCHITECTURE.md` con modelo ER, contrato de API REST, estructura Vue, autenticación, almacenamiento privado de comprobantes, diseño de plantillas CSV y design system.

### Fase 3: Implementación guiada por TDD (RED → GREEN → REFACTOR)

| Sprint | Contenido | Release |
|--------|-----------|---------|
| S0 | Repo en GitHub, scaffolding del monorepo, `docker-compose.yml`, CI mínimo | R1 |
| S1 ✅ | `accounts`: usuario por correo, invitación, aceptar invitación, login/logout, permisos básicos | R1 |
| S2 ✅ | `properties`: condominio/torre/unidades, relación persona ↔ unidad, roles por torre (`RoleAssignment`) | R1 |
| S3 ✅ | `finances` I: cargos mensuales, libro de movimientos, saldo | R1 |
| S4 ✅ | `finances` II: pagos con comprobante, aprobación, aplicación, saldo a favor | R1 |
| S5-A ✅ | Recibo PDF (interno, inmutable, con correlativo) y estado de cuenta PDF | R1 |
| S5-B ⏭ | Importación CSV (unidades, residentes, deudas iniciales, historial de pagos) | R1 |
| S6 | Frontend base: design system, login, dashboards admin/residente, flujo de pago | R1 |
| S7 | Gastos y resumen ingresos/egresos; comunicados; correo | R2 |
| S8 | Convenios de pago | R2 |
| S9 | Reservas del SUM | R3 |
| S10 | Votaciones | R3 |

**Estado:** S0 a S5-A cerrados (1111 pruebas, cobertura de `finances` 99 %): lo de S4 y, en S5-A, el `Receipt` inmutable con correlativo `R-AAAA-NNNNNN` sin huecos (emitido al aprobar el pago, en la misma transacción), el recibo en PDF (`GET /payments/{id}/receipt/pdf/`, con sello «ANULADO» si el pago se anula) y el estado de cuenta en PDF (`GET /units/{id}/statement/pdf/`), generados con WeasyPrint. Siguiente: S5-B (importación CSV). Pendiente de S4: confirmar con PostgreSQL en el CI las pruebas con hilos, y documentar en la Fase 5 el volumen de comprobantes, `client_max_body_size` y el límite de subidas. Pendiente de S5-A: ver que el job `docker` del CI construya la imagen con las librerías de Pango, valorar un límite de frecuencia para los PDF y decidir en la Fase 4 si el recibo se blinda con un disparador en PostgreSQL.
**Definición de "Hecho":** tests en verde, cobertura ≥ 80 % del módulo, lint y type-check limpios, OpenAPI actualizado, permisos por rol probados.

### Fase 4: Pruebas, Seguridad y Accesibilidad
Revisión OWASP, IDOR entre unidades, límite de intentos en login, validación de subida de archivos, `manage.py check --deploy`, `pip-audit`/`npm audit`, WCAG 2.2 AA, pruebas E2E con Playwright. Salida: `SECURITY.md`.

### Fase 5: Despliegue CI/CD y Servidor
Nginx en el Host, Certbot, GitHub Actions por SSH, `docker compose up -d --build`, backups y rollback. Salida: `DEPLOYMENT.md`.

---

## 9. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| Alcance grande para un MVP | Tres releases; R1 ya es utilizable por sí solo |
| Datos históricos inconsistentes en el Excel | Importación con dry-run y reporte de errores |
| Comprobantes falsos o duplicados | Aprobación manual + detección de duplicados |
| Valor legal de votaciones en línea | Validar con asesor legal antes de R3 (Ley 27157 y reglamento interno) |
| RAM limitada del VPS | Decisiones de la sección 7 |
| Datos personales | Minimización, acceso por rol, consentimiento (normativa peruana de protección de datos personales) |

---

## 10. Decisiones cerradas en Fase 1 (✅ confirmadas por el cliente)

| Tema | Decisión |
|------|----------|
| Vencimiento | **Último día de cada mes** |
| Copropiedad | Puede haber varios propietarios; **solo el titular principal recibe el recibo** |
| Refinanciamiento | Se pacta con el propietario el **número de cuotas y el monto de cada una**; admite **interés** y también **interés 0** |
| Votaciones | **Un voto por unidad**; los inquilinos **no** votan; mayoría mínima **según reglamento** (configurable) |
| SUM | Con **costo, garantía y horarios configurables**; las reservas las **aprueba el administrador** |
| Correo | Brevo; el cliente **controla el DNS** de `arequipa.site` (SPF, DKIM y DMARC) |
| Inicio del sistema | **Noviembre**, mostrando **toda la información histórica** |
| Excel | Es solo **muestra**; los valores finales serán otros |

## 11. Hallazgos del Excel que condicionan el alcance

- El historial anterior a 2024 solo tiene estado mensual (`PAGO` / `DEBE` / `NC`), sin montos ni fechas; desde 2024 hay movimientos bancarios con detalle.
- La muestra no incluye correos, teléfonos ni DNI de los residentes.
- Los datos de la muestra terminan en dic-2024 (cuotas) y abr-2025 (banco), y el sistema arranca en nov-2026.
- Detalle completo y reglas de conversión en `ARCHITECTURE.md`, secciones 2 y 7.

## 12. Decisiones cerradas tras la Fase 2 (✅ confirmadas por el cliente)

| Tema | Decisión |
|------|----------|
| Estado `NC` | Significa **"no corresponde cobrar"** (la unidad aún no pagaba). No genera cargo |
| Celdas vacías | El Excel final **no tendrá celdas vacías**; las no cobrables vendrán como `NC`. La revisión manual se hace en la importación en bloque |
| Cobertura de datos | El Excel final traerá **todos los pagos de todos los años**, incluido el tramo 2025 a oct-2026 |
| Correos y teléfonos | No están disponibles. El sistema permite **completarlos y editarlos después**, a mano, por la administración |
| `DEP.EN EFECTIVO` | Es un **depósito en la cuenta** del banco; cuenta como **Depósito**. "No se acepta efectivo" se refiere a efectivo entregado a la administración |
| Gastos | Los **propietarios** ven el **detalle de cada gasto** (proveedor y monto) y su **comprobante** si existe |
| Umbral de morosidad | **Más de 3 meses de deuda** (≥ 4): es el valor por defecto y sigue siendo configurable |
| Gastos e inquilinos | Los **inquilinos NO** ven el detalle de los gastos: lo ven propietarios, junta y administración (se aplica en S7) |

## 13. Preguntas abiertas (⏳)
Ninguna. Las dos que quedaban se cerraron en S5-A (ver sección 12):
1. Umbral de morosidad: más de 3 meses de deuda (≥ 4).
2. Los inquilinos no ven el detalle de gastos.

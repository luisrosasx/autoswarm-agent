# 📋 TODOs: Fix de Actualizaciones Redundantes de Redes

## 🎯 Objetivo
Parchear autoswarm para leer las redes desde `Spec.TaskTemplate.Networks` (y/o cachear el último valor aplicado) para que deje de emitir actualizaciones redundantes.

---

## 📊 Resumen de TODOs

| Fase | TODOs | Tiempo Estimado | Estado |
|------|-------|-----------------|--------|
| **Análisis** | 4 | 30 min | 🔴 Pendiente |
| **Diseño** | 3 | 20 min | 🔴 Pendiente |
| **Desarrollo** | 5 | 45 min | 🔴 Pendiente |
| **Testing** | 5 | 60 min | 🔴 Pendiente |
| **Documentación** | 2 | 15 min | 🔴 Pendiente |
| **TOTAL** | **19 TODOs** | **~2.5 horas** | 🔴 0% |

---

## 📝 TODOs Detallados por Fase

### 🔍 FASE 1: ANÁLISIS (30 min)

#### ☐ TODO analysis-1: Investigar ubicaciones de redes en Docker Swarm
**Descripción**: Verificar dónde Docker almacena las redes (Spec.Networks vs Spec.TaskTemplate.Networks) después de crear/actualizar servicios

**Acciones**:
- [ ] Crear script de diagnóstico para inspeccionar estructura de Spec
- [ ] Crear servicio de prueba y capturar Spec antes/después de actualización
- [ ] Documentar diferencias observadas
- [ ] Verificar si el comportamiento es consistente

**Entregable**: Documento con findings sobre ubicaciones de Networks

---

#### ☐ TODO analysis-2: Reproducir el problema
**Descripción**: Crear servicio, aplicar redes, verificar logs de reconciliación para confirmar actualizaciones redundantes

**Acciones**:
- [ ] Configurar `AUTOSWARM_LOG_LEVEL=DEBUG`
- [ ] Crear servicio con red Traefik
- [ ] Observar 3-5 ciclos de reconciliación
- [ ] Capturar logs que muestren actualizaciones repetitivas
- [ ] Contar número de actualizaciones redundantes

**Entregable**: Logs que evidencian el problema + métricas (ej: 5 actualizaciones en 5 min)

---

#### ☐ TODO analysis-3: Revisar documentación Docker API
**Descripción**: Entender el comportamiento esperado de Networks en diferentes ubicaciones del Spec

**Acciones**:
- [ ] Leer Docker API docs sobre Service Spec
- [ ] Buscar información sobre Networks en Spec vs TaskTemplate
- [ ] Verificar si comportamiento está documentado o es bug
- [ ] Consultar issues de docker-py en GitHub

**Entregable**: Resumen de hallazgos de la documentación

---

#### ☐ TODO analysis-4: Identificar puntos de lectura de redes
**Descripción**: Encontrar todos los lugares donde se leen current_networks en reconciler.py

**Acciones**:
- [ ] Buscar todas las referencias a `Networks` en el código
- [ ] Identificar otras lecturas similares que puedan tener el mismo problema
- [ ] Verificar si docker_manager.py tiene lecturas similares

**Entregable**: Lista de ubicaciones en el código que necesitan actualización

---

### 🎨 FASE 2: DISEÑO (20 min)

#### ☐ TODO design-1: Definir estrategia de lectura de redes
**Descripción**: Decidir si leer de ambas ubicaciones, prioridad de lectura, o implementar cache

**Acciones**:
- [ ] Evaluar Opción 1: Lectura dual (Spec.Networks + TaskTemplate.Networks)
- [ ] Evaluar Opción 2: Cache de último valor aplicado
- [ ] Evaluar Opción 3: Híbrida (dual + cache)
- [ ] Documentar pros/contras de cada opción
- [ ] **DECISIÓN**: Seleccionar estrategia a implementar

**Entregable**: Documento de decisión técnica

---

#### ☐ TODO design-2: Diseñar función get_current_networks()
**Descripción**: Crear método que lea de Spec.Networks y/o Spec.TaskTemplate.Networks con fallback inteligente

**Acciones**:
- [ ] Definir firma de la función
- [ ] Especificar parámetros de entrada
- [ ] Definir valor de retorno
- [ ] Documentar lógica de prioridad/fallback
- [ ] Crear pseudocódigo

**Entregable**: Diseño detallado de la función con pseudocódigo

---

#### ☐ TODO design-3: Evaluar necesidad de cache
**Descripción**: Determinar si cachear último valor aplicado mejora la solución o si lectura dual es suficiente

**Acciones**:
- [ ] Analizar si lectura dual resuelve 100% del problema
- [ ] Evaluar overhead de implementar cache
- [ ] Considerar edge cases (restart del agente, etc.)
- [ ] **DECISIÓN**: Implementar cache o no

**Entregable**: Decisión documentada sobre implementación de cache

---

### 💻 FASE 3: DESARROLLO (45 min)

#### ☐ TODO dev-1: Implementar get_current_networks() en Reconciler
**Descripción**: Método que lee redes de Spec.Networks, TaskTemplate.Networks, o ambos

**Ubicación**: `src/reconciler.py` (nueva función en clase Reconciler)

**Código esperado**:
```python
def get_current_networks(self, service_spec: Dict) -> List[Dict[str, str]]:
    """
    Obtiene las redes actuales del servicio desde múltiples ubicaciones.

    Docker Swarm puede almacenar redes en:
    - Spec.Networks (nivel de servicio)
    - Spec.TaskTemplate.Networks (nivel de tarea, donde Docker las mueve)

    Returns:
        Lista de configuraciones de red actuales
    """
    # Implementación aquí
```

**Entregable**: Función implementada y funcionando

---

#### ☐ TODO dev-2: Actualizar reconcile_application()
**Descripción**: Reemplazar línea 127 para usar get_current_networks() en lugar de lectura directa

**Ubicación**: `src/reconciler.py`, línea ~127

**Cambio**:
```python
# ANTES:
current_networks = service_spec.get("Networks") or []

# DESPUÉS:
current_networks = self.get_current_networks(service_spec)
```

**Entregable**: Código actualizado en reconcile_application()

---

#### ☐ TODO dev-3: Mejorar logging
**Descripción**: Agregar logs DEBUG que muestren dónde se encontraron las redes actuales y por qué se considera necesaria actualización

**Ubicación**: `src/reconciler.py`, función get_current_networks() y reconcile_application()

**Logs a agregar**:
```python
LOGGER.debug(
    "Service '%s' networks - Spec: %s, TaskTemplate: %s, Using: %s",
    service_name, spec_nets, task_nets, current_networks
)

LOGGER.debug(
    "Service '%s' network comparison - Current: %s, Desired: %s, Needs update: %s",
    service.name, current_networks, desired_networks, needs_network_update
)
```

**Entregable**: Logging mejorado para debugging

---

#### ☐ TODO dev-4: (Opcional) Implementar cache de redes aplicadas
**Descripción**: Si se decide por cache, implementar diccionario {service_name: networks} con timestamp

**Condición**: Solo si design-3 decide implementar cache

**Ubicación**: `src/reconciler.py`, clase Reconciler

**Estructura**:
```python
class Reconciler:
    def __init__(self, ...):
        # ... existente ...
        self._applied_networks_cache: Dict[str, List[Dict]] = {}
        self._cache_timestamps: Dict[str, float] = {}
        self._cache_ttl = 300  # 5 minutos
```

**Entregable**: Cache implementado (si aplicable)

---

#### ☐ TODO dev-5: Agregar validación de normalización
**Descripción**: Asegurar que ambas representaciones de redes se normalicen al mismo formato antes de comparar

**Ubicación**: `src/reconciler.py`, función service_networks_match()

**Acciones**:
- [ ] Verificar que formato de redes sea consistente
- [ ] Normalizar claves (mayúsculas/minúsculas)
- [ ] Manejar orden de redes (si es relevante)
- [ ] Considerar aliases opcionales

**Entregable**: Comparación de redes robusta y normalizada

---

### 🧪 FASE 4: TESTING (60 min)

#### ☐ TODO test-1: Crear test unitario para get_current_networks()
**Descripción**: Probar lectura desde Spec.Networks, TaskTemplate.Networks, y escenarios de fallback

**Ubicación**: Crear `tests/test_reconciler_networks.py`

**Casos de prueba**:
```python
def test_get_current_networks_from_spec():
    """Redes solo en Spec.Networks"""

def test_get_current_networks_from_task_template():
    """Redes solo en TaskTemplate.Networks"""

def test_get_current_networks_both_locations():
    """Redes en ambos lugares - debe priorizar TaskTemplate"""

def test_get_current_networks_empty():
    """Sin redes en ninguna ubicación"""
```

**Entregable**: Tests unitarios con 100% coverage de la función

---

#### ☐ TODO test-2: Test de integración
**Descripción**: Crear servicio, reconciliar, verificar que no genere segunda actualización redundante

**Ubicación**: `tests/integration/test_network_reconciliation.py`

**Flujo**:
```python
def test_no_redundant_network_updates():
    # 1. Crear servicio con red Traefik
    # 2. Primera reconciliación (debe actualizar si necesario)
    # 3. Capturar número de actualizaciones
    # 4. Segunda reconciliación (NO debe actualizar)
    # 5. Tercera reconciliación (NO debe actualizar)
    # 6. Assert: solo 1 actualización en total
```

**Entregable**: Test de integración que valida el fix

---

#### ☐ TODO test-3: Verificar logs
**Descripción**: Confirmar que logs DEBUG muestran correctamente detección de redes y decisiones de actualización

**Acciones**:
- [ ] Ejecutar con `AUTOSWARM_LOG_LEVEL=DEBUG`
- [ ] Crear servicio y observar logs
- [ ] Verificar que muestra dónde encontró las redes
- [ ] Verificar que explica decisión de update/no-update
- [ ] Confirmar claridad de mensajes

**Entregable**: Logs verificados y validados

---

#### ☐ TODO test-4: Test con múltiples redes
**Descripción**: Verificar comportamiento con servicios que tienen múltiples redes overlay incluyendo Traefik

**Escenario**:
```python
def test_multiple_networks():
    # Servicio con:
    # - Red Traefik
    # - Red de aplicación custom
    # - Red de base de datos
    # Verificar detección correcta de todas
```

**Entregable**: Test con múltiples redes funcionando

---

#### ☐ TODO test-5: Test de regresión
**Descripción**: Ejecutar suite completa para asegurar que el parche no rompe funcionalidad existente

**Acciones**:
- [ ] Ejecutar todos los tests existentes
- [ ] Ejecutar `python verify_refactor.py`
- [ ] Verificar que no hay regresiones
- [ ] Validar métricas de performance

**Entregable**: Suite de tests pasando al 100%

---

### 📚 FASE 5: DOCUMENTACIÓN (15 min)

#### ☐ TODO doc-1: Documentar comportamiento de Docker Swarm
**Descripción**: Agregar comentarios explicando por qué se leen redes de múltiples ubicaciones

**Ubicación**: `src/reconciler.py`, función get_current_networks()

**Contenido del comentario**:
```python
"""
Obtiene las redes actuales del servicio desde múltiples ubicaciones.

CONTEXTO:
Docker Swarm tiene un comportamiento donde las redes pueden estar en
diferentes ubicaciones del Spec dependiendo del momento:

1. Al crear/actualizar: Las redes se especifican en Spec.Networks
2. Después de aplicar: Docker puede mover las redes a Spec.TaskTemplate.Networks

Si solo leemos de Spec.Networks, podemos no encontrar redes que ya están
aplicadas, causando actualizaciones redundantes.

SOLUCIÓN:
Esta función lee de ambas ubicaciones y prioriza TaskTemplate.Networks
cuando está disponible, ya que representa el estado efectivo del servicio.

Ver: NETWORK_RECONCILIATION_FIX.md para más detalles.
"""
```

**Entregable**: Código bien documentado

---

#### ☐ TODO doc-2: Actualizar ARCHITECTURE.md
**Descripción**: Documentar la solución al problema de actualizaciones redundantes

**Ubicación**: `ARCHITECTURE.md`

**Sección a agregar**:
```markdown
### Fix: Reconciliación de Redes

**Problema**: Actualizaciones redundantes debido a lectura incorrecta de redes.

**Solución**: Lectura dual de `Spec.Networks` y `Spec.TaskTemplate.Networks`.

**Detalles**: Ver `NETWORK_RECONCILIATION_FIX.md`
```

**Entregable**: ARCHITECTURE.md actualizado

---

## 🎯 Criterios de Aceptación

Para considerar este trabajo COMPLETO, se deben cumplir:

- [x] ✅ Todos los 19 TODOs completados
- [ ] ✅ Tests unitarios pasando (coverage > 90%)
- [ ] ✅ Tests de integración pasando
- [ ] ✅ Logs muestran 0 actualizaciones redundantes en prueba de 5 ciclos
- [ ] ✅ Documentación actualizada y clara
- [ ] ✅ Code review aprobado
- [ ] ✅ Deploy exitoso sin regresiones

---

## 📈 Progreso

```
Progreso: [░░░░░░░░░░░░░░░░░░░░] 0% (0/19 completados)

Estado por fase:
  Análisis:       [░░░░] 0/4
  Diseño:         [░░░] 0/3
  Desarrollo:     [░░░░░] 0/5
  Testing:        [░░░░░] 0/5
  Documentación:  [░░] 0/2
```

---

## 🚀 Próximos Pasos

1. **Iniciar analysis-1**: Crear script de diagnóstico
2. Ejecutar análisis completo (30 min)
3. Proceder a diseño
4. Implementar solución
5. Validar con tests
6. Documentar y desplegar

---

**Creado**: 2025-10-22
**Estimado**: 2.5 horas
**Prioridad**: Alta
**Asignado**: Equipo Autoswarm


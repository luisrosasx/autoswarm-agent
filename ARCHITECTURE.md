# Autoswarm Agent - Architecture Documentation

## Overview

Autoswarm Agent ha sido refactorizado desde una arquitectura monolítica a una arquitectura modular para mejorar la escalabilidad, mantenibilidad y testing.

## Arquitectura Modular

### Diagrama de Componentes

```
┌─────────────────────────────────────────────────────────────┐
│                      autoswarm.py                           │
│                  (Main Orchestrator)                        │
└─────────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
        ▼             ▼             ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   Docker     │ │  Reconciler  │ │    Event     │
│   Manager    │ │              │ │   Monitor    │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       │                ▼                │
       │         ┌──────────────┐        │
       └────────►│   Dokploy    │◄───────┘
                 │    Client    │
                 └──────────────┘
                        │
                        ▼
                 ┌──────────────┐
                 │    Config    │
                 │    Utils     │
                 └──────────────┘
```

## Módulos

### 1. config.py
**Responsabilidad**: Configuración centralizada

**Contenido**:
- Variables de entorno
- Constantes del sistema
- Configuración de logging
- Expresiones regulares compartidas

**Dependencias**: Ninguna (módulo base)

### 2. utils.py
**Responsabilidad**: Utilidades compartidas

**Funciones principales**:
- `get_docker_client()`: Obtener cliente Docker
- `fetch_node_id()`: Obtener ID del nodo Swarm
- `resolve_overlay_network_id()`: Resolver ID de red overlay
- `is_swarm_container()`: Detectar contenedor Swarm
- `should_ignore()`: Verificar si contenedor debe ignorarse
- `derive_service_name()`: Generar nombre de servicio válido

**Dependencias**: `config`, `docker`

### 3. dokploy_client.py
**Responsabilidad**: Comunicación con API de Dokploy

**Clase principal**: `DokployClient`

**Métodos**:
- `list_applications()`: Listar todas las aplicaciones
- `find_application_by_appname()`: Buscar aplicación por nombre
- `update_application()`: Actualizar metadatos de aplicación
- `update_domain()`: Actualizar configuración de dominio

**Características**:
- Cache con TTL configurable
- Thread-safe con locks
- Manejo robusto de errores
- Soporte para TRPC endpoints

**Dependencias**: `config`, `requests`

### 4. docker_manager.py
**Responsabilidad**: Conversión de contenedores a servicios Swarm

**Clase principal**: `DockerManager`

**Métodos principales**:
- `build_service_spec()`: Construir especificación de servicio
- `create_service_from_container()`: Convertir contenedor a servicio
- `collect_networks()`: Recopilar configuración de redes
- `collect_mounts()`: Recopilar montajes
- `collect_ports()`: Recopilar puertos
- `initial_sweep()`: Barrido inicial de contenedores

**Lógica clave**:
- Solo propaga redes overlay
- Detecta necesidad de restricciones de nodo
- Preserva configuración completa del contenedor
- Asegura presencia de red Traefik

**Dependencias**: `config`, `utils`, `docker`

### 5. reconciler.py
**Responsabilidad**: Sincronización Dokploy ↔ Swarm ↔ Traefik

**Clase principal**: `Reconciler`

**Métodos principales**:
- `reconcile_all()`: Reconciliar todas las aplicaciones
- `reconcile_application()`: Reconciliar aplicación específica
- `reconcile_service_by_name()`: Reconciliar servicio por nombre
- `build_desired_labels()`: Construir labels deseados
- `build_desired_networks()`: Construir redes deseadas
- `normalize_router_rule()`: Normalizar reglas de Traefik

**Lógica clave**:
- Detecta drift en labels y redes
- Normaliza reglas `Host()` de Traefik
- Actualiza servicios y Dokploy cuando es necesario
- Manejo de múltiples dominios

**Dependencias**: `config`, `dokploy_client`, `docker`

### 6. event_monitor.py
**Responsabilidad**: Monitoreo de eventos y loops periódicos

**Clases principales**:
- `EventMonitor`: Monitorea eventos de Docker en tiempo real
- `ReconciliationLoop`: Ejecuta reconciliación periódica

**Características**:
- Detección de duplicados con caché
- Ejecución en threads daemon
- Manejo robusto de excepciones
- Soporte para stop events

**Dependencias**: `config`, `docker`

### 7. autoswarm.py
**Responsabilidad**: Orquestación y punto de entrada

**Función principal**: `main()`

**Flujo de ejecución**:
1. Inicializar clientes Docker
2. Obtener información de Swarm (node ID, redes)
3. Inicializar cliente Dokploy
4. Crear instancias de componentes
5. Configurar manejo de señales
6. Ejecutar barrido inicial
7. Iniciar thread de reconciliación
8. Ejecutar event loop (blocking)
9. Shutdown graceful

**Dependencias**: Todos los módulos

## Flujos de Datos

### Flujo 1: Conversión de Contenedor Nuevo

```
Contenedor creado → Event Monitor detecta evento
                           ↓
                    Docker Manager verifica si debe procesar
                           ↓
                    Docker Manager construye spec de servicio
                           ↓
                    Docker Manager crea servicio en Swarm
                           ↓
                    Docker Manager detiene/elimina contenedor
                           ↓
                    Reconciler sincroniza con Dokploy
```

### Flujo 2: Reconciliación Periódica

```
Timer expira → Reconciliation Loop ejecuta
                      ↓
               Dokploy Client obtiene aplicaciones
                      ↓
               Para cada aplicación:
                      ↓
               Reconciler compara estado actual vs deseado
                      ↓
               Si hay diferencias:
                      ↓
               ├─ Actualiza servicio en Swarm
               └─ Actualiza metadatos en Dokploy
```

## Ventajas de la Arquitectura Modular

### 1. Separación de Responsabilidades
Cada módulo tiene una responsabilidad única y bien definida, siguiendo el principio SOLID de responsabilidad única.

### 2. Testabilidad
Los módulos pueden testearse de forma aislada:
- Mock de Dokploy Client para testing sin API real
- Mock de Docker Client para testing sin Docker
- Unit tests por módulo

### 3. Mantenibilidad
- Código más legible (archivos < 300 líneas)
- Fácil localización de bugs
- Cambios aislados sin efectos secundarios

### 4. Escalabilidad
- Componentes pueden reemplazarse (ej: otro cliente de API)
- Fácil agregar nuevas funcionalidades
- Posibilidad de ejecutar componentes en procesos separados

### 5. Reutilización
- Dokploy Client puede usarse en otros proyectos
- Utils compartidas entre módulos
- Patrones claros para extensión

## Comparación con Monolito

| Aspecto | Monolito (antes) | Modular (ahora) |
|---------|------------------|-----------------|
| Líneas por archivo | 701 | 100-250 |
| Dependencias | Todas en un archivo | Claramente definidas |
| Testing | Difícil | Fácil por módulo |
| Debugging | Buscar en 700 líneas | Ir al módulo correcto |
| Extensión | Modificar monolito | Agregar/modificar módulo |
| Reusabilidad | Baja | Alta |

## Compatibilidad

✅ **100% compatible con la versión monolítica**
- Misma funcionalidad exacta
- Mismas variables de entorno
- Mismo comportamiento observable
- Mismo punto de entrada (`python src/autoswarm.py`)

## Testing

Para verificar que el comportamiento es idéntico:

```bash
# Ejecutar versión monolítica (git stash los cambios)
git stash
python src/autoswarm.py

# Ejecutar versión modular (aplicar cambios)
git stash pop
python src/autoswarm.py
```

## Futuras Mejoras Posibles

Con esta arquitectura modular, futuras mejoras son más fáciles:

1. **Métricas y Observabilidad**: Agregar módulo `metrics.py`
2. **Health Checks**: Agregar módulo `health.py`
3. **Multiple Backends**: Soportar otros orquestadores además de Swarm
4. **Plugins**: Sistema de plugins para extensiones custom
5. **API REST**: Agregar API para control externo
6. **WebUI**: Dashboard de monitoreo
7. **Testing Suite**: Tests unitarios y de integración completos


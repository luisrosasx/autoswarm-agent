#!/usr/bin/env python3
"""
autoswarm.py

Enhanced Swarm automation that:
  * Converts unmanaged containers into single-replica Swarm services.
  * Aligns services with Dokploy metadata (labels, networks, domains) so Traefik
    routers stay in sync with what is defined inside the Dokploy UI.
  * Periodically reconciles every application declared in Dokploy, fixing
    drifted labels/networks and warning about inconsistent domain settings.
"""

from __future__ import annotations

import signal
import threading

from config import (
    DOKPLOY_API_KEY,
    DOKPLOY_BASE_URL,
    LOGGER,
    RECONCILE_INTERVAL,
    TRAEFIK_NETWORK_NAME,
)
from docker_manager import DockerManager
from dokploy_client import DokployClient
from event_monitor import EventMonitor, ReconciliationLoop
from reconciler import Reconciler
from utils import (
    fetch_node_id,
    get_docker_api_client,
    get_docker_client,
    resolve_overlay_network_id,
)


def main() -> None:
    """Punto de entrada principal del sistema."""
    # Inicializar clientes Docker
    docker_client = get_docker_client()
    docker_api = get_docker_api_client()

    # Obtener información del nodo Swarm
    local_node_id = fetch_node_id(docker_client)
    traefik_network_id = resolve_overlay_network_id(docker_client, TRAEFIK_NETWORK_NAME)

    # Inicializar cliente Dokploy
    dokploy_client = DokployClient(DOKPLOY_BASE_URL, DOKPLOY_API_KEY)

    # Inicializar componentes principales
    docker_manager = DockerManager(
        docker_client,
        docker_api,
        local_node_id,
        traefik_network_id,
    )

    reconciler = Reconciler(
        docker_client,
        docker_api,
        dokploy_client,
        traefik_network_id,
    )

    event_monitor = EventMonitor(docker_api)

    reconciliation_loop = ReconciliationLoop(
        reconciler.reconcile_all,
        RECONCILE_INTERVAL,
    )

    # Configurar manejo de señales
    stop_event = threading.Event()

    def handle_signal(signum, frame):
        LOGGER.info("Received signal %s; shutting down.", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_signal)

    # Barrido inicial de contenedores existentes
    docker_manager.initial_sweep()

    # Callback para procesar nuevos contenedores detectados
    def process_container(container_id: str) -> None:
        service_name = docker_manager.create_service_from_container(container_id)
        if service_name:
            reconciler.reconcile_service_by_name(service_name)

    # Iniciar thread de reconciliación periódica
    reconcile_thread = threading.Thread(
        target=reconciliation_loop.run,
        args=(stop_event,),
        daemon=True,
    )
    reconcile_thread.start()

    # Ejecutar event loop (blocking)
    event_monitor.event_loop(stop_event, process_container)

    # Esperar a que el thread de reconciliación termine
    reconcile_thread.join(timeout=5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user, exiting.")

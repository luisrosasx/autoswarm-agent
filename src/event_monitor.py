#!/usr/bin/env python3
"""
event_monitor.py

Monitor de eventos Docker para detección de nuevos contenedores.
"""

import threading
import time
from typing import Callable, Set

import docker

from config import LOGGER


class EventMonitor:
    """
    Monitorea eventos de Docker para detectar nuevos contenedores.
    """

    def __init__(self, api_client: docker.APIClient):
        self.api = api_client
        self._handled: Set[str] = set()

    def event_loop(
        self, stop_event: threading.Event, callback: Callable[[str], None]
    ) -> None:
        """
        Bucle principal de monitoreo de eventos.

        Args:
            stop_event: Evento para detener el bucle
            callback: Función a ejecutar cuando se detecta un nuevo contenedor
        """
        while not stop_event.is_set():
            try:
                for event in self.api.events(decode=True):
                    if stop_event.is_set():
                        break
                    if event.get("Type") != "container":
                        continue
                    action = event.get("Action")
                    if action not in {"create", "start"}:
                        continue
                    container_id = event.get("id")
                    if not container_id or container_id in self._handled:
                        continue
                    self._handled.add(container_id)
                    # Ejecutar callback en thread separado
                    threading.Thread(
                        target=callback,
                        args=(container_id,),
                        daemon=True,
                    ).start()
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.error("Event loop error: %s", exc, exc_info=True)
                time.sleep(3)


class ReconciliationLoop:
    """
    Ejecuta reconciliación periódica.
    """

    def __init__(self, reconcile_callback: Callable[[], None], interval: int):
        self.reconcile_callback = reconcile_callback
        self.interval = interval

    def run(self, stop_event: threading.Event) -> None:
        """
        Ejecuta el bucle de reconciliación.

        Args:
            stop_event: Evento para detener el bucle
        """
        while not stop_event.is_set():
            try:
                self.reconcile_callback()
            except Exception:  # pylint: disable=broad-except
                LOGGER.exception("Unexpected error during reconciliation loop.")
            stop_event.wait(self.interval)

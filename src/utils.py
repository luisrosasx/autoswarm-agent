#!/usr/bin/env python3
"""
utils.py

Funciones auxiliares y utilidades para el sistema Autoswarm.
"""

from typing import Dict, Optional

import docker

from config import DOCKER_SOCK, IGNORED_LABEL, LOGGER, MANAGED_LABEL


def get_docker_client() -> docker.DockerClient:
    """Obtiene cliente Docker de alto nivel."""
    return docker.DockerClient(base_url=DOCKER_SOCK)


def get_docker_api_client() -> docker.APIClient:
    """Obtiene cliente API de Docker de bajo nivel."""
    return docker.APIClient(base_url=DOCKER_SOCK)


def fetch_node_id(client: docker.DockerClient) -> str:
    """Obtiene el ID del nodo Swarm actual."""
    info = client.info()
    swarm_info = info.get("Swarm") or {}
    node_id = swarm_info.get("NodeID")
    if not node_id:
        raise RuntimeError("This node is not part of a Swarm cluster.")
    return node_id


def resolve_overlay_network_id(client: docker.DockerClient, name: str) -> Optional[str]:
    """Resuelve el ID de una red overlay por su nombre."""
    try:
        matches = client.networks.list(names=[name])
        if matches:
            return matches[0].id
    except Exception:
        LOGGER.exception("Failed to resolve network %s", name)
    LOGGER.warning("Overlay network '%s' not found.", name)
    return None


def is_swarm_container(labels: Optional[Dict[str, str]]) -> bool:
    """Verifica si un contenedor es parte de Swarm."""
    if not labels:
        return False
    swarm_keys = (
        "com.docker.swarm.service.name",
        "com.docker.swarm.task",
        "com.docker.compose.project",
        MANAGED_LABEL,
    )
    return any(key in labels for key in swarm_keys)


def should_ignore(labels: Optional[Dict[str, str]]) -> bool:
    """Verifica si un contenedor debe ser ignorado."""
    if not labels:
        return False
    return labels.get(IGNORED_LABEL, "false").lower() == "true"


def derive_service_name(container_attrs: Dict) -> str:
    """Deriva un nombre de servicio válido desde los atributos del contenedor."""
    raw_name = container_attrs.get("Name", "").lstrip("/")
    if not raw_name:
        raw_name = container_attrs.get("Id", "")[:12]
    sanitized = []
    for char in raw_name:
        if char.isalnum():
            sanitized.append(char.lower())
        elif char in ("-", "_"):
            sanitized.append(char)
        else:
            sanitized.append("-")
    name = "".join(sanitized).strip("-")
    if not name:
        name = f"autoswarm-{container_attrs.get('Id', '')[:8]}"
    return name

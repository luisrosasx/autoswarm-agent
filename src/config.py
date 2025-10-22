#!/usr/bin/env python3
"""
config.py

Configuración central y constantes del sistema Autoswarm.
"""

import logging
import os
import re

# Configuración de logging
LOG_LEVEL = os.environ.get("AUTOSWARM_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
LOGGER = logging.getLogger("autoswarm")

# Configuración de Docker
DOCKER_SOCK = os.environ.get("DOCKER_HOST", "unix://var/run/docker.sock")

# Configuración de red
TRAEFIK_NETWORK_NAME = os.environ.get("AUTOSWARM_TRAEFIK_NETWORK", "traefik-public")

# Labels del sistema
IGNORED_LABEL = "autoswarm.ignore"
MANAGED_LABEL = "autoswarm.managed"

# Intervalos y timeouts
RECONCILE_INTERVAL = int(os.environ.get("AUTOSWARM_RECONCILE_INTERVAL", "60"))

# Configuración de Dokploy
DOKPLOY_BASE_URL = os.environ.get(
    "AUTOSWARM_DOKPLOY_URL", "http://dokploy:3000"
).rstrip("/")
DOKPLOY_API_KEY = os.environ.get("AUTOSWARM_DOKPLOY_API_KEY")
APPLICATION_CACHE_TTL = int(os.environ.get("AUTOSWARM_DOKPLOY_CACHE_TTL", "30"))

# Expresiones regulares
HOST_RULE_RE = re.compile(r"Host\(`([^`]+)`\)")

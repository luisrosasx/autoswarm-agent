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

import copy
import json
import logging
import os
import re
import signal
import threading
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

import docker
import requests
from docker.errors import APIError, NotFound
from requests import Response
from requests.exceptions import RequestException

LOG_LEVEL = os.environ.get("AUTOSWARM_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
LOGGER = logging.getLogger("autoswarm")


DOCKER_SOCK = os.environ.get("DOCKER_HOST", "unix://var/run/docker.sock")
CLIENT = docker.DockerClient(base_url=DOCKER_SOCK)
API = docker.APIClient(base_url=DOCKER_SOCK)

TRAEFIK_NETWORK_NAME = os.environ.get("AUTOSWARM_TRAEFIK_NETWORK", "traefik-public")
IGNORED_LABEL = "autoswarm.ignore"
MANAGED_LABEL = "autoswarm.managed"
RECONCILE_INTERVAL = int(os.environ.get("AUTOSWARM_RECONCILE_INTERVAL", "60"))

DOKPLOY_BASE_URL = os.environ.get("AUTOSWARM_DOKPLOY_URL", "http://dokploy:3000").rstrip("/")
DOKPLOY_API_KEY = os.environ.get("AUTOSWARM_DOKPLOY_API_KEY")
APPLICATION_CACHE_TTL = int(os.environ.get("AUTOSWARM_DOKPLOY_CACHE_TTL", "30"))

HOST_RULE_RE = re.compile(r"Host\(`([^`]+)`\)")


class DokployClient:
    """
    Lightweight Dokploy API wrapper using TRPC endpoints.
    """

    def __init__(self, base_url: str, api_key: Optional[str]):
        self.base_url = base_url
        self.api_key = api_key
        self.session = requests.Session()
        self._applications: List[Dict] = []
        self._cache_timestamp = 0.0
        self._lock = threading.Lock()

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {"x-api-key": self.api_key or "", "content-type": "application/json"}

    def _handle_response(self, resp: Response, endpoint: str) -> Dict:
        try:
            resp.raise_for_status()
            data = resp.json()
        except (RequestException, ValueError) as exc:
            LOGGER.error("Dokploy %s request failed: %s", endpoint, exc)
            raise
        if "error" in data:
            LOGGER.error("Dokploy %s returned error: %s", endpoint, data["error"])
            raise RuntimeError(f"Dokploy error: {data['error']}")
        return data.get("result", {}).get("data", {}).get("json", {})

    def _refresh_cache(self, force: bool = False) -> None:
        if not self.is_enabled():
            return
        now = time.time()
        if not force and (now - self._cache_timestamp) < APPLICATION_CACHE_TTL:
            return
        params = {"input": json.dumps({})}
        try:
            resp = self.session.get(
                f"{self.base_url}/api/trpc/project.all",
                params=params,
                headers=self._headers(),
                timeout=15,
            )
            payload = self._handle_response(resp, "project.all")
        except Exception:
            LOGGER.exception("Failed to refresh Dokploy project cache.")
            return
        applications: List[Dict] = []
        for project in payload or []:
            for environment in project.get("environments", []):
                for application in environment.get("applications", []):
                    applications.append(application)
        with self._lock:
            self._applications = applications
            self._cache_timestamp = now
        LOGGER.debug("Dokploy cache refreshed with %d applications.", len(applications))

    def list_applications(self) -> List[Dict]:
        self._refresh_cache()
        with self._lock:
            return copy.deepcopy(self._applications)

    def find_application_by_appname(self, app_name: str) -> Optional[Dict]:
        if not self.is_enabled():
            return None
        self._refresh_cache()
        with self._lock:
            for application in self._applications:
                if application.get("appName") == app_name:
                    return copy.deepcopy(application)
        return None

    def update_application(
        self,
        application_id: str,
        labels: Optional[Dict[str, str]] = None,
        networks: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        if not self.is_enabled():
            return
        payload: Dict[str, Dict] = {"0": {"json": {"applicationId": application_id}}}
        if labels is not None:
            payload["0"]["json"]["labelsSwarm"] = labels
        if networks is not None:
            payload["0"]["json"]["networkSwarm"] = networks
        try:
            resp = self.session.post(
                f"{self.base_url}/api/trpc/application.update?batch=1",
                headers=self._headers(),
                json=payload,
                timeout=15,
            )
            self._handle_response(resp, "application.update")
        except Exception:
            LOGGER.exception("Unable to update Dokploy application %s", application_id)
            return
        LOGGER.debug("Dokploy application %s updated.", application_id)
        self._refresh_cache(force=True)

    def update_domain(self, domain_id: str, payload: Dict) -> None:
        if not self.is_enabled():
            return
        body = {"0": {"json": {"domainId": domain_id, **payload}}}
        try:
            resp = self.session.post(
                f"{self.base_url}/api/trpc/domain.update?batch=1",
                headers=self._headers(),
                json=body,
                timeout=15,
            )
            self._handle_response(resp, "domain.update")
        except Exception:
            LOGGER.exception("Unable to update Dokploy domain %s", domain_id)
            return
        LOGGER.debug("Dokploy domain %s updated.", domain_id)
        self._refresh_cache(force=True)


DOKPLOY_CLIENT = DokployClient(DOKPLOY_BASE_URL, DOKPLOY_API_KEY)


def fetch_node_id() -> str:
    info = CLIENT.info()
    swarm_info = info.get("Swarm") or {}
    node_id = swarm_info.get("NodeID")
    if not node_id:
        raise RuntimeError("This node is not part of a Swarm cluster.")
    return node_id


LOCAL_NODE_ID = fetch_node_id()


def resolve_overlay_network_id(name: str) -> Optional[str]:
    try:
        matches = CLIENT.networks.list(names=[name])
        if matches:
            return matches[0].id
    except Exception:
        LOGGER.exception("Failed to resolve network %s", name)
    LOGGER.warning("Overlay network '%s' not found.", name)
    return None


TRAEFIK_NETWORK_ID = resolve_overlay_network_id(TRAEFIK_NETWORK_NAME)


def is_swarm_container(labels: Optional[Dict[str, str]]) -> bool:
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
    if not labels:
        return False
    return labels.get(IGNORED_LABEL, "false").lower() == "true"


def collect_networks(container_attrs: Dict) -> List[Dict]:
    """
    Translate container network attachments into service network definitions.
    Only overlay networks are carried forward; bridge/host/none are skipped.
    Always ensure the Traefik network is present for ingress.
    """
    networks = []
    network_settings = container_attrs.get("NetworkSettings", {})
    networks_cfg = network_settings.get("Networks") or {}
    overlay_names: Set[str] = set()

    for net_name, cfg in networks_cfg.items():
        if net_name in {"bridge", "host", "none"}:
            continue
        overlay_names.add(net_name)

    if TRAEFIK_NETWORK_NAME:
        overlay_names.add(TRAEFIK_NETWORK_NAME)

    existing_networks = {net.name: net for net in CLIENT.networks.list()}
    for name in overlay_names:
        docker_net = existing_networks.get(name)
        if not docker_net:
            LOGGER.warning(
                "Overlay network '%s' not found; create it manually if required.",
                name,
            )
            continue
        if docker_net.attrs.get("Driver") != "overlay":
            LOGGER.warning(
                "Network '%s' is not an overlay network (driver=%s); skipping.",
                name,
                docker_net.attrs.get("Driver"),
            )
            continue
        networks.append({"Target": docker_net.id})

    return networks


def collect_mounts(container_attrs: Dict) -> List[Dict]:
    mounts = []
    for mount in container_attrs.get("Mounts", []):
        target = mount.get("Destination")
        source = mount.get("Source")
        if not target or not source:
            continue
        mount_type = mount.get("Type")
        entry = {
            "Target": target,
            "Source": source,
            "Type": mount_type,
            "ReadOnly": not mount.get("RW", True),
        }
        if mount_type == "bind":
            entry["BindOptions"] = {"Propagation": mount.get("Propagation", "rprivate")}
        mounts.append(entry)
    return mounts


def collect_ports(container_attrs: Dict) -> List[Dict]:
    ports_spec = []
    port_bindings = container_attrs.get("HostConfig", {}).get("PortBindings") or {}
    for container_port_proto, bindings in port_bindings.items():
        if not bindings:
            continue
        port_part, proto = container_port_proto.split("/")
        target_port = int(port_part)
        for binding in bindings:
            published = binding.get("HostPort")
            if not published:
                continue
            publish_mode = (
                "host"
                if binding.get("HostIp") not in (None, "", "0.0.0.0")
                else "ingress"
            )
            ports_spec.append(
                {
                    "Protocol": proto,
                    "TargetPort": target_port,
                    "PublishedPort": int(published),
                    "PublishMode": publish_mode,
                }
            )
    return ports_spec


def requires_local_constraint(mounts: Iterable[Dict]) -> bool:
    for mount in mounts:
        if mount["Type"] == "bind":
            return True
        if mount["Type"] == "volume" and not mount["Source"].startswith(
            "/var/lib/docker/volumes/"
        ):
            # named volumes are node-local; keep things on this node
            return True
    return False


def build_service_spec(container_attrs: Dict) -> Dict:
    config = container_attrs.get("Config", {})
    host_config = container_attrs.get("HostConfig", {})
    service_name = derive_service_name(container_attrs)

    command = config.get("Entrypoint") or None
    args = config.get("Cmd") or None

    mounts = collect_mounts(container_attrs)
    networks = collect_networks(container_attrs)
    ports = collect_ports(container_attrs)

    container_spec = {
        "Image": config.get("Image"),
        "Env": config.get("Env"),
        "User": config.get("User"),
        "Dir": config.get("WorkingDir") or None,
        "Command": command,
        "Args": args,
        "Mounts": mounts if mounts else None,
        "TTY": config.get("Tty", False),
    }

    restart_policy = host_config.get("RestartPolicy", {})
    condition = restart_policy.get("Name") or "any"
    if condition == "no":
        condition = "none"
    restart_spec = {"Condition": condition}
    if restart_policy.get("MaximumRetryCount"):
        restart_spec["MaxAttempts"] = restart_policy["MaximumRetryCount"]

    task_template = {
        "ContainerSpec": {k: v for k, v in container_spec.items() if v},
        "RestartPolicy": restart_spec,
    }

    placement = {}
    if requires_local_constraint(mounts):
        placement["Constraints"] = [f"node.id=={LOCAL_NODE_ID}"]
    if placement:
        task_template["Placement"] = placement

    endpoint_spec = {"Ports": ports} if ports else None

    spec = {
        "Name": service_name,
        "TaskTemplate": task_template,
        "Mode": {"Replicated": {"Replicas": 1}},
        "Labels": {
            MANAGED_LABEL: "true",
            "autoswarm.source": container_attrs.get("Name", "").lstrip("/"),
        },
    }

    if networks:
        spec["Networks"] = networks
    if endpoint_spec:
        spec["EndpointSpec"] = endpoint_spec

    return spec


def derive_service_name(container_attrs: Dict) -> str:
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


def create_service_from_container(container_id: str) -> None:
    try:
        container = CLIENT.containers.get(container_id)
    except NotFound:
        return

    attrs = container.attrs
    labels = attrs.get("Config", {}).get("Labels")
    if should_ignore(labels):
        LOGGER.info("Ignoring container %s due to autoswarm.ignore=true", container.name)
        return
    if is_swarm_container(labels):
        return

    spec = build_service_spec(attrs)
    service_name = spec["Name"]
    LOGGER.info(
        "Creating swarm service '%s' from container '%s' (image=%s).",
        service_name,
        container.name,
        spec["TaskTemplate"]["ContainerSpec"].get("Image"),
    )

    try:
        API.create_service(
            name=spec["Name"],
            labels=spec["Labels"],
            task_template=spec["TaskTemplate"],
            mode=spec["Mode"],
            networks=spec.get("Networks"),
            endpoint_spec=spec.get("EndpointSpec"),
        )
    except APIError as exc:
        LOGGER.error("Failed to create service %s: %s", service_name, exc)
        return

    try:
        container.stop(timeout=5)
    except APIError as exc:
        LOGGER.warning("Failed to stop container %s: %s", container.name, exc)
    try:
        container.remove()
    except APIError as exc:
        LOGGER.warning("Failed to remove container %s: %s", container.name, exc)

    reconcile_service_by_name(service_name)


def reconcile_service_by_name(service_name: str) -> None:
    if not DOKPLOY_CLIENT.is_enabled():
        return
    application = DOKPLOY_CLIENT.find_application_by_appname(service_name)
    if not application:
        LOGGER.debug(
            "No Dokploy application mapping found for service '%s'.", service_name
        )
        return
    try:
        service = CLIENT.services.get(service_name)
    except NotFound:
        LOGGER.debug("Service '%s' not found during reconciliation.", service_name)
        return
    reconcile_application(application, service)


def normalize_router_rule(value: str, host: str) -> Tuple[str, bool]:
    match = HOST_RULE_RE.search(value)
    if match and match.group(1) == host:
        return value, False
    return f"Host(`{host}`)", True


def build_desired_labels(application: Dict) -> Tuple[Dict[str, str], bool]:
    labels = dict(application.get("labelsSwarm") or {})
    domains = application.get("domains") or []
    current_host = None
    for key, value in labels.items():
        if key.endswith(".rule") and "Host(" in value:
            match = HOST_RULE_RE.search(value)
            if match:
                current_host = match.group(1)
                break
    primary_domain = None
    if current_host:
        for domain in domains:
            if domain.get("host") == current_host:
                primary_domain = domain
                break
    if primary_domain is None and domains:
        application_domains = [
            domain for domain in domains if domain.get("domainType") == "application"
        ]
        if application_domains:
            application_domains.sort(
                key=lambda item: item.get("createdAt") or item.get("uniqueConfigKey") or ""
            )
            primary_domain = application_domains[-1]
    changed = False
    if primary_domain:
        host = primary_domain.get("host")
        if host:
            for key, value in list(labels.items()):
                if key.endswith(".rule") and "Host(" in value:
                    new_value, modified = normalize_router_rule(value, host)
                    if modified:
                        labels[key] = new_value
                        changed = True
    return labels, changed


def build_desired_networks(application: Dict) -> List[Dict[str, str]]:
    networks = []
    for entry in application.get("networkSwarm") or []:
        target = entry.get("Target")
        if not target:
            continue
        networks.append({"Target": target, "Aliases": entry.get("Aliases")})

    targets = {net.get("Target") for net in networks}
    if TRAEFIK_NETWORK_ID and TRAEFIK_NETWORK_ID not in targets:
        networks.append({"Target": TRAEFIK_NETWORK_ID})
    elif TRAEFIK_NETWORK_NAME and not TRAEFIK_NETWORK_ID:
        LOGGER.warning("Traefik network '%s' unresolved; skipping auto-attach.", TRAEFIK_NETWORK_NAME)

    return networks


def service_labels_match(current: Dict[str, str], desired: Dict[str, str]) -> bool:
    for key, value in desired.items():
        if current.get(key) != value:
            return False
    return True


def service_networks_match(
    current: List[Dict[str, str]], desired: List[Dict[str, str]]
) -> bool:
    def extract_targets(data: List[Dict[str, str]]) -> Set[str]:
        return {item.get("Target") for item in data if item.get("Target")}

    return extract_targets(current) == extract_targets(desired)


def reconcile_application(application: Dict, service) -> None:
    service_spec = service.attrs.get("Spec", {})
    current_labels = service_spec.get("Labels") or {}
    current_networks = service_spec.get("Networks") or []
    task_template = copy.deepcopy(service_spec.get("TaskTemplate", {}))
    container_spec = task_template.get("ContainerSpec", {})
    container_labels = container_spec.get("Labels") or {}

    desired_labels, labels_changed = build_desired_labels(application)
    desired_networks = build_desired_networks(application)

    if not desired_labels:
        LOGGER.debug("Application %s has no labelsSwarm defined.", application["appName"])
        return

    merged_service_labels = current_labels.copy()
    merged_service_labels.update(desired_labels)

    merged_container_labels = container_labels.copy()
    merged_container_labels.update(desired_labels)
    container_spec["Labels"] = merged_container_labels
    task_template["ContainerSpec"] = container_spec

    needs_label_update = not service_labels_match(current_labels, desired_labels)
    needs_network_update = not service_networks_match(current_networks, desired_networks)
    needs_container_update = not service_labels_match(container_labels, desired_labels)

    if not any([needs_label_update, needs_network_update, needs_container_update]):
        LOGGER.debug("Service '%s' already aligned with Dokploy.", service.name)
        return

    version = service.attrs.get("Version", {}).get("Index")
    if version is None:
        LOGGER.error("Service '%s' missing version metadata; skipping update.", service.name)
        return
    try:
        API.update_service(
            service.id,
            version=version,
            name=service.name,
            labels=merged_service_labels,
            task_template=task_template,
            networks=desired_networks or None,
        )
    except APIError as exc:
        LOGGER.error(
            "Failed to update service '%s' for Dokploy alignment: %s",
            service.name,
            exc,
        )
        return

    LOGGER.info(
        "Updated service '%s' (labels: %s, networks: %s).",
        service.name,
        needs_label_update or needs_container_update,
        needs_network_update,
    )

    if labels_changed:
        DOKPLOY_CLIENT.update_application(
            application["applicationId"],
            labels=desired_labels,
            networks=application.get("networkSwarm"),
        )


def reconcile_all() -> None:
    if not DOKPLOY_CLIENT.is_enabled():
        LOGGER.debug("Dokploy integration disabled; skipping reconciliation loop.")
        return
    applications = DOKPLOY_CLIENT.list_applications()
    services = {service.name: service for service in CLIENT.services.list()}
    for application in applications:
        app_name = application.get("appName")
        if not app_name:
            continue
        service = services.get(app_name)
        if not service:
            LOGGER.debug("Dokploy application '%s' has no matching Swarm service.", app_name)
            continue
        reconcile_application(application, service)


def event_loop(stop_event: threading.Event) -> None:
    handled: Set[str] = set()
    while not stop_event.is_set():
        try:
            for event in API.events(decode=True):
                if stop_event.is_set():
                    break
                if event.get("Type") != "container":
                    continue
                action = event.get("Action")
                if action not in {"create", "start"}:
                    continue
                container_id = event.get("id")
                if not container_id or container_id in handled:
                    continue
                handled.add(container_id)
                threading.Thread(
                    target=create_service_from_container,
                    args=(container_id,),
                    daemon=True,
                ).start()
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.error("Event loop error: %s", exc, exc_info=True)
            time.sleep(3)


def reconciliation_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            reconcile_all()
        except Exception:  # pylint: disable=broad-except
            LOGGER.exception("Unexpected error during reconciliation loop.")
        stop_event.wait(RECONCILE_INTERVAL)


def initial_sweep() -> None:
    """
    On startup, walk existing local containers and convert anything unmanaged.
    """
    LOGGER.info("Performing initial sweep of standalone containers.")
    for container in CLIENT.containers.list(all=True):
        labels = container.attrs.get("Config", {}).get("Labels")
        if is_swarm_container(labels) or should_ignore(labels):
            continue
        create_service_from_container(container.id)


def main() -> None:
    stop_event = threading.Event()

    def handle_signal(signum, frame):
        LOGGER.info("Received signal %s; shutting down.", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_signal)

    initial_sweep()

    reconcile_thread = threading.Thread(
        target=reconciliation_loop, args=(stop_event,), daemon=True
    )
    reconcile_thread.start()

    event_loop(stop_event)
    reconcile_thread.join(timeout=5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user, exiting.")

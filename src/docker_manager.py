#!/usr/bin/env python3
"""
docker_manager.py

Gestión de conversión de contenedores Docker a servicios Swarm.
"""

from typing import Dict, Iterable, List, Set

import docker
from docker.errors import APIError, NotFound

from config import LOGGER, MANAGED_LABEL, TRAEFIK_NETWORK_NAME
from utils import derive_service_name, is_swarm_container, should_ignore


class DockerManager:
    """
    Gestiona la conversión de contenedores standalone a servicios Swarm.
    """

    def __init__(
        self,
        client: docker.DockerClient,
        api_client: docker.APIClient,
        local_node_id: str,
        traefik_network_id: str,
    ):
        self.client = client
        self.api = api_client
        self.local_node_id = local_node_id
        self.traefik_network_id = traefik_network_id

    def collect_networks(self, container_attrs: Dict) -> List[Dict]:
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

        existing_networks = {net.name: net for net in self.client.networks.list()}
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

    def collect_mounts(self, container_attrs: Dict) -> List[Dict]:
        """Colecta y traduce montajes del contenedor."""
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
                entry["BindOptions"] = {
                    "Propagation": mount.get("Propagation", "rprivate")
                }
            mounts.append(entry)
        return mounts

    def collect_ports(self, container_attrs: Dict) -> List[Dict]:
        """Colecta y traduce configuración de puertos."""
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

    def requires_local_constraint(self, mounts: Iterable[Dict]) -> bool:
        """Determina si los montajes requieren restricción de nodo local."""
        for mount in mounts:
            if mount["Type"] == "bind":
                return True
            if mount["Type"] == "volume" and not mount["Source"].startswith(
                "/var/lib/docker/volumes/"
            ):
                # named volumes are node-local; keep things on this node
                return True
        return False

    def build_service_spec(self, container_attrs: Dict) -> Dict:
        """Construye especificación de servicio Swarm desde atributos de contenedor."""
        config = container_attrs.get("Config", {})
        host_config = container_attrs.get("HostConfig", {})
        service_name = derive_service_name(container_attrs)

        command = config.get("Entrypoint") or None
        args = config.get("Cmd") or None

        mounts = self.collect_mounts(container_attrs)
        networks = self.collect_networks(container_attrs)
        ports = self.collect_ports(container_attrs)

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
        if self.requires_local_constraint(mounts):
            placement["Constraints"] = [f"node.id=={self.local_node_id}"]
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

    def create_service_from_container(self, container_id: str) -> str:
        """
        Convierte un contenedor en servicio Swarm.
        Retorna el nombre del servicio creado o cadena vacía si falla.
        """
        try:
            container = self.client.containers.get(container_id)
        except NotFound:
            return ""

        attrs = container.attrs
        labels = attrs.get("Config", {}).get("Labels")
        if should_ignore(labels):
            LOGGER.info(
                "Ignoring container %s due to autoswarm.ignore=true", container.name
            )
            return ""
        if is_swarm_container(labels):
            return ""

        spec = self.build_service_spec(attrs)
        service_name = spec["Name"]
        LOGGER.info(
            "Creating swarm service '%s' from container '%s' (image=%s).",
            service_name,
            container.name,
            spec["TaskTemplate"]["ContainerSpec"].get("Image"),
        )

        try:
            self.api.create_service(
                name=spec["Name"],
                labels=spec["Labels"],
                task_template=spec["TaskTemplate"],
                mode=spec["Mode"],
                networks=spec.get("Networks"),
                endpoint_spec=spec.get("EndpointSpec"),
            )
        except APIError as exc:
            LOGGER.error("Failed to create service %s: %s", service_name, exc)
            return ""

        try:
            container.stop(timeout=5)
        except APIError as exc:
            LOGGER.warning("Failed to stop container %s: %s", container.name, exc)
        try:
            container.remove()
        except APIError as exc:
            LOGGER.warning("Failed to remove container %s: %s", container.name, exc)

        return service_name

    def initial_sweep(self) -> None:
        """
        On startup, walk existing local containers and convert anything unmanaged.
        """
        LOGGER.info("Performing initial sweep of standalone containers.")
        for container in self.client.containers.list(all=True):
            labels = container.attrs.get("Config", {}).get("Labels")
            if is_swarm_container(labels) or should_ignore(labels):
                continue
            self.create_service_from_container(container.id)

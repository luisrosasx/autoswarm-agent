#!/usr/bin/env python3
"""
reconciler.py

Lógica de reconciliación entre Dokploy, Docker Swarm y Traefik.
"""

import copy
from typing import Dict, List, Set, Tuple

import docker
from docker.errors import APIError, NotFound

from config import HOST_RULE_RE, LOGGER, TRAEFIK_NETWORK_NAME
from dokploy_client import DokployClient


class Reconciler:
    """
    Maneja la reconciliación de servicios Swarm con metadatos de Dokploy.
    """

    def __init__(
        self,
        client: docker.DockerClient,
        api_client: docker.APIClient,
        dokploy_client: DokployClient,
        traefik_network_id: str,
    ):
        self.client = client
        self.api = api_client
        self.dokploy_client = dokploy_client
        self.traefik_network_id = traefik_network_id

    def normalize_router_rule(self, value: str, host: str) -> Tuple[str, bool]:
        """Normaliza una regla de router Traefik."""
        match = HOST_RULE_RE.search(value)
        if match and match.group(1) == host:
            return value, False
        return f"Host(`{host}`)", True

    def build_desired_labels(self, application: Dict) -> Tuple[Dict[str, str], bool]:
        """Construye labels deseados desde la aplicación Dokploy."""
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
                domain
                for domain in domains
                if domain.get("domainType") == "application"
            ]
            if application_domains:
                application_domains.sort(
                    key=lambda item: item.get("createdAt")
                    or item.get("uniqueConfigKey")
                    or ""
                )
                primary_domain = application_domains[-1]
        changed = False
        if primary_domain:
            host = primary_domain.get("host")
            if host:
                for key, value in list(labels.items()):
                    if key.endswith(".rule") and "Host(" in value:
                        new_value, modified = self.normalize_router_rule(value, host)
                        if modified:
                            labels[key] = new_value
                            changed = True
        return labels, changed

    def build_desired_networks(self, application: Dict) -> List[Dict[str, str]]:
        """Construye configuración de redes deseada."""
        networks = []
        for entry in application.get("networkSwarm") or []:
            target = entry.get("Target")
            if not target:
                continue
            networks.append({"Target": target, "Aliases": entry.get("Aliases")})

        targets = {net.get("Target") for net in networks}
        if self.traefik_network_id and self.traefik_network_id not in targets:
            networks.append({"Target": self.traefik_network_id})
        elif TRAEFIK_NETWORK_NAME and not self.traefik_network_id:
            LOGGER.warning(
                "Traefik network '%s' unresolved; skipping auto-attach.",
                TRAEFIK_NETWORK_NAME,
            )

        return networks

    def service_labels_match(
        self, current: Dict[str, str], desired: Dict[str, str]
    ) -> bool:
        """Verifica si los labels actuales coinciden con los deseados."""
        for key, value in desired.items():
            if current.get(key) != value:
                return False
        return True

    def service_networks_match(
        self, current: List[Dict[str, str]], desired: List[Dict[str, str]]
    ) -> bool:
        """Verifica si las redes actuales coinciden con las deseadas."""

        def extract_targets(data: List[Dict[str, str]]) -> Set[str]:
            return {item.get("Target") for item in data if item.get("Target")}

        return extract_targets(current) == extract_targets(desired)

    def reconcile_application(self, application: Dict, service) -> None:
        """Reconcilia una aplicación específica con su servicio."""
        service_spec = service.attrs.get("Spec", {})
        current_labels = service_spec.get("Labels") or {}
        current_networks = service_spec.get("Networks") or []
        task_template = copy.deepcopy(service_spec.get("TaskTemplate", {}))
        container_spec = task_template.get("ContainerSpec", {})
        container_labels = container_spec.get("Labels") or {}

        desired_labels, labels_changed = self.build_desired_labels(application)
        desired_networks = self.build_desired_networks(application)

        if not desired_labels:
            LOGGER.debug(
                "Application %s has no labelsSwarm defined.", application["appName"]
            )
            return

        merged_service_labels = current_labels.copy()
        merged_service_labels.update(desired_labels)

        merged_container_labels = container_labels.copy()
        merged_container_labels.update(desired_labels)
        container_spec["Labels"] = merged_container_labels
        task_template["ContainerSpec"] = container_spec

        needs_label_update = not self.service_labels_match(
            current_labels, desired_labels
        )
        needs_network_update = not self.service_networks_match(
            current_networks, desired_networks
        )
        needs_container_update = not self.service_labels_match(
            container_labels, desired_labels
        )

        if not any([needs_label_update, needs_network_update, needs_container_update]):
            LOGGER.debug("Service '%s' already aligned with Dokploy.", service.name)
            return

        version = service.attrs.get("Version", {}).get("Index")
        if version is None:
            LOGGER.error(
                "Service '%s' missing version metadata; skipping update.", service.name
            )
            return
        try:
            self.api.update_service(
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
            self.dokploy_client.update_application(
                application["applicationId"],
                labels=desired_labels,
                networks=application.get("networkSwarm"),
            )

    def reconcile_service_by_name(self, service_name: str) -> None:
        """Reconcilia un servicio específico por nombre."""
        if not self.dokploy_client.is_enabled():
            return
        application = self.dokploy_client.find_application_by_appname(service_name)
        if not application:
            LOGGER.debug(
                "No Dokploy application mapping found for service '%s'.", service_name
            )
            return
        try:
            service = self.client.services.get(service_name)
        except NotFound:
            LOGGER.debug("Service '%s' not found during reconciliation.", service_name)
            return
        self.reconcile_application(application, service)

    def reconcile_all(self) -> None:
        """Reconcilia todas las aplicaciones Dokploy con servicios Swarm."""
        if not self.dokploy_client.is_enabled():
            LOGGER.debug("Dokploy integration disabled; skipping reconciliation loop.")
            return
        applications = self.dokploy_client.list_applications()
        services = {service.name: service for service in self.client.services.list()}
        for application in applications:
            app_name = application.get("appName")
            if not app_name:
                continue
            service = services.get(app_name)
            if not service:
                LOGGER.debug(
                    "Dokploy application '%s' has no matching Swarm service.", app_name
                )
                continue
            self.reconcile_application(application, service)

#!/usr/bin/env python3
"""
dokploy_client.py

Cliente para interactuar con la API de Dokploy usando endpoints TRPC.
"""

import copy
import json
import threading
import time
from typing import Dict, List, Optional

import requests
from requests import Response
from requests.exceptions import RequestException

from config import APPLICATION_CACHE_TTL, LOGGER


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

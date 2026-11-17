"""Minimaler Microsoft-Graph-Client (App-Registrierung, Client-Credentials) für Outlook-Kalender.

Benötigte Anwendungsberechtigung im Azure-Portal: Calendars.ReadWrite (Application) + Admin-Zustimmung.
Empfohlen: per ApplicationAccessPolicy auf die Postfächer der Außendienst-/Filialmitarbeiter beschränken.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

from .config import GraphConfig

log = logging.getLogger("topm.graph")


class GraphError(Exception):
    pass


class GraphClient:
    def __init__(self, cfg: GraphConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.http = session or requests.Session()
        self._token: str | None = None
        self._exp = 0.0

    def _auth(self) -> dict[str, str]:
        if not self._token or time.time() > self._exp - 60:
            r = self.http.post(
                f"{self.cfg.authority}/{self.cfg.tenant_id}/oauth2/v2.0/token",
                data={"client_id": self.cfg.client_id, "client_secret": self.cfg.client_secret,
                      "grant_type": "client_credentials", "scope": "https://graph.microsoft.com/.default"},
                timeout=30)
            if r.status_code != 200:
                raise GraphError(f"Token-Abruf fehlgeschlagen: {r.status_code} {r.text[:300]}")
            j = r.json()
            self._token = j["access_token"]
            self._exp = time.time() + int(j.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}",
                "Prefer": f'outlook.timezone="{self.cfg.timezone}"',
                "Content-Type": "application/json"}

    def _req(self, method: str, path: str, **kw: Any) -> requests.Response:
        url = path if path.startswith("http") else f"{self.cfg.base_url}{path}"
        for attempt in range(4):
            r = self.http.request(method, url, headers=self._auth(), timeout=30, **kw)
            if r.status_code in (429, 503, 504):
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                log.warning("Graph %s -> %s, warte %ss", path, r.status_code, wait)
                time.sleep(min(wait, 30))
                continue
            return r
        return r

    @staticmethod
    def _u(mailbox: str) -> str:
        return f"/users/{quote(mailbox)}"

    # ------------------------------------------------------------------ Events
    def create_event(self, mailbox: str, event: dict) -> dict:
        r = self._req("POST", f"{self._u(mailbox)}/events", json=event)
        if r.status_code not in (200, 201):
            raise GraphError(f"Event anlegen ({mailbox}): {r.status_code} {r.text[:300]}")
        return r.json()

    def update_event(self, mailbox: str, event_id: str, patch: dict) -> dict | None:
        r = self._req("PATCH", f"{self._u(mailbox)}/events/{event_id}", json=patch)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise GraphError(f"Event ändern ({mailbox}): {r.status_code} {r.text[:300]}")
        return r.json()

    def delete_event(self, mailbox: str, event_id: str) -> None:
        r = self._req("DELETE", f"{self._u(mailbox)}/events/{event_id}")
        if r.status_code not in (204, 404):
            raise GraphError(f"Event löschen ({mailbox}): {r.status_code} {r.text[:300]}")

    def get_event(self, mailbox: str, event_id: str) -> dict | None:
        r = self._req("GET", f"{self._u(mailbox)}/events/{event_id}")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise GraphError(f"Event lesen ({mailbox}): {r.status_code} {r.text[:300]}")
        return r.json()

    def calendar_view(self, mailbox: str, start: datetime, end: datetime) -> list[dict]:
        url = (f"{self._u(mailbox)}/calendarView?startDateTime={start.isoformat()}"
               f"&endDateTime={end.isoformat()}&$top=200"
               f"&$select=id,subject,start,end,isAllDay,categories,lastModifiedDateTime,isCancelled,body,location")
        out: list[dict] = []
        while url:
            r = self._req("GET", url)
            if r.status_code != 200:
                raise GraphError(f"Kalender lesen ({mailbox}): {r.status_code} {r.text[:300]}")
            j = r.json()
            out.extend(j.get("value", []))
            url = j.get("@odata.nextLink")
        return out

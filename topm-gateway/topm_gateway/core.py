"""Zusammenbau aller Komponenten + Sync-Jobs mit Sperre (kein paralleler Lauf)."""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Any

from .calendar_sync import CalendarSync
from .config import Settings, load_settings
from .controlling import Controlling
from .graph_client import GraphClient
from .san6_api import San6Service
from .san6_client import San6Client
from .store import Store

log = logging.getLogger("topm.core")


class Gateway:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.store = Store(self.settings.db_path)
        self.client = San6Client(self.settings.san6, tan_provider=self.store.next_tan)
        self.svc = San6Service(self.client)
        self.graph = GraphClient(self.settings.graph) if self.settings.graph.enabled else None
        self.calendar = CalendarSync(self.svc, self.store, self.settings, self.graph)
        self.controlling = Controlling(self.svc, self.store)
        self._lock = threading.Lock()

    def _job(self, name: str, fn: Any) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"job": name, "ok": False, "fehler": "Es läuft bereits ein Sync-Job."}
        log_id = self.store.log_start(name)
        try:
            res = fn()
            self.store.log_end(log_id, True, res)
            return {"job": name, "ok": True, "ergebnis": res}
        except Exception as e:  # noqa: BLE001 – Fehler protokollieren statt Absturz des Workers
            log.exception("Job %s fehlgeschlagen", name)
            self.store.log_end(log_id, False, {"fehler": str(e)})
            return {"job": name, "ok": False, "fehler": str(e)}
        finally:
            self._lock.release()

    def sync_termine(self) -> dict:
        return self._job("termine", self.calendar.run)

    def sync_controlling(self) -> dict:
        res = self._job("controlling", lambda: self.controlling.archive_run(self.settings.sync.rechnungen_days_back))
        if res["ok"]:
            self.store.set("controlling_last_run", date.today().isoformat())
        return res

    def tick(self) -> list[dict]:
        """Ein Scheduler-Durchlauf: Termine immer, Controlling einmal täglich ab SYNC_CONTROLLING_HOUR."""
        out = [self.sync_termine()]
        today = date.today().isoformat()
        if (datetime.now().hour >= self.settings.sync.controlling_hour
                and self.store.get("controlling_last_run") != today):
            out.append(self.sync_controlling())
        return out

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        interval = max(1, self.settings.sync.interval_minutes) * 60
        log.info("Sync-Worker gestartet (Intervall %s min)", interval // 60)
        while not stop.is_set():
            t0 = time.time()
            for r in self.tick():
                log.info("%s: %s", r["job"], "ok" if r["ok"] else r.get("fehler"))
            stop.wait(max(5, interval - (time.time() - t0)))

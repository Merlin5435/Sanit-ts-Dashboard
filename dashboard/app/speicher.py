"""Speicher (SQLite): Briefings, Erledigt/Später, und ein Protokoll aller KI-Anfragen."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import date, datetime


class Speicher:
    def __init__(self, pfad: str):
        if pfad != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(pfad)), exist_ok=True)
        self.db = sqlite3.connect(pfad, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS briefing (id INTEGER PRIMARY KEY, erstellt TEXT, daten TEXT);
                CREATE TABLE IF NOT EXISTS zustand (kid TEXT PRIMARY KEY, zustand TEXT, bis TEXT, geaendert TEXT);
                CREATE TABLE IF NOT EXISTS ki_protokoll (id INTEGER PRIMARY KEY, zeit TEXT, zweck TEXT, modell TEXT,
                    gesendet TEXT, antwort TEXT, eingabe_tokens INTEGER, ausgabe_tokens INTEGER);
            """)
            self.db.commit()

    # --------------------------------------------------------------- Briefing
    def briefing_speichern(self, daten: dict) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO briefing (erstellt, daten) VALUES (?, ?)",
                                  (datetime.now().isoformat(timespec="seconds"), json.dumps(daten, ensure_ascii=False)))
            self.db.commit()
            return cur.lastrowid

    def letztes_briefing(self) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT daten FROM briefing ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(r["daten"]) if r else None

    # --------------------------------------------------------------- Erledigt / Später
    def setze(self, kids: list[str], zustand: str, bis: date | None = None) -> None:
        with self.lock:
            for k in kids:
                if zustand == "offen":
                    self.db.execute("DELETE FROM zustand WHERE kid=?", (k,))
                else:
                    self.db.execute("INSERT OR REPLACE INTO zustand VALUES (?,?,?,?)",
                                    (k, zustand, bis.isoformat() if bis else None, datetime.now().isoformat()))
            self.db.commit()

    def ausgeblendet(self, heute: date) -> set[str]:
        """Erledigte Dinge und solche, die auf später verschoben sind (bis einschließlich gestern ausgeblendet)."""
        with self.lock:
            rows = self.db.execute("SELECT kid, zustand, bis FROM zustand").fetchall()
        return {r["kid"] for r in rows if r["zustand"] == "erledigt"
                or (r["zustand"] == "spaeter" and r["bis"] and r["bis"] > heute.isoformat())}

    def zustaende(self) -> dict[str, str]:
        with self.lock:
            return {r["kid"]: r["zustand"] for r in self.db.execute("SELECT kid, zustand FROM zustand")}

    # --------------------------------------------------------------- KI-Protokoll
    def protokolliere(self, zweck: str, modell: str, gesendet: str, antwort: str, ein: int = 0, aus: int = 0) -> None:
        with self.lock:
            self.db.execute("INSERT INTO ki_protokoll (zeit, zweck, modell, gesendet, antwort, eingabe_tokens, ausgabe_tokens)"
                            " VALUES (?,?,?,?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), zweck, modell,
                                                        gesendet, antwort, ein, aus))
            self.db.commit()

    def protokoll(self, n: int = 20) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM ki_protokoll ORDER BY id DESC LIMIT ?", (n,))]

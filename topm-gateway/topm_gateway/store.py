"""SQLite-Ablage des Gateways.

Tabellen
--------
kv              Schlüssel/Wert (z.B. letzter AUFTRAEGEGEAENDERT-Timestamp)
tan             letzte TAN-Nummer pro Gerät (Kap. 3.3.3: muss streng steigend sein)
termine         letzter bekannter Stand jedes san6-Termins (Snapshot + Hash)
termin_map      Zuordnung san6-Terminnummer <-> Outlook-Event-ID
changes         Änderungsfeed (created/updated/deleted) für nachgelagerte Systeme
rechnungen      Archiv der abgerufenen Rechnungen (Controlling-Historie)
op_verlauf      Tages-Snapshot der offenen Posten (Trend)
sync_log        Protokoll der Sync-Läufe
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS tan (device TEXT PRIMARY KEY, last INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS termine (
    nummer TEXT PRIMARY KEY,
    mitarbeiter_nr TEXT,
    start TEXT, ende TEXT,
    status TEXT,
    hash TEXT NOT NULL,
    data TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_termine_start ON termine(start);
CREATE INDEX IF NOT EXISTS ix_termine_ma ON termine(mitarbeiter_nr);
CREATE TABLE IF NOT EXISTS termin_map (
    nummer TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    event_id TEXT NOT NULL,
    pushed_hash TEXT,
    pushed_start TEXT, pushed_ende TEXT,
    pushed_at TEXT,
    PRIMARY KEY (nummer, mailbox)
);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    entity TEXT NOT NULL,
    key TEXT NOT NULL,
    op TEXT NOT NULL,
    source TEXT NOT NULL,
    data TEXT
);
CREATE TABLE IF NOT EXISTS rechnungen (
    rechnungsnummer TEXT PRIMARY KEY,
    auftragsnummer TEXT,
    rechnungsdatum TEXT,
    zahlcode TEXT,
    konto TEXT,
    name TEXT,
    netto REAL, brutto REAL,
    status TEXT,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_re_datum ON rechnungen(rechnungsdatum);
CREATE TABLE IF NOT EXISTS op_verlauf (
    datum TEXT PRIMARY KEY,
    anzahl INTEGER, summe_offen REAL, summe_ueberfaellig REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT, started TEXT, finished TEXT, ok INTEGER, summary TEXT
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, args))

    # ------------------------------------------------------------- kv
    def get(self, k: str, default: str | None = None) -> str | None:
        r = self.q("SELECT v FROM kv WHERE k=?", (k,))
        return r[0]["v"] if r else default

    def set(self, k: str, v: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    # ------------------------------------------------------------- TAN
    def next_tan(self, device: str) -> str:
        with self.tx() as c:
            r = c.execute("SELECT last FROM tan WHERE device=?", (device,)).fetchone()
            n = (r["last"] if r else 0) + 1
            c.execute("INSERT INTO tan(device,last) VALUES(?,?) ON CONFLICT(device) DO UPDATE SET last=excluded.last",
                      (device, n))
        return f"{device}-{n:08d}"

    # ------------------------------------------------------------- Termine
    def termin_get(self, nummer: str) -> dict | None:
        r = self.q("SELECT * FROM termine WHERE nummer=?", (nummer,))
        if not r:
            return None
        d = dict(r[0])
        d["data"] = json.loads(d["data"])
        return d

    def termine_in_window(self, start: str, ende: str, mitarbeiter: list[str] | None = None,
                          include_deleted: bool = False) -> list[dict]:
        sql = "SELECT * FROM termine WHERE start < ? AND ende >= ?"
        args: list[Any] = [ende, start]
        if not include_deleted:
            sql += " AND deleted=0"
        if mitarbeiter:
            sql += f" AND mitarbeiter_nr IN ({','.join('?' * len(mitarbeiter))})"
            args += mitarbeiter
        sql += " ORDER BY start"
        out = []
        for r in self.q(sql, tuple(args)):
            d = dict(r)
            d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    def termin_upsert(self, t: dict, h: str) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO termine(nummer,mitarbeiter_nr,start,ende,status,hash,data,seen_at,deleted)
                   VALUES(?,?,?,?,?,?,?,?,0)
                   ON CONFLICT(nummer) DO UPDATE SET mitarbeiter_nr=excluded.mitarbeiter_nr, start=excluded.start,
                   ende=excluded.ende, status=excluded.status, hash=excluded.hash, data=excluded.data,
                   seen_at=excluded.seen_at, deleted=0""",
                (t["nummer"], t.get("mitarbeiter_nr", ""), t["start"], t["ende"], t.get("status", ""), h,
                 json.dumps(t, ensure_ascii=False), now_iso()),
            )

    def termin_mark_deleted(self, nummer: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE termine SET deleted=1, seen_at=? WHERE nummer=?", (now_iso(), nummer))

    # ------------------------------------------------------------- Mapping Outlook
    def map_get(self, nummer: str, mailbox: str) -> dict | None:
        r = self.q("SELECT * FROM termin_map WHERE nummer=? AND mailbox=?", (nummer, mailbox))
        return dict(r[0]) if r else None

    def map_by_event(self, mailbox: str, event_id: str) -> dict | None:
        r = self.q("SELECT * FROM termin_map WHERE mailbox=? AND event_id=?", (mailbox, event_id))
        return dict(r[0]) if r else None

    def map_for_mailbox(self, mailbox: str) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM termin_map WHERE mailbox=?", (mailbox,))]

    def map_put(self, nummer: str, mailbox: str, event_id: str, h: str, start: str, ende: str) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO termin_map(nummer,mailbox,event_id,pushed_hash,pushed_start,pushed_ende,pushed_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(nummer,mailbox) DO UPDATE SET event_id=excluded.event_id,
                   pushed_hash=excluded.pushed_hash, pushed_start=excluded.pushed_start,
                   pushed_ende=excluded.pushed_ende, pushed_at=excluded.pushed_at""",
                (nummer, mailbox, event_id, h, start, ende, now_iso()),
            )

    def map_delete(self, nummer: str, mailbox: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM termin_map WHERE nummer=? AND mailbox=?", (nummer, mailbox))

    # ------------------------------------------------------------- Änderungsfeed
    def change(self, entity: str, key: str, op: str, source: str, data: Any = None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO changes(ts,entity,key,op,source,data) VALUES(?,?,?,?,?,?)",
                      (now_iso(), entity, key, op, source,
                       json.dumps(data, ensure_ascii=False) if data is not None else None))

    def changes_since(self, since_id: int, entity: str | None = None, limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM changes WHERE id > ?"
        args: list[Any] = [since_id]
        if entity:
            sql += " AND entity=?"
            args.append(entity)
        sql += " ORDER BY id LIMIT ?"
        args.append(limit)
        out = []
        for r in self.q(sql, tuple(args)):
            d = dict(r)
            d["data"] = json.loads(d["data"]) if d["data"] else None
            out.append(d)
        return out

    # ------------------------------------------------------------- Controlling-Archiv
    def rechnungen_upsert(self, items: list[dict]) -> int:
        n = 0
        with self.tx() as c:
            for r in items:
                c.execute(
                    """INSERT INTO rechnungen(rechnungsnummer,auftragsnummer,rechnungsdatum,zahlcode,konto,name,
                       netto,brutto,status,data,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(rechnungsnummer) DO UPDATE SET auftragsnummer=excluded.auftragsnummer,
                       rechnungsdatum=excluded.rechnungsdatum, zahlcode=excluded.zahlcode, konto=excluded.konto,
                       name=excluded.name, netto=excluded.netto, brutto=excluded.brutto, status=excluded.status,
                       data=excluded.data, updated_at=excluded.updated_at""",
                    (r["rechnungsnummer"], r.get("auftragsnummer"), r.get("rechnungsdatum"), r.get("zahlcode"),
                     (r.get("rechnungsadresse") or {}).get("konto"), (r.get("rechnungsadresse") or {}).get("name"),
                     r.get("netto", 0.0), r.get("brutto", 0.0), r.get("status"),
                     json.dumps(r, ensure_ascii=False), now_iso()),
                )
                n += 1
        return n

    def rechnungen_query(self, von: str, bis: str) -> list[dict]:
        rows = self.q("SELECT data FROM rechnungen WHERE rechnungsdatum BETWEEN ? AND ? ORDER BY rechnungsdatum",
                      (von, bis))
        return [json.loads(r["data"]) for r in rows]

    def op_snapshot(self, datum: str, anzahl: int, offen: float, ueberfaellig: float, data: Any) -> None:
        with self.tx() as c:
            c.execute("""INSERT INTO op_verlauf(datum,anzahl,summe_offen,summe_ueberfaellig,data) VALUES(?,?,?,?,?)
                         ON CONFLICT(datum) DO UPDATE SET anzahl=excluded.anzahl, summe_offen=excluded.summe_offen,
                         summe_ueberfaellig=excluded.summe_ueberfaellig, data=excluded.data""",
                      (datum, anzahl, offen, ueberfaellig, json.dumps(data, ensure_ascii=False)))

    def op_verlauf(self, von: str, bis: str) -> list[dict]:
        rows = self.q("SELECT datum,anzahl,summe_offen,summe_ueberfaellig,data FROM op_verlauf "
                      "WHERE datum BETWEEN ? AND ? ORDER BY datum", (von, bis))
        out = []
        for r in rows:
            d = dict(r)
            d["altersstruktur"] = json.loads(d.pop("data") or "{}")
            out.append(d)
        return out

    # ------------------------------------------------------------- Sync-Log
    def log_start(self, job: str) -> int:
        with self.tx() as c:
            cur = c.execute("INSERT INTO sync_log(job,started,ok) VALUES(?,?,0)", (job, now_iso()))
            return int(cur.lastrowid)

    def log_end(self, log_id: int, ok: bool, summary: Any) -> None:
        with self.tx() as c:
            c.execute("UPDATE sync_log SET finished=?, ok=?, summary=? WHERE id=?",
                      (now_iso(), 1 if ok else 0, json.dumps(summary, ensure_ascii=False), log_id))

    def log_last(self, limit: int = 20) -> list[dict]:
        out = []
        for r in self.q("SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,)):
            d = dict(r)
            d["summary"] = json.loads(d["summary"]) if d["summary"] else None
            d["ok"] = bool(d["ok"])
            out.append(d)
        return out

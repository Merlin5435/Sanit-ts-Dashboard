"""Termin-Synchronisation san6-Timer  <->  Gateway-Snapshot  <->  Outlook (Microsoft 365).

Ablauf eines Laufs
------------------
1. san6 -> Gateway: API-TERMINE für das Fenster [heute - days_back, heute + days_ahead] abrufen,
   gegen den Snapshot vergleichen (Hash) und Änderungen in den Änderungsfeed schreiben
   (created / updated / deleted). Fehlt ein Termin im Fenster, gilt er als gelöscht.
2. Outlook -> san6 (optional): Für jedes bereits übertragene Event prüfen, ob es in Outlook
   verschoben oder gelöscht wurde. Verschiebung -> API-TERMINUPDATE (neue Zeiten),
   Löschung -> API-TERMINUPDATE Status 7 (abgesagt). Konflikt (beidseitig geändert) -> conflict_policy.
   Outlook-Termine mit der Kategorie SYNC_OUTLOOK_NEW_CATEGORY werden per API-TERMINNEU1 angelegt.
3. san6 -> Outlook: neue Termine anlegen, geänderte aktualisieren, abgesagte/gelöschte entfernen.

Serientermine und Termine mit mehreren Mitarbeitern werden von TERMINUPDATE nicht unterstützt
(Kap. 4.18.4) und daher nur in Richtung san6 -> Outlook synchronisiert.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import Settings
from .graph_client import GraphClient
from .san6_api import STATUS_REMOVED, San6Service, termin_hash
from .san6_client import San6Error
from .store import Store
from .xmlutil import fmt_date

log = logging.getLogger("topm.sync.termine")


# ====================================================================== Outlook-Mapping
def termin_to_event(t: dict, category: str, tz: str) -> dict:
    kunde = t.get("kunde") or {}
    auftrag = t.get("auftrag") or {}
    name = kunde.get("Kundenname") or ""
    titel = t.get("thema") or (auftrag.get("Thema") if isinstance(auftrag, dict) else "") or t.get("terminart") or "Termin"
    subject = f"{titel} – {name}" if name else titel
    if t.get("notfall"):
        subject = "NOTFALL: " + subject
    ort = ", ".join(x for x in (kunde.get("Strasse", ""), " ".join(
        x for x in (kunde.get("Plz", ""), kunde.get("Ort", "")) if x)) if x)
    lines = [
        f"san6-Termin {t['nummer']} · {t.get('terminart', '')} · Status: {t.get('status_text', '')}",
        f"Mitarbeiter: {t.get('mitarbeiter', '')}",
    ]
    if name:
        lines.append(f"Kunde: {kunde.get('Kundennr', '')} {name}".strip())
    if kunde.get("Telefon"):
        lines.append(f"Telefon: {kunde['Telefon']}")
    if kunde.get("FixTerminText"):
        lines.append(f"Hinweis: {kunde['FixTerminText']}")
    if isinstance(auftrag, dict) and auftrag.get("Auftragsnummer"):
        lines.append(f"Auftrag: {auftrag.get('Auftragsnummer')} {auftrag.get('Stichwort', '')}".strip())
    if t.get("infos"):
        lines.append("")
        lines.append(t["infos"])
    lines.append("")
    lines.append("Änderungen an Zeit/Absage werden mit san6 synchronisiert. Bitte Inhalt im san6 pflegen.")

    if t.get("ganztaegig"):
        start = {"dateTime": t["start"][:10] + "T00:00:00", "timeZone": tz}
        end = {"dateTime": t["ende"][:10] + "T00:00:00", "timeZone": tz}
    else:
        start = {"dateTime": t["start"] + ":00", "timeZone": tz}
        end = {"dateTime": t["ende"] + ":00", "timeZone": tz}
    return {
        "subject": subject[:255],
        "body": {"contentType": "text", "content": "\n".join(lines)},
        "start": start,
        "end": end,
        "isAllDay": bool(t.get("ganztaegig")),
        "location": {"displayName": ort} if ort else {"displayName": ""},
        "categories": [category],
        "showAs": "tentative" if t.get("status") in ("2", "3") else "busy",
        "importance": "high" if t.get("notfall") else "normal",
        "isReminderOn": t.get("status") not in ("8",),
    }


def _ev_local(ev: dict, key: str) -> str:
    """Graph liefert '2026-11-04T10:00:00.0000000' (in Prefer-Zeitzone) -> '2026-11-04T10:00'"""
    return (ev.get(key) or {}).get("dateTime", "")[:16]


# ====================================================================== Sync
class CalendarSync:
    def __init__(self, svc: San6Service, store: Store, settings: Settings, graph: GraphClient | None = None):
        self.svc = svc
        self.store = store
        self.s = settings
        self.graph = graph

    def window(self, heute: date | None = None) -> tuple[date, date]:
        heute = heute or date.today()
        return heute - timedelta(days=self.s.sync.days_back), heute + timedelta(days=self.s.sync.days_ahead)

    # ------------------------------------------------------------------ Schritt 1
    def pull_san6(self, von: date, bis: date) -> tuple[dict[str, dict], dict[str, int]]:
        mitarbeiter = self.s.sync.mitarbeiter or None
        termine = self.svc.termine(von, bis, mitarbeiter)
        stats = {"abgerufen": len(termine), "neu": 0, "geaendert": 0, "geloescht": 0}
        current: dict[str, dict] = {}
        for t in termine:
            if not t.get("nummer") or not t.get("start"):
                continue
            current[t["nummer"]] = t
            h = termin_hash(t)
            prev = self.store.termin_get(t["nummer"])
            if prev is None or prev["deleted"]:
                op = "created"
                stats["neu"] += 1
            elif prev["hash"] != h:
                op = "updated"
                stats["geaendert"] += 1
            else:
                continue
            self.store.termin_upsert(t, h)
            self.store.change("termin", t["nummer"], op, "san6", t)

        # verschwundene Termine im Fenster
        start_s = datetime.combine(von, datetime.min.time()).isoformat(timespec="minutes")
        ende_s = datetime.combine(bis + timedelta(days=1), datetime.min.time()).isoformat(timespec="minutes")
        for old in self.store.termine_in_window(start_s, ende_s, mitarbeiter):
            if old["nummer"] not in current and old["start"] >= start_s:
                self.store.termin_mark_deleted(old["nummer"])
                self.store.change("termin", old["nummer"], "deleted", "san6", old["data"])
                stats["geloescht"] += 1
        return current, stats

    # ------------------------------------------------------------------ Schritt 2+3
    def mailbox_for(self, t: dict) -> str | None:
        return self.s.sync.outlook_map.get(t.get("mitarbeiter_nr", ""))

    def _termin_update(self, t: dict, start: str, ende: str, status: str | None = None) -> None:
        sd, st = start[:10], start[11:16] or "00:00"
        ed, et = ende[:10], ende[11:16] or "00:00"
        data: dict[str, Any] = {
            "Nummer": t["nummer"],
            "Datum": fmt_date(date.fromisoformat(sd)), "Uhrzeit": st,
            "BisDatum": fmt_date(date.fromisoformat(ed)), "BisUhrzeit": et,
            "Mitarbeiter": t.get("mitarbeiter") or t.get("mitarbeiter_nr"),
        }
        if status is not None:
            data["Status"] = status
        self.svc.termin_update(data)

    def sync_outlook(self, current: dict[str, dict], von: date, bis: date) -> dict[str, int]:
        assert self.graph is not None
        stats = {"outlook_angelegt": 0, "outlook_geaendert": 0, "outlook_entfernt": 0,
                 "san6_verschoben": 0, "san6_abgesagt": 0, "san6_neu": 0, "konflikte": 0, "fehler": 0}
        cat = self.s.graph.category
        tz = self.s.graph.timezone
        v_start = datetime.combine(von, datetime.min.time())
        v_end = datetime.combine(bis + timedelta(days=1), datetime.min.time())

        for ma, mbx in self.s.sync.outlook_map.items():
            try:
                events = {e["id"]: e for e in self.graph.calendar_view(mbx, v_start, v_end)}
            except Exception as e:  # Postfach nicht erreichbar -> nächstes
                log.error("Outlook %s: %s", mbx, e)
                stats["fehler"] += 1
                continue
            handled: set[str] = set()

            # --- 2a) Rückweg Outlook -> san6 für bereits verknüpfte Termine
            for m in self.store.map_for_mailbox(mbx):
                nummer = m["nummer"]
                t = current.get(nummer)
                ev = events.get(m["event_id"])
                if ev is None:
                    ev = self.graph.get_event(mbx, m["event_id"])  # evtl. aus dem Fenster verschoben
                san6_changed = t is None or termin_hash(t) != m["pushed_hash"]
                if ev is None or ev.get("isCancelled"):
                    # in Outlook gelöscht
                    if (t and self.s.sync.outlook_to_san6 and t.get("status") not in STATUS_REMOVED
                            and (not san6_changed or self.s.sync.conflict_policy == "outlook")):
                        try:
                            self._termin_update(t, t["start"], t["ende"], status="7")
                            t["status"], t["status_text"] = "7", "abgesagt"
                            self.store.termin_upsert(t, termin_hash(t))
                            self.store.change("termin", nummer, "updated", "outlook", t)
                            stats["san6_abgesagt"] += 1
                        except San6Error as e:
                            log.error("TERMINUPDATE %s: %s", nummer, e)
                            stats["fehler"] += 1
                        handled.add(nummer)
                    self.store.map_delete(nummer, mbx)
                    continue
                ev_start, ev_end = _ev_local(ev, "start"), _ev_local(ev, "end")
                outlook_changed = (ev_start, ev_end) != ((m["pushed_start"] or "")[:16], (m["pushed_ende"] or "")[:16])
                if not outlook_changed or t is None:
                    continue
                if san6_changed and self.s.sync.conflict_policy != "outlook":
                    stats["konflikte"] += 1
                    self.store.change("termin", nummer, "conflict", "outlook",
                                      {"outlook": [ev_start, ev_end], "san6": [t["start"], t["ende"]],
                                       "gewinner": "san6"})
                    continue  # san6 gewinnt -> wird in 3) überschrieben
                if not self.s.sync.outlook_to_san6 or t.get("serientermin") or len(t.get("mitarbeiter_alle", [])) > 1:
                    continue  # nicht unterstützt -> san6-Stand wird wieder nach Outlook geschrieben
                try:
                    self._termin_update(t, ev_start, ev_end)
                except San6Error as e:
                    log.error("TERMINUPDATE %s: %s", nummer, e)
                    stats["fehler"] += 1
                    continue
                t["start"], t["ende"] = ev_start, ev_end
                h = termin_hash(t)
                self.store.termin_upsert(t, h)
                self.store.map_put(nummer, mbx, m["event_id"], h, ev_start, ev_end)
                self.store.change("termin", nummer, "updated", "outlook", t)
                stats["san6_verschoben"] += 1
                handled.add(nummer)

            # --- 2b) neue Outlook-Termine mit Import-Kategorie -> san6 TERMINNEU1
            newcat = self.s.sync.outlook_new_category
            if newcat and self.s.sync.outlook_to_san6:
                for ev in events.values():
                    cats = ev.get("categories") or []
                    if newcat not in cats or cat in cats or self.store.map_by_event(mbx, ev["id"]):
                        continue
                    if self.store.get(f"imported:{mbx}:{ev['id']}"):
                        continue
                    try:
                        res = self._import_outlook_event(ma, ev)
                    except San6Error as e:
                        log.error("TERMINNEU1 aus Outlook (%s): %s", ev.get("subject"), e)
                        stats["fehler"] += 1
                        continue
                    stats["san6_neu"] += 1
                    s_, e_ = _ev_local(ev, "start"), _ev_local(ev, "end")
                    if res.get("nummer"):  # san6 liefert Terminnummer -> direkt verknüpfen
                        self.store.map_put(res["nummer"], mbx, ev["id"], "", s_, e_)
                        self.store.set(f"imported:{mbx}:{ev['id']}", "linked")
                    else:  # Verknüpfung beim nächsten Abruf über Start/Ende
                        self.store.set(f"imported:{mbx}:{ev['id']}", json.dumps({"start": s_, "ende": e_}))
                    self.graph.update_event(mbx, ev["id"], {"categories": [c for c in cats if c != newcat] + [cat]})

            # --- 3) san6 -> Outlook
            pending = self._pending_imports(mbx)
            for nummer, t in current.items():
                if self.mailbox_for(t) != mbx or nummer in handled and t.get("status") in STATUS_REMOVED:
                    continue
                m = self.store.map_get(nummer, mbx)
                try:
                    if t.get("status") in STATUS_REMOVED:
                        if m:
                            self.graph.delete_event(mbx, m["event_id"])
                            self.store.map_delete(nummer, mbx)
                            stats["outlook_entfernt"] += 1
                        continue
                    h = termin_hash(t)
                    payload = termin_to_event(t, cat, tz)
                    if m is None:
                        ev_id = pending.pop((t["start"], t["ende"]), None)
                        if ev_id:  # aus Outlook importierter Termin taucht jetzt in san6 auf -> verknüpfen
                            self.graph.update_event(mbx, ev_id, payload)
                            self.store.set(f"imported:{mbx}:{ev_id}", "linked")
                        else:
                            payload["transactionId"] = f"san6-{nummer}-{h[:8]}"
                            ev_id = self.graph.create_event(mbx, payload)["id"]
                            stats["outlook_angelegt"] += 1
                        self.store.map_put(nummer, mbx, ev_id, h, t["start"], t["ende"])
                    elif m["pushed_hash"] != h or (nummer not in handled and self._outlook_drift(events, m, t)):
                        if self.graph.update_event(mbx, m["event_id"], payload) is None:
                            ev_id = self.graph.create_event(mbx, payload)["id"]
                        else:
                            ev_id = m["event_id"]
                        self.store.map_put(nummer, mbx, ev_id, h, t["start"], t["ende"])
                        stats["outlook_geaendert"] += 1
                except Exception as e:
                    log.error("Outlook-Push %s -> %s: %s", nummer, mbx, e)
                    stats["fehler"] += 1

            # in san6 verschwundene Termine aus Outlook entfernen
            for m in self.store.map_for_mailbox(mbx):
                if m["nummer"] in current:
                    continue
                old = self.store.termin_get(m["nummer"])
                if old is not None and old["deleted"]:
                    try:
                        self.graph.delete_event(mbx, m["event_id"])
                        self.store.map_delete(m["nummer"], mbx)
                        stats["outlook_entfernt"] += 1
                    except Exception as e:
                        log.error("Outlook-Löschen %s: %s", m["nummer"], e)
                        stats["fehler"] += 1
        return stats

    @staticmethod
    def _outlook_drift(events: dict, m: dict, t: dict) -> bool:
        """Outlook-Zeit weicht vom san6-Stand ab (z.B. Konflikt, san6 gewinnt) -> zurücksetzen."""
        ev = events.get(m["event_id"])
        if not ev:
            return False
        return (_ev_local(ev, "start"), _ev_local(ev, "end")) != (t["start"][:16], t["ende"][:16]) \
            and not t.get("ganztaegig")

    def _pending_imports(self, mbx: str) -> dict[tuple[str, str], str]:
        out = {}
        for r in self.store.q("SELECT k, v FROM kv WHERE k LIKE ?", (f"imported:{mbx}:%",)):
            if r["v"] and r["v"].startswith("{"):
                d = json.loads(r["v"])
                out[(d["start"], d["ende"])] = r["k"].split(":", 2)[2]
        return out

    def _import_outlook_event(self, ma: str, ev: dict) -> dict:
        s_, e_ = _ev_local(ev, "start"), _ev_local(ev, "end")
        body = ((ev.get("body") or {}).get("content") or "")
        if (ev.get("body") or {}).get("contentType") == "html":
            body = re.sub(r"<[^>]+>", " ", body)
        data = {
            "Terminart": self.s.sync.outlook_new_terminart,
            "Mitarbeiter": ma,
            "Datum": fmt_date(date.fromisoformat(s_[:10])), "Uhrzeit": s_[11:16],
            "BisDatum": fmt_date(date.fromisoformat(e_[:10])), "BisUhrzeit": e_[11:16],
            "SpaeterePlanung": "N",
            "Status": "0",
            "Thema": (ev.get("subject") or "")[:80],
            "Info": " ".join(body.split())[:2000],
        }
        loc = (ev.get("location") or {}).get("displayName")
        if loc:
            data["Strasse"] = loc[:80]
        return self.svc.termin_neu(data)

    # ------------------------------------------------------------------ Gesamtlauf
    def run(self, heute: date | None = None) -> dict:
        von, bis = self.window(heute)
        current, stats = self.pull_san6(von, bis)
        out: dict[str, Any] = {"fenster": [von.isoformat(), bis.isoformat()], "san6": stats}
        if self.graph is not None and self.s.sync.outlook_map:
            out["outlook"] = self.sync_outlook(current, von, bis)
        return out


# ====================================================================== iCalendar-Feed
def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    b = line.encode("utf-8")
    if len(b) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        e = ch.encode("utf-8")
        if len(cur) + len(e) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += e
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


VTIMEZONE_BERLIN = """BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


def termine_to_ics(termine: list[dict], name: str = "san6 Termine") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//TopM-Gateway//san6 Termine//DE",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_escape(name)}",
             "X-WR-TIMEZONE:Europe/Berlin", "REFRESH-INTERVAL;VALUE=DURATION:PT15M"]
    lines += VTIMEZONE_BERLIN.split("\n")
    for t in termine:
        if t.get("status") in STATUS_REMOVED or not t.get("start"):
            continue
        ev = termin_to_event(t, "san6", "Europe/Berlin")
        lines += ["BEGIN:VEVENT", f"UID:san6-{t['nummer']}@topm-gateway", f"DTSTAMP:{stamp}"]
        if t.get("ganztaegig"):
            lines.append(f"DTSTART;VALUE=DATE:{t['start'][:10].replace('-', '')}")
            lines.append(f"DTEND;VALUE=DATE:{t['ende'][:10].replace('-', '')}")
        else:
            f = lambda s: s.replace("-", "").replace(":", "")[:13] + "00"  # noqa: E731
            lines.append(f"DTSTART;TZID=Europe/Berlin:{f(t['start'])}")
            lines.append(f"DTEND;TZID=Europe/Berlin:{f(t['ende'])}")
        lines.append(f"SUMMARY:{_ics_escape(ev['subject'])}")
        lines.append(f"DESCRIPTION:{_ics_escape(ev['body']['content'])}")
        if ev["location"]["displayName"]:
            lines.append(f"LOCATION:{_ics_escape(ev['location']['displayName'])}")
        lines.append("STATUS:" + ("TENTATIVE" if t.get("status") in ("2", "3") else "CONFIRMED"))
        lines.append(f"CATEGORIES:{_ics_escape(t.get('terminart', ''))}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"

"""Daten abholen: san6 (über den Client des Gateways) und Microsoft 365 (Graph-API).

Ergebnis ist ein einfaches Dict mit Listen – noch MIT echten Namen. Das verlässt den Server nie;
an die KI geht nur, was kuerzel.py daraus macht.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from topm_gateway.graph_client import GraphClient
from topm_gateway.san6_api import San6Service, normalize_op
from topm_gateway.san6_client import San6Client
from topm_gateway.xmlutil import child_text, find_all, fmt_date, parse_date, parse_num

from .config import Config

log = logging.getLogger("dashboard.abholen")


def _iso(x: date | None) -> str | None:
    return x.isoformat() if x else None


def san6_daten(client: San6Client, cfg: Config, heute: date) -> dict:
    svc = San6Service(client)
    out: dict = {"fehler": []}

    def versuch(name, fn):
        try:
            return fn()
        except Exception as e:  # eine fehlende Freigabe soll nicht den ganzen Abgleich stoppen
            log.warning("san6 %s: %s", name, e)
            out["fehler"].append(f"san6 {name}: {e}")
            return []

    # Termine heute (alle Mitarbeiter, für Auslastung) – Diagnose-Sonderfelder werden nicht übernommen
    out["termine"] = versuch("TERMINE", lambda: [
        {"nummer": t.get("nummer"), "zeit": t.get("start", "")[11:16] if t.get("start") else "",
         "mitarbeiter": t.get("mitarbeiter_nr"), "mitarbeiter_name": (t.get("mitarbeiter") or "").split("-", 1)[-1],
         "thema": t.get("thema"), "kunde_nr": (t.get("kunde") or {}).get("Kundennr", ""),
         "kunde": (t.get("kunde") or {}).get("Kundenname", ""), "info": t.get("infos")}
        for t in svc.termine(heute, heute)])

    def rezepte():
        r = client.get("REZEPTANFORDERUNGEN")
        return [{"auftrag": child_text(e, "Auftragsnummer"), "stichwort": child_text(e, "Stichwort"),
                 "anfrage": _iso(parse_date(child_text(e, "Anforderungsdatum"))),
                 "tage": (heute - (parse_date(child_text(e, "Anforderungsdatum")) or heute)).days,
                 "kunde_nr": child_text(e, "KundenNr"), "kunde": child_text(e, "Kunde"),
                 "versichertennr": child_text(e, "VersichertenNr"), "kasse": child_text(e, "Krankenkasse"),
                 "arzt": child_text(e, "Arzt"), "arzt_nr": child_text(e, "ArztNr")}
                for e in find_all(r.root, "Anforderung")]
    out["rezepte"] = versuch("REZEPTANFORDERUNGEN", rezepte)

    def wiedervorlagen():
        r = client.get("WIEDERVORLAGEN")
        return [{"nummer": child_text(e, "Nummer"), "datum": _iso(parse_date(child_text(e, "Vorlagedatum"))),
                 "faellig": (parse_date(child_text(e, "Vorlagedatum")) or heute) <= heute,
                 "typ": child_text(e, "Typ"), "vorgang": child_text(e, "Vorgang"),
                 "bemerkung": child_text(e, "Bemerkung")} for e in find_all(r.root, "Wiedervorlage")]
    out["wiedervorlagen"] = versuch("WIEDERVORLAGEN", wiedervorlagen)

    def orgas(gruppe: str, quelle: str):
        r = client.get("OFFENEORGAS", {"SachbearbeiterGruppe": gruppe})
        return [{"nummer": child_text(e, "Nummer"), "datum": _iso(parse_date(child_text(e, "AngelegtDatum"))),
                 "von": child_text(e, "Von"), "thema": child_text(e, "Thema"), "text": child_text(e, "Text"),
                 "kunde_nr": child_text(e, "Kundennr"), "kunde": child_text(e, "Kunde"), "quelle": quelle}
                for e in find_all(r.root, "Orga")]
    out["hotline"] = versuch("OFFENEORGAS Hotline", lambda: orgas(cfg.hotline_gruppe, "hotline"))
    out["orgas_chef"] = versuch("OFFENEORGAS Chef", lambda: orgas(cfg.chef_nummer, "intern"))

    out["offene_rechnungen"] = versuch("INKASSO1", lambda: [
        normalize_op(e, heute) for e in find_all(client.get("INKASSO1").root, "Rechnung")])

    def rueckstaende():
        r = client.get("EINKAUF5")
        rows = []
        for lief in find_all(r.root, "Einkauf5"):
            name = child_text(lief, "Name")
            for b in find_all(lief, "Bestellung"):
                pos = find_all(b, "Position")
                rows.append({"bestellung": child_text(b, "Bestellnummer"), "lieferant": name,
                             "liefertermin": _iso(parse_date(child_text(b, "Liefertermin"))),
                             "artikel": ", ".join(child_text(p, "Bezeichnung") for p in pos),
                             "offen": sum(parse_num(child_text(p, "Offen")) for p in pos)})
        return rows
    out["rueckstaende"] = versuch("EINKAUF5", rueckstaende)

    # Umsatz: dieser Monat bis gestern vs. gleicher Zeitraum im Vorjahr
    def umsatz():
        start = heute.replace(day=1)
        bis = heute - timedelta(days=1)
        if bis < start:
            return {"monat": 0.0, "vorjahr": 0.0}
        vj_start, vj_bis = start.replace(year=start.year - 1), bis.replace(year=bis.year - 1)
        akt = svc.rechnungen(start, bis, mit_positionen=False)
        vj = svc.rechnungen(vj_start, vj_bis, mit_positionen=False)
        summe = lambda rs: round(sum(x.get("netto") or 0 for x in rs), 2)  # noqa: E731
        return {"monat": summe(akt), "vorjahr": summe(vj)}
    out["umsatz"] = versuch("RECHNUNGEN", umsatz) or {"monat": 0.0, "vorjahr": 0.0}
    return out


def outlook_daten(graph: GraphClient, postfach: str, heute: date) -> dict:
    out: dict = {"fehler": [], "mails": [], "termine": []}
    try:
        r = graph._req("GET", f"/users/{postfach}/mailFolders/inbox/messages", params={"$top": 30})
        for m in r.json().get("value", []):
            out["mails"].append({
                "id": m["id"], "betreff": m.get("subject", ""),
                "von": m["from"]["emailAddress"].get("name", ""), "adresse": m["from"]["emailAddress"].get("address", ""),
                "zeit": m.get("receivedDateTime", ""), "gelesen": m.get("isRead", False),
                "markiert": (m.get("flag") or {}).get("flagStatus") == "flagged",
                "text": (m.get("body") or {}).get("content") or m.get("bodyPreview", "")})
    except Exception as e:
        out["fehler"].append(f"Outlook Mails: {e}")
    try:
        s = datetime.combine(heute, datetime.min.time())
        r = graph._req("GET", f"/users/{postfach}/calendarView",
                       params={"startDateTime": s.isoformat(), "endDateTime": (s + timedelta(days=1)).isoformat()})
        for ev in r.json().get("value", []):
            out["termine"].append({"zeit": ev["start"]["dateTime"][11:16], "bis": ev["end"]["dateTime"][11:16],
                                   "betreff": ev.get("subject", ""), "ort": (ev.get("location") or {}).get("displayName", "")})
    except Exception as e:
        out["fehler"].append(f"Outlook Kalender: {e}")
    return out


def termine_chef(san6: dict, outlook: dict, chef: str) -> list[dict]:
    rows = [{"zeit": t["zeit"], "titel": t["info"] or t["thema"], "quelle": "san6"}
            for t in san6.get("termine", []) if t.get("mitarbeiter") == chef]
    rows += [{"zeit": t["zeit"], "titel": t["betreff"], "quelle": "Outlook"} for t in outlook.get("termine", [])]
    return sorted(rows, key=lambda x: x["zeit"])

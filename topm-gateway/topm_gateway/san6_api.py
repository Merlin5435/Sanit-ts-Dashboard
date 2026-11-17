"""Fachliche Kapselung der benötigten san6-Funktionen inkl. Normalisierung nach JSON.

Termine (Kap. 4.18):  TERMINE, TERMINNEU, TERMINNEU1, TERMINUPDATE, TERMINARTEN, TERMINFILIALEN, TERMINBELEGUNG
Controlling:          RECHNUNGEN (4.7.12), INKASSO1 (4.22.1), ABRECHNUNG1 (4.15.1), BESTELLUNGEN1 (4.9.1),
                      EINKAUF5 (4.14.5), ARTIKELBESTAND (4.3.3), AUFTRAEGEGEAENDERT (4.7.11),
                      ANZAHLORGAS (4.10.1), OFFENEORGAS (4.10.2), KOSTENRECHNUNG (4.14.6)
System:               PING (4.2.1), STATUS (4.1.2)
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any

from .san6_client import San6Client
from .xmlutil import (as_list, child, child_text, find_all, find_first, fmt_date, parse_date, parse_num,
                      parse_time, xml_to_obj)

TERMIN_STATUS = {
    "0": "bestätigt", "1": "abgelaufen", "2": "angefragt", "3": "optional",
    "7": "abgesagt", "8": "erledigt", "9": "gelöscht",
}
STATUS_REMOVED = {"7", "9"}  # werden im Kalender entfernt


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _nr(mitarbeiter: str) -> str:
    """'012-Iltgen' / '000-Hr. Demouser' -> '012'"""
    m = (mitarbeiter or "").strip()
    head = m.split("-", 1)[0].strip()
    return head if head.isdigit() else m[:3]


def _jn(v: str) -> bool:
    return (v or "").strip().upper() in ("J", "JA", "1", "TRUE")


def _obj(el: ET.Element | None) -> Any:
    return xml_to_obj(el) if el is not None else None


# ====================================================================== Termine
def normalize_termin(el: ET.Element) -> dict:
    datum = parse_date(child_text(el, "Datum"))
    zeit = parse_time(child_text(el, "Zeit"))
    bis_datum = parse_date(child_text(el, "BisDatum")) or datum
    bis_zeit = parse_time(child_text(el, "BisZeit"))
    ganztaegig = zeit is None
    if datum is None:
        start = ende = None
    elif ganztaegig:
        start = datetime.combine(datum, datetime.min.time())
        ende = datetime.combine((bis_datum or datum) + timedelta(days=1), datetime.min.time())
    else:
        start = datetime.combine(datum, zeit)
        ende = datetime.combine(bis_datum or datum, bis_zeit) if bis_zeit else start + timedelta(minutes=30)
        if ende <= start:
            ende = start + timedelta(minutes=30)

    mitarbeiter_all = [c.text.strip() for c in el if c.tag.lower() == "mitarbeiter" and c.text]
    mitarbeiter = mitarbeiter_all[0] if mitarbeiter_all else ""
    status = child_text(el, "Status")
    return {
        "nummer": child_text(el, "Nummer"),
        "terminart": child_text(el, "Terminart"),
        "thema": child_text(el, "Thema"),
        "status": status,
        "status_text": TERMIN_STATUS.get(status, status),
        "start": start.isoformat(timespec="minutes") if start else None,
        "ende": ende.isoformat(timespec="minutes") if ende else None,
        "ganztaegig": ganztaegig,
        "serientermin": child_text(el, "Serientermin"),
        "serientermin_endet": child_text(el, "SerienterminEndet"),
        "mitarbeiter": mitarbeiter,
        "mitarbeiter_nr": _nr(mitarbeiter),
        "mitarbeiter_alle": mitarbeiter_all,
        "farbe": child_text(el, "Farbe"),
        "spaetere_planung": _jn(child_text(el, "SpaeterePlanung")),
        "infos": child_text(el, "Infos"),
        "notfall": _jn(child_text(el, "Notfall")),
        "kunde": _obj(child(el, "Kunde")) or {},
        "auftrag": _obj(child(el, "Auftrag")) or {},
        "hilfsmittel": _obj(child(el, "Hilfsmittel")),
    }


def termin_hash(t: dict) -> str:
    return hashlib.sha1(json.dumps(t, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ====================================================================== Rechnungen
def _positionen(el: ET.Element) -> list[dict]:
    out: list[dict] = []
    for pos_block in [c for c in el if c.tag.lower() in ("positionen", "position")]:
        inner = [c for c in pos_block if len(c)]
        items = inner if inner and all(len(c) for c in pos_block) else [pos_block]
        for p in items:
            d = xml_to_obj(p)
            if not isinstance(d, dict):
                continue
            menge = parse_num(d.get("Menge"))
            out.append({
                "artikelnummer": d.get("Artikelnummer", ""),
                "groesse": d.get("Groesse", ""),
                "bezeichnung": d.get("Bezeichnung", ""),
                "menge": menge,
                "einheit": d.get("Einheit", ""),
                "vk_preis": parse_num(d.get("VKPreis")),
                "rabatt_prozent": parse_num(d.get("Rabatt")),
                "gesamt": parse_num(d.get("Gesamt")),
                "mwst_kz": d.get("MwSt", ""),
                "ean": d.get("EAN", ""), "pzn": d.get("PZN", ""), "hmv": d.get("HMV", ""),
            })
    return out


def normalize_rechnung(el: ET.Element, mit_positionen: bool = True) -> dict:
    netto = parse_num(child_text(el, "Summe"))
    brutto = parse_num(child_text(el, "Rechnungsbetrag"))
    adr = child(el, "Rechnungsadresse")
    adresse = {}
    if adr is not None:
        adresse = {k.lower(): v for k, v in (xml_to_obj(adr) or {}).items()} if len(adr) else {}
    r = {
        "rechnungsnummer": child_text(el, "Rechnungsnummer"),
        "auftragsnummer": child_text(el, "Auftragsnummer"),
        "rechnungsdatum": _iso(parse_date(child_text(el, "Rechnungsdatum"))),
        "zahlcode": child_text(el, "Zahlcode"),
        "lieferschein": child_text(el, "Lieferschein"),
        "versandtermin": _iso(parse_date(child_text(el, "Versandtermin"))),
        "netto": round(netto, 2),
        "netto_ohne_mwst": parse_num(child_text(el, "Summe0")),
        "netto_mwst1": parse_num(child_text(el, "Summe1")),
        "netto_mwst2": parse_num(child_text(el, "Summe2")),
        "brutto": round(brutto, 2),
        "mwst": round(brutto - netto, 2),
        "status": child_text(el, "Status"),
        "rechnungsadresse": adresse,
    }
    if mit_positionen:
        r["positionen"] = _positionen(el)
    return r


def normalize_op(el: ET.Element, heute: date) -> dict:
    ziel = parse_date(child_text(el, "Zahlungsziel"))
    re_datum = parse_date(child_text(el, "Rechnungsdatum"))
    offen = parse_num(child_text(el, "Offen"))
    betrag = parse_num(child_text(el, "Betrag"))
    if offen == 0 and not child_text(el, "Offen"):
        offen = betrag - parse_num(child_text(el, "gezahlt"))
    faellig = ziel or re_datum
    tage = (heute - faellig).days if faellig else 0
    return {
        "rechnungsnummer": child_text(el, "Rechnungsnummer"),
        "auftragsnummer": child_text(el, "Auftragsnummer"),
        "rechnungstyp": child_text(el, "Rechnungstyp"),
        "rechnungsdatum": _iso(re_datum),
        "vorgangsdatum": _iso(parse_date(child_text(el, "Vorgangsdatum"))),
        "status": child_text(el, "Status"),
        "auftragstyp": child_text(el, "Auftragstyp"),
        "stichwort": child_text(el, "Stichwort"),
        "kundennr": child_text(el, "Kundennr"),
        "adresstyp": child_text(el, "Adresstyp"),
        "name": " ".join(x for x in (child_text(el, "Name"), child_text(el, "Name1")) if x),
        "ort": child_text(el, "Ort"),
        "betrag": round(betrag, 2),
        "offen": round(offen, 2),
        "gezahlt": parse_num(child_text(el, "gezahlt")),
        "gezahlt_am": _iso(parse_date(child_text(el, "GezahltAm"))),
        "zahlungsziel": _iso(ziel),
        "mahnung_am": _iso(parse_date(child_text(el, "MahnungAm"))),
        "inkasso": child_text(el, "Inkasso") == "1",
        "inkasso_datum": _iso(parse_date(child_text(el, "InkassoDatum"))),
        "tage_ueberfaellig": max(tage, 0),
        "ueberfaellig": tage > 0 and offen > 0,
    }


# ====================================================================== Service
class San6Service:
    def __init__(self, client: San6Client):
        self.c = client

    # ---------------------------------------------------------------- System
    def ping(self) -> dict:
        r = self.c.get("PING")
        m = find_first(r.root, "Mandanten")
        return _obj(m) or {"code": r.code, "text": r.text}

    def status(self) -> dict:
        r = self.c.get("STATUS")
        return {"software": _obj(find_first(r.root, "Software")),
                "eingang": [_obj(e) for e in find_all(r.root, "Datei")]}

    # ---------------------------------------------------------------- Termine
    def termine(self, von: date, bis: date, mitarbeiter: list[str] | None = None) -> list[dict]:
        params = {"Von": fmt_date(von), "Bis": fmt_date(bis)}
        if mitarbeiter:
            params["Mitarbeiter"] = ",".join(mitarbeiter)
        r = self.c.get("TERMINE", params)
        return [normalize_termin(e) for e in find_all(r.root, "Termin")]

    def termin_neu(self, data: dict) -> dict:
        """TERMINNEU1 – Termin direkt anlegen."""
        r = self.c.post("TERMINNEU1", "TerminNeu", data)
        return _result_extra(r.root, r.code, r.text)

    def termin_anfrage(self, data: dict) -> dict:
        """TERMINNEU – Terminanfrage (Online-Terminbuchung)."""
        r = self.c.post("TERMINNEU", "TerminNeu", data)
        return _result_extra(r.root, r.code, r.text)

    def termin_update(self, data: dict) -> dict:
        r = self.c.post("TERMINUPDATE", "TerminUpdate", data)
        return _result_extra(r.root, r.code, r.text)

    def terminarten(self) -> list[dict]:
        r = self.c.get("TERMINARTEN")
        return [{"nummer": child_text(e, "Nummer"), "bezeichnung": child_text(e, "Bezeichnung"),
                 "dauer_min": int(parse_num(child_text(e, "Dauer"))) or None,
                 "kurztext": child_text(e, "Kurztext"), "langtext": child_text(e, "Langtext")}
                for e in find_all(r.root, "Terminart")]

    def terminfilialen(self, terminart: str) -> list[dict]:
        r = self.c.get("TERMINFILIALEN", {"Terminart": terminart})
        return [{"nummer": child_text(e, "Nummer"), "bezeichnung": child_text(e, "Bezeichnung"),
                 "kurztext": child_text(e, "Kurztext"), "langtext": child_text(e, "Langtext")}
                for e in find_all(r.root, "Filiale")]

    def terminbelegung(self, terminart: str, filiale: str, datum: date | None = None) -> list[dict]:
        r = self.c.get("TERMINBELEGUNG", {"Terminart": terminart, "Filiale": filiale,
                                          "Datum": fmt_date(datum) if datum else None})
        tage = []
        for tag in find_all(r.root, "Tag"):
            d = parse_date(child_text(tag, "Datum"))
            frei = [c.text.strip() for c in tag if c.tag.lower() == "frei" and c.text]
            tage.append({"datum": _iso(d), "frei": frei})
        return tage

    # ---------------------------------------------------------------- Controlling
    def rechnungen(self, von: date, bis: date, zahlcode: str = "", konto: str = "",
                   mit_positionen: bool = True) -> list[dict]:
        r = self.c.get("RECHNUNGEN", {"DatumVon": fmt_date(von), "DatumBis": fmt_date(bis),
                                      "Zahlcode": zahlcode, "Konto": konto})
        return [normalize_rechnung(e, mit_positionen) for e in find_all(r.root, "Rechnung")]

    def offene_posten(self, heute: date | None = None) -> list[dict]:
        r = self.c.get("INKASSO1")
        heute = heute or date.today()
        return [normalize_op(e, heute) for e in find_all(r.root, "Rechnung")]

    def abrechnungen(self) -> list[dict]:
        r = self.c.get("ABRECHNUNG1")
        out = []
        for e in find_all(r.root, "Abrechnung1"):
            out.append({"beleg": child_text(e, "Beleg"), "ik_nummer": child_text(e, "IKNummer"),
                        "ik_kostentraeger": child_text(e, "IKKostentraeger"),
                        "rechnungsnummer": child_text(e, "Rechnungsnummer"),
                        "datum": _iso(parse_date(child_text(e, "Datum"))),
                        "status": child_text(e, "Status"), "summe": parse_num(child_text(e, "Summe")),
                        "api_status": child_text(e, "APIStatus")})
        return out

    def bestellungen(self, filiale: str = "", bestellung: str = "", abnr: str = "") -> list[dict]:
        r = self.c.get("BESTELLUNGEN1", {"Filiale": filiale, "Bestellung": bestellung, "ABNr": abnr})
        out = []
        for e in find_all(r.root, "Bestellung"):
            if len(e) == 0:  # Feld <Bestellung> (Nummer) innerhalb des Segments
                continue
            pos = []
            for a in find_all(e, "Artikel"):
                pos.append({"zeilen_id": child_text(a, "ZeilenID"), "artnr": child_text(a, "Artnr"),
                            "groesse": child_text(a, "Groesse"), "bezeichnung": child_text(a, "Bezeichnung"),
                            "bestellt": parse_num(child_text(a, "Bestellt")),
                            "offen": parse_num(child_text(a, "Offen")),
                            "einheit": child_text(a, "Einheit"),
                            "liefertermin": _iso(parse_date(child_text(a, "Liefertermin"))),
                            "we_datum": _iso(parse_date(child_text(a, "WEDatum")))})
            out.append({"bestellung": child_text(e, "Bestellung"), "typ": child_text(e, "Typ"),
                        "status": child_text(e, "Status"), "filiale": child_text(e, "Filiale"),
                        "lieferantennummer": child_text(e, "Lieferantennummer"),
                        "lieferantenname": child_text(e, "Lieferantenname"),
                        "datum": _iso(parse_date(child_text(e, "Datum"))),
                        "liefertermin": _iso(parse_date(child_text(e, "Liefertermin"))),
                        "abnr": child_text(e, "ABNr"),
                        "positionen": pos,
                        "offene_menge": sum(p["offen"] for p in pos)})
        return out

    def lieferrueckstaende(self) -> list[Any]:
        r = self.c.get("EINKAUF5")
        return [_obj(e) for e in find_all(r.root, "Einkauf5")]

    def lagerbestand(self, artikel: str = "") -> list[dict]:
        r = self.c.get("ARTIKELBESTAND", {"Artikel": artikel})
        out = []
        for e in find_all(r.root, "Lagereintrag"):
            out.append({"ident": child_text(e, "Ident"), "artikelnummer": child_text(e, "Artikelnummer"),
                        "groesse": child_text(e, "Groesse"), "bezeichnung": child_text(e, "Bezeichnung"),
                        "charge_sn": child_text(e, "ChargeSN"), "filiale": child_text(e, "Filiale"),
                        "aussenlager": child_text(e, "Aussenlager"), "lagerort": child_text(e, "Lagerort"),
                        "bestand": parse_num(child_text(e, "Bestand")),
                        "bestellt": parse_num(child_text(e, "Bestellt")),
                        "bestellt_fuer_auftrag": parse_num(child_text(e, "BestelltFuerAuftrag")),
                        "reserviert": parse_num(child_text(e, "Reserviert")),
                        "verfallsdatum": _iso(parse_date(child_text(e, "VerfallsDatum")))})
        return out

    def auftraege_geaendert(self, timestamp: str = "", schnittstelle: str = "*") -> dict:
        r = self.c.get("AUFTRAEGEGEAENDERT", {"Timestamp": timestamp, "Schnittstelle": schnittstelle},
                       ok_codes=("13",))
        ts_el = find_first(r.root, "TimestampNeu")
        auftraege = find_first(r.root, "Auftraege")
        items: list[Any] = []
        if auftraege is not None:
            kids = list(auftraege)
            items = [xml_to_obj(k) for k in kids] if kids else []
        return {"timestamp_neu": (ts_el.text or "").strip() if ts_el is not None else timestamp,
                "anzahl": len(items), "auftraege": items}

    def anzahl_orgas(self, gruppe: str) -> int:
        r = self.c.get("ANZAHLORGAS", {"SachbearbeiterGruppe": gruppe})
        anzahl = find_first(r.root, "Anzahl")
        return int(parse_num(anzahl.text if anzahl is not None else 0))

    def offene_orgas(self, gruppe: str) -> list[Any]:
        r = self.c.get("OFFENEORGAS", {"SachbearbeiterGruppe": gruppe})
        segs = find_all(r.root, "Offeneorgas")
        out: list[Any] = []
        for s in segs:
            kids = [k for k in s if len(k)]
            out.extend(xml_to_obj(k) for k in kids) if kids else out.append(xml_to_obj(s))
        return out

    def kostenrechnung(self, data: dict) -> dict:
        r = self.c.post("KOSTENRECHNUNG", "Kostenrechnung", data)
        return _result_extra(r.root, r.code, r.text)


def _result_extra(root: ET.Element, code: str, text: str) -> dict:
    """Ergebnis eines POST-Aufrufs inkl. evtl. zurückgelieferter Daten (z.B. Terminnummer)."""
    extra = {}
    for el in root:
        if el.tag.lower() != "result":
            extra[el.tag] = xml_to_obj(el)
    nummer = find_first(root, "Nummer")
    out = {"code": code, "text": text}
    if nummer is not None and nummer.text:
        out["nummer"] = nummer.text.strip()
    if extra:
        out["daten"] = extra
    return out

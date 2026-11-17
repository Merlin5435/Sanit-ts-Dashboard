"""Mock des san6-Webservices (inhouse.top) für Entwicklung und Tests – Struktur gem. TG-SAN6-02.

Start:  python mock/san6_mock.py   (Port 8084, Passwort HALLO, MD5-Modus)
"""
from __future__ import annotations

import hashlib
import os
import sys
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from xml.sax.saxutils import escape

from flask import Flask, Response, request

PASSWORD = os.environ.get("MOCK_PASSWORD", "HALLO")


def xml_resp(code: str = "00", text: str = "OK", body: str = "") -> Response:
    x = (f'<?xml version="1.0" encoding="utf-8"?><Daten><Result><Code>{code}</Code>'
         f"<Text>{escape(text)}</Text></Result>{body}</Daten>")
    return Response(x, mimetype="text/xml")


def tag(name: str, value) -> str:
    return f"<{name}>{escape(str(value))}</{name}>"


def d(x: date) -> str:
    return x.strftime("%d.%m.%Y")


class State:
    def __init__(self, today: date | None = None):
        t = today or date.today()
        self.today = t
        self.tans: dict[str, int] = {}
        self.calls: list[tuple[str, dict, bytes]] = []
        self.next_nr = 910
        self.termine: dict[str, dict] = {
            "00000901-0001": dict(nr="00000901-0001", art="80-Einlagenversorgung", thema="Einlagenversorgung",
                                  status="0", datum=t, zeit="09:00", bis=t, biszeit="09:30",
                                  ma="012-Iltgen", kunde=("10006", "Sonja Maier", "Hauptstr. 1", "86399", "Bobingen",
                                                          "08234/1234"), info="Kunde ist Diabetiker.", serie=""),
            "00000902-0001": dict(nr="00000902-0001", art="20-Außendienst", thema="Klinikbesuch", status="2",
                                  datum=t + timedelta(days=1), zeit="13:00", bis=t + timedelta(days=1),
                                  biszeit="14:30", ma="012-Iltgen", kunde=None, info="Dringend%#Bitte Muster mitnehmen",
                                  serie=""),
            "00000903-0001": dict(nr="00000903-0001", art="30-Reha", thema="Rollstuhlanpassung", status="0",
                                  datum=t + timedelta(days=2), zeit="", bis=t + timedelta(days=2), biszeit="",
                                  ma="013-Koopman", kunde=("10010", "Hans Huber", "Ringweg 5", "86150", "Augsburg", ""),
                                  info="", serie=""),
            "00000904-0001": dict(nr="00000904-0001", art="20-Außendienst", thema="Teambesprechung", status="0",
                                  datum=t + timedelta(days=3), zeit="08:00", bis=t + timedelta(days=3),
                                  biszeit="08:30", ma="013-Koopman", kunde=None, info="", serie="W1 - wöchentlich"),
        }

    # ------------------------------------------------------------- XML Bausteine
    def termin_xml(self, x: dict) -> str:
        s = "<Termin>" + tag("Nummer", x["nr"]) + tag("Terminart", x["art"]) + tag("Thema", x["thema"]) \
            + tag("Status", x["status"]) + tag("Datum", d(x["datum"])) + tag("Zeit", x["zeit"]) \
            + tag("BisDatum", d(x["bis"])) + tag("BisZeit", x["biszeit"]) + tag("Serientermin", x["serie"]) \
            + tag("Mitarbeiter", x["ma"]) + tag("Farbe", "01") + tag("SpaeterePlanung", "N") \
            + f"<Infos>{x['info']}</Infos>" + tag("Notfall", "N")
        if x["kunde"]:
            k = x["kunde"]
            s += ("<Kunde>" + tag("Kundennr", k[0]) + tag("Kundenname", k[1]) + tag("Strasse", k[2])
                  + tag("Plz", k[3]) + tag("Ort", k[4]) + tag("Telefon", k[5]) + "</Kunde>")
            s += "<Auftrag>" + tag("Thema", x["thema"]) + tag("Auftragsnummer", "000355") + tag("Stichwort", "Einlagen") + "</Auftrag>"
        return s + "</Termin>"

    def rechnungen_xml(self, von: date, bis: date, zahlcode: str) -> str:
        rows = []
        base = self.today.replace(day=1)
        data = [
            ("2900001", base, "01", "10005", "AOK Bayern", 100.00, 119.00, [("1234567", "Kompressionsstrumpf", 2, 100.00)]),
            ("2900002", base + timedelta(days=1), "02", "10006", "Sonja Maier", 51.84, 61.69, [("7654321", "Badehocker", 1, 51.84)]),
            ("2900003", base - timedelta(days=20), "01", "10005", "AOK Bayern", 400.00, 476.00, [("1234567", "Kompressionsstrumpf", 8, 400.00)]),
            ("2800004", base.replace(year=base.year - 1), "01", "10005", "AOK Bayern", 80.00, 95.20, [("1234567", "Kompressionsstrumpf", 2, 80.00)]),
        ]
        for nr, dt, zc, konto, name, netto, brutto, pos in data:
            if not (von <= dt <= bis) or (zahlcode and zc != zahlcode):
                continue
            p = "".join("<Positionen>" + tag("Menge", m) + tag("Einheit", "Stück") + tag("VKPreis", f"{g / m:.2f}")
                        + tag("Rabatt", "0.0") + tag("Gesamt", f"{g:.2f}".replace(".", ",")) + tag("MwSt", "1")
                        + tag("Artikelnummer", a) + tag("Groesse", "01") + tag("Bezeichnung", b) + "</Positionen>"
                        for a, b, m, g in pos)
            rows.append("<Rechnung>" + tag("Auftragsnummer", "000181") + tag("Rechnungsnummer", nr)
                        + tag("Rechnungsdatum", d(dt)) + tag("Zahlcode", zc) + tag("Lieferschein", "123456")
                        + tag("Versandtermin", d(dt)) + tag("Summe", f"{netto:.2f}") + tag("Summe0", "0.00")
                        + tag("Summe1", f"{netto:.2f}") + tag("Summe2", "0.00")
                        + tag("Rechnungsbetrag", f"{brutto:.2f}") + tag("Status", "3")
                        + "<Rechnungsadresse>" + tag("Konto", konto) + tag("Name", name) + tag("Ort", "Bobingen")
                        + "</Rechnungsadresse>" + p + "</Rechnung>")
        return "<Rechnungen>" + "".join(rows) + "</Rechnungen>"

    def inkasso_xml(self) -> str:
        t = self.today
        rows = [
            ("270234", "250001", "Standard", t - timedelta(days=45), t - timedelta(days=15), "238,--", "100,-", "10010", "Hans Huber", "1"),
            ("270235", "250002", "Eigenanteil", t - timedelta(days=5), t + timedelta(days=9), "45,50", "45,50", "10006", "Sonja Maier", ""),
            ("270236", "250003", "Standard", t - timedelta(days=150), t - timedelta(days=120), "1.250,00", "1.250,00", "10011", "Klinik Süd", ""),
        ]
        out = []
        for a, r, typ, rd, ziel, betrag, offen, kd, name, mahn in rows:
            out.append("<Rechnung>" + tag("Auftragsnummer", a) + tag("Rechnungstyp", typ) + tag("Vorgangsdatum", d(rd))
                       + tag("Rechnungsnummer", r) + tag("Rechnungsdatum", d(rd)) + tag("Status", "3")
                       + tag("Auftragstyp", "S") + tag("Kundennr", kd) + tag("Adresstyp", "P") + tag("Name", name)
                       + tag("Betrag", betrag) + tag("Offen", offen) + tag("Zahlungsziel", d(ziel))
                       + (tag("MahnungAm", d(ziel + timedelta(days=7))) if mahn else "") + "</Rechnung>")
        return "<Inkasso1>" + "".join(out) + "</Inkasso1>"


app = Flask(__name__)
STATE = State()


def reset(today: date | None = None) -> State:
    global STATE
    STATE = State(today)
    return STATE


def check_auth() -> Response | None:
    qs = request.query_string.decode()
    if "&authentifizierung=" not in qs:
        return xml_resp("02", "Ungültige Authentifizierung")
    base, auth = qs.rsplit("&authentifizierung=", 1)
    if auth == PASSWORD:
        pass
    else:
        if not base.startswith("funktion=API-"):
            return xml_resp("02", "Ungültige Authentifizierung")
        expect = hashlib.md5((base[len("funktion=API-"):] + PASSWORD).encode()).hexdigest()
        if auth != expect:
            return xml_resp("02", "Ungültige Authentifizierung")
    tan = request.args.get("tan")
    if tan:
        dev, n = tan.split("-")
        if int(n) <= STATE.tans.get(dev, 0):
            return xml_resp("02", "TAN bereits verwendet")
        STATE.tans[dev] = int(n)
    return None


@app.route("/inhouse.top", methods=["GET", "POST"])
def inhouse():
    err = check_auth()
    if err:
        return err
    f = request.args.get("funktion", "").upper().replace("API-", "")
    a = request.args
    STATE.calls.append((f, dict(a), request.get_data()))
    t = STATE.today

    if f == "PING":
        return xml_resp(body="<Mandanten>" + tag("Produkt", "SAN6") + tag("Version", "6.55.22")
                        + tag("Mandant", a.get("mandant", "")) + tag("IPWhitelist", "") + "</Mandanten>")
    if f == "STATUS":
        return xml_resp(body="<Software>" + tag("Produkt", "SAN6") + tag("Version", "6.55.22") + "</Software>")
    if f == "TERMINE":
        if not a.get("Von"):
            return xml_resp("11", "Der Parameter VON fehlt im Funktionsaufruf")
        von = date(*reversed([int(x) for x in a["Von"].split(".")]))
        bis = date(*reversed([int(x) for x in a["Bis"].split(".")]))
        mas = [m for m in a.get("Mitarbeiter", "").split(",") if m]
        rows = [x for x in STATE.termine.values() if von <= x["datum"] <= bis
                and (not mas or x["ma"][:3] in mas)]
        return xml_resp(body="<Termine>" + "".join(STATE.termin_xml(x) for x in rows) + "</Termine>")
    if f in ("TERMINNEU1", "TERMINNEU"):
        root = ET.fromstring(request.get_data())
        g = lambda n: (root.findtext(n) or "").strip()  # noqa: E731
        if not g("Terminart") or not g("Datum") or not g("Mitarbeiter"):
            return xml_resp("30", "Unvollständige Terminangaben")
        nr = f"{STATE.next_nr:08d}-0001"
        STATE.next_nr += 1
        dd = date(*reversed([int(x) for x in g("Datum").split(".")]))
        bd = date(*reversed([int(x) for x in (g("BisDatum") or g("Datum")).split(".")]))
        STATE.termine[nr] = dict(nr=nr, art=g("Terminart"), thema=g("Thema") or "Terminanfrage",
                                 status="2" if f == "TERMINNEU" else (g("Status")[:1] or "0"), datum=dd,
                                 zeit=g("Uhrzeit"), bis=bd, biszeit=g("BisUhrzeit") or "", ma=g("Mitarbeiter"),
                                 kunde=None, info=g("Info") or g("Hinweise"), serie="")
        return xml_resp(body=tag("Nummer", nr))
    if f == "TERMINUPDATE":
        root = ET.fromstring(request.get_data())
        g = lambda n: (root.findtext(n) or "").strip()  # noqa: E731
        x = STATE.termine.get(g("Nummer"))
        if not x:
            return xml_resp("30", "Terminnummer fehlt oder hat ungültiges Format")
        if not all(g(k) for k in ("Datum", "Uhrzeit", "BisDatum", "BisUhrzeit", "Mitarbeiter")):
            return xml_resp("31", "Unvollständiger Datensatz")
        if g("Status") and g("Status") not in "0123789":
            return xml_resp("32", "Ungültiger Status")
        x["datum"] = date(*reversed([int(v) for v in g("Datum").split(".")]))
        x["bis"] = date(*reversed([int(v) for v in g("BisDatum").split(".")]))
        x["zeit"], x["biszeit"] = g("Uhrzeit"), g("BisUhrzeit")
        if g("Status"):
            x["status"] = g("Status")
        if root.find("Infos") is not None:
            x["info"] = g("Infos")
        return xml_resp()
    if f == "TERMINARTEN":
        return xml_resp(body="<Terminarten><Terminart>" + tag("Nummer", "80") + tag("Bezeichnung", "Einlagenversorgung")
                        + tag("Dauer", "30") + tag("Kurztext", "Fußvermessung") + tag("Langtext", "…")
                        + "</Terminart></Terminarten>")
    if f == "TERMINFILIALEN":
        if not a.get("Terminart"):
            return xml_resp("11", "Terminart fehlt")
        return xml_resp(body="<Filialen><Filiale>" + tag("Nummer", "01") + tag("Bezeichnung", "Zentrale")
                        + tag("Kurztext", "") + tag("Langtext", "") + "</Filiale></Filialen>")
    if f == "TERMINBELEGUNG":
        return xml_resp(body="<Belegung><Tag>" + tag("Datum", d(t)) + tag("frei", "09:00") + tag("frei", "09:30")
                        + "</Tag></Belegung>")
    if f == "RECHNUNGEN":
        if not a.get("DatumVon"):
            return xml_resp("11", "DatumVon fehlt im Funktionsaufruf")
        von = date(*reversed([int(x) for x in a["DatumVon"].split(".")]))
        bis = date(*reversed([int(x) for x in a["DatumBis"].split(".")]))
        return xml_resp(body=STATE.rechnungen_xml(von, bis, a.get("Zahlcode", "")))
    if f == "INKASSO1":
        return xml_resp(body=STATE.inkasso_xml())
    if f == "ABRECHNUNG1":
        return xml_resp(body="<Abrechnung1>" + tag("Beleg", "000004-471108") + tag("IKNummer", "108001963")
                        + tag("IKKostentraeger", "107202793") + tag("Rechnungsnummer", "970061")
                        + tag("Datum", d(t)) + tag("Status", "0") + tag("Summe", "90.10") + tag("APIStatus", "1")
                        + "</Abrechnung1>")
    if f == "BESTELLUNGEN1":
        return xml_resp(body="<Bestellungen1><Bestellung>" + tag("Status", "1-Bestellt") + tag("Typ", "B")
                        + tag("Filiale", "01") + tag("Bestellung", "000034") + tag("Lieferantennummer", "70001")
                        + tag("Lieferantenname", "Medi GmbH") + tag("Datum", d(t - timedelta(days=20)))
                        + tag("Liefertermin", d(t - timedelta(days=2)))
                        + "<Positionen><Artikel>" + tag("ZeilenID", "0000000001") + tag("Artnr", "1234567")
                        + tag("Groesse", "02") + tag("Bezeichnung", "Bandage, XXL") + tag("Bestellt", "10")
                        + tag("Offen", "2") + tag("Einheit", "Stück") + "</Artikel></Positionen>"
                        + "</Bestellung></Bestellungen1>")
    if f == "EINKAUF5":
        return xml_resp(body="<Einkauf5><Lieferant>70001</Lieferant></Einkauf5>")
    if f == "ARTIKELBESTAND":
        rows = [("0000010117", "1234567", "01", "Kompressionsstrumpf", "01", 12, 20, 5, d(t + timedelta(days=30))),
                ("0000010118", "7654321", "01", "Badehocker", "02", -1, 0, 0, "")]
        return xml_resp(body="<Artikelbestand>" + "".join(
            "<Lagereintrag>" + tag("Ident", i) + tag("Artikelnummer", n) + tag("Groesse", g) + tag("Bezeichnung", b)
            + tag("Filiale", fi) + tag("Bestand", be) + tag("Bestellt", bs) + tag("Reserviert", r)
            + tag("VerfallsDatum", v) + "</Lagereintrag>"
            for i, n, g, b, fi, be, bs, r, v in rows) + "</Artikelbestand>")
    if f == "AUFTRAEGEGEAENDERT":
        if a.get("Timestamp") == "20990101000000000":
            return xml_resp("13", "Keine geänderten Aufträge gefunden")
        return xml_resp(body=tag("TimestampNeu", "20261104120000000") + "<Auftraege><Auftrag>"
                        + tag("Auftragsnummer", "000355") + tag("Status", "3") + "</Auftrag></Auftraege>")
    if f == "ANZAHLORGAS":
        if not a.get("SachbearbeiterGruppe"):
            return xml_resp("11", "Sachbearbeiter oder Gruppe fehlt im Funktionsaufruf")
        return xml_resp(body="<Offeneorgas>" + tag("Anzahl", "7") + "</Offeneorgas>")
    if f == "KOSTENRECHNUNG":
        root = ET.fromstring(request.get_data())
        if root.find("Anlagen/Anlage") is not None and not root.findtext("Anlagen/Anlage/Dateiname"):
            return xml_resp("14", "Dateiname fehlt")
        return xml_resp()
    return xml_resp("03", "Ungültiger Funktionsaufruf")


if __name__ == "__main__":
    app.run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8084)

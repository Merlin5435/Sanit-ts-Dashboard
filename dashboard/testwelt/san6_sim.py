"""Simuliertes san6 – spricht dasselbe Protokoll wie der echte TopM-Webservice (TG-SAN6-02).

Aufrufe kommen als HTTP-GET auf /inhouse.top?funktion=API-…, Antworten als XML (ISO-8859-1).
Die Daten stammen aus der Testwelt (welt.py).
"""
from __future__ import annotations

import hashlib
from datetime import date
from xml.sax.saxutils import escape

from flask import Flask, Response, request

from .welt import MITARBEITER, Welt, d


def _t(name: str, value) -> str:
    return f"<{name}>{escape('' if value is None else str(value))}</{name}>"


def _antwort(body: str = "", code: str = "00", text: str = "OK") -> Response:
    x = (f'<?xml version="1.0" encoding="ISO-8859-1"?><Daten><Result>{_t("Code", code)}{_t("Text", text)}</Result>'
         f"{body}</Daten>")
    return Response(x.encode("iso-8859-1", "xmlcharrefreplace"), mimetype="text/xml; charset=ISO-8859-1")


def _datum(s: str) -> date:
    t, m, j = (int(x) for x in s.split("."))
    return date(j, m, t)


def _eur(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def create_app(welt: Welt, passwort: str = "TEST") -> Flask:
    app = Flask("san6_sim")

    def auth_ok() -> bool:
        qs = request.query_string.decode()
        if "&authentifizierung=" not in qs:
            return False
        basis, code = qs.rsplit("&authentifizierung=", 1)
        if code == passwort:
            return True
        for cut in ("funktion=API-", "funktion="):
            if basis.startswith(cut) and hashlib.md5((basis[len(cut):] + passwort).encode()).hexdigest() == code:
                return True
        return False

    @app.route("/inhouse.top", methods=["GET", "POST"])
    def inhouse():
        if not auth_ok():
            return _antwort(code="02", text="Ungültige Authentifizierung")
        f = request.args.get("funktion", "").upper().removeprefix("API-")
        a = request.args
        w = welt
        with w.lock:
            if f == "PING":
                return _antwort("<Mandanten>" + _t("Produkt", "SAN6") + _t("Version", "6.55.26 (Testwelt)")
                                + _t("Mandant", a.get("mandant", "")) + _t("IPWhitelist", "") + "</Mandanten>")
            if f == "SETUP":
                return _antwort("<Sachbearbeiterliste>" + "".join(
                    "<Sachbearbeiter>" + _t("Nummer", n) + _t("Name", name) + _t("Aktiv", "1") + "</Sachbearbeiter>"
                    for n, name in MITARBEITER) + "</Sachbearbeiterliste>")
            if f == "TERMINE":
                if not a.get("Von") or not a.get("Bis"):
                    return _antwort(code="11", text="Der Parameter VON fehlt im Funktionsaufruf")
                von, bis = _datum(a["Von"]), _datum(a["Bis"])
                mas = [m for m in a.get("Mitarbeiter", "").split(",") if m]
                rows = [x for x in w.termine if von <= x["datum"] <= bis and (not mas or x["ma"] in mas)]
                out = []
                for x in rows:
                    ma = dict(MITARBEITER).get(x["ma"], "")
                    s = ("<Termin>" + _t("Nummer", x["nr"]) + _t("Terminart", "Versorgung") + _t("Thema", x["thema"])
                         + _t("Status", x["status"]) + _t("Datum", d(x["datum"])) + _t("Zeit", x["zeit"])
                         + _t("BisDatum", d(x["datum"])) + _t("BisZeit", x["bis"]) + _t("Serientermin", "")
                         + _t("Mitarbeiter", f"{x['ma']}-{ma}") + _t("Infos", x.get("extern", "")) + _t("Notfall", "N"))
                    if x["kunde"]:
                        k = x["kunde"]
                        s += "<Kunde>" + _t("Kundennr", k[0]) + _t("Kundenname", k[1]) + _t("Telefon", k[4]) + "</Kunde>"
                    s += "<Auftrag>" + _t("Filiale", f"{x['filiale'][0]}-{x['filiale'][1]}") + "</Auftrag>"
                    out.append(s + "</Termin>")
                # Annahme: Urlaub/Krank stehen im TopM-Timer als Termine mit eigener Terminart
                for x in w.abwesend:
                    if x["von"] <= bis and x["bis"] >= von and (not mas or x["ma"][0] in mas):
                        out.append("<Termin>" + _t("Nummer", f"A{x['ma'][0]}-{d(x['von'])}") + _t("Terminart", x["art"])
                                   + _t("Thema", x["art"]) + _t("Status", "0") + _t("Datum", d(x["von"])) + _t("Zeit", "")
                                   + _t("BisDatum", d(x["bis"])) + _t("BisZeit", "")
                                   + _t("Mitarbeiter", f"{x['ma'][0]}-{x['ma'][1]}") + "</Termin>")
                return _antwort("<Termine>" + "".join(out) + "</Termine>")
            if f == "AUFTRAEGE1":
                st = a.get("Status", "012")
                rows = [x for x in w.auftraege if x["status"] in st]
                return _antwort("<Auftraege1>" + "".join(
                    "<Auftrag>" + _t("Modul", "A") + _t("Auftragstyp", "R") + _t("Filiale", x["filiale"][0])
                    + _t("Auftragsnummer", x["nr"]) + _t("Auftragsnummer1", x["nr"] + "-A1B2C3")
                    + _t("Kundennummer", x["kunde"][0]) + _t("Kundenname", x["kunde"][1]) + _t("Status", x["status"])
                    + _t("Datum", d(x["datum"]))
                    # Annahme Testwelt: diese Felder liefert das echte san6 ggf. erst über AUFTRAGSDATEN
                    + _t("Krankenkasse", x["kasse"]) + _t("Betrag", f"{x['betrag']:.2f}")
                    + _t("Rezepteingang", d(x["rezepteingang"]) if x["rezepteingang"] else "")
                    + _t("Diagnose", x["diagnose"])
                    + "<Positionen><Artikel>" + _t("Bezeichnung", x["produkt"]) + _t("HMVNr", x["hmv"]) + _t("Offen", "1")
                    + "</Artikel></Positionen></Auftrag>" for x in rows) + "</Auftraege1>")
            if f == "ABRECHNUNG2":
                ab = next((x for x in w.abrechnungen if x["beleg"] == a.get("Beleg")), None)
                if not ab:
                    return _antwort(code="13", text="Abrechnung nicht wiedergefunden")
                nach_nr = {x["nr"]: x for x in w.auftraege}
                belege = "".join("<Beleg><BA>" + _t("Lfdnr", i + 1) + _t("Typ", "302") + _t("Beleg", f"{nr}-A1B2C3")
                                 + _t("BelegOhnePruefziffer", nr) + _t("Referenz1", nr) + "</BA>"
                                 + "<VS>" + _t("Nachname", nach_nr[nr]["kunde"][1].split()[-1]) + "</VS></Beleg>"
                                 for i, nr in enumerate(ab["verordnungen"]) if nr in nach_nr)
                return _antwort("<Abrechnung2>" + _t("Rechnungsnummer", ab["beleg"][:6]) + _t("APIStatus", "1")
                                + "<Belege>" + belege + "</Belege></Abrechnung2>")
            if f == "ABRECHNUNG3":
                nr = a.get("Auftrag", "")[:6]
                x = next((x for x in w.auftraege if x["nr"] == nr), None)
                if not x:
                    return _antwort(code="41", text="Auftragsnummer fehlt")
                doks = "" if not x["rezept_scan"] else ("<Dokument>" + _t("Archivnummer", f"{int(nr):08d}") + _t("Datum", d(x["rezepteingang"] or x["datum"]))
                                                       + _t("Typ", "R") + _t("Bezeichnung", "Rezept") + _t("Dateiname", "Rezept.pdf") + "</Dokument>")
                return _antwort("<Abrechnung3><Dokumente>" + doks + "</Dokumente></Abrechnung3>")
            if f == "ABRECHNUNG1":
                return _antwort("".join(
                    "<Abrechnung1>" + _t("Beleg", x["beleg"]) + _t("IKNummer", x["ik"]) + _t("IKKostentraeger", x["kasse"])
                    + _t("Rechnungsnummer", x["beleg"][:6]) + _t("Datum", d(x["datum"])) + _t("Status", x["status"])
                    + _t("Summe", f"{x['summe']:.2f}") + _t("APIStatus", "1") + "</Abrechnung1>" for x in w.abrechnungen))
            if f == "RECHNUNGEN":
                if not a.get("DatumVon"):
                    return _antwort(code="11", text="DatumVon fehlt im Funktionsaufruf")
                if not a.get("DatumBis"):
                    return _antwort(code="12", text="DatumBis fehlt im Funktionsaufruf")
                von, bis = _datum(a["DatumVon"]), _datum(a["DatumBis"])
                rows = [x for x in w.rechnungen if von <= x["datum"] <= bis]
                return _antwort("<Rechnungen>" + "".join(
                    "<Rechnung>" + _t("Auftragsnummer", "000000") + _t("Rechnungsnummer", x["rnr"])
                    + _t("Rechnungsdatum", d(x["datum"])) + _t("Zahlcode", x["zahlcode"]) + _t("Summe", f"{x['netto']:.2f}")
                    + _t("Rechnungsbetrag", f"{x['brutto']:.2f}") + _t("Status", "3")
                    + "<Rechnungsadresse>" + _t("Konto", "10000") + _t("Name", x["konto"]) + "</Rechnungsadresse></Rechnung>"
                    for x in rows) + "</Rechnungen>")
            if f == "INKASSO1":
                return _antwort("<Inkasso1>" + "".join(
                    "<Rechnung>" + _t("Auftragsnummer", x["auftrag"]) + _t("Rechnungstyp", x["typ"])
                    + _t("Vorgangsdatum", d(x["datum"])) + _t("Rechnungsnummer", x["rnr"])
                    + _t("Rechnungsdatum", d(x["datum"])) + _t("Status", "3") + _t("Auftragstyp", "S")
                    + _t("Kundennr", x["kunde"][0]) + _t("Adresstyp", "P") + _t("Name", x["kunde"][1])
                    + _t("Betrag", _eur(x["betrag"])) + _t("Offen", _eur(x["offen"])) + _t("Zahlungsziel", d(x["ziel"]))
                    + (_t("MahnungAm", d(x["mahnung"])) if x["mahnung"] else "") + _t("Telefon", x["kunde"][4])
                    + "</Rechnung>" for x in w.op) + "</Inkasso1>")
            if f == "REZEPTANFORDERUNGEN":
                arzt = a.get("Arzt", "")
                rows = [x for x in w.rezepte if not arzt or x["arzt"][0] == arzt]
                return _antwort("<Rezeptanforderungen>" + "".join(
                    "<Anforderung>" + _t("Auftragsnummer", f"{x['auftrag']}-{x['pruef']}") + _t("Auftragstyp", "R")
                    + _t("Anforderungsdatum", d(x["anfrage"])) + _t("Stichwort", x["stichwort"])
                    + _t("Kunde", x["kunde"][1]) + _t("KundenNr", x["kunde"][0]) + _t("VersichertenNr", x["kunde"][2])
                    + _t("Krankenkasse", x["kasse"]) + _t("Arzt", x["arzt"][1]) + _t("ArztNr", x["arzt"][0])
                    + "</Anforderung>" for x in rows) + "</Rezeptanforderungen>")
            if f == "WIEDERVORLAGEN":
                return _antwort("<Wiedervorlagen>" + "".join(
                    "<Wiedervorlage>" + _t("Nummer", x["nr"]) + _t("Vorlagedatum", d(x["datum"])) + _t("Typ", x["typ"])
                    + _t("Vorgang", x["vorgang"]) + _t("Bemerkung", x["bemerkung"]) + "</Wiedervorlage>"
                    for x in w.wiedervorlagen) + "</Wiedervorlagen>")
            if f in ("OFFENEORGAS", "ANZAHLORGAS"):
                g = a.get("SachbearbeiterGruppe", "")
                if not g:
                    return _antwort(code="11", text="Sachbearbeiter oder Gruppe fehlt im Funktionsaufruf")
                rows = [x for x in w.orgas if x["an"] == g]
                if f == "ANZAHLORGAS":
                    return _antwort("<Offeneorgas>" + _t("Anzahl", len(rows)) + "</Offeneorgas>")
                return _antwort("<Offeneorgas>" + "".join(
                    "<Orga>" + _t("Nummer", x["nr"]) + _t("AngelegtDatum", d(x["datum"])) + _t("AngelegtZeit", "18:12:00")
                    + _t("Von", x["von"]) + _t("An", x["an"]) + _t("Thema", x["thema"]) + _t("Text", x["text"])
                    + _t("Status", "1") + _t("Kundennr", x["kunde"][0] if x["kunde"] else "")
                    + _t("Kunde", x["kunde"][1] if x["kunde"] else "") + _t("Ktyp", "P" if x["kunde"] else "")
                    + "</Orga>" for x in rows) + "</Offeneorgas>")
            if f == "EINKAUF5":
                lief: dict[str, list] = {}
                for x in w.rueckstaende:
                    lief.setdefault(x["lieferant"][0], []).append(x)
                out = []
                for nr, rows in lief.items():
                    name = rows[0]["lieferant"][1]
                    s = "<Einkauf5>" + _t("Adressnummer", nr) + _t("Name", name)
                    for x in rows:
                        s += ("<Bestellung>" + _t("Bestellnummer", x["bestellung"]) + _t("Datum", d(x["datum"]))
                              + _t("Status", "1") + _t("Liefertermin", d(x["liefertermin"]))
                              + "<Positionen><Position>" + _t("Bestellmenge", x["bestellt"]) + _t("Offen", x["offen"])
                              + _t("Bezeichnung", x["artikel"]) + "</Position></Positionen></Bestellung>")
                    out.append(s + "</Einkauf5>")
                return _antwort("".join(out))
        return _antwort(code="03", text="Ungültiger Funktionsaufruf")

    return app

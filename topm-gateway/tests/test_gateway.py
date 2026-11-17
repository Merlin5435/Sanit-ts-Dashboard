import hashlib
from datetime import date, timedelta

import graph_mock
import san6_mock

from topm_gateway.san6_client import San6Client, San6Error
from topm_gateway.config import San6Config
from topm_gateway.xmlutil import parse_num


# ------------------------------------------------------------------ san6-Client
def test_md5_url_wie_dokumentation():
    c = San6Client(San6Config(base_url="http://home.topm.de:8081/inhouse.top", company="san6demo",
                              product="SAN6-DEMO", mandant="demo", sys="01", password="HALLO", auth_mode="md5"))
    url = c.build_url("PFLEGEPLAN", {"mitarbeiter": "KLATT"})
    q = url.split("?", 1)[1]
    base, auth = q.rsplit("&authentifizierung=", 1)
    assert base.startswith("funktion=API-PFLEGEPLAN&company=san6demo")
    assert auth == hashlib.md5((base[len("funktion=API-"):] + "HALLO").encode()).hexdigest()


def test_basismodus_und_tan(gw):
    gw.client.cfg.auth_mode = "basic"
    assert gw.svc.ping()["Produkt"] == "SAN6"
    assert gw.svc.ping()["Produkt"] == "SAN6"           # zweite TAN muss höher sein
    assert san6_mock.STATE.tans["0001"] == 2


def test_falsches_passwort(gw):
    gw.client.cfg.password = "FALSCH"
    try:
        gw.svc.ping()
        assert False
    except San6Error as e:
        assert e.code == "02"


def test_zahlen():
    assert parse_num("238,--") == 238.0
    assert parse_num("100,-") == 100.0
    assert parse_num("1.250,00") == 1250.0
    assert parse_num("51.84") == 51.84
    assert parse_num("53,77") == 53.77
    assert parse_num("") == 0.0


# ------------------------------------------------------------------ REST: Auth
def test_auth(client):
    assert client.get("/health").status_code == 200
    r = client.get("/api/v1/terminarten", headers={"X-API-Key": "nope"})
    assert r.status_code == 401
    r = client.get("/api/v1/terminarten", headers={"X-API-Key": "read-key"})
    assert r.status_code == 200
    r = client.post("/api/v1/sync/termine", headers={"X-API-Key": "read-key"})
    assert r.status_code == 403


# ------------------------------------------------------------------ REST: Termine
def test_termine_live(client):
    heute = date.today()
    r = client.get(f"/api/v1/termine?von={heute}&bis={heute + timedelta(days=5)}")
    j = r.get_json()
    assert r.status_code == 200 and j["anzahl"] == 4
    t = next(x for x in j["daten"] if x["nummer"] == "00000901-0001")
    assert t["start"] == f"{heute}T09:00" and t["ende"] == f"{heute}T09:30"
    assert t["kunde"]["Kundenname"] == "Sonja Maier" and t["mitarbeiter_nr"] == "012"
    t2 = next(x for x in j["daten"] if x["nummer"] == "00000902-0001")
    assert t2["infos"] == "Dringend\nBitte Muster mitnehmen" and t2["status_text"] == "angefragt"
    t3 = next(x for x in j["daten"] if x["nummer"] == "00000903-0001")
    assert t3["ganztaegig"] is True
    r = client.get(f"/api/v1/termine?von={heute}&bis={heute + timedelta(days=5)}&mitarbeiter=013&format=csv")
    assert r.mimetype == "text/csv" and "Rollstuhlanpassung" in r.get_data(as_text=True)


def test_termin_anlegen_aendern_absagen(client):
    heute = date.today()
    r = client.post("/api/v1/termine", json={"terminart": "80", "mitarbeiter": "012-Iltgen",
                                             "start": f"{heute}T15:00", "ende": f"{heute}T16:00",
                                             "thema": "Neuer Termin", "kundennummer": "10006"})
    assert r.status_code == 201, r.get_json()
    nr = r.get_json()["nummer"]
    client.post("/api/v1/sync/termine")
    r = client.patch(f"/api/v1/termine/{nr}", json={"start": f"{heute}T17:00", "ende": f"{heute}T17:45"})
    assert r.status_code == 200
    assert san6_mock.STATE.termine[nr]["zeit"] == "17:00"
    assert client.get(f"/api/v1/termine/{nr}").get_json()["start"] == f"{heute}T17:00"
    r = client.delete(f"/api/v1/termine/{nr}")
    assert r.status_code == 200 and san6_mock.STATE.termine[nr]["status"] == "7"
    # Pflichtfeld fehlt
    assert client.post("/api/v1/termine", json={"terminart": "80"}).status_code == 400
    # Serientermin -> 409
    assert client.patch("/api/v1/termine/00000904-0001", json={"start": f"{heute}T10:00"}).status_code == 409


def test_terminportal(client):
    assert client.get("/api/v1/terminarten").get_json()["daten"][0]["dauer_min"] == 30
    assert client.get("/api/v1/terminarten/80/filialen").get_json()["daten"][0]["bezeichnung"] == "Zentrale"
    j = client.get("/api/v1/terminbelegung?terminart=80&filiale=01").get_json()
    assert j["tage"][0]["frei"] == ["09:00", "09:30"]
    r = client.post("/api/v1/termine/anfragen", json={"terminart": "80", "filiale": "01", "mitarbeiter": "012",
                                                      "start": f"{date.today()}T09:00", "vorname": "Uwe",
                                                      "nachname": "Test", "email": "a@b.de"})
    assert r.status_code == 201


def test_ics_feed(client):
    client.post("/api/v1/sync/termine")
    r = client.get("/api/v1/kalender/012.ics?token=ics-token", headers={"X-API-Key": ""})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "BEGIN:VCALENDAR" in body
    assert "UID:san6-00000901-0001@topm-gateway" in body and "00000903" not in body
    assert client.get("/api/v1/kalender/012.ics?token=falsch", headers={"X-API-Key": ""}).status_code == 401


# ------------------------------------------------------------------ Sync / Outlook
def test_sync_outlook_beidseitig(client, gw):
    heute = date.today()
    r = client.post("/api/v1/sync/termine").get_json()
    assert r["ok"], r
    assert r["ergebnis"]["san6"]["neu"] == 4
    assert r["ergebnis"]["outlook"]["outlook_angelegt"] == 4
    il = graph_mock.EVENTS["iltgen@firma.de"]
    assert len(il) == 2
    ev_id = gw.store.map_get("00000901-0001", "iltgen@firma.de")["event_id"]
    assert il[ev_id]["subject"].startswith("Einlagenversorgung – Sonja Maier")

    # zweiter Lauf: keine Änderungen
    r = client.post("/api/v1/sync/termine").get_json()["ergebnis"]
    assert r["san6"]["neu"] == 0 and r["outlook"]["outlook_geaendert"] == 0

    # Änderung in san6 -> Outlook aktualisiert
    san6_mock.STATE.termine["00000901-0001"]["zeit"] = "10:00"
    san6_mock.STATE.termine["00000901-0001"]["biszeit"] = "10:45"
    r = client.post("/api/v1/sync/termine").get_json()["ergebnis"]
    assert r["san6"]["geaendert"] == 1 and r["outlook"]["outlook_geaendert"] == 1
    assert il[ev_id]["start"]["dateTime"].startswith(f"{heute}T10:00")

    # Verschiebung in Outlook -> TERMINUPDATE in san6
    il[ev_id]["start"]["dateTime"] = f"{heute}T14:00:00.0000000"
    il[ev_id]["end"]["dateTime"] = f"{heute}T14:30:00.0000000"
    r = client.post("/api/v1/sync/termine").get_json()["ergebnis"]
    assert r["outlook"]["san6_verschoben"] == 1
    assert san6_mock.STATE.termine["00000901-0001"]["zeit"] == "14:00"
    r = client.post("/api/v1/sync/termine").get_json()["ergebnis"]  # stabil, kein Ping-Pong
    assert r["outlook"]["outlook_geaendert"] == 0 and r["outlook"]["san6_verschoben"] == 0

    # Löschung in Outlook -> Status 7 in san6
    del il[ev_id]
    r = client.post("/api/v1/sync/termine").get_json()["ergebnis"]
    assert r["outlook"]["san6_abgesagt"] == 1
    assert san6_mock.STATE.termine["00000901-0001"]["status"] == "7"

    # Serientermin in Outlook verschoben -> san6 gewinnt (zurückgesetzt)
    ko = graph_mock.EVENTS["koopman@firma.de"]
    sid = gw.store.map_get("00000904-0001", "koopman@firma.de")["event_id"]
    ko[sid]["start"]["dateTime"] = ko[sid]["start"]["dateTime"].replace("08:00", "11:00")
    ko[sid]["end"]["dateTime"] = ko[sid]["end"]["dateTime"].replace("08:30", "11:30")
    client.post("/api/v1/sync/termine")
    assert "T08:00" in ko[sid]["start"]["dateTime"]
    assert san6_mock.STATE.termine["00000904-0001"]["zeit"] == "08:00"

    # Termin verschwindet in san6 -> Outlook-Event wird entfernt
    del san6_mock.STATE.termine["00000903-0001"]
    r = client.post("/api/v1/sync/termine").get_json()["ergebnis"]
    assert r["san6"]["geloescht"] == 1 and r["outlook"]["outlook_entfernt"] == 1

    feed = client.get("/api/v1/sync/aenderungen?seit=0&entity=termin").get_json()
    ops = {(a["key"], a["op"], a["source"]) for a in feed["aenderungen"]}
    assert ("00000901-0001", "updated", "outlook") in ops and ("00000903-0001", "deleted", "san6") in ops


def test_outlook_import_neuer_termin(servers, tmp_path):
    from conftest import make_settings
    from topm_gateway.core import Gateway
    san6_mock.reset()
    graph_mock.reset()
    gw = Gateway(make_settings(servers, tmp_path, outlook_new_category="san6 neu"))
    heute = date.today()
    graph_mock.EVENTS.setdefault("iltgen@firma.de", {})["ev-x"] = {
        "id": "ev-x", "subject": "Hausbesuch Müller", "categories": ["san6 neu"],
        "start": {"dateTime": f"{heute}T11:00:00.0000000"}, "end": {"dateTime": f"{heute}T12:00:00.0000000"},
        "body": {"contentType": "text", "content": "Rollator bringen"}}
    r = gw.sync_termine()["ergebnis"]
    assert r["outlook"]["san6_neu"] == 1
    neu = [t for t in san6_mock.STATE.termine.values() if t["thema"] == "Hausbesuch Müller"]
    assert neu and neu[0]["zeit"] == "11:00"
    r = gw.sync_termine()["ergebnis"]  # neuer san6-Termin wird mit dem Outlook-Event verknüpft, nicht dupliziert
    assert r["outlook"]["san6_neu"] == 0
    assert len([e for e in graph_mock.EVENTS["iltgen@firma.de"].values() if "Hausbesuch" in e["subject"]]) == 1


# ------------------------------------------------------------------ Controlling
def test_controlling(client):
    heute = date.today()
    von = heute.replace(day=1)
    j = client.get(f"/api/v1/controlling/rechnungen?von={von}&bis={von + timedelta(days=5)}&positionen=1").get_json()
    assert j["summen"]["netto"] == 151.84 and j["summen"]["brutto"] == 180.69
    assert j["daten"][0]["positionen"][0]["gesamt"] == 100.0
    j = client.get(f"/api/v1/controlling/umsatz?von={von - timedelta(days=40)}&bis={von + timedelta(days=5)}"
                   f"&gruppierung=zahlcode").get_json()
    assert {r["schluessel"]: r["netto"] for r in j["daten"]} == {"01": 500.0, "02": 51.84}
    j = client.get(f"/api/v1/controlling/umsatz?von={von - timedelta(days=40)}&bis={von + timedelta(days=5)}"
                   f"&gruppierung=artikel").get_json()
    assert j["daten"][0]["schluessel"] == "1234567-01" and j["daten"][0]["menge"] == 10

    j = client.get("/api/v1/controlling/offene-posten").get_json()
    z = j["zusammenfassung"]
    assert z["anzahl"] == 3 and z["summe_offen"] == 1395.5 and z["summe_ueberfaellig"] == 1350.0
    assert z["altersstruktur"]["1_30"]["summe"] == 100.0 and z["altersstruktur"]["ueber_90"]["summe"] == 1250.0
    assert j["daten"][0]["rechnungsnummer"] == "250003"

    j = client.get("/api/v1/controlling/bestellungen").get_json()
    assert j["zusammenfassung"]["offene_menge"] == 2 and j["zusammenfassung"]["ueberfaellig"] == 1
    j = client.get("/api/v1/controlling/lagerbestand").get_json()
    assert j["zusammenfassung"]["negativbestaende"] == 1 and j["zusammenfassung"]["verfall_90_tage"] == 1
    assert client.get("/api/v1/controlling/abrechnungen").get_json()["summe"] == 90.1
    assert client.get("/api/v1/controlling/orgas?gruppe=012").get_json()["anzahl"] == 7
    assert client.get("/api/v1/controlling/orgas").status_code == 400

    j = client.get("/api/v1/controlling/auftraege/aenderungen?fortsetzen=1").get_json()
    assert j["anzahl"] == 1 and j["timestamp_neu"] == "20261104120000000"
    j = client.get("/api/v1/controlling/auftraege/aenderungen?timestamp=20990101000000000").get_json()
    assert j["anzahl"] == 0

    j = client.get(f"/api/v1/controlling/kpi?von={von}&bis={heute}&orga_gruppe=01").get_json()
    assert "fehler" not in j, j
    assert j["umsatz"]["netto"] >= 151.84 and j["offene_posten"]["anzahl"] == 3 and j["offene_orgas"]["anzahl"] == 7
    assert "vorjahr" in j["umsatz"]

    r = client.get(f"/api/v1/controlling/rechnungen?von={von}&bis={heute}&format=csv")
    txt = r.get_data(as_text=True)
    assert txt.startswith("﻿rechnungsnummer;") and "100,0" in txt


def test_controlling_archiv(client):
    r = client.post("/api/v1/sync/controlling").get_json()
    assert r["ok"] and r["ergebnis"]["rechnungen_archiviert"] >= 2
    heute = date.today()
    j = client.get(f"/api/v1/controlling/rechnungen?von={heute - timedelta(days=60)}&bis={heute}&quelle=archiv").get_json()
    assert j["anzahl"] >= 2
    j = client.get("/api/v1/controlling/offene-posten/verlauf").get_json()
    assert j["daten"][0]["summe_offen"] == 1395.5


def test_kostenrechnung(client):
    r = client.post("/api/v1/controlling/kostenrechnung", json={
        "lieferant": "Medizintechnik AG", "ustid": "DE127500072", "rechnungsnummer": "4000001",
        "rechnungsdatum": "2026-10-20", "netto": 150.0, "brutto": 178.5,
        "anlagen": [{"dateiname": "rechnung.pdf", "datei_base64": "JVBERi0x"}]})
    assert r.status_code == 201, r.get_json()
    body = san6_mock.STATE.calls[-1][2].decode()
    assert "<Rechnungsdatum>20.10.2026</Rechnungsdatum>" in body and "<Dateiname>rechnung.pdf</Dateiname>" in body
    assert "<Netto>150.00</Netto>" in body


def test_san6_fehler_wird_durchgereicht(client):
    san6_mock.STATE.today = date.today()
    r = client.post("/api/v1/termine", json={"terminart": "80", "mitarbeiter": "012", "start": "2026-11-04T10:00",
                                             "ende": "2026-11-04T09:00"})
    assert r.status_code == 400
    r = client.patch("/api/v1/termine/99999999-0001", json={"start": "2026-11-04T10:00", "ende": "2026-11-04T11:00",
                                                            "mitarbeiter": "012"})
    assert r.status_code == 422 and r.get_json()["san6"]["code"] == "30"

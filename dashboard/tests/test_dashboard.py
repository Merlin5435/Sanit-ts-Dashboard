"""Tests der Testversion – ohne echte KI (ein Ersatz-Client spielt die Claude-Antworten)."""
import json
import os
import re
import sys
from datetime import date
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from dashboard.__main__ import baue_dienst  # noqa: E402
from dashboard.app.config import lade_config  # noqa: E402
from dashboard.app.web import create_app  # noqa: E402
from dashboard.testwelt.welt import KUNDEN  # noqa: E402

HEUTE = date(2026, 11, 18)


class ErsatzClaude:
    """Verhält sich wie anthropic.Anthropic().beta.messages – merkt sich, was gesendet wurde."""

    def __init__(self):
        self.gesendet: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.gesendet.append(kw)
        inhalt = kw["messages"][-1]["content"]
        if "output_config" in kw and "format" in kw["output_config"]:
            daten = json.loads(inhalt.split("\n\n", 1)[1])
            ids = [k["id"] for k in daten["kandidaten"]]
            rez = [i for i in ids if i.startswith("rezept:")][:3]
            mail = next(i for i in ids if i.startswith("mail:"))
            text = json.dumps({
                "zusammenfassung": "Heute zählen vor allem die Rezepte von K-01.",
                "punkte": [
                    {"titel": "Rezepte nachfordern", "begruendung": "K-01 wartet seit Wochen.", "dringlichkeit": "dringend",
                     "kandidaten": rez + ["gibt-es-nicht"], "aktion": "keine"},
                    {"titel": "Mail beantworten", "begruendung": "Wartet auf Antwort.", "dringlichkeit": "heute",
                     "kandidaten": [mail], "aktion": "antwort_entwerfen"},
                ],
                "assistent_angebot": "Soll ich K-01 nachfassen?", "vorschlaege": ["a", "b", "c", "d"]})
        else:
            text = "Antwort zu K-01."
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               model=kw["model"], usage=SimpleNamespace(input_tokens=100, output_tokens=20))


@pytest.fixture
def dienst(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return baue_dienst(lade_config(), db_pfad=":memory:", heute=HEUTE)


@pytest.fixture
def mit_ki(dienst, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    fake = ErsatzClaude()
    dienst.ki._client = fake
    return dienst, fake


def test_abgleich_ohne_ki_liefert_regeln(dienst):
    b = dienst.abgleich()
    assert b["ohne_ki"] is True
    assert b["punkte"] and b["fehler"] == []
    assert b["kennzahlen"]["umsatz_monat"] > 0 and b["kennzahlen"]["umsatz_vorjahr"] > 0
    assert b["kennzahlen"]["rezepte_fehlend"] == 11


def test_an_die_ki_gehen_keine_patientendaten(mit_ki):
    dienst, fake = mit_ki
    dienst.abgleich()
    gesendet = fake.gesendet[0]["messages"][0]["content"] + fake.gesendet[0]["system"]
    for nr, name, versnr, geb, tel in KUNDEN:
        assert name not in gesendet, name
        assert versnr not in gesendet
        assert tel not in gesendet
        assert geb not in gesendet
    assert re.search(r"\bK-\d{2}\b", gesendet)
    # Ärzte und Lieferanten (keine Patienten) dürfen drin sein
    assert "Praxis Dr. Brandt" in gesendet


def test_ki_briefing_wird_zurueckuebersetzt(mit_ki):
    dienst, fake = mit_ki
    b = dienst.abgleich()
    assert b["ohne_ki"] is False
    p1 = b["punkte"][0]
    assert "K-01" not in p1["begruendung"]
    assert b["zuordnung"]["K-01"] in p1["begruendung"]
    assert all(k["id"] != "gibt-es-nicht" for k in p1["kandidaten"])  # erfundene IDs werden verworfen
    assert b["punkte"][1]["mail_id"] and b["punkte"][1]["aktion"] == "antwort_entwerfen"
    assert len(b["vorschlaege"]) == 3
    kw = fake.gesendet[0]
    assert kw["fallbacks"] == "default" and kw["output_config"]["format"]["type"] == "json_schema"
    assert dienst.speicher.protokoll()[0]["zweck"] == "Briefing"


def test_erledigt_verschwindet_beim_naechsten_abgleich(mit_ki):
    dienst, _ = mit_ki
    b = dienst.abgleich()
    kids = [k["id"] for k in b["punkte"][0]["kandidaten"]]
    dienst.setze(kids, "erledigt")
    b2 = dienst.abgleich()
    alle = {k["id"] for p in b2["punkte"] for k in p["kandidaten"]} | {k["id"] for k in b2["weitere"]}
    assert not alle & set(kids)


def test_spaeter_kommt_am_naechsten_tag_wieder(dienst):
    b = dienst.abgleich()
    kid = b["weitere"][0]["id"] if b["weitere"] else b["punkte"][0]["kandidaten"][0]["id"]
    dienst.setze([kid], "spaeter")
    assert kid in dienst.speicher.ausgeblendet(dienst.heute())
    dienst.welt.naechster_tag()
    assert kid not in dienst.speicher.ausgeblendet(dienst.heute())


def test_neuer_tag_veraendert_daten(dienst):
    b1 = dienst.abgleich()
    b2 = dienst.neuer_tag()
    assert b2["heute"] > b1["heute"]
    assert {k["id"] for k in b1["weitere"]} != {k["id"] for k in b2["weitere"]}


def test_frage_und_entwurf_schwaerzen_namen(mit_ki):
    dienst, fake = mit_ki
    b = dienst.abgleich()
    name = b["zuordnung"]["K-01"]
    antwort = dienst.frage(f"Was ist mit {name}?", [])
    assert name in antwort                              # Anzeige mit echtem Namen
    assert name not in fake.gesendet[-1]["messages"][-1]["content"]   # gesendet nur Kürzel
    entwurf = dienst.entwurf(b["punkte"][1]["mail_id"])
    assert entwurf
    assert fake.gesendet[-1]["output_config"]["effort"] == "low"


def test_web_endpunkte(mit_ki):
    dienst, _ = mit_ki
    c = create_app(dienst).test_client()
    j = c.get("/api/briefing").get_json()
    assert j["testversion"] and "zuordnung" not in j and "kontext_ki" not in j
    kids = [k["id"] for k in j["punkte"][0]["kandidaten"]]
    assert c.post("/api/zustand", json={"kandidaten": kids, "zustand": "erledigt"}).status_code == 200
    assert c.post("/api/zustand", json={"kandidaten": kids, "zustand": "quatsch"}).status_code == 400
    assert c.post("/api/frage", json={"frage": "Hallo", "verlauf": []}).get_json()["antwort"]
    assert c.post("/api/neuer-tag").status_code == 200
    assert c.get("/api/ki-protokoll").get_json()
    assert c.get("/").status_code == 200


def test_ohne_schluessel_klare_meldung(dienst):
    dienst.abgleich()
    c = create_app(dienst).test_client()
    r = c.post("/api/frage", json={"frage": "Hallo", "verlauf": []})
    assert r.status_code == 503 and "nicht verbunden" in r.get_json()["fehler"]


def test_neue_wuensche_und_datenschutz(mit_ki):
    dienst, fake = mit_ki
    b = dienst.abgleich()
    k = b["kennzahlen"]
    assert k["kv_ueber_21"] > 0 and k["mahnung_3"] + k["mahnung_4"] == 4 and k["absetzungen_anzahl"] > 0
    assert k["telefonie"]["anrufe"] > 0 and b["rezeptpruefung"]
    gesendet = fake.gesendet[0]["messages"][0]["content"]
    for r in b["rezeptpruefung"]:
        assert r["diagnose"] not in gesendet          # Diagnosen gehen nie an die KI
    assert '"Krank"' not in gesendet and '"Urlaub"' not in gesendet   # Abwesenheitsgrund bleibt im Haus


def test_abgleich_rezepte_abrechnungen(dienst):
    b = dienst.abgleich()
    typen = {g["typ"] for g in b["abgleich"]}
    assert "Rezept fehlt im Archiv" in typen and "Noch nicht abgerechnet" in typen
    assert b["kennzahlen"]["absetzungen_anzahl"] > 0
    assert any(p["titel"].startswith("Rezept-Scans") for p in b["punkte"])

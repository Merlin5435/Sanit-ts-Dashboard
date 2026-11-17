"""REST-API (JSON) des TopM-Gateways.

Alle Endpunkte unter /api/v1 erfordern einen API-Key (Header ``X-API-Key`` oder ``Authorization: Bearer``).
Lese-Keys (GATEWAY_READONLY_KEYS) dürfen nur GET. Der iCal-Feed akzeptiert ``?token=`` (für Outlook-Abo).
"""
from __future__ import annotations

import csv
import hmac
import io
import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

from flask import Flask, Response, g, jsonify, request, send_file

from .calendar_sync import termine_to_ics
from .controlling import (GRUPPIERUNGEN, bestellungen_summary, lager_summary, offene_posten_summary,
                          summe_rechnungen, umsatz)
from .core import Gateway
from .san6_api import TERMIN_STATUS, termin_hash
from .san6_client import San6Error
from .xmlutil import fmt_date, parse_date

log = logging.getLogger("topm.api")
HERE = os.path.dirname(os.path.abspath(__file__))

VALIDATION_CODES = {"01", "10", "11", "12", "13", "14", "20", "30", "31", "32"}


class ApiError(Exception):
    def __init__(self, status: int, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.message, self.details = status, message, details


# ====================================================================== Hilfen
def _date_arg(name: str, default: date | None = None, required: bool = False) -> date:
    v = request.args.get(name, "").strip()
    if not v:
        if required or default is None:
            raise ApiError(400, f"Parameter '{name}' fehlt (JJJJ-MM-TT oder TT.MM.JJJJ).")
        return default
    d = parse_date(v)
    if d is None:
        raise ApiError(400, f"Parameter '{name}' hat ein ungültiges Datum: {v!r}")
    return d


def _bool_arg(name: str, default: bool = False) -> bool:
    v = request.args.get(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "ja", "j", "yes")


def _list_arg(name: str) -> list[str]:
    return [x.strip() for x in request.args.get(name, "").split(",") if x.strip()]


def _dt(v: Any, name: str) -> datetime:
    if not v:
        raise ApiError(400, f"Feld '{name}' fehlt (ISO-Format, z.B. 2026-11-04T10:00).")
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        raise ApiError(400, f"Feld '{name}' ist kein gültiger Zeitpunkt: {v!r}")


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON-Body erwartet.")
    return data


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _flatten(row: dict, prefix: str = "") -> dict:
    out: dict[str, Any] = {}
    for k, v in row.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        elif isinstance(v, list):
            out[key] = json.dumps(v, ensure_ascii=False) if v and isinstance(v[0], (dict, list)) else ", ".join(map(str, v))
        else:
            out[key] = v
    return out


def _csv(rows: list[dict], filename: str) -> Response:
    flat = [_flatten(r) for r in rows]
    cols: list[str] = []
    for r in flat:
        for k in r:
            if k not in cols:
                cols.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, delimiter=";", extrasaction="ignore")
    w.writeheader()
    for r in flat:
        w.writerow({k: (str(v).replace(".", ",") if isinstance(v, float) else
                        ("ja" if v is True else "nein" if v is False else v)) for k, v in r.items()})
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})


def _out(rows: list[dict], filename: str, extra: dict | None = None):
    if request.args.get("format", "json").lower() == "csv":
        return _csv(rows, filename)
    payload = dict(extra or {})
    payload.update(anzahl=len(rows), daten=rows)
    return jsonify(payload)


# ====================================================================== App
def create_app(gateway: Gateway | None = None) -> Flask:
    gw = gateway or Gateway()
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    app.config["GATEWAY"] = gw
    s = gw.settings

    # ---------------------------------------------------------------- Auth
    def _key_ok(key: str, keys: list[str]) -> bool:
        return any(hmac.compare_digest(key, k) for k in keys)

    @app.before_request
    def _auth():
        if not request.path.startswith("/api/"):
            return None
        if not s.api_keys and not s.readonly_keys:
            raise ApiError(503, "Keine API-Keys konfiguriert (GATEWAY_API_KEYS).")
        key = request.headers.get("X-API-Key", "")
        auth = request.headers.get("Authorization", "")
        if not key and auth.lower().startswith("bearer "):
            key = auth[7:].strip()
        if not key and request.path.endswith(".ics"):
            tok = request.args.get("token", "")
            if tok and ((s.ics_token and hmac.compare_digest(tok, s.ics_token)) or _key_ok(tok, s.api_keys + s.readonly_keys)):
                g.role = "read"
                return None
        if key and _key_ok(key, s.api_keys):
            g.role = "write"
            return None
        if key and _key_ok(key, s.readonly_keys):
            if request.method != "GET":
                raise ApiError(403, "Dieser API-Key ist nur lesend berechtigt.")
            g.role = "read"
            return None
        raise ApiError(401, "Ungültiger oder fehlender API-Key.")

    # ---------------------------------------------------------------- Fehler
    @app.errorhandler(ApiError)
    def _api_err(e: ApiError):
        body = {"fehler": e.message}
        if e.details is not None:
            body["details"] = e.details
        return jsonify(body), e.status

    @app.errorhandler(San6Error)
    def _san6_err(e: San6Error):
        status = 504 if e.code == "NET" else 422 if e.code in VALIDATION_CODES else 502
        return jsonify({"fehler": f"san6 meldet {e.code}-{e.text}", "san6": e.to_dict()}), status

    @app.errorhandler(ValueError)
    def _val_err(e: ValueError):
        return jsonify({"fehler": str(e)}), 400

    @app.errorhandler(404)
    def _nf(_e):
        return jsonify({"fehler": "Endpunkt nicht gefunden. Übersicht: /docs"}), 404

    # ---------------------------------------------------------------- Basis
    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "zeit": datetime.now().isoformat(timespec="seconds"),
                        "outlook_sync": bool(gw.graph), "mandant": s.san6.mandant})

    @app.get("/openapi.yaml")
    def openapi():
        return send_file(os.path.join(HERE, "openapi.yaml"), mimetype="application/yaml")

    @app.get("/docs")
    def docs():
        return Response("""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>TopM-Gateway API</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.17.14/swagger-ui.min.css"></head>
<body><div id="ui"></div><script src="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.17.14/swagger-ui-bundle.min.js"></script>
<script>SwaggerUIBundle({url:'/openapi.yaml',dom_id:'#ui'})</script></body></html>""", mimetype="text/html")

    # ---------------------------------------------------------------- System
    @app.get("/api/v1/system/ping")
    def sys_ping():
        return jsonify(gw.svc.ping())

    @app.get("/api/v1/system/status")
    def sys_status():
        return jsonify(gw.svc.status())

    @app.get("/api/v1/sync/status")
    def sync_status():
        last = gw.store.q("SELECT MAX(id) AS m FROM changes")[0]["m"] or 0
        return jsonify({"letzte_laeufe": gw.store.log_last(int(request.args.get("limit", 20))),
                        "letzte_aenderungs_id": last,
                        "controlling_letzter_lauf": gw.store.get("controlling_last_run"),
                        "intervall_minuten": s.sync.interval_minutes,
                        "outlook_postfaecher": s.sync.outlook_map if gw.graph else {}})

    @app.post("/api/v1/sync/termine")
    def sync_termine():
        r = gw.sync_termine()
        return jsonify(r), (200 if r["ok"] else 409 if "bereits" in r.get("fehler", "") else 502)

    @app.post("/api/v1/sync/controlling")
    def sync_controlling():
        r = gw.sync_controlling()
        return jsonify(r), (200 if r["ok"] else 409 if "bereits" in r.get("fehler", "") else 502)

    @app.get("/api/v1/sync/aenderungen")
    def aenderungen():
        seit = int(request.args.get("seit", 0))
        limit = min(int(request.args.get("limit", 500)), 5000)
        rows = gw.store.changes_since(seit, request.args.get("entity") or None, limit)
        return jsonify({"seit": seit, "naechste_seit": rows[-1]["id"] if rows else seit,
                        "anzahl": len(rows), "aenderungen": rows})

    # ---------------------------------------------------------------- Termine
    def _filter_termine(termine: list[dict]) -> list[dict]:
        st = _list_arg("status")
        if st:
            termine = [t for t in termine if t.get("status") in st]
        return termine

    @app.get("/api/v1/termine")
    def termine_list():
        heute = date.today()
        von = _date_arg("von", heute)
        bis = _date_arg("bis", von + timedelta(days=14))
        if bis < von:
            raise ApiError(400, "'bis' liegt vor 'von'.")
        ma = _list_arg("mitarbeiter")
        if request.args.get("quelle", "live") == "cache":
            rows = [r["data"] for r in gw.store.termine_in_window(
                von.isoformat(), (bis + timedelta(days=1)).isoformat(), ma or None)]
        else:
            rows = gw.svc.termine(von, bis, ma or None)
        return _out(_filter_termine(rows), "termine", {"von": von.isoformat(), "bis": bis.isoformat()})

    @app.get("/api/v1/termine/<nummer>")
    def termin_get(nummer: str):
        t = gw.store.termin_get(nummer)
        if not t:
            raise ApiError(404, f"Termin {nummer} ist nicht im Gateway-Cache. Erst /sync/termine ausführen "
                                f"oder /termine?von=..&bis=.. live abfragen.")
        return jsonify({**t["data"], "geloescht": bool(t["deleted"]), "zuletzt_gesehen": t["seen_at"]})

    @app.post("/api/v1/termine")
    def termin_create():
        b = _body()
        for f in ("terminart", "mitarbeiter", "start", "ende"):
            if not b.get(f):
                raise ApiError(400, f"Pflichtfeld '{f}' fehlt.")
        st, en = _dt(b["start"], "start"), _dt(b["ende"], "ende")
        if en <= st:
            raise ApiError(400, "'ende' muss nach 'start' liegen.")
        status = str(b.get("status", "0"))
        data = {
            "Terminart": b["terminart"], "Mitarbeiter": b["mitarbeiter"],
            "Datum": fmt_date(st.date()), "Uhrzeit": st.strftime("%H:%M"),
            "BisDatum": fmt_date(en.date()), "BisUhrzeit": en.strftime("%H:%M"),
            "SpaeterePlanung": "J" if b.get("spaetere_planung") else "N",
            "Status": f"{status}-{TERMIN_STATUS.get(status, '')}".rstrip("-"),
            "Thema": b.get("thema"), "Info": b.get("info"),
            "Kundennummer": b.get("kundennummer"), "Auftragsnummer": b.get("auftragsnummer"),
            "Kunde": b.get("kunde"), "Strasse": b.get("strasse"), "Plz": b.get("plz"),
            "Ort": b.get("ort"), "Telefon": b.get("telefon"),
        }
        res = gw.svc.termin_neu({k: v for k, v in data.items() if v not in (None, "")})
        gw.store.change("termin", res.get("nummer", "neu"), "created", "api", b)
        return jsonify(res), 201

    @app.post("/api/v1/termine/anfragen")
    def termin_anfrage():
        b = _body()
        for f in ("terminart", "filiale", "start", "mitarbeiter", "vorname", "nachname", "email"):
            if not b.get(f):
                raise ApiError(400, f"Pflichtfeld '{f}' fehlt.")
        st = _dt(b["start"], "start")
        data = {
            "Terminart": b["terminart"], "Filiale": b["filiale"],
            "Datum": fmt_date(st.date()), "Uhrzeit": st.strftime("%H:%M"),
            "Mitarbeiter": b["mitarbeiter"], "Vorname": b["vorname"], "Nachname": b["nachname"],
            "Strasse": b.get("strasse"), "Plz": b.get("plz"), "Ort": b.get("ort"),
            "Geburtsdatum": fmt_date(parse_date(b["geburtsdatum"])) if b.get("geburtsdatum") else None,
            "Versichertennummer": b.get("versichertennummer"), "Telefon": b.get("telefon"),
            "Mobil": b.get("mobil"), "EMail": b["email"], "Hinweise": b.get("hinweise"),
        }
        res = gw.svc.termin_anfrage({k: v for k, v in data.items() if v not in (None, "")})
        return jsonify(res), 201

    def _update(nummer: str, b: dict, source: str = "api") -> dict:
        cached = gw.store.termin_get(nummer)
        cur = cached["data"] if cached else {}
        start = _dt(b.get("start") or cur.get("start"), "start")
        ende = _dt(b.get("ende") or cur.get("ende"), "ende")
        if ende <= start:
            raise ApiError(400, "'ende' muss nach 'start' liegen.")
        ma = b.get("mitarbeiter") or cur.get("mitarbeiter")
        if not ma:
            raise ApiError(400, "Feld 'mitarbeiter' fehlt (Termin nicht im Cache).")
        if cur.get("serientermin"):
            raise ApiError(409, "Serientermine können über TERMINUPDATE nicht geändert werden (Kap. 4.18.4).")
        data: dict[str, Any] = {
            "Nummer": nummer,
            "Datum": fmt_date(start.date()), "Uhrzeit": start.strftime("%H:%M"),
            "BisDatum": fmt_date(ende.date()), "BisUhrzeit": ende.strftime("%H:%M"),
            "Mitarbeiter": ma,
        }
        if "status" in b:
            if str(b["status"]) not in TERMIN_STATUS:
                raise ApiError(400, f"Ungültiger Status. Erlaubt: {TERMIN_STATUS}")
            data["Status"] = str(b["status"])
        if "spaetere_planung" in b:
            data["SpaeterePlanung"] = "J" if b["spaetere_planung"] else "N"
        if "notfall" in b:
            data["Notfall"] = "J" if b["notfall"] else "N"
        if "infos" in b:
            data["Infos"] = b["infos"]
        res = gw.svc.termin_update(data)
        if cached:
            t = dict(cur)
            t.update(start=start.isoformat(timespec="minutes"), ende=ende.isoformat(timespec="minutes"),
                     mitarbeiter=ma)
            if "status" in data:
                t.update(status=data["Status"], status_text=TERMIN_STATUS[data["Status"]])
            if "infos" in b:
                t["infos"] = b["infos"]
            gw.store.termin_upsert(t, termin_hash(t))
            gw.store.change("termin", nummer, "updated", source, t)
        return res

    @app.patch("/api/v1/termine/<nummer>")
    def termin_patch(nummer: str):
        return jsonify(_update(nummer, _body()))

    @app.delete("/api/v1/termine/<nummer>")
    def termin_delete(nummer: str):
        status = request.args.get("status", "7")
        if status not in ("7", "9"):
            raise ApiError(400, "status muss 7 (abgesagt) oder 9 (gelöscht) sein.")
        return jsonify(_update(nummer, {"status": status}))

    @app.get("/api/v1/terminarten")
    def terminarten():
        return _out(gw.svc.terminarten(), "terminarten")

    @app.get("/api/v1/terminarten/<terminart>/filialen")
    def terminfilialen(terminart: str):
        return _out(gw.svc.terminfilialen(terminart), "filialen")

    @app.get("/api/v1/terminbelegung")
    def terminbelegung():
        ta, fi = request.args.get("terminart", ""), request.args.get("filiale", "")
        if not ta or not fi:
            raise ApiError(400, "Parameter 'terminart' und 'filiale' sind Pflicht.")
        d = _date_arg("datum", date.today())
        return jsonify({"terminart": ta, "filiale": fi, "tage": gw.svc.terminbelegung(ta, fi, d)})

    @app.get("/api/v1/kalender/<ma>.ics")
    def ics(ma: str):
        heute = date.today()
        von, bis = heute - timedelta(days=s.sync.days_back), heute + timedelta(days=s.sync.days_ahead)
        mas = None if ma.lower() in ("alle", "all") else [m for m in ma.split(",") if m]
        rows = [r["data"] for r in gw.store.termine_in_window(
            von.isoformat(), (bis + timedelta(days=1)).isoformat(), mas)]
        if not rows and gw.store.get("termine_live_fallback") != "aus":
            rows = gw.svc.termine(von, bis, mas)
        name = "san6 Termine" + ("" if mas is None else f" {ma}")
        return Response(termine_to_ics(rows, name), mimetype="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'inline; filename="san6-{ma}.ics"'})

    # ---------------------------------------------------------------- Controlling
    def _period() -> tuple[date, date]:
        heute = date.today()
        von = _date_arg("von", _month_start(heute))
        bis = _date_arg("bis", heute)
        if bis < von:
            raise ApiError(400, "'bis' liegt vor 'von'.")
        return von, bis

    def _rechnungen(von: date, bis: date, positionen: bool) -> list[dict]:
        if request.args.get("quelle", "live") == "archiv":
            rows = gw.store.rechnungen_query(von.isoformat(), bis.isoformat())
            zc, ko = request.args.get("zahlcode"), request.args.get("konto")
            if zc:
                rows = [r for r in rows if r.get("zahlcode") == zc]
            if ko:
                rows = [r for r in rows if (r.get("rechnungsadresse") or {}).get("konto") == ko]
            if not positionen:
                rows = [{k: v for k, v in r.items() if k != "positionen"} for r in rows]
            return rows
        return gw.svc.rechnungen(von, bis, request.args.get("zahlcode", ""), request.args.get("konto", ""),
                                 positionen)

    @app.get("/api/v1/controlling/kpi")
    def kpi():
        von, bis = _period()
        return jsonify(gw.controlling.kpi(von, bis, request.args.get("orga_gruppe", "")))

    @app.get("/api/v1/controlling/rechnungen")
    def rechnungen():
        von, bis = _period()
        rows = _rechnungen(von, bis, _bool_arg("positionen", False))
        return _out(rows, "rechnungen", {"von": von.isoformat(), "bis": bis.isoformat(),
                                         "summen": summe_rechnungen(rows)})

    @app.get("/api/v1/controlling/umsatz")
    def umsatz_ep():
        von, bis = _period()
        gr = request.args.get("gruppierung", "monat")
        if gr not in GRUPPIERUNGEN:
            raise ApiError(400, f"gruppierung muss eine von {', '.join(GRUPPIERUNGEN)} sein.")
        rows = _rechnungen(von, bis, gr == "artikel")
        return _out(umsatz(rows, gr), f"umsatz-{gr}", {"von": von.isoformat(), "bis": bis.isoformat(),
                                                       "gruppierung": gr, "summen": summe_rechnungen(rows)})

    @app.get("/api/v1/controlling/offene-posten")
    def offene_posten():
        ops = gw.svc.offene_posten()
        summary = offene_posten_summary(ops)
        if _bool_arg("nur_ueberfaellig"):
            ops = [o for o in ops if o["ueberfaellig"]]
        ops.sort(key=lambda o: -o["tage_ueberfaellig"])
        return _out(ops, "offene-posten", {"zusammenfassung": summary})

    @app.get("/api/v1/controlling/offene-posten/verlauf")
    def op_verlauf():
        heute = date.today()
        von = _date_arg("von", heute - timedelta(days=90))
        bis = _date_arg("bis", heute)
        return _out(gw.store.op_verlauf(von.isoformat(), bis.isoformat()), "offene-posten-verlauf")

    @app.get("/api/v1/controlling/abrechnungen")
    def abrechnungen():
        rows = gw.svc.abrechnungen()
        return _out(rows, "abrechnungen", {"summe": round(sum(r["summe"] for r in rows), 2)})

    @app.get("/api/v1/controlling/bestellungen")
    def bestellungen():
        rows = gw.svc.bestellungen(request.args.get("filiale", ""), request.args.get("bestellung", ""),
                                   request.args.get("abnr", ""))
        return _out(rows, "bestellungen", {"zusammenfassung": bestellungen_summary(rows)})

    @app.get("/api/v1/controlling/lieferrueckstaende")
    def lieferrueckstaende():
        return jsonify({"daten": gw.svc.lieferrueckstaende()})

    @app.get("/api/v1/controlling/lagerbestand")
    def lagerbestand():
        rows = gw.svc.lagerbestand(request.args.get("artikel", ""))
        fil = _list_arg("filiale")
        if fil:
            rows = [r for r in rows if r.get("filiale") in fil]
        return _out(rows, "lagerbestand", {"zusammenfassung": lager_summary(rows)})

    @app.get("/api/v1/controlling/auftraege/aenderungen")
    def auftraege_aenderungen():
        fortsetzen = _bool_arg("fortsetzen", False)
        ts = request.args.get("timestamp", "")
        if fortsetzen and not ts:
            ts = gw.store.get("auftraege_timestamp", "") or ""
        res = gw.svc.auftraege_geaendert(ts, request.args.get("schnittstelle", "*"))
        if fortsetzen and res.get("timestamp_neu"):
            gw.store.set("auftraege_timestamp", res["timestamp_neu"])
        res["timestamp_verwendet"] = ts
        return jsonify(res)

    @app.get("/api/v1/controlling/orgas")
    def orgas():
        gruppe = request.args.get("gruppe", "")
        if not gruppe:
            raise ApiError(400, "Parameter 'gruppe' fehlt (2-stellig = Gruppe, 3-stellig = Sachbearbeiter).")
        if _bool_arg("liste"):
            rows = gw.svc.offene_orgas(gruppe)
            return jsonify({"gruppe": gruppe, "anzahl": len(rows), "daten": rows})
        return jsonify({"gruppe": gruppe, "anzahl": gw.svc.anzahl_orgas(gruppe)})

    @app.post("/api/v1/controlling/kostenrechnung")
    def kostenrechnung():
        b = _body()
        anlagen = b.pop("anlagen", []) or []
        for a in anlagen:
            if not a.get("dateiname") or not a.get("datei_base64"):
                raise ApiError(400, "Jede Anlage braucht 'dateiname' und 'datei_base64'.")
        mapping = {"rechnungsempfaenger": "Rechnungsempfaenger", "rechnungsempfaenger1": "Rechnungsempfaenger1",
                   "lieferant": "Lieferant", "ustid": "UstID", "rechnungsnummer": "Rechnungsnummer",
                   "rechnungsdatum": "Rechnungsdatum", "leistungsdatum": "Leistungsdatum",
                   "leistungsdatum_bis": "LeistungsdatumBis", "netto": "Netto", "netto0": "Netto0",
                   "netto1": "Netto1", "netto2": "Netto2", "brutto": "Brutto", "bank": "Bank", "iban": "IBAN",
                   "bic": "BIC", "email_id": "EMailID", "anhang_id": "AnhangID"}
        data: dict[str, Any] = {}
        for k, x in mapping.items():
            if b.get(k) not in (None, ""):
                v = b[k]
                if k in ("rechnungsdatum", "leistungsdatum", "leistungsdatum_bis") and parse_date(str(v)):
                    v = fmt_date(parse_date(str(v)))
                if isinstance(v, float):
                    v = f"{v:.2f}"
                data[x] = v
        if anlagen:
            data["Anlagen"] = {"Anlage": [{"Dateiname": a["dateiname"], "Bezeichnung": a.get("bezeichnung", ""),
                                           "Datei": a["datei_base64"]} for a in anlagen]}
        res = gw.svc.kostenrechnung(data)
        gw.store.change("kostenrechnung", str(b.get("rechnungsnummer", "")), "created", "api",
                        {k: v for k, v in data.items() if k != "Anlagen"})
        return jsonify(res), 201

    return app

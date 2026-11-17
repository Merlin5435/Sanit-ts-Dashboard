"""Controlling-Auswertungen auf Basis der san6-Daten (Umsatz, offene Posten, Lager, Einkauf, Termine)."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .san6_api import San6Service
from .san6_client import San6Error
from .store import Store

log = logging.getLogger("topm.controlling")

GRUPPIERUNGEN = ("monat", "tag", "woche", "zahlcode", "kostentraeger", "artikel", "status")


def _r(x: float) -> float:
    return round(x + 0.0, 2)


def umsatz(rechnungen: list[dict], gruppierung: str = "monat") -> list[dict]:
    if gruppierung not in GRUPPIERUNGEN:
        raise ValueError(f"gruppierung muss eine von {', '.join(GRUPPIERUNGEN)} sein")
    acc: dict[str, dict[str, Any]] = defaultdict(lambda: {"anzahl": 0, "netto": 0.0, "brutto": 0.0, "menge": 0.0})
    for r in rechnungen:
        if gruppierung == "artikel":
            for p in r.get("positionen") or []:
                k = f"{p['artikelnummer']}{('-' + p['groesse']) if p.get('groesse') else ''}"
                a = acc[k]
                a["anzahl"] += 1
                a["netto"] += p.get("gesamt", 0.0)
                a["menge"] += p.get("menge", 0.0)
                a.setdefault("bezeichnung", p.get("bezeichnung", ""))
            continue
        d = r.get("rechnungsdatum") or ""
        if gruppierung == "monat":
            k = d[:7]
        elif gruppierung == "tag":
            k = d
        elif gruppierung == "woche":
            k = date.fromisoformat(d).strftime("%G-KW%V") if d else ""
        elif gruppierung == "zahlcode":
            k = r.get("zahlcode", "")
        elif gruppierung == "status":
            k = r.get("status", "")
        else:  # kostentraeger
            adr = r.get("rechnungsadresse") or {}
            k = f"{adr.get('konto', '')} {adr.get('name', '')}".strip()
        a = acc[k]
        a["anzahl"] += 1
        a["netto"] += r.get("netto", 0.0)
        a["brutto"] += r.get("brutto", 0.0)
    out = []
    for k, a in acc.items():
        row = {"schluessel": k, "anzahl": a["anzahl"], "netto": _r(a["netto"])}
        if gruppierung == "artikel":
            row.update(menge=a["menge"], bezeichnung=a.get("bezeichnung", ""))
        else:
            row.update(brutto=_r(a["brutto"]), mwst=_r(a["brutto"] - a["netto"]),
                       durchschnitt_netto=_r(a["netto"] / a["anzahl"]) if a["anzahl"] else 0.0)
        out.append(row)
    key: Callable[[dict], Any] = (lambda x: -x["netto"]) if gruppierung in ("artikel", "kostentraeger") \
        else (lambda x: x["schluessel"])
    return sorted(out, key=key)


def summe_rechnungen(rechnungen: list[dict]) -> dict:
    n = sum(r.get("netto", 0.0) for r in rechnungen)
    b = sum(r.get("brutto", 0.0) for r in rechnungen)
    return {"anzahl": len(rechnungen), "netto": _r(n), "brutto": _r(b), "mwst": _r(b - n),
            "durchschnitt_netto": _r(n / len(rechnungen)) if rechnungen else 0.0}


AGING = (("nicht_faellig", 0, 0), ("1_30", 1, 30), ("31_60", 31, 60), ("61_90", 61, 90), ("ueber_90", 91, 10 ** 6))


def offene_posten_summary(ops: list[dict]) -> dict:
    offen = [o for o in ops if o.get("offen", 0) > 0]
    buckets = {name: {"anzahl": 0, "summe": 0.0} for name, _, _ in AGING}
    for o in offen:
        t = o.get("tage_ueberfaellig", 0)
        for name, lo, hi in AGING:
            if lo <= t <= hi:
                buckets[name]["anzahl"] += 1
                buckets[name]["summe"] += o["offen"]
                break
    for b in buckets.values():
        b["summe"] = _r(b["summe"])
    schuldner: dict[str, dict] = defaultdict(lambda: {"name": "", "summe": 0.0, "anzahl": 0})
    for o in offen:
        s = schuldner[o.get("kundennr", "")]
        s["name"] = o.get("name", "")
        s["summe"] += o["offen"]
        s["anzahl"] += 1
    top = sorted(({"kundennr": k, **v, "summe": _r(v["summe"])} for k, v in schuldner.items()),
                 key=lambda x: -x["summe"])[:10]
    return {
        "anzahl": len(offen),
        "summe_offen": _r(sum(o["offen"] for o in offen)),
        "summe_ueberfaellig": _r(sum(o["offen"] for o in offen if o.get("ueberfaellig"))),
        "anzahl_gemahnt": sum(1 for o in offen if o.get("mahnung_am")),
        "anzahl_inkasso": sum(1 for o in offen if o.get("inkasso")),
        "summe_eigenanteile": _r(sum(o["offen"] for o in offen if "eigen" in (o.get("rechnungstyp") or "").lower())),
        "altersstruktur": buckets,
        "top_schuldner": top,
    }


def lager_summary(items: list[dict], heute: date | None = None) -> dict:
    heute = heute or date.today()
    grenze = (heute + timedelta(days=90)).isoformat()
    artikel = {(i["artikelnummer"], i["groesse"]) for i in items}
    per_filiale: dict[str, float] = defaultdict(float)
    for i in items:
        per_filiale[i.get("filiale") or "-"] += i["bestand"]
    return {
        "lagereintraege": len(items),
        "artikel": len(artikel),
        "bestand_gesamt": sum(i["bestand"] for i in items),
        "bestellt_gesamt": sum(i["bestellt"] for i in items),
        "reserviert_gesamt": sum(i["reserviert"] for i in items),
        "negativbestaende": sum(1 for i in items if i["bestand"] < 0),
        "verfall_90_tage": sum(1 for i in items if i.get("verfallsdatum") and i["verfallsdatum"] <= grenze),
        "bestand_je_filiale": dict(sorted(per_filiale.items())),
    }


def bestellungen_summary(items: list[dict], heute: date | None = None) -> dict:
    heute_s = (heute or date.today()).isoformat()
    lief: dict[str, int] = defaultdict(int)
    for b in items:
        lief[f"{b['lieferantennummer']} {b['lieferantenname']}".strip()] += 1
    return {
        "offene_bestellungen": len(items),
        "offene_positionen": sum(1 for b in items for p in b["positionen"] if p["offen"] > 0),
        "offene_menge": sum(b["offene_menge"] for b in items),
        "ueberfaellig": sum(1 for b in items if b.get("liefertermin") and b["liefertermin"] < heute_s
                            and b["offene_menge"] > 0),
        "je_lieferant": dict(sorted(lief.items(), key=lambda x: -x[1])[:15]),
    }


def termine_summary(termine: list[dict]) -> dict:
    by_status: dict[str, int] = defaultdict(int)
    by_ma: dict[str, int] = defaultdict(int)
    by_art: dict[str, int] = defaultdict(int)
    minuten = 0
    for t in termine:
        by_status[t.get("status_text") or t.get("status", "")] += 1
        by_ma[t.get("mitarbeiter") or t.get("mitarbeiter_nr", "")] += 1
        by_art[t.get("terminart", "")] += 1
        if not t.get("ganztaegig") and t.get("start") and t.get("ende") and t.get("status") not in ("7", "9"):
            minuten += int((datetime.fromisoformat(t["ende"]) - datetime.fromisoformat(t["start"])).total_seconds() // 60)
    return {"anzahl": len(termine), "je_status": dict(by_status), "je_mitarbeiter": dict(by_ma),
            "je_terminart": dict(by_art), "geplante_stunden": round(minuten / 60, 1),
            "abgesagt_quote": round(by_status.get("abgesagt", 0) / len(termine), 3) if termine else 0.0}


class Controlling:
    def __init__(self, svc: San6Service, store: Store):
        self.svc = svc
        self.store = store

    def kpi(self, von: date, bis: date, orga_gruppe: str = "") -> dict:
        out: dict[str, Any] = {"zeitraum": {"von": von.isoformat(), "bis": bis.isoformat()}, "fehler": {}}

        def section(name: str, fn: Callable[[], Any]) -> None:
            try:
                out[name] = fn()
            except San6Error as e:
                out["fehler"][name] = e.to_dict()
                log.warning("KPI %s: %s", name, e)

        def _umsatz() -> dict:
            re_ = self.svc.rechnungen(von, bis, mit_positionen=False)
            vj_von, vj_bis = _vorjahr(von), _vorjahr(bis)
            res = {**summe_rechnungen(re_), "je_monat": umsatz(re_, "monat"), "je_zahlcode": umsatz(re_, "zahlcode")}
            try:
                vj = summe_rechnungen(self.svc.rechnungen(vj_von, vj_bis, mit_positionen=False))
                res["vorjahr"] = {**vj, "zeitraum": [vj_von.isoformat(), vj_bis.isoformat()],
                                  "veraenderung_netto_prozent":
                                      round((res["netto"] / vj["netto"] - 1) * 100, 1) if vj["netto"] else None}
            except San6Error:
                pass
            return res

        section("umsatz", _umsatz)
        section("offene_posten", lambda: offene_posten_summary(self.svc.offene_posten()))
        section("einkauf", lambda: bestellungen_summary(self.svc.bestellungen()))
        section("lager", lambda: lager_summary(self.svc.lagerbestand()))
        section("termine", lambda: termine_summary(self.svc.termine(von, bis)))
        if orga_gruppe:
            section("offene_orgas", lambda: {"gruppe": orga_gruppe, "anzahl": self.svc.anzahl_orgas(orga_gruppe)})
        if not out["fehler"]:
            del out["fehler"]
        return out

    def archive_run(self, days_back: int, heute: date | None = None) -> dict:
        """Täglicher Lauf: Rechnungen archivieren + Snapshot der offenen Posten (für Trendanalysen)."""
        heute = heute or date.today()
        res: dict[str, Any] = {}
        re_ = self.svc.rechnungen(heute - timedelta(days=days_back), heute, mit_positionen=True)
        res["rechnungen_archiviert"] = self.store.rechnungen_upsert(re_)
        try:
            ops = self.svc.offene_posten(heute)
            s = offene_posten_summary(ops)
            self.store.op_snapshot(heute.isoformat(), s["anzahl"], s["summe_offen"], s["summe_ueberfaellig"],
                                   s["altersstruktur"])
            res["offene_posten"] = {"anzahl": s["anzahl"], "summe_offen": s["summe_offen"]}
        except San6Error as e:
            res["offene_posten_fehler"] = e.to_dict()
        return res


def _vorjahr(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29.02.
        return d.replace(year=d.year - 1, day=28)

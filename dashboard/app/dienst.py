"""Der Dienst: verbindet Abholen -> Kürzel -> KI -> Speicher. Läuft auf dem Server, morgens automatisch."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable

from topm_gateway.graph_client import GraphClient
from topm_gateway.san6_client import San6Client

from . import abholen, kandidaten
from .config import Config
from .ki import KI, KIFehler
from .kuerzel import Kuerzel
from .speicher import Speicher

log = logging.getLogger("dashboard.dienst")

DRINGLICHKEIT_REIHENFOLGE = {"dringend": 0, "heute": 1, "diese Woche": 2}


def _pid(kids: list[str]) -> str:
    return "p" + hashlib.sha1("|".join(sorted(kids)).encode()).hexdigest()[:10]


class Dienst:
    def __init__(self, cfg: Config, san6: San6Client, graph: GraphClient, speicher: Speicher,
                 heute: Callable[[], date] = date.today, welt=None):
        self.cfg, self.san6, self.graph, self.speicher = cfg, san6, graph, speicher
        self.heute = heute
        self.welt = welt            # nur in der Testversion
        self.ki = KI(cfg, speicher)
        self._lauf = threading.Lock()

    # ------------------------------------------------------------------ Abgleich
    def abgleich(self) -> dict:
        with self._lauf:
            t0 = time.time()
            heute = self.heute()
            s6 = abholen.san6_daten(self.san6, self.cfg, heute)
            ol = abholen.outlook_daten(self.graph, self.cfg.chef_postfach, heute)
            kz = Kuerzel()
            for t in s6.get("termine", []):         # alle bekannten Kundennamen vorab registrieren
                kz.kunde(t.get("kunde", ""))
            for r in s6.get("offene_rechnungen", []):
                kz.kunde(r["name"])
            kands = kandidaten.baue(s6, ol, heute, kz, self.speicher.ausgeblendet(heute))
            kzahlen = kandidaten.kennzahlen(s6, heute)
            termine = abholen.termine_chef(s6, ol, self.cfg.chef_nummer)
            auslastung = self._auslastung(s6)

            eingabe = {
                "heute": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][heute.weekday()]
                         + heute.strftime(", %d.%m.%Y"),
                "kennzahlen": kzahlen,
                "termine_geschaeftsfuehrer": [{"zeit": t["zeit"], "titel": kz.schwaerzen(t["titel"])} for t in termine],
                "termine_mitarbeiter_heute": auslastung,
                "kandidaten": [{"id": k["id"], "art": k["art"], "text": k["ki"]} for k in kands],
            }
            ergebnis, ohne_ki, ki_fehler = None, not self.ki.aktiv, None
            if self.ki.aktiv:
                try:
                    ergebnis = self.ki.briefing(eingabe)
                except KIFehler as e:
                    ki_fehler, ohne_ki = str(e), True
            if ergebnis is None:
                ergebnis = self._ohne_ki(kands)

            briefing = self._zusammenbauen(ergebnis, kands, kz, heute, kzahlen, termine, ol, ohne_ki)
            briefing["fehler"] = s6["fehler"] + ol["fehler"] + ([ki_fehler] if ki_fehler else [])
            briefing["kontext_ki"] = json.dumps(eingabe, ensure_ascii=False)
            briefing["dauer_s"] = round(time.time() - t0, 1)
            self.speicher.briefing_speichern(briefing)
            log.info("Abgleich fertig: %d Kandidaten, %d Punkte, ohne_ki=%s", len(kands), len(briefing["punkte"]), ohne_ki)
            return briefing

    def _auslastung(self, s6: dict) -> list[dict]:
        pro: dict[str, dict] = {}
        for t in s6.get("termine", []):
            p = pro.setdefault(t["mitarbeiter"], {"mitarbeiter": t["mitarbeiter_name"], "vormittag": 0, "nachmittag": 0})
            p["vormittag" if t["zeit"] < "12:00" else "nachmittag"] += 1
        return list(pro.values())

    def _zusammenbauen(self, erg: dict, kands: list[dict], kz: Kuerzel, heute: date, kzahlen: dict,
                       termine: list[dict], ol: dict, ohne_ki: bool) -> dict:
        nach_id = {k["id"]: k for k in kands}
        punkte, benutzt = [], set()
        for p in erg.get("punkte", []):
            kids = [k for k in p.get("kandidaten", []) if k in nach_id and k not in benutzt]
            if not kids:
                continue
            benutzt.update(kids)
            mail = next((nach_id[k].get("mail_id") for k in kids if nach_id[k].get("mail_id")), None)
            punkte.append({
                "id": _pid(kids), "titel": kz.zurueck(p["titel"]), "begruendung": kz.zurueck(p["begruendung"]),
                "dringlichkeit": p.get("dringlichkeit", "heute"),
                "quellen": sorted({nach_id[k]["quelle"] for k in kids}),
                "kandidaten": [{"id": k, "anzeige": nach_id[k]["anzeige"], "quelle": nach_id[k]["quelle"]} for k in kids],
                "aktion": p.get("aktion") if mail else "keine", "mail_id": mail,
            })
        weitere = [{"id": k["id"], "anzeige": k["anzeige"], "quelle": k["quelle"]} for k in kands if k["id"] not in benutzt]
        mails = [{"id": m["id"], "von": m["von"], "betreff": m["betreff"], "zeit": m["zeit"]}
                 for m in ol.get("mails", []) if m["markiert"]]
        return {
            "erstellt": datetime.now().isoformat(timespec="minutes"), "heute": heute.isoformat(),
            "ohne_ki": ohne_ki, "modell": None if ohne_ki else self.cfg.modell,
            "zusammenfassung": kz.zurueck(erg.get("zusammenfassung", "")),
            "punkte": punkte, "weitere": weitere, "kandidaten_geprueft": len(kands),
            "assistent_angebot": kz.zurueck(erg.get("assistent_angebot", "")),
            "vorschlaege": [kz.zurueck(v) for v in erg.get("vorschlaege", [])][:3],
            "kennzahlen": kzahlen, "termine": termine, "postfach": mails,
            "zuordnung": kz.kz_zu_name,
        }

    def _ohne_ki(self, kands: list[dict]) -> dict:
        """Einfache feste Regeln – nur wenn kein API-Schlüssel hinterlegt ist. Wird im Dashboard klar so gekennzeichnet."""
        punkte = []
        rez = [k for k in kands if k["art"] == "fehlendes Rezept"]
        alt = [k for k in rez if k.get("tage", 0) > 14]
        if rez:
            punkte.append({"titel": f"{len(rez)} fehlende Rezepte nachfordern",
                           "begruendung": f"{len(alt)} davon seit über 14 Tagen offen",
                           "dringlichkeit": "dringend" if alt else "heute", "kandidaten": [k["id"] for k in rez], "aktion": "keine"})
        for art, titel in [("Anliegen aus der KI-Hotline", "Anliegen aus der KI-Hotline bearbeiten"),
                           ("offene Rechnung", "Überfällige Rechnungen prüfen"),
                           ("Lieferrückstand", "Lieferrückstände klären"), ("Wiedervorlage", "Fällige Wiedervorlagen")]:
            ks = [k for k in kands if k["art"] == art]
            if ks:
                punkte.append({"titel": f"{titel} ({len(ks)})", "begruendung": "",
                               "dringlichkeit": "heute", "kandidaten": [k["id"] for k in ks], "aktion": "keine"})
        for k in [k for k in kands if k["art"] == "E-Mail" and k.get("markiert")][:2]:
            punkte.append({"titel": k["anzeige"], "begruendung": "Als „Antwort nötig“ markiert",
                           "dringlichkeit": "heute", "kandidaten": [k["id"]], "aktion": "antwort_entwerfen"})
        return {"zusammenfassung": "Ohne KI – Liste nach festen Regeln sortiert.",
                "punkte": punkte[:5], "assistent_angebot": "", "vorschlaege": []}

    # ------------------------------------------------------------------ Aktionen
    def setze(self, kids: list[str], zustand: str) -> None:
        bis = self.heute() + timedelta(days=1) if zustand == "spaeter" else None
        self.speicher.setze(kids, zustand, bis)

    def frage(self, frage: str, verlauf: list[dict]) -> str:
        b = self.speicher.letztes_briefing()
        if not b:
            raise KIFehler("Es gibt noch keinen Abgleich.")
        if not self.ki.aktiv:
            raise KIFehler("Die KI ist nicht verbunden (kein API-Schlüssel hinterlegt).")
        kz = Kuerzel(b.get("zuordnung"))
        antwort = self.ki.frage(b["kontext_ki"], [{"role": m["role"], "content": kz.schwaerzen(m["content"])} for m in verlauf],
                                kz.schwaerzen(frage))
        return kz.zurueck(antwort)

    def entwurf(self, mail_id: str) -> str:
        if not self.ki.aktiv:
            raise KIFehler("Die KI ist nicht verbunden (kein API-Schlüssel hinterlegt).")
        b = self.speicher.letztes_briefing() or {}
        kz = Kuerzel(b.get("zuordnung"))
        ol = abholen.outlook_daten(self.graph, self.cfg.chef_postfach, self.heute())
        m = next((m for m in ol["mails"] if m["id"] == mail_id), None)
        if not m:
            raise KIFehler("Die Mail wurde nicht gefunden.")
        text = f"Von: {m['von']}\nBetreff: {m['betreff']}\n\n{m['text']}"
        return kz.zurueck(self.ki.entwurf(kz.schwaerzen(text)))

    def neuer_tag(self) -> dict:
        if not self.welt:
            raise RuntimeError("Nur in der Testversion verfügbar.")
        self.welt.naechster_tag()
        return self.abgleich()

    # ------------------------------------------------------------------ Zeitplan (5 Uhr)
    def starte_zeitplan(self) -> None:
        def schleife():
            while True:
                jetzt = datetime.now()
                ziel = jetzt.replace(hour=self.cfg.abgleich_stunde, minute=self.cfg.abgleich_minute, second=0, microsecond=0)
                if ziel <= jetzt:
                    ziel += timedelta(days=1)
                time.sleep((ziel - jetzt).total_seconds())
                try:
                    self.abgleich()
                except Exception:
                    log.exception("Morgen-Abgleich fehlgeschlagen")
        threading.Thread(target=schleife, daemon=True, name="zeitplan").start()

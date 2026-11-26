"""Aus den Rohdaten werden "Kandidaten": einzelne Dinge, die heute wichtig sein könnten.

Jeder Kandidat hat eine feste ID (z. B. "rezept:000412"), damit "Erledigt" und "Später"
über mehrere Abgleiche hinweg funktionieren. Jeder Kandidat gibt es zweimal:
- "anzeige": mit echten Namen, bleibt auf dem Server
- "ki":      mit Kürzeln, ohne Telefon/Versichertennummer – nur das geht an Claude
"""
from __future__ import annotations

from datetime import date

from .kuerzel import Kuerzel


def _eur(x: float) -> str:
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def baue(san6: dict, outlook: dict, heute: date, kz: Kuerzel, ausgeblendet: set[str],
         rezeptpruefung: list[dict] | None = None, abgleich: list[dict] | None = None) -> list[dict]:
    rezeptpruefung = rezeptpruefung or []
    out: list[dict] = []

    def neu(kid: str, art: str, quelle: str, anzeige: str, ki: str, **extra):
        if kid in ausgeblendet:
            return
        out.append({"id": kid, "art": art, "quelle": quelle, "anzeige": anzeige, "ki": kz.schwaerzen(ki), **extra})

    for r in san6.get("rezepte", []):
        k = kz.kunde(r["kunde"], r.get("versichertennr", ""))
        neu(f"rezept:{r['auftrag']}", "fehlendes Rezept", "san6 · Rezeptanforderungen",
            f"{r['kunde']} – {r['stichwort']}, angefordert vor {r['tage']} Tagen bei {r['arzt']} ({r['kasse']})",
            f"Kunde {k}: Rezept für {r['stichwort']} seit {r['tage']} Tagen bei {r['arzt']} angefordert, "
            f"nicht eingegangen. Kasse: {r['kasse']}.", tage=r["tage"], arzt=r["arzt"])

    for w in san6.get("wiedervorlagen", []):
        if not w["faellig"]:
            continue
        neu(f"wv:{w['nummer']}", "Wiedervorlage", "san6 · Wiedervorlagen",
            f"Wiedervorlage {w['vorgang']} ({w['datum']}): {w['bemerkung']}",
            f"Wiedervorlage zu {w['typ']} {w['vorgang']}, fällig {w['datum']}: {w['bemerkung']}.")

    for o in san6.get("hotline", []):
        k = kz.kunde(o["kunde"])
        neu(f"orga:{o['nummer']}", "Anliegen aus der KI-Hotline", "san6 · Orgas der Hotline",
            f"{o['thema']} – {o['text']}", f"{o['thema']} vom {o['datum']}, Kunde {k}: {o['text']}",
            thema=o["thema"])

    for o in san6.get("orgas_chef", []):
        neu(f"orga:{o['nummer']}", "Orga an den Chef", "san6 · Orgas",
            f"{o['thema']}: {o['text']}", f"Interne Aufgabe an den Chef vom {o['datum']}: {o['thema']} – {o['text']}")

    for r in san6.get("offene_rechnungen", []):
        if not r["ueberfaellig"]:
            continue
        k = kz.kunde(r["name"])
        stufe = "gemahnt" if r["mahnung_am"] else "überfällig"
        neu(f"op:{r['rechnungsnummer']}", "offene Rechnung", "san6 · Offene Rechnungen",
            f"Rechnung {r['rechnungsnummer']} ({r['name']}): {_eur(r['offen'])} offen, {r['tage_ueberfaellig']} Tage {stufe}",
            f"{r['rechnungstyp']}-Rechnung {r['rechnungsnummer']} an {k}: {_eur(r['offen'])} offen, "
            f"{r['tage_ueberfaellig']} Tage über Zahlungsziel, {stufe}.", betrag=r["offen"])

    for b in san6.get("rueckstaende", []):
        neu(f"rueckstand:{b['bestellung']}", "Lieferrückstand", "san6 · Lieferrückstände",
            f"{b['lieferant']}: {int(b['offen'])} × {b['artikel']} offen, Liefertermin {b['liefertermin']}",
            f"Bestellung {b['bestellung']} bei {b['lieferant']}: {int(b['offen'])} × {b['artikel']} noch offen, "
            f"zugesagter Liefertermin {b['liefertermin']}.")

    for m in outlook.get("mails", []):
        if m["gelesen"] and not m["markiert"]:
            continue
        neu(f"mail:{m['id']}", "E-Mail", "Outlook · Mail",
            f"{m['von']}: {m['betreff']}", f"Mail von {m['von']} ({m['zeit'][:10]}), Betreff „{m['betreff']}“: {m['text']}",
            mail_id=m["id"], markiert=m["markiert"])

    # --- Wünsche des Chefs (Nov. 2026)
    for a in san6.get("auftraege", []):
        if a["status"] == "1" and a["tage"] > 21:
            k = kz.kunde(a["kunde"])
            neu(f"kv:{a['auftrag']}", "KV älter als 3 Wochen", "san6 · Aufträge",
                f"KV {a['auftrag']} ({a['kunde']}, {a['kasse']}): {a['produkt']}, eingereicht vor {a['tage']} Tagen",
                f"Kostenvoranschlag {a['auftrag']} für {k} bei {a['kasse']}: {a['produkt']}, {_eur(a['betrag'])}, "
                f"vor {a['tage']} Tagen eingereicht, noch keine Antwort der Kasse.", tage=a["tage"])
        elif a["status"] in ("6", "7"):
            k = kz.kunde(a["kunde"])
            stufe = "3. Mahnung" if a["status"] == "6" else "4. Mahnung"
            neu(f"mahnung:{a['auftrag']}", stufe, "san6 · Aufträge",
                f"{stufe}: Auftrag {a['auftrag']} ({a['kunde']}), {_eur(a['betrag'])}",
                f"Auftrag {a['auftrag']} an {k} steht auf {stufe}, offen {_eur(a['betrag'])}.")
    for r in rezeptpruefung:
        k = kz.kunde(r["kunde"])
        neu(f"pruefung:{r['auftrag']}", "Rezept mit Absetzungsrisiko", "san6 · Rezeptprüfung",
            f"{r['kunde']} – {r['produkt']} (Diagnose {r['diagnose']}): {r['hinweis']}",
            # Diagnose geht NICHT an die KI, nur Produkt und Hinweis
            f"Neues Rezept von {k} für {r['produkt']} vor der Abrechnung: dieselbe Kombination aus Produkt und "
            f"Diagnosegruppe wurde zuletzt {r['anzahl']}× abgesetzt, meist wegen „{r['grund']}“.")
    for g in abgleich or []:
        k = kz.kunde(g["kunde"])
        neu(f"abgleich:{g['auftrag']}", g["typ"], "san6 · Abgleich Abrechnung",
            f"{g['kunde']} – {g['produkt']} ({g['kasse']}): {g['hinweis']}",
            f"{g['typ']}: Auftrag {g['auftrag']} von {k}, {g['produkt']}, {g['kasse']}. {g['hinweis']}.")
    return out


def kennzahlen(san6: dict, heute: date, zusatz: dict | None = None, rezeptpruefung: list | None = None,
               abgleich: list | None = None) -> dict:
    zusatz = zusatz or {}
    auftraege = san6.get("auftraege", [])
    kvs = [a for a in auftraege if a["status"] == "1"]
    offen = [a for a in auftraege if a["status"] == "0"]
    mahn = [a for a in auftraege if a["status"] in ("6", "7")]
    abr = [a for a in san6.get("abrechnungen", []) if a["status"] == "0"]
    abs_ = zusatz.get("absetzungen") or []
    gruende: dict[str, int] = {}
    for a in abs_:
        gruende[a["grund"]] = gruende.get(a["grund"], 0) + 1
    extra = {
        "kv_offen": len(kvs), "kv_ueber_21": sum(1 for a in kvs if a["tage"] > 21),
        "kv_summe": round(sum(a["betrag"] for a in kvs), 2),
        "auftraege_offen": len(offen), "auftraege_summe": round(sum(a["betrag"] for a in offen), 2),
        "mahnung_3": sum(1 for a in mahn if a["status"] == "6"), "mahnung_4": sum(1 for a in mahn if a["status"] == "7"),
        "mahnung_summe": round(sum(a["betrag"] for a in mahn), 2),
        "abrechnungen_offen": len(abr), "abrechnungen_summe": round(sum(a["summe"] for a in abr), 2),
        "abrechnungen": [{"kasse": a["kasse"], "datum": a["datum"], "summe": a["summe"]} for a in abr],
        "absetzungen_anzahl": len(abs_), "absetzungen_summe": round(sum(a["betrag"] for a in abs_), 2),
        "absetzungen_gruende": dict(sorted(gruende.items(), key=lambda x: -x[1])[:4]),
        "rezepte_geprueft": len(rezeptpruefung or []),
        "abgleich_ohne_scan": sum(1 for g in abgleich or [] if g["typ"] == "Rezept fehlt im Archiv"),
        "abgleich_nicht_abgerechnet": sum(1 for g in abgleich or [] if g["typ"] == "Noch nicht abgerechnet"),
        "telefonie": zusatz.get("telefonie"),
        "abwesend": san6.get("abwesend", []),
    }
    return {**_kennzahlen_basis(san6, heute), **extra}


def _kennzahlen_basis(san6: dict, heute: date) -> dict:
    ops = san6.get("offene_rechnungen", [])
    nicht_faellig = sum(r["offen"] for r in ops if not r["ueberfaellig"])
    ueberfaellig = sum(r["offen"] for r in ops if r["ueberfaellig"] and not r["mahnung_am"])
    gemahnt = sum(r["offen"] for r in ops if r["mahnung_am"])
    rezepte = san6.get("rezepte", [])
    hotline: dict[str, int] = {}
    for o in san6.get("hotline", []):
        art = o["thema"].replace("Hotline: ", "")
        hotline[art] = hotline.get(art, 0) + 1
    um = san6.get("umsatz") or {"monat": 0, "vorjahr": 0}
    return {
        "umsatz_monat": um["monat"], "umsatz_vorjahr": um["vorjahr"],
        "umsatz_veraenderung": round((um["monat"] / um["vorjahr"] - 1) * 100, 1) if um["vorjahr"] else None,
        "offene_rechnungen": round(nicht_faellig + ueberfaellig + gemahnt, 2),
        "or_nicht_faellig": round(nicht_faellig, 2), "or_ueberfaellig": round(ueberfaellig, 2), "or_gemahnt": round(gemahnt, 2),
        "anzahl_gemahnt": sum(1 for r in ops if r["mahnung_am"]),
        "termine_heute": len(san6.get("termine", [])),
        "rezepte_fehlend": len(rezepte), "rezepte_ueber_14": sum(1 for r in rezepte if r["tage"] > 14),
        "hotline_offen": hotline,
    }

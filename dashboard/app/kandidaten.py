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


def baue(san6: dict, outlook: dict, heute: date, kz: Kuerzel, ausgeblendet: set[str]) -> list[dict]:
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
    return out


def kennzahlen(san6: dict, heute: date) -> dict:
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

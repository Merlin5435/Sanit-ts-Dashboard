"""Rezeptprüfung vor der Abrechnung – läuft komplett auf dem eigenen Server, ohne KI.

Neue Rezepte werden mit den Absetzungen der letzten Wochen verglichen. Wurde dasselbe Produkt bei
derselben Diagnose-Gruppe (erste 3 Stellen des ICD-10) mehrfach abgesetzt, gibt es einen Hinweis,
z. B. „Bei dieser Diagnose wurde Rollator 3× abgesetzt – Grund: Genehmigung der Kasse fehlt“.

Diagnosen verlassen dabei nie den Server. An die KI geht nur der fertige Hinweis ohne Diagnose.
"""
from __future__ import annotations

from collections import Counter


def neue_rezepte(auftraege: list[dict], tage: int = 7) -> list[dict]:
    return [a for a in auftraege if a.get("rezepteingang") and a["status"] == "0" and a["tage"] <= tage]


def pruefe(rezepte: list[dict], absetzungen: list[dict], mindestens: int = 2) -> list[dict]:
    out = []
    for r in rezepte:
        gruppe = (r.get("diagnose") or "")[:3]
        treffer = [a for a in absetzungen if a.get("produkt") == r.get("produkt") and (a.get("diagnose") or "")[:3] == gruppe]
        if len(treffer) < mindestens:
            continue
        grund, n = Counter(a["grund"] for a in treffer).most_common(1)[0]
        out.append({**r, "anzahl": len(treffer), "grund": grund, "grund_anzahl": n,
                    "hinweis": f"Bei dieser Diagnose zuletzt {len(treffer)}× abgesetzt – meist: {grund}"})
    return out

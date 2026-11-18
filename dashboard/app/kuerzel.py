"""Datenschutz: Patientendaten durch Kürzel ersetzen, bevor etwas an die KI geht.

- Kundennamen werden zu "K-01", "K-02", … (pro Abgleich fest zugeordnet)
- Versichertennummern, Telefonnummern und Geburtsdaten werden entfernt
- Diagnosen werden gar nicht erst abgeholt
Die Zuordnung Kürzel -> Name bleibt auf dem eigenen Server. Kommt die Antwort der KI zurück,
werden die Kürzel für die Anzeige wieder durch die Namen ersetzt.
"""
from __future__ import annotations

import re

TELEFON = re.compile(r"(?<![\w.,])(?:\+49|0)\d{2,5}[ /-]?\d{3,}(?:[ -]\d+)*")
VERSICHERTENNR = re.compile(r"\b[A-Z]\d{9}\b")
DATUM = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")


class Kuerzel:
    def __init__(self, zuordnung: dict[str, str] | None = None):
        self.name_zu_kz: dict[str, str] = {}
        self.kz_zu_name: dict[str, str] = dict(zuordnung or {})
        for kz, name in self.kz_zu_name.items():
            self.name_zu_kz[name] = kz
        self.geheim: set[str] = set()      # Versichertennummern, Telefone, Geburtsdaten

    def kunde(self, name: str, *geheim: str) -> str:
        name = (name or "").strip()
        for g in geheim:
            if g:
                self.geheim.add(g.strip())
        if not name:
            return "(ohne Kunde)"
        if name not in self.name_zu_kz:
            kz = f"K-{len(self.name_zu_kz) + 1:02d}"
            self.name_zu_kz[name] = kz
            self.kz_zu_name[kz] = name
        return self.name_zu_kz[name]

    def schwaerzen(self, text: str) -> str:
        """Ersetzt alle bekannten Namen durch Kürzel und entfernt Nummern, die auf Personen zeigen."""
        if not text:
            return text
        for name in sorted(self.name_zu_kz, key=len, reverse=True):
            text = text.replace(name, self.name_zu_kz[name])
        for g in self.geheim:
            text = text.replace(g, "[entfernt]")
        text = VERSICHERTENNR.sub("[entfernt]", text)
        text = TELEFON.sub("[Telefon entfernt]", text)
        return text

    def zurueck(self, text: str) -> str:
        """Kürzel in Texten der KI wieder durch die echten Namen ersetzen (nur für die Anzeige)."""
        if not text:
            return text
        return re.sub(r"\bK-\d{2}\b", lambda m: self.kz_zu_name.get(m.group(0), m.group(0)), text)

    def zurueck_tief(self, obj):
        if isinstance(obj, str):
            return self.zurueck(obj)
        if isinstance(obj, list):
            return [self.zurueck_tief(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.zurueck_tief(v) for k, v in obj.items()}
        return obj

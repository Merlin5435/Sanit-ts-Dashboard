"""Anbindung an Claude (Anthropic API): Briefing sortieren, Fragen beantworten, Mail-Antworten entwerfen.

Alles, was hier hinausgeht, ist bereits durch kuerzel.py bereinigt. Jede Anfrage wird mit Inhalt
im KI-Protokoll gespeichert, damit man jederzeit nachsehen kann, was die KI bekommen hat.
"""
from __future__ import annotations

import json
import logging

from .config import Config
from .speicher import Speicher

log = logging.getLogger("dashboard.ki")

BETA_FALLBACK = "server-side-fallback-2026-07-01"

SYSTEM_BRIEFING = """Du bist der Assistent des Geschäftsführers eines Sanitätshauses mit mehreren Filialen.
Jeden Morgen bekommst du eine Liste von Kandidaten (fehlende Rezepte, Wiedervorlagen, Anliegen aus der KI-Telefonhotline,
offene Rechnungen, Lieferrückstände, E-Mails, interne Aufgaben) sowie Kennzahlen und die Termine des Tages.

Deine Aufgabe: Wähle die bis zu 5 Punkte aus, um die sich der Geschäftsführer heute kümmern sollte, und sortiere sie nach
Wichtigkeit. Fasse Zusammengehöriges zu einem Punkt zusammen (z. B. mehrere fehlende Rezepte bei derselben Praxis, mehrere
Hotline-Rückrufe, ein Lieferrückstand und die Mail des Lieferanten dazu). Verbinde Informationen aus verschiedenen Quellen,
wenn sie zusammenpassen – das ist dein eigentlicher Mehrwert.

Wichtig ist, was Geld kostet oder bindet (Abrechnung blockiert, Mahnungen), was Kunden verärgert (Beschwerden, Verzug),
was eine Frist hat und was nur der Geschäftsführer entscheiden kann. Reine Routine, die das Team allein erledigt, und
Newsletter gehören nicht in die Top-Punkte.

Regeln:
- Erfinde nichts. Nenne nur Zahlen, Namen, Daten und Zusammenhänge, die in den Daten stehen.
- Kunden heißen in den Daten K-01, K-02 usw. Verwende genau diese Kürzel, wenn du einen Kunden nennen musst.
- Schreibe auf Deutsch, knapp und konkret, in der Sie-Form an den Geschäftsführer.
- "begruendung": 1–2 Sätze, warum das heute wichtig ist, und ein konkreter nächster Schritt.
- "kandidaten": die IDs aller Kandidaten, die zu dem Punkt gehören (genau wie angegeben).
- "aktion": "antwort_entwerfen", wenn der Punkt im Kern eine E-Mail ist, die beantwortet werden muss, sonst "keine".
- "zusammenfassung": 2–3 Sätze zum Tag, das Wichtigste zuerst.
- "assistent_angebot": ein konkretes Angebot, womit du ihm heute helfen kannst (eine Frage an ihn).
- "vorschlaege": genau 3 kurze Fragen, die er dir zu den heutigen Daten stellen könnte."""

SCHEMA = {
    "type": "object",
    "properties": {
        "zusammenfassung": {"type": "string"},
        "punkte": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "titel": {"type": "string"},
                "begruendung": {"type": "string"},
                "dringlichkeit": {"type": "string", "enum": ["dringend", "heute", "diese Woche"]},
                "kandidaten": {"type": "array", "items": {"type": "string"}},
                "aktion": {"type": "string", "enum": ["keine", "antwort_entwerfen"]},
            },
            "required": ["titel", "begruendung", "dringlichkeit", "kandidaten", "aktion"],
            "additionalProperties": False,
        }},
        "assistent_angebot": {"type": "string"},
        "vorschlaege": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["zusammenfassung", "punkte", "assistent_angebot", "vorschlaege"],
    "additionalProperties": False,
}

SYSTEM_CHAT = """Du bist der Assistent des Geschäftsführers eines Sanitätshauses. Beantworte seine Fragen ausschließlich
anhand der Daten von heute, die unten stehen. Wenn die Daten etwas nicht hergeben, sag das offen und erfinde nichts.
Kunden heißen K-01, K-02 usw.; verwende diese Kürzel. Antworte auf Deutsch, knapp, in der Sie-Form.
Listen und kurze Absätze sind erlaubt, keine Überschriften.

Daten von heute:
"""

SYSTEM_ENTWURF = """Du schreibst Antwortentwürfe auf E-Mails im Namen der Geschäftsführung eines Sanitätshauses.
Höflich, kurz, sachlich, auf Deutsch. Erfinde keine Fakten, Preise oder Termine: wo eine Angabe fehlt, setze einen
Platzhalter in eckigen Klammern, z. B. [Liefertermin]. Gib nur den Text der Mail aus (Anrede bis Gruß), ohne Betreffzeile."""


class KIFehler(Exception):
    pass


class KI:
    def __init__(self, cfg: Config, speicher: Speicher):
        self.cfg = cfg
        self.speicher = speicher
        self._client = None

    @property
    def aktiv(self) -> bool:
        return self.cfg.ki_aktiv

    def _c(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def _anfrage(self, zweck: str, system: str, messages: list[dict], effort: str, max_tokens: int,
                 schema: dict | None = None) -> str:
        output_config: dict = {"effort": effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        try:
            r = self._c().beta.messages.create(
                model=self.cfg.modell, max_tokens=max_tokens, system=system, messages=messages,
                output_config=output_config, betas=[BETA_FALLBACK], fallbacks="default")
        except Exception as e:
            log.error("Claude-Anfrage (%s) fehlgeschlagen: %s", zweck, e)
            raise KIFehler(f"Die KI ist gerade nicht erreichbar ({type(e).__name__}).") from e
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        gesendet = system + "\n\n" + "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
        self.speicher.protokolliere(zweck, getattr(r, "model", self.cfg.modell), gesendet, text,
                                    getattr(r.usage, "input_tokens", 0), getattr(r.usage, "output_tokens", 0))
        if r.stop_reason == "refusal":
            raise KIFehler("Die KI hat diese Anfrage abgelehnt.")
        if r.stop_reason == "max_tokens":
            raise KIFehler("Die Antwort der KI war zu lang und wurde abgeschnitten.")
        return text

    # ------------------------------------------------------------------ Briefing
    def briefing(self, eingabe: dict) -> dict:
        nachricht = ("Hier die Daten von heute (JSON). Kandidaten-IDs bitte exakt übernehmen.\n\n"
                     + json.dumps(eingabe, ensure_ascii=False, indent=1))
        text = self._anfrage("Briefing", SYSTEM_BRIEFING, [{"role": "user", "content": nachricht}],
                             effort="medium", max_tokens=16000, schema=SCHEMA)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise KIFehler("Die Antwort der KI war kein gültiges Format.") from e

    # ------------------------------------------------------------------ Fragen
    def frage(self, kontext: str, verlauf: list[dict], frage: str) -> str:
        msgs = [m for m in verlauf if m.get("role") in ("user", "assistant") and m.get("content")][-10:]
        msgs.append({"role": "user", "content": frage})
        return self._anfrage("Frage", SYSTEM_CHAT + kontext, msgs, effort="low", max_tokens=4000)

    # ------------------------------------------------------------------ Mail-Entwurf
    def entwurf(self, mail_text: str) -> str:
        return self._anfrage("Antwortentwurf", SYSTEM_ENTWURF,
                             [{"role": "user", "content": "Bitte entwirf eine Antwort auf diese Mail:\n\n" + mail_text}],
                             effort="low", max_tokens=2000)

# KI-Dashboard – Testversion

Das Programm holt Daten aus san6 und Microsoft 365, ersetzt Patientendaten durch Kürzel,
lässt Claude die wichtigsten Punkte des Tages auswählen und zeigt sie im Browser.

**Testversion:** Es ist nichts Echtes angeschlossen. san6 und Outlook werden von einer
„Testwelt“ simuliert – einem erfundenen Sanitätshaus mit Kunden, Rezepten, Rechnungen,
Mails und Hotline-Anliegen. Die Testwelt spricht dasselbe Protokoll wie die echten Systeme.

## Starten (Windows, Mac oder Linux)

Voraussetzung: Python 3.10 oder neuer (python.org).

```bash
pip install -r dashboard/requirements.txt
```

Optional, damit die KI wirklich mitdenkt: `dashboard/.env.example` als `dashboard/.env`
kopieren und den Claude-API-Schlüssel bei `ANTHROPIC_API_KEY=` eintragen.

```bash
python -m dashboard
```

Der Befehl wird im Hauptordner des Projekts ausgeführt. Der Browser öffnet sich mit
http://127.0.0.1:8090. Beenden mit Strg+C. Jeder Start beginnt mit einer frischen Testwelt.

## Was man ausprobieren kann

| Knopf | Was passiert |
|---|---|
| **Jetzt abgleichen** | Dasselbe wie morgens um 5 Uhr: Daten holen, Kürzel setzen, KI sortieren lassen |
| **Neuer Tag (Test)** | In der Testwelt vergeht ein Tag: Rezepte gehen ein, neue Mails und Anliegen kommen, Rechnungen werden gemahnt |
| **Erledigt / Später** | Wird gespeichert. Erledigtes taucht nicht mehr auf, Verschobenes erst am nächsten Tag |
| **Details** | Zeigt die einzelnen Vorgänge hinter einem Punkt |
| **Assistent** | Fragen zu den Daten von heute stellen (nur mit API-Schlüssel) |
| **Antwort entwerfen / Entwurf** | Die KI schreibt einen Antwortvorschlag auf eine Mail (wird nicht gesendet) |
| **Was ging an die KI?** | Zeigt genau, was an Claude geschickt wurde – mit Kürzeln statt Namen |

## Datenschutz

- An Claude gehen nur Kürzel (K-01, K-02 …) statt Kundennamen.
- Telefonnummern, Versichertennummern und Geburtsdaten werden entfernt, Diagnosen gar nicht abgeholt.
- Die Zuordnung Kürzel → Name bleibt auf dem Server.
- Ärzte, Krankenkassen, Lieferanten und Mail-Absender (Firmen) bleiben lesbar, weil die KI sie zum Sortieren braucht.

## Tests

```bash
python -m pytest dashboard/tests
```

## Aufbau

```
dashboard/
├── __main__.py        Start, verbindet Testwelt bzw. später echte Systeme
├── app/
│   ├── abholen.py     Daten aus san6 (über das Gateway) und Microsoft 365 holen
│   ├── kuerzel.py     Patientendaten durch Kürzel ersetzen
│   ├── kandidaten.py  Einzelne Vorgänge mit fester ID + Kennzahlen
│   ├── ki.py          Claude: Briefing, Fragen, Antwortentwürfe
│   ├── dienst.py      Ablauf des Abgleichs, 5-Uhr-Zeitplan
│   ├── speicher.py    SQLite: Briefings, Erledigt/Später, KI-Protokoll
│   ├── web.py         Browser-Oberfläche und Schnittstelle
│   └── static/index.html
├── testwelt/          Simuliertes san6 und Microsoft 365
└── tests/
```

## Für die Vollversion fehlt noch

- Anschluss an das echte san6 und Microsoft 365 (Zugänge siehe `fragen-an-chef.html`)
- Betrieb als Dienst auf dem Firmenserver und Programm mit Autostart auf dem PC des Chefs
- Lesezugriff auf Mails in Microsoft 365 (Mail.Read) zusätzlich zur Kalender-Berechtigung

# TopM-Gateway – REST-API für san6 (Controlling & Termine)

Das Gateway ist ein kleiner Dienst, der zwischen Ihren Systemen und dem **TopM-san6-Webservice** sitzt
(Schnittstellenbeschreibung *TG-SAN6-02*, Stand 26.10.2026). Es bietet:

| Bereich | Was es tut | san6-Funktionen |
|---|---|---|
| **Termine** | lesen, anlegen, verschieben, absagen; Online-Terminbuchung (Terminarten, Filialen, freie Slots) | TERMINE, TERMINNEU1, TERMINNEU, TERMINUPDATE, TERMINARTEN, TERMINFILIALEN, TERMINBELEGUNG |
| **Kalender-Sync** | san6-Timer ↔ Outlook/Microsoft 365 in beide Richtungen, iCal-Abo je Mitarbeiter, Änderungsfeed | wie oben |
| **Controlling** | Umsatz (Monat/Woche/Zahlcode/Kostenträger/Artikel, Vorjahresvergleich), offene Posten mit Altersstruktur, offene Abrechnungen, Bestellungen & Lieferrückstände, Lagerbestand, geänderte Aufträge, Orgas, KPI-Übersicht, CSV-Export, tägliches Archiv | RECHNUNGEN, INKASSO1, ABRECHNUNG1, BESTELLUNGEN1, EINKAUF5, ARTIKELBESTAND, AUFTRAEGEGEAENDERT, ANZAHLORGAS, OFFENEORGAS |
| **Kostenrechnung** | Eingangsrechnungen (z.B. aus KI-Erkennung) inkl. PDF/XML-Anlage an san6 übergeben | KOSTENRECHNUNG |

Das Gateway übernimmt URL-Aufbau, **MD5-Authentifizierung**, **TAN** (streng steigend, persistent),
XML ↔ JSON, Datums-/Zahlenformate (`TT.MM.JJJJ`, `238,--`) und Zeilenumbrüche (`%#`).

---

## 1. Vorbereitung im san6

1. Hauptreiter **Vorbereitung → API-Schnittstelle → Neu**: System für das Gateway anlegen
   (Systemnummer + Passwort notieren). Optional „erlaubte IPs“ auf den Gateway-Server beschränken.
2. Für diese Systemnummer die Funktionen **freischalten** (Kap. 3.3.4):
   `PING, STATUS, TERMINE, TERMINNEU, TERMINNEU1, TERMINUPDATE, TERMINARTEN, TERMINFILIALEN, TERMINBELEGUNG,
   RECHNUNGEN, INKASSO1, ABRECHNUNG1, BESTELLUNGEN1, EINKAUF5, ARTIKELBESTAND, AUFTRAEGEGEAENDERT,
   ANZAHLORGAS, OFFENEORGAS, KOSTENRECHNUNG` – nur die, die Sie wirklich nutzen.
3. Für **INKASSO1** in der API-Vorbereitung festlegen, ab wann Rechnungen übertragen werden
   (für Controlling: alle offenen, nicht erst ab Mahnung).
4. Für **ABRECHNUNG1 / KOSTENRECHNUNG** die Adressnummer des Kostenträgers bzw. den Archivtyp bei der Systemnummer hinterlegen.
5. Für **TERMINE** ggf. Sonderfelder / Auftragspositionen / Hilfsmittel aktivieren.

## 2. Installation

```bash
cp .env.example .env        # Werte eintragen (siehe Kommentare)
docker compose up -d --build
curl -H "X-API-Key: <key>" http://localhost:8080/api/v1/system/ping
```

Ohne Docker (Windows-Server im Betrieb geht auch):

```bash
pip install -r requirements.txt
python -m topm_gateway ping            # Zugang zum san6 prüfen
python -m topm_gateway serve           # API + Sync-Worker in einem Prozess (Port 8080)
# Produktion: gunicorn -w 2 -b 0.0.0.0:8080 wsgi:app   +   python -m topm_gateway worker
# Windows:    waitress-serve --port=8080 wsgi:app      +   python -m topm_gateway worker
```

Die interaktive API-Dokumentation liegt unter **`/docs`** (Swagger), die Spezifikation unter `/openapi.yaml`.

> **Wichtig:** Das Gateway hat Zugriff auf Patienten- und Abrechnungsdaten. Nur im internen Netz bzw. hinter
> einem Reverse-Proxy mit HTTPS betreiben, API-Keys pro Anwendung vergeben und Lese-Keys für Auswertungen nutzen.

## 3. Endpunkte (Auszug)

Alle unter `/api/v1`, Header `X-API-Key`. Datumsangaben `2026-11-04` oder `04.11.2026`.
Listen können mit `?format=csv` direkt in Excel geöffnet werden.

```text
GET    /termine?von=&bis=&mitarbeiter=012,013&status=0,2     Termine live aus san6
POST   /termine                                            Termin anlegen
PATCH  /termine/{nummer}                                   verschieben / Status / Infos
DELETE /termine/{nummer}?status=7                          absagen (9 = löschen)
GET    /terminarten · /terminarten/{nr}/filialen · /terminbelegung?terminart=&filiale=
GET    /kalender/012.ics?token=…                           iCal-Abo (Outlook „Kalender aus dem Internet“)

POST   /sync/termine · /sync/controlling                   Sync sofort starten
GET    /sync/status · /sync/aenderungen?seit=0             Protokoll · Delta-Feed

GET    /controlling/kpi?von=&bis=&orga_gruppe=01           Kennzahlen-Übersicht
GET    /controlling/rechnungen?von=&bis=&positionen=1&quelle=live|archiv
GET    /controlling/umsatz?gruppierung=monat|woche|tag|zahlcode|kostentraeger|artikel|status
GET    /controlling/offene-posten?nur_ueberfaellig=1  ·  /controlling/offene-posten/verlauf
GET    /controlling/abrechnungen · /bestellungen · /lieferrueckstaende · /lagerbestand
GET    /controlling/auftraege/aenderungen?fortsetzen=1     inkrementell per Timestamp
GET    /controlling/orgas?gruppe=012&liste=1
POST   /controlling/kostenrechnung
```

Beispiel Termin anlegen:

```bash
curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" http://localhost:8080/api/v1/termine \
  -d '{"terminart":"80","mitarbeiter":"012","start":"2026-11-05T10:00","ende":"2026-11-05T10:30",
       "thema":"Einlagenversorgung","kundennummer":"10006","info":"Kunde ist Diabetiker."}'
```

## 4. Wie der Kalender-Sync arbeitet

Alle `SYNC_INTERVAL_MINUTES` (Standard 15):

1. **san6 → Gateway:** Termine im Fenster *heute − 7 bis heute + 90 Tage* abrufen, mit dem letzten Stand
   vergleichen, Änderungen in den Änderungsfeed schreiben. Fehlt ein Termin, gilt er als gelöscht.
2. **Outlook → san6** (wenn Microsoft 365 konfiguriert): Wird ein übertragener Termin in Outlook verschoben,
   schreibt das Gateway die neue Zeit per TERMINUPDATE zurück; wird er gelöscht, setzt es Status 7 (abgesagt).
   Optional: Outlook-Termine mit der Kategorie `SYNC_OUTLOOK_NEW_CATEGORY` werden in san6 neu angelegt.
3. **san6 → Outlook:** neue Termine anlegen, geänderte aktualisieren, abgesagte/gelöschte entfernen.
   Status *angefragt/optional* erscheint als „mit Vorbehalt“, Notfälle mit hoher Wichtigkeit.

Regeln: Bei Änderungen auf beiden Seiten gewinnt standardmäßig **san6** (`SYNC_CONFLICT_POLICY`).
Serientermine und Termine mit mehreren Mitarbeitern kann TERMINUPDATE nicht ändern (Kap. 4.18.4) –
sie werden nur von san6 nach Outlook übertragen; Änderungen in Outlook werden dort zurückgesetzt.

**Microsoft 365 einrichten:** Azure-Portal → App-Registrierung → API-Berechtigung *Microsoft Graph →
Anwendung → Calendars.ReadWrite* → Administratorzustimmung → Client-Secret anlegen.
Empfehlung: per `New-ApplicationAccessPolicy` auf eine Gruppe mit den betroffenen Postfächern einschränken.
Danach `GRAPH_*` und `SYNC_OUTLOOK_MAP=012=name@…,013=…` in `.env` setzen.

Ohne Microsoft 365 funktionieren REST-API, Änderungsfeed und iCal-Abo trotzdem.

## 5. Offene Punkte zur Abstimmung mit TopM / san6-Betreuer

Die Dokumentation enthält keine XML-Beispiele; folgende Punkte sind konfigurierbar umgesetzt und beim
ersten Test mit `python -m topm_gateway ping` bzw. einem Testtermin zu bestätigen:

- **MD5-Basis:** Das Beispiel in Kap. 3.3.2 hasht ab dem Funktionsnamen (ohne „API-“). Antwortet PING mit
  `02-Ungültige Authentifizierung`, `SAN6_MD5_BASIS=mit_api` testen oder vorübergehend `SAN6_AUTH_MODE=basic`.
- **POST-Format:** Das Gateway sendet `<TerminNeu>…</TerminNeu>` bzw. `<TerminUpdate>`/`<Kostenrechnung>`
  als XML-Wurzel. Erwartet der Webservice eine Hülle, `SAN6_POST_WRAPPER` setzen; Zeichensatz über `SAN6_POST_ENCODING`.
- **Rückgabe von TERMINNEU1:** Liefert san6 keine Terminnummer zurück, verknüpft das Gateway den Termin beim
  nächsten Abruf über Start/Ende.
- Status-Codes bei Rechnungen (`Status`) und Zahlcodes sind betriebsspezifisch – sie werden 1:1 durchgereicht.

## 6. Entwicklung & Tests

```bash
python mock/san6_mock.py 8084          # san6-Simulator (Passwort HALLO)
python -m pytest -q                    # 15 Tests: Auth/TAN, Termine, Outlook-Sync beidseitig, Controlling
```

Aufbau: `san6_client.py` (Aufruf/Auth/TAN) · `san6_api.py` (Funktionen + JSON) · `calendar_sync.py`
(Sync + iCal) · `controlling.py` (Kennzahlen) · `graph_client.py` (Outlook) · `store.py` (SQLite) · `app.py` (REST).

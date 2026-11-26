"""Die Testwelt: ein erfundenes Sanitätshaus mit Kunden, Rezepten, Rechnungen, Mails usw.

Alle Namen, Nummern und Beträge sind ausgedacht. Die Welt "lebt": mit naechster_tag() vergeht
ein Tag – Rezepte gehen ein, neue Mails und Hotline-Anliegen kommen dazu, Rechnungen werden
bezahlt oder gemahnt. So sieht man im Dashboard, dass sich die Prioritäten wirklich ändern.
"""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

CHEF = ("001", "Chef")
MITARBEITER = [("001", "Chef"), ("012", "Krause"), ("013", "Yilmaz"), ("020", "Schäfer"), ("021", "Becker")]
FILIALEN = [("01", "Zentrale"), ("02", "Nord"), ("03", "Süd")]

KUNDEN = [
    ("10006", "Sonja Maier", "A123456781", "24.03.1946", "08234/96521"),
    ("10010", "Hans Huber", "B234567892", "11.07.1951", "0821/556677"),
    ("10014", "Erika Vogt", "C345678903", "02.12.1938", "08231/4411"),
    ("10017", "Peter Lang", "D456789014", "19.05.1960", "0171/2233445"),
    ("10021", "Gisela Ott", "E567890125", "30.09.1944", "08234/7788"),
    ("10025", "Walter Kern", "F678901236", "14.01.1955", "0821/909090"),
    ("10028", "Anna Roth", "G789012347", "08.08.1972", "0160/5544332"),
    ("10031", "Friedrich Bauer", "H890123458", "21.11.1949", "08232/1234"),
    ("10035", "Monika Seitz", "I901234569", "06.06.1966", "0821/313131"),
    ("10039", "Karl Winter", "J012345670", "17.02.1941", "08234/6060"),
    ("10042", "Helga Fuchs", "K123450987", "25.12.1953", "0151/7766554"),
    ("10046", "Josef Brandl", "L234561098", "03.04.1958", "08231/8080"),
]
AERZTE = [("40007", "Praxis Dr. Brandt", "Königsbrunn"), ("40008", "Dr. Okafor", "Augsburg"),
          ("40009", "MVZ Lechfeld", "Schwabmünchen"), ("40011", "Dr. Weiß", "Bobingen")]
KASSEN = ["AOK Bayern", "BARMER", "Techniker Krankenkasse", "DAK-Gesundheit"]
LIEFERANTEN = [("70001", "Medi GmbH", "Kompressionsstrumpf"), ("70002", "Bauerfeind AG", "Bandage"),
               ("70003", "Invacare GmbH", "Rollator"), ("70004", "Ottobock", "Orthese")]
PRODUKTE = [  # (Produkt, Hilfsmittelnummer, typische Diagnose ICD-10)
    ("Kompressionsstrümpfe", "17.06.01.2001", "I83.9"), ("Einlagen", "08.03.01.1000", "M21.4"),
    ("Rollator", "10.50.04.0001", "R26.8"), ("Kniebandage", "05.04.01.0006", "M17.1"),
    ("Pflegebett", "19.40.01.0010", "G35.9"), ("Orthese Handgelenk", "23.03.01.0002", "S62.5"),
]
# Muster, die in den Absetzungen gehäuft auftreten (für die Rezeptprüfung)
ABSETZUNGSGRUENDE = {
    ("Kompressionsstrümpfe", "I83"): "Diagnose begründet die Kompressionsklasse nicht",
    ("Einlagen", "M21"): "Verordnung bei Abgabe älter als 28 Tage",
    ("Rollator", "R26"): "Genehmigung der Kasse fehlt",
}
VERSORGUNGEN = ["Kompressionsstrümpfe", "Einlagen", "Rollator", "Pflegebett", "Inkontinenzversorgung",
                "Kniebandage", "Badewannenlifter", "Rollstuhl", "Orthese Handgelenk"]

MAIL_VORLAGEN = [
    ("Fr. Jansen", "jansen@pflegeheim-stadtpark.de", "Angebot Pflegebetten für 14 Zimmer",
     "Guten Tag, wir benötigen für unseren Neubau 14 Pflegebetten. Können Sie uns bis Ende des Monats liefern? "
     "Bitte schicken Sie uns ein Angebot mit Lieferzeit. Mit freundlichen Grüßen, R. Jansen, Heimleitung", True),
    ("Medi GmbH", "service@medi-beispiel.de", "Neuer Liefertermin Kompressionsware",
     "Sehr geehrte Damen und Herren, aufgrund eines Engpasses verschiebt sich die Lieferung Ihrer Bestellung "
     "auf Kalenderwoche {kw}. Wir bitten um Verständnis.", True),
    ("Steuerbüro Klein", "kanzlei@steuer-klein.de", "Fehlende Unterlagen Q3",
     "Hallo, für den Abschluss Q3 fehlen uns noch die Kassenberichte September der Filiale Süd. "
     "Bitte bis Freitag nachreichen.", True),
    ("AOK Bayern", "hilfsmittel@aok-beispiel.de", "Rückfrage Kostenvoranschlag Rollstuhl",
     "Zum Kostenvoranschlag für {kunde} benötigen wir noch eine ärztliche Begründung. Bitte reichen Sie diese nach.", True),
    ("Newsletter Reha-Messe", "info@rehamesse-beispiel.de", "Jetzt Tickets sichern",
     "Die Reha-Messe öffnet im Frühjahr ihre Tore. Frühbucherrabatt bis Monatsende.", False),
    ("Filiale Nord", "nord@sanitaetshaus-beispiel.de", "Kasse Nord: Wechselgeld knapp",
     "Kurze Info: Wir brauchen bis morgen Wechselgeld, sonst wird es am Samstag eng.", True),
    ("Dr. Okafor", "praxis@okafor-beispiel.de", "Rezept {kunde}",
     "Das angeforderte Rezept für {kunde} geht heute per Post raus.", False),
    ("Bauerfeind AG", "vertrieb@bauerfeind-beispiel.de", "Jahresgespräch Konditionen",
     "Gern würden wir mit Ihnen die Konditionen für das kommende Jahr besprechen. Passt Ihnen ein Termin "
     "in der nächsten oder übernächsten Woche?", True),
    ("IT-Service Lech", "support@it-lech-beispiel.de", "Wartungsfenster Server Samstag",
     "Am Samstag zwischen 18 und 20 Uhr spielen wir Updates auf dem Server ein. San6 ist in dieser Zeit nicht erreichbar.", False),
]
HOTLINE_ANLIEGEN = [
    ("Terminwunsch", "Möchte einen Termin zur Fußvermessung für Einlagen, am liebsten vormittags."),
    ("Terminwunsch", "Bittet um Termin zur Anpassung der Kompressionsstrümpfe."),
    ("Lieferstatus", "Fragt, wann die bestellten Kompressionsstrümpfe kommen. Wartet schon zwei Wochen."),
    ("Reparatur", "Bremse am Rollator greift nicht mehr richtig, bittet um Abholung."),
    ("Reparatur", "Pflegebett lässt sich nicht mehr hochfahren."),
    ("Neukunde", "Neue Kundin, hat ein Rezept für einen Badewannenlifter und fragt nach dem Ablauf."),
    ("Rechnung", "Versteht die Zuzahlung auf der letzten Rechnung nicht, bittet um Rückruf."),
    ("Beschwerde", "Ist unzufrieden, weil der Techniker gestern nicht wie vereinbart gekommen ist."),
]


def d(x: date) -> str:
    return x.strftime("%d.%m.%Y")


@dataclass
class Welt:
    heute: date
    seed: int = 7
    tag_nr: int = 0
    rezepte: list[dict] = field(default_factory=list)
    wiedervorlagen: list[dict] = field(default_factory=list)
    orgas: list[dict] = field(default_factory=list)
    op: list[dict] = field(default_factory=list)
    rechnungen: list[dict] = field(default_factory=list)
    rueckstaende: list[dict] = field(default_factory=list)
    termine: list[dict] = field(default_factory=list)
    mails: list[dict] = field(default_factory=list)
    outlook_termine: list[dict] = field(default_factory=list)
    auftraege: list[dict] = field(default_factory=list)      # KVs, offene Aufträge, Mahnstufen, neue Rezepte
    abrechnungen: list[dict] = field(default_factory=list)   # offene Abrechnungen (ABRECHNUNG1)
    absetzungen: list[dict] = field(default_factory=list)    # Absetzungen der Kassen (Export, Quelle offen)
    abwesend: list[dict] = field(default_factory=list)       # Urlaub / Krank (als Termine im Timer)
    telefonie: dict = field(default_factory=dict)            # Statistik vom Hotline-Anbieter (Quelle offen)
    _n: int = 100

    def __post_init__(self):
        self.lock = threading.RLock()
        self.rnd = random.Random(self.seed)
        self._start()

    # ------------------------------------------------------------------ Hilfen
    def nr(self) -> int:
        self._n += 1
        return self._n

    def kunde(self):
        return self.rnd.choice(KUNDEN)

    # ------------------------------------------------------------------ Aufbau
    def _start(self) -> None:
        t, r = self.heute, self.rnd
        # fehlende Rezepte: 4 bei Dr. Brandt, Rest verteilt
        for i in range(11):
            arzt = AERZTE[0] if i < 4 else r.choice(AERZTE[1:])
            self._neues_rezept(t - timedelta(days=r.choice([3, 6, 9, 15, 17, 19, 22, 26])), arzt)
        for _ in range(3):
            self._neue_wiedervorlage(t, "Rezept fehlt")
        self._neue_wiedervorlage(t - timedelta(days=2), "Genehmigung der Kasse nachfassen")
        self._neue_wiedervorlage(t + timedelta(days=3), "Kunde wegen Anpassung anrufen")
        for _ in range(5):
            self._neues_hotline_anliegen(t - timedelta(days=1))
        self.orgas.append(dict(nr=f"{self.nr():05d}", an="001", von="020", datum=t - timedelta(days=1),
                               thema="Urlaubsantrag Dezember", text="Bitte um Freigabe meines Urlaubs 22.12.–02.01.",
                               kunde=None, quelle="intern"))
        for i in range(14):
            self._neue_op(t - timedelta(days=r.randint(5, 80)))
        self._umsatz_aufbauen()
        self.rueckstaende = [
            dict(bestellung="B00412", lieferant=LIEFERANTEN[0], artikel="Kompressionsstrumpf AD Kl. 2", bestellt=12,
                 offen=12, datum=t - timedelta(days=21), liefertermin=t - timedelta(days=4)),
            dict(bestellung="B00419", lieferant=LIEFERANTEN[2], artikel="Rollator Banjo", bestellt=3, offen=1,
                 datum=t - timedelta(days=12), liefertermin=t + timedelta(days=2)),
        ]
        for m in MAIL_VORLAGEN[:5]:
            self._neue_mail(m, datetime.combine(t - timedelta(days=r.randint(0, 2)), datetime.min.time())
                            + timedelta(hours=r.randint(7, 18)))
        self._termine_fuer(t)
        self._neue_bereiche()

    # ------------------------------------------------------------------ Wünsche des Chefs (Nov. 2026)
    def _auftrag(self, status: str, datum: date, **extra) -> dict:
        k = self.kunde()
        prod, hmv, icd = self.rnd.choice(PRODUKTE)
        a = dict(nr=f"{self.nr():06d}", status=status, datum=datum, kunde=k, kasse=self.rnd.choice(KASSEN),
                 produkt=prod, hmv=hmv, diagnose=icd, betrag=round(self.rnd.choice([89.0, 145.5, 238.0, 412.0, 980.0, 1890.0]), 2),
                 filiale=self.rnd.choice(FILIALEN), rezepteingang=None, rezept_scan=True)
        a.update(extra)
        self.auftraege.append(a)
        return a

    def _neue_absetzung(self, datum: date) -> None:
        prod, hmv, icd = self.rnd.choice(PRODUKTE)
        grund = ABSETZUNGSGRUENDE.get((prod, icd[:3])) or self.rnd.choice(
            ["Zuzahlung falsch berechnet", "Unterschrift fehlt", "Abgabedatum fehlt"])
        self.absetzungen.append(dict(datum=datum, beleg=f"B{self.nr():05d}", kasse=self.rnd.choice(KASSEN),
                                     produkt=prod, hmv=hmv, diagnose=icd, grund=grund,
                                     betrag=round(self.rnd.choice([45.0, 89.9, 145.5, 238.0]), 2)))

    def _neue_bereiche(self) -> None:
        t, r = self.heute, self.rnd
        for tage in (4, 9, 16, 23, 25, 31, 38, 44, 52):                     # eingereichte KVs
            self._auftrag("1", t - timedelta(days=tage))
        for _ in range(12):                                                   # offene Aufträge
            self._auftrag("0", t - timedelta(days=r.randint(0, 20)))
        for st in ("6", "6", "7", "7"):                                       # 3. und 4. Mahnung
            self._auftrag(st, t - timedelta(days=r.randint(70, 140)))
        for prod_icd in [("Kompressionsstrümpfe", "I83.9"), ("Einlagen", "M21.4"), ("Rollator", "R26.8"),
                         ("Kniebandage", "M17.1"), ("Kompressionsstrümpfe", "I87.2")]:  # neue Rezepte
            self._neues_rezept_eingang(t - timedelta(days=r.randint(0, 3)), *prod_icd)
        for _ in range(16):                                                   # Absetzungen der letzten 3 Wochen
            self._neue_absetzung(t - timedelta(days=r.randint(1, 21)))
        for (p, d), g in ABSETZUNGSGRUENDE.items():                           # klare Muster für die Prüfung
            for _ in range(3):
                hmv = next(x[1] for x in PRODUKTE if x[0] == p)
                self.absetzungen.append(dict(datum=t - timedelta(days=r.randint(1, 21)), beleg=f"B{self.nr():05d}",
                                             kasse=r.choice(KASSEN), produkt=p, hmv=hmv, diagnose=d + ".9", grund=g,
                                             betrag=round(r.choice([89.9, 145.5]), 2)))
        self.abrechnungen = [dict(beleg=f"{self.nr():06d}-AB12CD", ik="108310400", kasse=k, datum=t - timedelta(days=r.randint(1, 12)),
                                  summe=round(r.uniform(2500, 18000), 2), status=r.choice(["0", "0", "1"]), verordnungen=[])
                             for k in KASSEN[:3]]
        self.abrechnungen[0]["status"] = "0"
        # gelieferte Rezept-Aufträge (Status 2 = Lieferschein): teils in einer Abrechnung, teils noch nicht
        for i in range(9):
            a = self._auftrag("2", t - timedelta(days=r.randint(4, 30)), rezepteingang=t - timedelta(days=r.randint(20, 40)),
                              rezept_scan=(i not in (1, 4)))
            if i < 6:
                self.abrechnungen[i % 2]["verordnungen"].append(a["nr"])
        self._abwesenheiten()
        self._telefonie()

    def _neues_rezept_eingang(self, eingang: date, prod: str, icd: str) -> None:
        hmv = next(x[1] for x in PRODUKTE if x[0] == prod)
        self._auftrag("0", eingang, rezepteingang=eingang, produkt=prod, hmv=hmv, diagnose=icd)

    def _abwesenheiten(self) -> None:
        r, t = self.rnd, self.heute
        ma = r.sample(MITARBEITER[1:], k=2)
        self.abwesend = [dict(ma=ma[0], art="Urlaub", von=t - timedelta(days=2), bis=t + timedelta(days=r.randint(1, 6))),
                         dict(ma=ma[1], art="Krank", von=t, bis=t + timedelta(days=r.randint(0, 2)))]
        if r.random() < 0.4:
            self.abwesend.pop()

    def _telefonie(self) -> None:
        r = self.rnd
        n = r.randint(28, 46)
        arten = {"Terminwunsch": 0, "Lieferstatus": 0, "Rezept / Rechnung": 0, "Reparatur": 0, "Sonstiges": 0}
        for _ in range(n):
            arten[r.choices(list(arten), weights=[5, 4, 3, 2, 2])[0]] += 1
        self.telefonie = dict(datum=self.heute - timedelta(days=1), anrufe=n, geloest=int(n * r.uniform(0.62, 0.8)),
                              weitergeleitet=r.randint(3, 8), zufriedenheit=round(r.uniform(3.6, 4.6), 1),
                              unzufrieden=r.randint(0, 4), dauer_min=round(r.uniform(1.8, 3.4), 1), arten=arten)

    def _neues_rezept(self, anfrage: date, arzt=None) -> None:
        k = self.kunde()
        arzt = arzt or self.rnd.choice(AERZTE)
        self.rezepte.append(dict(auftrag=f"{self.nr():06d}", pruef="A1B2C3", anfrage=anfrage,
                                 stichwort=self.rnd.choice(VERSORGUNGEN), kunde=k, kasse=self.rnd.choice(KASSEN),
                                 arzt=arzt))

    def _neue_wiedervorlage(self, datum: date, bemerkung: str) -> None:
        self.wiedervorlagen.append(dict(nr=f"{self.nr():06d}", datum=datum, typ="Auftrag",
                                        vorgang=f"A{self.nr():05d}", bemerkung=bemerkung))

    def _neues_hotline_anliegen(self, datum: date) -> None:
        k = self.kunde()
        art, text = self.rnd.choice(HOTLINE_ANLIEGEN)
        self.orgas.append(dict(nr=f"{self.nr():05d}", an="00", von="KI", datum=datum,
                               thema=f"Hotline: {art}", text=f"{k[1]} (Tel. {k[4]}) – {text}",
                               kunde=k, quelle="hotline", art=art))

    def _neue_op(self, rechnungsdatum: date) -> None:
        k = self.kunde()
        betrag = round(self.rnd.choice([24.5, 45.0, 89.9, 119.0, 238.0, 412.5, 690.0]), 2)
        ziel = rechnungsdatum + timedelta(days=14)
        mahnung = ziel + timedelta(days=14) if (self.heute - ziel).days > 14 else None
        self.op.append(dict(rnr=f"25{self.nr():04d}", auftrag=f"{self.nr():06d}", datum=rechnungsdatum,
                            ziel=ziel, mahnung=mahnung, betrag=betrag, offen=betrag, kunde=k,
                            typ=self.rnd.choice(["Standard", "Eigenanteil"])))

    def _umsatz_aufbauen(self) -> None:
        """Rechnungen dieses und letzten Monats sowie des Vorjahresmonats (für Umsatz und Vergleich)."""
        r, t = self.rnd, self.heute
        start = (t.replace(day=1) - timedelta(days=1)).replace(day=1)
        tage = [start + timedelta(days=i) for i in range((t - start).days)]
        tage += [x.replace(year=x.year - 1) for x in tage if not (x.month == 2 and x.day == 29)]
        for tag in tage:
            if tag.weekday() >= 5:
                continue
            for _ in range(r.randint(10, 18)):
                self._rechnung(tag)

    def _rechnung(self, tag: date) -> None:
        netto = round(self.rnd.uniform(25, 900), 2)
        self.rechnungen.append(dict(rnr=f"29{self.nr():05d}", datum=tag, netto=netto, brutto=round(netto * 1.19, 2),
                                    zahlcode=self.rnd.choice(["01", "01", "02"]),
                                    konto=self.rnd.choice(KASSEN + ["Privat"])))

    def _neue_mail(self, vorlage, zeit: datetime) -> None:
        name, adr, betreff, text, antwort = vorlage
        k = self.kunde()
        kw = (self.heute + timedelta(days=10)).isocalendar()[1]
        self.mails.append(dict(id=f"m{self.nr()}", von=name, adresse=adr, betreff=betreff.format(kunde=k[1], kw=kw),
                               text=text.format(kunde=k[1], kw=kw), zeit=zeit, gelesen=False, antwort_noetig=antwort))

    def _termine_fuer(self, t: date) -> None:
        r = self.rnd
        self.termine = [x for x in self.termine if x["datum"] >= t - timedelta(days=1)]
        if any(x["datum"] == t for x in self.termine):
            return
        for ma, _ in MITARBEITER[1:]:
            for h in r.sample([8, 9, 10, 11, 13, 14, 15, 16], k=r.randint(3, 6)):
                k = self.kunde()
                self.termine.append(dict(nr=f"{self.nr():08d}-0001", datum=t, zeit=f"{h:02d}:00", bis=f"{h:02d}:45",
                                         ma=ma, thema=r.choice(VERSORGUNGEN), kunde=k, status="0",
                                         filiale=r.choice(FILIALEN)))
        k = self.kunde()
        self.termine.append(dict(nr=f"{self.nr():08d}-0001", datum=t, zeit="13:30", bis="14:15", ma="001",
                                 thema="Versorgungsabstimmung Orthopädie", kunde=None, status="0",
                                 filiale=FILIALEN[0], extern="Hr. Peters, Orthopädie Nord"))
        self.outlook_termine = [
            dict(id=f"ev{self.nr()}", betreff="Strategie Filialleitung", start=datetime.combine(t, datetime.min.time()) + timedelta(hours=11),
                 dauer=60, ort="Besprechungsraum"),
            dict(id=f"ev{self.nr()}", betreff="Steuerberater", start=datetime.combine(t, datetime.min.time()) + timedelta(hours=15),
                 dauer=45, ort="Teams"),
        ]

    # ------------------------------------------------------------------ Ein Tag vergeht
    def naechster_tag(self) -> None:
        with self.lock:
            r = self.rnd
            self.heute += timedelta(days=1)
            while self.heute.weekday() >= 5:
                self.heute += timedelta(days=1)
            self.tag_nr += 1
            t = self.heute
            # Rezepte gehen ein, neue Anforderungen kommen
            self.rezepte = [x for x in self.rezepte if r.random() > 0.3]
            for _ in range(r.randint(1, 3)):
                self._neues_rezept(t - timedelta(days=1))
            # Wiedervorlagen
            self.wiedervorlagen = [x for x in self.wiedervorlagen if x["datum"] >= t - timedelta(days=3) and r.random() > 0.4]
            for _ in range(r.randint(1, 3)):
                self._neue_wiedervorlage(t, r.choice(["Rezept fehlt", "Genehmigung der Kasse nachfassen",
                                                      "Kunde wegen Anpassung anrufen"]))
            # Hotline: alte Anliegen teils erledigt, neue vom Vortag
            self.orgas = [x for x in self.orgas if x["quelle"] != "hotline" or r.random() > 0.6]
            for _ in range(r.randint(2, 6)):
                self._neues_hotline_anliegen(t - timedelta(days=1))
            # Rechnungen: einige bezahlt, einige gemahnt, neue offen
            for x in self.op:
                if r.random() < 0.15:
                    x["offen"] = 0
                elif x["mahnung"] is None and (t - x["ziel"]).days > 14:
                    x["mahnung"] = t
            self.op = [x for x in self.op if x["offen"] > 0]
            for _ in range(r.randint(1, 3)):
                self._neue_op(t - timedelta(days=1))
            for _ in range(r.randint(10, 18)):
                self._rechnung(t - timedelta(days=1))
            # Lieferrückstände: teils geliefert
            for x in self.rueckstaende:
                if r.random() < 0.3:
                    x["offen"] = max(0, x["offen"] - r.randint(1, x["offen"] or 1))
            self.rueckstaende = [x for x in self.rueckstaende if x["offen"] > 0]
            if r.random() < 0.5:
                l = r.choice(LIEFERANTEN)
                self.rueckstaende.append(dict(bestellung=f"B{self.nr():05d}", lieferant=l, artikel=l[2], bestellt=6,
                                              offen=6, datum=t - timedelta(days=14), liefertermin=t - timedelta(days=1)))
            # Mails: alte gelesen/beantwortet, neue kommen
            for m in self.mails:
                if r.random() < 0.5:
                    m["gelesen"] = True
                    m["antwort_noetig"] = False
            for v in r.sample(MAIL_VORLAGEN, k=r.randint(2, 4)):
                self._neue_mail(v, datetime.combine(t - timedelta(days=1), datetime.min.time()) + timedelta(hours=r.randint(8, 19)))
            self._termine_fuer(t)
            # KVs: teils genehmigt (-> offen), neue kommen dazu
            for a in self.auftraege:
                if a["status"] == "1" and r.random() < 0.2:
                    a["status"] = "0"
                elif a["status"] == "6" and r.random() < 0.15:
                    a["status"] = "7"
                elif a["status"] in ("6", "7") and r.random() < 0.15:
                    a["status"] = "8"
                elif a["status"] == "0" and r.random() < 0.15:
                    a["status"] = "2"
            self.auftraege = [a for a in self.auftraege if a["status"] in ("0", "1", "2", "6", "7")]
            for _ in range(r.randint(0, 2)):
                self._auftrag("2", t - timedelta(days=1), rezepteingang=t - timedelta(days=r.randint(10, 30)),
                              rezept_scan=r.random() > 0.25)
            for _ in range(r.randint(0, 2)):
                self._auftrag("1", t - timedelta(days=1))
            for _ in range(r.randint(1, 3)):
                self._auftrag("0", t - timedelta(days=1))
            for _ in range(r.randint(1, 3)):
                p, _, icd = r.choice(PRODUKTE)
                self._neues_rezept_eingang(t - timedelta(days=1), p, icd)
            self.absetzungen = [x for x in self.absetzungen if (t - x["datum"]).days <= 21]
            for _ in range(r.randint(0, 3)):
                self._neue_absetzung(t - timedelta(days=1))
            for a in self.abrechnungen:
                if r.random() < 0.3:
                    a["status"] = "1"
            self._abwesenheiten()
            self._telefonie()

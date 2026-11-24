"""Simulierte Zusatzquellen, deren echter Weg noch offen ist:

- Telefonie-Statistik des KI-Hotline-Anbieters (Anrufe, Zufriedenheit) – san6 kennt diese Zahlen nicht.
- Absetzungen der Kassen – die san6-API nimmt sie nur an (ABRECHNUNG4), gibt sie aber nicht aus.
  Denkbar: Export der Abrechnungsstelle oder aus san6.

Beide liefern hier einfaches JSON. Wenn klar ist, woher die echten Daten kommen, wird nur diese Stelle ersetzt.
"""
from __future__ import annotations

from flask import Flask, jsonify

from .welt import Welt


def create_app(welt: Welt) -> Flask:
    app = Flask("anbieter_sim")

    @app.get("/telefonie/gestern")
    def telefonie():
        with welt.lock:
            t = dict(welt.telefonie)
            t["datum"] = t["datum"].isoformat()
            return jsonify(t)

    @app.get("/abrechnung/absetzungen")
    def absetzungen():
        with welt.lock:
            return jsonify([{**x, "datum": x["datum"].isoformat()} for x in welt.absetzungen])

    return app

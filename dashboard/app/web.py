"""Web-Oberfläche und JSON-Schnittstelle des Dashboards (läuft auf dem Server, Aufruf im Browser)."""
from __future__ import annotations

import os

from flask import Flask, jsonify, request, send_from_directory

from .dienst import Dienst
from .ki import KIFehler

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def create_app(dienst: Dienst) -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/api/briefing")
    def briefing():
        b = dienst.speicher.letztes_briefing()
        if not b:
            b = dienst.abgleich()
        b = {k: v for k, v in b.items() if k not in ("kontext_ki", "zuordnung")}
        b["testversion"] = dienst.welt is not None
        b["ki_verbunden"] = dienst.ki.aktiv
        return jsonify(b)

    @app.post("/api/abgleich")
    def abgleich():
        dienst.abgleich()
        return jsonify({"ok": True})

    @app.post("/api/neuer-tag")
    def neuer_tag():
        if not dienst.welt:
            return jsonify({"fehler": "Nur in der Testversion."}), 400
        dienst.neuer_tag()
        return jsonify({"ok": True})

    @app.post("/api/zustand")
    def zustand():
        j = request.get_json(force=True)
        z = j.get("zustand")
        if z not in ("erledigt", "spaeter", "offen") or not j.get("kandidaten"):
            return jsonify({"fehler": "ungültig"}), 400
        dienst.setze(list(j["kandidaten"]), z)
        return jsonify({"ok": True})

    @app.post("/api/frage")
    def frage():
        j = request.get_json(force=True)
        try:
            return jsonify({"antwort": dienst.frage(j.get("frage", "")[:2000], j.get("verlauf", []))})
        except KIFehler as e:
            return jsonify({"fehler": str(e)}), 503

    @app.post("/api/entwurf/<mail_id>")
    def entwurf(mail_id: str):
        try:
            return jsonify({"entwurf": dienst.entwurf(mail_id)})
        except KIFehler as e:
            return jsonify({"fehler": str(e)}), 503

    @app.get("/api/ki-protokoll")
    def protokoll():
        return jsonify(dienst.speicher.protokoll(20))

    return app

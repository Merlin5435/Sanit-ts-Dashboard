"""Simuliertes Microsoft 365 (Graph-API): Anmeldung, Postfach und Kalender des Chefs.

Spricht dieselben Adressen und dasselbe JSON-Format wie graph.microsoft.com,
damit das Programm später ohne Änderung auf das echte Microsoft 365 umgestellt werden kann.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Flask, jsonify, request

from .welt import Welt


def create_app(welt: Welt) -> Flask:
    app = Flask("graph_sim")

    @app.post("/<tenant>/oauth2/v2.0/token")
    def token(tenant: str):
        return jsonify({"access_token": "testwelt-token", "expires_in": 3600})

    @app.get("/v1.0/users/<mbx>/mailFolders/inbox/messages")
    def messages(mbx: str):
        with welt.lock:
            mails = sorted(welt.mails, key=lambda m: m["zeit"], reverse=True)[: int(request.args.get("$top", 50))]
            return jsonify({"value": [{
                "id": m["id"], "subject": m["betreff"], "bodyPreview": m["text"][:250],
                "body": {"contentType": "text", "content": m["text"]},
                "from": {"emailAddress": {"name": m["von"], "address": m["adresse"]}},
                "receivedDateTime": m["zeit"].strftime("%Y-%m-%dT%H:%M:%SZ"), "isRead": m["gelesen"],
                "flag": {"flagStatus": "flagged" if m["antwort_noetig"] else "notFlagged"},
            } for m in mails]})

    @app.get("/v1.0/users/<mbx>/calendarView")
    def calendar(mbx: str):
        s = datetime.fromisoformat(request.args["startDateTime"][:19])
        e = datetime.fromisoformat(request.args["endDateTime"][:19])
        with welt.lock:
            out = []
            for ev in welt.outlook_termine:
                end = ev["start"] + timedelta(minutes=ev["dauer"])
                if ev["start"] < e and end > s:
                    out.append({"id": ev["id"], "subject": ev["betreff"], "location": {"displayName": ev["ort"]},
                                "start": {"dateTime": ev["start"].strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "Europe/Berlin"},
                                "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "Europe/Berlin"}})
            return jsonify({"value": out})

    return app

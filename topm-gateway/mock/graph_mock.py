"""Sehr kleiner Microsoft-Graph-Mock (Token + Kalender-Events) für Tests."""
from __future__ import annotations

import uuid

from flask import Flask, jsonify, request

app = Flask(__name__)
EVENTS: dict[str, dict[str, dict]] = {}


def reset() -> None:
    EVENTS.clear()


@app.post("/<tenant>/oauth2/v2.0/token")
def token(tenant: str):
    return jsonify({"access_token": "mock-token", "expires_in": 3600})


def _mbx(m: str) -> dict[str, dict]:
    return EVENTS.setdefault(m, {})


@app.post("/v1.0/users/<mbx>/events")
def create(mbx: str):
    ev = request.get_json()
    ev["id"] = "ev-" + uuid.uuid4().hex[:8]
    ev["start"]["dateTime"] = ev["start"]["dateTime"][:19] + ".0000000"
    ev["end"]["dateTime"] = ev["end"]["dateTime"][:19] + ".0000000"
    _mbx(mbx)[ev["id"]] = ev
    return jsonify(ev), 201


@app.route("/v1.0/users/<mbx>/events/<eid>", methods=["GET", "PATCH", "DELETE"])
def one(mbx: str, eid: str):
    ev = _mbx(mbx).get(eid)
    if ev is None:
        return jsonify({"error": {"code": "ErrorItemNotFound"}}), 404
    if request.method == "DELETE":
        del _mbx(mbx)[eid]
        return "", 204
    if request.method == "PATCH":
        ev.update(request.get_json())
    return jsonify(ev)


@app.get("/v1.0/users/<mbx>/calendarView")
def view(mbx: str):
    s, e = request.args["startDateTime"][:16], request.args["endDateTime"][:16]
    out = [ev for ev in _mbx(mbx).values() if ev["start"]["dateTime"][:16] < e and ev["end"]["dateTime"][:16] > s]
    return jsonify({"value": out})

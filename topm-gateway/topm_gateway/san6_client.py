"""Client für die TopM-san6-API (TG-SAN6-02).

Umsetzung der Dokumentation:
* Kap. 3.2  Aufruf per HTTP-GET (Abruf) bzw. HTTP-POST (Übertragung, XML im POST-Anteil)
* Kap. 3.3.2 Authentifizierung: Basismodus (Passwort) oder MD5-Modus
             MD5 über den Aufruf ab "API-" (ohne "API-") + Passwort, ohne abschließendes "&"
* Kap. 3.3.3 TAN: <GeräteId>-<8stellig fortlaufend>, streng steigend pro Gerät
* Kap. 3.6  URL-Aufbau: company, product, mandant, ssid=nosession, sys, (mitarbeiter), (tan), authentifizierung
* Rückgabe: XML mit <Result><Code>..</Code><Text>..</Text></Result>; "00" = OK
"""
from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from typing import Any, Callable
from urllib.parse import quote

import requests

from .config import San6Config
from .xmlutil import build_xml, child_text, find_first, parse_xml

log = logging.getLogger("topm.san6")

# allgemeine Fehlercodes (gelten für alle Funktionen)
GENERIC_CODES = {
    "01": "Unvollständige Parameter",
    "02": "Ungültige Authentifizierung",
    "03": "Ungültiger Funktionsaufruf (Funktion im san6 für dieses System nicht freigeschaltet?)",
}


class San6Error(Exception):
    def __init__(self, funktion: str, code: str, text: str, http_status: int | None = None):
        self.funktion = funktion
        self.code = code
        self.text = text
        self.http_status = http_status
        super().__init__(f"san6 {funktion}: {code}-{text}")

    def to_dict(self) -> dict:
        return {"funktion": self.funktion, "code": self.code, "text": self.text}


class San6Response:
    def __init__(self, funktion: str, root: ET.Element, raw: bytes):
        self.funktion = funktion
        self.root = root
        self.raw = raw
        res = find_first(root, "Result")
        self.code = child_text(res, "Code") if res is not None else ""
        self.text = child_text(res, "Text") if res is not None else ""

    @property
    def ok(self) -> bool:
        return self.code == "00"


def _enc(v: Any) -> str:
    # san6 erwartet Parameter im Klartext; nur Zeichen kodieren, die die URL zerstören würden
    return quote(str(v), safe=".,;:-_*/@")


class San6Client:
    def __init__(self, cfg: San6Config, tan_provider: Callable[[str], str] | None = None,
                 session: requests.Session | None = None):
        self.cfg = cfg
        self.tan_provider = tan_provider
        self.http = session or requests.Session()

    # ------------------------------------------------------------ URL & Auth
    def build_url(self, funktion: str, params: dict[str, Any] | None = None) -> str:
        funktion = funktion.upper()
        if funktion.startswith("API-"):
            funktion = funktion[4:]
        c = self.cfg
        pairs: list[tuple[str, str]] = [
            ("funktion", f"API-{funktion}"),
            ("company", c.company),
            ("product", c.product),
            ("mandant", c.mandant),
            ("ssid", "nosession"),
            ("sys", c.sys),
        ]
        if c.mitarbeiter:
            pairs.append(("mitarbeiter", c.mitarbeiter))
        if c.tan_device and self.tan_provider:
            pairs.append(("tan", self.tan_provider(c.tan_device)))
        for k, v in (params or {}).items():
            if v is None or v == "":
                continue
            pairs.append((k, str(v)))

        query = "&".join(f"{k}={_enc(v)}" for k, v in pairs)
        if c.auth_mode == "md5":
            # Kap. 3.3.2: String ab "API-" (Beispiel beginnt mit dem Funktionsnamen) + Passwort
            cut = len("funktion=") if c.md5_basis == "mit_api" else len("funktion=API-")
            basis = query[cut:].rstrip("&") + c.password
            auth = hashlib.md5(basis.encode("utf-8")).hexdigest()
        else:
            auth = _enc(c.password)
        return f"{c.base_url}?{query}&authentifizierung={auth}"

    # ------------------------------------------------------------ Aufrufe
    def _handle(self, funktion: str, resp: requests.Response, ok_codes: tuple[str, ...]) -> San6Response:
        if resp.status_code >= 400:
            raise San6Error(funktion, f"HTTP{resp.status_code}", resp.text[:300], resp.status_code)
        try:
            root = parse_xml(resp.content)
        except ET.ParseError as e:
            raise San6Error(funktion, "XML", f"Antwort ist kein gültiges XML: {e}; Anfang: {resp.text[:200]!r}")
        r = San6Response(funktion, root, resp.content)
        if r.code not in ("00",) + ok_codes:
            text = r.text or GENERIC_CODES.get(r.code, "unbekannter Fehler")
            raise San6Error(funktion, r.code or "??", text)
        return r

    def get(self, funktion: str, params: dict[str, Any] | None = None,
            ok_codes: tuple[str, ...] = ()) -> San6Response:
        url = self.build_url(funktion, params)
        log.debug("GET %s", url.split("&authentifizierung=")[0])
        try:
            resp = self.http.get(url, timeout=self.cfg.timeout, verify=self.cfg.verify_tls)
        except requests.RequestException as e:
            raise San6Error(funktion, "NET", f"san6 nicht erreichbar: {e}")
        return self._handle(funktion, resp, ok_codes)

    def post(self, funktion: str, root_tag: str, data: dict, params: dict[str, Any] | None = None,
             ok_codes: tuple[str, ...] = ()) -> San6Response:
        url = self.build_url(funktion, params)
        body = build_xml(root_tag, data, self.cfg.post_encoding, self.cfg.post_wrapper)
        log.debug("POST %s (%d Bytes)", url.split("&authentifizierung=")[0], len(body))
        try:
            resp = self.http.post(url, data=body, timeout=self.cfg.timeout, verify=self.cfg.verify_tls,
                                  headers={"Content-Type": f"text/xml; charset={self.cfg.post_encoding}"})
        except requests.RequestException as e:
            raise San6Error(funktion, "NET", f"san6 nicht erreichbar: {e}")
        return self._handle(funktion, resp, ok_codes)

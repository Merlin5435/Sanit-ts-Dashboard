"""XML-Helfer: san6 liefert XML, das Gateway spricht JSON.

* ``xml_to_obj``   – wandelt ein Element generisch in dict/list/str um
                     (wiederholte Tags werden zu Listen)
* ``build_xml``    – baut aus einem dict ein XML-Dokument für POST-Funktionen
* Datums-/Zahlen-Parser für das san6-Format (TT.MM.JJJJ, "51.84", "238,--", "53,77")
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, time
from typing import Any
from xml.sax.saxutils import escape

LINEBREAK = "%#"  # san6 ersetzt Zeilenumbrüche durch %# (Kap. 3.7)


def parse_xml(raw: bytes | str) -> ET.Element:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    raw = raw.strip()
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        # ohne XML-Deklaration kommt das san6 häufig in Windows-1252
        text = raw.decode("cp1252", errors="replace")
        text = re.sub(r"^<\?xml[^>]*\?>", "", text).strip()
        return ET.fromstring(text)


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def norm_text(v: str | None) -> str:
    if v is None:
        return ""
    return v.strip().replace(LINEBREAK, "\n")


def xml_to_obj(el: ET.Element) -> Any:
    children = list(el)
    if not children:
        return norm_text(el.text)
    out: dict[str, Any] = {}
    for c in children:
        key = _local(c.tag)
        val = xml_to_obj(c)
        if key in out:
            if not isinstance(out[key], list):
                out[key] = [out[key]]
            out[key].append(val)
        else:
            out[key] = val
    return out


def find_first(el: ET.Element, name: str) -> ET.Element | None:
    lname = name.lower()
    for e in el.iter():
        if _local(e.tag).lower() == lname:
            return e
    return None


def find_all(el: ET.Element, name: str) -> list[ET.Element]:
    lname = name.lower()
    return [e for e in el.iter() if _local(e.tag).lower() == lname]


def child_text(el: ET.Element, name: str, default: str = "") -> str:
    lname = name.lower()
    for c in el:
        if _local(c.tag).lower() == lname:
            return norm_text(c.text) if len(c) == 0 else default
    return default


def child(el: ET.Element, name: str) -> ET.Element | None:
    lname = name.lower()
    for c in el:
        if _local(c.tag).lower() == lname:
            return c
    return None


def as_list(v: Any) -> list:
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


# ---------------------------------------------------------------- XML bauen
def _emit(name: str, value: Any, parts: list[str], indent: int) -> None:
    pad = "  " * indent
    if value is None:
        return
    if isinstance(value, list):
        for v in value:
            _emit(name, v, parts, indent)
        return
    if isinstance(value, dict):
        parts.append(f"{pad}<{name}>")
        for k, v in value.items():
            _emit(k, v, parts, indent + 1)
        parts.append(f"{pad}</{name}>")
        return
    if isinstance(value, bool):
        value = "J" if value else "N"
    text = str(value).replace("\r\n", "\n").replace("\n", LINEBREAK)
    parts.append(f"{pad}<{name}>{escape(text)}</{name}>")


def build_xml(root: str, data: dict, encoding: str = "utf-8", wrapper: str = "") -> bytes:
    parts: list[str] = [f'<?xml version="1.0" encoding="{encoding}"?>']
    if wrapper:
        parts.append(f"<{wrapper}>")
        _emit(root, data, parts, 1)
        parts.append(f"</{wrapper}>")
    else:
        _emit(root, data, parts, 0)
    return "\n".join(parts).encode(encoding, errors="replace")


# ---------------------------------------------------------- Datum & Zahlen
def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(s: str | None) -> time | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%H.%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def fmt_date(d: date) -> str:
    """Datum im san6-Format TT.MM.JJJJ."""
    return d.strftime("%d.%m.%Y")


def fmt_time(t: time) -> str:
    return t.strftime("%H:%M")


def to_date_param(s: str) -> str:
    """Akzeptiert ISO (2026-11-04) oder deutsch (04.11.2026) und liefert TT.MM.JJJJ."""
    d = parse_date(s)
    if d is None:
        raise ValueError(f"Ungültiges Datum: {s!r} (erwartet JJJJ-MM-TT oder TT.MM.JJJJ)")
    return fmt_date(d)


def parse_num(s: Any) -> float:
    """Zahlen aus san6: '51.84', '61,69', '238,--', '1.234,56', '' -> 0.0"""
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("€", "").replace(" ", "")
    if not t:
        return 0.0
    t = re.sub(r"([,.])-+$", r"\g<1>00", t)  # "238,--" / "100,-" -> "238,00"
    neg = t.startswith("-") or t.endswith("-")
    t = t.strip("-")
    if "," in t and "." in t:
        # 1.234,56  oder 1,234.56
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return 0.0
    return -v if neg else v

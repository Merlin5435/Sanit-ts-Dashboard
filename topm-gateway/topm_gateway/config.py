"""Konfiguration des TopM-Gateways – ausschließlich über Umgebungsvariablen (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name, "1" if default else "0").lower()
    return v in ("1", "true", "ja", "yes", "on", "j")


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _list(name: str) -> list[str]:
    return [x.strip() for x in _env(name).split(",") if x.strip()]


def _map(name: str) -> dict[str, str]:
    """Format: 012=anna@firma.de,013=bernd@firma.de"""
    out: dict[str, str] = {}
    for item in _list(name):
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
    return out


@dataclass
class San6Config:
    base_url: str          # z.B. https://san6.meinbetrieb.de:8084/inhouse.top
    company: str
    product: str
    mandant: str
    sys: str               # Systemnummer aus Vorbereitung -> API-Schnittstelle
    password: str
    auth_mode: str = "md5"  # "basic" (Passwort im Klartext) oder "md5"
    mitarbeiter: str = ""   # optionaler Mitarbeiter im Aufruf
    tan_device: str = ""    # wenn gesetzt, wird jede Anfrage mit TAN <device>-<8stellig> versehen
    timeout: int = 60
    verify_tls: bool = True
    post_encoding: str = "utf-8"
    post_wrapper: str = ""  # optionales Wurzel-Element um den POST-Body (falls vom Betrieb gefordert)
    md5_basis: str = "ohne_api"  # "ohne_api": ab Funktionsname (Beispiel Kap. 3.3.2), "mit_api": ab "API-"


@dataclass
class GraphConfig:
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    authority: str = "https://login.microsoftonline.com"
    base_url: str = "https://graph.microsoft.com/v1.0"
    timezone: str = "W. Europe Standard Time"
    category: str = "TopM san6"

    @property
    def enabled(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret)


@dataclass
class SyncConfig:
    interval_minutes: int = 15
    days_back: int = 7
    days_ahead: int = 90
    mitarbeiter: list[str] = field(default_factory=list)      # leer = alle
    outlook_map: dict[str, str] = field(default_factory=dict)  # Mitarbeiter -> Postfach
    conflict_policy: str = "san6"  # "san6" oder "outlook" – wer gewinnt bei beidseitiger Änderung
    outlook_to_san6: bool = True   # Verschiebungen/Löschungen aus Outlook zurückschreiben
    outlook_new_category: str = ""  # Outlook-Termine mit dieser Kategorie werden in san6 neu angelegt
    outlook_new_terminart: str = "01"
    rechnungen_days_back: int = 40
    controlling_hour: int = 2       # tägliche Controlling-Archivierung (Stunde, lokal)


@dataclass
class Settings:
    san6: San6Config
    graph: GraphConfig
    sync: SyncConfig
    api_keys: list[str]
    readonly_keys: list[str]
    ics_token: str
    db_path: str
    timezone: str = "Europe/Berlin"
    log_level: str = "INFO"


def load_settings() -> Settings:
    return Settings(
        san6=San6Config(
            base_url=_env("SAN6_BASE_URL", "https://home.topm.de:8084/inhouse.top"),
            company=_env("SAN6_COMPANY"),
            product=_env("SAN6_PRODUCT"),
            mandant=_env("SAN6_MANDANT"),
            sys=_env("SAN6_SYS", "01"),
            password=_env("SAN6_PASSWORD"),
            auth_mode=_env("SAN6_AUTH_MODE", "md5").lower(),
            mitarbeiter=_env("SAN6_MITARBEITER"),
            tan_device=_env("SAN6_TAN_DEVICE"),
            timeout=_int("SAN6_TIMEOUT", 60),
            verify_tls=_bool("SAN6_VERIFY_TLS", True),
            post_encoding=_env("SAN6_POST_ENCODING", "utf-8"),
            post_wrapper=_env("SAN6_POST_WRAPPER"),
            md5_basis=_env("SAN6_MD5_BASIS", "ohne_api").lower(),
        ),
        graph=GraphConfig(
            tenant_id=_env("GRAPH_TENANT_ID"),
            client_id=_env("GRAPH_CLIENT_ID"),
            client_secret=_env("GRAPH_CLIENT_SECRET"),
            authority=_env("GRAPH_AUTHORITY", "https://login.microsoftonline.com"),
            base_url=_env("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0"),
            timezone=_env("GRAPH_TIMEZONE", "W. Europe Standard Time"),
            category=_env("GRAPH_CATEGORY", "TopM san6"),
        ),
        sync=SyncConfig(
            interval_minutes=_int("SYNC_INTERVAL_MINUTES", 15),
            days_back=_int("SYNC_DAYS_BACK", 7),
            days_ahead=_int("SYNC_DAYS_AHEAD", 90),
            mitarbeiter=_list("SYNC_MITARBEITER"),
            outlook_map=_map("SYNC_OUTLOOK_MAP"),
            conflict_policy=_env("SYNC_CONFLICT_POLICY", "san6").lower(),
            outlook_to_san6=_bool("SYNC_OUTLOOK_TO_SAN6", True),
            outlook_new_category=_env("SYNC_OUTLOOK_NEW_CATEGORY"),
            outlook_new_terminart=_env("SYNC_OUTLOOK_NEW_TERMINART", "01"),
            rechnungen_days_back=_int("SYNC_RECHNUNGEN_DAYS_BACK", 40),
            controlling_hour=_int("SYNC_CONTROLLING_HOUR", 2),
        ),
        api_keys=_list("GATEWAY_API_KEYS"),
        readonly_keys=_list("GATEWAY_READONLY_KEYS"),
        ics_token=_env("GATEWAY_ICS_TOKEN"),
        db_path=_env("GATEWAY_DB_PATH", "./data/gateway.sqlite"),
        timezone=_env("GATEWAY_TIMEZONE", "Europe/Berlin"),
        log_level=_env("LOG_LEVEL", "INFO"),
    )

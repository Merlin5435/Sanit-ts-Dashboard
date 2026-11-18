"""Einstellungen – alles über Umgebungsvariablen bzw. die Datei .env."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def lade_env_datei(pfad: str) -> None:
    """Liest KEY=WERT-Zeilen aus .env, ohne bereits gesetzte Variablen zu überschreiben."""
    if not os.path.exists(pfad):
        return
    for zeile in open(pfad, encoding="utf-8"):
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        k, v = zeile.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Config:
    modus: str              # "test" (Testwelt) oder "echt"
    port: int
    db_pfad: str
    modell: str
    abgleich_stunde: int
    abgleich_minute: int
    chef_nummer: str        # san6-Mitarbeiternummer des Chefs
    chef_postfach: str      # Outlook-Postfach des Chefs
    hotline_gruppe: str     # Orga-Gruppe, an die die KI-Hotline Anliegen schickt

    @property
    def ki_aktiv(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def lade_config() -> Config:
    return Config(
        modus=_env("DASHBOARD_MODUS", "test"),
        port=int(_env("DASHBOARD_PORT", "8090")),
        db_pfad=_env("DASHBOARD_DB", "./daten/dashboard.sqlite"),
        modell=_env("CLAUDE_MODELL", "claude-opus-5-5"),
        abgleich_stunde=int(_env("ABGLEICH_STUNDE", "5")),
        abgleich_minute=int(_env("ABGLEICH_MINUTE", "0")),
        chef_nummer=_env("CHEF_SAN6_NUMMER", "001"),
        chef_postfach=_env("CHEF_POSTFACH", "chef@sanitaetshaus-beispiel.de"),
        hotline_gruppe=_env("HOTLINE_ORGA_GRUPPE", "00"),
    )

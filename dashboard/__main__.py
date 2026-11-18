"""Start:  python -m dashboard            (Testversion mit simulierten Daten, öffnet den Browser)
        python -m dashboard --kein-browser
        python -m dashboard abgleich   (einmal abgleichen und Ergebnis ausgeben)
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import webbrowser
from datetime import date

from werkzeug.serving import make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard.app.config import lade_config, lade_env_datei  # noqa: E402

lade_env_datei(os.path.join(ROOT, "dashboard", ".env"))

from dashboard.app.dienst import Dienst  # noqa: E402
from dashboard.app.speicher import Speicher  # noqa: E402
from dashboard.app.web import create_app  # noqa: E402
from topm_gateway.config import GraphConfig, San6Config  # noqa: E402
from topm_gateway.graph_client import GraphClient  # noqa: E402
from topm_gateway.san6_client import San6Client  # noqa: E402


def _im_hintergrund(app) -> str:
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}"


def baue_dienst(cfg, db_pfad: str | None = None, heute: date | None = None) -> Dienst:
    if cfg.modus != "test":
        raise SystemExit("Die Vollversion (echte Systeme) ist noch nicht freigeschaltet. DASHBOARD_MODUS=test verwenden.")
    from dashboard.testwelt import graph_sim, san6_sim
    from dashboard.testwelt.welt import Welt
    welt = Welt(heute=heute or date.today())
    s6_url = _im_hintergrund(san6_sim.create_app(welt, "TEST")) + "/inhouse.top"
    gr_url = _im_hintergrund(graph_sim.create_app(welt))
    san6 = San6Client(San6Config(base_url=s6_url, company="Testwelt", product="san6Test", mandant="Test", sys="50",
                                 password="TEST", auth_mode="md5"))
    graph = GraphClient(GraphConfig(tenant_id="test", client_id="test", client_secret="test",
                                    authority=gr_url, base_url=gr_url + "/v1.0"))
    return Dienst(cfg, san6, graph, Speicher(db_pfad or cfg.db_pfad), heute=lambda: welt.heute, welt=welt)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = lade_config()
    args = sys.argv[1:]
    if args and args[0] == "abgleich":
        b = baue_dienst(cfg, db_pfad=":memory:").abgleich()
        print(("OHNE KI" if b["ohne_ki"] else f"KI: {b['modell']}") + f" – {b['zusammenfassung']}")
        for i, p in enumerate(b["punkte"], 1):
            print(f"{i}. [{p['dringlichkeit']}] {p['titel']} – {p['begruendung']}")
        for f in b["fehler"]:
            print("Fehler:", f)
        return
    # Testversion: jeder Start beginnt mit einer frischen Testwelt
    db = cfg.db_pfad
    if os.path.exists(db):
        os.remove(db)
    dienst = baue_dienst(cfg)
    dienst.starte_zeitplan()
    app = create_app(dienst)
    url = f"http://127.0.0.1:{cfg.port}"
    print(f"\n  KI-Dashboard (Testversion) läuft: {url}")
    print("  KI: " + ("verbunden (" + cfg.modell + ")" if cfg.ki_aktiv else "NICHT verbunden – ANTHROPIC_API_KEY in dashboard/.env eintragen"))
    print("  Beenden mit Strg+C\n")
    if "--kein-browser" not in args:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    make_server("127.0.0.1", cfg.port, app, threaded=True).serve_forever()


if __name__ == "__main__":
    main()

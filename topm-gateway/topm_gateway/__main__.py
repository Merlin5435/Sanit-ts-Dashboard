"""Start:  python -m topm_gateway serve | worker | sync-termine | sync-controlling | ping"""
from __future__ import annotations

import json
import logging
import os
import sys

from .core import Gateway


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    _load_dotenv()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    gw = Gateway()
    logging.basicConfig(level=gw.settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if cmd == "serve":
        from .app import create_app
        app = create_app(gw)
        if os.environ.get("GATEWAY_EMBEDDED_WORKER", "1") == "1":
            import threading
            threading.Thread(target=gw.run_forever, daemon=True).start()
        app.run(host=os.environ.get("GATEWAY_HOST", "0.0.0.0"), port=int(os.environ.get("GATEWAY_PORT", 8080)))
    elif cmd == "worker":
        gw.run_forever()
    elif cmd == "sync-termine":
        print(json.dumps(gw.sync_termine(), ensure_ascii=False, indent=2))
    elif cmd == "sync-controlling":
        print(json.dumps(gw.sync_controlling(), ensure_ascii=False, indent=2))
    elif cmd == "ping":
        print(json.dumps(gw.svc.ping(), ensure_ascii=False, indent=2))
    else:
        sys.exit(f"Unbekannter Befehl {cmd!r}. Erlaubt: serve, worker, sync-termine, sync-controlling, ping")


if __name__ == "__main__":
    main()

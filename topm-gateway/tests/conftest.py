import os
import sys
import threading

import pytest
from werkzeug.serving import make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mock"))

import graph_mock  # noqa: E402
import san6_mock  # noqa: E402

from topm_gateway.config import GraphConfig, San6Config, Settings, SyncConfig  # noqa: E402


def _serve(app):
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv


@pytest.fixture(scope="session")
def servers():
    s = _serve(san6_mock.app)
    g = _serve(graph_mock.app)
    yield {"san6": f"http://127.0.0.1:{s.server_port}/inhouse.top", "graph": f"http://127.0.0.1:{g.server_port}"}
    s.shutdown()
    g.shutdown()


def make_settings(servers, tmp_path, auth_mode="md5", graph=True, **sync) -> Settings:
    return Settings(
        san6=San6Config(base_url=servers["san6"], company="TopM", product="san6Test", mandant="Extern",
                        sys="07", password="HALLO", auth_mode=auth_mode, tan_device="0001"),
        graph=GraphConfig(tenant_id="t1", client_id="c", client_secret="s", authority=servers["graph"],
                          base_url=servers["graph"] + "/v1.0") if graph else GraphConfig(),
        sync=SyncConfig(days_back=3, days_ahead=30,
                        outlook_map=sync.pop("outlook_map", {"012": "iltgen@firma.de", "013": "koopman@firma.de"}),
                        **sync),
        api_keys=["write-key"], readonly_keys=["read-key"], ics_token="ics-token",
        db_path=str(tmp_path / "gw.sqlite"),
    )


@pytest.fixture
def gw(servers, tmp_path):
    from topm_gateway.core import Gateway
    san6_mock.reset()
    graph_mock.reset()
    return Gateway(make_settings(servers, tmp_path))


@pytest.fixture
def client(gw):
    from topm_gateway.app import create_app
    app = create_app(gw)
    app.testing = True
    c = app.test_client()
    c.environ_base["HTTP_X_API_KEY"] = "write-key"
    return c

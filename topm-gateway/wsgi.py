"""WSGI-Einstieg für gunicorn/waitress:  gunicorn -w 2 -b 0.0.0.0:8080 wsgi:app
Der Sync-Worker läuft dann separat:      python -m topm_gateway worker"""
from topm_gateway.__main__ import _load_dotenv

_load_dotenv()
from topm_gateway.app import create_app  # noqa: E402

app = create_app()

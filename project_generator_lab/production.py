"""Production entrypoint: session verification is mandatory here."""

import os

from .app import app
from .core import web
from .core.access import SessionGuard

# Links from search answers are opened on the server: connect only to the checked address, never through proxy variables.
web.SERVER = True

auth_url = os.environ.get("PROJECT_LAB_AUTH_URL", "")
if not auth_url:
    raise RuntimeError("PROJECT_LAB_AUTH_URL is required for production")
app.add_middleware(SessionGuard, auth_url=auth_url)

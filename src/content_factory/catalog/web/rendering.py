"""Native Jinja rendering for the catalog UI.

Mirrors the legacy viewer's ``create_app`` render(): same templates, same filters,
same shared context (nav, summary, route zone) — so output is visually identical.
The only addition is the ``base`` URL-prefix global, so template links resolve under
the FastAPI router mount (``/app/spravochnik``) without the old PrefixRewrite hack.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from content_factory.catalog.viewer._common import (
    format_local_datetime,
    load_summary,
    refresh_summary_counts,
)
from content_factory.catalog.viewer.catalog_admin_ops import COMPLEXITY_OPTIONS
from content_factory.catalog.viewer.labels import (
    edge_reason_label,
    review_entity_label,
    review_severity_label,
    review_source_label,
    review_status_label,
    review_text_label,
)
from content_factory.catalog.viewer.route_zones import (
    detect_route_zone,
    get_ecosystem_nav,
    get_main_nav,
    get_secondary_nav,
    show_secondary_nav,
)
from content_factory.catalog.viewer.ui_constants import (
    DEFAULT_DB,
    DEFAULT_SUMMARY,
    INTAKE_PROGRESS_STEPS,
    TEMPLATES_DIR,
)

#: URL prefix the catalog UI router is mounted under.
CATALOG_URL_PREFIX = "/app/spravochnik"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)
_env.filters["datetime_local"] = format_local_datetime
_env.filters["review_entity_label"] = review_entity_label
_env.filters["review_source_label"] = review_source_label
_env.filters["review_text_label"] = review_text_label
_env.filters["edge_reason_label"] = edge_reason_label
_env.filters["review_severity_label"] = review_severity_label
_env.filters["review_status_label"] = review_status_label
_env.globals["base"] = CATALOG_URL_PREFIX


def render(
    template_name: str,
    context: dict[str, Any],
    *,
    request_path: str,
    db_path: Path | None = None,
    summary_path: Path | None = None,
) -> str:
    """Render a catalog template with the shared shell context.

    ``request_path`` is the WSGI-equivalent logical path (e.g. ``/competencies``),
    without the ``/app/spravochnik`` prefix, so nav active-state and route zones
    behave exactly as in the legacy viewer.
    """

    db_path = db_path or DEFAULT_DB
    summary_path = summary_path or DEFAULT_SUMMARY
    summary = refresh_summary_counts(load_summary(summary_path), db_path)
    shared: dict[str, Any] = {
        "ecosystem_nav": get_ecosystem_nav("catalog"),
        "nav": get_main_nav(),
        "secondary_nav": get_secondary_nav(request_path),
        "show_secondary_nav": show_secondary_nav(request_path),
        "route_zone": detect_route_zone(request_path),
        "complexity_options": COMPLEXITY_OPTIONS,
        "intake_progress_steps": INTAKE_PROGRESS_STEPS,
        "summary": summary,
        "request_path": request_path,
    }
    return _env.get_template(template_name).render(**{**shared, **context})

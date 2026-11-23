"""Download a curriculum in the requested format, keeping the same review status."""

from typing import Any, Literal

from fastapi.responses import PlainTextResponse, Response

from content_factory.catalog.pipeline.curriculum.source_fidelity import source_release_failures
from content_factory.catalog.viewer.curriculum_ops import curriculum_plan_to_csv_bytes, curriculum_plan_to_xlsx_bytes
from content_factory.catalog.viewer.curriculum_xlsx import ExcelCellError

ExportFormat = Literal["csv", "xlsx"]


def curriculum_attachment(
    plan: dict[str, Any], filename_stem: str, fmt: ExportFormat, *, check_dag_status: bool,
) -> Response:
    failures = source_release_failures(plan)
    if check_dag_status and str(plan.get("status") or "").casefold() == "invalid" and not failures:
        return PlainTextResponse(f"{fmt.upper()} export is blocked: curriculum plan has DAG order violations.", status_code=409)
    if fmt == "xlsx":
        try:
            data = curriculum_plan_to_xlsx_bytes(plan, failures=failures)
        except ExcelCellError as exc:
            return PlainTextResponse(str(exc), status_code=422)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        data = curriculum_plan_to_csv_bytes(plan, failures=failures)
        mime = "text/csv; charset=utf-8"
    filename = f"{filename_stem}{'_draft' if failures else ''}.{fmt}"
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

"""One source-precedence policy for every consumer of the curriculum."""

from .models import StartRequest


def effective_request(request: StartRequest) -> StartRequest:
    """Retain the uploaded original in storage; derive the view permitted for planning and review."""
    policy = request.brief.curriculum_policy
    if policy == "authoritative" or request.curriculum is None:
        return request
    if policy == "ignore":
        return request.model_copy(update={"curriculum": None, "selected_row_id": ""})
    brief = request.brief
    rows = []
    for row in request.curriculum.rows:
        if row.id != request.selected_row_id:
            rows.append(row)
            continue
        # The position and neighbours remain context; all project-specific claims come from the current brief.
        values = {key: getattr(brief, key) for key in ("title", "description", "outcomes", "skills", "tools", "software", "audience",
                                                        "storytelling", "storytelling_type", "checking", "repository", "format")}
        values.update(hours=str(brief.hours or ""), group_size=str(brief.group_size or ""), materials=brief.references,
                      cases="", raw={"policy": "Устаревшее содержание строки отменено методологом; текущие требования — в форме"})
        rows.append(row.model_copy(update=values))
    return request.model_copy(update={"curriculum": request.curriculum.model_copy(update={"rows": rows})})

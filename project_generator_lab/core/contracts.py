"""Base contract shared by every module: unknown fields are an error, strings are trimmed.

One exception, for rollbacks: a stored file written by a NEWER release (its schema_version above STORED_SCHEMA) is
read with the fields this release does not know left out, so rolling back a release that only added stored fields
needs no data restore. Everything else stays strict — model answers, requests, and files of this or an older release:
an older file with a field since removed is never guessed into the new shape.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationInfo, model_validator

# The shape of stored files (concept threads, runs, content drafts). Raise it in the release that adds or removes a
# stored field: tests/core/test_storage.py compares the stored fields with the digest recorded for this number and
# fails until it is raised. A release that meets a higher number knows the file is newer than itself.
# 2 (05.10.2026): a plan row keeps its kind (project or exam), the project's code and its cases; a plan keeps the
# platform's catalogue of skills and the skills blocks of its projects; a budget keeps the cap on main tasks.
# A task of a concept keeps the weights of the skills it practises, a run — the block code counted from them; a plan
# row keeps whether the plan gives the project a bonus part, a handoff — the block as lines of the tab and what is wrong
# with it. A brief keeps how the text is to be told and the story of the series (06.10.2026), a handoff carries them, a
# content draft keeps its style, a task its examples, how it is checked and the style it was written in; and how its
# theory is given, the summaries of its sections and the excerpt of a primary source (content 0.6). All of it
# under one number: no release had been built with 2 before 20261007-01 (deploy/README.md).
# 3 (07.10.2026, after the release 20261007-01 was built with 2): the titles a project may go by, kept with its
# title and annotation (content 0.9).
# 4 (27.10.2026): the sources of a freshness check that did not open when code tried them
# (Freshness.unreachable). Added after a methodologist reported links in the report that would not open: the
# content module opened every link already, the freshness check took the provider's word for them.
# 5 (29.10.2026): a run keeps that the methodologist kept more hours than one project holds (Run.hours_kept).
# 6: source precedence, durable clarification resolutions and project writing preferences.
# 7: evaluation resource dependencies, history warnings and failed-call accounting.
STORED_SCHEMA = 7


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def newer_fields_left_out(cls, data: Any, info: ValidationInfo) -> Any:
        """Only with the context core.storage.parse_stored passes for a newer file; otherwise a no-op."""
        context = info.context
        if not (isinstance(context, dict) and context.get("newer") and isinstance(data, dict)):
            return data
        unknown = [key for key in data if key not in cls.model_fields]
        if unknown:
            context.setdefault("left_out", []).extend(f"{cls.__name__}.{key}" for key in unknown)
        return {key: value for key, value in data.items() if key in cls.model_fields}

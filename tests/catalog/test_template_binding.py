"""Durable TemplateBinding provenance (project-contract epic, slice 6a)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.domain import TemplateBinding
from content_factory.catalog.pipeline.curriculum.planner import _template_binding_for


def test_binding_from_global_template_snapshots_version() -> None:
    binding = _template_binding_for(
        {"code": "tmpl-ci", "source": "global", "updated_at": "2026-07-13T10:00:00"}
    )
    assert binding == TemplateBinding(
        template_code="tmpl-ci",
        template_version="2026-07-13T10:00:00",
        source="global",
        repeatable=False,
    )


def test_binding_marks_brief_source_and_repeatable() -> None:
    binding = _template_binding_for({"code": "p1", "source": "brief", "repeatable": True})
    assert binding is not None
    assert binding.source == "brief"
    assert binding.repeatable is True


def test_no_binding_without_template_or_code() -> None:
    assert _template_binding_for(None) is None
    assert _template_binding_for({"source": "global"}) is None


def test_as_dict_shape() -> None:
    binding = _template_binding_for({"code": "c", "source": "global", "updated_at": "v1"})
    assert binding is not None
    assert set(binding.as_dict()) == {"template_code", "template_version", "source", "repeatable"}

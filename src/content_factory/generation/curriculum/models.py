"""Typed curriculum-context models."""

from pydantic import BaseModel, Field


class CurriculumEntry(BaseModel):
    """Compact project entry used for curriculum ordering and continuity."""

    track: str
    order: int
    code: str
    code_name: str
    title: str
    skills: list[str] = Field(default_factory=list)
    learning_outcomes: list[str] = Field(default_factory=list)
    tone_flags: list[str] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    file_path: str | None = None

"""Teaching fields shared by incoming proposals, the review editor and the plan compiler."""

from pydantic import BaseModel, Field


class AcceptanceTest(BaseModel):
    check: str = Field(min_length=5)
    expected_result: str = Field(min_length=5)


class LearningOutcomes(BaseModel):
    know: list[str] = Field(default_factory=list)
    can: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class RevisionPractice(BaseModel):
    inputs: list[str] = Field(default_factory=list)
    case_context: str = ""
    required_tools: list[str] = Field(default_factory=list)
    effort_hours: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    is_capstone: bool = False
    is_entry_project: bool = False
    choose_one_group: str = ""
    group_size: int | None = Field(default=None, ge=2, strict=True)
    peer_reviews: int | None = Field(default=None, ge=0, strict=True)
    acceptance_test: AcceptanceTest | None = None
    prerequisite_source_ids: list[str] = Field(default_factory=list)
    learning_outcomes: LearningOutcomes = Field(default_factory=LearningOutcomes)

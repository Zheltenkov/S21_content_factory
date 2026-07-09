"""Facade and default registry for audit checkers."""

from __future__ import annotations

from content_factory.audit.checker_base import (
    BaseChecker,
    CheckContext,  # noqa: F401 - compatibility re-export
    _finding,  # noqa: F401 - compatibility re-export
)
from content_factory.audit.curriculum_relevance import CurriculumRelevanceChecker
from content_factory.audit.dependency_freshness import DependencyFreshnessChecker
from content_factory.audit.document_structure import (
    BrokenUrlSyntaxChecker,
    ExamPresenceChecker,
    LabelPunctuationChecker,
    MarkdownStructureChecker,
    StructureChecker,
)
from content_factory.audit.fact_claims import (
    FactCheckerPerplexity,
    ReadmeFactActualityChecker,
)
from content_factory.audit.language_readability import (
    LanguageCoverageChecker,
    ReadabilityChecker,  # noqa: F401 - compatibility re-export
)
from content_factory.audit.link_checks import (
    ImageQualityChecker,
    LinkChecker,
    LocalLinkChecker,
)
from content_factory.audit.local_consistency import LocalConsistencyChecker
from content_factory.audit.model_assisted_checks import (
    MarketFitChecker,
    ModelRubricChecker,
    _finding_from_model_item,  # noqa: F401 - compatibility re-export
)
from content_factory.audit.regional_checks import RegionalAvailabilityChecker
from content_factory.audit.resource_checks import ChecklistChecker, ResourceAvailabilityChecker
from content_factory.audit.rights import CodeMatch
from content_factory.audit.rights_checks import (
    RightsAndOriginalityChecker,
    RightsChecker,  # noqa: F401 - compatibility re-export
)
from content_factory.audit.spelling_wording import SpellingAndWordingChecker
from content_factory.audit.tech_freshness import (
    TechFreshnessChecker,
    TechnologyFreshnessChecker,  # noqa: F401 - compatibility re-export
)


def default_checkers(
    use_model: bool,
    code_similarity_index: dict[str, list[CodeMatch]] | None = None,
    lean: bool = False,
) -> list[BaseChecker]:
    """Возвращает набор проверок для первого рабочего прототипа."""

    from content_factory.audit.extra_checkers import (
        CourseMaterialRelevanceChecker,
        CrossFileConsistencyChecker,
    )

    checkers: list[BaseChecker] = [
        StructureChecker(),
        BrokenUrlSyntaxChecker(),
        MarkdownStructureChecker(),
        LabelPunctuationChecker(),
        SpellingAndWordingChecker(),
        LocalConsistencyChecker(),
        ChecklistChecker(),
        ResourceAvailabilityChecker(),
        LinkChecker(),
        LocalLinkChecker(),
        LanguageCoverageChecker(),
        ExamPresenceChecker(),
        ImageQualityChecker(),
        RightsAndOriginalityChecker(code_similarity_index=code_similarity_index),
        MarketFitChecker(),
        DependencyFreshnessChecker(),
        RegionalAvailabilityChecker(),
        TechFreshnessChecker(),
        CurriculumRelevanceChecker(),
        CrossFileConsistencyChecker(),
        CourseMaterialRelevanceChecker(),
    ]
    if use_model:
        checkers.append(ReadmeFactActualityChecker())
        checkers.append(FactCheckerPerplexity())
        checkers.append(ModelRubricChecker())
    if lean:
        # Убираем дорогие/нулевые по точности правила: фактчек Perplexity, readme-факты, tech-freshness.
        _drop = {"fact_checker_perplexity", "readme_fact_actuality_checker", "tech_freshness_checker"}
        checkers = [c for c in checkers if c.name not in _drop]
    return checkers

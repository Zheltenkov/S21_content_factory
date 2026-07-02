"""Structure phase executor for title, annotation, and README skeleton."""

import logging

from .generation_runtime import GenerationRuntimeContainer
from .models.flow_state import ProjectBlueprint
from .models.phase_results import SkeletonPhaseResult, StructurePhaseResult, TitleAnnotationPhaseResult
from .models.schemas import Annotation, IntroSection, ProjectContextMeta, ProjectSeed

logger = logging.getLogger("content_gen.structure_phase_executor")


def _execute_skeleton_phase(
    orchestrator,
    seed: ProjectSeed,
    context_meta: ProjectContextMeta,
    generate_bonus: bool
) -> SkeletonPhaseResult:
    """
    Phase 1: Каркас с StructuralPreflight.
    
    Args:
        orchestrator: Runtime-like object with structure agents and validators
        seed: Проектный seed
        context_meta: Метаданные curriculum context
        generate_bonus: Генерировать ли бонусные задания
        
    Returns:
        Typed skeleton phase result with markdown, review metadata, and blueprint.
    """
    title_annotation = _generate_title_annotation(orchestrator, seed, context_meta)
    structure = _build_structure(
        orchestrator,
        seed,
        context_meta,
        generate_bonus,
        title_annotation.title,
        title_annotation.annotation,
    )

    return SkeletonPhaseResult(
        markdown=structure.markdown,
        preflight_result=structure.preflight_result,
        intro_section=structure.intro_section,
        blueprint=structure.blueprint,
        title=title_annotation.title,
        annotation=title_annotation.annotation,
    )


def _generate_title_annotation(
    orchestrator,
    seed: ProjectSeed,
    context_meta: ProjectContextMeta,
) -> TitleAnnotationPhaseResult:
    """Generate the project title and annotation as a separately reviewable artifact."""
    logger.info("🔄 Phase 1 | TitleAnnotation")
    ta = orchestrator.title_annot.generate(seed, context_meta)
    annotation = Annotation(text=ta.annotation.text, chars=len(ta.annotation.text))
    return TitleAnnotationPhaseResult(title=ta.title, annotation=annotation)


def _build_structure(
    orchestrator,
    seed: ProjectSeed,
    context_meta: ProjectContextMeta,
    generate_bonus: bool,
    title: str,
    annotation: Annotation | dict,
) -> StructurePhaseResult:
    """Build the README skeleton and intro using an already approved title/annotation."""
    logger.info("🔄 Phase 1 | Skeleton")
    annotation_text = _annotation_text(annotation)
    sk = orchestrator.skeleton.build(language=seed.language, has_bonus=generate_bonus)
    md = orchestrator.skeleton.stitch(title=title, annotation_md=annotation_text, sk=sk)

    logger.info("🔄 Phase 1 | IntroRules")
    intro_res = orchestrator.intro.generate(seed, context_meta, annotation_text=annotation_text)
    md = orchestrator.intro.inject_into_markdown(md, intro_res)
    intro_section = IntroSection(
        intro_text=intro_res.intro_text,
        instruction_text=intro_res.instruction_text,
    )
    blueprint = ProjectBlueprint(
        language=str(seed.language),
        has_bonus=generate_bonus,
        section_order=["title", "annotation", "toc", "intro", "theory", "practice"] + (["bonus"] if generate_bonus else []),
        chapter_titles={
            "toc": sk.toc_anchor.splitlines()[0].strip(),
            "intro": sk.ch1.splitlines()[0].strip(),
            "theory": sk.ch2.splitlines()[0].strip(),
            "practice": sk.ch3.splitlines()[0].strip(),
            "bonus": sk.bonus.splitlines()[0].strip() if sk.bonus else "",
        },
        intro_subsections=["Введение", "Инструкция"],
        planned_tasks_count=seed.tasks_count,
        planned_task_complexity=seed.task_complexity,
    )

    # Structural Preflight
    logger.info("🔄 Phase 1 | StructuralPreflight")
    preflight_result = orchestrator.structural_preflight.check(md, has_bonus=generate_bonus)

    if not preflight_result.passed:
        logger.warning(f"⚠️ Structural Preflight: {len(preflight_result.hard_issues)} HARD проблем")
        # Попытка исправления через Regeneration
        if preflight_result.hard_issues:
            fixable_issues = [i for i in preflight_result.hard_issues if i.fixable]
            if fixable_issues:
                logger.info("🔄 Phase 1 | Regeneration (fix structure)")
                issues_text = "\n".join(f"- {i.message}" for i in fixable_issues[:3])
                try:
                    md = orchestrator.regeneration.regenerate(
                        original_md=md,
                        comments=f"Исправь следующие проблемы структуры:\n{issues_text}",
                        language=seed.language
                    ).regenerated_md
                    # Повторная проверка
                    preflight_result = orchestrator.structural_preflight.check(md, has_bonus=generate_bonus)
                except Exception as e:
                    logger.warning(f"⚠️ Regeneration не удался: {e}")

    return StructurePhaseResult(
        markdown=md,
        preflight_result=preflight_result,
        intro_section=intro_section,
        blueprint=blueprint,
    )


def _annotation_text(annotation: Annotation | dict) -> str:
    """Read annotation text from either hydrated schema or persisted paused-state dict."""
    if isinstance(annotation, dict):
        return str(annotation.get("text") or "")
    return str(getattr(annotation, "text", "") or "")


class StructurePhaseExecutor:
    """Execute Phase 1 title, annotation, and README structure steps."""

    def __init__(self, runtime: GenerationRuntimeContainer) -> None:
        self.runtime = runtime

    def build_skeleton(
        self,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta,
        generate_bonus: bool,
    ) -> SkeletonPhaseResult:
        return _execute_skeleton_phase(self.runtime, seed, context_meta, generate_bonus)

    def generate_title_annotation(
        self,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta,
    ) -> TitleAnnotationPhaseResult:
        return _generate_title_annotation(self.runtime, seed, context_meta)

    def build_structure(
        self,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta,
        generate_bonus: bool,
        title: str,
        annotation: Annotation | dict,
    ) -> StructurePhaseResult:
        return _build_structure(self.runtime, seed, context_meta, generate_bonus, title, annotation)

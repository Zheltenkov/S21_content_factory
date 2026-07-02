"""Runtime dependency container for content generation phases."""

from __future__ import annotations

from typing import Any

from .agents.content_editor import ContentEditorAgent
from .agents.context_analysis import ContextAnalysisResult
from .agents.dataset_generator import DatasetGeneratorAgent
from .agents.definitions_agent import DefinitionsAgent
from .agents.intro_rules import IntroRulesAgent
from .agents.length_agent import LengthAgent
from .agents.practice import PracticeAgent
from .agents.practice_critic import PracticeCriticAgent
from .agents.readability_agent import ReadabilityAgent
from .agents.regeneration import RegenerationAgent
from .agents.theory import TheoryAgent
from .agents.theory_enhancement_agent import TheoryEnhancementAgent
from .agents.title_annotation import TitleAnnotationAgent
from .agents.translator import TranslatorAgent
from .renderers.skeleton import SkeletonRenderer
from .renderers.toc import TOCRenderer
from .repair.style_guard import StyleGuardRepair
from .models.flow_state import ProjectContextBundle
from .models.schemas import PracticeTask, ProjectContextMeta, TheoryPart
from .validators.practice import PracticeValidator
from .validators.structural_preflight import StructuralPreflight
from .validators.structure import IntroValidator
from .validators.theory import TheoryValidator
from .validators.theory_checks import TheoryChecks


class GenerationRuntimeContainer:
    """Own agents, validators, and mutable phase state for one generation run."""

    def __init__(self, llm_client: Any, cancellation_token: Any = None, progress_tracker: Any = None) -> None:
        self.llm = llm_client
        self.cancellation_token = cancellation_token
        self.progress_tracker = progress_tracker

        # Generation dependencies are grouped by boundary: LLM agents,
        # deterministic renderers/repair components, and validators.
        self.intent = None
        self.title_annot = TitleAnnotationAgent(self.llm_for("title_annotation", "TitleAnnotationAgent"))
        self.skeleton = SkeletonRenderer()
        self.intro = IntroRulesAgent(self.llm_for("skeleton", "IntroRulesAgent"))
        self.theory = TheoryAgent(self.llm_for("theory", "TheoryAgent"))
        self.theory_enhancement = TheoryEnhancementAgent(self.llm_for("theory", "TheoryEnhancementAgent"))
        self.theory_completeness = None
        self.content_editor = ContentEditorAgent(self.llm_for("quality", "ContentEditorAgent"))
        self.definitions_agent = DefinitionsAgent(self.llm_for("theory", "DefinitionsAgent"))
        self.length_agent = LengthAgent(self.llm_for("theory", "LengthAgent"))
        self.readability_agent = ReadabilityAgent(self.llm_for("theory", "ReadabilityAgent"))
        self.practice = PracticeAgent(self.llm_for("practice", "PracticeAgent"))
        self.dataset_generator = DatasetGeneratorAgent(self.llm_for("practice", "DatasetGeneratorAgent"))
        self.toc = TOCRenderer()
        self.style = StyleGuardRepair()
        self.practice_critic = PracticeCriticAgent(self.llm_for("practice", "PracticeCriticAgent"))
        self.translator = TranslatorAgent(self.llm_for("translate", "TranslatorAgent"))
        self.regeneration = RegenerationAgent(self.llm_for("repair", "RegenerationAgent"))

        # Validators are deterministic services shared by phase executors.
        self.structural_preflight = StructuralPreflight()
        self.theory_checks = TheoryChecks()
        self.practice_checks = None
        self.intro_validator = IntroValidator()
        self.theory_validator = TheoryValidator()
        self.practice_validator = PracticeValidator()
        self.rubric = None

        # Mutable artifacts are runtime state, not orchestration API.
        self.practice_critic_issues: list[dict[str, Any]] = []
        self.fallback_traces: list[dict[str, Any]] = []
        self.practice_tasks: list[PracticeTask] = []
        self.dataset_files: list[dict[str, Any]] = []
        self.theory_parts: list[TheoryPart] = []
        self.story_map_contract: Any | None = None
        self.practice_plan_contract: Any | None = None
        self.artifact_chain_plan: Any | None = None
        self.evidence_specs: list[Any] = []

    def llm_for(self, node: str, agent: str, prompt_version: str | None = None) -> Any:
        """Return a node-scoped LLM client when the active client supports tracing scopes."""
        scoped = getattr(self.llm, "scoped", None)
        if callable(scoped):
            return scoped(
                node=node,
                agent=agent,
                prompt_version=prompt_version or agent,
            )
        return self.llm

    def create_empty_context(self, thematic_block: str) -> ProjectContextMeta:
        """Create empty curriculum context for fallback paths."""
        return ProjectContextMeta(track=thematic_block, thematic_block=thematic_block)

    def create_empty_context_analysis(self) -> ContextAnalysisResult:
        """Create empty context-analysis result for fallback paths."""
        return ContextAnalysisResult(
            is_first_project=True,
            context_summary="",
            narrative_anchor="",
            similar_projects=[],
            relevant_chunks=[],
            skills_alignment={"intersection": [], "new": []},
            learning_outcomes_alignment={"continuation": [], "new": []},
            tools_alignment={"intersection": [], "new": []},
            audience_level_match=True,
            metrics={},
        )

    def create_empty_context_bundle(self, thematic_block: str) -> ProjectContextBundle:
        """Create a minimal context bundle when curriculum data is absent."""
        return ProjectContextBundle(
            context_source="fallback",
            thematic_block=thematic_block,
            current_project_order=None,
            previous_projects_count=0,
            is_first_project=True,
            reference_enabled=False,
            context_summary="",
            narrative_anchor="",
            narrative_contract={},
            aligned_skills=[],
            new_skills=[],
            continued_learning_outcomes=[],
            new_learning_outcomes=[],
            used_tools=[],
            new_tools=[],
        )

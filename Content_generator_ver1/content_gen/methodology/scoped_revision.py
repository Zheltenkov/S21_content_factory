"""Scoped methodologist revisions for paused generation state."""

from __future__ import annotations

import difflib
import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from content_gen.exceptions import ContentGenerationError
from content_gen.practice_contract import find_non_raw_material_issues
from content_gen.utils.markdown_block_contract import MarkdownBlockContract

from .change_request import (
    MethodologistChangeRequest,
    has_hard_conflicts,
    validate_methodologist_change_request,
)
from .target_registry import SectionTarget, build_section_target_registry

RevisionStatus = Literal["applied", "skipped", "rejected"]
RevisionTargetKind = Literal["field", "markdown_section", "material_file", "unsupported"]
_DISPLAY_BLOCK_REQUEST_RE = re.compile(
    r"(?:диаграм|mermaid|схем|таблиц|markdown\s*table|\btable\b)",
    re.IGNORECASE,
)


class ScopedRevisionResult(BaseModel):
    """Result of processing one methodologist change request."""

    action_id: str
    status: RevisionStatus
    target_kind: RevisionTargetKind
    target_stage: str
    target_selector: str = ""
    target_id: str = ""
    target_label: str = ""
    scope: str
    changed: bool = False
    changed_chars: int = 0
    recommended_resume_node: str | None = None
    issues: list[str] = Field(default_factory=list)
    diff_preview: list[str] = Field(default_factory=list)
    before_hash: str = ""
    after_hash: str = ""


class ScopedResumePlan(BaseModel):
    """Explicit resume contract after accepted methodologist revisions."""

    original_resume_from_index: int
    resume_from_index: int
    original_resume_node: str = ""
    resume_node: str = ""
    moved_back: bool = False
    invalidated_nodes: list[str] = Field(default_factory=list)
    applied_action_ids: list[str] = Field(default_factory=list)
    ignored_action_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class _MarkdownSection(BaseModel):
    start: int
    end: int
    text: str
    heading: str
    target_id: str = ""


class ScopedRevisionExecutor:
    """Apply approved change requests without letting LLM edit outside the target."""

    def __init__(
        self,
        llm_client: Any,
        block_contract: MarkdownBlockContract | None = None,
    ) -> None:
        self.llm = llm_client
        self.block_contract = block_contract or MarkdownBlockContract()

    def apply_pending_change_requests(
        self,
        context: dict[str, Any],
        *,
        raise_on_rejected: bool = True,
    ) -> list[ScopedRevisionResult]:
        """Apply unprocessed `changes_requested` actions stored in resume context."""
        actions = list(context.get("methodology_review_actions") or [])
        processed_ids = set(context.get("processed_methodology_change_ids") or [])
        results: list[ScopedRevisionResult] = []
        newly_processed: list[ScopedRevisionResult] = []
        for item in context.get("methodology_revision_results") or []:
            if not isinstance(item, dict):
                continue
            try:
                results.append(ScopedRevisionResult.model_validate(item))
            except Exception:
                continue

        for index, action in enumerate(actions):
            if not isinstance(action, dict) or action.get("action") != "changes_requested":
                continue
            details = action.get("details") if isinstance(action.get("details"), dict) else {}
            payload = details.get("change_request") if isinstance(details, dict) else None
            if not isinstance(payload, dict):
                continue

            request = MethodologistChangeRequest.model_validate(payload)
            action_id = self.action_id_for_action(action, index, request)
            if action_id in processed_ids:
                continue

            result = self.apply_change_request(context, request, action_id=action_id)
            results.append(result)
            newly_processed.append(result)
            processed_ids.add(action_id)
            if result.status == "rejected" and raise_on_rejected:
                context["processed_methodology_change_ids"] = sorted(processed_ids)
                context["methodology_revision_results"] = [item.model_dump(mode="json") for item in results]
                raise ContentGenerationError(
                    "Запрос правок методолога отклонен ScopedRevisionExecutor",
                    context={
                        "phase": "methodology_revision",
                        "error_type": "ScopedRevisionRejected",
                        "issues": result.issues,
                    },
                )

        context["processed_methodology_change_ids"] = sorted(processed_ids)
        context["methodology_revision_results"] = [item.model_dump(mode="json") for item in results]
        self._sync_state(context)
        return newly_processed

    def approved_preview_results_for_resume(self, context: dict[str, Any]) -> list[ScopedRevisionResult]:
        """Return accepted preview results when resume should not re-apply the same edits.

        Preview approval commits the previewed context before `/approve` resumes the flow.
        In that case `apply_pending_change_requests()` intentionally returns no new items,
        but the flow still needs the accepted revision metadata to choose the correct
        downstream resume node.
        """
        approved_ids = self._latest_diff_approved_ids(context)
        if not approved_ids:
            return []

        results: list[ScopedRevisionResult] = []
        for item in context.get("methodology_revision_results") or []:
            if not isinstance(item, dict):
                continue
            try:
                result = ScopedRevisionResult.model_validate(item)
            except Exception:
                continue
            if result.action_id in approved_ids:
                results.append(result)
        return results

    def apply_change_request(
        self,
        context: dict[str, Any],
        request: MethodologistChangeRequest,
        *,
        action_id: str,
    ) -> ScopedRevisionResult:
        """Apply one request to a markdown section or material file."""
        conflicts = validate_methodologist_change_request(request)
        if has_hard_conflicts(conflicts):
            return ScopedRevisionResult(
                action_id=action_id,
                status="rejected",
                target_kind="unsupported",
                target_stage=request.target_stage,
                target_selector=request.target_selector,
                target_id=request.target_selector,
                scope=request.scope,
                issues=[f"{conflict.code}: {conflict.message}" for conflict in conflicts],
            )

        if self._targets_field(context, request):
            return self._apply_field_revision(context, request, action_id)
        if self._targets_material(request):
            return self._apply_material_revision(context, request, action_id)
        if self._targets_markdown(request):
            return self._apply_markdown_revision(context, request, action_id)

        return ScopedRevisionResult(
            action_id=action_id,
            status="skipped",
            target_kind="unsupported",
            target_stage=request.target_stage,
            target_selector=request.target_selector,
            target_id=request.target_selector,
            scope=request.scope,
            issues=[f"Unsupported revision target: {request.target_stage}/{request.scope}"],
        )

    def _apply_field_revision(
        self,
        context: dict[str, Any],
        request: MethodologistChangeRequest,
        action_id: str,
    ) -> ScopedRevisionResult:
        target = self._resolve_target(context, request, kind="field")
        field_name = str((target.metadata.get("field") if target else "") or request.target_stage or "").strip()
        if field_name not in {"title", "annotation"}:
            return ScopedRevisionResult(
                action_id=action_id,
                status="skipped",
                target_kind="field",
                target_stage=request.target_stage,
                target_selector=request.target_selector,
                target_id=target.id if target else request.target_selector,
                scope=request.scope,
                issues=["Target field was not found"],
            )

        original = self._field_value(context, field_name)
        revised, issues = self._revise_field(original, request, field_name=field_name)
        if issues:
            return ScopedRevisionResult(
                action_id=action_id,
                status="rejected",
                target_kind="field",
                target_stage=request.target_stage,
                target_selector=request.target_selector,
                target_id=target.id if target else field_name,
                target_label=target.label if target else field_name,
                scope=request.scope,
                issues=issues,
            )

        self._set_field_value(context, field_name, revised)
        self._invalidate_downstream_context(context, request)
        return ScopedRevisionResult(
            action_id=action_id,
            status="applied",
            target_kind="field",
            target_stage=request.target_stage,
            target_selector=request.target_selector,
            target_id=target.id if target else field_name,
            target_label=target.label if target else field_name,
            scope=request.scope,
            changed=revised != original,
            changed_chars=len(revised) - len(original),
            recommended_resume_node=self._recommended_node_for_stage(request.target_stage),
            issues=[],
            diff_preview=self._diff_preview(original, revised, fromfile=target.label if target else field_name),
            before_hash=self._hash_text(original),
            after_hash=self._hash_text(revised),
        )

    def build_resume_plan(
        self,
        current_start_index: int,
        execution_plan: list[str],
        results: list[ScopedRevisionResult] | None,
    ) -> ScopedResumePlan:
        """Build an auditable plan for resuming after scoped revisions."""
        total_nodes = len(execution_plan)
        original_index = max(0, min(int(current_start_index or 0), total_nodes))
        start_index = original_index
        applied_action_ids: list[str] = []
        ignored_action_ids: list[str] = []
        reasons: list[str] = []

        for result in results or []:
            if result.status != "applied":
                ignored_action_ids.append(result.action_id)
                reasons.append(f"{result.action_id}: status={result.status}")
                continue
            if not result.changed:
                ignored_action_ids.append(result.action_id)
                reasons.append(f"{result.action_id}: unchanged")
                continue
            if not result.recommended_resume_node:
                ignored_action_ids.append(result.action_id)
                reasons.append(f"{result.action_id}: no recommended resume node")
                continue
            try:
                node_index = execution_plan.index(result.recommended_resume_node)
            except ValueError:
                ignored_action_ids.append(result.action_id)
                reasons.append(f"{result.action_id}: unknown node {result.recommended_resume_node}")
                continue

            applied_action_ids.append(result.action_id)
            if node_index < start_index:
                reasons.append(
                    f"{result.action_id}: {result.target_stage} invalidates from {result.recommended_resume_node}"
                )
            start_index = min(start_index, node_index)

        invalidated_nodes = execution_plan[start_index:original_index] if start_index < original_index else []
        return ScopedResumePlan(
            original_resume_from_index=original_index,
            resume_from_index=start_index,
            original_resume_node=execution_plan[original_index] if original_index < total_nodes else "completed",
            resume_node=execution_plan[start_index] if start_index < total_nodes else "completed",
            moved_back=start_index < original_index,
            invalidated_nodes=invalidated_nodes,
            applied_action_ids=applied_action_ids,
            ignored_action_ids=ignored_action_ids,
            reasons=reasons,
        )

    @staticmethod
    def trim_previous_steps_for_resume(
        previous_steps: list[Any] | None,
        resume_from_index: int,
        execution_plan: list[str],
    ) -> list[Any]:
        """Drop stale trace steps for nodes that will be rerun after a moved-back resume."""
        if not previous_steps:
            return []
        node_positions = {node_id: index for index, node_id in enumerate(execution_plan)}
        keep_before = max(0, min(int(resume_from_index or 0), len(execution_plan)))
        trimmed: list[Any] = []
        for step in previous_steps:
            node_id = getattr(step, "node_id", None)
            if node_id is None and isinstance(step, dict):
                node_id = step.get("node_id")
            node_index = node_positions.get(str(node_id or ""))
            if node_index is None or node_index < keep_before:
                trimmed.append(step)
        return trimmed

    @staticmethod
    def _latest_diff_approved_ids(context: dict[str, Any]) -> set[str]:
        actions = list(context.get("methodology_review_actions") or [])
        terminal_index: int | None = None
        for index in range(len(actions) - 1, -1, -1):
            action = actions[index]
            if isinstance(action, dict) and action.get("action") in {"approved", "rejected"}:
                terminal_index = index
                break

        if terminal_index is not None:
            terminal_action = actions[terminal_index]
            if not isinstance(terminal_action, dict) or terminal_action.get("action") != "approved":
                return set()
            cycle_start = 0
            for index in range(terminal_index - 1, -1, -1):
                action = actions[index]
                if isinstance(action, dict) and action.get("action") in {"approved", "rejected"}:
                    cycle_start = index + 1
                    break
            actions = actions[cycle_start : terminal_index + 1]

        approved_ids: set[str] = set()
        for action in actions:
            if not isinstance(action, dict) or action.get("action") != "diff_approved":
                continue
            details = action.get("details") if isinstance(action.get("details"), dict) else {}
            approved_ids.update(str(item) for item in details.get("approved_action_ids") or [] if item)
        return approved_ids

    def _apply_markdown_revision(
        self,
        context: dict[str, Any],
        request: MethodologistChangeRequest,
        action_id: str,
    ) -> ScopedRevisionResult:
        markdown = str(context.get("markdown") or "")
        section = self._resolve_markdown_section(markdown, request)
        if section is None:
            return ScopedRevisionResult(
                action_id=action_id,
                status="skipped",
                target_kind="markdown_section",
                target_stage=request.target_stage,
                target_selector=request.target_selector,
                target_id=request.target_selector,
                scope=request.scope,
                issues=["Target markdown section was not found"],
            )

        revised_section, issues = self._revise_text(section.text, request, target_label=section.heading)
        if issues:
            return ScopedRevisionResult(
                action_id=action_id,
                status="rejected",
                target_kind="markdown_section",
                target_stage=request.target_stage,
                target_selector=request.target_selector,
                target_id=section.target_id,
                target_label=section.heading,
                scope=request.scope,
                issues=issues,
            )

        revised_section = self._preserve_markdown_boundaries(
            original_section=section.text,
            revised_section=revised_section,
            suffix=markdown[section.end :],
        )
        revised_markdown = markdown[: section.start] + revised_section + markdown[section.end :]
        if markdown[: section.start] != revised_markdown[: section.start]:
            return self._scope_violation_result(action_id, request, "prefix changed outside target section")
        suffix_start = section.start + len(revised_section)
        if markdown[section.end :] != revised_markdown[suffix_start:]:
            return self._scope_violation_result(action_id, request, "suffix changed outside target section")

        context["markdown"] = revised_markdown
        self._invalidate_downstream_context(context, request)
        if request.target_stage in {"annotation", "skeleton"}:
            self._sync_title_annotation_from_markdown(context)
        return ScopedRevisionResult(
            action_id=action_id,
            status="applied",
            target_kind="markdown_section",
            target_stage=request.target_stage,
            target_selector=request.target_selector,
            target_id=section.target_id,
            target_label=section.heading,
            scope=request.scope,
            changed=revised_section != section.text,
            changed_chars=len(revised_section) - len(section.text),
            recommended_resume_node=self._recommended_node_for_stage(request.target_stage),
            issues=[],
            diff_preview=self._diff_preview(section.text, revised_section, fromfile=section.heading),
            before_hash=self._hash_text(section.text),
            after_hash=self._hash_text(revised_section),
        )

    def _apply_material_revision(
        self,
        context: dict[str, Any],
        request: MethodologistChangeRequest,
        action_id: str,
    ) -> ScopedRevisionResult:
        dataset_files = list(context.get("dataset_files") or [])
        target = self._resolve_target(context, request, kind="material_file")
        target_index = self._find_material_index(dataset_files, request.target_selector, target=target)
        if target_index is None:
            return ScopedRevisionResult(
                action_id=action_id,
                status="skipped",
                target_kind="material_file",
                target_stage=request.target_stage,
                target_selector=request.target_selector,
                target_id=request.target_selector,
                scope=request.scope,
                issues=["Target material file was not found"],
            )

        file_item = dict(dataset_files[target_index])
        original_bytes = file_item.get("data") or b""
        if isinstance(original_bytes, str):
            original_text = original_bytes
        else:
            original_text = bytes(original_bytes).decode("utf-8", errors="replace")

        revised_text, issues = self._revise_text(original_text, request, target_label=str(file_item.get("path") or "material"))
        if issues:
            return ScopedRevisionResult(
                action_id=action_id,
                status="rejected",
                target_kind="material_file",
                target_stage=request.target_stage,
                target_selector=request.target_selector,
                target_id=target.id if target else request.target_selector,
                target_label=str(file_item.get("path") or ""),
                scope=request.scope,
                issues=issues,
            )

        material_issues = find_non_raw_material_issues(revised_text)
        if material_issues:
            return ScopedRevisionResult(
                action_id=action_id,
                status="rejected",
                target_kind="material_file",
                target_stage=request.target_stage,
                target_selector=request.target_selector,
                target_id=target.id if target else request.target_selector,
                target_label=str(file_item.get("path") or ""),
                scope=request.scope,
                issues=[f"raw_evidence_contract_violation:{issue}" for issue in material_issues],
            )

        file_item["data"] = revised_text.encode("utf-8")
        dataset_files[target_index] = file_item
        context["dataset_files"] = dataset_files
        self._invalidate_downstream_context(context, request)
        return ScopedRevisionResult(
            action_id=action_id,
            status="applied",
            target_kind="material_file",
            target_stage=request.target_stage,
            target_selector=request.target_selector,
            target_id=target.id if target else request.target_selector,
            target_label=str(file_item.get("path") or ""),
            scope=request.scope,
            changed=revised_text != original_text,
            changed_chars=len(revised_text) - len(original_text),
            recommended_resume_node=self._recommended_node_for_stage(request.target_stage),
            issues=[],
            diff_preview=self._diff_preview(original_text, revised_text, fromfile=str(file_item.get("path") or "material")),
            before_hash=self._hash_text(original_text),
            after_hash=self._hash_text(revised_text),
        )

    def _revise_text(
        self,
        text: str,
        request: MethodologistChangeRequest,
        *,
        target_label: str,
    ) -> tuple[str, list[str]]:
        original = text or ""
        allow_display_block_edit = self._allows_display_block_edit(request)
        protected, blocks = self.block_contract.protect(
            original,
            protect_mermaid=not allow_display_block_edit,
            protect_tables=not allow_display_block_edit,
        )
        display_block_instruction = (
            "Если инструкция касается Mermaid-диаграммы или Markdown-таблицы, исправь только этот блок "
            "внутри выбранного фрагмента. Для Mermaid запрещены %%{init}, classDef, class, style, "
            "linkStyle и ручные fill/stroke/color/background-стили: приложение само задаёт светлое "
            "оформление. Для Markdown-таблиц сохраняй строку заголовков, строку-разделитель и формат | ... |."
            if allow_display_block_edit
            else ""
        )
        system = (
            "Ты редактор учебного контента. Исправляй только переданный фрагмент или файл. "
            "Верни только обновленный markdown/text без комментариев. "
            "Не меняй маркеры [[[BLOCK_N]]] и не добавляй готовые ответы за студента."
        )
        user = "\n".join(
            [
                f"Target: {target_label}",
                f"Stage: {request.target_stage}",
                f"Scope: {request.scope}",
                f"Selector: {request.target_selector or '-'}",
                f"Instruction: {request.instruction}",
                f"Expected outcome: {request.expected_outcome or '-'}",
                f"Forbidden changes: {', '.join(request.forbidden_changes or []) or '-'}",
                display_block_instruction,
                self.block_contract.protection_instruction(
                    blocks,
                    allow_display_block_edit=allow_display_block_edit,
                ).strip(),
                "",
                "ФРАГМЕНТ ДЛЯ ПРАВКИ:",
                protected,
            ]
        )
        edited = self.llm.complete(
            system=system,
            user=user,
            temperature=0.1,
            use_cache=False,
        ).strip()
        placeholder_issues = self._validate_protected_placeholders(edited, len(blocks))
        if placeholder_issues:
            return original, placeholder_issues
        restored = self.block_contract.restore(edited, blocks)
        validation_issues = self.block_contract.validate(restored)
        if validation_issues:
            return original, validation_issues
        return restored, []

    @staticmethod
    def _allows_display_block_edit(request: MethodologistChangeRequest) -> bool:
        """Allow Mermaid/table edits only when the methodologist explicitly asks for them."""
        haystack = " ".join(
            [
                request.instruction or "",
                request.target_selector or "",
                request.expected_outcome or "",
                " ".join(request.issue_codes or []),
            ]
        )
        return bool(_DISPLAY_BLOCK_REQUEST_RE.search(haystack))

    def _revise_field(
        self,
        text: str,
        request: MethodologistChangeRequest,
        *,
        field_name: str,
    ) -> tuple[str, list[str]]:
        label = "название проекта" if field_name == "title" else "аннотация"
        system = (
            "Ты редактор учебного контента. Исправляй только указанное поле. "
            "Верни только новое значение поля без markdown-разметки, комментариев и пояснений."
        )
        user = "\n".join(
            [
                f"Поле: {label}",
                f"Instruction: {request.instruction}",
                f"Expected outcome: {request.expected_outcome or '-'}",
                f"Forbidden changes: {', '.join(request.forbidden_changes or []) or '-'}",
                "",
                "ТЕКУЩЕЕ ЗНАЧЕНИЕ:",
                text or "",
            ]
        )
        edited = self.llm.complete(
            system=system,
            user=user,
            temperature=0.1,
            use_cache=False,
        ).strip()
        edited = re.sub(r"^#+\s*", "", edited).strip(" \t\r\n\"'`")
        edited = re.sub(r"\s+", " ", edited).strip()
        if not edited:
            return text or "", ["LLM returned an empty field value"]
        if field_name == "title":
            first_line = edited.splitlines()[0].strip()
            if len(first_line.split()) > 12:
                return text or "", ["Project title is too long after revision"]
            return first_line, []
        return edited, []

    @staticmethod
    def _preserve_markdown_boundaries(
        *,
        original_section: str,
        revised_section: str,
        suffix: str,
    ) -> str:
        revised = revised_section or ""
        if original_section.startswith("\n") and not revised.startswith("\n"):
            revised = "\n\n" + revised.lstrip()
        if suffix.startswith("#") and revised.strip() and not revised.endswith("\n"):
            revised = revised.rstrip() + "\n\n"
        return revised

    def _resolve_markdown_section(
        self,
        markdown: str,
        request: MethodologistChangeRequest,
    ) -> _MarkdownSection | None:
        target = self._resolve_target({"markdown": markdown}, request, kind="markdown_section")
        if target is not None and target.start is not None and target.end is not None:
            return _MarkdownSection(
                start=target.start,
                end=target.end,
                text=markdown[target.start : target.end],
                heading=target.label,
                target_id=target.id,
            )

        selector = (request.target_selector or self._default_selector(request.target_stage)).strip()
        if not selector:
            return None

        matches = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", markdown, flags=re.MULTILINE))
        selector_norm = self._norm(selector)
        for index, match in enumerate(matches):
            level = len(match.group(1))
            heading = match.group(2).strip()
            heading_norm = self._norm(heading)
            if selector_norm not in heading_norm and heading_norm not in selector_norm:
                continue
            end = len(markdown)
            for next_match in matches[index + 1 :]:
                if len(next_match.group(1)) <= level:
                    end = next_match.start()
                    break
            return _MarkdownSection(
                start=match.start(),
                end=end,
                text=markdown[match.start() : end],
                heading=heading,
                target_id="",
            )
        return None

    @staticmethod
    def _resolve_target(
        context: dict[str, Any],
        request: MethodologistChangeRequest,
        *,
        kind: str,
    ) -> SectionTarget | None:
        registry = build_section_target_registry(context)
        target = registry.find(request.target_selector, kind=kind)
        if target is not None:
            return target
        if kind == "markdown_section":
            return registry.find(
                request.target_selector or ScopedRevisionExecutor._default_selector(request.target_stage),
                kind="markdown_section",
                stage=request.target_stage
                if request.target_stage in {"annotation", "skeleton", "theory", "practice", "final"}
                else None,
            )
        return None

    @staticmethod
    def _field_value(context: dict[str, Any], field_name: str) -> str:
        if field_name == "title":
            return str(context.get("title") or "")
        annotation = context.get("annotation")
        if isinstance(annotation, dict):
            return str(annotation.get("text") or "")
        if hasattr(annotation, "text"):
            return str(annotation.text or "")
        return ""

    @staticmethod
    def _set_field_value(context: dict[str, Any], field_name: str, value: str) -> None:
        if field_name == "title":
            context["title"] = value
            markdown = str(context.get("markdown") or "")
            if markdown:
                context["markdown"] = re.sub(r"^#\s+.+?$", f"# {value}", markdown, count=1, flags=re.MULTILINE)
            return

        annotation = context.get("annotation")
        if isinstance(annotation, dict):
            annotation["text"] = value
            annotation["chars"] = len(value)
        elif hasattr(annotation, "text") and hasattr(annotation, "chars"):
            annotation.text = value
            annotation.chars = len(value)
        else:
            context["annotation"] = {"text": value, "chars": len(value)}

    @staticmethod
    def _default_selector(stage: str) -> str:
        return {
            "skeleton": "Глава 1",
            "annotation": "annotation",
            "theory": "Глава 2",
            "practice": "Глава 3",
        }.get(stage, "")

    @staticmethod
    def _find_material_index(
        dataset_files: list[Any],
        selector: str,
        *,
        target: SectionTarget | None = None,
    ) -> int | None:
        if not dataset_files:
            return None
        if target is not None:
            target_path = str(target.metadata.get("path") or target.selector or "").replace("\\", "/").lower()
            for index, item in enumerate(dataset_files):
                if isinstance(item, dict) and str(item.get("path") or "").replace("\\", "/").lower() == target_path:
                    return index
        selector_norm = selector.replace("\\", "/").lower().strip()
        if not selector_norm and len(dataset_files) == 1:
            return 0
        for index, item in enumerate(dataset_files):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").replace("\\", "/").lower()
            if selector_norm and (selector_norm == path or selector_norm in path or path in selector_norm):
                return index
        return None

    @staticmethod
    def _targets_material(request: MethodologistChangeRequest) -> bool:
        selector = (request.target_selector or "").replace("\\", "/").lower()
        return request.scope == "materials_only" or request.target_stage == "dataset" or selector.startswith("materials/")

    @staticmethod
    def _targets_field(context: dict[str, Any], request: MethodologistChangeRequest) -> bool:
        if request.target_stage == "title":
            return True
        target = ScopedRevisionExecutor._resolve_target(context, request, kind="field")
        return target is not None

    @staticmethod
    def _targets_markdown(request: MethodologistChangeRequest) -> bool:
        return request.target_stage in {"annotation", "skeleton", "theory", "practice", "final"}

    @staticmethod
    def _recommended_node_for_stage(stage: str) -> str | None:
        return {
            "title": "skeleton",
            "annotation": "theory",
            "skeleton": "theory",
            "theory": "practice",
            "practice": "global_quality",
            "dataset": "global_quality",
            "final": "evaluation",
        }.get(stage)

    @staticmethod
    def _invalidate_downstream_context(context: dict[str, Any], request: MethodologistChangeRequest) -> None:
        stage = request.target_stage
        if stage in {"title", "annotation", "skeleton", "theory"}:
            context["practice_tasks"] = []
            context["dataset_files"] = []
            context["practice_critic_issues"] = []
        if stage in {"title", "annotation", "skeleton", "theory"}:
            context["theory_parts"] = []
        if stage in {"practice", "dataset"}:
            context["practice_tasks"] = []
            context["practice_critic_issues"] = []
        if stage in {"title", "annotation", "skeleton", "theory", "practice", "dataset", "final"}:
            context["rubric_json"] = {}
            context.pop("translated_markdown", None)

    @staticmethod
    def _validate_protected_placeholders(edited: str, block_count: int) -> list[str]:
        issues: list[str] = []
        for block_id in range(block_count):
            marker = f"[[[BLOCK_{block_id}]]]"
            count = edited.count(marker)
            if count != 1:
                issues.append(f"protected block marker {marker} count is {count}, expected 1")
        return issues

    @staticmethod
    def _scope_violation_result(
        action_id: str,
        request: MethodologistChangeRequest,
        issue: str,
    ) -> ScopedRevisionResult:
        return ScopedRevisionResult(
            action_id=action_id,
            status="rejected",
            target_kind="markdown_section",
            target_stage=request.target_stage,
            target_selector=request.target_selector,
            target_id=request.target_selector,
            scope=request.scope,
            issues=[issue],
        )

    @staticmethod
    def action_id_for_action(
        action: dict[str, Any],
        index: int,
        request: MethodologistChangeRequest,
    ) -> str:
        """Build a stable id for a stored methodologist change action."""
        fingerprint = "|".join(
            [
                str(action.get("timestamp") or index),
                request.target_stage,
                request.scope,
                request.target_selector,
                request.instruction,
            ]
        )
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _sync_title_annotation_from_markdown(context: dict[str, Any]) -> None:
        markdown = str(context.get("markdown") or "")
        title_match = re.search(r"^#\s+(.+?)\s*$", markdown, flags=re.MULTILINE)
        if not title_match:
            return
        title = title_match.group(1).strip()
        next_heading = re.search(r"^##\s+", markdown[title_match.end() :], flags=re.MULTILINE)
        end = len(markdown) if next_heading is None else title_match.end() + next_heading.start()
        annotation_text = markdown[title_match.end() : end].strip()
        context["title"] = title
        annotation = context.get("annotation")
        if isinstance(annotation, dict):
            annotation["text"] = annotation_text
            annotation["chars"] = len(annotation_text)
        elif hasattr(annotation, "text") and hasattr(annotation, "chars"):
            annotation.text = annotation_text
            annotation.chars = len(annotation_text)
        else:
            context["annotation"] = {"text": annotation_text, "chars": len(annotation_text)}

    @staticmethod
    def _diff_preview(original: str, revised: str, *, fromfile: str) -> list[str]:
        if original == revised:
            return []
        diff = list(
            difflib.unified_diff(
                (original or "").splitlines(),
                (revised or "").splitlines(),
                fromfile=f"before:{fromfile}",
                tofile=f"after:{fromfile}",
                lineterm="",
                n=3,
            )
        )
        max_lines = 160
        if len(diff) > max_lines:
            return diff[:max_lines] + [f"... diff truncated: {len(diff) - max_lines} more lines"]
        return diff

    @staticmethod
    def _hash_text(value: str) -> str:
        return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _norm(value: str) -> str:
        normalized = re.sub(r"[^\wа-яА-ЯёЁ0-9]+", " ", value.lower(), flags=re.I)
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def _sync_state(context: dict[str, Any]) -> None:
        state = context.get("state")
        if hasattr(state, "sync_from_context"):
            state.sync_from_context(context)

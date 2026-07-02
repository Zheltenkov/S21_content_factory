"""Проверка Раздела 3: Единый сторителлинг (3.1-3.2)."""

import json
import re

from ...config.thresholds import THRESHOLDS
from ...models.criteria_models import CheckMethod, CriteriaItem, StrictnessLevel
from ...models.readme_document import ReadmeDocument
from ...utils.logging import safe_print
from ...utils.text_analysis import clean_markdown_prose_for_counting
from .document_utils import (
    chapter_prose_text,
    document_paragraphs,
    intro_content_without_instruction,
    practice_brief_from_document,
)
from .similarity import SimilarityCalculator


class Section3Checker:
    """Проверяет единый сторителлинг проекта."""

    def __init__(self, similarity_calculator: SimilarityCalculator, llm_client=None):
        """
        Инициализация checker'а.
        
        Args:
            similarity_calculator: Калькулятор семантического сходства
            llm_client: LLM клиент для AI-проверок
        """
        self.similarity_calc = similarity_calculator
        self.llm = llm_client

    def _split_paragraphs(self, md: str) -> list[str]:
        """
        Делит документ на абзацы для анализа когерентности.
        
        Правила:
        - Абзац = блок текста, разделённый одной или более пустыми строками.
        - Отбрасываем совсем короткие куски (например, одиночные слова/фразы).
        - Отбрасываем чистые заголовки (#, ##, ###).
        """
        # Режем по пустым строкам
        raw_paragraphs = re.split(r'\n\s*\n', md)
        paragraphs: list[str] = []

        min_length = THRESHOLDS.get("paragraph_min_length", 40)

        for p in raw_paragraphs:
            p = p.strip()
            if not p:
                continue

            # Пропускаем заголовки
            if re.match(r'^#{1,3}\s+', p):
                continue

            # Пропускаем совсем короткие обрывки
            if len(p) < min_length:
                continue

            paragraphs.append(p)

        return paragraphs

    def _paragraph_coherence(self, md: str) -> tuple[float, list[float]]:
        """
        Считает средний SBERT-score между соседними абзацами.
        Использует оптимизированный универсальный метод с batch embedding.
        
        Returns:
            avg_score: средний score (0..1)
            pairwise_scores: список всех соседних score (для деталей в отчёте)
        """
        paragraphs = self._split_paragraphs(md)
        if len(paragraphs) < 2:
            return 0.0, []

        # Используем оптимизированный метод для последовательных сравнений
        return self.similarity_calc.compute_sequential_similarities(paragraphs, use_batch_embedding=True)

    def _paragraph_coherence_document(self, document: ReadmeDocument) -> tuple[float, list[float]]:
        """Compute coherence from typed prose blocks without rendering the whole README."""
        paragraphs = document_paragraphs(
            document,
            min_length=THRESHOLDS.get("paragraph_min_length", 40),
        )
        if len(paragraphs) < 2:
            return 0.0, []
        return self.similarity_calc.compute_sequential_similarities(paragraphs, use_batch_embedding=True)

    @staticmethod
    def _extract_chapter(md: str, chapter_number: int) -> str:
        """Извлекает содержимое главы без следующей главы."""
        match = re.search(
            rf"(##\s+Глава\s+{chapter_number}[^\n]*\n)(.*?)(?=\n##\s+Глава\s+{chapter_number + 1}|\n##\s+Бонус|\Z)",
            md,
            flags=re.S,
        )
        return match.group(2).strip() if match else ""

    @staticmethod
    def _strip_instruction(chapter_1: str) -> str:
        """Для narrative focus оставляет введение без статической инструкции."""
        intro = re.search(r"###\s+Введение[^\n]*\n(.*?)(?=\n###\s+Инструкция|\Z)", chapter_1, flags=re.S)
        return intro.group(1).strip() if intro else chapter_1

    @staticmethod
    def _extract_practice_brief(chapter_3: str) -> str:
        """Сжимает практику до названий задач, целей и ожидаемых результатов."""
        blocks: list[str] = []
        for match in re.finditer(r"^###\s+Задани(?:е|я)\s+\d+\.\s+(.+)$", chapter_3, flags=re.M):
            start = match.end()
            next_match = re.search(r"^###\s+Задани(?:е|я)\s+\d+\.", chapter_3[start:], flags=re.M)
            end = start + next_match.start() if next_match else len(chapter_3)
            body = chapter_3[start:end]
            action = re.search(r"\*\*Что нужно сделать:?\*\*\s*(.+?)(?=\n\*\*|\Z)", body, flags=re.S | re.I)
            action_text = action.group(1) if action else ""
            goal = re.search(
                r"(?:^|\n)\s*Цель:\s*(.+?)(?=\n\s*(?:Подход:|Исходные данные:)|\Z)",
                action_text,
                flags=re.S | re.I,
            )
            result = re.search(r"\*\*Что должно получиться:?\*\*\s*(.+?)(?=\n\*\*|\Z)", body, flags=re.S | re.I)
            blocks.append(
                "\n".join(
                    part for part in [
                        f"Задание: {match.group(1).strip()}",
                        f"Цель: {goal.group(1).strip()}" if goal else "",
                        f"Результат: {result.group(1).strip()}" if result else "",
                    ] if part
                )
            )
        return "\n\n".join(blocks)

    def _check_narrative_focus_parts(
        self,
        *,
        context_parts: dict[str, str],
        project_ids: list[str],
    ) -> tuple[bool, list[str], dict[str, object], bool]:
        """Check narrative focus over prepared context from legacy Markdown or typed document."""
        if not any(context_parts.values()):
            return False, ["Недостаточно контекста для проверки нарратива"], {"context_parts": context_parts}, False

        details: dict[str, object] = {
            "context_parts": context_parts,
            "project_ids": project_ids,
            "mode": "script",
        }
        if len(project_ids) > 1:
            return False, [f"В тексте смешаны разные project_id: {', '.join(project_ids[:5])}"], details, False

        if not self.llm:
            comments = [] if all(context_parts.values()) else ["Не все главы содержат проверяемый narrative context"]
            return not comments, comments, details, False

        try:
            prompt = f"""Проверь, сохраняет ли проект единый нарративный фокус.

Требования:
- один рабочий кейс/продукт/проект должен проходить через введение, теорию и практику;
- статическая инструкция не учитывается как нарратив;
- внешние примеры допустимы, если они короткие, поддерживают объяснение и возвращают читателя к основному проекту;
- считай drift только ситуацию, где внешний пример или чужая тема доминирует над основным проектом и вытесняет его;
- чужие учебные направления, чужой project_id или случайные технологии считаются drift.

Введение:
{context_parts["intro"]}

Теория:
{context_parts["theory"]}

Практика:
{context_parts["practice"]}

Верни только JSON:
{{
  "has_unified_focus": true/false,
  "anchors": ["повторяющиеся смысловые якоря"],
  "drift": ["что выбивается из основного кейса"],
  "reason": "краткое объяснение"
}}"""

            response = self.llm.complete(
                system="Ты эксперт по анализу образовательных текстов.",
                user=prompt,
                response_format="json_object",
                temperature=0.1
            )

            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                ok = bool(data.get("has_unified_focus", False))
                details.update({
                    "mode": "hybrid",
                    "anchors": data.get("anchors", []),
                    "drift": data.get("drift", []),
                    "reason": data.get("reason", ""),
                })
                comments = [] if ok else [
                    data.get("reason") or "Проект не сохраняет единый рабочий кейс между главами"
                ]
                return ok, comments, details, True
        except Exception as exc:
            details["ai_error"] = str(exc)

        return False, ["Не удалось подтвердить единый narrative focus"], details, True

    def _check_narrative_focus(self, md: str) -> tuple[bool, list[str], dict[str, object], bool]:
        """Проверяет, что главы удерживают один рабочий кейс и не утекают в чужие контексты."""
        chapter_1 = self._extract_chapter(md, 1)
        chapter_2 = self._extract_chapter(md, 2)
        chapter_3 = self._extract_chapter(md, 3)

        intro = clean_markdown_prose_for_counting(self._strip_instruction(chapter_1))
        theory = clean_markdown_prose_for_counting(chapter_2)
        practice = clean_markdown_prose_for_counting(self._extract_practice_brief(chapter_3) or chapter_3)

        context_parts = {
            "intro": intro[:1200],
            "theory": theory[:2200],
            "practice": practice[:1800],
        }
        project_ids = sorted(set(re.findall(r"\b[A-Za-zА-Яа-я]{2,}\d+_[A-Za-zА-Яа-я0-9_]+\b", md)))
        return self._check_narrative_focus_parts(context_parts=context_parts, project_ids=project_ids)

    def _check_narrative_focus_document(
        self,
        document: ReadmeDocument,
    ) -> tuple[bool, list[str], dict[str, object], bool]:
        """Typed version of the narrative focus check using parsed chapter sections."""
        chapter_2 = chapter_prose_text(document, 2)
        chapter_3 = chapter_prose_text(document, 3)

        intro = clean_markdown_prose_for_counting(intro_content_without_instruction(document))
        theory = clean_markdown_prose_for_counting(chapter_2)
        practice = clean_markdown_prose_for_counting(practice_brief_from_document(document) or chapter_3)

        context_parts = {
            "intro": intro[:1200],
            "theory": theory[:2200],
            "practice": practice[:1800],
        }
        project_text = "\n\n".join(context_parts.values())
        project_ids = sorted(set(re.findall(r"\b[A-Za-zА-Яа-я]{2,}\d+_[A-Za-zА-Яа-я0-9_]+\b", project_text)))
        return self._check_narrative_focus_parts(context_parts=context_parts, project_ids=project_ids)

    def _build_items(
        self,
        *,
        average_similarity: float,
        pairwise_scores: list[float],
        narrative_result: tuple[bool, list[str], dict[str, object], bool],
    ) -> list[CriteriaItem]:
        """Build Section 3 rubric items from prepared typed or Markdown analysis inputs."""
        items = []

        safe_print("    🔗 3.1: Проверка когерентности текста (SBERT между абзацами)...", flush=True)

        if not pairwise_scores:
            items.append(CriteriaItem(
                id="3.1",
                title="Проверка когерентности (связности) текста",
                description="Логические переходы и семантическая близость между частями проекта",
                check_method=CheckMethod.SBERT if self.similarity_calc.embedding_function else CheckMethod.SCRIPT,
                score=0,
                comments=["Недостаточно абзацев для оценки (нужно ≥ 2 содержательных абзаца)"],
                parent_id="3",
                details={"average_similarity": average_similarity, "pairwise_scores": pairwise_scores},
                strictness=StrictnessLevel.SOFT,
            ))
        else:
            threshold = max(0.5, THRESHOLDS.get("coherence_sbert_threshold", 0.5))
            eps = 1e-6
            items.append(CriteriaItem(
                id="3.1",
                title="Проверка когерентности (связности) текста",
                description=f"Средний SBERT-score между соседними абзацами ≥ {threshold}",
                check_method=CheckMethod.SBERT if self.similarity_calc.embedding_function else CheckMethod.SCRIPT,
                score=1 if average_similarity + eps >= threshold else 0,
                comments=[] if average_similarity + eps >= threshold else [
                    f"Низкая когерентность: средний score {average_similarity:.2f} (< {threshold})"
                ],
                parent_id="3",
                details={
                    "average_similarity": average_similarity,
                    "threshold": threshold,
                    "pairwise_scores": pairwise_scores,
                },
            ))

        safe_print(
            f"      {'✅' if items[-1].score == 1 else '❌'} 3.1: "
            f"Связность {items[-1].details.get('average_similarity', 0):.2f} "
            f"(порог: {items[-1].details.get('threshold', 0)})",
            flush=True,
        )

        safe_print("    🔗 3.2: Проверка единого нарративного фокуса (hybrid)...", flush=True)
        narrative_ok, narrative_comments, narrative_details, used_ai = narrative_result
        items.append(CriteriaItem(
            id="3.2",
            title="Проверка единого нарративного фокуса",
            description="Весь проект сохраняет единый контекст и окружение",
            check_method=CheckMethod.HYBRID if used_ai else CheckMethod.SCRIPT,
            score=1 if narrative_ok else 0,
            comments=narrative_comments,
            parent_id="3",
            details=narrative_details,
            strictness=StrictnessLevel.SOFT,
        ))

        safe_print(f"      {'✅' if items[-1].score == 1 else '❌'} 3.2: {items[-1].title}", flush=True)

        return items

    def check(self, md: str, *, document: ReadmeDocument | None = None) -> list[CriteriaItem]:
        """Проверяет раздел 3: Единый сторителлинг (3.1-3.2)."""
        if document is not None:
            average_similarity, pairwise_scores = self._paragraph_coherence_document(document)
            narrative_result = self._check_narrative_focus_document(document)
        else:
            average_similarity, pairwise_scores = self._paragraph_coherence(md)
            narrative_result = self._check_narrative_focus(md)
        return self._build_items(
            average_similarity=average_similarity,
            pairwise_scores=pairwise_scores,
            narrative_result=narrative_result,
        )

    def check_document(self, document: ReadmeDocument) -> list[CriteriaItem]:
        """Проверяет раздел 3 по typed README document tree."""
        average_similarity, pairwise_scores = self._paragraph_coherence_document(document)
        return self._build_items(
            average_similarity=average_similarity,
            pairwise_scores=pairwise_scores,
            narrative_result=self._check_narrative_focus_document(document),
        )

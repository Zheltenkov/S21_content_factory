"""Проверка аннотации (2.1.1-2.1.3)."""

import json
import re
from typing import Any

from ...config.thresholds import THRESHOLDS
from ...models.criteria_models import CheckMethod, CriteriaItem, StrictnessLevel
from ...utils.logging import safe_print
from .utils import semantic_similarity as _semantic_similarity


def is_generic_title(title: str) -> bool:
    """Проверяет, является ли название общим (шаблонным)."""
    GENERIC_PATTERNS = [
        "проект по", "введение в", "основы", "базовый курс",
        "курс по", "обзор", "введение"
    ]
    title_low = title.lower()
    return any(p in title_low for p in GENERIC_PATTERNS)


def is_formal_title(title: str) -> bool:
    """Проверяет, является ли название слишком формальным/пустым."""
    t = title.lower().strip()
    if len(t) < 15:
        return True
    BAD_WORDS = ["проект", "курс", "система", "программа", "модуль", "школа"]
    words = t.split()
    meaningful = [w for w in words if w not in BAD_WORDS]
    return len(meaningful) <= 1


def get_annotation_focus(annotation: str) -> str:
    """Извлекает фокусную часть аннотации (первый абзац или первые 600 символов)."""
    # Берём первый параграф до пустой строки
    parts = annotation.strip().split("\n\n", 1)
    head = parts[0].strip()
    if len(head) > 600:
        head = head[:600]
    return head


class AnnotationChecker:
    """Проверяет аннотацию проекта."""

    def __init__(self, llm_client=None, embedding_function=None, language: str = "ru"):
        """
        Инициализация checker'а.
        
        Args:
            llm_client: LLM клиент для AI-проверок
            embedding_function: Функция для создания эмбеддингов
            language: Язык текстов
        """
        self.llm = llm_client
        self.embedding_function = embedding_function
        self.lang = language

    def _ai_check_annotation_structure(self, annotation: str) -> dict[str, bool] | None:
        """
        ИИ-проверка структуры аннотации.
        
        Returns:
            Словарь с детальной информацией о найденных элементах:
            {"has_purpose": bool, "has_content": bool, "has_outcome": bool}
            или None при ошибке
        """
        if not self.llm:
            return None

        safe_print("        🤖 ИИ-проверка структуры аннотации...", flush=True)
        try:
            prompt = f"""Проверь, содержит ли аннотация три обязательных элемента:
1. Назначение проекта (зачем, ценность)
2. Краткое описание содержания (что внутри)
3. Ожидаемый результат (что получится)

Аннотация:
{annotation}

Верни только JSON:
{{"has_purpose": true/false, "has_content": true/false, "has_outcome": true/false, "all_present": true/false}}"""

            response = self.llm.complete(
                system="Ты эксперт по анализу образовательных текстов.",
                user=prompt,
                response_format="json_object",
                temperature=0.1
            )

            import json
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                # Возвращаем детальную информацию, а не только all_present
                result = {
                    "has_purpose": data.get("has_purpose", False),
                    "has_content": data.get("has_content", False),
                    "has_outcome": data.get("has_outcome", False),
                }
                ai_count = sum(result.values())
                safe_print(f"        {'✅' if ai_count >= 2 else '❌'} ИИ-проверка завершена: найдено {ai_count}/3 элементов", flush=True)
                return result
        except Exception as e:
            safe_print(f"        ⚠️ Ошибка ИИ-проверки: {str(e)}", flush=True)
            pass

        return None

    def _score_from_ai_result(self, ai_result: dict[str, bool], kw_components_found: int) -> tuple[int, list[str], dict[str, Any]]:
        """
        Формирует результат критерия на основе LLM-ответа.
        
        Args:
            ai_result: Словарь с флагами has_purpose, has_content, has_outcome
            kw_components_found: Количество компонентов, найденных по ключевым словам (для details)
        
        Returns:
            Tuple[score, comments, details]
        """
        has_purpose = ai_result.get("has_purpose", False)
        has_content = ai_result.get("has_content", False)
        has_outcome = ai_result.get("has_outcome", False)

        ai_count = sum([has_purpose, has_content, has_outcome])

        if ai_count >= 2:
            score = 1
            comments = []

            if ai_count == 2:
                # Формируем рекомендацию о недостающем элементе
                missing = []
                if not has_purpose:
                    missing.append("назначение проекта")
                if not has_content:
                    missing.append("краткое описание содержания")
                if not has_outcome:
                    missing.append("ожидаемый результат")

                if missing:
                    comments.append(
                        f"Аннотация в целом ок, но желательно явно добавить: {', '.join(missing)}."
                    )
        else:
            score = 0
            comments = [
                f"Найдено только {ai_count} из 3 обязательных элементов "
                f"(назначение, содержание, результат). Рекомендуем дописать недостающие."
            ]

        details = {
            "has_purpose": has_purpose,
            "has_content": has_content,
            "has_outcome": has_outcome,
            "components_found": ai_count,
            "kw_components_found": kw_components_found,
            "check_method": "AI_AGENT"
        }

        return score, comments, details

    def _score_from_keywords_only(self, components_found: int) -> tuple[int, list[str], dict[str, Any]]:
        """
        Формирует результат критерия на основе проверки по ключевым словам (fallback).
        
        Args:
            components_found: Количество найденных компонентов (0-3)
        
        Returns:
            Tuple[score, comments, details]
        """
        if components_found >= 2:
            score = 1
            comments = []

            if components_found == 2:
                comments.append(
                    "Аннотация в целом ок, но желательно явно указать все три элемента: "
                    "назначение, содержание и ожидаемый результат."
                )
        else:
            score = 0
            comments = [
                f"Найдено только {components_found} из 3 обязательных элементов "
                "(назначение, содержание, результат). Рекомендуем дописать недостающие."
            ]

        details = {
            "components_found": components_found,
            "check_method": "SCRIPT"
        }

        return score, comments, details

    def _ai_check_title_annotation(
        self,
        title: str,
        annotation_focus: str,
        theory_summary: str | None,
        similarity_annotation: float | None,
        similarity_theory: float | None,
    ) -> tuple[bool, str | None]:
        """
        LLM-проверка соответствия аннотации теме проекта.
        
        Args:
            title: Название проекта
            annotation: Текст аннотации
            similarity: SBERT similarity score (для контекста)
        
        Returns:
            Tuple[matches: bool, reason: Optional[str]]
        """
        if not self.llm:
            return False, None

        try:
            theory_block = theory_summary or "Нет доступного краткого содержания теории."
            sim_ann = f"{similarity_annotation:.3f}" if similarity_annotation is not None else "нет данных"
            sim_theory = f"{similarity_theory:.3f}" if similarity_theory is not None else "нет данных"

            prompt = f"""Проверь, соответствует ли аннотация теме проекта. У тебя есть фокус аннотации и краткая суммаризация теоретической части.

Название проекта:
{title}

Фокус аннотации (короткий фрагмент):
{annotation_focus or "Аннотация отсутствует"}

Суммаризация теоретической части:
{theory_block}

Семантическое сходство (SBERT):
- Аннотация vs название: {sim_ann}
- Теория vs название: {sim_theory}

Ответь только JSON:
{{"matches": true/false, "reason": "1–2 предложения"}}"""

            response = self.llm.complete(
                system="Ты эксперт по анализу образовательных текстов. Будь толерантным: общие аннотации допустимы, если они хотя бы косвенно связаны с темой проекта.",
                user=prompt,
                response_format="json_object",
                temperature=0.2
            )

            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                matches = data.get("matches", False)
                reason = data.get("reason", "")
                return matches, reason
        except Exception as e:
            safe_print(f"        ⚠️ Ошибка LLM-проверки соответствия темы: {str(e)}", flush=True)
            pass

        return False, None

    def _summarize_theory(self, theory_text: str | None) -> str:
        if not theory_text:
            return ""
        cleaned = re.sub(r'\s+', ' ', theory_text).strip()
        if not cleaned:
            return ""
        snippet = cleaned[:4000]
        if self.llm:
            try:
                response = self.llm.complete(
                    system="Ты лаконичный редактор, делаешь выжимки теоретических разделов.",
                    user=(
                        "Суммаризируй теоретическую часть проекта в 2-3 предложениях, "
                        "без списков и цитат:\n"
                        f"{snippet}"
                    ),
                    temperature=0.1,
                )
                summary = response.strip()
                if summary:
                    return summary
            except Exception as exc:
                safe_print(f"        ⚠️ Не удалось суммаризировать главу 2: {exc}", flush=True)
        return cleaned[:800]

    def check(self, annotation: str, title: str, theory_text: str | None = None) -> list[CriteriaItem]:
        """2.1: Проверка аннотации (2.1.1-2.1.3)."""
        items = []

        # 2.1.1: Проверка длины аннотации
        annotation_clean = re.sub(r'\s+', ' ', annotation).strip()
        length = len(annotation_clean)
        lo, hi = THRESHOLDS["annotation_chars"]
        sentence_count = len([s for s in re.split(r"(?<=[.!?])\s+", annotation_clean) if s.strip()])

        if lo <= length <= hi and 2 <= sentence_count <= 4:
            items.append(CriteriaItem(
                id="2.1.1",
                title="Проверка длины аннотации",
                description=f"Корректный диапазон — {lo}–{hi} символов и 2–4 предложения",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.1"
            ))
        else:
            comments = []
            if not (lo <= length <= hi):
                comments.append(f"Аннотация {length} символов (ожидалось {lo}–{hi})")
            if not (2 <= sentence_count <= 4):
                comments.append(f"Аннотация содержит {sentence_count} предложений (ожидалось 2–4)")
            items.append(CriteriaItem(
                id="2.1.1",
                title="Проверка длины аннотации",
                description=f"Корректный диапазон — {lo}–{hi} символов и 2–4 предложения",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=comments,
                parent_id="2.1"
            ))

        # 2.1.2: Проверка структуры аннотации (ИИ)
        # Вычисляем компоненты по ключевым словам для details и fallback
        has_purpose_kw = any(
            p in annotation.lower()
            for p in [
                "зачем", "ценность", "что решает", "проблем", "назначен", "нужен",
                "для чего", "чтобы", "поможет", "понадобится",
            ]
        )
        has_content_kw = any(
            p in annotation.lower()
            for p in [
                "что внутри", "формат", "будет делать", "характер работы", "содержан",
                "дела", "работа", "темы", "действия", "инструмент", "подход",
                "метод", "практик", "разбер",
            ]
        )
        has_outcome_kw = any(
            p in annotation.lower()
            for p in [
                "результат", "получишь", "навык", "продукт", "опыт", "итог",
                "получит", "артефакт", "подготовишь", "сформируешь", "соберёшь",
                "соберешь",
            ]
        )
        kw_components_found = sum([has_purpose_kw, has_content_kw, has_outcome_kw])

        # Всегда запускаем LLM, если он доступен (не блокируем ключевыми словами)
        if self.llm and annotation.strip():
            ai_result = self._ai_check_annotation_structure(annotation)

            if ai_result is not None:
                # Используем результат LLM
                score, comments, details = self._score_from_ai_result(ai_result, kw_components_found)
                items.append(CriteriaItem(
                    id="2.1.2",
                    title="Проверка структуры аннотации",
                    description="Наличие трех элементов: назначение / что внутри / ожидаемый результат",
                    check_method=CheckMethod.AI_AGENT,
                    score=score,
                    comments=comments,
                    parent_id="2.1",
                    details=details,
                    strictness=StrictnessLevel.SOFT  # Рекомендация, не блокирует прохождение
                ))
            else:
                # LLM вернул ошибку - fallback на ключевые слова
                score, comments, details = self._score_from_keywords_only(kw_components_found)
                items.append(CriteriaItem(
                    id="2.1.2",
                    title="Проверка структуры аннотации",
                    description="Наличие трех элементов: назначение / что внутри / ожидаемый результат",
                    check_method=CheckMethod.SCRIPT,
                    score=score,
                    comments=comments,
                    parent_id="2.1",
                    details=details,
                    strictness=StrictnessLevel.SOFT  # Рекомендация, не блокирует прохождение
                ))
        else:
            # LLM недоступен - используем только ключевые слова
            score, comments, details = self._score_from_keywords_only(kw_components_found)
            items.append(CriteriaItem(
                id="2.1.2",
                title="Проверка структуры аннотации",
                description="Наличие трех элементов: назначение / что внутри / ожидаемый результат",
                check_method=CheckMethod.SCRIPT,
                score=score,
                comments=comments,
                parent_id="2.1",
                details=details,
                strictness=StrictnessLevel.SOFT  # Рекомендация, не блокирует прохождение
            ))

        theory_summary = self._summarize_theory(theory_text)

        # 2.1.3: Проверка соответствия теме проекта (SBERT + LLM)
        if title:
            # Пороги для разных зон
            SIM_HARD_OK = 0.22  # уверенно ок
            SIM_LLM_ZONE = 0.12  # ниже — почти точно мимо

            # Тримминг аннотации: сравниваем с фокусной частью
            focus = get_annotation_focus(annotation)
            similarity_annotation = _semantic_similarity(title, focus, self.lang, self.embedding_function) if focus else None
            similarity_theory = _semantic_similarity(title, theory_summary, self.lang, self.embedding_function) if theory_summary else None
            similarity = 0.0
            best_source = None
            similarities = []
            if similarity_annotation is not None:
                similarities.append(("annotation", similarity_annotation))
            if similarity_theory is not None:
                similarities.append(("theory", similarity_theory))
            if similarities:
                best_source, similarity = max(similarities, key=lambda x: x[1])
            source_label = "аннотация" if best_source == "annotation" else ("теоретическая часть" if best_source == "theory" else "аннотация")
            used_sbert = bool(self.embedding_function)

            # Проверка формальных названий (ранний выход)
            if is_formal_title(title):
                items.append(CriteriaItem(
                    id="2.1.3",
                    title="Проверка соответствия теме проекта",
                    description="Аннотация должна быть семантически связана с названием проекта",
                    check_method=CheckMethod.SBERT,
                    score=1,
                    comments=[
                        "Название проекта слишком общее, критерий 2.1.3 неинформативен. "
                        "Подумайте о более содержательном заголовке."
                    ],
                    parent_id="2.1",
                    details={
                        "similarity": similarity,
                        "formal_title": True,
                        "used_sbert": used_sbert,
                        "similarity_annotation": similarity_annotation,
                        "similarity_theory": similarity_theory,
                        "source": best_source,
                    },
                    strictness=StrictnessLevel.SOFT
                ))
            # Проверка общих названий (ранний выход)
            elif is_generic_title(title):
                soft_comment = []
                if similarity < SIM_LLM_ZONE:
                    soft_comment.append(
                        "Название проекта очень общее, поэтому критерий оценивается мягко. "
                        "Аннотация проходит проверку, но лучше уточнить тему в заголовке."
                    )
                items.append(CriteriaItem(
                    id="2.1.3",
                    title="Проверка соответствия теме проекта",
                    description="Аннотация должна быть семантически связана с названием проекта",
                    check_method=CheckMethod.SBERT,
                    score=1,
                    comments=soft_comment,
                    parent_id="2.1",
                    details={
                        "similarity": similarity,
                        "generic_title": True,
                        "used_sbert": used_sbert,
                        "similarity_annotation": similarity_annotation,
                        "similarity_theory": similarity_theory,
                        "source": best_source,
                    },
                    strictness=StrictnessLevel.SOFT
                ))
            # Мягкий fallback без SBERT
            elif not used_sbert:
                score = 1
                comments = []
                if similarity < 0.1:
                    comments.append(
                        f"Проверка без SBERT: низкое текстовое пересечение между названием и {source_label} (≈{similarity:.2f}). "
                        "Проверьте, что аннотация явно описывает тему проекта."
                    )

                items.append(CriteriaItem(
                    id="2.1.3",
                    title="Проверка соответствия теме проекта",
                    description="Аннотация должна быть семантически связана с названием проекта",
                    check_method=CheckMethod.SCRIPT,
                    score=score,
                    comments=comments,
                    parent_id="2.1",
                    details={
                        "similarity": similarity,
                        "used_sbert": False,
                        "similarity_annotation": similarity_annotation,
                        "similarity_theory": similarity_theory,
                        "source": best_source,
                    },
                    strictness=StrictnessLevel.SOFT
                ))
            # Строгая логика с порогами и LLM
            else:
                if similarity >= SIM_HARD_OK:
                    # Уверенно ок
                    score = 1
                    comments = []
                    check_method = CheckMethod.SBERT
                elif similarity >= SIM_LLM_ZONE and self.llm:
                    # Зона неопределенности - LLM-проверка
                    ai_ok, llm_reason = self._ai_check_title_annotation(
                        title,
                        focus,
                        theory_summary,
                        similarity_annotation,
                        similarity_theory,
                    )
                    score = 1 if ai_ok else 0
                    if ai_ok:
                        comments = []
                    else:
                        if llm_reason:
                            comments = [f"Модель посчитала, что аннотация не про ту же тему, что и название: {llm_reason}."]
                        else:
                            comments = [f"{source_label.capitalize()} слабо связана с темой проекта (SBERT≈{similarity:.2f})."]
                    check_method = CheckMethod.HYBRID if used_sbert else CheckMethod.AI_AGENT
                else:
                    # Низкое сходство
                    score = 0
                    if similarity < 0.05:
                        comments = [
                            f"{source_label.capitalize()} почти не связана с темой из названия. "
                            "Попробуйте явно указать, о чём проект и какие задачи он решает."
                        ]
                    else:
                        comments = [
                            f"{source_label.capitalize()} слабо связана с названием проекта. "
                            "Возможно, текст ушёл в детали и не проговаривает тему проекта явно."
                        ]
                    check_method = CheckMethod.SBERT

                items.append(CriteriaItem(
                    id="2.1.3",
                    title="Проверка соответствия теме проекта",
                    description="Аннотация должна быть семантически связана с названием проекта",
                    check_method=check_method,
                    score=score,
                    comments=comments,
                    parent_id="2.1",
                    details={
                        "similarity": similarity,
                        "similarity_annotation": similarity_annotation,
                        "similarity_theory": similarity_theory,
                        "source": best_source,
                        "threshold_hard": SIM_HARD_OK,
                        "threshold_llm": SIM_LLM_ZONE,
                        "used_sbert": used_sbert,
                    },
                    strictness=StrictnessLevel.SOFT
                ))
        else:
            items.append(CriteriaItem(
                id="2.1.3",
                title="Проверка соответствия теме проекта",
                description="Аннотация должна быть семантически связана с названием проекта",
                check_method=CheckMethod.SBERT,
                score=0,
                comments=["Нет названия проекта для сравнения"],
                parent_id="2.1"
            ))

        return items

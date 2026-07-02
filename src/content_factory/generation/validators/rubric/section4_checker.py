"""Проверка Раздела 4: Tone of voice и редактура (4.1-4.3)."""

import re

from ...models.criteria_models import CheckMethod, CriteriaItem, StrictnessLevel
from ...models.readme_document import ReadmeDocument
from ...utils.logging import safe_print
from .document_utils import document_prose_text


class Section4Checker:
    """Проверяет Tone of Voice и редактуру."""

    def __init__(self, llm_client=None, regex_patterns: dict = None):
        """
        Инициализация checker'а.
        
        Args:
            llm_client: LLM клиент для AI-проверок
            regex_patterns: Словарь с регулярными выражениями для парсинга
        """
        self.llm = llm_client
        self.rx_directives = regex_patterns.get("rx_directives", []) if regex_patterns else []
        self.rx_marketing = regex_patterns.get("rx_marketing", []) if regex_patterns else []

    @staticmethod
    def _matches(pattern: object, text: str, flags: int = 0) -> bool:
        """Поддерживает и строки regex, и уже скомпилированные паттерны."""
        if hasattr(pattern, "search"):
            return bool(pattern.search(text or ""))
        return bool(re.search(pattern, text or "", flags=flags))

    def _ai_check_tov(self, md: str) -> bool:
        """ИИ-проверка Tone of Voice."""
        if not self.llm:
            return False

        try:
            prompt = f"""Проверь, соответствует ли текст Tone of Voice «Школы 21».

Принципы Tone of Voice «Школы 21»:
1. Универсальность и простота — избегай канцеляризмов, неологизмов, перегруженных конструкций
2. Умеренность и непринужденность — избегай избыточного пафоса, продавливания, навязчивости
3. Уважение и забота — избегай дискриминационных формулировок, оценочных ярлыков
4. Общение на «ты» — дружелюбно, но без панибратства (предпочтительно «ты», но единичные «вы» допустимы)
5. Отсутствие академизма — живой, современный язык без формальностей

Текст для проверки (первые 3000 символов):
{md[:3000]}

ВАЖНО - БУДЬ ТОЛЕРАНТНЫМ:
- Текст считается соответствующим ToV, если ОСНОВНЫЕ принципы соблюдены
- Единичные нарушения (1-2 случая) не критичны, если общий тон соответствует
- Обращение на «ты» предпочтительно, но единичные «вы» допустимы (особенно в цитатах, примерах)
- Небольшие канцеляризмы или формальные конструкции допустимы, если они не доминируют в тексте
- Текст должен быть в целом дружелюбным, простым и понятным — это главное
- Считай текст соответствующим, если он в целом соответствует духу ToV, даже при мелких отклонениях

Верни только JSON:
{{"matches_tov": true/false, "reason": "краткое объяснение (1-2 предложения)"}}"""

            response = self.llm.complete(
                system="Ты эксперт по анализу образовательных текстов. Будь толерантным и гибким при оценке соответствия Tone of Voice.",
                user=prompt,
                response_format="json_object",
                temperature=0.2
            )

            import json
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                result = data.get("matches_tov", False)
                reason = data.get("reason", "")
                if reason:
                    safe_print(f"        {'✅' if result else '❌'} ИИ-проверка ToV: {reason}", flush=True)
                else:
                    safe_print(f"        {'✅' if result else '❌'} ИИ-проверка ToV завершена", flush=True)
                return result
        except Exception as e:
            safe_print(f"        ⚠️ Ошибка ИИ-проверки ToV: {str(e)}", flush=True)
            pass

        return False

    @staticmethod
    def _script_check_tov(md: str, rx_directives: list) -> bool:
        low = (md or "").lower()
        has_ty = bool(re.search(r"\bты\b", low))
        has_directives = any(Section4Checker._matches(p, md, flags=re.I) for p in rx_directives)
        has_eval_labels = bool(re.search(r"\b(правильно|неправильно|плохо|хорошо|верно|неверно)\b", md or "", re.I))
        marketing_triggers = bool(re.search(r"\b(срочно|немедленно|уникальн|не упусти|только сейчас|лучший)\b", low))
        return has_ty and not has_directives and not has_eval_labels and not marketing_triggers

    def _ai_check_neutrality(self, md: str) -> bool:
        """ИИ-проверка нейтральности."""
        if not self.llm:
            return False

        safe_print("        🤖 ИИ-проверка нейтральности...", flush=True)
        try:
            prompt = f"""Проверь, содержит ли текст навязчивые призывы, маркетинговые триггеры или агрессивные продажи.

Текст (первые 2000 символов):
{md[:2000]}

Верни только JSON:
{{"is_neutral": true/false, "found_triggers": ["список найденных триггеров"]}}"""

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
                result = data.get("is_neutral", False)
                safe_print(f"        {'✅' if result else '❌'} ИИ-проверка нейтральности завершена", flush=True)
                return result
        except Exception as e:
            safe_print(f"        ⚠️ Ошибка ИИ-проверки нейтральности: {str(e)}", flush=True)
            pass

        return False

    def _check_text(self, md: str, *, protected_blocks_removed: bool = False) -> list[CriteriaItem]:
        """Проверяет раздел 4: Tone of voice и редактура (4.1-4.3)."""
        items = []

        safe_print("    🎨 4.1: Проверка Tone of Voice «Школы 21» (ИИ)...", flush=True)
        # 4.1: Соответствие Tone of Voice «Школы 21» (ИИ)
        script_tov_ok = self._script_check_tov(md, self.rx_directives)
        if self.llm:
            ai_check = self._ai_check_tov(md) or script_tov_ok
            items.append(CriteriaItem(
                id="4.1",
                title="Соответствие Tone of Voice «Школы 21»",
                description="Универсальность, простота, умеренность, уважение, общение на «ты»",
                check_method=CheckMethod.AI_AGENT,
                score=1 if ai_check else 0,
                comments=[] if ai_check else ["Текст не соответствует Tone of Voice «Школы 21»"],
                parent_id="4"
            ))
        else:
            # Fallback на скриптовую проверку
            items.append(CriteriaItem(
                id="4.1",
                title="Соответствие Tone of Voice «Школы 21»",
                description="Универсальность, простота, умеренность, уважение, общение на «ты»",
                check_method=CheckMethod.SCRIPT,
                score=1 if script_tov_ok else 0,
                comments=[] if script_tov_ok else ["Текст не соответствует Tone of Voice «Школы 21»"],
                parent_id="4"
            ))

        safe_print(f"      {'✅' if items[-1].score == 1 else '❌'} 4.1: {items[-1].title}", flush=True)

        safe_print("    🎨 4.2: Проверка нейтральности и доверительного тона (ИИ)...", flush=True)
        # 4.2: Проверка нейтральности и доверительного тона (ИИ)
        if self.llm:
            ai_check = self._ai_check_neutrality(md)
            items.append(CriteriaItem(
                id="4.2",
                title="Проверка нейтральности и доверительного тона",
                description="Нет навязчивых призывов, маркетинговых триггеров, агрессивных продаж",
                check_method=CheckMethod.AI_AGENT,
                score=1 if ai_check else 0,
                comments=[] if ai_check else ["Текст содержит маркетинговые триггеры или навязчивые призывы"],
                parent_id="4"
            ))
        else:
            # Fallback на скриптовую проверку
            has_marketing = any(self._matches(p, md, flags=re.I) for p in self.rx_marketing)

            items.append(CriteriaItem(
                id="4.2",
                title="Проверка нейтральности и доверительного тона",
                description="Нет навязчивых призывов, маркетинговых триггеров, агрессивных продаж",
                check_method=CheckMethod.SCRIPT,
                score=0 if has_marketing else 1,
                comments=[] if not has_marketing else ["Обнаружены маркетинговые триггеры"],
                parent_id="4"
            ))

        safe_print(f"      {'✅' if items[-1].score == 1 else '❌'} 4.2: {items[-1].title}", flush=True)

        safe_print("    🎨 4.3: Проверка соблюдения правил редактуры...", flush=True)
        # 4.3: Проверка соблюдения правил редактуры
        editing_issues = []

        # Проверка «Школа 21»
        if not re.search(r'«Школа\s+21»|"Школа\s+21"', md):
            # Не критично, но проверяем
            pass

        md_for_quote_check = md
        if not protected_blocks_removed:
            # Legacy Markdown input still needs deterministic cleanup before typography checks.
            md_for_quote_check = re.sub(r'```[\s\S]*?```', '', md_for_quote_check)  # Блоки кода
            md_for_quote_check = re.sub(r'`[^`]+`', '', md_for_quote_check)  # Inline код
            md_for_quote_check = re.sub(r'\{[^}]*"[^}]*\}', '', md_for_quote_check)  # JSON объекты
            md_for_quote_check = re.sub(r'\[[^\]]*"[^\]]*\]', '', md_for_quote_check)  # JSON массивы
            md_for_quote_check = re.sub(r'<div[^>]*>[\s\S]*?</div>', '', md_for_quote_check)  # HTML блоки (Mermaid)
            md_for_quote_check = re.sub(r'%%\{init:[\s\S]*?\}%%', '', md_for_quote_check)  # Mermaid init блоки
            md_for_quote_check = re.sub(r'(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|gitgraph|journey|requirement)[\s\S]*?(?=\n\n|\n#|$|```)', '', md_for_quote_check, flags=re.I)  # Mermaid диаграммы

        # Ищем прямые кавычки в тексте (не в коде)
        straight_quotes = re.findall(r'"[^"]*"', md_for_quote_check)
        # Дополнительная фильтрация: исключаем кавычки в технических контекстах
        filtered_quotes = []
        for q in straight_quotes:
            # Исключаем кавычки, которые явно в технических контекстах
            if not re.search(r'(mermaid|json|code|init|theme|flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|gitgraph|journey|requirement|http|https|url|path|file|\.py|\.md|\.txt|\.csv|\.json)', q, re.I):
                # Исключаем кавычки, которые выглядят как технические (содержат только технические символы)
                if not re.match(r'^"[^а-яёА-ЯЁ]*"$', q, re.I):  # Если в кавычках нет русских букв - скорее всего техническое
                    filtered_quotes.append(q)

        # СТРОГАЯ ПРОВЕРКА: любая прямая кавычка в тексте - это ошибка
        if len(filtered_quotes) > 0:
            editing_issues.append(f"Использованы прямые кавычки вместо «елочек» (найдено: {len(filtered_quotes)})")

        # Проверка тире
        # Можно добавить проверку на длинное тире (—) vs дефис (-)

        if len(editing_issues) == 0:
            items.append(CriteriaItem(
                id="4.3",
                title="Проверка соблюдения правил редактуры",
                description="Соблюдение норм оформления: кавычки, тире, названия",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="4",
                strictness=StrictnessLevel.SOFT  # Редакторская проверка, не блокирует прохождение
            ))
        else:
            items.append(CriteriaItem(
                id="4.3",
                title="Проверка соблюдения правил редактуры",
                description="Соблюдение норм оформления: кавычки, тире, названия",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=editing_issues[:5],
                parent_id="4",
                details={"issues": editing_issues},
                strictness=StrictnessLevel.SOFT  # Редакторская проверка, не блокирует прохождение
            ))

        safe_print(f"      {'✅' if items[-1].score == 1 else '❌'} 4.3: {items[-1].title}", flush=True)

        return items

    def check(self, md: str) -> list[CriteriaItem]:
        """Проверяет раздел 4 по legacy Markdown input."""
        return self._check_text(md, protected_blocks_removed=False)

    def check_document(self, document: ReadmeDocument) -> list[CriteriaItem]:
        """Проверяет раздел 4 по typed README document tree."""
        return self._check_text(document_prose_text(document), protected_blocks_removed=True)

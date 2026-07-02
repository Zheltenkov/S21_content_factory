"""Проверка Раздела 2: Соответствие требованиям (2.1-2.5)."""

import concurrent.futures

from ...models.criteria_models import CriteriaItem
from ...models.readme_document import ReadmeDocument
from ...utils.logging import safe_print
from .document_utils import chapter_prose_text


class Section2Checker:
    """Проверяет соответствие требованиям (аннотация, оглавление, главы)."""

    def __init__(self, llm_client=None, embedding_function=None, language: str = "ru", regex_patterns: dict = None):
        """
        Инициализация checker'а.
        
        Args:
            llm_client: LLM клиент для AI-проверок
            embedding_function: Функция для создания эмбеддингов
            language: Язык текстов
            regex_patterns: Словарь с регулярными выражениями для парсинга
        """
        self.llm = llm_client
        self.embedding_function = embedding_function
        self.lang = language
        self.rx_h1 = regex_patterns.get("rx_h1") if regex_patterns else None
        self.rx_h2 = regex_patterns.get("rx_h2") if regex_patterns else None
        self.rx_h3 = regex_patterns.get("rx_h3") if regex_patterns else None
        self.rx_toc = regex_patterns.get("rx_toc") if regex_patterns else None
        self.rx_chapter1 = regex_patterns.get("rx_chapter1") if regex_patterns else None
        self.rx_chapter2 = regex_patterns.get("rx_chapter2") if regex_patterns else None
        self.rx_chapter3 = regex_patterns.get("rx_chapter3") if regex_patterns else None
        self.rx_theory_part = regex_patterns.get("rx_theory_part") if regex_patterns else None
        self.rx_task = regex_patterns.get("rx_task") if regex_patterns else None
        self.rx_directives = regex_patterns.get("rx_directives", []) if regex_patterns else []
        self.rx_bad_goals = regex_patterns.get("rx_bad_goals", []) if regex_patterns else []

    def check(self, md: str, learning_outcomes: list[str] | None = None) -> list[CriteriaItem]:
        """
        Проверяет раздел 2: Соответствие требованиям (2.1-2.5).
        
        Args:
            md: Markdown документ
            learning_outcomes: Список образовательных результатов
        
        Returns:
            Список CriteriaItem для раздела 2
        """
        items = []

        # Извлекаем основные блоки
        m_h1 = self.rx_h1.search(md) if self.rx_h1 else None
        annotation = ""
        if m_h1:
            chunk = md[m_h1.end():]
            annotation = chunk.split("\n## ", 1)[0].strip()

        # Извлекаем главы
        ch1_match = self.rx_chapter1.search(md) if self.rx_chapter1 else None
        ch2_match = self.rx_chapter2.search(md) if self.rx_chapter2 else None
        ch3_match = self.rx_chapter3.search(md) if self.rx_chapter3 else None

        ch1_content = ""
        ch2_content = ""
        ch3_content = ""

        if ch1_match:
            ch1_start = ch1_match.end()
            ch1_end = md.find("\n## ", ch1_start)
            if ch1_end == -1:
                ch1_end = len(md)
            ch1_content = md[ch1_start:ch1_end]

        if ch2_match:
            ch2_start = ch2_match.end()
            ch2_end = md.find("\n## ", ch2_start)
            if ch2_end == -1:
                ch2_end = len(md)
            ch2_content = md[ch2_start:ch2_end]

        if ch3_match:
            ch3_start = ch3_match.end()
            ch3_end = md.find("\n## ", ch3_start)
            if ch3_end == -1:
                ch3_end = len(md)
            ch3_content = md[ch3_start:ch3_end]

        # Параллельное выполнение независимых проверок (2.1-2.5)
        # Все проверки независимы, так как ch2_content уже извлечен
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            # Запускаем все проверки параллельно
            title = m_h1.group(1) if m_h1 else ""
            future_2_1 = executor.submit(self._check_annotation, annotation, title, ch2_content)
            future_2_2 = executor.submit(self._check_toc, md)
            future_2_3 = executor.submit(self._check_chapter1, ch1_content)
            future_2_4 = executor.submit(self._check_chapter2, ch2_content, learning_outcomes)
            future_2_5 = executor.submit(self._check_chapter3, ch3_content, ch2_content)

            # Обрабатываем результаты по мере готовности
            safe_print("    📝 2.1: Проверка аннотации...", flush=True)
            annotation_items = future_2_1.result()
            items.extend(annotation_items)
            annotation_score = sum(item.score for item in annotation_items)
            safe_print(f"      ✅ 2.1: {annotation_score}/{len(annotation_items)} подкритериев пройдено", flush=True)

            safe_print("    📝 2.2: Проверка оглавления...", flush=True)
            toc_items = future_2_2.result()
            items.extend(toc_items)
            toc_score = sum(item.score for item in toc_items)
            safe_print(f"      ✅ 2.2: {toc_score}/{len(toc_items)} подкритериев пройдено", flush=True)

            safe_print("    📝 2.3: Проверка Главы 1...", flush=True)
            ch1_items = future_2_3.result()
            items.extend(ch1_items)
            ch1_score = sum(item.score for item in ch1_items)
            safe_print(f"      ✅ 2.3: {ch1_score}/{len(ch1_items)} подкритериев пройдено", flush=True)

            safe_print("    📝 2.4: Проверка Главы 2...", flush=True)
            ch2_items = future_2_4.result()
            items.extend(ch2_items)
            ch2_score = sum(item.score for item in ch2_items)
            safe_print(f"      ✅ 2.4: {ch2_score}/{len(ch2_items)} подкритериев пройдено", flush=True)

            safe_print("    📝 2.5: Проверка Главы 3...", flush=True)
            ch3_items = future_2_5.result()
            items.extend(ch3_items)
            ch3_score = sum(item.score for item in ch3_items)
            safe_print(f"      ✅ 2.5: {ch3_score}/{len(ch3_items)} подкритериев пройдено", flush=True)

        return items

    def check_document(
        self,
        document: ReadmeDocument,
        learning_outcomes: list[str] | None = None,
    ) -> list[CriteriaItem]:
        """Проверяет раздел 2 по typed README document tree."""
        items = []
        annotation = document.annotation.strip()
        title = document.title.strip()
        theory_text = chapter_prose_text(document, 2, language=self.lang)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_2_1 = executor.submit(self._check_annotation, annotation, title, theory_text)
            future_2_2 = executor.submit(self._check_toc_document, document)
            future_2_3 = executor.submit(self._check_chapter1_document, document)
            future_2_4 = executor.submit(self._check_chapter2_document, document, learning_outcomes)
            future_2_5 = executor.submit(self._check_chapter3_document, document)

            safe_print("    📝 2.1: Проверка аннотации...", flush=True)
            annotation_items = future_2_1.result()
            items.extend(annotation_items)
            annotation_score = sum(item.score for item in annotation_items)
            safe_print(f"      ✅ 2.1: {annotation_score}/{len(annotation_items)} подкритериев пройдено", flush=True)

            safe_print("    📝 2.2: Проверка оглавления...", flush=True)
            toc_items = future_2_2.result()
            items.extend(toc_items)
            toc_score = sum(item.score for item in toc_items)
            safe_print(f"      ✅ 2.2: {toc_score}/{len(toc_items)} подкритериев пройдено", flush=True)

            safe_print("    📝 2.3: Проверка Главы 1...", flush=True)
            ch1_items = future_2_3.result()
            items.extend(ch1_items)
            ch1_score = sum(item.score for item in ch1_items)
            safe_print(f"      ✅ 2.3: {ch1_score}/{len(ch1_items)} подкритериев пройдено", flush=True)

            safe_print("    📝 2.4: Проверка Главы 2...", flush=True)
            ch2_items = future_2_4.result()
            items.extend(ch2_items)
            ch2_score = sum(item.score for item in ch2_items)
            safe_print(f"      ✅ 2.4: {ch2_score}/{len(ch2_items)} подкритериев пройдено", flush=True)

            safe_print("    📝 2.5: Проверка Главы 3...", flush=True)
            ch3_items = future_2_5.result()
            items.extend(ch3_items)
            ch3_score = sum(item.score for item in ch3_items)
            safe_print(f"      ✅ 2.5: {ch3_score}/{len(ch3_items)} подкритериев пройдено", flush=True)

        return items

    def _check_annotation(self, annotation: str, title: str, theory_text: str) -> list[CriteriaItem]:
        """2.1: Проверка аннотации (2.1.1-2.1.3)."""
        from .annotation_checker import AnnotationChecker
        checker = AnnotationChecker(
            llm_client=self.llm,
            embedding_function=self.embedding_function,
            language=self.lang
        )
        return checker.check(annotation, title, theory_text=theory_text)

    def _check_toc(self, md: str) -> list[CriteriaItem]:
        """2.2: Проверка оглавления (2.2.1-2.2.4)."""
        from .toc_checker import TOCChecker
        regex_patterns = {
            "rx_h2": self.rx_h2,
            "rx_h3": self.rx_h3,
            "rx_toc": self.rx_toc
        }
        checker = TOCChecker(
            llm_client=self.llm,
            embedding_function=self.embedding_function,
            language=self.lang,
            regex_patterns=regex_patterns
        )
        return checker.check(md)

    def _check_toc_document(self, document: ReadmeDocument) -> list[CriteriaItem]:
        """2.2: Проверка оглавления через typed document при поддержке checker'а."""
        from .toc_checker import TOCChecker
        regex_patterns = {
            "rx_h2": self.rx_h2,
            "rx_h3": self.rx_h3,
            "rx_toc": self.rx_toc,
        }
        checker = TOCChecker(
            llm_client=self.llm,
            embedding_function=self.embedding_function,
            language=self.lang,
            regex_patterns=regex_patterns,
        )
        return checker.check_document(document)

    def _check_chapter1(self, ch1_content: str) -> list[CriteriaItem]:
        """2.3: Проверка Главы 1 (2.3.1-2.3.7)."""
        from .chapter1_checker import Chapter1Checker
        regex_patterns = {
            "rx_h3": self.rx_h3,
            "rx_directives": self.rx_directives
        }
        checker = Chapter1Checker(
            llm_client=self.llm,
            language=self.lang,
            regex_patterns=regex_patterns
        )
        return checker.check(ch1_content)

    def _check_chapter1_document(self, document: ReadmeDocument) -> list[CriteriaItem]:
        """2.3: Проверка Главы 1 через typed document."""
        from .chapter1_checker import Chapter1Checker
        regex_patterns = {
            "rx_h3": self.rx_h3,
            "rx_directives": self.rx_directives,
        }
        checker = Chapter1Checker(
            llm_client=self.llm,
            language=self.lang,
            regex_patterns=regex_patterns,
        )
        return checker.check_document(document)

    def _check_chapter2(self, ch2_content: str, learning_outcomes: list[str] | None = None) -> list[CriteriaItem]:
        """2.4: Проверка Главы 2 (2.4.1-2.4.7)."""
        from .chapter2_checker import Chapter2Checker
        regex_patterns = {
            "rx_theory_part": self.rx_theory_part
        }
        checker = Chapter2Checker(
            llm_client=self.llm,
            language=self.lang,
            regex_patterns=regex_patterns
        )
        return checker.check(ch2_content, learning_outcomes)

    def _check_chapter2_document(
        self,
        document: ReadmeDocument,
        learning_outcomes: list[str] | None = None,
    ) -> list[CriteriaItem]:
        """2.4: Проверка Главы 2 через typed document."""
        from .chapter2_checker import Chapter2Checker
        regex_patterns = {
            "rx_theory_part": self.rx_theory_part,
        }
        checker = Chapter2Checker(
            llm_client=self.llm,
            language=self.lang,
            regex_patterns=regex_patterns,
        )
        return checker.check_document(document, learning_outcomes)

    def _check_chapter3(self, ch3_content: str, ch2_content: str) -> list[CriteriaItem]:
        """2.5: Проверка Главы 3 (2.5.1-2.5.7)."""
        from .chapter3_checker import Chapter3Checker
        regex_patterns = {
            "rx_task": self.rx_task
        }
        checker = Chapter3Checker(
            llm_client=self.llm,
            embedding_function=self.embedding_function,
            language=self.lang,
            regex_patterns=regex_patterns
        )
        return checker.check(ch3_content, ch2_content)

    def _check_chapter3_document(
        self,
        document: ReadmeDocument,
    ) -> list[CriteriaItem]:
        """2.5: Проверка Главы 3 через typed document."""
        from .chapter3_checker import Chapter3Checker
        regex_patterns = {
            "rx_task": self.rx_task,
        }
        checker = Chapter3Checker(
            llm_client=self.llm,
            embedding_function=self.embedding_function,
            language=self.lang,
            regex_patterns=regex_patterns,
        )
        return checker.check_document(document)


"""
Агент генерации файлов данных для практических задач.

Анализирует input_data задач, находит упоминания файлов (CSV, JSON, TXT),
и генерирует реалистичные данные через LLM.
"""

import csv
import io
import json
import logging
import re
from typing import Any

from ..artifact_chain import EvidenceSpec
from ..config.loader import get_agent_config, prompt_trace_kwargs
from ..models.schemas import PracticeTask, ProjectSeed
from ..practice_contract import is_solution_like_material_ref
from .base.agent import BaseAgent
from .base.llm_client import LLMClientProtocol

logger = logging.getLogger("content_gen.agents.dataset_generator")


class DatasetGeneratorAgent(BaseAgent):
    """Генерирует файлы данных для практических задач."""

    CONFIG_NAME = "dataset_generator"

    # Регулярные выражения для поиска упоминаний файлов
    RX_FILE_CSV = re.compile(r'\b([\w\-_]+\.csv)\b', re.I)
    RX_FILE_JSON = re.compile(r'\b([\w\-_]+\.json)\b', re.I)
    RX_FILE_TXT = re.compile(r'\b([\w\-_]+\.txt)\b', re.I)
    RX_FILE_XLSX = re.compile(r'\b([\w\-_]+\.xlsx?)\b', re.I)
    RX_FILE_MD = re.compile(r'\bmaterials/([\w\-_]+\.md)\b', re.I)  # Markdown файлы

    # Паттерны для описания структуры данных
    RX_COLUMNS = re.compile(r'(?:столбцы?|колонки?|поля?|columns?)\s*[:\-]?\s*([^\.]+)', re.I)
    RX_ROWS = re.compile(r'(?:строки?|записей?|rows?|records?)\s*[:\-]?\s*(\d+)', re.I)
    RX_CONTAINS = re.compile(r'(?:содержит?|включает?|contains?|includes?)\s+([^\.]+)', re.I)

    def __init__(self, llm: LLMClientProtocol):
        super().__init__(llm)
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}
        self.llm_kwargs.setdefault("temperature", 0.7)
        self.llm_kwargs.setdefault("max_tokens", 4000)

    def generate_files(
        self,
        tasks: list[PracticeTask],
        seed: ProjectSeed,
        evidence_specs: list[EvidenceSpec] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Генерирует файлы данных на основе упоминаний в задачах.

        Args:
            tasks: Список практических задач
            seed: Проектный seed

        Returns:
            Список файлов в формате [{"path": "...", "data": bytes}, ...]
        """
        if not tasks:
            return []

        generated_files = []
        seen_files = set()
        spec_index = self._index_evidence_specs(evidence_specs or [])

        for idx, task in enumerate(tasks, 1):
            file_refs = self._extract_file_references(task.input_data)

            for file_ref in file_refs:
                filename = file_ref["filename"]
                file_type = file_ref["type"]

                # Пропускаем уже обработанные файлы
                if filename in seen_files:
                    continue
                seen_files.add(filename)

                # Проверяем, нужно ли генерировать файл
                task_context = f"Задача {idx}: {task.title}\n{task.input_data}\n{task.goal}"
                if not self._should_generate_file(task.input_data, task_context, filename):
                    logger.info(f"ℹ️ Пропущен файл {filename}: задача про создание данных, не генерируем")
                    continue

                try:
                    evidence_spec = self._find_evidence_spec(filename, spec_index)
                    content = self._generate_file_content(
                        filename=filename,
                        file_type=file_type,
                        description=file_ref.get("description", ""),
                        task_context=f"Задача {idx}: {task.title}\n{task.input_data}\n{task.goal}",
                        seed=seed,
                        evidence_spec=evidence_spec,
                    )

                    if content:
                        # Определяем путь для файла
                        file_path = evidence_spec.path if evidence_spec else self._determine_file_path(filename, task, idx)
                        generated_file = {
                            "path": file_path,
                            "data": content,
                        }
                        if evidence_spec:
                            generated_file["evidence_spec"] = evidence_spec.model_dump()
                        generated_files.append(generated_file)
                        logger.info(f"✅ Сгенерирован файл: {file_path} ({len(content)} байт)")
                    else:
                        logger.warning(f"⚠️ Не удалось сгенерировать содержимое для {filename}")

                except Exception as e:
                    logger.error(f"❌ Ошибка генерации файла {filename}: {e}", exc_info=True)
                    continue

        return generated_files

    def _extract_file_references(self, input_data: str) -> list[dict[str, Any]]:
        """
        Извлекает упоминания файлов из input_data.

        Args:
            input_data: Текст входных данных задачи

        Returns:
            Список словарей с информацией о файлах
        """
        files = []

        # Ищем файлы разных форматов
        for match in self.RX_FILE_CSV.finditer(input_data):
            filename = match.group(1)
            files.append({
                "filename": filename,
                "type": "csv",
                "description": self._extract_file_description(input_data, filename)
            })

        for match in self.RX_FILE_JSON.finditer(input_data):
            filename = match.group(1)
            files.append({
                "filename": filename,
                "type": "json",
                "description": self._extract_file_description(input_data, filename)
            })

        for match in self.RX_FILE_TXT.finditer(input_data):
            filename = match.group(1)
            files.append({
                "filename": filename,
                "type": "txt",
                "description": self._extract_file_description(input_data, filename)
            })

        for match in self.RX_FILE_XLSX.finditer(input_data):
            filename = match.group(1)
            files.append({
                "filename": filename,
                "type": "xlsx",  # Excel файлы создаем через pandas
                "description": self._extract_file_description(input_data, filename)
            })

        # Markdown файлы (для писем, шаблонов, чатов)
        for match in self.RX_FILE_MD.finditer(input_data):
            filename = match.group(1)
            files.append({
                "filename": filename,
                "type": "md",
                "description": self._extract_file_description(input_data, filename)
            })

        return files

    def _should_generate_file(self, input_data: str, task_context: str, filename: str) -> bool:
        """
        Определяет, нужно ли генерировать файл на основе контекста задачи.

        Args:
            input_data: Входные данные задачи
            task_context: Полный контекст задачи (название, входные данные, цель)
            filename: Имя файла

        Returns:
            True если файл нужно сгенерировать (упоминается как существующий),
            False если задача про создание файла (не генерируем)
        """
        text_lower = (input_data + " " + task_context).lower()
        filename_lower = filename.lower()

        if is_solution_like_material_ref(filename, context=input_data):
            logger.warning(
                "⚠️ Пропущен файл %s: имя похоже на готовый артефакт студента, а не на сырой input material",
                filename,
            )
            return False

        # Ключевые слова, указывающие на создание/генерацию данных (НЕ генерируем)
        creation_keywords = [
            r"\bсоздать\s+(?:файл|датасет|данные|набор)",
            r"\bсгенерировать\s+(?:файл|датасет|данные|набор)",
            r"\bнаписать\s+код\s+для\s+(?:создания|генерации)",
            r"\bразработать\s+функцию\s+для\s+(?:создания|генерации)",
            r"\bгенерировать\s+(?:файл|датасет|данные)",
            r"\bподготовить\s+(?:файл|датасет|данные)",
            r"generate\s+(?:file|dataset|data)",
            r"create\s+(?:file|dataset|data)",
            r"write\s+code\s+to\s+(?:create|generate)",
        ]

        # Проверяем, не про создание ли задача
        for pattern in creation_keywords:
            if re.search(pattern, text_lower):
                # Дополнительная проверка: если файл упоминается в контексте создания
                if filename_lower in text_lower:
                    # Проверяем, не является ли это просто упоминанием результата
                    # (например, "создать файл X.csv" - не генерируем)
                    creation_context = re.search(
                        rf"(?:создать|сгенерировать|написать|разработать).*?{re.escape(filename_lower)}",
                        text_lower
                    )
                    if creation_context:
                        return False

        # Ключевые слова, указывающие на существующий файл (генерируем)
        existing_keywords = [
            rf"\b(?:датасет|файл|набор\s+данных)\s+{re.escape(filename_lower)}\s+(?:содержит|включает|имеет)",
            rf"\b{re.escape(filename_lower)}\s+(?:содержит|включает|имеет|с\s+данными)",
            r"\bу\s+тебя\s+есть\s+(?:файл|датасет)",
            r"\bдоступен\s+(?:файл|датасет)",
            r"\bпредоставлен\s+(?:файл|датасет)",
            r"\b(?:файл|датасет)\s+с\s+данными",
            r"dataset\s+contains",
            r"file\s+contains",
            r"available\s+(?:file|dataset)",
        ]

        # Проверяем, упоминается ли файл как существующий
        for pattern in existing_keywords:
            if re.search(pattern, text_lower):
                return True

        # Если файл упоминается в контексте "содержит данные" или "с колонками"
        if re.search(rf"{re.escape(filename_lower)}.*?(?:содержит|колонки|столбцы|поля)", text_lower):
            return True

        # По умолчанию: если файл упоминается, но нет явных указаний на создание - генерируем
        # (лучше предоставить данные, чем оставить задачу невыполнимой)
        return True

    def _extract_file_description(self, text: str, filename: str) -> str:
        """
        Извлекает описание файла из контекста.

        Args:
            text: Текст с упоминанием файла
            filename: Имя файла

        Returns:
            Описание структуры и содержимого файла
        """
        # Ищем предложения, содержащие имя файла
        sentences = re.split(r'[.!?]\s+', text)
        relevant = []

        for sent in sentences:
            if filename.lower() in sent.lower():
                relevant.append(sent.strip())

        # Извлекаем информацию о структуре
        description_parts = []

        # Колонки/поля
        cols_match = self.RX_COLUMNS.search(text)
        if cols_match:
            description_parts.append(f"Структура: {cols_match.group(1).strip()}")

        # Количество строк
        rows_match = self.RX_ROWS.search(text)
        if rows_match:
            description_parts.append(f"Количество записей: {rows_match.group(1)}")

        # Содержимое
        contains_match = self.RX_CONTAINS.search(text)
        if contains_match:
            description_parts.append(f"Содержит: {contains_match.group(1).strip()}")

        # Объединяем релевантные предложения
        if relevant:
            description_parts.extend(relevant[:2])  # Берем первые 2 предложения

        return ". ".join(description_parts) if description_parts else ""

    def _generate_file_content(
        self,
        filename: str,
        file_type: str,
        description: str,
        task_context: str,
        seed: ProjectSeed,
        evidence_spec: EvidenceSpec | None = None,
    ) -> bytes | None:
        """
        Генерирует содержимое файла через LLM.

        Args:
            filename: Имя файла
            file_type: Тип файла (csv, json, txt)
            description: Описание структуры данных
            task_context: Контекст задачи
            seed: Проектный seed

        Returns:
            Байты содержимого файла или None при ошибке
        """
        system_prompt = self.config.get_prompt("system").format(language=seed.language)
        evidence_spec_section = ""
        if evidence_spec is not None:
            evidence_spec_section = (
                "\n\n=== EVIDENCE SPEC ===\n"
                f"{evidence_spec.to_prompt_context()}\n"
                "Соблюдай EvidenceSpec строго: файл должен быть сырьём для анализа, "
                "а не полуготовым решением задачи."
            )

        user_prompt = self.config.get_prompt("user_template").format(
            filename=filename,
            file_type=file_type,
            description=description or "Нет описания структуры",
            task_context=task_context,
            project_title=seed.title_seed or seed.platform_name or "Текущий проект",
            project_description=seed.project_description,
            learning_outcomes="; ".join(seed.learning_outcomes) if seed.learning_outcomes else "—",
            skills="; ".join(seed.skills) if seed.skills else "—",
            thematic_block=seed.thematic_block or seed.direction or "—",
            sjm=seed.sjm or "—",
            language=seed.language,
            rows_count=75  # 50-100 строк, берем среднее
        ) + evidence_spec_section

        try:
            llm_kwargs = self.llm_kwargs.copy()
            llm_kwargs.update(
                prompt_trace_kwargs(
                    self.config,
                    "system",
                    "user_template",
                    output_schema=f"{file_type}_dataset",
                )
            )
            response = self.llm.complete(
                system=system_prompt,
                user=user_prompt,
                response_format="json_object" if file_type == "json" else None,
                **llm_kwargs
            )

            if not response:
                logger.warning(f"⚠️ LLM вернул пустой ответ для {filename}")
                return None

            # Парсим и валидируем ответ в зависимости от типа файла
            if file_type == "csv":
                return self._parse_csv_response(response, filename)
            elif file_type == "xlsx":
                return self._parse_xlsx_response(response, filename)
            elif file_type == "json":
                return self._parse_json_response(response, filename)
            elif file_type == "txt":
                return self._parse_txt_response(response, filename)
            elif file_type == "md":
                return self._parse_md_response(response, filename)
            else:
                logger.warning(f"⚠️ Неподдерживаемый тип файла: {file_type}")
                return None

        except Exception as e:
            logger.error(f"❌ Ошибка генерации содержимого для {filename}: {e}", exc_info=True)
            return None

    def _parse_csv_response(self, response: str, filename: str) -> bytes | None:
        """Парсит ответ LLM для CSV файла."""
        try:
            # Пытаемся найти CSV блок в ответе
            csv_match = re.search(r'```(?:csv)?\s*\n(.*?)\n```', response, re.DOTALL)
            if csv_match:
                csv_content = csv_match.group(1).strip()
            else:
                # Если нет блока, ищем JSON структуру
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(0))
                    if isinstance(data, dict) and "data" in data:
                        # Преобразуем JSON в CSV
                        csv_content = self._json_to_csv(data["data"])
                    elif isinstance(data, list):
                        csv_content = self._json_to_csv(data)
                    else:
                        csv_content = response.strip()
                else:
                    csv_content = response.strip()

            # Валидируем CSV
            reader = csv.reader(io.StringIO(csv_content))
            rows = list(reader)
            if len(rows) < 2:
                logger.warning(f"⚠️ CSV {filename} содержит менее 2 строк, добавляем заголовок")
                if not rows or not rows[0]:
                    return None

            # Ограничиваем до 100 строк
            if len(rows) > 100:
                rows = rows[:100]
                logger.info(f"📝 CSV {filename} обрезан до 100 строк")

            # Формируем финальный CSV
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerows(rows)
            csv_content = output.getvalue()
            # Добавляем UTF-8 BOM для правильного отображения в Excel
            return '\ufeff'.encode() + csv_content.encode('utf-8')

        except Exception as e:
            logger.error(f"❌ Ошибка парсинга CSV для {filename}: {e}")
            return None

    def _parse_json_response(self, response: str, filename: str) -> bytes | None:
        """Парсит ответ LLM для JSON файла."""
        try:
            # Ищем JSON объект
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
            else:
                # Пытаемся распарсить весь ответ как JSON
                data = json.loads(response.strip())

            # Валидируем структуру
            if isinstance(data, list):
                # Ограничиваем до 100 элементов
                if len(data) > 100:
                    data = data[:100]
                    logger.info(f"📝 JSON {filename} обрезан до 100 элементов")
            elif isinstance(data, dict):
                # Если это словарь с данными, проверяем размер
                if "data" in data and isinstance(data["data"], list):
                    if len(data["data"]) > 100:
                        data["data"] = data["data"][:100]
                        logger.info(f"📝 JSON {filename} обрезан до 100 элементов")

            return json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')

        except json.JSONDecodeError as e:
            logger.error(f"❌ Ошибка парсинга JSON для {filename}: {e}")
            return None

    def _parse_txt_response(self, response: str, filename: str) -> bytes | None:
        """Парсит ответ LLM для TXT файла."""
        try:
            # Убираем markdown блоки, если есть
            text = re.sub(r'```[^\n]*\n(.*?)\n```', r'\1', response, flags=re.DOTALL)
            text = text.strip()

            # Ограничиваем до разумного размера (примерно 100 строк)
            lines = text.split('\n')
            if len(lines) > 100:
                lines = lines[:100]
                logger.info(f"📝 TXT {filename} обрезан до 100 строк")
                text = '\n'.join(lines)

            return text.encode('utf-8')

        except Exception as e:
            logger.error(f"❌ Ошибка парсинга TXT для {filename}: {e}")
            return None

    def _parse_md_response(self, response: str, filename: str) -> bytes | None:
        """Парсит ответ LLM для Markdown файла (письма, шаблоны, чаты)."""
        try:
            # Markdown оставляем как есть, только убираем внешние code-блоки если они обрамляют весь ответ
            text = response.strip()

            # Если весь ответ обрамлён ```markdown ... ```, убираем
            md_block_match = re.match(r'^```(?:markdown|md)?\s*\n(.*?)\n```$', text, re.DOTALL)
            if md_block_match:
                text = md_block_match.group(1).strip()

            # Ограничиваем до разумного размера
            lines = text.split('\n')
            if len(lines) > 150:
                lines = lines[:150]
                logger.info(f"📝 MD {filename} обрезан до 150 строк")
                text = '\n'.join(lines)

            return text.encode('utf-8')

        except Exception as e:
            logger.error(f"❌ Ошибка парсинга MD для {filename}: {e}")
            return None

    def _json_to_csv(self, data: list[dict[str, Any]]) -> str:
        """Преобразует JSON массив в CSV строку."""
        if not data:
            return ""

        output = io.StringIO()
        if isinstance(data[0], dict):
            fieldnames = list(data[0].keys())
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)
        else:
            writer = csv.writer(output)
            for row in data:
                writer.writerow([row] if not isinstance(row, list) else row)

        return output.getvalue()

    def _parse_xlsx_response(self, response: str, filename: str) -> bytes | None:
        """Парсит ответ LLM для Excel файла и создает настоящий .xlsx через pandas."""
        try:
            # Пытаемся найти JSON структуру в ответе
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                if isinstance(data, dict) and "data" in data:
                    rows = data["data"]
                elif isinstance(data, list):
                    rows = data
                else:
                    # Пытаемся найти CSV блок и конвертировать
                    csv_match = re.search(r'```(?:csv)?\s*\n(.*?)\n```', response, re.DOTALL)
                    if csv_match:
                        csv_content = csv_match.group(1).strip()
                        reader = csv.DictReader(io.StringIO(csv_content))
                        rows = list(reader)
                    else:
                        logger.warning(f"⚠️ Не удалось распарсить структуру данных для {filename}")
                        return None
            else:
                # Пытаемся найти CSV блок и конвертировать
                csv_match = re.search(r'```(?:csv)?\s*\n(.*?)\n```', response, re.DOTALL)
                if csv_match:
                    csv_content = csv_match.group(1).strip()
                    reader = csv.DictReader(io.StringIO(csv_content))
                    rows = list(reader)
                else:
                    logger.warning(f"⚠️ Не удалось найти данные для Excel файла {filename}")
                    return None

            if not rows:
                logger.warning(f"⚠️ Excel файл {filename} пуст")
                return None

            # Ограничиваем до 100 строк
            if len(rows) > 100:
                rows = rows[:100]
                logger.info(f"📝 Excel {filename} обрезан до 100 строк")

            # Создаем DataFrame и сохраняем в Excel
            try:
                import pandas as pd
            except ImportError:
                logger.error("❌ pandas не установлен, не могу создать Excel файл")
                return None

            # Преобразуем в DataFrame
            if isinstance(rows[0], dict):
                df = pd.DataFrame(rows)
            else:
                # Если это список списков, создаем DataFrame с первой строкой как заголовком
                df = pd.DataFrame(rows[1:], columns=rows[0] if rows else [])

            # Создаем Excel файл в памяти
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Данные')
            buffer.seek(0)

            return buffer.getvalue()

        except Exception as e:
            logger.error(f"❌ Ошибка парсинга Excel для {filename}: {e}", exc_info=True)
            return None

    def _determine_file_path(self, filename: str, task: PracticeTask, task_idx: int) -> str:
        """
        Определяет путь для сохранения файла данных.

        Args:
            filename: Имя файла
            task: Задача
            task_idx: Индекс задачи

        Returns:
            Путь для сохранения файла (всегда в materials/ или data/)
        """
        # Файлы данных всегда идут в materials/ или data/
        # НЕ используем artifact_location (это для README артефактов)

        # Используем materials/ по умолчанию
        return f"materials/{filename}"

    @staticmethod
    def _index_evidence_specs(evidence_specs: list[EvidenceSpec]) -> dict[str, EvidenceSpec]:
        indexed: dict[str, EvidenceSpec] = {}
        for raw_spec in evidence_specs:
            spec = raw_spec if isinstance(raw_spec, EvidenceSpec) else EvidenceSpec(**raw_spec)
            path = spec.path.replace("\\", "/")
            indexed[path.lower()] = spec
            indexed[path.split("/")[-1].lower()] = spec
        return indexed

    @staticmethod
    def _find_evidence_spec(filename: str, spec_index: dict[str, EvidenceSpec]) -> EvidenceSpec | None:
        normalized = filename.replace("\\", "/").lower()
        return spec_index.get(normalized) or spec_index.get(normalized.split("/")[-1])

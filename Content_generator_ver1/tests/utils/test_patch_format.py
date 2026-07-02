"""
Тесты для модуля patch_format.
"""

from content_gen.utils.patch_format import (
    Patch,
    _find_text_with_fuzzy_match,
    _validate_patch,
    apply_patches,
    parse_patches_from_response,
)


class TestParsePatchesFromResponse:
    """Тесты для функции parse_patches_from_response."""

    def test_parse_valid_json(self):
        """Тест парсинга валидного JSON с патчами."""
        response = """
        {
          "changes": [
            {
              "location_hint": "заголовок раздела",
              "old_text": "Старый текст",
              "new_text": "Новый текст"
            }
          ]
        }
        """
        patches = parse_patches_from_response(response)

        assert patches is not None
        assert len(patches) == 1
        assert patches[0].location_hint == "заголовок раздела"
        assert patches[0].old_text == "Старый текст"
        assert patches[0].new_text == "Новый текст"

    def test_parse_multiple_patches(self):
        """Тест парсинга нескольких патчей."""
        response = """
        {
          "changes": [
            {
              "location_hint": "первое изменение",
              "old_text": "Текст 1",
              "new_text": "Текст 1 новый"
            },
            {
              "location_hint": "второе изменение",
              "old_text": "Текст 2",
              "new_text": "Текст 2 новый"
            }
          ]
        }
        """
        patches = parse_patches_from_response(response)

        assert patches is not None
        assert len(patches) == 2

    def test_parse_invalid_json(self):
        """Тест парсинга некорректного JSON."""
        response = "Просто текст без JSON"
        patches = parse_patches_from_response(response)

        assert patches is None

    def test_parse_missing_fields(self):
        """Тест парсинга JSON с отсутствующими полями."""
        response = """
        {
          "changes": [
            {
              "location_hint": "заголовок"
            }
          ]
        }
        """
        patches = parse_patches_from_response(response)

        assert patches is None or len(patches) == 0

    def test_parse_empty_changes_as_empty_list(self):
        """Пустой список changes — валидный no-op, а не ошибка парсинга."""
        patches = parse_patches_from_response('{ "changes": [] }')

        assert patches == []


class TestApplyPatches:
    """Тесты для функции apply_patches."""

    def test_apply_single_patch(self):
        """Тест применения одного патча."""
        original = """
# Заголовок

Старый текст для замены.

Другой текст.
"""
        patches = [
            Patch(
                location_hint="замена текста",
                old_text="Старый текст для замены.",
                new_text="Новый текст после замены."
            )
        ]

        result = apply_patches(original, patches)

        assert result.success is True
        assert len(result.applied_patches) == 1
        assert len(result.failed_patches) == 0
        assert "Новый текст после замены." in result.result_md
        assert "Старый текст для замены." not in result.result_md

    def test_apply_multiple_patches(self):
        """Тест применения нескольких патчей."""
        original = """
# Заголовок

Первый текст.
Второй текст.
"""
        patches = [
            Patch(
                location_hint="первое изменение",
                old_text="Первый текст.",
                new_text="Первый текст новый."
            ),
            Patch(
                location_hint="второе изменение",
                old_text="Второй текст.",
                new_text="Второй текст новый."
            ),
        ]

        result = apply_patches(original, patches)

        assert result.success is True
        assert len(result.applied_patches) == 2
        assert "Первый текст новый." in result.result_md
        assert "Второй текст новый." in result.result_md

    def test_apply_patch_not_found(self):
        """Тест применения патча с несуществующим old_text."""
        original = "# Заголовок\n\nТекст."
        patches = [
            Patch(
                location_hint="несуществующий патч",
                old_text="Несуществующий текст",
                new_text="Новый текст"
            )
        ]

        result = apply_patches(original, patches)

        assert result.success is False
        assert len(result.applied_patches) == 0
        assert len(result.failed_patches) == 1
        assert len(result.errors) > 0

    def test_apply_patch_with_protected_blocks(self):
        """Тест применения патча с защищёнными блоками (должен быть отклонён)."""
        original = "# Заголовок\n\nТекст с [[[BLOCK_0]]] маркером."
        patches = [
            Patch(
                location_hint="патч с маркером",
                old_text="Текст с [[[BLOCK_0]]] маркером.",
                new_text="Новый текст."
            )
        ]

        result = apply_patches(original, patches)

        # Патч должен быть отклонён из-за маркера в old_text
        assert result.success is False
        assert len(result.failed_patches) == 1
        assert any("маркер" in error.lower() or "блок" in error.lower() for error in result.errors)

    def test_apply_deletion_patch_allows_empty_new_text(self):
        """Тест удаления фрагмента через пустой new_text."""
        original = "# Заголовок\n\nЭтот абзац нужно удалить полностью.\n\nСледующий абзац."
        patches = [
            Patch(
                location_hint="удаление абзаца",
                old_text="Этот абзац нужно удалить полностью.",
                new_text="",
            )
        ]

        result = apply_patches(original, patches)

        assert result.success is True
        assert len(result.applied_patches) == 1
        assert "Этот абзац нужно удалить полностью." not in result.result_md
        assert "Следующий абзац." in result.result_md

    def test_apply_scoped_patch_uses_selected_duplicate_not_first_occurrence(self):
        """Патч в scoped-режиме должен применяться внутри выбранного диапазона."""
        original = (
            "# README\n\n"
            "Повторяющийся абзац для замены.\n\n"
            "## 2.1. Пример\n"
            "Повторяющийся абзац для замены.\n"
        )
        patches = [
            Patch(
                location_hint="пример 2.1",
                old_text="Повторяющийся абзац для замены.",
                new_text="Новый пример в разделе 2.1.",
            )
        ]

        result = apply_patches(original, patches, allowed_line_ranges=[(5, 6, "2.1. Пример")])

        assert result.success is True
        assert result.result_md.count("Повторяющийся абзац для замены.") == 1
        assert "## 2.1. Пример\nНовый пример в разделе 2.1." in result.result_md

    def test_apply_scoped_patch_rejects_text_outside_allowed_range(self):
        """Патч вне выбранных строк должен быть отклонён, даже если old_text есть в README."""
        original = (
            "# README\n\n"
            "Этот раздел нельзя менять.\n\n"
            "## 2.1. Пример\n"
            "Старый пример для замены.\n"
        )
        patches = [
            Patch(
                location_hint="чужой раздел",
                old_text="Этот раздел нельзя менять.",
                new_text="Сломанный текст.",
            )
        ]

        result = apply_patches(original, patches, allowed_line_ranges=[(5, 6, "2.1. Пример")])

        assert result.success is False
        assert result.applied_patches == []
        assert "Этот раздел нельзя менять." in result.result_md
        assert any("разреш" in error.lower() for error in result.errors)


class TestValidatePatch:
    """Тесты для функции _validate_patch."""

    def test_validate_valid_patch(self):
        """Тест валидации корректного патча."""
        patch = Patch(
            location_hint="тест",
            old_text="Достаточно длинный текст для замены",
            new_text="Новый текст"
        )
        original = "Достаточно длинный текст для замены"

        is_valid, error = _validate_patch(patch, original)

        assert is_valid is True
        assert error is None

    def test_validate_empty_old_text(self):
        """Тест валидации патча с пустым old_text."""
        patch = Patch(
            location_hint="тест",
            old_text="",
            new_text="Новый текст"
        )
        original = "Текст"

        is_valid, error = _validate_patch(patch, original)

        assert is_valid is False
        assert "пуст" in error.lower()

    def test_validate_short_old_text(self):
        """Тест валидации патча с коротким old_text."""
        patch = Patch(
            location_hint="тест",
            old_text="Короткий",
            new_text="Новый текст"
        )
        original = "Короткий"

        is_valid, error = _validate_patch(patch, original)

        assert is_valid is False
        assert "коротк" in error.lower()

    def test_validate_patch_with_markers(self):
        """Тест валидации патча с маркерами в old_text."""
        patch = Patch(
            location_hint="тест",
            old_text="Текст с [[[BLOCK_0]]] маркером",
            new_text="Новый текст"
        )
        original = "Текст"

        is_valid, error = _validate_patch(patch, original)

        assert is_valid is False
        assert "маркер" in error.lower() or "блок" in error.lower()


class TestFindTextWithFuzzyMatch:
    """Тесты для функции _find_text_with_fuzzy_match."""

    def test_find_exact_match(self):
        """Тест поиска точного совпадения."""
        text = "Текст для поиска в документе"
        pattern = "Текст для поиска"

        result = _find_text_with_fuzzy_match(text, pattern)

        assert result is not None
        start, end = result
        assert text[start:end] == pattern

    def test_find_with_whitespace_variations(self):
        """Тест поиска с вариациями пробелов."""
        text = "Текст   для    поиска   в   документе"
        pattern = "Текст для поиска"

        result = _find_text_with_fuzzy_match(text, pattern)

        assert result is not None

    def test_find_not_found(self):
        """Тест поиска несуществующего текста."""
        text = "Текст для поиска"
        pattern = "Несуществующий текст"

        result = _find_text_with_fuzzy_match(text, pattern)

        assert result is None


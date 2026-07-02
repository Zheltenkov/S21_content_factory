"""Language and script contracts for document translation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TranslationLanguageProfile:
    """Prompt-facing language metadata used by translation agents."""

    code: str
    name: str
    prompt_label: str
    expected_script: str
    script_instruction: str


LANGUAGE_PROFILES: dict[str, TranslationLanguageProfile] = {
    "en": TranslationLanguageProfile(
        code="en",
        name="английский",
        prompt_label="английский язык",
        expected_script="latin",
        script_instruction=(
            "пиши переводимый текст английской латиницей; кириллица допустима "
            "только в неизменяемых именах собственных, коде, путях и ссылках"
        ),
    ),
    "kg": TranslationLanguageProfile(
        code="kg",
        name="киргизский",
        prompt_label="кыргызский / киргизский язык",
        expected_script="cyrillic",
        script_instruction=(
            "пиши переводимый текст кыргызской кириллицей; не используй латинскую "
            "транслитерацию; латиница допустима только для неизменяемых "
            "технических терминов, кода, путей, ссылок и имен собственных"
        ),
    ),
    "uz": TranslationLanguageProfile(
        code="uz",
        name="узбекский",
        prompt_label="узбекский язык",
        expected_script="latin",
        script_instruction=(
            "пиши переводимый текст современной узбекской латиницей; кириллица "
            "допустима только в неизменяемых именах собственных, коде, путях и ссылках"
        ),
    ),
    "tg": TranslationLanguageProfile(
        code="tg",
        name="таджикский",
        prompt_label="таджикский язык",
        expected_script="cyrillic",
        script_instruction=(
            "пиши переводимый текст современной таджикской кириллицей; не используй "
            "латинскую транслитерацию; латиница допустима только для неизменяемых "
            "технических терминов, кода, путей, ссылок и имен собственных"
        ),
    ),
}


def get_translation_language_profile(language_code: str) -> TranslationLanguageProfile:
    """Return a stable language profile for translation prompts and validators."""
    normalized = (language_code or "").lower().strip()
    return LANGUAGE_PROFILES.get(
        normalized,
        TranslationLanguageProfile(
            code=normalized or "unknown",
            name=normalized or "целевой язык",
            prompt_label=normalized or "целевой язык",
            expected_script="unknown",
            script_instruction="соблюдай стандартную письменность целевого языка",
        ),
    )

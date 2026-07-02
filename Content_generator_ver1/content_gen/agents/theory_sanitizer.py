"""Sanitizers for theory agent public Markdown fragments."""

from __future__ import annotations

import re

from ..models.schemas import ProjectSeed
from ..utils.markdown_display_normalizer import normalize_markdown_display_blocks
from ..utils.text_analysis import count_prose_words, count_words


def _split_sentences(text: str) -> list[str]:
    """Split text into sentence-like units."""
    chunks = re.split(r"(?<=[\.\!\?])\s+", (text or "").strip())
    return [chunk.strip() for chunk in chunks if chunk and chunk.strip()]


def _compact_sentences(sentences: list[str]) -> str:
    """Build short paragraphs from a compact list of sentences."""
    if not sentences:
        return ""
    first = " ".join(sentences[:2]).strip()
    second = " ".join(sentences[2:4]).strip()
    if second:
        return f"{first}\n\n{second}".strip()
    return first


_FENCED_BLOCK_RX = re.compile(r"```[\s\S]*?```", re.M)
_TABLE_BLOCK_RX = re.compile(
    r"(^\|[^\n]+\|\s*\n\|[-:\s|]+\|\s*\n(?:\|[^\n]+\|\s*(?:\n|$))+)",
    re.M,
)


def _normalize_inline_markdown_tables(text: str) -> str:
    """Recover markdown tables that were flattened into a single line."""
    normalized_lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.rstrip()
        if stripped.count("|") < 6 or not re.search(r"\|\s+(?=\|)", stripped):
            normalized_lines.append(line)
            continue

        repaired = re.sub(r"\|\s+(?=\|)", "|\n", stripped)
        if "\n|" not in repaired:
            normalized_lines.append(line)
            continue

        first_pipe = repaired.find("|")
        prefix = repaired[:first_pipe].rstrip()
        table = repaired[first_pipe:].strip()
        if prefix:
            normalized_lines.append(prefix)
            normalized_lines.append("")
        normalized_lines.extend(table.splitlines())
    return "\n".join(normalized_lines)


def _protect_markdown_blocks(text: str) -> tuple[str, dict[str, str]]:
    """Protect tables and fenced blocks from prose compaction."""
    protected: dict[str, str] = {}

    def repl(match: re.Match[str]) -> str:
        key = f"@@THEORY_BLOCK_{len(protected)}@@"
        protected[key] = match.group(0).strip("\n")
        return f"\n\n{key}\n\n"

    stage = _FENCED_BLOCK_RX.sub(repl, text or "")
    stage = _TABLE_BLOCK_RX.sub(repl, stage)
    return stage, protected


def _restore_markdown_blocks(text: str, blocks: dict[str, str]) -> str:
    """Restore protected markdown blocks with stable blank lines around them."""
    restored = text or ""
    for key, block in blocks.items():
        restored = restored.replace(key, block)
    restored = re.sub(r"\n{3,}", "\n\n", restored)
    return restored.strip()


def _sanitize_theory_prose_chunk(text: str, title: str, seed: ProjectSeed, anchors: list[str], hi: int) -> str:
    """Sanitize only prose fragments, without touching preserved markdown blocks."""
    compact_source = re.sub(r"[ \t]+", " ", (text or "").strip())
    compact_source = re.sub(r"\n{3,}", "\n\n", compact_source)
    if not compact_source:
        return ""

    generic_leads = [
        r"^теперь, когда\b",
        r"^исходя из\b",
        r"^вдобавок\b",
        r"^также стоит\b",
        r"^в конечном итоге\b",
        r"^облачные услуги связаны\b",
        r"^исходя из значимости\b",
        r"^выбор технологий\b.*\bявляется\b",
    ]
    generic_fillers = [
        r"\bстанет важным вкладом\b",
        r"\bзначительно расширит\b",
        r"\bв современном мире\b",
        r"\bв различных сферах it\b",
        r"\bкрайне важно\b",
    ]

    sentences = _split_sentences(compact_source)
    filtered: list[str] = []
    definition_count = 0

    for sentence in sentences:
        low = sentence.lower()
        if any(re.search(pattern, low, flags=re.I) for pattern in generic_leads):
            continue
        if any(re.search(pattern, low, flags=re.I) for pattern in generic_fillers):
            continue

        if "— это" in low or " - это" in low:
            definition_count += 1
            if definition_count > 1 and not any(anchor in low for anchor in anchors[:8]):
                continue

        filtered.append(sentence.strip())

    if len(filtered) < 2:
        filtered = sentences[:4]

    filtered = filtered[:4]
    compact = _compact_sentences(filtered)

    anchor_hit = any(anchor in compact.lower() for anchor in anchors[:10]) if anchors else False
    if not anchor_hit and (seed.project_description or "").strip():
        compact = (
            compact.rstrip()
            + f" Для этого проекта важно понять {title.lower()}, чтобы связать теорию с практическими решениями, артефактами и критериями проверки."
        ).strip()

    words = count_words(compact, seed.language)
    if words > hi:
        kept: list[str] = []
        for sentence in _split_sentences(compact):
            candidate = _compact_sentences(kept + [sentence])
            if kept and count_words(candidate, seed.language) > hi:
                break
            kept.append(sentence)
        if kept:
            compact = _compact_sentences(kept)

    return compact.strip()


def _normalize_definition_bold(text: str) -> str:
    """Add bold markdown to terms in definitions when the term is still plain text."""
    body = text or ""
    if not body:
        return body

    term_expr = r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9]*(?:[ /-][A-Za-zА-Яа-яЁё0-9]+){0,8}"

    def _wrap_term(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or ""
        term = (match.group("term") or "").strip()
        glue = match.group("glue") or ""
        if not term or term.startswith("**") or term.endswith("**"):
            return match.group(0)
        return f"{prefix}**{term}**{glue}"

    simple_patterns = [
        re.compile(
            rf"(?P<prefix>(?:^|[.!?\n]\s*))(?!\*\*)(?P<term>{term_expr})(?P<glue>\s*[—-]\s*это\b)",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?P<prefix>(?:^|[.!?\n]\s*))(?!\*\*)(?P<term>{term_expr})(?P<glue>\s+(?:представляет собой|является)\b)",
            re.IGNORECASE,
        ),
    ]

    normalized = body
    for pattern in simple_patterns:
        normalized = pattern.sub(_wrap_term, normalized)

    normalized = re.sub(
        rf"Под\s+(?!\*\*)({term_expr})\s+(понима(?:ют|ется|ются|ет)?|подразумевается)\b",
        lambda match: f"Под **{match.group(1).strip()}** {match.group(2)}",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


def _build_theory_padding_sentences(title: str, seed: ProjectSeed, anchors: list[str]) -> list[str]:
    """Build short deterministic sentences to bring compact theory up to the minimal threshold."""
    title_low = (title or "этот блок").strip().lower()
    description = re.sub(r"\s+", " ", (seed.project_description or "").strip()).strip(". ")
    outcome = ""
    if seed.learning_outcomes:
        outcome = re.sub(r"\s+", " ", seed.learning_outcomes[0]).strip().strip(".")
    unique_anchors = list(dict.fromkeys([a for a in anchors if len(a) > 3]))
    anchor_text = ", ".join(unique_anchors[:3])

    sentences: list[str] = [
        f"В практике тебе важно не просто назвать {title_low}, а связать это решение с ограничениями проекта, доступными ресурсами и ожидаемым результатом.",
        "Сначала зафиксируй критерии выбора, затем проверь ограничения и последствия решения, и только после этого переходи к конкретным действиям или артефактам.",
    ]
    if outcome:
        sentences.append(f"Это напрямую помогает выйти на результат обучения: {outcome}.")
    if description:
        sentences.append(f"Ориентируйся на контекст проекта: {description[:180]}.")
    elif anchor_text:
        sentences.append(f"Держи в фокусе ключевые опоры этого проекта: {anchor_text}.")
    return sentences


def _sanitize_theory_body_text(body: str, title: str, seed: ProjectSeed, anchors: list[str], lo: int, hi: int) -> str:
    """Make theory less lecture-like and closer to didactics."""
    text = normalize_markdown_display_blocks((body or "").strip())
    text = _normalize_inline_markdown_tables(text)
    if not text:
        return text

    protected_text, blocks = _protect_markdown_blocks(text)
    parts = re.split(r"(@@THEORY_BLOCK_\d+@@)", protected_text)
    sanitized_parts: list[str] = []
    for chunk in parts:
        if not chunk:
            continue
        if chunk in blocks:
            sanitized_parts.append(chunk)
            continue
        sanitized = _sanitize_theory_prose_chunk(chunk, title, seed, anchors, hi)
        if sanitized:
            sanitized_parts.append(_normalize_definition_bold(sanitized))

    compact = "\n\n".join(part for part in sanitized_parts if part.strip()).strip()
    compact = _restore_markdown_blocks(compact, blocks)

    current_words = count_prose_words(compact, seed.language)
    if current_words < lo:
        additions: list[str] = []
        padding_pool = _build_theory_padding_sentences(title, seed, anchors)
        while padding_pool and count_prose_words((compact + " " + " ".join(additions)).strip(), seed.language) < lo:
            reached_target = False
            for sentence in padding_pool:
                candidate = (compact + " " + " ".join(additions + [sentence])).strip()
                if count_prose_words(candidate, seed.language) > hi:
                    reached_target = True
                    break
                additions.append(sentence)
                if count_prose_words(candidate, seed.language) >= lo:
                    reached_target = True
                    break
            if reached_target:
                break
        if additions:
            compact = (compact + "\n\n" + " ".join(additions)).strip()

    compact = _normalize_definition_bold(compact)
    compact = normalize_markdown_display_blocks(compact)

    return compact.strip()


def _sanitize_theory_example_text(example: str) -> str:
    """Keep examples concrete and concise."""
    sentences = _split_sentences(re.sub(r"\s+", " ", (example or "").strip()))
    return " ".join(sentences[:3]).strip()

"""Extra detection checkers: cross-file README<->code consistency and a
generalized course-material relevance check.

Kept in a separate ASCII module (Russian strings load from
extra_checkers_data.json). Registered lazily from checks.default_checkers to
avoid a circular import.

CrossFileConsistencyChecker
    Flags the same documented call (e.g. ``atm(8350); // {...}``) having
    different expected outputs in README vs the code file - the FEB1 case where
    the README example includes a zero-count bill that the code omits. Plus an
    optional model pass for free-form spec<->code contradictions.

CourseMaterialRelevanceChecker
    Complements CurriculumRelevanceChecker: infers the unit's primary language
    from source-file extensions (more reliable than README hints) and flags a
    style guide / framework that belongs to a different language. It skips the
    (cpp->java), (csharp->java), (cpp->c) pairs already handled by the
    curriculum checker, adding coverage for other languages.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from content_audit.checks import BaseChecker, CheckContext, _finding
from content_audit.domain import (
    ContentUnit,
    Criterion,
    Evidence,
    ExtractedEntity,
    Severity,
    TextLocation,
    Verdict,
)

_DATA = json.loads((Path(__file__).parent / "extra_checkers_data.json").read_text(encoding="utf-8"))
_XF = _DATA["xfile"]
_CO = _DATA["course"]

CODE_EXTS = (".js", ".ts", ".py", ".java", ".go", ".c", ".cpp", ".cc", ".sql", ".sh", ".rb", ".cs")
CODE_EXTS_SET = set(CODE_EXTS)
_IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".idea", ".vscode"}


def _disk_code_files(unit, limit_bytes: int = 200_000, max_files: int = 40):
    """Reads source-code files from the unit folder on disk.

    The ingestion step loads only docs (md/yml/txt), so code files are not in
    unit.files. We read them directly so cross-file/course checks can see code.
    Returns a list of (relative_path, text).
    """

    root = getattr(unit, "root_path", None)
    out = []
    if not root:
        return out
    root = Path(root)
    if not root.is_dir():
        return out
    for fp in sorted(root.rglob("*")):
        if len(out) >= max_files:
            break
        if not fp.is_file() or fp.suffix.lower() not in CODE_EXTS_SET:
            continue
        if any(part in _IGNORE_DIRS for part in fp.parts):
            continue
        try:
            if fp.stat().st_size > limit_bytes:
                continue
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        out.append((fp.relative_to(root).as_posix(), text))
    return out


def _disk_code_paths(unit, max_files: int = 200):
    """Lightweight scan of code-file relative paths (no read) for language inference."""

    root = getattr(unit, "root_path", None)
    if not root:
        return []
    root = Path(root)
    if not root.is_dir():
        return []
    paths = []
    for fp in root.rglob("*"):
        if len(paths) >= max_files:
            break
        if fp.is_file() and fp.suffix.lower() in CODE_EXTS_SET and not any(part in _IGNORE_DIRS for part in fp.parts):
            paths.append(fp.relative_to(root).as_posix())
    return paths
README_RE = re.compile(r"(?i)readme(?:_[a-z]+)?\.md$")
# IDENT(ARGS) [;] // EXPECTED  -- the documented-example pattern used in tasks
CALL_OUTPUT_RE = re.compile(r"([A-Za-z_]\w*)\s*\(([^()]*)\)\s*;?\s*//\s*(\S.*?)\s*$")


def _is_readme(path: str) -> bool:
    return bool(README_RE.search(path.replace("\\", "/")))


def _is_code(path: str) -> bool:
    return path.lower().endswith(CODE_EXTS)


def _parse_output(raw: str):
    """Normalize an expected-output comment into a comparable value."""

    s = raw.strip().strip("`").strip()
    pairs = re.findall(r"(\d+)\s*:\s*(-?\d+)", s)
    if pairs and "{" in s:
        return ("map", frozenset((int(a), int(b)) for a, b in pairs))
    return ("str", re.sub(r"\s+", "", s.lower().strip("'\"`{}() ")))


def _collect_call_outputs(unit: ContentUnit):
    """Map a normalized call signature to its documented outputs per file."""

    occ: dict = defaultdict(list)  # call_key -> [(path, line_no, raw_out, parsed)]
    sources = [(f.relative_path, f.text) for f in unit.files if _is_readme(f.relative_path) or _is_code(f.relative_path)]
    sources += _disk_code_files(unit)  # code files are not ingested; read from disk
    for path, text in sources:
        for line_no, line in enumerate(text.splitlines(), start=1):
            m = CALL_OUTPUT_RE.search(line)
            if not m:
                continue
            ident, args, out = m.group(1), m.group(2), m.group(3)
            if not out or len(out) > 200:
                continue
            call_key = "%s(%s)" % (ident, re.sub(r"\s+", "", args))
            occ[call_key].append((path, line_no, out.strip(), _parse_output(out)))
    return occ


class CrossFileConsistencyChecker(BaseChecker):
    """Flags spec<->code contradictions between README and the example code."""

    name = "cross_file_consistency_checker"
    prompt_version = "cross_file_consistency_checker:v1"

    model_context_limit = 6000

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        findings: list[Finding] = []
        findings.extend(self._deterministic(unit))
        if context is not None and getattr(context, "model_client", None) is not None:
            findings.extend(self._model(unit, context))
        return findings

    def _model(self, unit: ContentUnit, context: CheckContext) -> list[Finding]:
        client = context.model_client
        readmes = [f for f in unit.files if _is_readme(f.relative_path)]
        codes = [
            f for f in unit.files
            if _is_code(f.relative_path) and "/test" not in f.relative_path.replace("\\", "/").lower()
        ]
        if not readmes or not codes:
            return []
        readme_text = "\n\n".join(f.text for f in readmes)[: self.model_context_limit]
        findings: list[Finding] = []
        seen: set = set()
        for code in codes[:3]:
            user = "ЗАДАНИЕ (README):\n%s\n\nКОД-ПРИМЕР (%s):\n%s" % (
                readme_text, code.relative_path, code.text[: self.model_context_limit]
            )
            try:
                data = client.complete_json(_XF["model_system"], user)
            except Exception:
                continue
            for item in (data.get("findings") or []):
                if not isinstance(item, dict):
                    continue
                conf = float(item.get("confidence", 0) or 0)
                if conf < 0.6:
                    continue
                quote = str(item.get("quote", ""))[:160]
                key = (code.relative_path, quote)
                if key in seen:
                    continue
                seen.add(key)
                line_start = item.get("line_start")
                loc = TextLocation(
                    file_path=str(item.get("file_path") or code.relative_path),
                    line_start=int(line_start) if isinstance(line_start, (int, float)) else None,
                )
                severity = Severity.MAJOR if str(item.get("severity")) == "major" else Severity.MINOR
                findings.append(
                    _finding(
                        unit, self.name, Criterion.CORRECTNESS, severity, Verdict.FAIL, conf, quote, loc,
                        [Evidence(title=_XF["evidence_title"], detail=str(item.get("evidence", ""))[:300])],
                        str(item.get("recommendation", ""))[:300], True,
                        extra={"issue_type": "spec_code_contradiction"}, prompt_version=self.prompt_version,
                    )
                )
        return findings

    def _deterministic(self, unit: ContentUnit) -> list[Finding]:
        findings: list[Finding] = []
        for call_key, occ in _collect_call_outputs(unit).items():
            files = {o[0] for o in occ}
            if len(files) < 2:
                continue  # need the call documented in at least two files
            parsed_by_file: dict = {}
            for path, line_no, raw, parsed in occ:
                parsed_by_file.setdefault(path, (line_no, raw, parsed))
            distinct = {pf[2] for pf in parsed_by_file.values()}
            if len(distinct) < 2:
                continue  # same output everywhere -> consistent
            # pick a README occurrence as anchor if present, else first file
            ordered = sorted(parsed_by_file.items(), key=lambda kv: (0 if _is_readme(kv[0]) else 1, kv[0]))
            a_file, (a_line, a_raw, _ap) = ordered[0]
            b_file, (b_line, b_raw, _bp) = ordered[1]
            evidence = Evidence(
                title=_XF["evidence_title"],
                detail=_XF["evidence_detail"].format(
                    call=call_key, a_file=a_file, a_out=a_raw[:120], b_file=b_file, b_out=b_raw[:120]
                ),
            )
            findings.append(
                _finding(
                    unit,
                    self.name,
                    Criterion.CORRECTNESS,
                    Severity.MAJOR,
                    Verdict.FAIL,
                    0.85,
                    a_raw[:160],
                    TextLocation(file_path=a_file, line_start=a_line, line_end=a_line),
                    [evidence],
                    _XF["recommendation"].format(a_file=a_file, b_file=b_file),
                    True,
                    extra={"issue_type": "spec_code_output_mismatch", "call": call_key},
                )
            )
        return findings


# ---- course-material relevance (complements CurriculumRelevanceChecker) ----

EXT_LANG = {
    ".py": "python", ".java": "java", ".go": "go", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".js": "javascript", ".ts": "javascript",
    ".rb": "ruby", ".cs": "csharp", ".sql": "sql", ".sh": "bash",
}
# tech token -> (language it belongs to, regex)
TECH_RULES = (
    ("cpp", re.compile(r"\b(?:google\s+)?c\+\+\s*(?:style|guide)\b|\bcppguide\b", re.IGNORECASE)),
    ("python", re.compile(r"\bpep\s*-?\s*8\b", re.IGNORECASE)),
    ("csharp", re.compile(r"\basp\.?net\b|\bc#\b", re.IGNORECASE)),
    ("go", re.compile(r"\bgofmt\b|\beffective\s+go\b", re.IGNORECASE)),
    ("java", re.compile(r"\bgoogle\s+java\s+style\b|\bcheckstyle\b", re.IGNORECASE)),
    ("javascript", re.compile(r"\bairbnb\s+javascript\b|\beslint\b|\bprettier\b", re.IGNORECASE)),
    ("ruby", re.compile(r"\brubocop\b", re.IGNORECASE)),
)
# pairs already covered by CurriculumRelevanceChecker - avoid double findings
CURRICULUM_COVERED = {("cpp", "java"), ("csharp", "java"), ("cpp", "c")}
INSTRUCTION_RE = re.compile(r"(?i)(readme(?:_[a-z]+)?\.md$|materials/|instructions?)")


class CourseMaterialRelevanceChecker(BaseChecker):
    """Flags style guides / frameworks of a language other than the unit's."""

    name = "course_material_relevance_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities, context
        unit_lang = self._unit_language(unit)
        if unit_lang is None:
            return []
        findings: list[Finding] = []
        seen: set = set()
        for file in unit.files:
            path = file.relative_path
            if not INSTRUCTION_RE.search(path.replace("\\", "/")):
                continue
            for line_no, line in enumerate(file.text.splitlines(), start=1):
                for tech_lang, pattern in TECH_RULES:
                    if tech_lang == unit_lang:
                        continue
                    if (tech_lang, unit_lang) in CURRICULUM_COVERED:
                        continue
                    m = pattern.search(line)
                    if not m:
                        continue
                    key = (path, tech_lang)
                    if key in seen:
                        continue
                    seen.add(key)
                    tech = m.group(0)
                    evidence = Evidence(
                        title=_CO["evidence_title"],
                        detail=_CO["evidence_detail"].format(tech=tech, tech_lang=tech_lang, unit_lang=unit_lang),
                    )
                    findings.append(
                        _finding(
                            unit,
                            self.name,
                            Criterion.CORRECTNESS,
                            Severity.MAJOR,
                            Verdict.FAIL,
                            0.8,
                            line.strip()[:160],
                            TextLocation(file_path=path, line_start=line_no, line_end=line_no),
                            [evidence],
                            _CO["recommendation"].format(tech=tech, unit_lang=unit_lang),
                            True,
                            extra={"issue_type": "course_language_material_mismatch", "tech_lang": tech_lang, "unit_lang": unit_lang},
                        )
                    )
        return findings

    def _unit_language(self, unit: ContentUnit) -> str | None:
        counts: dict = defaultdict(int)
        for rel in _disk_code_paths(unit):
            rel = rel.replace("\\", "/").lower()
            base = Path(rel).name
            if re.search(r"test", rel) or base in ("build.py", "conftest.py", "setup.py", "add_args.py"):
                continue  # skip test-harness / build scripts so they don't decide the language
            ext = Path(rel).suffix.lower()
            lang = EXT_LANG.get(ext)
            if lang and lang not in ("sql", "bash"):  # ignore ubiquitous helper langs
                counts[lang] += 1
        if not counts:
            # fall back to project name hint
            name = unit.name.lower()
            for hint, lang in (("java", "java"), ("_go_", "go"), ("_js", "javascript"), ("python", "python")):
                if hint in name:
                    return lang
            return None
        return max(counts.items(), key=lambda kv: kv[1])[0]


# Finding type is only needed for annotations; import here to avoid an unused
# top-level import if the module is partially loaded.
from content_audit.domain import Finding  # noqa: E402

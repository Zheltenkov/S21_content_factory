"""Dataset and output loading helpers for offline evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .models import GeneratedProjectOutput, GoldenDataset


def load_golden_dataset(path: str | Path) -> GoldenDataset:
    """Load a golden dataset from JSON/YAML using the typed dataset contract."""
    dataset_path = Path(path)
    payload = _read_structured_file(dataset_path)
    if isinstance(payload, list):
        payload = {"cases": payload}
    if not isinstance(payload, dict):
        raise ValueError(f"Golden dataset must be a mapping or list: {dataset_path}")
    return GoldenDataset.model_validate(payload)


def load_generated_outputs(path: str | Path) -> dict[str, GeneratedProjectOutput]:
    """Load generated README outputs and resolve optional markdown_path entries."""
    output_path = Path(path)
    payload = _read_structured_file(output_path)
    raw_outputs = payload.get("outputs", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_outputs, list):
        raise ValueError(f"Generated outputs must be a list or contain outputs[]: {output_path}")

    outputs: dict[str, GeneratedProjectOutput] = {}
    for raw in raw_outputs:
        if not isinstance(raw, dict):
            raise ValueError(f"Generated output entry must be a mapping: {raw!r}")
        entry = dict(raw)
        markdown = str(entry.get("markdown") or "")
        markdown_path = entry.pop("markdown_path", None)
        if not markdown and markdown_path:
            resolved = (output_path.parent / str(markdown_path)).resolve()
            markdown = resolved.read_text(encoding="utf-8")
        entry["markdown"] = markdown
        output = GeneratedProjectOutput.model_validate(entry)
        if output.case_id in outputs:
            raise ValueError(f"Duplicate generated output for case_id={output.case_id!r}")
        outputs[output.case_id] = output
    return outputs


def _read_structured_file(path: Path) -> Any:
    """Read JSON/YAML by extension without guessing formats from contents."""
    suffix = path.suffix.casefold()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        return json.loads(text)
    if suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text) or {}
    raise ValueError(f"Unsupported evaluation file extension: {path.suffix}")

"""Run offline golden-set evaluation for generated README outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_factory.generation.evaluation import (  # noqa: E402
    EvaluationHarness,
    evaluation_gate_failures,
    load_generated_outputs,
    load_golden_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate generated README outputs against a golden dataset.")
    parser.add_argument("--dataset", required=True, help="Path to golden dataset JSON/YAML.")
    parser.add_argument("--outputs", required=True, help="Path to generated outputs JSON/YAML.")
    parser.add_argument("--out", help="Optional JSON report path.")
    parser.add_argument("--fail-under", type=float, default=None, help="Fail process when run pass_rate is lower.")
    parser.add_argument("--require-cases", type=int, default=None, help="Fail when the dataset has fewer cases.")
    parser.add_argument("--fail-on-missing", action="store_true", help="Fail when a dataset case has no output.")
    parser.add_argument("--fail-on-errors", action="store_true", help="Fail when a case evaluation errors.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail unless every case passes and there are no missing outputs or evaluation errors.",
    )
    args = parser.parse_args()

    dataset = load_golden_dataset(args.dataset)
    outputs = load_generated_outputs(args.outputs)
    summary = EvaluationHarness().evaluate_dataset(dataset, outputs)
    payload = summary.model_dump(mode="json", by_alias=True)
    pass_rate_floor = args.fail_under
    fail_on_missing = args.fail_on_missing
    fail_on_errors = args.fail_on_errors
    if args.strict:
        pass_rate_floor = 1.0 if pass_rate_floor is None else pass_rate_floor
        fail_on_missing = True
        fail_on_errors = True
    gate_failures = evaluation_gate_failures(
        summary,
        pass_rate_floor=pass_rate_floor,
        require_cases=args.require_cases,
        fail_on_missing=fail_on_missing,
        fail_on_errors=fail_on_errors,
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if gate_failures:
        for failure in gate_failures:
            print(f"EVAL_GATE {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

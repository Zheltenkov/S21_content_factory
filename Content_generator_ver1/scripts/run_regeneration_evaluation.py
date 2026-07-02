"""Run offline regression evaluation for README regeneration outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_gen.evaluation import (  # noqa: E402
    RegenerationEvaluationHarness,
    load_regeneration_eval_dataset,
    load_regeneration_eval_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate regenerated README outputs against scoped regeneration regression cases."
    )
    parser.add_argument("--dataset", required=True, help="Path to regeneration eval dataset JSON/YAML.")
    parser.add_argument("--outputs", required=True, help="Path to regenerated outputs JSON/YAML.")
    parser.add_argument("--out", help="Optional JSON report path.")
    parser.add_argument("--fail-under", type=float, default=None, help="Fail when pass_rate is lower.")
    args = parser.parse_args()

    dataset = load_regeneration_eval_dataset(args.dataset)
    outputs = load_regeneration_eval_outputs(args.outputs)
    summary = RegenerationEvaluationHarness().evaluate_dataset(dataset, outputs)
    payload = summary.model_dump(mode="json", by_alias=True)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.fail_under is not None and summary.pass_rate < args.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

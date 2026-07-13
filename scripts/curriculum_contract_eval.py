#!/usr/bin/env python
"""Golden contract eval for a built UP (project-contract epic, slice 9).

Run after rebuilding a UP to see the contract metrics + publication-gate verdict, optionally
against a baseline (e.g. the pre-contract UP #30 run) to show the improvement.

Usage:
  python scripts/curriculum_contract_eval.py --plan-id 30 [--baseline tests/catalog/golden/up30_baseline.json]
  python scripts/curriculum_contract_eval.py --payload plan.json [--baseline ...]

Exit code: 0 if the publication gate passed, 1 if blocked (usable as a CI/pre-publish check).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from content_factory.catalog.pipeline.curriculum.contract_eval import (
    gate_passed,
    render_summary,
    summarize_plan,
)


def _load_plan_from_db(plan_id: int) -> dict[str, Any]:
    from content_factory.catalog.db import open_catalog_connection
    from content_factory.catalog.viewer.curriculum_ops import get_curriculum_plan

    conn = open_catalog_connection()
    try:
        plan = get_curriculum_plan(conn, plan_id)
    finally:
        conn.close()
    if plan is None:
        raise SystemExit(f"UP #{plan_id} не найден")
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden contract eval for a built UP")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan-id", type=int, help="UP plan_id to load from the catalog DB")
    source.add_argument("--payload", type=Path, help="UP plan payload JSON file")
    parser.add_argument("--baseline", type=Path, help="Baseline summary JSON for before -> after")
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON (e.g. to save a baseline)")
    args = parser.parse_args(argv)

    plan = json.loads(args.payload.read_text(encoding="utf-8")) if args.payload else _load_plan_from_db(args.plan_id)
    summary = summarize_plan(plan)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if gate_passed(summary) else 1

    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    print(render_summary(summary, baseline))
    return 0 if gate_passed(summary) else 1


if __name__ == "__main__":
    sys.exit(main())

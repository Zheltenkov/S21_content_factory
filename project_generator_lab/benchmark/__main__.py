"""Measure the projects of a folder by layer 1 (code). No model is called and nothing is generated.

    python -m project_generator_lab.benchmark <каталог со случаями или репозиториями> [--out <каталог>] [--release]

Results: <out>/report.md and report.json; by default data/benchmarks/<time>-code/.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from . import cases, code_layer, repository

PACKAGE = Path(__file__).resolve().parent.parent


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m project_generator_lab.benchmark", description="Слой кода: проверки генератора на готовых проектах.")
    parser.add_argument("root", type=Path, help="каталог случая, каталог со случаями или с репозиториями проектов")
    parser.add_argument("--out", type=Path, help="куда положить report.md и report.json")
    parser.add_argument("--release", action="store_true", help="измерить и отложенные случаи (split: held_out)")
    options = parser.parse_args(arguments)
    if options.out and options.out.exists() and not options.out.is_dir():
        parser.error(f"--out: «{options.out}» — файл, а нужен каталог")
    try:
        found, passed = cases.find(options.root)
    except ValueError as error:
        parser.error(str(error))
    held = [case.name for case in found if case.meta.split == "held_out" and not options.release]
    measured = []
    for case in found:
        if case.name in held:
            continue
        if case.etalon is None:
            passed.append(f"«{case.name}» — у случая нет готового проекта (etalon/): слою кода нечего измерять")
            continue
        try:
            measured.append(code_layer.measure(repository.read(case.etalon, case.meta.readme, case.meta.checklist, case.meta.skip, case.meta.broken)._replace(name=case.name)))
        except ValueError as error:
            passed.append(str(error))
        except Exception as error:  # noqa: BLE001 — one project that breaks the reader must not cost the report of the rest
            passed.append(f"«{case.name}» — не прочитан ({type(error).__name__})")
    text = code_layer.report(measured, passed, held)
    out = options.out or PACKAGE / "data" / "benchmarks" / f"{datetime.now():%Y%m%d-%H%M%S}-code"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "report.json").write_text(json.dumps({"projects": [code_layer.row(m) for m in measured], "passed": passed, "held_out": held},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
    print(text.split("## Что проверено")[0].strip())
    print(f"\nОтчёт: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

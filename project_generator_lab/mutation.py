"""Mutation testing for the lab's deterministic code.

Usage (Linux, macOS or WSL; mutmut does not run on native Windows):
    pip install "mutmut>=3.8,<4"
    python -m project_generator_lab.mutation            # full report
    python -m project_generator_lab.mutation --min-score 75

The run happens in a temporary copy, so the working tree and data/runs stay untouched.
Surviving mutants are grouped by function: a cluster in logic (budget, check, program_position,
build_patches) is a missing test; a cluster in prompt text or recommendation wording is usually accepted.
"""

from __future__ import annotations

import argparse
import collections
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
# Code whose behaviour is fully computable: every surviving mutant here is a question to the tests.
# Paths inside the package: the concept stage, the shared core and the content stage.
TARGETS = [
    "concept/workload.py", "concept/input_semantics.py", "concept/dialogue.py", "concept/thread.py", "concept/patches.py",
    "concept/answers.py", "concept/route.py", "concept/ingestion.py", "concept/skills.py", "concept/benchmark.py",
    "concept/freshness.py", "concept/validation.py", "concept/planning.py", "concept/runs.py", "core/llm.py", "core/intents.py",
    "core/web.py", "core/storage.py", "core/isolated.py", "core/chat.py", "core/contracts.py", "content/store.py", "bridge.py",
    "content/models.py", "content/handoff.py",
    "content/checks.py", "content/edits.py", "content/stages.py", "content/render.py", "content/flow.py",
    # What a writer is sent: the style it reads and the fields a draft begun before the styles must not gain.
    "content/writer.py",
    # The search for links and for an excerpt of a primary source: what code takes from the pages and what it refuses.
    "content/research.py",
    # The chat around the model: what it is given and what code decides without it.
    "content/api.py",
    # The measure of projects that exist: a ruler that reads wrong measures wrong.
    "benchmark/repository.py", "benchmark/cases.py", "benchmark/code_layer.py", "benchmark/word.py",
    # The judge: everything code does around the model — what it is shown, which quotes are believed, how marks are counted.
    "benchmark/criteria.py", "benchmark/judge.py", "benchmark/seeded.py",
    # The account of what a project cost: a sum that leaves a call out, or counts another owner's, is a wrong price.
    "core/usage.py", "usage.py",
]
# «--chat»: only the code between the methodologist's message and the model's proposal.
CHAT = ["core/intents.py", "core/chat.py", "content/edits.py", "content/api.py", "concept/dialogue.py", "concept/patches.py",
        "concept/answers.py", "concept/route.py"]


def config(targets: list[str]) -> str:
    sources = "\n    ".join(f"project_generator_lab/{name}" for name in targets)
    return (
        "[mutmut]\n"
        f"source_paths =\n    {sources}\n"
        "pytest_add_cli_args_test_selection =\n    project_generator_lab/tests/\n"
        "also_copy =\n    project_generator_lab/\n"
    )


def pattern(name: str) -> str:
    """mutmut's name for the mutants of a function («core.chat.move») or a method («core.storage.Locks.__call__»)."""
    owner, function = name.rsplit(".", 1)
    module, _, cls = owner.rpartition(".")
    if cls[:1].isupper():
        return f"project_generator_lab.{module}.xǁ{cls}ǁ{function}__mutmut_*"
    return f"project_generator_lab.{owner}.x_{function}__mutmut_*"


def mutmut(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "mutmut", *args], cwd=cwd, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-score", type=float, default=0, help="fail when the killed share (%%) is lower")
    parser.add_argument("--only", nargs="*", choices=TARGETS, help="mutate only these files")
    parser.add_argument("--chat", action="store_true", help="mutate only the chat code: " + ", ".join(CHAT))
    parser.add_argument("--keep", action="store_true", help="keep the temporary copy for `mutmut show <name>`")
    parser.add_argument("--functions", nargs="*", default=[],
                        help="mutate only these functions, e.g. concept.dialogue.by_kind or core.storage.Locks.__call__ "
                             "(with --only for their files)")
    options = parser.parse_args()
    if os.name == "nt":
        print("mutmut 3 не работает в Windows напрямую: запустите команду в WSL или в CI на Linux.")
        return 2
    work = Path(tempfile.mkdtemp(prefix="lab-mutation-"))
    shutil.copytree(
        PACKAGE,
        work / "project_generator_lab",
        ignore=shutil.ignore_patterns("__pycache__", "data", "mutants", "*.pyc"),
    )
    (work / "setup.cfg").write_text(config(CHAT if options.chat else options.only or TARGETS), encoding="utf-8")
    # mutmut names a function's mutants «<module>.x_<function>__mutmut_<n>» and a method's «<module>.xǁ<Class>ǁ<method>__…»;
    # a pattern per function selects them.
    patterns = [pattern(name) for name in options.functions]
    run = mutmut(["run", *patterns], work)
    if run.returncode:
        print(run.stdout[-2000:], run.stderr[-2000:], sep="\n")
        return run.returncode
    lines = mutmut(["results", "--all", "true"], work).stdout.splitlines()
    if patterns:
        # Only the selected functions count: the rest of the file was not run and would read as «not checked».
        lines = [line for line in lines if any(fnmatch.fnmatch(line.split(":")[0].strip(), p) for p in patterns)]
    states = collections.Counter(line.rsplit(":", 1)[1].strip() for line in lines if ":" in line)
    survivors = collections.Counter(
        re.sub(r"__mutmut_\d+$", "", line.split(":")[0].strip()).replace(".x_", ".")
        for line in lines
        if line.endswith("survived")
    )
    total = sum(states.values())
    killed = states.get("killed", 0)
    score = 100 * killed / total if total else 0
    print(f"Убито {killed} из {total} мутантов ({score:.0f}%). Состояния: {dict(states)}")
    for name, count in survivors.most_common():
        print(f"  выжило {count:3} — {name}")
    if options.keep:
        print(f"Копия: {work}  (cd туда и `python -m mutmut show <имя>`)")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if score < options.min_score else 0


if __name__ == "__main__":
    raise SystemExit(main())

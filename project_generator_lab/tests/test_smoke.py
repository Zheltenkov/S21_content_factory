"""The run the release script makes in the new image before the switch (smoke.py) must pass here too: a change that
breaks it would otherwise be found on the production host, with a release half made."""

import os
import subprocess
import sys
from pathlib import Path

from project_generator_lab import smoke

PACKAGE = Path(__file__).resolve().parents[1]


def test_the_run_before_the_switch_passes_and_touches_no_folder_of_the_service(tmp_path):
    # The folders the service is configured with stand in the environment, as they do in the image: the run must
    # put its own in their place, not write a project of its own among the methodologists' ones.
    kept = {name: str(tmp_path / name) for name in ("runs", "content", "usage")}
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PROJECT_LAB_RUNS_DIR": kept["runs"],
           "PROJECT_LAB_CONTENT_DIR": kept["content"], "PROJECT_LAB_USAGE_DIR": kept["usage"]}
    done = subprocess.run([sys.executable, "-m", f"{PACKAGE.name}.smoke"], cwd=PACKAGE.parent, env=env, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=180)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "smoke ok: route" in done.stdout and "searches" in done.stdout
    assert "python " in done.stdout and "fastapi " in done.stdout  # the libraries it ran on are named
    assert not any((tmp_path / name).exists() for name in kept)


def test_a_step_that_goes_wrong_ends_the_run_with_a_failure_and_names_it(monkeypatch, capsys, tmp_path):
    made = []

    def broken(work):
        made.append(work)
        (work / "left.txt").write_text("x", encoding="ascii")
        smoke.expect(1 + 1 == 3, "the route does not come to its end", "ответ сервиса")
        return "never"

    monkeypatch.setattr(smoke, "run", broken)
    assert smoke.main() == 1
    out = capsys.readouterr().out
    assert "smoke FAILED: the route does not come to its end: ответ сервиса" in out and "python " in out
    assert made and not made[0].exists()  # the temporary folder is removed whatever happened


def test_code_that_breaks_is_a_failure_too_not_a_pass(monkeypatch, capsys):
    def breaks(work):
        raise TypeError("create_task() got an unexpected keyword argument")

    monkeypatch.setattr(smoke, "run", breaks)
    assert smoke.main() == 1
    captured = capsys.readouterr()
    assert "smoke FAILED: the run broke off" in captured.out and "unexpected keyword argument" in captured.err


def test_expect_passes_what_holds_and_cuts_a_long_answer():
    smoke.expect(True, "anything")
    try:
        smoke.expect(False, "the page", "я" * 1000)
    except smoke.Failed as failure:
        assert str(failure).startswith("the page: я") and len(str(failure)) < 420
    else:
        raise AssertionError("a failed step must stop the run")

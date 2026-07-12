from datetime import datetime, timezone
from pathlib import Path

from content_factory.audit.cache import AuditCache
from content_factory.audit.checks import CheckContext, DependencyFreshnessChecker
from content_factory.audit.dependencies import DependencyCandidate, DependencyMetadata, DependencyRegistryClient
from content_factory.audit.domain import AuditSettings, Criterion, Severity, Verdict
from content_factory.audit.ingestion import discover_content_units, load_unit_files


def _settings(tmp_path: Path, project: Path, allow_network: bool = True) -> AuditSettings:
    return AuditSettings(input_path=project, output_path=tmp_path / "out", allow_network=allow_network)


def test_dependency_manifests_are_loaded(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("# Проект\n", encoding="utf-8")
    (project / "package.json").write_text('{"dependencies":{"react":"^17.0.0"}}', encoding="utf-8")
    (project / "pyproject.toml").write_text('[project]\ndependencies=["requests==2.31.0"]\n', encoding="utf-8")
    build_dir = project / "materials" / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")

    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=5000)

    loaded = {file.relative_path: file.kind for file in unit.files}
    assert loaded["package.json"] == "dependency_manifest"
    assert loaded["pyproject.toml"] == "dependency_manifest"
    assert loaded["materials/build/Dockerfile"] == "dependency_manifest"


def test_dependency_checker_finds_npm_peer_conflict(workspace_tmp_path: Path, monkeypatch) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("# Проект\n", encoding="utf-8")
    (project / "package.json").write_text(
        '{"dependencies":{"react":"^17.0.0","react-dom":"17.0.0"}}',
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=5000)

    def fake_fetch(self, candidate: DependencyCandidate) -> DependencyMetadata:
        del self
        return DependencyMetadata(
            ecosystem="npm",
            name=candidate.name,
            latest_version="18.2.0",
            source_url=f"https://registry.npmjs.org/{candidate.name}",
            checked_at=datetime.now(timezone.utc),
            peer_dependencies={"react": "^18.0.0"} if candidate.name == "react-dom" else {},
        )

    monkeypatch.setattr(DependencyRegistryClient, "fetch", fake_fetch)
    context = CheckContext(_settings(workspace_tmp_path, project), cache=AuditCache.load(workspace_tmp_path / "cache.json"))

    findings = DependencyFreshnessChecker().check(unit, [], context)

    compatibility = [finding for finding in findings if finding.support_status == "конфликт ограничений"]
    assert compatibility
    assert compatibility[0].criterion == Criterion.TECHNOLOGY_FRESHNESS
    assert compatibility[0].severity == Severity.MAJOR
    assert compatibility[0].verdict == Verdict.WARNING
    assert "react^17.0.0" in compatibility[0].evidence[0].detail
    assert "^18.0.0" in compatibility[0].evidence[0].detail


def test_dependency_checker_records_successful_registry_check(workspace_tmp_path: Path, monkeypatch) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("# Проект\n", encoding="utf-8")
    (project / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=5000)

    def fake_fetch(self, candidate: DependencyCandidate) -> DependencyMetadata:
        del self
        return DependencyMetadata(
            ecosystem="pypi",
            name=candidate.name,
            latest_version="2.31.0",
            source_url=f"https://pypi.org/pypi/{candidate.name}/json",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(DependencyRegistryClient, "fetch", fake_fetch)
    context = CheckContext(_settings(workspace_tmp_path, project), cache=AuditCache.load(workspace_tmp_path / "cache.json"))

    findings = DependencyFreshnessChecker().check(unit, [], context)

    assert findings[0].verdict == Verdict.PASS
    assert findings[0].support_status == "проверено"
    assert findings[0].latest_version == "2.31.0"


def test_dependency_checker_reports_network_required(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("# Проект\n", encoding="utf-8")
    (project / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=5000)
    context = CheckContext(_settings(workspace_tmp_path, project, allow_network=False))

    findings = DependencyFreshnessChecker().check(unit, [], context)

    assert findings[0].verdict == Verdict.UNKNOWN
    assert findings[0].support_status == "не проверялось"


def test_dependency_checker_does_not_fetch_runtime_constraints(workspace_tmp_path: Path, monkeypatch) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("# Проект\n", encoding="utf-8")
    (project / "package.json").write_text('{"engines":{"node":">=20"}}', encoding="utf-8")
    (project / "pyproject.toml").write_text('[project]\nrequires-python=">=3.12"\n', encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=5000)

    def fail_fetch(self, candidate: DependencyCandidate) -> DependencyMetadata:
        del self
        raise AssertionError(f"runtime constraint was fetched as package: {candidate.name}")

    monkeypatch.setattr(DependencyRegistryClient, "fetch", fail_fetch)

    findings = DependencyFreshnessChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []

from content_audit.cache import AuditCache


def test_audit_cache_persists_namespaced_records(workspace_tmp_path) -> None:
    cache_path = workspace_tmp_path / "reports" / "audit_cache.json"
    cache = AuditCache.load(cache_path)

    cache.set("fact", "same-key", {"response": {"verdict": "pass"}})
    cache.set("technology", "same-key", {"response": {"verdict": "warning"}})
    cache.save()

    restored = AuditCache.load(cache_path)

    assert restored.get("fact", "same-key") == {"response": {"verdict": "pass"}}
    assert restored.get("technology", "same-key") == {"response": {"verdict": "warning"}}

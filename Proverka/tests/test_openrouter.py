import requests

from content_audit.openrouter import OpenRouterClient, OpenRouterError


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        error = requests.HTTPError(f"{self.status_code} Client Error")
        error.response = self
        raise error

    def json(self) -> dict:
        return self._payload


def test_openrouter_retries_without_response_format_after_400(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(_url, headers, json, timeout):
        del headers, timeout
        calls.append(dict(json))
        if len(calls) == 1:
            return _FakeResponse(400, text='{"error":"response_format is not supported"}')
        return _FakeResponse(
            200,
            {
                "choices": [{"message": {"content": '{"verdict":"pass","confidence":0.9}'}}],
                "usage": {"total_tokens": 12},
            },
        )

    monkeypatch.setattr("content_audit.openrouter.requests.post", fake_post)

    result = OpenRouterClient(api_key="test-key", model="test-model").complete_json("system", "user")

    assert result["verdict"] == "pass"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_openrouter_error_includes_provider_body(monkeypatch) -> None:
    def fake_post(_url, headers, json, timeout):
        del headers, json, timeout
        return _FakeResponse(400, text='{"error":"invalid model"}')

    monkeypatch.setattr("content_audit.openrouter.requests.post", fake_post)

    try:
        OpenRouterClient(api_key="test-key", model="bad-model").complete_json("system", "user", max_retries=0)
    except OpenRouterError as exc:
        assert "HTTP 400" in str(exc)
        assert "invalid model" in str(exc)
    else:
        raise AssertionError("OpenRouterError was not raised")

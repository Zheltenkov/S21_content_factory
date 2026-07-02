import io
import zipfile
from unittest.mock import patch

import pytest

from api.routers.download import download_results


@pytest.mark.asyncio
async def test_download_archive_contains_readme_alias():
    cached = {
        "markdown": "# Test README",
        "assets": {},
        "user_id": "user_1",
    }
    report_json = {
        "title": "Публичные выступления",
        "title_en": "PublicSpeaking",
        "context": {"track": "PjM", "last_order": 14},
    }

    with patch("api.routers.download.get_result", return_value=cached):
        with patch("api.routers.download.get_generation_result", return_value=None):
            with patch("api.routers.download.get_report_by_request_id", return_value=report_json):
                response = await download_results(
                    request_id="req-1",
                    include_regenerated=False,
                    user={"id": "user_1"},
                )

    archive = response.body_iterator
    chunks = []
    async for chunk in archive:
        chunks.append(chunk)

    data = b"".join(chunks)
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        names = set(zf.namelist())

    assert "README.md" in names
    assert "PjM15_PublicSpeaking.md" in names

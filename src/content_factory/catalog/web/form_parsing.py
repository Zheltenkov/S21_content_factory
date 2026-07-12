"""Shared form parsing helpers for catalog server-rendered routes."""

from __future__ import annotations

from fastapi import Request
from starlette.datastructures import UploadFile as StarletteUploadFile

from content_factory.catalog.viewer._common import UploadedFile


async def form_and_files(request: Request) -> tuple[dict[str, str], dict[str, UploadedFile]]:
    """Split a multipart body into plain form fields and uploaded files."""

    data = await request.form()
    form_data: dict[str, str] = {}
    files: dict[str, UploadedFile] = {}
    for key, value in data.multi_items():
        if isinstance(value, StarletteUploadFile):
            payload = await value.read()
            if value.filename and payload:
                files[key] = UploadedFile(
                    filename=value.filename,
                    content_type=value.content_type or "application/octet-stream",
                    data=payload,
                )
            continue
        form_data[key] = str(value)
    return form_data, files


async def form_fields(request: Request) -> dict[str, str]:
    """Return only text fields from a request form."""

    form_data, _files = await form_and_files(request)
    return form_data

"""Brief upload/text extraction for the intake UI.

This module is intentionally small and deep: callers only ask for a normalized
brief text tuple, while CSV/DOCX decoding details stay local.
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from content_factory.catalog.viewer._common import UploadedFile


def decode_uploaded_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_docx_text(data: bytes) -> str:
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text for node in paragraph.findall(".//w:t", namespace) if node.text]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def extract_csv_text(data: bytes) -> str:
    decoded = decode_uploaded_text(data)
    sample = decoded[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    rows: list[str] = []
    reader = csv.reader(io.StringIO(decoded), dialect)
    for row in reader:
        cells = [cell.replace("\ufeff", "").strip() for cell in row]
        non_empty = [cell for cell in cells if cell]
        if not non_empty:
            continue
        if len(non_empty) == 1:
            rows.append(non_empty[0])
            continue
        head, tail = non_empty[0], non_empty[1:]
        if len(tail) == 1:
            rows.append(f"{head}: {tail[0]}")
            continue
        rows.append(f"{head}: {' | '.join(tail)}")
    return "\n\n".join(rows)


def extract_brief_text_from_bytes(data: bytes, suffix: str) -> str:
    if suffix in {".txt", ".md"}:
        return decode_uploaded_text(data).strip()
    if suffix == ".csv":
        return extract_csv_text(data).strip()
    if suffix == ".docx":
        return extract_docx_text(data).strip()
    raise ValueError("Поддерживаются только файлы .txt, .md, .csv и .docx.")


def load_brief_text(
    form_data: dict[str, str],
    files: dict[str, UploadedFile],
) -> tuple[str, str | None, str, str | None]:
    """Resolve the intake brief from an uploaded file or pasted text.

    Server-side filesystem paths are intentionally NOT accepted here: reading an
    arbitrary process-local path from web form data was a local file-disclosure
    vector, so only multipart upload (``brief_file``) and pasted ``brief`` text are
    supported. The 4th tuple element (legacy ``file_path``) is always ``None``.
    """
    uploaded_file = files.get("brief_file")
    if uploaded_file:
        suffix = Path(uploaded_file.filename).suffix.casefold()
        brief_text = extract_brief_text_from_bytes(uploaded_file.data, suffix)
        return brief_text, uploaded_file.filename, "file", None

    brief_text = form_data.get("brief", "").strip()
    if brief_text:
        return brief_text, None, "text", None

    return "", None, "text", None

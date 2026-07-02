"""OpenAI embedding helpers for semantic checks.

This module is intentionally independent from Chroma or any retrieval store.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

try:
    from openai import OpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


def create_openai_embeddings(
    texts: list[str],
    model: str = "text-embedding-3-large",
    api_key: str | None = None,
    base_url: str | None = None,
    dimensions: int | None = None,
) -> list[list[float]]:
    """Create embeddings through OpenAI API."""
    if not OPENAI_AVAILABLE:
        raise RuntimeError("OpenAI library is not installed. Install openai to use embeddings.")
    if not texts:
        return []

    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    client_kwargs = {"api_key": api_key}
    resolved_base_url = base_url or os.getenv("OPENAI_BASE_URL")
    if resolved_base_url:
        client_kwargs["base_url"] = resolved_base_url

    client = OpenAI(**client_kwargs)
    request_params = {"model": model}
    if dimensions is not None:
        request_params["dimensions"] = dimensions

    embeddings: list[list[float]] = []
    for i in range(0, len(texts), 2048):
        batch = texts[i : i + 2048]
        response = client.embeddings.create(input=batch, **request_params)
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


class OpenAIEmbeddingFunction:
    """Callable embedding adapter used by validators."""

    def __init__(
        self,
        model: str = "text-embedding-3-large",
        api_key: str | None = None,
        base_url: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.dimensions = dimensions

    def __call__(self, input: str | list[str]) -> list[list[float]]:
        texts = [input] if isinstance(input, str) else input
        return create_openai_embeddings(
            texts,
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            dimensions=self.dimensions,
        )


def create_embedding_function(
    model: str = "text-embedding-3-large",
    api_key: str | None = None,
    base_url: str | None = None,
    dimensions: int | None = None,
) -> OpenAIEmbeddingFunction:
    """Return a callable embedding adapter without binding to a vector store."""
    return OpenAIEmbeddingFunction(model=model, api_key=api_key, base_url=base_url, dimensions=dimensions)

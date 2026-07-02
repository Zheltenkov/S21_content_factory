"""Structured-output client facade.

New code should depend on :class:`StructuredOutputRunner`. This facade remains
for components that still call ``complete_structured`` directly while their
constructors are migrated to the runner contract.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from .structured_runner import (
    InstructorStructuredOutputRunner,
    StructuredOutputPrompt,
    StructuredOutputRunner,
    prepare_openai_json_schema,
    supports_structured_outputs,
)

T = TypeVar("T", bound=BaseModel)


class StructuredLLMClient:
    """Compatibility wrapper around the structured-output runner contract."""

    SUPPORTED_MODELS = {
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-5.4-mini",
        "o1",
        "o1-mini",
        "o1-preview",
        "o1-mini-preview",
    }

    def __init__(
        self,
        llm_client: Any,
        *,
        runner: StructuredOutputRunner | None = None,
    ) -> None:
        self.llm = llm_client
        self.runner = runner or InstructorStructuredOutputRunner()

    def _supports_structured_outputs(self) -> bool:
        """Return whether the wrapped model is known to support schema mode."""
        return supports_structured_outputs(getattr(self.llm, "model", None))

    def _prepare_json_schema(self, model_class: type[BaseModel]) -> dict[str, Any]:
        """Return an OpenAI-compatible JSON schema for test callers."""
        return prepare_openai_json_schema(model_class)

    def run(
        self,
        *,
        prompt: StructuredOutputPrompt,
        response_model: type[T],
        retries: int = 3,
    ) -> T:
        """Execute the new runner contract against the wrapped LLM client."""
        return self.runner.run(
            model=self.llm,
            prompt=prompt,
            response_model=response_model,
            retries=retries,
        )

    def complete_structured(
        self,
        output_model: type[T],
        system: str,
        user: str,
        use_structured_outputs: bool | None = None,
        retries: int = 3,
        **kwargs: Any,
    ) -> T:
        """Execute a structured request using the new runner contract."""
        prompt_kwargs = dict(kwargs)
        if use_structured_outputs is not None:
            prompt_kwargs["_structured_use_schema"] = use_structured_outputs
        return self.runner.run(
            model=self.llm,
            prompt=StructuredOutputPrompt(system=system, user=user, kwargs=prompt_kwargs),
            response_model=output_model,
            retries=retries,
        )

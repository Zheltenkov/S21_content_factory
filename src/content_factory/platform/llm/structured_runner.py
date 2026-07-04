"""Typed structured-output runner contracts.

The public contract is intentionally small: callers provide an LLM client,
two prompt parts and a Pydantic response model. Provider-specific schema
wiring stays in the LLM gateway or in this compatibility runner.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..exceptions import LLMAPIError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class StructuredOutputPrompt:
    """Prompt payload for one structured-output call."""

    system: str
    user: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class StructuredOutputRunner(Protocol):
    """Provider-neutral contract for Pydantic structured outputs."""

    def run(
        self,
        *,
        model: Any,
        prompt: StructuredOutputPrompt,
        response_model: type[T],
        retries: int = 3,
    ) -> T:
        """Return a validated Pydantic object or raise a typed failure."""


class InstructorStructuredOutputRunner:
    """Structured-output runner backed by Instructor when the client supports it.

    Production clients are expected to be :class:`LLMGateway` or
    :class:`ObservedLLMClient`. They expose ``complete_structured`` and perform
    routing, budget accounting and provider fallback internally. Simple test
    doubles that only expose ``complete`` are handled through the JSON runner below.
    """

    def run(
        self,
        *,
        model: Any,
        prompt: StructuredOutputPrompt,
        response_model: type[T],
        retries: int = 3,
    ) -> T:
        structured_call = getattr(model, "complete_structured", None)
        if callable(structured_call):
            return structured_call(
                output_model=response_model,
                system=prompt.system,
                user=prompt.user,
                retries=retries,
                **dict(prompt.kwargs),
            )
        return CompletionJSONStructuredOutputRunner().run(
            model=model,
            prompt=prompt,
            response_model=response_model,
            retries=retries,
        )


class CompletionJSONStructuredOutputRunner:
    """JSON structured-output path for clients that only expose ``complete``.

    This keeps lightweight unit-test doubles usable while production clients
    perform structured output through ``LLMGateway.complete_structured``.
    """

    def run(
        self,
        *,
        model: Any,
        prompt: StructuredOutputPrompt,
        response_model: type[T],
        retries: int = 3,
    ) -> T:
        kwargs = dict(prompt.kwargs)
        use_structured_outputs = kwargs.pop("_structured_use_schema", None)
        if use_structured_outputs is None:
            use_structured_outputs = supports_structured_outputs(getattr(model, "model", None))

        response_format: str | dict[str, Any]
        if use_structured_outputs:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__.lower().replace("model", ""),
                    "strict": True,
                    "schema": prepare_openai_json_schema(response_model),
                },
            }
            logger.debug(
                "Using JSON schema structured output for model=%s schema=%s",
                getattr(model, "model", None),
                response_model.__name__,
            )
        else:
            response_format = "json_object"
            logger.debug(
                "Using JSON object structured output for model=%s schema=%s",
                getattr(model, "model", None),
                response_model.__name__,
            )

        try:
            response = model.complete(
                system=prompt.system,
                user=prompt.user,
                response_format=response_format,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - provider adapters expose heterogeneous errors
            logger.error("LLM API error during structured output call: %s", exc)
            if isinstance(exc, LLMAPIError):
                raise
            raise LLMAPIError(f"Ошибка при вызове LLM для structured output: {exc}") from exc

        return parse_json_model(response, response_model)


def supports_structured_outputs(model_name: str | None) -> bool:
    """Return whether the model name is known to support JSON schema mode."""

    supported_models = {
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-5.4-mini",
        "o1",
        "o1-mini",
        "o1-preview",
        "o1-mini-preview",
    }
    normalized = str(model_name or "").lower()
    provider_prefixes = ("openrouter/", "openai/")
    stripped = True
    while stripped:
        stripped = False
        for provider_prefix in provider_prefixes:
            if normalized.startswith(provider_prefix):
                normalized = normalized.removeprefix(provider_prefix)
                stripped = True
    return any(normalized.startswith(supported.lower()) for supported in supported_models)


def prepare_openai_json_schema(model_class: type[BaseModel]) -> dict[str, Any]:
    """Prepare a strict OpenAI-compatible JSON schema from a Pydantic model."""

    schema = model_class.model_json_schema()

    def add_additional_properties(obj: Any) -> Any:
        if isinstance(obj, dict):
            result: dict[str, Any] = {}
            for key, value in obj.items():
                if key in ("$defs", "definitions"):
                    if isinstance(value, dict):
                        result[key] = {
                            def_name: add_additional_properties(def_schema)
                            for def_name, def_schema in value.items()
                        }
                    else:
                        result[key] = value
                    continue
                if key == "title":
                    continue
                if key == "$ref":
                    result[key] = value
                    continue
                result[key] = add_additional_properties(value)

            obj_type = obj.get("type")
            has_properties = "properties" in obj
            if (obj_type == "object" or has_properties) and "$defs" not in obj and "definitions" not in obj:
                result["additionalProperties"] = False
                if "type" not in result and has_properties:
                    result["type"] = "object"

            for union_key in ["anyOf", "oneOf", "allOf"]:
                if union_key in result:
                    result[union_key] = [add_additional_properties(item) for item in result[union_key]]
            return result
        if isinstance(obj, list):
            return [add_additional_properties(item) for item in obj]
        return obj

    cleaned_schema = add_additional_properties(schema)
    properties = cleaned_schema.get("properties", {})
    original_required = cleaned_schema.get("required", [])

    if original_required:
        required_fields = [key for key in original_required if key in properties]
        required_fields.extend(key for key in properties.keys() if key not in required_fields)
        extra_in_required = [key for key in original_required if key not in properties]
        if extra_in_required:
            logger.warning(
                "Removed required keys absent from properties for %s: %s",
                model_class.__name__,
                extra_in_required,
            )
    else:
        required_fields = list(properties.keys())

    def fix_required_in_defs(defs_dict: dict[str, Any]) -> dict[str, Any]:
        fixed_defs: dict[str, Any] = {}
        for def_name, def_schema in defs_dict.items():
            if not isinstance(def_schema, dict):
                fixed_defs[def_name] = def_schema
                continue
            fixed_schema = dict(def_schema)
            if "properties" in fixed_schema:
                def_properties = fixed_schema["properties"]
                def_required = fixed_schema.get("required", [])
                filtered_required = [key for key in def_required if key in def_properties]
                filtered_required.extend(key for key in def_properties.keys() if key not in filtered_required)
                fixed_schema["required"] = filtered_required
            if "$defs" in fixed_schema:
                fixed_schema["$defs"] = fix_required_in_defs(fixed_schema["$defs"])
            fixed_defs[def_name] = fixed_schema
        return fixed_defs

    final_schema: dict[str, Any] = {
        "type": cleaned_schema.get("type", "object"),
        "properties": properties,
        "required": required_fields,
        "additionalProperties": False,
    }
    if "$defs" in cleaned_schema:
        final_schema["$defs"] = fix_required_in_defs(cleaned_schema["$defs"])
    elif "$defs" in schema:
        final_schema["$defs"] = fix_required_in_defs(schema["$defs"])
    elif "definitions" in cleaned_schema:
        final_schema["definitions"] = fix_required_in_defs(cleaned_schema["definitions"])
    elif "definitions" in schema:
        final_schema["definitions"] = fix_required_in_defs(schema["definitions"])
    return final_schema


def parse_json_model[T: BaseModel](response: Any, response_model: type[T]) -> T:
    """Parse raw JSON-ish model output and validate it with Pydantic."""

    try:
        data = _extract_json_payload(response)
        result = response_model.model_validate(data)
        logger.debug(
            "Validated structured output for %s: %s...",
            response_model.__name__,
            result.model_dump_json(exclude_none=True)[:200],
        )
        return result
    except json.JSONDecodeError as exc:
        raw_text = str(response)
        logger.error(
            "JSON decode error for %s: %s; raw=%s...",
            response_model.__name__,
            exc,
            raw_text[:500],
        )
        raise ValidationError.from_exception_data(
            response_model.__name__,
            [
                {
                    "type": "value_error",
                    "loc": ("__root__",),
                    "msg": f"Не удалось распарсить JSON ответ от LLM: {exc}",
                    "input": response,
                    "ctx": {"error": f"Не удалось распарсить JSON ответ от LLM: {exc}"},
                }
            ],
        ) from exc
    except ValidationError as exc:
        logger.error(
            "Pydantic validation error for %s: %s; raw=%s...",
            response_model.__name__,
            exc,
            str(response)[:500],
        )
        raise


def _extract_json_payload(response: Any) -> Any:
    if not isinstance(response, str):
        return response
    response_clean = response.strip()
    if response_clean.startswith("```json"):
        response_clean = response_clean[7:]
    if response_clean.startswith("```"):
        response_clean = response_clean[3:]
    if response_clean.endswith("```"):
        response_clean = response_clean[:-3]
    return json.loads(response_clean.strip())

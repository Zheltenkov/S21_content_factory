"""Project-scoped writing preferences; sampling never changes extraction or evaluation."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract


class GenerationSettings(Contract):
    variety: Literal["restrained", "balanced", "expressive"] = "balanced"
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    top_p: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2, allow_inf_nan=False)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2, allow_inf_nan=False)

    @model_validator(mode="after")
    def one_sampler(self) -> "GenerationSettings":
        # Keep an interpretable experiment: change temperature OR nucleus sampling.
        if self.temperature is not None and self.top_p is not None:
            raise ValueError("Выберите температуру или top_p, не оба одновременно")
        return self

    def sampling(self) -> dict[str, float]:
        return {key: value for key, value in self.model_dump(exclude={"variety"}).items() if value is not None}

    def instruction(self) -> str:
        return {
            "restrained": "Подача сдержанная и конкретная: короткие условия, необходимые примеры, без декоративного сюжета.",
            "balanced": "Разнообразь ситуации, примеры и формулировки. Сохраняй единые термины и ясные требования; избегай однообразных вступлений.",
            "expressive": "Пиши живо: конкретные ситуации, разные примеры, естественный язык. Не повторяй один приём во всех заданиях. "
                          "Не усложняй условия ради сюжета и не меняй ОР, часы, сдачу или критерии проверки.",
        }[self.variety]


active: ContextVar[GenerationSettings | None] = ContextVar("project_writing_settings", default=None)


@contextmanager
def writing(settings: GenerationSettings):
    """Async tasks inherit their own context; another project's parameters cannot leak here."""
    token = active.set(settings)
    try:
        yield
    finally:
        active.reset(token)


def record_sampling(model: str, requested: dict[str, float], effective: dict[str, float]) -> None:
    # Parameters are reproducibility metadata, never prompts or provider error bodies.
    if not requested:
        return
    logging.getLogger("project_generator_lab.model_calls").info(
        "model_sampling model=%s requested=%s effective=%s", model, requested, effective)

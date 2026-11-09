"""Jobs for tests/core/test_isolated.py: they must be importable by their full name in a fresh interpreter."""

from project_generator_lab.concept.ingestion import InputError


def hog() -> bytearray:
    return bytearray(1024 * 1024 * 1024)


def spin() -> None:
    while True:
        pass


def refuse() -> None:
    raise InputError("Формат не поддерживается.")


def chatty() -> str:
    print("шум парсера в stdout")
    import os
    return os.environ.get("POLZA_AI_API_KEY", "нет ключа")

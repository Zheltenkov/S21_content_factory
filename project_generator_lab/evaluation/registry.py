"""Versioned source rubrics; policy changes invalidate earlier evaluations."""
import hashlib
from pathlib import Path

from .models import Profile

FILE = Path(__file__).with_name("criteria.json")


def load() -> Profile:
    profile = Profile.model_validate_json(FILE.read_text(encoding="utf-8"))
    if [c.number for c in profile.criteria] != list(range(1, 57)):
        raise ValueError("Реестр должен содержать критерии 1–56 ровно по одному разу")
    return profile


def digest(profile: Profile) -> str:
    return hashlib.sha256(profile.model_dump_json().encode()).hexdigest()

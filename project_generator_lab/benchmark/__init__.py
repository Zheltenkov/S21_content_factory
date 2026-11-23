"""Measuring the generator against projects that exist (РГ 02.10.2026, раздел 7).

A case is a folder: what the methodologist had before the project existed, the project that was made of it, and
what is known of the case. This package reads cases and measures them; it generates nothing and calls no model.

    cases.py       the folder of a case and what is in it
    repository.py  a project as it lies in its repository: README and check-list read into tasks and questions
    code_layer.py  layer 1 — the generator's own checks of the text, applied to a project read from a repository

It imports content/ (the checks and the task they check) and is imported by nothing.
"""

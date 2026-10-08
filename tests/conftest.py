import pytest

from app.config import ServiceConfig
from app.languages.json_lang import JsonPlugin
from app.languages.python_lang import PythonPlugin
from app.languages.registry import LanguageRegistry, default_registry
from app.service import CodeService


@pytest.fixture
def py():
    return PythonPlugin()


@pytest.fixture
def js():
    return JsonPlugin()


@pytest.fixture
def registry():
    return default_registry()


@pytest.fixture
def service(registry):
    return CodeService(ServiceConfig(), registry)


def make_service(**cfg):
    return CodeService(ServiceConfig(**cfg), default_registry())


def slice_range(source: str, rng):
    """Exact source substring covered by a (line, col) half-open range."""
    lines = source.split("\n")
    if rng.start.line == rng.end.line:
        return lines[rng.start.line][rng.start.col : rng.end.col]
    parts = [lines[rng.start.line][rng.start.col :]]
    parts.extend(lines[rng.start.line + 1 : rng.end.line])
    parts.append(lines[rng.end.line][: rng.end.col])
    return "\n".join(parts)


def reconstruct(source: str, highlights) -> str:
    """Concatenate highlighted slices in range order."""
    return "".join(slice_range(source, h.range) for h in highlights)


def assert_partitions(source: str, highlights) -> None:
    """Highlights must be sorted, non-overlapping, gap-free and reconstruct."""
    ordered = sorted(highlights, key=lambda h: (h.range.start, h.range.end))
    prev = None
    for h in ordered:
        assert h.range.start < h.range.end, "empty highlight"
        if prev is not None:
            assert prev.range.end <= h.range.start, "overlapping highlights"
        prev = h
    assert reconstruct(source, ordered) == source, "highlights must cover source exactly"


PY_SAMPLE = '''import os
from typing import List

CONST = 42  # answer


class Animal:
    """Base class.

    Multiline docstring with trailing spaces:   
    """

    legs: int = 4

    def speak(self):
        raise NotImplementedError


class Dog(Animal):
    def speak(self):
        return "woof"


def make_animal(kind):
    factory = lambda: Dog()
    if kind == "dog":
        return factory()
    return Animal()


def dup():
    pass


def dup():  # redefinition on purpose
    pass
'''

JSON_SAMPLE = '{"name": "svc", "replicas": 3, "tags": ["a", "b"], "nested": {"x": 1.5e2, "ok": true}}'

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

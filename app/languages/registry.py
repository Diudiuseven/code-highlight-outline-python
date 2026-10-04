"""Language registry: maps language names to plugins."""
from __future__ import annotations

from app.errors import LanguageNotSupportedError
from app.languages.base import LanguagePlugin


class LanguageRegistry:
    """Registry of language plugins.

    Plugins are isolated: registering or replacing one language never touches
    the entries of other languages, so adding/switching a language cannot
    affect existing results.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, LanguagePlugin] = {}

    def register(self, plugin: LanguagePlugin) -> None:
        self._plugins[plugin.name] = plugin

    def unregister(self, name: str) -> None:
        self._plugins.pop(name, None)

    def get(self, name: str) -> LanguagePlugin:
        try:
            return self._plugins[name]
        except KeyError:
            raise LanguageNotSupportedError(
                f"language {name!r} is not supported; "
                f"available: {sorted(self._plugins)}"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._plugins)


def default_registry() -> LanguageRegistry:
    from app.languages.json_lang import JsonPlugin
    from app.languages.python_lang import PythonPlugin

    registry = LanguageRegistry()
    registry.register(PythonPlugin())
    registry.register(JsonPlugin())
    return registry

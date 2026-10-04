"""Configuration defaults and env overrides."""

from codeview.config import load_settings

def test_defaults():
    s = load_settings()
    assert {"python", "json", "plaintext"} <= set(s.languages)
    assert s.max_file_size > 0
    assert s.cache_capacity > 0
    assert s.timeout_ms > 0


def test_explicit_values():
    s = load_settings(
        languages=("python",),
        max_file_size=10,
        cache_capacity=1,
        session_capacity=2,
        timeout_ms=3,
    )
    assert s.languages == frozenset({"python"})
    assert (s.max_file_size, s.cache_capacity, s.session_capacity, s.timeout_ms) == (10, 1, 2, 3)


def test_env_override(monkeypatch):
    monkeypatch.setenv("CODEVIEW_LANGUAGES", "json, plaintext")
    monkeypatch.setenv("CODEVIEW_MAX_FILE_SIZE", "123")
    monkeypatch.setenv("CODEVIEW_CACHE_CAPACITY", "7")
    monkeypatch.setenv("CODEVIEW_TIMEOUT_MS", "99")
    s = load_settings()
    assert s.languages == frozenset({"json", "plaintext"})
    assert s.max_file_size == 123
    assert s.cache_capacity == 7
    assert s.timeout_ms == 99


def test_bad_env_falls_back(monkeypatch):
    monkeypatch.setenv("CODEVIEW_MAX_FILE_SIZE", "not-a-number")
    s = load_settings()
    assert s.max_file_size >= 0

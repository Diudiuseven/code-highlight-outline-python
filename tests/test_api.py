"""End-to-end HTTP tests via FastAPI TestClient."""

import base64

import pytest
from fastapi.testclient import TestClient

from codeview.app import create_app
from codeview.models import Settings
from codeview.service import CodeViewService


@pytest.fixture
def client():
    settings = Settings(
        languages=frozenset({"python", "json", "plaintext"}),
        max_file_size=5000,
        cache_capacity=16,
        session_capacity=16,
        timeout_ms=2000,
    )
    svc = CodeViewService(settings=settings)
    app = create_app(svc)
    with TestClient(app) as c:
        yield c
    svc.close()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert set(r.json()["enabled_languages"]) == {"python", "json", "plaintext"}


def test_analyze_python(client):
    r = client.post("/analyze", json={"language": "python", "content": "x = 1\n"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["highlights"]
    assert body["formatted"] == "x = 1\n"


def test_analyze_json(client):
    r = client.post("/analyze", json={"language": "json", "content": '{"a":1}'})
    body = r.json()
    assert body["status"] == "ok"
    assert body["outline"][0]["kind"] == "object"


def test_unknown_language_error_not_degraded(client):
    r = client.post("/analyze", json={"language": "ruby", "content": "x"})
    assert r.status_code == 400
    body = r.json()
    assert body["status"] == "error"
    assert body["reason"] == "unknown_language"


def test_illegal_encoding_base64(client):
    bad = base64.b64encode(b"x = \xff\xfe 1\n").decode()
    r = client.post(
        "/analyze", json={"language": "python", "content_base64": bad}
    )
    assert r.status_code == 400
    assert r.json()["reason"] == "illegal_encoding"


def test_too_large(client):
    r = client.post(
        "/analyze", json={"language": "python", "content": "a" * 5001}
    )
    assert r.status_code == 413
    assert r.json()["reason"] == "file_too_large"


def test_syntax_error_degraded_200(client):
    r = client.post(
        "/analyze", json={"language": "python", "content": "def (: \n"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["reasons"] == ["syntax_error"]
    pos = body["diagnostics"][0]["range"]["start_pos"]
    assert pos["line"] >= 1


def test_incremental_edit_endpoint(client):
    old = "a = 1\n\nb = 2\n"
    client.post(
        "/edit",
        json={
            "language": "python",
            "old_content": old,
            "new_content": old,
            "edit_start": 0,
            "edit_end_old": 0,
            "edit_end_new": 0,
            "session_id": "sess",
        },
    )
    new = old.replace("a = 1", "a = 11")
    start = 0
    r = client.post(
        "/edit",
        json={
            "language": "python",
            "session_id": "sess",
            "new_content": new,
            "edit_start": start,
            "edit_end_old": start + 5,
            "edit_end_new": start + 6,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["incremental"] is True
    full = client.post(
        "/analyze", json={"language": "python", "content": new}
    ).json()
    assert body["highlights"] == full["highlights"]
    assert body["outline"] == full["outline"]
    assert body["formatted"] == full["formatted"]


def test_concurrent_identical_requests(client):
    payload = {"language": "python", "content": "def f():\n    return 1\n"}
    responses = []

    def call():
        responses.append(client.post("/analyze", json=payload).json())

    import threading

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r == responses[0] for r in responses)


def test_multilanguage_switch_isolation(client):
    py = client.post(
        "/analyze", json={"language": "python", "content": "x = 1\n"}
    ).json()
    js = client.post(
        "/analyze", json={"language": "json", "content": '{"x":1}'}
    ).json()
    assert py["language"] != js["language"]
    # repeat python: unchanged despite json activity
    py2 = client.post(
        "/analyze", json={"language": "python", "content": "x = 1\n"}
    ).json()
    assert py2 == py

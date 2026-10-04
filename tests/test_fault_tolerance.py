import asyncio

import pytest

from app.errors import LanguageNotSupportedError
from tests.conftest import make_service


def test_syntax_error_degrades_with_position():
    async def run():
        service = make_service()
        result = await service.analyze("python", b"def broken(:\n    pass\n")
        assert result.degraded
        diags = [d for d in result.diagnostics if d.code == "syntax-error"]
        assert diags and diags[0].position is not None
        assert diags[0].position.line >= 0 and diags[0].position.col >= 0
        # request still succeeds: formatted text is returned
        assert result.formatted

    asyncio.run(run())


def test_json_syntax_error_position(js):
    result = js.analyze('{\n  "a": ,\n}')
    assert result.degraded
    err = [d for d in result.diagnostics if d.code == "syntax-error"][0]
    assert err.position is not None


def test_unknown_language_raises():
    async def run():
        service = make_service()
        with pytest.raises(LanguageNotSupportedError) as exc:
            await service.analyze("cobol", b"IDENTIFICATION DIVISION.")
        assert exc.value.code == "language-not-supported"

    asyncio.run(run())


def test_invalid_encoding_degrades():
    async def run():
        service = make_service()
        result = await service.analyze("python", b"x = 1\ny = '\xff'\n")
        assert result.degraded
        diag = [d for d in result.diagnostics if d.code == "invalid-encoding"][0]
        assert diag.position.line == 1
        assert result.formatted  # still returns usable text

    asyncio.run(run())


def test_oversize_file_degrades():
    async def run():
        service = make_service(max_file_size=16)
        result = await service.analyze("python", b"x" * 100)
        assert result.degraded
        assert any(d.code == "file-too-large" for d in result.diagnostics)
        assert result.highlights == [] and result.outline == []
        assert result.formatted  # original text preserved

    asyncio.run(run())


def test_error_result_not_cached_as_success():
    async def run():
        service = make_service()
        bad = b"def broken(:\n"
        r1 = await service.analyze("python", bad)
        assert r1.degraded
        # deterministic: same degraded result on repeat, no exception
        r2 = await service.analyze("python", bad)
        assert [d.code for d in r2.diagnostics] == [d.code for d in r1.diagnostics]

    asyncio.run(run())

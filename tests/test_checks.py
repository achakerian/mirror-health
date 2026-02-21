from pathlib import Path

import httpx
import pytest
import respx

from src.checks.basic import run_basic_check
from src.checks.full import run_full_check

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


# --- Basic Health Check Tests ---


class TestBasicCheckSuccess:
    @respx.mock
    async def test_valid_page_passes(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is True
        assert result.response_ms > 0
        assert result.status_code == 200
        assert result.failure_reason is None

    @respx.mock
    async def test_response_time_captured(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.response_ms >= 0


class TestBasicCheckCloudflare:
    @respx.mock
    async def test_cloudflare_challenge_detected(self):
        cf_html = _read_fixture("cloudflare_challenge.html")
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text=cf_html)
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.cloudflare_detected is True
        assert result.failure_reason == "cloudflare_challenge"


class TestBasicCheckPlaceholder:
    @respx.mock
    async def test_placeholder_page_detected(self):
        placeholder = _read_fixture("1337x_placeholder.html")
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text=placeholder)
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "placeholder_content"


class TestBasicCheckFailures:
    @respx.mock
    async def test_404_fails(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.status_code == 404

    @respx.mock
    async def test_500_fails(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "server_error"

    @respx.mock
    async def test_403_rate_limited(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(403, text="Forbidden")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "rate_limited"

    @respx.mock
    async def test_empty_body_fails(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "empty_response"

    @respx.mock
    async def test_near_empty_body_fails(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "empty_response"

    @respx.mock
    async def test_timeout(self):
        respx.get("https://example.com/").mock(side_effect=httpx.ReadTimeout("timeout"))
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "timeout"

    @respx.mock
    async def test_dns_failure(self):
        respx.get("https://example.com/").mock(
            side_effect=httpx.ConnectError("[Errno -2] Name or service not known")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "dns_failure"

    @respx.mock
    async def test_ssl_error(self):
        respx.get("https://example.com/").mock(
            side_effect=httpx.ConnectError("SSL: CERTIFICATE_VERIFY_FAILED")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "ssl_error"

    @respx.mock
    async def test_connection_refused(self):
        respx.get("https://example.com/").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "connection_refused"

    @respx.mock
    async def test_generic_connect_error(self):
        respx.get("https://example.com/").mock(
            side_effect=httpx.ConnectError("Network unreachable")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "connect_error"

    @respx.mock
    async def test_non_httpx_exception_caught(self):
        respx.get("https://example.com/").mock(
            side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "invalid")
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "unexpected_error"

    @respx.mock
    async def test_oversized_response_rejected(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                200,
                text="x",
                headers={"content-length": "999999999"},
            )
        )
        async with httpx.AsyncClient() as client:
            result = await run_basic_check("https://example.com/", client)
        assert result.passed is False
        assert result.failure_reason == "response_too_large"


# --- Full Health Check Tests ---


class TestFullCheckYTS:
    SCRAPER_CONFIG = {
        "fingerprint_type": "json_api",
        "fingerprint_path": "/api/v2/list_movies.json?limit=1",
        "fingerprint_check": ["data", "movies"],
    }

    @respx.mock
    async def test_valid_yts_response(self):
        yts_json = _read_fixture("yts_valid.json")
        respx.get("https://yts.mx/api/v2/list_movies.json?limit=1").mock(
            return_value=httpx.Response(200, text=yts_json,
                                       headers={"content-type": "application/json"})
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://yts.mx", self.SCRAPER_CONFIG, client)
        assert result.passed is True

    @respx.mock
    async def test_empty_movies_array(self):
        yts_json = _read_fixture("yts_invalid.json")
        respx.get("https://yts.mx/api/v2/list_movies.json?limit=1").mock(
            return_value=httpx.Response(200, text=yts_json,
                                       headers={"content-type": "application/json"})
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://yts.mx", self.SCRAPER_CONFIG, client)
        assert result.passed is False
        assert result.failure_reason == "invalid_response"

    @respx.mock
    async def test_invalid_json(self):
        respx.get("https://yts.mx/api/v2/list_movies.json?limit=1").mock(
            return_value=httpx.Response(200, text="not json at all")
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://yts.mx", self.SCRAPER_CONFIG, client)
        assert result.passed is False
        assert result.failure_reason == "invalid_response"

    @respx.mock
    async def test_missing_nested_key(self):
        respx.get("https://yts.mx/api/v2/list_movies.json?limit=1").mock(
            return_value=httpx.Response(200, text='{"status": "ok"}',
                                       headers={"content-type": "application/json"})
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://yts.mx", self.SCRAPER_CONFIG, client)
        assert result.passed is False


class TestFullCheck1337x:
    SCRAPER_CONFIG = {
        "fingerprint_type": "html_contains",
        "fingerprint_path": "/",
        "fingerprint_check": "table-list",
    }

    @respx.mock
    async def test_valid_1337x(self):
        html = _read_fixture("1337x_valid.html")
        respx.get("https://1337x.to/").mock(
            return_value=httpx.Response(200, text=html)
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://1337x.to", self.SCRAPER_CONFIG, client)
        assert result.passed is True

    @respx.mock
    async def test_placeholder_1337x(self):
        html = _read_fixture("1337x_placeholder.html")
        respx.get("https://1337x.to/").mock(
            return_value=httpx.Response(200, text=html)
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://1337x.to", self.SCRAPER_CONFIG, client)
        assert result.passed is False


class TestFullCheckTPB:
    SCRAPER_CONFIG = {
        "fingerprint_type": "html_contains_any",
        "fingerprint_path": "/",
        "fingerprint_check": ["detLink", "searchResult"],
    }

    @respx.mock
    async def test_valid_tpb(self):
        html = _read_fixture("tpb_valid.html")
        respx.get("https://tpb.example.com/").mock(
            return_value=httpx.Response(200, text=html)
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://tpb.example.com", self.SCRAPER_CONFIG, client)
        assert result.passed is True

    @respx.mock
    async def test_no_markers_fails(self):
        respx.get("https://tpb.example.com/").mock(
            return_value=httpx.Response(200, text="<html><body>No torrent content</body></html>")
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://tpb.example.com", self.SCRAPER_CONFIG, client)
        assert result.passed is False


class TestFullCheckTorrentGalaxy:
    SCRAPER_CONFIG = {
        "fingerprint_type": "html_contains",
        "fingerprint_path": "/",
        "fingerprint_check": "tgxtablerow",
    }

    @respx.mock
    async def test_valid(self):
        html = _read_fixture("torrentgalaxy_valid.html")
        respx.get("https://tg.example.com/").mock(
            return_value=httpx.Response(200, text=html)
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://tg.example.com", self.SCRAPER_CONFIG, client)
        assert result.passed is True


class TestFullCheckEZTV:
    SCRAPER_CONFIG = {
        "fingerprint_type": "html_contains",
        "fingerprint_path": "/",
        "fingerprint_check": "forum_header_border",
    }

    @respx.mock
    async def test_valid(self):
        html = _read_fixture("eztv_valid.html")
        respx.get("https://eztv.example.com/").mock(
            return_value=httpx.Response(200, text=html)
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://eztv.example.com", self.SCRAPER_CONFIG, client)
        assert result.passed is True


class TestFullCheckAnnasArchive:
    SCRAPER_CONFIG = {
        "fingerprint_type": "html_contains",
        "fingerprint_path": "/search?q=test",
        "fingerprint_check": "js-vim-focus",
    }

    @respx.mock
    async def test_valid(self):
        html = _read_fixture("annas_archive_valid.html")
        respx.get("https://annas.example.com/search?q=test").mock(
            return_value=httpx.Response(200, text=html)
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://annas.example.com", self.SCRAPER_CONFIG, client)
        assert result.passed is True


class TestFullCheckRuTracker:
    SCRAPER_CONFIG = {
        "fingerprint_type": "html_contains",
        "fingerprint_path": "/",
        "fingerprint_check": "f-name",
    }

    @respx.mock
    async def test_valid(self):
        html = _read_fixture("rutracker_valid.html")
        respx.get("https://rutracker.example.com/").mock(
            return_value=httpx.Response(200, text=html)
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://rutracker.example.com", self.SCRAPER_CONFIG, client)
        assert result.passed is True


class TestFullCheckErrors:
    SCRAPER_CONFIG = {
        "fingerprint_type": "html_contains",
        "fingerprint_path": "/",
        "fingerprint_check": "table-list",
    }

    @respx.mock
    async def test_timeout(self):
        respx.get("https://example.com/").mock(side_effect=httpx.ReadTimeout("timeout"))
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://example.com", self.SCRAPER_CONFIG, client)
        assert result.passed is False
        assert result.failure_reason == "timeout"

    @respx.mock
    async def test_non_200_status(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )
        async with httpx.AsyncClient() as client:
            result = await run_full_check("https://example.com", self.SCRAPER_CONFIG, client)
        assert result.passed is False
        assert result.failure_reason == "http_503"

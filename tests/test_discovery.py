import httpx
import respx

from src.discovery import discover_mirrors, resolve_doh, resolve_with_fallback
from src.models import Mirror, MirrorState

DOH_CLOUDFLARE = "https://cloudflare-dns.com/dns-query"
DOH_GOOGLE = "https://dns.google/resolve"

# Sample DoH JSON responses
DOH_SUCCESS = {
    "Status": 0,
    "Answer": [{"name": "example.to", "type": 1, "TTL": 300, "data": "1.2.3.4"}],
}

DOH_NXDOMAIN = {
    "Status": 3,
    "Answer": [],
}

DOH_NO_ANSWER = {
    "Status": 0,
    "Answer": [],
}


class TestResolveDoh:
    @respx.mock
    async def test_success(self):
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(200, json=DOH_SUCCESS)
        )
        async with httpx.AsyncClient() as client:
            result = await resolve_doh("example.to", client, DOH_CLOUDFLARE)
        assert result is True

    @respx.mock
    async def test_nxdomain(self):
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(200, json=DOH_NXDOMAIN)
        )
        async with httpx.AsyncClient() as client:
            result = await resolve_doh("nonexistent.xyz", client, DOH_CLOUDFLARE)
        assert result is False

    @respx.mock
    async def test_no_answer_records(self):
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(200, json=DOH_NO_ANSWER)
        )
        async with httpx.AsyncClient() as client:
            result = await resolve_doh("norecords.xyz", client, DOH_CLOUDFLARE)
        assert result is False

    @respx.mock
    async def test_doh_server_error(self):
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        async with httpx.AsyncClient() as client:
            result = await resolve_doh("example.to", client, DOH_CLOUDFLARE)
        assert result is False

    @respx.mock
    async def test_network_error(self):
        respx.get(DOH_CLOUDFLARE).mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        async with httpx.AsyncClient() as client:
            result = await resolve_doh("example.to", client, DOH_CLOUDFLARE)
        assert result is False


class TestResolveWithFallback:
    @respx.mock
    async def test_cloudflare_success_no_fallback(self):
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(200, json=DOH_SUCCESS)
        )
        # Google should NOT be called
        async with httpx.AsyncClient() as client:
            result = await resolve_with_fallback("example.to", client)
        assert result is True

    @respx.mock
    async def test_cloudflare_fails_google_succeeds(self):
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(500, text="error")
        )
        respx.get(DOH_GOOGLE).mock(
            return_value=httpx.Response(200, json=DOH_SUCCESS)
        )
        async with httpx.AsyncClient() as client:
            result = await resolve_with_fallback("example.to", client)
        assert result is True

    @respx.mock
    async def test_both_fail(self):
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(500, text="error")
        )
        respx.get(DOH_GOOGLE).mock(
            return_value=httpx.Response(500, text="error")
        )
        async with httpx.AsyncClient() as client:
            result = await resolve_with_fallback("nonexistent.xyz", client)
        assert result is False


class TestDiscoverMirrors:
    @respx.mock
    async def test_discovers_new_mirror(self):
        """Domain resolves and passes basic check -> new mirror returned."""
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(200, json=DOH_SUCCESS)
        )
        respx.get("https://test.to/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )
        state = MirrorState()
        async with httpx.AsyncClient() as doh_client, httpx.AsyncClient() as probe_client:
            mirrors = await discover_mirrors(
                "testscraper", ["test"], ["to"], state, doh_client, probe_client
            )
        assert len(mirrors) == 1
        assert mirrors[0].url == "https://test.to"
        assert mirrors[0].scraper == "testscraper"

    @respx.mock
    async def test_skips_existing_mirror(self):
        """Domain already in state -> skipped."""
        existing = Mirror(url="https://test.to", scraper="testscraper")
        state = MirrorState(mirrors=[existing])
        # No HTTP mocks needed — should be skipped before any requests
        async with httpx.AsyncClient() as doh_client, httpx.AsyncClient() as probe_client:
            mirrors = await discover_mirrors(
                "testscraper", ["test"], ["to"], state, doh_client, probe_client
            )
        assert len(mirrors) == 0

    @respx.mock
    async def test_skips_unresolvable_domain(self):
        """Domain doesn't resolve -> no basic check, no mirror."""
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(200, json=DOH_NXDOMAIN)
        )
        respx.get(DOH_GOOGLE).mock(
            return_value=httpx.Response(200, json=DOH_NXDOMAIN)
        )
        state = MirrorState()
        async with httpx.AsyncClient() as doh_client, httpx.AsyncClient() as probe_client:
            mirrors = await discover_mirrors(
                "testscraper", ["test"], ["xyz"], state, doh_client, probe_client
            )
        assert len(mirrors) == 0

    @respx.mock
    async def test_resolves_but_fails_basic_check(self):
        """Domain resolves but basic check fails -> no mirror."""
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(200, json=DOH_SUCCESS)
        )
        respx.get("https://test.to/").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        state = MirrorState()
        async with httpx.AsyncClient() as doh_client, httpx.AsyncClient() as probe_client:
            mirrors = await discover_mirrors(
                "testscraper", ["test"], ["to"], state, doh_client, probe_client
            )
        assert len(mirrors) == 0

    @respx.mock
    async def test_multiple_base_names_and_tlds(self):
        """Multiple combinations are probed."""
        # Both resolve
        respx.get(DOH_CLOUDFLARE).mock(
            return_value=httpx.Response(200, json=DOH_SUCCESS)
        )
        # Only a.to and b.to pass basic check
        respx.get("https://a.to/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )
        respx.get("https://a.st/").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        respx.get("https://b.to/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )
        respx.get("https://b.st/").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        state = MirrorState()
        async with httpx.AsyncClient() as doh_client, httpx.AsyncClient() as probe_client:
            mirrors = await discover_mirrors(
                "test", ["a", "b"], ["to", "st"], state, doh_client, probe_client
            )
        urls = {m.url for m in mirrors}
        assert "https://a.to" in urls
        assert "https://b.to" in urls
        assert "https://a.st" not in urls
        assert "https://b.st" not in urls

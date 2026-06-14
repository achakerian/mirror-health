from datetime import datetime, timedelta, timezone

import httpx
import respx

from src.geo import (
    GeoBudget,
    _country_to_region,
    _geo_cooldown_elapsed,
    _node_country,
    _node_ok,
    check_geo_reachability,
    should_geo_recheck,
)
from src.models import Mirror, Tier

NOW = datetime(2026, 6, 14, tzinfo=timezone.utc)

GEO_CONFIG = {
    "enabled": True,
    "max_nodes": 10,
    "poll_attempts": 3,
    "poll_interval_seconds": 0,  # no real sleep in tests
    "per_run_recheck_cap": 100,
    "recheck_cooldown_hours": 24,
    "min_nodes_per_region": 1,
    "recheck_failure_reasons": ["timeout", "connect_error", "rate_limited", "server_error"],
    "regions": {"US": ["us"], "EU": ["de", "nl", "fr", "gb", "uk"], "ASIA": ["jp", "sg", "hk"]},
}

SUBMIT = {
    "ok": 1,
    "request_id": "abc123",
    "permanent_link": "https://check-host.net/check-report/abc123",
    "nodes": {
        "us1.node.check-host.net": ["North America", "us", "Los Angeles", "ASxx", "1.1.1.1"],
        "de1.node.check-host.net": ["Europe", "de", "Falkenstein", "ASyy", "2.2.2.2"],
        "jp1.node.check-host.net": ["Asia", "jp", "Tokyo", "ASzz", "3.3.3.3"],
    },
}


def _result(us, de, jp):
    return {
        "us1.node.check-host.net": us,
        "de1.node.check-host.net": de,
        "jp1.node.check-host.net": jp,
    }


OK = [[1, 0.3, "OK", "200", "2.2.2.2"]]
FAIL = [[0, 5.0, "Timed out"]]


# --- Pure helpers ---

class TestNodeOk:
    def test_success(self):
        assert _node_ok([[1, 0.3, "OK", "200", "1.2.3.4"]]) is True

    def test_failure(self):
        assert _node_ok([[0, 5.0, "Timed out"]]) is False

    def test_pending_none(self):
        assert _node_ok(None) is False

    def test_malformed(self):
        assert _node_ok([]) is False
        assert _node_ok("nope") is False


class TestNodeCountry:
    def test_from_metadata(self):
        assert _node_country("de1.node.check-host.net", ["Europe", "de", "Falkenstein"]) == "de"

    def test_hostname_fallback(self):
        assert _node_country("jp1.node.check-host.net", None) == "jp"

    def test_uk_prefix(self):
        assert _node_country("uk1.node.check-host.net", None) == "uk"


class TestCountryToRegion:
    def test_maps(self):
        regions = GEO_CONFIG["regions"]
        assert _country_to_region("de", regions) == "EU"
        assert _country_to_region("jp", regions) == "ASIA"
        assert _country_to_region("us", regions) == "US"

    def test_unknown(self):
        assert _country_to_region("zz", GEO_CONFIG["regions"]) is None
        assert _country_to_region(None, GEO_CONFIG["regions"]) is None


class TestGeoBudget:
    def test_take_decrements(self):
        b = GeoBudget(remaining=2)
        assert b.take() is True
        assert b.take() is True
        assert b.take() is False
        assert b.remaining == 0


class TestCooldown:
    def test_never_checked_is_elapsed(self):
        m = Mirror(url="https://t.com", scraper="s")
        assert _geo_cooldown_elapsed(m, NOW, 24) is True

    def test_recent_not_elapsed(self):
        m = Mirror(url="https://t.com", scraper="s", geo_checked_at=NOW - timedelta(hours=1))
        assert _geo_cooldown_elapsed(m, NOW, 24) is False

    def test_old_elapsed(self):
        m = Mirror(url="https://t.com", scraper="s", geo_checked_at=NOW - timedelta(hours=25))
        assert _geo_cooldown_elapsed(m, NOW, 24) is True


# --- should_geo_recheck gating ---

class TestShouldGeoRecheck:
    def test_disabled_config(self):
        m = Mirror(url="https://t.com", scraper="s", consecutive_fails=5)
        assert should_geo_recheck(m, Tier.DEAD, "timeout", NOW, None, None) is False
        assert should_geo_recheck(m, Tier.DEAD, "timeout", NOW, {"enabled": False}, None) is False

    def test_non_geo_reason(self):
        m = Mirror(url="https://t.com", scraper="s", consecutive_fails=5)
        assert should_geo_recheck(m, Tier.DEAD, "dns_failure", NOW, GEO_CONFIG, None) is False

    def test_not_at_brink(self):
        m = Mirror(url="https://t.com", scraper="s", tier=Tier.ALIVE, consecutive_fails=2)
        assert should_geo_recheck(m, Tier.ALIVE, "timeout", NOW, GEO_CONFIG, None) is False

    def test_at_brink_passes(self):
        m = Mirror(url="https://t.com", scraper="s", tier=Tier.ALIVE, consecutive_fails=5)
        assert should_geo_recheck(m, Tier.DEAD, "timeout", NOW, GEO_CONFIG, None) is True

    def test_already_geo_restricted_passes(self):
        m = Mirror(url="https://t.com", scraper="s", tier=Tier.GEO_RESTRICTED, consecutive_fails=9)
        assert should_geo_recheck(m, Tier.GEO_RESTRICTED, "timeout", NOW, GEO_CONFIG, None) is True

    def test_cooldown_blocks(self):
        m = Mirror(url="https://t.com", scraper="s", tier=Tier.ALIVE, consecutive_fails=5,
                   geo_checked_at=NOW - timedelta(hours=1))
        assert should_geo_recheck(m, Tier.DEAD, "timeout", NOW, GEO_CONFIG, None) is False

    def test_budget_exhausted_blocks(self):
        m = Mirror(url="https://t.com", scraper="s", tier=Tier.ALIVE, consecutive_fails=5)
        assert should_geo_recheck(m, Tier.DEAD, "timeout", NOW, GEO_CONFIG, GeoBudget(0)) is False

    def test_budget_consumed_on_true(self):
        m = Mirror(url="https://t.com", scraper="s", tier=Tier.ALIVE, consecutive_fails=5)
        b = GeoBudget(1)
        assert should_geo_recheck(m, Tier.DEAD, "timeout", NOW, GEO_CONFIG, b) is True
        assert b.remaining == 0


# --- check_geo_reachability (mocked check-host) ---

class TestCheckGeoReachability:
    @respx.mock
    async def test_reachable_non_us_regions(self):
        respx.get(url__regex=r"https://check-host\.net/check-http").mock(
            return_value=httpx.Response(200, json=SUBMIT)
        )
        respx.get(url__regex=r"https://check-host\.net/check-result/.+").mock(
            return_value=httpx.Response(200, json=_result(FAIL, OK, OK))
        )
        async with httpx.AsyncClient() as client:
            result = await check_geo_reachability("https://blocked.example", client, GEO_CONFIG)
        assert result.ok is True
        assert result.reachable_regions == ["ASIA", "EU"]  # sorted, US filtered out
        assert result.checked_nodes == 3
        assert result.reachable_nodes == 2

    @respx.mock
    async def test_only_us_reachable_yields_no_regions(self):
        respx.get(url__regex=r"https://check-host\.net/check-http").mock(
            return_value=httpx.Response(200, json=SUBMIT)
        )
        respx.get(url__regex=r"https://check-host\.net/check-result/.+").mock(
            return_value=httpx.Response(200, json=_result(OK, FAIL, FAIL))
        )
        async with httpx.AsyncClient() as client:
            result = await check_geo_reachability("https://x.example", client, GEO_CONFIG)
        assert result.ok is True  # we DID get a verdict
        assert result.reachable_regions == []  # US filtered; nothing else reachable

    @respx.mock
    async def test_submit_declined(self):
        respx.get(url__regex=r"https://check-host\.net/check-http").mock(
            return_value=httpx.Response(200, json={"ok": 0, "error": "too many requests"})
        )
        async with httpx.AsyncClient() as client:
            result = await check_geo_reachability("https://x.example", client, GEO_CONFIG)
        assert result.ok is False

    @respx.mock
    async def test_submit_http_error(self):
        respx.get(url__regex=r"https://check-host\.net/check-http").mock(
            return_value=httpx.Response(503, text="unavailable")
        )
        async with httpx.AsyncClient() as client:
            result = await check_geo_reachability("https://x.example", client, GEO_CONFIG)
        assert result.ok is False

    @respx.mock
    async def test_all_pending_is_undetermined(self):
        respx.get(url__regex=r"https://check-host\.net/check-http").mock(
            return_value=httpx.Response(200, json=SUBMIT)
        )
        respx.get(url__regex=r"https://check-host\.net/check-result/.+").mock(
            return_value=httpx.Response(200, json=_result(None, None, None))
        )
        async with httpx.AsyncClient() as client:
            result = await check_geo_reachability("https://x.example", client, GEO_CONFIG)
        assert result.ok is False  # nothing finished

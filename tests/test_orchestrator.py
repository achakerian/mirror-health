from datetime import datetime, timedelta, timezone

import httpx
import respx

from src.main import (
    MAX_RESPONSE_TIMES,
    _maybe_reset_7d_history,
    _update_response_times,
    check_mirror,
    run_active_check,
    run_inactive_check,
)
from src.models import CheckHistory7d, Mirror, MirrorState, Tier

from .conftest import SCORING_CONFIG

SCRAPER_CONFIG = {
    "fingerprint_type": "html_contains",
    "fingerprint_path": "/",
    "fingerprint_check": "table-list",
}


class TestMaybeReset7dHistory:
    def test_resets_when_window_expired(self):
        now = datetime.now(timezone.utc)
        old_start = now - timedelta(days=8)
        m = Mirror(
            url="https://test.com",
            scraper="test",
            check_history_7d=CheckHistory7d(
                basic_total=100, basic_passed=90, window_start=old_start
            ),
        )
        _maybe_reset_7d_history(m, now)
        assert m.check_history_7d.basic_total == 0
        assert m.check_history_7d.basic_passed == 0
        assert m.check_history_7d.window_start == now

    def test_no_reset_when_within_window(self):
        now = datetime.now(timezone.utc)
        recent_start = now - timedelta(days=3)
        m = Mirror(
            url="https://test.com",
            scraper="test",
            check_history_7d=CheckHistory7d(
                basic_total=50, basic_passed=45, window_start=recent_start
            ),
        )
        _maybe_reset_7d_history(m, now)
        assert m.check_history_7d.basic_total == 50
        assert m.check_history_7d.basic_passed == 45

    def test_resets_when_window_start_is_none(self):
        now = datetime.now(timezone.utc)
        m = Mirror(
            url="https://test.com",
            scraper="test",
            check_history_7d=CheckHistory7d(basic_total=10, basic_passed=8),
        )
        _maybe_reset_7d_history(m, now)
        assert m.check_history_7d.basic_total == 0
        assert m.check_history_7d.window_start == now


class TestUpdateResponseTimes:
    def test_first_response(self):
        m = Mirror(url="https://test.com", scraper="test")
        _update_response_times(m, 500.0)
        assert m.avg_response_ms == 500.0
        assert len(m.response_times) == 1

    def test_rolling_average(self):
        m = Mirror(url="https://test.com", scraper="test")
        for i in range(5):
            _update_response_times(m, 100.0)
        assert m.avg_response_ms == 100.0
        assert len(m.response_times) == 5

    def test_window_size_capped(self):
        m = Mirror(url="https://test.com", scraper="test")
        for i in range(15):
            _update_response_times(m, float(i * 100))
        assert len(m.response_times) == MAX_RESPONSE_TIMES
        # Should only have the last 10 values (500-1400)
        assert m.response_times[0] == 500.0


class TestCheckMirror:
    @respx.mock
    async def test_basic_pass_no_full(self):
        """Basic passes, no full check requested -> mirror updated correctly."""
        respx.get("https://test.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )
        m = Mirror(url="https://test.com", scraper="test", elo=1000)

        async with httpx.AsyncClient() as client:
            await check_mirror(m, None, client, SCORING_CONFIG, run_full=False)

        assert m.consecutive_passes == 1
        assert m.consecutive_fails == 0
        assert m.total_checks == 1
        assert m.total_passes == 1
        assert m.elo > 1000  # Elo increased
        assert m.last_checked is not None
        assert m.last_passed is not None

    @respx.mock
    async def test_basic_fail(self):
        """Basic fails -> mirror marked as failed."""
        respx.get("https://test.com/").mock(
            return_value=httpx.Response(500, text="Server Error")
        )
        m = Mirror(url="https://test.com", scraper="test", elo=1000)

        async with httpx.AsyncClient() as client:
            await check_mirror(m, None, client, SCORING_CONFIG, run_full=False)

        assert m.consecutive_fails == 1
        assert m.consecutive_passes == 0
        assert m.total_checks == 1
        assert m.total_passes == 0
        assert m.elo < 1000  # Elo decreased
        assert m.last_failure_reason == "server_error"

    @respx.mock
    async def test_basic_pass_full_pass(self):
        """Both basic and full pass -> full success."""
        respx.get("https://test.com/").mock(
            return_value=httpx.Response(200, text='<html><div class="table-list">content</div></html>' + "x" * 200)
        )
        m = Mirror(url="https://test.com", scraper="1337x", elo=1000)

        async with httpx.AsyncClient() as client:
            await check_mirror(m, SCRAPER_CONFIG, client, SCORING_CONFIG, run_full=True)

        assert m.consecutive_passes == 1
        assert m.consecutive_fails == 0
        assert m.total_passes == 1
        assert m.check_history_7d.full_total == 1
        assert m.check_history_7d.full_passed == 1

    @respx.mock
    async def test_basic_pass_full_fail(self):
        """Basic passes but full fails -> counted as failure."""
        # Basic check passes (200, non-empty, no CF/placeholder)
        respx.get("https://test.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )
        m = Mirror(url="https://test.com", scraper="1337x", elo=1000)

        async with httpx.AsyncClient() as client:
            await check_mirror(m, SCRAPER_CONFIG, client, SCORING_CONFIG, run_full=True)

        # Full check fails because "table-list" not in basic response body
        assert m.consecutive_fails == 1
        assert m.consecutive_passes == 0
        assert m.check_history_7d.full_total == 1
        assert m.check_history_7d.full_passed == 0

    @respx.mock
    async def test_cloudflare_detection_persists(self):
        """Cloudflare detection flag is set on mirror."""
        cf_html = (
            '<html><head><title>Just a moment...</title></head>'
            '<body><div id="cf-browser-verification">challenge</div></body></html>'
        )
        respx.get("https://test.com/").mock(
            return_value=httpx.Response(200, text=cf_html)
        )
        m = Mirror(url="https://test.com", scraper="test")

        async with httpx.AsyncClient() as client:
            await check_mirror(m, None, client, SCORING_CONFIG, run_full=False)

        assert m.cloudflare_detected is True
        assert m.last_failure_reason == "cloudflare_challenge"

    @respx.mock
    async def test_tier_transition_on_consecutive_fails(self):
        """5 consecutive fails -> tier demotion."""
        respx.get("https://test.com/").mock(
            return_value=httpx.Response(500, text="Error")
        )
        m = Mirror(
            url="https://test.com",
            scraper="test",
            tier=Tier.ALIVE,
            consecutive_fails=4,  # One more fail will trigger
        )

        async with httpx.AsyncClient() as client:
            await check_mirror(m, None, client, SCORING_CONFIG, run_full=False)

        assert m.consecutive_fails == 5
        assert m.tier == Tier.DEAD.value


class TestErrorIsolation:
    @respx.mock
    async def test_bad_mirror_doesnt_crash_scraper_group(self):
        """One mirror raising an exception should not prevent others from being checked."""
        # First mirror causes an exception (e.g. non-httpx error)
        respx.get("https://bad.com/").mock(
            side_effect=RuntimeError("something completely unexpected")
        )
        # Second mirror works fine
        respx.get("https://good.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )

        mirrors = [
            Mirror(url="https://bad.com", scraper="s1", tier=Tier.ALIVE),
            Mirror(url="https://good.com", scraper="s1", tier=Tier.ALIVE),
        ]
        state = MirrorState(mirrors=mirrors)

        await run_active_check(state, {"s1": SCRAPER_CONFIG}, SCORING_CONFIG)

        good = next(m for m in state.mirrors if m.url == "https://good.com")
        assert good.total_checks > 0, "Good mirror should still have been checked"


class TestRunActiveCheck:
    @respx.mock
    async def test_only_active_tiers_checked(self):
        """Only Candidate, Alive, and GOAT mirrors are checked."""
        respx.get("https://alive.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )
        respx.get("https://goat.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )
        respx.get("https://candidate.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )

        mirrors = [
            Mirror(url="https://alive.com", scraper="s1", tier=Tier.ALIVE),
            Mirror(url="https://goat.com", scraper="s1", tier=Tier.GOAT),
            Mirror(url="https://candidate.com", scraper="s1", tier=Tier.CANDIDATE),
            Mirror(url="https://dead.com", scraper="s1", tier=Tier.DEAD),
            Mirror(url="https://fc.com", scraper="s1", tier=Tier.FALLEN_COMRADE),
        ]
        state = MirrorState(mirrors=mirrors)

        scrapers = {"s1": SCRAPER_CONFIG}
        await run_active_check(state, scrapers, SCORING_CONFIG)

        # Active mirrors should have been checked (total_checks > 0)
        for m in state.mirrors:
            if m.tier in ("Alive", "GOAT", "Candidate"):
                assert m.total_checks > 0, f"{m.url} should have been checked"
            else:
                assert m.total_checks == 0, f"{m.url} should NOT have been checked"


class TestRunInactiveCheck:
    @respx.mock
    async def test_only_inactive_tiers_checked(self):
        """Only Dead and Fallen Comrade mirrors are checked."""
        respx.get("https://dead.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )
        respx.get("https://fc.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )

        mirrors = [
            Mirror(url="https://alive.com", scraper="s1", tier=Tier.ALIVE),
            Mirror(url="https://dead.com", scraper="s1", tier=Tier.DEAD),
            Mirror(url="https://fc.com", scraper="s1", tier=Tier.FALLEN_COMRADE, fallen_comrade=True),
        ]
        state = MirrorState(mirrors=mirrors)

        await run_inactive_check(state, SCORING_CONFIG)

        alive = next(m for m in state.mirrors if m.url == "https://alive.com")
        dead = next(m for m in state.mirrors if m.url == "https://dead.com")
        fc = next(m for m in state.mirrors if m.url == "https://fc.com")

        assert alive.total_checks == 0
        assert dead.total_checks > 0
        assert fc.total_checks > 0

    @respx.mock
    async def test_dead_resurrection(self):
        """Dead mirror that passes basic check -> promoted to Candidate."""
        respx.get("https://dead.com/").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 200 + "</html>")
        )

        m = Mirror(url="https://dead.com", scraper="s1", tier=Tier.DEAD, consecutive_fails=10)
        state = MirrorState(mirrors=[m])

        await run_inactive_check(state, SCORING_CONFIG)

        assert m.tier == Tier.CANDIDATE.value
        assert m.consecutive_passes == 1

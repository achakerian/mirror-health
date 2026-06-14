"""Mirror Health orchestrator — entry point for all workflow modes."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .checks.basic import run_basic_check
from .checks.full import run_full_check
from .discovery import discover_mirrors
from .models import CheckHistory7d, Mirror, MirrorState, Tier
from .geo import GeoBudget, check_geo_reachability, should_geo_recheck
from .scoring import load_scoring_config, record_outcome
from .state import (
    bootstrap_state,
    load_known_domains,
    load_state,
    save_region_scores,
    save_scores,
    save_state,
)
from .tiers import evaluate_tier_transition
from .utils import fetch_runner_geo, logger, random_delay, setup_logging

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

MAX_RESPONSE_TIMES = 10  # Rolling window for avg_response_ms
HISTORY_WINDOW = timedelta(days=7)

# Module-level ref for signal handler to save partial work
_current_state: MirrorState | None = None
_current_scoring_config: dict[str, Any] | None = None
_current_geo_config: dict[str, Any] | None = None


def _maybe_reset_7d_history(mirror: Mirror, now: datetime) -> None:
    """Reset the 7-day check history if the window has expired."""
    window_start = mirror.check_history_7d.window_start
    if window_start is None or (now - window_start) >= HISTORY_WINDOW:
        mirror.check_history_7d = CheckHistory7d(window_start=now)


def _update_response_times(mirror: Mirror, response_ms: float) -> None:
    """Update the rolling response time average (last 10 checks)."""
    mirror.response_times.append(response_ms)
    if len(mirror.response_times) > MAX_RESPONSE_TIMES:
        mirror.response_times = mirror.response_times[-MAX_RESPONSE_TIMES:]
    mirror.avg_response_ms = sum(mirror.response_times) / len(mirror.response_times)


async def check_mirror(
    mirror: Mirror,
    scraper_config: dict | None,
    client: httpx.AsyncClient,
    scoring_config: dict[str, Any],
    run_full: bool = False,
    geo_config: dict[str, Any] | None = None,
    geo_budget: GeoBudget | None = None,
) -> None:
    """Run health checks on a single mirror and update its state in-place.

    Args:
        mirror: The mirror to check (mutated in-place)
        scraper_config: Fingerprint config for full checks (None to skip full)
        client: Shared httpx async client
        scoring_config: Reliability scoring parameters
        run_full: Whether to attempt a full check if basic passes
        geo_config: check-host geo config (None disables geo-restriction detection)
        geo_budget: Shared per-run cap on geo rechecks
    """
    now = datetime.now(timezone.utc)
    mirror.last_checked = now
    mirror.total_checks += 1
    _maybe_reset_7d_history(mirror, now)
    mirror.check_history_7d.basic_total += 1

    # --- Basic check ---
    basic_result = await run_basic_check(mirror.url, client)
    mirror.cloudflare_detected = basic_result.cloudflare_detected

    if not basic_result.passed:
        mirror.consecutive_fails += 1
        mirror.consecutive_passes = 0
        mirror.last_failed = now
        mirror.last_failure_reason = basic_result.failure_reason
        record_outcome(mirror, passed=False, now=now, config=scoring_config)
        old_tier = mirror.tier
        new_tier, new_fallen = await _resolve_failed_tier(
            mirror, basic_result.failure_reason, now, client, geo_config, geo_budget
        )
        mirror.tier = new_tier
        mirror.fallen_comrade = new_fallen
        if old_tier != new_tier:
            logger.info(
                "Tier transition: %s %s -> %s (reason: %s)",
                mirror.url, old_tier, new_tier, basic_result.failure_reason,
            )
        return

    mirror.check_history_7d.basic_passed += 1
    _update_response_times(mirror, basic_result.response_ms)
    # Reachable from the US runner again -> not geo-restricted.
    mirror.geo_reachable_regions = []

    # --- Full check (only if basic passed and requested) ---
    full_passed = True
    if run_full and scraper_config:
        # Reuse basic response body when fingerprint path is root
        basic_body = basic_result.body if scraper_config.get("fingerprint_path") == "/" else None
        full_result = await run_full_check(
            mirror.url, scraper_config, client, basic_body=basic_body
        )
        mirror.check_history_7d.full_total += 1

        if full_result.passed:
            mirror.check_history_7d.full_passed += 1
            if full_result.response_ms > 0:
                _update_response_times(mirror, full_result.response_ms)
        else:
            full_passed = False
            mirror.last_failure_reason = full_result.failure_reason

    if full_passed:
        mirror.consecutive_passes += 1
        mirror.consecutive_fails = 0
        mirror.total_passes += 1
        mirror.last_passed = now
        mirror.last_failure_reason = None
    else:
        mirror.consecutive_fails += 1
        mirror.consecutive_passes = 0
        mirror.last_failed = now

    # Update reliability score based on overall result
    record_outcome(mirror, passed=full_passed, now=now, config=scoring_config)

    # Evaluate tier transition
    new_tier, new_fallen = evaluate_tier_transition(mirror)
    old_tier = mirror.tier
    mirror.tier = new_tier
    mirror.fallen_comrade = new_fallen
    if old_tier != new_tier:
        logger.info("Tier transition: %s %s -> %s", mirror.url, old_tier, new_tier)


async def _resolve_failed_tier(
    mirror: Mirror,
    failure_reason: str | None,
    now: datetime,
    client: httpx.AsyncClient,
    geo_config: dict[str, Any] | None,
    geo_budget: GeoBudget | None,
) -> tuple[Tier, bool]:
    """Decide the post-failure tier, consulting check-host.net for geo-restriction.

    Returns (tier, fallen_comrade). Falls back to the standard ladder whenever
    geo info is unavailable or inconclusive.
    """
    standard_tier, standard_fallen = evaluate_tier_transition(mirror)

    if not should_geo_recheck(
        mirror, standard_tier, failure_reason, now, geo_config, geo_budget
    ):
        return standard_tier, standard_fallen

    result = await check_geo_reachability(mirror.url, client, geo_config)
    mirror.geo_checked_at = now
    if not result.ok:
        return standard_tier, standard_fallen  # undetermined

    mirror.geo_reachable_regions = result.reachable_regions
    if result.reachable_regions:
        # Alive elsewhere -> geo-blocked, not dead. Don't newly set the FC badge.
        return Tier.GEO_RESTRICTED, mirror.fallen_comrade

    # Confirmed unreachable from every region we probed.
    if Tier(mirror.tier) == Tier.GEO_RESTRICTED:
        # Was geo-restricted, now gone everywhere -> it really died.
        return (
            Tier.FALLEN_COMRADE if mirror.fallen_comrade else Tier.DEAD,
            mirror.fallen_comrade,
        )
    return standard_tier, standard_fallen


async def run_active_check(
    state: MirrorState,
    scrapers: dict,
    scoring_config: dict[str, Any],
    geo_config: dict[str, Any] | None = None,
    geo_budget: GeoBudget | None = None,
) -> None:
    """Active check: probe all Candidate, Alive, and GOAT mirrors."""
    active_tiers = {Tier.CANDIDATE.value, Tier.ALIVE.value, Tier.GOAT.value}

    async with httpx.AsyncClient(http2=True) as client:
        # Group mirrors by scraper for parallel execution across scrapers
        by_scraper: dict[str, list[Mirror]] = {}
        for mirror in state.mirrors:
            if mirror.tier in active_tiers:
                by_scraper.setdefault(mirror.scraper, []).append(mirror)

        async def check_scraper_mirrors(scraper_name: str, mirrors: list[Mirror]) -> None:
            scraper_cfg = scrapers.get(scraper_name)
            for mirror in mirrors:
                try:
                    await check_mirror(
                        mirror, scraper_cfg, client, scoring_config, run_full=True,
                        geo_config=geo_config, geo_budget=geo_budget,
                    )
                except Exception:
                    logger.exception("Unexpected error checking %s", mirror.url)
                await random_delay(1.0, 5.0)

        # Run all scrapers concurrently
        tasks = [
            check_scraper_mirrors(name, mirrors)
            for name, mirrors in by_scraper.items()
        ]
        await asyncio.gather(*tasks)


async def run_inactive_check(
    state: MirrorState,
    scoring_config: dict[str, Any],
    geo_config: dict[str, Any] | None = None,
    geo_budget: GeoBudget | None = None,
) -> None:
    """Inactive check: re-probe Dead, Fallen Comrade, and GeoRestricted mirrors (basic only)."""
    inactive_tiers = {Tier.DEAD.value, Tier.FALLEN_COMRADE.value, Tier.GEO_RESTRICTED.value}

    async with httpx.AsyncClient(http2=True) as client:
        by_scraper: dict[str, list[Mirror]] = {}
        for mirror in state.mirrors:
            if mirror.tier in inactive_tiers:
                by_scraper.setdefault(mirror.scraper, []).append(mirror)

        async def check_scraper_mirrors(scraper_name: str, mirrors: list[Mirror]) -> None:
            for mirror in mirrors:
                try:
                    await check_mirror(
                        mirror, None, client, scoring_config, run_full=False,
                        geo_config=geo_config, geo_budget=geo_budget,
                    )
                except Exception:
                    logger.exception("Unexpected error checking %s", mirror.url)
                await random_delay(1.0, 5.0)

        tasks = [
            check_scraper_mirrors(name, mirrors)
            for name, mirrors in by_scraper.items()
        ]
        await asyncio.gather(*tasks)


async def run_discovery(
    state: MirrorState,
    known_domains: dict[str, list[str]],
    tlds: list[str],
) -> None:
    """Discovery: TLD brute force for new mirrors, parallelized across scrapers."""
    existing_urls = {m.url for m in state.mirrors}

    # Separate clients: one for DoH resolution, one for mirror probes.
    # Prevents a slow DoH provider from starving the mirror check pool.
    async with httpx.AsyncClient() as doh_client, httpx.AsyncClient(http2=True) as probe_client:

        async def discover_for_scraper(
            scraper_name: str, base_names: list[str]
        ) -> list[Mirror]:
            try:
                return await discover_mirrors(
                    scraper_name, base_names, tlds, state, doh_client, probe_client
                )
            except Exception:
                logger.exception("Unexpected error during discovery for %s", scraper_name)
                return []

        tasks = [
            discover_for_scraper(name, bases)
            for name, bases in known_domains.items()
        ]
        results = await asyncio.gather(*tasks)

        for new_mirrors in results:
            for mirror in new_mirrors:
                if mirror.url not in existing_urls:
                    state.mirrors.append(mirror)
                    existing_urls.add(mirror.url)
                    logger.info("Added new mirror: %s [%s]", mirror.url, mirror.scraper)


def _save_results(
    state: MirrorState,
    scoring_config: dict[str, Any],
    geo_config: dict[str, Any] | None = None,
) -> None:
    """Save state, scores, and per-region scores, each handled independently."""
    try:
        save_state(state)
    except Exception:
        logger.exception("Failed to save state")

    try:
        save_scores(state, scoring_config)
    except Exception:
        logger.exception("Failed to save scores")

    if geo_config and geo_config.get("regions"):
        try:
            save_region_scores(state, scoring_config, list(geo_config["regions"].keys()))
        except Exception:
            logger.exception("Failed to save region scores")


def _sigterm_handler(signum: int, frame: Any) -> None:
    """On SIGTERM, save whatever state we have before exiting."""
    logger.warning("Received SIGTERM, saving partial state before exit")
    if _current_state is not None and _current_scoring_config is not None:
        _save_results(_current_state, _current_scoring_config, _current_geo_config)
    sys.exit(0)


def _load_config_file(path: Path) -> Any:
    """Load a JSON config file with error handling."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Failed to load config %s: %s", path, e)
        raise SystemExit(1) from e


def _load_geo_config() -> dict[str, Any] | None:
    """Load config/regions.json if present and enabled; else None (geo disabled)."""
    path = CONFIG_DIR / "regions.json"
    if not path.exists():
        return None
    try:
        cfg = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        logger.error("Failed to load regions config %s: %s", path, e)
        return None
    if not cfg.get("enabled", True):
        return None
    return cfg


def main() -> None:
    global _current_state, _current_scoring_config, _current_geo_config

    setup_logging()
    start_time = time.monotonic()

    signal.signal(signal.SIGTERM, _sigterm_handler)

    parser = argparse.ArgumentParser(description="Mirror Health Service")
    parser.add_argument(
        "mode",
        choices=["active", "inactive", "discovery"],
        help="Workflow mode to run",
    )
    args = parser.parse_args()

    # Load configuration — fail fast with clear error messages
    scrapers = _load_config_file(CONFIG_DIR / "scrapers.json")
    known_domains = _load_config_file(CONFIG_DIR / "known_domains.json")
    tlds = _load_config_file(CONFIG_DIR / "tlds.json")
    scoring_config = load_scoring_config()
    geo_config = _load_geo_config()

    # Load state and bootstrap seed mirrors
    state = load_state()
    added = bootstrap_state(state)
    if added:
        logger.info("Bootstrapped %d seed mirrors", added)
    logger.info("Loaded state with %d mirrors", len(state.mirrors))

    # Geo-locate the runner (best-effort)
    state.runner_geo = fetch_runner_geo()

    # Store refs for signal handler
    _current_state = state
    _current_scoring_config = scoring_config
    _current_geo_config = geo_config

    geo_budget = (
        GeoBudget(remaining=geo_config.get("per_run_recheck_cap", 15))
        if geo_config
        else None
    )

    # Run appropriate workflow
    if args.mode == "active":
        asyncio.run(run_active_check(state, scrapers, scoring_config, geo_config, geo_budget))
    elif args.mode == "inactive":
        asyncio.run(run_inactive_check(state, scoring_config, geo_config, geo_budget))
    elif args.mode == "discovery":
        asyncio.run(run_discovery(state, known_domains, tlds))

    # Save results
    _save_results(state, scoring_config, geo_config)

    elapsed = time.monotonic() - start_time
    logger.info(
        "Completed %s run in %.1fs. %d mirrors tracked.",
        args.mode,
        elapsed,
        len(state.mirrors),
    )
    if elapsed > 300:
        logger.warning(
            "Run took %.1fs (>5min). Consider reducing mirror count or check frequency.",
            elapsed,
        )


if __name__ == "__main__":
    main()

"""Geo-restriction detection via check-host.net multi-node probing.

A single US runner can't tell a geo-blocked-but-alive mirror from a dead one.
When a mirror fails our (US) check, we ask check-host.net to probe it from nodes
worldwide; if it answers from non-US regions, it's GeoRestricted, not Dead.

Everything here is best-effort: any error or ambiguous result yields no verdict
(`GeoResult.ok is False`) and callers fall back to the standard tier ladder.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from .models import Mirror, Tier
from .utils import logger

CHECK_HOST_BASE = "https://check-host.net"

DEFAULT_GEO_RECHECK_REASONS = (
    "timeout",
    "connect_error",
    "connection_refused",
    "rate_limited",
    "server_error",
)


@dataclass
class GeoResult:
    """Outcome of a check-host probe. `ok` is False when there's no usable verdict."""

    ok: bool = False
    reachable_regions: list[str] = field(default_factory=list)  # non-US regions only
    checked_nodes: int = 0
    reachable_nodes: int = 0


@dataclass
class GeoBudget:
    """Soft per-run cap on how many check-host probes we'll make."""

    remaining: int

    def take(self) -> bool:
        if self.remaining > 0:
            self.remaining -= 1
            return True
        return False


def _geo_cooldown_elapsed(mirror: Mirror, now: datetime, cooldown_hours: float) -> bool:
    if mirror.geo_checked_at is None:
        return True
    return (now - mirror.geo_checked_at) >= timedelta(hours=cooldown_hours)


def should_geo_recheck(
    mirror: Mirror,
    standard_tier: Tier,
    failure_reason: str | None,
    now: datetime,
    geo_config: dict | None,
    geo_budget: GeoBudget | None,
) -> bool:
    """Gate check-host probing: only at the demotion brink, bounded, cooled-down.

    Consumes a budget slot (side effect) when it returns True.
    """
    if not geo_config or not geo_config.get("enabled", True):
        return False
    reasons = geo_config.get("recheck_failure_reasons", DEFAULT_GEO_RECHECK_REASONS)
    if failure_reason not in reasons:
        return False
    currently_geo = Tier(mirror.tier) == Tier.GEO_RESTRICTED
    # Only worth probing when the mirror would otherwise drop to Dead/FC, or is
    # already GeoRestricted (so we can confirm it's still alive elsewhere).
    if standard_tier not in (Tier.DEAD, Tier.FALLEN_COMRADE) and not currently_geo:
        return False
    if not _geo_cooldown_elapsed(mirror, now, geo_config.get("recheck_cooldown_hours", 24)):
        return False
    if geo_budget is not None and not geo_budget.take():
        return False
    return True


def _node_ok(result) -> bool:
    """True when a check-host check-result entry indicates a successful fetch.

    check-http results look like ``[[1, time, "OK", "200", "ip"]]`` on success,
    ``[[0, time, "Timed out"]]`` on failure, or ``null`` while still pending.
    """
    return (
        isinstance(result, list)
        and len(result) >= 1
        and isinstance(result[0], list)
        and len(result[0]) >= 1
        and result[0][0] == 1
    )


def _node_country(hostname: str, meta) -> str | None:
    """Derive a 2-letter country code for a check-host node.

    Prefers the country code in the node metadata; falls back to the hostname
    prefix (e.g. ``de1.node.check-host.net`` -> ``de``).
    """
    if isinstance(meta, (list, tuple)):
        for item in meta[:3]:
            if isinstance(item, str) and len(item) == 2 and item.isalpha():
                return item.lower()
    head = hostname.split(".", 1)[0]
    letters = "".join(c for c in head if c.isalpha())
    return letters[:2].lower() if len(letters) >= 2 else None


def _country_to_region(cc: str | None, regions: dict[str, list[str]]) -> str | None:
    if not cc:
        return None
    cc = cc.lower()
    for region, codes in regions.items():
        if cc in codes:
            return region
    return None


async def check_geo_reachability(
    url: str, client: httpx.AsyncClient, geo_config: dict
) -> GeoResult:
    """Probe `url` from check-host.net nodes; report which non-US regions reach it.

    Best-effort: returns ``GeoResult(ok=False)`` on any error or if no node finished.
    """
    regions = geo_config.get("regions", {})
    max_nodes = geo_config.get("max_nodes", 10)
    attempts = max(1, geo_config.get("poll_attempts", 4))
    interval = geo_config.get("poll_interval_seconds", 1.5)
    min_nodes = geo_config.get("min_nodes_per_region", 1)
    headers = {"Accept": "application/json"}

    try:
        resp = await client.get(
            f"{CHECK_HOST_BASE}/check-http",
            params={"host": url, "max_nodes": max_nodes},
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        submit = resp.json()
    except Exception as e:
        logger.debug("check-host submit failed for %s: %s", url, e)
        return GeoResult(ok=False)

    request_id = submit.get("request_id")
    if not submit.get("ok") or not request_id:
        logger.debug("check-host declined %s: %s", url, submit)
        return GeoResult(ok=False)
    node_meta = submit.get("nodes") or {}

    results: dict = {}
    for _ in range(attempts):
        await asyncio.sleep(interval)
        try:
            r = await client.get(
                f"{CHECK_HOST_BASE}/check-result/{request_id}",
                headers=headers,
                timeout=10.0,
            )
            r.raise_for_status()
            results = r.json() or {}
        except Exception as e:
            logger.debug("check-host poll failed for %s: %s", url, e)
            continue
        if results and all(v is not None for v in results.values()):
            break

    region_hits: dict[str, int] = {}
    checked = 0
    reachable = 0
    for node, res in results.items():
        if res is None:
            continue
        checked += 1
        if _node_ok(res):
            reachable += 1
            region = _country_to_region(_node_country(node, node_meta.get(node)), regions)
            if region:
                region_hits[region] = region_hits.get(region, 0) + 1

    if checked == 0:
        return GeoResult(ok=False)  # nothing finished -> undetermined

    reachable_regions = sorted(
        r for r, n in region_hits.items() if n >= min_nodes and r != "US"
    )
    return GeoResult(
        ok=True,
        reachable_regions=reachable_regions,
        checked_nodes=checked,
        reachable_nodes=reachable,
    )

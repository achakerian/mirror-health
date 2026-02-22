from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

logger = logging.getLogger("mirror-health")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

CLOUDFLARE_MARKERS = [
    "cf-browser-verification",
    "challenge-platform",
    "cf-chl-bypass",
    "just a moment...",
    "attention required! | cloudflare",
]

PLACEHOLDER_MARKERS = [
    "domain is for sale",
    "buy this domain",
    "parked domain",
    "this page is under construction",
    "coming soon",
    "website is for sale",
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


async def random_delay(min_s: float = 1.0, max_s: float = 5.0) -> None:
    delay = random.uniform(min_s, max_s)
    await asyncio.sleep(delay)


def detect_issues(html: str) -> tuple[bool, bool]:
    """Detect Cloudflare and placeholder markers in a single pass.

    Returns (cloudflare_detected, placeholder_detected).
    """
    lower = html.lower()
    cf = any(m in lower for m in CLOUDFLARE_MARKERS)
    ph = any(m in lower for m in PLACEHOLDER_MARKERS)
    return cf, ph


def detect_cloudflare(html: str) -> bool:
    lower = html.lower()
    return any(m in lower for m in CLOUDFLARE_MARKERS)


def detect_placeholder(html: str) -> bool:
    lower = html.lower()
    return any(m in lower for m in PLACEHOLDER_MARKERS)


def fetch_runner_geo() -> Optional["RunnerGeo"]:  # noqa: F821
    """Fetch geolocation of the current runner from ipinfo.io.

    Best-effort: returns None on any failure so the run proceeds normally.
    """
    import httpx

    from .models import RunnerGeo

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get("https://ipinfo.io/json")
            resp.raise_for_status()
            data = resp.json()
        geo = RunnerGeo(
            ip=data.get("ip"),
            city=data.get("city"),
            region=data.get("region"),
            country=data.get("country"),
            org=data.get("org"),
            timezone=data.get("timezone"),
        )
        logger.info("Runner geo: %s, %s, %s", geo.city, geo.region, geo.country)
        return geo
    except Exception as e:
        logger.warning("Failed to fetch runner geo: %s", e)
        return None


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

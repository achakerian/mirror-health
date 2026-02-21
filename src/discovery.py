from __future__ import annotations

import asyncio

import httpx

from .checks.basic import BasicCheckResult, run_basic_check
from .models import Mirror, MirrorState
from .utils import logger, random_delay

DNS_CONCURRENCY = 10

DOH_CLOUDFLARE = "https://cloudflare-dns.com/dns-query"
DOH_GOOGLE = "https://dns.google/resolve"


async def resolve_doh(
    domain: str,
    client: httpx.AsyncClient,
    doh_url: str = DOH_CLOUDFLARE,
) -> bool:
    """Resolve domain via DoH JSON API. Returns True if an A record exists."""
    try:
        response = await client.get(
            doh_url,
            params={"name": domain, "type": "A"},
            headers={"Accept": "application/dns-json"},
            timeout=5.0,
        )
        if response.status_code != 200:
            return False
        data = response.json()
        # Status 0 = NOERROR, Answer must contain at least one record
        return data.get("Status") == 0 and bool(data.get("Answer"))
    except Exception as e:
        logger.debug("DoH resolution failed for %s via %s: %s", domain, doh_url, e)
        return False


async def resolve_with_fallback(domain: str, client: httpx.AsyncClient) -> bool:
    """Try Cloudflare DoH, fall back to Google on failure."""
    if await resolve_doh(domain, client, DOH_CLOUDFLARE):
        return True
    logger.debug("Cloudflare DoH failed for %s, trying Google", domain)
    return await resolve_doh(domain, client, DOH_GOOGLE)


async def discover_mirrors(
    scraper_name: str,
    base_names: list[str],
    tlds: list[str],
    existing_state: MirrorState,
    doh_client: httpx.AsyncClient,
    probe_client: httpx.AsyncClient,
) -> list[Mirror]:
    """Discover new mirrors by brute-forcing base domain + TLD combinations.

    Args:
        scraper_name: The scraper identifier (e.g. "yts", "1337x")
        base_names: Base domain names to try (e.g. ["yts", "yify"])
        tlds: TLD list without dots (e.g. ["to", "st", "mx"])
        existing_state: Current mirror state for deduplication
        doh_client: httpx client dedicated to DoH resolution
        probe_client: httpx client dedicated to mirror HTTP probes
    """
    existing_urls = {m.url for m in existing_state.mirrors}
    discovered: list[Mirror] = []

    # Generate all candidate domains
    candidates: list[str] = []
    for base in base_names:
        for tld in tlds:
            domain = f"{base}.{tld}"
            url = f"https://{domain}"
            if url not in existing_urls:
                candidates.append(domain)

    logger.info(
        "Discovery [%s]: %d candidates to probe (%d base names x %d TLDs, %d already tracked)",
        scraper_name,
        len(candidates),
        len(base_names),
        len(tlds),
        len(existing_urls),
    )

    # Phase 1: Batch DNS resolution with concurrency limit
    sem = asyncio.Semaphore(DNS_CONCURRENCY)

    async def resolve_one(domain: str) -> tuple[str, bool]:
        async with sem:
            resolved = await resolve_with_fallback(domain, doh_client)
            return (domain, resolved)

    results = await asyncio.gather(*[resolve_one(d) for d in candidates])
    resolved_domains = [domain for domain, ok in results if ok]

    logger.info(
        "Discovery [%s]: %d/%d candidates resolved DNS",
        scraper_name,
        len(resolved_domains),
        len(candidates),
    )

    # Phase 2: Sequential HTTP probes on resolved domains (rate limited)
    for domain in resolved_domains:
        logger.info("Discovery [%s]: %s resolves, running basic check", scraper_name, domain)

        url = f"https://{domain}"
        result: BasicCheckResult = await run_basic_check(url, probe_client)

        if result.passed:
            mirror = Mirror(url=url, scraper=scraper_name)
            discovered.append(mirror)
            logger.info("Discovery [%s]: new mirror found: %s", scraper_name, url)
        else:
            logger.debug(
                "Discovery [%s]: %s resolved but failed basic check: %s",
                scraper_name,
                domain,
                result.failure_reason,
            )

        await random_delay(1.0, 3.0)

    logger.info("Discovery [%s]: found %d new mirrors", scraper_name, len(discovered))
    return discovered

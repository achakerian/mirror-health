from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from ..utils import detect_issues, logger, random_user_agent

BASIC_TIMEOUT = 10.0
MIN_BODY_LENGTH = 100
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB — no legitimate mirror index page is this large


@dataclass
class BasicCheckResult:
    passed: bool
    response_ms: float = 0.0
    cloudflare_detected: bool = False
    failure_reason: str | None = None
    status_code: int | None = None
    body: str | None = None


async def run_basic_check(
    url: str,
    client: httpx.AsyncClient,
    timeout: float = BASIC_TIMEOUT,
) -> BasicCheckResult:
    """Basic health check: GET /, verify 200, non-empty, no Cloudflare, no placeholder."""
    headers = {"User-Agent": random_user_agent()}
    start = time.monotonic()

    try:
        response = await client.get(
            url,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        # Guard against oversized responses before reading body
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > MAX_RESPONSE_BYTES:
            return BasicCheckResult(
                passed=False,
                response_ms=elapsed_ms,
                status_code=response.status_code,
                failure_reason="response_too_large",
            )

        if response.status_code != 200:
            reason = f"http_{response.status_code}"
            if response.status_code in (403, 429):
                reason = "rate_limited"
            elif response.status_code >= 500:
                reason = "server_error"
            return BasicCheckResult(
                passed=False,
                response_ms=elapsed_ms,
                status_code=response.status_code,
                failure_reason=reason,
            )

        body = response.text
        if not body or len(body.strip()) < MIN_BODY_LENGTH:
            return BasicCheckResult(
                passed=False,
                response_ms=elapsed_ms,
                status_code=200,
                failure_reason="empty_response",
            )

        cf_detected, ph_detected = detect_issues(body)
        if cf_detected:
            return BasicCheckResult(
                passed=False,
                response_ms=elapsed_ms,
                cloudflare_detected=True,
                status_code=200,
                failure_reason="cloudflare_challenge",
            )

        if ph_detected:
            return BasicCheckResult(
                passed=False,
                response_ms=elapsed_ms,
                status_code=200,
                failure_reason="placeholder_content",
            )

        logger.debug("Basic check passed for %s (%.0fms)", url, elapsed_ms)
        return BasicCheckResult(
            passed=True,
            response_ms=elapsed_ms,
            status_code=200,
            body=body,
        )

    except httpx.TimeoutException:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("Basic check timeout for %s", url)
        return BasicCheckResult(
            passed=False,
            response_ms=elapsed_ms,
            failure_reason="timeout",
        )

    except httpx.ConnectError as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        reason = _classify_connect_error(e)
        return BasicCheckResult(
            passed=False,
            response_ms=elapsed_ms,
            failure_reason=reason,
        )

    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("Basic check error for %s: %s: %s", url, type(e).__name__, e)
        return BasicCheckResult(
            passed=False,
            response_ms=elapsed_ms,
            failure_reason="unexpected_error",
        )


def _classify_connect_error(e: httpx.ConnectError) -> str:
    """Classify a ConnectError into a specific failure reason."""
    msg = str(e).lower()
    if "name or service not known" in msg or "nodename nor servname" in msg or "getaddrinfo" in msg:
        return "dns_failure"
    if "ssl" in msg or "certificate" in msg or "tls" in msg:
        return "ssl_error"
    if "refused" in msg:
        return "connection_refused"
    return "connect_error"

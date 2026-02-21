from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from ..utils import logger, random_user_agent

FULL_TIMEOUT = 15.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB


@dataclass
class FullCheckResult:
    passed: bool
    response_ms: float = 0.0
    failure_reason: str | None = None


async def run_full_check(
    url: str,
    scraper_config: dict,
    client: httpx.AsyncClient,
    timeout: float = FULL_TIMEOUT,
    basic_body: str | None = None,
) -> FullCheckResult:
    """Full fingerprint check: scraper-specific content validation.

    Should only be called when the most recent basic check passed.

    Args:
        basic_body: When provided and fingerprint_path is "/", reuses this
            body from the basic check instead of making a second HTTP request.
    """
    fp_type = scraper_config["fingerprint_type"]
    fp_path = scraper_config["fingerprint_path"]
    fp_check = scraper_config["fingerprint_check"]

    # Reuse basic check body when fingerprint path is root and check is HTML-based
    if basic_body is not None and fp_path == "/" and fp_type in ("html_contains", "html_contains_any"):
        return _check_html_contains(
            basic_body, fp_check, 0.0,
            match_all=(fp_type == "html_contains"),
        )

    target_url = url.rstrip("/") + fp_path
    headers = {"User-Agent": random_user_agent()}
    start = time.monotonic()

    try:
        response = await client.get(
            target_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        # Guard against oversized responses
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > MAX_RESPONSE_BYTES:
            return FullCheckResult(
                passed=False,
                response_ms=elapsed_ms,
                failure_reason="response_too_large",
            )

        if response.status_code != 200:
            return FullCheckResult(
                passed=False,
                response_ms=elapsed_ms,
                failure_reason=f"http_{response.status_code}",
            )

        if fp_type == "json_api":
            return _check_json_api(response, fp_check, elapsed_ms)
        elif fp_type in ("html_contains", "html_contains_any"):
            try:
                body = response.text
            except Exception:
                return FullCheckResult(
                    passed=False,
                    response_ms=elapsed_ms,
                    failure_reason="decode_error",
                )
            return _check_html_contains(
                body, fp_check, elapsed_ms,
                match_all=(fp_type == "html_contains"),
            )
        else:
            return FullCheckResult(
                passed=False,
                response_ms=elapsed_ms,
                failure_reason="unknown_fingerprint_type",
            )

    except httpx.TimeoutException:
        elapsed_ms = (time.monotonic() - start) * 1000
        return FullCheckResult(
            passed=False,
            response_ms=elapsed_ms,
            failure_reason="timeout",
        )

    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("Full check error for %s: %s: %s", url, type(e).__name__, e)
        return FullCheckResult(
            passed=False,
            response_ms=elapsed_ms,
            failure_reason="unexpected_error",
        )


def _check_json_api(
    response: httpx.Response, keys: list[str], elapsed_ms: float
) -> FullCheckResult:
    """Validate JSON API response by traversing nested keys."""
    try:
        data = response.json()
        current = data
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return FullCheckResult(
                    passed=False,
                    response_ms=elapsed_ms,
                    failure_reason="invalid_response",
                )
            current = current[key]
        # The final value must be non-empty (non-empty list/dict/string)
        if not current:
            return FullCheckResult(
                passed=False,
                response_ms=elapsed_ms,
                failure_reason="invalid_response",
            )
        return FullCheckResult(passed=True, response_ms=elapsed_ms)
    except Exception:
        return FullCheckResult(
            passed=False,
            response_ms=elapsed_ms,
            failure_reason="invalid_response",
        )


def _check_html_contains(
    body: str,
    markers: str | list[str],
    elapsed_ms: float,
    match_all: bool = True,
) -> FullCheckResult:
    """Check if HTML body contains the specified markers.

    match_all=True: ALL markers must be present (AND logic).
    match_all=False: ANY marker must be present (OR logic).
    """
    if isinstance(markers, str):
        markers = [markers]

    if match_all:
        for marker in markers:
            if marker not in body:
                return FullCheckResult(
                    passed=False,
                    response_ms=elapsed_ms,
                    failure_reason="invalid_response",
                )
        return FullCheckResult(passed=True, response_ms=elapsed_ms)
    else:
        for marker in markers:
            if marker in body:
                return FullCheckResult(passed=True, response_ms=elapsed_ms)
        return FullCheckResult(
            passed=False,
            response_ms=elapsed_ms,
            failure_reason="invalid_response",
        )

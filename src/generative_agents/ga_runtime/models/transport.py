"""Cancellable HTTP waits without abandoned worker threads or SDK retries."""
from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
import ssl
import time
from urllib.parse import urlsplit
from urllib.request import proxy_bypass

import httpx

from generative_agents.ga_runtime.models.retry import check_cancelled


def request_json(method, url, *, headers=None, payload=None, deadline, control=None, metadata=None):
    """Execute on the calling worker; cancelling the task closes its connection."""
    check_cancelled(control)
    return asyncio.run(_request_json(method, url, headers, payload, deadline, control, metadata))


async def _request_json(method, url, headers, payload, deadline, control, metadata):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise httpx.TimeoutException("model logical-call timeout budget exhausted")
    # HTTPX discovers Windows system proxies but does not apply the registry's
    # bypass list. Honor it, especially for local model servers: an intervening
    # proxy could keep generating after its downstream connection is cancelled.
    # Preserve the CA overrides accepted by the former requests transport.
    # Otherwise HTTPX retains its SSL_CERT_FILE / SSL_CERT_DIR handling.
    ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE")
    verify = (
        ssl.create_default_context(**{
            "capath" if Path(ca_bundle).is_dir() else "cafile": ca_bundle,
        }) if ca_bundle else True
    )
    transport = (
        httpx.AsyncHTTPTransport(retries=0, verify=verify)
        if proxy_bypass(urlsplit(url).hostname or "") else None
    )
    async with httpx.AsyncClient(timeout=remaining, transport=transport, verify=verify) as client:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise httpx.TimeoutException("model logical-call timeout budget exhausted")
        # Cooperating providers use the same remaining allowance for queueing,
        # prefill and generation. The client still enforces its own deadline.
        request_headers = httpx.Headers(headers)
        request_headers['X-Model-Timeout-Seconds'] = str(remaining)
        if metadata is not None:
            metadata['request_timeout_seconds'] = remaining
        request = asyncio.create_task(client.request(method, url, headers=request_headers, json=payload))
        try:
            while not request.done():
                check_cancelled(control)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise httpx.TimeoutException("model logical-call timeout budget exhausted")
                await asyncio.wait({request}, timeout=min(0.05, remaining))
            check_cancelled(control)
            response = request.result()
            if metadata is not None:
                _record_limits(response.headers, metadata)
            response.raise_for_status()
            return response.json()
        finally:
            if not request.done():
                request.cancel()
            # Await cleanup before returning; never leave a live request behind.
            await asyncio.gather(request, return_exceptions=True)


def _record_limits(headers, metadata):
    """Retain numeric provider limits, never arbitrary or credential headers."""
    for header, field in (
        ('X-Model-Timeout-Seconds', 'effective_timeout_seconds'),
        ('X-Model-Max-Timeout-Seconds', 'max_timeout_seconds'),
        ('X-Model-Effective-Max-Tokens', 'effective_max_tokens'),
    ):
        try:
            value = float(headers[header])
        except (KeyError, ValueError, TypeError):
            continue
        if math.isfinite(value) and value > 0:
            if field == 'effective_max_tokens' and not value.is_integer():
                continue
            metadata[field] = int(value) if field == 'effective_max_tokens' else value

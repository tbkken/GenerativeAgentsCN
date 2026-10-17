"""Cancellable HTTP waits without abandoned worker threads or SDK retries."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import ssl
import time
from urllib.parse import urlsplit
from urllib.request import proxy_bypass

import httpx

from generative_agents.ga_runtime.models.retry import check_cancelled


def request_json(method, url, *, headers=None, payload=None, deadline, control=None):
    """Execute on the calling worker; cancelling the task closes its connection."""
    check_cancelled(control)
    return asyncio.run(_request_json(method, url, headers, payload, deadline, control))


async def _request_json(method, url, headers, payload, deadline, control):
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
        request = asyncio.create_task(client.request(method, url, headers=headers, json=payload))
        try:
            while not request.done():
                check_cancelled(control)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise httpx.TimeoutException("model logical-call timeout budget exhausted")
                await asyncio.wait({request}, timeout=min(0.05, remaining))
            check_cancelled(control)
            response = request.result()
            response.raise_for_status()
            return response.json()
        finally:
            if not request.done():
                request.cancel()
            # Await cleanup before returning; never leave a live request behind.
            await asyncio.gather(request, return_exceptions=True)

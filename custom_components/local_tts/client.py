"""Thin async client for the TTS service (prod gateway).

Two calls: fetch the prod entries (voices) and synthesize one request. The
service owns all backend knowledge; this client only carries a prod-entry id
plus optional param overrides.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

import aiohttp

from .const import DEFAULT_TIMEOUT, LOGGER


async def fetch_prod(
    session: aiohttp.ClientSession, base_url: str, api_key: str
) -> dict[str, Any]:
    """GET /api/prod -> {"entries": [...], "backends": {...}}. Empty on failure."""
    url = base_url.rstrip("/") + "/api/prod"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                LOGGER.warning("prod catalog fetch: %s returned %s", url, resp.status)
                return {}
            return await resp.json()
    except Exception as err:
        LOGGER.warning("prod catalog fetch failed (%s): %s", url, err)
        return {}


async def synth(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    body: dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> bytes:
    """POST /v1/audio/speech, return the full WAV bytes.

    Raises aiohttp.ClientError / asyncio.TimeoutError on failure — the caller
    decides how to surface it.
    """
    url = base_url.rstrip("/") + "/v1/audio/speech"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with session.post(
        url, json=body, headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        resp.raise_for_status()
        return await resp.read()


async def synth_stream(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    body: dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> AsyncGenerator[Any]:
    """Stream one synth as raw PCM. Yields the sample rate (int) first, then the
    PCM byte chunks as the engine emits them — so first audio arrives after the
    first chunk, not the whole clip. The gateway sends 16-bit mono PCM with the
    rate in X-Sample-Rate (the pcm stream carries no WAV header).

    Raises aiohttp.ClientError / asyncio.TimeoutError on failure.
    """
    url = base_url.rstrip("/") + "/v1/audio/speech"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with session.post(
        url, json={**body, "stream": True}, headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        resp.raise_for_status()
        yield int(resp.headers.get("X-Sample-Rate", 24000))
        async for chunk in resp.content.iter_chunked(8192):
            yield chunk

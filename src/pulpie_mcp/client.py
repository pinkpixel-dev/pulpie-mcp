"""Calls the backend's /extract endpoint on behalf of the MCP tools."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from pulpie_mcp import config
from pulpie_mcp.launcher import ensure_backend

EXTRACT_TIMEOUT = 120.0


class PulpieError(Exception):
    """A failure with a message meant for the agent."""


@dataclass
class Page:
    url: str
    title: str | None
    markdown: str
    links: list[str]
    passthrough: bool


async def extract(url: str, *, include_links: bool = False) -> Page:
    payload = {
        "url": url,
        "allow_private": config.allow_private(),
        "include_links": include_links,
    }
    # One retry covers the backend idling out right as a request arrives.
    for attempt in (1, 2):
        await ensure_backend()
        try:
            async with httpx.AsyncClient(timeout=EXTRACT_TIMEOUT) as client:
                resp = await client.post(f"{config.backend_url()}/extract", json=payload)
        except httpx.ConnectError:
            if attempt == 2:
                raise PulpieError(f"Lost connection to the pulpie backend at {config.backend_url()}.")
            continue
        except httpx.TimeoutException as err:
            raise PulpieError(f"Timed out extracting {url}.") from err

        if resp.status_code != 200:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise PulpieError(str(detail))

        data = resp.json()
        return Page(
            url=data["url"],
            title=data.get("title"),
            markdown=data["markdown"],
            links=data.get("links", []),
            passthrough=data.get("passthrough", False),
        )
    raise PulpieError("Unreachable")  # pragma: no cover

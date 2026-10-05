"""Blocks requests to private, loopback, and other non-public addresses.

Agents fetch URLs they found on the open web, so by default nothing is
allowed to reach localhost or the local network. The check runs on every
request, including each redirect hop.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "text/markdown;q=0.9,text/plain;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class BlockedAddressError(Exception):
    """Raised when a URL points at a non-public address."""


def is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def check_host(host: str) -> None:
    """Raise BlockedAddressError unless every address for host is public."""
    try:
        addresses = [ipaddress.ip_address(host.strip("[]")).compressed]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, None, type=socket.SOCK_STREAM
            )
        except socket.gaierror as err:
            raise httpx.ConnectError(f"Could not resolve host {host!r}") from err
        addresses = [str(info[4][0]) for info in infos]

    for address in addresses:
        if not is_public_ip(address.split("%")[0]):
            where = host if host.strip("[]") == address else f"{host} ({address})"
            raise BlockedAddressError(
                f"{where} is a private or local address. "
                "Set PULPIE_ALLOW_PRIVATE=1 to allow local and LAN addresses."
            )


def make_client(*, allow_private: bool, timeout: float = 20.0) -> httpx.AsyncClient:
    """An httpx client that enforces the public-address rule on every hop."""

    async def guard(request: httpx.Request) -> None:
        if request.url.scheme not in ("http", "https"):
            raise BlockedAddressError(f"Unsupported URL scheme {request.url.scheme!r}.")
        if not allow_private:
            await check_host(request.url.host)

    return httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        follow_redirects=True,
        max_redirects=10,
        event_hooks={"request": [guard]},
    )

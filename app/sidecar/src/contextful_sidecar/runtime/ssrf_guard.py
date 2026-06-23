"""Block web_fetch to private/loopback/link-local hosts (SSRF mitigation)."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata.google.internal",
})


def _ip_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
    )


def validate_fetch_url(url: str) -> str | None:
    """Return an ERROR message if the URL must not be fetched, else None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "ERROR: web_fetch only supports http/https URLs"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return "ERROR: web_fetch URL has no host"
    if host in _BLOCKED_HOSTNAMES:
        return f"ERROR: web_fetch blocked (restricted host: {host})"
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return f"ERROR: web_fetch blocked (could not resolve host: {host})"
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _ip_blocked(addr):
            return f"ERROR: web_fetch blocked (restricted address: {ip_str})"
    return None

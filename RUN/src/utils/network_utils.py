"""
Network utilities.
Autonomous outbound IP detection and caching for exchange whitelist configuration.
"""

from __future__ import annotations

import logging
import re
import socket
import urllib.request

logger = logging.getLogger("bot.utils.network")

_cached_outbound_ip: str | None = None
_IPV4_REGEX = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")


def get_outbound_ip(timeout: float = 3.0) -> str:
    """
    Autonomously detect the server's public outbound IP address.
    Caches the result in memory so it doesn't incur latency on repeated checks.
    """
    global _cached_outbound_ip
    if _cached_outbound_ip:
        return _cached_outbound_ip

    endpoints = [
        "https://api.ipify.org",
        "https://icanhazip.com",
        "https://ifconfig.me/ip",
        "https://checkip.amazonaws.com",
    ]

    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ip = resp.read().decode("utf-8").strip()
                if ip and _IPV4_REGEX.match(ip):
                    _cached_outbound_ip = ip
                    return ip
        except Exception:
            continue

    # Fallback: attempt socket route check to external DNS
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(1.0)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            if local_ip and _IPV4_REGEX.match(local_ip):
                _cached_outbound_ip = local_ip
                return local_ip
    except Exception:
        pass

    return "unknown"


def get_whitelist_ip_summary() -> str:
    """Returns the autonomously detected server outbound IP."""
    return get_outbound_ip()

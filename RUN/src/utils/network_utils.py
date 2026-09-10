"""
Network utilities.
Provides outbound IP detection and caching for exchange whitelist configuration.
"""

from __future__ import annotations

import logging
from typing import Optional
import urllib.request

logger = logging.getLogger("bot.utils.network")

_cached_outbound_ip: Optional[str] = None


def get_outbound_ip(timeout: float = 3.0) -> str:
    """
    Get the server's public outbound IP address.
    Caches the result to avoid redundant network requests.
    """
    global _cached_outbound_ip
    if _cached_outbound_ip:
        return _cached_outbound_ip

    endpoints = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]

    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ip = resp.read().decode("utf-8").strip()
                if ip and len(ip.split(".")) == 4:
                    _cached_outbound_ip = ip
                    return ip
        except Exception:
            continue

    return "46.210.168.102"

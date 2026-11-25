"""Chooses which proxy each request goes through.

- PROXY_URL: rotating residential gateway. Logged-out calls use it without keep-alive,
  so every request gets a fresh IP from the provider.
- PROXY_STICKY_URL: gateway template containing `{session}`. Each Instagram account gets its
  own stable IP (accounts that hop IPs get checkpointed). Falls back to PROXY_URL.
- PROXIES: plain list. Round-robin for logged-out calls, stable assignment per account,
  and proxies that keep failing are rested automatically.
"""

import hashlib
import itertools
import logging
import time

from app.core.config import Settings

log = logging.getLogger(__name__)

FAILURES_BEFORE_REST = 3
REST_SECONDS = 600


def _token(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:12]


class ProxyRouter:
    def __init__(self, settings: Settings) -> None:
        self._gateway = settings.proxy_url
        self._sticky = settings.proxy_sticky_url
        self._list = list(settings.proxies)
        self._rr = itertools.count()
        self._failures: dict[str, int] = {}
        self._rest_until: dict[str, float] = {}

    @property
    def mode(self) -> str:
        if self._gateway or self._sticky:
            return "gateway"
        return "list" if self._list else "direct"

    def rotates(self, proxy: str | None) -> bool:
        """True when each new connection through this proxy gets a new IP."""
        return proxy is not None and proxy == self._gateway

    def anonymous(self) -> str | None:
        if self._gateway:
            return self._gateway
        healthy = self._healthy()
        return healthy[next(self._rr) % len(healthy)] if healthy else None

    def for_account(self, username: str) -> str | None:
        if self._sticky:
            return self._sticky.replace("{session}", _token(username))
        if self._gateway:
            return self._gateway
        if not self._list:
            return None
        start = int(_token(username), 16) % len(self._list)
        ordered = self._list[start:] + self._list[:start]
        healthy = set(self._healthy())
        return next((p for p in ordered if p in healthy), ordered[0])

    def report(self, proxy: str | None, *, ok: bool) -> None:
        if proxy is None or proxy not in self._list:
            return  # gateways rotate by themselves
        if ok:
            self._failures[proxy] = 0
            return
        self._failures[proxy] = self._failures.get(proxy, 0) + 1
        if self._failures[proxy] >= FAILURES_BEFORE_REST:
            self._failures[proxy] = 0
            self._rest_until[proxy] = time.monotonic() + REST_SECONDS
            log.warning("Proxy resting for %ss after repeated failures", REST_SECONDS)

    def _healthy(self) -> list[str]:
        now = time.monotonic()
        healthy = [p for p in self._list if self._rest_until.get(p, 0) <= now]
        return healthy or self._list  # never end up with zero proxies

    def summary(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "list_size": len(self._list),
            "list_resting": len(self._list) - len(self._healthy()) if self._list else 0,
            "sticky_per_account": bool(self._sticky),
        }

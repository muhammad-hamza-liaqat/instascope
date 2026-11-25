"""Background self-management loop: keeps accounts healthy and raises alerts."""

import asyncio
import logging

from app.core.alerts import Alerter
from app.core.config import Settings
from app.scraper.accounts import AccountPool
from app.scraper.client import InstagramClient

log = logging.getLogger(__name__)

MIN_CALLS_FOR_FAILURE_ALERT = 20


class Supervisor:
    def __init__(
        self, pool: AccountPool, client: InstagramClient, alerts: Alerter, settings: Settings
    ) -> None:
        self._pool = pool
        self._client = client
        self._alerts = alerts
        self._s = settings

    async def tick(self) -> None:
        """One pass: account upkeep (logins, cooldowns, hot-reload, pool alerts) + scrape health."""
        await self._pool.maintain()
        stats = self._client.stats.snapshot()
        if (
            stats["recent_calls"] >= MIN_CALLS_FOR_FAILURE_ALERT
            and stats["failure_ratio"] >= self._s.alert_failure_ratio
        ):
            await self._alerts.send(
                "scrape_failing",
                f"{stats['failure_ratio']:.0%} of the last {stats['recent_calls']} Instagram "
                f"calls failed (last error: {stats['last_error']}). Instagram may have changed "
                "something, or proxies/accounts are blocked.",
                level="error",
            )

    async def run_forever(self) -> None:
        interval = self._s.maintenance_interval_seconds
        log.info("Supervisor started (every %ss)", interval)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Supervisor tick failed")
            await asyncio.sleep(interval)

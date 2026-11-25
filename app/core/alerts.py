"""Operational alerts: always logged, optionally POSTed to a Slack/Discord-style webhook.

Each alert has a key; the same key is not re-sent within ALERT_REPEAT_SECONDS.
"""

import logging
import time
from collections import deque
from typing import Any

import httpx

from app.core.config import Settings
from app.core.utils import utcnow

log = logging.getLogger("app.alerts")


class Alerter:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._last_sent: dict[str, float] = {}
        self.recent: deque[dict[str, Any]] = deque(maxlen=20)

    async def send(self, key: str, message: str, level: str = "warning") -> bool:
        """Returns True if the alert was emitted (False when deduplicated)."""
        now = time.monotonic()
        last = self._last_sent.get(key)
        if last is not None and now - last < self._s.alert_repeat_seconds:
            return False
        self._last_sent[key] = now

        log.log(
            logging.ERROR if level == "error" else logging.WARNING, "ALERT [%s] %s", key, message
        )
        self.recent.append(
            {"key": key, "level": level, "message": message, "at": utcnow().isoformat()}
        )

        url = self._s.alert_webhook_url
        if url:
            text = f"[{self._s.app_name}] {message}"
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    # `text` for Slack, `content` for Discord; each ignores the other.
                    await client.post(url, json={"text": text, "content": text})
            except httpx.HTTPError as exc:
                log.warning("Alert webhook failed: %r", exc)
        return True

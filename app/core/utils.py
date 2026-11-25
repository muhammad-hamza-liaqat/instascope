"""Small shared helpers."""

import logging
import re
from datetime import UTC, datetime

from app.core.errors import InvalidUsername

_USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")
_URL_RE = re.compile(r"instagram\.com/([^/?#]+)", re.IGNORECASE)


def normalize_username(value: str) -> str:
    """Accept `natgeo`, `@NatGeo` or `https://instagram.com/natgeo/` and return `natgeo`."""
    candidate = value.strip()
    if match := _URL_RE.search(candidate):
        candidate = match.group(1)
    candidate = candidate.lstrip("@").strip("/").lower()
    if not _USERNAME_RE.fullmatch(candidate):
        raise InvalidUsername(f"'{value}' is not a valid Instagram username")
    return candidate


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo; treat naive datetimes as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

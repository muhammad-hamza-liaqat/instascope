"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Comma-separated env values ("a,b,c") become lists.
CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- app ---
    app_name: str = "InstaScope"
    environment: str = "development"
    log_level: str = "INFO"
    api_keys: CsvList = []  # empty = auth disabled
    cors_origins: CsvList = ["*"]

    # --- storage ---
    database_url: str = "sqlite+aiosqlite:///./storage/instascope.db"
    storage_dir: Path = Path("storage")
    store_raw_payloads: bool = True  # keep Instagram's original JSON next to parsed data
    download_media: bool = True  # save profile picture locally (IG CDN URLs expire)
    download_thumbnails: bool = True  # also save post/reel cover images
    cache_ttl_seconds: int = 3600

    secret_key: str | None = None  # encrypts stored sessions; auto-generated if empty

    # --- Instagram accounts (self-managed pool) ---
    ig_accounts: CsvList = []  # "username:password" entries; logged in automatically
    ig_accounts_file: Path | None = None  # one "username:password" per line; hot-reloaded
    ig_session_ids: CsvList = []  # optional session-only accounts (can't re-login)
    always_use_session: bool = False  # False: try logged-out first, use an account when refused
    account_max_requests_per_hour: int = 120
    account_cooldown_seconds: int = 600  # first rest after a rate limit; doubles each time
    account_cooldown_max_seconds: int = 21600
    account_login_retry_seconds: int = 1800  # wait after a failed login attempt
    account_checkpoint_retry_seconds: int = 21600  # re-try checkpointed accounts every 6h
    account_wait_seconds: float = 10.0  # how long a request waits for a busy account

    # --- proxies ---
    proxy_url: str | None = None  # rotating residential gateway
    proxy_sticky_url: str | None = None  # gateway template with {session}: stable IP per account
    proxies: CsvList = []  # or a plain list, rotated and health-checked
    accounts_use_proxy: bool = True  # False: accounts log in/scrape from this machine's own IP

    # --- self-management ---
    maintenance_interval_seconds: int = 60  # background loop; 0 disables it
    alert_webhook_url: str | None = None  # Slack/Discord incoming webhook
    alert_min_healthy_accounts: int = 1
    alert_failure_ratio: float = 0.5  # alert when this share of recent Instagram calls fails
    alert_repeat_seconds: int = 3600

    # --- scraper ---
    ig_app_id: str = "936619743392459"
    ig_asbd_id: str = "129477"
    user_agent: str = DEFAULT_USER_AGENT
    request_timeout: float = 20.0
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    max_concurrency: int = 4
    min_request_interval: float = 1.5  # seconds between Instagram calls (politeness/anti-ban)
    max_pages: int = 20  # pagination cap per request

    @field_validator(
        "secret_key",
        "ig_accounts_file",
        "proxy_url",
        "proxy_sticky_url",
        "alert_webhook_url",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator(
        "api_keys", "cors_origins", "ig_accounts", "ig_session_ids", "proxies", mode="before"
    )
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("proxies", mode="after")
    @classmethod
    def _normalize_proxies(cls, value: list[str]) -> list[str]:
        return [normalize_proxy(p) for p in value]

    @property
    def media_dir(self) -> Path:
        return self.storage_dir / "media"


def normalize_proxy(raw: str) -> str:
    """Accept provider formats and return a proxy URL.

    `ip:port:user:pass` -> `http://user:pass@ip:port`, `ip:port` -> `http://ip:port`.
    Values that already contain `://` are kept as they are.
    """
    raw = raw.strip()
    if "://" in raw:
        return raw
    parts = raw.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{raw}"
    raise ValueError("Proxy must look like ip:port, ip:port:user:pass or http://user:pass@ip:port")


@lru_cache
def get_settings() -> Settings:
    return Settings()
